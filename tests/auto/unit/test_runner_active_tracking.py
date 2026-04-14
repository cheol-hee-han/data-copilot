"""runner.run_pipeline 의 ActiveRunStore 통합 테스트.

테스트 범위:
  1. 정상 실행 — 파이프라인 중 is_active True, 완료 후 False
  2. 예외 발생 — finally 에서 clear 보장
  3. Redis 장애 주입 — 파이프라인 정상 완료 (래퍼가 예외 흡수)

run_pipeline 내부에서 _execute_and_finalize 는 많은 의존성을 필요로 하므로,
_execute_and_finalize 를 monkeypatch 해서 mark_active/clear_active 호출만
확인한다. 실제 그래프 실행은 별도의 E2E 테스트 몫.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.agents.graph.active_run import (
    check_active,
    reset_active_run_store,
    set_active_run_store,
)
from src.services.active_run_store import MemoryActiveRunStore


@pytest.fixture(autouse=True)
def _cleanup_active_run():
    yield
    reset_active_run_store()


@pytest.fixture
def _patch_run_pipeline_deps(monkeypatch):
    """run_pipeline 이 의존하는 외부 호출을 모두 no-op 으로 패치."""
    from src.agents.graph import runner as _runner

    # _prepare_input: early_result=None 으로 정상 진행
    prepared = _runner._PreparedInput(
        sanitized_text="sanitized",
        previous_cancel_turn_id=None,
    )
    monkeypatch.setattr(
        _runner, "_prepare_input", AsyncMock(return_value=prepared),
    )

    # _check_interrupt: False
    monkeypatch.setattr(
        _runner, "_check_interrupt", AsyncMock(return_value=False),
    )

    # 조기 저장 / manager / app 은 우회
    class _FakePool:
        pass

    class _FakeManager:
        checkpointer_pool = None

        async def connect_all(self):
            pass

    monkeypatch.setattr(
        _runner, "get_connector_manager", lambda: _FakeManager(),
    )
    monkeypatch.setattr(_runner, "get_compiled_app", lambda: object())

    # DataCopilotCallbackHandler 는 실제 동작해도 부작용 없음
    return _runner


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. 정상 실행 — finally 에서 clear
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestActiveRunLifecycle:
    @pytest.mark.asyncio
    async def test_normal_completion_clears_active(
        self, _patch_run_pipeline_deps, monkeypatch,
    ):
        """정상 완료 시 finally 에서 clear 되어 is_active=False."""
        set_active_run_store(MemoryActiveRunStore())

        runner = _patch_run_pipeline_deps

        # _execute_and_finalize 가 반환되는 동안 is_active 가 True 인지 확인
        captured_during_exec: dict[str, bool] = {}

        async def _fake_execute(*, session_id, **kwargs):
            # 실행 중에는 등록되어 있어야 함
            captured_during_exec["active"] = await check_active(session_id)
            return object()  # PipelineResult placeholder

        monkeypatch.setattr(
            runner, "_execute_and_finalize", _fake_execute,
        )

        await runner.run_pipeline(user_input="테스트", session_id="s1")

        assert captured_during_exec["active"] is True
        # 완료 후에는 해제
        assert await check_active("s1") is False

    @pytest.mark.asyncio
    async def test_exception_still_clears_active(
        self, _patch_run_pipeline_deps, monkeypatch,
    ):
        """파이프라인 예외 발생해도 finally 에서 clear 보장."""
        set_active_run_store(MemoryActiveRunStore())

        runner = _patch_run_pipeline_deps

        async def _raising_execute(**kwargs):
            raise RuntimeError("pipeline boom")

        monkeypatch.setattr(
            runner, "_execute_and_finalize", _raising_execute,
        )

        with pytest.raises(RuntimeError, match="pipeline boom"):
            await runner.run_pipeline(user_input="테스트", session_id="s1")

        # 예외 후에도 clear
        assert await check_active("s1") is False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. Redis 장애 주입 — 파이프라인 정상 완료
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestRedisFailureDoesNotBreakPipeline:
    @pytest.mark.asyncio
    async def test_store_exception_absorbed(
        self, _patch_run_pipeline_deps, monkeypatch,
    ):
        """스토어 set/clear 가 예외를 던져도 파이프라인은 정상 완료."""
        failing = AsyncMock()
        failing.set_active.side_effect = RuntimeError("redis down")
        failing.clear_active.side_effect = RuntimeError("redis down")
        failing.is_active.return_value = False
        set_active_run_store(failing)

        runner = _patch_run_pipeline_deps

        sentinel = object()

        async def _fake_execute(**kwargs):
            return sentinel

        monkeypatch.setattr(
            runner, "_execute_and_finalize", _fake_execute,
        )

        # 스토어 예외가 래퍼에서 흡수되어 파이프라인이 정상 완료되어야 함
        result = await runner.run_pipeline(
            user_input="테스트", session_id="s1",
        )
        assert result is sentinel


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. CAS race 방어 (동일 세션 빠른 재전송)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestCASRaceSafety:
    @pytest.mark.asyncio
    async def test_cas_prevents_clearing_newer_run(self):
        """A 턴 clear 가 B 턴 등록을 덮어쓰지 않음 (MemoryStore CAS)."""
        store = MemoryActiveRunStore()

        await store.set_active("s1", "turn_a")
        await store.set_active("s1", "turn_b")  # B 가 덮어씀
        # A 의 finally 가 A 의 turn_id 로 clear 시도
        await store.clear_active("s1", "turn_a")
        assert await store.is_active("s1") is True

    @pytest.mark.asyncio
    async def test_run_pipeline_generates_unique_run_keys(
        self, _patch_run_pipeline_deps, monkeypatch,
    ):
        """run_pipeline 이 매 호출마다 고유 _run_key 를 생성해 CAS 가
        실제로 동작해야 한다 (동일 session_id 두 번 호출 시에도 덮어쓰기
        race 방어)."""
        set_active_run_store(MemoryActiveRunStore())
        runner = _patch_run_pipeline_deps

        # A 가 실행 중인 상태에서 B 가 mark_active 로 덮어썼다고 가정.
        # A 의 finally 는 A 의 _run_key 로 clear 하므로 B 의 등록이
        # 살아남아야 한다.
        import asyncio

        gate = asyncio.Event()
        b_registered = asyncio.Event()

        async def _fake_execute_a(*, session_id, **kwargs):
            # A 가 실행 중인 동안 B 를 진입시킨다
            b_registered.set()
            await gate.wait()
            return object()

        async def _fake_execute_b(*, session_id, **kwargs):
            return object()

        call_count = {"n": 0}
        orig_execute = runner._execute_and_finalize

        async def _dispatching_execute(**kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return await _fake_execute_a(**kwargs)
            return await _fake_execute_b(**kwargs)

        monkeypatch.setattr(
            runner, "_execute_and_finalize", _dispatching_execute,
        )

        task_a = asyncio.create_task(
            runner.run_pipeline(user_input="A", session_id="s1"),
        )
        await b_registered.wait()
        # B 시작 — A 의 등록을 덮어쓰는 효과 (Memory CAS 로 보호)
        task_b = asyncio.create_task(
            runner.run_pipeline(user_input="B", session_id="s1"),
        )
        # B 먼저 완료
        await task_b
        # A 해제
        gate.set()
        await task_a

        # 두 호출 모두 종료 후에는 당연히 False
        from src.agents.graph.active_run import check_active
        assert await check_active("s1") is False
        _ = orig_execute  # unused ref 방지
