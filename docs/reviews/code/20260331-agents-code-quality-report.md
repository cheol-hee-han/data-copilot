# src/agents/ 코드 품질 리뷰 리포트

- **날짜**: 2026-03-31
- **대상**: `src/agents/` 전체 (37개 Python 파일)
- **중점 사항**: 책임 분리, 중복 코드, 의존성, 가독성, 죽은 코드, 과도한 추상화, 테스트 용이성

---

## 1. Critical (빨간색)

### C-01. `validate_sql_safety` 함수 중복 — 서로 다른 모듈에서 다른 구현 사용

- **파일/라인**:
  - `nodes/present/sql_executor.py:33` — `from src.utils.security import validate_sql_safety`
  - `nodes/reason/sql_validator.py:31` — `from src.services.sql_safety_checker import validate_sql_safety`
- **문제**: SQL 안전성 검증이라는 동일한 책임을 수행하는 함수가 `src/utils/security`와 `src/services/sql_safety_checker` 두 곳에 각각 존재하며, **시그니처와 반환 타입이 다르다**.
  - `sql_executor.py`는 `validate_sql_safety(sql)` 호출 후 `(is_safe, safety_errors)` 튜플을 받는다 (라인 53-54).
  - `sql_validator.py`는 `validate_sql_safety(sql, dialect)` 호출 후 `safety.is_safe` / `safety.errors` 객체 속성을 참조한다 (라인 163-166).
- **위험도**: 보안 검증 로직의 분산은 한쪽만 업데이트될 때 검증 회피 가능성을 만든다.
- **개선안**: SQL 안전성 검증을 단일 모듈(`src/services/sql_safety_checker.py`)로 통합하고, `sql_executor.py`의 import를 교체한다. 반환 타입도 `SafetyResult` 객체로 통일한다.

### C-02. `ConfidenceLevel` Enum 중복 정의

- **파일/라인**:
  - `models/clarification.py:36-45` — `ConfidenceLevel(StrEnum)` (HIGH/MEDIUM/LOW)
  - `models/normalization.py:75-80` — `ConfidenceLevel(str, Enum)` (HIGH/MEDIUM/LOW)
- **문제**: 동일한 이름과 값을 가진 Enum이 두 모듈에 별도로 정의되어 있다. 기본 클래스도 `StrEnum` vs `str, Enum`으로 다르다. `history_resolver.py`와 `clarification_handler.py`는 `clarification.ConfidenceLevel`을 import하고, `normalization.py` 내부 슬롯 모델들은 문자열(`"MEDIUM"`)로 기본값을 설정하여 자기 모듈의 Enum을 사실상 사용하지 않는다.
- **개선안**: `src/models/enums.py`에 단일 `ConfidenceLevel`을 정의하고 양쪽에서 re-import하거나, `normalization.py`의 것을 `NormConfidenceLevel`로 구분 명명한다. 현재 normalization 슬롯 모델의 `confidence` 필드가 문자열 기본값(`"MEDIUM"`)을 사용하므로, Enum 자체가 검증에 활용되지 않는 상태이다 — 이 역시 Enum 사용 또는 Pydantic validator로 연결해야 한다.

### C-03. `context_explorer.py` 파일 크기 및 책임 과다 (1133+ 라인)

- **파일**: `nodes/reason/context_explorer.py`
- **문제**: 6-Phase 오케스트레이션, 도구 실행, 배치 LLM 호출, 결과 파싱, 날짜 관찰, 샘플링, KnowledgeItem 승격, 테이블 판정, 중복 제거 등 **최소 8가지 책임**이 단일 파일에 집중되어 있다.
- **영향**: 코드 탐색이 어렵고, 단위 테스트 작성 시 mock 범위가 과도하다.
- **개선안**: 다음과 같이 분리를 제안한다:
  1. `context_explorer.py` — 메인 노드 함수 + 6-Phase 오케스트레이션만 유지
  2. `context_explorer_batch.py` (또는 `services/batch_interpreter.py`) — `_interpret_batch`, `_parse_batch_result`, `_interpret_batch_fallback`, `BatchInterpretResult`
  3. `context_explorer_observations.py` (또는 해당 로직을 tools.py로 이관) — `_observe_all_date_distributions`, `_sample_unsampled_tables`
  4. 테이블 판정 로직 — `_merge_llm_inferred_fields`, `_remove_unsuitable_tables`, `_promote_sampled_confidence`

---

## 2. Warning (노란색)

### W-01. 싱글턴 패턴의 테스트 취약성 — `get_compiled_app`

- **파일/라인**: `graph/pipeline.py:509-529`
- **문제**: 모듈 수준 전역 변수 `_compiled_app`과 `get_compiled_app()` 싱글턴 패턴은 테스트 격리를 어렵게 만든다. `reset_compiled_app()`이 있지만, 멀티스레드 테스트에서 경합 조건이 발생할 수 있다.
- **개선안**: `create_app()`을 직접 호출하도록 하고, 앱 인스턴스를 DI 컨테이너(예: FastAPI lifespan의 app.state)로 관리하는 것을 권장한다. 혹은 최소한 `get_compiled_app`에 thread-safety를 추가한다.

### W-02. `runner.py`에서 `result` 타입이 `dict[str, Any]`로 느슨하게 처리됨

- **파일/라인**: `graph/runner.py:134-151`
- **문제**: `app.ainvoke()`의 반환값 `result`가 `dict[str, Any]`로 처리되며, `_build_result`(라인 194)에서 `.get()` 체인으로 값을 추출한다. LangGraph의 State가 Pydantic 모델(PipelineState)로 정의되어 있음에도, ainvoke 결과를 dict로 사용하여 타입 안전성이 없다.
- **개선안**: `result`를 `PipelineState`로 변환하거나, 최소한 TypedDict를 정의하여 `.get()` 대신 속성 접근을 사용한다.

### W-03. 노드 내부에서 지연 import 패턴 남용

- **파일/라인**:
  - `nodes/interpret/intent_classifier.py:94-97` — `from src.utils.tracker.dispatch import ...`
  - `nodes/interpret/query_normalizer.py:92-95` — 동일
  - `nodes/reason/confidence_evaluator.py:66-68` — `from src.services.confidence_scorer import ReadinessVerdict`
  - `nodes/reason/recovery_planner.py:246` — `from src.config import settings`
  - `nodes/reason/sql_generator.py:43` — 상단 import와 별도로 함수 내 settings 접근
- **문제**: 순환 import 회피 목적이 아닌 곳에서도 지연 import가 사용되고 있어 일관성이 없다. 특히 `dispatch_tracking_event`는 대부분의 노드에서 사용하므로 상단에 import하는 것이 자연스럽다.
- **개선안**: 순환 import가 발생하지 않는 모듈(tracker, config, enums)은 모두 상단 import로 통일한다.

### W-04. 죽은 코드 — 사용되지 않는 프롬프트 변수

- **파일/라인**:
  - `system_prompts.py:94-95` — `CLARIFIER_SYSTEM`, `CLARIFIER_USER`: 기존 `clarifier.py` 노드가 삭제(`git status`에서 `D src/agents/nodes/interpret/clarifier.py`)되었으나, 이 프롬프트 변수는 여전히 로드되고 있다. 프로젝트 전체에서 이 변수를 import하는 코드가 `system_prompts.py` 이외에 없다.
  - `system_prompts.py:48` — `CONTEXT_EXPLORER_SYSTEM` 변수명이 docstring의 파일 매핑에 기재되어 있으나, 실제 변수로 정의되어 있지 않다 (로드되지도 않는다).
  - `system_prompts.py:148-153` — `SQL_VALIDATION_FEEDBACK_SECTION`: 프로젝트 전체에서 사용하는 곳이 없다.
- **개선안**: `CLARIFIER_SYSTEM`, `CLARIFIER_USER`, `SQL_VALIDATION_FEEDBACK_SECTION`을 삭제하고, docstring의 파일 매핑도 최신화한다.

### W-05. `normalization.py` 슬롯 모델에서 Enum을 정의하고도 실제 필드에는 `str` 사용

- **파일/라인**: `models/normalization.py:259-378`
- **문제**: `EntityType`, `MeasureType`, `AggFunction`, `DimensionRole` 등 14개의 Enum을 정의했으나, 각 슬롯 모델(`EntitySlot`, `MeasureSlot` 등)의 필드는 `type: str = "DIRECT"` 같은 순수 문자열로 선언되어 있다. Pydantic v2에서 Enum 타입을 필드에 직접 사용하면 자동 검증이 이루어지지만, 현재는 검증이 없다.
- **추가 문제**: 파일 하단(386-402)에 `VALID_INTENTS`, `VALID_ENTITY_TYPES` 등 set 상수를 별도 생성했는데, 이것도 사용처가 모호하다.
- **개선안**: 슬롯 모델의 `str` 필드를 해당 Enum 타입으로 교체하여 LLM 응답 파싱 시 자동 검증을 활성화하거나, Pydantic validator에서 `VALID_*` 상수를 활용하여 검증한다.

### W-06. `runner.py`의 `session_id` 조건 중복

- **파일/라인**: `graph/runner.py:74, 113`
- **문제**: 라인 74에서 `if not session_id`이면 UUID를 생성하므로, 라인 113의 `if session_id`는 항상 True이다. 불필요한 조건 분기이다.
- **개선안**: `run_config["configurable"] = {"thread_id": session_id}`를 무조건 설정한다.

### W-07. `planner.py`의 과도한 함수 수 (14개 private 함수)

- **파일**: `nodes/reason/planner.py`
- **문제**: `_build_decomposition_from_normalized`, `_initialize_knowledge_items`, `_detect_ambiguous_output`, `_build_output_scope_item`, `_extract_meta_search_query`, `_collect_initial_context`, `_build_initial_candidates`, `_should_fast_path`, `_generate_hypotheses`, `_parse_plan_response`, `_generate_hypotheses_fallback`, `_build_execution_plan`, `_build_fallback_plan`, 그리고 모듈 상수 2개(`VAGUE_OUTPUT_KEYWORDS`, `EXTRACTION_KEYWORDS`).
- **영향**: 단일 파일에 644라인, 14개 함수가 집중되어 있어 탐색이 어렵다.
- **개선안**:
  - 분해/초기화 로직(`_build_decomposition_from_normalized`, `_initialize_knowledge_items`, `_detect_ambiguous_output`)을 `planner_utils.py`나 서비스 계층으로 분리
  - 가설 생성/파싱(`_generate_hypotheses`, `_parse_plan_response`, `_generate_hypotheses_fallback`)을 별도 모듈로 분리

### W-08. `BatchInterpretResult`가 일반 클래스로 정의됨 (Pydantic 미사용)

- **파일/라인**: `nodes/reason/context_explorer.py:386-403`
- **문제**: 프로젝트의 모든 데이터 모델이 Pydantic `BaseModel`을 사용하는데, `BatchInterpretResult`만 일반 `__init__` 클래스로 정의되어 있다. 직렬화/검증 일관성이 깨진다.
- **개선안**: `BaseModel`로 전환하여 프로젝트 패턴에 맞춘다.

### W-09. `recovery_planner.py`에서 `_build_replan_context` 2회 호출 가능성

- **파일/라인**: `nodes/reason/recovery_planner.py:115-117, 151-153`
- **문제**: `pending` 가설이 없을 때 LLM 호출 전에 `_build_replan_context`를 1회 호출하고(라인 115), LLM이 `llm_plan`을 반환하지 않으면 다시 호출한다(라인 151). 동일한 입력으로 동일한 dict를 두 번 생성한다.
- **개선안**: `_build_replan_context` 호출을 1회로 통합하고 결과를 재사용한다.

### W-10. `_handle_error` 함수의 위치 부적절

- **파일/라인**: `graph/pipeline.py:309-326`
- **문제**: `_handle_error`는 LangGraph 노드 함수로 등록되어(`workflow.add_node("error_end", _handle_error)`) state dict를 반환하는데, pipeline.py의 라우팅 함수들 사이에 위치해 있다. 이 함수는 실질적으로 노드 로직이므로 `nodes/` 하위에 배치하는 것이 노드 명명 규칙("그래프 노드 이름 = 파일명 = 함수명")과 일관된다.
- **개선안**: `nodes/present/error_handler.py`로 분리하거나, 최소한 pipeline.py 내에서 라우팅 함수와 명확히 분리하여 배치한다.

---

## 3. Info (초록색)

### I-01. `normalization.py`에서 `Optional` 대신 `X | None` 패턴 권장

- **파일/라인**: `models/normalization.py:20, 272-345`
- **상황**: Python 3.12 프로젝트이므로 `from typing import Optional`을 사용할 필요가 없다. PEP 604의 `str | None` 표기가 더 간결하다.
- **영향**: 기능 차이는 없으나, 프로젝트 내 다른 파일들(`clarification.py`, `state.py` 등)은 이미 `X | None` 패턴을 사용하고 있어 일관성이 부족하다.

### I-02. `tools.py`의 `_safe_search` 래퍼에서 f-string 로거 사용

- **파일/라인**: `nodes/reason/tools.py:62`
- **문제**: `logger.warning(f"{tool_name} 실패", error=str(e))`에서 f-string을 사용한다. structlog 패턴에서는 `logger.warning("도구 검색 실패", tool=tool_name, error=str(e))`가 구조화 로그에 더 적합하다.
- **개선안**: 로그 메시지에서 f-string 대신 키워드 인자로 변수를 전달한다.

### I-03. `thinking_modes.py`와 실제 노드 목록 불일치

- **파일/라인**: `nodes/thinking_modes.py:17-36`
- **상황**: `NODE_THINKING_MODES` dict에 `"table_comparison"` 키가 있으나, 이는 독립 노드가 아니라 `context_explorer` 내부에서 호출되는 프롬프트이다. 반면 `result_finalizer`, `confidence_evaluator` 노드는 dict에 없다 (LLM 미호출이라 영향은 없지만, 매핑의 완전성 차원).
- **개선안**: docstring에 "LLM을 호출하는 노드 및 프롬프트 단위의 매핑"임을 명시하거나, 실제 노드 목록과 동기화한다.

### I-04. `pipeline.py`에서 `_VALID_RETURN_TARGETS`와 실제 clarification_handler 복귀 경로 불일치 가능성

- **파일/라인**: `graph/pipeline.py:288-291`
- **상황**: `_VALID_RETURN_TARGETS`에 `"planner"`가 없다. 만약 planner에서 pending_signals를 발생시키면 복귀할 수 없게 된다. 현재 planner는 AmbiguitySignal을 생성하지 않으므로 실제 문제는 아니지만, 확장 시 누락될 수 있다.
- **개선안**: 향후 확장을 고려하여 `"planner"`를 targets에 포함하거나, YAGNI 원칙에 따라 현 상태를 유지하되 주석으로 이유를 명시한다.

### I-05. `state/__init__.py`에서 `should_terminate`만 re-export하고 `ReasoningState` 헬퍼 메서드는 누락

- **파일/라인**: `state/__init__.py:15-36`
- **상황**: `should_terminate` 함수는 re-export하지만, `ReasoningState`의 헬퍼 메서드(`get_confirmed_knowledge`, `format_confirmed_text` 등)는 클래스 내부이므로 자동 포함된다. 다만 `StepStatus`, `FinalStatus`, `HypothesisStatus`, `TableSelectionStatus` 등 자주 사용되는 Enum이 re-export 목록에 빠져 있다.
- **개선안**: 외부에서 자주 import하는 Enum을 `__init__.py`에 추가한다.

### I-06. `planner.py`의 `_extract_meta_search_query`에서 과도한 방어 코딩

- **파일/라인**: `nodes/reason/planner.py:348-371`
- **상황**: `nq`가 dict일 수도 있고 Pydantic 모델일 수도 있는 경우를 모두 분기 처리한다. 그러나 `nq`는 항상 `NormalizedQuery`(Pydantic 모델) 또는 None이다 (state.normalized_query의 타입 정의 상).
- **개선안**: dict 분기를 제거하고 Pydantic 모델 접근만 유지한다. dict 방어가 필요하다면 타입 힌트에 `NormalizedQuery | dict | None`을 명시한다.

### I-07. `clarification_handler.py`에서 ASK 외 시그널 무시

- **파일/라인**: `nodes/interpret/clarification_handler.py:143-157`
- **상황**: ASK 시그널이 2개 이상이면 우선순위가 가장 높은 1개만 interrupt하고, 나머지 ASK 시그널은 resolved_signals에 포함되지 않는다 (다음 턴에서 재발견을 기대).
- **개선안**: 나머지 ASK 시그널을 pending_signals에 유지하거나, 현재 설계가 의도적이라면 해당 주석을 보강한다.

### I-08. `recovery_planner.py`의 `priority_map` 중복 정의

- **파일/라인**:
  - `planner.py:519` — `priority_map = {"high": 0.9, "medium": 0.5, "low": 0.1}`
  - `recovery_planner.py:331-333` — 동일한 `priority_map`
- **개선안**: 공통 상수(`PRIORITY_MAP`)를 `models/` 또는 `state/state.py`에 정의하고 양쪽에서 import한다.

### I-09. `sql_executor.py`의 `time.time()` 사용

- **파일/라인**: `nodes/present/sql_executor.py:17, 67, 75`
- **상황**: 경과 시간 측정에 `time.time()`을 사용한다. `time.perf_counter()`가 더 정밀하며, `context_explorer.py`(라인 138)에서는 `time.perf_counter()`를 사용하고 있어 일관성이 없다.
- **개선안**: `time.perf_counter()`로 통일한다.

### I-10. `context_explorer.py`의 `import re` 사용 범위 최소

- **파일/라인**: `nodes/reason/context_explorer.py:43`
- **상황**: `re` 모듈이 import되어 있으나, 파일 내에서 970라인의 `re.findall` 1회에서만 사용된다. 해당 로직이 분리 대상(C-03 참조)이라면 import도 함께 이동해야 한다.

---

## 4. 아키텍처 수준 관찰

### A-01. 노드 함수의 프롬프트 주입 패턴 일관성

- **현재 패턴**: 노드 함수가 `system_prompts.py`에서 상수를 import하여 서비스에 주입한다 (예: `history_resolver.py`에서 `HISTORY_RESOLVER_SYSTEM`을 `resolve_history()` 서비스에 전달).
- **이 패턴은 잘 동작한다**: 프롬프트 교체가 쉽고, 서비스의 테스트 시 프롬프트를 직접 주입할 수 있다.
- **일관성 문제**: `planner.py`, `recovery_planner.py`, `context_explorer.py`는 프롬프트를 서비스에 위임하지 않고 노드 내부에서 직접 `render_prompt()` + `llm_call_with_parse_retry()`를 호출한다. reason 계층이 아직 서비스 분리 전 단계인 것으로 보인다.
- **권장**: 당장 리팩토링하지 않더라도, reason 계층 노드의 LLM 호출 패턴을 interpret/present와 동일하게 서비스 위임으로 통일하는 로드맵을 수립한다.

### A-02. 전체 구조 평가

- **좋은 점**:
  - 3계층 파이프라인(interpret/reason/present) 구조가 명확하다.
  - `PipelineState`와 `ReasoningState`의 분리가 적절하다.
  - 라우팅 함수가 순수 함수로 분리되어 테스트하기 쉽다.
  - 프롬프트 외부화(`resources/prompts/`)가 잘 되어 있다.
  - `AmbiguitySignal` 단일 모델로 명확화 생명주기를 관리하는 설계가 깔끔하다.
  - `tools.py`의 TOOL_MAP 디스패치 패턴이 확장에 유리하다.
- **개선 필요**:
  - reason 계층 노드의 파일 크기가 전반적으로 크다 (`context_explorer` 1133+, `planner` 644, `recovery_planner` 481 라인).
  - 노드 내부에서 직접 LLM을 호출하는 패턴이 서비스 위임 패턴과 혼재한다.
  - `validate_sql_safety` 중복(C-01)은 보안 관점에서 즉시 수정이 필요하다.

---

## 5. 우선순위 요약

| 등급 | ID | 요약 | 난이도 |
|------|-----|------|--------|
| 빨간색 | C-01 | validate_sql_safety 이중 구현 통합 | 낮음 |
| 빨간색 | C-02 | ConfidenceLevel Enum 중복 해소 | 낮음 |
| 빨간색 | C-03 | context_explorer.py 분리 설계 | 높음 |
| 노란색 | W-01 | 싱글턴 get_compiled_app 테스트 격리 | 중간 |
| 노란색 | W-02 | runner.py result 타입 안전성 | 중간 |
| 노란색 | W-03 | 지연 import 패턴 정리 | 낮음 |
| 노란색 | W-04 | 죽은 프롬프트 변수 삭제 | 낮음 |
| 노란색 | W-05 | normalization 슬롯 Enum 미활용 | 중간 |
| 노란색 | W-06 | session_id 조건 중복 | 낮음 |
| 노란색 | W-07 | planner.py 함수 분리 | 중간 |
| 노란색 | W-08 | BatchInterpretResult Pydantic 전환 | 낮음 |
| 노란색 | W-09 | _build_replan_context 2회 호출 | 낮음 |
| 노란색 | W-10 | _handle_error 위치 이동 | 낮음 |
| 초록색 | I-01~I-10 | 일관성, 가독성, 사소한 개선 | 낮음 |
