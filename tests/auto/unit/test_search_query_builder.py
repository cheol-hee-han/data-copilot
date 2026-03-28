"""검색 쿼리 빌더 단위 테스트.

=== 개념 설명 ===
사용자 질의를 분석하여 ES, PostgreSQL, Qdrant 각 소스에 최적화된
검색 쿼리를 생성하는 빌더이다.
도메인 용어 매칭, 불용어 제거, 동의어 확장, 카테고리-도메인코드 매핑,
소스별 쿼리 차별화, NormalizedQuery 연동을 검증한다.

=== 단독 실행 ===
    python -m pytest tests/unit/test_search_query_builder.py -v -s

=== 테스트 데이터 예시 ===
    입력: "이번 달 신규 여신 실행 건수 뽑아줘"
    기대: es_table_query에 "TB_LOAN_INFO" 포함, categories에 "여신" 포함
          불용어("뽑아줘") 제거, 동의어("대출") 확장

=== 정상 결과 ===
    SourceQuery 객체에 4개 소스별 차별화된 쿼리 생성
=== 오류 결과 ===
    빈 입력 → SourceQuery 반환 (에러 없음, 빈 필드)

=== 평가 관점 ===
    1. 도메인 용어 매칭 정확도
    2. 불용어 제거 효과
    3. 동의어 확장 효과
    4. 소스별 쿼리 차별화
    5. 복합 시나리오/엣지 케이스
    6. Before/After 비교
    7. 골든셋 품질 점수 산출
    8. NormalizedQuery 연동
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from tests.conftest import get_test_logger, log_test_case

logger = get_test_logger("test_search_query_builder")

# search_query_builder 는 domain_dictionary 모듈에 의존하므로
# 해당 모듈이 없으면 이 테스트 파일 전체를 건너뜀.
try:
    from src.services.search_query_builder import (  # noqa: I001
        _CATEGORY_TO_DOMAIN_CD,
        _extract_core_keywords,
        SourceQuery,
        build_source_queries,
        build_source_queries_with_normalization,
    )

    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False

_SKIP = pytest.mark.skipif(
    not _AVAILABLE,
    reason="src.services.search_query_builder 임포트 불가",
)


# ══════════════════════════════════════════════════════════════
# 헬퍼
# ══════════════════════════════════════════════════════════════

def _has_any(text: str, keywords: list[str]) -> bool:
    """text 에 keywords 중 하나라도 포함되어 있으면 True."""
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in keywords)


def _has_all(text: str, keywords: list[str]) -> bool:
    """text 에 keywords 가 모두 포함되어 있으면 True."""
    text_lower = text.lower()
    return all(kw.lower() in text_lower for kw in keywords)


# ══════════════════════════════════════════════════════════════
# 1. 도메인 용어 매칭 정확도
# ══════════════════════════════════════════════════════════════

@_SKIP
class TestDomainTermMatching:
    """도메인 용어 매칭이 정확하게 동작하는지 검증."""

    def test_single_domain_term(self):
        """단일 도메인 용어 매칭."""
        result = build_source_queries("이번 달 신규 여신 실행 건수 뽑아줘")
        terms = [t.term for t in result.matched_terms]
        assert "여신" in terms or "대출 실행" in terms
        assert "TB_LOAN_INFO" in result.extracted_tables

    def test_multiple_domain_terms(self):
        """복수 도메인 용어 동시 매칭."""
        result = build_source_queries("개인 고객 중 연체 대출 현황")
        terms = [t.term for t in result.matched_terms]
        assert "개인 고객" in terms
        assert "연체" in terms
        assert "고객" in result.categories
        assert "여신" in result.categories

    def test_alias_matching(self):
        """동의어(alias) 매칭 — '주담대' → '주택담보대출'."""
        result = build_source_queries("주담대 잔액 현황 알려줘")
        terms = [t.term for t in result.matched_terms]
        assert "주택담보대출" in terms
        assert "TB_LOAN_INFO" in result.extracted_tables

    def test_financial_indicator_matching(self):
        """금융지표 용어 매칭."""
        result = build_source_queries("BIS비율 추이 분석해줘")
        terms = [t.term for t in result.matched_terms]
        assert "BIS비율" in terms
        assert "금융지표" in result.categories

    def test_time_expression_matching(self):
        """시간 표현 매칭."""
        result = build_source_queries("전년동기 대비 수신 잔액 비교")
        terms = [t.term for t in result.matched_terms]
        assert "전년동기" in terms
        assert "시간" in result.categories

    def test_card_domain_matching(self):
        """카드 도메인 용어 매칭."""
        result = build_source_queries("이번 달 신용카드 이용금액 합계")
        terms = [t.term for t in result.matched_terms]
        assert "신용카드" in terms
        assert "카드" in result.categories

    def test_fx_domain_matching(self):
        """외환 도메인 용어 매칭."""
        result = build_source_queries("달러예금 잔액 현황")
        terms = [t.term for t in result.matched_terms]
        assert "외화예금" in terms
        assert "외환" in result.categories


# ══════════════════════════════════════════════════════════════
# 2. 불용어 제거 효과
# ══════════════════════════════════════════════════════════════

@_SKIP
class TestStopwordRemoval:
    """불용어 제거가 정확히 동작하는지 검증."""

    def test_particles_removed(self):
        """조사(은/는/이/가/을/를) 제거."""
        keywords = _extract_core_keywords("여신의 연체율을 알려줘")
        assert "을" not in keywords
        assert "의" not in keywords
        assert _has_any(" ".join(keywords), ["여신", "연체율"])

    def test_request_verbs_removed(self):
        """요청 동사(뽑아줘, 알려줘, 보여줘) 제거."""
        keywords = _extract_core_keywords("이번 달 신규 고객 수 뽑아줘")
        assert "뽑아줘" not in keywords

    def test_content_words_preserved(self):
        """핵심 내용어는 유지."""
        keywords = _extract_core_keywords("지점별 수신 잔액 현황 보여줘")
        assert "지점별" in keywords or "지점" in keywords
        assert "수신" in keywords
        assert "잔액" in keywords
        assert "현황" in keywords

    def test_single_char_removed(self):
        """1글자 토큰 제거."""
        keywords = _extract_core_keywords("이 달 수 알려 줘")
        assert "이" not in keywords
        assert "수" not in keywords
        assert "줘" not in keywords

    def test_stopword_in_build_result(self):
        """build_source_queries 결과의 core_keywords 에서도 불용어 제거."""
        result = build_source_queries_with_normalization("연체율 알려주세요 보여줘", None)
        stopwords_present = [
            w for w in result.core_keywords
            if w in ["주세요", "알려줘", "보여줘", "알려주세요"]
        ]
        passed = len(stopwords_present) == 0
        log_test_case(logger, "test_stopword_in_build", "연체율 알려주세요 보여줘",
                      "불용어 미포함", f"core_keywords={result.core_keywords}", passed)
        assert passed, f"불용어가 core_keywords 에 포함됨: {stopwords_present}"


# ══════════════════════════════════════════════════════════════
# 3. 동의어 확장 효과
# ══════════════════════════════════════════════════════════════

@_SKIP
class TestSynonymExpansion:
    """동의어 확장이 검색 재현율을 높이는지 검증."""

    def test_loan_alias_expansion(self):
        """'여신' → '대출', '론' 등 확장."""
        result = build_source_queries("여신 잔액 현황")
        assert _has_any(" ".join(result.expanded_keywords), ["대출", "론"])

    def test_deposit_alias_expansion(self):
        """'수신' → '예금', '예적금' 등 확장."""
        result = build_source_queries("수신 잔액 현황")
        assert _has_any(" ".join(result.expanded_keywords), ["예금", "예적금"])

    def test_overdue_alias_expansion(self):
        """'연체' → '미상환', '부실' 등 확장."""
        result = build_source_queries("연체 현황 알려줘")
        assert _has_any(" ".join(result.expanded_keywords), ["미상환", "부실"])

    def test_no_duplicate_expansion(self):
        """확장 결과에 중복 키워드가 없어야 한다."""
        result = build_source_queries("여신 대출 잔액")
        lowered = [kw.lower() for kw in result.expanded_keywords]
        assert len(lowered) == len(set(lowered))

    def test_synonym_expansion_via_normalization(self):
        """build_source_queries_with_normalization 에서 expanded >= core."""
        result = build_source_queries_with_normalization("여신 현황", None)
        if result.matched_terms:
            passed = len(result.expanded_keywords) >= len(result.core_keywords)
        else:
            passed = True
        log_test_case(logger, "test_synonym_via_nq", "여신 현황", "expanded >= core",
                      f"core={len(result.core_keywords)}, expanded={len(result.expanded_keywords)}",
                      passed)
        assert passed


# ══════════════════════════════════════════════════════════════
# 4. 소스별 쿼리 차별화
# ══════════════════════════════════════════════════════════════

@_SKIP
class TestSourceSpecificQueries:
    """각 소스에 최적화된 쿼리가 생성되는지 검증."""

    def test_es_table_query_contains_table_names(self):
        """ES 테이블 검색 쿼리에 테이블명이 포함된다."""
        result = build_source_queries("이번 달 여신 실행 건수")
        assert "TB_LOAN_INFO" in result.es_table_query

    def test_es_table_query_boosts_table_names(self):
        """ES 테이블 검색에서 테이블명이 부스트(반복)된다."""
        result = build_source_queries("수신 잔액 현황")
        count = result.es_table_query.count("TB_DEPOSIT_INFO")
        assert count >= 2, f"테이블명 부스트 횟수: {count}"

    def test_es_table_query_excludes_time_keywords(self):
        """ES 테이블 검색에서 시간 키워드가 제외된다."""
        result = build_source_queries("이번 달 여신 실행 건수")
        assert "이번" not in result.es_table_query.split()

    def test_es_report_query_preserves_natural_language(self):
        """ES 보고서 검색은 자연어 형태를 유지한다."""
        result = build_source_queries("지점별 연체율 현황 분석해줘")
        assert "연체율" in result.es_report_query
        assert "지점별" in result.es_report_query or "지점" in result.es_report_query

    def test_es_report_query_strips_time(self):
        """ES 보고서 검색에서 시간 표현이 제거된다."""
        result = build_source_queries("이번 달 신규 여신 실행 건수")
        assert "이번 달" not in result.es_report_query

    def test_history_db_query_has_expanded_keywords(self):
        """이력 DB 검색에 확장 키워드가 포함된다."""
        result = build_source_queries("여신 연체 현황")
        assert _has_any(result.history_db_query, ["대출", "여신"])

    def test_history_db_query_includes_table_names(self):
        """이력 DB 검색에 테이블명이 포함된다."""
        result = build_source_queries("수신 잔액 현황")
        assert "TB_DEPOSIT_INFO" in result.history_db_query

    def test_qdrant_query_enriched_with_descriptions(self):
        """Qdrant 검색에 도메인 용어 설명이 보강된다."""
        result = build_source_queries("연체율 추이 분석")
        assert "연체율" in result.qdrant_query
        assert len(result.qdrant_query) > len("연체율 추이 분석")

    def test_all_four_queries_are_different(self):
        """4개 소스의 쿼리가 모두 다르다."""
        result = build_source_queries("이번 달 신규 여신 실행 건수 뽑아줘")
        queries = {
            result.es_table_query,
            result.es_report_query,
            result.history_db_query,
            result.qdrant_query,
        }
        assert len(queries) == 4, "모든 소스 쿼리가 차별화되어야 한다"


# ══════════════════════════════════════════════════════════════
# 5. 복합 시나리오 시뮬레이션
# ══════════════════════════════════════════════════════════════

@_SKIP
class TestComplexScenarios:
    """실제 은행 직원이 사용할 법한 복합 질의 시뮬레이션."""

    def test_multi_category_query(self):
        """여러 카테고리에 걸친 복합 질의."""
        result = build_source_queries(
            "개인 고객 중 VIP 등급의 여신 잔액과 수신 잔액 비교"
        )
        assert "고객" in result.categories
        assert "여신" in result.categories
        assert "수신" in result.categories
        assert len(result.extracted_tables) >= 2

    def test_colloquial_expression(self):
        """구어체 표현 처리 — '마통' → '한도대출'."""
        result = build_source_queries("마통 얼마나 나갔어?")
        terms = [t.term for t in result.matched_terms]
        assert "한도대출" in terms

    def test_abbreviation_handling(self):
        """약어 처리 — 'NPL비율' → '고정이하여신비율'."""
        result = build_source_queries("NPL비율 좀 알려줘")
        terms = [t.term for t in result.matched_terms]
        assert "고정이하여신비율" in terms or "부실채권" in terms

    def test_analysis_query(self):
        """분석형 질의의 쿼리 전략."""
        result = build_source_queries("지난 분기 대비 연체율 변화 추이 분석해줘")
        assert "연체율" in result.qdrant_query
        assert "연체율" in result.es_report_query

    def test_vague_query_still_works(self):
        """모호한 질의도 최소한의 키워드를 생성한다."""
        result = build_source_queries("최근 현황 좀 알려줘")
        assert len(result.core_keywords) >= 1
        assert result.es_table_query.strip()
        assert result.qdrant_query.strip()

    def test_specific_metric_query(self):
        """특정 지표 조회 질의."""
        result = build_source_queries("NIM 추이 보여줘")
        terms = [t.term for t in result.matched_terms]
        assert "NIM" in terms
        assert "TB_MGMT_INDICATOR" in result.extracted_tables

    def test_branch_comparison_query(self):
        """지점 비교 질의."""
        result = build_source_queries("지점별 수신 잔액 현황 비교")
        assert "수신" in result.categories


# ══════════════════════════════════════════════════════════════
# 6. 엣지 케이스
# ══════════════════════════════════════════════════════════════

@_SKIP
class TestEdgeCases:
    """경계 조건 및 예외 상황 처리 검증."""

    def test_empty_input(self):
        """빈 입력 처리."""
        result = build_source_queries("")
        assert isinstance(result, SourceQuery)
        assert result.es_table_query == ""
        log_test_case(logger, "test_empty_input", "", "빈 결과", "OK", True)

    def test_only_stopwords(self):
        """불용어로만 구성된 입력."""
        result = build_source_queries("좀 알려줘 보여줘")
        assert isinstance(result, SourceQuery)

    def test_english_mixed_query(self):
        """영문 혼용 질의."""
        result = build_source_queries("ROA 지표 확인해줘")
        terms = [t.term for t in result.matched_terms]
        assert "ROA" in terms

    def test_long_input(self):
        """긴 입력 처리 (키워드 수 제한)."""
        long_input = "여신 수신 고객 거래 카드 외환 " * 10
        result = build_source_queries(long_input)
        history_words = result.history_db_query.split()
        assert len(history_words) <= 15

    def test_clarification_synthesized_input(self):
        """명확화 합성 입력 처리."""
        synthesized = "연체 현황 알려줘\n추가 조건: 이번 달 기준 전체 연체 현황"
        result = build_source_queries(synthesized)
        terms = [t.term for t in result.matched_terms]
        assert "연체" in terms

    def test_empty_via_normalization(self):
        """build_source_queries_with_normalization 빈 입력도 에러 없이 반환."""
        result = build_source_queries_with_normalization("", None)
        assert result is not None
        log_test_case(logger, "test_empty_nq", "", "결과 반환", "OK", True)


# ══════════════════════════════════════════════════════════════
# 7. Before/After 비교 (기존 방식 vs 전략 방식)
# ══════════════════════════════════════════════════════════════

@_SKIP
class TestBeforeAfterComparison:
    """기존 방식(원본 그대로 전달)과 전략 방식의 품질 비교."""

    @pytest.mark.parametrize(
        "user_input,expected_table,noise_words",
        [
            ("이번 달 신규 여신 실행 건수 뽑아줘", "TB_LOAN_INFO", ["뽑아줘", "이번"]),
            ("지난 분기 부서별 수신 잔액 현황 알려줘", "TB_DEPOSIT_INFO", ["알려줘", "지난"]),
            ("VIP 고객 중 연체 대출 목록 보여줘", "TB_LOAN_INFO", ["보여줘", "중"]),
        ],
    )
    def test_es_table_query_removes_noise(self, user_input, expected_table, noise_words):
        """ES 테이블 검색에서 노이즈 단어가 제거되고 테이블명이 포함된다."""
        result = build_source_queries(user_input)
        assert expected_table in result.es_table_query
        for noise in noise_words:
            assert noise not in result.es_table_query.split(), (
                f"ES 테이블 쿼리에 노이즈 '{noise}'가 남아있음"
            )

    @pytest.mark.parametrize(
        "user_input,expected_expanded",
        [
            ("여신 현황", ["대출"]),
            ("수신 잔액", ["예금"]),
            ("연체 목록", ["미상환"]),
        ],
    )
    def test_history_query_expanded(self, user_input, expected_expanded):
        """이력 DB 검색에 동의어가 확장되어 재현율이 높아진다."""
        result = build_source_queries(user_input)
        assert _has_any(result.history_db_query, expected_expanded), (
            f"확장 키워드 {expected_expanded}가 이력 쿼리에 없음"
        )


# ══════════════════════════════════════════════════════════════
# 8. 골든셋 품질 점수 산출
# ══════════════════════════════════════════════════════════════

@_SKIP
class TestSearchQualityScore:
    """검색 품질을 정량적으로 측정하는 시뮬레이션."""

    GOLDEN_CASES: list[dict] = [
        {
            "input": "이번 달 신규 여신 실행 건수 뽑아줘",
            "expected_tables": ["TB_LOAN_INFO"],
            "expected_categories": ["여신"],
            "must_have_keywords": ["여신", "실행", "건수"],
        },
        {
            "input": "지점별 수신 잔액 현황 비교해줘",
            "expected_tables": ["TB_DEPOSIT_INFO"],
            "expected_categories": ["수신", "조직"],
            "must_have_keywords": ["수신", "잔액"],
        },
        {
            "input": "개인 고객 연체 대출 목록",
            "expected_tables": ["TB_CUST_INFO", "TB_LOAN_INFO"],
            "expected_categories": ["고객", "여신"],
            "must_have_keywords": ["고객", "연체"],
        },
        {
            "input": "전년동기 대비 NIM 추이",
            "expected_tables": ["TB_MGMT_INDICATOR"],
            "expected_categories": ["금융지표", "시간"],
            "must_have_keywords": ["NIM"],
        },
        {
            "input": "이번 달 신용카드 이용금액과 체크카드 이용금액 비교",
            "expected_tables": ["TB_CARD_INFO", "TB_CARD_USAGE"],
            "expected_categories": ["카드"],
            "must_have_keywords": ["신용카드", "체크카드"],
        },
        {
            "input": "해외송금 건수와 금액 현황",
            "expected_tables": ["TB_FX_REMITTANCE"],
            "expected_categories": ["외환"],
            "must_have_keywords": ["해외송금"],
        },
        {
            "input": "주담대 연체율 추이 분석",
            "expected_tables": ["TB_LOAN_INFO"],
            "expected_categories": ["여신"],
            "must_have_keywords": ["주택담보대출"],
        },
        {
            "input": "마통 잔액 현황 알려줘",
            "expected_tables": ["TB_LOAN_INFO"],
            "expected_categories": ["여신"],
            "must_have_keywords": ["한도대출"],
        },
    ]

    @pytest.mark.parametrize(
        "case", GOLDEN_CASES,
        ids=[c["input"][:30] for c in GOLDEN_CASES],
    )
    def test_table_extraction_accuracy(self, case):
        """기대 테이블이 extracted_tables에 포함되는지."""
        result = build_source_queries(case["input"])
        for expected in case["expected_tables"]:
            assert expected in result.extracted_tables, (
                f"'{case['input']}' → {expected} 누락. 실제: {result.extracted_tables}"
            )

    @pytest.mark.parametrize(
        "case", GOLDEN_CASES,
        ids=[c["input"][:30] for c in GOLDEN_CASES],
    )
    def test_category_extraction_accuracy(self, case):
        """기대 카테고리가 categories에 포함되는지."""
        result = build_source_queries(case["input"])
        for expected in case["expected_categories"]:
            assert expected in result.categories, (
                f"'{case['input']}' → 카테고리 '{expected}' 누락. 실제: {result.categories}"
            )

    @pytest.mark.parametrize(
        "case", GOLDEN_CASES,
        ids=[c["input"][:30] for c in GOLDEN_CASES],
    )
    def test_keyword_recall(self, case):
        """핵심 키워드가 소스 쿼리에 포함되는지."""
        result = build_source_queries(case["input"])
        all_query_text = (
            f"{result.es_table_query} {result.history_db_query} "
            f"{result.qdrant_query}"
        )
        for kw in case["must_have_keywords"]:
            assert kw.lower() in all_query_text.lower(), (
                f"'{case['input']}' → 키워드 '{kw}' 누락"
            )

    def test_aggregate_quality_score(self):
        """전체 골든셋에 대한 종합 품질 점수를 산출한다."""
        total_table_hits = 0
        total_table_expected = 0
        total_category_hits = 0
        total_category_expected = 0
        total_keyword_hits = 0
        total_keyword_expected = 0

        for case in self.GOLDEN_CASES:
            result = build_source_queries(case["input"])

            for t in case["expected_tables"]:
                total_table_expected += 1
                if t in result.extracted_tables:
                    total_table_hits += 1

            for c in case["expected_categories"]:
                total_category_expected += 1
                if c in result.categories:
                    total_category_hits += 1

            all_text = (
                f"{result.es_table_query} {result.history_db_query} "
                f"{result.qdrant_query}"
            ).lower()
            for kw in case["must_have_keywords"]:
                total_keyword_expected += 1
                if kw.lower() in all_text:
                    total_keyword_hits += 1

        table_score = total_table_hits / total_table_expected * 100
        category_score = total_category_hits / total_category_expected * 100
        keyword_score = total_keyword_hits / total_keyword_expected * 100
        overall = (table_score + category_score + keyword_score) / 3

        log_test_case(
            logger, "test_aggregate_quality_score",
            f"{len(self.GOLDEN_CASES)} 골든 케이스",
            "종합 >= 80%",
            f"테이블={table_score:.0f}% 카테고리={category_score:.0f}% "
            f"키워드={keyword_score:.0f}% 종합={overall:.0f}%",
            overall >= 80.0,
        )

        print(f"\n{'=' * 60}")
        print(f"  검색 쿼리 전략 품질 평가 결과")
        print(f"{'=' * 60}")
        print(f"  테이블 추출 정확도:  {table_score:.1f}% "
              f"({total_table_hits}/{total_table_expected})")
        print(f"  카테고리 추출 정확도: {category_score:.1f}% "
              f"({total_category_hits}/{total_category_expected})")
        print(f"  키워드 재현율:       {keyword_score:.1f}% "
              f"({total_keyword_hits}/{total_keyword_expected})")
        print(f"  종합 점수:           {overall:.1f}%")
        print(f"{'=' * 60}\n")

        assert overall >= 80.0, f"종합 점수 {overall:.1f}% < 80%"


# ══════════════════════════════════════════════════════════════
# 9. NormalizedQuery 연동
# ══════════════════════════════════════════════════════════════

@_SKIP
class TestNormalizationIntegration:
    """NormalizedQuery 연동 테스트."""

    def test_basic_normalization_enriches(self):
        """NormalizedQuery search_keywords 가 쿼리를 보강한다."""
        from src.agents.models.normalization import NormalizedQuery

        nq = NormalizedQuery(
            original_query="부서별 대출잔액 합계",
            search_keywords={
                "meta_search": ["대출", "잔액", "부서"],
                "vector_search": "부서별 대출잔액 합계 조회",
                "sql_history_search": "부서별 대출잔액 합계 집계",
            },
        )
        result = build_source_queries_with_normalization("부서별 대출잔액 합계", nq)
        has_enrichment = "대출" in result.es_table_query or "잔액" in result.es_table_query
        log_test_case(logger, "test_nq_enriches", "NQ + 질의", "보강됨",
                      result.es_table_query[:60], has_enrichment)
        assert has_enrichment

    def test_matched_terms_populated(self):
        """도메인 사전 매칭 결과가 채워진다."""
        result = build_source_queries_with_normalization("연체 고객 대출 현황", None)
        log_test_case(logger, "test_matched_terms", "연체 고객 대출", "matched > 0",
                      f"{len(result.matched_terms)}개", len(result.matched_terms) > 0)
        assert len(result.matched_terms) > 0

    def test_category_to_domain_cd(self):
        """주요 카테고리가 올바른 domain_cd 에 매핑된다."""
        cases = [("여신", "LON"), ("수신", "DEP"), ("고객", "CUS"), ("카드", "CRD")]
        failures = []
        for cat, expected_cd in cases:
            cds = _CATEGORY_TO_DOMAIN_CD.get(cat, [])
            if expected_cd not in cds:
                failures.append(f"{cat}→{expected_cd} 없음 (실제: {cds})")
        passed = len(failures) == 0
        log_test_case(logger, "test_category_to_domain_cd",
                      str(cases), "모두 매핑됨", f"실패: {failures}", passed)
        assert passed, f"domain_cd 매핑 오류: {failures}"
