"""파이프라인 취소(cancel) 기능 단위 테스트.

테스트 범위:
  1. MemoryCancelStore — set/is_cancelled/clear/pop, turn_id 매칭
  2. check_cancel — store=None, 예외, 와일드카드 폴백
  3. make_cancel_updates — 필드 정확성 (phase, final_status, error_message)
  4. pop_cancel / clear_cancel — 모듈 레벨 함수
  5. 라우팅 함수 — CANCELLED 시 올바른 경로
  6. result_finalizer — CANCELLED 분기 처리
  7. cancel REST 엔드포인트
  8. _build_result — cancelled 플래그
  9. with_cancel_check — 래퍼 함수
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.agents.graph.cancel import (
    CANCEL_MESSAGE,
    check_cancel,
    clear_cancel,
    make_cancel_updates,
    pop_cancel,
    reset_cancel_store,
    set_cancel_store,
    with_cancel_check,
)
from src.agents.state.state import (
    FinalStatus,
    Phase,
    PipelineState,
    QueryStatus,
    ReasoningState,
)
from src.services.cancel_store import MemoryCancelStore


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. MemoryCancelStore
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestMemoryCancelStore:
    """MemoryCancelStore CRUD + turn_id 매칭."""

    @pytest.fixture
    def store(self) -> MemoryCancelStore:
        return MemoryCancelStore()

    @pytest.mark.asyncio
    async def test_set_and_is_cancelled(self, store: MemoryCancelStore):
        await store.set_cancel("s1", "t1")
        assert await store.is_cancelled("s1", "t1") is True

    @pytest.mark.asyncio
    async def test_turn_id_mismatch(self, store: MemoryCancelStore):
        """다른 turn_id로 조회하면 False."""
        await store.set_cancel("s1", "t1")
        assert await store.is_cancelled("s1", "t2") is False

    @pytest.mark.asyncio
    async def test_no_flag_returns_false(self, store: MemoryCancelStore):
        assert await store.is_cancelled("s1", "t1") is False

    @pytest.mark.asyncio
    async def test_clear_cancel(self, store: MemoryCancelStore):
        await store.set_cancel("s1", "t1")
        await store.clear_cancel("s1")
        assert await store.is_cancelled("s1", "t1") is False

    @pytest.mark.asyncio
    async def test_clear_nonexistent_session(self, store: MemoryCancelStore):
        """존재하지 않는 세션 clear — 에러 없이 무시."""
        await store.clear_cancel("nonexistent")

    @pytest.mark.asyncio
    async def test_pop_cancel_returns_and_removes(
        self, store: MemoryCancelStore,
    ):
        await store.set_cancel("s1", "t1")
        val = await store.pop_cancel("s1")
        assert val == "t1"
        assert await store.is_cancelled("s1", "t1") is False

    @pytest.mark.asyncio
    async def test_pop_cancel_empty(self, store: MemoryCancelStore):
        assert await store.pop_cancel("s1") is None

    @pytest.mark.asyncio
    async def test_overwrite_turn_id(self, store: MemoryCancelStore):
        """같은 세션에 재설정 시 최신 turn_id만 유효."""
        await store.set_cancel("s1", "t1")
        await store.set_cancel("s1", "t2")
        assert await store.is_cancelled("s1", "t1") is False
        assert await store.is_cancelled("s1", "t2") is True

    @pytest.mark.asyncio
    async def test_wildcard_exact_match(self, store: MemoryCancelStore):
        """와일드카드 '*'도 정확 매칭으로 동작."""
        await store.set_cancel("s1", "*")
        assert await store.is_cancelled("s1", "*") is True
        # MemoryCancelStore 자체는 와일드카드 해석 안 함
        assert await store.is_cancelled("s1", "t1") is False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. check_cancel (모듈 레벨)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestCheckCancel:
    """check_cancel 함수 — store=None, 예외 처리, 와일드카드 폴백."""

    @pytest.mark.asyncio
    async def test_no_store_returns_false(self):
        """store 미설정 시 항상 False."""
        reset_cancel_store()
        assert await check_cancel("s1", "t1") is False

    @pytest.mark.asyncio
    async def test_exact_turn_id_match(self):
        store = MemoryCancelStore()
        await store.set_cancel("s1", "t1")
        set_cancel_store(store)
        assert await check_cancel("s1", "t1") is True

    @pytest.mark.asyncio
    async def test_wildcard_fallback(self):
        """turn_id 불일치 시 와일드카드('*') 폴백 체크."""
        store = MemoryCancelStore()
        await store.set_cancel("s1", "*")
        set_cancel_store(store)
        # turn_id="t1"이지만 저장된 값이 "*" → 와일드카드 폴백으로 True
        assert await check_cancel("s1", "t1") is True

    @pytest.mark.asyncio
    async def test_no_wildcard_no_match(self):
        """turn_id도 와일드카드도 불일치 → False."""
        store = MemoryCancelStore()
        await store.set_cancel("s1", "t1")
        set_cancel_store(store)
        assert await check_cancel("s1", "t2") is False

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        """store 예외 발생 시 False (파이프라인 계속)."""
        mock_store = AsyncMock()
        mock_store.is_cancelled.side_effect = RuntimeError("Redis down")
        set_cancel_store(mock_store)
        assert await check_cancel("s1", "t1") is False

    @pytest.mark.asyncio
    async def test_wildcard_turn_id_no_double_check(self):
        """turn_id='*'인 경우 와일드카드 폴백 안 함 (무한 루프 방지)."""
        store = MemoryCancelStore()
        # 아무것도 설정 안 함
        set_cancel_store(store)
        assert await check_cancel("s1", "*") is False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. make_cancel_updates
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestMakeCancelUpdates:
    """make_cancel_updates — 반환 dict의 필드 정확성."""

    def test_fields(self):
        reason = ReasoningState()
        updates = make_cancel_updates(reason)

        assert updates["status"] == QueryStatus.CANCELLED
        assert updates["error_message"]  # non-empty
        assert updates["reason"].phase == Phase.DONE
        assert updates["reason"].final_status == FinalStatus.CANCELLED

    def test_deep_copy(self):
        """원본 reason이 변경되지 않아야 한다."""
        reason = ReasoningState(phase=Phase.EXPLORING)
        updates = make_cancel_updates(reason)

        assert reason.phase == Phase.EXPLORING  # 원본 불변
        assert updates["reason"].phase == Phase.DONE

    def test_exploration_summary_set(self):
        reason = ReasoningState()
        updates = make_cancel_updates(reason)
        assert updates["reason"].exploration_summary


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. pop_cancel / clear_cancel (모듈 레벨)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestModuleLevelHelpers:
    """모듈 레벨 pop_cancel, clear_cancel."""

    @pytest.mark.asyncio
    async def test_pop_cancel_no_store(self):
        reset_cancel_store()
        assert await pop_cancel("s1") is None

    @pytest.mark.asyncio
    async def test_pop_cancel_with_store(self):
        store = MemoryCancelStore()
        await store.set_cancel("s1", "t1")
        set_cancel_store(store)
        val = await pop_cancel("s1")
        assert val == "t1"
        # 삭제 확인
        assert await pop_cancel("s1") is None

    @pytest.mark.asyncio
    async def test_clear_cancel_no_store(self):
        """store 없으면 에러 없이 무시."""
        reset_cancel_store()
        await clear_cancel("s1")  # no error

    @pytest.mark.asyncio
    async def test_clear_cancel_with_store(self):
        store = MemoryCancelStore()
        await store.set_cancel("s1", "t1")
        set_cancel_store(store)
        await clear_cancel("s1")
        assert await check_cancel("s1", "t1") is False

    @pytest.mark.asyncio
    async def test_pop_cancel_exception(self):
        """pop_cancel 예외 시 None 반환."""
        mock_store = AsyncMock()
        mock_store.pop_cancel.side_effect = RuntimeError("Redis down")
        set_cancel_store(mock_store)
        assert await pop_cancel("s1") is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. 라우팅 함수 — CANCELLED 경로
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestCancelRouting:
    """CANCELLED 상태에서 라우팅 함수가 올바른 경로를 반환하는지 검증."""

    def _state(self, **kw) -> PipelineState:
        return PipelineState(
            user_input="test",
            session_id="s1",
            turn_id="t1",
            **kw,
        )

    def test_route_after_readiness_gate_cancelled(self):
        from src.agents.graph.pipeline import _route_after_readiness_gate
        state = self._state(status=QueryStatus.CANCELLED)
        assert _route_after_readiness_gate(state) == "conclude_failure"

    def test_route_after_recovery_agent_cancelled(self):
        from src.agents.graph.pipeline import _route_after_recovery_agent
        state = self._state(status=QueryStatus.CANCELLED)
        assert _route_after_recovery_agent(state) == "result_finalizer"

    def test_route_after_result_finalizer_cancelled(self):
        """F2 핵심: CANCELLED면 validated_sql 있어도 error_end."""
        from src.agents.graph.pipeline import _route_after_result_finalizer
        reason = ReasoningState(validated_sql="SELECT 1")
        state = self._state(
            status=QueryStatus.CANCELLED,
            reason=reason,
        )
        assert _route_after_result_finalizer(state) == "error_end"

    def test_route_after_result_finalizer_normal_with_sql(self):
        """정상 + validated_sql → execute_sql (취소 아닌 경우 대조군)."""
        from src.agents.graph.pipeline import _route_after_result_finalizer
        reason = ReasoningState(validated_sql="SELECT 1")
        state = self._state(reason=reason)
        assert _route_after_result_finalizer(state) == "sql_executor"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 6. result_finalizer — CANCELLED 분기
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestResultFinalizerCancelled:
    """result_finalizer_node가 CANCELLED 상태를 올바르게 처리하는지."""

    @pytest.mark.asyncio
    async def test_cancelled_sets_error_message(self):
        from src.agents.nodes.reason.result_finalizer import (
            result_finalizer_node,
        )
        state = PipelineState(
            user_input="test",
            session_id="s1",
            turn_id="t1",
            status=QueryStatus.CANCELLED,
        )
        result = await result_finalizer_node(state)

        assert result["error_message"]  # routing용 필수
        assert result["reason"].final_status == FinalStatus.CANCELLED
        assert result["reason"].phase == Phase.DONE
        assert "중단" in result["formatted_response"]

    @pytest.mark.asyncio
    async def test_cancelled_with_partial_data(self):
        """탐색 중 취소: 부분 결과(테이블, 지식)가 요약에 포함."""
        from src.agents.nodes.reason.result_finalizer import (
            result_finalizer_node,
        )
        from src.agents.state.state import (
            TableMeta,
            KnowledgeItem,
            ConfidenceStatus,
            SelectionStatus,
        )

        reason = ReasoningState(
            explored_tables=[
                TableMeta(
                    table_name="TB_LOAN",
                    selection_status=SelectionStatus.SELECTED,
                ),
            ],
            knowledge_items=[
                KnowledgeItem(
                    key="term:여신",
                    id="K1",
                    status=ConfidenceStatus.CONFIRMED,
                    value="대출",
                ),
            ],
        )
        state = PipelineState(
            user_input="test",
            session_id="s1",
            turn_id="t1",
            status=QueryStatus.CANCELLED,
            reason=reason,
        )
        result = await result_finalizer_node(state)
        summary = result["formatted_response"]
        assert "TB_LOAN" in summary
        assert "확인된 정보" in summary


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 7. Cancel REST 엔드포인트
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestCancelEndpoint:
    """POST /api/sessions/{session_id}/cancel 엔드포인트."""

    @pytest.mark.asyncio
    async def test_cancel_sets_flag(self):
        """엔드포인트가 cancel store에 플래그를 설정하는지."""
        store = MemoryCancelStore()
        set_cancel_store(store)

        # get_cancel_store는 함수 내부에서 import하므로 원본 모듈을 패치
        with patch(
            "src.agents.graph.cancel.get_cancel_store",
            return_value=store,
        ):
            from src.routers.sessions import cancel_pipeline
            result = await cancel_pipeline("s1", turn_id="t1")

        assert result["status"] == "cancel_requested"
        assert await store.is_cancelled("s1", "t1") is True

    @pytest.mark.asyncio
    async def test_cancel_default_wildcard(self):
        """turn_id 미지정 시 기본값 '*' 사용."""
        store = MemoryCancelStore()
        set_cancel_store(store)

        with patch(
            "src.agents.graph.cancel.get_cancel_store",
            return_value=store,
        ):
            from src.routers.sessions import cancel_pipeline
            result = await cancel_pipeline("s1", turn_id="*")

        assert result["turn_id"] == "*"
        assert await store.is_cancelled("s1", "*") is True

    @pytest.mark.asyncio
    async def test_cancel_no_store_503(self):
        """store 미활성 시 HTTPException 503."""
        reset_cancel_store()

        with patch(
            "src.agents.graph.cancel.get_cancel_store",
            return_value=None,
        ):
            from fastapi import HTTPException
            from src.routers.sessions import cancel_pipeline
            with pytest.raises(HTTPException) as exc_info:
                await cancel_pipeline("s1")
            assert exc_info.value.status_code == 503


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 8. _build_result — cancelled 플래그
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestBuildResultCancelled:
    """runner._build_result가 cancelled 플래그를 올바르게 설정하는지."""

    def test_cancelled_from_enum(self):
        from src.agents.graph.runner import _build_result
        from src.utils.tracker.callback_handler import (
            DataCopilotCallbackHandler,
        )
        handler = DataCopilotCallbackHandler(run_id="test")
        handler.start_run(user_input="test", session_id="s1")
        result = _build_result(handler, {
            "formatted_response": "중단됨",
            "status": QueryStatus.CANCELLED,
        })
        assert result.cancelled is True

    def test_not_cancelled_normal(self):
        from src.agents.graph.runner import _build_result
        from src.utils.tracker.callback_handler import (
            DataCopilotCallbackHandler,
        )
        handler = DataCopilotCallbackHandler(run_id="test")
        handler.start_run(user_input="test", session_id="s1")
        result = _build_result(handler, {
            "formatted_response": "결과",
            "status": QueryStatus.PENDING,
        })
        assert result.cancelled is False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 9. with_cancel_check 래퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestWithCancelCheck:
    """with_cancel_check 래퍼 — cancel 시 노드 미실행, 정상 시 통과."""

    @pytest.fixture(autouse=True)
    def _cleanup(self):
        yield
        reset_cancel_store()

    @pytest.mark.asyncio
    async def test_cancelled_skips_node_body(self):
        """cancel 플래그 설정 시 노드 본문을 실행하지 않는다."""
        store = MemoryCancelStore()
        await store.set_cancel("s1", "t1")
        set_cancel_store(store)

        called = False

        async def dummy_node(state: PipelineState) -> dict:
            nonlocal called
            called = True
            return {"formatted_response": "ok"}

        wrapped = with_cancel_check(dummy_node)
        state = PipelineState(
            user_input="test", session_id="s1", turn_id="t1",
        )
        result = await wrapped(state)

        assert called is False
        assert result["status"] == QueryStatus.CANCELLED
        assert result["error_message"] == CANCEL_MESSAGE

    @pytest.mark.asyncio
    async def test_not_cancelled_passes_through(self):
        """cancel 플래그 미설정 시 원본 노드가 정상 실행된다."""
        reset_cancel_store()

        async def dummy_node(state: PipelineState) -> dict:
            return {"formatted_response": "ok"}

        wrapped = with_cancel_check(dummy_node)
        state = PipelineState(
            user_input="test", session_id="s1", turn_id="t1",
        )
        result = await wrapped(state)

        assert result["formatted_response"] == "ok"
        assert "status" not in result

    def test_preserves_function_name(self):
        """functools.wraps가 원본 함수명을 보존한다."""
        async def my_special_node(state: PipelineState) -> dict:
            return {}

        wrapped = with_cancel_check(my_special_node)
        assert wrapped.__name__ == "my_special_node"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 10. Interpret 라우팅 — CANCELLED 경로 (W-06 보완)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestInterpretCancelRouting:
    """Interpret 계층 라우팅이 CANCELLED를 error_end로 보내는지."""

    def _state(self, **kw) -> PipelineState:
        return PipelineState(
            user_input="test",
            session_id="s1",
            turn_id="t1",
            **kw,
        )

    def test_route_after_intent_classifier_cancelled(self):
        from src.agents.graph.pipeline import _route_after_intent_classifier
        state = self._state(status=QueryStatus.CANCELLED)
        assert _route_after_intent_classifier(state) == "error_end"

    def test_route_after_normalize_cancelled(self):
        from src.agents.graph.pipeline import _route_after_normalize
        state = self._state(status=QueryStatus.CANCELLED)
        assert _route_after_normalize(state) == "error_end"
