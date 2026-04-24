"""연속 여부 판정 + 의도 분류 통합 노드.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

사용자 질의가 이전 대화의 연속인지(CONTINUE/NEW/SKIP) 판정하고,
동시에 의도(DATA_EXTRACTION, DATA_ANALYSIS, CASUAL_TALK 등)를 분류한다.
기존에 별도였던 resolve_history와 classify_intent를 단일 LLM 호출로 통합하여
토큰 비용과 레이턴시를 절감한다.

핵심 전략:
    - CONTINUE 판정 시 continue_context를 preprocessed_input에 반영하여
      하류 노드가 이전 대화 맥락을 자연스럽게 활용할 수 있게 한다.
    - UNSURE/AMBIGUOUS 판정 시 AmbiguitySignal을 생성하여
      clarification_handler로 라우팅한다.
    - 무한루프 방어: 명확화 횟수가 settings.clarification_max_turns를
      초과하면 DATA_EXTRACTION으로 강제 진행한다.

DATA_ANALYSIS 시각화/분석 지시어 제거(`extraction_query_rewriter`)는
본 노드가 아닌 `query_normalizer` 에서 수행한다. 이유: CONTINUE 턴에서는
본 노드가 `CONTINUE_ORCHESTRATION_PENDING` 을 설정하여 continue_orchestrator 로
라우팅하며, 그 시점에서 재작성 전 원본 질의가 오케스트레이터 입력이어야
맥락 해석이 정확하기 때문.

핵심 함수:
    - intent_classifier_node: 파이프라인 노드 진입점

명확화 Q&A 조립은 공용 유틸 `build_clarification_context` 사용.
"""

from __future__ import annotations

from src.agents.models.clarification import AmbiguitySignal
from src.agents.nodes.system_prompts import (
    INTENT_CLASSIFIER_SYSTEM,
    INTENT_CLASSIFIER_USER,
)
from src.agents.utils.clarification_context import (
    build_clarification_context,
)
from src.agents.state.state import (
    PipelineState,
    QueryStatus,
    add_trace,
)
from src.models.enums import HistoryDecision
from src.models.enums import IntentType
from src.services.intent_classifier import (
    IntentClassifyResult,
    intent_classifier,
)
from src.utils.logger import get_logger
from src.utils.tracker import (
    LLMInteraction,
    build_llm_reasoning_payload,
)
from src.utils.tracker.dispatch import (
    dispatch_tracking_event,
    REASONING_STEP,
)
from src.utils.truncate import truncate_log

logger = get_logger(__name__)


async def _emit_intent_classifier_reasoning_step(
    *,
    interaction: LLMInteraction,
    result: IntentClassifyResult,
    next_node: str,
    routing_reason: str,
) -> None:
    """intent_classifier 의 REASONING_STEP 이벤트를 표준 payload 로 방출한다.

    Option B (§trace-input-output-redesign) 권고에 따라 프롬프트 변수와
    원본 응답을 손실 없이 전달한다.
    """
    if result.is_error:
        parsed_summary: dict = {"error": True}
    else:
        parsed_summary = {
            "resolution": result.resolution.value,
            "intent": result.intent.value,
            "confidence": result.confidence,
            "category": result.category,
            "reason": result.reason or result.continue_reason or "",
            "needs_analyzer": result.needs_analyzer,
            "ambiguity_count": len(result.ambiguities or []),
        }
    await dispatch_tracking_event(
        REASONING_STEP,
        build_llm_reasoning_payload(
            node="intent_classifier",
            phase="interpret",
            round=0,
            hypothesis_id="",
            interaction=interaction,
            routing={
                "next_node": next_node,
                "reason": routing_reason,
            },
            parsed_summary=parsed_summary,
        ),
    )


async def _handle_forced_progression(
    state: PipelineState,
    result: IntentClassifyResult,
    interaction: LLMInteraction,
    ask_count: int,
) -> dict:
    """명확화 상한 초과 시 DATA_EXTRACTION 으로 강제 진행한다."""
    logger.warning(
        "명확화 상한 초과, 강제 진행",
        ask_count=ask_count,
    )
    forced_signal = AmbiguitySignal(
        source_node="intent_classifier",
        decision="INFER",
        ambiguity_type="INTENT",
        confidence="LOW",
        question=(
            "명확화 횟수 상한에 도달하여 "
            "데이터 추출로 자동 진행합니다"
        ),
        question_type="confirm",
        inferred_value="데이터 추출",
        reasoning=f"명확화 {ask_count}회 초과",
        turn_id=state.turn_id,
    )
    await _emit_intent_classifier_reasoning_step(
        interaction=interaction,
        result=result,
        next_node="query_normalizer",
        routing_reason=(
            f"명확화 {ask_count}회 초과 → DATA_EXTRACTION 강제 진행"
        ),
    )
    return {
        "intent": IntentType.DATA_EXTRACTION,
        "intent_confidence": 0.4,
        "query_category": "DATA_EXTRACTION",
        "is_continuation": False,
        "resolved_signals": [*state.resolved_signals, forced_signal],
        "status": QueryStatus.INTENT_CLASSIFIED,
        "trace_log": add_trace(
            state, "맥락분류",
            "명확화 상한 초과, 강제 진행",
            f"횟수={ask_count}",
        ),
    }


def _build_ambiguity_signal(
    result: IntentClassifyResult,
) -> tuple[AmbiguitySignal, str]:
    """LLM ambiguities (폴백 포함) 에서 ASK 시그널을 구성한다.

    Returns:
        (signal, label): label 은 "UNSURE" 또는 "AMBIGUOUS".
    """
    amb = result.ambiguities[0] if result.ambiguities else {}
    is_unsure = result.resolution == HistoryDecision.UNSURE
    fallback_question = (
        "이전 대화에 이어서 질문하신 건지, "
        "새로운 데이터를 찾으시는 건지 알려주시겠어요?"
        if is_unsure
        else f"요청하신 내용을 좀 더 구체적으로 "
             f"알려주시겠어요?\n{result.reason or ''}"
    )
    signal = AmbiguitySignal(
        source_node="intent_classifier",
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
        options=amb.get("options") or [],
        inferred_value=amb.get("inferred_value"),
        reasoning=amb.get("reasoning", result.reason or ""),
    )
    label = "UNSURE" if is_unsure else "AMBIGUOUS"
    return signal, label


async def _handle_ambiguous_or_unsure(
    state: PipelineState,
    result: IntentClassifyResult,
    interaction: LLMInteraction,
) -> dict:
    """UNSURE/AMBIGUOUS 판정 시 명확화 신호를 생성하거나 강제 진행한다."""
    from src.config import settings
    ask_count = sum(
        1 for s in state.resolved_signals
        if s.decision == "ASK"
    )
    if ask_count >= settings.clarification_max_turns:
        return await _handle_forced_progression(
            state, result, interaction, ask_count,
        )

    signal, label = _build_ambiguity_signal(result)
    await _emit_intent_classifier_reasoning_step(
        interaction=interaction,
        result=result,
        next_node="clarification_handler",
        routing_reason=f"{label} → 명확화 질문",
    )
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


def _apply_continue_updates(
    state: PipelineState,
    result: IntentClassifyResult,
    updates: dict,
) -> None:
    """CONTINUE 판정 시 updates 에 맥락 반영·시그널·라우팅 전환을 적용한다."""
    updates["continue_context"] = result.continue_context
    # 하류 노드가 continue_context를 직접 참조하도록 전환 전까지
    # preprocessed_input에 맥락 반영 질의를 설정하여 맥락 손실 방지
    if result.continue_context:
        updates["preprocessed_input"] = result.continue_context
    # 사용자에게 이전 대화 연속 해석 사실을 안내
    updates["resolved_signals"] = [
        *state.resolved_signals,
        AmbiguitySignal(
            source_node="intent_classifier",
            decision="INFER",
            ambiguity_type="CONTEXT",
            confidence="MEDIUM",
            question="이전 대화의 연속으로 해석하였습니다",
            question_type="confirm",
            inferred_value="기존 맥락 기반 재질의",
            reasoning=result.continue_reason or "",
            turn_id=state.turn_id,
        ),
    ]
    # Multi-Turn CONTINUE 오케스트레이터 라우팅 — 참조할 이전 턴 스냅샷이 있는 경우에만.
    # turn_snapshots가 비어있으면(첫 CONTINUE 턴) 스냅샷이 없으므로 일반 흐름 유지.
    # save_turn_snapshot의 I4 필터가 위 INFER 시그널을 걸러내므로 충돌 없음.
    if state.turn_snapshots:
        updates["status"] = QueryStatus.CONTINUE_ORCHESTRATION_PENDING


def _build_trace(
    state: PipelineState,
    result: IntentClassifyResult,
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


async def intent_classifier_node(
    state: PipelineState,
) -> dict:
    """대화 연속 여부를 판정하고 의도를 분류한다."""
    query = state.preprocessed_input
    history = state.conversation_history

    result, interaction = await intent_classifier(
        query,
        history,
        system_prompt=INTENT_CLASSIFIER_SYSTEM,
        user_template=INTENT_CLASSIFIER_USER,
        clarification_history=build_clarification_context(state),
    )

    # ── 에러 ──
    if result.is_error:
        await _emit_intent_classifier_reasoning_step(
            interaction=interaction,
            result=result,
            next_node="(end)",
            routing_reason="LLM 호출 실패 → 에러 반환",
        )
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
        return await _handle_ambiguous_or_unsure(
            state, result, interaction,
        )

    # ── 정상 경로: SKIP / NEW / CONTINUE ──
    updates: dict = {
        "intent": result.intent,
        "intent_confidence": result.confidence,
        "query_category": result.category,
        "is_continuation": result.resolution == HistoryDecision.CONTINUE,
        "needs_analyzer": result.needs_analyzer,
        "status": QueryStatus.INTENT_CLASSIFIED,
        "trace_log": _build_trace(state, result),
    }

    if result.resolution == HistoryDecision.CONTINUE:
        _apply_continue_updates(state, result, updates)

    # ── 추적 이벤트 ──
    from src.utils.tracker.dispatch import (
        DECISION_INTENT,
    )
    await dispatch_tracking_event(DECISION_INTENT, {
        "node": "intent_classifier",
        "decision_type": "intent_classification",
        "resolution": result.resolution.value,
        "chosen": result.intent.value,
        "confidence": result.confidence,
        "reason": (
            result.continue_reason
            if result.resolution == HistoryDecision.CONTINUE
            else result.reason
        ),
        "needs_analyzer": result.needs_analyzer,
        "needs_analyzer_reason": result.needs_analyzer_reason,
    })

    # ── Reasoning Flow 트레이스 ──
    # 라우팅 결정: 정상 경로에서는 데이터 의도 → query_normalizer, 비데이터 → simple_responder
    if result.intent in (IntentType.CASUAL_TALK, IntentType.META_QUESTION):
        _next_node = "simple_responder"
    elif updates.get("status") == QueryStatus.CONTINUE_ORCHESTRATION_PENDING:
        _next_node = "continue_orchestrator"
    else:
        _next_node = "query_normalizer"

    await _emit_intent_classifier_reasoning_step(
        interaction=interaction,
        result=result,
        next_node=_next_node,
        routing_reason=(
            f"{result.resolution.value} + {result.intent.value}"
        ),
    )

    return updates
