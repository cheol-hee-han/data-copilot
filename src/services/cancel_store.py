"""파이프라인 취소 플래그 관리.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

Redis 사용 환경에서는 RedisCancelStore,
개발/테스트 환경에서는 MemoryCancelStore를 사용한다.
settings.redis_backend 값에 따라 lifespan에서 선택된다.

주의: MemoryCancelStore는 단일 워커(개발/테스트) 전용.
운영 환경(multi-worker)에서는 반드시 Redis를 사용해야 한다.
"""

from __future__ import annotations

from typing import Any, Protocol


class CancelStore(Protocol):
    """취소 플래그 스토어 프로토콜."""

    async def set_cancel(
        self, session_id: str, turn_id: str,
    ) -> None:
        """취소 플래그를 설정한다."""
        ...

    async def is_cancelled(
        self, session_id: str, turn_id: str,
    ) -> bool:
        """해당 턴이 취소되었는지 확인한다."""
        ...

    async def clear_cancel(self, session_id: str) -> None:
        """취소 플래그를 삭제한다."""
        ...

    async def pop_cancel(self, session_id: str) -> str | None:
        """취소 플래그를 반환하고 삭제한다."""
        ...


class MemoryCancelStore:
    """개발/테스트용 인메모리 취소 스토어.

    단일 워커에서만 동작한다. 운영 환경에서 사용 금지.
    """

    def __init__(self) -> None:
        self._flags: dict[str, str] = {}    # session_id → turn_id

    async def set_cancel(self, session_id: str, turn_id: str) -> None:
        """취소 플래그를 설정한다."""
        self._flags[session_id] = turn_id

    async def is_cancelled(self, session_id: str, turn_id: str) -> bool:
        """해당 턴이 취소되었는지 확인한다."""
        stored = self._flags.get(session_id)
        return stored is not None and stored == turn_id

    async def clear_cancel(self, session_id: str) -> None:
        """취소 플래그를 삭제한다."""
        self._flags.pop(session_id, None)

    async def pop_cancel(self, session_id: str) -> str | None:
        """플래그를 반환하고 삭제한다."""
        return self._flags.pop(session_id, None)


class RedisCancelStore:
    """운영용 Redis 취소 스토어.

    TTL(300초) 안전망으로 플래그 미정리 시 자동 만료.
    값으로 turn_id를 저장하여 턴 간 경쟁 상태를 방어한다.
    """

    _CANCEL_TTL = 300

    def __init__(self, redis_client: Any) -> None:
        self._client = redis_client

    def _key(self, session_id: str) -> str:
        return f"cancel:{session_id}"

    async def set_cancel(self, session_id: str, turn_id: str) -> None:
        """취소 플래그를 설정한다 (TTL 안전망 포함)."""
        await self._client.set(
            self._key(session_id), turn_id, ex=self._CANCEL_TTL,
        )

    async def is_cancelled(self, session_id: str, turn_id: str) -> bool:
        """해당 턴이 취소되었는지 확인한다."""
        stored = await self._client.get(self._key(session_id))
        if stored is None:
            return False
        if isinstance(stored, bytes):
            stored = stored.decode()
        return bool(stored == turn_id)

    async def clear_cancel(self, session_id: str) -> None:
        """취소 플래그를 삭제한다."""
        await self._client.delete(self._key(session_id))

    _POP_SCRIPT = "local v=redis.call('GET',KEYS[1]); if v then redis.call('DEL',KEYS[1]) end; return v"

    async def pop_cancel(self, session_id: str) -> str | None:
        """플래그를 원자적으로 반환하고 삭제한다.

        GETDEL(Redis 6.2+)을 시도하고, 미지원 시 Lua 스크립트로 fallback.
        """
        key = self._key(session_id)
        try:
            val = await self._client.getdel(key)
        except Exception:
            val = await self._client.eval(self._POP_SCRIPT, 1, key)
        if val is None:
            return None
        return val.decode() if isinstance(val, bytes) else val
