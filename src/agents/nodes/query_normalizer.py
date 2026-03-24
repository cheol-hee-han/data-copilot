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
    - 프롬프트: nodes/prompts/system_prompts.py 에서 NORMALIZATION_PHASE1/PHASE2 프롬프트를
      로드하여 서비스에 주입

폴백:
    - 정규화 실패(예외 발생) 시 original_query 만 담은 기본 NormalizedQuery 를 반환하여
      파이프라인이 중단 없이 진행되도록 한다.
"""

from __future__ import annotations

from src.agents.models.normalization import NormalizedQuery
from src.agents.nodes.prompts.system_prompts import (
    NORMALIZATION_PHASE1,
    NORMALIZATION_PHASE1_USER,
    NORMALIZATION_PHASE2,
    NORMALIZATION_PHASE2_USER,
)
from src.agents.state.state import (
    PipelineState,
    QueryStatus,
    add_trace,
)
from src.services.query_normalizer import run_normalization
from src.utils.logger import get_logger

logger = get_logger(__name__)


async def normalize_query_node(
    state: PipelineState,
) -> dict:
    """사용자 질의를 8-Slot NormalizedQuery로 정규화한다."""
    raw_query = state.preprocessed_input
    logger.info("질의 정규화 시작", input=raw_query[:80])

    try:
        normalized = await run_normalization(
            raw_query,
            phase1_system=NORMALIZATION_PHASE1,
            phase1_user_template=NORMALIZATION_PHASE1_USER,
            phase2_system=NORMALIZATION_PHASE2,
            phase2_user_template=NORMALIZATION_PHASE2_USER,
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
                str(e)[:200],
            ),
        }

    intent_primary = normalized.intent.primary
    entity_terms = [e.term for e in normalized.entities]
    measure_terms = [m.term for m in normalized.measures]
    ambiguity_count = len(normalized.ambiguities)

    logger.info(
        "질의 정규화 완료",
        intent=intent_primary,
        entities=entity_terms,
        measures=measure_terms,
        ambiguities=ambiguity_count,
    )

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

    return {
        "normalized_query": normalized,
        "status": QueryStatus.QUERY_NORMALIZED,
        "trace_log": add_trace(
            state, "질의정규화",
            "8-Slot 정규화 완료",
            trace_detail,
        ),
    }
