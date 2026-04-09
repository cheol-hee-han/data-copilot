"""질의 정규화(의도분석) 파이프라인 단위 테스트.

테스트 대상:
    자연어 질의 → 8-Slot NormalizedQuery 변환 파이프라인의 각 단계를 검증한다.
    LLM 호출 없이 전처리·파싱·검증·후처리·동의어 사전 로딩을 개별 테스트한다.

    ┌─────────────────────────────────────────────────────────────────────┐
    │  파이프라인 단계          테스트 클래스           테스트 대상 함수   │
    │  ─────────────────────── ─────────────────────── ──────────────── │
    │  0. 모델/스키마           TestNormalizationModels  NormalizedQuery  │
    │  1. 전처리 (약어 확장)    TestPreprocessor         _preprocess_*    │
    │  2. (삭제됨)              -                        -                │
    │  3. 구조 검증 (Enum)     TestValidator            _validate_*      │
    │  4. 후처리 (정합성 보정)  TestPostProcessor        _postprocess     │
    │  5. 동의어 사전           TestSynonyms             ALL_SYNONYMS 등  │
    └─────────────────────────────────────────────────────────────────────┘

입력 예시 (정상):
    - 전처리: "YoY 매출 추이"
        → "전년동기대비 매출 추이" (약어 확장)
    - JSON 파싱: '```json\\n{"intent": {"primary": "AGGREGATE"}}\\n```'
        → {"intent": {"primary": "AGGREGATE"}} (코드 펜스 제거)
    - 구조 검증: {"intent": {"primary": "EXTRACT"}, "entities": [...], ...}
        → 동일 dict 반환, errors=[]
    - 후처리: AGGREGATE + GROUP 차원 + agg_function="NONE"
        → agg_function이 "SUM"으로 자동 보정

결과 예시 (오류 케이스):
    - JSON 파싱 실패: "not json at all"
        → ValueError("LLM이 유효한 JSON을 반환하지 않았습니다")
    - 잘못된 intent: {"intent": {"primary": "INVALID"}}
        → primary가 "EXTRACT"로 폴백, errors=["intent.primary 보정 → EXTRACT"]
    - 잘못된 modifier: {"modifiers": [{"type": "INVALID_TYPE"}]}
        → 해당 modifier 삭제됨 (modifiers=[])

실행 스크립트:
    # 전체 실행
    pytest tests/unit/test_query_normalizer.py -v

    # 클래스별 실행
    pytest tests/unit/test_query_normalizer.py::TestNormalizationModels -v
    pytest tests/unit/test_query_normalizer.py::TestPreprocessor -v
    pytest tests/unit/test_query_normalizer.py::TestJsonParser -v
    pytest tests/unit/test_query_normalizer.py::TestValidator -v
    pytest tests/unit/test_query_normalizer.py::TestPostProcessor -v
    pytest tests/unit/test_query_normalizer.py::TestSynonyms -v

    # 개별 테스트 실행
    pytest tests/unit/test_query_normalizer.py::TestValidator::test_invalid_intent_corrected -v

    # 실패 시 즉시 중단 + 상세 출력
    pytest tests/unit/test_query_normalizer.py -v -x --tb=short

참고:
    - 외부 의존성 없음 (LLM, DB, ES, Qdrant 불필요)
    - .env 파일 없이도 모든 테스트 통과
    - 테스트 대상 소스: src/services/query_normalizer.py
    - 동의어 사전 소스: resources/domain/business_synonyms.yaml (resource_loader 경유)
    - 모델 정의: src/agents/models/normalization.py
"""

from __future__ import annotations

import json

import pytest

from src.agents.models.normalization import (
    NormalizedQuery,
    NormIntentType,
    QueryCategory,
    VALID_INTENTS,
    VALID_QUERY_CATEGORIES,
)
from src.services.query_normalizer import (
    _postprocess,
    _preprocess_for_normalization,
    _validate_structure,
)
from src.services.query_normalizer import (
    ALL_SYNONYMS,
    ABBREVIATION_MAP,
)
from src.utils.llm.prompt import (
    serialize_synonym_dict,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 모델 테스트
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestNormalizationModels:
    """정규화 모델 Enum 및 스키마 테스트."""

    def test_query_category_values(self):
        assert "DATA_QUERY" in VALID_QUERY_CATEGORIES
        assert "CASUAL_TALK" in VALID_QUERY_CATEGORIES
        assert "META_QUESTION" in VALID_QUERY_CATEGORIES

    def test_intent_type_values(self):
        assert "EXTRACT" in VALID_INTENTS
        assert "AGGREGATE" in VALID_INTENTS
        assert "COMPARE" in VALID_INTENTS
        assert "TREND" in VALID_INTENTS

    def test_normalized_query_default(self):
        nq = NormalizedQuery()
        assert nq.intent.primary == "EXTRACT"
        assert nq.entities == []
        assert nq.time.type == "NONE"
        assert nq.output_hint.format == "NONE"

    def test_normalized_query_from_dict(self):
        data = {
            "original_query": "지점별 여신잔액",
            "rewritten_query": "지점별 여신잔액 조회",
            "intent": {
                "primary": "AGGREGATE",
                "secondary": [],
            },
            "entities": [
                {
                    "term": "대출",
                    "type": "DIRECT",
                    "confidence": "HIGH",
                },
            ],
            "measures": [
                {
                    "term": "여신잔액",
                    "measure_type": "RAW",
                    "agg_function": "SUM",
                    "confidence": "HIGH",
                },
            ],
            "dimensions": [
                {
                    "term": "지점",
                    "role": "GROUP",
                    "granularity": "CATEGORY",
                    "confidence": "HIGH",
                },
            ],
            "filters": [],
            "time": {"type": "NONE"},
            "modifiers": [],
            "output_hint": {"format": "NONE"},
            "ambiguities": [],
            "search_keywords": {
                "meta_search": ["대출", "지점", "여신잔액"],
                "vector_search": "지점별 여신잔액 합계 조회",
            },
        }
        nq = NormalizedQuery.model_validate(data)
        assert nq.intent.primary == "AGGREGATE"
        assert len(nq.entities) == 1
        assert nq.entities[0].term == "대출"
        assert nq.measures[0].agg_function == "SUM"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 전처리 테스트
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestPreprocessor:
    """전처리기 테스트."""

    def test_abbreviation_not_expanded_in_preprocess(self):
        """약어 확장은 LLM 추론으로 전환됨 — 전처리 단계에서는 치환하지 않음."""
        result = _preprocess_for_normalization("YoY 매출 추이")
        assert "YoY" in result  # 약어가 그대로 보존

    def test_abbreviation_nim_not_expanded(self):
        """NIM도 전처리에서 치환하지 않음."""
        result = _preprocess_for_normalization("NIM 변화 추이")
        assert "NIM" in result

    def test_tilde_normalization(self):
        result = _preprocess_for_normalization("지점별~~~매출")
        assert "~~~" not in result
        assert "~" in result

    def test_no_change_for_normal_input(self):
        text = "이번 달 연체 현황 조회"
        result = _preprocess_for_normalization(text)
        assert result == text


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 구조 검증 테스트
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestValidator:
    """구조 검증기 테스트."""

    def test_valid_structure_passes(self):
        data = {
            "intent": {"primary": "EXTRACT", "secondary": []},
            "entities": [
                {
                    "term": "고객",
                    "type": "DIRECT",
                    "confidence": "HIGH",
                },
            ],
            "measures": [],
            "dimensions": [],
            "filters": [],
            "time": {"type": "NONE"},
            "modifiers": [],
            "output_hint": {"format": "NONE"},
        }
        result, errors = _validate_structure(data)
        assert result["intent"]["primary"] == "EXTRACT"
        assert len(errors) == 0

    def test_invalid_intent_corrected(self):
        data = {
            "intent": {"primary": "INVALID", "secondary": []},
            "entities": [],
            "measures": [],
            "dimensions": [],
            "filters": [],
            "time": {"type": "NONE"},
            "modifiers": [],
            "output_hint": {"format": "NONE"},
        }
        result, errors = _validate_structure(data)
        assert result["intent"]["primary"] == "EXTRACT"
        assert len(errors) > 0

    def test_case_insensitive_correction(self):
        data = {
            "intent": {"primary": "extract", "secondary": []},
            "entities": [],
            "measures": [],
            "dimensions": [],
            "filters": [],
            "time": {"type": "none"},
            "modifiers": [],
            "output_hint": {"format": "none"},
        }
        result, errors = _validate_structure(data)
        assert result["intent"]["primary"] == "EXTRACT"

    def test_invalid_modifier_removed(self):
        data = {
            "intent": {"primary": "EXTRACT", "secondary": []},
            "entities": [],
            "measures": [],
            "dimensions": [],
            "filters": [],
            "time": {"type": "NONE"},
            "modifiers": [{"type": "INVALID_TYPE"}],
            "output_hint": {"format": "NONE"},
        }
        result, errors = _validate_structure(data)
        assert len(result["modifiers"]) == 0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 후처리 테스트
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestPostProcessor:
    """후처리기 테스트."""

    def test_aggregate_forces_agg_function(self):
        data = {
            "intent": {"primary": "AGGREGATE", "secondary": []},
            "dimensions": [{"role": "GROUP", "term": "지점"}],
            "measures": [
                {"term": "잔액", "agg_function": "NONE"},
            ],
            "modifiers": [],
            "output_hint": {},
            "search_keywords": {"meta_search": []},
        }
        result = _postprocess(data)
        assert result["measures"][0]["agg_function"] == "SUM"

    def test_rank_fills_by_field(self):
        data = {
            "intent": {"primary": "RANK", "secondary": []},
            "dimensions": [],
            "measures": [
                {"term": "여신잔액", "agg_function": "SUM"},
            ],
            "modifiers": [
                {"type": "RANK", "by": None},
            ],
            "output_hint": {},
            "search_keywords": {"meta_search": []},
        }
        result = _postprocess(data)
        assert result["modifiers"][0]["by"] == "여신잔액"

    def test_output_hint_columns_merged_to_meta_search(self):
        data = {
            "intent": {"primary": "EXTRACT", "secondary": []},
            "dimensions": [],
            "measures": [],
            "modifiers": [],
            "output_hint": {
                "expected_columns": ["대출번호", "고객명"],
            },
            "search_keywords": {
                "meta_search": ["대출"],
            },
        }
        result = _postprocess(data)
        meta = result["search_keywords"]["meta_search"]
        assert "고객명" in meta
        assert "대출번호" in meta

    def test_stopwords_removed_from_meta_search(self):
        data = {
            "intent": {"primary": "EXTRACT", "secondary": []},
            "dimensions": [],
            "measures": [],
            "modifiers": [],
            "output_hint": {},
            "search_keywords": {
                "meta_search": ["대출", "해줘", "뽑아줘", "연체"],
            },
        }
        result = _postprocess(data)
        meta = result["search_keywords"]["meta_search"]
        assert "해줘" not in meta
        assert "뽑아줘" not in meta
        assert "대출" in meta
        assert "연체" in meta


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 동의어 사전 테스트
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestSynonyms:
    """동의어 사전 및 유틸리티 테스트."""

    def test_all_synonyms_has_four_categories(self):
        assert "measures" in ALL_SYNONYMS
        assert "entities" in ALL_SYNONYMS
        assert "dimensions" in ALL_SYNONYMS
        assert "time" in ALL_SYNONYMS

    def test_banking_measures_present(self):
        measures = ALL_SYNONYMS["measures"]
        assert "여신잔액" in measures
        assert "예금잔액" in measures
        assert "연체율" in measures
        assert "BIS비율" in measures

    def test_banking_entities_present(self):
        entities = ALL_SYNONYMS["entities"]
        assert "계좌" in entities
        assert "대출" in entities
        assert "지점" in entities

    def test_synonym_prompt_text_not_empty(self):
        text = serialize_synonym_dict(ALL_SYNONYMS)
        assert len(text) > 100
        assert "[measures]" in text

    def test_abbreviation_map_has_banking_terms(self):
        assert "NIM" in ABBREVIATION_MAP
        assert "NPL" in ABBREVIATION_MAP
        assert "BIS" in ABBREVIATION_MAP
        assert "LCR" in ABBREVIATION_MAP
