# Trace 분석: Recovery Planner Replan Cycle

- **Trace ID**: `session-1774704847175_20260328_232621`
- **질의**: "이번년도 예금신규 유입 금액 기준 top 10 지점 알려줘"
- **최종 상태**: `error` (SQL 생성 실패)
- **소요 시간**: 87초 / LLM 21회 / 90,066 토큰
- **분석일**: 2026-03-28

---

## 1. 전체 실행 흐름

```
preprocess → resolve_history → classify_intent → normalize_query
→ reason_plan → [Cycle 1~4 반복] → reason_finalize (failure)
```

### Node Path (23 단계)
```
preprocess → resolve_history → classify_intent → normalize_query
→ reason_plan → reason_explore → reason_evaluate → reason_recover
→ reason_explore → reason_evaluate → reason_generate_sql → reason_validate_sql
→ reason_generate_sql → reason_validate_sql → reason_recover
→ reason_explore → reason_evaluate → reason_generate_sql → reason_validate_sql
→ reason_recover → reason_explore → reason_evaluate → reason_finalize
```

---

## 2. Recovery Cycle 상세 분석

### Cycle 1: 초기 탐색 → replan (confidence 68%)

| 단계 | 노드 | 내용 |
|:---:|:---|:---|
| 1 | `reason_plan` | TB_ADW_DEP201P(예금신규계좌잔액), TB_ADW_COM001M(공통관리점기본) 선택 |
| 2 | `reason_explore` | 12개 테이블 메타 조회 → LLM 비교 판단 |
| 3 | `reason_evaluate` | **knowledge=2/3, confidence=68% → `replan`** |
| 4 | `reason_recover` | PENDING 가설 남아있어 LLM 호출 없이 즉시 통과 (1ms) |

**핵심**: 정보 부족으로 추가 탐색 필요 판정. PENDING 가설이 있어 별도 replan LLM 호출 없이 다음 가설로 전환.

---

### Cycle 2: 추가 탐색 → SQL 생성 → 검증 실패 → recovery

| 단계 | 노드 | 내용 |
|:---:|:---|:---|
| 1 | `reason_explore` | 추가 메타 + 활용사례 검색 (embedding + reranker + use_cases) |
| 2 | `reason_evaluate` | **knowledge=4/5, confidence=75% → `generate_sql`** |
| 3 | `reason_generate_sql` (1차) | `SUM(BAL_AMT)` + `OPEN_DT >= TO_CHAR(...)` |
| 4 | `reason_validate_sql` (1차) | **FAIL** — "BAL_AMT는 현재 잔액이지 '신규 유입 금액'이 아닐 수 있다" |
| 5 | `reason_generate_sql` (2차) | fix_section 피드백 반영했으나 동일 패턴 `SUM(BAL_AMT)` |
| 6 | `reason_validate_sql` (2차) | **PASS** — 의미 검증 통과 |
| 7 | DB 실행 | **타입 오류**: `operator does not exist: date >= text` |
| 8 | `reason_recover` (2차) | LLM 호출로 실패 분석 (4.2초) |

**Recovery Planner LLM 판단 (2차)**:
```
lessons_learned:
  "잔액 테이블(TB_ADW_DEP201P)은 스냅샷 데이터로,
   특정 기간 동안의 '신규 유입'이라는 이벤트(Flow)를 파악하기에는 부적합.
   신규 유입의 정확한 정의(OPEN_DT)와 거래금액 혹은 최초 입금액을
   나타내는 컬럼을 확인해야 한다."

give_up: false

new_hypothesis (H_NEW_3):
  description: "신규는 OPEN_DT로 식별하고, 해당 계좌의 잔액(BAL_AMT)을 유입 금액으로 대용"
  missing_terms: ["예금신규의 정의(계좌개설일 기준)", "유입 금액의 정의(최초 잔액 vs 특정 기간 잔액 합계)"]
  priority: high
  strategy: "계좌 개설일 기준 필터링 + 잔액 합산"
```

---

### Cycle 3: 재탐색 → SQL 생성 → DB 오류 재발 → recovery

| 단계 | 노드 | 내용 |
|:---:|:---|:---|
| 1 | `reason_explore` | DEP201P의 OPEN_DT 활용 패턴 추가 탐색 |
| 2 | `reason_evaluate` | **knowledge=6/7, confidence=78% → `generate_sql`** |
| 3 | `reason_generate_sql` (3차) | 거의 동일한 SQL — **타입 캐스팅 미적용** |
| 4 | `reason_validate_sql` (3차) | **PASS** — 의미 검증만 수행 |
| 5 | DB 실행 | **동일 타입 오류 재발**: `date >= text` |
| 6 | `reason_recover` (3차) | LLM 호출로 재분석 (6.6초) |

**Recovery Planner LLM 판단 (3차)**:
```
lessons_learned:
  "잔액 테이블에서 OPEN_DT 필터링은 비즈니스적으로 정확하지 않고,
   DB 오류도 계속 발생. 타입 불일치 문제가 반복."

give_up: false

new_hypothesis (H_NEW_4):
  description: "잔액 테이블 대신 '신규 거래 입금' 이력 테이블(History)을 활용하여 유입 금액 산출"
  missing_terms: ["예금 신규 거래 식별 코드", "유입 금액 컬럼 명칭"]
  priority: high
  strategy: "잔액 테이블 대신 거래 내역 테이블(History)에서 신규 계좌 입금 레코드 탐색"
```

---

### Cycle 4 (최종): 탐색 후 가설 소진 → finalize (failure)

| 단계 | 노드 | 내용 |
|:---:|:---|:---|
| 1 | `reason_explore` | 거래 이력 테이블 탐색 — 적합한 테이블 미발견 |
| 2 | `reason_evaluate` | **knowledge=8/9, confidence=80% → `generate_sql`** |
| 3 | `reason_finalize` | 최종 실패 처리 |

---

## 3. Recovery Planner 전략 변화 흐름

```
[Cycle 1] 정보 부족 → PENDING 가설 소비 (LLM 미호출)
    ↓
[Cycle 2] DB 타입 오류 → "잔액≠유입금액" 교훈 → OPEN_DT 기반 대안
    ↓
[Cycle 3] 동일 DB 오류 재발 → "잔액 테이블 한계" 인지 → 거래이력 테이블로 전환
    ↓
[Cycle 4] 이력 테이블 미발견 → 가설 소진 → 최종 실패
```

| 회차 | 실패 유형 | failure_type | recovery 전략 | 탐색한 테이블 수 |
|:---:|:---:|:---:|:---|:---:|
| 1차 | 정보 부족 | `term_unresolvable` | PENDING 가설 소비 | 12 |
| 2차 | DB 타입 오류 | `db_error` | OPEN_DT 기반 대안 가설 생성 | +10 |
| 3차 | DB 타입 오류 | `db_error` | 거래이력 테이블로 전략 전환 | +7 |
| 4차 | 가설 소진 | - | finalize (failure) | +10 |

---

## 4. 핵심 문제점 및 개선 포인트

### 문제 1: DB 타입 오류가 도메인 오류로 오인됨
- **현상**: `date >= text` 타입 캐스팅 오류가 `db_error`로 분류
- **결과**: recovery_planner가 "비즈니스적 부적합"으로 해석 → 불필요한 테이블 교체 전략
- **개선안**: `_infer_failure_type()`에서 DB 에러 메시지를 파싱하여 `sql_syntax`/`type_mismatch` 등 세분류 → fix_section에 구체적 타입 캐스팅 가이드 전달

### 문제 2: sql_validator가 DB 실행 오류를 학습하지 못함
- **현상**: Validator는 의미적 검증만 수행하고 PASS → 이후 DB 실행에서 동일 오류 반복
- **결과**: 3회 모두 `OPEN_DT >= TO_CHAR(DATE_TRUNC(...), 'YYYYMMDD')` 패턴 반복
- **개선안**: DB 실행 오류 발생 시 sql_generator에게 전달하는 fix_section에 **구체적 SQL 수정 예시** 포함 (예: `TO_CHAR → TO_DATE` 또는 `CAST`)

### 문제 3: recovery_planner의 전략 전환이 과도함
- **현상**: 단순 타입 캐스팅 문제인데 테이블 자체를 교체하는 전략으로 escalation
- **결과**: 불필요한 탐색 39개 테이블, 87초 소요
- **개선안**: DB 오류는 SQL 수정 루프에서 해결하고, 비즈니스 로직 실패만 recovery_planner로 전달하는 라우팅 분리

---

## 5. AI 판단 필드 출처 매핑

Recovery Planner의 AI 판단은 아래 구조로 생성됩니다:

### 입력 (recovery_planner → LLM)
| 필드 | 출처 | 설명 |
|:---|:---|:---|
| `failure_history` | `ReasoningState.dead_ends` | 이전 가설별 실패 사유, 시도한 테이블, 거부된 테이블 |
| `discovered_facts` | `ExecutionStep.insight` (status=DONE) | context_explorer의 batch_interpret LLM이 생성한 도구 결과 해석 |
| `confirmed_knowledge` | `KnowledgeItem` (CONFIRMED/PROBABLE) | context_explorer가 누적한 확인된 지식 |
| `unresolved_items` | `KnowledgeItem` (UNRESOLVED/CONFLICTED) | 아직 미해소된 용어 목록 |
| `searched_queries` | `ReasoningState.searched_queries` | 이미 검색한 쿼리 (중복 방지) |

### 출력 (LLM → recovery_planner)
| 필드 | 소비처 | 설명 |
|:---|:---|:---|
| `lessons_learned` | 로깅만 (state 미저장) | 실패 교훈 — 다음 가설 생성의 맥락 |
| `give_up` | 가설 소진 판단 | true면 빈 리스트 반환 → DONE |
| `new_hypothesis.description` | `Hypothesis.description` | 새 접근 방식 설명 |
| `new_hypothesis.strategy` | `Hypothesis.strategy` | 구체적 탐색 전략 |
| `new_hypothesis.missing_terms` | `Hypothesis.missing_terms` → `ExecutionStep.input` | 검색 키워드로 사용 |

### 프롬프트 파일
- **Recovery 판단**: `resources/prompts/reason/recovery_planner_system.txt`
- **도구 결과 해석 (insight)**: `resources/prompts/reason/batch_interpret_system.txt`
- **테이블 비교/선택**: `resources/prompts/reason/context_explorer_system.txt`
- **초기 가설 생성**: `resources/prompts/reason/planner_system.txt`

---

## 부록: 생성된 SQL 변화

| 회차 | SQL (핵심 부분) | 결과 |
|:---:|:---|:---|
| 1차 | `SUM(A.BAL_AMT) ... WHERE A.OPEN_DT >= TO_CHAR(DATE_TRUNC('year', CURRENT_DATE), 'YYYYMMDD')` | Validator FAIL (의미적) |
| 2차 | `SUM(T1.BAL_AMT) ... WHERE T1.OPEN_DT >= TO_CHAR(DATE_TRUNC('year', CURRENT_DATE), 'YYYYMMDD')` | Validator PASS → DB 타입 오류 |
| 3차 | `SUM(T1.BAL_AMT) ... WHERE T1.OPEN_DT >= TO_CHAR(DATE_TRUNC('year', CURRENT_DATE), 'YYYYMMDD')` | Validator PASS → DB 타입 오류 (동일) |

> 3회 모두 `date >= text` 타입 불일치. `TO_CHAR()` 대신 `OPEN_DT::text >=` 또는 날짜 리터럴 비교가 필요했음.
