"""SQL 안전성 검증 서비스 엣지 케이스 테스트.

서브쿼리, CTE, 중첩 DML 등 경계 케이스를 검증한다.
테스트 대상: src/services/sql_safety_checker.py — validate_sql_safety()
"""

import pytest

from src.services.sql_safety_checker import SafetyCheckResult, validate_sql_safety


@pytest.fixture(autouse=True)
def _force_pii_masking(monkeypatch):
    """PII 마스킹을 강제 활성화한다.

    .env의 PII_MASKING_ENABLED=false 설정과 무관하게
    check_pii_columns 가 실제 동작하도록 보장한다.
    """
    import src.config as _cfg
    monkeypatch.setattr(_cfg.settings, "pii_masking_enabled", True)


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


def _fail(result: SafetyCheckResult) -> bool:
    """검증 실패 여부를 판단한다."""
    return not result.is_safe


def _ok(result: SafetyCheckResult) -> bool:
    """검증 성공 여부를 판단한다."""
    return result.is_safe


# ---------------------------------------------------------------------------
# 서브쿼리 관련
# ---------------------------------------------------------------------------


def test_validate_subquery_in_from_valid():
    """FROM 절 서브쿼리가 포함된 집계 쿼리는 통과된다."""
    inner = (
        "SELECT BRCH_CD, COUNT(*) AS loan_cnt "
        "FROM TB_LOAN_INFO GROUP BY BRCH_CD"
    )
    result = validate_sql_safety(
        f"SELECT brch_cd, SUM(loan_cnt) AS total "
        f"FROM ({inner}) sub "
        "GROUP BY brch_cd"
    )
    assert _ok(result)


def test_validate_subquery_in_where_valid():
    """WHERE 절 서브쿼리가 포함된 LIMIT 쿼리는 통과된다."""
    result = validate_sql_safety(
        "SELECT CUST_NO, CUST_NM FROM TB_CUST_INFO "
        "WHERE BRCH_CD IN ("
        "SELECT BRCH_CD FROM TB_BRANCH_INFO WHERE REGION_CD = '02'"
        ") LIMIT 100"
    )
    assert _ok(result)


def test_validate_subquery_with_dml_inside_rejected():
    """서브쿼리 내부에 DML 시도 시 거부된다."""
    result = validate_sql_safety(
        "SELECT * FROM "
        "(DELETE FROM TB_CUST_INFO RETURNING *) sub LIMIT 10"
    )
    assert _fail(result)
    assert any("DML/DDL" in e for e in result.errors)


def test_validate_correlated_subquery_with_limit():
    """상관 서브쿼리 + LIMIT 조합은 통과된다."""
    corr = (
        "SELECT COUNT(*) FROM TB_LOAN_INFO l "
        "WHERE l.CUST_NO = c.CUST_NO"
    )
    result = validate_sql_safety(
        f"SELECT c.CUST_NO, c.CUST_NM, ({corr}) AS loan_cnt "
        "FROM TB_CUST_INFO c "
        "ORDER BY loan_cnt DESC LIMIT 50"
    )
    assert _ok(result)


def test_validate_subquery_without_limit_rejected():
    """서브쿼리가 있어도 비집계 쿼리에 LIMIT 없으면 거부된다."""
    result = validate_sql_safety(
        "SELECT c.CUST_NO, c.CUST_NM "
        "FROM TB_CUST_INFO c "
        "WHERE c.BRCH_CD IN ("
        "SELECT BRCH_CD FROM TB_BRANCH_INFO WHERE REGION_CD = '02'"
        ")"
    )
    assert _fail(result)
    assert any("LIMIT" in e for e in result.errors)


# ---------------------------------------------------------------------------
# CTE (WITH 절) 관련
# ---------------------------------------------------------------------------


def test_validate_cte_aggregate_valid():
    """CTE를 사용한 집계 쿼리는 통과된다."""
    result = validate_sql_safety(
        "WITH monthly_stats AS ("
        "SELECT BRCH_CD, COUNT(*) AS loan_cnt, SUM(LOAN_BAL) AS total_bal "
        "FROM TB_LOAN_INFO "
        "WHERE LOAN_DT >= DATE_TRUNC('month', CURRENT_DATE) "
        "GROUP BY BRCH_CD) "
        "SELECT b.BRCH_NM, s.loan_cnt, s.total_bal "
        "FROM monthly_stats s "
        "JOIN TB_BRANCH_INFO b ON s.BRCH_CD = b.BRCH_CD "
        "ORDER BY s.total_bal DESC LIMIT 10"
    )
    assert _ok(result)


def test_validate_cte_with_dml_rejected():
    """CTE 내 INSERT 시도는 거부된다."""
    result = validate_sql_safety(
        "WITH ins AS (INSERT INTO TB_LOG VALUES (1) RETURNING *) "
        "SELECT * FROM ins"
    )
    assert _fail(result)


# ---------------------------------------------------------------------------
# 시스템 카탈로그 우회 시도
# ---------------------------------------------------------------------------


def test_validate_reject_pg_tables():
    """pg_tables 접근 거부."""
    result = validate_sql_safety(
        "SELECT tablename FROM pg_tables LIMIT 10",
    )
    assert _fail(result)


def test_validate_reject_pg_stat_user_tables():
    """pg_stat_user_tables 접근 거부."""
    result = validate_sql_safety(
        "SELECT relname, n_live_tup "
        "FROM pg_stat_user_tables LIMIT 10",
    )
    assert _fail(result)


def test_validate_reject_information_schema_columns():
    """information_schema.columns 접근 거부."""
    result = validate_sql_safety(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'TB_CUST_INFO' LIMIT 50",
    )
    assert _fail(result)


# ---------------------------------------------------------------------------
# PII 컬럼 조합 테스트
# ---------------------------------------------------------------------------


def test_validate_reject_multiple_pii_columns():
    """여러 PII 컬럼 동시 조회 거부."""
    result = validate_sql_safety(
        "SELECT JUMIN_NO, CARD_NO, ACCT_PWD "
        "FROM TB_CUST_INFO LIMIT 10",
    )
    assert _fail(result)
    pii_errors = [e for e in result.errors if "개인정보" in e]
    assert len(pii_errors) >= 2


def test_validate_reject_pii_in_subquery():
    """서브쿼리 내 PII 컬럼 조회 거부."""
    result = validate_sql_safety(
        "SELECT CUST_NO FROM TB_CUST_INFO WHERE JUMIN_NO IN "
        "(SELECT JUMIN_NO FROM TB_CUST_INFO WHERE CUST_TYPE_CD = '01') "
        "LIMIT 10",
    )
    assert _fail(result)


def test_validate_allow_non_pii_column():
    """PII 목록에 없는 컬럼은 통과된다 (CUST_TYPE_CD 등)."""
    result = validate_sql_safety(
        "SELECT CUST_TYPE_CD, COUNT(*) AS cnt "
        "FROM TB_CUST_INFO GROUP BY CUST_TYPE_CD",
    )
    assert _ok(result)


# ---------------------------------------------------------------------------
# 다중 쿼리 경계 케이스
# ---------------------------------------------------------------------------


def test_validate_reject_semicolon_select():
    """세미콜론 뒤 SELECT 다중 쿼리 거부."""
    result = validate_sql_safety(
        "SELECT 1; SELECT COUNT(*) FROM TB_LOAN_INFO",
    )
    assert _fail(result)


# ---------------------------------------------------------------------------
# DDL 변형 테스트
# ---------------------------------------------------------------------------


def test_validate_reject_create_table():
    """CREATE TABLE로 시작하는 문장 거부."""
    result = validate_sql_safety(
        "CREATE TABLE TB_TEST AS "
        "SELECT * FROM TB_CUST_INFO LIMIT 10",
    )
    assert _fail(result)


def test_validate_reject_alter_table():
    """ALTER TABLE 거부."""
    result = validate_sql_safety(
        "ALTER TABLE TB_CUST_INFO ADD COLUMN TEST_COL VARCHAR(10)",
    )
    assert _fail(result)


def test_validate_reject_truncate():
    """TRUNCATE 거부."""
    result = validate_sql_safety("TRUNCATE TABLE TB_CUST_INFO")
    assert _fail(result)


def test_validate_reject_grant():
    """GRANT 거부."""
    result = validate_sql_safety(
        "GRANT SELECT ON TB_CUST_INFO TO public",
    )
    assert _fail(result)


# ---------------------------------------------------------------------------
# LIMIT 관련 경계 케이스
# ---------------------------------------------------------------------------


def test_validate_having_with_group_by_aggregate():
    """HAVING 절이 있는 GROUP BY 집계 쿼리는 LIMIT 없이도 통과된다."""
    result = validate_sql_safety(
        "SELECT BRCH_CD, COUNT(*) AS cnt FROM TB_LOAN_INFO "
        "GROUP BY BRCH_CD HAVING COUNT(*) > 100",
    )
    assert _ok(result)


def test_validate_window_function_requires_limit():
    """윈도우 함수 사용 비집계 쿼리는 LIMIT이 없으면 거부된다."""
    result = validate_sql_safety(
        "SELECT CUST_NO, LOAN_AMT, "
        "RANK() OVER (ORDER BY LOAN_AMT DESC) AS rnk "
        "FROM TB_LOAN_INFO",
    )
    assert _fail(result)
    assert any("LIMIT" in e for e in result.errors)


def test_validate_window_function_with_limit_valid():
    """윈도우 함수 + LIMIT 조합은 통과된다."""
    result = validate_sql_safety(
        "SELECT CUST_NO, LOAN_AMT, "
        "RANK() OVER (ORDER BY LOAN_AMT DESC) AS rnk "
        "FROM TB_LOAN_INFO LIMIT 10",
    )
    assert _ok(result)


# ---------------------------------------------------------------------------
# 케이스 인센서티비티
# ---------------------------------------------------------------------------


def test_validate_lowercase_dml_rejected():
    """소문자 drop 도 거부된다."""
    result = validate_sql_safety("drop table tb_cust_info")
    assert _fail(result)


def test_validate_mixed_case_insert_rejected():
    """혼합 케이스 Insert 도 거부된다."""
    result = validate_sql_safety(
        "Insert Into TB_LOG (msg) Values ('test')",
    )
    assert _fail(result)


# ---------------------------------------------------------------------------
# EXECUTE / 프로시저 관련
# ---------------------------------------------------------------------------


def test_validate_reject_execute():
    """EXECUTE 거부."""
    result = validate_sql_safety("EXECUTE some_procedure('param')")
    assert _fail(result)


def test_validate_reject_exec():
    """EXEC 거부."""
    result = validate_sql_safety("EXEC sp_executesql N'SELECT 1'")
    assert _fail(result)


# ---------------------------------------------------------------------------
# 빈 SQL / SELECT 미시작 케이스
# ---------------------------------------------------------------------------


def test_validate_empty_sql_has_errors():
    """빈 SQL은 errors 를 가진다."""
    result = validate_sql_safety("")
    assert _fail(result)


def test_validate_non_select_start_has_errors():
    """SELECT/WITH 로 시작하지 않는 SQL 은 errors 를 가진다."""
    result = validate_sql_safety(
        "UPDATE TB_CUST_INFO SET CUST_NM = 'test' "
        "WHERE CUST_NO = '001'",
    )
    assert _fail(result)


# ---------------------------------------------------------------------------
# 시간 기반 인젝션 방어
# ---------------------------------------------------------------------------


def test_validate_reject_sleep():
    """SLEEP() 거부."""
    result = validate_sql_safety(
        "SELECT COUNT(*) FROM TB_CUST_INFO WHERE SLEEP(5) = 0",
    )
    assert _fail(result)


def test_validate_reject_pg_sleep():
    """pg_sleep() 거부."""
    result = validate_sql_safety(
        "SELECT pg_sleep(5), COUNT(*) FROM TB_CUST_INFO",
    )
    assert _fail(result)


# ---------------------------------------------------------------------------
# SQL 주석 차단 (키워드 분할 우회 방어)
# ---------------------------------------------------------------------------


def test_validate_reject_sql_line_comment():
    """-- 라인 주석 거부 (키워드 분할 우회 방어)."""
    result = validate_sql_safety(
        "SELECT COUNT(*) FROM TB_CUST_INFO -- WHERE 1=0",
    )
    assert _fail(result)


def test_validate_reject_sql_block_comment():
    """/* */ 블록 주석 거부 (SE/**/LECT 우회 방어)."""
    result = validate_sql_safety(
        "SELECT /* comment */ COUNT(*) FROM TB_CUST_INFO",
    )
    assert _fail(result)
