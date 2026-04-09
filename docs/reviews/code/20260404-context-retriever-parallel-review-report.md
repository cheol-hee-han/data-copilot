# context_retriever 병렬화 코드 리뷰 리포트

- **대상 파일**: `src/agents/nodes/reason/context_retriever.py`
- **변경 요약**: `context_retriever_node`의 PENDING 스텝 실행을 순차 for-loop에서 `asyncio.gather` 병렬 실행으로 변경
- **리뷰 일자**: 2026-04-04
- **리뷰어**: Code Reviewer Agent

---

## 1. 공유 가변 상태 경합

### 1-1. `searched_queries` (list) append 경합 -- Info(녹색)

`_run_step` L103-104에서 `searched_queries.append(step.input)`을 호출한다.
CPython의 `list.append`는 GIL 하에서 atomic이며, asyncio는 단일 스레드 이벤트 루프이므로
`append` 자체가 await 지점이 아니라 동기 연산이다. **따라서 append 호출 간 인터리빙은 발생하지 않고, 데이터 손상 위험은 없다.**

그러나 **논리적 경합**이 존재한다:

- 스텝 A가 `await execute_tool(...)` (L98) 중에 스텝 B가 먼저 완료되어 `searched_queries`에 값을 추가한다.
- 순차 실행 시에는 A 완료 후 B가 시작되므로 B의 `_should_skip_step` 평가 시 A의 결과가 반영되지만, 병렬화 후에는 모든 `_should_skip_step`이 **gather 전에 한 번만 평가**되므로 중복 쿼리가 동시에 실행될 수 있다.

**영향**: 동일 입력을 가진 스텝이 실행 계획에 포함되는 경우 중복 조회 발생 가능. 다만 실행 계획을 LLM이 생성하는 시점에서 중복 입력이 나올 가능성은 낮고, 결과 정합성에는 영향 없음(읽기 전용 조회). **성능 낭비 수준의 이슈**.

### 1-2. `explored_tables` (list) extend 경합 -- Warning(황색)

`_run_step` L108에서 `explored_tables.extend(new_tables)`를 호출한다.
`list.extend`도 동기 연산이므로 데이터 손상은 없다.

그러나 `_enrich_use_cases` (L187-188)에서 `seen_tables = {t.table_name for t in explored_tables}`로
스냅샷을 찍는 시점이 문제다:

- `search_use_cases` 스텝과 `search_table_meta` 스텝이 동시에 실행될 때,
  `search_table_meta`가 먼저 완료되어 `explored_tables`에 추가한 테이블이
  `search_use_cases` 스텝의 `_enrich_use_cases` 내 `seen_tables` 스냅샷에 **반영될 수도, 안 될 수도 있다**.
- 반영되면: `_enrich_use_cases`가 해당 테이블 메타를 중복 조회하지 않음 (정상)
- 반영 안 되면: `_enrich_use_cases`가 동일 테이블 메타를 한 번 더 조회함 (중복이지만 결과는 동일)

**결론**: 정합성 문제는 아니나, 순차 실행 대비 중복 조회가 증가할 수 있다. **허용 가능한 트레이드오프**.

### 1-3. `code_map` (dict) -- Info(녹색)

`_run_step`은 `code_map`을 `_enrich_use_cases`에 전달하며, `_enrich_use_cases` L281-282에서
`col not in code_map`으로 **읽기만** 수행한다. 쓰기는 없으므로 경합 없음.

### 1-4. 개별 `step` 객체 -- Info(녹색)

각 `step`은 `pending` 리스트의 개별 원소이며, `_run_step`은 전달받은 `step` 하나만 변경한다.
다른 코루틴이 같은 `step` 객체에 접근하지 않으므로 충돌 없음. **문제 없음**.

---

## 2. `_enrich_use_cases`와 `search_table_meta` 중복 조회

### Warning(황색)

`search_use_cases` 스텝의 `_enrich_use_cases` (L256)가 내부에서 `search_table_meta`를 호출하고,
동시에 실행 계획의 `search_table_meta` 스텝도 같은 테이블을 조회할 수 있다.

**중복 발생 경로**:
1. LLM이 실행 계획에 `search_table_meta("TB_LOAN")` 스텝을 생성
2. 동시에 `search_use_cases("여신 잔액 조회")` 스텝도 생성
3. 유사 SQL 결과에 `TB_LOAN` 테이블이 포함됨
4. 두 경로 모두 MongoDB에 `TB_LOAN` 메타를 조회

**정합성 영향**: 없음. 두 조회 모두 읽기 전용이며 결과는 동일하다.

**중복 제거 여부**: `_enrich_use_cases`는 `seen_tables` (L247)과 `already_queried` (L248)로
중복을 방지하려 하지만, 병렬 실행 시 `search_table_meta` 스텝이 `searched_queries`에 값을 추가하는 시점과
`_enrich_use_cases`가 `already_queried = set(searched_queries)`를 스냅샷하는 시점 사이에
**타이밍 의존**이 있어 중복 방지가 보장되지 않는다.

**interpreter 측 중복 제거**: interpreter가 `raw_result`를 처리할 때 `explored_tables`에 같은 테이블이
중복으로 들어갈 수 있는지는 interpreter 코드에 따라 다르나, `_enrich_use_cases`의 결과는
`step.raw_result["use_cases"][*]["enrichment_tables"]`에 저장되고 `search_table_meta`의 결과는
별도의 `step.raw_result["tables"]`에 저장되므로, **interpreter가 병합 시 table_name 기준 dedup을
수행하지 않으면 중복 적재될 수 있다**.

**권장 조치**: interpreter에서 `table_name` 기준 dedup이 수행되는지 확인 필요. 수행되지 않는다면
interpreter 또는 state 적재 로직에 dedup을 추가해야 한다.

---

## 3. `_should_skip_step` 평가 시점

### Warning(황색)

**변경 전 (순차)**:
```
for step in plan:
    if _should_skip_step(step, searched_queries, explored_tables): continue
    await _run_step(step, ...)  # 완료 후 searched_queries/explored_tables 갱신
```
앞 스텝 결과가 뒤 스텝의 skip 판정에 반영됨.

**변경 후 (병렬)**:
```
pending = [s for s in plan if not _should_skip_step(s, ...)]  # 한 번에 전부 평가
await asyncio.gather(...)
```
모든 skip 판정이 동시에 수행되므로, 순차 실행 시 스킵됐을 스텝이 실행될 수 있다.

**구체적 문제 케이스**:
- `search_table_meta("TB_LOAN")` 스텝이 두 개 있을 때: 순차면 두 번째가 스킵되지만, 병렬이면 둘 다 실행
- `get_sample_rows("TB_LOAN, ...")` 스텝이 `search_table_meta("TB_LOAN")` 이후에 있을 때:
  순차면 `explored_tables`에 sample_rows 정보가 반영되어 스킵 판정 가능하지만, 병렬이면 판정 불가

**실제 영향**: LLM이 동일 입력의 중복 스텝을 생성하는 경우는 드물다. `get_sample_rows`의 skip 조건은
`sample_rows is not None`인데, 같은 라운드에서 sample_rows가 설정되려면 이전 라운드에서 이미
조회된 경우여야 하므로 현재 라운드 내에서는 발생하지 않는다.

**결론**: 이론적으로 중복 실행 가능성이 있으나, **실제 발생 확률이 극히 낮고 결과 정합성에 영향 없음**.
다만 docstring의 "스텝 간 데이터 의존이 없으므로" (L355)라는 전제가 skip 로직과 모순되므로,
주석을 정확히 수정하거나 skip 평가 로직의 한계를 명시해야 한다.

---

## 4. `MAX_TOOL_CALLS` 제한

### Info(녹색)

```python
remaining = MAX_TOOL_CALLS - total_tool_calls
pending = [...skip 필터...][:remaining]
```

L366-371에서 `remaining`으로 슬라이싱하여 실행할 스텝 수를 제한하고 있다.
`_run_step`은 항상 `1`을 반환하므로 (L160), `sum(calls for _, _, calls in results)`는
`len(pending)`과 동일하다.

**문제 없음**. `remaining`이 0 이하인 경우 빈 리스트가 되어 `gather`가 호출되지 않는다 (L373 `if pending:`).

단, `_run_step` 내부의 `_enrich_use_cases`가 추가적인 외부 호출(search_table_meta, search_code_meta)을
수행하는데, 이 호출들은 `total_tool_calls`에 **카운트되지 않는다**. 이는 순차 실행 때부터 동일한 동작이므로
병렬화로 인한 새로운 문제는 아니지만, 실제 외부 호출 횟수가 `MAX_TOOL_CALLS`를 초과할 수 있다는 점은
인지해 둘 필요가 있다.

---

## 5. 에러 전파

### Info(녹색)

`_run_step`은 L140에서 `except Exception as e:`로 **모든 예외를 내부에서 잡고** `step.status = FAILED`로
설정한 뒤 정상 반환한다. 따라서 `asyncio.gather`에 예외가 전파되지 않으며,
한 스텝의 실패가 다른 스텝에 영향을 주지 않는다.

**문제 없음**. `return_exceptions=True`를 추가하지 않아도 안전하다.

단, `_run_step` 내부의 `dispatch_tracking_event` (L151-158)가 예외를 던질 경우
이 역시 catch되어 `step.status = FAILED`가 되는데, 트래킹 실패와 도구 실행 실패가 구분되지 않는다.
이는 기존 코드의 문제이며 병렬화와 무관하다.

---

## 6. 순서 보존

### Info(녹색)

`asyncio.gather`는 입력 순서와 동일한 순서로 결과를 반환한다 (Python 공식 문서 보장).
그러나 현재 코드에서 `results`의 순서는 사실상 사용되지 않는다:

- `total_tool_calls` 계산은 순서 무관 (L378: `sum(...)`)
- step 객체는 in-place 변경되므로 `results`를 순회할 필요 없음
- `reason.execution_plan`의 원래 순서가 유지됨

**문제 없음**.

---

## 종합 평가

| # | 포인트 | 등급 | 결론 |
|---|--------|------|------|
| 1 | 공유 가변 상태 경합 | Info(녹색) | asyncio 단일 스레드 + 동기 연산으로 데이터 손상 없음. 논리적 중복 실행은 허용 범위 |
| 2 | enrich + table_meta 중복 조회 | Warning(황색) | 정합성 영향 없으나 interpreter 측 dedup 확인 필요 |
| 3 | skip 평가 시점 | Warning(황색) | 이론적 중복 가능하나 실발생 확률 극저. 주석 수정 권장 |
| 4 | MAX_TOOL_CALLS 제한 | Info(녹색) | 정확하게 동작함 |
| 5 | 에러 전파 | Info(녹색) | 내부 catch로 안전 |
| 6 | 순서 보존 | Info(녹색) | 문제 없음 |

---

## 권장 조치 사항

### Warning(황색) 수준 -- 권장 수정

1. **L355 docstring 수정**: "스텝 간 데이터 의존이 없으므로"는 부정확하다.
   `_should_skip_step`이 공유 상태(`searched_queries`, `explored_tables`)에 의존하므로,
   "스텝 간 결과 의존은 없으나 중복 검색 스킵이 병렬 실행 시 완전하지 않을 수 있다" 정도로 수정 권장.

2. **interpreter 측 테이블 dedup 확인**: `_enrich_use_cases`와 `search_table_meta`가
   동일 테이블을 중복 조회한 경우, interpreter가 `explored_tables` 적재 시
   `table_name` 기준 dedup을 수행하는지 확인하고, 미수행 시 추가.

### Info(녹색) 수준 -- 선택적 개선

3. **`_enrich_use_cases` 내부 호출의 tool_calls 카운트**: 현재 카운트되지 않는 것은
   순차 실행 때부터 동일하므로 이번 변경의 범위는 아니지만, 정확한 제한을 위해
   장기적으로 개선 검토 권장.

4. **L114의 TODO 주석 (`depends_on`)**: `ExecutionStep`에 `depends_on` 필드가 주석 처리되어 있다.
   향후 스텝 간 의존 관계가 필요해지면, 병렬화 로직에서 의존 그래프를 고려한
   단계별 gather (topological sort)로 확장해야 한다. 현재는 불필요.
