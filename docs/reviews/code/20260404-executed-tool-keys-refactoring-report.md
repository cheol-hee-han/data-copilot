# Code Review: searched_queries -> executed_tool_keys 리팩터링

**리뷰 일자**: 2026-04-04
**리뷰 대상**: `searched_queries: list[str]` -> `executed_tool_keys: set[str]` 전환
**리뷰어**: Code Reviewer Agent

---

## 총평

전반적으로 깔끔한 리팩터링이다. `list[str]`에서 `set[str]`로 전환하여 O(n) 탐색을 O(1)로 개선하고, `"tool:input"` 네임스페이스를 도입하여 도구 간 중복 키 충돌을 방지한 점이 좋다. 아래 몇 가지 확인 사항과 개선 포인트를 등급별로 정리한다.

---

## 1. 잔존 참조 검토

### 1-1. src/, tests/ 내 searched_queries 잔존 여부

| 심각도 | 항목 |
|--------|------|
| Info | `src/` 및 `tests/` 코드 파일 내 `searched_queries` 문자열 **0건** -- 완전 제거 확인됨 |

### 1-2. 기타 잔존

| 심각도 | 위치 | 내용 |
|--------|------|------|
| Minor | `tests/reports/agentic_real_e2e_report.txt:59` | `searched_queries: ['고객별 여신 잔액 합계']` 문자열이 남아 있음. 테스트 리포트 파일(실행 결과 스냅샷)이므로 코드 동작에 영향은 없으나, 다음 테스트 실행 시 자동으로 갱신될 것이다. 수동 정리가 필요하면 해당 리포트를 재생성하면 된다. |

---

## 2. 일관성 검토 ("tool:input" 형식)

| 심각도 | 위치 | 판정 |
|--------|------|------|
| Info | `context_retriever.py:58` `_should_skip_step` | `f"{step.tool}:{step.input}"` -- 올바름 |
| Info | `context_retriever.py:102` `_run_step` | `f"{step.tool}:{step.input}"` -- 올바름 |
| Info | `context_retriever.py:254` `_enrich_use_cases` | `f"search_table_meta:{t}"` -- 올바름 |
| Info | `context_retriever.py:265` | `f"search_table_meta:{table_name}"` -- 올바름 |
| Info | `context_retriever.py:288` | `f"search_code_meta:{col}"` -- 올바름 |
| Info | `context_retriever.py:297` | `f"search_code_meta:{col_name}"` -- 올바름 |
| Info | `reasoning_preparer.py:357` | `f"search_table_meta:{meta_query}"` -- 올바름 |

모든 지점에서 `"tool_name:input_value"` 형식이 일관되게 사용되고 있다.

---

## 3. 타입 정합성 (set/list 혼용)

| 심각도 | 위치 | 판정 |
|--------|------|------|
| Info | `state.py:492` | `set[str]` 선언 -- 올바름 |
| Info | `context_retriever.py:370` | `set(reason.executed_tool_keys)` 로컬 복사 -- 올바름 |
| Info | `context_retriever.py:392` | `reason.executed_tool_keys = executed_tool_keys` (`set` -> `set`) -- 올바름 |
| Info | `reasoning_preparer.py:89` | `reason.executed_tool_keys` 직접 전달 (`set[str]`) -- 올바름 |
| Info | `test_agentic_e2e.py:738` | `{"search_table_meta:예금", ...}` set literal -- 올바름 |
| Info | `test_agentic_flow_trace.py:269` | `{"search_table_meta:고객"}` set literal -- 올바름 |
| Info | `test_agentic_real_e2e.py:346` | `list(reason.executed_tool_keys)` 출력용 변환 -- 올바름 |

**list/set 혼용 없음** -- 모든 지점에서 `set[str]` 타입이 정합적으로 사용된다.

### 3-1. LangGraph Checkpointer 직렬화 호환성

| 심각도 | 위치 | 내용 |
|--------|------|------|
| Warning | `state.py:492` | `set[str]`은 JSON 직렬화 시 `list`로 변환된다. Pydantic v2의 `model_dump(mode="json")`은 set을 list로 자동 변환하므로 **LangGraph의 기본 checkpointer(MemorySaver, PostgresSaver)에서는 문제없다** -- Pydantic BaseModel 하위 필드는 serde가 Pydantic에 위임되기 때문이다. 단, checkpoint에서 복원 시 `list`가 역직렬화되어 `set`으로 복원되는 흐름이 Pydantic `model_validate`를 통과하므로 정상이다. **현재 구조에서는 문제 없으나**, 향후 커스텀 serde를 추가하거나 raw JSON으로 checkpoint를 다루는 경우 set 순서 비결정성에 유의할 것. |

---

## 4. 죽은 코드 검토

| 심각도 | 항목 | 판정 |
|--------|------|------|
| Info | `_DEDUP_TOOLS` | `src/` 전체에서 **0건** -- 이미 제거 확인됨 |
| Info | 미사용 import | `context_retriever.py`의 import 목록이 모두 함수 내에서 사용됨 -- 문제 없음 |

---

## 5. 로직 흐름 검토

### 호출 체인: context_retriever_node -> _should_skip_step -> _run_step -> _apply_tool_result -> _enrich_use_cases

| 심각도 | 검토 항목 | 판정 |
|--------|-----------|------|
| Info | 파라미터 전달 일관성 | `executed_tool_keys: set[str]`가 모든 함수에 동일한 mutable reference로 전달된다. `_run_step` 내부에서 `.add()`로 직접 변이하고, `_enrich_use_cases`에서도 `.add()`로 변이한다. 동일 set 객체를 공유하므로 병렬 실행 시 도구 간 중복 방지가 실시간 반영된다. |
| Info | `explored_tables` 전달 | 마찬가지로 mutable list를 공유하며 `.extend()`로 변이. `_enrich_use_cases`에서도 읽기 참조. 일관적이다. |
| Info | `code_map` 전달 | `_run_step` -> `_apply_tool_result` -> `_enrich_use_cases`로 전달. `_enrich_use_cases`에서는 읽기만 하고 변이하지 않는다. 올바름. |

### 병렬 실행 시 mutable 공유에 대한 주의

| 심각도 | 위치 | 내용 |
|--------|------|------|
| Warning | `context_retriever.py:383-386` | `asyncio.gather`로 `_run_step`을 병렬 실행할 때, 모든 코루틴이 동일한 `executed_tool_keys` set과 `explored_tables` list를 공유한다. Python의 GIL 덕분에 `set.add()`와 `list.extend()`는 thread-safe하지만, asyncio에서는 **await 지점 사이의 인터리빙**이 발생할 수 있다. 현재 구조에서는 각 `_run_step`이 독립 도구를 호출하고, 키 등록은 결과 수신 후 수행되므로 **실질적인 race condition 위험은 낮다**. 다만 이 설계 의도를 주석으로 명시하면 유지보수성이 높아진다. |

---

## 6. Enrichment Fallback 검토

### _enrich_use_cases: explored_tables -> existing_map -> uc_tables fallback

| 심각도 | 위치 | 내용 |
|--------|------|------|
| Info | Line 246-256 | `seen_tables`(이미 탐색된 테이블명 set)와 `executed_tool_keys`(도구 실행 이력) **이중 필터**로 중복 조회를 방지한다. `seen_tables`는 메모리에 실제 있는 테이블, `executed_tool_keys`는 조회를 시도했으나 결과가 없었을 수 있는 테이블까지 커버한다. 올바른 이중 방어 구조이다. |
| Info | Line 331-336 | `fetched_tables` (새로 조회) -> `existing_map` (기존 explored_tables) 순서로 fallback. 새로 조회한 결과가 우선이고, 없으면 기존 것을 사용한다. 의도대로 동작한다. |

### 잠재적 개선 포인트

| 심각도 | 위치 | 내용 |
|--------|------|------|
| Minor | Line 252-256 | `t not in seen_tables` 조건과 `f"search_table_meta:{t}" not in executed_tool_keys` 조건이 동시에 필요한 이유는 -- `executed_tool_keys`에 키가 있지만 `explored_tables`에 결과가 없는 경우(조회 실패/빈 결과)를 커버하기 위함이다. 이 의도를 인라인 주석으로 명확히 하면 좋겠다. 현재 주석 `# (3) 중복 제거 후 테이블 메타 일괄 조회`는 "왜 이중 조건인지"를 설명하지 않는다. |

---

## 7. reasoning_preparer: searched_queries write 제거

| 심각도 | 위치 | 내용 |
|--------|------|------|
| Info | `reasoning_preparer.py:60-102` | `searched_queries` 관련 write가 완전히 제거되었다. `executed_tool_keys`는 `_build_execution_plan`에 읽기 전용으로 전달되어 중복 스텝 방지에만 사용된다. 실제 key 등록은 `context_retriever_node`에서만 수행되므로 책임이 명확하게 분리되었다. |
| Info | 의존성 검토 | `src/` 전체에서 `searched_queries` 문자열이 0건이므로, 해당 write에 의존하는 코드는 없다. |

---

## 8. insight_builder: explored_use_cases 전환

| 심각도 | 위치 | 내용 |
|--------|------|------|
| Info | `insight_builder.py:229-236` | `searched_queries` 참조를 제거하고 `explored_use_cases`로 전환. `_get_attr_or_key(reason, "explored_use_cases", [])`로 안전하게 접근하며, 길이만 사용하므로 자연스러운 전환이다. |

---

## 9. 테스트 검토

### test_agentic_e2e.py

| 심각도 | 위치 | 내용 |
|--------|------|------|
| Info | Line 736-742 `test_06_executed_tool_keys_dedup` | set literal `{"search_table_meta:예금", "search_code_meta:상태"}`으로 초기화하고 `in` 연산자로 멤버십 확인. `set`의 네임스페이스 분리(`tool:input`)를 검증하는 좋은 테스트다. |

### test_agentic_flow_trace.py

| 심각도 | 위치 | 내용 |
|--------|------|------|
| Info | Line 120 | `reason.executed_tool_keys`를 trace dict에 기록. 출력용이므로 set 그대로 저장해도 무방하다. |
| Info | Line 269 | `executed_tool_keys={"search_table_meta:고객"}`으로 사전 등록된 키가 스킵을 유발하는지 검증. `context_retriever_node`를 실제 호출하여 `StepStatus.SKIPPED`를 확인하는 통합 테스트이며, 새 구조에 올바르게 대응한다. |

### test_agentic_real_e2e.py

| 심각도 | 위치 | 내용 |
|--------|------|------|
| Info | Line 346 | `list(reason.executed_tool_keys)`로 리포트 출력용 변환. 올바름. |

### 추가 테스트 제안

| 심각도 | 항목 | 내용 |
|--------|------|------|
| Minor | 누락 테스트 | `_enrich_use_cases`의 중복 방지 로직(`executed_tool_keys`에 이미 있는 테이블 스킵)을 단위 테스트하는 케이스가 없다. `_enrich_use_cases`가 동일 테이블을 2번 조회하지 않는 것을 검증하는 테스트를 추가하면 회귀 방지에 도움이 된다. |

---

## 10. recovery_agent 검토

| 심각도 | 위치 | 내용 |
|--------|------|------|
| Info | Line 414-419 | `_build_exploration_history` 함수의 docstring에서 `executed_tool_keys`를 언급하면서 "모든 도구 실행 이력이 섞여 있으므로 explored_use_cases의 `_search_query` 태그만으로 그루핑한다"고 설명한다. 이 설명이 리팩터링 의도와 정확히 부합한다 -- `executed_tool_keys`는 중복 방지 전용이고, 검색 이력 표시에는 `explored_use_cases`를 사용하는 것이 맞다. |

---

## 이슈 요약

| 등급 | 건수 | 요약 |
|------|------|------|
| Critical | 0 | -- |
| Warning | 2 | Checkpointer set 직렬화 유의사항, asyncio.gather 공유 mutable 주석 보강 |
| Minor | 2 | `_enrich_use_cases` 이중 조건 주석 보강, enrichment 중복 방지 단위 테스트 추가 |
| Info | 다수 | 잔존 참조 완전 제거 확인, 타입 정합성 확인, 로직 흐름 정상 등 |

---

## 결론

`searched_queries` -> `executed_tool_keys` 리팩터링은 **완전하고 일관적**으로 수행되었다. 잔존 참조 없음, 타입 정합성 확보, 호출 체인의 파라미터 전달 일관성 확인. Critical 이슈는 없으며, Warning 2건은 현재 동작에는 영향이 없으나 유지보수 관점에서의 방어적 조치이다.
