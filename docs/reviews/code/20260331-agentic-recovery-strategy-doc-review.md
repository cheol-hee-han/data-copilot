# Agentic Recovery 재설계 전략 문서 리뷰

- **리뷰 대상**:
  - `docs/strategy-proposals/agentic-recovery-redesign/01-strategy.md`
  - `docs/strategy-proposals/agentic-recovery-redesign/02-detailed-design.md`
- **리뷰 유형**: 문서 정합성 (Documentation Consistency)
- **리뷰 일자**: 2026-03-31
- **검증 기준**: 두 문서 간 내부 일관성 + 실제 코드베이스 정확성

---

## 요약

| 등급 | 건수 |
|------|------|
| Critical | 4 |
| Warning | 7 |
| Info | 4 |

---

## Critical (코드베이스와 불일치 -- 구현 시 혼란 유발)

### C-01: `should_terminate()`는 메서드가 아닌 모듈 레벨 함수

**위치**: 01-strategy 3.3절 (line ~277), 02-detailed-design 4.2절 (line ~342)

두 문서 모두 `reason.should_terminate()`를 ReAct 루프 종료 조건으로 사용한다.

```python
# 01-strategy line 277
while not reason.should_terminate():

# 02-detailed-design line 342
if reason.should_terminate():
```

**실제 코드** (`state.py` line 508):
```python
def should_terminate(reason: ReasoningState) -> bool:
```

`should_terminate`는 `ReasoningState`의 메서드가 아닌 **모듈 레벨 독립 함수**이다. `reason.should_terminate()`는 `AttributeError`를 발생시킨다.

**수정 방안**: 문서 내 호출부를 `should_terminate(reason)`으로 변경하거나, 구현 시 `should_terminate`를 `ReasoningState`의 메서드로 이동할지 결정하고 문서에 반영한다.

---

### C-02: `_execute_steps()` 함수가 실제로 존재하지 않음

**위치**: 01-strategy 1.2절 Phase 1 (line 61), 3.2절 knowledge_fetcher 추출 대상 (line 162), 02-detailed-design 2.1절 (line 160, 173)

두 문서 모두 `_execute_steps()`를 context_explorer에서 추출할 함수로 명시한다.

**실제 코드**: `context_explorer.py`에 `_execute_steps`라는 이름의 함수는 **존재하지 않는다**. Phase 1 도구 실행 루프는 `context_explorer_node()` 함수 내부에 **인라인**으로 작성되어 있다 (lines 261-282).

```python
# 실제 코드 (context_explorer.py lines 261-282)
# _execute_steps() 함수가 아닌 인라인 for 루프
for step in execution_plan:
    if step.status != StepStatus.PENDING or total_tool_calls >= MAX_TOOL_CALLS:
        continue
    ...
```

**영향**: Step 1의 "기계적 분리"가 함수 이동이 아닌 **인라인 코드 추출**이 되므로, 예상보다 리팩터링 범위가 넓다. 이동 대상 함수 목록에서 `_execute_steps()`를 제거하고 "인라인 루프를 함수로 추출 후 이동"으로 수정해야 한다.

---

### C-03: `_remove_unsuitable_tables()` 함수가 실제로 존재하지 않음

**위치**: 01-strategy 3.2절 knowledge_interpreter 추출 대상 (line 172), 02-detailed-design 2.2절 이동 대상 (line 230)

두 문서 모두 `_remove_unsuitable_tables()`를 knowledge_interpreter로 이동할 함수로 명시한다.

**실제 코드**: 이 이름의 함수 정의는 context_explorer.py에 **존재하지 않는다**. docstring (line 29)에만 참조가 있고, Phase 5의 실제 구현은 `context_explorer_node()` 내부에 인라인 코드(line 327~341 부근)로 작성되어 있다.

**수정 방안**: C-02와 동일 -- "인라인 코드를 함수로 추출 후 이동"으로 기술을 수정한다.

---

### C-04: RecoveryDecision 스키마 불일치 (01-strategy vs 02-detailed-design)

**위치**: 01-strategy 3.3절 (line 238-245), 02-detailed-design 1.2절 (line 86-95)

| 필드 | 01-strategy | 02-detailed-design |
|------|-------------|-------------------|
| `table_updates` | **없음** | `list[TableUpdate]` 포함 |
| `lessons_learned` | `str` (required처럼 기술) | `str = ""` (default 있음) |
| `target_knowledge_gap` | `str` (required, default 없음) | `str = ""` (default 있음) |

01-strategy의 `RecoveryDecision` 스키마에는 `table_updates` 필드가 없다. 02-detailed-design에서 `TableUpdate` 모델과 함께 추가되었으나, 01-strategy가 갱신되지 않았다.

또한 01-strategy의 프롬프트 스키마(부록 A, line 935-963)에는 `table_updates`가 포함되어 있어, **같은 문서 내에서도 불일치**가 발생한다.

**수정 방안**: 01-strategy의 RecoveryDecision 정의에 `table_updates` 필드를 추가하고, default 값 유무를 02-detailed-design과 일치시킨다.

---

## Warning (정확도 이슈 -- 구현 참고 시 주의 필요)

### W-01: Force-generate 조건의 verdict 비교 방식 불일치

**위치**: 02-detailed-design 3절 readiness_gate (line 264-268)

```python
# 02-detailed-design의 readiness_gate 코드
if (
    reason.loop_guard.replan_count >= 2
    and score >= THRESHOLD_FORCE_GENERATE
    and verdict in (ReadinessVerdict.REPLAN, ReadinessVerdict.TERMINATE)
):
```

**실제 코드** (`confidence_evaluator.py` line 61-64):
```python
if (
    verdict.value in ("replan", "conclude_failure")
    and reason.loop_guard.replan_count >= 2
    and score >= THRESHOLD_FORCE_GENERATE
):
```

차이점:
1. 실제 코드는 `verdict.value in ("replan", "conclude_failure")`로 **문자열 비교**, 문서는 `verdict in (ReadinessVerdict.REPLAN, ReadinessVerdict.TERMINATE)`로 **enum 비교**
2. `ReadinessVerdict.TERMINATE`의 value는 `"conclude_failure"`이다 -- 의미는 동일하지만 코드 스타일이 다름
3. 조건 순서가 다름 (기능적 차이 없음)

**권장**: 문서가 실제 구현보다 더 나은 패턴(enum 직접 비교)을 제시하고 있으므로, 구현 시 문서 패턴을 채택하되 이 차이를 인지할 것.

---

### W-02: THRESHOLD_FORCE_GENERATE 값 기술 불일치

**위치**: 01-strategy 6.3절 (line 641)

```
if replan_count >= 2 AND readiness_score >= THRESHOLD_FORCE_GENERATE (0.55):
```

**실제 코드** (`confidence_evaluator.py` line 16): docstring에는 "score >= 40%"라고 기술되어 있다.

```python
# confidence_evaluator.py line 16
#     2회 이상 replan 후에도 score >= 40%이면 더 탐색해도 개선 가능성이 낮으므로
```

**실제 값** (`confidence_scorer.py` line 22): `THRESHOLD_FORCE_GENERATE = 0.55`

01-strategy의 0.55는 실제 코드 값과 일치하지만, `confidence_evaluator.py`의 docstring "40%"가 stale이다. 전략 문서 자체의 오류는 아니지만, 구현 시 docstring과의 불일치로 혼란이 발생할 수 있다.

---

### W-03: `_route_after_confidence_evaluator`는 ReadinessVerdict.value 문자열을 반환

**위치**: 01-strategy 5.3절 라우팅 테이블 (line 583-587)

01-strategy는 readiness_gate의 라우팅을 `GENERATE`, `EXPLORE`, `REPLAN`, `ASK_USER`, `TERMINATE`로 기술한다.

**실제 코드** (`pipeline.py` line 189):
```python
return evaluate_readiness(state.reason).value
```

ReadinessVerdict의 실제 value:
- `GENERATE` -> `"generate_sql"` (not `"GENERATE"`)
- `EXPLORE` -> `"explore"`
- `REPLAN` -> `"replan"`
- `ASK_USER` -> `"ask_user"`
- `TERMINATE` -> `"conclude_failure"` (not `"TERMINATE"`)

pipeline.py의 conditional_edges (line 417-428)에서 `"ask_user"` -> `result_finalizer`로 매핑되어 있다.

문서에서 `ASK_USER -> clarification_handler`로 기술했지만, **현행 코드에서는 `ask_user -> result_finalizer`**이다. 현행 기술이 부정확하거나, 의도적 변경인지 명시가 필요하다.

---

### W-04: `_route_after_planner`의 반환값이 `"context_explorer"`

**위치**: 01-strategy 검토 5 (line 473-474)

```python
# pipeline.py -- 변경 전
def _route_after_planner(state):
    if state.reason.fast_path_triggered:
        return "sql_generator"
    return "context_explorer"
```

**실제 코드** (`pipeline.py` line 174-180): 정확히 일치한다. 다만 실제 코드의 함수 시그니처는 `state: PipelineState`로 타입 힌트가 있으나, 문서에서는 `state`만 기술. 사소한 차이.

---

### W-05: `_observe_all_date_distributions`, `_sample_unsampled_tables` 시그니처 오류

**위치**: 02-detailed-design 2.1절 (line 160, 164)

```python
# 02-detailed-design
await _observe_all_date_distributions(reason)
await _sample_unsampled_tables(reason)
```

**실제 코드** (`context_explorer.py` lines 294-295, 840, 885):
```python
await _observe_all_date_distributions(candidate_tables)
await _sample_unsampled_tables(candidate_tables)
```

실제 함수는 `ReasoningState`가 아닌 `candidate_tables` (list)를 받는다. knowledge_fetcher로 이동 시 시그니처를 `reason`으로 통합할 수도 있지만, "시그니처 변경 없음"이라는 02-detailed-design 2.1절의 기술과 모순된다.

---

### W-06: context_explorer 6-Phase에 `_collect_observations` 함수 불일치

**위치**: 01-strategy 1.2절 Phase 2 (line 61)

01-strategy는 Phase 2의 함수를 `_observe_all_date_distributions()`, `_sample_unsampled_tables()`로 기술한다.

**실제 코드**: context_explorer.py의 docstring (line 27)에는 `_collect_observations`로 Phase 2를 기술하지만, 이 함수도 실제로는 존재하지 않는다. `_observe_all_date_distributions()`과 `_sample_unsampled_tables()`가 `context_explorer_node()` 내부에서 직접 호출된다 (line 294-295).

01-strategy의 함수명은 실제 코드 함수명과 일치하므로 정확하다. 다만 docstring의 `_collect_observations`는 stale이다.

---

### W-07: TOOL_MAP에 `detect_date_pattern`, `extract_hints_from_use_cases` 미등록

**위치**: 01-strategy 1.3절 (line 116) "TOOL_MAP 디스패처"

01-strategy는 TOOL_MAP을 "도구 추가/제거가 선언적"이라고 보존 대상으로 기술한다. 02-detailed-design의 ToolCall.tool Literal에는 6개 도구를 나열한다.

**실제 TOOL_MAP** (`tools.py` lines 279-287):
```python
TOOL_MAP = {
    "search_use_cases", "search_table_meta", "search_code_meta",
    "search_manual", "search_glossary",
    "get_sample_rows", "get_date_distribution",
}
```

7개 도구가 등록되어 있다. 문서의 6개 도구 목록에서 `search_use_cases`가 제외된 것은 의도적이며 (01-strategy line 248에서 이유 설명), 정확하다. 그러나 tools.py에는 `detect_date_pattern`과 `extract_hints_from_use_cases`도 정의되어 있지만 TOOL_MAP에는 미등록이다. 이 두 함수는 execute_tool로 호출되지 않고 context_explorer에서 직접 호출되는 유틸리티이므로 문서 기술에 문제는 없다.

---

## Info (내부 일관성 -- 양호하거나 사소한 차이)

### I-01: execute_tool 시그니처 정확히 기술됨

02-detailed-design 7.2절 (line 998)에서 `execute_tool(tool_name, tool_input)` 시그니처가 `tool_input: str`임을 정확히 지적하고, kwargs->str 어댑터 필요성을 논의한다.

**실제 코드** (`tools.py` line 290): `async def execute_tool(tool_name: str, tool_input: str) -> Any`

정확하다.

---

### I-02: LoopGuard 종료 조건 5종 정확

01-strategy 6.1절 (line 618-625)의 5종 종료 조건과 기본값:
- `total_tool_calls >= 20` -- config.py line 230: `max_tool_calls: int = 20` (일치)
- `replan_count >= 3` -- config.py line 231: `max_replans: int = 3` (일치)
- `generate_attempts >= 4` -- config.py line 232: `max_generates: int = 4` (일치)
- `final_status == FAILURE` -- state.py line 519 (일치)
- hypotheses 소진 -- state.py line 520-523 (일치)

모두 정확하다.

---

### I-03: Phase enum 값 정확

01-strategy 5.4절의 Phase 전이 매핑이 사용하는 Phase 값(`PLANNING`, `EXPLORING`, `GENERATING`, `REPLANNING`, `VALIDATING`, `DONE`)은 enums.py의 실제 정의와 정확히 일치한다. `VERIFYING` Phase도 실제로 존재한다.

---

### I-04: Hypothesis 생명주기 기술 정확

01-strategy 3.3절 및 02-detailed-design 4.3절의 hypothesis 관리 로직:
- `ACTIVE -> FAILED` 전이
- `DeadEnd` 생성 시 `failure_type`, `reason` 사용
- `PENDING` hypothesis 소비 시 priority 정렬

**실제 코드** (`recovery_planner.py` lines 86-110)와 비교하면 로직이 정확히 일치한다. 다만 실제 코드는 `model_copy()`를 사용한 immutable 패턴이고, 문서의 pseudo-code는 직접 mutation 패턴이다 (구현 선택의 문제이며 불일치는 아님).

---

## 종합 판정

| 영역 | 판정 |
|------|------|
| 두 문서 간 내부 일관성 | **C-04 제외 양호** -- RecoveryDecision의 table_updates 불일치만 수정 필요 |
| 노드 이름 일관성 | 양호 -- knowledge_fetcher, knowledge_interpreter, readiness_gate, recovery_agent가 두 문서에서 동일 |
| 코드베이스 정확성 | **보통** -- 함수 존재 여부(C-02, C-03)와 should_terminate 호출 방식(C-01)이 부정확 |
| LoopGuard/Phase/Hypothesis | 양호 -- 구조, 값, 생명주기 모두 정확 |
| 라우팅 로직 | 양호 -- 현행 구조를 정확히 기술, 변경 방향도 합리적 |
| LLM 호출 횟수 분석 | 양호 -- 현행 2회/cycle 대비 개선 분석이 정확 |

### 구현 전 필수 수정 사항

1. **01-strategy RecoveryDecision에 `table_updates` 추가** (C-04)
2. **두 문서의 `_execute_steps()`, `_remove_unsuitable_tables()` 기술을 "인라인 코드를 함수로 추출 후 이동"으로 수정** (C-02, C-03)
3. **`reason.should_terminate()` -> `should_terminate(reason)` 으로 수정하거나, 구현 시 메서드로 이관할지 결정** (C-01)
4. **02-detailed-design의 `_observe_all_date_distributions(reason)` 시그니처를 `candidate_tables`로 수정하거나, "시그니처 변경 있음"으로 기술 변경** (W-05)
