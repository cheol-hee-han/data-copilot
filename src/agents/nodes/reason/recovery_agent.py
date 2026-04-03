"""recovery_agent 노드 — 실패 후 재계획 전용.

readiness_gate 또는 sql_validator에서 진입하여,
현재 가설을 FAILED 처리하고 dead_end에 기록한 뒤,
LLM 1회 호출로 새 execution_plan을 수립한다.

도구 실행과 결과 해석은 수행하지 않는다.
수립된 execution_plan은 기존 파이프라인(knowledge_fetcher → knowledge_interpreter
→ readiness_gate)에서 실행·해석·평가된다.

흐름:
    1. [Python] Hypothesis 상태 전이 (ACTIVE→FAILED, PENDING 소비)
    2. [Python] DeadEnd 기록
    3. [LLM 1회] 새 execution_plan 수립 (+ 선택적 새 가설 생성)
    4. [Python] Phase 전이 → EXPLORING (knowledge_fetcher로 라우팅)
       또는 give_up → Phase.DONE (result_finalizer로 라우팅)

핵심 함수:
    - recovery_agent_node: 메인 노드 (재계획 전용)

위임 구조:
    - 프롬프트: system_prompts.py의 RECOVERY_AGENT_SYSTEM
"""

from __future__ import annotations

from typing import Any

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

logger = get_logger(__name__)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 메인 노드
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def recovery_agent_node(
    state: PipelineState,
) -> dict:
    """실패 분석 후 새 execution_plan을 수립한다.

    도구 실행·결과 해석은 하지 않는다.
    수립된 plan은 knowledge_fetcher → knowledge_interpreter → readiness_gate에서 처리.
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

    # Step 2: LLM 1회 호출 → 새 execution_plan 수립
    # 루프 가드 검사를 LLM 호출 이전이 아닌 이후에 수행하면
    # PENDING 가설이 소진된 상태에서도 LLM이 새 가설을 생성할 기회를 얻는다.
    plan_result = await _build_recovery_plan(
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
        return {"reason": reason}

    if plan_result is None or plan_result.action == "give_up":
        _attach_lessons(reason, plan_result)
        _finalize_give_up(reason)
        logger.info(
            "recovery_agent: give_up",
            phase=reason.phase.value,
        )
        return {"reason": reason}

    _attach_lessons(reason, plan_result)

    # Step 3: 새 execution_plan 적용
    reason.execution_plan = plan_result.execution_plan
    if plan_result.new_hypothesis:
        reason.hypotheses.append(plan_result.new_hypothesis)
        reason.current_hypothesis = plan_result.new_hypothesis

    # Phase → EXPLORING (knowledge_fetcher로 라우팅)
    reason.phase = Phase.EXPLORING

    logger.info(
        "recovery_agent: 재계획 완료",
        execution_steps=len(reason.execution_plan),
        new_hypothesis=bool(plan_result.new_hypothesis),
        phase=reason.phase.value,
    )

    return {"reason": reason}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Hypothesis 관리
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _handle_hypothesis_transition(
    reason: ReasoningState,
) -> None:
    """현재 가설 FAILED 전환 + PENDING 소비 + DeadEnd 기록."""
    if (
        reason.current_hypothesis
        and reason.current_hypothesis.status
        == HypothesisStatus.ACTIVE
    ):
        failed = reason.current_hypothesis.model_copy()
        failed.status = HypothesisStatus.FAILED
        for i, h in enumerate(reason.hypotheses):
            if h.hypothesis_id == failed.hypothesis_id:
                reason.hypotheses[i] = failed
                break

        reason.dead_ends.append(DeadEnd(
            hypothesis_id=failed.hypothesis_id,
            failure_type=(
                reason.failure_type
                or FailureType.TERM_UNRESOLVABLE
            ),
            reason=reason.failure_reason or "실패 사유 미제공",
        ))

    next_hyp = _consume_next_pending(reason.hypotheses)
    if next_hyp:
        reason.current_hypothesis = next_hyp
    else:
        reason.current_hypothesis = None


def _consume_next_pending(
    hypotheses: list[Any],
) -> Any | None:
    """우선순위 순 PENDING 가설을 소비하여 ACTIVE로 전환."""
    pending = [
        h for h in hypotheses
        if h.status == HypothesisStatus.PENDING
    ]
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
    """recovery_agent LLM 출력 — 재계획 결과."""

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
) -> RecoveryPlan | None:
    """LLM 1회 호출로 새 execution_plan을 수립한다."""
    prompt, variables = _build_prompt(
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
                    "content": "실패 원인을 분석하고 새로운 탐색 계획을 수립하세요.",
                },
            ],
            parse_fn=_parse_fn,
            max_tokens=1024,
            timeout=settings.llm_long_timeout,
            node_name="recovery_agent",
        )
        await record_prompt_variables(variables)
        return plan
    except (ParseError, ValueError, TimeoutError) as e:
        logger.warning(
            "recovery_agent LLM 호출 실패",
            error=str(e),
        )
        return None


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
            steps.append(ExecutionStep(
                step=i + 1,
                tool=step_data["tool"],
                input=step_data.get("input", ""),
                purpose=step_data.get("purpose", ""),
                expected_output=step_data.get(
                    "expected_output", "",
                ),
            ))

    # 새 가설 파싱 (선택적)
    new_hypothesis = None
    hyp_data = data.get("new_hypothesis")
    if isinstance(hyp_data, dict) and hyp_data.get("description"):
        new_hypothesis = Hypothesis(
            hypothesis_id=hyp_data.get(
                "hypothesis_id",
                f"H_R{len(steps)}",
            ),
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

    return RecoveryPlan(
        action="replan",
        lessons_learned=lessons_learned,
        execution_plan=steps,
        new_hypothesis=new_hypothesis,
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 프롬프트 빌더
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _build_prompt(
    reason: ReasoningState,
    *,
    entry_failure_type: FailureType | None = None,
    entry_failure_reason: str | None = None,
) -> tuple[str, dict[str, str]]:
    """recovery_agent 프롬프트 조립."""
    # 진입 경로 설명
    entry_src = reason.recovery_entry_source or "readiness_gate"
    if entry_src == "sql_validator":
        ft = entry_failure_type or "미제공"
        fr = entry_failure_reason or "미제공"
        entry_desc = (
            f"sql_validator에서 진입: SQL 검증 실패.\n"
            f"실패 유형: {ft}\n"
            f"실패 사유: {fr}"
        )
    else:
        entry_desc = (
            "readiness_gate에서 진입: "
            "초기 탐색이 불충분하여 추가 탐색이 필요합니다."
        )

    # 지식 항목 분류
    confirmed = []
    unresolved = []
    for ki in reason.knowledge_items:
        tag = f"[{ki.knowledge_id}] {ki.key}"
        if ki.status in (
            ConfidenceStatus.CONFIRMED,
            ConfidenceStatus.PROBABLE,
        ):
            confirmed.append(
                f"{tag} — {ki.status.value} "
                f"({ki.value}, {ki.source})",
            )
        else:
            unresolved.append(
                f"{tag} — {ki.status.value}",
            )

    # 후보 테이블 요약
    from src.agents.state.state import TableSelectionStatus
    table_lines = []
    for ct in reason.candidate_tables:
        if ct.selection_status == TableSelectionStatus.REJECTED:
            continue
        line = (
            f"- {ct.table_name}"
            f" ({ct.selection_status.value})"
        )
        if ct.description:
            line += f": {ct.description[:80]}"
        col_names = ", ".join(c.name for c in ct.columns[:10])
        if col_names:
            line += f"\n  컬럼: {col_names}"
            if len(ct.columns) > 10:
                line += f" (+{len(ct.columns) - 10})"
        table_lines.append(line)

    # dead_ends 요약
    de_lines = []
    for de in reason.dead_ends:
        line = f"- [{de.failure_type}] {de.reason}"
        if de.lessons_learned:
            line += f" (교훈: {de.lessons_learned[:100]})"
        de_lines.append(line)

    # 치환 (프롬프트 파일의 placeholder와 1:1 매핑)
    prompt = RECOVERY_AGENT_SYSTEM
    replacements = {
        "{entry_source_description}": entry_desc,
        "{confirmed_knowledge}": (
            "\n".join(confirmed) or "(없음)"
        ),
        "{unresolved_items}": (
            "\n".join(unresolved) or "(없음)"
        ),
        "{candidate_tables_summary}": (
            "\n".join(table_lines) or "(없음)"
        ),
        "{dead_ends_summary}": (
            "\n".join(de_lines) or "(없음)"
        ),
        "{exploration_history}": (
            _build_exploration_history(reason)
        ),
        "{discovered_facts}": (
            _build_discovered_facts(reason)
        ),
        "{sample_data_summary}": (
            _build_sample_summary(reason)
        ),
    }
    for key, value in replacements.items():
        prompt = prompt.replace(key, value)

    variables = {
        k.strip("{}"): v[:200] for k, v in replacements.items()
    }
    return prompt, variables


def _build_exploration_history(reason: ReasoningState) -> str:
    """explored_use_cases를 검색 쿼리별로 그루핑하여 요약한다.

    검색 쿼리 → 발견된 유사 SQL 설명 + 관련성 표시.
    searched_queries에는 table_meta 등 다른 도구 검색도 섞여 있으므로,
    explored_use_cases의 _search_query 태그만으로 그루핑한다.
    """
    from collections import defaultdict

    by_query: dict[str, list[dict]] = defaultdict(list)
    for uc in reason.explored_use_cases:
        sq = uc.get("_search_query", "(알 수 없음)")
        by_query[sq].append(uc)

    if not by_query:
        return "(없음)"

    lines: list[str] = []
    for i, (query, ucs) in enumerate(by_query.items(), 1):
        lines.append(f"{i}. 검색: \"{query}\"")
        for uc in ucs:
            desc = (
                uc.get("description", "")
                or uc.get("desc", "")
                or "(설명 없음)"
            )[:100]
            relevant = uc.get("_relevant")
            if relevant is True:
                tag = "관련 ✓"
                reason_text = uc.get(
                    "_eval_reason", "",
                )
                if reason_text:
                    tag += f" — {reason_text[:60]}"
            elif relevant is False:
                tag = "비관련 ✗"
            else:
                tag = "미평가"
            lines.append(f"   - {desc} ({tag})")

    return "\n".join(lines)


def _build_discovered_facts(reason: ReasoningState) -> str:
    """누적 인사이트를 번호 매겨 반환한다."""
    if not reason.discovered_facts:
        return "(없음)"
    return "\n".join(
        f"{i}. {fact}"
        for i, fact in enumerate(reason.discovered_facts, 1)
    )


def _build_sample_summary(reason: ReasoningState) -> str:
    """후보 테이블별 샘플 데이터 현황을 요약한다.

    0행인 경우도 명시하여 recovery_agent가 같은 테이블을
    반복 샘플링하지 않도록 한다.
    """
    from src.agents.state.state import TableSelectionStatus

    lines: list[str] = []
    for ct in reason.candidate_tables:
        if ct.selection_status == TableSelectionStatus.REJECTED:
            continue
        rows = ct.sample_rows
        if rows:
            cols = list(rows[0].keys())[:5]
            lines.append(
                f"- {ct.table_name}: {len(rows)}행 "
                f"(컬럼: {', '.join(cols)})",
            )
        else:
            lines.append(
                f"- {ct.table_name}: 0행 (데이터 없음 또는 미조회)",
            )

    return "\n".join(lines) or "(없음)"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 유틸리티
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

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
    """give_up 시 force-generate 또는 실패 처리."""
    score = calculate_readiness(reason)
    if score >= THRESHOLD_FORCE_GENERATE:
        reason.phase = Phase.GENERATING
        reason.is_force_generated = True
        reason.inference_notes.insert(
            0,
            "확인된 정보가 충분하지 않아 "
            "일부 추론을 포함하여 조회하였습니다. "
            "결과가 예상과 다를 경우 구체적으로 요청해 주세요.",
        )
    else:
        reason.phase = Phase.DONE
        reason.final_status = FinalStatus.FAILURE
        reason.exploration_summary = (
            _build_failure_summary(reason)
        )


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
            "미해소 용어: "
            f"{', '.join(ki.key for ki in unresolved)}",
        )

    return "\n".join(parts)
