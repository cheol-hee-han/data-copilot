"""의도 분류 노드 — Intent Gate 통합.

전처리된 사용자 입력의 의도(데이터 조회, 분석, 일상 대화 등)를 LLM 으로 분류한다.
normalization_enabled 설정에 따라 Intent Gate(구조화 분류) 방식을 우선 시도하고,
Gate 실패 시 Legacy 분류 방식으로 자동 폴백한다.
분류 결과로 intent, intent_confidence, query_category 상태 필드를 기록하며,
이후 라우팅 노드가 이 값을 기반으로 파이프라인 경로를 결정한다.

핵심 함수:
    - classify_intent_node: state.preprocessed_input 을 읽어 의도를 분류하고
      state.intent, state.intent_confidence, state.query_category 에 기록

위임 구조:
    - 비즈니스 로직: services/intent_resolver.py (classify_with_gate, classify_legacy)
    - 프롬프트: nodes/prompts/system_prompts.py 에서 INTENT_GATE, INTENT_CLASSIFICATION 등을
      로드하여 서비스에 주입

폴백:
    - Intent Gate 호출이 실패(is_error=True)하면 Legacy 분류로 자동 폴백한다.
    - 양쪽 모두 실패 시 QueryStatus.ERROR 를 반환하고 사용자에게 일반 에러 메시지를 전달한다.
"""

from __future__ import annotations

from src.agents.models.user_messages import ERR_GENERIC, format_error
from src.agents.nodes.prompts.system_prompts import (
    INTENT_CLASSIFICATION,
    INTENT_FORMAT_HINT,
    INTENT_GATE,
    INTENT_GATE_USER,
)
from src.agents.state.state import (
    PipelineState,
    QueryStatus,
    add_trace,
)
from src.config import settings
from src.services.intent_resolver import (
    classify_legacy,
    classify_with_gate,
    get_intent_label,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)


async def classify_intent_node(
    state: PipelineState,
) -> dict:
    """사용자 입력의 의도를 분류한다."""
    logger.info(
        "의도 분류 시작",
        input=state.preprocessed_input,
    )

    if settings.normalization_enabled:
        result = await classify_with_gate(
            state.preprocessed_input,
            system_prompt=INTENT_GATE,
            user_template=INTENT_GATE_USER,
        )
        # Gate 실패 시 Legacy 폴백
        if result.is_error:
            result = await classify_legacy(
                state.preprocessed_input,
                system_prompt=INTENT_CLASSIFICATION,
                format_hint=INTENT_FORMAT_HINT,
            )
    else:
        result = await classify_legacy(
            state.preprocessed_input,
            system_prompt=INTENT_CLASSIFICATION,
            format_hint=INTENT_FORMAT_HINT,
        )

    if result.is_error:
        return {
            "intent": result.intent,
            "intent_confidence": result.confidence,
            "status": QueryStatus.ERROR,
            "error_message": format_error(ERR_GENERIC),
        }

    label = get_intent_label(result.intent)

    logger.info(
        "의도 분류 완료",
        intent=result.intent.value,
        confidence=result.confidence,
    )

    return {
        "intent": result.intent,
        "intent_confidence": result.confidence,
        "query_category": result.category,
        "status": QueryStatus.INTENT_CLASSIFIED,
        "trace_log": add_trace(
            state, "의도분류",
            f"'{label}' 의도로 분류 "
            f"(신뢰도 {result.confidence:.0%})",
            f"카테고리={result.category}, "
            f"근거={result.reason}",
        ),
    }
