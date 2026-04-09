# 리뷰 이슈 검증 보고서

- 작성일: 2026-04-06
- 대상 파일: pipeline.py, runner.py, cancel.py, cancel_store.py, main.py
- 목적: 기존 리뷰 이슈 5건의 실제 코드 대비 검증 (오탐 식별)

---

## INT-01: pipeline.py 라우팅 함수에서 state 직접 mutation

**판정: 부분 진양성 (Partial True Positive)**

### 검증 결과

라우팅 함수 전체를 검토한 결과, **대부분의 라우팅 함수는 순수 함수**이다.
그러나 `_route_after_sql_validator`에서 **1건의 state 직접 mutation**이 확인되었다.

- **pipeline.py L233-235**:
  ```python
  case FailureType.SQL_SEMANTIC_LOCAL:
      lg = state.reason.loop_guard
      if lg.should_escalate_to_structural():
          state.reason.recovery_entry_source = "sql_validator"  # <-- mutation
          return "replan"
  ```

- **pipeline.py L252-254**:
  ```python
  if not state.reason.recovery_entry_source:
      state.reason.recovery_entry_source = "sql_validator"  # <-- mutation
  ```

### 분석

- LangGraph에서 라우팅 함수(conditional edge function)는 state를 읽어 문자열 키를 반환하는 **순수 함수**여야 한다. state mutation은 노드 함수에서 dict를 반환하는 방식으로만 수행해야 한다.
- 다만, LangGraph 내부적으로 라우팅 함수에서의 mutation이 **즉시 오류를 발생시키지는 않는다**. Pydantic 모델의 in-place mutation이므로 현재 동작에는 문제가 없을 수 있다.
- 그러나 이는 LangGraph의 설계 원칙 위반이며, 향후 checkpointer 사용 시 **스냅샷 일관성 문제**가 발생할 수 있다. `recovery_entry_source` 설정은 sql_validator_node 내부에서 수행하는 것이 맞다.

### 결론

**실제 문제이나 심각도는 중간 수준(Warning)**. `_route_after_sql_validator`의 L233, L252-253에서만 발생. 나머지 라우팅 함수(`_route_after_intent_classifier`, `_route_after_normalize`, `_route_after_readiness_gate`, `_route_after_sql_generator`, `_route_after_recovery_agent`, `_route_after_result_finalizer`, `_route_after_execution`, `_route_after_clarify`)는 모두 순수 함수로 확인.

---

## INT-02: RedisCancelStore.pop_cancel의 GET+DELETE 비원자적 경쟁 상태

**판정: 진양성 (True Positive)**

### 검증 결과

- **cancel_store.py L80-87**:
  ```python
  async def pop_cancel(self, session_id: str) -> str | None:
      """플래그를 반환하고 삭제한다 (GET + DELETE)."""
      key = self._key(session_id)
      val = await self._client.get(key)
      if val is None:
          return None
      await self._client.delete(key)
      return val.decode() if isinstance(val, bytes) else val
  ```

### 분석

- GET과 DELETE가 별도의 Redis 명령으로 실행되므로, 두 명령 사이에 다른 워커/요청이 동일 키를 읽을 수 있는 **TOCTOU(Time-Of-Check-Time-Of-Use) 경쟁 상태**가 존재한다.
- Redis에는 `GETDEL` 명령(Redis 6.2+)이 있어 원자적으로 GET+DELETE를 수행할 수 있다. 또는 Lua 스크립트나 Redis 트랜잭션(MULTI/EXEC + WATCH)으로 해결 가능하다.
- **실제 영향 범위**: 동일 세션에 대해 동시에 여러 요청이 `pop_cancel`을 호출하는 시나리오에서만 발생. 현재 사용 패턴(`runner.py`의 `run_pipeline` 시작부에서 1회 호출)에서는 **같은 세션의 요청이 동시에 실행되는 경우가 제한적**이므로 실무적 위험은 낮다.
- 그러나 multi-worker 운영 환경에서의 방어적 코딩 관점에서 `GETDEL` 사용이 바람직하다.

### 결론

**실제 문제이나 실무적 영향도는 낮음(Info~Warning)**. `GETDEL` 한 줄로 해결 가능하므로 수정 비용 대비 효과가 좋다.

---

## INT-04: get_compiled_app 싱글턴이 checkpointer 없이 초기화될 수 있는 순서 의존성

**판정: 오탐 (False Positive)**

### 검증 결과

- **pipeline.py L582-593**:
  ```python
  def get_compiled_app(checkpointer: Any = None) -> Any:
      global _compiled_app
      if _compiled_app is None:
          _compiled_app = create_app(checkpointer=checkpointer)
          logger.info("LangGraph 파이프라인 컴파일 완료 (싱글턴)")
      return _compiled_app
  ```

- **main.py L129-134** (lifespan에서의 호출):
  ```python
  async with create_checkpointer(settings.history_db) as (checkpointer, pool):
      if pool is not None:
          manager.set_checkpointer_pool(pool)
      get_compiled_app(checkpointer=checkpointer)  # <-- checkpointer 주입
  ```

- **runner.py L142** (요청 시 호출):
  ```python
  app = get_compiled_app()  # <-- checkpointer 인자 없음 (캐시된 앱 반환)
  ```

### 분석

- `lifespan`은 FastAPI 서버 기동 시 **모든 요청보다 먼저 실행**된다. `get_compiled_app(checkpointer=checkpointer)`가 lifespan 내에서 호출되므로, 싱글턴은 반드시 checkpointer가 주입된 상태로 초기화된다.
- `runner.py`의 `get_compiled_app()`은 항상 lifespan 이후에 호출되므로 캐시된 (checkpointer가 주입된) 앱을 반환한다.
- CLI 모드(`python -m src.agents.graph.runner`)에서는 lifespan을 거치지 않으므로 checkpointer 없이 초기화될 수 있으나, 이는 CLI의 의도된 동작이다 (개발/디버깅용, checkpointer 불필요).
- 싱글턴 패턴 자체가 순서 의존적인 것은 사실이나, FastAPI의 lifespan 보장에 의해 운영 환경에서는 문제가 발생하지 않는다.

### 결론

**오탐**. FastAPI lifespan 메커니즘이 초기화 순서를 보장한다. CLI 모드에서 checkpointer 없이 실행되는 것은 의도된 설계이다.

---

## LOG-01: _sql_result_cache 전역 dict -- 멀티워커 불가, 대용량 메모리 누수

**판정: 진양성 (True Positive), 단 심각도는 조건부**

### 검증 결과

- **main.py L687-713**:
  ```python
  _sql_result_cache: dict[str, dict[str, Any]] = {}
  _MAX_CACHE = 100

  def _cache_sql_result(session_id: str, sql_result: Any) -> None:
      if sql_result is None:
          return
      if len(_sql_result_cache) >= _MAX_CACHE:
          oldest = next(iter(_sql_result_cache))
          del _sql_result_cache[oldest]
      _sql_result_cache[session_id] = {
          "columns": ...,
          "rows": ...,
      }
  ```

### 분석

1. **멀티워커 격리**: 맞다. `uvicorn --workers 4` 등 multi-worker 실행 시 각 워커가 별도 프로세스이므로 `_sql_result_cache`는 워커 간 공유되지 않는다. 사용자가 WS로 조회한 워커와 다운로드 요청을 받는 워커가 다를 수 있어 404가 발생할 수 있다.

2. **메모리 누수**: `_MAX_CACHE = 100`으로 **상한이 설정되어 있어** 무한 증가는 아니다. 다만 각 캐시 항목에 SQL 결과의 전체 rows가 저장되므로, 대용량 결과(수천~수만 행)가 100개 캐시되면 **상당한 메모리를 점유**할 수 있다.

3. **LRU가 아닌 FIFO**: `next(iter(...))` 방식은 삽입 순서 기반 제거이므로 LRU가 아니다. 빈번하게 다운로드하는 세션의 결과도 순서상 밀려나면 제거된다.

### 결론

- "메모리 누수"라는 표현은 과장 -- **상한이 있으므로 누수(leak)는 아니다**. 다만 100개 항목 x 대용량 결과로 인한 **메모리 과점유 가능성**은 실재한다.
- **멀티워커 불가**는 정확한 지적이다. 운영 환경에서는 Redis 등 공유 캐시로 전환하거나, sticky session을 적용해야 한다.
- 현재 개발/단일워커 단계에서는 기능상 문제 없음.

---

## LOG-02: connect_all() 매 요청 호출 -- lifespan에서 완료된 초기화 반복

**판정: 오탐 (False Positive)**

### 검증 결과

- **runner.py L140**:
  ```python
  await manager.connect_all()
  ```

- **connectors/manager.py L135-142** (connect_all 구현):
  ```python
  async def connect_all(self) -> None:
      """활성 커넥터를 초기화한다 (멱등)."""
      if self._connected:
          return
      # ... 실제 연결 로직 ...
  ```

### 분석

- `connect_all()`은 **멱등(idempotent) 함수**이다. `self._connected` 플래그를 확인하여 이미 연결된 경우 즉시 반환한다.
- lifespan에서 `connect_all()`이 성공하면 `_connected = True`가 설정되므로, runner.py에서의 호출은 `if self._connected: return` 한 줄만 실행하고 종료된다.
- 이는 **방어적 코딩 패턴**이다. CLI 모드 등 lifespan 없이 `run_pipeline`이 직접 호출되는 경우를 위한 안전장치이다.
- 성능 오버헤드: bool 체크 1회이므로 무시할 수 있는 수준.

### 결론

**오탐**. 멱등성이 보장된 방어적 호출이며, 성능 오버헤드가 없다. CLI 모드 지원을 위해 의도적으로 유지된 코드이다.

---

## 요약

| 이슈 ID | 이슈 설명 | 판정 | 비고 |
|---------|----------|------|------|
| INT-01 | 라우팅 함수 state 직접 mutation | **부분 진양성** | `_route_after_sql_validator` L233, L252에서만 발생. 나머지 라우팅 함수는 순수 함수 |
| INT-02 | RedisCancelStore.pop_cancel 비원자적 | **진양성** | `GETDEL` 명령으로 간단히 해결 가능. 실무적 위험은 낮음 |
| INT-04 | get_compiled_app 순서 의존성 | **오탐** | FastAPI lifespan이 초기화 순서를 보장. CLI 모드는 의도된 설계 |
| LOG-01 | _sql_result_cache 전역 dict 문제 | **진양성** (조건부) | 메모리 누수는 아님 (상한 100). 멀티워커 불가는 정확한 지적 |
| LOG-02 | connect_all() 반복 호출 | **오탐** | 멱등 함수 (bool 체크 1회). 방어적 코딩이자 CLI 모드 지원 |

### 등급별 분류

- Warning (INT-01): `_route_after_sql_validator`에서 `recovery_entry_source` mutation을 sql_validator_node로 이동 권장
- Info (INT-02): `GETDEL` 적용 권장 (1줄 수정)
- 오탐 (INT-04, LOG-02): 수정 불필요
- Info (LOG-01): 멀티워커 전환 시 Redis 캐시로 교체 필요 (현재 단계에서는 허용)
