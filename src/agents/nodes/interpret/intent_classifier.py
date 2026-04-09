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
    - DATA_ANALYSIS 의도일 경우 시각화/분석 지시어를 제거한
      추출 전용 질의를 별도로 생성한다.

핵심 함수:
    - intent_classifier_node: 파이프라인 노드 진입점
    - _build_clarification_history: 이전 명확화 Q&A를 프롬프트용 텍스트로 조립
    - _rewrite_for_analysis: DATA_ANALYSIS 질의에서 시각화 지시어 제거
"""

from __future__ import annotations

from src.agents.models.clarification import AmbiguitySignal
from src.agents.nodes.system_prompts import (
    INTENT_CLASSIFIER_QUERY_REWRITER,
    INTENT_CLASSIFIER_SYSTEM,
    INTENT_CLASSIFIER_USER,
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
    rewrite_analysis_query,
)
from src.utils.logger import get_logger
from src.utils.tracker.dispatch import (
    dispatch_tracking_event,
    REASONING_STEP,
)
from src.utils.truncate import truncate_log

logger = get_logger(__name__)


def _build_clarification_history(state: PipelineState) -> str:
    """명확화 이력을 프롬프트용 텍스트로 조립한다.

    clarification_handler의 interrupt/resume 사이클 후
    resolved_signals에 저장된 질문-응답을 추출한다.
    """
    # resolved_signals에서 intent_classifier가 발생시킨 명확화 이력 추출
    parts: list[str] = []
    for signal in state.resolved_signals:
        if signal.source_node != "intent_classifier":
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


async def _rewrite_for_analysis(original_input: str) -> dict:
    """DATA_ANALYSIS 질의에서 시각화/분석 지시어를 제거한다.

    원본을 analysis_query에 보관하고, 추출 중심 질의로
    preprocessed_input을 교체한다. 실패 시 원본을 그대로 유지한다.
    """
    updates: dict = {"analysis_query": original_input}
    try:
        extraction = await rewrite_analysis_query(
            original_input,
            system_prompt=INTENT_CLASSIFIER_QUERY_REWRITER,
        )
        if extraction:
            updates["preprocessed_input"] = extraction
    except Exception as e:
        logger.warning(
            "분석 질의 재작성 실패, 원본 유지",
            error=str(e),
        )
    return updates


async def intent_classifier_node(
    state: PipelineState,
) -> dict:
    """대화 연속 여부를 판정하고 의도를 분류한다."""
    query = state.preprocessed_input
    history = state.conversation_history

    result = await intent_classifier(
        query,
        history,
        system_prompt=INTENT_CLASSIFIER_SYSTEM,
        user_template=INTENT_CLASSIFIER_USER,
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
            # 사용자에게 자동 진행 사실을 안내하기 위해 INFER 시그널 생성
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
            return {
                "intent": IntentType.DATA_EXTRACTION,
                "intent_confidence": 0.4,
                "query_category": "DATA_EXTRACTION",
                "is_continuation": False,
                "resolved_signals": [forced_signal],
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
        # 사용자에게 이전 대화 연속 해석 사실을 안내
        updates["resolved_signals"] = [AmbiguitySignal(
            source_node="intent_classifier",
            decision="INFER",
            ambiguity_type="CONTEXT",
            confidence="MEDIUM",
            question="이전 대화의 연속으로 해석하였습니다",
            question_type="confirm",
            inferred_value="기존 맥락 기반 재질의",
            reasoning=result.continue_reason or "",
            turn_id=state.turn_id,
        )]

    # ── DATA_ANALYSIS: 시각화/분석 지시어 제거 ──
    if result.intent == IntentType.DATA_ANALYSIS:
        current_input = updates.get(
            "preprocessed_input", query,
        )
        updates.update(
            await _rewrite_for_analysis(current_input),
        )

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
    })

    # ── Reasoning Flow 트레이스 ──
    # 라우팅 결정: 정상 경로에서는 데이터 의도 → normalize_query, 비데이터 → simple_responder
    if result.intent in (IntentType.CASUAL_TALK, IntentType.META_QUESTION):
        _next_node = "simple_responder"
    else:
        _next_node = "normalize_query"
    _routing_reason = (
        f"{result.resolution.value} + {result.intent.value}"
    )

    clarification_hist = _build_clarification_history(state)
    await dispatch_tracking_event(REASONING_STEP, {
        "node": "intent_classifier",
        "phase": "interpret",
        "step_type": "llm_decision",
        "round": 0,
        "hypothesis_id": "",
        "inputs": {
            "query": query,
            "history": (
                f"최근 {len(history)}턴"
                if history else "(없음)"
            ),
            "clarification_history": (
                f"{len(state.resolved_signals)}건"
                if clarification_hist else "(없음)"
            ),
        },
        "output": {
            "resolution": (
                f"{result.resolution.value}"
                f" ({result.confidence:.0%})"
            ),
            "resolution_reason": result.reason or result.continue_reason or "",
            "intent": result.intent.value,
            "confidence": result.confidence,
            "ambiguities": [
                a.get("question", "") for a in (result.ambiguities or [])
            ],
        },
        "routing": {
            "next_node": _next_node,
            "reason": _routing_reason,
        },
    })

    return updates
