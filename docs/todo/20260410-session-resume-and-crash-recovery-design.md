# 세션 재진입 UX 개선 및 크래시 복구 설계

**작성일**: 2026-04-10
**작성자**: 한철희
**상태**: 설계 확정 — 구현 계획 수립 단계

## 결정사항 (2026-04-10 사용자 확정)

1. **`/active` 응답에서 `turn_id` 필드 제거** — 프론트 미사용. 단, **서버 내부 저장소는 turn_id를 값으로 유지** (동시 재전송 race 방어용 CAS).
2. **Redis 우선 + Memory fallback** — L4 hash 기반 세션 고정이 있어도 안전망으로 Redis 기본, 미연결 시 Memory. CancelStore와 동일 분기 패턴.
3. **TTL은 settings로 노출** — `settings.active_run_ttl_seconds = 600` (기본 10분). 장기 실행 파이프라인 필요시 상향.
4. **Orphan gap 메시지는 기존 문구 재사용** — `'응답이 기록되지 않았습니다.'` ([embedded.html:1038](../../static/embedded.html#L1038), [embedded.html:2461](../../static/embedded.html#L2461))에서 이미 `turnType:'gap'` 으로 정의됨.
5. **Phase 2는 단일 변경(단일 PR)** — 단일 파일(embedded.html) 내 상호 결합된 모듈 4개라 단계 분할 시 중간 상태가 동작 불능. 수동 E2E 체크리스트로 회귀 방지.

## 전문가 검토 반영 사항 (2026-04-10)

**설계 강화** — pipeline-designer / security-guard / code-reviewer 3개 관점 검토 후 반영:

- **A. 파일/클래스 명명 변경**: `active_run_registry.py` → `src/services/active_run_store.py`, 클래스 `ActiveRunStore` / `MemoryActiveRunStore` / `RedisActiveRunStore`. 메서드 `set_active(session_id, turn_id)` / `clear_active(session_id, turn_id)` / `is_active(session_id) -> bool`. **cancel_store와 완전 대칭**.
- **B. CAS 기반 clear_active**: `cancel_store.pop_cancel` 과 동일하게 "내 turn_id가 현재 값일 때만 삭제". 동시 재전송 race 방어.
- **C. 래퍼 모듈 도입**: `src/agents/graph/active_run.py` 신설 — `cancel.py` 와 구조 대칭. `set_active()` / `clear_active()` / `check_active()` 헬퍼가 내부에서 `try/except + logger.warning` 으로 Redis 장애 흡수. 파이프라인이 Redis 실패로 중단되지 않도록 보장.
- **D. runner 통합 단순화**: `if store is not None` 가드 제거. 래퍼 헬퍼가 None 방어 및 예외 처리 담당.
- **E. session_id path 검증**: 엔드포인트에 path parameter regex validator 추가 (Redis 키 오염 방지).
- **F. 미해결 한계 명시**: 기존 sessions 라우터 전체가 user_id 소유권 미검증 상태. Phase 1은 기존 패턴과 일관성 유지하되 "세션 소유자 검증 일괄 도입" 을 별도 TODO 로 추적.

---

## 1. 배경과 문제

### 1.1 관찰된 버그 (크래시 시나리오)

서버가 파이프라인 실행 중 비정상 종료된 후 재기동되면, 해당 세션의 마지막 턴이
프론트엔드에서 "서버에서 처리 중…" 상태로 영구히 고정된다. 서버 로그에는
`GET /api/sessions/{session_id}` 요청이 반복 출력된다.

### 1.2 동작 원리 분석

- [src/agents/graph/runner.py:99-113](../../src/agents/graph/runner.py#L99-L113) 에서
  user 턴은 파이프라인 실행 **직전에 DB 에 조기 저장**된다 (크래시 시 메시지
  유실 방지 목적).
- 파이프라인이 완료되면 [runner.py:381-442](../../src/agents/graph/runner.py#L381-L442)
  에서 assistant 턴이 DB 에 저장된다. 크래시가 나면 이 저장이 실행되지 않는다.
- 재기동 후 프론트엔드는 `GET /api/sessions/{id}` 로 턴 목록을 받고
  [embedded.html:2449-2467 `_validateTurns`](../../static/embedded.html#L2449-L2467)
  에서 "마지막 턴이 user 이고 assistant 가 짝이 없으면 `_pendingResponse=true`"
  로 판정한다.
- `_pendingResponse=true` 이면
  [embedded.html:2414-2448 `_startPendingPoll`](../../static/embedded.html#L2414-L2448)
  이 2초 간격으로 최대 60회(2분) 폴링을 수행한다.
- 서버에 파이프라인이 실제로 돌고 있지 않으므로 assistant 턴은 영원히
  나타나지 않고, 사용자는 2분간 스피너만 바라본다. 세션을 다시 열 때마다
  폴링이 재시작되어 서버 로그가 계속 누적된다.

### 1.3 왜 폴링이 존재하는가 (재검토)

초기에는 "설계 근거 없는 안전망" 으로 의심했으나 코드를 정독한 결과,
정당한 요구사항이 있다:

- **세션 전환 시나리오**: 사용자가 세션 A 에서 질의를 시작하고 세션 B 로
  이동한 뒤 세션 A 로 돌아왔을 때, 파이프라인이 여전히 실행 중일 수 있다.
- 세션 전환 시
  [embedded.html:2234-2235 `reconnectWith`](../../static/embedded.html#L2234-L2235)
  가 기존 WebSocket 을 **강제로 close 하고** 새 연결을 연다. 세션 A 의 WebSocket
  이 죽었으므로 그 뒤 발생한 이벤트는 허공에 날아간다.
- 파이프라인은 서버 프로세스에서 계속 실행되고, 완료 시 결과가 DB 에 저장된다.
- 복귀 시 DB 를 폴링하여 assistant 턴이 등장하면 렌더링. 이것이 `_startPendingPoll`
  의 정당한 유스케이스다.

단, 이 폴링은 **"실제 실행 중" 과 "크래시 후 방치됨"** 을 구분하지 못한다.
이것이 버그의 직접 원인이다.

### 1.4 연관 UX 제약

세션 전환 후 복귀 시 **실시간 processing steps** (`msg.progress`) 는 복원되지 않는다.
이유는:

- `msg.progress` 는 WebSocket `stream.*`, `progress.*` 이벤트를 통해 실시간으로
  `MS` 모듈 메모리에 쌓인다.
- 세션 전환 시 [embedded.html:2370 `MS.clear()`](../../static/embedded.html#L2370)
  가 전부 삭제한다.
- 재연결된 WebSocket 은 과거 이벤트를 재전송하지 않는다 (서버에 이벤트 버퍼
  없음 — [runner.py](../../src/agents/graph/runner.py),
  [callback_handler.py](../../src/utils/tracker/callback_handler.py) 확인 완료).
- DB 에 저장되는 것은 `process_summary` (파이프라인 완료 후 구조화 요약)
  뿐이며, 실시간 스텝 진행 상태는 저장되지 않는다.

결과적으로, 복귀 시 사용자는 "스피너 → 완료된 결과" 만 보고 중간 과정을
볼 수 없다.

---

## 2. 요구사항

### 2.1 필수 요구사항

- **R1**: 서버 비정상 종료 후 재기동 시 크래시로 버려진 턴을 감지하고
  사용자에게 "응답 유실" 을 표시한다. 무한 폴링을 차단한다.
- **R2**: 세션 전환 후 복귀 시 정상 실행 중인 파이프라인의 결과를 받을 수
  있어야 한다 (현재 동작 유지).

### 2.2 희망 요구사항

- **R3**: 세션 전환 후 복귀 시 실시간 processing steps 가 끊김 없이 이어져
  보여야 한다.
- **R4**: 다중 워커 배포 환경에서도 동작해야 한다
  ([src/main.py:29](../../src/main.py#L29) 의 `--workers 4` 예시 참조).

### 2.3 비요구사항 / 명시적 범위 밖

- 토큰 단위 스트리밍 리플레이는 지원하지 않는다 (노드 단위 해상도로 충분).
- 브라우저 완전 재시작 (탭 닫고 며칠 후 재진입) 후 실시간 스텝 복원은
  불가능함을 받아들인다. 그 경우는 DB 이력 + gap 메시지로 대응.

---

## 3. 설계 개요

요구사항을 **두 Phase** 로 분리한다.

- **Phase 1**: R1, R4 해결 — 크래시 버그 차단. 서버 측 인메모리/Redis 레지스트리
  + `/active` 엔드포인트 + 프론트 분기.
- **Phase 2**: R2 유지, R3 달성 — 프론트 세션 스코프 상태 관리. `MS`/`CN`/`ED`/`RD`
  모듈 리팩토링.

Phase 2 는 Phase 1 의 `/active` 엔드포인트를 재사용한다 (WebSocket 재연결 후
크래시 감지 용도). 두 Phase 를 독립된 PR 로 진행하되 Phase 1 이 Phase 2 의
기반이 되도록 순서를 고정한다.

---

## 4. Phase 1: 크래시 버그 차단

### 4.1 핵심 아이디어

> **서버 프로세스 메모리가 이미 "지금 실행 중인 파이프라인" 의 진실을 알고 있다.**
> 그것을 조회 가능하게 노출하면 된다.

서버가 크래시하면 프로세스 메모리는 자동으로 비워진다. 재기동 후 새 프로세스의
메모리에는 어떤 활성 파이프라인도 없다 (실제로 새로 실행 중인 것이 없으므로
정확한 진실). 따라서 별도의 cleanup/reconciliation 로직이 필요하지 않다.

### 4.2 신규 모듈: ActiveRunStore

**위치**: [src/services/active_run_store.py](../../src/services/active_run_store.py) (신규)

**설계 근거**: 기존 [src/services/cancel_store.py](../../src/services/cancel_store.py)
와 **완전 대칭** 패턴(Protocol + Memory + Redis 구현). 프로젝트 일관성을 위해 동일 구조 채택.
turn_id를 값으로 저장하는 것도 cancel_store와 동일한 이유(CAS race 방어).

**Protocol**:

```python
from typing import Any, Protocol


class ActiveRunStore(Protocol):
    """세션별 활성 파이프라인 스토어 프로토콜."""

    async def set_active(self, session_id: str, turn_id: str) -> None: ...
    async def clear_active(self, session_id: str, turn_id: str) -> None: ...
    async def is_active(self, session_id: str) -> bool: ...
```

> **Note**: 엔드포인트 응답 스키마에는 turn_id를 노출하지 않음 (결정사항 #1).
> 내부 저장소는 turn_id를 유지하여 동시 재전송 시 CAS 삭제로 race 방어.

**MemoryActiveRunStore** (개발/테스트/단일 워커/Redis 장애 fallback):

```python
class MemoryActiveRunStore:
    """개발/테스트용 인메모리 활성 파이프라인 스토어.

    운영(multi-worker)에서 주 저장소로 사용 금지. Redis 장애 시 안전망 용도.
    """

    def __init__(self) -> None:
        self._active: dict[str, str] = {}  # session_id → turn_id

    async def set_active(self, session_id: str, turn_id: str) -> None:
        self._active[session_id] = turn_id

    async def clear_active(self, session_id: str, turn_id: str) -> None:
        """CAS: 현재 값이 내 turn_id 일 때만 삭제."""
        if self._active.get(session_id) == turn_id:
            self._active.pop(session_id, None)

    async def is_active(self, session_id: str) -> bool:
        return session_id in self._active
```

**RedisActiveRunStore** (운영/다중 워커):

```python
class RedisActiveRunStore:
    """운영용 Redis 활성 파이프라인 스토어.

    L4 hash 로 세션 sticky 라도 장애 대비 Redis 기본 사용.
    값에 turn_id 저장 → clear_active 에서 CAS 방식 삭제 (동시 재전송 race 방어).
    """

    # 현재 turn_id 가 내 것과 같으면 삭제. cancel_store.pop_cancel 과 동일 원리.
    _CAS_DEL_SCRIPT = (
        "local v=redis.call('GET',KEYS[1]); "
        "if v==ARGV[1] then redis.call('DEL',KEYS[1]); return 1 end; "
        "return 0"
    )

    def __init__(self, redis_client: Any, ttl_seconds: int = 600) -> None:
        self._client = redis_client
        self._ttl = ttl_seconds

    def _key(self, session_id: str) -> str:
        return f"active_run:{session_id}"

    async def set_active(self, session_id: str, turn_id: str) -> None:
        await self._client.set(self._key(session_id), turn_id, ex=self._ttl)

    async def clear_active(self, session_id: str, turn_id: str) -> None:
        await self._client.eval(
            self._CAS_DEL_SCRIPT, 1, self._key(session_id), turn_id,
        )

    async def is_active(self, session_id: str) -> bool:
        return bool(await self._client.exists(self._key(session_id)))
```

**TTL 논리**:
- 워커 프로세스가 `kill -9` 같은 방식으로 죽으면 `unregister` 가 실행되지
  않는다. TTL 이 없으면 해당 세션은 영구히 "active" 로 남는다.
- 기본 600초(10분)는 `cancel_store` 의 300초보다 길게. 파이프라인 실행 시간이
  5분을 초과할 가능성(복잡한 집계·LLM 재시도 등)을 고려한 보수적 값.
- `settings.active_run_ttl_seconds` 로 노출하여 운영 환경에서 조정 가능.
- TTL 은 "최악의 경우에도 X분 후에는 stale 레지스트리가 자연 소멸" 이라는
  보장이다. 정상 경로에서는 finally 블록이 즉시 해제한다.

**Settings 추가** — [src/config/settings.py](../../src/config/settings.py):

```python
active_run_ttl_seconds: int = 600  # ActiveRunRegistry TTL (stale 안전망)
```

### 4.3 싱글턴 래퍼 모듈 + lifespan 통합

**래퍼 모듈**: [src/agents/graph/active_run.py](../../src/agents/graph/active_run.py) (신규)

cancel.py 와 구조 대칭. Redis 장애를 래퍼에서 흡수하여 파이프라인 중단 방지.

```python
"""활성 파이프라인 추적 헬퍼 (ActiveRunStore 래퍼).

cancel.py 와 대칭 구조. Redis 장애 시 로깅 후 조용히 진행 —
활성 추적 실패가 파이프라인 자체를 막으면 안 된다는 원칙.
"""
from __future__ import annotations

import structlog

from src.services.active_run_store import ActiveRunStore

logger = structlog.get_logger(__name__)

_active_run_store: ActiveRunStore | None = None


def set_active_run_store(store: ActiveRunStore | None) -> None:
    global _active_run_store
    _active_run_store = store


def get_active_run_store() -> ActiveRunStore | None:
    return _active_run_store


def reset_active_run_store() -> None:
    global _active_run_store
    _active_run_store = None


async def mark_active(session_id: str, turn_id: str) -> None:
    """파이프라인 실행 시작 기록. 실패해도 조용히 진행."""
    store = _active_run_store
    if store is None:
        return
    try:
        await store.set_active(session_id, turn_id)
    except Exception as e:
        logger.warning("mark_active 실패", session_id=session_id, error=str(e))


async def clear_active(session_id: str, turn_id: str) -> None:
    """파이프라인 실행 종료 기록. 실패해도 조용히 진행."""
    store = _active_run_store
    if store is None:
        return
    try:
        await store.clear_active(session_id, turn_id)
    except Exception as e:
        logger.warning("clear_active 실패", session_id=session_id, error=str(e))


async def check_active(session_id: str) -> bool:
    """세션에 활성 파이프라인이 있는지 조회. 실패 시 False."""
    store = _active_run_store
    if store is None:
        return False
    try:
        return await store.is_active(session_id)
    except Exception as e:
        logger.warning("check_active 실패", session_id=session_id, error=str(e))
        return False
```

**lifespan 통합** — [src/main.py:156-166](../../src/main.py#L156-L166) 의
CancelStore 초기화 바로 아래:

```python
from src.agents.graph.active_run import set_active_run_store
from src.services.active_run_store import (
    MemoryActiveRunStore,
    RedisActiveRunStore,
)
if settings.session_backend == "redis" and _redis:
    set_active_run_store(
        RedisActiveRunStore(
            _redis, ttl_seconds=settings.active_run_ttl_seconds,
        ),
    )
else:
    # Redis 미연결 (개발/단일워커/Redis 장애) 시 Memory fallback
    set_active_run_store(MemoryActiveRunStore())
```

### 4.4 runner 통합

**위치**: [src/agents/graph/runner.py:148-160 `run_pipeline`](../../src/agents/graph/runner.py#L148-L160)

`_execute_and_finalize` 호출을 try/finally 로 감싼다. register/unregister 를
runner 진입점에 두는 이유:

- 현재 `_execute_and_finalize` 는 자체 try/except 로 에러 턴을 저장한 후
  `raise` 로 예외를 전파한다. 더 상위인 `run_pipeline` 에서 finally 를
  추가하면 기존 에러 처리 경로에 간섭하지 않는다.
- 인터럽트 재개 시나리오에서는 State 에 turn_id 가 이미 있으므로 State 를
  조회해야 하는데, 우선 단순화를 위해 **session_id 만으로 레지스트리 키**
  를 구성한다. 이 프로젝트는 "세션당 동시 파이프라인 1개" 가정을 사용 중
  ([cancel_store.py:34](../../src/services/cancel_store.py#L34) 주석 참조 —
  `session_id → turn_id` 단일 매핑).

**수정안**:

```python
# run_pipeline() 내부, _execute_and_finalize 호출 직전
from src.agents.graph.active_run import mark_active, clear_active

# turn_id 가 이 시점에 확정되지 않으므로 session_id 를 placeholder 로 사용.
# CAS 키로만 쓰이며 응답 스키마에는 노출되지 않음.
_run_key = session_id
try:
    await mark_active(session_id, _run_key)
    return await _execute_and_finalize(
        app=app,
        run_config=run_config,
        ...
    )
finally:
    await clear_active(session_id, _run_key)
```

**중요**:

- 래퍼 `mark_active` / `clear_active` 가 내부에서 예외를 흡수하므로 Redis 장애가
  파이프라인을 막지 않음.
- `_run_key` 가 session_id 와 같아도 CAS 의 의미는 유지됨 (같은 세션에서 중첩
  실행이 일어나도 마지막 turn만 유효 — 실제로는 cancel_store 의 "세션당 1개"
  가정으로 중첩 실행은 없음).
- 향후 turn_id 가 확정 가능한 위치(`_execute_and_finalize` 내부 290줄 이후)에서
  `mark_active` 를 재호출하여 값을 갱신하는 개선은 Phase 1 v2 로 분리.

### 4.5 엔드포인트: GET /api/sessions/{id}/active

**위치**: [src/routers/sessions.py](../../src/routers/sessions.py) 에 추가.

```python
from fastapi import Path

class SessionActiveResponse(BaseModel):
    """세션 활성 파이프라인 조회 응답."""
    session_id: str
    active: bool


# Redis 키 오염 방지: 영숫자·하이픈·언더스코어만 허용 (session_id 생성 규칙 확인)
_SESSION_ID_PATTERN = r"^[A-Za-z0-9_-]{1,128}$"


@router.get(
    "/sessions/{session_id}/active",
    response_model=SessionActiveResponse,
)
async def get_session_active(
    session_id: str = Path(..., pattern=_SESSION_ID_PATTERN),
) -> SessionActiveResponse:
    """해당 세션에 현재 실행 중인 파이프라인이 있는지 반환한다.

    서버 프로세스 메모리(또는 Redis) 기반 조회. DB 접근 없음.
    서버 재기동 시 자동으로 비어있으므로 크래시 복구 공짜.
    """
    from src.agents.graph.active_run import check_active
    active = await check_active(session_id)
    return SessionActiveResponse(session_id=session_id, active=active)
```

**패턴 검증 전 할일**: 실제 프로젝트에서 session_id 생성 규칙을 확인하고
정규식을 맞춤 (UUID 형식이면 `^[0-9a-f-]{32,36}$`, 현재 관찰된 `session-<epoch>`
형식이면 `^session-[0-9]+$`).

**세션 소유자 검증**: 현재 sessions 라우터 전체가 user_id 검증 없음. Phase 1 은
기존 패턴과 일관성 유지하되 별도 TODO 로 일괄 도입 추적.

**서비스 레이어 경유 여부**: CancelStore 는 라우터에서 직접 store 를 호출한다
([src/routers/sessions.py:196-220](../../src/routers/sessions.py#L196-L220)
`cancel_pipeline` 참조). 같은 패턴으로 `session_service` 를 거치지 않고 라우터에서
직접 registry 조회. DB 접근이 없으므로 서비스 레이어는 불필요.

**응답 모델 위치**: [src/models/api/session_models.py](../../src/models/api/session_models.py)
에 `SessionActiveResponse` 추가 (다른 응답 모델들과 함께).

### 4.6 프론트엔드 분기 수정

**위치**: [static/embedded.html:2396-2398](../../static/embedded.html#L2396-L2398)

**API 모듈에 메서드 추가**: (기존 `API.getSession` 근처)

```javascript
getSessionActive: function(id) {
    return _fetchJSON('/api/sessions/' + encodeURIComponent(id) + '/active');
}
```

**loadSession 수정**:

```javascript
if (enriched._pendingResponse) {
    // 서버에 진짜 실행 중인 파이프라인이 있는지 확인
    try {
        var activeInfo = await API.getSessionActive(id);
        if (activeInfo && activeInfo.active) {
            _startPendingPoll(id);  // 진짜 실행 중 → 폴링
        } else {
            _renderOrphanGap();  // 크래시로 버려진 턴
        }
    } catch (e) {
        // 네트워크 오류 시 안전한 기본값 (폴링 시작 안 함)
        _renderOrphanGap();
    }
}
```

**`_renderOrphanGap` 구현**: 기존 `_validateTurns` 의 gap 메시지 로직과 동일한
스타일로 "응답이 기록되지 않았습니다" 메시지를 렌더링. 크래시 전용 문구는
쓰지 않음 (UX 혼란 방지, 일관성).

### 4.7 테스트 계획

**단위 테스트**: [tests/auto/unit/test_active_run_registry.py](../../tests/auto/unit/test_active_run_registry.py) (신규)

- `test_cancel.py` 를 템플릿으로 사용
- MemoryActiveRunRegistry: register → is_active → unregister → not active
- RedisActiveRunRegistry: fakeredis 사용 (tests 디렉토리에 이미 쓰이는지 확인
  필요 — 있으면 재사용, 없으면 mock)
- TTL 경계: set 후 TTL 경과 시뮬레이션

**통합 테스트**: [tests/auto/integration/test_runner_active_registry.py](../../tests/auto/integration/test_runner_active_registry.py) (신규)

- `run_pipeline` 실행 중 `is_active=True`, 완료 후 `False`
- 파이프라인 예외 발생 시 unregister 보장 (try/finally 검증)
- 취소 시에도 unregister 보장

**수동 테스트 시나리오**:

1. **크래시 후 재진입**: 질의 실행 중 `kill -9` 서버 → 재기동 → 세션 복귀 →
   gap 메시지 확인, 폴링 없음 확인 (네트워크 탭에서 /active 1회만 호출 확인)
2. **정상 진행 중 세션 전환**: 질의 후 다른 세션 이동 → 원 세션 복귀 → /active
   true → 폴링 시작 → DB 결과 수신 후 정상 렌더링
3. **네트워크 일시 끊김**: `/active` 호출이 실패하는 상황 → gap 메시지 표시
   (안전한 기본값)

### 4.8 Phase 1 변경 범위 추정

| 파일 | 변경 유형 | 예상 규모 |
|---|---|---|
| src/services/active_run_store.py | 신규 (Protocol + Memory + Redis) | ~100줄 |
| src/agents/graph/active_run.py | 신규 (래퍼: 싱글턴 + try/except 흡수) | ~80줄 |
| src/config/settings.py | `active_run_ttl_seconds` 추가 | ~3줄 |
| src/main.py (lifespan) | ActiveRunStore 초기화 | ~12줄 |
| src/agents/graph/runner.py (run_pipeline) | mark_active/clear_active 래핑 | ~10줄 |
| src/routers/sessions.py | 엔드포인트 추가 + pattern validator | ~25줄 |
| src/models/api/session_models.py | `SessionActiveResponse` 추가 | ~8줄 |
| static/embedded.html | `API.getSessionActive` + `_pendingResponse` 분기 | ~25줄 |
| tests/auto/unit/test_active_run_store.py | 신규 (cancel_store 테스트 복제) | ~120줄 |
| tests/auto/unit/test_active_run_wrapper.py | 신규 (예외 흡수 검증) | ~60줄 |
| tests/auto/integration/test_runner_active_tracking.py | 신규 (run_pipeline 통합) | ~80줄 |
| **합계** | | **~523줄** |

---

## 5. Phase 2: 세션 전환 UX 개선

Phase 1 머지 후 착수. 독립 PR.

### 5.1 핵심 아이디어

> **세션 전환 시 WebSocket 과 메시지 상태를 닫지 않고 프론트 메모리에 보관.**
> 세션 복귀 시 기존 상태를 그대로 렌더링.

서버 변경 없음. 프론트 `MS`/`CN`/`ED`/`RD` 모듈의 "단일 활성 세션" 가정을
"세션별 독립 상태 + 현재 포커스 세션" 모델로 재설계.

### 5.2 데이터 모델 변경

**현재**:
- `MS._m: Map<msgId, Message>` — 전역 메시지 풀
- `CN.ws: WebSocket` — 단일 WebSocket
- `CN.sid: string` — 단일 세션 ID
- `ED._cur: msgId` — 현재 활성 메시지 (스트리밍 중인 것)
- `ED._last: msgId` — 마지막 활성 메시지

**변경 후**:
```javascript
var sessionStates = {
    'session-abc': {
        ws: WebSocket,            // 이 세션의 WebSocket
        msgs: Map<msgId, Msg>,    // 이 세션의 메시지 풀
        cur: 'msg-xxx' | null,    // 현재 스트리밍 중 메시지 ID
        last: 'msg-yyy' | null,
        wsAttempt: 0,             // 재연결 시도 횟수
        wsStatus: 'connected' | 'connecting' | 'disconnected',
        lastActivityAt: timestamp // LRU 용
    },
    ...
};
var activeSessionId = 'session-abc'; // 현재 DOM 에 표시 중인 세션
```

### 5.3 모듈별 리팩토링

#### CN (WebSocket Connection)

**변경 원칙**:
- 하나의 `ws` 대신 `Map<sessionId, WebSocket>`
- `openFor(sessionId)` — 해당 세션의 WS 가 없으면 새로 연결, 있으면 재사용
- `sendTo(sessionId, payload)` — 특정 세션으로 전송 (호환성을 위해 `send()`
  는 현재 active 세션으로 전송)
- `closeFor(sessionId)` — 특정 세션의 WS 만 닫음
- `onmessage` 는 **sessionId 를 closure 로 캡처**하여 ED 에 전달

**LRU 정책**:
- `MAX_SESSIONS_IN_MEMORY = 5` (상수)
- 신규 세션 진입 시 LRU 위반이면 가장 오래된 idle 세션의 WS 닫고 state drop
- 단, 활성 파이프라인이 있는 세션(`active=true` 로 확인된 상태)은 eviction
  대상에서 제외 — 중요한 UX 요구사항

#### MS (Message Store)

**변경 원칙**:
- 전역 `_m: Map` → `_statesBySession: Map<sessionId, Map<msgId, Msg>>`
- 모든 API 에 암묵적 `activeSessionId` 참조. 외부 호출부에 sessionId 명시 전파는
  최소화 (하위 호환).
- `MS.clear()` 는 "현재 active 세션만 클리어" 로 의미 변경
- `MS.clearSession(sessionId)` 신규 — 특정 세션 전체 제거 (LRU eviction 시)
- `_persist()` 는 현재처럼 sessionStorage 에 active 세션만 저장. 다른 세션들은
  메모리에만. (브라우저 탭 닫으면 휘발되는 것 수용)

#### ED (Event Dispatcher)

**변경 원칙**:
- `_cur`, `_last` 가 세션별로 분리됨
- `ED.handle(data, sessionId)` — sessionId 파라미터 추가
  - `sessionId === activeSessionId` 이면 기존처럼 MS 업데이트 + RD.render
  - 아니면 MS 업데이트만 (background update), RD.render 호출 안 함
- progress / stream / viz 핸들러들도 모두 sessionId 라우팅

#### RD (Renderer)

**변경 원칙**:
- `RD.render(msg)` — msg 의 소유 세션이 active 가 아니면 no-op
  - 이를 위해 msg 객체에 `sessionId` 필드 추가 필요 (MS.create 시 주입)
- `RD.clearChat()` — 현재 active 세션의 DOM 만 정리
- `RD.replayAll(sessionId)` 신규 — 세션 복귀 시 해당 세션의 모든 msg 를 DOM 에
  다시 그림

### 5.4 플로우 변경

#### loadSession(id) 재설계

```
1. 현재 activeSessionId 저장 (나중에 복귀 가능)
2. activeSessionId = id 로 전환
3. sessionStates[id] 존재?
   ├─ YES (메모리에 있음)
   │   - RD.clearChat() (DOM 초기화)
   │   - RD.replayAll(id) (메모리 → DOM 재렌더링)
   │   - WS 는 유지 (새로 연결 X)
   │   - API.getSession 호출 불필요
   │
   └─ NO (처음 열거나 LRU 에서 evict 됨)
       - API.getSession(id) 로 DB 이력 로드
       - sessionStates[id] = { msgs: 새 Map, ws: null, ... }
       - MS 에 DB 턴들 등록
       - RD.clearChat() + RD.replayAll(id)
       - CN.openFor(id) 로 WS 새로 연결
       - 마지막 턴이 user 이고 assistant 없으면 Phase 1 의 /active 호출
         * active=true → _startPendingPoll(id)
         * active=false → _renderOrphanGap()
4. LRU 체크 — sessionStates 크기가 MAX 초과하면 oldest idle 제거
```

#### 백그라운드 이벤트 처리

```
WebSocket.onmessage (sessionId 캡처됨):
  ED.handle(parsed, sessionId)
    sessionId === activeSessionId ?
      └─ YES: MS 업데이트 + RD.render (기존 경로)
      └─ NO: MS 업데이트만 (background), lastActivityAt 갱신
```

이 동작으로:
- 세션 A 에서 질의 후 세션 B 로 이동 → 세션 A 의 WS 유지
- 세션 A 의 파이프라인 이벤트들이 도착하면 → sessionStates['session-a'].msgs 에
  progress 가 누적됨
- 세션 A 로 복귀 → RD.replayAll → 그때까지 쌓인 progress 가 DOM 에 한 번에 그려짐
- 이후 도착하는 이벤트는 active 세션이므로 실시간 렌더링

#### WS 재연결 + 크래시 감지 (Phase 1 재사용)

```
ws.onclose (의도치 않음):
  1. 재연결 시도 (backoff)
  2. 재연결 성공:
     a. sessionStates[sid].msgs 에 streaming/thinking 상태 메시지가 있는가?
     b. 없으면 end (정상)
     c. 있으면 → API.getSessionActive(sid) 호출
        * active=true → 그대로 대기 (서버가 파이프라인 계속 실행 중)
        * active=false → 진행 중 메시지를 error 상태로 마킹
          * sid === activeSessionId 이면 즉시 DOM 업데이트
          * 아니면 상태만 업데이트, 복귀 시 렌더링
```

### 5.5 엣지케이스

1. **사용자가 동일 세션 ID 에서 두 번째 질의 전송** — 기존 `_cur` 가 남아있으면
   새 질의 수신 시 `_cur` 를 갈아끼움. 세션별 `cur` 필드로 분리했으므로 동작
   동일.

2. **LRU eviction 시 활성 파이프라인 있는 세션 보호** — eviction 후보 선정 시
   해당 세션의 msgs 에 streaming 상태 메시지가 있으면 skip. 극단적 상황
   (모든 세션이 활성) 에서는 cap 초과 허용.

3. **브라우저 백그라운드 탭 throttling** — WebSocket 자체는 유지되지만
   `setTimeout` 기반 재연결이 느려질 수 있음. 사용자 인지 가능한 지연.
   수용 가능.

4. **WS 재연결 시 서버가 파이프라인을 보유하고 있지 않음** — 서버가 죽은
   상황이므로 `/active` false → 진행 중 메시지를 failed 로 마킹. 사용자는
   재진입 시 즉시 알 수 있음.

5. **여러 세션에서 동시에 질의 전송** — 각 세션의 WS 가 독립적으로 이벤트
   수신. 서버는 워커 프로세스가 각 세션의 파이프라인을 독립 실행. 병렬성
   확보.

6. **Phase 1 의 `_pendingResponse` → `/active` 분기** 와 Phase 2 의 "메모리에
   상태 있음 분기" 가 겹치지 않도록 순서 주의. Phase 2 의 loadSession 은
   "메모리에 상태 있으면 메모리 우선" 이다. DB 재로드는 메모리에 없을 때만.

### 5.6 Phase 2 변경 범위 추정

| 영역 | 변경 성격 | 추정 규모 |
|---|---|---|
| static/embedded.html MS 모듈 | 데이터 모델 재설계 | ~80줄 |
| static/embedded.html CN 모듈 | Map 기반 WS 관리 + LRU | ~120줄 |
| static/embedded.html ED 모듈 | sessionId 라우팅 | ~60줄 |
| static/embedded.html RD 모듈 | replayAll 추가, 세션 인식 | ~50줄 |
| static/embedded.html loadSession/newSession | 플로우 재설계 | ~60줄 |
| static/embedded.html WS 재연결/크래시 감지 | Phase 1 통합 | ~40줄 |
| **프론트 합계** | | **~410줄** |

변경 규모 자체보다 **기존 "단일 세션 가정" 에 의존하는 호출부 탐색** 이 더 큰
노력. MS/CN/ED/RD 호출하는 모든 지점을 점검해야 함.

---

## 6. 리스크 / 가정 점검

### 6.1 다중 워커 배포 시나리오

**리스크**: Phase 1 에서 MemoryActiveRunRegistry 는 워커별로 독립 메모리.
워커 A 가 파이프라인 실행 → 사용자가 REST GET /active 호출 → 로드밸런서가
워커 B 로 라우팅 → 워커 B 는 해당 세션을 모름 → active=false 반환 → **프론트가
orphan gap 으로 잘못 표시**.

**완화**: `settings.session_backend == "redis"` 일 때 RedisActiveRunRegistry
자동 선택. 운영 환경에서 Redis 필수.

**검증 필요**: `docs/todo/20260405-fastapi-ha-configuration.md` 에 명시된
운영 배포 가이드와 일치하는지 교차 확인 필요.

### 6.2 WebSocket 과 REST 의 워커 일치 문제

**리스크**: WebSocket 은 최초 accept 한 워커에 고정되지만, REST 는 매 요청
라운드로빈. 파이프라인이 워커 A 에서 실행되고, 같은 브라우저의 REST 가 워커
B 로 라우팅되면 인메모리 레지스트리로는 일관성 보장 불가.

**해결**: Redis 레지스트리 사용 시 워커 일치 불필요 (상태가 Redis 에 있음).

### 6.3 TTL 만료 후 실행 중인 경우

**리스크**: 파이프라인이 10분(TTL)을 초과해도 실행 중일 수 있다. TTL 만료 후
`/active` 가 false 반환 → 프론트가 gap 처리 → 곧이어 assistant 턴이 DB 에
저장 → 모순된 UX.

**완화**:

- `settings.active_run_ttl_seconds` 로 운영 환경에서 상향 가능 (결정사항 #3)
- 초기 기본 600초는 보수적 값. 실제 파이프라인 최대 latency 측정 후 조정 권장
- 장기적으로 refresh 전략(주기적 TTL 갱신) 검토 가능. 현재는 범위 밖
- 사용자가 수동 새로고침하면 DB 이력으로 정상 복원됨 (데이터 손실 없음)

### 6.4 turn_id 정확성 (해소됨)

결정사항 #1로 `turn_id` 필드를 응답 스키마에서 제거. `is_active` bool 만
사용하므로 이 리스크는 소멸.

### 6.5 프론트 리팩토링 회귀 리스크 (Phase 2)

**리스크**: MS/CN/ED/RD 는 embedded.html 에서 수백 줄 규모로 상호 의존.
세션 스코프 도입 시 호출부 전수 조사 실패하면 회귀 발생.

**완화** (결정사항 #5 — 단일 변경):

- 단일 파일 내 4개 모듈이 강결합되어 단계 분할 시 중간 상태가 동작 불능
- 대신 **구현 전 수동 E2E 체크리스트 확정** 및 **변경 중 모듈별 커밋 분리**
- 수동 E2E 시나리오:
  1. 단일 세션 정상 질의/응답
  2. 질의 실행 중 다른 세션 이동 → 복귀 → progress 끊김 없음 확인
  3. 질의 중 새 세션 생성 → 원 세션 복귀 → 양쪽 독립 확인
  4. 6개 이상 세션 진입 → LRU eviction 동작, 활성 파이프라인 세션 보호 확인
  5. 서버 크래시 후 복귀 → `/active` false → gap 메시지
  6. WS 일시 끊김 → 자동 재연결 → 상태 보존 확인

### 6.6 sessionStorage 용량 한계

**리스크**: Phase 2 에서 메모리에 여러 세션의 msgs 를 보관. sessionStorage 는
5MB 제한. msgs 가 많으면 초과 가능.

**완화**: `_persist()` 는 현재도 active 세션만 저장. Phase 2 에서도 동일 정책
유지. 다른 세션들은 메모리에만 보관 (브라우저 탭 닫힘 시 소실 수용).

### 6.8 동시 재전송 race (전문가 검토 반영)

**리스크**: 사용자가 질의 A 전송 직후 빠르게 질의 B 전송 시, A 의 finally
`clear_active` 가 B 의 `set_active` 이후 실행되면 **B 가 실행 중인데
레지스트리가 비어 `is_active=false` 오탐** 가능.

**완화**: `clear_active(session_id, turn_id)` 가 CAS (현재 값이 내 turn_id
일 때만 삭제) 방식으로 구현됨. B 가 덮어쓴 이후에는 A 의 clear 가 no-op.
`cancel_store` 와 동일 원리.

**주의**: 현재 runner 통합에서 turn_id placeholder 로 session_id 를 쓰고 있어
CAS 효과가 약화됨 (두 턴이 같은 key). 그러나 프로젝트의 "세션당 1개 동시 실행"
가정상 중첩 자체가 드물며, 발생해도 최악의 결과는 "막 시작한 B 턴이 orphan 으로
오표시" 이며 DB 이력으로 자동 복원됨. Phase 1 v2 에서 실제 turn_id 전달로 완전
해결 가능.

### 6.9 Redis 런타임 장애 (전문가 검토 반영)

**리스크**: `RedisActiveRunStore.set_active` / `clear_active` 호출이 Redis
flap 중 예외를 던지면, 래핑하지 않으면 `_execute_and_finalize` 자체가 실행되지
않거나 finally 에서 예외가 원본 예외를 덮음.

**완화**: `src/agents/graph/active_run.py` 래퍼 모듈에서
`try/except + logger.warning` 으로 모든 Redis 예외 흡수. `cancel.py` 의
`check_cancel` 과 동일 원칙 — **활성 추적 실패가 파이프라인을 막으면 안 됨**.

### 6.10 다중 워커 + Redis 장애 중 안전성 한계

**리스크**: Redis 장애로 Memory fallback 전환 시, 워커 A 에서 실행 중인
파이프라인을 워커 B 가 `is_active` 조회하면 항상 false 반환 → orphan 오표시.

**완화**:

- L4 hash sticky session 이 정상이면 프론트의 `/active` 호출도 같은 워커로
  라우팅되어 문제 없음
- Sticky 깨지는 드문 경우에만 false positive → 사용자는 gap 메시지만 보고
  새로고침하면 DB 이력으로 복원
- 허용 가능한 degradation 으로 수용

### 6.11 Phase 2 는 Redis 전제 (전문가 검토 반영)

Phase 2 의 WS 재연결 후 `/active` 재사용은 **다중 워커 환경에서 Redis 가
필수**. Memory fallback 시 sticky 가 깨지면 잘못된 크래시 판정 가능.
Phase 2 착수 전 Redis 연결 상태 확인을 startup 단계에 경고로 추가 권장.

### 6.7 "register 전 early return" 경로

**리스크**: [runner.py:95-97](../../src/agents/graph/runner.py#L95-L97) 의
`prepared.early_result` 경로는 sanitize 실패 시 register 없이 return 한다.
registry 에 등록되지 않은 채 종료되므로 unregister 도 불필요. 문제 없음.

하지만 **register 시점을 `_execute_and_finalize` 직전으로 해야** 이 경로에
간섭하지 않음. 설계안 4.4 의 위치가 정확.

---

## 7. 자체 코드 리뷰

### 7.1 검증된 가정

- ✅ `cancel_store.py` 패턴이 ActiveRunRegistry 에 그대로 적용 가능
  ([src/services/cancel_store.py](../../src/services/cancel_store.py))
- ✅ `run_pipeline` → `_execute_and_finalize` 단일 경로로 파이프라인 실행
  (interrupt 재개도 동일 경로)
- ✅ `_execute_and_finalize` 의 try/except 가 모든 예외를 잡은 후 `raise` 로
  전파하므로, 상위 finally 블록이 정상 동작
  ([runner.py:451-486](../../src/agents/graph/runner.py#L451-L486))
- ✅ lifespan 에서 CancelStore 와 동일한 패턴으로 초기화 가능
  ([main.py:156-166](../../src/main.py#L156-L166))
- ✅ MS 가 단일 `Map` 기반이며, sessionStorage 에 active 세션만 저장하는 구조
  ([embedded.html:952-993](../../static/embedded.html#L952-L993))
- ✅ CN 이 단일 `ws`, `sid` 변수에 의존. 세션 전환은 `reconnectWith` 로 강제
  close + 재연결
  ([embedded.html:2234-2235](../../static/embedded.html#L2234-L2235))
- ✅ ED 에서 `_cur`, `_last` 는 모듈 전역. 세션 인식 없음
  ([embedded.html:2089-2188](../../static/embedded.html#L2089-L2188))
- ✅ WS onclose 에 `_intentionalClose` 플래그가 있어 세션 전환과 실제 끊김
  을 구분함 → Phase 2 재연결 로직 설계 시 활용 가능
- ✅ process_summary 는 파이프라인 완료 후에만 DB 에 저장됨. 실시간 스텝은
  DB 에 없음 → Phase 2 의 세션 전환 상태 보존 동기와 일치
- ✅ LangGraph checkpointer 는 노드 경계에서 상태 저장. "현재 노드 실행 중"
  을 확실히 구분 불가 → 대안적 접근(체크포인트 기반 감지) 기각 정당
- ✅ 기존 세션 전환 관련 문서
  [docs/todo/20260405-conversation-history-ui-design.md](../../docs/todo/20260405-conversation-history-ui-design.md)
  에 명시적 "폴링 설계 근거" 는 없음. v5 리팩토링 중 추가된 것으로 추정

### 7.2 불확실한 부분 (구현 전 확인 필요)

- ❓ **Redis 클라이언트 API 호환성**: `redis.asyncio.Redis` 인지 확인. CancelStore
  구현과 동일한 클라이언트여야 함. [src/services/session/store.py](../../src/services/session/store.py)
  의 `_client` 필드 타입 확인 필요.
- ❓ **tests/ 디렉토리 내 fakeredis 사용 이력**: `cancel_store` 테스트에서 어떻게
  Redis 모의를 했는지 확인 후 동일 방식 재사용.
- ❓ **Phase 2 MS 의 msg.sessionId 주입이 _persist() 직렬화에 영향 있는지**:
  추가 필드가 sessionStorage 용량이나 기존 localStorage fallback 코드에 간섭
  하지 않는지 확인.

※ 결정사항 #1 (turn_id 제거), #2 (멀티워커 + L4 hash + Redis 기본), #3 (TTL
settings 노출), #4 (기존 gap 메시지 재사용), #5 (단일 변경) 로 기존 5개 불확실
점 중 2개 해소.

### 7.3 구현 가능성 종합 판단

**Phase 1**: **정상 구현 가능, 저위험**. 전문가 3명 검토 후 설계 보강 완료
(CAS race, Redis 장애 흡수 래퍼, session_id 검증). 기존 CancelStore / cancel.py
패턴을 대칭 복제하는 구조라 구현 난이도 낮고 테스트 용이. 변경 범위 ~523줄.

**Phase 2**: **구현 가능**. 단일 파일(embedded.html)이라 단계 분할의 실익이
없어 단일 변경으로 진행 (결정사항 #5). 대신 6.5 의 수동 E2E 체크리스트를
구현 전 확정하고, 모듈별 커밋 단위를 분리하여 git history 로 롤백 가능성
확보.

### 7.4 개선 제안 (선택)

- Phase 2 의 LRU cap(5)을 설정으로 추출 가능 (`settings.session_memory_cap`)
- Phase 1 완료 시점에 운영 체크리스트 문서에 "활성 런 레지스트리 Redis 설정"
  항목 추가 (해당 문서 존재 시)

---

## 8. Phase 1 구현 계획 (확정)

### 8.1 작업 순서 (의존성 기반)

#### Step 1 — 스토어 레이어 (독립적으로 테스트 가능)

1. `src/services/active_run_store.py` 신규
   - Protocol, MemoryActiveRunStore, RedisActiveRunStore (CAS Lua 포함)
2. `tests/auto/unit/test_active_run_store.py` 신규
   - `test_cancel_store.py` 를 템플릿으로 복제
   - Memory: set → is_active true → clear (올바른 turn_id) → is_active false
   - Memory: set(A) → clear(B) → is_active true (CAS 방어)
   - Redis: fakeredis 로 동일 시나리오
   - TTL 만료 시뮬레이션

#### Step 2 — 래퍼 모듈 (스토어 레이어 위)

1. `src/agents/graph/active_run.py` 신규
   - 싱글턴 get/set/reset + mark_active/clear_active/check_active 헬퍼
   - 예외 흡수 (try/except → logger.warning)
2. `tests/auto/unit/test_active_run_wrapper.py` 신규
   - 스토어 미설정 시 no-op 검증
   - 스토어가 예외를 던져도 헬퍼가 삼키는지 검증 (AsyncMock)
   - check_active 실패 시 False 반환 검증

#### Step 3 — Settings

1. `src/config/settings.py` 에 `active_run_ttl_seconds: int = 600` 추가

#### Step 4 — lifespan 통합

1. `src/main.py` 에 `set_active_run_store` 초기화 (CancelStore 블록 바로 아래)
   - Redis 있으면 RedisActiveRunStore(TTL from settings), 없으면 Memory

#### Step 5 — runner 통합

1. `src/agents/graph/runner.py::run_pipeline`
   - `_execute_and_finalize` 호출을 try/finally 로 래핑
   - 진입: `await mark_active(session_id, session_id)`
   - 종료: `await clear_active(session_id, session_id)` (finally)
2. `tests/auto/integration/test_runner_active_tracking.py` 신규
   - 정상 실행: 중간에 check_active True, 완료 후 False
   - 예외 발생: finally 에서 clear 보장
   - 취소: clear 보장
   - Redis 장애 주입: 파이프라인 정상 완료

#### Step 6 — API 엔드포인트

1. `src/models/api/session_models.py` 에 `SessionActiveResponse` 추가
2. `src/routers/sessions.py` 에 `GET /sessions/{session_id}/active` 추가
   - Path parameter regex validator
   - check_active 호출
3. 수동 테스트: curl 로 바로 확인 가능

#### Step 7 — 프론트엔드 분기

1. `static/embedded.html`
   - `API.getSessionActive(id)` 메서드 추가
   - `loadSession` 의 `_pendingResponse=true` 처리에서 `/active` 분기
   - `active=true` → `_startPendingPoll`, `active=false` → gap 메시지

### 8.2 검증 단계

**단위 테스트 pass 기준**: Step 1, 2, 5 의 pytest 100% pass

**수동 E2E 시나리오**:

1. **정상 경로**: 질의 실행 → 중간에 브라우저 DevTools 로 `/active` 호출 →
   true 확인 → 완료 후 false 확인
2. **크래시 시나리오**: 질의 실행 중 서버 `kill -9` → 재기동 → 세션 복귀 →
   gap 메시지 표시, 폴링 없음 (Network 탭에서 `/active` 1회만 확인)
3. **정상 진행 중 세션 전환**: 질의 후 다른 세션 이동 → 원 세션 복귀 →
   `/active` true → 폴링 시작 → DB 결과 수신 후 렌더링
4. **Redis 장애 주입**: Redis 정지 → 질의 실행 → 파이프라인 정상 완료 확인
   (Memory fallback 또는 로그 warning)
5. **session_id 인젝션 시도**: `curl /api/sessions/foo:bar*/active` →
   422 Unprocessable Entity 확인

### 8.3 롤백 전략

Phase 1 은 모든 변경이 "추가" 성격 (기존 코드 수정 최소). 문제 발생 시:

- 엔드포인트 롤백: 라우터 등록만 주석 처리 → 프론트는 catch 분기로 fallback
- runner 롤백: `mark_active`/`clear_active` 호출만 주석 처리
- 프론트 롤백: `/active` 분기 제거 (기존 `_startPendingPoll` 무조건 호출)

## 9. Phase 2 계획 (Phase 1 완료 후)

1. Phase 2 세부 설계 — 현재 섹션 5 기반으로 상세화 (별도 문서 또는 갱신)
2. Phase 2 단일 변경 구현 (결정사항 #5) — embedded.html 의 MS/CN/ED/RD 재설계
3. Phase 2 수동 E2E (6.5 체크리스트)

---

## 9. 열린 질문 (모두 해소됨)

5건 모두 사용자 결정으로 확정됨 — 문서 상단 "결정사항" 섹션 참조.
