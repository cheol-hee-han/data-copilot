# 프롬프트 · 노드 · 서비스 매핑표

> **Version 1.7** (2026-04-20)
> 프롬프트 변수, 템플릿 파일, 노드, 서비스/유틸리티 간의 전체 매핑을 정리한다.
>
> v1.1: `sql_hint_extractor.py` → `utils/sqlglot_analyzer.py` 이동 반영, 횡단 관심사 위치 컬럼 추가
> v1.2: 전체 노드 재검증 — LLM 직접 호출 여부, `utils/llm/` 모듈 매핑, `batch_interpret_system.txt` 변수 추가(`{unresolved_items}`) 반영
> v1.3: 노드 리네임 반영 — `context_explorer` → `context_retriever` + `context_interpreter` 분리, `confidence_evaluator` → `readiness_gate`, `recovery_planner` → `recovery_agent`, `preprocessor` 제거(sanitize → runner.py 이관), `clarifier` → `clarification_handler`(규칙 기반, 프롬프트 미사용)
> v1.4: `planner` → `reasoning_preparer` 리네임 반영 (규칙 기반, LLM/프롬프트 미사용). `PLANNER_SYSTEM` 프롬프트 미사용 처리.
> v1.5: `미사용_` 접두사 프롬프트 파일 4건 삭제 확인 반영 (파일 잔존 → 파일 삭제됨으로 정정).
> v1.6: **v2.4 노드 정합성 반영** — `continue_orchestrator`(신규, LLM 직접) · `visualizer`(analyzer에서 분리, LLM 직접 + 19종 SVG few-shot) · `turn_reset` / `save_turn_snapshot`(규칙 기반, 프롬프트 미사용) · `intent_classifier_query_rewriter` 추가 · `sql_generator_system_{postgres,sybase_iq,impala,oracle}` dialect 분기 반영 · `analyzer`에서 시각화 프롬프트 4건 → `visualizer`로 이동.
> v1.7: rewriter 재배치 — `INTENT_CLASSIFIER_QUERY_REWRITER` → `EXTRACTION_QUERY_REWRITER` 로 리네임, 호출 지점도 `intent_classifier` 노드 → `query_normalizer` 노드로 이동. 이유: CONTINUE 라우팅 시점에 재작성된 질의가 오케스트레이터 입력으로 주입되는 버그(K-01) 해결.

---

## 목차

- [명명 규칙](#명명-규칙)
- [1. Interpret 계층](#1-interpret-계층--질의-해석) — intent / continue / normalize / clarification
- [2. Reason 계층](#2-reason-계층--에이전틱-추론-루프) — preparer / retriever / interpreter / generator / validator / recovery / finalizer
- [3. Present 계층](#3-present-계층--결과-생성-및-표현) — sql_executor / analyzer / visualizer / formatter / simple_responder
- [4. 횡단 관심사](#4-횡단-관심사-비-llm-유틸리티)
- [5. LLM 유틸리티](#5-llm-유틸리티)
- [6. 인라인 템플릿](#6-인라인-템플릿)
- [7. 프롬프트 변수 매핑](#7-프롬프트-변수-매핑-context_interpreter_systemtxt)
- [8. 종료 훅 (post)](#8-종료-훅-post)

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
| *(프롬프트 없음)* | — | `turn_reset` (`pipeline.py::_turn_reset`) | `PipelineState.turn_reset_updates(intent_norm)` SSoT | 없음 |
| `INTENT_CLASSIFIER_SYSTEM` | `interpret/intent_classifier_system.txt` | `intent_classifier.py` | `intent_classifier.py` — 이력해소 + 의도분류 통합 | **서비스 위임** |
| `INTENT_CLASSIFIER_USER` | `interpret/intent_classifier_user.txt` | 〃 | 〃 | 〃 |
| `CONTINUE_ORCHESTRATOR_SYSTEM` | `interpret/continue_orchestrator_system.txt` | `continue_orchestrator.py` | 4-Way 라우팅 판정 + handoff_note 생성 | **노드 직접** |
| `CONTINUE_ORCHESTRATOR_USER` | `interpret/continue_orchestrator_user.txt` | 〃 | turn_snapshots/conversation_history 직렬화 | 〃 |
| `EXTRACTION_QUERY_REWRITER` | `interpret/extraction_query_rewriter.txt` | `query_normalizer.py` | `query_normalizer.py` — `extraction_query_rewriter()` (DATA_ANALYSIS 전처리, 정규화 전에 시각화/분석 지시어 제거) | 서비스 위임 |
| `QUERY_NORMALIZER_PHASE1_SYSTEM` | `interpret/query_normalizer_phase1_system.txt` | `query_normalizer.py` | `query_normalizer.py` — `run_normalization()` | 서비스 위임 |
| `QUERY_NORMALIZER_PHASE1_USER` | `interpret/query_normalizer_phase1_user.txt` | 〃 | 〃 | 〃 |
| `QUERY_NORMALIZER_PHASE2_SYSTEM` | `interpret/query_normalizer_phase2_system.txt` | 〃 | REFINE 라우트에서 `{handoff_note}` 플레이스홀더 주입 | 〃 |
| `QUERY_NORMALIZER_PHASE2_USER` | `interpret/query_normalizer_phase2_user.txt` | 〃 | 〃 | 〃 |
| *(프롬프트 없음)* | — | `clarification_handler.py` | `interrupt` 패턴, `AmbiguitySignal` 소비 | 없음 |
| *(프롬프트 없음)* | — | ~~`preprocessor.py`~~ **제거됨** | `input_sanitizer.py` — sanitize가 `runner.py`로 이관 | 없음 |

---

## 2. Reason 계층 — 에이전틱 추론 루프

| 프롬프트 변수 | 프롬프트 파일 | 노드 | 서비스 / 유틸리티 | LLM 호출 |
| --- | --- | --- | --- | --- |
| ~~`PLANNER_SYSTEM`~~ | *(파일 삭제됨)* | ~~`planner.py`~~ → `reasoning_preparer.py` | 통합됨 — **프롬프트 미사용** (규칙 기반, LLM 호출 없음). 파일도 삭제됨 | 없음 |
| *(프롬프트 없음)* | — | `reasoning_preparer.py` | 규칙 기반 가설 생성·탐색 계획 수립 (deterministic, LLM 호출 없음) | 없음 |
| *(프롬프트 없음)* | — | `context_retriever.py` | `tools.py` — 도구 실행 + 관찰 데이터 수집 (rule-based) | 없음 |
| `CONTEXT_INTERPRETER_SYSTEM` | `reason/context_interpreter_system.txt` | `context_interpreter.py` | `utils/llm/response` — `extract_json()`, `utils/llm/prompt` — `render_prompt()` | **노드 직접** |
| ~~`TABLE_COMPARISON_SYSTEM`~~ | *(파일 삭제됨)* | 〃 | 통합됨 — 프롬프트 미사용, 파일도 삭제됨 | 없음 |
| `SQL_GENERATOR_SYSTEM` | `reason/sql_generator_system.txt` (공통) | `sql_generator.py` | `utils/llm/prompt` — `serialize_decomp_slots()` | **노드 직접** |
| `SQL_GENERATOR_SYSTEM_POSTGRES` / `_SYBASE_IQ` / `_IMPALA` / `_ORACLE` | `reason/sql_generator_system_{dialect}.txt` | 〃 | `target_db_resolver` 결정값으로 dialect별 프롬프트 라우팅 (Phase 3) | 〃 |
| `SQL_GENERATOR_FIX_SECTION` | `reason/sql_generator_fix_section.txt` | 〃 | 재생성 시 failure_type/handoff_note/`{previous_sql}` 플레이스홀더 주입 | 〃 |
| `SQL_VALIDATOR_SYSTEM` | `reason/sql_validator_system.txt` | `sql_validator.py` | `sql_safety_checker.py` (L1), `sqlglot_analyzer.py` (L1 AST), `utils/llm/prompt` — `serialize_decomp_slots()` (L2b) | **조건부** (L2b만) |
| `RECOVERY_AGENT_SYSTEM` | `reason/recovery_agent_system.txt` | `recovery_agent.py` | `tools.py` — `TOOL_MAP`, `confidence_scorer.py`, `{previous_sql}` 플레이스홀더 (Phase 3 §3.6) | **노드 직접** |
| *(프롬프트 없음)* | — | `readiness_gate.py` | `confidence_scorer.py` — `calculate_readiness()`, `evaluate_readiness()` | 없음 |
| *(프롬프트 없음)* | — | `result_finalizer.py` | `target_db_resolver.py` SSoT (FORCED/SINGLE/AMBIGUOUS/NO_SELECTION) + 결과 조립 | 없음 |
| *(프롬프트 없음)* | — | `tools.py` | `sqlglot_analyzer.py` — `extract_structural_hints()`, `merge_hints()` | 없음 |

---

## 3. Present 계층 — 결과 생성 및 표현

| 프롬프트 변수 | 프롬프트 파일 | 노드 | 서비스 / 유틸리티 | LLM 호출 |
| --- | --- | --- | --- | --- |
| *(프롬프트 없음)* | — | `sql_executor.py` | `utils/security` — `validate_sql_safety()`, 커넥터로 `target_db` SQL 실행, `result_data` 산출 | 없음 |
| `ANALYZER_SYSTEM` | `present/analyzer_system.txt` | `analyzer.py` | `data_analyzer.py` — `analyze_data()`, ANALYZE 라우트에서 `{handoff_note}` 주입 | 서비스 위임 |
| `ANALYZER_USER` | `present/analyzer_user.txt` | 〃 | 〃 | 〃 |
| `VISUALIZER_JUDGMENT_SYSTEM` | `present/visualizer_judgment_system.txt` | `visualizer.py` (v2.4 신규) | 시각화 필요 판단 + 차트 유형 19종 + REDISPLAY 시 `{handoff_note}` 주입 | **노드 직접** |
| `VISUALIZER_JUDGMENT_USER` | `present/visualizer_judgment_user.txt` | 〃 | 〃 | 〃 |
| `VISUALIZER_SVG_SYSTEM_BASE` | `present/visualizer_svg_system_base.txt` | 〃 | SVG 생성 베이스 + 보안 규칙 (`<script>`/`on*`/`javascript:` 금지) | 〃 |
| `VISUALIZER_SVG_USER` | `present/visualizer_svg_user.txt` | 〃 | result_data + 선택된 chart_type 직렬화 | 〃 |
| `VISUALIZER_SVG_EXAMPLE_*` (19개) | `present/visualizer_svg_example_{chart_type}.txt` | 〃 | 차트 유형별 few-shot 예제 (bar/line/pie/donut/scatter/heatmap/waterfall/horizontal_bar/grouped_bar/stacked_bar/info_card/flowchart/timeline/mind_map/org_chart/process_diagram/venn/matrix/value_chain) | 〃 |
| *(프롬프트 없음)* | — | `formatter.py` | `response_formatter.py` — rule-based 포맷팅 + `process_summary_builder.py` | 없음 (LLM 제거) |
| *(프롬프트 없음)* | — | `simple_responder.py` | 규칙 기반 — 비데이터 의도 경량 정형 응답 (LLM 호출 없음) | 없음 |

---

## 4. 횡단 관심사 (비-LLM 유틸리티)

프롬프트 없이 복수 노드에서 공유되는 서비스/유틸리티:

| 모듈 | 위치 | 사용 노드 | 역할 |
| --- | --- | --- | --- |
| `confidence_scorer.py` | `services/` | readiness_gate, recovery_agent, pipeline.py | 확신도 계산 + 행동 판정 (SSOT) |
| `sql_safety_checker.py` | `services/` | sql_validator (L1), sql_executor (이중 방어) | SQL 안전성 검증 (DML/DDL 차단) |
| `sqlglot_analyzer.py` | `utils/` | tools.py, sql_validator.py | sqlglot AST 파싱, 테이블/컬럼 추출, 구조적 힌트 추출 |
| `input_sanitizer.py` | `services/` | **runner.py** (파이프라인 러너) | 입력 정제, 인젝션 감지 (기존 preprocessor에서 runner.py로 이관) |
| `insight_builder.py` | `services/` | **runner.py** (파이프라인 러너) | 최종 응답 인사이트 문구 조립 |

---

## 5. LLM 유틸리티

노드에서 직접 LLM을 호출할 때 공통으로 사용하는 유틸리티:

| 모듈 | 위치 | 제공 함수 | 사용 노드 |
| --- | --- | --- | --- |
| `client.py` | `utils/llm/` | `get_llm_client()` — AsyncAnthropic 싱글턴 | context_interpreter, sql_generator, sql_validator(L2b), recovery_agent |
| `prompt.py` | `utils/llm/` | `render_prompt()` — 템플릿 변수 치환 + 추적, `serialize_decomp_slots()` — query_decomposition 직렬화 | 〃 |
| `response.py` | `utils/llm/` | `extract_json()` — LLM 응답에서 JSON 추출 | context_interpreter, sql_generator, sql_validator(L2b), recovery_agent |
| `retry.py` | `utils/llm/` | LLM 호출 재시도 (지수 백오프) | 모든 LLM 호출 노드 |

---

## 6. 인라인 템플릿

코드에 직접 정의된 프롬프트 (외부 파일 없음):

| 변수명 | 위치 | 용도 |
| --- | --- | --- |
| `SQL_VALIDATION_FEEDBACK_SECTION` | `system_prompts.py` | SQL 재생성 시 이전 오류 피드백 삽입 |

---

## 8. 종료 훅 (post)

| 노드 | 위치 | 서비스 / 유틸리티 | LLM 호출 |
| --- | --- | --- | --- |
| `save_turn_snapshot` | `agents/nodes/post/save_turn_snapshot.py` | `services/message_store.py` — `save_turn_snapshot()` 영속화 (세션 보존, 다음 턴 CONTINUE hydration 소스) | 없음 |
| `error_end` | `pipeline.py::_handle_error` | `error_message_builder` 규칙 기반, `save_turn_snapshot`으로 직행 (실패 턴도 영속화) | 없음 |

---

## 7. 프롬프트 변수 매핑 (context_interpreter_system.txt)

배치 해석 프롬프트의 변수-데이터 소스 매핑:

| 프롬프트 변수 | 직렬화 함수 | 데이터 소스 |
| --- | --- | --- |
| `{original_query}` | — (문자열 직접) | `state.preprocessed_input` |
| `{time_slot}` | `_extract_time_slot()` | `state.normalized_query.time` |
| `{unresolved_items}` | `_serialize_unresolved_items()` | `knowledge_items` 중 UNRESOLVED/CONFLICTED |
| `{tool_results}` | `_serialize_tool_results()` | 도구 실행 결과 (step, result) 쌍 |
| `{table_observations}` | `_serialize_table_observations()` | `candidate_tables`의 메타/샘플/날짜분포 |
