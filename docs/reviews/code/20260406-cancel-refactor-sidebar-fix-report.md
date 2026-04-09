# Cancel 리팩토링 + 사이드바 빈세션 수정 - 최종 검증 리뷰

> 일시: 2026-04-06
> 대상: cancel 중앙화 리팩토링 (Part 2) + 사이드바 빈세션 필터링 (Part 1)
> 검토 범위: cancel.py, pipeline.py, runner.py, turn_text_store.py, 11개 노드 파일

---

## 검증 체크리스트 결과

| # | 항목 | 결과 |
|---|------|------|
| 1 | mid-node 체크 3건 유지 | PASS |
| 2 | 미사용 import 잔존 여부 | 1건 발견 (pipeline.py) |
| 3 | 라우팅 엣지맵 정합성 | PASS |
| 4 | 사이드바 쿼리 기존 기능 영향 | PASS (주의사항 1건) |
| 5 | 유저 턴 중복 저장 방지 로직 | 1건 버그 발견 |
| 6 | cancel.py with_cancel_check 래퍼 | PASS (개선 제안 1건) |
| 7 | pipeline.py FinalStatus 미사용 import | 1건 발견 |

---

## Critical (0건)

없음.

---

## Warning (2건)

### W-01: runner.py 427행 - `_user_turn_id` 미정의 NameError 가능성

**파일**: `src/agents/graph/runner.py` 라인 427
**등급**: Warning

정상 완료 경로에서 `pipeline_result.user_turn_id = _user_turn_id`를 참조하지만,
이 변수는 clarification 경로(321행)에서만 정의된다.

정상 경로의 360-367행에서 `user_turn_saved`가 False일 때 `save_turn`을 호출하지만
반환값을 `_user_turn_id`에 할당하지 않는다. `user_turn_saved`가 True인 경우(조기 저장 성공)에는
`save_turn` 자체가 호출되지 않으므로 `_user_turn_id`가 어디에서도 정의되지 않는다.

try 블록 내부이므로 `NameError`가 발생해도 except에서 잡히긴 하지만,
턴 저장은 성공했는데 `user_turn_id`가 누락되는 사일런트 실패가 발생한다.

**수정 제안**:
```python
# _execute_and_finalize() 시작부에 초기화 추가
user_turn_saved = user_turn_saved_early
_user_turn_id: str | None = None  # <-- 추가

# 조기 저장 시 turn_id도 캡처 (run_pipeline 97~109행)
_user_turn_id_early: str | None = None
# ...
_user_turn_id_early = await save_turn(...)
# ...
# _execute_and_finalize에 전달

# 또는 정상 경로에서 save_turn 호출 시 반환값 캡처
if not user_turn_saved:
    _user_turn_id = await save_turn(...)  # <-- 반환값 캡처
    user_turn_saved = True
```

---

### W-02: pipeline.py 51행 - `FinalStatus` 미사용 import

**파일**: `src/agents/graph/pipeline.py` 라인 51
**등급**: Warning

`FinalStatus`는 import되지만 pipeline.py 내에서 사용되는 곳이 없다.
cancel.py에서 직접 `from src.agents.state.state import FinalStatus`로 import하고 있다.

**수정 제안**: pipeline.py의 import 블록에서 `FinalStatus,` 제거.

```python
from src.agents.state.state import (
    FailureType,
    # FinalStatus,  <-- 제거
    IntentType,
    Phase,
    PipelineState,
    QueryStatus,
)
```

---

## Info (4건)

### I-01: cancel.py `with_cancel_check` - 타입 힌트 보강 가능

**파일**: `src/agents/graph/cancel.py` 라인 86
**등급**: Info

`node_fn` 파라미터에 `# noqa: ANN001`로 타입 힌트를 생략했다.
`Callable`을 사용하면 완전한 시그니처를 표현하기 어려우므로 현재 처리가 합리적이나,
`Protocol`이나 `ParamSpec`으로 개선 가능하다.

```python
from typing import Callable, Awaitable

NodeFn = Callable[["PipelineState"], Awaitable[dict]]

def with_cancel_check(node_fn: NodeFn) -> NodeFn:
```

---

### I-02: turn_text_store.py `_has_turns` - f-string SQL 조립 패턴

**파일**: `src/services/turn_text_store.py` 라인 307-332
**등급**: Info

`_has_turns` 문자열을 f-string으로 SQL에 삽입하고 있다.
사용자 입력이 아닌 코드 내 상수이므로 SQL 인젝션 리스크는 없으나,
보안 규칙(`SQL은 반드시 파라미터 바인딩 사용, f-string 금지`)의 예외로서
주석에 안전성 근거를 명시하면 향후 리뷰 시 혼란을 방지할 수 있다.

```python
# 상수 SQL 조각 — 사용자 입력 미포함, 인젝션 리스크 없음
_has_turns = (
    "EXISTS ("
    ...
)
```

---

### I-03: runner.py 조기 저장 시 turn_id 반환값 미캡처

**파일**: `src/agents/graph/runner.py` 라인 103-108
**등급**: Info (W-01과 연관)

`save_turn()`의 반환값(turn_id)을 캡처하지 않고 있다.
W-01 수정 시 함께 `_user_turn_id_early`를 캡처하여
`_execute_and_finalize`에 전달하는 것이 바람직하다.

---

### I-04: mid-node cancel 체크 3건 - 정상 유지 확인

**파일들**:
- `src/agents/nodes/reason/context_interpreter.py` 라인 387: Level1 루프 내 cancel 체크 -- PASS
- `src/agents/nodes/reason/sql_validator.py` 라인 110: Layer2b 전 cancel 체크 -- PASS
- `src/agents/nodes/present/analyzer.py` 라인 63: `_is_cancelled` 콜백 -- PASS

3건 모두 함수 내부 지역 import(`from src.agents.graph.cancel import ...`)로
적절하게 유지되어 있으며, 노드 진입 시 cancel 체크와는 독립적으로 동작한다.

---

## 파일별 검증 결과 요약

| 파일 | 상태 | 비고 |
|------|------|------|
| `src/agents/graph/cancel.py` | OK | CANCEL_MESSAGE, with_cancel_check, make_cancel_updates 모두 정상 |
| `src/agents/graph/pipeline.py` | W-02 | FinalStatus 미사용 import 1건 |
| `src/agents/graph/runner.py` | W-01 | `_user_turn_id` 미정의 위험, 조기 저장 turn_id 미캡처 |
| `src/services/turn_text_store.py` | OK | EXISTS 서브쿼리 정상, 파라미터 바인딩 준수 |
| `src/agents/nodes/interpret/intent_classifier.py` | OK | cancel import 없음 (with_cancel_check로 대체) |
| `src/agents/nodes/interpret/query_normalizer.py` | OK | cancel import 없음 (with_cancel_check로 대체) |
| `src/agents/nodes/reason/context_retriever.py` | OK | cancel import 없음 (with_cancel_check로 대체) |
| `src/agents/nodes/reason/context_interpreter.py` | OK | mid-node 체크만 유지 (387행) |
| `src/agents/nodes/reason/readiness_gate.py` | OK | cancel import 없음 (with_cancel_check로 대체) |
| `src/agents/nodes/reason/sql_generator.py` | OK | cancel import 없음 (with_cancel_check로 대체) |
| `src/agents/nodes/reason/sql_validator.py` | OK | mid-node 체크만 유지 (110행) |
| `src/agents/nodes/reason/recovery_agent.py` | OK | cancel import 없음 (with_cancel_check로 대체) |
| `src/agents/nodes/present/sql_executor.py` | OK | cancel import 없음 (with_cancel_check로 대체) |
| `src/agents/nodes/present/analyzer.py` | OK | mid-node 체크만 유지 (63행) |
| `src/agents/nodes/present/formatter.py` | OK | cancel import 없음 (with_cancel_check로 대체) |

---

## 라우팅 엣지맵 정합성 검증

| 라우팅 함수 | 반환값 | 엣지맵 포함 여부 |
|-------------|--------|-----------------|
| `_route_after_intent_classifier` | error_end | OK (458행) |
| `_route_after_normalize` | error_end | OK (466행) |
| `_route_after_readiness_gate` | CANCELLED -> conclude_failure | OK (result_finalizer 매핑) |
| `_route_after_recovery_agent` | CANCELLED -> result_finalizer | OK (519행) |
| `_route_after_result_finalizer` | CANCELLED -> error_end | OK (531행) |
| `_route_after_execution` | CANCELLED -> error_end | OK (553행) |

모든 라우팅 함수의 CANCELLED 분기가 해당 엣지맵에 포함되어 있음을 확인.

---

## 결론

전체적으로 cancel 중앙화 리팩토링이 깔끔하게 수행되었다.
**즉시 수정이 필요한 항목은 W-01 (`_user_turn_id` 미정의)** 1건이며,
이 버그는 try-except 안에 있어 런타임 크래시로 이어지지는 않지만
정상 경로에서 `user_turn_id`가 항상 누락되는 사일런트 실패를 유발한다.
W-02(FinalStatus 미사용 import)는 기능 영향 없는 정리 사항이다.
