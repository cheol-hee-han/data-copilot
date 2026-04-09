# LangGraph 병렬 실행: Fan-out/Fan-in 패턴 완전 분석

- 작성일: 2026-04-04
- 작성자: Research Analyst Agent
- 상태: 완료
- 관련 이슈: Data Copilot 컨텍스트 수집 파이프라인 병렬화 설계

---

## 요약 (Executive Summary)

LangGraph는 Fan-out/Fan-in을 세 가지 다른 메커니즘으로 지원한다.

| 패턴 | 분기 수 결정 시점 | Barrier 보장 | 체크포인팅 | 주요 위험 |
|---|---|---|---|---|
| 정적 Fan-out (add_edge) | 설계 시 고정 | 자동 (superstep) | O | 비균형 브랜치 시 중복 실행 |
| 동적 Fan-out (Send API) | 런타임 결정 | 자동 (superstep) | O | 브랜치 수 무제한 → rate limit |
| 단일 노드 asyncio.gather | 런타임 결정 | Python 레벨 | X (내부 불투명) | 개별 실패 추적 불가 |

**핵심 권고: Data Copilot 컨텍스트 수집(MongoDB + Qdrant + SQL History 병렬 조회)에는 정적 Fan-out이 적합하다.** 분기 수가 설계 시 고정되고, 체크포인팅이 보장되며, 구현 복잡도가 가장 낮다.

---

## 1. LangGraph 실행 모델: Superstep

### 1.1 Pregel 기반 Bulk Synchronous Parallel (BSP)

LangGraph는 Google Pregel에서 영감을 받은 BSP 실행 모델을 사용한다. 실행의 기본 단위는 **superstep**이다.

```
Superstep N:
  - 이전 superstep에서 edge가 활성화된 모든 노드를 동시 실행
  - 모든 노드 완료 대기 (barrier)
  - 상태 reducer 적용 (병렬 업데이트 병합)
  
Superstep N+1:
  - 상태 업데이트를 받은 edge가 활성화된 노드들 실행
```

출처: [LangGraph's Execution Model is Trickier Than You Might Think (Atomic Object, 2025)](https://spin.atomicobject.com/langgraphs-execution-model-tricky/)

**중요한 함의:** Fan-in은 별도 구현이 필요하지 않다. 다운스트림 노드는 자신에게 연결된 모든 상위 노드가 같은 superstep에서 완료될 때까지 자동으로 대기한다.

### 1.2 비균형 브랜치의 함정

```
START --> A --> C (짧은 브랜치)
START --> B --> D --> C (긴 브랜치)
```

이 구조에서 C는 두 번 실행된다:
1. A가 완료된 superstep에서 1회
2. D가 완료된 다음 superstep에서 1회

**해결책:** 비균형 브랜치는 서브그래프로 캡슐화하여 각 브랜치의 깊이를 외부에서 동일하게 보이도록 만든다.

```python
# 짧은 브랜치를 서브그래프로 감싸서 균형 맞추기
fast_subgraph = StateGraph(SubState)
fast_subgraph.add_edge(START, "fast_node")
fast_subgraph.add_edge("fast_node", END)

main_graph.add_node("fast_chain", fast_subgraph.compile())
main_graph.add_node("slow_chain", slow_subgraph.compile())
```

출처: [Superstep parallelism: a slow sibling blocks fast branch #6320 (GitHub)](https://github.com/langchain-ai/langgraph/issues/6320)

---

## 2. 패턴 A: 정적 Fan-out (Static Parallel Edges)

### 2.1 메커니즘

설계 시 분기 수를 고정하고 `add_edge`로 여러 목적지 노드를 지정한다. LangGraph가 동일 소스에서 복수 엣지를 감지하면 해당 목적지들을 같은 superstep에서 병렬 실행한다.

### 2.2 상태 Reducer 없이 스칼라 키 충돌 발생

```python
# 이 패턴은 INVALID_CONCURRENT_GRAPH_UPDATE 오류 발생
class State(TypedDict):
    result: str  # reducer 없음 — 병렬 쓰기 불가

def node_a(state): return {"result": "from_a"}
def node_b(state): return {"result": "from_b"}

builder.add_edge(START, "node_a")
builder.add_edge(START, "node_b")
# 오류: 두 노드가 같은 step에서 'result'에 동시 쓰기
```

출처: [INVALID_CONCURRENT_GRAPH_UPDATE - LangChain Docs](https://docs.langchain.com/oss/python/langgraph/errors/INVALID_CONCURRENT_GRAPH_UPDATE)

### 2.3 Reducer 패턴으로 해결

```python
from typing import Annotated, TypedDict
import operator
from langgraph.graph import StateGraph, START, END

# 핵심: Annotated[list, operator.add] — 병렬 업데이트를 리스트로 누적
class ContextState(TypedDict):
    query: str
    context_items: Annotated[list[str], operator.add]  # reducer 지정
    final_answer: str

def fetch_from_mongodb(state: ContextState) -> dict:
    result = mongodb_client.search(state["query"])
    return {"context_items": [f"[META] {result}"]}

def fetch_from_qdrant(state: ContextState) -> dict:
    result = qdrant_client.search(state["query"])
    return {"context_items": [f"[RAG] {result}"]}

def fetch_sql_history(state: ContextState) -> dict:
    result = sql_history_search(state["query"])
    return {"context_items": [f"[SQL] {result}"]}

def generate_sql(state: ContextState) -> dict:
    # 이 시점에서 context_items는 세 브랜치 결과가 모두 병합된 리스트
    all_context = "\n".join(state["context_items"])
    answer = llm.invoke(f"Context:\n{all_context}\n\nQuery: {state['query']}")
    return {"final_answer": answer}

# 그래프 구성
builder = StateGraph(ContextState)
builder.add_node("fetch_mongodb", fetch_from_mongodb)
builder.add_node("fetch_qdrant", fetch_from_qdrant)
builder.add_node("fetch_sql_history", fetch_sql_history)
builder.add_node("generate_sql", generate_sql)

# Fan-out: START -> 3개 병렬 노드
builder.add_edge(START, "fetch_mongodb")
builder.add_edge(START, "fetch_qdrant")
builder.add_edge(START, "fetch_sql_history")

# Fan-in: 3개 병렬 노드 -> generate_sql (barrier 자동)
builder.add_edge("fetch_mongodb", "generate_sql")
builder.add_edge("fetch_qdrant", "generate_sql")
builder.add_edge("fetch_sql_history", "generate_sql")
builder.add_edge("generate_sql", END)

graph = builder.compile()
```

**동작 원리:**
- `generate_sql` 노드는 세 상위 노드 모두 완료되어야 실행됨 (자동 barrier)
- `operator.add` reducer가 각 브랜치의 `["result"]` 리스트를 concatenate
- 추가 동기화 코드 불필요

출처:
- [Parallel workflows in LangGraph: A Practical Approach (Medium)](https://medium.com/@ameejais0999/parallel-workflows-in-langgraph-a-practical-approach-6e4340ceb8d4)
- [Parallelization Techniques - LangChain Academy DeepWiki](https://deepwiki.com/langchain-ai/langchain-academy/7.3-parallelization-techniques)

### 2.4 List-to-join에서 add_edge 사용법 (fan-in 명시)

LangGraph는 `add_edge(["node_a", "node_b", "node_c"], "sink_node")` 문법을 지원하여 명시적 barrier를 선언할 수 있다.

```python
# 방법 1: 개별 edge (앞 예시 — 동일 효과)
builder.add_edge("fetch_mongodb", "generate_sql")
builder.add_edge("fetch_qdrant", "generate_sql")

# 방법 2: 리스트 문법 (명시적 AND 조건)
builder.add_edge(["fetch_mongodb", "fetch_qdrant", "fetch_sql_history"], "generate_sql")
```

주의: 리스트 문법과 개별 edge를 혼합하면 스케줄링 모호성 버그가 발생한다.

출처: [Node with multiple incoming edges not executed correctly #3249 (GitHub)](https://github.com/langchain-ai/langgraph/issues/3249)

---

## 3. 패턴 B: 동적 Fan-out (Send API / Map-Reduce)

### 3.1 Send API 동작 원리

`Send`는 conditional edge 함수 내에서 반환하는 특수 객체다. 런타임에 분기 수가 결정될 때 사용한다.

```python
from langgraph.types import Send
from langgraph.constants import Send  # 동일 (alias)

# conditional edge 함수가 List[Send]를 반환 -> LangGraph가 병렬 실행
def dispatch_tasks(state: OverallState) -> list[Send]:
    return [
        Send("process_item", {"item": item, "config": state["config"]})
        for item in state["items"]  # 런타임에 결정된 수
    ]
```

`Send(node_name, state_dict)` 형태로 각 인스턴스에 **독립된 상태**를 전달한다는 점이 정적 fan-out과의 핵심 차이다.

### 3.2 완전한 Map-Reduce 예시

```python
from typing import Annotated, TypedDict
import operator
from langgraph.types import Send
from langgraph.graph import StateGraph, START, END

# 전체 상태 (OverallState)
class OverallState(TypedDict):
    subjects: list[str]             # map 입력 (ex: 여러 테이블 후보)
    results: Annotated[list[str], operator.add]  # reduce 타겟 — 리스트 누적
    final_summary: str

# 각 병렬 인스턴스에 전달되는 개별 상태 (WorkerState)
class WorkerState(TypedDict):
    subject: str

# Map 단계: 각 subject에 대해 독립 처리
def analyze_table(state: WorkerState) -> dict:
    # state["subject"]만 접근 가능 (WorkerState 기준)
    analysis = llm.invoke(f"Analyze table: {state['subject']}")
    return {"results": [analysis.content]}
    # OverallState.results에 operator.add로 병합됨

# Reduce 단계: 모든 병렬 결과 집계
def summarize_all(state: OverallState) -> dict:
    combined = "\n---\n".join(state["results"])
    summary = llm.invoke(f"Summarize: {combined}")
    return {"final_summary": summary.content}

# Dispatch 함수 — conditional edge로 등록
def dispatch_analysis(state: OverallState) -> list[Send]:
    return [Send("analyze_table", {"subject": s}) for s in state["subjects"]]

# 그래프 구성
builder = StateGraph(OverallState)
builder.add_node("analyze_table", analyze_table)
builder.add_node("summarize_all", summarize_all)

# dispatch_analysis가 List[Send]를 반환 -> analyze_table 인스턴스들 병렬 실행
builder.add_conditional_edges(START, dispatch_analysis, ["analyze_table"])
builder.add_edge("analyze_table", "summarize_all")
builder.add_edge("summarize_all", END)

graph = builder.compile()

# 실행 — 런타임에 테이블 수만큼 병렬 인스턴스 생성
result = graph.invoke({
    "subjects": ["LOAN_MASTER", "LOAN_DETAIL", "CUSTOMER_ACCOUNT"],
    "results": [],
    "final_summary": ""
})
```

출처:
- [Implementing Map-Reduce with LangGraph (Medium/@astropomeai)](https://medium.com/@astropomeai/implementing-map-reduce-with-langgraph-creating-flexible-branches-for-parallel-execution-b6dc44327c0e)
- [Map-Reduce Pattern - LangChain Academy DeepWiki](https://deepwiki.com/langchain-ai/langchain-academy/7.1-map-reduce-pattern)

### 3.3 Send API의 상태 병합 메커니즘

```
WorkerState (analyze_table 인스턴스 1): {"results": ["analysis_A"]}
WorkerState (analyze_table 인스턴스 2): {"results": ["analysis_B"]}
WorkerState (analyze_table 인스턴스 3): {"results": ["analysis_C"]}
                    |
                    | operator.add reducer 적용
                    v
OverallState: {"results": ["analysis_A", "analysis_B", "analysis_C"]}
```

각 worker는 자신의 OverallState 키에 단일 아이템 리스트 `["value"]`를 반환하고, reducer가 이를 concatenate한다.

### 3.4 max_concurrency 제어

```python
# 병렬 인스턴스 수 제한 — rate limit 보호에 필수
result = graph.invoke(
    state,
    config={"max_concurrency": 5}  # 최대 5개 동시 실행
)
```

출처: [Best practices for parallel nodes (fanouts) - LangChain Forum](https://forum.langchain.com/t/best-practices-for-parallel-nodes-fanouts/1900)

---

## 4. 패턴 C: defer=True (비균형 브랜치 동기화)

### 4.1 문제 상황

supervisor 노드가 `Send`로 여러 작업을 dispatch할 때, 일부 작업은 1-step(단순 도구 호출), 다른 작업은 multi-step(에이전트 순회)일 수 있다. `defer=True` 없이는 supervisor가 빠른 작업 완료 직후 재실행되어 느린 작업 결과를 놓친다.

### 4.2 defer=True 패턴

```python
from langgraph.types import Command, Send
from langgraph.graph import StateGraph, START, END
from typing import Annotated, Literal
import operator
from pydantic import BaseModel, Field

class State(BaseModel):
    results: Annotated[list[str], operator.add] = Field(default_factory=list)

# 단순 1-step 브랜치
def tool_node(state: State) -> dict:
    return {"results": ["tool_result"]}

# 복잡 multi-step 브랜치 (step 1)
def agent_node1(state: State) -> Command[Literal["agent_node2"]]:
    return Command(update={"results": ["agent_step1"]}, goto="agent_node2")

# 복잡 multi-step 브랜치 (step 2)
def agent_node2(state: State) -> dict:
    return {"results": ["agent_step2"]}

# 집계 노드
def reducer_node(state: State) -> Command[Literal["supervisor"]]:
    print(f"[REDUCER] total results: {len(state.results)}")
    return Command(goto="supervisor")

# supervisor: defer=True 없으면 빠른 브랜치 완료 시 즉시 재실행됨
def supervisor(state: State) -> Command[Literal["tool_node", "agent_node1", END]]:
    if state.results:
        return Command(goto=END)
    return Command(goto=[Send("tool_node", state), Send("agent_node1", state)])

# 그래프 구성 — defer=True가 핵심
builder = StateGraph(State)
builder.add_node("supervisor", supervisor, defer=True)  # 모든 Send 완료까지 대기
builder.add_node("tool_node", tool_node)
builder.add_node("agent_node1", agent_node1)
builder.add_node("agent_node2", agent_node2)
builder.add_node("reducer_node", reducer_node)

builder.add_edge(START, "supervisor")
builder.add_edge("tool_node", "reducer_node")
builder.add_edge("agent_node2", "reducer_node")

graph = builder.compile()
```

**defer=True 효과:**
- `supervisor`는 자신이 dispatch한 모든 `Send` 작업이 완료될 때까지 다음 superstep으로 미뤄짐
- 브랜치 완료 시점이 달라도 전체 완료 후에만 supervisor 재실행

출처: [Parallel Nodes in LangGraph: Managing Concurrent Branches with Deferred Execution (Medium/@gmurro)](https://medium.com/@gmurro/parallel-nodes-in-langgraph-managing-concurrent-branches-with-the-deferred-execution-d7e94d03ef78)

---

## 5. 패턴 D: 단일 노드 asyncio.gather

### 5.1 패턴

```python
import asyncio

async def fetch_all_context(state: PipelineState) -> dict:
    # LangGraph 그래프 구조 밖에서 병렬화 — 단일 노드 내부
    mongo_task = asyncio.create_task(fetch_mongodb_async(state["query"]))
    qdrant_task = asyncio.create_task(fetch_qdrant_async(state["query"]))
    sql_task = asyncio.create_task(fetch_sql_history_async(state["query"]))
    
    mongo_result, qdrant_result, sql_result = await asyncio.gather(
        mongo_task, qdrant_task, sql_task,
        return_exceptions=True  # 개별 실패가 전체를 차단하지 않도록
    )
    
    context_items = []
    if not isinstance(mongo_result, Exception):
        context_items.append(f"[META] {mongo_result}")
    if not isinstance(qdrant_result, Exception):
        context_items.append(f"[RAG] {qdrant_result}")
    if not isinstance(sql_result, Exception):
        context_items.append(f"[SQL] {sql_result}")
    
    return {"context_items": context_items}
```

### 5.2 asyncio.gather vs 그래프 레벨 병렬화 비교

| 기준 | asyncio.gather (단일 노드) | 그래프 레벨 (add_edge / Send) |
|---|---|---|
| 체크포인팅 | 단일 노드 단위로만 저장 | 각 브랜치 노드별 저장 |
| 부분 실패 재시도 | 전체 노드 재실행 | 실패한 브랜치만 재실행 가능 |
| LangSmith 가시성 | 단일 span (내부 불투명) | 개별 노드별 추적 |
| 구현 복잡도 | 낮음 | 중간 |
| 분기 수 | 런타임 유연 | 정적: 고정, Send: 유연 |
| RetryPolicy 적용 | 불가 (노드 수준만) | 각 브랜치 노드별 적용 가능 |
| 폐쇄망 적합성 | 높음 (추적 없이도 단순) | 중간 (LangSmith 없어도 동작) |

**asyncio.gather를 선택해야 할 때:**
- 분기 수가 많고(10개 이상) 각 분기 처리 시간이 매우 짧을 때
- 개별 브랜치 추적이 불필요한 단순 I/O 병렬화
- 폐쇄망에서 체크포인터 없이 단순 동작 원할 때

**그래프 레벨 병렬화를 선택해야 할 때:**
- 각 브랜치가 LLM 호출을 포함하여 추적이 중요할 때
- 부분 실패 후 재시도가 필요할 때
- RetryPolicy가 브랜치별로 필요할 때

출처:
- [Scaling LangGraph Agents: Parallelization, Subgraphs, and Map-Reduce Trade-Offs](https://aipractitioner.substack.com/p/scaling-langgraph-agents-parallelization)
- [Best practices for parallel nodes (fanouts) - LangChain Forum](https://forum.langchain.com/t/best-practices-for-parallel-nodes-fanouts/1900)

---

## 6. 핵심 Reducer 패턴 레퍼런스

### 6.1 기본 리스트 누적 (가장 일반적)

```python
from typing import Annotated
import operator

class State(TypedDict):
    items: Annotated[list[str], operator.add]
    # 각 노드가 ["item"] 반환 -> concatenate
```

### 6.2 딕셔너리 병합 (구조화된 결과)

```python
def merge_dicts(left: dict | None, right: dict | None) -> dict:
    if left is None:
        return right or {}
    if right is None:
        return left
    return {**left, **right}

class State(TypedDict):
    source_results: Annotated[dict | None, merge_dicts]
    # 각 노드: {"mongodb": result_a} + {"qdrant": result_b} -> {"mongodb": result_a, "qdrant": result_b}
```

### 6.3 메시지 누적 (LangGraph 내장)

```python
from langgraph.graph.message import add_messages
from langchain_core.messages import AnyMessage

class State(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    # ID 기반 upsert 지원 — 중복 메시지 병합
```

출처: [LangGraph Graph API Overview - LangChain Docs](https://docs.langchain.com/oss/python/langgraph/graph-api)

---

## 7. 알려진 버그 및 주의사항

### 7.1 INVALID_CONCURRENT_GRAPH_UPDATE

- **원인:** reducer 없는 상태 키에 병렬 노드가 동시 쓰기
- **증상:** `InvalidUpdateError: Can receive only one value per step`
- **해결:** 병렬 쓰기 대상 키는 반드시 `Annotated` + reducer 적용
- 출처: [Issue #2336 (GitHub)](https://github.com/langchain-ai/langgraph/issues/2336)

### 7.2 add_edge 리스트 문법 혼합 사용 금지

```python
# 버그 유발 — OR 조건과 AND 조건이 혼재
builder.add_edge("node_a", "sink")
builder.add_edge(["node_b", "node_c"], "sink")
# sink 실행 조건이 모호해짐

# 올바른 방법: 한 가지만 사용
builder.add_edge(["node_a", "node_b", "node_c"], "sink")  # 전부 AND 조건
```

출처: [Issue #3249 (GitHub)](https://github.com/langchain-ai/langgraph/issues/3249)

### 7.3 Superstep 내 오류는 원자적 실패

하나의 superstep에서 병렬 실행 중 일부 노드가 성공하고 일부가 실패하면 **전체 superstep 실패**로 처리된다. 체크포인터가 있는 경우 내부적으로 성공한 노드 결과는 저장되어 재시도 시 해당 브랜치는 건너뛸 수 있다.

```python
from langgraph.pregel import RetryPolicy

# 브랜치별 재시도 정책 개별 적용
builder.add_node("fetch_qdrant", fetch_qdrant, retry=RetryPolicy(max_attempts=3))
builder.add_node("fetch_mongodb", fetch_mongodb, retry=RetryPolicy(max_attempts=2))
```

---

## 8. Data Copilot 적용 권고

### 8.1 컨텍스트 수집 파이프라인 (MongoDB + Qdrant + SQL History)

**권고: 정적 Fan-out (패턴 A)**

분기 수가 3개로 고정되어 있고, 각 소스별 독립적 오류 처리 및 RetryPolicy가 필요하기 때문이다.

```python
class ContextState(TypedDict):
    query: str
    table_candidates: list[str]
    context_items: Annotated[list[ContextItem], operator.add]

# 각 브랜치가 서로 다른 RetryPolicy를 가짐
builder.add_node("fetch_meta", fetch_meta_node,
                 retry=RetryPolicy(max_attempts=2))
builder.add_node("fetch_rag", fetch_rag_node,
                 retry=RetryPolicy(max_attempts=3))
builder.add_node("fetch_sql_history", fetch_sql_history_node,
                 retry=RetryPolicy(max_attempts=2))

builder.add_edge(START, "fetch_meta")
builder.add_edge(START, "fetch_rag")
builder.add_edge(START, "fetch_sql_history")

# Fan-in: 3개 완료 후 SQL 생성
builder.add_edge(["fetch_meta", "fetch_rag", "fetch_sql_history"], "generate_sql")
```

### 8.2 복수 SQL 생성 (Send API 고려)

쿼리 모호성 처리 시 N개의 SQL 후보를 병렬 생성하는 경우 Send API가 적합하다. (N은 런타임 결정)

```python
def dispatch_sql_generation(state: OverallState) -> list[Send]:
    return [
        Send("generate_sql_candidate", {"interpretation": interp, "context": state["context"]})
        for interp in state["query_interpretations"]
    ]
```

### 8.3 asyncio.gather 적합 케이스

단순 코드 메타 조회(소규모 key-value lookup)처럼 LLM 호출이 없고 매우 빠른 I/O 병렬화에는 단일 노드 내 `asyncio.gather`가 오버헤드 없이 적합하다.

---

## 9. 기각된 대안

| 대안 | 기각 이유 |
|---|---|
| asyncio.gather (전면 사용) | 체크포인팅 없음, 브랜치별 RetryPolicy 불가, LangSmith 추적 불가 |
| 수동 threading.Barrier | Python GIL + asyncio 혼재로 deadlock 위험, LangGraph 외부 동기화 안티패턴 |
| 순차 실행 | 3개 소스 순차 조회 시 총 지연 = 합산, 137x 성능 손실 사례 존재 |
| celery/distributed task queue | 외부 의존성 추가, 폐쇄망 반입 복잡도, superstep과 이중 스케줄링 |

---

## 참고 문헌

### 공식 문서
- [LangGraph Graph API Overview](https://docs.langchain.com/oss/python/langgraph/graph-api)
- [INVALID_CONCURRENT_GRAPH_UPDATE Error Guide](https://docs.langchain.com/oss/python/langgraph/errors/INVALID_CONCURRENT_GRAPH_UPDATE)

### 기술 분석
- [LangGraph's Execution Model is Trickier Than You Might Think (Atomic Object)](https://spin.atomicobject.com/langgraphs-execution-model-tricky/)
- [Scaling LangGraph Agents: Parallelization, Subgraphs, and Map-Reduce Trade-Offs](https://aipractitioner.substack.com/p/scaling-langgraph-agents-parallelization)
- [Parallel Nodes in LangGraph: Managing Concurrent Branches with Deferred Execution](https://medium.com/@gmurro/parallel-nodes-in-langgraph-managing-concurrent-branches-with-the-deferred-execution-d7e94d03ef78)

### 구현 예시
- [Implementing Map-Reduce with LangGraph (Medium/@astropomeai)](https://medium.com/@astropomeai/implementing-map-reduce-with-langgraph-creating-flexible-branches-for-parallel-execution-b6dc44327c0e)
- [Leveraging LangGraph's Send API for Dynamic and Parallel Workflow Execution (DEV)](https://dev.to/sreeni5018/leveraging-langgraphs-send-api-for-dynamic-and-parallel-workflow-execution-4pgd)
- [Parallel workflows in LangGraph: A Practical Approach (Medium)](https://medium.com/@ameejais0999/parallel-workflows-in-langgraph-a-practical-approach-6e4340ceb8d4)
- [Parallelization Techniques - LangChain Academy DeepWiki](https://deepwiki.com/langchain-ai/langchain-academy/7.3-parallelization-techniques)
- [Map-Reduce Pattern - LangChain Academy DeepWiki](https://deepwiki.com/langchain-ai/langchain-academy/7.1-map-reduce-pattern)

### 커뮤니티 & 이슈
- [Best practices for parallel nodes (fanouts) - LangChain Forum](https://forum.langchain.com/t/best-practices-for-parallel-nodes-fanouts/1900)
- [Issue #3249: Multiple incoming edges + conditional edges bug](https://github.com/langchain-ai/langgraph/issues/3249)
- [Issue #6320: Slow sibling blocks fast branch progress](https://github.com/langchain-ai/langgraph/issues/6320)
- [Issue #2336: INVALID_CONCURRENT_GRAPH_UPDATE with parallel nodes](https://github.com/langchain-ai/langgraph/issues/2336)
