"""SQL 검증 노드 테스트.

pipeline-designer 에이전트의 재생성 루프 도입 이후,
검증 실패 시 status는 SQL_GENERATED를 반환한다 (ERROR가 아님).
이는 pipeline의 _route_after_validation이 sql_retry_count를 보고
재시도할지 error_end로 갈지를 결정하기 위함이다.
"""

from src.agents.state.state import PipelineState, QueryStatus
from src.agents.nodes.sql_validator import validate_sql_node


def test_validate_valid_select_aggregate():
    """유효한 집계 SELECT 문 통과 (LIMIT 불필요)."""
    state = PipelineState(
        generated_sql=(
            "SELECT COUNT(*) FROM TB_CUST_INFO"
            " WHERE REG_DT >= '2024-01-01'"
        )
    )
    result = validate_sql_node(state)
    assert result["status"] == QueryStatus.SQL_VALIDATED
    assert result["validated_sql"] == state.generated_sql


def test_validate_valid_select_with_limit():
    """LIMIT이 있는 비집계 SELECT 문 통과."""
    state = PipelineState(
        generated_sql="SELECT CUST_NO FROM TB_CUST_INFO LIMIT 100"
    )
    result = validate_sql_node(state)
    assert result["status"] == QueryStatus.SQL_VALIDATED


def test_validate_reject_no_limit_non_aggregate():
    """LIMIT 없는 비집계 쿼리는 검증 실패 (재생성 대상)."""
    state = PipelineState(
        generated_sql=(
            "SELECT CUST_NO FROM TB_CUST_INFO"
            " WHERE REG_DT >= '2024-01-01'"
        )
    )
    result = validate_sql_node(state)
    assert result["status"] == QueryStatus.SQL_GENERATED  # 재생성 루프용
    assert len(result["sql_validation_errors"]) > 0
    assert result["validation_feedback"]  # 피드백 존재


def test_validate_reject_drop():
    """DROP 문은 검증 실패."""
    state = PipelineState(generated_sql="DROP TABLE TB_CUST_INFO")
    result = validate_sql_node(state)
    assert result["status"] == QueryStatus.SQL_GENERATED
    assert len(result["sql_validation_errors"]) > 0


def test_validate_reject_insert():
    """INSERT 문 거부."""
    state = PipelineState(
        generated_sql="INSERT INTO TB_CUST_INFO VALUES ('test')"
    )
    result = validate_sql_node(state)
    assert result["status"] == QueryStatus.SQL_GENERATED
    assert len(result["sql_validation_errors"]) > 0


def test_validate_reject_delete():
    """DELETE 문 거부."""
    state = PipelineState(
        generated_sql="DELETE FROM TB_CUST_INFO WHERE CUST_NO = '1'"
    )
    result = validate_sql_node(state)
    assert result["status"] == QueryStatus.SQL_GENERATED
    assert len(result["sql_validation_errors"]) > 0


def test_validate_reject_multi_query():
    """다중 쿼리 거부."""
    state = PipelineState(generated_sql="SELECT 1; SELECT 2")
    result = validate_sql_node(state)
    assert result["status"] == QueryStatus.SQL_GENERATED
    assert len(result["sql_validation_errors"]) > 0


def test_validate_reject_system_catalog():
    """시스템 카탈로그 접근 거부."""
    state = PipelineState(
        generated_sql="SELECT * FROM information_schema.tables"
    )
    result = validate_sql_node(state)
    assert result["status"] == QueryStatus.SQL_GENERATED
    assert len(result["sql_validation_errors"]) > 0


def test_validate_reject_pii_column():
    """PII 컬럼 직접 조회 거부."""
    state = PipelineState(
        generated_sql="SELECT JUMIN_NO FROM TB_CUST_INFO"
    )
    result = validate_sql_node(state)
    assert result["status"] == QueryStatus.SQL_GENERATED
    assert any("JUMIN_NO" in e for e in result["sql_validation_errors"])


def test_validate_empty_sql():
    """빈 SQL 거부."""
    state = PipelineState(generated_sql="")
    result = validate_sql_node(state)
    assert result["status"] == QueryStatus.SQL_GENERATED
    assert len(result["sql_validation_errors"]) > 0


def test_validate_group_by_aggregate():
    """GROUP BY 집계 쿼리는 LIMIT 없이도 통과."""
    state = PipelineState(
        generated_sql=(
            "SELECT LOAN_TYPE_CD, COUNT(*) AS cnt"
            " FROM TB_LOAN_INFO GROUP BY LOAN_TYPE_CD"
        )
    )
    result = validate_sql_node(state)
    assert result["status"] == QueryStatus.SQL_VALIDATED


def test_validate_produces_feedback_on_failure():
    """검증 실패 시 validation_feedback이 생성된다."""
    state = PipelineState(generated_sql="DROP TABLE test")
    result = validate_sql_node(state)
    assert "validation_feedback" in result
    assert result["validation_feedback"]  # 비어있지 않음
