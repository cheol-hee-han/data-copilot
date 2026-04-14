"""세션별 활성 파이프라인 추적 스토어.

작성자: 한철희 / 최종수정: 2026-04-10

서버 프로세스 메모리(또는 Redis) 기반으로 "현재 파이프라인 실행 중인 세션"
을 기록한다. 서버 크래시 시 자동으로 비워지므로, 재기동 후 `/active` 조회로
크래시 전 실행 중이던 턴과 실제 실행 중인 턴을 구분할 수 있다.

settings.redis_backend 값에 따라 lifespan에서 CancelStore와 함께 선택된다.

주의: MemoryActiveRunStore는 단일 워커(개발/테스트/Redis 장애 fallback) 전용.
운영(multi-worker)에서는 반드시 Redis를 사용해야 한다.
"""

from __future__ import annotations

from typing import Any, Protocol


class ActiveRunStore(Protocol):
    """활성 파이프라인 스토어 프로토콜."""

    async def set_active(
        self, session_id: str, turn_id: str,
    ) -> None:
        """세션을 활성 상태로 등록한다."""
        ...

    async def clear_active(
        self, session_id: str, turn_id: str,
    ) -> None:
        """세션의 활성 상태를 해제한다 (CAS)."""
        ...

    async def is_active(self, session_id: str) -> bool:
        """세션이 현재 실행 중인지 확인한다."""
        ...


class MemoryActiveRunStore:
    """개발/테스트/단일 워커용 인메모리 활성 파이프라인 스토어.

    운영(multi-worker)에서 주 저장소로 사용 금지. Redis 장애 시 fallback.
    """

    def __init__(self) -> None:
        self._active: dict[str, str] = {}  # session_id → turn_id

    async def set_active(self, session_id: str, turn_id: str) -> None:
        """세션을 활성 상태로 등록한다."""
        self._active[session_id] = turn_id

    async def clear_active(self, session_id: str, turn_id: str) -> None:
        """CAS: 현재 값이 내 turn_id 일 때만 삭제한다.

        동시 재전송 race 방어 — 이전 턴의 finally 가 새 턴의 등록을 덮어쓰지
        않도록 turn_id 매칭 후 삭제.
        """
        if self._active.get(session_id) == turn_id:
            self._active.pop(session_id, None)

    async def is_active(self, session_id: str) -> bool:
        """세션이 현재 실행 중인지 확인한다."""
        return session_id in self._active


class RedisActiveRunStore:
    """운영용 Redis 활성 파이프라인 스토어.

    TTL 안전망으로 워커 크래시(`kill -9`) 시 stale 엔트리 자동 만료.
    값에 turn_id 저장 → clear_active 는 CAS 방식으로 동시 재전송 race 방어.
    cancel_store.pop_cancel 과 동일 원리.
    """

    # 현재 값이 인자와 같으면 삭제. (cancel_store 의 POP 과 유사)
    _CAS_DEL_SCRIPT = (
        "local v=redis.call('GET',KEYS[1]); "
        "if v==ARGV[1] then redis.call('DEL',KEYS[1]); return 1 end; "
        "return 0"
    )

    def __init__(self, redis_client: Any, ttl_seconds: int = 600) -> None:
        self._client = redis_client
        self._ttl = ttl_seconds

    def _key(self, session_id: str) -> str:
        return f"active_run:{session_id}"

    async def set_active(self, session_id: str, turn_id: str) -> None:
        """세션을 활성 상태로 등록한다 (TTL 안전망 포함)."""
        await self._client.set(
            self._key(session_id), turn_id, ex=self._ttl,
        )

    async def clear_active(self, session_id: str, turn_id: str) -> None:
        """CAS: 현재 값이 내 turn_id 일 때만 삭제."""
        await self._client.eval(
            self._CAS_DEL_SCRIPT, 1, self._key(session_id), turn_id,
        )

    async def is_active(self, session_id: str) -> bool:
        """세션이 현재 실행 중인지 확인한다."""
        return bool(await self._client.exists(self._key(session_id)))
