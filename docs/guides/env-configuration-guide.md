# 환경 변수 설정 레퍼런스

> `.env` 파일의 모든 설정 항목에 대한 설명, 허용값, 권장값을 정의한다.

**버전**: 1.0
**최종 수정**: 2026-03-23
**대상 독자**: 개발자, 운영자, 폐쇄망 배포 담당자

---

## 목차

1. [LLM 프로바이더](#1-llm-프로바이더)
2. [LLM 호출 제어](#2-llm-호출-제어)
3. [임베딩 모델](#3-임베딩-모델)
4. [Reranker](#4-reranker)
5. [파이프라인 제어](#5-파이프라인-제어)
6. [질의 정규화](#6-질의-정규화)
7. [PostgreSQL](#7-postgresql)
8. [ElasticSearch](#8-elasticsearch)
9. [Qdrant](#9-qdrant)
10. [MongoDB](#10-mongodb)
11. [Redis / 세션](#11-redis--세션)
12. [폐쇄망 전용 DB](#12-폐쇄망-전용-db)
13. [트래킹](#13-트래킹)
14. [애플리케이션](#14-애플리케이션)
15. [시각화 레이아웃](#15-시각화-레이아웃)
16. [금융 도메인 상수](#16-금융-도메인-상수)
17. [Gunicorn (운영 서버)](#17-gunicorn-운영-서버)

---

## 1. LLM 프로바이더

| 키 | 설명 | 값 예시 | 권장값 |
| --- | --- | --- | --- |
| `LLM_PROVIDER` | LLM API 프로바이더 선택 | `anthropic` \| `openai_compatible` | 온라인: `anthropic`, 폐쇄망: `openai_compatible` |
| `ANTHROPIC_API_KEY` | Anthropic Claude API 키 | `sk-ant-...` | LLM_PROVIDER=anthropic 시 필수 |
| `LLM_MODEL` | 사용할 LLM 모델 ID | `claude-sonnet-4-20250514` \| `llama-3.3-70b-versatile` \| `qwen3:8b` | 온라인: `claude-sonnet-4-20250514`, Groq: `llama-3.3-70b-versatile` |
| `OPENAI_API_KEY` | OpenAI Compatible API 키 | `gsk_...` (Groq) \| `sk-or-...` (OpenRouter) | LLM_PROVIDER=openai_compatible 시 필수 |
| `OPENAI_BASE_URL` | OpenAI Compatible 엔드포인트 | `https://api.groq.com/openai/v1` \| `http://localhost:11434/v1` | 프로바이더별 상이 |

---

## 2. LLM 호출 제어

| 키 | 설명 | 값 예시 | 권장값 |
| --- | --- | --- | --- |
| `LLM_PARSE_MAX_RETRY` | LLM 응답 포맷 파싱 실패 시 최대 재시도 횟수 | `2` \| `4` | 소형 모델: `4` (포맷 오류 빈번), 대형 모델: `2` |
| `LLM_DEFAULT_MAX_TOKENS` | 기본 응답 max_tokens (의도 분류 등 짧은 응답) | `200` | `200` |
| `LLM_DEFAULT_TIMEOUT` | 기본 LLM 호출 타임아웃 (초) | `15.0` | `15.0` |
| `LLM_LONG_TIMEOUT` | SQL 생성·분석·포맷팅 등 긴 작업 타임아웃 (초) | `30.0` | `30.0` |
| `LLM_CONTEXT_TIMEOUT` | 컨텍스트 수집 전체(테이블 보강 포함) 타임아웃 (초) | `60.0` | `60.0` |
| `LLM_FORMAT_MAX_TOKENS` | 포맷팅·분석 응답 max_tokens | `2000` | `2000` |
| `LLM_SVG_MAX_TOKENS` | SVG 차트 생성 응답 max_tokens | `4000` | `4000` — SVG 코드가 길어서 충분히 확보 |
| `LLM_CONCURRENCY_LIMIT` | 동시 LLM 호출 제한 (테이블 보강 등 병렬 호출) | `3` | `3` — API rate limit 방어 |

---

## 3. 임베딩 모델

| 키 | 설명 | 값 예시 | 권장값 |
| --- | --- | --- | --- |
| `EMBEDDING_MODEL` | 임베딩 모델명 (HuggingFace ID) | `BAAI/bge-m3` | `BAAI/bge-m3` — Dense+Sparse 하이브리드 지원 |
| `EMBEDDING_DIM` | Dense 벡터 차원 수 | `1024` | `1024` — BGE-M3 기본 차원 |
| `EMBEDDING_USE_FP16` | FP16 반정밀도 사용 여부 | `true` \| `false` | GPU: `true`, CPU: `false` (필수) |
| `EMBEDDING_CACHE_PATH` | 오프라인 모델 캐시 경로 | `/models/embedding` \| (빈값) | 폐쇄망: 사전 다운로드 경로 지정, 온라인: 빈값 (자동 다운로드) |
| `EMBEDDING_BATCH_SIZE` | 임베딩 배치 크기 | `64` | `64` — GPU 메모리에 따라 조정 |

---

## 4. Reranker

### 4.1 기본 설정

| 키 | 설명 | 값 예시 | 권장값 |
| --- | --- | --- | --- |
| `RERANKER_ENABLED` | Reranker 활성화 여부 | `true` \| `false` | `true` — sql_history 검색 품질에 큰 영향 |
| `RERANKER_MODEL` | Reranker 모델명 (HuggingFace ID) | `BAAI/bge-reranker-v2-m3` | `BAAI/bge-reranker-v2-m3` |
| `RERANKER_USE_FP16` | FP16 반정밀도 사용 여부 | `true` \| `false` | GPU: `true`, CPU: `false` (필수) |
| `RERANKER_CACHE_PATH` | 오프라인 모델 캐시 경로 | `/models/reranker` \| (빈값) | 폐쇄망: 사전 다운로드 경로 지정 |
| `RERANKER_TOP_K` | 재순위 후 최종 반환 건수 | `10` | `10` |

### 4.2 CPU 최적화 (ONNX Runtime)

| 키 | 설명 | 값 예시 | 권장값 |
| --- | --- | --- | --- |
| `RERANKER_BACKEND` | 추론 백엔드 | `pytorch` \| `onnx` | CPU: `onnx` (2~4배 가속), GPU: `pytorch` |
| `RERANKER_ONNX_PATH` | ONNX 모델 파일 경로 | `/models/reranker.onnx` \| (빈값) | 빈값이면 PyTorch 모델에서 자동 변환 (최초 1회) |
| `RERANKER_QUANTIZE` | INT8 동적 양자화 활성화 | `true` \| `false` | `true` — CPU 2.5~3.5배 가속, 품질 손실 1% 미만 |
| `RERANKER_CPU_THREADS` | CPU 스레드 수 | `0` \| `4` \| `8` | `0` (자동 감지). 서버: 물리코어수, 개발PC: 물리코어수/2 |
| `RERANKER_SCORE_THRESHOLD` | 사전 필터링 벡터 스코어 하한 (0.0=비활성화) | `0.0` \| `0.15` \| `0.25` | 초기: `0.0`, 튜닝 후: `0.15~0.25` |
| `RERANKER_MIN_CANDIDATES` | 사전 필터링 후 최소 보장 후보 수 | `15` | `15` — 필터링이 너무 공격적이면 recall 저하 방지 |

---

## 5. 파이프라인 제어

| 키 | 설명 | 값 예시 | 권장값 |
| --- | --- | --- | --- |
| `USE_DUMMY` | Dummy 모드 (내장 샘플 데이터로 동작) | `true` \| `false` | 개발: `true` (인프라 불필요), 운영: `false` |
| `SQL_MAX_RETRY` | SQL 검증 실패 시 재생성 최대 횟수 | `2` | `2` — 3회 이상은 동일 오류 반복 가능성 높음 |
| `CLARIFICATION_MAX_TURNS` | 명확화 질문 최대 왕복 횟수 | `2` | `2` — 3회 이상은 사용자 피로도 증가 |
| `MAX_INPUT_LENGTH` | 사용자 입력 최대 문자 수 | `500` | `500` — DoS 방어 + 프롬프트 토큰 절약 |
| ~~`MAX_SESSIONS`~~ | 제거됨 (세션 스토어 제거) | — | — |
| `MIN_DESCRIPTION_LENGTH` | 테이블 설명 보강 판단 최소 길이 | `20` | `20` — 20자 미만이면 LLM 보강 실행 |
| `MIN_ROWS_FOR_VISUALIZATION` | 시각화 판단 최소 행 수 | `3` | `3` — 2행 이하는 차트 의미 없음 |
| `FORMAT_MAX_ROWS` | 포맷팅 프롬프트에 포함할 최대 행 수 | `50` | `50` — 프롬프트 토큰 제한 대응 |
| `MAX_QUERY_ROWS` | SQL 실행 결과 최대 행 수 | `10000` | `10000` — 대량 덤프 방지 |

---

## 6. 질의 정규화

| 키 | 설명 | 값 예시 | 권장값 |
| --- | --- | --- | --- |
| `NORMALIZATION_ENABLED` | 8-Slot 질의 정규화 활성화 여부 | `true` \| `false` | `true` — SQL 생성 정확도 향상의 핵심 |
| `NORMALIZATION_PHASE2_ENABLED` | Phase 2 교차 검증 활성화 | `true` \| `false` | 소형 LLM: `true` (품질 보완), 대형 모델: `false` (비용 절약) |
| `NORMALIZATION_MAX_TOKENS` | 정규화 LLM 응답 max_tokens | `3000` | `3000` — 8-Slot JSON이 길어질 수 있음 |

---

## 7. PostgreSQL

### 7.1 테스트 DB (개발/테스트용 — 폐쇄망 전환 시 제거)

| 키 | 설명 | 값 예시 | 권장값 |
| --- | --- | --- | --- |
| `TEST_DB_HOST` | 호스트 | `localhost` \| `db.internal` | 환경별 상이 |
| `TEST_DB_PORT` | 포트 | `5432` | `5432` |
| `TEST_DB_NAME` | 데이터베이스명 | `test_db` | 환경별 상이 |
| `TEST_DB_USER` | 사용자 (읽기 전용 계정) | `readonly_user` | SELECT 권한만 부여된 계정 사용 (보안) |
| `TEST_DB_PASSWORD` | 비밀번호 | | |

### 7.2 공통 PostgreSQL DB (SQL 이력·체크포인터 — 폐쇄망에서도 유지)

| 키 | 설명 | 값 예시 | 권장값 |
| --- | --- | --- | --- |
| `POSTGRES_DB_HOST` | 호스트 | `localhost` | 환경별 상이 |
| `POSTGRES_DB_PORT` | 포트 | `5432` | `5432` |
| `POSTGRES_DB_NAME` | 데이터베이스명 | `postgres_db` | 환경별 상이 |
| `POSTGRES_DB_USER` | 사용자 | `postgres_user` | |
| `POSTGRES_DB_PASSWORD` | 비밀번호 | | |

### 7.3 DB 타임아웃

| 키 | 설명 | 값 예시 | 권장값 |
| --- | --- | --- | --- |
| `DB_POOL_TIMEOUT` | 커넥션 풀 대기 타임아웃 (초) | `30` | `30` |
| `DB_QUERY_TIMEOUT` | 쿼리 실행 타임아웃 (초) | `60` | `60` — 복잡한 집계 쿼리 대응 |

---

## 8. ElasticSearch

| 키 | 설명 | 값 예시 | 권장값 |
| --- | --- | --- | --- |
| `ES_HOST` | 호스트 | `localhost` | 환경별 상이 |
| `ES_PORT` | 포트 | `9200` | `9200` |
| `ES_USER` | 사용자 | `elastic` | |
| `ES_PASSWORD` | 비밀번호 | | |
| `ES_TABLE_META_INDEX` | 테이블 메타 인덱스명 | `table_meta` | 폐쇄망에서 변경 가능 |
| `ES_REPORT_SQL_INDEX` | 보고서 SQL 인덱스명 | `report_sql` | 폐쇄망에서 변경 가능 |
| `ES_CODE_META_INDEX` | 코드 메타 인덱스명 | `code_meta` | 폐쇄망에서 변경 가능 |
| `ES_TABLE_META_SIZE` | 테이블 메타 검색 결과 수 | `10` | `10` |
| `ES_REPORT_SQL_SIZE` | 보고서 SQL 검색 결과 수 | `5` | `5` |
| `ES_CODE_META_SIZE` | 코드 메타 검색 결과 수 | `20` | `20` — 금융 코드값이 많아 넉넉하게 |
| `ES_REQUEST_TIMEOUT` | ES 검색 요청 타임아웃 (초) | `10` | `10` |

---

## 9. Qdrant

| 키 | 설명 | 값 예시 | 권장값 |
| --- | --- | --- | --- |
| `QDRANT_HOST` | 호스트 | `localhost` | 환경별 상이 |
| `QDRANT_PORT` | REST API 포트 | `6333` | `6333` |
| `QDRANT_SEARCH_TOP_K` | 업무 매뉴얼(biz_manual) 검색 반환 건수 | `3` | `3` |
| `QDRANT_COLLECTION_NAME` | 업무 매뉴얼 컬렉션명 | `biz_manual` | 폐쇄망에서 변경 가능 |
| `QDRANT_SQL_HISTORY_COLLECTION` | SQL 이력 컬렉션명 | `sql_history` | 폐쇄망에서 변경 가능 |
| `QDRANT_SQL_HISTORY_TOP_K` | SQL 이력 Reranker 후 최종 반환 건수 | `10` | `10` |
| `QDRANT_SQL_HISTORY_PREFETCH_LIMIT` | SQL 이력 하이브리드 검색 후보 수 (Reranker 전) | `50` | `50` — Top-K의 5배 이상 권장 |
| `QDRANT_REQUEST_TIMEOUT` | Qdrant 검색 요청 타임아웃 (초) | `10` | `10` |

---

## 10. MongoDB

| 키 | 설명 | 값 예시 | 권장값 |
| --- | --- | --- | --- |
| `MONGO_HOST` | 호스트 | `localhost` | 환경별 상이 |
| `MONGO_PORT` | 포트 | `27017` | `27017` |
| `MONGO_USER` | 사용자 | `mongoadmin` | |
| `MONGO_PASSWORD` | 비밀번호 | | |
| `MONGO_DATABASE` | 데이터베이스명 | `meta_db` | |
| `MONGO_TABLE_META_COLLECTION` | 테이블 메타 컬렉션명 | `table_meta` | |
| `MONGO_CODE_META_COLLECTION` | 코드 메타 컬렉션명 | `code_meta` | |
| `MONGO_BIZ_META_COLLECTION` | 비즈 메타 컬렉션명 | `biz_meta` | |
| `MONGO_REQUEST_TIMEOUT` | 요청 타임아웃 (초) | `10` | `10` |

---

## 11. Redis / 세션

### 11.1 Redis 연결

| 키 | 설명 | 값 예시 | 권장값 |
| --- | --- | --- | --- |
| `REDIS_HOST` | 호스트 | `localhost` | 환경별 상이 |
| `REDIS_PORT` | 포트 | `6379` | `6379` |
| `REDIS_DB` | 데이터베이스 번호 | `0` | `0` |
| `REDIS_PASSWORD` | 비밀번호 (미설정 시 빈값) | | 운영: 비밀번호 설정 권장 |

### 11.2 CancelStore / ActiveRunStore

| 키 | 설명 | 값 예시 | 권장값 |
| --- | --- | --- | --- |
| `REDIS_BACKEND` | CancelStore/ActiveRunStore 백엔드 | `memory` \| `redis` | 단일 워커: `memory`, 멀티 워커: `redis` (원자적 취소/크래시 감지) |
| `ACTIVE_RUN_TTL_SECONDS` | 활성 파이프라인 TTL (초) | `1800` | `1800` (30분) — 워커 크래시 시 자동 만료 |

> **참고**: 대화 이력은 DB(`checkpoint_dc_messages`)를 단일 소스로 사용합니다.
> 세션 스토어(인메모리/Redis)는 더 이상 사용하지 않습니다.

---

## 12. 폐쇄망 전용 DB

> 온라인 개발 환경에서는 주석 상태로 유지. 폐쇄망 배포 시에만 활성화.

### 12.1 Impala (Cloudera CDP 7.1.9)

| 키 | 설명 | 값 예시 | 권장값 |
| --- | --- | --- | --- |
| `IMPALA_HOST` | 호스트 | `impala-server.internal` | |
| `IMPALA_PORT` | 포트 | `21050` | `21050` |
| `IMPALA_AUTH_MECHANISM` | 인증 방식 | `NOSASL` \| `PLAIN` \| `LDAP` \| `GSSAPI` | 금융사 환경에 따라 `LDAP` 또는 `GSSAPI` |
| `IMPALA_USER` | 사용자 | | |
| `IMPALA_PASSWORD` | 비밀번호 | | |
| `IMPALA_USE_SSL` | SSL 사용 여부 | `true` \| `false` | 금융사 정책에 따름 |
| `IMPALA_DATABASE` | 기본 데이터베이스 | `default` | |
| `IMPALA_QUERY_TIMEOUT` | 쿼리 실행 타임아웃 (초) | `60` | `60` |

### 12.2 Hive (3.1.3, HiveServer2)

| 키 | 설명 | 값 예시 | 권장값 |
| --- | --- | --- | --- |
| `HIVE_HOST` | 호스트 | `hive-server.internal` | |
| `HIVE_PORT` | 포트 | `10000` | `10000` |
| `HIVE_AUTH_MECHANISM` | 인증 방식 | `NOSASL` \| `PLAIN` \| `LDAP` \| `GSSAPI` | |
| `HIVE_USER` | 사용자 | | |
| `HIVE_PASSWORD` | 비밀번호 | | |
| `HIVE_USE_SSL` | SSL 사용 여부 | `true` \| `false` | |
| `HIVE_DATABASE` | 기본 데이터베이스 | `default` | |
| `HIVE_QUERY_TIMEOUT` | 쿼리 실행 타임아웃 (초) | `120` | `120` — Hive는 Impala보다 느리므로 여유 있게 |

### 12.3 Sybase IQ (16.1)

| 키 | 설명 | 값 예시 | 권장값 |
| --- | --- | --- | --- |
| `SYBASE_DRIVER` | 드라이버 방식 | `native` \| `odbc` | `native` (sqlanydb) 우선, 실패 시 `odbc` (pyodbc) |
| `SYBASE_HOST` | 호스트 | `sybase-server.internal` | |
| `SYBASE_PORT` | 포트 | `2638` | `2638` |
| `SYBASE_DATABASE` | 데이터베이스명 | | |
| `SYBASE_USER` | 사용자 | | |
| `SYBASE_PASSWORD` | 비밀번호 | | |
| `SYBASE_ODBC_DRIVER` | ODBC 드라이버명 (odbc 방식 전용) | `SQL Anywhere 16` | `odbcinst -q -d`로 확인 |
| `SYBASE_CHARSET` | 문자셋 | `UTF-8` | `UTF-8` |
| `SYBASE_QUERY_TIMEOUT` | 쿼리 실행 타임아웃 (초) | `60` | `60` |

---

## 13. 트래킹

| 키 | 설명 | 값 예시 | 권장값 |
| --- | --- | --- | --- |
| `LANGSMITH_ENABLED` | LangSmith 트레이싱 활성화 | `true` \| `false` | 온라인 개발: `true`, 폐쇄망: `false` (외부 통신 불가) |
| `LANGSMITH_API_KEY` | LangSmith API 키 | `lsv2_pt_...` | LANGSMITH_ENABLED=true 시 필수 |
| `LANGSMITH_PROJECT` | LangSmith 프로젝트명 | `data-copilot` | `data-copilot` |
| `LANGSMITH_ENDPOINT` | LangSmith 엔드포인트 | `https://api.smith.langchain.com` | |
| `EVAL_TRACKER_ENABLED` | 자체 평가 트래커 활성화 (폐쇄망 호환) | `true` \| `false` | `true` — LangSmith 대안, JSON 파일로 저장 |
| `EVAL_TRACKER_OUTPUT_DIR` | 트래커 출력 디렉토리 | `logs/traces` | `logs/traces` |

---

## 14. 애플리케이션

| 키 | 설명 | 값 예시 | 권장값 |
| --- | --- | --- | --- |
| `LOG_LEVEL` | 로그 레벨 | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` | 개발: `DEBUG`, 운영: `INFO` |
| `LOG_FORMAT` | 로그 출력 형식 | `console` \| `json` | 개발: `console` (가독성), 운영: `json` (로그 수집기 호환) |
| `LOG_LONG_VALUE_THRESHOLD` | 로그 미리보기 최대 문자 수 | `80` | `80` |
| `LOG_SEPARATOR_WIDTH` | 로그 구분선 너비 | `72` | `72` |

---

## 15. 시각화 레이아웃

> SVG 차트 기본 크기. 프론트엔드 CSS로 재조정 가능하므로 변경 빈도 낮음.

| 키 | 설명 | 값 예시 | 권장값 |
| --- | --- | --- | --- |
| `CHART_WIDTH` | 차트 너비 (px) | `600` | `600` |
| `CHART_HEIGHT` | 차트 높이 (px) | `400` | `400` |
| `CHART_MARGIN_LEFT` | 좌측 여백 (px, Y축 라벨 공간) | `80` | `80` |
| `CHART_MARGIN_RIGHT` | 우측 여백 (px) | `40` | `40` |
| `CHART_MARGIN_TOP` | 상단 여백 (px, 제목 공간) | `60` | `60` |
| `CHART_MARGIN_BOTTOM` | 하단 여백 (px, X축 라벨 공간) | `70` | `70` |

---

## 16. 금융 도메인 상수

> 한국 금융 표기 고정값. 변경 사유가 없으면 기본값 유지.

| 키 | 설명 | 값 예시 | 권장값 |
| --- | --- | --- | --- |
| `KRW_EOK_THRESHOLD` | 억원 기준 (이 값 이상이면 "~억원" 표기) | `100000000` | `100000000` (1억) |
| `KRW_MAN_THRESHOLD` | 만원 기준 (이 값 이상이면 "~만원" 표기) | `10000` | `10000` (1만) |

---

## 17. Gunicorn (운영 서버)

> Linux 전용. Windows에서는 uvicorn 단독 사용. `pyproject.toml`의 `prod` optional dependency.

| 키 | 설명 | 값 예시 | 권장값 |
| --- | --- | --- | --- |
| `GUNICORN_BIND` | 바인드 주소:포트 | `0.0.0.0:8000` \| `unix:/tmp/gunicorn.sock` | `0.0.0.0:8000` |
| `GUNICORN_WORKERS` | 워커 프로세스 수 | `1` \| `2` \| `4` | `1` (임베딩 모델 메모리 제약, 하단 주의사항 참조) |
| `GUNICORN_TIMEOUT` | 워커 응답 타임아웃(초) | `120` \| `180` | `120` (파이프라인 최대 소요 시간 기준) |

**워커 수 결정 기준**:

- 기본 `1` — asyncio가 단일 워커에서도 충분한 동시 처리량을 제공
- BGE-M3(~570MB) + ONNX Reranker가 워커마다 중복 로드됨. `workers=4`이면 추가 ~2.3GB 필요
- DB 커넥션 풀: `workers × pool_size ≤ DB max_connections` 확인 필수
- 임베딩 모델을 별도 서빙 프로세스로 분리한 후에 워커 수 확장 권장

---

## 부록: 폐쇄망 전환 체크리스트

온라인 환경에서 폐쇄망으로 전환할 때 변경해야 하는 항목:

| # | 키 | 온라인 값 | 폐쇄망 값 |
| --- | --- | --- | --- |
| 1 | `LLM_PROVIDER` | `anthropic` | `openai_compatible` |
| 2 | `LLM_MODEL` | `claude-sonnet-4-*` | 로컬 모델 (7B~70B) |
| 3 | `OPENAI_BASE_URL` | (빈값) | `http://local-llm:8080/v1` |
| 4 | `EMBEDDING_CACHE_PATH` | (빈값) | `/models/embedding` (사전 다운로드) |
| 5 | `RERANKER_CACHE_PATH` | (빈값) | `/models/reranker` (사전 다운로드) |
| 6 | `NORMALIZATION_PHASE2_ENABLED` | `false` | `true` (소형 LLM 품질 보완) |
| 7 | `LLM_PARSE_MAX_RETRY` | `2` | `4` (소형 LLM 포맷 오류 대응) |
| 8 | `LANGSMITH_ENABLED` | `true` | `false` (외부 통신 불가) |
| 9 | `REDIS_BACKEND` | `redis` | `redis` (멀티 워커 시) |
| 10 | `USE_DUMMY` | `false` | `false` |
| 11 | 인프라 호스트 (DB/ES/Qdrant) | `localhost` | 폐쇄망 내부 주소 |

상세 전환 가이드: [migration-guide.md](migration-guide.md)
