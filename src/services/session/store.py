"""세션 스토어 인터페이스 및 팩토리.

대화 이력(history)과 명확화 대기 상태(clarification)를 추상화하여,
memory(dict)와 redis 구현체를 동일한 인터페이스로 사용할 수 있게 한다.
settings.session_backend 값에 따라 팩토리가 적절한 구현체를 반환한다.

핵심 함수:
    - get_session_store: 싱글턴 세션 스토어 반환 (최초 호출 시 생성)
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

# 싱글턴 인스턴스
_store: SessionStore | None = None


class SessionStore(ABC):
    """세션 스토어 인터페이스."""

    @abstractmethod
    async def get_history(
        self, session_id: str,
    ) -> list[dict[str, str]]:
        """대화 이력을 반환한다."""

    @abstractmethod
    async def append_history(
        self, session_id: str, entry: dict[str, str],
    ) -> None:
        """대화 이력에 1건을 추가한다."""

    @abstractmethod
    async def get_clarification(
        self, session_id: str,
    ) -> dict | None:
        """명확화 대기 상태를 반환하고 삭제(pop)한다."""

    @abstractmethod
    async def set_clarification(
        self, session_id: str, state: dict,
    ) -> None:
        """명확화 대기 상태를 저장한다."""

    @abstractmethod
    async def ensure_session(self, session_id: str) -> None:
        """세션이 없으면 초기화한다."""

    @abstractmethod
    async def clear_session(self, session_id: str) -> None:
        """세션의 대화 이력과 명확화 상태를 모두 삭제한다."""

    @abstractmethod
    async def connect(self) -> None:
        """스토어 연결을 초기화한다."""

    @abstractmethod
    async def disconnect(self) -> None:
        """스토어 연결을 해제한다."""

    @abstractmethod
    async def health_check(self) -> bool:
        """스토어 상태를 확인한다."""


def get_session_store() -> SessionStore:
    """설정에 따라 세션 스토어 싱글턴을 반환한다."""
    global _store
    if _store is not None:
        return _store

    backend = settings.session_backend.lower()
    if backend == "redis":
        from src.services.session.redis_store import (
            RedisSessionStore,
        )
        _store = RedisSessionStore()
        logger.info("세션 백엔드: Redis")
    else:
        from src.services.session.memory_store import (
            MemorySessionStore,
        )
        _store = MemorySessionStore()
        logger.info("세션 백엔드: Memory (dict)")

    return _store
