# LangGraph Tool-as-Node 패턴 리서치

**작성일**: 2026-04-04
**작성자**: Research Analyst Agent
**목적**: LangGraph에서 tool을 노드로 정의하는 공식/커뮤니티 패턴 분석 및 Data Copilot 적용 권고안 도출

---

## 결론 요약 (400자)

LangGraph 공식 패턴은 **단일 ToolNode가 여러 tool을 내부 디스패치**하는 구조다. 각 tool이 별도 그래프 노드가 되는 패턴은 공식 권장사항이 아니다. `create_react_agent`의 v1은 모든 tool call을 하나의 ToolNode 호출 안에서 병렬 실행하고, v2는 Send API로 각 tool call을 별도 ToolNode 인스턴스에 분산한다. 동적 tool 수 처리는 Send API 기반 map-reduce가 공식 권고 패턴이며, 도구 수가 실행 시점에 결정된다. Data Copilot처럼 단계별 tool이 고정된 경우 단일 ToolNode(v1) + 커스텀 노드 혼용이 최적이다.

---

## 1. ToolNode: 공식 클래스 정의 및 동작 원리

### 1.1 ToolNode 존재 확인

LangGraph는 `langgraph.prebuilt.ToolNode` 라는 공식 prebuilt 클래스를 제공한다.

- **위치**: `langgraph/libs/prebuilt/langgraph/prebuilt/tool_node.py`
- **임포트**: `from langgraph.prebuilt import ToolNode`
- **기본 역할**: 여러 tool을 등록받아, LLM의 AIMessage에 포함된 `tool_calls`를 읽고 해당 함수를 실행하여 ToolMessage 리스트를 반환하는 단일 노드

### 1.2 핵심 동작 메커니즘

```python
# 등록 방식: 여러 tool을 하나의 ToolNode에 묶음
tool_node = ToolNode([search_tool, calculator_tool, fetch_data_tool])
graph.add_node("tools", tool_node)
```

내부 구현 핵심:
- `_tools_by_name: dict[str, BaseTool]` — tool 이름으로 등록, 런타임에 이름 기반 디스패치
- **동기 경로**: `get_executor_for_config()` + `_run_one` 매핑으로 병렬 실행
- **비동기 경로**: `asyncio.gather()` + `_arun_one` 병렬 실행
- 에러 핸들링: `handle_tool_errors` 파라미터(bool/str/callable/Exception type) 로 구성 가능

### 1.3 ToolNode v1 vs v2 실행 모델

| 구분 | v1 (기본) | v2 (Send API 기반) |
|------|-----------|-------------------|
| 적용 위치 | `create_react_agent` 기본값 | `tool_execution_type="v2"` 지정 시 |
| tool call 처리 단위 | 하나의 ToolNode 인스턴스에서 모든 tool call 병렬 실행 | 각 tool call을 Send API로 별도 ToolNode 인스턴스에 분산 |
| 입력 형태 | AIMessage (tool_calls 다수 포함) | ToolCallWithContext (Send API 페이로드) |
| 격리 수준 | tool call 간 공유 실행 컨텍스트 | tool call 별 독립 상태 스냅샷 |
| 활용 적합성 | 대부분의 표준 패턴 | 세밀한 격리/추적이 필요한 경우 |

v2 라우팅 예시:
```python
# should_continue 함수가 Send 객체 리스트를 반환
def should_continue(state):
    tool_calls = state["messages"][-1].tool_calls
    return [Send("tools", {"tool_call": tc, "state": state}) for tc in tool_calls]
```

---

## 2. 공식 에이전트 패턴별 tool 실행 구조

### 2.1 ReAct Agent (create_react_agent)

**그래프 구조**: agent 노드 → tools 노드 → (조건부 엣지) → agent 또는 END

```
[agent 노드] --tool_calls 있음--> [tools 노드(ToolNode)]
      ^                                      |
      |______________________________________|
                (ToolMessage 반환)
```

- tool이 몇 개든 **단일 "tools" 노드 하나**가 모두 처리
- 각 tool이 별도 그래프 노드가 되는 구조가 아님
- `tools_condition` 헬퍼 함수로 라우팅 자동화

```python
from langgraph.prebuilt import create_react_agent, ToolNode
tools = [search, calculate, fetch]
app = create_react_agent(model, tools)
# 내부적으로: graph.add_node("tools", ToolNode(tools))
```

### 2.2 Plan-and-Execute Agent (공식 튜토리얼)

**그래프 구조**:

```
[planner] → [agent(executor)] → [replan] → END 또는 [agent]
```

- `planner`: 고성능 LLM이 다단계 계획 생성
- `agent(executor)`: `create_react_agent`를 **서브그래프로 사용** — 내부적으로 ToolNode를 포함
- `replan`: 진행 상황 보고 후 계획 수정 또는 최종 답변

핵심 특징:
- executor 자체가 ReAct 루프이므로, plan의 각 단계를 실행할 때 ToolNode를 재사용
- plan step 수가 달라져도 그래프 구조는 불변 (loop 처리)
- 개별 tool이 각각 graph 노드가 되는 방식은 사용하지 않음

### 2.3 LLMCompiler 패턴 (ICML 2024)

이전 리서치(`project_depends_on_wave_scheduling.md`)에서 확인한 내용과 연계:

**그래프 구조**:
```
[planner] → [task_fetching_unit] → [joiner]
                    |
              Send API로 각 task를
              parallel_tool_executor 노드에 분산
```

- planner가 DAG 형태의 task 그래프를 생성
- `task_fetching_unit`이 준비된 task를 골라 `Send("parallel_tool_executor", task)` 발행
- `parallel_tool_executor`는 실질적으로 단일 ToolNode 역할 — 단 Send로 병렬 인스턴스화
- tool이 3개든 7개든 **노드 수는 고정**, tool 수는 Send 호출 수로 표현

```python
# LLMCompiler 방식의 Send 기반 dispatch
def task_fetching_unit(state):
    ready_tasks = get_ready_tasks(state["dag"])
    return [Send("execute_task", {"task": t}) for t in ready_tasks]
```

### 2.4 개별 tool을 각각 노드로 만드는 패턴 (커뮤니티)

**공식 권장 패턴이 아님**. 커뮤니티 일부에서 시도되는 방식:

```python
# 비권장: 각 tool을 별도 노드로
graph.add_node("search_node", search_tool_fn)
graph.add_node("calc_node", calc_tool_fn)
graph.add_node("fetch_node", fetch_tool_fn)
```

이 방식이 등장하는 맥락:
- tool이 단 하나이고 결정적으로(deterministically) 호출되는 경우
- tool 실행 결과가 state에 직접 쓰여야 하고 ToolMessage 형식이 불필요한 경우
- 커스텀 상태 주입이 ToolNode의 `InjectedState` 만으로 불충분한 경우

---

## 3. ToolNode vs 개별 tool 노드: 상세 비교

### 3.1 단일 ToolNode (공식 권장)

**장점**:
- **동적 디스패치**: LLM이 어떤 tool을 선택하든 단일 노드에서 처리 — tool 수와 무관하게 그래프 구조 불변
- **병렬 실행 내장**: `asyncio.gather()` 기반으로 여러 tool call을 자동 병렬화
- **에러 격리**: `handle_tool_errors`로 개별 tool 실패가 전체 그래프를 중단시키지 않음
- **ToolMessage 자동 포맷팅**: LLM 히스토리에 자동으로 결과 삽입
- **구조 단순성**: 노드 수 최소화 → 그래프 가독성 유지
- **Dynamic tool calling 지원**: 런타임에 tool 목록 변경 가능

**단점**:
- **State 직접 쓰기 불가**: ToolNode는 messages에만 결과를 추가하므로, 커스텀 state field 변경 불가
- **Command 객체 제한**: tool이 `Command` 반환 시 복잡도 증가
- **디버깅 가시성 낮음**: 어떤 tool이 실행됐는지 그래프 레벨에서 시각적으로 구분 안 됨
- **tool 간 순서 의존성 표현 불가**: v1에서 모든 tool이 동시 실행됨

### 3.2 커스텀 개별 tool 노드

**장점**:
- **State 직접 조작**: 노드 함수가 state의 임의 field를 읽고 쓸 수 있음
- **명시적 실행 흐름**: 그래프에서 어떤 tool이 언제 실행되는지 시각적으로 표현
- **tool 간 의존성 표현**: 특정 tool 노드를 조건부 엣지로 연결해 순서 보장
- **테스트 용이성**: 각 tool 노드를 독립 단위로 테스트 가능

**단점**:
- **동적 tool 수 처리 불가**: 실행 전에 tool 목록이 확정되어야 함 — plan에 따라 tool이 달라지는 경우 대응 어려움
- **라우팅 복잡도 급증**: tool N개 → 조건부 엣지 N개, 그래프 복잡도 O(N)
- **LLM tool_call 연동 단절**: 표준 tool_calls 형식을 직접 파싱해야 함
- **ToolMessage 포맷 수동 구성**: 응답을 messages에 추가하는 코드 직접 작성 필요

### 3.3 커스텀 ToolNode (상태 쓰기 필요 시)

ToolNode를 상속하거나 유사 구조를 직접 구현하는 방식. LangGraph가 공식 문서에서 명시적으로 권장:

```python
# 상태 직접 업데이트가 필요한 경우 커스텀 tool 노드
async def custom_tool_node(state: DataCopilotState):
    last_message = state["messages"][-1]
    tool_calls = last_message.tool_calls

    results = []
    for tc in tool_calls:
        if tc["name"] == "search_metadata":
            result = await search_metadata(tc["args"])
            # state["retrieved_schema"] 직접 업데이트 가능
            results.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))

    return {"messages": results, "retrieved_schema": result}
```

---

## 4. 동적 tool 수 처리: 공식 권장 패턴

### 4.1 문제 정의

Plan에 따라 실행할 tool 수가 가변적인 경우:
- Plan A: search_metadata → generate_sql → validate → execute (4단계)
- Plan B: search_metadata → clarify (2단계만)
- Plan C: search_metadata → search_similar_sql → generate_sql → validate → execute → rewrite (6단계)

### 4.2 공식 해법: Send API + 단일 ToolNode

LangGraph 공식 문서는 이 패턴을 "map-reduce" 또는 "Send API for dynamic dispatch"로 명명:

```python
def dispatch_tools(state: AgentState):
    tool_calls = state["messages"][-1].tool_calls
    # tool 수에 무관하게 동적 생성
    return [Send("tools", {"tool_call": tc}) for tc in tool_calls]

graph.add_conditional_edges("agent", dispatch_tools)
graph.add_node("tools", ToolNode(all_tools))
```

이 방식의 핵심:
- **그래프 노드 수 고정**: tool이 3개든 7개든 "tools" 노드 하나
- **병렬성 확보**: Send API가 fan-out, reducer가 fan-in
- **LLMCompiler가 검증**: ICML 2024 논문에서 이 패턴으로 ReAct 대비 3.7배 속도 개선 확인

### 4.3 Dynamic tool calling (2025 신기능)

LangChain이 발표한 "Dynamic tool calling" 기능:
- 에이전트 실행 중 step에 따라 **available tools 목록을 변경**
- 초기: 제한된 tool만 노출 → 단계 진행 후 확장
- 구현: `model.bind_tools(tools_for_this_step)` 패턴을 노드 안에서 동적 적용
- 여전히 단일 ToolNode 구조를 유지하면서 가용 tool만 필터링

---

## 5. 아키텍처 패턴 비교표

| 패턴 | 그래프 구조 | tool 수 가변 지원 | State 직접 쓰기 | 공식 지원 | 권장 사용처 |
|------|------------|-----------------|----------------|----------|------------|
| **단일 ToolNode (v1)** | agent + tools (2노드) | 가능 (LLM 선택) | 불가 (messages만) | 공식 권장 | 표준 ReAct, 대부분의 에이전트 |
| **Send API ToolNode (v2)** | agent + tools (동적 인스턴스) | 가능 (Send 동적 생성) | 불가 | 공식 지원 | 병렬 tool 격리, LLMCompiler 스타일 |
| **커스텀 tool 노드 (단일)** | agent + custom_tool (2노드) | 한정적 | 가능 | 공식 언급 | State 업데이트 필요, tool 1~2개 |
| **개별 tool 노드 (각각)** | agent + tool_A + tool_B + ... | 불가 (컴파일 시 확정) | 가능 | 비권장 | 결정론적 워크플로, 소수 고정 tool |
| **Plan-and-Execute (서브그래프)** | planner + executor(ReAct) + replan | 가능 | 서브그래프 내부 | 공식 권장 | 장기 다단계 계획, 복잡 task |
| **LLMCompiler** | planner + task_fetcher + executor | 가능 (DAG 기반) | 제한적 | 공식 튜토리얼 | 병렬 최적화 필요, 속도 중요 |

---

## 6. Data Copilot 맥락에서의 권고안

### 6.1 Data Copilot의 tool 특성 분석

Data Copilot 파이프라인에서 tool에 해당하는 작업:
1. `search_metadata` — MongoDB에서 테이블/컬럼 메타 검색
2. `search_similar_sql` — Qdrant에서 유사 SQL 검색
3. `search_manual` — Qdrant에서 업무 매뉴얼 검색
4. `execute_sql` — PostgreSQL/Sybase IQ/Impala 쿼리 실행
5. `validate_sql` — SQLGlot 기반 SQL 검증

이 tool들의 특성:
- **호출 순서 의존성 있음**: metadata 검색 → SQL 생성 → 검증 → 실행 (순차)
- **State 필드 직접 갱신 필요**: `retrieved_schema`, `generated_sql`, `validation_result` 등이 별도 state field
- **LLM이 어떤 tool을 선택하는 방식이 아님**: 각 노드가 결정론적으로 특정 작업 수행
- **tool 수가 고정적**: 실행 시점에 달라지지 않음

### 6.2 권고 패턴: 커스텀 노드 + 선택적 ToolNode 혼용

**결론**: Data Copilot은 순수 ToolNode 패턴보다 **커스텀 노드를 각 단계로 정의**하는 방식이 적합하다.

이유:
1. 각 단계가 LLM의 동적 tool 선택이 아닌, 파이프라인 단계로 결정론적 실행
2. 각 단계 결과를 state의 전용 field에 저장해야 하므로 ToolNode의 messages-only 제약이 걸림돌
3. tool 수가 런타임에 변하지 않아 동적 dispatch의 이점 없음
4. 개별 노드 구조가 LangGraph Studio/추적에서 시각적으로 명확

**권고 그래프 구조**:
```
clarify → search_metadata → search_context → generate_sql → validate_sql → execute_sql → format_result
              (MongoDB)         (Qdrant)         (LLM)       (SQLGlot)     (DB)            (LLM)
```

각 단계를 독립 그래프 노드로 정의하되, **LLM tool_call 방식이 아닌 직접 함수 호출** 사용:

```python
# 권고: 커스텀 노드로 각 단계 구현
async def search_metadata_node(state: DataCopilotState) -> DataCopilotState:
    result = await mongodb_client.search(state["query"])
    return {"retrieved_schema": result}  # state 직접 업데이트

async def generate_sql_node(state: DataCopilotState) -> DataCopilotState:
    sql = await llm.generate(state["query"], state["retrieved_schema"])
    return {"generated_sql": sql}

graph.add_node("search_metadata", search_metadata_node)
graph.add_node("generate_sql", generate_sql_node)
graph.add_edge("search_metadata", "generate_sql")
```

### 6.3 ToolNode가 유용한 Data Copilot 내 서브 에이전트

**SQL 재작성 ReAct 루프** 등 LLM이 동적으로 tool을 선택해야 하는 서브 에이전트에는 ToolNode가 적합:

```python
# SQL 디버깅 서브에이전트 (재귀적 오류 수정)
debug_tools = [check_syntax, lookup_column, suggest_fix]
debug_agent = create_react_agent(llm, debug_tools)
# 내부적으로 ToolNode 사용 → LLM이 필요한 tool을 동적 선택
```

### 6.4 기각된 대안과 이유

| 대안 | 기각 이유 |
|------|----------|
| 모든 단계를 tool로 정의 후 단일 ToolNode | State 직접 갱신 불가, messages 누적으로 컨텍스트 오염 |
| 각 tool을 별도 그래프 노드 + LLM tool_call 라우팅 | Data Copilot은 순차 파이프라인으로 LLM 라우팅 불필요, 라우팅 비용만 증가 |
| Send API 기반 병렬 tool 실행 | tool 간 순서 의존성으로 병렬 실행 불가, 이전 리서치(project_langgraph_parallel_patterns.md) 확인 |
| Plan-and-Execute 전체 채용 | Data Copilot의 파이프라인은 이미 계획이 고정, 동적 계획 수립 불필요 |

---

## 출처

### Tier 1: 공식 문서 / 논문

- [LangGraph prebuilt ToolNode 소스코드](https://github.com/langchain-ai/langgraph/blob/main/libs/prebuilt/langgraph/prebuilt/tool_node.py) — ToolNode 구현 (v1/v2, asyncio.gather, Send API)
- [LangGraph ToolNode Python API Reference](https://reference.langchain.com/python/langgraph.prebuilt/tool_node/ToolNode) — 공식 API 문서
- [LangGraph LLMCompiler Tutorial](https://langchain-ai.github.io/langgraph/tutorials/llm-compiler/LLMCompiler/) — LLMCompiler (ICML 2024) LangGraph 구현
- [LangGraph ReAct Agent (create_react_agent) DeepWiki](https://deepwiki.com/langchain-ai/langgraph/8.1-react-agent-(create_react_agent)) — v1/v2 실행 모델 상세
- [LangGraph ToolNode and Tool Execution DeepWiki](https://deepwiki.com/langchain-ai/langgraph/8.2-toolnode-and-tool-execution) — 단일 executor vs 개별 노드 분석
- [Plan-and-Execute Agents - LangChain Blog](https://blog.langchain.com/planning-agents/) — plan-and-execute 공식 발표
- [LangChain Dynamic Tool Calling Changelog](https://changelog.langchain.com/announcements/dynamic-tool-calling-in-langgraph-agents) — 동적 tool 기능 발표

### Tier 2: 커뮤니티

- [LangGraph ToolNode OpenTutorial](https://langchain-opentutorial.gitbook.io/langchain-opentutorial/17-langgraph/01-core-features/10-langgraph-toolnode) — ToolNode 사용 패턴 실습
- [Send API for Dynamic Parallel Execution - DEV.to](https://dev.to/sreeni5018/leveraging-langgraphs-send-api-for-dynamic-and-parallel-workflow-execution-4pgd) — Send API map-reduce 패턴
- [LangGraph ToolNode Architecture - AlgoMart Medium](https://medium.com/algomart/when-to-use-toolnode-in-langgraph-and-when-not-to-52371d879a1e) — ToolNode 사용 판단 기준
- [ToolNode and Tool Execution - DEV.to](https://dev.to/programmingcentral/mastering-langgraphs-toolnode-the-ultimate-bridge-between-ai-and-the-real-world-4e2h) — ToolNode 상세 분석
- [LangGraph ToolNode Advantages Discussion #1876](https://github.com/langchain-ai/langgraph/discussions/1876) — 커뮤니티 trade-off 논의
- [LangGraph Plan-and-Execute Mirror](https://www.baihezi.com/mirrors/langgraph/tutorials/plan-and-execute/plan-and-execute/index.html) — plan-and-execute 구조 참조
