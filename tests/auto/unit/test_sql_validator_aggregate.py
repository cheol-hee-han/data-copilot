"""SQL 검증 노드 — 집계 쿼리 판별(is_aggregate_query) 단위 테스트.

테스트 대상:
    SQL이 집계 쿼리인지 판별하는 함수를 검증한다.
    COUNT/SUM/AVG 등 집계함수 유무와 GROUP BY 존재 여부를 조합하여
    집계/비집계를 정확히 구분하는 것이 핵심이다.

입력 예시 (정상):
    - "SELECT COUNT(*) FROM T" → True (집계)
    - "SELECT CUST_NO, CUST_NM FROM T" → False (비집계)

결과 예시 (오류 케이스):
    - "SELECT CUST_TYPE_CD, COUNT(*) FROM T" (GROUP BY 없음) → False

실행 스크립트:
    pytest tests/unit/test_sql_validator_aggregate.py -v

참고:
    - 외부 의존성 없음
    - 테스트 대상 소스: src/services/sql_safety_checker.py
"""

from src.services.sql_safety_checker import is_aggregate_query


class TestIsAggregateQuery:
    """_is_aggregate_query 단위 테스트."""

    def test_simple_count(self):
        """SELECT COUNT(*) FROM T → 집계."""
        assert is_aggregate_query(
            "SELECT COUNT(*) FROM TB_CUST_INFO",
        ) is True

    def test_count_with_group_by(self):
        """GROUP BY 있는 집계."""
        sql = (
            "SELECT CUST_TYPE_CD, COUNT(*) "
            "FROM TB_CUST_INFO GROUP BY CUST_TYPE_CD"
        )
        assert is_aggregate_query(sql) is True

    def test_sum_only(self):
        """SELECT SUM(AMT) FROM T → 집계."""
        assert is_aggregate_query(
            "SELECT SUM(LOAN_AMT) FROM TB_LOAN_INFO",
        ) is True

    def test_non_aggregate_select(self):
        """일반 SELECT → 비집계."""
        assert is_aggregate_query(
            "SELECT CUST_NO, CUST_NM FROM TB_CUST_INFO",
        ) is False

    def test_mixed_columns_no_group_by(self):
        """집계+일반 컬럼이 GROUP BY 없이 → 비집계."""
        sql = (
            "SELECT CUST_TYPE_CD, COUNT(*) "
            "FROM TB_CUST_INFO"
        )
        assert is_aggregate_query(sql) is False

    def test_multiple_agg_functions(self):
        """여러 집계 함수만 사용 → 집계."""
        sql = (
            "SELECT COUNT(*), SUM(LOAN_AMT), "
            "AVG(INT_RATE) FROM TB_LOAN_INFO"
        )
        assert is_aggregate_query(sql) is True
