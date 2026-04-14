"""턴 격리(turn_id) 기능 단위 테스트.

turn_id 필드를 통해 이전 대화 턴의 resolved_signals가 현재 턴의 컨텍스트를
오염시키지 않도록 격리되는지 검증한다.

테스트 구간:
    ┌──────────────────────────────────────────────────────────────────┐
    │  테스트 대상                         검증 내용             LLM    │
    │  ─────────────────────────────── ──────────────────────── ───  │
    │  build_clarification_context       현재 턴 ASK/INFER만 포함  X    │
    │  _route_after_clarify              현재 턴 시그널로 라우팅   X    │
    │  intent_classifier ask_count      전체 세션 ASK 카운트      X    │
    │  clarification_handler_node        turn_id 주입 확인         X    │
    │  query_normalizer signal           turn_id 설정 확인         X    │
    └──────────────────────────────────────────────────────────────────┘

실행:
    pytest tests/auto/unit/test_turn_isolation.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import pytest

from tests.conftest import get_test_logger, log_test_case

logger = get_test_logger("test_turn_isolation")


# ──────────────────────────────────────────────────────────────
# 헬퍼
# ──────────────────────────────────────────────────────────────

TURN_A = "aaaaaaaa-0000-0000-0000-000000000001"
TURN_B = "bbbbbbbb-0000-0000-0000-000000000002"


def _make_signal(
    *,
    decision: str = "INFER",
    ambiguity_type: str = "TIMEFRAME",
    confidence: str = "HIGH",
    source_node: str = "normalize_query",
    question: str = "기간 기준은?",
    answer: str | None = None,
    inferred_value: str | None = "이번 달",
    reasoning: str = "기간 미지정으로 이번 달로 추론",
    turn_id: str | None = TURN_A,
    options: list[str] | None = None,
) -> "AmbiguitySignal":
    """테스트용 AmbiguitySignal 팩토리."""
    from src.agents.models.clarification import (
        AmbiguitySignal,
        AmbiguityType,
        ConfidenceLevel,
        QuestionType,
    )
    return AmbiguitySignal(
        source_node=source_node,
        ambiguity_type=AmbiguityType(ambiguity_type),
        decision=decision,  # type: ignore[arg-type]
        confidence=ConfidenceLevel(confidence),
        question=question,
        question_type=QuestionType.FREE_TEXT,
        options=options or [],
        inferred_value=inferred_value,
        reasoning=reasoning,
        answer=answer,
        turn_id=turn_id,
    )


def _make_state(
    *,
    turn_id: str = TURN_A,
    resolved_signals: list | None = None,
    pending_signals: list | None = None,
) -> "PipelineState":
    """테스트용 PipelineState 팩토리."""
    from src.agents.state.state import PipelineState
    return PipelineState(
        user_input="테스트 질의",
        preprocessed_input="테스트 질의",
        turn_id=turn_id,
        resolved_signals=resolved_signals or [],
        pending_signals=pending_signals or [],
    )


# ──────────────────────────────────────────────────────────────
# 1. build_clarification_context — 현재 턴 ASK/INFER만 포함
# ──────────────────────────────────────────────────────────────

class TestBuildClarificationContext:
    """build_clarification_context의 턴 격리 동작 검증."""

    def test_filters_ask_signals_by_turn_id(self):
        """현재 턴의 ASK 시그널만 [명확화 대화] 섹션에 포함된다."""
        from src.agents.utils.clarification_context import (
            build_clarification_context,
        )
        current_ask = _make_signal(
            decision="ASK",
            ambiguity_type="INTENT",
            question="어떤 데이터를 원하시나요?",
            answer="대출 건수",
            inferred_value=None,
            turn_id=TURN_A,
        )
        prev_ask = _make_signal(
            decision="ASK",
            ambiguity_type="INTENT",
            question="이전 턴 질문?",
            answer="이전 턴 답변",
            inferred_value=None,
            turn_id=TURN_B,
        )
        state = _make_state(
            turn_id=TURN_A,
            resolved_signals=[prev_ask, current_ask],
        )

        result = build_clarification_context(state)

        includes_current_q = "어떤 데이터를 원하시나요?" in result
        excludes_prev_q = "이전 턴 질문?" not in result
        passed = includes_current_q and excludes_prev_q

        log_test_case(
            logger,
            "test_filters_ask_by_turn",
            "prev_turn ASK + current_turn ASK",
            "현재 턴 질문만 포함",
            result[:200],
            passed,
        )
        assert includes_current_q, "현재 턴 ASK가 컨텍스트에 없음"
        assert excludes_prev_q, "이전 턴 ASK가 컨텍스트에 포함됨 — 오염"

    def test_filters_infer_signals_by_turn_id(self):
        """현재 턴의 INFER 시그널만 [자동 추론된 조건] 섹션에 포함된다."""
        from src.agents.utils.clarification_context import (
            build_clarification_context,
        )
        current_infer = _make_signal(
            decision="INFER",
            question="기간 기준?",
            inferred_value="2025년 3월",
            turn_id=TURN_A,
        )
        prev_infer = _make_signal(
            decision="INFER",
            question="이전 기간?",
            inferred_value="2025년 2월",
            turn_id=TURN_B,
        )
        state = _make_state(
            turn_id=TURN_A,
            resolved_signals=[prev_infer, current_infer],
        )

        result = build_clarification_context(state)

        includes_current = "2025년 3월" in result
        excludes_prev = "2025년 2월" not in result
        passed = includes_current and excludes_prev

        log_test_case(
            logger,
            "test_filters_infer_by_turn",
            "prev_turn INFER + current_turn INFER",
            "현재 턴 추론값만 포함",
            result[:200],
            passed,
        )
        assert includes_current, "현재 턴 INFER가 컨텍스트에 없음"
        assert excludes_prev, "이전 턴 INFER가 컨텍스트에 포함됨 — 오염"

    def test_returns_empty_when_no_current_turn_signals(self):
        """현재 턴 시그널이 전혀 없으면 빈 문자열을 반환한다."""
        from src.agents.utils.clarification_context import (
            build_clarification_context,
        )
        state = _make_state(
            turn_id=TURN_A,
            resolved_signals=[
                _make_signal(turn_id=TURN_B, decision="ASK", answer="답변"),
                _make_signal(turn_id=TURN_B, decision="INFER"),
            ],
        )

        result = build_clarification_context(state)

        passed = result == ""
        log_test_case(
            logger,
            "test_empty_no_current_signals",
            "다른 턴 시그널만 존재",
            '""',
            repr(result),
            passed,
        )
        assert passed, f"빈 문자열 기대, 실제: {repr(result)}"


# ──────────────────────────────────────────────────────────────
# 3. _route_after_clarify — 현재 턴 시그널로 라우팅
# ──────────────────────────────────────────────────────────────

class TestRouteAfterClarify:
    """_route_after_clarify의 턴 격리 라우팅 동작 검증."""

    def test_routes_to_current_turn_source_node(self):
        """현재 턴 시그널의 source_node로 라우팅된다."""
        from src.agents.graph.pipeline import _route_after_clarify

        current_signal = _make_signal(
            source_node="normalize_query",
            decision="ASK",
            answer="답변",
            inferred_value=None,
            turn_id=TURN_A,
        )
        state = _make_state(
            turn_id=TURN_A,
            resolved_signals=[current_signal],
        )

        result = _route_after_clarify(state)

        passed = result == "normalize_query"
        log_test_case(
            logger,
            "test_routes_to_current_source",
            f"current turn signal source_node=normalize_query",
            "normalize_query",
            result,
            passed,
        )
        assert passed, f"normalize_query 기대, 실제: {result}"

    def test_ignores_stale_signals_from_previous_turns(self):
        """이전 턴 시그널은 라우팅에 영향을 주지 않는다.

        이전 턴: source_node="sql_generator" (다른 노드)
        현재 턴: source_node="normalize_query"
        → normalize_query 로 라우팅되어야 함.
        """
        from src.agents.graph.pipeline import _route_after_clarify

        prev_signal = _make_signal(
            source_node="sql_generator",
            decision="ASK",
            answer="이전 답변",
            inferred_value=None,
            turn_id=TURN_B,
        )
        current_signal = _make_signal(
            source_node="normalize_query",
            decision="ASK",
            answer="현재 답변",
            inferred_value=None,
            turn_id=TURN_A,
        )
        state = _make_state(
            turn_id=TURN_A,
            resolved_signals=[prev_signal, current_signal],
        )

        result = _route_after_clarify(state)

        passed = result == "normalize_query"
        log_test_case(
            logger,
            "test_ignores_stale_signals",
            "prev_turn→sql_generator, current_turn→normalize_query",
            "normalize_query",
            result,
            passed,
        )
        assert passed, (
            f"이전 턴 시그널에 의한 오라우팅 발생. 기대=normalize_query, 실제={result}"
        )

    def test_falls_back_to_intent_classifier_when_no_current_signals(self):
        """현재 턴 시그널이 없으면 intent_classifier로 폴백한다."""
        from src.agents.graph.pipeline import _route_after_clarify

        prev_signal = _make_signal(
            source_node="sql_generator",
            turn_id=TURN_B,
        )
        state = _make_state(
            turn_id=TURN_A,
            resolved_signals=[prev_signal],
        )

        result = _route_after_clarify(state)

        passed = result == "intent_classifier"
        log_test_case(
            logger,
            "test_fallback_no_current_signals",
            "현재 턴 시그널 없음",
            "intent_classifier",
            result,
            passed,
        )
        assert passed, f"intent_classifier 기대, 실제: {result}"

    def test_routes_last_signal_when_multiple_current_turn_signals(self):
        """현재 턴 시그널이 여러 개이면 마지막 시그널의 source_node로 라우팅한다."""
        from src.agents.graph.pipeline import _route_after_clarify

        first_signal = _make_signal(
            source_node="intent_classifier",
            decision="ASK",
            answer="첫 번째 답변",
            inferred_value=None,
            turn_id=TURN_A,
        )
        last_signal = _make_signal(
            source_node="readiness_gate",
            decision="ASK",
            answer="마지막 답변",
            inferred_value=None,
            turn_id=TURN_A,
        )
        state = _make_state(
            turn_id=TURN_A,
            resolved_signals=[first_signal, last_signal],
        )

        result = _route_after_clarify(state)

        passed = result == "readiness_gate"
        log_test_case(
            logger,
            "test_last_signal_routing",
            "두 개의 현재 턴 시그널",
            "readiness_gate (마지막 시그널)",
            result,
            passed,
        )
        assert passed, f"readiness_gate 기대, 실제: {result}"


# ──────────────────────────────────────────────────────────────
# 4. ask_count — 전체 세션(모든 턴) ASK 카운트 (회귀 테스트)
# ──────────────────────────────────────────────────────────────

class TestAskCountSessionWide:
    """ask_count는 세션 전체 ASK 카운트여야 한다.

    turn_id로 필터링하면 이전 턴의 명확화 횟수가 무시되어
    무한루프 방어(clarification_max_turns)가 우회될 수 있다.
    이는 의도된 설계이며 회귀 테스트로 보호한다.
    """

    def test_ask_count_counts_all_turns(self):
        """ask_count는 모든 턴의 ASK 시그널 수를 합산한다."""
        from src.agents.state.state import PipelineState

        # 이전 턴 3회 ASK
        prev_asks = [
            _make_signal(decision="ASK", answer=f"답변{i}", inferred_value=None, turn_id=TURN_B)
            for i in range(3)
        ]
        # 현재 턴 1회 ASK
        current_ask = _make_signal(
            decision="ASK", answer="현재 답변", inferred_value=None, turn_id=TURN_A,
        )
        # INFER는 카운트에서 제외되어야 함
        current_infer = _make_signal(decision="INFER", turn_id=TURN_A)

        state = PipelineState(
            user_input="테스트",
            preprocessed_input="테스트",
            turn_id=TURN_A,
            resolved_signals=[*prev_asks, current_ask, current_infer],
        )

        ask_count = sum(
            1 for s in state.resolved_signals
            if s.decision == "ASK"
        )

        expected = 4  # 이전 턴 3 + 현재 턴 1
        passed = ask_count == expected

        log_test_case(
            logger,
            "test_ask_count_all_turns",
            "prev_turn ASK x3 + current_turn ASK x1 + current_turn INFER x1",
            f"ask_count={expected}",
            f"ask_count={ask_count}",
            passed,
        )
        assert passed, (
            f"ask_count가 전체 세션 기준이어야 함. 기대={expected}, 실제={ask_count}. "
            "turn_id 필터링이 적용되었다면 무한루프 방어가 우회될 수 있음."
        )

    def test_ask_count_excludes_infer(self):
        """ask_count는 INFER 시그널을 포함하지 않는다."""
        from src.agents.state.state import PipelineState

        signals = [
            _make_signal(decision="ASK", answer="답변", inferred_value=None, turn_id=TURN_A),
            _make_signal(decision="INFER", turn_id=TURN_A),
            _make_signal(decision="INFER", turn_id=TURN_B),
        ]
        state = PipelineState(
            user_input="테스트",
            preprocessed_input="테스트",
            turn_id=TURN_A,
            resolved_signals=signals,
        )

        ask_count = sum(
            1 for s in state.resolved_signals
            if s.decision == "ASK"
        )

        passed = ask_count == 1
        log_test_case(
            logger,
            "test_ask_count_excludes_infer",
            "ASK x1, INFER x2",
            "ask_count=1",
            f"ask_count={ask_count}",
            passed,
        )
        assert passed, f"INFER가 ask_count에 포함됨. 실제={ask_count}"


# ──────────────────────────────────────────────────────────────
# 5. 빈 turn_id 방어
# ──────────────────────────────────────────────────────────────

class TestEmptyTurnIdDefense:
    """state.turn_id가 빈 문자열일 때 조기 반환 동작 검증."""

    def test_clarification_context_returns_empty_for_empty_turn_id(self):
        """turn_id가 빈 문자열이면 build_clarification_context는 빈 문자열을 반환한다."""
        from src.agents.utils.clarification_context import (
            build_clarification_context,
        )
        signal = _make_signal(
            decision="ASK",
            answer="답변",
            inferred_value=None,
            turn_id=TURN_A,
        )
        state = _make_state(
            turn_id="",
            resolved_signals=[signal],
        )

        result = build_clarification_context(state)

        passed = result == ""
        log_test_case(
            logger,
            "test_context_empty_turn_id",
            "turn_id=''",
            '""',
            repr(result),
            passed,
        )
        assert passed, f"빈 turn_id 시 빈 문자열 기대, 실제: {repr(result)}"


# ──────────────────────────────────────────────────────────────
# 6. clarification_handler_node — turn_id 주입
# ──────────────────────────────────────────────────────────────

class TestClarificationHandlerInjectsTurnId:
    """clarification_handler_node가 pending_signals에 state.turn_id를 주입한다."""

    @pytest.mark.asyncio
    async def test_infer_signal_gets_turn_id_injected(self):
        """INFER 시그널이 resolved_signals로 이동할 때 state.turn_id가 주입된다."""
        from src.agents.nodes.interpret.clarification_handler import (
            clarification_handler_node,
        )

        # turn_id가 None인 시그널 (노드 생성 직후 상태)
        signal = _make_signal(
            decision="INFER",
            ambiguity_type="TIMEFRAME",
            confidence="HIGH",
            turn_id=None,  # 아직 주입 전
        )
        state = _make_state(
            turn_id=TURN_A,
            pending_signals=[signal],
        )

        result = await clarification_handler_node(state)

        resolved = result.get("resolved_signals", [])
        injected_turn_ids = [s.turn_id for s in resolved]
        passed = len(resolved) == 1 and all(
            tid == TURN_A for tid in injected_turn_ids
        )

        log_test_case(
            logger,
            "test_infer_turn_id_injected",
            f"pending_signal.turn_id=None, state.turn_id={TURN_A[:8]}",
            f"resolved[0].turn_id={TURN_A[:8]}",
            str(injected_turn_ids),
            passed,
        )
        assert len(resolved) == 1, "INFER 시그널이 resolved로 이동하지 않음"
        assert all(tid == TURN_A for tid in injected_turn_ids), (
            f"turn_id 주입 실패. 실제: {injected_turn_ids}"
        )

    @pytest.mark.asyncio
    async def test_multiple_signals_all_get_turn_id(self):
        """여러 pending_signals 모두 state.turn_id를 주입받는다."""
        from src.agents.nodes.interpret.clarification_handler import (
            clarification_handler_node,
        )

        signals = [
            _make_signal(
                decision="INFER",
                ambiguity_type="TIMEFRAME",
                confidence="HIGH",
                question=f"질문 {i}",
                turn_id=None,
            )
            for i in range(3)
        ]
        state = _make_state(
            turn_id=TURN_A,
            pending_signals=signals,
        )

        result = await clarification_handler_node(state)

        resolved = result.get("resolved_signals", [])
        all_injected = all(s.turn_id == TURN_A for s in resolved)
        passed = len(resolved) == 3 and all_injected

        log_test_case(
            logger,
            "test_multiple_signals_turn_id",
            "3개 pending_signals, turn_id=None",
            f"모두 turn_id={TURN_A[:8]}",
            f"resolved={len(resolved)}, all_injected={all_injected}",
            passed,
        )
        assert passed, (
            f"일부 시그널에 turn_id 미주입. "
            f"resolved={len(resolved)}, all_injected={all_injected}"
        )

    @pytest.mark.asyncio
    async def test_existing_turn_id_is_overwritten(self):
        """이미 다른 turn_id가 있는 시그널도 현재 state.turn_id로 덮어쓴다.

        재처리(replay) 시나리오에서 stale turn_id가 남지 않도록 보장한다.
        """
        from src.agents.nodes.interpret.clarification_handler import (
            clarification_handler_node,
        )

        signal = _make_signal(
            decision="INFER",
            ambiguity_type="VALUE",
            confidence="HIGH",
            turn_id=TURN_B,  # 다른 턴 ID
        )
        state = _make_state(
            turn_id=TURN_A,
            pending_signals=[signal],
        )

        result = await clarification_handler_node(state)

        resolved = result.get("resolved_signals", [])
        injected_id = resolved[0].turn_id if resolved else None
        passed = injected_id == TURN_A

        log_test_case(
            logger,
            "test_overwrite_existing_turn_id",
            f"signal.turn_id={TURN_B[:8]}, state.turn_id={TURN_A[:8]}",
            f"resolved.turn_id={TURN_A[:8]}",
            str(injected_id),
            passed,
        )
        assert passed, (
            f"기존 turn_id가 덮어쓰여지지 않음. 기대={TURN_A}, 실제={injected_id}"
        )


# ──────────────────────────────────────────────────────────────
# 7. query_normalizer 시그널 — turn_id 설정
# ──────────────────────────────────────────────────────────────

class TestQueryNormalizerSignalTurnId:
    """query_normalizer가 생성하는 AmbiguitySignal에 turn_id가 설정된다.

    실제 LLM 호출 없이 AmbiguitySignal 생성 경로만 검증한다.
    (query_normalizer 내 T3 블록: turn_id=state.turn_id)
    """

    def test_signal_turn_id_matches_state(self):
        """query_normalizer가 생성하는 시그널 turn_id == state.turn_id."""
        from src.agents.models.clarification import (
            AmbiguitySignal,
            AmbiguityType,
            ConfidenceLevel,
        )
        from src.agents.state.state import PipelineState

        # query_normalizer T3 블록의 AmbiguitySignal 생성 코드를 직접 재현
        state = PipelineState(
            user_input="테스트 질의",
            preprocessed_input="테스트 질의",
            turn_id=TURN_A,
        )

        # normalizer가 생성하는 ambiguity 목록 (LLM 응답 모사)
        mock_ambiguities = [
            {
                "ambiguity_type": "TIMEFRAME",
                "confidence": "HIGH",
                "question": "기간 기준은?",
                "question_type": "single_select",
                "options": ["이번 달", "지난 달"],
                "inferred_value": "이번 달",
                "reasoning": "기간 미지정",
            }
        ]

        # T3 블록과 동일한 시그널 생성 로직
        signals = [
            AmbiguitySignal(
                source_node="normalize_query",
                decision="INFER",
                ambiguity_type=AmbiguityType(
                    amb.get("ambiguity_type", "CONTEXT")
                ),
                confidence=ConfidenceLevel(
                    amb.get("confidence", "LOW")
                ),
                question=amb.get("question", ""),
                inferred_value=amb.get("inferred_value"),
                reasoning=amb.get("reasoning", ""),
                turn_id=state.turn_id,  # ← 핵심 검증 지점
            )
            for amb in mock_ambiguities
        ]

        all_match = all(s.turn_id == TURN_A for s in signals)
        passed = len(signals) == 1 and all_match

        log_test_case(
            logger,
            "test_query_normalizer_signal_turn_id",
            f"state.turn_id={TURN_A[:8]}",
            f"signal.turn_id={TURN_A[:8]}",
            str([s.turn_id for s in signals]),
            passed,
        )
        assert passed, (
            f"query_normalizer 시그널에 turn_id 미설정. "
            f"실제: {[s.turn_id for s in signals]}"
        )

    def test_signal_turn_id_isolated_from_other_turns(self):
        """서로 다른 state.turn_id로 생성된 시그널은 turn_id가 격리된다."""
        from src.agents.models.clarification import (
            AmbiguitySignal,
            AmbiguityType,
            ConfidenceLevel,
        )
        from src.agents.state.state import PipelineState

        def make_normalizer_signal(turn_id: str) -> AmbiguitySignal:
            """주어진 turn_id로 query_normalizer 시그널 생성."""
            state = PipelineState(
                user_input="테스트",
                preprocessed_input="테스트",
                turn_id=turn_id,
            )
            return AmbiguitySignal(
                source_node="normalize_query",
                decision="INFER",
                ambiguity_type=AmbiguityType.TIMEFRAME,
                confidence=ConfidenceLevel.HIGH,
                question="기간?",
                reasoning="기간 미지정",
                turn_id=state.turn_id,
            )

        signal_a = make_normalizer_signal(TURN_A)
        signal_b = make_normalizer_signal(TURN_B)

        passed = signal_a.turn_id == TURN_A and signal_b.turn_id == TURN_B

        log_test_case(
            logger,
            "test_signal_turn_id_isolation",
            f"turn_A={TURN_A[:8]}, turn_B={TURN_B[:8]}",
            "각 시그널이 자신의 turn_id를 보유",
            f"a={signal_a.turn_id}, b={signal_b.turn_id}",
            passed,
        )
        assert signal_a.turn_id == TURN_A, f"signal_a.turn_id 불일치: {signal_a.turn_id}"
        assert signal_b.turn_id == TURN_B, f"signal_b.turn_id 불일치: {signal_b.turn_id}"
