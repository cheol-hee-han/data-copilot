"""자연어 질의 정규화 노드 — 8-Slot NormalizedQuery 구조화.

사용자의 자연어 질의를 LLM 2-Phase 호출을 통해 8-Slot 구조(intent, entities,
measures, filters, time_range, group_by, output_hint, ambiguities)로 정규화한다.
Phase1 에서 슬롯을 추출하고, Phase2 에서 검증·보완하여 NormalizedQuery 모델을 생성한다.
정규화된 결과는 이후 컨텍스트 수집과 SQL 생성에서 핵심 입력으로 사용된다.

핵심 함수:
    - normalize_query_node: state.preprocessed_input 을 읽어 정규화하고
      state.normalized_query 에 NormalizedQuery 인스턴스를 기록

위임 구조:
    - 비즈니스 로직: services/query_normalizer.py (run_normalization)
    - 프롬프트: nodes/prompts/system_prompts.py 에서
      QUERY_NORMALIZER_PHASE1/PHASE2 프롬프트를 로드하여 서비스에 주입

폴백:
    - 정규화 실패(예외 발생) 시 original_query 만 담은 기본 NormalizedQuery 를 반환하여
      파이프라인이 중단 없이 진행되도록 한다.
"""

from __future__ import annotations

from src.agents.models.clarification import AmbiguitySignal
from src.agents.models.normalization import NormalizedQuery
from src.agents.nodes.system_prompts import (
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
from src.services.query_normalizer import run_normalization
from src.utils.logger import get_logger
from src.utils.truncate import truncate_log

logger = get_logger(__name__)


async def normalize_query_node(
    state: PipelineState,
) -> dict:
    """사용자 질의를 8-Slot NormalizedQuery로 정규화한다."""
    from src.agents.utils.clarification_context import (
        build_clarification_context,
    )

    raw_query = state.preprocessed_input

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
        normalized = await run_normalization(
            query_for_llm,
            phase1_system=QUERY_NORMALIZER_PHASE1_SYSTEM,
            phase1_user_template=QUERY_NORMALIZER_PHASE1_USER,
            phase2_system=QUERY_NORMALIZER_PHASE2_SYSTEM,
            phase2_user_template=QUERY_NORMALIZER_PHASE2_USER,
        )
    except Exception as e:
        logger.error("질의 정규화 실패", error=str(e))
        return {
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
    from src.utils.tracker.dispatch import (
        dispatch_tracking_event,
        DECISION_NORMALIZATION,
    )
    await dispatch_tracking_event(DECISION_NORMALIZATION, {
        "node": "normalize_query",
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
        "normalized_query": normalized,
        "status": QueryStatus.QUERY_NORMALIZED,
        "trace_log": add_trace(
            state, "질의정규화",
            "8-Slot 정규화 완료",
            trace_detail,
        ),
    }

    # ── T3: ambiguities → AmbiguitySignal 생성 (INFER) ──
    # 전략 §2.3 T3: 정규화 시점에는 메타/SQL이력이 없으므로
    # 항상 INFER로 처리하여 resolved_signals에 기록.
    # Reason 계층에서 탐색 우선순위·검증 포인트로 활용된다.
    if ambiguity_count > 0:
        signals = [
            AmbiguitySignal(
                source_node="normalize_query",
                decision="INFER",
                ambiguity_type=amb.get("ambiguity_type", "CONTEXT"),
                confidence=amb.get("confidence", "LOW"),
                question=amb.get("question", ""),
                question_type=amb.get("question_type", "single_select"),
                options=amb.get("options", []),
                inferred_value=amb.get("inferred_value"),
                reasoning=amb.get("reasoning", ""),
                turn_id=state.turn_id,
            )
            for amb in normalized.ambiguities
        ]
        result["resolved_signals"] = signals
        logger.info(
            "T3 AmbiguitySignal 생성 (INFER)",
            count=ambiguity_count,
        )

    return result
