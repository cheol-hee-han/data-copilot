# Cancel/Abort 코드 복잡도 및 아키텍처 리뷰 보고서

> 검토일: 2026-04-06
> 검토 범위: cancel/abort 관련 전체 코드 (src/ 14개 파일, tests/ 8개 파일)
> 검토 관점: 코드 복잡도, 중복, 에러 처리 일관성, 보안, 성능, 아키텍처 적합성
> 기존 리뷰: `20260406-pipeline-cancel-implementation-review-report.md` (보안/동시성 중심)
> 본 리뷰 초점: **"난개발" 우려에 대한 구조적 분석 및 리팩토링 제안**

---

## 요약

Cancel 기능의 설계 문서(`docs/todo/20260404-pipeline-cancel-design.md`)는 매우 잘 정리되어 있으며, LangGraph의 현실적 제약(asyncio.Task.cancel 버그 3건)을 회피하는 앱 레벨 플래그 방식은 합리적이다. 그러나 **구현 과정에서 cancel 체크가 12개 노드에 14회 산재**되어 있으며, **3가지 서로 다른 cancel 응답 패턴**이 혼재한다. 이는 "난개발" 인상의 핵심 원인이다.

| 등급 | 건수 |
|------|------|
| Critical | 1건 |
| Warning | 6건 |
| Info | 3건 |

---

## Critical

### C-01. 12개 노드에 산재된 cancel 보일러플레이트 -- 단일 책임 원칙 위반

**현상**

cancel 체크 코드가 12개 노드 파일에 14회 복사-붙여넣기되어 있다.

| 계층 | 파일 | 체크 횟수 | 패턴 |
|------|------|----------|------|
| interpret | `intent_classifier.py:124-128, 252-255` | 2 | 인라인 dict |
| interpret | `query_normalizer.py:48-52` | 1 | 인라인 dict |
| reason | `context_retriever.py:423-425` | 1 | `make_cancel_updates` |
| reason | `context_interpreter.py:103-105, 391-393` | 2 | `make_cancel_updates` + break |
| reason | `readiness_gate.py:67-69` | 1 | `make_cancel_updates` |
| reason | `sql_generator.py:215-217` | 1 | `make_cancel_updates` |
| reason | `sql_validator.py:64-66, 112-113` | 2 | `make_cancel_updates` |
| reason | `recovery_agent.py:72-74` | 1 | `make_cancel_updates` |
| present | `sql_executor.py:48-52` | 1 | 인라인 dict (다른 메시지) |
| present | `analyzer.py:54-58` | 1 | 인라인 dict |
| present | `formatter.py:54-59` | 1 | 인라인 dict (3필드) |
| **합계** | **12개 파일** | **14회** | **3가지 패턴** |

**문제점**

1. **중복**: 동일 로직이 14곳에 산재. 새 노드 추가 시 cancel 체크 누락 가능성 높음.
2. **일관성 부재**: 3가지 서로 다른 cancel 응답 dict 패턴이 사용됨 (아래 W-01 상세).
3. **지역 import 남용**: 모든 노드에서 `from src.agents.graph.cancel import check_cancel`을 함수 본문 내부에서 import. 12회 반복.
4. **변경 비용**: cancel 메시지 문구 변경 시 최소 6개 파일을 동시 수정해야 함.

**개선안: LangGraph 미들웨어 패턴으로 중앙화**

LangGraph에서는 `add_node` 시 래핑 함수를 사용하여 모든 노드에 대해 전처리 로직을 적용할 수 있다.

```python
# src/agents/graph/cancel.py 에 추가

def with_cancel_check(node_fn, *, use_reason: bool = False):
    """노드 함수에 cancel 체크를 주입하는 데코레이터.

    Args:
        node_fn: 원본 노드 함수 (async def fn(state) -> dict).
        use_reason: True면 make_cancel_updates(state.reason) 사용,
                    False면 간단한 CANCELLED dict 반환.
    """
    @functools.wraps(node_fn)
    async def wrapper(state: PipelineState) -> dict:
        if await check_cancel(state.session_id, state.turn_id):
            if use_reason:
                return make_cancel_updates(state.reason)
            return _CANCEL_SIMPLE_RESPONSE.copy()
        return await node_fn(state)
    return wrapper

_CANCEL_SIMPLE_RESPONSE = {
    "status": QueryStatus.CANCELLED,
    "error_message": "사용자 요청으로 중단되었습니다.",
}
```

```python
# src/agents/graph/pipeline.py -- build_pipeline() 내부
from src.agents.graph.cancel import with_cancel_check

# Interpret 계층 (reason 없음)
workflow.add_node("intent_classifier", with_cancel_check(intent_classifier_node))
workflow.add_node("normalize_query", with_cancel_check(normalize_query_node))

# Reason 계층 (reason deep copy 필요)
workflow.add_node("context_retriever", with_cancel_check(context_retriever_node, use_reason=True))
workflow.add_node("readiness_gate", with_cancel_check(readiness_gate_node, use_reason=True))
workflow.add_node("sql_generator", with_cancel_check(sql_generator_node, use_reason=True))
# ... 동일 패턴
```

**효과**:
- 14개 cancel 체크 -> `pipeline.py`의 `add_node` 선언부 1곳에서 관리
- 노드 코드에서 cancel 관련 코드 완전 제거
- 새 노드 추가 시 `with_cancel_check` 래핑만 하면 됨
- 단, `sql_validator.py:112`의 mid-node 체크와 `context_interpreter.py:391`의 루프 내 체크는 노드 내부에 유지 (이 2건만 예외)

**영향 범위**: 12개 노드 파일, `cancel.py`, `pipeline.py`
**위험도**: 중 (기존 테스트가 라우팅 + result_finalizer cancel을 검증하므로 안전망 있음)

---

## Warning

### W-01. 3가지 cancel 응답 패턴의 비일관성

**현상**

cancel 감지 시 반환하는 dict가 노드마다 다르다.

| 패턴 | 사용 노드 | 반환 필드 |
|------|----------|----------|
| A: `make_cancel_updates(reason)` | context_retriever, readiness_gate, sql_generator, recovery_agent, context_interpreter, sql_validator | `reason`, `status`, `error_message` |
| B: 인라인 dict (2필드) | intent_classifier, query_normalizer, analyzer, sql_executor | `status`, `error_message` (또는 `formatted_response`) |
| C: 인라인 dict (3필드) | formatter | `formatted_response`, `status`, `error_message` |

**구체적 불일관**:

1. `sql_executor.py:51` -- 메시지가 다름: "요청이 중단되었습니다. **다른 질문이 있으시면 말씀해 주세요.**"
2. `formatter.py:57-59` -- `formatted_response`를 추가로 설정 (다른 노드는 안 함)
3. `intent_classifier.py:127` -- `error_message`만 설정, `formatted_response` 미설정
4. `analyzer.py:57` -- `error_message`만 설정, 분석 결과 데이터 미정리

**위험**: CANCELLED 상태가 다양한 경로를 통과하면서 `formatted_response`가 설정되거나 안 되거나 하여, 최종 사용자에게 표시되는 메시지가 비결정적일 수 있다.

**개선안**: C-01의 `with_cancel_check` 래퍼에서 통일된 응답 dict를 반환하면 자동 해소된다.

---

### W-02. `make_cancel_updates`의 타입 힌트 부재

**파일**: `src/agents/graph/cancel.py:79`

```python
def make_cancel_updates(reason_state) -> dict[str, Any]:
```

`reason_state`에 타입 힌트가 없다. `ReasoningState`를 받아야 하며, `model_copy(deep=True)` 호출에서 `ReasoningState`임을 암묵적으로 가정한다. 프로젝트의 mypy --strict 기준에 위배된다.

**개선안**:

```python
from src.agents.state.state import ReasoningState

def make_cancel_updates(reason_state: ReasoningState) -> dict[str, Any]:
```

순환 참조가 우려되면 `TYPE_CHECKING` 가드를 사용한다.

---

### W-03. 와일드카드(`"*"`) 매칭의 보안 위험 및 복잡도 증가

**파일**: `src/agents/graph/cancel.py:50`, `src/routers/sessions.py:140`

**현상**: `check_cancel`이 정확한 `turn_id` 매칭 실패 시 `"*"` 와일드카드로 폴백한다. cancel API의 `turn_id` 기본값도 `"*"`이다.

```python
# cancel.py:50
if turn_id != "*" and await _cancel_store.is_cancelled(session_id, "*"):
    return True
```

**문제점**:

1. **보안**: `"*"` 와일드카드는 모든 턴을 취소한다. turn_id 격리 설계(CR-03)의 의도를 무력화한다.
2. **복잡도**: 매 `check_cancel` 호출마다 Redis GET이 2회 발생한다 (정확 매칭 + 와일드카드 폴백).
3. **과도기적**: 설계 문서에 "프론트엔드가 turn_id를 전달할 수 있게 되면 와일드카드 의존을 제거한다"고 명시되어 있으나, 제거 시점이 불명확하다.

**개선안**:

1. 프론트엔드에서 `turn_id`를 전달하도록 즉시 구현하고, 와일드카드를 deprecation 경고로 전환한다.
2. 와일드카드 폴백에 로그 레벨을 WARNING으로 올려 사용 빈도를 추적한다.
3. 최소한 와일드카드 사용 시 rate limiting을 적용한다.

---

### W-04. `_build_result`의 cancelled 판정이 취약

**파일**: `src/agents/graph/runner.py:464-468`

```python
_cancelled = (
    _status == "cancelled"
    or (hasattr(_status, "value") and _status.value == "cancelled")
)
```

**문제점**:

1. `QueryStatus.CANCELLED`와 직접 비교(`_status == QueryStatus.CANCELLED`)하지 않고, 문자열 "cancelled"와 `.value` 속성을 이중 체크한다.
2. `hasattr`/`.value` 방어 코드는 LangGraph가 state를 dict로 직렬화할 수 있기 때문이지만, 이 패턴이 프로젝트 다른 곳에서는 사용되지 않는 비표준 방식이다.

**개선안**:

```python
_cancelled = (
    _status == QueryStatus.CANCELLED
    or _status == QueryStatus.CANCELLED.value
)
```

또는 LangGraph의 직렬화 동작을 한 곳에서 정규화하는 유틸리티를 만든다.

---

### W-05. `intent_classifier`에 cancel 체크가 2회 존재 -- 불필요한 중복

**파일**: `src/agents/nodes/interpret/intent_classifier.py:124-128, 252-255`

**현상**: 노드 시작부(line 124)에서 cancel을 체크하고, LLM 호출 후 `DATA_ANALYSIS` 분기(line 252)에서 다시 체크한다.

```python
# line 124 -- 시작부
if await check_cancel(state.session_id, state.turn_id):
    return { "status": QueryStatus.CANCELLED, ... }

# ... LLM 호출 ...

# line 252 -- DATA_ANALYSIS 분기 내
if await check_cancel(state.session_id, state.turn_id):
    return { "status": QueryStatus.CANCELLED, ... }
```

**문제점**: `intent_classifier`의 LLM 호출은 단일 호출이므로, 시작부 체크와 호출 후 체크 사이에 cancel이 설정될 수 있는 구간은 LLM 호출 시간뿐이다. 그러나 이것은 모든 노드에 동일하게 적용되는 상황이므로, `intent_classifier`에만 특별히 2회 체크하는 것은 일관성이 없다.

**개선안**: C-01의 래퍼 패턴으로 전환하면 시작부 1회 체크로 통일된다. `DATA_ANALYSIS` 분기 내부의 2차 체크는 제거한다.

---

### W-06. Interpret 계층 라우팅에 CANCELLED 체크 누락 -- cancel 전파 지연

**파일**: `src/agents/graph/pipeline.py:120-158`

**현상**: `_route_after_intent_classifier`와 `_route_after_normalize`가 `QueryStatus.CANCELLED`를 체크하지 않는다. 반면 Reason 계층의 `_route_after_readiness_gate`(line 182), `_route_after_recovery_agent`(line 267)는 CANCELLED를 최우선으로 체크한다.

```python
# _route_after_intent_classifier -- CANCELLED 미체크
def _route_after_intent_classifier(state):
    if state.pending_signals:          # ← pending 체크
        return "clarification_handler"
    if state.status == QueryStatus.ERROR:  # ← ERROR만 체크, CANCELLED 없음
        return "error_end"
    # ... 이하 intent 분기
```

```python
# _route_after_readiness_gate -- CANCELLED 체크 있음 (비교)
def _route_after_readiness_gate(state):
    if state.status == QueryStatus.CANCELLED:  # ← 최우선 체크
        return "conclude_failure"
    # ...
```

**문제점**:

1. **cancel 전파 지연**: intent_classifier에서 cancel이 감지되어 `{"status": CANCELLED}`를 반환해도, 라우팅이 이를 잡지 못하고 `normalize_query` → `reasoning_preparer` → `context_retriever`까지 최대 3개 노드를 불필요하게 통과한다.
2. **일관성 부재**: Reason 계층은 CANCELLED를 라우팅에서 처리하는데, Interpret 계층은 그렇지 않다. 동일 패턴이 계층마다 다르다.
3. **C-01 래퍼 적용 시 증폭**: `with_cancel_check` 래퍼로 전환하면 모든 노드가 동일한 cancel dict를 반환하므로, 라우팅에서 잡지 못하면 후속 노드가 무의미하게 Redis를 다시 조회하게 된다.

**개선안**:

```python
def _route_after_intent_classifier(state):
    if state.pending_signals:
        return "clarification_handler"
    if state.status == QueryStatus.CANCELLED:  # 추가
        return "error_end"
    if state.status == QueryStatus.ERROR:
        return "error_end"
    # ...

def _route_after_normalize(state):
    if state.status == QueryStatus.CANCELLED:  # 추가
        return "error_end"
    if state.pending_signals:
        return "clarification_handler"
    return "reasoning_preparer"
```

`normalize_query`의 `add_conditional_edges` 맵에 `"error_end": "error_end"` 추가 필요.

---

## Info

### I-01. 설계 대비 구현 범위 초과 -- 설계 문서에 없는 노드에도 cancel 체크 추가됨

**현상**: 설계 문서(`docs/todo/20260404-pipeline-cancel-design.md` section 5.4)에서 명시한 cancel 체크 대상은 다음 7곳이다.

| 체크 위치 | 방식 | 설계 문서 명시 |
|-----------|------|---------------|
| `context_retriever` 시작부 | B | O |
| `readiness_gate` 시작부 | B | O |
| `sql_generator` 시작부 | B | O |
| `execute_sql` 시작부 | B | O |
| `_route_after_readiness_gate` | A | O |
| `_route_after_recovery_agent` | A | O |
| `_route_after_result_finalizer` | A | O |

그러나 실제 구현에는 설계 문서에 없는 다음 노드에도 cancel 체크가 추가되어 있다.

| 추가된 노드 | 설계 미명시 |
|-------------|-----------|
| `intent_classifier` (2회) | O |
| `query_normalizer` | O |
| `context_interpreter` (2회) | O |
| `sql_validator` (2회) | O |
| `recovery_agent` | O |
| `analyzer` | O |
| `formatter` | O |

**평가**: 방어적 프로그래밍 관점에서는 양호하지만, 설계 문서와 구현 사이에 괴리가 있다. 이것이 "난개발" 인상을 주는 원인 중 하나이다. 만약 모든 노드에 cancel 체크가 필요하다면 설계 문서를 갱신하고, C-01의 래퍼 패턴으로 체계화해야 한다.

---

### I-02. mid-node cancel 체크의 가치 대비 복잡도 분석

**파일**: `sql_validator.py:112`, `context_interpreter.py:391`, `intent_classifier.py:252`

설계 문서는 "노드 경계에서만 cancel 체크"를 원칙으로 하지만, 위 3곳은 노드 내부에서 추가 체크한다.

| 위치 | 의미 | 최대 절감 시간 |
|------|------|---------------|
| `sql_validator` Layer2b 전 | LLM 호출 1회 방지 | ~15초 |
| `context_interpreter` Level1 루프 | 스텝별 LLM 호출 방지 | 스텝당 ~15초, 최대 3-5스텝 |
| `intent_classifier` DATA_ANALYSIS 분기 | analysis_query 전처리 방지 | ~1초 (LLM 미호출) |

**평가**: `context_interpreter`의 루프 내 체크는 효과가 크다 (최대 75초 절감). `sql_validator`도 합리적이다. `intent_classifier`의 2차 체크는 효과 미미하며 제거 권장.

---

### I-03. 테스트 커버리지 양호 -- 단 통합 테스트 보완 필요

**파일**: `tests/auto/unit/test_cancel.py` (431줄, 8개 테스트 클래스)

단위 테스트 커버리지가 양호하다. `MemoryCancelStore`, `check_cancel`, `make_cancel_updates`, `pop_cancel`, `clear_cancel`, 라우팅 CANCELLED 경로, `result_finalizer` CANCELLED 분기, REST 엔드포인트, `_build_result` cancelled 플래그 모두 테스트되어 있다.

**보완 필요 사항**:
1. `RedisCancelStore`의 Lua 스크립트 fallback 경로 테스트 (`tests/auto/unit/test_redis_cancel_store.py` 존재 확인)
2. cancel 후 interrupt resume이 새 턴으로 전환되는 통합 테스트
3. cancel + 동시 새 질의 경쟁 상태 테스트

---

## 종합 아키텍처 평가

### 잘 된 점

1. **LangGraph 버그 회피 전략**: `asyncio.Task.cancel()` 대신 앱 레벨 플래그 방식은 현 시점 최선의 선택이다.
2. **turn_id 기반 격리**: 이전 턴 cancel이 새 턴에 영향을 주지 않도록 하는 설계는 경쟁 상태 방어에 효과적이다.
3. **Protocol + 구현 분리**: `CancelStore` Protocol + `Memory`/`Redis` 구현체 패턴은 깔끔하다.
4. **pop_cancel 원자적 연산**: interrupt 대기 중 cancel 감지를 위한 `pop_cancel`은 우아한 해결책이다.
5. **테스트 커버리지**: 단위 테스트가 핵심 경로를 잘 커버하고 있다.

### 핵심 개선 포인트 (우선순위 순)

| 순위 | 항목 | 효과 | 난이도 |
|------|------|------|--------|
| 1 | C-01: `with_cancel_check` 래퍼로 중앙화 | 14회 중복 제거, 노드 코드 간소화 | 중 |
| 2 | W-01: cancel 응답 dict 통일 | C-01과 동시 해소 | C-01에 포함 |
| 3 | W-02: 타입 힌트 추가 | mypy 적합성 | 하 |
| 4 | W-03: 와일드카드 deprecation 계획 수립 | 보안 강화 | 중 |
| 5 | W-06: Interpret 라우팅 CANCELLED 체크 추가 | cancel 전파 지연 해소, 불필요한 Redis 조회 방지 | 하 |
| 6 | W-05: intent_classifier 2차 체크 제거 | 코드 간소화 | 하 |

### "난개발" 진단 결론

cancel 기능의 **설계 품질은 높다**. 문제는 구현 과정에서 "방어적으로 모든 노드에 cancel 체크를 넣자"는 접근이 체계 없이 적용되어, 설계 문서에 명시된 7곳 외에 7곳이 추가되고, 응답 패턴도 3가지로 분화된 것이다.

C-01의 래퍼 패턴을 적용하면:
- 12개 노드 파일에서 cancel 관련 코드 완전 제거
- `pipeline.py`의 `add_node` 선언부에서 cancel 대상 노드를 한눈에 파악 가능
- 응답 패턴 자동 통일
- 새 노드 추가 시 cancel 체크 누락 불가능 (래핑 여부로 명시)
- mid-node 체크가 필요한 `sql_validator`와 `context_interpreter`만 예외로 노드 내부에 유지

이것만으로 "난개발" 인상의 ~80%가 해소될 것으로 판단한다.

---

## 리팩토링 실행 체크리스트

C-01 래퍼 패턴 적용 시 변경 파일 목록:

- [ ] `src/agents/graph/cancel.py` -- `with_cancel_check` 래퍼 함수 추가, `_CANCEL_SIMPLE_RESPONSE` 상수 추가
- [ ] `src/agents/graph/pipeline.py` -- `add_node` 호출부에 `with_cancel_check` 적용
- [ ] `src/agents/nodes/interpret/intent_classifier.py` -- cancel 체크 2건 제거
- [ ] `src/agents/nodes/interpret/query_normalizer.py` -- cancel 체크 1건 제거
- [ ] `src/agents/nodes/reason/context_retriever.py` -- cancel 체크 1건 제거
- [ ] `src/agents/nodes/reason/context_interpreter.py` -- 시작부 cancel 체크 1건 제거 (루프 내 1건 유지)
- [ ] `src/agents/nodes/reason/readiness_gate.py` -- cancel 체크 1건 제거
- [ ] `src/agents/nodes/reason/sql_generator.py` -- cancel 체크 1건 제거
- [ ] `src/agents/nodes/reason/sql_validator.py` -- 시작부 cancel 체크 1건 제거 (Layer2b 전 1건 유지)
- [ ] `src/agents/nodes/reason/recovery_agent.py` -- cancel 체크 1건 제거
- [ ] `src/agents/nodes/present/sql_executor.py` -- cancel 체크 1건 제거
- [ ] `src/agents/nodes/present/analyzer.py` -- cancel 체크 1건 제거 (`_is_cancelled` 콜백은 유지)
- [ ] `src/agents/nodes/present/formatter.py` -- cancel 체크 1건 제거
- [ ] `tests/auto/unit/test_cancel.py` -- `with_cancel_check` 래퍼 단위 테스트 추가
- [ ] `docs/todo/20260404-pipeline-cancel-design.md` -- 설계 문서 갱신 (래퍼 패턴 반영)

**변경 파일**: 15개
**제거 코드**: ~60줄 (cancel 보일러플레이트)
**추가 코드**: ~30줄 (`with_cancel_check` + 상수)
**순감소**: ~30줄
