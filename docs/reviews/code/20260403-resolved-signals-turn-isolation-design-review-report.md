# 설계 리뷰: resolved_signals 턴 간 격리

- **일자**: 2026-04-03
- **대상 문서**: `docs/reviews/design/20260403-resolved-signals-turn-isolation.md`
- **리뷰어**: Code Reviewer Agent
- **상태**: 리뷰 완료

---

## 요약 평가

전반적으로 문제 진단이 정확하고, `turn_id` 기반 필터링 접근은 `operator.add` 리듀서의 누적 특성을 훼손하지 않으면서 소비자 측에서 격리를 달성하는 합리적인 설계이다. 다만, 아래의 Critical 1건, Warning 4건, Info 2건의 이슈가 식별되었다.

---

## Critical (1건)

### C-01. `_route_after_clarify`의 `resolved_signals[-1]` 접근이 현재 턴 시그널이 아닌 이전 턴 시그널을 참조할 수 있다

**파일**: `src/agents/graph/pipeline.py` 라인 333-334

```python
def _route_after_clarify(state: PipelineState) -> str:
    if state.resolved_signals:
        target = state.resolved_signals[-1].source_node
```

**문제**: 설계 문서 4절에서 이 함수는 "변경하지 않는 파일"로 분류하며, 그 이유를 "`resolved_signals[-1]`은 항상 현재 턴의 마지막 시그널 (operator.add가 append이므로)"라고 설명한다. 이 전제는 **현재 턴에서 시그널이 반드시 1건 이상 생성될 때만 성립**한다.

그러나 다음 시나리오에서 이 전제가 깨진다:

- `clarification_handler_node`가 빈 `pending_signals`를 받을 경우 (`return {}`) - 이 경우 `resolved_signals`에 현재 턴 시그널이 추가되지 않으며, `[-1]`은 이전 턴의 마지막 시그널을 참조한다.

현재 파이프라인 라우팅 구조상 `pending_signals`가 비어있으면 `clarification_handler`로 라우팅되지 않으므로 이론적으로는 발생하지 않지만, **향후 노드 추가나 라우팅 변경 시 잠재적 위험**이 있다. 또한, `clarification_handler`가 `infer + [best]`를 반환하는 ASK 경로에서 resume 실패 등으로 예외가 발생하면 `resolved_signals`가 갱신되지 않은 채 라우팅이 진행될 수 있다.

**권장**: 설계의 "변경하지 않는 파일" 목록에서 제외하고, `_route_after_clarify`에도 `turn_id` 기반 필터를 적용하는 것을 고려해야 한다:

```python
def _route_after_clarify(state: PipelineState) -> str:
    current_signals = [
        s for s in state.resolved_signals
        if s.turn_id == state.turn_id
    ]
    if current_signals:
        target = current_signals[-1].source_node
        ...
```

---

## Warning (4건)

### W-01. `sql_generator.py`의 T4 Cross-DB INFER 시그널에 `turn_id`가 설정되지 않는 경로가 존재한다

**파일**: `src/agents/nodes/reason/sql_generator.py` 라인 179-206

설계 문서 3.4절에서 `clarification_handler`를 거치는 경로의 시그널에는 `turn_id`가 주입된다고 설명한다. 그러나 sql_generator의 T4 시그널은 `pending_signals`에 기록되므로 `clarification_handler`를 거치기는 한다. 문제는 설계에서 sql_generator를 **변경 파일 목록에 포함하지 않았다**는 점이다.

`clarification_handler`에서 `pending_signals`의 시그널에 `turn_id`를 주입하므로 기능적으로는 커버되지만, 설계 문서의 변경 파일 요약 (7절)에 sql_generator가 누락되어 있으면 리뷰어와 구현자 모두 혼란을 겪을 수 있다. "변경하지 않는 파일"의 명확한 근거 표에 sql_generator를 추가하여 `pending_signals` 경로이므로 `clarification_handler`에서 커버됨을 명시해야 한다.

### W-02. `build_clarification_context`에서 ASK 시그널에 `turn_id` 필터를 적용하면 명확화 복귀 시 맥락이 손실될 수 있다

**파일**: `src/agents/utils/clarification_context.py` - 설계 문서 3.6절

설계에서 제안하는 코드:

```python
asks = [
    s for s in state.resolved_signals
    if s.decision == "ASK" and s.turn_id == tid
]
```

`build_clarification_context`는 `normalize_query_node`와 `sql_generator`에서 호출된다. 이 함수의 ASK 시그널은 **같은 턴 내에서** 여러 차례 interrupt/resume 사이클을 거친 명확화 Q&A 이력이다.

문제: 같은 턴 내에서 ASK가 여러 번 발생하는 경우 (예: context_classifier에서 ASK 1회 -> resume -> normalize_query에서 다시 ASK 1회), 두 시그널 모두 같은 `turn_id`를 갖는 것은 맞다. 그러나 현재 코드에서는 **같은 턴의 이전 라운드 ASK도 포함**하므로 이것이 의도대로 동작한다.

다만, `build_clarification_context`의 현재 소비자 중 `sql_generator`(라인 304)는 reason 계층에서 호출되는데, 이 시점에서는 interpret 계층의 ASK 시그널뿐 아니라 reason 계층 도중 발생한 ASK (예: readiness_gate T5)도 필요하다. 모두 같은 `turn_id`이므로 문제없지만, **설계 문서에서 이 다중 interrupt 시나리오를 데이터 흐름 검증 (6절)에서 다루지 않았다**. 다중 ASK 시나리오에 대한 데이터 흐름 검증 케이스를 추가해야 한다.

### W-03. `PipelineState.turn_id`의 기본값 `""` (빈 문자열)와 `AmbiguitySignal.turn_id`의 기본값 `None` 사이의 불일치가 엣지 케이스를 만든다

설계 문서 3.1절에서:
- `AmbiguitySignal.turn_id: str | None = None`
- `PipelineState.turn_id: str = ""`

설계 문서 3.2절의 설명: "빈 문자열 기본값 -> CLI 단독 실행 시에도 정상 동작 (필터가 `""` == `None`이 아니므로 구턴 시그널 제외)"

이 논리는 맞다. 그러나 **다음 시나리오에서 의도치 않은 동작**이 발생한다:

1. CLI에서 `turn_id`를 설정하지 않고 실행 (`turn_id = ""`)
2. `query_normalizer`가 시그널을 생성할 때 `turn_id=state.turn_id`로 설정 -> `turn_id = ""`
3. `build_auto_resolved_notice`에서 `s.turn_id == tid` -> `"" == ""` -> True -> 표시됨

이것은 CLI 단독 실행에서는 정상이다. 그런데:

4. 같은 세션에서 다음 턴 실행 시, 새 `turn_id`가 UUID로 생성됨
5. 이전 턴의 `turn_id=""` 시그널은 `"" != "uuid-xxx"`이므로 정상 제외됨

여기까지는 문제가 없다. 그러나 **runner.py에서 `turn_id=str(uuid.uuid4())`를 설정하는 코드가 빠질 경우** (개발자 실수), 모든 턴의 시그널이 `turn_id=""`를 갖게 되어 턴 격리가 전혀 작동하지 않는다. 이 실패 모드는 **조용하게 발생**한다(런타임 에러 없이 단지 이전 턴 시그널이 포함됨).

**권장**: `PipelineState`에서 `turn_id`를 default가 아닌 required로 만들거나, `clarification_context.py`에서 빈 `turn_id`일 때 경고 로그를 남기는 방어 코드를 추가할 것을 권장한다.

### W-04. 테스트 파일에서 `AmbiguitySignal` 생성 시 `turn_id` 미설정에 따른 테스트 커버리지 공백

**파일**: `tests/auto/unit/test_clarify_node.py`, `tests/auto/e2e/test_agentic_core.py`, `tests/auto/e2e/test_agentic_e2e.py`

설계 문서 4절: "테스트 3개 -- `turn_id: str | None = None`이므로 기존 테스트에서 미설정 시 None -> 호환"

하위 호환성은 맞지만, **턴 격리 로직 자체를 검증하는 테스트가 없다**. 최소한 다음 테스트가 필요하다:

1. 멀티턴 시나리오에서 `build_auto_resolved_notice`가 현재 턴 INFER만 반환하는지
2. 멀티턴 시나리오에서 `build_clarification_context`가 현재 턴 시그널만 반환하는지
3. `_route_after_clarify`가 현재 턴 시그널의 `source_node`로 올바르게 라우팅하는지
4. `ask_count`가 여전히 세션 전체 ASK를 카운트하는지 (회귀 방지)

설계 문서에 테스트 계획 섹션을 추가해야 한다.

---

## Info (2건)

### I-01. 대안 분석에서 "리듀서 교체" 방안이 누락되었다

설계 문서 2.2절에서 4가지 대안을 비교하고 있으나, LangGraph가 지원하는 **커스텀 리듀서 함수** 방안이 검토되지 않았다:

```python
def _turn_scoped_add(
    existing: list[AmbiguitySignal],
    new: list[AmbiguitySignal],
) -> list[AmbiguitySignal]:
    """현재 턴 시그널만 유지하는 커스텀 리듀서."""
    if not new:
        return existing
    return existing + new

resolved_signals: Annotated[
    list[AmbiguitySignal], _turn_scoped_add,
] = Field(default_factory=list)
```

이 방안은 소비자 측 변경 없이 생산자 측에서만 제어할 수 있다는 장점이 있지만, `ask_count`와 `_build_clarification_history`가 세션 전체 이력을 필요로 하므로 채택이 어려웠을 것이다. 그럼에도 대안 비교 표에 이 방안과 탈락 사유를 명시하면 설계 결정의 완결성이 높아진다.

### I-02. `turn_id` 필드가 로그/감사 추적에서 활용될 수 있다

현재 설계는 `turn_id`를 순수하게 필터링 목적으로만 사용한다. 그러나 `turn_id`는 세션 내 각 턴을 고유하게 식별하므로:

- 로그에 `turn_id`를 포함하면 멀티턴 디버깅이 크게 개선된다
- `DataCopilotCallbackHandler`에 `turn_id`를 전달하면 추적 리포트에서 턴 단위 분석이 가능해진다
- 향후 세션 이력 조회 API에서 턴 단위 필터링에 활용할 수 있다

구현 범위를 확대할 필요는 없으나, 설계 문서의 "향후 확장 가능성" 섹션에 기록해두면 좋다.

---

## 완전성 검증: 누락된 참조 확인

코드베이스 전체를 `resolved_signals` 및 `AmbiguitySignal`으로 검색한 결과, 설계 문서에서 식별하지 못한 참조는 다음과 같다:

| 파일 | 참조 | 설계 문서 커버 여부 |
|------|------|:---:|
| `sql_generator.py:17,28,34,159,179,206` | `pending_signals`에 T4 INFER 기록 + `build_clarification_context` 호출 | `clarification_handler` 경유이므로 간접 커버되나, **4절 "변경하지 않는 파일" 표에 누락** |
| `result_finalizer.py:25,49,63,188-217` | `pending_signals`에 T5 ASK 기록 | `clarification_handler` 경유이므로 간접 커버되나, **4절 표에 누락** |
| `reasoning_preparer.py:172,199` | 주석에서만 `resolved_signals` 언급 (읽기/쓰기 없음) | 영향 없음 |
| `미사용_intent_classifier.py:34,124,129` | 미사용 파일 (dead code) | 영향 없음, 별도 정리 권장 |

**결론**: 시그널 생산자 `sql_generator`와 `result_finalizer`가 4절의 "변경하지 않는 파일" 명시적 확인 표에 포함되어 있지 않다. 기능적으로는 `clarification_handler`에서 `turn_id` 주입이 이루어지므로 문제 없지만, 설계 문서의 완전성을 위해 표에 추가해야 한다.

---

## 정확성 검증

### operator.add 리듀서와 turn_id의 상호작용

turn_id 접근은 리듀서 자체를 건드리지 않으며, 소비자 측 필터링만 추가한다. `operator.add`는 이전과 동일하게 리스트를 누적하고, 소비 시점에서 `turn_id`로 걸러낸다. **기술적으로 정확하다.**

### 체크포인터 역직렬화 호환성

Pydantic v2의 `model_validate`는 미지 필드를 무시하고, Optional 필드의 기본값(`None`)을 적용한다. 기존 체크포인트의 `AmbiguitySignal` 데이터에 `turn_id` 키가 없으므로 `turn_id=None`으로 역직렬화된다. `None != "uuid-xxx"`이므로 필터에서 자동 제외된다. **하위 호환성이 확보된다.**

### `_ALLOWLIST_MODULES` 영향

`src.agents.models.clarification`이 이미 등록되어 있으므로 필드 추가로 인한 allowlist 변경은 불필요하다. **정확하다.**

---

## 최종 의견

| 등급 | 건수 | 요약 |
|------|:---:|------|
| Critical | 1 | `_route_after_clarify`의 `[-1]` 접근이 이전 턴 시그널을 참조할 수 있는 잠재적 위험 |
| Warning | 4 | 데이터 흐름 검증 케이스 부족, 기본값 불일치, 변경 대상 표 누락, 테스트 계획 부재 |
| Info | 2 | 대안 분석 보완, turn_id의 로그/감사 활용 가능성 |

설계의 핵심 접근(turn_id 필터링)은 건전하며, 위의 지적 사항을 반영한 후 구현에 착수할 것을 권장한다. 특히 C-01은 현재 파이프라인에서는 발생 확률이 낮으나, 방어적 프로그래밍 관점에서 수정하는 것이 안전하다.
