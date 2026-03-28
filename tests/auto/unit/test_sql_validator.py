"""SQL 안전성 검증 서비스(sql_safety_checker.validate_sql_safety) 테스트.

검증 대상:
    LLM이 생성한 SQL의 5단계 안전성 검증 파이프라인:
    SELECT/WITH 시작 여부, DML/DDL 차단, 시스템 카탈로그 접근 차단,
    PII 컬럼 직접 노출 차단, LIMIT 강제(집계 쿼리 예외).
"""

from src.services.sql_safety_checker import validate_sql_safety


def test_validate_valid_select_aggregate():
    """유효한 집계 SELECT 문 통과 (LIMIT 불필요)."""
    sql = (
        "SELECT COUNT(*) FROM TB_CUST_INFO"
        " WHERE REG_DT >= '2024-01-01'"
    )
    result = validate_sql_safety(sql)
    assert result.is_safe is True
    assert result.errors == []


def test_validate_valid_select_with_limit():
    """LIMIT이 있는 비집계 SELECT 문 통과."""
    result = validate_sql_safety(
        "SELECT CUST_NO FROM TB_CUST_INFO LIMIT 100",
    )
    assert result.is_safe is True


def test_validate_reject_no_limit_non_aggregate():
    """LIMIT 없는 비집계 쿼리는 검증 실패."""
    result = validate_sql_safety(
        "SELECT CUST_NO FROM TB_CUST_INFO"
        " WHERE REG_DT >= '2024-01-01'",
    )
    assert result.is_safe is False
    assert len(result.errors) > 0
    assert result.feedback  # 피드백 존재


def test_validate_reject_drop():
    """DROP 문은 검증 실패."""
    result = validate_sql_safety("DROP TABLE TB_CUST_INFO")
    assert result.is_safe is False
    assert len(result.errors) > 0


def test_validate_reject_insert():
    """INSERT 문 거부."""
    result = validate_sql_safety(
        "INSERT INTO TB_CUST_INFO VALUES ('test')",
    )
    assert result.is_safe is False
    assert len(result.errors) > 0


def test_validate_reject_delete():
    """DELETE 문 거부."""
    result = validate_sql_safety(
        "DELETE FROM TB_CUST_INFO WHERE CUST_NO = '1'",
    )
    assert result.is_safe is False
    assert len(result.errors) > 0


def test_validate_reject_multi_query():
    """다중 쿼리 거부."""
    result = validate_sql_safety("SELECT 1; SELECT 2")
    assert result.is_safe is False
    assert len(result.errors) > 0


def test_validate_reject_system_catalog():
    """시스템 카탈로그 접근 거부."""
    result = validate_sql_safety(
        "SELECT * FROM information_schema.tables",
    )
    assert result.is_safe is False
    assert len(result.errors) > 0


def test_validate_reject_pii_column():
    """PII 컬럼 직접 조회 거부."""
    result = validate_sql_safety(
        "SELECT JUMIN_NO FROM TB_CUST_INFO",
    )
    assert result.is_safe is False
    assert any("JUMIN_NO" in e for e in result.errors)


def test_validate_empty_sql():
    """빈 SQL 거부."""
    result = validate_sql_safety("")
    assert result.is_safe is False
    assert len(result.errors) > 0


def test_validate_group_by_aggregate():
    """GROUP BY 집계 쿼리는 LIMIT 없이도 통과."""
    result = validate_sql_safety(
        "SELECT LOAN_TYPE_CD, COUNT(*) AS cnt"
        " FROM TB_LOAN_INFO GROUP BY LOAN_TYPE_CD",
    )
    assert result.is_safe is True


def test_validate_produces_feedback_on_failure():
    """검증 실패 시 feedback이 생성된다."""
    result = validate_sql_safety("DROP TABLE test")
    assert result.feedback  # 비어있지 않음
