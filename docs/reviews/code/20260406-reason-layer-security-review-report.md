# Reason 계층 코드 리뷰 보고서

- 일자: 2026-04-06
- 리뷰 대상: Reason 계층 노드 11개 + 서비스 4개 (총 15개 파일)
- 리뷰 중점: 보안(최우선), 성능, 타입, 에러 처리, 아키텍처, 금융 도메인

---

## 재검증 결과 (2026-04-06 2차 검토)

아래 이슈들은 실제 코드와 대조 검증한 결과, 심각도 조정이 필요한 것으로 확인되었습니다.

| 원래 ID | 판정 | 사유 |
|---------|------|------|
| **C-01** | **유지 (설명 보정)** → ⏸️ **미적용/TODO (A-1)** | 수동 sanitize(L187 `replace("'","''"`)는 존재하나 파라미터 바인딩 대비 불충분. 다만 화이트리스트+이스케이프+읽기전용+LLM출력으로 실질적 위험 극히 낮아 **미적용** 결정. 폐쇄망 배포 후 재검토 |
| **C-02** | **Critical → Warning** → ⏸️ **미적용/TODO (A-1)** | 식별자 영역(테이블명/컬럼명)은 파라미터 바인딩 불가. `_IDENT_RE` 화이트리스트가 방어. A-1과 동일하게 **미적용** |
| **C-04** | **Critical → Warning** | `get_sample_rows`는 내부 탐색 도구 용도(LLM이 테이블 구조 파악). 사용자 직접 노출 경로 미확인. 위험은 있으나 즉시 Critical은 아님 |
| **W-01** | ✅ **적용 완료 (A-6)** | security.py에서 UNION 차단 패턴 제거. input_sanitizer는 유지. LLM 생성 SQL의 정당한 UNION ALL 허용 |
| **W-06** | ❌ **제외** | LLM이 action을 오타낸 것이지 해결 불가능한 상황이 아님. `"replan"` 보정이 올바른 동작. LoopGuard가 최종 횟수 제한 보장. 폐쇄망 모델은 키워드 오타 확률 높아 replan 보정이 더 적절 |

---

## Critical (RED)

### C-01. SQL 인젝션 취약점 — search_column_values의 LIKE 키워드 직접 삽입

- 파일: `src/agents/nodes/reason/tools.py` (Line 187~207)
- 등급: CRITICAL (보안)
- 현상: `search_column_values`에서 `keyword`를 `sanitized_kw = keyword.replace("'", "''").replace("\\", "\\\\")`로만 처리한 뒤 f-string으로 SQL에 직접 삽입한다. 이 방식은 다음 공격을 방어하지 못한다.
  - LIKE 와일드카드 인젝션: `%` 또는 `_`가 포함된 키워드로 전체 데이터 스캔 유도 가능
  - 테이블명/컬럼명은 `_IDENT_RE`로 검증하지만 **keyword는 사용자 질의에서 파생된 값**이므로 LLM이 악의적 값을 전달할 가능성이 있다
  - DB별(Sybase IQ, Impala) escape 문법이 다르므로 단순 문자열 치환으로는 불충분
- 해결: **파라미터 바인딩 사용 필수**. DB 커넥터가 파라미터 바인딩을 지원하도록 `execute_query(sql, params)` 인터페이스를 확장하고, 모든 사용자 유래 값은 바인딩으로 전달해야 한다. LIKE 와일드카드(`%`, `_`)는 escape 함수를 별도 적용한다.

```python
# 현재 (취약)
sql = f"... WHERE {column_name} LIKE '%{sanitized_kw}%' ..."

# 권장
escaped_kw = keyword.replace("%", "\\%").replace("_", "\\_")
sql = f"... WHERE {column_name} LIKE :pattern ESCAPE '\\' ..."
params = {"pattern": f"%{escaped_kw}%"}
result = await db.execute_query(sql, params)
```

### C-02. SQL 인젝션 취약점 — DB 직접 조회 도구의 f-string SQL 생성

- 파일: `src/agents/nodes/reason/tools.py` (Line 147~148, 194~207, 249~257, 318~326)
- 등급: CRITICAL (보안)
- 현상: `get_sample_rows`, `get_column_profile`, `get_date_distribution` 모두 식별자 검증(`_IDENT_RE`) 후 f-string으로 SQL을 조립한다. `_IDENT_RE`는 `^[A-Za-z_]\w*$` 패턴으로, **한글이나 숫자로 시작하는 식별자를 허용하지 않아** 한국어 테이블/컬럼명이 있을 경우 기능이 동작하지 않을 수 있다.
- 또한 `limit` 파라미터도 f-string에 직접 삽입되고 있으나, 현재는 기본값이 코드에 하드코딩되어 있어 즉시 위험은 낮다. 그러나 향후 사용자 입력이 전달될 경우를 대비해 int 타입 강제 검증이 필요하다.
- 해결:
  1. `_IDENT_RE`를 `^[A-Za-z_\uAC00-\uD7A3]\w*$` 등으로 확장하거나, DB의 quoted identifier(`"테이블명"`)를 사용
  2. `limit` 파라미터에 `int()` 캐스팅 + 범위 검증 추가
  3. 장기적으로 모든 DB 조회 도구에 파라미터 바인딩 적용

### C-03. Layer 3 실행 검증에서 원본 SQL을 서브쿼리로 감쌀 때 인젝션 가능

- 파일: `src/agents/nodes/reason/sql_validator.py` (Line 511~515)
- 등급: CRITICAL (보안)
- 현상: `_validate_layer3`에서 `SELECT * FROM ({sql}) _t LIMIT 5` 형태로 원본 SQL을 서브쿼리로 감싸 실행한다. 이 시점에서 `sql`은 LLM이 생성한 문자열이며, Layer 1의 safety check를 통과했더라도 **실행 시점에서 DB에 전달되는 SQL 자체에 대한 이중 방어가 없다**.
- 해결: Layer 3 실행 직전에 `check_sql_safety_quick()` (src/utils/security.py)을 한 번 더 호출하는 이중 방어 추가. sql_executor에서도 동일 검증이 있으므로 validator에서도 동일 수준의 방어를 적용해야 한다.

```python
from src.utils.security import check_sql_safety_quick

async def _validate_layer3(sql: str, reason: ReasoningState) -> dict:
    is_safe, errors = check_sql_safety_quick(sql)
    if not is_safe:
        return {"status": "FAIL", "failure_type": FailureType.DB_ERROR,
                "feedback": "; ".join(errors)}
    # ... 기존 로직
```

### C-04. PII 컬럼 검증이 SELECT * 를 허용

- 파일: `src/services/sql_safety_checker.py` (Line 142~148)
- 등급: CRITICAL (보안/금융 규정)
- 현상: `check_pii_columns`는 SQL 문자열에서 `\bCOL_NAME\b` 패턴으로 PII 컬럼을 검색한다. 그러나 `SELECT *`로 전체 컬럼을 조회하면 PII 컬럼이 SQL 텍스트에 명시적으로 나타나지 않으므로 검증을 우회한다. `SELECT * FROM customer` 같은 쿼리가 주민번호, 계좌번호 등을 모두 반환할 수 있다.
- 해결: `SELECT *` 사용 시 explored_tables의 컬럼 목록에서 PII 컬럼 포함 여부를 확인하는 추가 검증이 필요하다. 혹은 `SELECT *`를 아예 금지하는 정책을 FORBIDDEN_SQL_PATTERNS에 추가한다.

```python
# sql_safety_checker.py에 추가
(r"\bSELECT\s+\*\s+FROM\b",
 "SELECT * 는 개인정보 노출 위험이 있습니다. 필요한 컬럼만 명시하세요"),
```

---

## Warning (YELLOW)

### W-01. UNION SELECT 차단이 정상적인 UNION ALL 사용도 막음

- 파일: `src/utils/security.py` (Line 156~158)
- 등급: WARNING (기능 제한)
- 현상: `FORBIDDEN_SQL_PATTERNS`에 `\bUNION\s+(?:ALL\s+)?SELECT\b` 패턴이 있어, 데이터를 합치기 위한 정상적인 `UNION ALL SELECT` 사용도 차단된다. 금융 도메인에서 여러 테이블의 데이터를 합치는 것은 흔한 패턴이다.
- 해결: UNION 차단 정책을 재검토하거나, sql_safety_checker에서 UNION이 사용된 경우 서브쿼리가 모두 SELECT인지 확인하는 정밀 검증으로 대체한다. 현재 상태로는 복잡한 질의에 대한 SQL 생성이 불가능할 수 있다.

### W-02. response_formatter에서 LLM 호출 시 타임아웃만 있고 재시도 없음

- 파일: `src/services/response_formatter.py` (Line 124~132)
- 등급: WARNING (에러 처리)
- 현상: `format_response`에서 `client.messages.create`를 직접 호출하면서 `try/except` 없이 예외가 상위로 전파된다. 다른 서비스들은 `llm_call_with_parse_retry`를 사용하여 재시도 + 파싱 로직이 내장되어 있는데, 이 함수만 raw client를 직접 호출한다.
- 해결: `llm_call_with_parse_retry` 사용으로 통일하거나, 최소한 `try/except`로 예외를 잡아 사용자 친화적 에러 메시지를 반환해야 한다.

```python
try:
    response = await client.messages.create(...)
except Exception as e:
    logger.error("포맷팅 LLM 호출 실패", error=str(e))
    return f"데이터를 조회했으나 보고서 형태로 변환하는 중 오류가 발생했습니다. 원본 데이터: {format_result_for_prompt(sql_result)[:500]}"
```

### W-03. generate_svg_via_llm에서도 raw client 직접 호출

- 파일: `src/services/data_analyzer.py` (Line 149~167)
- 등급: WARNING (에러 처리/일관성)
- 현상: W-02와 동일하게 `client.messages.create`를 직접 호출한다. 재시도 로직이 없고, 예외 발생 시 빈 문자열만 반환하여 원인 파악이 어렵다.
- 해결: `llm_call_with_parse_retry` 사용으로 통일하여 재시도 + 구조화된 에러 처리 적용.

### W-04. context_interpreter의 토큰 예산 추정이 부정확

- 파일: `src/agents/nodes/reason/context_interpreter.py` (Line 263~265)
- 등급: WARNING (성능)
- 현상: `_estimate_tokens` 함수가 `len(text) // 3`으로 토큰을 추정하지만, 실제로 한국어 텍스트는 1토큰당 약 1.5~2자, 영어는 약 4자 정도이다. 한/영 혼합 비율에 따라 추정치가 크게 달라질 수 있어 Level 0/1 분기 결정이 부정확할 수 있다.
- 해결: 함수 자체를 사용하지는 않고 `_TOKEN_BUDGET_CHARS` 상수로 문자 수 기반 비교만 하고 있으므로, 상수명을 `_CHAR_BUDGET`으로 변경하고 주석에서 토큰 추정 언급을 제거하여 혼동을 방지한다. 또는 `tiktoken` 등을 사용한 정밀 추정으로 전환한다.

### W-05. sql_validator Layer 2b에서 except 범위가 과도하게 넓음

- 파일: `src/agents/nodes/reason/sql_validator.py` (Line 479~490)
- 등급: WARNING (에러 처리)
- 현상: `except (ParseError, Exception) as e`에서 `Exception`이 `ParseError`를 포함하므로 `ParseError`를 별도 나열하는 것이 무의미하다. 또한 모든 예외를 FAIL + structural로 처리하면 네트워크 일시 장애 같은 일시적 오류에도 SQL이 구조적 문제로 분류된다.
- 해결: 일시적 오류(TimeoutError, ConnectionError 등)는 별도 처리하여 재시도 가능하도록 하고, LLM 응답 파싱 오류만 structural FAIL로 처리한다.

```python
except ParseError:
    # LLM 응답 파싱 실패 — structural FAIL
    ...
except (TimeoutError, ConnectionError) as e:
    # 일시적 오류 — PASS로 처리하고 Layer3에 위임
    logger.warning("Layer2b 일시적 오류, Layer3에 위임", error=str(e))
    return {"status": "PASS", "passed": [], "failed": [], "checks": {}}
except Exception as e:
    # 기타 오류
    ...
```

### W-06. recovery_agent의 _parse_plan_response에서 action 값 보정이 역방향

- 파일: `src/agents/nodes/reason/recovery_agent.py` (Line 276~277)
- 등급: WARNING (로직)
- 현상: `if action not in ("replan", "give_up"): action = "replan"`으로 보정하는데, 알 수 없는 action을 "replan"(재시도)으로 보정하면 무한 루프 위험이 있다. "give_up"으로 보정하는 것이 안전하다.
- 해결: 안전한 방향(give_up)으로 보정하거나, 루프 가드에서 이미 replan 횟수를 제한하고 있으므로 현재는 무한 루프는 아니지만, 불필요한 LLM 호출이 발생할 수 있다.

### W-07. context_retriever에서 explored_tables 리스트를 직접 변경하며 동시성 위험

- 파일: `src/agents/nodes/reason/context_retriever.py` (Line 431~434)
- 등급: WARNING (동시성)
- 현상: `asyncio.gather`로 병렬 실행하는 `_run_step` 함수들이 공유 리스트(`explored_tables`, `executed_tool_keys`)를 동시에 변경한다. Python의 GIL이 리스트 append를 atomic하게 만들어주지만, `set.add`와 리스트 순회가 동시에 일어나면 예측하기 어려운 동작이 발생할 수 있다.
- 해결: `asyncio.gather` 대신 `asyncio.Semaphore`로 동시 실행 수를 제한하거나, 각 step 결과를 독립적으로 수집한 후 메인 루프에서 순차적으로 병합한다.

### W-08. data_analyzer에서 JSON 파싱 로직이 extract_json과 중복

- 파일: `src/services/data_analyzer.py` (Line 73~99)
- 등급: WARNING (코드 중복)
- 현상: `parse_analysis_json` 함수에서 코드 블록 추출, JSON 파싱 등의 로직이 `src/utils/llm/response.py`의 `extract_json`과 중복된다. 다른 파싱 함수들은 모두 `extract_json`을 사용하는데 이 함수만 자체 구현을 사용한다.
- 해결: `extract_json` 유틸리티로 통일한다.

```python
def parse_analysis_json(text: str) -> AnalysisResult:
    data = extract_json(text)
    if not data or not isinstance(data, dict):
        raise ValueError("분석 JSON 파싱 실패")
    return AnalysisResult(
        summary=data.get("summary", ""),
        insights=data.get("insights", []),
        statistics=data.get("statistics", {}),
    )
```

### W-09. insight_builder에서 _get_attr_or_key 과다 사용으로 타입 안전성 저하

- 파일: `src/services/insight_builder.py` (전체)
- 등급: WARNING (타입 안전성)
- 현상: `build_insight` 함수가 `state: dict[str, Any]`를 받아 `_get_attr_or_key`로 모든 접근을 처리한다. State가 Pydantic 모델과 dict 양쪽에서 올 수 있기 때문이지만, 이로 인해 타입 힌트가 무력화되고 필드명 오타를 컴파일 타임에 잡을 수 없다.
- 해결: 함수 시그니처를 `state: PipelineState | dict[str, Any]`로 명확히 하고, Pydantic 모델인 경우 `.model_dump()` 변환을 함수 진입점에서 한 번만 수행한 뒤 dict로 통일한다. 또는 ReasoningState를 직접 받는 별도 내부 함수를 두어 타입 안전한 경로를 확보한다.

### W-10. sql_safety_checker에서 LIMIT 누락을 에러로 처리하나 프롬프트가 LIMIT 포함을 유도하지 않을 수 있음

- 파일: `src/services/sql_safety_checker.py` (Line 207~216)
- 등급: WARNING (UX/성능)
- 현상: LIMIT 절이 없으면 검증 실패로 처리하는데, LLM이 LIMIT을 포함하지 않는 SQL을 생성할 경우 매번 검증 실패 -> 재생성 루프에 빠진다. 금융 도메인에서 집계 쿼리가 아닌 상세 조회는 항상 LIMIT이 필요하므로 정책 자체는 올바르나, **sql_generator 프롬프트에서 LIMIT 포함을 명시적으로 지시하고 있는지** 확인이 필요하다.
- 해결: sql_generator 시스템 프롬프트에 "집계가 아닌 조회에는 반드시 LIMIT 10000을 포함하세요" 문구가 포함되어 있는지 확인하고, 없으면 추가한다.

---

## Info (GREEN)

### I-01. _format_table_for_sql_prompt가 sql_generator에 정의되어 있으나 sql_validator에서도 import

- 파일: `src/agents/nodes/reason/sql_generator.py` (Line 71), `sql_validator.py` (Line 42~43)
- 등급: INFO (아키텍처)
- 현상: `_format_table_for_sql_prompt`는 프라이빗 함수(`_` 접두사)이지만 sql_validator에서도 import하여 사용한다. 프라이빗 함수의 크로스 모듈 참조는 모듈 경계를 위반한다.
- 해결: 이 함수를 공통 유틸리티(예: `src/agents/nodes/reason/formatters.py` 또는 `tool_renderers.py`)로 이동하고, 접두사 `_`를 제거하여 public 함수로 선언한다.

### I-02. tools.py의 _safe_search에서 f-string 로깅

- 파일: `src/agents/nodes/reason/tools.py` (Line 77)
- 등급: INFO (로깅 패턴)
- 현상: `logger.warning(f"{tool_name} 실패", error=str(e))`에서 f-string을 사용하고 있다. structlog의 lazy formatting을 활용하려면 `logger.warning("도구 실패", tool=tool_name, error=str(e))` 형태가 더 적합하다.
- 해결: 프로젝트 전체 로깅 패턴과 통일하여 structlog의 키-값 형태로 수정.

### I-03. context_interpreter에서 `_estimate_tokens` 함수가 정의만 되고 직접 호출되지 않음

- 파일: `src/agents/nodes/reason/context_interpreter.py` (Line 263~265)
- 등급: INFO (데드 코드)
- 현상: `_estimate_tokens` 함수가 정의되어 있지만 실제 분기 로직(`_interpret_batch`)에서는 `len(tool_results_str) > _TOKEN_BUDGET_CHARS`로 문자 수를 직접 비교한다. 함수가 호출되는 곳이 없다.
- 해결: 사용하지 않는 함수 제거.

### I-04. readiness_gate에서 SelectionStatus를 상단 import와 함수 내부에서 이중 import

- 파일: `src/agents/nodes/reason/readiness_gate.py` (Line 38, 163)
- 등급: INFO (코드 정리)
- 현상: 모듈 상단에서 `SelectionStatus`를 import하고 있으나, `_set_failure_context` 함수 내부에서는 import 없이 사용하고 있고, `_collect_stats`에서도 직접 사용한다. 일관성 있게 모듈 상단 import를 사용하면 된다.

### I-05. recovery_agent에서 SelectionStatus를 함수 내부에서 lazy import

- 파일: `src/agents/nodes/reason/recovery_agent.py` (Line 384, 496)
- 등급: INFO (import 패턴)
- 현상: `_build_prompt`와 `_build_sample_summary`에서 `from src.agents.state.state import SelectionStatus`를 함수 내부에서 import하고 있다. 이미 파일 상단에서 많은 state 모델을 import하고 있으므로 여기에 추가하면 된다.
- 해결: 파일 상단 import에 `SelectionStatus` 추가하고 함수 내부 lazy import 제거.

### I-06. tool_renderers.py에서 Callable 타입 힌트에 반환값 누락

- 파일: `src/agents/nodes/reason/tool_renderers.py` (Line 20)
- 등급: INFO (타입 힌트)
- 현상: `from typing import Any, Callable`을 import하지만 `_TOOL_RENDERERS`의 타입이 `dict[str, Callable[[ExecutionStep], str]]`로 정확하게 명시되어 있다. 다만 `Callable`이 사용되고 있지 않은 것처럼 보이므로 확인 필요 -- 실제로는 Line 408에서 사용 중이므로 문제 없음.

### I-07. result_finalizer의 _build_conflicted_signals에서 타입 힌트 부재

- 파일: `src/agents/nodes/reason/result_finalizer.py` (Line 178~179)
- 등급: INFO (타입 힌트)
- 현상: `conflicted_items: list`로만 선언되어 있고, 원소 타입(`KnowledgeItem`)이 명시되지 않았다.
- 해결: `conflicted_items: list[KnowledgeItem]`로 변경.

### I-08. __init__.py의 노출 범위

- 파일: `src/agents/nodes/reason/__init__.py`
- 등급: INFO (모듈 구성)
- 현상: __init__.py의 내용이 비어있거나 매우 짧다(1줄). 노드 함수들의 export가 명시되지 않아 pipeline.py에서 각 모듈을 직접 import해야 한다.
- 해결: `__all__` 또는 명시적 re-export를 추가하면 import 경로가 단순해진다. 다만 현재 패턴이 프로젝트 전체에서 일관적이라면 현 상태 유지도 괜찮다.

### I-09. reasoning_preparer에서 query_decomposition이 dict로 반환됨

- 파일: `src/agents/nodes/reason/reasoning_preparer.py` (Line 109~155)
- 등급: INFO (타입 안전성)
- 현상: `_build_decomposition_from_normalized`이 `dict`를 반환하여 `reason.query_decomposition`에 저장된다. 이후 sql_validator의 `_validate_layer2a`에서 `decomp.get("group_by")` 등으로 접근하는데, Pydantic 모델 대신 plain dict를 사용하면 키 오타를 런타임까지 발견할 수 없다.
- 해결: `QueryDecomposition` Pydantic 모델을 정의하여 타입 안전성을 확보하는 것을 권장한다. 장기적으로 dict 기반 인터페이스를 Pydantic 모델로 전환.

### I-10. context_interpreter Level 1 모드에서 스텝별 순차 LLM 호출

- 파일: `src/agents/nodes/reason/context_interpreter.py` (Line 381~456)
- 등급: INFO (성능)
- 현상: Level 1 모드에서 각 스텝을 순차적으로 LLM 호출하고 있다. 이전 스텝의 insight를 다음 스텝에 전달하기 위함이지만, 독립적인 스텝(서로 다른 도구 결과)은 병렬 호출이 가능하다.
- 해결: 동일 도구 유형의 스텝은 병렬 호출하고, 결과를 합쳐서 종합 판정에 전달하는 방식으로 최적화 가능. 단, 현재 이전 insight를 누적하는 교차 참조 로직이 있으므로 trade-off를 검토해야 한다.

---

## 종합 평가

### 보안 (최우선)
- SQL 인젝션 방어 체계가 전반적으로 잘 설계되어 있으나, **DB 직접 조회 도구에서 파라미터 바인딩 미사용**(C-01, C-02)이 가장 큰 취약점이다. 식별자 화이트리스트(`_IDENT_RE`)는 좋은 첫 번째 방어선이지만, 사용자 유래 값(keyword)은 반드시 파라미터 바인딩으로 전달해야 한다.
- PII 보호에서 `SELECT *` 우회 가능성(C-04)은 금융 규정 위반으로 이어질 수 있어 즉시 조치가 필요하다.
- 프롬프트 인젝션 방어(82개 패턴), 유니코드 정규화, 다중 쿼리 차단 등 다층 방어가 잘 구현되어 있다.

### 아키텍처
- retriever/interpreter/readiness_gate의 3단계 탐색 루프 설계가 깔끔하다.
- recovery_agent의 dead_end 추적과 가설 전이 로직이 체계적이다.
- tool_renderers의 렌더러 맵 패턴이 확장성이 좋다.

### 성능
- context_retriever의 병렬 실행(`asyncio.gather`)과 중복 방지(`executed_tool_keys`)가 효율적이다.
- Level 0/1 자동 분기로 토큰 예산을 관리하는 설계가 합리적이다.

### 코드 품질
- 전반적으로 docstring이 충실하고, 함수 분리가 잘 되어 있다.
- LLM 호출 패턴이 대부분 `llm_call_with_parse_retry`로 통일되어 있으나, response_formatter와 data_analyzer 일부에서 raw client 직접 호출이 남아있다(W-02, W-03).

### 우선순위 권장 조치
1. **(즉시)** C-01, C-04: 파라미터 바인딩 적용 + SELECT * 차단
2. **(단기)** C-02, C-03: DB 도구 전체 파라미터 바인딩 + Layer 3 이중 방어
3. **(중기)** W-02, W-03, W-08: LLM 호출 패턴 통일
4. **(장기)** I-09, W-09: dict -> Pydantic 모델 전환으로 타입 안전성 강화
