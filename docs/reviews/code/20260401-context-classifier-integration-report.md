# Code Review: context_classifier 통합 리팩터링

**일시**: 2026-04-01
**대상**: resolve_history + classify_intent -> context_classifier 통합
**리뷰어**: Code Reviewer Agent


## 요약

resolve_history + classify_intent 두 노드를 context_classifier 단일 노드로 통합하고,
비데이터 의도(CASUAL_TALK, META_QUESTION)를 위한 simple_responder 경량 응답 노드를 신설한 리팩터링.
전반적으로 설계 의도가 잘 반영되어 있으나, 아래 6건의 Critical 및 Warning 이슈가 확인됨.


---

## 1. 기능 정확성

### 1.1 [Critical] AMBIGUOUS 경로에서 source_node가 "context_classifier"로 설정되어 명확화 후 자기 자신으로 복귀

**파일**: `src/agents/nodes/interpret/context_classifier.py` L98-119
**파일**: `src/agents/graph/pipeline.py` L352-356, L369-379

AMBIGUOUS 판정 시 AmbiguitySignal.source_node = "context_classifier"로 설정된다.
clarification_handler에서 interrupt/resume 후 `_route_after_clarify`가 resolved_signals[-1].source_node를 읽어 복귀 대상을 결정한다.

현재 `_VALID_RETURN_TARGETS`에 "context_classifier"가 포함되어 있으므로 **context_classifier 자신에게 복귀**한다.
이는 설계 의도대로일 수 있지만, 기존 intent_classifier.py(L127-128)에서는 AMBIGUOUS 시 source_node를
"normalize_query"로 설정하여 명확화 후 정규화로 직행했다.

**문제**: 통합 후 AMBIGUOUS -> clarify -> context_classifier 복귀 시,
사용자의 명확화 응답이 preprocessed_input에 반영되지 않은 상태에서 LLM이 동일한 질의를
재판정하므로 **무한 루프 가능성**이 있다. 명확화 응답은 clarification_response에 저장되지만,
context_classifier 서비스는 이를 clarification_history 파라미터로 전달받아 LLM에게 제공한다.
따라서 LLM이 명확화 응답을 참고하여 다른 판정을 내릴 가능성은 있으나, LLM이 다시 AMBIGUOUS를
반환하면 루프가 발생한다.

**권장 조치**:
- (A안) clarification_turns 카운터를 체크하여 재진입 시 AMBIGUOUS를 강제로 DATA_EXTRACTION 폴백
- (B안) 기존처럼 source_node를 "normalize_query"로 변경하여 명확화 후 정규화로 직행


### 1.2 [Warning] UNSURE에서 status가 설정되지 않음

**파일**: `src/agents/nodes/interpret/context_classifier.py` L72-93

UNSURE 경로에서 pending_signals, intent, intent_confidence, query_category, trace_log를 반환하지만
**status 필드가 누락**되어 있다. 기본값 QueryStatus.PENDING이 유지된다.
비교 대상인 AMBIGUOUS 경로(L114)에서는 `QueryStatus.INTENT_CLASSIFIED`를 명시적으로 설정한다.

UNSURE -> clarification_handler -> context_classifier 복귀 시,
상태가 PENDING인 채로 다시 context_classifier가 실행되므로 기능적 문제는 없지만,
status 필드의 의미론적 일관성이 깨진다.

**권장 조치**: UNSURE 반환 dict에도 `"status": QueryStatus.INTENT_CLASSIFIED`를 추가.


---

## 2. 그래프 무결성

### 2.1 [Info] NODE_PROGRESS_MAP에 신규 노드 미등록

**파일**: `src/utils/tracker/callback_handler.py` L62-156

`NODE_PROGRESS_MAP`에 "context_classifier"와 "simple_responder" 노드가 등록되어 있지 않다.
기존 "resolve_history"와 "classify_intent"는 등록되어 있으나,
실제 그래프에서 사용되는 노드명은 "context_classifier"이므로 WebSocket 진행률 이벤트가 전송되지 않는다.

**권장 조치**: NODE_PROGRESS_MAP에 다음 항목 추가:
```python
"context_classifier": {
    "phase": "interpret",
    "label": "대화이력 해소 + 질의 유형 분류",
    "thinking": "질의 의도 파악 중",
},
"simple_responder": {
    "phase": "present",
    "label": "간단 응답 생성",
    "thinking": "응답 생성 중",
},
```


### 2.2 [Info] simple_responder -> format_response 경로에서 이중 formatted_response 설정

**파일**: `src/agents/nodes/present/simple_responder.py` L70
**파일**: `src/agents/graph/pipeline.py` L560

simple_responder가 `formatted_response`를 설정한 후 `format_response` 노드로 엣지가 연결된다.
format_response 노드에서 `formatted_response`를 다시 덮어쓸 수 있다.

현재 format_response 구현이 이미 formatted_response가 있을 때 이를 존중하는지 확인이 필요하다.
만약 덮어쓴다면 simple_responder의 경량 응답이 손실된다.

**권장 조치**: format_response_node가 이미 formatted_response가 설정된 상태를 적절히 처리하는지 확인.
또는 simple_responder -> END로 직접 연결하는 것도 고려 (경량 응답이므로 포맷팅 불필요).


### 2.3 [Info] _LEGACY_TARGET_MAP이 잘 설계됨

**파일**: `src/agents/graph/pipeline.py` L359-363

기존 세션에서 source_node가 "resolve_history", "classify_intent", "resolve_and_classify"일 수 있는
과도기 호환이 적절히 처리되어 있다. 설계 의도 충족.


---

## 3. 상태 일관성

### 3.1 [Critical] awaiting_clarification이 PipelineResult에 여전히 존재하며 runner.py에서 사용 중

**파일**: `src/agents/models/response.py` L47
**파일**: `src/agents/graph/runner.py` L180
**파일**: `src/main.py` L231, L280, L426

PipelineState에서 `awaiting_clarification`이 제거된 것은 확인되었으나,
`PipelineResult` 모델과 `runner.py`, `main.py`에서는 여전히 `awaiting_clarification` 필드를 사용한다.

이는 **의도적 분리**일 수 있다 (PipelineState와 PipelineResult는 별개 모델).
runner.py L180에서 interrupt 감지 시 `awaiting_clarification=True`를 직접 설정하고,
main.py에서 이를 참조하여 WebSocket 응답을 분기한다.

**결론**: PipelineState에서의 제거는 올바르며, PipelineResult의 awaiting_clarification은
그래프 외부(runner/main)에서 interrupt 상태를 전달하는 용도이므로 유지가 타당하다.
**이 항목은 Critical에서 Info로 하향** -- 기존 리뷰 관점 유지를 위해 잔류 확인 결과를 기록.


### 3.2 [Warning] is_continuation/continue_context가 하류 노드에서 아직 활용되지 않음

**파일**: `src/agents/state/state.py` L579-581

grep 결과, reason 계층(planner, sql_generator 등)과 normalize_query에서
`is_continuation`이나 `continue_context`를 참조하는 코드가 없다.

이 필드들은 현재 **저장만 되고 소비되지 않는** 상태다.
CONTINUE 판정 시 질의 재작성을 하지 않으므로, 하류 노드가 continue_context를 활용하여
맥락을 보강해야 정확한 SQL 생성이 가능하다.

**문제 시나리오**: "이번 달 신규 고객 수 알려줘" -> "그 중에서 VIP는?"
- 기존: resolve_history가 "이번 달 신규 고객 중 VIP 등급 고객 수 알려줘"로 재작성
- 통합 후: preprocessed_input = "그 중에서 VIP는?" 그대로, continue_context에 해석만 저장
- planner/sql_generator는 "그 중에서 VIP는?"만 보고 SQL 생성 -> **맥락 손실**

**권장 조치**:
- normalize_query 또는 planner에서 is_continuation == True일 때
  continue_context를 preprocessed_input 대신(또는 보조로) 활용하는 로직 추가 필수
- 또는 context_classifier에서 CONTINUE 시 preprocessed_input을 continue_context로 덮어쓰기


---

## 4. import/참조 깨짐

### 4.1 [Warning] context_classifier 서비스에서 private 함수 _format_history를 import

**파일**: `src/services/context_classifier.py` L17

```python
from src.services.history_resolver import (
    HistoryDecision,
    build_unsure_clarification,
    _format_history,  # private 함수 외부 import
)
```

`_format_history`는 `_` prefix로 모듈 내부 함수임을 나타낸다.
외부 모듈에서 import하는 것은 캡슐화 위반이다.

**권장 조치**: `_format_history`를 `format_history`로 rename하여 public API로 명시하거나,
공통 유틸리티(`src/utils/` 또는 `src/services/`)로 추출.


### 4.2 [Warning] context_classifier 서비스에서 private 함수 _map_category_to_intent를 import

**파일**: `src/services/context_classifier.py` L19-21

```python
from src.services.intent_resolver import (
    _map_category_to_intent,
)
```

동일하게 private 함수 외부 import. rename 또는 추출 필요.


### 4.3 [Info] deprecated 노드에서 제거된 프롬프트 변수를 직접 로드하여 자체 해결

**파일**: `src/agents/nodes/interpret/history_resolver.py` L33-37
**파일**: `src/agents/nodes/interpret/intent_classifier.py` L42-50

system_prompts.py에서 제거된 변수를 `load_text_required`로 직접 로드하여 자체 폴백 경로를 유지.
deprecated 모듈로서 적절한 처리.


### 4.4 [Info] QueryCategory.CLARIFICATION 제거 영향 범위 확인

`intent_resolver.py` L185에서 `"CLARIFICATION"` 문자열 키가 여전히 존재하지만,
이는 QueryCategory Enum이 아닌 일반 dict 키이므로 런타임 오류는 발생하지 않는다.
`context_classifier.py` L226-227에서 LLM이 "CLARIFICATION"을 반환하면 "AMBIGUOUS"로 변환하는
방어 코드가 적절히 구현되어 있다.


---

## 5. 보안/성능

### 5.1 [Info] LLM 호출 타임아웃 적절히 설정됨

**파일**: `src/services/context_classifier.py` L109-110

```python
max_tokens=settings.llm_default_max_tokens,  # 1000
timeout=settings.llm_default_timeout,         # 15.0초
```

`llm_call_with_parse_retry`에 타임아웃과 max_tokens가 settings에서 주입되어 적절하다.


### 5.2 [Info] 폴백에서 무한 재시도 방지 확인

**파일**: `src/services/context_classifier.py` L113-122

primary LLM 호출 실패 시 `_fallback`을 1회만 호출하고,
fallback도 실패하면 `is_error=True`로 종료한다.
폴백 내부에서 `resolve_history`를 호출하는데, 이 함수도 자체 재시도 + 실패 시 NEW 폴백을 가지므로
무한 재시도는 발생하지 않는다.


### 5.3 [Info] 프롬프트 인젝션 방어 유지

사용자 입력은 runner.py의 `sanitize()`를 거쳐 preprocessed_input으로 전달되며,
context_classifier 프롬프트에서 사용자 입력은 `{query}` 플레이스홀더에 삽입된다.
sanitize 단계에서 위험한 패턴이 필터링되므로 기존 방어 수준이 유지된다.


---

## 6. 하류 노드 영향

### 6.1 [Critical] CONTINUE 시 질의 재작성 미수행 -- 하류 노드 맥락 손실

*3.2항과 동일 이슈의 구체적 영향 분석*

**기존 동작** (resolve_history_node):
- CONTINUE -> `preprocessed_input`을 재작성된 질의로 **교체**
- 하류 노드(normalize_query, planner, sql_generator)는 재작성된 완전한 질의를 기반으로 동작

**통합 후 동작** (context_classifier_node):
- CONTINUE -> `preprocessed_input` **변경 안 함**, `continue_context`에 해석만 저장
- 하류 노드는 원본 질의("그 중에서 VIP는?")만 참조

**영향받는 노드 목록**:
| 노드 | preprocessed_input 참조 | continue_context 참조 | 영향 |
|------|------------------------|----------------------|------|
| normalize_query | O (원본으로 8-Slot 파싱) | X | CONTINUE 시 불완전한 파싱 |
| planner | O (탐색 전략 수립) | X | 맥락 없는 탐색 계획 |
| sql_generator | O (SQL 생성) | X | 부정확한 SQL |

**conversation_history 접근**: PipelineState에 conversation_history가 있으므로 하류 노드가
이를 직접 참조할 수는 있으나, 현재 reason 계층 노드들은 이를 사용하지 않는다.

**권장 조치 (우선순위 높음)**:
1. normalize_query에서 `is_continuation == True`이면 `continue_context`를 원본 대신 사용
2. 또는 context_classifier_node에서 CONTINUE 시 `preprocessed_input`을 `continue_context`로 교체


---

## 7. 기타 개선 제안

### 7.1 [Info] user 프롬프트 템플릿에서 빈 history 처리

**파일**: `resources/prompts/interpret/context_classifier_user.txt`

```
{history}
{clarification_history}

[현재 입력]

{query}
```

history가 빈 문자열("")일 때 프롬프트 상단에 빈 줄이 남는다.
LLM 성능에 실질적 영향은 미미하지만, 시스템 프롬프트에서 "이전 대화가 없으면 SKIP"이라는
지침이 있으므로 빈 history에 대해 "(이전 대화 없음)"을 삽입하면 LLM의 판단이 더 명확해질 수 있다.


### 7.2 [Info] simple_responder의 정형 응답 확장 가능성

**파일**: `src/agents/nodes/present/simple_responder.py`

현재 5개 키워드 매칭만 수행하며, "넘어가"가 `_CASUAL_SIGNALS`에 있지만
`_CASUAL_RESPONSES`에는 대응 응답이 없다. `_CASUAL_DEFAULT`가 반환되지만,
"넘어가"에 대해 "됐어"/"그만"과 같은 종료 뉘앙스 응답이 더 적절하다.


---

## 이슈 요약

| 등급 | 항목 | 위치 | 설명 |
|------|------|------|------|
| Critical | 1.1 | context_classifier_node L98 | AMBIGUOUS 복귀 시 무한루프 가능성 |
| Critical | 6.1 / 3.2 | context_classifier_node L123-130 | CONTINUE 시 질의 재작성 미수행, 하류 맥락 손실 |
| Warning | 1.2 | context_classifier_node L72-93 | UNSURE에서 status 미설정 |
| Warning | 4.1 | context_classifier.py L17 | private 함수 _format_history 외부 import |
| Warning | 4.2 | context_classifier.py L19-21 | private 함수 _map_category_to_intent 외부 import |
| Info | 2.1 | callback_handler.py | NODE_PROGRESS_MAP에 신규 노드 미등록 |
| Info | 2.2 | pipeline.py L560, simple_responder.py L70 | simple_responder -> format_response 이중 설정 |
| Info | 7.1 | context_classifier_user.txt | 빈 history 표현 개선 가능 |
| Info | 7.2 | simple_responder.py | "넘어가" 키워드 응답 누락 |


## 결론

통합 리팩터링의 그래프 구조, 엣지 연결, 폴백 설계, 보안은 양호하다.
그러나 **Critical 2건은 즉시 대응이 필요**하다:

1. **CONTINUE 시 맥락 전달 문제** (6.1): 질의 재작성을 제거했으나 대체 수단이 하류에 미구현.
   이 상태로 배포하면 후속 질의("그 중에서 VIP는?") 유형의 요청에서 SQL 품질이 크게 저하된다.

2. **AMBIGUOUS 루프 방지** (1.1): clarification_turns 기반 가드 또는 source_node 변경으로
   무한 루프를 차단해야 한다.
