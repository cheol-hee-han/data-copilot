# NL-to-SQL 파이프라인 단위 테스트 설계서

> 최종 갱신: 2026-03-22
> 작성 목적: 파이프라인 전 구간의 답변 정확도·품질에 영향을 미치는 13개 핵심 구간에 대한 단위 테스트 전략 및 모듈 설계를 문서화한다.

---

## 1. 개요

### 1.1 배경

Data Copilot은 사용자의 자연어 질의를 SQL로 변환하여 데이터를 추출·분석하는 AI 에이전트 서비스이다.
파이프라인은 전처리 → 의도분류 → 정규화 → 컨텍스트수집 → SQL생성 → 검증 → 실행 → 분석 → 포맷팅의 다단계 구조로 이루어지며,
각 단계의 in/out 품질이 최종 답변 정확도에 누적적으로 영향을 미친다.

### 1.2 목표

- 파이프라인 전 구간에 대한 **독립적 단위 테스트**를 통해 각 노드의 정상/오류 동작을 검증
- **실제 데이터** 기반 테스트(Mock/Dummy 미사용)로 현실적인 품질 검증
- **Tracker 수준 로깅**으로 테스트 in/out을 파일에 기록하여 모니터링 가능

### 1.3 설계 원칙

| 원칙 | 설명 |
|------|------|
| 실데이터 우선 | Mock/Dummy 대신 실제 LLM 호출, 실제 DB/ES/Qdrant 연동 |
| 격리 가능 | 외부 인프라 미연결 시 `@pytest.mark.skipif`로 안전하게 건너뜀 |
| 트래커 로깅 | 모든 테스트 케이스의 입출력을 `logs/test/{yyyymmdd}-{모듈명}.log`에 기록 |
| 순수함수 분리 | LLM 호출 없이 검증 가능한 파싱/검증/후처리 로직은 별도 테스트 |

---

## 2. 테스트 대상 구간 (13개)

아래 표는 NL-to-SQL 파이프라인에서 답변 정확도에 영향을 미치는 모든 구간을 나열한다.

| # | 구간 | 소스 파일 | 정확도 영향 | 테스트 모듈 |
|---|------|-----------|------------|------------|
| 1 | 입력 전처리 | `src/agents/nodes/preprocessor.py` | HIGH | `test_preprocess_node.py` |
| 2 | 보안 유틸리티 | `src/utils/security.py` | HIGH | `test_security_utils.py` |
| 3 | 의도 분류 | `src/agents/nodes/intent_classifier.py` | CRITICAL | `test_classify_intent.py` |
| 4 | 질의 정규화 | `src/agents/nodes/query_normalizer.py` | CRITICAL | `test_normalize_query.py` |
| 5 | 검색 쿼리 전략 | `src/services/search_query_builder.py` | HIGH | `test_search_query_builder.py` |
| 6 | 컨텍스트 수집 | `src/services/search_context_assembler.py` | CRITICAL | `test_context_collection.py` |
| 7 | 테이블 선택 검증 | `src/services/similar_table_resolver.py` | HIGH | `test_table_selection.py` |
| 8 | SQL 생성 | `src/agents/nodes/sql_generator.py` | CRITICAL | `test_generate_sql.py` |
| 9 | SQL 검증 | `src/agents/nodes/sql_validator.py` | CRITICAL | `test_validate_sql.py` |
| 10 | SQL 실행 | `src/agents/nodes/sql_executor.py` | MEDIUM | `test_execute_sql.py` |
| 11 | 데이터 분석 | `src/agents/nodes/analyzer.py` | MEDIUM | `test_analyze_data.py` |
| 12 | 결과 포맷팅 | `src/agents/nodes/formatter.py` | MEDIUM | `test_format_response.py` |
| 13 | 명확화 질문 | `src/agents/nodes/clarifier.py` | MEDIUM | `test_clarify_node.py` |

---

## 3. 디렉토리 구조

```
tests/
├── __init__.py
├── unit/
│   ├── __init__.py
│   ├── conftest.py                      # 공통 fixture (로거, 캐시, SLA, 로테이션)
│   │
│   │  ── 파이프라인 노드 테스트 (기존 + 보강) ──
│   ├── test_preprocessor.py             # 입력 전처리 (12 tests, 보강됨)
│   ├── test_classify_intent.py          # 의도 분류 (15 tests, 신규)
│   ├── test_query_normalizer.py         # 질의 정규화 (27 tests)
│   ├── test_search_query_builder.py           # 검색 쿼리 전략 (9 tests, 신규)
│   ├── test_context_collection.py       # 컨텍스트 수집 (9 tests, 신규)
│   ├── test_similar_table_resolver.py           # 테이블 선택 검증 (25 tests)
│   ├── test_sql_generator.py            # SQL 생성 (14 tests, 보강됨)
│   ├── test_sql_validator.py            # SQL 검증 (12 tests)
│   ├── test_sql_validator_aggregate.py  # SQL 집계 판별 (6 tests)
│   ├── test_sql_validator_edge_cases.py # SQL 검증 엣지 (31 tests)
│   ├── test_execute_sql.py              # SQL 실행 (11 tests, 신규)
│   ├── test_analyze_data.py             # 데이터 분석 (13 tests, 신규)
│   ├── test_format_response.py          # 결과 포맷팅 (11 tests, 신규)
│   ├── test_clarify_node.py             # 명확화 질문 (9 tests, 신규)
│   ├── test_security.py                 # 보안 유틸리티 (23 tests, 보강됨)
│   │
│   │  ── 보조 모듈 테스트 ──
│   ├── test_table_meta_enricher.py           # 테이블 설명 보강 (21 tests)
│   ├── test_chart_generator.py          # SVG 차트 생성 (24 tests)
│   ├── test_connectors.py               # 커넥터 Dummy 모드 (8 tests)
│   ├── test_finance_terms.py            # 도메인 사전 (7 tests)
│   ├── test_finance_terms_edge_cases.py # 도메인 사전 엣지 (30 tests)
│   ├── test_evaluation_tracker.py       # 평가 트래커 (15 tests)
│   ├── test_evaluator.py                # 평가 모듈 (8 tests)
│   ├── test_langsmith.py                # LangSmith 트레이싱 (6 tests)
│   ├── test_trace.py                    # 추론 추적 로그 (11 tests)
│   │
│   │  ── 횡단 테스트 (개선사항) ──
│   ├── test_node_chain.py               # 노드 연쇄 흐름 (5 tests, 신규)
│   └── test_edge_cases.py               # 전 구간 엣지 보강 (23 tests, 신규)
├── fixtures/
│   ├── llm_snapshot.py                  # LLM 응답 스냅샷 캐시
│   ├── real_queries.json                # 실 사용 로그 기반 테스트 데이터 (20건)
│   └── snapshots/                       # 캐시된 LLM 응답 (자동 생성)
├── integration/
│   └── test_pipeline_e2e.py             # E2E 통합 테스트
└── ...
```

**로그 출력 디렉토리:**

```
logs/test/
├── 20260322-test_preprocessor.log
├── 20260322-test_classify_intent.log
├── 20260322-test_query_normalizer.log
├── ...  (7일 초과 로그 자동 정리)
```

---

## 4. 모듈별 상세 설계

### 4.1 입력 전처리 — `test_preprocessor.py`

**테스트 대상**: `preprocess_node(state: PipelineState) → dict`

| 입력 (In) | 출력 (Out) | 검증 포인트 |
|-----------|-----------|------------|
| `user_input: str` | `preprocessed_input: str` | 유니코드 NFKC 정규화 |
| | `status: QueryStatus` | 연속 공백 단일화 |
| | `error_message: str` (오류 시) | 길이 제한 (500자) |

**테스트 케이스 (12개):**

| # | 케이스 | 입력 예시 | 기대 결과 |
|---|--------|----------|----------|
| 1 | 정상 한국어 질의 | "이번달 신규 고객 수 알려줘" | PREPROCESSING |
| 2 | 전각 문자 정규화 | "ｓｅｌｅｃｔ 데이터" | "select 데이터" |
| 3 | 공백 정리 | "이번달   신규    고객" | 단일 공백 |
| 4 | 길이 초과 | "가" × 501 | ERROR |
| 5 | 경계값 통과 | "가" × 500 | PREPROCESSING |
| 6-11 | SQL 인젝션 (6종) | "; DROP", "UNION SELECT", "--" 등 | ERROR |
| 12 | 프롬프트 인젝션 (7종) | "ignore instructions", "이전 지시 무시" 등 | ERROR |

### 4.2 보안 유틸리티 — `test_security_utils.py`

**테스트 대상**: `normalize_unicode`, `mask_pii`, `detect_prompt_injection`, `validate_sql_safety`

| 함수 | 테스트 수 | 핵심 검증 |
|------|----------|----------|
| `normalize_unicode` | 3 | 전각→반각, 제어문자 제거, 한국어 보존 |
| `mask_pii` | 7 | 주민번호·카드·전화·이메일·계좌 마스킹 |
| `detect_prompt_injection` | 8 | 영어/한국어/간접/유니코드 우회 감지 |
| `validate_sql_safety` | 8 | SELECT 허용, DML/DDL/주석/시스템카탈로그 차단 |

**PII 마스킹 규칙:**

```
860101-1234567  →  86****-*****67  (주민등록번호)
1234-5678-9012-3456  →  12**-****-****-**56  (카드번호)
010-1234-5678  →  01*-****-**78  (전화번호)
user@example.com  →  us**@*******.**m  (이메일)
```

### 4.3 의도 분류 — `test_classify_intent.py`

**테스트 대상**: `classify_intent_node(state) → dict`

| 유형 | 테스트 수 | 환경 |
|------|----------|------|
| 순수 함수 (파싱/매핑) | 12 | LLM 불필요 |
| LLM 실제 호출 | 3 | `ANTHROPIC_API_KEY` 필요 |

**Intent Gate 카테고리 매핑:**

```
DATA_QUERY    → DATA_EXTRACTION / DATA_ANALYSIS (세분류)
CASUAL_TALK   → CASUAL_TALK
META_QUESTION → META_QUESTION
CLARIFICATION → CLARIFICATION_NEEDED
AMBIGUOUS     → CLARIFICATION_NEEDED
```

### 4.4 질의 정규화 (8-Slot) — `test_normalize_query.py`

**테스트 대상**: `normalize_query_node`, `_validate_structure`, `_postprocess`, `_parse_llm_json`

| 유형 | 테스트 수 | 핵심 검증 |
|------|----------|----------|
| JSON 파싱 | 3 | 코드펜스 제거, 잘못된 JSON 예외 |
| Enum 검증 | 4 | 대소문자 보정, 잘못된 값 기본값 대체 |
| 후처리 | 4 | 집계함수 자동보정, RANK by 채움, 불용어 제거 |
| LLM 통합 | 2 | 실제 8-Slot 분해 검증 |

**8-Slot 구조:**

```
1. INTENT: EXTRACT | AGGREGATE | COMPARE | TREND | RANK | ...
2. ENTITY: 대상 테이블 (confidence: HIGH/MEDIUM/LOW)
3. MEASURE: 측정값 + 집계함수 (SUM/AVG/COUNT/...)
4. DIMENSION: 분류축 + 세분도
5. FILTER: 조건 (EQUALS/RANGE/LIKE/...)
6. TIME: 시간 범위 (ABSOLUTE/RELATIVE/COMPARISON)
7. MODIFIER: 결과 가공 (SORT/LIMIT/RANK/...)
8. OUTPUT_HINT: 출력 형식
```

### 4.5 검색 쿼리 전략 — `test_search_query_builder.py`

**테스트 대상**: `build_source_queries_with_normalization(query, normalized_query)`

**테스트 케이스 (9개):**

| # | 케이스 | 검증 |
|---|--------|------|
| 1 | 기본 쿼리 | 모든 소스 쿼리 필드 생성됨 |
| 2 | 대출 도메인 매칭 | 여신 카테고리, LOAN 테이블 추출 |
| 3 | 고객 도메인 매칭 | 고객 카테고리, CUST 테이블 추출 |
| 4 | 빈 쿼리 | 에러 없이 최소 결과 반환 |
| 5 | 도메인 사전 매칭 | matched_terms > 0 |
| 6 | NQ 보강 | search_keywords 반영 검증 |
| 7 | 불용어 제거 | "주세요", "알려줘" 제거 |
| 8 | 카테고리→도메인코드 | 여신→LON, 수신→DEP 매핑 |
| 9 | 동의어 확장 | expanded >= core keywords |

### 4.6 컨텍스트 수집 — `test_context_collection.py`

**테스트 대상**: `collect_context(query, tracker, normalized_query)`

6개 병렬 소스:

```
┌─ ES table_meta ────────┐
├─ ES report_sql ────────┤
├─ PG history_db ────────┼── asyncio.gather() ──→ ContextInfo
├─ Qdrant biz_manual ────┤
├─ Qdrant sql_history ───┤
└─ ES code_meta ─────────┘
```

- 인프라 연결 시: 각 소스 반환값 검증 (9 tests)
- 오프라인: ContextInfo 모델 기본값·직렬화 검증

### 4.7 테이블 선택 검증 — `test_table_selection.py`

**테스트 대상**: `extract_tables_from_sql`, `find_relevant_groups`, `validate_table_selection`, `score_table_for_query`, `check_rejected_tables`

| 함수 | 테스트 수 | 핵심 검증 |
|------|----------|----------|
| extract_tables_from_sql | 5 | FROM/JOIN 추출, TB_ 필터, 중복 제거 |
| find_relevant_groups | 2 | 유사 그룹 감지/미감지 |
| validate_table_selection | 5 | PASS/WARNING/AMBIGUOUS 판정 |
| score_table_for_query | 2 | 신호어 증가, 부적합 감소 |
| check_rejected_tables | 5 | 거부 테이블 사용 여부 검증 |
| build_disambiguation_prompt | 3 | 빈 그룹, 가이드 텍스트 생성 |

### 4.8 SQL 검증 — `test_validate_sql.py`

**테스트 대상**: `validate_sql_node`, 금지 패턴·구문·PII·LIMIT 검증

| 검증 레이어 | 테스트 수 | 패턴 |
|------------|----------|------|
| 금지 패턴 (13종) | 12 | DML/DDL, 주석, UNION, SLEEP, 파일I/O 등 |
| SQL 구문 (SQLGlot) | 2 | 유효/무효 구문 파싱 |
| PII 컬럼 | 3 | JUMIN_NO, CARD_NO 등 직접 조회 차단 |
| LIMIT 검사 | 3 | 비집계 LIMIT 필수, 집계 예외 |
| 전체 노드 | 5 | 통과/실패/빈SQL/재시도 피드백 |

### 4.9 SQL 생성 — `test_generate_sql.py`

**테스트 대상**: `generate_sql_node`, `_clean_sql_response`, `_build_*` 헬퍼

- 순수 함수 (9): SQL 응답 정리, 테이블 정보 포맷, 과거 SQL 중복 제거
- LLM 통합 (4): 실제 SQL 생성, SELECT 검증, 재시도 카운터

### 4.10 SQL 실행 — `test_execute_sql.py`

**테스트 대상**: `execute_sql_node(state)`

- 안전성 이중검증 (`validate_sql_safety` 재호출)
- 결과 행 수 제한 (`max_query_rows`)
- SQLResult 필드 검증 (columns, rows, row_count, execution_time_ms)

### 4.11 데이터 분석 — `test_analyze_data.py`

**테스트 대상**: `analyze_data_node`, `_parse_analysis_json`, `_parse_viz_judgment`

- 분석 JSON 파싱: 코드펜스 처리, 잘못된 JSON 폴백
- 시각화 판단: CHART_TYPE 파싱, 최소 행 수 미만 시 스킵
- LLM 통합: 실제 인사이트 생성 검증

### 4.12 결과 포맷팅 — `test_format_response.py`

**테스트 대상**: `format_response_node`, `_format_result_for_prompt`

- 보고서 형태 변환 검증
- 빈 결과 처리
- trace_log → `<details>` 접기 블록 추가 검증

### 4.13 명확화 질문 — `test_clarify_node.py`

**테스트 대상**: `clarify_node`, `_build_messages`

- 메시지 조립: 히스토리 최근 4턴 제한, role 유지
- LLM 통합: 명확화 질문 생성, `awaiting_clarification=True` 설정

---

## 5. 공통 인프라

### 5.1 conftest.py — 테스트 로깅 유틸리티

```python
# tests/unit/conftest.py

get_test_logger(module_name)  → logging.Logger
    # 로그 파일: logs/test/{yyyymmdd}-{module_name}.log

log_test_case(logger, test_name, input, expected, actual, passed)
    # 입출력을 Tracker 수준으로 기록
```

**로그 포맷 예시:**

```
[2026-03-22 14:30:15] INFO     | ========================================================================
  TEST: test_normal_input
  STATUS: PASS ✓
  INPUT:    이번달 신규 고객 수 알려줘
  EXPECTED: PREPROCESSING
  ACTUAL:   PREPROCESSING
========================================================================
```

### 5.2 pytest 마커

| 마커 | 용도 | skip 조건 |
|------|------|----------|
| `@pytest.mark.live_llm` | 실제 LLM 호출 테스트 | `ANTHROPIC_API_KEY` 미설정 |
| `@pytest.mark.live_infra` | 외부 인프라 연동 테스트 | ES/PG/Qdrant 미연결 |
| `@pytest.mark.asyncio` | 비동기 테스트 | (자동) |

---

## 6. 실행 방법

### 전체 실행

```bash
python -m pytest tests/unit/ -v -s
```

### 순수 함수 테스트만 (LLM/인프라 불필요)

```bash
python -m pytest tests/unit/ -v -k "not live_llm and not live_infra"
```

### 특정 모듈 실행

```bash
python -m pytest tests/unit/test_validate_sql.py -v -s
```

### LLM 테스트 포함 실행

```bash
ANTHROPIC_API_KEY=sk-... python -m pytest tests/unit/ -v -m live_llm
```

---

## 7. 테스트 커버리지 요약

### 7.1 파이프라인 노드별 (13개 구간)

| 구간 | 테스트 파일 | 테스트 수 | 비고 |
| --- | --- | --- | --- |
| 전처리 | `test_preprocessor.py` | 12 | 기존 5 + 보강 7 |
| 보안 유틸 | `test_security.py` | 23 | 기존 9 + 보강 14 |
| 의도 분류 | `test_classify_intent.py` | 15 | 신규 |
| 질의 정규화 | `test_query_normalizer.py` | 27 | 기존 |
| 검색 전략 | `test_search_query_builder.py` | 9 | 신규 |
| 컨텍스트 수집 | `test_context_collection.py` | 9 | 신규 |
| 테이블 선택 | `test_similar_table_resolver.py` | 25 | 기존 |
| SQL 생성 | `test_sql_generator.py` | 14 | 기존 6 + 보강 8 |
| SQL 검증 | `test_sql_validator*.py` (3파일) | 49 | 기존 |
| SQL 실행 | `test_execute_sql.py` | 11 | 신규 |
| 데이터 분석 | `test_analyze_data.py` | 13 | 신규 |
| 결과 포맷팅 | `test_format_response.py` | 11 | 신규 |
| 명확화 질문 | `test_clarify_node.py` | 9 | 신규 |

### 7.2 보조 모듈

| 대상 | 테스트 파일 | 테스트 수 |
| --- | --- | --- |
| 테이블 설명 보강 | `test_table_meta_enricher.py` | 21 |
| SVG 차트 생성 | `test_chart_generator.py` | 24 |
| 커넥터 Dummy | `test_connectors.py` | 8 |
| 도메인 사전 | `test_finance_terms*.py` (2파일) | 37 |
| 평가 트래커 | `test_evaluation_tracker.py` | 15 |
| 평가 모듈 | `test_evaluator.py` | 8 |
| LangSmith | `test_langsmith.py` | 6 |
| 추론 추적 | `test_trace.py` | 11 |

### 7.3 횡단 테스트

| 대상 | 테스트 파일 | 테스트 수 |
| --- | --- | --- |
| 노드 연쇄 흐름 | `test_node_chain.py` | 5 |
| 전 구간 엣지 | `test_edge_cases.py` | 23 |

### 7.4 총계

26개 테스트 파일, 385개 테스트 함수

---

## 8. 적용된 개선사항

비판적 검토에서 식별된 7개 개선사항 중 6개를 적용하였다 (#2 Docker Compose 는 본 환경이 이미 테스트 컨테이너이므로 불필요).

### 8.1 개선 1: LLM 응답 스냅샷 캐시 (flaky 방지) ✅

- **파일**: `tests/fixtures/llm_snapshot.py`
- **사용**: `llm_cache` pytest fixture (conftest.py 제공)
- **동작**: 첫 실행 시 LLM 응답을 `tests/fixtures/snapshots/{module}/{key}.json` 에 저장, 이후 재사용
- **갱신**: `LLM_SNAPSHOT_UPDATE=1` 환경 변수로 강제 갱신

### 8.2 개선 3: 2-노드 연쇄 흐름 테스트 ✅

- **파일**: `tests/unit/test_node_chain.py` (5 tests)
- **검증 연쇄**:
  - preprocessor → intent_classifier (preprocessed_input 전달)
  - sql_validator → sql_generator (validation_feedback 재시도)
  - sql_validator → sql_executor (validated_sql 전달)
  - 명확화 왕복 (clarify → user → preprocess 재진입)
  - QueryStatus 상태 전이 정합성

### 8.3 개선 4: 부정 케이스(edge case) 보강 ✅

- **파일**: `tests/unit/test_edge_cases.py` (23 tests)
- **보강 구간**:
  - 의도 분류: 다국어 혼합, 빈 Gate 응답, 중첩 JSON
  - 질의 정규화: 빈 슬롯, 유효하지 않은 modifier, 후행 텍스트 JSON
  - SQL 검증: CTE 내부 DML, 중첩 서브쿼리, 주석 키워드 분할, 전각 SQL
  - 분석: 전체 NULL 데이터, 비dict JSON
  - 보안: PII 겹침, 줄바꿈 인젝션, 빈/공백 SQL
  - 포맷팅: 빈 컬럼, 대량 행 제한

### 8.4 개선 5: 성능 SLA assertion ✅

- **구현**: `SLATimer` 클래스 + `sla_timer` pytest fixture (conftest.py)
- **사용 예**:

  ```python
  def test_perf(sla_timer):
      with sla_timer("전처리", max_ms=100):
          result = await preprocess_node(state)
  ```

- SLA 위반 시 `AssertionError` 와 함께 경과 시간/제한 시간 표시

### 8.5 개선 6: 실 사용 로그 기반 테스트 데이터 ✅

- **파일**: `tests/fixtures/real_queries.json` (20건)
- **fixture**: `real_queries` session-scoped pytest fixture
- **카테고리 분포**: 고객(3), 여신(7), 수신(2), 카드(1), 외환(1), 금융지표(2), 일반/모호(4)
- **복잡도 분포**: trivial(1), simple(4), medium(5), complex(4), ambiguous(2), typo(1), domain_specific(2)

### 8.6 개선 7: 로그 로테이션 ✅

- **구현**: `cleanup_old_logs` session-scoped autouse fixture (conftest.py)
- **동작**: 매 테스트 세션 시작 시 `logs/test/` 에서 7일 초과 로그 자동 삭제
- **조절**: `TEST_LOG_RETENTION_DAYS` 환경 변수 (기본 7일)
