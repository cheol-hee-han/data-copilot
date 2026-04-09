# 리뷰 이슈 검증 리포트 (커넥터/새니타이저/명확화)

- **일시**: 2026-04-06
- **목적**: 기존 리뷰에서 제기된 8건의 이슈에 대한 실제 코드 기반 검증 (오탐 식별)
- **대상 파일**: clarification_handler.py, input_sanitizer.py, intent_classifier.py(노드/서비스), postgres/hive/impala/sybase/mongo_connector.py, manager.py

---

## 검증 결과 요약

| # | 이슈 ID | 판정 | 설명 |
|---|---------|------|------|
| 1 | INT-05 | 확인됨 (의도된 설계) | in-place mutation은 의도적이며 주석으로 명시됨 |
| 2 | LOG-03 | 부분 확인 (현재 버그 아님) | 호출부에서 falsy 가드 존재, 단 API 계약 불명확 |
| 3 | W-SEC-06 | 확인됨 (오탐 위험 존재) | `--` 패턴이 자연어에서 오탐 가능 |
| 4 | SEC-06 | 부분 확인 | PostgreSQL/MongoDB만 해당, Sybase/Hive/Impala는 해당 없음 |
| 5 | SEC-07 | 확인됨 (의도된 설계) | HistoryDBConnector는 이력 적재용으로 SELECT 제한 없음이 의도적 |
| 6 | SEC-08 | 확인됨 (실제 이슈) | Hive/Impala가 params를 완전히 무시함 |
| 7 | LOG-04 | 확인됨 (실제 이슈) | Hive/Impala/Sybase에서 sanitize_row() 미적용 |
| 8 | W-ERR-04 | 오탐 | enabled_connectors 기반 선택적 연결, 의도된 동작 |

---

## 상세 검증

### 1. INT-05: clarification_handler에서 AmbiguitySignal을 in-place mutation

**판정: 확인됨 -- 의도된 설계**

`src/agents/nodes/interpret/clarification_handler.py` L133~149:

```python
# NOTE: in-place mutation 패턴 (기존 가드레일과 동일).
#       AmbiguitySignal에 frozen=True 설정 시 이 코드가 깨지므로 주의.
for s in signals:
    s.turn_id = state.turn_id

# 1. 가드레일 적용 (인라인)
for s in signals:
    override = _should_override_to_ask(s, state)
    if override:
        s.decision = "ASK"
        s.override_reason = override
```

**검증 근거:**
- `AmbiguitySignal`은 Pydantic `BaseModel`이며 `frozen=True`가 설정되어 있지 않다 (`src/agents/models/clarification.py` L49).
- 코드에 NOTE 주석으로 이 패턴의 의도와 위험성(`frozen=True` 설정 시 깨짐)을 명시하고 있다.
- L155~156에서도 `s.resolved_at = datetime.now()`로 동일 패턴 사용.

**평가**: in-place mutation 자체는 의도적 설계이며 문서화되어 있다. 다만 `AmbiguitySignal`에 추후 `frozen=True`를 추가하면 런타임 에러가 발생하므로, 이 의존성은 유효한 경고 사항이다. 현 시점에서는 Warning 수준이지 버그는 아니다.

---

### 2. LOG-03: rewrite_analysis_query에서 LLM 빈 응답이 그대로 preprocessed_input에 설정

**판정: 부분 확인 -- 현재 코드에서 실제 버그는 발생하지 않음**

두 파일에 걸친 흐름을 추적해야 한다.

**1단계** - `src/services/intent_classifier.py` L313~316 (rewrite_analysis_query):

```python
result = (
    response.content[0].text.strip()
    if response.content else ""
)
```

`response.content`가 존재하지만 `text`가 빈 문자열인 경우, `result`는 `""`가 된다.

**2단계** - `src/agents/nodes/interpret/intent_classifier.py` L106~111 (_rewrite_for_analysis):

```python
extraction = await rewrite_analysis_query(
    original_input,
    system_prompt=INTENT_CLASSIFIER_QUERY_REWRITER,
)
if extraction:
    updates["preprocessed_input"] = extraction
```

`if extraction:` 조건에 의해 빈 문자열(`""`)은 falsy이므로 `preprocessed_input`에 설정되지 **않는다**.

**결론**: 호출부에서 falsy 가드가 있으므로 빈 문자열이 `preprocessed_input`에 설정되는 버그는 발생하지 않는다. 그러나 `rewrite_analysis_query` 서비스 함수가 빈 문자열을 정상 반환값처럼 반환하는 것은 API 계약상 불명확하다. 호출자가 항상 falsy 체크를 해야 하는 암묵적 규약에 의존한다.

---

### 3. W-SEC-06: input_sanitizer SQL 인젝션 패턴(--, /\*)이 자연어 입력에서 오탐

**판정: 확인됨 -- `--` 패턴에서 오탐 위험 존재**

`src/services/input_sanitizer.py` L42~43:

```python
(r"--", "SQL 단행 주석 패턴"),
(r"/\*", "SQL 블록 주석 패턴"),
```

**오탐 시나리오:**
- `--` : "2024년 1월--3월 실적", "대출금리--예금금리 비교" 등 범위/대비 표현에서 매칭
- 유니코드 NFKC 정규화(L73)가 선행되므로 전각 대시(`\uff0d\uff0d`)도 반각으로 변환된 후 매칭됨
- `/\*` : 자연어에서 사실상 거의 발생하지 않으므로 오탐 위험 낮음

**개선 제안**: `--` 패턴을 `--\s` 또는 세미콜론 뒤에 오는 `--`만 감지하도록 제한하는 것을 고려. 또는 `\b--\s`처럼 SQL 주석 특성(뒤에 공백 또는 문자열 끝)에 맞게 보강.

---

### 4. SEC-06: PostgreSQL, MongoDB, Sybase 커넥터에서 비밀번호가 f-string으로 URL에 직접 삽입

**판정: 부분 확인 -- PostgreSQL/MongoDB만 해당**

**PostgreSQL (확인됨)**

`src/connectors/impl/postgres_connector.py` L61~67 (InfoDBConnector):

```python
url = (
    f"postgresql+asyncpg://"
    f"{settings.info_db_user}"
    f":{settings.info_db_password}"
    f"@{settings.info_db_host}"
    f":{settings.info_db_port}"
    f"/{settings.info_db_name}"
)
```

L176~179 (HistoryDBConnector)에서도 동일 패턴. 비밀번호에 `@`, `:`, `/` 같은 특수문자가 포함되면 URL 파싱이 깨질 수 있다. SQLAlchemy는 `URL.create()` 메서드로 안전한 URL 생성을 제공한다.

**MongoDB (확인됨)**

`src/connectors/impl/mongo_connector.py` L145~149:

```python
connection_uri = (
    f"mongodb://{settings.mongo_user}:{settings.mongo_password}"
    f"@{settings.mongo_host}:{settings.mongo_port}"
    f"/{settings.mongo_database}?authSource=admin"
)
```

동일하게 비밀번호 특수문자 문제가 존재. `urllib.parse.quote_plus()`로 인코딩하거나 Motor의 구조화된 연결 옵션을 사용해야 한다.

**Sybase (해당 없음 -- 오탐)**

`src/connectors/impl/sybase_connector.py`에서는 f-string URL을 사용하지 않는다.
- native 연결 (L77~84): `sqlanydb.connect()`에 키워드 인자로 전달
- ODBC 연결 (L91~104): ODBC 연결 문자열(`KEY=VALUE;` 형식)으로 URL 파싱 문제 없음

**Hive/Impala (해당 없음)**

`impala.dbapi.connect()`에 키워드 인자로 전달하므로 URL 삽입 문제 없음.

---

### 5. SEC-07: HistoryDBConnector가 SELECT 제한 없이 임의 쿼리 실행 가능

**판정: 확인됨 -- 의도된 설계이나 방어 심화 가능**

`src/connectors/impl/postgres_connector.py` L212~217:

```python
async def execute_query(
    self,
    query: str,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """범용 쿼리를 실행한다 (SELECT 제한 없음, 이력 적재용)."""
```

docstring에 "SELECT 제한 없음, 이력 적재용"으로 명시되어 있다.

**검증 근거:**
- 이 커넥터는 SQL 수행 이력을 적재(INSERT)하는 용도이므로 SELECT 제한이 없는 것은 의도된 설계
- params 바인딩을 사용하고 있으므로 (L224: `text(query), params or {}`), SQL 인젝션 리스크는 파라미터 바인딩으로 완화
- InfoDBConnector(L109~114)에는 `^\s*(SELECT|WITH)\b` 정규식으로 SELECT 제한이 적용되어 역할이 분리됨

**개선 제안**: 허용 SQL 유형(SELECT/INSERT만 등)의 화이트리스트를 추가하면 방어 심화 가능. 단, 현재 설계 의도에 부합하므로 필수 사항은 아님.

---

### 6. SEC-08: Hive/Impala 커넥터가 params 파라미터를 완전히 무시

**판정: 확인됨 -- 실제 이슈**

**Hive** - `src/connectors/impl/hive_connector.py` L126~137:

```python
def _execute() -> list[dict[str, Any]]:
    cursor = self._conn.cursor()
    cursor.execute(query)   # <-- params 미사용
    columns = [desc[0] for desc in cursor.description]
    rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    cursor.close()
    return rows
```

`execute_query` 시그니처(L107~110)에 `params: dict[str, Any] | None = None`을 받지만, 내부 `_execute()`에서 `params`를 전혀 사용하지 않는다.

**Impala** - `src/connectors/impl/impala_connector.py` L124~135:

동일한 문제. `cursor.execute(query)`에 params를 전달하지 않음.

**대조: Sybase** - `src/connectors/impl/sybase_connector.py` L180~185:

```python
if params:
    cursor.execute(query, list(params.values()))
else:
    cursor.execute(query)
```

Sybase는 params를 사용하고 있다. 다만 `list(params.values())`로 변환하여 positional parameter로 전달하므로, dict 키 순서에 의존하는 잠재적 문제가 있다 (Python 3.7+ dict는 삽입 순서 보장이므로 실무상 문제는 낮음).

**대조: PostgreSQL** - `src/connectors/impl/postgres_connector.py` L129~131:

```python
result = await session.execute(text(query), params or {})
```

SQLAlchemy text()의 named parameter 바인딩을 사용하여 올바르게 처리.

---

### 7. LOG-04: Hive/Impala/Sybase 커넥터에서 sanitize_row() 미적용

**판정: 확인됨 -- 실제 이슈**

`src/connectors/interfaces.py` L74~82에 정의된 `sanitize_row()`:

```python
def sanitize_row(row: dict[str, Any]) -> dict[str, Any]:
    """DB row의 값을 JSON 직렬화 가능한 타입으로 정규화한다.
    ...
    모든 DatabaseConnector 구현체는 execute_query 결과를 반환하기 전에
    이 함수를 적용해야 한다.
    """
```

| 커넥터 | sanitize_row 적용 | import 여부 | 해당 라인 |
|--------|-------------------|-------------|-----------|
| InfoDBConnector | O (L134) | O (L26) | postgres_connector.py |
| HistoryDBConnector | O (L229) | O (L26) | postgres_connector.py |
| HiveConnector | **X** | **X** | hive_connector.py L132~136 |
| ImpalaConnector | **X** | **X** | impala_connector.py L130~134 |
| SybaseIQConnector | **X** | **X** | sybase_connector.py L189~193 |

interfaces.py docstring에 "모든 DatabaseConnector 구현체는 ... 이 함수를 적용해야 한다"고 명시되어 있으므로, 이는 명백한 누락이다. Hive/Impala/Sybase가 `Decimal`, `datetime` 등의 타입을 반환하면 하류에서 JSON 직렬화 에러가 발생할 수 있다.

---

### 8. W-ERR-04: connect_all()에서 한 커넥터 실패 시 전체 기동 중단

**판정: 오탐**

`src/connectors/manager.py` L135~160:

```python
async def connect_all(self) -> None:
    """활성 커넥터를 초기화한다 (멱등).

    settings.enabled_connectors에 포함된 커넥터만 connect를 수행한다.
    비활성 커넥터는 dummy 모드 인스턴스로 유지된다.
    """
    if self._connected:
        return
    enabled = settings.enabled_connectors
    logger.info("커넥터 초기화 시작", enabled=sorted(enabled))

    for cfg_name, attr in _CONNECTORS:
        if cfg_name in enabled:
            await getattr(self, attr).connect()
```

**검증 근거:**
- `settings.enabled_connectors`에 포함된 커넥터만 연결을 시도한다. 불필요한 커넥터는 아예 connect를 시도하지 않음.
- 한 커넥터가 실패하면 예외가 전파되어 전체 기동이 중단되는 것은 맞다.
- 그러나 이것은 **의도된 설계**로 판단:
  - `enabled_connectors`에 포함된 커넥터는 파이프라인 실행에 **필수적인 것만** 설정함
  - 필수 커넥터 연결 실패 시 불완전한 상태로 서비스를 시작하는 것보다 기동을 중단하는 것이 **안전함**
  - 선택적 커넥터는 `enabled_connectors`에서 제외하면 됨

만약 일부 커넥터 실패를 허용해야 한다면, `_CONNECTORS` 레지스트리에 `required` 플래그를 추가하여 필수/선택을 구분하는 패턴이 적절하다. 현재 설계가 "enabled = 필수" 의미론을 따르고 있으므로 현 동작은 합리적이다.

---

## 등급별 정리

### Critical -- 실제 수정 필요

| 이슈 | 파일 | 라인 | 설명 |
|------|------|------|------|
| SEC-08 | hive_connector.py, impala_connector.py | L128, L126 | params 파라미터 완전 무시 |
| LOG-04 | hive/impala/sybase_connector.py | execute_query 전체 | sanitize_row() 누락 |

### Warning -- 개선 권장

| 이슈 | 파일 | 라인 | 설명 |
|------|------|------|------|
| SEC-06 | postgres_connector.py, mongo_connector.py | L61-67, L145-149 | f-string URL에 비밀번호 직접 삽입 (특수문자 파싱 오류 위험) |
| W-SEC-06 | input_sanitizer.py | L42 | `--` 패턴 자연어 오탐 위험 |
| LOG-03 | intent_classifier.py (서비스) | L313-316 | rewrite_analysis_query API 계약 불명확 (빈 문자열 반환) |

### Info -- 참고 또는 의도된 설계

| 이슈 | 판정 | 설명 |
|------|------|------|
| INT-05 | 의도된 설계 | 주석으로 문서화된 in-place mutation, frozen=True 추가 시 주의 |
| SEC-07 | 의도된 설계 | HistoryDBConnector는 이력 적재용, 방어 심화는 선택적 |
| W-ERR-04 | 오탐 | enabled_connectors 기반 선택적 연결, 의도된 fail-fast 동작 |
