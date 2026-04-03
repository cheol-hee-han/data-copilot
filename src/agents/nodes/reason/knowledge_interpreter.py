"""knowledge_interpreter 노드 — 도구 결과를 배치 LLM 해석하고 상태에 반영한다.

context_explorer의 Phase 3-6을 분리한 노드.
knowledge_fetcher가 수집한 도구 결과를 LLM으로 해석하고,
knowledge_items/candidate_tables/code_map을 갱신한다.

Phase 3: 배치 LLM 해석 1회 (해석 + 비교 판정, 관찰 데이터 포함)
Phase 4: 해석 결과 반영 (KnowledgeItem 승격, CandidateTable 갱신)
Phase 4.5: 유사 SQL 관련성 주석 부착 (relevant_use_cases 기반)
Phase 5: 테이블 판정 결과 마킹 (CandidateTable.selection_status)
Phase 6: confidence 승격 + 중복 제거

위임 구조:
    - 프롬프트: system_prompts.py의 KNOWLEDGE_INTERPRETER_SYSTEM
    - LLM 호출: utils/llm (llm_call_with_parse_retry)
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
    ConfidenceStatus,
    KeyDateColumn,
    KnowledgeItem,
    ObservedDateColumn,
    StepStatus,
    TableSelectionStatus,
)
from src.agents.nodes.system_prompts import (
    KNOWLEDGE_INTERPRETER_SYSTEM,
)
from src.config import settings
from src.utils.llm import llm_call_with_parse_retry, ParseError
from src.utils.llm.prompt import render_prompt
from src.utils.logger import get_logger
from src.utils.truncate import truncate_trace
from src.utils.tracker.dispatch import (
    dispatch_tracking_event,
    record_prompt_variables,
    DECISION_TABLE_COMPARISON,
    LLM_PROMPT_VARIABLES,
)

logger = get_logger(__name__)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 배치 해석 결과 모델
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 메인 노드 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


async def knowledge_interpreter_node(state: PipelineState) -> dict:
    """도구 결과를 배치 LLM 해석하고 상태에 반영한다.

    Phase 3: 배치 LLM 해석 1회
    Phase 4: 해석 결과 반영
    Phase 4.5: 유사 SQL 관련성 주석
    Phase 5: 테이블 판정 마킹
    Phase 6: 중복 제거 + confidence 승격
    """
    reason = state.reason.model_copy(deep=True)

    execution_plan = list(reason.execution_plan)
    knowledge_items = list(reason.knowledge_items)
    candidate_tables = list(reason.candidate_tables)
    discovered_facts = list(reason.discovered_facts)

    explored_use_cases = list(reason.explored_use_cases)
    code_map = dict(reason.code_map)

    # ── Phase 3: 배치 LLM 해석 (1회, 관찰 데이터 포함) ──
    time_slot = _extract_time_slot(state.normalized_query)
    batch_result = await _interpret_batch(
        candidate_tables,
        explored_use_cases,
        code_map,
        execution_plan,
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

    # ── Phase 6: 중복 제거 + confidence 승격 ──
    _dedup_knowledge_items(knowledge_items)
    _promote_sampled_confidence(candidate_tables, knowledge_items)

    reason.knowledge_items = knowledge_items
    reason.candidate_tables = candidate_tables
    reason.execution_plan = execution_plan
    reason.discovered_facts = discovered_facts

    return {"reason": reason}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 프롬프트 직렬화
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _extract_time_slot(normalized_query: Any) -> str:
    """NormalizedQuery에서 시간 조건 문자열을 추출한다."""
    if normalized_query is None:
        return "(명시되지 않음)"
    tr = getattr(normalized_query, "time", None)
    if tr is None:
        tr = getattr(normalized_query, "time_range", None)
    if tr is None:
        return "(명시되지 않음)"
    raw_text = getattr(tr, "raw_text", None)
    if raw_text:
        return raw_text
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
    explored_use_cases: list[dict],
    code_map: dict[str, CodeMeta],
    execution_plan: list,
) -> str:
    """state에 축적된 도구 결과를 프롬프트용 문자열로 직렬화한다.

    candidate_tables는 _serialize_table_observations()에서 별도 처리하므로
    여기서는 explored_use_cases와 code_map만 직렬화한다.
    execution_plan의 DONE 스텝 메타(tool, input, purpose)를 참조하여
    어떤 검색이 수행되었는지 맥락을 제공한다.
    """
    blocks: list[str] = []

    # 실행된 스텝 요약 (어떤 도구가 어떤 입력으로 실행되었는지)
    done_steps = [s for s in execution_plan if s.status == StepStatus.DONE]
    if done_steps:
        step_lines = []
        for s in done_steps:
            step_lines.append(f"- {s.tool}({s.input}): {s.purpose or ''}")
        blocks.append(
            "### 실행된 도구 목록\n" + "\n".join(step_lines),
        )

    # 유사 SQL (explored_use_cases)
    if explored_use_cases:
        uc_str = json.dumps(
            explored_use_cases, ensure_ascii=False, default=str,
        )
        blocks.append(f"### 유사 SQL 이력\n{uc_str}")

    # 코드 메타 (code_map)
    if code_map:
        code_entries: list[str] = []
        for col_name, meta in code_map.items():
            codes_str = json.dumps(
                meta.codes, ensure_ascii=False, default=str,
            ) if meta.codes else "{}"
            code_entries.append(
                f"- {col_name} ({meta.column_desc}): {codes_str}",
            )
        blocks.append(
            "### 코드 메타\n" + "\n".join(code_entries),
        )

    return "\n\n".join(blocks) if blocks else "(도구 결과 없음)"


def _serialize_table_observations(
    candidate_tables: list[CandidateTable],
) -> str:
    """후보 테이블의 관찰 데이터를 프롬프트용 문자열로 직렬화한다."""
    if not candidate_tables:
        return "(후보 테이블 없음)"
    lines: list[str] = []
    for table in candidate_tables:
        lines.extend(_build_table_block(table))
    return "\n".join(lines)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LLM 배치 해석 (Phase 3)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


async def _interpret_batch(
    candidate_tables: list[CandidateTable],
    explored_use_cases: list[dict],
    code_map: dict[str, CodeMeta],
    execution_plan: list,
    original_query: str,
    time_slot: str,
    knowledge_items: list[KnowledgeItem] | None = None,
) -> BatchInterpretResult:
    """모든 도구 결과를 배치로 LLM 해석하고 비교 판정한다.

    실패 시 rule-based fallback으로 전환한다.
    """
    done_steps = [s for s in execution_plan if s.status == StepStatus.DONE]
    if not done_steps:
        return BatchInterpretResult()

    tool_results_str = _serialize_tool_results(
        explored_use_cases, code_map, execution_plan,
    )
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
    prompt, tracked_vars = render_prompt(
        KNOWLEDGE_INTERPRETER_SYSTEM, render_vars,
    )
    await record_prompt_variables(tracked_vars)

    def _parse_fn(raw_text: str) -> BatchInterpretResult:
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
        return _interpret_batch_fallback(execution_plan)


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
    execution_plan: list,
) -> BatchInterpretResult:
    """배치 LLM 실패 시 실패 사실만 기록한다."""
    interpretations: list[dict] = []

    for step in execution_plan:
        if step.status != StepStatus.DONE:
            continue
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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Phase 4: 해석 결과 반영
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


def _annotate_use_case_relevance(
    explored_use_cases: list[dict],
    relevant_use_cases: list[dict],
) -> None:
    """배치 LLM의 relevant_use_cases 결과를 explored_use_cases에 부착한다."""
    relevant_map = {
        e["sql_id"]: e.get("reason", "")
        for e in relevant_use_cases
        if "sql_id" in e
    }
    for uc in explored_use_cases:
        uc_id = uc.get("id", "")
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


def _merge_llm_inferred_fields(
    candidate_tables: list[CandidateTable],
    llm_tables: list[dict],
) -> None:
    """LLM이 추론한 3측면 필드를 기존 CandidateTable에 인플레이스 병합한다."""
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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Phase 6: 중복 제거 + 신뢰도 승격
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


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
    """샘플 데이터가 확보된 테이블의 KnowledgeItem confidence를 승격한다."""
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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 테이블 프롬프트 빌드 유틸리티
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _resolve_observed_source_tag(
    odc: ObservedDateColumn,
    key_date_columns: list[KeyDateColumn],
) -> str:
    """ObservedDateColumn의 출처 태그를 반환한다."""
    kdc_match = next(
        (k for k in key_date_columns if k.column_name == odc.column_name),
        None,
    )
    if kdc_match is not None and kdc_match.source == "llm_fallback":
        return "LLM 추론"
    return "관찰"


def _format_date_columns(table: CandidateTable) -> list[str]:
    """테이블의 날짜 컬럼 정보를 프롬프트 라인 목록으로 변환한다."""
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
    if table.subject_area:
        lines.append(f"- 주제영역: {table.subject_area} (메타 원본)")
    if table.description:
        lines.append(f'- 테이블 설명: "{table.description}" (메타 원본)')
    lines.extend(_format_date_columns(table))

    if table.columns:
        col_labels: list[str] = []
        for c in table.columns:
            label = c.name
            if c.alt_name:
                label += f"({c.alt_name})"
            if c.col_type:
                label += f"[{c.col_type}]"
            col_labels.append(label)
        lines.append(f"- 컬럼: {', '.join(col_labels)} (메타 원본)")
    if table.inferred_entity_scope:
        lines.append(f'- 엔티티 범위: "{table.inferred_entity_scope}" (LLM 추론)')
    if table.inferred_functional_usage:
        lines.append(f'- 기능적 용도: "{table.inferred_functional_usage}" (LLM 추론)')
    if table.inferred_data_refresh_hint:
        lines.append(f'- 갱신 주기: "{table.inferred_data_refresh_hint}" (LLM 추론)')
    if table.sample_rows:
        lines.append(f"- 샘플 데이터 ({len(table.sample_rows)}행) (관찰):")
        for row in table.sample_rows[:3]:
            row_str = ", ".join(
                f"{k}={v}" for k, v in row.items()
            )
            lines.append(f"  {row_str}")

    lines.append("")  # 테이블 간 빈 줄 구분
    return lines


def _group_by_keyword(
    candidate_tables: list[CandidateTable],
) -> tuple[list[list[CandidateTable]], set[str]]:
    """inferred_entity_scope 기반 한글 키워드로 유사 테이블 그룹."""
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
        group = [
            t for t in tables
            if t.table_name not in grouped_tables
        ]
        if len(group) >= 2:
            groups.append(group)
            grouped_tables.update(
                t.table_name for t in group
            )

    return groups, grouped_tables


def _group_by_prefix(
    candidate_tables: list[CandidateTable],
    already_grouped: set[str],
) -> list[list[CandidateTable]]:
    """테이블명 접두사(앞 3개 토큰)로 그룹을 만든다."""
    ungrouped = [
        t for t in candidate_tables
        if t.table_name not in already_grouped
    ]
    if len(ungrouped) < 2:
        return []

    prefix_map: dict[str, list[CandidateTable]] = {}
    for t in ungrouped:
        parts = t.table_name.split("_")
        prefix = (
            "_".join(parts[:3])
            if len(parts) >= 3
            else t.table_name
        )
        prefix_map.setdefault(prefix, []).append(t)

    return [
        tables for tables in prefix_map.values()
        if len(tables) >= 2
    ]


def _find_comparison_groups(
    candidate_tables: list[CandidateTable],
) -> list[list[CandidateTable]]:
    """비교가 필요한 유사 테이블 그룹을 반환한다.

    1차: inferred_entity_scope에서 도메인 키워드 공유
    2차 (fallback): 테이블명 접두사 매칭
    """
    if len(candidate_tables) < 2:
        return []

    groups, grouped = _group_by_keyword(candidate_tables)
    groups.extend(
        _group_by_prefix(candidate_tables, grouped),
    )
    return groups


def _build_comparison_block(group: list[CandidateTable]) -> str:
    """비교 대상 테이블 그룹을 프롬프트용 문자열로 조립한다."""
    lines: list[str] = []
    for table in group:
        lines.extend(_build_table_block(table))
    return "\n".join(lines)
