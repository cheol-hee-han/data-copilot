"""sqlglot 기반 SQL 파싱 및 구조 분석 단위 테스트.

테스트 대상:
    - extract_structural_hints: 12가지 구조 힌트 추출
    - parse_sql_safe: 방언별 안전한 파싱 (실패 시 None)
    - merge_hints: 다수 SQL 힌트 병합 (중복 제거)
    - get_real_tables: AST + regex fallback 테이블 추출
    - get_real_columns: 실제 참조 컬럼만 추출
    - extract_select_alias_map: SELECT 절 alias 매핑

실행 스크립트:
    pytest tests/auto/unit/test_sqlglot_analyzer.py -v

참고:
    - 외부 의존성 없음 (sqlglot 라이브러리만 사용)
    - 실제 SQL 예제로 파싱 결과를 검증
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tests.conftest import get_test_logger, log_test_case
from src.utils.sqlglot_analyzer import (
    extract_structural_hints,
    extract_select_alias_map,
    get_real_columns,
    get_real_tables,
    merge_hints,
    parse_sql_safe,
)

logger = get_test_logger("test_sqlglot_analyzer")


# ── 테스트용 SQL 픽스처 ──

_SQL_SIMPLE_JOIN = """
SELECT A.CUST_NO, A.CUST_NM, B.LOAN_AMT
FROM TB_CRM_CUSTOMER A
JOIN TB_LNS_LOANINFO B ON A.CUST_NO = B.CUST_NO
WHERE A.CUST_STAT_CD = '1'
"""

_SQL_AGGREGATE = """
SELECT A.BRANCH_CD, COUNT(*) AS CNT, SUM(A.LOAN_AMT) AS TOTAL_AMT
FROM TB_LNS_LOANINFO A
WHERE A.LOAN_DT >= '20260101' AND A.LOAN_DT <= '20260331'
GROUP BY A.BRANCH_CD
HAVING COUNT(*) > 10
ORDER BY TOTAL_AMT DESC
LIMIT 20
"""

_SQL_SUBQUERY = """
SELECT T.CUST_NO, T.CUST_NM
FROM TB_CRM_CUSTOMER T
WHERE T.CUST_NO IN (
    SELECT S.CUST_NO
    FROM TB_LNS_LOANINFO S
    WHERE S.LOAN_STAT_CD = 'A'
)
"""

_SQL_CTE = """
WITH overdue_loans AS (
    SELECT CUST_NO, SUM(OVDU_AMT) AS TOTAL_OVDU
    FROM TB_LNS_LOANINFO
    WHERE OVDU_YN = 'Y'
    GROUP BY CUST_NO
)
SELECT C.CUST_NM, O.TOTAL_OVDU
FROM TB_CRM_CUSTOMER C
JOIN overdue_loans O ON C.CUST_NO = O.CUST_NO
"""

_SQL_WINDOW = """
SELECT
    BRANCH_CD,
    LOAN_AMT,
    SUM(LOAN_AMT) OVER (PARTITION BY BRANCH_CD ORDER BY LOAN_DT) AS RUNNING_TOTAL,
    RANK() OVER (PARTITION BY BRANCH_CD ORDER BY LOAN_AMT DESC) AS RNK
FROM TB_LNS_LOANINFO
WHERE LOAN_STAT_CD = 'A'
"""

_SQL_MULTI_TABLE = """
SELECT A.CUST_NO, A.CUST_NM, B.ACCT_NO, C.BRANCH_NM
FROM TB_CRM_CUSTOMER A
JOIN TB_DPS_ACCOUNT B ON A.CUST_NO = B.CUST_NO
JOIN TB_BRH_BRANCH C ON B.BRANCH_CD = C.BRANCH_CD
WHERE A.CUST_STAT_CD = '1'
  AND B.ACCT_STAT_CD IN ('10', '20')
"""

_SQL_DISTINCT = """
SELECT DISTINCT CUST_GRP_CD, BRANCH_CD
FROM TB_CRM_CUSTOMER
WHERE CUST_STAT_CD = '1'
"""

_SQL_INVALID = "THIS IS NOT SQL AT ALL $$$$"

_SQL_SYBASE_LIKE = """
BEGIN
    SELECT A.CUST_NO FROM TB_CRM_CUSTOMER A WHERE A.CUST_STAT_CD = '1'
END
"""


# ════════════════════════════════════════════════════════════
# parse_sql_safe
# ════════════════════════════════════════════════════════════

class TestParseSqlSafe:
    """parse_sql_safe: 방언별 안전 파싱."""

    def test_valid_sql_returns_ast(self):
        """유효한 SQL은 AST 객체를 반환한다."""
        result = parse_sql_safe(_SQL_SIMPLE_JOIN)
        passed = result is not None
        log_test_case(logger, "parse_sql_safe_valid", _SQL_SIMPLE_JOIN[:50], "not None", result, passed)
        assert passed

    def test_invalid_sql_returns_none(self):
        """파싱 불가 SQL은 None을 반환한다 (흐름 차단 없음)."""
        result = parse_sql_safe(_SQL_INVALID)
        passed = result is None
        log_test_case(logger, "parse_sql_safe_invalid", _SQL_INVALID, None, result, passed)
        assert passed

    def test_empty_string_returns_none(self):
        """빈 문자열은 None을 반환한다."""
        result = parse_sql_safe("")
        passed = result is None
        log_test_case(logger, "parse_sql_safe_empty", "", None, result, passed)
        assert passed

    def test_cte_sql_parseable(self):
        """CTE가 포함된 SQL도 파싱된다."""
        result = parse_sql_safe(_SQL_CTE)
        passed = result is not None
        log_test_case(logger, "parse_sql_safe_cte", _SQL_CTE[:50], "not None", result, passed)
        assert passed

    def test_aggregate_sql_parseable(self):
        """GROUP BY / HAVING 포함 SQL이 파싱된다."""
        result = parse_sql_safe(_SQL_AGGREGATE)
        passed = result is not None
        log_test_case(logger, "parse_sql_safe_aggregate", _SQL_AGGREGATE[:50], "not None", result, passed)
        assert passed


# ════════════════════════════════════════════════════════════
# get_real_tables
# ════════════════════════════════════════════════════════════

class TestGetRealTables:
    """get_real_tables: AST + regex fallback 테이블 추출."""

    def test_simple_join_tables(self):
        """단순 JOIN에서 두 테이블이 추출된다."""
        tables = get_real_tables(_SQL_SIMPLE_JOIN)
        passed = "TB_CRM_CUSTOMER" in tables and "TB_LNS_LOANINFO" in tables
        log_test_case(logger, "get_real_tables_simple_join", _SQL_SIMPLE_JOIN[:50], "두 테이블 포함", tables, passed)
        assert passed

    def test_multi_join_tables(self):
        """3-테이블 JOIN에서 세 테이블이 모두 추출된다."""
        tables = get_real_tables(_SQL_MULTI_TABLE)
        passed = (
            "TB_CRM_CUSTOMER" in tables
            and "TB_DPS_ACCOUNT" in tables
            and "TB_BRH_BRANCH" in tables
        )
        log_test_case(logger, "get_real_tables_multi_join", _SQL_MULTI_TABLE[:50], "3개 테이블", tables, passed)
        assert passed

    def test_cte_alias_excluded(self):
        """CTE 별칭(overdue_loans)은 실제 테이블로 추출되지 않는다."""
        tables = get_real_tables(_SQL_CTE)
        passed = (
            "overdue_loans" not in tables
            and "TB_CRM_CUSTOMER" in tables
            and "TB_LNS_LOANINFO" in tables
        )
        log_test_case(logger, "get_real_tables_cte_alias", _SQL_CTE[:50], "CTE alias 제외", tables, passed)
        assert passed

    def test_regex_fallback_sybase(self):
        """AST 파싱 실패 시 regex fallback으로 폐쇄망 네이밍 패턴 테이블명을 추출한다.

        regex 패턴: TB_{3자리}_{7자리알파벳숫자}
        TB_CRM_CUST001 = TB_CRM(3) + CUST001(7) → 매칭됨
        """
        sql = """
        BEGIN
            SELECT A.CUST_NO FROM TB_CRM_CUST001 A WHERE A.CUST_STAT_CD = '1'
        END
        """
        tables = get_real_tables(sql)
        passed = "TB_CRM_CUST001" in tables
        log_test_case(logger, "get_real_tables_regex_fallback", sql[:50], "TB_CRM_CUST001", tables, passed)
        assert passed

    def test_comment_inside_sql_ignored(self):
        """주석 내 TB_ 패턴은 테이블로 오탐하지 않는다."""
        sql_with_comment = """
        -- TB_XXX_COMMENT 는 제외
        SELECT A.CUST_NO FROM TB_CRM_CUSTOMER A
        """
        tables = get_real_tables(sql_with_comment)
        passed = "TB_XXX_COMMENT" not in tables and "TB_CRM_CUSTOMER" in tables
        log_test_case(logger, "get_real_tables_comment", sql_with_comment[:50], "주석 오탐 없음", tables, passed)
        assert passed

    def test_empty_sql_returns_empty(self):
        """빈 SQL은 빈 리스트를 반환한다."""
        tables = get_real_tables("")
        passed = tables == []
        log_test_case(logger, "get_real_tables_empty", "", [], tables, passed)
        assert passed


# ════════════════════════════════════════════════════════════
# get_real_columns
# ════════════════════════════════════════════════════════════

class TestGetRealColumns:
    """get_real_columns: 실제 참조 컬럼 추출."""

    def test_extracts_referenced_columns(self):
        """SELECT + WHERE 절에서 참조 컬럼이 추출된다."""
        ast = parse_sql_safe(_SQL_SIMPLE_JOIN)
        assert ast is not None
        columns = get_real_columns(ast)
        passed = "CUST_NO" in columns and "CUST_STAT_CD" in columns
        log_test_case(logger, "get_real_columns_basic", _SQL_SIMPLE_JOIN[:50], "CUST_NO, CUST_STAT_CD", columns, passed)
        assert passed

    def test_excludes_wildcards(self):
        """와일드카드(*)는 컬럼 목록에서 제외된다."""
        sql = "SELECT COUNT(*) AS CNT FROM TB_CRM_CUSTOMER"
        ast = parse_sql_safe(sql)
        assert ast is not None
        columns = get_real_columns(ast)
        passed = "*" not in columns
        log_test_case(logger, "get_real_columns_no_wildcard", sql, "* 없음", columns, passed)
        assert passed

    def test_alias_excluded_from_columns(self):
        """SELECT alias(TOTAL_AMT)는 컬럼 목록에 포함되지 않는다."""
        ast = parse_sql_safe(_SQL_AGGREGATE)
        assert ast is not None
        columns = get_real_columns(ast)
        # TOTAL_AMT는 alias이므로 컬럼으로 오탐하면 안 됨
        passed = "TOTAL_AMT" not in columns
        log_test_case(logger, "get_real_columns_alias_excluded", _SQL_AGGREGATE[:50], "TOTAL_AMT 없음", columns, passed)
        assert passed

    def test_returns_sorted(self):
        """컬럼 목록은 정렬되어 반환된다."""
        ast = parse_sql_safe(_SQL_SIMPLE_JOIN)
        assert ast is not None
        columns = get_real_columns(ast)
        passed = columns == sorted(columns)
        log_test_case(logger, "get_real_columns_sorted", _SQL_SIMPLE_JOIN[:50], "정렬된 리스트", columns, passed)
        assert passed


# ════════════════════════════════════════════════════════════
# extract_select_alias_map
# ════════════════════════════════════════════════════════════

class TestExtractSelectAliasMap:
    """extract_select_alias_map: SELECT alias → 원본컬럼 매핑."""

    def test_alias_to_column_mapping(self):
        """AS alias가 있으면 alias → 원본컬럼명으로 매핑된다."""
        sql = "SELECT A.LOAN_DCD AS 대출구분, A.LOAN_AMT AS 대출금액 FROM TB_LNS_LOANINFO A"
        result = extract_select_alias_map(sql)
        passed = result.get("대출구분") == "LOAN_DCD" and result.get("대출금액") == "LOAN_AMT"
        log_test_case(logger, "alias_map_basic", sql, {"대출구분": "LOAN_DCD"}, result, passed)
        assert passed

    def test_aggregate_alias_maps_to_none(self):
        """COUNT(*) AS 건수 — 집계함수는 원본컬럼이 None이다."""
        sql = "SELECT COUNT(*) AS 건수 FROM TB_CRM_CUSTOMER"
        result = extract_select_alias_map(sql)
        passed = "건수" in result and result["건수"] is None
        log_test_case(logger, "alias_map_aggregate", sql, {"건수": None}, result, passed)
        assert passed

    def test_no_alias_maps_column_to_itself(self):
        """alias 없는 컬럼은 컬럼명 자체가 키이자 값이다."""
        sql = "SELECT CUST_NO FROM TB_CRM_CUSTOMER"
        result = extract_select_alias_map(sql)
        passed = result.get("CUST_NO") == "CUST_NO"
        log_test_case(logger, "alias_map_no_alias", sql, {"CUST_NO": "CUST_NO"}, result, passed)
        assert passed

    def test_invalid_sql_returns_empty(self):
        """파싱 불가 SQL은 빈 dict를 반환한다."""
        result = extract_select_alias_map(_SQL_INVALID)
        passed = result == {}
        log_test_case(logger, "alias_map_invalid", _SQL_INVALID, {}, result, passed)
        assert passed


# ════════════════════════════════════════════════════════════
# extract_structural_hints
# ════════════════════════════════════════════════════════════

class TestExtractStructuralHints:
    """extract_structural_hints: 12가지 구조 힌트 추출."""

    def test_simple_join_hints(self):
        """단순 JOIN SQL에서 join_patterns와 source_tables가 추출된다."""
        hints = extract_structural_hints(_SQL_SIMPLE_JOIN)
        passed = (
            len(hints.get("join_patterns", [])) > 0
            and len(hints.get("source_tables", [])) >= 2
        )
        log_test_case(logger, "hints_simple_join", _SQL_SIMPLE_JOIN[:50], "join_patterns+source_tables", hints, passed)
        assert passed

    def test_join_pattern_resolves_alias(self):
        """join_patterns에서 alias가 실제 테이블명으로 치환된다."""
        hints = extract_structural_hints(_SQL_SIMPLE_JOIN)
        patterns = hints.get("join_patterns", [])
        # A → TB_CRM_CUSTOMER, B → TB_LNS_LOANINFO 로 치환
        has_real_tables = any(
            "TB_CRM_CUSTOMER" in p or "TB_LNS_LOANINFO" in p
            for p in patterns
        )
        log_test_case(logger, "hints_join_alias_resolved", _SQL_SIMPLE_JOIN[:50], "실제 테이블명 포함", patterns, has_real_tables)
        assert has_real_tables

    def test_aggregate_hints(self):
        """집계 SQL에서 agg_expressions, group_by_columns, has_having이 추출된다."""
        hints = extract_structural_hints(_SQL_AGGREGATE)
        passed = (
            len(hints.get("agg_expressions", [])) > 0
            and len(hints.get("group_by_columns", [])) > 0
            and hints.get("has_having") is True
        )
        log_test_case(logger, "hints_aggregate", _SQL_AGGREGATE[:50], "agg+group+having", hints, passed)
        assert passed

    def test_order_by_and_limit(self):
        """ORDER BY와 LIMIT이 힌트에 포함된다."""
        hints = extract_structural_hints(_SQL_AGGREGATE)
        passed = (
            len(hints.get("order_by_columns", [])) > 0
            and hints.get("limit_value") == 20
        )
        log_test_case(logger, "hints_order_limit", _SQL_AGGREGATE[:50], "order_by+limit=20", hints, passed)
        assert passed

    def test_subquery_detection(self):
        """서브쿼리가 포함된 SQL에서 has_subquery=True."""
        hints = extract_structural_hints(_SQL_SUBQUERY)
        passed = hints.get("has_subquery") is True
        log_test_case(logger, "hints_subquery", _SQL_SUBQUERY[:50], "has_subquery=True", hints, passed)
        assert passed

    def test_distinct_detection(self):
        """DISTINCT 사용 시 has_distinct=True."""
        hints = extract_structural_hints(_SQL_DISTINCT)
        passed = hints.get("has_distinct") is True
        log_test_case(logger, "hints_distinct", _SQL_DISTINCT[:50], "has_distinct=True", hints, passed)
        assert passed

    def test_date_filter_detection(self):
        """날짜 컬럼 필터가 date_filters에 추출된다."""
        hints = extract_structural_hints(_SQL_AGGREGATE)
        date_filters = hints.get("date_filters", [])
        has_date = any(
            f.get("column", "").upper().endswith("DT") or "DT" in f.get("column", "")
            for f in date_filters
        )
        log_test_case(logger, "hints_date_filter", _SQL_AGGREGATE[:50], "날짜 필터 포함", date_filters, has_date)
        assert has_date

    def test_code_column_extraction(self):
        """WHERE col = '값' 패턴에서 code_columns가 추출된다."""
        hints = extract_structural_hints(_SQL_SIMPLE_JOIN)
        code_cols = hints.get("code_columns", {})
        passed = "CUST_STAT_CD" in code_cols and "1" in code_cols.get("CUST_STAT_CD", [])
        log_test_case(logger, "hints_code_columns", _SQL_SIMPLE_JOIN[:50], "CUST_STAT_CD=['1']", code_cols, passed)
        assert passed

    def test_invalid_sql_returns_empty_or_partial(self):
        """파싱 불가 SQL은 빈 dict 또는 테이블만 포함한 dict를 반환한다."""
        hints = extract_structural_hints(_SQL_INVALID)
        passed = isinstance(hints, dict)
        log_test_case(logger, "hints_invalid", _SQL_INVALID, "dict 타입", hints, passed)
        assert passed

    def test_cte_tables_correct(self):
        """CTE SQL에서 CTE 별칭이 아닌 실제 테이블만 source_tables에 포함된다."""
        hints = extract_structural_hints(_SQL_CTE)
        tables = hints.get("source_tables", [])
        passed = "overdue_loans" not in tables and "TB_CRM_CUSTOMER" in tables
        log_test_case(logger, "hints_cte_tables", _SQL_CTE[:50], "CTE alias 제외", tables, passed)
        assert passed

    def test_window_function_sql(self):
        """윈도우 함수 SQL도 파싱 가능하며 source_tables를 반환한다."""
        hints = extract_structural_hints(_SQL_WINDOW)
        passed = len(hints.get("source_tables", [])) > 0
        log_test_case(logger, "hints_window_function", _SQL_WINDOW[:50], "source_tables 있음", hints, passed)
        assert passed

    def test_in_clause_code_columns(self):
        """IN (val1, val2) 패턴도 code_columns에 추출된다."""
        hints = extract_structural_hints(_SQL_MULTI_TABLE)
        code_cols = hints.get("code_columns", {})
        passed = "ACCT_STAT_CD" in code_cols
        log_test_case(logger, "hints_in_clause", _SQL_MULTI_TABLE[:50], "ACCT_STAT_CD in code_cols", code_cols, passed)
        assert passed


# ════════════════════════════════════════════════════════════
# merge_hints
# ════════════════════════════════════════════════════════════

class TestMergeHints:
    """merge_hints: 다수 힌트 dict 병합."""

    def test_deduplicates_source_tables(self):
        """동일 테이블이 두 힌트에 있으면 한 번만 포함된다."""
        h1 = {"source_tables": ["TB_CRM_CUSTOMER", "TB_LNS_LOANINFO"]}
        h2 = {"source_tables": ["TB_LNS_LOANINFO", "TB_DPS_ACCOUNT"]}
        merged = merge_hints([h1, h2])
        tables = merged["source_tables"]
        passed = tables.count("TB_LNS_LOANINFO") == 1
        log_test_case(logger, "merge_dedup_tables", [h1, h2], "중복 없음", tables, passed)
        assert passed

    def test_deduplicates_join_patterns(self):
        """동일 조인 조건(순서 무관)은 한 번만 병합된다."""
        h1 = {"join_patterns": ["TB_CRM_CUSTOMER.CUST_NO = TB_LNS_LOANINFO.CUST_NO"]}
        h2 = {"join_patterns": ["TB_LNS_LOANINFO.CUST_NO = TB_CRM_CUSTOMER.CUST_NO"]}
        merged = merge_hints([h1, h2])
        passed = len(merged["join_patterns"]) == 1
        log_test_case(logger, "merge_dedup_joins", [h1, h2], "join 1개", merged["join_patterns"], passed)
        assert passed

    def test_merges_code_columns(self):
        """code_columns는 값 목록이 합쳐지고 중복은 제거된다."""
        h1 = {"code_columns": {"LOAN_STAT_CD": ["01", "02"]}}
        h2 = {"code_columns": {"LOAN_STAT_CD": ["02", "03"]}}
        merged = merge_hints([h1, h2])
        vals = merged["code_columns"]["LOAN_STAT_CD"]
        passed = set(vals) == {"01", "02", "03"}
        log_test_case(logger, "merge_code_columns", [h1, h2], "{01,02,03}", vals, passed)
        assert passed

    def test_has_flags_any_true(self):
        """has_distinct/has_subquery/has_having는 어느 하나라도 True면 True."""
        h1 = {"has_distinct": False, "has_subquery": False, "has_having": False}
        h2 = {"has_distinct": True, "has_subquery": False, "has_having": False}
        merged = merge_hints([h1, h2])
        passed = merged["has_distinct"] is True
        log_test_case(logger, "merge_flags", [h1, h2], "has_distinct=True", merged, passed)
        assert passed

    def test_limit_takes_first(self):
        """limit_value는 처음 등장한 값이 사용된다."""
        h1 = {"limit_value": 100}
        h2 = {"limit_value": 50}
        merged = merge_hints([h1, h2])
        passed = merged["limit_value"] == 100
        log_test_case(logger, "merge_limit_first", [h1, h2], 100, merged["limit_value"], passed)
        assert passed

    def test_empty_hints_list(self):
        """빈 힌트 리스트는 기본 구조를 가진 dict를 반환한다."""
        merged = merge_hints([])
        passed = isinstance(merged, dict) and merged["source_tables"] == []
        log_test_case(logger, "merge_empty", [], "빈 구조 dict", merged, passed)
        assert passed

    def test_none_hint_skipped(self):
        """빈 dict 힌트는 건너뛴다."""
        h1 = {}
        h2 = {"source_tables": ["TB_CRM_CUSTOMER"]}
        merged = merge_hints([h1, h2])
        passed = "TB_CRM_CUSTOMER" in merged["source_tables"]
        log_test_case(logger, "merge_skip_empty_hint", [h1, h2], "TB_CRM_CUSTOMER 포함", merged, passed)
        assert passed

    def test_full_merge_from_real_sql(self):
        """실제 SQL 두 개의 힌트를 병합했을 때 구조적 완결성을 갖는다."""
        h1 = extract_structural_hints(_SQL_SIMPLE_JOIN)
        h2 = extract_structural_hints(_SQL_AGGREGATE)
        merged = merge_hints([h1, h2])
        required_keys = {
            "join_patterns", "code_columns", "agg_expressions",
            "date_filters", "source_tables", "select_columns",
            "group_by_columns", "order_by_columns", "limit_value",
            "has_distinct", "has_subquery", "has_having",
        }
        passed = required_keys.issubset(merged.keys())
        log_test_case(logger, "merge_full_real_sql", "두 SQL 힌트", "12개 키 포함", list(merged.keys()), passed)
        assert passed
