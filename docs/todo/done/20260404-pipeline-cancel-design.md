# 파이프라인 실행 중단(Cancel) 구현 상세 설계

> 작성일: 2026-04-04
> 최종 갱신: 2026-04-06
> 상태: 구현 준비 완료 (설계 검토 반영 완료)
> 리서치 원문: `docs/research/20260404-langgraph-cancel-abort-pattern.md`
> 설계 검토 보고서: `docs/reviews/design/20260406-pipeline-cancel-design-review-report.md`

---

## 1. 문제 정의

사용자가 웹 UI에서 중단 버튼을 클릭해도 파이프라인이 계속 실행된다.
현재 `runner.py`에서 `await app.ainvoke()`로 완료까지 블로킹 대기하며,
Task 래핑도 중단 엔드포인트도 없다.

## 2. 리서치 결론: `asyncio.Task.cancel()` 직접 사용은 위험

LangGraph에 미해결 버그가 다수 존재한다 (2026-04 기준, 3건 모두 OPEN):

| 버그 | GitHub Issue | 심각도 | 영향 |
|------|-------------|--------|------|
| 서브그래프에 CancelledError 미전파 | [#5682](https://github.com/langchain-ai/langgraph/issues/5682) | 높음 | 서브그래프가 중단 후에도 계속 실행 |
| ToolNode가 CancelledError 미포착 | [#6726](https://github.com/langchain-ai/langgraph/issues/6726) | 높음 | INVALID_CHAT_HISTORY → 다음 실행 파손 |
| AsyncPregelLoop 클린업 중 2차 예외 | [#6950](https://github.com/langchain-ai/langgraph/issues/6950) | 중간 | 리소스 누수 |

**공식적으로 "사용자 주도 중단" 패턴은 아직 제공되지 않는다** (Discussion [#2930](https://github.com/langchain-ai/langgraph/discussions/2930)).
Python용 cancel 가이드 없음 (JS만 `AbortController` 존재). `runs.cancel()`은 self-hosted 404.

## 3. 선택 방식: 앱 레벨 취소 플래그 (REST 전용)

### 3.1 핵심 메커니즘

Redis에 `cancel:{session_id}` 플래그를 저장하고,
**노드 진입 시마다** 이를 확인하여 그래프를 정상 종료(END)로 라우팅한다.

```
[사용자: 중단 클릭]
    ↓
[프론트엔드] → POST /api/sessions/{session_id}/cancel
    ↓
[REST API] → Redis SET cancel:{session_id} = {turn_id}
    ↓
[다음 노드 진입 시] → check_cancel(session_id, turn_id) → True
    ↓
[해당 노드] → CANCELLED 상태 반환 → result_finalizer → error_end → END
```

### 3.2 REST 전용 cancel (WebSocket cancel 제외)

> **설계 판단 (CR-01 반영)**: `websocket_endpoint`는 `while True: await receive_text() → await _run_ws_pipeline()` 순차 구조이므로, 파이프라인 실행 중에는 `receive_text()` 루프로 돌아가지 않는다. WebSocket cancel 메시지를 수신할 수 없다.
>
> `asyncio.create_task` 분리는 동시성 버그 위험이 높으므로 채택하지 않는다.
> 프론트엔드가 cancel 버튼 클릭 시 별도 REST 요청으로 처리하면 간단하고 안전하다.

**결론**: cancel은 `POST /api/sessions/{session_id}/cancel` REST 엔드포인트 **전용**.
WebSocket `{"type": "cancel"}` 메시지 처리는 구현하지 않는다.

### 3.3 turn_id 기반 경쟁 상태 방어

> **설계 판단 (CR-03 반영)**: 동일 세션에서 "이전 질의 cancel + 새 질의 제출"이 동시 도착하면, 새 질의가 의도치 않게 취소될 수 있다.

cancel 플래그에 `turn_id`를 포함하여 현재 턴만 취소되도록 격리한다:

```
Redis key:   cancel:{session_id}
Redis value: {turn_id}     ← 취소 대상 턴
TTL:         300초 (안전망)
```

`check_cancel(session_id, turn_id)` 호출 시, 저장된 turn_id가 현재 turn_id와 일치하는 경우만 True.

### 3.4 장점 / 단점

| 장점 | 단점 |
|------|------|
| LangGraph 내부 버그(#5682, #6726, #6950)와 무관 | 노드 경계에서만 확인 가능 |
| 체크포인터에 정상 종료 상태로 안전 저장 | 현재 노드의 LLM 호출 완료까지 최대 30초 지연 |
| 폐쇄망 완전 호환 | - |
| 모든 LangGraph 버전에서 동작 | - |
| REST 전용으로 WebSocket 동시성 문제 회피 | - |

### 3.5 기각된 대안

| 대안 | 기각 이유 |
|------|----------|
| `asyncio.Task.cancel()` 직접 사용 | #5682 서브그래프 미전파, #6726 INVALID_CHAT_HISTORY |
| LangGraph SDK `runs.cancel()` | 폐쇄망 미적용, self-hosted 404 오류 |
| `interrupt_before` 모든 노드 | 매 노드마다 체크포인트 저장 → 성능 저하 |
| WebSocket cancel 메시지 | 단일 루프 블로킹으로 수신 불가 (CR-01) |
| WebSocket Task 분리 (asyncio.create_task) | 동시성 버그 위험, edge case 다수 |

---

## 4. 변경 범위

### 4.1 파일 목록

| 구분 | 파일 | 변경 내용 | 신규/수정 |
|------|------|----------|----------|
| Enum | `src/models/enums.py` | `QueryStatus.CANCELLED`, `FinalStatus.CANCELLED` 추가 | 수정 |
| 취소 스토어 | `src/services/cancel_store.py` | `CancelStore` 프로토콜 + `Redis`/`Memory` 구현 | **신규** |
| 취소 유틸 | `src/agents/graph/cancel.py` | `check_cancel()`, `make_cancel_updates()`, 싱글턴 관리 | **신규** |
| 라우팅 | `src/agents/graph/pipeline.py` | 라우팅 함수 4개 + `_handle_error`에 취소 분기 추가 | 수정 |
| 노드 체크 | `src/agents/nodes/reason/context_retriever.py` | 시작부 취소 체크 (방식 B) | 수정 |
| 노드 체크 | `src/agents/nodes/reason/readiness_gate.py` | 시작부 취소 체크 (방식 B) | 수정 |
| 노드 체크 | `src/agents/nodes/reason/sql_generator.py` | 시작부 취소 체크 (방식 B) | 수정 |
| 노드 체크 | `src/agents/nodes/present/sql_executor.py` | 시작부 취소 체크 (방식 B, CR-02) | 수정 |
| 결과 처리 | `src/agents/nodes/reason/result_finalizer.py` | `CANCELLED` 분기 + 부분결과 메시지 | 수정 |
| 실행기 | `src/agents/graph/runner.py` | cancel 상태 감지 + 플래그 정리 순서 수정 | 수정 |
| API | `src/routers/sessions.py` | `POST /api/sessions/{session_id}/cancel` 엔드포인트 | 수정 |
| 서버 | `src/main.py` | lifespan에서 CancelStore 초기화 | 수정 |
| 응답 | `src/main.py` | stream end 메시지에 status 필드 추가 (CR-07) | 수정 |
| 응답모델 | `src/agents/models/response.py` | PipelineResult.cancelled 필드 추가 | 수정 |
| 테스트 | `tests/conftest.py` | `reset_cancel_store` autouse fixture 추가 | 수정 |

### 4.2 변경하지 않는 파일

| 파일 | 이유 |
|------|------|
| `state.py` (PipelineState) | 새 필드 불필요. `QueryStatus.CANCELLED` + `FinalStatus.CANCELLED`로 충분 |
| `clarification_handler.py` | interrupt 대기 중 cancel은 §6에서 별도 처리 |
| `checkpointer.py` | CANCELLED는 정상 종료(END 도달)이므로 체크포인터 변경 불필요 |
| `config.py` | cancel TTL은 `cancel_store.py` 내부 상수로 충분 |

---

## 5. 구현 상세

### 5.1 Enum 추가 (`src/models/enums.py`)

```python
class QueryStatus(str, Enum):
    # ... 기존 값 ...
    COMPLETED = "completed"
    CANCELLED = "cancelled"       # ← 추가
    ERROR = "error"


class FinalStatus(str, Enum):
    PENDING = "pending"
    SUCCESS = "success"
    CANCELLED = "cancelled"       # ← 추가
    FAILURE = "failure"
```

### 5.2 취소 스토어 (`src/services/cancel_store.py` — 신규)

기존 `session/store.py`의 싱글턴 패턴을 따른다.

```python
"""파이프라인 취소 플래그 관리.

Redis 사용 환경에서는 RedisCancelStore,
개발/테스트 환경에서는 MemoryCancelStore를 사용한다.
SessionStore와 동일한 백엔드 선택 패턴을 따른다.

주의: MemoryCancelStore는 단일 워커(개발/테스트) 전용.
운영 환경(multi-worker)에서는 반드시 Redis를 사용해야 한다.
"""

from __future__ import annotations

from typing import Protocol


class CancelStore(Protocol):
    """취소 플래그 스토어 프로토콜."""

    async def set_cancel(self, session_id: str, turn_id: str) -> None: ...
    async def is_cancelled(self, session_id: str, turn_id: str) -> bool: ...
    async def clear_cancel(self, session_id: str) -> None: ...
    async def pop_cancel(self, session_id: str) -> str | None: ...


class MemoryCancelStore:
    """개발/테스트용 인메모리 취소 스토어.

    단일 워커에서만 동작한다. 운영 환경에서 사용 금지.
    """

    def __init__(self) -> None:
        self._flags: dict[str, str] = {}    # session_id → turn_id

    async def set_cancel(self, session_id: str, turn_id: str) -> None:
        self._flags[session_id] = turn_id

    async def is_cancelled(self, session_id: str, turn_id: str) -> bool:
        stored = self._flags.get(session_id)
        return stored is not None and stored == turn_id

    async def clear_cancel(self, session_id: str) -> None:
        self._flags.pop(session_id, None)

    async def pop_cancel(self, session_id: str) -> str | None:
        """플래그를 반환하고 삭제한다 (원자적 읽기+삭제)."""
        return self._flags.pop(session_id, None)


class RedisCancelStore:
    """운영용 Redis 취소 스토어.

    TTL(300초) 안전망으로 플래그 미정리 시 자동 만료.
    값으로 turn_id를 저장하여 턴 간 경쟁 상태를 방어한다.
    """

    _CANCEL_TTL = 300

    def __init__(self, redis_client) -> None:
        self._client = redis_client

    def _key(self, session_id: str) -> str:
        return f"cancel:{session_id}"

    async def set_cancel(self, session_id: str, turn_id: str) -> None:
        await self._client.set(
            self._key(session_id), turn_id, ex=self._CANCEL_TTL,
        )

    async def is_cancelled(self, session_id: str, turn_id: str) -> bool:
        stored = await self._client.get(self._key(session_id))
        if stored is None:
            return False
        if isinstance(stored, bytes):
            stored = stored.decode()
        return stored == turn_id

    async def clear_cancel(self, session_id: str) -> None:
        await self._client.delete(self._key(session_id))

    async def pop_cancel(self, session_id: str) -> str | None:
        """플래그를 반환하고 삭제한다 (GETDEL 사용)."""
        val = await self._client.getdel(self._key(session_id))
        if val is None:
            return None
        return val.decode() if isinstance(val, bytes) else val
```

**설계 포인트**:
- `set_cancel(session_id, turn_id)`: 값으로 `turn_id`를 저장 (CR-03)
- `is_cancelled(session_id, turn_id)`: 현재 턴과 일치하는 경우만 True
- `pop_cancel(session_id)`: 원자적 읽기+삭제 — runner에서 interrupt 판단용

### 5.3 취소 유틸 (`src/agents/graph/cancel.py` — 신규)

```python
"""노드/라우팅에서 사용하는 취소 체크 유틸.

싱글턴 CancelStore를 보유하며, lifespan에서 초기화한다.
CancelStore가 미설정이면 (테스트 등) 항상 False를 반환하여
기존 동작에 영향을 주지 않는다.
"""

from __future__ import annotations

from typing import Any

from src.agents.state.state import FinalStatus, Phase, QueryStatus
from src.services.cancel_store import CancelStore
from src.utils.logger import get_logger

logger = get_logger(__name__)

_cancel_store: CancelStore | None = None


def get_cancel_store() -> CancelStore | None:
    return _cancel_store


def set_cancel_store(store: CancelStore) -> None:
    global _cancel_store
    _cancel_store = store


def reset_cancel_store() -> None:
    """테스트에서 싱글턴을 초기화한다."""
    global _cancel_store
    _cancel_store = None


async def check_cancel(session_id: str, turn_id: str) -> bool:
    """세션+턴의 취소 플래그를 확인한다.

    CancelStore가 미설정이면 항상 False.
    Redis 장애 등 예외 발생 시 False 반환 (cancel 불가 > 파이프라인 실패).
    정확한 turn_id 매칭 후, 와일드카드("*") 폴백으로 하위 호환 지원.
    """
    if _cancel_store is None:
        return False
    try:
        if await _cancel_store.is_cancelled(session_id, turn_id):
            return True
        # "*" 와일드카드: turn_id를 모르는 클라이언트 하위 호환
        return await _cancel_store.is_cancelled(session_id, "*")
    except Exception:
        logger.warning("취소 플래그 확인 실패 — 무시하고 계속")
        return False


async def clear_cancel(session_id: str) -> None:
    """세션의 취소 플래그를 삭제한다."""
    if _cancel_store is not None:
        try:
            await _cancel_store.clear_cancel(session_id)
        except Exception:
            logger.warning("취소 플래그 삭제 실패", exc_info=True)


async def pop_cancel(session_id: str) -> str | None:
    """세션의 취소 플래그를 반환하고 삭제한다."""
    if _cancel_store is None:
        return None
    try:
        return await _cancel_store.pop_cancel(session_id)
    except Exception:
        logger.warning("취소 플래그 pop 실패", exc_info=True)
        return None


def make_cancel_updates(reason_state) -> dict[str, Any]:
    """취소 시 반환할 state 업데이트 dict.

    노드 시작부 체크(방식 B)에서 사용한다.
    reason을 deep copy하여 phase=DONE, final_status=CANCELLED로 설정.
    error_message를 설정하여 _route_after_result_finalizer에서
    error_end로 라우팅되도록 한다.
    """
    reason = reason_state.model_copy(deep=True)
    reason.phase = Phase.DONE
    reason.final_status = FinalStatus.CANCELLED
    reason.exploration_summary = "사용자 요청으로 중단되었습니다."
    return {
        "reason": reason,
        "status": QueryStatus.CANCELLED,
        "error_message": "사용자 요청으로 중단되었습니다.",
    }
```

**설계 포인트**:
- `check_cancel`에서 예외 catch → False 반환 (F1 반영: cancel 불가 > 파이프라인 실패)
- `pop_cancel`: runner에서 interrupt 대기 중 cancel 여부 판단용 원자적 연산
- `make_cancel_updates`에 `error_message` 포함 (F2 반영: `_route_after_result_finalizer`가 `error_end`로 라우팅)
- `reset_cancel_store()` 제공 (CR-06 반영: 테스트 격리)

### 5.4 취소 체크 방식: `with_cancel_check` 래퍼 + 라우팅 분기

> **갱신 (2026-04-06)**: 기존 Hybrid(방식 A+B) 인라인 체크를 `with_cancel_check` 래퍼로
> 중앙화하고, 모든 라우팅 함수에 CANCELLED 체크를 통일 적용하였다.
> 리뷰 보고서: `docs/reviews/code/20260406-cancel-abort-complexity-review-report.md`

#### 래퍼 패턴 (`pipeline.py` → `cancel.py`)

`pipeline.py`의 `add_node` 호출부에서 `with_cancel_check(node_fn)`으로 래핑하여
모든 노드 진입 시 cancel 플래그를 1회 체크한다. 노드 코드에 cancel 관련 코드를 넣지 않는다.

```python
# pipeline.py — build_pipeline() 내부
from src.agents.graph.cancel import with_cancel_check

workflow.add_node("intent_classifier", with_cancel_check(intent_classifier_node))
workflow.add_node("context_retriever", with_cancel_check(context_retriever_node))
# ... 11개 노드에 동일 적용
```

#### 래핑 대상 (11개) vs 제외 (5개)

| 래핑 대상 | 래핑 제외 | 사유 |
|-----------|----------|------|
| intent_classifier | reasoning_preparer | 전처리만, cancel 불필요 |
| normalize_query | result_finalizer | 내부에서 CANCELLED 처리 |
| context_retriever | clarification_handler | interrupt 노드 |
| context_interpreter | simple_responder | 비데이터 경량 |
| readiness_gate | error_end | 터미널 노드 |
| sql_generator | | |
| sql_validator | | |
| recovery_agent | | |
| execute_sql | | |
| analyze_data | | |
| format_response | | |

#### mid-node 체크 (예외적 노드 내부 유지, 3건)

| 위치 | 목적 | 절감 효과 |
|------|------|----------|
| `context_interpreter` Level1 루프 내 | 스텝별 LLM 호출 방지 | 스텝당 ~15초 |
| `sql_validator` Layer2b 전 | LLM 검증 호출 방지 | ~15초 |
| `analyzer` 서비스 콜백 | 분석 중 취소 감지 | 가변 |

#### 라우팅 함수 CANCELLED 분기 (5개)

| 라우팅 함수 | CANCELLED 시 경로 |
|-------------|------------------|
| `_route_after_intent_classifier` | `error_end` |
| `_route_after_normalize` | `error_end` |
| `_route_after_readiness_gate` | `conclude_failure` |
| `_route_after_recovery_agent` | `result_finalizer` |
| `_route_after_result_finalizer` | `error_end` |

최대 취소 지연: 현재 실행 중인 노드의 LLM 호출 시간 (`llm_default_timeout` 15초 ~ `llm_long_timeout` 30초).

### 5.5 mid-node 체크 (래퍼로 대체되지 않는 예외)

래퍼는 노드 진입 시 1회만 체크한다. 아래 3개 노드는 노드 내부에서 추가 체크가 필요하다.

```python
# context_interpreter — Level1 스텝별 LLM 호출 루프 내
for step in done_steps:
    if session_id and turn_id:
        from src.agents.graph.cancel import check_cancel
        if await check_cancel(session_id, turn_id):
            break

# sql_validator — Layer2b LLM 검증 전
if settings.validate_layer2b_enabled:
    if await check_cancel(state.session_id, state.turn_id):
        return make_cancel_updates(state.reason)

# analyzer — 서비스 콜백
async def _is_cancelled() -> bool:
    return await check_cancel(state.session_id, state.turn_id)
```

### 5.6 방식 A: 조건부 엣지 통합

```python
# pipeline.py — 라우팅 함수 3개에 취소 분기 추가

def _route_after_readiness_gate(state: PipelineState) -> str:
    if state.status == QueryStatus.CANCELLED:
        return "conclude_failure"         # → result_finalizer
    if state.pending_signals:
        return "clarification_handler"
    return _PHASE_TO_ROUTE.get(state.reason.phase, "conclude_failure")


def _route_after_recovery_agent(state: PipelineState) -> str:
    if state.status == QueryStatus.CANCELLED:
        return "result_finalizer"
    # ... 기존 로직


def _route_after_result_finalizer(state: PipelineState) -> str:
    """reason 계층 완료 후 라우팅.

    CANCELLED/error 시 error_end로 보내 END에 안전 도달.
    F2 반영: CANCELLED 상태에서 validated_sql이 남아있어도
    execute_sql로 빠지지 않도록 최우선 체크.
    """
    if state.status == QueryStatus.CANCELLED:
        return "error_end"                 # ← 추가 (F2 해소)
    if state.pending_signals:
        return "clarification_handler"
    if state.error_message:
        return "error_end"
    if state.reason.validated_sql:
        return "execute_sql"
    return "error_end"
```

**핵심**: `_route_after_result_finalizer`에서 `QueryStatus.CANCELLED`를 **최우선** 체크.
이 없으면 `validated_sql`이 남아있는 상태에서 CANCELLED여도 `execute_sql`로 라우팅되는 버그 발생.

### 5.7 result_finalizer CANCELLED 처리

```python
async def result_finalizer_node(state: PipelineState) -> dict:
    reason = state.reason.model_copy(deep=True)
    reason.phase = Phase.DONE
    updates: dict[str, Any] = {"reason": reason}

    # ── CANCELLED: 노드 체크에서 설정된 경우 ──
    if state.status == QueryStatus.CANCELLED:
        reason.final_status = FinalStatus.CANCELLED
        reason.exploration_summary = _build_cancel_summary(reason)
        updates["reason"] = reason
        updates["formatted_response"] = reason.exploration_summary
        updates["error_message"] = reason.exploration_summary
        # ↑ error_message 설정 필수: _route_after_result_finalizer가 error_end로 라우팅
        return updates

    # ── T5: CONFLICTED → AmbiguitySignal 생성 ──
    # ... 기존 로직
```

```python
def _build_cancel_summary(reason: ReasoningState) -> str:
    """취소 시 부분 결과를 포함한 사용자 메시지."""
    parts: list[str] = ["요청이 중단되었습니다."]

    selected_tables = [
        t.table_name for t in reason.explored_tables
        if t.selection_status == SelectionStatus.SELECTED
    ]
    if selected_tables:
        parts.append(f"탐색한 테이블: {', '.join(selected_tables)}")

    confirmed = reason.get_confirmed_knowledge()
    if confirmed:
        parts.append(f"확인된 정보 {len(confirmed)}건이 있습니다.")

    return " ".join(parts)
```

### 5.8 runner.py — cancel 상태 감지 + 플래그 정리

> **설계 판단 (CR-04 보완)**: interrupt 대기 중 cancel 후 새 질의 시, 이전 interrupt 상태를 무시하고 새 턴으로 시작해야 한다. `clear_cancel()` → `is_cancelled()` 순서 문제를 `pop_cancel()`로 해결.

```python
async def run_pipeline(user_input, session_id, ...):
    if not session_id:
        session_id = str(uuid.uuid4())

    # ── 0. 이전 취소 플래그 읽기+삭제 (원자적) ──
    # pop_cancel은 값을 반환하고 삭제한다.
    # 이 값을 interrupt 판단에 사용한 뒤 버린다.
    from src.agents.graph.cancel import pop_cancel
    previous_cancel_turn_id = await pop_cancel(session_id)

    # ── 1. sanitize ──
    sanitized = sanitize(user_input)
    if sanitized.is_error:
        return PipelineResult(response=sanitized.error_message)

    # ... (기존: query_id, 세션 인덱스, 핸들러 생성) ...

    app = get_compiled_app()

    # ── 2. interrupt 대기 감지 ──
    run_config = {"configurable": {"thread_id": session_id}, ...}
    is_interrupt_pending = False
    try:
        state_snapshot = await app.aget_state(run_config)
        is_interrupt_pending = bool(
            state_snapshot is not None and state_snapshot.next
        )
    except Exception as e:
        logger.debug("aget_state 조회 실패 (새 세션)", error=str(e))

    # ── 2a. interrupt 대기 중이었으나 cancel이 요청된 경우 → 새 턴 시작 ──
    if is_interrupt_pending and previous_cancel_turn_id is not None:
        logger.info(
            "이전 턴 cancel 감지 — interrupt 무시, 새 턴 시작",
            session_id=session_id,
            cancelled_turn_id=previous_cancel_turn_id,
        )
        is_interrupt_pending = False

    user_turn_saved = False

    try:
        if is_interrupt_pending:
            # ── 3a. interrupt 재개 ──
            raw_state = await app.ainvoke(
                Command(resume=sanitized.text),
                config=run_config,
            )
        else:
            # ── 3b. 새 턴 ──
            initial_state = PipelineState(
                user_input=user_input,
                original_query=user_input,
                preprocessed_input=sanitized.text,
                session_id=session_id,
                conversation_history=conversation_history or [],
                turn_id=str(uuid.uuid4()),
            )
            raw_state = await app.ainvoke(
                initial_state,
                config=run_config,
            )

        # ── 4. 이후 기존 로직 동일 ──
        # ...
```

**핵심 흐름**:
1. `pop_cancel(session_id)` — 이전 cancel 플래그를 읽고 삭제 (원자적)
2. interrupt 대기 중 + cancel 플래그 존재 → `is_interrupt_pending = False` → 새 턴 시작
3. interrupt 대기 중 + cancel 플래그 없음 → 정상 resume
4. 새 턴 시작 → 새 `turn_id` 생성 → 이전 cancel은 turn_id 불일치로 자연 무효화

### 5.9 CANCELLED 턴 저장 (`src/agents/graph/runner.py` — _build_result 보완)

runner.py의 `_build_result`에서 CANCELLED 상태를 `PipelineResult.cancelled`에 반영하고,
턴 저장 시 `status="cancelled"`로 기록한다.

```python
# runner.py — _build_result 내부
pipeline_result.cancelled = (
    result.get("status") == QueryStatus.CANCELLED
    or result.get("status") == "cancelled"
)

# runner.py — 턴 저장 시 status 분기
_status = "cancelled" if pipeline_result.cancelled else "success"
_assistant_turn_id = await save_turn(
    _pool,
    thread_id=session_id, role="assistant",
    content=pipeline_result.response,
    turn_type="cancel" if pipeline_result.cancelled else "normal",
    status=_status,
    # ... 기존 필드
)
```

### 5.10 테스트 fixture (`tests/conftest.py`)

```python
# conftest.py — cancel store 격리 fixture
import pytest
from src.agents.graph.cancel import reset_cancel_store

@pytest.fixture(autouse=True)
def _reset_cancel_store():
    """각 테스트 전후로 cancel store 싱글턴을 초기화한다."""
    reset_cancel_store()
    yield
    reset_cancel_store()
```

### 5.11 REST cancel 엔드포인트 (`src/routers/sessions.py`)

```python
from fastapi import Query as QueryParam

@router.post("/sessions/{session_id}/cancel")
async def cancel_pipeline(
    session_id: str,
    turn_id: str = QueryParam(
        default="*",
        description="취소 대상 턴 ID. 미지정 시 '*'로 현재 활성 턴 취소.",
    ),
):
    """파이프라인 실행 중단을 요청한다.

    Redis에 취소 플래그(turn_id 포함)를 설정하고,
    다음 노드 진입 시 파이프라인이 정상 종료(CANCELLED)된다.

    turn_id는 현재 실행 중인 턴을 식별하기 위해 필요하다.
    프론트엔드가 파이프라인 시작 시 받은 turn_id를 전달해야 한다.
    turn_id가 없으면 "*"로 세션 전체에 cancel을 설정한다 (하위 호환).
    """
    from src.agents.graph.cancel import get_cancel_store

    store = get_cancel_store()
    if store is None:
        raise HTTPException(
            status_code=503, detail="취소 기능 미활성",
        )

    await store.set_cancel(session_id, turn_id)
    return {
        "status": "cancel_requested",
        "session_id": session_id,
        "turn_id": turn_id,
    }
```

> **turn_id 전달 방식**: 프론트엔드가 WebSocket `stream start` 메시지에 `turn_id`를 포함하여 전달받고, cancel 시 이를 REST 요청에 포함. 프론트엔드 구현 전까지는 `turn_id="*"`로 하위 호환.
> 와일드카드(`"*"`) 매칭: cancel 엔드포인트에서 `turn_id="*"`를 값으로 저장하면, `check_cancel`이 정확한 turn_id 매칭 실패 후 `"*"` 폴백으로 매칭한다 (§5.3 참조). 프론트엔드가 turn_id를 전달할 수 있게 되면 와일드카드 의존을 제거한다.

### 5.12 lifespan에서 CancelStore 초기화 (`src/main.py`)

```python
# main.py lifespan 내부 — store.connect() 이후

from src.agents.graph.cancel import set_cancel_store
from src.services.cancel_store import RedisCancelStore, MemoryCancelStore

# SessionStore와 동일한 백엔드 선택 패턴
if settings.session_backend == "redis":
    from src.services.session.redis_store import RedisSessionStore
    session_store = get_session_store()
    if isinstance(session_store, RedisSessionStore):
        cancel_store = RedisCancelStore(session_store._client)
    else:
        cancel_store = MemoryCancelStore()
else:
    cancel_store = MemoryCancelStore()
set_cancel_store(cancel_store)
```

### 5.13 stream end 메시지에 status 추가 (`src/main.py` — CR-07)

```python
# _run_ws_pipeline 내부 — stream end 메시지

await websocket.send_json({
    "type": "stream",
    "action": "end",
    "status": (
        "cancelled"
        if pipeline_result.response
        and "중단되었습니다" in pipeline_result.response
        else "success"
    ),
    "insight": pipeline_result.insight,
    "turn_id": pipeline_result.turn_id,
    "user_turn_id": pipeline_result.user_turn_id,
})
```

> **개선안**: `PipelineResult`에 `status` 필드를 추가하여 문자열 매칭 대신 명시적 판정.

```python
# src/agents/models/response.py — PipelineResult
class PipelineResult(BaseModel):
    # ... 기존 필드 ...
    cancelled: bool = False    # ← 추가

# runner.py — _build_result에서 설정
pipeline_result.cancelled = (
    result.get("status") == QueryStatus.CANCELLED
    or result.get("status") == "cancelled"
)

# main.py — stream end에서 사용
"status": "cancelled" if pipeline_result.cancelled else "success",
```

---

## 6. interrupt 대기 중 cancel 처리 (CR-04)

### 6.1 시나리오

명확화 대기(`interrupt()`) 중 사용자가 cancel → 새 질의 입력:

```
T1: clarification_handler에서 interrupt() 호출 → 대기
T2: 사용자가 cancel 클릭 → POST /cancel → Redis 플래그 설정
T3: 사용자가 새 질의 입력 → run_pipeline() 호출
T4: pop_cancel() → 이전 cancel 플래그 발견
T5: is_interrupt_pending = True이지만 cancel 감지 → False로 전환
T6: 새 PipelineState로 ainvoke (새 turn_id) → 이전 interrupt 무시
```

### 6.2 체크포인터 정합성

cancel 후 새 턴 시작 시, 체크포인터에 이전 `interrupted` 상태가 남아있다.
`is_interrupt_pending = False`로 전환하여 새 턴(`ainvoke(initial_state)`)을 시작하면,
LangGraph는 기존 체크포인트 위에 새 실행을 덮어쓴다.

- 같은 `thread_id`(session_id)를 사용하므로 체크포인터에 새 상태가 저장됨
- 이전 interrupted 상태는 새 실행의 초기 상태로 교체됨
- 정합성 문제 없음 (LangGraph의 표준 동작)

### 6.3 장기 과제

cancel 시 새 `thread_id`를 생성하여 완전 격리하는 방안은
세션 관리 구조(thread_id = session_id) 변경이 필요하므로 별도 이슈로 분리.

---

## 7. CANCELLED 경로 END 도달 검증

### 7.1 Reason 계층에서 cancel 감지된 경우

```
context_retriever (방식 B: CANCELLED 반환)
    → context_interpreter (통과 — status 체크 없음, 문제 없음)
    → readiness_gate (방식 B: CANCELLED 감지 시 재반환)
    → _route_after_readiness_gate: CANCELLED → "conclude_failure"
    → result_finalizer: CANCELLED 분기 → error_message 설정
    → _route_after_result_finalizer: CANCELLED → "error_end"
    → error_end → END ✅
```

### 7.2 result_finalizer 이후 cancel 감지된 경우

```
result_finalizer (정상 완료, validated_sql 존재)
    → _route_after_result_finalizer: CANCELLED 체크 (최우선) → "error_end"
    → error_end → END ✅
```

만약 `_route_after_result_finalizer`를 통과한 후 cancel이 설정된 경우:

```
execute_sql (방식 B: CANCELLED 체크)
    → CANCELLED → 부분 결과 반환 (formatted_response 설정)
    → _route_after_execution: status != ERROR → "format_response"
    → format_response → END ✅
```

> **주의**: `execute_sql`에서 cancel 시 `QueryStatus.CANCELLED`를 설정하지만, `_route_after_execution`은 `QueryStatus.ERROR`만 체크한다. `CANCELLED`는 `ERROR`가 아니므로 `analyze_data` 또는 `format_response`로 라우팅된다. `formatted_response`가 이미 cancel 메시지로 설정되어 있으므로 `format_response`가 이를 그대로 전달하면 문제 없다. 단, `analyze_data`로 빠지면 불필요한 LLM 호출이 발생한다.

**보완**: `_route_after_execution`에도 CANCELLED 체크 추가:

```python
def _route_after_execution(state: PipelineState) -> str:
    if state.status in (QueryStatus.ERROR, QueryStatus.CANCELLED):
        return "error_end"
    if state.intent == IntentType.DATA_ANALYSIS:
        return "analyze_data"
    return "format_response"
```

이 경우 `error_end` 노드가 cancel용 메시지를 덮어쓰지 않도록, `_handle_error`에서 `CANCELLED` 체크:

```python
def _handle_error(state: PipelineState) -> dict:
    if state.status == QueryStatus.CANCELLED:
        return {
            "formatted_response": (
                state.formatted_response
                or "요청이 중단되었습니다."
            ),
            "status": QueryStatus.CANCELLED,
        }
    # ... 기존 에러 처리 로직
```

### 7.3 최종 CANCELLED 경로 요약

| cancel 감지 시점 | 경로 | END 도달 |
|-----------------|------|---------|
| context_retriever/readiness_gate/sql_generator | → result_finalizer → error_end → END | ✅ |
| _route_after_readiness_gate | → result_finalizer → error_end → END | ✅ |
| _route_after_recovery_agent | → result_finalizer → error_end → END | ✅ |
| _route_after_result_finalizer | → error_end → END | ✅ |
| execute_sql_node | → error_end → END | ✅ |

---

## 8. `astream()` 전환 검토

리서치 결과 `astream()`이 중단에 더 적합하나,
현재 `ainvoke()` 기반 구조에서 전면 전환은 별도 이슈.
취소 플래그 방식은 `ainvoke()`/`astream()` 어느 쪽에서든 동작한다.

---

## 9. 미결 사항

- [ ] `astream()` 전환 시점 및 범위 (별도 이슈)
- [ ] LangGraph 버그 #5682, #6726, #6950 수정 추적 → 수정 시 `Task.cancel()` 재검토
- [ ] 프론트엔드 중단 버튼 UI/UX (로딩 중 취소 → 부분 결과 표시)
- [ ] 프론트엔드에 turn_id 전달 프로토콜 확정

---

## 10. 구현 순서

### Phase 1: 핵심 인프라 (서버)

1. `src/models/enums.py` — `CANCELLED` Enum 값 추가
2. `src/services/cancel_store.py` — 취소 스토어 신규 작성
3. `src/agents/graph/cancel.py` — 취소 체크 유틸 신규 작성
4. `src/main.py` — lifespan에서 CancelStore 초기화

### Phase 2: 파이프라인 통합 (서버)

5. `src/agents/nodes/reason/context_retriever.py` — 시작부 체크
6. `src/agents/nodes/reason/readiness_gate.py` — 시작부 체크
7. `src/agents/nodes/reason/sql_generator.py` — 시작부 체크
8. `src/agents/nodes/present/sql_executor.py` — 시작부 체크 + 부분결과
9. `src/agents/graph/pipeline.py` — 라우팅 함수 4개 취소 분기 + `_handle_error` 보완
10. `src/agents/nodes/reason/result_finalizer.py` — CANCELLED 분기

### Phase 3: 엔드포인트 + runner (서버)

11. `src/agents/graph/runner.py` — pop_cancel + interrupt cancel 판단
12. `src/routers/sessions.py` — REST cancel 엔드포인트
13. `src/agents/models/response.py` — PipelineResult.cancelled 필드

### Phase 4: 프론트엔드 연동 (UI)

14. `src/main.py` — stream end 메시지 status 필드
15. 프론트엔드 — cancel 버튼 → REST POST 호출
16. 프론트엔드 — stream end status에 따른 "중단됨" UI

### Phase 5: 테스트

17. 단위 테스트 — CancelStore (Memory/Redis)
18. 단위 테스트 — check_cancel, make_cancel_updates
19. 통합 테스트 — cancel 후 파이프라인 CANCELLED 종료
20. 통합 테스트 — interrupt 대기 중 cancel → 새 턴 시작

---

## 11. 출처

### 공식 문서
- [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [LangGraph Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)

### GitHub Issues / Discussions
- [#5682: 서브그래프 CancelledError 미전파](https://github.com/langchain-ai/langgraph/issues/5682) — OPEN
- [#6726: ToolNode CancelledError 미포착](https://github.com/langchain-ai/langgraph/issues/6726) — OPEN
- [#6950: AsyncPregelLoop 클린업 2차 예외](https://github.com/langchain-ai/langgraph/issues/6950) — OPEN
- [#2930: abort 기능 구현 논의](https://github.com/langchain-ai/langgraph/discussions/2930)
- [#5356: SDK runs.cancel() 동작 문제](https://github.com/langchain-ai/langgraph/discussions/5356)
