"""LangGraph 표준 콜백 기반 파이프라인 텔레메트리 핸들러.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

요청마다 새 인스턴스를 생성하여
``config={"callbacks": [handler]}`` 로 주입한다.

기존 ``EvaluationTracker`` 의 모든 기능을 LangGraph 표준 API
(``AsyncCallbackHandler`` + ``adispatch_custom_event``)로 대체하며,
State 변화 추적을 신규로 제공한다.

수집 항목:
    - 노드 시작/종료 + 실행 시간
    - State 변화 (입력→출력 diff)
    - LLM 호출 (in/out, 토큰, 지연시간)
    - 의사결정 (intent, table, confidence)
    - 컨텍스트 검색 (ES, Qdrant, DB)
    - SQL 라이프사이클 (생성→검증→실행)
    - 통합 타임라인 (순번 + 부모-자식)
    - WebSocket 진행률 전파

사용법::

    handler = DataCopilotCallbackHandler(
        run_id=session_id,
        on_event=ws_callback,
    )
    handler.start_run(user_input=query, session_id=sid)
    result = await app.ainvoke(state, config={"callbacks": [handler]})
    handler.end_run(...)
    handler.save()
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler

from src.config import settings
from src.utils.timezone import now_filesafe, now_stamp
from src.utils.truncate import truncate_trace

from src.utils.tracker.evaluation import (
    ContextRetrievalRecord,
    DecisionRecord,
    EvaluationTrace,
    LLMCallRecord,
    NodeRecord,
    ReasoningStep,
    RoutingDecision,
    SQLRecord,
    TimelineEntry,
)

from src.utils.logger import get_logger

logger = get_logger(__name__)


# ── 프로그레스 메시지 매핑 ──────────────────────────

NODE_PROGRESS_MAP: dict[str, dict[str, str]] = {
    # ── Interpret ──
    "preprocess": {
        "phase": "interpret",
        "label": "질의 전처리, 보안 검사",
        "thinking": "입력 전처리 중",
    },
    "intent_classifier": {
        "phase": "interpret",
        "label": "대화 맥락 분석 및 질의 의도 분류",
        "thinking": "질문 의도 파악 중",
    },
    "normalize_query": {
        "phase": "interpret",
        "label": "사용자 질의 8-Slot 정규화",
        "thinking": "질문 정규화 중",
    },
    "clarify": {
        "phase": "interpret",
        "label": "명확화 질문 생성",
        "thinking": "명확화 질문 생성 중",
    },
    # ── Reason ──
    "reasoning_preparer": {
        "phase": "reason",
        "label": "데이터 탐색 준비",
        "thinking": "탐색 계획 수립 중",
    },
    "context_explorer": {
        "phase": "reason",
        "label": "관련 테이블·데이터 탐색",
        "thinking": "데이터 소스 탐색 중",
    },
    "context_retriever": {
        "phase": "reason",
        "label": "관련 테이블·데이터 수집",
        "thinking": "데이터 소스 수집 중",
    },
    "context_interpreter": {
        "phase": "reason",
        "label": "수집 데이터 해석",
        "thinking": "데이터 해석 중",
    },
    "readiness_gate": {
        "phase": "reason",
        "label": "수집 정보 충분성 판단",
        "thinking": "준비도 평가 중",
    },
    "sql_generator": {
        "phase": "reason",
        "label": "조회 조건 작성",
        "thinking": "SQL 생성 중",
    },
    "sql_validator": {
        "phase": "reason",
        "label": "조회 조건 검증",
        "thinking": "SQL 검증 중",
    },
    "recovery_planner": {
        "phase": "reason",
        "label": "대안 탐색",
        "thinking": "대안 탐색 중",
    },
    "recovery_agent": {
        "phase": "reason",
        "label": "복구 탐색",
        "thinking": "복구 탐색 중",
    },
    "result_finalizer": {
        "phase": "reason",
        "label": "최종 결과 정리",
        "thinking": "결과 확정 중",
    },
    # ── Present ──
    "simple_responder": {
        "phase": "present",
        "label": "간단 응답 생성",
        "thinking": "응답 작성 중",
    },
    "execute_sql": {
        "phase": "present",
        "label": "데이터 조회",
        "thinking": "데이터베이스 조회 중",
    },
    "analyze_data": {
        "phase": "present",
        "label": "결과 분석·시각화",
        "thinking": "데이터 분석 중",
    },
    "format_response": {
        "phase": "present",
        "label": "보고서 작성",
        "thinking": "결과 정리 중",
    },
}

PHASE_LABELS: dict[str, str] = {
    "interpret": "사용자의 질의를 분석하는 중",
    "reason": "데이터를 탐색하고 SQL을 생성하는 중",
    "present": "결과를 정리하는 중",
}

# State diff에서 추적할 키 목록
_TRACKED_STATE_KEYS = frozenset({
    # interpret
    "intent",
    "intent_confidence",
    "query_category",
    "preprocessed_input",
    "is_continuation",
    # reason (중첩 객체는 요약만)
    "reason",
    # present
    "status",
    "error_message",
    "formatted_response",
})


class DataCopilotCallbackHandler(AsyncCallbackHandler):
    """LangGraph 표준 콜백 기반 파이프라인 텔레메트리 핸들러."""

    # ── 생성자 ──

    def __init__(
        self,
        run_id: str = "",
        *,
        on_event: Any = None,
        enabled: bool | None = None,
    ) -> None:
        super().__init__()
        self._run_id = run_id or now_filesafe()
        self._trace = EvaluationTrace(run_id=self._run_id)
        self._enabled = (
            enabled if enabled is not None
            else (
                settings.eval_trace_json_enabled
                or settings.eval_trace_report_enabled
                or settings.eval_trace_reasoning_enabled
            )
        )

        # 타임라인 순번
        self._seq: int = 0
        # reasoning flow 순번
        self._reasoning_seq: int = 0
        # run_id(UUID) → node_name 매핑 (on_chain_end에서 노드 식별)
        self._run_to_node: dict[str, str] = {}
        # node_name → (start_time, start_seq)
        self._node_timers: dict[str, tuple[float, int]] = {}
        # node_name → 입력 state 스냅샷 (state diff 계산용)
        self._node_inputs: dict[str, dict[str, Any]] = {}
        # context_explorer 반복 카운터
        self._explore_count: int = 0
        # 같은 노드 방문 횟수 (①②③ 표기용)
        self._node_visit_count: dict[str, int] = {}
        # 현재 실행 중인 노드 (중복 on_chain_start 방지)
        self._active_nodes: set[str] = set()
        # 중복으로 감지되어 무시할 run_id
        self._nested_runs: set[str] = set()

        # WebSocket 콜백
        self._on_event = on_event

        # 실행 시간
        self._start_time: float = 0.0

    # ── 프로퍼티 ──

    @property
    def trace(self) -> EvaluationTrace:
        """현재 트레이스 객체."""
        return self._trace

    @property
    def enabled(self) -> bool:
        """트래커 활성 여부."""
        return self._enabled

    @property
    def run_id(self) -> str:
        """외부 참조용 실행 ID (trace_id로 사용)."""
        return self._run_id

    # ── 타임라인 관리 (내부) ──

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
        """타임라인에 이벤트를 추가하고 seq를 반환한다."""
        seq = self._next_seq()
        entry = TimelineEntry(
            seq=seq,
            event_type=event_type,
            node=node,
            parent_seq=parent_seq,
            summary=summary,
            detail=detail or {},
            duration_ms=duration_ms,
            status=status,
            timestamp=now_stamp(),
        )
        self._trace.timeline.append(entry)
        return seq

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 표준 훅: 노드 경계
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def on_chain_start(
        self,
        serialized: dict[str, Any],
        inputs: dict[str, Any],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """LangGraph 노드 시작 시 호출된다."""
        node = (metadata or {}).get("langgraph_node")
        if not node:
            return  # 내부 체인 이벤트 무시

        rid = str(run_id)

        # LangGraph는 RunnableSequence wrapper + 실제 노드에 대해
        # on_chain_start를 2번 호출한다. 이미 활성 상태이면 중복 무시.
        if node in self._active_nodes:
            self._nested_runs.add(rid)
            logger.debug("중복 on_chain_start 무시", node=node)
            return
        self._active_nodes.add(node)
        self._run_to_node[rid] = node

        # contextvars에 현재 노드 설정 (client.py 등에서 참조)
        from src.utils.tracker.context import set_current_node

        set_current_node(node)

        # WebSocket 진행률 전송
        await self._emit_progress(node, "add")

        if not self._enabled:
            return

        # 방문 횟수 갱신
        self._node_visit_count[node] = (
            self._node_visit_count.get(node, 0) + 1
        )

        # 타이머 시작
        start_seq = self._append_timeline(
            "node_start",
            node,
            summary=f"{node} 시작",
        )
        self._node_timers[node] = (
            time.perf_counter(),
            start_seq,
        )

        # State 스냅샷 (diff 계산용)
        self._node_inputs[node] = _snapshot_state(inputs)

        # 노드 경로 기록
        self._trace.node_path.append(node)

    async def on_chain_end(
        self,
        outputs: dict[str, Any],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """LangGraph 노드 종료 시 호출된다."""
        rid = str(run_id)

        # 중복 호출(nested run) 무시
        if rid in self._nested_runs:
            self._nested_runs.discard(rid)
            return

        node = self._run_to_node.pop(rid, None)
        if not node:
            return

        # 활성 노드에서 제거
        self._active_nodes.discard(node)

        # WebSocket 진행률 전송
        await self._emit_progress(node, "done")

        if not self._enabled:
            return

        # duration 계산
        timer = self._node_timers.pop(node, None)
        duration_ms = 0.0
        parent_seq: int | None = None
        if timer:
            duration_ms = (
                (time.perf_counter() - timer[0]) * 1000
            )
            parent_seq = timer[1]

        # State diff 계산
        before = self._node_inputs.pop(node, {})
        state_changes = _compute_state_diff(before, outputs)

        # 타임라인 기록
        self._append_timeline(
            "node_end",
            node,
            summary=f"{node} 완료",
            detail={"state_changes": state_changes},
            duration_ms=duration_ms,
            status="success",
            parent_seq=parent_seq,
        )

        # NodeRecord 기록
        self._trace.nodes.append(
            NodeRecord(
                node=node,
                input_summary=_summarize_state(before),
                output_summary=_summarize_state(outputs),
                duration_ms=round(duration_ms, 1),
                status="success",
            )
        )

    async def on_chain_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """노드 에러 시 호출된다."""
        rid = str(run_id)

        if rid in self._nested_runs:
            self._nested_runs.discard(rid)
            return

        node = self._run_to_node.pop(rid, None)
        if not node:
            return

        self._active_nodes.discard(node)
        await self._emit_progress(node, "done")

        if not self._enabled:
            return

        timer = self._node_timers.pop(node, None)
        duration_ms = 0.0
        parent_seq: int | None = None
        if timer:
            duration_ms = (
                (time.perf_counter() - timer[0]) * 1000
            )
            parent_seq = timer[1]

        self._node_inputs.pop(node, None)

        self._append_timeline(
            "node_end",
            node,
            summary=f"{node} 실패: {error}",
            duration_ms=duration_ms,
            status="error",
            parent_seq=parent_seq,
        )

        self._trace.nodes.append(
            NodeRecord(
                node=node,
                duration_ms=round(duration_ms, 1),
                status="error",
                error_message=truncate_trace(str(error)),
            )
        )

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 표준 훅: 커스텀 이벤트 수신
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def on_custom_event(
        self,
        name: str,
        data: Any,
        *,
        run_id: UUID,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """``adispatch_custom_event`` 로 디스패치된 이벤트를 수신한다."""
        if not self._enabled:
            return

        # 현재 활성 노드 식별 (run_id → node)
        node = self._run_to_node.get(str(run_id), "")

        domain = name.split(".")[0]
        match domain:
            case "decision":
                self._record_decision(node, data)
            case "context":
                self._record_context_retrieval(node, data)
            case "llm":
                if name == "llm.call":
                    self._record_llm_call(data)
                elif name == "llm.prompt_variables":
                    self._merge_prompt_variables(data)
            case "sql":
                self._record_sql(data)
            case "reasoning":
                self._record_reasoning_step(node, data)

    # ── 커스텀 이벤트 레코딩 ──

    def _record_decision(
        self,
        node: str,
        data: dict[str, Any],
    ) -> None:
        """의사결정을 기록한다."""
        effective_node = data.get("node", node)
        record = DecisionRecord(
            node=effective_node,
            decision_type=data.get("decision_type", ""),
            chosen=data.get("chosen", ""),
            alternatives=data.get("alternatives", []),
            confidence=data.get("confidence", 0.0),
            reason=data.get("reason", ""),
        )
        self._trace.decisions.append(record)

        parent_seq = self._get_active_parent_seq(
            effective_node,
        )
        self._append_timeline(
            "decision",
            effective_node,
            summary=(
                f"{data.get('decision_type', '')}: "
                f"{data.get('chosen', '')}"
            ),
            detail={
                "confidence": data.get("confidence", 0.0),
                "alternatives": data.get("alternatives", []),
                "reason": data.get("reason", ""),
                **(
                    {"detail": data["detail"]}
                    if "detail" in data
                    else {}
                ),
            },
            parent_seq=parent_seq,
        )

    def _record_context_retrieval(
        self,
        node: str,
        data: dict[str, Any],
    ) -> None:
        """컨텍스트 수집을 기록한다."""
        record = ContextRetrievalRecord(
            source=data.get("source", ""),
            query=data.get("query", ""),
            results_count=data.get("results_count", 0),
            results_summary=data.get(
                "results_summary", [],
            ),
            latency_ms=data.get("latency_ms", 0.0),
        )
        self._trace.context_retrievals.append(record)

        status = data.get("status", "success")
        parent_seq = self._get_active_parent_seq(node)
        self._append_timeline(
            "tool_call",
            node,
            summary=(
                f"{data.get('source', '')}: "
                f"{data.get('results_count', 0)}건"
            ),
            detail={
                "source": data.get("source", ""),
                "query": truncate_trace(data.get("query", "")),
                "results_count": data.get(
                    "results_count", 0,
                ),
            },
            duration_ms=data.get("latency_ms", 0.0),
            status=status,
            parent_seq=parent_seq,
        )

    def _record_llm_call(
        self,
        data: dict[str, Any],
    ) -> None:
        """LLM 호출을 기록한다."""
        record = LLMCallRecord(
            node=data.get("node", ""),
            prompt_summary=data.get(
                "prompt_summary", "",
            ),
            prompt_variables=data.get(
                "prompt_variables",
            )
            or {},
            prompt_tokens=data.get("prompt_tokens", 0),
            response_text=data.get("response_text", ""),
            response_tokens=data.get(
                "response_tokens", 0,
            ),
            model=data.get("model", ""),
            latency_ms=data.get("latency_ms", 0.0),
        )
        self._trace.llm_calls.append(record)

        # 통계 갱신
        self._trace.total_llm_calls += 1
        self._trace.total_llm_latency_ms += (
            record.latency_ms
        )
        self._trace.total_llm_tokens += (
            record.prompt_tokens + record.response_tokens
        )

        node = data.get("node", "")
        parent_seq = self._get_active_parent_seq(node)
        self._append_timeline(
            "llm_call",
            node,
            summary=(
                f"LLM({data.get('model', '?')}) "
                f"{record.prompt_tokens + record.response_tokens}tok"
            ),
            detail={
                "model": data.get("model", ""),
                "prompt_tokens": record.prompt_tokens,
                "response_tokens": record.response_tokens,
            },
            duration_ms=record.latency_ms,
            parent_seq=parent_seq,
        )

    def _merge_prompt_variables(
        self,
        data: dict[str, Any],
    ) -> None:
        """직전 LLM 호출 기록에 프롬프트 치환 변수를 보강한다."""
        variables = data.get("variables", {})
        if self._trace.llm_calls and variables:
            self._trace.llm_calls[-1].prompt_variables = (
                variables
            )

    def _record_sql(
        self,
        data: dict[str, Any],
    ) -> None:
        """SQL 라이프사이클을 기록한다."""
        self._trace.sql = SQLRecord(
            generated_sql=data.get("generated_sql", ""),
            validated=data.get("validated", False),
            validation_errors=data.get(
                "validation_errors", [],
            ),
            retry_count=data.get("retry_count", 0),
            validation_feedback=data.get(
                "validation_feedback", "",
            ),
            execution_success=data.get(
                "execution_success", False,
            ),
            row_count=data.get("row_count", 0),
            execution_time_ms=data.get(
                "execution_time_ms", 0.0,
            ),
        )

    # ── reasoning flow 레코딩 ──

    def _next_reasoning_seq(self) -> int:
        """reasoning flow 순번을 발급한다."""
        self._reasoning_seq += 1
        return self._reasoning_seq

    def _record_reasoning_step(
        self,
        node: str,
        data: dict[str, Any],
    ) -> None:
        """reasoning flow 단계를 기록한다."""
        step = ReasoningStep(
            seq=self._next_reasoning_seq(),
            node=data.get("node", node),
            phase=data.get("phase", ""),
            round=data.get("round", 0),
            hypothesis_id=data.get("hypothesis_id", ""),
            step_type=data.get("step_type", ""),
            inputs=data.get("inputs", {}),
            output=data.get("output", {}),
            routing=RoutingDecision(
                **data["routing"],
            ) if isinstance(data.get("routing"), dict) else RoutingDecision(),
            duration_ms=data.get("duration_ms", 0.0),
            model=data.get("model", ""),
            tokens=data.get("tokens", 0),
        )
        self._trace.reasoning_flow.append(step)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 그래프 외부 직접 호출 메서드
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def record_sql(self, data: dict[str, Any]) -> None:
        """그래프 실행 외부에서 SQL 메트릭을 직접 기록한다.

        ``runner.py`` 의 ``_record_sql_metrics`` 에서 사용.
        ``ainvoke`` 완료 후에는 ``adispatch_custom_event`` 를
        사용할 수 없으므로 직접 호출한다.
        """
        if self._enabled:
            self._record_sql(data)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Run 라이프사이클
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def start_run(
        self,
        user_input: str,
        session_id: str = "",
    ) -> None:
        """파이프라인 실행 추적을 시작한다."""
        self._start_time = time.perf_counter()
        self._trace.user_input = user_input
        self._trace.session_id = session_id
        self._trace.start_time = now_stamp()

    def end_run(
        self,
        final_intent: str = "",
        final_status: str = "",
        final_response_summary: str = "",
        error_message: str = "",
    ) -> None:
        """파이프라인 실행 추적을 종료한다."""
        self._trace.end_time = now_stamp()
        self._trace.total_duration_ms = (
            (time.perf_counter() - self._start_time) * 1000
        )
        self._trace.final_intent = final_intent
        self._trace.final_status = final_status
        self._trace.final_response_summary = (
            final_response_summary
        )
        self._trace.error_message = error_message

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 저장 / 내보내기
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def save(
        self,
        output_dir: str | None = None,
        *,
        turn_id: str = "",
        user_id: str = "anonymous",
        with_report: bool = True,
    ) -> list[dict[str, str]]:
        """트레이스를 JSON + Markdown 보고서로 저장한다.

        Args:
            output_dir: 출력 디렉토리 (기본: settings 참조).
            turn_id: 턴 식별자 (파일명에 12자 사용).
            user_id: 사용자 식별자 (기본: anonymous).
            with_report: 보고서 생성 여부 (개별 플래그와 AND).

        Returns:
            생성된 파일 목록 ``[{"name": ..., "filename": ...}, ...]``.
        """
        if not self._enabled:
            return []

        base = Path(
            output_dir or settings.eval_tracker_output_dir,
        )
        base.mkdir(parents=True, exist_ok=True)

        from src.utils.timezone import now_kst

        date_str = now_kst().strftime("%Y%m%d")
        tid = turn_id.replace("-", "")[:12] if turn_id else self._run_id[:12]
        sid = self._trace.session_id or self._run_id
        prefix = f"{date_str}_{user_id}_{sid}_{tid}"

        data = self._trace.model_dump(mode="json")
        saved_files: list[dict[str, str]] = []

        # JSON 텔레메트리
        if settings.eval_trace_json_enabled:
            filename = f"trace_telemetry_{prefix}.json"
            filepath = base / filename
            filepath.write_text(
                json.dumps(
                    data, ensure_ascii=False, indent=2,
                ),
                encoding="utf-8",
            )
            logger.info(
                "평가 트레이스 저장", path=str(filepath),
            )
            saved_files.append({
                "name": "텔레메트리 (JSON)",
                "filename": filename,
            })

        # Markdown 보고서 (기존 5섹션)
        if (
            settings.eval_trace_report_enabled
            and with_report
            and self._trace.timeline
        ):
            from src.utils.tracker.visualizer import (
                save_report,
            )

            report_name = f"trace_report_{prefix}.md"
            save_report(data, base / report_name)
            saved_files.append({
                "name": "분석 보고서",
                "filename": report_name,
            })

        # Reasoning flow Markdown (신규)
        if (
            settings.eval_trace_reasoning_enabled
            and self._trace.reasoning_flow
        ):
            from src.utils.tracker.visualizer import (
                render_reasoning_flow,
            )
            reasoning_name = (
                f"trace_reasoning_{prefix}.md"
            )
            reasoning_path = base / reasoning_name
            reasoning_md = render_reasoning_flow(data)
            reasoning_path.write_text(
                reasoning_md, encoding="utf-8",
            )
            logger.info(
                "reasoning flow 저장",
                path=str(reasoning_path),
            )
            saved_files.append({
                "name": "추론 흐름",
                "filename": reasoning_name,
            })

        return saved_files

    def to_dict(self) -> dict[str, Any]:
        """트레이스를 dict로 직렬화한다."""
        return self._trace.model_dump(mode="json")

    def resume_from(
        self,
        previous: dict[str, Any],
    ) -> None:
        """이전 트레이스 데이터를 복원하여 이어서 기록한다.

        명확화 인터럽트 후 재개 시, 이전 턴의 트레이스를
        이어받아 전체 사고 과정을 하나의 흐름으로 연결한다.

        Args:
            previous: 이전 트레이스 JSON (model_dump 결과).
        """
        # reasoning_flow 복원
        for step_data in previous.get("reasoning_flow", []):
            routing_data = step_data.get("routing", {})
            step = ReasoningStep(
                seq=step_data.get("seq", 0),
                node=step_data.get("node", ""),
                phase=step_data.get("phase", ""),
                round=step_data.get("round", 0),
                hypothesis_id=step_data.get(
                    "hypothesis_id", "",
                ),
                step_type=step_data.get("step_type", ""),
                inputs=step_data.get("inputs", {}),
                output=step_data.get("output", {}),
                routing=RoutingDecision(**routing_data)
                if routing_data
                else RoutingDecision(),
                duration_ms=step_data.get(
                    "duration_ms", 0.0,
                ),
                model=step_data.get("model", ""),
                tokens=step_data.get("tokens", 0),
                timestamp=step_data.get("timestamp", ""),
            )
            self._trace.reasoning_flow.append(step)

        # timeline 복원
        for entry_data in previous.get("timeline", []):
            entry = TimelineEntry(
                seq=entry_data.get("seq", 0),
                event_type=entry_data.get(
                    "event_type", "",
                ),
                node=entry_data.get("node", ""),
                parent_seq=entry_data.get("parent_seq"),
                summary=entry_data.get("summary", ""),
                detail=entry_data.get("detail", {}),
                duration_ms=entry_data.get(
                    "duration_ms", 0.0,
                ),
                status=entry_data.get("status", ""),
                timestamp=entry_data.get("timestamp", ""),
            )
            self._trace.timeline.append(entry)

        # llm_calls 복원
        for call_data in previous.get("llm_calls", []):
            record = LLMCallRecord(
                node=call_data.get("node", ""),
                prompt_summary=call_data.get(
                    "prompt_summary", "",
                ),
                prompt_variables=call_data.get(
                    "prompt_variables", {},
                ),
                prompt_tokens=call_data.get(
                    "prompt_tokens", 0,
                ),
                response_text=call_data.get(
                    "response_text", "",
                ),
                response_tokens=call_data.get(
                    "response_tokens", 0,
                ),
                model=call_data.get("model", ""),
                latency_ms=call_data.get(
                    "latency_ms", 0.0,
                ),
                timestamp=call_data.get("timestamp", ""),
            )
            self._trace.llm_calls.append(record)

        # node_path 복원
        self._trace.node_path = (
            previous.get("node_path", [])
            + self._trace.node_path
        )

        # 실행 시작 정보 복원
        self._trace.start_time = previous.get(
            "start_time", self._trace.start_time,
        )
        self._trace.user_input = previous.get(
            "user_input", self._trace.user_input,
        )

        # 순번 카운터 복원
        prev_timeline = previous.get("timeline", [])
        if prev_timeline:
            self._seq = max(
                e.get("seq", 0) for e in prev_timeline
            )
        prev_reasoning = previous.get(
            "reasoning_flow", [],
        )
        if prev_reasoning:
            self._reasoning_seq = max(
                s.get("seq", 0) for s in prev_reasoning
            )

        # 누적 통계 복원
        self._trace.total_llm_calls += previous.get(
            "total_llm_calls", 0,
        )
        self._trace.total_llm_tokens += previous.get(
            "total_llm_tokens", 0,
        )
        self._trace.total_llm_latency_ms += previous.get(
            "total_llm_latency_ms", 0.0,
        )

        logger.debug(
            "이전 트레이스 복원 완료",
            prev_reasoning_steps=len(prev_reasoning),
            prev_timeline_entries=len(prev_timeline),
        )

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # WebSocket 진행률
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def _emit_progress(
        self,
        node: str,
        action: str,
    ) -> None:
        """WebSocket으로 노드 진행 상황을 전송한다."""
        if self._on_event is None:
            return
        if node not in NODE_PROGRESS_MAP:
            return

        if node in ("context_explorer", "context_retriever") and action == "add":
            self._explore_count += 1

        info = NODE_PROGRESS_MAP[node]
        label = info["label"]
        phase = info["phase"]
        if (
            node in ("context_explorer", "context_retriever")
            and self._explore_count > 1
        ):
            label = (
                f"추가 데이터 탐색"
                f" ({self._explore_count}차)"
            )

        try:
            await self._on_event(
                {
                    "type": "progress",
                    "action": action,
                    "phase": phase,
                    "phaseLabel": PHASE_LABELS.get(
                        phase, phase,
                    ),
                    "label": label,
                    "thinkingLabel": info["thinking"],
                }
            )
        except Exception as e:
            logger.debug("WebSocket progress 전파 실패", error=str(e))

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 유틸리티
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _get_active_parent_seq(
        self,
        node: str,
    ) -> int | None:
        """현재 활성 노드의 시작 seq를 반환한다."""
        timer = self._node_timers.get(node)
        return timer[1] if timer else None

    def get_node_display_name(self, node: str) -> str:
        """노드의 표시 이름을 반환한다 (반복 시 ①②③)."""
        count = self._node_visit_count.get(node, 0)
        if count <= 1:
            return node
        markers = "①②③④⑤⑥⑦⑧⑨⑩"
        idx = min(count - 1, len(markers) - 1)
        return f"{node} {markers[idx]}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 모듈 수준 유틸리티 (State diff)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _snapshot_state(
    state: dict[str, Any],
) -> dict[str, Any]:
    """State에서 추적 대상 키만 얕은 복사한다.

    전체 State 복사는 비용이 크므로 주요 키만 추적한다.
    """
    snapshot: dict[str, Any] = {}
    for key in _TRACKED_STATE_KEYS:
        if key not in state:
            continue
        val = state[key]
        if key == "reason" and hasattr(val, "phase"):
            # ReasoningState 요약
            snapshot[key] = {
                "phase": str(getattr(val, "phase", "")),
                "hypotheses_count": len(
                    getattr(val, "hypotheses", []),
                ),
                "explored_tables_count": len(
                    getattr(val, "explored_tables", []),
                ),
                "knowledge_confirmed": sum(
                    1
                    for ki in getattr(
                        val, "knowledge_items", [],
                    )
                    if getattr(ki, "status", None)
                    and str(ki.status) == "CONFIRMED"
                ),
                "generated_sql": bool(
                    getattr(val, "generated_sql", None),
                ),
                "validated_sql": bool(
                    getattr(val, "validated_sql", None),
                ),
                "final_status": str(
                    getattr(val, "final_status", ""),
                ),
            }
        elif hasattr(val, "value"):
            # Enum
            snapshot[key] = val.value
        elif isinstance(val, str):
            snapshot[key] = truncate_trace(val)
        else:
            snapshot[key] = val
    return snapshot


def _compute_state_diff(
    before: dict[str, Any],
    outputs: dict[str, Any] | Any,
) -> list[dict[str, str]]:
    """노드 입력과 출력의 차이를 계산한다.

    LangGraph에서 outputs는 변경된 키만 포함하는 partial dict.
    조건부 엣지 등에서 str이 올 수 있어 방어 처리한다.
    """
    if not isinstance(outputs, dict):
        logger.debug(
            "state diff 스킵: outputs가 dict가 아님",
            output_type=type(outputs).__name__,
        )
        return []
    changes: list[dict[str, str]] = []
    for key, new_val in outputs.items():
        if key in ("trace_log",):
            continue  # 메타 필드 제외

        new_display = _format_value(new_val)
        old_val = before.get(key)
        if old_val is not None:
            old_display = _format_value(old_val)
            if old_display != new_display:
                changes.append(
                    {
                        "field": key,
                        "before": old_display,
                        "after": new_display,
                    }
                )
        else:
            changes.append(
                {"field": key, "after": new_display}
            )
    return changes


def _format_value(val: Any) -> str:
    """값을 사람이 읽기 쉬운 문자열로 변환한다."""
    if hasattr(val, "value"):
        return str(val.value)
    if isinstance(val, str):
        return truncate_trace(val)
    if isinstance(val, (list, dict)):
        return truncate_trace(str(val))
    return truncate_trace(str(val))


def _summarize_state(
    state: dict[str, Any] | Any,
) -> dict[str, Any]:
    """State를 사람이 읽기 쉬운 요약 dict로 변환한다."""
    if not isinstance(state, dict):
        return {"raw": truncate_trace(str(state))}
    summary: dict[str, Any] = {}
    for key, val in state.items():
        if isinstance(val, str):
            summary[key] = truncate_trace(val)
        elif isinstance(val, (list, dict)):
            summary[key] = (
                f"({type(val).__name__}, len={len(val)})"
            )
        elif hasattr(val, "value"):
            summary[key] = val.value
        else:
            summary[key] = val
    return summary
