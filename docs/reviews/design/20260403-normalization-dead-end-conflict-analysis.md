# 정규화 ↔ Dead_End 상충 사례 분석

> 작성일: 2026-04-03
> 관련 코드: `recovery_agent.py`, `reasoning_preparer.py`, `sql_validator.py`

## 1. 문제 정의

에이전틱 루프에서 recovery_agent가 실패 경로를 `dead_ends`에 기록할 때,
그 피드백이 **최초 정규화(normalized_query)와 근본적으로 상충**하는 경우가 존재한다.

현재 구조에서는 이를 처리할 메커니즘이 없다:

- `normalized_query` → `query_decomposition`은 reasoning_preparer에서 **한 번 세팅되면 불변**
- recovery_agent는 `execution_plan`만 새로 만들 수 있고, decomposition은 수정 불가
- recovery_agent 프롬프트에 `normalized_query` 자체가 전달되지 않음

결과적으로 정규화가 잘못된 경우, recovery가 아무리 새 사실을 발견해도
**"잘못된 프레임 안에서 더 열심히 찾는"** 것만 가능하다.


## 2. 사례 분석

### 사례 1: intent 오분류 — "추이"가 누락된 EXTRACT

**사용자 질의:** "지점별 여신잔액 추이 보여줘"

**정규화 결과:**
```
intent: DATA_EXTRACTION
measures: [여신잔액(NONE)]
dimensions: [지점(GROUP)]
time_range: (비어있음)          ← 문제
modifiers: []
```

**파이프라인 경과:**
1. reasoning_preparer: knowledge_items에 `measure:여신잔액`만 등록. 시간 차원 탐색 없음
2. context_retriever: TB_LOAN_BAL 테이블 발견, 잔액 컬럼 확인
3. sql_generator: `SELECT BRANCH_CD, BAL_AMT FROM TB_LOAN_BAL` — 단건 스냅샷
4. sql_validator L2b: 원본 질의 "추이"와 대조 → "시계열 GROUP BY 없음"

**Dead_End:**
```
failure_type: SQL_STRUCTURAL (escalate)
reason: "질의에 '추이'가 있으나 시계열 차원(GROUP BY 년월 등) 없음"
lessons_learned: "time dimension이 필요했으나 탐색 자체가 없었음"
```

**상충 지점:**
- `query_decomposition.group_by`에 시간 차원이 없고, `dimensions`에도 없음
- execution_plan을 바꿔서 해결할 문제가 아니라 **정규화의 dimensions 슬롯에 시간 차원을 추가해야** 하는 문제
- recovery_agent는 `search_table_meta`로 날짜 컬럼을 더 찾는 것밖에 할 수 없고, `query_decomposition.group_by`를 수정할 수 없음

**상충 슬롯:** dimensions, time_range
**recovery 복구 가능 여부:** 불가

---

### 사례 2: measure가 산출식인데 단일 컬럼으로 정규화됨

**사용자 질의:** "부서별 연체율 현황"

**정규화 결과:**
```
measures: [연체율(NONE)]          ← "연체율"이라는 컬럼은 없음
dimensions: [부서(GROUP)]
```

**파이프라인 경과:**
1. context_retriever: `search_table_meta("연체율")` → 테이블 메타에 "연체율" 컬럼 없음
2. context_interpreter: `measure:연체율` UNRESOLVED 유지
3. readiness_gate: UNRESOLVED 남아있음 → recovery 진입
4. recovery_agent: `search_manual("연체율 산출식")` 계획 수립
5. 2회차 탐색: 매뉴얼에서 "연체율 = 연체금액합계 / 대출잔액합계 × 100" 발견
6. context_interpreter: `measure:연체율` → CONFIRMED, value="연체금액합/대출잔액합×100"

**Dead_End (1회차):**
```
failure_type: TERM_UNRESOLVABLE
reason: "연체율은 단일 컬럼이 아님. 산출식(연체금액/대출잔액×100)으로 
         2개 테이블 JOIN + 계산식이 필요"
lessons_learned: "연체율 같은 금융 지표는 컬럼 직접 매핑이 아닌 산출식 탐색이 필요"
```

**상충 지점:**
- 정규화의 `measures: [연체율(NONE)]`은 "연체율이라는 컬럼을 SELECT하라"는 의미
- 실제로는 `SUM(연체금액)/SUM(대출잔액)*100`이라는 **계산 표현식**
- 이걸 알았어도 `query_decomposition.measures`는 `[{"term": "연체율", "agg_function": "NONE"}]`으로 고정
- CONFIRMED된 knowledge_item에 산출식이 있어도 decomposition과 모순
- sql_generator LLM이 knowledge_item의 산출식을 보고 우회할 수는 있으나, **decomposition과 knowledge가 상충하는 상태에서 LLM이 둘 중 뭘 따를지는 보장 없음** (폐쇄망 모델에서 특히 위험)

**상충 슬롯:** measures
**recovery 복구 가능 여부:** 부분적 (knowledge에 산출식은 있으나 decomposition과 모순)

---

### 사례 3: filter 값이 시스템 코드와 불일치

**사용자 질의:** "작년 VIP고객 이탈 현황"

**정규화 결과:**
```
filters: [고객등급="VIP"]
time_range: [작년]
measures: [이탈건수(COUNT)]
```

**파이프라인 경과:**
1. context_retriever: `search_code_meta("CUS_GRD_CD")`
2. code_meta 결과: `{"01": "일반", "02": "우수", "03": "프리미엄"}` — "VIP" 없음
3. readiness_gate: filter:고객등급=VIP UNRESOLVED → recovery 진입

**Dead_End:**
```
failure_type: TERM_UNRESOLVABLE
reason: "CUS_GRD_CD에 'VIP' 코드 없음. 
         '02(우수)' 또는 '03(프리미엄)'이 의도된 값일 수 있음"
lessons_learned: "사용자의 일상어 'VIP'는 시스템 코드와 1:1 매핑되지 않음"
```

**상충 지점:**
- `query_decomposition.filters`에 `{"term": "고객등급", "value": "VIP"}`가 고정
- recovery_agent가 코드값 `"02"` 또는 `"03"`을 찾았어도:
  - `filter:고객등급=VIP`라는 knowledge_item 키 자체가 잘못됨
  - sql_generator가 이 filter를 `WHERE CUS_GRD_CD = 'VIP'`로 그대로 변환할 위험
  - "VIP가 02인지 03인지 아니면 둘 다인지"는 **정규화 수준의 결정** — 사용자 명확화가 필요할 수 있음
- recovery_agent가 이 모호성을 감지해도 **사용자에게 명확화 질문을 트리거할 경로가 없음** (recovery → context_retriever 루프만 가능)

**상충 슬롯:** filters
**recovery 복구 가능 여부:** 부분적 (code_map에 정보는 있으나 어떤 코드인지 결정 불가)

---

### 사례 4: 집계 단위와 테이블 granularity 불일치 (silent failure)

**사용자 질의:** "고객별 평균 거래금액 상위 10명"

**정규화 결과:**
```
dimensions: [고객(GROUP)]
measures: [거래금액(AVG)]
modifiers: [TOP(10)]
```

**파이프라인 경과:**
1. TB_TXN_DAILY (일별 거래 집계 테이블) 발견
2. sql_generator: `SELECT CUST_ID, AVG(TXN_AMT) FROM TB_TXN_DAILY GROUP BY CUST_ID ORDER BY 2 DESC LIMIT 10`
3. sql_validator L3: 실행 성공, 결과 있음 → **PASS**
4. 하지만 결과가 "건당 평균"이 아니라 "일평균" — TB_TXN_DAILY는 이미 일 단위 SUM된 테이블

**Dead_End:**
```
(생성되지 않음 — SQL이 PASS했으므로)
```

**상충 지점:**
- **이 사례가 가장 위험하다.** validator가 "결과가 있고, 구문도 맞고, 의미도 표면적으로 맞다"고 판정하면 dead_end가 안 쌓이고 사용자에게 **틀린 답**이 전달됨
- 정규화의 `measures: [거래금액(AVG)]`는 사용자 의도와 일치하지만, TB_TXN_DAILY의 granularity(일 집계)와 충돌
- **테이블 선택 단계에서 granularity를 검증하는 로직이 없기 때문**에 발생

**상충 슬롯:** measures × 테이블 granularity
**recovery 복구 가능 여부:** 불가 (dead_end 자체가 안 생김)

---

### 사례 5: 여러 entity가 하나로 합쳐짐

**사용자 질의:** "수신 계좌와 여신 계좌의 잔액 비교"

**정규화 결과:**
```
entities: [계좌잔액]               ← "수신"과 "여신"이 구분 안 됨
measures: [잔액(NONE)]
modifiers: [COMPARISON]
```

**파이프라인 경과:**
1. context_retriever: "계좌잔액"으로 검색 → TB_ACCOUNT_BAL 하나만 발견
2. sql_generator: 단일 테이블에서 잔액 조회, 비교 불가
3. sql_validator L2b: "COMPARISON modifier가 있으나 비교 대상이 1개뿐"

**Dead_End:**
```
failure_type: SQL_STRUCTURAL
reason: "비교 요청이나 비교 대상이 단일 테이블. 
         수신(TB_DEPOSIT_BAL)과 여신(TB_LOAN_BAL) 두 테이블이 필요"
lessons_learned: "'수신 계좌와 여신 계좌'는 서로 다른 테이블에 존재하는 
                  별개 entity로 분리 탐색해야 함"
```

**상충 지점:**
- 정규화가 `entities: [계좌잔액]`으로 하나로 합쳤는데, 실제로는:
  - entities: [수신계좌잔액, 여신계좌잔액] (2개)
  - measures: [수신잔액(SUM), 여신잔액(SUM)] (2개)
- 이렇게 되어야 각각 다른 테이블을 탐색하고 JOIN/UNION으로 비교하는 SQL이 나옴
- recovery_agent가 이걸 알아도 `query_decomposition.required_concepts`를 수정할 수 없음

**상충 슬롯:** entities
**recovery 복구 가능 여부:** 불가 (탐색 방향 자체가 단일 entity로 고정)


## 3. 종합 비교

| 사례 | 상충 슬롯 | recovery 복구 | 위험도 | 비고 |
|------|-----------|:---:|:---:|------|
| 1. 추이 누락 | dimensions, time_range | 불가 | 높음 | group_by 자체가 없음 |
| 2. 산출식 | measures | 부분적 | 중간 | knowledge에 산출식은 있으나 decomposition과 모순 |
| 3. VIP 코드 | filters | 부분적 | 중간 | code_map에 정보는 있으나 어떤 코드인지 결정 불가 |
| 4. granularity | measures × 테이블 | 불가 | **최고** | dead_end 자체가 안 생김 (silent failure) |
| 5. entity 병합 | entities | 불가 | 높음 | 탐색 방향 자체가 단일 entity로 고정 |


## 4. 구조적 원인 분석

### 4-1. recovery_agent 프롬프트에 normalized_query 미전달

`_build_prompt()`는 다음만 전달한다:
- confirmed_knowledge, unresolved_items
- candidate_tables_summary
- dead_ends_summary
- exploration_history, discovered_facts

**normalized_query도, query_decomposition도 전달되지 않음.**
recovery LLM은 정규화가 뭐였는지 모른 채 plan만 세운다.

### 4-2. query_decomposition 불변

`reasoning_preparer_node()`에서 한 번 세팅된 후 어떤 노드도 수정하지 않는다:
```python
decomposition = _build_decomposition_from_normalized(nq)
reason.query_decomposition = decomposition
```

### 4-3. recovery → clarification 경로 부재

recovery_agent가 "사용자에게 물어봐야 한다"고 판단해도,
현재 루프에서는 `recovery → context_retriever` 경로만 존재한다.
`pending_signals`에 AmbiguitySignal을 추가하는 경로가 recovery에 없다.

### 4-4. validator의 granularity 검증 부재

sql_validator L2b는 query_decomposition 체크리스트와 원본 질의를 대조하지만,
**"이 테이블이 raw 데이터인지 집계 데이터인지"** 같은 granularity 검증은 수행하지 않는다.
이로 인해 사례 4 같은 silent failure가 발생한다.


## 5. 개선 방향 (검토 필요)

### 방향 A: targeted normalization patch

recovery에서 정규화를 전면 재실행하지 않고, 특정 슬롯만 수정하는 방식.

```
recovery 진입 시:
  ├─ SQL 레벨 가설 변경으로 해결 가능? → 기존대로
  └─ decomposition과 상충하는 dead_end 감지?
       └─ normalization patch 트리거
            ├─ 변경 대상 슬롯 식별 (intent / dimensions / measures / filters / entities)
            ├─ 해당 슬롯만 수정 (전면 재정규화 X)
            ├─ 변경된 슬롯에 영향받는 컨텍스트만 재수집
            └─ patch 횟수 제한 (최대 1~2회)
```

장점: 기존 파이프라인 구조 유지, 변경 범위 최소
단점: patch 판단 로직의 정확도에 의존

### 방향 B: 정규화 자체 강화 (upstream fix)

정규화 단계에서 상충 사례를 사전 방지:
- "추이/변화/트렌드" → 자동으로 time_range + dimensions에 시간 차원 추가
- 금융 지표 사전: "연체율", "BIS비율" 등 → 산출식 태그 부착
- entity 분리 규칙: "A와 B의 비교" 패턴 감지 → entity 2개로 분리

장점: 근본 원인 해결
단점: 정규화 모델 복잡도 증가, 폐쇄망 모델에서의 안정성 미지수

### 방향 C: granularity 검증 레이어 추가

테이블 선택 후 SQL 생성 전에 "테이블 granularity ↔ 사용자 의도 집계 단위" 정합성 검사.

장점: silent failure(사례 4) 방어
단점: 테이블별 granularity 메타가 필요 (현재 미보유)

### 방향 D: recovery → clarification 경로 추가

recovery_agent가 "정규화 수준의 모호성"을 감지하면 사용자에게 명확화 질문을 트리거.

장점: 사례 3(VIP 코드) 같은 근본적 모호성 해소
단점: 사용자 경험 측면에서 recovery 중 갑작스러운 질문이 될 수 있음
