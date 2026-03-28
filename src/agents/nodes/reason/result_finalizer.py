"""result_finalizer 노드 — 성공/실패 최종 출력 구성.

에이전틱 코어의 마지막 노드. 성공/실패에 따라 최종 출력을 구성하고
PipelineState의 최종 상태를 설정한다.

agentic_to_pipeline 변환 로직을 흡수하여 ContextInfo를 직접 구성한다.
"""

from __future__ import annotations

from typing import Any

from src.agents.state.state import (
    PipelineState,
    ReasoningState,
    ColumnMeta,
    ContextInfo,
    QueryStatus,
    TableMeta,
)


async def result_finalizer_node(state: PipelineState) -> dict:
    """성공/실패에 따라 최종 응답을 구성한다."""
    reason = state.reason.model_copy(deep=True)
    reason.phase = "DONE"
    updates: dict[str, Any] = {"reason": reason}

    # 사용자 확인 필요 (CONFLICTED 해소)
    if state.reason.phase == "VERIFYING":
        conflicted = [
            ki for ki in reason.knowledge_items
            if ki.status == "CONFLICTED"
        ]
        if conflicted:
            question = _build_clarification_question(
                conflicted,
            )
            reason.final_status = "pending"
            updates["reason"] = reason
            updates["awaiting_clarification"] = True
            updates["clarification_question"] = question
            return updates

    # SQL 검증 성공
    if reason.validated_sql:
        reason.final_status = "success"
        reason.exploration_summary = (
            _build_success_summary(reason)
        )
        updates["reason"] = reason

        # ContextInfo 구성 (agentic_to_pipeline 로직 흡수)
        context = _build_context_info(reason)
        updates["context"] = context
        return updates

    # 실패
    reason.final_status = "failure"
    reason.exploration_summary = _build_failure_output(
        reason,
    )
    updates["reason"] = reason
    updates["error_message"] = reason.exploration_summary
    updates["status"] = QueryStatus.ERROR
    return updates


def _build_context_info(
    reason: ReasoningState,
) -> ContextInfo:
    """reason에서 CONFIRMED 테이블을 ContextInfo로 변환한다.

    agentic_core.agentic_to_pipeline(lines 195-238) 로직을 흡수.
    """
    confirmed_table_names = {
        ki.key.removeprefix("table:")
        for ki in reason.knowledge_items
        if ki.key.startswith("table:")
        and ki.status == "CONFIRMED"
    }

    table_metas = [
        TableMeta(
            table_name=ct.table_name,
            table_description=ct.role,
            columns=[
                ColumnMeta(
                    column_name=col,
                    column_description="",
                    data_type="",
                    is_pii=False,
                )
                for col in ct.relevant_columns
            ],
        )
        for ct in reason.candidate_tables
        if ct.table_name in confirmed_table_names
    ]

    return ContextInfo(table_metas=table_metas)


def _build_success_summary(
    reason: ReasoningState,
) -> str:
    """성공 시 탐색 과정 요약."""
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
        and ki.status == "CONFIRMED"
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

    if not reason.structural_hints.is_empty():
        parts.append("sqlglot 구조적 힌트 활용")

    return " | ".join(parts)


def _build_failure_output(
    reason: ReasoningState,
) -> str:
    """실패 시 상세 정보."""
    parts: list[str] = ["SQL 생성 실패"]

    if reason.dead_ends:
        parts.append("시도한 접근 방식:")
        for de in reason.dead_ends:
            parts.append(f"  - {de.reason}")

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


def _build_clarification_question(
    conflicted_items: list,
) -> str:
    """CONFLICTED 항목에 대한 사용자 확인 질문을 생성한다.

    output_scope CONFLICTED는 별도 처리:
      "무엇을 뽑을지" 모호한 경우 구체적인 선택지를 제시한다.
    """
    # output_scope 모호 → 별도 선택지 질문
    output_items = [
        ki for ki in conflicted_items
        if ki.key == "output_scope"
    ]
    other_items = [
        ki for ki in conflicted_items
        if ki.key != "output_scope"
    ]

    parts: list[str] = []

    if output_items:
        parts.append(
            "요청하신 내용에서 어떤 데이터를 뽑아야 할지 "
            "조금 더 구체적으로 알려주시겠어요?\n\n"
            "예를 들어:\n"
            "  1) 기본 정보 목록 "
            "(번호, 이름, 등급, 상태 등)\n"
            "  2) 집계/요약 "
            "(건수, 합계, 평균 등)\n"
            "  3) 상세 내역 "
            "(거래일자, 금액, 유형 등)\n\n"
            "어떤 형태가 필요하신가요?"
        )

    if other_items:
        if parts:
            parts.append("\n---\n")
        parts.append(
            "추가로 다음 항목도 확인이 필요합니다:\n",
        )
        for i, ki in enumerate(other_items, 1):
            evidence_str = "\n".join(
                f"    - {e}" for e in ki.evidence
            )
            parts.append(
                f"{i}. **{ki.key}**\n"
                f"   상충 정보:\n{evidence_str}\n",
            )
        parts.append(
            "어떤 정보가 맞는지 확인해 주시겠어요?",
        )

    return "\n".join(parts)
