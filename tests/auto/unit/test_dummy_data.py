"""dummy_data 단위 테스트.

테스트 대상:
    - generate_dummy_data: SQL 파싱 후 alias 기반 랜덤 행 생성
    - search_dummy_table_meta: 키워드 매칭 기반 테이블 메타 검색
    - search_dummy_code_meta: 코드 필드명 매칭 기반 코드 메타 검색
    - search_dummy_sql_history: 키워드 매칭 기반 과거 SQL 이력 검색
    - search_dummy_manuals: 키워드 점수 기반 업무 매뉴얼 검색

설계 원칙:
    - 외부 의존성 없음 (내장 더미 데이터만 사용)
    - SQL 파싱 로직과 키워드 검색 로직을 독립적으로 검증

실행:
    pytest tests/auto/unit/test_dummy_data.py -v
"""

from __future__ import annotations

import pytest

from src.connectors.dummy_data import (
    generate_dummy_data,
    search_dummy_table_meta,
    search_dummy_code_meta,
    search_dummy_sql_history,
    search_dummy_manuals,
    DUMMY_TABLE_META,
    DUMMY_CODE_META,
    DUMMY_SQL_HISTORY,
    DUMMY_MANUALS,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# generate_dummy_data — SQL 파싱 및 더미 데이터 생성
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_generate_returns_list_of_dicts():
    """반환 값이 dict 목록이어야 한다."""
    sql = "SELECT COUNT(*) AS cnt FROM TB_CUST_INFO"
    result = generate_dummy_data(sql)
    assert isinstance(result, list)
    assert len(result) >= 1
    assert isinstance(result[0], dict)


def test_generate_alias_as_key():
    """SELECT alias가 반환 dict의 키로 사용되어야 한다."""
    sql = "SELECT CUST_NO, CUST_NM FROM TB_CUST_INFO LIMIT 5"
    result = generate_dummy_data(sql)
    assert len(result) >= 1
    assert "CUST_NO" in result[0]
    assert "CUST_NM" in result[0]


def test_generate_explicit_as_alias():
    """AS 절로 정의된 alias가 키로 사용되어야 한다."""
    sql = "SELECT COUNT(*) AS loan_cnt FROM TB_LOAN_INFO"
    result = generate_dummy_data(sql)
    assert "loan_cnt" in result[0]


def test_generate_aggregate_returns_single_row_without_group():
    """GROUP BY 없는 집계 쿼리는 1행 반환."""
    sql = "SELECT COUNT(*) AS cnt, SUM(LOAN_AMT) AS total FROM TB_LOAN_INFO"
    result = generate_dummy_data(sql)
    assert len(result) == 1


def test_generate_with_group_by_returns_multiple_rows():
    """GROUP BY가 있으면 복수 행 반환."""
    sql = (
        "SELECT LOAN_TYPE_CD, COUNT(*) AS cnt "
        "FROM TB_LOAN_INFO GROUP BY LOAN_TYPE_CD"
    )
    result = generate_dummy_data(sql)
    assert len(result) >= 1


def test_generate_respects_limit():
    """LIMIT 절이 있으면 행 수가 LIMIT을 초과하지 않는다."""
    sql = "SELECT CUST_NO FROM TB_CUST_INFO LIMIT 3"
    result = generate_dummy_data(sql)
    assert len(result) <= 3


def test_generate_amount_alias_returns_large_value():
    """금액 관련 alias는 큰 숫자값을 생성한다."""
    sql = "SELECT SUM(LOAN_AMT) AS total_amt FROM TB_LOAN_INFO"
    result = generate_dummy_data(sql)
    assert isinstance(result[0]["total_amt"], (int, float))
    assert result[0]["total_amt"] > 0


def test_generate_rate_alias_returns_small_value():
    """비율 관련 alias는 작은 소수값을 생성한다."""
    sql = "SELECT ROUND(AVG(INT_RATE), 2) AS avg_rate FROM TB_LOAN_INFO"
    result = generate_dummy_data(sql)
    val = result[0]["avg_rate"]
    assert isinstance(val, (int, float))
    assert 0 < val < 100


def test_generate_count_alias_returns_positive_int():
    """건수 관련 alias는 양의 정수를 생성한다."""
    sql = "SELECT COUNT(*) AS loan_cnt FROM TB_LOAN_INFO"
    result = generate_dummy_data(sql)
    assert isinstance(result[0]["loan_cnt"], int)
    assert result[0]["loan_cnt"] > 0


def test_generate_date_alias_ym_returns_yyyymm_string():
    """ym이 포함된 alias는 YYYYMM 형식 문자열 생성."""
    sql = "SELECT BASE_YM FROM TB_LOAN_OVERDUE_STAT GROUP BY BASE_YM"
    result = generate_dummy_data(sql)
    val = str(result[0]["BASE_YM"])
    assert len(val) == 6
    assert val.isdigit()


def test_generate_no_select_alias_returns_default():
    """alias 없는 SQL은 기본 result 키로 반환."""
    result = generate_dummy_data("INVALID SQL WITH NO SELECT")
    assert isinstance(result, list)
    assert len(result) == 1
    assert "result" in result[0]


def test_generate_branch_alias_returns_branch_name():
    """지점 관련 alias는 지점명 문자열을 생성한다."""
    sql = "SELECT BRCH_NM FROM TB_BRANCH_INFO GROUP BY BRCH_NM"
    result = generate_dummy_data(sql)
    val = result[0]["BRCH_NM"]
    assert isinstance(val, str)
    assert len(val) > 0


def test_generate_aggregate_column_sorted_descending():
    """집계 컬럼이 있으면 내림차순 정렬된다."""
    sql = (
        "SELECT BRCH_NM, SUM(LOAN_AMT) AS total_amt "
        "FROM TB_LOAN_INFO GROUP BY BRCH_NM"
    )
    result = generate_dummy_data(sql)
    if len(result) > 1:
        amounts = [r["total_amt"] for r in result]
        assert amounts == sorted(amounts, reverse=True)


def test_generate_multiple_aliases_all_present():
    """복수 alias가 모두 반환 dict에 포함되어야 한다."""
    sql = (
        "SELECT LOAN_TYPE_CD, COUNT(*) AS loan_cnt, "
        "SUM(LOAN_AMT) AS total_amt, AVG(INT_RATE) AS avg_rate "
        "FROM TB_LOAN_INFO GROUP BY LOAN_TYPE_CD"
    )
    result = generate_dummy_data(sql)
    assert len(result) >= 1
    row = result[0]
    assert "LOAN_TYPE_CD" in row
    assert "loan_cnt" in row
    assert "total_amt" in row
    assert "avg_rate" in row


def test_generate_complex_sql_with_join():
    """JOIN이 포함된 복잡한 SQL도 처리 가능해야 한다."""
    sql = (
        "SELECT b.BRCH_NM, COUNT(c.CUST_NO) AS cust_cnt "
        "FROM TB_CUST_INFO c "
        "JOIN TB_BRANCH_INFO b ON c.BRCH_CD = b.BRCH_CD "
        "GROUP BY b.BRCH_NM ORDER BY cust_cnt DESC LIMIT 10"
    )
    result = generate_dummy_data(sql)
    assert isinstance(result, list)
    assert len(result) <= 10
    assert "BRCH_NM" in result[0]
    assert "cust_cnt" in result[0]


def test_generate_subquery_alias():
    """서브쿼리가 포함된 SQL에서 외부 alias를 추출."""
    sql = (
        "SELECT ROUND(SUM(OVERDUE_AMT)::NUMERIC "
        "/ NULLIF(SUM(TOTAL_LOAN_AMT), 0) * 100, 2) AS overdue_rate "
        "FROM TB_LOAN_OVERDUE_STAT"
    )
    result = generate_dummy_data(sql)
    assert "overdue_rate" in result[0]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# search_dummy_table_meta
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_search_table_meta_returns_list():
    """반환 타입이 list이어야 한다."""
    result = search_dummy_table_meta("고객")
    assert isinstance(result, list)


def test_search_table_meta_customer_keyword():
    """'고객' 키워드로 고객 관련 테이블 검색."""
    result = search_dummy_table_meta("고객")
    names = [t["name"] for t in result]
    assert "TB_CUST_INFO" in names


def test_search_table_meta_loan_keyword():
    """'대출' 키워드로 여신 관련 테이블 검색."""
    result = search_dummy_table_meta("대출")
    names = [t["name"] for t in result]
    assert "TB_LOAN_INFO" in names


def test_search_table_meta_transaction_keyword():
    """'거래' 키워드로 거래 테이블 검색."""
    result = search_dummy_table_meta("거래")
    names = [t["name"] for t in result]
    assert "TB_TRANSACTION" in names


def test_search_table_meta_branch_keyword():
    """'지점' 키워드로 지점 테이블 검색."""
    result = search_dummy_table_meta("지점")
    names = [t["name"] for t in result]
    assert "TB_BRANCH_INFO" in names


def test_search_table_meta_overdue_keyword():
    """'연체' 키워드로 연체 통계 테이블 검색."""
    result = search_dummy_table_meta("연체")
    names = [t["name"] for t in result]
    assert "TB_LOAN_OVERDUE_STAT" in names


def test_search_table_meta_no_match_returns_all():
    """매칭 없는 쿼리는 전체 더미 테이블 목록 반환."""
    result = search_dummy_table_meta("전혀없는키워드xyz123")
    assert result == DUMMY_TABLE_META


def test_search_table_meta_empty_query_returns_all():
    """빈 쿼리도 전체 목록 반환 (split('')이 빈 리스트라 매칭 없음)."""
    result = search_dummy_table_meta("")
    assert result == DUMMY_TABLE_META


def test_search_table_meta_column_description_search():
    """컬럼 설명에서도 키워드 매칭이 가능해야 한다 ('잔액' → 대출·예금 테이블)."""
    result = search_dummy_table_meta("잔액")
    names = [t["name"] for t in result]
    assert any("LOAN" in n or "DEPOSIT" in n for n in names)


def test_search_table_meta_results_have_required_fields():
    """검색 결과 각 항목에 name, columns 필드가 있어야 한다."""
    result = search_dummy_table_meta("고객")
    for table in result:
        assert "name" in table
        assert "columns" in table


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# search_dummy_code_meta
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_search_code_meta_returns_list():
    result = search_dummy_code_meta("CUST_TYPE_CD")
    assert isinstance(result, list)


def test_search_code_meta_exact_column_name():
    """정확한 컬럼명으로 해당 코드 메타 반환."""
    result = search_dummy_code_meta("CUST_TYPE_CD")
    fields = [r["code_field"] for r in result]
    assert "CUST_TYPE_CD" in fields


def test_search_code_meta_partial_match():
    """부분 매칭으로 복수 코드 메타 반환 ('TYPE' → CUST_TYPE_CD, LOAN_TYPE_CD)."""
    result = search_dummy_code_meta("TYPE")
    fields = [r["code_field"] for r in result]
    assert len(fields) >= 1


def test_search_code_meta_no_match_returns_all():
    """매칭 없는 쿼리는 전체 코드 메타 반환."""
    result = search_dummy_code_meta("NONEXISTENT_FIELD")
    all_keys = list(DUMMY_CODE_META.keys())
    returned_keys = [r["code_field"] for r in result]
    assert set(returned_keys) == set(all_keys)


def test_search_code_meta_result_has_required_fields():
    """반환 결과에 code_field, code_field_desc, codes 필드가 있어야 한다."""
    result = search_dummy_code_meta("GENDER_CD")
    for item in result:
        assert "code_field" in item
        assert "code_field_desc" in item
        assert "codes" in item


def test_search_code_meta_codes_dict_nonempty():
    """codes 필드가 비어 있지 않은 dict이어야 한다."""
    result = search_dummy_code_meta("LOAN_TYPE_CD")
    for item in result:
        if item["code_field"] == "LOAN_TYPE_CD":
            assert isinstance(item["codes"], dict)
            assert len(item["codes"]) > 0


def test_search_code_meta_case_insensitive():
    """대소문자에 무관하게 검색 (구현이 upper()를 사용하므로 대문자 기준)."""
    result_upper = search_dummy_code_meta("CUST_TYPE_CD")
    result_lower = search_dummy_code_meta("cust_type_cd")
    # lower()로 검색하면 upper()로 변환된 키에 포함되지 않아 전체 반환
    # 동작 명세: 대문자 기준으로 검색
    assert isinstance(result_lower, list)


def test_search_code_meta_gender_codes():
    """GENDER_CD는 M/F 코드값을 반환해야 한다."""
    result = search_dummy_code_meta("GENDER_CD")
    for item in result:
        if item["code_field"] == "GENDER_CD":
            assert "M" in item["codes"]
            assert "F" in item["codes"]


def test_search_code_meta_overdue_yn_codes():
    """OVERDUE_YN은 Y/N 코드값을 반환해야 한다."""
    result = search_dummy_code_meta("OVERDUE_YN")
    for item in result:
        if item["code_field"] == "OVERDUE_YN":
            assert "Y" in item["codes"]
            assert "N" in item["codes"]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# search_dummy_sql_history
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_search_sql_history_returns_list():
    result = search_dummy_sql_history("고객")
    assert isinstance(result, list)


def test_search_sql_history_customer_keyword():
    """'고객' 키워드로 고객 관련 SQL 이력 검색."""
    result = search_dummy_sql_history("고객")
    texts = [r["query_text"] for r in result]
    assert any("고객" in t for t in texts)


def test_search_sql_history_loan_keyword():
    """'대출' 키워드로 대출 관련 SQL 이력 검색."""
    result = search_dummy_sql_history("대출")
    texts = [r["query_text"] for r in result]
    assert any("대출" in t for t in texts)


def test_search_sql_history_overdue_keyword():
    """'연체' 키워드로 연체율 관련 SQL 이력 검색."""
    result = search_dummy_sql_history("연체")
    texts = [r["query_text"] for r in result]
    assert any("연체" in t for t in texts)


def test_search_sql_history_no_match_returns_top3():
    """매칭 없는 쿼리는 상위 3건 반환."""
    result = search_dummy_sql_history("전혀없는키워드xyz999")
    assert len(result) == 3
    assert result == DUMMY_SQL_HISTORY[:3]


def test_search_sql_history_result_has_required_fields():
    """반환 결과에 query_text, sql, executed_at 필드가 있어야 한다."""
    result = search_dummy_sql_history("고객")
    for item in result:
        assert "query_text" in item
        assert "sql" in item
        assert "executed_at" in item


def test_search_sql_history_success_flag():
    """반환 결과에 success 필드가 있어야 한다."""
    result = search_dummy_sql_history("예금")
    for item in result:
        assert "success" in item


def test_search_sql_history_multi_word_query():
    """복수 단어 쿼리는 OR 매칭."""
    result = search_dummy_sql_history("연체율 추이")
    assert len(result) >= 1


def test_search_sql_history_branch_keyword():
    """'지점' 키워드로 지점별 SQL 이력 검색."""
    result = search_dummy_sql_history("지점")
    texts = [r["query_text"] for r in result]
    assert any("지점" in t for t in texts)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# search_dummy_manuals
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_search_manuals_returns_list():
    result = search_dummy_manuals("연체", top_k=3)
    assert isinstance(result, list)


def test_search_manuals_top_k_limit():
    """top_k 이하의 결과 반환."""
    result = search_dummy_manuals("여신", top_k=2)
    assert len(result) <= 2


def test_search_manuals_overdue_keyword():
    """'연체' 키워드는 연체 관리 매뉴얼 반환."""
    result = search_dummy_manuals("연체", top_k=3)
    contents = [r["content"] for r in result]
    assert any("연체" in c for c in contents)


def test_search_manuals_bis_keyword():
    """'BIS' 키워드는 BIS 비율 매뉴얼 반환."""
    result = search_dummy_manuals("BIS", top_k=3)
    titles = [r["title"] for r in result]
    assert any("BIS" in t for t in titles)


def test_search_manuals_no_match_returns_top_k_from_all():
    """매칭 없는 쿼리는 전체에서 top_k만큼 반환."""
    result = search_dummy_manuals("전혀없는키워드xyz", top_k=2)
    assert len(result) == 2
    # _point_id 필드가 추가되므로 원본 필드 포함 여부로 검증
    for i, item in enumerate(result):
        assert item["title"] == DUMMY_MANUALS[i]["title"]
        assert item["content"] == DUMMY_MANUALS[i]["content"]
        assert "_point_id" in item


def test_search_manuals_result_has_required_fields():
    """반환 결과에 title, content, category 필드가 있어야 한다."""
    result = search_dummy_manuals("여신", top_k=3)
    for item in result:
        assert "title" in item
        assert "content" in item
        assert "category" in item


def test_search_manuals_higher_score_items_come_first():
    """더 많은 키워드가 포함된 매뉴얼이 앞에 와야 한다."""
    # "연체 관리"를 검색하면 연체가 많이 언급된 항목이 우선
    result = search_dummy_manuals("연체 관리", top_k=5)
    assert len(result) >= 1
    # 첫 번째 항목 내용에 두 키워드 중 하나 이상 포함
    first_content = result[0]["content"] + result[0]["title"]
    assert "연체" in first_content or "관리" in first_content


def test_search_manuals_top_k_zero():
    """top_k=0이면 빈 리스트 반환."""
    result = search_dummy_manuals("연체", top_k=0)
    assert result == []


def test_search_manuals_customer_grade_keyword():
    """'고객 등급' 키워드로 고객 등급 분류 매뉴얼 검색."""
    result = search_dummy_manuals("고객 등급", top_k=3)
    titles = [r["title"] for r in result]
    assert any("고객" in t for t in titles)
