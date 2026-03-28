"""트레이스 타임라인 시각화 — Mermaid 다이어그램 + seq 상세 테이블.

EvaluationTracker가 기록한 timeline을 기반으로:
1. Mermaid sequence diagram — 노드·도구·LLM 간 호출 흐름
2. seq별 상세 테이블 — 각 이벤트의 in/out, 소요시간, 상태

사용법::

    from src.utils.tracker.visualizer import (
        render_mermaid,
        render_detail_table,
        render_full_report,
        save_report,
    )

    # EvaluationTracker 또는 JSON 트레이스에서 생성
    mermaid_text = render_mermaid(tracker.trace.timeline)
    table_text = render_detail_table(tracker.trace.timeline)

    # 한 번에 전체 보고서 (Mermaid + 테이블) 생성·저장
    save_report(tracker.trace, output_path="report.md")
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.utils.logger import get_logger

logger = get_logger(__name__)

# ── Mermaid 렌더링 상수 ────────────────────────────

# 이벤트 타입별 Mermaid participant 매핑
_LAYER_ORDER = [
    "User",
    "interpret",
    "reason",
    "present",
]

_NODE_LAYER: dict[str, str] = {
    "preprocess": "interpret",
    "resolve_history": "interpret",
    "classify_intent": "interpret",
    "normalize_query": "interpret",
    "clarify": "interpret",
    "reason_plan": "reason",
    "reason_explore": "reason",
    "reason_evaluate": "reason",
    "reason_generate_sql": "reason",
    "reason_validate_sql": "reason",
    "reason_recover": "reason",
    "reason_finalize": "reason",
    "execute_sql": "present",
    "analyze_data": "present",
    "format_response": "present",
}

# 이벤트 타입별 아이콘
_EVENT_ICONS: dict[str, str] = {
    "node_start": "▶",
    "node_end": "■",
    "llm_call": "🤖",
    "tool_call": "🔧",
    "decision": "⚡",
}


def _sanitize(text: str, max_len: int = 50) -> str:
    """Mermaid 안전 문자열로 변환한다."""
    safe = (
        text.replace('"', "'")
        .replace("\n", " ")
        .replace("#", "")
        .replace(";", ",")
    )
    if len(safe) > max_len:
        safe = safe[:max_len] + "…"
    return safe


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. Mermaid Sequence Diagram
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def render_mermaid(
    timeline: list[dict[str, Any]],
    *,
    title: str = "Pipeline Execution Timeline",
) -> str:
    """타임라인을 Mermaid sequence diagram으로 변환한다.

    Args:
        timeline: TimelineEntry 딕셔너리 리스트
        title: 다이어그램 제목

    Returns:
        Mermaid 텍스트 (```mermaid 블록 미포함)
    """
    lines: list[str] = []
    lines.append("sequenceDiagram")
    lines.append(f"    title {title}")
    lines.append("")

    # participant 선언 — 등장 순서 기준
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
            f"    participant {alias} as {node}<br/>({layer})"
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
        summary = _sanitize(
            entry.get("summary", ""), 45,
        )
        dur = entry.get("duration_ms", 0)
        status = entry.get("status", "")
        alias = _abbreviate(node)

        if etype == "node_start":
            if not prev_node:
                lines.append(
                    f"    User->>+{alias}: "
                    f"[{seq}] {summary}"
                )
            else:
                prev_alias = _abbreviate(prev_node)
                lines.append(
                    f"    {prev_alias}->>+{alias}: "
                    f"[{seq}] {summary}"
                )
            prev_node = node

        elif etype == "node_end":
            dur_str = f" ({dur:.0f}ms)" if dur else ""
            note = f"[{seq}] {status}{dur_str}"
            if status == "error":
                lines.append(
                    f"    Note right of {alias}: "
                    f"❌ {note}"
                )
            lines.append(
                f"    deactivate {alias}"
            )

        elif etype == "llm_call":
            dur_str = f"{dur:.0f}ms" if dur else ""
            lines.append(
                f"    {alias}->>+LLM: "
                f"[{seq}] {summary}"
            )
            lines.append(
                f"    LLM-->>-{alias}: "
                f"응답 {dur_str}"
            )

        elif etype == "tool_call":
            mark = "x" if status == "error" else ">>"
            ret_mark = (
                "--x" if status == "error" else "-->>"
            )
            lines.append(
                f"    {alias}-{mark}+Tool: "
                f"[{seq}] {summary}"
            )
            dur_str = f"{dur:.0f}ms" if dur else ""
            ret_label = (
                f"실패 {dur_str}"
                if status == "error"
                else f"결과 {dur_str}"
            )
            lines.append(
                f"    Tool{ret_mark}-{alias}: "
                f"{ret_label}"
            )

        elif etype == "decision":
            lines.append(
                f"    Note over {alias}: "
                f"[{seq}] ⚡ {summary}"
            )

    return "\n".join(lines)


def _abbreviate(node_name: str) -> str:
    """노드 이름을 Mermaid participant ID로 축약한다."""
    # 공백·특수문자 없는 안전한 ID
    return node_name.replace("_", "")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. 상세 Seq 테이블
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def render_detail_table(
    timeline: list[dict[str, Any]],
) -> str:
    """seq별 상세 테이블을 Markdown으로 생성한다.

    각 행에 seq, 이벤트 타입, 노드, 요약, in/out 상세,
    소요시간, 상태를 표시한다.
    """
    header = (
        "| Seq | Type | Node | Summary "
        "| Detail (In/Out) | Duration | Status |"
    )
    sep = (
        "|----:|------|------|-------"
        "|-----------------|--------:|--------|"
    )
    rows: list[str] = [header, sep]

    for entry in timeline:
        seq = entry.get("seq", 0)
        etype = entry.get("event_type", "")
        icon = _EVENT_ICONS.get(etype, "")
        node = entry.get("node", "")
        summary = _sanitize(
            entry.get("summary", ""), 40,
        )
        detail = _format_detail(
            entry.get("detail", {}), etype,
        )
        dur = entry.get("duration_ms", 0)
        dur_str = (
            f"{dur:.0f}ms" if dur else "-"
        )
        status = entry.get("status", "-") or "-"

        rows.append(
            f"| {seq} "
            f"| {icon} {etype} "
            f"| {node} "
            f"| {summary} "
            f"| {detail} "
            f"| {dur_str} "
            f"| {status} |"
        )

    return "\n".join(rows)


def _format_detail(
    detail: dict[str, Any],
    event_type: str,
) -> str:
    """이벤트 타입에 따라 detail을 요약 문자열로 변환."""
    if not detail:
        return "-"

    if event_type == "node_end":
        parts: list[str] = []
        inp = detail.get("input", {})
        out = detail.get("output", {})
        if inp:
            keys = ", ".join(list(inp.keys())[:3])
            parts.append(f"in:[{keys}]")
        if out:
            keys = ", ".join(list(out.keys())[:3])
            parts.append(f"out:[{keys}]")
        return " ".join(parts) if parts else "-"

    if event_type == "llm_call":
        model = detail.get("model", "")
        pt = detail.get("prompt_tokens", 0)
        rt = detail.get("response_tokens", 0)
        preview = _sanitize(
            detail.get("response_preview", ""), 30,
        )
        return f"{model} {pt}+{rt}tok '{preview}'"

    if event_type == "tool_call":
        tool = detail.get("tool", "")
        query = _sanitize(
            detail.get("query", ""), 30,
        )
        cnt = detail.get("results_count", 0)
        return f"{tool}: '{query}' → {cnt}건"

    if event_type == "decision":
        chosen = detail.get("chosen", "")
        conf = detail.get("confidence", 0)
        reason = _sanitize(
            detail.get("reason", ""), 30,
        )
        return f"{chosen} ({conf:.0%}) {reason}"

    # fallback
    text = json.dumps(
        detail, ensure_ascii=False, default=str,
    )
    return _sanitize(text, 60)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. Gantt Chart (노드별 타이밍)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def render_gantt(
    timeline: list[dict[str, Any]],
    *,
    title: str = "Node Execution Gantt",
) -> str:
    """노드 실행 구간을 Mermaid gantt chart로 변환한다."""
    lines: list[str] = [
        "gantt",
        f"    title {title}",
        "    dateFormat X",
        "    axisFormat %s",
        "",
    ]

    # node_start/end 쌍으로 구간 산출
    starts: dict[str, dict] = {}
    sections: dict[str, list[tuple]] = {}

    for entry in timeline:
        etype = entry.get("event_type", "")
        node = entry.get("node", "")

        if etype == "node_start":
            starts[node] = entry
        elif etype == "node_end" and node in starts:
            dur = entry.get("duration_ms", 0)
            layer = _NODE_LAYER.get(node, "etc")
            sections.setdefault(layer, []).append(
                (node, dur),
            )
            del starts[node]

    # 누적 오프셋 계산 (실제 timestamp 대신 상대 시간)
    offset = 0.0
    for layer in _LAYER_ORDER[1:]:  # skip User
        items = sections.get(layer, [])
        if not items:
            continue
        lines.append(f"    section {layer}")
        for node_name, dur_ms in items:
            dur_s = max(dur_ms / 1000, 0.1)
            crit = "crit," if dur_ms > 10000 else ""
            end = round(offset + dur_s, 1)
            lines.append(
                f"    {node_name} :{crit} "
                f"{round(offset, 1)}, {end}s"
            )
            offset = end

    return "\n".join(lines)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. 통합 보고서
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def render_full_report(
    trace_data: dict[str, Any],
) -> str:
    """트레이스 전체를 Markdown 보고서로 렌더링한다.

    Args:
        trace_data: EvaluationTrace.model_dump() 결과

    Returns:
        Markdown 문자열 (Mermaid + Gantt + 상세 테이블)
    """
    timeline = trace_data.get("timeline", [])
    run_id = trace_data.get("run_id", "unknown")
    user_input = trace_data.get("user_input", "")
    status = trace_data.get("final_status", "")
    dur = trace_data.get("total_duration_ms", 0)
    llm_calls = trace_data.get("total_llm_calls", 0)
    llm_tokens = trace_data.get("total_llm_tokens", 0)

    parts: list[str] = []

    # 헤더
    parts.append(f"# Pipeline Trace: {run_id}")
    parts.append("")
    parts.append(f"- **입력**: {user_input}")
    parts.append(f"- **상태**: {status}")
    parts.append(f"- **총 소요**: {dur:.0f}ms")
    parts.append(
        f"- **LLM**: {llm_calls}회, {llm_tokens}토큰"
    )
    parts.append("")

    # 1) Sequence Diagram
    if timeline:
        parts.append("## Execution Flow")
        parts.append("")
        parts.append("```mermaid")
        parts.append(render_mermaid(timeline))
        parts.append("```")
        parts.append("")

        # 2) Gantt Chart
        parts.append("## Node Timing")
        parts.append("")
        parts.append("```mermaid")
        parts.append(render_gantt(timeline))
        parts.append("```")
        parts.append("")

        # 3) Detail Table
        parts.append("## Event Detail")
        parts.append("")
        parts.append(render_detail_table(timeline))
        parts.append("")
    else:
        parts.append(
            "> timeline 데이터 없음 — "
            "tracker 활성화 여부를 확인하세요."
        )

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
            f"evaluation/traces/report_{run_id}.md"
        )

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    content = render_full_report(trace_data)
    path.write_text(content, encoding="utf-8")

    logger.info(
        "트레이스 보고서 저장", path=str(path),
    )
    return path


def render_from_json(
    json_path: str | Path,
) -> str:
    """JSON 트레이스 파일에서 전체 보고서를 생성한다."""
    data = json.loads(
        Path(json_path).read_text(encoding="utf-8"),
    )
    return render_full_report(data)
