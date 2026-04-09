# Pipeline Trace: session-2048193847261

## 1. Executive Summary

**질의**: 올해 지점별 여신 연체율 분석해줘
**결과**: ✅ 성공 (1회 재탐색 후)
**소요**: 128.4s | LLM 12회, 62,480토큰

| 단계 | 결과 |
|------|------|
| 의도 분류 | DATA_ANALYSIS (95%) |
| 질문 정규화 | AGGREGATE [COMPARE] |
| 준비도 판정 | REPLAN → GENERATE (2차) |
| SQL | 2회 시도, 검증 통과, 실행 성공 (42건) |
| 분석 | 인사이트 3건, 시각화 BARCHART |

---

## 2. Reasoning Flow

> 총 소요 128.4s · LLM 12회 · 62,480tok · 경로: intent → normalize(×2) → prepare → retrieve → interpret → gate(REPLAN) → recovery → retrieve② → interpret② → gate(GENERATE) → generate → validate(PASS) → execute → analyze → format

---

### Phase 1: Interpret

#### [1] Intent Classification (12.8s, 4,820tok)

► **입력**
  query: "올해 지점별 여신 연체율 분석해줘"
  history: (없음)

◄ **LLM 판단**
  resolution: NEW (HIGH) — "독립 질의, 분석 요청"
  intent: DATA_ANALYSIS (0.95)
  → rewritten_analysis_query: "올해 지점별 여신 연체율 분석해줘" (시각화 지시어 없음, 원본 유지)

→ **normalize_query** — NEW + DATA_ANALYSIS

---

#### [2] Query Normalization (18.2s, Phase1 6,540tok + Phase2 7,120tok)

► **입력**
  raw_query: "올해 지점별 여신 연체율 분석해줘"

◄ **Phase 1 — 8-Slot 추출**

| Slot | 값 |
|------|---|
| intent | AGGREGATE [COMPARE] |
| entities | 여신→대출 (MEDIUM), 지점 (HIGH) |
| measures | 연체율 RATIO (LOW) ⚠ |
| time | THIS_YEAR (올해) |
| filters | (없음) |
| dimensions | 지점 GROUP INDIVIDUAL |
| modifiers | (없음) |
| output_hint | CHART [지점명, 연체율] |

  ⚠ **모호성 1건**:
  "연체율 산출 기준이 다음 중 어느 것인가요?"
  ① 연체금액 / 대출잔액 × 100  ② 연체건수 / 총건수 × 100
  → **자동추론(INFER): 연체금액 / 대출잔액 × 100** (은행 표준 기준)

◄ **Phase 2 — 교차 검증**
  rewritten: "올해 지점별 여신 연체율(연체금액÷대출잔액×100)을 산출하여 비교 분석한다"
  search_keywords:
    meta: [여신, 대출, 연체, 연체율, 연체금액, 대출잔액, 지점]
    vector: "올해 지점별 여신 연체율을 연체금액 대비 대출잔액으로 산출하여 분석"

→ **reasoning_preparer** — 모호성 1건 INFER 처리

---

### Phase 2: Reason — Round 0 (H_INIT)

> 가설: "유사 SQL + 테이블 메타 기반 초기 탐색"

#### [3] Reasoning Preparer (0.8ms, rule-based)

  query_decomposition:
    measures: [연체율 RATIO] · filters: [] · group_by: [지점] · order_limit: []
  knowledge_items:
    K1: measure:연체율 (UNRESOLVED, **critical**) — 산출식 필요
    K2: measure:연체금액 (UNRESOLVED) — 연체율 분자
    K3: measure:대출잔액 (UNRESOLVED) — 연체율 분모
  hypothesis: H_INIT
  execution_plan:
    Step 1: search_use_cases("올해 지점별 여신 연체율을 연체금액 대비 대출잔액으로 산출하여 조회한다")
    Step 2: search_table_meta("여신 대출 연체 연체금액 대출잔액 지점")

→ **context_retriever**

---

#### [4] Context Retriever — H_INIT (22.4s)

| Step | Tool | 결과 | 소요 |
|-----:|------|-----:|-----:|
| 1 | search_use_cases | 3건 | 18.2s |
| 2 | search_table_meta | 12건 | 210ms |

→ **context_interpreter**

---

#### [5] Context Interpretation (11.3s, 10,820tok)

► **입력**
  tool 결과 2건, unresolved: [K1:연체율, K2:연체금액, K3:대출잔액]

◄ **LLM 판단**
  **테이블 선정**:
    ✅ TB_ADW_LNB301M (여신기본) — 대출잔액(BAL_AMT) 보유, 지점코드(BLNG_BRCD) 보유
    ✅ TB_ADW_COM001M (부점정보) — 지점명(BR_NM)
    ❌ TB_ADW_LNB302M (여신실행이력) — 실행 이력, 잔액/연체 정보 없음
    ❌ TB_ADW_LNB501P (여신상환내역) — 상환 내역, 연체 직접 판단 불가
    ❌ 외 8건

  **지식 갱신**:
    K3: measure:대출잔액 → **CONFIRMED** (LNB301M.BAL_AMT)
    K1: measure:연체율 → **UNRESOLVED** ⚠ 산출식 미확인, 연체금액 컬럼 미발견
    K2: measure:연체금액 → **UNRESOLVED** ⚠ LNB301M에 연체금액 컬럼 없음

  **인사이트**:
    "LNB301M에 BAL_AMT(대출잔액)는 있으나 연체금액 컬럼이 없음.
     유사 SQL 3건 모두 단순 잔액 집계이며 연체율 산출 사례 없음.
     연체 관련 별도 테이블 탐색 필요."

→ **readiness_gate**

---

#### [6] Readiness Gate (rule-based)

| 항목 | 값 |
|------|---|
| readiness_score | 0.35 |
| knowledge | 1/3 CONFIRMED (33%) — K1:연체율 ✗, K2:연체금액 ✗, K3:대출잔액 ✓ |
| tables | 2건 SELECTED |
| pending_steps | 0 |
| replan_count | 0 |

  **verdict: REPLAN** (score 0.35 < threshold)
  → failure_type: TERM_UNRESOLVABLE
  → failure_reason: "핵심 측정값 '연체율' 산출식 미확인, 연체금액 컬럼 미발견"

→ **recovery_agent**

---

### ◆ Recovery — Round 1

#### [7] Recovery Agent (4.1s, 8,240tok)

► **진입 맥락**
  readiness_gate에서 진입: 초기 탐색이 불충분
  failure_type: TERM_UNRESOLVABLE
  failure_reason: "핵심 측정값 '연체율' 산출식 미확인, 연체금액 컬럼 미발견"

► **확인된 지식**
  [K3] measure:대출잔액 — CONFIRMED (BAL_AMT, ADWOWN.TB_ADW_LNB301M)

► **미해소 항목**
  [K1] measure:연체율 — UNRESOLVED
  [K2] measure:연체금액 — UNRESOLVED

► **도구 실행 이력**
  [스텝 1] ✓ search_use_cases("올해 지점별 여신 연체율을 연체금액 대비...")
    결과: 3건 | 관련: 관련 1건("지점별 대출잔액 상위 조회"), 비관련 2건
    발견: 연체율 산출 사례 없음, 잔액 집계만 존재
  [스텝 2] ✓ search_table_meta("여신 대출 연체 연체금액 대출잔액 지점")
    결과: 12건 | 관련: SELECTED 2건(LNB301M, COM001M), REJECTED 10건
    발견: LNB301M에 연체 관련 컬럼 없음

► **탐색된 테이블**
  - TB_ADW_LNB301M (SELECTED): 여신기본 — 대출잔액 보유
    컬럼: ACN, STD_DT, LN_DCD, BAL_AMT, BLNG_BRCD, PD_CD, LN_DT (+8)
  - TB_ADW_COM001M (SELECTED): 부점정보기본
    컬럼: BLNG_BRCD, BR_NM, RGN_CD, BIZ_DCD, USE_YN

► **이전 실패 기록**
  - [TERM_UNRESOLVABLE] 핵심 측정값 '연체율' 산출식 미확인, 연체금액 컬럼 미발견

► **샘플 데이터**
  - TB_ADW_LNB301M: 0행 (미조회)
  - TB_ADW_COM001M: 0행 (미조회)

◄ **LLM 판단**
  analysis: "LNB301M은 여신 기본 테이블로 대출잔액(BAL_AMT)은 있으나
            연체금액 컬럼이 없다. 연체 정보는 별도의 연체 관리 테이블에
            있을 가능성이 높다. '연체', '연체금액' 키워드로 추가 테이블 탐색 필요.
            또한 업무 매뉴얼에서 연체율 공식 산출식을 확인해야 한다."
  lessons: "여신기본 테이블에 연체 정보가 없음.
           연체는 별도 관리 테이블(연체원장 등)에서 관리될 수 있음"
  action: **replan**

  **새 가설**: H_R1 "연체 전용 테이블 탐색 + 산출식 확인"
    strategy: "연체 키워드로 테이블 재탐색, 업무 매뉴얼에서 산출식 확인"

  **실행 계획**:
    Step 1: search_table_meta("연체 연체금액 연체원장 여신연체, page=1")
    Step 2: search_manual("여신 연체율 산출식, page=1")
    Step 3: search_biz_terms("연체율, page=1")

→ **context_retriever** — replan, 가설 H_R1로 재탐색

---

### Phase 2: Reason — Round 1 (H_R1)

> 가설: "연체 전용 테이블 탐색 + 산출식 확인"

#### [8] Context Retriever — H_R1 (8.6s)

| Step | Tool | 결과 | 소요 |
|-----:|------|-----:|-----:|
| 1 | search_table_meta | 6건 | 150ms |
| 2 | search_manual | 2건 | 5.2s |
| 3 | search_biz_terms | 1건 | 3.1s |

→ **context_interpreter**

---

#### [9] Context Interpretation — Round 1 (9.8s, 9,640tok)

► **입력**
  tool 결과 3건, unresolved: [K1:연체율, K2:연체금액]

◄ **LLM 판단**
  **테이블 선정**:
    ✅ TB_ADW_LNB401P (여신연체현황) — **연체금액(OVDU_AMT) 발견**, 기준일자+계좌번호 구조
    ❌ TB_ADW_LNB402M (연체이력) — 이벤트 이력, 현재 시점 집계에 부적합
    ❌ 외 4건

  **지식 갱신**:
    K2: measure:연체금액 → **CONFIRMED** (LNB401P.OVDU_AMT)
    K1: measure:연체율 → **CONFIRMED**
      산출식: **연체금액(OVDU_AMT) / 대출잔액(BAL_AMT) × 100**
      근거: 업무 매뉴얼 "여신 연체율 = 연체원금 합계 ÷ 대출잔액 합계 × 100"
      추가 확인: 용어사전 "연체율: 총 대출잔액 대비 연체금액 비율(%)"

  **인사이트**:
    "LNB401P에서 OVDU_AMT(연체금액)을 확보.
     LNB301M.BAL_AMT(대출잔액)과 LNB401P.OVDU_AMT(연체금액)을
     ACN+STD_DT로 조인하여 지점별 SUM 후 비율 산출 가능.
     COM001M으로 지점명 조인."

→ **readiness_gate**

---

#### [10] Readiness Gate — Round 1 (rule-based)

| 항목 | 값 |
|------|---|
| readiness_score | 1.0 |
| knowledge | 3/3 CONFIRMED (100%) — K1:연체율 ✓, K2:연체금액 ✓, K3:대출잔액 ✓ |
| tables | 3건 SELECTED (LNB301M, LNB401P, COM001M) |
| pending_steps | 0 |
| replan_count | 1 |

  **verdict: GENERATE** (score 1.0)

→ **sql_generator**

---

#### [11] SQL Generation (2.8s, 5,640tok)

► **입력**
  tables:
    LNB301M (15 cols) — 대출잔액
    LNB401P (10 cols) — 연체금액
    COM001M (5 cols) — 지점명
  confirmed_terms:
    K1: 연체율 = OVDU_AMT / BAL_AMT × 100
    K2: 연체금액 = LNB401P.OVDU_AMT
    K3: 대출잔액 = LNB301M.BAL_AMT
  dead_ends:
    - [TERM_UNRESOLVABLE] LNB301M에 연체금액 컬럼 없음 (교훈: 연체는 LNB401P)
  failure_reason: (없음 — 첫 생성)

◄ **LLM 판단**
  status: **success**

```sql
SELECT C.BR_NM                                     AS 지점명,
       SUM(B.OVDU_AMT)                              AS 연체금액합계,
       SUM(A.BAL_AMT)                               AS 대출잔액합계,
       ROUND(SUM(B.OVDU_AMT) / NULLIF(SUM(A.BAL_AMT), 0) * 100, 2) AS 연체율
  FROM ADWOWN.TB_ADW_LNB301M A
  LEFT JOIN ADWOWN.TB_ADW_LNB401P B
    ON A.ACN = B.ACN AND A.STD_DT = B.STD_DT
  JOIN ADWOWN.TB_ADW_COM001M C
    ON A.BLNG_BRCD = C.BLNG_BRCD
 WHERE A.STD_DT = CURRENT_DATE
 GROUP BY C.BR_NM
 ORDER BY 연체율 DESC
 ```