"""readiness_gate 노드 — 현재 지식 상태 평가 및 다음 행동 결정.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

LLM 없이 순수 rule-based로 동작한다.
실질적 판단은 confidence_scorer.evaluate_readiness()에 위임하여
context_retriever 조기 탈출과 라우팅 로직의 단일 진실 공급원을 보장한다.

판정 결과 → Phase → 다음 노드 매핑:
    EXPLORE   → EXPLORING   → context_retriever (추가 탐색)
    GENERATE  → GENERATING  → sql_generator (SQL 생성)
    REPLAN    → REPLANNING  → recovery_agent (ReAct 복구 루프)
    ASK_USER  → VERIFYING   → result_finalizer (사용자 확인)
    TERMINATE → DONE        → result_finalizer (강제 종료)

라우팅 함수(pipeline.py)는 reason.phase만 읽어서 다음 노드를 결정한다.
모든 state mutation(phase, exploration_phase, recovery_entry_source)은
이 노드에서 완결한다.

강제 생성 로직:
    force_generate_after_replans(기본 2)회 이상 replan 후에도
    score ≥ THRESHOLD_FORCE_GENERATE이면 더 탐색해도 개선 가능성이 낮으므로
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
    SelectionStatus,
    StepStatus,
    TargetDbStatus,
)
from src.services.confidence_scorer import (
    ReadinessVerdict,
    THRESHOLD_FORCE_GENERATE,
    VERDICT_TO_PHASE,
    calculate_readiness,
    evaluate_readiness,
)
from src.services.target_db_resolver import resolve_target_db
from src.config import settings
from src.utils.logger import get_logger
from src.utils.tracker.dispatch import (
    dispatch_tracking_event,
    DECISION_READINESS,
    REASONING_STEP,
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

    # 가설에 준비도 판정 기록 (deep copy로 분리된 hypotheses 리스트도 동기화)
    if reason.current_hypothesis:
        hid = reason.current_hypothesis.hypothesis_id
        reason.current_hypothesis.readiness_score = score
        reason.current_hypothesis.readiness_verdict = verdict.value
        for i, h in enumerate(reason.hypotheses):
            if h.hypothesis_id == hid:
                reason.hypotheses[i].readiness_score = score
                reason.hypotheses[i].readiness_verdict = verdict.value
                break

    # Phase 확정 + 분기별 state 설정
    reason.phase = VERDICT_TO_PHASE[verdict]
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
            f"tables={stats['explored_tables']}, "
            f"pending_steps={stats['pending_steps']}"
        ),
    })

    # ── Reasoning Flow 트레이스 ──
    _phase_to_next = {
        Phase.EXPLORING: "context_retriever",
        Phase.GENERATING: "sql_generator",
        Phase.REPLANNING: "recovery_agent",
        Phase.DONE: "result_finalizer",
        Phase.VERIFYING: "result_finalizer",
    }
    _next = _phase_to_next.get(
        reason.phase, "recovery_agent",
    )
    _ft = reason.failure_type
    _fr = reason.failure_reason
    _output: dict = {
        "verdict": verdict.value,
        "score_breakdown": (
            f"knowledge={stats['knowledge']}, "
            f"tables={stats['explored_tables']}"
        ),
    }
    if _ft:
        _output["failure_type"] = str(_ft)
    if _fr:
        _output["failure_reason"] = _fr

    await dispatch_tracking_event(REASONING_STEP, {
        "node": "readiness_gate",
        "phase": "reason",
        "step_type": "rule_decision",
        "round": reason.loop_guard.replan_count,
        "hypothesis_id": (
            reason.current_hypothesis.hypothesis_id
            if reason.current_hypothesis else ""
        ),
        "inputs": {
            "readiness_score": score,
            "knowledge_status": stats["knowledge"],
            "table_status": (
                f"{stats['explored_tables']}건"
            ),
            "pending_steps": stats["pending_steps"],
            "replan_count": (
                reason.loop_guard.replan_count
            ),
        },
        "output": _output,
        "routing": {
            "next_node": _next,
            "reason": (
                f"readiness {score:.0%} → "
                f"{verdict.value}"
            ),
        },
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
    """N회 이상 replan 후에도 일정 점수 이상이면 GENERATE로 강제 전환."""
    if (
        verdict.value in ("replan", "conclude_failure")
        and reason.loop_guard.replan_count >= settings.force_generate_after_replans
        and score >= THRESHOLD_FORCE_GENERATE
        and reason.explored_tables
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
    - GENERATING: target_db 결정 (단일 진실원). NO_SELECTION 이면 REPLANNING 전환.
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

    # GENERATING 진입 시점에 target_db 단일 진실원 확정
    if reason.phase == Phase.GENERATING:
        decision = resolve_target_db(reason, settings)
        reason.target_db_decision = decision
        if decision.status in (
            TargetDbStatus.NO_SELECTION,
            TargetDbStatus.AMBIGUOUS,
        ):
            # SELECTED 테이블 부재 또는 복수 시스템 혼재 → SQL 생성 불가, REPLAN
            # AMBIGUOUS 는 "단일 시스템으로 선정 실패" 를 NO_TABLE 로 취급하여
            # recovery_agent 가 failure_reason 으로 원인을 surface 한다.
            reason.target_db = ""
            reason.phase = Phase.REPLANNING
            reason.failure_type = FailureType.NO_TABLE
            reason.failure_reason = decision.decision_rationale
            logger.info(
                "target_db 결정 실패 — REPLAN 전환",
                status=decision.status.value,
                rationale=decision.decision_rationale,
            )
        else:
            reason.target_db = decision.target
            logger.info(
                "target_db 결정 완료",
                status=decision.status.value,
                target=decision.target,
                chosen=decision.chosen_tables,
                dropped=[t for t, _ in decision.dropped_tables],
            )

    # REPLANNING: recovery 진입 state 설정
    if reason.phase == Phase.REPLANNING:
        reason.exploration_phase = "recovery"
        reason.recovery_entry_source = "readiness_gate"
        if reason.failure_type is None:
            _set_failure_context(reason, score)


def _set_failure_context(
    reason: ReasoningState, score: float,
) -> None:
    """REPLAN 판정 시 failure_type과 failure_reason을 설정한다.

    failure_type별로 recovery_agent가 활용할 수 있는 구체적인
    failure_reason을 조립한다.
    """
    ki_total = len(reason.knowledge_items)
    ki_confirmed = len([
        i for i in reason.knowledge_items if i.confidence >= 0.8
    ])
    explored_count = len(reason.explored_tables)
    selected_count = len([
        t for t in reason.explored_tables
        if t.selection_status == SelectionStatus.SELECTED
    ])
    unresolved = [
        ki.key for ki in reason.knowledge_items
        if ki.status in (
            ConfidenceStatus.UNRESOLVED,
            ConfidenceStatus.CONFLICTED,
        )
    ]

    # ── failure_type 결정 (SELECTED 기준) ──
    if selected_count == 0:
        reason.failure_type = FailureType.NO_TABLE
    elif ki_total == 0:
        reason.failure_type = FailureType.NO_KNOWLEDGE
    else:
        reason.failure_type = FailureType.TERM_UNRESOLVABLE

    # ── failure_reason 조립 (타입별 분기) ──
    if reason.failure_type == FailureType.NO_TABLE:
        if explored_count == 0:
            table_msg = (
                "후보 테이블이 0개로, "
                "데이터 소스를 특정하지 못했습니다."
            )
        else:
            table_msg = (
                f"탐색된 테이블 {explored_count}개가 "
                f"모두 부적합(REJECTED) 판정되어 "
                "사용 가능한 테이블이 없습니다."
            )
        parts = [
            "SQL 생성에 필요한 테이블이 확보되지 않았습니다.",
            table_msg,
        ]
        if unresolved:
            parts.append(
                "미해소 용어: " + ", ".join(unresolved[:5]),
            )
        parts.append(f"확신도 {score:.0%}로 생성 기준 미달")
        reason.failure_reason = "\n- ".join(parts)

    elif reason.failure_type == FailureType.NO_KNOWLEDGE:
        parts = [
            "질의 정규화(분해)에서 측정값·조건이 추출되지 않아 "
            "지식 항목이 생성되지 않았습니다.",
            "원본 질의를 다른 관점으로 재분해하거나, "
            "유사 SQL 이력을 참고하여 "
            "필요한 측정 항목을 파악해야 합니다.",
            f"후보 테이블: {selected_count}개 "
            f"(탐색 {explored_count}개)",
        ]
        parts.append(f"확신도 {score:.0%}로 생성 기준 미달")
        reason.failure_reason = "\n- ".join(parts)

    else:  # TERM_UNRESOLVABLE
        parts = ["SQL 생성에 필요한 정보가 부족합니다."]
        if ki_confirmed == 0:
            parts.append(
                f"테이블·컬럼 매핑이 확인된 지식 항목이 "
                f"없습니다 (전체 {ki_total}건 중 확정 0건)",
            )
        else:
            parts.append(
                f"확정된 지식: {ki_confirmed}/{ki_total}건",
            )
        parts.append(
            f"후보 테이블: {selected_count}개 "
            f"(탐색 {explored_count}개)",
        )
        if unresolved:
            parts.append(
                "미해소 용어: " + ", ".join(unresolved[:5]),
            )
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
        "explored_tables": len(reason.explored_tables),
        "pending_steps": len([
            s for s in reason.execution_plan
            if s.status == StepStatus.PENDING
        ]),
    }
