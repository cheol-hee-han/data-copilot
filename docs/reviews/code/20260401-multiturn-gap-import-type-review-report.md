# 멀티턴 아키텍처 갭 해소 8-파일 변경 리뷰

- 리뷰 일시: 2026-04-01
- 리뷰 범위: 체크포인터 기반 멀티턴 아키텍처 갭 해소 8개 파일
- 중점 사항: import 정합성, 린트 오류, 타입 안전성, 레거시 전환 완결성, 라우팅 일관성

---

## Critical (R-01 ~ R-02)

### R-01. sql_generator의 pending_signals가 sql_validator에 무시됨 (라우팅 구조 결함)

- 파일: `src/agents/graph/pipeline.py` (line 504-506)
- 파일: `src/agents/nodes/reason/sql_generator.py` (line 202-205)

**현상**: `sql_generator_node()`에서 Cross-DB 감지 시 `pending_signals`를 반환하지만,
`sql_generator -> sql_validator`는 `add_edge()` (무조건 직행)로 연결되어 있다.
따라서 `pending_signals`가 설정되어도 `clarification_handler`로 라우팅되지 않고 `sql_validator`가 실행된다.
`sql_validator`는 `generated_sql`이 None인 상태로 진입하여 예기치 않은 동작을 유발할 수 있다.

**비교**: `recovery_agent` 후 라우팅(line 289-323)에서는 `pending_signals` 검사가 첫 번째 조건으로 배치되어 정상 동작한다.

**해결 방안**: `sql_generator -> sql_validator` 직행 엣지를 conditional_edges로 변경하여 `pending_signals` 존재 시 `clarification_handler`로 분기해야 한다.

```python
# pipeline.py: add_edge 대신 conditional_edges 사용
def _route_after_sql_generator(state: PipelineState) -> str:
    if state.pending_signals:
        return "clarification_handler"
    return "sql_validator"

workflow.add_conditional_edges(
    "sql_generator",
    _route_after_sql_generator,
    {
        "clarification_handler": "clarification_handler",
        "sql_validator": "sql_validator",
    },
)
```

또한 `_VALID_RETURN_TARGETS`에 `"sql_generator"`를 추가해야 clarification_handler 후 복귀가 가능하다.


### R-02. _route_after_result_finalizer에서 레거시 필드 awaiting_clarification 직접 참조

- 파일: `src/agents/graph/pipeline.py` (line 336)

**현상**: `_route_after_result_finalizer()`에서 `state.awaiting_clarification`을 직접 참조한다.
이 함수는 T5 전환 대상으로, `pending_signals` 기반 패턴으로 전환되었어야 하지만 레거시 분기가 남아 있다.

```python
# line 336 - 레거시 참조 잔존
if state.awaiting_clarification:
    return "clarify_end"
```

T5 변경에서 `result_finalizer_node()`는 이미 CONFLICTED 항목을 `pending_signals`로 변환한다(line 49-63).
그러나 `_route_after_result_finalizer()`의 `awaiting_clarification` 분기는 레거시 흐름(runner.py에서 직접 설정)과
새 `pending_signals` 흐름이 혼재하는 상태를 만든다.

**영향**: `pending_signals` 검사(line 335)가 먼저 실행되므로 T5 신규 경로는 정상 동작하지만,
`awaiting_clarification`이 True인 채로 `pending_signals`가 비어 있는 레거시 경로도 여전히 활성이다.
이 두 경로가 동시에 존재하면 디버깅 시 혼란을 유발한다.

**해결 방안**: TODO 주석(state.py line 594)에 명시된 대로 레거시 명확화 필드 이관 계획을
명확히 하고, `_route_after_result_finalizer`의 `awaiting_clarification` 분기를 제거하거나
deprecation 주석을 추가해야 한다.

---

## Warning (R-03 ~ R-08)

### R-03. result_finalizer.py에 logger, add_trace 누락

- 파일: `src/agents/nodes/reason/result_finalizer.py`

**현상**: 다른 모든 노드(intent_classifier, query_normalizer, sql_generator 등)는
`get_logger(__name__)`와 `add_trace()`를 사용하여 실행 과정을 추적하지만,
`result_finalizer_node()`에는 logger 인스턴스도, trace_log 기록도 없다.

- logger import 없음
- add_trace import 없음 (state.py에서 re-export하는 add_trace 미사용)
- 성공/실패/CONFLICTED 3개 분기 모두 trace 없음

**영향**: 추론 루프의 마지막 노드에서 어떤 분기로 종료했는지 trace_log에 기록되지 않아
디버깅 및 감사 추적에 공백이 생긴다.

**해결 방안**:
```python
from src.utils.logger import get_logger
from src.agents.state.state import add_trace  # 이미 PipelineState import 블록에 추가 가능

logger = get_logger(__name__)
```
각 분기에 `add_trace()` 호출 추가.


### R-04. result_finalizer.py 독스트링과 실제 함수 불일치

- 파일: `src/agents/nodes/reason/result_finalizer.py` (line 17)

**현상**: 모듈 독스트링의 "핵심 함수" 목록에 `_build_clarification_question`이 남아 있지만,
실제로 이 함수는 제거되고 `_build_conflicted_signals`로 대체되었다.

```
핵심 함수:
    ...
    - _build_clarification_question: CONFLICTED 항목 -> 사용자 확인 질문  # 실재하지 않음
```

**해결 방안**: 독스트링을 `_build_conflicted_signals`로 갱신.


### R-05. _build_conflicted_signals의 파라미터 타입이 list[Any]

- 파일: `src/agents/nodes/reason/result_finalizer.py` (line 186-188)

**현상**: `conflicted_items: list` — 타입 힌트가 bare `list`이다.
호출부(line 53-55)에서는 `list[KnowledgeItem]`으로 필터링한 결과를 전달하므로
`list[KnowledgeItem]`이 정확한 타입이다.

```python
# 현재
def _build_conflicted_signals(
    conflicted_items: list,
) -> list[AmbiguitySignal]:

# 개선
from src.agents.state.state import KnowledgeItem

def _build_conflicted_signals(
    conflicted_items: list[KnowledgeItem],
) -> list[AmbiguitySignal]:
```

**영향**: mypy --strict에서 경고 발생. 함수 내부에서 `ki.key`, `ki.evidence`, `ki.status` 등
KnowledgeItem 속성에 접근하므로 타입 힌트가 없으면 IDE 자동완성도 동작하지 않는다.


### R-06. query_normalizer.py의 time_range 접근 시 getattr 불필요

- 파일: `src/agents/nodes/interpret/query_normalizer.py` (line 99)

**현상**:
```python
time_range = getattr(normalized, "time_range", None)
```

`NormalizedQuery` 모델에는 `time_range` 필드가 존재하지 않는다.
실제 필드명은 `time` (TimeSlot 타입)이다. `getattr`로 접근하므로 항상 `None`이 반환되어
로그에 `time_range=(없음)`이 찍힌다.

**영향**: 시간 범위 정보가 로그에 기록되지 않아 디버깅 시 정규화 결과를 정확히 파악할 수 없다.

**해결 방안**:
```python
time_slot = normalized.time
time_range_str = (
    f"type={time_slot.type}"
    if time_slot.type != "NONE"
    else None
)
```


### R-07. checkpointer.py의 serde allowlist 패턴 정합성

- 파일: `src/agents/graph/checkpointer.py` (line 61-63)

**현상**:
```python
serde = JsonPlusSerializer().with_msgpack_allowlist([
    ("src.",),
])
```

`with_msgpack_allowlist`의 인자가 `list[tuple[str]]` 형태인데,
LangGraph 공식 API에서 이 메서드의 인자 형식이 `list[str]`인지 `list[tuple[str]]`인지
확인이 필요하다. 현재 형태는 `("src.",)` 단일 요소 튜플로, prefix match인지 exact match인지
동작 방식에 따라 `src.agents.models.clarification.AmbiguitySignal` 등
구체적인 클래스 경로가 허용되지 않을 수 있다.

**해결 방안**: LangGraph `JsonPlusSerializer.with_msgpack_allowlist` API 문서 또는 소스를
확인하여 `("src.",)` 패턴이 `src.*` prefix match로 동작하는지 검증하고,
단위 테스트에서 `AmbiguitySignal`, `NormalizedQuery` 등 신규 모델의 직렬화/역직렬화를
명시적으로 검증해야 한다.


### R-08. intent_classifier.py의 T2 AmbiguitySignal에서 source_node 의도적 불일치

- 파일: `src/agents/nodes/interpret/intent_classifier.py` (line 113-117)

**현상**: `classify_intent_node()`에서 생성하는 AmbiguitySignal의 `source_node`가
`"normalize_query"`로 설정되어 있다. 주석에 "명확화 후 정규화로 진행"이라고 설명되어 있으나,
실제 발생 노드는 `classify_intent`이다.

이 설계는 의도적(clarify 후 복귀 대상을 normalize_query로 지정)이지만:
1. `source_node`가 "발생 노드"가 아니라 "복귀 대상 노드"로 사용되는 이중 의미를 갖게 된다
2. 감사 추적 시 어떤 노드에서 모호성이 감지되었는지 불분명해진다

**해결 방안**: AmbiguitySignal에 `return_node` 필드를 분리하거나, 현재 설계를 유지한다면
독스트링에 "source_node는 clarify 후 복귀 대상을 의미한다"는 규칙을 명확히 문서화해야 한다.
현 단계에서는 기존 패턴과의 일관성을 위해 주석 보강으로 충분하다.

---

## Info (R-09 ~ R-12)

### R-09. sql_generator.py에서 import time 제거 확인 완료

- 파일: `src/agents/nodes/reason/sql_generator.py`

검토 결과 `import time` 구문은 현재 파일에 존재하지 않는다. 제거가 정상 반영되었음을 확인.


### R-10. query_normalizer.py에서 IntentType 레거시 제거 확인 완료

- 파일: `src/agents/nodes/interpret/query_normalizer.py`

검토 결과 `IntentType` import는 현재 파일에 존재하지 않는다. 레거시 제거가 정상 반영되었음을 확인.


### R-11. state.py의 normalized_query 타입 전환 정상

- 파일: `src/agents/state/state.py` (line 54-55, 579)

`NormalizedQuery` import 추가 및 `normalized_query: NormalizedQuery | None = None` 타입 변경이
정상 반영되었다. `AmbiguitySignal` import도 추가되어 `pending_signals`, `resolved_signals`
필드의 타입이 올바르게 해석된다.

다만 `from __future__ import annotations` (line 19)가 있으므로 런타임 시 타입은 문자열로
평가된다. Pydantic v2는 `model_rebuild()`를 통해 지연 해석하므로 순환 import 위험은 없다.


### R-12. _VALID_RETURN_TARGETS에 누락된 대상 정리

- 파일: `src/agents/graph/pipeline.py` (line 362-365)

현재 `_VALID_RETURN_TARGETS`:
```python
frozenset({
    "resolve_history", "classify_intent", "normalize_query",
    "sql_generator", "readiness_gate", "result_finalizer",
})
```

`source_node` 값 전수 조사 결과:
| source_node 값 | 발생 위치 | _VALID_RETURN_TARGETS 포함 |
|---|---|---|
| `"resolve_history"` | history_resolver.py | O |
| `"normalize_query"` | intent_classifier.py, query_normalizer.py | O |
| `"sql_generator"` | sql_generator.py | O (단, R-01 참조: 현재 도달 불가) |
| `"readiness_gate"` | (미사용, 잠재적) | O |
| `"result_finalizer"` | result_finalizer.py | O |

`"classify_intent"`는 _VALID_RETURN_TARGETS에 포함되어 있으나 현재 어떤 노드에서도
`source_node="classify_intent"`로 시그널을 생성하지 않는다. 미래 확장을 위한 예약으로
판단되나, 사용되지 않는 항목이 있다는 점은 인지해 둘 필요가 있다.

---

## 요약

| 등급 | 건수 | 핵심 |
|------|------|------|
| Critical | 2 | R-01 Cross-DB 시그널 라우팅 불가, R-02 레거시/신규 혼재 |
| Warning | 6 | R-03~R-08 로깅 누락, 독스트링 불일치, 타입 미비, 필드명 오류 등 |
| Info | 4 | R-09~R-12 제거 확인, 타입 전환 확인, 라우팅 맵 정리 |

**우선 조치 권장 순서**:
1. R-01 (Critical): `sql_generator -> sql_validator` 직행 엣지를 conditional로 변경
2. R-06 (Warning): `time_range` -> `time` 필드명 오류 수정 (실질적 버그)
3. R-05 (Warning): 타입 힌트 보강
4. R-03, R-04 (Warning): 로깅/독스트링 정비
