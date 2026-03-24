"""컨텍스트 수집 노드 — 다중 소스에서 SQL 생성용 참조 정보를 병렬 수집.

SQL 생성에 필요한 참조 정보를 ES 테이블 메타, 과거 SQL 이력(DB/벡터),
보고서 SQL, 업무 매뉴얼(Qdrant) 등 다중 소스에서 병렬로 수집한다.
커넥터 매니저를 통해 실제/Dummy 데이터 소스를 투명하게 전환할 수 있으며,
정규화된 질의(normalized_query)가 있으면 이를 활용해 검색 정밀도를 높인다.
수집 결과는 ContextInfo 모델로 통합되어 state.context 에 기록된다.

핵심 함수:
    - collect_context_node: state.preprocessed_input, state.normalized_query 를 읽어
      참조 정보를 수집하고 state.context (ContextInfo) 에 기록

위임 구조:
    - 비즈니스 로직: services/search_context_assembler.py (collect_context)
    - 커넥터 계측: utils/tracker.py 의 EvaluationTracker 를 주입하여
      소스별 성능·실패 현황을 추적
"""

from __future__ import annotations

from src.services.search_context_assembler import collect_context
from src.agents.state.state import PipelineState, QueryStatus, add_trace
from src.utils.logger import get_logger
from src.utils.tracker import get_current_tracker

logger = get_logger(__name__)


async def collect_context_node(state: PipelineState) -> dict:
    """관련 컨텍스트 정보를 커넥터를 통해 수집한다."""
    logger.info("컨텍스트 수집 시작", input=state.preprocessed_input)

    context = await collect_context(
        state.preprocessed_input,
        tracker=get_current_tracker(),
        normalized_query=state.normalized_query,
    )

    logger.info(
        "컨텍스트 수집 완료",
        tables=len(context.table_metas),
        past_sqls=len(context.past_sqls),
        vector_past_sqls=len(context.vector_past_sqls),
        report_sqls=len(context.report_sqls),
        manuals=len(context.manual_references),
    )

    # trace: 수집한 테이블 목록 및 보강 여부 기록
    table_names = [t.table_name for t in context.table_metas]
    enriched_count = sum(
        1 for t in context.table_metas if t.enriched_description
    )
    detail_parts = [
        f"테이블 {len(table_names)}건"
        f"({', '.join(table_names)})",
    ]
    if enriched_count:
        detail_parts.append(f"설명 보강 {enriched_count}건")
    if context.past_sqls:
        detail_parts.append(
            f"과거SQL {len(context.past_sqls)}건",
        )
    if context.vector_past_sqls:
        detail_parts.append(
            f"벡터SQL {len(context.vector_past_sqls)}건",
        )
    if context.report_sqls:
        detail_parts.append(
            f"보고서SQL {len(context.report_sqls)}건",
        )
    if context.manual_references:
        detail_parts.append(
            f"업무매뉴얼 {len(context.manual_references)}건",
        )

    return {
        "context": context,
        "status": QueryStatus.CONTEXT_COLLECTED,
        "trace_log": add_trace(
            state, "컨텍스트수집",
            "참조 정보 수집 완료",
            ", ".join(detail_parts),
        ),
    }
