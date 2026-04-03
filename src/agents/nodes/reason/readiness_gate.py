"""readiness_gate 노드 — 현재 지식 상태 평가 및 다음 행동 결정.

LLM 없이 순수 rule-based로 동작한다.
실질적 판단은 confidence_scorer.evaluate_readiness()에 위임하여
knowledge_fetcher 조기 탈출과 라우팅 로직의 단일 진실 공급원을 보장한다.

판정 결과 → Phase → 다음 노드 매핑:
    EXPLORE   → EXPLORING   → knowledge_fetcher (추가 탐색)
    GENERATE  → GENERATING  → sql_generator (SQL 생성)
    REPLAN    → REPLANNING  → recovery_agent (ReAct 복구 루프)
    ASK_USER  → VERIFYING   → result_finalizer (사용자 확인)
    TERMINATE → DONE        → result_finalizer (강제 종료)

라우팅 함수(pipeline.py)는 reason.phase만 읽어서 다음 노드를 결정한다.
모든 state mutation(phase, exploration_phase, recovery_entry_source)은
이 노드에서 완결한다.

강제 생성 로직:
    2회 이상 replan 후에도 score ≥ 40%이면 더 탐색해도 개선 가능성이 낮으므로
    REPLAN/TERMINATE 대신 GENERATE로 강제 전환하여 SQL 생성을 시도한다.

핵심 함수:
    - readiness_gate_node: state.reason을 평가하고 phase를 갱신

위임 구조:
    - 판정 로직: services/confidence_scorer.py (evaluate_readiness, calculate_readiness)
"""

from __future__ import annotations

from src.agents.state.state import (
    ConfidenceStatus,
    FailureType,
    Phase,
    PipelineState,
    ReasoningState,
    StepStatus,
)
from src.services.confidence_scorer import (
    ReadinessVerdict,
    THRESHOLD_FORCE_GENERATE,
    VERDICT_TO_PHASE,
    calculate_readiness,
    evaluate_readiness,
)
from src.utils.logger import get_logger
from src.utils.tracker.dispatch import (
    dispatch_tracking_event,
    DECISION_READINESS,
)

logger = get_logger(__name__)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 메인 노드
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def readiness_gate_node(state: PipelineState) -> dict:
    """현재 누적 지식 상태를 평가하고 phase를 확정한다.

    실질적 판단은 evaluate_readiness()에 위임하고,
    이 노드에서 강제 생성·EXPLORE 분기·REPLAN state 설정을 모두 처리한다.
    라우팅 함수(pipeline.py)는 reason.phase만 읽어서 다음 노드를 결정한다.
    """
    reason = state.reason.model_copy(deep=True)
    score = calculate_readiness(reason)
    verdict = _apply_force_generate(evaluate_readiness(reason), reason, score)

    # Phase 확정 + 분기별 state 설정
    reason.phase = VERDICT_TO_PHASE[verdict]
    reason.last_verdict = verdict.value
    _finalize_phase(reason, score)

    # 추적 이벤트
    stats = _collect_stats(reason)
    logger.info(
        "확신도 평가 완료",
        verdict=verdict.value,
        phase=reason.phase.value,
        readiness_score=score,
        **stats,
    )
    await dispatch_tracking_event(DECISION_READINESS, {
        "node": "readiness_gate",
        "decision_type": "readiness_verdict",
        "chosen": verdict.value,
        "confidence": score,
        "reason": (
            f"knowledge={stats['knowledge']}, "
            f"tables={stats['candidate_tables']}, "
            f"pending_steps={stats['pending_steps']}"
        ),
    })

    return {"reason": reason}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 판정 보조
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _apply_force_generate(
    verdict: ReadinessVerdict,
    reason: ReasoningState,
    score: float,
) -> ReadinessVerdict:
    """2회 이상 replan 후에도 일정 점수 이상이면 GENERATE로 강제 전환."""
    if (
        verdict.value in ("replan", "conclude_failure")
        and reason.loop_guard.replan_count >= 2
        and score >= THRESHOLD_FORCE_GENERATE
    ):
        logger.info(
            "강제 생성 전환",
            replan_count=reason.loop_guard.replan_count,
            score=score,
        )
        return ReadinessVerdict.GENERATE
    return verdict


def _finalize_phase(reason: ReasoningState, score: float) -> None:
    """phase별 후속 state 설정을 처리한다.

    - EXPLORING: PENDING 스텝이 없으면 REPLANNING으로 전환
    - REPLANNING: recovery 진입 state + failure 맥락 설정
    """
    # EXPLORE 분기: PENDING 스텝이 없으면 REPLANNING 전환
    if reason.phase == Phase.EXPLORING:
        has_pending = any(
            s.status == StepStatus.PENDING
            for s in reason.execution_plan
        )
        if not has_pending or reason.exploration_phase != "initial":
            reason.phase = Phase.REPLANNING

    # REPLANNING: recovery 진입 state 설정
    if reason.phase == Phase.REPLANNING:
        reason.exploration_phase = "recovery"
        reason.recovery_entry_source = "readiness_gate"
        if reason.failure_type is None:
            _set_failure_context(reason, score)


def _set_failure_context(
    reason: ReasoningState, score: float,
) -> None:
    """REPLAN 판정 시 failure_type과 failure_reason을 설정한다."""
    ki_total = len(reason.knowledge_items)
    ki_confirmed = len([
        i for i in reason.knowledge_items if i.confidence >= 0.8
    ])
    ct_count = len(reason.candidate_tables)
    unresolved = [
        ki.key for ki in reason.knowledge_items
        if ki.status in (
            ConfidenceStatus.UNRESOLVED,
            ConfidenceStatus.CONFLICTED,
        )
    ]

    # failure_type 결정
    if ct_count == 0:
        reason.failure_type = FailureType.NO_TABLE
    elif ki_total == 0:
        reason.failure_type = FailureType.NO_USE_CASE
    else:
        reason.failure_type = FailureType.TERM_UNRESOLVABLE

    # failure_reason 조립
    parts = ["SQL 생성에 필요한 정보가 부족합니다."]
    if ki_confirmed == 0:
        parts.append(
            f"테이블·컬럼 매핑이 확인된 지식 항목이 "
            f"없습니다 (전체 {ki_total}건 중 확정 0건)"
        )
    else:
        parts.append(f"확정된 지식: {ki_confirmed}/{ki_total}건")
    if ct_count == 0:
        parts.append(
            "후보 테이블이 0개로, "
            "데이터 소스를 특정하지 못했습니다"
        )
    else:
        parts.append(f"후보 테이블: {ct_count}개")
    if unresolved:
        parts.append("미해소 용어: " + ", ".join(unresolved[:5]))
    parts.append(f"확신도 {score:.0%}로 생성 기준 미달")
    reason.failure_reason = "\n- ".join(parts)


def _collect_stats(reason: ReasoningState) -> dict:
    """추적용 통계를 수집한다."""
    ki_total = len(reason.knowledge_items)
    ki_confirmed = len([
        i for i in reason.knowledge_items if i.confidence >= 0.8
    ])
    return {
        "knowledge": f"{ki_confirmed}/{ki_total}",
        "candidate_tables": len(reason.candidate_tables),
        "pending_steps": len([
            s for s in reason.execution_plan
            if s.status == StepStatus.PENDING
        ]),
    }
