"""세션 스토어 인터페이스 및 팩토리.

대화 이력(history)을 추상화하여, memory(dict)와 redis 구현체를
동일한 인터페이스로 사용할 수 있게 한다.
settings.session_backend 값에 따라 팩토리가 적절한 구현체를 반환한다.

대화 이력 엔트리 구조::

    {"role": "user"|"assistant", "content": "...", "type": "query"|"response"|"clarification"}

type 필드로 일반 질의/응답과 명확화 Q&A를 구분한다.
resolve_history 등 맥락 해소 노드는 type="query"|"response"만
필터링하여 사용할 수 있다.

명확화 상태 관리:
    checkpointer + interrupt() 패턴으로 이관됨.
    get_clarification / set_clarification은 deprecated.

핵심 함수:
    - get_session_store: 싱글턴 세션 스토어 반환 (최초 호출 시 생성)
"""

from __future__ import annotations

import warnings
from abc import ABC, abstractmethod
from enum import StrEnum

from src.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)


class HistoryEntryType(StrEnum):
    """대화 이력 엔트리의 유형."""

    QUERY = "query"                # 사용자 일반 질의
    RESPONSE = "response"          # 시스템 일반 응답
    CLARIFICATION = "clarification"  # 명확화 질문 또는 응답


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

    async def get_clarification(
        self, session_id: str,
    ) -> dict | None:
        """Deprecated: checkpointer + interrupt()로 대체됨."""
        warnings.warn(
            "get_clarification은 deprecated. "
            "checkpointer interrupt 패턴을 사용하세요.",
            DeprecationWarning,
            stacklevel=2,
        )
        return None

    async def set_clarification(
        self, session_id: str, state: dict,
    ) -> None:
        """Deprecated: checkpointer + interrupt()로 대체됨."""
        warnings.warn(
            "set_clarification은 deprecated. "
            "checkpointer interrupt 패턴을 사용하세요.",
            DeprecationWarning,
            stacklevel=2,
        )

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
