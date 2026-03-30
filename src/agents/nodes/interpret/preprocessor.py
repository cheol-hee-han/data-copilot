"""입력 전처리 노드 — 사용자 자연어 입력의 보안 검사 및 정규화.

파이프라인의 첫 번째 노드로, 사용자 원문을 안전하게 정제하여 이후 노드가
일관된 형태의 입력을 받을 수 있도록 보장한다.
보안 검사(인젝션 감지, 길이 제한)와 텍스트 정규화(유니코드, 공백)만 수행한다.

명확화 응답 합성과 대화 맥락 해소는 다음 노드(resolve_history)에서 처리한다.

핵심 함수:
    - preprocess_node: state.user_input을 읽어 정규화하여
      state.preprocessed_input에 기록

위임 구조:
    - 비즈니스 로직: services/input_sanitizer.py (sanitize)
"""

from __future__ import annotations

from src.agents.state.state import PipelineState, QueryStatus, add_trace
from src.services.input_sanitizer import (
    mask_for_logging,
    sanitize,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)


async def preprocess_node(state: PipelineState) -> dict:
    """사용자 입력을 전처리한다."""
    logger.info(
        "입력 전처리 시작",
        user_input=mask_for_logging(state.user_input),
    )

    result = sanitize(state.user_input)

    if result.is_error:
        return {
            "status": QueryStatus.ERROR,
            "error_message": result.error_message,
        }

    logger.info(
        "입력 전처리 완료",
        preprocessed_input=result.text,
    )
    return {
        "preprocessed_input": result.text,
        "status": QueryStatus.PREPROCESSING,
        "trace_log": add_trace(
            state, "전처리",
            "입력 정규화 완료",
            result.text,
        ),
    }
