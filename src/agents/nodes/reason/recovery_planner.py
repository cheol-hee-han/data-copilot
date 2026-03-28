"""recovery_planner 노드 — dead-ends 기반 가설 교체 + 새 실행계획 수립.

planner 노드와 완전히 분리된 별도 노드.
이미 탐색한 것은 재탐색하지 않으며, confirmed knowledge는 재사용한다.

v2.0 (2026-03-25): LLM 기반 재계획으로 전환 — 외부 프롬프트 사용.
"""

from __future__ import annotations

import json
import re
from typing import Any

from src.agents.state.state import (
    PipelineState,
    ReasoningState,
    DeadEnd,
    ExecutionStep,
    FailureType,
    Hypothesis,
)
from src.agents.nodes.system_prompts import REASON_REPLAN
from src.utils.logger import get_logger
from src.utils.tracker import record_prompt_variables

logger = get_logger(__name__)


async def recovery_planner_node(state: PipelineState) -> dict:
    """dead_ends를 기반으로 다음 가설을 선택하고 새 실행계획을 수립한다."""
    reason = state.reason.model_copy(deep=True)
    reason.phase = "REPLANNING"

    reason.loop_guard = reason.loop_guard.model_copy()
    reason.loop_guard.increment_replan()

    # C-07: 직접 mutation 대신 복사본으로 처리
    hypotheses = [h.model_copy() for h in reason.hypotheses]
    dead_ends = list(reason.dead_ends)

    if (
        reason.current_hypothesis
        and reason.current_hypothesis.status == "ACTIVE"
    ):
        failed_hyp = reason.current_hypothesis.model_copy()
        failed_hyp.status = "FAILED"
        for i, h in enumerate(hypotheses):
            if h.hypothesis_id == failed_hyp.hypothesis_id:
                hypotheses[i] = failed_hyp
                break
        dead_ends.append(DeadEnd(
            hypothesis_id=failed_hyp.hypothesis_id,
            reason=_infer_failure_reason(reason),
            tried_tables=[
                ct.table_name for ct in reason.candidate_tables
            ],
            rejected_tables=list(reason.rejected_tables),
            tried_terms=failed_hyp.missing_terms,
            failure_type=_infer_failure_type(reason),
        ))
        reason.hypotheses = hypotheses
        reason.dead_ends = dead_ends

    pending = [h for h in hypotheses if h.status == "PENDING"]

    if not pending:
        replan_context = _build_replan_context(
            reason, state.preprocessed_input, dead_ends,
        )
        new_hypotheses = await _generate_new_hypotheses(
            replan_context,
        )
        if not new_hypotheses:
            # C-09: 가설 소진 시 바로 DONE으로 전환
            reason.phase = "DONE"
            reason.final_status = "failure"
            reason.exploration_summary = (
                _build_failure_summary(reason, dead_ends)
            )
            reason.current_hypothesis = None
            return {"reason": reason}
        pending = new_hypotheses
        reason.hypotheses = hypotheses + new_hypotheses

    next_hyp = pending[0].model_copy()
    next_hyp.status = "ACTIVE"
    reason.current_hypothesis = next_hyp

    replan_context = _build_replan_context(
        reason, state.preprocessed_input, dead_ends,
    )
    execution_plan = _build_replan_execution(
        next_hyp, replan_context,
        candidate_tables=reason.candidate_tables,
    )
    reason.execution_plan = execution_plan
    reason.current_step_index = 0
    reason.phase = "EXPLORING"

    return {"reason": reason}


def _build_replan_context(
    reason: ReasoningState,
    original_query: str,
    dead_ends: list[DeadEnd],
) -> dict[str, Any]:
    """replan 프롬프트에 주입할 컨텍스트를 조립한다."""
    context: dict[str, Any] = {
        "original_query": original_query,
    }

    failure_history = [
        {
            "hypothesis": de.hypothesis_id,
            "reason": de.reason,
            "failure_type": de.failure_type,
            "tried_tables": de.tried_tables,
            "rejected_tables": de.rejected_tables,
            "tried_terms": de.tried_terms,
        }
        for de in dead_ends
    ]
    context["failure_history"] = failure_history

    discovered_facts = [
        f"[{step.tool}] {step.insight}"
        for step in reason.execution_plan
        if step.status == "DONE" and step.insight
    ]
    context["discovered_facts"] = discovered_facts

    confirmed_knowledge = [
        {
            "key": ki.key,
            "value": ki.value,
            "status": ki.status,
            "source": ki.source,
        }
        for ki in reason.knowledge_items
        if ki.status in ("CONFIRMED", "PROBABLE")
    ]
    context["confirmed_knowledge"] = confirmed_knowledge

    unresolved = [
        f"{ki.key}" + (
            f" (충돌: {'; '.join(ki.evidence[-2:])})"
            if ki.status == "CONFLICTED" and ki.evidence
            else ""
        )
        for ki in reason.knowledge_items
        if ki.status in ("UNRESOLVED", "CONFLICTED")
    ]
    context["unresolved_items"] = unresolved
    context["searched_queries"] = reason.searched_queries
    context["sampled_tables"] = reason.sampled_tables

    return context


def _infer_failure_reason(
    reason: ReasoningState,
) -> str:
    """현재 상태에서 실패 사유를 추론한다."""
    if reason.sql_validation_result:
        match reason.sql_validation_result.overall:
            case "FAIL_STRUCTURAL":
                return (
                    "SQL 구조적 오류 — "
                    "테이블/컬럼 재탐색 필요"
                )
            case "FAIL_EMPTY":
                return (
                    "실행 결과 0건 — "
                    "조건 또는 테이블 부적절"
                )
            case "FAIL_DB_ERROR":
                return "DB 실행 오류"
            case _:
                return "SQL 검증 실패"

    unresolved = reason.get_unresolved_knowledge()
    if unresolved:
        return (
            "미해소 용어: "
            f"{', '.join(ki.key for ki in unresolved)}"
        )

    return "탐색 스텝 소진 — 충분한 정보 미확보"


def _infer_failure_type(
    reason: ReasoningState,
) -> FailureType:
    """실패 유형을 추론한다."""
    if reason.sql_validation_result:
        match reason.sql_validation_result.overall:
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
    """LLM을 사용하여 dead-ends를 회피하는 새 가설을 생성한다."""
    from src.config import settings
    from src.utils.llm import get_llm_client

    # 프롬프트 조립
    prompt = REASON_REPLAN
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
    }
    for key, value in replacements.items():
        prompt = prompt.replace(key, value)

    try:
        client = get_llm_client()
        response = await client.messages.create(
            model=settings.llm_model,
            max_tokens=1024,
            timeout=settings.llm_long_timeout,
            system=prompt,
            messages=[
                {
                    "role": "user",
                    "content": "재계획하세요.",
                },
            ],
        )

        record_prompt_variables({
            k.strip("{}"): v for k, v in replacements.items()
        })
        raw = response.content[0].text
        return _parse_replan_response(raw)
    except Exception as e:
        logger.warning(
            "replan LLM 실패, rule-based fallback",
            error=str(e),
        )
        return _generate_hypotheses_fallback(replan_context)


def _parse_replan_response(raw: str) -> list[Hypothesis]:
    """LLM 응답에서 새 가설을 파싱한다."""
    json_match = re.search(r"\{[\s\S]*\}", raw)
    if not json_match:
        return []

    try:
        data = json.loads(json_match.group())
    except json.JSONDecodeError:
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


def _generate_hypotheses_fallback(
    replan_context: dict[str, Any],
) -> list[Hypothesis]:
    """LLM 실패 시 rule-based fallback 가설."""
    failed_tables: set[str] = set()
    for fh in replan_context.get("failure_history", []):
        failed_tables.update(fh.get("tried_tables", []))

    query = replan_context.get("original_query", "")
    unresolved = replan_context.get("unresolved_items", [])
    ft_count = len(failed_tables)

    new_hyps: list[Hypothesis] = []

    new_hyps.append(Hypothesis(
        hypothesis_id=f"H_RPT_{ft_count}",
        description="보고서 SQL에서 유사 패턴 참조",
        strategy="보고서 SQL 검색으로 테이블/조인 구조 참고",
        priority=0.6,
    ))

    if unresolved:
        new_hyps.append(Hypothesis(
            hypothesis_id=f"H_MAN_{ft_count}",
            description="업무 매뉴얼에서 용어 정의 확인",
            missing_terms=[
                u.split(":")[0] if ":" in u else u
                for u in unresolved[:3]
            ],
            strategy=(
                "매뉴얼에서 업무 규정/산출식 확인 후 재탐색"
            ),
            priority=0.4,
        ))

    keywords = query.split()[:3] if query else []
    if keywords:
        new_hyps.append(Hypothesis(
            hypothesis_id=f"H_KW_{ft_count}",
            description="키워드 변형 직접 탐색",
            strategy="질의 키워드 조합으로 새 테이블 탐색",
            priority=0.3,
        ))

    return new_hyps


def _build_replan_execution(
    hypothesis: Hypothesis,
    replan_context: dict[str, Any],
    candidate_tables: list[Any] | None = None,
) -> list[ExecutionStep]:
    """새 가설에 대한 실행계획을 수립한다.

    검증된 테이블(candidate_tables)로 메타 조회 스텝을 생성한다.
    """
    steps: list[ExecutionStep] = []
    step_num = 1

    searched = set(
        replan_context.get("searched_queries", []),
    )
    failed_tables: set[str] = set()
    for fh in replan_context.get("failure_history", []):
        failed_tables.update(fh.get("tried_tables", []))

    # candidate_tables에서 아직 검색하지 않은 테이블 조회
    for ct in (candidate_tables or []):
        table = ct.table_name if hasattr(ct, "table_name") else str(ct)
        if table not in failed_tables and table not in searched:
            steps.append(ExecutionStep(
                step=step_num,
                tool="search_table_meta",
                input=table,
                purpose=(
                    f"새 가설: {table} 테이블 구조 확인"
                ),
            ))
            step_num += 1

    if not steps:
        # 가설의 missing_terms에서 검색 키워드 추출
        search_kw = (
            " ".join(hypothesis.missing_terms[:3])
            if hypothesis.missing_terms
            else hypothesis.strategy
        )
        steps.append(ExecutionStep(
            step=1,
            tool="search_table_meta",
            input=search_kw,
            purpose="새 가설 기반 테이블 직접 검색",
        ))
        steps.append(ExecutionStep(
            step=2,
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
            parts.append(
                f"  - [{de.failure_type}] {de.reason}",
            )

    unresolved = reason.get_unresolved_knowledge()
    if unresolved:
        parts.append(
            "미해소 용어: "
            f"{', '.join(ki.key for ki in unresolved)}",
        )

    return "\n".join(parts)
