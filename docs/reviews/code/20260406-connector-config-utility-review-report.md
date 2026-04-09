# 코드 리뷰: 커넥터, 설정, 유틸리티

- 일자: 2026-04-06
- 대상: `src/config.py`, `src/connectors/` 전체, `src/agents/utils/clarification_context.py`
- 중점: 보안, 성능, 에러 처리, 아키텍처, ES 잔존 코드, 폐쇄망 커넥터

---

## 재검증 결과 (2026-04-06 2차 검토)

아래 이슈들은 실제 코드와 대조 검증한 결과, 오탐 또는 심각도 조정이 필요한 것으로 확인되었습니다.

| 원래 ID | 판정 | 사유 |
|---------|------|------|
| **C-01 (Sybase 부분)** | **오탐 (제거)** | Sybase는 ODBC 연결 문자열 또는 키워드 인자 방식으로 연결. f-string URL 삽입 문제 해당 없음. PostgreSQL/MongoDB 부분만 유효 |
| **W-09** | **오탐 (제거)** | `enabled_connectors` 기반 선택적 연결이므로 "enabled = 필수" 의미론에 따라 fail-fast가 의도된 설계. 비필수 커넥터는 enabled 목록에서 제외하면 됨 |
| **C-02 (HistoryDB SELECT 제한)** | ✅ **모든 DML 허용 (A-5)** | 이력 적재 용도이므로 INSERT 등 DML 허용. 현행 유지 |
| **C-03 (Hive/Impala params 무시)** | ❌ **제외** | LLM 생성 SQL만 실행하므로 params 전달 경로 자체가 없음. 호출부(`execute_query`) 전수 확인 완료 — 모두 SQL 문자열만 전달 |
| **W-06 (Hive/Impala 중복)** | ⏸️ **통합 안함 (B-3)** | 폐쇄망 실 연동 전이므로 현행 유지 |

---

## Critical (RED)

### C-01. DB 연결 문자열에 비밀번호가 f-string으로 직접 삽입됨

**파일**: `src/connectors/impl/postgres_connector.py` L60-67, L172-179
**파일**: `src/connectors/impl/mongo_connector.py` L145-149
**파일**: `src/connectors/impl/sybase_connector.py` L91-99

비밀번호가 f-string 내에 직접 포함되어 URL/연결 문자열을 생성한다.
비밀번호에 `@`, `/`, `%`, `:` 등 특수문자가 포함되면 URL 파싱이 깨져 연결 실패 또는 예상치 못한 호스트 연결이 발생할 수 있다.
또한 예외 트레이스백에 연결 문자열이 노출될 수 있어 보안 위험이 존재한다.

```python
# 현재 (postgres_connector.py L60-67)
url = (
    f"postgresql+asyncpg://"
    f"{settings.info_db_user}"
    f":{settings.info_db_password}"    # 특수문자 미이스케이프
    f"@{settings.info_db_host}"
    f":{settings.info_db_port}"
    f"/{settings.info_db_name}"
)
```

**개선안**: SQLAlchemy `URL.create()` 사용으로 자동 이스케이프. MongoDB는 `urllib.parse.quote_plus()` 적용.
```python
from sqlalchemy.engine import URL
url = URL.create(
    drivername="postgresql+asyncpg",
    username=settings.info_db_user,
    password=settings.info_db_password,
    host=settings.info_db_host,
    port=settings.info_db_port,
    database=settings.info_db_name,
)
```

---

### C-02. HistoryDBConnector에 SELECT 문 제한 없음 -- 보안 위반

**파일**: `src/connectors/impl/postgres_connector.py` L212-231

HistoryDBConnector.execute_query()는 주석에 "범용 쿼리 실행 (SELECT 제한 없음, 이력 적재용)"이라고 명시하며, SQL 유형 검증 없이 임의 쿼리를 실행한다. 프로젝트 보안 규칙(`data-security.md`)은 SELECT만 허용하도록 명시하고 있다.

이력 적재가 필요하다면 별도의 `insert_history()` 메서드를 만들어 파라미터 바인딩 방식으로 INSERT만 허용하는 것이 안전하다.

```python
# 현재 (L217) -- 임의 쿼리 실행 가능
async def execute_query(
    self, query: str, params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """범용 쿼리를 실행한다 (SELECT 제한 없음, 이력 적재용)."""
```

**개선안**: SELECT 제한 로직 추가 + 이력 적재 전용 메서드 분리.

---

### C-03. Hive/Impala 커넥터 params 무시 -- SQL 인젝션 위험

**파일**: `src/connectors/impl/hive_connector.py` L126-137
**파일**: `src/connectors/impl/impala_connector.py` L124-135

execute_query() 시그니처에 `params` 파라미터가 있으나 실제 실행에서 완전히 무시된다. 파라미터 바인딩 없이 쿼리 문자열이 그대로 실행되므로, 상위 레이어에서 사용자 입력이 쿼리에 포함되면 SQL 인젝션이 가능하다.

```python
# hive_connector.py L126-137
def _execute() -> list[dict[str, Any]]:
    cursor = self._conn.cursor()
    cursor.execute(query)  # params 미사용
```

**개선안**: impyla cursor.execute()는 `cursor.execute(query, params)` 형태의 파라미터 바인딩을 지원한다. params가 전달되면 반드시 사용하도록 수정.

```python
def _execute() -> list[dict[str, Any]]:
    cursor = self._conn.cursor()
    if params:
        cursor.execute(query, params)
    else:
        cursor.execute(query)
```

---

### C-04. Hive/Impala/Sybase 동기 커넥터: sanitize_row 미적용

**파일**: `src/connectors/impl/hive_connector.py` L131-133
**파일**: `src/connectors/impl/impala_connector.py` L129-131
**파일**: `src/connectors/impl/sybase_connector.py` L186-191

PostgreSQL 커넥터는 `sanitize_row()`를 적용하여 `Decimal`, `date` 등을 JSON 직렬화 가능한 타입으로 변환하지만, Hive/Impala/Sybase 커넥터는 `sanitize_row()`를 호출하지 않는다. 폐쇄망 전환 시 `Decimal` 타입의 금액 컬럼이 JSON 직렬화 오류를 유발할 수 있다.

```python
# impala_connector.py L129-131 -- sanitize_row 미적용
rows = [
    dict(zip(columns, row))
    for row in cursor.fetchall()
]
```

**개선안**: `sanitize_row` import 후 적용.
```python
rows = [
    sanitize_row(dict(zip(columns, row)))
    for row in cursor.fetchall()
]
```

---

## Warning (YELLOW)

### W-01. ElasticSearch 설정 및 코드 잔존 -- 미사용 확정 시스템

**파일**: `src/config.py` L77-88 (ES 설정 12개 필드)
**파일**: `src/connectors/impl/elasticsearch_connector.py` (전체 202줄)
**파일**: `src/connectors/manager.py` L26-28, L51, L89

ES는 미사용이 확정되었으나 config.py에 12개의 ES 설정 필드, 전체 elasticsearch_connector.py 구현체, manager.py에서의 import 및 인스턴스 생성이 남아있다. 실제 `src/agents/` 내에서 ES를 직접 참조하는 코드는 없다.

`enabled_connectors`에서 주석 처리되어 실행되지 않지만, import로 인해 `elasticsearch` 패키지가 설치되어 있어야 하고, 새로운 개발자가 혼동할 여지가 있다.

**개선안**:
1. ES 관련 코드를 `_deprecated/` 디렉토리로 이동하거나 완전 제거
2. config.py에서 ES 설정 필드 제거
3. manager.py에서 ES import 및 인스턴스 생성 제거
4. dummy_data.py에서 `search_dummy_report_sql` 등 ES 전용 함수 정리

---

### W-02. InfoDBConnector/HistoryDBConnector 코드 중복 (~90%)

**파일**: `src/connectors/impl/postgres_connector.py`

두 커넥터의 `connect()`, `disconnect()`, `health_check()` 로직이 거의 동일하며, 차이점은 settings 참조 필드와 SELECT 제한 유무뿐이다. 약 90줄이 중복된다.

**개선안**: 공통 베이스 클래스 `_AsyncpgBaseConnector` 추출.
```python
class _AsyncpgBaseConnector(DatabaseConnector):
    def __init__(self, *, url_factory, use_dummy, readonly):
        ...
    async def connect(self): ...
    async def disconnect(self): ...
    async def health_check(self): ...

class InfoDBConnector(_AsyncpgBaseConnector):
    def __init__(self, use_dummy=True):
        super().__init__(url_factory=..., use_dummy=use_dummy, readonly=True)

class HistoryDBConnector(_AsyncpgBaseConnector):
    ...
```

---

### W-03. Hive/Impala 커넥터 코드 중복 (~95%)

**파일**: `src/connectors/impl/hive_connector.py`
**파일**: `src/connectors/impl/impala_connector.py`

두 파일의 구조가 거의 동일하다. 차이점은 settings 접두사(`hive_` vs `impala_`), 기본 포트, `default_schema` 뿐이다.

**개선안**: `_ThriftBaseConnector` 공통 베이스 추출 또는, 설정 딕셔너리를 받는 단일 클래스로 통합.

---

### W-04. Hive/Impala/Sybase: 단일 커넥션, 커넥션 풀 미적용

**파일**: `src/connectors/impl/hive_connector.py` L54 (`self._conn: Any`)
**파일**: `src/connectors/impl/impala_connector.py` L50 (`self._conn: Any`)
**파일**: `src/connectors/impl/sybase_connector.py` L60 (`self._conn: Any`)

세 커넥터 모두 단일 커넥션(`self._conn`)을 유지하며, asyncio.to_thread()로 동기 실행한다. 동시 요청 시 하나의 커넥션을 여러 스레드에서 공유하게 되어 race condition이 발생할 수 있다.

**개선안**:
- 최소한 스레드 안전한 커넥션 풀 구현 (예: `queue.Queue` 기반 simple pool)
- 또는 요청마다 커넥션을 생성/해제하는 방식으로 변경
- 장기적으로는 impyla의 connection pool 기능 또는 DBUtils 등 활용

---

### W-05. Neo4j 인메모리 캐시: 메모리 누수 위험, TTL 기반 만료만 존재

**파일**: `src/connectors/impl/neo4j_connector.py` L49 (`self._cache: dict`)

캐시가 `dict[str, tuple[float, list[dict]]]`로 무제한 성장한다. TTL 만료는 조회 시에만 확인하며, 만료된 항목도 삭제하지 않고 덮어쓴다. 다양한 쿼리가 반복되면 캐시 크기가 계속 증가한다.

**개선안**: `functools.lru_cache` 또는 maxsize 제한이 있는 TTL 캐시 사용. 또는 만료 항목 정리 로직 추가.

```python
# 간단한 maxsize 제한
MAX_CACHE = 1000

async def _execute_cypher_cached(self, cache_key, cypher, params=None):
    now = _time.time()
    if cache_key in self._cache:
        ts, data = self._cache[cache_key]
        if now - ts < settings.neo4j_cache_ttl:
            return data
    if len(self._cache) >= MAX_CACHE:
        # 가장 오래된 항목 제거
        oldest = min(self._cache, key=lambda k: self._cache[k][0])
        del self._cache[oldest]
    result = await self._execute_cypher(cypher, params)
    self._cache[cache_key] = (now, result)
    return result
```

---

### W-06. MongoConnector: to_list(length=None) 무제한 로드

**파일**: `src/connectors/impl/mongo_connector.py` L241, L290, L352

`collection.aggregate(pipeline).to_list(length=None)`은 결과 전체를 메모리에 로드한다. aggregation pipeline에 `$limit`이 포함되어 있지만, 그 전 단계에서 대량의 중간 결과가 생성될 수 있다. `length` 파라미터에 적절한 상한을 지정하는 것이 안전하다.

**개선안**: `to_list(length=limit * 2)` 등 상한 지정.

---

### W-07. config.py Settings 클래스가 과도하게 비대 (340줄, 100+ 필드)

**파일**: `src/config.py`

단일 `Settings` 클래스에 LLM, DB, ES, Qdrant, MongoDB, Neo4j, Redis, Embedding, Reranker, Impala, Hive, Sybase, 파이프라인, 세션, 차트, 로그 등 모든 설정이 혼재되어 있다. 설정 간 관계 파악이 어렵고, 특정 시스템 설정만 변경할 때 전체 파일을 편집해야 한다.

**개선안**: 중첩 모델로 분리.
```python
class LlmSettings(BaseModel):
    provider: str = "anthropic"
    anthropic_api_key: str = ""
    model: str = "claude-sonnet-4-20250514"
    ...

class DbSettings(BaseModel):
    info_db: DbConnectionInfo = DbConnectionInfo()
    history_db: DbConnectionInfo = DbConnectionInfo()
    pool_timeout: int = 30
    ...

class Settings(BaseSettings):
    llm: LlmSettings = LlmSettings()
    db: DbSettings = DbSettings()
    ...
```
단, 이 변경은 참조하는 모든 파일에 영향이 크므로 점진적 마이그레이션이 필요하다.

---

### W-08. Reranker/QdrantConnector: sync 메서드 내 fire-and-forget 태스크 패턴

**파일**: `src/connectors/impl/reranker.py` L373-404
**파일**: `src/connectors/impl/qdrant_connector.py` L156-179

sync 메서드(`encode`, `rerank`) 내에서 `asyncio.get_running_loop()`로 루프를 얻어 `loop.create_task()`로 트래킹 이벤트를 비동기 디스패치한다. 이 패턴은:
1. 이벤트 루프가 없는 환경(테스트, 스크립트)에서 무시됨
2. 태스크 실패 시 에러가 완전히 삼켜짐
3. 코드 의도 파악이 어려움

**개선안**: 트래킹 디스패치를 호출하는 쪽(async 노드)으로 이동하거나, 동기 큐 기반 트래킹으로 변경.

---

### W-09. ConnectorManager.connect_all() 에러 전파 미흡

**파일**: `src/connectors/manager.py` L135-160

개별 커넥터의 `connect()` 실패 시 예외가 그대로 전파되어 이후 커넥터 초기화가 중단된다. 한 커넥터 실패가 전체 시스템 기동을 막을 수 있다.

**개선안**: 개별 커넥터의 connect를 try-except로 감싸고, 실패한 커넥터를 로깅 후 계속 진행. 실패한 커넥터는 dummy 모드로 폴백.

```python
for cfg_name, attr in _CONNECTORS:
    if cfg_name in enabled:
        try:
            await getattr(self, attr).connect()
        except Exception as e:
            logger.error(
                "커넥터 초기화 실패, dummy 모드 폴백",
                connector=cfg_name, error=str(e),
            )
```

---

### W-10. ElasticSearchConnector: connect()에 타임아웃 미설정

**파일**: `src/connectors/impl/elasticsearch_connector.py` L50-59

AsyncElasticsearch 클라이언트 생성 시 `request_timeout` 기본값(10초)이 적용되지만, 연결 자체의 타임아웃은 별도로 설정되지 않았다. (ES 미사용 확정이므로 우선순위 낮음)

---

## Info (GREEN)

### I-01. interfaces.py의 sanitize_row -- 위치 적절성

**파일**: `src/connectors/interfaces.py` L74-99

`sanitize_row`와 `_to_json_safe`는 인터페이스 정의 파일에 위치하지만, 유틸리티 성격의 함수이다. `src/utils/` 하위로 이동하거나 `src/connectors/utils.py`로 분리하면 interfaces.py가 순수 계약 정의에만 집중할 수 있다.

---

### I-02. dummy_data.py 파일 규모 (893줄) -- 분리 검토

**파일**: `src/connectors/dummy_data.py`

단일 파일에 테이블 메타 6종, 보고서 SQL 3종, 코드 메타 7종, SQL 이력, 매뉴얼, Qdrant SQL 이력, 검색 헬퍼 등 모든 더미 데이터가 집중되어 있다. 데이터 추가 시 파일이 더 커질 것이다.

**개선안**: `connectors/dummy/` 디렉토리로 분리.
```
connectors/dummy/
    __init__.py         # 공개 API re-export
    table_meta.py       # 테이블 메타 데이터
    code_meta.py        # 코드 메타 데이터
    sql_data.py         # SQL 이력 + 보고서 SQL
    manual_data.py      # 업무 매뉴얼
    generator.py        # generate_dummy_data + 헬퍼
```

---

### I-03. Neo4j 커넥터 내 더미 데이터 함수 위치

**파일**: `src/connectors/impl/neo4j_connector.py` L270-373

Neo4j 커넥터 파일 내에 5개의 `_dummy_*` 함수가 정의되어 있다. 다른 커넥터들은 `dummy_data.py`에서 더미 데이터를 가져오는 반면, Neo4j만 자체 파일에 포함하고 있어 일관성이 떨어진다.

**개선안**: Neo4j 더미 데이터도 `dummy_data.py`로 이동하여 패턴 통일.

---

### I-04. clarification_context.py -- 파일 위치 적절성

**파일**: `src/agents/utils/clarification_context.py`

이 모듈은 `PipelineState`의 `resolved_signals`를 프롬프트 문자열로 변환하는 순수 유틸리티이다. `src/agents/utils/` 위치는 적절하며, 코드 품질도 양호하다. turn_id 기반 필터링, ASK/INFER 분리 등 설계가 잘 되어 있다.

미미한 개선점: `build_clarification_context`와 `build_auto_resolved_notice`에서 `tid` 검증 및 INFER 필터링 로직이 중복된다. 내부 헬퍼로 추출 가능.

---

### I-05. 타입 힌트: `Any` 과다 사용

**파일**: 전체 커넥터 파일들

`self._client: Any`, `self._engine: Any`, `self._conn: Any`, `self._db: Any` 등 외부 라이브러리 객체에 `Any` 타입이 광범위하게 사용된다. lazy import 때문에 불가피한 면이 있으나, `TYPE_CHECKING` 블록을 활용하면 정적 타입 검사를 개선할 수 있다.

```python
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

class MongoConnector(SearchConnector):
    _client: AsyncIOMotorClient | None
    _db: AsyncIOMotorDatabase | None
```

---

### I-06. config.py에서 ES 관련 설정 주석에 `# 보고서 SQL 검색 (현재 미사용)` 표기

**파일**: `src/config.py` L226

`enabled_connectors`에서 elasticsearch가 주석 처리되어 있고, 주석에 미사용 명시가 있다. 하지만 ES 설정 필드 자체(L77-88)에는 미사용 주석이 없어 혼동 가능.

---

### I-07. reranker.py의 _prefilter 최소 보장 로직 버그

**파일**: `src/connectors/impl/reranker.py` L440-448

```python
if len(filtered) < min_count:
    filtered = sorted_cands[:max(min_count, len(filtered))]
```

`max(min_count, len(filtered))`에서 `len(filtered) < min_count`가 보장되므로 항상 `min_count`가 선택된다. 이 자체는 정확하지만, `len(sorted_cands) < min_count`인 경우에도 안전하게 동작한다. 코드의 의도가 `max` 없이 `sorted_cands[:min_count]`로 충분함을 나타내므로 단순화 가능.

---

### I-08. reranker.py의 위치 -- impl/ 내 비커넥터 모듈

**파일**: `src/connectors/impl/reranker.py`

Reranker는 외부 시스템에 대한 커넥터가 아니라 로컬 ML 모델을 래핑하는 서비스이다. `src/connectors/impl/`에 위치하는 것은 아키텍처 관점에서 어색하다.

**개선안**: `src/services/reranker.py` 또는 `src/ml/reranker.py`로 이동 검토. 다만 QdrantConnector와 강하게 결합되어 있어 현재 위치도 실용적으로 문제는 없다.

---

## 요약

| 등급 | 건수 | 핵심 키워드 |
|------|------|-------------|
| Critical (RED) | 4 | DB 비밀번호 URL 인젝션, HistoryDB 무제한 실행, Hive/Impala params 무시, sanitize_row 미적용 |
| Warning (YELLOW) | 10 | ES 잔존 코드, PostgreSQL/Hive-Impala 중복, 커넥션 풀 미적용, Neo4j 캐시 누수, Settings 비대화, connect_all 에러 전파 |
| Info (GREEN) | 8 | sanitize_row 위치, dummy_data 분리, 타입 힌트, reranker 위치 |

### 우선 조치 권장 순서

1. **C-01** DB 연결 문자열 보안 (URL.create + quote_plus) -- 즉시
2. **C-03** Hive/Impala params 바인딩 적용 -- 즉시
3. **C-04** sanitize_row 폐쇄망 커넥터 적용 -- 즉시
4. **C-02** HistoryDB SELECT 제한 또는 메서드 분리 -- 단기
5. **W-01** ES 잔존 코드 정리 -- 단기
6. **W-02, W-03** 커넥터 중복 코드 추출 -- 중기
7. **W-04** 폐쇄망 커넥터 커넥션 풀 -- 중기 (폐쇄망 배포 전)
8. **W-07** Settings 분리 -- 장기 (영향 범위 넓음)
