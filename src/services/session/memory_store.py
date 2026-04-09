"""인메모리(dict) 세션 스토어 — 개발/테스트용.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

서버 프로세스 메모리에 대화 이력을 저장한다.
서버 재시작 시 모든 세션 데이터가 소실되므로 운영에서는 RedisSessionStore를 사용한다.
max_sessions 초과 시 dict 삽입 순서(Python 3.7+)를 활용한 FIFO 방식으로
가장 오래된 세션을 제거하여 메모리 사용량을 제한한다.

connect/disconnect는 no-op이며, health_check는 항상 True를 반환한다.
이를 통해 SessionStore 인터페이스와의 호환성을 유지하면서
외부 의존 없이 즉시 사용 가능하다.
"""

from __future__ import annotations

from src.config import settings
from src.services.session.store import SessionStore
from src.utils.logger import get_logger

logger = get_logger(__name__)


class MemorySessionStore(SessionStore):
    """인메모리 dict 기반 세션 스토어."""

    def __init__(self) -> None:
        self._history: dict[str, list[dict[str, str]]] = {}
        self._max_sessions = settings.max_sessions
        self._max_history = settings.session_max_history

    async def get_history(
        self, session_id: str,
    ) -> list[dict[str, str]]:
        return list(self._history.get(session_id, []))

    async def append_history(
        self, session_id: str, entry: dict[str, str],
    ) -> None:
        history = self._history.setdefault(session_id, [])
        history.append(entry)
        # 최대 턴 수 초과 시 오래된 항목 제거
        if len(history) > self._max_history:
            del history[: len(history) - self._max_history]

    async def ensure_session(self, session_id: str) -> None:
        if session_id in self._history:
            return
        # FIFO 제거
        if len(self._history) >= self._max_sessions:
            oldest = next(iter(self._history))
            del self._history[oldest]
        self._history[session_id] = []

    async def clear_session(self, session_id: str) -> None:
        self._history.pop(session_id, None)

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        self._history.clear()

    async def health_check(self) -> bool:
        return True
