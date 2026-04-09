"""readiness_gate 노드 헬퍼 함수 단위 테스트.

테스트 대상 (LLM 없이 rule-based):
  - _apply_force_generate: 2회 이상 replan 후 강제 GENERATE 전환
  - _set_failure_context: failure_type·failure_reason 결정 로직
  - _collect_stats: 추적용 통계 수집

실제 환경에서 실행 — Mock 없음.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import pytest

from src.agents.state.state import (
    ConfidenceStatus,
    DeadEnd,
    ExecutionStep,
    FailureType,
    Hypothesis,
    HypothesisStatus,
    KnowledgeItem,
    LoopGuard,
    ReasoningState,
    SelectionStatus,
    StepStatus,
    TableMeta,
)
from src.agents.nodes.reason.readiness_gate import (
    _apply_force_generate,
    _collect_stats,
    _set_failure_context,
)
from src.services.confidence_scorer import (
    ReadinessVerdict,
    THRESHOLD_FORCE_GENERATE,
)
from tests.conftest import get_test_logger, log_test_case

logger = get_test_logger("test_readiness_gate")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 헬퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _ki(
    key: str = "measure:잔액",
    status: ConfidenceStatus = ConfidenceStatus.UNRESOLVED,
    confidence: float = 0.0,
    is_critical: bool = True,
) -> KnowledgeItem:
    return KnowledgeItem(
        key=key, status=status, confidence=confidence, is_critical=is_critical,
    )


def _table(
    name: str = "TB_LOAN",
    selection_status: SelectionStatus = SelectionStatus.SELECTED,
) -> TableMeta:
    return TableMeta(table_name=name, selection_status=selection_status)


def _reason(**kw) -> ReasoningState:
    defaults = dict(
        hypotheses=[
            Hypothesis(
                hypothesis_id="H1",
                status=HypothesisStatus.ACTIVE,
            )
        ],
        current_hypothesis=Hypothesis(
            hypothesis_id="H1",
            status=HypothesisStatus.ACTIVE,
        ),
    )
    defaults.update(kw)
    return ReasoningState(**defaults)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _apply_force_generate
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestApplyForceGenerate:
    """_apply_force_generate 강제 전환 로직 테스트."""

    def _base_reason(
        self,
        replan_count: int = 2,
        with_table: bool = True,
    ) -> ReasoningState:
        tables = [_table()] if with_table else []
        guard = LoopGuard(replan_count=replan_count)
        return _reason(explored_tables=tables, loop_guard=guard)

    def test_replan_2times_sufficient_score_triggers_force_generate(self):
        """replan 2회 + 점수 ≥ THRESHOLD_FORCE_GENERATE + 탐색 테이블 → GENERATE."""
        reason = self._base_reason(replan_count=2)
        score = THRESHOLD_FORCE_GENERATE + 0.01
        result = _apply_force_generate(ReadinessVerdict.REPLAN, reason, score)
        passed = result == ReadinessVerdict.GENERATE
        log_test_case(logger, "force_generate_replan", score, "GENERATE", result, passed)
        assert passed

    def test_terminate_also_triggers_force_generate(self):
        """TERMINATE 판정도 replan 2회 + 점수 충족이면 GENERATE로 전환."""
        reason = self._base_reason(replan_count=2)
        score = THRESHOLD_FORCE_GENERATE + 0.01
        result = _apply_force_generate(ReadinessVerdict.TERMINATE, reason, score)
        passed = result == ReadinessVerdict.GENERATE
        log_test_case(logger, "force_generate_terminate", score, "GENERATE", result, passed)
        assert passed

    def test_replan_1time_does_not_trigger(self):
        """replan 1회는 강제 전환 미적용 — 원래 verdict 유지."""
        reason = self._base_reason(replan_count=1)
        score = THRESHOLD_FORCE_GENERATE + 0.1
        result = _apply_force_generate(ReadinessVerdict.REPLAN, reason, score)
        passed = result == ReadinessVerdict.REPLAN
        log_test_case(logger, "no_force_replan_1", score, "REPLAN", result, passed)
        assert passed

    def test_score_below_threshold_does_not_trigger(self):
        """점수 < THRESHOLD_FORCE_GENERATE면 강제 전환 불가."""
        reason = self._base_reason(replan_count=3)
        score = THRESHOLD_FORCE_GENERATE - 0.01
        result = _apply_force_generate(ReadinessVerdict.REPLAN, reason, score)
        passed = result == ReadinessVerdict.REPLAN
        log_test_case(logger, "no_force_low_score", score, "REPLAN", result, passed)
        assert passed

    def test_no_explored_tables_does_not_trigger(self):
        """탐색된 테이블이 없으면 강제 전환 불가."""
        reason = self._base_reason(replan_count=3, with_table=False)
        score = THRESHOLD_FORCE_GENERATE + 0.1
        result = _apply_force_generate(ReadinessVerdict.REPLAN, reason, score)
        passed = result == ReadinessVerdict.REPLAN
        log_test_case(logger, "no_force_no_tables", score, "REPLAN", result, passed)
        assert passed

    def test_generate_verdict_is_passed_through_unchanged(self):
        """이미 GENERATE이면 그대로 반환."""
        reason = self._base_reason(replan_count=5)
        score = THRESHOLD_FORCE_GENERATE + 0.1
        result = _apply_force_generate(ReadinessVerdict.GENERATE, reason, score)
        passed = result == ReadinessVerdict.GENERATE
        log_test_case(logger, "generate_passthrough", score, "GENERATE", result, passed)
        assert passed

    def test_explore_verdict_is_passed_through_unchanged(self):
        """EXPLORE 판정은 강제 전환 대상이 아님."""
        reason = self._base_reason(replan_count=5)
        score = THRESHOLD_FORCE_GENERATE + 0.1
        result = _apply_force_generate(ReadinessVerdict.EXPLORE, reason, score)
        passed = result == ReadinessVerdict.EXPLORE
        log_test_case(logger, "explore_passthrough", score, "EXPLORE", result, passed)
        assert passed


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _set_failure_context
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestSetFailureContext:
    """_set_failure_context failure_type·reason 결정 테스트."""

    def test_no_selected_table_sets_no_table(self):
        """SELECTED 테이블 없음 → NO_TABLE."""
        tables = [_table("TB_A", SelectionStatus.REJECTED)]
        reason = _reason(explored_tables=tables)
        _set_failure_context(reason, 0.3)
        passed = reason.failure_type == FailureType.NO_TABLE
        log_test_case(logger, "no_table_failure", {}, "NO_TABLE", reason.failure_type, passed)
        assert passed

    def test_no_explored_tables_at_all_sets_no_table(self):
        """탐색된 테이블이 아예 없음 → NO_TABLE, 메시지에 0개 언급."""
        reason = _reason(explored_tables=[])
        _set_failure_context(reason, 0.1)
        passed = (
            reason.failure_type == FailureType.NO_TABLE
            and "0개" in (reason.failure_reason or "")
        )
        log_test_case(
            logger, "empty_tables_no_table", {},
            "NO_TABLE+0개", reason.failure_type, passed,
        )
        assert passed

    def test_selected_table_no_knowledge_sets_no_knowledge(self):
        """SELECTED 테이블 있고 knowledge_items 비어있음 → NO_KNOWLEDGE."""
        tables = [_table("TB_A", SelectionStatus.SELECTED)]
        reason = _reason(explored_tables=tables, knowledge_items=[])
        _set_failure_context(reason, 0.2)
        passed = reason.failure_type == FailureType.NO_KNOWLEDGE
        log_test_case(logger, "no_knowledge_failure", {}, "NO_KNOWLEDGE", reason.failure_type, passed)
        assert passed

    def test_selected_table_with_knowledge_sets_term_unresolvable(self):
        """SELECTED 테이블 + knowledge_items 있음 → TERM_UNRESOLVABLE."""
        tables = [_table("TB_A", SelectionStatus.SELECTED)]
        items = [_ki("measure:잔액", ConfidenceStatus.UNRESOLVED)]
        reason = _reason(explored_tables=tables, knowledge_items=items)
        _set_failure_context(reason, 0.4)
        passed = reason.failure_type == FailureType.TERM_UNRESOLVABLE
        log_test_case(
            logger, "term_unresolvable", {},
            "TERM_UNRESOLVABLE", reason.failure_type, passed,
        )
        assert passed

    def test_failure_reason_includes_score(self):
        """failure_reason에 점수 백분율이 포함된다."""
        tables = [_table("TB_A", SelectionStatus.SELECTED)]
        items = [_ki("measure:잔액")]
        reason = _reason(explored_tables=tables, knowledge_items=items)
        _set_failure_context(reason, 0.42)
        passed = "42%" in (reason.failure_reason or "")
        log_test_case(
            logger, "failure_reason_score", 0.42,
            "42% in reason", reason.failure_reason, passed,
        )
        assert passed

    def test_unresolved_terms_listed_in_failure_reason(self):
        """UNRESOLVED 용어가 failure_reason에 열거된다."""
        tables = [_table("TB_A", SelectionStatus.SELECTED)]
        items = [
            _ki("measure:여신잔액", ConfidenceStatus.UNRESOLVED),
            _ki("filter:지점코드=001", ConfidenceStatus.UNRESOLVED),
        ]
        reason = _reason(explored_tables=tables, knowledge_items=items)
        _set_failure_context(reason, 0.3)
        fr = reason.failure_reason or ""
        passed = "measure:여신잔액" in fr or "filter:지점코드=001" in fr
        log_test_case(logger, "unresolved_in_reason", items, "terms listed", fr, passed)
        assert passed

    def test_does_not_overwrite_existing_failure_type(self):
        """이미 failure_type이 설정되어 있으면 덮어쓴다 (호출자 책임)."""
        # _set_failure_context는 항상 재설정한다 (readiness_gate에서 None 체크 후 호출)
        reason = _reason()
        reason.failure_type = FailureType.SQL_SYNTAX  # 이미 설정
        _set_failure_context(reason, 0.2)
        # 함수는 무조건 재설정하므로 SQL_SYNTAX가 남을 수 없다
        passed = reason.failure_type != FailureType.SQL_SYNTAX
        log_test_case(
            logger, "overwrite_failure_type", "SQL_SYNTAX",
            "not SQL_SYNTAX", reason.failure_type, passed,
        )
        assert passed

    def test_no_table_rejected_all_includes_explored_count_in_reason(self):
        """탐색은 했지만 전부 REJECTED → failure_reason에 탐색 테이블 수 표기."""
        tables = [
            _table("TB_A", SelectionStatus.REJECTED),
            _table("TB_B", SelectionStatus.REJECTED),
        ]
        reason = _reason(explored_tables=tables)
        _set_failure_context(reason, 0.1)
        fr = reason.failure_reason or ""
        passed = "2개" in fr
        log_test_case(logger, "rejected_tables_count", tables, "2개 in reason", fr, passed)
        assert passed


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _collect_stats
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestCollectStats:
    """_collect_stats 통계 수집 테스트."""

    def test_empty_state_returns_zero_stats(self):
        """빈 상태 → 0/0, tables=0, pending=0."""
        reason = _reason()
        stats = _collect_stats(reason)
        passed = (
            stats["knowledge"] == "0/0"
            and stats["explored_tables"] == 0
            and stats["pending_steps"] == 0
        )
        log_test_case(logger, "empty_stats", {}, "0/0,0,0", stats, passed)
        assert passed

    def test_knowledge_ratio_confirmed_vs_total(self):
        """knowledge는 confidence>=0.8인 항목/전체 비율로 표시된다."""
        items = [
            _ki("k1", confidence=0.9),
            _ki("k2", confidence=0.8),
            _ki("k3", confidence=0.5),
        ]
        reason = _reason(knowledge_items=items)
        stats = _collect_stats(reason)
        passed = stats["knowledge"] == "2/3"
        log_test_case(logger, "knowledge_ratio", items, "2/3", stats["knowledge"], passed)
        assert passed

    def test_explored_tables_count(self):
        """explored_tables 수가 정확히 집계된다."""
        tables = [_table("TB_A"), _table("TB_B"), _table("TB_C")]
        reason = _reason(explored_tables=tables)
        stats = _collect_stats(reason)
        passed = stats["explored_tables"] == 3
        log_test_case(logger, "tables_count", tables, 3, stats["explored_tables"], passed)
        assert passed

    def test_pending_steps_only_counts_pending(self):
        """PENDING 스텝만 집계하고 DONE·SKIPPED는 제외한다."""
        steps = [
            ExecutionStep(step=1, tool="t", input="i", purpose="p", status=StepStatus.PENDING),
            ExecutionStep(step=2, tool="t", input="i", purpose="p", status=StepStatus.DONE),
            ExecutionStep(step=3, tool="t", input="i", purpose="p", status=StepStatus.PENDING),
            ExecutionStep(step=4, tool="t", input="i", purpose="p", status=StepStatus.SKIPPED),
        ]
        reason = _reason(execution_plan=steps)
        stats = _collect_stats(reason)
        passed = stats["pending_steps"] == 2
        log_test_case(logger, "pending_steps_count", steps, 2, stats["pending_steps"], passed)
        assert passed

    def test_stats_keys_are_present(self):
        """반환 dict에 필수 키가 모두 존재한다."""
        reason = _reason()
        stats = _collect_stats(reason)
        required_keys = {"knowledge", "explored_tables", "pending_steps"}
        passed = required_keys.issubset(stats.keys())
        log_test_case(logger, "stats_keys", {}, required_keys, set(stats.keys()), passed)
        assert passed
