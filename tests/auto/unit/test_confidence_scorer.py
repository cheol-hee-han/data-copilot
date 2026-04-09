"""confidence_scorer 단위 테스트.

테스트 대상:
  - calculate_readiness: 0.0~1.0 준비도 점수 계산
  - all_critical_confirmed: critical 항목 전체 해소 여부
  - should_ask_user: ASK_USER 발동 조건
  - evaluate_readiness: 다음 행동 판정 (SSOT)

실제 환경에서 실행 — Mock 없음.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import pytest

from src.agents.state.state import (
    ConfidenceStatus,
    ExecutionStep,
    Hypothesis,
    HypothesisStatus,
    KnowledgeItem,
    LoopGuard,
    ReasoningState,
    StepStatus,
    UseCaseEntry,
)
from src.services.confidence_scorer import (
    THRESHOLD_FORCE_GENERATE,
    THRESHOLD_GENERATE,
    ReadinessVerdict,
    all_critical_confirmed,
    calculate_readiness,
    evaluate_readiness,
    should_ask_user,
)
from tests.conftest import get_test_logger, log_test_case

logger = get_test_logger("test_confidence_scorer")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 헬퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _ki(
    key: str = "measure:잔액",
    status: ConfidenceStatus = ConfidenceStatus.UNRESOLVED,
    is_critical: bool = True,
    evidence: list[str] | None = None,
    confidence: float = 0.0,
) -> KnowledgeItem:
    return KnowledgeItem(
        key=key,
        status=status,
        is_critical=is_critical,
        evidence=evidence or [],
        confidence=confidence,
    )


def _uc(relevant: bool = False) -> UseCaseEntry:
    return UseCaseEntry(
        id="uc1",
        description="테스트 유사 SQL",
        relevant=relevant,
    )


def _active_hypothesis() -> Hypothesis:
    return Hypothesis(
        hypothesis_id="H1",
        description="테스트 가설",
        status=HypothesisStatus.ACTIVE,
    )


def _reason(
    knowledge_items: list[KnowledgeItem] | None = None,
    explored_use_cases: list[UseCaseEntry] | None = None,
    execution_plan: list[ExecutionStep] | None = None,
    loop_guard: LoopGuard | None = None,
    explored_tables=None,
    hypotheses=None,
    current_hypothesis=None,
) -> ReasoningState:
    from src.agents.state.state import TableMeta
    tables = explored_tables if explored_tables is not None else []
    hyp_list = hypotheses if hypotheses is not None else [_active_hypothesis()]
    cur_hyp = current_hypothesis if current_hypothesis is not None else _active_hypothesis()
    return ReasoningState(
        knowledge_items=knowledge_items or [],
        explored_use_cases=explored_use_cases or [],
        execution_plan=execution_plan or [],
        loop_guard=loop_guard or LoopGuard(),
        explored_tables=tables,
        hypotheses=hyp_list,
        current_hypothesis=cur_hyp,
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# calculate_readiness
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestCalculateReadiness:
    """calculate_readiness 점수 계산 테스트."""

    def test_no_items_no_usecases_returns_zero(self):
        """지식 항목도 활용사례도 없으면 0.0."""
        reason = _reason()
        score = calculate_readiness(reason)
        passed = score == 0.0
        log_test_case(logger, "score=0 when empty", {}, 0.0, score, passed)
        assert passed, f"expected 0.0, got {score}"

    def test_all_confirmed_no_usecases(self):
        """critical 항목 전부 CONFIRMED, 활용사례 없음 → term_score=1.0, uc=0.0 → 0.7."""
        items = [
            _ki("measure:잔액", ConfidenceStatus.CONFIRMED),
            _ki("measure:건수", ConfidenceStatus.CONFIRMED),
        ]
        reason = _reason(knowledge_items=items)
        score = calculate_readiness(reason)
        passed = abs(score - 0.7) < 0.001
        log_test_case(logger, "all_confirmed_no_uc", items, 0.7, score, passed)
        assert passed, f"expected 0.7, got {score}"

    def test_all_confirmed_with_3_relevant_usecases(self):
        """critical 모두 CONFIRMED + 관련 활용사례 3건 → 1.0."""
        items = [_ki("measure:잔액", ConfidenceStatus.CONFIRMED)]
        ucs = [_uc(relevant=True), _uc(relevant=True), _uc(relevant=True)]
        reason = _reason(knowledge_items=items, explored_use_cases=ucs)
        score = calculate_readiness(reason)
        passed = abs(score - 1.0) < 0.001
        log_test_case(logger, "all_confirmed_3_uc", {}, 1.0, score, passed)
        assert passed, f"expected 1.0, got {score}"

    def test_probable_counts_as_resolved(self):
        """PROBABLE 상태도 해소된 것으로 계산한다."""
        items = [_ki("measure:잔액", ConfidenceStatus.PROBABLE)]
        reason = _reason(knowledge_items=items)
        score = calculate_readiness(reason)
        passed = abs(score - 0.7) < 0.001
        log_test_case(logger, "probable_resolved", items, 0.7, score, passed)
        assert passed, f"expected 0.7, got {score}"

    def test_half_confirmed_half_unresolved(self):
        """critical 4개 중 2개 CONFIRMED → term=0.5, uc=0 → 0.35."""
        items = [
            _ki("k1", ConfidenceStatus.CONFIRMED),
            _ki("k2", ConfidenceStatus.CONFIRMED),
            _ki("k3", ConfidenceStatus.UNRESOLVED),
            _ki("k4", ConfidenceStatus.UNRESOLVED),
        ]
        reason = _reason(knowledge_items=items)
        score = calculate_readiness(reason)
        passed = abs(score - 0.35) < 0.001
        log_test_case(logger, "half_confirmed", items, 0.35, score, passed)
        assert passed, f"expected 0.35, got {score}"

    def test_non_critical_items_excluded_from_term_score(self):
        """is_critical=False 항목은 term_score 계산에서 제외된다."""
        items = [
            _ki("k1", ConfidenceStatus.CONFIRMED, is_critical=True),
            _ki("k2", ConfidenceStatus.UNRESOLVED, is_critical=False),
        ]
        reason = _reason(knowledge_items=items)
        score = calculate_readiness(reason)
        # critical 1개 모두 CONFIRMED → term=1.0, uc=0 → 0.7
        passed = abs(score - 0.7) < 0.001
        log_test_case(logger, "non_critical_excluded", items, 0.7, score, passed)
        assert passed, f"expected 0.7, got {score}"

    def test_only_non_critical_items_term_score_zero(self):
        """is_critical=True 항목이 없으면 term_score=0.0."""
        items = [
            _ki("k1", ConfidenceStatus.CONFIRMED, is_critical=False),
            _ki("k2", ConfidenceStatus.CONFIRMED, is_critical=False),
        ]
        reason = _reason(knowledge_items=items)
        score = calculate_readiness(reason)
        passed = score == 0.0
        log_test_case(logger, "only_non_critical", items, 0.0, score, passed)
        assert passed, f"expected 0.0, got {score}"

    @pytest.mark.parametrize("relevant_count,expected_uc_score", [
        (0, 0.0),
        (1, 1 / 3),
        (2, 2 / 3),
        (3, 1.0),
        (5, 1.0),  # 3개 초과 시 cap=1.0
    ])
    def test_use_case_score_capped_at_3(self, relevant_count, expected_uc_score):
        """활용사례 점수는 3건에서 cap=1.0이 된다."""
        ucs = [_uc(relevant=True)] * relevant_count + [_uc(relevant=False)]
        reason = _reason(explored_use_cases=ucs)
        score = calculate_readiness(reason)
        expected_total = round(0.0 * 0.70 + expected_uc_score * 0.30, 3)
        passed = abs(score - expected_total) < 0.001
        log_test_case(
            logger, f"uc_cap relevant={relevant_count}",
            relevant_count, expected_total, score, passed,
        )
        assert passed, f"relevant={relevant_count}: expected {expected_total}, got {score}"

    def test_conflicted_status_not_counted_as_resolved(self):
        """CONFLICTED 항목은 해소된 것으로 계산되지 않는다."""
        items = [
            _ki("k1", ConfidenceStatus.CONFIRMED),
            _ki("k2", ConfidenceStatus.CONFLICTED),
        ]
        reason = _reason(knowledge_items=items)
        score = calculate_readiness(reason)
        # term=0.5 (2개 중 1개), uc=0 → 0.35
        passed = abs(score - 0.35) < 0.001
        log_test_case(logger, "conflicted_not_resolved", items, 0.35, score, passed)
        assert passed, f"expected 0.35, got {score}"

    def test_score_rounded_to_3_decimal_places(self):
        """점수는 소수점 3자리로 반올림된다."""
        items = [
            _ki("k1", ConfidenceStatus.CONFIRMED),
            _ki("k2", ConfidenceStatus.UNRESOLVED),
            _ki("k3", ConfidenceStatus.UNRESOLVED),
        ]
        reason = _reason(knowledge_items=items)
        score = calculate_readiness(reason)
        # term=1/3 ≈ 0.333, score = 0.333*0.7 = 0.233
        passed = score == round(score, 3)
        log_test_case(logger, "score_rounded", {}, "rounded", score, passed)
        assert passed


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# all_critical_confirmed
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestAllCriticalConfirmed:
    """all_critical_confirmed 함수 테스트."""

    def test_empty_knowledge_items_returns_true(self):
        """critical 항목이 없으면 True (all() 공집합 규칙)."""
        reason = _reason()
        result = all_critical_confirmed(reason)
        passed = result is True
        log_test_case(logger, "empty_items_true", {}, True, result, passed)
        assert passed

    def test_all_confirmed_returns_true(self):
        """모든 critical 항목이 CONFIRMED이면 True."""
        items = [
            _ki("k1", ConfidenceStatus.CONFIRMED),
            _ki("k2", ConfidenceStatus.CONFIRMED),
        ]
        reason = _reason(knowledge_items=items)
        result = all_critical_confirmed(reason)
        passed = result is True
        log_test_case(logger, "all_confirmed", items, True, result, passed)
        assert passed

    def test_all_probable_returns_true(self):
        """모든 critical 항목이 PROBABLE이면 True."""
        items = [
            _ki("k1", ConfidenceStatus.PROBABLE),
            _ki("k2", ConfidenceStatus.PROBABLE),
        ]
        reason = _reason(knowledge_items=items)
        result = all_critical_confirmed(reason)
        passed = result is True
        log_test_case(logger, "all_probable", items, True, result, passed)
        assert passed

    @pytest.mark.parametrize("blocking_status", [
        ConfidenceStatus.UNRESOLVED,
        ConfidenceStatus.CANDIDATE,
        ConfidenceStatus.CONFLICTED,
    ])
    def test_blocking_status_returns_false(self, blocking_status):
        """UNRESOLVED/CANDIDATE/CONFLICTED 중 하나라도 있으면 False."""
        items = [
            _ki("k1", ConfidenceStatus.CONFIRMED),
            _ki("k2", blocking_status),
        ]
        reason = _reason(knowledge_items=items)
        result = all_critical_confirmed(reason)
        passed = result is False
        log_test_case(
            logger, f"blocking_{blocking_status.value}",
            blocking_status, False, result, passed,
        )
        assert passed, f"status={blocking_status.value}: expected False"

    def test_non_critical_unresolved_does_not_block(self):
        """is_critical=False인 UNRESOLVED 항목은 차단하지 않는다."""
        items = [
            _ki("k1", ConfidenceStatus.CONFIRMED, is_critical=True),
            _ki("k2", ConfidenceStatus.UNRESOLVED, is_critical=False),
        ]
        reason = _reason(knowledge_items=items)
        result = all_critical_confirmed(reason)
        passed = result is True
        log_test_case(logger, "non_critical_unresolved_ok", items, True, result, passed)
        assert passed

    def test_mixed_confirmed_and_probable_returns_true(self):
        """CONFIRMED + PROBABLE 조합은 True."""
        items = [
            _ki("k1", ConfidenceStatus.CONFIRMED),
            _ki("k2", ConfidenceStatus.PROBABLE),
            _ki("k3", ConfidenceStatus.CONFIRMED),
        ]
        reason = _reason(knowledge_items=items)
        result = all_critical_confirmed(reason)
        passed = result is True
        log_test_case(logger, "confirmed_probable_mix", items, True, result, passed)
        assert passed


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# should_ask_user
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestShouldAskUser:
    """should_ask_user 함수 테스트."""

    def test_no_conflicted_returns_false(self):
        """CONFLICTED 항목이 없으면 False."""
        items = [
            _ki("k1", ConfidenceStatus.CONFIRMED),
            _ki("k2", ConfidenceStatus.PROBABLE),
        ]
        reason = _reason(knowledge_items=items)
        result = should_ask_user(reason)
        passed = result is False
        log_test_case(logger, "no_conflicted", items, False, result, passed)
        assert passed

    def test_conflicted_single_table_reference_does_not_trigger(self):
        """CONFLICTED지만 다른 테이블 언급이 없으면 추론 진행 (False)."""
        ki = _ki("k1", ConfidenceStatus.CONFLICTED, evidence=["단순 모호성 설명"])
        reason = _reason(knowledge_items=[ki])
        result = should_ask_user(reason)
        passed = result is False
        log_test_case(logger, "conflicted_same_table", ki, False, result, passed)
        assert passed

    def test_conflicted_two_different_tables_triggers(self):
        """CONFLICTED + critical + 서로 다른 두 테이블 언급 → True."""
        ki = _ki(
            "measure:잔액",
            ConfidenceStatus.CONFLICTED,
            is_critical=True,
            evidence=[
                "TB_LOAN_ACNT 사용 시 일별 잔액",
                "TB_DEPOSIT_ACNT 사용 시 월별 잔액",
            ],
        )
        reason = _reason(knowledge_items=[ki])
        result = should_ask_user(reason)
        passed = result is True
        log_test_case(logger, "two_tables_conflict", ki, True, result, passed)
        assert passed

    def test_conflicted_non_critical_does_not_trigger(self):
        """critical=False인 CONFLICTED는 사용자 확인 불필요."""
        ki = KnowledgeItem(
            key="k1",
            status=ConfidenceStatus.CONFLICTED,
            is_critical=False,
            evidence=["TB_A 연관", "TB_B 연관"],
        )
        reason = _reason(knowledge_items=[ki])
        result = should_ask_user(reason)
        passed = result is False
        log_test_case(logger, "non_critical_conflict", ki, False, result, passed)
        assert passed

    def test_empty_state_returns_false(self):
        """지식 항목이 없으면 False."""
        reason = _reason()
        result = should_ask_user(reason)
        passed = result is False
        log_test_case(logger, "empty_state", {}, False, result, passed)
        assert passed

    def test_conflicted_with_three_tables_triggers(self):
        """세 개 이상의 테이블 참조도 True."""
        ki = _ki(
            "measure:지표",
            ConfidenceStatus.CONFLICTED,
            is_critical=True,
            evidence=[
                "TB_LOAN 관련",
                "TB_DEPOSIT 관련",
                "TB_BOND 관련",
            ],
        )
        reason = _reason(knowledge_items=[ki])
        result = should_ask_user(reason)
        passed = result is True
        log_test_case(logger, "three_tables_conflict", ki, True, result, passed)
        assert passed


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# evaluate_readiness
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestEvaluateReadiness:
    """evaluate_readiness 판정 함수 통합 테스트."""

    def _make_table(self, table_name: str = "TB_LOAN_ACNT"):
        from src.agents.state.state import TableMeta, SelectionStatus
        return TableMeta(
            table_name=table_name,
            selection_status=SelectionStatus.SELECTED,
        )

    def test_terminate_when_tool_calls_exceeded(self):
        """도구 호출 총량 초과 → TERMINATE."""
        from src.agents.state.state import MAX_TOOL_CALLS
        guard = LoopGuard(total_tool_calls=MAX_TOOL_CALLS)
        reason = _reason(loop_guard=guard, hypotheses=[], current_hypothesis=None)
        verdict = evaluate_readiness(reason)
        passed = verdict == ReadinessVerdict.TERMINATE
        log_test_case(logger, "terminate_tool_calls", MAX_TOOL_CALLS, "TERMINATE", verdict, passed)
        assert passed

    def test_terminate_when_replan_exceeded(self):
        """재계획 횟수 초과 → TERMINATE."""
        from src.agents.state.state import MAX_REPLANS
        guard = LoopGuard(replan_count=MAX_REPLANS)
        reason = _reason(loop_guard=guard, hypotheses=[], current_hypothesis=None)
        verdict = evaluate_readiness(reason)
        passed = verdict == ReadinessVerdict.TERMINATE
        log_test_case(logger, "terminate_replan", MAX_REPLANS, "TERMINATE", verdict, passed)
        assert passed

    def test_generate_when_score_high_and_all_confirmed_and_tables(self):
        """점수 ≥ 0.65 + all_critical_confirmed + explored_tables → GENERATE."""
        items = [_ki("k1", ConfidenceStatus.CONFIRMED)]
        tables = [self._make_table()]
        reason = _reason(knowledge_items=items, explored_tables=tables)
        verdict = evaluate_readiness(reason)
        passed = verdict == ReadinessVerdict.GENERATE
        log_test_case(logger, "generate_happy_path", {}, "GENERATE", verdict, passed)
        assert passed

    def test_replan_when_high_score_but_no_tables(self):
        """점수 충분하지만 explored_tables 없음 → REPLAN."""
        items = [_ki("k1", ConfidenceStatus.CONFIRMED)]
        reason = _reason(knowledge_items=items, explored_tables=[])
        verdict = evaluate_readiness(reason)
        passed = verdict == ReadinessVerdict.REPLAN
        log_test_case(logger, "replan_no_tables", {}, "REPLAN", verdict, passed)
        assert passed

    def test_explore_when_pending_steps_remain(self):
        """PENDING 스텝이 있으면 점수 낮아도 EXPLORE."""
        pending_step = ExecutionStep(
            step=1, tool="search_table_meta", input="여신",
            purpose="테이블 검색", status=StepStatus.PENDING,
        )
        reason = _reason(execution_plan=[pending_step])
        verdict = evaluate_readiness(reason)
        passed = verdict == ReadinessVerdict.EXPLORE
        log_test_case(logger, "explore_pending_steps", {}, "EXPLORE", verdict, passed)
        assert passed

    def test_ask_user_when_unresolvable_conflict(self):
        """탐색 완료 후 CONFLICTED + 다중 테이블 → ASK_USER."""
        ki = _ki(
            "measure:잔액",
            ConfidenceStatus.CONFLICTED,
            is_critical=True,
            evidence=["TB_LOAN 언급", "TB_DEPOSIT 언급"],
        )
        reason = _reason(knowledge_items=[ki], execution_plan=[])
        verdict = evaluate_readiness(reason)
        passed = verdict == ReadinessVerdict.ASK_USER
        log_test_case(logger, "ask_user_conflict", ki, "ASK_USER", verdict, passed)
        assert passed

    def test_replan_when_low_score_no_pending(self):
        """점수 낮고 PENDING 스텝 없고 충돌 없으면 REPLAN."""
        items = [
            _ki("k1", ConfidenceStatus.UNRESOLVED),
            _ki("k2", ConfidenceStatus.UNRESOLVED),
        ]
        reason = _reason(knowledge_items=items, execution_plan=[])
        verdict = evaluate_readiness(reason)
        passed = verdict == ReadinessVerdict.REPLAN
        log_test_case(logger, "replan_low_score", items, "REPLAN", verdict, passed)
        assert passed

    def test_done_steps_do_not_trigger_explore(self):
        """DONE 스텝만 있으면 PENDING 없음 → EXPLORE로 분기 안 됨."""
        done_step = ExecutionStep(
            step=1, tool="search_table_meta", input="여신",
            purpose="테이블 검색", status=StepStatus.DONE,
        )
        items = [_ki("k1", ConfidenceStatus.UNRESOLVED)]
        reason = _reason(knowledge_items=items, execution_plan=[done_step])
        verdict = evaluate_readiness(reason)
        # DONE 스텝만 있고 점수 낮음 → REPLAN or ASK
        passed = verdict != ReadinessVerdict.EXPLORE
        log_test_case(logger, "done_steps_no_explore", done_step, "not EXPLORE", verdict, passed)
        assert passed

    def test_threshold_constants_are_sane(self):
        """임계값 상수가 논리적 범위를 가진다."""
        assert 0.0 < THRESHOLD_FORCE_GENERATE < THRESHOLD_GENERATE < 1.0

    def test_verdict_to_phase_covers_all_verdicts(self):
        """VERDICT_TO_PHASE가 모든 ReadinessVerdict를 커버한다."""
        from src.services.confidence_scorer import VERDICT_TO_PHASE
        for verdict in ReadinessVerdict:
            assert verdict in VERDICT_TO_PHASE, f"{verdict} not in VERDICT_TO_PHASE"
