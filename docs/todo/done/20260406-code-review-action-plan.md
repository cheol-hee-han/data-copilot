# 코드리뷰 수정 계획 및 상세설계안

> 작성일: 2026-04-06
> 근거: `docs/reviews/code/20260406-full-codebase-review-summary.md` (37건 이슈)
> 대상: 수정 대상 15건 (#3~7, #9, #10, #14, #17~19, #23~27, #31)

---

## 0. 요약문서 ↔ 원본 리뷰 상충 검토 결과

6개 원본 리뷰 보고서와 요약문서를 전수 대조한 결과, **실질적 판정 모순은 0건**이며
심각도 표기 불일치(하향 사유 미기재) 등 경미한 기재 오류가 존재합니다.
모두 최종 판정(수정/제외/보류)에는 영향을 주지 않으므로 **요약문서의 수정 대상 15건을 그대로 채택**합니다.

### 발견된 표기 불일치 (참고용)

| 이슈 | 원본 등급 | 요약 등급 | 비고 |
|------|----------|----------|------|
| #10 (cancel_store 경쟁 상태) | Critical | Warning | 하향 사유 미기재. GETDEL 1줄 수정이므로 Warning 적정 |
| #23,#24,#26 (프롬프트 포맷) | Critical | Warning | 프롬프트 특수문자는 기능 장애가 아닌 토큰 낭비이므로 Warning 적정 |
| #30 (UNION 차단) | Warning + `security.py` | ~~Info~~ + `sql_safety_checker.py` | 파일명 오기. 이미 적용 완료 |
| #33 (chart_generator YAML) | Warning | Info | 모듈 import 시점 I/O로 Info 적정 |
| #34 (neo4j 캐시) | Warning | Info | 실제 메모리 영향 미미하므로 Info 적정 |
| #36 (embedded.html 명명) | Warning + `ThemeManager` | Info + `TimerManager` | 모듈명 오기 (`ThemeManager`가 정확) |
| #12,#29 원래 등급 표기 | Critical | ~~Warning~~ | 취소선 등급이 원본과 다르나 최종 판정 동일 |
| #31 (LLM 재시도) | Present 리포트에 미존재 | Warning | 별도 발견 이슈로 추정. 코드 확인 시 유효 |
| #35 (turn_seq 채번) | Critical | ~~Info~~ | 중간 등급 표기 차이. 최종 판정(제외) 동일 |

> 위 불일치는 요약문서에 정정 코멘트를 추가하는 수준이며, 수정 계획에는 영향 없음

---

## 1. 수정 Phase 및 상세설계

### Phase 1 — 보안 Critical (즉시)

#### 1-1. #3 Cancel 시 SQL 원문 노출 제거

- **파일**: `src/agents/nodes/present/sql_executor.py:50-54`
- **현재 코드**:
  ```python
  cancel_msg = "요청이 중단되었습니다."
  if state.reason.validated_sql:
      cancel_msg += (
          f" 생성된 SQL: {state.reason.validated_sql[:200]}"
      )
  ```
- **변경**:
  ```python
  cancel_msg = "요청이 중단되었습니다. 다른 질문이 있으시면 말씀해 주세요."
  ```
- **설계 근거**: IT 지식 없는 일반 직원에게 SQL 구조 노출 불필요. 자연어 메시지로 충분
- **영향 범위**: 이 함수 내부만. 반환 dict의 `formatted_response` 값만 변경
- **테스트**: cancel 시나리오에서 응답에 SQL 키워드가 없는지 확인

#### 1-2. #4 PostgreSQL 커넥터 비밀번호 URL 안전 처리

- **파일**: `src/connectors/impl/postgres_connector.py:60-67, 172-179`
- **현재 코드**:
  ```python
  url = (
      f"postgresql+asyncpg://"
      f"{settings.info_db_user}"
      f":{settings.info_db_password}"
      f"@{settings.info_db_host}..."
  )
  ```
- **변경 (InfoDBConnector, HistoryDBConnector 동일 패턴)**:
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
- **설계 근거**: `URL.create()`는 특수문자(`@`, `#`, `:` 등)를 자동 이스케이프. 폐쇄망 비밀번호 정책 대응 필수
- **영향 범위**: `create_async_engine(url)` 호출부. `URL` 객체도 engine 생성자가 수용하므로 추가 변환 불필요
- **테스트**: 특수문자 포함 비밀번호(`p@ss#w0rd!`)로 연결 테스트

#### 1-3. #5 MongoDB 커넥터 비밀번호 URI 안전 처리

- **파일**: `src/connectors/impl/mongo_connector.py:145-149`
- **현재 코드**:
  ```python
  connection_uri = (
      f"mongodb://{settings.mongo_user}:{settings.mongo_password}"
      f"@{settings.mongo_host}:{settings.mongo_port}"
      f"/{settings.mongo_database}?authSource=admin"
  )
  ```
- **변경**:
  ```python
  from urllib.parse import quote_plus

  connection_uri = (
      f"mongodb://{quote_plus(settings.mongo_user)}:{quote_plus(settings.mongo_password)}"
      f"@{settings.mongo_host}:{settings.mongo_port}"
      f"/{settings.mongo_database}?authSource=admin"
  )
  ```
- **설계 근거**: `quote_plus`로 사용자/비밀번호의 특수문자를 퍼센트 인코딩
- **영향 범위**: `AsyncIOMotorClient(connection_uri)` 호출부. 인코딩된 URI도 드라이버가 정상 처리
- **테스트**: 특수문자 포함 자격증명으로 MongoDB 연결 테스트

#### 1-4. #6 sanitizeHTML에 `<style>` 태그 제거 추가

- **파일**: `static/embedded.html` (sanitizeHTML 함수, ~L2040-2048)
- **현재 상태**: `script`, `iframe`, `embed`, `object`, `link` 제거. `style` 누락
- **변경**: 제거 대상 태그 목록에 `style` 추가
  ```javascript
  // 기존: 'script', 'iframe', 'embed', 'object', 'link'
  // 변경: 'script', 'iframe', 'embed', 'object', 'link', 'style'
  ```
- **설계 근거**: CSS 인젝션으로 UI 위조, `@import url()`로 외부 서버 데이터 유출 가능
- **영향 범위**: LLM 생성 HTML 응답에서 인라인 스타일이 제거됨. LLM 응답의 서식은 마크다운 → HTML 변환으로 처리하므로 `<style>` 태그 의존 없음
- **테스트**: `<style>@import url('http://evil.com')</style>` 포함 HTML이 정제되는지 확인

#### 1-5. #7 sanitizeSVG 외부 URL 참조 차단

- **파일**: `static/embedded.html` (sanitizeSVG 함수, ~L2028-2039)
- **현재 상태**: `javascript:` 프로토콜만 차단. `<use href="http://...">` 허용
- **변경**:
  ```javascript
  // href/xlink:href 속성에서 외부 URL 차단
  // 허용: href="#내부참조" (# 으로 시작하는 내부 참조만)
  // 차단: href="http://...", href="//...", href="data:..." 등
  svg.querySelectorAll('[href], [xlink\\:href]').forEach(el => {
      const href = el.getAttribute('href') || el.getAttribute('xlink:href') || '';
      if (href && !href.startsWith('#')) {
          el.removeAttribute('href');
          el.removeAttribute('xlink:href');
      }
  });
  ```
- **설계 근거**: SVG 내 `<use>`, `<image>`, `<feImage>` 등에서 외부 URL 참조로 SSRF/정보 유출 가능
- **영향 범위**: LLM 생성 SVG 차트에서 외부 리소스 로드 차단. 내부 `#id` 참조는 유지
- **테스트**: `<use href="http://external.com/icon.svg">` 가 href 제거되는지 확인

---

### Phase 2 — 데이터 무결성 / 동시성 (1주 내)

#### 2-1. #9 Pipeline 라우팅 함수 내 State Mutation 제거

- **파일**: `src/agents/graph/pipeline.py:232-255`
- **현재 코드**: `_route_after_sql_validator` 라우팅 함수에서 `state.reason.recovery_entry_source = "sql_validator"` 직접 설정 (L233-234, L252-254)

- **기존 패턴 분석**: `sql_validator.py`는 L64에서 `reason.model_copy(deep=True)`로 복사본을 생성한 후 복사본을 mutation하고 `{"reason": reason}`으로 반환하는 패턴. 이것은 LangGraph에서 올바른 노드 패턴 (원본 state가 아닌 복사본 mutation + 반환). 따라서 `recovery_entry_source`도 이 동일 복사본에 설정하면 패턴이 일관됨

- **변경 대상 파일 2개**:

  **(a) `src/agents/nodes/reason/sql_validator.py`** — `reason` 복사본에 `recovery_entry_source` 설정 추가.
  recovery 진입 가능한 failure_type을 설정하는 **4개 위치**에 각각 추가:

  ```python
  # 위치 1: L82 (Layer1 SQL_SYNTAX 실패) — recovery 대상 아님, 추가 불필요

  # 위치 2: _build_layer2a_failure (L183-197)
  # SQL_SEMANTIC_LOCAL 또는 SQL_STRUCTURAL 판정 → recovery 가능
  reason.recovery_entry_source = "sql_validator"

  # 위치 3: _build_layer2b_failure (L200+)
  # LLM 의미 검증 실패 → recovery 가능
  reason.recovery_entry_source = "sql_validator"

  # 위치 4: L138,158 (Layer3 DB_ERROR 등)
  # DB 실행 실패 → recovery 가능
  reason.recovery_entry_source = "sql_validator"
  ```

  각 위치에서 `reason`은 이미 `model_copy(deep=True)` 복사본이므로, 기존 `failure_type` 설정과 동일한 mutation 패턴. 단, 다른 노드에서 이미 설정한 경우를 보존해야 하므로:
  ```python
  if not reason.recovery_entry_source:
      reason.recovery_entry_source = "sql_validator"
  ```

  **(b) `src/agents/graph/pipeline.py:232-255`** — 라우팅 함수에서 mutation 2줄 삭제:
  ```python
  # 삭제: state.reason.recovery_entry_source = "sql_validator"  (L233-234)
  # 삭제: if not state.reason.recovery_entry_source:            (L252)
  #        state.reason.recovery_entry_source = "sql_validator"  (L253-254)
  ```

- **설계 근거**: LangGraph 라우팅 함수는 순수 함수여야 함. 체크포인터가 mutation 전 상태를 저장하면 interrupt/resume 시 불일치 발생. A-2 결정에 따라 `sql_validator_node` 반환값으로 이동. 노드 내부의 복사본 mutation은 기존 패턴과 100% 일관
- **영향 범위**: `sql_validator_node` → `_route_after_sql_validator` 경로. recovery_entry_source 값이 노드에서 설정되므로 라우팅 시점에는 이미 state에 반영
- **테스트**: SQL 검증 실패(Layer1/2a/2b/3 각 경로) → recovery 진입 시 `recovery_entry_source`가 정상 설정되는지 확인

#### 2-2. #10 Cancel Store Redis 원자적 처리

- **파일**: `src/services/cancel_store.py:80-87`
- **현재 코드**:
  ```python
  async def pop_cancel(self, session_id: str) -> str | None:
      key = self._key(session_id)
      val = await self._client.get(key)
      if val is None:
          return None
      await self._client.delete(key)
      return val.decode() if isinstance(val, bytes) else val
  ```
- **변경**:
  ```python
  async def pop_cancel(self, session_id: str) -> str | None:
      """플래그를 원자적으로 반환하고 삭제한다 (GETDEL)."""
      key = self._key(session_id)
      val = await self._client.getdel(key)
      if val is None:
          return None
      return val.decode() if isinstance(val, bytes) else val
  ```
- **설계 근거**: `GETDEL`은 Redis 6.2+에서 지원. GET+DELETE 사이 경쟁 상태 제거
- **영향 범위**: `pop_cancel` 호출부(cancel 체크 로직)만. 동작 결과 동일
- **호환성**: Redis 6.2+ 필요. 프로젝트 Redis 버전 확인 필요. 미지원 시 Lua 스크립트 대안:
  ```python
  _POP_SCRIPT = "local v=redis.call('GET',KEYS[1]); if v then redis.call('DEL',KEYS[1]) end; return v"
  val = await self._client.eval(_POP_SCRIPT, 1, key)
  ```
- **테스트**: 동시 pop_cancel 호출 시 하나만 값을 반환하는지 확인

---

### Phase 3 — 아키텍처 / 기능 (2주 내)

#### 3-1. #14 runner.py `run_pipeline` 함수 분리

- **파일**: `src/agents/graph/runner.py:62-371` (~310줄)
- **현재 상태**: sanitize → interrupt 확인 → 그래프 실행 → 턴 저장 → 에러 처리가 단일 함수
- **변경**: 4개 private 함수로 분리

  ```python
  async def run_pipeline(user_input, session_id, ...):
      """파이프라인 실행 진입점."""
      state = _prepare_initial_state(user_input, session_id, ...)
      state = await _sanitize_and_check(state, on_event)
      if state.status != QueryStatus.PROCESSING:
          return _build_result(state)
      result = await _execute_graph(state, on_event)
      return result

  def _prepare_initial_state(user_input, session_id, ...) -> PipelineState:
      """입력 정제, 초기 State 구성."""
      ...

  async def _sanitize_and_check(state, on_event) -> PipelineState:
      """입력 검증, cancel/interrupt 체크."""
      ...

  async def _execute_graph(state, on_event) -> PipelineResult:
      """그래프 실행, 턴 저장, 에러 처리."""
      ...
  ```

- **설계 근거**: 테스트 용이성, 에러 경로 추적 개선. 각 단계를 독립 테스트 가능
- **원칙**: 로직 변경 없이 순수 구조 분리만 수행. 기존 동작 100% 보존
- **영향 범위**: `run_pipeline` 내부만. 외부 API (`main.py`의 호출부) 시그니처 불변
- **테스트**: 기존 통합 테스트 전체 패스 확인

#### 3-2. #17 ES 잔존 데드코드 제거

- **파일**: `src/agents/models/user_messages.py:52-59`
- **현재 코드**:
  ```python
  CONTEXT_SOURCE_LABELS: dict[str, str] = {
      "es_table_meta": "테이블 정보",
      "es_report_sql": "보고서 SQL",
      "es_code_meta": "코드 정보",
      ...
  }
  ```
- **변경**: `format_context_warning()` 함수 자체가 호출 0건인 데드코드이므로 함수와 `CONTEXT_SOURCE_LABELS` dict 전체 삭제
- **확인 사항**: 삭제 전 `format_context_warning` 참조가 0건인지 grep으로 재확인
- **영향 범위**: 없음 (데드코드)

#### 3-3. #18 config.py ES 관련 필드 주석 처리

- **파일 4개 변경**:

  **(a) `src/config.py:77-87`** — ES 관련 필드 12개 주석 처리:
  ```python
  # --- ElasticSearch (미사용, 향후 재사용 가능성 있어 주석 보존) ---
  # es_host: str = "localhost"
  # es_port: int = 9200
  # es_user: str = "elastic"
  # es_password: str = ""
  # es_table_meta_index: str = "table_meta"
  # es_report_sql_index: str = "report_sql"
  # es_code_meta_index: str = "code_meta"
  # es_table_meta_size: int = 10
  # es_report_sql_size: int = 5
  # es_code_meta_size: int = 20
  ```

  **(b) `src/connectors/manager.py:26-28, 52`** — ES import + 레지스트리 항목 주석 처리:
  ```python
  # from src.connectors.impl.elasticsearch_connector import (
  #     ElasticSearchConnector,
  # )
  ...
  # ("elasticsearch", "es"),  # 미사용
  ```

  **(c) `pyproject.toml:31`** — ES 의존성을 optional로 이동:
  ```toml
  # dependencies에서 제거:
  # "elasticsearch>=8.0.0,<9.0.0",

  # optional-dependencies에 추가:
  [project.optional-dependencies]
  elasticsearch = ["elasticsearch>=8.0.0,<9.0.0"]
  ```

  **(d) `src/connectors/impl/elasticsearch_connector.py`** — 파일 자체는 보존 (향후 재사용)

- **설계 근거**: 향후 재사용 가능성 있어 삭제하지 않고 주석 처리. 폐쇄망에서 ES 패키지 설치 시도 방지
- **영향 범위**: `enabled_connectors`에 `"elasticsearch"`가 없으면 기존에도 ES 커넥터 미사용. import 제거로 패키지 미설치 환경에서도 정상 기동
- **테스트**: `elasticsearch` 패키지 없이 앱 기동 + 기존 커넥터 health_check 정상 확인

---

### Phase 4 — 커넥터 / 폐쇄망 대응 (배포 전)

#### 4-1. #19 Hive/Impala/Sybase 커넥터에 sanitize_row() 적용

- **파일**:
  - `src/connectors/impl/hive_connector.py:126-134`
  - `src/connectors/impl/impala_connector.py` (동일 구조)
  - `src/connectors/impl/sybase_connector.py` (동일 구조)
- **현재 상태**: PostgreSQL 커넥터만 `sanitize_row()` 적용. 나머지 3개 커넥터는 미적용
- **변경 (hive_connector.py 예시)**:
  ```python
  from src.connectors.interfaces import DatabaseConnector, sanitize_row

  # execute_query 내 rows 반환 직전:
  rows = [
      sanitize_row(dict(zip(columns, row)))
      for row in cursor.fetchall()
  ]
  ```
- **설계 근거**: `interfaces.py` docstring의 의무사항. Decimal, date, bytes 등 비직렬화 타입이 JSON 변환에서 TypeError 발생 방지
- **영향 범위**: 각 커넥터의 `execute_query` 반환값. sanitize_row는 Decimal→float, date→str, bytes→str 변환
- **패턴 일관성**: PostgreSQL 커넥터(`postgres_connector.py:134,229`)의 기존 패턴을 그대로 적용
- **테스트**: Decimal/date/bytes 포함 더미 데이터로 JSON 직렬화 성공 확인

---

### Phase 5 — 프롬프트 포맷 통일 (병행)

#### 5-1. #23 analyzer_viz_svg_system.txt 구분선 변환

- **파일**: `resources/prompts/present/analyzer_viz_svg_system.txt`
- **현재 상태**: `━━━[...]━━━` 구분선 6쌍(12줄) 잔존
- **변경**: `━━━ 제목 ━━━` → `## 제목` 마크다운 헤딩으로 변환
- **설계 근거**: 설계서 `20260402-prompt-format-unification.md`의 잔여 작업. 폐쇄망 모델(Solar Pro 2 70B)이 특수문자를 토큰 낭비 가능

#### 5-2. #24 query_normalizer_phase1_system.txt 특수문자 변환

- **파일**: `resources/prompts/interpret/query_normalizer_phase1_system.txt`
- **현재 상태**: `■` 4개소(L270,320,373,428) + `━━━` 2개소(L485,487)
- **변경**:
  - `■ 예제 N:` → `### 예제 N:` (4개소)
  - `━━━` → 제거 또는 `---` 마크다운 수평선 (2개소)

#### 5-3. #25 analyzer_viz_judgment_system.txt 체크박스 변환

- **파일**: `resources/prompts/present/analyzer_viz_judgment_system.txt`
- **현재 상태**: `□` 체크박스 3개소(L70-72)
- **변경**: `□` → `- [ ]` 또는 `- ` (마크다운 리스트)
  ```
  # Before
  □ 각 행에 고유 식별자가 있음
  # After
  - 각 행에 고유 식별자가 있음
  ```

#### 5-4. #26 query_normalizer_phase2_system.txt 블릿 변환

- **파일**: `resources/prompts/interpret/query_normalizer_phase2_system.txt`
- **현재 상태**: `■` 2개소(L80, L93)
- **변경**: `■ R1 위반:` → `### R1 위반:` 또는 `**R1 위반:**`

#### 5-5. #27 intent_classifier Few-shot ambiguities 키 정렬

- **파일**: `resources/prompts/interpret/intent_classifier_system.txt`
- **현재 상태**: CASUAL_TALK(L339), META_QUESTION(L358), DATA_EXTRACTION(L379) 등 예제에서 `ambiguities` 키 자체를 생략. 스키마 정의(L98)에서는 `"ambiguities": []` 빈 배열 출력을 명시
- **변경**: `ambiguities` 키가 없는 Few-shot 예제에 `"ambiguities": []` 추가
  ```json
  {
    "continuity": { ... },
    "intent": { ... },
    "ambiguities": []
  }
  ```
- **대상 예제**: ambiguities 키가 없는 모든 예제 (CASUAL_TALK, META_QUESTION, DATA_EXTRACTION 중 ambiguity 없는 케이스)
- **설계 근거**: 폐쇄망 모델일수록 예제 의존도 높음. 예제에서 키를 생략하면 LLM이 키 자체를 빼고 출력하여 파싱 시 KeyError 발생

---

### Phase 6 — 코드 품질 / 성능 (여유 시)

#### 6-1. #31 LLM 호출 재시도 적용

- **파일**:
  - `src/services/response_formatter.py:124`
  - `src/services/data_analyzer.py:156`

- **재조사 결과**: `get_llm_client()`가 반환하는 `AsyncAnthropic` 클라이언트에 이미 `max_retries=settings.llm_transport_max_retry`(기본 3)가 설정됨(`client.py:305`). 따라서 429/500/503/네트워크 오류에 대한 **전송 레벨 재시도는 이미 SDK에 내장**.

- **실제 누락**: 두 서비스가 raw `client.messages.create()`를 직접 호출하여, 프로젝트 표준 패턴인 `llm_call_with_parse_retry()`의 **포맷 파싱 재시도**(JSON 파싱 실패 시 재요청)를 우회하고 있음. `data_analyzer.py`는 일부 호출(L112, L319)은 `llm_call_with_parse_retry` 사용, 다른 호출(L156)은 raw 호출로 혼재

- **변경 방안**: JSON 파싱이 필요한 호출은 `llm_call_with_parse_retry`로 통일. 자유 텍스트 응답(마크다운 등)을 받는 호출은 raw 호출 유지가 적절하므로 각 호출부의 응답 포맷을 확인 후 판단
  ```python
  # JSON 응답 기대 → llm_call_with_parse_retry 사용
  # 자유 텍스트 응답 → raw client.messages.create 유지 (SDK 전송 재시도로 충분)
  ```

- **설계 근거**: 프로젝트 내 11개소에서 `llm_call_with_parse_retry` 사용 중. 동일 패턴으로 통일하여 일관성 확보. 전송 레벨 재시도는 이미 동작하므로 심각도는 원래보다 낮음
- **영향 범위**: 해당 호출부만. 응답 처리 로직 불변
- **테스트**: JSON 파싱 실패 시 재시도 동작 확인

---

## 2. 수정 순서 및 의존성

```
Phase 1 (즉시, 독립 작업 5건 병렬 가능)
├── 1-1 #3  cancel SQL 제거 ─────────── 독립
├── 1-2 #4  postgres URL.create() ───── 독립
├── 1-3 #5  mongo quote_plus ────────── 독립
├── 1-4 #6  sanitizeHTML style ──────── 독립
└── 1-5 #7  sanitizeSVG href ────────── 1-4와 같은 파일, 순차

Phase 2 (1주 내)
├── 2-1 #9  state mutation 제거 ──────── sql_validator.py 수정 후 pipeline.py 수정
└── 2-2 #10 cancel GETDEL ───────────── 독립

Phase 3 (2주 내)
├── 3-1 #14 runner.py 분리 ──────────── 독립
├── 3-2 #17 ES 데드코드 삭제 ─────────── 독립
└── 3-3 #18 config.py ES 주석 ────────── 3-2 이후 (import 참조 확인)

Phase 4 (배포 전)
└── 4-1 #19 sanitize_row 적용 ────────── 독립

Phase 5 (병행, 모두 독립)
├── 5-1 #23 svg 프롬프트 구분선
├── 5-2 #24 phase1 특수문자
├── 5-3 #25 judgment 체크박스
├── 5-4 #26 phase2 블릿
└── 5-5 #27 intent Few-shot ambiguities

Phase 6 (여유 시)
└── 6-1 #31 LLM 재시도 ──────────────── 기존 패턴 확인 후 적용
```

---

## 3. 변경 영향도 요약

| Phase | 변경 파일 수 | 위험도 | 회귀 테스트 범위 |
|-------|------------|--------|----------------|
| 1 | 4개 (executor, postgres, mongo, embedded.html) | 중 | 보안 시나리오 + 커넥터 연결 |
| 2 | 3개 (sql_validator, pipeline, cancel_store) | **높음** | 파이프라인 전체 흐름 + recovery 경로 |
| 3 | 3개 (runner, user_messages, config) | 중 | 앱 기동 + 파이프라인 실행 |
| 4 | 3개 (hive, impala, sybase 커넥터) | 낮음 | 더미 데이터 직렬화 |
| 5 | 5개 (프롬프트 텍스트) | 낮음 | LLM 출력 포맷 검증 |
| 6 | 2개 (formatter, analyzer 서비스) | 중 | LLM 호출 재시도 시나리오 |

---

## 4. 제외/보류 항목 정리 (수정하지 않음)

| 항목 | 사유 | 재검토 시점 |
|------|------|-----------|
| #1 (tools.py 파라미터 바인딩) | 화이트리스트+이스케이프+읽기전용+LLM출력으로 실질 위험 극히 낮음 | 폐쇄망 배포 후 |
| #2 (PII 로그 마스킹) | 로그 마스킹 정책 미확정 | 감사 요건 확정 시 |
| #8 (hive/impala params) | LLM 생성 SQL만 실행, params 전달 경로 없음 | — |
| #11 (redis_store None) | session_backend=memory 기본, RedisSessionStore 미사용 | Redis 활성화 시 |
| #12 (AmbiguitySignal mutation) | 전체 프로젝트 동일 패턴, frozen=True 전환 계획 없음 | — |
| #13 (sql_result_cache) | 파일 기반 캐시 방향 TODO. copy 기능으로 대체 가능 | 멀티워커 배포 시 |
| #15,#16 (인증/인가) | SSO redirection 연동 예정 | 폐쇄망 배포 시 |
| #20,#21 (커넥터 중복) | 폐쇄망 실 연동 전 현행 유지 | 폐쇄망 연동 시 |
| #22 (HistoryDB DML) | 이력 적재용 INSERT 허용 의도 | — |
| #28 (프롬프트 인젝션 경계) | TODO 기록. 향후 XML 경계 태그 적용 | — |
| #29 (input_sanitizer 오탐) | `--` 패턴 차단 빈도 극히 낮음 | — |
| #32 (recovery replan) | LoopGuard 횟수 제한 보장. replan 보정이 올바른 동작 | — |
| #33~35 (Info 수준) | 실질적 영향 미미 | — |
| #36 (embedded.html 명명) | Info 수준, 기능 영향 없음 | 프론트엔드 리팩토링 시 |
| #37 (프롬프트 경량화) | 3만 토큰 수준으로 컨텍스트 윈도우 내 수용 가능 | — |
