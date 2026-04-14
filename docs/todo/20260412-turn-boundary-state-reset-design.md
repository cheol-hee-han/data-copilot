# 턴 경계 상태 리셋 설계 (Turn Boundary State Reset)

- 작성일: 2026-04-12
- 상태: 설계 확정, 구현 미착수
- 우선순위: 높음 (사용자 테스트 재현 버그 + 잠복 버그 2건)
- 관련 파일:
  - [src/agents/state/state.py](../../src/agents/state/state.py)
  - [src/agents/graph/pipeline.py](../../src/agents/graph/pipeline.py)
  - [src/agents/graph/runner.py](../../src/agents/graph/runner.py)
  - [src/models/trace.py](../../src/models/trace.py)
  - [src/services/process_summary_builder.py](../../src/services/process_summary_builder.py)

---

## 1. 증상 (보고된 버그)

사용자 테스트에서 두 번째 질의가 파이프라인 실패(TERM_UNRESOLVABLE,
확신도 부족)로 종료되었음에도, **직전 턴의 성공 응답 마크다운 테이블
(고객등급별 고객수/비율)이 실패 메시지와 함께 그대로 다시 렌더링**됨.
실패한 현재 턴과는 무관한 이전 턴 데이터가 노출되는 심각한 UX/데이터
신뢰성 이슈.

---

## 2. 근본원인

### 2.1 LangGraph Checkpointer의 부분 머지 동작

[runner.py:149-153, 299-311](../../src/agents/graph/runner.py#L149-L311) 에서
`thread_id=session_id`로 checkpointer를 사용한다. 이 상태에서
`app.ainvoke(initial_state, ...)`는 **이전 턴의 체크포인트 상태 위에
`initial_state`를 부분 머지**한다. `initial_state`가 명시하는 필드
(`user_input / original_query / preprocessed_input / session_id /
conversation_history / turn_id`) 외의 모든 필드는 **이전 턴 값이 그대로
유지**된다.

이는 LangGraph의 의도된 동작이다. 멀티턴 채팅에서 `messages`를 누적하는
용도로 설계된 것. 문제는 본 프로젝트 `PipelineState`에 "누적해야 할
필드(세션 지속)"와 "턴마다 버려야 할 필드(턴 스코프)"가 혼재되어 있는데
턴 경계 리셋 장치가 없다는 점.

### 2.2 실패 경로가 Present 계층 산출물을 비우지 않음

[pipeline.py:371-403](../../src/agents/graph/pipeline.py#L371-L403)
`_handle_error`는 `formatted_response`와 `status`만 반환한다.
`result_data`/`process_summary`를 `None`으로 덮어쓰지 않는다.
[result_finalizer.py:96-106](../../src/agents/nodes/reason/result_finalizer.py#L96-L106)
실패 경로도 `error_message`만 설정한다.

결과적으로 실패 턴에서는 **이전 성공 턴의 `result_data`(고객등급 테이블)가
state에 그대로 살아남음**.

### 2.3 발현 경로

```
Turn 1 (성공) → state.result_data = 고객등급 테이블, checkpoint 저장
Turn 2 (실패)
  → ainvoke(initial_state) → checkpointer 머지 → result_data 유지
  → 실패 라우팅 → _handle_error → result_data 미정리
  → _build_result → PipelineResult.result_data = 이전 테이블
  → main.py:541-546 → end_msg["result_data"] 포함 전송
  → embedded.html:2117 → msg.resultData 할당
  → renderResultTable → 이전 테이블 렌더링
```

---

## 3. 잠복 버그 (같은 원인의 2차 피해)

본 수정 과정에서 같은 구조적 원인으로 인한 추가 문제 2건을 발견했다.

### 3.1 `trace_log` 턴 간 오염

[models/trace.py:38-49](../../src/models/trace.py#L38-L49) `add_trace`:
```python
def add_trace(state, node, action, detail=""):
    entry = TraceEntry(node=node, action=action, detail=detail)
    return [*state.trace_log, entry]
```

**append 시맨틱**이다. 턴 N+1의 첫 노드가 `add_trace`를 호출하면
`[*state.trace_log, new]`가 되는데, 턴 경계에서 `trace_log`가 리셋되지
않으므로 **이전 턴의 모든 trace 엔트리가 새 턴의 trace 앞에 얹혀진다**.

**영향**:
- `save_turn`([runner.py:421-423](../../src/agents/graph/runner.py#L421-L423))이
  매 턴 trace_log를 DB로 직렬화할 때 이전 턴 엔트리가 **중복 저장**
- 프론트엔드의 턴별 trace 표시가 혼탁
- 세션이 길어질수록 trace_log 크기가 선형 증가 (slow leak)

이 버그는 현재 사용자에게 직접 노출되지 않지만 DB/로깅에 잠복 중.

### 3.2 `resolved_signals` 턴 간 누적 (설계상 의도 모호)

[state.py:753-755](../../src/agents/state/state.py#L753-L755):
```python
resolved_signals: Annotated[
    list[AmbiguitySignal], operator.add,
] = Field(default_factory=list)
```

`operator.add` reducer가 걸려 있어 **빈 리스트를 반환해도 비울 수 없다**.
어떤 코드가 `{"resolved_signals": []}`을 반환해도 LangGraph는 기존값에
`[]`를 append한다.

**현재 소비 패턴**:
[process_summary_builder.py:178-184](../../src/services/process_summary_builder.py#L178-L184)
```python
tid = state.turn_id
if tid:
    for s in state.resolved_signals:
        if s.decision == "INFER" and s.turn_id == tid:
            ...
```
→ 소비 지점에서 `turn_id`로 필터링하여 현재 턴 시그널만 추출.
이 필터링이 필요하다는 것 자체가 **"누적되고 있다"는 사실의 증거**.

**영향**:
- 같은 세션 내에서 턴이 거듭될수록 `resolved_signals` 리스트가 선형 증가
- 체크포인트 크기 slow leak
- NEW 분류 질의에서도 이전 턴의 clarification 시그널이 그대로 남음
  (사용자가 **"NEW 대화가 되면 지워져야 하는 게 맞는데"**로 지적한 부분 —
  정확한 지적)
- 현재 기능 버그가 안 터지는 이유는 `turn_id` 필터링의 우회 덕분.
  설계상의 정합성은 아님.

**src/ 전수 검색 결과 `resolved_signals`를 비우는 코드 전무.**

---

## 4. 설계 결정 — Entry 리셋 노드 패턴

### 4.1 검토한 대안 비교

| 방식 | 장점 | 단점 |
|---|---|---|
| A. State 스키마 분리 (Session vs Turn) | 구조적으로 오염 불가능, 타입 시스템이 경계 강제 | 대규모 리팩토링 (필드 40+개 재분류, 모든 노드 시그니처 변경). 투입 대비 이득 애매 |
| **B. Entry 노드(`turn_reset`)에서 일괄 리셋** | LangGraph 커뮤니티 표준 패턴, 그래프 안에 턴 경계가 1급 개념으로 명시, 진입점 독립적, 유지보수 쉬움 | 노드 1개 추가 |
| C. runner `initial_state`에서 명시 리셋 | 수정 최소 | 턴 경계 지식이 그래프 바깥에 존재, 다른 진입점(테스트/CLI)에서 리셋 누락 가능, State 스키마 변경 시 runner와 암묵 결합 |
| D. 각 실패 노드별 개별 리셋 | 패치 로컬 | 신규 실패 노드마다 반복 필요, 누락 시 재발 |

**채택: B (Entry 리셋 노드)** — LangGraph 공식 문서/커뮤니티의
"multi-turn + ephemeral per-turn scratchpad" 표준 패턴. 이 프로젝트
규모에서 근본성과 실용성의 균형이 최적.

### 4.2 interrupt 재개 경로 안전성

명확화 대기 → 사용자 응답 시 `Command(resume=...)`는 LangGraph resume
semantics에 의해 **중단된 노드부터 재개**한다. `turn_reset`은 START 직후
이미 완료된 노드이므로 재개 경로에서 재실행되지 않음. 별도 분기 로직
불필요.

### 4.3 CONTINUE 경로 안전성 (정밀 감사 결과)

리셋 범위 확정 전 CONTINUE 경로 전수 감사를 수행했다. 결론: **18개
필드 전부 리셋 안전**. 근거는 다음과 같다.

**(가) conversation_history는 텍스트만 담김**

[turn_text_store.py:144-163](../../src/services/turn_text_store.py#L144-L163)
`get_conversation_history`는 `[{"role", "content"}]` 형태만 반환한다.
이전 턴의 SQL/결과/시각화는 DB의 `metadata` 컬럼([runner.py:420-456](../../src/agents/graph/runner.py#L420-L456))
에 저장되지만 이 API는 꺼내지 않는다. 즉 **CONTINUE 처리 LLM은 이전 턴
SQL/데이터에 접근 경로 자체가 없다**. 원래부터 없었으므로 리셋 도입이
CONTINUE 품질을 악화시키지 않는다.

**(나) 하류 노드 전수 점검 — 이전 턴 state 필드 의존성 전무**

- [intent_classifier.py:248-263](../../src/agents/nodes/interpret/intent_classifier.py#L248-L263):
  CONTINUE 판정 시 `continue_context`/`preprocessed_input` 덮어쓰기만
  수행. 이전 턴의 `reason.*`/`sql_result`/`analysis_result` 참조 없음.
  LLM 입력은 `conversation_history`만.
- [reasoning_preparer.py:48-144](../../src/agents/nodes/reason/reasoning_preparer.py#L48-L144):
  `is_continuation` 플래그를 **전혀 읽지 않는다**. 매 턴 Hypothesis/
  knowledge_items/execution_plan을 새로 생성. recovery 상태도 항상 초기화.
- [sql_generator.py:240-442](../../src/agents/nodes/reason/sql_generator.py#L240-L442):
  `state.reason.*`(explored_tables/codes/validated_sql 등)를 읽지만
  모두 **현재 턴 내에서 누적된 값**이다. `clarification_context`는
  [clarification_context.py:33-91](../../src/agents/utils/clarification_context.py#L33-L91)
  에서 `turn_id` 필터링으로 현재 턴 시그널만 추출.
- [context_retriever.py:87-119](../../src/agents/nodes/reason/context_retriever.py#L87-L119):
  `executed_tool_keys`/`explored_tables`는 현재 턴 내 중복 방지용.
  이전 턴 캐시 아님.
- [formatter.py:82-102](../../src/agents/nodes/present/formatter.py#L82-L102),
  [analyzer.py:72-102](../../src/agents/nodes/present/analyzer.py#L72-L102):
  `state.sql_result`/`state.reason.*` 모두 현재 턴 값만 참조. 이전 턴
  비교 로직 없음.

**(다) `is_continuation` 플래그는 사실상 미사용**

intent_classifier가 설정만 할 뿐 하류에서 이 플래그를 분기 조건으로
사용하는 코드가 없음. 리셋해도 기능 손실 없음.

**(라) 부수적 발견 — CONTINUE 품질의 별도 이슈 (§7.3 참고)**

현재 CONTINUE 처리 LLM이 이전 턴 SQL/결과를 참조할 수 없는 것은
**리셋과 무관한 별도 설계 이슈**다. "방금 쿼리에 WHERE만 추가" 같은
정교한 연속 요청의 품질이 낮을 가능성이 있으나, 본 수정(리셋 도입)은
이 품질을 악화시키지 않고 현 수준을 그대로 유지한다. 개선 아이디어는
§7.3에서 별도 과제로 다룬다.

---

## 5. 필드 분류 (전수 감사 결과)

### 5.1 세션 지속 — 리셋 금지 (6개)

| 필드 | 유지 이유 |
|---|---|
| `session_id` | 세션 식별 |
| `conversation_history` | 멀티턴 문맥 (runner가 매 턴 fresh 주입) |
| `user_input` | runner 매 턴 주입 |
| `original_query` | runner 매 턴 주입 |
| `preprocessed_input` | runner 매 턴 주입 |
| `turn_id` | runner 매 턴 uuid4 신규 발급 |

→ runner의 `initial_state`가 이미 담당. turn_reset 노드는 건드리지 않음.

### 5.2 턴 스코프 — 리셋 대상 (18개)

| # | 필드 | 기본값 | 검증 근거 |
|---|---|---|---|
| 1 | `analysis_query` | `""` | analyzer 턴 내 소비 |
| 2 | `intent` | `IntentType.UNKNOWN` | 라우팅 턴 내 소비 |
| 3 | `intent_confidence` | `0.0` | 로깅 턴 내 소비 |
| 4 | `query_category` | `""` | 로깅 턴 내 소비 |
| 5 | `is_continuation` | `False` | intent_classifier 매 턴 재계산 |
| 6 | `continue_context` | `""` | preprocessed_input에 병합 후 소비 |
| 7 | `normalized_query` | `None` | reason 계층 시드 |
| 8 | `pending_signals` | `[]` | clarification_handler 소비 후 비움 |
| 9 | `reason` | `ReasoningState()` | 내부 25개 서브필드 모두 턴 스코프 확인 |
| 10 | `sql_result` | `SQLResult()` | PipelineResult.sql_result로 복사 후 소비 |
| 11 | `analysis_result` | `AnalysisResult()` | formatted_response에 녹음 |
| 12 | `visualization` | `VisualizationData()` | PipelineResult.visualization로 복사 |
| 13 | `formatted_response` | `""` | PipelineResult.response + save_turn DB |
| 14 | `result_data` | `None` | stream.end 전송 + save_turn |
| 15 | `process_summary` | `None` | stream.end 전송 + save_turn |
| 16 | `status` | `QueryStatus.PENDING` | 턴 종료 시 의미 소진 |
| 17 | `error_message` | `""` | formatted_response에 녹음 |
| 18 | `trace_log` | `[]` | **필수 리셋**: append 시맨틱 + save_turn DB 영속화 |

### 5.3 `reason` 내부 서브필드 검증 (~25개)

모두 `reason = ReasoningState()` 통째 교체로 리셋됨. 주요 검증:
- `validated_sql`/`generated_sql`/`explored_*`: formatter가 같은 턴 내 소비 ✅
- `loop_guard`: 루프 제어 — 리셋 필수 (미리셋 시 다음 턴이 MAX_GENERATES 조기 도달) ✅
- `dead_ends`/`fix_history`: 재시도 루프 내 누적, 턴 내 리셋 맞음 ✅
- `target_db`/`target_db_decision`: 설계상 매 턴 readiness_gate 재결정 ✅

### 5.4 특수 — `resolved_signals` (이번 범위 외, 별도 이슈)

`operator.add` reducer 제약으로 turn_reset에서 `{"resolved_signals": []}`
반환해도 비울 수 없음. **본 수정 범위에서 제외**하고 §7 후속 과제로
트래킹.

---

## 6. 구현 계획

### 6.1 [state.py](../../src/agents/state/state.py) — 단일 진실 공급원

`PipelineState`에 클래스 메서드 추가. 리셋 필드 목록을 State 정의 바로
옆에 두어 향후 필드 추가 시 리셋 여부 판단을 강제.

```python
@classmethod
def turn_reset_updates(cls) -> dict[str, Any]:
    """턴 경계에서 이전 턴 산출물을 초기화하기 위한 updates dict.

    세션 지속 필드(session_id/conversation_history/user_input/
    original_query/preprocessed_input/turn_id)는 포함하지 않는다.
    operator.add reducer가 걸린 resolved_signals는 이 경로로
    리셋할 수 없어 제외한다(§7 별도 이슈).
    """
    return {
        "analysis_query": "",
        "intent": IntentType.UNKNOWN,
        "intent_confidence": 0.0,
        "query_category": "",
        "is_continuation": False,
        "continue_context": "",
        "normalized_query": None,
        "pending_signals": [],
        "reason": ReasoningState(),
        "sql_result": SQLResult(),
        "analysis_result": AnalysisResult(),
        "visualization": VisualizationData(),
        "formatted_response": "",
        "result_data": None,
        "process_summary": None,
        "status": QueryStatus.PENDING,
        "error_message": "",
        "trace_log": [],
    }
```

### 6.2 [pipeline.py](../../src/agents/graph/pipeline.py) — turn_reset 노드

```python
def _turn_reset(state: PipelineState) -> dict:
    """턴 진입점 — 이전 턴의 작업 산출물 일괄 초기화.

    Checkpointer가 같은 thread_id에서 상태를 유지하므로,
    새 턴 시작 시 세션 지속 필드를 제외한 턴 스코프 필드를
    명시적으로 초기값으로 덮어쓴다. interrupt 재개 경로는
    LangGraph resume semantics에 의해 이 노드를 타지 않는다.
    """
    return PipelineState.turn_reset_updates()
```

그래프 빌더 수정:
- `workflow.add_node("turn_reset", _turn_reset)` 추가
- `workflow.set_entry_point("intent_classifier")` →
  `workflow.set_entry_point("turn_reset")`
- `workflow.add_edge("turn_reset", "intent_classifier")` 추가

### 6.3 테스트

- **단위**: `turn_reset` 노드 단독 호출 → 리턴 dict 키셋이 세션 지속
  6개 필드를 포함하지 않는지 assert
- **통합 (신규 골든셋)**:
  1. Turn 1 성공 → `result_data` 존재 확인
  2. Turn 2 실패 (TERM_UNRESOLVABLE 유도) → `stream.end`에 `result_data`
     키 **부재** 확인
  3. Turn 2 `trace_log`가 Turn 1 엔트리 미포함 확인 (§3.1 회귀 방지)
- **회귀**: 멀티턴 CONTINUE 골든셋 전체 재실행 → 통과 확인
- **수동**: interrupt 재개 (명확화 대기 → 사용자 응답) 시 이전 컨텍스트
  유지 확인

### 6.4 범위 외 (건드리지 않음)

- `_handle_error` / `result_finalizer` 실패 경로의 개별 리셋 코드 —
  turn_reset이 근본 차단하므로 불필요. 기존 코드 그대로 둠.

---

## 7. 후속 과제

### 7.1 `resolved_signals` reducer 제거 + 턴 경계 리셋 (구현 설계 확정)

#### 7.1.1 문제 재정리

##### 증상 (§3.2)

- `operator.add` reducer로 세션 내 무한 누적
- NEW 분류 질의에서도 이전 턴 시그널 잔존 (사용자 지적)
- 체크포인트 크기 slow leak (turn 20회 세션 기준 ~40~60개 누적)

##### 잠복 버그 2건 (필터 누락) — 본 수정으로 자동 해소

- [intent_classifier.py:62-74](../../src/agents/nodes/interpret/intent_classifier.py#L62-L74)
  `_build_clarification_history`: `turn_id` 필터 **없음**. 이전 턴의
  `intent_classifier` 시그널이 CONTINUE 판정 프롬프트에 누출되어 LLM
  입력 오염 가능.
- [intent_classifier.py:156-159](../../src/agents/nodes/interpret/intent_classifier.py#L156-L159)
  `ask_count` 집계: `turn_id` 필터 **없음**. 세션 내 누적 ASK 수로
  `clarification_max_turns` 판정 → 2턴째부터 조기 강제진행 가능.

#### 7.1.2 근본원인 — reducer 도입 전제의 뒤집힘

`operator.add` 도입 당시 의도는 "한 턴 내 여러 노드가 시그널을 순차
append할 때 각 노드가 전체 재구성 책임을 지지 않아도 되게 하기"였다.
LangGraph 공식 예제의 `messages` 누적 패턴에서 차용.

하지만 체크포인터(`thread_id=session_id`) 도입 후 **누적이 턴 경계를
넘어서** 일어나게 되었고, §6의 turn_reset 도입으로 **"턴 경계 리셋 가능성"
이 최상위 제약**이 되었다. reducer가 걸린 필드는 `{"xxx": []}` 반환으로
비울 수 없으므로 turn_reset 메커니즘의 혜택을 받지 못한다.

소비 지점에서 `turn_id == state.turn_id` 필터로 사후 우회했으나 필수
규칙이 암묵적이라 18곳 중 2곳이 누락(잠복 버그). reducer를 제거하면
매 턴 fresh이므로 **필터 규칙 자체가 사라져** 이 클래스의 버그가
원천 차단된다.

**병렬 쓰기 안전성**: 본 그래프는 순차 실행이며 병렬 노드 동시 쓰기가
없음을 확인. reducer가 제공하는 유일한 본질적 가치(병렬 머지)는 현
설계에 해당사항 없음. 따라서 제거의 비용이 0에 수렴.

#### 7.1.3 설계 결정

**채택: reducer 제거 + 호출 측 명시 누적 (읽어서 append)**.

[trace_log](../../src/models/trace.py#L38-L49)의 `add_trace` 패턴과
**동일**하여 프로젝트 내부 일관성 확보:

```python
return [*state.trace_log, entry]                   # 기존 trace_log
return [*state.resolved_signals, new_signal]       # 신규 resolved_signals
```

##### 검토 대안 비교

| 방안 | 장점 | 단점 | 판정 |
| --- | --- | --- | --- |
| **A. reducer 제거 + 호출측 누적** | turn_reset 즉시 작동, 소비자 필터 불필요, `trace_log`와 일관 | 쓰기 지점 6곳 한 줄 수정 | **채택** |
| B. 커스텀 reducer (빈 리스트 sentinel) | 쓰기 지점 무변경 | 의미 비명시, 빈 리스트의 특별 취급을 매번 암기 | 기각 |
| C. 턴별 dict 구조 (turn_id → list) | 턴별 격리 강제 | 현 소비 API 전면 변경, 과설계 | 기각 |

#### 7.1.4 구현 변경 사항

##### (1) [state.py:753-755](../../src/agents/state/state.py#L753-L755) — 필드 정의

```python
# 변경 전
resolved_signals: Annotated[
    list[AmbiguitySignal], operator.add,
] = Field(default_factory=list)

# 변경 후
# W: interpret/reason 노드 여러 곳  R: clarification_context/routing/UI
# 호출 측 누적 패턴 — [*state.resolved_signals, new]. turn_reset이 자연스럽게 비운다.
resolved_signals: list[AmbiguitySignal] = Field(default_factory=list)
```

**임포트 정리**: `operator.add`가 `resolved_signals` 전용으로 남으면 `import operator` 제거 검토. 다른 사용처가 없으면 함께 제거.

##### (2) `turn_reset_updates()` — 리셋 대상 추가

[state.py:818-841](../../src/agents/state/state.py#L818-L841)에 1개 필드
추가:

```python
return {
    ...
    "resolved_signals": [],   # ← 신규 추가
    "trace_log": [],
}
```

docstring의 "제외 — resolved_signals..." 문단 제거, "포함 19개 필드"로 갱신.

##### (3) 쓰기 지점 6곳 — `state.resolved_signals`를 읽어 append

| 파일 | 라인 | 변경 전 | 변경 후 |
| --- | --- | --- | --- |
| [intent_classifier.py](../../src/agents/nodes/interpret/intent_classifier.py#L185) | 185 | `"resolved_signals": [forced_signal]` | `"resolved_signals": [*state.resolved_signals, forced_signal]` |
| [intent_classifier.py](../../src/agents/nodes/interpret/intent_classifier.py#L265) | 265 | `updates["resolved_signals"] = [AmbiguitySignal(...)]` | `updates["resolved_signals"] = [*state.resolved_signals, AmbiguitySignal(...)]` |
| [clarification_handler.py](../../src/agents/nodes/interpret/clarification_handler.py#L162) | 162 | `"resolved_signals": infer` | `"resolved_signals": [*state.resolved_signals, *infer]` |
| [clarification_handler.py](../../src/agents/nodes/interpret/clarification_handler.py#L194) | 194 | `"resolved_signals": infer + [best]` | `"resolved_signals": [*state.resolved_signals, *infer, best]` |
| [query_normalizer.py](../../src/agents/nodes/interpret/query_normalizer.py#L172) | 172 | `result["resolved_signals"] = signals` | `result["resolved_signals"] = [*state.resolved_signals, *signals]` |
| [result_finalizer.py](../../src/agents/nodes/reason/result_finalizer.py#L92) | 92 | `updates["resolved_signals"] = assumption_signals` | `updates["resolved_signals"] = [*state.resolved_signals, *assumption_signals]` |

**주의**: 한 노드 내에서 resolved_signals 갱신이 **2회 이상** 나타나는
경우가 없음을 확인(한 노드 return 1회 규칙). 같은 노드 내 2회 호출은
read-then-write 특성상 두 번째가 첫 번째를 놓치게 되므로 금지. 이는
`add_trace`와 동일한 제약.

##### (4) 소비 지점 — 필터 제거 (선택적 단순화)

reducer 제거 후에는 매 턴 `resolved_signals`가 `[]`에서 시작하므로
`turn_id == state.turn_id` 필터는 **불필요**. 다만 **안전 측면에서
즉시 제거하지 않는 것이 권장됨** — 본 수정의 핵심은 reducer 제거이며,
필터 제거는 별도 클린업 스텝으로 분리하면 회귀 리스크 감소.

**Phase 1 (본 수정)**: 필터 유지 (방어 유지, 동작 불변).
**Phase 2 (후속 클린업, 선택)**: 필터 제거 + docstring 갱신.

Phase 2 대상 소비 지점 (참고용):
- [clarification_context.py:51-73](../../src/agents/utils/clarification_context.py#L51-L73)
  `asks`/`infers` 리스트 컴프리헨션의 `turn_id` 조건 삭제
- [pipeline.py:355-358](../../src/agents/graph/pipeline.py#L355-L358)
  `_route_after_clarify`의 `current_signals` 필터 삭제
- [process_summary_builder.py:178-194](../../src/services/process_summary_builder.py#L178-L194)
  `tid`/`turn_id` 필터 삭제 (docstring의 "operator.add 리듀서로 누적되므로" 문장 제거)
- [intent_classifier.py:62-74](../../src/agents/nodes/interpret/intent_classifier.py#L62-L74)
  `_build_clarification_history` — **잠복 버그 자동 해소**, 필터 추가 불필요
- [intent_classifier.py:156-159](../../src/agents/nodes/interpret/intent_classifier.py#L156-L159)
  `ask_count` — **잠복 버그 자동 해소**, 필터 추가 불필요

##### (5) `AmbiguitySignal.turn_id` 필드의 필요성 재검토

reducer 제거 후 매 턴 fresh이므로 **시그널에 `turn_id`를 저장할 필연적
이유가 없다**. 그러나:

- save_turn DB metadata 저장 시 감사 추적용으로 유용
- Phase 2 클린업 전까지 소비자 필터가 참조
- 제거 시 다운스트림 영향 범위 추가 조사 필요

→ **Phase 1에서는 필드 유지**. Phase 2 이후 사용처가 0건이 되면 제거
검토. 본 수정 범위 외.

#### 7.1.5 테스트

##### 단위

1. `turn_reset_updates()` 리턴 키셋이 19개, `resolved_signals` 포함 확인
2. 각 쓰기 지점 단위 테스트에서 `state.resolved_signals`가 비어있을 때 / 1개 있을 때 각각 결과 리스트 길이 검증

##### 통합 (신규 골든셋)

1. Turn 1에서 query_normalizer가 INFER 시그널 2개 생성
2. Turn 2에서 normalizer가 INFER 시그널 1개 생성 → Turn 2 종료 시 `state.resolved_signals` 길이 = **1** (이전 턴 2개가 섞이지 않음)
3. 2턴 연속 명확화 시나리오: Turn 1에 ASK 1회 → Turn 2에서 `ask_count`가 0으로 시작하는지 확인 (조기 강제진행 회귀 방지)

##### 회귀

- 멀티턴 CONTINUE 골든셋 재실행
- 명확화 interrupt/resume 수동 테스트 — Turn 1 interrupt → resume 시 같은 턴 시그널 모두 보존되는지 확인(resume은 turn_reset 경유하지 않으므로 영향 없어야 함)

#### 7.1.6 영향 범위

- **변경 파일**: 6개 (state.py, intent_classifier.py, clarification_handler.py, query_normalizer.py, result_finalizer.py, state.py의 turn_reset_updates 함께 갱신)
- **변경 라인 수**: 8~10줄
- **호환성**: 그래프 구조 불변, 노드 시그니처 불변, 프롬프트 불변. 체크포인터 저장 포맷 불변 (필드 타입만 `Annotated` 제거)
- **성능**: 호출 측 `[*state.resolved_signals, ...]`는 O(n) 복사, 세션당 시그널 수가 작아(<50) 무시 가능
- **관측성**: 기존 `logger.debug("resolved_signals 필터", total=len(...))` 출력의 `total` 값이 "현재 턴 누적"을 의미하게 바뀜 (이전에는 "세션 누적"). 로그 해석 문서 주의.

#### 7.1.7 롤백 전략

- 문제 발견 시 필드 정의만 `Annotated[list, operator.add]`로 되돌리고
  쓰기 지점은 그대로 둠. 결과적으로 `old + [*old, new] = old + old + [new]`
  형태로 이중 누적이 발생하므로 **롤백 시 쓰기 지점도 함께 되돌려야 함**.
- 안전을 위해 본 수정은 **단일 커밋**으로 처리하여 `git revert` 한 번에
  전체 원복 가능하도록 한다.

#### 7.1.8 선행 조건 및 순서

- **선행**: §6 turn_reset 구현 완료 (이미 완료됨 ✅)
- **본 수정 (§7.1 Phase 1)**: reducer 제거 + 쓰기 지점 6곳 변경 + turn_reset_updates 갱신. 필터는 유지.
- **후속 (§7.1 Phase 2, 선택)**: 소비 지점 3곳 필터 제거 + docstring 정리. 회귀 테스트 통과 후 별도 커밋.

### 7.2 `add_trace`도 reducer 방식으로 이전하는 것 검토

현재 `add_trace`는 호출 측에서 `[*state.trace_log, entry]`를 만들어
반환한다. 매 노드가 반복. `trace_log`에 `operator.add` reducer를
걸면 노드는 `[entry]` 하나만 반환하면 됨. 다만 이 경우 §7.1과 같은
턴 경계 누적 문제가 발생하므로, §7.1 해법(reducer 제거 + 호출 측
누적)과 일관된 방향으로 유지하는 것이 나을 수 있음. 추가 검토 필요.

### 7.3 CONTINUE 턴의 이전 SQL/컨텍스트 참조 가능 (별도 품질 개선 이슈)

**배경**: §4.3 (라)에서 기술한 대로, 현재 CONTINUE 처리 경로는 이전 턴의
SQL/탐색 결과/코드매핑을 전혀 참조하지 않는다. `conversation_history`가
텍스트만 담고 있고, turn_reset 도입 후에도 이 구조는 동일하다. 이로 인해
"방금 쿼리에서 VIP만 필터해줘", "그 결과에서 월별 추이로 바꿔줘" 같은
정교한 연속 요청의 품질이 낮을 수 있다.

turn_reset 수정과는 **독립된 품질 개선 이슈**로 분리한다. 본 수정은 이
품질을 악화시키지도, 개선하지도 않는다.

**설계 공간 — 이전 SQL을 어디서 주입할지**

이전 턴 정보의 출처는 두 가지다. 두 소스 모두 turn_reset에 영향받지
않으므로 안전하게 사용 가능.

- **runner가 DB에서 로딩**: [turn_text_store.py](../../src/services/turn_text_store.py)의
  `metadata` 컬럼에 `executed_sql`/`sql_result`/`result_data`가 이미
  저장됨. runner가 턴 시작 시 이전 턴 metadata를 읽어 `initial_state`에
  신규 "previous_turn_context" 필드로 주입. 세션 지속 필드로 취급되어
  turn_reset이 건드리지 않음.
- **PipelineState 신규 필드 + reducer**: `previous_validated_sql: str`을
  세션 지속 필드로 추가. 매 턴 FMT/result_finalizer가 성공 시 현재 SQL을
  이 필드에 복사. turn_reset은 건드리지 않음.

두 방식 모두 동작하지만 DB 로딩 방식이 더 견고함(checkpointer 상태에
의존하지 않음, 세션 재개 시 자동 복구).

**주입 지점 — 어느 노드부터 이전 SQL을 볼 수 있게 할지**

이 결정이 핵심이며 트레이드오프가 있다.

| 주입 지점 | 장점 | 단점 |
|---|---|---|
| **sql_generator** (가장 하류, 최소 침습) | 프롬프트 변수 하나 추가로 끝. 기존 `{reference_sqls}`(Qdrant history) 패턴과 동일. 영향 범위 최소 | 탐색 단계(context_retriever)는 "새 질의인 것처럼" 재탐색 수행 → 이전 턴이 이미 찾은 테이블/코드를 중복 조회. LLM은 최종 생성 단계에서만 "아, 이어받는 거구나"를 인식 |
| **reasoning_preparer** (탐색 시작 전, 웜 스타트) | 이전 턴의 `explored_tables`/`explored_codes`/`knowledge_items`를 시드로 주입 → 탐색 루프가 중복 조회 스킵, 토큰/레이턴시 절감. CONTINUE 의미와 자연스럽게 일치 | 주입 데이터 품질에 따라 탐색 경로가 편향될 위험(이전 턴이 잘못된 테이블을 봤으면 이어짐). 검증 필요 |
| **intent_classifier** (가장 상류) | CONTINUE 판정 자체에 이전 SQL을 활용해 "리파인"/"새 분석" 구분 정확도 향상 가능 | 분류 단계가 SQL까지 봐야 하는 것은 과도. 프롬프트 크기 증가 대비 이득 불분명 |

**권장 설계 방향(초안 — 구현 전 검토 필요)**

1. **1차**: sql_generator에 이전 턴 SQL을 프롬프트 변수로 주입(가장 안전,
   영향 최소). 프롬프트에 "`is_continuation=True`일 때 이전 SQL을 수정
   기반으로 삼되, 질의 의도가 전혀 다르면 처음부터 생성해도 됨" 가이드
   추가.
2. **2차**(효과 측정 후): reasoning_preparer에서 `explored_tables` 웜
   스타트 도입. 이전 턴 테이블이 현재 질의와 관련 있는지를 LLM이 판정
   하게 하거나, 단순히 후보 목록으로만 제공하여 재검증 유도.

**주의사항**

- 이전 턴이 **실패**했을 경우 이전 SQL은 무효. `metadata.status`로 성공
  턴만 필터링해야 함.
- 이전 턴이 여러 개일 때 어디까지 볼지(직전 1개? 최근 N개?). 직전 1개가
  안전한 시작점.
- CONTINUE가 아닌 NEW 분류 질의에서는 주입하지 않음(의미 오염 방지).
- 프롬프트 크기 관리: 이전 SQL이 길면 요약/축약 필요.

**선행 조건**: 본 문서(§6)의 turn_reset 수정이 먼저 안정화된 후 착수.
turn_reset이 세션 지속 필드와 턴 스코프 필드의 경계를 명확히 만든 뒤에,
"이전 턴 컨텍스트"를 어느 쪽에 둘지가 깔끔하게 판단 가능해짐.

---

## 8. 영향 범위 요약

- **사용자 증상 해결**: 실패 턴에서 이전 턴 테이블 재노출 차단 (§1)
- **잠복 버그 해결**: `trace_log` 턴 간 오염 차단 (§3.1)
- **범위 외**: `resolved_signals` slow leak (§3.2 → §7.1 별도 이슈)
- **성능**: 턴당 dict 1개 생성 + LangGraph update 1회, 무시 가능
- **하위 호환**: 그래프 진입점만 변경, 기존 노드/엣지 불변
