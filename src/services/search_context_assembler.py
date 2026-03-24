"""컨텍스트 수집 서비스.

ES, Qdrant, 이력 DB에서 관련 컨텍스트를 병렬로 수집하여 통합한다.

병렬 처리 전략:
    asyncio.gather 로 4개 소스를 동시에 호출한다.
    - ES 테이블 메타 검색
    - ES 보고서 SQL 검색
    - 이력 DB 과거 SQL 검색
    - Qdrant 업무 매뉴얼 검색

    각 소스 호출은 독립적이며 결과가 서로 의존하지 않으므로 완전 병렬이 가능하다.
    개별 소스 실패 시 해당 소스의 결과는 빈 값으로 폴백하고 나머지는 정상 반환한다.

    코드 메타(5번째 소스)는 전체 로드 특성상 캐시 의존도가 높아
    테이블 메타와 같은 gather 그룹에 포함한다.
"""

from __future__ import annotations

import asyncio
import time

from src.connectors.manager import get_connector_manager
from src.services.search_query_builder import (
    build_source_queries_with_normalization,
)
from src.services.similar_table_resolver import (
    build_table_disambiguation_prompt,
    find_relevant_groups,
)
from src.models.context import ColumnMeta, ContextInfo, TableMeta
from src.utils.tracker import EvaluationTracker
from src.utils.logger import get_logger

logger = get_logger(__name__)


async def _fetch_table_metas(
    query: str,
    tracker: EvaluationTracker | None = None,
    failed_sources: list[str] | None = None,
) -> list[TableMeta]:
    """ES 에서 테이블/컬럼 메타를 검색한다.

    실패 시 빈 목록을 반환하여 다른 소스 수집에 영향을 주지 않는다.
    """
    start = time.perf_counter()
    try:
        manager = get_connector_manager()
        results = await manager.es.search_table_meta(query)
        elapsed = (time.perf_counter() - start) * 1000

        table_metas: list[TableMeta] = []
        for t in results:
            columns = [
                ColumnMeta(
                    column_name=c["name"],
                    column_description=c.get("desc", ""),
                    data_type=c.get("type", ""),
                    is_pii=c.get("pii", False),
                )
                for c in t.get("columns", [])
            ]
            table_metas.append(
                TableMeta(
                    table_name=t["table_name"],
                    table_description=t.get("table_description", ""),
                    columns=columns,
                    update_cycle=t.get("update_cycle", ""),
                )
            )

        results_detail = [
            f"{t.table_name} ({t.table_description}, 컬럼 {len(t.columns)}개)"
            for t in table_metas
        ]
        logger.info(
            "ES 테이블 메타 검색 완료",
            query=query,
            results_count=len(table_metas),
            results=results_detail,
            latency_ms=round(elapsed, 1),
        )
        if tracker and tracker.enabled:
            tracker.track_context_retrieval(
                source="es_table_meta",
                query=query,
                results_count=len(table_metas),
                results_summary=results_detail,
                latency_ms=elapsed,
            )
        return table_metas
    except Exception as e:
        logger.warning("테이블 메타 검색 실패, 빈 목록으로 폴백", error=str(e))
        if failed_sources is not None:
            failed_sources.append("es_table_meta")
        return []


async def _fetch_report_sqls(
    query: str,
    tracker: EvaluationTracker | None = None,
    failed_sources: list[str] | None = None,
) -> list[str]:
    """ES 에서 유사 보고서 SQL 을 검색한다.

    실패 시 빈 목록을 반환한다.
    """
    start = time.perf_counter()
    try:
        manager = get_connector_manager()
        results = await manager.es.search_report_sql(query)
        sqls = [r.get("sql", "") for r in results if r.get("sql")]
        elapsed = (time.perf_counter() - start) * 1000

        results_detail = [
            f"{r.get('report_name', '?')}: {r.get('sql', '')[:100]}"
            for r in results if r.get("sql")
        ]
        logger.info(
            "ES 보고서 SQL 검색 완료",
            query=query,
            results_count=len(sqls),
            results=results_detail,
            latency_ms=round(elapsed, 1),
        )
        if tracker and tracker.enabled:
            tracker.track_context_retrieval(
                source="es_report_sql",
                query=query,
                results_count=len(sqls),
                results_summary=results_detail,
                latency_ms=elapsed,
            )
        return sqls
    except Exception as e:
        logger.warning("보고서 SQL 검색 실패, 빈 목록으로 폴백", error=str(e))
        if failed_sources is not None:
            failed_sources.append("es_report_sql")
        return []


async def _fetch_past_sqls(
    query: str,
    tracker: EvaluationTracker | None = None,
    failed_sources: list[str] | None = None,
) -> list[str]:
    """이력 DB 에서 과거 유사 SQL 을 검색한다.

    실패 시 빈 목록을 반환한다.
    """
    start = time.perf_counter()
    try:
        manager = get_connector_manager()
        results = await manager.history_db.search_similar_sql(query)
        sqls = [h.get("sql", "") for h in results if h.get("sql")]
        elapsed = (time.perf_counter() - start) * 1000

        results_detail = [
            f"{h.get('query_text', '?')}: {h.get('sql', '')[:100]}"
            for h in results if h.get("sql")
        ]
        logger.info(
            "이력DB 과거 SQL 검색 완료",
            query=query,
            results_count=len(sqls),
            results=results_detail,
            latency_ms=round(elapsed, 1),
        )
        if tracker and tracker.enabled:
            tracker.track_context_retrieval(
                source="history_db_sql",
                query=query,
                results_count=len(sqls),
                results_summary=results_detail,
                latency_ms=elapsed,
            )
        return sqls
    except Exception as e:
        logger.warning("과거 SQL 검색 실패, 빈 목록으로 폴백", error=str(e))
        if failed_sources is not None:
            failed_sources.append("history_db_sql")
        return []


async def _fetch_manual_refs(
    query: str,
    tracker: EvaluationTracker | None = None,
    failed_sources: list[str] | None = None,
) -> list[str]:
    """Qdrant 에서 업무 매뉴얼 관련 내용을 검색한다.

    실패 시 빈 목록을 반환한다.
    """
    start = time.perf_counter()
    try:
        manager = get_connector_manager()
        results = await manager.qdrant.search_manual(query)
        refs = [
            f"[{m.get('title', '')}] {m.get('content', '')}"
            for m in results
        ]
        elapsed = (time.perf_counter() - start) * 1000

        results_detail = [
            f"{m.get('title', '?')} ({m.get('category', '')}): "
            f"{m.get('content', '')[:80]}"
            for m in results
        ]
        logger.info(
            "Qdrant 업무 매뉴얼 검색 완료",
            query=query,
            results_count=len(refs),
            results=results_detail,
            latency_ms=round(elapsed, 1),
        )
        if tracker and tracker.enabled:
            tracker.track_context_retrieval(
                source="qdrant_manual",
                query=query,
                results_count=len(refs),
                results_summary=results_detail,
                latency_ms=elapsed,
            )
        return refs
    except Exception as e:
        logger.warning("업무 매뉴얼 검색 실패, 빈 목록으로 폴백", error=str(e))
        if failed_sources is not None:
            failed_sources.append("qdrant_manual")
        return []


async def _fetch_sql_history_vectors(
    query: str,
    tracker: EvaluationTracker | None = None,
    failed_sources: list[str] | None = None,
) -> list[str]:
    """Qdrant sql_history 에서 유사 SQL 을 벡터 검색한다.

    Dense+Sparse 하이브리드 검색 → RRF 후보 →
    BGE-Reranker 재순위 → 최종 Top-K SQL 반환.
    실패 시 빈 목록을 반환한다.
    """
    start = time.perf_counter()
    try:
        manager = get_connector_manager()
        raw_results = await manager.qdrant.search_sql_history(
            query,
        )
        elapsed_search = (time.perf_counter() - start) * 1000

        if not raw_results:
            logger.info(
                "sql_history 벡터 검색 결과 없음",
                query=query[:60],
            )
            return []

        # Reranker 적용
        from src.services.reranker import (
            RerankCandidate,
            get_reranker,
        )

        candidates = [
            RerankCandidate(
                text=r.get("description", ""),
                payload=r,
                score=r.get("_score", 0.0),
            )
            for r in raw_results
        ]

        reranked = get_reranker().rerank(
            query=query,
            candidates=candidates,
        )

        sqls = [
            c.payload.get("sql", "")
            for c in reranked
            if c.payload.get("sql")
        ]

        elapsed_total = (time.perf_counter() - start) * 1000

        results_detail = [
            f"{c.payload.get('description', '?')[:60]} "
            f"(rerank={c.rerank_score:.3f})"
            for c in reranked[:5]
        ]
        logger.info(
            "sql_history 벡터 검색+재순위 완료",
            query=query[:60],
            search_ms=round(elapsed_search, 1),
            total_ms=round(elapsed_total, 1),
            raw_count=len(raw_results),
            reranked_count=len(sqls),
            top_results=results_detail,
        )

        if tracker and tracker.enabled:
            tracker.track_context_retrieval(
                source="qdrant_sql_history",
                query=query,
                results_count=len(sqls),
                results_summary=results_detail,
                latency_ms=elapsed_total,
            )

        return sqls
    except Exception as e:
        logger.warning(
            "sql_history 벡터 검색 실패, 빈 목록으로 폴백",
            error=str(e),
        )
        if failed_sources is not None:
            failed_sources.append("qdrant_sql_history")
        return []


async def _fetch_code_meta(
    tracker: EvaluationTracker | None = None,
    failed_sources: list[str] | None = None,
) -> dict[str, str]:
    """ES 에서 코드 메타를 전체 로드하여 도메인 용어 사전을 구성한다.

    실패 시 기본 도메인 용어만 반환한다.
    """
    # 도메인 핵심 용어는 코드 메타 조회 실패와 무관하게 항상 포함
    domain_terms: dict[str, str] = {
        "신규 고객": "REG_DT가 해당 기간 내인 고객",
        "연체": "OVERDUE_YN = 'Y'",
        "여신": "대출 (TB_LOAN_INFO)",
        "수신": "예금 (TB_DEPOSIT_INFO)",
    }
    start = time.perf_counter()
    try:
        manager = get_connector_manager()
        code_results = await manager.es.search_code_meta("")
        for code_item in code_results:
            field = code_item.get("code_field", "")
            codes = code_item.get("codes", {})
            for code_val, code_desc in codes.items():
                domain_terms[code_desc] = f"{field} = '{code_val}'"
        elapsed = (time.perf_counter() - start) * 1000

        results_detail = [
            f"{c.get('code_field', '?')}: {len(c.get('codes', {}))}개 코드값"
            for c in code_results
        ]
        logger.info(
            "ES 코드 메타 로드 완료",
            results_count=len(code_results),
            results=results_detail,
            latency_ms=round(elapsed, 1),
        )
        if tracker and tracker.enabled:
            tracker.track_context_retrieval(
                source="es_code_meta",
                query="(전체 코드 메타 로드)",
                results_count=len(code_results),
                results_summary=results_detail,
                latency_ms=elapsed,
            )
    except Exception as e:
        logger.warning("코드 메타 로드 실패, 기본 도메인 용어만 사용", error=str(e))
        if failed_sources is not None:
            failed_sources.append("es_code_meta")
    return domain_terms


async def collect_context(
    query: str,
    tracker: EvaluationTracker | None = None,
    normalized_query: object | None = None,
) -> ContextInfo:
    """사용자 질의에 대한 컨텍스트를 병렬로 수집한다.

    검색 쿼리 전략 빌더를 통해 소스별 최적화된 쿼리를 생성한 뒤,
    4개 소스(ES 테이블 메타, ES 보고서 SQL, 이력 DB, Qdrant)와
    코드 메타를 asyncio.gather 로 동시에 호출한다.
    각 소스는 독립적으로 실패를 처리하므로 한 소스의 오류가 전체를 막지 않는다.

    Args:
        tracker: 평가 트래커. 제공 시 각 소스별 쿼리/결과를 기록한다.
    """
    logger.info("컨텍스트 병렬 수집 시작", query=query[:80])

    # 도메인 지식 기반 소스별 최적화 쿼리 생성
    # NormalizedQuery 가 있으면 search_keywords 로 검색 보강
    source_queries = build_source_queries_with_normalization(
        query, normalized_query,
    )

    sql_hist_query = source_queries.sql_history_query

    logger.info(
        "검색 쿼리 전략 적용",
        es_table=source_queries.es_table_query[:60],
        es_report=source_queries.es_report_query[:60],
        history=source_queries.history_db_query[:60],
        qdrant=source_queries.qdrant_query[:60],
        sql_history=sql_hist_query[:60],
        matched_terms=len(source_queries.matched_terms),
        categories=source_queries.categories,
    )

    if tracker and tracker.enabled:
        tracker.track_context_retrieval(
            source="search_query_builder",
            query=query,
            results_count=len(source_queries.matched_terms),
            results_summary=[
                f"es_table: {source_queries.es_table_query[:80]}",
                f"es_report: {source_queries.es_report_query[:80]}",
                f"history: {source_queries.history_db_query[:80]}",
                f"qdrant: {source_queries.qdrant_query[:80]}",
                f"sql_history: {sql_hist_query[:80]}",
                f"tables: {source_queries.extracted_tables}",
                f"categories: {source_queries.categories}",
                f"core_keywords: {source_queries.core_keywords}",
            ],
            latency_ms=0.0,
        )

    failed_sources: list[str] = []

    (
        table_metas,
        report_sqls,
        past_sqls,
        manual_refs,
        vector_past_sqls,
        domain_terms,
    ) = await asyncio.gather(
        _fetch_table_metas(
            source_queries.es_table_query,
            tracker=tracker,
            failed_sources=failed_sources,
        ),
        _fetch_report_sqls(
            source_queries.es_report_query,
            tracker=tracker,
            failed_sources=failed_sources,
        ),
        _fetch_past_sqls(
            source_queries.history_db_query,
            tracker=tracker,
            failed_sources=failed_sources,
        ),
        _fetch_manual_refs(
            source_queries.qdrant_query,
            tracker=tracker,
            failed_sources=failed_sources,
        ),
        _fetch_sql_history_vectors(
            sql_hist_query,
            tracker=tracker,
            failed_sources=failed_sources,
        ),
        _fetch_code_meta(
            tracker=tracker,
            failed_sources=failed_sources,
        ),
    )

    if failed_sources:
        logger.warning(
            "컨텍스트 소스 일부 실패",
            failed_sources=failed_sources,
        )

    # 유사 테이블 그룹 감지 및 구분 가이드 생성
    table_names = [t.table_name for t in table_metas]
    similar_groups = find_relevant_groups(table_names)
    disambiguation_guide = build_table_disambiguation_prompt(similar_groups)

    if similar_groups:
        logger.info(
            "유사 테이블 그룹 감지",
            groups=[g.group_id for g in similar_groups],
            tables=table_names,
        )

    context = ContextInfo(
        table_metas=table_metas,
        past_sqls=past_sqls,
        report_sqls=report_sqls,
        manual_references=manual_refs,
        domain_terms=domain_terms,
        table_disambiguation_guide=disambiguation_guide,
        vector_past_sqls=vector_past_sqls,
        failed_sources=failed_sources,
    )

    logger.info(
        "컨텍스트 병렬 수집 완료",
        tables=len(table_metas),
        past_sqls=len(past_sqls),
        vector_past_sqls=len(vector_past_sqls),
        report_sqls=len(report_sqls),
        manuals=len(manual_refs),
        domain_terms=len(domain_terms),
    )

    return context
