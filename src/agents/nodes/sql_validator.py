"""SQL 검증 노드 — 안전성 검증 및 테이블 적절성 판정.

생성된 SQL 을 2단계로 검증한다.
1단계(안전성): DML/DDL 차단, 시스템 카탈로그 접근 차단, 다중 쿼리 차단 등
보안 규칙을 점검하여 위반 시 validation_feedback 과 함께 재생성을 유도한다.
2단계(테이블 적절성): SQL 에 사용된 테이블이 컨텍스트에서 제공된 후보 테이블과
일치하는지 검증하며, PASS / WARNING / AMBIGUOUS 세 가지 판정을 내린다.

핵심 함수:
    - validate_sql_node: state.generated_sql, state.context.table_metas,
      state.preprocessed_input 을 읽어 검증하고 state.validated_sql,
      state.sql_validation_errors, state.validation_feedback,
      state.table_selection_verdict 에 기록

위임 구조:
    - 안전성 검증: services/sql_safety_checker.py (validate_sql_safety)
    - 테이블 적절성: services/similar_table_resolver.py (validate_table_selection)

재시도/분기:
    - 안전성 실패 또는 WARNING 판정 시 status=SQL_GENERATED 를 반환하여
      generate_sql → validate_sql 재시도 루프를 트리거한다.
    - AMBIGUOUS 판정 시 clarification_question 을 설정하여 명확화 흐름으로 분기한다.
    - PASS 판정 시 validated_sql 에 최종 SQL 을 기록하고 다음 노드로 진행한다.
"""

from __future__ import annotations

from src.agents.state.state import (
    PipelineState,
    QueryStatus,
    add_trace,
)
from src.services.similar_table_resolver import (
    TableVerdict,
    validate_table_selection,
)
from src.services.sql_safety_checker import validate_sql_safety
from src.utils.logger import get_logger

logger = get_logger(__name__)


def validate_sql_node(state: PipelineState) -> dict:
    """생성된 SQL을 검증한다."""
    logger.info(
        "SQL 검증 시작",
        retry_count=state.sql_retry_count,
    )

    # 1. 안전성 검증
    safety = validate_sql_safety(state.generated_sql)

    if not safety.is_safe:
        logger.warning(
            "SQL 검증 실패",
            errors=safety.errors,
            retry_count=state.sql_retry_count,
        )
        return {
            "sql_validation_errors": safety.errors,
            "validation_feedback": safety.feedback,
            "status": QueryStatus.SQL_GENERATED,
            "trace_log": add_trace(
                state, "SQL검증",
                "검증 실패 → 재생성 시도",
                "; ".join(safety.errors[:3]),
            ),
        }

    # 2. 테이블 적절성 검증
    sql = state.generated_sql.strip()
    context_tables = [
        t.table_name for t in state.context.table_metas
    ]
    table_result = validate_table_selection(
        sql=sql,
        query=(
            state.preprocessed_input or state.user_input
        ),
        context_tables=context_tables,
    )

    if table_result.verdict == TableVerdict.AMBIGUOUS:
        logger.info(
            "테이블 선택 모호 — 명확화 필요",
            used=table_result.used_tables,
        )
        return {
            "validated_sql": sql,
            "sql_validation_errors": [],
            "validation_feedback": "",
            "table_selection_verdict": (
                TableVerdict.AMBIGUOUS
            ),
            "table_selection_warnings": (
                table_result.warnings
            ),
            "clarification_question": (
                table_result.clarification_question
            ),
            "status": QueryStatus.SQL_VALIDATED,
        }

    if table_result.verdict == TableVerdict.WARNING:
        from src.services.sql_safety_checker import (
            build_validation_feedback,
        )
        feedback = build_validation_feedback(
            table_result.warnings, sql,
        )
        logger.warning(
            "테이블 선택 부적합 가능성",
            warnings=table_result.warnings,
        )
        return {
            "sql_validation_errors": (
                table_result.warnings
            ),
            "validation_feedback": (
                feedback + "\n\n"
                + (table_result.suggestion or "")
            ),
            "table_selection_verdict": (
                TableVerdict.WARNING
            ),
            "table_selection_warnings": (
                table_result.warnings
            ),
            "status": QueryStatus.SQL_GENERATED,
        }

    logger.info(
        "SQL 검증 통과",
        tables=table_result.used_tables,
    )
    return {
        "validated_sql": sql,
        "sql_validation_errors": [],
        "validation_feedback": "",
        "table_selection_verdict": TableVerdict.PASS,
        "table_selection_warnings": [],
        "status": QueryStatus.SQL_VALIDATED,
        "trace_log": add_trace(
            state, "SQL검증",
            "보안·구문·테이블 검증 통과",
        ),
    }
