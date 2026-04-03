# 파이프라인 실행 중단(Cancel) 설계

> 작성일: 2026-04-04
> 상태: 설계 검토 중
> 리서치 원문: `docs/research/20260404-langgraph-cancel-abort-pattern.md`

## 1. 문제 정의

사용자가 웹 UI에서 중단 버튼을 클릭해도 파이프라인이 계속 실행된다.
현재 `runner.py`에서 `await app.ainvoke()`로 완료까지 블로킹 대기하며,
Task 래핑도 중단 엔드포인트도 없다.

## 2. 리서치 결론: `asyncio.Task.cancel()` 직접 사용은 위험

LangGraph에 미해결 버그가 다수 존재한다 (2026-04 기준):

| 버그 | GitHub Issue | 심각도 | 영향 |
|------|-------------|--------|------|
| 서브그래프에 CancelledError 미전파 | [#5682](https://github.com/langchain-ai/langgraph/issues/5682) | 높음 | 서브그래프가 중단 후에도 계속 실행 |
| ToolNode가 CancelledError 미포착 | [#6726](https://github.com/langchain-ai/langgraph/issues/6726) | 높음 | INVALID_CHAT_HISTORY → 다음 실행 파손 |
| AsyncPregelLoop 클린업 중 2차 예외 | [#6950](https://github.com/langchain-ai/langgraph/issues/6950) | 중간 | 리소스 누수 |

**공식적으로 "사용자 주도 중단" 패턴은 아직 제공되지 않는다** (Discussion [#2930](https://github.com/langchain-ai/langgraph/discussions/2930)).

## 3. 권고 방식: 앱 레벨 취소 플래그 + interrupt() thread 방기

### 3.1 앱 레벨 취소 플래그 (핵심)

Redis에 `cancel:{session_id}` 플래그를 저장하고,
**노드 진입 시마다** 이를 확인하여 그래프를 정상 종료(END)로 라우팅한다.

```
[사용자: 중단 클릭]
    ↓
[WebSocket/API] → Redis SET cancel:{session_id} = 1
    ↓
[다음 노드 진입 시] → 취소 플래그 확인 → END로 라우팅
    ↓
[result_finalizer] → status=CANCELLED, 부분 결과 반환
```

#### 장점
- LangGraph 내부 버그(#5682, #6726, #6950)와 무관
- 체크포인터에 정상 종료 상태로 안전하게 저장됨
- 폐쇄망에서도 동작 (LangGraph Platform SDK 불필요)
- 모든 LangGraph 버전에서 호환

#### 단점
- 노드 경계에서만 확인 가능 (LLM 호출 중 즉시 취소 불가)
- 노드 하나의 LLM 호출이 30초 걸리면 최대 30초 지연

### 3.2 interrupt() 대기 중 취소 (보완)

현재 `clarification_handler`��서 `interrupt()`로 사용자 응답 대기 중인 경우,
사용자가 "취소"를 선택하면:
- 해당 thread에 더 이상 `Command(resume=...)` 미전송
- 체크포인트는 `interrupted` 상태로 영구 보존
- 새 질의 시 새 thread로 시작

### 3.3 기각된 대안

| 대안 | 기각 이유 |
|------|----------|
| `asyncio.Task.cancel()` 직접 사용 | #5682 서브그래프 미전파, #6726 INVALID_CHAT_HISTORY |
| LangGraph SDK `runs.cancel()` | 폐쇄망 미적용, 프로덕션 404 오류 보고 |
| `interrupt_before` 모든 노드 | 매 노드마다 체크포인트 저장 → 성능 저하 |

## 4. 구현 설계

### 4.1 변경 범위

| 구분 | 변경 내용 | 난이도 |
|------|----------|--------|
| `state.py` | `is_cancelled: bool = False` 필드 추가 | 1줄 |
| `pipeline.py` | 취소 체크 유틸 노드 또는 조건부 엣지 추가 | 중 |
| `main.py` | 중단 엔드��인트 + WebSocket cancel 메시지 처리 | 중 |
| `runner.py` | 취소 상태 시 부분 결과 반환 로직 | 저 |
| `result_finalizer.py` | `CANCELLED` 상태 처리 | 저 |
| Redis 연동 | `cancel:{session_id}` 플래그 set/get/del | 저 |

### 4.2 취소 플래그 확인 위치

모든 노드 앞에 별도 노드를 추가하는 것은 과도하다.
**비용이 큰 노드(LLM 호출) 진입 전**에만 확인한다:

```
reasoning_preparer → [cancel_check] → knowledge_fetcher
knowledge_interpreter → [cancel_check] → readiness_gate
recovery_agent → [cancel_check] → knowledge_fetcher (재진입)
sql_generator → [cancel_check] → sql_validator
```

구현 방식: 조건부 엣지 함수에 취소 체크를 통합하거나,
각 노드 함수 시작부에 1줄 체크를 삽입한다.

```python
# 방식 A: 조건부 엣지에 통합
def _route_after_readiness_gate(state):
    if state.is_cancelled:
        return "result_finalizer"
    # ... 기존 로직

# 방식 B: 노드 시작부 체크 (더 단순)
async def knowledge_fetcher_node(state):
    if await _check_cancel(state):
        return {"reason": _mark_cancelled(state.reason)}
    # ... 기존 로직
```

### 4.3 중단 엔드포인트

```python
# 방식 1: REST API
@app.post("/api/cancel/{session_id}")
async def cancel_pipeline(session_id: str):
    await redis.set(f"cancel:{session_id}", "1", ex=300)
    return {"status": "cancel_requested"}

# 방식 2: WebSocket 메시지 (기존 연결 활용)
# 클라이언트 → {"type": "cancel"}
# 서버 → Redis 플래그 설정
```

### 4.4 부분 결과 보존

취소 시점까지 수집된 정보를 사용자에게 반환한다:

```python
# result_finalizer에서 CANCELLED 처리
if reason.is_cancelled:
    return {
        "status": "cancelled",
        "partial_result": {
            "candidate_tables": [t.table_name for t in reason.candidate_tables],
            "knowledge_items": len(reason.knowledge_items),
            "message": "요청이 중단되었습니다. 지금까지 수집한 정보가 있습니다.",
        }
    }
```

### 4.5 LLM 호출 중 즉시 중단 (향후 개선)

현재 설계는 노드 경계에서만 취소를 확인한다.
LLM 호출 중 즉시 중단이 필요하면:
- `llm_call` 유틸에서 `asyncio.wait` + cancel 이벤트 조합
- 또는 `astream()` 전환 후 WebSocket disconnect 감지

이는 별도 이슈로 분리한다.

## 5. `astream()` 전환 검토

리서치 결과 `astream()`이 중단에 더 적��하다:
- 노드 경계 yield 지점에서 자연스러운 취소
- interrupt 이벤트를 스트림에서 직접 확인 가능
- WebSocket disconnect 시 for-loop break로 처리

단, 현재 `ainvoke()` 기반 구조에서 `astream()` ��환은 별도 이슈.
취소 플래그 방식은 `ainvoke()`/`astream()` 어느 쪽에서든 동작한다.

## 6. 미결 사항

- [ ] 방식 A(조건부 엣지) vs 방식 B(노드 시작부 체크) 선택
- [ ] Redis 미사용 환경(MemorySaver) 시 취소 플래그 대안 (in-memory dict)
- [ ] `astream()` 전환 시점 및 범위 (별도 이슈)
- [ ] LangGraph 버그 #5682, #6726, #6950 수정 추적 → 수정 시 `Task.cancel()` 재검토
- [ ] 프론트엔드 중단 버튼 UI/UX (로딩 중 취소 → 부분 결과 표시)
- [ ] 취소 후 같은 세션에서 새 질의 시 플래그 정리 타이밍

## 7. 출처

### 공식 문서
- [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [LangGraph Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [LangGraph Durable Execution](https://docs.langchain.com/oss/python/langgraph/durable-execution)
- [INVALID_CHAT_HISTORY 트러블슈팅](https://langchain-ai.github.io/langgraph/troubleshooting/errors/INVALID_CHAT_HISTORY/)

### GitHub Issues / Discussions
- [#5682: 서브그래프 CancelledError 미전파](https://github.com/langchain-ai/langgraph/issues/5682)
- [#6726: ToolNode CancelledError 미포착](https://github.com/langchain-ai/langgraph/issues/6726)
- [#6950: AsyncPregelLoop 클린업 2차 예외](https://github.com/langchain-ai/langgraph/issues/6950)
- [#2930: abort 기능 구현 논의](https://github.com/langchain-ai/langgraph/discussions/2930)
- [#5356: SDK runs.cancel() 동작 문제](https://github.com/langchain-ai/langgraph/discussions/5356)
