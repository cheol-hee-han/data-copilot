"""recovery_planner 노드 — dead-ends 기반 가설 교체 + 새 실행계획 수립.

SQL 검증 실패 또는 confidence 부족으로 replan이 필요할 때 호출된다.
planner 노드(초기 계획)와 완전히 분리되어 재계획만 담당한다.

핵심 전략:
    1. 현재 활성 가설을 FAILED로 전환하고 DeadEnd에 기록
    2. PENDING 가설이 남아있으면 LLM 호출 없이 즉시 다음 가설로 전환
    3. PENDING 가설이 없으면 LLM을 호출하여 새로운 가설을 생성
    4. 새 가설도 생성 불가(give_up=true)면 phase="DONE"으로 종료

실패 맥락 소비:
    이전 노드(sql_validator 또는 confidence_evaluator)가 설정한
    failure_type과 failure_reason을 읽어 DeadEnd를 생성한다.
    recovery_planner 자체에서 실패 원인을 추론하지 않는다.

LLM 입력 (replan 프롬프트):
    - failure_history: dead_ends에서 추출한 이전 실패 이력
    - discovered_facts: 완료된 실행 스텝의 insight 모음
    - confirmed_knowledge: CONFIRMED/PROBABLE 지식 항목
    - unresolved_items: UNRESOLVED/CONFLICTED 용어 목록
    - tried_tables: 세션 레벨 후보 테이블 목록
    - rejected_tables: 부적합 판정 테이블 목록

LLM 출력:
    - lessons_learned: 실패 교훈 → DeadEnd에 저장
    - give_up: 재시도 포기 여부
    - new_hypothesis: 새 가설 (description, strategy, missing_terms)
    - execution_plan: LLM 제안 실행계획 (유효 tool만 필터링하여 우선 사용)

핵심 함수:
    - recovery_planner_node: 메인 노드 함수
    - _build_replan_context: LLM 프롬프트용 컨텍스트 조립 (세션 레벨 state 직접 참조)
    - _generate_new_hypotheses: LLM 호출로 새 가설 + 실행계획 + lessons_learned 생성
    - _parse_replan_execution: LLM 제안 plan 파싱 + TOOL_MAP 검증
    - _build_replan_execution: LLM plan 없을 때 rule-based 실행계획 생성

위임 구조:
    - 프롬프트: system_prompts.py의 RECOVERY_PLANNER_SYSTEM

v2.0 (2026-03-25): LLM 기반 재계획으로 전환 — 외부 프롬프트 사용.
v2.1 (2026-03-29): failure_type/failure_reason 통합, DeadEnd 경량화,
                    _infer 함수 제거, lessons_learned 저장.
"""

from __future__ import annotations

import json
from typing import Any

from src.agents.state.state import (
    ConfidenceStatus,
    FailureType,
    FinalStatus,
    HypothesisStatus,
    Phase,
    PipelineState,
    ReasoningState,
    DeadEnd,
    ExecutionStep,
    Hypothesis,
    TableSelectionStatus,
)
from src.agents.nodes.system_prompts import RECOVERY_PLANNER_SYSTEM
from src.utils.llm import llm_call_with_parse_retry, ParseError
from src.utils.llm.response import extract_json
from src.utils.llm.prompt import render_prompt
from src.utils.logger import get_logger
from src.utils.tracker import record_prompt_variables

logger = get_logger(__name__)


async def recovery_planner_node(state: PipelineState) -> dict:
    """dead_ends를 기반으로 다음 가설을 선택하고 새 실행계획을 수립한다."""
    reason = state.reason.model_copy(deep=True)
    reason.phase = Phase.REPLANNING

    reason.loop_guard = reason.loop_guard.model_copy()
    reason.loop_guard.increment_replan()

    # C-07: 직접 mutation 대신 복사본으로 처리
    hypotheses = [h.model_copy() for h in reason.hypotheses]
    dead_ends = list(reason.dead_ends)

    if (
        reason.current_hypothesis
        and reason.current_hypothesis.status == HypothesisStatus.ACTIVE
    ):
        failed_hyp = reason.current_hypothesis.model_copy()
        failed_hyp.status = HypothesisStatus.FAILED
        for i, h in enumerate(hypotheses):
            if h.hypothesis_id == failed_hyp.hypothesis_id:
                hypotheses[i] = failed_hyp
                break

        # DeadEnd 생성: 이전 노드가 설정한 failure_type/failure_reason을 직접 사용
        dead_ends.append(DeadEnd(
            hypothesis_id=failed_hyp.hypothesis_id,
            failure_type=reason.failure_type or FailureType.TERM_UNRESOLVABLE,
            reason=reason.failure_reason or "실패 사유 미제공",
        ))
        reason.hypotheses = hypotheses
        reason.dead_ends = dead_ends

    # 실패 맥락 소비 완료 → 초기화
    reason.failure_type = None
    reason.failure_reason = None

    pending = [h for h in hypotheses if h.status == HypothesisStatus.PENDING]
    llm_plan: list[ExecutionStep] = []
    lessons: str = ""

    if not pending:
        replan_context = _build_replan_context(
            reason, state.preprocessed_input, dead_ends,
        )
        new_hypotheses, llm_plan, lessons, give_up_reason = (
            await _generate_new_hypotheses(replan_context)
        )

        # lessons_learned를 방금 생성한 DeadEnd에 저장
        if lessons and dead_ends:
            last_de = dead_ends[-1]
            dead_ends[-1] = last_de.model_copy(
                update={"lessons_learned": lessons},
            )
            reason.dead_ends = dead_ends

        if not new_hypotheses:
            # C-09: 가설 소진 시 바로 DONE으로 전환
            reason.phase = Phase.DONE
            reason.final_status = FinalStatus.FAILURE
            reason.exploration_summary = (
                give_up_reason
                or _build_failure_summary(reason, dead_ends)
            )
            reason.current_hypothesis = None
            return {"reason": reason}
        pending = new_hypotheses
        reason.hypotheses = hypotheses + new_hypotheses

    next_hyp = pending[0].model_copy()
    next_hyp.status = HypothesisStatus.ACTIVE
    reason.current_hypothesis = next_hyp

    # LLM 제안 plan 우선, 없으면 rule-based fallback
    if llm_plan:
        execution_plan = llm_plan
    else:
        replan_context = _build_replan_context(
            reason, state.preprocessed_input, dead_ends,
        )
        execution_plan = _build_replan_execution(
            next_hyp, replan_context,
            candidate_tables=reason.candidate_tables,
        )
    reason.execution_plan = execution_plan
    reason.phase = Phase.EXPLORING

    return {"reason": reason}


def _build_replan_context(
    reason: ReasoningState,
    original_query: str,
    dead_ends: list[DeadEnd],
) -> dict[str, Any]:
    """replan 프롬프트에 주입할 컨텍스트를 조립한다.

    DeadEnd에서 빠진 필드(tried_tables 등)는
    세션 레벨 state에서 직접 참조한다.
    """
    context: dict[str, Any] = {
        "original_query": original_query,
    }

    # 실패 이력 (DeadEnd 경량 구조)
    failure_history = [
        {
            "hypothesis": de.hypothesis_id,
            "failure_type": de.failure_type,
            "reason": de.reason,
            "lessons_learned": de.lessons_learned,
        }
        for de in dead_ends
    ]
    context["failure_history"] = failure_history

    # 탐색에서 발견한 사실 (세션 레벨 누적)
    context["discovered_facts"] = list(reason.discovered_facts)

    # 확인된 지식 항목
    confirmed_knowledge = [
        {
            "key": ki.key,
            "value": ki.value,
            "status": ki.status,
            "source": ki.source,
        }
        for ki in reason.knowledge_items
        if ki.status in (ConfidenceStatus.CONFIRMED, ConfidenceStatus.PROBABLE)
    ]
    context["confirmed_knowledge"] = confirmed_knowledge

    # 미해소 항목
    unresolved = [
        f"{ki.key}" + (
            f" (충돌: {'; '.join(ki.evidence[-2:])})"
            if ki.status == ConfidenceStatus.CONFLICTED and ki.evidence
            else ""
        )
        for ki in reason.knowledge_items
        if ki.status in (ConfidenceStatus.UNRESOLVED, ConfidenceStatus.CONFLICTED)
    ]
    context["unresolved_items"] = unresolved

    # 세션 레벨 참조: 중복 방지용 누적 목록
    context["searched_queries"] = reason.searched_queries
    context["sampled_tables"] = [
        ct.table_name for ct in reason.candidate_tables if ct.sample_rows
    ]

    # 세션 레벨 참조: 시도/제외 테이블
    context["tried_tables"] = [
        ct.table_name for ct in reason.candidate_tables
    ]
    context["rejected_tables"] = [
        ct.table_name for ct in reason.candidate_tables
        if ct.selection_status == TableSelectionStatus.REJECTED
    ]

    return context


async def _generate_new_hypotheses(
    replan_context: dict[str, Any],
) -> tuple[list[Hypothesis], list[ExecutionStep], str, str]:
    """LLM을 사용하여 dead-ends를 회피하는 새 가설과 실행계획을 생성한다.

    Returns:
        (가설 리스트, LLM 제안 실행계획, lessons_learned, give_up_reason).
        retry 후에도 실패하면 빈 결과를 반환하여 호출측에서 DONE 처리한다.
    """
    from src.config import settings

    # 프롬프트 조립
    prompt = RECOVERY_PLANNER_SYSTEM
    replacements = {
        "{original_query}": replan_context.get(
            "original_query", "",
        ),
        "{failure_history}": json.dumps(
            replan_context.get("failure_history", []),
            ensure_ascii=False,
        ),
        "{discovered_facts}": "\n".join(
            replan_context.get("discovered_facts", []),
        ) or "(없음)",
        "{confirmed_knowledge}": json.dumps(
            replan_context.get("confirmed_knowledge", []),
            ensure_ascii=False,
        ),
        "{unresolved_items}": ", ".join(
            replan_context.get("unresolved_items", []),
        ) or "(없음)",
        "{searched_queries}": ", ".join(
            replan_context.get("searched_queries", []),
        ) or "(없음)",
        "{sampled_tables}": ", ".join(
            replan_context.get("sampled_tables", []),
        ) or "(없음)",
        "{tried_tables}": ", ".join(
            replan_context.get("tried_tables", []),
        ) or "(없음)",
        "{rejected_tables}": ", ".join(
            replan_context.get("rejected_tables", []),
        ) or "(없음)",
    }
    prompt, tracking_vars = render_prompt(prompt, replacements)

    def _parse_fn(raw_text: str) -> dict[str, Any]:
        data = extract_json(raw_text)
        if not data:
            raise ValueError("recovery LLM 응답에서 JSON 추출 실패")
        return data

    try:
        _, data = await llm_call_with_parse_retry(
            system=prompt,
            messages=[
                {
                    "role": "user",
                    "content": "재계획하세요.",
                },
            ],
            parse_fn=_parse_fn,
            max_tokens=1024,
            timeout=settings.llm_long_timeout,
            node_name="recovery_planner",
        )

        await record_prompt_variables(tracking_vars)
        hypotheses = _parse_replan_response(data)
        llm_plan = _parse_replan_execution(data)
        lessons = data.get("lessons_learned", "")
        give_up_reason = data.get("reason", "") if data.get("give_up") else ""
        return hypotheses, llm_plan, lessons, give_up_reason
    except (ParseError, Exception) as e:
        logger.warning(
            "replan LLM 최종 실패, 재계획 불가",
            error=str(e),
        )
        return [], [], "", ""


def _parse_replan_response(
    data: dict[str, Any] | None,
) -> list[Hypothesis]:
    """파싱된 JSON dict에서 새 가설을 추출한다."""
    if data is None:
        return []

    if data.get("give_up"):
        return []

    h = data.get("new_hypothesis", {})
    if not h or not h.get("description"):
        return []

    priority_map = {
        "high": 0.9, "medium": 0.5, "low": 0.1,
    }
    return [Hypothesis(
        hypothesis_id=h.get("hypothesis_id", "H_NEW"),
        description=h.get("description", ""),
        missing_terms=h.get("missing_terms", []),
        strategy=h.get("strategy", ""),
        priority=priority_map.get(
            h.get("priority", "medium"), 0.5,
        ),
    )]


def _parse_replan_execution(
    data: dict[str, Any] | None,
) -> list[ExecutionStep]:
    """파싱된 JSON dict에서 execution_plan을 추출하고 유효한 tool만 필터링한다."""
    from src.agents.nodes.reason.tools import TOOL_MAP

    if data is None or data.get("give_up"):
        return []

    plan = data.get("execution_plan", [])
    if not isinstance(plan, list) or not plan:
        return []

    steps: list[ExecutionStep] = []
    for s in plan:
        tool = s.get("tool", "")
        if tool not in TOOL_MAP:
            logger.info(
                "recovery LLM이 제안한 도구 스킵 (미등록)",
                tool=tool,
            )
            continue
        steps.append(ExecutionStep(
            step=len(steps) + 1,
            tool=tool,
            input=s.get("input", ""),
            purpose=s.get("purpose", ""),
        ))

    return steps



def _steps_from_candidates(
    candidate_tables: list[Any] | None,
    failed_tables: set[str],
    searched: set[str],
) -> list[ExecutionStep]:
    """candidate_tables 중 미탐색 테이블에 대한 검색 스텝을 생성한다."""
    steps: list[ExecutionStep] = []
    for ct in (candidate_tables or []):
        table = ct.table_name if hasattr(ct, "table_name") else str(ct)
        if table not in failed_tables and table not in searched:
            steps.append(ExecutionStep(
                step=len(steps) + 1,
                tool="search_table_meta",
                input=table,
                purpose=f"새 가설: {table} 테이블 구조 확인",
            ))
    return steps


def _steps_from_missing_terms(
    hypothesis: Hypothesis,
    searched: set[str],
    start_step: int = 1,
) -> list[ExecutionStep]:
    """missing_terms 각각에 대해 개별 검색 스텝을 생성한다."""
    steps: list[ExecutionStep] = []
    step_num = start_step
    for term in hypothesis.missing_terms:
        if term in searched:
            continue
        steps.append(ExecutionStep(
            step=step_num,
            tool="search_table_meta",
            input=term,
            purpose=f"미해소 용어 '{term}' 테이블 검색",
        ))
        step_num += 1

    if not steps:
        # missing_terms가 없거나 모두 이미 검색된 경우
        steps.append(ExecutionStep(
            step=start_step,
            tool="search_table_meta",
            input=hypothesis.strategy,
            purpose="새 가설 기반 테이블 직접 검색",
        ))
    return steps


def _build_replan_execution(
    hypothesis: Hypothesis,
    replan_context: dict[str, Any],
    candidate_tables: list[Any] | None = None,
) -> list[ExecutionStep]:
    """새 가설에 대한 실행계획을 수립한다.

    검증된 테이블(candidate_tables)로 메타 조회 스텝을 생성한다.
    """
    searched = set(replan_context.get("searched_queries", []))
    failed_tables: set[str] = set(replan_context.get("tried_tables", []))

    steps = _steps_from_candidates(candidate_tables, failed_tables, searched)
    if not steps:
        steps = _steps_from_missing_terms(hypothesis, searched)

    step_num = steps[-1].step + 1
    steps.append(ExecutionStep(
        step=step_num,
        tool="search_use_cases",
        input=hypothesis.strategy,
        purpose="새 가설 기반 활용사례 재검색",
    ))

    return steps


def _build_failure_summary(
    reason: ReasoningState,
    dead_ends: list[DeadEnd],
) -> str:
    """최종 실패 요약을 생성한다."""
    parts = [
        f"총 {reason.loop_guard.total_tool_calls}회 "
        f"도구 호출, "
        f"{reason.loop_guard.replan_count}회 재계획 시도",
    ]

    if dead_ends:
        parts.append("실패 경로:")
        for de in dead_ends:
            line = f"  - [{de.failure_type}] {de.reason}"
            if de.lessons_learned:
                line += f" (교훈: {de.lessons_learned})"
            parts.append(line)

    unresolved = reason.get_unresolved_knowledge()
    if unresolved:
        parts.append(
            "미해소 용어: "
            f"{', '.join(ki.key for ki in unresolved)}",
        )

    return "\n".join(parts)
