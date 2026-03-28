"""context_explorer 노드 — 실행계획 스텝을 내부 루프로 순차 실행.

탐색은 노드 내부에서 루프로 처리하고, 판단만 외부 노드(confidence_evaluator)로 분리한다.

v2.0 (2026-03-25): LLM 기반 도구 결과 해석 추가 — 외부 프롬프트 사용.
v3.0 (2026-03-27): 배치 해석 구조로 전환 — 도구 실행과 LLM 해석 분리.
  Phase 1: 도구 전부 실행 (rule-based만, LLM 없음)
  Phase 2: 관찰 데이터 수집 (날짜 분포, 샘플 — DB 쿼리)
  Phase 3: 배치 LLM 해석 1회 (해석 + 비교 판정, 관찰 데이터 포함)
  Phase 4: 해석 결과 반영
  Phase 5: 부적합 테이블 제거
  Phase 6: confidence 승격 + readiness 체크
"""

from __future__ import annotations

import json
import re
from typing import Any

from src.agents.state.state import (
    PipelineState,
    CandidateTable,
    KeyDateColumn,
    KnowledgeItem,
    ObservedDateColumn,
    ReasoningState,
    MAX_TOOL_CALLS,
)
from src.utils.db_routing import parse_db_source, get_schema_for_source
from src.agents.nodes.reason.tools import (
    execute_tool,
    get_date_distribution,
    get_sample_rows,
    detect_date_pattern,
)
from src.agents.nodes.system_prompts import (
    REASON_BATCH_INTERPRET,
    REASON_EXPLORE_OBSERVE,
    REASON_TABLE_COMPARISON,
)
from src.services.confidence_scorer import ReadinessVerdict, evaluate_readiness
from src.config import settings
from src.utils.llm import get_llm_client
from src.utils.logger import get_logger
from src.utils.tracker import get_current_tracker, record_prompt_variables

logger = get_logger(__name__)


# 중복 방지 대상 도구 목록 (이미 검색한 입력은 스킵)
_DEDUP_TOOLS: frozenset[str] = frozenset({
    "search_use_cases", "search_table_meta",
    "search_code_meta", "search_manual", "search_glossary",
})


def _should_skip_step(
    step: Any,
    searched_queries: list[str],
    sampled_tables: list[str],
) -> bool:
    """중복 실행 여부를 판정한다.

    검색 계열 도구는 동일 입력이 이미 검색됐으면 스킵.
    get_sample_data는 동일 테이블이 이미 샘플 조회됐으면 스킵.
    """
    if step.tool in _DEDUP_TOOLS and step.input in searched_queries:
        step.status = "SKIPPED"
        step.insight = "이미 검색한 쿼리 — 스킵"
        return True
    if step.tool == "get_sample_data":
        table_name = step.input.split(",")[0].strip()
        if table_name in sampled_tables:
            step.status = "SKIPPED"
            step.insight = "이미 샘플 조회한 테이블 — 스킵"
            return True
    return False


async def _run_step(
    step: Any,
    searched_queries: list[str],
    sampled_tables: list[str],
    candidate_tables: list,
    explored_use_cases: list,
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
        step.status = "DONE"

        if step.tool == "get_sample_data":
            sampled_tables.append(step.input.split(",")[0].strip())
        else:
            searched_queries.append(step.input)

        # rule-based CandidateTable 추출 (LLM 없이)
        new_tables = _extract_tables(step, result)
        candidate_tables.extend(new_tables)

        if step.tool == "search_use_cases" and result:
            explored_use_cases.extend(result)

        # ── 추적: 도구 실행 성공 ──
        result_count = len(result) if isinstance(result, list) else 1
        logger.info(
            "도구 실행 완료",
            tool=step.tool,
            input=step.input[:80],
            results=result_count,
            new_tables=len(new_tables),
            latency_ms=round(_elapsed, 1),
        )

        tracker = get_current_tracker()
        if tracker and tracker.enabled:
            tracker.track_context_retrieval(
                source=step.tool,
                query=step.input[:200],
                results_count=result_count,
                results_summary=[
                    f"결과 {result_count}건 수집 (배치 해석 대기)",
                ],
                latency_ms=round(_elapsed, 1),
            )

    except Exception as e:
        _elapsed = (_time.perf_counter() - _t0) * 1000
        step.status = "FAILED"
        step.insight = f"도구 실행 실패: {e}"
        logger.warning(
            "도구 실행 실패",
            tool=step.tool,
            input=step.input[:80],
            error=str(e),
            latency_ms=round(_elapsed, 1),
        )

        tracker = get_current_tracker()
        if tracker and tracker.enabled:
            tracker.track_context_retrieval(
                source=step.tool,
                query=step.input[:200],
                results_count=0,
                results_summary=[
                    f"실패: {e}"[:200],
                ],
                latency_ms=round(_elapsed, 1),
                status="error",
            )

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
        confirmed_join_path=reason.confirmed_join_path,
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
    reason.phase = "EXPLORING"

    execution_plan = list(reason.execution_plan)
    knowledge_items = list(reason.knowledge_items)
    candidate_tables = list(reason.candidate_tables)
    searched_queries = list(reason.searched_queries)
    sampled_tables = list(reason.sampled_tables)
    explored_use_cases = list(reason.explored_use_cases)
    rejected_tables = list(reason.rejected_tables)
    total_tool_calls = reason.loop_guard.total_tool_calls

    # ── Phase 1: 도구 전부 실행 (rule-based만, LLM 없음) ──
    collected_results: list[tuple[Any, Any]] = []

    for step in execution_plan:
        if step.status != "PENDING" or total_tool_calls >= MAX_TOOL_CALLS:
            continue

        if _should_skip_step(step, searched_queries, sampled_tables):
            continue

        executed_step, result, calls = await _run_step(
            step, searched_queries, sampled_tables,
            candidate_tables, explored_use_cases,
        )
        total_tool_calls += calls

        if result is not None:
            collected_results.append((executed_step, result))

    loop_guard = reason.loop_guard.model_copy()
    loop_guard.total_tool_calls = total_tool_calls

    reason.execution_plan = execution_plan
    reason.searched_queries = searched_queries
    reason.sampled_tables = sampled_tables
    reason.explored_use_cases = explored_use_cases
    reason.loop_guard = loop_guard

    # ── Phase 2: 관찰 데이터 수집 (DB 쿼리, 전체 대상) ──
    # 날짜 분포/샘플은 DB 쿼리(수십ms)로 비용 무시 가능하며,
    # 비교 판정의 정확도를 높이는 핵심 입력이므로 배치 해석 전에 수집한다.
    await _observe_all_date_distributions(candidate_tables)
    await _sample_unsampled_tables(candidate_tables, sampled_tables)

    # ── Phase 3: 배치 LLM 해석 (1회, 관찰 데이터 포함) ──
    time_slot = _extract_time_slot(state.normalized_query)
    batch_result = await _interpret_batch(
        collected_results,
        candidate_tables,
        state.preprocessed_input,
        time_slot,
    )

    # ── Phase 4: 해석 결과 반영 ──
    knowledge_items.extend(batch_result.knowledge_updates)
    _merge_llm_inferred_fields(candidate_tables, batch_result.new_tables)

    # 배치 해석의 insight를 각 step에 반영 (tracker/로깅용)
    _apply_batch_insights(execution_plan, batch_result.interpretations)

    # ── Phase 5: 부적합 테이블 제거 ──
    if batch_result.rejected:
        rejected_set = set(batch_result.rejected)
        rejected_tables.extend(batch_result.rejected)
        candidate_tables[:] = [
            t for t in candidate_tables
            if t.table_name not in rejected_set
        ]
        knowledge_items[:] = [
            ki for ki in knowledge_items
            if not _is_rejected_table_knowledge(ki, rejected_set)
        ]
        logger.info(
            "배치 비교 판정 완료",
            selected=batch_result.selected,
            rejected=batch_result.rejected,
            reason=batch_result.comparison_reason[:100],
        )

        # ── Tracker: 비교 판정 의사결정 기록 ──
        tracker = get_current_tracker()
        if tracker and tracker.enabled:
            tracker.track_decision(
                node="batch_interpret",
                decision_type="table_comparison",
                chosen=", ".join(batch_result.selected),
                alternatives=batch_result.rejected,
                confidence=len(batch_result.selected) / max(
                    len(batch_result.selected) + len(batch_result.rejected), 1,
                ),
                reason=batch_result.comparison_reason[:200],
            )

    # ── Phase 6: confidence 승격 + readiness 체크 ──
    _promote_sampled_confidence(candidate_tables, knowledge_items)

    reason.knowledge_items = knowledge_items
    reason.candidate_tables = candidate_tables
    reason.rejected_tables = rejected_tables

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
        selected: list[str] | None = None,
        rejected: list[str] | None = None,
        comparison_reason: str = "",
    ) -> None:
        self.interpretations = interpretations or []
        self.knowledge_updates = knowledge_updates or []
        self.new_tables = new_tables or []
        self.selected = selected or []
        self.rejected = rejected or []
        self.comparison_reason = comparison_reason


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


def _serialize_tool_results(
    collected_results: list[tuple[Any, Any]],
) -> str:
    """수집된 도구 결과를 프롬프트용 문자열로 직렬화한다.

    각 도구 결과를 상위 5건, 3000자로 제한한다
    (기존 _interpret_with_llm과 동일 전략).
    """
    blocks: list[str] = []
    for step, result in collected_results:
        if result is None:
            continue
        if isinstance(result, list):
            truncated = result[:5]
        else:
            truncated = result
        result_str = json.dumps(
            truncated, ensure_ascii=False, default=str,
        )[:3000]
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
) -> BatchInterpretResult:
    """모든 도구 결과를 배치로 LLM 해석하고 비교 판정한다.

    교차 참조로 해석 정확도를 높이고, selected/rejected를 한 번에 판정한다.
    실패 시 rule-based fallback으로 전환한다.
    """
    if not collected_results:
        return BatchInterpretResult()

    tool_results_str = _serialize_tool_results(collected_results)
    table_obs_str = _serialize_table_observations(candidate_tables)

    batch_vars = {
        "original_query": original_query or "",
        "time_slot": time_slot or "(명시되지 않음)",
        "tool_results": tool_results_str,
        "table_observations": table_obs_str,
    }
    prompt = REASON_BATCH_INTERPRET
    for vk, vv in batch_vars.items():
        prompt = prompt.replace(f"{{{vk}}}", vv)

    import time as _time

    _t0 = _time.perf_counter()
    try:
        client = get_llm_client()
        response = await client.messages.create(
            model=settings.llm_model,
            max_tokens=2048,
            timeout=settings.llm_long_timeout,
            system=prompt,
            messages=[
                {"role": "user", "content": "모든 결과를 통합 분석하세요."},
            ],
        )
        _elapsed = (_time.perf_counter() - _t0) * 1000
        record_prompt_variables(batch_vars)

        raw = response.content[0].text
        prompt_tokens = getattr(
            getattr(response, "usage", None), "input_tokens", 0,
        )
        response_tokens = getattr(
            getattr(response, "usage", None), "output_tokens", 0,
        )

        # ── Tracker: 배치 LLM 호출 기록 ──
        tracker = get_current_tracker()
        if tracker and tracker.enabled:
            tracker.track_llm_call(
                node="batch_interpret",
                prompt_summary=prompt[:500],
                prompt_variables=batch_vars,
                response_text=raw[:500],
                model=settings.llm_model,
                prompt_tokens=prompt_tokens,
                response_tokens=response_tokens,
                latency_ms=round(_elapsed, 2),
            )

        logger.info(
            "배치 LLM 해석 완료",
            model=settings.llm_model,
            prompt_tokens=prompt_tokens,
            response_tokens=response_tokens,
            latency_ms=round(_elapsed, 1),
            tool_results_count=len(collected_results),
        )

        json_match = re.search(r"\{[\s\S]*\}", raw)
        if not json_match:
            raise ValueError("배치 LLM 응답에서 JSON 추출 실패")

        data = json.loads(json_match.group())
        return _parse_batch_result(data)

    except Exception as e:
        _elapsed = (_time.perf_counter() - _t0) * 1000
        logger.warning(
            "배치 LLM 해석 실패, rule-based fallback",
            error=str(e),
            latency_ms=round(_elapsed, 1),
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
                status=ku.get("new_status", "CANDIDATE"),
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
        comparison_reason=data.get("comparison_reason", ""),
    )


def _interpret_batch_fallback(
    collected_results: list[tuple[Any, Any]],
) -> BatchInterpretResult:
    """배치 LLM 실패 시 rule-based fallback으로 해석한다.

    기존 _RULE_DISPATCH를 도구별로 적용한다.
    비교 판정(selected/rejected)은 수행하지 않는다.
    """
    knowledge_updates: list[KnowledgeItem] = []
    interpretations: list[dict] = []

    for step, result in collected_results:
        if result is None:
            continue
        insight, knowledge = _interpret_rule_based(step, result)
        step.insight = insight
        knowledge_updates.extend(knowledge)
        interpretations.append({
            "tool_name": step.tool,
            "tool_input": step.input,
            "insight": insight,
        })

    return BatchInterpretResult(
        interpretations=interpretations,
        knowledge_updates=knowledge_updates,
    )


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
        if step.status == "DONE" and not step.insight:
            matched = insight_map.get(step.input, "")
            if matched:
                step.insight = matched


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 개별 해석 함수 (v2.0 — 배치 실패 시 fallback용으로 유지)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


async def _interpret_result(
    step: Any,
    result: Any,
    original_query: str,
) -> tuple[str, list[KnowledgeItem], list[CandidateTable]]:
    """도구 결과를 LLM으로 해석하고 knowledge/tables를 추출한다.

    1차: rule-based로 CandidateTable을 추출한다.
    2차: LLM 해석으로 insight/knowledge와 3측면 필드(entity_scope 등)를 병합한다.
    3차: LLM 실패 시 rule-based fallback으로 insight/knowledge를 생성한다.
    """
    if not result:
        return (
            f"{step.tool} 결과 없음",
            [],
            [],
        )

    # 구조적 데이터 추출 (rule-based — API 응답 파싱)
    new_tables = _extract_tables(step, result)

    # LLM 해석 시도
    try:
        insight, knowledge, llm_new_tables = await _interpret_with_llm(
            step, result, original_query,
        )
        # LLM 추론 3측면 필드를 rule-based CandidateTable에 병합
        _merge_llm_inferred_fields(new_tables, llm_new_tables)
        return insight, knowledge, new_tables
    except Exception as e:
        logger.debug(
            "LLM 해석 실패, rule-based fallback",
            error=str(e),
        )

    # Rule-based fallback
    insight, knowledge = _interpret_rule_based(step, result)
    return insight, knowledge, new_tables


# ── 행내표준 날짜 접미사 (PK 컬럼에서 기준 날짜 컬럼을 식별하는 데 사용) ──
DATE_SUFFIXES: list[str] = ["YMD", "YM", "YY", "DT"]

# ── 한글 기준 날짜 키워드 (alt_name 보조 식별에 사용) ──
KOREAN_DATE_KEYWORDS: list[str] = [
    "기준일", "기준년월", "기준년", "거래일", "실행일",
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
) -> tuple[list[str], dict[str, str], list[str]]:
    """메타 응답의 columns 필드를 파싱한다.

    반환: (영문 컬럼명 목록, 한글명 매핑, PK 컬럼명 목록)
    """
    if not isinstance(raw_cols, list):
        return [], {}, []
    columns = [c.get("name", "") for c in raw_cols]
    alt_names = {
        c.get("name", ""): c.get("alt_name", "")
        for c in raw_cols
        if isinstance(c, dict) and c.get("name") and c.get("alt_name")
    }
    pk_columns = [
        c.get("name", "")
        for c in raw_cols
        if isinstance(c, dict) and c.get("is_pk")
    ]
    return columns, alt_names, pk_columns


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
        table_name = meta.get("name", "") or meta.get("table_name", "")
        if not table_name:
            continue

        raw_cols = meta.get("columns", [])
        columns, col_alt_names, pk_columns = _parse_meta_columns(raw_cols)
        desc = (
            meta.get("description")
            or meta.get("alt_name", "")
            or meta.get("table_description", "")
        )
        schema_name = meta.get("schema_name", "")
        db_source = parse_db_source(table_name)

        # MongoDB 메타에 schema_name이 없으면 db_source에서 추론
        if not schema_name:
            schema_name = get_schema_for_source(db_source)

        key_date_cols = _resolve_key_date_columns(pk_columns, raw_cols)

        tables.append(CandidateTable(
            table_name=table_name,
            schema_name=schema_name,
            db_source=db_source,
            role=desc,
            relevant_columns=columns,
            column_alt_names=col_alt_names,
            key_date_columns=key_date_cols,
        ))
    return tables


async def _interpret_with_llm(
    step: Any,
    result: Any,
    original_query: str,
) -> tuple[str, list[KnowledgeItem], list[dict]]:
    """LLM으로 도구 결과를 해석한다.

    반환:
        insight: 도구 결과 요약 문자열
        knowledge: KnowledgeItem 목록 (knowledge_updates 필드 파싱)
        llm_tables: LLM이 추론한 테이블 3측면 필드 목록 (new_tables 필드 파싱)
            각 항목 키: table_name, entity_scope,
                       functional_usage, data_refresh_hint
    """
    # 결과를 문자열로 변환 (토큰 절약: 상위 5건)
    if isinstance(result, list):
        truncated = result[:5]
    else:
        truncated = result
    result_str = json.dumps(
        truncated, ensure_ascii=False, default=str,
    )[:3000]

    explore_vars = {
        "original_query": original_query or "",
        "tool_name": step.tool,
        "tool_input": step.input,
        "tool_purpose": step.purpose or "",
        "tool_result": result_str,
    }
    prompt = REASON_EXPLORE_OBSERVE
    for vk, vv in explore_vars.items():
        prompt = prompt.replace(f"{{{vk}}}", vv)

    client = get_llm_client()
    response = await client.messages.create(
        model=settings.llm_model,
        max_tokens=1024,
        timeout=settings.llm_default_timeout,
        system=prompt,
        messages=[
            {"role": "user", "content": "결과를 해석하세요."},
        ],
    )
    record_prompt_variables(explore_vars)

    raw = response.content[0].text
    json_match = re.search(r"\{[\s\S]*\}", raw)
    if not json_match:
        raise ValueError("LLM 응답에서 JSON 추출 실패")

    data = json.loads(json_match.group())
    insight = data.get("insight", "해석 완료")

    knowledge: list[KnowledgeItem] = []
    for ku in data.get("knowledge_updates", []):
        knowledge.append(KnowledgeItem(
            key=ku.get("key", ""),
            value=ku.get("value", ""),
            confidence=ku.get("confidence", 0.5),
            status=ku.get("new_status", "CANDIDATE"),
            source=ku.get("source", step.tool),
            evidence=[ku.get("evidence", "")],
            is_critical=ku.get("is_critical", False),
        ))

    # LLM이 추론한 테이블 3측면 필드 파싱
    # 프롬프트에서 new_tables 필드를 반환하도록 지시한 경우에만 존재한다
    llm_tables: list[dict] = data.get("new_tables", [])

    return insight, knowledge, llm_tables


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


def _rule_table_meta(
    result: list[dict],
) -> tuple[str, list[KnowledgeItem]]:
    """search_table_meta 결과를 rule-based로 해석한다.

    신규 필드(name, description, columns[].name)를 우선 참조하고
    구 필드(table_name, table_description, columns[].column_name)를 폴백으로 지원한다.
    """
    knowledge: list[KnowledgeItem] = []
    for meta in result:
        name = meta.get("name", "") or meta.get("table_name", "")
        desc = (
            meta.get("description")
            or meta.get("alt_name", "")
            or meta.get("table_description", "")
        )
        cols = meta.get("columns", [])
        col_names = (
            [c.get("name", "") for c in cols[:5]]
            if isinstance(cols, list) else []
        )
        if not name:
            continue
        knowledge.append(KnowledgeItem(
            key=f"table:{name}",
            value=f"{desc} (컬럼: {', '.join(col_names)})",
            confidence=0.4,
            status="CANDIDATE",
            source="테이블메타",
            evidence=[f"메타검색에서 {name} 발견"],
            is_critical=True,
        ))
    names = [
        meta.get("name", "") or meta.get("table_name", "")
        for meta in result
    ]
    insight = (
        f"테이블 {len(result)}건 발견: "
        f"{', '.join(n for n in names if n)}"
    )
    return insight, knowledge


def _rule_code_meta(
    result: list[dict],
) -> tuple[str, list[KnowledgeItem]]:
    """search_code_meta 결과를 rule-based로 해석한다."""
    knowledge: list[KnowledgeItem] = []
    for entry in result:
        col = entry.get("code_field", "")
        codes = entry.get("codes", {})
        if not (isinstance(codes, dict) and col):
            continue
        labels = [f"{k}={v}" for k, v in list(codes.items())[:5]]
        knowledge.append(KnowledgeItem(
            key=f"code:{col}",
            value=f"{col} IN ({', '.join(labels)})",
            confidence=0.7,
            status="PROBABLE",
            source="코드메타",
            evidence=[f"코드메타에서 {len(codes)}개 값 확인"],
        ))
    return f"코드값 {len(result)}건 확인", knowledge


def _rule_glossary(
    result: list[dict],
) -> tuple[str, list[KnowledgeItem]]:
    """search_glossary 결과를 rule-based로 해석한다."""
    knowledge: list[KnowledgeItem] = []
    for entry in result:
        name = entry.get("name", "")
        defn = entry.get("glossary_definition", "")
        if not name:
            continue
        knowledge.append(KnowledgeItem(
            key=f"glossary:{name}",
            value=defn,
            confidence=0.6,
            status="PROBABLE",
            source="용어사전",
            evidence=[f"용어사전: {name} = {defn[:50]}"],
            is_critical=False,
        ))
    return f"용어사전 {len(result)}건 확인", knowledge


def _rule_use_cases(
    result: list[dict],
) -> tuple[str, list[KnowledgeItem]]:
    """search_use_cases 결과를 rule-based로 해석한다."""
    knowledge: list[KnowledgeItem] = []
    for uc in result[:3]:
        desc = uc.get("description", "")
        score = uc.get("_score", 0)
        if not (uc.get("sql") and score > 0.5):
            continue
        knowledge.append(KnowledgeItem(
            key=f"use_case:{desc[:30]}",
            value=f"유사도 {score:.2f}",
            confidence=min(score, 0.8),
            status="CANDIDATE",
            source="활용사례",
            evidence=[f"SQL이력에서 유사도 {score:.2f} 매칭"],
            is_critical=False,
        ))
    if not result:
        return "활용사례 0건", knowledge
    top_score = result[0].get("_score", 0)
    return (
        f"활용사례 {len(result)}건 검색 (상위 유사도: {top_score:.2f})",
        knowledge,
    )


def _rule_manual(
    result: list[dict],
) -> tuple[str, list[KnowledgeItem]]:
    """search_manual 결과를 rule-based로 해석한다."""
    knowledge: list[KnowledgeItem] = []
    for doc in result[:3]:
        title = doc.get("title", "")
        content = doc.get("content", "")[:100]
        if not (title or content):
            continue
        knowledge.append(KnowledgeItem(
            key=f"manual:{title[:30] or '문서'}",
            value=content,
            confidence=0.5,
            status="CANDIDATE",
            source="업무매뉴얼",
            evidence=[f"매뉴얼 '{title}' 참조"],
            is_critical=False,
        ))
    return f"업무 매뉴얼 {len(result)}건 확인", knowledge


# tool명 → rule 함수 디스패치 테이블
_RULE_DISPATCH: dict[
    str,
    Any,
] = {
    "search_table_meta": _rule_table_meta,
    "search_code_meta": _rule_code_meta,
    "search_glossary": _rule_glossary,
    "search_use_cases": _rule_use_cases,
    "search_manual": _rule_manual,
}


def _interpret_rule_based(
    step: Any,
    result: Any,
) -> tuple[str, list[KnowledgeItem]]:
    """Rule-based fallback 해석.

    tool별 처리 함수(_rule_*)를 디스패치 테이블로 위임하여
    Cognitive Complexity를 낮춘다.
    """
    if not isinstance(result, list):
        return f"{step.tool} 처리 완료", []

    if step.tool == "get_sample_data":
        return f"샘플 데이터 {len(result)}건 조회 완료", []

    rule_fn = _RULE_DISPATCH.get(step.tool)
    if rule_fn:
        return rule_fn(result)

    return f"{step.tool} 처리 완료", []


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
    sampled_tables: list[str],
) -> None:
    """아직 샘플 조회하지 않은 후보 테이블의 샘플 데이터를 조회한다.

    context_explorer_node 후처리에서 rule-based로 자동 호출.
    비교 판정 전에 실행하여 샘플 데이터를 비교 프롬프트에 포함한다.
    """
    sampled_set = set(sampled_tables)
    for table in candidate_tables:
        if table.table_name in sampled_set:
            continue
        if table.sample_rows:
            continue
        try:
            rows = await get_sample_rows(
                table.table_name,
                schema_name=table.schema_name,
                db_source=table.db_source,
            )
            table.sample_rows = rows
            sampled_set.add(table.table_name)
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


def _promote_sampled_confidence(
    candidate_tables: list[CandidateTable],
    knowledge_items: list,
) -> None:
    """샘플 데이터가 확보된 테이블의 KnowledgeItem confidence를 승격한다.

    샘플 데이터 확인 = 실제 데이터 존재 확인 → 0.8 이상으로 승격.
    같은 key의 KI가 여러 건이면 최고 confidence 항목만 승격하고 나머지는 제거한다.
    """
    sampled_table_names = {
        ct.table_name for ct in candidate_tables if ct.sample_rows
    }
    if not sampled_table_names:
        return

    # 같은 key의 중복 제거 + 최고 confidence 유지
    best_ki: dict[str, int] = {}  # key → 최고 confidence 항목의 인덱스
    for i, ki in enumerate(knowledge_items):
        if ki.key in best_ki:
            existing_idx = best_ki[ki.key]
            if ki.confidence > knowledge_items[existing_idx].confidence:
                best_ki[ki.key] = i
        else:
            best_ki[ki.key] = i

    # 중복 제거 (최고 confidence 항목만 유지)
    keep_indices = set(best_ki.values())
    knowledge_items[:] = [
        ki for i, ki in enumerate(knowledge_items)
        if i in keep_indices
    ]

    # 샘플 확인된 테이블의 confidence 승격
    for ki in knowledge_items:
        if not ki.key.startswith("table:"):
            continue
        table_name = ki.key.removeprefix("table:")
        if table_name in sampled_table_names:
            if ki.confidence < 0.8:
                ki.confidence = 0.85
                ki.status = "CONFIRMED"
                ki.evidence.append("샘플 데이터 확인 완료")


def _group_by_keyword(
    candidate_tables: list[CandidateTable],
) -> tuple[list[list[CandidateTable]], set[str]]:
    """inferred_entity_scope 기반 한글 키워드로 유사 테이블 그룹을 만든다.

    반환: (그룹 목록, 이미 그룹에 속한 테이블명 집합)
    """
    keyword_to_tables: dict[str, list[CandidateTable]] = {}
    for t in candidate_tables:
        scope = t.inferred_entity_scope or t.role or ""
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

    client = get_llm_client()

    for group in groups:
        table_block = _build_comparison_block(group)

        cmp_vars = {
            "original_query": original_query or "",
            "time_slot": time_slot or "(명시되지 않음)",
            "table_comparison_block": table_block,
        }
        prompt = REASON_TABLE_COMPARISON
        for vk, vv in cmp_vars.items():
            prompt = prompt.replace(f"{{{vk}}}", vv)

        try:
            response = await client.messages.create(
                model=settings.llm_model,
                max_tokens=512,
                timeout=settings.llm_default_timeout,
                system=prompt,
                messages=[{"role": "user", "content": "테이블을 비교 판정하세요."}],
            )
            record_prompt_variables(cmp_vars)
            raw = response.content[0].text
            json_match = re.search(r"\{[\s\S]*\}", raw)
            if not json_match:
                continue

            result = json.loads(json_match.group())
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
    lines: list[str] = [
        f"### {table.qualified_name}",
        f'- 메타 설명: "{table.role}" (메타 원본)',
    ]
    lines.extend(_format_date_columns(table))

    if table.relevant_columns:
        col_labels = [
            f"{c}({table.column_alt_names[c]})"
            if c in table.column_alt_names else c
            for c in table.relevant_columns[:8]
        ]
        lines.append(f"- 주요 컬럼: {', '.join(col_labels)} (메타 원본)")
    if table.inferred_entity_scope:
        lines.append(f'- 엔티티 범위: "{table.inferred_entity_scope}" (LLM 추론)')
    if table.inferred_functional_usage:
        lines.append(f'- 기능적 용도: "{table.inferred_functional_usage}" (LLM 추론)')
    if table.inferred_data_refresh_hint:
        lines.append(f'- 갱신 주기: "{table.inferred_data_refresh_hint}" (LLM 추론)')
    if table.sample_rows:
        lines.append(f"- 샘플 데이터 ({len(table.sample_rows)}행) (관찰):")
        # 상위 3행만 프롬프트에 포함 (비교 판정용)
        for row in table.sample_rows[:3]:
            row_str = ", ".join(
                f"{k}={v}" for k, v in list(row.items())[:6]
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
