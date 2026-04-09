# executed_tool_keys 통합 중복 방지 설계안 리뷰

**일시**: 2026-04-04
**대상**: `searched_queries: list[str]` → `executed_tool_keys: set[str]` 재설계안
**관점**: 보안, 성능, 타입 안전성, 에러 처리, 기존 코드 일관성, 엣지 케이스, 설계 결함

---

## Critical (빨간)

### C-1. LangGraph checkpointer의 `set` 직렬화 비호환

**위치**: `state.py` (ReasoningState), `checkpointer.py`

**문제**:
현재 프로젝트는 `AsyncPostgresSaver` + `JsonPlusSerializer`를 사용한다.
`JsonPlusSerializer`는 내부적으로 msgpack 기반 직렬화를 수행하며, Python `set`은 msgpack 표준 타입이 아니다.
LangGraph의 `JsonPlusSerializer`는 set을 지원하기는 하지만, 이는 커스텀 태그(`__set__`)를 통한 확장이므로 다음 위험이 존재한다:

1. **checkpoint 복원 시 역직렬화 실패 가능성**: LangGraph 버전 업그레이드 시 serde 동작이 변경될 수 있음
2. **MemorySaver(테스트용)와 AsyncPostgresSaver(프로덕션)간 직렬화 경로 불일치**: 테스트에서 통과하더라도 프로덕션에서 실패할 수 있음
3. **ReasoningState가 Pydantic BaseModel**: `model_copy(deep=True)` 시 set의 deep copy 동작은 문제 없으나, `model_dump()` → JSON 직렬화 시 set은 list로 변환되어 타입 불일치 발생 가능

**현재 프로젝트 패턴**:
기존 코드에서 `set` 타입을 State 필드로 사용하는 사례가 **전혀 없다**.
모든 누적 필드는 `list`, `dict` 타입이다. 이 관례를 깨는 것은 예측 불가능한 문제를 유발할 수 있다.

**개선 제안**:
```python
# set 대신 dict를 사용하여 O(1) lookup 확보 + 직렬화 안전성 보장
executed_tool_keys: dict[str, bool] = Field(default_factory=dict)

# 기록: executed_tool_keys[f"{step.tool}:{step.input}"] = True
# 조회: f"{step.tool}:{step.input}" in executed_tool_keys
```
또는 기존 패턴을 따라 `list[str]`을 유지하되, 조회 시 로컬 `set` 변환:
```python
executed_tool_keys: list[str] = Field(default_factory=list)

# context_retriever_node 내부에서:
_key_set = set(executed_tool_keys)  # O(1) lookup용 로컬 캐시
```

---

### C-2. recovery_agent replan 시 누적된 키가 새 가설 탐색을 차단

**위치**: `recovery_agent.py:122`, `context_retriever.py:62`

**문제**:
설계안에서 `executed_tool_keys`를 리셋하지 않는다고 명시했으나, 이는 recovery 시나리오에서 심각한 문제를 유발한다.

Recovery 시나리오 예시:
1. 초기 가설 H1: `search_table_meta:"여신 잔액"` 실행 → 결과 불충분 → FAILED
2. Recovery에서 H2 새 가설 수립: `search_table_meta:"여신 잔액"` (동일 키워드로 재검색 필요)
3. `executed_tool_keys`에 이미 `"search_table_meta:여신 잔액"`이 있으므로 **스킵됨**

기존 `searched_queries: list[str]`도 동일한 문제를 가지고 있었으나, recovery_agent가 `execution_plan`만 리셋하는 것은 의도적 설계였다 — **동일 입력의 재검색은 결과가 동일하므로 스킵이 맞다**.

그러나 관측 도구(get_sample_rows, search_column_values 등)까지 dedup 대상에 포함하면서 문제가 발생한다:
- H1에서 `get_sample_rows:TB_A`를 실행했는데 결과 해석이 달라야 하는 경우
- H2에서 다른 컬럼 조건으로 `search_column_values:TB_A,COL_X`를 이미 실행했으나, 새 가설에서 같은 컬럼의 값 분포를 재확인해야 하는 경우

**개선 제안**:
관측 도구는 입력이 완전히 동일해도 결과가 동일하므로 dedup이 타당하다.
다만 `_should_skip_step`에서 관측 도구의 스킵 사유를 명확히 분리하고, recovery_agent에서 `explored_tables`의 sample_rows를 기반으로 판단하는 기존 로직을 유지할 것을 권장한다.
설계안의 현재 방향은 **올바르다** — 단, 이 판단 근거를 코드 주석으로 명시해야 한다.

---

## Major (노란)

### M-1. 키 형식에서 구분자 `:` 충돌 가능성

**위치**: 설계안 전체 (`"tool_name:input"` 형식)

**문제**:
`step.input`에 `:`가 포함될 수 있다.
- `search_column_values`의 입력: `"schema.table, column_name"` — 현재는 안전
- 사용자 질의 자체에 `:`가 포함될 수 있음: `"search_use_cases:2024년 3분기: 여신 실적"` → 키 파싱 시 혼동

현재 설계에서는 키를 **파싱하지 않고 완전 일치 비교만 수행**하므로 기능적 문제는 없다.
그러나 향후 키에서 tool_name을 추출해야 하는 요구사항이 발생하면 문제가 된다.

**개선 제안**:
현재 완전 일치 비교만 사용하므로 즉시 수정 불필요.
다만 구분자를 `::` (이중 콜론)이나 `|`로 변경하면 미래 안전성이 높아진다:
```python
tool_key = f"{step.tool}::{step.input}"
```

### M-2. `original_query` prefix가 dedup 네임스페이스를 오염

**위치**: `reasoning_preparer.py:72-75` (설계안)

**문제**:
설계안에서 `executed_tool_keys.add(f"original_query:{query}")`를 기록한다.
`original_query`는 실제 도구 이름이 아니므로 네임스페이스 관점에서 이질적이다.
또한 이 키는 어디서도 dedup 조회에 사용되지 않는다 — 기록만 하고 읽지 않는 dead write이다.

기존 코드에서 `searched_queries.append(query)` (line 74)를 하는 이유는 `_build_execution_plan`에서 `meta_query not in searched_queries` (line 363)로 초기 쿼리와 meta_query 중복을 방지하기 위해서다.

설계안에서 이 read 지점(line 363)은 `f"search_table_meta:{meta_query}" not in executed_tool_keys`로 변경되므로, `original_query:{query}` 기록은 **완전히 불필요**하다.

**개선 제안**:
`original_query:` prefix 기록을 제거한다. 불필요한 상태 기록은 디버깅 혼란을 유발한다.

### M-3. `insight_builder` UI 메시지 의미 왜곡

**위치**: `src/services/insight_builder.py:230-236`

**문제**:
기존 코드:
```python
searched = _get_attr_or_key(reason, "searched_queries", [])
refs.append({
    "detail": f"{len(searched)}건의 유사 쿼리를 참조했습니다.",
})
```

설계안:
```python
# len(executed_tool_keys) 사용
```

`len(executed_tool_keys)`는 **모든 도구 실행 횟수**를 포함한다 (get_sample_rows, search_column_values 등).
기존 `searched_queries`도 사실 검색 쿼리뿐 아니라 enrichment의 table_name, col_name이 혼재되어 있었으므로 이미 부정확했지만, 관측 도구까지 포함하면 숫자가 더 커져 사용자에게 오해를 줄 수 있다.

또한 이 참조 항목의 `source`가 `"sql_history"`이고 `title`이 `"유사 SQL 이력"`인데, executed_tool_keys 건수를 여기에 표시하면 의미적으로 완전히 맞지 않는다.

**개선 제안**:
```python
# 도구별 카운트를 분리하여 의미에 맞는 표시
search_count = sum(
    1 for k in executed_tool_keys
    if k.startswith("search_use_cases:")
)
if search_count:
    refs.append({
        "source": "sql_history",
        "title": "유사 SQL 이력",
        "detail": f"{search_count}건의 유사 쿼리를 참조했습니다.",
    })
```
이를 위해서는 키에서 tool_name 추출이 필요하므로, M-1의 구분자 설계와 연관된다.

### M-4. enrichment fallback에서 `explored_tables`의 `model_dump()` 구조 불일치

**위치**: 설계안의 `_enrich_use_cases` 변경부

**문제**:
설계안에서 이미 탐색된 테이블을 메모리에서 채우는 fallback:
```python
existing_map[t.table_name] = [t.model_dump()]
```

기존 `fetched_tables`에 저장되는 값:
```python
# context_retriever.py:272
ct = TableMeta.from_meta(m)
entries.append(ct.model_dump())
```

두 경로의 `model_dump()` 결과가 구조적으로 동일한지 확인이 필요하다.
`explored_tables`에 저장된 TableMeta는 `context_retriever._extract_tables`에서 생성되며 `key_date_columns`, `schema_name` 등이 보강된 상태다.
반면 enrichment에서 직접 `TableMeta.from_meta(m)`로 생성한 것은 `key_date_columns`가 없을 수 있다.

따라서 fallback 경로의 데이터가 **더 풍부**하므로 기능적 문제는 없으나, 같은 테이블이 두 경로로 들어올 때 구조가 다를 수 있어 하류 처리에서 혼란을 줄 수 있다.

**개선 제안**:
fallback에서 `model_dump()` 대신 필요한 필드만 추출하는 정규화 함수를 사용하거나, enrichment 전용 축약 스키마를 정의한다.

### M-5. 누락된 수정 지점 — `test_agentic_flow_trace.py`와 `test_agentic_real_e2e.py`

**위치**: 
- `tests/auto/e2e/test_agentic_flow_trace.py:120, 269`
- `tests/manual/e2e/test_agentic_real_e2e.py:346`

**문제**:
설계안에서 테스트 수정 대상으로 `test_agentic_e2e.py:736-739`만 언급했으나, 실제로 `searched_queries`를 참조하는 테스트 파일이 추가로 2개 더 있다:

1. `test_agentic_flow_trace.py:120` — trace 기록에 `searched_queries` 포함
2. `test_agentic_flow_trace.py:269` — `ReasoningState(searched_queries=["고객"])` 직접 사용
3. `test_agentic_real_e2e.py:346` — 리포트에 `searched_queries` 출력

이들을 수정하지 않으면 필드명 변경 후 테스트가 깨진다.

### M-6. `_enrich_use_cases` 시그니처 변경이 호출부와 불일치

**위치**: 설계안의 `_enrich_use_cases` 시그니처 vs `context_retriever.py:186-188`

**문제**:
설계안에서 `_enrich_use_cases`의 시그니처를 변경한다:
```python
async def _enrich_use_cases(
    use_cases, executed_tool_keys: set[str],
    explored_tables: list[TableMeta],  # set[str] → list[TableMeta]으로 변경
    code_map,
)
```

그러나 호출부(`_apply_tool_result`)의 수정을 설계안에서 명시하지 않았다:
```python
# 현재 (context_retriever.py:185-188)
seen_tables = {t.table_name for t in explored_tables}
enriched = await _enrich_use_cases(
    result, searched_queries, seen_tables, code_map,
)
```

`seen_tables`(set[str])를 `explored_tables`(list[TableMeta])로 변경해야 하며, `searched_queries`도 `executed_tool_keys`로 변경해야 한다.
`_apply_tool_result`의 파라미터도 함께 변경해야 하고, `_run_step`에서 `_apply_tool_result`를 호출하는 부분도 변경해야 한다.

이 연쇄 변경이 설계안에서 빠져 있다.

---

## Minor (초록)

### m-1. `_DEDUP_TOOLS` frozenset 삭제의 명시성 손실

**위치**: 설계안 (`_DEDUP_TOOLS frozenset 삭제` 언급)

**문제**:
기존에는 어떤 도구가 dedup 대상인지 `_DEDUP_TOOLS`로 명시적으로 선언되어 있었다.
설계안에서는 "모든 도구가 통합 dedup 대상"이므로 삭제하지만, 새로운 도구가 추가될 때 dedup 동작이 자동으로 적용되는 것이 항상 바람직한 것은 아니다.
예를 들어 부작용이 있는 도구(실행 순서에 따라 결과가 달라지는 도구)가 추가되면 의도치 않은 스킵이 발생할 수 있다.

**개선 제안**:
`_DEDUP_TOOLS`를 삭제하는 대신, 주석으로 "현재 모든 도구는 멱등하므로 통합 dedup 적용" 근거를 남기거나, `_NO_DEDUP_TOOLS`(dedup 제외 목록) 방식으로 화이트리스트→블랙리스트 전환을 고려한다.

### m-2. `context_retriever_node`에서 `set` → `list` 변환 누락

**위치**: `context_retriever.py:382`

**문제**:
C-1에서 `set` 대신 `dict` 또는 `list`를 권장했는데, 만약 `set`을 그대로 사용한다면:
```python
# 현재: reason.searched_queries = searched_queries  (list)
# 변경 후: reason.executed_tool_keys = executed_tool_keys  (set)
```
`context_retriever_node`에서 로컬 변수로 `set(reason.executed_tool_keys)`를 사용하고, 마지막에 다시 state에 대입하는 패턴은 문제 없으나, `_run_step`에서 `searched_queries.append()`를 `executed_tool_keys.add()`로 변경할 때 `_run_step`의 파라미터 타입도 `list` → `set`으로 변경해야 한다.

### m-3. 프로토타입 코드(`docs/working/`)에 `searched_queries` 잔존

**위치**: `docs/working/agentic-loop-integration/prototype/` 하위 4개 파일

**문제**:
프로토타입 코드에도 `searched_queries`가 사용되고 있다.
프로토타입은 수정 대상이 아니라는 판단은 타당하나, 향후 프로토타입 참조 시 혼란을 줄 수 있다.

**개선 제안**:
프로토타입 코드 상단에 "이 코드는 v3 시점의 프로토타입이며 현재 구현과 다름" 주석을 추가하거나, 프로토타입 디렉토리에 README를 작성한다.

### m-4. `_build_execution_plan`의 파라미터 타입도 변경 필요

**위치**: `reasoning_preparer.py:337`

**문제**:
```python
def _build_execution_plan(
    knowledge_items: list[KnowledgeItem],
    searched_queries: list[str],  # → executed_tool_keys로 변경 필요
    ...
```
이 함수의 시그니처와 내부 로직(`meta_query not in searched_queries` → `f"search_table_meta:{meta_query}" not in executed_tool_keys`)도 변경해야 하는데, 설계안의 Read 지점 4번에서 언급은 했으나 함수 시그니처 변경은 명시하지 않았다.

---

## Info (정보)

### I-1. 성능 개선 효과 분석

기존 `searched_queries: list[str]`의 `in` 연산은 O(n)이며, 실제 파이프라인에서 n은 최대 ~30 (MAX_TOOL_CALLS 기본값 수준).
`set`으로 변경 시 O(1)이 되지만, n=30 수준에서 실질적 성능 차이는 무시할 수 있다.

다만 `_enrich_use_cases`에서 `already_queried = set(searched_queries)` (line 247)로 매번 set 변환하는 비용을 제거할 수 있으므로, 코드 정리 관점에서 의미가 있다.

### I-2. `docs/` 내 27개 파일에서 `searched_queries` 참조

검색 결과 `docs/` 내 27개 파일에서 `searched_queries`를 참조하고 있다.
필드명 변경 시 아키텍처 문서, 전략 문서, 리뷰 문서 등의 정합성도 함께 업데이트해야 한다.
'문서최신화 skill' 호출 대상이다.

### I-3. 설계 방향의 타당성 확인

전체 설계 방향 자체는 타당하다:
- flat namespace → tool-scoped namespace: **올바른 방향**
- 관측 도구를 dedup에 포함: **올바른 방향** (멱등 도구이므로)
- enrichment에서 bare table_name 대신 scoped key: **올바른 방향** (기존 충돌 해소)

---

## 요약

| 등급 | 건수 | 핵심 내용 |
|------|------|-----------|
| Critical | 2 | set 직렬화 호환성, recovery 시 탐색 차단 |
| Major | 6 | 키 충돌, dead write, UI 의미 왜곡, 구조 불일치, 누락 수정지점, 호출부 변경 누락 |
| Minor | 4 | 명시성 손실, 타입 변경 누락, 프로토타입 잔존, 함수 시그니처 |
| Info | 3 | 성능, 문서 영향, 방향성 확인 |

## 우선 조치 권장 사항

1. **C-1 해결**: `set[str]` 대신 `dict[str, bool]` 또는 `list[str]` + 로컬 set 캐시 패턴 사용
2. **M-2 해결**: `original_query:` prefix 기록 제거
3. **M-3 해결**: `insight_builder` UI 메시지를 도구별 카운트로 분리
4. **M-5 해결**: 누락된 테스트 수정 지점 보완
5. **M-6 해결**: `_apply_tool_result`, `_run_step` 파라미터 연쇄 변경 명시
6. 변경 완료 후 '문서최신화 skill' 호출하여 docs/ 내 27개 파일 정합성 확보
