# Standalone 로컬 테스트 가이드

## 사전 준비

- Python 3.12+
- (선택) Docker Desktop — 실제 DB/MongoDB/Qdrant/Neo4j 테스트 시 필요

## 1. 의존성 설치

```bash
python -m pip install -e ".[dev]"
```

## 2. 서버 실행 (Dummy 모드)

외부 인프라 없이 내장 샘플 데이터로 즉시 테스트 가능합니다.

```bash
# 서버 기동 (코드 변경 시 자동 재시작)
uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
```

브라우저에서 **http://localhost:8000** 접속 → 챗봇 UI

## 3. CLI 테스트

```bash
python -m src.agents.graph.runner "이번 달 신규 고객 수 알려줘"
```

## 4. API 테스트

```bash
# 상태 확인
curl http://localhost:8000/health

# 질의 실행
curl -X POST http://localhost:8000/api/query \
  -H "Content-Type: application/json" \
  -d '{"query": "이번 달 신규 고객 수 알려줘", "include_trace": true}'
```

## 5. 단위 테스트

```bash
pytest tests/ -v
```

## 6. 서버 종료

```bash
# Linux/Mac/Git Bash
pkill -f "uvicorn"

# Windows PowerShell
Get-Process | Where-Object {$_.ProcessName -like "*python*"} | Stop-Process
```

---

## LLM 프로바이더 전환

`.env` 파일에서 사용할 Case의 주석을 해제하고 나머지를 주석 처리합니다.

### Groq (무료, 권장)

```env
LLM_PROVIDER=openai_compatible
OPENAI_API_KEY=your-groq-api-key
OPENAI_BASE_URL=https://api.groq.com/openai/v1
LLM_MODEL=llama-3.3-70b-versatile
```

- 발급: https://console.groq.com → API Keys
- 무료 한도: 30 req/분

### Anthropic Claude

```env
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=your-api-key
LLM_MODEL=claude-sonnet-4-20250514
```

### Ollama (로컬, 오프라인)

```bash
# Ollama 설치 후 모델 다운로드
ollama pull qwen3:8b
```

```env
LLM_PROVIDER=openai_compatible
OPENAI_API_KEY=ollama
OPENAI_BASE_URL=http://localhost:11434/v1
LLM_MODEL=qwen3:8b
```

---

## (선택) Docker 인프라 구축

실제 PostgreSQL / MongoDB / Qdrant / Neo4j 로 테스트하려면:

```bash
# 1. 컨테이너 기동 (2026-04 ES 제거됨)
docker compose -f devtools/docker/docker-compose.dev.yml up -d --build

# 2. 데이터 시딩
python devtools/scripts/seed_postgres.py
python devtools/scripts/seed_mongodb.py          # 테이블/컬럼/코드/용어사전 메타
python devtools/scripts/seed_qdrant.py           # fastembed 모델 필요 (pip install fastembed)
python devtools/scripts/seed_neo4j.py            # 온톨로지 그래프

# 3. .env 에서 Dummy 모드 비활성화
USE_DUMMY=false

# 4. 서버 재시작
uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
```

### 개별 시딩 (필요 시)

```bash
docker compose -f devtools/docker/docker-compose.dev.yml up -d --build
python devtools/scripts/seed_postgres.py
python devtools/scripts/seed_mongodb.py
python devtools/scripts/seed_qdrant.py
python devtools/scripts/seed_neo4j.py
```

### 검색 쿼리 전략 테스트

```bash
# 단위 테스트 (Dummy 커넥터)
pytest tests/test_search_query_builder.py -v

# 실제 Docker 서비스 대상 Live 테스트
pytest tests/test_search_query_builder.py -v

# 골든셋 90건 E2E 컨텍스트 품질 테스트
pytest tests/test_golden_set_context_quality.py -v -s

# Qdrant 벡터 검색 품질 테스트
pytest tests/test_qdrant_vector_search.py -v -s
```

### 인프라 정리

```bash
docker compose -f devtools/docker/docker-compose.dev.yml down -v
```
