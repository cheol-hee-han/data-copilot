"""비데이터 의도 경량 응답 노드.

CASUAL_TALK, META_QUESTION 등 데이터 추출/분석이 필요 없는
의도에 대해 간단한 정형 응답을 생성한다.

CASUAL_TALK → 정형 응답 (LLM 호출 없음)
META_QUESTION → 시스템 안내 메시지
"""

from __future__ import annotations

from src.agents.state.state import (
    PipelineState,
    QueryStatus,
    add_trace,
)
from src.models.enums import IntentType
from src.utils.logger import get_logger

logger = get_logger(__name__)

# ── 정형 응답 ──

_CASUAL_RESPONSES: dict[str, str] = {
    "안녕": "안녕하세요! 데이터 관련 궁금한 점이 있으시면 편하게 물어보세요.",
    "감사": "도움이 되었다니 기쁩니다. 추가로 필요한 데이터가 있으시면 말씀해주세요.",
    "수고": "감사합니다! 더 필요하신 게 있으시면 언제든 말씀해주세요.",
    "됐어": "네, 알겠습니다. 새로운 데이터가 필요하시면 말씀해주세요.",
    "그만": "네, 알겠습니다. 새로운 데이터가 필요하시면 말씀해주세요.",
}

_CASUAL_DEFAULT = (
    "안녕하세요! 저는 데이터 분석을 도와드리는 AI 어시스턴트입니다. "
    "데이터 추출이나 분석이 필요하시면 편하게 말씀해주세요."
)

_META_RESPONSE = (
    "저는 은행 업무 데이터를 조회하고 분석하는 AI 어시스턴트입니다.\n\n"
    "다음과 같은 요청을 처리할 수 있어요:\n"
    "- 고객, 여신, 수신, 카드 등 업무 데이터 조회\n"
    "- 기간별 추이, 지점별 비교 등 데이터 분석\n"
    "- 연체율, 실적 등 주요 지표 확인\n\n"
    "궁금하신 데이터가 있으시면 자연스럽게 말씀해주세요!"
)


async def simple_responder_node(
    state: PipelineState,
) -> dict:
    """비데이터 의도에 대해 경량 응답을 생성한다."""
    query = state.preprocessed_input
    intent = state.intent

    if intent == IntentType.CASUAL_TALK:
        response = _match_casual_response(query)
        label = "일반대화"
    elif intent == IntentType.META_QUESTION:
        response = _META_RESPONSE
        label = "메타질문"
    else:
        response = _CASUAL_DEFAULT
        label = "기타"

    logger.info(
        "비데이터 의도 응답 생성",
        intent=intent.value,
        label=label,
    )

    return {
        "formatted_response": response,
        "status": QueryStatus.COMPLETED,
        "is_continuation": False,
        "continue_context": "",
        "trace_log": add_trace(
            state, label,
            f"{intent.value} → 경량 응답",
            response[:50],
        ),
    }


def _match_casual_response(query: str) -> str:
    """입력에서 키워드를 매칭하여 적절한 정형 응답을 반환한다."""
    q = query.strip()
    for keyword, response in _CASUAL_RESPONSES.items():
        if keyword in q:
            return response
    return _CASUAL_DEFAULT
