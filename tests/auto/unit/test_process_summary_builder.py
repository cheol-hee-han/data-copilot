"""5단계 조회 과정 요약 빌더 단위 테스트.

테스트 대상:
    src/services/process_summary_builder.py 의 build_process_summary 및 각 섹션 빌더.
    반환 타입: dict[str, Any] | None (구조화 JSON)

실행 스크립트:
    pytest tests/auto/unit/test_process_summary_builder.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.services.process_summary_builder import (
    build_process_summary,
    _build_intent_dict,
    _build_interpretation_dict,
    _build_context_dict,
    _build_ai_decision_dict,
    _build_validation_dict,
)


# ──────────────────────────────────────────────────────────────
# 헬퍼
# ──────────────────────────────────────────────────────────────

def _make_state(**overrides):
    """최소 PipelineState 생성."""
    from src.agents.state.state import PipelineState
    return PipelineState(**overrides)


# ══════════════════════════════════════════════════════════════
# 1단계: 질의 분류
# ══════════════════════════════════════════════════════════════

class TestIntentDict:
    """질의 분류 섹션 테스트."""

    def test_data_extraction(self):
        from src.models.enums import IntentType
        state = _make_state(intent=IntentType.DATA_EXTRACTION)
        result = _build_intent_dict(state)
        assert result["label"] == "데이터 추출"
        assert result["is_continuation"] is False

    def test_data_analysis(self):
        from src.models.enums import IntentType
        state = _make_state(intent=IntentType.DATA_ANALYSIS)
        result = _build_intent_dict(state)
        assert result["label"] == "데이터 분석"

    def test_continuation(self):
        from src.models.enums import IntentType
        state = _make_state(
            intent=IntentType.DATA_EXTRACTION,
            is_continuation=True,
        )
        result = _build_intent_dict(state)
        assert result["is_continuation"] is True


# ══════════════════════════════════════════════════════════════
# 2단계: 질의 해석
# ══════════════════════════════════════════════════════════════

class TestInterpretationDict:
    """질의 해석 섹션 테스트."""

    def test_no_normalized_query(self):
        state = _make_state()
        result = _build_interpretation_dict(state)
        assert result == {}

    def test_with_measures_and_entities(self):
        from src.agents.models.normalization import (
            NormalizedQuery, MeasureSlot, EntitySlot,
        )
        nq = NormalizedQuery(
            measures=[MeasureSlot(term="대출건수")],
            entities=[EntitySlot(term="강남지점")],
        )
        state = _make_state(normalized_query=nq)
        result = _build_interpretation_dict(state)
        assert "대출건수" in result["measures"]
        assert "강남지점" in result["entities"]

    def test_with_filters(self):
        from src.agents.models.normalization import (
            NormalizedQuery, FilterSlot,
        )
        nq = NormalizedQuery(
            filters=[FilterSlot(target="연체상태")],
        )
        state = _make_state(normalized_query=nq)
        result = _build_interpretation_dict(state)
        assert "연체상태" in result["filters"]

    def test_with_dimensions(self):
        from src.agents.models.normalization import (
            NormalizedQuery, DimensionSlot,
        )
        nq = NormalizedQuery(
            dimensions=[DimensionSlot(term="지점별")],
        )
        state = _make_state(normalized_query=nq)
        result = _build_interpretation_dict(state)
        assert "지점별" in result["dimensions"]

    def test_with_time(self):
        from src.agents.models.normalization import (
            NormalizedQuery, TimeSlot, TimePeriod,
        )
        nq = NormalizedQuery(
            time=TimeSlot(
                type="ABSOLUTE",
                base_period=TimePeriod(label="2026년 4월"),
            ),
        )
        state = _make_state(normalized_query=nq)
        result = _build_interpretation_dict(state)
        assert result["period"] == "2026년 4월"


# ══════════════════════════════════════════════════════════════
# 3단계: 활용 정보
# ══════════════════════════════════════════════════════════════

class TestContextDict:
    """활용 정보 섹션 테스트."""

    def test_empty_context(self):
        state = _make_state()
        result = _build_context_dict(state)
        assert isinstance(result, dict)

    def test_with_selected_tables(self):
        from src.models.enums import SelectionStatus

        state = _make_state()

        class FakeTable:
            table_name = "TB_LOAN"
            alt_name = "여신기본"
            description = ""
            selection_status = SelectionStatus.SELECTED
        state.reason.explored_tables = [FakeTable()]

        result = _build_context_dict(state)
        assert result["tables"][0]["name"] == "TB_LOAN"
        assert result["tables"][0]["label"] == "여신기본"

    def test_rejected_tables_excluded(self):
        from src.models.enums import SelectionStatus

        state = _make_state()

        class FakeTable:
            table_name = "TB_OLD"
            alt_name = "구테이블"
            description = ""
            selection_status = SelectionStatus.REJECTED
        state.reason.explored_tables = [FakeTable()]

        result = _build_context_dict(state)
        assert "tables" not in result


# ══════════════════════════════════════════════════════════════
# 4단계: AI 판단
# ══════════════════════════════════════════════════════════════

class TestAIDecisionDict:
    """AI 판단 섹션 테스트."""

    def test_empty_returns_none(self):
        """INFER도 assumptions도 없으면 None (섹션 생략)."""
        state = _make_state(turn_id="test-turn")
        result = _build_ai_decision_dict(state)
        assert result is None

    def test_with_assumptions(self):
        state = _make_state(turn_id="test-turn")
        state.reason.pending_assumptions = ["기간을 당월로 해석"]
        result = _build_ai_decision_dict(state)
        assert "기간을 당월로 해석" in result["pending_assumptions"]
        assert "다른 기준을 원하시면" in result["notice"]

    def test_with_infer_signal(self):
        from src.agents.models.clarification import AmbiguitySignal
        state = _make_state(
            turn_id="turn-123",
            resolved_signals=[
                AmbiguitySignal(
                    source_node="normalizer",
                    ambiguity_type="TIMEFRAME",
                    decision="INFER",
                    confidence="HIGH",
                    question="기간은?",
                    inferred_value="이번 달",
                    reasoning="문맥상 당월",
                    turn_id="turn-123",
                ),
            ],
        )
        result = _build_ai_decision_dict(state)
        assert result["inferences"][0]["question"] == "기간은?"
        assert result["inferences"][0]["value"] == "이번 달"

    def test_other_turn_infer_excluded(self):
        """다른 턴의 INFER는 포함하지 않는다."""
        from src.agents.models.clarification import AmbiguitySignal
        state = _make_state(
            turn_id="turn-current",
            resolved_signals=[
                AmbiguitySignal(
                    source_node="normalizer",
                    ambiguity_type="TIMEFRAME",
                    decision="INFER",
                    confidence="HIGH",
                    question="과거 턴 질문",
                    inferred_value="과거값",
                    reasoning="과거",
                    turn_id="turn-old",
                ),
            ],
        )
        result = _build_ai_decision_dict(state)
        assert result is None


# ══════════════════════════════════════════════════════════════
# 5단계: 검증 결과
# ══════════════════════════════════════════════════════════════

class TestValidationDict:
    """검증 결과 섹션 테스트."""

    def test_with_summary(self):
        state = _make_state()
        state.reason.validation_summary = (
            "SQL 검증 통과. 8개 항목 모두 정상."
        )
        result = _build_validation_dict(state)
        assert "8개 항목 모두 정상" in result["summary"]

    def test_with_checks_fallback(self):
        state = _make_state()
        state.reason.validation_checks = {
            "check1": {"pass": True},
            "check2": {"pass": True},
            "check3": {"pass": False},
        }
        result = _build_validation_dict(state)
        assert "3개 항목 중 2개 통과" in result["summary"]

    def test_with_row_count(self):
        from src.agents.state.state import SQLResult
        state = _make_state(
            sql_result=SQLResult(
                columns=["a"], rows=[{"a": 1}], row_count=342,
            ),
        )
        result = _build_validation_dict(state)
        assert result["row_count"] == 342
        assert "342건 조회 완료" in result["row_label"]

    def test_zero_rows(self):
        from src.agents.state.state import SQLResult
        state = _make_state(
            sql_result=SQLResult(
                columns=[], rows=[], row_count=0,
            ),
        )
        result = _build_validation_dict(state)
        assert result["row_count"] == 0
        assert "조회 결과 0건" in result["row_label"]


# ══════════════════════════════════════════════════════════════
# 통합: build_process_summary
# ══════════════════════════════════════════════════════════════

class TestBuildProcessSummary:
    """5단계 전체 요약 통합 테스트."""

    def test_minimal_state(self):
        """기본 State로도 에러 없이 dict가 생성된다."""
        state = _make_state()
        result = build_process_summary(state)
        assert isinstance(result, dict)
        assert "intent" in result
        assert "interpretation" in result
        assert "context" in result
        assert "validation" in result

    def test_ai_section_omitted_when_empty(self):
        """AI 판단 내용이 없으면 ai_decisions 키가 없다."""
        state = _make_state(turn_id="test-turn")
        result = build_process_summary(state)
        assert "ai_decisions" not in result

    def test_all_five_sections_present(self):
        """모든 데이터가 있으면 5단계 모두 포함."""
        state = _make_state(turn_id="test-turn")
        state.reason.pending_assumptions = ["기간 추론"]
        result = build_process_summary(state)
        assert "intent" in result
        assert "interpretation" in result
        assert "context" in result
        assert "ai_decisions" in result
        assert "validation" in result
