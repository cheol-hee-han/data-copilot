"""context_interpreter 노드 — 도구 결과를 배치 LLM 해석하고 상태에 반영한다.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

fetcher가 step.raw_result에 저장한 도구 결과를 tool_renderers로 직렬화하여
LLM 배치 해석을 수행하고, 9단계 후처리로 state를 갱신한다.

1. _apply_batch_insights      — step.insight 설정
2. _populate_discovered_facts  — discovered_facts 누적
3. _apply_judgments            — 4종 판정 결과를 state에 적재
4. _apply_observation_data     — 관찰 도구 결과를 테이블 보조 정보로 매칭
5. _hydrate_enrichment         — use_case enrichment → state 적재
6. _cleanup_rejected_knowledge — rejected 테이블 관련 KI 정리
7. _dedup_knowledge_items      — 중복 제거
8. _promote_sampled_confidence — confidence 승격
9. _clear_raw_results          — DONE 스텝의 raw_result = None

위임 구조:
    - 프롬프트: system_prompts.py의 CONTEXT_INTERPRETER_SYSTEM
    - LLM 호출: utils/llm (llm_call_with_parse_retry)
    - 직렬화: tool_renderers.py의 serialize_tool_results_by_step
"""

from __future__ import annotations

from typing import Any

from src.utils.llm.response import extract_json

from src.agents.state.state import (
    BizManualEntry,
    BizTermEntry,
    PipelineState,
    TableMeta,
    UseCaseEntry,
    CodeMeta,
    ConfidenceStatus,
    KnowledgeItem,
    ObservedDateColumn,
    StepStatus,
    SelectionStatus,
)
from src.agents.nodes.reason.tool_renderers import (
    serialize_tool_results_by_step,
    serialize_single_step,
)
from src.agents.nodes.system_prompts import (
    CONTEXT_INTERPRETER_SYSTEM,
)
from src.config import settings
from src.agents.nodes.thinking_modes import LLMNode
from src.utils.llm import llm_call_with_parse_retry, ParseError
from src.utils.llm.prompt import render_prompt
from src.agents.nodes.reason.tools import _TABLE_META_TOOLS
from src.utils.logger import get_logger
from src.utils.truncate import truncate_trace, truncate_log
from src.utils.tracker import (
    LLMInteraction,
    build_llm_reasoning_payload,
    llm_failure_sentinel,
)
from src.utils.tracker.dispatch import (
    dispatch_tracking_event,
    DECISION_TABLE_COMPARISON,
    REASONING_STEP,
)

logger = get_logger(__name__)

_TIME_SLOT_UNSPECIFIED = "(명시되지 않음)"
_TABLE_KEY_PREFIX = "table:"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 배치 해석 결과 모델
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class BatchInterpretResult:
    """배치 LLM 해석 결과.

    Level 0(전체 배치)과 Level 1(스텝별 분할) 모두 동일한 구조로 반환한다.

    Attributes:
        interpretations: 스텝별 해석 결과 dict 목록 (판정·insight 포함).
        knowledge_updates: 해석 과정에서 도출된 KnowledgeItem 목록.
    """

    def __init__(
        self,
        interpretations: list[dict] | None = None,
        knowledge_updates: list[KnowledgeItem] | None = None,
    ) -> None:
        self.interpretations = interpretations or []
        self.knowledge_updates = knowledge_updates or []


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 메인 노드 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


async def context_interpreter_node(state: PipelineState) -> dict:
    """도구 결과를 배치 LLM 해석하고 9단계 후처리로 상태에 반영한다.

    context_retriever가 수집한 raw_result를 tool_renderers로 직렬화하여
    LLM에 전달하고, 판정 결과를 explored_tables/use_cases 등에 반영한다.
    토큰 예산 초과 시 Level 1(스텝별 분할) 모드로 자동 전환된다.
    """
    reason = state.reason.model_copy(deep=True)

    execution_plan = list(reason.execution_plan)
    knowledge_items = list(reason.knowledge_items)
    explored_tables = list(reason.explored_tables)
    explored_use_cases = list(reason.explored_use_cases)
    explored_biz_terms = list(reason.explored_biz_terms)
    explored_biz_manuals = list(reason.explored_biz_manuals)
    code_map = dict(reason.explored_codes)
    discovered_facts = list(reason.discovered_facts)

    # ── Phase 3: 배치 LLM 해석 ──
    time_slot = _extract_time_slot(state.normalized_query)
    nq = state.normalized_query
    rewritten = (
        getattr(nq, "rewritten_query", "")
        if nq else ""
    )
    from src.agents.utils.handoff import normalize_handoff_note

    batch_result, interactions = await _interpret_batch(
        execution_plan,
        state.preprocessed_input,
        time_slot,
        knowledge_items,
        rewritten_query=rewritten,
        session_id=state.session_id,
        turn_id=state.turn_id,
        handoff_note=normalize_handoff_note(state.handoff_note),
    )

    # 1. _apply_batch_insights — step.insight 설정
    _apply_batch_insights(execution_plan, batch_result.interpretations)

    # 2. _populate_discovered_facts — discovered_facts 누적
    _populate_discovered_facts(execution_plan, discovered_facts)

    # 2.5 raw_result → explored_* 신규 적재 (판정 전 항목 생성)
    _hydrate_from_raw_results(
        execution_plan,
        explored_tables,
        explored_use_cases,
        explored_biz_terms,
        explored_biz_manuals,
        code_map,
    )

    # 3. _apply_judgments — 4종 판정 결과를 state에 적재
    _apply_judgments(
        batch_result.interpretations,
        explored_tables,
        explored_use_cases,
        explored_biz_terms,
        explored_biz_manuals,
        execution_plan,
    )

    # ── Tracker: 비교 판정 의사결정 기록 ──
    selected_names = [
        t.table_name
        for t in explored_tables
        if t.selection_status == SelectionStatus.SELECTED
    ]
    rejected_names = [
        t.table_name
        for t in explored_tables
        if t.selection_status == SelectionStatus.REJECTED
    ]
    if rejected_names:
        await dispatch_tracking_event(
            DECISION_TABLE_COMPARISON,
            {
                "node": "batch_interpret",
                "decision_type": "table_comparison",
                "chosen": ", ".join(selected_names),
                "alternatives": rejected_names,
                "confidence": len(selected_names)
                / max(
                    len(selected_names) + len(rejected_names),
                    1,
                ),
                "reason": truncate_trace(
                    "; ".join(
                        f"{t.table_name}: {t.selection_reason}"
                        for t in explored_tables
                        if t.selection_status == SelectionStatus.REJECTED
                    )
                ),
            },
        )

    # 4. _apply_observation_data — 관찰 도구 결과를 테이블 보조 정보로 매칭
    _apply_observation_data(execution_plan, explored_tables)

    # 5. _hydrate_enrichment — SELECTED use_case의 enrichment만 적재
    selected_uc_ids = _collect_selected_use_case_ids(
        batch_result.interpretations,
    )
    _hydrate_enrichment(
        execution_plan,
        explored_tables,
        selected_uc_ids,
        code_map,
    )

    # 6. _cleanup_rejected_knowledge — rejected 테이블 관련 KI 정리
    _cleanup_rejected_knowledge(explored_tables, knowledge_items)

    # 7. LLM UPDATE 항목을 기존 KI에 병합 (id 매칭, 신규 생성 없음)
    _merge_updates_into_items(knowledge_items, batch_result.knowledge_updates)
    # 방어: 이론상 id 중복 없어야 하나, 과거 데이터/이상 상태 대비 dedup 1회
    _dedup_knowledge_items(knowledge_items)

    # 8. _promote_sampled_confidence
    _promote_sampled_confidence(explored_tables, knowledge_items)

    # 9. _clear_raw_results — 모든 DONE 스텝의 raw_result = None
    _clear_raw_results(execution_plan)

    # state 갱신
    reason.knowledge_items = knowledge_items
    reason.explored_tables = explored_tables
    reason.explored_use_cases = explored_use_cases
    reason.explored_biz_terms = explored_biz_terms
    reason.explored_biz_manuals = explored_biz_manuals
    reason.explored_codes = code_map
    reason.execution_plan = execution_plan
    reason.discovered_facts = discovered_facts

    # ── Reasoning Flow 트레이스 ──
    _selected = [
        f"{t.table_name} — {truncate_trace(t.selection_reason or '')}"
        for t in explored_tables
        if t.selection_status == SelectionStatus.SELECTED
    ]
    _rejected = [
        t.table_name
        for t in explored_tables
        if t.selection_status == SelectionStatus.REJECTED
    ]
    _ki_updates = [
        f"{ki.id}: {ki.key} → {ki.status.value}" for ki in knowledge_items
    ]
    _key_insights = discovered_facts[-5:] if discovered_facts else []

    _hyp = reason.current_hypothesis
    parsed_summary = {
        "table_SELECTED": _selected,
        "table_REJECTED": _rejected,
        "knowledge_updates": _ki_updates,
        "key_insights": _key_insights,
    }
    routing_reason = (
        f"테이블 {len(_selected)}건 선정, KI {len(_ki_updates)}건 갱신"
    )
    await _emit_context_interpreter_reasoning_steps(
        interactions=interactions,
        hypothesis_id=_hyp.hypothesis_id if _hyp else "",
        round_num=reason.loop_guard.replan_count,
        parsed_summary=parsed_summary,
        routing_reason=routing_reason,
    )

    return {"reason": reason}


async def _emit_context_interpreter_reasoning_steps(
    *,
    interactions: list[LLMInteraction],
    hypothesis_id: str,
    round_num: int,
    parsed_summary: dict,
    routing_reason: str,
) -> None:
    """Level 0/1 각 LLM 호출마다 REASONING_STEP 이벤트를 방출한다.

    프롬프트 [INPUT] 치환 변수와 [OUTPUT_CONTRACT] 원본 응답을 손실 없이 보존
    (20260422 trace-input-output-redesign §2 권고안 B).
    최종 호출에만 종합 parsed_summary 를 싣는다.
    """
    if not interactions:
        return
    last_idx = len(interactions) - 1
    for idx, interaction in enumerate(interactions):
        is_last = idx == last_idx
        node_name = (
            "context_interpreter"
            if len(interactions) == 1
            else f"context_interpreter:step{idx + 1}"
        )
        summary = parsed_summary if is_last else {"stage": "step_raw"}
        next_node = "readiness_gate" if is_last else f"context_interpreter:step{idx + 2}"
        reason = routing_reason if is_last else "Level1 스텝 해석"
        await dispatch_tracking_event(
            REASONING_STEP,
            build_llm_reasoning_payload(
                node=node_name,
                phase="reason",
                round=round_num,
                hypothesis_id=hypothesis_id,
                interaction=interaction,
                routing={
                    "next_node": next_node,
                    "reason": reason,
                },
                parsed_summary=summary,
            ),
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 프롬프트 직렬화
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _extract_time_slot(normalized_query: Any) -> str:
    """NormalizedQuery에서 시간 조건 문자열을 추출한다."""
    if normalized_query is None:
        return _TIME_SLOT_UNSPECIFIED
    tr = getattr(normalized_query, "time", None)
    if tr is None:
        tr = getattr(normalized_query, "time_range", None)
    if tr is None:
        return _TIME_SLOT_UNSPECIFIED
    raw_text = getattr(tr, "raw_text", None)
    if raw_text:
        return str(raw_text)
    bp = getattr(tr, "base_period", None)
    if bp:
        start = getattr(bp, "absolute_start", "")
        end = getattr(bp, "absolute_end", "")
        label = getattr(bp, "label", "")
        if start and end:
            return f"{label} ({start} ~ {end})"
        if label:
            return label
    return str(tr)


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


def _serialize_unresolved_items(
    knowledge_items: list[KnowledgeItem] | None,
) -> str:
    """CANDIDATE/UNRESOLVED/CONFLICTED 지식 항목을 프롬프트용 문자열로 직렬화한다."""
    if not knowledge_items:
        return "(미해소 항목 없음)"
    lines: list[str] = []
    for ki in knowledge_items:
        prefix, _, label = ki.key.partition(":")

        if ki.status == ConfidenceStatus.UNRESOLVED:
            lines.append(
                f"- ({ki.id}) {ki.key} — {ki.status.value}"
                f"  역할: {_ROLE_DESC.get(prefix, "기타")}",
            )
        elif ki.status == ConfidenceStatus.CANDIDATE:
            lines.append(
                f"- ({ki.id}) {ki.key} — {ki.status.value}"
                f"  역할: {_ROLE_DESC.get(prefix, "기타")}"
                f"  {_VALUE_LABEL.get(prefix, "값")} 후보: {ki.value}"
                f"  후보 판단 사유: {", ".join(ki.evidence) or "미생성"} (출처: {ki.source})",
            )
        elif ki.status == ConfidenceStatus.CONFLICTED:
            lines.append(
                f"- ({ki.id}) {ki.key} — {ki.status.value}"
                f"  역할: {_ROLE_DESC.get(prefix, "기타")}"
                f"  {_VALUE_LABEL.get(prefix, "값")} : {ki.value or "판단 불가"}"
                f"  판단 충돌 사유: {", ".join(ki.evidence) or "미생성"} (출처: {ki.source})",
            )

    return "\n".join(lines) if lines else "(미해소 항목 없음)"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LLM 배치 해석 — Level 0/1 분기
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# 프롬프트 토큰 예산 (렌더링된 도구 결과 기준, 간이 추정)
# LLM_DEFAULT_MAX_TOKENS(1000) * 3(문자/토큰) 기준으로 입력 예산을 설정
_TOKEN_BUDGET_CHARS = 12000


def _estimate_tokens(text: str) -> int:
    """문자 수 기반 간이 토큰 추정 (한국어+영어 혼합, 1토큰 ≈ 3자)."""
    return len(text) // 3


async def _interpret_batch(
    execution_plan: list,
    original_query: str,
    time_slot: str,
    knowledge_items: list[KnowledgeItem] | None = None,
    rewritten_query: str = "",
    session_id: str = "",
    turn_id: str = "",
    handoff_note: str = "(없음)",
) -> tuple[BatchInterpretResult, list[LLMInteraction]]:
    """도구 결과를 LLM 해석한다. 토큰 예산에 따라 Level 0/1을 자동 분기한다.

    Level 0 (기본): 모든 DONE 스텝을 한 프롬프트에 배치 — 교차 참조 가능.
    Level 1 (토큰 초과): 스텝별 개별 호출 + 종합 판정 — 정보 축소 없이 분할.

    CONTINUE handoff_note 는 Level 0 배치 호출에만 주입한다 (§14.3.2).
    Level 1 개별 스텝 해석은 단일 tool_result 단위라 힌트가 과도한 일반화를
    유도할 수 있어 의도적 opt-out.

    Returns:
        (BatchInterpretResult, interactions):
            - 해석 결과 (insight/judgments/knowledge_updates).
            - 각 LLM 호출의 prompt_variables + raw_response 페어.
              Level 0 은 1개, Level 1 은 스텝 수만큼. REASONING_STEP
              payload 구성에 사용된다.
    """
    done_steps = [s for s in execution_plan if s.status == StepStatus.DONE]
    if not done_steps:
        return BatchInterpretResult(), []

    tool_results_str = serialize_tool_results_by_step(execution_plan)

    # 토큰 예산 확인 — Level 0/1 분기
    if len(tool_results_str) > _TOKEN_BUDGET_CHARS:
        logger.info(
            "토큰 예산 초과, Level 1 분할 모드 전환",
            chars=len(tool_results_str),
            budget=_TOKEN_BUDGET_CHARS,
            steps=len(done_steps),
        )
        return await _interpret_level1(
            execution_plan,
            original_query,
            time_slot,
            knowledge_items,
            rewritten_query=rewritten_query,
            session_id=session_id,
            turn_id=turn_id,
        )

    return await _interpret_level0(
        execution_plan,
        tool_results_str,
        original_query,
        time_slot,
        knowledge_items,
        rewritten_query=rewritten_query,
        handoff_note=handoff_note,
    )


async def _interpret_level0(
    execution_plan: list,
    tool_results_str: str,
    original_query: str,
    time_slot: str,
    knowledge_items: list[KnowledgeItem] | None = None,
    rewritten_query: str = "",
    handoff_note: str = "(없음)",
) -> tuple[BatchInterpretResult, list[LLMInteraction]]:
    """Level 0: 전체 배치 1회 호출.

    Returns:
        (BatchInterpretResult, [LLMInteraction]): 해석 결과와 단일 상호작용.
    """
    unresolved_str = _serialize_unresolved_items(knowledge_items)
    # INSERT 차단 가드용 — 기존 KI id 집합 (배치 파서에 주입)
    existing_id_set = {
        ki.id for ki in (knowledge_items or []) if ki.id
    }

    batch_vars = {
        "original_query": original_query or "",
        "rewritten_query": rewritten_query or original_query or "",
        "time_slot": time_slot or _TIME_SLOT_UNSPECIFIED,
        "unresolved_items": unresolved_str,
        "tool_results": tool_results_str,
        "handoff_note": handoff_note,
    }
    render_vars = {f"{{{k}}}": v for k, v in batch_vars.items()}
    prompt, tracked_vars = render_prompt(
        CONTEXT_INTERPRETER_SYSTEM,
        render_vars,
    )

    def _parse_fn(raw_text: str) -> BatchInterpretResult:
        data = extract_json(raw_text)
        if not data:
            raise ValueError("배치 LLM 응답에서 JSON 추출 실패")
        return _parse_batch_result(data, existing_id_set)

    try:
        raw_text, result = await llm_call_with_parse_retry(
            system=prompt,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "[TASK]"
                        "모든 도구 결과를 교차 참조하여 해석하고, "
                        "미해소 항목의 상태를 판정하세요."
                    ),
                },
            ],
            parse_fn=_parse_fn,
            max_tokens=2048,
            timeout=settings.llm_long_timeout,
            node_name=LLMNode.CONTEXT_INTERPRETER,
        )
        interaction = LLMInteraction(
            prompt_variables=tracked_vars,
            raw_response=raw_text,
        )
        return result, [interaction]

    except (ParseError, Exception) as e:
        logger.warning(
            "Level 0 배치 LLM 해석 실패, rule-based fallback",
            error=str(e),
        )
        fallback_interaction = LLMInteraction(
            prompt_variables=tracked_vars,
            raw_response=llm_failure_sentinel("LLM 실패", e),
        )
        return (
            _interpret_batch_fallback(execution_plan),
            [fallback_interaction],
        )


async def _interpret_level1(
    execution_plan: list,
    original_query: str,
    time_slot: str,
    knowledge_items: list[KnowledgeItem] | None = None,
    rewritten_query: str = "",
    session_id: str = "",
    turn_id: str = "",
) -> tuple[BatchInterpretResult, list[LLMInteraction]]:
    """Level 1: 스텝별 개별 호출 + 종합 판정.

    각 스텝을 개별 LLM 호출로 분석한 뒤,
    모든 스텝의 insight와 판정을 모아 종합 판정 1회를 수행한다.

    Returns:
        (BatchInterpretResult, interactions): 스텝별 LLM 호출마다 하나씩.
    """
    done_steps = [s for s in execution_plan if s.status == StepStatus.DONE]
    unresolved_str = _serialize_unresolved_items(knowledge_items)
    # INSERT 차단 가드용 — 기존 KI id 집합
    existing_id_set = {
        ki.id for ki in (knowledge_items or []) if ki.id
    }

    all_interpretations: list[dict] = []
    all_knowledge_updates: list[KnowledgeItem] = []
    step_insights: list[str] = []
    step_interactions: list[LLMInteraction] = []

    # ── 스텝별 개별 호출 ──
    for step in done_steps:
        # cancel 체크: 스텝별 LLM 호출 전
        if session_id and turn_id:
            from src.agents.graph.cancel import check_cancel

            if await check_cancel(session_id, turn_id):
                logger.info("Level1 스텝 루프 중 취소 감지", step=step.step)
                break

        step_text = serialize_single_step(step)
        if not step_text:
            continue

        # 이전 스텝에서 이번 라운드에 도출한 insight 누적
        prev_insights = ""
        if step_insights:
            prev_insights = "\n\n## 이전 스텝 분석 결과\n" + "\n".join(step_insights)

        tool_results_for_step = step_text
        if prev_insights:
            tool_results_for_step = f"{prev_insights}\n\n{step_text}"

        step_vars = {
            "original_query": original_query or "",
            "rewritten_query": rewritten_query or original_query or "",
            "time_slot": time_slot or _TIME_SLOT_UNSPECIFIED,
            "unresolved_items": unresolved_str,
            "tool_results": tool_results_for_step,
        }
        render_vars = {f"{{{k}}}": v for k, v in step_vars.items()}
        step_prompt, tracked_step_vars = render_prompt(
            CONTEXT_INTERPRETER_SYSTEM,
            render_vars,
        )

        def _parse_step(raw_text: str) -> dict:
            data = extract_json(raw_text)
            if not data:
                raise ValueError("스텝 LLM 응답에서 JSON 추출 실패")
            return data

        try:
            step_raw, step_result = await llm_call_with_parse_retry(
                system=step_prompt,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "[TASK]"
                            "이 단일 도구 결과를 해석하고, "
                            "미해소 항목의 상태를 판정하세요."
                        ),
                    },
                ],
                parse_fn=_parse_step,
                max_tokens=1024,
                timeout=settings.llm_long_timeout,
                node_name=LLMNode.CONTEXT_INTERPRETER,
            )
            all_interpretations.append(step_result)
            step_interactions.append(LLMInteraction(
                prompt_variables=tracked_step_vars,
                raw_response=step_raw,
            ))

            # knowledge_updates 추출 — INSERT 차단 가드 통과분만 수용
            for ku in step_result.get("knowledge_updates", []):
                item = _build_knowledge_update(
                    ku, existing_id_set, "Level1해석",
                )
                if item is not None:
                    all_knowledge_updates.append(item)

            # 다음 스텝용 insight 누적
            insight = step_result.get("insight", "")
            if insight:
                step_insights.append(f"- [{step.tool}({step.input})] {insight}")

        except (ParseError, Exception) as e:
            logger.warning(
                "Level 1 스텝별 해석 실패",
                step=step.step,
                tool=step.tool,
                error=str(e),
            )
            all_interpretations.append(
                {
                    "tool_name": step.tool,
                    "tool_input": step.input,
                    "insight": f"{step.tool}({step.input}) Level 1 해석 실패",
                }
            )
            step_interactions.append(LLMInteraction(
                prompt_variables=tracked_step_vars,
                raw_response=llm_failure_sentinel("LLM 실패", e),
            ))

    logger.info(
        "Level 1 스텝별 해석 완료",
        total_steps=len(done_steps),
        interpretations=len(all_interpretations),
    )

    return (
        BatchInterpretResult(
            interpretations=all_interpretations,
            knowledge_updates=all_knowledge_updates,
        ),
        step_interactions,
    )


def _build_knowledge_update(
    ku: dict,
    existing_id_set: set[str],
    default_source: str,
) -> KnowledgeItem | None:
    """LLM 응답 dict → KnowledgeItem 변환 + INSERT 차단 가드.

    Returns None 이면 호출부는 해당 update 를 폐기한다.
    정규화 규칙: 대문자화 + 공백/소괄호 제거.

    Args:
        ku: LLM 응답 knowledge_updates[*] 항목 dict.
        existing_id_set: 기존 reason.knowledge_items 의 id 집합.
        default_source: source 누락 시 사용할 기본 출처명.

    Returns:
        UPDATE 로 수용할 KnowledgeItem (key="", is_critical=False —
        병합 단계에서 기존 KI 의 key/is_critical 이 보존됨).
        폐기 시 None.
    """
    raw_id = (ku.get("id") or "").upper().strip().strip("()").strip()

    # Guard 1: id 누락
    if not raw_id:
        logger.warning(
            "knowledge_updates: id missing — dropped. raw=%s",
            truncate_log(str(ku)),
        )
        return None

    # Guard 2: 형식 오류 (K숫자가 아님)
    if not raw_id.startswith("K") or not raw_id[1:].isdigit():
        logger.warning(
            "knowledge_updates: malformed id %r — dropped", ku.get("id"),
        )
        return None

    # Guard 3: 존재하지 않는 id (INSERT 시도)
    if raw_id not in existing_id_set:
        logger.warning(
            "knowledge_updates: unknown id %s — dropped (INSERT blocked). raw=%s",
            raw_id, truncate_log(str(ku)),
        )
        return None

    return KnowledgeItem(
        id=raw_id,
        key="",
        value=ku.get("value", ""),
        confidence=ku.get("confidence", 0.5),
        status=ku.get("new_status", ConfidenceStatus.CANDIDATE),
        source=ku.get("source", default_source),
        evidence=[ku.get("evidence", "")],
        is_critical=False,
    )


def _parse_batch_result(
    data: dict, existing_id_set: set[str],
) -> BatchInterpretResult:
    """배치 LLM 응답 JSON을 BatchInterpretResult로 파싱한다.

    각 interpretation에 nested된 explored_tables/use_cases/biz_terms/
    biz_manuals 판정과 knowledge_updates를 추출한다.
    knowledge_updates는 _build_knowledge_update 가드를 통과한 항목만 포함.
    """
    knowledge_updates: list[KnowledgeItem] = []

    for interp in data.get("interpretations", []):
        for ku in interp.get("knowledge_updates", []):
            item = _build_knowledge_update(ku, existing_id_set, "배치해석")
            if item is not None:
                knowledge_updates.append(item)

    return BatchInterpretResult(
        interpretations=data.get("interpretations", []),
        knowledge_updates=knowledge_updates,
    )


def _interpret_batch_fallback(
    execution_plan: list,
) -> BatchInterpretResult:
    """배치 LLM 실패 시 실패 사실만 기록한다."""
    interpretations: list[dict] = []

    for step in execution_plan:
        if step.status != StepStatus.DONE:
            continue
        insight = f"{step.tool}({step.input}) LLM 해석 실패"
        step.insight = insight
        interpretations.append(
            {
                "tool_name": step.tool,
                "tool_input": step.input,
                "insight": insight,
            }
        )

    return BatchInterpretResult(
        interpretations=interpretations,
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Phase 4: 해석 결과 반영 — 9단계 후처리
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _apply_batch_insights(
    execution_plan: list,
    interpretations: list[dict],
) -> None:
    """배치 해석의 insight를 각 ExecutionStep에 반영한다."""
    insight_map: dict[str, str] = {}
    for interp in interpretations:
        key = interp.get("tool_input", "")
        if key:
            insight_map[key] = interp.get("insight", "")

    for step in execution_plan:
        if step.status == StepStatus.DONE and not step.insight:
            matched = insight_map.get(step.input, "")
            if matched:
                step.insight = matched


def _populate_discovered_facts(
    execution_plan: list,
    discovered_facts: list[str],
) -> None:
    """성공 스텝의 insight를 discovered_facts에 누적한다."""
    for step in execution_plan:
        if step.status == StepStatus.DONE and step.insight:
            discovered_facts.append(f"[{step.tool}({step.input})] {step.insight}")


def _apply_judgments(
    interpretations: list[dict],
    explored_tables: list[TableMeta],
    explored_use_cases: list[UseCaseEntry],
    explored_biz_terms: list[BizTermEntry],
    explored_biz_manuals: list[BizManualEntry],
    execution_plan: list | None = None,
) -> None:
    """4종 판정 결과(테이블/유사SQL/용어/매뉴얼)를 state에 적재한다."""
    enrichment_index = (
        _build_enrichment_table_index(execution_plan) if execution_plan else {}
    )
    for interp in interpretations:
        _apply_table_judgments(
            interp.get("explored_tables", []),
            explored_tables,
            enrichment_index,
        )
        _apply_use_case_judgments(
            interp.get("explored_use_cases", []),
            explored_use_cases,
        )
        _apply_biz_term_judgments(
            interp.get("explored_biz_terms", []),
            explored_biz_terms,
        )
        _apply_biz_manual_judgments(
            interp.get("explored_biz_manuals", []),
            explored_biz_manuals,
        )


def _build_enrichment_table_index(
    execution_plan: list,
) -> dict[str, dict]:
    """use case enrichment 테이블을 이름 → 메타 dict로 인덱싱한다."""
    index: dict[str, dict] = {}
    for step in execution_plan:
        if step.tool != "search_use_cases" or step.raw_result is None:
            continue
        raw = step.raw_result
        if not isinstance(raw, dict):
            continue
        for uc_data in raw.get("use_cases", []):
            for t_data in uc_data.get("enrichment_tables", []):
                tname = t_data.get("table_name", "")
                if tname and tname not in index:
                    index[tname] = t_data
    return index


def _create_table_from_enrichment(
    t_data: dict,
    reason: str,
) -> TableMeta:
    """enrichment 메타 dict로부터 SELECTED 상태의 TableMeta를 생성한다."""
    valid_fields = {
        k: v for k, v in t_data.items() if k in TableMeta.model_fields
    }
    valid_fields["selection_status"] = SelectionStatus.SELECTED
    valid_fields["selection_reason"] = reason
    return TableMeta(**valid_fields)


def _apply_table_judgments(
    judgments: list[dict],
    explored_tables: list[TableMeta],
    enrichment_index: dict[str, dict] | None = None,
) -> None:
    """LLM 테이블 판정을 TableMeta에 반영한다.

    PENDING과 REFERENCE 상태의 테이블을 판정 대상으로 한다.
    REFERENCE 테이블이 직접 탐색되어 SELECTED 판정을 받으면 승격된다.

    LLM이 enrichment 테이블을 SELECTED 판정했으나 explored_tables에
    아직 등록되지 않은 경우, enrichment 메타에서 생성하여 적재한다.
    """
    _JUDGEABLE = (SelectionStatus.PENDING, SelectionStatus.REFERENCE)
    existing = {t.table_name: t for t in explored_tables}
    for j in judgments:
        tname = j.get("table_name", "")
        status_str = j.get("status", "")
        reason = j.get("reason", "")
        if tname in existing:
            ct = existing[tname]
            if ct.selection_status in _JUDGEABLE:
                ct.selection_status = _parse_selection_status(status_str)
                ct.selection_reason = reason
        elif (
            status_str == "SELECTED"
            and enrichment_index
            and tname in enrichment_index
        ):
            new_table = _create_table_from_enrichment(
                enrichment_index[tname], reason,
            )
            explored_tables.append(new_table)
            existing[tname] = new_table


def _apply_use_case_judgments(
    judgments: list[dict],
    explored_use_cases: list[UseCaseEntry],
) -> None:
    """LLM 유사 SQL 판정을 explored_use_cases에 반영한다."""
    uc_map = {uc.id: uc for uc in explored_use_cases}
    for j in judgments:
        sql_id = j.get("sql_id", "")
        if sql_id in uc_map:
            uc_map[sql_id].relevant = j.get("status", "") == "SELECTED"
            uc_map[sql_id].eval_reason = j.get("reason", "")


def _apply_biz_term_judgments(
    judgments: list[dict],
    explored_biz_terms: list[BizTermEntry],
) -> None:
    """LLM 비즈니스 용어 판정을 BizTermEntry에 반영한다."""
    bt_map = {bt.biz_term_id: bt for bt in explored_biz_terms}
    for j in judgments:
        term_id = j.get("biz_term_id", "")
        if term_id in bt_map:
            bt = bt_map[term_id]
            if bt.selection_status == SelectionStatus.PENDING:
                bt.selection_status = _parse_selection_status(
                    j.get("status", ""),
                )
                bt.selection_reason = j.get("reason", "")


def _apply_biz_manual_judgments(
    judgments: list[dict],
    explored_biz_manuals: list[BizManualEntry],
) -> None:
    """LLM 업무 매뉴얼 판정을 BizManualEntry에 반영한다."""
    bm_map = {bm.biz_manual_id: bm for bm in explored_biz_manuals}
    for j in judgments:
        manual_id = j.get("biz_manual_id", "")
        if manual_id in bm_map:
            bm = bm_map[manual_id]
            if bm.selection_status == SelectionStatus.PENDING:
                bm.selection_status = _parse_selection_status(
                    j.get("status", ""),
                )
                bm.selection_reason = j.get("reason", "")


def _resolve_table_for_step(
    step: Any,
    table_map: dict[str, TableMeta],
) -> TableMeta | None:
    """스텝 입력에서 테이블명을 추출하고 매칭한다."""
    parts = [p.strip() for p in step.input.split(",")]
    first = parts[0]
    bare = first.rpartition(".")[2] if "." in first else first
    return table_map.get(bare)


def _apply_sample_rows(step: Any, table: TableMeta) -> None:
    """get_sample_rows 결과를 테이블에 적재한다."""
    raw = step.raw_result
    if isinstance(raw, list) and table.sample_rows is None:
        table.sample_rows = raw


def _apply_observation_data(
    execution_plan: list,
    explored_tables: list[TableMeta],
) -> None:
    """관찰 도구 결과를 해당 테이블의 보조 정보로 매칭한다."""
    table_map = {t.table_name: t for t in explored_tables}
    for step in execution_plan:
        if step.status != StepStatus.DONE or step.raw_result is None:
            continue
        table = _resolve_table_for_step(step, table_map)
        if not table:
            continue
        handler = _OBS_DISPATCHERS.get(step.tool)
        if handler:
            handler(step, table)


def _parse_step_column_name(step: Any) -> str:
    """스텝 입력의 두 번째 인자(컬럼명)를 추출한다."""
    parts = [p.strip() for p in step.input.split(",")]
    return parts[1] if len(parts) > 1 else ""


def _find_column(
    table: TableMeta,
    col_name: str,
) -> Any | None:
    """테이블에서 컬럼명으로 ColumnInfo를 찾는다."""
    for col in table.columns:
        if col.name == col_name:
            return col
    return None


def _infer_date_pattern(sample: str) -> str:
    """날짜 샘플 값에서 패턴을 추정한다."""
    if len(sample) == 8:
        return "YYYYMMDD"
    if len(sample) == 6:
        return "YYYYMM"
    if len(sample) == 10 and "-" in sample:
        return "YYYY-MM-DD"
    return sample


def _apply_date_distribution(
    step: Any,
    table: TableMeta,
) -> None:
    """get_date_distribution 결과를 ObservedDateColumn으로 적재한다."""
    raw = step.raw_result
    if not isinstance(raw, dict):
        return
    col_name = _parse_step_column_name(step)
    if not col_name:
        return

    dates = raw.get("dates", [])
    if not dates:
        return

    sorted_dates = sorted(str(d) for d in dates)
    date_range = f"{sorted_dates[0]} ~ {sorted_dates[-1]}"
    pattern = _infer_date_pattern(sorted_dates[0])
    recent = raw.get("recent_values", [])

    existing_cols = {odc.column_name for odc in table.observed_date_columns}
    if col_name not in existing_cols:
        table.observed_date_columns.append(
            ObservedDateColumn(
                column_name=col_name,
                date_range=date_range,
                date_pattern=pattern,
                recent_values=[str(v) for v in recent[:10]],
            )
        )


def _apply_column_values(
    step: Any,
    table: TableMeta,
) -> None:
    """get_column_values 결과를 discovered_values에 적재한다."""
    raw = step.raw_result
    if not isinstance(raw, list):
        return
    col_name = _parse_step_column_name(step)
    if not col_name:
        return

    col = _find_column(table, col_name)
    if col is None:
        return
    if col.discovered_values is None:
        col.discovered_values = list(raw)
    else:
        existing = set(col.discovered_values)
        col.discovered_values.extend(v for v in raw if v not in existing)


# 컬럼 프로파일 필드 매핑 (raw_key → col attr, str 변환 여부)
_PROFILE_FIELDS: list[tuple[str, str, bool]] = [
    ("total_rows", "total_rows", False),
    ("non_null_count", "non_null_count", False),
    ("null_count", "null_count", False),
    ("null_rate", "null_rate", False),
    ("distinct_count", "distinct_count", False),
    ("min_val", "min_val", True),
    ("max_val", "max_val", True),
]


def _apply_column_profile(
    step: Any,
    table: TableMeta,
) -> None:
    """get_column_profile 결과를 컬럼 통계 필드에 적재한다."""
    raw = step.raw_result
    if not isinstance(raw, dict):
        return
    col_name = _parse_step_column_name(step)
    if not col_name:
        return

    col = _find_column(table, col_name)
    if col is None:
        return
    for raw_key, attr, to_str in _PROFILE_FIELDS:
        val = raw.get(raw_key)
        if val is not None:
            setattr(col, attr, str(val) if to_str else val)


_OBS_DISPATCHERS: dict[
    str,
    Any,
] = {
    "get_sample_rows": _apply_sample_rows,
    "get_date_distribution": _apply_date_distribution,
    "get_column_values": _apply_column_values,
    "get_column_profile": _apply_column_profile,
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Phase 2.5: raw_result → explored_* 신규 적재
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _hydrate_from_raw_results(
    execution_plan: list,
    explored_tables: list[TableMeta],
    explored_use_cases: list[UseCaseEntry],
    explored_biz_terms: list[BizTermEntry],
    explored_biz_manuals: list[BizManualEntry],
    code_map: dict[str, CodeMeta],
) -> None:
    """DONE 스텝의 raw_result에서 Entry 객체를 생성하여 explored_*에 적재한다.

    _apply_judgments 직전에 호출하여 판정 대상 항목을 먼저 생성한다.
    재진입 시 _clear_raw_results가 raw_result=None으로 초기화하므로
    이미 처리된 스텝은 자동 스킵된다.
    """
    # 중복 방지용 기존 키 집합
    existing_tables = {t.table_name for t in explored_tables}
    existing_uc_ids = {uc.id for uc in explored_use_cases}
    existing_bt_ids = {bt.biz_term_id for bt in explored_biz_terms}
    existing_bm_ids = {bm.biz_manual_id for bm in explored_biz_manuals}

    # ID 채번용 카운터 (기존 항목 수 기반)
    bt_counter = len(explored_biz_terms)
    bm_counter = len(explored_biz_manuals)

    for step in execution_plan:
        if step.status != StepStatus.DONE or step.raw_result is None:
            continue

        if step.tool in _TABLE_META_TOOLS:
            _hydrate_tables_from_raw(
                step.raw_result,
                explored_tables,
                existing_tables,
                source_step=step.step,
                hypothesis_id=step.hypothesis_id,
            )
        elif step.tool == "search_use_cases":
            _hydrate_use_cases_from_raw(
                step.raw_result,
                explored_use_cases,
                existing_uc_ids,
                source_step=step.step,
                hypothesis_id=step.hypothesis_id,
            )
        elif step.tool == "search_biz_terms":
            bt_counter = _hydrate_biz_terms_from_raw(
                step.raw_result,
                explored_biz_terms,
                existing_bt_ids,
                bt_counter,
                step.input,
                source_step=step.step,
                hypothesis_id=step.hypothesis_id,
            )
        elif step.tool == "search_manual":
            bm_counter = _hydrate_biz_manuals_from_raw(
                step.raw_result,
                explored_biz_manuals,
                existing_bm_ids,
                bm_counter,
                step.input,
                source_step=step.step,
                hypothesis_id=step.hypothesis_id,
            )
        elif step.tool == "lookup_code_meta":
            _hydrate_codes_from_raw(step.raw_result, code_map)


def _hydrate_tables_from_raw(
    raw: Any,
    explored_tables: list[TableMeta],
    existing_names: set[str],
    source_step: int = 0,
    hypothesis_id: str = "",
) -> None:
    """search/lookup_table_meta raw_result → TableMeta PENDING 적재."""
    if not isinstance(raw, dict):
        return
    for t_data in raw.get("tables", []):
        tname = t_data.get("table_name", "")
        if not tname or tname in existing_names:
            continue
        valid_fields = {
            k: v for k, v in t_data.items()
            if k in TableMeta.model_fields
        }
        valid_fields["selection_status"] = SelectionStatus.PENDING
        valid_fields["source_step"] = source_step
        valid_fields["hypothesis_id"] = hypothesis_id
        explored_tables.append(TableMeta(**valid_fields))
        existing_names.add(tname)


def _hydrate_use_cases_from_raw(
    raw: Any,
    explored_use_cases: list[UseCaseEntry],
    existing_ids: set[str],
    source_step: int = 0,
    hypothesis_id: str = "",
) -> None:
    """search_use_cases의 raw_result에서 UseCaseEntry를 적재한다."""
    if not isinstance(raw, dict):
        return
    for uc_data in raw.get("use_cases", []):
        uc_id = str(uc_data.get("_point_id", ""))
        if not uc_id or uc_id in existing_ids:
            continue
        explored_use_cases.append(
            UseCaseEntry(
                id=uc_id,
                description=uc_data.get("description", ""),
                sql=uc_data.get("sql", ""),
                domain=uc_data.get("domain", ""),
                score=uc_data.get("score", 0.0),
                point_id=uc_id,
                source_step=source_step,
                hypothesis_id=hypothesis_id,
            )
        )
        existing_ids.add(uc_id)


def _hydrate_biz_terms_from_raw(
    raw: Any,
    explored_biz_terms: list[BizTermEntry],
    existing_ids: set[str],
    counter: int,
    source: str,
    source_step: int = 0,
    hypothesis_id: str = "",
) -> int:
    """search_biz_terms의 raw_result에서 BizTermEntry를 적재한다.

    Returns:
        갱신된 카운터.
    """
    items = raw if isinstance(raw, list) else []
    for item in items:
        term_name = item.get("name", "")
        if not term_name:
            continue
        counter += 1
        bt_id = f"bt_{counter:03d}"
        if bt_id in existing_ids:
            continue
        explored_biz_terms.append(
            BizTermEntry(
                biz_term_id=bt_id,
                term=term_name,
                definition=item.get(
                    "biz_term_definition", "",
                ),
                synonyms=item.get("synonyms", []),
                related_tables=[
                    t for t in [
                        item.get("table_name", ""),
                    ] if t
                ],
                source=source,
                source_step=source_step,
                hypothesis_id=hypothesis_id,
            )
        )
        existing_ids.add(bt_id)
    return counter


def _hydrate_biz_manuals_from_raw(
    raw: Any,
    explored_biz_manuals: list[BizManualEntry],
    existing_ids: set[str],
    counter: int,
    source: str,
    source_step: int = 0,
    hypothesis_id: str = "",
) -> int:
    """search_manual의 raw_result에서 BizManualEntry를 적재한다.

    Returns:
        갱신된 카운터.
    """
    items = raw if isinstance(raw, list) else []
    for item in items:
        content = item.get("content", "") or item.get("text", "")
        if not content:
            continue
        counter += 1
        bm_id = f"bm_{counter:03d}"
        if bm_id in existing_ids:
            continue
        explored_biz_manuals.append(
            BizManualEntry(
                biz_manual_id=bm_id,
                content=content,
                score=item.get("score", 0.0),
                source=source,
                point_id=str(
                    item.get("_point_id", ""),
                ),
                source_step=source_step,
                hypothesis_id=hypothesis_id,
            )
        )
        existing_ids.add(bm_id)
    return counter


def _hydrate_codes_from_raw(
    raw: Any,
    code_map: dict[str, CodeMeta],
) -> None:
    """search_code_meta의 raw_result에서 CodeMeta를 적재한다."""
    items = raw if isinstance(raw, list) else []
    for item in items:
        col_name = item.get("code_field", "")
        if not col_name or col_name in code_map:
            continue
        code_map[col_name] = CodeMeta(
            column_name=col_name,
            column_desc=item.get("code_field_desc", ""),
            codes=item.get("codes", {}),
        )


def _hydrate_tables_from_enrichment(
    tables_data: list[dict],
    explored_tables: list[TableMeta],
    existing_names: set[str],
) -> None:
    """enrichment의 테이블 메타를 explored_tables에 REFERENCE로 추가한다.

    SELECTED된 use_case SQL 해석용 참고 테이블이므로
    LLM 판정 대상이 아니며, SQL 생성에도 직접 사용하지 않는다.
    """
    for t_data in tables_data:
        tname = t_data.get("table_name", "")
        if not tname or tname in existing_names:
            continue
        valid_fields = {k: v for k, v in t_data.items() if k in TableMeta.model_fields}
        valid_fields["selection_status"] = SelectionStatus.REFERENCE
        explored_tables.append(TableMeta(**valid_fields))
        existing_names.add(tname)


def _hydrate_codes_from_enrichment(
    codes_data: dict[str, Any],
    code_map: dict[str, CodeMeta],
) -> None:
    """enrichment의 코드 메타를 code_map에 추가한다."""
    for col_name, code_data in codes_data.items():
        if col_name not in code_map:
            code_map[col_name] = CodeMeta(
                column_name=code_data.get("column_name", col_name),
                column_desc=code_data.get("column_desc", ""),
                codes=code_data.get("codes", {}),
            )


def _collect_selected_use_case_ids(
    interpretations: list[dict],
) -> set[str]:
    """LLM 판정 결과에서 SELECTED use_case id를 수집한다."""
    selected: set[str] = set()
    for interp in interpretations:
        for j in interp.get("explored_use_cases", []):
            if j.get("status", "") == "SELECTED":
                sql_id = j.get("sql_id", "")
                if sql_id:
                    selected.add(sql_id)
    return selected


def _hydrate_enrichment(
    execution_plan: list,
    explored_tables: list[TableMeta],
    selected_uc_ids: set[str],
    code_map: dict[str, CodeMeta],
) -> None:
    """SELECTED use_case의 enrichment tables/codes만 state에 적재한다.

    테이블은 REFERENCE 상태로 추가되어 SQL 생성에 직접 사용되지 않고
    use_case SQL 해석용 참고 정보로만 제공된다.
    코드 메타는 SQL 결과 해석에 활용될 수 있으므로 SELECTED use_case 것만 적재한다.
    """
    existing_names = {t.table_name for t in explored_tables}
    for step in execution_plan:
        if step.tool != "search_use_cases" or step.status != StepStatus.DONE:
            continue
        raw = step.raw_result
        if not isinstance(raw, dict):
            continue
        for uc_data in raw.get("use_cases", []):
            uc_id = str(uc_data.get("_point_id", ""))
            if not uc_id or uc_id not in selected_uc_ids:
                continue
            _hydrate_tables_from_enrichment(
                uc_data.get("enrichment_tables", []),
                explored_tables,
                existing_names,
            )
            _hydrate_codes_from_enrichment(
                uc_data.get("enrichment_codes", {}),
                code_map,
            )


def _cleanup_rejected_knowledge(
    explored_tables: list[TableMeta],
    knowledge_items: list[KnowledgeItem],
) -> None:
    """rejected 테이블 관련 KnowledgeItem을 정리한다."""
    rejected_set = {
        t.table_name
        for t in explored_tables
        if t.selection_status == SelectionStatus.REJECTED
    }
    if rejected_set:
        knowledge_items[:] = [
            ki
            for ki in knowledge_items
            if not _is_rejected_table_knowledge(ki, rejected_set)
        ]
        logger.info(
            "배치 비교 판정: rejected 테이블 관련 KI 정리 완료",
            rejected=list(rejected_set),
        )


def _is_rejected_table_knowledge(
    ki: KnowledgeItem,
    rejected: set[str],
) -> bool:
    """KnowledgeItem이 rejected 테이블에 속하는지 판정한다."""
    if not ki.key.startswith(_TABLE_KEY_PREFIX):
        return False
    table_name = ki.key.removeprefix(_TABLE_KEY_PREFIX)
    return table_name in rejected


def _clear_raw_results(execution_plan: list) -> None:
    """모든 DONE 스텝의 raw_result를 None으로 초기화한다."""
    for step in execution_plan:
        if step.status == StepStatus.DONE:
            step.raw_result = None


def _parse_selection_status(status_str: str) -> SelectionStatus:
    """문자열을 SelectionStatus로 변환한다. 실패 시 PENDING 반환."""
    try:
        return SelectionStatus(status_str)
    except ValueError:
        return SelectionStatus.PENDING


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Phase 6: 중복 제거 + 신뢰도 승격
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _dedup_knowledge_items(knowledge_items: list) -> None:
    """id 기반 dedup. 동일 id에 대해 최고 confidence 항목만 유지. in-place 변형.

    seed 이후 id 는 고정이므로 key 표기 차이(공백 등)에 영향 받지 않는다.
    id 미할당 항목(이론상 발생 안 함 — seed에서 반드시 채번)은 건너뜀.
    """
    best_ki: dict[str, int] = {}
    for i, ki in enumerate(knowledge_items):
        if not ki.id:
            continue
        if ki.id in best_ki:
            existing_idx = best_ki[ki.id]
            if ki.confidence > knowledge_items[existing_idx].confidence:
                best_ki[ki.id] = i
        else:
            best_ki[ki.id] = i

    keep_indices = set(best_ki.values())
    knowledge_items[:] = [
        ki for i, ki in enumerate(knowledge_items) if i in keep_indices
    ]


def _should_promote(
    current: ConfidenceStatus, incoming: ConfidenceStatus,
) -> bool:
    """상태 승격 허용 여부. 단일 호출에서 UNRESOLVED→CONFIRMED 점프는 허용.

    _merge_updates_into_items 에서 기존 KI 의 status 와 UPDATE 가 제안한
    new_status 를 비교해 승격 여부를 판정한다. 현재는 단순 서열 비교로
    충분하며, 단일 근거 CONFIRMED 제약 등 추가 규칙은 confidence_scorer 가
    담당한다.
    """
    order = {
        ConfidenceStatus.UNRESOLVED: 0,
        ConfidenceStatus.CONFLICTED: 0,
        ConfidenceStatus.CANDIDATE: 1,
        ConfidenceStatus.PROBABLE: 2,
        ConfidenceStatus.CONFIRMED: 3,
    }
    return order.get(incoming, 0) > order.get(current, 0)


def _merge_updates_into_items(
    existing: list[KnowledgeItem],
    updates: list[KnowledgeItem],
) -> None:
    """UPDATE 항목을 기존 KI에 병합. id로 매칭. in-place 변형.

    - 기존 KI 의 key, is_critical 은 보존 (UPDATE 는 절대 건드리지 않음)
    - value, source 는 UPDATE 가 비어있지 않으면 덮어씀
    - status 는 _should_promote 에서 허용한 경우에만 승격
    - confidence 는 max 로 누적
    - evidence 는 중복 제외 append
    - updates 중 id 가 existing 에 없으면 폐기 (가드에서 이미 걸러졌으나 방어)
    """
    by_id = {ki.id: ki for ki in existing if ki.id}
    for upd in updates:
        base = by_id.get(upd.id)
        if not base:
            continue
        if upd.value:
            base.value = upd.value
        if _should_promote(base.status, upd.status):
            base.status = upd.status
        base.confidence = max(base.confidence, upd.confidence)
        if upd.source:
            base.source = upd.source
        for ev in upd.evidence:
            if ev and ev not in base.evidence:
                base.evidence.append(ev)


def _promote_sampled_confidence(
    explored_tables: list[TableMeta],
    knowledge_items: list,
) -> None:
    """샘플 데이터가 확보된 테이블의 KnowledgeItem confidence를 승격한다."""
    sampled_table_names = {ct.table_name for ct in explored_tables if ct.sample_rows}
    if not sampled_table_names:
        return

    for ki in knowledge_items:
        if not ki.key.startswith(_TABLE_KEY_PREFIX):
            continue
        table_name = ki.key.removeprefix(_TABLE_KEY_PREFIX)
        if table_name in sampled_table_names and ki.confidence < 0.8:
            ki.confidence = 0.85
            ki.status = ConfidenceStatus.CONFIRMED
            ki.evidence.append("샘플 데이터 확인 완료")
