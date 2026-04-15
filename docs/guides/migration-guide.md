# 폐쇄망 이관 및 실 데이터소스 연동 가이드

**최종 수정일:** 2026-04-03
**기준 버전:** v0.1.0 (3계층 에이전틱 파이프라인 + 6종 신규 커넥터)
**대상 환경:** 은행 폐쇄망 (인터넷 차단) 내 로컬 PC 또는 서버
**목적:** Dummy 모드 → 실 데이터소스 + 폐쇄망 LLM API 연결 후 정상 동작 테스트

---

## 목차

1. [사전 준비](#1-사전-준비)
2. [패키지 오프라인 이관](#2-패키지-오프라인-이관)
3. [환경 설정 (.env)](#3-환경-설정-env)
4. [LLM API 전환](#4-llm-api-전환-anthropic--폐쇄망-llm)
5. [임베딩 모델 & 리랭커 오프라인 배포](#5-임베딩-모델--리랭커-오프라인-배포)
6. [커넥터 실 모드 전환](#6-커넥터-실-모드-전환)
7. [데이터소스별 연동 작업](#7-데이터소스별-연동-작업)
8. [도메인 데이터 실 데이터 교체](#8-도메인-데이터-실-데이터-교체)
9. [프롬프트 재튜닝](#9-프롬프트-재튜닝)
10. [Docker 이미지 오프라인 빌드](#10-docker-이미지-오프라인-빌드)
11. [동작 테스트](#11-동작-테스트)
12. [유의사항 체크리스트](#12-유의사항-체크리스트)
13. [향후 개선사항](#13-향후-개선사항)
14. [파일별 수정 체크리스트](#부록-파일별-수정-체크리스트)

---

## 1. 사전 준비

### 1-1. 폐쇄망 내 필수 인프라 확인

```text
실 데이터소스 연결을 위해 다음 인프라가 폐쇄망 내에서 접근 가능한지 확인한다.
각 서비스의 호스트:포트를 사전에 확보해야 한다.
```

| 인프라 | 용도 | 확인 항목 |
| ------ | ---- | --------- |
| **PostgreSQL (정보계)** | 실 데이터 추출 대상 DB (읽기 전용) | 호스트, 포트, DB명, **읽기 전용** 계정/비밀번호 |
| **PostgreSQL (이력)** | 과거 SQL 실행 이력 저장 | 호스트, 포트, DB명, 계정/비밀번호 |
| **MongoDB** | 테이블/컬럼/코드/용어사전 메타 (메타 단일 소스, 2026-04 ES 제거 후 통합) | 호스트, 포트, 인증 정보, DB명 |
| **Qdrant** | 업무 매뉴얼 + SQL 이력 벡터 검색 | 호스트, 포트 |
| **Neo4j** | 온톨로지 그래프 (테이블 관계, JOIN 경로, 산출식) | 호스트, 포트, 인증 정보, DB명 |
| **Redis** | 세션 캐시 (선택) | 호스트, 포트 |
| **폐쇄망 LLM API** | Claude 대체 LLM 서비스 | 엔드포인트 URL, API 키, 모델명 |
| **ADW (Sybase IQ)** | ADW 정보계 (폐쇄망 전용, `adw_connector.py`) | 호스트, 포트, DB명, 계정 |
| **BDP (Impala)** | BDP 빅데이터 (폐쇄망 전용, `bdp_connector.py`, LDAP 인증) | 호스트, 포트, LDAP 인증 정보 |
| **CRP** | CRP 플랫폼 (폐쇄망 전용, `crp_connector.py`) | 호스트, 포트, 인증 정보 |

### 1-2. 폐쇄망 PC 환경 확인

```bash
# Python 3.12 이상 설치 여부 확인
python --version
# 결과: Python 3.12.x 이상이어야 함

# uv 설치 여부 확인 (인터넷 환경에서 미리 설치)
python -m uv --version
```

### 1-3. 현재 시스템 아키텍처 개요

```text
v0.1.0 기준 시스템은 다음 구성 요소로 이루어진다.
이전 버전 대비 MongoDB, Neo4j, Reranker, Impala, Hive, Sybase IQ 커넥터가 신규 추가되었다.

┌─────────────────────────────────────────────────────────────────┐
│                     Data Copilot Server                        │
│  FastAPI + WebSocket (src/main.py)                            │
│                                                                │
│  ┌─── 3계층 파이프라인 (LangGraph) ───────────────────────┐   │
│  │  Interpret → Reason (에이전틱 루프) → Present          │   │
│  └────────────────────────────────────────────────────────┘   │
│                                                                │
│  ┌─── 커넥터 (10종) ─────────────────────────────────────┐   │
│  │  LLM Client (Anthropic / OpenAI Compatible)            │   │
│  │  MongoDB (메타 단일 소스, 2026-04 ES 제거)             │   │
│  │  Qdrant (매뉴얼 + SQL이력, BGE-M3 임베딩)             │   │
│  │  Neo4j (온톨로지 그래프)                                │   │
│  │  PostgreSQL (정보계 RO + 이력 RW + 체크포인터)         │   │
│  │  ADW / BDP / CRP / Hive (폐쇄망 전용)                 │   │
│  │  BGE-Reranker (Cross-Encoder, ONNX 최적화)            │   │
│  │  Redis (세션 캐시)                                     │   │
│  └────────────────────────────────────────────────────────┘   │
│                                                                │
│  ┌─── 설정 계층 ─────────────────────────────────────────┐   │
│  │  resources/domain/*.yaml (도메인 설정)                  │   │
│  │  resources/prompts/**/*.txt (프롬프트 3계층)            │   │
│  │  resources/connectors/**/* (쿼리 템플릿)               │   │
│  │  .env (환경변수 기반 전환)                              │   │
│  └────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. 패키지 오프라인 이관

### 2-1. 인터넷 환경에서 패키지 다운로드

```bash
# 인터넷이 되는 PC에서 실행한다.
cd data-copilot

# uv.lock을 requirements.txt로 변환한다.
python -m uv export --frozen --no-hashes -o requirements.txt

# 폐쇄망 PC와 동일한 플랫폼용 wheel 파일을 packages/ 디렉토리에 다운로드한다.
# --platform: 폐쇄망 PC OS에 맞게 변경 (예: win_amd64, manylinux2014_x86_64)
pip download -r requirements.txt -d packages/ \
    --platform win_amd64 --python-version 3.12 --only-binary=:all:

# uv 자체도 wheel로 준비
pip download uv -d packages/ \
    --platform win_amd64 --python-version 3.12 --only-binary=:all:
```

> **주요 의존성 (v0.1.0 기준, pyproject.toml 참조):**
>
> | 범주 | 패키지 |
> | ---- | ------ |
> | LLM/Agent | `anthropic>=0.40.0`, `openai>=1.0.0`, `langgraph>=0.2.0`, `langchain-core>=0.3.0` |
> | Web | `fastapi>=0.115.0`, `uvicorn[standard]>=0.32.0`, `websockets>=13.0` |
> | DB 드라이버 | `asyncpg>=0.30.0`, `psycopg2-binary>=2.9.0`, `motor>=3.6.0`, `neo4j>=5.20.0` |
> | 검색/벡터 | `qdrant-client>=1.12.0` (ElasticSearch는 2026-04 제거) |
> | 임베딩/리랭킹 | `FlagEmbedding>=1.3.0`, `transformers>=4.45.0,<5.0.0`, `torch>=2.4.0`, `onnxruntime>=1.18.0` |
> | SQL 파싱 | `sqlglot>=25.0.0` |
> | 폐쇄망 DB | `impyla>=0.20.0`, `thrift==0.16.0`, `pyodbc>=5.0.0`, `sqlanydb>=1.0.13` |
> | 유틸 | `pydantic>=2.0.0`, `redis>=5.0.0`, `structlog>=24.0.0`, `psutil>=5.9.0` |

### 2-2. ML 모델 파일 사전 다운로드

```bash
# v0.1.0에서는 3개의 ML 모델이 필요하다.
# 인터넷 환경에서 미리 다운로드하여 폐쇄망에 이관해야 한다.

# 1) BGE-M3 임베딩 모델 (1024차원, Dense+Sparse 하이브리드)
#    Qdrant 벡터 검색에 사용 (이전 MiniLM-L12 384차원에서 변경됨)
python -c "
from FlagEmbedding import BGEM3FlagModel
model = BGEM3FlagModel('BAAI/bge-m3', use_fp16=False)
print('BGE-M3 다운로드 완료:', model.model.config._name_or_path)
"

# 2) BGE-Reranker-v2-m3 (Cross-Encoder 재순위 모델)
#    벡터 검색 Top-N 결과를 정밀 재순위
python -c "
from FlagEmbedding import FlagReranker
reranker = FlagReranker('BAAI/bge-reranker-v2-m3', use_fp16=False)
print('BGE-Reranker 다운로드 완료')
"

# 3) 모델 캐시 디렉토리 확인 — 이 디렉토리를 통째로 폐쇄망에 복사한다
# Windows: %USERPROFILE%\.cache\huggingface\hub\
# Linux:   ~/.cache/huggingface/hub/
ls ~/.cache/huggingface/hub/ | grep -E "bge-m3|bge-reranker"
```

### 2-3. USB/보안매체로 이관

| 이관 대상 | 설명 |
| --------- | ---- |
| `data-copilot/` 프로젝트 전체 | 소스코드, pyproject.toml, uv.lock 포함 |
| `packages/` 디렉토리 | 오프라인 설치용 wheel 파일 전체 |
| HuggingFace 모델 캐시 디렉토리 | BGE-M3 + BGE-Reranker 모델 파일 |
| Python 3.12+ 설치 파일 | 폐쇄망 PC에 Python 미설치 시 |
| Docker 이미지 tar (선택) | Qdrant+MongoDB+Neo4j+Redis+PostgreSQL |
| `deploy/offline-bundle/` 산출물 | uv 기반 오프라인 wheel 번들 + 모델 파일 |

### 2-4. 폐쇄망 PC에서 오프라인 설치

```bash
cd data-copilot

# uv를 먼저 설치한다.
pip install --no-index --find-links=packages/ uv

# uv로 프로젝트 의존성을 오프라인 설치한다.
python -m uv sync --all-extras --find-links=packages/ --no-index

# 설치 확인 — 주요 패키지가 정상 설치되었는지 점검한다.
python -m uv run python -c "
import anthropic, fastapi, sqlalchemy, motor, neo4j
from FlagEmbedding import BGEM3FlagModel
print('OK: 모든 핵심 패키지 설치 확인')
"
```

> **유의:** `--find-links`와 `--no-index` 조합으로 완전한 오프라인 설치를 지원한다.
> 단, 디렉토리에 **모든** 의존성(전이 의존성 포함)의 wheel이 있어야 한다.
> C 확장 패키지(`asyncpg`, `torch`, `onnxruntime`, `grpcio` 등)는 OS/아키텍처별
> 바이너리 wheel이 다르므로 폐쇄망 PC와 동일 환경에서 다운로드해야 한다.

---

## 3. 환경 설정 (.env)

### 3-1. .env 파일 작성

```bash
# .env.example을 복사하여 실 환경 값으로 수정한다.
cp .env.example .env
```

```dotenv
# === .env — 폐쇄망 실 환경 설정 (v0.1.0) ===

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LLM API
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
LLM_PROVIDER=openai_compatible                    # 폐쇄망은 보통 openai_compatible
LLM_MODEL=폐쇄망-모델명                            # 예: solar-pro-2-70b, qwen3.5-397b
OPENAI_API_KEY=폐쇄망-api-key
OPENAI_BASE_URL=https://internal-llm.bank.co.kr/v1

# LLM 파싱 재시도 (폐쇄망 모델 대응)
LLM_PARSE_MAX_RETRY=3                             # 포맷 불일치 시 재시도 (기본 2, 폐쇄망 3~5 권장)
LLM_TRANSPORT_MAX_RETRY=3                         # SDK 레벨 429/500/503 재시도

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 더미 모드 & 배포 모드
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
USE_DUMMY=false                                    # false: 실 데이터소스 연결
DEPLOYMENT_MODE=internal                           # external: PostgreSQL / internal: ADW+BDP 라우팅

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 임베딩 & 리랭커
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
EMBEDDING_MODEL=BAAI/bge-m3                        # 1024차원 Dense+Sparse 하이브리드
EMBEDDING_CACHE_PATH=/opt/models/bge-m3            # 오프라인 모델 캐시 경로
EMBEDDING_USE_FP16=false                           # CPU 환경은 false

RERANKER_ENABLED=true
RERANKER_MODEL=BAAI/bge-reranker-v2-m3
RERANKER_BACKEND=onnx                              # CPU 최적화: onnx | pytorch
RERANKER_QUANTIZE=true                             # INT8 동적 양자화 (75% 모델 크기 절감)
RERANKER_CACHE_PATH=/opt/models/bge-reranker       # 오프라인 모델 캐시 경로
RERANKER_CPU_THREADS=0                             # 0=자동감지(물리 코어 수)
RERANKER_TOP_K=5                                   # 최종 리랭킹 결과 수

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 정보계 DB (읽기 전용)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
INFO_DB_HOST=10.xx.xx.xx
INFO_DB_PORT=5432
INFO_DB_NAME=dw
INFO_DB_USER=data_copilot_ro                       # 반드시 SELECT 전용 읽기 전용 계정
INFO_DB_PASSWORD=실제비밀번호

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SQL 이력 DB
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HISTORY_DB_HOST=10.xx.xx.xx
HISTORY_DB_PORT=5432
HISTORY_DB_NAME=data_copilot_hist
HISTORY_DB_USER=history_user
HISTORY_DB_PASSWORD=실제비밀번호

# ElasticSearch — 제거됨(2026-04). 모든 메타는 MongoDB, SQL이력은 Qdrant로 통합됨.

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MongoDB (메타 단일 소스)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MONGO_HOST=10.xx.xx.xx
MONGO_PORT=27017
MONGO_USER=mongoadmin
MONGO_PASSWORD=실제비밀번호
MONGO_DATABASE=meta_db

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Qdrant (벡터 검색)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
QDRANT_HOST=10.xx.xx.xx
QDRANT_PORT=6333

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Neo4j (온톨로지 그래프 — 신규)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NEO4J_HOST=10.xx.xx.xx
NEO4J_PORT=7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=실제비밀번호
NEO4J_DATABASE=neo4j

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Redis (세션 캐시)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
REDIS_HOST=10.xx.xx.xx
REDIS_PORT=6379
REDIS_DB=0
SESSION_BACKEND=redis                              # memory | redis

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 폐쇄망 DB (DEPLOYMENT_MODE=internal 일 때만 사용)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Sybase IQ (ADW 정보계)
SYBASE_DRIVER=native                               # native | odbc
SYBASE_HOST=10.xx.xx.xx
SYBASE_PORT=2638
SYBASE_DATABASE=실제DB명
SYBASE_USER=실제계정
SYBASE_PASSWORD=실제비밀번호
# SYBASE_ODBC_DRIVER="SQL Anywhere 16"             # odbc 방식일 때만

# Impala (BDP 빅데이터)
IMPALA_HOST=10.xx.xx.xx
IMPALA_PORT=21050
IMPALA_AUTH_MECHANISM=LDAP                          # LDAP | PLAIN | NOSASL | GSSAPI
IMPALA_USER=실제계정
IMPALA_PASSWORD=실제비밀번호
IMPALA_DATABASE=BDPOWN

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 기타
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LangSmith — 제거됨(2026-04). 트레이싱은 src/utils/tracker/ 자체 구현 사용.
EVAL_TRACKER_ENABLED=true
LOG_LEVEL=INFO                                     # 초기 연동 테스트 시 DEBUG 권장
LOG_FORMAT=json                                    # 폐쇄망에서는 json 포맷 권장
MAX_QUERY_ROWS=10000
```

### 3-2. 환경변수 검증 스크립트

```bash
python -m uv run python -c "
from src.config import settings

# 프로바이더에 따른 LLM 키 체크
if settings.llm_provider == 'anthropic':
    llm_key_name = 'ANTHROPIC_API_KEY'
    llm_key_value = settings.anthropic_api_key
else:
    llm_key_name = 'OPENAI_API_KEY'
    llm_key_value = settings.openai_api_key

checks = {
    'LLM_PROVIDER':      settings.llm_provider,
    llm_key_name:        llm_key_value,
    'LLM_MODEL':         settings.llm_model,
    'USE_DUMMY':         str(settings.use_dummy),
    'DEPLOYMENT_MODE':   settings.deployment_mode,
    'INFO_DB_HOST':      settings.info_db_host,
    'ES_HOST':           settings.es_host,
    'MONGO_HOST':        settings.mongo_host,
    'QDRANT_HOST':       settings.qdrant_host,
    'NEO4J_HOST':        settings.neo4j_host,
    'EMBEDDING_MODEL':   settings.embedding_model,
    'RERANKER_ENABLED':  str(settings.reranker_enabled),
}

if settings.llm_provider == 'openai_compatible':
    checks['OPENAI_BASE_URL'] = settings.openai_base_url

if settings.deployment_mode == 'internal':
    checks['SYBASE_HOST'] = settings.sybase_host
    checks['IMPALA_HOST'] = settings.impala_host

for k, v in checks.items():
    v_str = str(v) if v else ''
    status = 'PASS' if v_str and 'your-' not in v_str and v_str != 'localhost' else 'WARN'
    display = v_str[:30] + '...' if len(v_str) > 30 else v_str
    print(f'  [{status}] {k} = {display}')
"
```

---

## 4. LLM API 전환 (Anthropic → 폐쇄망 LLM)

### 4-1. 현재 아키텍처 — 통합 LLM 클라이언트

```text
src/utils/llm/client.py 에 UnifiedLLMClient 래퍼가 구현되어 있어,
.env 의 LLM_PROVIDER 설정만 변경하면 코드 수정 없이 프로바이더를 전환할 수 있다.
모든 노드는 client.messages.create() 통합 인터페이스만 호출하므로
프로바이더 변경이 노드 코드에 영향을 주지 않는다.

v0.1.0에서 추가된 기능:
- Thinking 모드 지원: Gemini(reasoning_effort), Qwen(enable_thinking) 자동 대응
- Qwen <think> 태그 자동 제거: _strip_thinking_tags()
- 노드별 Thinking 모드 제어: src/agents/nodes/thinking_modes.py
- LLM 호출 메트릭 자동 추적: prompt_tokens, response_tokens, latency
```

| 폐쇄망 LLM 유형 | LLM_PROVIDER 값 | 코드 수정 | 설명 |
| --------------- | --------------- | --------- | ---- |
| **Anthropic API 호환** (AWS Bedrock, 사내 프록시) | `anthropic` | **없음** | ANTHROPIC_API_KEY 만 설정 |
| **OpenAI API 호환** (vLLM, TGI, Ollama 등) | `openai_compatible` | **없음** | OPENAI_API_KEY + OPENAI_BASE_URL 설정 |
| **독자 API** (자체 개발 LLM 서비스) | — | client.py에 프로바이더 추가 | UnifiedLLMClient에 새 Messages 클래스 작성 |

### 4-2. .env 설정 예시

```dotenv
# ── [유형 A] Anthropic API 호환 (사내 프록시) ──
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-xxxxx
LLM_MODEL=claude-sonnet-4-20250514

# ── [유형 B] OpenAI API 호환 (vLLM, TGI 등 — 가장 일반적) ──
LLM_PROVIDER=openai_compatible
OPENAI_API_KEY=폐쇄망-api-key
OPENAI_BASE_URL=https://internal-llm.bank.co.kr/v1
LLM_MODEL=solar-pro-2-70b

# ── 포맷 재시도 횟수 ──
LLM_PARSE_MAX_RETRY=3   # 폐쇄망 모델은 3~5 권장
```

### 4-3. LLM 응답 포맷 파싱 재시도 메커니즘

```text
폐쇄망 모델이 지정된 출력 포맷(JSON, 2-line 등)을 준수하지 못할 수 있다.
src/utils/llm/retry.py 의 llm_call_with_parse_retry() 가 자동 재시도한다.

재시도 전략:
  1차: 원본 프롬프트로 LLM 호출
  2차~: [이전 LLM 응답] + [포맷 교정 힌트] 를 대화에 추가하여 재호출
  최종 실패: 노드별 안전한 기본값으로 폴백

적용 노드 (v0.1.0 기준):
  - context_classifier    — 이력 해소 + 의도 분류 통합 파싱
  - query_normalizer      — 8-Slot 정규화 JSON 파싱 (2-Phase)
  - sql_generator         — SQL 생성 + 근거 파싱
  - sql_validator (L2b)   — 의미 검증 판정 파싱
  - knowledge_interpreter — 검색 결과 해석 JSON 파싱
  - recovery_agent        — 복구 계획 JSON 파싱
  - analyzer              — 데이터 분석 + 시각화 판단 + SVG 생성
  - formatter             — 보고서 포맷팅

제어 환경변수:
  LLM_PARSE_MAX_RETRY (기본 2, 폐쇄망 3~5 권장)
  LLM_TRANSPORT_MAX_RETRY (SDK 레벨 429/500/503 재시도, 기본 3)
```

### 4-4. Thinking 모드 제어 (Qwen/Gemini 대응)

```text
src/agents/nodes/thinking_modes.py 에서 노드별 Thinking 모드를 제어한다.
폐쇄망 모델(Qwen3.5 등) 사용 시 Thinking 모드 호환성을 확인해야 한다.

현재 설정:
  sql_generator:          "high"   (최대 추론 — SQL 정확도 최우선)
  knowledge_interpreter:  "auto"   (검색 결과 해석)
  query_normalizer:       "auto"   (질의 정규화)
  그 외 노드:             "off"    (분류/포맷팅 등 단순 작업)

모델별 Thinking 파라미터 자동 변환 (src/utils/llm/client.py):
  Gemini → reasoning_effort ("none"/"low"/"medium"/"high")
  Qwen   → extra_body.chat_template_kwargs.enable_thinking (bool)
  기타   → 무시 (파라미터 미전달)

Qwen Thinking 모드 사용 시:
  - <think>...</think> 태그가 응답에 포함될 수 있음
  - _strip_thinking_tags() 에서 자동 제거됨
  - 모델이 thinking을 지원하지 않으면 오류 없이 무시
```

### 4-5. LLM 연결 테스트

```bash
python -m uv run python -c "
import asyncio
from src.utils.llm.client import get_llm_client
from src.config import settings

async def test():
    print(f'프로바이더: {settings.llm_provider}')
    print(f'모델: {settings.llm_model}')

    client = get_llm_client()
    response = await client.messages.create(
        model=settings.llm_model,
        max_tokens=100,
        system='테스트입니다.',
        messages=[{'role': 'user', 'content': '안녕하세요'}],
    )
    print('LLM 응답:', response.content[0].text)
    print(f'토큰: prompt={response.usage.input_tokens}, '
          f'response={response.usage.output_tokens}')

asyncio.run(test())
"
```

### 4-6. LLM 포맷 준수율 향상 팁

```text
폐쇄망 모델이 출력 포맷을 잘 지키지 못하는 경우:

1. temperature=0 사용 — 구조화된 출력에는 창의성이 불필요
2. max_tokens 적절 설정 — 불필요한 텍스트 생성 억제
3. LLM_PARSE_MAX_RETRY 상향 — 3~5회로 설정
4. JSON Mode 활용 — 일부 API는 response_format={"type": "json_object"} 지원
   (필요 시 client.py의 OpenAICompatibleMessages에 추가)
5. 프롬프트 단순화 — resources/prompts/ 에서 출력 형식 지시를 더 명시적으로 변경
6. few-shot 예제 강화 — 프롬프트에 출력 형식 예제 추가
```

---

## 5. 임베딩 모델 & 리랭커 오프라인 배포

### 5-1. 임베딩 모델 (BGE-M3)

```text
v0.1.0에서 임베딩 모델이 변경되었다.
  이전: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 (384차원)
  현재: BAAI/bge-m3 (1024차원, Dense+Sparse 하이브리드)

BGE-M3는 Dense 벡터와 Sparse 벡터를 동시에 생성하여
Qdrant의 하이브리드 검색(RRF 퓨전)에 활용된다.
```

| 항목 | 위치 | 설정 |
| ---- | ---- | ---- |
| 모델명 | `src/config.py:145` | `embedding_model` = "BAAI/bge-m3" |
| 차원 | `src/config.py:146` | `embedding_dim` = 1024 |
| FP16 | `src/config.py:147` | `embedding_use_fp16` = false (CPU) |
| 캐시 경로 | `src/config.py:148` | `embedding_cache_path` |
| 로딩 위치 | `src/connectors/impl/qdrant_connector.py` | lazy-load on first search |
| 인코딩 | `qdrant_connector.py:141-238` | `encode()` Dense+Sparse, `encode_dense_only()` Dense |

**오프라인 배포 절차:**

```bash
# 1) 인터넷 환경에서 모델 다운로드 + 캐시 디렉토리 확인
python -c "
from FlagEmbedding import BGEM3FlagModel
model = BGEM3FlagModel('BAAI/bge-m3', use_fp16=False)
# 캐시 위치: ~/.cache/huggingface/hub/models--BAAI--bge-m3/
"

# 2) 캐시 디렉토리를 폐쇄망 서버의 지정 경로에 복사
#    예: /opt/models/bge-m3/

# 3) .env에 캐시 경로 설정
# EMBEDDING_CACHE_PATH=/opt/models/bge-m3
```

> **주의사항:**
>
> - 시딩 스크립트(`seed_qdrant.py`)와 런타임(`qdrant_connector.py`)의 모델이 반드시 일치해야 한다.
> - 모델 변경 시 Qdrant 컬렉션 재생성 + 전체 데이터 재임베딩 필수
> - FP16은 GPU 환경에서만 사용 (`EMBEDDING_USE_FP16=true`)

### 5-2. 리랭커 (BGE-Reranker-v2-m3)

```text
v0.1.0에서 신규 추가된 Cross-Encoder 리랭커이다.
벡터 검색 Top-N 결과를 정밀 재순위하여 SQL 이력 검색 품질을 향상시킨다.

4단계 CPU 최적화 (누적 4~6배):
  1. ONNX Runtime O3 그래프 최적화 (1.5~2.0x)
  2. INT8 동적 양자화 (누적 2.5~3.5x)
  3. 입력 길이 정렬 패딩 감소 (누적 2.8~4.0x)
  4. 벡터 스코어 기반 사전 필터링 (누적 4~6x)
```

| 항목 | 위치 | 설정 |
| ---- | ---- | ---- |
| 활성화 | `src/config.py:152` | `reranker_enabled` = true |
| 모델명 | `src/config.py:153` | `reranker_model` = "BAAI/bge-reranker-v2-m3" |
| 백엔드 | `src/config.py:160` | `reranker_backend` = "onnx" (CPU 최적화) |
| 양자화 | `src/config.py:163` | `reranker_quantize` = true (INT8) |
| CPU 스레드 | `src/config.py:165` | `reranker_cpu_threads` = 0 (자동감지) |
| 캐시 경로 | `src/config.py:156` | `reranker_cache_path` |
| ONNX 경로 | `src/config.py:162` | `reranker_onnx_path` |
| 구현 | `src/connectors/impl/reranker.py` | ONNX/PyTorch 백엔드 선택 |

**오프라인 배포 절차:**

```bash
# 1) 인터넷 환경에서 모델 다운로드
python -c "
from FlagEmbedding import FlagReranker
reranker = FlagReranker('BAAI/bge-reranker-v2-m3', use_fp16=False)
"

# 2) ONNX 변환 + INT8 양자화 (선택, 성능 최적화)
python -c "
from src.connectors.impl.reranker import Reranker
# 초기화 시 ONNX 자동 변환 수행
r = Reranker()
print('ONNX 모델 생성 완료')
"

# 3) 캐시 디렉토리를 폐쇄망 서버에 복사
# RERANKER_CACHE_PATH=/opt/models/bge-reranker

# 4) ONNX 모델 파일 경로도 함께 설정 (선택)
# RERANKER_ONNX_PATH=/opt/models/bge-reranker/model_quantized.onnx
```

> **Sybase IQ 드라이버 요구사항 (native 방식):**
>
> - `sqlanydb` 패키지는 `libdbcapi_r.so` (Linux) 또는 `dbcapi.dll` (Windows) 필요
> - SAP SQL Anywhere 16 클라이언트 설치 필요
>
> **Sybase IQ 드라이버 요구사항 (ODBC 방식):**
>
> - `pyodbc` + unixODBC + SAP Sybase IQ ODBC 드라이버 (`libdbodbc16_r.so`)
> - `odbcinst.ini`에 드라이버 등록 필요
>
> **Impala 드라이버 요구사항:**
>
> - `impyla>=0.20.0` (0.18-0.19는 Python 3.12 버그 있음)
> - LDAP 인증: `pure-sasl>=0.6.2`
> - Kerberos 인증 (선택): `kerberos>=1.3.0` + OS `krb5-devel` 패키지

---

## 6. 커넥터 실 모드 전환

### 6-1. USE_DUMMY 환경변수

```text
v0.1.0에서 use_dummy가 Settings에 통합되었다.
.env의 USE_DUMMY=false 설정만으로 모든 커넥터가 실 모드로 전환된다.

  src/config.py:213   →  use_dummy: bool = True
  src/main.py          →  get_connector_manager() — settings.use_dummy 자동 참조
  src/agents/graph/runner.py  →  동일

코드 수정 없이 .env만 변경하면 된다.
```

```dotenv
# .env에 설정:
USE_DUMMY=false              # 실 데이터소스 연결
```

### 6-2. DEPLOYMENT_MODE (DB 라우팅)

```text
v0.1.0에서 신규 추가된 배포 모드 설정이다.
테이블명 접두어에 따라 적절한 DB 커넥터를 자동 라우팅한다.

  src/config.py:215   →  deployment_mode: str = "external"
  src/connectors/manager.py:141-197  →  parse_db_source() + get_query_db()
```

| DEPLOYMENT_MODE | 라우팅 규칙 | 사용 커넥터 |
| --------------- | ----------- | ----------- |
| `external` | 모든 테이블 → PostgreSQL 정보계 | InfoDBConnector |
| `internal` | `TB_ADW_*` → Sybase IQ, `TB_BDP_*` → Impala, 기타 → PostgreSQL | SybaseIQConnector, ImpalaConnector, InfoDBConnector |

```dotenv
# 폐쇄망 환경에서 ADW/BDP를 사용하는 경우:
DEPLOYMENT_MODE=internal

# 폐쇄망이지만 PostgreSQL만 사용하는 경우:
DEPLOYMENT_MODE=external
```

### 6-3. 전체 커넥터 연결 테스트

```bash
python -m uv run python -c "
import asyncio
from src.connectors.manager import get_connector_manager

async def test():
    manager = get_connector_manager()
    await manager.connect_all()

    status = await manager.health_check_all()
    for name, ok in status.items():
        print(f'  [{\"PASS\" if ok else \"FAIL\"}] {name}')

    await manager.disconnect_all()

asyncio.run(test())
"
# 기대 결과: 모든 커넥터 PASS
# (deployment_mode=internal 이면 adw_db, bigdata_db도 체크)
```

---

## 7. 데이터소스별 연동 작업

### 7-1. MongoDB — 메타데이터 단일 소스 (2026-04 ES 제거)

```text
v0.1.0에서 메타데이터 주 소스가 ElasticSearch → MongoDB로 변경되었고,
2026-04에는 ElasticSearch가 완전히 제거되어 MongoDB가 메타 단일 소스가 되었다.
테이블/컬럼 메타, 코드값, 용어사전은 모두 MongoDB에서 관리한다.
과거 보고서 SQL/SQL 이력은 Qdrant(`sql_history` 컬렉션, 하이브리드 + Reranker)로 이전되었다.
```

#### MongoDB 스키마 초기화

```bash
# 1) MongoDB 스키마 생성 (5개 컬렉션)
mongosh "mongodb://mongoadmin:mongo_pass@${MONGO_HOST}:${MONGO_PORT}/meta_db?authSource=admin" \
    --file resources/connectors/mongo/init_mongodb.js
```

**생성되는 컬렉션:**

| 컬렉션 | 용도 | 관계 |
| ------ | ---- | ---- |
| `dpasset_table` | 테이블 메타 (이름, 설명, 주제영역) | 1:N → dpasset_column |
| `dpasset_column` | 컬럼 메타 (이름, 타입, 설명, PK/FK) | N:1 → dpasset_table |
| `standard_code` | 코드 정의 (코드필드, 설명) | 1:N → standard_code_value |
| `standard_code_value` | 코드값 (코드값, 코드명) | N:1 → standard_code |
| `glossary` | 용어사전 (용어, 정의, 동의어) | N:M → dpasset_table |

#### 실 메타 데이터 적재

```text
실 환경에서는 정보계 DB의 메타 테이블에서 추출한 데이터를 MongoDB에 적재해야 한다.

적재 방법:
  1) 시딩 스크립트 활용: devtools/scripts/seed_mongodb.py (PostgreSQL → MongoDB)
  2) 직접 ETL: 정보계 메타 테이블 → JSON 변환 → mongoimport 또는 PyMongo

시딩 스크립트 실행 (테스트 데이터 기반):
  python devtools/scripts/seed_mongodb.py

실 데이터 적재 시 주의사항:
  - dpasset_table.name 은 유니크해야 한다 (init_mongodb.js에 유니크 인덱스 정의됨)
  - 컬럼 설명 품질이 SQL 생성 정확도에 직접 영향
  - 코드값 매핑이 누락되면 WHERE 조건 생성 실패
```

### 7-2. Neo4j — 온톨로지 그래프 (신규)

```text
v0.1.0에서 신규 추가된 지식 그래프이다.
테이블 간 관계(FK), JOIN 경로, 금융 산출식 분해, 코드 계층 등을 그래프로 관리한다.
에이전틱 루프의 도구(tools.py)에서 search_join_paths(), search_formula() 등으로 활용된다.
```

#### Neo4j 스키마 초기화

```bash
# 1) 그래프 스키마 생성 (6개 노드 레이블, 9개 관계 타입)
cat resources/connectors/neo4j/init_neo4j.cypher | \
    cypher-shell -u neo4j -p ${NEO4J_PASSWORD} -a bolt://${NEO4J_HOST}:${NEO4J_PORT}
```

**그래프 스키마:**

| 노드 | 설명 |
| ---- | ---- |
| `Table` | 테이블 메타 |
| `Column` | 컬럼 메타 |
| `DomainConcept` | 도메인 개념 (여신, 수신 등) |
| `CodeDefinition` | 코드 정의 |
| `SubjectArea` | 주제 영역 |
| `QueryCondition` | 쿼리 조건 패턴 |

| 관계 | 설명 |
| ---- | ---- |
| `FK_TO` | 테이블 간 외래키 관계 |
| `BELONGS_TO` | 컬럼 → 테이블 |
| `IN_AREA` | 테이블 → 주제영역 |
| `COMPOSED_OF` | 산출식 분해 관계 |
| `MEASURED_BY` | 지표 → 계산 컬럼 |
| `APPLIES_TO` | 코드 → 적용 컬럼/테이블 |

#### Neo4j 데이터 시딩

```bash
# MongoDB 메타 데이터 기반으로 Neo4j 온톨로지 그래프 구축 (3단계)
# 사전 조건: seed_mongodb.py 완료 후 실행

# 전체 시딩 (Phase 1: 노드 → Phase 2: 관계 → Phase 3: 비즈니스 규칙)
python devtools/scripts/seed_neo4j.py

# 특정 단계만 실행
python devtools/scripts/seed_neo4j.py --phases 1,2

# 초기화 + 재시딩
python devtools/scripts/seed_neo4j.py --full-reset
```

### 7-3. ElasticSearch — 제거됨(2026-04)

```text
ElasticSearch는 2026-04 기준 제거되었다.
  - 테이블/컬럼 메타·코드 메타·용어사전 → MongoDB로 통합
  - 보고서 SQL·SQL 이력 → Qdrant(sql_history, 하이브리드 + Reranker)로 이전
  - nori 한글 분석기 설정은 더 이상 적용 대상이 아님
  - resources/connectors/elasticsearch/, devtools/docker/elasticsearch/,
    devtools/scripts/seed_elasticsearch.py 는 모두 제거됨
```

### 7-4. Qdrant — 벡터 검색 (임베딩 모델 변경)

```text
v0.1.0에서 임베딩 모델이 변경되어 벡터 차원과 검색 방식이 달라졌다.

  이전: MiniLM-L12 (384차원, Dense only)
  현재: BGE-M3 (1024차원, Dense + Sparse 하이브리드)

컬렉션 2개:
  biz_manual    — 업무 매뉴얼 (Dense only 검색)
  sql_history   — SQL 이력 (하이브리드 검색 + Reranker)
```

#### Qdrant 컬렉션 생성 + 데이터 적재

```bash
# 시딩 스크립트 실행 (테스트 데이터 기반)
python devtools/scripts/seed_qdrant.py

# SQL 이력 시딩 (실 데이터 기반 — 폐쇄망에서 실행)
python -m src.tools.seed_sql_history                         # 전체 (설명 추론 + 임베딩)
python -m src.tools.seed_sql_history --mode embed            # 임베딩만 (설명 있는 SQL)
python -m src.tools.seed_sql_history --mode infer            # 설명 추론만 (LLM 사용)
python -m src.tools.seed_sql_history --verify-only           # 검증만
python -m src.tools.seed_sql_history --system-code BDP       # 시스템 코드 필터
python -m src.tools.seed_sql_history --recreate-collection   # 컬렉션 재생성
```

### 7-5. PostgreSQL (정보계) — 읽기 전용 계정 확인

```sql
-- DBA에게 요청하여 계정 권한을 확인한다.
-- data_copilot_ro 계정이 SELECT만 가능한지 반드시 검증한다.

-- INSERT/UPDATE/DELETE/DROP 모두 실패해야 정상이다.
INSERT INTO TB_CUST_INFO (CUST_NO) VALUES ('TEST');
-- 기대 결과: ERROR: permission denied

-- SELECT 정상 동작 확인
SELECT COUNT(*) FROM TB_CUST_INFO;
-- 기대 결과: 정상 건수 반환
```

### 7-6. PostgreSQL (이력) — SQL 이력 테이블

```sql
-- 이력 DB 초기화 (devtools/scripts/init_postgres.sql 참조)
-- seed_sql_history.py 가 사용하는 테이블 구조를 확인한다.

CREATE TABLE IF NOT EXISTS sql_execution_history (
    id              SERIAL PRIMARY KEY,
    query_text      TEXT NOT NULL,
    generated_sql   TEXT NOT NULL,
    executed_at     TIMESTAMP DEFAULT NOW(),
    execution_ms    INTEGER,
    result_rows     INTEGER,
    success         BOOLEAN DEFAULT TRUE,
    session_id      VARCHAR(128),
    error_message   TEXT
);

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX idx_query_text_trgm ON sql_execution_history
    USING gin (query_text gin_trgm_ops);
```

### 7-7. Redis — 세션 캐시

```bash
# Redis 연결 테스트
python -m uv run python -c "
import redis
r = redis.Redis(host='${REDIS_HOST}', port=6379, db=0)
r.ping()
print('[PASS] Redis 연결 성공')
"
```

```text
세션 스토어(src/services/session/*)는 2026-04 기준 제거되었다.
대화 이력은 LangGraph 체크포인터(PostgreSQL, checkpoint_dc_messages 테이블)에
단일 소스로 저장된다. Redis는 CancelStore / ActiveRunStore 용도로만 사용된다.
  - REDIS_BACKEND=memory | redis (단일 워커: memory, 멀티 워커: redis)
  - 턴 단위 메시지 저장은 src/services/message_store.py 가 담당
    (과거 turn_text_store.py에서 rename)
```

### 7-8. ADW — Sybase IQ (폐쇄망 ADW)

```text
src/connectors/impl/adw_connector.py — SAP Sybase IQ 16.1
  (2026-04 sybase_connector.py → adw_connector.py 로 rename)

두 가지 연결 방식:
  native: sqlanydb (libdbcapi_r.so 필요)
  odbc:   pyodbc + unixODBC + SAP ODBC 드라이버

SQL 방언: "tsql"
기본 스키마: "ADWOWN"
읽기 전용: SELECT/WITH만 허용 (정규식 검증)
```

```bash
# ADW (Sybase IQ) 연결 테스트
python -m uv run python -c "
import asyncio
from src.connectors.impl.adw_connector import ADWConnector

async def test():
    db = ADWConnector(use_dummy=False)
    await db.connect()
    print(f'[PASS] ADW 연결 성공 (dialect: {db.dialect})')
    rows = await db.execute_query('SELECT 1 AS test_col')
    print(f'[PASS] SELECT 실행 성공: {rows}')
    await db.disconnect()

asyncio.run(test())
"
```

### 7-9. BDP — Impala (폐쇄망 BDP)

```text
src/connectors/impl/bdp_connector.py — Cloudera CDP 7.1.9, HiveServer2 Thrift
  (2026-04 impala_connector.py → bdp_connector.py 로 rename)

드라이버: impyla (asyncio.to_thread 래핑)
SQL 방언: "hive"
기본 스키마: "BDPOWN"
인증: LDAP (기본), PLAIN, NOSASL, GSSAPI
```

```bash
# BDP (Impala) 연결 테스트
python -m uv run python -c "
import asyncio
from src.connectors.impl.bdp_connector import BDPConnector

async def test():
    db = BDPConnector(use_dummy=False)
    await db.connect()
    print(f'[PASS] BDP 연결 성공 (dialect: {db.dialect})')
    rows = await db.execute_query('SELECT 1 AS test_col')
    print(f'[PASS] SELECT 실행 성공: {rows}')
    await db.disconnect()

asyncio.run(test())
"
```

---

## 8. 도메인 데이터 실 데이터 교체

### 8-1. 교체 대상 요약

```text
v0.1.0에서 도메인 설정이 코드에서 YAML 파일로 전면 외부화되었다.
코드 수정 없이 resources/domain/*.yaml 파일만 교체하면 된다.

  이전: src/services/domain/finance_terms.py (코드 내 하드코딩)
  현재: resources/domain/*.yaml (7개 YAML 파일)
```

| YAML 파일 | 용도 | 교체 내용 |
| ---------- | ---- | --------- |
| `business_dictionary.yaml` | 금융 용어 사전 (자연어 → DB 스키마 매핑) | 실 테이블명/컬럼명/코드값으로 전면 교체 |
| `business_synonyms.yaml` | 동의어/약어 사전 (정규화·검색 확장용) | 은행 고유 약어·상품명·부서명 추가 |
| `business_categories.yaml` | 카테고리 → domain_cd 매핑 | 실 MongoDB 컬렉션의 domain_cd 필드값에 맞게 재매핑 |
| `pii_columns.yaml` | PII 컬럼 정의 (forbidden + masking + conditional) | 실 DB PII 컬럼명으로 전면 교체 |
| `chart_config.yaml` | 차트 폰트/색상/레이아웃 설정 | 서버 OS 폰트 + 기업 브랜드 색상 |
| `output_templates.yaml` | 출력 템플릿 정의 (거래명세, 여신현황 등 9종) | 실제 업무 보고서 양식에 맞게 교체 |
| `stopwords.yaml` | 검색 불용어 목록 (조사, 접미사, 지시어, 보조어) | 은행 내부 표현 분석 후 보강 |

### 8-2. business_dictionary.yaml 교체 예시

```yaml
# AS-IS (테스트 데이터):
# tables: [TB_CUST_INFO]
# conditions: {CUST_TYPE_CD: "01"}

# TO-BE (실 DB 예시):
# tables: [DW.T_DP_CUST_M]
# conditions: {DP_CUST_DVCD: "10"}
```

### 8-3. pii_columns.yaml 교체

```yaml
# 3계층 PII 정책 구조:
forbidden:            # SELECT 절대 금지 — 실 DB 컬럼명으로 교체
  - JUMIN_NO          # 주민번호
  - RRNO              # 주민번호 변형
  - CARD_NO           # 카드번호
  - ACCT_NO           # 계좌번호
  - PASSWORD          # 비밀번호

masking:              # SELECT 허용, 마스킹 필수
  - column: PHONE_NO
    expression: "LEFT({col}, 3) || '****'"
  - column: EMAIL
    expression: "LEFT({col}, 3) || '****'"

conditional:          # 목록 조회 시만 마스킹
  - column: CUST_NM
    when: list_query
```

### 8-4. chart_config.yaml 폰트 설정

```yaml
# Windows 서버:
fonts:
  primary: "Malgun Gothic"
  fallback: "맑은 고딕"
  system: sans-serif

# Linux 서버 (폐쇄망):
fonts:
  primary: "Noto Sans KR"
  fallback: "NanumGothic"
  system: sans-serif
```

### 8-5. 골든셋 재작성

```text
resources/evaluation/ 에 골든셋이 외부화되어 있다.

  golden_queries.json — 골든셋 (정확도 측정 기준)
  test_queries.json   — 테스트 쿼리 (일반 테스트)

실 DB 기준으로 전면 재작성해야 한다.

재작성 대상 필드:
  query: 실제 은행 직원의 요청 문장
  expected_intent: 의도 분류 기대값
  expected_tables: 실제 정보계 테이블명
  expected_sql_pattern: 실제 SQL 패턴
  category: 실제 업무 도메인 분류
  difficulty: 난이도 분포 유지 (easy 30%, medium 50%, hard 20%)
```

### 8-6. 커넥터 쿼리 템플릿 교체

```text
resources/connectors/ 에 커넥터별 쿼리 템플릿이 외부화되어 있다.

  # elasticsearch/*.json — 제거됨(2026-04)
  mongo/*.json          — MongoDB 집계 파이프라인 (참조 문서)
  mongo/init_mongodb.js — MongoDB 스키마 초기화
  neo4j/*.cypher        — Cypher 그래프 쿼리
  neo4j/init_neo4j.cypher — Neo4j 스키마 초기화

실 환경에 맞게 교체가 필요한 항목:
  - MongoDB 파이프라인: 실 컬렉션 필드명에 맞게 조정
  - Neo4j Cypher: 실 그래프 스키마에 맞게 조정
```

---

## 9. 프롬프트 재튜닝

### 9-1. 프롬프트 외부화 구조

```text
v0.1.0에서 모든 프롬프트가 코드에서 외부 텍스트 파일로 이전되었다.
코드 수정 없이 resources/prompts/**/*.txt 파일만 편집하면 된다.

  이전: src/agents/nodes/prompts/system_prompts.py (인라인 문자열)
  현재: resources/prompts/ 3계층 디렉토리 (24개 파일)

로딩: src/agents/nodes/system_prompts.py → load_text_required() 으로 파일 로드
```

### 9-2. 프롬프트 디렉토리 구조

```text
resources/prompts/
├── interpret/                                    # 질의 해석 계층
│   ├── context_classifier_system.txt             # 이력 해소 + 의도 분류 통합
│   ├── context_classifier_user.txt
│   ├── query_normalizer_phase1_system.txt        # 8-Slot 정규화 Phase 1
│   ├── query_normalizer_phase1_user.txt
│   ├── query_normalizer_phase2_system.txt        # 정규화 Phase 2 교차검증
│   └── query_normalizer_phase2_user.txt
│
├── reason/                                       # 추론 계층
│   ├── knowledge_interpreter_system.txt          # 검색 결과 해석·지식 승격
│   ├── table_comparison_system.txt               # 유사 테이블 비교
│   ├── sql_generator_system.txt                  # SQL 생성 (절대규칙 10개 포함)
│   ├── sql_generator_fix_section.txt             # SQL 수정 피드백 삽입 조각
│   ├── sql_validator_system.txt                  # 의미 검증 (Layer 2b)
│   └── recovery_agent_system.txt                 # 복구 에이전트 계획
│
└── present/                                      # 표현 계층
    ├── analyzer_system.txt                       # 데이터 분석
    ├── analyzer_user.txt
    ├── analyzer_viz_judgment_system.txt           # 시각화 판정
    ├── analyzer_viz_judgment_user.txt
    ├── analyzer_viz_svg_system.txt               # SVG 생성
    └── analyzer_viz_svg_user.txt
    # formatter_system/user.txt는 rule-based 전환으로 삭제됨
```

### 9-3. 폐쇄망 모델 전환 시 프롬프트 재튜닝 포인트

| 프롬프트 | 현재 설계 | 오픈소스 모델 전환 시 조정 방향 |
| -------- | --------- | ------------------------------ |
| `context_classifier_system` | 이력 해소 + 4-way 의도 분류 통합 | few-shot 예제 추가, 분류 기준 단순화 |
| `query_normalizer_phase1_system` | 8-Slot JSON 구조화 출력 | JSON 스키마 명시, 예제 추가 |
| `sql_generator_system` | CoT 추론 + 절대규칙 10개 + PII 보호 | 규칙 수 축소 또는 max_retry 상향 |
| `sql_validator_system` | LLM 기반 의미 검증 (L2b) | 비활성화 검토 (`VALIDATE_LAYER2B_ENABLED=false`) |
| `knowledge_interpreter_system` | 검색 결과 해석 + 지식 승격 JSON | 출력 형식 단순화, few-shot 추가 |
| `recovery_agent_system` | ReAct 스타일 가설 교체 | 단순 재시도로 폴백 검토 |
| `analyzer_system` | JSON 분석 결과 출력 | 마크다운 코드블록 파싱 강화 |
| `formatter_system` | 자연어 보고서 변환 | 포맷 규칙 단순화, 예제 추가 |

### 9-4. 프롬프트 컨텍스트 주입 변수

```text
프롬프트에서 사용하는 {placeholder} 변수들이다.
노드 코드에서 format() 으로 치환되며, 실 데이터에 맞게 확인해야 한다.

sql_generator_system.txt 주요 변수:
  {current_date}     — 현재 날짜
  {original_query}   — 사용자 원문 질의
  {measures}         — 정규화된 측정항목
  {filters}          — 정규화된 필터 조건
  {group_by}         — 정규화된 그룹핑
  {order_limit}      — 정규화된 정렬/제한
  {confirmed_terms}  — CONFIRMED 상태 지식 항목
  {tables}           — 테이블 메타 + 샘플 데이터
  {reference_sqls}   — 유사 SQL 이력 참조
  {dead_ends}        — 실패한 접근법 (반복 방지)
  {fix_section}      — 이전 실패 수정 지시 (sql_generator_fix_section.txt)
```

---

## 10. Docker 이미지 오프라인 빌드

### 10-1. 현재 Docker 구성 (devtools/docker/docker-compose.dev.yml)

| 서비스 | 이미지 | 포트 | 비고 |
| ------ | ------ | ---- | ---- |
| PostgreSQL | `postgres:16-alpine` | 5432 | 정보계 + 이력 + 체크포인터 |
| Qdrant | `qdrant/qdrant:v1.12.6` | 6333, 6334 | REST + gRPC |
| **MongoDB** | `mongo:8.0.6` | 27017 | **신규** |
| **Neo4j** | `neo4j:5-community` | 7687, 7474 | **신규** |
| Redis | `redis:7-alpine` | 6379 | |

### 10-2. 오프라인 이미지 사전 준비

```bash
# 인터넷 환경에서 이미지를 다운로드하여 tar 파일로 저장한다.
docker pull postgres:16-alpine
docker pull qdrant/qdrant:v1.12.6
docker pull mongo:8.0.6
docker pull neo4j:5-community
docker pull redis:7-alpine

# (ElasticSearch 커스텀 이미지 빌드는 2026-04 제거됨)

# tar로 저장
docker save postgres:16-alpine qdrant/qdrant:v1.12.6 mongo:8.0.6 \
    neo4j:5-community redis:7-alpine \
    | gzip > docker-images.tar.gz

# 폐쇄망에서 로드
docker load < docker-images.tar.gz
```

### 10-3. 오프라인 번들 스캐폴드 (신규 2026-04)

```text
deploy/ 디렉토리에 오프라인 배포용 스캐폴드가 정리되어 있다.

  deploy/offline-bundle/
    build.sh            — 외부망에서 wheel + 모델 번들 생성 (uv 기반)
    install.sh          — 폐쇄망에서 번들 설치
    download_models.sh  — BGE-M3 / BGE-Reranker 사전 다운로드
    os-packages.txt     — OS 레벨 패키지 목록
  deploy/db-init/
    postgres/init.sh    — PostgreSQL 체크포인터·커스텀 테이블 초기화
    mongo/init.sh       — MongoDB 컬렉션·인덱스 초기화
    qdrant/init.sh      — Qdrant 컬렉션 초기화
  deploy/systemd/
    data-copilot.service — 운영 서비스 등록용 systemd 유닛

(ElasticSearch nori 오프라인 설치 절차는 ES 제거와 함께 폐기됨.)
```

---

## 11. 동작 테스트

### 11-1. 단위 테스트

```bash
# 기존 단위 테스트를 먼저 실행하여 코드 수정으로 인한 회귀를 확인한다.
# tests/auto/ 아래 테스트는 mock 사용, 외부 인프라 불필요.
python -m uv run pytest tests/auto/unit/ -v

# E2E 테스트 (mock LLM, 다중 노드 연쇄)
python -m uv run pytest tests/auto/e2e/ -v
```

### 11-2. 커넥터 개별 연결 테스트

```bash
# 각 데이터소스에 대한 연결 테스트를 수행한다.
# tests/manual/e2e/test_connector_real.py 활용 가능

# 1) 전체 인프라 연결 확인
python -m uv run pytest tests/manual/e2e/test_infra_connectivity.py -v

# 2) 개별 커넥터 테스트
python -m uv run python -c "
import asyncio
from src.connectors.impl.mongo_connector import MongoConnector
from src.connectors.impl.neo4j_connector import Neo4jConnector

async def test():
    # MongoDB
    mongo = MongoConnector(use_dummy=False)
    await mongo.connect()
    ok = await mongo.health_check()
    print(f'[{\"PASS\" if ok else \"FAIL\"}] MongoDB')

    # 테이블 메타 검색 테스트
    results = await mongo.search_table_meta('고객')
    print(f'  → 검색 결과: {len(results)}건')

    # Neo4j
    neo4j = Neo4jConnector(use_dummy=False)
    await neo4j.connect()
    ok = await neo4j.health_check()
    print(f'[{\"PASS\" if ok else \"FAIL\"}] Neo4j')

    await mongo.disconnect()
    await neo4j.disconnect()

asyncio.run(test())
"
```

### 11-3. Health Check API 테스트

```bash
# 서버 기동 (실 모드)
python -m uv run uvicorn src.main:app --host 0.0.0.0 --port 8000

# Health Check
curl http://localhost:8000/health
# 기대 결과 (v0.1.0 — MongoDB, Neo4j 추가):
# {
#   "status": "ok",
#   "connectors": {
#     "mongodb": true,
#     "info_db": true,
#     "history_db": true,
#     "qdrant": true,
#     "neo4j": true,
#     "llm_api": true
#   }
# }
```

### 11-4. E2E 파이프라인 테스트

```bash
# CLI로 파이프라인 전체를 실행한다.
# 간단한 질의부터 시작하여 점진적으로 복잡도를 높인다.

# 1단계: 단순 집계 (COUNT)
python -m uv run python -m src.agents.graph.runner "이번 달 신규 고객 수 알려줘"

# 2단계: 그룹별 집계 (GROUP BY)
python -m uv run python -m src.agents.graph.runner "대출 유형별 건수와 금액 보여줘"

# 3단계: 시계열 분석
python -m uv run python -m src.agents.graph.runner "최근 12개월 연체율 추이 분석해줘"

# 4단계: 명확화 질문 트리거
python -m uv run python -m src.agents.graph.runner "데이터 좀 뽑아줘"

# 각 단계에서 확인할 사항:
# - LLM이 정상 응답하는가
# - 에이전틱 루프가 정상 동작하는가 (도구 호출: search_table_meta 등)
# - SQL이 실 테이블명/컬럼명으로 생성되는가
# - SQL 검증(3-레이어)을 통과하는가
# - 정보계 DB에서 정상 실행되는가 (또는 Sybase IQ/Impala)
# - 결과가 한국어로 포맷팅되는가
# - 시각화 SVG가 정상 생성되는가
```

### 11-5. WebSocket 챗봇 UI 테스트

```bash
# 브라우저에서 http://localhost:8000 접속 후 챗봇 UI에서 직접 질의한다.

# 테스트 시나리오:
# 1. 단순 질의 → 정상 응답 확인
# 2. 명확화 필요 질의 → 선택지 제시 확인
# 3. 분석 질의 → 차트(SVG) 렌더링 확인
# 4. 프롬프트 인젝션 시도 → 차단 메시지 확인
# 5. 연속 대화 → 세션 컨텍스트 유지 확인 (멀티턴)
# 6. /reset 명령 → 세션 초기화 확인
# 7. /history 명령 → 대화 이력 확인
```

### 11-6. 골든셋 평가

```bash
# 실 데이터소스 연결 후 골든셋 기반 SQL 생성 정확도를 측정한다.
# 골든셋이 실 DB 기준으로 재작성된 상태에서만 의미 있다.
python -m uv run python -m devtools.evaluation.run_evaluation
```

---

## 12. 유의사항 체크리스트

### 보안

- [ ] **정보계 DB 계정이 읽기 전용(SELECT only)인지 DB 레벨에서 확인**
- [ ] **.env 파일이 Git에 커밋되지 않는지 확인** — `.gitignore`에 `.env` 포함 여부
- [ ] **골든셋에 실 PII가 포함되지 않도록 검수**
- [ ] **로그에 PII가 평문으로 기록되지 않는지 확인** — `LOG_LEVEL=DEBUG` 시 주의
- [ ] **WebSocket을 wss://(TLS)로 전환** — 내부 네트워크 스니핑 방지
- [ ] **pii_columns.yaml에 실 DB PII 컬럼 변형명 반영** — `RRNO`, `ACCT_NO` 등

### 네트워크

- [ ] **폐쇄망 방화벽 규칙 확인** — 로컬 PC → 각 서버 간 포트 통신 허용
  - PostgreSQL: 5432
  - MongoDB: 27017
  - Qdrant: 6333
  - Neo4j: 7687
  - Redis: 6379
  - ADW(Sybase IQ): 2638
  - BDP(Impala): 21050
  - CRP: 환경별 상이
  - 폐쇄망 LLM API: 해당 포트
- [ ] **DNS 확인** — 호스트명 해석 안 되면 IP 직접 지정
- [ ] **프록시 설정** — 필요 시 `HTTP_PROXY`, `HTTPS_PROXY` 환경변수

### 패키지

- [ ] **전이 의존성 누락 확인** — `uv sync` 오류 시 누락 wheel 추가 이관
- [ ] **플랫폼 호환성** — C 확장(`asyncpg`, `torch`, `onnxruntime`, `grpcio`)은 OS별 바이너리 다름
- [ ] **ML 모델 파일 이관 확인** — BGE-M3, BGE-Reranker 캐시 디렉토리

### 데이터

- [ ] **MongoDB 스키마 초기화 확인** — `init_mongodb.js` 실행 여부
- [ ] **Neo4j 스키마 초기화 확인** — `init_neo4j.cypher` 실행 여부
- [ ] **Qdrant 임베딩 모델 오프라인 배포 확인** — `EMBEDDING_CACHE_PATH` 설정
- [ ] **리랭커 모델 오프라인 배포 확인** — `RERANKER_CACHE_PATH` 설정
- [ ] **MongoDB 메타와 도메인 사전 매핑 정합성** — 테이블명/컬럼명 오타 시 SQL 생성 실패
- [ ] **시딩 순서 확인** — PostgreSQL → MongoDB → Neo4j → Qdrant (ES 제거됨 2026-04)

### 외부 통신 차단

- [ ] **LangSmith 잔존 참조 확인** — 2026-04 제거됨, 코드/설정에 `LANGSMITH_*` 키 없어야 함
- [ ] **HuggingFace Hub 접근 차단 확인** — `EMBEDDING_CACHE_PATH`, `RERANKER_CACHE_PATH` 설정으로 오프라인 로딩
- [ ] **openai_referer, openai_title 확인** — `src/config.py` 의 기본값이 외부 URL이면 변경

### Sybase IQ 전용

- [ ] **libdbcapi_r.so (Linux) 또는 dbcapi.dll (Windows) 설치 확인** — native 방식
- [ ] **unixODBC + SAP ODBC 드라이버 설치 확인** — odbc 방식
- [ ] **odbcinst.ini 드라이버 등록 확인** — odbc 방식

### Impala 전용

- [ ] **impyla >= 0.20.0 설치 확인** — 0.18-0.19는 Python 3.12 버그
- [ ] **LDAP 인증 정보 확인** — `IMPALA_AUTH_MECHANISM=LDAP`
- [ ] **Kerberos 설정 확인 (필요 시)** — OS `krb5-devel` + `kerberos>=1.3.0`

---

## 13. 향후 개선사항

### 즉시 (폐쇄망 안정화 직후)

| 항목 | 설명 | 관련 코드 |
| ---- | ---- | --------- |
| **LLM health check 실 연결** | API 키 존재 여부만 체크 → 실제 LLM API ping 호출로 변경 | `src/main.py` health endpoint |
| **Checkpointer PostgreSQL 전환** | 멀티턴 상태를 Redis/PostgreSQL로 영속화 | `src/agents/graph/checkpointer.py` |
| **ONNX 리랭커 사전 변환** | 최초 요청 시 변환 대신 사전 변환된 ONNX 파일 배포 | `RERANKER_ONNX_PATH` 설정 |

### 단기 (1~2개월)

| 항목 | 설명 |
| ---- | ---- |
| **Rate Limiting** | WebSocket/REST API에 요청 횟수 제한 추가 |
| **사용자 인증** | JWT 또는 세션 쿠키 기반 인증. 폐쇄망 SSO 연동 |
| **감사 로그 DB 저장** | SQL 실행 이력을 별도 감사 테이블에 영구 저장 |
| **Qwen Thinking 모드 최적화** | 폐쇄망 LLM 모델별 thinking_modes.py 재조정 |

### 중기 (3~6개월)

| 항목 | 설명 |
| ---- | ---- |
| **Redis 캐시 본격 적용** | 동일/유사 질의에 대한 SQL + 결과 캐싱 |
| **EXPLAIN 기반 쿼리 최적화** | 생성된 SQL 실행 계획 사전 분석, 풀스캔 방지 |
| **골든셋 CI/CD 통합** | 프롬프트/모델 변경 시 골든셋 자동 평가 |
| **Neo4j 온톨로지 자동 갱신** | 메타 변경 시 그래프 자동 업데이트 |

### 장기 (6개월~)

| 항목 | 설명 |
| ---- | ---- |
| **사용자 피드백 루프** | "결과 맞음/틀림" 피드백 수집 → SQL 이력 품질 향상 |
| **분석 결과 캐시 + 증분 갱신** | 정기 분석 결과 캐싱 + 데이터 갱신 주기 연동 |
| **프롬프트 인젝션 패턴 정기 업데이트** | 분기별 새 공격 패턴 검토 + 방어 규칙 추가 |
| **멀티 LLM 라우팅** | 노드별 다른 모델 사용 (경량 분류 vs 고성능 SQL 생성) |

---

## 부록: 파일별 수정 체크리스트

### 필수 수정 (실 데이터소스 연결에 반드시 필요)

| 파일 | 수정 유형 | 내용 |
| ---- | --------- | ---- |
| `.env` (신규 작성) | 환경변수 | `LLM_PROVIDER`, API 키, 모든 데이터소스 접속 정보 입력 |
| `resources/domain/business_dictionary.yaml` | 데이터 교체 | 실 테이블명/컬럼명/코드값으로 전면 교체 |
| `resources/domain/pii_columns.yaml` | 보안 설정 | 실 DB PII 컬럼명으로 전면 교체 |
| `resources/evaluation/golden_queries.json` | 전면 재작성 | 실 DB 스키마 기준으로 전체 재작성 |

### 권장 수정 (운영 안정성 + 정확도 향상)

| 파일 | 수정 유형 | 내용 |
| ---- | --------- | ---- |
| `resources/domain/business_synonyms.yaml` | 보강 | 은행 고유 동의어/약어 추가 |
| `resources/domain/business_categories.yaml` | 재매핑 | 실 MongoDB domain_cd 매핑 |
| `resources/domain/chart_config.yaml` | 폰트 변경 | 서버 OS에 맞는 한글 폰트 |
| `resources/domain/output_templates.yaml` | 커스터마이징 | 실 업무 보고서 양식 |
| `resources/domain/stopwords.yaml` | 보강 | 은행 내부 표현 반영 |
| `resources/prompts/**/*.txt` | 재튜닝 | 폐쇄망 모델 특성에 맞게 조정 |
| `resources/connectors/mongo/init_mongodb.js` | 스키마 확인 | 실 메타 구조에 맞게 검증 |
| `resources/connectors/neo4j/init_neo4j.cypher` | 스키마 확인 | 실 온톨로지 구조에 맞게 검증 |

### 코드 수정 불필요 (환경변수만으로 전환)

```text
아래 파일들은 이미 프로바이더 전환이 구현되어 있으므로 코드 수정이 불필요하다.

- src/config.py              — Settings 통합 (use_dummy, deployment_mode, 전체 커넥터 설정)
- src/utils/llm/client.py    — UnifiedLLMClient (Anthropic / OpenAI 호환 / Thinking 모드 자동 대응)
- src/utils/llm/retry.py     — 폐쇄망 모델 포맷 파싱 재시도 메커니즘
- src/connectors/manager.py  — ConnectorManager (전체 커넥터 수명주기 + DB 라우팅)
- src/connectors/impl/*      — 10종 커넥터 (모두 use_dummy 지원)
- src/main.py                — settings.use_dummy 자동 참조
- src/agents/graph/runner.py  — settings.use_dummy 자동 참조
- pyproject.toml              — 모든 의존성 이미 포함 (openai, motor, neo4j, impyla 등)
```

### 시딩 실행 순서

```bash
# 전체 시딩 순서 (devtools/scripts/seed_all.sh 참조):
# 1. PostgreSQL — 테스트 데이터 + DDL
python devtools/scripts/seed_postgres.py

# 2. MongoDB — 테이블/컬럼/코드/용어사전 메타 (ES 대체, 2026-04)
python devtools/scripts/seed_mongodb.py

# 3. Neo4j — 온톨로지 그래프 (MongoDB 데이터 기반)
python devtools/scripts/seed_neo4j.py

# 4. Qdrant — 매뉴얼 + SQL 이력 벡터
python devtools/scripts/seed_qdrant.py

# 5. SQL 이력 임베딩 (실 데이터 기반)
python -m src.tools.seed_sql_history
```

---

## 부록: 커스터마이징 우선순위 요약

### P0 — 필수 (미수행 시 시스템 동작 불가)

| # | 항목 | 이유 |
| - | ---- | ---- |
| 1 | LLM 프로바이더 교체 (§4) | 전체 파이프라인 동작 불가 |
| 2 | 임베딩 모델 오프라인 배포 (§5-1) | Qdrant 벡터 검색 불가 |
| 3 | Docker 이미지/패키지 오프라인 준비 (§10) | 인프라 구동 불가 |
| 4 | 리랭커 모델 오프라인 배포 (§5-2) | SQL 이력 검색 품질 저하 |
| 5 | 오프라인 번들(`deploy/offline-bundle/`) 준비 (§10-3) | 폐쇄망 의존성·모델 반입 |

### P1 — 정확도 핵심 (미수행 시 답변 품질 심각 저하)

| # | 항목 | 이유 |
| - | ---- | ---- |
| 6 | 도메인 YAML 교체 (§8) | 자연어→SQL 변환 핵심 브릿지 |
| 7 | 프롬프트 재튜닝 (§9) | 폐쇄망 모델 포맷 준수율 |
| 8 | MongoDB 메타 적재 (§7-1) | 테이블/컬럼 검색 품질 |
| 9 | Neo4j 온톨로지 구축 (§7-2) | JOIN 경로·산출식 참조 |
| 10 | PII 컬럼 재설정 (§8-3) | 보안 규칙 미준수 위험 |
| 11 | LLM 재시도 설정 상향 (§4-3) | 폐쇄망 모델 포맷 실패 대응 |

### P2 — 품질 향상 (수행 시 정확도 추가 개선)

| # | 항목 | 이유 |
| - | ---- | ---- |
| 12 | 골든셋 재작성 (§8-5) | 정확도 측정·개선 기반 |
| 13 | 업무 매뉴얼 실데이터 Qdrant 적재 | 금융지표 산출식 참조 |
| 14 | SQL 이력 임베딩 (§7-4) | 유사 SQL 참조 품질 |
| 15 | MongoDB/Qdrant 쿼리 튜닝 (§8-6) | 메타·SQL이력 검색 정밀도 |
| 16 | Thinking 모드 조정 (§4-4) | 모델별 추론 품질 최적화 |

### P3 — 부가 (세부 품질 개선)

| # | 항목 | 이유 |
| - | ---- | ---- |
| 17 | 불용어 보강 (§8-1) | 검색 노이즈 감소 |
| 18 | SVG 폰트 (§8-4) | 차트 한글 깨짐 방지 |
| 19 | 출력 템플릿 교체 (§8-1) | 업무 보고서 양식 일치 |
| 20 | 자체 tracker 출력 경로 점검 (§12) | 2026-04 LangSmith 제거 후 관측성 유지 |
| 21 | 세션 Redis 전환 (§7-7) | 서버 재시작 시 세션 유지 |
