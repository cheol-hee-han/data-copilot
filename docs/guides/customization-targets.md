# 폐쇄망 환경 커스터마이징 대상 항목

> 답변 정확도 향상을 위해 적용된 전략 중, 실제 폐쇄망(은행 내부망) 환경 배포 시
> 커스터마이징이 필요한 항목을 분석·정리한 문서.
>
> 분석 기준일: 2026-03-20
> 대상: src/, src/agents/nodes/prompts/, evaluation/, standalone/scripts/, standalone/docker/ 전체 코드베이스

---

## 목차

1. [LLM 프로바이더 & 모델 교체](#1-llm-프로바이더--모델-교체)
2. [임베딩 모델 교체 (Qdrant 벡터 검색)](#2-임베딩-모델-교체-qdrant-벡터-검색)
3. [프롬프트 전면 재튜닝](#3-프롬프트-전면-재튜닝)
4. [LLM 포맷 재시도 설정](#4-llm-포맷-재시도-설정)
5. [ElasticSearch nori 한글 분석기](#5-elasticsearch-nori-한글-분석기)
6. [도메인 사전 커스터마이징](#6-도메인-사전-커스터마이징)
7. [카테고리-domain_cd 매핑](#7-카테고리-domain_cd-매핑)
8. [유사 테이블 그룹 재정의](#8-유사-테이블-그룹-재정의)
9. [PII 컬럼 정의 재설정](#9-pii-컬럼-정의-재설정)
10. [코드값 매핑 교체](#10-코드값-매핑-교체)
11. [한국어 불용어·조사 패턴](#11-한국어-불용어조사-패턴)
12. [Few-shot SQL 예제 교체](#12-few-shot-sql-예제-교체)
13. [골든셋 & 평가 프레임워크](#13-골든셋--평가-프레임워크)
14. [업무 매뉴얼 데이터 교체](#14-업무-매뉴얼-데이터-교체)
15. [LangSmith 비활성화 확인 & 대체 트레이싱](#15-langsmith-비활성화-확인--대체-트레이싱)
16. [테이블 메타 시딩 데이터](#16-테이블-메타-시딩-데이터)
17. [SVG 시각화 폰트](#17-svg-시각화-폰트)
18. [Docker 이미지 빌드 (오프라인)](#18-docker-이미지-빌드-오프라인)
19. [fastembed 모델 캐시 경로](#19-fastembed-모델-캐시-경로)
20. [커스터마이징 우선순위 요약](#커스터마이징-우선순위-요약)

---

## 1. LLM 프로바이더 & 모델 교체

| 위치 | 현재 설정 | 커스터마이징 내용 |
|------|-----------|-------------------|
| `src/config.py:10-14` | `anthropic` + `claude-sonnet-4-20250514` | 폐쇄망 내부 LLM (vLLM, TGI 등)으로 교체. `openai_compatible` 프로바이더 + 내부 엔드포인트 URL 지정 |
| `src/utils/llm/client.py:163-174` | OpenAI compatible 클라이언트의 `default_headers`에 `HTTP-Referer`, `X-OpenRouter-Title` 하드코딩 | 내부 LLM 게이트웨이에 맞는 헤더로 변경 또는 제거 |

**정확도 영향**: 모든 노드(의도분류, SQL생성, 분석, 포맷팅, 테이블보강)가 LLM에 의존한다. 소형 모델 전환 시 **모든 프롬프트의 few-shot 예제 수·CoT 깊이 재조정**이 필요하다.

**환경변수 예시**:
```env
LLM_PROVIDER=openai_compatible
OPENAI_BASE_URL=http://internal-llm-gateway:8080/v1
OPENAI_API_KEY=internal-key
LLM_MODEL=내부모델명
```

---

## 2. 임베딩 모델 교체 (Qdrant 벡터 검색)

| 위치 | 현재 설정 | 커스터마이징 내용 |
|------|-----------|-------------------|
| `src/connectors/qdrant_connector.py:18` | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (HuggingFace 다운로드) | 폐쇄망에서는 모델 파일을 **로컬 경로로 사전 배포** |
| `standalone/scripts/seed_qdrant.py:28-31` | 동일 모델, fastembed 사용 | fastembed의 모델 캐시 디렉토리를 내부 경로로 설정하거나 오프라인 모델 로딩 구현 |

**정확도 영향**: 임베딩 모델이 업무 매뉴얼(biz_manual 500건) + SQL 이력(sql_history 10,000건) 검색 품질을 직접 좌우한다. 모델 교체 시 **전체 컬렉션 재임베딩** 필수. 한국어 성능이 낮은 모델로 교체하면 업무 매뉴얼 RAG 정확도가 급락한다.

**주의사항**:
- 시딩 스크립트(`seed_qdrant.py`)와 커넥터(`qdrant_connector.py`)의 모델명이 반드시 일치해야 한다.
- 모델 차원(현재 384)이 변경되면 Qdrant 컬렉션의 `VectorParams.size`도 함께 변경해야 한다.

---

## 3. 프롬프트 전면 재튜닝

| 위치 | 현재 설정 | 커스터마이징 내용 |
|------|-----------|-------------------|
| `src/agents/nodes/prompts/system_prompts.py` 전체 (8개 프롬프트) | Claude Sonnet급 모델 기준 설계 | 내부 소형 LLM(7B~70B급)은 **출력 형식 준수율이 크게 떨어짐** → 프롬프트별 조정 필요 |

### 프롬프트별 재튜닝 포인트

| 프롬프트 | 줄 번호 | 현재 설계 | 소형 모델 전환 시 조정 방향 |
|---------|---------|-----------|---------------------------|
| `INTENT_CLASSIFICATION` | L24-72 | 4-way 분류 + 신뢰도 2줄 포맷 | few-shot 예제 추가 또는 JSON 출력으로 변경 |
| `CLARIFICATION` | L78-121 | 3개 선택지 자연어 생성 | 템플릿 기반 생성으로 단순화 |
| `SQL_GENERATION_RULES` | L127-213 | CoT 5단계 + 절대규칙 10개 | 규칙 수 축소 또는 `SQL_MAX_RETRY` 상향 |
| `RESULT_FORMATTING` | L228-281 | 자연어 보고서 변환 | 포맷 규칙 단순화, 예제 추가 |
| `DATA_ANALYSIS` | L287-323 | JSON-only 출력 | 마크다운 코드블록 파싱 로직 강화 |
| `TABLE_DESCRIPTION_ENRICHMENT` | L329-394 | 3관점 LLM 보강 | 소형 모델에서 품질 저하 → 비활성화 고려 |
| `VISUALIZATION_JUDGMENT` | L400-443 | 2줄 포맷 판단 | 포맷 실패 빈도 증가 대비 폴백 강화 |
| `VISUALIZATION_SVG_GENERATION` | L449-495 | LLM 직접 SVG 생성 | 템플릿 기반 `chart_generator.py`로 직접 폴백 비율 증가 |

---

## 4. LLM 포맷 재시도 설정

| 위치 | 현재 설정 | 커스터마이징 내용 |
|------|-----------|-------------------|
| `src/config.py:53` | `llm_parse_max_retry: int = 2` | 소형 모델은 포맷 준수율이 낮아 **3~5회로 상향** 필요 |
| `src/utils/llm/retry.py` | 재시도 시 이전 응답 + 포맷 힌트 주입 | 내부 모델의 특성에 맞는 포맷 힌트 재작성 |

**환경변수**:
```env
LLM_PARSE_MAX_RETRY=4
```

---

## 5. ElasticSearch nori 한글 분석기

| 위치 | 현재 설정 | 커스터마이징 내용 |
|------|-----------|-------------------|
| `standalone/docker/elasticsearch/Dockerfile` | `analysis-nori` 플러그인 온라인 설치 | 폐쇄망에서는 **nori 플러그인 오프라인 설치** (zip 파일 사전 배포) |
| `standalone/scripts/seed_elasticsearch.py` | `korean` analyzer 적용 | 커스텀 nori 분석기 설정(사용자 사전, 동의어 필터) 추가 검토 |

**정확도 영향**: nori 미적용 시 한글 검색 정확도 급락 (적용 전/후 비교):

| 검색어 | standard (적용 전) | nori (적용 후) |
|--------|-------------------|---------------|
| "여신" | 2건 | 29건 |
| "대출" | 0건 | 7건 |
| "연체" | 0건 | 5건 |
| "고객" | 0건 | 41건 |
| "카드" | 0건 | 23건 |

**추가 권장**: 은행 고유 용어(상품명, 내부 코드 등)를 **nori 사용자 사전**에 등록하면 검색 정확도가 추가 향상된다.

---

## 6. 도메인 사전 커스터마이징

| 위치 | 현재 설정 | 커스터마이징 내용 |
|------|-----------|-------------------|
| `src/services/domain/finance_terms.py` 전체 | 범용 은행 용어 150+개 (샘플 테이블명·코드값) | **실제 은행의 테이블명·컬럼명·코드값으로 전면 교체** |

### 구체적 교체 항목

- **테이블명**: `TB_LOAN_INFO`, `TB_CUST_INFO` 등 → 실제 정보계 테이블명
- **컬럼명**: `CUST_TYPE_CD`, `LOAN_TYPE_CD` 등 → 실제 컬럼명
- **코드값 조건**: `CUST_TYPE_CD = '01'`, `LOAN_TYPE_CD = '02'` 등 → 실제 코드 체계
- **동의어(aliases)**: 해당 은행 고유 약어·상품명 추가 (예: "직장인론", "e편한대출")
- **카테고리 추가**: 해당 은행의 업무 영역에 맞게 카테고리 확장

### 활용 지점 (영향받는 모듈)

1. `src/services/search_query_builder.py` — 검색 쿼리 생성 시 도메인 용어 매칭·동의어 확장
2. `src/agents/nodes/sql_generator.py` — SQL 생성 프롬프트에 `{domain_context}` 주입
3. `src/services/similar_table_resolver.py` — 유사 테이블 구분 신호어 참조

---

## 7. 카테고리-domain_cd 매핑

| 위치 | 현재 설정 | 커스터마이징 내용 |
|------|-----------|-------------------|
| `src/services/search_query_builder.py:41-50` | `_CATEGORY_TO_DOMAIN_CD` (CUS, LON, DEP, TRX, CRD, FEX, MGT) | 실제 ES 인덱스의 `domain_cd` 필드값에 맞게 **전면 재매핑** |

**현재 매핑**:
```python
_CATEGORY_TO_DOMAIN_CD = {
    "고객": ["CUS"],
    "여신": ["LON"],
    "수신": ["DEP"],
    "거래": ["TRX", "DEP"],
    "카드": ["CRD"],
    "외환": ["FEX"],
    "금융지표": ["MGT", "LON"],
    "조직": ["CUS"],
}
```

**정확도 영향**: domain_cd 주입이 ES 테이블 검색에서 +33.3%p 개선 효과(66.7%→100%)를 가져왔다. 잘못된 매핑은 오히려 검색 품질을 저하시킨다.

---

## 8. 유사 테이블 그룹 재정의

| 위치 | 현재 설정 | 커스터마이징 내용 |
|------|-----------|-------------------|
| `src/services/similar_table_resolver.py` | 5개 유사 테이블 그룹 (샘플 테이블 기준) | 실제 정보계 DB의 **유사 테이블 관계를 분석하여 그룹 재정의** |

### 재정의 필요 항목

- 각 그룹의 테이블명·용도·갱신주기
- 테이블별 `signal_keywords` (적합 질의 신호어)
- 테이블별 `suitable_types` / `unsuitable_types` (적합/부적합 요청 유형)
- SQL 검증 5계층의 Layer 5 (테이블 적절성 판정) 규칙 연동

### 현재 5개 그룹 (교체 대상)

| 그룹 | 도메인 | 포함 테이블 |
|------|--------|------------|
| 1 | 여신 연체 | TB_LOAN_INFO vs TB_LOAN_OVERDUE_STAT |
| 2 | 수신 잔액 | 현행잔액 vs 이력잔액 |
| 3 | 여신 상세/요약 | 개별건 vs 집계 |
| 4 | 거래 상세/요약 | 건별거래 vs 일별집계 |
| 5 | 고객 현재/이력 | 마스터 vs 변경이력 |

---

## 9. PII 컬럼 정의 재설정

| 위치 | 현재 설정 | 커스터마이징 내용 |
|------|-----------|-------------------|
| `src/agents/nodes/sql_validator.py` | 26개 PII 컬럼명 하드코딩 | 실제 DB의 **PII 컬럼명으로 전면 교체** |
| `src/utils/security.py` | 마스킹 패턴 정의 | 실제 컬럼의 데이터 형식에 맞는 마스킹 패턴 재정의 |
| `src/agents/nodes/prompts/system_prompts.py:133-136` | 절대규칙 3~4번 (PII 관련) | 실제 PII 컬럼명 목록으로 교체 |

### PII 분류 체계

| 분류 | 현재 정의 | 커스터마이징 |
|------|-----------|-------------|
| **직접 노출 금지** | 주민번호, 카드번호, 계좌번호, 비밀번호, CVC | 실제 컬럼명으로 교체 |
| **마스킹 필수** | 전화번호, 이메일, 생년월일, 주소 | 실제 컬럼명 + 마스킹 패턴 교체 |
| **조건부 마스킹** | 고객명 (목록 조회 시 부분 마스킹) | 은행 내부 규정에 따라 기준 재설정 |

---

## 10. 코드값 매핑 교체

| 위치 | 현재 설정 | 커스터마이징 내용 |
|------|-----------|-------------------|
| `src/agents/nodes/prompts/system_prompts.py:246-251` | RESULT_FORMATTING의 `[코드값 참고]` (01=개인, 02=기업 등 샘플) | 실제 은행 코드 체계로 **전면 교체** |
| `src/services/search_context_assembler.py` `_fetch_code_meta()` | ES에서 코드 메타 전체 로드 | 실제 ES 코드 메타 인덱스 구조에 맞게 **쿼리·파싱 로직 조정** |

**현재 샘플 코드값** (교체 대상):
```
고객유형: 01=개인, 02=기업, 03=개인사업자
대출유형: 01=신용대출, 02=담보대출, 03=보증대출
거래유형: 01=입금, 02=출금, 03=이체
계좌상태: 01=정상, 02=해지, 03=휴면
여신상태: 01=정상, 02=연체, 03=대손
```

---

## 11. 한국어 불용어·조사 패턴

| 위치 | 현재 설정 | 커스터마이징 내용 |
|------|-----------|-------------------|
| `src/services/search_query_builder.py:55-72` | 범용 한국어 불용어 60+개 | 해당 은행 임직원이 실제 사용하는 **업무 표현 분석 후 불용어 보강** |

**검증 필요 사항**:
- 은행 내부 시스템명, 약어 등이 불용어로 잘못 제거되지 않는지 확인
- 자주 사용되는 요청 동사("집계해줘", "산출해줘" 등)가 누락되었는지 확인
- 은행 고유 조사·접미사 패턴 추가 여부 검토

---

## 12. Few-shot SQL 예제 교체

| 위치 | 현재 설정 | 커스터마이징 내용 |
|------|-----------|-------------------|
| `src/agents/nodes/prompts/system_prompts.py:169-209` | SQL_GENERATION_RULES의 3개 few-shot (샘플 테이블 기반) | 실제 정보계 테이블·컬럼명 기반 few-shot으로 교체 |
| `src/agents/nodes/prompts/system_prompts.py:252-281` | RESULT_FORMATTING의 2개 few-shot | 실제 결과 데이터 형태에 맞는 포맷팅 예제로 교체 |
| `src/agents/nodes/prompts/system_prompts.py:306-323` | DATA_ANALYSIS의 2개 few-shot | 실제 분석 시나리오 기반 예제로 교체 |

**교체 원칙**:
- 실제 자주 조회되는 상위 3~5개 패턴 기반
- 난이도별 분포: 단순 집계 1건 + GROUP BY/ORDER BY 1건 + PII 마스킹 포함 1건
- 실제 테이블명·컬럼명·코드값 사용

---

## 13. 골든셋 & 평가 프레임워크

| 위치 | 현재 설정 | 커스터마이징 내용 |
|------|-----------|-------------------|
| `evaluation/golden_set/golden_queries.json` | 18건 벤치마크 (샘플 테이블 기준) | 실제 DB 기반 **골든 쿼리 전면 재작성** |
| `evaluation/golden_set/test_queries.json` | 90건 테스트 (샘플 기준) | 실제 업무 시나리오 기반 재작성 |
| `evaluation/evaluator.py` | 다차원 평가 (intent, table, pattern, syntax) | 실제 DB에서 **SQL 실행 결과 비교(execution accuracy)** 평가 차원 추가 가능 |

### 재작성 대상 필드

- `query`: 실제 은행 직원의 요청 문장
- `expected_intent`: 의도 분류 기대값
- `expected_tables`: 실제 정보계 테이블명
- `expected_sql_pattern`: 실제 SQL 패턴
- `rejected_tables`: 유사하지만 부적합한 테이블명
- `category`: 실제 업무 도메인 분류
- `difficulty`: 난이도 분포 유지 (easy 30%, medium 50%, hard 20%)

---

## 14. 업무 매뉴얼 데이터 교체

| 위치 | 현재 설정 | 커스터마이징 내용 |
|------|-----------|-------------------|
| `src/connectors/qdrant_connector.py:21-95` | `DUMMY_MANUALS` 5건 (샘플 업무 매뉴얼) | 실제 은행 **업무 매뉴얼·규정집·계수산출식** 문서를 Qdrant에 적재 |
| `standalone/scripts/seed_qdrant.py` + `qdrant_data_generators` | 생성기 기반 500+건 | 실제 문서 기반 데이터 적재 스크립트 재작성 |

**정확도 영향**: 금융지표 질의 시 정확한 산출식 참조 여부가 SQL 정확도를 결정한다. `financial-domain.md` 규칙에 따라 산출식이 불확실한 상태로 SQL을 생성하지 않으므로, 산출식 데이터가 없으면 해당 유형의 질의가 모두 실패한다.

### 필수 포함 문서 유형

- 금융 계수산출식 (연체율, BIS비율, LCR, NIM, ROA, ROE 등)
- 여신 심사·수신 상품·카드 업무 절차
- 고객 등급 분류 기준
- 내부 보고서 작성 기준
- 코드 체계 정의서

---

## 15. LangSmith 비활성화 확인 & 대체 트레이싱

| 위치 | 현재 설정 | 커스터마이징 내용 |
|------|-----------|-------------------|
| `src/config.py:56-59` | `langsmith_enabled: False`, 엔드포인트 `https://api.smith.langchain.com` | 폐쇄망에서 **반드시 False 유지** 확인. 외부 호출 시도 자체를 차단하려면 환경변수 제거 |
| `src/utils/tracker/evaluation.py` | 자체 JSON 트레이싱 (폐쇄망 호환) | 이미 폐쇄망 호환으로 설계됨. 내부 모니터링 시스템 연동 시 출력 포맷 확장 가능 |

**환경변수**:
```env
LANGSMITH_ENABLED=false
# LANGSMITH_API_KEY, LANGSMITH_ENDPOINT 는 설정하지 않음
```

---

## 16. 테이블 메타 시딩 데이터

| 위치 | 현재 설정 | 커스터마이징 내용 |
|------|-----------|-------------------|
| `standalone/scripts/seed_elasticsearch.py` | 샘플 8개 테이블 메타 | 실제 정보계 DB의 **전체 테이블 레이아웃(DDL)을 ES에 적재** |
| `standalone/scripts/seed_postgres.py` | 샘플 테스트 데이터 | 실제 DB 연결로 교체 (시딩 불필요, 읽기 전용 접근) |

### ES 인덱스 적재 대상

- `table_meta`: 테이블명, 컬럼 정보, 설명, domain_cd, 갱신주기
- `code_meta`: 코드 컬럼별 코드값-한글명 매핑
- `report_sql`: 기존 보고서 SQL + 요건 설명

---

## 17. SVG 시각화 폰트

| 위치 | 현재 설정 | 커스터마이징 내용 |
|------|-----------|-------------------|
| `src/agents/nodes/prompts/system_prompts.py:458` | `font-family="'Malgun Gothic','맑은 고딕',sans-serif"` | 폐쇄망 서버 OS에 해당 폰트 설치 여부 확인. 리눅스 서버는 **Noto Sans KR 등 대체 한글 폰트** 설정 |
| `src/utils/chart_generator.py` | 템플릿 기반 SVG (동일 폰트 사용) | 동일하게 폰트 설정 변경 |

**리눅스 환경 예시**:
```
font-family="'Noto Sans KR','NanumGothic',sans-serif"
```

---

## 18. Docker 이미지 빌드 (오프라인)

| 위치 | 현재 설정 | 커스터마이징 내용 |
|------|-----------|-------------------|
| `standalone/docker/docker-compose.dev.yml` | PostgreSQL 16, ES 8.15, Qdrant 1.12.6, Redis 이미지 | 폐쇄망 내부 레지스트리에서 pull 가능하도록 **이미지 사전 빌드·배포** |
| `standalone/docker/elasticsearch/Dockerfile` | nori 플러그인 온라인 설치 | **빌드 시점에 플러그인 포함된 커스텀 이미지** 사전 준비 |
| `pyproject.toml` | pip 패키지 의존성 | 내부 PyPI 미러 또는 **오프라인 wheel 번들** 준비 |

### 사전 준비 체크리스트

- [ ] PostgreSQL 16 이미지 → 내부 레지스트리 push
- [ ] ES 8.15 + nori 플러그인 커스텀 이미지 빌드 → 내부 레지스트리 push
- [ ] Qdrant v1.12.6 이미지 → 내부 레지스트리 push
- [ ] Redis 이미지 → 내부 레지스트리 push
- [ ] Python 3.12 이미지 → 내부 레지스트리 push
- [ ] pip 패키지 오프라인 번들 (anthropic, fastembed, qdrant-client, sqlglot, pydantic 등)

---

## 19. fastembed 모델 캐시 경로

| 위치 | 현재 설정 | 커스터마이징 내용 |
|------|-----------|-------------------|
| `src/connectors/qdrant_connector.py:125-127` | `TextEmbedding(model_name=_EMBEDDING_MODEL)` — HuggingFace Hub에서 자동 다운로드 | 폐쇄망에서는 **모델 파일을 로컬 디렉토리에 사전 배치** |

**설정 방법**:
```python
# 방법 1: 환경변수
os.environ["FASTEMBED_CACHE_PATH"] = "/opt/models/fastembed"

# 방법 2: 생성자 파라미터
TextEmbedding(model_name=_EMBEDDING_MODEL, cache_dir="/opt/models/fastembed")
```

**사전 준비**: 외부망에서 모델을 다운로드한 뒤 캐시 디렉토리를 통째로 폐쇄망 서버에 복사한다.

---

## 커스터마이징 우선순위 요약

### P0 — 필수 (미수행 시 시스템 동작 불가)

| # | 항목 | 이유 |
|---|------|------|
| 1 | [LLM 프로바이더 교체](#1-llm-프로바이더--모델-교체) | 전체 파이프라인 동작 불가 |
| 2 | [임베딩 모델 오프라인 배포](#2-임베딩-모델-교체-qdrant-벡터-검색) | Qdrant 벡터 검색 불가 |
| 18 | [Docker 이미지/패키지 오프라인 준비](#18-docker-이미지-빌드-오프라인) | 인프라 구동 불가 |
| 19 | [fastembed 모델 캐시 경로](#19-fastembed-모델-캐시-경로) | 임베딩 생성 불가 |
| 5 | [nori 플러그인 오프라인 설치](#5-elasticsearch-nori-한글-분석기) | 한글 검색 66.7%로 급락 |

### P1 — 정확도 핵심 (미수행 시 답변 품질 심각 저하)

| # | 항목 | 이유 |
|---|------|------|
| 6 | [도메인 사전 교체](#6-도메인-사전-커스터마이징) | 자연어→SQL 변환의 핵심 브릿지 |
| 3 | [프롬프트 재튜닝](#3-프롬프트-전면-재튜닝) | 소형 모델 전환 시 필수 |
| 4 | [LLM 포맷 재시도 설정](#4-llm-포맷-재시도-설정) | 소형 모델 포맷 준수율 보정 |
| 9 | [PII 컬럼 재설정](#9-pii-컬럼-정의-재설정) | 보안 규칙 미준수 위험 |
| 10 | [코드값 매핑 교체](#10-코드값-매핑-교체) | 결과 포맷팅 품질 |
| 14 | [업무 매뉴얼 실데이터 적재](#14-업무-매뉴얼-데이터-교체) | 금융지표 산출식 참조 불가 |

### P2 — 품질 향상 (수행 시 정확도 추가 개선)

| # | 항목 | 이유 |
|---|------|------|
| 8 | [유사 테이블 그룹 재정의](#8-유사-테이블-그룹-재정의) | 테이블 선택 정확도 |
| 13 | [골든셋 재작성](#13-골든셋--평가-프레임워크) | 정확도 측정·개선 기반 |
| 7 | [domain_cd 매핑](#7-카테고리-domain_cd-매핑) | ES 검색 정밀도 |
| 12 | [Few-shot SQL 예제 교체](#12-few-shot-sql-예제-교체) | SQL 생성 품질 |
| 16 | [테이블 메타 시딩](#16-테이블-메타-시딩-데이터) | 컨텍스트 수집 품질 |

### P3 — 부가 (세부 품질 개선)

| # | 항목 | 이유 |
|---|------|------|
| 11 | [불용어 보강](#11-한국어-불용어조사-패턴) | 검색 노이즈 감소 |
| 17 | [SVG 폰트](#17-svg-시각화-폰트) | 차트 한글 깨짐 방지 |
| 15 | [LangSmith 확인](#15-langsmith-비활성화-확인--대체-트레이싱) | 외부 통신 차단 확인 |
