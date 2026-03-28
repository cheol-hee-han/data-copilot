"""확신도(Confidence Score) 계산 및 행동 판정 모듈.

에이전트가 "SQL을 생성할 준비가 됐는가"를 판단하는 수치를 계산하고,
다음 행동을 결정하는 단일 판정 함수(evaluate_readiness)를 제공한다.

explore의 조기 탈출과 assess의 라우팅이 모두 evaluate_readiness()를 사용하여
판단 로직의 단일 진실 공급원(Single Source of Truth)을 보장한다.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prototype.agentic_state import AgenticCoreState

# ── 임계값 ──────────────────────────────────────────
THRESHOLD_GENERATE = 0.75   # 이상이면 SQL 생성 시도
THRESHOLD_REPLAN = 0.30     # 이하이면 가설 자체를 교체


class ReadinessVerdict(str, Enum):
    """다음 행동 판정 결과."""

    EXPLORE = "explore"
    GENERATE = "generate_sql"
    REPLAN = "replan"
    ASK_USER = "ask_user"
    TERMINATE = "conclude_failure"


# ── Phase 매핑 (assess 노드에서 사용) ──────────────
VERDICT_TO_PHASE: dict[ReadinessVerdict, str] = {
    ReadinessVerdict.GENERATE: "GENERATING",
    ReadinessVerdict.REPLAN: "REPLANNING",
    ReadinessVerdict.EXPLORE: "EXPLORING",
    ReadinessVerdict.ASK_USER: "VERIFYING",
    ReadinessVerdict.TERMINATE: "DONE",
}


def evaluate_readiness(state: AgenticCoreState) -> ReadinessVerdict:
    """다음 행동을 판정한다 — 단일 진실 공급원(Single Source of Truth).

    explore의 조기 탈출과 assess의 라우팅이 모두 이 함수를 사용한다.
    판단 우선순위:
      1. 루프 가드 초과 → TERMINATE
      2. CONFLICTED 항목 → ASK_USER
      3. 충분한 확신 → GENERATE
      4. 탐색 스텝 남음 → EXPLORE
      5. 확신 부족 또는 가설 실패 → REPLAN
    """
    from prototype.agentic_state import should_terminate

    # 1. 강제 종료
    if should_terminate(state):
        return ReadinessVerdict.TERMINATE

    # 2. CONFLICTED → 사용자 확인
    if has_conflicted_items(state):
        return ReadinessVerdict.ASK_USER

    # 3. 충분한 확신 → SQL 생성
    score = calculate_readiness(state)
    if score >= THRESHOLD_GENERATE and all_critical_confirmed(state):
        return ReadinessVerdict.GENERATE

    # 4. 탐색 스텝 남음 → 탐색 계속
    remaining = [s for s in state.execution_plan if s.status == "PENDING"]
    if remaining:
        return ReadinessVerdict.EXPLORE

    # 5. 가설 실패 또는 확신 부족 → 재계획
    return ReadinessVerdict.REPLAN


def calculate_readiness(state: AgenticCoreState) -> float:
    """SQL 생성 준비도를 0.0~1.0으로 계산한다.

    3차원 가중 평균 (옵션 C):
      1. term_resolution (50%) — knowledge_items의 CONFIRMED/PROBABLE 비율
         용어 매핑, 테이블 적합성 판단을 모두 포함 (단일 진실 공급원)
      2. use_case_match  (30%) — 유사 SQL 활용사례 유사도
      3. join_path       (20%) — 다중 테이블 시 조인 경로 확인 여부
    """
    scores: list[tuple[str, float, float]] = []

    # 1. 용어 해소율 (테이블 판단 포함) — 가중치 최대
    items = state.knowledge_items
    if items:
        resolved = [i for i in items if i.confidence >= 0.8]
        term_score = len(resolved) / len(items)
    else:
        term_score = 0.5  # 용어가 없으면 중립 (단순 질의)
    scores.append(("term_resolution", term_score, 0.5))

    # 2. 활용사례 유사도
    use_cases = state.explored_use_cases
    if use_cases:
        case_score = max(
            (uc.get("similarity", 0.0) for uc in use_cases),
            default=0.0,
        )
    else:
        case_score = 0.0
    scores.append(("use_case_match", case_score, 0.3))

    # 3. 조인 경로 확인
    # knowledge_items에서 table: 키가 2개 이상 CONFIRMED면 조인 필요
    confirmed_tables = [
        ki for ki in items
        if ki.key.startswith("table:") and ki.confidence >= 0.8
    ]
    needs_join = len(confirmed_tables) > 1
    if needs_join:
        join_score = 1.0 if state.confirmed_join_path else 0.0
    else:
        join_score = 1.0  # 단일 테이블이면 조인 불필요
    scores.append(("join_path", join_score, 0.2))

    total = sum(score * weight for _, score, weight in scores)
    return round(total, 3)


def all_critical_confirmed(state: AgenticCoreState) -> bool:
    """모든 critical 지식 항목이 CONFIRMED/PROBABLE 상태인지 확인.

    is_critical=True인 항목만 검사한다 (C-26 반영).
    is_critical=False인 보조 항목(지점명 변환용 테이블 등)은
    미해소여도 SQL 생성을 차단하지 않는다.
    is_critical은 context_explorer의 LLM이 도구 결과 해석 시 결정한다.
    """
    unresolved_critical = [
        ki for ki in state.knowledge_items
        if ki.is_critical
        and ki.status in ("UNRESOLVED", "CANDIDATE", "CONFLICTED")
    ]
    return len(unresolved_critical) == 0


def has_conflicted_items(state: AgenticCoreState) -> bool:
    """CONFLICTED 상태인 지식 항목이 있는지 확인."""
    return any(ki.status == "CONFLICTED" for ki in state.knowledge_items)
