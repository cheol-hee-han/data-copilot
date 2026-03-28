"""트레이스 자동 분석 유틸리티 — e2e 테스트 후 보완점 도출.

EvaluationTracker가 생성한 JSON 트레이스 파일을 읽어서
SQL 정확도에 영향을 주는 병목, 실패 패턴, 개선 기회를 자동으로 도출한다.

사용 방법:
    from src.utils.tracker.trace_analyzer import analyze_trace, analyze_batch

    # 단일 트레이스 분석
    findings = analyze_trace("traces/trace_001.json")
    for f in findings:
        print(f"{f.severity}: {f.message}")

    # 배치 분석 (디렉토리 내 모든 트레이스)
    report = analyze_batch("traces/")
    print(report.summary)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Finding:
    """분석에서 발견된 개선 사항."""

    severity: str  # "CRITICAL", "WARNING", "INFO"
    category: str  # "context", "sql", "llm", "pipeline", "accuracy"
    stage: str     # 발생한 파이프라인 단계
    message: str   # 사람이 읽을 수 있는 설명
    detail: str = ""  # 추가 상세 정보


@dataclass
class TraceReport:
    """단일 트레이스 분석 보고서."""

    run_id: str
    user_input: str
    final_status: str
    findings: list[Finding] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class BatchReport:
    """배치 트레이스 분석 보고서."""

    total_runs: int = 0
    success_count: int = 0
    failure_count: int = 0
    findings_by_severity: dict[str, int] = field(default_factory=dict)
    top_findings: list[Finding] = field(default_factory=list)
    per_run: list[TraceReport] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        """성공률 (0.0~1.0)."""
        return self.success_count / self.total_runs if self.total_runs else 0.0

    @property
    def summary(self) -> str:
        """사람이 읽을 수 있는 요약."""
        lines = [
            f"=== 배치 분석 결과 ({self.total_runs}건) ===",
            f"성공률: {self.success_rate:.0%} "
            f"({self.success_count}/{self.total_runs})",
            "",
            "심각도별 발견 사항:",
        ]
        for sev in ["CRITICAL", "WARNING", "INFO"]:
            count = self.findings_by_severity.get(sev, 0)
            if count:
                lines.append(f"  {sev}: {count}건")

        if self.top_findings:
            lines.append("")
            lines.append("주요 발견 사항:")
            for f in self.top_findings[:10]:
                lines.append(f"  [{f.severity}] {f.message}")

        return "\n".join(lines)


# ── 단일 트레이스 분석 ──────────────────────────────────────────


def analyze_trace(path: str | Path) -> TraceReport:
    """JSON 트레이스 파일을 분석하여 보완점을 도출한다."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    report = TraceReport(
        run_id=data.get("run_id", ""),
        user_input=data.get("user_input", ""),
        final_status=data.get("final_status", ""),
    )

    # 메트릭 수집
    report.metrics = _collect_metrics(data)

    # 분석 규칙 적용
    report.findings.extend(_check_context_retrieval(data))
    report.findings.extend(_check_llm_calls(data))
    report.findings.extend(_check_decisions(data))
    report.findings.extend(_check_sql_quality(data))
    report.findings.extend(_check_pipeline_flow(data))
    report.findings.extend(_check_node_performance(data))
    report.findings.extend(_check_timeline(data))

    return report


def _collect_metrics(data: dict) -> dict[str, Any]:
    """트레이스에서 주요 메트릭을 수집한다."""
    llm_calls = data.get("llm_calls", [])
    nodes = data.get("nodes", [])
    ctx = data.get("context_retrievals", [])
    sql_rec = data.get("sql", {})

    return {
        "total_duration_ms": data.get("total_duration_ms", 0),
        "llm_call_count": len(llm_calls),
        "total_llm_latency_ms": sum(
            c.get("latency_ms", 0) for c in llm_calls
        ),
        "total_llm_tokens": sum(
            c.get("prompt_tokens", 0) + c.get("response_tokens", 0)
            for c in llm_calls
        ),
        "node_count": len(nodes),
        "context_retrieval_count": len(ctx),
        "sql_retry_count": sql_rec.get("retry_count", 0),
        "sql_validated": sql_rec.get("validated", False),
        "sql_execution_success": sql_rec.get("execution_success", False),
        "sql_row_count": sql_rec.get("row_count", 0),
    }


# ── 분석 규칙 ──────────────────────────────────────────────────


def _check_context_retrieval(data: dict) -> list[Finding]:
    """컨텍스트 수집 관련 문제를 점검한다."""
    findings: list[Finding] = []
    retrievals = data.get("context_retrievals", [])

    if not retrievals:
        findings.append(Finding(
            severity="CRITICAL",
            category="context",
            stage="context_explorer",
            message="컨텍스트 수집 기록 없음 — 도구 실행이 전혀 추적되지 않았음",
        ))
        return findings

    # 도구별 성공/실패 집계
    tool_counts: dict[str, int] = {}
    zero_result_tools: list[str] = []
    slow_tools: list[str] = []

    for r in retrievals:
        source = r.get("source", "")
        tool_counts[source] = tool_counts.get(source, 0) + 1
        if r.get("results_count", 0) == 0:
            zero_result_tools.append(source)
        if r.get("latency_ms", 0) > 5000:
            slow_tools.append(
                f"{source}({r.get('latency_ms', 0):.0f}ms)"
            )

    # 검색 결과 0건인 도구
    if zero_result_tools:
        findings.append(Finding(
            severity="WARNING",
            category="context",
            stage="context_explorer",
            message=(
                f"검색 결과 0건인 도구: {', '.join(zero_result_tools)} "
                f"— 검색 키워드 또는 메타 데이터 점검 필요"
            ),
        ))

    # 느린 도구
    if slow_tools:
        findings.append(Finding(
            severity="WARNING",
            category="context",
            stage="context_explorer",
            message=f"응답 지연 5초 초과: {', '.join(slow_tools)}",
        ))

    # 테이블 메타 검색 누락
    if "search_table_meta" not in tool_counts:
        findings.append(Finding(
            severity="CRITICAL",
            category="context",
            stage="context_explorer",
            message="search_table_meta 호출 없음 — 후보 테이블을 찾지 못했을 수 있음",
        ))

    return findings


def _check_llm_calls(data: dict) -> list[Finding]:
    """LLM 호출 관련 문제를 점검한다."""
    findings: list[Finding] = []
    llm_calls = data.get("llm_calls", [])

    if not llm_calls:
        findings.append(Finding(
            severity="WARNING",
            category="llm",
            stage="pipeline",
            message="LLM 호출 기록 없음",
        ))
        return findings

    # 빈 응답 확인
    empty_responses = [
        c for c in llm_calls
        if not c.get("response_text", "").strip()
    ]
    if empty_responses:
        nodes = [c.get("node", "?") for c in empty_responses]
        findings.append(Finding(
            severity="CRITICAL",
            category="llm",
            stage=", ".join(nodes),
            message=(
                f"LLM 빈 응답 {len(empty_responses)}건 "
                f"— 노드: {', '.join(nodes)}"
            ),
        ))

    # 높은 지연
    slow_calls = [
        c for c in llm_calls if c.get("latency_ms", 0) > 10000
    ]
    if slow_calls:
        findings.append(Finding(
            severity="WARNING",
            category="llm",
            stage="pipeline",
            message=(
                f"LLM 응답 10초 초과: {len(slow_calls)}건 "
                f"— Thinking 모드 비활성화 고려"
            ),
        ))

    # 과도한 호출 수
    if len(llm_calls) > 15:
        findings.append(Finding(
            severity="WARNING",
            category="llm",
            stage="pipeline",
            message=f"LLM 호출 {len(llm_calls)}회 — 비용/지연 최적화 필요",
        ))

    return findings


def _check_decisions(data: dict) -> list[Finding]:
    """결정 지점 관련 문제를 점검한다."""
    findings: list[Finding] = []
    decisions = data.get("decisions", [])

    # readiness verdict 확인
    readiness = [
        d for d in decisions
        if d.get("decision_type") == "readiness_verdict"
    ]
    if not readiness:
        findings.append(Finding(
            severity="INFO",
            category="pipeline",
            stage="reason_evaluate",
            message="readiness 판정 기록 없음 — evaluator 추적 미적용 가능성",
        ))
    else:
        # 낮은 readiness로 GENERATE 진입
        for r in readiness:
            if (
                r.get("chosen") == "generate_sql"
                and r.get("confidence", 1.0) < 0.6
            ):
                findings.append(Finding(
                    severity="WARNING",
                    category="accuracy",
                    stage="reason_evaluate",
                    message=(
                        f"낮은 확신도({r.get('confidence', 0):.2f})로 "
                        f"SQL 생성 진입 — 정확도 위험"
                    ),
                    detail=r.get("reason", ""),
                ))

    # 의도 분류 확인
    intent = [
        d for d in decisions
        if d.get("decision_type") == "intent_classification"
    ]
    if intent and intent[0].get("confidence", 1.0) < 0.5:
        findings.append(Finding(
            severity="WARNING",
            category="accuracy",
            stage="intent_classifier",
            message=(
                f"의도 분류 확신도 낮음: "
                f"{intent[0].get('confidence', 0):.2f} "
                f"— 잘못된 라우팅 위험"
            ),
        ))

    return findings


def _check_sql_quality(data: dict) -> list[Finding]:
    """SQL 생성/검증/실행 관련 문제를 점검한다."""
    findings: list[Finding] = []
    sql_rec = data.get("sql", {})

    if not sql_rec.get("generated_sql"):
        if data.get("final_status") not in (
            "casual_response", "clarification",
        ):
            findings.append(Finding(
                severity="CRITICAL",
                category="sql",
                stage="sql_generator",
                message="SQL 미생성 — 파이프라인이 SQL 생성에 도달하지 못함",
            ))
        return findings

    # 검증 실패
    if not sql_rec.get("validated"):
        errors = sql_rec.get("validation_errors", [])
        findings.append(Finding(
            severity="CRITICAL",
            category="sql",
            stage="sql_validator",
            message="SQL 검증 실패",
            detail="; ".join(errors) if errors else "검증 오류 상세 없음",
        ))

    # 실행 실패
    if sql_rec.get("validated") and not sql_rec.get("execution_success"):
        findings.append(Finding(
            severity="CRITICAL",
            category="sql",
            stage="sql_executor",
            message="검증 통과했으나 실행 실패 — 실행 환경 문제 또는 런타임 오류",
        ))

    # 재시도 횟수
    retry = sql_rec.get("retry_count", 0)
    if retry >= 2:
        findings.append(Finding(
            severity="WARNING",
            category="sql",
            stage="sql_generator",
            message=f"SQL 재생성 {retry}회 — 프롬프트 또는 컨텍스트 품질 점검 필요",
            detail=sql_rec.get("validation_feedback", ""),
        ))

    # 결과 0건
    if (
        sql_rec.get("execution_success")
        and sql_rec.get("row_count", 0) == 0
    ):
        findings.append(Finding(
            severity="WARNING",
            category="accuracy",
            stage="sql_executor",
            message="SQL 실행 성공이나 결과 0건 — WHERE 조건 과도 또는 테이블 선택 오류 가능",
        ))

    return findings


def _check_pipeline_flow(data: dict) -> list[Finding]:
    """파이프라인 흐름 관련 문제를 점검한다."""
    findings: list[Finding] = []
    node_path = data.get("node_path", [])

    # replan 횟수
    replan_count = node_path.count("reason_replan")
    if replan_count >= 2:
        findings.append(Finding(
            severity="WARNING",
            category="pipeline",
            stage="recovery_planner",
            message=f"재계획 {replan_count}회 — 초기 가설 품질 또는 메타 부족 가능성",
        ))

    # 최종 실패
    if data.get("final_status") == "failure":
        findings.append(Finding(
            severity="CRITICAL",
            category="pipeline",
            stage="result_finalizer",
            message=(
                f"파이프라인 최종 실패: "
                f"{data.get('error_message', '원인 불명')}"
            ),
        ))

    return findings


def _check_node_performance(data: dict) -> list[Finding]:
    """노드별 성능 문제를 점검한다."""
    findings: list[Finding] = []
    nodes = data.get("nodes", [])

    for n in nodes:
        duration = n.get("duration_ms", 0)
        name = n.get("node", "?")
        if duration > 30000:
            findings.append(Finding(
                severity="WARNING",
                category="pipeline",
                stage=name,
                message=f"노드 {name} 실행 {duration:.0f}ms (30초 초과)",
            ))
        if n.get("status") == "error":
            findings.append(Finding(
                severity="CRITICAL",
                category="pipeline",
                stage=name,
                message=f"노드 {name} 오류: {n.get('error_message', '')}",
            ))

    return findings


def _check_timeline(data: dict) -> list[Finding]:
    """타임라인 기반 실행 흐름 문제를 점검한다."""
    findings: list[Finding] = []
    timeline = data.get("timeline", [])

    if not timeline:
        findings.append(Finding(
            severity="INFO",
            category="pipeline",
            stage="tracker",
            message=(
                "timeline 데이터 없음"
                " — 트래커 버전 확인 필요"
            ),
        ))
        return findings

    # 1) tool_call 실패 건 집계
    failed_tools = [
        e for e in timeline
        if e.get("event_type") == "tool_call"
        and e.get("status") == "error"
    ]
    if failed_tools:
        names = [
            e.get("summary", "?")[:40]
            for e in failed_tools
        ]
        findings.append(Finding(
            severity="WARNING",
            category="context",
            stage="context_explorer",
            message=(
                f"도구 호출 실패 {len(failed_tools)}건"
                f": {', '.join(names[:5])}"
            ),
        ))

    # 2) 연속 LLM 호출 (도구 결과 없이 3회 이상)
    consec_llm = 0
    max_consec = 0
    for e in timeline:
        if e.get("event_type") == "llm_call":
            consec_llm += 1
            max_consec = max(max_consec, consec_llm)
        else:
            consec_llm = 0

    if max_consec >= 3:
        findings.append(Finding(
            severity="WARNING",
            category="llm",
            stage="pipeline",
            message=(
                f"도구 없이 연속 LLM 호출 "
                f"{max_consec}회"
                f" — 불필요한 LLM 호출 가능성"
            ),
        ))

    # 3) 노드 내 이벤트 없이 종료 (빈 노드)
    node_events: dict[str, int] = {}
    for e in timeline:
        etype = e.get("event_type", "")
        node = e.get("node", "")
        if etype not in ("node_start", "node_end"):
            node_events[node] = (
                node_events.get(node, 0) + 1
            )

    started = [
        e.get("node", "")
        for e in timeline
        if e.get("event_type") == "node_start"
    ]
    empty_nodes = [
        n for n in started
        if node_events.get(n, 0) == 0
    ]
    if empty_nodes:
        findings.append(Finding(
            severity="INFO",
            category="pipeline",
            stage="tracker",
            message=(
                f"내부 이벤트 없는 노드: "
                f"{', '.join(empty_nodes[:5])}"
                f" — 추적 누락 가능성"
            ),
        ))

    return findings


# ── 배치 분석 ──────────────────────────────────────────────────


def analyze_batch(trace_dir: str | Path) -> BatchReport:
    """디렉토리 내 모든 트레이스 파일을 분석하여 종합 보고서를 생성한다."""
    trace_dir = Path(trace_dir)
    report = BatchReport()
    all_findings: list[Finding] = []

    for path in sorted(trace_dir.glob("trace_*.json")):
        trace_report = analyze_trace(path)
        report.per_run.append(trace_report)
        report.total_runs += 1
        if trace_report.final_status == "success":
            report.success_count += 1
        else:
            report.failure_count += 1
        all_findings.extend(trace_report.findings)

    # 심각도별 집계
    for f in all_findings:
        report.findings_by_severity[f.severity] = (
            report.findings_by_severity.get(f.severity, 0) + 1
        )

    # 빈도순 상위 발견 사항 (메시지 기준 중복 제거)
    msg_count: dict[str, tuple[int, Finding]] = {}
    for f in all_findings:
        key = f.message[:80]
        if key in msg_count:
            msg_count[key] = (msg_count[key][0] + 1, msg_count[key][1])
        else:
            msg_count[key] = (1, f)

    sorted_findings = sorted(
        msg_count.values(), key=lambda x: x[0], reverse=True,
    )
    report.top_findings = [f for _, f in sorted_findings[:15]]

    return report
