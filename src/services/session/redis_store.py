"""Redis 세션 스토어 — 운영/프로덕션용.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

Redis에 대화 이력을 JSON 문자열로 저장하여
서버 재시작, 다중 워커, 수평 확장 환경에서도 세션을 유지한다.
redis.asyncio를 지연 임포트하여 redis 패키지 미설치 환경에서도
모듈 로드가 가능하도록 설계되었다.

명확화 상태 관리는 checkpointer + interrupt() 패턴으로 이관되어
이 스토어는 대화 이력 관리에만 집중한다.

키 구조:
    session:{sid}:history  — JSON 배열 (대화 이력)

TTL 정책:
    - history: 슬라이딩 TTL (매 append_history 시 갱신, 기본 30분)
      활발한 대화는 TTL이 계속 연장되고, 비활성 세션은 자동 만료된다.
"""

from __future__ import annotations

import json

from src.config import settings
from src.services.session.store import SessionStore
from src.utils.logger import get_logger

logger = get_logger(__name__)

# redis 미설치 환경(memory 백엔드만 사용)에서도 모듈 로드 가능하도록 지연 임포트
_redis_module = None


def _get_redis():
    """redis.asyncio 모듈을 지연 임포트한다."""
    global _redis_module
    if _redis_module is None:
        import redis.asyncio as aioredis
        _redis_module = aioredis
    return _redis_module


class RedisSessionStore(SessionStore):
    """Redis 기반 세션 스토어."""

    def __init__(self) -> None:
        self._client = None
        self._ttl = settings.session_ttl
        self._max_history = settings.session_max_history
        self._prefix = "session"

    def _key(self, session_id: str, suffix: str) -> str:
        return f"{self._prefix}:{session_id}:{suffix}"

    async def connect(self) -> None:
        """Redis 연결을 초기화한다."""
        if self._client is not None:
            return
        aioredis = _get_redis()
        self._client = aioredis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            password=settings.redis_password or None,
            decode_responses=True,
        )
        logger.info(
            "Redis 세션 스토어 연결",
            host=settings.redis_host,
            port=settings.redis_port,
        )

    async def disconnect(self) -> None:
        """Redis 연결을 해제한다."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def health_check(self) -> bool:
        """Redis PING 응답을 확인한다."""
        if not self._client:
            return False
        try:
            return await self._client.ping()
        except Exception as e:
            logger.debug("Redis health_check 실패", error=str(e))
            return False

    async def get_history(
        self, session_id: str,
    ) -> list[dict[str, str]]:
        key = self._key(session_id, "history")
        raw = await self._client.get(key)
        if not raw:
            return []
        return json.loads(raw)

    async def append_history(
        self, session_id: str, entry: dict[str, str],
    ) -> None:
        key = self._key(session_id, "history")
        history = await self.get_history(session_id)
        history.append(entry)
        # 최대 턴 수 초과 시 오래된 항목 제거
        if len(history) > self._max_history:
            history = history[-self._max_history:]
        await self._client.set(
            key,
            json.dumps(history, ensure_ascii=False),
            ex=self._ttl,  # 슬라이딩 TTL: 매 append 시 갱신
        )

    async def ensure_session(self, session_id: str) -> None:
        """세션 존재 확인 (Redis는 TTL로 관리하므로 별도 초기화 불필요)."""

    async def clear_session(self, session_id: str) -> None:
        """세션의 대화 이력을 삭제한다."""
        await self._client.delete(
            self._key(session_id, "history"),
        )
