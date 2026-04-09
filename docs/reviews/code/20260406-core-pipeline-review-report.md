# 코어 파이프라인 모듈 코드 리뷰 보고서

- **일시**: 2026-04-06
- **대상**: pipeline.py, runner.py, cancel.py, checkpointer.py, state.py, nodes/__init__.py, main.py, sessions.py
- **리뷰어**: Code Reviewer Agent

---

## 요약

전반적으로 3계층 아키텍처(interpret -> reason -> present)가 잘 설계되어 있으며, 보안 다층 방어(sanitize, PII 마스킹, 프롬프트 인젝션 감지)와 에이전틱 루프 제어(LoopGuard)가 견고하다. 다만 동시성 안전, 리소스 관리, 타입 안전성, 에러 처리에서 운영 환경 배포 전 반드시 해결해야 할 이슈들이 식별되었다.

| 등급 | 건수 |
|------|------|
| Critical | 6 |
| Warning | 10 |
| Info | 8 |

---

## ⚠ 재검증 결과 (2026-04-06 2차 검토)

아래 이슈들은 실제 코드와 대조 검증한 결과, 오탐 또는 심각도 조정이 필요한 것으로 확인되었습니다.

| 원래 ID | 판정 | 사유 |
|---------|------|------|
| **C-01** | **Critical → Warning** → ⏸️ **TODO (A-3)** | `_MAX_CACHE=100` 상한 존재. 멀티워커 캐시 비공유 이슈. 파일 기반 캐시로 전환 예정이나, 현재 copy 기능으로 다운로드 대체 가능하여 우선순위 낮음 |
| **C-03** | **오탐 (제거)** | `manager.connect_all()`은 `_connected` 플래그(manager.py L141-142)로 멱등성 보장. 이미 연결 시 bool 체크 1회만 수행. CLI 모드 지원용 방어적 패턴 |
| **C-05** | ❌ **제외** | runner.py에서 순차 호출 구조라 동시성 문제 없음. session_id별 독립 채번이므로 전역 SEQUENCE 부적합. MAX+1이 현재 구조에 적합 |
| **C-06** | **오탐 (제거)** | lifespan 메커니즘이 초기화 순서 보장. CLI 모드에서 checkpointer 없이 실행은 의도된 설계 |

---

## Critical (반드시 수정 필요)

### C-01. `_sql_result_cache` 전역 dict의 동시성 비안전 및 메모리 누수

**파일**: `src/main.py` L687-713

```python
_sql_result_cache: dict[str, dict[str, Any]] = {}
_MAX_CACHE = 100
```

**문제**:
1. **멀티 워커 환경에서 공유 불가**: uvicorn `--workers 4` 실행 시 각 워커가 독립된 dict를 보유하여 다운로드 요청이 다른 워커로 라우팅되면 404 반환.
2. **FIFO 퇴거 불완전**: `next(iter(...))` 방식은 CPython dict 삽입 순서에 의존하나, 동일 session_id의 재요청(갱신) 시 순서가 유지되어 가장 오래된 것이 아닐 수 있음.
3. **대용량 데이터 메모리 점유**: `sql_result.rows`가 10,000건 x N컬럼이면 수십 MB. 100개 세션이면 수 GB 점유 가능.

**제안**:
- Redis 기반 캐시로 교체 (세션 스토어와 동일 백엔드). TTL 설정으로 자동 만료.
- 즉시 대안: rows를 CSV/JSON으로 직렬화하여 임시 파일 저장, 경로만 캐시.
- 최소한 `_MAX_CACHE`를 `settings`로 이동하고, rows에 크기 제한(예: 1000건) 적용.


### C-02. `RedisCancelStore.pop_cancel`의 경쟁 상태 (GET + DELETE 비원자적)

**파일**: `src/services/cancel_store.py` L80-87

```python
async def pop_cancel(self, session_id: str) -> str | None:
    key = self._key(session_id)
    val = await self._client.get(key)
    if val is None:
        return None
    await self._client.delete(key)
    return val.decode() if isinstance(val, bytes) else val
```

**문제**: GET과 DELETE 사이에 다른 요청이 동일 키를 읽을 수 있어, 동일 취소 플래그가 두 번 소비될 수 있다. 특히 빠른 연속 요청 시 cancel이 중복 적용되거나, 새 턴의 cancel 플래그가 이전 턴의 pop에 의해 소실될 수 있다.

**제안**: Redis `GETDEL` 명령(Redis 6.2+) 사용으로 원자적 pop 구현.

```python
async def pop_cancel(self, session_id: str) -> str | None:
    key = self._key(session_id)
    val = await self._client.getdel(key)
    if val is None:
        return None
    return val.decode() if isinstance(val, bytes) else val
```

GETDEL 미지원 환경이면 Lua 스크립트로 원자적 처리:
```python
_POP_SCRIPT = "local v = redis.call('GET', KEYS[1]); if v then redis.call('DEL', KEYS[1]) end; return v"
```


### C-03. `run_pipeline`에서 `manager.connect_all()` 매 요청 호출

**파일**: `src/agents/graph/runner.py` L140-141

```python
manager = get_connector_manager()
await manager.connect_all()
```

**문제**: `connect_all()`은 lifespan에서 이미 호출되며, 매 요청마다 재호출하면 커넥터가 이미 연결된 상태인지 확인하는 오버헤드가 발생한다. 더 심각하게, 커넥터 구현체에 따라 재연결을 시도하여 커넥션 풀 누수나 예상치 못한 에러를 유발할 수 있다.

**제안**: `connect_all()` 호출을 제거하거나, `ensure_connected()` 같은 idempotent 메서드로 교체. CLI 모드(`main()`)에서만 필요하다면 분기 처리.


### C-04. `_route_after_sql_validator`에서 state 직접 mutation

**파일**: `src/agents/graph/pipeline.py` L233-236

```python
case FailureType.SQL_SEMANTIC_LOCAL:
    lg = state.reason.loop_guard
    if lg.should_escalate_to_structural():
        state.reason.recovery_entry_source = "sql_validator"  # <-- 직접 mutation
        return "replan"
```

**문제**: LangGraph의 라우팅 함수는 순수 함수(pure function)여야 하며, state를 직접 변경하면 안 된다. LangGraph 내부의 상태 관리 메커니즘을 우회하여 체크포인터가 이 변경을 기록하지 못하고, interrupt/resume 시 상태 불일치가 발생할 수 있다. L252도 동일 문제.

**제안**: state mutation은 반드시 노드 함수에서 `return {"reason": updated_reason}` 형태로 반환해야 한다. `sql_validator_node`에서 `recovery_entry_source`를 설정하고, 라우팅 함수는 읽기만 수행하도록 분리.


### C-05. `turn_text_store.save_turn`의 turn_seq 채번 경쟁 상태

**파일**: `src/services/turn_text_store.py` L95-120

```sql
COALESCE(
    (SELECT MAX(turn_seq) + 1
     FROM checkpoint_dc_turn_texts
     WHERE thread_id = %(thread_id)s),
    1
)
```

**문제**: `autocommit=True`(checkpointer.py L61) 환경에서 동일 thread_id에 대해 두 개의 INSERT가 동시 실행되면 동일한 turn_seq가 채번될 수 있다. 명확화 응답과 정상 응답이 거의 동시에 저장되는 시나리오에서 발생 가능.

**제안**:
1. `checkpoint_dc_turn_texts` 테이블에 `(thread_id, turn_seq)` UNIQUE 제약 추가 + INSERT에 retry 로직
2. 또는 PostgreSQL SEQUENCE를 thread_id별로 사용 (복잡도 높음)
3. 또는 `advisory lock` 사용: `SELECT pg_advisory_xact_lock(hashtext(%(thread_id)s))`


### C-06. `get_compiled_app` 싱글턴의 스레드 안전성 미보장

**파일**: `src/agents/graph/pipeline.py` L582-593

```python
_compiled_app: Any = None

def get_compiled_app(checkpointer: Any = None) -> Any:
    global _compiled_app
    if _compiled_app is None:
        _compiled_app = create_app(checkpointer=checkpointer)
    return _compiled_app
```

**문제**: 멀티워커 환경에서는 프로세스별 독립이므로 큰 문제는 없으나, asyncio 환경에서도 `lifespan` 이전에 다른 경로에서 `get_compiled_app()`이 호출되면 checkpointer 없이 컴파일될 수 있다. 이후 호출에서 checkpointer를 전달해도 무시됨.

**제안**: `get_compiled_app(checkpointer=None)` 호출 시 checkpointer가 None이고 `_compiled_app`도 None이면 경고 로그를 남기거나, `checkpointer`가 필수인 `init_compiled_app(checkpointer)` + `get_compiled_app()` 패턴으로 분리.

---

## Warning (개선 권장)

### W-01. `runner.py`의 함수 내 import (lazy import 과다)

**파일**: `src/agents/graph/runner.py` L93, L111, L228-229, L265-266, L348

```python
from src.agents.graph.cancel import pop_cancel  # L93
from src.services.turn_text_store import upsert_session_index  # L111
from src.services.turn_text_store import save_turn  # L228, L265, L348
from src.config import settings  # L266
```

**문제**: 동일 모듈(`turn_text_store`, `cancel`)을 함수 내에서 반복 import한다. 순환 의존 방지가 목적이라면 이해되나, `cancel`과 `turn_text_store`는 순환 의존이 없으므로 모듈 상단으로 이동 가능. 가독성 저하와 IDE 정적 분석 방해 요인.

**제안**: 순환 의존이 실제로 존재하는지 확인 후, 가능한 것은 모듈 상단 import로 이동. `settings`는 이미 모듈 상단에서 간접 참조 중.


### W-02. `run_pipeline` 함수 과도한 책임 (170줄)

**파일**: `src/agents/graph/runner.py` L62-371

**문제**: 한 함수가 sanitize, cancel 확인, interrupt 감지, 파이프라인 실행, 명확화 처리, 턴 저장, 에러 턴 기록을 모두 담당한다. 단일 책임 원칙 위반. 테스트 시 개별 단계를 격리하기 어렵다.

**제안**: 다음과 같이 분리:
- `_check_and_resume_interrupt()` -- interrupt 상태 확인 및 resume 처리
- `_save_clarification_turns()` -- 명확화 턴 저장
- `_save_completion_turns()` -- 정상 완료 턴 저장
- `_save_error_turns()` -- 에러 턴 저장


### W-03. `cancel_pipeline` 엔드포인트의 인증/인가 부재

**파일**: `src/routers/sessions.py` L134-161

```python
@router.post("/sessions/{session_id}/cancel")
async def cancel_pipeline(session_id: str, turn_id: str = Query(default="*")):
```

**문제**: session_id에 대한 소유권 검증이 없다. 임의 사용자가 다른 사용자의 session_id를 알면 파이프라인을 취소할 수 있다. 현재 user_id 기반 인증이 없으므로 모든 세션이 취소 가능.

**제안**:
- 최소한 session_id 형식 검증(`_is_valid_session_id` 재사용) 적용
- 향후 인증 도입 시: session 소유자와 요청자 일치 확인
- 즉시 적용 가능: `session_id` 길이 제한 및 패턴 검증


### W-04. `sessions.py`의 `_pool()` 동기 함수에서 잠재적 타이밍 이슈

**파일**: `src/routers/sessions.py` L38-45

```python
def _pool():
    pool = get_connector_manager().checkpointer_pool
    if pool is None:
        raise HTTPException(503, ...)
    return pool
```

**문제**: `checkpointer_pool`이 lifespan에서 설정되기 전에 호출되면 항상 503을 반환한다. FastAPI의 라우터 등록 시점과 lifespan 실행 시점의 차이로 인해, 서버 기동 중 요청이 들어오면 503이 반환될 수 있다. 근본적 문제는 아니나, `_pool()`이라는 이름이 내부 구현을 노출.

**제안**: `_get_checkpointer_pool()`로 명칭 변경. Kubernetes readiness probe가 있으므로 실제 트래픽은 차단되지만, 함수명 개선은 가독성에 도움.


### W-05. `checkpointer.py`의 `_collect_src_types` 경직성

**파일**: `src/agents/graph/checkpointer.py` L110-132

```python
_ALLOWLIST_MODULES = (
    "src.models.enums",
    "src.models.result",
    ...
)
```

**문제**: 새 모듈에 Pydantic 모델이나 Enum을 추가할 때마다 이 목록을 수동 갱신해야 한다. 누락 시 체크포인터 역직렬화 실패로 interrupt/resume이 깨진다. 실패 시점이 런타임이라 발견이 늦다.

**제안**:
1. 테스트 추가: state의 모든 타입이 allowlist에 포함되는지 검증하는 단위 테스트
2. 또는 `src.agents.state.state`에서 사용하는 모든 import를 자동 추적하여 allowlist 생성


### W-06. `_handle_error` 노드에서 state 필드의 안전하지 않은 접근

**파일**: `src/agents/graph/pipeline.py` L373-405

```python
if state.reason.loop_guard.generate_attempts >= SQL_MAX_RETRY:
```

**문제**: `state.reason`이 초기 기본값(ReasoningState())인 경우에는 문제없으나, 비데이터 의도(CASUAL_TALK)에서 에러가 발생하면 reason 계층을 거치지 않은 상태로 이 노드에 진입할 수 있다. 현재는 기본값이 0이라 조건 불일치로 넘어가므로 버그는 아니지만, 의도가 불명확.

**제안**: 비데이터 의도에서는 reason.loop_guard를 체크하지 않도록 명시적 분기 추가.


### W-07. `main.py`의 `health_check`에서 실제 LLM 연결 미검증

**파일**: `src/main.py` L272-288

```python
if settings.llm_provider == "anthropic":
    llm_ok = bool(settings.anthropic_api_key)
else:
    llm_ok = bool(settings.openai_api_key)
```

**문제**: API 키 존재 여부만 확인하고 실제 LLM API 호출은 하지 않는다. 키가 유효하지 않거나, 네트워크 문제, 과금 한도 초과 등의 상황을 감지하지 못한다.

**제안**: health check에 간단한 LLM ping 호출 추가 (예: 짧은 토큰으로 completion 요청). 단, 타임아웃과 캐싱으로 빈번한 과금 방지. 또는 `/health`와 `/health/deep`을 분리하여 LLM 실제 호출은 deep에서만 수행.


### W-08. CORS `allow_origins=["*"]` 운영 미대응

**파일**: `src/main.py` L203-208

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 운영 시 특정 도메인으로 제한 필요
)
```

**문제**: 주석에 운영 시 제한 필요라 명시했으나, 배포 시 .env 교체만으로 전환 가능한 구조가 아니다.

**제안**: `settings`에 `cors_allowed_origins: list[str] = ["*"]` 필드 추가하여 .env에서 제어 가능하도록 변경.


### W-09. `PipelineState`의 `model_config` 미설정

**파일**: `src/agents/state/state.py`

**문제**: PipelineState가 `model_config`를 설정하지 않아 Pydantic v2 기본값인 `extra="ignore"`가 적용된다. LangGraph가 상태 업데이트 시 예상치 못한 필드를 전달하면 조용히 무시되어 디버깅이 어렵다.

**제안**: 개발 환경에서는 `model_config = {"extra": "forbid"}` 적용하여 예상치 못한 필드를 조기 발견. 운영 환경에서는 `"ignore"` 유지.


### W-10. `_route_after_clarify`에서 `turn_id` None 체크 로직

**파일**: `src/agents/graph/pipeline.py` L357-359

```python
current_signals = [
    s for s in state.resolved_signals
    if s.turn_id is not None and s.turn_id == state.turn_id
]
```

**문제**: `state.turn_id`가 빈 문자열("")인 경우(runner.py에서 초기값), `s.turn_id == state.turn_id`가 빈 문자열끼리 일치하여 이전 턴의 시그널이 포함될 수 있다. 실제로 runner.py L193에서 `turn_id=str(uuid.uuid4())`로 설정하므로 현실적 위험은 낮으나, interrupt resume 경로에서는 turn_id를 새로 설정하지 않음.

**제안**: `state.turn_id`가 빈 문자열이면 빈 리스트 반환하도록 방어 코드 추가.

---

## Info (참고/개선 기회)

### I-01. `state.py`의 `should_terminate` 함수가 모듈 수준 독립 함수

**파일**: `src/agents/state/state.py` L601-619

**관찰**: `should_terminate`는 `ReasoningState`의 메서드가 아닌 모듈 수준 함수이다. `get_confirmed_knowledge()`, `get_pending_hypotheses()` 등은 메서드인데 이것만 별도인 것은 일관성이 떨어진다.

**제안**: `ReasoningState.should_terminate()` 메서드로 이동하면 자연스럽고, pipeline.py에서 `state.reason.should_terminate()`로 호출 가능.


### I-02. `TableMeta.from_meta`에서 `ConnectorManager` import

**파일**: `src/agents/state/state.py` L276-277

```python
from src.connectors.manager import ConnectorManager
return cls(
    ...
    db_source=ConnectorManager.parse_db_source(table_name),
)
```

**관찰**: 상태 모델이 인프라 계층(ConnectorManager)에 의존한다. 순환 의존 방지를 위해 함수 내 import를 사용하고 있으나, 계층 위반이다.

**제안**: `parse_db_source`를 유틸 함수로 분리하여 `src/utils/` 또는 `src/models/`에 배치.


### I-03. `runner.py`의 `_build_result`에서 cancelled 판정 로직 중복

**파일**: `src/agents/graph/runner.py` L395-399

```python
_cancelled = (
    _status == "cancelled"
    or (hasattr(_status, "value") and _status.value == "cancelled")
)
```

**관찰**: `QueryStatus.CANCELLED`와 문자열 "cancelled" 모두 처리한다. 이는 raw_state가 dict일 때 Enum이 아닌 문자열일 수 있기 때문이나, 타입이 불확실한 방어 코드가 여러 곳에 산재.

**제안**: `QueryStatus` Enum에 `is_cancelled` 프로퍼티를 추가하거나, 유틸 함수 `is_cancelled_status(status)` 생성.


### I-04. `nodes/__init__.py`가 docstring만 포함 (re-export 없음)

**파일**: `src/agents/nodes/__init__.py`

**관찰**: docstring에 노드 목록이 나열되어 있으나 실제 re-export는 없다. pipeline.py에서 각 노드를 개별 import한다. 이는 의도적 설계로 보이며, re-export 없이도 동작에 문제없다.

**제안**: 유지보수 편의를 위해 `__all__` 목록 추가를 고려. 단, 현재 구조에서 필수는 아님.


### I-05. `checkpointer.py`의 password 분리 패턴 문서화

**파일**: `src/agents/graph/checkpointer.py` L64

```python
"password": history_db.password,  # DSN에서 분리하여 로그 노출 방지
```

**관찰**: DSN에서 password를 분리하여 `connection_kwargs`로 전달하는 것은 좋은 보안 관행. `DbConnectionInfo.dsn` 프로퍼티에서도 password를 제외하고 있어 일관적.


### I-06. ES 관련 설정이 config.py에 잔존

**파일**: `src/config.py` L80-88

```python
es_host: str = "localhost"
es_port: int = 9200
...
```

**관찰**: agent-memory에 "ES 완전 제거" 프로젝트 메모리가 있다. config.py에 ES 설정이 남아있어 혼란 유발.

**제안**: ES 미사용이 확정되었으면 관련 설정 제거. 단, enabled_connectors에서 이미 비활성이므로 즉시 영향은 없음.


### I-07. `DownloadRequest`와 `QueryRequest`가 `main.py`에 인라인 정의

**파일**: `src/main.py` L72-83, L716-726

**관찰**: `sessions.py`의 모델은 `src/models/api/session_models.py`에 분리되어 있으나, `main.py`의 요청 모델은 인라인으로 정의되어 있다. 일관성이 떨어진다.

**제안**: `QueryRequest`와 `DownloadRequest`를 `src/models/api/`로 이동하여 API 모델 관리를 일원화.


### I-08. `cancel.py`의 `make_cancel_updates` 반환 타입 명시화

**파일**: `src/agents/graph/cancel.py` L77

```python
def make_cancel_updates(reason_state) -> dict[str, Any]:
```

**관찰**: `reason_state` 파라미터에 타입 힌트가 없다. `ReasoningState`를 받는 것이 명확하지만 import를 피하기 위해 생략한 것으로 보인다.

**제안**: `from __future__ import annotations` 이미 적용되어 있으므로 `reason_state: ReasoningState`로 타입 힌트 추가 가능 (런타임 import 불필요).

---

## 아키텍처 관점 종합 의견

### 잘 된 점
1. **3계층 파이프라인 분리**: interpret/reason/present 경계가 명확하고 라우팅 함수가 잘 구조화됨
2. **보안 다층 방어**: sanitize(유니코드 정규화 + SQL 인젝션 + 프롬프트 인젝션) -> PII 마스킹 -> SQL 안전성 검증 -> 읽기 전용 DB 계정. 4중 방어가 체계적
3. **에이전틱 루프 제어**: LoopGuard 4차원 카운터 + should_terminate + dead_ends 기록으로 무한 루프 방어가 견고
4. **체크포인터 설계**: async context manager로 리소스 정리 보장, msgpack allowlist 동적 수집은 영리한 설계
5. **취소 메커니즘**: CancelStore Protocol 패턴으로 Redis/Memory 교체 용이, TTL 안전망 적용
6. **Kubernetes 배포 준비**: liveness/readiness probe 분리, 필수 커넥터 검증, 보안 헤더 미들웨어

### 개선 우선순위 (영향도 x 난이도 기준)

| 순위 | 이슈 | 영향 | 난이도 |
|------|------|------|--------|
| 1 | C-04 라우팅 함수 state mutation | 데이터 무결성 | 낮음 |
| 2 | C-02 pop_cancel 경쟁 상태 | 동시성 버그 | 낮음 |
| 3 | C-01 SQL 결과 캐시 | 메모리/멀티워커 | 중간 |
| 4 | C-03 connect_all 매 요청 호출 | 성능/안정성 | 낮음 |
| 5 | C-05 turn_seq 채번 | 데이터 무결성 | 중간 |
| 6 | C-06 싱글턴 초기화 순서 | 안정성 | 낮음 |
| 7 | W-02 run_pipeline 분리 | 유지보수성 | 중간 |
| 8 | W-08 CORS 설정 외부화 | 보안 | 낮음 |

---

*이 리뷰는 운영 환경 배포 전 점검을 기준으로 작성되었습니다.*
