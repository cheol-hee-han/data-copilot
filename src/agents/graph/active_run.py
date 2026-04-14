"""활성 파이프라인 추적 헬퍼 (ActiveRunStore 래퍼).

작성자: 한철희 / 최종수정: 2026-04-10

cancel.py 와 대칭 구조. 싱글턴 ActiveRunStore 를 보유하며, lifespan 에서
초기화한다. Redis 장애 등 예외 발생 시 로깅만 하고 조용히 진행 —
활성 추적 실패가 파이프라인 자체를 막으면 안 된다는 원칙.
"""

from __future__ import annotations

from src.services.active_run_store import ActiveRunStore
from src.utils.logger import get_logger

logger = get_logger(__name__)

_active_run_store: ActiveRunStore | None = None


def get_active_run_store() -> ActiveRunStore | None:
    """현재 등록된 ActiveRunStore 싱글턴을 반환한다."""
    return _active_run_store


def set_active_run_store(store: ActiveRunStore) -> None:
    """ActiveRunStore 싱글턴을 등록한다 (lifespan 에서 호출)."""
    global _active_run_store
    _active_run_store = store


def reset_active_run_store() -> None:
    """테스트에서 싱글턴을 초기화한다."""
    global _active_run_store
    _active_run_store = None


async def mark_active(session_id: str, turn_id: str) -> None:
    """파이프라인 실행 시작을 기록한다.

    ActiveRunStore 미설정 또는 예외 발생 시 조용히 진행한다
    (활성 추적 실패가 파이프라인을 막지 않도록).
    """
    store = _active_run_store
    if store is None:
        return
    try:
        await store.set_active(session_id, turn_id)
    except Exception:
        logger.warning(
            "활성 파이프라인 기록 실패 — 무시하고 계속",
            session_id=session_id,
            exc_info=True,
        )


async def clear_active(session_id: str, turn_id: str) -> None:
    """파이프라인 실행 종료를 기록한다.

    CAS 방식으로 내 turn_id 일 때만 삭제. 예외 발생 시 조용히 진행.
    """
    store = _active_run_store
    if store is None:
        return
    try:
        await store.clear_active(session_id, turn_id)
    except Exception:
        logger.warning(
            "활성 파이프라인 해제 실패 — 무시하고 계속",
            session_id=session_id,
            exc_info=True,
        )


async def check_active(session_id: str) -> bool:
    """세션에 활성 파이프라인이 있는지 조회한다.

    ActiveRunStore 미설정 또는 예외 발생 시 False 반환
    (안전한 기본값 — 크래시로 판정).
    """
    store = _active_run_store
    if store is None:
        return False
    try:
        return await store.is_active(session_id)
    except Exception:
        logger.warning(
            "활성 파이프라인 조회 실패",
            session_id=session_id,
            exc_info=True,
        )
        return False
