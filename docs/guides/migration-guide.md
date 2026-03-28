# 폐쇄망 이관 및 실 데이터소스 연동 가이드

**작성일:** 2026-03-19
**대상 환경:** 은행 폐쇄망 (인터넷 차단) 내 로컬 PC
**목적:** Dummy 모드 → 실 데이터소스 + 폐쇄망 LLM API 연결 후 정상 동작 테스트

---

## 목차

1. [사전 준비](#1-사전-준비)
2. [패키지 오프라인 이관](#2-패키지-오프라인-이관)
3. [환경 설정](#3-환경-설정)
4. [LLM API 전환 (Anthropic → 폐쇄망 LLM)](#4-llm-api-전환-anthropic--폐쇄망-llm)
5. [커넥터 실 모드 전환](#5-커넥터-실-모드-전환)
6. [데이터소스별 연동 작업](#6-데이터소스별-연동-작업)
7. [도메인 데이터 실 데이터 교체](#7-도메인-데이터-실-데이터-교체)
8. [로컬 PC 동작 테스트](#8-로컬-pc-동작-테스트)
9. [유의사항 체크리스트](#9-유의사항-체크리스트)
10. [향후 개선사항](#10-향후-개선사항)

---

## 1. 사전 준비

### 1-1. 폐쇄망 내 필수 인프라 확인

```text
실 데이터소스 연결을 위해 다음 인프라가 폐쇄망 내에서 접근 가능한지 확인한다.
각 서비스의 호스트:포트를 사전에 확보해야 한다.
```

| 인프라 | 용도 | 확인 항목 |
| ------ | ---- | --------- |
| PostgreSQL (정보계) | 실 데이터 추출 대상 DB | 호스트, 포트, DB명, **읽기 전용** 계정/비밀번호 |
| PostgreSQL (이력) | 과거 SQL 실행 이력 저장 | 호스트, 포트, DB명, 계정/비밀번호 |
| ElasticSearch | 테이블 메타 + 보고서 SQL + 코드 메타 검색 | 호스트, 포트, 인증 정보, 인덱스명 |
| Qdrant | 업무 매뉴얼 벡터 검색 | 호스트, 포트, 컬렉션명 |
| Redis | 캐시 | 호스트, 포트, DB 번호 |
| 폐쇄망 LLM API | Claude 대체 LLM 서비스 | 엔드포인트 URL, API 키, 모델명 |

### 1-2. 폐쇄망 PC 환경 확인

```bash
# Python 3.12 이상 설치 여부 확인
python --version
# 결과: Python 3.12.x 이상이어야 함

# uv 설치 여부 확인 (인터넷 환경에서 미리 설치)
python -m uv --version
```

---

## 2. 패키지 오프라인 이관

### 2-1. 인터넷 환경에서 패키지 다운로드

```bash
# 인터넷이 되는 PC에서 실행한다.
# 프로젝트 루트에서 uv.lock 기반으로 모든 의존성 wheel 파일을 다운로드한다.
# --python-platform: 폐쇄망 PC의 OS에 맞게 지정한다.
cd c:\Users\cjfgm\Desktop\workspace\data-copilot

# 방법 1) uv export → pip download
# uv.lock을 requirements.txt로 변환한다.
python -m uv export --frozen --no-hashes -o requirements.txt

# 폐쇄망 PC와 동일한 플랫폼용 wheel 파일을 packages/ 디렉토리에 다운로드한다.
# --platform: 폐쇄망 PC OS에 맞게 변경 (예: win_amd64, manylinux2014_x86_64)
pip download -r requirements.txt -d packages/ --platform win_amd64 --python-version 3.12 --only-binary=:all:

# 방법 2) uv 자체도 wheel로 준비
pip download uv -d packages/ --platform win_amd64 --python-version 3.12 --only-binary=:all:
```

### 2-2. USB/보안매체로 이관

```text
다음 파일들을 보안 USB 또는 허가된 매체로 폐쇄망 PC에 복사한다.
```

| 이관 대상 | 설명 |
| --------- | ---- |
| `data-copilot/` 프로젝트 전체 | 소스코드, pyproject.toml, uv.lock 포함 |
| `packages/` 디렉토리 | 오프라인 설치용 wheel 파일 전체 |
| Python 3.12+ 설치 파일 | 폐쇄망 PC에 Python 미설치 시 |

### 2-3. 폐쇄망 PC에서 오프라인 설치

```bash
# 폐쇄망 PC에서 실행한다.
cd data-copilot

# uv를 먼저 설치한다 (인터넷 없이 wheel에서 설치).
pip install --no-index --find-links=packages/ uv

# uv로 프로젝트 의존성을 오프라인 설치한다.
# --find-links: 로컬 wheel 디렉토리를 패키지 소스로 지정한다.
# --no-index: PyPI 접근을 차단하여 오프라인 모드를 강제한다.
python -m uv sync --all-extras --find-links=packages/ --no-index

# 설치 확인 — 주요 패키지가 정상 설치되었는지 점검한다.
python -m uv run python -c "import anthropic; import fastapi; import sqlalchemy; print('OK')"
```

> **유의:** uv는 `--find-links`와 `--no-index` 조합으로 완전한 오프라인 설치를 지원한다.
> 단, `--find-links` 디렉토리에 **모든** 의존성(전이 의존성 포함)의 wheel이 있어야 한다.
> 누락 패키지가 있으면 인터넷 PC에서 해당 패키지만 추가 다운로드 후 재이관한다.

---

## 3. 환경 설정

### 3-1. .env 파일 작성

```bash
# .env.example을 복사하여 실 환경 값으로 수정한다.
# .env 파일은 .gitignore에 포함되어 있으므로 Git에 커밋되지 않는다.
cp .env.example .env
```

```dotenv
# === .env — 폐쇄망 실 환경 설정 ===

# ── LLM API ──
# LLM_PROVIDER 로 프로바이더를 선택하면 llm_client.py 가 자동으로 전환한다.
# "anthropic": Anthropic Claude API (또는 Anthropic 호환 프록시)
# "openai_compatible": OpenAI 호환 API (vLLM, TGI, Groq, OpenRouter 등)

LLM_PROVIDER=openai_compatible                    # 폐쇄망은 보통 openai_compatible
LLM_MODEL=모델명                                   # 폐쇄망 LLM 모델명

# Anthropic 프로바이더 사용 시 (LLM_PROVIDER=anthropic)
ANTHROPIC_API_KEY=sk-ant-xxxxx

# OpenAI 호환 프로바이더 사용 시 (LLM_PROVIDER=openai_compatible)
OPENAI_API_KEY=폐쇄망-api-key                      # 폐쇄망 LLM API 키
OPENAI_BASE_URL=https://internal-llm.bank.co.kr/v1  # 폐쇄망 LLM 엔드포인트

# LLM 파싱 재시도 (소형/로컬 LLM 대응)
# 포맷 불일치 시 최대 재시도 횟수. 폐쇄망 소형 모델은 2~3 권장.
LLM_PARSE_MAX_RETRY=2

# ── 정보계 DB (읽기 전용) ──
# 반드시 SELECT 전용 읽기 전용 계정을 사용해야 한다.
# DBA에게 요청하여 계정 권한을 확인한다.
INFO_DB_HOST=10.xx.xx.xx          # 정보계 DB 서버 IP
INFO_DB_PORT=5432
INFO_DB_NAME=dw                   # 실 데이터웨어하우스 DB명
INFO_DB_USER=data_copilot_ro      # 읽기 전용 계정
INFO_DB_PASSWORD=실제비밀번호

# ── SQL 이력 DB ──
# 과거 SQL 실행 이력을 저장/조회하는 DB.
# 이 DB에는 INSERT 권한도 필요하다 (이력 저장).
HISTORY_DB_HOST=10.xx.xx.xx
HISTORY_DB_PORT=5432
HISTORY_DB_NAME=data_copilot_hist
HISTORY_DB_USER=history_user
HISTORY_DB_PASSWORD=실제비밀번호

# ── ElasticSearch ──
# 테이블 메타, 보고서 SQL, 코드 메타가 색인된 ES 클러스터.
ES_HOST=10.xx.xx.xx
ES_PORT=9200
ES_USER=data_copilot              # ES 검색 전용 계정
ES_PASSWORD=실제비밀번호

# ── Qdrant ──
# 업무 매뉴얼이 벡터 임베딩되어 적재된 Qdrant 서버.
QDRANT_HOST=10.xx.xx.xx
QDRANT_PORT=6333

# ── Redis ──
# 캐시용. 없으면 캐시 없이도 동작하지만 응답 속도가 느려진다.
REDIS_HOST=10.xx.xx.xx
REDIS_PORT=6379
REDIS_DB=0

# ── 애플리케이션 ──
LOG_LEVEL=INFO                    # 초기 연동 테스트 시 DEBUG 권장
LOG_FORMAT=json                   # 폐쇄망에서는 json 포맷 권장 (감사 로그 파싱)
MAX_QUERY_ROWS=10000              # 결과 행 수 제한
```

### 3-2. 환경변수 검증 스크립트

```bash
# .env 파일의 필수 값이 모두 채워졌는지 확인하는 간단한 검증이다.
# 'your-'로 시작하는 플레이스홀더가 남아 있으면 경고한다.
# LLM_PROVIDER 에 따라 필요한 API 키가 다르므로 분기 체크한다.
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
    'LLM_PROVIDER': settings.llm_provider,
    llm_key_name: llm_key_value,
    'LLM_MODEL': settings.llm_model,
    'INFO_DB_HOST': settings.info_db_host,
    'INFO_DB_PASSWORD': settings.info_db_password,
    'ES_HOST': settings.es_host,
    'QDRANT_HOST': settings.qdrant_host,
}

# openai_compatible 인 경우 base_url 도 체크
if settings.llm_provider == 'openai_compatible':
    checks['OPENAI_BASE_URL'] = settings.openai_base_url

for k, v in checks.items():
    status = 'PASS' if v and 'your-' not in v and v != 'localhost' else 'FAIL'
    print(f'  [{status}] {k} = {v[:20]}...' if len(v) > 20 else f'  [{status}] {k} = {v}')
"
```

---

## 4. LLM API 전환 (Anthropic → 폐쇄망 LLM)

### 4-1. 현재 아키텍처 — 통합 LLM 클라이언트

```text
llm_client.py 에 UnifiedLLMClient 래퍼가 구현되어 있어,
.env 의 LLM_PROVIDER 설정만 변경하면 코드 수정 없이 프로바이더를 전환할 수 있다.
모든 노드는 client.messages.create() 통합 인터페이스만 호출하므로
프로바이더 변경이 노드 코드에 영향을 주지 않는다.
```

| 폐쇄망 LLM 유형 | LLM_PROVIDER 값 | 코드 수정 | 설명 |
| --------------- | --------------- | --------- | ---- |
| **Anthropic API 호환** (AWS Bedrock, 사내 프록시) | `anthropic` | **없음** | ANTHROPIC_API_KEY 만 설정 |
| **OpenAI API 호환** (vLLM, TGI, Ollama, Azure OpenAI 등) | `openai_compatible` | **없음** | OPENAI_API_KEY + OPENAI_BASE_URL 설정 |
| **독자 API** (자체 개발 LLM 서비스) | — | llm_client.py 에 프로바이더 추가 | UnifiedLLMClient 에 새 Messages 클래스 작성 |

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
LLM_MODEL=폐쇄망-모델명

# ── 소형/로컬 LLM 대응 ──
# 포맷 불일치 시 재시도 횟수. 소형 모델일수록 높게 설정한다.
LLM_PARSE_MAX_RETRY=2
```

### 4-3. LLM 응답 포맷 파싱 재시도 메커니즘

```text
소형/로컬 LLM 은 지정된 출력 포맷(INTENT: xxx, JSON 등)을 준수하지 못할 수 있다.
이를 대비하여 llm_retry.py 에 자동 재시도 메커니즘이 구현되어 있다.

재시도 전략:
  1차: 원본 프롬프트로 LLM 호출
  2차~: [이전 LLM 응답] + [포맷 교정 힌트] 를 대화에 추가하여 재호출
  최종 실패: 노드별 안전한 기본값으로 폴백

적용 노드:
  - intent_classifier.py  — INTENT:/CONFIDENCE: 형식 파싱 (가장 치명적)
  - analyzer.py            — JSON 분석 결과 파싱 + 시각화 판단 파싱
  - table_meta_enricher.py      — 보강 설명 최소 품질(길이) 검증

재시도 횟수는 .env 의 LLM_PARSE_MAX_RETRY 로 제어한다 (기본 2회).
```

### 4-4. LLM 연결 테스트

```bash
# 폐쇄망 LLM API 에 정상 연결되는지 단독 테스트한다.
# LLM_PROVIDER 설정에 관계없이 동일한 코드로 테스트할 수 있다.
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

asyncio.run(test())
"
```

### 4-5. 소형 LLM 포맷 준수율 향상 팁

```text
폐쇄망 소형 모델이 출력 포맷을 잘 지키지 못하는 경우 아래를 점검한다.

1. temperature=0 사용 — 구조화된 출력에는 창의성이 불필요
2. max_tokens 를 최소로 — 불필요한 텍스트 생성을 억제
3. Few-shot 예제 확인 — src/agents/nodes/prompts/system_prompts.py 에 이미 포함되어 있음
4. LLM_PARSE_MAX_RETRY 조정 — 소형 모델은 2~3 으로 설정
5. JSON Mode 활용 — 일부 API 는 response_format={"type": "json_object"} 지원
   (현재 코드에는 미적용. 필요 시 llm_client.py 의 OpenAICompatibleMessages 에 추가)
```

---

## 5. 커넥터 실 모드 전환

### 5-1. use_dummy=False 전환

```text
현재 코드에서 use_dummy=True가 하드코딩된 위치가 3곳이다.
폐쇄망에서 실 데이터소스를 사용하려면 이 값을 False로 변경해야 한다.
```

| 파일 | 라인 | 현재 코드 | 변경 |
| ---- | ---- | --------- | ---- |
| `src/main.py` | L59 | `get_connector_manager(use_dummy=True)` | `use_dummy=False` |
| `src/agents/graph/runner.py` | L85 | `get_connector_manager(use_dummy=True)` | `use_dummy=False` |
| `src/connectors/manager.py` | L69 | `def get_connector_manager(use_dummy: bool = True)` | 기본값 `False` |

```python
# ── 권장 방법: Settings에서 일원화 관리 ──
# use_dummy를 .env에서 제어할 수 있도록 config.py에 추가한다.
# 이렇게 하면 코드 수정 없이 .env만 바꿔서 Dummy/실 모드를 전환할 수 있다.

# src/config.py에 추가:
class Settings(BaseSettings):
    # ... 기존 설정 ...

    # Dummy 모드 제어 — True: 더미 데이터 사용 / False: 실 데이터소스 연결
    # 폐쇄망 배포 시 False로 설정한다.
    use_dummy: bool = True
```

```python
# ── src/main.py (수정) ──
# 하드코딩 대신 settings.use_dummy를 참조한다.
from src.config import settings
# ...
manager = get_connector_manager(use_dummy=settings.use_dummy)
```

```python
# ── src/agents/graph/runner.py (수정) ──
# 동일하게 settings.use_dummy를 참조한다.
from src.config import settings
# ...
manager = get_connector_manager(use_dummy=settings.use_dummy)
```

```dotenv
# .env에 추가:
# 개발 환경에서는 True, 폐쇄망 실 환경에서는 False
USE_DUMMY=false
```

### 5-2. 연결 테스트

```bash
# 각 커넥터가 실 데이터소스에 정상 연결되는지 health check를 수행한다.
python -m uv run python -c "
import asyncio
from src.connectors.manager import get_connector_manager

async def test():
    # use_dummy=False로 실 커넥터 초기화
    manager = get_connector_manager(use_dummy=False)
    await manager.connect_all()

    # 각 커넥터 상태 확인
    status = await manager.health_check_all()
    for name, ok in status.items():
        print(f'  [{\"PASS\" if ok else \"FAIL\"}] {name}')

    await manager.disconnect_all()

asyncio.run(test())
"
# 기대 결과: 4개 커넥터 모두 PASS
```

---

## 6. 데이터소스별 연동 작업

### 6-1. ElasticSearch — 메타 데이터 색인

```text
현재 DUMMY_TABLE_META에는 테이블 6개만 있다.
실 환경에서는 정보계 DB의 전체 테이블 레이아웃을 ES에 색인해야 한다.
```

#### ES 인덱스 생성 (3개 인덱스)

```bash
# 1) 테이블 메타 인덱스 — 테이블/컬럼 정의 검색용
# 현재 코드의 elasticsearch_connector.py에서 "table_meta" 인덱스명을 사용한다.
curl -X PUT "http://${ES_HOST}:${ES_PORT}/table_meta" -H 'Content-Type: application/json' -d '{
  "mappings": {
    "properties": {
      "table_name":        {"type": "keyword"},
      "table_description": {"type": "text", "analyzer": "nori"},
      "schema":            {"type": "keyword"},
      "update_cycle":      {"type": "keyword"},
      "columns": {
        "type": "nested",
        "properties": {
          "name": {"type": "keyword"},
          "type": {"type": "keyword"},
          "desc": {"type": "text", "analyzer": "nori"},
          "pk":   {"type": "boolean"},
          "pii":  {"type": "boolean"},
          "fk":   {"type": "keyword"}
        }
      }
    }
  }
}'
# nori 분석기: 한국어 형태소 분석기. ES에 nori 플러그인이 설치되어 있어야 한다.
# 미설치 시: bin/elasticsearch-plugin install analysis-nori

# 2) 보고서 SQL 인덱스 — 기존 보고서 SQL 참조용
# 현재 코드에서 "report_sql" 인덱스명을 사용한다.
curl -X PUT "http://${ES_HOST}:${ES_PORT}/report_sql" -H 'Content-Type: application/json' -d '{
  "mappings": {
    "properties": {
      "report_name":  {"type": "text", "analyzer": "nori"},
      "description":  {"type": "text", "analyzer": "nori"},
      "sql":          {"type": "text"}
    }
  }
}'

# 3) 코드 메타 인덱스 — 코드값 매핑 검색용
# 현재 코드에서 "code_meta" 인덱스명을 사용한다.
curl -X PUT "http://${ES_HOST}:${ES_PORT}/code_meta" -H 'Content-Type: application/json' -d '{
  "mappings": {
    "properties": {
      "code_field": {"type": "keyword"},
      "codes":      {"type": "object", "enabled": true}
    }
  }
}'
```

#### 실 데이터 색인

```text
테이블 메타 데이터를 ES에 색인하는 ETL 작업이 필요하다.
정보계 DB의 information_schema 또는 DBA가 관리하는 메타 테이블에서
테이블명, 컬럼명, 설명 등을 추출하여 ES에 bulk 색인한다.

이 작업은 보통 데이터 관리팀이나 DBA와 협의하여 수행한다.
```

| 색인 대상 | 데이터 소스 | 예상 건수 | 주기 |
| --------- | ----------- | --------- | ---- |
| 테이블 메타 | 정보계 메타 테이블 / information_schema | 수백~수천 건 | 스키마 변경 시 |
| 보고서 SQL | 기존 보고서 관리 시스템 | 수백~수천 건 | 보고서 등록/수정 시 |
| 코드 메타 | 코드 관리 테이블 | 수십~수백 종 | 코드 변경 시 |

### 6-2. PostgreSQL (정보계) — 읽기 전용 계정 확인

```sql
-- DBA에게 요청하여 계정 권한을 확인한다.
-- data_copilot_ro 계정이 SELECT만 가능한지 반드시 검증한다.
-- 아래 쿼리들이 모두 실패해야 정상이다.

-- 1) INSERT 불가 확인
INSERT INTO TB_CUST_INFO (CUST_NO) VALUES ('TEST');
-- 기대 결과: ERROR: permission denied

-- 2) UPDATE 불가 확인
UPDATE TB_CUST_INFO SET CUST_NM = 'TEST' WHERE 1=0;
-- 기대 결과: ERROR: permission denied

-- 3) DELETE 불가 확인
DELETE FROM TB_CUST_INFO WHERE 1=0;
-- 기대 결과: ERROR: permission denied

-- 4) DROP 불가 확인
DROP TABLE TB_CUST_INFO;
-- 기대 결과: ERROR: permission denied

-- 5) SELECT 정상 동작 확인
SELECT COUNT(*) FROM TB_CUST_INFO;
-- 기대 결과: 정상 건수 반환
```

### 6-3. PostgreSQL (이력) — 이력 테이블 생성

```sql
-- SQL 실행 이력을 저장할 테이블이다.
-- 현재 DUMMY_SQL_HISTORY 구조를 기반으로 실 테이블을 생성한다.
-- 유사 SQL 검색에 사용되므로, 텍스트 검색 인덱스를 추가한다.

CREATE TABLE IF NOT EXISTS sql_execution_history (
    id              SERIAL PRIMARY KEY,
    query_text      TEXT NOT NULL,          -- 사용자 원문 질의
    generated_sql   TEXT NOT NULL,          -- 생성된 SQL
    executed_at     TIMESTAMP DEFAULT NOW(), -- 실행 시각
    execution_ms    INTEGER,                -- 실행 소요 시간(ms)
    result_rows     INTEGER,                -- 결과 행 수
    success         BOOLEAN DEFAULT TRUE,   -- 성공 여부
    session_id      VARCHAR(128),           -- 세션 ID
    error_message   TEXT                    -- 실패 시 오류 메시지
);

-- 유사 질의 검색용 인덱스
-- pg_trgm 확장을 사용하면 퍼지 매칭이 가능하다.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX idx_query_text_trgm ON sql_execution_history
    USING gin (query_text gin_trgm_ops);

-- 시간순 조회용 인덱스
CREATE INDEX idx_executed_at ON sql_execution_history (executed_at DESC);
```

```text
또한 HistoryDBConnector.search_similar_sql()의 실제 구현부를 작성해야 한다.
현재 코드에는 빈 리스트를 반환하는 TODO 상태이다.
(postgres_connector.py L265: "# 실제 구현 시 벡터 유사도 또는 키워드 검색 → return []")
```

### 6-4. Qdrant — 업무 매뉴얼 벡터 적재

```text
현재 DUMMY_MANUALS에는 5건만 있다.
실 환경에서는 은행 내 업무 매뉴얼, 규정집, 계수산출식 문서 등을
벡터 임베딩하여 Qdrant 컬렉션에 적재해야 한다.
```

```python
# 업무 매뉴얼 벡터 적재 예시 스크립트이다.
# 임베딩 모델은 폐쇄망 내에서 사용 가능한 모델을 선택해야 한다.

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct

client = QdrantClient(host="10.xx.xx.xx", port=6333)

# 컬렉션 생성 — 임베딩 차원은 사용하는 임베딩 모델에 따라 달라진다.
# 예: multilingual-e5-large → 1024차원
client.create_collection(
    collection_name="business_manual",
    vectors_config=VectorParams(
        size=1024,                 # 임베딩 모델 차원에 맞게 수정
        distance=Distance.COSINE,
    ),
)

# 문서를 청크로 분할하고 임베딩하여 적재한다.
# 이 부분은 문서 수집·전처리 파이프라인을 별도 구축해야 한다.
```

```text
Qdrant 벡터 검색을 사용하려면 QdrantConnector.search_manual()의
실제 구현부도 완성해야 한다.
현재 코드에서 주석 처리된 부분이다.
(qdrant_connector.py L140-L147)

필요 작업:
1. 폐쇄망 내 임베딩 모델 선정 (multilingual-e5-large 등)
2. 임베딩 함수 구현 (_get_embedding)
3. 주석 해제 및 컬렉션명/벡터 차원 맞춤 수정
```

### 6-5. Redis — 캐시 연결

```bash
# Redis 연결 테스트.
# 현재 config.py에 Redis 설정이 있으나, 실제 캐시 로직은 미구현 상태이다.
# 캐시 적용은 향후 개선사항에 해당한다.
python -m uv run python -c "
import redis
r = redis.Redis(host='10.xx.xx.xx', port=6379, db=0)
r.ping()
print('[PASS] Redis 연결 성공')
"
```

---

## 7. 도메인 데이터 실 데이터 교체

### 7-1. 교체 대상 요약

```text
아래 항목들은 현재 더미 테이블(TB_CUST_INFO 등) 기준으로 작성되어 있다.
실 DB 스키마가 확정되면 실 테이블명/컬럼명으로 교체해야 한다.
```

| 항목 | 파일 | 변경 내용 |
| ---- | ---- | --------- |
| **도메인 사전** | `src/services/domain/finance_terms.py` | 테이블명 매핑을 실 DB 테이블명으로 교체, SQL 조건값을 실 코드값에 맞게 수정 |
| **프롬프트 퓨샷** | `src/agents/nodes/prompts/system_prompts.py` | SQL 생성 예제의 테이블명/컬럼명을 실 DB 기준으로 교체 |
| **골든셋** | `evaluation/golden_set/golden_queries.json` | `expected_sql`, `expected_tables`를 실 DB 기준으로 전면 재작성 |
| **PII 컬럼 목록** | `src/agents/nodes/sql_validator.py` | PII_COLUMNS, MASKING_COLUMNS에 실 DB의 PII 컬럼 변형명 추가 |

### 7-2. 도메인 사전 교체 예시

```python
# src/services/domain/finance_terms.py
# AS-IS (더미):
# "tables": ["TB_CUST_INFO"],
# "conditions": {"CUST_TYPE_CD": "01"},

# TO-BE (실 DB 예시 — 실 테이블명/코드값으로 교체):
# "tables": ["DW.T_DP_CUST_M"],           # 실 스키마.테이블명
# "conditions": {"DP_CUST_DVCD": "10"},    # 실 코드값
```

### 7-3. 골든셋 재작성

```text
골든셋은 SQL 생성 정확도 평가의 기준이므로,
실 DB 스키마와 정확히 일치해야 한다.

재작성 절차:
1. 실 DB 스키마 확정
2. 업무 담당자와 협의하여 대표 질의 30~100건 선정
3. 각 질의에 대한 expected_sql을 실 DB 기준으로 작성
4. evaluator.py로 기본 평가 실행하여 골든셋 자체의 정합성 검증
```

---

## 8. 로컬 PC 동작 테스트

### 8-1. 단위 테스트 (Dummy 모드)

```bash
# 기존 단위 테스트를 먼저 실행하여 코드 수정으로 인한 회귀를 확인한다.
# Dummy 모드 테스트이므로 외부 연결 없이 실행 가능하다.
python -m uv run pytest tests/unit/ -v

# 기대 결과: 전건 통과
# 테스트 실패 시: LLM 클라이언트 어댑터 변경 등으로 인한 인터페이스 불일치 확인
```

### 8-2. 커넥터 개별 연결 테스트

```bash
# 각 데이터소스에 대한 연결 테스트를 개별 수행한다.
# 하나라도 실패하면 해당 인프라 접근을 먼저 해결해야 한다.

# 1) 정보계 DB 연결 + SELECT 테스트
python -m uv run python -c "
import asyncio
from src.connectors.postgres_connector import InfoDBConnector

async def test():
    db = InfoDBConnector(use_dummy=False)
    await db.connect()
    print('[PASS] 정보계 DB 연결 성공')

    # 간단한 SELECT로 데이터 조회 가능 여부 확인
    rows = await db.execute_query('SELECT 1 AS test_col')
    print(f'[PASS] SELECT 실행 성공: {rows}')
    await db.disconnect()

asyncio.run(test())
"

# 2) ElasticSearch 연결 + 검색 테스트
python -m uv run python -c "
import asyncio
from src.connectors.elasticsearch_connector import ElasticSearchConnector

async def test():
    es = ElasticSearchConnector(use_dummy=False)
    await es.connect()
    ok = await es.health_check()
    print(f'[{\"PASS\" if ok else \"FAIL\"}] ES 연결')

    # 테이블 메타 검색 테스트
    results = await es.search_table_meta('고객')
    print(f'[INFO] 검색 결과: {len(results)}건')
    await es.disconnect()

asyncio.run(test())
"

# 3) Qdrant 연결 테스트
python -m uv run python -c "
import asyncio
from src.connectors.qdrant_connector import QdrantConnector

async def test():
    qd = QdrantConnector(use_dummy=False)
    await qd.connect()
    ok = await qd.health_check()
    print(f'[{\"PASS\" if ok else \"FAIL\"}] Qdrant 연결')
    await qd.disconnect()

asyncio.run(test())
"
```

### 8-3. Health Check API 테스트

```bash
# 서버를 실 모드로 기동하여 전체 커넥터 상태를 확인한다.
# .env에 USE_DUMMY=false가 설정되어 있어야 한다.

# 터미널 1: 서버 기동
python -m uv run uvicorn src.main:app --host 0.0.0.0 --port 8000

# 터미널 2: Health Check
curl http://localhost:8000/health
# 기대 결과:
# {
#   "status": "ok",
#   "connectors": {
#     "elasticsearch": true,
#     "info_db": true,
#     "history_db": true,
#     "qdrant": true,
#     "llm_api": true
#   }
# }
```

### 8-4. E2E 파이프라인 테스트

```bash
# 실 데이터소스 연결 상태에서 CLI로 파이프라인 전체를 실행한다.
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
# - SQL이 실 테이블명/컬럼명으로 생성되는가
# - SQL 검증(sql_validator)을 통과하는가
# - 정보계 DB에서 정상 실행되는가
# - 결과가 한국어로 포맷팅되는가
```

### 8-5. WebSocket 챗봇 UI 테스트

```bash
# 브라우저에서 http://localhost:8000 접속 후 챗봇 UI에서 직접 질의한다.
# WebSocket 연결 → 질의 전송 → 응답 수신까지 전체 흐름을 검증한다.

# 테스트 시나리오:
# 1. 단순 질의 → 정상 응답 확인
# 2. 명확화 필요 질의 → 선택지 제시 확인
# 3. 분석 질의 → 차트(SVG) 렌더링 확인
# 4. 프롬프트 인젝션 시도 → 차단 메시지 확인
# 5. 연속 대화 → 세션 컨텍스트 유지 확인
```

### 8-6. 골든셋 평가 (선택)

```bash
# 실 데이터소스 연결 후 골든셋 기반 SQL 생성 정확도를 측정한다.
# 골든셋이 실 DB 기준으로 재작성된 상태에서만 의미 있다.
python -m uv run python -m devtools.evaluation.evaluator
```

---

## 9. 유의사항 체크리스트

### 보안

- [ ] **정보계 DB 계정이 읽기 전용(SELECT only)인지 DB 레벨에서 확인** — 코드의 방어 로직과 별개로 DB 권한 자체가 제한되어야 안전
- [ ] **.env 파일이 Git에 커밋되지 않는지 확인** — `.gitignore`에 `.env` 포함 여부 점검
- [ ] **골든셋에 실 PII가 포함되지 않도록 검수** — 테스트 데이터에 실제 고객 정보가 들어가면 안 됨
- [ ] **로그에 PII가 평문으로 기록되지 않는지 확인** — `LOG_LEVEL=DEBUG` 시 민감 정보 노출 가능성 점검
- [ ] **WebSocket을 wss://(TLS)로 전환** — 폐쇄망이라도 내부 네트워크 스니핑 방지를 위해 TLS 적용 권장
- [ ] **PII 컬럼 목록에 실 DB 컬럼 변형명 추가** — `RRNO`, `ACCT_NO` 등 은행 DB 특유의 약어 반영

### 네트워크

- [ ] **폐쇄망 방화벽 규칙 확인** — 로컬 PC → 각 데이터소스 서버 간 포트 통신 허용 여부
- [ ] **DNS 확인** — 폐쇄망에서 호스트명 해석이 안 되면 IP 직접 지정
- [ ] **프록시 설정** — 폐쇄망 내 HTTP 프록시가 필요한 경우 `HTTP_PROXY`, `HTTPS_PROXY` 환경변수 설정

### 패키지

- [ ] **전이 의존성 누락 확인** — `uv sync` 시 오류 발생하면 누락 wheel 추가 이관
- [ ] **플랫폼 호환성** — 인터넷 PC와 폐쇄망 PC의 OS/아키텍처가 다르면 해당 플랫폼용 wheel 필요
- [ ] **C 확장 패키지 주의** — `asyncpg`, `psycopg2-binary`, `grpcio` 등은 OS별 바이너리 wheel이 다름

### 데이터

- [ ] **ES 인덱스에 nori 한국어 분석기 설치 확인** — 미설치 시 한국어 검색 품질 저하
- [ ] **Qdrant 임베딩 모델 폐쇄망 사용 가능 여부 확인** — 임베딩 모델도 오프라인으로 이관 필요
- [ ] **정보계 DB 스키마와 도메인 사전 매핑 정합성 검증** — 테이블명/컬럼명 오타 시 SQL 생성 실패

### 싱글턴 주의

- [ ] **ConnectorManager 싱글턴의 use_dummy 고정 문제 인지** — 최초 호출 시 설정된 값이 이후 변경 불가. 테스트 시 주의 (design-review.md에서 지적된 사항)

---

## 10. 향후 개선사항

### 즉시 (폐쇄망 안정화 직후)

| 항목 | 설명 | 관련 코드 |
| ---- | ---- | --------- |
| **use_dummy Settings 일원화** | `use_dummy` 하드코딩 3곳을 `settings.use_dummy`로 통합하여 `.env`에서만 제어 | `server.py`, `main.py`, `manager.py` |
| **HistoryDB 유사 SQL 검색 구현** | 현재 빈 리스트 반환 상태를 pg_trgm 또는 벡터 유사도 검색으로 구현 | `postgres_connector.py` L265 |
| **Qdrant 실 벡터 검색 구현** | 주석 처리된 벡터 검색 코드 활성화 + 임베딩 함수 구현 | `qdrant_connector.py` L140-L147 |
| **LLM health check 실제 연결 확인** | 현재 API 키 존재 여부만 체크 → 실제 LLM API ping 호출로 변경 | `server.py` L91 |

### 단기 (1~2개월)

| 항목 | 설명 |
| ---- | ---- |
| **Rate Limiting** | WebSocket/REST API에 요청 횟수 제한 추가 (DoS 방어) |
| **사용자 인증** | JWT 또는 세션 쿠키 기반 인증. 폐쇄망 SSO 연동 검토 |
| **세션 저장소 Redis 전환** | 메모리 기반 세션 → Redis 기반으로 전환 (서버 재시작 시 세션 유지) |
| **감사 로그 DB 저장** | structlog 외에 SQL 실행 이력을 별도 감사 테이블에 영구 저장 |
| **SQL 자기수정 루프** | SQL 실행 오류 시 LLM에 오류 메시지를 피드백하여 자동 재생성 |

### 중기 (3~6개월)

| 항목 | 설명 |
| ---- | ---- |
| **Redis 캐시 본격 적용** | 동일/유사 질의에 대한 SQL + 결과 캐싱으로 LLM 호출 절감 |
| **PII 컬럼 목록 중앙화** | `sql_validator.py`와 `security.py`에 분산된 PII 목록을 단일 소스로 통합 |
| **EXPLAIN 기반 쿼리 최적화** | 생성된 SQL의 실행 계획을 사전 분석하여 풀스캔 방지 |
| **골든셋 CI/CD 통합** | 프롬프트/모델 변경 시 골든셋 자동 평가로 정확도 하락 조기 감지 |
| **멀티턴 분석 세션 강화** | 연속 질의 시 이전 분석 결과를 컨텍스트로 유지 |

### 장기 (6개월~)

| 항목 | 설명 |
| ---- | ---- |
| **사용자 피드백 루프** | "결과 맞음/틀림" 피드백을 수집하여 SQL 이력 품질 점진적 향상 |
| **테이블 선택 정밀도 고도화** | 수백 개 테이블 중 정확한 테이블 선택을 위한 2단계 검색 (ES 1차 → LLM 2차) |
| **분석 결과 캐시 + 증분 업데이트** | 정기 분석 결과를 캐싱하고 데이터 갱신 주기에 맞춰 갱신 |
| **프롬프트 인젝션 패턴 정기 업데이트** | 분기별 새로운 공격 패턴 검토 및 방어 규칙 추가 |
| **컨텍스트 수집 완전 병렬화** | ES + Qdrant + 이력 DB 검색을 `asyncio.gather()`로 동시 수행 |

---

## 부록: 파일별 수정 체크리스트

```text
폐쇄망 이관 시 수정이 필요한 파일과 수정 유형을 정리한다.
[필수]는 실 데이터소스 연결에 반드시 필요한 수정이고,
[권장]은 운영 안정성을 위해 함께 적용하면 좋은 수정이다.
```

| 파일 | 수정 유형 | 내용 |
| ---- | --------- | ---- |
| `.env` (신규) | **[필수]** 신규 작성 | `LLM_PROVIDER`, API 키, 데이터소스 접속 정보 입력 |
| `src/main.py` L59 | **[필수]** 모드 전환 | `use_dummy=True` → `settings.use_dummy` |
| `src/agents/graph/runner.py` L85 | **[필수]** 모드 전환 | `use_dummy=True` → `settings.use_dummy` |
| `src/connectors/manager.py` L69 | **[권장]** 기본값 변경 | `use_dummy` 기본값을 `settings.use_dummy`로 |
| `src/services/domain/finance_terms.py` | **[필수]** 데이터 교체 | 테이블명/조건값을 실 DB에 맞게 수정 |
| `src/agents/nodes/prompts/system_prompts.py` | **[필수]** 예제 교체 | 퓨샷 SQL 예제를 실 DB 기준으로 수정 |
| `evaluation/golden_set/golden_queries.json` | **[필수]** 전면 재작성 | 실 DB 스키마 기준으로 전체 재작성 |
| `src/agents/nodes/sql_validator.py` | **[권장]** PII 확장 | 실 DB PII 컬럼 변형명 추가 |
| `src/connectors/qdrant_connector.py` | **[권장]** 구현 완성 | 벡터 검색 코드 주석 해제 + 임베딩 함수 구현 |
| `src/connectors/postgres_connector.py` | **[권장]** 구현 완성 | 유사 SQL 검색 실제 구현 |

```text
참고: 아래 파일들은 이미 프로바이더 전환이 구현되어 있으므로 코드 수정이 불필요하다.
.env 의 LLM_PROVIDER, OPENAI_API_KEY, OPENAI_BASE_URL 설정만으로 전환된다.

- src/config.py           — llm_provider, openai_api_key, openai_base_url, llm_parse_max_retry 설정 구현됨
- src/utils/llm/client.py — UnifiedLLMClient 통합 래퍼 구현됨 (Anthropic / OpenAI 호환 자동 전환)
- src/utils/llm/retry.py  — 소형 LLM 포맷 파싱 재시도 메커니즘 구현됨
- pyproject.toml          — openai SDK 의존성 이미 포함됨
```
