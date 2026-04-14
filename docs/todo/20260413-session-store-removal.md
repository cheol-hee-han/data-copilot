# 세션 스토어 제거 — DB 단일 소스 전환 계획

> 작성일: 2026-04-13
> 상태: 계획 수립

## 배경

대화 이력이 두 곳에 중복 저장되고 있다:

| 저장소 | 쓰기 위치 | 읽기 위치 | 비고 |
|--------|-----------|-----------|------|
| 세션 스토어 (Memory/Redis) | `main.py` append_history | `main.py` get_history | 서버 재기동 시 소실, 20턴 제한 |
| DB (`checkpoint_dc_turn_texts`) | `runner.py` save_turn | DB fallback (세션 비었을 때만) | 영구 저장, 제한 없음 |

문제:
- 서버 재기동 후 세션 스토어가 비어 DB fallback을 1회 타지만, 복원된 이력이 세션에 저장되지 않아 2턴째부터 이전 대화 유실
- 세션 스토어 `session_max_history=20`이 DB와 다른 제한 적용
- 두 저장소 간 데이터 불일치 가능성
- 세션 스토어가 실질적으로 불필요 (DB가 단일 진실원)


## 변경 범위

### 파일별 변경 사항

#### 1. `src/services/turn_text_store.py` — DB 조회 보강

**현재**: `get_conversation_history()`가 `role`, `content`만 반환
**변경**: `turn_type` 필드 추가 반환

```
변경 전: SELECT role, content FROM checkpoint_dc_turn_texts ...
변경 후: SELECT role, content, turn_type FROM checkpoint_dc_turn_texts ...

반환값: [{"role": "user", "content": "...", "type": "normal"}, ...]
```

**이유**: `intent_classifier._format_history()`가 `type="clarification"` 필터링에 사용.
DB의 `turn_type` 컬럼에 이미 "normal"/"clarification" 값이 저장되고 있으므로
이를 `type` 키로 매핑하여 반환하면 세션 스토어와 동일한 인터페이스 유지.

**주의사항**:
- `turn_type` 값("normal", "clarification")과 세션 스토어의 `HistoryEntryType`
  값("query", "response", "clarification")은 다름
- intent_classifier의 필터 조건은 `type != "clarification"`이므로,
  "normal" → "query"/"response" 변환 없이 "normal" 그대로 반환해도 필터링은 정상 동작
  (조건이 `!= "clarification"`이므로 "normal"은 통과)
- 단, 향후 "query"/"response" 구분이 필요해지면 role 기반 매핑 추가 필요


#### 2. `src/main.py` — 세션 스토어 호출 제거

##### 2-1. WebSocket 엔드포인트 (`websocket_endpoint`)

| 라인 | 현재 | 변경 |
|------|------|------|
| 606 | `store = get_session_store()` | 제거 |
| 607 | `await store.ensure_session(session_id)` | 제거 (runner.py에서 upsert_session_index 이미 호출) |

##### 2-2. `_run_ws_pipeline` 함수

| 라인 | 현재 | 변경 |
|------|------|------|
| 414 | `store = get_session_store()` | 제거 |
| 447 | `conversation_history = await store.get_history(session_id)` | `get_conversation_history(pool, session_id)` 직접 호출 |
| 448-456 | DB fallback 분기 | 제거 (DB가 유일한 소스) |
| 466-472 | `await store.append_history(... user ...)` | 제거 (runner.py save_turn이 담당) |
| 478-489 | `await store.append_history(... assistant ...)` | 제거 (runner.py save_turn이 담당) |

**주의사항**:
- `main.py`의 append_history는 `mask_pii()`를 적용하여 저장
- `runner.py`의 save_turn은 원본 content를 저장
- 현재 이 불일치가 존재하나, DB가 원본이고 LLM에 전달되는 이력도
  DB에서 읽으므로 동작에 영향 없음
- PII 마스킹은 DB 저장 시점이 아닌 외부 노출 시점에서 적용하는 것이 원칙

##### 2-3. `_handle_slash_command` 함수

| 라인 | 현재 | 변경 |
|------|------|------|
| 375 | `store = get_session_store()` | 제거 |
| 377-383 | `/reset` 명령 → `store.clear_session()` | 제거 (이미 `/new`로 대체됨, `/reset` 미사용) |
| 385-397 | `/history` 명령 → `store.get_history()` | DB에서 조회로 변경 |

##### 2-4. REST 엔드포인트 (`query_endpoint`)

| 라인 | 현재 | 변경 |
|------|------|------|
| 708-709 | `store = get_session_store()` + ensure_session | 제거 |
| 711-714 | `/reset` 명령 | 제거 |
| 718-727 | get_history + DB fallback | DB 직접 호출 |
| 740-747 | append_history (user) | 제거 |
| 752-763 | append_history (assistant) | 제거 |


#### 3. `src/main.py` — lifespan 정리

| 라인 | 현재 | 변경 |
|------|------|------|
| 109 | `store = get_session_store()` | 제거 |
| 142-154 | `store.connect()` + Redis fallback → MemorySessionStore 전환 | 제거 |
| 172 | `_redis = getattr(store, "_client", None)` | Redis 클라이언트 직접 생성으로 변경 |
| 173-176 | `session_backend == "redis" and _redis` → RedisCancelStore | Redis 직접 연결 분기로 변경 |
| 185-193 | 동일 패턴 → RedisActiveRunStore | 동일하게 변경 |
| 200 | `await store.disconnect()` | 제거 (Redis 직접 연결 시 별도 close 추가) |

**Redis 클라이언트 공급 경로 변경**:

현재 CancelStore/ActiveRunStore는 세션 스토어 객체에서 Redis 클라이언트를 추출하여 공유:

```python
_redis = getattr(store, "_client", None)  # 세션 스토어에 의존
if settings.session_backend == "redis" and _redis:
    set_cancel_store(RedisCancelStore(_redis))
```

세션 스토어 제거 후에는 Redis 클라이언트를 직접 생성:

```python
# 세션 스토어 없이 Redis 직접 연결
_redis = None
if settings.cancel_store_backend == "redis":
    import redis.asyncio as aioredis
    _redis = aioredis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_db,
        password=settings.redis_password or None,
    )
    set_cancel_store(RedisCancelStore(_redis))
    set_active_run_store(RedisActiveRunStore(_redis, ...))
else:
    set_cancel_store(MemoryCancelStore())
    set_active_run_store(MemoryActiveRunStore())
```

**주의사항**:

- CancelStore, ActiveRunStore는 세션 스토어와 독립된 클래스 — 기능 변경 없음
- Redis 직접 연결 시 `finally` 블록에 `_redis.close()` 추가 필요
- `session_backend` 설정은 제거하므로, Redis 사용 여부를 판단할 새 설정이 필요
  (예: `cancel_store_backend` 또는 기존 `redis_host` 존재 여부로 판단)
- 현재 `session_backend = "memory"` (기본값)이므로 CancelStore/ActiveRunStore도
  항상 Memory 폴백을 타고 있음 → 세션 스토어 제거 시 즉각적 영향 없음


#### 4. `src/services/intent_classifier.py` — 필터링 호환성 확인

| 라인 | 현재 | 변경 |
|------|------|------|
| 89 | `t.get("type", "query") != "clarification"` | 변경 없음 |

DB에서 `turn_type`을 `type` 키로 반환하면 기존 필터 조건 그대로 동작.
`turn_type="normal"`인 항목은 `!= "clarification"` 조건 통과.
`turn_type="clarification"`인 항목은 필터링됨.
**코드 변경 불필요.**


#### 5. `src/config.py` — 불필요 설정 정리

| 설정 | 현재 값 | 변경 |
|------|---------|------|
| `session_backend` | `"memory"` | 제거 → CancelStore/ActiveRunStore용 `redis_backend` 신설 |
| `session_ttl` | `1800` | 제거 |
| `session_clarify_ttl` | `300` | 제거 (이미 deprecated) |
| `session_max_history` | `20` | 제거 (DB는 제한 없음) |
| `max_sessions` | 세션 스토어 전용 | 제거 |
| `prompt_history_window` | `0` | 유지 (intent_classifier에서 사용) |
| `redis_host/port/db/password` | Redis 접속 정보 | 유지 (CancelStore/ActiveRunStore가 사용) |
| `active_run_ttl_seconds` | `1800` | 유지 |

**주의사항**: `session_backend`을 제거하면서 CancelStore/ActiveRunStore의
Redis 사용 여부를 판단할 새 설정이 필요 (예: `redis_backend: str = "memory"`)


#### 6. 세션 스토어 파일 — 제거 또는 보존

| 파일 | 판단 |
|------|------|
| `src/services/session/store.py` | 제거 (CancelStore/ActiveRunStore는 상속하지 않음, 독립 클래스) |
| `src/services/session/memory_store.py` | 제거 |
| `src/services/session/redis_store.py` | 제거 |
| `src/services/session/__init__.py` | 디렉토리 전체 제거 |
| `src/services/session/store.py` → `HistoryEntryType` | 세션 스토어 전용 Enum — 디렉토리와 함께 제거 |


### 변경하지 않는 것

| 항목 | 이유 |
|------|------|
| `src/agents/graph/runner.py` | save_turn 로직 그대로 유지 |
| `src/services/cancel_store.py` | 세션 스토어와 무관 |
| `src/services/active_run_store.py` | 세션 스토어와 무관 |
| LangGraph checkpointer | 명확화 상태 관리, 세션 스토어와 무관 |
| `src/services/turn_text_store.py`의 save_turn | 변경 없음 |


## 실행 순서

### Step 1: DB 조회 보강 (안전, 기존 동작 영향 없음)

`turn_text_store.get_conversation_history()`에 `turn_type` 필드 추가.
기존 호출부(main.py fallback)에서도 추가 필드가 있어도 무해.

**참고**: `session_max_history=20` 제한이 제거되지만, LLM에 전달되는 이력은
`intent_classifier._format_history()`의 `prompt_history_window` 설정으로 제어되므로
DB 조회 자체에 LIMIT을 걸 필요는 없음. 장기 세션의 전체 이력은 `/history` 명령에서만 사용.

### Step 2: main.py 읽기 경로 전환

`store.get_history()` + DB fallback 이중 구조를
`get_conversation_history()` 단일 호출로 교체.
이 시점에서 세션 스토어 읽기 의존 제거.

### Step 3: main.py 쓰기 경로 제거

`store.append_history()` 호출 제거.
runner.py의 save_turn이 유일한 쓰기 경로.

### Step 4: 슬래시 명령 정리

`/reset` 관련 코드 제거 (미사용).
`/history` 명령은 DB 조회로 전환.

### Step 5: lifespan 및 초기화 정리

세션 스토어 초기화/종료 코드 제거.
ensure_session 호출 제거.
CancelStore/ActiveRunStore의 Redis 클라이언트 공급 경로를
세션 스토어 경유(`getattr(store, "_client")`) → Redis 직접 생성으로 변경.

### Step 6: config 정리

- 제거: `session_backend`, `session_ttl`, `session_clarify_ttl`, `session_max_history`, `max_sessions`
- 신설: `redis_backend` (CancelStore/ActiveRunStore용, 기본값 `"memory"`)
- 유지: `redis_host/port/db/password`, `active_run_ttl_seconds`, `prompt_history_window`

### Step 7: 세션 스토어 파일 제거

`src/services/session/` 디렉토리 전체 제거 (`HistoryEntryType` 포함).
`src/main.py`의 `HistoryEntryType` import 제거.

### Step 8: 문서 및 환경 파일 정리

- `.env.example` — `SESSION_BACKEND`, `SESSION_TTL` 등 세션 관련 환경 변수 제거
- `docs/guides/env-configuration-guide.md` — 해당 설정 설명 제거/갱신
- `src/main.py` 모듈 docstring — 세션 스토어 관련 설명 갱신
- `cancel_store.py`, `active_run_store.py` docstring — "SessionStore와 동일 백엔드" 문구 갱신


## 위험 요소 및 대응

### 위험 1: DB 연결 실패 시 이력 조회 불가

**현재**: 세션 스토어(메모리)가 있으므로 DB 없이도 현재 세션 내 이력 유지
**변경 후**: DB 연결 실패 = 이력 조회 불가

**대응**: DB 연결은 파이프라인 전체의 전제조건이므로 (SQL 실행, 체크포인터 등)
DB 없이 정상 동작하는 시나리오 자체가 없음. 허용 가능한 위험.

### 위험 2: save_turn 실패 시 이력 누락

**현재**: main.py의 append_history가 별도 저장하므로 save_turn 실패해도 세션에는 이력 있음
**변경 후**: save_turn 실패 = 해당 턴 이력 영구 누락

**대응**: save_turn에 이미 `_pending_turns` 재시도 버퍼가 구현되어 있음.
일시적 DB 장애 시 다음 save_turn 호출 시 함께 저장됨.

### 위험 3: PII 마스킹 불일치

**현재**: 세션 스토어에는 mask_pii() 적용된 값, DB에는 원본
**변경 후**: LLM에 전달되는 이력이 원본 (마스킹 안 됨)

**대응**: 현재도 DB fallback 경로에서는 원본이 전달되고 있었으므로
기존과 동일한 수준. PII 마스킹은 외부 출력 시점에서 적용하는 것이 원칙.
향후 필요 시 get_conversation_history에서 마스킹 적용 가능.
