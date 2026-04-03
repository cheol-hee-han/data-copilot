# LangGraph Checkpointer 아키텍처 및 Best Practices 리서치

- **작성일**: 2026-03-30
- **작성자**: Research Analyst Agent
- **대상 버전**: LangGraph 1.0 (2025-10 GA), langgraph-checkpoint-postgres 최신
- **적용 컨텍스트**: Data Copilot — FastAPI + WebSocket, 폐쇄망 배포, 은행 업무 챗봇

---

## 목차

1. [체크포인터 타입 비교](#1-체크포인터-타입-비교)
2. [내부 아키텍처 — 저장 스키마](#2-내부-아키텍처--저장-스키마)
3. [Human-in-the-Loop 패턴](#3-human-in-the-loop-패턴)
4. [멀티턴 대화 패턴](#4-멀티턴-대화-패턴)
5. [Thread 관리 전략](#5-thread-관리-전략)
6. [State Schema 설계 Best Practices](#6-state-schema-설계-best-practices)
7. [오류 복구 패턴](#7-오류-복구-패턴)
8. [프로덕션 배포 — PostgresSaver 완전 가이드](#8-프로덕션-배포--postgressaver-완전-가이드)
9. [스트리밍 + 체크포인터 통합](#9-스트리밍--체크포인터-통합)
10. [기존 그래프에 체크포인터 추가 (마이그레이션)](#10-기존-그래프에-체크포인터-추가-마이그레이션)
11. [Data Copilot 적용 권고안](#11-data-copilot-적용-권고안)
12. [기각된 대안 및 이유](#12-기각된-대안-및-이유)

---

## 1. 체크포인터 타입 비교

### 1.1 전체 비교표

| 체크포인터 | 패키지 | 동기 | 비동기 | 영속성 | 동시성 | 권장 환경 |
|---|---|---|---|---|---|---|
| `InMemorySaver` | `langgraph` (내장) | O | O | X (프로세스 종료 시 소멸) | 스레드 안전 | 개발/테스트 전용 |
| `SqliteSaver` | `langgraph-checkpoint-sqlite` | O | X | O (파일) | 단일 프로세스 | 로컬 실험, 단일 서버 |
| `AsyncSqliteSaver` | `langgraph-checkpoint-sqlite` | X | O | O (파일) | 비동기 단일 프로세스 | 로컬 async 실험 |
| `PostgresSaver` | `langgraph-checkpoint-postgres` | O | 선택적 | O (DB) | 분산 가능 | 프로덕션 (sync) |
| `AsyncPostgresSaver` | `langgraph-checkpoint-postgres` | X | O | O (DB) | 분산 최적 | **프로덕션 권장** |
| `RedisSaver` | `langgraph-checkpoint-redis` | O | X | O (TTL 설정 가능) | 고속 | 실시간 저지연 |
| `AsyncRedisSaver` | `langgraph-checkpoint-redis` | X | O | O (TTL 설정 가능) | 고속 비동기 | 실시간 저지연 |
| `ShallowRedisSaver` | `langgraph-checkpoint-redis` | O | X | O (최신 1개만) | 고속 | 메모리 절약형 |

**출처**: LangGraph 공식 문서 — Persistence, DeepWiki Checkpointing Architecture, Redis 공식 블로그

### 1.2 InMemorySaver

```python
from langgraph.checkpoint.memory import MemorySaver

checkpointer = MemorySaver()
graph = builder.compile(checkpointer=checkpointer)
```

**장점**: 의존성 없음, 즉시 사용 가능, 스레드 안전(불변 내부 구조)
**단점**: 프로세스 재시작 시 전체 소멸, 메모리 사용량 무제한 증가 가능
**판정**: 개발/테스트 전용. 프로덕션 절대 금지.

### 1.3 SqliteSaver / AsyncSqliteSaver

```python
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

# Sync
with SqliteSaver.from_conn_string("checkpoints.db") as saver:
    graph = builder.compile(checkpointer=saver)

# Async
async with AsyncSqliteSaver.from_conn_string("checkpoints.db") as saver:
    graph = builder.compile(checkpointer=saver)
```

**장점**: 파일 기반 영속성, 단순 설정
**단점**: 분산 배포 불가(파일 공유 필요), SqliteSaver는 async 미지원
**판정**: 단일 서버 개발 환경 또는 PoC. 분산 배포 시 기각.

### 1.4 PostgresSaver / AsyncPostgresSaver (프로덕션 권장)

LangSmith 인프라에서 실제 사용 중인 구현체. psycopg3(psycopg) 기반.

**장점**: 분산 배포 지원, 완전한 영속성, 쿼리 가능(JSONB), 암호화 직렬화 지원
**단점**: PostgreSQL 인프라 필요, psycopg3 의존성, `autocommit=True` 필수 설정

### 1.5 RedisSaver / AsyncRedisSaver

```python
from langgraph_checkpoint_redis import RedisSaver, AsyncRedisSaver

# 동기
saver = RedisSaver(connection_string="redis://localhost:6379")
saver.setup()

# 비동기
async_saver = AsyncRedisSaver(connection_string="redis://localhost:6379")
await async_saver.setup()
```

**요구사항**: Redis 8.0+ (RedisJSON + RediSearch 내장), Redis < 8.0은 Redis Stack 필요
**성능**: 상태 저장/조회 < 1ms (sub-millisecond)
**ShallowRedisSaver**: 최신 체크포인트 1개만 저장 → 메모리 절약, 히스토리 기반 time-travel 불가
**판정**: Redis를 이미 캐시로 사용하는 환경에서 저지연 요구 시 선택. 단, 완전한 이력 필요 시 PostgresSaver 권장.

---

## 2. 내부 아키텍처 — 저장 스키마

### 2.1 3-테이블 논리 모델 (PostgresSaver 기준)

| 테이블 | PK | 역할 |
|---|---|---|
| `checkpoints` | (thread_id, checkpoint_ns, checkpoint_id) | 체크포인트 메인 레코드, 원시값 JSONB 인라인 |
| `checkpoint_blobs` | (thread_id, checkpoint_ns, channel, version) | 복잡 객체 직렬화 저장 |
| `checkpoint_writes` | (thread_id, checkpoint_ns, checkpoint_id, task_id, idx) | 적용 전 pending writes |

### 2.2 핵심 데이터 구조

```python
# Checkpoint — 그래프 상태 스냅샷
class Checkpoint(TypedDict):
    v: int                          # 버전 (현재 1)
    id: str                         # UUID v6 (시간순 정렬 가능)
    ts: str                         # ISO 8601 타임스탬프
    channel_values: dict            # 채널별 역직렬화 상태
    channel_versions: dict          # 채널별 버전 식별자
    versions_seen: dict             # 노드별 처리된 채널 버전
    updated_channels: list[str]     # 수정된 채널 목록

# CheckpointMetadata — 컨텍스트 정보
class CheckpointMetadata(TypedDict):
    source: str        # "input" | "loop" | "update" | "fork"
    step: int          # -1(초기 입력), 0+(실행 단계)
    parents: dict      # 중첩 그래프용 부모 체크포인트 ID
```

### 2.3 채널 버전 형식

```
"{step:032}.{random:016}"
```
— 32자리 제로패딩 스텝 + 16자리 랜덤. 렉시코그래픽 정렬 + 동시 쓰기 충돌 방지.

### 2.4 특수 Write 인덱스

| 인덱스 | 의미 |
|---|---|
| -1 | 노드 오류 쓰기 |
| -2 | 스케줄된 태스크 쓰기 |
| -3 | Interrupt 쓰기 |
| -4 | Resume 값 쓰기 |

### 2.5 Checkpoint 타이밍

```
입력 수신 → [input checkpoint, step=-1, source="input"]
           → 노드 A 실행 → [loop checkpoint, step=0, source="loop"]
           → 노드 B 실행 → [loop checkpoint, step=1, source="loop"]
           → update_state() 호출 → [update checkpoint, source="update"]
```

**`durability='exit'` 모드**: 그래프 완료 시에만 체크포인트 저장 → 저장 비용 절감, 중간 복구 불가.

---

## 3. Human-in-the-Loop 패턴

### 3.1 두 가지 접근 방식 비교

| 방식 | 장점 | 단점 | 권장 상황 |
|---|---|---|---|
| `interrupt_before`/`interrupt_after` (정적 중단점) | 그래프 구조에 명시, 디버깅 용이 | 동적 조건 불가 | 개발/디버깅 |
| `interrupt()` 함수 (동적 인터럽트) | 노드 내 조건부 중단 가능, 재개값 직접 수신 | LangGraph >= 0.2.57 필요 | **프로덕션 권장** |

### 3.2 정적 중단점 패턴

```python
# 컴파일 타임 설정
graph = builder.compile(
    checkpointer=checkpointer,
    interrupt_before=["tool_execution_node"],
    interrupt_after=["sql_generation_node"],
)

config = {"configurable": {"thread_id": "session-001"}}

# 1차 실행 — interrupt_before에서 정지
state = graph.invoke({"messages": [...]}, config=config)
# state["__interrupt__"] 에 중단 정보 포함

# 상태 검토 후 재개 (None 입력으로 재개)
result = graph.invoke(None, config=config)
```

### 3.3 동적 interrupt() 함수 패턴 (권장)

```python
from langgraph.types import interrupt, Command

def sql_review_node(state: AgentState) -> dict:
    """SQL 실행 전 사용자 승인을 요청하는 노드."""
    generated_sql = state["generated_sql"]

    # 실행을 일시 중단하고 사용자 입력 대기
    # interrupt()는 JSON 직렬화 가능한 값만 전달 가능
    user_decision = interrupt({
        "message": "생성된 SQL을 확인해주세요.",
        "sql": generated_sql,
        "affected_tables": state["target_tables"],
    })

    if user_decision.get("action") == "approve":
        return {"approved_sql": user_decision.get("sql", generated_sql)}
    elif user_decision.get("action") == "edit":
        return {"approved_sql": user_decision["edited_sql"]}
    else:
        return {"error": "사용자가 SQL 실행을 취소했습니다."}
```

```python
# FastAPI WebSocket 엔드포인트에서 재개
async def handle_user_approval(thread_id: str, decision: dict):
    config = {"configurable": {"thread_id": thread_id}}
    result = await graph.ainvoke(
        Command(resume=decision),
        config=config,
    )
    return result
```

### 3.4 interrupt() 사용 시 반드시 지켜야 할 규칙

**규칙 1: try/except로 감싸지 말 것**
```python
# 잘못된 패턴 — interrupt는 내부적으로 예외를 사용
try:
    answer = interrupt("질문")   # GraphInterrupt 예외 발생
except Exception:
    pass                          # 인터럽트가 무시됨

# 올바른 패턴
answer = interrupt("질문")
try:
    result = risky_operation()
except Exception as e:
    handle_error(e)
```

**규칙 2: 인터럽트 순서 일관성 유지**
```python
# 잘못된 패턴 — 조건부 인터럽트로 순서가 달라짐
name = interrupt("이름을 입력하세요")
if state.get("needs_approval"):   # 이 조건이 변하면 순서가 깨짐
    approved = interrupt("승인?")

# 올바른 패턴 — 항상 같은 순서
name = interrupt("이름을 입력하세요")
approved = interrupt("승인?")     # 조건 없이 항상 실행
```

**규칙 3: interrupt() 이전 부수 효과는 멱등(idempotent)으로 설계**
```python
# 잘못된 패턴 — 재실행 시 중복 로그 생성
audit_log.create({"action": "sql_generated", "sql": sql})
approved = interrupt("SQL을 승인하시겠습니까?")

# 올바른 패턴 — upsert 또는 조건부 실행
audit_log.upsert({"id": state["request_id"], "status": "pending"})
approved = interrupt("SQL을 승인하시겠습니까?")
```

### 3.5 다중 병렬 인터럽트 처리

```python
# 여러 병렬 노드에서 동시에 interrupt 발생 시
interrupted = await graph.ainvoke(inputs, config=config)
interrupt_payloads = interrupted["__interrupt__"]

# 각 인터럽트에 대한 응답 맵 생성
resume_map = {
    payload.id: user_responses[i]
    for i, payload in enumerate(interrupt_payloads)
}

result = await graph.ainvoke(Command(resume=resume_map), config=config)
```

---

## 4. 멀티턴 대화 패턴

### 4.1 기본 구조

LangGraph에서 멀티턴 대화는 **동일한 `thread_id`로 반복 호출**하는 방식으로 구현된다. 체크포인터가 대화 히스토리를 자동 누적한다.

```python
from langgraph.graph import StateGraph, MessagesState
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langchain_core.messages import HumanMessage

async def chat_session(graph, checkpointer, session_id: str):
    """단일 사용자 세션의 멀티턴 대화."""
    config = {"configurable": {"thread_id": session_id}}

    # 1번째 메시지
    result1 = await graph.ainvoke(
        {"messages": [HumanMessage(content="이번 달 여신 잔액을 알려줘")]},
        config=config,
    )

    # 2번째 메시지 — 이전 컨텍스트가 자동으로 포함됨
    result2 = await graph.ainvoke(
        {"messages": [HumanMessage(content="그 중 연체 건수는?")]},
        config=config,  # 동일한 thread_id
    )
    # "그"가 무엇을 가리키는지 그래프가 체크포인트에서 복원

    return result2
```

### 4.2 MessagesState와 add_messages 리듀서

```python
from langgraph.graph import MessagesState
from langgraph.graph.message import add_messages
from typing import Annotated
from langchain_core.messages import BaseMessage

# 방법 1: 내장 MessagesState 사용 (권장)
class AgentState(MessagesState):
    """메시지 누적 + 추가 필드."""
    current_sql: str
    execution_result: dict | None
    user_confirmed: bool

# 방법 2: 직접 정의
class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    current_sql: str
    execution_result: dict | None
```

`add_messages` 리듀서: 새 메시지를 덮어쓰지 않고 **누적**. 같은 `id`를 가진 메시지는 덮어씀(편집 지원).

### 4.3 대화 히스토리 조회

```python
# 현재 상태 조회
state = await graph.aget_state(config)
print(state.values["messages"])  # 전체 대화 메시지 목록

# 특정 체크포인트 조회
config_at_step = {
    "configurable": {
        "thread_id": "session-001",
        "checkpoint_id": "specific-checkpoint-uuid",
    }
}
state_at_step = await graph.aget_state(config_at_step)

# 전체 이력 조회 (time-travel)
history = [s async for s in graph.aget_state_history(config)]
for snapshot in history:
    print(f"Step {snapshot.metadata['step']}: {snapshot.values}")
```

---

## 5. Thread 관리 전략

### 5.1 thread_id 명명 규칙

```python
# 권장 패턴 — 사용자 + 세션 + 타임스탬프
thread_id = f"user:{user_id}:session:{session_uuid}"

# 예시
thread_id = "user:emp_12345:session:550e8400-e29b-41d4-a716-446655440000"
```

**원칙**: thread_id가 곧 "대화의 영속적 커서". 동일 ID로 재호출 시 이전 체크포인트에서 재개, 새 ID는 새 대화 시작.

### 5.2 Thread 라이프사이클

```
생성 → [첫 메시지 수신] → 체크포인트 연속 저장 → [세션 종료]
                                                       ↓
                                              [GC/정리 정책 적용]
```

### 5.3 Thread 정리 전략

LangGraph 공식 API: `checkpointer.delete_thread(thread_id)` (비동기: `await checkpointer.adelete_thread(thread_id)`)

```python
from datetime import datetime, timedelta

async def cleanup_old_threads(
    checkpointer: AsyncPostgresSaver,
    max_age_days: int = 30
) -> int:
    """
    오래된 스레드를 정리하는 배치 작업.

    Returns:
        삭제된 스레드 수
    """
    cutoff = datetime.utcnow() - timedelta(days=max_age_days)
    deleted_count = 0

    # PostgreSQL 직접 쿼리로 오래된 thread_id 조회
    async with checkpointer.conn.cursor() as cur:
        await cur.execute(
            """
            SELECT DISTINCT thread_id
            FROM checkpoints
            WHERE created_at < %s
            """,
            (cutoff,),
        )
        old_thread_ids = [row["thread_id"] async for row in cur]

    for thread_id in old_thread_ids:
        await checkpointer.adelete_thread(thread_id)
        deleted_count += 1

    return deleted_count
```

### 5.4 Thread 암호화 (민감 정보 포함 시)

```python
from langgraph.checkpoint.serde.encrypted import EncryptedSerializer

# AES 기반 암호화 직렬화 (금융 데이터 보호)
serde = EncryptedSerializer.from_pycryptodome_aes(key=b"32-byte-key-here")
checkpointer = AsyncPostgresSaver.from_conn_string(
    "postgresql://...",
    serde=serde,
)
```

---

## 6. State Schema 설계 Best Practices

### 6.1 핵심 원칙

| 원칙 | 설명 |
|---|---|
| 최소화 | 재현 가능한 데이터는 상태에 저장하지 말 것 |
| 명시적 타입 | TypedDict + Pydantic BaseModel 중 하나 통일 |
| 리듀서 절제 | `add_messages` 같은 누적 리듀서는 필요한 필드에만 |
| 직렬화 가능성 | 모든 필드는 JSON 직렬화 가능해야 함 |
| Pydantic 기본값 | 체크포인터 로드 시 None 필드 방어 필수 |

### 6.2 Data Copilot 적합 State 설계

```python
from typing import Annotated, Any
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage
from pydantic import BaseModel, Field

class SqlExecutionResult(BaseModel):
    """SQL 실행 결과."""
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    execution_time_ms: float = 0.0
    truncated: bool = False    # 10,000건 제한으로 잘린 경우

class DataCopilotState(TypedDict):
    """
    Data Copilot 에이전트 상태 스키마.

    체크포인터 직렬화 요구사항:
    - 모든 필드는 JSON 직렬화 가능 (Pydantic 모델 포함)
    - BaseMessage 서브클래스는 add_messages 리듀서 필수
    """
    # 누적 필드 (리듀서 사용)
    messages: Annotated[list[BaseMessage], add_messages]

    # 단순 교체 필드 (리듀서 없음 = 마지막 쓰기 승리)
    user_intent: str | None              # 파싱된 사용자 의도
    target_tables: list[str]             # 선택된 테이블 목록
    generated_sql: str | None            # 생성된 SQL
    approved_sql: str | None             # 사용자 승인된 SQL
    execution_result: dict | None        # SqlExecutionResult.model_dump()
    clarification_needed: bool           # 명확화 질문 필요 여부
    clarification_question: str | None   # 명확화 질문 내용
    error_message: str | None            # 오류 메시지
    retry_count: int                     # 재시도 횟수
```

### 6.3 주의 사항 — Pydantic 모델은 model_dump()로 저장

```python
# 잘못된 패턴 — Pydantic 객체 직접 저장 시 직렬화 실패 가능
return {"execution_result": SqlExecutionResult(columns=["col1"], rows=[])}

# 올바른 패턴 — dict로 변환 후 저장
result = SqlExecutionResult(columns=["col1"], rows=[])
return {"execution_result": result.model_dump()}

# 로드 시 복원
result_dict = state["execution_result"]
if result_dict:
    result = SqlExecutionResult(**result_dict)
```

### 6.4 리듀서 없는 필드의 동시 쓰기 충돌

병렬 노드가 동일 필드에 쓰면 **마지막 쓰기 승리** 전략이 적용된다. 병렬 실행에서 결과를 합쳐야 할 경우 커스텀 리듀서를 정의한다.

```python
from operator import add
from typing import Annotated

def merge_results(existing: list, new: list) -> list:
    """병렬 노드 결과를 병합."""
    return existing + new

class ParallelSearchState(TypedDict):
    # 병렬 검색 결과 누적
    search_results: Annotated[list[dict], merge_results]
```

---

## 7. 오류 복구 패턴

### 7.1 체크포인터 기반 내결함성

체크포인터가 매 슈퍼스텝 후 스냅샷을 저장하므로, 노드 실패 시 성공한 노드는 재실행하지 않는다.

```
노드A(성공) → 체크포인트 저장 → 노드B(실패)
                ↑
         재시작 시 이 지점에서 재개
         노드A는 재실행하지 않음
```

### 7.2 노드 수준 RetryPolicy

```python
from langgraph.pregel import RetryPolicy

# 지수 백오프 재시도 정책
retry_policy = RetryPolicy(
    max_attempts=3,
    initial_interval=1.0,   # 초기 대기 1초
    backoff_factor=2.0,     # 지수 백오프
    max_interval=10.0,      # 최대 대기 10초
    jitter=True,            # 지터 추가 (thundering herd 방지)
)

builder.add_node(
    "es_search_node",
    es_search_function,
    retry=retry_policy,
)

builder.add_node(
    "sql_execution_node",
    sql_execute_function,
    retry=RetryPolicy(max_attempts=2),  # DB 쿼리는 2회만
)
```

### 7.3 오류 상태 라우팅 패턴

```python
from langgraph.graph import StateGraph, END

def should_retry_or_fail(state: DataCopilotState) -> str:
    """오류 발생 시 라우팅 결정."""
    if state.get("error_message") and state.get("retry_count", 0) < 3:
        return "retry_node"
    elif state.get("error_message"):
        return "error_response_node"
    return "continue_node"

builder.add_conditional_edges(
    "sql_execution_node",
    should_retry_or_fail,
    {
        "retry_node": "sql_generation_node",   # SQL 재생성 시도
        "error_response_node": "error_node",
        "continue_node": "result_formatting_node",
    }
)
```

### 7.4 체크포인트에서 강제 재개

```python
async def resume_from_checkpoint(
    graph,
    thread_id: str,
    target_checkpoint_id: str | None = None,
) -> dict:
    """
    특정 체크포인트에서 실행 재개.
    target_checkpoint_id가 None이면 최신 체크포인트에서 재개.
    """
    config = {
        "configurable": {
            "thread_id": thread_id,
            **({"checkpoint_id": target_checkpoint_id}
               if target_checkpoint_id else {}),
        }
    }

    # 현재 상태 확인
    state = await graph.aget_state(config)
    print(f"재개 지점: step={state.metadata['step']}, "
          f"next={state.next}")

    # None 입력으로 재개 (새 입력 없이 중단 지점에서 계속)
    return await graph.ainvoke(None, config=config)
```

---

## 8. 프로덕션 배포 — PostgresSaver 완전 가이드

### 8.1 의존성

```toml
# pyproject.toml
[project]
dependencies = [
    "langgraph>=1.0.0",
    "langgraph-checkpoint-postgres>=2.0.0",
    "psycopg[binary,pool]>=3.1.0",    # psycopg3 + 커넥션 풀
]
```

### 8.2 FastAPI lifespan 통합 (권장 패턴)

이전 리서치(20260330-langgraph-production-patterns.md)에서 확인한 싱글턴 패턴과 통합:

```python
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from psycopg_pool import AsyncConnectionPool
from psycopg.rows import dict_row
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.graphs.data_copilot_graph import build_graph

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    """
    FastAPI 앱 수명 주기 관리.
    체크포인터 커넥션 풀을 1회 생성하고 앱 종료 시 정리.
    """
    connection_kwargs = {
        "autocommit": True,       # 필수: psycopg3 기본값이 False임
        "prepare_threshold": 0,   # PgBouncer 등 풀러 호환성
        "row_factory": dict_row,  # 딕셔너리 형태 결과
    }

    async with AsyncConnectionPool(
        conninfo=settings.POSTGRES_CHECKPOINT_URL,
        min_size=2,
        max_size=10,
        kwargs=connection_kwargs,
    ) as pool:
        await pool.wait()  # 풀 준비 완료 대기

        checkpointer = AsyncPostgresSaver(pool)
        await checkpointer.setup()  # 최초 1회: 테이블 생성

        # 그래프는 체크포인터 없이 컴파일 후 주입
        # (이전 리서치: graph.compile()은 싱글턴으로 1회만)
        graph = build_graph()
        compiled_graph = graph.compile(checkpointer=checkpointer)

        app.state.graph = compiled_graph
        app.state.checkpointer = checkpointer

        yield

        # 종료 시 자동으로 pool.__aexit__ 호출됨

app = FastAPI(lifespan=lifespan)
```

**주의**: `prepare_threshold=0` 설정은 PgBouncer, Supabase pooler 등 세션 풀러 사용 시 필수. 없으면 `InvalidSqlStatementName` 오류 발생.

### 8.3 엔드포인트에서 체크포인터 사용

```python
from fastapi import APIRouter, Request, WebSocket
from langchain_core.messages import HumanMessage
from langgraph.types import Command

router = APIRouter()

@router.websocket("/ws/chat/{thread_id}")
async def websocket_chat(
    websocket: WebSocket,
    thread_id: str,
    request: Request,
) -> None:
    """WebSocket 기반 멀티턴 대화 엔드포인트."""
    await websocket.accept()
    graph = request.app.state.graph
    config = {"configurable": {"thread_id": thread_id}}

    try:
        async for raw_message in websocket.iter_json():
            message_type = raw_message.get("type", "message")

            if message_type == "message":
                # 일반 메시지
                input_data = {
                    "messages": [HumanMessage(content=raw_message["content"])]
                }
                async for chunk in graph.astream(
                    input_data,
                    config=config,
                    stream_mode="messages",
                    version="v2",
                ):
                    if chunk["type"] == "messages":
                        msg, metadata = chunk["data"]
                        if hasattr(msg, "content") and msg.content:
                            await websocket.send_json({
                                "type": "token",
                                "content": msg.content,
                                "node": metadata.get("langgraph_node"),
                            })

            elif message_type == "resume":
                # 인터럽트 후 재개
                decision = raw_message.get("decision", {})
                async for chunk in graph.astream(
                    Command(resume=decision),
                    config=config,
                    stream_mode="updates",
                    version="v2",
                ):
                    await websocket.send_json({
                        "type": "update",
                        "data": chunk["data"],
                    })

            await websocket.send_json({"type": "done"})

    except Exception as e:
        await websocket.send_json({"type": "error", "message": str(e)})
    finally:
        await websocket.close()
```

### 8.4 PostgresSaver 내부 테이블 직접 쿼리 (운영 모니터링)

```sql
-- 활성 스레드 수 (최근 1시간)
SELECT COUNT(DISTINCT thread_id)
FROM checkpoints
WHERE created_at > NOW() - INTERVAL '1 hour';

-- 스레드별 체크포인트 수 (디버깅)
SELECT thread_id, COUNT(*) as checkpoint_count, MAX(created_at) as last_active
FROM checkpoints
GROUP BY thread_id
ORDER BY last_active DESC
LIMIT 20;

-- 인터럽트 상태 스레드 목록 (pending 승인 건)
SELECT DISTINCT thread_id
FROM checkpoint_writes
WHERE idx = -3  -- interrupt write index
  AND created_at > NOW() - INTERVAL '24 hours';
```

---

## 9. 스트리밍 + 체크포인터 통합

### 9.1 스트리밍 모드 7종 요약

| 모드 | 출력 | 체크포인터 필요 | 용도 |
|---|---|---|---|
| `values` | 각 단계 후 전체 상태 스냅샷 | 아니오 | 상태 모니터링 |
| `updates` | 변경된 필드만 | 아니오 | 효율적 상태 추적 |
| `messages` | LLM 토큰 스트림 + 메타데이터 | 아니오 | **UI 토큰 스트리밍** |
| `custom` | `get_stream_writer()` 커스텀 이벤트 | 아니오 | 진행률 표시 |
| `checkpoints` | 체크포인트 이벤트 | **필수** | 체크포인트 모니터링 |
| `tasks` | 태스크 시작/완료 이벤트 | **필수** | 태스크 추적 |
| `debug` | 체크포인트 + 태스크 + 메타데이터 | **필수** | 상세 디버깅 |

### 9.2 토큰 스트리밍 + 체크포인터 조합 (프로덕션 패턴)

```python
async def stream_with_checkpoint(
    graph,
    user_message: str,
    thread_id: str,
) -> AsyncGenerator[dict, None]:
    """
    토큰 스트리밍 + 체크포인터를 결합한 프로덕션 패턴.
    messages 모드: 토큰 스트림
    updates 모드: 상태 변화 추적
    """
    config = {"configurable": {"thread_id": thread_id}}
    input_data = {"messages": [HumanMessage(content=user_message)]}

    async for chunk in graph.astream(
        input_data,
        config=config,
        stream_mode=["messages", "updates"],  # 다중 모드 동시 사용
        version="v2",
    ):
        if chunk["type"] == "messages":
            msg, metadata = chunk["data"]
            if msg.content:
                yield {
                    "event": "token",
                    "data": msg.content,
                    "node": metadata.get("langgraph_node"),
                }

        elif chunk["type"] == "updates":
            for node_name, state_update in chunk["data"].items():
                yield {
                    "event": "state_update",
                    "node": node_name,
                    "data": state_update,
                }
```

### 9.3 async 체크포인터 + astream_events 이슈

**알려진 이슈**: `astream_events`와 `sync` 체크포인터(SqliteSaver)를 혼용하면 경고 발생.

```python
# 잘못된 조합
from langgraph.checkpoint.sqlite import SqliteSaver  # sync
async for event in graph.astream_events(...):       # async
    ...  # 경고: sync checkpointer in async context

# 올바른 조합
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver  # async
async for event in graph.astream_events(...):
    ...  # 정상
```

**권고**: 프로덕션에서는 `AsyncPostgresSaver` + `astream()` 조합 사용. `astream_events`는 더 상세하지만 오버헤드가 크다. 토큰 스트리밍은 `stream_mode="messages"`가 더 효율적.

### 9.4 커스텀 진행률 이벤트 (Data Copilot 활용)

```python
from langgraph.config import get_stream_writer

async def es_search_node(state: DataCopilotState) -> dict:
    """ES 메타 검색 노드 — 진행률 이벤트 포함."""
    writer = get_stream_writer()

    writer({"progress": "테이블 메타 정보를 검색 중입니다..."})
    tables = await search_es_metadata(state["user_intent"])

    writer({"progress": f"{len(tables)}개 테이블 후보를 찾았습니다."})
    return {"target_tables": tables}
```

```python
# 클라이언트 측 수신
async for chunk in graph.astream(
    input_data,
    config=config,
    stream_mode=["messages", "custom"],
    version="v2",
):
    if chunk["type"] == "custom":
        await websocket.send_json({
            "event": "progress",
            "message": chunk["data"].get("progress"),
        })
```

---

## 10. 기존 그래프에 체크포인터 추가 (마이그레이션)

### 10.1 체크포인터 추가 자체는 하위 호환

그래프 구조 변경 없이 `compile()` 시 `checkpointer=` 인자만 추가하면 된다.

```python
# 기존 코드 (체크포인터 없음)
graph = builder.compile()
result = graph.invoke({"messages": [...]})

# 마이그레이션 후 (체크포인터 추가)
graph = builder.compile(checkpointer=checkpointer)
# thread_id 없으면 체크포인터 없는 것과 동일하게 동작
result = graph.invoke({"messages": [...]})  # thread_id 없음 → 체크포인트 미저장

# thread_id 포함 시 체크포인터 활성화
result = graph.invoke(
    {"messages": [...]},
    config={"configurable": {"thread_id": "session-001"}},
)
```

### 10.2 직렬화 호환성 위험 — 모듈 경로 변경 금지

```python
# 위험 시나리오: 배포 중 모듈 경로 변경
# 배포 전: app.states.agent_state.DataCopilotState
# 배포 후: app.core.state.DataCopilotState

# JSONPlusSerializer는 모듈 경로를 직렬화에 포함함
# 롤링 배포(rolling deploy) 시 구/신 버전 혼재 → 역직렬화 실패
```

**대응 전략**:
1. State 클래스 이동 시 기존 경로에 `import` 별칭 유지 (deprecated shim)
2. 블루-그린 배포로 버전 혼재 방지
3. 마이그레이션 기간 동안 thread 단위로 드레인(새 세션은 새 버전, 기존 세션 완료 후 구 버전 종료)

### 10.3 체크포인터 교체 마이그레이션 (MemorySaver → PostgresSaver)

기존 체크포인트는 새 백엔드로 자동 이전되지 않는다. **신규 세션은 새 체크포인터, 기존 세션은 만료 처리** 전략을 권장한다.

```python
import os

def get_checkpointer() -> AsyncPostgresSaver | MemorySaver:
    """
    환경에 따라 체크포인터를 선택.
    환경변수로 전환 제어.
    """
    if os.getenv("USE_POSTGRES_CHECKPOINTER", "false").lower() == "true":
        return _create_postgres_checkpointer()
    return MemorySaver()
```

---

## 11. Data Copilot 적용 권고안

### 11.1 권고 체크포인터 선택

| 환경 | 권고 체크포인터 | 이유 |
|---|---|---|
| 온라인 개발 | `AsyncPostgresSaver` | 기존 PostgreSQL 인프라 활용, 프로덕션과 동일 환경 |
| 폐쇄망 배포 | `AsyncPostgresSaver` | 동일. PostgreSQL은 폐쇄망에서도 구축 가능 |
| 테스트 | `MemorySaver` | 의존성 없음, 빠른 테스트 |

**기각된 대안**:
- `RedisSaver`: Redis는 이미 캐시로 사용 중이나, 체크포인트 히스토리가 필요하고 time-travel 디버깅을 위해 PostgresSaver 선택
- `SqliteSaver`: 분산 배포(FastAPI 다중 워커) 불가

### 11.2 Human-in-the-Loop 적용 포인트

Data Copilot에서 인터럽트가 필요한 노드:

```
1. [SQL 승인 노드]: 생성된 SQL을 사용자에게 표시하고 승인/수정 대기
2. [명확화 질문 노드]: 모호한 질의에 대해 추가 정보 요청
3. [대용량 쿼리 경고 노드]: 10,000건 초과 예상 시 사용자 확인
```

```python
def sql_approval_node(state: DataCopilotState) -> dict:
    """생성된 SQL 사용자 승인 노드."""
    # interrupt()는 try/except 없이 독립 실행
    decision = interrupt({
        "type": "sql_approval",
        "sql": state["generated_sql"],
        "estimated_rows": state.get("estimated_rows"),
        "message": "아래 SQL로 데이터를 조회하겠습니다. 확인해주세요.",
    })

    if decision["action"] == "approve":
        return {"approved_sql": state["generated_sql"]}
    elif decision["action"] == "edit":
        return {"approved_sql": decision["edited_sql"]}
    else:
        return {"error_message": "사용자가 SQL 조회를 취소했습니다."}
```

### 11.3 스트리밍 권고 패턴

```python
# WebSocket 메시지 타입 설계
{
    "type": "token",          # LLM 생성 토큰
    "type": "progress",       # 노드별 진행 상황 (custom 모드)
    "type": "state_update",   # 상태 변화 (updates 모드)
    "type": "interrupt",      # 인터럽트 발생 (승인 요청)
    "type": "done",           # 실행 완료
    "type": "error",          # 오류
}
```

### 11.4 은행 업무 특화 고려사항

1. **감사 추적**: 체크포인트의 `metadata` 필드에 사용자 ID, 부서, IP를 기록
   ```python
   config = {
       "configurable": {"thread_id": thread_id},
       "metadata": {
           "user_id": current_user.employee_id,
           "department": current_user.department,
       }
   }
   ```

2. **개인정보 보호**: State에 개인정보 포함 시 `EncryptedSerializer` 필수 적용

3. **TTL 관리**: 금융 데이터 보존 정책에 따라 체크포인트 보존 기간 설정 (예: 30일 후 자동 삭제)

4. **폐쇄망 호환**: `AsyncPostgresSaver`는 외부 네트워크 의존성 없음. 순수 psycopg3 + PostgreSQL.

---

## 12. 기각된 대안 및 이유

| 대안 | 기각 이유 |
|---|---|
| `MemorySaver` (프로덕션) | 프로세스 재시작 시 전체 대화 소멸, 서버 재배포 불가 |
| `SqliteSaver` (프로덕션) | 다중 워커(FastAPI) 환경에서 파일 잠금 충돌 |
| `ShallowRedisSaver` | 히스토리 없어 time-travel 디버깅 불가, 인터럽트 이력 추적 불가 |
| `interrupt_before/after` (유일한 HitL 방법) | 동적 조건 기반 중단 불가, 노드 외부에서만 제어 가능 |
| `astream_events` (토큰 스트리밍) | `stream_mode="messages"` 대비 오버헤드 크고, sync 체크포인터와 혼용 경고 |
| LangGraph Platform (Cloud) | 폐쇄망 배포 불가. 자체 호스팅 필수 |
| `durability='exit'` 모드 | 중간 체크포인트 없어 노드 실패 시 전체 재실행 필요 |

---

## 참고 문헌

### 공식 문서 (Tier 1)
1. LangGraph Persistence 공식 문서 — https://docs.langchain.com/oss/python/langgraph/persistence
2. LangGraph Interrupts 공식 문서 — https://docs.langchain.com/oss/python/langgraph/interrupts
3. LangGraph Streaming 공식 문서 — https://docs.langchain.com/oss/python/langgraph/streaming
4. DeepWiki — LangGraph Checkpointing Architecture — https://deepwiki.com/langchain-ai/langgraph/4.1-checkpointing-architecture
5. langgraph-checkpoint-postgres PyPI — https://pypi.org/project/langgraph-checkpoint-postgres/
6. langgraph-checkpoint-redis PyPI — https://pypi.org/project/langgraph-checkpoint-redis/

### 기술 블로그 및 사례 (Tier 2)
7. Redis 공식 블로그 — LangGraph & Redis (2025) — https://redis.io/blog/langgraph-redis-build-smarter-ai-agents-with-memory-persistence/
8. LangChain 블로그 — interrupt() 함수 소개 — https://blog.langchain.com/making-it-easier-to-build-human-in-the-loop-agents-with-interrupt/
9. LangGraph 1.0 GA 발표 — https://changelog.langchain.com/announcements/langgraph-1-0-is-now-generally-available
10. LangGraph Platform GA 발표 — https://blog.langchain.com/langgraph-platform-ga/
11. Thanos Aidinis — FastAPI + PostgreSQL 챗봇 구현 — https://medium.com/@thanos.aidinis/how-to-build-an-agentic-chatbot-with-fastapi-and-postgresql-022f199b0fa0
12. DEV Community — LangGraph Streaming 5가지 모드 — https://dev.to/sreeni5018/langgraph-streaming-101-5-modes-to-build-responsive-ai-applications-4p3f
13. DEV Community — RetryPolicy 가이드 — https://dev.to/aiengineering/a-beginners-guide-to-handling-errors-in-langgraph-with-retry-policies-h22

### GitHub Issues (Tier 2)
14. GitHub #2755 — AsyncPostgresSaver psycopg.errors.InvalidSqlStatementName — https://github.com/langchain-ai/langgraph/issues/2755
15. GitHub Discussion #6194 — Connection pool for Async/PostgresSaver — https://github.com/langchain-ai/langgraph/discussions/6194
