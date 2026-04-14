"""sql_generator 노드 — 누적 지식 기반 SQL 생성.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

CONFIRMED knowledge_items와 explored_tables를 기반으로 SQL을 생성한다.
재진입 시 failure_reason(이전 검증 피드백)을 프롬프트에 반드시 포함하고,
dead_ends를 참고하여 이전 실패 패턴을 반복하지 않는다.

멀티 DB 라우팅:
    readiness_gate 가 GENERATING 진입 시 reason.target_db 를 확정한다
    (단일 진실원). sql_generator 는 이 값을 그대로 신뢰하여 커넥터를 받고
    dialect 만 추출한다. cross-DB 감지/INFER 분기는 수행하지 않는다.

프롬프트 구성 ({슬롯} 치환 방식):
    - 기본: SQL_GENERATOR_SYSTEM (dialect 힌트 내장)
    - 재시도: SQL_GENERATOR_FIX_SECTION (failure_reason 포함)
    - 테이블 상세: _format_table_for_sql_prompt()로 컬럼·샘플·추론 정보 주입
    - confirmed_terms: CONFIRMED 지식 항목을 자연어로 정리
    - clarification_context: INFER 추론 결과 + ASK 질의응답 (resolved_signals)
    - current_date: 현재 날짜 (today_kst)

핵심 함수:
    - sql_generator_node: 메인 노드 함수
    - _build_agentic_prompt: 상태 → 프롬프트 치환 변수 조립
    - _call_llm_for_sql: LLM 호출 + SQL 추출
    - _format_table_for_sql_prompt: TableMeta → 프롬프트용 텍스트 변환

위임 구조:
    - 프롬프트: system_prompts.py의 SQL_GENERATOR_SYSTEM, SQL_GENERATOR_FIX_SECTION
    - 명확화 컨텍스트: clarification_context.py의 build_clarification_context
"""

from __future__ import annotations

import re
from typing import Any

from src.agents.models.clarification import (
    AmbiguitySignal,
    AmbiguityType,
    ConfidenceLevel,
)
from src.agents.state.state import (
    CodeMeta,
    ColumnInfo,
    FailureType,
    Phase,
    PipelineState,
    ReasoningState,
    SelectionStatus,
    TableMeta,
)
from src.connectors.manager import get_connector_manager
from src.agents.nodes.system_prompts import (
    SQL_GENERATOR_FIX_SECTION,
    get_sql_generator_system,
)
from src.config import settings
from src.utils.llm import llm_call_with_parse_retry
from src.utils.llm.response import extract_json
from src.utils.llm.prompt import render_prompt
from src.utils.logger import get_logger
from src.utils.sqlglot_analyzer import get_real_tables
from src.utils.timezone import today_kst
from src.utils.tracker import record_prompt_variables
from src.utils.tracker.dispatch import (
    dispatch_tracking_event,
    REASONING_STEP,
)
from src.utils.sql_formatter import format_sql_tabular

logger = get_logger(__name__)


def _format_table_for_sql_prompt(ct: TableMeta) -> str:
    """TableMeta을 SQL 생성 프롬프트용 텍스트로 포맷한다.

    컬럼별 한글명·타입·설명·PK 여부를 모두 전달하여
    SQL Generator가 정확한 컬럼 선택과 조인을 할 수 있도록 한다.
    """
    lines: list[str] = [_format_table_header(ct)]
    lines.extend(_format_columns(ct))
    lines.extend(_format_table_details(ct))
    return "\n".join(lines)


def _format_table_header(ct: TableMeta) -> str:
    """테이블 헤더 라인을 생성한다."""
    header = f"- {ct.qualified_name}"
    if ct.alt_name:
        header += f" ({ct.alt_name})"
    if ct.subject_area:
        header += f" [{ct.subject_area}]"
    if ct.description:
        header += f": {ct.description}"
    if ct.selection_status == SelectionStatus.REFERENCE:
        header += "\n  [참고] SQL 이력 해석용 테이블, SQL 생성에 사용하지 마세요."
    return header


def _format_column_line(c: ColumnInfo) -> str:
    """단일 ColumnInfo를 프롬프트 라인으로 포맷한다."""
    parts = [f"    {c.name}"]
    if c.alt_name:
        parts.append(f"({c.alt_name})")
    if c.col_type:
        parts.append(f" {c.col_type}")
    if c.is_pk:
        parts.append(" (PK)")
    if c.description:
        parts.append(f" — {c.description}")
    return "".join(parts)


def _format_columns(ct: TableMeta) -> list[str]:
    """컬럼 상세 라인 목록을 생성한다."""
    if not ct.columns:
        return []
    return ["  컬럼:"] + [_format_column_line(c) for c in ct.columns]


def _format_table_details(ct: TableMeta) -> list[str]:
    """관찰 도구 결과(날짜·샘플·컬럼값·통계) 라인 목록을 생성한다."""
    lines: list[str] = []

    # 날짜 컬럼 관찰 결과
    for odc in ct.observed_date_columns:
        parts = odc.date_range.split(" ~ ")
        min_val = parts[0] if parts else ""
        max_val = parts[-1] if parts else ""
        lines.append(
            f"  {odc.column_name} 일자컬럼정보: "
            f"MIN={min_val}, MAX={max_val}, PATTERN={odc.date_pattern}"
        )
    if not ct.observed_date_columns and ct.key_date_columns:
        for kdc in ct.key_date_columns:
            lines.append(f"  기준컬럼(PK): {kdc.column_name}")

    # 샘플 데이터
    if ct.sample_rows:
        lines.append(f"  샘플 데이터 ({len(ct.sample_rows)}행):")
        for row in ct.sample_rows[:3]:
            lines.append(f"    {row}")

    # 컬럼별 관찰 데이터 (discovered_values, column profile)
    if ct.columns:
        for c in ct.columns:
            col_obs = _format_column_observations(c)
            if col_obs:
                lines.extend(col_obs)

    return lines


def _format_column_observations(c: ColumnInfo) -> list[str]:
    """단일 컬럼의 관찰 데이터(값 목록, 통계)를 포맷한다."""
    lines: list[str] = []

    # 컬럼 값 검색 결과
    if c.discovered_values:
        total = len(c.discovered_values)
        display = c.discovered_values[:100]
        suffix = f" ... 외 {total - 100}건" if total > 100 else ""
        vals = ", ".join(str(v) for v in display)
        lines.append(
            f"  {c.name} 실제 값({total}건): {vals}{suffix}"
        )

    # 컬럼 통계
    if c.min_val is not None or c.max_val is not None:
        stats: list[str] = []
        if c.min_val is not None:
            stats.append(f"MIN={c.min_val}")
        if c.max_val is not None:
            stats.append(f"MAX={c.max_val}")
        if c.distinct_count is not None:
            stats.append(f"고유값={c.distinct_count:,}")
        if c.null_rate is not None:
            stats.append(f"NULL={c.null_rate:.1%}")
        lines.append(f"  {c.name} Profile: {', '.join(stats)}")

    return lines


def _format_codes_for_tables(
    tables: list[TableMeta],
    explored_codes: dict[str, CodeMeta],
) -> str:
    """SELECTED 테이블 컬럼과 관련된 코드값만 렌더링한다."""
    if not explored_codes:
        return "(없음)"

    # SELECTED 테이블의 전체 컬럼명 수집
    table_columns: set[str] = set()
    for ct in tables:
        for c in ct.columns:
            table_columns.add(c.name)

    # 관련 코드만 필터링
    lines: list[str] = []
    for col_name, code_meta in explored_codes.items():
        if col_name not in table_columns:
            continue
        if not code_meta.codes:
            continue
        desc = f" ({code_meta.column_desc})" if code_meta.column_desc else ""
        codes_str = ", ".join(
            f"{k}={v}" for k, v in code_meta.codes.items()
        )
        lines.append(f"- {col_name}{desc}: {codes_str}")

    return "\n".join(lines) if lines else "(없음)"


async def sql_generator_node(state: PipelineState) -> dict:
    """누적 지식을 컨텍스트로 SQL을 생성한다.

    CONFIRMED knowledge_items와 explored_tables(SELECTED)를 프롬프트에 주입한다.
    DB 라우팅은 readiness_gate 가 reason.target_db 에 이미 결정한 값을 신뢰하며
    cross-DB 감지/INFER 분기는 수행하지 않는다.
    재진입 시 failure_reason을 fix_section으로 포함하여 재시도한다.
    """
    reason = state.reason.model_copy(deep=True)
    reason.phase = Phase.GENERATING

    # 이전 validator 피드백이 있으면 fix_history에 누적 (재시도 시 전체 이력 표시)
    if reason.failure_reason:
        reason.fix_history.append(reason.failure_reason)

    reason.loop_guard = reason.loop_guard.model_copy()
    # generate_attempts는 SQL이 실제 생성된 경우에만 증가한다.
    # generator가 정보 부족으로 거부(fail)한 경우까지 카운터를 소비하면
    # validator의 local_fix 기회가 사라지는 문제가 있다. (20260407 trace 분석)

    # dialect 결정 — readiness_gate 가 결정한 reason.target_db 를 그대로 사용
    db = get_connector_manager().get_query_db(reason)
    dialect = db.dialect

    # agentic 전용 프롬프트 조립
    prompt, user_message, prompt_vars = _build_agentic_prompt(
        reason, state.preprocessed_input, dialect, state,
    )

    try:
        result = await _call_llm_for_sql(
            prompt, user_message,
        )
    except Exception as e:
        logger.error("SQL 생성 LLM 호출 오류", error=str(e))
        result = {
            "status": "fail",
            "sql": "",
            "reasoning_summary": "",
            "failure_reasons": [f"LLM 호출 오류: {type(e).__name__}"],
            "assumptions": [],
            "explanation": "",
        }

    # ── success / fail 분기 ──
    attempt = reason.loop_guard.generate_attempts
    table_names = [
        ct.qualified_name for ct in reason.explored_tables
    ]

    if result["status"] == "success" and result["sql"]:
        reason.loop_guard.increment_generate()
        reason.generated_sql = result["sql"]
        reason.sql_explanation = result.get("explanation", "")
        reason.failure_type = None
        reason.failure_reason = None
        reason.pending_assumptions = result.get("assumptions", [])
        _log_success_with_reasoning_check(
            result, dialect, attempt, table_names,
        )
    else:
        reason.generated_sql = None
        reason.pending_assumptions = []
        reason.failure_type = FailureType.GENERATION_FAILED
        reason.failure_reason = "\n".join(
            result.get("failure_reasons")
            or ["SQL 생성 실패 (사유 미제공)"],
        )
        reason.recovery_entry_source = "sql_generator"
        logger.warning(
            "SQL 생성 거부",
            dialect=dialect,
            attempt=attempt,
            reasons=result.get("failure_reasons", []),
        )

    await record_prompt_variables(prompt_vars)

    # ── reasoning flow 디스패치 ──
    _routing: dict[str, Any] = {
        "next_node": "sql_validator",
        "reason": "SQL 생성 완료 → 검증 진행",
    }
    if result["status"] != "success" or not result.get("sql"):
        _routing = {
            "next_node": "recovery_agent",
            "reason": "SQL 생성 실패 → recovery",
        }
    if attempt > 1:
        _routing["is_retry"] = True
        _routing["retry_count"] = attempt - 1

    await dispatch_tracking_event(REASONING_STEP, {
        "node": "sql_generator",
        "phase": "reason",
        "step_type": "llm_decision",
        "round": reason.loop_guard.replan_count,
        "hypothesis_id": (
            reason.current_hypothesis.hypothesis_id
            if reason.current_hypothesis else ""
        ),
        "inputs": {
            "tables": table_names,
            "confirmed_terms": [
                f"{ki.knowledge_id}: {ki.key} ({ki.status.value})"
                for ki in reason.knowledge_items
                if ki.status.value in ("CONFIRMED", "PROBABLE")
            ],
            "dead_ends": [
                f"`{de.failure_type.value}` {de.reason}"
                for de in reason.dead_ends
            ],
            "failure_reason": reason.failure_reason,
            "attempt": attempt,
        },
        "output": {
            "status": result["status"],
            "sql": (result.get("sql") or "")[:200],
            "explanation": result.get("explanation", ""),
            "assumptions": result.get("assumptions", []),
        },
        "routing": _routing,
    })

    return {"reason": reason}


def _build_agentic_prompt(
    reason: ReasoningState,
    original_query: str,
    dialect: str,
    state: PipelineState,
) -> tuple[str, str, dict[str, str]]:
    """SQL_GENERATOR_SYSTEM 템플릿에 상태를 주입한다.

    Returns:
        (치환된 시스템 프롬프트, LLM에 전달할 user 메시지, 치환 변수 사전) 튜플.
        재시도 시에는 user 메시지 끝에 직전 시도 피드백(fix_section)이 append된다.
    """
    decomp = reason.query_decomposition

    # 확인된 지식 항목
    confirmed_text = reason.format_confirmed_text()

    # 테이블 정보 (SELECTED + REFERENCE만 명시적으로 포함)
    active_tables = [
        ct for ct in reason.explored_tables
        if ct.selection_status in (
            SelectionStatus.SELECTED, SelectionStatus.REFERENCE,
        )
    ]
    tables_text = "\n".join(
        _format_table_for_sql_prompt(ct)
        for ct in active_tables
    ) if active_tables else "(후보 테이블 없음)"

    # SELECTED 테이블 컬럼과 관련된 코드값만 필터링
    codes_text = _format_codes_for_tables(
        active_tables, reason.explored_codes,
    )

    # 검증된 활용사례 SQL (관련 판정분만, reason 포함)
    relevant = [
        uc for uc in reason.explored_use_cases
        if uc.relevant
    ]
    ref_blocks: list[str] = []
    for i, uc in enumerate(relevant[:10], 1):
        if not uc.sql:
            continue
        lines = [f"[{i}]"]
        if uc.description:
            lines.append(f"- 설명: {uc.description}")
        if uc.eval_reason:
            lines.append(f"- 관련성: {uc.eval_reason}")
        lines.append(uc.sql)
        ref_blocks.append("\n".join(lines))
    ref_text = "\n\n".join(ref_blocks) if ref_blocks else "(없음)"

    # Dead-ends
    dead_text = reason.format_dead_ends_text()

    # Fix section (이전 검증 피드백이 있으면 재시도 프롬프트에 포함)
    fix_text = ""
    if reason.failure_reason:
        # fix_history에는 이전 시도들이 누적, failure_reason은 최신 피드백
        history_lines: list[str] = []
        if len(reason.fix_history) > 1:
            # fix_history의 마지막은 현재 failure_reason (진입 시 append됨)
            # 그 이전 항목들이 과거 시도 이력
            history_lines.append("### 이전 시도에서 실패한 접근 방식")
            for idx, prev in enumerate(reason.fix_history[:-1], 1):
                history_lines.append(f"[시도 {idx}] {prev}")
            history_lines.append("")
        history_lines.append("### 현재 문제점 (반드시 수정)")
        history_lines.append(reason.failure_reason)
        fix_history_section = "\n".join(history_lines)
        fix_text = SQL_GENERATOR_FIX_SECTION.replace(
            "{fix_history_section}", fix_history_section,
        )

    # 명확화 컨텍스트 (INFER 추론 + ASK Q&A)
    from src.agents.utils.clarification_context import (
        build_clarification_context,
    )
    clarification_text = build_clarification_context(state)

    prompt = get_sql_generator_system(dialect)
    nq = state.normalized_query
    rewritten = (
        getattr(nq, "rewritten_query", "")
        if nq else ""
    )

    expected_cols = decomp.get("output_hint", {}).get("expected_columns", [])
    expected_cols_text = ", ".join(expected_cols) if expected_cols else "(없음)"

    replacements = {
        "{current_date}": today_kst().isoformat(),
        "{original_query}": original_query or "",
        "{rewritten_query}": rewritten or original_query or "",
        "{expected_columns}": expected_cols_text,
        "{confirmed_terms}": confirmed_text,
        "{tables}": tables_text,
        "{codes}": codes_text,
        "{reference_sqls}": ref_text,
        "{dead_ends}": dead_text,
        "{clarification_context}": clarification_text or "(없음)",
    }
    prompt, variables = render_prompt(prompt, replacements)

    # 재시도 피드백은 user 메시지 끝에 append (system은 정적으로 유지 → 캐시 안정성)
    user_message = original_query or ""
    if fix_text:
        user_message = f"{user_message}\n\n{fix_text}" if user_message else fix_text
        variables["fix_section"] = fix_text

    return prompt, user_message, variables


_REASONING_TABLE_RE = re.compile(
    r"\b(TB_[A-Z]{3}_[A-Z0-9]{7})\b", re.IGNORECASE,
)


def _log_success_with_reasoning_check(
    result: dict,
    dialect: str,
    attempt: int,
    table_names: list[str],
) -> None:
    """SQL 생성 성공 시 reasoning_summary 교차 검증 + 로깅을 수행한다."""
    reasoning_summary = result.get("reasoning_summary", "")
    mismatches = _cross_check_reasoning_summary(
        reasoning_summary, result["sql"], dialect,
    )
    if mismatches:
        logger.warning(
            "reasoning_summary mismatch 감지",
            dialect=dialect,
            attempt=attempt,
            mismatches=mismatches,
            reasoning_summary=reasoning_summary,
        )
    logger.info(
        "SQL 생성 완료",
        dialect=dialect,
        attempt=attempt,
        tables=table_names,
        reasoning_summary=reasoning_summary,
        sql="\n" + format_sql_tabular(result["sql"]),
    )


def _cross_check_reasoning_summary(
    reasoning_summary: str,
    sql: str,
    dialect: str,
) -> list[str]:
    """reasoning_summary에 언급된 테이블이 실제 SQL에 등장하는지 교차 검증한다.

    thinking ON 노드의 think-answer mismatch 방어. reasoning_summary가
    사용했다고 선언한 테이블이 sql에 실제로 등장하지 않으면 mismatch 목록을
    반환한다. 코드값/컬럼 레벨 검증은 노이즈가 커서 테이블 레벨만 수행한다.

    Args:
        reasoning_summary: LLM이 출력한 판단 근거 요약.
        sql: 생성된 SELECT SQL.
        dialect: SQL dialect (tsql/hive/postgresql).

    Returns:
        mismatch 메시지 리스트. 비어있으면 정합.
    """
    if not reasoning_summary or not sql:
        return []

    sql_tables = {t.upper() for t in get_real_tables(sql, dialect)}
    if not sql_tables:
        return []

    # reasoning_summary에서 TB_XXX_XXXXXXX 패턴 추출
    mentioned = {
        m.group(1).upper()
        for m in _REASONING_TABLE_RE.finditer(reasoning_summary)
    }
    if not mentioned:
        return []

    missing = mentioned - sql_tables
    if not missing:
        return []
    return [
        f"reasoning_summary에 언급된 테이블이 SQL에 없음: {sorted(missing)}",
    ]


def _parse_sql_response(raw: str) -> dict:
    """LLM 응답에서 SQL 생성 결과를 추출한다.

    반환 형식: {"status", "sql", "reasoning_summary", "failure_reasons",
               "assumptions", "explanation"}
    JSON 형식 → 마크다운 코드 블록 fallback 순으로 시도.
    """
    data = extract_json(raw)
    if data and isinstance(data, dict):
        status = str(data.get("status", "success")).lower()
        if status not in ("success", "fail"):
            status = "fail"
        return {
            "status": status,
            "sql": data.get("sql", "").strip(),
            "reasoning_summary": data.get("reasoning_summary", ""),
            "failure_reasons": data.get(
                "failure_reasons", data.get("reasons", []),
            ),
            "assumptions": data.get("assumptions", []),
            "explanation": data.get("explanation", ""),
        }

    # JSON 추출 실패 시: 코드 블록에서 SQL 추출 (기존 호환)
    cleaned = _clean_sql_response(raw)
    if cleaned:
        return {
            "status": "success",
            "sql": cleaned,
            "reasoning_summary": "",
            "failure_reasons": [],
            "assumptions": [],
            "explanation": "",
        }

    raise ValueError(
        "SQL을 추출할 수 없음: JSON 파싱 실패, 코드 블록 없음",
    )


async def _call_llm_for_sql(
    prompt: str,
    query: str,
) -> dict:
    """LLM을 호출하여 SQL 생성 결과를 반환한다."""
    _, result = await llm_call_with_parse_retry(
        system=prompt,
        messages=[{"role": "user", "content": query}],
        parse_fn=_parse_sql_response,
        max_tokens=settings.llm_format_max_tokens,
        timeout=settings.llm_long_timeout,
        node_name="agentic_SQL생성",
    )
    return result


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


def _build_assumption_signals(
    assumptions: list[str],
    turn_id: str | None,
) -> list[AmbiguitySignal]:
    """SQL 생성 시 assumptions를 INFER AmbiguitySignal로 변환한다."""
    if not assumptions:
        return []
    signals: list[AmbiguitySignal] = []
    for text in assumptions:
        if "→" in text:
            q, v = text.split("→", 1)
            question = q.strip()
            inferred = v.strip()
        elif "->" in text:
            q, v = text.split("->", 1)
            question = q.strip()
            inferred = v.strip()
        else:
            question = text
            inferred = text
        signals.append(AmbiguitySignal(
            source_node="sql_generator",
            decision="INFER",
            ambiguity_type=AmbiguityType.INTENT,
            confidence=ConfidenceLevel.MEDIUM,
            question=question,
            question_type="confirm",
            inferred_value=inferred,
            reasoning="SQL 생성 시 해석적 선택",
            turn_id=turn_id,
        ))
    return signals
