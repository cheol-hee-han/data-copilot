# Present 계층 코드 리뷰 보고서

- **일자**: 2026-04-06
- **범위**: Present 노드(analyzer, formatter, sql_executor, simple_responder), 서비스(visualization, session, cancel_store, turn_text_store, session_service), 모델(response, user_messages, result, trace, session_models)
- **리뷰어**: Code Reviewer Agent

---

## 요약

Present 계층은 전반적으로 구조가 잘 설계되어 있다. 노드-서비스 위임 패턴이 일관되고, 에러 폴백이 충실하며, SQL 이중 방어가 적용되어 있다. 그러나 보안(PII 마스킹 미적용, 로그 내 원본 데이터 노출), 성능(Redis 비원자적 연산), 일관성(로거 패턴 혼재) 등에서 개선이 필요하다.

---

## 재검증 결과 (2026-04-06 2차 검토)

아래 이슈들은 실제 코드와 대조 검증한 결과, 오탐 또는 심각도 조정이 필요한 것으로 확인되었습니다.

| 원래 ID | 판정 | 사유 |
|---------|------|------|
| **CR-03** | **유지 (데드코드)** | `es_` 접두어 잔존은 사실이나, `format_context_warning()` 자체가 프로젝트 전체에서 **호출 0건**인 데드코드. 함수 자체 삭제 권장 |
| **WR-08** | **오탐 (제거)** | `trace_summary`는 `format_trace_summary()` (trace.py L53-65)가 생성하며, 데이터 원본은 모두 노드 내부 하드코딩 문자열. `preprocessed_input` 등 사용자 입력이 TraceEntry에 주입되는 경로 없음. XSS 벡터 아님 |
| **CR-01 (PII 로그)** | ⏸️ **미적용 (B-1)** | `rows[0]` 로그 마스킹은 당분간 현행 유지로 결정 |
| **WR-03 (redis_store)** | ⏸️ **Redis 활성화 시 적용 (B-2)** | 현재 `session_backend=memory` 기본값이므로 런타임 영향 없음 |

---

## Critical (RED) Issues

### CR-01. sql_executor: SQL 실행 결과 트래킹 로그에 원본 데이터 노출

**파일**: `src/agents/nodes/present/sql_executor.py` (L104-118)

```python
await dispatch_tracking_event(CONTEXT_SQL_EXECUTED, {
    ...
    "results_summary": [
        ...
        *(
            [f"샘플: {rows[0]}"]
            if rows
            else []
        ),
    ],
})
```

`rows[0]`은 SQL 실행 결과의 첫 행 전체를 문자열로 직렬화한다. 이 행에 고객명, 계좌번호, 전화번호 등 PII가 포함될 수 있으며, `data-security.md`의 "로그에 개인정보 포함 금지 (마스킹 후 로깅)" 규칙을 위반한다.

**개선안**: `mask_pii(str(rows[0]))`를 적용하거나, 컬럼명만 로깅하고 샘플 행 자체를 제거한다. 후자가 더 안전하다.

```python
# 안전한 방안: 샘플 행 대신 컬럼명만 기록
"results_summary": [
    f"컬럼: {', '.join(columns)}",
    f"행 수: {result.row_count}건",
    f"소요: {round(elapsed, 1)}ms",
    f"절삭: {truncated}",
],
```

---

### CR-02. sql_executor: cancel 메시지에 생성된 SQL 노출

**파일**: `src/agents/nodes/present/sql_executor.py` (L51-54)

```python
cancel_msg = "요청이 중단되었습니다."
if state.reason.validated_sql:
    cancel_msg += (
        f" 생성된 SQL: {state.reason.validated_sql[:200]}"
    )
```

이 메시지는 `formatted_response`로 사용자에게 직접 전달된다. IT 비전문 은행 직원에게 SQL 원문을 노출하는 것은 `user-interaction.md`의 "기술 용어(SQL, JOIN, WHERE 등) 사용 최소화" 원칙과 `code-style.md`의 "사용자에게 노출되는 에러 메시지에 내부 정보 포함 금지" 원칙을 위반한다. 또한 SQL 내에 테이블명/컬럼명이 포함되어 내부 DB 스키마 정보가 유출될 수 있다.

**개선안**: cancel 메시지에서 SQL을 제거한다. SQL은 trace_log에만 기록한다.

```python
return {
    "formatted_response": "요청이 중단되었습니다.",
    "status": QueryStatus.CANCELLED,
    "trace_log": add_trace(
        state, "SQL실행",
        f"취소됨 (SQL 생성 완료 후 실행 전 중단)",
    ),
}
```

---

### CR-03. user_messages: CONTEXT_SOURCE_LABELS에 ES 레거시 참조 잔존

**파일**: `src/agents/models/user_messages.py` (L52-59)

```python
CONTEXT_SOURCE_LABELS: dict[str, str] = {
    "es_table_meta": "테이블 정보",
    "es_report_sql": "보고서 SQL",
    "es_code_meta": "코드 정보",
    ...
}
```

프로젝트 메모리에 따르면 ElasticSearch는 완전 제거되었고 메타는 MongoDB, SQL이력은 Qdrant로 이관되었다. 그러나 이 매핑은 여전히 `es_` 접두어 키를 사용하고 있다. 현재 소스에서는 `src/config.py`와 `elasticsearch_connector.py`에 여전히 ES 관련 코드가 존재하므로, 전체 ES 제거 작업의 일부로 함께 정리해야 한다.

**개선안**: ES 제거 작업 시 이 매핑도 함께 업데이트한다. 현재 실제 소스 키가 무엇인지 확인하여 매핑을 일치시킨다.

---

### CR-04. RedisSessionStore: get_history 호출 시 미연결 상태 방어 없음

**파일**: `src/services/session/redis_store.py` (L88-95)

```python
async def get_history(
    self, session_id: str,
) -> list[dict[str, str]]:
    key = self._key(session_id, "history")
    raw = await self._client.get(key)  # self._client가 None일 수 있음
```

`connect()`가 호출되지 않은 상태에서 `get_history`, `append_history`, `clear_session` 등이 호출되면 `self._client`가 `None`이므로 `AttributeError`가 발생한다. `health_check`만 None 체크가 있고, 나머지 메서드는 방어가 없다.

**개선안**: 모든 Redis 호출 메서드의 시작부에 클라이언트 체크를 추가하거나, `connect()`에서 lazy init 패턴을 적용한다.

```python
async def _ensure_client(self) -> None:
    if self._client is None:
        await self.connect()

async def get_history(self, session_id: str) -> list[dict[str, str]]:
    await self._ensure_client()
    ...
```

---

## Warning (YELLOW) Issues

### WR-01. RedisCancelStore.pop_cancel: 비원자적 GET+DELETE 경합 조건

**파일**: `src/services/cancel_store.py` (L80-87)

```python
async def pop_cancel(self, session_id: str) -> str | None:
    key = self._key(session_id)
    val = await self._client.get(key)
    if val is None:
        return None
    await self._client.delete(key)
    return val.decode() if isinstance(val, bytes) else val
```

GET과 DELETE 사이에 다른 워커가 동일 키를 읽을 수 있는 경합 조건이 존재한다. 운영 환경(multi-worker)에서 중복 취소 처리가 발생할 수 있다.

**개선안**: Redis의 `GETDEL` 명령(Redis 6.2+)을 사용하여 원자적으로 처리한다.

```python
async def pop_cancel(self, session_id: str) -> str | None:
    key = self._key(session_id)
    val = await self._client.getdel(key)
    if val is None:
        return None
    return val.decode() if isinstance(val, bytes) else val
```

Redis 6.2 미만 환경이라면 Lua 스크립트 또는 WATCH/MULTI/EXEC 파이프라인을 사용한다.

---

### WR-02. RedisSessionStore.append_history: read-modify-write 경합 조건

**파일**: `src/services/session/redis_store.py` (L97-109)

```python
async def append_history(self, session_id: str, entry: dict[str, str]) -> None:
    key = self._key(session_id, "history")
    history = await self.get_history(session_id)  # GET
    history.append(entry)
    if len(history) > self._max_history:
        history = history[-self._max_history:]
    await self._client.set(key, json.dumps(history, ...), ex=self._ttl)  # SET
```

GET과 SET 사이에 다른 워커가 동일 세션에 append하면 하나의 항목이 유실된다. 이 문제는 동시 접속이 드문 초기 단계에서는 발생 빈도가 낮지만, 운영 환경에서는 잠재적 데이터 손실을 유발한다.

**개선안**: Redis의 LIST 자료구조(`RPUSH` + `LTRIM`)를 활용하면 원자적 append가 가능하다.

```python
async def append_history(self, session_id: str, entry: dict[str, str]) -> None:
    key = self._key(session_id, "history")
    await self._client.rpush(key, json.dumps(entry, ensure_ascii=False))
    await self._client.ltrim(key, -self._max_history, -1)
    await self._client.expire(key, self._ttl)
```

---

### WR-03. turn_text_store: 로거 패턴 불일치

**파일**: `src/services/turn_text_store.py` (L23)

```python
import logging
logger = logging.getLogger(__name__)
```

프로젝트 전체에서 `src/utils/logger.py`의 `get_logger(__name__)`를 사용하는 것이 표준 패턴이다(analyzer.py, formatter.py, sql_executor.py, simple_responder.py, store.py, redis_store.py, memory_store.py, cancel.py 등 모두 동일). 이 파일만 `import logging` + `logging.getLogger`를 직접 사용하여 구조화 로깅(structured logging) 기능을 활용하지 못하고 있다.

**개선안**:

```python
from src.utils.logger import get_logger
logger = get_logger(__name__)
```

---

### WR-04. session_service: pool 타입이 Any로 선언

**파일**: `src/services/session_service.py` (L9, L24, L48 등)

모든 함수의 `pool` 파라미터가 `Any`로 타입 선언되어 있다. `pool`은 `psycopg_pool.AsyncConnectionPool`이어야 하며, `Any`는 정적 분석과 IDE 자동완성을 무력화한다.

**개선안**: 프로젝트 내 pool 타입을 별도 type alias로 정의하고 사용한다.

```python
from psycopg_pool import AsyncConnectionPool

# 또는 TYPE_CHECKING 블록에서 import
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

async def list_sessions(pool: AsyncConnectionPool, ...) -> SessionListResponse:
```

같은 이슈가 `turn_text_store.py`의 모든 함수에도 해당된다.

---

### WR-05. chart_generator: STACKED_BAR가 일반 BAR_CHART와 동일 렌더링

**파일**: `src/services/visualization/chart_generator.py` (L395-399)

```python
generators = {
    VisualizationType.BAR_CHART: generate_bar_chart,
    VisualizationType.STACKED_BAR: generate_bar_chart,  # BAR와 동일
    ...
}
```

STACKED_BAR 유형이 LLM 판단에서 선택될 수 있지만, 실제 렌더링은 일반 막대 차트와 동일하다. 사용자에게 혼란을 줄 수 있으며, LLM이 stacked bar를 선택하는 데이터(다중 시리즈)에 대해 부정확한 시각화를 제공하게 된다.

**개선안**: (1) STACKED_BAR를 VisualizationType에서 제거하고 LLM 판단 프롬프트에서도 배제하거나, (2) 실제 stacked bar 렌더링을 구현한다. 현 단계에서는 (1)이 적절하며, TODO 주석을 추가한다.

---

### WR-06. chart_generator: 모듈 임포트 시점에 외부 파일 I/O 실행

**파일**: `src/services/visualization/chart_generator.py` (L72)

```python
_COLORS, _FONT = _load_chart_config()  # 모듈 임포트 시점에 파일 I/O
```

모듈이 임포트되는 시점에 `resources/domain/chart_config.yaml` 파일을 읽는다. 파일이 없으면 기본값으로 폴백하여 치명적 오류는 아니지만, (1) 테스트에서 임포트만으로 파일 시스템 의존성이 발생하고, (2) 파일 읽기 오류 시 모듈 전체가 임포트 실패할 수 있다.

**개선안**: `functools.lru_cache`를 사용한 lazy loading으로 전환한다.

```python
import functools

@functools.lru_cache(maxsize=1)
def _get_chart_config() -> tuple[list[str], str]:
    return _load_chart_config()
```

---

### WR-07. sql_executor: validated_sql이 None인 경우 방어 부족

**파일**: `src/agents/nodes/present/sql_executor.py` (L62-67)

```python
logger.info(
    "SQL 실행 시작",
    sql="\n" + format_sql(state.reason.validated_sql or ""),
)

is_safe, safety_errors = check_sql_safety_quick(
    state.reason.validated_sql,  # None이면 TypeError
)
```

`state.reason.validated_sql`이 `None`인 경우 `check_sql_safety_quick`에 `None`이 전달되어 `normalize_unicode(None)`에서 TypeError가 발생한다. 이전 노드(sql_validator)에서 반드시 SQL이 설정되어야 하지만, 상태 변조 방어 관점에서 명시적 체크가 필요하다.

**개선안**:

```python
sql = state.reason.validated_sql
if not sql:
    logger.error("실행할 SQL이 없음")
    return {
        "sql_result": SQLResult(),
        "status": QueryStatus.ERROR,
        "error_message": format_error(ERR_SQL_EXECUTION),
    }
```

---

### WR-08. formatter: `<details>` HTML 태그의 XSS 벡터

**파일**: `src/agents/nodes/present/formatter.py` (L96-103)

```python
trace_summary = format_trace_summary(state)
if trace_summary:
    formatted += (
        "\n\n<details>\n"
        "<summary>조회 과정 요약</summary>\n\n"
        f"{trace_summary}\n"
        "</details>"
    )
```

`trace_summary`는 각 노드의 action/detail 값으로 구성되며, 사용자 입력(preprocessed_input)이 trace에 포함될 수 있다. HTML 태그가 이스케이핑 없이 삽입되므로 XSS 벡터가 될 수 있다. 프론트엔드(React)가 dangerouslySetInnerHTML을 사용하는 경우 취약하다.

**개선안**: `trace_summary`를 HTML 이스케이핑하거나, 마크다운으로 통일한다. 프론트엔드가 마크다운 렌더러를 사용한다면 `<details>` 대신 마크다운 접기를 사용한다.

---

## Info (GREEN) Issues

### IN-01. present/__init__.py가 빈 파일

**파일**: `src/agents/nodes/present/__init__.py`

패키지 초기화 파일이 빈 상태이다. 다른 노드 패키지(`__init__.py`)가 노드 함수를 re-export하는 패턴을 따르고 있다면 일관성을 위해 주요 노드 함수를 export하는 것이 좋다.

---

### IN-02. visualization/__init__.py의 docstring 최소화

**파일**: `src/services/visualization/__init__.py`

```python
"""시각화 서비스 모듈."""
```

모듈 내 핵심 구성요소(chart_generator)와 사용법에 대한 간략한 설명이 있으면 코드 탐색이 용이하다.

---

### IN-03. simple_responder: 키워드 매칭의 확장성

**파일**: `src/agents/nodes/present/simple_responder.py` (L94-100)

```python
def _match_casual_response(query: str) -> str:
    q = query.strip()
    for keyword, response in _CASUAL_RESPONSES.items():
        if keyword in q:
            return response
    return _CASUAL_DEFAULT
```

현재 5개 키워드로 충분하지만, "고마워", "ㄱㅅ", "감사합니다" 등 변형이 매칭되지 않는다. 향후 LLM 없이 다양한 변형을 커버하려면 정규식 기반이나 키워드 그룹 매핑으로 전환을 고려한다.

---

### IN-04. trace.py: add_trace와 format_trace_summary의 이중 위치

**파일**: `src/models/trace.py` (L39-65)

`add_trace`와 `format_trace_summary`가 `src/models/trace.py`에 정의되어 있지만, `formatter.py`에서는 `src/agents/state/state`에서 import하고 있다 (L38-39). `state.py`가 `trace.py`를 re-export하고 있는 것으로 보이며, 이는 혼동을 유발할 수 있다. import 경로를 통일하는 것이 좋다.

---

### IN-05. session_service: update_session_title의 반환 타입 불일치

**파일**: `src/services/session_service.py` (L147-153)

다른 함수들은 실패 시 `None`을 반환하는 패턴(`XxxResponse | None`)을 따르지만, `update_session_title`만 `bool`을 반환한다. 라우터에서 404 처리 시 패턴이 달라진다.

**개선안**: 일관성을 위해 다른 함수들과 동일한 `SessionTitleResponse | None` 패턴으로 통일하거나, 현재 bool 반환이 의도적이라면 docstring에 이유를 명시한다.

---

### IN-06. chart_generator: 파이 차트의 하드코딩된 좌표

**파일**: `src/services/visualization/chart_generator.py` (L298)

```python
cx, cy, r = 260, 210, 140
```

막대/꺾은선 차트는 `settings.chart_*`에서 레이아웃을 가져오지만, 파이 차트의 중심 좌표와 반지름은 하드코딩되어 있다. `settings.chart_width/height` 변경 시 파이 차트만 레이아웃이 깨진다.

**개선안**: `settings.chart_width/height` 기반으로 동적 계산한다.

```python
cx = w * 0.4
cy = margin_top + (h - margin_top - margin_bottom) / 2
r = min(cx - margin_left, cy - margin_top) * 0.7
```

---

### IN-07. cancel_store: MemoryCancelStore에 thread-safety 부재

**파일**: `src/services/cancel_store.py` (L25-46)

asyncio 환경에서는 GIL에 의해 대부분 안전하지만, `_flags` dict 접근이 `await` 경계에서 interleave될 수 있다. 개발/테스트 전용이므로 현재 실질적 위험은 낮으나, 운영 오용 방지를 위해 docstring에 명시되어 있어 적절하다.

---

### IN-08. result.py: Decimal 변환 로직의 첫 행만 검사하는 최적화

**파일**: `src/models/result.py` (L46-48)

```python
first = v[0]
if not any(isinstance(val, Decimal) for val in first.values()):
    return v
```

첫 행에 Decimal이 없지만 이후 행에 Decimal이 있는 경우(혼합 타입 컬럼) 변환이 누락된다. 실제 DB에서는 컬럼별 타입이 고정이므로 이 상황은 거의 발생하지 않지만, 캐시 복원 등 방어 목적의 validator임을 감안하면 주석으로 가정을 명시하는 것이 좋다.

---

## 아키텍처 관찰

### 세션 관리 이중 구조

현재 세션 데이터는 두 곳에서 관리된다:
1. **SessionStore** (session/store.py, memory_store.py, redis_store.py): 대화 이력 인메모리/Redis 저장
2. **turn_text_store**: PostgreSQL `checkpoint_dc_turn_texts` 테이블에 턴 텍스트 저장

두 저장소의 역할 구분은 명확하다 (SessionStore=실시간 LLM 맥락, turn_text_store=영구 감사/UI 복원). 다만, `session_service.py`가 `turn_text_store`만 사용하고 `SessionStore`는 `main.py`에서 직접 사용하는 구조이므로, 향후 `session_service`가 두 저장소를 통합 관리하는 facade로 발전시키는 것을 고려한다.

### Deprecated 메서드 정리

`SessionStore`의 `get_clarification`/`set_clarification`이 deprecated 경고와 함께 남아 있다. 호출처가 없다면 다음 버전에서 제거하여 인터페이스를 깔끔하게 유지한다.

---

## 개선 우선순위

| 순위 | ID | 난이도 | 설명 |
|------|------|--------|------|
| 1 | CR-01 | 낮음 | 트래킹 로그 PII 노출 제거 |
| 2 | CR-02 | 낮음 | cancel 메시지에서 SQL 제거 |
| 3 | CR-04 | 낮음 | Redis 미연결 상태 방어 |
| 4 | WR-01 | 중간 | pop_cancel 원자적 연산 |
| 5 | WR-02 | 중간 | append_history 경합 조건 해소 |
| 6 | WR-07 | 낮음 | validated_sql None 방어 |
| 7 | WR-03 | 낮음 | 로거 패턴 통일 |
| 8 | WR-04 | 낮음 | pool 타입 힌트 개선 |
| 9 | WR-08 | 중간 | HTML 이스케이핑 |
| 10 | CR-03 | 중간 | ES 레거시 참조 정리 (ES 제거 작업과 병행) |
