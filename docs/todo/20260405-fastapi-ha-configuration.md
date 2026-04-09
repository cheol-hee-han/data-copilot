# FastAPI A/A 이중화 구성 설계

- **작성일**: 2026-04-05
- **상태**: 설계 완료, 선행 작업 대기
- **참조**: `docs/todo/20260405-postgres-conversation-history-design.md`
- **참조**: `docs/todo/20260403-production-readiness-checklist.md` (§2.4, §7.2, §8.3)

---

## 1. 구성 개요

L4 로드밸런서(nginx/HAProxy)에서 source IP hash 방식으로 두 대의
FastAPI 인스턴스에 트래픽을 분배한다.
모든 상태 저장소를 PostgreSQL로 통일하여 **Redis 없이** A/A 이중화한다.

```
[클라이언트]
     │
     ▼
[L4 Load Balancer — IP hash]
     ├──────────────────┐
     ▼                  ▼
[FastAPI A :8000]  [FastAPI B :8001]
     │                  │
     └────────┬─────────┘
              ▼
     [PostgreSQL history_db]
      ├ checkpoints           ← 파이프라인 상태 (AsyncPostgresSaver)
      ├ checkpoint_dc_turn_texts    ← 대화 이력 + 감사
      └ checkpoint_dc_session_index ← 세션 인덱스
```

---

## 2. 공유 저장소

모든 영속 데이터는 PostgreSQL(history_db) 한 곳에 저장한다.

| 데이터 | 테이블 | 설명 |
|--------|--------|------|
| 파이프라인 상태 | `bdptbl.checkpoints` 등 | AsyncPostgresSaver 자동 저장. `checkpointer.backend=postgres` 전환 필요 |
| 대화 이력 | `bdptbl.checkpoint_dc_turn_texts` | 경량 TEXT 기반, 역직렬화 없이 SQL SELECT 조회 |
| 세션 목록 | `bdptbl.checkpoint_dc_session_index` | user_id → thread_id 매핑 |

인스턴스 A에서 시작한 대화를 인스턴스 B에서 이어갈 수 있다.
체크포인터의 interrupt/resume도 인스턴스가 바뀌어도 정상 동작한다.

---

## 3. 인스턴스별 독립 자원

각 인스턴스가 독립적으로 보유하며 공유할 필요 없는 것들이다.

| 항목 | 크기 | 공유 불필요 사유 |
|------|------|----------------|
| BGE-M3 임베딩 모델 | ~570MB | 검색 요청은 어느 인스턴스에서 실행해도 동일한 결과 |
| ONNX Reranker 모델 | ~200MB | 동일 |
| SQL 결과 캐시 (`_sql_result_cache`) | 가변 | L4 IP hash로 같은 클라이언트가 같은 인스턴스로 라우팅 |
| structlog 파일 핸들 | — | 인스턴스별 로그 파일 분리 또는 stdout 출력 |

### SQL 결과 캐시 보충 설명

메모리 dict에 저장되어 인스턴스 간 공유 불가하지만, L4 IP hash로
같은 클라이언트의 WebSocket(질의)과 POST(다운로드)가 같은 인스턴스로 간다.
인스턴스 재시작 시 캐시가 소실되지만 기존에
`"조회 결과가 만료되었습니다"` 안내가 구현되어 있으므로 추가 작업 불필요.

---

## 4. L4 IP Hash 선택 이유

WebSocket은 long-lived 연결이라 같은 클라이언트의 요청이
같은 인스턴스로 가야 한다. L4 IP hash가 이를 자연스럽게 보장한다.

| 대안 | 장단점 |
|------|--------|
| **L4 IP hash (채택)** | 단순, WebSocket 자동 보장. NAT 환경에서 쏠림 가��� |
| L7 cookie sticky | NAT 뒤에서도 사용자별 분배 가능. WebSocket upgrade 시 쿠키 전달 설정 필요 |
| L7 URL path routing | REST API에는 적합하나 WebSocket은 path 기반 분배 어려움 |

---

## 5. 폐쇄망 NAT 쏠림 대비

같은 부서가 같은 NAT IP를 쓰면 한쪽 인스턴스에 쏠릴 수 있다.
사용자 규모가 수십~수백 명 수준이면 실제 문제가 되지 않는다.

쏠림이 심할 경우 대응:
- `ip_hash` + `consistent` 키워드: 인스턴스 추가/제거 시 재분배 최소화
- L7 cookie-based sticky session 전환: NAT 뒤에서도 사용자별 분배 가능
- `least_conn` fallback: 연결 수 기반 분배로 임시 대응

---

## 6. nginx 역할

L4/L7 로드밸런싱 외에 다음도 nginx에서 처리한다.

| 역할 | 설명 |
|------|------|
| HTTPS 종단 | 인증서 관리, TLS 오프로드 |
| 정적 파일 서빙 | `/vendor/` 경로를 FastAPI 거치지 않고 직접 ��빙 (부하 절감) |
| WebSocket proxy | `Upgrade` 헤더 전달, `proxy_read_timeout 300s` 장시간 연결 허용 |
| 인스턴스 장애 감지 | `/health/ready` 주기적 폴링, 503 시 트래픽 자동 제외 |
| 요청 크기 제한 | `client_max_body_size` 설정으로 대용량 payload DoS 방지 |
| 보안 헤더 | HTTPS 관련 헤더는 nginx에서 추가 (HSTS 등) |

---

## 7. 인스턴스 서버 설정

각 인스턴스는 **gunicorn + uvicorn worker=1**로 실행한다.

멀티워커를 쓰지 않는 이유:
- 임베딩 모델(BGE-M3 ~570MB)이 워커마다 중복 로드 → 메모리 폭증
- TimedRotatingFileHandler의 lock이 프로세스 간 보호 불가 → 로그 파일 깨짐
- DB 커넥션 풀이 워커마다 생성 → workers × pool_size ≤ DB max_connections 필요

대신 **A/A 두 인스턴스로 가용성을 확보**하고,
asyncio 기반 동시 처리로 단일 워커에서도 충분한 처리량을 제공한다.

---

## 8. 인스턴스당 메모리 요구량

| 항목 | 추정 |
|------|------|
| Python 프로세스 기본 | ~100MB |
| BGE-M3 임베딩 모델 | ~570MB |
| ONNX Reranker 모델 | ~200MB |
| DB 커넥션 풀 + asyncio 이벤트 루프 | ~100MB |
| 파이프라인 동시 실행 여유 | ~200MB |
| **합계** | **~1.2 ~ 1.5GB** |

인스턴스 2대 = **최소 3GB 이상** 서버 메모리 확보 필요.

---

## 9. 로그 처리

### 단일 인스��스 (현재)

`logs/app.log`��� TimedRotatingFileHandler로 기록. 이번 세션에서 구현 완료.

### A/A 이중화 시

두 인스턴스가 같은 로그 파일에 쓰면 깨짐. 두 가지 방법:

| 방법 | 설명 |
|------|------|
| **인스턴스별 로그 분리** | 각 인스턴스가 자기 디렉토리에 기록 (`logs/a/app.log`, `logs/b/app.log`) |
| **stdout + 외부 수집 (권장)** | `LOG_FORMAT=json` + stdout 출력. nginx 또는 syslog에서 수집. 폐쇄망에서는 ELK/Splunk 자체 호스팅 |

`LOG_FORMAT=json` 전환은 이번 세션에서 이미 구현 완료.

---

## 10. 장애 시나리오별 동작

| 시나리오 | 동작 |
|----------|------|
| 인스턴스 A 다운 | nginx가 `/health/ready` 503 감지 → B로만 트래픽 전달 |
| 인스턴스 A 재시작 | nginx가 `/health/ready` 200 감지 → A로 트래픽 복원 |
| A에서 대화 중 A 다운 | WebSocket 끊김 → 클라이언트 재연결 → B에서 대화 이력 PostgreSQL 조회 → 이어서 대화 |
| PostgreSQL 다운 | 양쪽 인스턴스 모두 `/health/ready` 503 → 전체 서비스 불가 (SPOF) |
| A에서 SQL 실행 후 B에서 다운로드 | L4 IP hash로 같은 인스턴스에 라우팅되므로 정상. 인스턴스 재시작 시에만 캐시 miss → "만료" 안내 |

### PostgreSQL SPOF 대비 (향후)

현재는 단일 PostgreSQL이지만, 향후:
- PostgreSQL read replica + 커넥션 failover 설정
- 또는 pgbouncer/HAProxy 기반 커넥션 라우팅
- production-readiness-checklist §16.4 참조

---

## 11. 선행 작업 체크리스트

이중화 전에 **반드시 완료**해야 하는 것들이다.

| # | 작업 | 상태 | 비고 |
|---|------|------|------|
| 1 | `checkpointer.backend=postgres` 전환 | `.env` 변경만 | 코드 구현 완료 |
| 2 | `checkpoint_dc_turn_texts` DDL + 구현 | 설계 완료, 구현 대기 | 대화 이력 PostgreSQL 전환 |
| 3 | `checkpoint_dc_session_index` DDL + 구현 | 설계 완료, 구현 대기 | 세션 목록 PostgreSQL 전환 |
| 4 | SessionStore → turn_text_store 전환 | 설계 완료, 구현 대기 | 메모리 의존 제거 |
| 5 | nginx 설정 파일 작성 | 미착수 | L4 IP hash + WebSocket proxy + 정적 파일 |
| 6 | gunicorn.conf.py | ✅ 완료 | workers=1, 멀티워커 주의사항 문서화 |
| 7 | `/health/ready` 엔드포인트 | ✅ 완료 | 필수 커넥터 실패 시 503 반환 |
| 8 | 보안 헤더 미들웨어 | ✅ 완료 | X-Frame-Options, X-Content-Type-Options 등 |
| 9 | 글로벌 예외 핸들러 | ✅ 완료 | ValidationError + 500 안전망 |
| 10 | Secrets + PII 로그 마스킹 | ✅ 완료 | structlog 프로세서 레벨 일괄 마스킹 |

**1~4번 완료 후**, 동일한 `.env`(DB 접속 정보)로 FastAPI 인스턴스 2대를
nginx 뒤에 배치하면 A/A 이중화가 완성된다.

---

## 12. 이중화 배포 절차 (요약)

1. PostgreSQL에 `bdptbl` 스키마 + DDL 실행 (checkpointer 테이블 + dc 테이블)
2. `.env` 설정: `CHECKPOINTER__BACKEND=postgres`, DB 접속 정보 동일
3. FastAPI 인스턴스 A, B 각각 기동: `gunicorn src.main:app -c gunicorn.conf.py`
4. nginx 설정: L4 IP hash → A:8000, B:8001
5. `/health/ready` 확인: 양쪽 모두 200 반환
6. nginx reload → 트래픽 분배 시작
