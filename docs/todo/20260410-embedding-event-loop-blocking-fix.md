# 임베딩/리랭커 이벤트 루프 블로킹 수정 계획

> 작성일: 2026-04-10
> 상태: 구현 대기 (3차 코드리뷰 반영 완료, rev.3)
> 발단: BGE-M3 첫 질의 시 서버 hang + UI 오프라인 배너 표시
>
> rev.2 개정 사유:
> - 독립 코드리뷰에서 Reranker 내부에도 동일한 tracker dispatch 버그 발견 (블로커)
> - seed_sql_history.py 배치 스크립트 호환성 주장 오류 발견
> - connect()/disconnect() 수명 관리 보강
> - 공식 레퍼런스(FastAPI/PyTorch/FlagEmbedding) 기반 설계 근거 보강
>
> rev.3 개정 사유 (3차 독립 코드리뷰):
> - **CRITICAL** Reranker._ensure_model() try/except가 fail-fast 원칙을 파열 → 제거
> - Reranker 비활성 상태 워밍업 오인 방지 (public `enabled` property + 스킵 분기)
> - seed_sql_history.py 실제 호출 지점(line 634/693) 명시
> - disconnect() 시 `_background_tasks` 정리 누락 → 2초 wait + cancel 추가
> - 타입/dataclass 세부 보강 (Task[None], frozen+slots)

---

## 1. 배경

### 1-1. 현상

- 서버 기동 후 첫 질의 요청 시점에 서버가 응답 불능 상태에 빠짐
- 포트 8000은 `LISTENING` 상태 유지, 그러나 `/health`, WebSocket 모두 응답 없음 (curl 5초 timeout)
- UI([static/embedded.html](../../static/embedded.html))는 WebSocket `onclose` 즉시 오프라인 배너 표시
- 재기동 외 복구 수단 없음

### 1-2. 로그 타임라인 (query_id=01643744)

```
15:16:23.931  reasoning_preparer 완료
15:16:50.255  BGE-M3 모델 로딩 시작       ← 첫 질의 시 lazy load 진입
15:17:06.802  BGE-M3 모델 로딩 완료       ← 약 16초 블록
(이후 로그 없음 — "임베딩 생성 완료" 로그 누락)
```

`임베딩 생성 완료` 로그([src/connectors/impl/qdrant_connector.py:150](../../src/connectors/impl/qdrant_connector.py#L150))가
찍히지 않았으므로 forward pass 또는 직후 Qdrant query_points 호출 중 블록된 것으로 추정.
정확한 stall 지점은 py-spy dump 없이 확정 불가하나, 근본 원인과 해결 방향은 동일.

---

## 2. 근본 원인

### 2-1. sync 메서드를 async 컨텍스트에서 직접 호출

`QdrantConnector`의 임베딩/리랭커 관련 메서드가 `def`(sync)인데,
`async def` 메서드에서 `await` 없이 직접 호출하고 있다.

| 호출 경로 | 위치 | sync 호출 |
|---|---|---|
| `search_sql_history` (async) | [qdrant_connector.py:355](../../src/connectors/impl/qdrant_connector.py#L355) | `self.encode(query)` |
| `search_sql_history` (async) | [qdrant_connector.py:402](../../src/connectors/impl/qdrant_connector.py#L402) | `self._rerank(...)` |
| `search_manual` (async) | [qdrant_connector.py:277](../../src/connectors/impl/qdrant_connector.py#L277) | `self.encode_dense_only(query)` |
| `_rerank` (sync) | [qdrant_connector.py:434](../../src/connectors/impl/qdrant_connector.py#L434) | `reranker.rerank(...)` |

결과: FastAPI 단일 이벤트 루프가 BGE-M3 로딩(16초) + 첫 forward pass + 리랭커 추론
기간 내내 완전히 블록된다. 이 시간 동안 WebSocket ping/pong도 처리 못 하므로
UI는 연결 끊김으로 판정하고 오프라인 배너를 띄운다.

### 2-2. BGE-M3 모델 lazy load

[qdrant_connector.py:122-142](../../src/connectors/impl/qdrant_connector.py#L122-L142) `_ensure_embed_model()`은
최초 encode 호출 시점에 모델을 로드한다. `connect()`는 Qdrant 클라이언트만 열고 종료한다.

결과: 기동 시점엔 아무 징후 없이 정상으로 보이나, 첫 사용자 질의가 들어온 바로 그 순간에
처음으로 16초 블록이 발생한다. 실사용 관점에서 매우 나쁜 UX.

### 2-3. Reranker 동일 패턴

[src/connectors/impl/reranker.py:294](../../src/connectors/impl/reranker.py#L294) `_ensure_model()`도 lazy,
[reranker.py:314](../../src/connectors/impl/reranker.py#L314) `rerank()`는 sync. BGE-M3와 동일한 위험을 가진다.
`get_reranker()`는 싱글턴이라 `ConnectorManager` 밖에서 관리된다.

### 2-4. UI 오프라인 판정

[embedded.html:2222](../../static/embedded.html#L2222)
```js
ws.onclose=function(){ ... setStatus('disconnected'); _setOffline(true); ... }
```

`onclose` 즉시 오프라인 상태 전환. 이벤트 루프가 블록되면 WS keepalive 실패 → 소켓
close → 오프라인. 지수백오프 재연결은 있으나 블록 지속 중엔 무의미.

---

## 3. 검토 중 발견한 추가 이슈 (이번 수정 범위와 직교)

구현 시 혼동 방지를 위해 **이번 수정에서는 건드리지 않음**을 명시한다.

### 3-1. `search_manual`이 구버전 Qdrant API 사용

[qdrant_connector.py:280](../../src/connectors/impl/qdrant_connector.py#L280):
```python
results = await self._client.search(
    collection_name=...,
    query_vector=("dense", embedding),  # legacy 튜플 스타일
    ...
)
```

반면 `search_sql_history`는 최신 `query_points()` + `Prefetch`/`FusionQuery` 사용.
동일 커넥터 안에 두 세대의 qdrant-client API가 공존. qdrant-client 차기 메이저에서 깨질 가능성.

→ **별도 P2 작업으로 분리**. 이번 hang 수정과 섞으면 회귀 위험 증가.

### 3-2. `encode` vs `encode_dense_only` 비대칭

`encode`([qdrant_connector.py:144](../../src/connectors/impl/qdrant_connector.py#L144))는 tracker dispatch 있음,
`encode_dense_only`([qdrant_connector.py:231](../../src/connectors/impl/qdrant_connector.py#L231))는 없음.
결과: 매뉴얼 검색 임베딩은 추적 로그에 안 남는다.

→ **별도 P2 작업**. 이번 수정에서 tracker 위치는 이동하지만 비대칭 해소는 하지 않음.

### 3-3. embed+search 패턴 중복

`search_manual`과 `search_sql_history`가 "embed → qdrant query → payload 변환" 구조를
각자 구현. 공통 헬퍼 추출이 바람직하나 이번 범위 밖.

---

## 4. 설계 결정

### 4-1. 범위

- **P1 (이번 작업)**: 이벤트 루프 블로킹 제거 + 기동 시 선로딩
- **P2 (별도 작업)**: `search_manual` legacy API 전환, 공통 헬퍼 추출, tracker 비대칭 해소

### 4-2. 직렬화 메커니즘: **전용 Executor (max_workers=1)**

BGE-M3(FlagEmbedding 래퍼)는 스레드 안전성을 공식적으로 보장하지 않는다.
공식 레퍼런스 기반 근거:

- **PyTorch 메인테이너 Edward Z Yang**: "PyTorch C++ 하부 라이브러리는 스레드 안전을 기대하지만,
  Tensor 객체 자체는 다중 쓰기에 안전하지 않다." — [PyTorch Forums](https://discuss.pytorch.org/t/is-pytorch-supposed-to-be-thread-safe/36540)
- **PyTorch 포럼**: "module state를 변경하지 않는 한 추론은 스레드 안전하다"고 하지만,
  실제 conv2d 세그폴트 버그(#16828)도 보고된 바 있음.
- **Ultralytics YOLO 공식 가이드**: 멀티스레드 추론 시 "스레드별 독립 모델 인스턴스" 사용 권고,
  단일 인스턴스 공유는 "예측 불가능한 동작과 내부 상태 변경 위험"이 있음을 명시. —
  [YOLO Thread-Safe Inference](https://docs.ultralytics.com/guides/yolo-thread-safe-inference/)
- **FlagEmbedding GitHub**: thread-safety에 대한 공식 보장 문구 없음.

`asyncio.to_thread`는 기본(unbounded에 가까운) ThreadPoolExecutor를 쓰므로 동시 요청 2건
이상 시 두 스레드가 같은 모델 인스턴스에 동시 접근할 수 있어 **부적합**.

- 선택: `concurrent.futures.ThreadPoolExecutor(max_workers=1)`를 `QdrantConnector` 필드로 둠
- 모든 encode/rerank sync 호출을 `loop.run_in_executor(self._embed_executor, ...)`로 라우팅
- encode와 rerank는 `search_sql_history` 안에서 **순차 실행**되므로 동일 executor 재사용해도
  단일 요청 내 지연은 동일 (4-3 참조). 여러 요청이 큐잉될 경우 직렬화는 의도된 동작.
- 장점: "임베딩/리랭커는 단일 워커에서만 돈다"가 코드·로그로 명시됨, 디버깅 명확
- 트레이드오프: 동시 요청이 큐잉되므로 throughput은 단일 사용자 챗봇 환경 기준으로 충분.
  규모 확장 시 별도 모델 서버 분리(Infinity 등) 검토 필요(§11 P2 이후).

대안으로 검토한 `asyncio.Lock`은 기본 executor를 다른 I/O와 공유하므로 동시성 파악이
어려워짐. 기각.

### 4-3. 리랭커 executor 분리 여부

검토: 별도 executor를 두는 게 더 깔끔하지만, 실제 호출 경로는
`search_sql_history` 하나에서 encode → rerank가 **순차**로 이어진다. 병렬 실행 요구가 없음.
→ **동일 executor 재사용**. 코드/리소스 단순화.

### 4-4. 워밍업 실패 시 정책: **fail-fast**

폐쇄망 운영 원칙과 일관성을 위해 `connect()` 중 모델 로딩 실패 시 예외를 상위로 전파한다.

⚠️ **rev.3 중요 수정**: 기존 [reranker.py:306-312](../../src/connectors/impl/reranker.py#L306-L312)
`_ensure_model()`은 백엔드 로딩 실패를 try/except로 삼키고 `self._enabled = False`로
**폴백**한다. 이 동작은 fail-fast 원칙을 정면으로 파열시킨다(로딩 실패해도 기동은 성공,
첫 질의에서야 비활성 상태 드러남 — 바로 막으려던 사고 재현 경로). rev.3에서 해당 try/except를
**완전 제거**한다. 명시적 `reranker_enabled=False` 설정만 비활성화 수단으로 남긴다.

**실제 예외 흐름** (리뷰에서 정정됨):

1. `qdrant.connect()`에서 예외 발생 → `manager.connect_all()`이 예외 전파
2. `lifespan`의 `try` 블록에서 raise → **`_REQUIRED_CONNECTORS` 체크는 도달하지 않음**
   (이 체크는 connect는 성공했지만 health_check가 실패했을 때 동작하는 경로)
3. `finally` 블록이 실행되어 `store.disconnect()` + `manager.disconnect_all()` 호출
4. 부분 초기화 상태(`self._client`, `self._embed_executor`가 None일 수 있음)이지만
   `disconnect()`는 `if self._client:` / `if self._embed_executor is not None:` 방어로 안전
5. 서버 프로세스는 예외 스택트레이스와 함께 종료

결과: fail-fast는 동작하지만, 기동 로그에 나타나는 건 `_REQUIRED_CONNECTORS` 메시지가
아니라 **`connect_all()`에서 raise된 원본 예외**다. 운영 가이드 작성 시 이 점 반영 필요.

근거:

- lazy 폴백을 두면 "기동은 성공했으나 첫 질의에 실패하는" 배포 사고가 재발함
- 폐쇄망에서 모델 캐시가 없으면 어차피 프로덕션 불가 → 조기 감지가 옳음
- 기동 로그에 로딩 실패 원인이 스택트레이스로 명확히 남음 → 운영 조치 용이

### 4-5. Tracker dispatch 위치 이동 (encode + reranker 양쪽)

**두 군데**에 `asyncio.get_running_loop()` 기반 fire-and-forget tracker dispatch가 있다:

1. [qdrant_connector.py:164-181](../../src/connectors/impl/qdrant_connector.py#L164-L181) `encode()` 내부
2. [reranker.py:375-406](../../src/connectors/impl/reranker.py#L375-L406) `Reranker.rerank()` 내부 ⚠️ **rev.1 누락**

둘 다 executor 경유로 호출되면 워커 스레드에 running loop가 없어 `RuntimeError` →
try/except로 삼켜져 `_loop=None`으로 떨어지고 dispatch가 조용히 스킵되어 **추적 이벤트가
전부 유실**된다. rev.1 계획은 (1)만 다루고 (2)를 완전히 놓쳤다 — **독립 코드리뷰에서 잡힌
블로커 B1**.

**seed_sql_history.py 호출 지점 실측** (rev.3 보강): 대량 임베딩은 [seed_sql_history.py:634](../../src/tools/seed_sql_history.py#L634)
`encode_batch`(원래 로그 없음)를 사용하고, 검증 단계는 [seed_sql_history.py:693](../../src/tools/seed_sql_history.py#L693)
`encode()`의 latency 로그를 소비한다. 따라서 `encode()`의 로그 보존이 B3 대응의 실질적 근거.

#### 접근 방침 (rev.2)

로그와 tracker dispatch는 **역할이 다름**을 분리해서 처리한다:

- **`logger.info` 로그는 유지** (sync 메서드에서도 안전). encode와 rerank 내부의 기존
  latency/통계 로그는 그대로 둔다. 이렇게 하면 `seed_sql_history.py`의 배치 스크립트
  호환성 회귀(블로커 B3)도 자동 해소된다.
- **tracker dispatch만 async 컨텍스트로 이동**. encode/rerank sync 메서드는 dispatch
  로직을 완전히 제거하고, 호출자(`_encode_async`, `_rerank` async)가 dispatch를 수행.
- **통계 데이터 전달**: Reranker는 dispatch에 필요한 통계(input_count, filtered_count,
  output_count, latency_ms, top_scores)를 어딘가로 노출해야 한다. 선택지:
  - (A) `rerank()` 반환 타입을 `tuple[list[RerankCandidate], RerankStats]`로 변경
  - (B) `self._last_rerank_stats: dict` 속성에 저장 (호출 직후 읽기)
  → **결정: (A) 반환 타입 확장**. 이유: (B)는 stateful이라 max_workers=1 직렬화에
  의존적이고 테스트 격리가 어려움. (A)는 명시적이고 type-safe.

#### asyncio.create_task GC 방지 패턴

Python 공식 문서: `asyncio.create_task`의 반환 Task를 강참조로 보관하지 않으면
가비지 컬렉션되어 코루틴이 실행 전 취소될 수 있다. 이벤트 루프가 바쁠 때 tracker
이벤트가 사라지는 경로가 된다 (코드리뷰 C1).

→ `QdrantConnector`에 `self._background_tasks: set[asyncio.Task] = set()` 필드 추가,
tracker dispatch는 아래 패턴으로:

```python
task = asyncio.create_task(dispatch_tracking_event(...))
self._background_tasks.add(task)
task.add_done_callback(self._background_tasks.discard)
```

---

## 5. 변경 상세

### 5-1. `src/connectors/impl/qdrant_connector.py`

#### (a-0) 모듈 레벨 import 추가

현재 파일은 `asyncio`와 `concurrent.futures`를 import하지 않는다. 필요한 import 추가:

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor
```

기존 `import time as _time` 바로 아래에 배치.

#### (a) 필드 추가 (`__init__`)

```python
def __init__(self, use_dummy: bool = True) -> None:
    self._use_dummy = use_dummy
    self._client: Any = None
    self._embed_model: Any = None
    self._embed_executor: ThreadPoolExecutor | None = None
    self._background_tasks: set[asyncio.Task[None]] = set()
```

#### (b) `connect()` — 멱등 가드 + executor 생성 + 모델 선로딩 + 워밍업

```python
async def connect(self) -> None:
    if self._use_dummy:
        logger.info("Qdrant Dummy 모드로 초기화")
        return

    # 멱등성 가드 (코드리뷰 C3): 중복 호출 시 리소스 누수 방지
    if self._client is not None:
        return

    from qdrant_client import AsyncQdrantClient
    self._client = AsyncQdrantClient(
        host=settings.qdrant_host,
        port=settings.qdrant_port,
        timeout=settings.qdrant_request_timeout,
    )
    logger.info("Qdrant 연결 완료")

    # 임베딩 전용 단일 워커 executor
    self._embed_executor = ThreadPoolExecutor(
        max_workers=1,
        thread_name_prefix="qdrant-embed",
    )

    # BGE-M3 선로딩 + 워밍업 (이벤트 루프 블로킹 없이)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        self._embed_executor, self._ensure_embed_model,
    )
    await loop.run_in_executor(
        self._embed_executor, self._warmup_embed_model,
    )
```

새 메서드 `_warmup_embed_model`:
```python
def _warmup_embed_model(self) -> None:
    """첫 forward pass 비용을 기동 시에 소진한다.

    encode_batch를 그대로 호출하여 실사용 경로(dense+sparse 생성 및 결과 변환)를
    전부 예열한다. 내부 JIT, 토크나이저 캐시, sparse 정렬 루프까지 한 번씩 돌린다.
    """
    self.encode_batch(["워밍업"])
    logger.info("BGE-M3 워밍업 완료")
```

직접 `self._embed_model.encode(...)`를 호출하지 않는 이유: `encode_batch`는
결과를 `EmbeddingResult`로 변환하는 sparse 정렬 루프([qdrant_connector.py:207-227](../../src/connectors/impl/qdrant_connector.py#L207-L227))를 포함한다.
실사용 경로와 100% 동일해야 워밍업의 의미가 있다.

실패 시 예외는 상위로 전파 → lifespan이 `_REQUIRED_CONNECTORS` 체크에서 서버 기동 중단.

#### (c) `disconnect()` — executor 먼저 정리 후 client close

순서 중요 (코드리뷰 C4): executor에서 in-flight 중인 encode/rerank가 완료되기 전에
Qdrant client를 닫으면 향후 `encode_and_query`류 결합 호출에서 버그가 난다. 지금은
encode가 client를 쓰지 않지만 방어적으로 executor를 먼저 shutdown.

```python
async def disconnect(self) -> None:
    # (1) 진행 중인 tracker dispatch task 완료 대기 (rev.3: 코드리뷰 M-1)
    #     2초는 tracker HTTP POST/로그 기록 수준이라 충분. 초과 시 cancel.
    if self._background_tasks:
        pending = list(self._background_tasks)
        _, still_pending = await asyncio.wait(pending, timeout=2.0)
        for t in still_pending:
            t.cancel()
        self._background_tasks.clear()

    # (2) in-flight encode/rerank 완료 대기 후 executor 종료
    if self._embed_executor is not None:
        self._embed_executor.shutdown(wait=True)
        self._embed_executor = None

    # (3) 마지막으로 client close
    if self._client:
        await self._client.close()
        self._client = None
```

순서 근거:
- tracker task는 executor를 사용하지 않으므로 먼저 정리해도 안전
- tracker task 먼저 정리해야 executor shutdown 시 "pending task" 경고가 사라짐
- `asyncio.wait`(not `gather`): tracker 실패가 종료 흐름을 망치지 않도록 수동 제어
- `self._background_tasks.clear()`: 멱등 disconnect 시 잔여 참조 제거

#### (d) `encode()` — tracker dispatch만 제거, 로깅은 유지

블로커 B3 대응: rev.1은 로깅까지 제거하려 했으나, `seed_sql_history.py` 배치
스크립트가 `encode()`를 직접 호출하며 "임베딩 생성 완료" 로그에 의존한다. 로그는
sync 컨텍스트에서도 안전하므로 **유지**하고, tracker dispatch만 제거한다.

```python
def encode(self, text: str) -> EmbeddingResult:
    """단일 텍스트를 Dense + Sparse 벡터로 변환한다.

    latency 로깅은 유지. tracker dispatch는 async 호출자에서 수행.
    """
    start = _time.perf_counter()
    result = self.encode_batch([text])[0]
    elapsed = (_time.perf_counter() - start) * 1000

    logger.info(
        "임베딩 생성 완료",
        text_length=len(text),
        dense_dim=len(result.dense),
        sparse_nnz=len(result.sparse_indices),
        latency_ms=round(elapsed, 1),
    )
    return result
```

삭제 대상: 기존 [qdrant_connector.py:158-181](../../src/connectors/impl/qdrant_connector.py#L158-L181)의 tracker dispatch 블록(`dispatch_tracking_event`, `CONTEXT_EMBEDDING`, `_asyncio.get_running_loop` 등).

#### (e) `_encode_async` / `_encode_dense_async` 헬퍼 추가

async 메서드에서 호출할 래퍼. 로깅은 `encode()`가 이미 수행하므로 중복 금지.
tracker dispatch는 여기서 수행하되 `_background_tasks`로 GC 방지(코드리뷰 C1).
executor 미생성 방어 포함(코드리뷰 C5).

```python
def _spawn_background(self, coro: Any) -> None:
    """create_task + 강참조 보관으로 GC로 인한 취소 방지."""
    task = asyncio.create_task(coro)
    self._background_tasks.add(task)
    task.add_done_callback(self._background_tasks.discard)

async def _encode_async(self, text: str) -> EmbeddingResult:
    """executor 경유 안전 encode + tracker dispatch.

    - encode() 내부에서 latency 로그를 찍으므로 여기선 로깅 생략
    - tracker dispatch는 async 컨텍스트에서 fire-and-forget (GC 안전)
    """
    if self._embed_executor is None:
        raise RuntimeError(
            "임베딩 executor 미초기화 — connect() 미호출 또는 dummy 모드",
        )

    loop = asyncio.get_running_loop()
    start = _time.perf_counter()
    result = await loop.run_in_executor(
        self._embed_executor, self.encode, text,
    )
    elapsed = (_time.perf_counter() - start) * 1000

    from src.utils.tracker.dispatch import (
        dispatch_tracking_event, CONTEXT_EMBEDDING,
    )
    self._spawn_background(dispatch_tracking_event(
        CONTEXT_EMBEDDING, {
            "source": "embedding_encode",
            "query": truncate_trace(text),
            "results_count": 1,
            "results_summary": [
                f"dense_dim={len(result.dense)}",
                f"sparse_nnz={len(result.sparse_indices)}",
            ],
            "latency_ms": elapsed,
        },
    ))
    return result

async def _encode_dense_async(self, text: str) -> list[float]:
    """executor 경유 안전 dense-only encode.

    tracker dispatch 없음 — 기존 비대칭 유지 (P2에서 해소).
    """
    if self._embed_executor is None:
        raise RuntimeError(
            "임베딩 executor 미초기화 — connect() 미호출 또는 dummy 모드",
        )
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        self._embed_executor, self.encode_dense_only, text,
    )
```

#### (f) `search_sql_history` — encode 호출부 교체

[qdrant_connector.py:355](../../src/connectors/impl/qdrant_connector.py#L355):
```python
# 변경 전
emb = self.encode(query)
# 변경 후
emb = await self._encode_async(query)
```

#### (g) `search_manual` — encode_dense_only 호출부 교체

[qdrant_connector.py:277](../../src/connectors/impl/qdrant_connector.py#L277):
```python
# 변경 전
embedding = self.encode_dense_only(query)
# 변경 후
embedding = await self._encode_dense_async(query)
```

#### (h) `_rerank` — async화 + tracker dispatch 인수

[qdrant_connector.py:404-449](../../src/connectors/impl/qdrant_connector.py#L404-L449) `_rerank`를
async로 변경하면서, **Reranker에서 이관된 tracker dispatch를 여기서 수행**한다.
Reranker가 새 반환 타입 `tuple[list[RerankCandidate], RerankStats]`를 돌려주므로
이를 이용해 CONTEXT_RERANKED 이벤트를 디스패치한다 (§5-3 참조).

```python
async def _rerank(
    self,
    query: str,
    candidates: list[dict[str, Any]],
    top_k: int,
) -> list[dict[str, Any]]:
    if self._embed_executor is None:
        raise RuntimeError(
            "임베딩 executor 미초기화 — connect() 미호출 또는 dummy 모드",
        )

    from src.connectors.impl.reranker import (
        RerankCandidate, get_reranker,
    )

    rerank_candidates = [
        RerankCandidate(
            text=c.get("description", "") + " " + c.get("sql", ""),
            payload=c,
            score=c.get("_score", 0.0),
        )
        for c in candidates
    ]

    reranker = get_reranker()
    loop = asyncio.get_running_loop()
    # 주의: lambda 내부에서 raise된 예외는 Future로 래핑되어 await 지점에서
    # 재발생한다. try/except 없이 상위(search_sql_history)로 전파한다.
    reranked, stats = await loop.run_in_executor(
        self._embed_executor,
        lambda: reranker.rerank(
            query, rerank_candidates, top_k=top_k,
        ),
    )

    # tracker dispatch (async 컨텍스트, GC 안전)
    from src.utils.tracker.dispatch import (
        dispatch_tracking_event, CONTEXT_RERANKED,
    )
    self._spawn_background(dispatch_tracking_event(
        CONTEXT_RERANKED, {
            "source": "reranker",
            "query": truncate_trace(query),
            "results_count": len(reranked),
            "results_summary": [
                f"backend={settings.reranker_backend}",
                f"input={stats.input_count}",
                f"filtered={stats.filtered_count}",
                f"output={len(reranked)}",
                *(
                    truncate_trace(
                        f"{c.payload.get('description', '?')}"
                        f" (score={c.rerank_score:.3f})"
                    )
                    for c in reranked[:3]
                ),
            ],
            "latency_ms": stats.latency_ms,
        },
    ))

    result: list[dict[str, Any]] = []
    for item in reranked:
        d = (
            item.payload.copy()
            if isinstance(item.payload, dict)
            else {}
        )
        d["_score"] = item.rerank_score
        d["similarity"] = item.rerank_score
        result.append(d)
    return result
```

호출부([qdrant_connector.py:402](../../src/connectors/impl/qdrant_connector.py#L402)):

```python
# 변경 전
return self._rerank(query, payloads, top_k)
# 변경 후
return await self._rerank(query, payloads, top_k)
```

### 5-2. `src/connectors/manager.py`

#### Reranker 워밍업을 `connect_all()` 끝에 추가

```python
async def connect_all(self) -> None:
    if self._connected:
        return
    enabled = settings.enabled_connectors
    logger.info("커넥터 초기화 시작", enabled=sorted(enabled))

    for cfg_name, attr in _CONNECTORS:
        if cfg_name in enabled:
            await getattr(self, attr).connect()

    if self._adw_db:
        await self._adw_db.connect()
    if self._bigdata_db:
        await self._bigdata_db.connect()

    # Reranker 워밍업 (Qdrant 실접속 모드에서만)
    if "qdrant" in enabled and not self._use_dummy:
        await self._warmup_reranker()

    self._connected = True
    logger.info(
        "커넥터 초기화 완료",
        deployment=self._deployment,
        enabled=sorted(enabled),
    )

async def _warmup_reranker(self) -> None:
    """Reranker 모델 선로딩 + 워밍업.

    QdrantConnector._embed_executor를 재사용한다.
    Reranker 공개 API(enabled, warmup, rerank)만 사용한다.
    """
    from src.connectors.impl.reranker import (
        RerankCandidate, get_reranker,
    )
    reranker = get_reranker()

    # rev.3: 비활성 설정 상태에서는 워밍업 완료 로그가 오인 가능성 → 명시적 스킵
    if not reranker.enabled:
        logger.info("Reranker 비활성 설정 — 워밍업 스킵")
        return

    executor = self.qdrant._embed_executor
    if executor is None:
        logger.warning("Reranker 워밍업 스킵 — executor 없음")
        return

    loop = asyncio.get_running_loop()
    try:
        # 모델 선로딩 (비활성 시 warmup은 no-op)
        await loop.run_in_executor(executor, reranker.warmup)

        # 더미 rerank 1회로 forward pass 워밍업
        # (비활성일 땐 rerank도 원본 스코어 폴백이라 부작용 없음)
        dummy = [RerankCandidate(
            text="워밍업", payload={}, score=0.0,
        )]
        await loop.run_in_executor(
            executor,
            lambda: reranker.rerank("워밍업", dummy, top_k=1),
        )
        logger.info("Reranker 워밍업 완료")
    except Exception as e:
        logger.error("Reranker 워밍업 실패", error=str(e))
        raise
```

주의: `self.qdrant._embed_executor`는 같은 `src.connectors` 패키지 내 private 필드
접근이다. 공개 getter를 추가하는 방안도 검토했으나, manager와 qdrant는 동일 패키지이고
executor는 내부 인프라이므로 public 인터페이스 노출보다는 패키지 내 접근이 더 적절하다.

### 5-3. `src/connectors/impl/reranker.py`

#### (a-0) ⚠️ rev.3 CRITICAL: `_ensure_model()` try/except 제거 (fail-fast 복원)

기존 [reranker.py:306-312](../../src/connectors/impl/reranker.py#L306-L312):

```python
# 변경 전 (rev.3 제거 대상)
try:
    # ... 백엔드 로딩 ...
except Exception as e:
    logger.warning("Reranker 모델 로딩 실패, 비활성화 폴백", error=str(e))
    self._enabled = False
```

→ **try/except 블록을 완전히 제거**하여 로딩 실패 시 예외가 상위로 전파되도록 한다.
이로써 `warmup()` → `_ensure_model()` 경로에서 로딩 실패가 `_warmup_reranker`로 전파되고
최종적으로 `connect_all()`에서 raise되어 lifespan이 서버 기동을 중단한다.

근거:
- 로딩 실패 자동 폴백은 rev.2 §4-4 fail-fast 원칙과 정면 충돌
- 폴백 경로가 남아 있으면 "기동은 성공했으나 첫 질의에 리랭커 비활성" 사고 재현
- 명시적 비활성화 수단은 `settings.reranker_enabled=False` 하나만 남겨 이분법 유지
- `_enabled`가 False로 설정되는 경로는 `__init__`의 settings 체크와 `_ensure_model`의
  except 2곳뿐 → except 제거해도 명시적 비활성화(①)는 그대로 동작

#### (a) 공개 API 추가: `enabled` property + `warmup()`

```python
@property
def enabled(self) -> bool:
    """외부에서 활성 상태 조회용 (private _enabled 노출 회피)."""
    return self._enabled

def warmup(self) -> None:
    """모델을 선로딩한다. 비활성화 시 no-op.

    로딩 실패는 _ensure_model()에서 예외 전파 (rev.3 fail-fast).
    """
    if not self._enabled:
        return
    self._ensure_model()
```

#### (b) `RerankStats` dataclass 추가 (tracker dispatch용 통계 반환)

`dataclass` import는 이미 [reranker.py:26](../../src/connectors/impl/reranker.py#L26)에 존재하므로 추가 import 불필요.

```python
@dataclass(frozen=True, slots=True)
class RerankStats:
    """rerank 호출의 통계 정보. tracker dispatch용 불변 통계 객체."""
    input_count: int
    filtered_count: int
    latency_ms: float
```

#### (c) `rerank()` 반환 타입 변경 + 내부 tracker dispatch 제거 ⚠️ **핵심 수정**

기존 반환: `list[RerankCandidate]`
신규 반환: `tuple[list[RerankCandidate], RerankStats]`

이유: 워커 스레드에서 `asyncio.get_running_loop()`는 RuntimeError → tracker 이벤트
100% 드롭 (rev.1 블로커 B1). dispatch 로직을 완전히 제거하고, 통계만 반환값으로
노출해 호출자(`QdrantConnector._rerank` async)가 async 컨텍스트에서 dispatch 수행.

```python
def rerank(
    self,
    query: str,
    candidates: list[RerankCandidate],
    top_k: int | None = None,
) -> tuple[list[RerankCandidate], RerankStats]:
    """후보 문서를 재순위한다.

    latency 로깅은 sync 컨텍스트에서 유지. tracker dispatch는 호출자가 수행.
    """
    if top_k is None:
        top_k = settings.reranker_top_k

    if not candidates:
        return [], RerankStats(
            input_count=0, filtered_count=0, latency_ms=0.0,
        )

    if not self._enabled:
        result = _sort_by_score(candidates)[:top_k]
        return result, RerankStats(
            input_count=len(candidates),
            filtered_count=len(candidates),
            latency_ms=0.0,
        )

    self._ensure_model()
    if not self._enabled or self._backend is None:
        result = _sort_by_score(candidates)[:top_k]
        return result, RerankStats(
            input_count=len(candidates),
            filtered_count=len(candidates),
            latency_ms=0.0,
        )

    filtered = _prefilter(candidates)
    start = time.perf_counter()
    documents = [c.text for c in filtered]
    scores = self._backend.compute_scores(query, documents)
    elapsed = (time.perf_counter() - start) * 1000

    for candidate, score in zip(filtered, scores):
        candidate.rerank_score = score
    reranked = sorted(
        filtered, key=lambda c: c.rerank_score, reverse=True,
    )
    result_top = reranked[:top_k]

    # latency 로그는 유지 (sync 안전)
    logger.info(
        "Reranker 재순위 완료",
        backend=settings.reranker_backend,
        input_count=len(candidates),
        filtered_count=len(filtered),
        output_count=len(result_top),
        latency_ms=round(elapsed, 1),
        top_score=(
            round(result_top[0].rerank_score, 4)
            if result_top else 0
        ),
    )

    # ⚠️ 기존 375-406 라인의 tracker dispatch 블록은 완전 삭제
    # (워커 스레드에서 asyncio.get_running_loop 호출 시 RuntimeError 드롭 문제)

    stats = RerankStats(
        input_count=len(candidates),
        filtered_count=len(filtered),
        latency_ms=elapsed,
    )
    return result_top, stats
```

#### (d) 호출부 호환성 검증

`rerank()`를 외부에서 호출하는 곳은 **`QdrantConnector._rerank` 한 곳**만
확인됨(grep `reranker.rerank` 결과). 이 호출부는 §5-1(h)에서 새 반환 타입에
맞춰 수정된다. 다른 호출 없음.

### 5-4. 수정하지 않는 파일

- [src/main.py](../../src/main.py): lifespan 로직 변경 없음. `manager.connect_all()`이
  내부적으로 모든 워밍업을 처리함.
- [static/embedded.html](../../static/embedded.html): UI 오프라인 판정 로직은 이번 수정
  범위 밖. BGE-M3/Reranker 선로딩만으로 근본 해결됨.
- [src/tools/seed_sql_history.py](../../src/tools/seed_sql_history.py): 배치 스크립트로
  async 이벤트 루프 영향 없음. 실제 호출 지점 2곳:
  - [line 634](../../src/tools/seed_sql_history.py#L634) `encode_batch(texts)` (대량 처리, 원래 로그 없음)
  - [line 693](../../src/tools/seed_sql_history.py#L693) `encode(query)` (검증 단계, latency 로그 소비)

  sync 시그니처 + `encode()` latency 로그가 보존되므로 호출 코드 무수정.
  (rev.1은 로그 제거까지 고려했으나 B3 블로커로 반려 — rev.2에서 로그 유지로 확정)
- [src/main.py](../../src/main.py): `lifespan`의 `_REQUIRED_CONNECTORS` 검증 경로와
  예외 처리는 기존 그대로. 모델 로딩 실패는 `connect_all()`에서 raise되어 `finally`
  경로로 disconnect → 서버 종료가 자동으로 성립.

---

## 6. 수정 전/후 호출 흐름 비교

### Before (현재, 블록 발생)

```
WS message → pipeline.run (async)
  → context_retriever (async)
    → search_use_cases (async)
      → qdrant.search_sql_history (async)
        → self.encode(query)              ← SYNC, 16s+forward 블록
          → _ensure_embed_model()         ← 16s 모델 로딩
          → BGEM3FlagModel.encode(...)    ← forward pass (수백 ms)
        → self._client.query_points(...)
        → self._rerank(...)               ← SYNC
          → reranker.rerank(...)          ← SYNC, forward pass
```

**이벤트 루프**: 처음 진입부터 리턴까지 전부 블록. WS ping 응답 불가 → UI 오프라인.

### After (수정 후)

```
기동 시 (lifespan)
  → manager.connect_all()
    → qdrant.connect()
      → Qdrant 클라이언트 생성
      → executor(max_workers=1) 생성
      → run_in_executor(_ensure_embed_model)    ← 16s 루프 양보
      → run_in_executor(_warmup_embed_model)    ← forward pass 워밍업
    → _warmup_reranker()
      → run_in_executor(reranker.warmup)        ← 루프 양보
      → run_in_executor(reranker.rerank[dummy]) ← forward pass 워밍업

요청 시
WS message → pipeline.run (async)
  → context_retriever (async)
    → search_use_cases (async)
      → qdrant.search_sql_history (async)
        → await _encode_async(query)
          → run_in_executor(encode)             ← 루프 양보, 다른 WS 처리 가능
        → await self._client.query_points(...)
        → await self._rerank(...)
          → run_in_executor(reranker.rerank)    ← 루프 양보
```

**이벤트 루프**: CPU 작업 동안 다른 WS 메시지/health 요청 처리 가능. 동시 요청 시
encode/rerank는 단일 워커 큐로 직렬화되므로 정확성 보장.

---

## 7. 위험 및 완화

| # | 위험 | 완화 |
|---|---|---|
| 1 | BGE-M3 모델 다운로드 실패 시 기동 중단 | 의도된 동작(fail-fast). 폐쇄망 배포 전 캐시 검증 체크리스트에 추가 |
| 2 | executor 미생성 상태에서 encode 호출 (버그 시나리오) | `_encode_async`에서 `self._embed_executor is None`이면 `RuntimeError` 명시적 발생 |
| 3 | Dummy 모드에서 executor가 생성 안 됨 | `connect()`의 dummy 분기 early return 유지. Dummy 경로는 encode를 호출하지 않음 (search_dummy_* 사용) |
| 4 | tracker dispatch가 호출자로 이동해 누락 가능성 | `_encode_async`에서 중앙 집중 처리. `encode_dense_only`의 tracker 부재는 기존과 동일(별도 P2) |
| 5 | `run_in_executor`에 lambda 전달 시 closure 누수 | 람다는 rerank만 사용. 호출 즉시 소비되므로 수명 문제 없음 |
| 6 | 기동 시간 증가 (~17-20초 추가) | 의도된 트레이드오프. 기동 1회 비용으로 첫 질의 UX 확보 |
| 7 | `_rerank`를 async로 바꾸면서 호출부 `await` 누락 | 유일한 호출처는 `search_sql_history:402` 한 곳. PR 리뷰 시 grep으로 재확인 |
| 8 | 동시 요청 2건 이상 시 encode 큐잉으로 지연 체감 | 단일 사용자 챗봇 환경에서 무시 가능. 부하 테스트는 별도 |
| 9 | `disconnect()`의 `shutdown(wait=True)`이 in-flight encode 대기로 종료 지연 | lifespan은 요청 수신 중단 후 disconnect 호출. 일반적으로 in-flight 없음. 최악의 경우 수 초 대기 후 종료. 문제 시 `wait=False`로 변경 |
| 10 | `seed_sql_history.py` 등 배치 스크립트의 sync `encode()` 호출 | `encode()`/`encode_batch()` sync 시그니처 + latency 로그 유지. 스크립트는 executor 없이도 동작하며 "임베딩 생성 완료" 로그 의존성 보존 (B3 대응) |
| 11 | `Reranker.rerank()` 반환 타입 변경으로 외부 호출자 깨짐 | grep 결과 유일 호출처는 `QdrantConnector._rerank`. 본 PR에서 동시 수정. 외부 래퍼/테스트 추가 호출 여부는 구현 시 전체 grep으로 재확인 |
| 12 | tracker dispatch의 `asyncio.create_task` GC로 인한 이벤트 드롭 | `_background_tasks: set` 강참조 + `add_done_callback(discard)` 패턴으로 방지 (Python 공식 경고 대응, 코드리뷰 C1) |
| 13 | `connect()` 중복 호출 시 executor/client 누수 | `if self._client is not None: return` 멱등 가드 (코드리뷰 C3) |
| 14 | `disconnect()` 순서 오류로 in-flight encode 유실 | executor `shutdown(wait=True)` 선행 → client close 후행 (코드리뷰 C4) |
| 15 | `RerankStats` 추가로 `dataclass` import 필요 | reranker.py:26에 이미 import됨 → 추가 조치 불필요 |
| 16 | Reranker `_ensure_model` 예외 전파로 기존 "자동 폴백" 기대 코드 깨질 가능성 | grep 확인 결과 `_enabled=False` 의존 경로는 `rerank()` 내부의 `_sort_by_score` 폴백 하나. 명시적 `reranker_enabled=False` 설정만 그 경로로 진입하므로 정상 동작 (rev.3 C-1) |
| 17 | disconnect 중 tracker task 2초 wait 초과 시 취소로 이벤트 유실 | tracker는 HTTP/로그 수준 경량 작업으로 2초 초과는 이상 징후. 정상 종료 시 유실 없음. 비정상 종료는 의도된 fallback (rev.3 M-1) |

---

## 8. 검증 계획

### 8-1. 기동 검증

1. 서버 재기동 (`uv run uvicorn src.main:app`)
2. 기동 로그에서 순서 확인:
   ```
   Qdrant 연결 완료
   BGE-M3 모델 로딩 시작
   BGE-M3 모델 로딩 완료
   BGE-M3 워밍업 완료
   Reranker 워밍업 완료
   커넥터 초기화 완료
   서버 시작 완료
   ```
3. 기동 총 소요 시간 기록 (기존 대비 +17~20초 예상)

### 8-2. 첫 질의 응답성 검증

1. 서버 기동 직후 `curl http://localhost:8000/health` → 즉시 응답
2. UI 접속 → WebSocket 연결 → "연결됨" 상태 확인
3. 골든셋 쿼리 투입: `연체 고객의 고객등급 분포 알려줘`
4. 응답 중 `curl http://localhost:8000/health` 병행 → 즉시 응답 (블록 없음)
5. 로그에서 `임베딩 생성 완료` 확인

### 8-3. 회귀 방지

- 기존 E2E 테스트 수행 (pytest tests/e2e/)
- `search_use_cases`, `search_manual` 관련 단위 테스트 확인

### 8-4. Dummy 모드 검증

- `use_dummy=True`로 기동 → executor 생성 안 됨, 워밍업 스킵 확인
- dummy 경로로 골든셋 쿼리 정상 처리 확인

---

## 9. 롤백 전략

변경 범위가 `QdrantConnector` + `ConnectorManager` + `Reranker` 3파일로 한정.
git revert로 단일 커밋 롤백 가능하도록 **한 PR/커밋에 묶어서 진행**.

롤백 신호:
- 기동 시간이 30초 이상 증가 → 모델 로딩 실패 가능성 조사
- 기동 중 `connect_all()`에서 예외 발생 → 모델 캐시/네트워크 경로 확인 후 복구
- 회귀 테스트 실패 → 즉시 revert
- `CONTEXT_EMBEDDING`/`CONTEXT_RERANKED` tracker 이벤트 누락 감지 → dispatch 위치 재점검

rev.3 변경 범위 요약:

- `reranker.py`: **`_ensure_model()` try/except 제거(fail-fast)**, `enabled` property,
  `RerankStats(frozen=True, slots=True)`, `warmup()` 공개 API,
  `rerank()` 반환 타입 `tuple[list[RerankCandidate], RerankStats]`, 내부 tracker dispatch 제거
- `qdrant_connector.py`: `_embed_executor`/`_background_tasks` 필드, 멱등 `connect()`,
  `disconnect()`(task 정리→executor→client 3단계), `encode()` tracker dispatch 제거(로그 유지),
  `_encode_async`/`_encode_dense_async`/`_spawn_background`, async `_rerank` + stats dispatch
- `manager.py`: `_warmup_reranker()`(reranker.enabled 스킵 분기) + `connect_all()` 후반 호출

---

## 10. 작업 순서

1. 현재 hang된 프로세스(PID 76920) 종료 — **사용자 승인 필수**
2. 본 문서 재검토 및 사용자 최종 승인
3. 코드 수정 (순서 중요 — Reranker 반환 타입 변경이 qdrant_connector._rerank와 동시 진행):
   - reranker.py:
     - **[CRITICAL]** `_ensure_model()`의 try/except 블록 제거 (fail-fast 복원, rev.3 C-1)
     - `enabled` property 추가 (private `_enabled` 노출 회피)
     - `RerankStats(frozen=True, slots=True)` dataclass 신설 (dataclass import는 line 26에 기존재)
     - `warmup()` 공개 메서드 추가
     - `rerank()` 반환 타입을 `tuple[list[RerankCandidate], RerankStats]`로 변경
     - 기존 내부 tracker dispatch 블록(`asyncio.get_running_loop`/`CONTEXT_RERANKED`) 전체 제거
     - latency/통계 logger.info 로그는 유지
   - qdrant_connector.py:
     - `asyncio` + `ThreadPoolExecutor` import 추가
     - `_embed_executor`, `_background_tasks: set[asyncio.Task[None]]` 필드 추가
     - `connect()` 멱등 가드 + executor 생성 + 모델 선로딩 + `_warmup_embed_model`
     - `disconnect()`: `_background_tasks` 2초 wait + cancel → executor shutdown → client close
     - `encode()` tracker dispatch 블록 제거, latency 로그 유지
     - `_spawn_background` / `_encode_async` / `_encode_dense_async` 헬퍼 추가
     - `_rerank`를 async로 변환, `reranker.rerank()` 반환 tuple 언패킹 → CONTEXT_RERANKED
       dispatch (`_spawn_background` 경유)
     - `search_sql_history`, `search_manual`, `_rerank` 호출부 `await` 교체
   - manager.py:
     - `_warmup_reranker()` 신설: `reranker.enabled` 스킵 분기 + Qdrant 실접속 조건
     - `connect_all()` 말미에 호출
4. 로컬 기동 검증 (§8-1, 8-2)
5. 회귀 테스트 (§8-3)
6. Dummy 모드 확인 (§8-4)
7. 커밋 (한 덩어리)

---

## 11. 범위 밖 (후속 작업 대상)

다음 항목은 이 수정과 섞지 않고 별도 문서/PR로 진행:

- **P2-1**: `search_manual`을 `query_points()` modern API로 전환
- **P2-2**: `search_manual`과 `search_sql_history`의 embed+search 공통 헬퍼 추출
- **P2-3**: `encode_dense_only`의 tracker dispatch 추가 (비대칭 해소)
- **P3**: UI `ws.onclose` 즉시 오프라인 배너 → 500ms 지연 (flicker 방지, 선택 사항)
