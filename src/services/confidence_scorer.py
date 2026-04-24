"""확신도(Confidence Score) 계산 및 행동 판정 모듈.

작성자: 한철희 / 최종수정: 2026-04-16

에이전트가 "SQL을 생성할 준비가 됐는가"를 판단하는 수치를 계산하고,
다음 행동을 결정하는 단일 판정 함수(evaluate_readiness)를 제공한다.

explore의 조기 탈출과 assess의 라우팅이 모두 evaluate_readiness()를 사용하여
판단 로직의 단일 진실 공급원(Single Source of Truth)을 보장한다.

스코어링 구조 (2차원 가중 평균):
    1. term_resolution (70%) — critical 지식 항목의 CONFIRMED/PROBABLE 비율
    2. use_case_match  (30%) — LLM이 관련성 판정한 유사 SQL 활용사례 보유 건수

    스코어 ≥ 0.65 AND all_critical_confirmed → SQL 생성 진입.
    all_critical_confirmed가 True이면 term_score=1.0이므로 최소 0.70이 보장되어
    임계값(0.65)은 사실상 use_case가 없어도 통과한다.
    use_case_match는 "참고 SQL이 있으면 품질이 올라간다"는 보조 지표이다.

핵심 함수:
    - evaluate_readiness: 다음 행동 판정 (SSOT)
    - calculate_readiness: 0.0~1.0 준비도 점수 계산
    - all_critical_confirmed: critical 항목 전체 해소 여부 확인
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from src.models.enums import ConfidenceStatus, Phase, StepStatus

if TYPE_CHECKING:
    from src.agents.state.state import KnowledgeItem, ReasoningState

# ── 임계값 ──────────────────────────────────────────
THRESHOLD_GENERATE = 0.65       # 이상이면 SQL 생성 시도
THRESHOLD_FORCE_GENERATE = 0.55 # 교착 시 강제 생성 최소 임계값


# ── 상태 승격 순서 (recovery_agent에서 사용) ──
PROMOTION_ORDER: dict[ConfidenceStatus, int] = {
    ConfidenceStatus.UNRESOLVED: 0,
    ConfidenceStatus.CANDIDATE: 1,
    ConfidenceStatus.PROBABLE: 2,
    ConfidenceStatus.CONFIRMED: 3,
    ConfidenceStatus.CONFLICTED: 4,
}


class ReadinessVerdict(str, Enum):
    """다음 행동 판정 결과."""

    EXPLORE = "explore"
    GENERATE = "generate_sql"
    REPLAN = "replan"
    TERMINATE = "conclude_failure"


# ── Phase 매핑 (assess 노드에서 사용) ──────────────
VERDICT_TO_PHASE: dict[ReadinessVerdict, Phase] = {
    ReadinessVerdict.GENERATE: Phase.GENERATING,
    ReadinessVerdict.REPLAN: Phase.REPLANNING,
    ReadinessVerdict.EXPLORE: Phase.EXPLORING,
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
      4. 확신 부족 또는 가설 실패 → REPLAN
    """
    from src.agents.state.state import should_terminate

    # 1. 강제 종료
    if should_terminate(reason):
        return ReadinessVerdict.TERMINATE

    # 2. 충분한 확신 → SQL 생성
    score = calculate_readiness(reason)
    if score >= THRESHOLD_GENERATE and all_critical_confirmed(reason):
        # 탐색된 테이블이 없으면 점수만 높아도 SQL 생성 불가 → 재계획
        if not reason.explored_tables:
            return ReadinessVerdict.REPLAN
        return ReadinessVerdict.GENERATE

    # 3. 탐색 스텝 남음 → 탐색 계속
    remaining = [
        s for s in reason.execution_plan
        if s.status == StepStatus.PENDING
    ]
    if remaining:
        return ReadinessVerdict.EXPLORE

    # 4. 확신 부족 또는 가설 실패 → 재계획
    # CONFLICTED 항목도 여기로 흘러서 recovery_agent가 탐색/명확화로 해소한다.
    return ReadinessVerdict.REPLAN


def calculate_readiness(
    reason: ReasoningState,
) -> float:
    """SQL 생성 준비도를 0.0~1.0으로 계산한다.

    2차원 가중 평균:
      1. term_resolution (70%) — knowledge_items의
         CONFIRMED/PROBABLE 비율
      2. use_case_match  (30%) — 관련 유사 SQL 활용사례 보유 여부

    테이블 존재 여부는 all_critical_confirmed()에서 간접 보장한다.
    """
    scores: list[tuple[str, float, float]] = []

    # 1. 용어 해소율 (70%) — is_critical 항목만 대상
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
        term_score = 0.0
    scores.append(("term_resolution", term_score, 0.70))

    # 2. 유사 SQL 활용사례 (30%)
    # LLM이 관련성 판정한(relevant=True) 활용사례 건수 기반
    use_cases = reason.explored_use_cases
    if use_cases:
        relevant_cnt = sum(
            1 for uc in use_cases
            if uc.relevant
        )
        uc_score = min(relevant_cnt / 3, 1.0)
    else:
        uc_score = 0.0
    scores.append(("use_case_match", uc_score, 0.30))

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


