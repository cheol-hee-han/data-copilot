# Tracker 리팩토링 상세 설계서

- **작성일**: 2026-03-30
- **목표**: LangGraph 표준 콜백 체계(`AsyncCallbackHandler` + `adispatch_custom_event`)로 전환
- **범위**: `src/utils/tracker/`, `src/agents/graph/`, 노드 14개소, 커넥터/유틸리티 4개소

---

## 1. 현재 구조의 문제점

### 1.1 아키텍처 문제

```
[현재 — 매 요청마다 그래프 재빌드]

runner.py
  ├── EvaluationTracker(run_id) 생성
  ├── tracker.on_node_event = on_event     ← WebSocket 콜백 주입
  ├── tracker.start_run(user_input)
  ├── create_app(tracker=tracker)           ← ⚠️ 매 요청마다 빌드+컴파일
  │     └── build_pipeline(tracker)
  │           ├── tracker.inject()          ← contextvars에 tracker 설정
  │           └── tracker.track(name)(fn)   ← 15개 노드를 데코레이터로 래핑
  ├── app.ainvoke(initial_state)
  └── tracker.save()
```

| 문제 | 상세 |
|------|------|
| **그래프 재빌드** | `tracker` 인스턴스가 빌드 시점에 데코레이터로 바인딩되어, 요청마다 새 그래프 필요 |
| **LangGraph 패턴 미준수** | 프로덕션 표준은 그래프 1회 컴파일 + `ainvoke(config={"callbacks": [...]})` |
| **contextvars 이중 사용** | `_current_tracker` + `_current_node` 두 개의 ContextVar로 tracker 전파 |
| **산재된 호출부** | 10개 파일 14곳에서 `get_current_tracker()` 직접 호출 |
| **State 변화 미추적** | 요구사항에 명시된 state 변화 기록이 구현되지 않음 |

### 1.2 현재 호출부 전수 목록

| # | 파일 | 메서드 | 데이터 |
|---|------|--------|--------|
| 1 | `intent_classifier.py:95` | `track_decision` | intent, confidence, reason |
| 2 | `query_normalizer.py:92` | `track_decision` | normalization slots |
| 3 | `confidence_evaluator.py:137` | `track_decision` | verdict, score, knowledge counts |
| 4 | `context_explorer.py:156` | `track_context_retrieval` | tool success |
| 5 | `context_explorer.py:180` | `track_context_retrieval` | tool error |
| 6 | `context_explorer.py:332` | `track_decision` | table comparison |
| 7 | `context_explorer.py:515` | `record_prompt_variables` | batch vars |
| 8 | `context_explorer.py:518` | `track_llm_call` | batch LLM |
| 9 | `sql_executor.py:84` | `track_context_retrieval` | SQL execution |
| 10 | `qdrant_connector.py:155` | `track_context_retrieval` | embedding |
| 11 | `reranker.py:375` | `track_context_retrieval` | reranking |
| 12 | `client.py:154` | `track_llm_call` | Anthropic call |
| 13 | `client.py:241` | `track_llm_call` | OpenAI call |
| 14 | `tracker/__init__.py:54` | `record_prompt_variables` | prompt vars |

---

## 2. 목표 구조

### 2.1 아키텍처 개요

```
[변경 후 — 싱글턴 그래프 + 표준 콜백]

앱 시작 시 (1회):
  compiled_app = build_pipeline().compile()

요청마다:
  runner.py
    ├── handler = DataCopilotCallbackHandler(session_id, on_event)
    ├── handler.start_run(user_input)
    ├── compiled_app.ainvoke(                      ← 싱글턴 재사용
    │       initial_state,
    │       config={"callbacks": [handler]}         ← 요청별 핸들러 주입
    │   )
    │   ├── on_chain_start  →  노드 시작 기록 + WebSocket + set_current_node
    │   ├── 노드 내부: adispatch_custom_event(...)  ← 비즈니스 이벤트
    │   │     → on_custom_event 에서 수신/기록
    │   └── on_chain_end    →  노드 종료 기록 + state diff + WebSocket
    ├── handler.end_run(...)
    └── handler.save()
```

### 2.2 핵심 설계 원칙

| 원칙 | 설명 |
|------|------|
| **표준 준수** | LangGraph `AsyncCallbackHandler` + `adispatch_custom_event` 표준 API만 사용 |
| **그래프 불변** | `StateGraph` 1회 빌드 후 `CompiledGraph`를 모듈 수준 싱글턴으로 유지 |
| **요청 격리** | 핸들러 인스턴스가 요청별 상태를 보유, `config={"callbacks": [...]}` 로 주입 |
| **최소 contextvars** | `_current_tracker` 삭제, `_current_node`만 유지 (LLM client용) |
| **출력 호환** | `EvaluationTrace` 모델 유지 → 기존 `visualizer.py`, `trace_analyzer.py` 변경 없음 |

### 2.3 기술 전제조건

| 항목 | 현재 | 요구 | 충족 |
|------|------|------|------|
| `langchain-core` | 1.2.20 | ≥ 0.2.15 | ✅ |
| `langgraph` | 1.1.2 | ≥ 0.2.0 | ✅ |
| Python | 3.12 | ≥ 3.11 (contextvars 자동 전파) | ✅ |

Python 3.12에서 `adispatch_custom_event(name, data)` 호출 시 `config` 생략 가능 — LangGraph가 설정한 `RunnableConfig`를 contextvars에서 자동 추출한다.

---

## 3. 이벤트 체계 설계

### 3.1 이벤트 네이밍 규칙

```
{도메인}.{행위}
```

| 이벤트 이름 | 대체 대상 | 설명 |
|------------|----------|------|
| `decision.intent` | `track_decision(decision_type="intent_classification")` | 의도 분류 |
| `decision.normalization` | `track_decision(decision_type="normalization_result")` | 정규화 결과 |
| `decision.readiness` | `track_decision(decision_type="readiness_verdict")` | 준비도 판정 |
| `decision.table_comparison` | `track_decision(decision_type="table_comparison")` | 테이블 비교 |
| `context.tool_success` | `track_context_retrieval(status="success")` | 도구 실행 성공 |
| `context.tool_error` | `track_context_retrieval(status="error")` | 도구 실행 실패 |
| `context.sql_executed` | `track_context_retrieval(source="info_db_execute")` | SQL 실행 결과 |
| `context.embedding` | `track_context_retrieval(source="embedding_encode")` | 임베딩 인코딩 |
| `context.reranked` | `track_context_retrieval(source="reranker")` | 리랭킹 결과 |
| `llm.call` | `track_llm_call` | LLM 호출 완료 |
| `llm.prompt_variables` | `record_prompt_variables` | 프롬프트 치환 변수 |
| `sql.recorded` | `track_sql` | SQL 생성/검증/실행 메트릭 |
| `eval.result` | `track_eval_result` | 골든셋 평가 결과 |

### 3.2 이벤트 페이로드 스키마

모든 페이로드는 기존 Pydantic 모델의 필드와 1:1 대응한다.

```python
# decision.* 페이로드 — DecisionRecord 대응
{
    "node": str,
    "decision_type": str,
    "chosen": str,
    "alternatives": list[str],
    "confidence": float,
    "reason": str,
}

# context.* 페이로드 — ContextRetrievalRecord 대응
{
    "source": str,
    "query": str,
    "results_count": int,
    "results_summary": list[str],
    "latency_ms": float,
    "status": str,  # "success" | "error"
}

# llm.call 페이로드 — LLMCallRecord 대응
{
    "node": str,
    "prompt_summary": str,
    "prompt_variables": dict[str, str] | None,
    "response_text": str,
    "model": str,
    "prompt_tokens": int,
    "response_tokens": int,
    "latency_ms": float,
}

# llm.prompt_variables 페이로드
{
    "variables": dict[str, str],
}

# sql.recorded 페이로드 — SQLRecord 대응
{
    "generated_sql": str,
    "validated": bool,
    "validation_errors": list[str],
    "retry_count": int,
    "validation_feedback": str,
    "execution_success": bool,
    "row_count": int,
    "execution_time_ms": float,
}

# eval.result 페이로드
{
    "passed": bool,
    "errors": list[str],
}
```

### 3.3 이벤트 디스패치 안전 래퍼

`adispatch_custom_event`는 LangGraph 실행 컨텍스트 밖에서 호출 시 `RuntimeError`를 발생시킨다.
커넥터, LLM 클라이언트는 단위 테스트에서 그래프 없이 호출될 수 있으므로 안전 래퍼가 필요하다.

```python
# src/utils/tracker/dispatch.py

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def dispatch_tracking_event(
    name: str,
    data: dict[str, Any],
) -> None:
    """LangGraph 실행 컨텍스트 내에서만 커스텀 이벤트를 디스패치한다.

    그래프 외부(단위 테스트 등)에서 호출되면 조용히 무시한다.
    Python 3.12에서 config를 contextvars에서 자동 추출하므로
    명시적 config 전달이 불필요하다.
    """
    try:
        from langchain_core.callbacks.manager import (
            adispatch_custom_event,
        )
        await adispatch_custom_event(name, data)
    except RuntimeError:
        # LangGraph 실행 컨텍스트 밖 — 무시
        pass
    except Exception:
        logger.debug(
            "tracking event dispatch 실패",
            extra={"event": name},
        )
```

**노드 함수 내부**(config 보장)에서는 명시적으로 `adispatch_custom_event`를 직접 호출해도 안전하다.
**커넥터/유틸리티**(config 미보장 가능)에서는 반드시 `dispatch_tracking_event`를 사용한다.

---

## 4. 핵심 컴포넌트 설계

### 4.1 DataCopilotCallbackHandler

```python
# src/utils/tracker/callback_handler.py

from __future__ import annotations

import time
import json
import logging
from pathlib import Path
from typing import Any
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler

from src.config import settings
from src.utils.tracker.context import set_current_node
from src.utils.tracker.evaluation import (
    EvaluationTrace,
    TimelineEntry,
    NodeRecord,
    LLMCallRecord,
    DecisionRecord,
    ContextRetrievalRecord,
    SQLRecord,
)

logger = logging.getLogger(__name__)


# 프로그레스 메시지 매핑 (runner.py에서 이동)
NODE_PROGRESS_MAP: dict[str, dict[str, str]] = {
    "classify_intent": {
        "label": "🔍 질문을 분석하고 있습니다",
        "thinking": "질문 의도 파악 중",
    },
    "normalize_query": {
        "label": "🔍 질문을 정리하고 있습니다",
        "thinking": "질문 정규화 중",
    },
    "planner": {
        "label": "🧠 데이터 탐색 전략을 세우고 있습니다",
        "thinking": "탐색 계획 수립 중",
    },
    "context_explorer": {
        "label": "📂 관련 테이블과 데이터를 찾고 있습니다",
        "thinking": "데이터 소스 탐색 중",
    },
    "sql_generator": {
        "label": "⚙️ 조회 조건을 작성하고 있습니다",
        "thinking": "SQL 생성 중",
    },
    "sql_validator": {
        "label": "✅ 조회 조건을 검증하고 있습니다",
        "thinking": "SQL 검증 중",
    },
    "recovery_planner": {
        "label": "🔄 다른 방법을 시도하고 있습니다",
        "thinking": "대안 탐색 중",
    },
    "execute_sql": {
        "label": "🗄️ 데이터를 조회하고 있습니다",
        "thinking": "데이터베이스 조회 중",
    },
    "analyze_data": {
        "label": "📊 결과를 분석하고 있습니다",
        "thinking": "데이터 분석 중",
    },
    "format_response": {
        "label": "📝 보고서를 작성하고 있습니다",
        "thinking": "결과 정리 중",
    },
}


class DataCopilotCallbackHandler(AsyncCallbackHandler):
    """LangGraph 표준 콜백 기반 파이프라인 텔레메트리 핸들러.

    요청마다 새 인스턴스를 생성하여 config={"callbacks": [handler]}로 주입한다.
    EvaluationTracker의 모든 기능을 LangGraph 표준 API로 대체한다.

    수집 항목:
        - 노드 시작/종료 + 실행 시간
        - State 변화 (입력→출력 diff)
        - LLM 호출 (in/out, 토큰, 지연시간)
        - 의사결정 (intent, table, confidence)
        - 컨텍스트 검색 (ES, Qdrant, DB)
        - SQL 라이프사이클 (생성→검증→실행)
        - 통합 타임라인 (순번 + 부모-자식)
        - WebSocket 진행률 전파
    """

    # ── 생성자 ──

    def __init__(
        self,
        run_id: str = "",
        *,
        on_event: Any = None,
        enabled: bool | None = None,
    ) -> None:
        super().__init__()
        from src.utils.llm.client import now_filesafe

        self._run_id = run_id or now_filesafe()
        self._trace = EvaluationTrace(run_id=self._run_id)
        self._enabled = (
            enabled if enabled is not None
            else settings.eval_tracker_enabled
        )

        # 타임라인 순번
        self._seq: int = 0
        # run_id → node_name 매핑 (on_chain_end에서 노드 식별)
        self._run_to_node: dict[str, str] = {}
        # node_name → (start_time, start_seq) (duration 계산용)
        self._node_timers: dict[str, tuple[float, int]] = {}
        # node_name → input state snapshot (state diff용)
        self._node_inputs: dict[str, dict[str, Any]] = {}
        # context_explorer 반복 카운터
        self._explore_count: int = 0

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

    # ── 타임라인 관리 (내부) ──

    def _next_seq(self) -> int:
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
            timestamp=self._now_iso(),
        )
        self._trace.timeline.append(entry)
        return seq

    # ── 표준 훅: 노드 경계 ──

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
        self._run_to_node[rid] = node

        # contextvars에 현재 노드 설정 (client.py 등에서 참조)
        set_current_node(node)

        # WebSocket 진행률 전송
        await self._emit_progress(node, "add")

        if not self._enabled:
            return

        # 타이머 시작
        start_seq = self._append_timeline(
            "node_start", node,
            summary=f"{node} 시작",
        )
        self._node_timers[node] = (time.perf_counter(), start_seq)

        # State 스냅샷 (diff 계산용)
        self._node_inputs[node] = self._snapshot_state(inputs)

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
        node = self._run_to_node.pop(rid, None)
        if not node:
            return

        # WebSocket 진행률 전송
        await self._emit_progress(node, "done")

        if not self._enabled:
            return

        # duration 계산
        timer = self._node_timers.pop(node, None)
        duration_ms = 0.0
        parent_seq: int | None = None
        if timer:
            duration_ms = (time.perf_counter() - timer[0]) * 1000
            parent_seq = timer[1]

        # State diff 계산
        before = self._node_inputs.pop(node, {})
        state_changes = self._compute_state_diff(before, outputs)

        # 타임라인 기록
        self._append_timeline(
            "node_end", node,
            summary=f"{node} 완료",
            detail={"state_changes": state_changes},
            duration_ms=duration_ms,
            status="success",
            parent_seq=parent_seq,
        )

        # NodeRecord 기록
        self._trace.nodes.append(NodeRecord(
            node=node,
            input_summary=self._summarize_state(before),
            output_summary=self._summarize_state(outputs),
            duration_ms=round(duration_ms, 1),
            status="success",
            timestamp=self._now_iso(),
        ))

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
        node = self._run_to_node.pop(rid, None)
        if not node:
            return

        await self._emit_progress(node, "done")

        if not self._enabled:
            return

        timer = self._node_timers.pop(node, None)
        duration_ms = 0.0
        parent_seq: int | None = None
        if timer:
            duration_ms = (time.perf_counter() - timer[0]) * 1000
            parent_seq = timer[1]

        self._node_inputs.pop(node, None)

        self._append_timeline(
            "node_end", node,
            summary=f"{node} 실패: {error}",
            duration_ms=duration_ms,
            status="error",
            parent_seq=parent_seq,
        )

        self._trace.nodes.append(NodeRecord(
            node=node,
            duration_ms=round(duration_ms, 1),
            status="error",
            error_message=str(error)[:500],
            timestamp=self._now_iso(),
        ))

    # ── 표준 훅: 커스텀 이벤트 수신 ──

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
        """adispatch_custom_event로 디스패치된 비즈니스 이벤트를 수신한다."""
        if not self._enabled:
            return

        # 현재 활성 노드 식별 (run_id → node or fallback)
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
            case "eval":
                self._record_eval(data)

    # ── 커스텀 이벤트 레코딩 ──

    def _record_decision(
        self, node: str, data: dict[str, Any],
    ) -> None:
        """의사결정을 기록한다."""
        record = DecisionRecord(
            node=data.get("node", node),
            decision_type=data.get("decision_type", ""),
            chosen=data.get("chosen", ""),
            alternatives=data.get("alternatives", []),
            confidence=data.get("confidence", 0.0),
            reason=data.get("reason", ""),
            timestamp=self._now_iso(),
        )
        self._trace.decisions.append(record)

        parent_seq = self._get_active_parent_seq(node)
        self._append_timeline(
            "decision", data.get("node", node),
            summary=(
                f"{data.get('decision_type', '')}: "
                f"{data.get('chosen', '')}"
            ),
            detail={
                "confidence": data.get("confidence", 0.0),
                "alternatives": data.get("alternatives", []),
                "reason": data.get("reason", ""),
            },
            parent_seq=parent_seq,
        )

    def _record_context_retrieval(
        self, node: str, data: dict[str, Any],
    ) -> None:
        """컨텍스트 수집을 기록한다."""
        record = ContextRetrievalRecord(
            source=data.get("source", ""),
            query=data.get("query", ""),
            results_count=data.get("results_count", 0),
            results_summary=data.get("results_summary", []),
            latency_ms=data.get("latency_ms", 0.0),
            timestamp=self._now_iso(),
        )
        self._trace.context_retrievals.append(record)

        parent_seq = self._get_active_parent_seq(node)
        status = data.get("status", "success")
        self._append_timeline(
            "tool_call", node,
            summary=(
                f"{data.get('source', '')}: "
                f"{data.get('results_count', 0)}건"
            ),
            detail={
                "source": data.get("source", ""),
                "query": data.get("query", "")[:200],
                "results_count": data.get("results_count", 0),
            },
            duration_ms=data.get("latency_ms", 0.0),
            status=status,
            parent_seq=parent_seq,
        )

    def _record_llm_call(self, data: dict[str, Any]) -> None:
        """LLM 호출을 기록한다."""
        record = LLMCallRecord(
            node=data.get("node", ""),
            prompt_summary=data.get("prompt_summary", ""),
            prompt_variables=data.get("prompt_variables") or {},
            prompt_tokens=data.get("prompt_tokens", 0),
            response_text=data.get("response_text", ""),
            response_tokens=data.get("response_tokens", 0),
            model=data.get("model", ""),
            latency_ms=data.get("latency_ms", 0.0),
            timestamp=self._now_iso(),
        )
        self._trace.llm_calls.append(record)

        # 통계 갱신
        self._trace.total_llm_calls += 1
        self._trace.total_llm_latency_ms += record.latency_ms
        self._trace.total_llm_tokens += (
            record.prompt_tokens + record.response_tokens
        )

        node = data.get("node", "")
        parent_seq = self._get_active_parent_seq(node)
        self._append_timeline(
            "llm_call", node,
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
        self, data: dict[str, Any],
    ) -> None:
        """직전 LLM 호출 기록에 프롬프트 치환 변수를 보강한다."""
        variables = data.get("variables", {})
        if self._trace.llm_calls and variables:
            self._trace.llm_calls[-1].prompt_variables = variables

    def _record_sql(self, data: dict[str, Any]) -> None:
        """SQL 라이프사이클을 기록한다."""
        self._trace.sql = SQLRecord(
            generated_sql=data.get("generated_sql", ""),
            validated=data.get("validated", False),
            validation_errors=data.get("validation_errors", []),
            retry_count=data.get("retry_count", 0),
            validation_feedback=data.get("validation_feedback", ""),
            execution_success=data.get("execution_success", False),
            row_count=data.get("row_count", 0),
            execution_time_ms=data.get("execution_time_ms", 0.0),
        )

    def _record_eval(self, data: dict[str, Any]) -> None:
        """골든셋 평가 결과를 기록한다."""
        self._trace.eval_passed = data.get("passed")
        self._trace.eval_errors = data.get("errors", [])

    # ── Run 라이프사이클 ──

    def start_run(
        self,
        user_input: str,
        session_id: str = "",
        golden_id: str = "",
    ) -> None:
        """파이프라인 실행 추적을 시작한다."""
        self._start_time = time.perf_counter()
        self._trace.user_input = user_input
        self._trace.session_id = session_id
        self._trace.golden_id = golden_id
        self._trace.start_time = self._now_iso()

    def end_run(
        self,
        final_intent: str = "",
        final_status: str = "",
        final_response_summary: str = "",
        error_message: str = "",
    ) -> None:
        """파이프라인 실행 추적을 종료한다."""
        self._trace.end_time = self._now_iso()
        self._trace.total_duration_ms = (
            (time.perf_counter() - self._start_time) * 1000
        )
        self._trace.final_intent = final_intent
        self._trace.final_status = final_status
        self._trace.final_response_summary = final_response_summary
        self._trace.error_message = error_message

    # ── 저장/내보내기 ──

    def save(
        self,
        output_dir: str | None = None,
        *,
        with_report: bool = True,
    ) -> Path | None:
        """트레이스를 JSON + Markdown 보고서로 저장한다."""
        if not self._enabled:
            return None

        from src.utils.llm.client import now_filesafe
        from src.utils.tracker.visualizer import save_report

        base = Path(
            output_dir or settings.eval_tracker_output_dir,
        )
        base.mkdir(parents=True, exist_ok=True)

        ts = now_filesafe()
        filepath = base / f"trace_{self._run_id}_{ts}.json"
        data = self._trace.model_dump(mode="json")
        filepath.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        if with_report and self._trace.timeline:
            report_path = base / f"report_{self._run_id}_{ts}.md"
            save_report(data, report_path)

        return filepath

    def to_dict(self) -> dict[str, Any]:
        """트레이스를 dict로 직렬화한다."""
        return self._trace.model_dump(mode="json")

    # ── State Diff 추적 (신규 기능) ──

    @staticmethod
    def _snapshot_state(
        state: dict[str, Any],
    ) -> dict[str, Any]:
        """State에서 추적 대상 키만 얕은 복사한다.

        전체 State 복사는 비용이 크므로, 주요 키만 추적한다.
        """
        # 추적 대상: 비즈니스 의미가 있는 키만 선별
        tracked_keys = {
            # interpret
            "intent", "intent_confidence", "query_category",
            "preprocessed_input", "clarification_question",
            "awaiting_clarification",
            # reason (중첩 객체는 존재여부 + 요약만)
            "reason",
            # present
            "status", "error_message",
            "formatted_response",
        }
        snapshot: dict[str, Any] = {}
        for key in tracked_keys:
            if key in state:
                val = state[key]
                if key == "reason" and hasattr(val, "phase"):
                    # ReasoningState 요약
                    snapshot[key] = {
                        "phase": str(getattr(val, "phase", "")),
                        "hypotheses_count": len(
                            getattr(val, "hypotheses", [])
                        ),
                        "candidate_tables_count": len(
                            getattr(val, "candidate_tables", [])
                        ),
                        "knowledge_confirmed": sum(
                            1 for ki in getattr(
                                val, "knowledge_items", [],
                            )
                            if getattr(ki, "status", None)
                            and str(ki.status) == "CONFIRMED"
                        ),
                        "generated_sql": bool(
                            getattr(val, "generated_sql", None)
                        ),
                        "validated_sql": bool(
                            getattr(val, "validated_sql", None)
                        ),
                        "final_status": str(
                            getattr(val, "final_status", "")
                        ),
                    }
                elif hasattr(val, "value"):
                    # Enum
                    snapshot[key] = val.value
                elif isinstance(val, str) and len(val) > 200:
                    snapshot[key] = val[:200] + "..."
                else:
                    snapshot[key] = val
        return snapshot

    @staticmethod
    def _compute_state_diff(
        before: dict[str, Any],
        outputs: dict[str, Any],
    ) -> list[dict[str, str]]:
        """노드 입력과 출력의 차이를 계산한다.

        LangGraph에서 outputs는 변경된 키만 포함하는 partial dict이다.
        """
        changes: list[dict[str, str]] = []
        for key, new_val in outputs.items():
            if key in ("trace_log",):
                continue  # 메타 필드 제외

            # Enum, Pydantic 모델 등 요약
            new_display = DataCopilotCallbackHandler._format_value(
                new_val,
            )
            old_val = before.get(key)
            if old_val is not None:
                old_display = str(old_val)[:100]
                if old_display != new_display:
                    changes.append({
                        "field": key,
                        "before": old_display,
                        "after": new_display,
                    })
            else:
                changes.append({
                    "field": key,
                    "after": new_display,
                })
        return changes

    @staticmethod
    def _format_value(val: Any) -> str:
        """값을 사람이 읽기 쉬운 문자열로 변환한다."""
        if hasattr(val, "value"):
            return str(val.value)
        if isinstance(val, str):
            return val[:100] + ("..." if len(val) > 100 else "")
        if isinstance(val, (list, dict)):
            s = str(val)
            return s[:100] + ("..." if len(s) > 100 else "")
        return str(val)[:100]

    @staticmethod
    def _summarize_state(
        state: dict[str, Any],
    ) -> dict[str, Any]:
        """State를 사람이 읽기 쉬운 요약 dict로 변환한다."""
        summary: dict[str, Any] = {}
        for key, val in state.items():
            if isinstance(val, str) and len(val) > 100:
                summary[key] = val[:100] + "..."
            elif isinstance(val, (list, dict)):
                summary[key] = f"({type(val).__name__}, len={len(val)})"
            elif hasattr(val, "value"):
                summary[key] = val.value
            else:
                summary[key] = val
        return summary

    # ── WebSocket 진행률 ──

    async def _emit_progress(
        self, node: str, action: str,
    ) -> None:
        """WebSocket으로 노드 진행 상황을 전송한다."""
        if self._on_event is None:
            return
        if node not in NODE_PROGRESS_MAP:
            return

        if node == "context_explorer" and action == "add":
            self._explore_count += 1

        info = NODE_PROGRESS_MAP[node]
        label = info["label"]
        if (
            node == "context_explorer"
            and self._explore_count > 1
        ):
            label = (
                "📂 추가 데이터를 탐색하고 있습니다"
                f" ({self._explore_count}차)"
            )

        try:
            await self._on_event({
                "type": "progress",
                "action": action,
                "label": label,
                "thinkingLabel": info["thinking"],
            })
        except Exception:
            pass

    # ── 유틸리티 ──

    def _get_active_parent_seq(self, node: str) -> int | None:
        """현재 활성 노드의 시작 seq를 반환한다."""
        timer = self._node_timers.get(node)
        return timer[1] if timer else None

    @staticmethod
    def _now_iso() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()
```

### 4.2 State 변화 추적 상세

**현재**: 미구현 (요구사항 누락)

**신규 구현**: `on_chain_start`에서 입력 State 스냅샷을 저장하고, `on_chain_end`에서 출력과 비교하여 diff를 계산한다.

#### 추적 대상 키

| 계층 | 키 | 설명 |
|------|---|------|
| interpret | `intent`, `intent_confidence` | 의도 분류 결과 |
| interpret | `query_category`, `preprocessed_input` | 전처리 결과 |
| interpret | `clarification_question`, `awaiting_clarification` | 명확화 상태 |
| reason | `phase` | 추론 단계 |
| reason | `hypotheses_count`, `candidate_tables_count` | 탐색 진행도 |
| reason | `knowledge_confirmed` | 확정된 지식 수 |
| reason | `generated_sql`, `validated_sql` | SQL 라이프사이클 |
| present | `status`, `error_message` | 실행 상태 |

#### 타임라인 출력 예시

```
[12] ■ node_end | classify_intent | 완료 | 1823ms | success
     state_changes:
       intent: UNKNOWN → DATA_QUERY
       intent_confidence: 0.0 → 0.92
       query_category: → "예금_신규"
```

#### 보고서 렌더링

`visualizer.py`의 `render_detail_table`에 `state_changes` 컬럼을 추가한다:

```
| Seq | Type | Node | Summary | State Changes | Duration |
|-----|------|------|---------|---------------|----------|
| 12 | ■ | classify_intent | 완료 | intent: UNKNOWN→DATA_QUERY, confidence: 0→0.92 | 1823ms |
```

---

## 5. 파일별 변경 계획

### 5.1 신규 파일 (2개)

| 파일 | 설명 | 라인 수 (추정) |
|------|------|-------------|
| `src/utils/tracker/callback_handler.py` | DataCopilotCallbackHandler | ~450 |
| `src/utils/tracker/dispatch.py` | dispatch_tracking_event + 이벤트 이름 상수 | ~60 |

### 5.2 수정 파일 (16개)

#### (A) 파이프라인 구조

| 파일 | 변경 내용 |
|------|----------|
| `src/agents/graph/pipeline.py` | `tracker` 파라미터 삭제, 데코레이터 래핑 삭제, 싱글턴 `_compiled_app` 도입 |
| `src/agents/graph/runner.py` | `DataCopilotCallbackHandler` 사용, `ainvoke(config={"callbacks": [handler]})`, `NODE_PROGRESS_MAP` 이동 후 import 변경 |

#### (B) 노드 — `adispatch_custom_event` 전환 (8개)

모든 노드 함수의 시그니처에 `config: RunnableConfig` 두 번째 파라미터를 추가한다.
LangGraph는 두 번째 파라미터가 있으면 자동으로 config를 주입한다.

| 파일 | 라인 | 변경 전 | 변경 후 |
|------|------|---------|---------|
| `intent_classifier.py` | 49, 95 | `classify_intent_node(state)` + `get_current_tracker()` | `classify_intent_node(state, config)` + `adispatch_custom_event("decision.intent", ...)` |
| `query_normalizer.py` | 43, 92 | `normalize_query_node(state)` + `get_current_tracker()` | `normalize_query_node(state, config)` + `adispatch_custom_event("decision.normalization", ...)` |
| `confidence_evaluator.py` | 46, 137 | `confidence_evaluator_node(state)` + `get_current_tracker()` | `confidence_evaluator_node(state, config)` + `adispatch_custom_event("decision.readiness", ...)` |
| `context_explorer.py` | 217, 156/180/332/515/518 | `context_explorer_node(state)` + 5곳 `get_current_tracker()` | `context_explorer_node(state, config)` + 5곳 `adispatch_custom_event(...)` |
| `sql_executor.py` | 39, 84 | `execute_sql_node(state)` + `get_current_tracker()` | `execute_sql_node(state, config)` + `adispatch_custom_event("context.sql_executed", ...)` |

> **중요**: 조건부 엣지 함수 (`should_clarify`, `route_after_validate` 등)는 순수 라우팅 함수이므로 `config` 파라미터를 **추가하지 않는다**.

#### 노드 변경 코드 예시

```python
# 변경 전: intent_classifier.py
async def classify_intent_node(
    state: PipelineState,
) -> dict:
    # ... 분류 로직 ...
    from src.utils.tracker import get_current_tracker
    tracker = get_current_tracker()
    if tracker and tracker.enabled:
        tracker.track_decision(
            node="intent_classifier",
            decision_type="intent_classification",
            chosen=result.intent.value,
            confidence=result.confidence,
            reason=f"category={result.category}, {result.reason}",
        )
    return {...}


# 변경 후: intent_classifier.py
from langchain_core.callbacks.manager import adispatch_custom_event
from langchain_core.runnables import RunnableConfig

async def classify_intent_node(
    state: PipelineState,
    config: RunnableConfig,
) -> dict:
    # ... 분류 로직 ...
    await adispatch_custom_event(
        "decision.intent",
        {
            "node": "intent_classifier",
            "decision_type": "intent_classification",
            "chosen": result.intent.value,
            "confidence": result.confidence,
            "reason": f"category={result.category}, {result.reason}",
        },
        config=config,
    )
    return {...}
```

> **설계 포인트**: 노드 내부에서는 `config`를 명시적으로 전달한다. 이는 Python 3.10 호환성과 명시성을 보장한다.
> 핸들러가 비활성이거나 없으면 이벤트는 자동으로 무시된다 — `if tracker and tracker.enabled` 가드가 불필요해진다.

#### (C) 커넥터/유틸리티 — `dispatch_tracking_event` 전환 (4개)

커넥터와 유틸리티는 LangGraph 노드가 아니므로 `config`를 직접 받지 못한다.
`dispatch_tracking_event` 래퍼를 사용하여 contextvars에서 config를 자동 추출한다.

| 파일 | 라인 | 변경 내용 |
|------|------|----------|
| `client.py` | 154, 241 | `get_current_tracker().track_llm_call(...)` → `await dispatch_tracking_event("llm.call", {...})` |
| `qdrant_connector.py` | 155 | `get_current_tracker().track_context_retrieval(...)` → `await dispatch_tracking_event("context.embedding", {...})` |
| `reranker.py` | 375 | `get_current_tracker().track_context_retrieval(...)` → `await dispatch_tracking_event("context.reranked", {...})` |
| `tracker/__init__.py` | 54 | `record_prompt_variables()` → `await dispatch_tracking_event("llm.prompt_variables", {...})` |

#### 커넥터 변경 코드 예시

```python
# 변경 전: client.py
_tracker = get_current_tracker()
if _tracker and _tracker.enabled:
    _tracker.track_llm_call(
        node=get_current_node(),
        prompt_summary=_build_prompt_summary(system, messages),
        response_text=result.content[0].text[:1000],
        model=model,
        prompt_tokens=getattr(_usage, "input_tokens", 0),
        response_tokens=getattr(_usage, "output_tokens", 0),
        latency_ms=_elapsed,
    )


# 변경 후: client.py
from src.utils.tracker.dispatch import dispatch_tracking_event
from src.utils.tracker.context import get_current_node

await dispatch_tracking_event("llm.call", {
    "node": get_current_node(),
    "prompt_summary": _build_prompt_summary(system, messages),
    "response_text": result.content[0].text[:1000] if result.content else "",
    "model": model,
    "prompt_tokens": getattr(_usage, "input_tokens", 0),
    "response_tokens": getattr(_usage, "output_tokens", 0),
    "latency_ms": _elapsed,
})
```

> **핵심 차이**: `if tracker and tracker.enabled` 가드 제거. `dispatch_tracking_event`가 컨텍스트 밖에서 호출 시 자동으로 무시한다. 핸들러의 `_enabled` 플래그는 `on_custom_event` 진입 시 체크한다.

#### (D) 트래커 모듈

| 파일 | 변경 내용 |
|------|----------|
| `context.py` | `set_current_tracker`, `get_current_tracker` 삭제. `set_current_node`, `get_current_node`만 유지 |
| `evaluation.py` | `EvaluationTracker` 클래스 삭제. 모델 7개 + `BatchEvaluationTracker` 유지 |
| `__init__.py` | export 목록 갱신: `DataCopilotCallbackHandler` 추가, `EvaluationTracker` 제거, `get_current_tracker`/`set_current_tracker` 제거, `record_prompt_variables` 함수 제거 |
| `visualizer.py` | `render_detail_table`에 `state_changes` 컬럼 추가 |

#### (E) BatchEvaluationTracker 수정

```python
# 변경 전
def add_trace(self, tracker: EvaluationTracker) -> None:
    self._traces.append(tracker.trace)

# 변경 후
def add_trace(self, handler: DataCopilotCallbackHandler) -> None:
    self._traces.append(handler.trace)
```

시그니처만 변경, 내부 로직(`generate_summary`, `save`)은 `EvaluationTrace` 객체를 사용하므로 변경 없음.

### 5.3 변경 없는 파일 (2개)

| 파일 | 이유 |
|------|------|
| `trace_analyzer.py` | JSON dict 기반 분석 — 입력 포맷 변경 없음 |
| `visualizer.py` (Mermaid/Gantt) | dict 기반 렌더링 — `state_changes` 컬럼 추가만 |

### 5.4 삭제 대상

| 대상 | 이유 |
|------|------|
| `EvaluationTracker` 클래스 (evaluation.py 176-671) | `DataCopilotCallbackHandler`로 완전 대체 |
| `set_current_tracker` / `get_current_tracker` (context.py) | 더 이상 사용되지 않음 |
| `_current_tracker` ContextVar (context.py) | 더 이상 사용되지 않음 |
| `record_prompt_variables` 함수 (__init__.py) | `dispatch_tracking_event("llm.prompt_variables", ...)` 로 대체 |
| `tracker.inject()` 호출 (pipeline.py) | 콜백 핸들러가 대체 |
| `tracker.track(name)` 데코레이터 (pipeline.py) | `on_chain_start/end`가 대체 |
| `NODE_PROGRESS_MAP` (runner.py) | `callback_handler.py`로 이동 |
| `_emit_progress` 함수 (runner.py) | 핸들러 내부 `_emit_progress`로 이동 |
| 모든 `get_current_tracker()` import (10개 파일) | `adispatch_custom_event` 또는 `dispatch_tracking_event`로 대체 |

---

## 6. pipeline.py 싱글턴 설계

```python
# src/agents/graph/pipeline.py — 변경 후

from langgraph.graph import StateGraph

_compiled_app: Any = None


def build_pipeline() -> StateGraph:
    """3계층 단일 LangGraph 파이프라인을 구성한다.

    tracker 파라미터 삭제 — 노드를 있는 그대로 등록한다.
    계측은 ainvoke 시점에 주입되는 콜백 핸들러가 담당한다.
    """
    workflow = StateGraph(PipelineState)

    # ── Interpret 계층 ──
    workflow.add_node("preprocess", preprocess_node)
    workflow.add_node("resolve_history", resolve_history_node)
    workflow.add_node("classify_intent", classify_intent_node)
    workflow.add_node("normalize_query", normalize_query_node)
    workflow.add_node("clarify", clarify_node)

    # ── Reason 계층 ──
    workflow.add_node("planner", planner_node)
    workflow.add_node("context_explorer", context_explorer_node)
    # ... 동일 패턴 ...

    # ── 엣지 (기존과 동일) ──
    workflow.add_edge("preprocess", "resolve_history")
    # ...

    return workflow


def get_compiled_app() -> Any:
    """컴파일된 LangGraph 앱 싱글턴을 반환한다.

    첫 호출 시 빌드+컴파일, 이후 캐싱된 인스턴스 반환.
    컴파일된 그래프는 불변(immutable)이므로 동시 요청에 안전하다.
    """
    global _compiled_app
    if _compiled_app is None:
        _compiled_app = build_pipeline().compile()
    return _compiled_app
```

### runner.py 변경

```python
# src/agents/graph/runner.py — 변경 후

from src.utils.tracker.callback_handler import (
    DataCopilotCallbackHandler,
)
from src.agents.graph.pipeline import get_compiled_app


async def run_pipeline(
    user_input: str,
    session_id: str | None = None,
    conversation_history: list[dict[str, str]] | None = None,
    handler: DataCopilotCallbackHandler | None = None,
    *,
    clarification_state: dict[str, Any] | None = None,
    on_event: OnEventCallback | None = None,
) -> PipelineResult:
    if not session_id:
        session_id = str(uuid.uuid4())

    if handler is None:
        handler = DataCopilotCallbackHandler(
            run_id=session_id,
            on_event=on_event,
        )

    handler.start_run(
        user_input=user_input,
        session_id=session_id,
    )

    manager = get_connector_manager()
    await manager.connect_all()

    app = get_compiled_app()  # 싱글턴

    initial_state = PipelineState(...)

    result = await app.ainvoke(
        initial_state,
        config={"callbacks": [handler]},  # 요청별 핸들러 주입
    )

    _record_sql_metrics(handler, result)
    _record_run_end(handler, result, response)
    handler.save()

    return PipelineResult(...)
```

---

## 7. 요구사항 매핑 검증

| # | 요구사항 | 구현 방식 | 현재 | 변경 후 |
|---|---------|----------|------|---------|
| 1 | 노드 시작/종료 기록 추적 | `on_chain_start/end` | ✅ `track()` 데코레이터 | ✅ 표준 콜백 |
| 2 | 노드 흐름 로깅 | `timeline` + `node_path` | ✅ | ✅ 동일 |
| 3 | LLM 호출 기록 (in/out, 시각) | `on_custom_event("llm.call")` | ✅ `track_llm_call` | ✅ 커스텀 이벤트 |
| 4 | LLM output 파싱 및 의도 트래킹 | `on_custom_event("decision.*")` | ✅ `track_decision` | ✅ 커스텀 이벤트 |
| 5 | Tool 호출 기록 (in/out, 시각) | `on_custom_event("context.*")` | ✅ `track_context_retrieval` | ✅ 커스텀 이벤트 |
| 6 | **State 변화 기록 추적** | `on_chain_start/end` diff | ❌ **미구현** | ✅ **신규** |
| 7 | 개별 노드 추가 트래킹 | `adispatch_custom_event` | ✅ 직접 호출 | ✅ 표준 이벤트 |
| 8 | WebSocket 진행상황 전파 | `_emit_progress` in handler | ✅ `_emit_node_start/done` | ✅ 핸들러 내장 |
| 9-1 | 통합 타임라인 (순번+부모-자식+Mermaid) | `_append_timeline` + visualizer | ✅ | ✅ 동일 출력 |
| 9-2 | 타임라인에 참조정보/의사결정/state변화/SQL 포함 | 모든 이벤트가 timeline에 기록 | ✅ (state변화 제외) | ✅ **완전 충족** |
| 9-3 | 자동 보고서 생성 | `handler.save(with_report=True)` | ✅ | ✅ 동일 |

---

## 8. 보안/구조 검증

### 8.1 보안 점검

| 항목 | 검증 결과 |
|------|----------|
| 이벤트 페이로드에 PII 포함 여부 | `prompt_summary`에 사용자 질의 일부 포함 — 현재와 동일 수준, 로그 마스킹 정책 준수 |
| SQL 평문 노출 | `sql.recorded` 이벤트에 `generated_sql` 포함 — 현재와 동일, 트레이스 파일은 내부 분석용 |
| API 키 노출 | 이벤트 페이로드에 API 키 미포함 확인 |
| State 스냅샷 범위 | `_snapshot_state`에서 `password`, `api_key` 등 민감 키 제외 확인 |
| RuntimeError 노출 | `dispatch_tracking_event`에서 예외 조용히 처리 — 사용자에게 미노출 |

### 8.2 구조 검증

| 항목 | 검증 결과 |
|------|----------|
| 레이어 역전 | `callback_handler.py`는 `evaluation.py` 모델만 import — 역전 없음 |
| 순환 참조 | `dispatch.py` → `langchain_core`만 import — 순환 없음 |
| 싱글턴 안전성 | `CompiledGraph`는 불변, 동시 요청에 안전 (LangGraph 문서 확인) |
| 요청 격리 | 핸들러 인스턴스가 요청별 상태 보유, 공유 상태 없음 |
| 메모리 누수 | `_run_to_node`, `_node_timers` 등 dict가 `on_chain_end/error`에서 정리됨 확인 |
| 병렬 노드 | asyncio 태스크별 contextvars 복사 — `set_current_node` 경합 없음 |

### 8.3 가독성 검증

| 항목 | 판정 |
|------|------|
| `if tracker and tracker.enabled:` 반복 제거 | ✅ 6줄→1줄 (`adispatch_custom_event` 한 줄) |
| 지연 import 패턴 제거 | ✅ `from src.utils.tracker import get_current_tracker` 삭제 |
| 노드 시그니처 일관성 | ✅ 모든 노드가 `(state, config)` 통일 |
| 이벤트 이름 규칙성 | ✅ `{도메인}.{행위}` 일관 네이밍 |

### 8.4 누락 점검 (3회차 비판적 검토)

| 점검 항목 | 결과 |
|-----------|------|
| `_record_sql_metrics` (runner.py) 호출부 | ✅ `handler` 인스턴스의 `trace` 직접 접근 또는 `dispatch_tracking_event("sql.recorded", ...)` — runner.py는 노드 밖이므로 `dispatch_tracking_event` 사용 불가. **handler 메서드 직접 호출로 변경 필요** |
| `_record_run_end` (runner.py) 호출부 | ✅ `handler.end_run(...)` 직접 호출 — 이벤트 불필요 |
| `BatchEvaluationTracker.add_trace` 시그니처 | ✅ `handler.trace` 접근으로 충분 |
| `_emit_progress`에서 `runner.NODE_PROGRESS_MAP` import | ✅ `callback_handler.py`로 이동하여 순환 참조 제거 |
| `context.py`의 `_current_node` 유지 이유 | ✅ `client.py`에서 `get_current_node()` 사용 — `on_chain_start`에서 `set_current_node` 호출 |
| `record_prompt_variables` 동기 함수 → 비동기 변환 | ✅ `dispatch_tracking_event`는 async — 호출부(`context_explorer.py`)가 이미 async이므로 `await` 추가만 |
| `_wrap_sync` 대응 | ✅ 동기 노드도 `on_chain_start/end`에서 동일하게 처리됨 — `AsyncCallbackHandler.on_chain_start`가 호출되며 LangGraph가 이를 올바르게 처리 |
| 조건부 엣지 함수가 `on_chain_start`를 트리거하는지 | ✅ 조건부 엣지는 chain이 아님 — `on_chain_start` 미발생 |
| 기존 테스트 코드 영향 | 아래 섹션 참조 |

#### runner.py에서 SQL 메트릭 기록 해결

`runner.py`의 `_record_sql_metrics`는 `ainvoke` 완료 후(그래프 밖)에서 호출된다.
이 시점에서 `adispatch_custom_event`는 사용 불가(RuntimeError).

**해결**: handler에 `record_sql` 메서드를 직접 노출한다:

```python
# DataCopilotCallbackHandler에 추가
def record_sql(self, data: dict[str, Any]) -> None:
    """그래프 실행 외부에서 SQL 메트릭을 직접 기록한다."""
    self._record_sql(data)

def record_eval_result(
    self, passed: bool, errors: list[str] | None = None,
) -> None:
    """그래프 실행 외부에서 평가 결과를 직접 기록한다."""
    self._record_eval({"passed": passed, "errors": errors or []})
```

이는 `start_run`/`end_run`과 동일한 패턴으로, 그래프 실행 전후의 메타데이터를 기록하는 용도이다.

---

## 9. visualizer.py 확장: State 변화 렌더링

### 9.1 render_detail_table 확장

```python
# 변경 전: 컬럼
| Seq | Type | Node | Summary | Detail | Duration | Status |

# 변경 후: 컬럼 추가
| Seq | Type | Node | Summary | State Changes | Detail | Duration | Status |
```

`event_type == "node_end"`인 행에서 `detail.state_changes`를 파싱하여 표시:

```python
def _format_state_changes(
    detail: dict[str, Any],
) -> str:
    """state_changes를 사람이 읽기 쉬운 텍스트로 변환."""
    changes = detail.get("state_changes", [])
    if not changes:
        return "-"
    parts = []
    for ch in changes[:5]:  # 최대 5개
        before = ch.get("before", "∅")
        after = ch.get("after", "")
        parts.append(f"`{ch['field']}`: {before} → {after}")
    return "<br>".join(parts)
```

### 9.2 Mermaid 시퀀스 다이어그램 확장

State 변화를 `Note` 블록으로 추가:

```mermaid
Note over classifyintent: ⚡ intent: UNKNOWN → DATA_QUERY (92%)
```

---

## 10. 테스트 전략

### 10.1 핸들러 단위 테스트

```python
# tests/auto/unit/test_callback_handler.py

class TestDataCopilotCallbackHandler:
    """콜백 핸들러의 이벤트 수신 및 기록을 검증한다."""

    async def test_node_boundary_tracking(self):
        """on_chain_start/end가 노드 시작/종료를 올바르게 기록한다."""

    async def test_custom_event_decision(self):
        """decision.* 이벤트가 DecisionRecord로 기록된다."""

    async def test_custom_event_context_retrieval(self):
        """context.* 이벤트가 ContextRetrievalRecord로 기록된다."""

    async def test_custom_event_llm_call(self):
        """llm.call 이벤트가 LLMCallRecord로 기록되고 통계가 갱신된다."""

    async def test_prompt_variables_merge(self):
        """llm.prompt_variables가 직전 LLM 기록에 병합된다."""

    async def test_state_diff_computation(self):
        """on_chain_end에서 state 변화가 올바르게 계산된다."""

    async def test_websocket_progress(self):
        """NODE_PROGRESS_MAP에 있는 노드만 progress 이벤트를 전송한다."""

    async def test_explore_count_increment(self):
        """context_explorer 반복 시 카운터가 증가한다."""

    async def test_non_node_chain_ignored(self):
        """langgraph_node 메타데이터가 없는 체인 이벤트는 무시된다."""

    async def test_chain_error_handling(self):
        """on_chain_error가 에러 상태를 올바르게 기록한다."""

    async def test_save_json_and_report(self):
        """save()가 JSON과 Markdown 보고서를 생성한다."""

    async def test_trace_output_compatibility(self):
        """생성된 EvaluationTrace가 기존 trace_analyzer, visualizer와 호환된다."""
```

### 10.2 통합 테스트

```python
# tests/auto/e2e/test_callback_integration.py

class TestCallbackIntegration:
    """실제 LangGraph 그래프에서 콜백 핸들러가 올바르게 동작하는지 검증한다."""

    async def test_singleton_graph_with_callback(self):
        """싱글턴 그래프 + 요청별 핸들러 주입이 정상 동작한다."""

    async def test_custom_event_from_node(self):
        """노드 내부 adispatch_custom_event가 핸들러에 도달한다."""

    async def test_custom_event_from_connector(self):
        """커넥터의 dispatch_tracking_event가 핸들러에 도달한다."""

    async def test_concurrent_requests(self):
        """동시 요청이 서로의 트레이스를 오염시키지 않는다."""

    async def test_state_diff_in_report(self):
        """보고서에 state 변화가 포함된다."""
```

### 10.3 dispatch_tracking_event 안전 테스트

```python
# tests/auto/unit/test_dispatch_safety.py

class TestDispatchSafety:

    async def test_outside_graph_context(self):
        """그래프 밖에서 호출 시 RuntimeError 없이 조용히 무시된다."""

    async def test_inside_graph_context(self):
        """그래프 내에서 호출 시 핸들러에 정상 전달된다."""
```

### 10.4 회귀 테스트

기존 트레이스 JSON 파일을 사용하여 `trace_analyzer.py`와 `visualizer.py`의 출력이 변경 전후 동일한지 확인한다.

---

## 11. 마이그레이션 순서

전체 변경을 **4단계**로 나누어 단계마다 테스트 통과를 확인한다.

### Step 1: 신규 파일 추가 (기존 코드 미변경)

1. `src/utils/tracker/callback_handler.py` 작성
2. `src/utils/tracker/dispatch.py` 작성
3. `tests/auto/unit/test_callback_handler.py` 작성 및 통과 확인

### Step 2: pipeline.py 싱글턴 전환

1. `pipeline.py`에서 `tracker` 파라미터 삭제, `get_compiled_app()` 추가
2. `runner.py`에서 `DataCopilotCallbackHandler` 사용으로 전환
3. `NODE_PROGRESS_MAP` → `callback_handler.py`로 이동
4. 기존 `EvaluationTracker` import는 아직 유지 (BatchEvaluationTracker 등)
5. E2E 테스트 통과 확인

### Step 3: 호출부 전환 (14개소)

1. 노드 8개: 시그니처 변경 + `adispatch_custom_event` 전환
2. 유틸리티 4개: `dispatch_tracking_event` 전환
3. runner.py의 `_record_sql_metrics` → `handler.record_sql()` 전환
4. 각 파일별 테스트 통과 확인

### Step 4: 삭제 및 정리

1. `EvaluationTracker` 클래스 삭제
2. `set_current_tracker` / `get_current_tracker` 삭제
3. `record_prompt_variables` 함수 삭제
4. `__init__.py` export 정리
5. `visualizer.py` state_changes 컬럼 추가
6. 전체 테스트 스위트 통과 확인
7. 기존 트레이스 파일과 출력 호환성 확인

---

## 12. 파일 변경 요약

| 구분 | 파일 수 | 파일 목록 |
|------|--------|----------|
| **신규** | 2 | `callback_handler.py`, `dispatch.py` |
| **수정** | 16 | `pipeline.py`, `runner.py`, `intent_classifier.py`, `query_normalizer.py`, `confidence_evaluator.py`, `context_explorer.py`, `sql_executor.py`, `client.py`, `qdrant_connector.py`, `reranker.py`, `evaluation.py`, `context.py`, `__init__.py`, `visualizer.py`, `BatchEvaluationTracker` (evaluation.py 내), `devtools/evaluation/evaluator.py` |
| **삭제 (클래스/함수)** | 5 | `EvaluationTracker` 클래스, `set_current_tracker`, `get_current_tracker`, `_current_tracker` ContextVar, `record_prompt_variables` |
| **변경 없음** | 2 | `trace_analyzer.py`, `visualizer.py` (Mermaid/Gantt 로직) |
| **테스트** | 3 | `test_callback_handler.py`, `test_callback_integration.py`, `test_dispatch_safety.py` |

---

## 13. 보고서 구조 전면 개선

### 13.1 현재 보고서의 문제점

현재 `render_full_report`는 `Header → Sequence Diagram → Gantt → Detail Table` 4섹션으로 구성된다.
실제 보고서(87초, 100+ 이벤트)를 분석한 결과 다음 문제가 확인되었다:

| 문제 | 상세 |
|------|------|
| Sequence Diagram 노이즈 | `search_table_meta` 12회 호출이 다이어그램의 1/3 차지. 개별 호출 상세는 불필요 |
| Gantt 사이클 미구분 | 같은 노드 3~4회 반복 시 어떤 것이 1차/2차인지 구분 불가 |
| Detail Table 평면적 | `parent_seq`가 있는데 계층(들여쓰기)으로 표현 안 됨. 87행 스크롤 필요 |
| 핵심 정보 부재 | 왜 실패했는지, 어떤 테이블이 선택되었는지, 생성 SQL이 무엇인지 헤더에 없음 |
| decisions 미표시 | `decisions`가 보고서에 독립 섹션으로 표시되지 않음 (Detail Table에 묻혀 있음) |
| context_retrievals 미표시 | 참조 정보가 보고서에 독립 섹션으로 표시되지 않음 |
| trace_analyzer 분리 | 같은 트레이스에 대해 보고서와 분석을 별도 실행해야 함 |
| State 변화 없음 | 요구사항 핵심인데 아예 누락 |

### 13.2 새 보고서 구조 (7섹션)

```
1. Executive Summary     — 규칙 기반 자연어 요약 (무슨 일이 일어났고 어떻게 끝났는지)
2. Decision Trail        — 핵심 의사결정만 phase별 시간순 나열 (판단 재료 포함)
3. Referenced Information — 소스별 그룹핑된 참조 정보 (테이블 메타, 벡터, 리랭킹, 이력)
4. State Evolution       — 노드별 state 변화 compact 테이블 (①②③ 사이클 표기)
5. Node Flow             — 요약 다이어그램 (30+이벤트→Flowchart, 미만→Sequence)
6. Performance           — 사이클별 Gantt + LLM 비용 분석 테이블
7. Automated Findings    — trace_analyzer 결과 통합
[Appendix] Detailed Timeline — 부모-자식 들여쓰기 적용된 상세 테이블
```

### 13.3 섹션별 렌더링 함수 설계

#### (1) Executive Summary — `render_executive_summary`

LLM 호출 없이 트레이스 데이터만으로 템플릿 기반 생성:

```python
def render_executive_summary(trace_data: dict[str, Any]) -> str:
    """트레이스 요약을 규칙 기반으로 생성한다."""
    # 입력: decisions, node_path, sql, context_retrievals, error_message
    # 출력 예시:
    # **질의**: 이번년도 예금신규 top 10 지점 알려줘
    # **결과**: ❌ 실패 (3회 재탐색 후 최대 시도 횟수 초과)
    # **소요**: 87.1초 | LLM 21회, 90,066토큰
    #
    # | 단계 | 결과 |
    # | 의도 분류 | DATA_ANALYSIS (95%) |
    # | 테이블 선택 | DEP201P, COM001M |
    # | SQL 생성 | 2회 시도, 모두 검증 실패 |
    # | 실패 원인 | SQL 검증 반복 실패 → 재계획 한도 초과 |
```

#### (2) Decision Trail — `render_decision_trail`

`decisions` 리스트를 phase별 그룹핑하여 테이블로 렌더링:
- `node_path`에서 `recovery_planner` 출현을 기준으로 사이클 경계 판단
- `detail` 필드가 있으면 판단 재료(확정 지식, 미확정 지식, 후보 테이블)를 펼쳐 표시
- 없으면 `reason` 한 줄만 표시

#### (3) Referenced Information — `render_referenced_info`

`context_retrievals`를 `source` 기준으로 그룹핑:
- 테이블 메타 (ES), 벡터 검색 (Qdrant), 리랭킹, 유사 보고서/이력
- 각 그룹에 사이클 번호, 소계 표시
- 합계 행: 총 검색 횟수, 성공/실패, 총 소요시간

#### (4) State Evolution — `render_state_evolution`

`timeline`에서 `node_end` 이벤트의 `detail.state_changes`를 추출:
- 같은 노드 반복 시 ①②③ 접미사 자동 부여
- 변화 없는 노드는 "(변화 없음)" 표시
- `field: before → after` 형태로 핵심 변경만 표시

#### (5) Node Flow — `render_node_flow`

이벤트 30개 이상이면 Mermaid flowchart, 미만이면 기존 sequence diagram:

```python
def render_node_flow(timeline: list[dict], node_path: list[str]) -> str:
    if len(timeline) < 30:
        return render_mermaid(timeline)  # 기존 sequence diagram
    return _render_flowchart(timeline, node_path)  # 신규 flowchart
```

Flowchart는 사이클별 subgraph로 그룹핑:
- 각 노드에 소요시간 + 핵심 결과 요약 포함
- ⚠️❌ 마커로 문제 지점 표시

#### (6) Performance — `render_performance`

- Gantt: section을 사이클별로 그룹핑, 같은 노드에 ①②③ 접미사
- LLM 비용 분석: 노드별 호출 수, 토큰, 소요시간, 비중(%) 테이블

#### (7) Automated Findings — `render_findings`

`trace_analyzer.analyze_trace()` 결과를 보고서에 직접 통합:
- 심각도별 아이콘 (🔴 CRITICAL, 🟡 WARNING, 🔵 INFO)
- `final_status`가 clarification/casual_response일 때 SQL 관련 경고 억제

#### Appendix: Detailed Timeline — `render_detail_table` 개선

기존 Detail Table에 두 가지 개선:
- `parent_seq` 기반 들여쓰기 (노드 내부 이벤트에 2칸 들여쓰기)
- `state_changes` 컬럼 추가 (node_end 행에서만 표시)

### 13.4 Decision Trail의 판단 재료 확장

`decision.*` 이벤트 페이로드에 `detail` 필드를 추가한다:

```python
# confidence_evaluator에서 dispatch 시
await adispatch_custom_event("decision.readiness", {
    "node": "confidence_evaluator",
    "decision_type": "readiness_verdict",
    "chosen": verdict.value,
    "confidence": score,
    "reason": f"knowledge={ki_confirmed}/{ki_total}, tables={ct_count}",
    "detail": {  # 신규
        "confirmed_knowledge": [...],
        "unresolved_knowledge": [...],
        "candidate_tables": [...],
    },
}, config=config)
```

`DecisionRecord` 모델에 `detail: dict[str, Any] = Field(default_factory=dict)` 필드 추가.

---

## 14. 기대 효과

| 항목 | Before | After |
|------|--------|-------|
| 그래프 빌드 | 매 요청마다 빌드+컴파일 | **1회** 빌드, 재사용 |
| LangGraph 표준 준수 | 커스텀 데코레이터 | **AsyncCallbackHandler** 표준 |
| contextvars 사용 | 2개 (`_current_tracker` + `_current_node`) | **1개** (`_current_node`만) |
| tracker 가드 코드 | 14곳 `if tracker and tracker.enabled:` | **0곳** (핸들러 내부에서 1회 체크) |
| state 변화 추적 | ❌ 미구현 | ✅ **자동 diff** |
| 호출부 코드량 | 6~8줄/곳 (import + guard + method call) | **1~3줄/곳** (`adispatch_custom_event` 한 줄) |
| 테스트 용이성 | tracker mock 필요 | 핸들러 없이 노드 직접 호출 가능 |
