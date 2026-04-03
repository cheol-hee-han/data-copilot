# 노드 데이터 흐름 분석 — 구현 기준 검증 리포트

> 최초 작성일: 2026-04-02
> 최종 갱신일: 2026-04-03
> 검증 대상: `docs/architecture/node-data-flow-review.md` (원본 분석 문서)
> 검증 방법: 소스코드 라인 단위 대조 + LLM 생성 State 필드 의도 분석 + 다운스트림 활용도 심층 추적
> 갱신 사유: 원본 문서 작성 이후 구현이 대폭 수정됨 — `join_keys` 필드 제거, `calculate_readiness()` 가중치 체계 변경, `TABLE_COMPARISON_SYSTEM` 미사용 정리 등

---

## 액션 분류 기준

| 분류 | 의미 | 기준 |
|------|------|------|
| **`적정`** | 현재 설계가 의도적이며 문제 없음 | 의도대로 동작하고 다운스트림에서 정상 활용됨 |
| **`확인 필요`** | 개선 여지 있으나 기능에 치명적이지 않음 — 설계 토론 대상 | 동작은 하지만 최적이 아니거나, 폐쇄망 배포 시 이슈 가능 |
| **`필수 개선`** | 의도된 기능이 동작하지 않거나 품질에 직접 영향 | 생성된 데이터가 버려지거나, 핵심 컨텍스트가 LLM에 미전달 |

---

## 0. 현재 구현 구조 요약

### 0.1 그래프 노드 흐름 (pipeline.py)

```text
[Interpret Layer]
  context_classifier → normalize_query → clarification_handler

[Reason Layer — Agentic Loop]
  reasoning_preparer → knowledge_fetcher → knowledge_interpreter
    → readiness_gate
        ├─ EXPLORING   → knowledge_fetcher (재탐색)
        ├─ GENERATING  → sql_generator → sql_validator
        │                  ├─ SUCCESS      → result_finalizer
        │                  ├─ fix_syntax   → sql_generator (재시도)
        │                  ├─ fix_local    → sql_generator (로컬 수정)
        │                  └─ replan       → recovery_agent
        ├─ REPLANNING  → recovery_agent → knowledge_fetcher (새 계획)
        ├─ VERIFYING   → result_finalizer (사용자 확인)
        └─ DONE        → result_finalizer (종료)

[Present Layer]
  execute_sql → analyze_data → format_response
  simple_responder (비데이터 의도)
  error_end (실패 포맷팅)
```

### 0.2 핵심 State 필드와 노드별 Read/Write 매트릭스

| State 필드 | Writer(s) | Reader(s) | LLM 프롬프트 전달 |
| --- | --- | --- | --- |
| `preprocessed_input` | context_classifier | normalize_query, sql_generator | sql_generator `{original_query}` |
| `normalized_query` | normalize_query | reasoning_preparer | ✗ (규칙 기반 분해 입력) |
| `query_decomposition` | reasoning_preparer | sql_generator, sql_validator | `{measures}`, `{filters}`, `{group_by}` 등 |
| `knowledge_items` | reasoning_preparer, knowledge_interpreter | readiness_gate, sql_generator, recovery_agent, result_finalizer | `{confirmed_terms}`, `{unresolved_items}` |
| `candidate_tables` | knowledge_fetcher, knowledge_interpreter | sql_generator, sql_validator, recovery_agent, result_finalizer | `{tables}` (REJECTED 제외) |
| `explored_use_cases` | knowledge_fetcher | knowledge_interpreter, sql_generator, recovery_agent, confidence_scorer | `{reference_sqls}` (top 10), `{exploration_history}` |
| `code_map` | knowledge_fetcher | knowledge_interpreter, formatter | knowledge_interpreter `{tool_results}` |
| `dead_ends` | recovery_agent | sql_generator, recovery_agent | `{dead_ends}`, `{dead_ends_summary}` |
| `discovered_facts` | knowledge_interpreter | recovery_agent | `{discovered_facts}` |
| `execution_plan` | reasoning_preparer, recovery_agent | knowledge_fetcher | ✗ (내부 제어 흐름) |
| `hypotheses` | reasoning_preparer, recovery_agent | should_terminate() | ✗ (내부 제어 흐름) |
| `phase` | readiness_gate, recovery_agent, result_finalizer | pipeline 라우팅 | ✗ |
| `loop_guard` | 모든 reason 노드 | readiness_gate, recovery_agent | ✗ |
| `selection_status` | knowledge_interpreter | sql_generator, sql_validator, recovery_agent | ✗ (필터링 기준) |
| `validation_checks` | sql_validator (L2b) | insight_builder | ✗ (간접) |
| `recovery_entry_source` | readiness_gate, pipeline.py | recovery_agent | `{entry_source_description}` |

### 0.3 스코어링 구조 (confidence_scorer.py:111-157)

현재 구현은 **2차원 가중 평균**:

- **term_resolution (70%)** — `is_critical=True`인 knowledge_items 중 CONFIRMED/PROBABLE 비율
- **use_case_match (30%)** — `_relevant=True`인 explored_use_cases 건수 (3건이면 만점)

> 원본 문서의 table_coverage, join_path 가중치 체계는 **현재 구현에서 완전 제거됨**.

---

## 1. 원본 문서 지적사항 검증 결과

### 1.1 `join_keys` 관련 — 필드 자체 제거됨 `적정`

**원본 지적**: `CandidateTable.from_meta()`에서 join_keys를 파싱하지 않음 + `_merge_llm_inferred_fields()`에서 join_keys 미병합.

**현재 상태: 해당 없음 (RESOLVED by Design)**

`CandidateTable` 모델(`state.py:151-182`)에 `join_keys` 필드가 **완전히 제거**되었다.
현재 CandidateTable 필드 구성:

```text
table_name, alt_name, description, schema_name, db_source, subject_area
columns: list[ColumnInfo]
key_date_columns: list[KeyDateColumn]          # 규칙 기반 (PK)
observed_date_columns: list[ObservedDateColumn] # DB 쿼리 관찰
sample_rows: list[dict]
inferred_entity_scope, inferred_functional_usage, inferred_data_refresh_hint  # LLM 추론
inferred_key_date_column: str                   # LLM 폴백 (미구현)
selection_status: TableSelectionStatus
selection_reason: str
```

조인 힌트는 **LLM이 컬럼 메타(PK 마크, 테이블 설명, 엔티티 추론)에서 자율 추론**하는 방식으로 전환됨.

**잔존 리스크**: 명시적 조인 힌트 없이 LLM이 복잡한 다테이블 조인을 정확히 추론하기 어려울 수 있음. 특히 폐쇄망 모델(Solar Pro 2 70B)에서 더 취약. → StructuralHints 활용(§2.6)으로 보완 가능.

---

### 1.2 `code_map`을 sql_generator에 주입하지 않는 문제 `필수 개선`

**원본 지적**: `reason.code_map`이 `sql_generator` 프롬프트에 전달되지 않음.

**현재 상태: 부분 해소 (Partially Resolved)**

- **knowledge_interpreter** (`_serialize_tool_results()`, line 242): code_map을 JSON 직렬화하여 배치 해석 LLM 프롬프트에 전달 (`{tool_results}` 변수)
- **배치 해석 결과**: LLM이 코드 매핑을 해석 → knowledge_items에 CONFIRMED 상태로 반영
- **sql_generator** (`_build_agentic_prompt()`, line 307): `{confirmed_terms}` 변수를 통해 **간접 전달** (code_map 해석 결과가 KI → confirmed_text에 포함)
- **formatter** (line 59): code_map 직접 참조

**문제**: `_build_agentic_prompt()` replacements(`sql_generator.py:307-318`)에 code_map **직접 주입 없음**. WHERE 절에서 정확한 코드값을 지정하려면 코드 원본(코드번호 → 코드명 매핑)이 필요하나, 현재는 LLM이 해석한 텍스트 요약만 전달됨.

**필수 개선 사유**: 금융 도메인에서 코드값 정확도는 SQL 결과 정합성에 직결. 간접 경로(LLM 해석 요약)로는 "상품구분코드 = '01'"처럼 정확한 코드번호를 생성하기 어려움.

**개선 방안**: `{code_mappings}` 변수를 추가하여 관련 코드 테이블 원본을 직접 주입.

---

### 1.3 `calculate_readiness()`에서 REJECTED 테이블 분모 포함 `적정`

**원본 지적**: table_coverage 계산에서 REJECTED 테이블이 분모에 포함됨.

**현재 상태: 해당 없음 (RESOLVED by Restructure)**

`calculate_readiness()` (`confidence_scorer.py:111-157`)가 **완전히 재설계**되어, table_coverage / join_path 가중치 체계가 제거됨. 현재는 term_resolution(70%) + use_case_match(30%) 2차원 구조.

**단, 관련 잔존 문제**: `_set_failure_context()` (`readiness_gate.py:146-190`)에서 `ct_count = len(reason.candidate_tables)` (line 154)로 REJECTED 포함 카운트. failure_reason 메시지에 "후보 테이블: N개"가 부정확할 수 있음 → §3.1에서 별도 다룸.

---

### 1.4 `_serialize_tool_results()`에서 use_cases 건수 무제한 `확인 필요`

**원본 지적**: explored_use_cases를 무제한으로 JSON.dumps 직렬화.

**현재 상태: 부분 해소 (sql_generator만 제한됨)**

- **sql_generator** (`sql_generator.py:275`): `relevant[:10]`으로 상위 10건만 사용 — `적정`
- **knowledge_interpreter** (`_serialize_tool_results()`, line 242): 여전히 **전체를 건수 제한 없이** JSON 직렬화
- **recovery_agent** (`_build_exploration_history()`, line 412): search_query별 그룹화, description 100자 제한은 있으나 **전체 건수 제한 없음**

**확인 필요 사유**: Claude 사용 시에는 컨텍스트 여유로 실질적 문제 없음. 그러나 **폐쇄망 배포(Solar Pro 2 70B, 컨텍스트 16K~32K)** 시에는 10건 이상의 use_case JSON이 수천 토큰을 차지하여 프롬프트 예산 초과 가능. 배포 전 필수 대응.

---

### 1.5 `query_decomposition` recovery_agent 미전달 `필수 개선`

**원본 지적**: recovery_agent에 query_decomposition이 전달되지 않음.

**현재 상태: 여전히 유효 (Open)**

`recovery_agent._build_prompt()` (`recovery_agent.py:312-405`)의 replacements (`line 382-405`)에 query_decomposition이 **없음**:

```python
replacements = {
    "{entry_source_description}": entry_desc,
    "{confirmed_knowledge}": ...,
    "{unresolved_items}": ...,
    "{candidate_tables_summary}": ...,
    "{dead_ends_summary}": ...,
    "{exploration_history}": ...,
    "{discovered_facts}": ...,
    "{sample_data_summary}": ...,
}
```

8개 placeholder 중 사용자 원본 질의(`preprocessed_input`)와 구조화된 분해(`query_decomposition`) **모두 부재**. LLM이 "어떤 measures/filters를 충족해야 하는지" 구조적 정보 없이 재계획을 수립.

**필수 개선 사유**: 재계획의 목표(사용자가 무엇을 원하는지)를 모르는 상태에서 수립되므로 재계획 품질에 직접 영향. 구현 난이도 하.

---

### 1.6 `_promote_sampled_confidence()`의 PENDING 테이블 무조건 승격 `확인 필요`

**원본 지적**: `selection_status`를 검사하지 않아 PENDING 테이블도 승격 대상.

**현재 상태: 여전히 유효 (Open, 경감됨)**

`knowledge_interpreter.py:523-543`:

- Phase 5에서 REJECTED 테이블의 KI를 삭제한 후 Phase 6에서 실행되므로 **REJECTED 승격은 방지됨**
- 그러나 **PENDING 상태 테이블의 무조건 승격**은 여전히 발생: 샘플 데이터만 있으면 confidence 0.85 + CONFIRMED로 승격

**확인 필요 사유**: REJECTED 최악 시나리오는 Phase 5에서 방지됨. PENDING 승격은 "아직 LLM 판정 전이지만 샘플은 있는" 테이블에 대한 낙관적 처리로, 설계 의도일 수 있음. 단, 의도적인지 누락인지 확인 필요.

---

## 2. LLM 생성 State 필드 — 의도 vs 실제 활용 심층 분석

### 2.1 `inferred_entity_scope` / `inferred_functional_usage` / `inferred_data_refresh_hint` `적정`

| 항목 | 내용 |
| --- | --- |
| **생성** | knowledge_interpreter 배치 LLM → `new_tables[*]` |
| **병합** | `_merge_llm_inferred_fields()` (`knowledge_interpreter.py:478`) |
| **활용 1** | 테이블 비교 판정 — `_find_comparison_groups()` (line 678)에서 entity_scope 기반 그룹핑 |
| **활용 2** | sql_generator 프롬프트 — `_format_table_details()` (`sql_generator.py:114-135`)에서 `{tables}` 변수에 포함 |
| **평가** | **의도대로 활용됨** — 생성 → 비교 판정 + SQL 프롬프트 양쪽에서 정상 소비 |

---

### 2.2 `inferred_key_date_column` `필수 개선`

| 항목 | 내용 |
| --- | --- |
| **정의** | `state.py:176` — 빈 문자열 기본값 |
| **기록하는 곳** | **없음** — 어떤 노드에서도 값을 채우지 않음 |
| **읽는 곳** | `knowledge_fetcher.py:359` (날짜 분포 관찰 폴백), `sql_generator.py:118` (프롬프트) |
| **평가** | **완전한 죽은 필드**. 정의 + 읽기 경로는 존재하지만 기록 경로가 없어 항상 `""` |

**필수 개선 사유**: `key_date_columns`가 비어있을 때의 LLM 기반 폴백이 구현되지 않은 채 방치됨. 읽기 경로가 활성 상태이므로 죽은 코드가 런타임에 불필요한 분기를 유발.

**조치**: LLM 폴백 로직 구현 또는 필드 + 읽기 경로 일괄 제거.

---

### 2.3 `query_decomposition` (8-slot 분해) `확인 필요`

| 항목 | 내용 |
| --- | --- |
| **생성** | reasoning_preparer에서 normalized_query 기반 규칙 구성 |
| **활용 1** | sql_generator: `{measures}`, `{filters}`, `{group_by}`, `{order_limit}`, `{output_hint}` — `적정` |
| **활용 2** | sql_validator: L2a 체크리스트 (`line 278`), L2b 의미 검증 (`line 355`) — `적정` |
| **미활용** | **recovery_agent** — 재계획 시 어떤 slots를 충족해야 하는지 정보 없음 |
| **미활용** | **knowledge_interpreter** — 배치 해석 시 원래 요청의 구조 정보 없음 |

**확인 필요 사유**: SQL 생성/검증에는 잘 활용되나, 재계획·해석 단계에서 구조 정보 단절. recovery_agent 전달은 §1.5에서 `필수 개선`으로 분류. knowledge_interpreter 전달은 배치 해석 품질에 유의미한 영향을 줄지 검증 필요.

---

### 2.4 `explored_use_cases`의 `_relevant` / `_eval_reason` 태그 `확인 필요`

| 항목 | 내용 |
| --- | --- |
| **생성** | knowledge_interpreter Phase 4.5에서 배치 LLM `relevant_use_cases` 결과 기반 부착 |
| **활용 1** | sql_generator (`line 272`): `_relevant` 필터 → `{reference_sqls}` 상위 10건 — `적정` |
| **활용 2** | confidence_scorer (`line 147`): `_relevant=True` 카운트 → use_case_match 점수 — `적정` |
| **주의** | sql_generator에서 `uc.get("_relevant", True)` — **기본값 True** |

**확인 필요 사유**: 태그 미부착 use_case도 relevant로 간주됨. recovery 이후 새로 추가된 use_case는 `_relevant` 태그 없이 sql_generator에 도달 가능 → 미평가 use_case가 reference SQL에 포함. 정상 흐름에서는 Phase 4.5를 반드시 거치므로 문제 없으나, recovery 경로에서 edge case 확인 필요.

---

### 2.5 `discovered_facts` `확인 필요`

| 항목 | 내용 |
| --- | --- |
| **생성** | knowledge_interpreter Phase 4에서 tool execution insights 추출 (`line 127`) |
| **활용** | recovery_agent 프롬프트의 `{discovered_facts}` (`line 399`) — `적정` |
| **미활용** | sql_generator, readiness_gate |

**확인 필요 사유**: "이 데이터는 월초에만 갱신됨", "테이블A와 B는 기준일 기준 1:N" 같은 발견 사실이 SQL 생성에도 유용할 수 있으나 전달 안 됨. recovery 전용은 의도된 설계로 보이나, sql_generator 전달 시 품질 개선 가능성 있음.

---

### 2.6 `StructuralHints` — 생성 후 폐기됨 `필수 개선`

| 항목 | 내용 |
| --- | --- |
| **정의** | `state.py:277` — 완전한 Pydantic 모델 |
| **구현 메서드** | `to_prompt_text()` (join_patterns, code_columns, agg_expressions, date_filters 등을 프롬프트 텍스트로 변환) |
| **생성** | `knowledge_fetcher`의 `extract_hints_from_use_cases()`에서 유사 SQL 구조 분석 후 생성 |
| **사용** | knowledge_fetcher 내부에서 **관련 메타 fetch 방향 결정**에만 사용 후 **폐기** |
| **ReasoningState 저장** | **안 됨** — ReasoningState에 `structural_hints` 필드 없음 |
| **sql_generator 전달** | **안 됨** |

**필수 개선 사유**: `to_prompt_text()` 메서드까지 구현되어 있어 **명백히 SQL 프롬프트 전달 의도**가 있었으나 연결 미완성. join_patterns, agg_expressions 등 SQL 생성에 직접 유용한 구조화 정보가 추출 후 폐기되는 것은 설계 의도와 명백히 불일치. §1.1의 join_keys 제거 이후 **조인 힌트의 유일한 공급 경로**이므로 중요도 높음.

**조치**: ReasoningState에 `structural_hints: StructuralHints` 필드 추가 → sql_generator `{structural_hints}` 변수로 전달.

---

### 2.7 `validation_checks` `확인 필요`

| 항목 | 내용 |
| --- | --- |
| **생성** | sql_validator Layer2b LLM (`sql_validator.py:118`) |
| **활용** | insight_builder에서만 참조 (`insight_builder.py:482`) — `적정` (본래 용도) |
| **미활용** | recovery_agent, sql_generator(재시도 시) |

**확인 필요 사유**: SQL 재생성 시 이전 검증에서 **어떤 체크가 통과/실패했는지** 구조적 정보 미전달. `failure_reason` 텍스트만 전달되므로 구조적 피드백이 약함. 다만 `{fix_section}`에 failure_reason이 포함되므로 최소한의 피드백은 존재.

---

### 2.8 `hypotheses` `적정`

| 항목 | 내용 |
| --- | --- |
| **생성** | reasoning_preparer(초기), recovery_agent(재계획) |
| **활용** | `should_terminate()` 판정, recovery_agent ACTIVE→FAILED 전환 |
| **LLM 프롬프트** | 전달 안 됨 |
| **평가** | **내부 제어 흐름 전용으로 의도된 설계. 적절함** |

---

## 3. 추가 발견된 구현 문제

### 3.1 `_set_failure_context()`와 `calculate_readiness()`의 판정 기준 불일치 `필수 개선`

**동일 개념("확정된 지식")에 대해 수치 기준과 enum 기준이 혼재:**

| 위치 | 기준 | 코드 |
| --- | --- | --- |
| `_set_failure_context()` (readiness_gate.py:151-153) | **수치 기반**: `confidence >= 0.8` | `ki_confirmed = len([i for i in reason.knowledge_items if i.confidence >= 0.8])` |
| `_collect_stats()` (readiness_gate.py:196-198) | **수치 기반**: `confidence >= 0.8` | 동일 |
| `calculate_readiness()` (confidence_scorer.py:129-134) | **status 기반**: CONFIRMED/PROBABLE | `if i.status in (ConfidenceStatus.CONFIRMED, ConfidenceStatus.PROBABLE)` |
| `all_critical_confirmed()` (confidence_scorer.py:167-176) | **status 기반**: UNRESOLVED/CANDIDATE/CONFLICTED 제외 | status enum 검사 |

**불일치 시나리오**: status=PROBABLE, confidence=0.6인 항목은 `calculate_readiness()`에서는 "해소됨"이지만 `_set_failure_context()`에서는 "미확정"으로 카운트 → failure_reason 메시지가 실제 판정과 불일치.

**필수 개선 사유**: failure_reason은 recovery_agent 프롬프트(`{entry_source_description}`)에 직접 전달되어 재계획 방향을 결정. 판정 로직과 불일치하는 failure_reason은 LLM에 잘못된 맥락을 제공. 구현 난이도 하 — `_set_failure_context()`와 `_collect_stats()`를 status 기반으로 통일하면 됨.

---

### 3.2 recovery_agent에 `preprocessed_input` (사용자 원본 질의) 미전달 `필수 개선`

`recovery_agent._build_prompt()` (`recovery_agent.py:312-405`)의 8개 placeholder 중 사용자가 **무엇을 요청했는지** 해당하는 변수가 없다:

```text
{entry_source_description}    — 진입 경로
{confirmed_knowledge}          — 확인된 지식
{unresolved_items}             — 미해소 항목
{candidate_tables_summary}     — 후보 테이블
{dead_ends_summary}            — 실패 경로
{exploration_history}          — 탐색 이력
{discovered_facts}             — 발견 사실
{sample_data_summary}          — 샘플 현황
```

`preprocessed_input`과 `query_decomposition` **모두 부재**. LLM이 "왜 실패했는지"와 "무엇을 찾아야 하는지"만으로 재계획을 수립해야 하며, **사용자 의도와의 정렬이 약해진다**.

**필수 개선 사유**: §1.5와 동일. 재계획의 목표(사용자 원본 질의)를 모르는 상태에서 수립. 구현 난이도 하 — `{original_query}`와 `{query_decomposition}` placeholder 2개 추가.

---

### 3.3 candidate_tables 중복 테이블 방지 미비 `확인 필요`

`knowledge_fetcher.py`의 두 경로에서 동일 table_name 중복 확인 없이 append:

- Phase 1 도구 결과: `_extract_tables()` (line 513) → `candidate_tables.extend(new_tables)` (line 112-113)
- 유사 SQL 후속 수집: `_fetch_use_case_related_metas()` → `candidate_tables.append(ct)` (line 228-229)

`_should_skip_step()`은 `searched_queries` 기반 중복만 방지하므로, **다른 검색 경로에서 같은 테이블이 반환되면 중복 삽입**.

영향:

- sql_generator 프롬프트에 동일 테이블 2번 출력 → 토큰 낭비 + LLM 혼란
- `_find_comparison_groups()` 불필요한 비교 그룹 생성
- failure_reason의 테이블 카운트 왜곡

**확인 필요 사유**: 기능적 오류는 아니지만 토큰 효율과 LLM 판단 품질에 영향. 실제 중복 빈도에 따라 우선순위 조정 가능 — 골든셋 평가 후 판단 권장.

---

### 3.4 Phase 2 DB 쿼리가 loop_guard에 미반영 `확인 필요`

`knowledge_fetcher.py`의 Phase 2:

- `_observe_all_date_distributions()` (line 346): 후보 테이블마다 `get_date_distribution()` DB 쿼리
- `_sample_unsampled_tables()` (line 390): 후보 테이블마다 `get_sample_rows()` DB 쿼리

후보 테이블 N개이면 최대 2N회의 DB 호출이 추가 발생하나, `total_tool_calls` 카운터에 **미반영**. `loop_guard`가 이를 인지하지 못해 DB 부하 제어와 타임아웃 예측에 영향.

**확인 필요 사유**: 무한 루프는 아니지만 DB 부하 예측이 불가. 현재 후보 테이블 수가 통상 5개 이내이므로 10회 이하 추가 쿼리로 실질적 문제 발생 빈도는 낮음. 단, 폐쇄망 DB(Sybase IQ)에서 대용량 테이블 샘플링 시 응답 지연 가능.

---

### 3.5 `TABLE_COMPARISON_SYSTEM` 프롬프트 미사용 `적정` (정리 완료)

`table_comparison_system.txt`는 `knowledge_interpreter.py`에서 import만 되고 실제 사용되지 않았음.
비교 판정이 배치 해석(KNOWLEDGE_INTERPRETER_SYSTEM)에 통합된 것으로 확인.

**조치 완료 (2026-04-03)**:

- 프롬프트 파일: `미사용_table_comparison_system.txt`로 리네임
- `system_prompts.py`: `TABLE_COMPARISON_SYSTEM` 변수 제거
- `knowledge_interpreter.py`: 미사용 import 제거
- `thinking_modes.py`: `"table_comparison"` 엔트리 제거
- `DECISION_TABLE_COMPARISON` 트래킹 상수는 배치 해석 결과 기록용이므로 유지

---

### 3.6 `_build_exploration_history()`에서 SQL 본문 미포함 `확인 필요`

`recovery_agent.py:412-450`에서 유사 SQL의 설명만 100자로 잘라 전달하고 SQL 본문은 미전달. 재계획 시 "어떤 SQL 패턴이 참고 가능한지" 구조적 정보 부족. StructuralHints가 ReasoningState에 저장되지 않기 때문에 유사 SQL에서 추출한 조인 패턴/집계 방식이 recovery_agent에 전달되지 않는 구조적 한계.

**확인 필요 사유**: SQL 본문 직접 전달은 토큰 부담이 크므로 부적절. §2.6의 StructuralHints가 ReasoningState에 저장되면 자연스럽게 해소 가능 — 독립 개선보다 §2.6과 연계하여 해결하는 것이 효율적.

---

## 4. 노드별 프롬프트 변수 — 현재 구현 기준 완전 매핑

### 4.1 sql_generator (`_build_agentic_prompt`, line 243-320)

| 프롬프트 변수 | 소스 | 필터링 |
| --- | --- | --- |
| `{current_date}` | `today_kst().isoformat()` | — |
| `{original_query}` | `preprocessed_input` | — |
| `{measures}` | `query_decomposition` | `serialize_decomp_slots()` |
| `{filters}` | `query_decomposition` | `serialize_decomp_slots()` |
| `{group_by}` | `query_decomposition` | `serialize_decomp_slots()` |
| `{order_limit}` | `query_decomposition` | `serialize_decomp_slots()` |
| `{output_hint}` | `query_decomposition` | `serialize_decomp_slots()` |
| `{confirmed_terms}` | `knowledge_items` | CONFIRMED/PROBABLE only (`format_confirmed_text()`) |
| `{tables}` | `candidate_tables` | **REJECTED 제외** (`selection_status != REJECTED`, line 262) |
| `{reference_sqls}` | `explored_use_cases` | `_relevant=True` (기본 True), 상위 10건 |
| `{dead_ends}` | `dead_ends` | `format_dead_ends_text()` |
| `{fix_section}` | `failure_reason` | 존재 시에만 `SQL_GENERATOR_FIX_SECTION` 적용 |
| `{clarification_context}` | `resolved_signals` | `build_clarification_context()` |
| `{dialect}` | ConnectorManager | — |

**미포함 — `필수 개선` 대상**:

- `code_map` 원본 (§1.2 — WHERE 코드값 정확도)
- `StructuralHints` (§2.6 — 조인 패턴, 집계 힌트)

**미포함 — `확인 필요` 대상**:

- `discovered_facts` (§2.5 — 관찰 인사이트)

### 4.2 knowledge_interpreter (`_interpret_batch`, line 307-372)

| 프롬프트 변수 | 소스 | 필터링 |
| --- | --- | --- |
| `{original_query}` | `preprocessed_input` | — |
| `{time_slot}` | `normalized_query.time` | — |
| `{unresolved_items}` | `knowledge_items` | UNRESOLVED, CONFLICTED only (`_serialize_unresolved_items()`, line 221) |
| `{tool_results}` | `execution_plan` + `explored_use_cases` + `code_map` | DONE 스텝만, JSON 직렬화 (**건수 제한 없음** — §1.4) |
| `{table_observations}` | `candidate_tables` | 전체 (REJECTED 포함), 관찰 데이터 상세 |

### 4.3 recovery_agent (`_build_prompt`, line 312-405)

| 프롬프트 변수 | 소스 | 필터링 |
| --- | --- | --- |
| `{entry_source_description}` | `recovery_entry_source` + `failure_type/reason` | — |
| `{confirmed_knowledge}` | `knowledge_items` | CONFIRMED/PROBABLE only |
| `{unresolved_items}` | `knowledge_items` | 나머지 status |
| `{candidate_tables_summary}` | `candidate_tables` | **REJECTED 제외** (line 357) |
| `{dead_ends_summary}` | `dead_ends` | `lessons_learned` 100자 제한 |
| `{exploration_history}` | `explored_use_cases` | `_search_query` 기준 그룹화, desc 100자 |
| `{discovered_facts}` | `discovered_facts` | — |
| `{sample_data_summary}` | `candidate_tables.sample_rows` | 행 수만 |

**미포함 — `필수 개선` 대상**:

- `preprocessed_input` (§3.2 — 사용자 원본 질의)
- `query_decomposition` (§1.5 — 구조적 분해)

---

## 5. 개선 우선순위 (현재 구현 기준)

### 필수 개선 (5건)

| 순위 | 항목 | 영향도 | 난이도 | 상세 |
| --- | --- | --- | --- | --- |
| **1** | StructuralHints를 ReasoningState에 저장 → sql_generator 전달 | 높음 (SQL 조인/집계 품질, 유일한 조인 힌트 경로) | 중 | §2.6 |
| **2** | recovery_agent에 `preprocessed_input` + `query_decomposition` 전달 | 높음 (재계획 품질) | 하 | §1.5, §3.2 |
| **3** | `code_map` 원본을 sql_generator에 직접 주입 | 높음 (WHERE 코드값 정확도, 금융 도메인 필수) | 하 | §1.2 |
| **4** | `_set_failure_context()` confidence 기준을 status 기반으로 통일 | 중간 (recovery_agent 프롬프트 맥락 정확성) | 하 | §3.1 |
| **5** | `inferred_key_date_column` 필드 정리 (구현 또는 제거) | 낮음 (죽은 코드 정리) | 하 | §2.2 |

### 확인 필요 (6건)

| 순위 | 항목 | 확인 포인트 | 상세 |
| --- | --- | --- | --- |
| **6** | knowledge_interpreter use_cases 건수 제한 | 폐쇄망 모델 컨텍스트 예산 내 수용 가능한지 | §1.4 |
| **7** | candidate_tables 중복 테이블 방지 | 골든셋 평가에서 실제 중복 빈도 확인 | §3.3 |
| **8** | Phase 2 DB 쿼리 loop_guard 반영 | 폐쇄망 DB 응답 지연 시 타임아웃 시나리오 검증 | §3.4 |
| **9** | `_promote_sampled_confidence()` PENDING 테이블 조건 | 의도적 낙관 처리인지 설계 확인 | §1.6 |
| **10** | `_relevant` 기본값 True — recovery 경로 edge case | recovery 후 미태그 use_case 발생 빈도 확인 | §2.4 |
| **11** | `discovered_facts` sql_generator 전달 검토 | 전달 시 품질 개선 효과 A/B 테스트 | §2.5 |

### 적정 (5건)

| 항목 | 사유 | 상세 |
| --- | --- | --- |
| join_keys 제거 → LLM 자율 추론 전환 | 의도적 설계 변경, StructuralHints 보완 시 완전 해소 | §1.1 |
| calculate_readiness() 재설계 | REJECTED 분모 문제 자체가 해소됨 | §1.3 |
| inferred 3측면 (entity_scope 등) | 생성 → 비교 판정 + SQL 프롬프트 양쪽에서 정상 소비 | §2.1 |
| hypotheses 내부 제어 전용 | LLM 프롬프트 전달 불필요, 의도된 설계 | §2.8 |
| TABLE_COMPARISON_SYSTEM 정리 | 미사용 프롬프트 리네임 + import 제거 완료 | §3.5 |

---

## 6. 원본 문서 대비 변경 추적 요약

| 원본 문서 항목 | 현재 상태 | 액션 분류 | 변경 사유 |
| --- | --- | --- | --- |
| 1.1 join_keys from_meta 미파싱 | **해당 없음** | `적정` | join_keys 필드 자체 제거 |
| 1.2 join_keys 병합 누락 | **해당 없음** | `적정` | join_keys 필드 자체 제거 |
| 1.3 code_map sql_generator 미전달 | **부분 해소** | `필수 개선` | 간접 전달 존재하나 코드값 원본 미전달 |
| 1.4 REJECTED 테이블 분모 포함 | **구조 변경** | `적정` | table_coverage 점수 제거. ct_count 잔존은 §3.1 |
| 1.5 use_cases 건수 무제한 | **부분 해소** | `확인 필요` | sql_generator top 10 제한. 나머지 미제한 |
| 2.1 Phase 2 DB loop_guard 미반영 | **여전히 유효** | `확인 필요` | — |
| 2.2 중복 테이블 방지 미비 | **여전히 유효** | `확인 필요` | — |
| 2.3 recovery_agent preprocessed_input 미전달 | **여전히 유효** | `필수 개선` | — |
| 2.4 confidence vs status 불일치 | **여전히 유효** | `필수 개선` | — |
| 2.5 exploration_history SQL 미포함 | **여전히 유효** | `확인 필요` | §2.6 해소 시 연계 해결 |
| 2.6 _promote PENDING 승격 | **여전히 유효 (경감)** | `확인 필요` | Phase 5 REJECTED KI 삭제로 최악 방지 |
| 3.2 (7) StructuralHints 미활용 | **여전히 유효** | `필수 개선` | ReasoningState 미저장, sql_generator 미전달 |
| — (신규) TABLE_COMPARISON_SYSTEM 미사용 | **정리 완료** | `적정` | 프롬프트 리네임 + import 제거 (2026-04-03) |
| — (신규) inferred_key_date_column 죽은 필드 | **신규 발견** | `필수 개선` | 기록 경로 없음, 읽기 경로 활성 |
| — (신규) discovered_facts sql_generator 미활용 | **신규 발견** | `확인 필요` | recovery 전용 설계, 확장 가치 검토 |
| — (신규) validation_checks sql_generator 미전달 | **신규 발견** | `확인 필요` | insight_builder 전용, 재시도 피드백 약함 |
| — (신규) _relevant 기본값 True | **신규 발견** | `확인 필요` | 미태그 use_case가 relevant로 간주 |
