"""confidence_evaluator 노드 — 현재 지식 상태 평가 및 다음 행동 결정.

LLM 없이 순수 rule-based로 동작한다.
실질적 판단은 confidence_scorer.evaluate_readiness()에 위임하여
context_explorer 조기 탈출과 라우팅 로직의 단일 진실 공급원을 보장한다.

판정 결과 → 다음 노드 매핑:
    EXPLORE   → context_explorer (추가 탐색)
    GENERATE  → sql_generator (SQL 생성)
    REPLAN    → recovery_planner (가설 교체)
    ASK_USER  → result_finalizer (사용자 확인)
    TERMINATE → result_finalizer (강제 종료)

강제 생성 로직:
    2회 이상 replan 후에도 score ≥ 40%이면 더 탐색해도 개선 가능성이 낮으므로
    REPLAN/TERMINATE 대신 GENERATE로 강제 전환하여 SQL 생성을 시도한다.

핵심 함수:
    - confidence_evaluator_node: state.reason을 평가하고 phase를 갱신

위임 구조:
    - 판정 로직: services/confidence_scorer.py (evaluate_readiness, calculate_readiness)
"""

from __future__ import annotations

from src.agents.state.state import (
    ConfidenceStatus,
    FailureType,
    Phase,
    PipelineState,
    StepStatus,
)
from src.services.confidence_scorer import (
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


async def confidence_evaluator_node(state: PipelineState) -> dict:
    """현재 누적 지식 상태를 평가한다.

    실질적 판단은 evaluate_readiness()에 위임하고,
    이 노드는 판정 결과를 phase로 매핑하는 역할만 수행한다.
    """
    reason = state.reason.model_copy(deep=True)
    score = calculate_readiness(reason)
    verdict = evaluate_readiness(reason)

    # 강제 생성: 2회 이상 replan 후에도 일정 점수 이상이면
    # 교착 상태로 판단하고 SQL 생성을 시도한다 (validator가 의미 검증 담당)
    if (
        verdict.value in ("replan", "conclude_failure")
        and reason.loop_guard.replan_count >= 2
        and score >= THRESHOLD_FORCE_GENERATE
    ):
        from src.services.confidence_scorer import (
            ReadinessVerdict,
        )
        verdict = ReadinessVerdict.GENERATE
        logger.info(
            "강제 생성 전환",
            replan_count=reason.loop_guard.replan_count,
            score=score,
        )

    reason.phase = VERDICT_TO_PHASE[verdict]

    # ── 추적: readiness 점수와 verdict ──
    ki_total = len(reason.knowledge_items)
    ki_confirmed = len([
        i for i in reason.knowledge_items if i.confidence >= 0.8
    ])
    ct_count = len(reason.candidate_tables)
    pending_steps = len([
        s for s in reason.execution_plan if s.status == StepStatus.PENDING
    ])
    unresolved = [
        ki.key for ki in reason.knowledge_items
        if ki.status in (ConfidenceStatus.UNRESOLVED, ConfidenceStatus.CONFLICTED)
    ]

    # ── REPLAN 판정 시 failure 맥락 설정 ──
    # 이전 노드(sql_validator 등)가 이미 구체적인 실패 정보를 설정한 경우 보존
    if reason.phase == Phase.REPLANNING and reason.failure_type is None:
        # 조건별 failure_type 분기
        if ct_count == 0:
            reason.failure_type = FailureType.NO_TABLE
        elif ki_total == 0:
            reason.failure_type = FailureType.NO_USE_CASE
        else:
            reason.failure_type = FailureType.TERM_UNRESOLVABLE
        parts = [
            "SQL 생성에 필요한 정보가 부족합니다.",
        ]
        if ki_confirmed == 0:
            parts.append(
                f"테이블·컬럼 매핑이 확인된 지식 항목이 "
                f"없습니다 (전체 {ki_total}건 중 확정 0건)"
            )
        else:
            parts.append(
                f"확정된 지식: {ki_confirmed}/{ki_total}건"
            )
        if ct_count == 0:
            parts.append(
                "후보 테이블이 0개로, "
                "데이터 소스를 특정하지 못했습니다"
            )
        else:
            parts.append(f"후보 테이블: {ct_count}개")
        if unresolved:
            parts.append(
                "미해소 용어: "
                + ", ".join(unresolved[:5])
            )
        parts.append(
            f"확신도 {score:.0%}로 생성 기준 미달"
        )
        reason.failure_reason = "\n- ".join(parts)

    logger.info(
        "확신도 평가 완료",
        verdict=verdict.value,
        readiness_score=score,
        knowledge=f"{ki_confirmed}/{ki_total}",
        candidate_tables=ct_count,
        pending_steps=pending_steps,
    )

    await dispatch_tracking_event(DECISION_READINESS, {
        "node": "confidence_evaluator",
        "decision_type": "readiness_verdict",
        "chosen": verdict.value,
        "confidence": score,
        "reason": (
            f"knowledge={ki_confirmed}/{ki_total}, "
            f"tables={ct_count}, "
            f"pending_steps={pending_steps}"
        ),
    })

    return {"reason": reason}
