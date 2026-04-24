"""트레이스 타임라인 시각화 — 5섹션 보고서.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

DataCopilotCallbackHandler가 기록한 EvaluationTrace를 기반으로
5개 섹션 + Appendix 형태의 Markdown 보고서를 생성한다.

섹션 구조:
    1. Executive Summary     — 규칙 기반 자연어 요약
    2. Reasoning Flow        — LLM 판단 흐름 서사형 렌더링 (기존 2+3+4 대체)
    3. Node Flow             — 요약 다이어그램 (30+ → Flowchart, <30 → Sequence)
    4. Performance           — 사이클별 Gantt + LLM 비용 분석
    5. Automated Findings    — trace_analyzer 결과 통합
    [Appendix] Detailed Timeline — 부모-자식 들여쓰기 상세 테이블
    [Appendix] Generated SQL — 생성된 SQL 원문

하위 호환:
    reasoning_flow가 빈 배열인 기존 trace → 기존 Decision Trail,
    Referenced Information, State Evolution 함수를 fallback 호출.

사용법::

    from src.utils.tracker.visualizer import save_report, render_full_report

    report_md = render_full_report(handler.to_dict())
    save_report(handler.to_dict(), output_path="report.md")
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from src.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

# ── 상수 ─────────────────────────────────────────────

_LAYER_ORDER = ["User", "interpret", "reason", "present"]

_NODE_LAYER: dict[str, str] = {
    "preprocess": "interpret",
    "resolve_history": "interpret",
    "intent_classifier": "interpret",
    "classify_intent": "interpret",  # 하위호환
    "query_normalizer": "interpret",
    "clarify": "interpret",
    "reasoning_preparer": "reason",
    "context_explorer": "reason",
    "context_retriever": "reason",
    "context_interpreter": "reason",
    "readiness_gate": "reason",
    "recovery_agent": "reason",
    "sql_generator": "reason",
    "sql_validator": "reason",
    "recovery_planner": "reason",
    "result_finalizer": "reason",
    "sql_executor": "present",
    "analyzer": "present",
    "visualizer": "present",
    "formatter": "present",
}

_EVENT_ICONS: dict[str, str] = {
    "node_start": "▶",
    "node_end": "■",
    "llm_call": "🤖",
    "tool_call": "🔧",
    "decision": "⚡",
}

_SEVERITY_ICONS: dict[str, str] = {
    "CRITICAL": "🔴",
    "WARNING": "🟡",
    "INFO": "🔵",
}

# 사이클 마커
_CYCLE_MARKERS = "①②③④⑤⑥⑦⑧⑨⑩"

# 비 SQL 파이프라인 상태 (SQL 관련 경고 억제 대상)
_NON_SQL_STATUSES = frozenset({
    "casual_response", "clarification",
    "greeting", "out_of_scope",
})


# ── 유틸리티 ─────────────────────────────────────────

def _sanitize(text: str, max_len: int = 50) -> str:
    """Mermaid 안전 문자열로 변환한다."""
    safe = (
        text.replace('"', "'")
        .replace("\n", " ")
        .replace("#", "")
        .replace(";", ",")
        .replace("|", "\\|")
    )
    if len(safe) > max_len:
        safe = safe[:max_len] + "…"
    return safe


def _abbreviate(node_name: str) -> str:
    """노드 이름을 Mermaid participant ID로 축약한다."""
    return node_name.replace("_", "")


def _fmt_duration(ms: float) -> str:
    """밀리초를 사람이 읽기 쉬운 형태로 변환한다."""
    if ms < 1000:
        return f"{ms:.0f}ms"
    return f"{ms / 1000:.1f}s"


def _fmt_tokens(tokens: int) -> str:
    """토큰 수를 천 단위 구분으로 포맷한다."""
    if tokens >= 1000:
        return f"{tokens:,}"
    return str(tokens)


def _detect_cycles(node_path: list[str]) -> list[list[str]]:
    """node_path에서 recovery_planner를 기준으로 사이클을 분리한다.

    Returns:
        사이클별 노드 리스트. 첫 번째는 초기 실행, 이후는 재시도.
    """
    cycles: list[list[str]] = []
    current: list[str] = []
    for node in node_path:
        current.append(node)
        if node == "recovery_planner":
            cycles.append(current)
            current = []
    if current:
        cycles.append(current)
    return cycles


def _node_visit_label(
    node: str,
    visit_counts: dict[str, int],
) -> str:
    """반복 방문 노드에 ①②③ 마커를 붙인다."""
    count = visit_counts.get(node, 0)
    if count <= 1:
        return node
    idx = min(count - 1, len(_CYCLE_MARKERS) - 1)
    return f"{node} {_CYCLE_MARKERS[idx]}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. Executive Summary
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def render_executive_summary(
    trace_data: dict[str, Any],
) -> str:
    """트레이스 요약을 규칙 기반으로 생성한다."""
    user_input = trace_data.get("user_input", "")
    status = trace_data.get("final_status", "unknown")
    dur = trace_data.get("total_duration_ms", 0)
    llm_calls = trace_data.get("total_llm_calls", 0)
    llm_tokens = trace_data.get("total_llm_tokens", 0)
    error_msg = trace_data.get("error_message", "")
    node_path = trace_data.get("node_path", [])
    decisions = trace_data.get("decisions", [])
    sql_rec = trace_data.get("sql") or {}

    # 상태 아이콘
    status_icon = "✅" if status == "success" else "❌"

    lines: list[str] = []
    lines.append("## 1. Executive Summary")
    lines.append("")
    lines.append(f"**질의**: {user_input}")

    # 결과 요약
    replan_count = node_path.count("recovery_planner")
    if status == "success":
        result_text = "성공"
        if replan_count:
            result_text += f" ({replan_count}회 재탐색 후)"
    elif status in _NON_SQL_STATUSES:
        result_text = f"{status}"
    else:
        result_text = "실패"
        if replan_count:
            result_text += f" ({replan_count}회 재탐색 후 최대 시도 횟수 초과)"
        if error_msg:
            result_text += f" — {error_msg[:100]}"

    lines.append(f"**결과**: {status_icon} {result_text}")
    lines.append(
        f"**소요**: {_fmt_duration(dur)} | "
        f"LLM {llm_calls}회, {_fmt_tokens(llm_tokens)}토큰"
    )
    lines.append("")

    # 단계별 요약 테이블
    lines.append("| 단계 | 결과 |")
    lines.append("|------|------|")

    # 의도 분류
    intent_dec = next(
        (d for d in decisions
         if d.get("decision_type") == "intent_classification"),
        None,
    )
    if intent_dec:
        conf = intent_dec.get("confidence", 0)
        lines.append(
            f"| 의도 분류 | "
            f"{intent_dec.get('chosen', '?')} "
            f"({conf:.0%}) |"
        )

    # 정규화 결정
    norm_dec = next(
        (d for d in decisions
         if d.get("decision_type") == "normalization"),
        None,
    )
    if norm_dec:
        lines.append(
            f"| 질문 정규화 | {norm_dec.get('chosen', '')} |"
        )

    # 테이블 선택 (readiness에서 추출)
    readiness_decs = [
        d for d in decisions
        if d.get("decision_type") == "readiness_verdict"
    ]
    if readiness_decs:
        last_r = readiness_decs[-1]
        detail = last_r.get("detail", {})
        tables = detail.get("explored_tables", [])
        if tables:
            lines.append(
                f"| 테이블 선택 | "
                f"{', '.join(str(t) for t in tables[:5])} |"
            )
        conf = last_r.get("confidence", 0)
        lines.append(
            f"| 준비도 판정 | "
            f"{last_r.get('chosen', '?')} ({conf:.0%}) |"
        )

    # SQL 결과
    if sql_rec.get("generated_sql"):
        retry = sql_rec.get("retry_count", 0)
        validated = sql_rec.get("validated", False)
        executed = sql_rec.get("execution_success", False)
        row_count = sql_rec.get("row_count", 0)

        sql_summary_parts: list[str] = []
        if retry > 0:
            sql_summary_parts.append(f"{retry + 1}회 시도")
        if validated:
            sql_summary_parts.append("검증 통과")
        else:
            sql_summary_parts.append("검증 실패")
        if executed:
            sql_summary_parts.append(f"실행 성공 ({row_count}건)")
        sql_text = ", ".join(sql_summary_parts)
        lines.append(f"| SQL | {sql_text} |")

    # 실패 원인
    if status not in ({"success"} | _NON_SQL_STATUSES) and error_msg:
        lines.append(
            f"| 실패 원인 | {_sanitize(error_msg, 80)} |"
        )

    lines.append("")
    return "\n".join(lines)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. Decision Trail
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def render_decision_trail(
    trace_data: dict[str, Any],
) -> str:
    """의사결정 내역을 phase별로 렌더링한다."""
    decisions = trace_data.get("decisions", [])
    node_path = trace_data.get("node_path", [])

    if not decisions:
        return "## 2. Decision Trail\n\n> 의사결정 기록 없음\n"

    lines: list[str] = []
    lines.append("## 2. Decision Trail")
    lines.append("")

    # 사이클 경계 판단
    cycles = _detect_cycles(node_path)
    cycle_count = len(cycles)

    # 의사결정을 노드 순서로 정렬 (timestamp 기반)
    lines.append(
        "| # | 노드 | 유형 | 결정 | 확신도 | 근거 |"
    )
    lines.append(
        "|--:|------|------|------|-------:|------|"
    )

    for i, dec in enumerate(decisions, 1):
        node = dec.get("node", "?")
        dtype = dec.get("decision_type", "")
        chosen = _sanitize(str(dec.get("chosen", "")), 30)
        conf = dec.get("confidence", 0)
        reason = _sanitize(dec.get("reason", ""), 50)

        lines.append(
            f"| {i} | {node} | {dtype} "
            f"| {chosen} | {conf:.0%} | {reason} |"
        )

    lines.append("")

    # 판단 재료 상세 (detail 필드가 있는 결정만)
    detailed = [
        d for d in decisions if d.get("detail")
    ]
    if detailed:
        lines.append("### 판단 재료 상세")
        lines.append("")
        for dec in detailed:
            node = dec.get("node", "?")
            dtype = dec.get("decision_type", "")
            detail = dec.get("detail", {})
            lines.append(
                f"**{node} — {dtype}**"
            )
            lines.append("")

            # 확정 지식
            confirmed = detail.get("confirmed_knowledge", [])
            if confirmed:
                lines.append("- 확정 지식:")
                for item in confirmed[:10]:
                    lines.append(f"  - {_sanitize(str(item), 80)}")

            # 미확정 지식
            unresolved = detail.get("unresolved_knowledge", [])
            if unresolved:
                lines.append("- 미확정 지식:")
                for item in unresolved[:10]:
                    lines.append(f"  - {_sanitize(str(item), 80)}")

            # 후보 테이블
            tables = detail.get("explored_tables", [])
            if tables:
                lines.append(
                    f"- 후보 테이블: "
                    f"{', '.join(str(t) for t in tables[:10])}"
                )

            lines.append("")

    if cycle_count > 1:
        lines.append(
            f"> 총 {cycle_count}개 사이클 감지됨 "
            f"(recovery_planner {cycle_count - 1}회 호출)"
        )
        lines.append("")

    return "\n".join(lines)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. Referenced Information
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def render_referenced_info(
    trace_data: dict[str, Any],
) -> str:
    """참조 정보를 소스별로 그룹핑하여 렌더링한다."""
    retrievals = trace_data.get("context_retrievals", [])

    if not retrievals:
        return "## 3. Referenced Information\n\n> 참조 정보 없음\n"

    lines: list[str] = []
    lines.append("## 3. Referenced Information")
    lines.append("")

    # 소스별 그룹핑
    by_source: dict[str, list[dict]] = defaultdict(list)
    for r in retrievals:
        by_source[r.get("source", "unknown")].append(r)

    total_count = 0
    total_latency = 0.0
    success_count = 0

    for source, items in by_source.items():
        lines.append(f"### {source}")
        lines.append("")
        lines.append(
            "| # | 검색 쿼리 | 결과 수 | 소요시간 |"
        )
        lines.append(
            "|--:|-----------|-------:|--------:|"
        )

        for i, item in enumerate(items, 1):
            query = _sanitize(
                item.get("query", ""), 50,
            )
            count = item.get("results_count", 0)
            latency = item.get("latency_ms", 0)
            lines.append(
                f"| {i} | {query} "
                f"| {count} | {_fmt_duration(latency)} |"
            )
            total_count += 1
            total_latency += latency
            if count > 0:
                success_count += 1

            # 결과 요약 (있으면)
            summaries = item.get("results_summary", [])
            if summaries:
                for s in summaries[:3]:
                    lines.append(
                        f"|   | ↳ {_sanitize(str(s), 60)} "
                        f"|   |   |"
                    )

        lines.append("")

    # 합계
    lines.append("### 합계")
    lines.append("")
    lines.append(
        f"- 총 검색: {total_count}회 "
        f"(성공 {success_count}, "
        f"결과 0건 {total_count - success_count})"
    )
    lines.append(f"- 총 소요: {_fmt_duration(total_latency)}")
    lines.append("")

    return "\n".join(lines)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. State Evolution
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def render_state_evolution(
    trace_data: dict[str, Any],
) -> str:
    """노드별 state 변화를 compact 테이블로 렌더링한다."""
    timeline = trace_data.get("timeline", [])

    # node_end 이벤트에서 state_changes 추출
    state_events: list[dict[str, Any]] = []
    visit_counts: dict[str, int] = {}
    for entry in timeline:
        if entry.get("event_type") != "node_end":
            continue
        node = entry.get("node", "")
        visit_counts[node] = visit_counts.get(node, 0) + 1
        changes = (
            entry.get("detail", {}).get("state_changes", [])
        )
        state_events.append({
            "node": node,
            "visit": visit_counts[node],
            "changes": changes,
        })

    if not state_events:
        return (
            "## 4. State Evolution\n\n"
            "> State 변화 데이터 없음\n"
        )

    lines: list[str] = []
    lines.append("## 4. State Evolution")
    lines.append("")
    lines.append(
        "| 노드 | 변경 필드 | 변화 내용 |"
    )
    lines.append(
        "|------|----------|----------|"
    )

    for evt in state_events:
        node = evt["node"]
        visit = evt["visit"]
        changes = evt["changes"]

        # 반복 방문 마커
        if visit > 1:
            idx = min(visit - 1, len(_CYCLE_MARKERS) - 1)
            display_node = f"{node} {_CYCLE_MARKERS[idx]}"
        else:
            display_node = node

        if not changes:
            lines.append(
                f"| {display_node} | - | (변화 없음) |"
            )
            continue

        for i, change in enumerate(changes):
            field = change.get("field", "?")
            before = change.get("before", "")
            after = change.get("after", "")

            if before:
                change_text = (
                    f"`{_sanitize(before, 25)}` → "
                    f"`{_sanitize(after, 25)}`"
                )
            else:
                change_text = f"→ `{_sanitize(after, 40)}`"

            # 첫 변경만 노드 이름 표시, 나머지는 빈 셀
            node_cell = display_node if i == 0 else ""
            lines.append(
                f"| {node_cell} | {field} | {change_text} |"
            )

    lines.append("")
    return "\n".join(lines)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2-NEW. Reasoning Flow (기존 2+3+4 대체, fallback 보존)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# 노드 이름 → 사람이 읽기 쉬운 표시명
_NODE_DISPLAY: dict[str, str] = {
    "intent_classifier": "Intent Classification",
    "classify_intent": "Intent Classification",  # 하위호환
    "query_normalizer": "Query Normalization",
    "reasoning_preparer": "Reasoning Preparer",
    "context_retriever": "Context Retriever",
    "context_interpreter": "Context Interpretation",
    "readiness_gate": "Readiness Gate",
    "recovery_agent": "Recovery Agent",
    "sql_generator": "SQL Generation",
    "sql_validator": "SQL Validation",
    "sql_executor": "SQL Execution",
    "analyzer": "Data Analysis",
    "visualizer": "Visualization",
    "formatter": "Response Formatting",
    "preprocess": "Preprocess",
    "resolve_history": "History Resolution",
    "clarify": "Clarification",
    "recovery_planner": "Recovery Planner",
    "result_finalizer": "Result Finalizer",
}


def render_reasoning_flow(trace_data: dict[str, Any]) -> str:
    """reasoning_flow를 시간순 서사형 Markdown으로 렌더링한다.

    reasoning_flow가 있으면 서사형 렌더링, 없으면 기존 3개 함수 fallback.
    """
    reasoning_flow = trace_data.get("reasoning_flow", [])
    if reasoning_flow:
        return _render_from_reasoning_flow(reasoning_flow, trace_data)
    # 하위 호환: 기존 3개 함수 호출
    return (
        render_decision_trail(trace_data)
        + render_referenced_info(trace_data)
        + render_state_evolution(trace_data)
    )


def _render_from_reasoning_flow(
    steps: list[dict[str, Any]],
    trace_data: dict[str, Any],
) -> str:
    """reasoning_flow 스텝 목록을 서사형 Markdown으로 렌더링한다."""
    lines: list[str] = []
    lines.append("## 2. Reasoning Flow")
    lines.append("")

    # ── 헤더 요약 ──
    total_dur = trace_data.get("total_duration_ms", 0)
    total_llm = trace_data.get("total_llm_calls", 0)
    total_tok = trace_data.get("total_llm_tokens", 0)
    node_path = trace_data.get("node_path", [])

    lines.append(
        f"> 총 소요 {_fmt_duration(total_dur)} · "
        f"LLM {total_llm}회 · "
        f"{_fmt_tokens(total_tok)}tok"
    )

    # 경로 요약
    if node_path:
        path_summary = _build_path_summary(node_path)
        lines.append(f"> 경로: {path_summary}")

    lines.append("")
    lines.append("---")
    lines.append("")

    # ── Phase/Round 그루핑 ──
    groups = _group_steps_by_phase_round(steps)

    for group in groups:
        # 그룹 헤더
        lines.append(f"### {group['header']}")
        lines.append("")

        # 가설 설명 (Round 시작 시)
        if group.get("hypothesis_desc"):
            lines.append(f"> 가설: \"{group['hypothesis_desc']}\"")
            lines.append("")

        # 각 스텝 렌더링
        for step in group["steps"]:
            step_lines = _render_single_step(step)
            lines.extend(step_lines)
            lines.append("")
            lines.append("---")
            lines.append("")

    return "\n".join(lines)


def _build_path_summary(node_path: list[str]) -> str:
    """node_path를 압축된 경로 요약 문자열로 변환한다."""
    # 연속 중복 제거 + 방문 횟수 표기
    compressed: list[str] = []
    visit_counts: dict[str, int] = {}
    replan_seen = False

    for node in node_path:
        short = node.replace("classify_", "").replace("normalize_", "normalize")
        short = short.replace("reasoning_", "").replace("context_", "")
        short = short.replace("readiness_", "").replace("recovery_", "recovery")
        short = short.replace("sql_", "").replace("analyze_", "analyze")
        short = short.replace("format_", "format").replace("execute_", "execute")

        visit_counts[node] = visit_counts.get(node, 0) + 1
        count = visit_counts[node]

        if node == "recovery_agent" and not replan_seen:
            replan_seen = True
            compressed.append(f"**REPLAN**")

        if count > 1:
            compressed.append(f"{short}②")
        else:
            compressed.append(short)

    return " → ".join(compressed)


def _group_steps_by_phase_round(
    steps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """스텝을 Phase/Round별 그룹으로 분류한다."""
    groups: list[dict[str, Any]] = []
    current_group: dict[str, Any] | None = None

    for step in steps:
        phase = step.get("phase", "")
        rnd = step.get("round", 0)
        step_type = step.get("step_type", "")
        hyp_id = step.get("hypothesis_id", "")

        # 그룹 키 결정
        if step_type == "recovery":
            group_key = f"recovery_{rnd + 1}"
            header = f"◆ Recovery → Round {rnd + 1}"
            hyp_desc = ""
        elif phase == "interpret":
            group_key = "interpret"
            header = "Phase 1: Interpret"
            hyp_desc = ""
        elif phase == "present":
            group_key = "present"
            header = "Phase 3: Present"
            hyp_desc = ""
        else:
            # reason phase
            group_key = f"reason_r{rnd}"
            hyp_label = f" ({hyp_id})" if hyp_id else ""
            header = f"Phase 2: Reason — Round {rnd}{hyp_label}"
            # 가설 설명은 reasoning_preparer 또는 recovery output에서 추출
            hyp_desc = _extract_hypothesis_desc(step)

        if current_group is None or current_group["key"] != group_key:
            current_group = {
                "key": group_key,
                "header": header,
                "hypothesis_desc": hyp_desc,
                "steps": [],
            }
            groups.append(current_group)
        elif hyp_desc and not current_group.get("hypothesis_desc"):
            current_group["hypothesis_desc"] = hyp_desc

        current_group["steps"].append(step)

    return groups


def _extract_hypothesis_desc(step: dict[str, Any]) -> str:
    """스텝에서 가설 설명을 추출한다."""
    output = step.get("output", {})
    # reasoning_preparer의 hypothesis 필드
    hyp = output.get("hypothesis", "")
    if isinstance(hyp, str) and hyp:
        # "H1: 유사SQL+..." → 콜론 뒤 부분
        if ": " in hyp:
            return hyp.split(": ", 1)[1]
        return hyp
    # recovery의 new_hypothesis
    new_hyp = output.get("new_hypothesis", {})
    if isinstance(new_hyp, dict):
        return str(new_hyp.get("description", ""))
    return ""


def _render_single_step(step: dict[str, Any]) -> list[str]:
    """단일 reasoning step을 Markdown 행 목록으로 렌더링한다."""
    lines: list[str] = []
    seq = step.get("seq", 0)
    node = step.get("node", "")
    step_type = step.get("step_type", "")
    dur_ms = step.get("duration_ms", 0)
    tokens = step.get("tokens", 0)
    hyp_id = step.get("hypothesis_id", "")

    display = _NODE_DISPLAY.get(node, node)

    # 가설 ID 보충
    hyp_suffix = f" — {hyp_id}" if hyp_id else ""

    # 메타 정보 (duration + tokens or rule-based)
    if step_type == "rule_decision":
        meta = "rule-based"
    elif tokens > 0:
        meta = f"{_fmt_duration(dur_ms)}, {_fmt_tokens(tokens)}tok"
    else:
        meta = _fmt_duration(dur_ms) if dur_ms else ""

    header_meta = f" ({meta})" if meta else ""
    lines.append(f"#### [{seq}] {display}{hyp_suffix}{header_meta}")
    lines.append("")

    inputs = step.get("inputs", {})
    output = step.get("output", {})
    routing = step.get("routing", {})

    # ── 입력 섹션 ──
    if inputs:
        _render_inputs(lines, inputs, step_type)

    # ── 출력 섹션 ──
    if output:
        _render_output(lines, output, step_type)

    # ── 라우팅 ──
    next_node = routing.get("next_node", "")
    reason = routing.get("reason", "")
    if next_node:
        is_retry = routing.get("is_retry", False)
        retry_mark = " (재시도)" if is_retry else ""
        reason_text = f" — {reason}" if reason else ""
        lines.append(f"→ **{next_node}**{reason_text}{retry_mark}")
        lines.append("")

    return lines


def _render_inputs(
    lines: list[str],
    inputs: dict[str, Any],
    step_type: str,
) -> None:
    """step의 inputs를 Markdown으로 렌더링한다."""
    # 신형 스키마: prompt_variables 가 있으면 프롬프트 변수별 섹션으로
    prompt_variables = (
        inputs.get("prompt_variables") if isinstance(inputs, dict) else None
    )
    if isinstance(prompt_variables, dict) and prompt_variables:
        _render_prompt_variables(lines, prompt_variables)
        extras = {
            k: v for k, v in inputs.items() if k != "prompt_variables"
        }
        if extras:
            lines.append("► **부가 입력**")
            for key, val in extras.items():
                _render_kv(lines, key, val, indent=2)
            lines.append("")
        return

    if step_type == "recovery":
        # Recovery: 7개 입력을 각각 ► 소제목 형식으로
        _render_recovery_inputs(lines, inputs)
        return

    if step_type == "tool_execution":
        # tool_execution: 간결 표기
        lines.append("► **입력**")
        for key, val in inputs.items():
            lines.append(f"  {key}: {_format_value_inline(val)}")
        lines.append("")
        return

    # 일반 입력
    lines.append("► **입력**")
    for key, val in inputs.items():
        _render_kv(lines, key, val, indent=2)
    lines.append("")


def _render_prompt_variables(
    lines: list[str],
    variables: dict[str, Any],
) -> None:
    """프롬프트 치환 변수를 변수별 섹션으로 렌더링한다.

    각 변수는 ``► **{var_name}**`` 헤더 아래 값을 코드블록/인라인으로 표시한다.
    긴 텍스트·줄바꿈 포함 값·dict/list 는 코드블록으로 원본 그대로 보존한다.
    """
    lines.append("► **프롬프트 입력 ([INPUT] 치환 변수)**")
    lines.append("")
    for var_name, value in variables.items():
        lines.append(f"**`{{{var_name}}}`**")
        _render_variable_value(lines, value)
        lines.append("")


def _render_variable_value(
    lines: list[str],
    value: Any,
) -> None:
    """프롬프트 변수 값 하나를 타입·길이에 맞게 렌더링한다."""
    if value is None:
        lines.append("  (없음)")
        return
    if isinstance(value, str):
        if not value.strip():
            lines.append("  (없음)")
            return
        if len(value) <= 120 and "\n" not in value:
            lines.append(f"  {value}")
            return
        lines.append("")
        lines.append("```")
        for line in value.split("\n"):
            lines.append(line)
        lines.append("```")
        return
    if isinstance(value, (int, float, bool)):
        lines.append(f"  {value}")
        return
    # dict / list → JSON 코드블록
    lines.append("")
    lines.append("```json")
    try:
        lines.append(
            json.dumps(value, ensure_ascii=False, indent=2, default=str),
        )
    except (TypeError, ValueError):
        lines.append(str(value))
    lines.append("```")


def _render_recovery_inputs(
    lines: list[str],
    inputs: dict[str, Any],
) -> None:
    """Recovery Agent의 7개 입력을 ► 소제목 형식으로 렌더링한다."""
    # Recovery 입력 키 → 표시 소제목 매핑
    recovery_labels: dict[str, str] = {
        "entry_source": "진입 맥락",
        "confirmed_knowledge": "확인된 지식",
        "unresolved_items": "미해소 항목",
        "tool_execution_history": "도구 실행 이력",
        "explored_tables": "탐색된 테이블",
        "dead_ends": "이전 실패 기록",
        "sample_data": "샘플 데이터",
    }
    for key, label in recovery_labels.items():
        val = inputs.get(key)
        if val is None:
            continue
        lines.append(f"► **{label}**")
        if isinstance(val, str):
            for line in val.split("\n"):
                lines.append(f"  {line}")
        else:
            lines.append(f"  {_format_value_inline(val)}")
        lines.append("")

    # recovery_labels에 없는 추가 키 처리
    for key, val in inputs.items():
        if key not in recovery_labels and val is not None:
            lines.append(f"► **{key}**")
            lines.append(f"  {_format_value_inline(val)}")
            lines.append("")


def _render_output(
    lines: list[str],
    output: dict[str, Any],
    step_type: str,
) -> None:
    """step의 output을 Markdown으로 렌더링한다."""
    # 신형 스키마: raw_response 가 있으면 원본 응답 우선 렌더링
    raw_response = (
        output.get("raw_response") if isinstance(output, dict) else None
    )
    if isinstance(raw_response, str) and raw_response.strip():
        _render_raw_response(lines, raw_response)
        parsed = output.get("parsed")
        if isinstance(parsed, dict) and parsed:
            lines.append("◄ **파싱 요약**")
            for key, val in parsed.items():
                _render_kv(lines, key, val, indent=2)
            lines.append("")
        # recovery/validation 전용 부가 필드는 기존 렌더러로 보강
        if step_type == "recovery":
            _render_recovery_output(lines, output)
        elif step_type == "validation":
            _render_validation_layers(lines, output)
        return

    # LLM vs rule 라벨
    if step_type == "rule_decision":
        lines.append("◄ **판단**")
    elif step_type == "validation":
        lines.append("◄ **검증 결과**")
    elif step_type == "tool_execution":
        _render_tool_results(lines, output)
        return
    elif step_type == "analysis":
        _render_analysis_output(lines, output)
        return
    elif step_type == "recovery":
        lines.append("◄ **LLM 판단**")
        _render_recovery_output(lines, output)
        return
    else:
        lines.append("◄ **LLM 판단**")

    # ── 8-Slot 테이블 ──
    slot_data = output.get("8_slot")
    if slot_data and isinstance(slot_data, dict):
        lines.append("")
        lines.append("| Slot | 값 |")
        lines.append("|------|---|")
        for slot_key, slot_val in slot_data.items():
            lines.append(f"| {slot_key} | {_format_value_inline(slot_val)} |")
        lines.append("")

    # ── table_decisions (Context Interpreter) ──
    table_dec = output.get("table_decisions")
    if table_dec and isinstance(table_dec, dict):
        lines.append("  **테이블 선정**:")
        selected = table_dec.get("SELECTED", [])
        for item in selected:
            lines.append(f"    ✅ {item}")
        rejected = table_dec.get("REJECTED", [])
        for item in rejected:
            lines.append(f"    ❌ {item}")
        lines.append("")

    # ── knowledge_updates ──
    updates = output.get("knowledge_updates")
    if updates and isinstance(updates, list):
        lines.append("  **지식 갱신**:")
        for upd in updates:
            if isinstance(upd, str):
                # CONFIRMED → ✅, UNRESOLVED → ⚠
                icon = "✅" if "CONFIRMED" in upd else "⚠"
                lines.append(f"    {upd.replace('CONFIRMED', f'**CONFIRMED** {icon}').replace('UNRESOLVED', f'**UNRESOLVED** {icon}')}")
            else:
                lines.append(f"    {_format_value_inline(upd)}")
        lines.append("")

    # ── key_insights ──
    insights = output.get("key_insights")
    if insights and isinstance(insights, list):
        lines.append("  **인사이트**:")
        for ins in insights:
            lines.append(f"    \"{ins}\"")
        lines.append("")

    # ── Layer 검증 결과 (sql_validator) ──
    if step_type == "validation":
        _render_validation_layers(lines, output)
        return

    # ── SQL 코드블록 ──
    sql = output.get("sql")
    if sql and isinstance(sql, str):
        lines.append("")
        lines.append("```sql")
        lines.append(sql)
        lines.append("```")
        lines.append("")

    # ── 일반 키-값 (위에서 처리하지 않은 것들) ──
    handled_keys = {
        "8_slot", "table_decisions", "knowledge_updates",
        "key_insights", "sql",
        "layer1_rule", "layer2a_structural", "layer3_execution",
        "layer2b_semantic", "final_verdict",
    }
    remaining = {k: v for k, v in output.items() if k not in handled_keys}
    for key, val in remaining.items():
        _render_kv(lines, key, val, indent=2)

    lines.append("")


def _render_raw_response(
    lines: list[str],
    raw_response: str,
) -> None:
    """LLM 원본 응답을 JSON 코드블록으로 렌더링한다.

    JSON 파싱이 성공하면 pretty-print 하고, 실패하면 원본 텍스트를 그대로
    보존한다. [OUTPUT_CONTRACT] 에 정의된 형식을 재구성할 수 있도록 절단하지
    않는다.
    """
    lines.append("◄ **LLM 원본 응답 ([OUTPUT_CONTRACT])**")
    lines.append("")
    stripped = raw_response.strip()
    lines.append("```json")
    try:
        parsed_obj = json.loads(stripped)
        lines.append(
            json.dumps(parsed_obj, ensure_ascii=False, indent=2),
        )
    except (json.JSONDecodeError, ValueError):
        for line in raw_response.split("\n"):
            lines.append(line)
    lines.append("```")
    lines.append("")


def _render_validation_layers(
    lines: list[str],
    output: dict[str, Any],
) -> None:
    """SQL Validator의 Layer별 검증 결과를 3열 테이블로 렌더링한다."""
    lines.append("")
    lines.append("| Layer | 결과 | 상세 |")
    lines.append("|-------|------|------|")

    layer_defs = [
        ("layer1_rule", "L1 (safety+parse)"),
        ("layer2a_structural", "L2a (structural)"),
        ("layer3_execution", "L3 (execution)"),
        ("layer2b_semantic", "L2b (semantic)"),
    ]

    for key, label in layer_defs:
        layer = output.get(key, {})
        if not layer:
            continue
        status = layer.get("status", "?")
        icon = "✅" if status == "PASS" else "❌"
        detail = layer.get("detail", "")
        rows = layer.get("rows")
        latency = layer.get("latency", "")
        extra_parts = []
        if rows is not None:
            extra_parts.append(f"{rows}건")
        if latency:
            extra_parts.append(str(latency))
        if detail:
            extra_parts.append(str(detail))
        detail_text = ", ".join(extra_parts) if extra_parts else ""
        lines.append(f"| {label} | {icon} {status} | {detail_text} |")

    lines.append("")

    # L2b 상세 checks
    l2b = output.get("layer2b_semantic", {})
    checks = l2b.get("checks", {})
    if checks:
        lines.append("  L2b checks:")
        for check_key, check_val in checks.items():
            lines.append(f"    {check_key}: {check_val}")
        lines.append("")

    verdict = output.get("final_verdict", "")
    if verdict:
        lines.append(f"  **final_verdict: {verdict}**")
        lines.append("")


def _render_tool_results(
    lines: list[str],
    output: dict[str, Any],
) -> None:
    """tool_execution 스텝의 결과를 4열 테이블로 렌더링한다."""
    results = output.get("results", [])
    if not results:
        for key, val in output.items():
            _render_kv(lines, key, val, indent=0)
        lines.append("")
        return

    lines.append("")
    lines.append("| Step | Tool | 결과 | 요약 |")
    lines.append("|-----:|------|-----:|------|")
    for item in results:
        step_num = item.get("step", "")
        tool = item.get("tool", "")
        count = item.get("count", "")
        summary = _sanitize(item.get("summary", ""), 45)
        count_text = f"{count}건" if count != "" else ""
        lines.append(f"| {step_num} | {tool} | {count_text} | {summary} |")
    lines.append("")


def _render_analysis_output(
    lines: list[str],
    output: dict[str, Any],
) -> None:
    """분석 노드(analyzer)의 출력을 렌더링한다."""
    summary = output.get("summary", "")
    insights = output.get("insights", [])
    recs = output.get("recommendations", [])
    viz_judgment = output.get("viz_judgment", "")
    chart_type = output.get("chart_type", "")

    if summary:
        lines.append(f"◄ **분석 LLM**")
        lines.append(f"  summary: \"{summary}\"")

    if insights:
        lines.append("  insights:")
        for i, ins in enumerate(insights, 1):
            marker = _CYCLE_MARKERS[i - 1] if i <= len(_CYCLE_MARKERS) else f"{i}."
            lines.append(f"    {marker} \"{ins}\"")

    if recs:
        lines.append("  recommendations:")
        for rec in recs:
            lines.append(f"    \"{rec}\"")
    lines.append("")

    if viz_judgment:
        lines.append(f"◄ **시각화 판단 LLM**")
        lines.append(f"  judgment: {viz_judgment}")
        if chart_type:
            lines.append(f"  chart_type: {chart_type}")
        lines.append("")

    # 기타 키
    handled = {"summary", "insights", "recommendations", "viz_judgment", "chart_type"}
    for key, val in output.items():
        if key not in handled:
            _render_kv(lines, key, val, indent=2)


def _render_recovery_output(
    lines: list[str],
    output: dict[str, Any],
) -> None:
    """Recovery Agent 출력을 렌더링한다."""
    analysis = output.get("analysis", "")
    lessons = output.get("lessons_learned", "")
    action = output.get("action", "")
    new_hyp = output.get("new_hypothesis", {})
    new_plan = output.get("new_plan", [])

    if analysis:
        lines.append(f"  analysis: \"{analysis}\"")
    if lessons:
        lines.append(f"  lessons: \"{lessons}\"")
    if action:
        lines.append(f"  action: **{action}**")

    if isinstance(new_hyp, dict) and new_hyp:
        hyp_id = new_hyp.get("id", "")
        desc = new_hyp.get("description", "")
        strategy = new_hyp.get("strategy", "")
        lines.append(f"  new_hypothesis: {hyp_id} \"{desc}\"")
        if strategy:
            lines.append(f"    strategy: \"{strategy}\"")

    if new_plan:
        lines.append("  new_plan:")
        for p in new_plan:
            lines.append(f"    {p}")

    lines.append("")

    # 기타 키
    handled = {"analysis", "lessons_learned", "action", "new_hypothesis", "new_plan"}
    for key, val in output.items():
        if key not in handled:
            _render_kv(lines, key, val, indent=2)


def _render_kv(
    lines: list[str],
    key: str,
    val: Any,
    indent: int = 0,
) -> None:
    """키-값 쌍을 적절한 형식으로 렌더링한다."""
    prefix = " " * indent
    if val is None:
        return

    if isinstance(val, str):
        if "\n" in val:
            lines.append(f"{prefix}{key}:")
            for line in val.split("\n"):
                lines.append(f"{prefix}  {line}")
        else:
            lines.append(f"{prefix}{key}: \"{val}\"" if len(val) > 0 else f"{prefix}{key}: (없음)")
    elif isinstance(val, (int, float, bool)):
        lines.append(f"{prefix}{key}: {val}")
    elif isinstance(val, list):
        if not val:
            lines.append(f"{prefix}{key}: (없음)")
        elif all(isinstance(v, str) and len(v) <= 50 for v in val):
            # 짧은 리스트: 한 줄
            lines.append(f"{prefix}{key}: [{', '.join(str(v) for v in val)}]")
        else:
            # 긴 리스트: 각 줄
            lines.append(f"{prefix}{key}:")
            for item in val:
                lines.append(f"{prefix}  {_format_value_inline(item)}")
    elif isinstance(val, dict):
        lines.append(f"{prefix}{key}:")
        for k, v in val.items():
            _render_kv(lines, k, v, indent=indent + 2)
    else:
        lines.append(f"{prefix}{key}: {val}")


def _format_value_inline(val: Any) -> str:
    """값을 인라인 문자열로 변환한다."""
    if val is None:
        return "(없음)"
    if isinstance(val, str):
        return val if val else "(없음)"
    if isinstance(val, (int, float, bool)):
        return str(val)
    if isinstance(val, list):
        if not val:
            return "(없음)"
        if all(isinstance(v, str) and len(v) <= 50 for v in val):
            return f"[{', '.join(str(v) for v in val)}]"
        return json.dumps(val, ensure_ascii=False, default=str)
    if isinstance(val, dict):
        return json.dumps(val, ensure_ascii=False, default=str)
    return str(val)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. Node Flow (기존 번호 유지 — render_full_report에서 ## 3으로 재번호)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def render_node_flow(
    trace_data: dict[str, Any],
) -> str:
    """노드 흐름을 다이어그램으로 렌더링한다.

    이벤트 30개 이상이면 Flowchart, 미만이면 Sequence Diagram.
    """
    timeline = trace_data.get("timeline", [])
    node_path = trace_data.get("node_path", [])

    lines: list[str] = []
    lines.append("## 5. Node Flow")
    lines.append("")

    if not timeline:
        lines.append("> timeline 데이터 없음")
        return "\n".join(lines)

    if len(timeline) >= 30:
        lines.append("```mermaid")
        lines.append(
            _render_flowchart(trace_data, node_path),
        )
        lines.append("```")
    else:
        lines.append("```mermaid")
        lines.append(
            _render_sequence_diagram(timeline),
        )
        lines.append("```")

    lines.append("")
    return "\n".join(lines)


def _render_flowchart(
    trace_data: dict[str, Any],
    node_path: list[str],
) -> str:
    """사이클별 subgraph가 있는 Mermaid flowchart를 생성한다."""
    nodes_data = trace_data.get("nodes", [])
    decisions = trace_data.get("decisions", [])

    # 노드별 메트릭 수집
    node_metrics: dict[str, dict] = {}
    for n in nodes_data:
        name = n.get("node", "")
        node_metrics[name] = {
            "duration_ms": n.get("duration_ms", 0),
            "status": n.get("status", ""),
        }

    # 의사결정 노드 집합
    decision_nodes = {d.get("node", "") for d in decisions}

    cycles = _detect_cycles(node_path)

    flines: list[str] = ["flowchart TD"]

    # 고유 노드 ID 생성
    node_ids: dict[str, str] = {}
    id_counter = 0

    for ci, cycle in enumerate(cycles):
        cycle_label = (
            "초기 실행" if ci == 0
            else f"재시도 {_CYCLE_MARKERS[min(ci - 1, len(_CYCLE_MARKERS) - 1)]}"
        )

        if len(cycles) > 1:
            flines.append(
                f"    subgraph cycle{ci}"
                f'["{cycle_label}"]'
            )

        prev_id = None
        for node in cycle:
            id_counter += 1
            nid = f"n{id_counter}"
            node_ids.setdefault(
                f"{node}_{ci}", nid,
            )

            metrics = node_metrics.get(node, {})
            dur = metrics.get("duration_ms", 0)
            status = metrics.get("status", "")
            layer = _NODE_LAYER.get(node, "?")

            # 노드 모양: 에러 → 빨간색, 결정 → 마름모
            dur_text = _fmt_duration(dur)
            if status == "error":
                flines.append(
                    f"    {nid}"
                    f'[["❌ {node}<br/>{dur_text}"]]'
                )
                flines.append(
                    f"    style {nid} fill:#fee,stroke:#c00"
                )
            elif node in decision_nodes:
                label = _sanitize(
                    f"⚡ {node}<br/>{dur_text}", 60,
                )
                flines.append(
                    f"    {nid}" + "{" + label + "}"
                )
            else:
                flines.append(
                    f"    {nid}"
                    f'["{node}<br/>{layer} | {dur_text}"]'
                )

            if prev_id:
                flines.append(f"    {prev_id} --> {nid}")
            prev_id = nid

        if len(cycles) > 1:
            flines.append("    end")

    # 사이클 간 연결
    for ci in range(len(cycles) - 1):
        last_node = cycles[ci][-1]
        first_node = cycles[ci + 1][0]
        from_id = node_ids.get(f"{last_node}_{ci}")
        to_id = node_ids.get(f"{first_node}_{ci + 1}")
        if from_id and to_id:
            flines.append(
                f"    {from_id} -.->|재시도| {to_id}"
            )

    return "\n".join(flines)


def _render_sequence_diagram(
    timeline: list[dict[str, Any]],
) -> str:
    """타임라인을 Mermaid sequence diagram으로 변환한다."""
    lines: list[str] = []
    lines.append("sequenceDiagram")
    lines.append("    title Pipeline Execution Timeline")
    lines.append("")

    # participant 선언
    seen_nodes: list[str] = []
    for entry in timeline:
        node = entry.get("node", "")
        if node and node not in seen_nodes:
            seen_nodes.append(node)

    lines.append("    participant User")
    for node in seen_nodes:
        layer = _NODE_LAYER.get(node, "reason")
        alias = _abbreviate(node)
        lines.append(
            f"    participant {alias} as "
            f"{node}<br/>({layer})"
        )
    lines.append("    participant LLM")
    lines.append("    participant Tool")
    lines.append("")

    # 시퀀스 생성
    prev_node = ""
    for entry in timeline:
        etype = entry.get("event_type", "")
        node = entry.get("node", "")
        seq = entry.get("seq", 0)
        summary = _sanitize(entry.get("summary", ""), 45)
        dur = entry.get("duration_ms", 0)
        status = entry.get("status", "")
        alias = _abbreviate(node)

        if etype == "node_start":
            src = (
                _abbreviate(prev_node) if prev_node
                else "User"
            )
            lines.append(
                f"    {src}->>+{alias}: "
                f"[{seq}] {summary}"
            )
            prev_node = node

        elif etype == "node_end":
            dur_str = f" ({dur:.0f}ms)" if dur else ""
            if status == "error":
                lines.append(
                    f"    Note right of {alias}: "
                    f"❌ [{seq}] {status}{dur_str}"
                )
            lines.append(f"    deactivate {alias}")

        elif etype == "llm_call":
            dur_str = f"{dur:.0f}ms" if dur else ""
            lines.append(
                f"    {alias}->>+LLM: [{seq}] {summary}"
            )
            lines.append(
                f"    LLM-->>-{alias}: 응답 {dur_str}"
            )

        elif etype == "tool_call":
            mark = "x" if status == "error" else ">>"
            ret_mark = "--x" if status == "error" else "-->>"
            lines.append(
                f"    {alias}-{mark}+Tool: "
                f"[{seq}] {summary}"
            )
            dur_str = f"{dur:.0f}ms" if dur else ""
            ret_label = (
                f"실패 {dur_str}" if status == "error"
                else f"결과 {dur_str}"
            )
            lines.append(
                f"    Tool{ret_mark}-{alias}: {ret_label}"
            )

        elif etype == "decision":
            lines.append(
                f"    Note over {alias}: "
                f"[{seq}] ⚡ {summary}"
            )

    return "\n".join(lines)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 6. Performance
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def render_performance(
    trace_data: dict[str, Any],
) -> str:
    """성능 분석: 사이클별 Gantt + LLM 비용 테이블."""
    node_path = trace_data.get("node_path", [])
    llm_calls = trace_data.get("llm_calls", [])
    nodes_data = trace_data.get("nodes", [])

    lines: list[str] = []
    lines.append("## 6. Performance")
    lines.append("")

    # ── Gantt Chart ──
    if nodes_data:
        lines.append("### 노드 실행 타이밍")
        lines.append("")
        lines.append("```mermaid")
        lines.append(_render_gantt(nodes_data, node_path))
        lines.append("```")
        lines.append("")

    # ── LLM 비용 분석 ──
    if llm_calls:
        lines.append("### LLM 호출 분석")
        lines.append("")

        # 노드별 LLM 집계
        by_node: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "count": 0,
                "tokens": 0,
                "latency_ms": 0.0,
            }
        )
        total_tokens = 0
        total_latency = 0.0

        for call in llm_calls:
            node = call.get("node", "unknown")
            tokens = (
                call.get("prompt_tokens", 0)
                + call.get("response_tokens", 0)
            )
            latency = call.get("latency_ms", 0)
            by_node[node]["count"] += 1
            by_node[node]["tokens"] += tokens
            by_node[node]["latency_ms"] += latency
            total_tokens += tokens
            total_latency += latency

        lines.append(
            "| 노드 | 호출 수 | 토큰 | 소요시간 | 비중 |"
        )
        lines.append(
            "|------|-------:|-----:|--------:|-----:|"
        )

        for node, stats in sorted(
            by_node.items(),
            key=lambda x: x[1]["tokens"],
            reverse=True,
        ):
            pct = (
                (stats["tokens"] / total_tokens * 100)
                if total_tokens else 0
            )
            lines.append(
                f"| {node} | {stats['count']} "
                f"| {_fmt_tokens(stats['tokens'])} "
                f"| {_fmt_duration(stats['latency_ms'])} "
                f"| {pct:.0f}% |"
            )

        lines.append(
            f"| **합계** | **{len(llm_calls)}** "
            f"| **{_fmt_tokens(total_tokens)}** "
            f"| **{_fmt_duration(total_latency)}** "
            f"| 100% |"
        )
        lines.append("")

    return "\n".join(lines)


def _render_gantt(
    nodes_data: list[dict[str, Any]],
    node_path: list[str],
) -> str:
    """사이클별로 그룹핑된 Gantt chart를 생성한다."""
    gantt_lines: list[str] = [
        "gantt",
        "    title Node Execution Gantt",
        "    dateFormat X",
        "    axisFormat %s",
        "",
    ]

    cycles = _detect_cycles(node_path)

    # 노드별 실행 시간 매핑 (순서 보장)
    node_durations: list[tuple[str, float]] = []
    for n in nodes_data:
        name = n.get("node", "")
        dur = n.get("duration_ms", 0)
        node_durations.append((name, dur))

    # 사이클별 section 생성
    node_idx = 0
    visit_counts: dict[str, int] = {}

    for ci, cycle in enumerate(cycles):
        section_name = (
            "초기 실행" if ci == 0
            else f"재시도 {_CYCLE_MARKERS[min(ci - 1, len(_CYCLE_MARKERS) - 1)]}"
        )
        gantt_lines.append(f"    section {section_name}")

        for node_name in cycle:
            # 매칭하는 node_durations 항목 찾기
            dur_ms = 0.0
            for idx in range(node_idx, len(node_durations)):
                if node_durations[idx][0] == node_name:
                    dur_ms = node_durations[idx][1]
                    node_idx = idx + 1
                    break

            visit_counts[node_name] = (
                visit_counts.get(node_name, 0) + 1
            )
            label = _node_visit_label(
                node_name, visit_counts,
            )

            dur_s = max(dur_ms / 1000, 0.1)
            crit = "crit," if dur_ms > 10000 else ""
            gantt_lines.append(
                f"    {label} :{crit} "
                f"0, {round(dur_s, 1)}s"
            )

    return "\n".join(gantt_lines)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 7. Automated Findings
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def render_findings(
    trace_data: dict[str, Any],
) -> str:
    """trace_analyzer 결과를 보고서에 통합한다."""
    from src.utils.tracker.trace_analyzer import (
        Finding,
        _check_context_retrieval,
        _check_decisions,
        _check_llm_calls,
        _check_node_performance,
        _check_pipeline_flow,
        _check_sql_quality,
        _check_timeline,
    )

    lines: list[str] = []
    lines.append("## 7. Automated Findings")
    lines.append("")

    # 분석 실행
    all_findings: list[Finding] = []
    all_findings.extend(_check_context_retrieval(trace_data))
    all_findings.extend(_check_llm_calls(trace_data))
    all_findings.extend(_check_decisions(trace_data))
    all_findings.extend(_check_sql_quality(trace_data))
    all_findings.extend(_check_pipeline_flow(trace_data))
    all_findings.extend(_check_node_performance(trace_data))
    all_findings.extend(_check_timeline(trace_data))

    # final_status가 비 SQL이면 SQL 관련 경고 억제
    final_status = trace_data.get("final_status", "")
    if final_status in _NON_SQL_STATUSES:
        all_findings = [
            f for f in all_findings
            if f.category != "sql"
        ]

    if not all_findings:
        lines.append("> 특이 사항 없음")
        lines.append("")
        return "\n".join(lines)

    # 심각도 순 정렬
    severity_order = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}
    all_findings.sort(
        key=lambda f: severity_order.get(f.severity, 9),
    )

    for finding in all_findings:
        icon = _SEVERITY_ICONS.get(finding.severity, "")
        lines.append(
            f"- {icon} **{finding.severity}** "
            f"[{finding.stage}] {finding.message}"
        )
        if finding.detail:
            lines.append(
                f"  - {_sanitize(finding.detail, 100)}"
            )

    lines.append("")

    # 집계
    counts: dict[str, int] = defaultdict(int)
    for f in all_findings:
        counts[f.severity] += 1
    summary_parts = [
        f"{sev} {cnt}건"
        for sev, cnt in sorted(counts.items())
    ]
    lines.append(f"> 합계: {', '.join(summary_parts)}")
    lines.append("")

    return "\n".join(lines)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Appendix: Detailed Timeline
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def render_detail_table(
    timeline: list[dict[str, Any]],
) -> str:
    """부모-자식 들여쓰기가 적용된 상세 타임라인 테이블."""
    if not timeline:
        return ""

    lines: list[str] = []
    lines.append("## Appendix: Detailed Timeline")
    lines.append("")

    header = (
        "| Seq | Type | Node | Summary "
        "| Detail | Duration | Status | State Changes |"
    )
    sep = (
        "|----:|------|------|-------"
        "|--------|--------:|--------|---------------|"
    )
    lines.extend([header, sep])

    # parent_seq → node_start seq 매핑으로 들여쓰기 결정
    node_start_seqs: set[int] = set()
    for entry in timeline:
        if entry.get("event_type") == "node_start":
            node_start_seqs.add(entry.get("seq", 0))

    for entry in timeline:
        seq = entry.get("seq", 0)
        etype = entry.get("event_type", "")
        icon = _EVENT_ICONS.get(etype, "")
        node = entry.get("node", "")
        summary = _sanitize(
            entry.get("summary", ""), 40,
        )
        detail_data = entry.get("detail", {})
        dur = entry.get("duration_ms", 0)
        dur_str = f"{dur:.0f}ms" if dur else "-"
        status = entry.get("status", "-") or "-"
        parent_seq = entry.get("parent_seq")

        # 들여쓰기: parent_seq가 있고 node_start/end가 아니면
        indent = ""
        if (
            parent_seq
            and etype not in ("node_start", "node_end")
        ):
            indent = "  "

        # 상세 포맷
        detail_str = _format_detail(detail_data, etype)

        # state_changes (node_end만)
        state_str = "-"
        if etype == "node_end":
            changes = detail_data.get("state_changes", [])
            if changes:
                parts = [
                    f"{c.get('field', '?')}: "
                    f"{c.get('after', '?')}"
                    for c in changes[:3]
                ]
                state_str = "; ".join(parts)
                if len(changes) > 3:
                    state_str += f" (+{len(changes) - 3})"

        lines.append(
            f"| {seq} "
            f"| {indent}{icon} {etype} "
            f"| {node} "
            f"| {summary} "
            f"| {detail_str} "
            f"| {dur_str} "
            f"| {status} "
            f"| {_sanitize(state_str, 40)} |"
        )

    lines.append("")
    return "\n".join(lines)


def _format_detail(
    detail: dict[str, Any],
    event_type: str,
) -> str:
    """이벤트 타입에 따라 detail을 요약 문자열로 변환한다."""
    if not detail:
        return "-"

    if event_type == "llm_call":
        model = detail.get("model", "")
        pt = detail.get("prompt_tokens", 0)
        rt = detail.get("response_tokens", 0)
        return f"{model} {pt}+{rt}tok"

    if event_type == "tool_call":
        source = detail.get("source", "")
        query = _sanitize(detail.get("query", ""), 25)
        cnt = detail.get("results_count", 0)
        return f"{source}: '{query}' → {cnt}건"

    if event_type == "decision":
        chosen = detail.get("chosen", "")
        conf = detail.get("confidence", 0)
        reason = _sanitize(detail.get("reason", ""), 25)
        return f"{chosen} ({conf:.0%}) {reason}"

    if event_type == "node_end":
        changes = detail.get("state_changes", [])
        if changes:
            return f"{len(changes)}개 필드 변경"
        return "-"

    # fallback
    text = json.dumps(
        detail, ensure_ascii=False, default=str,
    )
    return _sanitize(text, 50)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 통합 보고서 생성 / 저장
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def render_full_report(
    trace_data: dict[str, Any],
) -> str:
    """트레이스 전체를 5섹션 + Appendix Markdown 보고서로 렌더링한다.

    섹션 구조:
        1. Executive Summary
        2. Reasoning Flow (기존 Decision Trail + Referenced Info + State Evolution 대체)
        3. Node Flow
        4. Performance
        5. Automated Findings
        Appendix: Detailed Timeline
        Appendix: Generated SQL

    Args:
        trace_data: EvaluationTrace.model_dump() 결과

    Returns:
        Markdown 문자열
    """
    run_id = trace_data.get("run_id", "unknown")

    parts: list[str] = []

    # 타이틀
    parts.append(f"# Pipeline Trace: {run_id}")
    parts.append("")

    # 1. Executive Summary
    parts.append(render_executive_summary(trace_data))

    # 2. Reasoning Flow (기존 2+3+4 대체, fallback 보존)
    parts.append(render_reasoning_flow(trace_data))

    # 3. Node Flow (기존 섹션 5 → 3으로 재번호)
    node_flow = render_node_flow(trace_data)
    parts.append(node_flow.replace("## 5. Node Flow", "## 3. Node Flow"))

    # 4. Performance (기존 섹션 6 → 4로 재번호)
    perf = render_performance(trace_data)
    parts.append(perf.replace("## 6. Performance", "## 4. Performance"))

    # 5. Automated Findings (기존 섹션 7 → 5로 재번호)
    findings = render_findings(trace_data)
    parts.append(findings.replace("## 7. Automated Findings", "## 5. Automated Findings"))

    # Appendix: Detailed Timeline
    timeline = trace_data.get("timeline", [])
    if timeline:
        parts.append(render_detail_table(timeline))

    # Appendix: Generated SQL
    sql_rec = trace_data.get("sql") or {}
    if sql_rec.get("generated_sql"):
        parts.append("## Appendix: Generated SQL")
        parts.append("")
        parts.append("```sql")
        from src.utils.sql_formatter import format_sql_tabular
        parts.append(format_sql_tabular(sql_rec["generated_sql"]))
        parts.append("```")
        parts.append("")

    return "\n".join(parts)


def save_report(
    trace_data: dict[str, Any],
    output_path: str | Path | None = None,
) -> Path:
    """트레이스 보고서를 Markdown 파일로 저장한다.

    Args:
        trace_data: EvaluationTrace.model_dump() 결과
        output_path: 저장 경로 (미지정 시 자동 생성)

    Returns:
        저장된 파일 경로
    """
    if output_path is None:
        run_id = trace_data.get("run_id", "unknown")
        output_path = Path(settings.eval_tracker_output_dir) / f"report_{run_id}.md"

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    content = render_full_report(trace_data)
    path.write_text(content, encoding="utf-8")

    logger.info("트레이스 보고서 저장", path=str(path))
    return path


def render_from_json(
    json_path: str | Path,
) -> str:
    """JSON 트레이스 파일에서 전체 보고서를 생성한다."""
    data = json.loads(
        Path(json_path).read_text(encoding="utf-8"),
    )
    return render_full_report(data)
