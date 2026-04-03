"""knowledge_fetcher 노드 — 실행계획 스텝을 순차 실행하고 관찰 데이터를 수집한다.

context_explorer의 Phase 1-2를 분리한 노드.
도구 실행(Phase 1)과 관찰 데이터 수집(Phase 2)만 수행하며 LLM 호출은 없다.

Phase 1: PENDING 스텝 순차 실행 (rule-based, LLM 없음)
Phase 2: 관찰 데이터 수집 (날짜 분포, 샘플 — DB 쿼리)

위임 구조:
    - 도구 실행: reason/tools.py (execute_tool, TOOL_MAP)
    - 테이블 추출: _extract_tables (rule-based)
"""

from __future__ import annotations

import asyncio
from typing import Any

from src.agents.state.state import (
    PipelineState,
    CandidateTable,
    CodeMeta,
    ColumnInfo,
    KeyDateColumn,
    ObservedDateColumn,
    Phase,
    StepStatus,
    MAX_TOOL_CALLS,
)
from src.connectors.manager import get_connector_manager
from src.agents.nodes.reason.tools import (
    execute_tool,
    extract_hints_from_use_cases,
    get_date_distribution,
    get_sample_rows,
    detect_date_pattern,
    search_code_meta,
    search_table_meta,
)
from src.utils.logger import get_logger
from src.utils.truncate import truncate_log, truncate_trace
from src.utils.tracker.dispatch import (
    dispatch_tracking_event,
    CONTEXT_TOOL_SUCCESS,
    CONTEXT_TOOL_ERROR,
)

logger = get_logger(__name__)


# 중복 방지 대상 도구 목록 (이미 검색한 입력은 스킵)
_DEDUP_TOOLS: frozenset[str] = frozenset({
    "search_use_cases", "search_table_meta",
    "search_code_meta", "search_manual", "search_glossary",
})


def _should_skip_step(
    step: Any,
    searched_queries: list[str],
    candidate_tables: list[CandidateTable],
) -> bool:
    """중복 실행 여부를 판정한다.

    검색 계열 도구는 동일 입력이 이미 검색됐으면 스킵.
    get_sample_rows는 해당 테이블에 이미 sample_rows가 있으면 스킵.
    """
    if step.tool in _DEDUP_TOOLS and step.input in searched_queries:
        step.status = StepStatus.SKIPPED
        step.insight = "이미 검색한 쿼리 — 스킵"
        return True
    if step.tool == "get_sample_rows":
        raw_name = step.input.split(",")[0].strip()
        # schema.table 또는 table 형태 모두 매칭
        bare_name = raw_name.rpartition(".")[2] if "." in raw_name else raw_name
        has_sample = any(
            t.table_name == bare_name and t.sample_rows is not None
            for t in candidate_tables
        )
        if has_sample:
            step.status = StepStatus.SKIPPED
            step.insight = "이미 샘플 조회한 테이블 — 스킵"
            return True
    return False


async def _run_step(
    step: Any,
    searched_queries: list[str],
    candidate_tables: list,
    explored_use_cases: list,
    code_map: dict[str, CodeMeta] | None = None,
) -> tuple[Any, Any, int]:
    """단일 스텝을 실행하고 rule-based 결과만 반영한다.

    LLM 해석은 수행하지 않는다 (배치 해석으로 이관됨).
    반환값: (step, result, 소비한 tool_calls 수)
    """
    import time as _time

    result = None
    _t0 = _time.perf_counter()
    try:
        result = await execute_tool(step.tool, step.input)
        _elapsed = (_time.perf_counter() - _t0) * 1000
        step.status = StepStatus.DONE

        if step.tool != "get_sample_rows":
            searched_queries.append(step.input)

        # rule-based CandidateTable 추출 (LLM 없이)
        new_tables = _extract_tables(step, result)
        candidate_tables.extend(new_tables)

        if step.tool == "search_use_cases" and result:
            offset = len(explored_use_cases)
            for i, uc in enumerate(result):
                uc["id"] = f"uc_{offset + i + 1:03d}"
                uc["_search_query"] = step.input
            explored_use_cases.extend(result)

            # ── 내장 후속 수집: 유사SQL 관련 테이블 메타 + 코드 메타 ──
            await _fetch_use_case_related_metas(
                result, searched_queries, candidate_tables, code_map,
            )

        # 코드 메타 결과를 code_map에 축적 (컬럼 단위 중복 방지)
        if step.tool == "search_code_meta" and result and code_map is not None:
            for item in result:
                col = item.get("code_field", "")
                if col and col not in code_map:
                    code_map[col] = CodeMeta(
                        column_name=col,
                        column_desc=item.get("code_field_desc", ""),
                        codes=item.get("codes", {}),
                    )

        # ── 추적: 도구 실행 성공 ──
        result_count = len(result) if isinstance(result, list) else 1
        logger.info(
            "도구 실행 완료",
            tool=step.tool,
            input=truncate_log(step.input),
            results=result_count,
            new_tables=len(new_tables),
            latency_ms=round(_elapsed, 1),
        )

        await dispatch_tracking_event(CONTEXT_TOOL_SUCCESS, {
            "source": step.tool,
            "query": truncate_trace(step.input),
            "results_count": result_count,
            "results_summary": [
                f"결과 {result_count}건 수집 (배치 해석 대기)",
            ],
            "latency_ms": round(_elapsed, 1),
        })

    except Exception as e:
        _elapsed = (_time.perf_counter() - _t0) * 1000
        step.status = StepStatus.FAILED
        step.insight = f"도구 실행 실패: {e}"
        logger.warning(
            "도구 실행 실패",
            tool=step.tool,
            input=truncate_log(step.input),
            error=str(e),
            latency_ms=round(_elapsed, 1),
        )

        await dispatch_tracking_event(CONTEXT_TOOL_ERROR, {
            "source": step.tool,
            "query": truncate_trace(step.input),
            "results_count": 0,
            "results_summary": [
                truncate_trace(f"실패: {e}"),
            ],
            "latency_ms": round(_elapsed, 1),
            "status": "error",
        })

    return step, result, 1


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 내장 후속 수집 — search_use_cases 후 관련 메타 자동 수집
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


async def _fetch_use_case_related_metas(
    use_cases: list[dict],
    searched_queries: list[str],
    candidate_tables: list[CandidateTable],
    code_map: dict[str, CodeMeta] | None,
) -> None:
    """유사SQL에서 추출한 테이블 메타 + 코드 메타를 자동 수집한다.

    search_use_cases 실행의 내장 후속 처리.
    execution_plan에 스텝을 추가하지 않고,
    결과를 candidate_tables와 code_map에 직접 반영한다.
    tool_calls 카운트에 포함하지 않는다 (스텝 단위로만 카운팅).
    """
    hints = extract_hints_from_use_cases(use_cases)
    if hints.is_empty():
        return

    already_queried = set(searched_queries)

    # (1) source_tables → 테이블 메타 수집 (병렬)
    tables_to_fetch = [
        t for t in hints.source_tables if t not in already_queried
    ]
    if tables_to_fetch:
        meta_results = await asyncio.gather(
            *(search_table_meta(t) for t in tables_to_fetch),
            return_exceptions=True,
        )
        for table_name, result in zip(tables_to_fetch, meta_results):
            searched_queries.append(table_name)
            if isinstance(result, Exception):
                logger.warning(
                    "내장 후속 수집: 테이블 메타 조회 실패",
                    table=table_name, error=str(result),
                )
                continue
            if isinstance(result, list):
                for m in result:
                    ct = CandidateTable.from_meta(m)
                    if ct is not None:
                        candidate_tables.append(ct)

    # (2) code_columns → 코드 메타 수집 (병렬)
    if code_map is not None:
        cols_to_fetch = [
            col for col in hints.code_columns
            if col not in already_queried and col not in code_map
        ]
        if cols_to_fetch:
            code_results = await asyncio.gather(
                *(search_code_meta(col) for col in cols_to_fetch),
                return_exceptions=True,
            )
            for col_name, result in zip(cols_to_fetch, code_results):
                searched_queries.append(col_name)
                if isinstance(result, Exception):
                    logger.warning(
                        "내장 후속 수집: 코드 메타 조회 실패",
                        column=col_name, error=str(result),
                    )
                    continue
                if isinstance(result, list):
                    for item in result:
                        col = item.get("code_field", "")
                        if col and col not in code_map:
                            code_map[col] = CodeMeta(
                                column_name=col,
                                column_desc=item.get(
                                    "code_field_desc", "",
                                ),
                                codes=item.get("codes", {}),
                            )

    fetched_tables = len(tables_to_fetch)
    fetched_codes = len(cols_to_fetch) if code_map is not None else 0
    if fetched_tables or fetched_codes:
        logger.info(
            "내장 후속 수집 완료",
            fetched_tables=fetched_tables,
            fetched_codes=fetched_codes,
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 메인 노드 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


async def knowledge_fetcher_node(state: PipelineState) -> dict:
    """실행계획의 PENDING 스텝을 순차 실행하고 관찰 데이터를 수집한다.

    Phase 1: 도구 전부 실행 (rule-based만, LLM 없음)
    Phase 2: 관찰 데이터 수집 (날짜 분포, 샘플 — DB 쿼리)
    """
    reason = state.reason.model_copy(deep=True)
    reason.phase = Phase.EXPLORING

    execution_plan = list(reason.execution_plan)
    knowledge_items = list(reason.knowledge_items)
    candidate_tables = list(reason.candidate_tables)
    searched_queries = list(reason.searched_queries)
    discovered_facts = list(reason.discovered_facts)
    explored_use_cases = list(reason.explored_use_cases)
    code_map = dict(reason.code_map)
    total_tool_calls = reason.loop_guard.total_tool_calls

    # ── Phase 1: 도구 전부 실행 (rule-based만, LLM 없음) ──
    for step in execution_plan:
        not_pending = step.status != StepStatus.PENDING
        if not_pending or total_tool_calls >= MAX_TOOL_CALLS:
            continue

        if _should_skip_step(step, searched_queries, candidate_tables):
            continue

        _, _, calls = await _run_step(
            step, searched_queries,
            candidate_tables, explored_use_cases,
            code_map,
        )
        total_tool_calls += calls

    loop_guard = reason.loop_guard.model_copy()
    loop_guard.total_tool_calls = total_tool_calls

    reason.execution_plan = execution_plan
    reason.searched_queries = searched_queries
    reason.discovered_facts = discovered_facts
    reason.explored_use_cases = explored_use_cases
    reason.code_map = code_map
    reason.loop_guard = loop_guard
    reason.knowledge_items = knowledge_items

    # ── Phase 2: 관찰 데이터 수집 (DB 쿼리, 전체 대상) ──
    await _observe_all_date_distributions(candidate_tables)
    await _sample_unsampled_tables(candidate_tables)

    reason.candidate_tables = candidate_tables

    return {"reason": reason}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 관찰 데이터 수집 (Phase 2)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


# ── 행내표준 날짜 접미사 (PK 컬럼에서 기준 날짜 컬럼을 식별하는 데 사용) ──
DATE_SUFFIXES: list[str] = ["YMD", "YM", "YY", "DT", "DATE"]

# ── 한글 기준 날짜 키워드 (alt_name 보조 식별에 사용) ──
KOREAN_DATE_KEYWORDS: list[str] = [
    "기준일", "기준년월", "기준년월일", "기준년", "거래일", "실행일",
]


async def _observe_all_date_distributions(
    candidate_tables: list[CandidateTable],
) -> None:
    """모든 CandidateTable의 기준 컬럼별 날짜 분포를 조회한다.

    key_date_columns가 없거나 inferred_key_date_column만 있는 경우도 처리.
    조회 실패 시 graceful fallback — 기존 candidate_tables 유지.
    """
    for table in candidate_tables:
        # 1순위: rule-based 기준 컬럼
        date_cols = list(table.key_date_columns)

        # 2순위: LLM fallback 기준 컬럼 (rule-based 결과 없을 때만 사용)
        if not date_cols and table.inferred_key_date_column:
            date_cols = [KeyDateColumn(
                column_name=table.inferred_key_date_column,
                suffix="",
                source="llm_fallback",
            )]

        for kdc in date_cols:
            try:
                distinct_dates = await get_date_distribution(
                    table.table_name, kdc.column_name,
                    schema_name=table.schema_name,
                    db_source=table.db_source,
                )
                if not distinct_dates:
                    continue
                dates = sorted(distinct_dates)
                table.observed_date_columns.append(ObservedDateColumn(
                    column_name=kdc.column_name,
                    date_range=f"{dates[0]} ~ {dates[-1]}",
                    date_pattern=detect_date_pattern(dates),
                ))
            except Exception as e:
                logger.warning(
                    "날짜 분포 조회 실패, 스킵",
                    table=table.table_name,
                    column=kdc.column_name,
                    error=str(e),
                )


async def _sample_unsampled_tables(
    candidate_tables: list[CandidateTable],
) -> None:
    """아직 샘플 조회하지 않은 후보 테이블의 샘플 데이터를 조회한다.

    비교 판정 전에 실행하여 샘플 데이터를 비교 프롬프트에 포함한다.
    """
    for table in candidate_tables:
        if table.sample_rows is not None:
            continue
        try:
            rows = await get_sample_rows(
                table.table_name,
                schema_name=table.schema_name,
                db_source=table.db_source,
            )
            table.sample_rows = rows
            if rows:
                logger.info(
                    "샘플 데이터 조회 완료",
                    table=table.table_name,
                    rows=len(rows),
                )
        except Exception as e:
            logger.warning(
                "샘플 데이터 조회 실패, 스킵",
                table=table.table_name,
                error=str(e),
            )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 테이블 추출 유틸리티
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _identify_key_date_columns(pk_columns: list[str]) -> list[KeyDateColumn]:
    """PK 컬럼에서 행내표준 날짜 접미사로 기준 컬럼을 식별한다."""
    result: list[KeyDateColumn] = []
    for col in pk_columns:
        col_upper = col.upper()
        for suffix in DATE_SUFFIXES:
            if col_upper.endswith(f"_{suffix}") or col_upper == suffix:
                result.append(KeyDateColumn(
                    column_name=col,
                    suffix=suffix,
                    source="pk_rule",
                ))
                break
    return result


def _infer_suffix_from_column_name(name: str) -> str:
    """컬럼명에서 행내표준 날짜 접미사를 추론한다."""
    name_upper = name.upper()
    for s in DATE_SUFFIXES:
        if name_upper.endswith(f"_{s}") or name_upper == s:
            return s
    return ""


def _matches_korean_date_keyword(alt: str) -> bool:
    """alt_name이 기준 날짜 키워드를 포함하는지 확인한다."""
    return any(kw in alt for kw in KOREAN_DATE_KEYWORDS)


def _identify_key_date_by_alt_name(columns: list[dict]) -> list[KeyDateColumn]:
    """한글 컬럼명(alt_name)에서 기준 날짜 컬럼을 보조 식별한다."""
    result: list[KeyDateColumn] = []
    for col in columns:
        alt = col.get("alt_name", "")
        name = col.get("name", "")
        if not alt or not _matches_korean_date_keyword(alt):
            continue
        result.append(KeyDateColumn(
            column_name=name,
            suffix=_infer_suffix_from_column_name(name),
            source="alt_name_rule",
        ))
    return result


def _parse_meta_columns(
    raw_cols: Any,
) -> tuple[list[ColumnInfo], list[str]]:
    """메타 응답의 columns 필드를 파싱한다.

    반환: (ColumnInfo 목록, PK 컬럼명 목록)
    """
    if not isinstance(raw_cols, list):
        return [], []
    column_infos: list[ColumnInfo] = []
    pk_columns: list[str] = []
    for c in raw_cols:
        if not isinstance(c, dict) or not c.get("name"):
            continue
        name = c["name"]
        is_pk = bool(c.get("is_pk"))
        column_infos.append(ColumnInfo(
            name=name,
            alt_name=c.get("alt_name", ""),
            description=c.get("description", ""),
            col_type=c.get("type", ""),
            is_pk=is_pk,
        ))
        if is_pk:
            pk_columns.append(name)
    return column_infos, pk_columns


def _resolve_key_date_columns(
    pk_columns: list[str],
    raw_cols: Any,
) -> list[KeyDateColumn]:
    """PK 접미사 → alt_name 순으로 기준 날짜 컬럼을 식별한다."""
    key_date_cols = _identify_key_date_columns(pk_columns)
    if not key_date_cols:
        key_date_cols = _identify_key_date_by_alt_name(
            raw_cols if isinstance(raw_cols, list) else [],
        )
    return key_date_cols


def _extract_tables(
    step: Any,
    result: Any,
) -> list[CandidateTable]:
    """메타 응답에서 CandidateTable을 추출한다 (rule-based).

    PK 컬럼에서 행내표준 날짜 접미사로 key_date_columns를 식별한다.
    """
    if step.tool != "search_table_meta":
        return []
    if not isinstance(result, list):
        return []

    tables: list[CandidateTable] = []
    for meta in result:
        ct = CandidateTable.from_meta(meta)
        if ct is None:
            continue

        # schema_name이 없으면 커넥터의 기본 스키마 사용
        if not ct.schema_name:
            db = get_connector_manager().get_query_db(db_source=ct.db_source)
            ct.schema_name = db.default_schema

        # PK 접미사 기반 기준 날짜 컬럼 식별
        raw_cols = meta.get("columns", [])
        _, pk_columns = _parse_meta_columns(raw_cols)
        ct.key_date_columns = _resolve_key_date_columns(pk_columns, raw_cols)

        tables.append(ct)
    return tables
