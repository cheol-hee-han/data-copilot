"""명확화 질문 생성 노드 — 모호한 요청·일상대화·일반질문·메타질문 대응.

의도 분류 또는 질의 정규화 단계에서 명확화가 필요하다고 판단되었을 때 호출된다.
state.intent 값에 따라 LLM 을 호출하여 적절한 응답을 생성한다.

진입 경로:
    1. classify_intent → clarify
       - intent: CLARIFICATION_NEEDED, CASUAL_TALK,
         GENERAL_QUESTION, META_QUESTION
       - ambiguities 없음
    2. normalize_query → clarify
       - intent: CLARIFICATION_NEEDED (정규화 시 업데이트)
       - ambiguities 있음 (normalized_query.ambiguities)

패스스루:
    - state.clarification_question 이 이미 채워져 있으면 LLM 호출 없이
      기존 질문을 그대로 사용한다.

핵심 함수:
    - clarify_node: state.intent, state.preprocessed_input,
      state.normalized_query.ambiguities 를 읽어 명확화 질문을 생성하고
      state.clarification_question, state.awaiting_clarification 에 기록
    - _build_messages: 대화 히스토리·intent·ambiguities·현재 입력을
      LLM 메시지 리스트로 조립

위임 구조:
    - 비즈니스 로직: 이 노드가 LLM 을 직접 호출 (별도 서비스 없음)
    - 프롬프트: system_prompts.py 에서 CLARIFIER_SYSTEM, CLARIFIER_USER 로드

폴백:
    - LLM 호출 실패 시 하드코딩된 기본 질문을 반환하여
      사용자 경험이 끊기지 않도록 한다.
"""

from __future__ import annotations

from src.agents.nodes.system_prompts import (
    CLARIFIER_SYSTEM,
    CLARIFIER_USER,
)
from src.agents.state.state import (
    PipelineState,
    QueryStatus,
    add_trace,
)
from src.config import settings
from src.utils.llm import get_llm_client, render_prompt
from src.utils.logger import get_logger
from src.utils.tracker import record_prompt_variables
from src.utils.truncate import truncate_log

logger = get_logger(__name__)


async def clarify_node(
    state: PipelineState,
) -> dict:
    """명확화 질문을 생성하거나, 이미 생성된 질문을 패스스루한다."""
    logger.info(
        "명확화 질문 생성",
        input=truncate_log(state.preprocessed_input),
        intent=state.intent.value,
        turns=state.clarification_turns,
    )

    # 패스스루: 상류에서 이미 질문을 만들어왔으면 그대로 사용
    if state.clarification_question:
        question = state.clarification_question
        logger.info(
            "기존 명확화 질문 패스스루",
            question=truncate_log(question),
        )
    else:
        question = await _generate_question(state)

    logger.info(
        "명확화 질문 확정",
        question=truncate_log(question),
    )

    return {
        "clarification_question": question,
        "formatted_response": question,
        "awaiting_clarification": True,
        "clarification_turns": state.clarification_turns + 1,
        "status": QueryStatus.AWAITING_CLARIFICATION,
        "trace_log": add_trace(
            state, "명확화",
            f"명확화 질문 생성 완료 (intent={state.intent.value})",
            question,
        ),
    }


async def _generate_question(
    state: PipelineState,
) -> str:
    """LLM 을 호출하여 명확화 질문을 생성한다."""
    messages, prompt_vars = _build_messages(state)
    await record_prompt_variables(prompt_vars)

    try:
        client = get_llm_client()
        response = await client.messages.create(
            model=settings.llm_model,
            max_tokens=500,
            timeout=settings.llm_default_timeout,
            system=CLARIFIER_SYSTEM,
            messages=messages,
        )
        return response.content[0].text.strip()
    except Exception as e:
        logger.error(
            "명확화 LLM 호출 오류", error=str(e),
        )
        return (
            "요청을 좀 더 구체적으로 알려주시겠어요? "
            "어떤 데이터가 필요하신지 "
            "자세히 말씀해주세요."
        )


def _build_messages(
    state: PipelineState,
) -> tuple[list[dict[str, str]], dict[str, str]]:
    """대화 히스토리·intent·ambiguities·현재 입력을 LLM 메시지로 조립한다."""
    messages: list[dict[str, str]] = []

    recent_history = (
        state.conversation_history[-settings.prompt_history_window:]
        if state.conversation_history
        else []
    )
    for turn in recent_history:
        messages.append(
            {"role": turn["role"], "content": turn["content"]}
        )

    # ambiguities 블록 구성 (있을 때만 삽입)
    ambiguities_block = ""
    nq = state.normalized_query
    if nq and hasattr(nq, "ambiguities") and nq.ambiguities:
        joined = " / ".join(nq.ambiguities)
        ambiguities_block = f"[ambiguities]: {joined}\n"

    user_content, prompt_vars = render_prompt(CLARIFIER_USER, {
        "{intent}": state.intent.value,
        "{ambiguities_block}": ambiguities_block,
        "{query}": state.preprocessed_input,
    })
    messages.append({"role": "user", "content": user_content})

    return messages, prompt_vars
