"""result_finalizer 노드 — 성공/실패 최종 출력 구성.

작성자: 한철희 / 최종수정: 2026-04-16

reason 계층의 마지막 노드. 성공/실패를 판정하고 최종 출력을 구성한다:
    1. 성공 (validated_sql 존재) → 탐색 요약 기록
    2. 실패 → dead_ends 기반 실패 상세 기록, QueryStatus.ERROR 설정

핵심 함수:
    - result_finalizer_node: 메인 노드 함수
    - _build_success_summary: 성공 시 탐색 과정 요약 문자열
    - _build_failure_output: 실패 시 dead_ends 기반 상세 정보
"""

from __future__ import annotations

from typing import Any

from src.agents.nodes.reason.sql_generator import (
    _build_assumption_signals,
)
from src.agents.state.state import (
    PipelineState,
    ReasoningState,
    ConfidenceStatus,
    FinalStatus,
    Phase,
    QueryStatus,
    SelectionStatus,
)


async def result_finalizer_node(state: PipelineState) -> dict:
    """성공/실패에 따라 최종 응답을 구성한다.

    CANCELLED → 부분 결과 포함 취소 메시지,
    validated_sql 존재 → 성공 요약, 나머지 → 실패 상세 기록.
    """
    reason = state.reason.model_copy(deep=True)
    reason.phase = Phase.DONE
    updates: dict[str, Any] = {"reason": reason}

    # ── CANCELLED: 노드 체크에서 설정된 경우 ──
    if state.status == QueryStatus.CANCELLED:
        reason.final_status = FinalStatus.CANCELLED
        reason.exploration_summary = _build_cancel_summary(reason)
        updates["reason"] = reason
        updates["formatted_response"] = reason.exploration_summary
        updates["error_message"] = reason.exploration_summary
        return updates

    # SQL 검증 성공
    if reason.validated_sql:
        reason.final_status = FinalStatus.SUCCESS
        reason.exploration_summary = (
            _build_success_summary(reason)
        )
        updates["reason"] = reason

        # pending_assumptions → AmbiguitySignal(INFER) 변환
        assumption_signals = _build_assumption_signals(
            reason.pending_assumptions,
            state.turn_id,
        )
        if assumption_signals:
            updates["resolved_signals"] = [
                *state.resolved_signals,
                *assumption_signals,
            ]

        return updates

    # 실패
    reason.final_status = FinalStatus.FAILURE
    # recovery_planner가 give_up_reason(LLM 총평)을 이미 기록한 경우 보존
    if not reason.exploration_summary:
        reason.exploration_summary = _build_failure_output(
            reason,
        )
    updates["reason"] = reason
    updates["error_message"] = reason.exploration_summary
    updates["status"] = QueryStatus.ERROR
    return updates


def _build_success_summary(
    reason: ReasoningState,
) -> str:
    """성공 시 탐색 과정을 요약한다."""
    parts: list[str] = []

    g = reason.loop_guard
    parts.append(
        f"도구 호출 {g.total_tool_calls}회, "
        f"SQL 생성 {g.generate_attempts}회",
    )
    if g.replan_count > 0:
        parts.append(f"재계획 {g.replan_count}회")

    confirmed_tables = [
        ki.key.removeprefix("table:")
        for ki in reason.knowledge_items
        if ki.key.startswith("table:")
        and ki.status == ConfidenceStatus.CONFIRMED
    ]
    if confirmed_tables:
        parts.append(
            f"사용 테이블: {', '.join(confirmed_tables)}",
        )

    if reason.explored_use_cases:
        parts.append(
            "참고 활용사례: "
            f"{len(reason.explored_use_cases)}건",
        )

    return " | ".join(parts)


def _build_failure_output(
    reason: ReasoningState,
) -> str:
    """실패 시 마지막 실패 원인만 간결하게 표시한다.

    상세 추론 과정은 프로세스 요약(전구 아이콘)에서 확인 가능하므로
    사용자 응답에는 최종 실패 사유 한 줄만 노출한다.
    """
    if reason.dead_ends:
        last_reason = reason.dead_ends[-1].reason.split("\n")[0].strip()
        if last_reason:
            return f"요청하신 데이터를 조회하지 못했습니다.\n({last_reason})"

    if reason.failure_reason:
        first_line = reason.failure_reason.split("\n")[0].strip()
        if first_line:
            return f"요청하신 데이터를 조회하지 못했습니다.\n({first_line})"

    return "요청하신 데이터를 조회하기 어렵습니다."


def _build_cancel_summary(
    reason: ReasoningState,
) -> str:
    """취소 시 부분 결과를 포함한 사용자 메시지."""
    parts: list[str] = ["요청이 중단되었습니다."]

    selected_tables = [
        t.table_name for t in reason.explored_tables
        if t.selection_status == SelectionStatus.SELECTED
    ]
    if selected_tables:
        parts.append(
            f"탐색한 테이블: {', '.join(selected_tables)}",
        )

    confirmed = reason.get_confirmed_knowledge()
    if confirmed:
        parts.append(
            f"확인된 정보 {len(confirmed)}건이 있습니다.",
        )

    return " ".join(parts)

