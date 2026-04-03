"""recovery_agent 노드 단위 테스트.

핵심 함수별 독립 테스트:
  - _handle_hypothesis_transition: ACTIVE→FAILED, DeadEnd 생성, PENDING 소비
  - _consume_next_pending: 우선순위 기반 PENDING 가설 소비
  - _parse_plan_response: 정상 JSON, 부분 JSON, fallback
  - _finalize_give_up: give_up+force-generate, give_up+failure
  - _attach_lessons: lessons_learned 첨부
  - _build_failure_summary: 실패 요약 생성
  - RecoveryPlan: 모델 기본값 검증
"""

from __future__ import annotations

import json

import pytest

from src.agents.state.state import (
    ConfidenceStatus,
    DeadEnd,
    FailureType,
    FinalStatus,
    Hypothesis,
    HypothesisStatus,
    KnowledgeItem,
    LoopGuard,
    Phase,
    ReasoningState,
)
from src.agents.nodes.reason.recovery_agent import (
    RecoveryPlan,
    _attach_lessons,
    _build_failure_summary,
    _consume_next_pending,
    _finalize_give_up,
    _handle_hypothesis_transition,
    _parse_plan_response,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 헬퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _reason(**kw) -> ReasoningState:
    return ReasoningState(**kw)


def _ki(
    key: str,
    kid: str = "K1",
    status: ConfidenceStatus = ConfidenceStatus.UNRESOLVED,
    **kw,
) -> KnowledgeItem:
    return KnowledgeItem(
        key=key, knowledge_id=kid, status=status, **kw,
    )


def _hyp(
    hid: str = "H1",
    status: HypothesisStatus = HypothesisStatus.PENDING,
    priority: float = 0.5,
) -> Hypothesis:
    return Hypothesis(
        hypothesis_id=hid, description="test",
        status=status, priority=priority,
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _handle_hypothesis_transition
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestHandleHypothesisTransition:
    """가설 상태 전이 로직."""

    def test_active_to_failed_and_consume_pending(self):
        """ACTIVE 가설 → FAILED + PENDING 소비."""
        reason = _reason(
            hypotheses=[
                _hyp("H1", HypothesisStatus.ACTIVE),
                _hyp("H2", HypothesisStatus.PENDING),
            ],
            current_hypothesis=_hyp("H1", HypothesisStatus.ACTIVE),
            failure_type=FailureType.SQL_STRUCTURAL,
            failure_reason="구조 불일치",
        )
        _handle_hypothesis_transition(reason)

        assert reason.current_hypothesis is not None
        assert reason.current_hypothesis.hypothesis_id == "H2"
        assert reason.current_hypothesis.status == HypothesisStatus.ACTIVE
        assert len(reason.dead_ends) == 1
        assert reason.dead_ends[0].hypothesis_id == "H1"

    def test_no_active_no_pending(self):
        """활성 가설 없고 PENDING도 없으면 None."""
        reason = _reason(
            hypotheses=[_hyp("H1", HypothesisStatus.FAILED)],
            current_hypothesis=None,
        )
        _handle_hypothesis_transition(reason)
        assert reason.current_hypothesis is None

    def test_active_to_failed_no_pending(self):
        """ACTIVE 가설 FAILED 전환, PENDING 없으면 None."""
        reason = _reason(
            hypotheses=[_hyp("H1", HypothesisStatus.ACTIVE)],
            current_hypothesis=_hyp("H1", HypothesisStatus.ACTIVE),
            failure_type=FailureType.NO_TABLE,
            failure_reason="테이블 없음",
        )
        _handle_hypothesis_transition(reason)
        assert reason.current_hypothesis is None
        assert len(reason.dead_ends) == 1

    def test_pending_priority_ordering(self):
        """여러 PENDING 가설 중 우선순위 높은 것 소비."""
        reason = _reason(
            hypotheses=[
                _hyp("H2", HypothesisStatus.PENDING, priority=1),
                _hyp("H3", HypothesisStatus.PENDING, priority=5),
            ],
            current_hypothesis=None,
        )
        _handle_hypothesis_transition(reason)
        assert reason.current_hypothesis.hypothesis_id == "H3"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _consume_next_pending
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestConsumeNextPending:
    """PENDING 가설 소비 로직."""

    def test_returns_none_when_empty(self):
        """PENDING 없으면 None."""
        hypotheses = [_hyp("H1", HypothesisStatus.FAILED)]
        result = _consume_next_pending(hypotheses)
        assert result is None

    def test_returns_highest_priority(self):
        """가장 높은 우선순위 PENDING 반환."""
        hypotheses = [
            _hyp("H1", HypothesisStatus.PENDING, priority=1),
            _hyp("H2", HypothesisStatus.PENDING, priority=3),
        ]
        result = _consume_next_pending(hypotheses)
        assert result.hypothesis_id == "H2"
        assert result.status == HypothesisStatus.ACTIVE

    def test_mutates_list_in_place(self):
        """원본 리스트의 상태가 ACTIVE로 변경된다."""
        hypotheses = [
            _hyp("H1", HypothesisStatus.PENDING, priority=1),
        ]
        _consume_next_pending(hypotheses)
        assert hypotheses[0].status == HypothesisStatus.ACTIVE


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _parse_plan_response
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestParsePlanResponse:
    """LLM 응답 파싱."""

    def test_valid_replan_json(self):
        """정상 replan JSON → RecoveryPlan."""
        raw = json.dumps({
            "action": "replan",
            "lessons_learned": "교훈",
            "execution_plan": [
                {
                    "tool": "search_table_meta",
                    "input": "고객",
                    "purpose": "검색",
                },
            ],
        })
        plan = _parse_plan_response(raw)
        assert plan.action == "replan"
        assert len(plan.execution_plan) == 1
        assert plan.execution_plan[0].tool == "search_table_meta"
        assert plan.lessons_learned == "교훈"

    def test_give_up_action(self):
        """action=give_up → give_up 결과."""
        raw = json.dumps({
            "action": "give_up",
            "lessons_learned": "포기",
        })
        plan = _parse_plan_response(raw)
        assert plan.action == "give_up"

    def test_no_json_raises(self):
        """JSON 없는 응답 → ValueError."""
        with pytest.raises(ValueError):
            _parse_plan_response("이것은 JSON이 아닙니다")

    def test_empty_plan_becomes_give_up(self):
        """빈 execution_plan → give_up으로 전환."""
        raw = json.dumps({
            "action": "replan",
            "execution_plan": [],
        })
        plan = _parse_plan_response(raw)
        assert plan.action == "give_up"

    def test_markdown_wrapped_json(self):
        """마크다운 코드블록으로 감싼 JSON."""
        raw = '```json\n{"action": "give_up"}\n```'
        plan = _parse_plan_response(raw)
        assert plan.action == "give_up"

    def test_new_hypothesis_parsed(self):
        """new_hypothesis 포함 시 파싱."""
        raw = json.dumps({
            "action": "replan",
            "execution_plan": [
                {
                    "tool": "search_table_meta",
                    "input": "대출",
                    "purpose": "검색",
                },
            ],
            "new_hypothesis": {
                "description": "대출 테이블 접근",
                "strategy": "직접 검색",
            },
        })
        plan = _parse_plan_response(raw)
        assert plan.new_hypothesis is not None
        assert "대출" in plan.new_hypothesis.description


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _finalize_give_up
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestFinalizeGiveUp:
    """recovery_agent give_up 종료 처리."""

    def test_low_score_failure(self):
        """score < threshold → DONE + FAILURE."""
        reason = _reason()
        _finalize_give_up(reason)
        assert reason.phase == Phase.DONE
        assert reason.final_status == FinalStatus.FAILURE

    def test_high_score_force_generate(self):
        """score ≥ threshold → GENERATING + force_generated."""
        from src.agents.state.state import CandidateTable
        reason = _reason(
            knowledge_items=[
                _ki(
                    "x", "K1", ConfidenceStatus.CONFIRMED,
                    confidence=0.9, value="v", is_critical=True,
                ),
            ],
            candidate_tables=[
                CandidateTable(
                    table_name="TB_A", description="설명",
                ),
            ],
        )
        _finalize_give_up(reason)
        assert reason.phase == Phase.GENERATING
        assert reason.is_force_generated is True
        assert len(reason.inference_notes) > 0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _attach_lessons
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestAttachLessons:
    """lessons_learned 첨부."""

    def test_attaches_to_last_dead_end(self):
        """lessons_learned가 마지막 dead_end에 첨부된다."""
        reason = _reason(
            dead_ends=[
                DeadEnd(
                    hypothesis_id="H1",
                    reason="실패",
                    failure_type=FailureType.NO_TABLE,
                ),
            ],
        )
        plan = RecoveryPlan(
            action="give_up",
            lessons_learned="이 교훈 기록",
        )
        _attach_lessons(reason, plan)
        assert reason.dead_ends[-1].lessons_learned == "이 교훈 기록"

    def test_no_dead_ends_noop(self):
        """dead_ends가 없으면 아무 일도 안 한다."""
        reason = _reason()
        plan = RecoveryPlan(lessons_learned="교훈")
        _attach_lessons(reason, plan)  # 에러 없이 완료

    def test_none_plan_noop(self):
        """plan=None이면 아무 일도 안 한다."""
        reason = _reason(
            dead_ends=[
                DeadEnd(
                    hypothesis_id="H1",
                    reason="r",
                    failure_type=FailureType.NO_TABLE,
                ),
            ],
        )
        _attach_lessons(reason, None)
        assert reason.dead_ends[-1].lessons_learned == ""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# RecoveryPlan 모델
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestRecoveryPlan:
    """RecoveryPlan 모델 기본값 검증."""

    def test_default_values(self):
        """기본값: action=give_up, 빈 plan."""
        plan = RecoveryPlan()
        assert plan.action == "give_up"
        assert plan.execution_plan == []
        assert plan.new_hypothesis is None

    def test_with_execution_plan(self):
        """execution_plan 설정."""
        from src.agents.state.state import ExecutionStep
        plan = RecoveryPlan(
            action="replan",
            execution_plan=[
                ExecutionStep(
                    step=1,
                    tool="search_table_meta",
                    input="고객",
                    purpose="검색",
                ),
            ],
        )
        assert plan.action == "replan"
        assert len(plan.execution_plan) == 1


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _build_failure_summary
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestBuildFailureSummary:
    """실패 요약 생성."""

    def test_includes_dead_ends_and_unresolved(self):
        """실패 요약에 dead_ends와 미해소 용어 포함."""
        reason = _reason(
            dead_ends=[
                DeadEnd(
                    hypothesis_id="H1",
                    reason="no table",
                    failure_type=FailureType.NO_TABLE,
                    lessons_learned="교훈 1",
                ),
            ],
            knowledge_items=[
                _ki("term:미해소", "K1", ConfidenceStatus.UNRESOLVED),
            ],
            loop_guard=LoopGuard(replan_count=2),
        )
        summary = _build_failure_summary(reason)
        assert "재계획" in summary
        assert "실패 경로" in summary
        assert "미해소" in summary
        assert "교훈" in summary

    def test_empty_state_summary(self):
        """빈 상태에서도 요약 생성."""
        reason = _reason()
        summary = _build_failure_summary(reason)
        assert "도구 호출" in summary


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ASK_USER 기준 변경 (should_ask_user) — confidence_scorer 서비스
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestShouldAskUser:
    """선 추론 후 표시 정책 — ASK_USER 기준 테스트."""

    def test_simple_conflict_no_ask(self):
        """단순 용어 모호성 (단일 테이블) → ASK_USER 아님."""
        from src.services.confidence_scorer import should_ask_user
        reason = _reason(
            knowledge_items=[
                KnowledgeItem(
                    key="예금신규",
                    knowledge_id="K1",
                    status=ConfidenceStatus.CONFLICTED,
                    is_critical=True,
                    evidence=["TB_A.DEPOSIT_AMT 또는 건수일 수 있음"],
                ),
            ],
        )
        assert should_ask_user(reason) is False

    def test_multi_table_conflict_ask(self):
        """서로 다른 테이블 충돌 → ASK_USER."""
        from src.services.confidence_scorer import should_ask_user
        reason = _reason(
            knowledge_items=[
                KnowledgeItem(
                    key="여신잔액",
                    knowledge_id="K1",
                    status=ConfidenceStatus.CONFLICTED,
                    is_critical=True,
                    evidence=[
                        "TB_LOAN_BAL 기준 잔액",
                        "TB_CREDIT_LINE 기준 한도액",
                    ],
                ),
            ],
        )
        assert should_ask_user(reason) is True

    def test_non_critical_conflict_no_ask(self):
        """non-critical 항목 충돌 → ASK_USER 아님."""
        from src.services.confidence_scorer import should_ask_user
        reason = _reason(
            knowledge_items=[
                KnowledgeItem(
                    key="x",
                    knowledge_id="K1",
                    status=ConfidenceStatus.CONFLICTED,
                    is_critical=False,
                    evidence=["TB_A 기준", "TB_B 기준"],
                ),
            ],
        )
        assert should_ask_user(reason) is False
