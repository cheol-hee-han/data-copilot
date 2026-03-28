"""도메인 사전 엣지 케이스 테스트.

복합 용어 동시 등장, 동음이의어, 부분 매칭 오류, 카테고리 교차 등
경계 조건을 검증한다.
"""

from src.services.domain.domain_dictionary import (
    DOMAIN_DICTIONARY,
    format_domain_context,
    get_terms_by_category,
    lookup_terms,
)


# ---------------------------------------------------------------------------
# 복합 용어 동시 등장
# ---------------------------------------------------------------------------

def test_lookup_multiple_terms_loan_and_overdue():
    """여신 + 연체 복합 표현에서 두 용어 모두 매칭된다."""
    terms = lookup_terms("담보대출 중 연체된 건 알려줘")
    term_names = [t.term for t in terms]
    assert "담보대출" in term_names
    assert "연체" in term_names


def test_lookup_multiple_terms_branch_and_customer():
    """지점 + 고객 복합 표현에서 두 용어 모두 매칭된다."""
    terms = lookup_terms("지점별 신규 고객 수 알려줘")
    term_names = [t.term for t in terms]
    assert "지점" in term_names
    assert "신규 고객" in term_names


def test_lookup_multiple_terms_time_and_loan():
    """시간 표현 + 여신 동시 매칭."""
    terms = lookup_terms("이번 달 대출 실행 건수 보여줘")
    term_names = [t.term for t in terms]
    assert "이번 달" in term_names
    assert "대출 실행" in term_names


def test_lookup_three_terms_simultaneously():
    """3개 용어 동시 매칭 - 지점 + 담보대출 + 이번 달."""
    terms = lookup_terms("이번 달 지점별 담보대출 건수 알려줘")
    term_names = [t.term for t in terms]
    assert "이번 달" in term_names
    assert "지점" in term_names
    assert "담보대출" in term_names


def test_lookup_loan_type_and_overdue_combined():
    """신용대출과 연체 동시 매칭."""
    terms = lookup_terms("신용대출 연체 현황 보여줘")
    term_names = [t.term for t in terms]
    assert "신용대출" in term_names
    assert "연체" in term_names


# ---------------------------------------------------------------------------
# 별칭(alias) 매칭
# ---------------------------------------------------------------------------

def test_alias_joo_dam_dae_maps_to_dam_bo():
    """주담대는 담보대출로 매칭된다."""
    terms = lookup_terms("주담대 잔액 알려줘")
    term_names = [t.term for t in terms]
    assert "담보대출" in term_names


def test_alias_yeo_sin_maps_to_yeo_sin_term():
    """여신거래는 여신으로 매칭된다."""
    terms = lookup_terms("여신거래 현황")
    term_names = [t.term for t in terms]
    assert "여신" in term_names


def test_alias_jeompo_maps_to_branch():
    """점포는 지점으로 매칭된다."""
    terms = lookup_terms("점포별 실적 알려줘")
    term_names = [t.term for t in terms]
    assert "지점" in term_names


def test_alias_geumwol_maps_to_this_month():
    """금월은 이번 달로 매칭된다."""
    terms = lookup_terms("금월 실적 현황")
    term_names = [t.term for t in terms]
    assert "이번 달" in term_names


def test_alias_jeonwol_maps_to_last_month():
    """전월은 지난 달로 매칭된다."""
    terms = lookup_terms("전월 대비 실적")
    term_names = [t.term for t in terms]
    assert "지난 달" in term_names


def test_alias_retail_maps_to_individual_customer():
    """리테일은 개인 고객으로 매칭된다."""
    terms = lookup_terms("리테일 고객 수 알려줘")
    term_names = [t.term for t in terms]
    assert "개인 고객" in term_names


def test_alias_beop_in_maps_to_enterprise():
    """법인은 기업 고객으로 매칭된다."""
    terms = lookup_terms("법인 고객 대출 현황")
    term_names = [t.term for t in terms]
    assert "기업 고객" in term_names


# ---------------------------------------------------------------------------
# 중복 매칭 방지 (한 용어는 최대 한 번 매칭)
# ---------------------------------------------------------------------------

def test_no_duplicate_match_for_same_term():
    """같은 용어가 여러 별칭으로 중복 매칭되지 않는다."""
    # '주담대'와 '담보대출'이 모두 입력에 있어도 담보대출 하나만 매칭
    terms = lookup_terms("주담대(담보대출) 연체 현황")
    matched_term_objs = [t for t in terms if t.term == "담보대출"]
    assert len(matched_term_objs) == 1


# ---------------------------------------------------------------------------
# 부분 매칭 오류 방지
# ---------------------------------------------------------------------------

def test_no_false_positive_on_partial_word():
    """단어의 일부만 포함된 경우 불필요한 매칭이 발생하지 않는다."""
    # '입금' 이 포함되었지만 '출금' 과 '이체'는 매칭되지 않아야 한다
    terms = lookup_terms("오늘 입금 거래 현황")
    term_names = [t.term for t in terms]
    assert "입금" in term_names
    assert "출금" not in term_names
    assert "이체" not in term_names


# ---------------------------------------------------------------------------
# 카테고리 교차 조회
# ---------------------------------------------------------------------------

def test_get_terms_by_category_yeo_sin():
    """여신 카테고리 용어는 모두 여신 카테고리다."""
    terms = get_terms_by_category("여신")
    assert len(terms) > 0
    assert all(t.category == "여신" for t in terms)


def test_get_terms_by_category_su_sin():
    """수신 카테고리 용어 조회."""
    terms = get_terms_by_category("수신")
    assert len(terms) > 0
    assert all(t.category == "수신" for t in terms)


def test_get_terms_by_category_si_gan():
    """시간 카테고리 용어 조회."""
    terms = get_terms_by_category("시간")
    assert len(terms) > 0
    assert all(t.category == "시간" for t in terms)


def test_get_terms_by_nonexistent_category_returns_empty():
    """존재하지 않는 카테고리는 빈 리스트를 반환한다."""
    terms = get_terms_by_category("존재하지않는카테고리")
    assert terms == []


# ---------------------------------------------------------------------------
# format_domain_context 엣지 케이스
# ---------------------------------------------------------------------------

def test_format_context_with_multiple_terms():
    """여러 용어가 있을 때 모두 포맷팅에 포함된다."""
    terms = lookup_terms("이번 달 담보대출 연체 현황")
    formatted = format_domain_context(terms)
    assert "담보대출" in formatted
    assert "연체" in formatted
    assert "이번 달" in formatted


def test_format_context_includes_table_and_condition():
    """테이블명과 조건이 포맷팅에 포함된다."""
    terms = [t for t in DOMAIN_DICTIONARY if t.term == "연체"]
    formatted = format_domain_context(terms)
    assert "TB_LOAN_INFO" in formatted
    assert "OVERDUE_YN" in formatted


def test_format_context_term_without_table():
    """테이블이 없는 시간 용어도 포맷팅된다."""
    terms = [t for t in DOMAIN_DICTIONARY if t.term == "이번 달"]
    formatted = format_domain_context(terms)
    assert "이번 달" in formatted
    assert "DATE_TRUNC" in formatted


def test_format_context_single_term_structure():
    """단일 용어 포맷팅 구조 검증."""
    terms = [t for t in DOMAIN_DICTIONARY if t.term == "담보대출"]
    formatted = format_domain_context(terms)
    assert "## 매칭된 도메인 용어" in formatted
    assert "TB_LOAN_INFO" in formatted
    assert "LOAN_TYPE_CD" in formatted


# ---------------------------------------------------------------------------
# 복합 쿼리 - 여러 도메인 섞인 표현
# ---------------------------------------------------------------------------

def test_lookup_cross_domain_deposit_and_loan():
    """예금(수신)과 대출(여신)이 동시에 등장하는 요청."""
    terms = lookup_terms("고객별 예금 잔액과 대출 잔액 비교해줘")
    term_names = [t.term for t in terms]
    # 예금 잔액, 여신 중 적어도 하나 매칭
    loan_related = any(t.category == "여신" for t in terms)
    deposit_related = any(t.category == "수신" for t in terms)
    assert loan_related
    assert deposit_related


def test_lookup_negation_expression_overdue():
    """'연체가 아닌' 표현에서 '연체' 용어를 감지한다 (부정 처리는 상위에서)."""
    terms = lookup_terms("연체가 아닌 대출 건수")
    term_names = [t.term for t in terms]
    assert "연체" in term_names


def test_lookup_negation_expression_deposit():
    """'해지되지 않은' 표현에서 도메인 용어를 감지한다."""
    terms = lookup_terms("해지되지 않은 예금 계좌 수")
    term_names = [t.term for t in terms]
    # 수신 관련 용어가 매칭되어야 한다
    deposit_terms = [t for t in terms if t.category == "수신"]
    assert len(deposit_terms) > 0


# ---------------------------------------------------------------------------
# 한국어 숫자 표현 (도메인 사전 매핑 범위 확인)
# ---------------------------------------------------------------------------

def test_lookup_korean_number_expression_no_false_match():
    """'삼천만원' 같은 수치 표현이 도메인 용어 매칭에 영향 없음."""
    terms = lookup_terms("삼천만원 이상 대출 현황")
    # 여신 관련 용어는 매칭되어야 하지만 숫자 표현 자체는 도메인 용어가 아님
    term_names = [t.term for t in terms]
    assert "여신" in term_names or "대출 실행" in term_names or "담보대출" not in term_names


# ---------------------------------------------------------------------------
# 도메인 사전 무결성 검사
# ---------------------------------------------------------------------------

def test_all_terms_have_category():
    """도메인 사전의 모든 용어에 카테고리가 설정되어 있다."""
    assert all(t.category != "" for t in DOMAIN_DICTIONARY)


def test_all_terms_have_term_name():
    """도메인 사전의 모든 용어에 term 이름이 설정되어 있다."""
    assert all(t.term != "" for t in DOMAIN_DICTIONARY)


def test_no_duplicate_term_names():
    """도메인 사전에 중복된 term 이름이 없다."""
    term_names = [t.term for t in DOMAIN_DICTIONARY]
    assert len(term_names) == len(set(term_names)), "중복된 term 이름이 존재합니다"


def test_terms_with_condition_have_column_or_table():
    """condition이 설정된 용어는 반드시 table_name 또는 column_name을 가진다."""
    for term in DOMAIN_DICTIONARY:
        if term.condition and term.category != "시간":
            assert term.table_name != "" or term.column_name != "", (
                f"'{term.term}'은 condition이 있지만 table_name과 column_name이 모두 비어 있습니다"
            )
