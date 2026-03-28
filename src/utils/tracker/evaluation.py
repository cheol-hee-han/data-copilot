"""파이프라인 평가 트래커.

에이전트의 추론 과정, 선택, 결과를 구조화된 JSON으로 기록하여
문제점 분석과 개선 포인트 도출을 지원한다.

폐쇄망에서도 동작하며, 외부 의존성이 없다.

사용법:
    tracker = EvaluationTracker(run_id="eval-001")
    tracker.start_run(user_input="이번 달 신규 고객 수")
    tracker.track_node("preprocess", input_data={...}, output_data={...})
    tracker.track_llm_call("intent_classifier", prompt="...", response="...")
    tracker.track_decision("table_selection", chosen="TB_CUST_INFO", ...)
    tracker.end_run(final_state={...})
    tracker.save()  # JSON 파일로 저장
"""

from __future__ import annotations

import asyncio
import functools
import json
import time
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field

from src.config import settings
from src.utils.logger import get_logger
from src.utils.timezone import now_filesafe, now_stamp

logger = get_logger(__name__)


class TimelineEntry(BaseModel):
    """통합 타임라인 엔트리 — 모든 이벤트를 실행 순서대로 기록."""

    seq: int                          # 글로벌 순번 (1부터)
    event_type: str                    # node_start | node_end
                                       # llm_call | tool_call
                                       # decision
    node: str                          # 소속 노드 이름
    parent_seq: int | None = None      # 부모 node_start seq
    summary: str = ""                  # 한 줄 요약
    detail: dict[str, Any] = Field(
        default_factory=dict,
    )
    duration_ms: float = 0.0
    status: str = ""                   # success | error | skipped
    timestamp: str = Field(
        default_factory=now_stamp,
    )


class LLMCallRecord(BaseModel):
    """LLM 호출 기록."""

    node: str
    prompt_summary: str = ""  # 프롬프트 요약 (전체 저장은 토큰 낭비)
    prompt_variables: dict[str, str] = Field(default_factory=dict)
    prompt_tokens: int = 0
    response_text: str = ""
    response_tokens: int = 0
    model: str = ""
    latency_ms: float = 0.0
    timestamp: str = Field(
        default_factory=now_stamp,
    )


class NodeRecord(BaseModel):
    """노드 실행 기록."""

    node: str
    input_summary: dict[str, Any] = Field(default_factory=dict)
    output_summary: dict[str, Any] = Field(default_factory=dict)
    duration_ms: float = 0.0
    status: str = "success"  # success, error, skipped
    error_message: str = ""
    timestamp: str = Field(
        default_factory=now_stamp,
    )


class DecisionRecord(BaseModel):
    """의사결정 기록 — 에이전트가 선택한 판단과 그 근거."""

    node: str
    decision_type: str  # intent_classification, table_selection, routing, ...
    chosen: str  # 선택된 값
    alternatives: list[str] = Field(default_factory=list)  # 후보군
    confidence: float = 0.0
    reason: str = ""  # 선택 근거
    timestamp: str = Field(
        default_factory=now_stamp,
    )


class ContextRetrievalRecord(BaseModel):
    """컨텍스트 수집 기록."""

    source: str  # es_meta, es_report, history_sql, qdrant_manual, domain_dict
    query: str = ""
    results_count: int = 0
    results_summary: list[str] = Field(default_factory=list)
    latency_ms: float = 0.0
    timestamp: str = Field(
        default_factory=now_stamp,
    )


class SQLRecord(BaseModel):
    """SQL 생성/검증/실행 기록."""

    generated_sql: str = ""
    validated: bool = False
    validation_errors: list[str] = Field(default_factory=list)
    retry_count: int = 0
    validation_feedback: str = ""
    execution_success: bool = False
    row_count: int = 0
    execution_time_ms: float = 0.0


class EvaluationTrace(BaseModel):
    """단일 파이프라인 실행의 전체 트레이스."""

    run_id: str
    user_input: str = ""
    session_id: str = ""
    start_time: str = ""
    end_time: str = ""
    total_duration_ms: float = 0.0

    # 최종 결과
    final_intent: str = ""
    final_status: str = ""
    final_response_summary: str = ""
    error_message: str = ""

    # 상세 기록
    nodes: list[NodeRecord] = Field(default_factory=list)
    llm_calls: list[LLMCallRecord] = Field(
        default_factory=list,
    )
    decisions: list[DecisionRecord] = Field(
        default_factory=list,
    )
    context_retrievals: list[ContextRetrievalRecord] = Field(
        default_factory=list,
    )
    sql: SQLRecord = Field(default_factory=SQLRecord)

    # 통합 타임라인 (실행 순서 재현용)
    timeline: list[TimelineEntry] = Field(
        default_factory=list,
    )

    # 골든셋 평가 결과 (평가 시에만)
    golden_id: str = ""
    eval_passed: bool | None = None
    eval_errors: list[str] = Field(
        default_factory=list,
    )

    # 요약 통계
    total_llm_calls: int = 0
    total_llm_latency_ms: float = 0.0
    total_llm_tokens: int = 0
    node_path: list[str] = Field(
        default_factory=list,
    )


class EvaluationTracker:
    """파이프라인 실행 트래커.

    각 파이프라인 실행을 추적하고 구조화된 JSON으로 저장한다.
    """

    def __init__(self, run_id: str = "") -> None:
        self._run_id = run_id or now_filesafe()
        self._trace = EvaluationTrace(run_id=self._run_id)
        self._start_time: float = 0.0
        self._node_timers: dict[str, float] = {}
        self._enabled = settings.eval_tracker_enabled
        # timeline 지원
        self._seq: int = 0
        self._node_start_seq: dict[str, int] = {}
        # WebSocket 실시간 이벤트 콜백 (runner.py에서 주입)
        self.on_node_event: Any = None
        self._explore_count: int = 0

    @property
    def trace(self) -> EvaluationTrace:
        """현재 트레이스를 반환한다."""
        return self._trace

    @property
    def enabled(self) -> bool:
        """트래커 활성화 여부."""
        return self._enabled

    def _next_seq(self) -> int:
        """글로벌 순번을 발급한다."""
        self._seq += 1
        return self._seq

    def _append_timeline(
        self,
        event_type: str,
        node: str,
        *,
        summary: str = "",
        detail: dict[str, Any] | None = None,
        duration_ms: float = 0.0,
        status: str = "",
        parent_seq: int | None = None,
    ) -> int:
        """타임라인 엔트리를 추가하고 seq를 반환한다."""
        seq = self._next_seq()
        if parent_seq is None:
            parent_seq = self._node_start_seq.get(node)
        self._trace.timeline.append(TimelineEntry(
            seq=seq,
            event_type=event_type,
            node=node,
            parent_seq=parent_seq,
            summary=summary,
            detail=detail or {},
            duration_ms=round(duration_ms, 2),
            status=status,
        ))
        return seq

    async def _emit_node_start(self, node_name: str) -> None:
        """노드 시작 시 on_node_event 콜백으로 progress를 전송한다."""
        cb = self.on_node_event
        if cb is None:
            return
        from src.agents.graph.runner import NODE_PROGRESS_MAP
        if node_name not in NODE_PROGRESS_MAP:
            return

        if node_name == "reason_explore":
            self._explore_count += 1

        info = NODE_PROGRESS_MAP[node_name]
        label = info["label"]
        if (
            node_name == "reason_explore"
            and self._explore_count > 1
        ):
            label = (
                "📂 추가 데이터를 탐색하고 있습니다"
                f" ({self._explore_count}차)"
            )

        try:
            await cb({
                "type": "progress",
                "action": "add",
                "label": label,
                "thinkingLabel": info["thinking"],
            })
        except Exception:
            pass

    async def _emit_node_done(self, node_name: str) -> None:
        """노드 완료 시 on_node_event 콜백으로 done을 전송한다."""
        cb = self.on_node_event
        if cb is None:
            return
        from src.agents.graph.runner import NODE_PROGRESS_MAP
        if node_name not in NODE_PROGRESS_MAP:
            return
        try:
            await cb({"type": "progress", "action": "done"})
        except Exception:
            pass

    def inject(self) -> None:
        """contextvars에 자신을 설정한다.

        LangGraph 노드는 PipelineState만 인자로 받으므로,
        contextvars 기반 전파로 호출 스택 어디서든
        tracker에 접근할 수 있다.
        """
        from src.utils.tracker.context import (
            set_current_tracker,
        )
        set_current_tracker(self)

    def track(self, node_name: str) -> Callable:
        """노드 함수를 시간/에러 계측으로 감싸는 데코레이터.

        pipeline.py 에서 ``tracker.track("generate_sql")(fn)``
        형태로 사용한다.
        """
        def decorator(fn: Callable) -> Callable:
            if asyncio.iscoroutinefunction(fn):
                return self._wrap_async(node_name, fn)
            return self._wrap_sync(node_name, fn)
        return decorator

    def _wrap_async(
        self, node_name: str, fn: Callable,
    ) -> Callable:
        """async 노드 래퍼 — progress 이벤트 전송 포함."""
        from src.utils.tracker.context import set_current_node

        @functools.wraps(fn)
        async def wrapper(state: Any) -> Any:
            set_current_node(node_name)
            await self._emit_node_start(node_name)
            if not self._enabled:
                result = await fn(state)
                await self._emit_node_done(node_name)
                return result
            self.start_node(node_name)
            try:
                result = await fn(state)
                self.end_node(node_name)
                await self._emit_node_done(node_name)
                return result
            except Exception as e:
                self.end_node(
                    node_name,
                    status="error",
                    error_message=str(e),
                )
                await self._emit_node_done(node_name)
                raise
        return wrapper

    def _wrap_sync(
        self, node_name: str, fn: Callable,
    ) -> Callable:
        """sync 노드 래퍼."""
        from src.utils.tracker.context import set_current_node

        @functools.wraps(fn)
        def wrapper(state: Any) -> Any:
            set_current_node(node_name)
            if not self._enabled:
                return fn(state)
            self.start_node(node_name)
            try:
                result = fn(state)
                self.end_node(node_name)
                return result
            except Exception as e:
                self.end_node(
                    node_name,
                    status="error",
                    error_message=str(e),
                )
                raise
        return wrapper

    def start_run(
        self,
        user_input: str,
        session_id: str = "",
        golden_id: str = "",
    ) -> None:
        """파이프라인 실행 추적을 시작한다."""
        if not self._enabled:
            return
        self._start_time = time.perf_counter()
        self._trace.user_input = user_input
        self._trace.session_id = session_id
        self._trace.golden_id = golden_id
        self._trace.start_time = now_stamp()

    def start_node(self, node: str) -> None:
        """노드 실행 시작을 기록한다."""
        if not self._enabled:
            return
        self._node_timers[node] = time.perf_counter()
        self._trace.node_path.append(node)
        seq = self._append_timeline(
            "node_start", node,
            summary=f"{node} 시작",
            parent_seq=0,  # 최상위 이벤트
        )
        self._node_start_seq[node] = seq

    def end_node(
        self,
        node: str,
        input_summary: dict[str, Any] | None = None,
        output_summary: dict[str, Any] | None = None,
        status: str = "success",
        error_message: str = "",
    ) -> None:
        """노드 실행 종료를 기록한다."""
        if not self._enabled:
            return
        duration = 0.0
        if node in self._node_timers:
            duration = (
                time.perf_counter()
                - self._node_timers.pop(node)
            ) * 1000

        self._trace.nodes.append(NodeRecord(
            node=node,
            input_summary=input_summary or {},
            output_summary=output_summary or {},
            duration_ms=round(duration, 2),
            status=status,
            error_message=error_message,
        ))

        summary = f"{node} 완료"
        if error_message:
            summary = f"{node} 오류: {error_message[:60]}"
        self._append_timeline(
            "node_end", node,
            summary=summary,
            detail={
                "input": input_summary or {},
                "output": output_summary or {},
            },
            duration_ms=duration,
            status=status,
        )
        self._node_start_seq.pop(node, None)

    def track_llm_call(
        self,
        node: str,
        prompt_summary: str = "",
        prompt_variables: dict[str, str] | None = None,
        response_text: str = "",
        model: str = "",
        prompt_tokens: int = 0,
        response_tokens: int = 0,
        latency_ms: float = 0.0,
    ) -> None:
        """LLM 호출을 기록한다."""
        if not self._enabled:
            return
        self._trace.llm_calls.append(LLMCallRecord(
            node=node,
            prompt_summary=prompt_summary[:500],
            prompt_variables=prompt_variables or {},
            response_text=response_text,
            model=model,
            prompt_tokens=prompt_tokens,
            response_tokens=response_tokens,
            latency_ms=round(latency_ms, 2),
        ))
        self._trace.total_llm_calls += 1
        self._trace.total_llm_latency_ms += latency_ms
        total = prompt_tokens + response_tokens
        self._trace.total_llm_tokens += total

        tokens_str = f"{total}tok"
        self._append_timeline(
            "llm_call", node,
            summary=f"LLM({model}) {tokens_str}",
            detail={
                "model": model,
                "prompt_tokens": prompt_tokens,
                "response_tokens": response_tokens,
                "response_preview": response_text[:120],
            },
            duration_ms=latency_ms,
        )

    def track_decision(
        self,
        node: str,
        decision_type: str,
        chosen: str,
        alternatives: list[str] | None = None,
        confidence: float = 0.0,
        reason: str = "",
    ) -> None:
        """의사결정 포인트를 기록한다."""
        if not self._enabled:
            return
        self._trace.decisions.append(DecisionRecord(
            node=node,
            decision_type=decision_type,
            chosen=chosen,
            alternatives=alternatives or [],
            confidence=confidence,
            reason=reason,
        ))
        self._append_timeline(
            "decision", node,
            summary=(
                f"{decision_type}: {chosen}"
                f" ({confidence:.0%})"
            ),
            detail={
                "type": decision_type,
                "chosen": chosen,
                "alternatives": alternatives or [],
                "confidence": confidence,
                "reason": reason,
            },
        )

    def track_context_retrieval(
        self,
        source: str,
        query: str = "",
        results_count: int = 0,
        results_summary: list[str] | None = None,
        latency_ms: float = 0.0,
        status: str = "success",
    ) -> None:
        """컨텍스트 수집 결과를 기록한다."""
        if not self._enabled:
            return
        self._trace.context_retrievals.append(
            ContextRetrievalRecord(
                source=source,
                query=query[:200],
                results_count=results_count,
                results_summary=results_summary or [],
                latency_ms=round(latency_ms, 2),
            )
        )
        self._append_timeline(
            "tool_call",
            self._current_timeline_node(),
            summary=(
                f"{source}('{query[:40]}')"
                f"→{results_count}건"
            ),
            detail={
                "tool": source,
                "query": query[:200],
                "results_count": results_count,
            },
            duration_ms=latency_ms,
            status=status,
        )

    def _current_timeline_node(self) -> str:
        """현재 활성 노드 이름을 반환한다."""
        from src.utils.tracker.context import (
            get_current_node,
        )
        node = get_current_node()
        if node:
            return node
        # fallback: 가장 최근 시작된 노드
        if self._node_start_seq:
            return max(
                self._node_start_seq,
                key=self._node_start_seq.get,  # type: ignore[arg-type]
            )
        return "unknown"

    def track_sql(
        self,
        generated_sql: str = "",
        validated: bool = False,
        validation_errors: list[str] | None = None,
        retry_count: int = 0,
        validation_feedback: str = "",
        execution_success: bool = False,
        row_count: int = 0,
        execution_time_ms: float = 0.0,
    ) -> None:
        """SQL 생성/검증/실행 결과를 기록한다."""
        if not self._enabled:
            return
        self._trace.sql = SQLRecord(
            generated_sql=generated_sql,
            validated=validated,
            validation_errors=validation_errors or [],
            retry_count=retry_count,
            validation_feedback=validation_feedback,
            execution_success=execution_success,
            row_count=row_count,
            execution_time_ms=round(execution_time_ms, 2),
        )

    def track_eval_result(
        self,
        passed: bool,
        errors: list[str] | None = None,
    ) -> None:
        """골든셋 평가 결과를 기록한다."""
        if not self._enabled:
            return
        self._trace.eval_passed = passed
        self._trace.eval_errors = errors or []

    def end_run(
        self,
        final_intent: str = "",
        final_status: str = "",
        final_response_summary: str = "",
        error_message: str = "",
    ) -> None:
        """파이프라인 실행 추적을 종료한다."""
        if not self._enabled:
            return
        self._trace.end_time = now_stamp()
        if self._start_time:
            self._trace.total_duration_ms = round(
                (time.perf_counter() - self._start_time) * 1000, 2
            )
        self._trace.final_intent = final_intent
        self._trace.final_status = final_status
        self._trace.final_response_summary = final_response_summary[:500]
        self._trace.error_message = error_message

    def save(
        self,
        output_dir: str | None = None,
        *,
        with_report: bool = True,
    ) -> Path | None:
        """트레이스를 JSON + Markdown 보고서로 저장한다.

        Args:
            output_dir: 저장 디렉토리
            with_report: Mermaid 보고서 동시 생성 여부

        Returns:
            저장된 JSON 파일 경로 또는 None (비활성 시)
        """
        if not self._enabled:
            return None

        base = Path(
            output_dir or settings.eval_tracker_output_dir
        )
        base.mkdir(parents=True, exist_ok=True)

        ts = now_filesafe()
        filename = f"trace_{self._run_id}_{ts}.json"
        filepath = base / filename

        data = self._trace.model_dump(mode="json")
        filepath.write_text(
            json.dumps(
                data, ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        logger.info(
            "평가 트레이스 저장", path=str(filepath),
        )

        # Markdown 보고서 자동 생성
        if with_report and self._trace.timeline:
            from src.utils.tracker.visualizer import (
                save_report,
            )
            report_name = (
                f"report_{self._run_id}_{ts}.md"
            )
            save_report(data, base / report_name)

        return filepath

    def to_dict(self) -> dict[str, Any]:
        """트레이스를 딕셔너리로 반환한다."""
        return self._trace.model_dump(mode="json")


class BatchEvaluationTracker:
    """배치 골든셋 평가 트래커.

    여러 테스트 케이스를 실행하고 종합 보고서를 생성한다.
    """

    def __init__(self, batch_id: str = "") -> None:
        self._batch_id = (
            batch_id or f"batch_{now_filesafe()}"
        )
        self._traces: list[EvaluationTrace] = []
        self._start_time: float = 0.0
        self._enabled = settings.eval_tracker_enabled

    def start_batch(self) -> None:
        """배치 평가를 시작한다."""
        self._start_time = time.perf_counter()

    def add_trace(self, tracker: EvaluationTracker) -> None:
        """개별 실행 트레이스를 추가한다."""
        if not self._enabled:
            return
        self._traces.append(tracker.trace)

    def generate_summary(self) -> dict[str, Any]:
        """배치 평가 요약 보고서를 생성한다."""
        total = len(self._traces)
        if total == 0:
            return {"batch_id": self._batch_id, "total": 0}

        passed = sum(1 for t in self._traces if t.eval_passed is True)
        failed = sum(1 for t in self._traces if t.eval_passed is False)
        errors = sum(1 for t in self._traces if t.final_status == "error")

        # 노드별 평균 소요 시간
        node_durations: dict[str, list[float]] = {}
        for trace in self._traces:
            for node_rec in trace.nodes:
                node_durations.setdefault(node_rec.node, []).append(node_rec.duration_ms)

        avg_node_durations = {
            node: round(sum(durations) / len(durations), 2)
            for node, durations in node_durations.items()
        }

        # LLM 통계
        total_llm_calls = sum(t.total_llm_calls for t in self._traces)
        total_llm_tokens = sum(t.total_llm_tokens for t in self._traces)
        total_llm_latency = sum(t.total_llm_latency_ms for t in self._traces)

        # 의도 분류 정확도
        intent_decisions = [
            d for t in self._traces
            for d in t.decisions
            if d.decision_type == "intent_classification"
        ]

        # SQL 재시도 통계
        retry_counts = [t.sql.retry_count for t in self._traces if t.sql.generated_sql]
        avg_retry = sum(retry_counts) / len(retry_counts) if retry_counts else 0

        # 실패 원인 분류
        failure_reasons: dict[str, int] = {}
        for trace in self._traces:
            for err in trace.eval_errors:
                # 에러 메시지의 첫 단어(유형)로 그룹핑
                reason_key = err.split(":")[0].strip() if ":" in err else err[:30]
                failure_reasons[reason_key] = failure_reasons.get(reason_key, 0) + 1

        # 실행 경로 분포
        path_distribution: dict[str, int] = {}
        for trace in self._traces:
            path_key = " → ".join(trace.node_path)
            path_distribution[path_key] = path_distribution.get(path_key, 0) + 1

        total_duration = 0.0
        if self._start_time:
            total_duration = round(
                (time.perf_counter() - self._start_time) * 1000, 2
            )

        return {
            "batch_id": self._batch_id,
            "timestamp": now_stamp(),
            "total_duration_ms": total_duration,
            "summary": {
                "total": total,
                "passed": passed,
                "failed": failed,
                "errors": errors,
                "pass_rate": round(passed / total * 100, 1) if total > 0 else 0,
            },
            "llm_stats": {
                "total_calls": total_llm_calls,
                "total_tokens": total_llm_tokens,
                "total_latency_ms": round(total_llm_latency, 2),
                "avg_latency_per_call_ms": round(
                    total_llm_latency / total_llm_calls, 2
                ) if total_llm_calls > 0 else 0,
            },
            "sql_stats": {
                "avg_retry_count": round(avg_retry, 2),
                "max_retry_count": max(retry_counts) if retry_counts else 0,
            },
            "avg_node_durations_ms": avg_node_durations,
            "failure_reasons": failure_reasons,
            "execution_path_distribution": path_distribution,
            "intent_decision_count": len(intent_decisions),
        }

    def save(self, output_dir: str | None = None) -> Path | None:
        """배치 결과를 저장한다 (요약 + 개별 트레이스)."""
        if not self._enabled:
            return None

        base_dir = Path(output_dir or settings.eval_tracker_output_dir)
        batch_dir = base_dir / self._batch_id
        batch_dir.mkdir(parents=True, exist_ok=True)

        # 요약 보고서 저장
        summary = self.generate_summary()
        summary_path = batch_dir / "summary.json"
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # 개별 트레이스 저장
        traces_dir = batch_dir / "traces"
        traces_dir.mkdir(exist_ok=True)
        for trace in self._traces:
            trace_path = traces_dir / f"{trace.run_id}.json"
            trace_path.write_text(
                json.dumps(trace.model_dump(mode="json"), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        # 실패 케이스만 모은 파일
        failed_traces = [t for t in self._traces if t.eval_passed is False]
        if failed_traces:
            failures_path = batch_dir / "failures.json"
            failures_data = [
                {
                    "run_id": t.run_id,
                    "golden_id": t.golden_id,
                    "user_input": t.user_input,
                    "final_intent": t.final_intent,
                    "final_status": t.final_status,
                    "error_message": t.error_message,
                    "eval_errors": t.eval_errors,
                    "node_path": t.node_path,
                    "sql": t.sql.model_dump(mode="json"),
                    "decisions": [d.model_dump(mode="json") for d in t.decisions],
                }
                for t in failed_traces
            ]
            failures_path.write_text(
                json.dumps(failures_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        logger.info(
            "배치 평가 결과 저장",
            path=str(batch_dir),
            total=len(self._traces),
            passed=summary["summary"]["passed"],
            failed=summary["summary"]["failed"],
        )
        return batch_dir
