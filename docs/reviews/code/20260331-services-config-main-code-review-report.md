# Services / Config / Main 코드 리뷰 보고서

- **작성일**: 2026-03-31
- **대상**: `src/services/`, `src/config.py`, `src/main.py`
- **중점 사항**: 책임 분리, 중복 코드, 의존성 관리, 가독성, 죽은 코드, 계층/라이프사이클, 테스트 용이성

---

## 요약

전반적으로 서비스 계층의 설계가 잘 되어 있으며, 프롬프트 주입 패턴과 세션 추상화 등은 좋은 아키텍처 판단이다. 그러나 몇 가지 책임 분리 위반, 중복 패턴, 보안 우려, 타입 안전성 부재가 식별되었다.

| 등급 | 건수 |
|------|------|
| Critical | 4 |
| Warning | 10 |
| Info | 8 |

---

## Critical (반드시 수정)

### C-01. `main.py` 모듈 레벨 SQL 결과 캐시가 다중 워커에서 동작하지 않음

- **파일**: `src/main.py:462-489`
- **문제**: `_sql_result_cache`가 모듈 레벨 `dict`로 구현되어 있다. `uvicorn --workers 4`로 실행하면 워커 간 메모리가 공유되지 않아, 한 워커에서 조회한 결과를 다른 워커에서 다운로드할 수 없다. 또한 FIFO 제거 시 `next(iter(dict))`를 사용하는데, Python 3.7+에서 dict 삽입 순서가 보장되긴 하지만 동시 요청 시 race condition이 발생할 수 있다.
- **개선안**:
  1. 세션 스토어(Redis)를 활용하여 캐시를 관리하거나, 별도의 공유 캐시 서비스로 분리
  2. 최소한 `asyncio.Lock`으로 동시 접근을 보호
  3. 이 캐시 로직을 `main.py`에서 분리하여 별도 서비스 모듈로 이동 (책임 분리)

### C-02. `response_formatter.py` - LLM 호출에 에러 처리 없음

- **파일**: `src/services/response_formatter.py:118-126`
- **문제**: `format_response()`에서 `client.messages.create()` 호출에 `try/except`가 없다. 네트워크 오류, API 타임아웃, rate limit 등에서 예외가 그대로 상위로 전파된다. 같은 서비스 계층의 `data_analyzer.py`는 적절한 에러 처리를 하고 있어 일관성이 없다.
- **개선안**: `try/except`로 감싸고, 에러 시 사용자 친화적 폴백 응답을 반환하거나, `llm_call_with_parse_retry`를 사용하여 재시도 로직 통합

### C-03. `response_formatter.py` - `format_result_for_prompt`를 2회 호출하는 비효율

- **파일**: `src/services/response_formatter.py:110-132`
- **문제**: `format_response()` 내에서 `format_result_for_prompt(sql_result)`가 112번째 줄(LLM 요청용)과 128번째 줄(`record_prompt_variables`용)에서 **2번 호출**된다. 동일한 데이터에 대해 같은 마크다운 테이블 변환을 반복 수행하므로 불필요한 CPU 낭비이다.
- **개선안**:
  ```python
  result_text = format_result_for_prompt(sql_result)
  user_message = user_template.format(
      user_input=user_input,
      query_result=result_text,
  )
  # ... LLM 호출 ...
  await record_prompt_variables({
      "user_input": user_input,
      "query_result": truncate_log(result_text),
  })
  ```

### C-04. `redis_store.py` - `self._client` None 체크 없이 호출

- **파일**: `src/services/session/redis_store.py:85-104`
- **문제**: `get_history()`, `append_history()`, `clear_session()` 등에서 `self._client`가 `None`인지 확인하지 않고 바로 사용한다. `connect()`가 호출되기 전에 이 메서드들이 호출되면 `AttributeError: 'NoneType' object has no attribute 'get'`이 발생한다.
- **개선안**: 각 메서드 진입 시 `if not self._client: raise RuntimeError("Redis 미연결")` 가드 추가, 또는 `connect()`를 `__aenter__`에서 보장하는 컨텍스트 매니저 패턴 도입

---

## Warning (개선 권장)

### W-01. `main.py` 파일이 565줄로 책임이 과도하게 집중됨

- **파일**: `src/main.py`
- **문제**: FastAPI 앱 정의, lifespan 관리, WebSocket 핸들러, REST 엔드포인트, 슬래시 명령어 처리, SQL 결과 캐시, 다운로드 로직, 내장 HTML 폴백까지 하나의 파일에 모여 있다. CLAUDE.md의 "한 함수는 한 가지 작업만 수행" 원칙에 비추어 파일 수준에서 책임 분리가 필요하다.
- **개선안**:
  - `src/api/routes.py` — REST/WebSocket 엔드포인트
  - `src/api/websocket_handler.py` — WebSocket 전용 로직
  - `src/api/download.py` — 다운로드 캐시 및 엔드포인트
  - `src/main.py` — FastAPI 앱 생성, lifespan, 라우터 등록만 유지

### W-02. `input_sanitizer.py`와 `sql_safety_checker.py` 간 금지 패턴 중복

- **파일**: `src/services/input_sanitizer.py:38-50`, `src/services/sql_safety_checker.py:32-80`
- **문제**: SQL 인젝션 감지 패턴이 두 모듈에 유사하게 중복 정의되어 있다. 예를 들어 `SLEEP`, `WAITFOR`, `PG_SLEEP`, `LOAD_FILE`, `UNION SELECT`, 시스템 카탈로그 패턴 등이 양쪽에 모두 존재한다. 새 패턴 추가 시 양쪽 모두 수정해야 하므로 유지보수 부담이 있다.
- **개선안**: 공통 보안 패턴을 `src/utils/security.py` 또는 `resources/domain/forbidden_patterns.yaml`로 통합하고, 두 모듈이 이를 참조하도록 변경. 다만 input_sanitizer는 사용자 자연어 입력 대상이고, sql_safety_checker는 LLM 생성 SQL 대상이므로 적용 범위가 다른 패턴은 분리 유지

### W-03. `data_analyzer.py` - `rows_to_markdown_table` 지연 임포트가 2곳에서 반복

- **파일**: `src/services/data_analyzer.py:205-207`, `src/services/data_analyzer.py:300-302`
- **문제**: `from src.services.response_formatter import rows_to_markdown_table`이 함수 내부에서 2회 지연 임포트된다. 순환 참조를 피하기 위한 것으로 보이지만, 이 함수는 순수 유틸리티 함수이므로 순환 참조의 원인이 아니다.
- **개선안**: `rows_to_markdown_table`을 `src/utils/formatting.py` 같은 순환 참조 없는 유틸리티로 이동하고, 두 모듈 모두 모듈 레벨에서 임포트

### W-04. `insight_builder.py` - `_get_attr_or_key` 남용으로 타입 안전성 부재

- **파일**: `src/services/insight_builder.py` (전체, 30회 이상 호출)
- **문제**: 모든 State 접근이 `_get_attr_or_key(obj, key, default)`를 통해 이루어지며, 반환 타입이 항상 `Any`이다. Pydantic 모델과 dict를 모두 지원하기 위한 것이지만, mypy --strict 통과가 불가능하며 런타임 타입 오류를 감지할 수 없다.
- **개선안**:
  1. `build_insight()`의 파라미터를 `dict[str, Any]` 대신 `PipelineState` 타입으로 받도록 변경
  2. State에서 데이터 추출을 별도 어댑터(adapter)로 분리하여 변환 로직을 한 곳에 집중
  3. `_to_dict`와 `_get_attr_or_key`를 최소화하고 Pydantic 모델 접근을 직접 사용

### W-05. `config.py` - DB 연결 정보가 개별 필드로 평탄화되어 있음

- **파일**: `src/config.py:66-77`
- **문제**: `info_db_host`, `info_db_port`, `info_db_name` 등이 개별 필드로 나열되어 있지만, `history_db`는 `@property`로 `DbConnectionInfo`를 반환한다. `info_db`에는 이런 property가 없어 일관성이 떨어진다. 또한 DB 연결 정보를 추가할 때마다 6~7개 필드를 반복 선언해야 한다.
- **개선안**: `DbConnectionInfo`를 nested model로 활용하여 `info_db: DbConnectionInfo`, `history_db: DbConnectionInfo`로 통합. pydantic-settings v2의 `env_nested_delimiter`를 활용하면 `INFO_DB__HOST=localhost` 형태로 .env에서 설정 가능

### W-06. `main.py` - health_check의 LLM 상태 확인이 API 키 존재 여부로만 판단

- **파일**: `src/main.py:140-146`
- **문제**: LLM API 연결 상태를 API 키 존재 여부(`bool(settings.anthropic_api_key)`)로만 확인한다. 키가 설정되어 있어도 API가 다운되었거나, 키가 만료/무효화되었을 수 있다. 이는 health check의 목적에 부합하지 않는다.
- **개선안**: 실제 LLM API에 최소 비용의 ping 요청(예: 짧은 프롬프트)을 보내거나, 최소한 HTTP 연결 확인을 수행. 비용을 고려하여 캐시된 결과를 TTL과 함께 사용

### W-07. `session/store.py` - deprecated 메서드가 기본 구현으로 남아있음

- **파일**: `src/services/session/store.py:62-83`
- **문제**: `get_clarification()`과 `set_clarification()`이 deprecation warning을 발생시키면서도 추상 클래스에 기본 구현으로 남아있다. checkpointer + interrupt 패턴으로 이관이 완료되었다면 다음 메이저 버전에서 제거 계획이 필요하다.
- **개선안**: 제거 예정 시점을 명시(예: `DeprecationWarning` 메시지에 "v0.3에서 제거 예정" 추가)하고, 호출하는 코드가 없다면 즉시 제거

### W-08. `main.py` - `_run_ws_pipeline`의 응답 조립 로직이 너무 복잡함

- **파일**: `src/main.py:187-292`
- **문제**: 하나의 함수가 106줄이며, 이력 저장, 파이프라인 실행, PII 마스킹, 시각화 전송, 스트리밍 응답 전송, 통찰 전송, 다운로드 알림까지 7가지 책임을 지고 있다.
- **개선안**: 응답 메시지 조립 로직을 별도 함수로 분리. 예: `_send_visualization()`, `_send_streaming_response()`, `_notify_download_ready()` 등

### W-09. `chart_generator.py` - 차트 생성 함수 간 레이아웃 계산 중복

- **파일**: `src/services/visualization/chart_generator.py:98-189, 192-280`
- **문제**: `generate_bar_chart()`와 `generate_line_chart()`에서 settings로부터 margin/width/height를 읽고 chart_w/chart_h를 계산하는 코드가 거의 동일하게 반복된다. 제목 SVG 생성, 축 그리기 코드도 중복이다.
- **개선안**: `_ChartLayout` 데이터클래스를 만들어 공통 레이아웃 계산을 추출하고, `_render_title()`, `_render_axes()` 같은 공통 SVG 빌더 함수를 분리

### W-10. `query_normalizer.py` - 도메인 사전 로딩이 모듈 레벨에서 실행됨

- **파일**: `src/services/query_normalizer.py:70-87`
- **문제**: `load_yaml()`이 모듈 임포트 시 즉시 실행된다. YAML 파일이 없거나 파싱 오류가 발생하면 전체 서버가 기동되지 않는다. 또한 테스트 시 mock이 어렵다.
- **개선안**: 지연 로딩(lazy loading) 패턴으로 전환하거나, lifespan에서 명시적으로 초기화. 다만 현재 기본값(`{}`)을 제공하고 있어 실질적 장애 가능성은 낮으므로 Warning 수준

---

## Info (참고/개선 검토)

### I-01. `input_sanitizer.py:34` - `MAX_INPUT_LENGTH`가 모듈 레벨 상수로 캐시됨

- **파일**: `src/services/input_sanitizer.py:34`
- **문제**: `MAX_INPUT_LENGTH = settings.max_input_length`가 모듈 로드 시 한 번만 평가된다. 런타임에 settings를 변경해도 반영되지 않는다. 현재 동적 설정 변경이 없으므로 실질적 문제는 없지만, 다른 서비스들이 `settings.xxx`를 직접 참조하는 것과 패턴이 다르다.
- **개선안**: `settings.max_input_length`를 직접 참조하여 일관성 유지

### I-02. `input_sanitizer.py:128-130` - `mask_for_logging` 미사용

- **파일**: `src/services/input_sanitizer.py:128-130`
- **문제**: `mask_for_logging()` 함수가 정의되어 있지만 프로젝트 내 어디에서도 호출되지 않는다. 죽은 코드로 판단된다.
- **개선안**: 사용 계획이 없으면 제거. 로깅 시 PII 마스킹이 필요하다면 `src/utils/security.py`의 `mask_pii()`가 이미 존재하므로 그것을 사용

### I-03. `intent_resolver.py:194-206` - `subclassify_data_query` 미사용 가능성

- **파일**: `src/services/intent_resolver.py:194-206`
- **문제**: `subclassify_data_query()`가 프로젝트 내에서 호출되지 않는 것으로 보인다. Intent Gate가 이미 DATA_EXTRACTION/DATA_ANALYSIS를 직접 분류하므로 이 함수의 필요성이 줄었다.
- **개선안**: 실제 사용 여부를 재확인하고, 미사용이라면 제거 또는 `# TODO: Legacy, Intent Gate로 대체됨` 주석 추가

### I-04. `config.py` - 설정 항목이 290줄 이상으로 매우 길음

- **파일**: `src/config.py`
- **문제**: 단일 `Settings` 클래스에 LLM, DB(6종), ES, Qdrant, MongoDB, Neo4j, Redis, 임베딩, Reranker, Impala, Hive, Sybase, 파이프라인 제어, 차트 레이아웃 등 모든 설정이 평탄하게 나열되어 있어 308줄에 달한다. 설정을 찾기 어렵고, 관련 없는 시스템의 설정이 한 클래스에 혼재한다.
- **개선안**: 논리적 그룹별로 nested config 모델을 도입 (예: `LlmConfig`, `EsConfig`, `QdrantConfig` 등). pydantic-settings v2의 nested model 지원 활용. 단, 변경 규모가 크므로 점진적으로 진행

### I-05. `main.py:23` - docstring의 기동 예시에 줄바꿈 오류

- **파일**: `src/main.py:23`
- **문제**: `# 개발 (hot-reload)uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload` 주석과 명령어가 한 줄에 붙어 있다.
- **개선안**: 줄바꿈 추가 `# 개발 (hot-reload)\n    uvicorn src.main:app ...`

### I-06. `insight_builder.py` - `_build_step_timings`의 시간 계산이 부정확할 수 있음

- **파일**: `src/services/insight_builder.py:327-387`
- **문제**: 노드별 소요 시간을 trace_log의 timestamp 쌍으로 계산하는데, 같은 노드가 3회 이상 등장하면 마지막 2회의 시간만 측정된다. `seen_nodes[node] = t`로 덮어쓰기 때문이다.
- **개선안**: `action`이 "start"/"end" 쌍인 것을 확인하여 매칭하거나, 모든 실행 회차별 시간을 누적

### I-07. `history_resolver.py:158-183` - `_find_last_data_query`의 키워드 기반 감지가 취약

- **파일**: `src/services/history_resolver.py:174-183`
- **문제**: 하드코딩된 18개 키워드로 "데이터 질의"를 판단한다. 은행 업무 용어 범위가 넓어 누락될 수 있다. 예를 들어 "펀드", "보험", "수수료", "환율" 등이 빠져 있다.
- **개선안**: 도메인 사전(resources/domain/)에서 키워드 목록을 로드하거나, 보다 일반적인 패턴(길이 + 명사 존재 여부 등)으로 판단

### I-08. `main.py:70-81` - `QueryRequest`의 `max_length`가 config와 불일치

- **파일**: `src/main.py:73-75`, `src/config.py:221`
- **문제**: `QueryRequest`의 `max_length=2000`이 하드코딩되어 있는 반면, `settings.max_input_length=500`이 별도로 존재한다. REST API 입력과 input_sanitizer의 최대 길이 기준이 다르다.
- **개선안**: `QueryRequest`에서도 `settings.max_input_length`를 참조하거나, 두 값의 의미 차이를 문서화. 현재는 2000자가 API 수준 방어, 500자가 비즈니스 로직 방어로 보이지만 명시적 설명이 없음

---

## 아키텍처 개선 제안 (대규모 변경)

아래는 다수의 파일에 걸친 구조적 변경이 필요한 제안이다. 실행 전 확인을 요청한다.

### A-01. `main.py` 분할 리팩토링

현재 `main.py`가 API 라우팅, WebSocket 처리, 캐시 관리, HTML 서빙 등 과도한 책임을 지고 있다. 다음과 같이 분할을 제안한다:

| 현재 위치 | 분리 대상 | 제안 위치 |
|-----------|----------|----------|
| `main.py:154-184` | 슬래시 명령어 처리 | `src/api/slash_commands.py` |
| `main.py:187-292` | WebSocket 파이프라인 실행 | `src/api/websocket_handler.py` |
| `main.py:294-355` | WebSocket 엔드포인트 | `src/api/websocket_handler.py` |
| `main.py:358-459` | REST 엔드포인트 | `src/api/routes.py` |
| `main.py:462-550` | 다운로드 캐시/엔드포인트 | `src/api/download.py` |
| `main.py:553-564` | HTML 폴백 | `src/api/static.py` |
| `main.py:84-113` | FastAPI 앱 + lifespan | `src/main.py` (잔류) |

### A-02. 보안 패턴 중앙 관리

`input_sanitizer.py`와 `sql_safety_checker.py`의 금지 패턴을 통합하여 단일 소스로 관리:

```
resources/domain/security_patterns.yaml
  input_patterns:     # 자연어 입력용
  sql_patterns:       # 생성 SQL용
  common_patterns:    # 양쪽 공통
```

---

## 긍정적 평가

1. **프롬프트 주입 패턴** — 서비스 함수가 프롬프트를 인자로 받아 코드 변경 없이 프롬프트를 교체할 수 있는 설계가 우수하다.
2. **세션 스토어 추상화** — `SessionStore` ABC + 팩토리 패턴으로 memory/redis 전환이 깔끔하다.
3. **SQL 안전성 다층 방어** — 5단계 검증 파이프라인(정규화 → SELECT 확인 → 금지 패턴 → 구문 파싱 → PII)이 심층 방어 원칙에 부합한다.
4. **ParseError 기반 자동 재시도** — `llm_call_with_parse_retry`를 통해 LLM 포맷 불일치를 자동 재시도하는 패턴이 일관되게 적용되어 있다.
5. **도메인 사전 외부화** — 동의어, 약어, 출력 템플릿을 YAML로 관리하여 코드 수정 없이 도메인 지식을 업데이트할 수 있다.
