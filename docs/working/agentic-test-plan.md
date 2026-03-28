# 에이전틱 코어 통합 E2E 테스트 계획서

> **테스트 대상**: Hybrid Pipeline with Agentic Core
> **데이터소스**: Docker (PostgreSQL biz_schema 22테이블 + ES 5인덱스 + Qdrant 2컬렉션 + MongoDB 5컬렉션)
> **LLM**: Groq llama-3.3-70b-versatile (OpenAI Compatible API)
> **총 테스트 케이스**: 165건 (15개 카테고리)
> **작성일**: 2026-03-24

---

## 1. 테스트 목적

에이전틱 코어 통합 후 다음을 검증한다:
1. **에이전트 상태 전이** — 7개 phase, 5개 verdict, 6개 validation outcome의 모든 분기가 의도대로 동작
2. **질의 난이도별 처리** — Easy/Medium/Hard 질의에 대한 SQL 생성 정확도
3. **사용자 인터랙션** — 명확화 질문, 멀티턴 대화, 세션/턴 관리
4. **예외 처리** — SQL 인젝션, PII 보호, 루프 가드, graceful 장애 대응
5. **데이터 불완전성 대응** — TYPE-2~4 불완전성에 대한 에이전트 행동

---

## 2. 테스트 환경

```
Docker Containers:
  dc-postgres       (5432)  — info_db(biz_schema 22테이블) + history_db
  dc-elasticsearch  (9200)  — table_meta(6605), column_meta(6033), code_meta(24), report_sql(150), term_dict(200)
  dc-qdrant         (6333)  — sql_history(10,000), biz_manual(N)
  dc-mongodb        (27017) — dpasset_table(572), dpasset_column(6033), standard_code(24), standard_code_value(194), glossary(20)
  dc-redis          (6379)  — 세션 캐시

LLM API:
  Provider: Groq (OpenAI Compatible)
  Model: llama-3.3-70b-versatile
  Base URL: https://api.groq.com/openai/v1

설정:
  USE_DUMMY=false
  agentic_core_enabled=true
  validate_layer2b_enabled=true
```

---

## 3. 테스트 카테고리 및 케이스 (165건)

### CAT-01: 단순 데이터 추출 (10건) — Easy

| ID | 질의 | 기대 테이블 | 기대 집계 | 검증 포인트 |
|---|---|---|---|---|
| E01-001 | 이번 달 신규 고객 수 알려줘 | CSC101M | COUNT | STD_DT 날짜 필터 |
| E01-002 | 현재 정상 계좌 수 | DEP201P | COUNT | ACT_STCD='01' 코드 |
| E01-003 | 담보대출 평균 금리 | LNB301M | AVG | LN_DCD='02' |
| E01-004 | 카드 유형별 발급 건수 | CRD401M | COUNT | CRD_DCD GROUP BY |
| E01-005 | 전체 여신 잔액 합계 | LNB301M | SUM | 단순 SUM |
| E01-006 | 지점 수 | COM001M | COUNT | 단순 카운트 |
| E01-007 | VIP 고객 수 | CSC101M | COUNT | CUS_GRD_CD='01' |
| E01-008 | 신용대출 건수 | LNB301M | COUNT | LN_DCD='01' |
| E01-009 | 정기예금 총 잔액 | DEP201P | SUM | ACT_DCD 코드 확인 |
| E01-010 | 이번 달 거래 건수 | TRX701L | COUNT | 파티션+날짜조건 |

**에이전트 기대 경로**: planner → [Fast-Path 가능] → sql_generator → validator → success
**상태 추적**: `fast_path_triggered`, `loop_guard.generate_attempts`, `validated_sql`

---

### CAT-02: 그룹별 집계 (10건) — Easy-Medium

| ID | 질의 | 기대 패턴 | 검증 포인트 |
|---|---|---|---|
| E02-001 | 고객등급별 고객 수 | COUNT + GROUP BY | CUS_GRD_CD |
| E02-002 | 대출유형별 건수와 총 잔액 | COUNT, SUM + GROUP BY | 복수 집계 |
| E02-003 | 지점별 고객 수 내림차순 | COUNT + GROUP BY + ORDER BY | 정렬 반영 |
| E02-004 | 상품별 예금 계좌 수와 총 잔액 | COUNT, SUM + GROUP BY | 수신 도메인 |
| E02-005 | 거래유형별 건수와 금액 | COUNT, SUM + GROUP BY | TRX 도메인 |
| E02-006 | 연령대별 고객 수 분포 | COUNT + GROUP BY | 연령 계산 |
| E02-007 | 지점별 대출 잔액 TOP 10 | SUM + GROUP BY + LIMIT | TOP-N |
| E02-008 | 펀드유형별 계좌 수와 평가액 | COUNT, SUM + GROUP BY | FND 도메인 |
| E02-009 | 채널별 거래 건수와 평균 금액 | COUNT, AVG + GROUP BY | CHN_CD |
| E02-010 | 보험유형별 건수와 보험료 합계 | COUNT, SUM + GROUP BY | INS 도메인 |

**에이전트 기대 경로**: planner → explore → evaluator(GENERATE) → sql_generator → validator → success
**상태 추적**: `knowledge_items`(코드값 해소), `query_decomposition.group_by`

---

### CAT-03: 다중 테이블 조인 (10건) — Medium

| ID | 질의 | 기대 테이블 | 조인 키 | 난이도 |
|---|---|---|---|---|
| E03-001 | 지점별 고객 수 + 지점명 | CSC101M + COM001M | BLNG_BRCD | medium |
| E03-002 | VIP 고객 예금 잔액 합계 | CSC101M + DEP201P | EDPS_CSN | medium |
| E03-003 | 고객별 대출 건수 + 고객명 | CSC101M + LNB301M | EDPS_CSN | medium |
| E03-004 | 지점별 대출유형별 현황 | LNB301M + COM001M | BLNG_BRCD | medium |
| E03-005 | 고객등급별 예금+대출 비교 | CSC101M + DEP201P + LNB301M | EDPS_CSN | hard |
| E03-006 | 서울 VIP 대출 현황 | COM001M + CSC101M + LNB301M | BLNG_BRCD, EDPS_CSN | hard |
| E03-007 | 카드 이용 TOP 10 + 고객정보 | CRD401M + CSC101M | EDPS_CSN | medium |
| E03-008 | 연체 고객 등급 분포 | LNB301M + CSC101M | EDPS_CSN | medium |
| E03-009 | 외환딜 유형별 통화별 | FXD501L + FXB502M | CCY_CD | medium |
| E03-010 | 퇴직연금 유형별 고객 현황 | PNB904P + CSC101M | EDPS_CSN | medium |

**에이전트 기대 경로**: planner → explore(다수 스텝) → evaluator → generate(JOIN) → validator(조인 경로 검증) → success
**상태 추적**: `confirmed_join_path`, `candidate_tables` 복수 확인, `structural_hints.join_patterns`

---

### CAT-04: 복합 조건 질의 (10건) — Medium-Hard

| ID | 질의 | 핵심 패턴 | 난이도 |
|---|---|---|---|
| E04-001 | 대출 잔액 1억+ 연체 중인 건 | WHERE + AND | medium |
| E04-002 | 거래 10건+ 계좌 | HAVING COUNT >= 10 | medium |
| E04-003 | 평균보다 높은 예금 계좌 | 서브쿼리 | medium |
| E04-004 | 잔액이 승인액 50%+ | 계산 필드 비교 | hard |
| E04-005 | 만기 30일 이내 정기예금 | 날짜 계산 | medium |
| E04-006 | 보통예금 5천만+ & 정기예금 없는 고객 | NOT EXISTS | hard |
| E04-007 | 예금만 있고 대출+카드 없는 고객 | LEFT JOIN + IS NULL | hard |
| E04-008 | 1년간 거래 없는 고객 | NOT EXISTS + 날짜 | hard |
| E04-009 | 50대+ & 퇴직연금 없는 고객 | 연령 계산 + NOT EXISTS | hard |
| E04-010 | 체크카드 50만+ & 신용카드 없는 고객 | 자기조인/서브쿼리 | hard |

**에이전트 기대 경로**: planner → explore(코드값 확인) → evaluator → generate → validator(Layer2a sanity) → success
**상태 추적**: `knowledge_items`(filter 해소율), `sql_fix_instruction` 재생성 시

---

### CAT-05: 금융 지표 산출 (10건) — Hard

| ID | 질의 | 산출식 | 매뉴얼 참조 |
|---|---|---|---|
| E05-001 | 연체율 추이 | 연체금액/총대출*100 | 연체관리 매뉴얼 |
| E05-002 | BIS비율 | 자기자본/위험가중자산*100 | BIS 매뉴얼 |
| E05-003 | NIM 추이 | (이자수익-비용)/운용자산 | 손익 매뉴얼 |
| E05-004 | 고객별 DSR | 원리금상환/소득*100 | 여신심사 매뉴얼 |
| E05-005 | LTV 분포 | 대출/담보가치*100 | 여신심사 매뉴얼 |
| E05-006 | 신용등급별 NPL | 부실채권/총대출 | 리스크 매뉴얼 |
| E05-007 | 지점별 수익성 지표 | 복합 | 재무 매뉴얼 |
| E05-008 | 고객등급별 가중평균금리 | SUM(잔액*금리)/SUM(잔액) | - |
| E05-009 | 채널별 활동성 지수 | 거래빈도/고객수 | - |
| E05-010 | 연체등급별 추이 | OVDU_GRD_CD별 월별 | 연체관리 매뉴얼 |

**에이전트 기대 경로**: planner → explore(매뉴얼+용어사전 검색) → evaluator → generate → validator → success
**상태 추적**: `knowledge_items`(glossary: 항목), `explored_use_cases`(유사 SQL 참조), `structural_hints`

---

### CAT-06: 모호한 질의 처리 (12건)

| ID | 질의 | 모호성 유형 | 기대 행동 |
|---|---|---|---|
| E06-001 | 잔액 알려줘 | 테이블 모호 | clarification |
| E06-002 | 고객 정보 뽑아줘 | 범위 모호 | clarification |
| E06-003 | VIP 현황 | 코드 모호 (CUS/MKT) | glossary 탐색 |
| E06-004 | 연체 현황 | 관점 모호 | clarification |
| E06-005 | 실적 좀 알려줘 | 도메인 모호 | clarification |
| E06-006 | 등급별로 분석 | 등급 유형 모호 | clarification |
| E06-007 | 지난달 것 뽑아줘 | 맥락 부재 | clarification |
| E06-008 | 금리 현황 | 도메인 모호 | clarification |
| E06-009 | 상위 고객 리스트 | 기준 모호 | clarification |
| E06-010 | 리스크 현황 | 리스크 유형 모호 | clarification |
| E06-011 | 최근 현황 | 시간+대상 모호 | clarification |
| E06-012 | 수수료 얼마야 | 수수료 유형 모호 | clarification |

**에이전트 기대 경로**: planner → explore → evaluator(CONFLICTED/REPLAN) → ask_user
**상태 추적**: `needs_user_input`, `user_question`(선택지 포함), `knowledge_items`(CONFLICTED 항목)

---

### CAT-07: 명확화 질문 상호작용 (10건)

사용자 모호 질의 → 에이전트 명확화 질문 → 사용자 응답 → SQL 생성 완료

| ID | 초기 질의 | 명확화 응답 | 기대 결과 |
|---|---|---|---|
| E07-001 | 잔액 알려줘 | 예금 잔액이요 | DEP201P SELECT |
| E07-002 | 잔액 알려줘 | 대출 잔액이요 | LNB301M SELECT |
| E07-003 | 등급별 분석 | 고객등급 기준 | CUS_GRD_CD GROUP BY |
| E07-004 | 등급별 분석 | 연체등급 기준 | OVDU_GRD_CD GROUP BY |
| E07-005 | 상태코드? | 계좌 상태코드 | ACT_STCD 코드값 |
| E07-006 | 현황 좀 | 지점별 대출 현황 | LNB301M + COM001M JOIN |
| E07-007 | 추이 분석 | 연체율 추이 | 월별 연체율 계산 |
| E07-008 | 고객 수 | 이번 달 신규 | 날짜 조건 추가 |
| E07-009 | 비율 알려줘 | 연체율 | 산출식 적용 |
| E07-010 | TOP 10 | 대출잔액 기준 지점 | ORDER BY + LIMIT |

**에이전트 기대 경로**: ask_user → (checkpointer 복원) → generate → validate → success
**상태 추적**: `clarification_question`(선택지 형태), `awaiting_clarification`, 재진입 후 `CONFLICTED→CONFIRMED` 전환

---

### CAT-08: 멀티턴 대화 이력 (12건)

| ID | Turn 1 | Turn 2 | 기대 | 난이도 |
|---|---|---|---|---|
| E08-001 | 지점별 고객 수 | 대출 건수로도 | 지점별 대출 건수 | medium |
| E08-002 | VIP 고객 수 | 연체 있는 사람? | VIP+연체 결합 | medium |
| E08-003 | 이번 달 신규 고객 | 지난 달은? | 날짜만 변경 | easy |
| E08-004 | 서울 지점 대출 | 부산도 같이 | 지역 확장 | medium |
| E08-005 | 등급별 고객 수 | 평균 잔액도 추가 | SELECT 확장 | medium |
| E08-006 | 대출유형별 건수 | 금액도 같이 | SUM 추가 | easy |
| E08-007 | 연체 현황 | 지점별로 정리 | GROUP BY 추가 | medium |
| E08-008 | 카드 이용 TOP10 | 이번 달만 | 날짜 필터 추가 | medium |
| E08-009 | 고객별 예금 총액 | 대출과 비교 | JOIN 추가 | hard |
| E08-010 | 정기예금 현황 | 만기 임박만 | 조건 추가 | medium |
| E08-011 | 대출잔액 TOP10 | 연체율도 | 계산 컬럼 추가 | hard |
| E08-012 | VIP 대출 현황 | 예금은? | 테이블 변경 | medium |

**에이전트 기대 경로**: conversation_history 전달 → planner(이전 맥락 활용) → generate
**상태 추적**: `conversation_history` 길이, `original_query`(합성 여부)

---

### CAT-09: Session/Turn 관리 (10건)

| ID | 시나리오 | 검증 포인트 |
|---|---|---|
| E09-001 | 새 세션 첫 질의 | dead_ends=[], knowledge_items=[] |
| E09-002 | 같은 세션 두 번째 독립 질의 | 이전 상태 미유출 |
| E09-003 | 명확화 재진입 (같은 turn) | knowledge_items 보존 |
| E09-004 | 명확화 2회 후 자동 진행 | clarification_turns >= 2 |
| E09-005 | 긴 대화 (20턴) | history 20건 전달 |
| E09-006 | /reset 후 질의 | 새 session_id |
| E09-007 | 동시 세션 격리 | 상태 교차 오염 없음 |
| E09-008 | Fast-Path 트리거 | fast_path_triggered=True |
| E09-009 | 루프 가드 한도 | total_tool_calls >= 20 |
| E09-010 | SQL 재생성 한도 | generate_attempts >= 4 |

---

### CAT-10: 대화 중 독립 질의 전환 (10건)

| ID | 대화 맥락 | 새 질의 | 기대 의도 | 기대 처리 |
|---|---|---|---|---|
| E10-001 | 대출 분석 중 | 오늘 날씨 어때? | casual_talk | 에이전틱 미진입 |
| E10-002 | 고객 수 조회 중 | 테이블 구조 알려줘 | meta_question | 메타 질의 |
| E10-003 | VIP 분석 중 | 대출유형별 건수 | data_extraction | 새 독립 질의 |
| E10-004 | 연체율 분석 중 | 고마워 | casual_talk | 인사 응답 |
| E10-005 | 지점 현황 중 | 카드 이용 현황도 | data_extraction | 새 질의 |
| E10-006 | 예금 분석 중 | 아까 대출 건수 다시 | data_extraction | CONTINUE |
| E10-007 | 첫 질의 | 안녕하세요 | casual_talk | 인사 |
| E10-008 | 대출 결과 직후 | 엑셀로 뽑을 수 있어? | meta_question | 메타 |
| E10-009 | VIP 목록 결과 | 서울만 필터링 | data_extraction | CONTINUE |
| E10-010 | 없음 | 이 시스템 뭐 해? | meta_question | 안내 |

---

### CAT-11: 예외 처리 및 보안 (13건)

| ID | 입력 | 기대 차단 | 레이어 |
|---|---|---|---|
| E11-001 | SQL 인젝션 (;DROP TABLE) | 차단 | preprocess/validator |
| E11-002 | 주민번호 전체 추출 | PII 거부 | validator |
| E11-003 | DELETE FROM | DML 차단 | validator L1 |
| E11-004 | pg_catalog 접근 | 시스템 카탈로그 차단 | validator L1 |
| E11-005 | 빈 입력 | 처리 | preprocess |
| E11-006 | 1글자 (A) | 처리 | preprocess |
| E11-007 | 1글자 한글 (가) | 처리 | preprocess |
| E11-008 | 계좌번호 전체 목록 | PII 마스킹 | validator |
| E11-009 | UPDATE 구문 | DML 차단 | validator L1 |
| E11-010 | 프롬프트 인젝션 | 차단 | preprocess |
| E11-011 | information_schema | 시스템 카탈로그 | validator L1 |
| E11-012 | 비밀번호 요청 | PII 차단 | validator |
| E11-013 | 다중 쿼리 (;) | 차단 | validator L1 |

---

### CAT-12: 에이전틱 루프 분기 검증 (15건)

모든 confidence_evaluator 판정 + validator 실패 유형 + recovery_planner 분기를 검증.

| ID | 시나리오 | 기대 판정 | 기대 다음 노드 |
|---|---|---|---|
| E12-001 | 탐색 스텝 남음 | EXPLORE | context_explorer |
| E12-002 | 고확신도 | GENERATE | sql_generator |
| E12-003 | 저확신도 + 스텝 소진 | REPLAN | recovery_planner |
| E12-004 | CONFLICTED 존재 | ASK_USER | result_finalizer |
| E12-005 | tool_calls >= 20 | TERMINATE | result_finalizer |
| E12-006 | replan >= 3 | TERMINATE | result_finalizer |
| E12-007 | Fast-Path 성공 | - | result_finalizer(success) |
| E12-008 | Fast-Path 실패 (C-24) | - | context_explorer |
| E12-009 | FAIL_SYNTAX | - | sql_generator |
| E12-010 | FAIL_SEMANTIC_LOCAL | - | sql_generator |
| E12-011 | FAIL_STRUCTURAL | - | recovery_planner |
| E12-012 | local_fix 격상 | - | recovery_planner |
| E12-013 | FAIL_EMPTY | - | recovery_planner |
| E12-014 | 가설 전환 | - | context_explorer |
| E12-015 | fallback 가설 생성 | - | context_explorer |

---

### CAT-13: 코드값/메타 불완전성 대응 (10건)

| ID | 질의 | 불완전성 유형 | 기대 대응 |
|---|---|---|---|
| E13-001 | CNH 통화 거래 | TYPE-2 (미정의 코드) | 코드 미발견 → 탐색 확장 |
| E13-002 | E유형 보험 건수 | TYPE-2 | CONFLICTED → 사용자 확인 |
| E13-003 | HYB 연금 가입자 | TYPE-2 | 미정의 → 대안 탐색 |
| E13-004 | 테이블 설명 부실 | TYPE-3 (메타 품질) | 보고서SQL/매뉴얼 참조 |
| E13-005 | 가입일 기준 조회 | TYPE-4 (REG_DT vs JOIN_DT) | 컬럼 확인 |
| E13-006 | 잔액 합계 | TYPE-4 (BAL_AMT vs TOT_BAL_AMT) | 용어 해소 |
| E13-007 | 고객 구분별 | TYPE-4 (DCD vs GRD_CD) | 코드 식별 |
| E13-008 | T+0 예금 잔액 | 데이터 시점 차이 | 스냅샷 테이블 선택 |
| E13-009 | 지점코드 001 | 특수 지점 | 매뉴얼 참조 |
| E13-010 | IFRS9 Stage별 | RSK_STAGE_CD | 코드값 매핑 |

---

### CAT-14: 유사 테이블 구분 (10건)

| ID | 질의 | 후보 테이블들 | 올바른 선택 |
|---|---|---|---|
| E14-001 | 고객 기본 정보 | CSC101M / CSP103M / CSC102H | CSC101M |
| E14-002 | 고객 프로필 | CSC101M / CSP103M | CSP103M |
| E14-003 | 여신 기본 정보 | LNB301M / LNB302M | LNB301M |
| E14-004 | 담보/LTV 정보 | LNB301M / LNB302M | LNB302M |
| E14-005 | 예금 현재 잔액 | DEP201P / DEP202S | DEP201P |
| E14-006 | 예금 잔액 통계 | DEP201P / DEP202S | DEP202S |
| E14-007 | 펀드 계좌 현황 | FND601P / FND602P | FND601P |
| E14-008 | 펀드 수익률 | FND601P / FND602P | FND602P |
| E14-009 | 거래 내역 (일별) | TRX701L / TRX703M | TRX701L |
| E14-010 | 월간 거래 통계 | TRX701L / TRX703M | TRX703M |

---

### CAT-15: 분석 및 시각화 (10건)

| ID | 질의 | 기대 의도 | 기대 차트 |
|---|---|---|---|
| E15-001 | 등급별 분포 분석 | data_analysis | pie/donut |
| E15-002 | 연체율 추이 분석 | data_analysis | line |
| E15-003 | 지점별 대출잔액 비교 | data_analysis | bar |
| E15-004 | 연령대별 고객수+잔액 상관 | data_analysis | scatter |
| E15-005 | 대출유형별 비중 | data_analysis | pie |
| E15-006 | 채널별 거래 추이 그래프 | data_analysis | line |
| E15-007 | 등급별 잔액 히스토그램 | data_analysis | histogram |
| E15-008 | 여신 vs 수신 비교 | data_analysis | grouped_bar |
| E15-009 | 카드유형별 구성비 | data_analysis | donut |
| E15-010 | 월별 신규고객 트렌드 | data_analysis | line |

---

## 4. 테스트 실행 방법

```bash
# 기본 결함 검증 (Dummy 데이터, 빠름)
pytest tests/test_agentic_core.py tests/test_agentic_e2e.py -v

# 흐름 추적 (Dummy 데이터)
pytest tests/test_agentic_flow_trace.py -v -s

# 실제 Docker 데이터소스 E2E
pytest tests/test_agentic_real_e2e.py -v -s

# 전체 카탈로그 기반 E2E (추후 구현)
pytest tests/test_agentic_catalog_e2e.py -v -s --catalog tests/test_cases/agentic_e2e_test_catalog.json
```

---

## 5. 상태 추적 검증 항목

각 테스트에서 다음 AgenticCoreState 필드를 추적하여 보고서에 기록:

| 필드 | 추적 목적 |
|---|---|
| `phase` | 노드 전환 정상 여부 |
| `hypotheses` | 가설 수립/전환/소진 |
| `knowledge_items` | 지식 축적 진행률 (UNRESOLVED→CONFIRMED) |
| `candidate_tables` | 후보 테이블 발견 수 |
| `confirmed_join_path` | 조인 경로 확인 여부 |
| `structural_hints` | sqlglot 힌트 추출 여부 |
| `loop_guard` | 루프 카운터 적정성 |
| `dead_ends` | 실패 경로 학습 |
| `fast_path_triggered` | Fast-Path 발동 여부 |
| `sql_validation_result` | 3-레이어 검증 결과 |
| `needs_user_input` | 명확화 필요 여부 |
| `final_status` | 최종 성공/실패 |

---

## 6. 평가 지표

| 지표 | 기준 | 산출 방법 |
|---|---|---|
| SQL 생성 성공률 | >= 70% (Easy), >= 50% (Medium), >= 30% (Hard) | validated_sql 존재 비율 |
| 테이블 선택 정확도 | >= 80% | expected_tables와 실제 매칭 비율 |
| 명확화 정확도 | >= 90% | 모호 질의에 적절한 질문 생성 비율 |
| 보안 차단율 | 100% | SQL 인젝션/PII 차단 비율 |
| 평균 레이턴시 | <= 30초 (Easy), <= 60초 (Medium), <= 120초 (Hard) | 전체 파이프라인 실행 시간 |
| Fast-Path 활용률 | >= 20% (Easy 질의) | fast_path_triggered 비율 |
| 루프 효율성 | 평균 tool_calls <= 10 | loop_guard.total_tool_calls |

---

## 7. 테스트 데이터 파일

- **테스트 카탈로그 JSON**: [tests/test_cases/agentic_e2e_test_catalog.json](../../tests/test_cases/agentic_e2e_test_catalog.json)
- **골든 쿼리 (30건)**: [resources/evaluation/golden_queries.json](../../resources/evaluation/golden_queries.json)
- **표준 테스트 (90건)**: [resources/evaluation/test_queries.json](../../resources/evaluation/test_queries.json)
- **실사용 쿼리 (20건)**: [tests/fixtures/real_queries.json](../../tests/fixtures/real_queries.json)
