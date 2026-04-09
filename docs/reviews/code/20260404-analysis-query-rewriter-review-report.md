# DATA_ANALYSIS 질의 재작성 기능 코드 리뷰

**일시**: 2026-04-04  
**대상**: analysis_query rewriter 기능 구현  
**변경 파일**: state.py, system_prompts.py, intent_classifier.py (서비스), intent_classifier.py (노드), analyzer.py

---

## 요약

DATA_ANALYSIS 의도 판정 시 시각화/분석 지시어를 제거하여 데이터 추출 중심 질의로 재작성하는 기능.
전반적으로 기존 패턴을 잘 따르고 있으며, 프롬프트 품질이 높음. 아래 6건의 이슈를 식별함.

---

## 1. set_current_node 복원 누락 [Warning]

**파일**: `src/services/intent_classifier.py` L285  
**등급**: Warning

`rewrite_analysis_query()`에서 `set_current_node("intent_classifier_rewriter")`를 호출하지만, 함수 종료 시 이전 노드명으로 복원하지 않는다.

**비교 — `llm_call_with_parse_retry`의 패턴** (`src/utils/llm/retry.py` L91-164):
```python
_prev_node = get_current_node()
if node_name:
    set_current_node(node_name)
# ... LLM 호출 ...
# 성공/실패 모두에서 복원:
if node_name:
    set_current_node(_prev_node)
```

`rewrite_analysis_query`는 직접 호출이므로 이 복원 로직이 없다. `set_current_node`는 `contextvars` 기반이므로 값이 다음 LLM 호출까지 잔류한다.

**실제 영향**: `rewrite_analysis_query` 호출 후 `intent_classifier_node`가 즉시 반환하므로, LangGraph의 `DataCopilotCallbackHandler.on_chain_start`가 다음 노드 진입 시 `set_current_node(node)`를 다시 호출한다 (callback_handler.py L302-304). 따라서 **실제 런타임 버그는 아니지만**, `callback_handler`에 의존하는 암묵적 계약이므로 방어적으로 복원하는 것이 안전하다.

**개선안**:
```python
async def rewrite_analysis_query(
    query: str,
    *,
    system_prompt: str,
) -> str:
    prev_node = get_current_node()
    set_current_node("intent_classifier_rewriter")
    try:
        client = get_llm_client()
        response = await client.messages.create(...)
        # ... 기존 로직 ...
        return result
    finally:
        set_current_node(prev_node)
```

---

## 2. analysis_query 턴 격리 미보장 [Critical]

**파일**: `src/agents/state/state.py` L647, `src/agents/graph/runner.py` L140-147  
**등급**: Critical

`analysis_query: str = ""`는 State에 기본값 `""`로 선언되어 있다. 새 턴에서 `PipelineState` 초기화 시 (`runner.py` L140-147) `analysis_query`를 명시적으로 설정하지 않으므로 기본값 `""`가 적용된다. 이 부분은 **LangGraph의 checkpointer 사용 시 이전 턴의 State가 잔류하는지 여부에 달려 있다**.

LangGraph가 매 턴마다 새 `PipelineState`를 `ainvoke`에 전달하므로 기본값이 적용되어 정상 동작하는 것으로 보인다. 그러나 **interrupt/resume 경로** (`runner.py` L134-136)에서는 `Command(resume=...)` 만 전달하여 기존 State를 이어받으므로, 이전 턴의 `analysis_query`가 잔류할 수 있다.

**시나리오**: 
1. 턴1: "월별 예금 추이 차트로 보여줘" -> `analysis_query` = "월별 예금 추이 차트로 보여줘"
2. 턴1에서 명확화 interrupt 발생
3. 턴2(resume): 사용자가 명확화 답변 제공 -> `analysis_query`가 턴1의 값을 그대로 유지
4. 이후 intent가 DATA_EXTRACTION으로 바뀌어도 analyzer에서 `state.analysis_query or state.preprocessed_input`에 의해 오염된 값 사용

**개선안**: intent_classifier_node에서 DATA_ANALYSIS가 아닌 경우 `analysis_query`를 명시적으로 `""`로 리셋하거나, analyzer에서 `state.intent == IntentType.DATA_ANALYSIS`를 추가 조건으로 사용.

```python
# analyzer.py L58 개선안
user_input = (
    state.analysis_query
    if state.intent == IntentType.DATA_ANALYSIS and state.analysis_query
    else state.preprocessed_input
)
```

---

## 3. IntentType 이중 import [Warning]

**파일**: `src/agents/nodes/interpret/intent_classifier.py` L22, L143  
**등급**: Warning

L22에서 `from src.models.enums import IntentType`을 모듈 최상위에서 import하면서, L143에서는 동일 모듈을 지역 import로 `IntentType as _IT`로 다시 import하고 있다.

```python
# L22 (최상위)
from src.models.enums import IntentType

# L143 (함수 내부)
from src.models.enums import IntentType as _IT
return {
    "intent": _IT.DATA_EXTRACTION,
    ...
}
```

L143의 `_IT`는 L22의 `IntentType`과 동일하므로 불필요한 지역 import이다. 이는 리뷰 대상 변경 이전부터 있던 코드이지만, L22에서 `IntentType`을 새로 추가한 시점에 정리되어야 했다.

**개선안**: L143의 지역 import를 제거하고 L22의 `IntentType`을 직접 사용.
```python
# L143 변경
return {
    "intent": IntentType.DATA_EXTRACTION,
    ...
}
```

---

## 4. CONTINUE + DATA_ANALYSIS 경로에서 rewriter 입력의 적합성 [Warning]

**파일**: `src/agents/nodes/interpret/intent_classifier.py` L221-235  
**등급**: Warning

CONTINUE 판정 시 `preprocessed_input`이 `continue_context`로 교체된 후 (L226), 그 값이 `_rewrite_for_analysis`에 전달된다 (L230-235):

```python
if result.resolution == HistoryDecision.CONTINUE:
    updates["continue_context"] = result.continue_context
    if result.continue_context:
        updates["preprocessed_input"] = result.continue_context  # L226

# DATA_ANALYSIS: 시각화/분석 지시어 제거
if result.intent == IntentType.DATA_ANALYSIS:
    current_input = updates.get("preprocessed_input", query)  # L230-231
    updates.update(await _rewrite_for_analysis(current_input))  # L233-235
```

`continue_context`는 LLM이 대화 맥락을 해소한 질의이다. 예를 들어 원본이 "그거 차트로 보여줘"이면 `continue_context`는 "이전에 조회한 월별 예금 잔액을 차트로 보여줘"로 해소된다. 이 값이 rewriter에 들어가는 것은 **의도적으로 올바른 설계**이다. 맥락이 해소된 상태에서 시각화 지시어를 제거해야 의미가 보존되기 때문이다.

다만, `analysis_query`에도 `continue_context` 값이 저장된다 (L88에서 `original_input = current_input`). 이는 **원본 사용자 질의가 아닌 LLM이 해석한 질의**가 `analysis_query`에 들어간다는 의미이다. analyzer에서 시각화 판단 시 LLM 해석 질의를 참조하게 되는데, 이것이 의도된 것인지 확인이 필요하다.

**확인 필요**: `analysis_query`의 의미가 "원본 사용자 질의"인지 "시각화/분석 지시어가 포함된 질의(맥락 해소 후)"인지 명확히 문서화해야 한다. State 필드 주석(L646)에는 "원본 보관"이라고 되어 있으나, CONTINUE 경로에서는 맥락 해소된 질의가 들어간다.

**개선안**: State 주석을 실제 동작에 맞게 수정.
```python
# W: intent_classifier (DATA_ANALYSIS 시 시각화/분석 지시 포함 질의 보관, CONTINUE 시 맥락 해소 후)
# R: analyzer (시각화 판단 시 참조)
analysis_query: str = ""
```

---

## 5. rewrite_analysis_query의 빈 응답 처리 [Info]

**파일**: `src/services/intent_classifier.py` L296-299  
**등급**: Info

```python
result = (
    response.content[0].text.strip()
    if response.content else ""
)
```

빈 응답(`""`)이 반환되면 `_rewrite_for_analysis` (노드 L94)에서 `if extraction:` 체크로 `preprocessed_input` 교체가 skip된다. 이 폴백은 올바르다.

그러나 `response.content`가 비어있지 않지만 `response.content[0].text`가 공백만 포함하는 경우 (예: `"  \n  "`)도 `strip()` 후 `""`가 되어 동일하게 처리된다. **현재 구현은 정상**.

다만, `response.content[0]`이 `TextBlock`이 아닌 경우 (예: Anthropic API의 `ToolUseBlock`) `text` 속성이 없을 수 있다. 현재 시스템 프롬프트가 plain text만 요청하므로 실질적 위험은 낮으나, 방어적 처리를 고려할 수 있다.

**개선안** (선택적):
```python
block = response.content[0] if response.content else None
result = block.text.strip() if block and hasattr(block, "text") else ""
```

---

## 6. 프롬프트 파일 매핑 문서 누락 [Info]

**파일**: `src/agents/nodes/system_prompts.py` L33-38 (docstring)  
**등급**: Info

docstring의 파일 매핑 (L33-38)에 `INTENT_CLASSIFIER_QUERY_REWRITER`가 누락되어 있다.

```
파일 매핑 (interpret/):
  INTENT_CLASSIFIER_SYSTEM        <- intent_classifier_system.txt
  INTENT_CLASSIFIER_USER          <- intent_classifier_user.txt
  # INTENT_CLASSIFIER_QUERY_REWRITER 누락
```

**개선안**: docstring에 매핑 추가.
```
  INTENT_CLASSIFIER_QUERY_REWRITER <- intent_classifier_query_rewriter.txt
```

---

## 리뷰 요약

| # | 등급 | 항목 | 파일 |
|---|------|------|------|
| 1 | Warning | set_current_node 복원 누락 | services/intent_classifier.py |
| 2 | Critical | analysis_query 턴 격리 (interrupt/resume 잔류) | state.py, analyzer.py |
| 3 | Warning | IntentType 이중 import | nodes/interpret/intent_classifier.py |
| 4 | Warning | analysis_query 주석과 실제 동작 불일치 (CONTINUE 경로) | state.py |
| 5 | Info | 빈 응답/비텍스트 블록 방어 (현재 정상) | services/intent_classifier.py |
| 6 | Info | 프롬프트 파일 매핑 docstring 누락 | system_prompts.py |

---

## 리뷰 요청 포인트에 대한 답변

### 1. set_current_node 복원
Warning 등급. callback_handler가 다음 노드 진입 시 덮어쓰므로 런타임 버그는 아니지만, `llm_call_with_parse_retry`의 save/restore 패턴과 일관성이 없다. try/finally로 복원 추가를 권장한다.

### 2. 에러 폴백
analysis_query = original_input, preprocessed_input = original_input 으로 동일 값이 되는 케이스 자체는 문제없다. analyzer에서 `analysis_query or preprocessed_input`이므로 어느 쪽이든 동일 값이 사용된다. **다만 Critical #2의 턴 격리 문제가 더 중요하다.**

### 3. CONTINUE + DATA_ANALYSIS
맥락 해소된 질의가 rewriter에 들어가는 것은 올바른 설계. 다만 analysis_query의 의미가 "원본"에서 "맥락 해소 후 질의"로 바뀌는 점을 주석에 반영해야 한다 (Warning #4).

### 4. 직접 LLM 호출 패턴
`data_analyzer.py`의 `generate_svg_via_llm` (L140-169)도 동일하게 `client.messages.create`를 직접 호출한다. plain text 출력에서 JSON 파싱이 불필요한 경우의 확립된 패턴이므로 문제없다.

### 5. IntentType import
L22에서 모듈 최상위 import가 이번 변경에서 추가되었고, L143의 기존 지역 import `as _IT`와 중복된다. L143을 정리해야 한다 (Warning #3).

### 6. 타입 힌트 완전성
모든 함수 시그니처에 타입 힌트가 있다. `_rewrite_for_analysis` 반환 타입이 `dict`로 되어 있는데, `dict[str, str]`로 좁히는 것이 더 정확하지만, 기존 노드 함수들의 `dict` 반환 패턴과 일관성이 있으므로 수용 가능하다.
