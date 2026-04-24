"""REGENERATE 경로 non-local_fix 실패 차단 가드 단위 테스트 (Phase 3 §14.3.5).

테스트 대상:
    - REGENERATE × {STRUCTURAL, EMPTY_RESULT, DB_ERROR, SQL_SEMANTIC_GLOBAL}
      → `conclude_failure` 직행
    - REGENERATE × {None, SQL_SYNTAX, SQL_SEMANTIC_LOCAL} → 기존 루프 분기 유지
    - REFINE / REDISPLAY / ANALYZE / NEW(None) 는 본 가드 영향 없음

테스트 대상 소스:
    src/agents/graph/pipeline.py::_route_after_sql_validator
"""

from __future__ import annotations

import pytest

from src.agents.graph.pipeline import _route_after_sql_validator
from src.agents.state.state import (
    ContinueRoute,
    FailureType,
    PipelineState,
)


def _make_state(
    *,
    route: ContinueRoute | None,
    failure_type: FailureType | None,
) -> PipelineState:
    """테스트용 최소 PipelineState. loop_guard 는 기본값(0회)."""
    state = PipelineState(
        session_id="s1",
        user_message="테스트",
        route=route,
    )
    state.reason.failure_type = failure_type
    return state


# ──────────────────────────────────────────────────────────────────
# REGENERATE × non-local_fix → conclude_failure 직행
# ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "non_local_ft",
    [
        FailureType.SQL_STRUCTURAL,
        FailureType.EMPTY_RESULT,
        FailureType.DB_ERROR,
        FailureType.NO_KNOWLEDGE,
        FailureType.NO_TABLE,
        FailureType.TERM_UNRESOLVABLE,
        FailureType.GENERATION_FAILED,
    ],
)
def test_regenerate_blocks_non_local_fix(non_local_ft: FailureType) -> None:
    state = _make_state(
        route=ContinueRoute.REGENERATE, failure_type=non_local_ft,
    )
    assert _route_after_sql_validator(state) == "conclude_failure"


# ──────────────────────────────────────────────────────────────────
# REGENERATE × local_fix 가능 실패 → 기존 루프 (fix_*)
# ──────────────────────────────────────────────────────────────────

def test_regenerate_syntax_falls_through_to_fix_syntax() -> None:
    state = _make_state(
        route=ContinueRoute.REGENERATE,
        failure_type=FailureType.SQL_SYNTAX,
    )
    assert _route_after_sql_validator(state) == "fix_syntax"


def test_regenerate_semantic_local_falls_through_to_fix_local() -> None:
    state = _make_state(
        route=ContinueRoute.REGENERATE,
        failure_type=FailureType.SQL_SEMANTIC_LOCAL,
    )
    assert _route_after_sql_validator(state) == "fix_local"


def test_regenerate_pass_reaches_conclude_success() -> None:
    state = _make_state(
        route=ContinueRoute.REGENERATE, failure_type=None,
    )
    assert _route_after_sql_validator(state) == "conclude_success"


# ──────────────────────────────────────────────────────────────────
# 다른 route 는 가드 영향 없음
# ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "other_route",
    [
        ContinueRoute.REFINE,
        ContinueRoute.REDISPLAY,
        ContinueRoute.ANALYZE,
        None,  # NEW 턴
    ],
)
def test_other_routes_still_replan_on_structural(
    other_route: ContinueRoute | None,
) -> None:
    state = _make_state(
        route=other_route,
        failure_type=FailureType.SQL_STRUCTURAL,
    )
    # 가드 미적용 — 기존 로직대로 replan (→ recovery_agent 로 진입).
    assert _route_after_sql_validator(state) == "replan"


@pytest.mark.parametrize(
    "other_route",
    [
        ContinueRoute.REFINE,
        ContinueRoute.REDISPLAY,
        ContinueRoute.ANALYZE,
        None,
    ],
)
def test_other_routes_still_replan_on_empty_result(
    other_route: ContinueRoute | None,
) -> None:
    state = _make_state(
        route=other_route,
        failure_type=FailureType.EMPTY_RESULT,
    )
    assert _route_after_sql_validator(state) == "replan"
