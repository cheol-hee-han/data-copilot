# 전략 문서 정합성 검증 보고서

- **대상 문서**:
  - `docs/strategy-proposals/checkpointer-multi-turn/01-strategy.md`
  - `docs/strategy-proposals/checkpointer-multi-turn/02-detailed-design.md`
- **검증일**: 2026-03-31
- **검증 목적**: 01(전략)과 02(상세 설계)의 일관성, v3 원칙 준수, 기각 대안 잔재, 코드 스니펫 정합성

---

## 1. 설계 결정(D1~D8) 일관성

| # | 01 결정 | 02 반영 여부 | 판정 |
|---|---------|-------------|------|
| D1 | AsyncPostgresSaver | 1.2 팩토리에서 AsyncPostgresSaver 사용 | OK |
| D2 | history_db 공존 | 1.1 `_build_conninfo()`에서 history_db 폴백 | OK |
| D3 | 순수 interrupt() | 2.1~2.8 전체에서 interrupt만 사용 | OK |
| D4 | Pydantic BaseModel 유지 | 2.3 PipelineState(BaseModel) | OK |
| D5 | Unified Clarification | 2.2~2.5 clarification_handler 노드 + HandlerRegistry | OK |
| D6 | Structured Context Passing | 2.9 `_build_sql_prompt()` 분리 섹션 전달 | OK |
| D7 | preprocess 제거 + runner sanitize | 2.1, 2.6, 2.7, 2.10 | OK |
| D8 | SessionStore conversation_history 유지 | 3.1~3.4 | OK |

**결론**: D1~D8 전체가 두 문서에서 일관되게 반영됨. 불일치 없음.

---

## 2. v3 핵심 원칙 준수 검증

### 2.1 순수 interrupt()

- 01: Section 2.2에서 "모든 명확화를 interrupt()로 통일" 명시
- 02: 모든 트리거(T1~T5)가 `pending_clarification` 세팅 -> `clarification_handler` -> `interrupt()` 경유
- **판정**: OK

### 2.2 Unified Clarification Framework

- 01: Section 2.3에서 5개 트리거 통합 경로 표로 정의
- 02: Section 2.2~2.8에서 스키마, 핸들러, 노드, 마이그레이션 코드 모두 구현
- **판정**: OK

### 2.3 Structured Context Passing

- 01: Section 2.4에서 원본 보존 + Q&A 독립 누적 원칙
- 02: Section 2.9에서 `_build_sql_prompt()` 구현
- **판정**: OK

### 2.4 preprocess 노드 제거 + runner sanitize

- 01: Section 2.2 "보안 검증: preprocess 노드 제거" 명시
- 02: Section 2.7 `build_pipeline()`에서 preprocess 미등록, 2.10에서 `preprocessor.py` 제거 명시
- **판정**: OK

---

## 3. 기각 대안 잔재 검증

| 기각 대안 | 02 잔재 여부 | 판정 |
|-----------|-------------|------|
| Shortcut 패턴 | 02에 `_route_after_preprocess` 없음, shortcut 언급 없음 | OK |
| 하이브리드 (Checkpoint + Shortcut) | 02에 하이브리드 패턴 없음 | OK |
| Query Rewriting | 02에 질의 재작성 없음, original_query 불변 원칙 유지 | OK |
| `clarification_origin` | 02 state.py 스니펫에서 `[제거]` 명시, 코드에 미사용 | OK |

**결론**: 기각 대안이 02 코드에 남아있지 않음.

---

## 4. Phase 1~4 매칭 검증

| Phase | 01 정의 | 02 구현 | 판정 |
|-------|---------|---------|------|
| 1 | Core Checkpointer (config, factory, pipeline compile, runner thread_id, lifespan) | 1.1~1.6 (config, checkpointer.py, pipeline.py, runner.py, main.py, 직렬화 테스트) | OK |
| 2 | Unified Clarification + 순수 interrupt (스키마, 핸들러, clarification_handler, 트리거 마이그레이션, preprocess 제거) | 2.1~2.10 (전체 구현) | OK |
| 3 | 세션 관리 통합 (SessionStore clarify 제거, Redis 호출 제거, synthesize 제거) | 3.1~3.4 | OK |
| 4 | 고급 기능 (TTL, RetryPolicy, time-travel, Encrypted, SQL 승인) | 4.1~4.4 | OK |

**결론**: Phase 구성 매칭 정상.

---

## 5. 발견된 불일치 및 오류

### 5.1 [WARNING] T5 트리거 노드명 불일치

**위치**: 01 Section 2.3 트리거 표 vs 02 Section 2.8 T5 마이그레이션 코드

01의 트리거 표:
| # | 트리거 | return_to |
|---|--------|-----------|
| T5 | result_finalizer CONFLICTED | confidence_evaluator |

01 Section 4.3 CONFLICTED 흐름:
> `confidence_evaluator -> [CONFLICTED] -> ... -> return_to="confidence_evaluator"`

02 Section 2.8 T5 코드 (line 1007~1033):
```python
# result_finalizer.py (또는 confidence_evaluator.py)  <-- 파일명이 모호
async def confidence_evaluator_node(state: PipelineState) -> dict:  <-- 함수명은 confidence_evaluator
```

**문제**: 01에서 T5는 **"result_finalizer CONFLICTED"** 라고 명시하고 있으나, 02의 T5 마이그레이션 코드에서는 `confidence_evaluator_node` 함수로 구현하며 파일명도 "result_finalizer.py (또는 confidence_evaluator.py)"로 모호하게 기술. 현재 소스 코드를 확인하면 CONFLICTED 판정은 `confidence_evaluator.py`에서 수행하고, `result_finalizer.py`에서 `clarification_turns`를 증가시키는 코드가 존재하여 실제로 두 파일 모두 관련됨.

**구체적 혼란**: 01의 트리거 표에는 "result_finalizer CONFLICTED"라고 되어있으나, return_to는 "confidence_evaluator"이고, 02 구현 코드의 함수명은 `confidence_evaluator_node`. 트리거를 발생시키는 노드가 result_finalizer인지 confidence_evaluator인지 두 문서 간 불명확.

**등급**: WARNING

**권장 조치**: 01 트리거 표의 T5를 "confidence_evaluator CONFLICTED" 또는 정확한 트리거 노드명으로 통일하고, 02의 주석에서 "(또는 confidence_evaluator.py)" 모호 표현을 제거하여 단일 노드명으로 확정.

---

### 5.2 [WARNING] clarification_turns 필드 누락

**위치**: 01 Section 2.6 + 02 Section 2.3 (state.py 변경)

01과 02 모두 `state.py`의 신규/제거 필드를 나열하지만, 기존 `clarification_turns: int = 0` 필드의 처리 방침이 **어디에도 언급되지 않음**.

현재 코드에서 `clarification_turns`는 다음 위치에서 사용됨:
- `state.py` (line 568): 필드 정의
- `pipeline.py` (line 144, 163): 라우팅 분기에서 최대 턴수 체크
- `runner.py` (line 106, 148-149): 상태 초기화/결과 추출
- `clarifier.py` (line 63, 85): 턴 카운트 증가
- `result_finalizer.py` (line 56): 턴 카운트 증가
- `sql_generator.py` (line 166): 턴 카운트 증가
- `main.py` (line 220, 412): 결과 전송

02 Section 2.8 T3 마이그레이션 코드에서 `state.clarification_turns < max_turns`를 사용하고 있으나, 이 필드가 Unified Clarification 체제에서 어떻게 관리되는지(ClarificationEntry 리스트 길이로 대체? 기존 필드 유지?) 정의되지 않음.

**등급**: WARNING

**권장 조치**: `clarification_turns`를 `len(state.clarifications)`로 대체할지, 기존 필드를 유지할지 명시적으로 결정하고 두 문서에 반영. 대체한다면 `[제거]` 목록에 추가, 유지한다면 업데이트 로직을 clarification_handler 노드에서 처리하도록 명시.

---

### 5.3 [WARNING] ClarificationResponse 스키마 언급 후 미정의

**위치**: 01 Section 6 Phase 2 항목 1

01 Phase 2 단계 1:
> `ClarificationRequest` / `ClarificationEntry` / **`ClarificationResponse`** 스키마 정의

02 Section 2.2의 clarification.py 스키마:
- `ClarificationRequest` -- 정의됨
- `ClarificationEntry` -- 정의됨
- `ClarificationResponse` -- **정의되지 않음**

01에서 Phase 2의 첫 번째 단계에서 3개 스키마를 나열했지만, 02에는 `ClarificationResponse`가 구현되지 않음. `Command(resume=)`의 문자열 응답이 이를 대체하는 것으로 보이나, 명시적 설명이 없음.

**등급**: WARNING

**권장 조치**: `ClarificationResponse`가 불필요하다면 01의 Phase 2 항목에서 제거. 필요하다면 02에 스키마 정의를 추가.

---

### 5.4 [INFO] runner.py Phase 1 vs Phase 2 시그니처 불일치

**위치**: 02 Section 1.4 (Phase 1) vs 02 Section 2.6 (Phase 2)

Phase 1의 `run_pipeline()` 시그니처 (line 180-186):
```python
async def run_pipeline(
    user_input: str,
    session_id: str | None = None,
    conversation_history: list[dict[str, str]] | None = None,
    *,
    clarification_state: dict[str, Any] | None = None,  # <-- 아직 존재
    on_event: OnEventCallback | None = None,
) -> PipelineResult:
```

Phase 2의 `run_pipeline()` 시그니처 (line 737-743):
```python
async def run_pipeline(
    user_input: str,
    session_id: str | None = None,
    conversation_history: list[dict[str, str]] | None = None,
    *,
    on_event: OnEventCallback | None = None,  # clarification_state 제거됨
) -> PipelineResult:
```

Phase 3 (line 1149-1156)에서 `clarification_state` 파라미터 "완전 제거"라고 명시하지만, Phase 2 코드에서 이미 제거되어 있음.

**문제**: Phase 2에서 이미 `clarification_state`가 사라졌는데 Phase 3에서 다시 "제거"라고 기술. Phase 1에서는 아직 존재하므로, 제거 시점이 Phase 2인지 Phase 3인지 모호.

**등급**: INFO

**권장 조치**: `clarification_state` 파라미터 제거를 Phase 2 또는 Phase 3 중 하나로 확정하고, 나머지 Phase의 코드 스니펫을 일치시킬 것.

---

### 5.5 [INFO] 02 T5 return_to 값의 트리거 표 매칭 확인

**위치**: 01 Section 2.3 트리거 표 vs 02 Section 2.8 T5 코드

01 트리거 표: T5 return_to = `confidence_evaluator`
02 T5 코드 (line 1029): `return_to="confidence_evaluator"`

**판정**: return_to 값 자체는 일치함. 다만 5.1에서 지적한 트리거 발생 노드명 혼란이 있으므로 함께 정리 필요.

---

### 5.6 [INFO] 파일별 변경 범위 체크리스트 매칭

01 Section 5.1의 파일 목록과 02 체크리스트 비교:

| 01에 명시된 파일 | 02 체크리스트 존재 | 판정 |
|-----------------|-------------------|------|
| config.py (Phase 1) | Phase 1 체크리스트에 있음 | OK |
| pipeline.py (Phase 1+2) | Phase 1, 2에 있음 | OK |
| runner.py (Phase 1+2) | Phase 1, 2에 있음 | OK |
| state.py (Phase 2) | Phase 2에 있음 | OK |
| main.py (Phase 1+3) | Phase 1, 3에 있음 | OK |
| checkpointer.py (Phase 1) | Phase 1에 있음 | OK |
| clarification.py (Phase 2) | Phase 2에 있음 | OK |
| handlers.py (Phase 2) | Phase 2에 있음 | OK |
| clarification_handler.py (Phase 2) | Phase 2에 있음 | OK |
| preprocessor.py 제거 (Phase 2) | Phase 2에 있음 | OK |
| clarifier.py 제거 (Phase 2) | Phase 2에 있음 | OK |
| history_resolver.py (Phase 2) | Phase 2에 있음 | OK |
| intent_classifier.py (Phase 2) | Phase 2에 있음 | OK |
| query_normalizer.py (Phase 2) | Phase 2에 있음 | OK |
| sql_generator.py (Phase 2) | Phase 2에 있음 | OK |
| result_finalizer.py (Phase 2) | **Phase 2에 없음** | 불일치 |
| session/store.py (Phase 3) | Phase 3에 있음 | OK |
| input_sanitizer.py (Phase 3) | Phase 2 체크리스트에 있음 (01은 Phase 3) | 불일치 |
| thread_manager.py (Phase 4) | Phase 4에 있음 | OK |

**불일치 1**: 01에서 `result_finalizer.py`가 Phase 2 수정 대상이지만, 02 Phase 2 체크리스트에는 `confidence_evaluator.py`가 대신 나열됨. 이는 5.1의 T5 노드명 혼란과 연결됨.

**불일치 2**: 01은 `input_sanitizer.py`를 Phase 3에 배치했으나, 02 체크리스트는 Phase 2에서 `synthesize_clarification()` 제거를 나열함. Phase 분류가 다름.

**등급**: INFO

**권장 조치**: T5 트리거 노드를 확정한 후 01/02 양쪽의 파일 목록을 동기화. `input_sanitizer.py` 정리 시점도 통일.

---

### 5.7 [INFO] 02 코드에서 v2 잔재 확인

02 전체 코드 스니펫에서 v2 잔재 키워드 검색:

| v2 잔재 키워드 | 02에서 발견 여부 |
|---------------|-----------------|
| `clarification_origin` | 미발견 (state.py `[제거]` 주석만 있음) |
| `_route_after_preprocess` | 미발견 |
| `preprocess_node` / `preprocess` 노드 | 미발견 (제거 명시만 있음) |
| `clarification_response` | 미발견 (state.py `[제거]` 주석만 있음) |

**판정**: v2 잔재 없음. OK.

---

### 5.8 [INFO] 5개 트리거(T1~T5) 일관성

| # | 01 트리거 표 | 02 ClarifyTrigger enum | 02 마이그레이션 코드 | 판정 |
|---|-------------|----------------------|-------------------|------|
| T1 | history_resolver UNSURE | HISTORY_UNSURE | resolve_history_node (line 919) | OK |
| T2 | classify_intent AMBIGUOUS | INTENT_AMBIGUOUS | classify_intent_node (line 943) | OK |
| T3 | normalize_query ambiguities | QUERY_AMBIGUITIES | normalize_query_node (line 965) | OK |
| T4 | sql_generator Cross-DB | CROSS_DB | sql_generator_node (line 988) | OK |
| T5 | result_finalizer CONFLICTED | SCHEMA_CONFLICTED | confidence_evaluator_node (line 1012) | 노드명 불일치 (5.1 참조) |

트리거 ID, QuestionType, return_to 값:

| # | 01 응답형태 | 02 question_type | 01 return_to | 02 return_to | 판정 |
|---|-----------|-----------------|-------------|-------------|------|
| T1 | FREE_TEXT | FREE_TEXT | resolve_history | resolve_history | OK |
| T2 | FREE_TEXT | FREE_TEXT | normalize_query | normalize_query | OK |
| T3 | FREE_TEXT | FREE_TEXT | normalize_query | normalize_query | OK |
| T4 | SINGLE_SELECT | SINGLE_SELECT | sql_generator | sql_generator | OK |
| T5 | SINGLE_SELECT | SINGLE_SELECT | confidence_evaluator | confidence_evaluator | OK |

---

### 5.9 [INFO] ClarificationRequest / ClarificationEntry 스키마 일관성

01 Section 2.3 + 2.6에서 정의된 필드:

**ClarificationRequest** (01):
- question_type (FREE_TEXT/SINGLE_SELECT/CONFIRM)
- 선택지, context_summary, return_to

**ClarificationRequest** (02 line 374-386):
- trigger: ClarifyTrigger
- question: str
- question_type: QuestionType (FREE_TEXT/SINGLE_SELECT/CONFIRM)
- options: list[str]
- context_summary: str
- return_to: str

**차이점**: 02에는 `trigger` 필드가 추가되어 있으나 01의 ClarificationRequest 설명에는 trigger 필드가 명시적으로 나열되지 않음. 단, 01의 "구조" 다이어그램에서 "HandlerRegistry에서 유형별 핸들러"라고 기술하므로 trigger 식별자가 필요함이 암시됨.

**ClarificationEntry** (01 Section 2.6):
- 명시적 필드 나열 없음, "Q&A 리스트" 개념만 언급

**ClarificationEntry** (02 line 389-399):
- trigger: ClarifyTrigger
- question: str
- answer: str
- return_to: str

**판정**: 02가 01을 상세화한 것으로 모순은 없으나, 01에서 ClarificationEntry의 필드 구성을 명시하지 않아 추적이 어려움.

**등급**: INFO

---

### 5.10 [INFO] runner.py sanitize -> aget_state -> Command(resume=) / ainvoke 흐름

01 Section 2.2 + 3계층 구조 + 4.1 TO-BE:
```
run_pipeline: sanitize() -> aget_state -> interrupt 대기 중 감지
-> Command(resume=sanitized_text)
-> 새 턴? -> ainvoke(initial_state)
```

02 Section 2.6 (line 759~799):
```python
sanitized = sanitize(user_input)           # 1. sanitize
state_snapshot = await app.aget_state(config)  # 2. aget_state
if is_interrupt_pending:
    result = await app.ainvoke(Command(resume=sanitized.text), config=config)  # 3a
else:
    result = await app.ainvoke(initial_state, config=config)  # 3b
```

**판정**: 두 문서의 흐름이 정확히 일치함. OK.

---

### 5.11 [WARNING] pipeline.py 트리거 노드 목록과 실제 T5 불일치

**위치**: 02 Section 2.7 (line 866-872)

```python
for trigger_node in [
    "resolve_history",
    "classify_intent",
    "normalize_query",
    "sql_generator",
    "confidence_evaluator",  # <-- T5
]:
```

01 트리거 표에서 T5는 "result_finalizer CONFLICTED"로 표기되었으나, 02의 pipeline.py 코드에서는 `confidence_evaluator`가 트리거 노드로 등록됨.

현재 소스 코드 확인 결과:
- `confidence_evaluator.py` -- CONFLICTED 판정 수행
- `result_finalizer.py` (line 56) -- `clarification_turns` 증가 코드 존재

**문제**: 01에서 T5 트리거 노드를 "result_finalizer"라고 명시했으나, 02의 구현에서는 `confidence_evaluator`가 트리거를 발생시킴. 이것이 의도적 설계 변경이라면 01 문서 업데이트가 필요.

**등급**: WARNING (5.1과 동일 이슈이나 pipeline.py 코드 수준에서 재확인)

---

### 5.12 [INFO] 02 코드에서 신규 State 필드 미등록 가능성

**위치**: 02 Section 2.4 handlers.py

T4 `CrossDBHandler.apply_to_state()` (line 569):
```python
return {"selected_db_source": answer}
```

T5 `SchemaConflictedHandler.apply_to_state()` (line 594):
```python
return {"user_schema_selection": answer}
```

이 두 필드(`selected_db_source`, `user_schema_selection`)는 02 Section 2.3의 state.py 변경 사항에 **나열되지 않음**. 현재 소스 코드에도 존재하지 않는 필드.

Pydantic BaseModel에 정의되지 않은 필드를 dict로 반환하면 LangGraph의 state 업데이트에서 무시되거나 에러가 발생할 수 있음.

**등급**: INFO (구현 시 state.py에 필드 추가 필요)

**권장 조치**: 02 Section 2.3의 state.py 필드 목록에 `selected_db_source: str = ""` 및 `user_schema_selection: str = ""` 추가, 또는 기존 필드를 활용하는 방식으로 핸들러 수정.

---

## 6. 요약

### 불일치 통계

| 등급 | 건수 |
|------|------|
| CRITICAL | 0 |
| WARNING | 4 |
| INFO | 6 |

### WARNING 항목 요약

| # | 항목 | 위치 | 핵심 |
|---|------|------|------|
| 5.1 | T5 트리거 노드명 불일치 | 01 S2.3 표 vs 02 S2.8 T5 코드 | result_finalizer vs confidence_evaluator 혼재 |
| 5.2 | clarification_turns 처리 미정의 | 01 S2.6 + 02 S2.3 | 기존 필드의 유지/대체 방침 누락 |
| 5.3 | ClarificationResponse 미정의 | 01 S6 Phase 2 vs 02 S2.2 | 01에서 언급했으나 02에서 구현 없음 |
| 5.11 | pipeline.py T5 트리거 노드 | 02 S2.7 line 866-872 | 01 표와 02 코드의 트리거 발생 노드 상이 |

### 전체 판정

두 문서의 핵심 아키텍처 결정(D1~D8)과 v3 원칙(순수 interrupt, Unified Clarification, Structured Context, preprocess 제거)은 일관되게 반영되어 있음. 기각 대안(Shortcut, hybrid, Query Rewriting)과 v2 잔재(`clarification_origin`, `_route_after_preprocess`, preprocess 노드)도 02에 남아있지 않음.

발견된 불일치는 주로 **T5 트리거 노드의 정확한 소재(result_finalizer vs confidence_evaluator)** 와 **기존 필드(clarification_turns) 마이그레이션 방침 누락**, **ClarificationResponse 스키마 미정의** 등 세부 수준에 집중되어 있으며, 구현 착수 전 정리가 권장됨.
