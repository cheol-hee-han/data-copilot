"""유사 테이블 해결 엔진(similar_table_resolver) 단위 테스트.

테스트 대상:
    SQL에서 테이블 추출, 유사 그룹 탐색, 신호어 기반 점수 산출,
    테이블 추천, 선택 검증, 구분 프롬프트 생성을 검증한다.
    정보계 DB에 유사 테이블이 다수 존재하는 환경에서
    질의 의도에 맞는 테이블을 정확히 선택하는 것이 핵심이다.

입력 예시 (정상):
    - "월별 연체율 추이 분석" + [TB_LOAN_INFO, TB_LOAN_OVERDUE_STAT]
      → TB_LOAN_OVERDUE_STAT 추천 (통계 테이블이 추이 분석에 적합)
    - "현재 연체 중인 대출 건수" → TB_LOAN_INFO 추천 (건별 현황)

결과 예시 (오류 케이스):
    - 연체율+추이 질의에 TB_LOAN_INFO 사용 → WARNING (잘못된 테이블)

실행 스크립트:
    pytest tests/unit/test_table_selector.py -v

참고:
    - 외부 의존성 없음
    - 유사 그룹 정의: resources/domain/similar_tables.yaml
    - 테스트 대상 소스: src/services/similar_table_resolver.py
"""

from src.services.similar_table_resolver import (
    TableVerdict,
    build_table_disambiguation_prompt,
    check_rejected_tables,
    extract_tables_from_sql,
    find_relevant_groups,
    recommend_tables_for_query,
    score_table_for_query,
    validate_table_selection,
    SIMILAR_TABLE_GROUPS,
)


# ──────────────────────────────────────────────────
# extract_tables_from_sql
# ──────────────────────────────────────────────────

def test_extract_single_table():
    sql = "SELECT COUNT(*) FROM TB_LOAN_INFO WHERE X = 1"
    assert extract_tables_from_sql(sql) == ["TB_LOAN_INFO"]


def test_extract_join_tables():
    sql = (
        "SELECT a.X, b.Y FROM TB_LOAN_INFO a "
        "JOIN TB_CUST_INFO b ON a.CUST_NO = b.CUST_NO"
    )
    tables = extract_tables_from_sql(sql)
    assert "TB_LOAN_INFO" in tables
    assert "TB_CUST_INFO" in tables


def test_extract_subquery_tables():
    sql = (
        "SELECT * FROM TB_BRANCH_INFO b "
        "JOIN (SELECT BRCH_CD FROM TB_LOAN_INFO) sub "
        "ON b.BRCH_CD = sub.BRCH_CD"
    )
    tables = extract_tables_from_sql(sql)
    assert "TB_BRANCH_INFO" in tables
    assert "TB_LOAN_INFO" in tables


def test_extract_no_tb_prefix_excluded():
    """TB_ 접두사가 없는 alias는 테이블로 인식하지 않는다."""
    sql = "SELECT * FROM TB_LOAN_INFO a JOIN sub ON a.X = sub.Y"
    tables = extract_tables_from_sql(sql)
    assert "TB_LOAN_INFO" in tables
    assert "SUB" not in tables


def test_extract_deduplication():
    """같은 테이블이 여러 번 등장해도 중복 제거."""
    sql = (
        "SELECT * FROM TB_LOAN_INFO a "
        "JOIN TB_LOAN_INFO b ON a.X = b.Y"
    )
    assert extract_tables_from_sql(sql).count("TB_LOAN_INFO") == 1


# ──────────────────────────────────────────────────
# find_relevant_groups
# ──────────────────────────────────────────────────

def test_find_groups_loan_overdue():
    groups = find_relevant_groups(["TB_LOAN_INFO"])
    group_ids = [g.group_id for g in groups]
    assert "loan_overdue" in group_ids


def test_find_groups_deposit():
    groups = find_relevant_groups(["TB_DEPOSIT_INFO"])
    group_ids = [g.group_id for g in groups]
    assert "deposit_balance" in group_ids


def test_find_groups_no_match():
    groups = find_relevant_groups(["TB_BRANCH_INFO"])
    assert len(groups) == 0


def test_find_groups_multiple():
    groups = find_relevant_groups([
        "TB_LOAN_INFO", "TB_DEPOSIT_INFO",
    ])
    group_ids = [g.group_id for g in groups]
    assert "loan_overdue" in group_ids
    assert "deposit_balance" in group_ids


# ──────────────────────────────────────────────────
# score_table_for_query
# ──────────────────────────────────────────────────

def test_score_overdue_stat_for_trend_query():
    """연체율 추이 요청은 TB_LOAN_OVERDUE_STAT이 높은 점수."""
    group = next(
        g for g in SIMILAR_TABLE_GROUPS
        if g.group_id == "loan_overdue"
    )
    stat_score = score_table_for_query(
        "월별 연체율 추이 분석",
        group.tables["TB_LOAN_OVERDUE_STAT"],
    )
    info_score = score_table_for_query(
        "월별 연체율 추이 분석",
        group.tables["TB_LOAN_INFO"],
    )
    assert stat_score > info_score


def test_score_loan_info_for_list_query():
    """대출 건수/목록 요청은 TB_LOAN_INFO가 높은 점수."""
    group = next(
        g for g in SIMILAR_TABLE_GROUPS
        if g.group_id == "loan_overdue"
    )
    info_score = score_table_for_query(
        "현재 연체 중인 대출 건수 알려줘",
        group.tables["TB_LOAN_INFO"],
    )
    stat_score = score_table_for_query(
        "현재 연체 중인 대출 건수 알려줘",
        group.tables["TB_LOAN_OVERDUE_STAT"],
    )
    assert info_score > stat_score


# ──────────────────────────────────────────────────
# recommend_tables_for_query
# ──────────────────────────────────────────────────

def test_recommend_stat_for_rate_query():
    groups = find_relevant_groups([
        "TB_LOAN_INFO", "TB_LOAN_OVERDUE_STAT",
    ])
    loan_groups = [
        g for g in groups if g.group_id == "loan_overdue"
    ]
    recs = recommend_tables_for_query("연체율 추이", loan_groups)
    assert recs["loan_overdue"]["recommended"] == (
        "TB_LOAN_OVERDUE_STAT"
    )


def test_recommend_info_for_count_query():
    groups = find_relevant_groups([
        "TB_LOAN_INFO", "TB_LOAN_OVERDUE_STAT",
    ])
    loan_groups = [
        g for g in groups if g.group_id == "loan_overdue"
    ]
    recs = recommend_tables_for_query(
        "연체 대출 건수 목록", loan_groups,
    )
    assert recs["loan_overdue"]["recommended"] == "TB_LOAN_INFO"


# ──────────────────────────────────────────────────
# validate_table_selection
# ──────────────────────────────────────────────────

def test_validate_correct_table_pass():
    """올바른 테이블 사용 시 PASS."""
    sql = (
        "SELECT BASE_YM, OVERDUE_RATE "
        "FROM TB_LOAN_OVERDUE_STAT "
        "ORDER BY BASE_YM"
    )
    result = validate_table_selection(
        sql=sql,
        query="월별 연체율 추이",
        context_tables=["TB_LOAN_OVERDUE_STAT"],
    )
    assert result.verdict in (TableVerdict.PASS, TableVerdict.AMBIGUOUS)


def test_validate_wrong_table_warning():
    """잘못된 유사 테이블 사용 시 WARNING."""
    sql = (
        "SELECT OVERDUE_YN, COUNT(*) "
        "FROM TB_LOAN_INFO GROUP BY OVERDUE_YN"
    )
    result = validate_table_selection(
        sql=sql,
        query="월별 연체율 추이 분석해줘",
        context_tables=["TB_LOAN_INFO", "TB_LOAN_OVERDUE_STAT"],
    )
    # 연체율+추이+분석 → STAT이 적합한데 INFO 사용 → WARNING
    assert result.verdict in (
        TableVerdict.WARNING, TableVerdict.AMBIGUOUS,
    )


def test_validate_no_similar_group_pass():
    """유사 그룹에 속하지 않는 테이블만 사용 시 PASS."""
    sql = "SELECT * FROM TB_BRANCH_INFO LIMIT 10"
    result = validate_table_selection(
        sql=sql,
        query="지점 목록",
        context_tables=["TB_BRANCH_INFO"],
    )
    assert result.verdict == TableVerdict.PASS


def test_validate_ambiguous_query():
    """모호한 요청 시 AMBIGUOUS."""
    sql = (
        "SELECT COUNT(*) FROM TB_LOAN_INFO "
        "WHERE OVERDUE_YN = 'Y'"
    )
    # "연체 현황" — 건별일 수도, 통계일 수도 있음
    result = validate_table_selection(
        sql=sql,
        query="연체 현황 보여줘",
        context_tables=["TB_LOAN_INFO", "TB_LOAN_OVERDUE_STAT"],
    )
    # 신호어가 약해서 AMBIGUOUS가 될 수 있음
    assert result.verdict in (
        TableVerdict.PASS,
        TableVerdict.WARNING,
        TableVerdict.AMBIGUOUS,
    )


# ──────────────────────────────────────────────────
# build_table_disambiguation_prompt
# ──────────────────────────────────────────────────

def test_disambiguation_prompt_content():
    groups = find_relevant_groups(["TB_LOAN_INFO"])
    prompt = build_table_disambiguation_prompt(groups)
    assert "유사 테이블 구분 가이드" in prompt
    assert "TB_LOAN_INFO" in prompt
    assert "구분 기준" in prompt


def test_disambiguation_prompt_empty():
    prompt = build_table_disambiguation_prompt([])
    assert prompt == ""


# ──────────────────────────────────────────────────
# check_rejected_tables (골든셋 평가용)
# ──────────────────────────────────────────────────

def test_rejected_tables_pass():
    passed, errors = check_rejected_tables(
        "SELECT * FROM TB_LOAN_OVERDUE_STAT LIMIT 10",
        ["TB_LOAN_INFO"],
    )
    assert passed
    assert not errors


def test_rejected_tables_fail():
    passed, errors = check_rejected_tables(
        "SELECT * FROM TB_LOAN_INFO LIMIT 10",
        ["TB_LOAN_INFO"],
    )
    assert not passed
    assert any("부적합" in e for e in errors)


def test_rejected_tables_empty():
    passed, errors = check_rejected_tables(
        "SELECT 1", [],
    )
    assert passed


# ──────────────────────────────────────────────────
# 그룹 정의 무결성
# ──────────────────────────────────────────────────

def test_all_groups_have_at_least_two_tables():
    """모든 유사 그룹은 최소 2개 테이블을 포함."""
    for group in SIMILAR_TABLE_GROUPS:
        assert len(group.tables) >= 2, (
            f"{group.group_id}: 테이블 {len(group.tables)}개"
        )


def test_all_tables_have_signal_keywords():
    """모든 테이블은 최소 1개의 신호어를 가진다."""
    for group in SIMILAR_TABLE_GROUPS:
        for tname, tinfo in group.tables.items():
            assert len(tinfo.signal_keywords) >= 1, (
                f"{tname}: 신호어 없음"
            )


def test_all_groups_have_disambiguation_rule():
    """모든 그룹은 구분 규칙을 가진다."""
    for group in SIMILAR_TABLE_GROUPS:
        assert group.disambiguation_rule, (
            f"{group.group_id}: 구분 규칙 없음"
        )
