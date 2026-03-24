# 파이프라인 단계별 상세 설계

## 전체 파이프라인 아키텍처

```
사용자 자연어 입력
        │
        ▼
┌─────────────────────┐
│  1. 입력 전처리     │ ← InputProcessor
│  - 언어 감지         │   (한국어/영어 정규화)
│  - 악성 입력 필터링  │
│  - 길이/형식 검증    │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  2. 의도 분류       │ ← IntentClassifier
│  - 도메인 분류       │   (마케팅/영업/재무 등)
│  - 쿼리 유형 분류    │   (LIST/AGGREGATE/...)
│  - 복잡도 평가       │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  3. 컨텍스트 수집   │ ← ContextBuilder
│  - 관련 스키마 검색  │   (RAG: 벡터 유사도 검색)
│  - 도메인 규칙 로드  │
│  - 퓨샷 예제 선택    │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  4. SQL 생성        │ ← SQLGenerator (LLM 호출)
│  - 프롬프트 조립     │   (Claude API)
│  - SQL 생성         │
│  - 신뢰도 점수 산출  │
└──────────┬──────────┘
           │
    ┌──────┴──────┐
    │ 신뢰도 < 0.7? │
    └──────┬──────┘
           │ YES              NO
           ▼                  ▼
┌──────────────┐    ┌─────────────────────┐
│  재시도/명확화 │    │  5. SQL 검증        │
│  요청         │    │  - 문법 검사         │
└──────────────┘    │  - 보안 검사         │
                    │  - 비즈니스 규칙 검사 │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │  6. 쿼리 실행        │
                    │  - DB 연결           │
                    │  - 쿼리 실행         │
                    │  - 타임아웃 관리     │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │  7. 결과 포맷팅     │
                    │  - 데이터 변환       │
                    │  - 컬럼명 한글화     │
                    │  - 요약 생성         │
                    └─────────────────────┘
```

## 각 단계 입출력 명세

### Stage 1: 입력 전처리 (InputProcessor)
- 입력: 사용자 raw 텍스트
- 출력: 정규화된 텍스트, 메타데이터
- 처리: 공백 정규화, 길이 제한(500자), 금지 키워드 필터, 언어 감지

### Stage 2: 의도 분류 (IntentClassifier)
- 입력: 정규화된 텍스트
- 출력: `{domain: str, query_type: str, complexity: int, entities: list}`
- 처리: 도메인 키워드 매칭, 쿼리 유형 분류, 복잡도 점수(1-5), 엔티티 추출

### Stage 3: 컨텍스트 수집 (ContextCollector + QueryStrategy)
- 입력: 전처리된 질의 (preprocessed_input)
- 출력: ContextInfo (table_metas, report_sqls, past_sqls, manual_refs, domain_terms)
- 처리:
  1. QueryStrategyBuilder가 소스별 최적화 쿼리 생성 (6단계: 도메인 매칭→불용어 제거→동의어 확장→domain_cd 주입)
  2. asyncio.gather()로 5개 소스 병렬 수집 (ES table/report/code, Qdrant manual, History DB)
  3. 테이블 설명 보강 (LLM 3관점: 엔티티 정의, 기능 정의, 발생규칙)
  4. 유사 테이블 그룹 감지 + 구분 가이드 생성
- 참고: `docs/agent-guides/context-assembly.md` 상세 가이드

### Stage 4: SQL 생성 (SQLGenerator)
- 입력: 사용자 질의 + 컨텍스트
- 출력: `{sql: str, thinking: str, confidence: float, ambiguities: list}`
- 오류 처리: API 타임아웃(10초) → 재시도 1회, 신뢰도 < 0.7 → 명확화 질문, JSON 파싱 실패 → 재생성 1회

### Stage 5: SQL 검증 (SQLValidator)
- 입력: 생성된 SQL
- 출력: `{valid: bool, issues: list, fixed_sql: str | null}`
- 처리: SQLGlot 문법 파싱, 화이트리스트 패턴 검사, 위험 패턴 차단, 비즈니스 규칙 검사, 자동 수정 시도

### Stage 6: 쿼리 실행 (QueryExecutor)
- 입력: 검증된 SQL
- 출력: `{rows: list, columns: list, row_count: int, execution_time_ms: int}`
- 처리: 읽기 전용 커넥션, 실행 타임아웃 30초, 결과 행 수 제한 10,000건

### Stage 7: 결과 포맷팅 (ResultFormatter)
- 입력: raw DB 결과
- 출력: 사용자 친화적 응답 (JSON + 자연어 요약)
- 처리: 컬럼명 한글화, 날짜/금액 포맷, 결과 요약, 엑셀/CSV 다운로드 URL

## 에러 처리 전략

| 에러 유형 | 최대 재시도 | 폴백 | 사용자 메시지 |
|----------|-----------|------|-------------|
| SQL_GENERATION_FAILED | 2 | 질의 재작성 요청 | "질의를 좀 더 구체적으로 말씀해 주세요." |
| SQL_VALIDATION_FAILED | 1 | 안전한 기본 쿼리 제안 | "생성된 SQL에 문제가 발견되어 수정 중입니다." |
| DB_TIMEOUT | 0 | 쿼리 단순화 제안 | "조회 데이터가 너무 많습니다. 기간이나 조건을 추가해 주세요." |
| AMBIGUOUS_INTENT | 0 | 명확화 질문 생성 | "다음 중 어떤 것을 원하시나요? 1. {option_1} 2. {option_2}" |

## 성능 최적화

### 캐싱 전략
| 레벨 | TTL | 키 패턴 |
|------|-----|---------|
| 스키마 메타데이터 | 1시간 | `schema:{domain}` |
| 동일 질의 결과 | 5분 | `result:{query_hash}` |
| 퓨샷 임베딩 | 24시간 | `embedding:{example_id}` |

### 비동기 처리
- context_retrieval: 병렬 (스키마 + 도메인 규칙 동시 조회)
- sql_generation: 순차 (LLM 호출)
- result_formatting: 비동기 (엑셀 생성)
