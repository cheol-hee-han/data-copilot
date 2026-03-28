"""recovery_planner 노드 — dead-ends 기반 가설 교체 + 새 실행계획 수립.

planner 노드와 완전히 분리된 별도 노드.
이미 탐색한 것은 재탐색하지 않으며, confirmed knowledge는 재사용한다.

프롬프트 컨텍스트 조립:
  dead_ends의 reason + knowledge_items의 상태 변화 + execution_plan의 insight를
  하나의 구조화된 컨텍스트로 조립하여 LLM에 전달한다.
  별도 필드(learned_facts 등)를 추가하지 않고 기존 State의 데이터를 조합한다.
"""

from __future__ import annotations

from typing import Any

from prototype.agentic_state import (
    AgenticCoreState,
    DeadEnd,
    ExecutionStep,
    Hypothesis,
    KnowledgeItem,
)


async def recovery_planner_node(state: AgenticCoreState) -> dict:
    """dead_ends를 기반으로 다음 가설을 선택하고 새 실행계획을 수립한다."""
    updates: dict[str, Any] = {"phase": "REPLANNING"}

    # 루프 가드 업데이트
    loop_guard = state.loop_guard.model_copy()
    loop_guard.increment_replan()
    updates["loop_guard"] = loop_guard

    # 현재 가설을 FAILED로 마킹 (아직 안 됐으면)
    # C-07: 직접 mutation 대신 복사본으로 처리
    hypotheses = [h.model_copy() for h in state.hypotheses]
    dead_ends = list(state.dead_ends)

    if state.current_hypothesis and state.current_hypothesis.status == "ACTIVE":
        failed_hyp = state.current_hypothesis.model_copy()
        failed_hyp.status = "FAILED"
        # hypotheses 목록에서도 업데이트
        for i, h in enumerate(hypotheses):
            if h.hypothesis_id == failed_hyp.hypothesis_id:
                hypotheses[i] = failed_hyp
                break
        dead_ends.append(DeadEnd(
            hypothesis_id=failed_hyp.hypothesis_id,
            reason=_infer_failure_reason(state),
            tried_tables=failed_hyp.required_tables,
            tried_terms=failed_hyp.missing_terms,
            failure_type=_infer_failure_type(state),
        ))
        updates["hypotheses"] = hypotheses
        updates["dead_ends"] = dead_ends

    # PENDING 가설 중 다음 우선순위 선택
    pending = [h for h in hypotheses if h.status == "PENDING"]

    if not pending:
        # 가설 소진 → 프롬프트 컨텍스트 조립 후 새 가설 생성 시도
        replan_context = _build_replan_context(state, dead_ends)
        new_hypotheses = await _generate_new_hypotheses(replan_context)
        if not new_hypotheses:
            updates["phase"] = "DONE"
            updates["final_status"] = "failure"
            updates["exploration_summary"] = _build_failure_summary(
                state, dead_ends,
            )
            updates["current_hypothesis"] = None
            return updates
        pending = new_hypotheses
        updates["hypotheses"] = hypotheses + new_hypotheses

    # 다음 가설 활성화
    next_hyp = pending[0].model_copy()
    next_hyp.status = "ACTIVE"
    updates["current_hypothesis"] = next_hyp

    # 프롬프트 컨텍스트 조립 후 새 실행계획 수립
    replan_context = _build_replan_context(state, dead_ends)
    execution_plan = await _build_replan_execution(
        next_hyp, replan_context,
    )
    updates["execution_plan"] = execution_plan
    updates["current_step_index"] = 0
    updates["phase"] = "EXPLORING"

    return updates


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 프롬프트 컨텍스트 조립
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _build_replan_context(
    state: AgenticCoreState,
    dead_ends: list[DeadEnd],
) -> dict[str, Any]:
    """replan LLM 프롬프트에 주입할 컨텍스트를 조립한다.

    dead_ends의 reason, knowledge_items의 상태 변화,
    execution_plan의 step insight를 하나의 구조로 통합한다.
    별도 필드를 추가하지 않고 기존 State 데이터를 조합한다.
    """
    context: dict[str, Any] = {
        "original_query": state.original_query,
    }

    # ── 1. 실패 이력 (dead_ends) ──
    # 각 가설의 실패 사유 + 시도한 테이블/용어
    failure_history: list[dict[str, Any]] = []
    for de in dead_ends:
        failure_history.append({
            "hypothesis": de.hypothesis_id,
            "reason": de.reason,
            "failure_type": de.failure_type,
            "tried_tables": de.tried_tables,
            "tried_terms": de.tried_terms,
        })
    context["failure_history"] = failure_history

    # ── 2. 탐색에서 확인한 사실 (step insights) ──
    # execution_plan의 DONE 스텝에서 insight를 수집
    discovered_facts: list[str] = []
    for step in state.execution_plan:
        if step.status == "DONE" and step.insight:
            discovered_facts.append(
                f"[{step.tool}] {step.insight}"
            )
    context["discovered_facts"] = discovered_facts

    # ── 3. 확인된 지식 (재사용 가능) ──
    # CONFIRMED/PROBABLE 항목은 다음 가설에서 재탐색 불필요
    confirmed_knowledge: list[dict[str, str]] = []
    for ki in state.knowledge_items:
        if ki.status in ("CONFIRMED", "PROBABLE"):
            confirmed_knowledge.append({
                "key": ki.key,
                "value": ki.value,
                "status": ki.status,
                "source": ki.source,
            })
    context["confirmed_knowledge"] = confirmed_knowledge

    # ── 4. 미해소 항목 (다음 가설에서 해결해야 할 것) ──
    unresolved: list[str] = []
    for ki in state.knowledge_items:
        if ki.status in ("UNRESOLVED", "CONFLICTED"):
            detail = f"{ki.key}"
            if ki.status == "CONFLICTED" and ki.evidence:
                detail += f" (충돌: {'; '.join(ki.evidence[-2:])})"
            unresolved.append(detail)
    context["unresolved_items"] = unresolved

    # ── 5. 중복 방지 목록 ──
    context["searched_queries"] = state.searched_queries
    context["sampled_tables"] = state.sampled_tables

    return context


def _format_replan_prompt(context: dict[str, Any]) -> str:
    """조립된 컨텍스트를 LLM 프롬프트 텍스트로 변환한다.

    replan 전용 시스템 프롬프트와 함께 사용된다.
    """
    parts: list[str] = []

    # 실패 이력
    if context["failure_history"]:
        parts.append("## 이전 시도 결과")
        for fh in context["failure_history"]:
            parts.append(
                f"- 가설 {fh['hypothesis']} [{fh['failure_type']}]: "
                f"{fh['reason']}"
            )
            if fh["tried_tables"]:
                parts.append(
                    f"  시도한 테이블: {', '.join(fh['tried_tables'])}"
                )

    # 탐색에서 확인한 사실
    if context["discovered_facts"]:
        parts.append("\n## 탐색에서 확인한 사실")
        for fact in context["discovered_facts"]:
            parts.append(f"- {fact}")

    # 확인된 지식 (재사용 가능)
    if context["confirmed_knowledge"]:
        parts.append("\n## 확인된 지식 (재사용 가능, 재탐색 불필요)")
        for ck in context["confirmed_knowledge"]:
            parts.append(f"- {ck['key']}: {ck['value']} ({ck['source']})")

    # 미해소 항목
    if context["unresolved_items"]:
        parts.append("\n## 아직 해소되지 않은 항목")
        for item in context["unresolved_items"]:
            parts.append(f"- {item}")

    # 중복 방지
    if context["searched_queries"]:
        parts.append(
            f"\n## 이미 검색한 쿼리 (재검색 금지)\n"
            f"{', '.join(context['searched_queries'])}"
        )
    if context["sampled_tables"]:
        parts.append(
            f"\n## 이미 샘플 조회한 테이블 (재조회 금지)\n"
            f"{', '.join(context['sampled_tables'])}"
        )

    return "\n".join(parts)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 내부 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _infer_failure_reason(state: AgenticCoreState) -> str:
    """현재 상태에서 실패 사유를 추론한다."""
    if state.sql_validation_result:
        match state.sql_validation_result.overall:
            case "FAIL_STRUCTURAL":
                return "SQL 구조적 오류 — 테이블/컬럼 재탐색 필요"
            case "FAIL_EMPTY":
                return "실행 결과 0건 — 조건 또는 테이블 부적절"
            case "FAIL_DB_ERROR":
                return "DB 실행 오류"
            case _:
                return "SQL 검증 실패"

    unresolved = state.get_unresolved_knowledge()
    if unresolved:
        return f"미해소 용어: {', '.join(ki.key for ki in unresolved)}"

    return "탐색 스텝 소진 — 충분한 정보 미확보"


def _infer_failure_type(state: AgenticCoreState) -> str:
    """실패 유형을 추론한다."""
    if state.sql_validation_result:
        match state.sql_validation_result.overall:
            case "FAIL_STRUCTURAL":
                return "sql_structural"
            case "FAIL_EMPTY":
                return "empty_result"
            case "FAIL_DB_ERROR":
                return "db_error"
            case "FAIL_SYNTAX":
                return "sql_syntax"
            case "FAIL_SEMANTIC_LOCAL":
                return "sql_semantic_local"
    return "term_unresolvable"


async def _generate_new_hypotheses(
    replan_context: dict[str, Any],
) -> list[Hypothesis]:
    """LLM을 사용하여 기존 dead-ends를 회피하는 새 가설을 생성한다.

    _build_replan_context()로 조립된 컨텍스트를 프롬프트에 주입한다.
    LLM은 실패 이력 + 확인된 사실 + 미해소 항목을 종합하여
    "교훈 도출 + 새 가설 수립"을 한 번에 수행한다.

    TODO: 실제 구현 시 LLM 호출.
    prompt = REPLAN_SYSTEM_PROMPT + _format_replan_prompt(replan_context)
    """
    # 프로토타입: 빈 리스트 반환 (가설 소진)
    return []


async def _build_replan_execution(
    hypothesis: Hypothesis,
    replan_context: dict[str, Any],
) -> list[ExecutionStep]:
    """새 가설에 대한 실행계획을 수립한다.

    replan_context에서 이미 확인된 지식, 검색한 쿼리, 샘플 조회한 테이블을
    참조하여 중복을 방지한다.

    TODO: 실제 구현 시 LLM 호출.
    """
    steps: list[ExecutionStep] = []
    step_num = 1

    searched = set(replan_context.get("searched_queries", []))
    sampled = set(replan_context.get("sampled_tables", []))

    # dead-ends에서 실패한 테이블 목록
    failed_tables: set[str] = set()
    for fh in replan_context.get("failure_history", []):
        failed_tables.update(fh.get("tried_tables", []))

    # 가설의 required_tables 중 실패하지 않은 것만 탐색
    for table in hypothesis.required_tables:
        if table not in failed_tables and table not in searched:
            steps.append(ExecutionStep(
                step=step_num,
                tool="search_table_meta",
                input=table,
                purpose=f"새 가설: {table} 테이블 구조 확인",
            ))
            step_num += 1

    # 기본 탐색 스텝 (가설 전략에 따라)
    if not steps:
        steps.append(ExecutionStep(
            step=1,
            tool="search_use_cases",
            input=hypothesis.strategy,
            purpose="새 가설 기반 활용사례 재검색",
        ))

    return steps


def _build_failure_summary(
    state: AgenticCoreState,
    dead_ends: list[DeadEnd],
) -> str:
    """최종 실패 요약을 생성한다."""
    parts = [f"총 {state.loop_guard.total_tool_calls}회 도구 호출, "
             f"{state.loop_guard.replan_count}회 재계획 시도"]

    if dead_ends:
        parts.append("실패 경로:")
        for de in dead_ends:
            parts.append(f"  - [{de.failure_type}] {de.reason}")

    unresolved = state.get_unresolved_knowledge()
    if unresolved:
        parts.append(f"미해소 용어: {', '.join(ki.key for ki in unresolved)}")

    return "\n".join(parts)
