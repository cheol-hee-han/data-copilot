# LangGraph Fan-out/Fan-in: Reducer vs Separate Output Fields

**날짜**: 2026-04-04
**분류**: LangGraph 아키텍처 패턴
**핵심 질문**: Reducer가 공식 권장 패턴인가, 아니면 필요악인가?

---

## 결론 (300자 이내)

Reducer는 "병렬 노드가 동일 키를 쓸 때의 필수 충돌 방지 장치"이지, "복잡한 병합 로직을 담는 그릇"이 아니다. 공식 문서·LangChain Academy 모두 `Annotated[list, operator.add]` 수준의 단순 누적에만 reducer를 사용한다. 복잡한 중첩 객체에 커스텀 reducer를 쓰는 것은 공식 예시에 없으며, 커뮤니티에서 다수의 버그 레포트가 발생한 안티패턴이다. 순서·구조 제어가 필요한 경우 공식 포럼은 **sink 노드에서 별도 필드를 병합하는 패턴**을 명시적으로 권고한다.

---

## 1. LangGraph 공식 문서의 입장

### 1-1. Reducer 사용 범위

공식 Graph API 문서와 LangChain Academy(langchain-ai/langchain-academy) 교육 과정이 가르치는 reducer 패턴은 두 가지뿐이다:

| Reducer | 용도 |
|---------|------|
| `add_messages` | 메시지 히스토리 누적 (덮어쓰기 방지) |
| `operator.add` | 병렬 브랜치 결과 리스트 단순 연결 |

LangChain Academy 커리큘럼 원문: *"reducers remain scoped to aggregating simple collections rather than managing complex object merging strategies."*

### 1-2. 공식 주의사항

- reducer 미지정 상태에서 병렬 노드가 같은 키를 업데이트하면 `INVALID_CONCURRENT_GRAPH_UPDATE` 오류 발생. Reducer는 이 오류를 회피하기 위한 최소 요건이다.
- `operator.add` reducer를 사용한 Annotated 리스트 필드를 tools의 `Command(update=...)` 로 업데이트할 경우 지수적 중복 누적 버그 발생 보고 다수 존재 (forum issue #1546).
- 서브그래프 내 reducer의 상태 전파 동작이 예상과 다를 수 있음 (issue #3587).

---

## 2. 공식 예시가 실제로 사용하는 패턴

### fan-out/fan-in 공식 예시 (branching how-to, map-reduce how-to)

```python
# 공식 예시의 상태 정의 — 항상 이 수준
class State(TypedDict):
    query: str
    results: Annotated[list, operator.add]   # 단순 문자열/딕셔너리 리스트

# 병렬 노드: 각자 단순한 항목 하나씩 리스트로 반환
def branch_a(state: State) -> dict:
    return {"results": ["a_result"]}

def branch_b(state: State) -> dict:
    return {"results": ["b_result"]}

# sink 노드: 누적된 results를 처리
def aggregate(state: State) -> dict:
    # state["results"] == ["a_result", "b_result"]
    ...
```

**공식 예시에서 복잡한 중첩 객체(dict-of-dict, dataclass 리스트 등)에 커스텀 reducer를 적용한 사례는 존재하지 않는다.**

### Send API (map-reduce) 패턴

```python
# 동적 fan-out: 각 Send는 독립된 상태 인스턴스를 가짐
def router(state: State) -> list[Send]:
    return [Send("worker", {"item": x}) for x in state["items"]]

# worker 출력은 별도 키로 수집
class State(TypedDict):
    items: list[str]
    worker_outputs: Annotated[list, operator.add]
```

Send API 자체가 "각 병렬 실행에 독립 상태"를 제공하므로 복잡한 reducer 없이도 격리가 가능하다.

---

## 3. Reducer vs Separate Output Fields: 공식 트레이드오프

LangChain 공식 포럼 maintainer 답변 (forum.langchain.com/t/best-practices-for-parallel-nodes-fanouts/1900):

> "Have workers write to an append-only key (e.g., `results: Annotated[list, operator.add]`) and do synthesis in a downstream aggregator node."

> "If you need consistent, predetermined ordering of updates from a parallel superstep, you should write the outputs (along with an identifying key) to a separate field in your state, then combine them in the sink node."

| 상황 | 권장 패턴 |
|------|-----------|
| 순서 무관, 단순 누적 | `Annotated[list, operator.add]` reducer |
| 순서 보장 필요 | 별도 필드에 `(key, value)` 형태로 쓰고 sink 노드에서 정렬·병합 |
| 복잡한 객체 구조 병합 | sink 노드에서 Python 코드로 병합 (reducer 비사용) |

**핵심**: reducer는 "병합 로직"이 아니라 "동시 쓰기 충돌 방지 장치"로 설계되었다. 병합 로직은 downstream sink 노드의 Python 코드에 두는 것이 공식 권장이다.

---

## 4. 실제 발생 버그 패턴 (커뮤니티 레포트)

커스텀 reducer 또는 복잡한 객체 reducer 사용 시 보고된 문제:

1. **지수적 중복 누적** (issue #1546): `operator.add` + `Command(update=...)` 조합에서 상태가 `[result, [result, result], [[result, result], result]]` 형태로 중첩됨
2. **서브그래프 상태 전파 오류** (issue #3587): 중첩 상태 구조가 예상치 않게 생성됨 `{'dialog_state': ['init', [['a', 'b'], 'c']]}`
3. **Default value 미작동** (issue #5225): reducer 함수와 기본값 초기화가 충돌
4. **병렬 서브그래프 sink 오류** (issue #1964): 여러 서브그래프 병렬 실행 시 sink 노드 수신 실패

---

## 5. Data Copilot 적용 판단

Data Copilot의 컨텍스트 수집 병렬화(메타검색·SQL이력·매뉴얼 동시 실행) 구조에서:

### 권장: 단순 Annotated 리스트 + sink 노드 병합

```python
class ContextState(TypedDict):
    query: str
    # 각 브랜치가 ContextItem 리스트를 append-only로 기록
    raw_context_items: Annotated[list[dict], operator.add]

# sink 노드에서 구조화 병합
def context_aggregator(state: ContextState) -> dict:
    meta = [x for x in state["raw_context_items"] if x["source"] == "meta"]
    sql_history = [x for x in state["raw_context_items"] if x["source"] == "sql_history"]
    manual = [x for x in state["raw_context_items"] if x["source"] == "manual"]
    return {"context": ContextBundle(meta=meta, sql_history=sql_history, manual=manual)}
```

### 기각: 복잡한 커스텀 reducer로 ContextBundle 직접 병합

```python
# 이 패턴은 공식 예시에 없고 버그 리포트 다수 — 기각
def merge_context(existing: ContextBundle, new: ContextBundle) -> ContextBundle:
    return ContextBundle(
        meta=existing.meta + new.meta,
        sql_history=existing.sql_history + new.sql_history,
        ...
    )
```

**기각 이유**: 공식 문서 미지원, 커뮤니티 버그 레포트 다수, 디버깅 복잡도 상승, sink 노드 Python 코드가 동일 효과를 더 명확하게 제공함.

---

## 참고 출처

- LangGraph 공식 Graph API 문서: https://docs.langchain.com/oss/python/langgraph/graph-api
- LangChain Academy 병렬화 기법: https://deepwiki.com/langchain-ai/langchain-academy/7.3-parallelization-techniques
- LangChain 포럼 — Parallel nodes fanout best practices: https://forum.langchain.com/t/best-practices-for-parallel-nodes-fanouts/1900
- LangChain 포럼 — operator.add 지수 중복 버그: https://forum.langchain.com/t/subject-operator-add-reducer-causes-exponential-duplication-in-annotated-list-state-fields-when-tools-update-state/1546
- GitHub issue #3587 (서브그래프 상태 전파): https://github.com/langchain-ai/langgraph/issues/3587
- GitHub issue #1964 (병렬 서브그래프 sink): https://github.com/langchain-ai/langgraph/issues/1964
- Scaling LangGraph Agents — Parallelization trade-offs: https://aipractitioner.substack.com/p/scaling-langgraph-agents-parallelization
