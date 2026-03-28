"""sql_validator 노드 — 3-레이어 SQL 검증.

Layer 1 (Rule-based): 문법/구조 검증 — 기존 sql_safety_checker 재사용
Layer 2 (LLM): 의미 검증 — query_decomposition 체크리스트 대조
Layer 3 (Execution): 실행 검증 — LIMIT 5로 실제 실행

실패 유형을 5단계로 분류하고 sql_fix_instruction을 생성한다.
"""

from __future__ import annotations

from typing import Any, Optional

from prototype.agentic_state import (
    AgenticCoreState,
    SqlValidationResult,
)


async def sql_validator_node(state: AgenticCoreState) -> dict:
    """생성된 SQL을 3레이어로 검증한다."""
    updates: dict[str, Any] = {"phase": "VALIDATING"}

    sql = state.generated_sql
    if not sql:
        updates["sql_validation_result"] = SqlValidationResult(
            overall="FAIL_SYNTAX",
        )
        updates["sql_fix_instruction"] = "SQL이 생성되지 않았습니다."
        return updates

    # ── Layer 1: Rule-based (문법/구조) ──
    layer1_result = await _validate_layer1(sql, state)
    if layer1_result["status"] == "FAIL":
        loop_guard = state.loop_guard.model_copy()
        loop_guard.increment_local_fix()
        updates["loop_guard"] = loop_guard
        updates["sql_validation_result"] = SqlValidationResult(
            layer1_status="FAIL",
            overall="FAIL_SYNTAX",
        )
        updates["sql_fix_instruction"] = layer1_result["feedback"]
        return updates

    # ── Layer 2: LLM 의미 검증 ──
    layer2_result = await _validate_layer2(sql, state)
    if layer2_result["status"] == "FAIL":
        loop_guard = state.loop_guard.model_copy()

        failure_type = layer2_result.get("failure_type", "semantic_local")
        if failure_type == "structural" or loop_guard.should_escalate_to_structural():
            overall = "FAIL_STRUCTURAL"
        else:
            overall = "FAIL_SEMANTIC_LOCAL"
            loop_guard.increment_local_fix()

        updates["loop_guard"] = loop_guard
        updates["sql_validation_result"] = SqlValidationResult(
            layer1_status="PASS",
            layer2_status="FAIL",
            layer2_passed=layer2_result.get("passed", []),
            layer2_failed=layer2_result.get("failed", []),
            layer2_failure_type=failure_type,
            overall=overall,
        )
        updates["sql_fix_instruction"] = layer2_result["feedback"]
        return updates

    # ── Layer 3: 실행 검증 ──
    layer3_result = await _validate_layer3(sql)
    if layer3_result["status"] == "FAIL":
        updates["sql_validation_result"] = SqlValidationResult(
            layer1_status="PASS",
            layer2_status="PASS",
            layer3_status="FAIL",
            layer3_row_count=layer3_result.get("row_count"),
            layer3_is_sane=False,
            overall=layer3_result.get("overall", "FAIL_EMPTY"),
        )
        updates["sql_fix_instruction"] = layer3_result["feedback"]
        return updates

    # ── 전체 통과 ──
    updates["validated_sql"] = sql
    updates["sql_validation_result"] = SqlValidationResult(
        layer1_status="PASS",
        layer2_status="PASS",
        layer3_status="PASS",
        layer3_row_count=layer3_result.get("row_count", 0),
        layer3_is_sane=True,
        overall="SUCCESS",
    )
    return updates


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Layer 1: Rule-based 검증
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _validate_layer1(
    sql: str, state: AgenticCoreState,
) -> dict[str, Any]:
    """Rule-based 문법/구조 검증.

    기존 sql_safety_checker.validate_sql_safety() 재사용 +
    sqlglot 파싱 + 테이블/컬럼 존재 확인.
    """
    errors: list[str] = []

    # 1. 기존 안전성 검증 (DML/DDL/시스템카탈로그)
    # from src.services.sql_safety_checker import validate_sql_safety
    # safety = validate_sql_safety(sql)
    # if not safety.is_safe:
    #     return {"status": "FAIL", "feedback": "; ".join(safety.errors)}

    # 2. sqlglot 파싱 가능 여부
    # from prototype.sql_hint_extractor import parse_sql_safe
    # ast = parse_sql_safe(sql)
    # if ast is None:
    #     return {"status": "FAIL", "feedback": "SQL 파싱 실패 — 문법 오류"}

    # 3. 사용된 테이블이 candidate_tables에 존재하는지
    # from prototype.sql_hint_extractor import get_real_tables
    # used_tables = get_real_tables(ast)
    # candidate_names = {ct.table_name for ct in state.candidate_tables}
    # unknown_tables = [t for t in used_tables if t not in candidate_names]
    # if unknown_tables:
    #     errors.append(f"미확인 테이블 사용: {', '.join(unknown_tables)}")

    if errors:
        return {"status": "FAIL", "feedback": "; ".join(errors)}
    return {"status": "PASS"}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Layer 2: LLM 의미 검증
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _validate_layer2(
    sql: str, state: AgenticCoreState,
) -> dict[str, Any]:
    """LLM 기반 의미 검증 — query_decomposition 체크리스트와 SQL 대조.

    체크리스트:
      □ measure가 SQL에 반영됐는가?
      □ 모든 filter 조건이 WHERE에 있는가?
      □ group_by 기준이 GROUP BY에 있는가?
      □ CONFIRMED되지 않은 값을 사용하지 않았는가?
      □ dead_ends에 기록된 실패 패턴을 반복하지 않았는가?

    TODO: 실제 구현 시 LLM 호출.
    """
    # 프로토타입에서는 PASS 반환
    return {
        "status": "PASS",
        "passed": ["measure_check", "filter_check", "group_by_check"],
        "failed": [],
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Layer 3: 실행 검증
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _validate_layer3(sql: str) -> dict[str, Any]:
    """LIMIT 5로 실제 실행하여 검증한다.

    TODO: 실제 구현 시 InfoDBConnector.execute_query() 호출.
    """
    # from src.connectors.manager import get_connector_manager
    # mgr = get_connector_manager()
    # limited_sql = f"SELECT * FROM ({sql}) _t LIMIT 5"
    # try:
    #     result = await mgr.info_db.execute_query(limited_sql)
    #     if result.row_count == 0:
    #         return {
    #             "status": "FAIL",
    #             "overall": "FAIL_EMPTY",
    #             "row_count": 0,
    #             "feedback": "실행 결과 0건 — 조건이 너무 좁거나 테이블이 부적절합니다",
    #         }
    #     return {"status": "PASS", "row_count": result.row_count}
    # except Exception as e:
    #     return {
    #         "status": "FAIL",
    #         "overall": "FAIL_DB_ERROR",
    #         "feedback": f"DB 실행 오류: {e}",
    #     }

    # 프로토타입: PASS 반환
    return {"status": "PASS", "row_count": 5}
