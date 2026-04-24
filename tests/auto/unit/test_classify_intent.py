"""의도 분류 서비스 단위 테스트.

=== 개념 설명 ===
사용자 질의를 DATA_EXTRACTION, DATA_ANALYSIS, CASUAL_TALK, META_QUESTION,
CLARIFICATION_NEEDED 등으로 분류하는 서비스를 검증한다.

두 모듈을 함께 검증한다:
  - src/services/intent_classifier.py  (순수 함수: _parse_response, _map_category_to_intent, _format_history)
  - src/agents/nodes/interpret/intent_classifier.py  (노드: _build_trace)

LLM 없이 검증 가능한 순수 함수 경계만 단위 테스트로 분리하며,
실제 LLM 호출이 필요한 테스트는 live_llm 마커로 구분한다.

=== 단독 실행 ===
    python -m pytest tests/auto/unit/test_classify_intent.py -v -s
    python -m pytest tests/auto/unit/test_classify_intent.py -v -k "not live_llm"

=== 정상 결과 ===
    _map_category_to_intent: (IntentType, float) 반환
    _parse_response: 평탄화된 dict 반환 (resolution, category, confidence 포함)
    _format_history: 멀티턴 이력을 프롬프트 텍스트로 변환
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tests.conftest import get_test_logger, log_test_case

logger = get_test_logger("test_classify_intent")

_HAS_API_KEY = bool(os.getenv("ANTHROPIC_API_KEY"))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 헬퍼 팩토리
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _make_continuity_json(
    label: str = "NEW",
    confidence: str = "HIGH",
    reason: str = "독립 질의",
    context: str = "",
    intent_label: str = "DATA_EXTRACTION",
    intent_confidence: str = "HIGH",
    intent_reason: str = "데이터 추출 요청",
) -> str:
    """_parse_response 테스트용 LLM 응답 JSON 문자열을 생성한다."""
    data: dict = {
        "continuity": {
            "label": label,
            "confidence": confidence,
            "reason": reason,
        },
        "intent": {
            "label": intent_label,
            "confidence": intent_confidence,
            "reason": intent_reason,
        },
    }
    if context:
        data["continuity"]["context"] = context
    return json.dumps(data, ensure_ascii=False)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _map_category_to_intent 테스트
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestMapCategoryToIntent:
    """카테고리 → IntentType 매핑 및 신뢰도 변환 테스트."""

    @pytest.mark.parametrize("category,expected_value", [
        ("DATA_EXTRACTION", "data_extraction"),
        ("DATA_ANALYSIS", "data_analysis"),
        ("DATA_QUERY", "data_extraction"),      # 하위 호환 별칭
        ("CASUAL_TALK", "casual_talk"),
        ("META_QUESTION", "meta_question"),
        ("CLARIFICATION", "clarification_needed"),
        ("AMBIGUOUS", "clarification_needed"),
        ("UNKNOWN_XYZ", "clarification_needed"),  # 미인식 → 폴백
    ])
    def test_category_to_intent_value(self, category: str, expected_value: str):
        """각 카테고리가 올바른 IntentType 값으로 변환된다."""
        from src.services.intent_classifier import _map_category_to_intent

        intent, _ = _map_category_to_intent(category, "HIGH")
        passed = intent.value == expected_value
        log_test_case(logger, f"test_map_{category}", category, expected_value, intent.value, passed)
        assert intent.value == expected_value

    @pytest.mark.parametrize("conf_str,expected_value", [
        ("HIGH", 0.95),
        ("MEDIUM", 0.7),
        ("LOW", 0.4),
        ("UNKNOWN_CONF", 0.5),  # 미인식 → 기본값 0.5
    ])
    def test_confidence_string_to_float(self, conf_str: str, expected_value: float):
        """신뢰도 문자열이 올바른 float로 변환된다."""
        from src.services.intent_classifier import _map_category_to_intent

        _, confidence = _map_category_to_intent("DATA_EXTRACTION", conf_str)
        passed = confidence == expected_value
        log_test_case(logger, f"test_confidence_{conf_str}", conf_str, expected_value, confidence, passed)
        assert confidence == expected_value


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _parse_response 테스트
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestParseResponse:
    """LLM 중첩 JSON 응답 파싱 테스트."""

    def test_new_resolution_parsed(self):
        """NEW resolution의 JSON이 올바르게 평탄화된다."""
        from src.services.intent_classifier import _parse_response
        from src.models.enums import HistoryDecision

        raw = _make_continuity_json(label="NEW", intent_label="DATA_EXTRACTION")
        result = _parse_response(raw)

        passed = result["resolution"] == HistoryDecision.NEW
        log_test_case(logger, "test_new_resolution", "NEW", HistoryDecision.NEW, result["resolution"], passed)
        assert result["resolution"] == HistoryDecision.NEW
        assert result["category"] == "DATA_EXTRACTION"

    def test_continue_resolution_includes_context(self):
        """CONTINUE resolution에서 continue_reason과 continue_context가 추출된다."""
        from src.services.intent_classifier import _parse_response
        from src.models.enums import HistoryDecision

        raw = _make_continuity_json(
            label="CONTINUE",
            reason="이전 대화 이어짐",
            context="이번 달 신규 고객의 평균 나이를 알려줘",
            intent_label="DATA_EXTRACTION",
        )
        result = _parse_response(raw)

        passed = result["resolution"] == HistoryDecision.CONTINUE
        log_test_case(logger, "test_continue_resolution", "CONTINUE", HistoryDecision.CONTINUE,
                      result["resolution"], passed)
        assert result["resolution"] == HistoryDecision.CONTINUE
        assert result.get("continue_reason") == "이전 대화 이어짐"
        assert result.get("continue_context") == "이번 달 신규 고객의 평균 나이를 알려줘"

    def test_unsure_resolution_allows_empty_intent(self):
        """UNSURE resolution에서 intent label이 없으면 AMBIGUOUS로 폴백된다."""
        from src.services.intent_classifier import _parse_response
        from src.models.enums import HistoryDecision

        raw = json.dumps({
            "continuity": {"label": "UNSURE", "confidence": "LOW", "reason": "불확실"},
            "intent": {"label": "", "confidence": "LOW", "reason": ""},
        })
        result = _parse_response(raw)

        passed = result["resolution"] == HistoryDecision.UNSURE
        log_test_case(logger, "test_unsure_empty_intent", "UNSURE+no_intent",
                      "UNSURE+AMBIGUOUS", f"{result['resolution']}+{result['category']}", passed)
        assert result["resolution"] == HistoryDecision.UNSURE
        assert result["category"] == "AMBIGUOUS"

    def test_invalid_json_raises(self):
        """유효하지 않은 JSON은 json.JSONDecodeError를 발생시킨다."""
        from src.services.intent_classifier import _parse_response

        with pytest.raises((json.JSONDecodeError, ValueError, KeyError)):
            _parse_response("이것은 JSON이 아닙니다")

        log_test_case(logger, "test_invalid_json", "not-json", "예외 발생", "예외 발생", True)

    def test_invalid_continuity_label_raises(self):
        """허용되지 않는 continuity.label은 ValueError를 발생시킨다."""
        from src.services.intent_classifier import _parse_response

        raw = json.dumps({
            "continuity": {"label": "INVALID_LABEL", "confidence": "HIGH", "reason": ""},
            "intent": {"label": "DATA_EXTRACTION", "confidence": "HIGH", "reason": ""},
        })
        with pytest.raises(ValueError, match="허용되지 않는"):
            _parse_response(raw)

        log_test_case(logger, "test_invalid_label", "INVALID_LABEL", "ValueError", "ValueError", True)

    def test_code_fence_stripped(self):
        """코드 펜스로 감싼 JSON도 파싱된다."""
        from src.services.intent_classifier import _parse_response
        from src.models.enums import HistoryDecision

        inner = _make_continuity_json(label="NEW", intent_label="CASUAL_TALK")
        raw = f"```json\n{inner}\n```"
        result = _parse_response(raw)

        passed = result["resolution"] == HistoryDecision.NEW
        log_test_case(logger, "test_code_fence", "```json...```", HistoryDecision.NEW,
                      result["resolution"], passed)
        assert result["resolution"] == HistoryDecision.NEW
        assert result["category"] == "CASUAL_TALK"

    def test_clarification_category_normalized_to_ambiguous(self):
        """intent.label='CLARIFICATION'은 'AMBIGUOUS'로 정규화된다."""
        from src.services.intent_classifier import _parse_response

        raw = _make_continuity_json(label="NEW", intent_label="CLARIFICATION")
        result = _parse_response(raw)

        passed = result["category"] == "AMBIGUOUS"
        log_test_case(logger, "test_clarification_to_ambiguous", "CLARIFICATION",
                      "AMBIGUOUS", result["category"], passed)
        assert result["category"] == "AMBIGUOUS"

    def test_ambiguities_extracted(self):
        """최상위 ambiguities 배열이 파싱 결과에 포함된다."""
        from src.services.intent_classifier import _parse_response

        amb = [{"ambiguity_type": "INTENT", "question": "추출인가요 분석인가요?"}]
        data = json.loads(_make_continuity_json(label="NEW", intent_label="AMBIGUOUS"))
        data["ambiguities"] = amb
        raw = json.dumps(data)
        result = _parse_response(raw)

        passed = result.get("ambiguities") == amb
        log_test_case(logger, "test_ambiguities", amb, amb, result.get("ambiguities"), passed)
        assert result.get("ambiguities") == amb

    def test_data_analysis_category(self):
        """DATA_ANALYSIS 카테고리가 그대로 보존된다."""
        from src.services.intent_classifier import _parse_response

        raw = _make_continuity_json(label="NEW", intent_label="DATA_ANALYSIS")
        result = _parse_response(raw)

        passed = result["category"] == "DATA_ANALYSIS"
        log_test_case(logger, "test_data_analysis_category", "DATA_ANALYSIS",
                      "DATA_ANALYSIS", result["category"], passed)
        assert result["category"] == "DATA_ANALYSIS"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _format_history 테스트
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestFormatHistory:
    """대화 이력 포맷팅 테스트."""

    def test_empty_history_returns_empty_string(self):
        """빈 이력은 빈 문자열을 반환한다."""
        from src.services.intent_classifier import _format_history

        result = _format_history([])
        passed = result == ""
        log_test_case(logger, "test_empty_history", "[]", "", result, passed)
        assert result == ""

    def test_user_message_prefixed_correctly(self):
        """user role 메시지는 '사용자:' 접두사로 출력된다."""
        from src.services.intent_classifier import _format_history

        history = [{"role": "user", "content": "안녕하세요"}]
        result = _format_history(history)

        passed = "사용자" in result and "안녕하세요" in result
        log_test_case(logger, "test_user_prefix", history, "사용자: 안녕하세요", result, passed)
        assert "사용자" in result
        assert "안녕하세요" in result

    def test_assistant_message_prefixed_correctly(self):
        """assistant role 메시지는 '시스템:' 접두사로 출력된다."""
        from src.services.intent_classifier import _format_history

        history = [{"role": "assistant", "content": "무엇을 도와드릴까요?"}]
        result = _format_history(history)

        passed = "시스템" in result
        log_test_case(logger, "test_assistant_prefix", history, "시스템:", result, passed)
        assert "시스템" in result

    def test_clarification_turns_excluded(self):
        """type='clarification' 항목은 [명확화] 태그로 구분되어 포함된다."""
        from src.services.intent_classifier import _format_history

        history = [
            {"role": "user", "content": "정상 질의", "type": "query"},
            {"role": "assistant", "content": "명확화 질문", "type": "clarification"},
            {"role": "user", "content": "명확화 응답", "type": "clarification"},
        ]
        result = _format_history(history)

        # clarification 항목은 [명확화] 태그와 함께 포함됨
        passed = "[명확화]" in result and "명확화 질문" in result
        log_test_case(logger, "test_clarification_excluded", "clarification type",
                      "[명확화] 태그 포함", result, passed)
        assert "[명확화]" in result
        assert "명확화 질문" in result

    def test_max_turns_limits_output(self):
        """max_turns를 초과하는 이력은 최근 max_turns개만 포함된다."""
        from src.services.intent_classifier import _format_history

        history = [
            {"role": "user", "content": f"질의 {i}"}
            for i in range(10)
        ]
        result = _format_history(history, max_turns=3)

        # 최근 3개의 내용만 포함돼야 한다
        lines = [ln for ln in result.strip().split("\n") if ln.strip()]
        passed = len(lines) <= 3
        log_test_case(logger, "test_max_turns", "10개 이력, max=3", "3개 이하", len(lines), passed)
        assert len(lines) <= 3


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# IntentClassifyResult 생성자 테스트
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestIntentClassifyResult:
    """IntentClassifyResult 기본 동작 테스트."""

    def test_default_ambiguities_is_empty_list(self):
        """ambiguities 기본값은 빈 리스트다 (__post_init__ 검증)."""
        from src.services.intent_classifier import IntentClassifyResult
        from src.models.enums import HistoryDecision

        result = IntentClassifyResult(resolution=HistoryDecision.NEW)

        passed = result.ambiguities == []
        log_test_case(logger, "test_default_ambiguities", "no ambiguities", [], result.ambiguities, passed)
        assert result.ambiguities == []

    def test_is_error_default_false(self):
        """is_error 기본값은 False다."""
        from src.services.intent_classifier import IntentClassifyResult
        from src.models.enums import HistoryDecision

        result = IntentClassifyResult(resolution=HistoryDecision.SKIP)

        passed = result.is_error is False
        log_test_case(logger, "test_is_error_default", "SKIP result", False, result.is_error, passed)
        assert result.is_error is False

    def test_error_result_construction(self):
        """에러 결과가 올바른 필드를 가진다."""
        from src.services.intent_classifier import IntentClassifyResult
        from src.models.enums import HistoryDecision, IntentType

        result = IntentClassifyResult(
            resolution=HistoryDecision.SKIP,
            intent=IntentType.UNKNOWN,
            is_error=True,
        )

        passed = result.is_error is True and result.intent == IntentType.UNKNOWN
        log_test_case(logger, "test_error_result", "is_error=True",
                      "SKIP+UNKNOWN", f"{result.resolution}+{result.intent}", passed)
        assert result.is_error is True
        assert result.intent == IntentType.UNKNOWN


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LLM 실제 호출 테스트
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.live_llm
@pytest.mark.skipif(not _HAS_API_KEY, reason="ANTHROPIC_API_KEY 환경 변수 필요")
@pytest.mark.asyncio
async def test_intent_classifier_data_extraction_live():
    """실제 LLM 호출로 데이터 추출 의도를 분류한다."""
    from src.services.intent_classifier import intent_classifier
    from src.agents.nodes.system_prompts import (
        INTENT_CLASSIFIER_SYSTEM,
        INTENT_CLASSIFIER_USER,
    )
    from src.models.enums import IntentType, HistoryDecision

    result, _ = await intent_classifier(
        query="이번 달 신규 고객 수 알려줘",
        conversation_history=[],
        system_prompt=INTENT_CLASSIFIER_SYSTEM,
        user_template=INTENT_CLASSIFIER_USER,
    )

    log_test_case(
        logger, "test_data_extraction_live",
        "이번 달 신규 고객 수 알려줘",
        "DATA_EXTRACTION/DATA_ANALYSIS",
        f"{result.intent.value} ({result.confidence:.2f})",
        not result.is_error,
    )

    assert not result.is_error
    assert result.intent in (IntentType.DATA_EXTRACTION, IntentType.DATA_ANALYSIS)
    assert 0.0 <= result.confidence <= 1.0
    assert result.resolution in (HistoryDecision.NEW, HistoryDecision.SKIP)


@pytest.mark.live_llm
@pytest.mark.skipif(not _HAS_API_KEY, reason="ANTHROPIC_API_KEY 환경 변수 필요")
@pytest.mark.asyncio
async def test_intent_classifier_casual_talk_live():
    """실제 LLM 호출로 일반 대화를 분류한다."""
    from src.services.intent_classifier import intent_classifier
    from src.agents.nodes.system_prompts import (
        INTENT_CLASSIFIER_SYSTEM,
        INTENT_CLASSIFIER_USER,
    )
    from src.models.enums import IntentType

    result, _ = await intent_classifier(
        query="안녕하세요 좋은 아침이에요",
        conversation_history=[],
        system_prompt=INTENT_CLASSIFIER_SYSTEM,
        user_template=INTENT_CLASSIFIER_USER,
    )

    log_test_case(
        logger, "test_casual_talk_live",
        "안녕하세요 좋은 아침이에요",
        "CASUAL_TALK",
        result.intent.value,
        not result.is_error,
    )

    assert not result.is_error
    assert result.intent in (
        IntentType.CASUAL_TALK,
        IntentType.CLARIFICATION_NEEDED,
        IntentType.GENERAL_QUESTION,
    )


@pytest.mark.live_llm
@pytest.mark.skipif(not _HAS_API_KEY, reason="ANTHROPIC_API_KEY 환경 변수 필요")
@pytest.mark.asyncio
async def test_intent_classifier_continuation_live():
    """실제 LLM 호출로 멀티턴 연속 질의를 판정한다."""
    from src.services.intent_classifier import intent_classifier
    from src.agents.nodes.system_prompts import (
        INTENT_CLASSIFIER_SYSTEM,
        INTENT_CLASSIFIER_USER,
    )
    from src.models.enums import HistoryDecision

    history = [
        {"role": "user", "content": "이번 달 신규 고객 수 알려줘"},
        {"role": "assistant", "content": "이번 달 신규 고객은 1,234명입니다."},
    ]

    result, _ = await intent_classifier(
        query="그 중 VIP 고객은 몇 명이야?",
        conversation_history=history,
        system_prompt=INTENT_CLASSIFIER_SYSTEM,
        user_template=INTENT_CLASSIFIER_USER,
    )

    log_test_case(
        logger, "test_continuation_live",
        "그 중 VIP 고객은 몇 명이야?",
        "CONTINUE 또는 NEW",
        result.resolution.value,
        not result.is_error,
    )

    assert not result.is_error
    # 이전 대화 맥락이 있으므로 CONTINUE 또는 NEW가 모두 합리적
    assert result.resolution in (HistoryDecision.CONTINUE, HistoryDecision.NEW)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# needs_analyzer 플래그 파싱 테스트
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _make_intent_json(
    intent_label: str = "DATA_ANALYSIS",
    needs_analyzer=...,
    needs_analyzer_reason: str = "",
) -> str:
    """_parse_response needs_analyzer 테스트 전용 JSON 빌더.

    needs_analyzer=... (Ellipsis)이면 필드 자체를 생략하여 누락 케이스를 만든다.
    """
    intent: dict = {
        "label": intent_label,
        "confidence": "HIGH",
        "label_reason": "테스트",
    }
    if needs_analyzer is not ...:
        intent["needs_analyzer"] = needs_analyzer
    if needs_analyzer_reason:
        intent["needs_analyzer_reason"] = needs_analyzer_reason
    return json.dumps({
        "continuity": {"label": "NEW", "confidence": "HIGH", "reason": "독립"},
        "intent": intent,
    }, ensure_ascii=False)


class TestNeedsAnalyzerParse:
    """needs_analyzer 필드 파싱 동작 테스트.

    opt-in 원칙: 본 서비스는 명세 추출이 주 업무이므로 analyzer는 기본 False.
    LLM이 true(또는 "true"/"yes"/"1")를 명시 반환할 때만 True로 평가한다.
    """

    def test_field_missing_parsed_as_false(self):
        """needs_analyzer 필드 누락 → False (opt-in, get 기본값 False)."""
        from src.services.intent_classifier import _parse_response
        raw = _make_intent_json(needs_analyzer=...)
        result = _parse_response(raw)
        assert result["needs_analyzer"] is False

    def test_empty_string_parsed_as_false(self):
        """빈 문자열 → False (명시 true 아님, opt-in 미충족)."""
        from src.services.intent_classifier import _parse_response
        raw = _make_intent_json(needs_analyzer="")
        result = _parse_response(raw)
        assert result["needs_analyzer"] is False

    @pytest.mark.parametrize("raw_value", [
        False, "false", "False", "FALSE", "0", "no", "NO", " false ",
    ])
    def test_falsy_values_parsed_as_false(self, raw_value):
        """bool False 및 falsy 문자열 변종 → False."""
        from src.services.intent_classifier import _parse_response
        raw = _make_intent_json(needs_analyzer=raw_value)
        result = _parse_response(raw)
        assert result["needs_analyzer"] is False, f"{raw_value!r} should be False"

    @pytest.mark.parametrize("raw_value", [
        True, "true", "True", "yes", "1",
    ])
    def test_truthy_values_parsed_as_true(self, raw_value):
        """bool True 및 명시 true 문자열 → True."""
        from src.services.intent_classifier import _parse_response
        raw = _make_intent_json(needs_analyzer=raw_value)
        result = _parse_response(raw)
        assert result["needs_analyzer"] is True, f"{raw_value!r} should be True"

    def test_reason_field_propagated(self):
        """needs_analyzer_reason 필드가 결과에 전파된다."""
        from src.services.intent_classifier import _parse_response
        raw = _make_intent_json(
            needs_analyzer=False,
            needs_analyzer_reason="시각화 형식 지시어만 포함",
        )
        result = _parse_response(raw)
        assert result["needs_analyzer"] is False
        assert result["needs_analyzer_reason"] == "시각화 형식 지시어만 포함"

    def test_default_value_in_dataclass(self):
        """IntentClassifyResult.needs_analyzer 기본값 False (opt-in)."""
        from src.services.intent_classifier import IntentClassifyResult
        from src.models.enums import HistoryDecision
        result = IntentClassifyResult(resolution=HistoryDecision.NEW)
        assert result.needs_analyzer is False
        assert result.needs_analyzer_reason == ""
