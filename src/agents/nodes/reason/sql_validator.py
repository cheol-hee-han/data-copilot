"""sql_validator 노드 — 3-레이어 SQL 검증 (dialect 인식).

Layer 1 (Rule-based): 안전성 + sqlglot 파싱(dialect별) + 테이블/컬럼 존재
Layer 2a (Rule-based): 구조적 sanity check (C-22, dialect 무관)
Layer 3 (Execution): db_source에 맞는 커넥터로 실행 검증
Layer 2b (LLM): 의미 검증 + DB 에러 분류 — query_decomposition 체크리스트 대조

실패 시 failure_type(FailureType)과 failure_reason(상세 피드백)을
state에 직접 설정하여, pipeline 라우팅·sql_generator 재시도·recovery_planner
DeadEnd 생성까지 단일 필드로 통합 전달한다.

v2.0 (2026-03-25): Layer 2b LLM 구현 — 외부 프롬프트 사용.
v2.1 (2026-03-29): SqlValidationResult 제거 → failure_type/failure_reason 통합.
v3.0 (2026-04-03): Layer 순서 변경 (L3→L2b), DB 에러 LLM 분류, failure_classification.
"""

from __future__ import annotations

from typing import Any

from src.agents.state.state import (
    FailureType,
    Phase,
    PipelineState,
    ReasoningState,
    TableSelectionStatus,
)
from src.connectors.manager import get_connector_manager
from src.agents.nodes.system_prompts import (
    SQL_VALIDATOR_SYSTEM,
)
from src.agents.nodes.reason.sql_generator import (
    _format_table_for_sql_prompt,
)
from src.services.sql_safety_checker import validate_sql_safety
from src.utils.sqlglot_analyzer import (
    parse_sql_safe,
    get_real_tables,
    get_real_columns,
)
from src.config import settings
from src.utils.logger import get_logger
from src.utils.tracker import record_prompt_variables
from src.utils.truncate import truncate_log

logger = get_logger(__name__)


async def sql_validator_node(state: PipelineState) -> dict:
    """생성된 SQL을 3레이어로 검증한다.

    검증 순서: Layer1(룰) → Layer2a(룰) → Layer3(DB실행) → Layer2b(LLM).
    Layer3를 Layer2b 앞에 배치하여 DB 에러를 LLM이 분류할 수 있도록 한다.
    """
    reason = state.reason.model_copy(deep=True)
    reason.phase = Phase.VALIDATING

    sql = reason.generated_sql
    if not sql:
        reason.failure_type = FailureType.SQL_SYNTAX
        reason.failure_reason = "SQL이 생성되지 않았습니다."
        logger.warning("SQL 검증: 빈 SQL")
        return {"reason": reason}

    # dialect 결정 (커넥터에서 직접 가져옴)
    dialect = get_connector_manager().get_query_db(reason).dialect

    # Layer 1: Rule-based (문법/구조, dialect 인식)
    layer1_result = _validate_layer1(sql, reason, dialect)
    if layer1_result["status"] == "FAIL":
        reason.loop_guard = reason.loop_guard.model_copy()
        reason.loop_guard.increment_local_fix()
        reason.failure_type = FailureType.SQL_SYNTAX
        reason.failure_reason = layer1_result["feedback"]
        logger.warning(
            "SQL 검증 실패: Layer1(구문)",
            feedback=truncate_log(layer1_result["feedback"]),
        )
        return {"reason": reason}

    # Layer 2a: 구조적 sanity check (dialect 무관)
    layer2a_result = _validate_layer2a(sql, reason)
    if layer2a_result["status"] == "FAIL":
        logger.warning(
            "SQL 검증 실패: Layer2a(구조)",
            failed=layer2a_result.get("failed", []),
        )
        return _build_layer2a_failure(
            reason,
            layer2a_result,
        )

    # Layer 3: 실행 검증 (db_source 기반 커넥터 라우팅)
    # Layer 2b 이전에 실행하여 DB 에러를 LLM에게 전달한다.
    layer3_result = await _validate_layer3(sql, reason)

    # Layer 2b: LLM 의미 검증 + DB 에러 분류
    if settings.validate_layer2b_enabled:
        layer2b_result = await _validate_layer2b(
            sql,
            reason,
            state.preprocessed_input,
            layer3_result,
        )
        if layer2b_result["status"] == "FAIL":
            logger.warning(
                "SQL 검증 실패: Layer2b(의미+DB에러)",
                failed=layer2b_result.get("failed", []),
                classification=layer2b_result.get(
                    "failure_classification", "",
                ),
            )
            return _build_layer2b_failure(
                reason,
                layer2b_result,
            )
        # PASS 시 체크 항목별 판정 사유를 보존 (통찰 패널에서 사용)
        reason.validation_checks = layer2b_result.get("checks", {})

        # 안전장치: Layer2b가 PASS인데 Layer3가 FAIL이면
        # LLM이 DB 에러를 간과한 것이므로 Layer3 실패를 반영한다.
        if layer3_result["status"] == "FAIL":
            layer3_failure = layer3_result.get(
                "failure_type", FailureType.EMPTY_RESULT,
            )
            reason.failure_type = layer3_failure
            reason.failure_reason = layer3_result["feedback"]
            logger.warning(
                "SQL 검증 실패: Layer3(실행, Layer2b PASS이나 DB 에러)",
                failure_type=layer3_failure,
            )
            return {"reason": reason}

    else:
        # Layer2b 비활성 시: Layer3 결과만으로 판정
        if layer3_result["status"] == "FAIL":
            layer3_failure: FailureType = layer3_result.get(
                "failure_type", FailureType.EMPTY_RESULT,
            )
            reason.failure_type = layer3_failure
            reason.failure_reason = layer3_result["feedback"]
            logger.warning(
                "SQL 검증 실패: Layer3(실행, Layer2b 비활성)",
                failure_type=layer3_failure,
                feedback=truncate_log(layer3_result.get("feedback", "")),
            )
            return {"reason": reason}

    # 전체 통과
    reason.validated_sql = sql
    reason.failure_type = None
    reason.failure_reason = None
    logger.info(
        "SQL 검증 통과",
        row_count=layer3_result.get("row_count", 0),
    )
    return {"reason": reason}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 실패 결과 구성
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _build_layer2a_failure(
    reason: ReasoningState,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Layer 2a 실패 시 업데이트를 구성한다."""
    loop_guard = reason.loop_guard.model_copy()
    if loop_guard.should_escalate_to_structural():
        reason.failure_type = FailureType.SQL_STRUCTURAL
    else:
        reason.failure_type = FailureType.SQL_SEMANTIC_LOCAL
        loop_guard.increment_local_fix()

    reason.loop_guard = loop_guard
    reason.failure_reason = result.get("feedback", "")
    return {"reason": reason}


def _build_layer2b_failure(
    reason: ReasoningState,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Layer 2b 실패 시 LLM의 failure_classification을 사용하여 구성한다."""
    loop_guard = reason.loop_guard.model_copy()
    classification = result.get("failure_classification", "structural")

    if (
        classification == "local_fix"
        and not loop_guard.should_escalate_to_structural()
    ):
        reason.failure_type = FailureType.SQL_SEMANTIC_LOCAL
        loop_guard.increment_local_fix()
    else:
        reason.failure_type = FailureType.SQL_STRUCTURAL

    reason.loop_guard = loop_guard
    reason.failure_reason = result.get("feedback", "")
    return {"reason": reason}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Layer 1: Rule-based (dialect 인식)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _validate_layer1(
    sql: str,
    reason: ReasoningState,
    dialect: str = "tsql",
) -> dict[str, Any]:
    """Rule-based 문법/구조 검증 (dialect 인식)."""
    # 1. 공통 안전성 검증 (DML/DDL/시스템카탈로그)
    safety = validate_sql_safety(sql, dialect)
    if not safety.is_safe:
        return {
            "status": "FAIL",
            "feedback": "; ".join(safety.errors),
        }

    # 2. dialect별 sqlglot 파싱
    ast = parse_sql_safe(sql, dialect=dialect)
    if ast is None:
        return {
            "status": "FAIL",
            "feedback": (f"SQL 파싱 실패 ({dialect} 문법 오류)"),
        }

    # 3. 사용된 테이블이 candidate_tables에 존재하는지
    errors: list[str] = []
    used_tables = get_real_tables(ast)
    active_tables = [
        ct for ct in reason.candidate_tables
        if ct.selection_status != TableSelectionStatus.REJECTED
    ]
    candidate_names = {ct.table_name.upper() for ct in active_tables}
    qualified_names = [ct.qualified_name for ct in active_tables]
    if candidate_names:
        unknown = [t for t in used_tables if t.upper() not in candidate_names]
        if unknown:
            errors.append(
                f"미확인 테이블: {', '.join(unknown)} "
                f"— 사용할 테이블 목록에 없는 테이블입니다. "
                f"확인된 테이블만 사용하세요: "
                f"{', '.join(sorted(qualified_names))}",
            )

    # 4. 사용된 컬럼이 candidate_tables의 컬럼 범위 안인지
    if active_tables and not errors:
        allowed_columns = set()
        for ct in active_tables:
            allowed_columns.update(c.name.upper() for c in ct.columns)
        if allowed_columns:
            used_columns = get_real_columns(ast)
            unknown_cols = [c for c in used_columns if c.upper() not in allowed_columns]
            if unknown_cols:
                errors.append(
                    f"미확인 컬럼: {', '.join(unknown_cols)} "
                    f"— 후보 테이블의 컬럼 목록에 없습니다. "
                    f"확인된 컬럼만 사용하세요.",
                )

    if errors:
        return {
            "status": "FAIL",
            "feedback": "; ".join(errors),
        }
    return {"status": "PASS"}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Layer 2a: 구조적 sanity check (dialect 무관)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _validate_layer2a(
    sql: str,
    reason: ReasoningState,
) -> dict[str, Any]:
    """구조적 sanity check — 의미적 매칭은 하지 않음."""
    decomp = reason.query_decomposition
    failed: list[str] = []

    sql_upper = sql.upper()

    has_grouping = (
        "GROUP BY" in sql_upper
        or "PARTITION BY" in sql_upper
        or "DISTINCT" in sql_upper
    )
    if decomp.get("group_by") and not has_grouping:
        failed.append(
            "query_decomposition에 group_by가 있는데 "
            "SQL에 GROUP BY/PARTITION BY/DISTINCT가 없음",
        )

    agg_keywords = [
        "COUNT(",
        "SUM(",
        "AVG(",
        "MIN(",
        "MAX(",
    ]
    has_agg = any(kw in sql_upper for kw in agg_keywords)
    measures = decomp.get("measures", [])
    has_agg_in_decomp = any(m.get("agg_function") for m in measures)
    if has_agg_in_decomp and not has_agg:
        failed.append(
            "query_decomposition에 집계함수가 있는데 " "SQL에 집계함수가 없음",
        )

    if failed:
        return {
            "status": "FAIL",
            "failure_type": "semantic_local",
            "failed": failed,
            "feedback": "; ".join(failed),
        }
    return {"status": "PASS"}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Layer 2b: LLM 의미 검증 + DB 에러 분류
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _format_table_schema(reason: ReasoningState) -> str:
    """validator 프롬프트용 테이블 스키마 텍스트를 생성한다.

    sql_generator의 _format_table_for_sql_prompt를 재사용하여
    컬럼 타입·PK·설명 등을 동일한 포맷으로 전달한다.
    """
    active_tables = [
        ct for ct in reason.candidate_tables
        if ct.selection_status != TableSelectionStatus.REJECTED
    ]
    if not active_tables:
        return "(테이블 정보 없음)"
    return "\n".join(
        _format_table_for_sql_prompt(ct)
        for ct in active_tables
    )


def _format_db_execution_result(layer3_result: dict[str, Any]) -> str:
    """Layer 3 실행 결과를 프롬프트용 문자열로 변환한다."""
    if layer3_result["status"] == "PASS":
        row_count = layer3_result.get("row_count", 0)
        return f"PASS ({row_count}건 반환)"
    return f"FAIL: {layer3_result.get('feedback', '알 수 없는 오류')}"


async def _validate_layer2b(
    sql: str,
    reason: ReasoningState,
    original_query: str,
    layer3_result: dict[str, Any],
) -> dict[str, Any]:
    """LLM 기반 의미 검증 + DB 에러 분류 — 8개 체크리스트 대조.

    Layer 3(DB 실행) 결과를 포함하여 LLM이 의미 검증과
    DB 에러 분류를 통합적으로 수행한다.

    retry 후에도 실패하면 FAIL + structural로 처리하여
    검증되지 않은 SQL이 실행되지 않도록 한다.
    """
    from src.utils.llm import llm_call_with_parse_retry, ParseError
    from src.utils.llm.response import extract_json
    from src.utils.llm.prompt import (
        render_prompt,
        serialize_decomp_slots,
    )

    decomp = reason.query_decomposition

    # 확인된 지식 항목 / Dead-ends / 테이블 스키마 / DB 실행 결과
    confirmed_text = reason.format_confirmed_text()
    dead_text = reason.format_dead_ends_text()
    table_schema_text = _format_table_schema(reason)
    db_result_text = _format_db_execution_result(layer3_result)

    template = SQL_VALIDATOR_SYSTEM
    replacements = {
        "{original_query}": original_query or "",
        **serialize_decomp_slots(decomp),
        "{generated_sql}": sql,
        "{table_schema}": table_schema_text,
        "{confirmed_terms}": confirmed_text,
        "{dead_ends}": dead_text,
        "{db_execution_result}": db_result_text,
    }
    prompt, prompt_vars = render_prompt(template, replacements)

    def _parse_fn(raw_text: str) -> dict[str, Any]:
        data = extract_json(raw_text)
        if not data:
            raise ValueError("Layer2b JSON 파싱 실패")
        return data

    try:
        _, data = await llm_call_with_parse_retry(
            system=prompt,
            messages=[
                {
                    "role": "user",
                    "content": "SQL을 검증하세요.",
                },
            ],
            parse_fn=_parse_fn,
            max_tokens=1024,
            timeout=settings.llm_default_timeout,
            node_name="sql_validator_layer2b",
        )

        await record_prompt_variables(prompt_vars)
        verdict = data.get("verdict", "PASS")
        checks = data.get("checks", {})

        passed = [k for k, v in checks.items() if v.get("pass")]
        failed = [
            f"{k}: {v.get('detail', '')}"
            for k, v in checks.items()
            if not v.get("pass")
        ]
        fix = data.get("fix_instruction", "")
        classification = data.get(
            "failure_classification", "structural",
        )

        if verdict == "FAIL" and failed:
            return {
                "status": "FAIL",
                "passed": passed,
                "failed": failed,
                "feedback": fix or "; ".join(failed),
                "failure_classification": classification,
            }

        return {
            "status": "PASS",
            "passed": passed,
            "failed": [],
            "checks": checks,
        }

    except (ParseError, Exception) as e:
        logger.warning(
            "Layer2b LLM 최종 실패, FAIL 처리",
            error=str(e),
        )
        return {
            "status": "FAIL",
            "passed": [],
            "failed": ["LLM 의미 검증 불가"],
            "feedback": "LLM 의미 검증에 실패하여 SQL을 확정할 수 없습니다",
            "failure_classification": "structural",
        }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Layer 3: 실행 검증 (db_source 기반 커넥터 라우팅)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


async def _validate_layer3(
    sql: str,
    reason: ReasoningState,
) -> dict[str, Any]:
    """db_source에 맞는 커넥터로 LIMIT 5 실행 검증."""
    mgr = get_connector_manager()
    db = mgr.get_query_db(reason)

    # dialect별 행 제한 문법
    if db.dialect == "tsql":
        limited_sql = f"SELECT TOP 5 * FROM ({sql}) _t"
    else:
        limited_sql = f"SELECT * FROM ({sql}) _t LIMIT 5"

    try:
        result = await db.execute_query(limited_sql)
        if isinstance(result, list):
            row_count = len(result)
        else:
            row_count = getattr(result, "row_count", 0)
        if row_count == 0:
            return {
                "status": "FAIL",
                "failure_type": FailureType.EMPTY_RESULT,
                "row_count": 0,
                "feedback": (
                    "실행 결과 0건 — 조건이 너무 좁거나 " "테이블이 부적절합니다"
                ),
            }
        return {"status": "PASS", "row_count": row_count}
    except Exception as e:
        return {
            "status": "FAIL",
            "failure_type": FailureType.DB_ERROR,
            "feedback": f"DB 실행 오류: {e}",
        }
