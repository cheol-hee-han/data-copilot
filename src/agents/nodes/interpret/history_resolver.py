"""대화 이력 해소 노드 — 맥락 판정 + 질의 재작성 + 명확화 분기.

이전 대화를 참조하는 후속 질의, 명확화 응답, 독립 질의를 판별하여
파이프라인 경로를 결정한다. CONTINUE/NEW/UNSURE 3가지 판정을 통해:
  - CONTINUE: 이전 맥락을 병합한 완전한 질의로 재작성
  - NEW: 명확화 상태 리셋, 원본 질의로 진행
  - UNSURE: 맥락 인지형 명확화 질문 생성 → 사용자 확인

핵심 함수:
    - resolve_history_node: state를 읽어 맥락 판정 → 분기 결과 반환

위임 구조:
    - 비즈니스 로직: services/history_resolver.py (resolve_history)
    - 프롬프트: nodes/prompts/system_prompts.py에서 HISTORY_RESOLVE,
      HISTORY_RESOLVE_USER를 로드하여 서비스에 주입

폴백:
    - 대화 이력이 없고 명확화 대기 아니면 스킵 (LLM 호출 없음)
    - LLM 호출 실패 시 NEW로 폴백하여 원본으로 파이프라인 진행
"""

from __future__ import annotations

from src.agents.nodes.system_prompts import (
    HISTORY_RESOLVE,
    HISTORY_RESOLVE_USER,
)
from src.agents.state.state import (
    PipelineState,
    QueryStatus,
    add_trace,
)
from src.services.history_resolver import (
    HistoryDecision,
    build_unsure_clarification,
    resolve_history,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)


async def resolve_history_node(
    state: PipelineState,
) -> dict:
    """대화 이력을 해소하고 맥락에 따라 분기한다."""
    query = state.preprocessed_input
    history = state.conversation_history

    result = await resolve_history(
        query,
        history,
        system_prompt=HISTORY_RESOLVE,
        user_template=HISTORY_RESOLVE_USER,
        awaiting_clarification=state.awaiting_clarification,
    )

    # ── SKIP: 이력 없음, LLM 호출 안 함 ──
    if result.decision == HistoryDecision.SKIP:
        return {
            "trace_log": add_trace(
                state, "이력해소",
                "스킵 (독립 질의, 이력 없음)",
            ),
        }

    # ── CONTINUE: 이전 맥락 이어짐 → 재작성 ──
    if result.decision == HistoryDecision.CONTINUE:
        logger.info(
            "CONTINUE: 질의 재작성",
            original=query[:50],
            resolved=result.resolved_query[:50],
        )
        return {
            "preprocessed_input": result.resolved_query,
            # 명확화 상태 리셋 (명확화 응답이 처리되었으므로)
            "awaiting_clarification": False,
            "clarification_response": "",
            "trace_log": add_trace(
                state, "이력해소",
                f"CONTINUE — 질의 재작성 ({result.reason})",
                f"원본: {query[:40]} → "
                f"재작성: {result.resolved_query[:40]}",
            ),
        }

    # ── NEW: 독립 질의 → 명확화 상태 리셋 ──
    if result.decision == HistoryDecision.NEW:
        logger.info("NEW: 독립 질의, 명확화 상태 리셋")
        return {
            # 원본 유지 (preprocessed_input 변경 안 함)
            "awaiting_clarification": False,
            "clarification_response": "",
            "clarification_question": "",
            "trace_log": add_trace(
                state, "이력해소",
                f"NEW — 독립 질의 ({result.reason})",
            ),
        }

    # ── UNSURE: 불확실 → 명확화 질문 생성 ──
    clarification_q = build_unsure_clarification(history)

    logger.info(
        "UNSURE: 명확화 질문 생성",
        query=query[:50],
        question=clarification_q[:50],
    )

    return {
        "clarification_question": clarification_q,
        "formatted_response": clarification_q,
        "awaiting_clarification": True,
        "status": QueryStatus.AWAITING_CLARIFICATION,
        "trace_log": add_trace(
            state, "이력해소",
            f"UNSURE — 명확화 질문 생성 ({result.reason})",
            f"질문: {clarification_q[:40]}",
        ),
    }
