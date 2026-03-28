"""confidence_evaluator 노드 — 현재 지식 상태 평가 및 다음 행동 결정.

LLM 없이 순수 rule-based로 동작한다.
실질적 판단은 confidence_scorer.evaluate_readiness()에 위임하여
context_explorer 조기 탈출과 라우팅 로직의 단일 진실 공급원을 보장한다.
"""

from __future__ import annotations

from typing import Any

from prototype.agentic_state import AgenticCoreState
from prototype.confidence_scorer import (
    VERDICT_TO_PHASE,
    evaluate_readiness,
)


async def confidence_evaluator_node(state: AgenticCoreState) -> dict:
    """현재 누적 지식 상태를 평가한다.

    실질적 판단은 evaluate_readiness()에 위임하고,
    이 노드는 판정 결과를 phase로 매핑하는 역할만 수행한다.
    """
    verdict = evaluate_readiness(state)
    return {"phase": VERDICT_TO_PHASE[verdict]}
