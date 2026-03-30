# 예외처리 / 변수 관리 상세 리포트

- **검토 일시**: 2026-03-30
- **검토 관점**: 업무요건상 예외처리가 미흡하거나 부적절한 코드, 전역변수/멤버변수가 과도하게 선언된 경우

---

## 요약

| ID | 카테고리 | 위치 | 한줄 요약 |
|----|---------|------|----------|
| E-01 | 안정성 | redis_store.py | connect 전 호출 시 AttributeError 미방어 |
| E-02 | 안정성 | retry.py | LLM 호출 예외 시 노드 컨텍스트 복원 누락 |
| E-03 | 안정성 | neo4j_connector.py | 인메모리 캐시에 크기 제한 없음 |
| E-04 | 안정성 | seed_sql_history.py | DB 엔진 타임아웃 미설정 |
| E-05 | 안정성 | HistoryDBConnector | SELECT 문 검증 없음 — 의도 불명확 |
| E-06 | 안정성 | trace_analyzer.py | FinalStatus enum 비교 시 타입 불일치 가능 |
| V-01 | 실수방지 | state.py | 모듈 레벨 상수가 import 시점에 고정 |
| V-02 | 보안 | config.py | 비밀 필드 12개에 SecretStr 미사용 (**Critical C-05**) |
| V-03 | 안정성 | reranker.py | 전역 환경변수 런타임 변경 |
| V-04 | 유지보수성 | config.py | model_config에 extra 정책 미설정 |

---

## E-01. (안정성) RedisSessionStore connect 전 호출 시 방어 누락

### 위치
- `src/services/session/redis_store.py` — `get_history`, `append_history`, `clear_session` 등

### 문제 상세

`RedisSessionStore`는 `connect()` 메서드로 Redis 클라이언트를 초기화한다. `self._client`는 초기값이 `None`이며, `connect()` 호출 후에야 Redis 클라이언트 인스턴스가 할당된다.

그러나 `get_history`, `append_history`, `clear_session` 등의 메서드에서 `self._client`가 `None`인 경우에 대한 **방어 로직이 없다**:

```python
async def get_history(self, session_id: str) -> list[dict]:
    raw = await self._client.lrange(...)  # self._client가 None이면 AttributeError
    ...
```

**시나리오**: `connect()` 호출 전에 `get_history`가 호출되거나, `connect()` 실패 후에도 세션 접근이 시도되면 `AttributeError: 'NoneType' object has no attribute 'lrange'` 에러가 발생한다. 이는 사용자에게 비친화적인 기술 에러 메시지로 노출된다.

### 해결 방안

**방안 A (권장)**: 방어 가드를 프로퍼티로 구현

```python
class RedisSessionStore(SessionStore):
    def _ensure_connected(self) -> None:
        """Redis 클라이언트가 초기화되었는지 확인한다."""
        if self._client is None:
            raise RuntimeError(
                "RedisSessionStore가 초기화되지 않았습니다. "
                "connect()를 먼저 호출하세요."
            )

    async def get_history(self, session_id: str) -> list[dict]:
        self._ensure_connected()
        raw = await self._client.lrange(...)
        ...
```

**방안 B**: `connect()` 실패 시 자동으로 MemorySessionStore로 폴백

```python
async def connect(self):
    try:
        self._client = redis.asyncio.Redis(...)
        await self._client.ping()
    except (ConnectionError, TimeoutError) as e:
        logger.warning("Redis 연결 실패, 메모리 스토어로 폴백", error=str(e))
        self._fallback = MemorySessionStore()
```

금융 서비스에서는 방안 A가 **명시적 실패**로 더 안전하다. 사일런트 폴백은 데이터 유실 위험이 있다.

---

## E-02. (안정성) retry.py에서 LLM 호출 예외 시 노드 컨텍스트 복원 누락

### 위치
- `src/utils/llm/retry.py:90-168`

### 문제 상세

```python
async def llm_call_with_parse_retry(..., node_name=None):
    _prev_node = get_current_node()
    if node_name:
        set_current_node(node_name)      # 노드 컨텍스트 변경

    for attempt in range(max_retries):
        response = await client.create(...)  # ← 여기서 Exception 발생 시?
        try:
            result = parse_fn(response)
            if node_name:
                set_current_node(_prev_node)  # 성공 시만 복원
            return result
        except ParseError:
            ...  # 재시도 루프 계속

    if node_name:
        set_current_node(_prev_node)      # 최종 실패 시 복원
    raise ParseError(...)
```

문제: `client.create()` 호출에서 **네트워크 에러, 타임아웃, API 에러** 등 예기치 않은 Exception이 발생하면, `set_current_node(_prev_node)`가 호출되지 않고 함수가 탈출한다.

이후 다른 노드에서 tracker를 사용하면 **이전 노드의 컨텍스트가 잔존**하여, 추적 데이터가 오염된다.

### 해결 방안

`try/finally` 패턴으로 감싼다:

```python
async def llm_call_with_parse_retry(..., node_name=None):
    _prev_node = get_current_node()
    if node_name:
        set_current_node(node_name)

    try:
        for attempt in range(max_retries):
            response = await client.create(...)
            try:
                return parse_fn(response)
            except ParseError:
                ...  # 재시도
        raise ParseError(...)
    finally:
        if node_name:
            set_current_node(_prev_node)  # 성공/실패/예외 모두에서 복원 보장
```

---

## E-03. (안정성) Neo4j 인메모리 캐시에 크기 제한 없음

### 위치
- `src/connectors/impl/neo4j_connector.py:49`

### 문제 상세

```python
self._cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
```

Neo4j 커넥터는 Cypher 쿼리 결과를 인메모리 dict에 TTL 기반으로 캐시한다. TTL 만료된 항목은 조회 시 제거되지만, **최대 항목 수 제한이 없다**.

**시나리오**: 다양한 파라미터로 `search_join_paths`, `search_domain_tables` 등이 호출되면, 캐시 키가 무한히 증가한다. 장시간 운영 시 메모리 사용량이 지속적으로 증가하여 OOM 위험이 있다.

### 해결 방안

**방안 A (권장)**: `cachetools.TTLCache` 사용

```python
from cachetools import TTLCache

class Neo4jConnector(SearchConnector):
    def __init__(self, ...):
        self._cache = TTLCache(
            maxsize=500,     # 최대 500개 항목
            ttl=self._ttl,   # TTL 설정 유지
        )
```

**방안 B**: 기존 dict 기반에 maxsize 제한 추가

```python
MAX_CACHE_SIZE = 500

async def _execute_cypher_cached(self, query, params):
    cache_key = f"{query}:{params}"

    # 캐시 크기 초과 시 가장 오래된 항목 제거
    if len(self._cache) >= MAX_CACHE_SIZE:
        oldest_key = min(self._cache, key=lambda k: self._cache[k][0])
        del self._cache[oldest_key]

    # ... 기존 TTL 로직
```

방안 A가 표준 라이브러리 기반으로 더 신뢰성 있다.

---

## E-04. (안정성) seed_sql_history.py DB 엔진 타임아웃 미설정

### 위치
- `src/tools/seed_sql_history.py:197`

### 문제 상세

```python
engine = create_async_engine(dsn)
```

`create_async_engine` 호출 시 `pool_timeout`, `connect_args` 등 타임아웃 설정이 없다. 시딩 스크립트는 대량 데이터를 처리하므로:
- DB 연결이 끊어져도 무한 대기할 수 있음
- 느린 쿼리가 타임아웃 없이 실행됨
- 연결 풀 고갈 시 새 연결을 무한 대기

### 해결 방안

```python
engine = create_async_engine(
    dsn,
    pool_timeout=30,               # 연결 풀에서 연결 획득 대기 최대 30초
    pool_size=5,                   # 동시 연결 수 제한
    connect_args={
        "command_timeout": 60,     # 단일 쿼리 최대 60초
    },
)
```

가능하면 `settings`에서 읽도록 하여 환경별 조정이 가능하게 한다.

---

## E-05. (안정성) HistoryDBConnector에 SELECT 문 검증 없음

### 위치
- `src/connectors/impl/postgres_connector.py:199-218` — `HistoryDBConnector.execute_query`

### 문제 상세

`InfoDBConnector`는 `re.match(r"^\s*(SELECT|WITH)\b", ...)` 검증이 있어 SELECT/WITH 외의 SQL을 차단한다. 그러나 `HistoryDBConnector`에는 **동일한 검증이 없다**.

현재 `HistoryDBConnector`는 SQL 이력 조회에 사용되므로 SELECT만 필요한 것으로 보이지만:
- 이력 저장(INSERT)에도 사용된다면 의도적 생략
- SELECT만 사용한다면 검증 누락 (버그)

**코드만으로는 의도를 판단할 수 없어 위험하다.**

### 해결 방안

**의도를 코드에 명시한다:**

```python
# 의도적 생략인 경우
class HistoryDBConnector(DatabaseConnector):
    """SQL 이력 DB 커넥터.

    Note:
        이력 저장(INSERT)에도 사용되므로 SELECT 제한을 적용하지 않는다.
        단, 이 커넥터는 이력 DB 전용이며 정보계 DB 접근에 사용해서는 안 된다.
    """
```

```python
# SELECT만 사용해야 하는 경우
async def execute_query(self, query, params=None):
    validate_readonly_query(query)  # D-03에서 추출한 공통 유틸 사용
    ...
```

---

## E-06. (실수방지) trace_analyzer.py에서 FinalStatus enum 비교 타입 불일치 가능

### 위치
- `src/utils/tracker/trace_analyzer.py:401, 548`

### 문제 상세

```python
# 401줄: JSON에서 로드된 dict의 문자열 값과 비교
if data.get("final_status") == FinalStatus.FAILURE:

# 548줄: TraceReport 객체의 enum 속성과 비교
if trace_report.final_status == FinalStatus.SUCCESS:
```

`data`는 JSON에서 파싱된 dict이므로 `data.get("final_status")`의 타입은 `str`이다. `FinalStatus`가 `StrEnum`(Python 3.11+) 또는 `str, Enum` 다중 상속이면 비교가 정상 작동하지만, 일반 `Enum`이면 **항상 `False`를 반환**한다.

### 해결 방안

1. `FinalStatus`가 `StrEnum` 또는 `str, Enum`인지 확인
2. 그렇지 않다면 명시적 변환을 추가:

```python
# 안전한 비교
if data.get("final_status") == FinalStatus.FAILURE.value:
```

또는 `FinalStatus`를 `StrEnum`으로 변경:

```python
# src/models/enums.py
class FinalStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    # ...
```

프로젝트의 다른 Enum들이 이미 `str, Enum` 패턴을 사용하고 있다면 일관성이 유지된다.

---

## V-01. (실수방지) 모듈 레벨 상수가 import 시점에 고정

### 위치
- `src/agents/state/state.py:58-63`

### 문제 상세

```python
# 모듈 최상위에서 settings 값을 상수로 바인딩
MAX_TOOL_CALLS = settings.max_tool_calls        # ex: 20
MAX_REPLANS = settings.max_replans               # ex: 3
MAX_GENERATES = settings.max_generates           # ex: 4
SQL_MAX_RETRY = settings.sql_max_retry           # ex: 2
CONFIDENCE_THRESHOLD = settings.confidence_threshold  # ex: 0.7
```

이 상수들은 **모듈이 import되는 시점에 `settings`에서 읽혀 고정**된다. 이후 런타임에서 `settings` 값을 변경해도 (예: 테스트에서 `settings.max_generates = 2`로 오버라이드) 이미 바인딩된 상수는 갱신되지 않는다.

**시나리오**:
- 테스트에서 `settings.max_generates = 1`로 설정해도 `MAX_GENERATES`는 여전히 4
- `conftest.py`에서 fixture로 설정을 변경해도 `state.py`의 상수에는 반영 안 됨
- 설정 변경이 **실제로는 무시**되어 디버깅이 매우 어려움

### 해결 방안

**방안 A (권장)**: 상수 대신 함수 호출로 변경

```python
def get_max_generates() -> int:
    return settings.max_generates
```

사용처에서:
```python
# 변경 전
if guard.generate_attempts >= MAX_GENERATES:
# 변경 후
if guard.generate_attempts >= settings.max_generates:
```

**방안 B**: 모듈 레벨 상수를 유지하되 `pytest`에서 `monkeypatch`를 사용

```python
# conftest.py
@pytest.fixture
def override_max_generates(monkeypatch):
    monkeypatch.setattr("src.agents.state.state.MAX_GENERATES", 2)
```

방안 A가 더 깔끔하며, 상수의 존재 이유 자체를 제거한다.

---

## V-02. (보안) config.py 비밀 필드에 SecretStr 미사용

> Critical C-05와 동일 이슈. `20260330-01-critical-issues.md#C-05` 참조.

---

## V-03. (안정성) reranker.py에서 전역 환경변수 런타임 변경

### 위치
- `src/connectors/impl/reranker.py:533`

### 문제 상세

```python
def _export_to_onnx(self, ...):
    os.environ["PYTHONIOENCODING"] = "utf-8"
    # ... ONNX 모델 변환
```

전역 환경변수를 런타임에 변경하는 것은 **프로세스 전체에 영향을 미치는 사이드 이펙트**이다:
- 다른 스레드/코루틴에서 `PYTHONIOENCODING`을 다른 값으로 기대하고 있을 수 있음
- 이 설정은 프로세스 시작 시점에만 유효하며, 런타임 변경은 실질적 효과가 없을 수 있음
- 테스트 환경에서 예측 불가능한 동작 유발

### 해결 방안

1. `PYTHONIOENCODING`은 프로세스 시작 시 `.env` 또는 셸 프로파일에서 설정
2. ONNX 변환에서 인코딩 이슈가 있다면 **subprocess에서 환경변수를 격리하여 설정**:

```python
import subprocess
result = subprocess.run(
    ["python", "-c", "import onnx; ..."],
    env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    capture_output=True,
)
```

3. 코드 내에서 설정이 반드시 필요하다면, 변경 전 값을 저장하고 완료 후 복원:

```python
_prev = os.environ.get("PYTHONIOENCODING")
try:
    os.environ["PYTHONIOENCODING"] = "utf-8"
    # ... ONNX 변환
finally:
    if _prev is None:
        os.environ.pop("PYTHONIOENCODING", None)
    else:
        os.environ["PYTHONIOENCODING"] = _prev
```

---

## V-04. (유지보수성) config.py model_config에 extra 정책 미설정

### 위치
- `src/config.py` — `Settings` 클래스의 `model_config`

### 문제 상세

Pydantic Settings의 `model_config`에 `extra` 필드가 설정되어 있지 않다. 기본값은 `"ignore"`로, `.env` 파일에 **오타가 있는 키가 조용히 무시**된다.

예:
```bash
# .env
ANTHROPIC_API_KYE=sk-...   # ← 오타 (KEY → KYE)
```

이 오타는 아무런 경고 없이 무시되며, `settings.anthropic_api_key`는 빈 문자열이 된다. 런타임에 LLM 호출이 실패하고 나서야 원인을 발견하게 된다.

### 해결 방안

**방안 A**: `extra = "forbid"` — 알 수 없는 키가 있으면 즉시 에러

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="forbid",  # 알 수 없는 환경변수가 있으면 ValidationError
    )
```

단, 시스템 환경변수(PATH, HOME 등)와 충돌할 수 있으므로 `.env` 파일만 대상으로 검증해야 한다.

**방안 B (권장)**: `extra = "ignore"` 명시 + 시작 시 검증 로그

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._warn_empty_required_fields()

    def _warn_empty_required_fields(self):
        """필수 설정이 비어있으면 경고한다."""
        critical_fields = ["anthropic_api_key", "info_db_host"]
        for field in critical_fields:
            if not getattr(self, field, ""):
                logger.warning(f"필수 설정 '{field}'이 비어있습니다. .env를 확인하세요.")
```

이 방식은 `.env` 오타를 직접 감지하지는 않지만, **결과적으로 비어있는 필수 필드를 조기에 발견**할 수 있다.
