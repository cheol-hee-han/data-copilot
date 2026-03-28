"""result_finalizer 노드 — 성공/실패 최종 출력 구성 + 상태 역변환.

에이전틱 코어의 마지막 노드. 성공/실패에 따라 최종 출력을 구성하고
AgenticCoreState → PipelineState 역변환에 필요한 정보를 정리한다.
"""

from __future__ import annotations

from typing import Any

from prototype.agentic_state import AgenticCoreState


async def result_finalizer_node(state: AgenticCoreState) -> dict:
    """성공/실패에 따라 최종 응답을 구성한다."""
    updates: dict[str, Any] = {"phase": "DONE"}

    # ── 사용자 확인 필요 (CONFLICTED 해소) ──
    if state.phase == "VERIFYING":
        conflicted = [
            ki for ki in state.knowledge_items
            if ki.status == "CONFLICTED"
        ]
        if conflicted:
            question = _build_clarification_question(conflicted)
            updates["needs_user_input"] = True
            updates["user_question"] = question
            updates["final_status"] = "pending"
            return updates

    # ── SQL 검증 성공 ──
    if state.validated_sql:
        updates["final_status"] = "success"
        updates["exploration_summary"] = _build_success_summary(state)
        return updates

    # ── 실패 ──
    updates["final_status"] = "failure"
    updates["exploration_summary"] = _build_failure_output(state)
    return updates


def _build_success_summary(state: AgenticCoreState) -> str:
    """성공 시 탐색 과정 요약."""
    parts: list[str] = []

    # 시도 통계
    g = state.loop_guard
    parts.append(
        f"도구 호출 {g.total_tool_calls}회, "
        f"SQL 생성 {g.generate_attempts}회"
    )
    if g.replan_count > 0:
        parts.append(f"재계획 {g.replan_count}회")

    # 사용된 테이블 (knowledge_items에서 CONFIRMED인 테이블)
    confirmed_tables = [
        ki.key.removeprefix("table:")
        for ki in state.knowledge_items
        if ki.key.startswith("table:") and ki.status == "CONFIRMED"
    ]
    if confirmed_tables:
        parts.append(f"사용 테이블: {', '.join(confirmed_tables)}")

    # 참고 활용사례
    if state.explored_use_cases:
        parts.append(f"참고 활용사례: {len(state.explored_use_cases)}건")

    # 구조적 힌트 활용 여부
    if not state.structural_hints.is_empty():
        parts.append("sqlglot 구조적 힌트 활용")

    return " | ".join(parts)


def _build_failure_output(state: AgenticCoreState) -> str:
    """실패 시 상세 정보."""
    parts: list[str] = ["SQL 생성 실패"]

    # 실패 경로
    if state.dead_ends:
        parts.append("시도한 접근 방식:")
        for de in state.dead_ends:
            parts.append(f"  - {de.reason}")

    # 미해소 용어
    unresolved = state.get_unresolved_knowledge()
    if unresolved:
        terms = ", ".join(ki.key for ki in unresolved)
        parts.append(f"확인하지 못한 정보: {terms}")

    # partial SQL (있으면)
    if state.generated_sql and not state.validated_sql:
        parts.append(f"부분 SQL (미검증): {state.generated_sql[:100]}...")

    return "\n".join(parts)


def _build_clarification_question(
    conflicted_items: list,
) -> str:
    """CONFLICTED 항목에 대한 사용자 확인 질문을 생성한다."""
    parts = ["다음 항목에 대해 확인이 필요합니다:\n"]
    for i, ki in enumerate(conflicted_items, 1):
        evidence_str = "\n".join(f"    - {e}" for e in ki.evidence)
        parts.append(
            f"{i}. **{ki.key}**\n"
            f"   현재 상충되는 정보:\n{evidence_str}\n"
        )
    parts.append("어떤 정보가 맞는지 확인해 주시겠어요?")
    return "\n".join(parts)
