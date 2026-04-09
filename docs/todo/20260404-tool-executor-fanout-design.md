# tool_executor 노드 + Fan-out 병렬 실행 설계

> 작성일: 2026-04-04
> 상태: 설계 확정, 구현 대기
> 선행 작업: Phase 1~4 파이프라인 재설계 완료 (tool-result-renderer-design.md 기반)
> 리서치 근거:
>   - `docs/research/20260404-langgraph-parallel-fanout-fanin.md`
>   - `docs/research/20260404-langgraph-reducer-vs-separate-fields.md`
>   - `docs/research/20260404-depends-on-wave-scheduling-pattern.md`
> 리뷰: `docs/reviews/code/20260404-tool-executor-fanout-design-review-report.md`

---

## 1. 목적

### 1-1. 문제

현재 `context_retriever` 노드가 execution_plan의 스텝을 **순차 for 루프**로 실행한다.
(`context_retriever.py:330` -- `for step in reason.execution_plan`)

- 스텝 간 의존성이 없음에도 불구하고 순차 실행하여 불필요한 대기 발생
- 도구 카테고리가 retrieval(데이터 조회) 하나뿐이라 향후 LLM 호출 도구 등
  이종 카테고리 추가 시 구조적 확장이 어려움
- context_retriever가 "도구 실행 + enrichment + state 갱신"을 모두 담당하여 역할 과다

### 1-2. 목표

1. **병렬 실행**: 하나의 execution_plan 내 모든 독립 스텝을 병렬로 실행
2. **카테고리 확장**: retrieval 외 LLM 도구 등 새 카테고리를 노드 단위로 추가 가능
3. **LangGraph 정적 fan-out/fan-in**: superstep 기반 자동 barrier, 추가 코드 불필요
4. **state 충돌 회피**: 병렬 노드가 `reason`을 직접 수정하지 않고 별도 output 필드에 결과 반환

### 1-3. 설계 원칙

| 원칙 | 근거 |
|------|------|
| 병렬 노드는 `reason` 읽기 전용 | LangGraph 공식 권장: 복잡한 커스텀 reducer 대신 별도 output + sink 노드 병합 |
| 하나의 execution_plan 내 모든 스텝은 독립적 | 테이블 의존 도구는 이전 라운드에서 테이블 확보 후에만 계획에 추가됨 |
| LLM에게 병렬/순차 판단을 시키지 않는다 | 도구 간 의존성은 결정론적 -- 코드로 100% 정확하게 판별 가능 |
| recovery_agent 출력 형식 변경 없음 | flat ExecutionStep 리스트 유지, 스케줄링은 executor가 결정 |

---

## 2. 아키텍처 변경

### 2-1. 그래프 구조

**현재:**
```
reasoning_preparer -> context_retriever -> context_interpreter -> readiness_gate
                          ^                                         |
        recovery_agent <--+--------------------------------------------+
```

**변경 후:**
```
                         +-- context_retriever --+
reasoning_preparer -> tool_executor -+                      +-> context_interpreter -> readiness_gate
                         ^           +-- llm_executor ------+                            |
                         +-- recovery_agent <-------------------------------------------------+
```

### 2-2. LangGraph 엣지 정의

```python
# Fan-out: 개별 add_edge로 통일 (리스트 문법 혼합 금지 -- issue #3249)
builder.add_edge("tool_executor", "context_retriever")
builder.add_edge("tool_executor", "llm_executor")

# Fan-in: 개별 add_edge로 통일
builder.add_edge("context_retriever", "context_interpreter")
builder.add_edge("llm_executor", "context_interpreter")
```

> **설계 결정 근거 (리뷰 C-02 반영):**
> 리서치 문서에서 개별 `add_edge`와 리스트 `add_edge` 혼합 시
> 스케줄링 모호성 버그 발생을 경고 (issue #3249).
> fan-out/fan-in 모두 **개별 add_edge로 통일**하여 안전성 확보.
> 구현 시 superstep barrier가 정상 동작하는지 반드시 실증 테스트 수행.

LangGraph superstep 모델에 의해:
1. tool_executor 완료 -> context_retriever + llm_executor **동시 시작**
2. 둘 다 완료될 때까지 대기 (superstep barrier)
3. 둘 다 끝나면 -> context_interpreter 시작

### 2-3. 비균형 브랜치 대응

두 executor 노드의 그래프 깊이가 동일(각 1홉)하므로,
LangGraph 비균형 브랜치 이슈(#6320)에 해당하지 않는다.
향후 executor 내부에 서브그래프가 필요해지면 깊이 통일 필요.

---

## 3. State 변경

### 3-1. PipelineState에 카테고리별 executor output 필드 추가

병렬 노드의 output을 **카테고리별 별도 필드**로 분리한다.
각 executor는 자기 전용 필드에만 쓰므로 병렬 쓰기 충돌이 원천적으로 없다.

```python
class PipelineState(BaseModel):
    # ... 기존 필드 유지 ...

    # 신규: 카테고리별 executor output (reducer 없음 -- 각 executor가 자기 필드만 덮어쓰기)
    retrieval_results: list[ExecutionStep] = Field(default_factory=list)
    llm_results: list[ExecutionStep] = Field(default_factory=list)
```

- **reducer 없음** -- 각 필드를 하나의 executor만 쓴다 -> 병렬 쓰기 충돌 없음
- 매 라운드 각 executor가 덮어쓰기하므로 이전 라운드 잔여 문제 없음
- context_interpreter가 `retrieval_results + llm_results`를 수거하여 `reason.execution_plan`에 매칭
- 새 카테고리 추가 시 -> 필드 1개 추가 + executor 노드 1개 추가

> **설계 결정 근거 (리뷰 C-01 반영):**
> `operator.add`는 `prev + new`로 동작하여 빈 리스트 반환이 no-op이 된다.
> 따라서 "매 라운드 초기화" 요건을 충족할 수 없다 (무한 누적 문제).
> 별도 필드 + 덮어쓰기 방식이 LangGraph 공식 포럼 권장 패턴에 부합한다.

### 3-2. ExecutionStep.depends_on 필드 (추가 완료)

```python
class ExecutionStep(BaseModel):
    step: int
    tool: str
    input: str
    purpose: str
    status: StepStatus = StepStatus.PENDING
    insight: str | None = None
    raw_result: dict[str, Any] | list | None = None
    depends_on: int | None = None  # 선행 스텝 번호 (None이면 독립 실행)
```

- 현재 모든 스텝은 `depends_on=None` (전부 독립)
- 향후 순차 의존이 필요한 도구 등장 시 wave 스케줄링에 활용
- LLMCompiler (ICML 2024) 패턴 기반

### 3-3. reason.phase 중복 설정 제거

현재 context_retriever에서 `reason.phase = Phase.EXPLORING`을 설정하나,
진입 시점에 이미 EXPLORING 상태이므로 **중복 설정이며 제거 대상**이다.

phase 설정 권한:
- `reasoning_preparer`: EXPLORING 설정 (최초)
- `recovery_agent`: EXPLORING / GENERATING / DONE 설정 (재계획 후)
- `readiness_gate`: EXPLORING / REPLANNING / GENERATING / DONE 설정 (판정 후)
- tool_executor, context_retriever, llm_executor: **phase 수정 안 함**

---

## 4. 노드별 상세 설계

### 4-1. tool_executor (신규)

**파일**: `src/agents/nodes/reason/tool_executor.py`

**역할**: execution_plan에서 PENDING 스텝 추출, dedup 필터링, SKIPPED 스텝 선반영

```python
async def tool_executor_node(state: PipelineState) -> dict:
    """execution_plan의 PENDING 스텝을 추출하고 fan-out을 준비한다.

    실제 도구 실행은 하지 않는다 -- fan-out된 하위 노드가 수행.
    dedup 판정(이미 검색한 쿼리 스킵)을 이 노드에서 수행하고,
    SKIPPED 처리된 스텝은 retrieval_results에 바로 포함한다.

    MAX_TOOL_CALLS 검사도 이 노드에서 수행한다.
    한도 초과 시 초과분 스텝을 SKIPPED 처리.
    """
    reason = state.reason.model_copy(deep=True)

    remaining_budget = MAX_TOOL_CALLS - reason.loop_guard.total_tool_calls
    skipped_steps: list[ExecutionStep] = []

    for step in reason.execution_plan:
        if step.status != StepStatus.PENDING:
            continue
        if _should_skip_step(step, reason.searched_queries, reason.explored_tables):
            skipped_steps.append(step.model_copy(deep=True))
            continue
        if remaining_budget <= 0:
            step_copy = step.model_copy(deep=True)
            step_copy.status = StepStatus.SKIPPED
            step_copy.insight = "MAX_TOOL_CALLS 한도 초과"
            skipped_steps.append(step_copy)

    return {
        "reason": reason,
        "retrieval_results": skipped_steps,  # SKIPPED 스텝 선반영
        "llm_results": [],                    # 초기화
    }
```

**핵심 결정:**
- dedup 판정을 tool_executor에서 수행 (하위 노드에서 reason 읽기만 하므로)
- `_should_skip_step`은 context_retriever.py에서 이관
- MAX_TOOL_CALLS 검사도 이 노드에서 수행 (리뷰 I-01 반영)
- SKIPPED 스텝은 `retrieval_results`에 포함 (interpreter가 SKIPPED 인지 가능)

### 4-2. context_retriever (기존 노드 -> 역할 축소)

**파일**: `src/agents/nodes/reason/context_retriever.py`

**역할 변경**: "execution_plan 전체 실행 + state 갱신" -> "retrieval 스텝만 병렬 실행, retrieval_results 반환"

```python
RETRIEVAL_TOOLS: frozenset[str] = frozenset({
    "search_use_cases", "search_table_meta", "search_code_meta",
    "search_manual", "search_biz_terms",
    "get_sample_rows", "search_column_values",
    "get_column_profile", "get_date_distribution",
})

async def context_retriever_node(state: PipelineState) -> dict:
    """retrieval 카테고리 도구를 asyncio.gather로 병렬 실행한다.

    reason은 읽기 전용. 결과는 retrieval_results에만 반환.
    """
    reason = state.reason  # 읽기 전용 -- model_copy 불필요

    # PENDING인 retrieval 스텝만 필터 (tool_executor가 dedup 완료 상태)
    pending = [
        s.model_copy(deep=True)
        for s in reason.execution_plan
        if s.status == StepStatus.PENDING and s.tool in RETRIEVAL_TOOLS
    ]

    if not pending:
        return {"retrieval_results": []}

    # enrichment용 스냅샷 (읽기 전용 -- 병렬 실행 중 mutation 없음)
    enrichment_ctx = _EnrichmentContext(
        searched_queries=set(reason.searched_queries),
        seen_tables={t.table_name for t in reason.explored_tables},
        code_map=dict(reason.code_map),
    )

    # 전부 asyncio.gather로 병렬 실행 (개별 타임아웃 적용)
    results = await asyncio.gather(
        *[
            asyncio.wait_for(
                _execute_retrieval_step(step, enrichment_ctx),
                timeout=_STEP_TIMEOUT_SECONDS,
            )
            for step in pending
        ],
        return_exceptions=True,
    )

    # 에러 핸들링 + 완료 스텝 수집
    completed = []
    for step, result in zip(pending, results):
        if isinstance(result, Exception):
            step.status = StepStatus.FAILED
            step.insight = f"도구 실행 실패: {result}"
            completed.append(step)
        else:
            completed.append(result)  # _execute_retrieval_step이 반환한 완료 스텝

    return {"retrieval_results": completed}
```

**_execute_retrieval_step (기존 _run_step 리팩터링):**

```python
@dataclass(frozen=True)
class _EnrichmentContext:
    """enrichment 함수에 전달할 읽기 전용 스냅샷."""
    searched_queries: set[str]
    seen_tables: set[str]
    code_map: dict[str, Any]

_STEP_TIMEOUT_SECONDS = 30  # 개별 스텝 타임아웃

async def _execute_retrieval_step(
    step: ExecutionStep,
    enrich_ctx: _EnrichmentContext,
) -> ExecutionStep:
    """단일 retrieval 스텝을 실행한다. 공유 상태 mutation 없음.

    도구 실행 -> raw_result 저장 -> 추적 이벤트 발행 -> step 반환.
    enrichment(search_use_cases 후속 수집)도 여기서 수행하여 raw_result에 포함.
    """
    import time as _time
    _t0 = _time.perf_counter()

    result = await execute_tool(step.tool, step.input)
    _elapsed = (_time.perf_counter() - _t0) * 1000
    step.status = StepStatus.DONE

    if step.tool == "search_use_cases":
        enrichment = await _enrich_use_cases(
            result,
            searched_queries=list(enrich_ctx.searched_queries),
            seen_tables=enrich_ctx.seen_tables,
            code_map=enrich_ctx.code_map,
        )
        step.raw_result = {
            "use_cases": result,
            "tables": enrichment.get("tables", []),
            "codes": enrichment.get("codes", {}),
        }
    elif step.tool == "search_table_meta":
        new_tables = _extract_tables(step, result)
        step.raw_result = {"tables": [t.model_dump() for t in new_tables]}
    elif step.tool == "get_date_distribution":
        dates = sorted(result, reverse=True) if isinstance(result, list) and result else []
        step.raw_result = {"dates": result, "recent_values": dates[:10]}
    else:
        step.raw_result = result

    # 추적 이벤트 발행 (리뷰 W-04 반영)
    result_count = len(result) if isinstance(result, list) else 1
    await dispatch_tracking_event(CONTEXT_TOOL_SUCCESS, {
        "source": step.tool,
        "query": truncate_trace(step.input),
        "results_count": result_count,
        "results_summary": [f"결과 {result_count}건 수집"],
        "latency_ms": round(_elapsed, 1),
    })

    return step
```

> **설계 결정 근거 (리뷰 W-01 반영):**
> enrichment의 dedup 인자(`searched_queries`, `seen_tables`, `code_map`)를
> `_EnrichmentContext` frozen dataclass로 패키징하여 순수 함수에 전달.
> 병렬 실행 중 mutation 없이 읽기 전용 스냅샷으로 사용.
> 동일 테이블 중복 조회가 발생할 수 있으나, DB 조회 비용이 낮고
> interpreter에서 dedup되므로 허용 가능.

**enrichment 내부 병렬:**
`_enrich_use_cases` 내부에서 이미 `asyncio.gather`로 테이블 메타/코드 메타를 병렬 조회한다.
(기존 `context_retriever.py:247-249` -- 유지)

따라서 실행 구조는 2중 병렬:
```
asyncio.gather (스텝 간):
    search_use_cases --> _enrich_use_cases 내 asyncio.gather (테이블/코드 병렬)
    search_table_meta --> _extract_tables
    search_code_meta --> raw_result 저장
```

### 4-3. llm_executor (신규 -- 초기 no-op)

**파일**: `src/agents/nodes/reason/llm_executor.py`

**역할**: LLM 호출이 필요한 도구 실행. 초기에는 no-op (빈 리스트 반환).

```python
LLM_TOOLS: frozenset[str] = frozenset()  # 향후: {"calc_verify", "formula_resolve", ...}

async def llm_executor_node(state: PipelineState) -> dict:
    """LLM 호출 도구를 실행한다. 현재는 등록된 LLM 도구가 없으므로 no-op."""
    reason = state.reason  # 읽기 전용

    pending = [
        s.model_copy(deep=True)
        for s in reason.execution_plan
        if s.status == StepStatus.PENDING and s.tool in LLM_TOOLS
    ]

    if not pending:
        return {"llm_results": []}

    # 향후 구현: asyncio.gather 또는 순차 실행
    # LLM 도구는 rate limit/토큰 비용 관리가 필요할 수 있음
    completed = []
    for step in pending:
        # ... LLM 호출 로직 ...
        completed.append(step)

    return {"llm_results": completed}
```

**fan-in 동작**: LLM_TOOLS가 비어있으면 즉시 `{"llm_results": []}` 반환.
fan-in barrier는 LangGraph superstep이 자동 보장.

### 4-4. context_interpreter (기존 -> 앞부분 추가)

**파일**: `src/agents/nodes/reason/context_interpreter.py`

**추가 역할**: executor output 수거 -> reason.execution_plan 업데이트 + 메타데이터 갱신

```python
async def context_interpreter_node(state: PipelineState) -> dict:
    reason = state.reason.model_copy(deep=True)

    # -- 신규: executor output 수거 -> execution_plan 반영 --
    all_results = list(state.retrieval_results) + list(state.llm_results)
    _merge_step_results(reason, all_results)

    # -- 기존 로직 그대로 --
    # LLM 배치 해석 -> 9단계 후처리 -> state 적재
    ...

    return {"reason": reason}
```

**_merge_step_results:**

```python
def _merge_step_results(reason: ReasoningState, step_results: list[ExecutionStep]) -> None:
    """병렬 executor들이 반환한 결과를 reason에 반영한다.

    1. execution_plan 내 해당 스텝의 status, raw_result, insight 업데이트
    2. searched_queries에 실행된 쿼리 누적
    3. loop_guard.total_tool_calls 합산
    """
    step_map = {s.step: s for s in step_results}

    calls_used = 0
    for plan_step in reason.execution_plan:
        if plan_step.step in step_map:
            completed = step_map[plan_step.step]
            plan_step.status = completed.status
            plan_step.raw_result = completed.raw_result
            plan_step.insight = completed.insight

            # searched_queries 누적 (DONE + FAILED 모두 -- 재시도 방지)
            if completed.status in (StepStatus.DONE, StepStatus.FAILED):
                if completed.input not in reason.searched_queries:
                    reason.searched_queries.append(completed.input)
                calls_used += 1
            elif completed.status == StepStatus.SKIPPED:
                pass  # 스킵은 tool_calls 미소비

    loop_guard = reason.loop_guard.model_copy()
    loop_guard.total_tool_calls += calls_used
    reason.loop_guard = loop_guard
```

> **step 번호 충돌 방지 (리뷰 W-03 반영):**
> recovery_agent가 새 plan 수립 시 step 번호를 1부터 재채번한다.
> 별도 필드 방식(retrieval_results, llm_results)은 매 라운드 덮어쓰기되므로
> 이전 라운드 잔여가 없어 번호 충돌이 원천 불가.

---

## 5. 그래프 엣지 / 라우팅 변경

### 5-1. pipeline.py 변경

| 위치 | 현재 | 변경 |
|------|------|------|
| import | `context_retriever_node` | `tool_executor_node` 추가, `llm_executor_node` 추가 |
| 노드 등록 | `add_node("context_retriever", ...)` | `add_node("tool_executor", ...)` 추가, `llm_executor` 추가 |
| 엣지 | `reasoning_preparer -> context_retriever` | `reasoning_preparer -> tool_executor` |
| 엣지 | `context_retriever -> context_interpreter` | (아래 fan-out/fan-in 참조) |
| readiness_gate | `"explore": "context_retriever"` | `"explore": "tool_executor"` |
| recovery 라우팅 | `return "context_retriever"` | `return "tool_executor"` |

### 5-2. 엣지 코드 (변경 후)

```python
# Reason 계층 엣지
builder.add_edge("reasoning_preparer", "tool_executor")

# Fan-out (개별 add_edge -- 리스트 문법 혼합 금지)
builder.add_edge("tool_executor", "context_retriever")
builder.add_edge("tool_executor", "llm_executor")

# Fan-in (개별 add_edge -- 리스트 문법 혼합 금지)
builder.add_edge("context_retriever", "context_interpreter")
builder.add_edge("llm_executor", "context_interpreter")

builder.add_edge("context_interpreter", "readiness_gate")
```

> **구현 시 필수 검증:** LangGraph에서 개별 add_edge만으로 fan-in barrier가
> 정상 동작하는지 (즉, context_interpreter가 두 executor 모두 완료 후에만 실행되는지)
> 실증 테스트를 Phase D에서 반드시 수행한다.

### 5-3. 라우팅 함수 변경

```python
# _route_after_readiness_gate
"explore": "tool_executor",  # was: "context_retriever"

# _route_after_recovery_agent
if reason.phase == Phase.EXPLORING:
    return "tool_executor"  # was: "context_retriever"
```

---

## 6. depends_on + Wave 스케줄링 (미래 확장)

### 6-1. 배경

현재 모든 스텝은 `depends_on=None`으로 완전 독립이다.
향후 "step A 결과를 step B 입력에 사용"하는 순차 의존이 필요할 때를 대비하여
`ExecutionStep.depends_on: int | None` 필드를 추가 완료하였다.

### 6-2. 선례: LLMCompiler (ICML 2024)

```
NODE: 1, TOOL: search, DEPENDS_ON: []
NODE: 2, TOOL: search, DEPENDS_ON: []
NODE: 3, TOOL: calc,   DEPENDS_ON: [1, 2]
```

LLMCompiler는 streaming eager dispatch를 사용하나,
폐쇄망 LLM의 DAG 생성 신뢰도를 고려하여 wave grouping으로 단순화한다.

### 6-3. Wave 스케줄링 알고리즘

```python
def _schedule_waves(steps: list[ExecutionStep]) -> list[list[ExecutionStep]]:
    """depends_on 기반 위상 정렬 -> wave 단위 실행 그룹."""
    waves = []
    done_ids: set[int] = set()
    remaining = list(steps)

    while remaining:
        # 의존성이 충족된(또는 없는) 스텝만 현재 wave에 포함
        wave = [s for s in remaining
                if s.depends_on is None or s.depends_on in done_ids]
        if not wave:
            # 순환 의존 또는 미충족 -- 남은 스텝을 FAILED 처리
            for s in remaining:
                s.status = StepStatus.FAILED
                s.insight = "의존성 미충족"
            break
        waves.append(wave)
        done_ids.update(s.step for s in wave)
        remaining = [s for s in remaining if s.step not in done_ids]

    return waves
```

각 executor 내부에서 wave 단위 실행:
```python
waves = _schedule_waves(my_pending_steps)
for wave in waves:
    results = await asyncio.gather(*[_execute_step(s) for s in wave])
    # wave 완료 후 다음 wave에 결과 주입 가능
```

### 6-4. 현재 구현 범위

- `depends_on` 필드: **추가 완료** (state.py)
- wave 스케줄링: **미구현** (depends_on이 None뿐이므로 불필요)
- 구현 시점: 첫 번째 순차 의존 도구 등장 시

---

## 7. 데이터 흐름 상세

### 7-1. 전체 흐름도

```
tool_executor
    | return: reason (읽기용 전달)
    |         retrieval_results = [SKIPPED 스텝들]
    |         llm_results = []
    v
+----------------------+  +----------------------+
|  context_retriever   |  |    llm_executor      |
|                      |  |                      |
| reason: 읽기 전용    |  | reason: 읽기 전용    |
| asyncio.gather 병렬  |  | (현재 no-op)         |
|                      |  |                      |
| return:              |  | return:              |
|  retrieval_results:  |  |  llm_results: []     |
|  [step1, step2, ...] |  |                      |
+----------+-----------+  +----------+-----------+
           |                         |
           v                         v
             context_interpreter
               | retrieval_results + llm_results 수거
               | -> _merge_step_results():
               |   -> execution_plan 업데이트 (status, raw_result, insight)
               |   -> searched_queries 누적
               |   -> loop_guard.total_tool_calls 합산
               | LLM 배치 해석 (기존)
               | 9단계 후처리 (기존)
               v
             return {"reason": reason}
```

### 7-2. executor output 생명주기

| 시점 | retrieval_results | llm_results |
|------|-------------------|-------------|
| tool_executor 반환 | [SKIPPED 스텝들] | [] |
| context_retriever 반환 | [완료된 retrieval 스텝들] (덮어쓰기) | (변경 없음) |
| llm_executor 반환 | (변경 없음) | [완료된 LLM 스텝들] 또는 [] (덮어쓰기) |
| context_interpreter 진입 | 두 필드 모두 수거 후 reason에 병합 | |

> **초기화 문제 없음:** 각 필드가 reducer 없이 덮어쓰기되므로
> 이전 라운드 잔여가 다음 라운드에 누적되지 않는다.
> tool_executor -> executor -> interpreter 경로에서 3번 덮어쓰기되어
> 항상 현재 라운드 결과만 포함.

### 7-3. 멀티 라운드 동작 (recovery 경로)

```
Round 1: tool_executor -> [retriever, llm_executor] -> interpreter -> readiness_gate
    | (더 탐색 필요)
    readiness_gate -> recovery_agent -> tool_executor -> [retriever, llm_executor] -> interpreter -> ...
```

- recovery_agent가 새 execution_plan 수립 (기존과 동일)
- tool_executor가 새 PENDING 스텝 추출 + dedup
- 이전 라운드의 explored_tables, searched_queries는 reason에 보존됨
- retrieval_results, llm_results는 매 라운드 덮어쓰기 (잔여 없음)

---

## 8. 파일별 변경 상세

### 8-1. 신규 파일

| 파일 | 내용 |
|------|------|
| `src/agents/nodes/reason/tool_executor.py` | 디스패처 노드. dedup + MAX_TOOL_CALLS 검사 |
| `src/agents/nodes/reason/llm_executor.py` | LLM 도구 executor. 초기 no-op |

### 8-2. 수정 파일 -- 코드 변경

| 파일 | 변경 내용 |
|------|-----------|
| `src/agents/state/state.py` | PipelineState에 `retrieval_results`, `llm_results` 필드 추가 |
| `src/agents/nodes/reason/context_retriever.py` | (1) `context_retriever_node` 리팩터링: reason 읽기 전용, `retrieval_results` 반환 (2) `_run_step` -> `_execute_retrieval_step` 순수 함수화 (공유 상태 mutation 제거, `_EnrichmentContext` 인자 추가) (3) `_should_skip_step` -> tool_executor.py로 이관 (4) `reason.phase = Phase.EXPLORING` 제거 (5) `dispatch_tracking_event` 유지 (`_execute_retrieval_step` 내부에서 호출) (6) `asyncio.wait_for` 개별 스텝 타임아웃 추가 |
| `src/agents/nodes/reason/context_interpreter.py` | (1) 앞부분에 `_merge_step_results` 추가 (`retrieval_results + llm_results` -> reason 반영) |
| `src/agents/graph/pipeline.py` | (1) import 추가 (tool_executor_node, llm_executor_node) (2) 노드 등록 추가 (tool_executor, llm_executor) (3) 엣지 변경: fan-out/fan-in 구조 (개별 add_edge) (4) 라우팅 함수: `"context_retriever"` -> `"tool_executor"` |

### 8-3. 수정 파일 -- 문자열/주석만

| 파일 | 변경 내용 |
|------|-----------|
| `src/agents/nodes/reason/recovery_agent.py` | 주석/docstring: `context_retriever` -> `tool_executor` |
| `src/agents/nodes/reason/reasoning_preparer.py` | 주석 (L8, L344, L351): `context_retriever` -> `tool_executor` |
| `src/agents/nodes/reason/tools.py` | 주석 (L9): `context_retriever` -> `tool_executor` |
| `src/agents/nodes/thinking_modes.py` | `"context_retriever": "off"` -> `"tool_executor": "off"` 등 |
| `src/agents/nodes/__init__.py` | context_retriever 관련 주석 갱신 |
| `src/services/insight_builder.py` | `"context_retriever"` 문자열 -> `"tool_executor"` |
| `src/utils/tracker/callback_handler.py` | 노드명 문자열 갱신 |
| `src/utils/tracker/visualizer.py` | 동일 |
| `resources/prompts/reason/recovery_agent_system.txt` | `context_retriever` -> `tool_executor` + `depends_on` 필드 추가 (아래 §8-6 참조) |
| `docs/architecture/pipeline-architecture.md` | 그래프 흐름도 + 노드 목록 갱신 |

### 8-4. 수정 파일 -- 테스트

| 파일 | 변경 내용 |
|------|-----------|
| `tests/auto/e2e/test_agentic_flow_trace.py` | `context_retriever_node` import/호출 -> `tool_executor_node` 또는 테스트 구조 변경 |
| `tests/auto/e2e/test_agentic_e2e.py` | 동일 |
| `tests/auto/e2e/test_agentic_core.py` | 동일 |
| `tests/manual/e2e/test_agentic_real_e2e.py` | `context_retriever_node` 직접 호출 4곳 수정 |
| `tests/auto/unit/test_three_aspect_enrichment.py` | `context_retriever` import 경로 확인/수정 |

### 8-6. 프롬프트 변경 분석

#### recovery_agent_system.txt — `depends_on` 필드 추가

**변경 이유**: ExecutionStep 모델에 `depends_on: int | None = None`이 추가됐으므로,
recovery_agent가 생성하는 execution_plan JSON 형식에도 이 필드를 반영해야 한다.
프롬프트에 필드가 없으면 LLM은 해당 필드를 생성할 수 없고,
향후 순차 의존성 도구 추가 시 프롬프트 수정 없이는 활용이 불가능하다.

**변경 내용**:

1. 응답 형식의 execution_plan 예시에 `"depends_on": null` 추가:

   ```json
   "execution_plan": [
     {"tool": "도구명", "input": "입력값", "purpose": "목적", "depends_on": null}
   ]
   ```

1. 지시사항에 8번 항목 추가:

   ```text
   8. depends_on: 선행 스텝의 결과가 필요한 경우 해당 스텝 번호(1부터 시작)를 지정하세요.
      독립 실행 가능하면 null로 두세요.
   ```

**현재 영향**: 모든 도구가 독립 실행 가능하므로, LLM은 항상 `null`을 생성한다.
`default=None`이므로 LLM이 필드를 생략해도 파싱에 문제 없다.

#### context_interpreter_system.txt — 변경 불필요

interpreter 프롬프트는 `{tool_results}` (렌더러가 생성한 텍스트)를 입력받으며,
도구 실행 방식(순차/병렬)에 대한 참조가 없다. 변경 불필요.

#### reasoning_preparer 프롬프트 — 변경 불필요

reasoning_preparer는 하드코딩된 계획을 생성하며 LLM 프롬프트로 execution_plan을
생성하지 않는다. depends_on 필드는 코드에서 직접 설정한다.

### 8-5. 변경 없는 파일

| 파일 | 이유 |
|------|------|
| `src/agents/nodes/reason/tool_renderers.py` | step.raw_result 기반 렌더링 그대로 |
| `src/agents/nodes/reason/readiness_gate.py` | step.status 읽기 로직 그대로 |
| `src/agents/nodes/reason/sql_generator.py` | 변경 없음 |
| `src/agents/nodes/reason/sql_validator.py` | 변경 없음 |
| `src/services/confidence_scorer.py` | step.status 읽기 로직 그대로 |
| `src/agents/nodes/reason/tools.py` | TOOL_MAP, execute_tool 그대로 (주석만 변경) |

---

## 9. 구현 순서

### Phase A: State + 신규 노드 (기반)

1. `state.py`: PipelineState에 `retrieval_results`, `llm_results` 필드 추가
2. `tool_executor.py`: 신규 파일 작성 (dedup + MAX_TOOL_CALLS + SKIPPED 선반영)
3. `llm_executor.py`: 신규 파일 작성 (no-op)

### Phase B: context_retriever 리팩터링

4. `context_retriever.py`: 노드 함수 리팩터링 (reason 읽기 전용, `retrieval_results` 반환)
5. `context_retriever.py`: `_run_step` -> `_execute_retrieval_step` 순수 함수화 (`_EnrichmentContext` 패턴)
6. `context_retriever.py`: `_should_skip_step` 이관, `reason.phase` 설정 제거, 타임아웃 추가

### Phase C: context_interpreter 수정

7. `context_interpreter.py`: `_merge_step_results` 추가 (`retrieval_results + llm_results` 수거)

### Phase D: 그래프 연결 + 실증 테스트

8. `pipeline.py`: import + 노드 등록 + fan-out/fan-in 엣지 (개별 add_edge) + 라우팅 변경
9. **fan-in barrier 실증 테스트**: 개별 add_edge만으로 superstep barrier가 정상 동작하는지 확인
   - 실패 시 대안: `add_edge(["context_retriever", "llm_executor"], "context_interpreter")` 리스트 문법 시도

### Phase E: 문자열/주석/문서 정리

10. 8-3 목록의 모든 파일에서 문자열/주석 변경
11. 8-4 목록의 모든 테스트 파일 수정

### Phase F: 테스트

12. tool_executor 단위 테스트 (dedup, MAX_TOOL_CALLS, SKIPPED 선반영)
13. context_retriever 단위 테스트 (병렬 실행, retrieval_results 반환, 타임아웃)
14. context_interpreter 단위 테스트 (_merge_step_results)
15. fan-out/fan-in 통합 테스트 (superstep barrier 확인)
16. 기존 테스트 전체 통과 확인

> **롤백 계획 (리뷰 M-05 반영):**
> Phase D에서 문제 발생 시, pipeline.py의 엣지만 원복하면
> 기존 `reasoning_preparer -> context_retriever -> context_interpreter` 경로로 복원 가능.
> context_retriever의 원본 `context_retriever_node` 함수를 별도 함수명으로 보존하여
> 롤백 시 즉시 전환 가능하도록 한다.

---

## 10. 검증 방법

| 검증 항목 | 방법 |
|-----------|------|
| fan-out 병렬 실행 | LangSmith trace에서 context_retriever + llm_executor 동일 superstep 확인 |
| fan-in barrier | context_interpreter 시작 시점이 두 executor 모두 완료 후인지 확인 |
| executor output 병합 | 3개 스텝 plan -> interpreter에서 3개 모두 수거되는지 확인 |
| state 충돌 없음 | `INVALID_CONCURRENT_GRAPH_UPDATE` 에러 미발생 확인 |
| reason 읽기 전용 | context_retriever, llm_executor에서 reason 수정 코드 없음 grep 확인 |
| 기존 동작 보존 | 전체 pytest 통과 + E2E 시나리오 동일 결과 |
| recovery 멀티 라운드 | 2라운드 이상 recovery 시 executor output 정상 덮어쓰기 확인 |
| dedup 정상 | 동일 쿼리 재실행 방지 확인 |
| 타임아웃 | 개별 스텝 타임아웃 동작 확인 |
| MAX_TOOL_CALLS | 한도 초과 시 초과분 SKIPPED 처리 확인 |

---

## 11. 리스크 및 주의사항

### 11-1. fan-in 개별 add_edge의 barrier 동작 확인 필요

리서치에서 리스트 add_edge와 개별 add_edge 혼합을 금지했으므로,
개별 add_edge만으로 fan-in barrier가 보장되는지 실증 확인이 필수.
LangGraph superstep 모델상 동작해야 하나, 버전별 차이가 있을 수 있음.
Phase D에서 반드시 테스트하고, 실패 시 리스트 문법으로 전환.

### 11-2. enrichment 중복 조회 허용

`_enrich_use_cases` 내부에서 `searched_queries`와 `seen_tables`로 중복을 방지하나,
병렬 실행 시 스냅샷 기반이므로 여러 스텝이 동일 테이블을 조회할 수 있다.
- DB 조회 비용이 낮고 (수십ms), interpreter에서 dedup되므로 허용 가능
- 향후 최적화: enrichment를 스텝 실행에서 분리, interpreter에서 일괄 수행

### 11-3. PipelineState가 Pydantic BaseModel

현재 PipelineState는 `BaseModel`이다.
일반 필드(reducer 없음)의 병렬 쓰기 동작은 "각 executor가 자기 전용 필드에만 쓰기"로
충돌을 원천 방지하므로, Pydantic BaseModel에서도 문제 없다.
기존 `resolved_signals: Annotated[list[AmbiguitySignal], operator.add]`가
이미 PipelineState에서 정상 동작 중 (state.py:648-650).

### 11-4. tool_executor의 dedup과 현재 라운드 테이블 (리뷰 W-02)

tool_executor 시점에서 `reason.explored_tables`는 이전 라운드까지의 값만 보유.
현재 라운드의 `search_table_meta` 결과 테이블은 아직 없다.
그러나 현재 execution_plan 생성 패턴에서:
- `reasoning_preparer`: search_table_meta + search_use_cases + search_code_meta (테이블 의존 도구 없음)
- `recovery_agent`: 이전 라운드에서 확보한 테이블에 대해서만 get_sample_rows 등 추가

따라서 **같은 라운드에 search_table_meta와 그 결과 테이블의 get_sample_rows가 공존하는 케이스는 없다.**
이 전제가 깨지는 도구가 추가되면 depends_on + wave 스케줄링 도입 필요.
