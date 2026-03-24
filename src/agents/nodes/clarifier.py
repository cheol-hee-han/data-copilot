"""명확화 질문 생성 노드 — 모호한 요청에 대한 사용자 확인 유도.

의도 분류 또는 질의 정규화 단계에서 모호성이 감지되었을 때 호출되며,
LLM 을 직접 호출하여 사용자에게 되물을 명확화 질문을 생성한다.
대화 히스토리(최근 4턴)를 포함한 메시지를 구성하여 맥락을 유지하고,
생성된 질문을 clarification_question 과 formatted_response 에 기록한 뒤
awaiting_clarification=True 로 설정하여 파이프라인을 일시 중단(END)한다.

핵심 함수:
    - clarify_node: state.preprocessed_input, state.conversation_history 를 읽어
      명확화 질문을 생성하고 state.clarification_question,
      state.awaiting_clarification 에 기록
    - _build_messages: 대화 히스토리와 현재 입력을 LLM 메시지 리스트로 조립

위임 구조:
    - 비즈니스 로직: 이 노드가 LLM 을 직접 호출 (별도 서비스 없음)
    - 프롬프트: nodes/prompts/system_prompts.py 에서 CLARIFICATION 프롬프트를 로드

폴백:
    - LLM 호출 실패 시 하드코딩된 기본 질문("요청을 좀 더 구체적으로 알려주시겠어요?")을
      반환하여 사용자 경험이 끊기지 않도록 한다.
"""

from __future__ import annotations

from src.agents.nodes.prompts.system_prompts import (
    CLARIFICATION,
)
from src.agents.state.state import (
    PipelineState,
    QueryStatus,
    add_trace,
)
from src.config import settings
from src.utils.llm import get_llm_client
from src.utils.logger import get_logger

logger = get_logger(__name__)


async def clarify_node(
    state: PipelineState,
) -> dict:
    """모호한 요청에 대한 명확화 질문을 생성한다."""
    logger.info(
        "명확화 질문 생성",
        input=state.preprocessed_input[:80],
        turns=state.clarification_turns,
    )

    messages = _build_messages(state)

    try:
        client = get_llm_client()
        response = await client.messages.create(
            model=settings.llm_model,
            max_tokens=500,
            timeout=settings.llm_default_timeout,
            system=CLARIFICATION,
            messages=messages,
        )
        question = response.content[0].text.strip()
    except Exception as e:
        logger.error(
            "명확화 LLM 호출 오류", error=str(e),
        )
        question = (
            "요청을 좀 더 구체적으로 알려주시겠어요? "
            "어떤 데이터가 필요하신지 "
            "자세히 말씀해주세요."
        )

    logger.info(
        "명확화 질문 생성 완료",
        question=question[:100],
    )

    return {
        "clarification_question": question,
        "formatted_response": question,
        "awaiting_clarification": True,
        "status": QueryStatus.AWAITING_CLARIFICATION,
        "trace_log": add_trace(
            state, "명확화",
            "명확화 질문 생성 완료",
            question[:80],
        ),
    }


def _build_messages(
    state: PipelineState,
) -> list[dict[str, str]]:
    """대화 히스토리와 현재 입력을 LLM 메시지로 조립한다."""
    messages: list[dict[str, str]] = []

    recent_history = (
        state.conversation_history[-4:]
        if state.conversation_history
        else []
    )
    for turn in recent_history:
        messages.append(
            {"role": turn["role"], "content": turn["content"]}
        )

    messages.append(
        {"role": "user", "content": state.preprocessed_input}
    )

    return messages
