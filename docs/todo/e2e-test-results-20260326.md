# E2E 테스트 결과 상세 분석 — 2026-03-26

> **테스트 환경**: Docker (PostgreSQL, MongoDB, Qdrant, ES, Neo4j, Redis) + Groq API (llama-3.3-70b-versatile)
> **USE_DUMMY**: false (실제 데이터소스)
> **테스트 수**: 11건 (보고서 생성 1건 제외)
> **통과율**: 3/11 (27%) — 단, Rate Limit으로 무효 결과 포함

---

## 1. 테스트별 결과 상세

### A-01: "안녕하세요" — PASS

```
경로: preprocess → history_resolve → intent(CASUAL_TALK) → clarify → END
LLM 호출: 2회 | 소요: 4초
```

- 의도 분류 정확 (CASUAL_TALK, confidence=0.95)
- SQL 생성 안 함 (정상)
- 적절한 안내 메시지 반환
- **문제 없음**

---

### A-02: "TB_LOAN_INFO 테이블에 어떤 컬럼이 있어?" — PASS

```
경로: preprocess → history_resolve → intent(META_QUESTION) → clarify → END
LLM 호출: 2회 | 소요: 2초
```

- 메타 질문 분류 정확 (META_QUESTION)
- SQL 생성 안 함 (정상)
- **문제 없음**

---

### B-01: "전체 고객 수 알려줘" — FAIL (SQL 미생성)

```
경로: preprocess → intent(DATA_EXTRACTION) → normalize(실패) → planner → explorer → evaluator(REPLAN) × 3 → TERMINATE
LLM 호출: 11회 | 소요: 114초
```

**구간별 추적**:

| 구간 | 결과 | 문제점 |
|------|------|--------|
| 의도 분류 | DATA_EXTRACTION ✅ | 정상 |
| 정규화 | **실패** ❌ | filter[0].target 필드 누락 → Pydantic 검증 실패 → fallback |
| planner 초기 컨텍스트 | MongoDB 0건, Qdrant 50건 | MongoDB "전체 고객 수" 키워드 매칭 실패 → **후보 테이블 0건** |
| explorer 1차 | search_table_meta "고객 마스터" → 1건 | TB_CUST_INFO 1건 발견했으나 knowledge 미확정 |
| evaluator 1차 | readiness=0.45 → **REPLAN** | knowledge 0/0 → 확신도 부족 |
| replan 1차 | search_use_cases → 50건 | 활용사례만 추가, knowledge 미확정 |
| evaluator 2차 | readiness=0.2 → **REPLAN** | knowledge 0/2 |
| replan 2차 | search_table_meta "고객 관련 테이블" → 10건 | 거래/자산 테이블 10건 추가 발견 |
| 테이블 비교 판정 | **전체 rejected** ❌ | LLM이 "고객 수를 알려주는 적합 테이블 없음"으로 판정 |
| evaluator 3차 | readiness=0.2 → **conclude_failure** | 종료 |

**근본 원인**:

1. **정규화 Pydantic 검증 실패** (P1)
   - LLM이 `filter.target` 필드를 빠뜨림
   - 위치: `src/services/query_normalizer.py` → NormalizedQuery 모델 검증
   - 조치: filter 모델에서 `target` 필드에 기본값 부여하거나, 파싱 시 빈 문자열로 보정

2. **MongoDB 초기 검색 0건** (P1)
   - "전체 고객 수"로 텍스트 검색 시 TB_CUST_INFO가 매칭되지 않음
   - MongoDB의 `dpasset_table` 컬렉션에 "고객" 키워드가 `alt_name` 또는 `desc`에 포함되어 있지 않거나,
     텍스트 인덱스가 한글 형태소 분석 없이 전문 매칭만 수행
   - 조치: MongoDB 시딩 데이터에 "고객", "고객정보", "고객수" 등 키워드 보강 또는 검색 로직 개선

3. **테이블 비교 판정에서 유효 테이블까지 rejected** (P2)
   - explorer가 TB_CUST_INFO를 발견했으나, 후속 비교에서 거래 테이블 10건과 함께 전체 rejected
   - 비교 LLM이 10+1건을 한번에 비교하면서 TB_CUST_INFO도 함께 탈락시킨 것으로 추정
   - 조치: planner 단계에서 이미 발견한 테이블에 "보호" 가중치 부여, 또는 비교 그룹에서 분리

---

### B-02: "이번 달 신규 여신 건수" — FAIL (SQL 미생성)

```
경로: preprocess → intent(DATA_EXTRACTION) → normalize → planner → explorer → evaluator(REPLAN) × 3 → TERMINATE
LLM 호출: 9회 | 소요: 137초
```

**구간별 추적**:

| 구간 | 결과 | 문제점 |
|------|------|--------|
| 의도 분류 | DATA_EXTRACTION ✅ | 정상 |
| planner 초기 컨텍스트 | MongoDB 0건 | "여신" 키워드 매칭 실패 → **후보 테이블 0건** |
| explorer | search_use_cases만 반복 실행 | search_table_meta 호출 부족 |
| evaluator | REPLAN × 3 → conclude_failure | knowledge 미확정 |

**근본 원인**:

1. **MongoDB 검색에서 "여신" 키워드 미매칭** (P1) — B-01과 동일 패턴
2. **planner가 search_table_meta 대신 search_use_cases만 계획** (P2)
   - 후보 테이블 0건 상태에서 활용사례만 반복 검색
   - 조치: replan 시 search_table_meta를 우선 계획하도록 recovery_planner 프롬프트 개선

---

### B-03: "고객별 대출 잔액 합계" — FAIL (SQL 미생성)

```
경로: preprocess → intent(DATA_EXTRACTION) → normalize → planner → explorer → evaluator(REPLAN) × 3 → TERMINATE
LLM 호출: 7회 | 소요: 120초
```

**구간별 추적**:

| 구간 | 결과 | 문제점 |
|------|------|--------|
| search_table_meta × 3 | **전부 0건** ❌ | "대출", "잔액", "여신" 키워드 모두 매칭 실패 |
| search_use_cases × 2 | 50건씩 반환 | 활용사례는 있으나 테이블 확정 못함 |
| evaluator | REPLAN × 3 → conclude_failure | knowledge 0건 |

**근본 원인**: **MongoDB 텍스트 검색 전면 실패** — 핵심 문제 동일

---

### B-04: "지점별 수신 잔액 현황" — FAIL (ASK_USER → 종료)

```
경로: preprocess → intent(DATA_EXTRACTION) → normalize → planner → explorer → evaluator(ASK_USER) → finalize
LLM 호출: 3회 | 소요: 46초
```

**구간별 추적**:

| 구간 | 결과 | 문제점 |
|------|------|--------|
| planner | 가설 생성 + 초기 컨텍스트 수집 | 정상 |
| evaluator | **ASK_USER** | CONFLICTED 항목 발생 → 사용자 확인 요청으로 분기 |

**근본 원인**:

- KnowledgeItem 중 CONFLICTED 상태 항목이 있어 evaluator가 ASK_USER로 판정
- 테스트에서 사용자 응답을 주지 않으므로 종료
- 이 동작 자체는 **설계 의도대로** (모호한 상황에서 사용자 확인)
- 조치: 테스트에서 멀티턴 시뮬레이션 추가 또는, CONFLICTED 상태 발생 원인 분석

---

### D-02: "1억 이상 대출 보유 고객 수" — FAIL (SQL 미생성)

```
경로: preprocess → intent(DATA_EXTRACTION) → normalize → planner → evaluator(REPLAN) × 3 → TERMINATE
LLM 호출: 1회(의도분류만) + 도구만 실행 | 소요: 4초 (초기) + 재시도
```

**근본 원인**: B-01~B-03과 동일 (MongoDB 검색 실패 → 후보 테이블 0건 → REPLAN 반복)

---

### E-01: "고객 이름과 전화번호 목록" — FAIL (무효, Rate Limit 추정)

```
LLM 호출: 0회 | 소요: 0.9초
intent: (no decision)
```

**원인**: Groq Rate Limit으로 LLM 호출 자체가 실패한 것으로 추정.
intent_classifier가 호출되기 전에 에러가 발생하여 전체 파이프라인이 조기 종료.
**이 결과는 무효** — Rate Limit 해소 후 재실행 필요.

---

### E-02: "전체 거래 내역 다 뽑아줘" — FAIL (무효, Rate Limit 추정)

E-01과 동일 패턴. **무효 결과**.

---

### E-03: "외환 파생상품 거래 현황" — PASS

```
LLM 호출: 0회 | 소요: 0.9초
```

- 존재하지 않는 도메인 → 적절히 실패 처리
- **주의**: LLM 0회는 Rate Limit일 수 있음. 실패 처리가 Rate Limit 때문인지 실제 graceful 처리인지 재확인 필요.

---

### F-01: "지점별 여신 잔액 비교 분석해줘" — FAIL (무효, Rate Limit 추정)

E-01과 동일 패턴. **무효 결과**.

---

## 2. 문제 분류 및 우선순위

### P1 — 즉시 수정 필요

#### 이슈 1: MongoDB 텍스트 검색 전면 실패

- **현상**: "고객", "여신", "대출", "잔액", "수신" 등 핵심 금융 키워드로 search_table_meta 호출 시 0건 반환
- **영향**: 후보 테이블 0건 → planner/explorer 가 아무리 돌아도 SQL 생성 불가
- **발생 테스트**: B-01, B-02, B-03, D-02 (4건)
- **원인 추정**:
  - MongoDB `dpasset_table` 컬렉션의 텍스트 인덱스가 `name` 필드(영문 테이블명)에만 설정
  - `alt_name`(한글명)이나 `desc`(설명)에 텍스트 인덱스가 없거나 한글 형태소 분석 미지원
  - 또는 시딩된 데이터 자체가 한글 키워드를 포함하지 않음
- **조치 방향**:
  1. MongoDB 시딩 데이터 확인: `alt_name`, `desc` 필드에 한글 키워드 포함 여부
  2. 텍스트 인덱스 확인: `alt_name`, `desc`에 텍스트 인덱스 설정 여부
  3. `search_table_meta` 검색 쿼리 확인: `$text` 검색인지 `$regex` 검색인지
  4. 한글 형태소 분석 불가 시 → `$regex` 또는 `$or` 기반 부분 매칭으로 전환 검토
- **관련 파일**:
  - `src/connectors/impl/mongo_connector.py` — search_table_meta() 구현
  - `resources/connectors/mongo/init_mongodb.js` — 인덱스 설정
  - 시딩 스크립트 (MongoDB 데이터)

#### 이슈 2: 정규화 Pydantic 검증 실패

- **현상**: LLM이 filter.target 필드를 누락 → NormalizedQuery 검증 실패 → fallback (빈 query_decomposition)
- **영향**: SQL Generator가 measures/filters/group_by 정보 없이 원문만으로 SQL 생성 시도
- **발생 테스트**: B-01 (직접 확인), 다른 테스트에서도 동일 패턴 가능
- **원인**: LLM(llama-3.3-70b)이 filter 슬롯의 필수 필드를 빠뜨림
- **조치 방향**:
  1. `NormalizedQuery.filters[].target` 필드에 기본값(`""`) 부여
  2. 파싱 시 누락 필드 보정 로직 추가 (빈 문자열로 채우기)
  3. 프롬프트에 filter 슬롯의 필수 필드를 더 명확히 지시
- **관련 파일**:
  - `src/services/query_normalizer.py` — 파싱 로직
  - `src/agents/models/normalization.py` — NormalizedQuery Pydantic 모델
  - `resources/prompts/interpret/query_normalizer_phase1_system.txt` — 프롬프트

---

### P2 — 개선 필요

#### 이슈 3: replan 시 search_use_cases만 반복, search_table_meta 부족

- **현상**: 재계획 시 recovery_planner가 search_use_cases를 반복 계획하고 search_table_meta를 충분히 계획하지 않음
- **영향**: 후보 테이블 없이 활용사례만 쌓여서 readiness가 올라가지 않음
- **발생 테스트**: B-01, B-02, B-03
- **조치 방향**:
  1. recovery_planner 프롬프트에 "후보 테이블 0건이면 search_table_meta를 최우선으로 계획" 지시 추가
  2. 또는 rule-based: candidate_tables가 비어있으면 search_table_meta를 강제 삽입
- **관련 파일**:
  - `src/agents/nodes/reason/recovery_planner.py`
  - `resources/prompts/reason/recovery_planner_system.txt`

#### 이슈 4: 테이블 비교 판정에서 기존 후보까지 rejected

- **현상**: planner/explorer에서 발견한 유효 테이블(TB_CUST_INFO)이 후속 비교 판정에서 무관한 테이블 그룹과 함께 전체 rejected
- **영향**: 유효 후보 테이블이 최종 탈락하여 SQL 생성 불가
- **발생 테스트**: B-01
- **조치 방향**:
  1. 비교 그룹 구성 시 "다른 도메인 테이블"은 비교 대상에서 제외 (entity_scope가 다르면 비교하지 않음)
  2. 또는 비교 LLM에 "질의와 무관한 테이블만 rejected하고, 부분적으로라도 관련된 테이블은 유지" 지시
- **관련 파일**:
  - `src/agents/nodes/reason/context_explorer.py` — `_find_comparison_groups()`, `_run_table_comparison()`
  - `resources/prompts/reason/table_comparison_system.txt`

---

### P3 — 확인 후 조치

#### 이슈 5: Groq Rate Limit으로 4건 무효

- **현상**: E-01, E-02, F-01 테스트에서 LLM 호출 0건, intent 미분류 → Rate Limit으로 추정
- **영향**: 해당 테스트 결과 무효
- **조치**: Rate Limit 해소 후 재실행, 또는 Anthropic API로 전환하여 재실행
- **대안**: 테스트 간 딜레이를 현재 3초에서 10~15초로 증가

#### 이슈 6: CONFLICTED 상태 발생 원인 분석 필요

- **현상**: B-04에서 evaluator가 ASK_USER 판정 (CONFLICTED 항목 존재)
- **영향**: 테스트가 멀티턴 시뮬레이션 없이 종료
- **조치**:
  1. CONFLICTED 항목이 무엇인지 트레이스에서 확인 (knowledge_items 상태)
  2. 충돌 해소 로직 또는 기본 선택 로직 검토
  3. 테스트에 멀티턴 시뮬레이션 추가 (clarification_state 주입)

---

## 3. 요약: 파이프라인 구간별 건강도

| 파이프라인 구간 | 상태 | 설명 |
|----------------|------|------|
| 전처리 (preprocess) | 🟢 정상 | 모든 테스트에서 정상 동작 |
| 이력 해석 (history_resolve) | 🟢 정상 | 모든 테스트에서 정상 통과 |
| 의도 분류 (intent_classifier) | 🟢 정상 | Rate Limit 아닌 케이스에서 100% 정확 |
| 질의 정규화 (normalizer) | 🟡 부분 실패 | filter.target 누락 시 fallback (이슈 2) |
| planner 초기 컨텍스트 | 🔴 실패 | MongoDB 검색 0건 (이슈 1) |
| explorer 도구 실행 | 🟡 부분 동작 | search_table_meta 일부 성공, search_use_cases 정상 |
| evaluator 확신도 | 🟢 정상 | 점수/verdict 로깅 정상, 판정 로직 적절 |
| 테이블 비교 판정 | 🟡 과도한 rejection | 유효 테이블까지 rejected (이슈 4) |
| SQL 생성 | ⚪ 미도달 | 후보 테이블 부족으로 도달 못함 |
| SQL 검증 | ⚪ 미도달 | SQL 미생성으로 미도달 |
| SQL 실행 | ⚪ 미도달 | SQL 미생성으로 미도달 |
| 분석/포맷팅 | ⚪ 미도달 | SQL 미실행으로 미도달 |

---

## 4. 다음 조치 순서 (권장)

1. **MongoDB 검색 문제 해결** (이슈 1) — 이것만 해결되면 B-01~B-04, D-02 모두 진행 가능
2. **정규화 파싱 방어** (이슈 2) — query_decomposition 품질 확보
3. **Rate Limit 대응** (이슈 5) — 테스트 재실행하여 E-01, E-02, F-01 유효 결과 확보
4. **replan 전략 개선** (이슈 3) — 재계획 시 search_table_meta 우선
5. **비교 판정 과도 rejection 방지** (이슈 4) — 비교 그룹 구성 로직 개선
