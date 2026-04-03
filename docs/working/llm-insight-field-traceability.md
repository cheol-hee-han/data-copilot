# LLM 판단 및 인사이트 필드 추적 가이드

파이프라인 전체에서 LLM이 생성하는 비정형 판단 텍스트가
**어디서 생성되어, 어디에 저장되고, 어느 노드에서 소비되는지** 추적하기 위한 문서.

- **작성일**: 2026-03-28
- **대상 코드**: v2 (3계층 파이프라인)

---

## 1. 전체 흐름 개요

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         INTERPRET PHASE                                  │
│  (비정형 텍스트 생성 없음 — 구조화된 슬롯 분해만 수행)                  │
└────────────────────────────────┬──��──────────────────────────────────────┘
                                 │
┌────────────────────────────────▼─────────────────────────────────────────┐
│                          REASON PHASE                                    │
│                                                                          │
│  planner ──→ context_explorer ──→ evaluator ──→ sql_generator            │
│     │             │                                    │                  │
│     │        batch_interpret                    sql_validator             │
│     │             │                                    │                  │
│     │             ▼                                    ▼                  │
│     │     insight, evidence,              fix_instruction,               │
│     │     comparison_reason,              checks[].detail                │
│     │     entity_scope,                          │                       │
│     │     functional_usage,                      ▼                       │
│     │     data_refresh_hint          recovery_planner                    │
│     │             │                       │                              │
│     │             ▼                       ▼                              │
│     │     KnowledgeItem         lessons_learned (⚠ 미저장),             │
│     │     CandidateTable        new_hypothesis.description,              │
│     │             │             new_hypothesis.strategy                   │
│     │             ▼                       │                              │
│     └──── discovered_facts ◄──────────────┘                              │
│            confirmed_knowledge                                           │
│                                                                          │
│  result_finalizer                                                        │
│     └─ exploration_summary (코드 조립, LLM 아님)                         │
└────��─────────────────────────────────────────────────────────────────────┘
```

---

## 2. 비정형 텍스트 필드 전수 목록

### 범례
- **생성**: LLM 프롬프트 + 응답의 JSON 키
- **저장**: State 필드 또는 저장 여부
- **소비**: 다음 LLM 프롬프트에 주입되는 경로
- **유의미성**: 이 필드가 파이프라인 결과에 미치는 영향도

---

### 2.1 `batch_interpret` — 도구 결과 해석 (context_explorer 내부)

**프롬프트**: `resources/prompts/reason/batch_interpret_system.txt`
**LLM 호출 코드**: `src/agents/nodes/reason/context_explorer.py` → `_interpret_batch()` (line 388~468)
**파싱 코드**: `src/agents/nodes/reason/context_explorer.py` → `_parse_batch_result()` (line 480~505)

#### 2.1.1 `interpretations[].insight`

| 항목 | 내용 |
|:---|:---|
| **정의** | 각 도구 호출 결과에 대한 **한 줄 관찰 메모** |
| **예시** | "DEP예금신규계좌잔액(T+0) 테이블의 '개설일자(OPEN_DT)'와 '소속부서코드(BLNG_BRCD)'를 보유하고 있어 신규 유입 분석에 필수적" |
| **저장 위치** | `ExecutionStep.insight` (line 564~567, `_apply_batch_insights`) |
| **소비 경로** | `recovery_planner._build_replan_context()` (line 126~131) → `{discovered_facts}` 플레이스홀더로 recovery_planner LLM에 주입 |
| **영향도** | **높음** — recovery_planner가 "지금까지 뭘 알아냈는지"를 파악하는 유일한 근거. insight 품질이 나쁘면 recovery가 이미 알고 있는 정보를 재탐색하거나, 잘못된 교훈을 도출함 |
| **점검 포인트** | 1) insight가 도구 결과의 핵심을 정확히 요약하는지 2) 테이블명/컬럼명이 실제 메타와 일치하는지 3) 추론이 아닌 사실만 기술하는지 |

#### 2.1.2 `interpretations[].knowledge_updates[].evidence`

| 항목 | 내용 |
|:---|:---|
| **정의** | knowledge_item의 **판단 근거** — 왜 이 상태(CONFIRMED/PROBABLE)인지에 대한 설명 |
| **예시** | "계좌 개설일자(OPEN_DT)와 소속 부서(BLNG_BRCD) 컬럼을 보유하여 신규 유입 금액 집계가 가능" |
| **저장 위치** | `KnowledgeItem.evidence` (line 487~493, `_parse_batch_result`) |
| **소비 경로** | `recovery_planner._build_replan_context()` (line 145~153) → CONFLICTED 상태일 때 `{unresolved_items}`에 evidence 마지막 2건이 포함됨 |
| **영향도** | **중간** — 정상 흐름에서는 직접 소비되지 않으나, CONFLICTED 상태에서 recovery_planner가 충돌 원인을 파악하는 데 사용됨 |
| **점검 포인트** | 1) 근거가 구체적인지 (단순 "확인됨"이 아닌 실제 컬럼/값 기반) 2) confidence 수치와 evidence의 강도가 일관적인지 |

#### 2.1.3 `comparison_reason`

| 항목 | 내용 |
|:---|:---|
| **정의** | 후보 테이블 중 **적합/부적합 판정 사유** |
| **예시** | "신규 유입 예금 분석을 위해 계좌별 개설일자(OPEN_DT)와 소속 부서(BLNG_BRCD)를 보유한 TB_ADW_DEP201P가 가장 적합. 나머지 테이블들은 달력, 미수금, 보증금 등 관계없는 도메인" |
| **저장 위치** | `BatchInterpretResult.comparison_reason` (line 504) |
| **소비 경로** | 로깅 전용 (line 275, 289) → tracker `table_comparison` decision으로 기록 |
| **영향도** | **낮음 (진단용)** — 다른 LLM에 직접 전달되지 않으나, trace 분석 시 테이블 선택 근거를 확인하는 유일한 필드 |
| **점검 포인트** | 1) selected/rejected 결과와 comparison_reason의 논리가 일치하는지 2) 거부된 테이블에 대해 구체적 거부 사유가 있는지 |

#### 2.1.4 `interpretations[].new_tables[].entity_scope` / `functional_usage` / `data_refresh_hint`

| 항목 | 내용 |
|:---|:---|
| **정의** | LLM이 추론한 테이블의 **엔티티 범위** / **업무 용도** / **데이터 갱신 주기** |
| **예시** | entity_scope: "DEP 예금 계좌 및 잔액 관련 데이터" / functional_usage: "예금 신규 유입 건수 및 금액 분석" / data_refresh_hint: "실시간 갱신, 기준일자로 필터 필수" |
| **저장 위치** | `CandidateTable.inferred_entity_scope` 등 (line 870~875, `_merge_llm_inferred_fields`) |
| **소비 경로** | **`sql_generator_node`** → `_format_table_details()` (line 87~92) → SQL 생성 프롬프트의 테이블 설명에 `(LLM 추론)` 태그와 함께 주입 |
| **영향도** | **높음** — sql_generator가 테이블의 용도와 특성을 이해하는 유일한 맥락. 잘못된 추론이 부적절한 SQL 생성으로 이어짐 |
| **점검 포인트** | 1) ES 메타의 실제 테이블 설명과 추론이 일치하는지 2) data_refresh_hint가 실제 갱신 주기와 맞는지 (잘못되면 날짜 필터링 오류) 3) functional_usage가 질의 의도와 부합하는 용도를 기술하는지 |

---

### 2.2 `sql_validator` — SQL 의미적 검증

**프롬��트**: `resources/prompts/reason/sql_validator_system.txt`
**LLM 호출 코드**: `src/agents/nodes/reason/sql_validator.py` → `_validate_layer2b()` (line 300~430)

#### 2.2.1 `checks[].detail` (특히 `logical_consistency`)

| 항목 | 내용 |
|:---|:---|
| **정의** | 각 검증 항목의 **상세 판단 사유** |
| **예시** | "사용자 의도는 '신규 유입'이나, 현재 테이블(TB_ADW_DEP201P: 예금신규계좌잔액)의 컬럼(BAL_AMT)은 현재 '잔액'을 의미할 가능성이 있음. '유입 금액'을 표현하기 위해서는 계좌 개설 시 거래 금액(신규 입금액 등)을 식별할 수 있는 별도의 컬럼이나 테이블 참조가 필요한 것으로 판단됨" |
| **저장 위치** | `SqlValidationResult.layer2_failed` / `layer2_passed` (line 160~165) — passed/failed 리스트에 detail 문자열 포함 |
| **소비 경로** | **직접 소비 아님** — detail 자체는 후속 LLM에 전달되지 않음. 대신 FAIL 판정 시 `fix_instruction`이 생성되어 전달됨 |
| **영향도** | **진단용** — trace에서 "왜 FAIL/PASS인지" 확인할 때 핵심. 특히 `logical_consistency`의 detail이 의미적 판단의 근거 |
| **점검 포인트** | 1) PASS인데 detail이 부정확한 경우 (false positive) 2) FAIL의 detail이 실제 SQL 문제를 정확히 지적하는지 3) logical_consistency가 도메인 지식에 기반한 올바른 판단인지 |

#### 2.2.2 `fix_instruction` ★★★ 가장 중요한 피드백 루프

| 항목 | 내용 |
|:---|:---|
| **정의** | FAIL 판정 시 sql_generator에게 전달하는 **구체적 SQL 수정 지시** |
| **예시** | "현재 TB_ADW_DEP201P 테이블의 BAL_AMT는 현재 잔액을 나타내는 컬럼일 수 있습니다. '신규 유입 금액'이라는 비즈니스 요구사항을 정확히 반영하려면, 단순히 잔액을 합산하는 대신 신규 계좌의 최초 입금액 또는 신규 계좌 개설일의 입금액을 나타내는 컬럼(예: NEW_DEP_AMT 또는 거래 테이블 등)을 사용해야 합니다" |
| **저장 위치** | `reason.sql_fix_instruction` (line 168) |
| **소비 경로** | **`sql_generator_node`** (line 239~242) → `REASON_GENERATE_SQL_FIX` 템플릿의 `{fix_instruction}`에 주입 → 다음 SQL 생성 시 "이전 오류 + 수정 지침" 섹션으로 포함 |
| **영향도** | **최고** — 파이프라인에서 **유일한 LLM→LLM 직접 피드백 루프**. fix_instruction의 품질이 SQL 재생성의 성공/실패를 직접 결정 |
| **점검 포인트** | 1) 문제 지적만 하고 구체적 수정 방향을 제시하지 않는 경우 (sql_generator가 같은 실수 반복) 2) DB 실행 오류(타입 불일치 등)의 경우 SQL 수정 예시가 포함되는지 3) 비즈니스 로직 오류와 SQL 구문 오류를 구분하여 적절한 수준의 지시를 내리는지 |

**피드백 루프 상세 경로**:
```
sql_validator LLM → fix_instruction
  ↓
sql_validator.py:168 → reason.sql_fix_instruction에 저장
  ↓
sql_generator.py:239 → if reason.sql_fix_instruction 존재
  ��
sql_generator.py:240~241 → REASON_GENERATE_SQL_FIX 템플릿에 주입
  ↓
sql_generator.py:269 → "{fix_section}" 플레이스홀더 교체
  ↓
sql_generator LLM → 수정된 SQL 생성
```

---

### 2.3 `recovery_planner` — 실패 분석 및 가설 전환

**프롬프트**: `resources/prompts/reason/recovery_planner_system.txt`
**LLM 호출 코드**: `src/agents/nodes/reason/recovery_planner.py` → `_generate_new_hypotheses()` (line 211~273)
**파싱 코드**: `src/agents/nodes/reason/recovery_planner.py` → `_parse_replan_response()` (line 276~305)

#### 2.3.1 `lessons_learned` ⚠ 미저장 필드

| 항목 | 내용 |
|:---|:---|
| **정의** | 이전 실패에서 도출한 **교훈** |
| **예시** | "예금 계좌 잔액(TB_ADW_DEP201P)은 스냅샷 데이터로, 특정 기간 동안의 '신규 유입'이라는 이벤트(Flow)를 파악하기에는 부적합합니다. 신규 유입의 정확한 정의(OPEN_DT)와 거래금액 혹은 최초 입금액을 나타내는 컬럼을 확인해야 합니다." |
| **저장 위치** | **저장되지 않음** — `_parse_replan_response()` (line 287)에서 `give_up`만 확인하고 `lessons_learned`는 추출하지 않음 |
| **소비 경로** | **없음** — LLM 응답 JSON에만 존재하고 파싱 단계에서 버려짐 |
| **영향도** | **잠재적으로 높음이나 현재 활용 안됨** — 이 필드가 다음 사이클의 프롬프트에 포함된다면, recovery_planner가 과거 교훈을 누적하며 점점 더 나은 판단을 할 수 있음 |
| **점검 포인트** | 1) trace JSON에서만 확인 가능 (`llm_calls[].response_text`에서 추출) 2) lessons_learned의 질이 높다면 state에 저장하고 다음 replan 프롬프트에 주입하는 것을 고려 |

#### 2.3.2 `new_hypothesis.description`

| 항목 | 내용 |
|:---|:---|
| **정의** | 새 접근 방식에 대한 **설명** |
| **예시** | "신규는 OPEN_DT로 식별하고, 해당 계좌의 잔액(BAL_AMT)을 유입 금액으로 대용하여 지점별 합산" |
| **저장 위치** | `Hypothesis.description` (line 299) |
| **소비 경로** | 로깅/tracker 전용. 다른 LLM 프롬프트에 직접 주입되지 않음 |
| **영향도** | **낮음 (진단용)** — trace 분석 시 recovery_planner의 전략 변화를 추적하는 데 유용 |

#### 2.3.3 `new_hypothesis.strategy`

| 항목 | 내용 |
|:---|:---|
| **정의** | 구체적 **탐색 전략** 한 줄 요약 |
| **예시** | "잔액 테이블 대신 거래 내역 테이블(History)에서 신규 계좌 입금 레코드 탐색" |
| **저장 위치** | `Hypothesis.strategy` (line 301) |
| **소비 경로** | `_build_replan_execution()` (line 393~400) → missing_terms가 없을 때 `strategy` 텍스트가 `search_use_cases`의 검색 입력으로 사용됨 |
| **영향도** | **중간** — missing_terms가 비어있을 때만 실행 계획에 반영. strategy 텍스트의 품질이 검색 결과 품질에 직결됨 |
| **점검 포인트** | 1) strategy가 검색 쿼리로 사용되기에 적절한 키워드를 포함하는지 2) 너무 추상적이면 검색 결과가 부실해짐 |

#### 2.3.4 `new_hypothesis.missing_terms`

| 항목 | 내용 |
|:---|:---|
| **정의** | 새 가설에서 **해소 필요한 용어 목록** |
| **예시** | `["예금신규의 정의(계좌개설일 기준)", "유입 금액의 정의(최초 잔액 vs 특정 기간 잔액 합계)"]` |
| **저장 위치** | `Hypothesis.missing_terms` (line 300) |
| **소비 경로** | `_build_replan_execution()` (line 389~395) → missing_terms의 처음 3개가 `search_table_meta`의 검색 입력으로 사용됨 |
| **영향도** | **높음** — 다음 탐색 사이클의 **검색 키워드를 직접 결정**. missing_terms가 너무 추상적이면 관련 없는 테이블이 검색되고, 너무 구체적이면 검색 결과가 0건 |
| **점검 포인트** | 1) ES 메타 검색에 적합한 키워드 수준인지 (테이블명, 컬럼명, 업무 용어) 2) 이미 searched_queries에 있는 키워드를 반복하지 않는지 |

#### 2.3.5 `give_up` + `reason`

| 항목 | 내용 |
|:---|:---|
| **정의** | 포기 여부 (`boolean`) + **포기 사유** |
| **예시** | give_up: true, reason: "3가지 접근(테이블메타/보고서SQL/업무매뉴얼) 모두 실패. 'ESG 등급 환산 계수' 관련 데이터가 현재 접근 가능한 정보계 DB에 존재하지 않는 것으로 판단" |
| **저장 위치** | give_up: 파싱 로직 분기 (line 287~288), reason: **저장되지 않음** |
| **소비 경로** | give_up=true → 빈 가설 반환 → `recovery_planner_node`에서 DONE 상태 전환 (line 74~82) |
| **영향도** | **높음** — 파이프라인 종료를 결정. 너무 쉽게 give_up하면 해결 가능한 질의를 포기하고, 너무 고집하면 무한 루프에 가까운 탐색 발생 |
| **점검 포인트** | 1) give_up=true인데 다른 접근법이 남아있는 경우 2) give_up=false인데 동일 패턴을 반복하는 경우 3) reason 필드가 사용자에게 전달 가능한 수준의 설명인지 |

---

### 2.4 `sql_generator` — SQL 생성

**프롬프트**: `resources/prompts/reason/sql_generator_system.txt`
**LLM 호출 코드**: `src/agents/nodes/reason/sql_generator.py` → `_call_llm_for_sql()` (line 282~325)

#### 2.4.1 `explanation` ⚠ 미저장 필드

| 항목 | 내용 |
|:---|:---|
| **정의** | 생성된 SQL의 **로직 설명** 한 줄 |
| **예시** | "이번년도 신규 개설된 계좌의 잔액을 지점별로 합산하여 상위 10개 지점을 조회합니다" |
| **저장 위치** | **저장되지 않음** — `_call_llm_for_sql()` (line 317~320)에서 `data.get("sql")`만 추출하고 `explanation`은 버려짐 |
| **소비 경로** | **없음** |
| **영향도** | **진단용으로 높은 잠재 가치** — SQL의 의도를 LLM이 스스로 설명하는 필드이므로, sql_validator에 전달하면 의미 검증의 정확도를 높일 수 있음 |
| **점검 포인트** | trace JSON의 `llm_calls[].response_text`에서 추출하여 실제 SQL과 explanation의 일관성 확인 |

---

### 2.5 `planner` — 초기 가설 및 실행계획 수립

**프롬프��**: `resources/prompts/reason/planner_system.txt`
**LLM 호출 코드**: `src/agents/nodes/reason/planner.py`

#### 2.5.1 `hypotheses[].description` / `strategy`

| 항목 | 내용 |
|:---|:---|
| **정의** | 초기 가설의 설명과 탐색 전략 |
| **저장 위치** | `Hypothesis.description`, `Hypothesis.strategy` (reason.hypotheses) |
| **소비 경로** | recovery_planner가 실패 시 dead_end로 기록 → 다음 replan 프롬프트의 `{failure_history}`에 포함 |
| **영향도** | **중간** — 초기 가설이 recovery에 도달하면 failure_history의 맥락 제공 |

#### 2.5.2 `execution_plan[].purpose`

| 항목 | 내용 |
|:---|:---|
| **정의** | 각 실행 스텝이 **왜 필요한지** 설명 |
| **예시** | "신규 계좌의 잔액과 개설일자를 확인하기 위한 메타 조회" |
| **저장 위치** | `ExecutionStep.purpose` |
| **소비 경로** | 로깅/tracker 전용 |
| **영향도** | **낮음 (진단용)** — trace에서 실행 계획의 의도를 파악할 때 유용 |

---

### 2.6 `result_finalizer` — 최종 출력 조립

**코드**: `src/agents/nodes/reason/result_finalizer.py` (line 105~164)

이 노드는 **LLM을 호출하지 않음**. 코드 로직으로 state의 정보를 조립하여 요약문을 생성.

#### 2.6.1 `exploration_summary` (성공 시)

| 항목 | 내용 |
|:---|:---|
| **생성 코드** | `_build_success_summary()` (line 105~139) |
| **내용 구성** | "도구 호출 N회, SQL 생성 N회 \| 재계획 N회 \| 사용 테이블: X, Y \| 참고 활용사례: N건" |
| **저장 위치** | `reason.exploration_summary` |
| **소비 경로** | 최종 사용자 응답에 포함 |

#### 2.6.2 `exploration_summary` (실패 시)

| 항목 | 내용 |
|:---|:---|
| **생성 코드** | `_build_failure_output()` (line 142~164) |
| **내용 구성** | dead_ends의 `de.reason` (recovery_planner가 추론한 실패 사유)을 나열 + 미해소 용어 목록 + 부분 SQL |
| **소비 경로** | 최종 사용자 응답에 포함 |
| **점검 포인트** | dead_ends의 reason이 사용자에게 이해 가능한 수준인지. `_infer_failure_reason()` (recovery_planner.py:161~189)이 생성하므로 해당 함수의 출력 품질이 중요 |

---

## 3. LLM→LLM 소비 체인 상세

파이프라인에서 **한 LLM의 출력이 다른 LLM의 입력으로 들어가는 경로**만 추출한 목록.

### 3.1 fix_instruction 체인 (유일한 직접 피드백 루프)

```
sql_validator LLM
  │
  ├─ response: { verdict: "FAIL", fix_instruction: "..." }
  │
  ▼ 파싱: sql_validator.py:394
  │
  ▼ 저장: reason.sql_fix_instruction (sql_validator.py:168)
  │
  ▼ 주입: sql_generator.py:239~242
  │    REASON_GENERATE_SQL_FIX.replace("{fix_instruction}", ...)
  │
  ▼ 프롬프트 교체: sql_generator.py:269 → "{fix_section}"
  │
  ▼ sql_generator LLM → 수정된 SQL 생성
```

### 3.2 insight → discovered_facts 체인

```
batch_interpret LLM (context_explorer 내부)
  │
  ├─ response: { interpretations: [{ insight: "..." }] }
  │
  ▼ 파싱: context_explorer.py:480~505
  │
  ▼ 저장: ExecutionStep.insight (context_explorer.py:564~567)
  │
  ▼ 조립: recovery_planner.py:126~131
  │    f"[{step.tool}] {step.insight}" for step in execution_plan
  │
  ▼ 주입: recovery_planner.py:228~230 → "{discovered_facts}"
  │
  ▼ recovery_planner LLM → lessons_learned + new_hypothesis 생성
```

### 3.3 evidence → unresolved_items 체인 (CONFLICTED 한정)

```
batch_interpret LLM
  │
  ├─ response: { knowledge_updates: [{ evidence: "...", new_status: "..." }] }
  │
  ▼ 파싱: context_explorer.py:487~494
  ���
  ▼ 저장: KnowledgeItem.evidence (reason.knowledge_items)
  │
  ▼ 조립: recovery_planner.py:145~153
  │    CONFLICTED 상태일 때 evidence 마지막 2건 포함
  │    f"{ki.key} (충돌: {'; '.join(ki.evidence[-2:])})"
  │
  ▼ 주입: recovery_planner.py:235~237 → "{unresolved_items}"
  │
  ▼ recovery_planner LLM → 충돌 해소 전략 수립
```

### 3.4 inferred fields → SQL 생성 컨텍스트 체인

```
batch_interpret LLM
  │
  ├─ response: { new_tables: [{ entity_scope, functional_usage, data_refresh_hint }] }
  │
  ▼ 파싱: context_explorer.py:496
  │
  ▼ 저장: CandidateTable.inferred_* (context_explorer.py:870~875)
  │
  ▼ 주입: sql_generator.py:87~92
  │    f"  엔티티: {ct.inferred_entity_scope} (LLM 추론)"
  │    f"  용도: {ct.inferred_functional_usage} (LLM 추론)"
  │    f"  갱신: {ct.inferred_data_refresh_hint} (LLM 추론)"
  │
  ▼ sql_generator LLM → 테이블 용도를 이해하고 SQL 생성
```

### 3.5 DeadEnd.reason → failure_history 체인

```
recovery_planner 코드 (LLM 아님)
  │
  ├─ _infer_failure_reason() (recovery_planner.py:161~189)
  │    sql_validation_result의 overall 상태에서 사유 추론
  │    또는 미해소 KnowledgeItem의 key 나열
  │
  ▼ 저장: DeadEnd.reason (recovery_planner.py:54)
  │
  ▼ 조립: recovery_planner.py:113~123
  │    failure_history 딕셔너리에 포함
  │
  ▼ 주입: recovery_planner.py:224~226 ��� "{failure_history}"
  │
  ▼ recovery_planner LLM → 실패 패턴 분석 및 새 가설 수립
  │
  ▼ 그리고: result_finalizer.py:148~151
  │    _build_failure_output() → 최종 사용자 응답에 포함
```

---

## 4. 미저장 필드 분석 및 개선 제안

### 4.1 `lessons_learned` (recovery_planner)

| 항목 | 현재 상태 | 개선 방향 |
|:---|:---|:---|
| **현상** | LLM이 생성하지만 `_parse_replan_response()`에서 파싱하지 않음 | `Hypothesis` 또는 `ReasoningState`에 필드 추가 |
| **기대 효과** | 다음 replan 사이클의 프롬프트에 `{previous_lessons}`로 주입하면 동일 실수 반복 방지 |
| **위험** | 프롬프트 길이 증가, 잘못된 교훈이 다음 판단을 오염시킬 수 있음 |
| **난이도** | 낮음 — 파싱 코드 3줄 + state 필드 1개 + 프롬프트 1줄 추가 |

### 4.2 `explanation` (sql_generator)

| 항목 | 현재 상태 | 개선 방향 |
|:---|:---|:---|
| **현상** | `_call_llm_for_sql()`에서 JSON의 `sql`만 추출하고 `explanation` 버림 | 반환값에 explanation 포함하여 state 저장 |
| **기대 효과** | sql_validator에 전달하면 "SQL이 의도한 바"와 "실제 SQL 동작"의 갭을 더 정확히 검증 가능 |
| **위험** | 낮음 |
| **난이도** | 낮음 — 반환 타입을 tuple로 변경 + state 필드 추가 |

### 4.3 `give_up.reason` (recovery_planner)

| 항목 | 현재 상태 | 개선 방향 |
|:---|:---|:---|
| **현상** | give_up=true일 때의 사유가 저장되지 않음 | result_finalizer의 실패 출력에 포함 |
| **기대 효과** | 사용자에게 "왜 답변을 드리지 못하는지" 더 구체적 설명 가능 |
| **난이도** | 낮음 |

---

## 5. 인사이트 유의미성 점검 체크리스트

trace 분석 시 아래 항목을 순서대로 점검합니다.

### 5.1 batch_interpret 품질 점검

- [ ] `insight`가 도구 결과(테이블 메타)의 핵심을 정확히 요약하는가?
- [ ] `evidence`가 confidence 수치와 일관적인가? (0.9인데 "가능성 있음" 같은 모호한 표현은 문제)
- [ ] `comparison_reason`이 selected/rejected 결과를 논리적으로 뒷받침하는가?
- [ ] `entity_scope`가 실제 테이블 도메인과 일치하는가?
- [ ] `functional_usage`가 질의 의도에 부합하는 용도를 기술하는가?
- [ ] `data_refresh_hint`가 날짜 필터링 전략에 올바른 방향을 제시하는가?

### 5.2 sql_validator 품질 점검

- [ ] `logical_consistency`의 판단이 도메인 지식에 기반하는가?
- [ ] FAIL 시 `fix_instruction`이 **구체적 수정 방향**을 포함하는가? (단순 지적 vs 수정 예시)
- [ ] DB 실행 오류 발생 시 fix_instruction에 **SQL 구문 수정 가이드**가 포함되는가?
- [ ] PASS인데 이후 DB 실행이 실패하는 경우 — validator가 놓친 검증 항목 파악

### 5.3 recovery_planner 품질 점검

- [ ] `lessons_learned`가 실패의 **근본 원인**을 정확히 파악하는가? (증상 vs 원인 구분)
- [ ] `new_hypothesis.description`이 이전 실패와 **명확히 다른** 접근인가?
- [ ] `missing_terms`가 ES 메타 검색에 **실효성 있는 키워드**인가?
- [ ] `strategy`가 이미 searched_queries에 있는 키워드를 반복하지 않는가?
- [ ] `give_up` 판단이 적절한가? (포기가 너무 이르거나 너무 늦은 경우)

### 5.4 전체 체인 정합성 점검

- [ ] batch_interpret의 insight가 recovery_planner의 discovered_facts에 정확히 반영되는가?
- [ ] fix_instruction이 sql_generator의 다음 SQL에 실제로 반영되는가? (같은 실수 반복 여부)
- [ ] inferred_entity_scope/functional_usage가 sql_generator 프롬프트에서 올바르게 활용되는가?
- [ ] CONFLICTED evidence가 recovery_planner의 unresolved_items에 포함되어 충돌 해소에 기여하는가?

---

## 부록: trace JSON에서 각 필드를 확인하는 방법

```python
import json

with open("logs/traces/trace_XXXX.json", encoding="utf-8") as f:
    data = json.load(f)

for llm in data["llm_calls"]:
    node = llm["node"]
    resp = json.loads(llm["response_text"])  # 파싱 가능 시

    if node == "reason_validate_sql":
        print("=== SQL Validator ===")
        print("verdict:", resp.get("verdict"))
        print("fix_instruction:", resp.get("fix_instruction"))
        for check_name, check in resp.get("checks", {}).items():
            if not check.get("pass"):
                print(f"  FAIL: {check_name} — {check.get('detail')}")

    elif node == "reason_recover":
        print("=== Recovery Planner ===")
        print("lessons_learned:", resp.get("lessons_learned"))
        print("give_up:", resp.get("give_up"))
        hyp = resp.get("new_hypothesis", {})
        print("new_hypothesis:", hyp.get("description"))
        print("strategy:", hyp.get("strategy"))
        print("missing_terms:", hyp.get("missing_terms"))

    elif "explore" in node or "batch" in node:
        print(f"=== {node} ===")
        for interp in resp.get("interpretations", []):
            print(f"  [{interp.get('tool_input')}] insight: {interp.get('insight')}")
            for ku in interp.get("knowledge_updates", []):
                print(f"    evidence: {ku.get('evidence')}")
        print("comparison_reason:", resp.get("comparison_reason"))
```

---

## 6. 심층 분석: 필드 중복, 소실, 통합 참조 필요성

trace 데이터와 코드를 교차 검증하여 도출한 구조적 분석.
(기준 trace: `session-1774704847175_20260328_232621`)

---

### 6.1 유사하거나 중복된 인사이트 필드

#### 6.1.1 실질적 중복: `Hypothesis.description` vs `Hypothesis.strategy`

가장 문제적인 중복. 두 필드의 실제 값을 비교하면:

```python
# recovery_planner.py 룰기반 fallback (line 322~327)
Hypothesis(
    description="보고서 SQL에서 유사 패턴 참조",        # "뭘 하겠다"
    strategy="보고서 SQL 검색으로 테이블/조인 구조 참고",  # "어떻게 하겠다"
)
```

실제 trace에서 LLM이 생성한 값도 마찬가지:
```
description: "신규는 OPEN_DT로 식별하고, 해당 계좌의 잔액을 유입 금액으로 대용"
strategy:    "계좌 개설일 기준 필터링 + 잔액 합산"
```

**둘 다 "접근 방식"을 설명하는데, description이 좀 더 길 뿐이다.**
결정적으로, `description`은 코드에서 **어디서도 프로그래밍적으로 읽히지 않는다**.
유일하게 소비되는 필드는 `strategy`뿐이다 (recovery_planner.py:393 — missing_terms가 없을 때 검색 키워드로 사용).
description은 사실상 write-only 필드.

| 관점 | 판정 |
|:---|:---|
| **중복 수준** | 의미적 70~80% 중복 |
| **유해성** | 유해 — 유지보수 부담, LLM 생성 시 토큰 낭비, 불일치 가능성 |
| **권장 조치** | description 제거하고 strategy만 유지. 진단용이 필요하면 별도 `rationale` 필드 신설 |

**단, 주의할 점**: 현재 `strategy`가 ES 검색 키워드로 직접 쓰이고 있어서,
만약 description의 장문 설명이 strategy에 합쳐지면 검색 품질이 떨어질 수 있다.
차라리 description을 없애고 strategy만 남기되, 별도 `rationale` 같은 진단 전용 필드를 두는 게 낫다.

---

#### 6.1.2 같은 LLM 호출에서 다른 스키마로 저장: `KnowledgeItem.value` vs `CandidateTable.inferred_*`

둘 다 **동일한 batch_interpret LLM 호출** (context_explorer.py:388~468)에서 생성된다:

```
# 같은 LLM 응답의 두 부분
knowledge_updates[].value → KnowledgeItem.value
  예: "DEP예금신규계좌잔액(T+0) (ACN, OPEN_DT, BLNG_BRCD, BAL_AMT)"

new_tables[].entity_scope → CandidateTable.inferred_entity_scope
  예: "DEP 예금 계좌 및 잔액 관련 데이터"
```

그리고 **sql_generator 프롬프트에 둘 다 주입된다**:
- `{confirmed_terms}` ← KnowledgeItem.value (sql_generator.py:193~197)
- `{tables}` ← CandidateTable.inferred_* (sql_generator.py:87~92)

sql_generator LLM은 같은 테이블에 대한 정보를 **두 번** 읽게 된다.

| 관점 | 판정 |
|:---|:---|
| **중복 수준** | 부분 중복 — 다른 스키마지만 원천 동일 |
| **유해성** | 중간 — 토큰 15~20% 낭비, 정보 불일치 시 LLM 혼란 가능 |
| **권장 조치** | sql_generator 프롬프트에서 `{confirmed_terms}` 중 `table:` 접두사 항목 제외 |

두 필드가 서로 다른 **관점**(confidence-weighted belief vs. structured metadata)을 제공하므로,
완전한 통합보다는 sql_generator 프롬프트 주입 시 중복 제거가 현실적이다.

---

#### 6.1.3 추상화 수준만 다른 유사 정보: `DeadEnd.reason` vs `sql_fix_instruction`

둘 다 "뭐가 잘못됐는지"를 설명한다:

```
sql_fix_instruction (전술적, 즉시 소비):
  "OPEN_DT는 date 타입인데 TO_CHAR 결과는 text. 타입 캐스팅 필요"

DeadEnd.reason (전략적, 재계획용):
  "DB 실행 오류"
```

소비 시점이 다르다(sql_fix_instruction은 같은 루프 내 sql_generator가 소비,
DeadEnd.reason은 recovery_planner가 소비). 의도적 설계이므로 중복 자체는 문제가 아니다.

**하지만 핵심 문제는** DeadEnd.reason이 `_infer_failure_reason()` (recovery_planner.py:161~189)에서
rule-based로 생성되면서 **fix_instruction에 있던 구체적 진단 정보가 전략 레벨로 올라갈 때 소실된다**는 것이다.
"DB 실행 오류"라는 4글자와 "date >= text 타입 불일치"라는 구체적 진단 사이의 정보 격차가 크다.

| 관점 | 판정 |
|:---|:---|
| **중복 수준** | 의도적 분리 — 전술 vs 전략 |
| **유해성** | 중복 자체는 무해하나, 전략 레벨에서 전술 정보가 소실되는 것이 유해 |
| **권장 조치** | DeadEnd.reason에 sql_fix_instruction의 요약을 포함시키는 방안 검토 |

---

#### 6.1.4 의도적 상호 보완: 중복이 아닌 경우

아래 두 쌍은 유사해 보이지만 실제로는 서로 다른 질문에 답하므로 중복이 아니다:

| 필드 쌍 | 차이 | 판정 |
|:---|:---|:---|
| `KnowledgeItem.evidence` vs `ExecutionStep.insight` | evidence는 "왜 이 지식을 믿는가" (누적 감사 추적), insight는 "이 스텝이 뭘 관찰했나" (단발 메모) | **상호 보완** |
| `comparison_reason` vs `rejected_tables` | comparison_reason은 "왜 거부했나" (설명), rejected_tables는 "뭘 거부했나" (구조화 목록) | **상호 보완** |

---

### 6.2 버려지는 인사이트 필드의 구체적 정보와 소실 영향

#### 6.2.1 `lessons_learned` — 가장 아까운 필드

**실제 trace에서 버려진 정보**:

```
[Cycle 2 - 버려짐]
"예금 계좌 잔액(TB_ADW_DEP201P)은 스냅샷 데이터로, 특정 기간 동안의
 '신규 유입'이라는 이벤트(Flow)를 파악하기에는 부적합합니다.
 신규 유입의 정확한 정의(OPEN_DT)와 거래금액 혹은 최초 입금액을
 나타내는 컬럼을 확인해야 합니다."

[Cycle 3 - 버려짐]
"잔액 테이블에서 OPEN_DT 필터링은 비즈니스적으로 정확하지 않고,
 DB 오류도 계속 발생. 타입 불일치 문제가 반복."
```

**대신 recovery_planner에 전달된 정보** (`_infer_failure_reason()` 결과):

```
"DB 실행 오류"    ← 이게 failure_history에 들어감
```

**정보 격차가 심각하다.** LLM이 "잔액 테이블은 스냅샷이라 Flow 파악에 부적합"이라는
도메인 수준의 진단을 내렸는데, 다음 사이클의 recovery_planner LLM은 그걸 모르고
"DB 실행 오류"라는 단서만으로 재계획을 세워야 한다.

**`lessons_learned`와 `new_hypothesis.description`은 질적으로 다른 정보다:**

| lessons_learned (진단) | description (처방) |
|:---|:---|
| "TB_FX_TRANSFER는 국내 외환 거래 전용으로 해외송금 데이터 없음" | "해외송금 전용 테이블(TB_REMITTANCE 등) 탐색" |
| "LOAN_DCD='05' 조합이 0건. 코드값이 틀렸을 가능성" | "기업대출 코드값 재확인 + 기업여신 전용 테이블 탐색" |
| "잔액 테이블은 스냅샷이라 Flow 분석에 부적합" | "거래 이력 테이블에서 신규 입금 레코드 탐색" |

진단 없이 처방만 전달되니, 다음 사이클의 LLM이 **같은 부류의 실수를 반복**한다.
이번 trace에서 3회 연속 `date >= text` 오류가 반복된 것이 정확히 이 문제다.

| 관점 | 분석 |
|:---|:---|
| **소실 코드 위치** | `_parse_replan_response()` (recovery_planner.py:276~305) — `data.get("give_up")`만 확인하고 `lessons_learned`는 추출하지 않음 |
| **소실 영향** | 다음 replan 사이클에서 동일 부류의 실패 반복. trace에서 실제로 3회 동일 타입 오류 발생 |
| **복구 난이도** | 낮음 — 파싱 코드 3줄 + state 필드 1개 + 프롬프트에 `{previous_lessons}` 1줄 추가 |

---

#### 6.2.2 `explanation` — 검증 정확도를 높일 수 있는 필드

**실제 trace에서 버려진 정보**:

```
"이번년도 신규 개설된 계좌의 잔액을 지점별로 합산하여 상위 10개 지점을 조회"
```

**sql_validator가 대신 하는 일**: SQL 자체에서 의도를 **역추론**해야 한다.
sql_validator_system.txt의 `logical_consistency` 체크가 이 역할인데,
SQL에서 의도를 추론하는 것은 본질적으로 불확실하다.

이번 trace의 실제 사례:
- 1차 검증: FAIL — "BAL_AMT는 잔액인데 유입금액이 아닐 수 있다" (역추론으로 문제 발견)
- 2차 검증: PASS — 동일 SQL인데 이번에는 통과 (역추론의 불안정성)

만약 explanation("잔액을 유입 금액으로 대용")이 validator에 전달됐다면,
"generator가 의도적으로 잔액=유입으로 대용한 것"이라는 맥락을 알고 더 정확한 판단을 내릴 수 있었다.

| 관점 | 분석 |
|:---|:---|
| **소실 코드 위치** | `_call_llm_for_sql()` (sql_generator.py:317~320) — `data.get("sql")`만 추출하고 `explanation` 미추출 |
| **소실 영향** | sql_validator의 logical_consistency 체크가 불안정 (같은 SQL에 FAIL/PASS 교대) |
| **복구 난이도** | 낮음 — 반환 타입 tuple 변경 + state 필드 추가 |

**주의: 확증 편향 위험**

explanation을 validator에 넘기면 validator가 generator의 설명에 설득당해
잘못된 SQL에도 PASS를 줄 수 있다. 이를 방지하려면 explanation을 "참고 정보"가 아닌
**"검증 대상"**으로 제시해야 한다.
즉 프롬프트에서 "이 설명이 SQL과 일치하는지도 검증하세요"라고 지시해야 한다.

---

#### 6.2.3 LLM이 생성한 `execution_plan` — 소실 영향은 제한적이나 잠재 가치 있음

recovery_planner LLM이 생성한 실행계획:
```json
[
  {"tool": "get_sample_data", "input": "TB_ADW_DEP201P, 5",
   "purpose": "OPEN_DT와 BAL_AMT의 실제 데이터 패턴 확인"},
  {"tool": "run_sql", "input": "SELECT ... WHERE OPEN_DT LIKE '2024%'",
   "purpose": "개설일 기준 필터링 후 합계 가능성 확인"}
]
```

코드가 대신 만든 실행계획 (`_build_replan_execution`, recovery_planner.py:355~408):
```python
# 항상 search_table_meta 또는 search_use_cases만 생성
steps.append(ExecutionStep(tool="search_table_meta", input=search_kw))
steps.append(ExecutionStep(tool="search_use_cases", input=hypothesis.strategy))
```

LLM은 `search_code_meta`, `get_sample_data`, `run_sql`까지 제안하는데,
코드는 **항상 2가지 도구만** 사용한다.

| 관점 | 분석 |
|:---|:---|
| **소실 코드 위치** | `_parse_replan_response()` (recovery_planner.py:276~305) — execution_plan 미추출 |
| **소실 영향** | 도구 다양성 부족 — 코드의 고정 2-스텝 계획이 모든 실패 유형에 동일하게 적용됨 |
| **복구 난이도** | 중간 — LLM 제안 도구 중 실제 존재하는 것만 필터링하는 로직 필요 |
| **주의** | LLM이 존재하지 않는 도구명을 hallucinate할 수 있으므로 화이트리스트 검증 필수 |

하이브리드 접근이 현실적: LLM이 제안한 도구 중 실제 존재하는 것만 채택, fallback은 현재 코드 로직 유지.

---

#### 6.2.4 `comparison_reason` — 로깅에만 쓰이고 재계획에 활용 안 됨

batch_interpret이 생성한 테이블 거부 사유:
```
"TB_ADW_DEP219M은 미수금, TB_ADW_DEP220M은 보증금 관리 테이블로
 신규 예금 유입 분석과 관계없음"
```

이 정보는 recovery_planner에 **전달되지 않는다**.
recovery_planner가 받는 건 `rejected_tables: ["TB_ADW_DEP219M", "TB_ADW_DEP220M"]`이라는 이름 목록뿐이다.

| 관점 | 분석 |
|:---|:---|
| **소실 코드 위치** | context_explorer.py:275, 289 — 로깅/tracker에만 기록 |
| **소실 영향** | recovery_planner가 "미수금/보증금 계열은 피해야 한다"는 걸 모르고 유사 테이블 재탐색 가능 |
| **복구 난이도** | 낮음 — DeadEnd 또는 rejected_tables 메타데이터에 reason 포함 |

---

### 6.3 인사이트 통합 참조의 필요성 분석

#### 6.3.1 현재 상태: 파편화된 인사이트 저장

```
KnowledgeItem.evidence     → recovery_planner에 CONFLICTED일 때만 일부 전달
ExecutionStep.insight       → recovery_planner의 discovered_facts로 전달
CandidateTable.inferred_*   → sql_generator에만 전달
comparison_reason           → 로깅에만 전달
fix_instruction             → sql_generator에만 전달
lessons_learned             → 어디에도 전달 안됨
explanation                 → 어디에도 전달 안됨
DeadEnd.reason              → recovery_planner에 전달 (단, 룰기반 요약)
```

**핵심 문제**: 각 노드가 **자기 직전 노드의 출력만 부분적으로 참조**한다.
파이프라인 전체를 관통하는 "이 질의에 대해 지금까지 뭘 알아냈고,
뭐가 안 됐고, 왜 안 됐는지"를 아는 노드가 **하나도 없다**.

---

#### 6.3.2 누가 통합 뷰를 가장 필요로 하는가

**recovery_planner가 가장 절실하다.**
현재 recovery_planner가 받는 정보:
- failure_history: 룰기반 4글자 사유 ("DB 실행 오류")
- discovered_facts: 도구 실행 결과 요약
- confirmed_knowledge: 확인된 지식
- unresolved_items: 미해소 항목

이것만으로 "왜 실패했고 다음에 뭘 해야 하는지" 판단하라고 하는 건,
의사에게 "환자가 아픕니다"라고만 알려주고 진단하라는 것과 비슷하다.

**sql_generator도 마찬가지다.**
현재 fix_section으로 "이전 SQL이 왜 틀렸는지"만 받는데,
"지금까지 시도한 모든 접근의 교훈"을 알면 처음부터 더 나은 SQL을 생성할 수 있다.

---

#### 6.3.3 별도 통합 분석 LLM 노드 vs 구조화된 누적 메모리

**별도 LLM 노드의 문제점:**

| 문제 | 설명 |
|:---|:---|
| 레이턴시 증가 | 매 사이클마다 추가 LLM 호출 (현재도 21회) |
| 토큰 비용 | 모든 인사이트를 한 번에 분석하면 입력 토큰 급증 |
| 정보 왜곡 | LLM이 요약하면서 중요한 디테일 소실 가능 |
| 위치 모호성 | 그래프의 어느 위치에 삽입해야 하는지 아키텍처적으로 불명확 |

**대안 제안: `ReasoningMemory` 구조**

LLM 노드를 추가하는 대신, State에 **구조화된 누적 메모리**를 두고
각 노드가 기록하며 필요한 노드가 참조하는 방식:

```python
class ReasoningMemory(BaseModel):
    """파이프라인 전체의 추론 기억."""

    # 실패 교훈 (lessons_learned 저장)
    failure_lessons: list[FailureLesson] = []
    # FailureLesson: cycle_id, hypothesis_id, lesson_text, failure_type

    # 테이블 판단 근거 (comparison_reason 저장)
    table_judgments: list[TableJudgment] = []
    # TableJudgment: table_name, verdict(selected/rejected), reason

    # SQL 의도 기록 (explanation 저장)
    sql_intents: list[SqlIntent] = []
    # SqlIntent: cycle_id, sql_hash, explanation, validation_result

    # 누적 도메인 통찰
    domain_insights: list[str] = []
    # "잔액 테이블은 스냅샷이므로 Flow 분석에 부적합"
```

이렇게 하면 **추가 LLM 호출 없이**, 각 노드가 이미 생성한 인사이트를 버리지 않고 저장하고,
recovery_planner와 sql_generator가 필요한 부분만 참조할 수 있다.

---

#### 6.3.4 통합의 위험과 대응

| 위험 | 설명 | 대응 |
|:---|:---|:---|
| **컨텍스트 오염** | 잘못된 교훈이 누적되면 이후 모든 판단을 오염. 예: "잔액 테이블은 유입 분석에 부적합"이 저장되면, 잔액 테이블이 정답인 다른 질의에서도 회피 가능 | 교훈의 scope를 **현재 세션/질의로 한정** (세션 간 전파 금지) |
| **프롬프트 길이 폭발** | 모든 인사이트를 모아서 주입하면 사이클 반복 시 프롬프트 길이 급증 | **최근 N건** 또는 **현재 가설과 관련된 것만** 필터링 |
| **확증 편향** | 이전 LLM의 판단이 다음 LLM의 판단을 과도하게 지배 | 인사이트를 "참고"가 아닌 **"검증 대상"**으로 프롬프트에 제시 |

---

#### 6.3.5 현실적 우선순위: 가장 임팩트가 큰 2개 체인 연결

모든 인사이트를 통합하는 것보다, **가장 임팩트가 큰 2개 체인만 먼저 연결**하는 게 효과적이다:

**우선순위 1: `lessons_learned` → 다음 recovery_planner 프롬프트**

```
변경 전:
  recovery_planner LLM → lessons_learned 생성 → 버려짐
  다음 사이클 → failure_history에 "DB 실행 오류"만 전달

변경 후:
  recovery_planner LLM → lessons_learned 생성 → ReasoningMemory.failure_lessons에 저장
  다음 사이클 → {previous_lessons}로 주입:
    "Cycle 2 교훈: 잔액 테이블은 스냅샷이라 Flow 분석에 부적합"
    "Cycle 3 교훈: OPEN_DT 필터링 시 date/text 타입 불일치 반복"
```

이것만으로 이번 trace의 "3회 동일 오류 반복" 문제의 상당 부분이 해소될 수 있다.

**우선순위 2: `comparison_reason` → DeadEnd에 포함**

```
변경 전:
  batch_interpret → comparison_reason 생성 → 로깅만
  recovery_planner → rejected_tables 이름 목록만 참조

변경 후:
  batch_interpret → comparison_reason → DeadEnd.rejection_reasons에 저장
  recovery_planner → failure_history에 "왜 거부됐는지" 포함:
    "TB_ADW_DEP219M: 미수금 관리 테이블로 신규 예금과 무관"
```

이렇게 하면 recovery_planner가 **같은 도메인(미수금, 보증금 등)의 테이블을 재탐색하는 것을 방지**할 수 있다.

---

#### 6.3.6 종합 판단

| 접근 | 적합도 | 이유 |
|:---|:---:|:---|
| 별도 통합 분석 LLM 노드 신설 | **낮음** | 레이턴시/비용 증가 대비 효과 불확실. 정보 왜곡 위험 |
| State에 `ReasoningMemory` 구조 추가 | **높음** | LLM 호출 없이 기존 인사이트 보존. 점진적 적용 가능 |
| 현재 구조에서 2개 체인만 연결 | **가장 높음** | 최소 변경으로 최대 효과. lessons_learned 저장 + comparison_reason 전달 |

**결론**: 통합 분석 LLM 노드는 과도하고, `ReasoningMemory` 구조가 아키텍처적으로 올바르며,
당장은 `lessons_learned`와 `comparison_reason` 2개 체인 연결만으로도
이번 trace에서 관찰된 핵심 문제(동일 오류 반복, 유사 테이블 재탐색)를 해소할 수 있다.
