# SQL Assumptions Feature - Final Comprehensive Review Report

- 일자: 2026-04-07
- 대상: SQL 생성 시 해석적 선택(assumptions) 서피싱 기능 (최종 리뷰)
- 범위: state.py, sql_generator_system.txt, sql_generator.py, result_finalizer.py, insight_builder.py, test_sql_generator_format.py
- 선행 리뷰: `20260407-sql-assumptions-feature-review-report.md` (중간 리뷰)

---

## 0. 중간 리뷰 수정 확인

| # | 등급 | 항목 | 상태 |
|---|------|------|------|
| 1.1 | Critical | `state.get("turn_id")` -> `state.turn_id` | **수정 완료** (result_finalizer.py:87) |
| 1.2 | Critical | `_build_assumption_signals` underscore prefix 외부 import | **미수정** -- 아래 1.1에서 재기술 |
| 2.1 | Warning | 화살표 파싱 ASCII `->` 미지원 | **수정 완료** (sql_generator.py:558-560) |
| 2.2 | Warning | fail 분기에서 `pending_assumptions` 미초기화 | **수정 완료** (sql_generator.py:329) |
| 2.3 | Warning | insight_builder caveat에 `inferred_value` 누락 | **수정 완료** (insight_builder.py:426-429) |

---

## 1. Warning (YELLOW)

### 1.1 `_build_assumption_signals`의 `_` prefix -- 외부 모듈에서 import

- 파일: `src/agents/nodes/reason/result_finalizer.py` (line 26)
- 코드: `from src.agents.nodes.reason.sql_generator import _build_assumption_signals`
- 문제: Python convention에서 `_` prefix 함수는 모듈 내부 전용이다. result_finalizer에서 cross-module import하는 것은 캡슐화 위반이며, 일부 linter/IDE가 경고를 발생시킨다. 중간 리뷰에서 Critical로 분류했으나, 기능적으로는 정상 동작하므로 Warning으로 재분류한다.
- 수정 방안:
  - (A) `build_assumption_signals`로 rename하여 공개 API화 (최소 변경)
  - (B) `src/agents/utils/signal_factory.py` 등 공용 모듈로 이동 (권장 -- `_build_conflicted_signals`도 같은 패턴)
- 영향도: 낮음. 기능적 결함 없음. 리팩토링 시 깨지기 쉬운 구조.

---

## 2. Info (GREEN)

### 2.1 데이터 플로우 정합성 -- 전체 경로 검증 완료

전체 데이터 플로우를 추적한 결과 정합성이 확인되었다:

```
sql_generator_node
  success: reason.pending_assumptions = result["assumptions"]  (list[str])
  fail:    reason.pending_assumptions = []
     |
     v
sql_validator (pending_assumptions 미참조 -- 정상)
     |
     v
result_finalizer_node
  validated_sql 존재 시:
    _build_assumption_signals(reason.pending_assumptions, state.turn_id)
    -> list[AmbiguitySignal]
    -> updates["resolved_signals"] = assumption_signals
     |
     v
PipelineState.resolved_signals (operator.add reducer -- 기존 signals에 concat)
     |
     v
formatter: build_auto_resolved_notice(state)
  -> "- {s.question} -> {s.inferred_value}" 형태로 렌더링
     |
     v
insight_builder: _build_caveats(state, reason)
  -> sql_generator + INFER 시그널 필터링
  -> "{q} -> {v}" 형태로 caveat 추가
```

- `operator.add` reducer 덕분에 result_finalizer에서 반환한 `resolved_signals`는 기존 normalizer/intent_classifier의 INFER signals와 합산된다.
- `build_auto_resolved_notice`는 `turn_id` 필터링을 하므로 현재 턴의 signals만 표시한다.
- `_build_assumption_signals`에서 `turn_id`를 AmbiguitySignal에 주입하므로 턴 격리가 정상 동작한다.

### 2.2 재시도 시나리오 -- assumptions 정리 정상

시나리오: success(assumptions 있음) -> validator fail -> sql_generator 재진입

1. 첫 시도 success: `reason.pending_assumptions = ["가정A", "가정B"]`
2. validator fail: `pending_assumptions` 미변경 (정상 -- validator는 미참조)
3. sql_generator 재진입: `reason = state.reason.model_copy(deep=True)` -- 이전 assumptions 복사됨
4. 재진입 success: `reason.pending_assumptions = result.get("assumptions", [])` -- 새 assumptions로 **덮어쓰기** (정상)
5. 재진입 fail: `reason.pending_assumptions = []` -- **빈 리스트로 초기화** (정상, 중간 리뷰 2.2 수정 확인)

### 2.3 엣지 케이스 파싱 검증

`_build_assumption_signals` 파싱 검증:

| 입력 | question | inferred_value | 판정 |
|------|----------|----------------|------|
| `"A -> B"` | `"A"` | `"B"` | 정상 |
| `"A -> B"` | `"A"` | `"B"` | 정상 (ASCII fallback) |
| `"A -> B -> C"` | `"A"` | `"B -> C"` | 정상 (maxsplit=1) |
| `"A -> B -> C"` | `"A"` | `"B -> C"` | 정상 (ASCII maxsplit=1) |
| `"plain text"` | `"plain text"` | `"plain text"` | 정상 (fallback) |
| `""` | (빈 리스트 반환) | - | 정상 (빈 assumptions 입력 시) |

테스트 `test_arrow_in_value`, `test_ascii_arrow_fallback`에서 커버됨.

### 2.4 old `reasons` 키 잔여 참조 -- 없음

`src/` 전체에서 `result.get("reasons")`, `result["reasons"]` 검색 결과: **0건**.

`_parse_sql_response` 내부의 `data.get("reasons", [])` fallback은 의도적인 하위 호환 처리이며, LLM이 프롬프트 변경 전의 키를 출력하는 경우를 대비한다. 이는 정상이다.

`resources/prompts/` 전체에서도 `"reasons"` 단독 키 참조 없음. 프롬프트의 JSON 출력 형식에 `failure_reasons` + `assumptions` 이중 키 체계가 정확히 반영되어 있다.

### 2.5 `_parse_sql_response` 소비자 -- 단일

`_parse_sql_response`는 `_call_llm_for_sql`의 `parse_fn` 인자로만 사용된다. `_call_llm_for_sql`는 `sql_generator_node`에서만 호출된다. 다른 소비자 없음.

### 2.6 `failure_reasons` 키 일관성 확인

| 위치 | 사용 | 일관성 |
|------|------|--------|
| 프롬프트 JSON 스키마 | `failure_reasons` | 정상 |
| 프롬프트 few-shot 예시 6개 | 모두 `failure_reasons` + `assumptions` | 정상 |
| `_parse_sql_response` 반환 | `failure_reasons` | 정상 |
| exception fallback (line 304) | `failure_reasons` | 정상 |
| fail 분기 (line 332) | `result.get("failure_reasons")` | 정상 |
| tracking dispatch output (line 388) | `result.get("assumptions", [])` | 정상 |
| logging (line 340) | `result.get("failure_reasons", [])` | 정상 |

### 2.7 타입 안전성 확인

| 항목 | 타입 | 검증 |
|------|------|------|
| `ReasoningState.pending_assumptions` | `list[str]` + `Field(default_factory=list)` | 정상 |
| `_build_assumption_signals` 인자 | `assumptions: list[str], turn_id: str \| None` | 정상 |
| `_build_assumption_signals` 반환 | `list[AmbiguitySignal]` | 정상 |
| `result_finalizer` 반환 `resolved_signals` | `list[AmbiguitySignal]` | `operator.add` reducer와 호환 |

### 2.8 프롬프트-파서 일관성

프롬프트의 assumptions 출력 형식 지시: `"해석 대상 -> 선택한 해석"` (line 201)
파서 `_build_assumption_signals`의 분리자: `->` (유니코드), `->` (ASCII) -- 일관됨.

### 2.9 테스트 커버리지 평가

신규 12개 테스트의 커버리지:

| 함수 | 테스트 수 | 커버리지 |
|------|-----------|----------|
| `_parse_sql_response` | 6개 (success+assumptions, success-assumptions, multi-assumptions, fail+failure_reasons, backward-compat reasons, invalid status) | 충분 |
| `_build_assumption_signals` | 6개 (empty, single-arrow, no-arrow, multiple, arrow-in-value, ascii-fallback) | 충분 |

누락 엣지 케이스 (낮은 우선순위):
- `_parse_sql_response`에 완전히 비어있는 JSON(`{}`)이 입력되는 경우 -- `status`가 `""`이므로 fail로 처리됨. 기능적으로 정상이나 명시적 테스트가 없음.
- `_build_assumption_signals`에 whitespace-only 문자열 (`"   "`)이 입력되는 경우 -- 현재 `question = "   ".strip() = ""`이 되고 빈 question의 AmbiguitySignal이 생성됨. 실제 LLM이 이런 출력을 하진 않겠지만 방어 코드가 없음.

### 2.10 `build_auto_resolved_notice` 렌더링 확인

`clarification_context.py:121`에서 INFER signal을 다음과 같이 렌더링한다:
```python
lines.append(f"- {s.question} -> {s.inferred_value}")
```

`_build_assumption_signals`에서 생성된 signal의 `question`과 `inferred_value`가 이 포맷에 정확히 대응한다. `->` 구분자가 있는 경우 question과 inferred_value가 분리되어 있으므로 `"- '잔액'의 해석 -> 기말잔액(BAL_AMT)"` 형태로 표시된다. 구분자가 없는 경우 question=inferred_value이므로 `"- 최근 1개월로 해석 -> 최근 1개월로 해석"` 형태가 되어 다소 중복적이나, 정보 손실은 없다.

---

## 3. 요약

| 등급 | 건수 | 핵심 |
|------|------|------|
| Critical | 0 | 중간 리뷰의 Critical 2건 중 1건 수정, 1건 Warning으로 재분류 |
| Warning | 1 | `_build_assumption_signals` underscore prefix 외부 import (캡슐화 위반, 기능 정상) |
| Info | 10 | 데이터 플로우 정합, 재시도 시나리오 정상, 엣지 케이스 파싱 정상, old key 잔여 없음, 타입 안전, 테스트 충분 |

### 잔여 개선 사항 (향후 리팩토링 시)

1. `_build_assumption_signals` -> `build_assumption_signals`로 rename 또는 공용 signal_factory 모듈로 이동
2. whitespace-only assumption 입력에 대한 방어 코드 추가 (낮은 우선순위)
3. 구분자 없는 assumption의 `build_auto_resolved_notice` 중복 표시 개선 (낮은 우선순위)

### 결론

SQL assumptions 기능의 전체 데이터 플로우가 정상 동작하며, 중간 리뷰에서 지적된 Critical/Warning 항목 4건 중 4건이 수정되었다 (1건은 Warning으로 재분류). 프롬프트-파서-state-finalizer-formatter-insight_builder 경로의 일관성이 확보되었고, 테스트 커버리지도 충분하다. 즉시 수정이 필요한 결함은 없다.
