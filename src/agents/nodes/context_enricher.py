"""컨텍스트 보강 노드 — LLM 기반 테이블 메타 설명 보강.

컨텍스트 수집 단계에서 가져온 테이블 메타데이터의 설명이 부실한 경우,
LLM 을 활용하여 보고서 SQL 및 과거 SQL 사용 패턴을 참조해 설명을 보강한다.
보강된 설명은 SQL 생성 시 테이블 선택 정확도를 높이는 데 기여하며,
enriched_description 플래그로 보강 여부를 추적할 수 있다.
전체 보강 작업에 타임아웃(llm_context_timeout)을 적용하여 지연을 방지한다.

핵심 함수:
    - enrich_context_node: state.context.table_metas 를 읽어 LLM 으로 설명을 보강하고
      state.context 를 갱신된 메타와 함께 다시 기록

위임 구조:
    - 비즈니스 로직: services/table_meta_enricher.py (enrich_table_descriptions)
    - 프롬프트: nodes/prompts/system_prompts.py 에서 TABLE_DESCRIPTION_ENRICHMENT,
      ENRICHMENT_SYSTEM, ENRICHMENT_FORMAT_HINT 를 로드하여 서비스에 주입

폴백:
    - asyncio.TimeoutError 발생 시 원본 메타를 그대로 사용하여 파이프라인을 계속 진행한다.
    - 테이블 메타가 비어 있으면 보강을 건너뛴다.
"""

from __future__ import annotations

import asyncio

from src.agents.nodes.prompts.system_prompts import (
    ENRICHMENT_FORMAT_HINT,
    ENRICHMENT_SYSTEM,
    TABLE_DESCRIPTION_ENRICHMENT,
)
from src.agents.state.state import (
    PipelineState,
    QueryStatus,
    add_trace,
)
from src.config import settings
from src.services.table_meta_enricher import (
    enrich_table_descriptions,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)


async def enrich_context_node(
    state: PipelineState,
) -> dict:
    """테이블 메타 설명을 LLM으로 보강한다."""
    context = state.context
    table_metas = list(context.table_metas)

    if not table_metas:
        return {
            "status": QueryStatus.CONTEXT_COLLECTED,
        }

    try:
        enriched = await asyncio.wait_for(
            enrich_table_descriptions(
                table_metas,
                report_sqls=context.report_sqls,
                past_sqls=context.past_sqls,
                prompt_template=TABLE_DESCRIPTION_ENRICHMENT,
                system_prompt=ENRICHMENT_SYSTEM,
                format_hint=ENRICHMENT_FORMAT_HINT,
            ),
            timeout=settings.llm_context_timeout,
        )
        context.table_metas = enriched
    except asyncio.TimeoutError:
        logger.warning(
            "테이블 설명 보강 전체 타임아웃, 원본 메타 사용",
            timeout=settings.llm_context_timeout,
        )

    enriched_count = sum(
        1
        for t in context.table_metas
        if t.enriched_description
    )

    detail = (
        f"보강 {enriched_count}건"
        if enriched_count
        else "보강 대상 없음"
    )

    return {
        "context": context,
        "status": QueryStatus.CONTEXT_COLLECTED,
        "trace_log": add_trace(
            state, "컨텍스트보강",
            "테이블 설명 보강 완료",
            detail,
        ),
    }
