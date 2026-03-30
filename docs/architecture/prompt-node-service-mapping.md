# 프롬프트 · 노드 · 서비스 매핑표

> **Version 1.2** (2026-03-29)
> 프롬프트 변수, 템플릿 파일, 노드, 서비스/유틸리티 간의 전체 매핑을 정리한다.
>
> v1.1: `sql_hint_extractor.py` → `utils/sqlglot_analyzer.py` 이동 반영, 횡단 관심사 위치 컬럼 추가
> v1.2: 전체 노드 재검증 — LLM 직접 호출 여부, `utils/llm/` 모듈 매핑, `batch_interpret_system.txt` 변수 추가(`{unresolved_items}`) 반영

---

## 명명 규칙

프롬프트 변수명 = 파일명(확장자 제외).UPPER_SNAKE_CASE

```
예) analyzer_system.txt → ANALYZER_SYSTEM
```

프롬프트 변수 정의: `src/agents/nodes/system_prompts.py`
프롬프트 파일 루트: `resources/prompts/{interpret,reason,present}/`

---

## 1. Interpret 계층 — 질의 해석

| 프롬프트 변수 | 프롬프트 파일 | 노드 | 서비스 / 유틸리티 | LLM 호출 |
| --- | --- | --- | --- | --- |
| `INTENT_CLASSIFIER_SYSTEM` | `interpret/intent_classifier_system.txt` | `intent_classifier.py` | `intent_resolver.py` — `classify_with_gate()` | 서비스 위임 |
| `INTENT_CLASSIFIER_USER` | `interpret/intent_classifier_user.txt` | 〃 | 〃 | 〃 |
| `INTENT_CLASSIFIER_LEGACY_SYSTEM` | `interpret/intent_classifier_legacy_system.txt` | 〃 | `intent_resolver.py` — `classify_legacy()` | 서비스 위임 |
| `CLARIFIER_SYSTEM` | `interpret/clarifier_system.txt` | `clarifier.py` | `utils/llm` — `render_prompt()` | **노드 직접** |
| `CLARIFIER_USER` | `interpret/clarifier_user.txt` | 〃 | 〃 | 〃 |
| `QUERY_NORMALIZER_PHASE1_SYSTEM` | `interpret/query_normalizer_phase1_system.txt` | `query_normalizer.py` | `query_normalizer.py` — `run_normalization()` | 서비스 위임 |
| `QUERY_NORMALIZER_PHASE1_USER` | `interpret/query_normalizer_phase1_user.txt` | 〃 | 〃 | 〃 |
| `QUERY_NORMALIZER_PHASE2_SYSTEM` | `interpret/query_normalizer_phase2_system.txt` | 〃 | 〃 | 〃 |
| `QUERY_NORMALIZER_PHASE2_USER` | `interpret/query_normalizer_phase2_user.txt` | 〃 | 〃 | 〃 |
| `HISTORY_RESOLVER_SYSTEM` | `interpret/history_resolver_system.txt` | `history_resolver.py` | `history_resolver.py` — `resolve_history()` | 서비스 위임 |
| `HISTORY_RESOLVER_USER` | `interpret/history_resolver_user.txt` | 〃 | 〃 | 〃 |
| *(프롬프트 없음)* | — | `preprocessor.py` | `input_sanitizer.py` — 입력 정제, 인젝션 감지 | 없음 |

---

## 2. Reason 계층 — 에이전틱 추론 루프

| 프롬프트 변수 | 프롬프트 파일 | 노드 | 서비스 / 유틸리티 | LLM 호출 |
| --- | --- | --- | --- | --- |
| `PLANNER_SYSTEM` | `reason/planner_system.txt` | `planner.py` | `tools.py` — `search_table_meta()`, `search_use_cases()`, `extract_hints_from_use_cases()` | **노드 직접** |
| `CONTEXT_EXPLORER_SYSTEM` | `reason/context_explorer_system.txt` | `context_explorer.py` | `confidence_scorer.py` — `evaluate_readiness()`, `tools.py` — `execute_tool()` 등 | **노드 직접** |
| `BATCH_INTERPRET_SYSTEM` | `reason/batch_interpret_system.txt` | 〃 | 〃 | 〃 |
| `TABLE_COMPARISON_SYSTEM` | `reason/table_comparison_system.txt` | 〃 | 〃 | 〃 |
| `SQL_GENERATOR_SYSTEM` | `reason/sql_generator_system.txt` | `sql_generator.py` | `utils/llm/prompt` — `serialize_decomp_slots()` | **노드 직접** |
| `SQL_GENERATOR_FIX_SECTION` | `reason/sql_generator_fix_section.txt` | 〃 | 〃 | 〃 |
| `SQL_VALIDATOR_SYSTEM` | `reason/sql_validator_system.txt` | `sql_validator.py` | `sql_safety_checker.py` (L1), `sqlglot_analyzer.py` (L1 AST), `utils/llm/prompt` — `serialize_decomp_slots()` (L2b) | **조건부** (L2b만) |
| `RECOVERY_PLANNER_SYSTEM` | `reason/recovery_planner_system.txt` | `recovery_planner.py` | `tools.py` — `TOOL_MAP` | **노드 직접** |
| *(프롬프트 없음)* | — | `confidence_evaluator.py` | `confidence_scorer.py` — `calculate_readiness()`, `evaluate_readiness()` | 없음 |
| *(프롬프트 없음)* | — | `result_finalizer.py` | **없음** — 최종 결과 조립 (순수 변환) | 없음 |
| *(프롬프트 없음)* | — | `tools.py` | `sqlglot_analyzer.py` — `extract_structural_hints()`, `merge_hints()` | 없음 |

---

## 3. Present 계층 — 결과 생성 및 표현

| 프롬프트 변수 | 프롬프트 파일 | 노드 | 서비스 / 유틸리티 | LLM 호출 |
| --- | --- | --- | --- | --- |
| `ANALYZER_SYSTEM` | `present/analyzer_system.txt` | `analyzer.py` | `data_analyzer.py` — `analyze_data()` | 서비스 위임 |
| `ANALYZER_USER` | `present/analyzer_user.txt` | 〃 | 〃 | 〃 |
| `ANALYZER_VIZ_JUDGMENT_SYSTEM` | `present/analyzer_viz_judgment_system.txt` | 〃 | 〃 | 〃 |
| `ANALYZER_VIZ_JUDGMENT_USER` | `present/analyzer_viz_judgment_user.txt` | 〃 | 〃 | 〃 |
| `ANALYZER_VIZ_SVG_SYSTEM` | `present/analyzer_viz_svg_system.txt` | 〃 | 〃 | 〃 |
| `ANALYZER_VIZ_SVG_USER` | `present/analyzer_viz_svg_user.txt` | 〃 | 〃 | 〃 |
| `FORMATTER_SYSTEM` | `present/formatter_system.txt` | `formatter.py` | `response_formatter.py` — `format_response()` | 서비스 위임 |
| `FORMATTER_USER` | `present/formatter_user.txt` | 〃 | 〃 | 〃 |
| *(프롬프트 없음)* | — | `sql_executor.py` | `utils/security` — `validate_sql_safety()`, 커넥터로 SQL 실행 | 없음 |

---

## 4. 횡단 관심사 (비-LLM 유틸리티)

프롬프트 없이 복수 노드에서 공유되는 서비스/유틸리티:

| 모듈 | 위치 | 사용 노드 | 역할 |
| --- | --- | --- | --- |
| `confidence_scorer.py` | `services/` | confidence_evaluator, context_explorer, pipeline.py | 확신도 계산 + 행동 판정 (SSOT) |
| `sql_safety_checker.py` | `services/` | sql_validator (L1), sql_executor (이중 방어) | SQL 안전성 검증 (DML/DDL 차단) |
| `sqlglot_analyzer.py` | `utils/` | tools.py, sql_validator.py | sqlglot AST 파싱, 테이블/컬럼 추출, 구조적 힌트 추출 |
| `input_sanitizer.py` | `services/` | preprocessor | 입력 정제, 인젝션 감지 |
| `insight_builder.py` | `services/` | **runner.py** (파이프라인 러너) | 최종 응답 인사이트 문구 조립 |

---

## 5. LLM 유틸리티

노드에서 직접 LLM을 호출할 때 공통으로 사용하는 유틸리티:

| 모듈 | 위치 | 제공 함수 | 사용 노드 |
| --- | --- | --- | --- |
| `client.py` | `utils/llm/` | `get_llm_client()` — AsyncAnthropic 싱글턴 | clarifier, planner, context_explorer, sql_generator, sql_validator(L2b), recovery_planner |
| `prompt.py` | `utils/llm/` | `render_prompt()` — 템플릿 변수 치환 + 추적, `serialize_decomp_slots()` — query_decomposition 직렬화 | 〃 |
| `response.py` | `utils/llm/` | `extract_json()` — LLM 응답에서 JSON 추출 | planner, context_explorer, sql_generator, sql_validator(L2b), recovery_planner |
| `retry.py` | `utils/llm/` | LLM 호출 재시도 (지수 백오프) | 모든 LLM 호출 노드 |

---

## 6. 인라인 템플릿

코드에 직접 정의된 프롬프트 (외부 파일 없음):

| 변수명 | 위치 | 용도 |
| --- | --- | --- |
| `SQL_VALIDATION_FEEDBACK_SECTION` | `system_prompts.py` | SQL 재생성 시 이전 오류 피드백 삽입 |

---

## 7. 프롬프트 변수 매핑 (batch_interpret_system.txt)

배치 해석 프롬프트의 변수-데이터 소스 매핑:

| 프롬프트 변수 | 직렬화 함수 | 데이터 소스 |
| --- | --- | --- |
| `{original_query}` | — (문자열 직접) | `state.preprocessed_input` |
| `{time_slot}` | `_extract_time_slot()` | `state.normalized_query.time` |
| `{unresolved_items}` | `_serialize_unresolved_items()` | `knowledge_items` 중 UNRESOLVED/CONFLICTED |
| `{tool_results}` | `_serialize_tool_results()` | 도구 실행 결과 (step, result) 쌍 |
| `{table_observations}` | `_serialize_table_observations()` | `candidate_tables`의 메타/샘플/날짜분포 |
