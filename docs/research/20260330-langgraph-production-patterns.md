# LangGraph Production 구조 패턴 리서치

**작성일**: 2026-03-30
**작성자**: Research Analyst Agent
**분류**: 아키텍처 / 에이전트 프레임워크

---

## 요약 (Executive Summary)

LangGraph `StateGraph.compile()` 결과물은 **스레드 세이프(thread-safe)한 불변 객체**로, 모듈 수준(또는 FastAPI lifespan)에서 1회 컴파일 후 전체 요청 생애에 걸쳐 재사용하는 것이 공식 권장 패턴이다. 트레이싱은 그래프를 재빌드하지 않고, 요청 호출 시 `config={"callbacks": [...], "run_id": ..., "tags": [...]}` 형태의 `RunnableConfig`를 주입하는 방식으로 처리한다.

---

## 1. 그래프 컴파일 및 재사용 패턴

### 1.1 공식 입장: 그래프는 불변 싱글턴

LangGraph 공식 GitHub Discussion(#1211, #1454)에서 메인테이너가 명시적으로 확인한 내용:

> "It is entirely safe to share a graph between executions, whether they happen concurrently or not, whether in same thread or not. **No state is ever stored on the graph instance**, and the graph instance **isn't ever mutated in any way** during any execution of the graph."
> — langchain-ai/langgraph Discussion #1211

핵심 설계 특성:
- 컴파일된 그래프는 **불변(immutable)** — 런타임에 수정 불가
- 노드 연결, 사이클, 실행 경로 최적화는 컴파일 시점에 완료
- 요청별 상태(State)는 `Checkpointer`가 별도 저장소에 관리 — 그래프 인스턴스와 무관

### 1.2 권장 패턴: FastAPI lifespan + 모듈 수준 싱글턴

**패턴 A: FastAPI lifespan 글로벌 초기화** (단순 앱)

```python
# app.py
from contextlib import asynccontextmanager
from langgraph.graph import StateGraph
from langgraph.checkpoint.mongodb.aio import AsyncMongoDBSaver
from fastapi import FastAPI

graph: CompiledStateGraph | None = None
checkpointer_cm = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global graph, checkpointer_cm
    # Checkpointer는 async context manager로 관리
    checkpointer_cm = AsyncMongoDBSaver.from_conn_string(DB_URI)
    checkpointer = await checkpointer_cm.__aenter__()

    builder = StateGraph(AgentState)
    builder.add_node("interpret", interpret_node)
    builder.add_node("generate", generate_node)
    # ... 노드/엣지 추가
    graph = builder.compile(checkpointer=checkpointer)
    yield
    if checkpointer_cm:
        await checkpointer_cm.__aexit__(None, None, None)

app = FastAPI(lifespan=lifespan)
```

출처: langchain-ai/langgraph Discussion #4720 (AsyncMongoDBSaver singleton 패턴)

**패턴 B: app.state 주입 패턴** (멀티에이전트 앱, 권장)

```python
# agent_service_toolkit 방식 (JoshuaC215/agent-service-toolkit)
@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncPostgresSaver.from_conn_string(DB_URI) as saver:
        await saver.setup()
        async with AsyncPostgresStore.from_conn_string(DB_URI) as store:
            await store.setup()
            # 각 에이전트에 checkpointer/store 주입 후 app.state에 저장
            for agent_key in get_all_agent_keys():
                agent = get_agent(agent_key)
                agent.checkpointer = saver
                agent.store = store
            app.state.agents = agents  # FastAPI app.state에 저장
            yield
```

**패턴 C: 모듈 임포트 시점 컴파일** (경량 앱, 가장 단순)

```python
# agent.py — 모듈 임포트 시 컴파일 완료
from langgraph.graph import StateGraph, END

builder = StateGraph(AgentState)
builder.add_node(...)
builder.set_entry_point(...)

# 모듈 수준에서 컴파일 — 이 변수를 전체 앱이 공유
graph = builder.compile()
```

```python
# main.py
from agent import graph  # 이미 컴파일된 객체를 임포트

@app.post("/chat")
async def chat(request: ChatRequest):
    result = await graph.ainvoke(
        {"messages": [request.message]},
        config={"configurable": {"thread_id": request.session_id}}
    )
    return result
```

### 1.3 기각된 대안: 요청별 재컴파일

**기각 이유**:
1. `StateGraph.compile()`은 노드 연결 검증, 사이클 탐지, 실행 경로 최적화를 수행 — 수십 ms 오버헤드
2. 그래프 인스턴스에 상태가 없으므로 재컴파일 자체가 무의미
3. 컴파일에 사용하는 Checkpointer 연결 풀이 요청마다 재생성되어 연결 고갈 위험

---

## 2. 트레이싱/인스트루멘테이션 패턴

### 2.1 계층별 옵션 비교

| 방식 | 구현 난이도 | 폐쇄망 적용 가능 | 세부 제어 | 추천 용도 |
|------|------------|----------------|----------|----------|
| LangSmith 환경변수 | 매우 낮음 | 불가 (외부 SaaS) | 낮음 | 개발/테스트 |
| `config callbacks` 주입 | 낮음 | 가능 | 중간 | 프로덕션 기본 |
| 커스텀 CallbackHandler | 중간 | 가능 | 높음 | 폐쇄망 프로덕션 |
| OpenTelemetry + OpenInference | 높음 | 가능 | 매우 높음 | 엔터프라이즈 |
| `@traceable` 데코레이터 | 낮음 | LangSmith 필요 | 중간 | LangSmith 환경 |

### 2.2 방식 1: 환경변수 자동 트레이싱 (LangSmith, 개발용)

```bash
export LANGSMITH_TRACING=true
export LANGSMITH_API_KEY=<key>
```

그래프 코드 변경 없이 모든 LangChain/LangGraph 호출이 자동 추적된다. 단, LangSmith 외부 SaaS에 전송되므로 **폐쇄망 환경에서는 사용 불가**.

### 2.3 방식 2: 호출 시 config callbacks 주입 (핵심 패턴)

공유 그래프 인스턴스에 요청별 트레이싱 컨텍스트를 주입하는 방법. **그래프 재컴파일 없음**.

```python
import uuid
from langfuse.langchain import CallbackHandler  # 또는 커스텀 핸들러

async def handle_request(user_query: str, session_id: str):
    # 요청마다 고유 run_id 생성
    request_run_id = uuid.uuid4()

    # 트레이싱 핸들러 생성 (요청별)
    trace_handler = CallbackHandler(
        session_id=session_id,
        user_id=request.user_id,
    )

    # 공유 그래프에 config만 주입 — 재컴파일 없음
    result = await graph.ainvoke(
        {"messages": [user_query]},
        config={
            "configurable": {"thread_id": session_id},
            "callbacks": [trace_handler],
            "run_id": request_run_id,
            "run_name": f"data_copilot/{user_query[:30]}",
            "tags": ["production", "nl2sql"],
            "metadata": {"user_id": session_id, "env": "prod"},
        }
    )
    return result
```

`RunnableConfig` 주요 필드 (langchain_core 기준):
- `callbacks`: 이 호출 및 모든 하위 호출에 적용될 핸들러 목록
- `run_id`: 루트 추적 ID (LangSmith에서 trace_id로 사용됨)
- `run_name`: UI에 표시될 실행 이름
- `tags`: 필터링용 레이블 (부모 → 자식 상속)
- `metadata`: 임의 키-값 쌍 (부모 → 자식 상속)
- `configurable`: thread_id 등 그래프 내부 설정값

### 2.4 방식 3: 서버 수준 with_config() (LangGraph Server/전역 적용)

```python
# 앱 시작 시 한 번 — 모든 호출에 항상 적용
langfuse_handler = CallbackHandler()
graph_with_tracing = compiled_graph.with_config({
    "callbacks": [langfuse_handler]
})

# 이후 graph_with_tracing을 사용하면 자동으로 추적됨
result = await graph_with_tracing.ainvoke(state, config={"configurable": {...}})
```

출처: Langfuse LangGraph 통합 문서 (langfuse.com/guides/cookbook/integration_langgraph)

### 2.5 방식 4: 커스텀 CallbackHandler (폐쇄망 권장)

LangChain의 `BaseCallbackHandler`를 상속하여 구현한다. LangSmith 없이 내부 시스템(예: Kafka, PostgreSQL, OpenTelemetry Collector)으로 트레이스를 전송할 수 있다.

```python
from langchain_core.callbacks import BaseCallbackHandler
from typing import Any
import uuid

class InternalTraceHandler(BaseCallbackHandler):
    """폐쇄망용 내부 트레이싱 핸들러."""

    def __init__(self, session_id: str, request_id: str):
        self.session_id = session_id
        self.request_id = request_id
        self.node_timings: dict[str, float] = {}

    def on_chain_start(
        self, serialized: dict[str, Any], inputs: dict[str, Any],
        *, run_id: uuid.UUID, tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None, **kwargs: Any
    ) -> None:
        """노드/체인 시작 시 호출."""
        node_name = metadata.get("langgraph_node", "unknown") if metadata else "unknown"
        # 내부 추적 시스템으로 전송
        self._emit_span_start(node_name, run_id)

    def on_chain_end(
        self, outputs: dict[str, Any],
        *, run_id: uuid.UUID, **kwargs: Any
    ) -> None:
        """노드/체인 종료 시 호출."""
        self._emit_span_end(run_id)

    def on_chain_error(
        self, error: BaseException,
        *, run_id: uuid.UUID, **kwargs: Any
    ) -> None:
        """오류 발생 시 호출."""
        self._emit_span_error(run_id, str(error))

    def _emit_span_start(self, node_name: str, run_id: uuid.UUID) -> None:
        # 내부 로깅/추적 시스템 연동
        pass
```

LangGraph 메타데이터 접근 팁:
- `metadata["langgraph_node"]`: 현재 노드 이름
- `metadata["langgraph_checkpoint_ns"]`: 체크포인트 네임스페이스
- `metadata["langgraph_step"]`: 실행 스텝 번호

### 2.6 방식 5: OpenTelemetry 직접 계측

```python
from opentelemetry import trace
from langchain_core.callbacks import BaseCallbackHandler

tracer = trace.get_tracer("data-copilot")

class OtelCallbackHandler(BaseCallbackHandler):
    """OpenTelemetry 기반 트레이싱 핸들러."""

    spans: dict[str, trace.Span] = {}

    def on_chain_start(self, serialized, inputs, *, run_id, metadata=None, **kw):
        node = (metadata or {}).get("langgraph_node", "chain")
        span = tracer.start_span(f"langgraph.node.{node}")
        span.set_attribute("langgraph.node.name", node)
        span.set_attribute("session.id", self.session_id)
        self.spans[str(run_id)] = span

    def on_chain_end(self, outputs, *, run_id, **kw):
        span = self.spans.pop(str(run_id), None)
        if span:
            span.end()
```

출처: Last9 "Instrument LangChain and LangGraph Apps with OpenTelemetry" (2025)

---

## 3. LangGraph 공식 tracing 권장 경로

LangSmith 문서(docs.langchain.com/langsmith/trace-with-langgraph)에서 명시하는 우선순위:

1. **환경변수 방식**: LangChain 모듈이 내부적으로 LangSmith 클라이언트를 자동 초기화. 코드 변경 없음. 외부망 전용.
2. **per-invocation config callbacks**: 외부망/폐쇄망 모두 가능. `config={"callbacks": [handler]}`
3. **`@traceable` 데코레이터**: LangChain 비통합 커스텀 함수(예: 외부 API 호출, 순수 Python 로직)에 적용

### 폐쇄망 환경 권장 조합

```
환경변수 방식 기각 (LangSmith SaaS 불가)
    → config callbacks + 커스텀 BaseCallbackHandler
    → 내부 추적 시스템 (PostgreSQL 로그 테이블 or OTel Collector)
```

---

## 4. FastAPI + LangGraph 실전 구조

### 4.1 agent-service-toolkit 구조 분석

JoshuaC215/agent-service-toolkit (공식 LangGraph 커뮤니티 참조 구현):

```
src/
  agents/
    agents.py         # 모든 에이전트 사전 컴파일 + 딕셔너리 관리
    chatbot.py        # graph = builder.compile()  ← 모듈 임포트 시 컴파일
    research_assistant.py
  service/
    service.py        # FastAPI lifespan에서 checkpointer/store 주입
```

에이전트 등록 패턴:
```python
# agents.py
from .chatbot import graph as chatbot_graph
from .research_assistant import graph as research_graph

# 모듈 수준 딕셔너리 — 앱 전체 생애 공유
agents: dict[str, Agent] = {
    "chatbot": Agent(description="...", graph=chatbot_graph),
    "research-assistant": Agent(description="...", graph=research_graph),
}
```

lifespan에서 checkpointer 주입:
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncPostgresSaver.from_conn_string(DB_URI) as saver:
        await saver.setup()
        for agent in agents.values():
            # 이미 컴파일된 그래프에 checkpointer 사후 주입 가능
            agent.graph.checkpointer = saver
        app.state.agents = agents
        yield
```

### 4.2 langgraph.json (LangGraph Platform/Server 배포 시)

자체 FastAPI 없이 LangGraph Server(LangSmith Deployment)를 사용할 경우 진입점 설정:

```json
{
  "dependencies": ["./src", "langchain_anthropic"],
  "graphs": {
    "data_copilot": "./src/agents/graph/pipeline.py:graph"
  },
  "env": "./.env"
}
```

- `graphs` 키의 값: `파일경로:변수명` — 컴파일된 `CompiledStateGraph` 변수를 가리킴
- LangGraph Server가 이 변수를 로드하여 HTTP API로 노출
- 각 요청은 `thread_id`로 격리됨

---

## 5. Data Copilot 적용 권고

### 5.1 그래프 컴파일 전략

현재 `src/agents/graph/runner.py`와 `pipeline.py` 구조 기준:

**권고**: `pipeline.py` 모듈 수준에서 컴파일, FastAPI lifespan에서 checkpointer 주입

```python
# src/agents/graph/pipeline.py

# 빌더만 모듈 수준에서 정의
builder = StateGraph(AgentState)
builder.add_node("interpret", interpret_node)
builder.add_node("plan", planner_node)
# ... 나머지 노드/엣지

# checkpointer 없이 먼저 컴파일 (startup에서 주입)
graph = builder.compile()
```

```python
# src/main.py (FastAPI)
from contextlib import asynccontextmanager
from src.agents.graph.pipeline import graph

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncPostgresSaver.from_conn_string(settings.DB_URI) as saver:
        await saver.setup()
        graph.checkpointer = saver  # 사후 주입
        yield

app = FastAPI(lifespan=lifespan)
```

### 5.2 트레이싱 전략 (폐쇄망 대응)

**권고**: `BaseCallbackHandler` 상속 커스텀 핸들러 + config 주입 방식

구현 위치: `src/utils/tracker/`에 `InternalTraceHandler` 추가

```python
# src/utils/tracker/callback_handler.py
class DataCopilotTraceHandler(BaseCallbackHandler):
    """Data Copilot 내부 추적 핸들러 — 폐쇄망 호환."""

    def __init__(self, session_id: str, user_id: str | None = None):
        self.session_id = session_id
        self.user_id = user_id
        self.trace_id = str(uuid.uuid4())
        self._node_spans: dict[str, NodeSpan] = {}
```

```python
# API 엔드포인트에서 사용
@router.post("/chat")
async def chat(request: ChatRequest):
    handler = DataCopilotTraceHandler(
        session_id=request.session_id,
        user_id=request.user_id
    )
    result = await graph.ainvoke(
        state,
        config={
            "configurable": {"thread_id": request.session_id},
            "callbacks": [handler],
            "run_name": f"data_copilot",
            "tags": ["nl2sql", "production"],
        }
    )
    return result
```

### 5.3 기각된 대안 정리

| 대안 | 기각 이유 |
|------|----------|
| 요청별 `StateGraph.compile()` | 불필요한 오버헤드, 연결풀 고갈 위험 |
| LangSmith 환경변수 트레이싱 | 폐쇄망 외부 SaaS 접근 불가 |
| 노드 함수에 직접 트레이싱 데코레이터 | 그래프 컴파일 전 콜백 시스템 우회, 관심사 분리 위반 |
| LangGraph Server(LangSmith Deployment) | 외부 호스팅 서비스, 폐쇄망 불가 |

---

## 6. 검증된 출처 요약

### Tier 1 (공식 문서/공식 GitHub)

1. **LangGraph GitHub Discussion #1211**: "Is a LangGraph compiled graph thread-safe?" — 메인테이너 확인, 스레드 세이프 명시
   - URL: https://github.com/langchain-ai/langgraph/discussions/1211

2. **LangGraph GitHub Discussion #1454**: "Is CompiledStateGraph Thread safe and How to Use MemorySaver" — 싱글턴 패턴 공식 확인
   - URL: https://github.com/langchain-ai/langgraph/discussions/1454

3. **LangGraph GitHub Discussion #4720**: "Is it okay to keep an async checkpointer as singleton?" — AsyncMongoDBSaver singleton 패턴
   - URL: https://github.com/langchain-ai/langgraph/discussions/4720

4. **LangSmith 공식 문서 - Trace with LangGraph**: per-request 트레이싱 config 옵션
   - URL: https://docs.langchain.com/langsmith/trace-with-langgraph

5. **LangGraph Application Structure 공식 문서**: langgraph.json 구조
   - URL: https://docs.langchain.com/oss/python/langgraph/application-structure

### Tier 2 (참조 구현/커뮤니티 검증)

6. **JoshuaC215/agent-service-toolkit**: 공식 LangGraph 커뮤니티 참조 구현
   - URL: https://github.com/JoshuaC215/agent-service-toolkit

7. **Langfuse LangGraph Integration 가이드**: config callbacks 패턴 코드 예시
   - URL: https://langfuse.com/guides/cookbook/integration_langgraph

8. **Last9 OpenTelemetry LangGraph 계측 가이드**: OTel 커스텀 트레이싱
   - URL: https://last9.io/blog/langchain-and-langgraph-instrumentation-guide/
