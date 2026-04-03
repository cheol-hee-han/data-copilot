# recovery_agent 재계획 전용 리팩터링 코드 리뷰

- 일시: 2026-04-02
- 대상: recovery_agent ReAct 내부 루프 제거 -> 재계획 전용 노드 리팩터링
- 변경 파일: recovery_agent.py, pipeline.py, recovery_agent_system.txt, config.py

---

## 검토 요약

recovery_agent를 ReAct 내부 루프(도구 실행 + 해석 + 판정)에서 재계획 전용 노드(LLM 1회 호출)로 변환하는 리팩터링이다. 도구 실행/해석/평가 책임을 기존 파이프라인 노드(knowledge_fetcher, knowledge_interpreter, readiness_gate)에 위임하여 SRP를 달성하고, 그래프 구조의 일관성을 높인 좋은 설계 변경이다.

전반적으로 잘 구현되었으며, 아래에 등급별로 발견 사항을 정리한다.

---

## Critical (RED) -- 즉시 수정 필요

### C-01. `_RecoveryPlan` 이 plain class이며 Pydantic 미사용 -- 타입 안전성 저하

- 위치: `src/agents/nodes/reason/recovery_agent.py:200-218`
- 현상: `_RecoveryPlan`이 `__slots__` 기반 plain class로 구현되어 있다. 프로젝트의 다른 모든 데이터 구조(KnowledgeItem, Hypothesis, ExecutionStep, DeadEnd 등)는 Pydantic BaseModel을 사용한다.
- 영향:
  - 필드 검증이 없어 LLM 파싱 결과에서 잘못된 타입(예: action이 int)이 들어와도 감지 불가
  - `model_copy()` 등 Pydantic 헬퍼를 사용할 수 없음
  - 코드 일관성 위반 (code-style.md: "Validation: Pydantic v2")
- 제안: Pydantic BaseModel로 변환

```python
class _RecoveryPlan(BaseModel):
    action: Literal["replan", "give_up"]
    lessons_learned: str = ""
    execution_plan: list[ExecutionStep] = Field(default_factory=list)
    new_hypothesis: Hypothesis | None = None
```

### C-02. `should_terminate` 검사 후 가설 소진 조건에서 의도치 않은 즉시 종료 가능

- 위치: `recovery_agent.py:85`, `state.py:523-539`
- 현상: `should_terminate()`는 `current_hypothesis is None and len(pending) == 0`일 때도 True를 반환한다. `_handle_hypothesis_transition()`(line 73)에서 현재 가설을 FAILED로 전환한 후 PENDING 가설이 없으면 `current_hypothesis = None`이 되는데, 이 상태에서 `should_terminate()`가 True를 반환하여 LLM 호출 없이 즉시 give_up으로 빠진다.
- 영향: LLM이 새 가설(new_hypothesis)을 생성할 기회를 얻기 전에 종료되므로, 재계획의 핵심 기능(새 접근법 제안)이 작동하지 않는 케이스가 존재한다.
- 제안: `should_terminate()` 검사를 LLM 호출 **이후**로 이동하거나, recovery_agent 전용 종료 조건을 분리하여 가설 소진 조건을 제외한다.

```python
# 방안 1: recovery_agent 전용 guard
def _should_terminate_recovery(reason: ReasoningState) -> bool:
    g = reason.loop_guard
    return (
        g.total_tool_calls >= MAX_TOOL_CALLS
        or g.replan_count >= MAX_REPLANS
        or g.generate_attempts >= MAX_GENERATES
        or reason.final_status == FinalStatus.FAILURE
        # 가설 소진은 제외 -- LLM이 new_hypothesis를 생성할 수 있으므로
    )
```

### C-03. `except (ParseError, Exception)` -- 과도하게 넓은 예외 포착

- 위치: `recovery_agent.py:252`
- 현상: `ParseError`를 명시적으로 잡는 것은 좋으나, `Exception`을 함께 잡으면 `KeyboardInterrupt`, `SystemExit` 외의 모든 예외(CancelledError 포함)가 삼켜진다. 비동기 함수에서 `asyncio.CancelledError`가 삼켜지면 graceful shutdown이 불가능해진다.
- 제안:

```python
except (ParseError, ValueError, TimeoutError) as e:
    logger.warning("recovery_agent LLM 호출 실패", error=str(e))
    return None
```

---

## Warning (YELLOW) -- 개선 권장

### W-01. 프롬프트 placeholder 3개가 recovery_agent.py에서 치환되나 프롬프트 파일에 존재하지 않음

- 위치: `recovery_agent.py:406-408`, `recovery_agent_system.txt`
- 현상: `_build_prompt()`의 replacements에 `{candidate_knowledge}`, `{previous_tool_results}`, `{empty_tools_warning}` 3개가 있으나, `recovery_agent_system.txt`에는 이 placeholder가 없다. `.replace()`는 대상 문자열이 없으면 무시하므로 에러는 발생하지 않지만, 죽은 코드로 유지보수 혼란을 유발한다.
- 제안: 이 3개 항목을 replacements dict에서 제거하거나, 프롬프트에서 실제 사용할 placeholder를 추가한다.

### W-02. `_parse_plan_response`에서 Hypothesis import가 함수 내부에 위치

- 위치: `recovery_agent.py:294`
- 현상: `from src.agents.state.state import Hypothesis`가 함수 내부에 있다. 파일 상단에서 이미 `state.py`의 여러 타입을 import하고 있으므로 순환 참조 문제는 아니다.
- 영향: 함수 호출 시마다 import 해소가 발생한다 (CPython 캐시로 성능 영향은 미미하나 스타일 불일치).
- 제안: 파일 상단 import 블록으로 이동한다.

### W-03. `_build_prompt`에서 `TableSelectionStatus` import가 함수 내부에 위치

- 위치: `recovery_agent.py:368`
- 현상: W-02와 동일한 패턴. `TableSelectionStatus`는 이미 `state.py`에서 re-export되고 있으므로 상단 import 가능하다.
- 제안: 파일 상단으로 이동한다.

### W-04. `__init__.py` docstring에 레거시 설명 잔존

- 위치: `src/agents/nodes/__init__.py:24-25`
- 현상:
  - line 24: `recovery_planner: 실패 분석 + 재계획 (레거시)` -- 레거시 노드 설명이 여전히 존재
  - line 25: `recovery_agent: ReAct-style 반응적 복구 루프` -- 더 이상 ReAct가 아닌 재계획 전용
- 제안:
  ```
  - recovery_agent: 실패 분석 + 재계획 전용 (LLM 1회, 도구 실행 없음)
  ```
  recovery_planner 라인은 삭제한다.

### W-05. tracker 및 관련 유틸에 `recovery_planner` 레거시 참조 다수 잔존

- 위치:
  - `src/utils/tracker/callback_handler.py:120-124` -- "recovery_planner" 노드 설정
  - `src/utils/tracker/trace_analyzer.py:408,413` -- recovery_planner 기준 replan 집계
  - `src/utils/tracker/visualizer.py:53,120,129,177,364` -- recovery_planner 기준 사이클 감지/표시
  - `src/services/insight_builder.py:342` -- recovery_planner 라벨
- 현상: recovery_planner는 레거시이며 그래프에 등록되지 않는다. 이 참조들은 실제 트레이스에서 매칭되지 않으므로 죽은 코드이다. recovery_agent와 이중 등록되어 있어 혼란을 유발한다.
- 제안: `recovery_planner` 참조를 일괄 제거하고, 사이클 감지 기준을 `recovery_agent`로 변경한다.

### W-06. `readiness_gate.py` docstring에 "ReAct 복구 루프" 잔존

- 위치: `src/agents/nodes/reason/readiness_gate.py:10`
- 현상: `REPLAN -> recovery_agent (ReAct 복구 루프)` -- 더 이상 ReAct가 아님
- 제안: `REPLAN -> recovery_agent (재계획)` 으로 수정

### W-07. `_finalize_give_up`에서 GENERATING phase 전환 시 readiness_gate 우회

- 위치: `recovery_agent.py:431-440`
- 현상: force-generate 시 `reason.phase = Phase.GENERATING`으로 설정하면 `_route_after_recovery_agent`가 `sql_generator`로 직행한다. 이는 readiness_gate의 강제 생성 로직(readiness_gate.py:62-75)과 중복되며, readiness_gate의 추적 이벤트(dispatch_tracking_event)를 건너뛴다.
- 영향: 추적 로그에서 force-generate 경로가 누락될 수 있다.
- 제안: recovery_agent에서 GENERATING으로 직접 전환하는 경로에도 추적 이벤트를 발행하거나, force-generate 판단을 readiness_gate에 일원화하는 것을 검토한다.

---

## Info (GREEN) -- 참고 사항

### I-01. pipeline.py 라우팅 검증 결과: 정상

- `_route_after_recovery_agent` 반환값: `knowledge_fetcher`, `sql_generator`, `result_finalizer`, `clarification_handler`
- 엣지 맵(line 511-519): 4개 반환값 모두 등록됨 -- 정합성 확인 완료
- recovery_agent 노드의 phase 설정:
  - 재계획 성공: `Phase.EXPLORING` -> `knowledge_fetcher` (정상)
  - force-generate: `Phase.GENERATING` -> `sql_generator` (정상)
  - give_up: `Phase.DONE` -> `result_finalizer` (정상)

### I-02. 무한 루프 방지: 동작 확인

- `loop_guard.increment_replan()` (line 77)이 매 진입 시 호출됨
- `should_terminate()` (line 85)에서 `replan_count >= MAX_REPLANS (3)` 검사
- readiness_gate의 강제 생성 전환(replan_count >= 2 + score >= 55%)이 추가 안전장치
- 결론: 무한 루프 방지는 정상 동작하되, C-02에서 지적한 가설 소진 케이스는 별도 검토 필요

### I-03. config.py에서 삭제된 ReAct 전용 설정 참조 검사: 정상

- `react_max`, `react_tool`, `max_react` 패턴으로 전체 검색 결과 매칭 없음
- 삭제된 설정을 참조하는 코드가 없어 안전함

### I-04. thinking_modes.py의 recovery_agent 설정

- `recovery_agent: "off"` (line 30) -- 재계획 전용 노드에서 thinking이 불필요하므로 적절함
- 다만, recovery_agent가 이제 LLM 1회 호출로 재계획을 수립하는 역할이므로, "auto"로 변경을 검토할 여지가 있음 (단순 JSON 구조 응답이라면 "off" 유지가 합리적)

### I-05. `max_conflicted_bounces` 설정은 recovery_agent.py에서 미참조

- `config.py:236`에 정의되어 있으나, recovery_agent 리팩터링 후 사용처가 없음
- 다른 파일에서도 참조하지 않음 (grep 결과 config.py만 매칭)
- 죽은 설정이 되었을 가능성 있음 -- 별도 확인 필요

### I-06. 프롬프트-코드 placeholder 정합성 검증

프롬프트 파일의 placeholder와 `_build_prompt()` replacements 매칭:

| 프롬프트 placeholder | replacements key | 상태 |
|---|---|---|
| `{entry_source_description}` | O | 정상 |
| `{confirmed_knowledge}` | O | 정상 |
| `{unresolved_items}` | O | 정상 |
| `{candidate_tables_summary}` | O | 정상 |
| `{dead_ends_summary}` | O | 정상 |
| `{searched_use_case_queries}` | O | 정상 |
| (프롬프트에 없음) | `{candidate_knowledge}` | W-01 (죽은 코드) |
| (프롬프트에 없음) | `{previous_tool_results}` | W-01 (죽은 코드) |
| (프롬프트에 없음) | `{empty_tools_warning}` | W-01 (죽은 코드) |

---

## 수정 우선순위

| 순위 | ID | 수준 | 예상 공수 | 설명 |
|---|---|---|---|---|
| 1 | C-02 | Critical | 30분 | should_terminate 가설 소진 조건 분리 |
| 2 | C-03 | Critical | 5분 | 예외 범위 축소 |
| 3 | C-01 | Critical | 15분 | _RecoveryPlan Pydantic 변환 |
| 4 | W-01 | Warning | 5분 | 죽은 placeholder 제거 |
| 5 | W-04,W-06 | Warning | 10분 | 레거시 docstring 정리 |
| 6 | W-05 | Warning | 30분 | tracker 내 recovery_planner 레거시 참조 일괄 정리 |
| 7 | W-02,W-03 | Warning | 5분 | 함수 내부 import를 상단으로 이동 |
| 8 | W-07 | Warning | 20분 | force-generate 추적 이벤트 보완 |

---

## 총평

리팩터링의 설계 방향(ReAct 내부 루프 제거, 재계획 전용화, 기존 파이프라인 루프 재활용)은 올바르다. pipeline.py의 라우팅 변경, 엣지 맵, 무한 루프 방지가 모두 정합성을 유지하고 있다. Critical 3건 중 C-02(가설 소진 시 조기 종료)가 실제 동작에 영향을 줄 수 있는 가장 중요한 항목이며, 나머지는 타입 안전성과 에러 처리 개선이다.

레거시 `recovery_planner` 참조가 tracker/visualizer/insight_builder에 다수 남아 있어, 별도 정리 작업으로 일괄 처리하는 것을 권장한다.
