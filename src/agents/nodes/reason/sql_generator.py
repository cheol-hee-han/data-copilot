"""sql_generator 노드 — 누적 지식 기반 SQL 생성.

CONFIRMED knowledge_items와 candidate_tables를 기반으로 SQL을 생성한다.
재진입 시 failure_reason(이전 검증 피드백)을 프롬프트에 반드시 포함하고,
dead_ends를 참고하여 이전 실패 패턴을 반복하지 않는다.

멀티 DB 라우팅:
    candidate_tables의 db_source(테이블명 시스템코드에서 자동 파싱)를 확인하여
    SQL dialect(postgres, sybase, impala)을 결정한다.
    크로스 DB(ADW+BDP 혼재) 감지 시 명확화 질문으로 사용자에게 안내한다.

프롬프트 구성:
    - 기본: SQL_GENERATOR_SYSTEM (dialect 힌트 내장)
    - 재시도: SQL_GENERATOR_FIX_SECTION (failure_reason 포함)
    - 테이블 상세: _format_table_details()로 inferred_* 필드를 "(LLM 추론)" 태그와 함께 주입
    - confirmed_terms: CONFIRMED 지식 항목을 자연어로 정리

핵심 함수:
    - sql_generator_node: 메인 노드 함수
    - _call_llm_for_sql: LLM 호출 + SQL 추출 (explanation 필드는 현재 미사용)
    - _format_table_details: CandidateTable → 프롬프트용 텍스트 변환
    - _detect_db_source: 후보 테이블에서 dialect 자동 판정

위임 구조:
    - 프롬프트: system_prompts.py의 SQL_GENERATOR_SYSTEM, SQL_GENERATOR_FIX_SECTION

v2.0 (2026-03-25): agentic 전용 프롬프트(dialect 힌트 내장)로 전환.
"""

from __future__ import annotations

import time

from src.agents.state.state import (
    CandidateTable,
    ColumnInfo,
    Phase,
    PipelineState,
    ReasoningState,
    TableSelectionStatus,
)
from src.connectors.manager import ConnectorManager, get_connector_manager
from src.agents.nodes.system_prompts import (
    SQL_GENERATOR_SYSTEM,
    SQL_GENERATOR_FIX_SECTION,
)
from src.config import settings
from src.utils.llm import llm_call_with_parse_retry
from src.utils.llm.response import extract_json
from src.utils.llm.prompt import (
    render_prompt,
    serialize_decomp_slots,
)
from src.utils.logger import get_logger
from src.utils.tracker import record_prompt_variables
from src.utils.truncate import format_sql, truncate_log

logger = get_logger(__name__)


def _format_table_for_sql_prompt(ct: CandidateTable) -> str:
    """CandidateTable을 SQL 생성 프롬프트용 텍스트로 포맷한다.

    컬럼별 한글명·타입·설명·PK 여부를 모두 전달하여
    SQL Generator가 정확한 컬럼 선택과 조인을 할 수 있도록 한다.
    """
    lines: list[str] = [_format_table_header(ct)]
    lines.extend(_format_columns(ct))
    lines.extend(_format_table_details(ct))
    return "\n".join(lines)


def _format_table_header(ct: CandidateTable) -> str:
    """테이블 헤더 라인을 생성한다."""
    header = f"- {ct.qualified_name}"
    if ct.alt_name:
        header += f" ({ct.alt_name})"
    if ct.description:
        header += f": {ct.description}"
    return header


def _format_column_line(c: ColumnInfo) -> str:
    """단일 ColumnInfo를 프롬프트 라인으로 포맷한다."""
    parts = [f"    {c.name}"]
    if c.alt_name:
        parts.append(f"({c.alt_name})")
    if c.col_type:
        parts.append(f" {c.col_type}")
    if c.is_pk:
        parts.append(" [PK]")
    if c.description:
        parts.append(f" — {c.description}")
    return "".join(parts)


def _format_columns(ct: CandidateTable) -> list[str]:
    """컬럼 상세 라인 목록을 생성한다."""
    if not ct.columns:
        return []
    return ["  컬럼:"] + [_format_column_line(c) for c in ct.columns]


def _format_table_details(ct: CandidateTable) -> list[str]:
    """join_keys, LLM 추론, 날짜 관찰 라인 목록을 생성한다."""
    lines: list[str] = []
    if ct.join_keys:
        lines.append(f"  join_keys: {ct.join_keys}")
    if ct.inferred_entity_scope:
        lines.append(f"  엔티티: {ct.inferred_entity_scope} (LLM 추론)")
    if ct.inferred_functional_usage:
        lines.append(f"  용도: {ct.inferred_functional_usage} (LLM 추론)")
    if ct.inferred_data_refresh_hint:
        lines.append(f"  갱신: {ct.inferred_data_refresh_hint} (LLM 추론)")
    for odc in ct.observed_date_columns:
        lines.append(
            f"  기준컬럼 {odc.column_name}: "
            f"{odc.date_range}, {odc.date_pattern}"
        )
    if not ct.observed_date_columns and ct.key_date_columns:
        for kdc in ct.key_date_columns:
            lines.append(f"  기준컬럼(PK): {kdc.column_name}")
    return lines


async def sql_generator_node(state: PipelineState) -> dict:
    """누적 지식을 컨텍스트로 SQL을 생성한다."""
    reason = state.reason.model_copy(deep=True)
    reason.phase = Phase.GENERATING

    reason.loop_guard = reason.loop_guard.model_copy()
    reason.loop_guard.increment_generate()

    # dialect 결정 (커넥터에서 직접 가져옴)
    db = get_connector_manager().get_query_db(reason)
    dialect = db.dialect

    # 크로스 DB 감지
    sources = {
        ct.db_source or ConnectorManager.parse_db_source(ct.table_name)
        for ct in reason.candidate_tables
        if ct.table_name
    }
    sources.discard("")

    if len(sources) > 1:
        adw_tables = [
            ct.qualified_name for ct in reason.candidate_tables
            if (ct.db_source or ConnectorManager.parse_db_source(ct.table_name)) == "adw"
        ]
        bdp_tables = [
            ct.qualified_name for ct in reason.candidate_tables
            if (ct.db_source or ConnectorManager.parse_db_source(ct.table_name)) == "bigdata"
        ]
        reason.phase = Phase.VERIFYING
        question = (
            "요청하신 데이터가 서로 다른 시스템에 있습니다:\n"
            f"  - 정보계 DW(ADW): {', '.join(adw_tables)}\n"
            f"  - 빅데이터(BDP): {', '.join(bdp_tables)}\n"
            "한 번의 SQL로 조회할 수 없어 각각 따로 조회해야 합니다.\n"
            "어느 쪽 데이터를 먼저 조회할까요?"
        )
        return {
            "reason": reason,
            "awaiting_clarification": True,
            "clarification_turns": state.clarification_turns + 1,
            "clarification_question": question,
            "formatted_response": question,
        }

    # agentic 전용 프롬프트 조립
    prompt, prompt_vars = _build_agentic_prompt(
        reason, state.preprocessed_input, dialect,
    )

    try:
        generated = await _call_llm_for_sql(
            prompt, state.preprocessed_input,
        )
    except Exception as e:
        logger.error("SQL 생성 LLM 호출 오류", error=str(e))
        generated = ""

    reason.generated_sql = generated
    reason.failure_type = None
    reason.failure_reason = None

    # ── 추적: 생성된 SQL + 치환 변수 ──
    attempt = reason.loop_guard.generate_attempts
    table_names = [ct.qualified_name for ct in reason.candidate_tables]
    logger.info(
        "SQL 생성 완료",
        dialect=dialect,
        attempt=attempt,
        tables=table_names,
        sql=("\n" + format_sql(generated, dialect))
        if generated else "(빈 SQL)",
    )

    await record_prompt_variables(prompt_vars)

    return {"reason": reason}


def _build_agentic_prompt(
    reason: ReasoningState,
    original_query: str,
    dialect: str,
) -> tuple[str, dict[str, str]]:
    """SQL_GENERATOR_SYSTEM 템플릿에 상태를 주입한다.

    Returns:
        (치환된 프롬프트, 치환 변수 사전) 튜플.
    """
    decomp = reason.query_decomposition

    # 확인된 지식 항목
    confirmed_text = reason.format_confirmed_text()

    # 테이블 정보 (REJECTED 제외, 3측면 정보 포함)
    active_tables = [
        ct for ct in reason.candidate_tables
        if ct.selection_status != TableSelectionStatus.REJECTED
    ]
    tables_text = "\n".join(
        _format_table_for_sql_prompt(ct)
        for ct in active_tables
    ) if active_tables else "(후보 테이블 없음)"

    # 조인 힌트 (각 테이블의 join_keys 기반)
    join_entries = [
        f"{ct.qualified_name}: join_keys={ct.join_keys}"
        for ct in active_tables
        if ct.join_keys
    ]
    join_text = "\n".join(join_entries) if join_entries else "(미확인)"

    # 검증된 활용사례 SQL (관련 판정분만, reason 포함)
    relevant = [
        uc for uc in reason.explored_use_cases
        if uc.get("_relevant", True)
    ]
    ref_blocks: list[str] = []
    for i, uc in enumerate(relevant[:10], 1):
        sql = uc.get("sql", "")
        if not sql:
            continue
        reason_text = uc.get("_eval_reason", "")
        desc = uc.get("description", "")
        lines = [f"[{i}]"]
        if desc:
            lines.append(f"- 설명: {desc}")
        if reason_text:
            lines.append(f"- 관련성: {reason_text}")
        lines.append(sql)
        ref_blocks.append("\n".join(lines))
    ref_text = "\n\n".join(ref_blocks) if ref_blocks else "(없음)"

    # Dead-ends
    dead_text = reason.format_dead_ends_text()

    # Fix section (이전 검증 피드백이 있으면 재시도 프롬프트에 포함)
    fix_text = ""
    if reason.failure_reason:
        fix_text = SQL_GENERATOR_FIX_SECTION.replace(
            "{fix_instruction}", reason.failure_reason,
        )

    prompt = SQL_GENERATOR_SYSTEM
    replacements = {
        "{original_query}": original_query or "",
        **serialize_decomp_slots(decomp),
        "{confirmed_terms}": confirmed_text,
        "{tables}": tables_text,
        "{join_path}": join_text,
        "{reference_sqls}": ref_text,
        "{dead_ends}": dead_text,
        "{fix_section}": fix_text,
        "{dialect}": dialect,
    }
    prompt, variables = render_prompt(prompt, replacements)
    return prompt, variables


def _parse_sql_response(raw: str) -> str:
    """LLM 응답에서 SQL을 추출한다.

    JSON 형식 → 마크다운 코드 블록 → raw 텍스트 순으로 시도.
    """
    data = extract_json(raw)
    if data:
        sql = data.get("sql", "")
        if sql:
            return sql

    cleaned = _clean_sql_response(raw)
    if cleaned:
        return cleaned

    raise ValueError("SQL을 추출할 수 없음: JSON 'sql' 키 없음, 코드 블록 없음")


async def _call_llm_for_sql(
    prompt: str,
    query: str,
) -> str:
    """LLM을 호출하여 SQL을 생성한다."""
    _, sql = await llm_call_with_parse_retry(
        system=prompt,
        messages=[{"role": "user", "content": query}],
        parse_fn=_parse_sql_response,
        max_tokens=settings.llm_format_max_tokens,
        timeout=settings.llm_long_timeout,
        node_name="agentic_SQL생성",
    )
    return sql


def _clean_sql_response(raw: str) -> str:
    """LLM 응답에서 순수 SQL만 추출한다."""
    if "```" not in raw:
        return raw.strip()

    lines = raw.split("\n")
    cleaned: list[str] = []
    in_block = False
    for line in lines:
        if line.strip().startswith("```"):
            in_block = not in_block
            continue
        if in_block:
            cleaned.append(line)
    return "\n".join(cleaned).strip()
