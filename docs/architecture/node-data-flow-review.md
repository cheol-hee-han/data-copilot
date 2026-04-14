# 판단/평가 노드 데이터 흐름 및 State 설계 검토

> 작성일: 2026-04-02 | 최종 수정: 2026-04-13
> 범위: Reason 계층 6개 판단 노드 + Interpret/Present 계층 2개 노드
> 근거: 소스코드 전수 검토 (state.py, 각 노드 .py, 프롬프트 .txt)
> v1.1 (2026-04-13): 현행 코드 기준 재검증 — join_keys 미구현 반영, code_map/preprocessed_input/CANDIDATE 포함/loop_guard 반영 완료 상태 표기

---

## 1. 노드별 데이터 공급 현황 분석

### 1.1 reasoning_preparer_node (초기화)

| 필요 데이터 | 소스 | 공급 상태 | 비고 |
|-------------|------|-----------|------|
| `preprocessed_input` | intent_classifier | **정상** | sanitize 완료된 사용자 질의 |
| `normalized_query` | query_normalizer (선택적) | **조건부** | `settings.normalization_enabled=False`이면 None |
| `resolved_signals` | clarification_handler | **정상** | INFER 모호성 참조용 |

**분석**:
- `normalized_query`가 None일 때 `_build_decomposition_from_normalized()`이 빈 dict를 반환하여 **knowledge_items가 0건으로 초기화됨**.
- 이 경우 `_build_execution_plan()`에서 filter 기반 코드메타 검색도 생성되지 않아, **탐색 범위가 search_use_cases + search_table_meta 2건으로 축소됨**.
- **정규화 비활성 시 탐색 전략이 매우 제한적** — 개선 필요.

**누락 데이터**:
- `conversation_history`가 reasoning_preparer에 전달되지 않음. 대화 맥락에서 이전 턴에서 언급된 테이블명이나 조건을 활용할 수 없음.
- `continue_context`(intent_classifier 산출)도 reasoning_preparer에서 참조하지 않음. 연속 대화에서 이전 맥락이 실행계획에 반영되지 않음.

---

### 1.2 context_retriever_node (도구 실행)

| 필요 데이터 | 소스 | 공급 상태 | 비고 |
|-------------|------|-----------|------|
| `reason.execution_plan` | reasoning_preparer / recovery_agent | **정상** | PENDING 스텝 순차 실행 |
| `reason.searched_queries` | 자체 누적 | **정상** | 중복 방지용 |
| `reason.candidate_tables` | 자체 누적 + from_meta | **정상** | 메타에서 rule-based 추출 |
| `reason.code_map` | 자체 누적 | **정상** | search_code_meta 결과 축적 |
| `reason.explored_use_cases` | 자체 누적 | **정상** | search_use_cases 결과 축적 |

**분석**:
- `_fetch_use_case_related_metas()`가 유사SQL에서 sqlglot으로 추출한 테이블을 **자동 후속 수집**하는 구조가 우수.
- **tool_calls 카운트에 포함하지 않는 점**이 문제: 실제로는 DB 호출이 발생하지만 loop_guard가 인지하지 못함.

**데이터 형태 문제**:
- `_extract_tables()`에서 `CandidateTable.from_meta(meta)` 호출 시, MongoDB 응답의 `columns` 필드가 list[dict] 형태인데, 각 dict의 키명이 MongoDB 스키마에 의존적(`name`, `alt_name`, `type`, `is_pk`). MongoDB 스키마 변경 시 파싱이 깨질 수 있음.
- `search_use_cases` 결과에 `_search_query` 태그를 런타임에 삽입하는데(`uc["_search_query"] = step.input`), 이것이 원본 Qdrant 결과에 mutation을 가하는 구조. 순수성 위반.

---

### 1.3 context_interpreter_node (배치 LLM 해석)

| 필요 데이터 | 소스 | 공급 상태 | 비고 |
|-------------|------|-----------|------|
| `reason.candidate_tables` | context_retriever | **정상** | 관찰 데이터(날짜분포, 샘플) 포함 |
| `reason.explored_use_cases` | context_retriever | **정상** | 유사 SQL JSON |
| `reason.code_map` | context_retriever | **정상** | 코드값 매핑 |
| `reason.execution_plan` | context_retriever | **정상** | DONE 스텝 메타 |
| `reason.knowledge_items` | reasoning_preparer | **정상** | UNRESOLVED 항목 |
| `preprocessed_input` | interpret 계층 | **정상** | 원본 질의 |
| `normalized_query` | interpret 계층 | **조건부** | 시간 조건 추출용 |

**프롬프트 직렬화 형태 분석**:

| 변수 | 직렬화 함수 | 형태 | 문제점 |
|------|-----------|------|--------|
| `{original_query}` | 직접 | 문자열 | 없음 |
| `{time_slot}` | `_extract_time_slot()` | 문자열 | NormalizedQuery 없으면 "(명시되지 않음)" |
| `{unresolved_items}` | `_serialize_unresolved_items()` | 줄바꿈 목록 | ~~CANDIDATE 누락~~ **해소됨** — UNRESOLVED/CANDIDATE/CONFLICTED 3가지 상태 모두 포함 |
| `{tool_results}` | `_serialize_tool_results()` | 구조화 텍스트 | **explored_use_cases를 JSON.dumps로 통째 주입** — 건수가 많으면 토큰 폭발 |
| `{table_observations}` | `_serialize_table_observations()` | 테이블별 블록 | 잘 구조화됨 |

**핵심 문제 — explored_use_cases의 무제한 JSON 직렬화**:
- `_serialize_tool_results()` ([context_interpreter.py:268-272](src/agents/nodes/reason/context_interpreter.py#L268-L272))에서 `json.dumps(explored_use_cases, ensure_ascii=False)`를 호출.
- 유사 SQL 10건 × 평균 500토큰 = **5,000토큰**이 프롬프트에 무삭제로 주입됨.
- 폐쇄망 모델(70B)의 컨텍스트 윈도우 대비 과도한 비중.

**누락 데이터**:
- `reason.query_decomposition`이 context_interpreter 프롬프트에 전달되지 않음. LLM이 "사용자가 원하는 것이 무엇인지"의 구조화된 분해를 참조하지 못하고 `original_query` 원문만으로 판단.
- `reason.dead_ends`가 전달되지 않음. 이전 실패 경로를 context_interpreter가 인지하지 못해, 이미 실패한 테이블을 다시 SELECTED로 판정할 수 있음 (recovery 루프 시).

---

### 1.4 readiness_gate_node (준비도 평가)

| 필요 데이터 | 소스 | 공급 상태 | 비고 |
|-------------|------|-----------|------|
| `reason.knowledge_items` | context_interpreter | **정상** | is_critical 기반 term_resolution |
| `reason.candidate_tables` | context_interpreter | **정상** | description 기반 table_coverage |
| ~~`reason.candidate_tables[*].join_keys`~~ | — | **해당 없음** | join_keys 필드는 현재 구현에 존재하지 않음 (아래 참조) |
| `reason.execution_plan` | context_retriever | **정상** | PENDING 여부 확인 |
| `reason.loop_guard` | 각 노드 누적 | **정상** | 종료 조건 |
| `reason.exploration_phase` | 자체/recovery_agent | **정상** | initial/recovery 분기 |

**~~join_keys 공급 문제~~ [해소됨 — 해당 필드 미구현]**:
> 본 분석은 `CandidateTable.join_keys` 필드와 `calculate_readiness()`의 join_path 점수(20%)를 전제로 작성되었으나,
> 현재 구현에는 `join_keys` 필드 자체가 `TableMeta`/`CandidateTable`에 존재하지 않으며,
> `confidence_scorer.py`의 점수 계산에도 join_path 항목이 없다. 향후 다중 테이블 조인 지원 시 참고 자료로 보존.

**table_coverage 계산 왜곡 (이전 리뷰에서 지적)**:
- REJECTED 테이블이 분모에 포함. 5개 중 3개 REJECTED하면 coverage = 2/5 = 40%.
- SELECTED 2개 모두 description이 있어도 40%로 계산됨.

---

### 1.5 sql_generator_node (SQL 생성)

| 필요 데이터 | 소스 | 공급 상태 | 비고 |
|-------------|------|-----------|------|
| `reason.query_decomposition` | reasoning_preparer | **정상** | measures/filters/group_by/order_limit |
| `reason.knowledge_items` (CONFIRMED) | context_interpreter | **정상** | `format_confirmed_text()` |
| `reason.candidate_tables` (non-REJECTED) | context_interpreter | **정상** | 컬럼 상세 포함 |
| `reason.explored_use_cases` (_relevant) | context_interpreter | **정상** | 상위 10건 |
| `reason.dead_ends` | recovery_agent | **정상** | `format_dead_ends_text()` |
| `reason.failure_reason` | sql_validator | **정상** | 재시도 시 fix 피드백 |
| `preprocessed_input` | interpret 계층 | **정상** | user 메시지 |
| dialect | connector_manager | **정상** | DB 기반 자동 결정 |

**프롬프트 직렬화 형태 분석**:

| 변수 | 직렬화 함수 | 형태 | 평가 |
|------|-----------|------|------|
| `{tables}` | `_format_table_for_sql_prompt()` | 구조화 텍스트 | **우수** — 컬럼별 한글명·타입·PK·설명 + 날짜관찰 + LLM추론(태그) |
| `{confirmed_terms}` | `format_confirmed_text()` | 줄바꿈 목록 | **양호** — 단 evidence가 미포함 |
| `{reference_sqls}` | 인라인 조립 | 설명+관련성+SQL | **양호** — 상위 10건 제한 |
| `{join_path}` | 인라인 조립 | join_keys 나열 | **미흡** — 위 join_keys 공급 문제 |
| `{dead_ends}` | `format_dead_ends_text()` | 줄바꿈 목록 | **양호** |
| `{fix_section}` | SQL_GENERATOR_FIX_SECTION | 조건부 삽입 | **양호** |

**누락 데이터**:
- ~~`reason.code_map`이 sql_generator 프롬프트에 전달되지 않음.~~ **해소됨** — `reason.explored_codes`가 `_format_codes_for_tables()`를 통해 선택된 테이블의 코드값만 필터링하여 `{codes}` 변수로 프롬프트에 주입됨 (sql_generator.py:372-374, 439).

- `reason.inference_notes`가 sql_generator에 전달되지 않음. force_generate 시 "추론 포함" 맥락을 LLM이 인지하지 못함.

---

### 1.6 sql_validator_node (SQL 검증)

| 필요 데이터 | 소스 | 공급 상태 | 비고 |
|-------------|------|-----------|------|
| `reason.generated_sql` | sql_generator | **정상** | 검증 대상 |
| `reason.candidate_tables` | context_interpreter | **정상** | L1 테이블/컬럼 범위 |
| `reason.query_decomposition` | reasoning_preparer | **정상** | L2a 구조 검증 |
| `reason.knowledge_items` (CONFIRMED) | context_interpreter | **정상** | L2b 미확인값 감지 |
| `reason.dead_ends` | recovery_agent | **정상** | L2b 반복 감지 |
| `preprocessed_input` | interpret 계층 | **정상** | L2b 의도 대조 |
| dialect | connector_manager | **정상** | sqlglot 파싱 |

**프롬프트 직렬화 (Layer 2b)**:
- `serialize_decomp_slots(decomp)` → `{measures}`, `{filters}`, `{group_by}`, `{order_limit}`
- `format_confirmed_text()` → `{confirmed_terms}`
- `format_dead_ends_text()` → `{dead_ends}`

**데이터 형태 문제**:
- `serialize_decomp_slots()`가 measures를 `[{term: "건수", agg: "COUNT"}]` 형태의 JSON 문자열로 변환하는데, 프롬프트에서는 자연어 설명 형태가 더 적합할 수 있음.
- L1에서 `get_real_columns(ast)`가 반환하는 컬럼명이 **대소문자 정규화 없이** 비교됨 — `allowed_columns`는 `.upper()`로 정규화하고 `used_columns`도 `.upper()`로 비교하므로 실제로는 정상 동작.

**구조적 문제**:
- Layer 2b LLM 호출 실패 시 `failure_type: structural`로 처리 — LLM 인프라 장애를 SQL 품질 문제로 오분류 (이전 리뷰 재확인).

---

### 1.7 recovery_agent_node (재계획)

| 필요 데이터 | 소스 | 공급 상태 | 비고 |
|-------------|------|-----------|------|
| `reason.failure_type/reason` | readiness_gate / sql_validator | **정상** | 진입 맥락 |
| `reason.knowledge_items` | context_interpreter | **정상** | 확인/미해소 분류 |
| `reason.candidate_tables` | context_interpreter | **정상** | REJECTED 제외 |
| `reason.dead_ends` | 자체 누적 | **정상** | 반복 방지 |
| `reason.explored_use_cases` | context_retriever | **정상** | 탐색 이력 + 관련성 태그 |
| `reason.discovered_facts` | context_interpreter | **정상** | 누적 인사이트 |
| `reason.candidate_tables[*].sample_rows` | context_retriever | **정상** | 샘플 현황 |
| `reason.loop_guard` | 각 노드 누적 | **정상** | 종료 조건 |
| `reason.hypotheses` | reasoning_preparer / 자체 | **정상** | 가설 관리 |

**프롬프트 직렬화 형태 — 가장 충실한 맥락 전달**:
- 8개 placeholder 모두 별도 빌더 함수로 구조화되어 있어 **데이터 형태가 가장 잘 조립됨**.
- `_build_exploration_history()`는 검색 쿼리별 그루핑 + 관련성 태그 — **우수한 설계**.
- `_build_sample_summary()`는 0행 테이블도 명시 — **반복 방지에 효과적**.

**미흡한 점**:
- `reason.query_decomposition`이 recovery_agent 프롬프트에 전달되지 않음. LLM이 "사용자가 무엇을 원하는지"의 구조화된 분해 없이 실패 맥락만으로 재계획을 수립해야 함.
- `reason.code_map` 현황이 전달되지 않음. 어떤 코드값이 이미 확인되었는지 모르므로, 이미 수집한 코드를 다시 search_code_meta로 검색하는 중복이 발생할 수 있음.

---

### 1.8 result_finalizer_node (최종 출력)

| 필요 데이터 | 소스 | 공급 상태 | 비고 |
|-------------|------|-----------|------|
| `reason.validated_sql` | sql_validator | **정상** | 성공 분기 |
| `reason.knowledge_items` (CONFIRMED, CONFLICTED) | context_interpreter | **정상** | ContextInfo + T5 |
| `reason.candidate_tables` | context_interpreter | **정상** | TableMeta 생성 |
| `reason.loop_guard` | 각 노드 | **정상** | 요약 통계 |
| `reason.dead_ends` | recovery_agent | **정상** | 실패 요약 |
| `reason.exploration_summary` | recovery_agent (give_up) | **정상** | LLM 총평 |

**문제**:
- `_build_context_info()`에서 **CONFIRMED 상태의 `table:*` KI만** 사용하여 ContextInfo를 구성.
- 하지만 `_promote_sampled_confidence()`에서 샘플만 있으면 무조건 CONFIRMED로 승격하므로, **REJECTED 테이블도 CONFIRMED KI가 남아있으면 ContextInfo에 포함**될 수 있음.
  - 실제로는 [context_interpreter.py:156-159](src/agents/nodes/reason/context_interpreter.py#L156-L159)에서 REJECTED 테이블의 KI를 삭제하므로 대부분 방지되지만, `_promote_sampled_confidence()`가 Phase 6에서 REJECTED 제거 이후에 실행되므로 **타이밍 문제 없음** — 이 부분은 정상.

---

## 2. 데이터 형태(직렬화) 적정성 평가

### 2.1 잘 조립된 사례

| 노드 | 직렬화 | 평가 |
|------|--------|------|
| context_interpreter | `_build_table_block()` | **우수** — 메타 원본/관찰/LLM 추론 출처를 태그로 구분 |
| sql_generator | `_format_table_for_sql_prompt()` | **우수** — 컬럼 상세(한글명, 타입, PK, 설명)까지 전달 |
| recovery_agent | `_build_exploration_history()` | **우수** — 검색쿼리별 그루핑 + 관련성 ✓/✗ 표시 |
| sql_validator | `serialize_decomp_slots()` | **양호** — 구조화된 슬롯 분리 |

### 2.2 개선이 필요한 사례

| 노드 | 직렬화 | 문제 | 영향도 |
|------|--------|------|--------|
| context_interpreter | `_serialize_tool_results()` | explored_use_cases를 **JSON.dumps 통째 주입** — 건수 제한 없음 | **높음** — 토큰 폭발, 폐쇄망 모델 컨텍스트 초과 |
| context_interpreter | `_serialize_unresolved_items()` | CANDIDATE 상태 제외 — 아직 확인 중인 항목도 미해소로 취급해야 함 | **중간** — LLM이 불완전한 미해소 목록을 받음 |
| sql_generator | `format_confirmed_text()` | evidence(근거)가 미포함 — "왜 확인되었는지" 모름 | **낮음** — SQL 생성에 직접 영향 적음 |
| readiness_gate | 직렬화 없음 (rule-based) | join_keys 빈 리스트 문제 (위 1.4 참조) | **높음** — 점수 왜곡 |

---

## 3. State 구조 설계 평가

### 3.1 잘 설계된 부분

#### (1) ReasoningState 중첩 구조
- `PipelineState.reason: ReasoningState`로 **reason 계층 전체 상태를 단일 필드에 격리**.
- 각 노드가 `reason = state.reason.model_copy(deep=True)` → 가공 → `return {"reason": reason}` 패턴을 일관 사용.
- **장점**: 계층 간 오염 방지, 노드별 독립적 state mutation, Pydantic deep copy로 불변성 보장.

#### (2) KnowledgeItem의 다단계 신뢰도 모델
- `UNRESOLVED → CANDIDATE → PROBABLE → CONFIRMED / CONFLICTED` 5단계.
- `is_critical` 플래그로 필수/선택 분리.
- `knowledge_id`로 프롬프트 내 참조 가능 (예: "[K1] 여신실행일자").
- **장점**: 탐색 진행도를 세밀하게 추적, readiness_gate가 정량적 판정 가능.

#### (3) CandidateTable의 출처 구분 설계
- `(메타 원본)`: table_name, columns, description — MongoDB에서 직접 파싱
- `(관찰)`: observed_date_columns, sample_rows — DB 쿼리 결과
- `(LLM 추론)`: inferred_entity_scope, inferred_functional_usage, inferred_data_refresh_hint
- **장점**: context_interpreter 프롬프트에서 출처별 신뢰도 차등 적용 가능.

#### (4) LoopGuard 다층 카운터
- `total_tool_calls`, `replan_count`, `generate_attempts`, `local_fix_count` 4개 독립 카운터.
- `should_terminate()` 5가지 종료 조건 + `should_escalate_to_structural()` 에스컬레이션.
- **장점**: 무한 루프 방지와 점진적 에스컬레이션이 체계적.

#### (5) DeadEnd + Hypothesis 쌍 관리
- 실패 경로를 `DeadEnd`로 기록하고, 가설을 `Hypothesis`로 관리하여 반복 방지.
- recovery_agent가 dead_ends를 참조하여 같은 실패를 반복하지 않음.
- **장점**: 탐색 공간을 효율적으로 pruning.

#### (6) Unified Clarification 패턴
- `pending_signals` / `resolved_signals`를 통한 5개 trigger(T1-T5)의 단일 경로 처리.
- `operator.add` reducer로 resolved_signals 누적.
- **장점**: 명확화 로직이 분산되지 않고 clarification_handler에 집중.

### 3.2 미흡한 부분

#### (1) join_keys의 공급 경로 단절 [심각도: 높음]

**현상**: `CandidateTable.join_keys`가 대부분 빈 리스트로 남음.

**원인 추적**:
- `CandidateTable.from_meta()` ([state.py:184-222](src/agents/state/state.py#L184-L222))에서 MongoDB 응답을 파싱하지만, **join_keys 필드를 파싱하지 않음** (columns, description 등만 파싱).
- `context_interpreter`가 `new_tables[*].join_keys`를 LLM 응답에서 파싱하지만, `_merge_llm_inferred_fields()` ([context_interpreter.py:479-498](src/agents/nodes/reason/context_interpreter.py#L479-L498))에서 **join_keys를 병합하지 않음** (entity_scope, functional_usage, data_refresh_hint 3개만 병합).

**영향**:
- `calculate_readiness()`의 join_path 점수(20%)가 다중 테이블 시 항상 0.3으로 고정.
- sql_generator의 `{join_path}` 프롬프트 변수가 항상 "(미확인)".
- **다중 테이블 조인 쿼리의 성공률 저하**.

**수정 방안**:
1. `_merge_llm_inferred_fields()`에 join_keys 병합 추가.
2. context_retriever의 `_extract_tables()`에서 PK 컬럼 기반 join_keys 추론 추가.
3. 유사 SQL의 `StructuralHints.join_patterns`에서 join_keys를 역추출.

#### (2) code_map이 sql_generator에 전달되지 않음 [심각도: 높음]

**현상**: `reason.code_map`에 `{LOAN_STS_CD: {01: 정상, 02: 연체, ...}}` 형태의 코드 매핑이 축적되지만, sql_generator의 `_build_agentic_prompt()`에서 이 데이터를 프롬프트에 주입하지 않음.

**영향**: LLM이 WHERE 절에 코드값을 사용할 때, confirmed_terms의 요약 정보만으로는 **전체 코드값 목록을 알 수 없어 잘못된 코드값을 생성**할 가능성.

**수정 방안**: `_build_agentic_prompt()`에 `{code_map}` 변수를 추가하고, 관련 테이블의 코드 컬럼 매핑을 프롬프트에 주입.

#### (3) explored_use_cases의 무제한 직렬화 [심각도: 높음]

**현상**: context_interpreter 프롬프트에 `json.dumps(explored_use_cases)` 통째 주입.

**영향**: 유사 SQL이 10건 이상이면 프롬프트 토큰이 급증하여:
- 폐쇄망 모델(Solar Pro 2 70B, 컨텍스트 4K~8K)에서 컨텍스트 초과.
- 대형 모델에서도 주의력 분산으로 판정 품질 저하.

**수정 방안**: 유사 SQL을 상위 N건(5~8건)으로 제한하거나, SQL 본문 대신 설명+테이블+조인 요약만 전달.

#### (4) table_coverage 분모에 REJECTED 포함 [심각도: 중간]

**현상**: `confidence_scorer.py`의 table_coverage 계산:
```python
candidates = reason.candidate_tables  # REJECTED 포함
with_desc = [c for c in candidates if c.description]
table_score = len(with_desc) / len(candidates)
```

**영향**: REJECTED를 많이 할수록 (= 좋은 판단을 할수록) 점수가 낮아지는 역설.

**수정 방안**: SELECTED + PENDING 테이블만 대상으로 계산.

#### (5) query_decomposition이 context_interpreter/recovery_agent에 미전달 [심각도: 중간]

**현상**: 사용자 질의의 구조화된 분해(measures, filters, group_by)가 context_interpreter와 recovery_agent 프롬프트에 전달되지 않음.

**영향**:
- context_interpreter: "어떤 측정값이 필요한지" 구조적으로 알지 못해, 테이블 판정 시 질의 의도와의 정렬도가 낮아질 수 있음.
- recovery_agent: "무엇이 부족한지" 판단 시 decomposition 없이 원본 질의와 실패 사유만으로 재계획 수립.

#### (6) _promote_sampled_confidence의 무조건 승격 [심각도: 중간]

**현상**: [context_interpreter.py:524-543](src/agents/nodes/reason/context_interpreter.py#L524-L543)
```python
if ki.confidence < 0.8:
    ki.confidence = 0.85
    ki.status = ConfidenceStatus.CONFIRMED
```
- 샘플 데이터가 있는 테이블의 KI를 selection_status와 무관하게 CONFIRMED로 승격.

**영향**: PENDING 상태(아직 판정 전)인 테이블도 샘플만 있으면 CONFIRMED되어, readiness_gate가 과도하게 높은 준비도를 산출할 수 있음.

**수정 방안**: `selection_status == SELECTED`인 테이블만 대상으로 승격.

#### (7) StructuralHints가 sql_generator에서 활용되지 않음 [심각도: 낮음]

**현상**: `StructuralHints` 모델에 `to_prompt_text()` 메서드가 정의되어 있고, 유사SQL에서 sqlglot으로 12가지 구조 정보를 추출할 수 있지만, sql_generator 프롬프트에 주입하는 경로가 없음.

**영향**: 유사 SQL의 조인 패턴, 날짜 필터 형식, GROUP BY 구조 등이 SQL 생성 시 직접 참고되지 못함.

---

## 4. 노드 간 데이터 흐름 정합성 검증

### 4.1 정상 흐름 (Happy Path)

```
reasoning_preparer → context_retriever → context_interpreter → readiness_gate → sql_generator → sql_validator → result_finalizer
```

| 구간 | 전달 데이터 | 정합성 |
|------|-----------|--------|
| preparer→fetcher | execution_plan(PENDING), knowledge_items(UNRESOLVED) | **정상** |
| fetcher→interpreter | candidate_tables(관찰 포함), use_cases, code_map | **정상** |
| interpreter→gate | knowledge_items(승격), candidate_tables(판정) | **정상** (join_keys 제외) |
| gate→generator | phase=GENERATING, readiness 판정 | **정상** |
| generator→validator | generated_sql, candidate_tables, decomposition | **정상** |
| validator→finalizer | validated_sql, failure_type=None | **정상** |

### 4.2 Recovery 흐름

```
readiness_gate(REPLAN) → recovery_agent → context_retriever → context_interpreter → readiness_gate
```

| 구간 | 전달 데이터 | 정합성 |
|------|-----------|--------|
| gate→recovery | failure_type/reason, exploration_phase="recovery" | **정상** |
| recovery→fetcher | 새 execution_plan(PENDING) | **정상** |
| fetcher→interpreter | 기존 + 새 candidate_tables | **주의** — 중복 테이블 발생 가능 |
| interpreter→gate | 갱신된 knowledge_items | **정상** |

**중복 테이블 문제**: recovery_agent가 같은 테이블에 대해 search_table_meta를 재요청하면, context_retriever가 새 CandidateTable을 생성하여 candidate_tables에 **동일 테이블이 2건** 존재할 수 있음. `_should_skip_step()`이 searched_queries로 중복을 방지하지만, 테이블명이 아닌 검색어 기준이므로 다른 키워드로 같은 테이블이 반환되면 중복 발생.

### 4.3 SQL Fix 흐름

```
sql_validator(SEMANTIC_LOCAL) → sql_generator(재시도) → sql_validator
```

| 구간 | 전달 데이터 | 정합성 |
|------|-----------|--------|
| validator→generator | failure_reason(fix 피드백), failure_type | **정상** |
| generator 내부 | fix_section에 failure_reason 주입 | **정상** |

---

## 5. 추가 발견 문제 (비판적 검토 결과)

> 아래 항목은 서브에이전트 코드 리뷰를 통해 추가 발견된 문제임.
> 검증 리포트: `docs/reviews/code/20260402-node-data-flow-review-verification-report.md`

### 5.1 ~~recovery_agent 프롬프트에 사용자 원본 질의(preprocessed_input) 미전달~~ [해소됨]

~~`_build_prompt()`의 placeholder 중 사용자 질의에 해당하는 것이 없음.~~
**해소됨** — `state.preprocessed_input`을 `original_query` 변수로 읽어 `{original_query}` placeholder에 주입함 (recovery_agent.py:431, 903).

### 5.2 ~~Phase 2 DB 쿼리(날짜분포, 샘플)가 loop_guard에 미반영~~ [해소됨]

~~`_observe_all_date_distributions()`과 `_sample_unsampled_tables()`가 `total_tool_calls`에 반영되지 않음.~~
**해소됨** — `_run_step` 함수가 (step_results, insights, call_count) 튜플을 반환하며, context_retriever.py:512에서 `total_tool_calls += sum(calls for _, _, calls in results)`로 모든 도구 호출을 집계한 뒤 loop_guard에 반영함.

### 5.3 candidate_tables 중복 테이블 방지 미비 [심각도: 중간]

`_extract_tables()` → `candidate_tables.extend()` 및 `_fetch_use_case_related_metas()` → `candidate_tables.append()` 양 경로에서 **기존 table_name 존재 여부를 확인하지 않음**. 다른 검색어로 같은 테이블이 반환되면 중복 삽입되어 점수 왜곡 + 토큰 낭비 발생.

### 5.4 calculate_readiness()의 join_path에서도 REJECTED 미필터링 [심각도: 중간]

`len(candidates) > 1` ([confidence_scorer.py:141](src/services/confidence_scorer.py#L141))에서 REJECTED 포함. REJECTED 5개 + SELECTED 1개인 경우 `needs_join=True`가 되어 불필요하게 join_score가 낮아짐. table_coverage와 동일한 문제가 join_path에서도 발생.

### 5.5 readiness_gate의 confidence 수치 기준과 status 기준 불일치 [심각도: 중간]

`_set_failure_context()` ([readiness_gate.py:151-153](src/agents/nodes/reason/readiness_gate.py#L151-L153))에서 `confidence >= 0.8`로 확정 판정, `calculate_readiness()`에서는 `status in (CONFIRMED, PROBABLE)`로 확정 판정. status=PROBABLE, confidence=0.6인 항목에서 불일치 발생.

### 5.6 StructuralHints가 ReasoningState에 저장되지 않는 구조적 한계 [심각도: 낮음]

`extract_hints_from_use_cases()`로 유사SQL에서 12가지 구조 정보를 추출하지만, `_fetch_use_case_related_metas()`에서 테이블/코드 수집에만 사용되고 StructuralHints 객체 자체는 버려짐. `to_prompt_text()` 메서드까지 구현되어 있으나 활용 경로 없음. ReasoningState에 `structural_hints` 필드를 추가하여 sql_generator까지 전달하는 것이 자연스러운 개선 경로.

---

## 6. 종합 개선 우선순위 (검증 후 최종)

| 순위 | 항목 | 영향 | 난이도 |
|------|------|------|--------|
| **1** | join_keys 공급 경로 복구 (`_merge_llm_inferred_fields` + `from_meta`) | 다중 테이블 조인 성공률 직결 | 중 |
| **2** | code_map을 sql_generator 프롬프트에 주입 | WHERE 절 코드값 정확도 직결 | 하 |
| **3** | calculate_readiness()에서 REJECTED 제외 (table_coverage + join_path 양쪽) | readiness 점수 정확도 | 하 |
| **3.5** | recovery_agent에 preprocessed_input(사용자 질의) 전달 | 재계획 품질 직결 | 하 |
| **4** | explored_use_cases 직렬화 건수 제한 | 폐쇄망 모델 안정성 (배포 전 필수) | 하 |
| **5** | query_decomposition을 interpreter/recovery에 전달 | 판정 품질 향상 | 중 |
| **5.5** | candidate_tables 중복 테이블 방지 | 토큰 낭비 + 점수 왜곡 방지 | 하 |
| **6** | Phase 2 DB 쿼리 loop_guard 반영 | DB 부하 제어 | 하 |
| **7** | `_promote_sampled_confidence` 조건 강화 (SELECTED만 대상) | 과신 방지 | 하 |
| **8** | Layer 2b LLM 장애 시 skip fallback | 인프라 장애 대응 | 하 |
| **9** | StructuralHints를 ReasoningState에 저장 → sql_generator 활용 | SQL 품질 향상 | 중 |
