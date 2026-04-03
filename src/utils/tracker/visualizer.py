"""트레이스 타임라인 시각화 — 7섹션 보고서.

DataCopilotCallbackHandler가 기록한 EvaluationTrace를 기반으로
7개 섹션 + Appendix 형태의 Markdown 보고서를 생성한다.

섹션 구조:
    1. Executive Summary     — 규칙 기반 자연어 요약
    2. Decision Trail        — 핵심 의사결정 phase별 시간순
    3. Referenced Information — 소스별 그룹핑된 참조 정보
    4. State Evolution       — 노드별 state 변화 compact 테이블
    5. Node Flow             — 요약 다이어그램 (30+ → Flowchart, <30 → Sequence)
    6. Performance           — 사이클별 Gantt + LLM 비용 분석
    7. Automated Findings    — trace_analyzer 결과 통합
    [Appendix] Detailed Timeline — 부모-자식 들여쓰기 상세 테이블

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

from src.utils.logger import get_logger

logger = get_logger(__name__)

# ── 상수 ─────────────────────────────────────────────

_LAYER_ORDER = ["User", "interpret", "reason", "present"]

_NODE_LAYER: dict[str, str] = {
    "preprocess": "interpret",
    "resolve_history": "interpret",
    "classify_intent": "interpret",
    "normalize_query": "interpret",
    "clarify": "interpret",
    "reasoning_preparer": "reason",
    "context_explorer": "reason",
    "knowledge_fetcher": "reason",
    "knowledge_interpreter": "reason",
    "readiness_gate": "reason",
    "recovery_agent": "reason",
    "sql_generator": "reason",
    "sql_validator": "reason",
    "recovery_planner": "reason",
    "result_finalizer": "reason",
    "execute_sql": "present",
    "analyze_data": "present",
    "format_response": "present",
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
        tables = detail.get("candidate_tables", [])
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
            tables = detail.get("candidate_tables", [])
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
# 5. Node Flow
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
    counts = defaultdict(int)
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
    """트레이스 전체를 7섹션 Markdown 보고서로 렌더링한다.

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

    # 7 섹션 + Appendix
    parts.append(render_executive_summary(trace_data))
    parts.append(render_decision_trail(trace_data))
    parts.append(render_referenced_info(trace_data))
    parts.append(render_state_evolution(trace_data))
    parts.append(render_node_flow(trace_data))
    parts.append(render_performance(trace_data))
    parts.append(render_findings(trace_data))

    # Appendix
    timeline = trace_data.get("timeline", [])
    if timeline:
        parts.append(render_detail_table(timeline))

    # SQL 원문 (생성된 경우)
    sql_rec = trace_data.get("sql") or {}
    if sql_rec.get("generated_sql"):
        parts.append("## Appendix: Generated SQL")
        parts.append("")
        parts.append("```sql")
        parts.append(sql_rec["generated_sql"])
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
        output_path = Path(
            f"logs/traces/report_{run_id}.md"
        )

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
