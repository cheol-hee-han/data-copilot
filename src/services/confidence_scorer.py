"""확신도(Confidence Score) 계산 및 행동 판정 모듈.

에이전트가 "SQL을 생성할 준비가 됐는가"를 판단하는 수치를 계산하고,
다음 행동을 결정하는 단일 판정 함수(evaluate_readiness)를 제공한다.

explore의 조기 탈출과 assess의 라우팅이 모두 evaluate_readiness()를 사용하여
판단 로직의 단일 진실 공급원(Single Source of Truth)을 보장한다.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from src.models.enums import ConfidenceStatus, Phase, StepStatus

if TYPE_CHECKING:
    from src.agents.state.state import ReasoningState

# ── 임계값 ──────────────────────────────────────────
THRESHOLD_GENERATE = 0.65       # 이상이면 SQL 생성 시도
THRESHOLD_FORCE_GENERATE = 0.55 # 교착 시 강제 생성 최소 임계값


class ReadinessVerdict(str, Enum):
    """다음 행동 판정 결과."""

    EXPLORE = "explore"
    GENERATE = "generate_sql"
    REPLAN = "replan"
    ASK_USER = "ask_user"
    TERMINATE = "conclude_failure"


# ── Phase 매핑 (assess 노드에서 사용) ──────────────
VERDICT_TO_PHASE: dict[ReadinessVerdict, Phase] = {
    ReadinessVerdict.GENERATE: Phase.GENERATING,
    ReadinessVerdict.REPLAN: Phase.REPLANNING,
    ReadinessVerdict.EXPLORE: Phase.EXPLORING,
    ReadinessVerdict.ASK_USER: Phase.VERIFYING,
    ReadinessVerdict.TERMINATE: Phase.DONE,
}


def evaluate_readiness(
    reason: ReasoningState,
) -> ReadinessVerdict:
    """다음 행동을 판정한다 — 단일 진실 공급원(SSOT).

    explore의 조기 탈출과 assess의 라우팅이 모두 이 함수를 사용한다.
    판단 우선순위:
      1. 루프 가드 초과 → TERMINATE
      2. 충분한 확신 → GENERATE
      3. 탐색 스텝 남음 → EXPLORE
      4. CONFLICTED 항목 → ASK_USER (탐색 완료 후 판단)
      5. 확신 부족 또는 가설 실패 → REPLAN
    """
    from src.agents.state.state import should_terminate

    # 1. 강제 종료
    if should_terminate(reason):
        return ReadinessVerdict.TERMINATE

    # 2. 충분한 확신 → SQL 생성
    score = calculate_readiness(reason)
    if score >= THRESHOLD_GENERATE and all_critical_confirmed(reason):
        return ReadinessVerdict.GENERATE

    # 3. 탐색 스텝 남음 → 탐색 계속
    remaining = [
        s for s in reason.execution_plan
        if s.status == StepStatus.PENDING
    ]
    if remaining:
        return ReadinessVerdict.EXPLORE

    # 4. CONFLICTED → 사용자 확인 (탐색이 충돌을 해소할 수 있으므로 탐색 후 판단)
    if has_conflicted_items(reason):
        return ReadinessVerdict.ASK_USER

    # 5. 가설 실패 또는 확신 부족 → 재계획
    return ReadinessVerdict.REPLAN


def calculate_readiness(
    reason: ReasoningState,
) -> float:
    """SQL 생성 준비도를 0.0~1.0으로 계산한다.

    3차원 가중 평균 (옵션 C):
      1. term_resolution (50%) — knowledge_items의
         CONFIRMED/PROBABLE 비율
      2. use_case_match  (30%) — 유사 SQL 활용사례 유사도
      3. join_path       (20%) — 다중 테이블 시 조인 경로
         확인 여부
    """
    scores: list[tuple[str, float, float]] = []

    # 1. 용어 해소율 (55%) — is_critical 항목만 대상
    #    status 기반 판정 (all_critical_confirmed과 동일 기준)
    items = [ki for ki in reason.knowledge_items if ki.is_critical]
    if items:
        resolved = [
            i for i in items
            if i.status in (
                ConfidenceStatus.CONFIRMED,
                ConfidenceStatus.PROBABLE,
            )
        ]
        term_score = len(resolved) / len(items)
    else:
        term_score = 0.5
    scores.append(("term_resolution", term_score, 0.55))

    # 2. 테이블 커버리지 (25%)
    candidates = reason.candidate_tables
    if candidates:
        with_desc = [
            c for c in candidates if c.description
        ]
        table_score = (
            len(with_desc) / len(candidates)
        )
    else:
        table_score = 0.0
    scores.append(("table_coverage", table_score, 0.25))

    # 3. 조인 가능성 확인 (20%)
    # 다중 테이블 시, 공통 join_keys가 있으면 조인 가능으로 판단
    needs_join = len(candidates) > 1
    if needs_join:
        all_keys = [
            set(ct.join_keys) for ct in candidates
            if ct.join_keys
        ]
        has_common_key = any(
            a & b for i, a in enumerate(all_keys)
            for b in all_keys[i + 1:]
        )
        join_score = 1.0 if has_common_key else 0.3
    else:
        join_score = 1.0
    scores.append(("join_path", join_score, 0.20))

    total = sum(
        score * weight for _, score, weight in scores
    )
    return round(total, 3)


def all_critical_confirmed(
    reason: ReasoningState,
) -> bool:
    """모든 critical 지식 항목이 CONFIRMED/PROBABLE인지 확인.

    is_critical=True인 항목만 검사한다 (C-26 반영).
    """
    unresolved_critical = [
        ki for ki in reason.knowledge_items
        if ki.is_critical
        and ki.status in (
            ConfidenceStatus.UNRESOLVED,
            ConfidenceStatus.CANDIDATE,
            ConfidenceStatus.CONFLICTED,
        )
    ]
    return len(unresolved_critical) == 0


def has_conflicted_items(
    reason: ReasoningState,
) -> bool:
    """CONFLICTED 상태인 지식 항목이 있는지 확인."""
    return any(
        ki.status == ConfidenceStatus.CONFLICTED
        for ki in reason.knowledge_items
    )
