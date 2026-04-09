# 설계 검토 보고서: 파이프라인 실행 중단(Cancel) 설계

- **검토일**: 2026-04-06
- **검토 대상**: `docs/todo/20260404-pipeline-cancel-design.md` (2026-04-06 갱신본)
- **관련 코드**: `runner.py`, `pipeline.py`, `main.py`, `state.py`, `result_finalizer.py`, `enums.py`, `redis_store.py`, `sessions.py`

---

## 총평

전체 방향(앱 레벨 취소 플래그 + interrupt thread 방기)은 LangGraph 현재 한계를 고려했을 때 합리적이다.
리서치 근거(GitHub Issue 3건, Discussion 2건)가 충분하고, 기각 대안의 이유도 명확하다.

그러나 구현 설계 단계에서 **경쟁 상태**, **Present 계층 취소 누락**, **WebSocket 동시성**, **체크포인터 정합성** 등
실전 시나리오에 대한 고려가 부족하다. 아래 7건의 비판 사항을 반영하면 안전하게 구현할 수 있다.

| 심각도 | 건수 |
| ------ | ---- |
| [P0] 치명적 | 1건 |
| [P1] 중대한 | 3건 |
| [P2] 개선 필요 | 2건 |
| [P3] 제안 | 1건 |

---

## 가정 검증 결과

### 가정 A1: "WebSocket 메시지 수신과 파이프라인 실행은 동시에 진행된다"

**검증 결과: 거짓.**

현재 `websocket_endpoint`의 메시지 루프 구조를 보면:

```python
# main.py websocket_endpoint (현재)
while True:
    data = await websocket.receive_text()       # ← (1) 대기
    ...
    await _run_ws_pipeline(data, ...)           # ← (2) 블로킹
```

`_run_ws_pipeline`이 `await run_pipeline()`을 호출하고 완료될 때까지 블로킹하므로,
파이프라인 실행 중에는 `receive_text()` 루프로 돌아가지 않는다.
따라서 **사용자가 `{"type": "cancel"}` 메시지를 보내도 파이프라인 실행 중에는 수신되지 않는다.**

이는 설계의 핵심 전제("WebSocket에서 cancel 메시지를 수신하면 Redis 플래그를 설정")를 무너뜨린다.

→ **[P0] CR-01로 분류** (아래 상세)

### 가정 A2: "노드 시작부 check_cancel()은 Redis 1회 GET으로 충분하다"

**검증 결과: 참. 단 오버헤드 고려 필요.**

Redis GET은 보통 0.1~0.5ms이므로 3~5개 노드에서 호출해도 총 1.5~2.5ms로 무시할 만하다.
단, 폐쇄망 환경에서 Redis 지연이 커지면 파이프라인 전체 지연에 영향.
→ [P2] CR-05에서 대안 논의

### 가정 A3: "make_cancel_updates()가 반환한 dict는 LangGraph state reducer와 호환된다"

**검증 결과: 주의 필요.**

`PipelineState`의 `resolved_signals` 필드는 `Annotated[list[AmbiguitySignal], operator.add]` reducer를 사용한다.
`make_cancel_updates()`는 이 필드를 건드리지 않으므로 현재는 안전하다.
그러나 `reason` 필드를 통째로 교체하는 방식이므로, 동일 superstep에서 다른 노드가 reason의 일부를 수정하면 충돌할 수 있다.
현재 파이프라인은 노드가 순차 실행되므로 실질적 문제는 없다.

### 가정 A4: "§4.11 interrupt 대기 중 취소는 별도 구현 불필요"

**검증 결과: 부분적으로 참이나, 고려되지 않은 시나리오 존재.**

설계는 "새 질의 시 Command(resume=새질의)로 재개"라고 하지만, 이는 명확화 대기 중에 사용자가 전혀 다른 주제를 질문하는 경우를 의미한다.
이 경우 clarification_handler에서 resume 값을 받아 기존 AmbiguitySignal의 답변으로 처리하게 되므로,
**의도치 않은 명확화 응답으로 파이프라인이 잘못된 방향으로 진행될 수 있다.**
→ [P1] CR-04에서 상세화

---

## 주요 비판 사항

### [P0] CR-01: WebSocket 단일 루프 블로킹 — cancel 메시지 수신 불가

**비판**: 설계 §4.9의 WebSocket cancel 메시지 처리는 현재 `websocket_endpoint` 구조에서 동작하지 않는다.
`_run_ws_pipeline()`이 완료될 때까지 `receive_text()` 루프로 돌아가지 않으므로,
파이프라인 실행 중 사용자가 보낸 cancel 메시지는 큐에 쌓이기만 하고 읽히지 않는다.

**근거**: `main.py` L469-L508 — `while True: data = await websocket.receive_text()` → `await _run_ws_pipeline(data, ...)` 순차 구조.

**대안**:

**대안 A (권장)**: 파이프라인 실행과 WebSocket 수신을 별도 Task로 분리

```python
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    ...
    while True:
        data = await websocket.receive_text()
        ...
        # 파이프라인을 별도 Task로 실행
        pipeline_task = asyncio.create_task(
            _run_ws_pipeline(data, session_id, websocket, ...)
        )
        # cancel 메시지 수신 루프
        try:
            while not pipeline_task.done():
                # 짧은 타임아웃으로 메시지 확인
                try:
                    msg = await asyncio.wait_for(
                        websocket.receive_text(), timeout=0.5,
                    )
                    if _is_cancel_message(msg):
                        await cancel_store.set_cancel(session_id)
                        await websocket.send_json({...})
                except asyncio.TimeoutError:
                    continue
            await pipeline_task  # 예외 전파
        except WebSocketDisconnect:
            pipeline_task.cancel()
            raise
```

**대안 B (간단)**: WebSocket cancel은 포기하고 REST cancel 엔드포인트만 제공.
프론트엔드가 cancel 버튼 클릭 시 `POST /api/sessions/{session_id}/cancel`을 호출.
WebSocket은 현재 구조 유지.

→ **대안 B가 구현 복잡도 대비 효과적. 대안 A는 동시성 버그 위험이 높고, `asyncio.wait_for` + WebSocket 조합은 edge case가 많다.**

---

### [P1] CR-02: Present 계층(execute_sql, analyze_data) 취소 누락

**비판**: 설계의 취소 체크 포인트는 Reason 계층(context_retriever ~ result_finalizer)에만 집중되어 있다.
그러나 `execute_sql_node`는 실제 정보계 DB에 SQL을 실행하고,
`analyze_data_node`는 LLM을 호출하여 분석을 수행한다.
이미 result_finalizer를 통과하여 SQL이 확정된 후 cancel을 누르면,
DB 쿼리가 실행되고 분석까지 완료된 후에야 사용자에게 결과가 반환된다.

**근거**: `pipeline.py` L507-L541 — `result_finalizer → execute_sql → analyze_data → format_response → END` 경로에 cancel 체크 없음.
`execute_sql_node`는 정보계 DB에 실제 쿼리를 실행하므로 비용이 크다.

**대안**: `execute_sql_node` 시작부에도 방식 B 체크를 추가한다.
단, 이미 SQL이 확정된 시점이므로, cancel 시 "SQL은 생성되었으나 실행하지 않았습니다"라는 메시지와 함께
생성된 SQL을 부분 결과로 반환하는 것이 사용자 경험상 더 좋다.

```python
async def execute_sql_node(state: PipelineState) -> dict:
    from src.agents.graph.cancel import check_cancel
    if await check_cancel(state.session_id):
        return {
            "formatted_response": (
                "요청이 중단되었습니다. "
                f"생성된 SQL: {state.reason.validated_sql[:200]}"
            ),
            "status": QueryStatus.CANCELLED,
        }
    # ... 기존 로직
```

---

### [P1] CR-03: clear_cancel() → set_cancel() 경쟁 상태

**비판**: `runner.py`의 `run_pipeline()` 시작부에서 `await clear_cancel(session_id)`를 호출하는데,
동일 세션에서 거의 동시에 (1) 새 질의 제출 + (2) 이전 질의 cancel 이 발생하면:

```
시간축:  ──────────────────────────────────►
T1: 사용자: 새 질의 제출
T2: run_pipeline() 시작 → clear_cancel()  ← 플래그 삭제
T3: 이전 질의의 cancel 버튼 클릭 → set_cancel()  ← 플래그 설정
T4: 새 질의 파이프라인 실행 중 → check_cancel() → True!
결과: 새 질의가 의도치 않게 취소됨
```

**근거**: REST API와 WebSocket이 별개 연결이므로, cancel API 호출과 새 질의 제출이 동시에 도착할 수 있다.

**대안**: cancel 플래그에 `turn_id`를 포함시킨다.

```python
# set_cancel: cancel:{session_id} = turn_id 저장
await redis.set(f"cancel:{session_id}", turn_id, ex=300)

# check_cancel: 현재 turn_id와 일치하는 경우만 취소
stored = await redis.get(f"cancel:{session_id}")
return stored is not None and stored == current_turn_id
```

이렇게 하면 이전 턴의 cancel이 새 턴에 영향을 주지 않는다.
`clear_cancel()`도 여전히 사용하되, 경쟁 상태의 안전망 역할을 한다.

---

### [P1] CR-04: interrupt 대기 중 새 질의 시 잘못된 resume

**비판**: §4.11에서 "별도 구현 불필요"라고 했지만, 명확화 대기 중 사용자가 cancel 후 새 질의를 입력하면:
1. 체크포인터에는 `interrupted` 상태가 저장됨
2. `runner.py`의 `is_interrupt_pending = True` 감지
3. `Command(resume=새질의텍스트)`로 재개
4. `clarification_handler`가 새 질의 텍스트를 이전 AmbiguitySignal의 답변으로 처리

이는 **이전 명확화 질문에 엉뚱한 답변이 들어가는 결과**를 만든다.

**근거**: `runner.py` L162-L171 — `is_interrupt_pending`이면 무조건 `Command(resume=sanitized.text)`로 재개.
runner는 "이전 interrupt가 cancel로 인한 것인지 명확화 대기인지"를 구분하지 않는다.

**대안**: cancel 후 새 질의 시, interrupt 상태를 무시하고 새 턴으로 시작하는 로직이 필요하다.

```python
# runner.py — interrupt 대기 감지 후 cancel 여부 확인
is_interrupt_pending = False
try:
    state_snapshot = await app.aget_state(run_config)
    is_interrupt_pending = bool(
        state_snapshot is not None and state_snapshot.next
    )
except Exception:
    ...

# cancel이 요청된 적 있는 세션이면 interrupt를 무시하고 새 턴 시작
if is_interrupt_pending:
    cancel_store = get_cancel_store()
    was_cancelled = cancel_store and await cancel_store.is_cancelled(session_id)
    if was_cancelled:
        is_interrupt_pending = False  # 새 턴으로 처리
        await clear_cancel(session_id)
```

단, 이 방식은 체크포인터에 이전 interrupt 상태가 남아있는 문제가 있으므로,
장기적으로는 cancel 시 새 thread_id를 생성하여 완전히 격리하는 방안을 검토해야 한다.

---

### [P2] CR-05: 폐쇄망 Redis 미보장 + MemoryCancelStore 다중 워커 미지원

**비판**: 폐쇄망 환경에서 Redis가 반드시 가용하지 않을 수 있다. 또한 `MemoryCancelStore`는 인메모리 `set()`이므로
`uvicorn --workers 4` 등 다중 워커 배포 시 워커 간 플래그가 공유되지 않는다.
cancel API를 받은 워커 ≠ 파이프라인을 실행 중인 워커이면 cancel이 동작하지 않는다.

**근거**: `CLAUDE.md` — "uvicorn ... --workers 4" 운영 예시 명시. `config.py` — `session_backend: str = "memory"` 기본값.

**대안**:
1. **단기**: `MemoryCancelStore`는 단일 워커(개발/테스트) 전용임을 문서에 명시하고,
   운영 환경에서는 Redis 필수로 표기
2. **중기**: Redis 없는 폐쇄망을 위해 DB 기반 cancel store 추가
   (checkpointer pool을 재사용하여 `cancel_flags` 테이블에 upsert)

```python
class PostgresCancelStore:
    """Redis 없는 환경용 DB 기반 취소 스토어."""
    def __init__(self, pool):
        self._pool = pool
    async def set_cancel(self, session_id: str) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "INSERT INTO cancel_flags (session_id, created_at) "
                "VALUES (%s, NOW()) "
                "ON CONFLICT (session_id) DO UPDATE SET created_at = NOW()",
                (session_id,),
            )
    async def is_cancelled(self, session_id: str) -> bool:
        async with self._pool.connection() as conn:
            row = await conn.execute(
                "SELECT 1 FROM cancel_flags "
                "WHERE session_id = %s AND created_at > NOW() - INTERVAL '5 minutes'",
                (session_id,),
            )
            return row.fetchone() is not None
```

이미 checkpointer_pool이 존재하므로 추가 인프라 없이 구현 가능.

---

### [P2] CR-06: 싱글턴 `_cancel_store`의 테스트 격리 문제

**비판**: `cancel.py`의 `global _cancel_store` 패턴은 테스트 간 상태 누출을 유발한다.
`pipeline.py`의 `_compiled_app` 싱글턴은 `reset_compiled_app()`으로 초기화할 수 있지만,
cancel_store에는 리셋 함수가 없다.

**근거**: 프로젝트 테스트 전략 — `pytest` 사용, fixture로 격리.

**대안**: `reset_cancel_store()` 함수를 추가하고, `conftest.py`에서 fixture로 자동 초기화.

```python
# cancel.py
def reset_cancel_store() -> None:
    """테스트에서 싱글턴을 초기화한다."""
    global _cancel_store
    _cancel_store = None
```

---

### [P3] CR-07: cancel 후 WebSocket 스트리밍 응답 처리 미정의

**비판**: 현재 `_run_ws_pipeline()`은 파이프라인 완료 후 `stream start → chunk → end` 시퀀스를 보낸다.
cancel로 파이프라인이 `CANCELLED` 상태로 종료되면, 이 스트리밍 시퀀스에서
`type: "stream", action: "end"` 메시지에 cancel 여부가 표시되지 않는다.
프론트엔드가 "답변 완료"와 "취소됨"을 구분할 수 없다.

**근거**: `main.py` L398-L418 — stream end 메시지에 status 필드 없음.

**대안**: `stream end` 메시지에 `status` 필드를 추가한다.

```python
await websocket.send_json({
    "type": "stream",
    "action": "end",
    "status": "cancelled" if pipeline_result.status == "cancelled" else "success",
    "insight": pipeline_result.insight,
    ...
})
```

프론트엔드는 `status === "cancelled"`일 때 "중단됨" UI를 표시한다.

---

## 실패 시나리오

### 시나리오 F1: Redis 장애 시 cancel 동작

**상황**: 운영 중 Redis가 일시적으로 불통.
**현재 설계**: `RedisCancelStore.is_cancelled()`가 예외 발생 → 노드에서 미처리 예외로 파이프라인 실패.
**권장**: `check_cancel()`에서 Redis 예외를 catch하여 `False` 반환 (cancel 불가능이 파이프라인 실패보다 낫다).

```python
async def check_cancel(session_id: str) -> bool:
    if _cancel_store is None:
        return False
    try:
        return await _cancel_store.is_cancelled(session_id)
    except Exception:
        logger.warning("취소 플래그 확인 실패 — 무시하고 계속")
        return False
```

### 시나리오 F2: 취소 후 같은 세션에서 체크포인터 정합성

**상황**: cancel로 CANCELLED 상태가 체크포인터에 저장된 후, 같은 session_id로 새 질의.
**현재 설계**: `runner.py`에서 `aget_state()` 조회 시 `state_snapshot.next`가 비어있으면(CANCELLED는 END까지 갔으므로)
새 턴으로 `ainvoke()`.
**검증**: CANCELLED 경로가 `result_finalizer → error_end → END`(또는 execute_sql에서 직접 END)까지 도달하는지 확인 필요.
**결론**: `_route_after_result_finalizer`에서 CANCELLED 시 `error_end`로 라우팅되면 END까지 도달하므로 안전.
단, 현재 설계의 result_finalizer CANCELLED 처리에서 `error_message`를 설정하지 않아
`_route_after_result_finalizer`에서 `error_end`로 빠지지 않을 수 있다. 확인 필요.

### 시나리오 F3: REST API `/api/query`에서의 cancel

**상황**: REST 클라이언트가 `/api/query`로 질의 후, 별도 요청으로 cancel을 호출.
**현재 설계**: REST 엔드포인트는 `await run_pipeline()`으로 블로킹 대기. cancel API는 별도 요청이므로 Redis 플래그는 정상 설정됨.
**결론**: REST 경로에서는 cancel이 정상 동작한다 (Redis 플래그 설정 → 다음 노드 체크 → CANCELLED 반환).

---

## 아키텍처 대안 비교

| 기준 | 현재 설계 (앱 플래그) | 대안: astream() + break | 대안: asyncio.Event 주입 |
| ---- | -------------------- | ---------------------- | ---------------------- |
| 구현 복잡도 | 낮음 (10파일, 단순 분기) | 중간 (runner 전면 개편) | 중간 (config에 Event 주입) |
| 취소 지연 | 노드 1개 (15~30초) | 노드 경계 즉시 | 노드 내부도 가능 |
| LangGraph 버그 영향 | 없음 | #5682, #6726 영향 | 없음 |
| 폐쇄망 호환 | 완전 호환 | 완전 호환 | 완전 호환 |
| 체크포인터 안전 | 안전 (정상 종료) | 위험 (mid-superstep) | 안전 (정상 종료) |
| 테스트 용이성 | 높음 (Mock store) | 중간 | 중간 |

→ **현재 설계가 최적 균형점.** astream() 전환은 별도 이슈로 분리한 판단도 적절.

---

## 재조사 요청 사항

1. `_route_after_result_finalizer`에서 CANCELLED 상태가 `execute_sql`로 빠지지 않고 `error_end`(또는 직접 END)로 가는 경로가 확보되는지 코드 레벨 확인
2. 폐쇄망 타겟 환경의 Redis 가용성 확인 (인프라팀 문의)
3. 프론트엔드 팀과 cancel 프로토콜 합의 (REST vs WebSocket, stream end 메시지 포맷)

---

## 수용 불가 항목 (재설계 권고)

| ID | 심각도 | 요약 | 권고 |
| -- | ------ | ---- | ---- |
| CR-01 | [P0] | WebSocket cancel 메시지가 파이프라인 실행 중 수신 불가 | REST cancel 전용으로 전환하거나, 수신 루프를 Task 분리 |
| CR-03 | [P1] | clear_cancel/set_cancel 경쟁 상태 | cancel 플래그에 turn_id 포함 |

---

## 수용 가능 항목 (개선 권고)

| ID | 심각도 | 요약 | 권고 |
| -- | ------ | ---- | ---- |
| CR-02 | [P1] | Present 계층 취소 누락 | execute_sql 시작부에 체크 추가 |
| CR-04 | [P1] | interrupt 대기 중 cancel 후 resume 오작동 | cancel 플래그 확인 후 interrupt 무시 분기 추가 |
| CR-05 | [P2] | 폐쇄망 Redis 미보장 | DB 기반 cancel store 대안 명시 |
| CR-06 | [P2] | 싱글턴 테스트 격리 | reset_cancel_store() 추가 |
| CR-07 | [P3] | cancel 시 스트리밍 응답 구분 불가 | stream end에 status 필드 추가 |

---

## 합의 추천 설계 방향

1. **WebSocket cancel은 포기, REST cancel 전용으로 단순화** (CR-01 해소)
   - 프론트엔드가 cancel 버튼 클릭 시 `POST /api/sessions/{session_id}/cancel` 호출
   - WebSocket은 현재 구조 유지 (단일 루프, 추가 동시성 없음)
   - WebSocket `{"type": "cancel"}` 메시지 처리 코드는 설계에서 제거

2. **cancel 플래그에 turn_id를 포함** (CR-03 해소)
   - `cancel:{session_id} = {turn_id}` 형태로 저장
   - `check_cancel(session_id, turn_id)` 시그니처로 변경
   - clear_cancel은 안전망으로 유지하되, turn_id 매칭이 1차 방어

3. **execute_sql_node에 취소 체크 추가** (CR-02 해소)
   - CANCELLED 시 생성된 SQL을 부분 결과로 반환

4. **check_cancel() 내부에서 예외를 catch** (F1 해소)

5. **향후 과제**: interrupt cancel 시나리오(CR-04), DB cancel store(CR-05)
