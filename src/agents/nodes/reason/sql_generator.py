"""sql_generator 노드 — 누적 지식 기반 SQL 생성.

CONFIRMED knowledge_items만 사용하여 SQL을 생성한다.
재진입 시 sql_fix_instruction을 프롬프트에 반드시 포함하고,
dead_ends를 참고하여 이전 실패 패턴을 반복하지 않는다.

멀티 DB 라우팅:
  candidate_tables의 db_source(테이블명 시스템코드에서 자동 파싱)를 확인하여
  SQL dialect을 결정한다. 크로스 DB(ADW+BDP 혼재) 감지 시 사용자에게 안내한다.

v2.0 (2026-03-25): agentic 전용 프롬프트(dialect 힌트 내장)로 전환.
"""

from __future__ import annotations

import json
import re
import time

from src.agents.state.state import (
    CandidateTable,
    PipelineState,
    ReasoningState,
)
from src.utils.db_routing import parse_db_source, get_dialect_for_source
from src.agents.nodes.system_prompts import (
    REASON_GENERATE_SQL,
    REASON_GENERATE_SQL_FIX,
)
from src.config import settings
from src.utils.llm import get_llm_client
from src.utils.logger import get_logger
from src.utils.tracker import record_prompt_variables

logger = get_logger(__name__)


def _format_table_for_sql_prompt(ct: CandidateTable) -> str:
    """CandidateTable을 SQL 생성 프롬프트용 텍스트로 포맷한다.

    3측면 정보(엔티티 범위, 기능적 용도, 갱신 주기)와
    관찰된 날짜 분포를 포함하여 SQL Generator가
    정확한 WHERE 조건과 테이블 선택을 할 수 있도록 한다.
    """
    col_labels = [
        f"{c}({ct.column_alt_names[c]})"
        if c in ct.column_alt_names else c
        for c in ct.relevant_columns[:8]
    ]
    table_id = ct.qualified_name
    line = f"- {table_id}: {ct.role} (컬럼: {', '.join(col_labels)})"

    details: list[str] = []
    if ct.inferred_entity_scope:
        details.append(f"  엔티티: {ct.inferred_entity_scope}")
    if ct.inferred_functional_usage:
        details.append(f"  용도: {ct.inferred_functional_usage}")
    if ct.inferred_data_refresh_hint:
        details.append(f"  갱신: {ct.inferred_data_refresh_hint}")
    for odc in ct.observed_date_columns:
        details.append(
            f"  기준컬럼 {odc.column_name}: "
            f"{odc.date_range}, {odc.date_pattern}"
        )
    if not ct.observed_date_columns and ct.key_date_columns:
        for kdc in ct.key_date_columns:
            details.append(f"  기준컬럼(PK): {kdc.column_name}")

    if details:
        return line + "\n" + "\n".join(details)
    return line


def determine_dialect(reason: ReasoningState) -> str:
    """candidate_tables의 db_source로 SQL dialect을 결정한다.

    모든 테이블이 같은 DB 소스면 해당 dialect 반환.
    서로 다른 DB 소스가 혼재하면 "CROSS_DB" 반환.
    """
    sources = {
        ct.db_source or parse_db_source(ct.table_name)
        for ct in reason.candidate_tables
        if ct.table_name
    }
    # 빈 문자열 제거
    sources.discard("")

    if len(sources) > 1:
        return "CROSS_DB"
    if not sources:
        return get_dialect_for_source("")
    return get_dialect_for_source(sources.pop())


async def sql_generator_node(state: PipelineState) -> dict:
    """누적 지식을 컨텍스트로 SQL을 생성한다."""
    reason = state.reason.model_copy(deep=True)
    reason.phase = "GENERATING"

    reason.loop_guard = reason.loop_guard.model_copy()
    reason.loop_guard.increment_generate()

    # 크로스 DB 감지
    dialect = determine_dialect(reason)
    if dialect == "CROSS_DB":
        adw_tables = [
            ct.qualified_name for ct in reason.candidate_tables
            if (ct.db_source or parse_db_source(ct.table_name)) == "adw"
        ]
        bdp_tables = [
            ct.qualified_name for ct in reason.candidate_tables
            if (ct.db_source or parse_db_source(ct.table_name)) == "bigdata"
        ]
        reason.phase = "VERIFYING"
        return {
            "reason": reason,
            "awaiting_clarification": True,
            "clarification_question": (
                "요청하신 데이터가 서로 다른 시스템에 있습니다:\n"
                f"  - 정보계 DW(ADW): {', '.join(adw_tables)}\n"
                f"  - 빅데이터(BDP): {', '.join(bdp_tables)}\n"
                "한 번의 SQL로 조회할 수 없어 각각 따로 조회해야 합니다.\n"
                "어느 쪽 데이터를 먼저 조회할까요?"
            ),
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
    reason.sql_fix_instruction = None
    reason.sql_validation_result = None

    # ── 추적: 생성된 SQL + 치환 변수 ──
    attempt = reason.loop_guard.generate_attempts
    table_names = [ct.qualified_name for ct in reason.candidate_tables]
    logger.info(
        "SQL 생성 완료",
        dialect=dialect,
        attempt=attempt,
        tables=table_names,
        sql=generated[:300] if generated else "(빈 SQL)",
    )

    record_prompt_variables(prompt_vars)

    return {"reason": reason}


def _build_agentic_prompt(
    reason: ReasoningState,
    original_query: str,
    dialect: str,
) -> tuple[str, dict[str, str]]:
    """REASON_GENERATE_SQL 템플릿에 상태를 주입한다.

    Returns:
        (치환된 프롬프트, 치환 변수 사전) 튜플.
    """
    decomp = reason.query_decomposition

    # 확인된 지식 항목
    confirmed = reason.get_confirmed_knowledge()
    confirmed_text = "\n".join(
        f"- {ki.key}: {ki.value} ({ki.source})"
        for ki in confirmed
    ) if confirmed else "(확인된 항목 없음)"

    # 테이블 정보 (3측면 정보 포함)
    tables_text = "\n".join(
        _format_table_for_sql_prompt(ct)
        for ct in reason.candidate_tables
    ) if reason.candidate_tables else "(후보 테이블 없음)"

    # 조인 경로
    join_text = (
        str(reason.confirmed_join_path)
        if reason.confirmed_join_path
        else "(미확인)"
    )

    # 구조적 힌트
    hints_text = (
        reason.structural_hints.to_prompt_text()
        if not reason.structural_hints.is_empty()
        else "(없음)"
    )

    # 활용사례 SQL
    ref_sqls = [
        uc.get("sql", "")
        for uc in reason.explored_use_cases[:3]
        if uc.get("sql")
    ]
    ref_text = "\n".join(
        f"```sql\n{sql}\n```" for sql in ref_sqls
    ) if ref_sqls else "(없음)"

    # Dead-ends
    dead_text = "\n".join(
        f"- [{de.failure_type}] {de.reason} "
        f"(테이블: {', '.join(de.tried_tables)})"
        for de in reason.dead_ends
    ) if reason.dead_ends else "(없음)"

    # Fix section
    fix_text = ""
    if reason.sql_fix_instruction:
        fix_text = REASON_GENERATE_SQL_FIX.replace(
            "{fix_instruction}", reason.sql_fix_instruction,
        )

    prompt = REASON_GENERATE_SQL
    replacements = {
        "{original_query}": original_query or "",
        "{measures}": json.dumps(
            decomp.get("measures", []),
            ensure_ascii=False,
        ),
        "{filters}": json.dumps(
            decomp.get("filters", []),
            ensure_ascii=False,
        ),
        "{group_by}": json.dumps(
            decomp.get("group_by", []),
            ensure_ascii=False,
        ),
        "{order_limit}": json.dumps(
            decomp.get("order_limit", []),
            ensure_ascii=False,
        ),
        "{confirmed_terms}": confirmed_text,
        "{tables}": tables_text,
        "{join_path}": join_text,
        "{structural_hints}": hints_text,
        "{reference_sqls}": ref_text,
        "{dead_ends}": dead_text,
        "{fix_section}": fix_text,
        "{dialect}": dialect,
    }
    for key, value in replacements.items():
        prompt = prompt.replace(key, value)

    # 치환 변수 사전 (트래킹용, {} 제거한 키)
    variables = {
        k.strip("{}"): v for k, v in replacements.items()
    }
    return prompt, variables


async def _call_llm_for_sql(
    prompt: str,
    query: str,
) -> str:
    """LLM을 호출하여 SQL을 생성한다."""
    client = get_llm_client()
    llm_start = time.perf_counter()

    response = await client.messages.create(
        model=settings.llm_model,
        max_tokens=settings.llm_format_max_tokens,
        timeout=settings.llm_long_timeout,
        system=prompt,
        messages=[
            {"role": "user", "content": query},
        ],
    )

    llm_elapsed = (time.perf_counter() - llm_start) * 1000
    logger.info(
        "LLM 호출 완료",
        node="agentic_SQL생성",
        model=settings.llm_model,
        latency_ms=round(llm_elapsed, 1),
    )

    if not response.content:
        raise ValueError("SQL 생성 LLM 응답이 비어있음")

    raw = response.content[0].text

    # JSON 형식 응답에서 SQL 추출
    json_match = re.search(r"\{[\s\S]*\}", raw)
    if json_match:
        try:
            data = json.loads(json_match.group())
            sql = data.get("sql", "")
            if sql:
                return sql
        except json.JSONDecodeError:
            pass

    # 마크다운 코드 블록에서 SQL 추출
    return _clean_sql_response(raw)


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
