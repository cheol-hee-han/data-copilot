"""context_explorer 노드 — 실행계획 스텝을 내부 루프로 순차 실행.

탐색은 노드 내부에서 루프로 처리하고, 판단만 외부 노드(confidence_evaluator)로 분리한다.
도구 실행(Phase 1~2)과 LLM 해석(Phase 3)을 분리하여 LLM 호출 횟수를 최소화한다.

6단계 처리 흐름:
    Phase 1: 도구 전부 실행 (rule-based, LLM 없음)
    Phase 2: 관찰 데이터 수집 (날짜 분포, 샘플 — DB 쿼리)
    Phase 3: 배치 LLM 해석 1회 (해석 + 비교 판정, 관찰 데이터 포함)
    Phase 4: 해석 결과 반영 (KnowledgeItem 승격, CandidateTable 갱신)
    Phase 4.5: 유사 SQL 관련성 주석 부착 (relevant_use_cases 기반)
    Phase 5: 테이블 판정 결과 마킹 (CandidateTable.selection_status)
    Phase 6: confidence 승격 + readiness 조기 탈출 체크

LLM 배치 해석 출력 필드:
    - insight: 도구 결과 해석 (ExecutionStep.insight에 저장)
    - evidence: 근거 정보 (KnowledgeItem.evidence에 누적)
    - selected/rejected: 테이블별 판정 사유 (dict 리스트)
    - relevant_use_cases: 관련 유사 SQL 판정 (sql_id + reason)
    - inferred_entity_scope/functional_usage/data_refresh_hint: LLM 추론 메타
    - inferred_key_date_column: 날짜 기준 컬럼 추론

핵심 함수:
    - context_explorer_node: 메인 노드 함수 (6단계 오케스트레이션)
    - _execute_steps: Phase 1 — PENDING 스텝 순차 실행
    - _collect_observations: Phase 2 — 날짜/샘플 관찰 데이터 수집
    - _interpret_batch: Phase 3 — 배치 LLM 해석 (1회 호출)
    - _apply_batch_insights: Phase 4 — 해석 결과를 state에 반영
    - _remove_unsuitable_tables: Phase 5 — 부적합 테이블 필터링
    - _merge_llm_inferred_fields: CandidateTable에 LLM 추론 필드 병합

위임 구조:
    - 도구 실행: reason/tools.py (execute_tool, TOOL_MAP)
    - 프롬프트: system_prompts.py의 BATCH_INTERPRET_SYSTEM

v2.0 (2026-03-25): LLM 기반 도구 결과 해석 추가 — 외부 프롬프트 사용.
v3.0 (2026-03-27): 배치 해석 구조로 전환 — 도구 실행과 LLM 해석 분리.
"""

from __future__ import annotations

import json
import re
from typing import Any

from src.utils.llm.response import extract_json

from src.agents.state.state import (
    PipelineState,
    CandidateTable,
    CodeMeta,
    ColumnInfo,
    ConfidenceStatus,
    KeyDateColumn,
    KnowledgeItem,
    ObservedDateColumn,
    Phase,
    ReasoningState,
    StepStatus,
    TableSelectionStatus,
    MAX_TOOL_CALLS,
)
from src.connectors.manager import get_connector_manager
from src.agents.nodes.reason.tools import (
    execute_tool,
    get_date_distribution,
    get_sample_rows,
    detect_date_pattern,
)
from src.agents.nodes.system_prompts import (
    BATCH_INTERPRET_SYSTEM,
    TABLE_COMPARISON_SYSTEM,
)
from src.services.confidence_scorer import ReadinessVerdict, evaluate_readiness
from src.config import settings
from src.utils.llm import llm_call_with_parse_retry, ParseError
from src.utils.llm.prompt import render_prompt
from src.utils.logger import get_logger
from src.utils.truncate import truncate_log, truncate_trace
from src.utils.tracker.dispatch import (
    dispatch_tracking_event,
    record_prompt_variables,
    CONTEXT_TOOL_SUCCESS,
    CONTEXT_TOOL_ERROR,
    DECISION_TABLE_COMPARISON,
    LLM_CALL,
    LLM_PROMPT_VARIABLES,
)

logger = get_logger(__name__)


# 중복 방지 대상 도구 목록 (이미 검색한 입력은 스킵)
_DEDUP_TOOLS: frozenset[str] = frozenset({
    "search_use_cases", "search_table_meta",
    "search_code_meta", "search_manual", "search_glossary",
})


def _should_skip_step(
    step: Any,
    searched_queries: list[str],
    candidate_tables: list[CandidateTable],
) -> bool:
    """중복 실행 여부를 판정한다.

    검색 계열 도구는 동일 입력이 이미 검색됐으면 스킵.
    get_sample_data는 해당 테이블에 이미 sample_rows가 있으면 스킵.
    """
    if step.tool in _DEDUP_TOOLS and step.input in searched_queries:
        step.status = StepStatus.SKIPPED
        step.insight = "이미 검색한 쿼리 — 스킵"
        return True
    if step.tool == "get_sample_data":
        table_name = step.input.split(",")[0].strip()
        if any(t.table_name == table_name and t.sample_rows for t in candidate_tables):
            step.status = StepStatus.SKIPPED
            step.insight = "이미 샘플 조회한 테이블 — 스킵"
            return True
    return False


async def _run_step(
    step: Any,
    searched_queries: list[str],
    candidate_tables: list,
    explored_use_cases: list,
    code_map: dict[str, CodeMeta] | None = None,
) -> tuple[Any, Any, int]:
    """단일 스텝을 실행하고 rule-based 결과만 반영한다.

    LLM 해석은 수행하지 않는다 (배치 해석으로 이관됨).
    반환값: (step, result, 소비한 tool_calls 수)
    """
    import time as _time

    result = None
    _t0 = _time.perf_counter()
    try:
        result = await execute_tool(step.tool, step.input)
        _elapsed = (_time.perf_counter() - _t0) * 1000
        step.status = StepStatus.DONE

        if step.tool != "get_sample_data":
            searched_queries.append(step.input)

        # rule-based CandidateTable 추출 (LLM 없이)
        new_tables = _extract_tables(step, result)
        candidate_tables.extend(new_tables)

        if step.tool == "search_use_cases" and result:
            explored_use_cases.extend(result)

        # 코드 메타 결과를 code_map에 축적 (컬럼 단위 중복 방지)
        if step.tool == "search_code_meta" and result and code_map is not None:
            for item in result:
                col = item.get("code_field", "")
                if col and col not in code_map:
                    code_map[col] = CodeMeta(
                        column_name=col,
                        column_desc=item.get("code_field_desc", ""),
                        codes=item.get("codes", {}),
                    )

        # ── 추적: 도구 실행 성공 ──
        result_count = len(result) if isinstance(result, list) else 1
        logger.info(
            "도구 실행 완료",
            tool=step.tool,
            input=truncate_log(step.input),
            results=result_count,
            new_tables=len(new_tables),
            latency_ms=round(_elapsed, 1),
        )

        await dispatch_tracking_event(CONTEXT_TOOL_SUCCESS, {
            "source": step.tool,
            "query": truncate_trace(step.input),
            "results_count": result_count,
            "results_summary": [
                f"결과 {result_count}건 수집 (배치 해석 대기)",
            ],
            "latency_ms": round(_elapsed, 1),
        })

    except Exception as e:
        _elapsed = (_time.perf_counter() - _t0) * 1000
        step.status = StepStatus.FAILED
        step.insight = f"도구 실행 실패: {e}"
        logger.warning(
            "도구 실행 실패",
            tool=step.tool,
            input=truncate_log(step.input),
            error=str(e),
            latency_ms=round(_elapsed, 1),
        )

        await dispatch_tracking_event(CONTEXT_TOOL_ERROR, {
            "source": step.tool,
            "query": truncate_trace(step.input),
            "results_count": 0,
            "results_summary": [
                truncate_trace(f"실패: {e}"),
            ],
            "latency_ms": round(_elapsed, 1),
            "status": "error",
        })

    return step, result, 1


def _is_ready_to_generate(
    reason: ReasoningState,
    knowledge_items: list,
    candidate_tables: list,
    explored_use_cases: list,
    execution_plan: list,
) -> bool:
    """현재 누적 상태로 SQL 생성 가능 여부를 판정한다."""
    temp_reason = ReasoningState(
        knowledge_items=knowledge_items,
        candidate_tables=candidate_tables,
        explored_use_cases=explored_use_cases,
        query_decomposition=reason.query_decomposition,
        execution_plan=execution_plan,
        loop_guard=reason.loop_guard,
        hypotheses=reason.hypotheses,
        current_hypothesis=reason.current_hypothesis,
    )
    return evaluate_readiness(temp_reason) == ReadinessVerdict.GENERATE


async def context_explorer_node(state: PipelineState) -> dict:
    """실행계획의 스텝들을 6 Phase 구조로 실행한다.

    Phase 1: 도구 전부 실행 (rule-based만, LLM 없음)
    Phase 2: 관찰 데이터 수집 (날짜 분포, 샘플 — DB 쿼리)
    Phase 3: 배치 LLM 해석 1회 (해석 + 비교 판정, 관찰 데이터 포함)
    Phase 4: 해석 결과 반영
    Phase 5: 부적합 테이블 제거
    Phase 6: confidence 승격 + readiness 체크
    """
    reason = state.reason.model_copy(deep=True)
    reason.phase = Phase.EXPLORING

    # fast-path 실패로 진입한 경우 stale failure 컨텍스트 초기화
    if reason.fast_path_triggered:
        reason.failure_type = None
        reason.failure_reason = None
        reason.fast_path_triggered = False

    execution_plan = list(reason.execution_plan)
    knowledge_items = list(reason.knowledge_items)
    candidate_tables = list(reason.candidate_tables)
    searched_queries = list(reason.searched_queries)
    discovered_facts = list(reason.discovered_facts)
    explored_use_cases = list(reason.explored_use_cases)
    code_map = dict(reason.code_map)
    total_tool_calls = reason.loop_guard.total_tool_calls

    # ── Phase 1: 도구 전부 실행 (rule-based만, LLM 없음) ──
    collected_results: list[tuple[Any, Any]] = []

    for step in execution_plan:
        if step.status != StepStatus.PENDING or total_tool_calls >= MAX_TOOL_CALLS:
            continue

        if _should_skip_step(step, searched_queries, candidate_tables):
            continue

        executed_step, result, calls = await _run_step(
            step, searched_queries,
            candidate_tables, explored_use_cases,
            code_map,
        )
        total_tool_calls += calls

        if result is not None:
            collected_results.append((executed_step, result))

    loop_guard = reason.loop_guard.model_copy()
    loop_guard.total_tool_calls = total_tool_calls

    reason.execution_plan = execution_plan
    reason.searched_queries = searched_queries
    reason.discovered_facts = discovered_facts
    reason.explored_use_cases = explored_use_cases
    reason.code_map = code_map
    reason.loop_guard = loop_guard

    # ── Phase 2: 관찰 데이터 수집 (DB 쿼리, 전체 대상) ──
    # 날짜 분포/샘플은 DB 쿼리(수십ms)로 비용 무시 가능하며,
    # 비교 판정의 정확도를 높이는 핵심 입력이므로 배치 해석 전에 수집한다.
    await _observe_all_date_distributions(candidate_tables)
    await _sample_unsampled_tables(candidate_tables)

    # ── Phase 3: 배치 LLM 해석 (1회, 관찰 데이터 포함) ──
    time_slot = _extract_time_slot(state.normalized_query)
    batch_result = await _interpret_batch(
        collected_results,
        candidate_tables,
        state.preprocessed_input,
        time_slot,
        knowledge_items,
    )

    # ── Phase 4: 해석 결과 반영 ──
    knowledge_items.extend(batch_result.knowledge_updates)
    _merge_llm_inferred_fields(candidate_tables, batch_result.new_tables)

    # 배치 해석의 insight를 각 step에 반영 (tracker/로깅용)
    _apply_batch_insights(execution_plan, batch_result.interpretations)

    # discovered_facts 누적 (성공 스텝의 insight만, 대상 포함)
    for step in execution_plan:
        if step.status == StepStatus.DONE and step.insight:
            discovered_facts.append(
                f"[{step.tool}({step.input})] {step.insight}"
            )

    # ── Phase 4.5: 유사 SQL 관련성 주석 부착 ──
    _annotate_use_case_relevance(
        reason.explored_use_cases,
        batch_result.relevant_use_cases,
    )

    # ── Phase 5: 판정 결과를 CandidateTable에 마킹 ──
    selected_map: dict[str, str] = {
        t["table_name"]: t.get("reason", "")
        for t in batch_result.selected if isinstance(t, dict)
    }
    rejected_map: dict[str, str] = {
        t["table_name"]: t.get("reason", "")
        for t in batch_result.rejected if isinstance(t, dict)
    }
    for ct in candidate_tables:
        if ct.table_name in selected_map:
            ct.selection_status = TableSelectionStatus.SELECTED
            ct.selection_reason = selected_map[ct.table_name]
        elif ct.table_name in rejected_map:
            ct.selection_status = TableSelectionStatus.REJECTED
            ct.selection_reason = rejected_map[ct.table_name]

    if rejected_map:
        rejected_set = set(rejected_map.keys())
        knowledge_items[:] = [
            ki for ki in knowledge_items
            if not _is_rejected_table_knowledge(ki, rejected_set)
        ]
        logger.info(
            "배치 비교 판정 완료",
            selected=list(selected_map.keys()),
            rejected=batch_result.rejected,
        )

        # ── Tracker: 비교 판정 의사결정 기록 ──
        await dispatch_tracking_event(DECISION_TABLE_COMPARISON, {
            "node": "batch_interpret",
            "decision_type": "table_comparison",
            "chosen": ", ".join(selected_map.keys()),
            "alternatives": list(rejected_map.keys()),
            "confidence": len(selected_map) / max(
                len(selected_map) + len(rejected_map), 1,
            ),
            "reason": truncate_trace("; ".join(
                f"{name}: {reason}"
                for name, reason in rejected_map.items()
            )),
        })

    # ── Phase 6: 중복 제거 + confidence 승격 + readiness 체크 ──
    _dedup_knowledge_items(knowledge_items)
    _promote_sampled_confidence(candidate_tables, knowledge_items)

    reason.knowledge_items = knowledge_items
    reason.candidate_tables = candidate_tables

    return {"reason": reason}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 배치 해석 함수 (v3.0)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class BatchInterpretResult:
    """배치 LLM 해석 결과."""

    def __init__(
        self,
        interpretations: list[dict] | None = None,
        knowledge_updates: list[KnowledgeItem] | None = None,
        new_tables: list[dict] | None = None,
        selected: list[dict] | None = None,
        rejected: list[dict] | None = None,
        relevant_use_cases: list[dict] | None = None,
    ) -> None:
        self.interpretations = interpretations or []
        self.knowledge_updates = knowledge_updates or []
        self.new_tables = new_tables or []
        self.selected = selected or []
        self.rejected = rejected or []
        self.relevant_use_cases = relevant_use_cases or []


def _extract_time_slot(normalized_query: Any) -> str:
    """NormalizedQuery에서 시간 조건 문자열을 추출한다."""
    if normalized_query is None:
        return "(명시되지 않음)"
    # time 또는 time_range 필드 접근
    tr = getattr(normalized_query, "time", None)
    if tr is None:
        tr = getattr(normalized_query, "time_range", None)
    if tr is None:
        return "(명시되지 않음)"
    raw_text = getattr(tr, "raw_text", None)
    if raw_text:
        return raw_text
    # base_period에서 추출
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


def _serialize_unresolved_items(
    knowledge_items: list[KnowledgeItem] | None,
) -> str:
    """UNRESOLVED/CONFLICTED 지식 항목을 프롬프트용 문자열로 직렬화한다."""
    if not knowledge_items:
        return "(미해소 항목 없음)"
    lines: list[str] = []
    for ki in knowledge_items:
        if ki.status not in (
            ConfidenceStatus.UNRESOLVED,
            ConfidenceStatus.CONFLICTED,
        ):
            continue
        status_label = ki.status.value
        desc = ki.value or "DB 표현 미확인"
        lines.append(
            f"- {ki.key} ({status_label}) — {desc}",
        )
    return "\n".join(lines) if lines else "(미해소 항목 없음)"


def _serialize_tool_results(
    collected_results: list[tuple[Any, Any]],
) -> str:
    """수집된 도구 결과를 프롬프트용 문자열로 직렬화한다."""
    blocks: list[str] = []
    for step, result in collected_results:
        if result is None:
            continue
        result_str = json.dumps(
            result, ensure_ascii=False, default=str,
        )
        blocks.append(
            f"### 도구: {step.tool}\n"
            f"- 입력: {step.input}\n"
            f"- 목적: {step.purpose or ''}\n"
            f"- 결과:\n{result_str}\n"
        )
    return "\n".join(blocks) if blocks else "(도구 결과 없음)"


def _serialize_table_observations(
    candidate_tables: list[CandidateTable],
) -> str:
    """후보 테이블의 관찰 데이터를 프롬프트용 문자열로 직렬화한다.

    _build_table_block을 재활용하여 메타 원본 + 관찰 데이터를 포함한다.
    """
    if not candidate_tables:
        return "(후보 테이블 없음)"
    lines: list[str] = []
    for table in candidate_tables:
        lines.extend(_build_table_block(table))
    return "\n".join(lines)


async def _interpret_batch(
    collected_results: list[tuple[Any, Any]],
    candidate_tables: list[CandidateTable],
    original_query: str,
    time_slot: str,
    knowledge_items: list[KnowledgeItem] | None = None,
) -> BatchInterpretResult:
    """모든 도구 결과를 배치로 LLM 해석하고 비교 판정한다.

    교차 참조로 해석 정확도를 높이고, selected/rejected를 한 번에 판정한다.
    미해소 지식 항목이 있으면 프롬프트에 포함하여 LLM이 해소를 시도한다.
    실패 시 rule-based fallback으로 전환한다.
    """
    if not collected_results:
        return BatchInterpretResult()

    tool_results_str = _serialize_tool_results(collected_results)
    table_obs_str = _serialize_table_observations(candidate_tables)
    unresolved_str = _serialize_unresolved_items(knowledge_items)

    batch_vars = {
        "original_query": original_query or "",
        "time_slot": time_slot or "(명시되지 않음)",
        "unresolved_items": unresolved_str,
        "tool_results": tool_results_str,
        "table_observations": table_obs_str,
    }
    render_vars = {f"{{{k}}}": v for k, v in batch_vars.items()}
    prompt, tracked_vars = render_prompt(BATCH_INTERPRET_SYSTEM, render_vars)
    await record_prompt_variables(tracked_vars)

    def _parse_fn(raw_text: str) -> BatchInterpretResult:
        """LLM 응답을 BatchInterpretResult로 파싱한다."""
        data = extract_json(raw_text)
        if not data:
            raise ValueError("배치 LLM 응답에서 JSON 추출 실패")
        return _parse_batch_result(data)

    try:
        _, result = await llm_call_with_parse_retry(
            system=prompt,
            messages=[
                {"role": "user", "content": "모든 결과를 통합 분석하세요."},
            ],
            parse_fn=_parse_fn,
            max_tokens=2048,
            timeout=settings.llm_long_timeout,
            node_name="batch_interpret",
        )
        await dispatch_tracking_event(LLM_PROMPT_VARIABLES, {
            "variables": batch_vars,
        })

        return result

    except (ParseError, Exception) as e:
        logger.warning(
            "배치 LLM 해석 실패, rule-based fallback",
            error=str(e),
        )
        return _interpret_batch_fallback(collected_results)


def _parse_batch_result(data: dict) -> BatchInterpretResult:
    """배치 LLM 응답 JSON을 BatchInterpretResult로 파싱한다."""
    knowledge_updates: list[KnowledgeItem] = []
    new_tables: list[dict] = []

    for interp in data.get("interpretations", []):
        for ku in interp.get("knowledge_updates", []):
            knowledge_updates.append(KnowledgeItem(
                key=ku.get("key", ""),
                value=ku.get("value", ""),
                confidence=ku.get("confidence", 0.5),
                status=ku.get("new_status", ConfidenceStatus.CANDIDATE),
                source=ku.get("source", "배치해석"),
                evidence=[ku.get("evidence", "")],
                is_critical=ku.get("is_critical", False),
            ))
        new_tables.extend(interp.get("new_tables", []))

    return BatchInterpretResult(
        interpretations=data.get("interpretations", []),
        knowledge_updates=knowledge_updates,
        new_tables=new_tables,
        selected=data.get("selected", []),
        rejected=data.get("rejected", []),
        relevant_use_cases=data.get("relevant_use_cases", []),
    )


def _interpret_batch_fallback(
    collected_results: list[tuple[Any, Any]],
) -> BatchInterpretResult:
    """배치 LLM 실패 시 실패 사실만 기록한다.

    잘못된 rule-based 판단이 후속 노드에 전파되는 것을 방지한다.
    knowledge_updates를 생성하지 않으므로 confidence_evaluator가
    정보 부족으로 REPLAN 또는 추가 탐색을 판정한다.
    """
    interpretations: list[dict] = []

    for step, _result in collected_results:
        insight = f"{step.tool}({step.input}) LLM 해석 실패"
        step.insight = insight
        interpretations.append({
            "tool_name": step.tool,
            "tool_input": step.input,
            "insight": insight,
        })

    return BatchInterpretResult(
        interpretations=interpretations,
    )


def _annotate_use_case_relevance(
    explored_use_cases: list[dict],
    relevant_use_cases: list[dict],
) -> None:
    """배치 LLM의 relevant_use_cases 결과를 explored_use_cases에 부착한다.

    relevant_use_cases 목록에 있는 use_case는 _relevant=True + _eval_reason 부착.
    목록에 없는 use_case는 _relevant=False.
    """
    relevant_map = {
        e["sql_id"]: e.get("reason", "")
        for e in relevant_use_cases
        if "sql_id" in e
    }
    for uc in explored_use_cases:
        uc_id = uc.get("id", uc.get("_id", ""))
        if uc_id in relevant_map:
            uc["_relevant"] = True
            uc["_eval_reason"] = relevant_map[uc_id]
        else:
            uc["_relevant"] = False
            uc["_eval_reason"] = ""


def _is_rejected_table_knowledge(
    ki: KnowledgeItem,
    rejected: set[str],
) -> bool:
    """KnowledgeItem이 rejected 테이블에 속하는지 판정한다."""
    if not ki.key.startswith("table:"):
        return False
    table_name = ki.key.removeprefix("table:")
    return table_name in rejected


def _apply_batch_insights(
    execution_plan: list,
    interpretations: list[dict],
) -> None:
    """배치 해석의 insight를 각 ExecutionStep에 반영한다.

    tracker/로깅에서 step.insight를 참조하므로,
    배치 결과에서 tool_input 기준으로 매칭하여 반영한다.
    """
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


# ── 행내표준 날짜 접미사 (PK 컬럼에서 기준 날짜 컬럼을 식별하는 데 사용) ──
DATE_SUFFIXES: list[str] = ["YMD", "YM", "YY", "DT", "DATE"]

# ── 한글 기준 날짜 키워드 (alt_name 보조 식별에 사용) ──
KOREAN_DATE_KEYWORDS: list[str] = [
    "기준일", "기준년월", "기준년월일", "기준년", "거래일", "실행일",
]


def _identify_key_date_columns(pk_columns: list[str]) -> list[KeyDateColumn]:
    """PK 컬럼에서 행내표준 날짜 접미사로 기준 컬럼을 식별한다.

    예: STRD_YMD → suffix="YMD", source="pk_rule"
    """
    result: list[KeyDateColumn] = []
    for col in pk_columns:
        col_upper = col.upper()
        for suffix in DATE_SUFFIXES:
            if col_upper.endswith(f"_{suffix}") or col_upper == suffix:
                result.append(KeyDateColumn(
                    column_name=col,
                    suffix=suffix,
                    source="pk_rule",
                ))
                break
    return result


def _infer_suffix_from_column_name(name: str) -> str:
    """컬럼명에서 행내표준 날짜 접미사를 추론한다.

    매칭되는 접미사가 없으면 빈 문자열을 반환한다.
    """
    name_upper = name.upper()
    for s in DATE_SUFFIXES:
        if name_upper.endswith(f"_{s}") or name_upper == s:
            return s
    return ""


def _matches_korean_date_keyword(alt: str) -> bool:
    """alt_name이 기준 날짜 키워드를 포함하는지 확인한다."""
    return any(kw in alt for kw in KOREAN_DATE_KEYWORDS)


def _identify_key_date_by_alt_name(columns: list[dict]) -> list[KeyDateColumn]:
    """한글 컬럼명(alt_name)에서 기준 날짜 컬럼을 보조 식별한다.

    PK 접미사 기반 식별이 실패했을 때 호출된다.
    조건: alt_name이 KOREAN_DATE_KEYWORDS 중 하나를 포함하는 컬럼을 수집한다.
    """
    result: list[KeyDateColumn] = []
    for col in columns:
        alt = col.get("alt_name", "")
        name = col.get("name", "")
        if not alt or not _matches_korean_date_keyword(alt):
            continue
        result.append(KeyDateColumn(
            column_name=name,
            suffix=_infer_suffix_from_column_name(name),
            source="alt_name_rule",
        ))
    return result


def _parse_meta_columns(
    raw_cols: Any,
) -> tuple[list[ColumnInfo], list[str]]:
    """메타 응답의 columns 필드를 파싱한다.

    반환: (ColumnInfo 목록, PK 컬럼명 목록)
    """
    if not isinstance(raw_cols, list):
        return [], []
    column_infos: list[ColumnInfo] = []
    pk_columns: list[str] = []
    for c in raw_cols:
        if not isinstance(c, dict) or not c.get("name"):
            continue
        name = c["name"]
        is_pk = bool(c.get("is_pk"))
        column_infos.append(ColumnInfo(
            name=name,
            alt_name=c.get("alt_name", ""),
            description=c.get("description", ""),
            col_type=c.get("type", ""),
            is_pk=is_pk,
        ))
        if is_pk:
            pk_columns.append(name)
    return column_infos, pk_columns


def _resolve_key_date_columns(
    pk_columns: list[str],
    raw_cols: Any,
) -> list[KeyDateColumn]:
    """PK 접미사 → alt_name 순으로 기준 날짜 컬럼을 식별한다."""
    key_date_cols = _identify_key_date_columns(pk_columns)
    if not key_date_cols:
        key_date_cols = _identify_key_date_by_alt_name(
            raw_cols if isinstance(raw_cols, list) else [],
        )
    return key_date_cols


def _extract_tables(
    step: Any,
    result: Any,
) -> list[CandidateTable]:
    """메타 응답에서 CandidateTable을 추출한다 (rule-based).

    pipeline_table_meta.json 기준 신규 필드(name, description, columns[].name,
    columns[].alt_name, columns[].is_pk)를 우선 참조하고,
    하위 호환을 위해 table_name / table_description도 폴백으로 지원한다.

    PK 컬럼에서 행내표준 날짜 접미사로 key_date_columns를 식별한다.
    """
    if step.tool != "search_table_meta":
        return []
    if not isinstance(result, list):
        return []

    tables: list[CandidateTable] = []
    for meta in result:
        ct = CandidateTable.from_meta(meta)
        if ct is None:
            continue

        # schema_name이 없으면 커넥터의 기본 스키마 사용
        if not ct.schema_name:
            db = get_connector_manager().get_query_db(db_source=ct.db_source)
            ct.schema_name = db.default_schema

        # PK 접미사 기반 기준 날짜 컬럼 식별
        raw_cols = meta.get("columns", [])
        _, pk_columns = _parse_meta_columns(raw_cols)
        ct.key_date_columns = _resolve_key_date_columns(pk_columns, raw_cols)

        tables.append(ct)
    return tables



def _merge_llm_inferred_fields(
    candidate_tables: list[CandidateTable],
    llm_tables: list[dict],
) -> None:
    """LLM이 추론한 3측면 필드를 기존 CandidateTable에 인플레이스 병합한다.

    llm_tables 항목 스키마:
        table_name: str — rule-based CandidateTable과 매칭하는 키
        entity_scope: str — 테이블이 다루는 엔티티 범위 (예: "개인여신 계약 단위")
        functional_usage: str — 테이블의 기능적 용도 (예: "잔액 집계용")
        data_refresh_hint: str — 갱신 주기 힌트 (예: "일별 적재")

    각 필드를 CandidateTable의 독립 필드에 매핑한다.
    매칭되지 않는 llm_tables 항목은 무시한다.
    """
    llm_map = {
        t.get("table_name", ""): t
        for t in llm_tables
        if t.get("table_name")
    }
    for ct in candidate_tables:
        llm_t = llm_map.get(ct.table_name)
        if not llm_t:
            continue
        if llm_t.get("entity_scope"):
            ct.inferred_entity_scope = llm_t["entity_scope"]
        if llm_t.get("functional_usage"):
            ct.inferred_functional_usage = llm_t["functional_usage"]
        if llm_t.get("data_refresh_hint"):
            ct.inferred_data_refresh_hint = llm_t["data_refresh_hint"]


# ── 탐색 루프 후처리 함수 ─────────────────────────────────────────────


async def _observe_all_date_distributions(
    candidate_tables: list[CandidateTable],
) -> None:
    """모든 CandidateTable의 기준 컬럼별 날짜 분포를 조회한다.

    context_explorer_node의 탐색 루프 종료 후 호출.
    key_date_columns가 없거나 inferred_key_date_column만 있는 경우도 처리.
    조회 실패 시 graceful fallback — 기존 candidate_tables 유지.
    """
    for table in candidate_tables:
        # 1순위: rule-based 기준 컬럼
        date_cols = list(table.key_date_columns)

        # 2순위: LLM fallback 기준 컬럼 (rule-based 결과 없을 때만 사용)
        if not date_cols and table.inferred_key_date_column:
            date_cols = [KeyDateColumn(
                column_name=table.inferred_key_date_column,
                suffix="",
                source="llm_fallback",
            )]

        for kdc in date_cols:
            try:
                distinct_dates = await get_date_distribution(
                    table.table_name, kdc.column_name,
                    schema_name=table.schema_name,
                    db_source=table.db_source,
                )
                if not distinct_dates:
                    continue
                dates = sorted(distinct_dates)
                table.observed_date_columns.append(ObservedDateColumn(
                    column_name=kdc.column_name,
                    date_range=f"{dates[0]} ~ {dates[-1]}",
                    date_pattern=detect_date_pattern(dates),
                ))
            except Exception as e:
                logger.warning(
                    "날짜 분포 조회 실패, 스킵",
                    table=table.table_name,
                    column=kdc.column_name,
                    error=str(e),
                )


async def _sample_unsampled_tables(
    candidate_tables: list[CandidateTable],
) -> None:
    """아직 샘플 조회하지 않은 후보 테이블의 샘플 데이터를 조회한다.

    context_explorer_node 후처리에서 rule-based로 자동 호출.
    비교 판정 전에 실행하여 샘플 데이터를 비교 프롬프트에 포함한다.
    """
    for table in candidate_tables:
        if table.sample_rows:
            continue
        try:
            rows = await get_sample_rows(
                table.table_name,
                schema_name=table.schema_name,
                db_source=table.db_source,
            )
            table.sample_rows = rows
            if rows:
                logger.info(
                    "샘플 데이터 조회 완료",
                    table=table.table_name,
                    rows=len(rows),
                )
        except Exception as e:
            logger.warning(
                "샘플 데이터 조회 실패, 스킵",
                table=table.table_name,
                error=str(e),
            )


def _dedup_knowledge_items(knowledge_items: list) -> None:
    """같은 key의 KI가 여러 건이면 최고 confidence 항목만 유지한다."""
    best_ki: dict[str, int] = {}
    for i, ki in enumerate(knowledge_items):
        if ki.key in best_ki:
            existing_idx = best_ki[ki.key]
            if ki.confidence > knowledge_items[existing_idx].confidence:
                best_ki[ki.key] = i
        else:
            best_ki[ki.key] = i

    keep_indices = set(best_ki.values())
    knowledge_items[:] = [
        ki for i, ki in enumerate(knowledge_items)
        if i in keep_indices
    ]


def _promote_sampled_confidence(
    candidate_tables: list[CandidateTable],
    knowledge_items: list,
) -> None:
    """샘플 데이터가 확보된 테이블의 KnowledgeItem confidence를 승격한다.

    샘플 데이터 확인 = 실제 데이터 존재 확인 → 0.8 이상으로 승격.
    """
    sampled_table_names = {
        ct.table_name for ct in candidate_tables if ct.sample_rows
    }
    if not sampled_table_names:
        return

    for ki in knowledge_items:
        if not ki.key.startswith("table:"):
            continue
        table_name = ki.key.removeprefix("table:")
        if table_name in sampled_table_names:
            if ki.confidence < 0.8:
                ki.confidence = 0.85
                ki.status = ConfidenceStatus.CONFIRMED
                ki.evidence.append("샘플 데이터 확인 완료")


def _group_by_keyword(
    candidate_tables: list[CandidateTable],
) -> tuple[list[list[CandidateTable]], set[str]]:
    """inferred_entity_scope 기반 한글 키워드로 유사 테이블 그룹을 만든다.

    반환: (그룹 목록, 이미 그룹에 속한 테이블명 집합)
    """
    keyword_to_tables: dict[str, list[CandidateTable]] = {}
    for t in candidate_tables:
        scope = t.inferred_entity_scope or t.alt_name or ""
        for kw in re.findall(r"[가-힣]{2,}", scope):
            keyword_to_tables.setdefault(kw, []).append(t)

    grouped_tables: set[str] = set()
    groups: list[list[CandidateTable]] = []
    for tables in keyword_to_tables.values():
        if len(tables) < 2:
            continue
        group = [t for t in tables if t.table_name not in grouped_tables]
        if len(group) >= 2:
            groups.append(group)
            grouped_tables.update(t.table_name for t in group)

    return groups, grouped_tables


def _group_by_prefix(
    candidate_tables: list[CandidateTable],
    already_grouped: set[str],
) -> list[list[CandidateTable]]:
    """테이블명 접두사(앞 3개 토큰)로 그룹을 만든다.

    already_grouped에 없는 테이블만 대상으로 한다 (fallback).
    """
    ungrouped = [
        t for t in candidate_tables
        if t.table_name not in already_grouped
    ]
    if len(ungrouped) < 2:
        return []

    prefix_map: dict[str, list[CandidateTable]] = {}
    for t in ungrouped:
        parts = t.table_name.split("_")
        prefix = "_".join(parts[:3]) if len(parts) >= 3 else t.table_name
        prefix_map.setdefault(prefix, []).append(t)

    return [tables for tables in prefix_map.values() if len(tables) >= 2]


def _find_comparison_groups(
    candidate_tables: list[CandidateTable],
) -> list[list[CandidateTable]]:
    """비교가 필요한 유사 테이블 그룹을 반환한다.

    1차: inferred_entity_scope에서 도메인 키워드 공유 (_group_by_keyword)
    2차 (fallback): 테이블명 접두사 매칭 (_group_by_prefix)
    """
    if len(candidate_tables) < 2:
        return []

    groups, grouped_tables = _group_by_keyword(candidate_tables)
    groups.extend(_group_by_prefix(candidate_tables, grouped_tables))
    return groups


async def _run_table_comparison(
    groups: list[list[CandidateTable]],
    candidate_tables: list[CandidateTable],
    original_query: str,
    normalized_query: Any,
) -> None:
    """유사 테이블 그룹별로 LLM 비교 판정을 실행하고 rejected 테이블을 제거한다.

    그룹별로 LLM 1회 호출하여 선택/거절 테이블을 판정한다.
    LLM 실패 시 graceful fallback — 모든 후보 테이블을 그대로 유지.
    """
    # 시간 조건 추출 (normalized_query.time_range 안전하게 접근)
    time_slot = ""
    has_time_range = (
        normalized_query is not None
        and hasattr(normalized_query, "time_range")
    )
    if has_time_range:
        tr = normalized_query.time_range
        if tr is not None:
            time_slot = getattr(tr, "raw_text", "") or str(tr)

    def _parse_comparison(raw: str) -> dict:
        data = extract_json(raw)
        if not data:
            raise ValueError("JSON 추출 실패")
        return data

    for group in groups:
        table_block = _build_comparison_block(group)

        cmp_vars = {
            "original_query": original_query or "",
            "time_slot": time_slot or "(명시되지 않음)",
            "table_comparison_block": table_block,
        }
        render_vars = {f"{{{k}}}": v for k, v in cmp_vars.items()}
        prompt, tracked_vars = render_prompt(TABLE_COMPARISON_SYSTEM, render_vars)

        try:
            _, result = await llm_call_with_parse_retry(
                system=prompt,
                messages=[{"role": "user", "content": "테이블을 비교 판정하세요."}],
                parse_fn=_parse_comparison,
                max_tokens=512,
                timeout=settings.llm_default_timeout,
                node_name="context_explorer_table_cmp",
            )
            await record_prompt_variables(tracked_vars)
            rejected = set(result.get("rejected", []))

            # rejected 테이블을 candidate_tables에서 인플레이스 제거
            candidate_tables[:] = [
                t for t in candidate_tables
                if t.table_name not in rejected
            ]

            logger.info(
                "테이블 비교 판정 완료",
                selected=result.get("selected", []),
                rejected=list(rejected),
                reason=result.get("reason", ""),
            )
        except Exception as e:
            # LLM 실패 시 기존 동작 유지 (모든 후보 그대로)
            logger.warning("테이블 비교 판정 실패, 스킵", error=str(e))


def _resolve_observed_source_tag(
    odc: ObservedDateColumn,
    key_date_columns: list[KeyDateColumn],
) -> str:
    """ObservedDateColumn의 출처 태그를 반환한다.

    key_date_columns에서 동일 컬럼명을 찾아 source가 llm_fallback이면
    'LLM 추론'을, 그 외엔 '관찰'을 반환한다.
    """
    kdc_match = next(
        (k for k in key_date_columns if k.column_name == odc.column_name),
        None,
    )
    if kdc_match is not None and kdc_match.source == "llm_fallback":
        return "LLM 추론"
    return "관찰"


def _format_date_columns(table: CandidateTable) -> list[str]:
    """테이블의 날짜 컬럼 정보를 프롬프트 라인 목록으로 변환한다.

    관찰 결과(observed_date_columns) 우선, 없으면 메타 원본(key_date_columns) 사용.
    """
    lines: list[str] = []
    if table.observed_date_columns:
        lines.append("- 기준 컬럼 (관찰):")
        for odc in table.observed_date_columns:
            tag = _resolve_observed_source_tag(odc, table.key_date_columns)
            lines.append(
                f"  - {odc.column_name}: {odc.date_range}, "
                f"{odc.date_pattern} ({tag})"
            )
    elif table.key_date_columns:
        lines.append("- 기준 컬럼 (메타 원본):")
        for kdc in table.key_date_columns:
            lines.append(f"  - {kdc.column_name} (PK, 접미사: {kdc.suffix})")
    return lines


def _build_table_block(table: CandidateTable) -> list[str]:
    """단일 CandidateTable을 프롬프트 라인 목록으로 조립한다."""
    header = f"### {table.qualified_name}"
    if table.alt_name:
        header += f" ({table.alt_name})"
    lines: list[str] = [header]
    if table.description:
        lines.append(f'- 테이블 설명: "{table.description}" (메타 원본)')
    lines.extend(_format_date_columns(table))

    if table.columns:
        col_labels = [
            f"{c.name}({c.alt_name})" if c.alt_name else c.name
            for c in table.columns
        ]
        lines.append(f"- 컬럼: {', '.join(col_labels)} (메타 원본)")
    if table.inferred_entity_scope:
        lines.append(f'- 엔티티 범위: "{table.inferred_entity_scope}" (LLM 추론)')
    if table.inferred_functional_usage:
        lines.append(f'- 기능적 용도: "{table.inferred_functional_usage}" (LLM 추론)')
    if table.inferred_data_refresh_hint:
        lines.append(f'- 갱신 주기: "{table.inferred_data_refresh_hint}" (LLM 추론)')
    if table.sample_rows:
        lines.append(f"- 샘플 데이터 ({len(table.sample_rows)}행) (관찰):")
        # 샘플 행 전체 컬럼 표시 (비교 판정용)
        for row in table.sample_rows[:3]:
            row_str = ", ".join(
                f"{k}={v}" for k, v in row.items()
            )
            lines.append(f"  {row_str}")

    lines.append("")  # 테이블 간 빈 줄 구분
    return lines


def _build_comparison_block(group: list[CandidateTable]) -> str:
    """비교 대상 테이블 그룹을 프롬프트용 문자열로 조립한다.

    모든 정보에 출처 태그 부착: (메타 원본), (관찰), (LLM 추론)
    테이블별 조립은 _build_table_block에 위임한다.
    """
    lines: list[str] = []
    for table in group:
        lines.extend(_build_table_block(table))
    return "\n".join(lines)
