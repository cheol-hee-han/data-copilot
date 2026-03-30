# 중복 구현 상세 리포트

- **검토 일시**: 2026-03-30
- **검토 관점**: 의미가 유사한 기능이 여기저기 중복 구현되어 있는 코드

---

## 요약

| ID | 카테고리 | 위치 | 한줄 요약 |
|----|---------|------|----------|
| D-01 | 보안 | sql_safety_checker + input_sanitizer | 금지 패턴 이중 하드코딩 (**Critical C-01과 동일**) |
| D-02 | 유지보수성 | data_analyzer.py + response.py | JSON 코드펜스 추출 로직 중복 |
| D-03 | 유지보수성 | 4개 커넥터 | SELECT 검증 정규식 4곳 하드코딩 |
| D-04 | 유지보수성 | state.py + context.py | ColumnInfo / ColumnMeta 유사 모델 중복 |
| D-05 | 유지보수성 | hive/impala/sybase 커넥터 | health_check, execute_query, disconnect 100% 동일 코드 |
| D-06 | 가독성 | chart_generator.py | 3개 차트 함수에서 레이아웃 상수 반복 |
| D-07 | 유지보수성 | response_formatter.py | format_result_for_prompt 동일 데이터로 2회 호출 |

---

## D-01. (보안) SQL 인젝션 금지 패턴 이중 하드코딩

> Critical C-01과 동일 이슈. `20260330-01-critical-issues.md#C-01` 참조.

---

## D-02. (유지보수성) JSON 코드펜스 추출 로직 중복

### 위치
- `src/services/data_analyzer.py:72-98` — `parse_analysis_json()` 내부
- `src/utils/llm/response.py` — `extract_json()` 유틸 함수

### 문제 상세

`data_analyzer.py`의 `parse_analysis_json` 함수는 LLM 응답에서 JSON을 추출하기 위해 자체적으로 다음 로직을 인라인 구현하고 있다:

```python
# data_analyzer.py 내부 (자체 구현)
text = text.strip()
if text.startswith("```"):
    text = text.split("\n", 1)[1] if "\n" in text else text[3:]
if text.endswith("```"):
    text = text[:-3]
# ... JSON 파싱 + 필드별 디폴트 처리
```

반면 프로젝트에는 이미 `src/utils/llm/response.py`에 `extract_json()` 유틸이 존재하며, `query_normalizer.py`를 포함한 다른 서비스에서는 이 유틸을 사용하고 있다.

**위험**: LLM이 코드펜스 형식을 변경하거나 (예: ` ```json\n` vs ` ```\n`), 새로운 edge case가 발견되면 **두 곳을 동시에 수정해야 하며, 하나를 빠뜨리면 특정 노드에서만 파싱 실패가 발생**한다.

### 해결 방안

`data_analyzer.py`의 자체 코드펜스 파싱 로직을 제거하고 `extract_json`을 사용한다.

```python
# data_analyzer.py
from src.utils.llm.response import extract_json

def parse_analysis_json(text: str) -> AnalysisResult:
    """LLM 응답에서 분석 결과 JSON을 추출하여 AnalysisResult로 변환한다."""
    data = extract_json(text)  # 공통 유틸 사용

    # 필드별 디폴트 처리 (data_analyzer 고유 로직)
    return AnalysisResult(
        summary=data.get("summary", ""),
        key_findings=data.get("key_findings", []),
        statistics=data.get("statistics", {}),
    )
```

코드펜스 제거 + JSON 파싱은 `extract_json`에 위임하고, `AnalysisResult` 생성에 필요한 필드 매핑/디폴트 처리만 `data_analyzer`에 남긴다.

---

## D-03. (유지보수성) SELECT 검증 정규식 4곳 하드코딩

### 위치
- `src/connectors/impl/hive_connector.py:111`
- `src/connectors/impl/impala_connector.py:109`
- `src/connectors/impl/sybase_connector.py:164`
- `src/connectors/impl/postgres_connector.py:101` (InfoDBConnector)

### 문제 상세

4개 커넥터에서 동일한 정규식이 하드코딩되어 있다:

```python
if not re.match(r"^\s*(SELECT|WITH)\b", query, re.IGNORECASE):
    raise ValueError("SELECT 문만 실행할 수 있습니다")
```

향후 허용 패턴을 확장해야 하는 경우 (예: `EXPLAIN SELECT`, `VALUES` 구문 추가) **4개 파일을 동시에 수정해야 하며**, 하나를 빠뜨리면 특정 DB에서만 에러가 발생한다. 또한 `HistoryDBConnector`에는 이 검증이 없어 의도적 생략인지 버그인지 불분명하다.

### 해결 방안

공통 검증 함수를 `src/utils/security.py`에 추출한다.

```python
# src/utils/security.py
_READONLY_SQL_RE = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)

def validate_readonly_query(query: str) -> None:
    """읽기 전용 SQL인지 검증한다. SELECT/WITH만 허용."""
    if not _READONLY_SQL_RE.match(query):
        raise ValueError("SELECT 문만 실행할 수 있습니다")
```

각 커넥터에서는 이 함수를 호출:

```python
# hive_connector.py, impala_connector.py, sybase_connector.py, postgres_connector.py
from src.utils.security import validate_readonly_query

async def execute_query(self, query, params=None):
    validate_readonly_query(query)
    # ...
```

`HistoryDBConnector`에 대해서는 INSERT가 필요한 경우 docstring에 "이력 저장용이므로 SELECT 제한 없음"을 명시하고, 그렇지 않으면 동일한 검증을 추가한다.

---

## D-04. (유지보수성) ColumnInfo / ColumnMeta 유사 모델 중복

### 위치
- `src/agents/state/state.py:136-144` — `ColumnInfo`
- `src/models/context.py:18-24` — `ColumnMeta`

### 문제 상세

두 Pydantic 모델이 **거의 동일한 역할**(컬럼 이름, 설명, 타입을 표현)을 수행한다:

| 필드 | ColumnInfo (state.py) | ColumnMeta (context.py) |
|------|----------------------|------------------------|
| name | O | O |
| description | O | O |
| data_type | O | O |
| alt_name | O | X |
| is_pk | O | X |
| is_pii | X | O |

**위험**: 새로운 컬럼 속성(예: `is_nullable`, `default_value`)을 추가할 때 **어느 모델에 추가해야 하는지 판단이 어렵고**, 양쪽에 추가하면 동기화 부담이 생긴다. 변환 로직도 분산된다.

### 해결 방안

**방안 A (권장)**: `ColumnMeta`를 기본 모델로 통일하고, 추가 필드는 확장으로 처리

```python
# src/models/context.py — 기본 컬럼 모델 (SSOT)
class ColumnMeta(BaseModel):
    name: str
    description: str = ""
    data_type: str = ""
    alt_name: str = ""
    is_pk: bool = False
    is_pii: bool = False
```

`state.py`의 `ColumnInfo`는 제거하고, `CandidateTable.columns`의 타입을 `list[ColumnMeta]`로 변경한다.

**방안 B**: 공통 필드를 베이스 모델로 추출

```python
class BaseColumnMeta(BaseModel):
    name: str
    description: str = ""
    data_type: str = ""

class ColumnMeta(BaseColumnMeta):  # context.py용
    is_pii: bool = False

class ColumnInfo(BaseColumnMeta):  # state.py용
    alt_name: str = ""
    is_pk: bool = False
```

방안 A가 필드 수가 적어 더 간결하다.

---

## D-05. (유지보수성) Hive/Impala/Sybase 커넥터 공통 코드 대규모 중복

### 위치
- `src/connectors/impl/hive_connector.py`
- `src/connectors/impl/impala_connector.py`
- `src/connectors/impl/sybase_connector.py`

### 문제 상세

세 파일의 다음 메서드가 **사실상 100% 동일**하다:

| 메서드 | 동일 패턴 | 차이점 |
|--------|----------|--------|
| `health_check()` | dummy 체크 → `_ping()` 내부 함수 → `asyncio.to_thread` | 없음 (3파일 완전 동일) |
| `execute_query()` | SELECT 정규식 검증 → dummy 분기 → `_execute()` 내부 함수 → 타이밍 로깅 | Sybase의 params 처리 1줄 |
| `disconnect()` | `asyncio.to_thread(self._conn.close)` | 없음 |

또한 `postgres_connector.py`의 `InfoDBConnector`와 `HistoryDBConnector`도 거의 동일한 구조를 반복한다.

**위험**: 공통 로직(예: 타이밍 로깅 형식, SELECT 검증, dummy 분기)을 수정할 때 **3~5개 파일을 동시에 수정해야** 한다.

### 해결 방안

`BaseSyncDatabaseConnector` 추상 클래스를 도입하여 공통 패턴을 통합한다.

```python
# src/connectors/impl/_base_sync.py
class BaseSyncDatabaseConnector(DatabaseConnector):
    """동기 DB 드라이버를 asyncio.to_thread로 래핑하는 공통 베이스."""

    def __init__(self, use_dummy: bool = False):
        self._conn = None
        self._use_dummy = use_dummy

    async def health_check(self) -> bool:
        if self._use_dummy:
            return True
        def _ping():
            cursor = self._conn.cursor()
            cursor.execute(self._ping_query)
            return True
        return await asyncio.to_thread(_ping)

    async def execute_query(self, query: str, params=None) -> list[dict]:
        validate_readonly_query(query)
        if self._use_dummy:
            return self._get_dummy_data(query)

        def _execute():
            cursor = self._conn.cursor()
            cursor.execute(query, self._adapt_params(params))
            columns = [desc[0] for desc in cursor.description or []]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

        start = time.perf_counter()
        result = await asyncio.to_thread(_execute)
        logger.info("쿼리 실행 완료", elapsed=time.perf_counter() - start)
        return result

    async def disconnect(self):
        if self._conn:
            await asyncio.to_thread(self._conn.close)

    # 서브클래스가 오버라이드할 추상 메서드
    @abstractmethod
    def _create_connection(self): ...

    @abstractmethod
    def _adapt_params(self, params) -> Any: ...

    @property
    def _ping_query(self) -> str:
        return "SELECT 1"
```

각 구현체는 `_create_connection()`과 `_adapt_params()` 정도만 오버라이드한다:

```python
# hive_connector.py
class HiveConnector(BaseSyncDatabaseConnector):
    def _create_connection(self):
        from pyhive import hive
        return hive.connect(host=..., port=...)

    def _adapt_params(self, params):
        return params  # HiveServer2는 dict 파라미터 지원
```

**효과**: 공통 로직 수정 시 `_base_sync.py` 1곳만 변경. 현재 ~300줄(3파일) → ~100줄(베이스 1곳) + 각 구현체 ~50줄.

---

## D-06. (가독성) 차트 함수 3개에서 레이아웃 상수 반복

### 위치
- `src/services/visualization/chart_generator.py:113-120` (bar)
- `src/services/visualization/chart_generator.py:208-215` (line)
- `src/services/visualization/chart_generator.py:296-298` (pie)

### 문제 상세

`generate_bar_chart`, `generate_line_chart`, `generate_pie_chart` 3개 함수에서 동일한 레이아웃 상수를 반복적으로 참조한다:

```python
# bar_chart, line_chart 에서 동일하게 반복
w = settings.chart_width
h = settings.chart_height
margin_top = settings.chart_margin_top
margin_right = settings.chart_margin_right
margin_bottom = settings.chart_margin_bottom
margin_left = settings.chart_margin_left
plot_w = w - margin_left - margin_right
plot_h = h - margin_top - margin_bottom
```

### 해결 방안

`_ChartLayout` 데이터클래스로 추출한다:

```python
@dataclass(frozen=True)
class _ChartLayout:
    w: int
    h: int
    margin_top: int
    margin_right: int
    margin_bottom: int
    margin_left: int

    @property
    def plot_w(self) -> int:
        return self.w - self.margin_left - self.margin_right

    @property
    def plot_h(self) -> int:
        return self.h - self.margin_top - self.margin_bottom

    @classmethod
    def from_settings(cls) -> "_ChartLayout":
        return cls(
            w=settings.chart_width, h=settings.chart_height,
            margin_top=settings.chart_margin_top, ...
        )
```

각 차트 함수는 `layout = _ChartLayout.from_settings()` 한 줄로 레이아웃을 받는다.

---

## D-07. (가독성) format_result_for_prompt 동일 데이터로 2회 호출

### 위치
- `src/services/response_formatter.py:111` — `user_message` 생성용
- `src/services/response_formatter.py:127` — `record_prompt_variables`용

### 문제 상세

```python
# 111줄: LLM 호출용
user_message = user_template.format(
    user_input=user_input,
    data_summary=format_result_for_prompt(sql_result),  # 1차 호출
)

# ... LLM 호출 ...

# 127줄: 트래커 기록용
record_prompt_variables(
    data_summary=format_result_for_prompt(sql_result),  # 2차 호출 (동일 결과)
)
```

`format_result_for_prompt`는 순수 함수이므로 결과가 동일하지만, 대용량 결과셋인 경우 **마크다운 테이블 변환이 2번** 수행된다.

### 해결 방안

```python
data_summary = format_result_for_prompt(sql_result)  # 1회만 호출

user_message = user_template.format(
    user_input=user_input,
    data_summary=data_summary,
)
# ... LLM 호출 ...
record_prompt_variables(data_summary=data_summary)  # 변수 재사용
```
