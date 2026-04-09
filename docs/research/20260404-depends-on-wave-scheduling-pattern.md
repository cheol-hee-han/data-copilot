# depends_on + Wave-Based Scheduling 패턴 리서치

- 날짜: 2026-04-04
- 분석가: Research Analyst Agent
- 질문: "depends_on + wave topological sort"가 검증된 패턴인가, 더 나은 대안이 있는가?

---

## 1. 결론 요약 (400자 이내)

**`depends_on` + wave topological sort는 학술적으로 확립된 패턴이다.** ICML 2024 채택 논문 LLMCompiler(Kim et al., 2023)가 이 패턴의 정확한 선례다. 플래너가 `DEPENDS_ON: [task_id, ...]` 필드가 포함된 DAG를 생성하고, Task Fetching Unit이 의존성 충족 여부를 확인해 준비된 태스크를 즉시 병렬 디스패치한다. LangGraph 공식 plan-and-execute 튜토리얼은 flat `List[str]` 순차 실행만 보여주며 의존성 필드는 없다. `depends_on` 패턴을 원한다면 **LLMCompiler 아키텍처를 명시적으로 채택**해야 한다.

---

## 2. 질문별 상세 분석

### 2.1 LangGraph 공식: 순차 의존성 처리 패턴

**LangGraph 공식 plan-and-execute 튜토리얼의 상태 스키마:**

```python
class PlanExecute(TypedDict):
    input: str
    plan: List[str]          # 단순 문자열 목록, 의존성 필드 없음
    past_steps: Annotated[List[Tuple], operator.add]
    response: Optional[str]
```

- 공식 튜토리얼의 플랜은 **flat `List[str]`**로, 순서=의존성을 암묵적으로 가정한다.
- 스텝 간 명시적 `depends_on` 필드는 **공식 LangGraph 튜토리얼에 존재하지 않는다.**
- 병렬 실행은 그래프 구조(static fan-out edges)로 표현하는 것이 LangGraph의 방식이다.

**커뮤니티 구현 예시(Ujjwal Basnet, Medium)의 확장:**

```python
class Task(BaseModel):
    task_name: str
    contex_needed: List[str]  # 선행 태스크명 목록, depends_on의 이름 변형
```

이 구현도 순차 처리만 하며 병렬화는 없다.

**결론:** LangGraph는 의존성을 "그래프 토폴로지"로 표현하는 것이 관용적이다. plan-and-execute 안에서 `depends_on` 필드를 쓰는 것은 LangGraph 공식 패턴이 아니라, LLMCompiler 패턴을 LangGraph 위에서 구현하는 것이다.

---

### 2.2 DAG 기반 태스크 스케줄링: 프레임워크별 비교

| 프레임워크 | 의존성 표현 방식 | depends_on 필드 유무 | 병렬 실행 |
|---|---|---|---|
| **LLMCompiler** (SqueezeAILab, ICML 2024) | `DEPENDS_ON: [id, ...]` DAG | **있음** (명시적) | 있음 (Task Fetching Unit) |
| **LangGraph** plan-and-execute | `List[str]` 순서 | 없음 (암묵적 순서) | 없음 (flat sequential) |
| **LangGraph** 그래프 구조 | edge 연결 = 의존성 | 없음 (구조가 의존성) | 있음 (static fan-out) |
| **CrewAI** | `context: [task_obj, ...]` | 있음 (task 참조) | 있음 (async tasks) |
| **AutoGen** | 메시지 기반 순차 | 없음 | 없음 |

**CrewAI의 `context` 파라미터:**

```python
task_b = Task(
    description="...",
    context=[task_a],      # task_a 완료 후 출력을 컨텍스트로 사용
    agent=agent_b
)
```

CrewAI는 `depends_on` 대신 `context` 필드명을 사용하지만 의미는 동일하다.

---

### 2.3 LLMCompiler: depends_on의 정식 선례

**논문:** Kim et al., "An LLM Compiler for Parallel Function Calling", ICML 2024 (arXiv:2312.04511)

**플래너 출력 형식 (논문 및 LangGraph 튜토리얼 버전):**

```
NODE: 1
TOOL: search
ARGS: {"query": "2024년 국내 은행 여신 총액"}
DEPENDS_ON: []

NODE: 2
TOOL: search
ARGS: {"query": "2024년 국내 은행 총자산"}
DEPENDS_ON: []

NODE: 3
TOOL: calculator
ARGS: {"numerator": "$1", "denominator": "$2"}
DEPENDS_ON: [1, 2]
```

**Task Fetching Unit 스케줄링 로직 (의사코드):**

```python
while pending_tasks:
    ready = [t for t in pending_tasks
             if all(dep in completed for dep in t.depends_on)]
    # ready 태스크를 즉시 병렬 디스패치 (wave가 아닌 streaming 방식)
    for task in ready:
        executor.submit(task)
    wait_any()  # 하나라도 완료되면 재확인
```

중요 포인트: LLMCompiler는 **wave(레벨) 그룹화를 하지 않는다.** 의존성이 충족된 태스크를 즉시 디스패치하는 **streaming/eager 방식**이다. Wave grouping(토폴로지 레벨별 일괄 처리)은 구현 단순화를 위한 변형이며, LLMCompiler 원본과는 다르다.

**성능:** ReAct 대비 레이턴시 3.7×, 비용 6.7×, 정확도 ~9% 향상 (ICML 2024)

---

### 2.4 대안 패턴 분석

#### 패턴 A: depends_on 플랫 리스트 + wave topological sort (질문의 패턴)
- **선례:** LLMCompiler 변형, Google Workflows, Apache Airflow DAG
- **장점:** 플래너 출력이 단순(JSON 리스트), LLM이 생성하기 쉬움
- **단점:** wave 경계 계산 로직 필요, 조기 디스패치 불가(wave 완료 대기)
- **적합성:** 복잡도 중간, 단계 수 적음(≤10)

#### 패턴 B: LangGraph 정적 그래프 (LangGraph 관용)
- **선례:** LangGraph 공식 fan-out/fan-in 패턴
- **장점:** LangGraph 네이티브, defer=True로 동기화 보장
- **단점:** 동적 태스크 수 대응 불가, 플랜을 실행 전에 알아야 함
- **적합성:** 태스크 구조가 고정된 경우

#### 패턴 C: LLMCompiler 스타일 (streaming eager dispatch)
- **선례:** SqueezeAILab LLMCompiler, LangGraph LLMCompiler 튜토리얼
- **장점:** 최적 병렬성, 레이턴시 최소화
- **단점:** Task Fetching Unit 구현 복잡, 플래너 프롬프트 정밀 설계 필요
- **적합성:** 도구 호출 수 많음, 병렬성 극대화 필요

#### 패턴 D: Multi-round planning (incremental replanning)
- **선례:** LangGraph plan-and-execute with replan node, ADaPT(2024)
- **구조:**
  1. 독립적 스텝만 플랜
  2. 실행 후 결과 확인
  3. 결과 기반 다음 스텝 플랜
- **장점:** 결과에 따른 적응적 플랜, 의존성 명시 불필요
- **단점:** 플래너 LLM 호출 횟수 증가, 레이턴시 증가
- **적합성:** 사전에 의존성을 알 수 없는 경우, 탐색적 쿼리

#### 패턴 E: 순차/병렬 분리 리스트
- **구조:** `{"sequential": [...], "parallel": [...]}`
- **선례:** 없음 (비표준)
- **단점:** 의존성 표현력 부족 (순차 안에 병렬, 또는 그 반대 불가)
- **결론:** 기각

---

### 2.5 Send() API와 순차 의존성

**질문:** Send()가 "이 Send 완료 후 다음 Send 디스패치"를 처리할 수 있는가?

**답변:** 직접적으로는 불가하다.

Send()는 map-reduce 패턴을 위한 API로, 모든 Send를 동시에 디스패치한다. 순차 Send를 원한다면:

1. **defer=True**: 해당 노드가 모든 선행 태스크 완료까지 대기 (fan-in 동기화)
2. **Wave 분리**: Wave 1의 Send를 완료 후, 그 결과를 받는 노드에서 Wave 2의 Send를 발행

```python
# Wave 분리 패턴: wave_1_aggregator가 완료된 후 wave_2를 Send
def wave_1_aggregator(state):
    results = state["wave_1_results"]
    # wave 2 태스크를 여기서 Send로 디스패치
    return [Send("executor", {"task": t, "context": results}) for t in state["wave_2_tasks"]]
```

이는 wave-based scheduling의 LangGraph 구현 방법이다.

---

## 3. 권고안

### Data Copilot 맥락에서의 선택

Data Copilot의 실행 플랜 패턴은 다음 특성을 가진다:
- 태스크 수: 보통 3~7개 (메타 조회, SQL 생성, 실행 등)
- 의존성: 대부분 선형(A→B→C) + 간혹 병렬 가능(메타 조회 병렬)
- LLM: 폐쇄망 모델(Solar Pro 2 70B) → 복잡한 DAG 플랜 생성 신뢰도 낮음

**권고: depends_on + wave topological sort (패턴 A)**

이유:
1. LLMCompiler(ICML 2024)의 변형으로 학술 선례 충분
2. LLM이 생성하기 쉬운 flat JSON 구조
3. Wave 계산 로직이 단순(표준 BFS 레벨 계산, 10줄 이내)
4. Data Copilot의 태스크 수(≤10)에서 streaming vs wave 성능 차이 무의미
5. LangGraph Send() + wave 분리 노드로 네이티브 구현 가능

**기각된 대안:**
- LLMCompiler streaming: 구현 복잡, 폐쇄망 LLM의 DAG 생성 정확도 불확실
- Multi-round replanning: LLM 호출 횟수 증가, 응답 레이턴시 수 초 추가
- 정적 LangGraph 그래프: 동적 플랜 표현 불가

---

## 4. Wave Topological Sort 구현 참고

```python
from collections import defaultdict, deque

def compute_waves(tasks: list[dict]) -> list[list[str]]:
    """
    tasks: [{"id": "t1", "depends_on": []}, {"id": "t2", "depends_on": ["t1"]}, ...]
    반환: [["t1"], ["t2", "t3"], ["t4"]]  # 각 wave는 병렬 실행 가능
    """
    in_degree = {t["id"]: len(t["depends_on"]) for t in tasks}
    graph = defaultdict(list)
    for t in tasks:
        for dep in t["depends_on"]:
            graph[dep].append(t["id"])

    queue = deque([tid for tid, deg in in_degree.items() if deg == 0])
    waves = []
    while queue:
        wave = list(queue)
        waves.append(wave)
        queue.clear()
        for tid in wave:
            for neighbor in graph[tid]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)
    return waves
```

표준 BFS 레벨 분리 알고리즘. 순환 의존성 탐지: `sum(len(w) for w in waves) != len(tasks)` 이면 순환.

---

## 5. 출처

### Tier 1 논문
1. Kim et al., "An LLM Compiler for Parallel Function Calling", ICML 2024 — arXiv:2312.04511
   - `DEPENDS_ON` 필드 포함 DAG 플랜 + Task Fetching Unit 패턴의 정식 선례
2. Yao et al., "ReAct: Synergizing Reasoning and Acting in Language Models", ICLR 2023
   - LLMCompiler가 기준으로 삼은 비교 대상 (순차 실행 한계 명시)
3. "Plan-and-Act: Improving Planning of Agents for Long-Horizon Tasks", arXiv:2503.09572 (2025)
   - Multi-round replanning 패턴의 최신 연구

### 구현 사례
- [LangGraph LLMCompiler Tutorial](https://langchain-ai.github.io/langgraph/tutorials/llm-compiler/LLMCompiler/) — LangGraph 위 LLMCompiler 구현
- [SqueezeAILab/LLMCompiler](https://github.com/SqueezeAILab/LLMCompiler) — 원본 구현체
- [CrewAI Tasks 공식 문서](https://docs.crewai.com/en/concepts/tasks) — `context` 기반 의존성
- [LangGraph Plan-and-Execute](https://www.baihezi.com/mirrors/langgraph/tutorials/plan-and-execute/plan-and-execute/index.html) — flat List[str] 공식 패턴
- [Medium: Dynamic Plan-Execute with LangGraph](https://medium.com/@ujjwal-basnet-ml/build-dynamic-plan-and-execute-agents-with-langgraph-1b4dfee9d08c) — `contex_needed` 확장 구현
- [LangChain Planning Agents Blog](https://blog.langchain.com/planning-agents/) — LLMCompiler + P&E 비교
