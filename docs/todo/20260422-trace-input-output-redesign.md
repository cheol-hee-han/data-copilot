# Trace Input/Output 재설계 — 프롬프트 [INPUT]/[OUTPUT_CONTRACT] 원본 기록

## 목차

- [배경](#배경) — 현재 trace 가 LLM 실제 입출력을 재현 못 하는 문제
- [원칙](#원칙) — 입·출력을 프롬프트 원본과 동일하게 기록
- [설계](#설계) — 공용 유틸 + 노드별 적용 지점
- [PR 분할](#pr-분할) — P1~P5 단계
- [변경 매트릭스](#변경-매트릭스) — 노드별 파일·라인
- [검증](#검증) — E2E 재측정 기준

## 배경

A-04 디버깅 중 trace 의 `filters: 1` 출력이 실제 LLM 의 `FilterSlot(target/filter_type/values)` 내용을 전혀 담지 않아 LLM 이 무엇을 출력했는지 재구성 불가능함을 확인. 조사 결과 **9 개 LLM 노드 전부**가 프롬프트 [INPUT]/[OUTPUT_CONTRACT] 와 다른 축약본을 REASONING_STEP 에 기록하고 있음.

이미 존재하는 인프라:
- `render_prompt(template, replacements) -> (prompt, variables)` — variables 는 플레이스홀더별 실제 주입 값 dict
- `llm_call_with_parse_retry(...) -> (raw_text, parsed)` — raw_text 는 LLM 원본 응답
- `LLMCall.prompt_variables` 필드 — 현재는 LLM 호출 단위에서만 저장, REASONING_STEP 과 미연결

데이터는 노드 스코프에 있지만 dispatch 하지 않음이 핵심 결함.

## 원칙

1. **입력 원칙**: REASONING_STEP.inputs.prompt_variables = 프롬프트 템플릿의 `{placeholder}` 를 채운 실제 값 dict. 개별 축약 필드 (`tool_results_summary`, `filter_count` 등) 제거.
2. **출력 원칙**: REASONING_STEP.output.raw_response = LLM 이 반환한 원본 JSON 문자열. 파싱된 구조는 렌더링 편의용 `parsed` 로 보조 기록.
3. **비-LLM 노드 (rule_decision/tool_execution)**: 입출력을 원본 그대로 기록 (축약 금지). 렌더링에서만 길이 조절.
4. **서비스 레이어**: 시그니처 확장으로 `LLMInteraction` 을 노드에 넘김. 노드가 dispatch.
5. **공용 유틸**: 모든 LLM 노드가 `build_llm_reasoning_payload(...)` 단일 헬퍼로 payload 구성 — 일관성 확보.

## 설계

### 1. 공용 유틸 (신규)

파일: `src/utils/tracker/reasoning_payload.py`

```python
@dataclass
class LLMInteraction:
    """LLM 노드의 프롬프트·응답 한 쌍."""
    prompt_variables: dict[str, Any]
    raw_response: str


def build_llm_reasoning_payload(
    *,
    node: str,
    phase: str,
    round: int,
    hypothesis_id: str,
    interaction: LLMInteraction,
    routing: dict[str, Any],
    parsed_summary: dict[str, Any] | None = None,
    extra_inputs: dict[str, Any] | None = None,
    step_type: str = "llm_decision",
) -> dict[str, Any]:
    """LLM 노드용 REASONING_STEP payload 구성."""
```

규약:
- `inputs = {"prompt_variables": {...}, **extra_inputs}` — extra_inputs 는 프롬프트에 없는 메타(raw_query, dialect 등) 만 허용, 프롬프트 변수와 중복 금지.
- `output = {"raw_response": "...", "parsed": {...}}` — parsed 는 선택. 없으면 생략.

`src/utils/tracker/__init__.py` 에서 export.

### 2. Visualizer 렌더링 확장

파일: `src/utils/tracker/visualizer.py`

`_render_input` 함수에 prompt_variables 분기 추가:
- 각 변수를 `### {var_name}` 소제목으로 섹션 나누어 출력
- 긴 값(예: tool_results 수십 KB)은 코드블록 안에 그대로 출력 — 절단 없음
- 빈 문자열·`(없음)` 값은 한 줄 요약

`_render_output` 함수에 raw_response 분기 추가:
- `◄ **LLM 원본 응답**` 헤더
- ```` ```json ... ``` ```` 코드블록
- 그 뒤 기존 `parsed` 렌더링 (8_slot, table_decisions 등)

### 3. 서비스 레이어 시그니처 확장

다음 서비스 함수들이 `(parsed, LLMInteraction)` tuple 을 반환하도록 변경:

| 파일 | 함수 | 기존 반환 | 변경 반환 |
|------|------|-----------|-----------|
| `src/services/intent_classifier.py` | `classify_intent` | `IntentResult` | `tuple[IntentResult, LLMInteraction]` |
| `src/services/query_normalizer.py` | `normalize_query` | `NormalizedQuery` | `tuple[NormalizedQuery, list[LLMInteraction]]` (phase1+phase2) |
| `src/services/data_analyzer.py` | `analyze_data`, `judge_visualization`, `generate_svg_via_llm` | `Analysis`/`VizJudgment`/`str` | 각각 `tuple[..., LLMInteraction]` |

query_normalizer 는 phase1+phase2 2회 호출 — 리스트로 반환하여 노드가 각각 dispatch.

### 4. LLM 노드별 변경

각 노드에서:
1. 서비스에서 받은 `LLMInteraction` (혹은 노드 내부에서 직접 구성한 값) 을 `build_llm_reasoning_payload()` 에 전달
2. 기존 축약 필드 (`tool_results_summary`, `filters: count`, `sql[:200]`, `summary[:200]` 등) 모두 제거
3. `parsed_summary` 에는 필수 분기용 값 (status, verdict, action 등) 만 포함

대상 노드:
- interpret/intent_classifier.py
- interpret/query_normalizer.py (phase1, phase2 각각 dispatch)
- reason/context_interpreter.py
- reason/sql_generator.py
- reason/sql_validator.py (LLM layer 2b)
- reason/recovery_agent.py (`full_variables` → `prompt_variables` 로 키 통일 + raw_response 추가)
- present/analyzer.py (서비스 호출 결과 interaction 활용)
- present/visualizer.py (judgment + SVG 각각)

### 5. 비-LLM 노드

- **context_retriever**: `_extract_result_count` 제거, `step.raw_result` 전체를 `results[*].raw_result` 로 기록. count 는 렌더링에서 `len(raw_result)` 로 계산.
- **sql_executor**: 실행 SQL 원문 + 결과 전체 (rows/columns) 기록. 현재 구현 확인 후 gap 있으면 보강.
- **readiness_gate, reasoning_preparer, formatter, clarification_handler**: 현재 rule_decision 패턴 충실 — 유지.

### 6. A-04 같은 버그 판별성

변경 후 A-04 재측정 시 trace 에서 다음이 확인 가능해야 함:
- query_normalizer.raw_response 에 `{"filters": [{"target": "수익률", "filter_type": "LT", "values": ["0"]}], ...}` 포함 여부
- context_interpreter.prompt_variables.tool_results 에 Qdrant/MongoDB 검색 결과 전체 포함
- sql_generator.prompt_variables.confirmed_knowledge 에 `K2: filter:수익률=['0']` 형태로 주입된 문자열 포함

## PR 분할

### P1 — 인프라
- `src/utils/tracker/reasoning_payload.py` 신규
- `src/utils/tracker/__init__.py` export
- `src/utils/tracker/visualizer.py` 렌더링 확장 (`_render_input`/`_render_output`)
- 단위 테스트: `tests/auto/unit/test_reasoning_payload.py`

### P2 — Critical LLM 노드 3개
- query_normalizer (서비스 + 노드)
- context_interpreter (노드 직접 LLM 호출)
- sql_generator (노드 직접 LLM 호출)

### P3 — 나머지 LLM 노드
- intent_classifier (서비스 + 노드)
- sql_validator (LLM layer 2b)
- recovery_agent (full_variables → prompt_variables 키 통일 + raw_response)
- analyzer (서비스 + 노드)
- visualizer (판정 + SVG)

### P4 — 비-LLM 노드
- context_retriever: raw_result 전체 기록
- sql_executor: 현황 확인 후 보강 (필요 시)

### P5 — 검증
- A-04, V-04, D-02, C-04 E2E 재측정
- trace 가독성 점검, 과도한 중복 데이터 제거 여부 재검토
- 이 문서에 완료 체크 + done/ 이동

## 변경 매트릭스

| 노드 | 파일 | 현재 dispatch 라인 | 변경 요지 |
|------|------|-------------------:|-----------|
| intent_classifier | nodes/interpret/intent_classifier.py | 약 278-311 | extra_inputs 에 raw query/history 메타, prompt_variables 는 서비스 반환값 사용 |
| query_normalizer | nodes/interpret/query_normalizer.py | 234-284 | phase1/phase2 각각 dispatch, filter_count 삭제 |
| context_interpreter | nodes/reason/context_interpreter.py | 253-293 | tool_results_summary 삭제, prompt_variables 사용 (render_prompt 결과 활용) |
| sql_generator | nodes/reason/sql_generator.py | 312-342 | sql[:200] 삭제, raw_response 추가, confirmed_terms 는 parsed_summary 로 격하 |
| sql_validator | nodes/reason/sql_validator.py | 349-369 | layer별 각 LLM 호출에 prompt_variables/raw_response 부착 |
| recovery_agent | nodes/reason/recovery_agent.py | 307-355 | `full_variables` → `prompt_variables` 로 키 교체 (데이터는 동일), output 에 raw_response 추가 |
| analyzer | nodes/present/analyzer.py | 108-148 | 모든 필드 절단 제거, parsed_summary 는 주요 분기 필드만 |
| visualizer (판정) | nodes/present/visualizer.py | 131-156 | LLM 판정 결과만 |
| visualizer (SVG) | services/data_analyzer.py | 호출 지점에 맞춰 신규 dispatch | SVG 스트리밍 응답을 raw_response 로 |
| context_retriever | nodes/reason/context_retriever.py | 520-553 | raw_result 전체 기록, _extract_result_count 제거/격하 |
| sql_executor | nodes/reason/sql_executor.py | 별도 확인 | 현황 파악 후 결정 |

## 검증

### P1 단위 테스트
- `build_llm_reasoning_payload` 의 inputs/output 구조 검증
- `LLMInteraction` 빈 값/None 처리

### P5 E2E
- 실패 시나리오 3개 재측정 (A-04, V-04, D-02)
- trace 에서 prompt_variables·raw_response 로 LLM 판단 근거 재구성 가능한지 수작업 확인
- A-04 의 normalizer filter 실제 출력 확인 → "필터 환각 vs 기획 의도 대로" 판별

### 회귀 방지
- `pytest tests/auto/unit/` 그린 유지
- 기존 E2E PASS 시나리오 (C-04 등) 변경 없음 확인
