"""연속 여부 판정 + 의도 분류 통합 노드.

resolve_history + classify_intent를 단일 LLM 호출로 통합한다.
CONTINUE 시 continue_context를 preprocessed_input에 반영하여
하류 노드가 맥락을 활용할 수 있게 한다.
"""

from __future__ import annotations

from src.agents.models.clarification import AmbiguitySignal
from src.agents.nodes.system_prompts import (
    CONTEXT_CLASSIFIER_SYSTEM,
    CONTEXT_CLASSIFIER_USER,
)
from src.agents.state.state import (
    PipelineState,
    QueryStatus,
    add_trace,
)
from src.models.enums import HistoryDecision
from src.services.context_classifier import (
    ContextClassifyResult,
    context_classifier,
)
from src.utils.logger import get_logger
from src.utils.truncate import truncate_log

logger = get_logger(__name__)


def _build_clarification_history(state: PipelineState) -> str:
    """명확화 이력을 프롬프트용 텍스트로 조립한다.

    clarification_handler의 interrupt/resume 사이클 후
    resolved_signals에 저장된 질문-응답을 추출한다.
    """
    # resolved_signals에서 context_classifier가 발생시킨 명확화 이력 추출
    parts: list[str] = []
    for signal in state.resolved_signals:
        if signal.source_node != "context_classifier":
            continue
        if signal.question:
            parts.append(f"시스템: {signal.question}")
        if signal.answer:
            parts.append(f"사용자: {signal.answer}")

    if not parts:
        return ""
    return "\n".join(parts)


def _build_trace(
    state: PipelineState,
    result: ContextClassifyResult,
) -> list:
    """resolution별 trace_log를 생성한다."""
    if result.resolution == HistoryDecision.CONTINUE:
        return add_trace(
            state, "맥락분류",
            f"CONTINUE+{result.category}",
            f"사유: {result.continue_reason}\n"
            f"맥락반영: {truncate_log(result.continue_context)}",
        )
    if result.resolution == HistoryDecision.NEW:
        return add_trace(
            state, "맥락분류",
            f"NEW+{result.category}",
            f"독립 질의, 의도: {result.intent.value}",
        )
    # SKIP (이력 없음)
    return add_trace(
        state, "맥락분류",
        f"SKIP+{result.category}",
        f"의도: {result.intent.value} "
        f"(신뢰도 {result.confidence:.0%})",
    )


async def context_classifier_node(
    state: PipelineState,
) -> dict:
    """대화 연속 여부를 판정하고 의도를 분류한다."""
    query = state.preprocessed_input
    history = state.conversation_history

    result = await context_classifier(
        query,
        history,
        system_prompt=CONTEXT_CLASSIFIER_SYSTEM,
        user_template=CONTEXT_CLASSIFIER_USER,
        clarification_history=_build_clarification_history(state),
    )

    # ── 에러 ──
    if result.is_error:
        return {
            "intent": result.intent,
            "intent_confidence": result.confidence,
            "status": QueryStatus.ERROR,
            "error_message": "질의 해석에 실패했습니다. 다시 시도해주세요.",
        }

    # ── UNSURE / AMBIGUOUS → AmbiguitySignal (무한루프 방어 포함) ──
    if (
        result.resolution == HistoryDecision.UNSURE
        or result.category == "AMBIGUOUS"
    ):
        from src.config import settings
        ask_count = sum(
            1 for s in state.resolved_signals
            if s.decision == "ASK"
        )
        if ask_count >= settings.clarification_max_turns:
            logger.warning(
                "명확화 상한 초과, 강제 진행",
                ask_count=ask_count,
            )
            from src.models.enums import IntentType as _IT
            return {
                "intent": _IT.DATA_EXTRACTION,
                "intent_confidence": 0.4,
                "query_category": "DATA_EXTRACTION",
                "is_continuation": False,
                "status": QueryStatus.INTENT_CLASSIFIED,
                "trace_log": add_trace(
                    state, "맥락분류",
                    "명확화 상한 초과, 강제 진행",
                    f"횟수={ask_count}",
                ),
            }

        # LLM ambiguities에서 AmbiguitySignal 생성
        amb = (
            result.ambiguities[0]
            if result.ambiguities
            else {}
        )
        is_unsure = (
            result.resolution == HistoryDecision.UNSURE
        )

        # 폴백: LLM이 ambiguities를 안 생성한 경우
        fallback_question = (
            "이전 대화에 이어서 질문하신 건지, "
            "새로운 데이터를 찾으시는 건지 알려주시겠어요?"
            if is_unsure
            else f"요청하신 내용을 좀 더 구체적으로 "
                 f"알려주시겠어요?\n{result.reason or ''}"
        )

        signal = AmbiguitySignal(
            source_node="context_classifier",
            decision="ASK",
            ambiguity_type=amb.get(
                "ambiguity_type",
                "CONTEXT" if is_unsure else "INTENT",
            ),
            confidence=amb.get("confidence", "LOW"),
            question=amb.get("question", fallback_question),
            question_type=amb.get(
                "question_type",
                "confirm" if is_unsure else "single_select",
            ),
            options=amb.get("options", []),
            inferred_value=amb.get("inferred_value"),
            reasoning=amb.get(
                "reasoning",
                result.reason or "",
            ),
        )

        label = "UNSURE" if is_unsure else "AMBIGUOUS"
        return {
            "pending_signals": [signal],
            "intent": result.intent,
            "intent_confidence": result.confidence,
            "query_category": result.category,
            "status": QueryStatus.INTENT_CLASSIFIED,
            "trace_log": add_trace(
                state, "맥락분류",
                f"{label} — 명확화 신호 생성",
                f"질문: {signal.question}",
            ),
        }

    # ── 정상 경로: SKIP / NEW / CONTINUE ──
    updates: dict = {
        "intent": result.intent,
        "intent_confidence": result.confidence,
        "query_category": result.category,
        "is_continuation": result.resolution == HistoryDecision.CONTINUE,
        "status": QueryStatus.INTENT_CLASSIFIED,
        "trace_log": _build_trace(state, result),
    }

    if result.resolution == HistoryDecision.CONTINUE:
        updates["continue_context"] = result.continue_context
        # 하류 노드가 continue_context를 직접 참조하도록 전환 전까지
        # preprocessed_input에 맥락 반영 질의를 설정하여 맥락 손실 방지
        if result.continue_context:
            updates["preprocessed_input"] = result.continue_context

    # ── 추적 이벤트 ──
    from src.utils.tracker.dispatch import (
        dispatch_tracking_event,
        DECISION_INTENT,
    )
    await dispatch_tracking_event(DECISION_INTENT, {
        "node": "context_classifier",
        "decision_type": "intent_classification",
        "resolution": result.resolution.value,
        "chosen": result.intent.value,
        "confidence": result.confidence,
        "reason": (
            result.continue_reason
            if result.resolution == HistoryDecision.CONTINUE
            else result.reason
        ),
    })

    return updates
