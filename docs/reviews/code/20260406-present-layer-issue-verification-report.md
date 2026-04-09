# Present 계층 및 서비스 이슈 검증 보고서

- 작성일: 2026-04-06
- 대상 파일: sql_executor.py, formatter.py, analyzer.py, user_messages.py, redis_store.py, turn_text_store.py, chart_generator.py
- 목적: 기존 리뷰 이슈 7건의 실제 코드 대비 검증 (오탐 식별)

---

## 검증 결과 요약

| 이슈 ID | 판정 | 요약 |
|---------|------|------|
| SEC-04 | **진양성 (True Positive)** | rows[0] 원본 데이터가 트래킹 이벤트에 마스킹 없이 기록 |
| SEC-05 | **진양성 (True Positive)** | cancel 시 SQL 원문 200자가 사용자 응답에 직접 노출 |
| SEC-11 | **오탐 (False Positive)** | trace_summary는 내부 생성 데이터이며 XSS 공격 벡터 아님 |
| LOG-05 | **진양성 (True Positive)** | es_ 접두어 소스 키 잔존 (단, 현재 미호출 데드코드) |
| INT-06 | **진양성 (True Positive)** | connect() 미호출 시 self._client None으로 AttributeError 발생 |
| INT-03 | **진양성 (조건부)** | autocommit=True 환경에서 동시 INSERT 시 중복 가능하나 발생 확률 극히 낮음 |
| W-PRF-03 | **진양성 (True Positive)** | 모듈 임포트 시점에 YAML I/O 실행 |

---

## 1. SEC-04: sql_executor.py 트래킹 로그에 rows[0] 원본 데이터 마스킹 없이 기록

**판정: 진양성 (확인됨)**

### 검증 결과

- **sql_executor.py L104-120**:
  ```python
  await dispatch_tracking_event(CONTEXT_SQL_EXECUTED, {
      ...
      "results_summary": [
          ...
          *(
              [f"샘플: {rows[0]}"]  # L114: rows[0] 전체가 문자열로 변환
              if rows
              else []
          ),
      ],
      ...
  })
  ```

### 분석

- L114에서 `rows[0]` dict 전체를 f-string으로 트래킹 이벤트에 포함한다.
- SQL 결과에 고객명, 전화번호, 계좌번호 등 PII가 포함될 수 있다.
- 트래킹 이벤트는 `adispatch_custom_event`를 통해 `DataCopilotCallbackHandler`로 전달되어 로그나 외부 시스템에 기록될 수 있다.
- `data-security.md`의 "로그에 개인정보 포함 금지 (마스킹 후 로깅)" 규칙에 위반된다.

### 결론

**Critical**. 금융 PII가 로그/추적 시스템에 평문으로 유출될 수 있음. `rows[0]`의 키(컬럼명) 목록만 기록하거나, 값을 마스킹 유틸리티를 통해 처리한 후 기록해야 한다.

---

## 2. SEC-05: cancel 시 사용자에게 SQL 원문이 formatted_response로 직접 노출

**판정: 진양성 (확인됨)**

### 검증 결과

- **sql_executor.py L47-58**:
  ```python
  if await check_cancel(state.session_id, state.turn_id):
      cancel_msg = "요청이 중단되었습니다."
      if state.reason.validated_sql:
          cancel_msg += (
              f" 생성된 SQL: {state.reason.validated_sql[:200]}"  # L53
          )
      return {
          "formatted_response": cancel_msg,  # 사용자에게 직접 전달
          ...
      }
  ```

### 분석

- `formatted_response`는 최종적으로 사용자 UI에 표시되는 필드이다.
- cancel 시 SQL 원문 최대 200자가 "생성된 SQL: SELECT ..." 형태로 사용자에게 직접 노출된다.
- `user-interaction.md`의 "SQL 자체는 보여주지 않거나 접기(fold) 처리" 규칙 위반.
- SQL에 포함된 테이블명, 컬럼명, 조건값은 내부 스키마 정보 노출에 해당한다.

### 결론

**Warning**. 사용자 인터랙션 규칙 위반 + 내부 스키마 정보 노출. cancel 메시지에서 SQL 원문을 제거하고 "요청이 중단되었습니다."만 반환하거나, 디버그 정보는 trace_log에만 기록해야 한다.

---

## 3. SEC-11: formatter.py의 details 태그에 trace_summary가 HTML 이스케이핑 없이 삽입 -- XSS

**판정: 오탐 (False Positive)**

### 검증 결과

- **formatter.py L96-103**:
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

- **src/models/trace.py L53-65** (`format_trace_summary` 구현):
  ```python
  def format_trace_summary(state: PipelineState) -> str:
      if not state.trace_log:
          return ""
      lines: list[str] = []
      for i, entry in enumerate(state.trace_log, 1):
          if entry.detail:
              lines.append(f"{i}. {entry.action}: {entry.detail}")
          else:
              lines.append(f"{i}. {entry.action}")
      return "\n".join(lines)
  ```

### 오탐 이유

1. `trace_summary`는 `format_trace_summary()` 함수가 `state.trace_log`의 `TraceEntry.action`과 `TraceEntry.detail`을 조합하여 생성한다.
2. `TraceEntry`는 각 파이프라인 노드 내부에서 `add_trace(state, "SQL실행", "쿼리 실행 완료 (5건, 123.4ms)")` 형태로 생성된다. 노드 이름과 동작 요약은 **서버 측 코드에서 하드코딩된 한국어 문자열**이다.
3. **사용자 입력이 TraceEntry의 내용에 직접 삽입되는 경로가 없다.** `preprocessed_input`이 trace에 삽입되는 코드 패턴도 존재하지 않는다.
4. 따라서 `trace_summary`에 `<script>` 등 악성 HTML이 들어갈 공격 벡터가 없다.
5. 추가로, React 프론트엔드는 기본적으로 `dangerouslySetInnerHTML`을 명시적으로 사용하지 않는 한 HTML을 이스케이핑한다.

### 결론

**오탐**. 방어적 코딩 관점에서 이스케이핑을 추가하는 것은 좋은 관행이나, 현재 구조에서는 실질적인 XSS 위험이 없다.

---

## 4. LOG-05: user_messages.py에 es_ 접두어 소스 키 잔존

**판정: 진양성 (확인됨, 단 데드코드)**

### 검증 결과

- **user_messages.py L52-59**:
  ```python
  CONTEXT_SOURCE_LABELS: dict[str, str] = {
      "es_table_meta": "테이블 정보",       # es_ 접두어
      "es_report_sql": "보고서 SQL",        # es_ 접두어
      "es_code_meta": "코드 정보",          # es_ 접두어
      "history_db_sql": "과거 SQL 이력",
      "qdrant_manual": "업무 매뉴얼",
      "qdrant_sql_history": "SQL 수행이력",
  }
  ```

### 분석

1. `es_` 접두어는 ElasticSearch 시절의 네이밍이다. 현재 테이블/컬럼 메타는 MongoDB, SQL 이력은 Qdrant로 이관 완료 상태이다.
2. 그러나 `format_context_warning()` 함수 자체가 프로젝트 전체에서 **한 번도 호출되지 않는다** (grep 결과 0건). `CONTEXT_SOURCE_LABELS` 딕셔너리도 사용처가 없다.
3. 즉, es_ 접두어가 잘못된 것은 맞지만, 이 코드 자체가 **데드코드**이므로 실제 동작에 영향은 없다.
4. 참고: `src/config.py` L82-87에도 `es_table_meta_index`, `es_report_sql_index` 등 ES 관련 설정이 잔존하며, `src/connectors/impl/elasticsearch_connector.py`도 여전히 존재한다.

### 결론

**Info**. 데드코드 정리 대상. `format_context_warning` + `CONTEXT_SOURCE_LABELS`를 함께 제거하거나, 실제 컨텍스트 수집 실패 경고 기능을 구현할 때 올바른 소스 키(`mongo_table_meta` 등)로 갱신해야 한다. ES 관련 잔존 코드의 전체적인 정리와 함께 수행하는 것이 바람직하다.

---

## 5. INT-06: redis_store.py connect() 미호출 시 self._client None이어서 AttributeError

**판정: 진양성 (확인됨)**

### 검증 결과

- **redis_store.py L45-47** (초기화):
  ```python
  def __init__(self) -> None:
      self._client = None          # L46: 초기값 None
      ...
  ```

- **redis_store.py L88-95** (데이터 접근):
  ```python
  async def get_history(self, session_id: str) -> list[dict[str, str]]:
      key = self._key(session_id, "history")
      raw = await self._client.get(key)  # L92: _client이 None이면 AttributeError
      ...
  ```

- **redis_store.py L97-110** (데이터 쓰기):
  ```python
  async def append_history(self, session_id: str, entry: dict[str, str]) -> None:
      ...
      await self._client.set(...)  # L106: 동일 문제
  ```

### 분석

1. `__init__`에서 `self._client = None`으로 초기화 (L46).
2. `connect()` (L54-70)를 호출해야 `self._client`에 Redis 클라이언트가 할당된다.
3. `get_history` (L92), `append_history` (L106), `clear_session` (L117) 등 모든 데이터 접근 메서드가 `self._client`를 **None 체크 없이** 직접 사용한다.
4. `health_check`만 `if not self._client: return False` 방어 로직이 있다 (L84).
5. `connect()`가 미호출 상태에서 다른 메서드 호출 시 `AttributeError: 'NoneType' object has no attribute 'get'`이 발생한다.

### 결론

**Warning**. 라이프사이클 관리가 호출자에 위임되어 있어 실제 운영에서는 lifespan에서 connect()를 호출할 가능성이 높다. 그러나 방어 코딩이 누락된 것은 사실. 각 메서드 진입부에 `_ensure_connected()` 가드를 추가하거나, 연결 미완료 시 명시적인 `RuntimeError("Redis not connected. Call connect() first.")`를 발생시키는 것이 바람직하다.

---

## 6. INT-03: turn_text_store.py MAX(turn_seq)+1 서브쿼리 채번이 autocommit에서 동시 INSERT 시 중복

**판정: 진양성 (조건부 -- 이론적 위험, 실제 발생 확률 극히 낮음)**

### 검증 결과

- **turn_text_store.py L95-119** (INSERT 쿼리):
  ```python
  row = await conn.execute(
      """
      INSERT INTO checkpoint_dc_turn_texts (
          thread_id, turn_seq, ...
      ) VALUES (
          %(thread_id)s,
          COALESCE(
              (SELECT MAX(turn_seq) + 1
               FROM checkpoint_dc_turn_texts
               WHERE thread_id = %(thread_id)s),
              1
          ),
          ...
      )
      RETURNING turn_id::text
      """,
      turn,
  )
  ```

- **checkpointer.py L61** (pool 생성 시 autocommit 설정):
  ```python
  connection_kwargs = {
      "autocommit": True,       # 필수: psycopg3 기본값 False
      ...
  }
  ```

### 분석

1. checkpointer.py L61에서 pool이 `autocommit=True`로 생성된다. turn_text_store는 이 pool을 공유한다 (checkpointer.py L51 주석 확인).
2. `INSERT ... SELECT MAX(turn_seq)+1` 패턴은 단일 SQL문이지만, PostgreSQL READ COMMITTED 격리 수준에서 **두 개의 INSERT가 동시에 같은 thread_id로 실행**되면 둘 다 동일한 MAX 값을 읽고 같은 turn_seq를 채번할 수 있다.
3. 단, 현재 아키텍처에서 동일 thread_id에 대해 동시 INSERT가 발생하는 시나리오는 매우 제한적이다:
   - `runner.py`에서 `save_turn`은 파이프라인 실행 완료 후 순차적으로 user_turn, assistant_turn을 저장 (L228-282)
   - 같은 세션에서 동시 요청은 UI 레벨에서 차단됨
4. 그러나 명확화 응답과 일반 응답이 거의 동시에 완료되는 엣지 케이스나, `_pending_turns` flush가 겹치는 경우에서 이론적으로 발생 가능하다.

### 결론

**Warning (조건부)**. 이론적 위험은 실재하나 현재 사용 패턴에서는 발생 확률이 매우 낮다. 근본적 해결을 원하면 `(thread_id, turn_seq)` UNIQUE 제약조건 + `ON CONFLICT` 재시도, 또는 SEQUENCE 사용을 권장한다.

---

## 7. W-PRF-03: chart_generator.py가 모듈 임포트 시점에 YAML 파일 I/O 실행

**판정: 진양성 (확인됨)**

### 검증 결과

- **chart_generator.py L72** (모듈 레벨 실행):
  ```python
  _COLORS, _FONT = _load_chart_config()  # import 시점에 실행
  ```

- **chart_generator.py L45-69** (`_load_chart_config` 구현):
  ```python
  def _load_chart_config() -> tuple[list[str], str]:
      from src.utils.resource_loader import load_yaml
      data = load_yaml("domain/chart_config.yaml", None)
      if data is None:
          return _DEFAULT_COLORS, _DEFAULT_FONT
      ...
  ```

- **src/utils/resource_loader.py L182-197** (`load_yaml` 구현):
  ```python
  def load_yaml(name: str, default: Any) -> Any:
      path = _resolve(name)
      if not path.is_file():
          return default
      data = yaml.safe_load(path.read_text(encoding="utf-8"))
      ...
  ```

### 분석

1. `_load_chart_config()`은 `load_yaml()`을 호출하여 파일 시스템에서 YAML을 읽는다.
2. 이 호출은 모듈 레벨 변수 할당(L72)으로 인해 **`import` 시점에 실행**된다.
3. 파일 미존재 시 기본값이 사용되므로 import 실패는 발생하지 않는다.
4. 그러나 이 패턴은 다음 문제를 유발한다:
   - **테스트 환경**에서 chart_generator를 import만 해도 파일 I/O가 발생
   - import 순서에 따라 **settings가 완전히 초기화되기 전에** 호출될 수 있음
   - YAML 파일에 문법 오류가 있으면 **import 시점에 예외** 발생

### 결론

**Info**. 기능적 문제는 아니나 `@functools.lru_cache` 래핑된 지연 초기화 패턴으로 전환하는 것이 바람직하다. 예:
```python
@functools.lru_cache(maxsize=1)
def _get_chart_config() -> tuple[list[str], str]:
    return _load_chart_config()
```

---

## 종합 평가

| 등급 | 건수 | 이슈 |
|------|------|------|
| 진양성 (확인) | 5건 | SEC-04, SEC-05, LOG-05, INT-06, W-PRF-03 |
| 진양성 (조건부) | 1건 | INT-03 |
| 오탐 (False Positive) | 1건 | SEC-11 |

**오탐률: 1/7 (14.3%)**

SEC-11(XSS)은 trace_summary의 데이터 원본이 모두 서버 측 하드코딩 문자열이며 사용자 입력이 주입되는 경로가 없으므로 실질적 위험이 없다. 나머지 6건은 모두 실제 코드에서 확인되는 유효한 이슈이다.
