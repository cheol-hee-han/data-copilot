"""자연어 질의 정규화 노드 — 8-Slot NormalizedQuery 구조화.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

사용자의 자연어 질의를 LLM 2-Phase 호출을 통해 8-Slot 구조(intent, entities,
measures, filters, time_range, group_by, output_hint, ambiguities)로 정규화한다.
Phase1 에서 슬롯을 추출하고, Phase2 에서 검증·보완하여 NormalizedQuery 모델을 생성한다.
정규화된 결과는 이후 컨텍스트 수집과 SQL 생성에서 핵심 입력으로 사용된다.

DATA_ANALYSIS 의도 질의는 정규화 수행 전에 `extraction_query_rewriter` 로
시각화/분석 지시어를 제거한 추출 중심 질의로 재작성한다. 원본 질의는
`analysis_query` 에 보관되어 analyzer 에서 해석 텍스트 생성에 사용된다.
(이 재작성을 intent_classifier 가 아닌 query_normalizer 에서 수행하는 이유:
intent_classifier 는 CONTINUE 판정 시 `CONTINUE_ORCHESTRATION_PENDING` 으로
continue_orchestrator 에 라우팅되는데, 그 시점에서는 재작성 전 원본 질의가
오케스트레이터 입력이어야 맥락 해석이 정확하기 때문.)

핵심 함수:
    - query_normalizer_node: state.preprocessed_input 을 읽어 정규화하고
      state.normalized_query 에 NormalizedQuery 인스턴스를 기록

위임 구조:
    - 비즈니스 로직: services/query_normalizer.py
      (run_normalization + extraction_query_rewriter)
    - 프롬프트: nodes/prompts/system_prompts.py 에서
      QUERY_NORMALIZER_PHASE1/PHASE2, EXTRACTION_QUERY_REWRITER 를 로드하여 서비스에 주입

폴백:
    - 재작성 실패 시 원본 질의로 정규화를 계속 진행한다.
    - 정규화 실패(예외 발생) 시 original_query 만 담은 기본 NormalizedQuery 를 반환하여
      파이프라인이 중단 없이 진행되도록 한다.
"""

from __future__ import annotations

from src.agents.models.clarification import AmbiguitySignal
from src.agents.models.normalization import NormalizedQuery
from src.agents.nodes.system_prompts import (
    EXTRACTION_QUERY_REWRITER,
    QUERY_NORMALIZER_PHASE1_SYSTEM,
    QUERY_NORMALIZER_PHASE1_USER,
    QUERY_NORMALIZER_PHASE2_SYSTEM,
    QUERY_NORMALIZER_PHASE2_USER,
)
from src.agents.state.state import (
    PipelineState,
    QueryStatus,
    add_trace,
)
from src.agents.utils.handoff import normalize_handoff_note
from src.models.enums import IntentType
from src.services.query_normalizer import (
    extraction_query_rewriter,
    run_normalization,
)
from src.utils.logger import get_logger
from src.utils.tracker import (
    LLMInteraction,
    build_llm_reasoning_payload,
)
from src.utils.tracker.dispatch import (
    DECISION_NORMALIZATION,
    REASONING_STEP,
    dispatch_tracking_event,
)
from src.utils.truncate import truncate_log

logger = get_logger(__name__)


async def _emit_normalization_reasoning_steps(
    *,
    interactions: list[LLMInteraction],
    raw_query: str,
    clarification_ctx: str,
    final_slot_summary: dict,
    ambiguity_count: int,
) -> None:
    """Phase1/Phase2 각 LLM 호출마다 REASONING_STEP 이벤트를 방출한다.

    프롬프트 [INPUT] 치환 변수와 [OUTPUT_CONTRACT] 원본 응답을 손실 없이 보존
    (20260422 trace-input-output-redesign §2 권고안 B).
    """
    last_idx = len(interactions) - 1
    infer_suffix = " INFER 처리" if ambiguity_count > 0 else ""
    final_reason = (
        f"8-Slot 완료, 모호성 {ambiguity_count}건{infer_suffix}"
    )
    for idx, interaction in enumerate(interactions):
        is_last = idx == last_idx
        if is_last:
            next_node = "reasoning_preparer"
            routing_reason = final_reason
            parsed_summary = final_slot_summary
        else:
            next_node = f"query_normalizer:phase{idx + 2}"
            routing_reason = (
                f"Phase{idx + 1} 완료 → Phase{idx + 2} 교차검증"
            )
            parsed_summary = {"stage": f"phase{idx + 1}_raw"}
        await dispatch_tracking_event(
            REASONING_STEP,
            build_llm_reasoning_payload(
                node=f"query_normalizer:phase{idx + 1}",
                phase="interpret",
                round=0,
                hypothesis_id="",
                interaction=interaction,
                routing={
                    "next_node": next_node,
                    "reason": routing_reason,
                },
                parsed_summary=parsed_summary,
                extra_inputs={
                    "raw_query": raw_query,
                    "clarification_context": clarification_ctx or "(없음)",
                },
            ),
        )


async def _rewrite_for_analysis(original_input: str) -> dict:
    """DATA_ANALYSIS 질의에서 시각화/분석 지시어를 제거한다.

    원본을 analysis_query 에 보관하고, 추출 중심 질의로
    preprocessed_input 을 교체한다. 실패 시 원본을 그대로 유지한다.
    """
    updates: dict = {"analysis_query": original_input}
    try:
        extraction = await extraction_query_rewriter(
            original_input,
            system_prompt=EXTRACTION_QUERY_REWRITER,
        )
        if extraction:
            updates["preprocessed_input"] = extraction
    except Exception as e:
        logger.warning(
            "추출 질의 재작성 실패, 원본 유지",
            error=str(e),
        )
    return updates


async def query_normalizer_node(
    state: PipelineState,
) -> dict:
    """사용자 질의를 8-Slot NormalizedQuery로 정규화한다."""
    from src.agents.utils.clarification_context import (
        build_clarification_context,
    )

    raw_query = state.preprocessed_input

    # ── DATA_ANALYSIS: 시각화/분석 지시어 제거 (정규화 전 전처리) ──
    # 재작성된 질의로 8-Slot 정규화를 수행하고, 원본은 analyzer 가 사용한다.
    rewriter_updates: dict = {}
    if state.intent == IntentType.DATA_ANALYSIS:
        rewriter_updates = await _rewrite_for_analysis(raw_query)
        raw_query = rewriter_updates.get("preprocessed_input", raw_query)

    # 전략 §2.4: 명확화 Q&A를 구조화된 섹션으로 LLM에 전달
    clarification_ctx = build_clarification_context(state)
    query_for_llm = (
        f"{raw_query}\n\n{clarification_ctx}"
        if clarification_ctx
        else raw_query
    )

    logger.info(
        "질의 정규화 시작",
        input=truncate_log(raw_query),
    )

    try:
        normalized, interactions = await run_normalization(
            query_for_llm,
            phase1_system=QUERY_NORMALIZER_PHASE1_SYSTEM,
            phase1_user_template=QUERY_NORMALIZER_PHASE1_USER,
            phase2_system=QUERY_NORMALIZER_PHASE2_SYSTEM,
            phase2_user_template=QUERY_NORMALIZER_PHASE2_USER,
            handoff_note=normalize_handoff_note(state.handoff_note),
        )
    except Exception as e:
        logger.error("질의 정규화 실패", error=str(e))
        return {
            **rewriter_updates,
            "normalized_query": NormalizedQuery(
                original_query=raw_query,
            ),
            "status": QueryStatus.QUERY_NORMALIZED,
            "trace_log": add_trace(
                state, "질의정규화",
                "정규화 실패 — 기본값으로 진행",
                truncate_log(str(e)),
            ),
        }

    intent_primary = normalized.intent.primary
    entity_terms = [e.term for e in normalized.entities]
    measure_terms = [m.term for m in normalized.measures]
    ambiguity_count = len(normalized.ambiguities)

    filter_count = len(normalized.filters)
    time_type = normalized.time.type

    logger.info(
        "질의 정규화 완료",
        intent=intent_primary,
        entities=entity_terms,
        measures=measure_terms,
        filters=filter_count,
        time_type=time_type if time_type != "NONE" else "(없음)",
        ambiguities=ambiguity_count,
    )

    # ── 추적: 정규화 슬롯 요약 ──
    await dispatch_tracking_event(DECISION_NORMALIZATION, {
        "node": "query_normalizer",
        "decision_type": "normalization",
        "chosen": intent_primary,
        "reason": (
            f"entities={entity_terms}, "
            f"measures={measure_terms}, "
            f"filters={filter_count}, "
            f"ambiguities={ambiguity_count}"
        ),
    })

    trace_detail = (
        f"유형={intent_primary}, "
        f"엔티티={entity_terms}, "
        f"측정값={measure_terms}"
    )
    if normalized.output_hint.format != "NONE":
        trace_detail += (
            f", 출력형식={normalized.output_hint.format}"
        )
    if ambiguity_count > 0:
        trace_detail += f", 모호성 {ambiguity_count}건"

    result: dict = {
        **rewriter_updates,
        "normalized_query": normalized,
        "status": QueryStatus.QUERY_NORMALIZED,
        "trace_log": add_trace(
            state, "질의정규화",
            "8-Slot 정규화 완료",
            trace_detail,
        ),
    }

    # ── T3: ambiguities → AmbiguitySignal 생성 ──
    # LLM이 decision(ASK/INFER)을 직접 판단한다.
    # ASK 시그널은 pending_signals → clarification_handler에서 사용자에게 질문.
    # INFER 시그널은 resolved_signals → Reason 계층 검증 포인트로 활용.
    # (향후 정책 변경 시 이 분기를 수정하면 됨)
    if ambiguity_count > 0:
        signals = [
            AmbiguitySignal(
                source_node="query_normalizer",
                decision=amb.get("decision", "INFER"),
                ambiguity_type=amb.get("ambiguity_type", "CONTEXT"),
                confidence=amb.get("confidence", "LOW"),
                question=amb.get("question", ""),
                question_type=amb.get("question_type", "single_select"),
                options=amb.get("options") or [],
                inferred_value=amb.get("inferred_value"),
                reasoning=amb.get("reasoning", ""),
                turn_id=state.turn_id,
            )
            for amb in normalized.ambiguities
        ]
        ask_signals = [s for s in signals if s.decision == "ASK"]
        infer_signals = [s for s in signals if s.decision != "ASK"]
        if ask_signals:
            result["pending_signals"] = ask_signals
        if infer_signals:
            result["resolved_signals"] = [
                *state.resolved_signals,
                *infer_signals,
            ]
        logger.info(
            "T3 AmbiguitySignal 생성",
            ask=len(ask_signals),
            infer=len(infer_signals),
        )

    # ── Reasoning Flow 트레이스 ──
    final_slot_summary = {
        "intent": intent_primary,
        "entities": entity_terms,
        "measures": measure_terms,
        "time": time_type,
        "filter_count": filter_count,
        "output_hint": (
            normalized.output_hint.format
            if normalized.output_hint else "NONE"
        ),
        "ambiguity_count": ambiguity_count,
    }
    await _emit_normalization_reasoning_steps(
        interactions=interactions,
        raw_query=raw_query,
        clarification_ctx=clarification_ctx,
        final_slot_summary=final_slot_summary,
        ambiguity_count=ambiguity_count,
    )

    return result
