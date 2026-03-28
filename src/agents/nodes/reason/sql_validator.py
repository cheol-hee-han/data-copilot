"""sql_validator 노드 — 3-레이어 SQL 검증 (dialect 인식).

Layer 1 (Rule-based): 안전성 + sqlglot 파싱(dialect별) + 테이블/컬럼 존재
Layer 2a (Rule-based): 구조적 sanity check (C-22, dialect 무관)
Layer 2b (LLM): 의미 검증 — query_decomposition 체크리스트 대조
Layer 3 (Execution): db_source에 맞는 커넥터로 실행 검증

실패 유형을 5단계로 분류하고 sql_fix_instruction을 생성한다.

v2.0 (2026-03-25): Layer 2b LLM 구현 — 외부 프롬프트 사용.
"""

from __future__ import annotations

from typing import Any

from src.agents.state.state import (
    PipelineState,
    ReasoningState,
    SqlValidationResult,
)
from src.agents.nodes.reason.sql_generator import determine_dialect
from src.agents.nodes.system_prompts import (
    REASON_VALIDATE_LAYER2B,
)
from src.services.sql_safety_checker import validate_sql_safety
from src.services.sql_hint_extractor import (
    parse_sql_safe,
    get_real_tables,
    get_real_columns,
)
from src.config import settings
from src.utils.logger import get_logger
from src.utils.tracker import record_prompt_variables

logger = get_logger(__name__)


async def sql_validator_node(state: PipelineState) -> dict:
    """생성된 SQL을 3레이어로 검증한다."""
    reason = state.reason.model_copy(deep=True)
    reason.phase = "VALIDATING"

    sql = reason.generated_sql
    if not sql:
        reason.sql_validation_result = SqlValidationResult(
            overall="FAIL_SYNTAX",
        )
        reason.sql_fix_instruction = (
            "SQL이 생성되지 않았습니다."
        )
        logger.warning("SQL 검증: 빈 SQL")
        return {"reason": reason}

    # dialect 결정 (sql_generator와 동일 로직 공유)
    dialect = determine_dialect(reason)

    # Layer 1: Rule-based (문법/구조, dialect 인식)
    layer1_result = _validate_layer1(sql, reason, dialect)
    if layer1_result["status"] == "FAIL":
        reason.loop_guard = reason.loop_guard.model_copy()
        reason.loop_guard.increment_local_fix()
        reason.sql_validation_result = SqlValidationResult(
            layer1_status="FAIL",
            overall="FAIL_SYNTAX",
        )
        reason.sql_fix_instruction = (
            layer1_result["feedback"]
        )
        logger.warning(
            "SQL 검증 실패: Layer1(구문)",
            feedback=layer1_result["feedback"][:200],
        )
        return {"reason": reason}

    # Layer 2a: 구조적 sanity check (dialect 무관)
    layer2a_result = _validate_layer2a(sql, reason)
    if layer2a_result["status"] == "FAIL":
        logger.warning(
            "SQL 검증 실패: Layer2a(구조)",
            failed=layer2a_result.get("failed", []),
        )
        return _build_layer2_failure(
            reason, layer2a_result,
        )

    # Layer 2b: LLM 의미 검증 (대형 모델 환경에서만)
    if settings.validate_layer2b_enabled:
        layer2b_result = await _validate_layer2b(
            sql, reason, state.preprocessed_input,
        )
        if layer2b_result["status"] == "FAIL":
            logger.warning(
                "SQL 검증 실패: Layer2b(의미)",
                failed=layer2b_result.get("failed", []),
            )
            return _build_layer2_failure(
                reason, layer2b_result,
            )

    # Layer 3: 실행 검증 (db_source 기반 커넥터 라우팅)
    layer3_result = await _validate_layer3(
        sql, reason, dialect,
    )
    if layer3_result["status"] == "FAIL":
        reason.sql_validation_result = SqlValidationResult(
            layer1_status="PASS",
            layer2_status="PASS",
            layer3_status="FAIL",
            layer3_row_count=layer3_result.get("row_count"),
            layer3_is_sane=False,
            overall=layer3_result.get(
                "overall", "FAIL_EMPTY",
            ),
        )
        reason.sql_fix_instruction = (
            layer3_result["feedback"]
        )
        logger.warning(
            "SQL 검증 실패: Layer3(실행)",
            overall=layer3_result.get("overall"),
            feedback=layer3_result.get("feedback", "")[:200],
        )
        return {"reason": reason}

    # 전체 통과
    reason.validated_sql = sql
    reason.sql_validation_result = SqlValidationResult(
        layer1_status="PASS",
        layer2_status="PASS",
        layer3_status="PASS",
        layer3_row_count=layer3_result.get("row_count", 0),
        layer3_is_sane=True,
        overall="SUCCESS",
    )
    logger.info(
        "SQL 검증 통과",
        row_count=layer3_result.get("row_count", 0),
    )
    return {"reason": reason}


def _build_layer2_failure(
    reason: ReasoningState,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Layer 2 (a/b) 실패 시 공통 업데이트를 구성한다."""
    loop_guard = reason.loop_guard.model_copy()
    failure_type = result.get(
        "failure_type", "semantic_local",
    )
    if (
        failure_type == "structural"
        or loop_guard.should_escalate_to_structural()
    ):
        overall = "FAIL_STRUCTURAL"
    else:
        overall = "FAIL_SEMANTIC_LOCAL"
        loop_guard.increment_local_fix()

    reason.loop_guard = loop_guard
    reason.sql_validation_result = SqlValidationResult(
        layer1_status="PASS",
        layer2_status="FAIL",
        layer2_passed=result.get("passed", []),
        layer2_failed=result.get("failed", []),
        layer2_failure_type=failure_type,
        overall=overall,
    )
    reason.sql_fix_instruction = result.get("feedback", "")
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
    safety = validate_sql_safety(sql)
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
            "feedback": (
                f"SQL 파싱 실패 ({dialect} 문법 오류)"
            ),
        }

    # 3. 사용된 테이블이 candidate_tables에 존재하는지
    errors: list[str] = []
    used_tables = get_real_tables(ast)
    candidate_names = {
        ct.table_name.upper()
        for ct in reason.candidate_tables
    }
    qualified_names = [
        ct.qualified_name
        for ct in reason.candidate_tables
    ]
    if candidate_names:
        unknown = [
            t for t in used_tables
            if t.upper() not in candidate_names
        ]
        if unknown:
            errors.append(
                f"미확인 테이블: {', '.join(unknown)} "
                f"— 사용할 테이블 목록에 없는 테이블입니다. "
                f"확인된 테이블만 사용하세요: "
                f"{', '.join(sorted(qualified_names))}",
            )

    # 4. 사용된 컬럼이 candidate_tables의 컬럼 범위 안인지
    if reason.candidate_tables and not errors:
        allowed_columns = set()
        for ct in reason.candidate_tables:
            allowed_columns.update(
                c.upper() for c in ct.relevant_columns
            )
        if allowed_columns:
            used_columns = get_real_columns(ast)
            unknown_cols = [
                c for c in used_columns
                if c.upper() not in allowed_columns
            ]
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

    if decomp.get("group_by") and "GROUP BY" not in sql_upper:
        failed.append(
            "query_decomposition에 group_by가 있는데 "
            "SQL에 GROUP BY 절이 없음",
        )

    agg_keywords = [
        "COUNT(", "SUM(", "AVG(", "MIN(", "MAX(",
    ]
    has_agg = any(kw in sql_upper for kw in agg_keywords)
    measures = decomp.get("measures", [])
    has_agg_in_decomp = any(
        m.get("agg_function") for m in measures
    )
    if has_agg_in_decomp and not has_agg:
        failed.append(
            "query_decomposition에 집계함수가 있는데 "
            "SQL에 집계함수가 없음",
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
# Layer 2b: LLM 의미 검증
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _validate_layer2b(
    sql: str,
    reason: ReasoningState,
    original_query: str,
) -> dict[str, Any]:
    """LLM 기반 의미 검증 — 7개 체크리스트 대조."""
    import json
    import re

    from src.utils.llm import get_llm_client

    decomp = reason.query_decomposition

    # 확인된 지식 항목
    confirmed = reason.get_confirmed_knowledge()
    confirmed_text = "\n".join(
        f"- {ki.key}: {ki.value} ({ki.source})"
        for ki in confirmed
    ) if confirmed else "(없음)"

    # Dead-ends
    dead_text = "\n".join(
        f"- [{de.failure_type}] {de.reason}"
        for de in reason.dead_ends
    ) if reason.dead_ends else "(없음)"

    prompt = REASON_VALIDATE_LAYER2B
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
        "{generated_sql}": sql,
        "{confirmed_terms}": confirmed_text,
        "{dead_ends}": dead_text,
    }
    for key, value in replacements.items():
        prompt = prompt.replace(key, value)

    try:
        client = get_llm_client()
        response = await client.messages.create(
            model=settings.llm_model,
            max_tokens=1024,
            timeout=settings.llm_default_timeout,
            system=prompt,
            messages=[
                {
                    "role": "user",
                    "content": "SQL을 검증하세요.",
                },
            ],
        )

        record_prompt_variables({
            k.strip("{}"): v for k, v in replacements.items()
        })
        raw = response.content[0].text
        json_match = re.search(r"\{[\s\S]*\}", raw)
        if not json_match:
            logger.warning(
                "Layer2b JSON 파싱 실패, PASS 처리",
            )
            return {
                "status": "PASS",
                "passed": [],
                "failed": [],
            }

        data = json.loads(json_match.group())
        verdict = data.get("verdict", "PASS")
        checks = data.get("checks", {})

        passed = [
            k for k, v in checks.items() if v.get("pass")
        ]
        failed = [
            f"{k}: {v.get('detail', '')}"
            for k, v in checks.items()
            if not v.get("pass")
        ]
        fix = data.get("fix_instruction", "")

        if verdict == "FAIL" and failed:
            # 구조적 실패 여부 판별
            structural_checks = {
                "no_unconfirmed_values",
                "no_dead_end_repeat",
            }
            structural_failed = any(
                k in structural_checks
                for k, v in checks.items()
                if not v.get("pass")
            )
            return {
                "status": "FAIL",
                "passed": passed,
                "failed": failed,
                "feedback": fix or "; ".join(failed),
                "failure_type": (
                    "structural"
                    if structural_failed
                    else "semantic_local"
                ),
            }

        return {
            "status": "PASS",
            "passed": passed,
            "failed": [],
        }

    except Exception as e:
        logger.warning(
            "Layer2b LLM 호출 실패, PASS 처리",
            error=str(e),
        )
        return {"status": "PASS", "passed": [], "failed": []}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Layer 3: 실행 검증 (db_source 기반 커넥터 라우팅)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _validate_layer3(
    sql: str,
    reason: ReasoningState,
    dialect: str = "tsql",
) -> dict[str, Any]:
    """db_source에 맞는 커넥터로 LIMIT 5 실행 검증."""
    from src.connectors.manager import get_connector_manager

    # dialect별 행 제한 문법
    if dialect == "tsql":
        # Sybase IQ: SELECT TOP 5 * FROM (...)
        limited_sql = f"SELECT TOP 5 * FROM ({sql}) _t"
    else:
        # Impala/Hive/PostgreSQL: LIMIT
        limited_sql = (
            f"SELECT * FROM ({sql}) _t LIMIT 5"
        )

    mgr = get_connector_manager()

    # db_source에 따라 올바른 커넥터 선택
    db = mgr.get_query_db(reason)

    try:
        result = await db.execute_query(limited_sql)
        if isinstance(result, list):
            row_count = len(result)
        else:
            row_count = getattr(result, "row_count", 0)
        if row_count == 0:
            return {
                "status": "FAIL",
                "overall": "FAIL_EMPTY",
                "row_count": 0,
                "feedback": (
                    "실행 결과 0건 — 조건이 너무 좁거나 "
                    "테이블이 부적절합니다"
                ),
            }
        return {"status": "PASS", "row_count": row_count}
    except Exception as e:
        return {
            "status": "FAIL",
            "overall": "FAIL_DB_ERROR",
            "feedback": f"DB 실행 오류: {e}",
        }
