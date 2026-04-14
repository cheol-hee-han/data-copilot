"""노드/라우팅에서 사용하는 취소 체크 유틸.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

싱글턴 CancelStore를 보유하며, lifespan에서 초기화한다.
CancelStore가 미설정이면 (테스트 등) 항상 False를 반환하여
기존 동작에 영향을 주지 않는다.
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Any

from src.agents.state.state import FinalStatus, Phase, QueryStatus
from src.services.cancel_store import CancelStore
from src.utils.logger import get_logger

if TYPE_CHECKING:
    from src.agents.state.state import PipelineState, ReasoningState

logger = get_logger(__name__)

_cancel_store: CancelStore | None = None


def get_cancel_store() -> CancelStore | None:
    """현재 등록된 CancelStore 싱글턴을 반환한다."""
    return _cancel_store


def set_cancel_store(store: CancelStore) -> None:
    """CancelStore 싱글턴을 등록한다 (lifespan 에서 호출)."""
    global _cancel_store
    _cancel_store = store


def reset_cancel_store() -> None:
    """테스트에서 싱글턴을 초기화한다."""
    global _cancel_store
    _cancel_store = None


async def check_cancel(session_id: str, turn_id: str) -> bool:
    """세션+턴의 취소 플래그를 확인한다.

    CancelStore가 미설정이면 항상 False.
    Redis 장애 등 예외 발생 시 False 반환 (cancel 불가 > 파이프라인 실패).
    저장된 값이 turn_id와 일치하거나 와일드카드("*")이면 True.
    """
    if _cancel_store is None:
        return False
    try:
        # 단일 GET: 저장된 값이 turn_id 또는 "*"이면 취소
        if await _cancel_store.is_cancelled(session_id, turn_id):
            logger.info("취소 플래그 감지", session_id=session_id, turn_id=turn_id)
            return True
        if turn_id != "*" and await _cancel_store.is_cancelled(session_id, "*"):
            logger.info("취소 플래그 감지 (와일드카드)", session_id=session_id, turn_id=turn_id)
            return True
        return False
    except Exception:
        logger.warning("취소 플래그 확인 실패 — 무시하고 계속")
        return False


async def clear_cancel(session_id: str) -> None:
    """세션의 취소 플래그를 삭제한다."""
    if _cancel_store is not None:
        try:
            await _cancel_store.clear_cancel(session_id)
        except Exception:
            logger.warning("취소 플래그 삭제 실패", exc_info=True)


async def pop_cancel(session_id: str) -> str | None:
    """세션의 취소 플래그를 반환하고 삭제한다."""
    if _cancel_store is None:
        return None
    try:
        return await _cancel_store.pop_cancel(session_id)
    except Exception:
        logger.warning("취소 플래그 pop 실패", exc_info=True)
        return None


CANCEL_MESSAGE = "사용자 요청으로 중단되었습니다."


def with_cancel_check(node_fn: Any) -> Any:
    """노드 진입 시 cancel 플래그를 체크하는 래퍼.

    ``pipeline.py``의 ``add_node`` 호출부에서 사용하여
    모든 노드의 cancel 체크를 중앙 관리한다.
    """
    @functools.wraps(node_fn)
    async def wrapper(state: PipelineState) -> dict:
        """취소 플래그 확인 후 원본 노드를 실행한다."""
        if await check_cancel(state.session_id, state.turn_id):
            return {
                "status": QueryStatus.CANCELLED,
                "error_message": CANCEL_MESSAGE,
            }
        result: dict[str, Any] = await node_fn(state)
        return result
    return wrapper


def make_cancel_updates(reason_state: ReasoningState) -> dict[str, Any]:
    """취소 시 반환할 state 업데이트 dict.

    mid-node 체크에서 reason 상태까지 갱신이 필요한 경우 사용한다.
    reason을 deep copy하여 phase=DONE, final_status=CANCELLED로 설정.
    error_message를 설정하여 _route_after_result_finalizer에서
    error_end로 라우팅되도록 한다.
    """
    reason = reason_state.model_copy(deep=True)
    reason.phase = Phase.DONE
    reason.final_status = FinalStatus.CANCELLED
    reason.exploration_summary = CANCEL_MESSAGE
    return {
        "reason": reason,
        "status": QueryStatus.CANCELLED,
        "error_message": CANCEL_MESSAGE,
    }
