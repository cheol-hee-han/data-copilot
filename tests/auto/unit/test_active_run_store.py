"""ActiveRunStore 단위 테스트.

테스트 범위:
  1. MemoryActiveRunStore — set/is_active/clear + CAS 방어
  2. RedisActiveRunStore — AsyncMock 기반 동작 + CAS Lua 호출 검증
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.services.active_run_store import (
    MemoryActiveRunStore,
    RedisActiveRunStore,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. MemoryActiveRunStore
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestMemoryActiveRunStore:
    """MemoryActiveRunStore CRUD + CAS 동작."""

    @pytest.fixture
    def store(self) -> MemoryActiveRunStore:
        return MemoryActiveRunStore()

    @pytest.mark.asyncio
    async def test_set_and_is_active(self, store: MemoryActiveRunStore):
        await store.set_active("s1", "t1")
        assert await store.is_active("s1") is True

    @pytest.mark.asyncio
    async def test_no_entry_returns_false(
        self, store: MemoryActiveRunStore,
    ):
        assert await store.is_active("s1") is False

    @pytest.mark.asyncio
    async def test_clear_with_matching_turn_id(
        self, store: MemoryActiveRunStore,
    ):
        await store.set_active("s1", "t1")
        await store.clear_active("s1", "t1")
        assert await store.is_active("s1") is False

    @pytest.mark.asyncio
    async def test_clear_with_mismatched_turn_id_is_noop(
        self, store: MemoryActiveRunStore,
    ):
        """CAS 방어: 다른 turn_id 로 clear 해도 삭제되지 않음."""
        await store.set_active("s1", "t1")
        await store.clear_active("s1", "t_other")
        assert await store.is_active("s1") is True

    @pytest.mark.asyncio
    async def test_clear_nonexistent_session_is_noop(
        self, store: MemoryActiveRunStore,
    ):
        """존재하지 않는 세션 clear — 에러 없이 무시."""
        await store.clear_active("nonexistent", "t1")

    @pytest.mark.asyncio
    async def test_race_condition_cas_protection(
        self, store: MemoryActiveRunStore,
    ):
        """동시 재전송 race 방어:

        A 턴 시작 → B 턴이 같은 세션에 덮어쓰기 → A 의 finally clear 가
        B 를 지우지 않아야 함.
        """
        # A 턴 시작
        await store.set_active("s1", "turn_a")
        # B 턴이 덮어씀 (사용자 빠른 재전송)
        await store.set_active("s1", "turn_b")
        # A 의 finally 가 A 의 turn_id 로 clear 시도
        await store.clear_active("s1", "turn_a")
        # B 는 여전히 활성
        assert await store.is_active("s1") is True

    @pytest.mark.asyncio
    async def test_overwrite_same_session(
        self, store: MemoryActiveRunStore,
    ):
        """같은 세션에 set 재호출 시 최신 turn_id 만 유효."""
        await store.set_active("s1", "t1")
        await store.set_active("s1", "t2")
        # t1 로 clear 해도 no-op (현재 값은 t2)
        await store.clear_active("s1", "t1")
        assert await store.is_active("s1") is True
        # t2 로 clear 해야 삭제됨
        await store.clear_active("s1", "t2")
        assert await store.is_active("s1") is False

    @pytest.mark.asyncio
    async def test_multiple_sessions_independent(
        self, store: MemoryActiveRunStore,
    ):
        await store.set_active("s1", "t1")
        await store.set_active("s2", "t2")
        assert await store.is_active("s1") is True
        assert await store.is_active("s2") is True
        await store.clear_active("s1", "t1")
        assert await store.is_active("s1") is False
        assert await store.is_active("s2") is True


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. RedisActiveRunStore (AsyncMock 기반)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestRedisActiveRunStore:
    """RedisActiveRunStore — Redis 클라이언트 호출 시퀀스 검증.

    실 Redis 없이 AsyncMock 으로 호출 파라미터/반환값만 검증한다.
    (CancelStore 와 동일한 인터페이스를 전제).
    """

    @pytest.fixture
    def mock_client(self) -> AsyncMock:
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_set_active_calls_set_with_ttl(
        self, mock_client: AsyncMock,
    ):
        store = RedisActiveRunStore(mock_client, ttl_seconds=600)
        await store.set_active("s1", "t1")
        mock_client.set.assert_awaited_once_with(
            "active_run:s1", "t1", ex=600,
        )

    @pytest.mark.asyncio
    async def test_is_active_true(self, mock_client: AsyncMock):
        mock_client.exists.return_value = 1
        store = RedisActiveRunStore(mock_client)
        assert await store.is_active("s1") is True
        mock_client.exists.assert_awaited_once_with("active_run:s1")

    @pytest.mark.asyncio
    async def test_is_active_false(self, mock_client: AsyncMock):
        mock_client.exists.return_value = 0
        store = RedisActiveRunStore(mock_client)
        assert await store.is_active("s1") is False

    @pytest.mark.asyncio
    async def test_clear_active_runs_cas_script(
        self, mock_client: AsyncMock,
    ):
        mock_client.eval.return_value = 1
        store = RedisActiveRunStore(mock_client)
        await store.clear_active("s1", "t1")
        # Lua 스크립트 1번 호출, 키 1개, 인자는 turn_id
        assert mock_client.eval.await_count == 1
        args = mock_client.eval.await_args.args
        # (script, numkeys, key, turn_id)
        assert args[1] == 1
        assert args[2] == "active_run:s1"
        assert args[3] == "t1"

    @pytest.mark.asyncio
    async def test_custom_ttl(self, mock_client: AsyncMock):
        store = RedisActiveRunStore(mock_client, ttl_seconds=1800)
        await store.set_active("s1", "t1")
        mock_client.set.assert_awaited_once_with(
            "active_run:s1", "t1", ex=1800,
        )

    @pytest.mark.asyncio
    async def test_key_namespace(self, mock_client: AsyncMock):
        """키 prefix 는 cancel: 와 충돌하지 않는 active_run: 사용."""
        store = RedisActiveRunStore(mock_client)
        await store.set_active("abc", "xyz")
        call_args = mock_client.set.await_args.args
        assert call_args[0].startswith("active_run:")
        assert "cancel" not in call_args[0]
