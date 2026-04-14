"""clarification_context.py 단위 테스트.

테스트 대상:
    - build_clarification_context() — resolved_signals를 프롬프트 섹션으로 변환

핵심 검증 포인트:
    - turn_id 필터링: 현재 턴 시그널만 포함, 이전 턴 시그널 제외
    - ASK / INFER 분리 출력
    - 빈 입력 처리 (turn_id 없음, resolved_signals 없음)

외부 의존성 없음 (LLM, DB 호출 없는 순수 함수 테스트).
"""

from __future__ import annotations

from src.agents.models.clarification import AmbiguitySignal, AmbiguityType, QuestionType
from src.agents.state.state import PipelineState
from src.agents.utils.clarification_context import build_clarification_context
from src.models.enums import ConfidenceLevel


# ── 픽스처 헬퍼 ─────────────────────────────────────────────


def _ask_signal(
    question: str,
    answer: str,
    turn_id: str = "turn-001",
    options: list[str] | None = None,
) -> AmbiguitySignal:
    """ASK 결정의 AmbiguitySignal 빌더."""
    return AmbiguitySignal(
        source_node="test_node",
        ambiguity_type=AmbiguityType.TIMEFRAME,
        decision="ASK",
        confidence=ConfidenceLevel.HIGH,
        question=question,
        options=options or [],
        answer=answer,
        turn_id=turn_id,
    )


def _infer_signal(
    question: str,
    inferred_value: str,
    reasoning: str = "자동 추론 근거",
    turn_id: str = "turn-001",
) -> AmbiguitySignal:
    """INFER 결정의 AmbiguitySignal 빌더."""
    return AmbiguitySignal(
        source_node="test_node",
        ambiguity_type=AmbiguityType.TIMEFRAME,
        decision="INFER",
        confidence=ConfidenceLevel.MEDIUM,
        question=question,
        inferred_value=inferred_value,
        reasoning=reasoning,
        turn_id=turn_id,
    )


def _state_with_signals(
    signals: list[AmbiguitySignal],
    turn_id: str = "turn-001",
) -> PipelineState:
    """테스트용 PipelineState를 생성한다."""
    return PipelineState(
        user_input="테스트 질의",
        session_id="session-1",
        turn_id=turn_id,
        resolved_signals=signals,
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. build_clarification_context()
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestBuildClarificationContext:
    """build_clarification_context() 동작 검증."""

    def test_empty_turn_id_returns_empty_string(self):
        """turn_id가 빈 문자열이면 빈 문자열 반환."""
        state = PipelineState(
            user_input="test",
            session_id="s1",
            turn_id="",
            resolved_signals=[
                _ask_signal("기간?", "이번달", turn_id=""),
            ],
        )
        result = build_clarification_context(state)
        assert result == ""

    def test_no_signals_returns_empty_string(self):
        """resolved_signals가 없으면 빈 문자열 반환."""
        state = _state_with_signals([], turn_id="turn-001")
        result = build_clarification_context(state)
        assert result == ""

    def test_ask_signal_included_in_output(self):
        """ASK 시그널의 질문과 답변이 출력에 포함된다."""
        state = _state_with_signals(
            [_ask_signal("조회 기간은?", "이번 달")],
        )
        result = build_clarification_context(state)
        assert "명확화 대화" in result
        assert "조회 기간은?" in result
        assert "이번 달" in result

    def test_ask_signal_with_options_included(self):
        """ASK 시그널의 선택지가 출력에 포함된다."""
        state = _state_with_signals(
            [
                _ask_signal(
                    "지점 단위?",
                    "전체",
                    options=["전체", "개별 지점"],
                ),
            ],
        )
        result = build_clarification_context(state)
        assert "전체" in result
        assert "개별 지점" in result

    def test_infer_signal_included_in_output(self):
        """INFER 시그널의 추론값과 근거가 출력에 포함된다."""
        state = _state_with_signals(
            [
                _infer_signal(
                    "기준월은?",
                    inferred_value="2026년 3월",
                    reasoning="최근 발화 기준 추론",
                ),
            ],
        )
        result = build_clarification_context(state)
        assert "자동 추론된 조건" in result
        assert "기준월은?" in result
        assert "2026년 3월" in result
        assert "최근 발화 기준 추론" in result

    def test_ask_and_infer_both_present(self):
        """ASK와 INFER가 모두 있을 때 각 섹션이 존재한다."""
        state = _state_with_signals(
            [
                _ask_signal("기간?", "이번 달"),
                _infer_signal("지점?", "전체 지점"),
            ],
        )
        result = build_clarification_context(state)
        assert "명확화 대화" in result
        assert "자동 추론된 조건" in result

    def test_current_turn_only_filtered(self):
        """현재 turn_id 시그널만 포함, 이전 턴 시그널 제외."""
        current_signal = _ask_signal("기간?", "이번 달", turn_id="turn-002")
        prev_signal = _ask_signal("지점?", "서울", turn_id="turn-001")
        state = PipelineState(
            user_input="test",
            session_id="s1",
            turn_id="turn-002",
            resolved_signals=[prev_signal, current_signal],
        )
        result = build_clarification_context(state)
        assert "기간?" in result
        assert "이번 달" in result
        assert "지점?" not in result
        assert "서울" not in result

    def test_signal_with_none_turn_id_excluded(self):
        """turn_id가 None인 시그널은 제외된다."""
        signal_no_turn = AmbiguitySignal(
            source_node="n",
            ambiguity_type=AmbiguityType.TIMEFRAME,
            decision="ASK",
            confidence=ConfidenceLevel.HIGH,
            question="제외될 질문?",
            answer="제외될 답변",
            turn_id=None,
        )
        current_signal = _ask_signal("포함될 질문?", "포함될 답변", turn_id="turn-001")
        state = _state_with_signals([signal_no_turn, current_signal])
        result = build_clarification_context(state)
        assert "포함될 질문?" in result
        assert "제외될 질문?" not in result

    def test_multiple_ask_rounds_numbered(self):
        """ASK 시그널 여러 개 — 라운드 번호가 순서대로 포함된다."""
        signals = [
            _ask_signal("첫 번째 질문?", "첫 번째 답변"),
            _ask_signal("두 번째 질문?", "두 번째 답변"),
        ]
        state = _state_with_signals(signals)
        result = build_clarification_context(state)
        assert "라운드 1" in result
        assert "라운드 2" in result
        assert "첫 번째 질문?" in result
        assert "두 번째 질문?" in result

    def test_infer_only_no_ask_section(self):
        """INFER만 있을 때 '명확화 대화' 섹션이 없다."""
        state = _state_with_signals(
            [_infer_signal("기준월?", "2026년 3월")],
        )
        result = build_clarification_context(state)
        assert "명확화 대화" not in result
        assert "자동 추론된 조건" in result

    def test_ask_only_no_infer_section(self):
        """ASK만 있을 때 '자동 추론된 조건' 섹션이 없다."""
        state = _state_with_signals(
            [_ask_signal("기간?", "이번 달")],
        )
        result = build_clarification_context(state)
        assert "명확화 대화" in result
        assert "자동 추론된 조건" not in result
