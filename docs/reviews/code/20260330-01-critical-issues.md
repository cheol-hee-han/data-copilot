# Critical 이슈 상세 리포트

- **검토 일시**: 2026-03-30
- **검토 대상**: `src/` 디렉토리 전체 (~80 파일)
- **검토 기준**: 중복 구현, 죽은 코드, 모듈 배치, 책임 혼재, 네이밍, 인터페이스 규약, 변경 전파, 가독성, 예외처리, 변수 관리 (11개 관점)

---

## 요약

전수 검토 결과 **Critical 등급 9건**을 식별하였다. 보안 취약점 4건, 런타임 오동작 3건, 아키텍처 위반 1건, 성능 1건이다.

| ID | 카테고리 | 위치 | 한줄 요약 |
|----|---------|------|----------|
| C-01 | 보안 | sql_safety_checker + input_sanitizer | 금지 패턴 이중 관리 — 보안 홀 |
| C-02 | 보안 | sql_safety_checker | MASKING_COLUMNS 로드만 하고 검증 안 함 |
| C-03 | 보안 | sql_executor vs sql_validator | validate_sql_safety 이중 구현, 시그니처 불일치 |
| C-04 | 보안 | hive/impala execute_query | params 파라미터 완전 무시 |
| C-05 | 보안 | config.py 비밀 필드 12개 | SecretStr 미사용, 평문 노출 위험 |
| C-06 | 정합성 | pipeline.py _handle_error | SQL_MAX_RETRY(2) vs MAX_GENERATES(4) 불일치 |
| C-07 | 성능 | runner.py run_pipeline | 매 요청마다 connect_all + create_app 반복 |
| C-08 | 아키텍처 | connectors/impl/reranker.py | ML 추론 서비스가 커넥터 패키지에 위치 |
| C-09 | 보안 | seed_sql_history.py | LIMIT/OFFSET f-string 삽입 |

---

## C-01. (보안) SQL 인젝션 금지 패턴 이중 관리

### 위치
- `src/services/sql_safety_checker.py` — `FORBIDDEN_PATTERNS` (17개 정규식)
- `src/services/input_sanitizer.py` — `_SUSPICIOUS_PATTERNS` (내부 패턴 목록)
- `src/utils/security.py` — `detect_prompt_injection` (또 다른 패턴 검사)

### 문제 상세

사용자 입력 검증(input_sanitizer)과 생성된 SQL 검증(sql_safety_checker)에서 **동일한 위협(DDL/DML, 시스템 카탈로그, SLEEP, xp_cmdshell, UNION SELECT 등)** 을 탐지하기 위한 정규식이 각 파일에 독립적으로 하드코딩되어 있다.

현재 두 파일의 패턴 목록을 비교하면:
- `sql_safety_checker.FORBIDDEN_PATTERNS`에는 있지만 `input_sanitizer`에는 없는 패턴이 존재
- 반대로 `input_sanitizer._SUSPICIOUS_PATTERNS`에만 있는 패턴도 존재
- 같은 위협을 탐지하는 정규식의 표현 방식이 서로 다름 (ex: `\bDROP\s+TABLE\b` vs `DROP\s+(TABLE|DATABASE)`)

**위험**: 패턴이 불일치하면 한쪽(입력 검증)은 통과시키고 다른 쪽(SQL 검증)은 차단하거나, 그 반대가 발생할 수 있다. 보안 규칙 유지보수 시 한 파일만 수정하고 다른 파일을 빠뜨리는 실수가 구조적으로 발생하기 쉽다.

### 해결 방안

**공통 패턴 레지스트리를 `src/utils/security.py`에 단일화한다.**

```python
# src/utils/security.py

# ── 공통 금지 패턴 (SSOT) ──────────────────────────
FORBIDDEN_SQL_PATTERNS: list[re.Pattern] = [
    re.compile(r"\b(INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|CREATE|GRANT|REVOKE)\b", re.I),
    re.compile(r"\bUNION\s+(ALL\s+)?SELECT\b", re.I),
    re.compile(r"\b(xp_|sp_)\w+", re.I),
    re.compile(r"\bWAITFOR\s+DELAY\b", re.I),
    re.compile(r"\bSLEEP\s*\(", re.I),
    re.compile(r"\b(information_schema|pg_catalog|sys\.)\b", re.I),
    # ... 전체 패턴 통합
]

def check_forbidden_patterns(text: str) -> list[str]:
    """텍스트에서 금지 패턴을 검사하여 위반 목록을 반환한다."""
    ...
```

- `input_sanitizer`는 `from src.utils.security import check_forbidden_patterns` 로 위임
- `sql_safety_checker`도 동일하게 위임
- 패턴 추가/수정은 `security.py` 한 곳에서만 수행

---

## C-02. (보안) MASKING_COLUMNS 로드만 하고 검증 미수행

### 위치
- `src/services/sql_safety_checker.py:133` — `MASKING_COLUMNS = ...` (YAML 로드)
- `src/services/sql_safety_checker.py:check_pii_columns()` — `PII_COLUMNS`만 검사

### 문제 상세

`pii_columns.yaml`에서 두 종류의 컬럼 목록을 로드한다:
- **PII_COLUMNS** (금지): 주민등록번호, 계좌번호, 카드번호 등 — 조회 자체가 금지
- **MASKING_COLUMNS** (마스킹 필수): 전화번호, 이메일, 생년월일, 주소 등 — 마스킹 처리 후 노출 가능

그런데 `check_pii_columns()` 함수는 `PII_COLUMNS`만 검사하고, `MASKING_COLUMNS`에 대해서는 **아무 검증도 하지 않는다**. 변수를 로드해 놓고 사용하지 않는 죽은 코드인 동시에, `.claude/rules/data-security.md`에 명시된 "필수 마스킹 컬럼" 요건이 코드에 구현되지 않은 상태이다.

### 해결 방안

`check_pii_columns()` 함수를 확장하여 마스킹 컬럼도 검증한다.

```python
def check_pii_columns(sql_upper: str) -> list[str]:
    errors = []
    # 1) 금지 컬럼 — 조회 자체 차단
    for col in PII_COLUMNS:
        if col.upper() in sql_upper:
            errors.append(f"금지된 개인정보 컬럼 '{col}'이 SQL에 포함되어 있습니다.")

    # 2) 마스킹 필수 컬럼 — 마스킹 함수 없이 직접 노출 시 경고
    for col in MASKING_COLUMNS:
        if col.upper() in sql_upper:
            # SUBSTR, LEFT, CONCAT('***') 등 마스킹 패턴이 적용되었는지 확인
            if not _has_masking_function(sql_upper, col):
                errors.append(
                    f"마스킹 필수 컬럼 '{col}'이 마스킹 없이 노출됩니다. "
                    f"SUBSTR/CONCAT 등으로 마스킹 처리가 필요합니다."
                )
    return errors
```

마스킹 패턴 감지가 복잡하다면, 최소한 **경고(warning) 레벨로 로깅**하여 운영 시 감사 추적이 가능하도록 한다.

---

## C-03. (보안) validate_sql_safety 이중 구현 — 시그니처 불일치

### 위치
- `src/agents/nodes/present/sql_executor.py:33` — `from src.utils.security import validate_sql_safety`
- `src/agents/nodes/reason/sql_validator.py:30` — `from src.services.sql_safety_checker import validate_sql_safety`

### 문제 상세

**동일한 이름의 함수가 두 모듈에 존재하며, 반환 타입이 다르다:**

| 위치 | import 경로 | 반환 타입 | 호출 패턴 |
|------|-----------|----------|----------|
| sql_executor.py | `utils.security` | `tuple[bool, list[str]]` | `is_safe, errors = validate_sql_safety(sql)` |
| sql_validator.py | `services.sql_safety_checker` | `SafetyCheckResult` 객체 | `result = validate_sql_safety(sql, dialect)` → `result.is_safe`, `result.errors` |

이는 파이프라인의 **이중 방어 (reason 단계 + present 단계)** 설계 자체는 올바르지만, 어느 쪽이 SSOT(Single Source of Truth)인지 불분명하다. 두 함수의 검증 수준이 다를 경우:
- `sql_validator`에서 통과한 SQL이 `sql_executor`에서 차단되거나
- `sql_executor`의 검증이 더 느슨하여 위험한 SQL이 실행될 수 있다

### 해결 방안

**`services/sql_safety_checker.py`를 SSOT로 지정하고, `utils/security.py`의 `validate_sql_safety`는 제거한다.**

```python
# src/agents/nodes/present/sql_executor.py
from src.services.sql_safety_checker import validate_sql_safety

# 호출부 수정
safety = validate_sql_safety(state.reason.validated_sql, dialect="postgresql")
if not safety.is_safe:
    # 에러 처리
    ...
```

`utils/security.py`에는 저수준 유틸(normalize_unicode, mask_pii, detect_prompt_injection)만 남기고, SQL 검증 책임은 `services/sql_safety_checker.py`에 일원화한다.

---

## C-04. (보안) Hive/Impala execute_query에서 params 완전 무시

### 위치
- `src/connectors/impl/hive_connector.py:124` — `cursor.execute(query)`
- `src/connectors/impl/impala_connector.py:124` — `cursor.execute(query)`

### 문제 상세

`DatabaseConnector` 인터페이스는 `execute_query(query, params)` 시그니처를 선언한다. Sybase 구현은 `params`를 `list(params.values())`로 변환하여 전달하고, PostgreSQL 구현은 `sqlalchemy.text(query)` + params dict를 사용한다.

그러나 **Hive와 Impala 구현에서는 `params` 인자를 완전히 무시**하고 `cursor.execute(query)` 만 호출한다. 이는:
1. 인터페이스 계약 위반
2. 파라미터 바인딩이 적용되지 않아 SQL 인젝션 방어 계층이 누락됨
3. 향후 다른 노드에서 params를 전달하더라도 무시되어 예상과 다른 결과 반환

### 해결 방안

```python
# hive_connector.py / impala_connector.py
def _execute() -> list[dict[str, Any]]:
    cursor = self._conn.cursor()
    if params:
        cursor.execute(query, params)  # HiveServer2/Impala 모두 파라미터 바인딩 지원
    else:
        cursor.execute(query)
    columns = [desc[0] for desc in cursor.description or []]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]
```

추가로, 현재 `tools.py`에서 f-string으로 조립하는 SQL(get_sample_rows, get_date_distribution)에 대해서도 식별자 화이트리스트 검증을 **공통 유틸 함수**로 추출하고, `limit` 파라미터는 `int(limit)`로 명시적 캐스팅한다.

---

## C-05. (보안) config.py 비밀 필드 12개에 SecretStr 미사용

### 위치
- `src/config.py` — `anthropic_api_key`, `openai_api_key`, `info_db_password`, `history_db_password`, `es_password`, `mongo_password`, `neo4j_password`, `redis_password`, `sybase_password`, `hive_password`, `impala_password`, `langsmith_api_key`

### 문제 상세

12개의 비밀번호/API 키 필드가 모두 `str` 타입으로 선언되어 있다.

```python
anthropic_api_key: str = ""
info_db_password: str = ""
```

이 상태에서:
- `print(settings)` 또는 `settings.model_dump()` 호출 시 **비밀번호가 평문으로 출력**됨
- structlog 등에서 settings 객체를 로깅하면 **로그 파일에 API 키가 기록**됨
- 에러 트레이스백에서 settings가 포함되면 **디버그 리포트에 비밀번호 노출**

금융 도메인에서 이는 보안 감사 시 지적 대상이 된다.

### 해결 방안

Pydantic의 `SecretStr`을 사용한다.

```python
from pydantic import SecretStr

class Settings(BaseSettings):
    anthropic_api_key: SecretStr = SecretStr("")
    info_db_password: SecretStr = SecretStr("")
    # ... 나머지 비밀 필드 동일
```

사용 시에는 `.get_secret_value()`로 실제 값을 꺼낸다:

```python
# 커넥터에서 사용 시
password = settings.info_db_password.get_secret_value()
```

`str(settings)` 호출 시 `**********`로 마스킹되며, `model_dump()` 시에도 SecretStr 객체로 반환되어 평문 노출이 방지된다.

**영향 범위**: 각 커넥터의 connect 메서드에서 `.get_secret_value()` 호출 추가 필요 (약 10곳).

---

## C-06. (정합성) _handle_error의 SQL_MAX_RETRY vs MAX_GENERATES 불일치

### 위치
- `src/agents/graph/pipeline.py:291-293` — `_handle_error` 함수
- `src/agents/graph/pipeline.py:220-240` — `_route_after_sql_validator` 함수

### 문제 상세

파이프라인의 SQL 재시도 상한을 판정하는 상수가 **두 곳에서 서로 다른 값을 참조**한다:

| 함수 | 참조 상수 | 값 | 역할 |
|------|----------|-----|------|
| `_route_after_sql_validator` | `MAX_GENERATES` | 4 (settings.max_generates) | SQL 검증 실패 시 재시도 여부 판정 |
| `_handle_error` | `SQL_MAX_RETRY` | 2 (settings.sql_max_retry) | 에러 메시지 분기 판정 |

**시나리오**: `generate_attempts`가 3인 상태에서 `error_end`로 라우팅되면:
- `_route_after_sql_validator`는 아직 `MAX_GENERATES(4)` 미만이므로 재시도 가능으로 판단
- `_handle_error`는 `SQL_MAX_RETRY(2)` 이상이므로 `ERR_SQL_RETRY_EXHAUSTED` 메시지 출력

이 불일치로 인해 **사용자에게 잘못된 에러 메시지가 노출**되거나, 재시도 가능한 상태인데 "재시도 횟수 초과" 메시지가 표시될 수 있다.

### 해결 방안

`_handle_error`에서도 `MAX_GENERATES`를 사용하도록 통일한다:

```python
def _handle_error(state: PipelineState) -> dict:
    reason = state.reason
    if reason and reason.loop_guard.generate_attempts >= MAX_GENERATES:
        return {"formatted_response": ERR_SQL_RETRY_EXHAUSTED}
    # ...
```

또는 `sql_max_retry`와 `max_generates`의 역할을 명확히 분리하고, `_handle_error`가 참조해야 할 상수가 어느 것인지 docstring에 명시한다.

---

## C-07. (아키텍처) 매 요청마다 그래프 재빌드 — LangGraph 프로덕션 패턴 미준수

### 위치
- `src/agents/graph/runner.py:166` — `app = create_app(tracker=tracker)`
- `src/agents/graph/pipeline.py:310-317` — `build_pipeline(tracker)` + `tracker.track(name)(fn)`

> **Note**: `connect_all()`은 `_connected` 플래그로 **이미 멱등 처리**되어 있어 문제 없음 (`manager.py:82-84`).

### 문제 상세

`run_pipeline()`이 **매 요청마다** `create_app(tracker=tracker)`를 호출하여 LangGraph `StateGraph`를 재빌드+재컴파일한다. 이는 tracker 인스턴스가 `tracker.track(name)(fn)` 데코레이터를 통해 노드 함수의 클로저에 캡처되기 때문이다.

LangGraph 공식 입장 (GitHub Discussion #1211):
> "No state is ever stored on the graph instance, and the graph instance isn't ever mutated in any way during any execution."

즉 **컴파일된 그래프는 불변 객체**이며, 프로덕션에서는 모듈 로드 시 1회 컴파일 후 전체 앱 생애에 걸쳐 재사용하는 것이 표준 패턴이다.

현재 구조의 문제:
1. `tracker.track(name)(fn)` — tracker 인스턴스가 요청별로 다르므로 그래프가 tracker에 종속
2. LangGraph의 **표준 트레이싱 방식은 `config={"callbacks": [handler]}`** 를 `ainvoke()` 시 주입하는 것
3. 커스텀 데코레이터로 노드를 감싸는 방식은 LangGraph의 콜백 시스템과 충돌하며, 노드 함수의 원본 시그니처를 숨김

### 해결 방안

**LangGraph 프로덕션 표준 패턴을 따른다: 그래프는 싱글턴, 트레이싱은 config callbacks.**

**1단계: 그래프를 모듈 레벨 싱글턴으로 변경**

```python
# pipeline.py — tracker 의존성 제거
def build_pipeline() -> StateGraph:
    """순수 그래프 구조만 빌드한다 (tracker 없음)."""
    workflow = StateGraph(PipelineState)
    workflow.add_node("preprocess", preprocess_node)      # 원본 함수 그대로
    workflow.add_node("resolve_history", resolve_history_node)
    workflow.add_node("classify_intent", classify_intent_node)
    # ... 나머지 노드 동일
    return workflow

# 모듈 로드 시 1회 컴파일
graph = build_pipeline().compile()
```

**2단계: EvaluationTracker를 `BaseCallbackHandler`로 전환**

```python
# tracker/callback_handler.py
from langchain_core.callbacks import BaseCallbackHandler

class EvaluationCallbackHandler(BaseCallbackHandler):
    """LangGraph 표준 콜백 기반 트레이서. 폐쇄망 호환."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        self._node_starts: dict[str, float] = {}

    def on_chain_start(self, serialized, inputs, *, run_id, metadata=None, **kw):
        node_name = (metadata or {}).get("langgraph_node", "")
        if node_name:
            self._node_starts[node_name] = time.perf_counter()

    def on_chain_end(self, outputs, *, run_id, **kw):
        # 노드별 실행 시간, 결과 기록
        ...

    def on_llm_start(self, serialized, prompts, *, run_id, **kw):
        # LLM 호출 기록 (프롬프트, 모델, 타이밍)
        ...

    def on_llm_end(self, response, *, run_id, **kw):
        # LLM 응답 기록 (토큰 수, 지연 시간)
        ...
```

**3단계: runner에서 config로 주입**

```python
# runner.py
from src.agents.graph.pipeline import graph

async def run_pipeline(user_input, ..., tracker=None, ...):
    handler = EvaluationCallbackHandler(run_id=session_id)

    result = await graph.ainvoke(
        initial_state,
        config={
            "callbacks": [handler],
            "run_id": uuid.uuid4(),
            "metadata": {"session_id": session_id},
        },
    )
```

**효과**:
- 그래프 빌드+컴파일: 앱 시작 시 **1회만** 수행
- 요청별 트레이싱: `config` 파라미터로 격리 주입 — 그래프 재빌드 불필요
- LangGraph 표준 패턴 준수 — LangSmith/Langfuse 등 외부 트레이서와도 호환
- `langgraph.json`의 `"graphs": {"data_copilot": "./pipeline.py:graph"}` 형식으로 LangGraph Server 배포 가능

---

## C-08. (아키텍처) Reranker가 connectors/impl에 위치

### 위치
- `src/connectors/impl/reranker.py` — BGE-Reranker-v2-m3 Cross-Encoder 구현

### 문제 상세

`connectors/impl/` 패키지는 **외부 시스템 연결 구현체**가 위치하는 곳이다. 모든 파일이 `BaseConnector`/`SearchConnector`/`DatabaseConnector` 인터페이스를 구현하며, `ConnectorManager`에 의해 생명주기(connect/disconnect/health_check)가 관리된다.

그런데 `reranker.py`는:
- `BaseConnector` 인터페이스를 **구현하지 않음**
- `ConnectorManager`에서 **관리하지 않음**
- `connect()/disconnect()/health_check()` 메서드가 **없음**
- ML 모델 추론(ONNX/PyTorch)을 수행하는 **서비스 레이어 성격**

유일한 소비자는 `qdrant_connector.py`에서 `from src.connectors.impl.reranker import get_reranker`로 참조하는 1곳이다.

### 해결 방안

`src/services/reranker.py`로 이동한다.

```bash
# 파일 이동
git mv src/connectors/impl/reranker.py src/services/reranker.py

# import 경로 수정 (1곳)
# src/connectors/impl/qdrant_connector.py
# 변경 전: from src.connectors.impl.reranker import get_reranker, RerankCandidate
# 변경 후: from src.services.reranker import get_reranker, RerankCandidate
```

영향 범위: import 경로 1곳 수정.

---

## C-09. (보안) seed_sql_history.py에서 LIMIT/OFFSET f-string 삽입

### 위치
- `src/tools/seed_sql_history.py:200-203`

### 문제 상세

```python
query += f"\nLIMIT {self._limit}"
query += f"\nOFFSET {self._offset}"
```

`self._limit`과 `self._offset`은 CLI 인자에서 온 `int`이므로 직접적인 SQL 인젝션 위험은 낮지만, 프로젝트 보안 규칙 "SQL은 반드시 파라미터 바인딩 사용"에 위배된다. 개발 도구이더라도 보안 원칙을 일관되게 적용해야 팀 전체의 보안 습관이 유지된다.

추가로 같은 파일에서:
- `__import__("sqlalchemy").text(query)` — 가독성이 매우 떨어지는 지연 임포트
- `await seeder._verify_storage()` — private 메서드 외부 호출

### 해결 방안

```python
from sqlalchemy import text

# 파라미터 바인딩 사용
base_query = text(query + "\nLIMIT :limit OFFSET :offset")
result = await session.execute(base_query, {"limit": self._limit, "offset": self._offset})
```

`__import__` 패턴은 메서드 상단의 일반 import로 교체하고, `_verify_storage()`는 `verify()` public 메서드로 래핑한다.
