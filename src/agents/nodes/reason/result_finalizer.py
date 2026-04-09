"""result_finalizer 노드 — 성공/실패 최종 출력 구성.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

reason 계층의 마지막 노드. 3가지 분기로 최종 출력을 구성한다:
    1. VERIFYING (CONFLICTED 해소) → 명확화 질문 생성, 대기 상태로 전환
    2. 성공 (validated_sql 존재) → 탐색 요약 기록
    3. 실패 → dead_ends 기반 실패 상세 기록, QueryStatus.ERROR 설정

핵심 함수:
    - result_finalizer_node: 메인 노드 함수
    - _build_success_summary: 성공 시 탐색 과정 요약 문자열
    - _build_failure_output: 실패 시 dead_ends 기반 상세 정보
    - _build_conflicted_signals: CONFLICTED 항목 → 명확화 AmbiguitySignal 생성
"""

from __future__ import annotations

from typing import Any

from src.agents.models.clarification import (
    AmbiguitySignal,
    AmbiguityType,
    ConfidenceLevel,
    QuestionType,
)
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
    VERIFYING(CONFLICTED 해소 필요) → 명확화 질문 생성 후 대기,
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

    # ── T5: CONFLICTED → AmbiguitySignal 생성 ──
    # 전략 §2.3 T5: CONFLICTED 항목을 AmbiguitySignal로 변환하여
    # clarification_handler 통합 경로로 처리
    if state.reason.phase == Phase.VERIFYING:
        conflicted = [
            ki for ki in reason.knowledge_items
            if ki.status == ConfidenceStatus.CONFLICTED
        ]
        if conflicted:
            signals = _build_conflicted_signals(
                conflicted,
            )
            reason.final_status = FinalStatus.PENDING
            updates["reason"] = reason
            updates["pending_signals"] = signals
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
            updates["resolved_signals"] = assumption_signals

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
    """실패 시 dead_ends와 미해소 용어를 포함한 상세 정보를 조립한다."""
    parts: list[str] = ["SQL 생성 실패"]

    if reason.dead_ends:
        parts.append("시도한 접근 방식:")
        for de in reason.dead_ends:
            parts.append(f"  - [{de.failure_type}] {de.reason}")

    unresolved = reason.get_unresolved_knowledge()
    if unresolved:
        terms = ", ".join(ki.key for ki in unresolved)
        parts.append(f"확인하지 못한 정보: {terms}")

    if reason.generated_sql and not reason.validated_sql:
        parts.append(
            "부분 SQL (미검증): "
            f"{reason.generated_sql[:100]}...",
        )

    return "\n".join(parts)


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


def _build_conflicted_signals(
    conflicted_items: list,
) -> list[AmbiguitySignal]:
    """CONFLICTED 항목을 AmbiguitySignal 리스트로 변환한다.

    output_scope CONFLICTED는 SINGLE_SELECT (선택지 제시),
    나머지는 FREE_TEXT로 사용자 확인을 요청한다.
    """
    signals: list[AmbiguitySignal] = []

    for ki in conflicted_items:
        if ki.key == "output_scope":
            signals.append(AmbiguitySignal(
                source_node="result_finalizer",
                ambiguity_type=AmbiguityType.INTENT,
                decision="ASK",
                confidence=ConfidenceLevel.LOW,
                question=(
                    "어떤 데이터를 뽑아야 할지 "
                    "좀 더 구체적으로 알려주시겠어요?"
                ),
                question_type=QuestionType.SINGLE_SELECT,
                options=[
                    "기본 정보 목록 (번호, 이름 등)",
                    "집계/요약 (건수, 합계, 평균 등)",
                    "상세 내역 (거래일자, 금액 등)",
                ],
                reasoning="출력 범위가 불명확합니다",
            ))
        else:
            evidence_text = ", ".join(ki.evidence)
            signals.append(AmbiguitySignal(
                source_node="result_finalizer",
                ambiguity_type=AmbiguityType.CONFLICT,
                decision="ASK",
                confidence=ConfidenceLevel.LOW,
                question=(
                    f"'{ki.key}' 항목에 상충되는 "
                    f"정보가 있습니다: {evidence_text}"
                    "\n어떤 정보가 맞는지 "
                    "확인해 주시겠어요?"
                ),
                options=ki.evidence if ki.evidence else [],
                question_type=(
                    QuestionType.SINGLE_SELECT
                    if ki.evidence
                    else QuestionType.FREE_TEXT
                ),
                reasoning=(
                    f"{ki.key} 항목의 정보가 "
                    "상충됩니다"
                ),
            ))

    return signals
