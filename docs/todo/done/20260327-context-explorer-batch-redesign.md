# context_explorer 배치 해석 구조 개선

> 작성일: 2026-03-27
> 근거: trace_session-1774565791726 (예금신규 TOP 3 질의 → 123초 소요, LLM 27회, 최종 실패)

---

## 1. 문제 요약

### 1-1. 도구 결과마다 LLM 개별 호출 (핵심 병목)
- `_run_step` → `_interpret_result` → `_interpret_with_llm` 경로로, 매 도구 실행 결과마다 LLM 호출
- trace 기준: search_table_meta 12회 + search_use_cases 4회 등 → LLM 27회, 55초 소비
- 도구 실행 자체는 4~30ms로 빠르지만, 해석 LLM이 2~4초씩 추가

### 1-2. 개별 해석이라 교차 참조 불가
- DEP201P 메타를 해석할 때 use_cases에서 이미 확인한 조인 구조(BLNG_BRCD)를 참고 못함
- DEP219M을 해석할 때 DEP201P가 이미 적합하다는 판단을 모름
- 각 해석이 독립적이라 맥락 없이 개별 판단

### 1-3. 비교 판정이 개별 LLM 해석 비용을 다 쓴 후에야 실행됨
- `_run_table_comparison`은 탐색 루프 전부 종료 후 후처리 3단계에서 실행
- 이미 LLM 개별 해석 비용(55초, 12+회)을 다 쓴 후에야 rejected 제거
- 참고: 날짜 분포/샘플 조회는 DB 쿼리(수십ms)로 비용 무시 가능하며, 오히려 비교 판정의 정확도를 높이는 핵심 입력임

### 1-4. rejected 테이블이 readiness 점수를 낮춤
- LLM이 "부적절"하다고 판단해도 new_tables에 포함시켜 candidate_tables에 누적
- evaluate_readiness의 term_resolution = confirmed / 전체 knowledge_items
- 무관한 테이블의 knowledge가 분모를 키워 점수 하락 (trace: 29%로 고정)

### 1-5. recovery_planner가 새 테이블을 찾지 못함
- dead_ends.tried_tables에 candidate_tables 전체가 들어감 (selected/rejected 구분 없음)
- 재계획 시 candidate_tables에서 미검색 테이블을 찾지만, 전부 tried → 없음
- fallback으로 search_use_cases만 반복 (trace: 3회 재계획 모두 use_cases만 검색)

---

## 2. 개선 설계

### 2-1. context_explorer_node 메인 루프 재구성

**AS-IS:**
```
for step in execution_plan:
    result = execute_tool(step)                     # 4~30ms
    tables = _extract_tables(step, result)           # rule-based
    insight, knowledge, llm_tables                   # ← LLM 매번 호출 (2~4초)
        = _interpret_with_llm(step, result, query)
    _merge_llm_inferred_fields(tables, llm_tables)
    candidate_tables += tables
    knowledge_items += knowledge
    if _is_ready_to_generate(): break

# 후처리
1. _observe_all_date_distributions(candidate_tables)  # 전체 대상
2. _sample_unsampled_tables(candidate_tables)          # 전체 대상
3. _run_table_comparison → rejected 제거               # 늦음
4. _promote_sampled_confidence
```

**TO-BE:**
```
# ── Phase 1: 도구 전부 실행 (rule-based만, LLM 없음) ──
collected_results = []
for step in execution_plan:
    if step.status != "PENDING" or budget 초과: continue
    if _should_skip_step(step, ...): continue
    result = execute_tool(step)
    tables = _extract_tables(step, result)          # rule-based CandidateTable 추출
    candidate_tables += tables
    collected_results.append((step, result))        # raw 결과만 수집
    # tracker 기록은 여기서 (도구별 latency, results_count)

# ── Phase 2: 관찰 데이터 수집 (DB 쿼리, 전체 대상) ──
# 날짜 분포/샘플은 DB 쿼리(수십ms)로 비용 무시 가능하며,
# 비교 판정의 정확도를 높이는 핵심 입력이므로 배치 해석 전에 수집한다.
_observe_all_date_distributions(candidate_tables)
_sample_unsampled_tables(candidate_tables, sampled_tables)

# ── Phase 3: 배치 LLM 해석 (1회, 관찰 데이터 포함) ──
batch_result = await _interpret_batch(
    collected_results, candidate_tables, original_query, time_slot,
)
# 실패 시 _interpret_rule_based fallback (도구별 _RULE_DISPATCH 사용)

# ── Phase 4: 해석 결과 반영 ──
knowledge_items += batch_result.knowledge_updates
_merge_llm_inferred_fields(candidate_tables, batch_result.new_tables)

# ── Phase 5: 불필요 테이블 제거 ──
rejected_tables += batch_result.rejected
candidate_tables[:] = [t for t in candidate_tables
                       if t.table_name not in batch_result.rejected]
# rejected 테이블의 knowledge_items도 제거
knowledge_items[:] = [ki for ki in knowledge_items
                      if not _is_rejected_table_knowledge(ki, batch_result.rejected)]

# ── Phase 6: confidence 승격 + readiness 체크 ──
_promote_sampled_confidence(candidate_tables, knowledge_items)
# 배치 해석 후 1회만 체크 (도구 실행은 빠르므로 조기 탈출 불필요)
```

### 2-2. 배치 해석 프롬프트 설계

기존 `REASON_EXPLORE_OBSERVE`(개별 해석)와 `REASON_TABLE_COMPARISON`(비교 판정)을 하나의 배치 프롬프트로 통합한다.

**프롬프트 입력:**
```
- original_query: 원본 질의
- time_slot: 정규화된 시간 조건 (예: "2025-03-27 ~ 2026-03-27")
- tool_results[]: 모든 도구 결과 (tool_name, tool_input, tool_purpose, tool_result)
- table_observations[]: 후보 테이블별 관찰 데이터 (_build_table_block 재활용)
  - 날짜 분포/패턴 (관찰)
  - 샘플 데이터 (관찰)
  - 메타 설명, 컬럼 목록 (메타 원본)
```

**프롬프트 출력 (JSON):**
```json
{
  "interpretations": [
    {
      "tool_name": "search_use_cases",
      "tool_input": "...",
      "insight": "조인 구조 확인: DEP201P.BLNG_BRCD = COM001M.BLNG_BRCD",
      "knowledge_updates": [
        {
          "key": "table:TB_ADW_DEP201P",
          "value": "...",
          "confidence": 0.8,
          "new_status": "CONFIRMED",
          "source": "활용사례",
          "evidence": "...",
          "is_critical": true
        }
      ],
      "new_tables": [
        {
          "table_name": "TB_ADW_DEP201P",
          "entity_scope": "...",
          "functional_usage": "...",
          "data_refresh_hint": "..."
        }
      ]
    },
    {
      "tool_name": "search_table_meta",
      "tool_input": "TB_ADW_DEP219M",
      "insight": "휴면예금 테이블로 질의와 무관",
      "knowledge_updates": [],
      "new_tables": []
    }
  ],
  "selected": ["TB_ADW_DEP201P", "TB_ADW_COM001M"],
  "rejected": ["TB_ADW_DEP219M", "TB_ADW_DEP220M", ...],
  "comparison_reason": "DEP201P에 OPEN_DT(개설일자)와 BLNG_BRCD(지점코드)가 있어 적합. 나머지는 휴면/미수령/세금 등 무관"
}
```

**장점:**
- use_cases 결과를 보면서 table_meta를 해석 → 교차 참조로 정확도 향상
- 부적합 테이블을 맥락 속에서 판단 ("DEP201P가 이미 적합하니 DEP219M은 불필요")
- LLM 호출 12+1회 → 1회
- 해석과 비교 판정이 한 번에 완료

**기존 프롬프트 병합 요소:**
- `context_explorer_system.txt`의: 관찰 메모 기준, 지식 항목 상태 판정 (CANDIDATE/PROBABLE/CONFIRMED), is_critical 판단, 3측면 필드 추론
- `table_comparison_system.txt`의: 출처 태그 신뢰 우선순위 (메타 원본 > 관찰 > LLM 추론), 시간 조건 부합 판단, selected/rejected 분류

### 2-3. ReasoningState에 rejected_tables 필드 추가

**파일:** `src/agents/state/state.py`

```python
class ReasoningState(BaseModel):
    # ── 누적 지식 (기존) ──
    candidate_tables: list[CandidateTable] = Field(default_factory=list)

    # ── 신규 ──
    rejected_tables: list[str] = Field(default_factory=list)
    # 배치 해석에서 부적합 판정된 테이블명 목록
    # recovery_planner가 tried vs rejected를 구분하는 데 사용
```

### 2-4. DeadEnd에 rejected_tables 구분 추가

**파일:** `src/agents/state/state.py`

```python
class DeadEnd(BaseModel):
    hypothesis_id: str
    reason: str
    tried_tables: list[str] = Field(default_factory=list)   # 탐색했으나 SQL 생성 실패
    rejected_tables: list[str] = Field(default_factory=list) # 부적합 판정으로 제외 (신규)
    tried_terms: list[str] = Field(default_factory=list)
    failure_type: FailureType = "no_use_case"
```

### 2-5. recovery_planner 개선

**파일:** `src/agents/nodes/reason/recovery_planner.py`

**(a) dead_ends 기록 시 selected/rejected 구분:**

```python
# 현재 (line 52-60)
dead_ends.append(DeadEnd(
    tried_tables=[ct.table_name for ct in reason.candidate_tables],  # 전부 tried
))

# 개선
dead_ends.append(DeadEnd(
    tried_tables=[ct.table_name for ct in reason.candidate_tables],  # selected만 남아있음
    rejected_tables=list(reason.rejected_tables),                    # 신규
))
```

**(b) _build_replan_execution fallback 개선:**

```python
# 현재 (line 387-393): 테이블 스텝이 없으면 search_use_cases만 fallback
if not steps:
    steps.append(ExecutionStep(
        tool="search_use_cases",
        input=hypothesis.strategy,
    ))

# 개선: 새 키워드로 테이블 메타도 직접 검색
if not steps:
    # 가설의 missing_terms에서 검색 키워드 추출
    search_kw = " ".join(hypothesis.missing_terms[:3]) if hypothesis.missing_terms else hypothesis.strategy
    steps.append(ExecutionStep(
        tool="search_table_meta",
        input=search_kw,
        purpose="새 가설 기반 테이블 직접 검색",
    ))
    steps.append(ExecutionStep(
        tool="search_use_cases",
        input=hypothesis.strategy,
        purpose="새 가설 기반 활용사례 재검색",
    ))
```

**(c) replan 프롬프트에 rejected 정보 전달:**

`_build_replan_context`에 rejected_tables를 추가하여 LLM이 "DEP219M은 휴면예금이라 이미 제외됨"을 알고 새 방향을 잡을 수 있게 한다.

---

## 3. 수정 대상 파일

| 파일 | 변경 내용 |
|---|---|
| `src/agents/state/state.py` | ReasoningState에 `rejected_tables` 필드 추가. DeadEnd에 `rejected_tables` 필드 추가 |
| `src/agents/nodes/reason/context_explorer.py` | 메인 루프를 Phase 1~6으로 재구성. `_interpret_batch` 신규 함수 추가. `_run_step`에서 LLM 호출 제거 (Phase 1). rejected 제거 로직 추가 (Phase 4). 후처리 순서 변경 (Phase 5) |
| `resources/prompts/reason/` | 배치 해석+비교 통합 프롬프트 신규 작성 (예: `batch_interpret_system.txt`) |
| `src/agents/nodes/system_prompts.py` | 신규 프롬프트 상수 등록 (`REASON_BATCH_INTERPRET`) |
| `src/agents/nodes/reason/recovery_planner.py` | dead_ends에 rejected 구분 기록. fallback에 search_table_meta 추가. replan 컨텍스트에 rejected 정보 전달 |

## 4. 기존 코드 유지/제거 판단

| 함수/모듈 | 판단 | 이유 |
|---|---|---|
| `_interpret_with_llm` | **유지** | 배치 LLM 실패 시 개별 fallback 경로로 활용 |
| `_interpret_rule_based` + `_RULE_DISPATCH` | **유지** | LLM 전면 실패 시 최종 fallback |
| `_interpret_result` | **수정** | Phase 1에서는 rule-based만 사용. 배치 실패 시 기존 LLM 개별 호출 경로로 전환 |
| `_extract_tables` | **유지** | Phase 1에서 rule-based CandidateTable 추출에 그대로 사용 |
| `_merge_llm_inferred_fields` | **유지** | Phase 3에서 배치 결과의 3측면 필드 병합에 사용 |
| `_build_comparison_block` | **유지** | 배치 프롬프트 입력 구성 시 재활용 가능 |
| `_build_table_block` | **유지** | 위와 동일 |
| `_find_comparison_groups` | **제거 가능** | 배치 프롬프트가 전체 테이블을 한 번에 비교 |
| `_run_table_comparison` | **제거 가능** | 배치 프롬프트에 통합 |
| `_group_by_keyword` / `_group_by_prefix` | **제거 가능** | 위와 동일 |
| `REASON_EXPLORE_OBSERVE` 프롬프트 | **유지** | 개별 fallback 경로에서 사용 |
| `REASON_TABLE_COMPARISON` 프롬프트 | **유지** | 개별 fallback 경로에서 사용 |

## 5. 예상 효과

| 지표 | AS-IS (trace 기준) | TO-BE (예상) |
|---|---|---|
| LLM 호출 횟수 (explore) | 27회 | 1회 (배치) + 후속 노드 |
| explore 소요 시간 | 55초 | ~5초 (도구 실행) + ~5초 (배치 LLM 1회) |
| 날짜분포/샘플 조회 대상 | 12개 테이블 | 12개 (전체, 비교 판정 입력용. DB 쿼리라 ~1초) |
| readiness 점수 | 29% (분모 17) | 개선 (분모에서 rejected 제거) |
| 재계획 성공률 | 0% (3회 모두 실패) | 개선 (새 키워드 테이블 검색 가능) |

## 6. 미결정 사항

### 6-1. 배치 프롬프트 토큰 상한
- 12개 테이블 메타 + 5개 use_cases ≒ 6,000~8,000 토큰 (수용 가능 판단)
- 소형 모델(8K 컨텍스트)에서 프롬프트 + 출력이 넘칠 경우 대비 전략 필요
- **옵션 A**: tool_result를 요약(컬럼명만, description 축약)하여 토큰 절감
- **옵션 B**: 테이블 수가 N개 초과 시 2회로 분할 배치

### 6-2. 조기 탈출 제거의 영향
- Phase 1에서 도구를 전부 실행 후 Phase 2에서 배치 해석하므로 중간 조기 탈출 불가
- 도구 실행이 4~30ms로 빠르므로 영향 미미하다고 판단
- 단, MAX_TOOL_CALLS 예산 제한은 Phase 1 루프에서 그대로 적용

### 6-3. search_use_cases 결과도 배치에 포함할지
- 현재 설계: 모든 도구 결과(use_cases, table_meta, code_meta, glossary, manual)를 한 배치에 포함
- use_cases는 SQL 텍스트가 길어서 토큰을 많이 차지할 수 있음
- **옵션 A**: 전부 포함 (교차 참조 정확도 최대화)
- **옵션 B**: use_cases는 상위 3건만 요약 포함
- 현재 `_interpret_with_llm`에서도 result[:5]로 잘라서 3000자 제한하고 있으므로 동일 전략 적용 가능

### 6-4. fallback 전략 (배치 LLM 실패 시)
- **옵션 A**: 기존 `_interpret_with_llm` 개별 호출로 전환 (안전하지만 느림)
- **옵션 B**: `_interpret_rule_based`만 사용 (빠르지만 insight 품질 저하)
- **옵션 C**: A → B 순차 시도 (개별 LLM도 실패하면 rule-based)

---

## 7. 개선사항 목록 (이유 포함)

### I-1. 도구 실행과 LLM 해석을 분리하여 배치 처리

- **현상**: 도구 실행마다 `_interpret_with_llm`을 호출하여 LLM 27회, 55초 소비
- **원인**: `_run_step` 내부에서 도구 실행 → LLM 해석이 결합되어 있음 (`context_explorer.py:100`)
- **이유**: LLM 해석 자체는 가치 있음 (컬럼 조합으로 용도 유추, 부재 판단 등 rule-based로 어려운 추론). 문제는 건건이 호출하는 구조
- **개선**: Phase 1(도구 전부 실행, rule-based만) → Phase 2(배치 LLM 1회)로 분리

### I-2. 배치 해석에서 교차 참조 활성화

- **현상**: DEP201P 메타 해석 시 use_cases의 조인 구조를 참고 못함. 각 해석이 독립적
- **원인**: `_interpret_with_llm`이 단일 도구 결과만 입력받는 프롬프트 구조
- **이유**: use_cases에서 "DEP201P.BLNG_BRCD = COM001M.BLNG_BRCD" 조인이 검증되었으면, table_meta 해석 시 confidence를 바로 올릴 수 있어야 함
- **개선**: 모든 도구 결과를 한 프롬프트에 넣어 LLM이 교차 참조하여 해석

### I-3. 해석과 비교 판정을 하나의 배치 프롬프트로 통합

- **현상**: 개별 해석(REASON_EXPLORE_OBSERVE) 12+회 → 후처리에서 비교 판정(REASON_TABLE_COMPARISON) 별도 호출
- **원인**: 해석 프롬프트와 비교 프롬프트가 분리 설계됨. 비교 판정은 후처리 3단계에 위치
- **이유**: 해석 시점에 이미 "이 테이블은 질의에 적합/부적합" 판단이 가능. 별도 비교 단계가 중복
- **개선**: `batch_interpret_system.txt` 신규 프롬프트에서 interpretations + selected/rejected를 한 번에 출력

### I-4. rejected 테이블을 candidate_tables와 knowledge_items에서 즉시 제거

- **현상**: evaluate_readiness 점수 29%로 고정. knowledge=2/17로 분모가 계속 증가
- **원인**: LLM이 "부적절"하다고 판단해도 candidate_tables/knowledge_items에서 제거되지 않음. 비교 판정이 후처리라 제거 시점이 늦고, 제거 후에도 knowledge_items는 남아있음
- **이유**: readiness 점수 = confirmed / 전체 knowledge. 무관한 테이블의 knowledge가 분모를 키움
- **개선**: Phase 4에서 rejected 테이블과 해당 knowledge_items를 즉시 제거

### I-5. 날짜 분포/샘플 데이터를 비교 판정의 입력으로 활용

- **현상 (AS-IS)**: 날짜 분포/샘플 조회 → 비교 판정 순서이지만, 개별 LLM 해석이 이미 비용을 소진
- **현상 (TO-BE 초안의 문제)**: 비교 판정을 앞당기면서 날짜 분포/샘플 없이 판정하려 함 — 과도한 최적화
- **이유**: 날짜 분포/샘플은 DB 쿼리(수십ms)로 비용 무시 가능하며, 비교 판정의 핵심 입력. `table_comparison_system.txt`도 "(관찰) 데이터를 우선 신뢰"하도록 설계되어 있음. "이 테이블은 월별만 있는데 질의는 일별 기간 조건" 같은 판단에 필수
- **개선**: Phase 2에서 전체 테이블 대상으로 날짜 분포/샘플을 먼저 조회하고, Phase 3 배치 LLM에 관찰 데이터를 포함하여 비교 판정 정확도 확보

### I-6. ReasoningState에 rejected_tables 필드 추가

- **현상**: recovery_planner가 tried(적합했으나 실패) vs rejected(부적합으로 제외)를 구분 못함
- **원인**: state에 rejected 정보를 보존하는 필드가 없음
- **이유**: 재계획 시 "DEP201P는 적합했지만 매핑 실패"와 "DEP219M은 휴면예금이라 제외"를 구분해야 더 나은 가설 수립 가능
- **개선**: `ReasoningState.rejected_tables: list[str]` 추가

### I-7. DeadEnd에 rejected_tables 구분 추가

- **현상**: dead_ends.tried_tables에 candidate_tables 전체(selected+rejected)가 기록됨
- **원인**: `recovery_planner.py:55-57`에서 `reason.candidate_tables` 전체를 tried로 기록
- **이유**: TO-BE에서는 candidate_tables에 selected만 남지만, rejected 정보도 dead_ends에 기록해야 replan LLM이 참고 가능
- **개선**: `DeadEnd.rejected_tables: list[str]` 추가, dead_ends 기록 시 분리

### I-8. recovery_planner fallback에 search_table_meta 추가

- **현상**: 3회 재계획이 모두 search_use_cases만 반복하고 새 테이블을 찾지 못함
- **원인**: `_build_replan_execution`에서 candidate_tables 중 미검색 테이블이 없으면 search_use_cases만 fallback (`recovery_planner.py:387-393`)
- **이유**: 재계획의 목적은 새 방향 탐색인데, 테이블 직접 검색 없이 use_cases만 반복하면 같은 결과만 얻음
- **개선**: fallback 시 `hypothesis.missing_terms` 기반 search_table_meta 스텝도 생성

### I-9. replan 프롬프트에 rejected 정보 전달

- **현상**: replan LLM이 이전에 부적합 판정된 테이블 정보를 모름
- **원인**: `_build_replan_context`에 rejected_tables 정보가 포함되지 않음
- **이유**: "DEP219M은 휴면예금이라 이미 제외됨"을 알아야 같은 방향을 반복하지 않음
- **개선**: replan 컨텍스트에 rejected_tables + rejection 이유 추가

---

## 8. 구현 단계별 TODO

### Step 1: State 모델 확장
- [ ] `state.py`: `ReasoningState`에 `rejected_tables: list[str]` 필드 추가
- [ ] `state.py`: `DeadEnd`에 `rejected_tables: list[str]` 필드 추가
- [ ] 기존 테스트/타입 체크가 깨지지 않는지 확인 (default_factory=list이므로 하위호환)

### Step 2: 배치 해석 프롬프트 작성
- [ ] `resources/prompts/reason/batch_interpret_system.txt` 신규 작성
  - `context_explorer_system.txt`의 해석 기준 병합 (관찰 메모, 상태 판정, is_critical, 3측면)
  - `table_comparison_system.txt`의 비교 기준 병합 (출처 태그, 시간 조건, selected/rejected)
  - 입력: `{original_query}`, `{time_slot}`, `{tool_results}`
  - 출력: interpretations[] + selected[] + rejected[] + comparison_reason
  - few-shot 예제 2~3개 포함
- [ ] `system_prompts.py`에 `REASON_BATCH_INTERPRET` 상수 등록

### Step 3: context_explorer 메인 루프 재구성

- [ ] `_interpret_batch` 신규 함수 구현
  - 입력: `collected_results`, `candidate_tables` (관찰 데이터 포함), `original_query`, `time_slot`
  - tool_results 직렬화 (tool당 result[:5], 3000자 제한 — 기존 `_interpret_with_llm`과 동일 전략)
  - candidate_tables의 관찰 데이터 직렬화 (`_build_table_block` 재활용: 날짜 분포, 샘플, 메타)
  - LLM 호출 1회 → JSON 파싱
  - 반환 타입: `BatchInterpretResult` (interpretations, knowledge_updates, new_tables, selected, rejected, comparison_reason)
  - 실패 시 fallback: 기존 `_interpret_rule_based`를 도구별로 적용 (결정사항 6-4)
- [ ] `_is_rejected_table_knowledge` 헬퍼 함수 구현
  - `ki.key`가 `"table:{rejected_name}"` 형태인지 판정
- [ ] `context_explorer_node` 함수 재구성
  - Phase 1: 도구 실행 루프 — `_run_step`에서 LLM 호출 제거, raw 결과만 수집
    - `_extract_tables`는 그대로 호출 (rule-based CandidateTable 추출)
    - tracker 기록은 여기서 유지 (도구별 latency, results_count)
    - `_should_skip_step`, `MAX_TOOL_CALLS` 제한 그대로 적용
  - Phase 2: 관찰 데이터 수집 (`_observe_all_date_distributions`, `_sample_unsampled_tables` — 전체 테이블 대상, DB 쿼리)
  - Phase 3: `_interpret_batch` 호출 (관찰 데이터 포함)
  - Phase 4: knowledge_items += batch_result.knowledge_updates, `_merge_llm_inferred_fields` 호출
  - Phase 5: rejected 테이블/knowledge 제거, `reason.rejected_tables` 업데이트
  - Phase 6: `_promote_sampled_confidence` + readiness 체크 (배치 후 1회만)
- [ ] `_run_step` 함수 수정
  - `_interpret_result` 호출 제거
  - 도구 실행 + tracker 기록만 수행
  - 반환: `(step, result)` 튜플 (collected_results에 추가용)

### Step 4: 기존 비교 판정 코드 정리
- [ ] `_find_comparison_groups`, `_group_by_keyword`, `_group_by_prefix` — 제거 또는 주석 처리
- [ ] `_run_table_comparison` — 제거 또는 주석 처리
- [ ] `context_explorer_node` 후처리에서 기존 비교 판정 호출 제거
- [ ] `_interpret_with_llm`, `_interpret_result`, `_interpret_rule_based` — fallback용으로 유지 (호출 경로만 변경)

### Step 5: recovery_planner 개선
- [ ] `recovery_planner_node`에서 dead_ends 기록 시 `rejected_tables` 포함
  ```python
  dead_ends.append(DeadEnd(
      tried_tables=[ct.table_name for ct in reason.candidate_tables],
      rejected_tables=list(reason.rejected_tables),
  ))
  ```
- [ ] `_build_replan_execution` fallback 개선
  - `search_table_meta` 스텝 추가 (hypothesis.missing_terms 기반 키워드)
  - 기존 `search_use_cases` 유지 (순서: table_meta → use_cases)
- [ ] `_build_replan_context`에 rejected_tables 정보 추가
- [ ] replan 프롬프트(`replan_system.txt`)에 `{rejected_tables}` 변수 추가

### Step 6: 검증
- [ ] 기존 trace 시나리오("예금신규 TOP 3") 재실행하여 비교
  - LLM 호출 횟수 감소 확인
  - explore 소요 시간 감소 확인
  - readiness 점수 개선 확인
  - 재계획 시 새 테이블 탐색 여부 확인
- [ ] Fast-Path 시나리오 (유사 SQL 고유사도 매칭) 정상 동작 확인
- [ ] Cold Start 시나리오 (use_cases 0건) 정상 동작 확인
- [ ] 배치 LLM 실패 시 fallback 경로 동작 확인
