"""confidence_evaluator 노드 — 현재 지식 상태 평가 및 다음 행동 결정.

LLM 없이 순수 rule-based로 동작한다.
실질적 판단은 confidence_scorer.evaluate_readiness()에 위임하여
context_explorer 조기 탈출과 라우팅 로직의 단일 진실 공급원을 보장한다.
"""

from __future__ import annotations

from src.agents.state.state import PipelineState
from src.services.confidence_scorer import (
    VERDICT_TO_PHASE,
    calculate_readiness,
    evaluate_readiness,
)
from src.utils.logger import get_logger
from src.utils.tracker import get_current_tracker

logger = get_logger(__name__)


async def confidence_evaluator_node(state: PipelineState) -> dict:
    """현재 누적 지식 상태를 평가한다.

    실질적 판단은 evaluate_readiness()에 위임하고,
    이 노드는 판정 결과를 phase로 매핑하는 역할만 수행한다.
    """
    reason = state.reason.model_copy(deep=True)
    score = calculate_readiness(reason)
    verdict = evaluate_readiness(reason)

    # 강제 생성: 2회 이상 replan 후에도 score 40% 이상이면
    # 더 탐색해도 개선되지 않으므로 SQL 생성을 시도한다
    if (
        verdict.value in ("replan", "conclude_failure")
        and reason.loop_guard.replan_count >= 2
        and score >= 0.40
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
        s for s in reason.execution_plan if s.status == "PENDING"
    ])

    logger.info(
        "확신도 평가 완료",
        verdict=verdict.value,
        readiness_score=score,
        knowledge=f"{ki_confirmed}/{ki_total}",
        candidate_tables=ct_count,
        pending_steps=pending_steps,
    )

    tracker = get_current_tracker()
    if tracker and tracker.enabled:
        tracker.track_decision(
            node="reason_evaluate",
            decision_type="readiness_verdict",
            chosen=verdict.value,
            confidence=score,
            reason=(
                f"knowledge={ki_confirmed}/{ki_total}, "
                f"tables={ct_count}, "
                f"pending_steps={pending_steps}"
            ),
        )

    return {"reason": reason}
