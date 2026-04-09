# tool_executor Fan-out 설계문서 비판적 리뷰

> 리뷰 대상: `docs/todo/20260404-tool-executor-fanout-design.md`
> 리뷰 일자: 2026-04-04
> 리뷰어: Code Reviewer Agent
> 결론: **구현 불가 수준의 치명적 문제 2건, 주요 문제 5건 식별. 설계 보완 후 구현 진행 필요.**

---

## 치명적 문제 (구현 불가 -- 반드시 해결 필요)

### C-01. operator.add reducer로 step_results 초기화가 불가능하다

설계문서 Section 4-1 (line 149~166)에서 tool_executor가 `step_results: [SKIPPED들]`을 반환하여 "이전 라운드 잔여를 클리어"한다고 기술하고 있다. 그러나 `operator.add`는 **누적 전용**이다. `operator.add(prev_list, new_list)` = `prev_list + new_list`이므로, tool_executor가 무엇을 반환하든 이전 값 위에 append된다. 초기화(클리어)가 원천적으로 불가능하다.

설계문서 Section 11-2 (line 637~647)에서 이 위험을 인지하고 "구현 시 확인 필요"라고 표기했으나, 이것은 설계 단계에서 해결되어야 할 핵심 메커니즘이다. "구현 시 확인"으로 넘기면 안 된다.

리서치 문서 `20260404-langgraph-reducer-vs-separate-fields.md` Section 4에서 `operator.add` + `Command(update=...)`의 지수적 중복 누적 버그(forum issue #1546)를 경고하고 있다. 설계는 이 경고를 충분히 반영하지 않았다.

**구체적 시나리오:**
```
Round 1: tool_executor -> [skip_A] -> retriever -> [step_1, step_2] -> interpreter 진입 시 step_results = [skip_A, step_1, step_2]
         interpreter 반환 시 step_results = [] -> operator.add -> [skip_A, step_1, step_2] + [] = [skip_A, step_1, step_2] (클리어 실패!)
Round 2: tool_executor -> [skip_B] -> operator.add -> [skip_A, step_1, step_2, skip_B] (이전 라운드 잔여 포함)
```

**해결 방안:**
- (A) `step_results`를 `Annotated[list, operator.add]` 대신 일반 필드(reducer 없음)로 변경. 병렬 노드 중 하나만 step_results에 쓰고, sink 노드(context_interpreter)에서 별도 키(예: `retrieval_results`, `llm_results`)를 수거하여 병합. 리서치 문서 Section 3의 "별도 필드 + sink 노드 병합" 권장 패턴에 부합.
- (B) step_results를 PipelineState가 아닌 fan-out 전용 서브그래프 state에 두어 라운드마다 격리.
- (C) `_merge_step_results`에서 step.step 번호로 매칭하므로, 이전 라운드 잔여가 있어도 현재 라운드 plan에 없는 step 번호는 무시됨. 하지만 리스트가 무한 성장하는 메모리 누수 문제는 남음.

### C-02. add_edge 개별 문법과 리스트 문법의 혼합 사용 -- 리서치가 금지한 패턴

설계문서 Section 5-2 (line 376~390)의 엣지 코드:
```python
builder.add_edge("tool_executor", "context_retriever")   # 개별
builder.add_edge("tool_executor", "llm_executor")         # 개별
builder.add_edge(["context_retriever", "llm_executor"], "context_interpreter")  # 리스트
```

리서치 문서 `20260404-langgraph-parallel-fanout-fanin.md` Section 7.2 (line 454~466)에서 **명시적으로 금지**한 패턴이다:

> "리스트 문법과 개별 edge를 혼합하면 스케줄링 모호성 버그가 발생한다."
> "올바른 방법: 한 가지만 사용"

설계문서 Section 11-1 (line 621~635)에서도 이 문제를 인지하고 있으나, 제시한 "안전한 패턴"이 여전히 혼합 사용이다. context_retriever에 대해 fan-out의 개별 edge(`tool_executor -> context_retriever`)와 fan-in의 리스트 edge(`[context_retriever, ...] -> context_interpreter`)가 공존한다.

**해결 방안:**
- fan-out도 fan-in도 모두 개별 add_edge로 통일:
  ```python
  builder.add_edge("tool_executor", "context_retriever")
  builder.add_edge("tool_executor", "llm_executor")
  builder.add_edge("context_retriever", "context_interpreter")
  builder.add_edge("llm_executor", "context_interpreter")
  ```
- 또는 모두 리스트로 통일 (fan-out에 리스트 문법이 적용 가능한지 확인 필요).
- LangGraph issue #3249를 참조하여 실제 테스트로 동작을 검증하는 단계를 Phase D에 명시적으로 추가.

---

## 주요 문제 (구현은 가능하나 설계 보완 필요)

### W-01. _execute_retrieval_step 순수 함수화 시 enrichment의 dedup 인자 전달 미설계

현재 `_enrich_use_cases` 시그니처 (`context_retriever.py:219~225`):
```python
async def _enrich_use_cases(
    use_cases: list[dict],
    searched_queries: list[str],   # dedup용 -- 현재 공유 리스트를 mutation
    seen_tables: set[str],          # dedup용 -- 현재 공유 set
    code_map: dict[str, CodeMeta] | None,
) -> dict[str, Any]:
```

설계문서 Section 4-2 (line 236)의 순수 함수화된 `_execute_retrieval_step`에서:
```python
enrichment = await _enrich_use_cases(result, ...)  # "..." 가 무엇인지 미정의
```

`searched_queries`와 `seen_tables`를 어디서 가져오는지 설계에 명시되어 있지 않다. 순수 함수이므로 state를 직접 참조할 수 없고, 인자로 전달해야 한다. 그러나:
- 병렬 실행이므로 여러 스텝이 동시에 `_enrich_use_cases`를 호출할 때 동일한 스냅샷을 받게 됨
- 설계문서 Section 11-3에서 "중복 조회가 발생할 수 있음 -> 허용 가능"이라고 했으나, 이것은 **인자 전달 방식 자체가 미정의**인 문제와는 다른 이야기

**해결 방안:**
- `_execute_retrieval_step`에 reason의 스냅샷(searched_queries, explored_tables, code_map)을 인자로 전달하는 시그니처를 명시적으로 설계에 포함.
- 또는 enrichment를 스텝 실행에서 분리하여, 모든 스텝 실행 완료 후 context_interpreter에서 일괄 enrichment 수행 (설계문서 Section 11-3 후반에 언급된 "향후 최적화" 방향).

### W-02. tool_executor에서 dedup 시 explored_tables가 현재 라운드 결과를 반영하지 못하는 문제

설계문서 Section 4-1에서 tool_executor가 `_should_skip_step`으로 dedup을 수행한다. `_should_skip_step`은 `explored_tables`를 참조하여 get_sample_rows의 중복을 판정한다 (`context_retriever.py:66~77`).

그런데 tool_executor 시점에서 `reason.explored_tables`는 **이전 라운드까지의 값만** 보유한다. 현재 라운드의 search_table_meta 결과로 추가될 테이블은 아직 없다. 이것은 현재 구현(`context_retriever_node`)에서도 순차 루프로 실행하면서 `explored_tables.extend(new_tables)` 직후 다음 스텝의 `_should_skip_step`에서 참조하는 구조와 다르다.

현재 구현에서는:
```python
# context_retriever.py:337-338
_, _, calls = await _run_step(step, searched_queries, explored_tables, code_map)
# _run_step 내부에서 explored_tables.extend(new_tables) 수행 -> 다음 루프 반복에서 반영
```

변경 후에는 모든 스텝이 동시에 실행되므로, 같은 라운드 내 search_table_meta 결과가 get_sample_rows의 dedup에 반영되지 않는다. 다만 이것은 "같은 라운드에 search_table_meta와 그 결과 테이블에 대한 get_sample_rows가 함께 포함되는 경우"에만 해당하며, 현재 execution_plan에서 그런 조합이 발생하는지 확인 필요.

**해결 방안:**
- 현재 execution_plan 생성 패턴을 분석하여, 같은 라운드에 search_table_meta + 해당 결과 테이블의 get_sample_rows가 공존하는 케이스가 없음을 확인하고 문서에 명시.
- 또는 depends_on을 활용하여 get_sample_rows가 search_table_meta에 의존하도록 설정. (이 경우 wave 스케줄링 구현이 즉시 필요해짐)

### W-03. _merge_step_results에서 step 번호 충돌 가능성

설계문서 Section 4-4의 `_merge_step_results`는 `step.step` 번호로 매칭한다:
```python
step_map = {s.step: s for s in step_results}
for plan_step in reason.execution_plan:
    if plan_step.step in step_map: ...
```

`recovery_agent.py:267~273`에서 recovery_agent가 새 execution_plan을 수립할 때 step 번호를 `i + 1`로 재채번한다:
```python
steps.append(ExecutionStep(step=i + 1, ...))
```

이전 라운드의 step 번호도 1부터 시작하므로 번호가 겹칠 수 있다. 그러나 C-01의 step_results 초기화 문제와 결합하면, 이전 라운드의 step_results가 남아있을 때 현재 라운드의 동일 번호 스텝과 잘못 매칭될 수 있다.

C-01이 해결되어 step_results가 매 라운드 정상 초기화된다면 이 문제는 발생하지 않는다. C-01 해결이 선행 조건.

### W-04. dispatch_tracking_event 호출이 순수 함수화 후 누락됨

현재 `_run_step` (`context_retriever.py:130~158`)에서 도구 실행 성공/실패 시 `dispatch_tracking_event(CONTEXT_TOOL_SUCCESS/ERROR, ...)`를 호출한다. 설계문서의 `_execute_retrieval_step` (Section 4-2, line 226~251)에는 이 추적 호출이 포함되어 있지 않다.

순수 함수화를 위해 제거한 것인지 의도적 누락인지 불분명하다. `dispatch_tracking_event`는 async 함수이므로 순수 함수 내에서 호출 가능하지만, side-effect가 된다.

**해결 방안:**
- 추적 호출을 유지할 경우 `_execute_retrieval_step` 의사코드에 포함.
- 또는 context_interpreter의 `_merge_step_results`에서 완료/실패 스텝에 대해 일괄 추적 이벤트를 발행하도록 이관. 이 경우 지연(latency) 정보를 step 객체에 추가로 저장해야 함.

### W-05. 테스트 파일 4개에서 context_retriever_node를 직접 호출 -- 변경 영향 미기재

다음 테스트 파일들이 `context_retriever_node`를 직접 import하여 호출한다:
- `tests/auto/e2e/test_agentic_flow_trace.py` (line 33, 218, 289)
- `tests/auto/e2e/test_agentic_e2e.py` (line 55)
- `tests/auto/e2e/test_agentic_core.py` (line 70)
- `tests/manual/e2e/test_agentic_real_e2e.py` (line 385, 423, 486, 506)

설계문서 Section 8 (파일별 변경 상세)에 테스트 파일 변경이 전혀 기재되어 있지 않다. `context_retriever_node`의 시그니처와 반환값이 변경되므로(`{"reason": reason}` -> `{"step_results": completed}`), 이 테스트들은 모두 수정이 필요하다.

---

## 경미한 문제 (구현 중 결정 가능)

### I-01. MAX_TOOL_CALLS 검사 이관 위치 미명시

현재 `context_retriever_node` (`context_retriever.py:331`):
```python
if step.status != StepStatus.PENDING or total_tool_calls >= MAX_TOOL_CALLS:
    continue
```

설계문서의 `tool_executor_node`에는 MAX_TOOL_CALLS 검사가 없다. `context_retriever_node` 리팩터링 후에도 없다. 스텝 수가 MAX_TOOL_CALLS를 초과하는 경우의 제어가 누락될 수 있다.

**해결 방안:** tool_executor에서 PENDING 스텝 추출 시 `reason.loop_guard.total_tool_calls + len(pending) <= MAX_TOOL_CALLS` 검사를 추가하거나, `_merge_step_results`에서 합산 후 초과분을 FAILED 처리.

### I-02. _extract_tables가 insight_builder.py에서도 참조됨

```
src\services\insight_builder.py  -- _extract_tables 참조 확인 필요
```

grep 결과 `_extract_tables`는 `context_retriever.py`와 `insight_builder.py` 2개 파일에서 사용된다. 설계문서에서는 `_extract_tables`가 context_retriever에 잔류하는 것으로 기술하고 있으나, insight_builder의 참조가 영향받지 않는지 확인 필요.

### I-03. thinking_modes.py에 context_retriever 하드코딩 문자열 존재

`src/agents/nodes/thinking_modes.py:25`에 `"context_retriever": "off"` 하드코딩이 있다. 설계문서 Section 8-3 (문자열/주석 변경 파일)에 기재되어 있지 않다.

### I-04. __init__.py에 context_retriever 문자열 존재

`src/agents/nodes/__init__.py:18`에 context_retriever 관련 문자열이 있다. 설계문서에 미기재.

### I-05. reasoning_preparer.py 주석에 context_retriever 언급

`src/agents/nodes/reason/reasoning_preparer.py` (line 8, 344, 351)에 context_retriever 관련 주석이 있다. 설계문서 Section 8-3에 미기재.

---

## 빠진 상세 (설계문서에 추가 권장)

### M-01. 에러 처리 상세

설계문서 Section 4-2 (line 210~218)에서 `asyncio.gather(return_exceptions=True)` 후 Exception 케이스를 FAILED 처리하는 것까지는 기술되어 있으나:
- 전체 스텝이 실패하면 context_interpreter에 빈 결과가 전달됨 -- 이 경우의 동작 미기술
- asyncio.gather에 timeout이 없음. 하나의 도구가 무한 대기하면 전체 라운드가 blocking됨.
- 현재 `_run_step`에는 `time.perf_counter()` 기반 소요시간 측정이 있으나 timeout 제어는 없음 (기존 문제이나, 병렬화로 인해 영향이 확대됨)

**권장:** `asyncio.wait_for` 또는 `asyncio.timeout`으로 개별 스텝/전체 gather에 타임아웃 설정 추가.

### M-02. context_interpreter 반환값에 step_results: [] 추가 시 고려사항

설계문서 Section 4-4 (line 324)에서 `return {"reason": reason, "step_results": []}` 를 반환한다. 그런데 `step_results`가 `Annotated[list, operator.add]`이면, C-01에서 지적한 대로 빈 리스트 반환은 클리어가 아닌 no-op이다. 이 "이중 안전" 장치는 실제로 동작하지 않는다.

### M-03. tool_executor의 _should_skip_step 이관 시 explored_tables 참조 범위

설계문서에서 `_should_skip_step`을 tool_executor로 이관한다고 기술하나, `_should_skip_step`은 `_extract_tables`, `_find_table`, `_find_column` 등 context_retriever 내부 유틸리티에 의존하지 않으므로 이관 자체는 가능하다. 다만 이관 시 import 경로와 테스트 변경을 명시해야 한다.

### M-04. llm_executor의 PENDING 스텝 필터링과 tool_executor의 dedup 중복

tool_executor가 SKIPPED 처리한 스텝은 `step.status = StepStatus.SKIPPED`로 변경된다. 그런데 tool_executor의 반환값 `{"reason": reason}`에서 이 상태 변경이 reason에 반영되어 하위 노드에 전파되는지가 명확하지 않다. LangGraph에서 fan-out 시 각 하위 노드는 동일 state 스냅샷을 받는데, tool_executor가 reason을 수정해서 반환했을 때 하위 노드가 이 수정을 볼 수 있는지는 LangGraph superstep 동작에 의존한다.

### M-05. 구현 순서에 롤백 계획 부재

Phase A~F까지 순서가 잘 정리되어 있으나, 중간에 문제 발생 시 롤백 방법이 없다. 특히 Phase D(그래프 연결)에서 문제 발생 시 기존 파이프라인으로 복원하는 방법을 명시하면 좋다.

---

## 검증 완료 (문제 없음 확인된 항목)

### V-01. PipelineState(Pydantic BaseModel)에서 operator.add reducer 정상 동작

`state.py:648~650`에서 `resolved_signals: Annotated[list[AmbiguitySignal], operator.add]`가 이미 PipelineState(BaseModel)에서 사용 중이다. 설계문서 Section 11-4에서 정확히 이 사례를 근거로 제시하였으며, 이 판단은 타당하다.

### V-02. 비균형 브랜치 미해당

설계문서 Section 2-3의 판단 -- 두 executor 노드가 각 1홉이므로 비균형 브랜치 이슈(#6320)에 해당하지 않음 -- 은 정확하다. 리서치 문서 Section 1.2와 일치한다.

### V-03. ExecutionStep.depends_on 필드는 이미 state.py에 존재

`state.py:120`에서 `depends_on: int | None = None`이 이미 정의되어 있음을 확인. 설계문서 Section 3-2와 일치한다.

### V-04. "변경 없음" 파일 대부분 정확

- `readiness_gate.py`: `StepStatus.PENDING` 필터링만 하며 step_results를 참조하지 않음 -- 정확
- `confidence_scorer.py`: `StepStatus` 읽기만 하며 execution_plan 구조 변경 없음 -- 정확
- `tool_renderers.py`: `step.raw_result` 기반 렌더링이며 step_results 무관 -- 정확
- `tools.py`: `execute_tool`, `TOOL_MAP` 등 도구 실행 인터페이스 변경 없음 -- 정확
- `reasoning_preparer.py`: execution_plan 생성만 하며 실행 로직 무관 -- 정확 (단, 주석 변경 필요 -- I-05)

### V-05. 리서치 결론과 설계 방향 일치

- 정적 Fan-out 선택 (리서치 Section 8.1 권장) -- 일치
- 단순 `operator.add` reducer 사용 (리서치 2문서의 결론) -- 일치 (단 초기화 문제는 C-01)
- depends_on + wave 패턴 (리서치 3문서의 권고: 패턴 A) -- 일치
- LLM에 병렬/순차 판단을 시키지 않는 원칙 (리서치 3문서: 폐쇄망 LLM 신뢰도 고려) -- 일치

### V-06. recovery_agent 출력 형식 변경 없음 확인

`recovery_agent.py:267~273`에서 `ExecutionStep(step=i+1, ...)` flat 리스트를 생성하며, 설계문서의 "recovery_agent 출력 형식 변경 없음" 원칙과 일치한다.

---

## 영향도 누락 파일 전체 목록 (grep 결과 기반)

설계문서 Section 8에 기재되지 않은 `context_retriever` 참조 파일:

| 파일 | 참조 내용 | 영향도 |
|------|-----------|--------|
| `src/agents/nodes/thinking_modes.py:25` | `"context_retriever": "off"` | 문자열 변경 필요 (tool_executor 추가) |
| `src/agents/nodes/__init__.py:18` | context_retriever 관련 주석 | 주석 갱신 권장 |
| `src/agents/nodes/reason/reasoning_preparer.py:8,344,351` | context_retriever 관련 주석 | 주석 갱신 필요 |
| `src/services/insight_builder.py:312` | `"context_retriever": "데이터 수집"` | 문자열 변경 필요 |
| `src/agents/nodes/reason/tools.py:9` | context_retriever 관련 주석 | 주석 갱신 권장 |
| `tests/auto/e2e/test_agentic_flow_trace.py` | `context_retriever_node` 직접 호출 | **테스트 수정 필수** |
| `tests/auto/e2e/test_agentic_e2e.py` | `context_retriever_node` import | **테스트 수정 필수** |
| `tests/auto/e2e/test_agentic_core.py` | `context_retriever_node` import | **테스트 수정 필수** |
| `tests/manual/e2e/test_agentic_real_e2e.py` | `context_retriever_node` 직접 호출 (4곳) | **테스트 수정 필수** |

---

## 우선순위별 해결 권장 순서

1. **C-01 해결** -- step_results 초기화 메커니즘을 재설계 (별도 필드 분리 또는 replace reducer)
2. **C-02 해결** -- add_edge 혼합 패턴을 개별 add_edge로 통일, 실증 테스트 추가
3. **W-01 해결** -- _execute_retrieval_step의 enrichment 인자 전달 시그니처 확정
4. **W-04 해결** -- dispatch_tracking_event 이관 방안 확정
5. **W-05 해결** -- 테스트 파일 변경 목록을 Section 8에 추가
6. 나머지 경미한 문제 및 빠진 상세 반영
