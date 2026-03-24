# 코드 리뷰 결과 리포트

- **검토 일시**: 2026-03-22
- **검토 대상**: `src/` 디렉토리 전체, 프로젝트 디렉토리 구조, `tests/`, `standalone/`
- **검토 기준**: `docs/agent-guides/code-review-checklist.md`
- **검토 도구**: 수동 코드 리뷰 (정적 분석 병행)

---

## 요약

| 등급 | 건수 | 비고 |
|------|------|------|
| 🔴 Critical | 6건 | import 불일치 2건, 타임아웃 미설정 2건, 신뢰도 체크 부재 1건, 역방향 의존 1건 |
| 🟡 Warning | 13건 | PII 로깅, 지수 백오프, SQL 검증 보강, 코드 스타일, 디렉토리 구조 등 |
| 🟢 Info | 9건 | 명명 일관성, 코드 중복, 캐싱 최적화 등 |

---

## 🔴 Critical (반드시 수정) — 6건

### C-01. 존재하지 않는 모듈 import — 런타임 ImportError

- **위치**: `src/agents/nodes/sql_generator.py:18-21`, `src/services/search_query_builder.py:32-36`
- **위반 규칙**: 코드 품질
- **문제 설명**: `from src.services.domain.domain_dictionary import ...`로 import하지만, 실제 파일명은 `src/services/domain/finance_terms.py`이다. `domain_dictionary.py`는 존재하지 않으므로 해당 노드/서비스 진입 시 `ModuleNotFoundError`가 발생하여 파이프라인이 동작하지 않는다.
- **개선 방법**: 두 가지 중 택일.
  - (A) `finance_terms.py`를 `domain_dictionary.py`로 리네임하고, 모든 import를 일치시킨다.
  - (B) import 경로를 `from src.services.domain.finance_terms import ...`로 수정한다.
  - 권장: (A) — `domain_dictionary`가 더 범용적인 명칭이며 두 곳에서 이미 이 이름으로 참조하고 있다.

### C-02. `query_normalizer.py`에서도 import 경로 불일치

- **위치**: `src/agents/nodes/query_normalizer.py:26-29`
- **위반 규칙**: 코드 품질
- **문제 설명**: `from src.services.domain.normalization_synonyms import ...`로 import하지만, 실제 파일명은 `business_synonyms.py`이다. C-01과 동일한 유형의 파일명-import 불일치 문제이다.
- **개선 방법**: import 경로를 `from src.services.domain.business_synonyms import ...`로 수정하거나, 파일명을 `normalization_synonyms.py`로 변경한다.

### C-03. DB 쿼리에 타임아웃 미설정

- **위치**: `src/connectors/postgres_connector.py:96-104` (InfoDBConnector.execute_query), `src/connectors/postgres_connector.py:173-181` (HistoryDBConnector.execute_query), `src/connectors/postgres_connector.py:219-227` (HistoryDBConnector.search_similar_sql)
- **위반 규칙**: 모든 DB 쿼리에 타임아웃 설정
- **문제 설명**: `create_async_engine`에 `pool_timeout`, `connect_args={"command_timeout": N}` 등이 없다. LLM이 생성한 복잡한 SQL이 장시간 실행될 경우 커넥션 풀이 고갈되거나 서비스가 행(hang)될 수 있다. 정보계 DB 쿼리는 대용량 테이블을 대상으로 하므로 특히 위험하다.
- **개선 방법**:
  ```python
  # create_async_engine 시
  self._engine = create_async_engine(
      url, echo=False,
      pool_timeout=30,
      connect_args={
          "timeout": 30,
          "command_timeout": 60,  # asyncpg: 쿼리 실행 타임아웃
      },
  )
  # 또는 쿼리 실행 시
  result = await session.execute(
      text(f"SET statement_timeout = '60s'; {query}"), params or {},
  )
  ```

### C-04. ES/Qdrant 커넥터에 타임아웃 미설정

- **위치**: `src/connectors/elasticsearch_connector.py:91-102` (search_table_meta 등), `src/connectors/qdrant_connector.py:98-102` (search_manual), `src/connectors/qdrant_connector.py:156-174` (search_sql_history)
- **위반 규칙**: 모든 외부 호출에 타임아웃 설정
- **문제 설명**: ES 검색, Qdrant 검색 호출에 `request_timeout` 파라미터가 없다. `search_context_assembler.py`에서 `asyncio.gather`로 병렬 호출하지만, 개별 소스에 타임아웃이 없으면 하나의 소스가 지연될 때 전체 컨텍스트 수집이 블로킹된다.
- **개선 방법**:
  ```python
  # ES
  resp = await self._client.search(
      index=settings.es_table_meta_index,
      body={...},
      request_timeout=10,
  )
  # Qdrant
  results = await asyncio.wait_for(
      self._client.search(...),
      timeout=10,
  )
  ```

### C-05. 신뢰도 점수 기반 실행 차단 로직 부재

- **위치**: `src/agents/graph/pipeline.py:77-117` (_route_after_intent)
- **위반 규칙**: 신뢰도 점수 임계값 체크 로직 존재 / SQL이 생성되었어도 신뢰도 낮을 경우 실행하지 않고 fallback
- **문제 설명**: `intent_confidence`가 `PipelineState`에 저장되지만(`state.py:153`), 라우팅 로직에서 신뢰도 임계값 체크가 없다. 신뢰도 0.3인 DATA_EXTRACTION 의도도 그대로 SQL 생성/실행 경로로 진행한다.
- **개선 방법**:
  ```python
  # pipeline.py의 _route_after_intent에 추가
  CONFIDENCE_THRESHOLD = 0.5  # settings에서 설정 가능하도록

  if state.intent in (IntentType.DATA_EXTRACTION, IntentType.DATA_ANALYSIS):
      if state.intent_confidence < CONFIDENCE_THRESHOLD:
          logger.info("라우팅: 신뢰도 낮음 -> 명확화")
          return "clarify"
  ```

### C-06. 역방향/순환 의존 구조

- **위치**: `src/utils/chart_generator.py:17` (utils → agents 역방향 의존), `src/services/table_meta_enricher.py:20` (services → agents/nodes 역방향 의존)
- **위반 규칙**: 순환 의존 또는 역방향 의존을 유도하는 구조가 아닌가
- **문제 설명**:
  - `chart_generator.py`가 `src.agents.state.state`에서 `SQLResult`, `VisualizationType`을 import한다. 하위 계층(utils)이 상위 계층(agents)에 의존하는 역방향 의존이다.
  - `table_meta_enricher.py`가 `src.agents.nodes.prompts.system_prompts`에서 프롬프트를 import한다. services 계층이 agents/nodes 계층의 내부 모듈에 직접 의존한다.
- **개선 방법**:
  - `chart_generator.py`를 `src/services/visualization/`으로 이동.
  - 프롬프트를 공유 위치(`src/prompts/` 등)로 분리하여 services에서 직접 참조 가능하도록 변경.

---

## 🟡 Warning (권장 수정) — 13건

### W-01. `runner.py`에서 user_input을 PII 마스킹 없이 로깅

- **위치**: `src/agents/graph/runner.py:55-58`
- **위반 규칙**: 사용자 입력의 로깅 시 PII 마스킹
- **문제 설명**: `logger.info("파이프라인 실행 시작", user_input=user_input, ...)`에서 사용자 입력을 마스킹 없이 로그에 기록한다. 사용자가 "홍길동 010-1234-5678 고객 조회" 같은 입력을 할 경우 PII가 로그에 평문으로 남는다.
- **개선 방법**: `user_input=mask_pii(user_input[:100])`으로 변경.

### W-02. LLM 재시도 로직에 지수 백오프 미적용

- **위치**: `src/utils/llm/retry.py:97-160`
- **위반 규칙**: 재시도 로직에 지수 백오프(exponential backoff) 적용
- **문제 설명**: `llm_call_with_parse_retry`는 포맷 파싱 실패 시 딜레이 없이 즉시 재시도한다. LLM API rate limit에 걸릴 경우 연속 실패가 발생할 수 있다.
- **개선 방법**:
  ```python
  for attempt in range(1 + max_retries):
      if attempt > 0:
          delay = min(2 ** attempt, 10)  # 2, 4, 8, max 10초
          await asyncio.sleep(delay)
      # ... LLM 호출 ...
  ```

### W-03. SQL 재생성 루프에 지수 백오프 미적용

- **위치**: `src/agents/graph/pipeline.py:127-163` (_route_after_validation)
- **위반 규칙**: 재시도 로직에 지수 백오프(exponential backoff) 적용
- **문제 설명**: SQL 검증 실패 시 `generate_sql` → `validate_sql` 루프를 즉시 재실행한다. LLM API에 대한 연속 호출에 딜레이가 없다.
- **개선 방법**: `generate_sql_node` 진입 시 `sql_retry_count > 0`이면 짧은 딜레이를 추가한다.

### W-04. `seed_sql_history.py`에서 f-string으로 SQL 생성

- **위치**: `src/tools/seed_sql_history.py:200-203`
- **위반 규칙**: SQL은 반드시 파라미터 바인딩 사용 (f-string, format() 금지)
- **문제 설명**: `query += f"\nLIMIT {self._limit}"`, `query += f"\nOFFSET {self._offset}"` — 값이 CLI 인자(`argparse`로 `int` 강제 변환)에서 오므로 인젝션 위험은 낮지만, 프로젝트 규칙 위반이다.
- **개선 방법**:
  ```python
  query = _EXTRACT_SQL + " LIMIT :limit OFFSET :offset"
  params = {"limit": self._limit or 10000, "offset": self._offset}
  result = await conn.execute(text(query), params)
  ```

### W-05. JOIN 조건 누락 검증 미구현

- **위치**: `src/agents/nodes/sql_validator.py`
- **위반 규칙**: 생성된 SQL에 JOIN 조건 누락 여부 검증 (Cartesian product 방지)
- **문제 설명**: LLM이 `SELECT * FROM A, B` 또는 `FROM A JOIN B` (ON 절 없이)를 생성할 경우 대량의 교차곱 결과가 반환될 수 있다. 관련 검증 로직이 없다.
- **개선 방법**: sqlglot AST를 활용하여 JOIN 테이블 수와 ON/WHERE 조건 수를 비교하는 검증 로직을 추가한다.
  ```python
  def _check_join_conditions(sql: str) -> list[str]:
      """JOIN 조건 누락을 검사한다."""
      errors = []
      parsed = sqlglot.parse_one(sql, dialect="postgres")
      joins = list(parsed.find_all(sqlglot.exp.Join))
      for join in joins:
          if join.args.get("on") is None and join.args.get("using") is None:
              errors.append("JOIN에 ON/USING 조건이 누락되었습니다 (Cartesian product 위험)")
      return errors
  ```

### W-06. GROUP BY / aggregation 컬럼 정합성 검증 미구현

- **위치**: `src/agents/nodes/sql_validator.py`
- **위반 규칙**: GROUP BY / aggregation 시 컬럼 정합성 검증
- **문제 설명**: `_is_aggregate_query`로 집계 여부를 판단하지만, GROUP BY에 포함되지 않은 비집계 컬럼이 SELECT에 있는지 검증하지 않는다. DB 실행 시 오류로 잡히지만, 사전 검증으로 불필요한 DB 호출을 줄이고 더 명확한 메시지를 제공할 수 있다.
- **개선 방법**: sqlglot AST로 SELECT 컬럼과 GROUP BY 컬럼의 정합성을 검증하는 로직을 추가한다.

### W-07. `Optional[str]` 사용 — Python 3.12 스타일 미준수

- **위치**: `src/agents/models/normalization.py:22` 및 파일 전체 (12곳 이상)
- **위반 규칙**: 명명 규칙 / 코드 스타일 일관성
- **문제 설명**: 프로젝트는 Python 3.12를 사용하며, 다른 파일에서는 `str | None` 구문을 사용한다. `normalization.py`에서만 `Optional[str]`을 사용하여 일관성이 깨져 있다.
- **개선 방법**: `from typing import Optional`을 제거하고, 모든 `Optional[X]`를 `X | None`으로 변경한다.

### W-08. `PipelineState.normalized_query` 타입이 `Any`

- **위치**: `src/agents/state/state.py:157`
- **위반 규칙**: 타입 안전성 / 타입 힌트 필수
- **문제 설명**: `normalized_query: Any = None`으로 선언되어 타입 안전성이 없다. 주석에 "순환 import 방지로 Any"라고 되어 있으나, Python 3.12에서는 `from __future__ import annotations`로 해결 가능하다.
- **개선 방법**: `TYPE_CHECKING` 블록에서 `NormalizedQuery`를 import하고 `normalized_query: NormalizedQuery | None = None`으로 변경한다.

### W-09. `sql_generator.py`의 과도한 `getattr` 사용

- **위치**: `src/agents/nodes/sql_generator.py:102-230`
- **위반 규칙**: 코드 품질 / 가독성
- **문제 설명**: `NormalizedQuery`는 Pydantic 모델이므로 모든 속성이 보장되는데, `getattr(nq, "intent", None)` 같은 방어적 접근을 30회 이상 반복한다. W-08에서 타입을 `Any`로 선언한 것에서 기인한 문제이다.
- **개선 방법**: W-08을 해결하면 `nq.intent`, `nq.entities` 등 직접 접근이 가능하여 코드가 크게 단순화된다.

### W-10. `tests/unit/` — 31개 파일 미분리

- **위치**: `tests/unit/` (31개 .py 파일)
- **위반 규칙**: 하나의 디렉토리가 10개 이상의 파일을 가지면 하위 디렉토리 책임 분리 검토
- **문제 설명**: 31개의 테스트 파일이 단일 디렉토리에 평탄하게 놓여 있다. 관련 테스트를 함께 찾기 어렵고, 파일 목록 탐색이 비효율적이다.
- **개선 방법**: 논리 그룹별 하위 디렉토리 분리.
  ```
  tests/unit/
  ├── nodes/       # 파이프라인 노드 테스트
  ├── search/      # 검색 품질 테스트
  ├── domain/      # 도메인 보조 테스트
  ├── infra/       # 인프라/추적 테스트
  └── security/    # 보안 테스트
  ```

### W-11. `standalone/scripts/` — 실행 맥락 혼재

- **위치**: `standalone/scripts/` (10개 파일: .py + .sh + .sql 혼재)
- **위반 규칙**: 동일 디렉토리 내 프로그램들이 실행 맥락이 같은가 / 10개 이상 파일 분리 검토
- **문제 설명**: 시딩 스크립트, DDL 생성, 데이터 증강, 초기화 SQL이 혼재. 시딩과 증강은 실행 시점과 목적이 다르다.
- **개선 방법**:
  ```
  standalone/scripts/
  ├── seed/        # seed_all.sh, seed_postgres.py, seed_elasticsearch.py, seed_qdrant.py
  ├── augment/     # augment_report_sql.py, augment_term_dict.py, enrich_sql_history.py
  └── schema/      # generate_all_ddl.py, init_postgres.sql
  ```

### W-12. `src/services/` 루트 — 검색 파이프라인 파일 미그룹화

- **위치**: `src/services/` (6개 서비스 파일)
- **위반 규칙**: 이 디렉토리의 파일들은 하나의 주요 책임을 공유하는가
- **문제 설명**: `search_query_builder.py`(774줄), `search_context_assembler.py`(487줄), `search_query_embedder.py`, `reranker.py`는 모두 검색 파이프라인 구성요소이다. 반면 `similar_table_resolver.py`와 `table_meta_enricher.py`는 도메인 로직 보강 역할이다.
- **개선 방법**: 검색 관련 파일들을 `src/services/search/`로 그룹화.
  ```
  src/services/
  ├── search/
  │   ├── query_builder.py
  │   ├── query_embedder.py
  │   ├── context_assembler.py
  │   └── reranker.py
  ├── similar_table_resolver.py
  ├── table_meta_enricher.py
  └── domain/
  ```

### W-13. 디렉토리명 오타

- **위치**: `docs/strategy-proposals/nl-query-normaliazion/`
- **위반 규칙**: 디렉토리 이름만 보고 역할을 추론할 수 있는가
- **문제 설명**: `normaliazion`은 `normalization`의 오타이다. 하위 파일명에도 동일한 오타가 반영되어 있다.
- **개선 방법**: `nl-query-normaliazion` → `nl-query-normalization`으로 디렉토리명 및 파일명 일괄 수정.

---

## 🟢 Info (참고) — 9건

### I-01. SQL 실행 3중 방어 양호

- **위치**: `src/agents/nodes/sql_validator.py`, `src/utils/security.py`, `src/connectors/postgres_connector.py`
- **상태**: sql_validator(FORBIDDEN_PATTERNS + sqlglot 파싱 + PII 검사) → security.validate_sql_safety(DML/DDL, 시스템카탈로그, 시간지연, 파일I/O) → connector(SELECT/WITH 시작 패턴 확인). 3중 방어가 잘 구현되어 있다.

### I-02. 모든 LLM 호출에 타임아웃 설정 완료

- **위치**: `src/utils/llm/retry.py`, 각 노드 파일
- **상태**: `llm_default_timeout`(15초), `llm_long_timeout`(30초)으로 모든 LLM 호출에 타임아웃이 적용되어 있다.

### I-03. PII 마스킹 체계 양호

- **위치**: `src/agents/nodes/preprocessor.py:74`, `src/main.py:153-174`
- **상태**: 주민등록번호, 카드번호, 계좌번호, 전화번호, 이메일에 대한 마스킹이 적용되어 있다. (W-01 예외)

### I-04. 모든 노드에 fallback 전략 구현

- **위치**: 각 노드 파일
- **상태**: 의도 분류 실패 시 legacy 폴백, 분석 JSON 파싱 실패 시 텍스트 폴백, 시각화 판단 실패 시 NONE 반환, SVG 생성 실패 시 템플릿 폴백, 테이블 설명 보강 실패 시 원본 유지.

### I-05. 프롬프트 인젝션 방어 양호

- **위치**: `src/utils/security.py`, `src/agents/nodes/preprocessor.py`
- **상태**: 영어/한국어/간접 주입/유니코드 정규화(normalize_unicode)를 통한 동형 문자 우회 방어가 구현되어 있다.

### I-06. `_logger` vs `logger` 명명 불일관

- **위치**: `src/services/domain/finance_terms.py:27`
- **문제**: 프로젝트 전체에서 모듈 레벨 로거는 `logger`로 명명하는데, 이 파일만 `_logger`를 사용한다.
- **개선**: `_logger`를 `logger`로 통일.

### I-07. 정규식 동적 컴파일 반복

- **위치**: `src/agents/nodes/sql_validator.py:202-228`
- **문제**: `_check_forbidden_patterns`에서 `re.search(pattern, sql)` 반복 호출, `_check_pii_columns`에서 25개 이상의 정규식 동적 컴파일. Python 내부 캐시가 있지만 `preprocessor.py`의 `_COMPILED_SUSPICIOUS`처럼 미리 컴파일하는 것이 일관적이다.
- **개선**: `FORBIDDEN_PATTERNS`과 `PII_COLUMNS`를 `re.compile()` 리스트로 변환.

### I-08. `chart_generator.py` 3개 차트 함수 간 SVG 레이아웃 코드 중복

- **위치**: `src/utils/chart_generator.py:84-349`
- **문제**: `generate_bar_chart`, `generate_line_chart`, `generate_pie_chart` 간 SVG 헤더, 제목, 축선 그리기 등 공통 로직이 반복된다.
- **개선**: `_render_svg_header()`, `_render_title()`, `_render_axes()` 등 공통 헬퍼 함수로 추출.

### I-09. `init_postgres.sql` 파일 중복

- **위치**: `standalone/scripts/init_postgres.sql`, `standalone/docker/scripts/init_postgres.sql`
- **문제**: 두 파일의 내용이 동일하다. 한쪽을 수정하면 다른쪽도 수정해야 하므로 동기화 누락 위험이 있다.
- **개선**: `standalone/docker/scripts/init_postgres.sql`을 정본으로 유지하고 다른쪽은 삭제.

---

## 우선 수정 권장 순서

| 순위 | ID | 내용 | 근거 |
|------|----|------|------|
| 1 | C-01, C-02 | import 경로 불일치 수정 | 파이프라인 런타임 동작 불가 |
| 2 | C-03, C-04 | DB/ES/Qdrant 타임아웃 설정 | 서비스 가용성 직접 영향 |
| 3 | C-05 | 신뢰도 임계값 체크 로직 추가 | 잘못된 SQL 실행 방지 |
| 4 | W-01 | runner.py PII 마스킹 로깅 | 금융 규정 준수 |
| 5 | C-06 | 역방향 의존 해소 | 아키텍처 건전성 |
| 6 | W-02, W-03 | 지수 백오프 적용 | API rate limit 방어 |
| 7 | W-05, W-06 | JOIN/GROUP BY 검증 추가 | SQL 품질 향상 |
| 8 | W-04 | f-string SQL 파라미터 바인딩 전환 | 보안 규칙 일관성 |
| 9 | W-07, W-08, W-09 | 타입 힌트 정리 | 코드 품질/유지보수성 |
| 10 | W-10, W-11, W-12 | 디렉토리 구조 정리 | 장기 유지보수성 |

---

## 양호 사항

전반적으로 보안 설계가 잘 되어 있다. 특히 다음 항목들은 체크리스트 기준을 충족한다.

- **SQL 보안**: 3중 검증 (sql_validator + security + connector SELECT 체크)
- **비밀정보 관리**: 모든 시크릿이 환경 변수/`.env`로 관리, 하드코딩 없음
- **LLM 타임아웃**: 모든 LLM 호출에 15초/30초 타임아웃 설정
- **에러 메시지 보안**: 내부 정보 노출 없이 일반 메시지만 사용자에게 전달
- **fallback 전략**: 모든 주요 노드에 실패 시 대체 경로 구현
- **DB/LLM 에러 구분**: 오류 유형별 구분 처리 및 사용자 메시지 차별화
- **LIMIT 강제**: SQL 검증 단계에서 LIMIT 존재 확인 + 실행 단계에서 max_query_rows 추가 제한
- **프롬프트 인젝션 방어**: 영어/한국어/간접 주입/유니코드 동형 문자 우회 방어
