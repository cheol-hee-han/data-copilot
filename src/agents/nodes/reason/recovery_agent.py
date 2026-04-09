"""recovery_agent 노드 — 실패 후 재계획 전용.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

readiness_gate 또는 sql_validator에서 진입하여,
현재 가설을 FAILED 처리하고 dead_end에 기록한 뒤,
LLM 1회 호출로 새 execution_plan을 수립한다.

도구 실행과 결과 해석은 수행하지 않는다.
수립된 execution_plan은 기존 파이프라인(context_retriever → context_interpreter
→ readiness_gate)에서 실행·해석·평가된다.

흐름:
    1. [Python] Hypothesis 상태 전이 (ACTIVE→FAILED, PENDING 소비)
    2. [Python] DeadEnd 기록
    3. [LLM 1회] 새 execution_plan 수립 (+ 선택적 새 가설 생성)
    4. [Python] Phase 전이 → EXPLORING (context_retriever로 라우팅)
       또는 give_up → Phase.DONE (result_finalizer로 라우팅)

핵심 함수:
    - recovery_agent_node: 메인 노드 (재계획 전용)

위임 구조:
    - 프롬프트: system_prompts.py의 RECOVERY_AGENT_SYSTEM
"""

from __future__ import annotations

from typing import Any

from psycopg.sql import SQL
from pydantic import BaseModel, Field

from src.agents.state.state import (
    ConfidenceStatus,
    DeadEnd,
    ExecutionStep,
    FailureType,
    FinalStatus,
    Hypothesis,
    HypothesisStatus,
    Phase,
    PipelineState,
    ReasoningState,
    SelectionStatus,
    StepStatus,
    should_terminate,
)
from src.agents.nodes.system_prompts import RECOVERY_AGENT_SYSTEM
from src.config import settings
from src.services.confidence_scorer import (
    THRESHOLD_FORCE_GENERATE,
    calculate_readiness,
)
from src.utils.llm import llm_call_with_parse_retry, ParseError
from src.utils.llm.response import extract_json
from src.utils.logger import get_logger
from src.utils.tracker import record_prompt_variables
from src.utils.tracker.dispatch import (
    dispatch_tracking_event,
    REASONING_STEP,
)

logger = get_logger(__name__)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 메인 노드
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


async def recovery_agent_node(
    state: PipelineState,
) -> dict:
    """실패 분석 후 새 execution_plan을 수립한다.

    도구 실행·결과 해석은 하지 않는다.
    수립된 plan은 context_retriever → context_interpreter → readiness_gate에서 처리.
    """
    reason = state.reason.model_copy(deep=True)
    reason.phase = Phase.REPLANNING
    reason.exploration_phase = "recovery"

    # Step 1: Hypothesis 전이 + DeadEnd 기록
    _handle_hypothesis_transition(reason)

    reason.loop_guard = reason.loop_guard.model_copy()
    reason.loop_guard.increment_replan()

    # 실패 맥락을 로컬에 보존 (recovery 계획 수립에 사용)
    # DeadEnd에는 이미 line 74에서 기록됨 → state에서는 초기화한다.
    # recovery는 "접근 방식 전환"이므로 이전 SQL의 구체적 수정 지시(fix_section)는
    # 새 전략과 컨텍스트가 불일치할 수 있어 전달하지 않는다.
    entry_failure_type = reason.failure_type
    entry_failure_reason = reason.failure_reason
    reason.failure_type = None
    reason.failure_reason = None

    # ── 동일 실패 유형 연속 반복 가드 (rule-based) ──
    # _handle_hypothesis_transition에서 현재 실패를 dead_ends에 append한 직후이므로,
    # N번째 실패 진입 시 dead_ends에 N개가 쌓여 즉시 give_up된다.
    # 예: max=3이면 3번째 실패 기록 즉시 종료 (3번째 재시도 기회 없음).
    if entry_failure_type and _count_consecutive_same_failure(
        reason.dead_ends, entry_failure_type,
    ) >= settings.max_same_failure_repeats:
        # LLM 미호출이므로 정적 교훈을 마지막 dead_end에 직접 기입
        if reason.dead_ends:
            last = reason.dead_ends[-1]
            reason.dead_ends[-1] = last.model_copy(update={
                "lessons_learned": (
                    f"동일 실패 유형({entry_failure_type.value})이 "
                    f"{settings.max_same_failure_repeats}회 연속 반복되어 "
                    "추가 재시도가 무의미하다고 판단"
                ),
            })
        _finalize_give_up(reason)
        logger.info(
            "recovery_agent: 동일 failure_type 연속 반복, 강제 종료",
            failure_type=entry_failure_type.value,
            repeat_count=settings.max_same_failure_repeats,
        )
        await _dispatch_reasoning_step(
            reason,
            entry_failure_type,
            entry_failure_reason,
            None,
            {},
            action="give_up",
            next_node="result_finalizer",
            routing_reason=(
                f"동일 실패 유형({entry_failure_type.value}) "
                f"{settings.max_same_failure_repeats}회 연속 → 강제 종료"
            ),
        )
        return {"reason": reason}

    # Step 2: LLM 1회 호출 → 새 execution_plan 수립
    # 루프 가드 검사를 LLM 호출 이전이 아닌 이후에 수행하면
    # PENDING 가설이 소진된 상태에서도 LLM이 새 가설을 생성할 기회를 얻는다.
    plan_result, full_variables = await _build_recovery_plan(
        reason,
        entry_failure_type=entry_failure_type,
        entry_failure_reason=entry_failure_reason,
    )

    # 루프 가드 검사 (LLM 호출 후 — 새 가설 생성 기회 보장)
    if should_terminate(reason) and (
        plan_result is None or not plan_result.execution_plan
    ):
        _attach_lessons(reason, plan_result)
        _finalize_give_up(reason)
        logger.info(
            "recovery_agent: 루프 가드 한도 초과, 종료",
            phase=reason.phase.value,
        )
        await _dispatch_reasoning_step(
            reason,
            entry_failure_type,
            entry_failure_reason,
            plan_result,
            full_variables,
            action="give_up",
            next_node="result_finalizer",
            routing_reason="루프 가드 한도 초과 → 종료",
        )
        return {"reason": reason}

    if plan_result is None or plan_result.action == "give_up":
        _attach_lessons(reason, plan_result)
        _finalize_give_up(reason)
        logger.info(
            "recovery_agent: give_up",
            phase=reason.phase.value,
        )
        await _dispatch_reasoning_step(
            reason,
            entry_failure_type,
            entry_failure_reason,
            plan_result,
            full_variables,
            action="give_up",
            next_node="result_finalizer",
            routing_reason="give_up → 종료",
        )
        return {"reason": reason}

    _attach_lessons(reason, plan_result)

    # Step 3: 새 execution_plan 적용
    reason.execution_plan = plan_result.execution_plan
    if plan_result.new_hypothesis:
        plan_result.new_hypothesis.hypothesis_id = (
            f"H{len(reason.hypotheses) + 1}"
        )
        reason.hypotheses.append(plan_result.new_hypothesis)
        reason.current_hypothesis = plan_result.new_hypothesis

    # Phase → EXPLORING (context_retriever로 라우팅)
    reason.phase = Phase.EXPLORING

    logger.info(
        "recovery_agent: 재계획 완료",
        execution_steps=len(reason.execution_plan),
        new_hypothesis=bool(plan_result.new_hypothesis),
        phase=reason.phase.value,
    )

    _new_hyp = plan_result.new_hypothesis
    await _dispatch_reasoning_step(
        reason,
        entry_failure_type,
        entry_failure_reason,
        plan_result,
        full_variables,
        action="replan",
        next_node="context_retriever",
        routing_reason=(
            f"replan → 가설 {_new_hyp.hypothesis_id if _new_hyp else 'N/A'}"
            f"로 재탐색 (Round {reason.loop_guard.replan_count})"
        ),
    )

    return {"reason": reason}


async def _dispatch_reasoning_step(
    reason: ReasoningState,
    _entry_failure_type: FailureType | None,  # noqa: ARG001
    _entry_failure_reason: str | None,  # noqa: ARG001
    plan_result: RecoveryPlan | None,
    full_variables: dict[str, str],
    *,
    action: str,
    next_node: str,
    routing_reason: str,
) -> None:
    """recovery_agent의 reasoning step을 디스패치한다."""
    _output: dict[str, Any] = {"action": action}
    if plan_result is not None:
        _output["analysis"] = plan_result.lessons_learned or ""
        _output["lessons_learned"] = plan_result.lessons_learned or ""
        if plan_result.new_hypothesis:
            _output["new_hypothesis"] = {
                "id": plan_result.new_hypothesis.hypothesis_id,
                "description": plan_result.new_hypothesis.description,
            }
        if plan_result.execution_plan:
            _output["new_plan"] = [
                f'Step {s.step}: {s.tool}("{s.input[:80]}")'
                for s in plan_result.execution_plan
            ]

    await dispatch_tracking_event(
        REASONING_STEP,
        {
            "node": "recovery_agent",
            "phase": "reason",
            "step_type": "recovery",
            "round": reason.loop_guard.replan_count,
            "hypothesis_id": (
                reason.current_hypothesis.hypothesis_id
                if reason.current_hypothesis
                else ""
            ),
            "inputs": {
                "entry_source": full_variables.get(
                    "entry_source_description",
                    "",
                ),
                "confirmed_knowledge": full_variables.get(
                    "confirmed_knowledge",
                    "",
                ),
                "unresolved_items": full_variables.get(
                    "unresolved_items",
                    "",
                ),
                "tool_execution_history": full_variables.get(
                    "tool_execution_history",
                    "",
                ),
                "explored_tables": full_variables.get(
                    "explored_tables_summary",
                    "",
                ),
                "dead_ends": full_variables.get(
                    "dead_ends_summary",
                    "",
                ),
                "sample_data": full_variables.get(
                    "sample_data_summary",
                    "",
                ),
            },
            "output": _output,
            "routing": {
                "next_node": next_node,
                "reason": routing_reason,
            },
        },
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Hypothesis 관리
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _handle_hypothesis_transition(
    reason: ReasoningState,
) -> None:
    """현재 가설 FAILED 전환 + PENDING 소비 + DeadEnd 기록."""
    if (
        reason.current_hypothesis
        and reason.current_hypothesis.status == HypothesisStatus.ACTIVE
    ):
        failed = reason.current_hypothesis.model_copy()
        failed.status = HypothesisStatus.FAILED
        for i, h in enumerate(reason.hypotheses):
            if h.hypothesis_id == failed.hypothesis_id:
                reason.hypotheses[i] = failed
                break

        reason.dead_ends.append(
            DeadEnd(
                hypothesis_id=failed.hypothesis_id,
                failure_type=(reason.failure_type or FailureType.TERM_UNRESOLVABLE),
                reason=reason.failure_reason or "실패 사유 미제공",
            )
        )
    elif reason.failure_type:
        # 가설 없이 진입한 경우에도 DeadEnd를 기록하여
        # recovery LLM이 이전 실패 패턴을 인지할 수 있도록 한다.
        reason.dead_ends.append(
            DeadEnd(
                hypothesis_id="no_hypothesis",
                failure_type=reason.failure_type,
                reason=reason.failure_reason or "가설 없이 실패",
            )
        )

    next_hyp = _consume_next_pending(reason.hypotheses)
    if next_hyp:
        reason.current_hypothesis = next_hyp
    else:
        reason.current_hypothesis = None


def _consume_next_pending(
    hypotheses: list[Any],
) -> Any | None:
    """우선순위 순 PENDING 가설을 소비하여 ACTIVE로 전환."""
    pending = [h for h in hypotheses if h.status == HypothesisStatus.PENDING]
    if not pending:
        return None
    pending.sort(key=lambda h: h.priority, reverse=True)
    top = pending[0].model_copy()
    top.status = HypothesisStatus.ACTIVE
    for i, h in enumerate(hypotheses):
        if h.hypothesis_id == top.hypothesis_id:
            hypotheses[i] = top
            break
    return top


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LLM 호출 — 재계획
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class RecoveryPlan(BaseModel):
    """recovery_agent LLM 출력 — 재계획 결과.

    action이 "replan"이면 execution_plan과 선택적 new_hypothesis를 포함하고,
    "give_up"이면 lessons_learned만 포함한다.
    """

    action: str = "give_up"
    lessons_learned: str = ""
    execution_plan: list[ExecutionStep] = Field(
        default_factory=list,
    )
    new_hypothesis: Hypothesis | None = None


async def _build_recovery_plan(
    reason: ReasoningState,
    *,
    entry_failure_type: FailureType | None = None,
    entry_failure_reason: str | None = None,
) -> tuple[RecoveryPlan | None, dict[str, str]]:
    """LLM 1회 호출로 새 execution_plan을 수립한다.

    Returns:
        (RecoveryPlan 또는 None, full_variables) 튜플.
    """
    prompt, variables, full_variables = _build_prompt(
        reason,
        entry_failure_type=entry_failure_type,
        entry_failure_reason=entry_failure_reason,
    )

    def _parse_fn(raw_text: str) -> RecoveryPlan:
        return _parse_plan_response(raw_text)

    try:
        _, plan = await llm_call_with_parse_retry(
            system=prompt,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "[TASK]"
                        "실패 원인을 분석하고 새로운 탐색 계획을 수립하세요."
                    ),
                },
            ],
            parse_fn=_parse_fn,
            max_tokens=1024,
            timeout=settings.llm_long_timeout,
            node_name="recovery_agent",
        )
        await record_prompt_variables(variables)
        return plan, full_variables
    except (ParseError, ValueError, TimeoutError) as e:
        logger.warning(
            "recovery_agent LLM 호출 실패",
            error=str(e),
        )
        return None, full_variables


def _parse_plan_response(raw_text: str) -> RecoveryPlan:
    """LLM 응답에서 execution_plan을 파싱한다."""
    data = extract_json(raw_text)
    if not data:
        raise ValueError(
            "recovery LLM 응답에서 JSON 추출 실패",
        )

    action = data.get("action", "give_up")
    if action not in ("replan", "give_up"):
        action = "replan"

    lessons_learned = data.get("lessons_learned", "")

    # execution_plan 파싱
    steps: list[ExecutionStep] = []
    for i, step_data in enumerate(
        data.get("execution_plan", []),
    ):
        if isinstance(step_data, dict) and step_data.get("tool"):
            steps.append(
                ExecutionStep(
                    step=i + 1,
                    tool=step_data["tool"],
                    input=step_data.get("input", ""),
                    purpose=step_data.get("purpose", ""),
                )
            )

    # 새 가설 파싱 (선택적, ID는 Python에서 순번 채번)
    new_hypothesis = None
    hyp_data = data.get("new_hypothesis")
    if isinstance(hyp_data, dict) and hyp_data.get("description"):
        new_hypothesis = Hypothesis(
            hypothesis_id="",  # 호출부에서 채번
            description=hyp_data["description"],
            strategy=hyp_data.get("strategy", ""),
            priority=0.7,
            status=HypothesisStatus.ACTIVE,
        )

    if action == "give_up" or not steps:
        return RecoveryPlan(
            action="give_up",
            lessons_learned=lessons_learned,
        )

    # new_hypothesis 필수 — LLM이 누락 시 execution_plan에서 추론
    if new_hypothesis is None:
        new_hypothesis = Hypothesis(
            hypothesis_id="",  # 호출부에서 채번
            description=steps[0].purpose,
            strategy=", ".join(s.purpose for s in steps),
            priority=0.7,
            status=HypothesisStatus.ACTIVE,
        )

    return RecoveryPlan(
        action="replan",
        lessons_learned=lessons_learned,
        execution_plan=steps,
        new_hypothesis=new_hypothesis,
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 프롬프트 빌더 — 헬퍼 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _summarize_use_cases(
    reason: ReasoningState,
    step_num: int,
) -> str:
    """지정 스텝에서 발견된 유사 SQL의 관련성 요약을 반환한다."""
    step_ucs = [uc for uc in reason.explored_use_cases if uc.source_step == step_num]
    relevant = [uc for uc in step_ucs if uc.relevant]
    not_relevant = [uc for uc in step_ucs if not uc.relevant and uc.eval_reason]
    if not relevant and not not_relevant:
        return ""
    parts: list[str] = []
    if relevant:
        descs = ", ".join(f'"{uc.description[:30]}"' for uc in relevant[:3])
        parts.append(f"관련 {len(relevant)}건({descs})")
    if not_relevant:
        parts.append(f"비관련 {len(not_relevant)}건")
    return ", ".join(parts)


def _summarize_tables(
    reason: ReasoningState,
    step_num: int,
) -> str:
    """지정 스텝에서 발견된 테이블의 선택 상태 요약을 반환한다."""
    step_tables = [t for t in reason.explored_tables if t.source_step == step_num]
    selected = [
        t for t in step_tables if t.selection_status == SelectionStatus.SELECTED
    ]
    rejected = [
        t for t in step_tables if t.selection_status == SelectionStatus.REJECTED
    ]
    pending = [t for t in step_tables if t.selection_status == SelectionStatus.PENDING]
    if not selected and not rejected:
        return ""
    parts: list[str] = []
    if selected:
        names = ", ".join(t.table_name for t in selected[:3])
        parts.append(f"SELECTED {len(selected)}건({names})")
    if rejected:
        parts.append(f"REJECTED {len(rejected)}건")
    if pending:
        parts.append(f"PENDING {len(pending)}건")
    return ", ".join(parts)


def _summarize_biz_manuals(
    reason: ReasoningState,
    step_num: int,
) -> str:
    """지정 스텝에서 발견된 업무 매뉴얼의 선택 상태 요약을 반환한다."""
    step_manuals = [m for m in reason.explored_biz_manuals if m.source_step == step_num]
    selected = [
        m for m in step_manuals if m.selection_status == SelectionStatus.SELECTED
    ]
    rejected = [
        m for m in step_manuals if m.selection_status == SelectionStatus.REJECTED
    ]
    if not selected and not rejected:
        return ""
    parts: list[str] = []
    if selected:
        parts.append(f"SELECTED {len(selected)}건")
    if rejected:
        parts.append(f"REJECTED {len(rejected)}건")
    return ", ".join(parts)


def _summarize_biz_terms(
    reason: ReasoningState,
    step_num: int,
) -> str:
    """지정 스텝에서 발견된 비즈니스 용어의 선택 상태 요약을 반환한다."""
    step_terms = [t for t in reason.explored_biz_terms if t.source_step == step_num]
    selected = [t for t in step_terms if t.selection_status == SelectionStatus.SELECTED]
    rejected = [t for t in step_terms if t.selection_status == SelectionStatus.REJECTED]
    if not selected and not rejected:
        return ""
    parts: list[str] = []
    if selected:
        names = ", ".join(t.term for t in selected[:3])
        parts.append(f"SELECTED {len(selected)}건({names})")
    if rejected:
        parts.append(f"REJECTED {len(rejected)}건")
    return ", ".join(parts)


def _count_results(raw_result: Any) -> int:
    """raw_result에서 결과 건수를 추산한다."""
    if isinstance(raw_result, list):
        return len(raw_result)
    if isinstance(raw_result, dict):
        for key in ("use_cases", "results", "items"):
            if key in raw_result and isinstance(raw_result[key], list):
                return len(raw_result[key])
    return 0


_RELEVANCE_BUILDERS: dict[str, Any] = {
    "search_use_cases": _summarize_use_cases,
    "search_table_meta": _summarize_tables,
    "lookup_table_meta": _summarize_tables,
    "search_biz_terms": _summarize_biz_terms,
    "search_manual": _summarize_biz_manuals,
}


def _build_tool_execution_history(reason: ReasoningState) -> str:
    """ExecutionStep + 각 엔티티 state의 관련성 평가를 동적 집계한다."""
    lines: list[str] = []

    for step in reason.execution_plan:
        if step.status == StepStatus.SKIPPED:
            continue

        status_mark = "✓" if step.status == StepStatus.DONE else "✗"
        lines.append(
            f"[스텝 {step.step}] {status_mark} " f'{step.tool}("{step.input}")'
        )

        # 결과 건수
        if step.raw_result:
            count = _count_results(step.raw_result)
            lines.append(f"  결과: {count}건")

        # 관련성 요약
        builder = _RELEVANCE_BUILDERS.get(step.tool)
        if builder:
            summary = builder(reason, step.step)
            if summary:
                lines.append(f"  관련성: {summary}")

        # insight
        if step.insight:
            lines.append(f"  발견: {step.insight}")

    return "\n".join(lines) or "(실행 이력 없음)"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 프롬프트 빌더
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_VALUE_LABEL = {
    "measure": "SQL 표현",
    "filter": "조건식",
    "grouping": "기준 컬럼",
    "table": "관련성",
    "join": "조인 조건",
    "format": "형식",
}

_ROLE_DESC = {
    "measure": "SELECT 절 출력항목 확인 (예: 일반값, 집계, 율 등)",
    "filter": "WHERE 절 조건 확인",
    "grouping": "GROUP BY 기준 컬럼 확인",
    "table": "조회 대상 테이블 확인",
    "join": "테이블 간 연결 조건 확인",
    "format": "컬럼 데이터 형식 확인",
}


def _serialize_confirmed_items(
    knowledge_items: list[KnowledgeItem] | None,
) -> list[str]:
    """CONFIRMED/PROBABLE 지식 항목을 프롬프트용 문자열로 직렬화한다."""

    if not knowledge_items:
        return []

    confirmed = []
    for ki in knowledge_items:
        prefix, _, _ = ki.key.partition(":")
        if ki.status in (
            ConfidenceStatus.CONFIRMED,
            ConfidenceStatus.PROBABLE,
        ):
            evidence = ", ".join(ki.evidence) or "미생성"
            confirmed.append(
                f"- {ki.key} — {ki.status.value}\n"
                f"  역할: {_ROLE_DESC.get(prefix, '기타')}\n"
                f"  {_VALUE_LABEL.get(prefix, '값')}: {ki.value}\n"
                f"  판단 근거: {evidence} (출처: {ki.source})",
            )
    return confirmed


def _serialize_unresolved_items(
    knowledge_items: list[KnowledgeItem] | None,
) -> list[str]:
    """CANDIDATE/UNRESOLVED/CONFLICTED 지식 항목을 프롬프트용 문자열로 직렬화한다."""

    if not knowledge_items:
        return []

    unresolved = []
    for ki in knowledge_items:
        prefix, _, _ = ki.key.partition(":")
        critical_suffix = " (CRITICAL)" if ki.is_critical else ""
        if ki.status == ConfidenceStatus.UNRESOLVED:
            unresolved.append(
                f"- {ki.key} — {ki.status.value}{critical_suffix}\n"
                f"  역할: {_ROLE_DESC.get(prefix, '기타')}",
            )
        elif ki.status == ConfidenceStatus.CANDIDATE:
            evidence = ", ".join(ki.evidence) or "미생성"
            unresolved.append(
                f"- {ki.key} — {ki.status.value}{critical_suffix}\n"
                f"  역할: {_ROLE_DESC.get(prefix, '기타')}\n"
                f"  {_VALUE_LABEL.get(prefix, '값')} 후보: {ki.value}\n"
                f"  후보 판단 사유: {evidence} (출처: {ki.source})",
            )
        elif ki.status == ConfidenceStatus.CONFLICTED:
            evidence = ", ".join(ki.evidence) or "미생성"
            unresolved.append(
                f"- {ki.key} — {ki.status.value}{critical_suffix}\n"
                f"  역할: {_ROLE_DESC.get(prefix, '기타')}\n"
                f"  {_VALUE_LABEL.get(prefix, '값')}: {ki.value or '판단 불가'}\n"
                f"  판단 충돌 사유: {evidence} (출처: {ki.source})",
            )

    return unresolved


_TABLE_TYPE = {
    "M": "마스터",
    "D": "상세",
    "L": "내역",
    "H": "이력",
    "G": "로그",
    "S": "집계",
    "P": "스냅샷",
    "C": "코드",
    "F": "인터페이스",
}


def _serialize_selected_table_meta(explored_tables: list[TableMeta]) -> list[str]:
    """SELECTED table meta 를 프롬프트용 문자열로 직렬화한다."""

    if not explored_tables:
        return []
    
    selected_tables = []
    for ct in explored_tables:
        if ct.selection_status == SelectionStatus.REJECTED:
            continue

        line = f"- {ct.table_name}({ct.alt_name})" f" ({ct.selection_status.value})"
        if ct.description:
            line += f"\n  테이블 설명: {ct.description}"
        line += f"\n  테이블 유형: {ct.table_name[-1]}({_TABLE_TYPE.get(ct.table_name[-1], "기타")})"
        if ct.subject_area:
            line += f"\n  업무 주제영역: {ct.subject_area}"
        col_names = ", ".join(
            f"{c.name}({c.alt_name})" if c.alt_name else c.name for c in ct.columns
        )
        if col_names:
            line += f"\n  컬럼: {col_names}"
        selected_tables.append(line)

    return selected_tables


def _serialize_dead_ends(
    dead_ends: list[DeadEnd],
    hypotheses: list[Hypothesis],
) -> list[str]:
    """이전 실패 이력을 가설-실패 쌍으로 직렬화한다."""

    if not dead_ends:
        return []

    hyp_map: dict[str, Hypothesis] = {h.hypothesis_id: h for h in hypotheses}

    de_arr = []
    for idx, de in enumerate(dead_ends, 1):
        hyp = hyp_map.get(de.hypothesis_id)
        line = f"- (ROUND #{idx}, 가설 ID: {de.hypothesis_id})\n"
        if hyp:
            line += f"  가설 수립: \"{hyp.description}\"\n"
            if hyp.strategy:
                line += f"  해결 전략: {hyp.strategy}\n"
        line += f"  → 결과: 실패 ({de.failure_type.value})\n"
        line += f"  → 원인: {de.reason}\n"
        if de.lessons_learned:
            line += f"  → 교훈: {de.lessons_learned}"
        de_arr.append(line)

    return de_arr


def _build_prompt(
    reason: ReasoningState,
    *,
    entry_failure_type: FailureType | None = None,
    entry_failure_reason: str | None = None,
) -> tuple[str, dict[str, str], dict[str, str]]:
    """recovery_agent 프롬프트를 조립한다.

    진입 경로(readiness_gate/sql_validator/sql_generator)�� 설명,
    지식 항목 ��류, 후보 테이블 요약, dead_ends 이력을 치���하여
    RECOVERY_AGENT_SYSTEM 템플��을 완성한다.

    Returns:
        (치환된 프롬프트, 200자 truncated 변수, full text 원본) 튜플.
    """
    # 진입 경로 설명
    entry_src = reason.recovery_entry_source or "readiness_gate"
    ft = entry_failure_type or "미제공"
    fr = entry_failure_reason or "미제공"

    
#   - readiness_gate: 초기 탐색이 불충분하여 추가 탐색이 필요합니다. 넓은 범위에서 공백을 채우세요.
#   - readiness_gate(NO_KNOWLEDGE): 질의 정규화에서 측정값·조건이 추출되지 않았습니다.
#     유사 SQL을 참고하여 질의를 재해석하거나, 사용자에게 구체적인 항목을 확인하세요.
#   - sql_validator: SQL 검증이 실패했습니다. 실패 원인에 집중하세요.
#   - sql_generator: SQL 생성이 정보 부족으로 거부되었습니다. 거부 사유(reasons)를 확인하고 해당 정보를 채우는 탐색을 계획하세요.

    if entry_src == "sql_validator":
        entry_desc = (
            f"실패 노드: sql_validator — SQL을 생성하였으나 검증이 실패했습니다. 실패 사유에 집중하세요.\n"
            f"실패 유형: {ft}\n"
            f"상세 사유:\n{fr}"
        )
    elif entry_src == "sql_generator":
        entry_desc = (
            f"실패 노드: sql_generator — SQL 생성이 정보 부족으로 거부되었습니다. 실패 사유를 확인하고 해당 정보를 채우는 탐색을 계획하세요.\n"
            f"실패 유형: {ft}\n"
            f"상세 사유:\n{fr}"
        )
    elif ft == FailureType.NO_KNOWLEDGE:  # readiness_gate
        entry_desc = (
            f"실패 노드: readiness_gate — 정보 탐색이 불충분합니다. 질의 정규화에서 SELECT 절 표현, WHERE 필터 조건 등이 추출되지 않았습니다. 현재 정보를 기반으로 다시 정규화 해야합니다.\n"
            f"실패 유형: {ft}\n"
            f"상세 사유:\n{fr}"
        )
    else:
        entry_desc = (
            f"실패 노드: readiness_gate — 정보 탐색이 불충분합니다. 아직 확인되지 않은 정보를 해소하기 위해 집중하세요. 만약, 참고할 SQL이 부족하다면 추가로 탐색을 계획하세요.\n"
            f"실패 유형: {ft}\n"
            f"상세 사유:\n{fr}"
        )

    # 지식 항목 분류
    confirmed_items = _serialize_confirmed_items(reason.knowledge_items)
    unresolved_items = _serialize_unresolved_items(reason.knowledge_items)

    # 후보 테이블 요약
    selected_tables = _serialize_selected_table_meta(reason.explored_tables)

    # dead_ends 요약
    de_lines = _serialize_dead_ends(reason.dead_ends, reason.hypotheses)

    # 치환 (프롬프트 파일의 placeholder와 1:1 매핑)
    prompt = RECOVERY_AGENT_SYSTEM
    replacements = {
        "{entry_source_description}": entry_desc,
        "{confirmed_knowledge}": ("\n".join(confirmed_items) or "(없음 — 확인된 정보가 없습니다.)"),
        "{unresolved_items}": ("\n".join(unresolved_items) or "(없음 — 해소되지 않은 정보가 없습니다.)"),
        "{explored_tables_summary}": ("\n".join(selected_tables) or "(없음 — 연관 있는 테이블을 아직 찾지 못했습니다.)"),
        "{dead_ends_summary}": ("\n".join(de_lines) or "(없음 — 이전 실패 경험이 없습니다.)"),
        "{tool_execution_history}": (_build_tool_execution_history(reason)),
        "{sample_data_summary}": (_build_sample_summary(reason)),
    }
    for key, value in replacements.items():
        prompt = prompt.replace(key, value)

    variables = {k.strip("{}"): v[:200] for k, v in replacements.items()}
    full_variables = {k.strip("{}"): v for k, v in replacements.items()}
    return prompt, variables, full_variables


def _build_sample_summary(reason: ReasoningState) -> str:
    """후보 테이블별 샘플 데이터 현황을 요약한다.

    0행인 경우도 명시하여 recovery_agent가 같은 테이블을
    반복 샘플링하지 않도록 한다.
    """
    lines: list[str] = []
    for ct in reason.explored_tables:
        if ct.selection_status == SelectionStatus.REJECTED:
            continue
        rows = ct.sample_rows
        if rows:
            cols = list(rows[0].keys())[:5]
            lines.append(
                f"- {ct.table_name}: {len(rows)}행 " f"(컬럼: {', '.join(cols)})",
            )
        else:
            lines.append(
                f"- {ct.table_name}: 0행 (데이터 없음 또는 미조회)",
            )

    return "\n".join(lines) or "(없음)"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 유틸리티
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _count_consecutive_same_failure(
    dead_ends: list[DeadEnd],
    target: FailureType,
) -> int:
    """dead_ends 끝에서부터 target과 동일한 failure_type 연속 횟수."""
    count = 0
    for de in reversed(dead_ends):
        if de.failure_type == target:
            count += 1
        else:
            break
    return count


def _attach_lessons(
    reason: ReasoningState,
    plan: RecoveryPlan | None,
) -> None:
    """lessons_learned를 최신 DeadEnd에 첨부한다."""
    if plan and plan.lessons_learned and reason.dead_ends:
        last_de = reason.dead_ends[-1]
        reason.dead_ends[-1] = last_de.model_copy(
            update={"lessons_learned": plan.lessons_learned},
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 종료 처리
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _finalize_give_up(reason: ReasoningState) -> None:
    """give_up 시 force-generate 또는 실패 처리를 결정한다.

    확신도가 THRESHOLD_FORCE_GENERATE 이상이면 불완전하더라도 SQL 생성을 시도하고,
    미달이면 최종 실패(DONE + FAILURE)로 처리한다.
    """
    score = calculate_readiness(reason)
    if score >= THRESHOLD_FORCE_GENERATE:
        reason.phase = Phase.GENERATING
        reason.is_force_generated = True
    else:
        reason.phase = Phase.DONE
        reason.final_status = FinalStatus.FAILURE
        reason.exploration_summary = _build_failure_summary(reason)


def _build_failure_summary(
    reason: ReasoningState,
) -> str:
    """최종 실패 요약을 생성한다."""
    parts = [
        f"총 {reason.loop_guard.total_tool_calls}회 "
        f"도구 호출, "
        f"{reason.loop_guard.replan_count}회 재계획 시도",
    ]

    if reason.dead_ends:
        parts.append("실패 경로:")
        for de in reason.dead_ends:
            line = f"  - [{de.failure_type}] {de.reason}"
            if de.lessons_learned:
                line += f" (교훈: {de.lessons_learned})"
            parts.append(line)

    unresolved = reason.get_unresolved_knowledge()
    if unresolved:
        parts.append(
            "미해소 용어: " f"{', '.join(ki.key for ki in unresolved)}",
        )

    return "\n".join(parts)
