# 8-Slot 정규화 결과의 파이프라인 전파 분석 및 개선점 도출

## 목차

- [1. 배경과 목적](#1-배경과-목적) — 정규화 결과가 하류 노드에 실제로 어떻게 전달·소비되는지 전수 점검
- [2. 시뮬레이션 질의 4건](#2-시뮬레이션-질의-4건) — 대표 유형별 8-Slot 정규화 OUTPUT
- [3. 하류 전파 맵](#3-하류-전파-맵) — 노드별 실제 주입 placeholder / 직렬화 input (표)
- [4. 소비·비소비 요약표](#4-소비·비소비-요약표) — 슬롯 × 노드 소비 매트릭스
- [5. 개선점 9건 (우선순위)](#5-개선점-9건-우선순위) — 근거·기대효과·변경 범위
- [6. 사용자 질문 9건에 대한 검증 답변](#6-사용자-질문-9건에-대한-검증-답변) — Q1~Q9 (Q10은 제외)
- [7. 후속 작업 제안](#7-후속-작업-제안) — 합의 필요 항목과 착수 전 체크리스트

---

## 1. 배경과 목적

정규화(`query_normalizer`)가 생성하는 8-Slot `NormalizedQuery`는 Interpret → Reason → Present 전 구간에서 참조된다. 그러나 실제 **어떤 슬롯이 어떤 노드에 어떤 형태로 전달되어 소비되는가**는 문서화되어 있지 않다. 본 문서는:

1. 대표 질의 4건에 대해 정규화 OUTPUT을 정리
2. 각 OUTPUT이 하류 노드에 전파될 때의 **실제 직렬화 형태**(프롬프트 placeholder 기준) 확인
3. 정규화 구조와 실제 소비 간의 **갭(gap)**을 근거로 개선점 도출

근거는 모두 소스코드 경로·행 번호로 추적 가능하게 명시한다.

---

## 2. 시뮬레이션 질의 4건

각 질의별로 정규화 핵심 슬롯만 발췌한다(전체 JSON은 부록 생략). Phase2 통과 기준.

### Q-A. EXTRACT + RANK + 지역 필터
> "서울 지역 지점 중 여신잔액 상위 10개 지점 뽑아줘"

```yaml
intent: { primary: EXTRACT, secondary: [RANK] }
entities: [지점]
measures: [{term: 여신잔액, agg_function: SUM}]
dimensions: [{term: 지점, role: GROUP}]
filters: [{target: 지역, filter_type: IN, values: ["서울"]}]
time: { type: SNAPSHOT, resolve: LATEST }
modifiers: [{type: RANK, by: 여신잔액, limit: 10, order: DESC}]
output_hint: { format: LIST, expected_columns: [지점명, 여신잔액] }
search_keywords:
  meta_search: [지점, 여신잔액, 지역]
  vector_search: "서울 지점별 여신잔액 상위 조회"
  sql_history_search: "지점별 여신잔액 상위 10건 조회"
ambiguities: []
```

### Q-B. AGGREGATE + TREND (월별 추이)
> "최근 6개월 월별 신규 대출 실행금액 추이 보여줘"

```yaml
intent: { primary: TREND, secondary: [AGGREGATE] }
measures: [{term: 대출실행금액, agg_function: SUM}]
dimensions:
  - {term: 월, role: GROUP, is_time_dimension: true, granularity: MONTH}
filters: [{type: IMPLICIT, field: 대출구분, value: "신규"}]
time: { type: RANGE, resolve: LAST_N_MONTHS, value: 6 }
modifiers: []
output_hint: { format: TREND_CHART, expected_columns: [월, 실행금액] }
search_keywords:
  meta_search: [대출, 실행금액, 신규대출]
  vector_search: "월별 신규 대출 실행금액 추이"
  sql_history_search: "월별 신규 대출 실행금액 합계 시계열"
ambiguities: [{slot: filters, description: "신규 정의 확인 필요", decision: ASK}]
```

### Q-C. COMPARE + DISTRIBUTE (연체율)
> "이번달 지점별 연체율을 지난달과 비교해줘"

```yaml
intent: { primary: COMPARE, secondary: [DISTRIBUTE] }
measures:
  - {term: 연체율, agg_function: RATIO, measure_type: DERIVED,
     note: "연체금액/총대출잔액*100"}
dimensions: [{term: 지점, role: GROUP}]
filters: []
time:
  type: COMPARISON
  base_period: { resolve: THIS_MONTH }
  compare_period: { resolve: LAST_MONTH }
modifiers: [{type: PERCENTAGE}]
output_hint: { format: COMPARISON_TABLE, expected_columns: [지점, 이번달연체율, 지난달연체율, 증감] }
search_keywords:
  meta_search: [지점, 연체금액, 대출잔액, 연체율]
  vector_search: "전월 대비 지점별 연체율 비교"
  sql_history_search: "지점별 연체율 월간 비교 조회"
ambiguities: []
```

### Q-D. EXTRACT + 포괄 키워드 (output 모호)
> "정상 대출 고객 명세 뽑아줘"

```yaml
intent: { primary: EXTRACT, secondary: [] }
entities: [고객, 대출]
measures: []
dimensions: [{term: 고객, role: DISPLAY}]
filters: [{type: IMPLICIT, field: 대출상태, value: "정상", note: "비즈니스 규칙 확인"}]
time: { type: SNAPSHOT, resolve: LATEST }
modifiers: []
output_hint: { format: LIST, doc_type: null, expected_columns: [] }
search_keywords:
  meta_search: [대출, 고객, 대출상태]
  vector_search: "정상 대출 고객 목록 조회"
  sql_history_search: "정상 대출 고객 명세 조회"
ambiguities:
  - {slot: output_hint, description: "명세가 어떤 컬럼을 의미하는지 불명확", decision: ASK}
```

---

## 3. 하류 전파 맵

하류 노드가 **실제로 읽는 필드**만 열거한다(코드·프롬프트 grep 결과 기반).

### 3.1 reasoning_preparer → query_decomposition 압축

| 원본 슬롯 | → 변환 | 파일:행 |
|---|---|---|
| `measures[]` | `[{term, agg_function}]` | [reasoning_preparer.py:162-165](src/agents/nodes/reason/reasoning_preparer.py#L162-L165) |
| `filters[]` | `[{term, operator, value}]` (value는 list 그대로) | [reasoning_preparer.py:167-170](src/agents/nodes/reason/reasoning_preparer.py#L167-L170) |
| `dimensions[role=GROUP]` | `group_by: [term,...]` | [reasoning_preparer.py:172-174](src/agents/nodes/reason/reasoning_preparer.py#L172-L174) |
| `dimensions[role!=GROUP]` | **❌ 누락** (DISPLAY/FILTER_ONLY 등 손실) | — |
| `modifiers[]` | `order_limit: [{type, value}]` (limit·by 중 하나만 str, order/금액기준 손실) | [reasoning_preparer.py:176-179](src/agents/nodes/reason/reasoning_preparer.py#L176-L179) |
| `output_hint` | `{format, doc_type, expected_columns}` (note 제외) | [reasoning_preparer.py:184-191](src/agents/nodes/reason/reasoning_preparer.py#L184-L191) |
| `intent.primary/secondary` | **❌ decomposition에 담기지 않음** | — |
| `time` | **❌ decomposition에 담기지 않음** | — |
| `entities[]` | `required_concepts: [term,...]` (타입 손실) | [reasoning_preparer.py:181-182](src/agents/nodes/reason/reasoning_preparer.py#L181-L182) |
| `search_keywords.meta_search` | space join → `_extract_meta_search_query` → `search_table_meta` input | [reasoning_preparer.py:348-371](src/agents/nodes/reason/reasoning_preparer.py#L348-L371) |
| `search_keywords.vector_search` | **❌ 읽지 않음** — `search_use_cases` input은 `original_query` 사용 | [reasoning_preparer.py:391-397](src/agents/nodes/reason/reasoning_preparer.py#L391-L397) |
| `search_keywords.sql_history_search` | **❌ 읽지 않음** (소비자 없음) | — |

### 3.2 sql_generator 프롬프트 placeholder (실제 주입 값)

[sql_generator.py:422-468](src/agents/nodes/reason/sql_generator.py#L422-L468) — `_build_agentic_prompt`가 주입하는 placeholder 전수:

| placeholder | 출처 |
|---|---|
| `{original_query}` | `state.preprocessed_input` |
| `{rewritten_query}` | `nq.rewritten_query` |
| `{expected_columns}` | `decomp.output_hint.expected_columns` |
| `{confirmed_terms}` | knowledge_items 중 CONFIRMED |
| `{tables}` / `{codes}` / `{reference_sqls}` | reason context |
| `{dead_ends}` | reason.dead_ends |
| `{clarification_context}` | `build_clarification_context(state)` (resolved_signals + Q&A 히스토리) |
| `{handoff_note}` | CONTINUE 축적 노트 |
| `{previous_sql}` / `{previous_sql_explanation}` | REGENERATE 시 직전 SQL |
| `{current_date}` | 시스템 시각 |

**미주입**: `intent`, `time`(base/compare/resolve), `modifiers`(type/by/limit/order/percentage), `dimensions` (GROUP 외 role), `entities`, `filters` 원형, `output_hint.format/doc_type`.

→ sql_generator는 자연어(`rewritten_query`)에서 이들을 재해석해야 함. Phase2에서 보정한 구조화 결과가 LLM 입력으로 전달되지 않음.

### 3.3 sql_validator 프롬프트 (부분 소비)

[sql_validator.py:627, 676-685](src/agents/nodes/reason/sql_validator.py#L676-L685) — **구조화 슬롯을 부분 소비하는 유일한 Reason 노드**:

- `dimensions[role=GROUP]` → "집계 차원: ..." 섹션
- `modifiers` (RANK/LIMIT 제외한 계산 가공) → "계산 가공: ..." 섹션
- `output_hint.format + doc_type + expected_columns` → "출력 힌트: ..." 섹션

### 3.4 recovery_agent 프롬프트

[recovery_agent.py:1073-1138](src/agents/nodes/reason/recovery_agent.py#L1073-L1138) — placeholder 전수:

| placeholder | 내용 |
|---|---|
| `{original_query}` / `{rewritten_query}` | 질의 자연어 |
| `{confirmed_knowledge}` / `{unresolved_items}` | knowledge_items |
| `{explored_tables_summary}` | 탐색 테이블 |
| `{dead_ends_summary}` | 이전 실패 |
| `{tool_execution_history}` | 도구 실행 이력 |
| `{sample_data_summary}` | 샘플 행 수 요약 |
| `{clarification_history}` | `_build_clarification_history(state)` 산출 |
| `{ask_user_eligible_items}` | ASK 후보 |
| `{handoff_note}` | 멀티턴 힌트 |
| `{previous_sql}` / `{previous_sql_explanation}` | 이전 턴 SQL |

**주의**: `{clarification_context}` 가 아니라 `{clarification_history}` 로 주입. `resolved_signals`는 `clarification_history` 에 포함되는지 별도 확인 필요 ([recovery_agent.py:501](src/agents/nodes/reason/recovery_agent.py#L501) 로직).

### 3.5 analyzer / visualizer (Present)

- `analyzer_node` ([analyzer.py:50-73](src/agents/nodes/present/analyzer.py#L50-L73)):
  - `user_input = state.analysis_query or state.preprocessed_input`
  - `handoff_note` 전달
  - **`NormalizedQuery` 미전달** (intent·time·output_hint 모두 소비 안 함)
- `visualizer_node` ([visualizer.py:45-80](src/agents/nodes/present/visualizer.py#L45-L80)):
  - `build_visualization(sql_result, ..., handoff_note=state.handoff_note)` 호출
  - **시그니처에 `user_input`/`analysis_query` 파라미터 없음** ([data_analyzer.py:362-444](src/services/data_analyzer.py#L362-L444))
  - 단일 턴에서 "원형차트로 보여줘" 같은 사용자 요청이 전달되지 않음

---

## 4. 소비·비소비 요약표

| 슬롯 | normalizer | reasoning_preparer | sql_generator | sql_validator | recovery_agent | analyzer | visualizer |
|---|---|---|---|---|---|---|---|
| intent.primary/secondary | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| entities | ✅ | △ (term만) | ❌ | ❌ | ❌ | ❌ | ❌ |
| measures | ✅ | ✅ (term+agg) | △ (expected_cols 경유) | △ | △ | ❌ | ❌ |
| dimensions[GROUP] | ✅ | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ |
| dimensions[기타 role] | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| filters | ✅ | △ (operator/value) | ❌ | ❌ | ❌ | ❌ | ❌ |
| time.type/base/compare/resolve | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| modifiers | ✅ | △ (일부 손실) | ❌ | ✅ (RANK/LIMIT 제외) | ❌ | ❌ | ❌ |
| output_hint.format/doc_type | ✅ | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ |
| output_hint.expected_columns | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| search_keywords.meta_search | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| search_keywords.vector_search | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| search_keywords.sql_history_search | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| ambiguities | ✅ | △ (pending/resolved로 분리) | △ (resolved만) | ❌ | △ (clarification_history) | ❌ | ❌ |

범례: ✅ 소비, △ 부분 손실 소비, ❌ 소비 없음

---

## 5. 개선점 9건 (우선순위)

**우선순위 기준**: 영향도(정확도/재현성) × 적용 비용 × 기대 효과.

| # | 제목 | 우선순위 | 타입 |
|---|---|---|---|
| 1 | `search_use_cases` 입력 = `sql_history_search` 사용 | 높음 | 정확도 |
| 2 | modifier 구조 주입 재검토 (sql_generator에 RANK/DELTA 전달) | 높음 | 정확도 |
| 3 | filter KI 키 list 표기 안정화 | 낮음 | 보수성 |
| 4 | DISPLAY 역할 dimension도 정규화→SQL 경로에 노출 | 중간 | 정확도 |
| 5 | analyzer에 intent/time 전달 (선택적) | **제외 권고** | — |
| 6 | visualizer에 사용자 질의 전달 | 높음 | 기능 |
| 7 | formatter의 `output_hint.format` 소비 분기 추가 검토 | 중간 | 기능 |
| 8 | recovery_agent의 resolved_signals 접근 재검증 | 중간 | 정확도 |
| 9 | Phase1 프롬프트 `HARD_CONSTRAINTS`/`FORMAT_LOCK`/`OUTPUT_CONTRACT` 중복 정리 | 낮음 | 효율 |

상세 근거는 §6 Q&A 답변에 통합 서술한다.

---

## 6. 사용자 질문 9건에 대한 검증 답변

> 원칙: 방어가 아닌 재검토. 원래 분석이 틀렸으면 수정, 맞으면 근거 제시.

### Q1. `sql_history_search`가 use case 조회에 유용한 입력인가?

**결론: 유용하다. 채택 권고.**

근거:
- Qdrant `sql_history` 컬렉션의 임베딩 원천은 **SQL 설명 문장**이다 (`.claude/skills/seed-qdrant/SKILL.md` 확인). 예: _"지점별 여신 잔액 상위 10개 지점의 대출 건수와 총 잔액을 조회한 데이터"_
- 현재 `_build_execution_plan`은 `original_query`만 `search_use_cases` input으로 사용 ([reasoning_preparer.py:391-397](src/agents/nodes/reason/reasoning_preparer.py#L391-L397)). 원본 질의는 **조사·동사·구어체**가 섞여 있어 설명문 임베딩과 의미적 거리가 있다.
- `sql_history_search`는 `_post_build_sql_history_search`가 `rewritten + 동작어 + entities/measures/dimensions`로 조합하여 저장 [query_normalizer.py:530-567](src/services/query_normalizer.py#L530-L567) — **설명문 스타일에 가깝다**.

변경: `_build_execution_plan`에서 step1의 input을 `sql_history_search`로 교체. 없을 때만 `original_query` 폴백.

### Q2. modifier / time 구조가 필요한가? 정규화는 잘 되나, sql_generator가 소비할 필요가 있나?

**결론: (a) 정규화 품질은 괜찮음, (b) sql_generator는 modifier/time을 자연어로만 받고 있어 risk 존재.**

근거:
- 정규화는 R1~R12 검증 덕에 modifier/time 정합성은 유지됨 ([query_normalizer_phase2_system.txt](resources/prompts/interpret/query_normalizer_phase2_system.txt) R2·R3·R4·R5).
- 그러나 sql_generator 프롬프트는 `{rewritten_query}` 외에 modifier/time 구조를 전혀 받지 않는다 ([sql_generator.py:422-468](src/agents/nodes/reason/sql_generator.py#L422-L468)). "최근 6개월", "상위 10건", "전월 대비" 같은 구조화된 정보가 있음에도 LLM이 자연어에서 재파싱한다.
- sql_validator만 계산 가공 modifier와 차원 정보를 구조로 받음 ([sql_validator.py:627-687](src/agents/nodes/reason/sql_validator.py#L627-L687)). 생성 단계보다 검증 단계가 더 많은 구조 정보를 쓰는 역구조.

권고: sql_generator 프롬프트에 `{execution_hints}` (time.resolve / COMPARISON base·compare / RANK by·limit·order / DELTA_RATE 등)를 구조화 주입. 자연어에 더해 구조 힌트를 병기하면 폐쇄망 70B/397B 모델의 SQL 정확도가 올라갈 가능성이 큼 (Claude 대비 자연어 해석력 열위 보완).

### Q3. `filter:지역=['서울']` 같은 KI 키 표기가 이렇게까지 필요한가?

**결론: 현재 형태는 과설계. 리스트 repr은 불안정하고 실익이 적다.**

근거:
- KI 키 생성: `key=f"filter:{term}={value}"` — `value`가 list면 `['서울']` 같은 Python repr이 키에 박힘 ([reasoning_preparer.py:230-233](src/agents/nodes/reason/reasoning_preparer.py#L230-L233)).
- KI 키의 실제 용도는 **UNRESOLVED→CONFIRMED 상태 추적용 식별자**로만 쓰인다. 값 원형이 키 안에 들어갈 필요는 없다.
- 리스트 순서·공백·따옴표 스타일이 바뀌면 같은 필터가 다른 키로 보여 상태 갱신 실패 위험.

권고: 키는 `filter:{term}` 으로 단축, `value`는 `KnowledgeItem.value` 필드에 담는다. 기존 매칭 로직이 있으면 동시 마이그레이션.

### Q4. DISPLAY dimension이 sql_generator에 파싱된 형태로 전달될 필요가 있나?

**결론: 부분적으로 필요. 다만 `output_hint.expected_columns`로 대체 가능하다면 불필요.**

근거:
- `_build_decomposition_from_normalized`는 `role=="GROUP"`만 `group_by`에 담는다 ([reasoning_preparer.py:172-174](src/agents/nodes/reason/reasoning_preparer.py#L172-L174)). DISPLAY/FILTER_ONLY는 손실.
- Q-D 사례("고객 명세")에서 `dimensions=[{term:고객, role:DISPLAY}]`는 SELECT 컬럼 힌트로 기능해야 하지만, 현재 `expected_columns: []`이면 sql_generator에 아무 정보도 안 간다.
- 단, `output_hint.expected_columns`가 채워져 있으면 동일 역할을 수행 ([sql_generator.py:434](src/agents/nodes/reason/sql_generator.py#L434)).

권고: 둘 중 하나 선택.
- (A) reasoning_preparer에서 `dimensions[role=DISPLAY]`를 `expected_columns`에 merge.
- (B) DISPLAY role을 정규화 스키마에서 제거하고 항상 `output_hint.expected_columns`로 수렴.

**(B)를 추천** — 슬롯 간 책임 중첩 제거.

### Q5. analyzer는 추출된 데이터를 보므로 intent/assumption 주입이 불필요?

**결론: 맞다. 주입 불필요. 원 분석의 개선점 #5는 철회한다.**

근거:
- analyzer는 `sql_result` (행·컬럼)와 `user_input` (analysis_query)·`handoff_note`를 받는다 ([analyzer.py:50-73](src/agents/nodes/present/analyzer.py#L50-L73)).
- 분석의 방향성은 `analysis_query`(질의 문장)와 실제 데이터로 충분히 결정됨. intent는 이미 DATA_ANALYSIS로 한정되어 있고, time/filter는 데이터 자체에 반영됨.
- 구조화 슬롯을 추가로 주입하면 **중복 정보로 인한 혼선 위험**이 크고 효용은 작음.

→ §5 표에서 #5는 "제외 권고"로 유지.

### Q6. visualizer에 analysis_query 전달이 로직에 반영 안 되었는가?

**결론: 맞다. 단일 턴 경로에서 사용자 질의가 visualizer에 전달되지 않는다. 실질 문제.**

근거:
- `visualizer_node`는 `build_visualization(sql_result, ..., handoff_note=state.handoff_note)`만 호출 ([visualizer.py:45-80](src/agents/nodes/present/visualizer.py#L45-L80)).
- `build_visualization` 시그니처에 `user_input` 파라미터 없음 ([data_analyzer.py:362-444](src/services/data_analyzer.py#L362-L444)). (비교: `analyze_data`는 `user_input` 받음.)
- CONTINUE의 경우 `handoff_note`에 사용자 재지시가 축적되지만, **첫 턴에서 "원형차트로 보여줘"** 같은 요청은 `analysis_query`/`preprocessed_input`에 있고 visualizer까지 도달하지 못함.

권고: `build_visualization`에 `user_input` 파라미터 추가 → viz_judgment_user 프롬프트에 치환. `state.analysis_query or state.preprocessed_input`를 넘긴다.

### Q7. output_hint.format이 "어디로도 흐르지 않음"은 무슨 뜻? 확실한가?

**결론: 원 분석의 "어디로도 안 흐른다"는 부정확. 수정.**

실제:
- `output_hint.format`은 **sql_validator가 소비**한다 ([sql_validator.py:676-685](src/agents/nodes/reason/sql_validator.py#L676-L685)) — "출력 힌트: SUMMARY, 문서유형=..., 기대컬럼=[...]" 섹션.
- 정규화에서는 `rewritten_query`에 "출력형식=..." 을 병기하여 하류 프롬프트가 자연어로 읽을 수 있게 함 ([query_normalizer.py:179-181](src/agents/nodes/interpret/query_normalizer.py#L179-L181)).

재프레이밍:
- **진짜 공백은 Present 단계.** response_formatter/analyzer/visualizer는 `output_hint.format`(LIST / TREND_CHART / COMPARISON_TABLE / SUMMARY 등)을 읽어 렌더링 분기에 사용할 수 있지만 **현재는 소비하지 않음**.
- 질문: 사용자가 "트렌드 차트로 보여줘"라고 하면 `output_hint.format=TREND_CHART`가 설정되는데, visualizer가 이를 읽지 않는다. → Q6과 연결되는 이슈.

권고: `build_visualization`에 `output_hint` 전달 옵션 추가. viz_judgment_user 프롬프트에 "사용자 요청 형식: {format}" 주입.

### Q8. "ambiguities[INFER] recovery leak"은 무슨 뜻? 확실한가?

**결론: 원 표현이 모호했다. 정확히 다시 서술.**

현재 상태:
- `ambiguities`는 normalizer에서 `pending_signals`(ASK) / `resolved_signals`(INFER)로 분리 저장 ([query_normalizer.py](src/agents/nodes/interpret/query_normalizer.py)).
- sql_generator는 `build_clarification_context(state)`로 resolved_signals를 **읽는다** ([sql_generator.py:422-425](src/agents/nodes/reason/sql_generator.py#L422-L425)).
- recovery_agent는 `{clarification_history}` placeholder로 받는다 ([recovery_agent.py:1123](src/agents/nodes/reason/recovery_agent.py#L1123)) — 이는 `_build_clarification_history(state)` 결과로, **질문·응답 히스토리** 위주.

실제 갭:
- `resolved_signals` (정규화 단계 INFER 결정 — 예: "정상 대출 → 대출상태=정상"으로 가정)이 `clarification_history`에 포함되는지 확인 필요. 포함 안 되면 **recovery 시 INFER 가정이 누락**되어 동일 추론을 다시 실패할 위험.

재명명: "recovery leak" → **"recovery_agent가 resolved_signals를 받지 못할 수 있음"**.

권고: `_build_clarification_history` 실제 출력을 로그로 확인 후, resolved_signals도 함께 주입하도록 확장 (또는 별도 `{inferred_assumptions}` placeholder 신설).

### Q9. Phase1 프롬프트 `HARD_CONSTRAINTS` / `FORMAT_LOCK` / `OUTPUT_CONTRACT` 진짜 중복인가?

**결론: 일부 중복 있음. 다만 치명적이진 않고, 정리하면 토큰 10~15줄 절감 수준.**

근거 (line-by-line):
- **HARD_CONSTRAINTS** (L10-22, 7개 항목): 주로 JSON 형식·enum·ambiguities 채움 규칙.
  - 1번 "출력은 JSON 객체 하나로만 구성. 첫 문자는 `{`"
  - 2번 "JSON 이전/이후 설명 텍스트 없음"
  - 6번 "큰따옴표만. 마크다운 코드 펜스 없음"
- **FORMAT_LOCK** (L629-634, 5개 항목): JSON 하나·첫 문자 `{`·마크다운 금지·이전후 텍스트 금지·큰따옴표.
  - → HARD_CONSTRAINTS 1·2·6과 **직접 중복**.
- **OUTPUT_CONTRACT** (L609-627): JSON 최상위 구조 정의 (키 이름·중첩). → 중복 아님, 구조 정의 역할.

실제 중복: **FORMAT_LOCK ≡ HARD_CONSTRAINTS 1/2/6** (내용 대부분 겹침).

권고:
- `FORMAT_LOCK` 섹션 제거 (HARD_CONSTRAINTS에 흡수).
- Phase2 프롬프트도 동일 구조인지 확인하여 동시에 정리.
- OUTPUT_CONTRACT는 유지 (역할이 다름).

### Q10. [검토 제외]

사용자 지시에 따라 제외.

---

## 7. 후속 작업 제안

**착수 전 합의 필요 항목** (사용자 확인 후 진행):

1. Q1: `search_use_cases` input 교체 → reasoning_preparer 1곳 수정, 폴백 로직 유지 확인 필요.
2. Q2: sql_generator 프롬프트에 `{execution_hints}` 구조 주입 → **스키마 + 프롬프트 변경**, 회귀 테스트 필요.
3. Q3: filter KI 키 단축 → knowledge_items 소비처 전수 영향 분석 선행.
4. Q4: DISPLAY role 수렴 방향(A/B) 선택.
5. Q6: `build_visualization`에 `user_input`(+ `output_hint`) 추가.
6. Q7: formatter/visualizer의 `output_hint.format` 소비는 Q6과 묶어 추진.
7. Q8: `_build_clarification_history` 로그 확인 → resolved_signals 누락 여부 결정.
8. Q9: Phase1 프롬프트 FORMAT_LOCK 제거 + Phase2 동조화 점검.

**메모리 원칙 적용**: 위 8개 항목 모두 "수정 전 검토 필수" — 각 항목은 개별 합의 후 PR 분리 추진.
