"""파이프라인 라우팅 함수 단위 테스트.

=== 개념 설명 ===
pipeline.py의 조건부 엣지 라우팅 함수(_route_after_*)와
_handle_error 노드, runner.py의 _build_result/_build_safe_insight를 검증한다.

모든 라우팅 함수는 PipelineState를 받아 노드 이름 문자열을 반환하는
순수 함수이므로 LLM 없이 단위 테스트 가능하다.
각 함수의 분기 조건을 최소 상태 객체로 검증한다.

=== 대상 함수 ===
  - _route_after_intent_classifier  : pending_signals / ERROR / 비데이터 / 데이터
  - _route_after_normalize          : pending_signals / 정상
  - _route_after_readiness_gate     : CANCELLED / pending_signals / Phase별
  - _route_after_sql_generator      : GENERATION_FAILED / pending_signals / 정상
  - _route_after_sql_validator      : failure_type 6가지 분기
  - _route_after_recovery_agent     : CANCELLED / pending_signals / Phase별
  - _route_after_result_finalizer   : CANCELLED / pending_signals / error_message / validated_sql
  - _route_after_execution          : ERROR/CANCELLED / DATA_ANALYSIS / 정상
  - _route_after_clarify            : source_node → 복귀 / legacy 이름 / 유효하지 않은 이름
  - _handle_error                   : CANCELLED 보존 / 재시도 소진 / 일반 에러

=== 단독 실행 ===
    python -m pytest tests/auto/unit/test_pipeline_routing.py -v -s
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tests.conftest import get_test_logger, log_test_case

logger = get_test_logger("test_pipeline_routing")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 헬퍼 팩토리
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _make_state(**kwargs):
    """최소 필드로 PipelineState를 생성한다."""
    from src.agents.state.state import PipelineState
    return PipelineState(**kwargs)


def _make_signal(
    source_node: str = "intent_classifier",
    decision: str = "ASK",
    turn_id: str | None = None,
    answer: str | None = None,
):
    """AmbiguitySignal을 생성한다."""
    from src.agents.models.clarification import AmbiguitySignal
    return AmbiguitySignal(
        source_node=source_node,
        decision=decision,
        ambiguity_type="INTENT",
        confidence="HIGH",
        question="테스트 질문",
        answer=answer,
        turn_id=turn_id,
    )


def _make_reason(**kwargs):
    """ReasoningState를 생성한다."""
    from src.agents.state.state import ReasoningState
    return ReasoningState(**kwargs)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _route_after_intent_classifier
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestRouteAfterIntentClassifier:
    """intent_classifier 후 라우팅 — 4가지 분기."""

    def test_pending_signals_routes_to_clarification(self):
        """pending_signals가 있으면 clarification_handler로 라우팅된다."""
        from src.agents.graph.pipeline import _route_after_intent_classifier

        state = _make_state(pending_signals=[_make_signal()])
        result = _route_after_intent_classifier(state)

        passed = result == "clarification_handler"
        log_test_case(logger, "test_pending_signals", "pending_signals=[signal]",
                      "clarification_handler", result, passed)
        assert result == "clarification_handler"

    def test_error_status_routes_to_error_end(self):
        """status=ERROR이면 error_end로 라우팅된다."""
        from src.agents.graph.pipeline import _route_after_intent_classifier
        from src.agents.state.state import QueryStatus

        state = _make_state(status=QueryStatus.ERROR)
        result = _route_after_intent_classifier(state)

        passed = result == "error_end"
        log_test_case(logger, "test_error_status", "status=ERROR",
                      "error_end", result, passed)
        assert result == "error_end"

    def test_casual_talk_routes_to_simple_responder(self):
        """CASUAL_TALK 의도는 simple_responder로 라우팅된다."""
        from src.agents.graph.pipeline import _route_after_intent_classifier
        from src.agents.state.state import IntentType

        state = _make_state(intent=IntentType.CASUAL_TALK)
        result = _route_after_intent_classifier(state)

        passed = result == "simple_responder"
        log_test_case(logger, "test_casual_talk", "CASUAL_TALK",
                      "simple_responder", result, passed)
        assert result == "simple_responder"

    def test_meta_question_routes_to_simple_responder(self):
        """META_QUESTION 의도는 simple_responder로 라우팅된다."""
        from src.agents.graph.pipeline import _route_after_intent_classifier
        from src.agents.state.state import IntentType

        state = _make_state(intent=IntentType.META_QUESTION)
        result = _route_after_intent_classifier(state)

        passed = result == "simple_responder"
        log_test_case(logger, "test_meta_question", "META_QUESTION",
                      "simple_responder", result, passed)
        assert result == "simple_responder"

    def test_data_extraction_routes_to_data_path(self):
        """DATA_EXTRACTION 의도는 normalize_query 또는 reasoning_preparer로 라우팅된다."""
        from src.agents.graph.pipeline import _route_after_intent_classifier
        from src.agents.state.state import IntentType

        state = _make_state(intent=IntentType.DATA_EXTRACTION)
        result = _route_after_intent_classifier(state)

        passed = result in ("query_normalizer", "reasoning_preparer")
        log_test_case(logger, "test_data_extraction", "DATA_EXTRACTION",
                      "query_normalizer or reasoning_preparer", result, passed)
        assert result in ("query_normalizer", "reasoning_preparer")

    def test_pending_signals_takes_priority_over_error(self):
        """pending_signals는 ERROR 상태보다 우선한다."""
        from src.agents.graph.pipeline import _route_after_intent_classifier
        from src.agents.state.state import QueryStatus

        state = _make_state(
            pending_signals=[_make_signal()],
            status=QueryStatus.ERROR,
        )
        result = _route_after_intent_classifier(state)

        passed = result == "clarification_handler"
        log_test_case(
            logger, "test_signals_over_error",
            "pending_signals + ERROR",
            "clarification_handler", result, passed,
        )
        assert result == "clarification_handler"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _route_after_normalize
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestRouteAfterNormalize:
    """normalize_query 후 라우팅 — 2가지 분기."""

    def test_pending_signals_routes_to_clarification(self):
        """pending_signals가 있으면 clarification_handler로 라우팅된다."""
        from src.agents.graph.pipeline import _route_after_normalize

        state = _make_state(pending_signals=[_make_signal()])
        result = _route_after_normalize(state)

        passed = result == "clarification_handler"
        log_test_case(logger, "test_normalize_pending_signals",
                      "pending_signals", "clarification_handler", result, passed)
        assert result == "clarification_handler"

    def test_no_signals_routes_to_reasoning_preparer(self):
        """pending_signals가 없으면 reasoning_preparer로 라우팅된다."""
        from src.agents.graph.pipeline import _route_after_normalize

        state = _make_state()
        result = _route_after_normalize(state)

        passed = result == "reasoning_preparer"
        log_test_case(logger, "test_normalize_normal",
                      "no signals", "reasoning_preparer", result, passed)
        assert result == "reasoning_preparer"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _route_after_readiness_gate
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestRouteAfterReadinessGate:
    """readiness_gate 후 라우팅 — CANCELLED / pending / Phase별."""

    def test_cancelled_routes_to_conclude_failure(self):
        """CANCELLED 상태는 conclude_failure로 라우팅된다."""
        from src.agents.graph.pipeline import _route_after_readiness_gate
        from src.agents.state.state import QueryStatus

        state = _make_state(status=QueryStatus.CANCELLED)
        result = _route_after_readiness_gate(state)

        passed = result == "conclude_failure"
        log_test_case(logger, "test_gate_cancelled",
                      "CANCELLED", "conclude_failure", result, passed)
        assert result == "conclude_failure"

    def test_pending_signals_routes_to_clarification(self):
        """pending_signals가 있으면 clarification_handler로 라우팅된다."""
        from src.agents.graph.pipeline import _route_after_readiness_gate

        state = _make_state(pending_signals=[_make_signal()])
        result = _route_after_readiness_gate(state)

        passed = result == "clarification_handler"
        log_test_case(logger, "test_gate_pending_signals",
                      "pending_signals", "clarification_handler", result, passed)
        assert result == "clarification_handler"

    @pytest.mark.parametrize("phase,expected_route", [
        ("EXPLORING", "explore"),
        ("GENERATING", "generate_sql"),
        ("REPLANNING", "recovery"),
        ("DONE", "conclude_failure"),
    ])
    def test_phase_routing(self, phase: str, expected_route: str):
        """각 Phase가 올바른 라우팅 키로 변환된다."""
        from src.agents.graph.pipeline import _route_after_readiness_gate
        from src.agents.state.state import Phase

        reason = _make_reason(phase=Phase(phase))
        state = _make_state(reason=reason)
        result = _route_after_readiness_gate(state)

        passed = result == expected_route
        log_test_case(logger, f"test_gate_phase_{phase}",
                      phase, expected_route, result, passed)
        assert result == expected_route

    def test_unknown_phase_routes_to_conclude_failure(self):
        """매핑에 없는 Phase는 conclude_failure로 폴백된다."""
        from src.agents.graph.pipeline import _route_after_readiness_gate
        from src.agents.state.state import Phase

        # PLANNING은 _PHASE_TO_ROUTE에 없는 Phase
        reason = _make_reason(phase=Phase.PLANNING)
        state = _make_state(reason=reason)
        result = _route_after_readiness_gate(state)

        passed = result == "conclude_failure"
        log_test_case(logger, "test_gate_unknown_phase",
                      "PLANNING", "conclude_failure", result, passed)
        assert result == "conclude_failure"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _route_after_sql_generator
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestRouteAfterSqlGenerator:
    """sql_generator 후 라우팅 — 3가지 분기."""

    def test_generation_failed_routes_to_replan(self):
        """GENERATION_FAILED는 replan(recovery_agent)으로 라우팅된다."""
        from src.agents.graph.pipeline import _route_after_sql_generator
        from src.agents.state.state import FailureType

        reason = _make_reason(failure_type=FailureType.GENERATION_FAILED)
        state = _make_state(reason=reason)
        result = _route_after_sql_generator(state)

        passed = result == "replan"
        log_test_case(logger, "test_gen_failed",
                      "GENERATION_FAILED", "replan", result, passed)
        assert result == "replan"

    def test_pending_signals_routes_to_clarification(self):
        """pending_signals가 있으면 clarification_handler로 라우팅된다."""
        from src.agents.graph.pipeline import _route_after_sql_generator

        state = _make_state(pending_signals=[_make_signal()])
        result = _route_after_sql_generator(state)

        passed = result == "clarification_handler"
        log_test_case(logger, "test_gen_pending_signals",
                      "pending_signals", "clarification_handler", result, passed)
        assert result == "clarification_handler"

    def test_normal_routes_to_sql_validator(self):
        """실패 없고 pending_signals 없으면 sql_validator로 라우팅된다."""
        from src.agents.graph.pipeline import _route_after_sql_generator

        state = _make_state()
        result = _route_after_sql_generator(state)

        passed = result == "sql_validator"
        log_test_case(logger, "test_gen_normal",
                      "no failure/signals", "sql_validator", result, passed)
        assert result == "sql_validator"

    def test_generation_failed_takes_priority_over_signals(self):
        """GENERATION_FAILED는 pending_signals보다 우선한다."""
        from src.agents.graph.pipeline import _route_after_sql_generator
        from src.agents.state.state import FailureType

        reason = _make_reason(failure_type=FailureType.GENERATION_FAILED)
        state = _make_state(
            reason=reason,
            pending_signals=[_make_signal()],
        )
        result = _route_after_sql_generator(state)

        passed = result == "replan"
        log_test_case(logger, "test_gen_failed_priority",
                      "GENERATION_FAILED + signals", "replan", result, passed)
        assert result == "replan"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _route_after_sql_validator
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestRouteAfterSqlValidator:
    """sql_validator 후 라우팅 — failure_type 6가지 분기."""

    def test_none_failure_routes_to_conclude_success(self):
        """failure_type=None이면 conclude_success(result_finalizer)로 라우팅된다."""
        from src.agents.graph.pipeline import _route_after_sql_validator

        reason = _make_reason(failure_type=None)
        state = _make_state(reason=reason)
        result = _route_after_sql_validator(state)

        passed = result == "conclude_success"
        log_test_case(logger, "test_val_success",
                      "failure_type=None", "conclude_success", result, passed)
        assert result == "conclude_success"

    def test_sql_syntax_with_retries_remaining_routes_to_fix_syntax(self):
        """SQL_SYNTAX이고 local_fix 한도 미달이면 fix_syntax로 라우팅된다."""
        from src.agents.graph.pipeline import _route_after_sql_validator
        from src.agents.state.state import FailureType
        from src.agents.state.state import LoopGuard

        lg = LoopGuard(generate_attempts=0)
        reason = _make_reason(failure_type=FailureType.SQL_SYNTAX, loop_guard=lg)
        state = _make_state(reason=reason)
        result = _route_after_sql_validator(state)

        passed = result == "fix_syntax"
        log_test_case(logger, "test_val_syntax_retry",
                      "SQL_SYNTAX + attempts=0", "fix_syntax", result, passed)
        assert result == "fix_syntax"

    def test_sql_syntax_escalates_to_replan_on_local_fix_limit(self):
        """SQL_SYNTAX이고 local_fix 한도 초과 시 replan으로 에스컬레이션된다."""
        from src.agents.graph.pipeline import _route_after_sql_validator
        from src.agents.state.state import FailureType, LoopGuard, MAX_LOCAL_FIXES

        lg = LoopGuard(local_fix_count=MAX_LOCAL_FIXES)
        reason = _make_reason(failure_type=FailureType.SQL_SYNTAX, loop_guard=lg)
        state = _make_state(reason=reason)
        result = _route_after_sql_validator(state)

        passed = result == "replan"
        log_test_case(
            logger, "test_val_syntax_escalate",
            f"SQL_SYNTAX + local_fix={MAX_LOCAL_FIXES}",
            "replan", result, passed,
        )
        assert result == "replan"

    def test_sql_syntax_with_max_generates_zero_skips_limit(self):
        """MAX_GENERATES == 0 이면 generate_attempts 조건이 비활성화되어 fix_syntax로 라우팅된다."""
        from src.agents.graph.pipeline import _route_after_sql_validator
        from src.agents.state.state import FailureType, LoopGuard, MAX_GENERATES

        assert MAX_GENERATES == 0, "이 테스트는 max_generates=0 환경을 전제"
        lg = LoopGuard(generate_attempts=100)  # 아무리 높아도 무시됨
        reason = _make_reason(failure_type=FailureType.SQL_SYNTAX, loop_guard=lg)
        state = _make_state(reason=reason)
        result = _route_after_sql_validator(state)

        passed = result == "fix_syntax"
        log_test_case(
            logger, "test_val_syntax_max0_skip",
            "SQL_SYNTAX + MAX_GENERATES=0 + attempts=100",
            "fix_syntax", result, passed,
        )
        assert result == "fix_syntax"

    def test_sql_syntax_exhausted_routes_to_conclude_failure_if_positive(self, monkeypatch):
        """MAX_GENERATES > 0 이면 generate_attempts 한도에서 conclude_failure로 라우팅된다."""
        import src.agents.graph.pipeline as pipeline_mod
        from src.agents.graph.pipeline import _route_after_sql_validator
        from src.agents.state.state import FailureType, LoopGuard

        monkeypatch.setattr(pipeline_mod, "MAX_GENERATES", 5)
        lg = LoopGuard(generate_attempts=5)
        reason = _make_reason(failure_type=FailureType.SQL_SYNTAX, loop_guard=lg)
        state = _make_state(reason=reason)
        result = _route_after_sql_validator(state)

        passed = result == "conclude_failure"
        log_test_case(
            logger, "test_val_syntax_exhausted",
            "SQL_SYNTAX + MAX_GENERATES=5 + attempts=5",
            "conclude_failure", result, passed,
        )
        assert result == "conclude_failure"

    def test_sql_semantic_local_within_limit_routes_to_fix_local(self):
        """SQL_SEMANTIC_LOCAL이고 local_fix 한도 미달이면 fix_local로 라우팅된다."""
        from src.agents.graph.pipeline import _route_after_sql_validator
        from src.agents.state.state import (
            FailureType, LoopGuard, MAX_LOCAL_FIXES,
        )

        lg = LoopGuard(local_fix_count=0, generate_attempts=0)
        reason = _make_reason(
            failure_type=FailureType.SQL_SEMANTIC_LOCAL,
            loop_guard=lg,
        )
        state = _make_state(reason=reason)
        result = _route_after_sql_validator(state)

        passed = result == "fix_local"
        log_test_case(
            logger, "test_val_semantic_local_fix",
            f"SQL_SEMANTIC_LOCAL + local_fix=0/<{MAX_LOCAL_FIXES}",
            "fix_local", result, passed,
        )
        assert result == "fix_local"

    def test_sql_semantic_local_escalates_to_replan(self):
        """local_fix 한도 초과 시 SQL_SEMANTIC_LOCAL은 replan으로 에스컬레이션된다."""
        from src.agents.graph.pipeline import _route_after_sql_validator
        from src.agents.state.state import (
            FailureType, LoopGuard, MAX_LOCAL_FIXES,
        )

        lg = LoopGuard(local_fix_count=MAX_LOCAL_FIXES)
        reason = _make_reason(
            failure_type=FailureType.SQL_SEMANTIC_LOCAL,
            loop_guard=lg,
        )
        state = _make_state(reason=reason)
        result = _route_after_sql_validator(state)

        passed = result == "replan"
        log_test_case(
            logger, "test_val_semantic_escalate",
            f"SQL_SEMANTIC_LOCAL + local_fix={MAX_LOCAL_FIXES}",
            "replan", result, passed,
        )
        assert result == "replan"

    @pytest.mark.parametrize("failure_type", [
        "SQL_STRUCTURAL",
        "EMPTY_RESULT",
        "DB_ERROR",
        "NO_KNOWLEDGE",
        "NO_TABLE",
        "TERM_UNRESOLVABLE",
        "GENERATION_FAILED",
    ])
    def test_structural_failures_route_to_replan(self, failure_type: str):
        """구조적/치명적 실패 유형은 replan(recovery_agent)으로 라우팅된다."""
        from src.agents.graph.pipeline import _route_after_sql_validator
        from src.agents.state.state import FailureType

        reason = _make_reason(failure_type=FailureType(failure_type))
        state = _make_state(reason=reason)
        result = _route_after_sql_validator(state)

        passed = result == "replan"
        log_test_case(
            logger, f"test_val_structural_{failure_type}",
            failure_type, "replan", result, passed,
        )
        assert result == "replan"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _route_after_recovery_agent
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestRouteAfterRecoveryAgent:
    """recovery_agent 후 라우팅 — CANCELLED / pending / Phase별."""

    def test_cancelled_routes_to_result_finalizer(self):
        """CANCELLED 상태는 result_finalizer로 즉시 종료된다."""
        from src.agents.graph.pipeline import _route_after_recovery_agent
        from src.agents.state.state import QueryStatus

        state = _make_state(status=QueryStatus.CANCELLED)
        result = _route_after_recovery_agent(state)

        passed = result == "result_finalizer"
        log_test_case(logger, "test_recovery_cancelled",
                      "CANCELLED", "result_finalizer", result, passed)
        assert result == "result_finalizer"

    def test_pending_signals_routes_to_clarification(self):
        """pending_signals가 있으면 clarification_handler로 라우팅된다."""
        from src.agents.graph.pipeline import _route_after_recovery_agent

        state = _make_state(pending_signals=[_make_signal()])
        result = _route_after_recovery_agent(state)

        passed = result == "clarification_handler"
        log_test_case(logger, "test_recovery_pending",
                      "pending_signals", "clarification_handler", result, passed)
        assert result == "clarification_handler"

    def test_exploring_phase_routes_to_context_retriever(self):
        """Phase=EXPLORING이면 context_retriever로 라우팅된다."""
        from src.agents.graph.pipeline import _route_after_recovery_agent
        from src.agents.state.state import Phase

        reason = _make_reason(phase=Phase.EXPLORING)
        state = _make_state(reason=reason)
        result = _route_after_recovery_agent(state)

        passed = result == "context_retriever"
        log_test_case(logger, "test_recovery_exploring",
                      "EXPLORING", "context_retriever", result, passed)
        assert result == "context_retriever"

    def test_generating_phase_routes_to_sql_generator(self):
        """Phase=GENERATING이면 sql_generator로 라우팅된다."""
        from src.agents.graph.pipeline import _route_after_recovery_agent
        from src.agents.state.state import Phase

        reason = _make_reason(phase=Phase.GENERATING)
        state = _make_state(reason=reason)
        result = _route_after_recovery_agent(state)

        passed = result == "sql_generator"
        log_test_case(logger, "test_recovery_generating",
                      "GENERATING", "sql_generator", result, passed)
        assert result == "sql_generator"

    def test_done_phase_routes_to_result_finalizer(self):
        """Phase=DONE이면 result_finalizer로 라우팅된다."""
        from src.agents.graph.pipeline import _route_after_recovery_agent
        from src.agents.state.state import Phase

        reason = _make_reason(phase=Phase.DONE)
        state = _make_state(reason=reason)
        result = _route_after_recovery_agent(state)

        passed = result == "result_finalizer"
        log_test_case(logger, "test_recovery_done",
                      "DONE", "result_finalizer", result, passed)
        assert result == "result_finalizer"

    def test_unexpected_phase_routes_to_result_finalizer(self):
        """예상치 못한 Phase는 result_finalizer로 안전하게 폴백된다."""
        from src.agents.graph.pipeline import _route_after_recovery_agent
        from src.agents.state.state import Phase

        # PLANNING / VALIDATING / REPLANNING 등 처리되지 않는 Phase
        reason = _make_reason(phase=Phase.PLANNING)
        state = _make_state(reason=reason)
        result = _route_after_recovery_agent(state)

        passed = result == "result_finalizer"
        log_test_case(logger, "test_recovery_unexpected_phase",
                      "PLANNING", "result_finalizer", result, passed)
        assert result == "result_finalizer"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _route_after_result_finalizer
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestRouteAfterResultFinalizer:
    """result_finalizer 후 라우팅 — 4가지 분기."""

    def test_cancelled_routes_to_error_end(self):
        """CANCELLED는 error_end로 라우팅된다."""
        from src.agents.graph.pipeline import _route_after_result_finalizer
        from src.agents.state.state import QueryStatus

        state = _make_state(
            status=QueryStatus.CANCELLED,
            reason=_make_reason(validated_sql="SELECT 1"),
        )
        result = _route_after_result_finalizer(state)

        passed = result == "error_end"
        log_test_case(logger, "test_finalizer_cancelled",
                      "CANCELLED + validated_sql", "error_end", result, passed)
        assert result == "error_end"

    def test_error_message_routes_to_error_end(self):
        """error_message가 있으면 error_end로 라우팅된다."""
        from src.agents.graph.pipeline import _route_after_result_finalizer

        state = _make_state(error_message="처리 중 오류 발생")
        result = _route_after_result_finalizer(state)

        passed = result == "error_end"
        log_test_case(logger, "test_finalizer_error_msg",
                      "error_message", "error_end", result, passed)
        assert result == "error_end"

    def test_validated_sql_routes_to_sql_executor(self):
        """validated_sql이 있으면 sql_executor로 라우팅된다."""
        from src.agents.graph.pipeline import _route_after_result_finalizer

        reason = _make_reason(validated_sql="SELECT 1 FROM dual")
        state = _make_state(reason=reason)
        result = _route_after_result_finalizer(state)

        passed = result == "sql_executor"
        log_test_case(logger, "test_finalizer_validated_sql",
                      "validated_sql present", "sql_executor", result, passed)
        assert result == "sql_executor"

    def test_no_sql_no_error_routes_to_error_end(self):
        """validated_sql도 error_message도 없으면 error_end로 라우팅된다."""
        from src.agents.graph.pipeline import _route_after_result_finalizer

        state = _make_state()
        result = _route_after_result_finalizer(state)

        passed = result == "error_end"
        log_test_case(logger, "test_finalizer_no_sql_no_error",
                      "no sql, no error", "error_end", result, passed)
        assert result == "error_end"

    def test_cancelled_takes_priority_over_validated_sql(self):
        """CANCELLED는 validated_sql이 있어도 error_end로 라우팅된다 (F2 해소)."""
        from src.agents.graph.pipeline import _route_after_result_finalizer
        from src.agents.state.state import QueryStatus

        reason = _make_reason(validated_sql="SELECT 1")
        state = _make_state(
            status=QueryStatus.CANCELLED,
            reason=reason,
        )
        result = _route_after_result_finalizer(state)

        passed = result == "error_end"
        log_test_case(
            logger, "test_finalizer_cancelled_priority",
            "CANCELLED + validated_sql",
            "error_end (CANCELLED 최우선)", result, passed,
        )
        assert result == "error_end"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _route_after_execution
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestRouteAfterExecution:
    """execute_sql 후 라우팅 — 3가지 분기."""

    def test_error_status_routes_to_error_end(self):
        """ERROR 상태는 error_end로 라우팅된다."""
        from src.agents.graph.pipeline import _route_after_execution
        from src.agents.state.state import QueryStatus

        state = _make_state(status=QueryStatus.ERROR)
        result = _route_after_execution(state)

        passed = result == "error_end"
        log_test_case(logger, "test_exec_error",
                      "ERROR", "error_end", result, passed)
        assert result == "error_end"

    def test_cancelled_status_routes_to_error_end(self):
        """CANCELLED 상태는 error_end로 라우팅된다."""
        from src.agents.graph.pipeline import _route_after_execution
        from src.agents.state.state import QueryStatus

        state = _make_state(status=QueryStatus.CANCELLED)
        result = _route_after_execution(state)

        passed = result == "error_end"
        log_test_case(logger, "test_exec_cancelled",
                      "CANCELLED", "error_end", result, passed)
        assert result == "error_end"

    def test_data_analysis_intent_routes_to_analyzer(self):
        """DATA_ANALYSIS + needs_analyzer=True 의도는 analyzer로 라우팅된다."""
        from src.agents.graph.pipeline import _route_after_execution
        from src.agents.state.state import IntentType, QueryStatus

        state = _make_state(
            intent=IntentType.DATA_ANALYSIS,
            status=QueryStatus.EXECUTED,
            needs_analyzer=True,
        )
        result = _route_after_execution(state)

        passed = result == "analyzer"
        log_test_case(logger, "test_exec_analysis",
                      "DATA_ANALYSIS+needs_analyzer", "analyzer", result, passed)
        assert result == "analyzer"

    def test_data_analysis_without_needs_analyzer_routes_to_visualizer(self):
        """DATA_ANALYSIS이지만 needs_analyzer=False면 visualizer로 스킵 라우팅된다 (opt-in)."""
        from src.agents.graph.pipeline import _route_after_execution
        from src.agents.state.state import IntentType, QueryStatus

        state = _make_state(
            intent=IntentType.DATA_ANALYSIS,
            status=QueryStatus.EXECUTED,
            needs_analyzer=False,
        )
        result = _route_after_execution(state)

        passed = result == "visualizer"
        log_test_case(logger, "test_exec_analysis_optin_false",
                      "DATA_ANALYSIS+needs_analyzer=False", "visualizer", result, passed)
        assert result == "visualizer"

    def test_data_extraction_intent_routes_to_visualizer(self):
        """DATA_EXTRACTION 의도는 visualizer로 라우팅된다."""
        from src.agents.graph.pipeline import _route_after_execution
        from src.agents.state.state import IntentType, QueryStatus

        state = _make_state(
            intent=IntentType.DATA_EXTRACTION,
            status=QueryStatus.EXECUTED,
        )
        result = _route_after_execution(state)

        passed = result == "visualizer"
        log_test_case(logger, "test_exec_extraction",
                      "DATA_EXTRACTION", "visualizer", result, passed)
        assert result == "visualizer"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _route_after_clarify
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestRouteAfterClarify:
    """clarification_handler 후 라우팅 — source_node 복귀."""

    @pytest.mark.parametrize("source_node", [
        "intent_classifier",
        "query_normalizer",
    ])
    def test_valid_source_node_returns_to_it(self, source_node: str):
        """유효한 source_node로 올바르게 복귀한다."""
        from src.agents.graph.pipeline import _route_after_clarify

        signal = _make_signal(
            source_node=source_node,
            turn_id="turn-001",
            answer="사용자 응답",
        )
        state = _make_state(
            turn_id="turn-001",
            resolved_signals=[signal],
        )
        result = _route_after_clarify(state)

        passed = result == source_node
        log_test_case(
            logger, f"test_clarify_return_{source_node}",
            source_node, source_node, result, passed,
        )
        assert result == source_node

    @pytest.mark.parametrize("legacy_name,expected", [
        ("resolve_history", "intent_classifier"),
        ("classify_intent", "intent_classifier"),
        ("resolve_and_classify", "intent_classifier"),
    ])
    def test_legacy_source_node_remapped(self, legacy_name: str, expected: str):
        """과도기 레거시 source_node 이름이 현재 이름으로 리매핑된다."""
        from src.agents.graph.pipeline import _route_after_clarify

        signal = _make_signal(
            source_node=legacy_name,
            turn_id="turn-002",
            answer="응답",
        )
        state = _make_state(
            turn_id="turn-002",
            resolved_signals=[signal],
        )
        result = _route_after_clarify(state)

        passed = result == expected
        log_test_case(
            logger, f"test_clarify_legacy_{legacy_name}",
            legacy_name, expected, result, passed,
        )
        assert result == expected

    def test_invalid_source_node_falls_back_to_intent_classifier(self):
        """유효하지 않은 source_node는 intent_classifier로 폴백된다."""
        from src.agents.graph.pipeline import _route_after_clarify

        signal = _make_signal(
            source_node="unknown_node_xyz",
            turn_id="turn-003",
            answer="응답",
        )
        state = _make_state(
            turn_id="turn-003",
            resolved_signals=[signal],
        )
        result = _route_after_clarify(state)

        passed = result == "intent_classifier"
        log_test_case(
            logger, "test_clarify_invalid_source",
            "unknown_node_xyz", "intent_classifier", result, passed,
        )
        assert result == "intent_classifier"

    def test_no_matching_turn_id_falls_back_to_intent_classifier(self):
        """현재 turn_id와 일치하는 시그널이 없으면 intent_classifier로 폴백된다."""
        from src.agents.graph.pipeline import _route_after_clarify

        # 이전 턴의 시그널 (turn_id 불일치)
        signal = _make_signal(
            source_node="sql_generator",
            turn_id="old-turn-999",
            answer="이전 응답",
        )
        state = _make_state(
            turn_id="current-turn-001",
            resolved_signals=[signal],
        )
        result = _route_after_clarify(state)

        passed = result == "intent_classifier"
        log_test_case(
            logger, "test_clarify_turn_id_mismatch",
            "old turn_id signal", "intent_classifier", result, passed,
        )
        assert result == "intent_classifier"

    def test_empty_resolved_signals_falls_back_to_intent_classifier(self):
        """resolved_signals가 없으면 intent_classifier로 폴백된다."""
        from src.agents.graph.pipeline import _route_after_clarify

        state = _make_state(turn_id="turn-001")
        result = _route_after_clarify(state)

        passed = result == "intent_classifier"
        log_test_case(
            logger, "test_clarify_no_signals",
            "empty resolved_signals", "intent_classifier", result, passed,
        )
        assert result == "intent_classifier"

    def test_latest_signal_used_when_multiple_exist(self):
        """동일 turn_id 시그널이 여러 개면 마지막 것이 사용된다."""
        from src.agents.graph.pipeline import _route_after_clarify

        signals = [
            _make_signal(
                source_node="intent_classifier",
                turn_id="turn-x",
                answer="첫 응답",
            ),
            _make_signal(
                source_node="query_normalizer",
                turn_id="turn-x",
                answer="두 번째 응답",
            ),
        ]
        state = _make_state(
            turn_id="turn-x",
            resolved_signals=signals,
        )
        result = _route_after_clarify(state)

        passed = result == "query_normalizer"
        log_test_case(
            logger, "test_clarify_latest_signal",
            "2개 시그널, 마지막=query_normalizer",
            "query_normalizer", result, passed,
        )
        assert result == "query_normalizer"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _handle_error
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestHandleError:
    """_handle_error 노드 — CANCELLED 보존 / 재시도 소진 / 일반 에러."""

    def test_cancelled_preserves_existing_response(self):
        """CANCELLED 상태에서 기존 formatted_response를 보존한다."""
        from src.agents.graph.pipeline import _handle_error
        from src.agents.state.state import QueryStatus

        state = _make_state(
            status=QueryStatus.CANCELLED,
            formatted_response="요청이 취소되었습니다.",
        )
        result = _handle_error(state)

        passed = (
            result["formatted_response"] == "요청이 취소되었습니다."
            and result["status"] == QueryStatus.CANCELLED
        )
        log_test_case(
            logger, "test_handle_cancelled",
            "CANCELLED + formatted_response",
            "보존된 메시지", result["formatted_response"], passed,
        )
        assert result["formatted_response"] == "요청이 취소되었습니다."
        assert result["status"] == QueryStatus.CANCELLED

    def test_cancelled_without_response_uses_default_message(self):
        """CANCELLED 상태에서 formatted_response가 없으면 기본 메시지를 사용한다."""
        from src.agents.graph.pipeline import _handle_error
        from src.agents.state.state import QueryStatus

        state = _make_state(status=QueryStatus.CANCELLED)
        result = _handle_error(state)

        passed = "중단" in result["formatted_response"]
        log_test_case(
            logger, "test_handle_cancelled_default",
            "CANCELLED + no response",
            "중단 메시지", result["formatted_response"], passed,
        )
        assert "중단" in result["formatted_response"]
        assert result["status"] == QueryStatus.CANCELLED

    def test_retry_exhausted_returns_exhausted_message(self, monkeypatch):
        """MAX_GENERATES > 0 이고 generate_attempts >= 한도이면 재시도 소진 메시지를 반환한다."""
        import src.agents.graph.pipeline as pipeline_mod
        from src.agents.graph.pipeline import _handle_error
        from src.agents.state.state import QueryStatus
        from src.agents.models.user_messages import ERR_SQL_RETRY_EXHAUSTED

        monkeypatch.setattr(pipeline_mod, "MAX_GENERATES", 5)
        lg_state = _make_reason()
        lg_state.loop_guard.generate_attempts = 5
        state = _make_state(reason=lg_state)

        result = _handle_error(state)

        passed = (
            result["formatted_response"] == ERR_SQL_RETRY_EXHAUSTED
            and result["status"] == QueryStatus.ERROR
        )
        log_test_case(
            logger, "test_handle_retry_exhausted",
            "MAX_GENERATES=5 + generate_attempts=5",
            "ERR_SQL_RETRY_EXHAUSTED", result["formatted_response"][:30], passed,
        )
        assert result["formatted_response"] == ERR_SQL_RETRY_EXHAUSTED
        assert result["status"] == QueryStatus.ERROR

    def test_generic_error_includes_error_message(self):
        """일반 에러는 error_message를 포함한 친절한 응답을 반환한다."""
        from src.agents.graph.pipeline import _handle_error
        from src.agents.state.state import QueryStatus

        state = _make_state(error_message="테이블을 찾을 수 없습니다.")
        result = _handle_error(state)

        passed = (
            "테이블을 찾을 수 없습니다." in result["formatted_response"]
            and result["status"] == QueryStatus.ERROR
        )
        log_test_case(
            logger, "test_handle_generic_error",
            "error_message set",
            "error_message 포함", result["formatted_response"], passed,
        )
        assert "테이블을 찾을 수 없습니다." in result["formatted_response"]
        assert result["status"] == QueryStatus.ERROR

    def test_no_error_message_uses_generic_fallback(self):
        """error_message가 없으면 ERR_GENERIC 폴백을 사용한다."""
        from src.agents.graph.pipeline import _handle_error
        from src.agents.state.state import QueryStatus
        from src.agents.models.user_messages import ERR_GENERIC

        state = _make_state()
        result = _handle_error(state)

        passed = (
            ERR_GENERIC in result["formatted_response"]
            and result["status"] == QueryStatus.ERROR
        )
        log_test_case(
            logger, "test_handle_no_error_msg",
            "no error_message",
            "ERR_GENERIC 포함", result["formatted_response"][:40], passed,
        )
        assert ERR_GENERIC in result["formatted_response"]
        assert result["status"] == QueryStatus.ERROR


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# runner.py — _build_result / _build_safe_insight
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestBuildSafeInsight:
    """_build_safe_insight — 예외 안전성 테스트."""

    def test_empty_result_returns_dict(self):
        """빈 result dict에서 dict를 반환한다."""
        from src.agents.graph.runner import _build_safe_insight

        result = _build_safe_insight({})

        passed = isinstance(result, dict)
        log_test_case(logger, "test_insight_empty_result",
                      "{}", "dict", type(result).__name__, passed)
        assert isinstance(result, dict)

    def test_exception_returns_empty_dict(self):
        """내부 예외가 발생해도 빈 dict를 반환한다 (예외 미전파)."""
        from src.agents.graph.runner import _build_safe_insight

        # insight_builder가 처리할 수 없는 잘못된 데이터
        result = _build_safe_insight({"sql_result": "not_a_model"})

        passed = isinstance(result, dict)
        log_test_case(
            logger, "test_insight_exception_safe",
            "invalid sql_result",
            "dict (예외 미전파)", type(result).__name__, passed,
        )
        assert isinstance(result, dict)


class TestBuildResult:
    """_build_result — formatted_response 추출 및 CANCELLED 판정."""

    def _make_mock_handler(self):
        """DataCopilotCallbackHandler 대용 최소 mock 객체를 생성한다."""
        class _FakeTrace:
            total_duration_ms = 0
            node_path: list = []

        class _FakeHandler:
            trace = _FakeTrace()
            run_id = "test-run"

            def record_sql(self, data): pass
            def end_run(self, **kwargs): pass
            def save(self, **kwargs): return []

        return _FakeHandler()

    def test_formatted_response_extracted(self):
        """formatted_response 필드가 response로 추출된다."""
        from src.agents.graph.runner import _build_result

        handler = self._make_mock_handler()
        raw = {"formatted_response": "결과 텍스트입니다."}
        result = _build_result(handler, raw)

        passed = result.response == "결과 텍스트입니다."
        log_test_case(
            logger, "test_build_result_response",
            "formatted_response",
            "결과 텍스트입니다.", result.response, passed,
        )
        assert result.response == "결과 텍스트입니다."

    def test_missing_formatted_response_uses_fallback(self):
        """formatted_response가 없으면 기본 fallback 메시지를 사용한다."""
        from src.agents.graph.runner import _build_result

        handler = self._make_mock_handler()
        result = _build_result(handler, {})

        passed = "응답을 생성할 수 없습니다." in result.response
        log_test_case(
            logger, "test_build_result_fallback",
            "no formatted_response",
            "fallback 메시지", result.response, passed,
        )
        assert "응답을 생성할 수 없습니다." in result.response

    def test_cancelled_status_sets_cancelled_flag(self):
        """status=cancelled이면 PipelineResult.cancelled=True가 설정된다."""
        from src.agents.graph.runner import _build_result
        from src.agents.state.state import QueryStatus

        handler = self._make_mock_handler()
        raw = {
            "formatted_response": "취소됨",
            "status": QueryStatus.CANCELLED,
        }
        result = _build_result(handler, raw)

        passed = result.cancelled is True
        log_test_case(
            logger, "test_build_result_cancelled",
            "status=CANCELLED",
            "cancelled=True", result.cancelled, passed,
        )
        assert result.cancelled is True

    def test_non_cancelled_status_does_not_set_flag(self):
        """취소되지 않은 상태는 cancelled=False다."""
        from src.agents.graph.runner import _build_result
        from src.agents.state.state import QueryStatus

        handler = self._make_mock_handler()
        raw = {
            "formatted_response": "정상 응답",
            "status": QueryStatus.COMPLETED,
        }
        result = _build_result(handler, raw)

        passed = result.cancelled is False
        log_test_case(
            logger, "test_build_result_not_cancelled",
            "status=COMPLETED",
            "cancelled=False", result.cancelled, passed,
        )
        assert result.cancelled is False
