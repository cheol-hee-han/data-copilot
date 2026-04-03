# LangGraph 실행 중단(Cancel/Abort) 및 체크포인터 상태 보존 패턴 리서치

- 작성일: 2026-04-04
- 작성자: Research Analyst Agent
- 분류: LangGraph / 프로덕션 패턴 / 취소 처리

---

## 1. 요약 (Executive Summary)

LangGraph에서 사용자 주도 실행 중단은 **공식적으로 완전히 해결된 문제가 아니다.** 체크포인터의 superstep 단위 저장 메커니즘 덕분에 마지막 완료 노드까지의 상태는 보존되지만, `asyncio.Task.cancel()` 시 서브그래프 미전파, `ToolNode`의 `CancelledError` 미포착, `AsyncPregelLoop` 클린업 중 2차 예외 등 **미해결 버그가 다수 존재**한다 (2026-04-04 기준). 프로덕션 구현 시 이 제약들을 명시적으로 우회하는 설계가 필요하다.

---

## 2. 핵심 질문별 조사 결과

### Q1. ainvoke() 실행 중 asyncio.Task.cancel()을 호출하면 체크포인터에 마지막 완료 노드까지 안전하게 저장되는가?

**조건부 Yes — 단, 중요한 예외 존재.**

LangGraph의 체크포인터는 BSP(Bulk Synchronous Parallel) superstep 완료 시점마다 체크포인트를 저장한다. 공식 문서(Persistence 페이지)는 다음을 명시한다:

> "When a graph node fails mid-execution at a given super-step, LangGraph stores pending checkpoint writes from any other nodes that completed successfully at that superstep. When you resume graph execution from that superstep you don't re-run the successful nodes."

즉, `asyncio.Task.cancel()`로 그래프 태스크가 취소될 경우:
- **직전 superstep까지 완료된 노드의 출력**: `checkpoint_writes` 테이블에 `pending_writes`로 기록되어 보존됨
- **취소 시점에 실행 중이던 노드의 출력**: 저장되지 않음 (해당 superstep 미완료)

**단, 다음 조건에서 저장이 실패할 수 있다:**
- `ainvoke()`를 `asyncio.run()` 안에서 호출한 경우: LangGraph는 내부적으로 자체 이벤트 루프를 사용하므로, 외부에서 `asyncio.run`으로 래핑하면 취소 신호가 그래프 내부로 전파되지 않음 (Discussion #1601)
- `AsyncPregelLoop.__aexit__` 중 CancelledError 발생 시 클린업 자체가 실패하는 버그 존재 (Issue #6950, 미해결)

### Q2. astream() 방식이 중단에 더 적합한가?

**Yes — 명확하고 실질적인 이유가 있다.**

`astream()` vs `ainvoke()`의 취소 적합성 차이:

| 특성 | astream() | ainvoke() |
|------|-----------|-----------|
| interrupt 이벤트 가시성 | 스트림에서 직접 확인 가능 | 체크포인터를 직접 쿼리해야 함 |
| 노드 경계에서의 취소 | 각 yield 지점에서 자연스럽게 취소 가능 | 전체 완료까지 블로킹 |
| 부분 결과 수집 | 가능 (yield된 청크까지) | 불가 |
| WebSocket disconnect 대응 | for-loop break으로 자연스러운 처리 | 외부 Task.cancel() 필요 |

공식 문서:
> "If you use stream or astream to run the graph, you will see an interrupt event when it was interrupted. However, if you use invoke or ainvoke, this information is not available as part of the response currently."

**결론: `astream()` + for-loop break + 외부 asyncio.Task.cancel()의 조합이 가장 자연스러운 취소 패턴이다.**

### Q3. LangGraph가 공식적으로 권장하는 "사용자 주도 중단" 패턴이 있는가?

**공식 권장 패턴은 두 계층으로 분리된다:**

#### 계층 1: LangGraph Platform/SDK 사용 시
LangGraph SDK(`langgraph-sdk`)는 `runs.cancel(thread_id, run_id)`를 제공한다. 그러나:
- SDK의 `runs.cancel()`은 "interrupt를 throw하는" 동작을 하며, 실제 asyncio 태스크를 즉시 종료하지는 않는다 (Discussion #5356 커뮤니티 보고)
- 로컬 환경에서는 동작하나 프로덕션 배포에서 404 에러를 반환하는 사례 보고됨 (LangChain Forum)

#### 계층 2: Self-hosted (FastAPI + LangGraph 직접 통합) 시
공식 문서는 다음 환경변수 기반 타임아웃 설정을 제시한다:
- `BG_JOB_TIMEOUT_SECS`: 백그라운드 작업 타임아웃
- `BG_JOB_ISOLATED_LOOPS=true`: 이벤트 루프 격리
- `BG_JOB_SHUTDOWN_GRACE_PERIOD_SECS`: 240~1200초 권장

**사용자가 직접 "중단 버튼"을 누르는 시나리오의 공식 가이드는 현재 존재하지 않는다.** 이는 커뮤니티에서 활발히 요청 중인 기능이다 (Discussion #2930, Forum "Stopping endpoint for deep agents").

### Q4. checkpoint_writes 테이블의 역할 — cancel 시 어떻게 처리되는가?

**체크포인트 저장 아키텍처 (DeepWiki / 공식 문서 종합):**

```
superstep N 시작
  → 노드 A, B 병렬 실행
  → A 완료: checkpoint_writes에 put_writes() 호출 → pending_writes 기록
  → B 완료: checkpoint_writes에 put_writes() 호출 → pending_writes 기록
  → 모든 노드 완료 후 superstep N 체크포인트 확정 (channel_values에 병합)
superstep N+1 시작 → channel_values가 최신 상태로 업데이트됨
```

**cancel 발생 시나리오:**

| 타이밍 | 결과 |
|--------|------|
| superstep N 완료 후, N+1 시작 전 | superstep N 상태 완전 보존 |
| superstep N 중, 일부 노드만 완료 | 완료된 노드의 pending_writes는 저장, 미완료 노드는 손실 |
| put_writes() 실행 중 CancelledError | 트랜잭션 롤백 → 해당 노드 출력 손실 |

`checkpoint_writes` 테이블의 `pending_writes`는 다음 superstep의 `channel_values`에 병합될 때까지 임시 기록으로 유지된다. PostgreSQL 기반 체크포인터에서 취소 시 이 병합이 완료되지 않으면 pending_writes는 남아있지만 channel_values에는 반영되지 않는다.

### Q5. CancelledError 발생 시 LangGraph 내부 정리(cleanup) 동작은?

**알려진 버그 목록 (2026-04 기준):**

#### 버그 1: 서브그래프 CancelledError 미전파 (Issue #5682, 2025-07)
- **증상**: FastAPI에서 `graph.astream()`을 통한 스트리밍 중 클라이언트 disconnect 시, 부모 그래프는 중단되나 자식 서브그래프(ainvoke로 호출된)는 계속 실행됨
- **버전**: LangGraph 0.3.5+
- **미해결**: 노드 내에서 `asyncio.create_task()` 방식으로 서브그래프를 실행하면 취소 신호가 전파되지 않음

#### 버그 2: ToolNode가 CancelledError를 포착하지 못함 (Issue #6726)
- **증상**: `handle_tool_errors=True`여도 `asyncio.CancelledError`는 `BaseException`을 상속하므로 `except Exception`에 포착되지 않음
- **결과**: ToolMessage 없이 AIMessage만 기록되어 `INVALID_CHAT_HISTORY` 오류 발생
- **공식 문서**: INVALID_CHAT_HISTORY 트러블슈팅 페이지에 별도 문서화됨
- **우회**: `asyncio.CancelledError`를 명시적으로 except하여 error ToolMessage를 수동 생성

#### 버그 3: AsyncPregelLoop 클린업 중 CancelledError (Issue #6950)
- **증상**: `AsyncPregelLoop.__aexit__` 내의 `AsyncExitStack` 정리 중 CancelledError가 재발생
- **영향**: 클린업 자체가 실패하여 리소스 누수 가능성
- **미해결**: 최신 안정 버전에서도 재현됨

#### 버그 4: asyncio.Task destroyed 경고 (Discussion #6163, Issue #6367)
- **증상**: `AsyncPostgresStore`가 백그라운드 배치 태스크를 정리하지 못해 "Task was destroyed but it is pending!" 경고 발생
- **영향**: 체크포인터 + 스토어 동시 사용 시

### Q6. interrupt() (명확화용)와 사용자 주도 cancel의 차이점과 조합 가능성

**핵심 차이:**

| 특성 | interrupt() | asyncio.Task.cancel() |
|------|-------------|----------------------|
| 목적 | 사용자 입력 대기 후 재개 | 실행 완전 종료 |
| 체크포인트 저장 | 명시적 저장 후 suspend | superstep 경계에서만 보장 |
| 재개 가능성 | Command로 재개 가능 | 새 run 시작 또는 time-travel |
| 내부 메커니즘 | GraphInterrupt 예외 raise | asyncio CancelledError |
| ToolNode 호환성 | 완전 지원 | 버그 #6726으로 부분 파손 |

**interrupt()의 정확한 동작 (공식 문서):**
1. 노드 내에서 `interrupt(value)` 호출
2. LangGraph 런타임이 `GraphInterrupt` 예외를 raise
3. 현재 노드 실행 전까지의 상태를 체크포인터에 저장
4. 그래프 상태: `interrupted`
5. `Command(resume=value)`로 재개 시 해당 노드를 처음부터 재실행 (interrupt() 호출 이전 코드 포함)

**중요 제약**: 재개 시 노드 전체를 처음부터 재실행하므로, interrupt() 이전의 부수효과(DB write, 외부 API 호출)가 반복 실행될 수 있다. 따라서 interrupt() 앞에 멱등성(idempotency) 보장이 필요하다.

**조합 패턴:**
```
interrupt()  →  사용자가 "취소"를 선택  →  해당 thread에 cancel 상태 마킹
              →  사용자가 "계속"을 선택  →  Command(resume=...)로 재개
```
`interrupt()`로 일시 중단한 후 사용자가 취소를 선택할 경우, 별도의 asyncio 취소 없이도 해당 thread에 더 이상 `Command`를 보내지 않는 것으로 사실상 취소가 가능하다. 체크포인트는 interrupted 상태로 영구 보존된다.

---

## 3. 프로덕션 패턴 종합 평가

### 패턴 A: astream() + asyncio.Task.cancel() (가장 일반적)

```python
# FastAPI WebSocket 예시 (개념)
async def stream_graph(websocket, config):
    task = asyncio.create_task(
        consume_astream(graph, config, websocket)
    )
    try:
        await task
    except asyncio.CancelledError:
        # 마지막 superstep까지 체크포인터에 저장됨
        # 단, 실행 중 노드 출력은 손실
        pass

async def consume_astream(graph, config, websocket):
    async for chunk in graph.astream(input, config, stream_mode="updates"):
        try:
            await websocket.send_json(chunk)
        except WebSocketDisconnect:
            return  # break으로 astream 루프 탈출
```

**평가:**
- 장점: 노드 경계에서 자연스러운 취소, astream의 yield 지점마다 취소 체크
- 단점: 서브그래프 CancelledError 미전파 (Issue #5682), ToolNode 파손 위험 (Issue #6726)

### 패턴 B: interrupt() 기반 사용자 주도 중단 (권장)

`interrupt()`로 명확화 대기 노드를 두고, 해당 시점에서 사용자가 "중단"을 선택하면 thread 상태를 `cancelled`로 마킹하는 앱 레벨 로직을 추가한다.

**평가:**
- 장점: LangGraph 공식 메커니즘, 체크포인트 안전 보장, 재개 가능
- 단점: 노드 내부 긴 LLM 호출 중에는 중단 불가 (interrupt() 호출 전)

### 패턴 C: 앱 레벨 취소 플래그 (폴백)

Redis 또는 DB에 `{thread_id: "cancel"}` 플래그를 저장하고, 노드 진입 시마다 이를 확인하여 `END`로 라우팅하는 패턴.

```python
async def check_cancel_node(state, config):
    thread_id = config["configurable"]["thread_id"]
    if await redis.get(f"cancel:{thread_id}"):
        return Command(goto=END)
    return state
```

**평가:**
- 장점: 버그 #5682, #6726과 무관, 모든 LangGraph 버전에서 동작
- 단점: 노드 경계에서만 확인 가능 (LLM 호출 중 즉시 취소 불가), 레이턴시 추가

---

## 4. Data Copilot 프로젝트 적용 권고

### 적용 권고안

**1순위: 패턴 B + 패턴 C 혼합**

현재 Data Copilot은 이미 `interrupt()` 기반 명확화 노드를 가지고 있다 (project_hitl_clarification_unification.md 참조). 여기에 다음을 추가한다:

- `interrupt()` 대기 중 사용자가 "취소/새 질문"을 선택 → thread 상태 마킹 → 더 이상 `Command(resume=...)` 미전송 → 기존 thread 방기(abandon)
- 파이프라인 노드 시작부에 취소 플래그 체크 추가 (패턴 C)

**기각된 대안:**

| 대안 | 기각 이유 |
|------|----------|
| `asyncio.Task.cancel()` 직접 사용 | Issue #5682 (서브그래프 미전파), #6726 (INVALID_CHAT_HISTORY) |
| LangGraph SDK `runs.cancel()` | Discussion #5356: 실제 취소 아닌 interrupt throw, 프로덕션 404 오류 보고 |
| `interrupt_before/after` 모든 노드 | 모든 노드마다 체크포인트 저장 → 성능 저하 |

**2순위 (장기): LangGraph 버그 수정 추적**

- Issue #5682, #6726, #6950 모니터링
- `checkpoint_during` 파라미터 안정화 후 노드 내부 체크포인팅 활용 가능성 재평가

### 폐쇄망 특이사항

- LangGraph Platform SDK (`runs.cancel()`)는 폐쇄망에서 사용 불가 → 패턴 C(앱 레벨 플래그)가 유일한 실용 대안
- `BG_JOB_*` 환경변수는 LangGraph Platform(호스팅 서비스) 전용 → 자체 FastAPI 배포에는 미적용

---

## 5. 출처

### 공식 문서
- [LangGraph Persistence 개념 문서](https://docs.langchain.com/oss/python/langgraph/persistence)
- [LangGraph Interrupts 개념 문서](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [LangGraph Durable Execution](https://docs.langchain.com/oss/python/langgraph/durable-execution)
- [INVALID_CHAT_HISTORY 트러블슈팅](https://langchain-ai.github.io/langgraph/troubleshooting/errors/INVALID_CHAT_HISTORY/)
- [CancelledError 해결 공식 지원 문서](https://support.langchain.com/articles/7667944497-how-do-i-resolve-cancellederror-in-langgraph-long-running-operations)
- [LangGraph Changelog: pending writes checkpointing 개선](https://changelog.langchain.com/announcements/improved-message-handling-checkpointing-of-pending-writes-and-metadata-rendering-in-langgraph)

### GitHub Issues / Discussions
- [Discussion #1601: Cancelled error with langgraph runs](https://github.com/langchain-ai/langgraph/discussions/1601)
- [Discussion #2930: Implementing abort functionality in LangGraph (Python)](https://github.com/langchain-ai/langgraph/discussions/2930)
- [Discussion #5141: CancelledError when exiting from subgraph](https://github.com/langchain-ai/langgraph/discussions/5141)
- [Discussion #5356: How to abort a run using langgraph sdk](https://github.com/langchain-ai/langgraph/discussions/5356)
- [Issue #5682: Can not stop sub graph when asyncio.CancelledError occurred](https://github.com/langchain-ai/langgraph/issues/5682)
- [Issue #6726: handle_tool_errors=True does not catch asyncio.CancelledError](https://github.com/langchain-ai/langgraph/issues/6726)
- [Issue #6950: Random CancelledError in AsyncPregelLoop cleanup](https://github.com/langchain-ai/langgraph/issues/6950)
- [Issue #6367: AsyncPostgresStore cleanup leaves pending background batch tasks](https://github.com/langchain-ai/langgraph/issues/6367)
- [Discussion #3336: Differentiate graph ended vs. interrupted](https://github.com/langchain-ai/langgraph/discussions/3336)

### 아키텍처 참조
- [LangGraph Checkpointing Architecture (DeepWiki)](https://deepwiki.com/langchain-ai/langgraph/4.1-checkpointing-architecture)
- [LangChain Forum: Stopping endpoint for deep agents](https://forum.langchain.com/t/stopping-endpoint-for-deep-agents/2538)
- [LangChain Forum: Run cancellation 404 in production](https://forum.langchain.com/t/langgraph-run-cancellation-works-locally-but-returns-404-in-production/609)
- [LangGraph Checkpoint README (GitHub)](https://github.com/langchain-ai/langgraph/blob/main/libs/checkpoint/README.md)
