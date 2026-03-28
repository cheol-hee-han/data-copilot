"""의도 분류 노드 단위 테스트.

=== 개념 설명 ===
사용자 질의를 DATA_EXTRACTION, DATA_ANALYSIS, CASUAL_TALK, META_QUESTION,
CLARIFICATION_NEEDED 등으로 분류하는 노드이다.
Intent Gate(5-카테고리)와 Legacy(2-카테고리) 두 가지 경로가 있으며,
잘못된 분류는 전체 파이프라인 경로를 틀리게 하여 답변 품질에 치명적 영향을 준다.

=== 단독 실행 ===
    python -m pytest tests/unit/intent_classifier/test_classify_intent.py -v -s

    # 순수 함수 테스트만 실행 (LLM 불필요):
    python -m pytest tests/unit/intent_classifier/test_classify_intent.py -v -k "not live_llm"

=== 테스트 데이터 예시 ===
    데이터 추출: "이번 달 신규 고객 수 알려줘" → DATA_EXTRACTION
    데이터 분석: "분기별 대출 추이를 분석해줘" → DATA_ANALYSIS
    일반 대화:   "안녕하세요" → CASUAL_TALK
    메타 질의:   "고객 테이블 컬럼 목록" → META_QUESTION

=== 정상 결과 ===
    intent: IntentType 값, intent_confidence: 0.0~1.0, status: INTENT_CLASSIFIED
=== 오류 결과 ===
    LLM 오류 시 → intent=UNKNOWN, status=ERROR
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.agents.nodes.interpret.intent_classifier import (
    classify_intent_node,
)
from src.services.intent_resolver import (
    _map_category_to_intent,
    _parse_gate_response,
    _parse_intent_response,
    subclassify_data_query,
)
from src.agents.state.state import IntentType, PipelineState, QueryStatus
from tests.conftest import get_test_logger, log_test_case

logger = get_test_logger("test_classify_intent")

_HAS_API_KEY = bool(os.getenv("ANTHROPIC_API_KEY"))


# ══════════════════════════════════════════════════════════════
# 순수 함수 테스트 (LLM 불필요)
# ══════════════════════════════════════════════════════════════

class TestParseGateResponse:
    """Intent Gate LLM 응답 파싱 테스트."""

    def test_valid_json(self):
        """유효한 JSON 응답을 파싱한다."""
        raw = '{"category": "DATA_QUERY", "confidence": "HIGH", "reason": "데이터 추출 요청"}'
        result = _parse_gate_response(raw)

        assert result["category"] == "DATA_QUERY"
        assert result["confidence"] == "HIGH"
        log_test_case(logger, "test_parse_gate_valid_json", raw, "DATA_QUERY", result["category"], True)

    def test_json_with_code_fence(self):
        """코드 펜스로 감싼 JSON 을 파싱한다."""
        raw = '```json\n{"category": "CASUAL_TALK", "confidence": "HIGH"}\n```'
        result = _parse_gate_response(raw)

        assert result["category"] == "CASUAL_TALK"
        log_test_case(logger, "test_parse_gate_code_fence", raw, "CASUAL_TALK", result["category"], True)

    def test_invalid_json(self):
        """잘못된 JSON → AMBIGUOUS 로 폴백한다."""
        raw = "이것은 JSON이 아닙니다"
        result = _parse_gate_response(raw)

        assert result["category"] == "AMBIGUOUS"
        log_test_case(logger, "test_parse_gate_invalid_json", raw, "AMBIGUOUS", result["category"], True)

    def test_unknown_category(self):
        """미인식 카테고리 → AMBIGUOUS 로 보정된다."""
        raw = '{"category": "UNKNOWN_CAT", "confidence": "HIGH"}'
        result = _parse_gate_response(raw)

        assert result["category"] == "AMBIGUOUS"
        log_test_case(logger, "test_parse_gate_unknown_category", raw, "AMBIGUOUS", result["category"], True)


class TestMapCategoryToIntent:
    """카테고리 → IntentType 매핑 테스트."""

    @pytest.mark.parametrize("category,expected_intent", [
        ("DATA_QUERY", IntentType.DATA_EXTRACTION),
        ("CASUAL_TALK", IntentType.CASUAL_TALK),
        ("META_QUESTION", IntentType.META_QUESTION),
        ("CLARIFICATION", IntentType.CLARIFICATION_NEEDED),
        ("AMBIGUOUS", IntentType.CLARIFICATION_NEEDED),
    ])
    def test_category_mapping(self, category: str, expected_intent: IntentType):
        """각 카테고리가 올바른 IntentType 으로 매핑된다."""
        intent, confidence = _map_category_to_intent(category, "HIGH")

        passed = intent == expected_intent
        log_test_case(logger, f"test_map_{category}", category, expected_intent, intent, passed)
        assert intent == expected_intent

    @pytest.mark.parametrize("conf_str,expected_range", [
        ("HIGH", (0.9, 1.0)),
        ("MEDIUM", (0.6, 0.8)),
        ("LOW", (0.3, 0.5)),
    ])
    def test_confidence_mapping(self, conf_str: str, expected_range: tuple):
        """신뢰도 문자열이 올바른 float 범위로 변환된다."""
        _, confidence = _map_category_to_intent("DATA_QUERY", conf_str)

        low, high = expected_range
        passed = low <= confidence <= high
        log_test_case(logger, f"test_confidence_{conf_str}", conf_str, expected_range, confidence, passed)
        assert low <= confidence <= high


class TestSubclassifyDataQuery:
    """DATA_QUERY 세분류 테스트."""

    @pytest.mark.parametrize("text,expected", [
        ("분기별 대출 추이를 분석해줘", IntentType.DATA_ANALYSIS),
        ("연체율 비교 분석", IntentType.DATA_ANALYSIS),
        ("매출 트렌드 보여줘", IntentType.DATA_ANALYSIS),
        ("증감 추이 확인", IntentType.DATA_ANALYSIS),
    ])
    def test_analysis_keywords(self, text: str, expected: IntentType):
        """분석 키워드가 포함되면 DATA_ANALYSIS 로 분류된다."""
        state = PipelineState(preprocessed_input=text)
        intent, _ = subclassify_data_query(state.preprocessed_input, 0.9)

        passed = intent == expected
        log_test_case(logger, "test_analysis_keywords", text, expected, intent, passed)
        assert intent == expected

    @pytest.mark.parametrize("text", [
        "이번달 신규 고객 수 알려줘",
        "부서별 대출 잔액 뽑아줘",
        "고객 목록 조회해줘",
    ])
    def test_extraction_default(self, text: str):
        """분석 키워드가 없으면 DATA_EXTRACTION 으로 분류된다."""
        state = PipelineState(preprocessed_input=text)
        intent, _ = subclassify_data_query(state.preprocessed_input, 0.9)

        passed = intent == IntentType.DATA_EXTRACTION
        log_test_case(logger, "test_extraction_default", text, "DATA_EXTRACTION", intent, passed)
        assert intent == IntentType.DATA_EXTRACTION


class TestParseLegacyResponse:
    """레거시 의도 분류 응답 파싱 테스트."""

    def test_valid_response(self):
        """정상 형식 응답을 파싱한다."""
        text = "INTENT: data_extraction\nCONFIDENCE: 0.9"
        intent, confidence = _parse_intent_response(text)

        assert intent == IntentType.DATA_EXTRACTION
        assert confidence == 0.9
        log_test_case(logger, "test_parse_legacy_valid", text, "data_extraction/0.9",
                      f"{intent}/{confidence}", True)

    def test_missing_intent(self):
        """INTENT 행이 없으면 ValueError 를 발생시킨다."""
        text = "CONFIDENCE: 0.9"
        with pytest.raises(ValueError):
            _parse_intent_response(text)
        log_test_case(logger, "test_parse_legacy_missing_intent", text, "ValueError", "ValueError", True)

    def test_missing_confidence_defaults(self):
        """CONFIDENCE 행이 없으면 0.5 로 기본값을 사용한다."""
        text = "INTENT: data_analysis"
        intent, confidence = _parse_intent_response(text)

        assert intent == IntentType.DATA_ANALYSIS
        assert confidence == 0.5
        log_test_case(logger, "test_parse_legacy_default_confidence", text, "0.5", confidence, True)

    def test_confidence_clamped(self):
        """신뢰도가 0~1 범위로 클램핑된다."""
        text = "INTENT: data_extraction\nCONFIDENCE: 1.5"
        _, confidence = _parse_intent_response(text)

        assert confidence == 1.0
        log_test_case(logger, "test_confidence_clamped", "1.5", "1.0", confidence, True)


# ══════════════════════════════════════════════════════════════
# LLM 실제 호출 테스트
# ══════════════════════════════════════════════════════════════

@pytest.mark.live_llm
@pytest.mark.skipif(not _HAS_API_KEY, reason="ANTHROPIC_API_KEY 환경 변수 필요")
@pytest.mark.asyncio
async def test_classify_data_extraction_live():
    """실제 LLM 호출로 데이터 추출 의도를 분류한다."""
    state = PipelineState(preprocessed_input="이번 달 신규 고객 수 알려줘")
    result = await classify_intent_node(state)

    intent = result["intent"]
    confidence = result["intent_confidence"]

    log_test_case(logger, "test_classify_data_extraction_live",
                  "이번 달 신규 고객 수 알려줘",
                  "DATA_EXTRACTION or DATA_ANALYSIS",
                  f"{intent} ({confidence})", True)

    assert result["status"] == QueryStatus.INTENT_CLASSIFIED
    assert intent in (IntentType.DATA_EXTRACTION, IntentType.DATA_ANALYSIS)
    assert 0.0 <= confidence <= 1.0


@pytest.mark.live_llm
@pytest.mark.skipif(not _HAS_API_KEY, reason="ANTHROPIC_API_KEY 환경 변수 필요")
@pytest.mark.asyncio
async def test_classify_casual_talk_live():
    """실제 LLM 호출로 일반 대화를 분류한다."""
    state = PipelineState(preprocessed_input="안녕하세요 좋은 아침이에요")
    result = await classify_intent_node(state)

    intent = result["intent"]
    log_test_case(logger, "test_classify_casual_talk_live",
                  "안녕하세요 좋은 아침이에요",
                  "CASUAL_TALK or CLARIFICATION_NEEDED",
                  str(intent), True)

    assert result["status"] == QueryStatus.INTENT_CLASSIFIED
    # 일반 대화는 CASUAL_TALK 또는 CLARIFICATION_NEEDED 둘 다 합리적
    assert intent in (IntentType.CASUAL_TALK, IntentType.CLARIFICATION_NEEDED,
                      IntentType.GENERAL_QUESTION)


@pytest.mark.live_llm
@pytest.mark.skipif(not _HAS_API_KEY, reason="ANTHROPIC_API_KEY 환경 변수 필요")
@pytest.mark.asyncio
async def test_classify_analysis_live():
    """실제 LLM 호출로 데이터 분석 의도를 분류한다."""
    state = PipelineState(preprocessed_input="분기별 대출 추이를 분석해줘")
    result = await classify_intent_node(state)

    intent = result["intent"]
    log_test_case(logger, "test_classify_analysis_live",
                  "분기별 대출 추이를 분석해줘",
                  "DATA_ANALYSIS", str(intent), True)

    assert result["status"] == QueryStatus.INTENT_CLASSIFIED
    assert intent == IntentType.DATA_ANALYSIS
