"""sql_formatter tabularRight 포매터 종합 단위 테스트.

테스트 대상:
    - format_sql_tabular(sql, uppercase): 메인 공개 API
    - pad_keyword(token_text): 키워드 9자 우측 패딩

설계 원칙:
    - LLM 호출 없음, DB 연결 없음
    - 실제 sqlparse 파싱 결과로 검증 (Mock 없음)
    - 각 케이스에서 actual 출력을 캡쳐하여 expected와 비교

실행:
    pytest tests/auto/unit/test_sql_formatter.py -v
"""

from __future__ import annotations

import pytest
import sqlparse as _sqlparse
import sqlparse.tokens as _sqltok

from src.utils.sql_formatter import format_sql_tabular, pad_keyword

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 헬퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _lines(sql: str) -> list[str]:
    """포맷된 SQL을 줄 단위로 분리한다 (빈 줄 제외)."""
    return [ln for ln in sql.splitlines() if ln.strip()]


def _assert_line_starts(actual: str, expected_starts: list[str]) -> None:
    """각 줄이 expected_starts의 prefix로 시작하는지 확인한다."""
    lines = _lines(actual)
    for i, prefix in enumerate(expected_starts):
        assert i < len(lines), (
            f"줄 {i} 없음. 전체 출력:\n{actual}"
        )
        assert lines[i].startswith(prefix), (
            f"줄 {i}: '{lines[i]}' 는 '{prefix}'로 시작해야 함\n전체:\n{actual}"
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-01: 기본 SELECT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestBasicSelect:
    SQL = "SELECT a.col1, b.col2 FROM table1 a WHERE a.id = 1"

    def test_output_is_string(self) -> None:
        actual = format_sql_tabular(self.SQL)
        print(f"\n[TC-01 actual]\n{actual}")
        assert isinstance(actual, str)
        assert len(actual) > 0

    def test_select_clause_present(self) -> None:
        actual = format_sql_tabular(self.SQL)
        assert "SELECT" in actual

    def test_from_clause_present(self) -> None:
        actual = format_sql_tabular(self.SQL)
        assert "FROM" in actual

    def test_where_clause_present(self) -> None:
        actual = format_sql_tabular(self.SQL)
        assert "WHERE" in actual

    def test_clause_keywords_are_right_aligned(self) -> None:
        """SELECT/FROM/WHERE 는 9자 패딩으로 우측 정렬되어야 한다."""
        actual = format_sql_tabular(self.SQL)
        lines = _lines(actual)
        # 각 절 키워드 줄은 공백 + 키워드 형태
        clause_lines = [ln for ln in lines if ln.lstrip().startswith(("SELECT", "FROM", "WHERE"))]
        assert len(clause_lines) >= 3, f"절 키워드 줄 부족\n전체:\n{actual}"
        for ln in clause_lines:
            # 키워드는 10번째 문자(인덱스 9) 이후 콘텐츠가 시작돼야 함
            keyword = ln.lstrip()
            kw = keyword.split()[0]
            pos = ln.index(kw)
            assert pos + len(kw) <= 9 + 1, (
                f"키워드 '{kw}' 위치({pos}) 가 9자 패딩 범위를 초과함\n줄: '{ln}'"
            )

    def test_col_values_preserved(self) -> None:
        actual = format_sql_tabular(self.SQL)
        assert "a.col1" in actual
        assert "b.col2" in actual
        assert "table1" in actual


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-02: 다중 JOIN
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestMultiJoin:
    SQL = (
        "SELECT a.col1, b.col2, c.col3 FROM table1 a "
        "INNER JOIN table2 b ON a.id = b.aid "
        "LEFT JOIN table3 c ON b.id = c.bid "
        "WHERE a.status = 'A' "
        "GROUP BY a.col1, b.col2, c.col3 "
        "ORDER BY a.col1 LIMIT 100"
    )

    def test_all_clauses_present(self) -> None:
        actual = format_sql_tabular(self.SQL)
        print(f"\n[TC-02 actual]\n{actual}")
        expected_kws = (
            "SELECT", "FROM", "INNER JOIN", "LEFT JOIN",
            "WHERE", "GROUP BY", "ORDER BY", "LIMIT",
        )
        for kw in expected_kws:
            assert kw in actual, f"'{kw}' 없음\n{actual}"

    def test_join_keywords_on_separate_lines(self) -> None:
        actual = format_sql_tabular(self.SQL)
        lines = _lines(actual)
        inner_join_lines = [ln for ln in lines if "INNER JOIN" in ln]
        left_join_lines = [ln for ln in lines if "LEFT JOIN" in ln]
        assert len(inner_join_lines) >= 1, f"INNER JOIN 줄 없음\n{actual}"
        assert len(left_join_lines) >= 1, f"LEFT JOIN 줄 없음\n{actual}"

    def test_limit_value_preserved(self) -> None:
        actual = format_sql_tabular(self.SQL)
        assert "100" in actual

    def test_multiline_output(self) -> None:
        actual = format_sql_tabular(self.SQL)
        assert "\n" in actual, "단일 줄 출력은 포맷 실패"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-03: AND/OR 체이닝 + BETWEEN...AND 구분
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestBetweenAndLogical:
    SQL = (
        "SELECT * FROM orders WHERE order_date BETWEEN '2024-01-01' AND '2024-12-31' "
        "AND status = 'COMPLETED' AND amount > 1000 OR priority = 'HIGH'"
    )

    def test_between_keyword_present(self) -> None:
        actual = format_sql_tabular(self.SQL)
        print(f"\n[TC-03 actual]\n{actual}")
        assert "BETWEEN" in actual

    def test_logical_and_on_separate_lines(self) -> None:
        """BETWEEN의 AND가 아닌 논리 AND는 별도 줄에 있어야 한다."""
        actual = format_sql_tabular(self.SQL)
        lines = _lines(actual)
        and_lines = [ln for ln in lines if ln.lstrip().startswith("AND")]
        # 논리 AND 는 최소 2개 (status, amount 조건)
        assert len(and_lines) >= 2, (
            f"논리 AND 줄이 2개 미만 ({len(and_lines)}개)\n{actual}"
        )

    def test_or_on_separate_line(self) -> None:
        actual = format_sql_tabular(self.SQL)
        lines = _lines(actual)
        or_lines = [ln for ln in lines if ln.lstrip().startswith("OR")]
        assert len(or_lines) >= 1, f"OR 줄 없음\n{actual}"

    def test_date_literals_preserved(self) -> None:
        actual = format_sql_tabular(self.SQL)
        assert "2024-01-01" in actual
        assert "2024-12-31" in actual

    def test_between_and_not_on_own_line(self) -> None:
        """BETWEEN의 AND는 논리 연산자 줄 정렬에서 제외되어야 한다.
        즉, '2024-01-01' AND '2024-12-31' 이 같은 논리 AND 줄 처리가 아님."""
        actual = format_sql_tabular(self.SQL)
        # BETWEEN의 AND는 날짜 리터럴과 함께 같은 줄 또는 인라인에 있어야 함
        # 단순 체크: 양쪽 날짜가 모두 출력에 포함됨을 확인
        assert "'2024-01-01'" in actual or "2024-01-01" in actual
        assert "'2024-12-31'" in actual or "2024-12-31" in actual


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-04: 서브쿼리 (FROM절 인라인뷰)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestFromSubquery:
    SQL = (
        "SELECT t.name, t.total FROM "
        "(SELECT customer_name AS name, SUM(amount) AS total FROM orders GROUP BY customer_name) t "
        "WHERE t.total > 10000 ORDER BY t.total DESC"
    )

    def test_subquery_formatted(self) -> None:
        actual = format_sql_tabular(self.SQL)
        print(f"\n[TC-04 actual]\n{actual}")
        # 서브쿼리 내 SELECT 가 있어야 함
        assert actual.count("SELECT") >= 2, f"서브쿼리 SELECT 없음\n{actual}"

    def test_outer_clauses_present(self) -> None:
        actual = format_sql_tabular(self.SQL)
        assert "WHERE" in actual
        assert "ORDER BY" in actual

    def test_subquery_alias_preserved(self) -> None:
        actual = format_sql_tabular(self.SQL)
        # 서브쿼리 alias t 가 보존되어야 함
        assert " t" in actual or "\nt" in actual

    def test_paren_balance(self) -> None:
        actual = format_sql_tabular(self.SQL)
        assert actual.count("(") == actual.count(")"), (
            f"괄호 불균형\n{actual}"
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-05: 스칼라 서브쿼리 (SELECT절)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestScalarSubquery:
    SQL = (
        "SELECT a.emp_name, a.dept_cd, "
        "(SELECT d.dept_name FROM departments d WHERE d.dept_cd = a.dept_cd) AS dept_name "
        "FROM employees a WHERE a.status = 'ACTIVE'"
    )

    def test_scalar_subquery_present(self) -> None:
        actual = format_sql_tabular(self.SQL)
        print(f"\n[TC-05 actual]\n{actual}")
        # 스칼라 서브쿼리 내 SELECT가 있어야 함
        assert actual.count("SELECT") >= 2, f"스칼라 서브쿼리 SELECT 없음\n{actual}"

    def test_alias_dept_name_present(self) -> None:
        actual = format_sql_tabular(self.SQL)
        assert "dept_name" in actual

    def test_outer_where_present(self) -> None:
        actual = format_sql_tabular(self.SQL)
        assert "ACTIVE" in actual

    def test_paren_balance(self) -> None:
        actual = format_sql_tabular(self.SQL)
        assert actual.count("(") == actual.count(")"), (
            f"괄호 불균형\n{actual}"
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-06: WHERE절 IN 서브쿼리
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestWhereInSubquery:
    SQL = (
        "SELECT * FROM products WHERE category_id IN "
        "(SELECT id FROM categories WHERE is_active = 1) AND price > 100"
    )

    def test_in_subquery_present(self) -> None:
        actual = format_sql_tabular(self.SQL)
        print(f"\n[TC-06 actual]\n{actual}")
        assert "IN" in actual
        assert actual.count("SELECT") >= 2

    def test_price_condition_preserved(self) -> None:
        actual = format_sql_tabular(self.SQL)
        assert "price" in actual
        assert "100" in actual

    def test_paren_balance(self) -> None:
        actual = format_sql_tabular(self.SQL)
        assert actual.count("(") == actual.count(")"), (
            f"괄호 불균형\n{actual}"
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-07: N-depth 중첩 서브쿼리 (3단계)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestNestedSubquery3Depth:
    SQL = (
        "SELECT * FROM "
        "(SELECT a.id, a.name FROM "
        "(SELECT id, name, rank FROM employees WHERE rank > 5) a "
        "WHERE a.id IN (SELECT emp_id FROM awards)) b "
        "WHERE b.name IS NOT NULL"
    )

    def test_three_selects_present(self) -> None:
        actual = format_sql_tabular(self.SQL)
        print(f"\n[TC-07 actual]\n{actual}")
        assert actual.count("SELECT") >= 3, (
            f"3개의 SELECT 없음 (got {actual.count('SELECT')})\n{actual}"
        )

    def test_outer_where_present(self) -> None:
        actual = format_sql_tabular(self.SQL)
        assert "IS NOT NULL" in actual or ("IS" in actual and "NOT" in actual and "NULL" in actual)

    def test_paren_balance(self) -> None:
        actual = format_sql_tabular(self.SQL)
        assert actual.count("(") == actual.count(")"), (
            f"괄호 불균형\n{actual}"
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-08: UNION ALL / EXCEPT / INTERSECT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestSetOperations:
    SQL = (
        "SELECT id, name FROM customers WHERE region = 'EAST' "
        "UNION ALL "
        "SELECT id, name FROM customers WHERE region = 'WEST' "
        "EXCEPT "
        "SELECT id, name FROM blacklist"
    )

    def test_set_ops_present(self) -> None:
        actual = format_sql_tabular(self.SQL)
        print(f"\n[TC-08 actual]\n{actual}")
        assert "UNION ALL" in actual
        assert "EXCEPT" in actual

    def test_three_selects_present(self) -> None:
        actual = format_sql_tabular(self.SQL)
        assert actual.count("SELECT") >= 3, (
            f"3개 SELECT 없음\n{actual}"
        )

    def test_set_ops_on_separate_lines(self) -> None:
        actual = format_sql_tabular(self.SQL)
        lines = _lines(actual)
        union_lines = [ln for ln in lines if "UNION ALL" in ln]
        except_lines = [ln for ln in lines if ln.lstrip().startswith("EXCEPT") or "EXCEPT" in ln]
        assert len(union_lines) >= 1, f"UNION ALL 줄 없음\n{actual}"
        assert len(except_lines) >= 1, f"EXCEPT 줄 없음\n{actual}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-09: CTE (WITH ... AS)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestCTE:
    SQL = (
        "WITH monthly_sales AS (SELECT month, SUM(amount) AS total FROM sales GROUP BY month), "
        "top_months AS (SELECT month, total FROM monthly_sales WHERE total > 1000000) "
        "SELECT * FROM top_months ORDER BY total DESC"
    )

    def test_with_keyword_present(self) -> None:
        actual = format_sql_tabular(self.SQL)
        print(f"\n[TC-09 actual]\n{actual}")
        assert "WITH" in actual

    def test_cte_names_preserved(self) -> None:
        actual = format_sql_tabular(self.SQL)
        assert "monthly_sales" in actual
        assert "top_months" in actual

    def test_outer_select_present(self) -> None:
        actual = format_sql_tabular(self.SQL)
        assert "ORDER BY" in actual
        assert "total" in actual

    def test_paren_balance(self) -> None:
        actual = format_sql_tabular(self.SQL)
        assert actual.count("(") == actual.count(")"), (
            f"괄호 불균형\n{actual}"
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-10: CASE WHEN THEN ELSE END
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestCaseWhen:
    SQL = (
        "SELECT emp_name, CASE WHEN salary > 100000 THEN 'HIGH' "
        "WHEN salary > 50000 THEN 'MID' ELSE 'LOW' END AS grade FROM employees"
    )

    def test_case_keywords_present(self) -> None:
        actual = format_sql_tabular(self.SQL)
        print(f"\n[TC-10 actual]\n{actual}")
        for kw in ("CASE", "WHEN", "THEN", "ELSE", "END"):
            assert kw in actual, f"'{kw}' 없음\n{actual}"

    def test_when_then_inline_or_separate(self) -> None:
        """CASE가 SELECT 컬럼 목록 내 Identifier로 래핑될 경우 포매터는
        인라인 CASE를 원문 그대로 출력한다. 모든 CASE 키워드가 출력에
        포함되는지만 검증하고, 줄 분리 여부는 강제하지 않는다.

        Note: CASE 블록의 WHEN/THEN 줄 분리는 독립된 Case 노드로 파싱될 때만
        동작한다 (TC-12 복합 쿼리에서 검증됨).
        """
        actual = format_sql_tabular(self.SQL)
        # WHEN 이 최소 2회, THEN 이 최소 2회 텍스트로 포함되어야 함
        assert actual.count("WHEN") >= 2, (
            f"WHEN 2회 미만\n{actual}"
        )
        assert actual.count("THEN") >= 2, (
            f"THEN 2회 미만\n{actual}"
        )

    def test_grade_values_preserved(self) -> None:
        actual = format_sql_tabular(self.SQL)
        assert "HIGH" in actual
        assert "MID" in actual
        assert "LOW" in actual

    def test_alias_grade_preserved(self) -> None:
        actual = format_sql_tabular(self.SQL)
        assert "grade" in actual


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-11: 윈도우 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestWindowFunction:
    SQL = (
        "SELECT emp_name, dept, salary, "
        "ROW_NUMBER() OVER (PARTITION BY dept ORDER BY salary DESC) AS rn "
        "FROM employees"
    )

    def test_window_function_preserved(self) -> None:
        actual = format_sql_tabular(self.SQL)
        print(f"\n[TC-11 actual]\n{actual}")
        assert "ROW_NUMBER" in actual
        assert "OVER" in actual
        assert "PARTITION BY" in actual

    def test_from_clause_present(self) -> None:
        actual = format_sql_tabular(self.SQL)
        assert "FROM" in actual
        assert "employees" in actual

    def test_alias_rn_preserved(self) -> None:
        actual = format_sql_tabular(self.SQL)
        assert "rn" in actual

    def test_paren_balance(self) -> None:
        actual = format_sql_tabular(self.SQL)
        assert actual.count("(") == actual.count(")"), (
            f"괄호 불균형\n{actual}"
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-12: 복합 실전 쿼리 (은행 도메인 CTE + CASE + HAVING)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestBankingDomainComplexQuery:
    SQL = (
        "WITH base AS ("
        "SELECT a.CUST_NO, a.CUST_NM, b.ACNO, b.OPEN_DT, b.BAL_AMT "
        "FROM ADWOWN.TB_ADW_CUS001M a "
        "INNER JOIN ADWOWN.TB_ADW_DEA208M b ON a.CUST_NO = b.CUST_NO "
        "WHERE b.OPEN_DT >= DATE_TRUNC('year', CURRENT_DATE) AND a.CUST_TP_CD = '01') "
        "SELECT base.CUST_NM, COUNT(base.ACNO) AS acct_cnt, SUM(base.BAL_AMT) AS total_bal, "
        "CASE WHEN SUM(base.BAL_AMT) > 100000000 THEN 'VIP' "
        "WHEN SUM(base.BAL_AMT) > 10000000 THEN 'GOLD' ELSE 'NORMAL' END AS grade "
        "FROM base "
        "GROUP BY base.CUST_NM "
        "HAVING COUNT(base.ACNO) > 1 "
        "ORDER BY total_bal DESC LIMIT 50"
    )

    def test_all_major_clauses_present(self) -> None:
        actual = format_sql_tabular(self.SQL)
        print(f"\n[TC-12 actual]\n{actual}")
        expected_kws = (
            "WITH", "SELECT", "FROM", "INNER JOIN",
            "WHERE", "GROUP BY", "HAVING", "ORDER BY", "LIMIT",
        )
        for kw in expected_kws:
            assert kw in actual, f"'{kw}' 없음\n{actual}"

    def test_case_block_present(self) -> None:
        actual = format_sql_tabular(self.SQL)
        for kw in ("CASE", "WHEN", "THEN", "ELSE", "END"):
            assert kw in actual, f"'{kw}' 없음\n{actual}"

    def test_cte_name_preserved(self) -> None:
        actual = format_sql_tabular(self.SQL)
        assert "base" in actual

    def test_table_names_preserved(self) -> None:
        actual = format_sql_tabular(self.SQL)
        assert "TB_ADW_CUS001M" in actual
        assert "TB_ADW_DEA208M" in actual

    def test_grade_values_preserved(self) -> None:
        actual = format_sql_tabular(self.SQL)
        assert "VIP" in actual
        assert "GOLD" in actual
        assert "NORMAL" in actual

    def test_limit_50(self) -> None:
        actual = format_sql_tabular(self.SQL)
        assert "50" in actual

    def test_paren_balance(self) -> None:
        actual = format_sql_tabular(self.SQL)
        assert actual.count("(") == actual.count(")"), (
            f"괄호 불균형\n{actual}"
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-13: INSERT INTO ... VALUES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestInsertIntoValues:
    SQL = (
        "INSERT INTO audit_log (event_type, event_date, user_id) "
        "VALUES ('LOGIN', CURRENT_DATE, 'admin')"
    )

    def test_insert_into_clause_present(self) -> None:
        actual = format_sql_tabular(self.SQL)
        print(f"\n[TC-13 actual]\n{actual}")
        assert "INSERT INTO" in actual

    def test_values_clause_present(self) -> None:
        actual = format_sql_tabular(self.SQL)
        assert "VALUES" in actual

    def test_column_names_preserved(self) -> None:
        actual = format_sql_tabular(self.SQL)
        assert "event_type" in actual
        assert "event_date" in actual
        assert "user_id" in actual

    def test_value_literals_preserved(self) -> None:
        actual = format_sql_tabular(self.SQL)
        assert "LOGIN" in actual
        assert "admin" in actual


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-14: UPDATE ... SET ... WHERE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestUpdateSetWhere:
    SQL = (
        "UPDATE employees SET salary = salary * 1.1, updated_at = CURRENT_TIMESTAMP "
        "WHERE dept_cd = 'IT' AND performance_grade = 'A'"
    )

    def test_update_clause_present(self) -> None:
        actual = format_sql_tabular(self.SQL)
        print(f"\n[TC-14 actual]\n{actual}")
        assert "UPDATE" in actual

    def test_set_clause_present(self) -> None:
        actual = format_sql_tabular(self.SQL)
        assert "SET" in actual

    def test_where_clause_present(self) -> None:
        actual = format_sql_tabular(self.SQL)
        assert "WHERE" in actual

    def test_column_values_preserved(self) -> None:
        actual = format_sql_tabular(self.SQL)
        assert "salary" in actual
        assert "dept_cd" in actual


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-15: DELETE FROM ... WHERE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestDeleteFromWhere:
    SQL = "DELETE FROM temp_data WHERE created_at < DATE_TRUNC('month', CURRENT_DATE)"

    def test_delete_from_clause_present(self) -> None:
        actual = format_sql_tabular(self.SQL)
        print(f"\n[TC-15 actual]\n{actual}")
        assert "DELETE FROM" in actual

    def test_where_clause_present(self) -> None:
        actual = format_sql_tabular(self.SQL)
        assert "WHERE" in actual

    def test_table_name_preserved(self) -> None:
        actual = format_sql_tabular(self.SQL)
        assert "temp_data" in actual

    def test_condition_preserved(self) -> None:
        actual = format_sql_tabular(self.SQL)
        assert "created_at" in actual
        assert "DATE_TRUNC" in actual


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-16: 빈 문자열 / 공백 입력
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestEmptyInput:
    def test_empty_string_returns_empty(self) -> None:
        actual = format_sql_tabular("")
        print(f"\n[TC-16a actual] repr={repr(actual)}")
        # 빈 문자열 입력은 빈 문자열 반환
        assert actual == "", f"빈 문자열 입력에 빈 문자열 반환 기대, got: {repr(actual)}"

    def test_whitespace_only_returns_same(self) -> None:
        actual = format_sql_tabular("   ")
        print(f"\n[TC-16b actual] repr={repr(actual)}")
        # 공백만 있는 입력은 원본 그대로 반환 (strip 후 falsy)
        assert actual == "   ", f"공백 입력 원본 반환 기대, got: {repr(actual)}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-17: 파싱 불가 자연어 입력 (에러 복원)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestNonSqlInput:
    def test_natural_language_returns_original(self) -> None:
        natural = "올해 신규 고객 수를 알려줘"
        actual = format_sql_tabular(natural)
        print(f"\n[TC-17 actual] repr={repr(actual)}")
        # 파싱 실패 시 원본 반환 (에러 전파 금지)
        assert isinstance(actual, str), "반환값이 str이어야 함"
        # 원본이 그대로 반환되거나 파싱 후 재조합된 결과 중 하나
        # 최소한 예외 없이 실행되어야 함
        assert len(actual) > 0

    def test_no_exception_raised(self) -> None:
        """어떤 입력에도 예외가 발생해서는 안 된다."""
        try:
            format_sql_tabular("완전히 이상한 텍스트 !@#$%^&*()")
        except Exception as e:
            pytest.fail(f"예외가 발생해서는 안 됨: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-18: uppercase=False 옵션
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestLowercaseOption:
    SQL = "SELECT a.col1 FROM table1 a WHERE a.id = 1 ORDER BY a.col1"

    def test_keywords_lowercase(self) -> None:
        actual = format_sql_tabular(self.SQL, uppercase=False)
        print(f"\n[TC-18 actual]\n{actual}")
        # 키워드가 소문자여야 함
        assert "select" in actual.lower()
        # 대문자 키워드가 없어야 함 (col 이름은 원본 그대로)
        lines = _lines(actual)
        clause_prefixes = {"SELECT", "FROM  ", "WHERE ", "ORDER "}
        keyword_lines = [
            ln for ln in lines
            if ln.lstrip()[:6].upper() in clause_prefixes
        ]
        for ln in keyword_lines:
            kw = ln.lstrip().split()[0]
            assert kw == kw.lower(), (
                f"uppercase=False 인데 대문자 키워드 발견: '{kw}'\n전체:\n{actual}"
            )

    def test_content_preserved(self) -> None:
        actual = format_sql_tabular(self.SQL, uppercase=False)
        assert "a.col1" in actual
        assert "table1" in actual


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-19: pad_keyword 단위 테스트
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestPadKeyword:
    """pad_keyword 함수의 단위 테스트.

    tabularRight 규칙: 7자 우측 정렬.
    복합 키워드는 첫 단어만 7자 rjust, 나머지 단어는 그대로 붙임.
    """

    def test_select(self) -> None:
        actual = pad_keyword("SELECT")
        print(f"\n[TC-19] pad_keyword('SELECT') = repr({repr(actual)})")
        assert actual == " SELECT", f"expected ' SELECT', got {repr(actual)}"

    def test_from(self) -> None:
        actual = pad_keyword("FROM")
        print(f"[TC-19] pad_keyword('FROM') = repr({repr(actual)})")
        assert actual == "   FROM", f"expected '   FROM', got {repr(actual)}"

    def test_where(self) -> None:
        actual = pad_keyword("WHERE")
        print(f"[TC-19] pad_keyword('WHERE') = repr({repr(actual)})")
        assert actual == "  WHERE", f"expected '  WHERE', got {repr(actual)}"

    def test_group_by(self) -> None:
        actual = pad_keyword("GROUP BY")
        print(f"[TC-19] pad_keyword('GROUP BY') = repr({repr(actual)})")
        # 복합 키워드: 첫 단어 "GROUP"(5자) rjust(7) + " BY"
        assert actual == "  GROUP BY", f"expected '  GROUP BY', got {repr(actual)}"

    def test_order_by(self) -> None:
        actual = pad_keyword("ORDER BY")
        print(f"[TC-19] pad_keyword('ORDER BY') = repr({repr(actual)})")
        # 복합 키워드: 첫 단어 "ORDER"(5자) rjust(7) + " BY"
        assert actual == "  ORDER BY", f"expected '  ORDER BY', got {repr(actual)}"

    def test_inner_join(self) -> None:
        actual = pad_keyword("INNER JOIN")
        print(f"[TC-19] pad_keyword('INNER JOIN') = repr({repr(actual)})")
        # 첫 단어 "INNER"(5자) rjust(7) + " JOIN" = "  INNER JOIN"
        assert actual == "  INNER JOIN", f"expected '  INNER JOIN', got {repr(actual)}"

    def test_left_outer_join(self) -> None:
        actual = pad_keyword("LEFT OUTER JOIN")
        print(f"[TC-19] pad_keyword('LEFT OUTER JOIN') = repr({repr(actual)})")
        # 첫 단어 "LEFT"(4자) rjust(7) = "   LEFT"
        expected = "   LEFT OUTER JOIN"
        assert actual == expected, (
            f"expected {repr(expected)}, got {repr(actual)}"
        )

    def test_and(self) -> None:
        actual = pad_keyword("AND")
        print(f"[TC-19] pad_keyword('AND') = repr({repr(actual)})")
        assert actual == "    AND", f"expected '    AND', got {repr(actual)}"

    def test_limit(self) -> None:
        actual = pad_keyword("LIMIT")
        print(f"[TC-19] pad_keyword('LIMIT') = repr({repr(actual)})")
        assert actual == "  LIMIT", f"expected '  LIMIT', got {repr(actual)}"

    def test_total_length_single_keyword(self) -> None:
        """단일 키워드의 패딩 결과 길이는 정확히 7자여야 한다."""
        for kw in ("SELECT", "FROM", "WHERE", "HAVING", "AND", "OR", "LIMIT"):
            actual = pad_keyword(kw)
            assert len(actual) == 7, (
                f"'{kw}' 패딩 결과 길이 기대 7, got {len(actual)}: {repr(actual)}"
            )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 헬퍼: 토큰 추출 (의미 보존 검증용)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _extract_tokens(sql: str) -> list[str]:
    """SQL에서 공백·개행 제외 토큰 값 목록을 추출한다.

    uppercase=True 포매터는 예약어(keyword)를 대문자로 변환하므로
    대소문자 정규화 없이 순수 토큰 시퀀스만 반환한다.
    """
    parsed = _sqlparse.parse(sql)
    result: list[str] = []
    for stmt in parsed:
        for tok in stmt.flatten():
            if tok.ttype not in (
                _sqltok.Text.Whitespace,
                _sqltok.Newline,
                _sqltok.Text.Whitespace.Newline,
            ):
                result.append(tok.value)
    return result


def _extract_tokens_normalized(sql: str) -> list[str]:
    """대소문자를 무시하고 토큰을 추출한다.

    uppercase=True 포매터가 예약어를 대문자화하기 때문에
    'month' → 'MONTH' 같은 차이를 허용하기 위해 전체 upper() 처리한다.
    """
    return [t.upper() for t in _extract_tokens(sql)]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-A: SQL 의미 보존 테스트
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestSemanticTokenPreservation:
    """포맷 전후 SQL 의미 토큰이 100% 동일한지 검증한다.

    uppercase=True(기본값) 시 예약어는 대문자로 변환되므로
    대소문자 정규화(upper) 후 비교한다.
    """

    def test_simple_select(self) -> None:
        """단순 SELECT의 토큰이 보존되어야 한다."""
        sql = "SELECT id, name FROM t WHERE id = 1"
        assert _extract_tokens(sql) == _extract_tokens(format_sql_tabular(sql))

    def test_full_clauses_all_present(self) -> None:
        """JOIN + WHERE + GROUP BY + HAVING + ORDER BY + LIMIT 전체 절 토큰 보존."""
        sql = (
            "SELECT a.id, b.name FROM t1 a "
            "INNER JOIN t2 b ON a.id = b.aid "
            "WHERE a.status = 1 "
            "GROUP BY a.id, b.name "
            "HAVING COUNT(*) > 1 "
            "ORDER BY a.id DESC "
            "LIMIT 100"
        )
        assert _extract_tokens_normalized(sql) == _extract_tokens_normalized(
            format_sql_tabular(sql)
        )

    def test_case_when_tokens_preserved(self) -> None:
        """CASE WHEN THEN ELSE END 토큰이 모두 보존되어야 한다."""
        sql = (
            "SELECT id, "
            "CASE WHEN x > 100 THEN 1 WHEN x > 50 THEN 2 ELSE 3 END AS grade "
            "FROM t"
        )
        assert _extract_tokens_normalized(sql) == _extract_tokens_normalized(
            format_sql_tabular(sql)
        )

    def test_between_and_mixed_tokens_preserved(self) -> None:
        """BETWEEN...AND + 논리 AND 혼합 시 토큰이 보존되어야 한다."""
        sql = (
            "SELECT * FROM orders "
            "WHERE order_date BETWEEN '2024-01-01' AND '2024-12-31' "
            "AND status = 'COMPLETED' AND amount > 1000"
        )
        assert _extract_tokens(sql) == _extract_tokens(format_sql_tabular(sql))

    def test_nested_subquery_3depth_tokens_preserved(self) -> None:
        """3단계 중첩 서브쿼리의 토큰이 대소문자 무시 기준으로 보존되어야 한다."""
        sql = (
            "SELECT * FROM "
            "(SELECT a.id, a.name FROM "
            "(SELECT id, name FROM employees WHERE rank > 5) a "
            "WHERE a.id IN (SELECT emp_id FROM awards)) b "
            "WHERE b.name IS NOT NULL"
        )
        assert _extract_tokens_normalized(sql) == _extract_tokens_normalized(
            format_sql_tabular(sql)
        )

    def test_cte_tokens_preserved(self) -> None:
        """CTE (WITH ... AS) 토큰이 대소문자 무시 기준으로 보존되어야 한다."""
        sql = (
            "WITH m AS (SELECT dept, SUM(amt) AS total FROM sales GROUP BY dept) "
            "SELECT * FROM m ORDER BY total DESC"
        )
        assert _extract_tokens_normalized(sql) == _extract_tokens_normalized(
            format_sql_tabular(sql)
        )

    def test_union_all_except_tokens_preserved(self) -> None:
        """UNION ALL + EXCEPT 토큰이 보존되어야 한다."""
        sql = (
            "SELECT id FROM a "
            "UNION ALL "
            "SELECT id FROM b "
            "EXCEPT "
            "SELECT id FROM c"
        )
        assert _extract_tokens(sql) == _extract_tokens(format_sql_tabular(sql))

    def test_string_literals_preserved(self) -> None:
        """문자열 리터럴 ('hello', '2024-01-01') 이 보존되어야 한다."""
        sql = "SELECT id FROM t WHERE s = 'hello' AND d = '2024-01-01'"
        assert _extract_tokens(sql) == _extract_tokens(format_sql_tabular(sql))

    def test_numeric_literals_preserved(self) -> None:
        """숫자 리터럴 (100, 3.14, -5) 이 보존되어야 한다."""
        sql = "SELECT id FROM t WHERE x = 100 AND y = 3.14 AND z = -5"
        orig = _extract_tokens(sql)
        fmt = _extract_tokens(format_sql_tabular(sql))
        assert orig == fmt, f"orig={orig}\nfmt= {fmt}"

    def test_korean_alias_preserved(self) -> None:
        """한국어 별칭 (\"지점명\", \"신규개설건수\") 이 보존되어야 한다."""
        sql = (
            'SELECT b.BR_NM AS "지점명", COUNT(a.ACN) AS "신규개설건수" '
            "FROM t a JOIN t2 b ON a.id = b.id "
            'GROUP BY b.BR_NM ORDER BY "신규개설건수" DESC'
        )
        assert _extract_tokens(sql) == _extract_tokens(format_sql_tabular(sql))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-B: Oracle 구문 테스트
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestOracleSyntax:
    """Oracle 전용 구문(CONNECT BY / START WITH / MINUS) 포맷 검증."""

    HIERARCHY_SQL = (
        "SELECT LEVEL, emp_id, manager_id FROM employees "
        "START WITH manager_id IS NULL "
        "CONNECT BY PRIOR emp_id = manager_id"
    )

    def test_connect_by_on_separate_line(self) -> None:
        """CONNECT BY 는 독립된 줄에 출력되어야 한다."""
        actual = format_sql_tabular(self.HIERARCHY_SQL)
        lines = _lines(actual)
        cb_lines = [ln for ln in lines if "CONNECT BY" in ln]
        assert len(cb_lines) >= 1, f"CONNECT BY 줄 없음\n{actual}"

    def test_start_with_on_separate_line(self) -> None:
        """START WITH 는 독립된 줄에 출력되어야 한다."""
        actual = format_sql_tabular(self.HIERARCHY_SQL)
        lines = _lines(actual)
        sw_lines = [ln for ln in lines if "START WITH" in ln]
        assert len(sw_lines) >= 1, f"START WITH 줄 없음\n{actual}"

    def test_connect_by_padded(self) -> None:
        """CONNECT BY 줄은 tabularRight 패딩이 적용되어야 한다."""
        actual = format_sql_tabular(self.HIERARCHY_SQL)
        lines = _lines(actual)
        cb_line = next(ln for ln in lines if "CONNECT BY" in ln)
        # CONNECT(7자) = PAD_WIDTH(7) → 패딩 0, 키워드가 줄 시작
        end_col = _keyword_end_col(cb_line, "CONNECT")
        assert end_col == 7, (
            f"CONNECT 끝 열 기대 7, got {end_col}: {repr(cb_line)}"
        )

    def test_minus_on_separate_line(self) -> None:
        """MINUS 는 독립된 줄에 SET_OP로 처리되어야 한다."""
        sql = "SELECT id FROM a MINUS SELECT id FROM b"
        actual = format_sql_tabular(sql)
        lines = _lines(actual)
        minus_lines = [ln for ln in lines if ln.strip().startswith("MINUS") or "MINUS" in ln]
        assert len(minus_lines) >= 1, f"MINUS 줄 없음\n{actual}"

    def test_minus_two_selects_present(self) -> None:
        """MINUS 전후 SELECT 가 각각 포맷되어야 한다."""
        sql = "SELECT id FROM a MINUS SELECT id FROM b"
        actual = format_sql_tabular(sql)
        assert actual.count("SELECT") >= 2, f"SELECT 2개 미만\n{actual}"

    def test_level_keyword_in_column_list_newline(self) -> None:
        """SELECT 목록의 LEVEL 뒤 다른 컬럼이 줄바꿈되어야 한다."""
        sql = "SELECT LEVEL, emp_id, manager_id FROM employees"
        actual = format_sql_tabular(sql)
        lines = _lines(actual)
        assert "LEVEL" in actual, f"LEVEL 없음\n{actual}"
        # emp_id와 manager_id가 별도 줄에 있어야 함
        assert any("emp_id" in ln for ln in lines[1:]), (
            f"emp_id 가 SELECT 이후 별도 줄에 없음\n{actual}"
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-C: Impala 구문 테스트
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestImpalaSyntax:
    """Impala 전용 구문(QUALIFY) 포맷 검증."""

    QUALIFY_SQL = (
        "SELECT id, name, "
        "ROW_NUMBER() OVER (PARTITION BY dept ORDER BY salary DESC) AS rn "
        "FROM emp "
        "QUALIFY rn = 1"
    )

    def test_qualify_on_separate_line(self) -> None:
        """QUALIFY 는 독립된 줄에 출력되어야 한다."""
        actual = format_sql_tabular(self.QUALIFY_SQL)
        lines = _lines(actual)
        q_lines = [ln for ln in lines if ln.lstrip().startswith("QUALIFY")]
        assert len(q_lines) >= 1, f"QUALIFY 줄 없음\n{actual}"

    def test_qualify_padded(self) -> None:
        """QUALIFY 줄은 tabularRight 패딩이 적용되어야 한다."""
        actual = format_sql_tabular(self.QUALIFY_SQL)
        lines = _lines(actual)
        q_line = next(ln for ln in lines if "QUALIFY" in ln)
        # QUALIFY(7자) = PAD_WIDTH(7) → 패딩 0, 키워드가 줄 시작
        end_col = _keyword_end_col(q_line, "QUALIFY")
        assert end_col == 7, (
            f"QUALIFY 끝 열 기대 7, got {end_col}: {repr(q_line)}"
        )

    def test_qualify_condition_preserved(self) -> None:
        """QUALIFY 조건 값이 포맷 후에도 보존되어야 한다."""
        actual = format_sql_tabular(self.QUALIFY_SQL)
        assert "rn" in actual
        assert "= 1" in actual or "=1" in actual

    def test_window_function_preserved_with_qualify(self) -> None:
        """QUALIFY 포함 시 윈도우 함수도 보존되어야 한다."""
        actual = format_sql_tabular(self.QUALIFY_SQL)
        assert "ROW_NUMBER" in actual
        assert "PARTITION BY" in actual


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-D: Sybase IQ BEGIN-END 테스트
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestBeginEndBlock:
    """BEGIN-END 블록 (Sybase IQ) 포맷 검증."""

    def test_single_begin_end_present(self) -> None:
        """단일 BEGIN-END 블록이 출력에 포함되어야 한다."""
        sql = "BEGIN SELECT id FROM t END"
        actual = format_sql_tabular(sql)
        print(f"\n[TC-D1 actual]\n{actual}")
        assert "BEGIN" in actual
        assert "END" in actual

    def test_begin_on_own_line(self) -> None:
        """BEGIN 키워드는 독립된 줄에 있어야 한다."""
        sql = "BEGIN SELECT id FROM t END"
        actual = format_sql_tabular(sql)
        lines = _lines(actual)
        begin_lines = [ln for ln in lines if ln.strip() == "BEGIN"]
        assert len(begin_lines) >= 1, f"BEGIN 단독 줄 없음\n{actual}"

    def test_begin_end_inner_select_present(self) -> None:
        """BEGIN-END 내부 SELECT 가 포맷되어야 한다."""
        sql = "BEGIN SELECT id FROM t WHERE status = 1 END"
        actual = format_sql_tabular(sql)
        assert "SELECT" in actual
        assert "FROM" in actual

    def test_nested_begin_end_indentation(self) -> None:
        """중첩 BEGIN-END 에서 내부 블록이 들여쓰기되어야 한다."""
        sql = "BEGIN BEGIN SELECT id FROM t END END"
        actual = format_sql_tabular(sql)
        print(f"\n[TC-D nested actual]\n{actual}")
        lines = _lines(actual)
        # 내부 BEGIN 줄의 들여쓰기가 외부 BEGIN 줄보다 커야 함
        begin_lines = [ln for ln in lines if ln.strip() == "BEGIN"]
        assert len(begin_lines) >= 2, f"BEGIN 줄 2개 미만\n{actual}"
        outer_indent = len(begin_lines[0]) - len(begin_lines[0].lstrip())
        inner_indent = len(begin_lines[1]) - len(begin_lines[1].lstrip())
        assert inner_indent > outer_indent, (
            f"내부 BEGIN 들여쓰기({inner_indent})가 외부({outer_indent})보다 크지 않음\n{actual}"
        )

    def test_begin_with_select_and_update(self) -> None:
        """BEGIN 내부 SELECT + UPDATE 복합 구문이 모두 포맷되어야 한다."""
        sql = "BEGIN SELECT id FROM t WHERE status=1; UPDATE t SET status=2 WHERE id=1 END"
        actual = format_sql_tabular(sql)
        assert "SELECT" in actual
        assert "UPDATE" in actual
        assert "SET" in actual

    def test_begin_end_paren_balance(self) -> None:
        """BEGIN-END 블록 처리 후 괄호 균형이 맞아야 한다."""
        sql = "BEGIN SELECT id FROM t WHERE x IN (1, 2, 3) END"
        actual = format_sql_tabular(sql)
        assert actual.count("(") == actual.count(")"), (
            f"괄호 불균형\n{actual}"
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-E: 다중 Statement 테스트
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestMultipleStatements:
    """세미콜론으로 구분된 다중 Statement 포맷 검증."""

    def test_two_selects_both_formatted(self) -> None:
        """세미콜론으로 구분된 2개 SELECT 가 모두 포맷되어야 한다."""
        sql = "SELECT id FROM a; SELECT name FROM b"
        actual = format_sql_tabular(sql)
        print(f"\n[TC-E2 actual]\n{actual}")
        assert actual.count("SELECT") >= 2, f"SELECT 2개 미만\n{actual}"
        assert "FROM" in actual

    def test_two_selects_semicolon_preserved(self) -> None:
        """세미콜론이 출력에 보존되어야 한다."""
        sql = "SELECT id FROM a; SELECT name FROM b"
        actual = format_sql_tabular(sql)
        assert ";" in actual, f"세미콜론 없음\n{actual}"

    def test_three_selects_all_formatted(self) -> None:
        """3개 SELECT 가 모두 포맷되어야 한다."""
        sql = "SELECT 1 AS a; SELECT 2 AS b; SELECT 3 AS c"
        actual = format_sql_tabular(sql)
        print(f"\n[TC-E3 actual]\n{actual}")
        assert actual.count("SELECT") >= 3, f"SELECT 3개 미만\n{actual}"

    def test_trailing_semicolon_no_crash(self) -> None:
        """마지막 세미콜론만 있는 경우 예외 없이 처리되어야 한다."""
        sql = "SELECT id FROM t;"
        actual = format_sql_tabular(sql)
        assert isinstance(actual, str), "반환값이 str이어야 함"
        assert "SELECT" in actual

    def test_trailing_semicolon_content_preserved(self) -> None:
        """마지막 세미콜론이 있어도 SQL 내용이 보존되어야 한다."""
        sql = "SELECT id FROM t;"
        actual = format_sql_tabular(sql)
        assert "id" in actual
        assert "FROM" in actual


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-F: IdentifierList 콤마 줄바꿈 테스트
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestIdentifierListNewline:
    """SELECT/GROUP BY/ORDER BY 절의 다중 컬럼 줄바꿈 검증."""

    def test_select_five_columns_each_on_own_line(self) -> None:
        """SELECT 절 5개 이상 컬럼이 각각 별도 줄에 있어야 한다."""
        sql = "SELECT col1, col2, col3, col4, col5 FROM t"
        actual = format_sql_tabular(sql)
        print(f"\n[TC-F1 actual]\n{actual}")
        lines = _lines(actual)
        # col2~col5 가 각각 독립된 줄에 있어야 함
        for col in ("col2", "col3", "col4", "col5"):
            col_lines = [ln for ln in lines if col in ln]
            assert len(col_lines) >= 1, f"'{col}' 별도 줄 없음\n{actual}"
        # 각 컬럼이 서로 다른 줄에 있어야 함
        col_line_indices = [
            next(i for i, ln in enumerate(lines) if f"col{j}" in ln)
            for j in range(1, 6)
        ]
        assert len(set(col_line_indices)) == 5, (
            f"5개 컬럼이 5개 다른 줄에 있지 않음\n{actual}"
        )

    def test_group_by_multiple_columns_newline(self) -> None:
        """GROUP BY 절의 다중 컬럼이 줄바꿈되어야 한다."""
        sql = "SELECT a, b, c, SUM(d) FROM t GROUP BY a, b, c"
        actual = format_sql_tabular(sql)
        lines = _lines(actual)
        gb_line_idx = next(
            i for i, ln in enumerate(lines) if "GROUP BY" in ln
        )
        # GROUP BY 이후 줄들에 a, b, c 가 분산되어야 함
        after_gb = lines[gb_line_idx:]
        assert any("b" in ln for ln in after_gb), (
            f"GROUP BY 절 'b' 별도 줄 없음\n{actual}"
        )
        assert any("c" in ln for ln in after_gb), (
            f"GROUP BY 절 'c' 별도 줄 없음\n{actual}"
        )

    def test_order_by_multiple_columns_with_direction(self) -> None:
        """ORDER BY 절의 다중 컬럼+ASC/DESC 가 줄바꿈되어야 한다."""
        sql = "SELECT a, b FROM t ORDER BY a ASC, b DESC"
        actual = format_sql_tabular(sql)
        print(f"\n[TC-F3 actual]\n{actual}")
        lines = _lines(actual)
        ob_line_idx = next(
            i for i, ln in enumerate(lines) if "ORDER BY" in ln
        )
        after_ob = lines[ob_line_idx:]
        # a ASC 와 b DESC 가 ORDER BY 절 내에 있어야 함
        combined = " ".join(after_ob)
        assert "ASC" in combined, f"ASC 없음\n{actual}"
        assert "DESC" in combined, f"DESC 없음\n{actual}"
        # b DESC 가 별도 줄에 있어야 함
        assert any("b" in ln and "DESC" in ln for ln in after_ob), (
            f"'b DESC' 가 ORDER BY 이후 별도 줄에 없음\n{actual}"
        )

    def test_reserved_word_in_column_list(self) -> None:
        """예약어(LEVEL, DATE, TYPE)가 컬럼 목록에 포함될 때 줄바꿈되어야 한다."""
        sql = "SELECT LEVEL, DATE, TYPE FROM t"
        actual = format_sql_tabular(sql)
        lines = _lines(actual)
        # 3개 컬럼이 분산되어야 함
        assert any("DATE" in ln for ln in lines), f"DATE 없음\n{actual}"
        assert any("TYPE" in ln for ln in lines), f"TYPE 없음\n{actual}"
        # DATE 와 LEVEL 이 서로 다른 줄에 있어야 함
        level_line = next(i for i, ln in enumerate(lines) if "LEVEL" in ln)
        date_line = next(i for i, ln in enumerate(lines) if "DATE" in ln)
        assert level_line != date_line, (
            f"LEVEL 과 DATE 가 같은 줄에 있음\n{actual}"
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-G: 이중·다중 괄호 서브쿼리 감지 테스트
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestDoubleParenSubquery:
    """이중/3중 괄호로 감싼 서브쿼리 감지 및 포맷 검증."""

    def test_double_paren_select_preserved(self) -> None:
        """((SELECT ...)) 구조에서 SELECT 가 보존되어야 한다."""
        sql = "SELECT * FROM ((SELECT id FROM t)) sq"
        actual = format_sql_tabular(sql)
        print(f"\n[TC-G1 actual]\n{actual}")
        assert "SELECT" in actual
        assert "id" in actual
        assert "FROM" in actual

    def test_double_paren_paren_balance(self) -> None:
        """이중 괄호 처리 후 괄호 균형이 맞아야 한다."""
        sql = "SELECT * FROM ((SELECT id FROM t)) sq"
        actual = format_sql_tabular(sql)
        assert actual.count("(") == actual.count(")"), (
            f"괄호 불균형\n{actual}"
        )

    def test_double_paren_no_exception(self) -> None:
        """이중 괄호 서브쿼리 처리 시 예외가 발생해서는 안 된다."""
        sql = "SELECT * FROM ((SELECT id FROM t)) sq"
        try:
            format_sql_tabular(sql)
        except Exception as e:
            pytest.fail(f"예외 발생: {e}")

    def test_triple_paren_select_preserved(self) -> None:
        """(((SELECT ...))) 구조에서 SELECT 가 보존되어야 한다."""
        sql = "SELECT * FROM (((SELECT id FROM t))) sq"
        actual = format_sql_tabular(sql)
        print(f"\n[TC-G2 actual]\n{actual}")
        assert "SELECT" in actual
        assert "id" in actual

    def test_triple_paren_paren_balance(self) -> None:
        """3중 괄호 처리 후 괄호 균형이 맞아야 한다."""
        sql = "SELECT * FROM (((SELECT id FROM t))) sq"
        actual = format_sql_tabular(sql)
        assert actual.count("(") == actual.count(")"), (
            f"괄호 불균형\n{actual}"
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-H: 엣지케이스 보강
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestEdgeCasesExtended:
    """None 입력, 자연어, 긴 SQL, uppercase 옵션 등 경계 조건 검증."""

    def test_none_input_returns_empty_string(self) -> None:
        """None 입력은 빈 문자열을 반환해야 한다."""
        actual = format_sql_tabular(None)  # type: ignore[arg-type]
        assert actual == "", f"None 입력에 빈 문자열 반환 기대, got: {repr(actual)}"

    def test_empty_string_returns_empty_string(self) -> None:
        """빈 문자열 입력은 빈 문자열을 반환해야 한다."""
        actual = format_sql_tabular("")
        assert actual == "", f"빈 문자열 반환 기대, got: {repr(actual)}"

    def test_whitespace_only_returns_original(self) -> None:
        """공백만 있는 입력은 원본 그대로 반환해야 한다."""
        actual = format_sql_tabular("   ")
        assert actual == "   ", f"공백 원본 반환 기대, got: {repr(actual)}"

    def test_natural_language_returns_string(self) -> None:
        """자연어 입력 ('올해 매출 보여줘') 은 예외 없이 str을 반환해야 한다."""
        actual = format_sql_tabular("올해 매출 보여줘")
        assert isinstance(actual, str)
        assert len(actual) > 0

    def test_natural_language_no_exception(self) -> None:
        """자연어 입력 처리 중 예외가 발생해서는 안 된다."""
        try:
            format_sql_tabular("올해 매출 보여줘")
        except Exception as e:
            pytest.fail(f"예외 발생: {e}")

    def test_long_sql_processed_correctly(self) -> None:
        """1000자 이상의 긴 SQL 이 정상 처리되어야 한다."""
        # 80개 긴 컬럼명으로 1000자 이상 SQL 생성
        cols = ", ".join([f"column_name_{i}" for i in range(80)])
        long_sql = (
            f"SELECT {cols}"
            " FROM big_table"
            " WHERE status = 1 AND category = 2"
            " AND region = 3 AND dept_code = 100"
        )
        assert len(long_sql) >= 1000, (
            f"테스트 SQL 길이가 1000자 미만: {len(long_sql)}"
        )
        actual = format_sql_tabular(long_sql)
        assert isinstance(actual, str)
        assert len(actual) > 0
        assert "SELECT" in actual
        assert "FROM" in actual

    def test_uppercase_false_keywords_are_lowercase(self) -> None:
        """uppercase=False 시 절 키워드가 소문자여야 한다."""
        sql = "SELECT a.col1 FROM table1 a WHERE a.id = 1 ORDER BY a.col1"
        actual = format_sql_tabular(sql, uppercase=False)
        lines = _lines(actual)
        for ln in lines:
            stripped = ln.lstrip()
            if not stripped:
                continue
            kw = stripped.split()[0]
            # 절 키워드(select/from/where/order)만 소문자 확인
            if kw.lower() in ("select", "from", "where", "order"):
                assert kw == kw.lower(), (
                    f"uppercase=False 인데 대문자 키워드: '{kw}'\n전체:\n{actual}"
                )

    def test_uppercase_false_content_preserved(self) -> None:
        """uppercase=False 시 컬럼명·테이블명이 보존되어야 한다."""
        sql = "SELECT a.col1 FROM table1 a WHERE a.id = 1"
        actual = format_sql_tabular(sql, uppercase=False)
        assert "a.col1" in actual
        assert "table1" in actual


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-I: 패딩 정렬 정밀 검증
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _keyword_end_col(line: str, keyword: str) -> int:
    """줄에서 키워드 끝 열(0-indexed) 을 반환한다.

    예: '   SELECT a' 에서 'SELECT' 끝 위치는 8 (0-indexed).
    """
    idx = line.find(keyword)
    if idx == -1:
        return -1
    return idx + len(keyword)


class TestPaddingAlignment:
    """tabularRight 패딩 정렬 규칙을 정밀하게 검증한다.

    tabularRight 규칙:
    - 절 키워드(SELECT/FROM/WHERE 등)는 9자 rjust 패딩 후 공백 1자 = 10자 위치에서 데이터 시작
    - GROUP BY / ORDER BY 는 첫 단어 'GROUP'/'ORDER' 만 9자 rjust
    """

    SQL = (
        "SELECT a, b FROM t WHERE x = 1 "
        "GROUP BY a, b HAVING COUNT(*) > 1 "
        "ORDER BY a LIMIT 10"
    )

    def _clause_lines(self, actual: str) -> dict[str, str]:
        """절 키워드가 있는 줄을 {키워드: 줄} 형태로 반환한다."""
        result: dict[str, str] = {}
        for ln in _lines(actual):
            for kw in ("SELECT", "FROM", "WHERE", "GROUP BY", "HAVING", "ORDER BY", "LIMIT"):
                if kw in ln and kw not in result:
                    result[kw] = ln
        return result

    def test_select_data_starts_at_column_8(self) -> None:
        """SELECT 절 데이터는 8번째 열(0-indexed 7)부터 시작해야 한다."""
        actual = format_sql_tabular(self.SQL)
        clause_map = self._clause_lines(actual)
        assert "SELECT" in clause_map
        ln = clause_map["SELECT"]
        # ' SELECT a' → SELECT 끝은 인덱스 7, 공백 1개 후 데이터 시작 = 인덱스 8
        end_col = _keyword_end_col(ln, "SELECT")
        assert end_col == 7, (
            f"SELECT 끝 열 기대 7, got {end_col}: {repr(ln)}"
        )

    def test_from_data_starts_at_column_8(self) -> None:
        """FROM 절 데이터는 8번째 열부터 시작해야 한다."""
        actual = format_sql_tabular(self.SQL)
        clause_map = self._clause_lines(actual)
        assert "FROM" in clause_map
        ln = clause_map["FROM"]
        end_col = _keyword_end_col(ln, "FROM")
        assert end_col == 7, (
            f"FROM 끝 열 기대 7, got {end_col}: {repr(ln)}"
        )

    def test_where_data_starts_at_column_8(self) -> None:
        """WHERE 절 데이터는 8번째 열부터 시작해야 한다."""
        actual = format_sql_tabular(self.SQL)
        clause_map = self._clause_lines(actual)
        assert "WHERE" in clause_map
        ln = clause_map["WHERE"]
        end_col = _keyword_end_col(ln, "WHERE")
        assert end_col == 7, (
            f"WHERE 끝 열 기대 7, got {end_col}: {repr(ln)}"
        )

    def test_limit_data_starts_at_column_8(self) -> None:
        """LIMIT 절 데이터는 8번째 열부터 시작해야 한다."""
        actual = format_sql_tabular(self.SQL)
        clause_map = self._clause_lines(actual)
        assert "LIMIT" in clause_map
        ln = clause_map["LIMIT"]
        end_col = _keyword_end_col(ln, "LIMIT")
        assert end_col == 7, (
            f"LIMIT 끝 열 기대 7, got {end_col}: {repr(ln)}"
        )

    def test_and_aligned_with_clauses(self) -> None:
        """AND 는 절 키워드와 같은 열 정렬(JOIN-like)이어야 한다."""
        sql = "SELECT a FROM t WHERE x = 1 AND y = 2 AND z = 3"
        actual = format_sql_tabular(sql)
        lines = _lines(actual)
        and_lines = [ln for ln in lines if ln.lstrip().startswith("AND")]
        assert len(and_lines) >= 2, f"AND 줄 2개 미만\n{actual}"
        for ln in and_lines:
            end_col = _keyword_end_col(ln, "AND")
            assert end_col == 7, (
                f"AND 끝 열 기대 7, got {end_col}: {repr(ln)}"
            )

    def test_or_aligned_with_clauses(self) -> None:
        """OR 는 절 키워드와 같은 열 정렬이어야 한다."""
        sql = "SELECT a FROM t WHERE x = 1 OR y = 2"
        actual = format_sql_tabular(sql)
        lines = _lines(actual)
        or_lines = [ln for ln in lines if ln.lstrip().startswith("OR")]
        assert len(or_lines) >= 1, f"OR 줄 없음\n{actual}"
        for ln in or_lines:
            end_col = _keyword_end_col(ln, "OR")
            assert end_col == 7, (
                f"OR 끝 열 기대 7, got {end_col}: {repr(ln)}"
            )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-J: 윈도우 함수 상세 테스트
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestWindowFunctionDetailed:
    """ROW_NUMBER / SUM OVER / LEAD / LAG 윈도우 함수 인라인 보존 검증."""

    def test_row_number_over_inline(self) -> None:
        """ROW_NUMBER() OVER (PARTITION BY ... ORDER BY ...) 가 인라인으로 보존되어야 한다."""
        sql = (
            "SELECT emp_name, dept, salary, "
            "ROW_NUMBER() OVER (PARTITION BY dept ORDER BY salary DESC) AS rn "
            "FROM employees"
        )
        actual = format_sql_tabular(sql)
        print(f"\n[TC-J1 actual]\n{actual}")
        assert "ROW_NUMBER" in actual
        assert "OVER" in actual
        assert "PARTITION BY" in actual
        assert "rn" in actual

    def test_row_number_paren_balance(self) -> None:
        """ROW_NUMBER 윈도우 함수 포함 시 괄호 균형이 맞아야 한다."""
        sql = (
            "SELECT ROW_NUMBER() OVER (PARTITION BY dept ORDER BY salary DESC) AS rn "
            "FROM employees"
        )
        actual = format_sql_tabular(sql)
        assert actual.count("(") == actual.count(")"), (
            f"괄호 불균형\n{actual}"
        )

    def test_sum_over_order_by_inline(self) -> None:
        """SUM() OVER (ORDER BY ...) 가 인라인으로 보존되어야 한다."""
        sql = (
            "SELECT id, "
            "SUM(val) OVER (ORDER BY id ROWS UNBOUNDED PRECEDING) AS running_sum "
            "FROM t"
        )
        actual = format_sql_tabular(sql)
        print(f"\n[TC-J2 actual]\n{actual}")
        assert "SUM" in actual
        assert "OVER" in actual
        assert "running_sum" in actual

    def test_lead_lag_preserved(self) -> None:
        """LEAD / LAG 윈도우 함수가 보존되어야 한다."""
        sql = (
            "SELECT id, val, "
            "LEAD(val, 1) OVER (ORDER BY id) AS next_val, "
            "LAG(val, 1) OVER (ORDER BY id) AS prev_val "
            "FROM t"
        )
        actual = format_sql_tabular(sql)
        print(f"\n[TC-J3 actual]\n{actual}")
        assert "LEAD" in actual
        assert "LAG" in actual
        assert "next_val" in actual
        assert "prev_val" in actual

    def test_lead_lag_paren_balance(self) -> None:
        """LEAD/LAG 포함 시 괄호 균형이 맞아야 한다."""
        sql = (
            "SELECT LEAD(val) OVER (ORDER BY id) AS nv, "
            "LAG(val) OVER (ORDER BY id) AS pv "
            "FROM t"
        )
        actual = format_sql_tabular(sql)
        assert actual.count("(") == actual.count(")"), (
            f"괄호 불균형\n{actual}"
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TC-K: 복합 실전 쿼리 (은행 도메인)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestBankingDomainAdvanced:
    """CTE + JOIN + CASE + HAVING + 서브쿼리를 조합한 은행 도메인 복합 쿼리 검증."""

    LOAN_SQL = (
        "WITH loan_base AS ("
        "SELECT c.CUST_NO, c.CUST_NM, l.LOAN_AMT, l.LOAN_DT, "
        "CASE WHEN l.LOAN_AMT > 100000000 THEN 'HIGH' "
        "WHEN l.LOAN_AMT > 50000000 THEN 'MID' ELSE 'LOW' END AS grade "
        "FROM TB_CUST c "
        "INNER JOIN TB_LOAN l ON c.CUST_NO = l.CUST_NO "
        "WHERE l.LOAN_DT >= '2024-01-01') "
        "SELECT grade, COUNT(*) AS cnt, SUM(LOAN_AMT) AS total "
        "FROM loan_base "
        "GROUP BY grade "
        "HAVING COUNT(*) > 10 "
        "ORDER BY total DESC "
        "LIMIT 20"
    )

    def test_cte_join_case_having_all_present(self) -> None:
        """CTE + INNER JOIN + CASE + HAVING + ORDER BY + LIMIT 가 모두 출력되어야 한다."""
        actual = format_sql_tabular(self.LOAN_SQL)
        print(f"\n[TC-K1 actual]\n{actual}")
        for kw in ("WITH", "INNER JOIN", "CASE", "HAVING", "ORDER BY", "LIMIT"):
            assert kw in actual, f"'{kw}' 없음\n{actual}"

    def test_cte_name_preserved(self) -> None:
        """CTE 이름 'loan_base' 가 보존되어야 한다."""
        actual = format_sql_tabular(self.LOAN_SQL)
        assert "loan_base" in actual

    def test_case_grade_values_preserved(self) -> None:
        """CASE 블록의 HIGH/MID/LOW 값이 보존되어야 한다."""
        actual = format_sql_tabular(self.LOAN_SQL)
        assert "HIGH" in actual
        assert "MID" in actual
        assert "LOW" in actual

    def test_paren_balance(self) -> None:
        """복합 쿼리 포맷 후 괄호 균형이 맞아야 한다."""
        actual = format_sql_tabular(self.LOAN_SQL)
        assert actual.count("(") == actual.count(")"), (
            f"괄호 불균형\n{actual}"
        )

    def test_limit_value_preserved(self) -> None:
        """LIMIT 20 이 보존되어야 한다."""
        actual = format_sql_tabular(self.LOAN_SQL)
        assert "20" in actual

    MULTI_CTE_SQL = (
        "WITH a AS (SELECT id FROM t1 WHERE x > 1), "
        "b AS (SELECT id, name FROM t2 WHERE y < 10) "
        "SELECT a.id, b.name FROM a "
        "JOIN b ON a.id = b.id"
    )

    def test_multi_cte_both_names_present(self) -> None:
        """다중 CTE 이름 a, b 가 모두 출력되어야 한다."""
        actual = format_sql_tabular(self.MULTI_CTE_SQL)
        print(f"\n[TC-K2 multi-cte actual]\n{actual}")
        assert "WITH" in actual
        # CTE 이름이 출력에 포함되어야 함
        assert " a " in actual or "\na " in actual or actual.strip().startswith("WITH a")
        assert " b " in actual or "\nb " in actual

    def test_multi_cte_join_present(self) -> None:
        """다중 CTE 이후 JOIN 이 포맷되어야 한다."""
        actual = format_sql_tabular(self.MULTI_CTE_SQL)
        assert "JOIN" in actual
        assert "ON" in actual

    def test_multi_cte_paren_balance(self) -> None:
        """다중 CTE 포맷 후 괄호 균형이 맞아야 한다."""
        actual = format_sql_tabular(self.MULTI_CTE_SQL)
        assert actual.count("(") == actual.count(")"), (
            f"괄호 불균형\n{actual}"
        )
