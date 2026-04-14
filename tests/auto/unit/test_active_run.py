"""active_run 래퍼 모듈 단위 테스트.

테스트 범위:
  1. 싱글턴 관리 (get/set/reset)
  2. mark_active / clear_active / check_active — 정상 경로
  3. 예외 흡수: 스토어가 Exception 을 던져도 파이프라인이 영향 받지 않음
  4. 스토어 미설정 시 no-op 동작
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.agents.graph.active_run import (
    check_active,
    clear_active,
    get_active_run_store,
    mark_active,
    reset_active_run_store,
    set_active_run_store,
)
from src.services.active_run_store import MemoryActiveRunStore


@pytest.fixture(autouse=True)
def _cleanup():
    """각 테스트 후 싱글턴 초기화."""
    yield
    reset_active_run_store()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. 싱글턴 관리
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestSingleton:
    def test_set_and_get(self):
        store = MemoryActiveRunStore()
        set_active_run_store(store)
        assert get_active_run_store() is store

    def test_reset(self):
        set_active_run_store(MemoryActiveRunStore())
        reset_active_run_store()
        assert get_active_run_store() is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. 정상 경로
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestHappyPath:
    @pytest.mark.asyncio
    async def test_mark_then_check_true(self):
        set_active_run_store(MemoryActiveRunStore())
        await mark_active("s1", "t1")
        assert await check_active("s1") is True

    @pytest.mark.asyncio
    async def test_mark_clear_check_false(self):
        set_active_run_store(MemoryActiveRunStore())
        await mark_active("s1", "t1")
        await clear_active("s1", "t1")
        assert await check_active("s1") is False

    @pytest.mark.asyncio
    async def test_clear_cas_mismatch(self):
        """다른 turn_id 로 clear 해도 삭제되지 않음 (CAS)."""
        set_active_run_store(MemoryActiveRunStore())
        await mark_active("s1", "t1")
        await clear_active("s1", "wrong_turn")
        assert await check_active("s1") is True


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. 스토어 미설정 시 no-op
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestNoStore:
    @pytest.mark.asyncio
    async def test_mark_active_no_store(self):
        """스토어 미설정 시 mark_active 는 조용히 no-op."""
        reset_active_run_store()
        await mark_active("s1", "t1")  # no error

    @pytest.mark.asyncio
    async def test_clear_active_no_store(self):
        reset_active_run_store()
        await clear_active("s1", "t1")  # no error

    @pytest.mark.asyncio
    async def test_check_active_no_store_returns_false(self):
        reset_active_run_store()
        assert await check_active("s1") is False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. 예외 흡수 (Redis 장애 시뮬레이션)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestExceptionAbsorption:
    """스토어가 예외를 던져도 래퍼가 삼키고 파이프라인을 막지 않아야 함."""

    @pytest.mark.asyncio
    async def test_mark_active_absorbs_exception(self):
        mock_store = AsyncMock()
        mock_store.set_active.side_effect = RuntimeError("Redis down")
        set_active_run_store(mock_store)
        # 예외가 새어 나오면 실패
        await mark_active("s1", "t1")

    @pytest.mark.asyncio
    async def test_clear_active_absorbs_exception(self):
        mock_store = AsyncMock()
        mock_store.clear_active.side_effect = ConnectionError("Redis flap")
        set_active_run_store(mock_store)
        await clear_active("s1", "t1")

    @pytest.mark.asyncio
    async def test_check_active_returns_false_on_exception(self):
        """Redis 조회 실패 시 False (크래시로 간주 = 안전한 기본값)."""
        mock_store = AsyncMock()
        mock_store.is_active.side_effect = TimeoutError("Redis timeout")
        set_active_run_store(mock_store)
        assert await check_active("s1") is False
