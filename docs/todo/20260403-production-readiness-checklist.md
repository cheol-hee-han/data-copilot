# 프로덕션 운영환경 보완 체크리스트

> 폐쇄망 이관 전, 운영환경에서 안정적으로 동작하기 위해 필요한 보완/구현 항목을 정리한다.
> 현재 코드베이스 분석 기준일: 2026-04-03

---

## 1. 서버 기동 및 커넥터 초기화

### 1.1 커넥터 연결 검증 부재

**현상**: `connect_all()`의 각 커넥터 `connect()`는 클라이언트 객체만 생성하고 실제 연결을 시도하지 않음.
`AsyncIOMotorClient`, `AsyncQdrantClient`, `create_async_engine` 등 모두 lazy 초기화라 기동 시 연결 실패를 감지 못함.

**보완**:
- `connect_all()` 후 `health_check_all()`을 1회 실행하여 실제 연결 검증
- 필수 커넥터(MongoDB, Qdrant, info_db) 실패 시 서버 기동 중단
- 선택 커넥터(ES, Neo4j — 현재 미사용) 실패 시 degraded 모드로 기동, 로그 경고

### 1.2 미사용 커넥터 정리

**현상**: ES, Neo4j는 사용하지 않지만 `connect_all()`/`disconnect_all()`/`health_check_all()`에 포함.
config.py에도 ES 관련 설정 16줄, Neo4j 관련 설정 8줄이 남아있음.

**보완**:
- `ConnectorManager`에서 ES/Neo4j 제거 또는 feature flag로 비활성화
- config.py에서 ES 설정 블록 정리
- docker-compose.dev.yml에서 ES 컨테이너 제거 또는 주석 처리

### 1.3 커넥션 풀 설정 미비

**현상**: PostgreSQL `create_async_engine`에 `pool_size`, `max_overflow`, `pool_recycle`, `pool_pre_ping` 미설정.
장시간 운영 시 stale 커넥션 누적, 커넥션 풀 고갈 위험.

**보완**:
```python
create_async_engine(
    url,
    pool_size=5,           # 기본 풀 크기
    max_overflow=10,       # 최대 초과 커넥션
    pool_recycle=1800,     # 30분마다 재활용 (DB idle timeout 방어)
    pool_pre_ping=True,    # 사용 전 연결 유효성 검사
    pool_timeout=30,
)
```

### 1.4 연결 재시도 / Circuit Breaker 없음

**현상**: 커넥터 연결 실패 시 즉시 예외. 재시도 로직 없음. 일시적 네트워크 장애에 취약.

**보완**:
- 기동 시 커넥터별 exponential backoff 재시도 (최대 3회)
- 런타임 시 circuit breaker 패턴 도입 검토 (연속 N회 실패 시 일정 시간 차단)
- `tenacity` 라이브러리 활용 가능

---

## 2. FastAPI 서버 구성

### 2.1 미들웨어 전무

**현상**: CORS, Rate Limiting, Request ID, Timeout, GZip 등 미들웨어가 하나도 없음.

**보완**:
```python
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

# CORS (프론트엔드 분리 배포 시 필수)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,  # 설정으로 관리
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# GZip 압축 (응답 크기 절감)
app.add_middleware(GZipMiddleware, minimum_size=1000)
```

- **Rate Limiting**: `slowapi` 또는 직접 구현 (IP/세션 기준)
- **Request ID**: 미들웨어에서 `X-Request-ID` 생성 → structlog context에 바인딩
- **Request Timeout**: 전체 요청 타임아웃 미들웨어 (파이프라인 무한 대기 방지)

### 2.2 Global Exception Handler 없음

**현상**: 개별 엔드포인트에서 try/except 하지만, 글로벌 핸들러 미등록.
Pydantic ValidationError, 예상치 못한 500 에러 등에 대한 일관된 응답 포맷 없음.

**보완**:
```python
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.error("Unhandled exception", error=str(exc), path=request.url.path)
    return JSONResponse(status_code=500, content={"error": "내부 오류가 발생했습니다."})
```

### 2.3 Graceful Shutdown 미흡

**현상**: `lifespan`의 `yield` 이후 `disconnect_all()` 호출은 있지만,
진행 중인 WebSocket 세션이나 파이프라인 실행을 기다리는 로직 없음.

**보완**:
- SIGTERM 수신 시 새 요청 거부 + 진행 중 요청 완료 대기 (drain period)
- Uvicorn `--timeout-graceful-shutdown` 옵션 활용 (기본 0 = 즉시 종료)

### 2.4 Uvicorn 프로덕션 설정 없음

**현상**: main.py 주석에 `--workers 4` 예시가 있지만, gunicorn config나 uvicorn 설정 파일 없음.

**보완**:
- `gunicorn.conf.py` 또는 `uvicorn_config.py` 생성
  ```python
  # gunicorn.conf.py
  bind = "0.0.0.0:8000"
  workers = 4                    # CPU 코어 수 기반
  worker_class = "uvicorn.workers.UvicornWorker"
  timeout = 120                  # worker 타임아웃
  graceful_timeout = 30          # graceful shutdown 대기
  keepalive = 5
  accesslog = "-"
  ```
- 또는 uvicorn CLI 옵션을 환경변수/설정파일로 관리

### 2.5 보안 헤더 없음

**현상**: `X-Frame-Options`, `X-Content-Type-Options`, `Content-Security-Policy`, `Strict-Transport-Security` 등 응답 헤더 미설정.

**보완**: 별도 미들웨어 또는 리버스 프록시(nginx)에서 설정
```
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
Referrer-Policy: strict-origin-when-cross-origin
```

---

## 3. 인증/인가 (Authentication & Authorization)

### 3.1 인증 없음

**현상**: 모든 엔드포인트가 인증 없이 접근 가능. 신뢰된 네트워크 가정.
폐쇄망이라도 내부 사용자 식별 필요 (감사 추적, 접근 제어).

**보완**:
- **SSO 연동 진입점 구현**: OAuth2/SAML2 콜백 엔드포인트
  - 은행 내부 SSO 시스템(Active Directory, LDAP 등)과 연동
  - FastAPI의 `OAuth2PasswordBearer` 또는 커스텀 dependency
- **세션 기반 인증**: SSO 인증 후 JWT 또는 서버사이드 세션 발급
- **WebSocket 인증**: 연결 시 첫 메시지 또는 쿼리 파라미터로 토큰 전달

### 3.2 권한 관리 없음

**현상**: 역할 기반 접근 제어(RBAC) 없음. 모든 사용자가 동일한 권한.

**보완**:
- 역할 정의: `viewer`(조회만), `analyst`(분석+다운로드), `admin`(설정 변경)
- 민감 테이블/컬럼 접근 제어 (개인정보 관련)
- 사용자별/부서별 쿼리 실행 건수 제한

### 3.3 감사 추적 (Audit Trail) 미비

**현상**: 구조화 로그는 있지만, 누가 어떤 쿼리를 실행했는지 추적 불가 (사용자 식별자 없음).
금융권 규정상 데이터 접근 이력 보관 필수.

**보완**:
- 모든 쿼리 실행 이력에 `user_id`, `department`, `ip_address` 기록
- history_db의 `sql_exec_log` 테이블에 사용자 정보 컬럼 추가
- 감사 로그 별도 테이블/파일로 분리 (삭제 불가, 보관 기간 설정)

---

## 4. 대화 이력 관리

### 4.1 메모리 백엔드 — 서버 재시작 시 전량 유실

**현상**: 기본값 `session_backend=memory`. 서버 재시작/배포 시 모든 대화 이력 소멸.
멀티 워커 환경에서 워커 간 세션 공유 불가.

**보완 (단기)**:
- 프로덕션 환경에서는 `session_backend=redis` 강제 또는 기본값 변경
- Redis 장애 시 fallback 전략 (memory + warning)

**보완 (중기)**: PostgreSQL 기반 대화 이력 관리
- `history_db`에 `conversation_history` 테이블 추가
  ```sql
  CREATE TABLE conversation_history (
      id BIGSERIAL PRIMARY KEY,
      session_id VARCHAR(128) NOT NULL,
      user_id VARCHAR(64),          -- SSO 연동 후
      role VARCHAR(16) NOT NULL,    -- user / assistant
      content TEXT NOT NULL,
      entry_type VARCHAR(32),       -- query / response / clarification
      created_at TIMESTAMPTZ DEFAULT NOW(),
      INDEX idx_session (session_id, created_at)
  );
  ```
- 장점: 영속성, 검색 가능, 감사 추적, 워커 간 공유
- Redis는 핫 캐시로 유지, DB는 원본 저장소

### 4.2 세션 만료/정리 백그라운드 작업 없음

**현상**: Redis TTL에만 의존. 메모리 모드에서는 FIFO 밀림으로만 정리.
오래된 세션 메타데이터(생성 시간, 마지막 접근 등) 없음.

**보완**:
- 주기적 세션 정리 백그라운드 태스크 (asyncio.create_task)
- 세션 메타데이터 추가: `created_at`, `last_accessed_at`, `user_id`

---

## 5. 에러 처리 및 Fallback

### 5.1 LLM 호출 실패 시 사용자 경험

**현상**: LLM API 장애/타임아웃 시 generic 에러 메시지만 반환.
사용자는 "나중에 다시 시도해주세요" 이상의 정보를 받지 못함.

**보완**:
- 에러 유형별 사용자 메시지 세분화
  - LLM 타임아웃 → "처리 시간이 초과되었습니다. 질문을 간결하게 바꿔 다시 시도해주세요."
  - DB 연결 실패 → "데이터 조회 시스템에 일시적 문제가 있습니다."
  - 메타 검색 실패 → graceful degradation (일부 정보 없이 진행)
- WebSocket에서 진행 중 에러 시 partial result 전송 가능하도록

### 5.2 커넥터별 Fallback 전략

**현상**: MongoDB 장애 시 전체 파이프라인 실패. Qdrant 장애 시 과거 SQL 참조 불가. 단일 장애점(SPOF).

**보완**:
| 커넥터 | 실패 시 Fallback |
|--------|-----------------|
| MongoDB (메타) | 캐시된 메타 반환 or 제한된 응답 + 경고 |
| Qdrant (매뉴얼/SQL이력) | 해당 참조 건너뜀, 메타 기반으로만 진행 |
| info_db (쿼리 실행) | 실패 불가피, 사용자에게 명확한 안내 |
| Redis (세션) | 메모리 모드 fallback + 경고 로그 |

### 5.3 파이프라인 전체 타임아웃

**현상**: `agentic_total_timeout=120초` 설정은 있지만, HTTP 레벨에서의 전체 요청 타임아웃 미적용.
WebSocket은 타임아웃 없이 무한 대기 가능.

**보완**:
- HTTP POST `/api/query`: `asyncio.wait_for` 또는 미들웨어 타임아웃
- WebSocket: 파이프라인 실행에 `asyncio.wait_for(run_pipeline(...), timeout=...)` 적용
- 타임아웃 시 사용자에게 "처리 시간이 초과되었습니다" 알림

---

## 6. 로깅 및 모니터링

### 6.1 JSON 로그 포맷 미구현

**현상**: `settings.log_format = "json"` 설정은 있지만 실제 JSON 렌더러 미구현.
폐쇄망에서 ELK/Splunk 등 로그 수집 시스템 연동 불가.

**보완**:
- structlog의 `JSONRenderer` 활용
  ```python
  if settings.log_format == "json":
      processors.append(structlog.processors.JSONRenderer())
  ```

### 6.2 로그 로테이션 없음

**현상**: `logs/app.log` 단일 파일에 계속 쓰기. 장기 운영 시 디스크 풀 위험.

**보완**:
- `logging.handlers.RotatingFileHandler` 또는 `TimedRotatingFileHandler`
  ```python
  RotatingFileHandler(
      "logs/app.log",
      maxBytes=100 * 1024 * 1024,  # 100MB
      backupCount=10,
  )
  ```
- 또는 외부 logrotate 설정

### 6.3 메트릭 수집 없음

**현상**: Prometheus, StatsD 등 메트릭 수집 미구현. 요청량, 응답 시간, 에러율 모니터링 불가.

**보완**:
- `prometheus-fastapi-instrumentator` 또는 직접 미들웨어 구현
  - 요청 수 (endpoint별)
  - 응답 시간 (p50/p95/p99)
  - 에러율
  - 활성 WebSocket 연결 수
  - LLM 호출 시간/토큰 사용량
  - 커넥터별 health 상태

### 6.4 Request Tracing

**현상**: LangSmith는 외부 서비스 (폐쇄망 사용 불가). 자체 eval_tracker는 평가용이지 운영 모니터링용 아님.

**보완**:
- 요청별 고유 ID (`X-Request-ID`) → 전체 로그에 전파
- OpenTelemetry 도입 검토 (폐쇄망에서는 Jaeger 자체 호스팅)
- 또는 structlog의 `query_id` 기반 자체 트레이싱 강화

### 6.5 Health Check 세분화

**현상**: `/health` 엔드포인트 1개. Kubernetes liveness/readiness 구분 없음.

**보완**:
- `/health/live` — 프로세스 살아있음 (항상 200)
- `/health/ready` — 모든 필수 커넥터 연결 정상 (커넥터 장애 시 503)
- `/health/startup` — 초기화 완료 여부

---

## 7. 성능 최적화

### 7.1 LLM 동시 호출 제한

**현상**: `llm_concurrency_limit=3`으로 설정만 있고, 실제 세마포어 적용 여부 확인 필요.
동시 사용자 다수일 때 LLM API 과부하 위험.

**보완**:
- 글로벌 `asyncio.Semaphore`로 동시 LLM 호출 제어
- 사용자별 큐잉: 앞선 요청 처리 중이면 대기 메시지 전송
- LLM 응답 캐싱: 동일 질의 패턴에 대한 캐시 (Redis)

### 7.2 임베딩/리랭커 모델 로딩

**현상**: 모델 로딩 시점과 메모리 관리 확인 필요. 워커별 모델 중복 로딩 시 메모리 폭증.

**보완**:
- 모델을 프로세스 레벨 싱글턴으로 관리
- 멀티워커 시 모델 서빙 분리 (별도 프로세스 또는 서비스) 검토
- GPU 미사용(CPU) 환경에서 ONNX 최적화 확인 (이미 `reranker_backend=onnx` 설정 존재)

### 7.3 대용량 결과 처리

**현상**: `max_query_rows=10000`이지만, 대량 결과를 메모리에 전부 올린 후 JSON 직렬화.
`_sql_result_cache`도 메모리 딕셔너리 (워커별 격리, 10000건 * 100세션 시 메모리 부담).

**보완**:
- 결과 스트리밍 (DB 커서 기반 청크 전송)
- CSV 다운로드 시 스트리밍 응답 (현재도 `StreamingResponse` 사용하지만 데이터는 메모리에 전량 적재)
- `_sql_result_cache` → Redis 기반으로 이관 (TTL 적용)

### 7.4 정적 파일 서빙

**현상**: FastAPI가 직접 static 파일 서빙. 프로덕션에서는 비효율적.

**보완**:
- nginx 등 리버스 프록시에서 정적 파일 서빙
- 또는 프론트엔드 빌드 결과물을 별도 서빙 (CDN 불가한 폐쇄망에서는 nginx 정적 경로)

---

## 8. 배포 인프라

### 8.1 애플리케이션 Dockerfile 없음

**현상**: 인프라(PG, Qdrant 등)의 docker-compose는 있지만 애플리케이션 자체의 Dockerfile 없음.

**보완**:
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install uv && uv sync --frozen
COPY . .
EXPOSE 8000
CMD ["gunicorn", "src.main:app", "-c", "gunicorn.conf.py"]
```

### 8.2 프로덕션 docker-compose 없음

**현상**: `docker-compose.dev.yml`만 존재. 프로덕션용 구성(리소스 제한, 볼륨 마운트, 네트워크 격리) 없음.

**보완**:
- `docker-compose.prod.yml` 생성
  - 리소스 제한 (mem_limit, cpus)
  - 볼륨 마운트 (로그, 모델 캐시)
  - 네트워크 격리 (프론트엔드 ↔ 백엔드 ↔ DB)
  - healthcheck 강화

### 8.3 리버스 프록시 설정 없음

**현상**: HTTPS 종단, 로드밸런싱, 정적 파일 서빙을 위한 리버스 프록시 구성 없음.

**보완**:
- nginx 설정 파일 (`nginx.conf`)
  - HTTPS 종단 (인증서 경로)
  - WebSocket 프록시 (`Upgrade` 헤더)
  - 정적 파일 서빙 (`/vendor`, `/static`)
  - Rate limiting
  - 요청 크기 제한 (`client_max_body_size`)

### 8.4 DB 마이그레이션 도구 없음

**현상**: Alembic 등 스키마 버전 관리 도구 없음. 수동 DDL 관리.
checkpointer가 `setup()`으로 테이블 자동 생성하지만, 스키마 변경 이력 없음.

**보완**:
- Alembic 도입 (history_db 스키마 관리)
- 초기 마이그레이션 + 향후 변경 이력 관리
- 롤백 가능한 마이그레이션 스크립트

---

## 9. 관리 편의 기능

### 9.1 Admin API / 관리 대시보드

**현상**: 관리용 엔드포인트 없음. 서버 상태, 활성 세션, 커넥터 상태 등 운영 정보 확인 불가.

**보완**:
- `/admin/sessions` — 활성 세션 목록, 세션별 상태
- `/admin/connectors` — 커넥터별 연결 상태, 풀 사용량
- `/admin/config` — 현재 런타임 설정 조회 (비밀번호 마스킹)
- `/admin/cache/clear` — 캐시 수동 초기화
- 인증 필수 (admin 역할만 접근)

### 9.2 운영 도구

**보완 항목**:
- **로그 레벨 동적 변경**: 재시작 없이 DEBUG ↔ INFO 전환
  ```
  POST /admin/log-level {"level": "DEBUG"}
  ```
- **커넥터 수동 재연결**: 특정 커넥터 장애 복구 후 수동 reconnect
- **파이프라인 실행 통계**: 일별/시간별 질의 건수, 평균 응답 시간, 성공/실패율
- **SQL 실행 이력 조회**: 특정 기간/사용자의 SQL 이력 검색

### 9.3 설정 검증 CLI

**현상**: `.env` 오타나 누락을 기동 전에 확인하는 방법 없음.

**보완**:
```bash
# 기동 전 설정 검증
python -m src.validate_config
# → .env 로드 → 필수값 존재 확인 → DB 연결 테스트 → 결과 출력
```

---

## 10. 기타 운영 권장사항

### 10.1 CSRF 보호

**현상**: POST 엔드포인트에 CSRF 토큰 검증 없음.
폐쇄망 웹 UI에서 POST 요청을 받으므로, 내부 공격 벡터 존재.

### 10.2 요청 크기 제한

**현상**: `QueryRequest.query`의 `max_length=2000`은 Pydantic 레벨.
HTTP body 자체의 크기 제한은 없음 (대용량 payload DoS 가능).

**보완**: 미들웨어 또는 리버스 프록시에서 `Content-Length` 제한

### 10.3 WebSocket 연결 수 제한

**현상**: 동시 WebSocket 연결 수 제한 없음. 리소스 고갈 위험.

**보완**:
- 최대 동시 연결 수 설정 (`max_ws_connections`)
- 연결 초과 시 503 반환
- 비활성 WebSocket idle timeout

### 10.4 Secrets 로깅 방지

**현상**: `settings` 객체가 로그에 출력될 경우 DB 비밀번호 등 노출 가능.
`DbConnectionInfo.dsn`은 비밀번호 제외하지만, 다른 경로로 노출될 수 있음.

**보완**:
- `Settings.__repr__` 오버라이드하여 민감 필드 마스킹
- structlog 프로세서에서 `password`, `api_key` 등 자동 마스킹

### 10.5 의존성 보안 감사

**보완**:
- `pip-audit` 또는 `safety` CLI로 알려진 취약점 점검
- 폐쇄망 반입 전 `.whl` 파일 무결성 검증 (해시 체크)

---

## 11. LLM 안전장치 강화

### 11.1 토큰 예산 관리 부재

**현상**: `llm_default_max_tokens` 등 출력 토큰 설정만 존재. 프롬프트 조립 후 입력 토큰 수를 사전 검증하지 않아 컨텍스트 윈도우 초과 가능.
멀티턴 세션에서 누적 토큰 소비량 추적 없음.

**보완**:
- 프롬프트 조립 후 `tiktoken` 또는 모델별 토크나이저로 입력 토큰 카운팅
- 컨텍스트 윈도우 80% 초과 시 자동 truncation (오래된 대화 이력부터 제거)
- 세션별 누적 토큰 카운터 → 임계치 초과 시 경고/차단
- 특히 폐쇄망 오픈소스 모델(Solar Pro 2 70B: 32K, Qwen3.5: 128K)은 Claude 대비 윈도우가 제한적

### 11.2 LLM 응답 스키마 검증 부재

**현상**: LLM 응답에 대해 JSON 파싱만 수행. 필수 필드 존재 여부, 타입, enum 범위 등 구조 검증 없음.
`_parse_sql_response`에서 `status` 필드 검증은 추가되었으나, 다른 노드의 LLM 응답은 미검증.

**보완**:
- 노드별 LLM 응답 Pydantic 모델 정의 → `model_validate()` 적용
- 검증 실패 시 재시도 또는 구조화된 에러 반환 (현재 KeyError/TypeError로 전파)
- 특히 오픈소스 모델은 JSON 구조 불안정성이 높아 필수

### 11.3 DB 조회 결과 → LLM 전달 시 PII 필터링 부재

**현상**: SQL 생성 시 PII 컬럼 차단은 있으나, DB 조회 결과가 `analyzer`/`formatter` 노드의 LLM 프롬프트에 전달될 때 마스킹 없음.
SQL safety checker는 SELECT 대상 컬럼만 검증하며, 실제 반환된 데이터 값의 PII는 미검사.

**보완**:
- DB 결과 → LLM 전달 전 PII 마스킹 레이어 추가
- 명확화 질의에 사용자 입력 포함 시에도 PII 마스킹 적용
- SQL 실패 시 에러 메시지에 포함될 수 있는 샘플 데이터 필터링
- LangSmith/트레이스 로그에서 PII 자동 삭제

---

## 12. LangGraph 그래프 운영 패턴

> 참조: `docs/research/20260330-langgraph-production-patterns.md`

### 12.1 그래프 싱글턴 컴파일 미적용

**현상**: 리서치에서 "모듈 수준 1회 컴파일 + lifespan checkpointer 사후 주입"을 권고하나,
현재 `runner.py`의 그래프 빌드 패턴이 권고 패턴과 일치하는지 확인 필요.

**보완** (리서치 섹션 5.1 권고):
```python
# pipeline.py — 모듈 수준에서 1회 컴파일
graph = builder.compile()

# main.py — lifespan에서 checkpointer 사후 주입
async with AsyncPostgresSaver.from_conn_string(DB_URI) as saver:
    graph.checkpointer = saver
    yield
```

### 12.2 Checkpoint TTL 미적용 — DB 무한 성장 위험

**현상**: `CheckpointerConfig.thread_ttl_days=30` 설정은 존재하나 **실제 purge 로직 미구현**.
AsyncPostgresSaver는 자동 삭제 기능이 없어 checkpoint 테이블이 무한 성장.

**보완**:
- 주기적 checkpoint 정리 배치 (asyncio background task 또는 cron)
  ```sql
  DELETE FROM checkpoints
  WHERE created_at < NOW() - INTERVAL '30 days';
  ```
- checkpoint 테이블 크기 모니터링 + 알림
- 또는 PostgreSQL 파티셔닝으로 월별 테이블 자동 drop

### 12.3 폐쇄망 트레이싱 전환

**현상**: LangSmith 의존 (폐쇄망 사용 불가). 자체 `eval_tracker`는 평가용이지 운영 모니터링용이 아님.
요청별 `run_id`, `tags`, `metadata` 주입도 미적용.

**보완** (리서치 섹션 2.5 권고):
- `BaseCallbackHandler` 상속 커스텀 핸들러 구현
  - 노드별 실행 시간, LLM 호출 횟수, 토큰 사용량 추적
  - 내부 추적 시스템(PostgreSQL 로그 테이블 또는 OTel Collector)으로 전송
- 호출 시 config callbacks 주입 방식:
  ```python
  result = await graph.ainvoke(state, config={
      "callbacks": [InternalTraceHandler(session_id, user_id)],
      "run_id": uuid4(),
      "tags": ["production", "nl2sql"],
      "metadata": {"user_id": user_id, "env": "prod"},
  })
  ```
- `eval_tracker`의 운영 지표 수집 기능은 커스텀 핸들러로 통합

---

## 13. 프롬프트 버전 관리

### 13.1 프롬프트 변경 추적 없음

**현상**: `resources/prompts/` 파일에 버전 메타데이터 없음.
프롬프트 수정이 SQL 생성 품질에 미치는 영향을 추적할 수 없음.

**보완**:
- 프롬프트 파일 상단에 `# version: 1.2` + 변경 사유 주석
- 또는 별도 `prompt_registry.yaml`로 파일별 버전·변경 이력 관리
- 실행 로그에 프롬프트 파일 해시값 기록 → 품질 회귀 분석 가능

### 13.2 프롬프트 롤백 절차 없음

**현상**: 프롬프트 변경으로 품질 저하 시 git revert + 재배포만 가능.
폐쇄망에서 재배포는 시간 소요가 크므로 핫 롤백 필요.

**보완**:
- 프롬프트 핫 리로드 (파일 변경 감지 → 자동 재로딩)
- 또는 설정으로 프롬프트 버전 선택 (`prompt_version: "v1.1"` → 해당 버전 파일 로드)

---

## 14. 임베딩 모델 일관성

### 14.1 모델 버전 고정 없음

**현상**: `embedding_model="BAAI/bge-m3"` — HuggingFace revision 미지정.
모델 업데이트 시 임베딩 차원/분포가 변경되어 Qdrant 기존 벡터와 불일치 위험.

**보완**:
- HuggingFace revision 해시 고정: `embedding_model_revision="abc123def"`
- 폐쇄망 반입 시 모델 파일 해시 검증

### 14.2 벡터 호환성 검증 없음

**현상**: 임베딩 모델 교체 시 기존 Qdrant 벡터와 불일치를 감지하는 메커니즘 없음.
검색 품질이 조용히 저하될 수 있음.

**보완**:
- 서버 시작 시 샘플 쿼리 임베딩 → 기존 벡터와 코사인 유사도 검증
- 유사도가 임계치 이하면 경고 로그 + 재인덱싱 권고
- 재인덱싱 스크립트 + 블루/그린 컬렉션 전환 전략 문서화

---

## 15. CI/CD 및 품질 게이트

### 15.1 자동화 파이프라인 없음

**현상**: GitHub Actions, Jenkins 등 CI 설정 없음. 코드 변경의 품질 검증이 수동에 의존.

**보완**:
- push/PR 시 자동 실행: `pytest` + `ruff` lint + `mypy` type check
- 폐쇄망에서는 Jenkins 또는 GitLab CI 자체 호스팅
- 골든셋 회귀 테스트를 CI 파이프라인에 포함

### 15.2 테스트 커버리지 기준 없음

**현상**: `pytest-cov` 설치됨, 최소 커버리지 미설정. 커버리지 추적/게이트 없음.

**보완**:
- `pyproject.toml`에 `--cov-fail-under=70` 설정
- 핵심 모듈(pipeline, state, services) 커버리지 80% 이상 목표

### 15.3 테스트 스위트 정비 필요

**현상**: 삭제된 모듈(`intent_resolver`, `history_resolver`)을 참조하는 테스트 5건+ 실패 중.
`@pytest.mark.skip` 사유가 "삭제됨"인 테스트 다수.

**보완**:
- 깨진 import 테스트 수정 또는 삭제
- skip 사유 정리, 불필요한 skip 테스트 제거
- 새 아키텍처(v4)에 맞는 테스트 추가

---

## 16. 백업 및 재해 복구 (DR)

### 16.1 DB 백업 전략 없음

**현상**: MongoDB, PostgreSQL, Qdrant 백업 정책 미수립. 장애 시 데이터 유실 위험.
금융권 규정상 데이터 보관 및 복구 능력 필수.

**보완**:
- 커넥터별 백업 스케줄 수립:
  | DB | 백업 방식 | 주기 | 보관 |
  |----|----------|------|------|
  | PostgreSQL (info_db) | pg_dump + WAL 아카이빙 | 일 1회 full + 실시간 WAL | 30일 |
  | PostgreSQL (history_db) | pg_dump | 일 1회 | 90일 |
  | MongoDB | mongodump | 일 1회 | 30일 |
  | Qdrant | snapshot API | 주 1회 | 4주 |

### 16.2 RTO/RPO 미정의

**현상**: 금융권 요건 기준 복구 목표 미설정.

**보완**:
- RTO (복구 시간 목표): 4시간 이내
- RPO (복구 시점 목표): 1시간 이내
- 정기 DR 훈련 (반기 1회)

### 16.3 장애 복구 절차서 없음

**현상**: 커넥터별 장애 시나리오에 대한 운영 매뉴얼 없음.

**보완**:
- 시나리오별 복구 절차 문서화 (MongoDB 장애, PostgreSQL 장애, Qdrant 장애, LLM API 장애)
- 에스컬레이션 경로 정의 (1차: 자동 재시도 → 2차: 운영자 알림 → 3차: 수동 개입)

### 16.4 DB 페일오버 없음

**현상**: 단일 `info_db_host` — replica/failover 미구성.

**보완**:
- PostgreSQL read replica + 커넥션 스트링 failover 설정
- 또는 pgbouncer/HAProxy 기반 커넥션 라우팅

---

## 17. API 버전 관리

### 17.1 버전 프리픽스 없음

**현상**: `/api/query`, `/ws/{session_id}` — 버전 없는 엔드포인트.
향후 breaking change 시 기존 클라이언트 대응 불가.

**보완**:
- `/api/v1/query`로 변경
- 하위 호환성 유지 기간 + 폐기(deprecation) 정책 수립
- 응답 헤더에 `API-Version` 포함

---

## 18. 프론트엔드 빌드/배포

### 18.1 프로덕션 빌드 설정 미확인

**현상**: React + Vite + TypeScript 프론트엔드의 프로덕션 빌드 설정 및 배포 파이프라인 미확인.
FastAPI `StaticFiles` 마운트는 있으나, 빌드 산출물 경로 관리 불명확.

**보완**:
- `vite build` 프로덕션 빌드 스크립트 확인/정비
- 빌드 결과물 경로와 FastAPI `StaticFiles` 경로 일치 확인
- 환경별(dev/prod) 빌드 설정 분리

### 18.2 폐쇄망 npm 의존성

**현상**: npm 패키지 오프라인 설치 전략 없음.

**보완**:
- `node_modules` 아카이브 또는 private npm registry (Verdaccio 등)
- `package-lock.json` 기반 정확한 버전 고정
- 폐쇄망 반입 전 의존성 무결성 검증

---

## 우선순위 요약

| 순위 | 항목 | 난이도 | 영향도 |
|------|------|--------|--------|
| **P0** | 커넥터 연결 검증 (1.1) | 낮음 | 높음 |
| **P0** | 미사용 커넥터 정리 (1.2) | 낮음 | 중간 |
| **P0** | 커넥션 풀 설정 (1.3) | 낮음 | 높음 |
| **P0** | Global Exception Handler (2.2) | 낮음 | 높음 |
| **P0** | 로그 로테이션 (6.2) | 낮음 | 높음 |
| **P0** | Checkpoint TTL 적용 (12.2) | 낮음 | 높음 |
| **P0** | 테스트 스위트 정비 (15.3) | 낮음 | 높음 |
| **P0** | DB 결과 PII 필터링 (11.3) | 중간 | 높음 |
| **P1** | SSO 인증 진입점 (3.1) | 높음 | 높음 |
| **P1** | 감사 추적 (3.3) | 중간 | 높음 |
| **P1** | CORS/보안 헤더 미들웨어 (2.1, 2.5) | 낮음 | 중간 |
| **P1** | 대화 이력 DB 이관 (4.1) | 중간 | 높음 |
| **P1** | Rate Limiting (2.1) | 낮음 | 중간 |
| **P1** | Dockerfile + 프로덕션 compose (8.1, 8.2) | 중간 | 높음 |
| **P1** | JSON 로그 포맷 (6.1) | 낮음 | 중간 |
| **P1** | 폐쇄망 트레이싱 전환 (12.3) | 중간 | 높음 |
| **P1** | 토큰 예산 관리 (11.1) | 낮음 | 중간 |
| **P1** | CI/CD 파이프라인 (15.1) | 중간 | 높음 |
| **P1** | DB 백업 전략 (16.1) | 중간 | 높음 |
| **P2** | 파이프라인 타임아웃 강화 (5.3) | 낮음 | 중간 |
| **P2** | Fallback 전략 (5.2) | 중간 | 중간 |
| **P2** | 메트릭 수집 (6.3) | 중간 | 중간 |
| **P2** | Health Check 세분화 (6.5) | 낮음 | 낮음 |
| **P2** | Uvicorn/Gunicorn 프로덕션 설정 (2.4) | 낮음 | 중간 |
| **P2** | 리버스 프록시 설정 (8.3) | 중간 | 중간 |
| **P2** | Admin API (9.1) | 중간 | 중간 |
| **P2** | LLM 응답 스키마 검증 (11.2) | 낮음 | 중간 |
| **P2** | 임베딩 모델 버전 고정 (14.1) | 낮음 | 중간 |
| **P2** | 프롬프트 변경 추적 (13.1) | 낮음 | 중간 |
| **P2** | 테스트 커버리지 기준 (15.2) | 낮음 | 중간 |
| **P2** | RTO/RPO 정의 (16.2) | 낮음 | 중간 |
| **P3** | DB 마이그레이션 도구 (8.4) | 중간 | 낮음 |
| **P3** | Circuit Breaker (1.4) | 중간 | 낮음 |
| **P3** | 결과 스트리밍 (7.3) | 중간 | 낮음 |
| **P3** | WebSocket 연결 수 제한 (10.3) | 낮음 | 낮음 |
| **P3** | 설정 검증 CLI (9.3) | 낮음 | 낮음 |
| **P3** | 그래프 싱글턴 패턴 (12.1) | 낮음 | 낮음 |
| **P3** | 벡터 호환성 검증 (14.2) | 낮음 | 낮음 |
| **P3** | 프롬프트 롤백 (13.2) | 낮음 | 낮음 |
| **P3** | API 버전 관리 (17.1) | 낮음 | 낮음 |
| **P3** | DB 페일오버 (16.4) | 중간 | 중간 |
| **P3** | 장애 복구 절차서 (16.3) | 낮음 | 중간 |
| **P3** | 프론트엔드 빌드 설정 (18.1) | 중간 | 중간 |
| **P3** | 폐쇄망 npm 의존성 (18.2) | 중간 | 중간 |
