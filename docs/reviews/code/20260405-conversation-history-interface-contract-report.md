# 대화 이력 시스템 인터페이스 계약 검증 보고서

- 일시: 2026-04-05
- 범위: REST API / WebSocket / Runner / Pool Sharing / DDL 일관성 검증
- 대상 파일:
  - `src/models/api/session_models.py`
  - `src/services/session_service.py`
  - `src/services/turn_text_store.py`
  - `src/routers/sessions.py`
  - `src/agents/graph/runner.py`
  - `src/agents/models/response.py`
  - `src/agents/graph/checkpointer.py`
  - `src/connectors/manager.py`
  - `src/main.py`
  - `resources/connectors/postgres/init_checkpoint_dc_tables_ddl.sql`

---

## 1. REST API 계약 일관성

### 1.1 LikeRequest / LikeResponse - feedback 필드 [PASS]

| 계층 | feedback 지원 | 비고 |
|------|:---:|------|
| `session_models.py` LikeRequest | O | `feedback: str \| None` (line 77-79) |
| `session_models.py` LikeResponse | O | `feedback: str \| None` (line 87) |
| `sessions.py` router | O | `body.feedback` 전달 (line 107) |
| `session_service.py` toggle_like | O | `feedback` 파라미터 (line 95, 98) |
| `turn_text_store.py` toggle_like | O | `feedback` 파라미터 (line 219), RETURNING에 포함 (line 235) |
| DDL `feedback TEXT` 컬럼 | O | line 80 |

결론: feedback 필드가 모든 계층에서 일관되게 전달된다. 문제 없음.

### 1.2 unarchive / title 엔드포인트 (GAP-01, GAP-02) [PASS]

| 엔드포인트 | Router | Service | Store |
|-----------|:------:|:-------:|:-----:|
| `PATCH /sessions/{id}/unarchive` | O (line 68) | O (line 135) | O (line 372) |
| `PATCH /sessions/{id}/title` | O (line 81) | O (line 146) | O (line 393) |

결론: 두 엔드포인트 모두 Router -> Service -> Store 전 계층 구현 완료.

### 1.3 save_turn() 반환 타입 [PASS]

`turn_text_store.save_turn()` 반환: `str | None` (line 58). `RETURNING turn_id::text` (line 117-118).
runner.py에서 반환값을 `_user_turn_id`, `_assistant_turn_id`로 수신하여 `PipelineResult.turn_id`/`user_turn_id`에 할당.

---

## 2. WebSocket 프로토콜

### 2.1 stream.end 메시지 [PASS]

`src/main.py` line 411-417:
```python
{"type": "stream", "action": "end",
 "insight": ..., "turn_id": ..., "user_turn_id": ...}
```

`PipelineResult`에 `turn_id`(line 52-54), `user_turn_id`(line 55-58) 필드 존재.
runner.py에서 정상/명확화 경로 모두에서 할당됨 (line 241, 317-318).

### 2.2 download_ready 메시지 [PASS]

`src/main.py` line 431-437:
```python
{"type": "download_ready", "session_id": ..., "row_count": ...,
 "formats": [...], "turn_id": ...}
```

`turn_id`가 포함되어 UI에서 다운로드 기록 API 호출 시 사용 가능.

---

## 3. Runner 통합

### 3.1 PipelineResult 필드 [PASS]

`src/agents/models/response.py`:
- `turn_id: str | None` (line 52-54)
- `user_turn_id: str | None` (line 55-58)

### 3.2 run_pipeline() 시그니처 [PASS]

`src/agents/graph/runner.py` line 62-70:
- `client_ip: str | None = None` (keyword-only)
- `user_agent: str | None = None` (keyword-only)

main.py에서 WebSocket(line 349-350)과 REST(line 559-560) 모두 전달.

### 3.3 user_turn_saved 플래그 로직 [PASS]

- 정상 경로: line 260에서 `user_turn_saved = True` 설정
- 명확화 경로: line 221에서 `user_turn_saved = True` 설정
- 에러 경로: line 329에서 `if not user_turn_saved:` 조건 확인 후 저장

중복 user 턴 저장이 방지됨.

---

## 4. Pool Sharing Chain

### 4.1 정상 경로 (postgres backend) [PASS]

1. `checkpointer.py` line 99: `yield checkpointer, pool`
2. `main.py` line 130-131: `as (checkpointer, pool)` -> `manager.set_checkpointer_pool(pool)`
3. `manager.py` line 99-105: `set_checkpointer_pool()` 저장
4. `manager.py` line 107-114: `checkpointer_pool` property 반환
5. `sessions.py` line 34-35: `get_connector_manager().checkpointer_pool` 접근

전체 체인 문제 없음.

### 4.2 MemorySaver 경로 (memory backend) [BUG]

| 등급 | 위치 | 설명 |
|------|------|------|
| :red_circle: **Critical** | `checkpointer.py:107` -> `manager.py:110` | **MemorySaver 사용 시 pool=None이 주입되어 모든 대화 이력 기능이 RuntimeError로 실패** |

**상세:**
- `checkpointer.py` line 107: `yield MemorySaver(), None` -- pool이 None
- `main.py` line 131: `manager.set_checkpointer_pool(None)` 호출
- `manager.py` line 110: `if self._checkpointer_pool is None: raise RuntimeError(...)` -- None이 저장되었으므로 항상 RuntimeError

**영향:**
- 개발/테스트 환경에서 `checkpointer.backend = "memory"` 설정 시:
  - REST API `/api/sessions/*`, `/api/turns/*` 모든 엔드포인트 500 에러
  - `runner.py`의 `save_turn()` 호출 시 RuntimeError (try/except로 잡히므로 파이프라인 자체는 동작하지만 모든 턴 저장 실패)
  - `runner.py`의 `upsert_session_index()` 호출 시 RuntimeError (line 106, try/except로 잡힘)

**수정 방안:**
```python
# manager.py checkpointer_pool property
@property
def checkpointer_pool(self) -> Any:
    if self._checkpointer_pool is None:
        raise RuntimeError(
            "checkpointer_pool 미주입 — postgres backend에서만 사용 가능",
        )
    return self._checkpointer_pool
```

그리고 `runner.py`의 pool 접근 전에 None 체크를 추가하거나, memory 백엔드일 때 턴 저장을 스킵하는 분기 필요:
```python
# runner.py에서 pool 접근 시
_pool = get_connector_manager()._checkpointer_pool
if _pool is None:
    logger.debug("memory backend — 턴 저장 스킵")
else:
    await save_turn(_pool, ...)
```

---

## 5. DDL <-> 코드 일관성

### 5.1 feedback 컬럼 [PASS]

DDL line 80: `feedback TEXT` 존재.
`turn_text_store.py`:
- `get_session_turns_for_ui()` SELECT에 `feedback` 포함 (line 179)
- `toggle_like()` UPDATE에 `feedback` 포함 (line 232, RETURNING line 235)

### 5.2 컬럼명 매핑 전체 확인 [PASS]

DDL 컬럼과 `save_turn()` INSERT 컬럼 대조:

| DDL 컬럼 | save_turn INSERT | 일치 |
|----------|:---:|:---:|
| thread_id | O | O |
| turn_seq | O (서브쿼리) | O |
| role | O | O |
| content | O | O |
| client_ip | O | O |
| user_agent | O | O |
| turn_type | O | O |
| intent | O | O |
| token_count | O | O |
| latency_ms | O | O |
| request_id | O | O |
| status | O | O |
| error_type | O | O |
| error_message | O | O |
| exit_node | O | O |
| model_id | O | O |
| trace_id | O | O |
| metadata | O | O |

`turn_id`, `base_ymd`, `created_at`은 DEFAULT 값이 있으므로 INSERT에서 생략 가능. 문제 없음.

---

## 6. Import Chain

### 6.1 __init__.py 존재 여부 [PASS]

- `src/routers/__init__.py`: 존재
- `src/models/api/__init__.py`: 존재

### 6.2 순환 참조 분석 [PASS]

- `turn_text_store.py`: 외부 import 없음 (psycopg만 사용), session_service를 import하지 않음
- `session_service.py`: `turn_text_store`를 import, `session_models`를 import. 역방향 의존성 없음
- `sessions.py` (router): `session_service`, `session_models`를 import. 역방향 의존성 없음

순환 참조 위험 없음.

---

## 7. 추가 발견 사항

### 7.1 TurnSummary에 feedback 필드 누락 [WARNING]

| 등급 | 위치 | 설명 |
|------|------|------|
| :yellow_circle: **Warning** | `session_models.py:41-53`, `turn_text_store.py:179` | **SQL이 feedback를 SELECT하지만 TurnSummary 모델에 필드가 없어 데이터가 버려짐** |

**상세:**
- `get_session_turns_for_ui()` line 179: SELECT에 `feedback` 포함
- `session_service.py` line 63-74: `TurnSummary` 생성 시 `feedback`를 매핑하지 않음
- `TurnSummary` (line 41-53): `feedback` 필드 없음

**영향:**
- 기능 오류는 아님 (Pydantic은 extra 키를 무시)
- 하지만 UI에서 세션 상세 조회 시 각 턴의 피드백 사유를 볼 수 없음
- 만약 UI가 턴별 피드백 표시 기능을 구현하면 이 필드가 필요함

**수정 방안:**
의도적 누락이라면 SQL SELECT에서 `feedback` 제거 (불필요한 데이터 전송 방지).
UI에 표시가 필요하다면 `TurnSummary`에 `feedback: str | None = None` 추가 + `session_service.py` 매핑 추가.

### 7.2 에러 턴 저장 시 client_ip/user_agent 누락 [INFO]

| 등급 | 위치 | 설명 |
|------|------|------|
| :green_circle: **Info** | `runner.py:336-343` | 에러 경로의 assistant 턴 저장 시 `client_ip`, `user_agent` 미전달 |

정상 경로(line 264-315)에서는 전달되지만, 에러 경로의 assistant 턴(line 336-343)에서는 누락.
감사 추적 일관성을 위해 추가 권장:
```python
await save_turn(
    _pool,
    thread_id=session_id, role="assistant",
    content="처리 중 오류가 발생했습니다.",
    client_ip=client_ip, user_agent=user_agent,  # 추가
    turn_type="error", status="failure",
    ...
)
```

---

## 요약

| 등급 | 건수 | 항목 |
|------|:---:|------|
| :red_circle: Critical | 1 | MemorySaver 경로에서 pool=None -> 전체 대화 이력 기능 RuntimeError |
| :yellow_circle: Warning | 1 | TurnSummary에 feedback 필드 누락 (SQL은 조회하지만 모델에서 버려짐) |
| :green_circle: Info | 1 | 에러 턴 저장 시 client_ip/user_agent 미전달 |

**전체 판정:** REST API 계약, WebSocket 프로토콜, DDL-코드 매핑, Import Chain은 모두 일관성 있음. MemorySaver 경로의 pool=None 문제가 개발/테스트 환경에서 대화 이력 기능 전체 장애를 유발하는 유일한 Critical 이슈.
