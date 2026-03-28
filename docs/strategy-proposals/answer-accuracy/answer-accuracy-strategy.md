# 질의 답변 정확도·성능 개선 전략 종합

> 프로젝트 전체 코드베이스·문서·테스트 분석 기반 (2026-03-20)
> 대상: src/, src/agents/nodes/prompts/, evaluation/, docs/, scripts/, tests/ 전체 130+ 파일

---

## 1. 입력 전처리 및 보안 방어

### 1.1 유니코드 정규화 (NFKC)
- **위치**: `src/utils/security.py` → `normalize_unicode()`
- **전략**: 전각 문자(ｓｅｌｅｃｔ)를 반각 ASCII로 변환, 제어 문자(U+0000~U+001F) 제거
- **효과**: 동형 문자(homograph) 기반 SQL 인젝션 우회 차단

### 1.2 SQL 인젝션 다층 방어 (13개 패턴)
- **위치**: `src/agents/nodes/interpret/preprocessor.py` → `_COMPILED_SUSPICIOUS`
- **전략**: 세미콜론 연쇄 DML, SQL 주석(--/\*/\*), UNION SELECT, 시간지연 함수(SLEEP/PG_SLEEP), 파일 I/O(LOAD_FILE), 시스템 카탈로그(pg\_\*, information_schema), 확장 프로시저(xp\_\*) 등
- **특이점**: 유니코드 정규화 *후* 패턴 검사 → 전각 우회 사전 차단

### 1.3 프롬프트 인젝션 탐지 (40+ 패턴)
- **위치**: `src/utils/security.py` → `detect_prompt_injection()`
- **전략**: 영어("ignore instructions", "jailbreak"), 한국어("지시 무시", "관리자 모드"), 간접 주입(JSON/XML 이스케이프, \[INST\] 태그) 2단계 탐지 (원본 + 정규화 텍스트)

### 1.4 PII 마스킹
- **위치**: `src/utils/security.py` → `mask_pii()`
- **전략**: 주민번호·카드번호·계좌번호는 직접 노출 금지, 전화번호·이메일은 앞2+뒤2 유지 패턴 마스킹
- **적용**: 로그 기록 전, 사용자 응답 전 이중 적용

### 1.5 입력 길이 제한
- **위치**: `src/agents/nodes/interpret/preprocessor.py` → `MAX_INPUT_LENGTH = 500`
- **전략**: 과도한 입력 차단, 명확화 합성 시에도 1,000자 제한

---

## 2. 의도 분류 (Intent Classification)

### 2.1 Few-shot 기반 분류 + 신뢰도
- **위치**: `src/agents/nodes/prompts/system_prompts.py` → `INTENT_CLASSIFICATION`
- **전략**:
  - 4개 의도 분류 (data_extraction, data_analysis, clarification_needed, general_question)
  - **신호어 기반 판별**: 각 의도별 판별 키워드 명시 (~건수/~금액 → 추출, 분석해줘/추이 → 분석)
  - 6개 few-shot 예제로 경계 케이스 커버 (모호한 입력 "데이터 좀 뽑아줘" → clarification)
  - 0.0~1.0 신뢰도(confidence) 반환

### 2.2 소형 LLM 대응 포맷 재시도
- **위치**: `src/utils/llm/retry.py` → `llm_call_with_parse_retry()`
- **전략**:
  - LLM 응답 포맷 불일치 시 최대 N회 재시도 (기본 2회)
  - 이전 실패 응답 + 포맷 교정 힌트를 대화 이력으로 추가하여 자기 수정 유도
  - 최종 실패 시 `ParseError`에 마지막 응답 첨부 → 호출자가 폴백 처리
- **효과**: GPT-3.5급 소형 모델에서도 포맷 준수율 향상

### 2.3 UNKNOWN 폴백
- **위치**: `src/agents/nodes/interpret/intent_classifier.py`
- **전략**: 파싱 최종 실패 시 IntentType.UNKNOWN, confidence=0.0으로 안전하게 폴백

---

## 3. 명확화 (Clarification)

### 3.1 대화 히스토리 활용 질문 생성
- **위치**: `src/agents/nodes/interpret/clarifier.py` → `_build_messages()`
- **전략**: 최근 4턴 대화 이력을 LLM에 전달하여 중복 질문 방지, 문맥 유지

### 3.2 선택지 형태 질문
- **위치**: `src/agents/nodes/prompts/system_prompts.py` → `CLARIFICATION`
- **전략**: 최대 3개 선택지 + "직접 입력" 옵션, 기술 용어 금지, 업무 용어로만 제시
- **예시**: "1) 이번 달 신규 고객 유입 현황, 2) 이번 달 여신 실행 현황, 3) 수신 잔액 현황, 4) 다른 데이터"

### 3.3 멀티턴 합성
- **위치**: `src/agents/nodes/interpret/preprocessor.py` → `_handle_clarification_response()`
- **전략**: 원래 질의 + 명확화 응답을 "\[원래 질의\]\n추가 조건: \[응답\]" 형태로 합성
- **제한**: 최대 2회 라운드 (`CLARIFICATION_MAX_TURNS = 2`) → 무한 루프 방지

---

## 4. 컨텍스트 수집 (Context Collection)

### 4.1 4소스 병렬 수집
- **위치**: `src/services/search_context_assembler.py` → `collect_context()`
- **전략**: `asyncio.gather()`로 ES 테이블메타, ES 보고서SQL, 이력DB, Qdrant 매뉴얼 4개를 동시 호출
- **개별 폴백**: 한 소스 실패해도 나머지 정상 반환 (빈 목록 폴백)
- **효과**: 단일 소스 대비 4배 컨텍스트 풍부도, 병렬 처리로 레이턴시 300~600ms 절감

### 4.2 도메인 지식 기반 검색 쿼리 전략
- **위치**: `src/services/search_query_builder.py` → `SearchQueryBuilder`
- **핵심 전략 (6단계 파이프라인)**:

  **Step 1 — 도메인 용어 매칭**: 150+개 금융 용어 사전에서 테이블명·컬럼명·카테고리 추출

  **Step 2 — 구조화 엔티티 추출**: 매칭된 용어에서 테이블, 컬럼, 카테고리를 분리

  **Step 3 — 불용어 제거**: 한국어 조사·어미·요청동사 60+개 제거 ("뽑아줘", "알려줘", "을", "를")

  **Step 4 — 동의어 확장**: "여신" → "대출", "론", "대여금" 확장으로 재현율(recall) 향상

  **Step 5 — 유사 테이블 신호어 수집**: "추이", "통계" → TB_LOAN_OVERDUE_STAT 선택 유도

  **Step 6 — 소스별 쿼리 특화**:
  - ES table_meta: `domain_cd` 주입 + 테이블명 부스트 + 시간어 제거
  - ES report_sql: 시간 표현 제거 + 카테고리 보강
  - PostgreSQL history: 핵심 키워드 + 동의어 확장 + 테이블명 (15개 제한)
  - Qdrant manual: 원본 자연어 + 도메인 설명 보강

### 4.3 domain_cd 주입
- **위치**: `src/services/search_query_builder.py` → `_CATEGORY_TO_DOMAIN_CD`
- **전략**: ES table_meta의 `table_name`이 keyword 타입이라 부분 검색 불가 → 카테고리에서 domain_cd(LON, DEP, CUS, CRD, TRX)를 추출하여 쿼리 선두에 주입
- **효과**: nori 미적용 환경에서 ES 테이블 검색 66.7% → 100.0% (+33.3%p)

### 4.4 도메인 사전 (150+개 용어)
- **위치**: `src/services/domain/finance_terms.py` → `DOMAIN_DICTIONARY`
- **전략**: 9개 카테고리(고객, 여신, 수신, 거래, 카드, 외환, 금융지표, 조직, 시간)에 걸쳐 용어별로 테이블명·컬럼명·SQL 조건·동의어(aliases) 매핑
- **핵심**: 사용자의 자연어 표현("마통", "주담대", "NPL")을 DB 스키마(TB_LOAN_INFO, LOAN_TYPE_CD='40')로 즉시 변환
- **활용 지점**: query_strategy (검색 쿼리 생성), sql_generator (도메인 용어 프롬프트 주입)

### 4.5 코드 메타 전체 로드
- **위치**: `src/services/search_context_assembler.py` → `_fetch_code_meta()`
- **전략**: ES에서 코드 메타(CUST_TYPE_CD: 01=개인, 02=기업 등)를 전체 로드하여 domain_terms에 병합
- **효과**: SQL 생성 시 코드값 매핑 자동 참조

---

## 5. 테이블 설명 보강 (Table Enrichment)

### 5.1 3관점 LLM 보강
- **위치**: `src/services/table_meta_enricher.py` → `enrich_table_descriptions()`
- **전략**: 메타 설명이 부실한 테이블에 대해 LLM이 3관점으로 보강
  1. **엔티티 집합 정의**: 한 행이 무엇을 의미하는지
  2. **기능적 정의**: 어디에, 어떻게 활용되는지
  3. **데이터 발생규칙**: 언제, 어떤 조건으로 생성되는지
- **근거 기반**: 관련 보고서 SQL + 과거 이력 SQL을 참고 자료로 제공

### 5.2 충분성 판별
- **전략**: description이 3가지 관점 모두 커버하는지 자동 판별 → 부족한 것만 LLM 호출
- **효율**: 이미 충분한 테이블은 LLM 호출 건너뜀

### 5.3 동시성 제어 + 타임아웃
- **전략**: `asyncio.Semaphore(3)`으로 LLM 동시 호출 3개 제한, 전체 60초 타임아웃
- **효과**: API rate limit 준수 + 파이프라인 지연 방지

---

## 6. 유사 테이블 구분 (Table Disambiguation)

### 6.1 유사 테이블 그룹 정의
- **위치**: `src/services/similar_table_resolver.py` → `SIMILAR_TABLE_GROUPS`
- **전략**: 5개 그룹 정의 (여신 연체, 수신 잔액, 여신 상세/요약, 거래 상세/요약, 고객 현재/이력)
- **각 테이블별**: 용도, 갱신주기, 적합한 요청 유형, 부적합한 요청 유형, 신호어 정의

### 6.2 신호어 기반 적합도 점수
- **위치**: `similar_table_resolver.py` → `score_table_for_query()`
- **전략**: 질의에서 신호어 매칭(+1점), 적합 유형 매칭(+0.5점), 부적합 유형 매칭(-1.0점)
- **예시**: "연체율 추이" → TB_LOAN_OVERDUE_STAT(+3.0) > TB_LOAN_INFO(-0.5)

### 6.3 구분 가이드 프롬프트 주입
- **위치**: `similar_table_resolver.py` → `build_table_disambiguation_prompt()`
- **전략**: 컨텍스트 수집에서 유사 그룹 감지 시 SQL 생성 프롬프트에 "유사 테이블 구분 가이드" 자동 추가
- **효과**: LLM이 TB_LOAN_INFO vs TB_LOAN_OVERDUE_STAT를 구분할 수 있는 명시적 기준 제공

### 6.4 SQL 검증 시 테이블 적절성 판정
- **위치**: `similar_table_resolver.py` → `validate_table_selection()`
- **전략**: 생성된 SQL에서 사용된 테이블이 유사 그룹 규칙에 부합하는지 3단계 판정 (PASS/WARNING/AMBIGUOUS)
- **AMBIGUOUS 시**: 사용자에게 명확화 질문 자동 생성

---

## 7. SQL 생성 (SQL Generation)

### 7.1 절대 규칙 10개
- **위치**: `src/agents/nodes/prompts/system_prompts.py` → `SQL_GENERATION_RULES`
- **핵심**:
  1. SELECT 문만 허용 (DML/DDL 절대 금지)
  2. 세미콜론 연쇄 금지
  3. PII 컬럼 SELECT 금지
  4. 전화번호·이메일 LEFT(col,3)||'\*\*\*\*' 마스킹
  5. LIMIT 없으면 LIMIT 10000 강제
  6. 날짜 조건 없으면 최근 1개월 자동 추가
  7. TB_TRANSACTION 반드시 TXN_DT 조건 포함
  8. 집계 함수에 한글/영문 alias 필수
  9. 시스템 카탈로그 조회 금지
  10. SQL 외 텍스트 출력 금지

### 7.2 Chain-of-Thought 5단계 추론
- **전략**: SQL 작성 전 STEP 1~5 순서로 사고
  1. 사용자가 원하는 데이터 파악
  2. 필요 테이블·컬럼을 스키마에서 찾기
  3. 조건(기간, 대상, 필터) 결정
  4. 집계·GROUP BY·ORDER BY 결정
  5. 절대 규칙 위반 여부 최종 점검

### 7.3 동적 컨텍스트 주입
- **위치**: `src/agents/nodes/sql_generator.py` → `generate_sql_node()`
- **주입 요소**:
  - `{table_info}`: 테이블 스키마 + enriched_description
  - `{report_sqls}`: 유사 보고서 SQL (최대 3건)
  - `{past_sqls}`: 과거 이력 SQL (최대 5건)
  - `{manual_refs}`: 업무 매뉴얼 참조 (최대 3건)
  - `{domain_context}`: 매칭된 도메인 용어의 테이블·컬럼·조건
  - `{domain_terms}`: 코드 메타 기반 용어 사전
  - `{validation_feedback_section}`: 재시도 시 이전 오류 내용

### 7.4 Few-shot 예제 3건
- **전략**: 난이도별 예제 (단순 집계, GROUP BY+ORDER BY+LIMIT, PII 마스킹 포함 목록)

### 7.5 마크다운 코드 블록 제거
- **위치**: `sql_generator.py` → `_clean_sql_response()`
- **전략**: LLM이 \`\`\`sql 블록으로 감싸는 경우 자동 추출

---

## 8. SQL 검증 (SQL Validation)

### 8.1 5계층 검증
- **위치**: `src/agents/nodes/sql_validator.py`
- **Layer 1 — 금지 패턴**: DML/DDL, 시스템 카탈로그, 파일 I/O, 시간지연, 주석, UNION SELECT (20+ 패턴)
- **Layer 2 — SQL 구문**: `sqlglot.parse()` 파싱 검증
- **Layer 3 — PII 직접 노출**: 주민번호·카드번호 등 26개 컬럼 SELECT 금지
- **Layer 4 — LIMIT 강제**: 순수 집계 쿼리(COUNT/SUM/AVG만)는 예외 허용
- **Layer 5 — 테이블 적절성**: 유사 테이블 그룹 규칙 검증

### 8.2 집계 쿼리 지능적 판별
- **위치**: `sql_validator.py` → `_is_aggregate_query()`
- **전략**: SELECT 절에서 집계 함수와 alias를 제거한 뒤, 잔여 내용이 구두점뿐이면 순수 집계로 판정 → LIMIT 면제

### 8.3 검증 실패 피드백 생성
- **위치**: `sql_validator.py` → `_build_validation_feedback()`
- **전략**: 실패한 SQL + 오류 목록(번호 매김)을 구조화하여 재생성 프롬프트에 주입
- **효과**: LLM이 이전 실패 원인을 명시적으로 인지한 채 재생성

### 8.4 이중 방어 (Double Defense)
- **위치**: `src/agents/nodes/present/sql_executor.py` + `src/utils/security.py`
- **전략**: sql_validator와 독립적으로 `validate_sql_safety()` 재검증
- **효과**: 검증 우회 시도 차단 (단일 검증 실패 보정)

---

## 9. SQL 재생성 루프

### 9.1 검증 피드백 기반 재시도
- **위치**: `src/agents/graph/pipeline.py` → `_route_after_validation()`
- **전략**: 검증 실패 → `validation_feedback`에 오류 내용 기록 → `generate_sql_node` 재진입 → 프롬프트에 이전 오류 주입
- **제한**: `SQL_MAX_RETRY = 2` (최대 3회 시도)
- **근거**: MAC-SQL 논문에서 self-correction으로 +8.2%p 개선 보고

### 9.2 피드백 섹션 템플릿
- **위치**: `src/agents/nodes/prompts/system_prompts.py` → `SQL_VALIDATION_FEEDBACK_SECTION`
- **전략**: "\[이전 시도에서 발견된 오류 — 반드시 수정하세요\]" 헤더 + 오류 목록

---

## 10. SQL 실행

### 10.1 행 수 제한
- **위치**: `src/agents/nodes/present/sql_executor.py`
- **전략**: `settings.max_query_rows` (기본 10,000) 초과 시 truncation 플래그 설정
- **효과**: 메모리 폭발 방지, 대용량 덤프 차단

### 10.2 사용자 친화적 오류 메시지
- **전략**: 내부 오류 상세는 로그에만, 사용자에게는 "잠시 후 다시 시도해주세요" 수준만 노출

---

## 11. 결과 포맷팅

### 11.1 비기술 사용자 대상 보고서 변환
- **위치**: `src/agents/nodes/prompts/system_prompts.py` → `RESULT_FORMATTING`
- **핵심 규칙**:
  - SQL·JOIN·WHERE 등 기술 용어 절대 불사용
  - 금액: 1억 이상 → "X억 Y,XXX만원"
  - 비율: 소수점 첫째 자리 "X.X%"
  - 건수: 천 단위 구분자 "X,XXX건"
  - 날짜: "2024-03" → "2024년 3월"
  - 코드값 → 한국어 의미 변환 (01=개인, 02=기업)

### 11.2 결과 행수 제한 (프롬프트용)
- **위치**: `src/agents/nodes/present/formatter.py` → `_format_result_for_prompt()`
- **전략**: 최대 50행만 LLM에 전달 + 전체 건수 주석

### 11.3 추론 과정 투명성 (Trace)
- **위치**: `src/agents/state/state.py` (add_trace, format_trace_summary) + `src/agents/nodes/present/formatter.py`
- **전략**: 각 노드의 결정(의도분류, 테이블선택, SQL생성)을 \<details\> 접이식으로 응답 끝에 첨부
- **효과**: 사용자가 결과의 근거를 확인 가능, 디버깅 용이

---

## 12. 데이터 분석

### 12.1 LLM 분석 + CoT 5단계
- **위치**: `src/agents/nodes/prompts/system_prompts.py` → `DATA_ANALYSIS`
- **전략**: STEP 1~5 (규모 파악 → 최고/최저값 → 증감률 → 이상치 → 시사점)
- **출력**: JSON (summary, insights, statistics, action_items)

### 12.2 기본 통계 자동 산출
- **위치**: `src/agents/nodes/present/analyzer.py`
- **전략**: 합계·평균·최소·최대·건수 자동 계산, 추세 감지(상승/하락/횡보), Z-score 이상치 탐지(2σ)

### 12.3 시각화 3단계 폴백
- **위치**: `src/agents/nodes/present/analyzer.py`
- **전략**:
  1. LLM이 차트 필요성 판단 (VISUALIZATION_JUDGMENT)
  2. 필요 시 LLM이 SVG 직접 생성 (VISUALIZATION_SVG_GENERATION)
  3. LLM SVG 실패 시 → 템플릿 기반 SVG 생성기 폴백 (`src/utils/chart_generator.py`)
- **최소 행수**: 3행 미만 데이터는 시각화 건너뜀
- **SVG 검증**: `<svg>...</svg>` 태그 존재 여부 정규식 검증

---

## 13. 파이프라인 라우팅

### 13.1 조건부 분기 5개 지점
- **위치**: `src/agents/graph/pipeline.py` → `build_pipeline()`
- **전략**:
  1. 전처리 후: 오류 → END, 정상 → 의도분류
  2. 의도분류 후: 명확화 → clarify, 추출/분석 → 컨텍스트수집
  3. 검증 후: PASS → 실행, FAIL+재시도가능 → SQL재생성, AMBIGUOUS → 명확화
  4. 실행 후: 분석의도 → 분석, 추출의도 → 포맷팅
  5. 에러: 컨텍스트별 사용자 친화적 메시지

### 13.2 Fail-Fast
- **전략**: 보안 위반(인젝션, 금지 패턴)은 즉시 종료, 복구 불가

---

## 14. 프롬프트 엔지니어링 기법 종합

### 14.1 역할 부여 (Role Prompting)
| 노드 | 역할 |
|------|------|
| SQL 생성 | "은행 정보계 PostgreSQL DB의 SQL 전문가" |
| 결과 포맷팅 | "은행 직원에게 보고서를 전달하는 AI" |
| 데이터 분석 | "은행 데이터 분석 AI 전문가" |
| 명확화 | "은행 직원의 데이터 요청을 돕는 AI 어시스턴트" |

### 14.2 Few-shot 예제 수
| 프롬프트 | 예제 수 | 커버리지 |
|---------|---------|---------|
| INTENT_CLASSIFICATION | 6건 | 4개 의도 + 경계 케이스 |
| SQL_GENERATION_RULES | 3건 | 단순집계, GROUP BY+LIMIT, PII마스킹 |
| RESULT_FORMATTING | 2건 | 단일행 결과, 다행 테이블 |
| DATA_ANALYSIS | 2건 | 지점별 비교, 시계열 추이 |
| TABLE_DESCRIPTION_ENRICHMENT | 3건 | 상세, 요약, 미상 정보 |
| CLARIFICATION | 3건 | 모호, 범위불명, 대상불명 |
| VISUALIZATION_JUDGMENT | 4건 | 시계열→line, 카테고리→bar, 비율→pie, 단일값→none |

### 14.3 Chain-of-Thought (CoT)
- SQL 생성: 5단계 사고 (의도파악 → 테이블선택 → 조건결정 → 집계결정 → 규칙검증)
- 데이터 분석: 5단계 사고 (규모파악 → 극값 → 증감률 → 이상치 → 시사점)

### 14.4 출력 형식 엄격 제한
- 의도분류: 2줄만 (INTENT: ..., CONFIDENCE: ...)
- 시각화판단: 2줄만 (CHART_TYPE: ..., CHART_TITLE: ...)
- SQL 생성: 순수 SQL만, 마크다운/주석/설명 금지
- 데이터 분석: JSON만, 마크다운 코드블록 금지

---

## 15. ES 한글 검색 최적화

### 15.1 nori analyzer 적용
- **위치**: `devtools/docker/elasticsearch/Dockerfile`, `devtools/scripts/seed_elasticsearch.py`
- **전략**: ES 커스텀 이미지에 `analysis-nori` 플러그인 포함, 모든 text 필드에 `korean` analyzer 적용
- **효과**: "여신정보(잔액)" → \["여신", "정보", "잔액"\] 토큰화 → 한글 검색 정상화

### 15.2 적용 전/후
| 검색어 | standard (적용 전) | nori (적용 후) |
|--------|-------------------|--------------|
| "여신" | 2건 | 29건 |
| "대출" | 0건 | 7건 |
| "연체" | 0건 | 5건 |
| "고객" | 0건 | 41건 |
| "카드" | 0건 | 23건 |

---

## 16. 벡터 검색 (Qdrant)

### 16.1 임베딩 모델
- **모델**: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (fastembed)
- **차원**: 384, Distance: Cosine
- **적용**: biz_manual(500건, content 임베딩), sql_history(10,000건, description 임베딩)

### 16.2 모델 불일치 수정
- **이슈**: 시딩(`paraphrase-multilingual-MiniLM-L12-v2`) vs 조회(`intfloat/multilingual-e5-small`) 모델 불일치
- **수정**: 커넥터 모델을 시딩과 동일하게 통일

---

## 17. 테스트 데이터 품질 전략

### 17.1 의도적 불완전성 주입 (5가지 TYPE)
- **위치**: `docs/data-generation-rules/` (5개 문서)
- **TYPE-1**: 컬럼 설명 누락/부실 (30~50% 빈 설명, "비고"/"기타" 같은 무의미 설명)
- **TYPE-2**: 코드 메타 불일치 (메타에 01/02만 정의, 실제 데이터에 03/04 존재)
- **TYPE-3**: 유사 테이블 혼동 (원장 vs 집계, 현행 vs 이력, 마스터 vs 서브)
- **TYPE-4**: 이중화 데이터 (영업등급 vs 마케팅등급, 당일잔액 vs 전일잔액)
- **TYPE-5**: 데이터 품질 이슈 (soft-delete, 휴면계좌, 정지대출)

### 17.2 골든셋 설계
- **위치**: `evaluation/golden_set/test_queries.json` (90건), `golden_queries.json` (18건)
- **분포**: easy 30% / medium 50% / hard 20%
- **도메인**: CUS(고객), DEP(수신), LON(여신), CRD(카드), TRX(거래), MKT(마케팅)
- **평가 차원**: 의도매칭, 테이블매칭, 패턴매칭, SQL구문, 부적합테이블 배제

---

## 18. 평가 프레임워크

### 18.1 다차원 평가
- **위치**: `devtools/evaluation/evaluator.py`
- **기준**: Intent match AND Table match AND (Pattern OR Syntax) AND No rejected tables
- **가중치**: 실행결과 일치(45%) > 의미적 일치(35%) > 구성요소 일치(20%)

### 18.2 배치 평가 리포팅
- **위치**: `devtools/evaluation/run_evaluation.py`, `src/utils/tracker/evaluation.py`
- **지표**: pass rate, 의도분류 정확도, SQL 재시도 통계, 노드별 레이턴시, LLM 호출 횟수/토큰
- **실패 분석**: 오류 카테고리별 분류, 실행 경로 분포, 병목 노드 식별

### 18.3 계기 비행 (Instrumented Pipeline)
- **위치**: `src/agents/graph/instrumented_pipeline.py`
- **전략**: 노드 래핑으로 입력/출력 요약, 의사결정 기록, 레이턴시 측정을 비침투적으로 수행

---

## 19. LLM 추상화 및 유연성

### 19.1 멀티 프로바이더 지원
- **위치**: `src/utils/llm/client.py`
- **전략**: Anthropic / OpenAI Compatible (Groq, OpenRouter) 통합 인터페이스
- **효과**: 폐쇄망 전환 시 프로바이더만 변경, 노드 코드 수정 불필요

### 19.2 소형 LLM 대응 설계
- 모든 프롬프트에 few-shot 예제 포함 (형식 학습)
- 출력 형식 엄격 제한 (2줄, JSON only 등)
- 포맷 실패 시 자동 재시도 + 힌트 주입
- 마크다운 코드블록 자동 제거

---

## 20. 골든셋 90건 E2E 검증 결과 (현재 수준)

```
소스별 적합도:
  ES table_meta :  98.9% (89/90)
  sql_history   :  85.6% (77/90)
  biz_manual    :  88.9% (80/90)
  종합          :  91.1% (246/270)

도메인별:
  CUS 100.0% / DEP 100.0% / LON 100.0% / CRD 100.0% / TRX 100.0% / MKT 96.7% (ES 기준)

난이도별:
  easy 100.0% / medium 100.0% / hard 97.0% (ES 기준)
```

---

## 부록: 전략 간 연결 관계

```
사용자 입력
  │
  ├─ [1] 유니코드 정규화 + SQL/프롬프트 인젝션 탐지 + PII 마스킹
  │
  ├─ [2] 의도 분류 (Few-shot + 신뢰도) → [3] 명확화 (멀티턴 합성)
  │
  ├─ [4] 컨텍스트 수집 (4소스 병렬)
  │    ├─ [4.2] 쿼리 전략 (도메인 용어 매칭 + 불용어 제거 + 동의어 확장 + 소스별 특화)
  │    ├─ [4.3] domain_cd 주입 (ES 검색 정밀도 향상)
  │    ├─ [5] 테이블 설명 보강 (3관점 LLM)
  │    └─ [6] 유사 테이블 구분 (신호어 + 구분 가이드)
  │
  ├─ [7] SQL 생성 (절대규칙 10개 + CoT 5단계 + 동적 컨텍스트 + Few-shot 3건)
  │    └─ [9] 재생성 루프 (검증 피드백 → 재시도, 최대 2회)
  │
  ├─ [8] SQL 검증 (5계층) + [8.4] 이중 방어
  │
  ├─ [10] SQL 실행 (행수 제한 + 이중 보안 검증)
  │
  ├─ [11] 결과 포맷팅 (비기술 보고서 + 코드값 변환 + 추론 트레이스)
  │
  └─ [12] 데이터 분석 (LLM CoT + 기본 통계 + 시각화 3단계 폴백)
```
