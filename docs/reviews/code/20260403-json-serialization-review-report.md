# JSON 직렬화(Decimal/date/datetime) 변경 코드 리뷰

- **일시**: 2026-04-03
- **대상 파일**:
  - `src/connectors/interfaces.py` (sanitize_row, _to_json_safe 추가)
  - `src/connectors/impl/postgres_connector.py` (sanitize_row 호출 추가)
  - `src/models/result.py` (SQLResult field_validator 추가)
- **리뷰 관점**: 타입 안전성, 엣지 케이스, 성능, Pydantic v2 사용법, 일관성

---

## Critical (RED)

### C-01. Decimal('NaN'), Decimal('Inf') 처리 누락

**파일**: `src/connectors/interfaces.py:89-92`

```python
if isinstance(value, Decimal):
    if value == value.to_integral_value():
        return int(value)
    return float(value)
```

`Decimal('NaN') == Decimal('NaN').to_integral_value()`는 `False`를 반환하므로 `float(value)` 경로로 빠지며, 결과는 `float('nan')`이 된다. `Decimal('Inf')`도 마찬가지로 `float('inf')`가 된다. 이 값들은 JSON 표준(`RFC 8259`)에서 유효하지 않으며, `json.dumps()` 시 `ValueError`가 발생한다.

**해결 방안**:
```python
def _to_json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Decimal):
        if value.is_nan() or value.is_infinite():
            return None  # 또는 str(value) 등 정책에 따라
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    ...
```

---

### C-02. 다른 DatabaseConnector 구현체에 sanitize_row 미적용

**파일**: `src/connectors/impl/hive_connector.py`, `impala_connector.py`, `sybase_connector.py`

`sanitize_row`가 `postgres_connector.py`에만 적용되어 있다. Hive, Impala, Sybase 커넥터의 `execute_query`에서는 적용하지 않고 있어, 폐쇄망 배포 시 동일한 직렬화 오류가 재현된다. `interfaces.py`의 docstring에 "모든 DatabaseConnector 구현체는 이 함수를 적용해야 한다"고 명시했으나 실제로 강제되지 않는다.

**해결 방안 (2가지 중 택 1)**:

A) 각 커넥터 구현체에 `sanitize_row` 호출 추가 (단순하지만 누락 위험 존속)

B) `DatabaseConnector` 인터페이스에 Template Method 패턴 적용:
```python
class DatabaseConnector(BaseConnector):
    async def execute_query(
        self, query: str, params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        rows = await self._execute_query_impl(query, params)
        return [sanitize_row(row) for row in rows]

    @abstractmethod
    async def _execute_query_impl(
        self, query: str, params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        ...
```

방안 B가 구조적으로 누락을 원천 차단하므로 권장한다. 다만 변경 범위가 모든 커넥터 구현체에 걸치므로 별도 작업으로 진행하는 것이 안전하다.

---

## Warning (YELLOW)

### W-01. SQLResult.ensure_json_serializable에서 첫 번째 행만 샘플링하는 최적화의 함정

**파일**: `src/models/result.py:46-47`

```python
first = v[0]
if not any(isinstance(val, Decimal) for val in first.values()):
    return v
```

첫 번째 행에 Decimal이 없으면 전체 변환을 건너뛴다. 그러나 SQL 결과에서 특정 컬럼이 첫 행에서만 NULL이고 나머지 행에서 Decimal인 경우, 변환이 누락된다. 예: `SELECT amount FROM t` 결과에서 첫 행의 amount가 `None`이고 두 번째 행부터 `Decimal('100.50')`인 경우.

**해결 방안**: 이 validator는 방어 계층이므로 false-negative가 있어도 치명적이지 않다(커넥터 계층에서 1차 변환). 다만 주석으로 이 제약을 명시하거나, 안전하게 가려면 첫 행 검사 최적화를 제거하는 것이 좋다:

```python
@field_validator("rows", mode="before")
@classmethod
def ensure_json_serializable(
    cls, v: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """rows 내 Decimal 값을 int/float로 변환한다.

    커넥터에서 이미 sanitize_row를 적용하지만,
    테스트/캐시 복원 등 커넥터를 거치지 않는 경로를 방어한다.
    Note: 첫 행 샘플링으로 Decimal 존재를 판별하므로,
    첫 행에만 NULL이 있는 희귀 케이스에서는 변환이 누락될 수 있다.
    """
    ...
```

---

### W-02. field_validator에서 date/datetime 미처리

**파일**: `src/models/result.py:33-61`

`interfaces.py`의 `_to_json_safe`는 Decimal, datetime, date를 모두 처리한다. 그러나 `SQLResult.ensure_json_serializable`은 Decimal만 변환한다. 방어 계층이라면 동일한 범위를 커버해야 일관성이 있다.

**해결 방안**: `_to_json_safe` 함수를 `interfaces.py`에서 public으로 노출하고 validator에서 재사용:

```python
from src.connectors.interfaces import sanitize_row

@field_validator("rows", mode="before")
@classmethod
def ensure_json_serializable(cls, v: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not v:
        return v
    return [sanitize_row(row) for row in v]
```

이렇게 하면 변환 로직 중복도 제거된다. 다만, `src/models`에서 `src/connectors`를 import하는 것이 의존성 방향에 맞는지 검토가 필요하다. 맞지 않는다면 `_to_json_safe`를 `src/utils/` 아래 공용 모듈로 추출하는 것이 적절하다.

---

### W-03. to_integral_value() 비교 시 소수점 이하 0 처리

**파일**: `src/connectors/interfaces.py:90`

```python
if value == value.to_integral_value():
```

`Decimal('100.00') == Decimal('100.00').to_integral_value()`는 `True`이므로 `int(100)`으로 변환된다. 이것이 의도된 동작인지 확인이 필요하다. 금융 도메인에서는 `100.00`(원 단위 금액)과 `100`(건수)의 구분이 중요할 수 있다. DB 컬럼 타입이 `NUMERIC(18,2)`인 금액 컬럼의 값 `100.00`이 `int(100)`으로 변환되면 프론트엔드에서 소수점 표시가 불가능해진다.

**해결 방안**: 금액 컬럼의 경우 항상 float로 변환하는 것이 안전할 수 있다. 현재 단계에서는 이 동작을 문서화하고, 프론트엔드에서 컬럼 메타(통화 여부)에 따라 포맷팅하는 전략을 검토:

```python
# 대안: 항상 float 변환 (금융 도메인에서 더 안전)
if isinstance(value, Decimal):
    if value.is_nan() or value.is_infinite():
        return None
    return int(value) if value == value.to_integral_value() else float(value)
```

현재 로직은 유지하되, 이 동작을 docstring에 명시할 것을 권장한다.

---

### W-04. sanitize_row의 위치가 인터페이스 모듈에 부적절

**파일**: `src/connectors/interfaces.py:69-97`

`interfaces.py`는 ABC 정의 모듈이다. 유틸리티 함수(`sanitize_row`, `_to_json_safe`)는 인터페이스 계약이 아니라 구현 헬퍼이므로 이 모듈에 두는 것은 책임 분리 원칙에 맞지 않는다.

**해결 방안**: `src/connectors/utils.py` 또는 `src/utils/type_coerce.py` 등으로 분리. 단, 현재 규모에서는 과도한 분리일 수 있으므로, 파일이 더 커질 경우 분리하는 것으로 충분하다.

---

## Info (GREEN)

### I-01. isinstance 순서: datetime -> date 올바름

**파일**: `src/connectors/interfaces.py:93-96`

```python
if isinstance(value, datetime):
    return value.isoformat()
if isinstance(value, date):
    return value.isoformat()
```

`datetime`은 `date`의 서브클래스이므로 `datetime`을 먼저 체크하는 것이 올바르다. 순서가 바뀌면 `datetime` 객체가 `date.isoformat()`으로 처리되어 시간 정보가 손실된다. 현재 구현이 정확하다.

---

### I-02. Pydantic v2 field_validator 사용법 정확

**파일**: `src/models/result.py:33-34`

```python
@field_validator("rows", mode="before")
@classmethod
```

Pydantic v2에서 `field_validator`에 `mode="before"`와 `@classmethod` 데코레이터를 함께 사용하는 것은 올바른 패턴이다. `mode="before"`는 Pydantic 타입 변환 전에 실행되므로 raw 데이터를 조작하기에 적합하다.

---

### I-03. 성능 오버헤드 (10,000건 기준)

`sanitize_row`는 dict comprehension + isinstance 체크로 구성되어 있으며, 10,000건 x 20 컬럼 기준으로 200,000회 isinstance 호출이 발생한다. Python의 isinstance는 C 레벨에서 실행되므로 이 규모에서의 오버헤드는 무시할 수 있다 (약 10-20ms 수준). DB 쿼리 자체의 네트워크 I/O 대비 미미하다.

다만, `SQLResult.ensure_json_serializable`에서 동일한 변환이 중복 실행될 수 있으므로, 커넥터 계층에서 이미 변환된 데이터가 validator를 다시 통과할 때 불필요한 순회가 발생한다. 첫 행 검사 최적화(W-01)가 이를 어느 정도 완화한다.

---

### I-04. 빈 리스트/None 엣지 케이스 처리 양호

`sanitize_row`는 빈 dict가 들어와도 문제없이 빈 dict를 반환한다. `ensure_json_serializable`은 `if not v: return v`로 빈 리스트를 조기 반환한다. 양호하다.

---

## 조치 요약

| 등급 | ID | 내용 | 우선순위 |
|------|----|------|----------|
| RED | C-01 | Decimal NaN/Inf 처리 추가 | 즉시 |
| RED | C-02 | Hive/Impala/Sybase 커넥터에 sanitize_row 적용 | 즉시 |
| YELLOW | W-01 | 첫 행 샘플링 제약 주석 추가 또는 제거 | 단기 |
| YELLOW | W-02 | validator에서 date/datetime도 처리 또는 sanitize_row 재사용 | 단기 |
| YELLOW | W-03 | Decimal 정수 변환 동작 docstring 명시 | 단기 |
| YELLOW | W-04 | sanitize_row 위치 검토 (현 규모에서는 유지 가능) | 장기 |

---

## 테스트 커버리지 확인

`sanitize_row`, `_to_json_safe`, `ensure_json_serializable`에 대한 단위 테스트가 현재 존재하지 않는다. 다음 테스트 케이스를 추가할 것을 권장한다:

```python
# tests/unit/connectors/test_sanitize_row.py

import pytest
from datetime import date, datetime
from decimal import Decimal
from src.connectors.interfaces import sanitize_row, _to_json_safe

class TestToJsonSafe:
    def test_none(self):
        assert _to_json_safe(None) is None

    def test_decimal_integer(self):
        assert _to_json_safe(Decimal("100")) == 100
        assert isinstance(_to_json_safe(Decimal("100")), int)

    def test_decimal_float(self):
        assert _to_json_safe(Decimal("100.50")) == 100.5
        assert isinstance(_to_json_safe(Decimal("100.50")), float)

    def test_decimal_zero_fraction(self):
        # W-03: 100.00 -> int(100)인지 확인
        assert _to_json_safe(Decimal("100.00")) == 100
        assert isinstance(_to_json_safe(Decimal("100.00")), int)

    def test_decimal_nan(self):
        # C-01: 현재 float('nan') 반환 -> None 반환으로 수정 후 테스트
        result = _to_json_safe(Decimal("NaN"))
        assert result is None

    def test_decimal_inf(self):
        result = _to_json_safe(Decimal("Inf"))
        assert result is None

    def test_datetime(self):
        dt = datetime(2026, 4, 3, 14, 30, 0)
        assert _to_json_safe(dt) == "2026-04-03T14:30:00"

    def test_date(self):
        d = date(2026, 4, 3)
        assert _to_json_safe(d) == "2026-04-03"

    def test_datetime_not_treated_as_date(self):
        dt = datetime(2026, 4, 3, 14, 30, 0)
        # datetime이 date보다 먼저 체크되어 시간 정보 포함됨을 검증
        assert "T" in _to_json_safe(dt)

    def test_passthrough_str(self):
        assert _to_json_safe("hello") == "hello"

    def test_passthrough_int(self):
        assert _to_json_safe(42) == 42

class TestSanitizeRow:
    def test_empty_row(self):
        assert sanitize_row({}) == {}

    def test_mixed_types(self):
        row = {"a": Decimal("10"), "b": "text", "c": None, "d": date(2026, 1, 1)}
        result = sanitize_row(row)
        assert result == {"a": 10, "b": "text", "c": None, "d": "2026-01-01"}
```
