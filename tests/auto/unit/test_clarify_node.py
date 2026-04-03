"""통합 명확화 노드(clarification_handler) 테스트.

테스트 대상:
    clarification_handler 노드의 가드레일(_should_override_to_ask),
    응답 검증(validate_answer), 시그널 분류(ASK/INFER) 로직을 검증한다.

    ┌─────────────────────────────────────────────────────────────────┐
    │  테스트 구간                테스트 내용                LLM 필요   │
    │  ──────────────────────── ──────────────────────────── ────── │
    │  _should_override_to_ask   가드레일 보정 로직              X     │
    │  validate_answer            응답 검증 로직                 X     │
    │  clarification_handler_node       시그널 분류, INFER 처리        X     │
    └─────────────────────────────────────────────────────────────────┘

실행 스크립트:
    pytest tests/auto/unit/test_clarify_node.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from tests.conftest import get_test_logger, log_test_case

logger = get_test_logger("test_clarify_node")


# ──────────────────────────────────────────────────────────────
# 헬퍼
# ──────────────────────────────────────────────────────────────

def _make_signal(
    ambiguity_type: str = "INTENT",
    decision: str = "ASK",
    confidence: str = "LOW",
    question: str = "테스트 질문?",
    options: list[str] | None = None,
    source_node: str = "test",
    question_type: str = "free_text",
) -> "AmbiguitySignal":
    from src.agents.models.clarification import (
        AmbiguitySignal,
        AmbiguityType,
        ConfidenceLevel,
        QuestionType,
    )
    return AmbiguitySignal(
        source_node=source_node,
        ambiguity_type=AmbiguityType(ambiguity_type),
        decision=decision,
        confidence=ConfidenceLevel(confidence),
        question=question,
        question_type=QuestionType(question_type),
        options=options or [],
        reasoning="테스트",
    )


def _make_state(
    pending_signals: list | None = None,
) -> "PipelineState":
    from src.agents.state.state import PipelineState
    return PipelineState(
        user_input="테스트",
        preprocessed_input="테스트",
        pending_signals=pending_signals or [],
    )


# ──────────────────────────────────────────────────────────────
# _should_override_to_ask 가드레일 테스트
# ──────────────────────────────────────────────────────────────

class TestShouldOverrideToAsk:
    """가드레일 보정 로직 테스트."""

    def test_ask_signal_not_overridden(self):
        """ASK 시그널은 보정하지 않는다."""
        from src.agents.nodes.interpret.clarification_handler import (
            _should_override_to_ask,
        )
        signal = _make_signal(decision="ASK")
        state = _make_state()
        result = _should_override_to_ask(signal, state)

        passed = result is None
        log_test_case(logger, "test_ask_not_overridden", "ASK signal",
                      "None (보정 불필요)", str(result), passed)
        assert passed

    def test_formula_infer_overridden(self):
        """FORMULA 타입 INFER는 ASK로 보정된다 (금융 규제)."""
        from src.agents.nodes.interpret.clarification_handler import (
            _should_override_to_ask,
        )
        signal = _make_signal(
            ambiguity_type="FORMULA", decision="INFER",
        )
        state = _make_state()
        result = _should_override_to_ask(signal, state)

        passed = result is not None and "산출식" in result
        log_test_case(logger, "test_formula_override", "FORMULA INFER",
                      "보정 사유 반환", str(result), passed)
        assert passed

    def test_table_low_confidence_overridden(self):
        """TABLE 타입 + LOW 확신도 + 2개 이상 옵션 → ASK로 보정."""
        from src.agents.nodes.interpret.clarification_handler import (
            _should_override_to_ask,
        )
        signal = _make_signal(
            ambiguity_type="TABLE", decision="INFER",
            confidence="LOW", options=["TB_A", "TB_B"],
        )
        state = _make_state()
        result = _should_override_to_ask(signal, state)

        passed = result is not None and "테이블" in result
        log_test_case(logger, "test_table_low_override", "TABLE LOW 2 options",
                      "보정 사유 반환", str(result), passed)
        assert passed

    def test_intent_low_confidence_overridden(self):
        """INTENT 타입 + LOW 확신도 → ASK로 보정."""
        from src.agents.nodes.interpret.clarification_handler import (
            _should_override_to_ask,
        )
        signal = _make_signal(
            ambiguity_type="INTENT", decision="INFER",
            confidence="LOW",
        )
        state = _make_state()
        result = _should_override_to_ask(signal, state)

        passed = result is not None and "의도" in result
        log_test_case(logger, "test_intent_low_override", "INTENT LOW INFER",
                      "보정 사유 반환", str(result), passed)
        assert passed

    def test_table_high_confidence_not_overridden(self):
        """TABLE 타입 + HIGH 확신도 → 보정 안 함."""
        from src.agents.nodes.interpret.clarification_handler import (
            _should_override_to_ask,
        )
        signal = _make_signal(
            ambiguity_type="TABLE", decision="INFER",
            confidence="HIGH", options=["TB_A"],
        )
        state = _make_state()
        result = _should_override_to_ask(signal, state)

        passed = result is None
        log_test_case(logger, "test_table_high_no_override", "TABLE HIGH INFER",
                      "None (보정 불필요)", str(result), passed)
        assert passed


# ──────────────────────────────────────────────────────────────
# validate_answer 응답 검증 테스트
# ──────────────────────────────────────────────────────────────

class TestValidateAnswer:
    """사용자 응답 검증 로직."""

    def test_free_text_accepted(self):
        """자유 텍스트 응답은 그대로 반환된다."""
        from src.agents.nodes.interpret.clarification_handler import (
            validate_answer,
        )
        signal = _make_signal(question_type="free_text")
        result = validate_answer("이번 달 기준으로 알려줘", signal)

        passed = result == "이번 달 기준으로 알려줘"
        log_test_case(logger, "test_free_text", "자유 텍스트 응답",
                      "원문 반환", result, passed)
        assert passed

    def test_single_select_by_number(self):
        """SINGLE_SELECT: 번호 입력으로 선택."""
        from src.agents.nodes.interpret.clarification_handler import (
            validate_answer,
        )
        signal = _make_signal(
            question_type="single_select",
            options=["기본 목록", "집계 요약", "상세 내역"],
        )
        result = validate_answer("2", signal)

        passed = result == "집계 요약"
        log_test_case(logger, "test_select_by_number", "2",
                      "집계 요약", result, passed)
        assert passed

    def test_single_select_by_text(self):
        """SINGLE_SELECT: 텍스트 입력으로 선택."""
        from src.agents.nodes.interpret.clarification_handler import (
            validate_answer,
        )
        signal = _make_signal(
            question_type="single_select",
            options=["기본 목록", "집계 요약"],
        )
        result = validate_answer("기본 목록", signal)

        passed = result == "기본 목록"
        log_test_case(logger, "test_select_by_text", "기본 목록",
                      "기본 목록", result, passed)
        assert passed

    def test_single_select_invalid_raises(self):
        """SINGLE_SELECT: 잘못된 선택 → ValueError."""
        from src.agents.nodes.interpret.clarification_handler import (
            validate_answer,
        )
        signal = _make_signal(
            question_type="single_select",
            options=["A", "B"],
        )
        with pytest.raises(ValueError, match="선택지"):
            validate_answer("C", signal)
        log_test_case(logger, "test_select_invalid", "C",
                      "ValueError", "ValueError", True)

    def test_empty_answer_raises(self):
        """빈 응답 → ValueError."""
        from src.agents.nodes.interpret.clarification_handler import (
            validate_answer,
        )
        signal = _make_signal()
        with pytest.raises(ValueError, match="비어있습니다"):
            validate_answer("", signal)
        log_test_case(logger, "test_empty_answer", "",
                      "ValueError", "ValueError", True)


# ──────────────────────────────────────────────────────────────
# clarification_handler_node INFER 처리 테스트
# ──────────────────────────────────────────────────────────────

class TestClarifyUnifiedInfer:
    """INFER 시그널 처리 (interrupt 없이 진행)."""

    @pytest.mark.asyncio
    async def test_empty_signals_noop(self):
        """시그널 없으면 빈 결과 반환."""
        from src.agents.nodes.interpret.clarification_handler import (
            clarification_handler_node,
        )
        state = _make_state(pending_signals=[])
        result = await clarification_handler_node(state)

        passed = result == {}
        log_test_case(logger, "test_empty_signals", "signals=[]",
                      "{}", str(result), passed)
        assert passed

    @pytest.mark.asyncio
    async def test_infer_signals_resolved(self):
        """INFER 시그널은 resolved_signals로 이동한다."""
        from src.agents.nodes.interpret.clarification_handler import (
            clarification_handler_node,
        )
        signal = _make_signal(
            ambiguity_type="TIMEFRAME", decision="INFER",
            confidence="HIGH",
        )
        state = _make_state(pending_signals=[signal])
        result = await clarification_handler_node(state)

        passed = (
            len(result.get("resolved_signals", [])) == 1
            and result.get("pending_signals") == []
        )
        log_test_case(
            logger, "test_infer_resolved",
            "INFER signal", "resolved=1, pending=0",
            f"resolved={len(result.get('resolved_signals', []))}", passed,
        )
        assert passed
