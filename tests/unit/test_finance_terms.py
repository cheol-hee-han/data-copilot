"""도메인 사전(domain_dictionary) 용어 매칭 단위 테스트.

테스트 대상:
    자연어 질의에서 금융 도메인 용어(테이블명·컬럼명·조건식 매핑)를
    인식하고 카테고리별 컨텍스트를 포맷팅하는 기능을 검증한다.

입력 예시 (정상):
    - "이번 달 신규 고객 수 알려줘" → ["신규 고객", "이번 달"] 매칭
    - "주담대 현황 보여줘" → alias "주담대" → "담보대출" 매칭

결과 예시 (오류 케이스):
    - "안녕하세요" → 매칭 용어 0건 (빈 리스트)

실행 스크립트:
    pytest tests/unit/test_finance_terms.py -v

참고:
    - 외부 의존성 없음
    - 테스트 대상 소스: src/services/domain/domain_dictionary.py
"""

from src.services.domain.domain_dictionary import (
    format_domain_context,
    get_terms_by_category,
    lookup_terms,
)


def test_lookup_basic_terms():
    """기본 용어 매칭."""
    terms = lookup_terms("이번 달 신규 고객 수 알려줘")
    term_names = [t.term for t in terms]
    assert "신규 고객" in term_names
    assert "이번 달" in term_names


def test_lookup_loan_terms():
    """여신 관련 용어 매칭."""
    terms = lookup_terms("담보대출 연체 현황")
    term_names = [t.term for t in terms]
    assert "담보대출" in term_names
    assert "연체" in term_names


def test_lookup_aliases():
    """동의어 매칭."""
    terms = lookup_terms("주담대 현황 보여줘")
    term_names = [t.term for t in terms]
    assert "담보대출" in term_names


def test_lookup_no_match():
    """매칭되는 용어가 없는 경우."""
    terms = lookup_terms("안녕하세요")
    assert len(terms) == 0


def test_get_terms_by_category():
    """카테고리별 용어 조회."""
    loan_terms = get_terms_by_category("여신")
    assert len(loan_terms) > 0
    assert all(t.category == "여신" for t in loan_terms)


def test_format_domain_context():
    """도메인 컨텍스트 포맷팅."""
    terms = lookup_terms("신규 고객")
    formatted = format_domain_context(terms)
    assert "신규 고객" in formatted
    assert "TB_CUST_INFO" in formatted


def test_format_empty_context():
    """빈 컨텍스트."""
    assert format_domain_context([]) == ""
