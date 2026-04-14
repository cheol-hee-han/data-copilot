"""context_retriever 노드 — 실행계획의 스텝을 병렬 실행하고 결과를 raw_result에 저장한다.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

도구 실행 결과를 step.raw_result에 저장하며, state 필드에는 직접 적재하지 않는다.
interpreter 노드가 raw_result를 읽어 렌더링→판정→state 적재를 수행한다.

위임 구조:
    - 도구 실행: reason/tools.py (execute_tool, TOOL_MAP)
    - 테이블 추출: _extract_tables (rule-based)
    - enrichment: _enrich_use_cases (search_use_cases 후 테이블/코드 메타 수집)
"""

from __future__ import annotations

import asyncio
from typing import Any

from src.agents.state.state import (
    ExecutionStep,
    PipelineState,
    TableMeta,
    CodeMeta,
    ColumnInfo,
    KeyDateColumn,
    Phase,
    StepStatus,
    MAX_TOOL_CALLS,
)
from src.connectors.manager import ConnectorManager, get_connector_manager
from src.agents.nodes.reason.tools import (
    execute_tool,
    lookup_code_meta,
    lookup_table_meta,
    search_table_meta,
    _TABLE_META_TOOLS,
)
from src.utils.sqlglot_analyzer import extract_structural_hints
from src.utils.logger import get_logger
from src.utils.truncate import truncate_log, truncate_trace
from src.utils.tracker.dispatch import (
    dispatch_tracking_event,
    CONTEXT_TOOL_SUCCESS,
    CONTEXT_TOOL_ERROR,
    REASONING_STEP,
)

logger = get_logger(__name__)


def _forced_target_db() -> str:
    """FORCED 모드에서 허용되는 시스템 코드를 반환한다.

    settings.target_db_code 는 시스템 코드(ADW/BDP/CRP) 이며,
    TableMeta.db_source 도 동일 어휘이므로 직접 비교에 사용한다.
    비어있으면 빈 문자열을 반환(필터 없음).
    """
    from src.config import settings
    return settings.target_db_code


def _filter_by_forced_target(
    tables: list[TableMeta],
) -> list[TableMeta]:
    """FORCED 모드에서 타겟 외 시스템 테이블을 제거한다.

    FORCED 모드가 아니면 원본을 그대로 반환한다. PR2의 push-down
    필터 도입 시 이 함수는 완전 제거될 예정이다.
    """
    target = _forced_target_db()
    if not target:
        return tables
    filtered: list[TableMeta] = []
    for t in tables:
        if not t.db_source or t.db_source == target:
            filtered.append(t)
        else:
            logger.info(
                "FORCED 모드: 타겟 외 테이블 제외",
                table=t.table_name,
                db_source=t.db_source,
                target=target,
            )
    return filtered


def _should_skip_step(
    step: Any,
    executed_tool_keys: set[str],
    explored_tables: list[TableMeta],
) -> bool:
    """중복 실행 여부를 판정한다.

    모든 도구: 동일 tool+input 조합이 이미 실행됐으면 스킵.
    get_sample_rows: 추가로 해당 테이블에 이미 sample_rows가 있으면 스킵.

    관측 도구(get_sample_rows, get_column_values 등)는 멱등이므로
    동일 입력 재실행을 방지하여 불필요한 DB 호출을 줄인다.
    """
    tool_key = f"{step.tool}:{step.input}"
    if tool_key in executed_tool_keys:
        step.status = StepStatus.SKIPPED
        step.insight = "이미 실행한 도구+입력 — 스킵"
        return True

    if step.tool == "get_sample_rows":
        raw_name = step.input.split(",")[0].strip()
        # schema.table 또는 table 형태 모두 매칭
        bare_name = raw_name.rpartition(".")[2] if "." in raw_name else raw_name
        has_sample = any(
            t.table_name == bare_name and t.sample_rows is not None
            for t in explored_tables
        )
        if has_sample:
            step.status = StepStatus.SKIPPED
            step.insight = "이미 샘플 조회한 테이블 — 스킵"
            return True

    return False


# DB 직접 조회 도구 — 테이블명에 스키마가 필요한 도구 목록
_DB_QUERY_TOOLS = frozenset({
    "get_sample_rows",
    "get_column_profile",
    "get_date_distribution",
    "get_column_values",
})


def _qualify_table_in_input(
    tool_name: str,
    tool_input: str,
    explored_tables: list[TableMeta],
) -> str:
    """DB 직접 조회 도구의 tool_input에서 테이블명에 스키마를 자동 보충한다.

    LLM이 'TB_ADW_DEP201P,OPEN_DT'처럼 스키마 없이 테이블명만 전달하면
    explored_tables에서 해당 테이블의 schema_name을 찾아
    'ADWOWN.TB_ADW_DEP201P,OPEN_DT'로 보정한다.
    """
    if tool_name not in _DB_QUERY_TOOLS:
        return tool_input

    parts = [p.strip() for p in tool_input.split(",")]
    if not parts:
        return tool_input

    raw_table = parts[0]
    # 이미 스키마가 있으면 그대로
    if "." in raw_table:
        return tool_input

    # explored_tables에서 테이블명으로 스키마 조회
    matched_db_source = ""
    for t in explored_tables:
        if t.table_name == raw_table and t.schema_name:
            parts[0] = f"{t.schema_name}.{raw_table}"
            return ",".join(parts)
        if t.table_name == raw_table:
            matched_db_source = t.db_source

    # explored_tables 의 db_source 또는 테이블명 시스템코드로 적절한 커넥터를 고른다.
    # (외부망 환경에서는 system_db_overrides 에 의해 ADW → TEST 로 해석된다.)
    db_source = matched_db_source or ConnectorManager.parse_db_source(raw_table)
    if not db_source:
        return tool_input

    db = get_connector_manager().get_query_db(db_source=db_source)
    if db.default_schema:
        parts[0] = f"{db.default_schema}.{raw_table}"
        return ",".join(parts)

    return tool_input


async def _run_step(
    step: Any,
    executed_tool_keys: set[str],
    explored_tables: list[TableMeta],
    code_map: dict[str, CodeMeta] | None = None,
    seen_ids: dict[str, list[str]] | None = None,
) -> tuple[Any, Any, int]:
    """단일 스텝을 실행하고 결과를 step.raw_result에 저장한다.

    LLM 해석은 수행하지 않는다 (배치 해석으로 이관됨).
    도구별 후처리는 _apply_tool_result로 위임한다.
    반환값: (step, result, 소비한 tool_calls 수)
    """
    import time as _time

    # DB 직접 조회 도구: 스키마 미지정 시 explored_tables에서 자동 보충
    step.input = _qualify_table_in_input(step.tool, step.input, explored_tables)

    result = None
    _t0 = _time.perf_counter()
    try:
        exclude_ids = (seen_ids or {}).get(step.tool) or None
        if exclude_ids:
            result = await execute_tool(
                step.tool, step.input, exclude_ids=exclude_ids,
            )
        else:
            result = await execute_tool(step.tool, step.input)
        _elapsed = (_time.perf_counter() - _t0) * 1000
        step.status = StepStatus.DONE

        # 중복 방지 키 기록
        executed_tool_keys.add(f"{step.tool}:{step.input}")

        # rule-based TableMeta 추출 (search_table_meta 전용)
        new_tables = _extract_tables(step, result)
        explored_tables.extend(new_tables)

        # raw_result에 변환된 테이블 포함
        if step.tool in _TABLE_META_TOOLS:
            step.raw_result = {"tables": [t.model_dump() for t in new_tables]}

        # _TABLE_META_TOOLS는 위에서 raw_result 설정 완료
        if step.tool not in _TABLE_META_TOOLS:
            await _apply_tool_result(
                step, result, executed_tool_keys, explored_tables, code_map,
            )

        # ── 추적 ──
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
            "results_summary": [truncate_trace(f"실패: {e}")],
            "latency_ms": round(_elapsed, 1),
            "status": "error",
        })

    return step, result, 1


# ── 도구별 결과 반영 ──


async def _apply_tool_result(
    step: Any,
    result: Any,
    executed_tool_keys: set[str],
    explored_tables: list[TableMeta],
    code_map: dict[str, CodeMeta] | None,
) -> None:
    """도구 실행 결과를 step.raw_result에 저장한다.

    search_use_cases: enrichment(테이블 메타 + 코드 메타) 수행 후 통합 저장.
    search_table_meta: _extract_tables 결과 포함.
    기타 도구: 결과 그대로 저장.
    """
    if not result:
        return

    tool = step.tool

    if tool == "search_use_cases":
        # enrichment: use_case 개별 SQL에서 테이블/코드 메타 수집
        enriched = await _enrich_use_cases(
            result, executed_tool_keys, explored_tables, code_map,
        )
        step.raw_result = {"use_cases": enriched}
    elif tool in _TABLE_META_TOOLS:
        # _extract_tables는 이미 호출됨 (in _run_step) — 결과를 raw_result에 포함
        # raw_result는 _run_step에서 설정됨
        pass
    elif tool == "get_date_distribution":
        # recent_values 계산 포함
        if isinstance(result, list) and result:
            dates = sorted(result, reverse=True)
            step.raw_result = {
                "dates": result,
                "recent_values": dates[:10],
            }
        else:
            step.raw_result = result
    else:
        # 기타 도구: 결과 그대로 저장
        step.raw_result = result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 내장 후속 수집 — search_use_cases 후 관련 메타 자동 수집
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


async def _enrich_use_cases(
    use_cases: list[dict],
    executed_tool_keys: set[str],
    explored_tables: list[TableMeta],
    code_map: dict[str, CodeMeta] | None,
) -> list[dict]:
    """use_case 개별 SQL에서 테이블/코드 메타를 수집하여 각 use_case에 첨부한다.

    DB 조회는 중복 제거 후 한 번만 수행하되,
    이미 탐색된 테이블은 explored_tables에서 채워 DB 재조회 없이 매핑한다.
    결과 매핑은 use_case 단위로 첨부한다.

    Returns:
        use_case dict 리스트. 각 dict에 enrichment_tables, enrichment_codes 첨부.
    """
    # (1) use_case별 SQL 파싱 → 테이블/코드 컬럼 추출
    uc_hints: dict[str, dict] = {}  # uc_id → {"tables": [...], "codes": [...]}
    all_tables: set[str] = set()
    all_codes: set[str] = set()

    for uc in use_cases:
        uc_id = str(uc.get("_point_id", ""))
        sql = uc.get("sql", "")
        if not sql:
            uc_hints[uc_id] = {"tables": [], "codes": []}
            continue
        hints = extract_structural_hints(sql)
        tables = hints.get("source_tables", [])
        codes = hints.get("code_columns", [])
        uc_hints[uc_id] = {"tables": tables, "codes": codes}
        all_tables.update(tables)
        all_codes.update(codes)

    # (2) 이미 탐색된 테이블은 메모리에서 채움
    seen_tables = {t.table_name for t in explored_tables}
    existing_map: dict[str, list[dict]] = {}
    for t in explored_tables:
        existing_map.setdefault(t.table_name, []).append(t.model_dump())

    # (3) 중복 제거 후 테이블 메타 일괄 조회
    tables_to_fetch = [
        t for t in all_tables
        if f"lookup_table_meta:{t}" not in executed_tool_keys
        and t not in seen_tables
    ]
    fetched_tables: dict[str, list[dict]] = {}  # table_name → [TableMeta dumps]

    if tables_to_fetch:
        meta_results = await asyncio.gather(
            *(lookup_table_meta(t) for t in tables_to_fetch),
            return_exceptions=True,
        )
        for table_name, result in zip(tables_to_fetch, meta_results):
            executed_tool_keys.add(f"lookup_table_meta:{table_name}")
            if isinstance(result, Exception):
                logger.warning(
                    "내장 후속 수집: 테이블 메타 조회 실패",
                    table=table_name, error=str(result),
                )
                continue
            if isinstance(result, list):
                candidates: list[TableMeta] = []
                for m in result:
                    ct = TableMeta.from_meta(m)
                    if ct is None:
                        continue
                    _finalize_table_meta(ct, m)
                    candidates.append(ct)
                entries = []
                for ct in _filter_by_forced_target(candidates):
                    explored_tables.append(ct)
                    seen_tables.add(ct.table_name)
                    entries.append(ct.model_dump())
                if entries:
                    fetched_tables[table_name] = entries

    # (4) 중복 제거 후 코드 메타 일괄 조회
    cols_to_fetch: list[str] = []
    fetched_codes: dict[str, dict] = {}  # col_name → code_data

    if code_map is not None:
        cols_to_fetch = [
            col for col in all_codes
            if f"lookup_code_meta:{col}" not in executed_tool_keys
            and col not in code_map
        ]
        if cols_to_fetch:
            code_results = await asyncio.gather(
                *(lookup_code_meta(col) for col in cols_to_fetch),
                return_exceptions=True,
            )
            for col_name, result in zip(cols_to_fetch, code_results):
                executed_tool_keys.add(f"lookup_code_meta:{col_name}")
                if isinstance(result, Exception):
                    logger.warning(
                        "내장 후속 수집: 코드 메타 조회 실패",
                        column=col_name, error=str(result),
                    )
                    continue
                if isinstance(result, list):
                    for item in result:
                        col = item.get("code_field", "")
                        if col and col not in fetched_codes:
                            fetched_codes[col] = {
                                "column_name": col,
                                "column_desc": item.get(
                                    "code_field_desc", "",
                                ),
                                "codes": item.get("codes", {}),
                            }

    if tables_to_fetch or cols_to_fetch:
        logger.info(
            "내장 후속 수집 완료",
            fetched_tables=len(tables_to_fetch),
            fetched_codes=len(cols_to_fetch),
        )

    # (5) use_case별 결과 매핑
    enriched: list[dict] = []
    for uc in use_cases:
        uc_id = str(uc.get("_point_id", ""))
        hints_for_uc = uc_hints.get(uc_id, {"tables": [], "codes": []})

        # 해당 use_case SQL에서 추출된 테이블의 조회 결과만 매핑
        # 새로 조회한 것 우선, 없으면 기존 explored_tables에서 fallback
        uc_tables: list[dict] = []
        for tname in hints_for_uc["tables"]:
            if tname in fetched_tables:
                uc_tables.extend(fetched_tables[tname])
            elif tname in existing_map:
                uc_tables.extend(existing_map[tname])

        # 해당 use_case SQL에서 추출된 코드 컬럼의 조회 결과만 매핑
        uc_codes: dict[str, dict] = {}
        for col in hints_for_uc["codes"]:
            if col in fetched_codes:
                uc_codes[col] = fetched_codes[col]

        enriched.append({
            **uc,
            "enrichment_tables": uc_tables,
            "enrichment_codes": uc_codes,
        })

    return enriched


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 메인 노드 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


async def context_retriever_node(state: PipelineState) -> dict:
    """실행계획의 PENDING 스텝을 병렬 실행하고 결과를 raw_result에 저장한다.

    도구 실행 결과는 step.raw_result에 저장되며,
    interpreter 노드가 raw_result를 읽어 state 필드를 적재한다.
    스텝 간 데이터 의존이 없으므로 asyncio.gather로 병렬 실행한다.
    """
    reason = state.reason.model_copy(deep=True)
    reason.phase = Phase.EXPLORING

    # 로컬 dedup용 (state��� 다시 쓰지 않음 — interpreter가 raw_result에서 적재)
    explored_tables = list(reason.explored_tables)
    executed_tool_keys = set(reason.executed_tool_keys)
    code_map = dict(reason.explored_codes)
    total_tool_calls = reason.loop_guard.total_tool_calls

    # ── PENDING 스텝 필터링 후 병��� 실행 ──
    remaining = MAX_TOOL_CALLS - total_tool_calls
    pending = [
        step for step in reason.execution_plan
        if step.status == StepStatus.PENDING
        and not _should_skip_step(step, executed_tool_keys, explored_tables)
    ][:remaining]

    if pending:
        # batch 실행 전 1회: seen_ids 수집
        seen_ids: dict[str, list[str]] = {
            "search_use_cases": [
                uc.point_id for uc in reason.explored_use_cases
                if uc.point_id
            ],
            "search_manual": [
                bm.point_id for bm in reason.explored_biz_manuals
                if bm.point_id
            ],
        }
        results = await asyncio.gather(
            *(_run_step(s, executed_tool_keys, explored_tables, code_map, seen_ids)
              for s in pending),
        )
        total_tool_calls += sum(calls for _, _, calls in results)

    loop_guard = reason.loop_guard.model_copy()
    loop_guard.total_tool_calls = total_tool_calls

    reason.executed_tool_keys = executed_tool_keys
    reason.loop_guard = loop_guard

    # ─�� Reasoning Flow 트레이스 ──
    _step_results = []
    for step in reason.execution_plan:
        if step.status == StepStatus.PENDING:
            continue
        _step_results.append({
            "step": step.step,
            "tool": step.tool,
            "status": step.status.value,
            "count": _extract_result_count(step),
            "summary": truncate_trace(step.insight or ""),
        })

    _hyp = reason.current_hypothesis
    await dispatch_tracking_event(REASONING_STEP, {
        "node": "context_retriever",
        "phase": "reason",
        "step_type": "tool_execution",
        "round": reason.loop_guard.replan_count,
        "hypothesis_id": _hyp.hypothesis_id if _hyp else "",
        "inputs": {
            "hypothesis": _hyp.hypothesis_id if _hyp else "",
            "plan": [
                f"Step {s.step}: {s.tool}" for s in pending
            ] if pending else [],
        },
        "output": {
            "results": _step_results,
        },
        "routing": {
            "next_node": "context_interpreter",
            "reason": "도구 실행 완료 → 결과 해석",
        },
    })

    return {"reason": reason}


def _extract_result_count(step: ExecutionStep) -> int | str:
    """step.raw_result에서 결과 건수를 추출한다."""
    raw = step.raw_result
    if raw is None:
        return ""
    if isinstance(raw, list):
        return len(raw)
    if isinstance(raw, dict):
        for key in ("dates", "use_cases", "tables"):
            if key in raw:
                return len(raw[key])
    return ""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 테이블 추출 유틸리티
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


# ── 행내표준 날짜 접미사 (PK 컬럼에서 기준 날짜 컬럼을 식별하는 데 사용) ──
DATE_SUFFIXES: list[str] = ["YMD", "YM", "YY", "DT", "DATE"]

# ── 한글 기준 날짜 키워드 (alt_name 보조 식별에 사용) ──
KOREAN_DATE_KEYWORDS: list[str] = [
    "기준일", "기준년월", "기준년월일", "기준년", "거래일", "실행일",
]


def _find_table(
    qualified_input: str,
    explored_tables: list[TableMeta],
) -> TableMeta | None:
    """TOOL_MAP 어댑터 입력에서 테이블명을 파싱하여 explored_tables에서 매칭한다."""
    raw_table = qualified_input.split(",")[0].strip()
    bare_name = raw_table.rpartition(".")[2] if "." in raw_table else raw_table
    for table in explored_tables:
        if table.table_name == bare_name:
            return table
    return None


def _find_column(
    table: TableMeta,
    column_name: str,
) -> ColumnInfo | None:
    """TableMeta에서 컬럼명으로 ColumnInfo를 찾는다."""
    for col in table.columns:
        if col.name == column_name:
            return col
    return None


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


def _finalize_table_meta(ct: TableMeta, raw_meta: dict) -> None:
    """TableMeta에 schema 보정 + key_date_columns 식별을 적용한다.

    _extract_tables와 _enrich_use_cases 양쪽에서 공통으로 사용한다.
    """
    if not ct.schema_name:
        db = get_connector_manager().get_query_db(db_source=ct.db_source)
        ct.schema_name = db.default_schema

    raw_cols = raw_meta.get("columns", [])
    _, pk_columns = _parse_meta_columns(raw_cols)
    ct.key_date_columns = _resolve_key_date_columns(pk_columns, raw_cols)


def _extract_tables(
    step: Any,
    result: Any,
) -> list[TableMeta]:
    """메타 응답에서 TableMeta을 추출한다 (rule-based).

    PK 컬럼에서 행내표준 날짜 접미사로 key_date_columns를 식별한다.
    """
    if step.tool not in _TABLE_META_TOOLS:
        return []
    if not isinstance(result, list):
        return []

    tables: list[TableMeta] = []
    for meta in result:
        ct = TableMeta.from_meta(meta)
        if ct is None:
            continue
        _finalize_table_meta(ct, meta)
        tables.append(ct)
    return _filter_by_forced_target(tables)
