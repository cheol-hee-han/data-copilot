"""RedisCancelStore 단위 테스트.

테스트 대상:
    - RedisCancelStore.set_cancel()
    - RedisCancelStore.is_cancelled() — bytes/str 반환 모두 처리
    - RedisCancelStore.clear_cancel()
    - RedisCancelStore.pop_cancel() — getdel 성공 / Lua fallback

Redis 클라이언트는 AsyncMock으로 격리한다.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.cancel_store import RedisCancelStore


# ── 픽스처 ─────────────────────────────────────────────────


@pytest.fixture
def mock_redis() -> AsyncMock:
    """AsyncMock Redis 클라이언트."""
    return AsyncMock()


@pytest.fixture
def store(mock_redis: AsyncMock) -> RedisCancelStore:
    """RedisCancelStore 인스턴스 (Mock Redis 주입)."""
    return RedisCancelStore(redis_client=mock_redis)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. set_cancel
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestRedisCancelStoreSetCancel:
    """set_cancel() — Redis SET 호출 및 TTL 설정 검증."""

    @pytest.mark.asyncio
    async def test_set_cancel_calls_redis_set(
        self, store: RedisCancelStore, mock_redis: AsyncMock,
    ):
        """set_cancel이 올바른 key/value/TTL로 redis.set을 호출한다."""
        await store.set_cancel("session-1", "turn-001")
        mock_redis.set.assert_called_once_with(
            "cancel:session-1",
            "turn-001",
            ex=RedisCancelStore._CANCEL_TTL,
        )

    @pytest.mark.asyncio
    async def test_set_cancel_key_format(
        self, store: RedisCancelStore, mock_redis: AsyncMock,
    ):
        """Redis 키는 'cancel:{session_id}' 형식이다."""
        await store.set_cancel("my-session", "t1")
        call_args = mock_redis.set.call_args
        assert call_args[0][0] == "cancel:my-session"

    @pytest.mark.asyncio
    async def test_set_cancel_ttl_is_300(
        self, store: RedisCancelStore, mock_redis: AsyncMock,
    ):
        """TTL은 300초로 설정된다."""
        await store.set_cancel("s1", "t1")
        call_kwargs = mock_redis.set.call_args[1]
        assert call_kwargs["ex"] == 300


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. is_cancelled
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestRedisCancelStoreIsCancelled:
    """is_cancelled() — 일치/불일치/None/bytes 반환 처리."""

    @pytest.mark.asyncio
    async def test_returns_true_when_turn_id_matches_str(
        self, store: RedisCancelStore, mock_redis: AsyncMock,
    ):
        """Redis가 str turn_id 반환 → 일치하면 True."""
        mock_redis.get.return_value = "turn-001"
        result = await store.is_cancelled("session-1", "turn-001")
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_true_when_turn_id_matches_bytes(
        self, store: RedisCancelStore, mock_redis: AsyncMock,
    ):
        """Redis가 bytes 반환 → decode 후 비교하여 True."""
        mock_redis.get.return_value = b"turn-001"
        result = await store.is_cancelled("session-1", "turn-001")
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_turn_id_mismatch_str(
        self, store: RedisCancelStore, mock_redis: AsyncMock,
    ):
        """Redis가 다른 turn_id 반환 → False."""
        mock_redis.get.return_value = "turn-002"
        result = await store.is_cancelled("session-1", "turn-001")
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_turn_id_mismatch_bytes(
        self, store: RedisCancelStore, mock_redis: AsyncMock,
    ):
        """Redis가 bytes로 다른 turn_id 반환 → False."""
        mock_redis.get.return_value = b"turn-999"
        result = await store.is_cancelled("session-1", "turn-001")
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_redis_returns_none(
        self, store: RedisCancelStore, mock_redis: AsyncMock,
    ):
        """Redis에 키 없으면 (None 반환) → False."""
        mock_redis.get.return_value = None
        result = await store.is_cancelled("session-1", "turn-001")
        assert result is False

    @pytest.mark.asyncio
    async def test_is_cancelled_calls_correct_key(
        self, store: RedisCancelStore, mock_redis: AsyncMock,
    ):
        """is_cancelled이 올바른 키로 redis.get을 호출한다."""
        mock_redis.get.return_value = None
        await store.is_cancelled("my-session", "t1")
        mock_redis.get.assert_called_once_with("cancel:my-session")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. clear_cancel
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestRedisCancelStoreClearCancel:
    """clear_cancel() — Redis DELETE 호출 검증."""

    @pytest.mark.asyncio
    async def test_clear_cancel_calls_redis_delete(
        self, store: RedisCancelStore, mock_redis: AsyncMock,
    ):
        """clear_cancel이 올바른 키로 redis.delete를 호출한다."""
        await store.clear_cancel("session-1")
        mock_redis.delete.assert_called_once_with("cancel:session-1")

    @pytest.mark.asyncio
    async def test_clear_cancel_uses_correct_key_format(
        self, store: RedisCancelStore, mock_redis: AsyncMock,
    ):
        """키 형식 'cancel:{session_id}' 검증."""
        await store.clear_cancel("abc-session")
        call_args = mock_redis.delete.call_args[0]
        assert call_args[0] == "cancel:abc-session"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. pop_cancel — getdel 성공 경로
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestRedisCancelStorePopCancelGetdel:
    """pop_cancel() — getdel 성공 시 반환값 및 bytes 처리."""

    @pytest.mark.asyncio
    async def test_pop_cancel_returns_str_from_getdel(
        self, store: RedisCancelStore, mock_redis: AsyncMock,
    ):
        """getdel이 str 반환 → 그대로 반환."""
        mock_redis.getdel.return_value = "turn-001"
        result = await store.pop_cancel("session-1")
        assert result == "turn-001"

    @pytest.mark.asyncio
    async def test_pop_cancel_decodes_bytes_from_getdel(
        self, store: RedisCancelStore, mock_redis: AsyncMock,
    ):
        """getdel이 bytes 반환 → decode하여 반환."""
        mock_redis.getdel.return_value = b"turn-001"
        result = await store.pop_cancel("session-1")
        assert result == "turn-001"

    @pytest.mark.asyncio
    async def test_pop_cancel_returns_none_when_getdel_returns_none(
        self, store: RedisCancelStore, mock_redis: AsyncMock,
    ):
        """getdel이 None 반환 → None 반환."""
        mock_redis.getdel.return_value = None
        result = await store.pop_cancel("session-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_pop_cancel_calls_correct_key(
        self, store: RedisCancelStore, mock_redis: AsyncMock,
    ):
        """pop_cancel이 올바른 키로 getdel을 호출한다."""
        mock_redis.getdel.return_value = None
        await store.pop_cancel("my-session")
        mock_redis.getdel.assert_called_once_with("cancel:my-session")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. pop_cancel — Lua fallback 경로
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestRedisCancelStorePopCancelLuaFallback:
    """pop_cancel() — getdel 실패 시 Lua 스크립트 폴백."""

    @pytest.mark.asyncio
    async def test_lua_fallback_on_getdel_exception(
        self, store: RedisCancelStore, mock_redis: AsyncMock,
    ):
        """getdel이 예외를 던지면 eval(Lua)로 폴백한다."""
        mock_redis.getdel.side_effect = Exception("GETDEL not supported")
        mock_redis.eval.return_value = "turn-001"
        result = await store.pop_cancel("session-1")
        assert result == "turn-001"
        mock_redis.eval.assert_called_once()

    @pytest.mark.asyncio
    async def test_lua_fallback_decodes_bytes(
        self, store: RedisCancelStore, mock_redis: AsyncMock,
    ):
        """Lua 폴백이 bytes를 반환하면 decode하여 반환."""
        mock_redis.getdel.side_effect = Exception("not supported")
        mock_redis.eval.return_value = b"turn-abc"
        result = await store.pop_cancel("session-1")
        assert result == "turn-abc"

    @pytest.mark.asyncio
    async def test_lua_fallback_returns_none_on_none(
        self, store: RedisCancelStore, mock_redis: AsyncMock,
    ):
        """Lua 폴백이 None 반환 → None 반환."""
        mock_redis.getdel.side_effect = Exception("not supported")
        mock_redis.eval.return_value = None
        result = await store.pop_cancel("session-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_lua_fallback_uses_correct_script_and_key(
        self, store: RedisCancelStore, mock_redis: AsyncMock,
    ):
        """Lua 폴백이 1개의 키(KEYS[1])와 함께 올바른 스크립트를 호출한다."""
        mock_redis.getdel.side_effect = Exception("not supported")
        mock_redis.eval.return_value = None
        await store.pop_cancel("session-1")
        call_args = mock_redis.eval.call_args[0]
        # eval(script, num_keys, key)
        assert call_args[1] == 1  # num_keys
        assert call_args[2] == "cancel:session-1"  # key

    @pytest.mark.asyncio
    async def test_lua_script_contains_get_and_del(
        self, store: RedisCancelStore, mock_redis: AsyncMock,
    ):
        """Lua 스크립트 문자열에 GET과 DEL 명령이 포함된다."""
        mock_redis.getdel.side_effect = Exception("not supported")
        mock_redis.eval.return_value = None
        await store.pop_cancel("session-1")
        lua_script = mock_redis.eval.call_args[0][0]
        assert "GET" in lua_script
        assert "DEL" in lua_script


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 6. _key 헬퍼 (내부 메서드 직접 검증)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestRedisCancelStoreKeyHelper:
    """_key() — 키 형식 검증."""

    def test_key_format(self, store: RedisCancelStore):
        """'cancel:{session_id}' 형식 반환."""
        assert store._key("session-1") == "cancel:session-1"

    def test_key_with_empty_session(self, store: RedisCancelStore):
        """빈 session_id도 키 형식으로 변환된다."""
        assert store._key("") == "cancel:"

    def test_key_with_special_characters(self, store: RedisCancelStore):
        """특수문자가 포함된 session_id도 그대로 사용된다."""
        assert store._key("session:123/abc") == "cancel:session:123/abc"
