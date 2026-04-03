# 설계 검토 보고서: Checkpointer 도입 및 멀티턴 상호작용

- **검토 대상**: `docs/strategy-proposals/checkpointer-multi-turn/01-strategy.md`, `02-detailed-design.md`
- **검토일**: 2026-03-31
- **검토 관점**: 가정 검증 → 실패 시나리오 → 대안 비교 → 시간축 리스크
- **현재 코드 기준**: `main` 브랜치 (`6491f9b`)

---

## 총평

전체적으로 학술 근거와 프로덕션 사례를 바탕으로 한 탄탄한 설계. 특히 2계층 판정(LLM+가드레일), Structured Context Passing, AmbiSQL 7종 분류 체계는 금융 도메인에 적합한 선택. 그러나 **현재 코드와의 간극**, **런타임 엣지 케이스**, **구현 복잡도 대비 실익** 측면에서 아래 이슈들이 구현 전 해소 필요.

**심각도 분포**: P0 3건 / P1 6건 / P2 6건 / P3 3건

---

## [P0] 치명적 — 구현 전 반드시 해소

### P0-1. `clarification_handler` 노드의 조건부 interrupt 위반

**문제**: `clarification_handler_node`는 `if ask_signals:` 조건에서만 `interrupt()`를 호출한다 (02-detailed-design.md 960행). LangGraph 공식 문서는 "interrupt calls should happen in the same order every time, and you should not conditionally skip interrupt calls within a node"라고 명시.

설계 문서 자체(01-strategy.md 224~228행)에서 이 규칙을 인지하고 "항상 1회만 호출"이라고 기술했지만, **실제 코드에서는 `if ask_signals:` 분기 안에 있어 호출 자체를 조건부로 스킵**한다. "1회만 호출"과 "조건부 스킵 금지"는 다른 규칙이다.

**근거**: LangGraph 인터럽트 인덱싱은 노드 진입 시마다 0번부터 카운트. 첫 진입에서 interrupt 호출(인덱스 0), resume 후 재진입에서 인덱스 0의 interrupt를 찾아 스킵하고 이후 코드를 실행한다. 조건 분기가 바뀌면 인덱스 불일치로 예측 불가 동작 발생.

**위험 시나리오**: 첫 진입 시 uncertainty_signals에 ASK 1개 + INFER 2개가 있어 interrupt 호출됨. resume 후 재진입 시 handler가 상태를 업데이트하고 uncertainty_signals를 비움. 그런데 재진입 시 signals가 비어있어 `if not signals: return` (938행)으로 즉시 반환 → interrupt 인덱스 0이 실행되지 않음 → **인덱스 불일치 위험**.

**대안**:
```
옵션 A (권장): clarification_handler를 2개 노드로 분리
  - clarify_evaluate: 가드레일 적용 + ASK/INFER 분리 + auto_resolved 기록
    → ASK 있으면 pending_clarification 세팅 후 clarify_ask로 라우팅
    → ASK 없으면 return_to 또는 다음 노드로 라우팅
  - clarify_ask: 항상 interrupt() 호출 (무조건 실행, 조건부 스킵 없음)
    → resume 후 handler 검증 + ClarificationEntry 누적

옵션 B: 항상 interrupt 호출 + sentinel 패턴
  - interrupt(payload) 무조건 호출, payload가 None이면 "skip" 취급
  - 하지만 사용자에게 불필요한 대기가 발생하므로 비현실적
```

**결론**: 옵션 A를 적용해야 LangGraph 런타임 안전성을 보장할 수 있다. 그래프 복잡도가 미미하게 증가하지만 인덱스 규칙 위반의 치명적 리스크를 제거한다.

---

### P0-2. return_to 복귀 시 무한 루프 가능성

**문제**: clarification_handler(또는 분리된 clarify_ask) 해소 후 `return_to` 노드로 복귀한다. 복귀된 노드(예: `normalize_query`)가 재실행되면서 **동일한 모호성을 다시 감지**하여 uncertainty_signal을 재생성하고, 다시 clarification_handler로 진입하는 무한 루프 가능성.

**설계 문서의 완화 시도**: handler.apply_to_state()가 "재트리거 방지를 위해 상태를 충분히 업데이트"해야 한다고 기술 (02-detailed-design.md 600행). 그러나 이것은 **희망 사항이지 보장이 아니다**.

**구체적 실패 경로**:
1. normalize_query가 INTENT 모호성 감지 → ASK → interrupt → 사용자 "2번" 답변
2. FreeTextHandler.apply_to_state()가 `preprocessed_input = "2번"` 세팅
3. normalize_query 재실행 → LLM이 "2번"만으로는 원래 질의 맥락 불충분 → 또 INTENT 모호성 감지
4. 무한 루프 (clarification_turns가 max에 도달할 때까지)

**근거**: 현재 코드의 `_route_after_normalize`는 `clarification_turns < CLARIFICATION_MAX_TURNS` 가드가 있어 최소한 max_turns에서 멈추지만, **3회 동일 질문 반복은 사용자 경험 파괴**.

**대안**:
```
1. (필수) 루프 가드 추가:
   - clarification_handler가 ClarificationEntry를 누적할 때 (ambiguity_type, source_node) 쌍의
     연속 반복 횟수를 추적
   - 동일 쌍 2회 반복 시 INFER로 강제 전환 (가드레일 역방향이지만 루프 탈출 목적)

2. (필수) apply_to_state의 재트리거 방지를 구조적으로 보장:
   - 복귀 노드가 clarifications 리스트를 확인하여,
     동일 ambiguity_type의 해소 이력이 있으면 해당 모호성을 스킵하는 로직을 노드 레벨에 삽입
   - handler.apply_to_state()만으로는 LLM의 재감지를 구조적으로 차단할 수 없음

3. (권장) Structured Context 전달 시 clarification 응답을 노드 프롬프트에 직접 주입:
   - 복귀 노드의 LLM 프롬프트에 "[이전 명확화: Q={question}, A={answer}]" 섹션을 추가
   - LLM이 이미 해소된 모호성을 인지하게 하여 재감지 확률을 낮춤
```

---

### P0-3. Reason 계층 복귀 시 탐색 상태 불일치

**문제**: confidence_evaluator(T5) 또는 sql_generator(T4)에서 interrupt 발생 후 resume → return_to로 복귀할 때, **Reason 계층의 ReasoningState가 중간 상태**에 있다.

현재 코드의 Reason 계층은 복잡한 상태 머신이다:
- `reason.phase`: PLANNING → exploration 순환 → DONE
- `reason.loop_guard`: total_tool_calls, replan_count, generate_attempts 카운터
- `reason.knowledge_items`: CONFIRMED / CONFLICTED / UNVERIFIED 상태 관리
- `reason.hypotheses`: ACTIVE / CONFIRMED / REJECTED 상태 관리

confidence_evaluator에서 CONFLICTED로 interrupt된 후, 사용자가 "1번"으로 답변하면:
1. clarification_handler가 SchemaConflictHandler로 `user_schema_selection` 세팅
2. return_to="confidence_evaluator"로 복귀
3. **confidence_evaluator가 재실행** — 이때 기존 knowledge_items의 CONFLICTED 항목이 여전히 CONFLICTED인가? 사용자 선택이 반영되어 CONFIRMED로 바뀌었는가?

**설계 문서의 빈틈**: handler.apply_to_state()가 `{"user_schema_selection": answer}`만 반환. 하지만 confidence_evaluator가 재실행될 때 이 필드를 읽어서 CONFLICTED → CONFIRMED로 전환하는 로직이 **어디에도 정의되지 않았다**.

**대안**:
```
1. (필수) SchemaConflictHandler.apply_to_state()에서 ReasoningState까지 업데이트:
   - CONFLICTED 항목의 status를 CONFIRMED로 전환
   - 선택된 테이블/스키마 정보를 knowledge_items에 반영
   - confidence_evaluator가 재실행 시 CONFLICTED가 없으므로 GENERATE로 진행

2. (필수) T4 Cross-DB 핸들러도 동일:
   - selected_db_source 세팅뿐 아니라
   - reason.candidate_tables에서 미선택 DB의 테이블을 제거하거나 태깅
   - sql_generator 재실행 시 선택된 DB만 대상으로 생성

3. (검토 시점에서 결정) return_to 복귀 시 노드의 "재실행 모드" 정의:
   - 노드가 처음부터 재실행하는지, 아니면 clarification 응답만 반영하는지 명시 필요
   - 처음부터 재실행하면 LLM 호출 비용 발생 + 상태 불일치 위험
   - clarification 응답만 반영하면 노드 로직 분기가 필요
```

---

## [P1] 중대 — 구현 초기에 해소 권장

### P1-1. 보안 검증 이중화 해소의 실제 코드와 불일치

**문제**: 설계 문서는 "main.py의 `detect_prompt_injection()` + preprocess_node의 `sanitize()` → 2회 중복"이라고 기술. 그러나 현재 코드 확인 결과:

- `main.py:327` — WebSocket 핸들러에서 `detect_prompt_injection(data)` 호출 (1회차)
- `main.py:377` — REST 핸들러에서 `detect_prompt_injection(user_input)` 호출 (1회차)
- `preprocess_node` → `sanitize()` → 내부에서 `detect_prompt_injection()` 호출 (2회차)

설계대로 preprocess 노드를 제거하고 runner.py에서 sanitize()를 실행하면, **main.py의 detect_prompt_injection()은 여전히 남아있다**. 결과적으로 main.py(1회) + runner.py sanitize(2회) = **여전히 2회 중복**.

**대안**:
```
- main.py의 detect_prompt_injection() 호출도 함께 제거
- runner.py의 sanitize()가 유일한 보안 검증 진입점으로 확정
- 또는 main.py의 것은 빠른 거부(early reject)용으로 유지하되,
  "의도적 2계층 방어"로 재정의 (현재 문서처럼 "이중화 해소"라 부르지 않음)
```

### P1-2. `_route_after_trigger` 공통 라우팅 함수의 비현실성

**문제**: 설계 문서(02-detailed-design.md 1160~1192행)에서 5개 트리거 노드에 동일한 `_route_after_trigger` 함수를 conditional edge로 적용.

현재 코드에서 각 노드 후속 라우팅은 **완전히 다르다**:
- `resolve_history` → classify_intent 또는 clarify_end
- `classify_intent` → clarify / normalize_query / planner / error_end (4갈래)
- `normalize_query` → clarify / planner (2갈래)
- `sql_generator` → sql_validator (무조건)
- `confidence_evaluator` → explore / generate_sql / replan / conclude_failure / ask_user (5갈래)

하나의 `_route_after_trigger`가 "uncertainty_signals 있으면 clarification_handler, 없으면 기존 라우팅"을 처리하려면, **기존 라우팅 로직 전체를 이 함수 안에 복제**해야 한다. `_default_next(state)` 같은 마법 함수로는 해결 불가.

**대안**:
```
(권장) 노드별 개별 라우팅 함수 유지 + uncertainty_signals 검사 삽입:

def _route_after_confidence_evaluator(state: PipelineState) -> str:
    if state.uncertainty_signals:
        return "clarification_handler"  # 또는 "clarify_evaluate" (P0-1 반영)
    return evaluate_readiness(state.reason).value

def _route_after_normalize(state: PipelineState) -> str:
    if state.uncertainty_signals:
        return "clarification_handler"
    return "planner"

# 각 기존 라우팅 함수에 2줄 추가로 해결.
# _route_after_trigger 추상화는 불필요한 복잡도.
```

### P1-3. PipelineState에 BaseModel 유지 시 체크포인터 호환 미검증

**문제**: 설계 문서(01-strategy.md D4)에서 "15개 노드 + 30개 서브타입이 Pydantic 기반이므로 TypedDict 전환 비용 과다"로 BaseModel 유지 결정. 그러나 LangGraph의 `StateGraph`는 기본적으로 **TypedDict 또는 dataclass 기반 State**를 전제하며, Pydantic BaseModel 사용 시 다음 이슈가 발생할 수 있다:

1. **Reducer 미적용**: `list[UncertaintySignal]` 같은 리스트 필드에 노드가 `return {"uncertainty_signals": [...]}` 하면, LangGraph의 기본 동작은 **전체 교체(overwrite)**. `operator.add` reducer 없이는 append 불가. 설계 문서의 모든 노드 코드가 `list(state.uncertainty_signals) + [signal]`로 수동 append하는데, **이 패턴을 하나라도 빠뜨리면 기존 시그널이 소실된다**.

2. **Pydantic + Annotated reducer**: LangGraph가 Pydantic v2 State에서 `Annotated[list[X], operator.add]`를 지원하는지 검증 필요. 지원하면 수동 append를 제거하고 reducer로 안전하게 전환 가능.

**대안**:
```
1. (Phase 1 테스트에서 검증) Pydantic BaseModel + Annotated reducer 동작 확인
   - 동작하면: uncertainty_signals, clarifications, auto_resolved에 reducer 적용
   - 동작하지 않으면: 해당 필드만 TypedDict 서브그래프로 분리하거나
     수동 append 패턴을 철저히 유지 + 리뷰 체크리스트에 추가

2. (Phase 1 필수 추가) 직렬화 검증 시 Pydantic v2 + Annotated + reducer 조합 테스트
```

### P1-4. uncertainty_signals 누적 타이밍과 다중 노드 시그널 충돌

**문제**: 설계에서 각 노드가 `uncertainty_signals`에 시그널을 추가하고, clarification_handler가 이를 일괄 처리한다. 그런데 **그래프 실행 순서상 여러 노드가 순차 실행**되면서 시그널이 누적될 수 있다.

예: resolve_history(T1 ASK) → classify_intent(T2 ASK) → normalize_query(T3 ASK) 순서로 실행되면, normalize_query 완료 시점에 3개 ASK 시그널이 있다. `_route_after_normalize`가 clarification_handler로 보내면, T1 시그널(resolve_history 기원)에 대해 interrupt가 발생하고 return_to="resolve_history"로 복귀한다.

**하지만**: 이미 classify_intent, normalize_query를 통과한 상태에서 resolve_history로 돌아가면, **이미 완료된 노드들의 결과가 state에 남아있다**. resolve_history 재실행이 이 상태를 어떻게 취급하는지 정의 없음.

**실제로는**: 현재 그래프 구조에서 resolve_history → classify_intent → normalize_query는 순차적이고, 각 라우팅 함수에서 uncertainty_signals를 검사하므로 **T1이 발생하면 즉시 clarification_handler로 가고, T2/T3는 아직 실행되지 않는다**. 그래서 이 시나리오는 발생하지 않을 수 있다.

**하지만 Reason 계층에서는 다르다**: confidence_evaluator가 T5 시그널을 생성하면서 동시에 다른 판단도 할 수 있다. 또한 **INFER 시그널은 누적되고 ASK만 interrupt 트리거**하므로, 여러 노드의 INFER + ASK 혼합이 clarification_handler에 도착할 수 있다.

**대안**:
```
1. (설계 명확화) 시그널 생성-처리 타이밍 규칙 명시:
   - 규칙: 각 라우팅 함수에서 uncertainty_signals 검사 → 즉시 clarification_handler 진입
   - 따라서 한 번에 1개 노드의 시그널만 처리됨 (다중 노드 시그널 혼합 불가)
   - 이 규칙을 문서에 명시하고, clarification_handler 진입 시 assertion으로 검증

2. (방어적) clarification_handler에서 source_node가 혼재된 경우 warning 로그 +
   source_node별 그룹핑 → 가장 상위 계층 시그널만 우선 처리
```

### P1-5. `FreeTextHandler.apply_to_state()`가 preprocessed_input을 덮어씀

**문제**: FreeTextHandler (INTENT, CONTEXT, TIMEFRAME 용)의 apply_to_state가 `{"preprocessed_input": answer}`를 반환 (02-detailed-design.md 622행). 이는 **원본 질의의 sanitized 버전을 사용자의 명확화 응답으로 완전히 교체**한다.

예: 원본 "여신 데이터 뽑아줘" → sanitize → preprocessed_input="여신 데이터 뽑아줘"
→ INTENT 명확화 → 사용자 "신규 여신 실행 금액이요"
→ apply_to_state: preprocessed_input="신규 여신 실행 금액이요"

이제 normalize_query로 복귀 시 preprocessed_input에 원본 질의가 없고, 명확화 응답만 있다. `original_query`는 보존되지만, **normalize_query가 preprocessed_input을 읽는다면 원본 맥락이 소실**된다.

Structured Context Passing(01-strategy.md 259~289행)에서 원본 + Q&A를 분리 전달한다고 했지만, 이것은 SQL 생성 노드 전용이고, **중간 노드(normalize_query)는 preprocessed_input을 직접 참조**한다.

**대안**:
```
1. (권장) FreeTextHandler.apply_to_state()가 preprocessed_input을 덮어쓰지 않음
   - 대신 clarifications 리스트에 Q&A를 누적하는 것으로 충분 (이미 clarification_handler가 함)
   - 복귀 노드가 state.clarifications[-1].answer를 참조하여 모호성 해소

2. (대안) preprocessed_input 교체 시 원본과 합성:
   - preprocessed_input = f"{state.original_query} [추가 조건: {answer}]"
   - 하지만 이것은 Query Rewriting과 동일한 문제를 야기하므로 기각 대상

3. (필수 조치와 무관하게) 각 노드가 Structured Context를 읽는 방식을 통일:
   - preprocessed_input: 최초 sanitized 입력 (불변으로 전환)
   - 모호성 해소 정보는 clarifications에서 읽도록 노드별 프롬프트 수정
```

### P1-6. conversation_history 이중 소스의 동기화 누락

**문제**: 설계 문서(02-detailed-design.md 1531~1547행)에서 Checkpointer(정본) + SessionStore(경량 인덱스) 이중 소스 전략을 제시. "동기화: main.py에서 append_history 유지"라고만 기술.

하지만 **interrupt 사이클 동안** main.py는 PipelineResult에서 awaiting_clarification=True를 받고 clarification_request를 프론트엔드에 전달한다. 이때 conversation_history에:
- 사용자 원본 질의를 append 해야 하는가?
- 시스템의 명확화 질문을 append 해야 하는가?
- 사용자의 명확화 응답을 append 해야 하는가?

현재 코드(main.py:274)는 `not pipeline_result.awaiting_clarification`일 때만 history에 append한다. 즉 **명확화 중에는 대화 이력에 기록되지 않는다**.

interrupt 기반으로 전환 시, 체크포인터가 그래프 상태를 보존하므로 conversation_history의 중요도가 낮아지지만, **UI 사이드바에서 대화 흐름이 끊겨 보이는 문제**가 발생.

**대안**:
```
1. (권장) 명확화 중에도 SessionStore에 대화 이력 기록:
   - 질문 메시지: {"role": "assistant", "content": clarification_question}
   - 응답 메시지: {"role": "user", "content": user_answer}
   - 다음 턴에서 conversation_history에 포함되어 resolve_history에 맥락 제공

2. (문서 보완) Phase 3의 세션 관리 통합 시 이 동기화 규칙을 명시
```

---

## [P2] 개선 필요

### P2-1. HandlerRegistry의 클래스 변수 공유 문제

`_handlers`가 클래스 변수(dict)로 선언되어 있어 모든 인스턴스/호출이 같은 dict을 공유한다. `register()` 메서드로 런타임 핸들러 변경 시 **전역 상태 오염** 가능.

**대안**: `_handlers`를 `ClassVar`로 명시하고 immutable 패턴(frozendict 또는 등록 시 deep copy) 적용. 또는 register()를 제거하고 __init_subclass__나 데코레이터 패턴으로 전환.

### P2-2. QueryContext의 build 로직이 스텁

`build_query_context(state)` 내부의 `_check_code_match(state)`, `_check_calculation(state)`가 정의되지 않았다. 가드레일 규칙 중 VALUE (코드 매칭 실패)와 TIMEFRAME (산출식 연관) 보정이 **이 스텁이 완성되어야 동작**한다.

**대안**: Phase 2A 체크리스트에 이 두 함수의 구현을 명시적으로 추가. ES 메타 조회 결과를 state에 저장하는 필드(또는 reason.knowledge_items에서 추출하는 로직)를 설계에 포함.

### P2-3. FormulaHandler와 SingleSelectHandler의 중복

FormulaHandler의 validate/apply_to_state 로직이 SingleSelectHandler와 거의 동일하다 (선택지 검증 → 번호/텍스트 매칭). "금융 규제 리스크가 높으므로 별도 검증 로직"이라고 주석이 있지만, 실제로 별도 검증은 없다.

**대안**: 현재는 중복을 수용하되, 실제 FORMULA 전용 검증(산출식 형식 확인, 매뉴얼 출처 교차 검증 등)이 추가될 때까지 SingleSelectHandler를 재사용하고, 가드레일에서 FORMULA는 무조건 ASK로 보정하는 현재 설계로 충분.

### P2-4. AuditEntry 활용 경로 미정의

AuditEntry 스키마는 정의되었지만, **어디서 생성하고 어디에 저장하는지** 경로가 없다. Phase 2B TODO로 분류되어 있지만, 금융 감사 추적이 프로젝트 핵심 요건이므로 구현 시점을 명확히 해야 한다.

**대안**: Phase 2A에서 AuditEntry 생성 훅(clarification_handler + response_formatter에서 호출)만 추가하고, 저장은 기존 trace_log에 AuditEntry를 포함하는 방식으로 시작. Phase 4에서 별도 audit 테이블로 분리.

### P2-5. Thread TTL 정리의 SQL 쿼리가 AsyncPostgresSaver 내부 스키마에 의존

`cleanup_expired_threads()` (02-detailed-design.md 1588~1604행)가 `checkpoints` 테이블을 직접 쿼리한다. AsyncPostgresSaver의 내부 스키마는 라이브러리 버전에 따라 변경될 수 있다. 또한 `created_at` 컬럼의 존재를 가정하지만, LangGraph의 checkpoint 테이블에 이 컬럼이 있는지 확인 필요.

**대안**: LangGraph가 제공하는 공식 API (`alist`, `adelete_thread` 등)만 사용하여 TTL 관리. 직접 SQL 대신 `alist(filter={"before": cutoff})` 같은 패턴 조사.

### P2-6. EncryptedSerializer의 성능 영향 미평가

체크포인트마다 Fernet 암호화/복호화가 실행된다. PipelineState에 sql_result (대량 행 데이터)가 포함되면 직렬화 크기가 수 MB에 달할 수 있고, 암호화 오버헤드가 latency에 영향.

**대안**: 민감 필드만 선택적 암호화하거나, PostgreSQL의 TDE(Transparent Data Encryption) 또는 pgcrypto를 사용하여 DB 레벨에서 처리. 애플리케이션 레벨 암호화는 키 관리 부담도 추가됨.

---

## [P3] 제안

### P3-1. sql_generator의 Cross-DB 명확화(T4)에서 AmbiguityType.TABLE 사용이 의미 불일치

Cross-DB 분기는 "어느 DB를 조회할지"이지 "어느 테이블을 선택할지"가 아니다. AmbiguityType에 `DATABASE` 유형이 없어서 TABLE로 대체했지만, 핸들러 라우팅 시 TABLE(진짜 테이블 모호) vs TABLE(Cross-DB) 구분이 필요.

**대안**: AmbiguityType에 `DATABASE` 추가를 검토하거나, UncertaintySignal의 source_node로 구분하는 규칙을 핸들러에 명시.

### P3-2. 도메인 기본값의 관리 주체 불명확

`resources/domain_defaults.yaml`은 git 관리 + 수동 갱신으로 기술. 그러나 금융 도메인에서 "여신 실적 = 실행 금액 (85%)"같은 통계는 변할 수 있다. 누가 언제 이 yaml을 업데이트하는지 운영 프로세스가 없으면 실제로는 초기값이 영원히 사용된다.

**대안**: 과거 SQL 이력에서 주기적으로 기본값을 자동 갱신하는 배치를 Phase 4에 추가. 또는 yaml 대신 DB 테이블로 관리하여 운영팀이 UI로 수정 가능하게 전환.

### P3-3. 프론트엔드 변경 범위의 과소평가

설계 문서에서 "프론트엔드: 변경 필요 — question_type으로 UI 자동 렌더링"이라고 한 줄로 기술. 실제로는:
- WebSocket 메시지 프로토콜 변경 (기존 clarification_question → ClarificationRequest 구조)
- 선택지 UI (SINGLE_SELECT), 확인 UI (CONFIRM) 신규 컴포넌트
- auto_resolved 안내 영역 신규
- 기존 명확화 처리 로직 전면 교체

**대안**: Phase 2A 체크리스트에 프론트엔드 변경 항목을 구체적으로 추가하고, WebSocket 메시지 스키마 변경을 별도 마이그레이션 가이드로 문서화.

---

## 검토 요약 매트릭스

| ID | 심각도 | 카테고리 | 핵심 키워드 | 상태 |
|----|--------|----------|-------------|------|
| P0-1 | 치명적 | 런타임 안전성 | 조건부 interrupt 인덱스 위반 | 해소 필수 |
| P0-2 | 치명적 | 런타임 안전성 | return_to 무한 루프 | 해소 필수 |
| P0-3 | 치명적 | 상태 일관성 | Reason 계층 복귀 시 상태 불일치 | 해소 필수 |
| P1-1 | 중대 | 보안 | 보안 검증 이중화 해소 불완전 | 코드와 문서 정합성 |
| P1-2 | 중대 | 구현 복잡도 | 공통 라우팅 함수 비현실적 | 설계 수정 |
| P1-3 | 중대 | 직렬화 | Pydantic + reducer 미검증 | Phase 1 테스트 |
| P1-4 | 중대 | 상태 관리 | 다중 노드 시그널 타이밍 | 설계 명확화 |
| P1-5 | 중대 | 데이터 보존 | preprocessed_input 덮어쓰기 | 설계 수정 |
| P1-6 | 중대 | 세션 관리 | 명확화 중 대화 이력 누락 | 동기화 규칙 명시 |
| P2-1 | 개선 | 코드 안전성 | HandlerRegistry 전역 상태 | 코드 패턴 개선 |
| P2-2 | 개선 | 구현 완성도 | QueryContext 스텁 미구현 | 체크리스트 추가 |
| P2-3 | 개선 | 코드 중복 | FormulaHandler 중복 | 수용 가능 |
| P2-4 | 개선 | 감사 추적 | AuditEntry 저장 경로 미정의 | 구현 시점 명확화 |
| P2-5 | 개선 | 유지보수 | TTL 쿼리의 내부 스키마 의존 | 공식 API 사용 |
| P2-6 | 개선 | 성능 | EncryptedSerializer 오버헤드 | DB레벨 암호화 검토 |
| P3-1 | 제안 | 설계 정합성 | Cross-DB의 TABLE 유형 사용 | 유형 추가 검토 |
| P3-2 | 제안 | 운영 | 도메인 기본값 갱신 프로세스 | 배치 자동화 검토 |
| P3-3 | 제안 | 범위 | 프론트엔드 변경 과소평가 | 체크리스트 보완 |

---

## 권장 조치 순서

1. **P0-1 해소**: clarification_handler를 clarify_evaluate + clarify_ask 2노드로 분리
2. **P0-2 해소**: 루프 가드 + 복귀 노드의 clarifications 참조 로직 설계
3. **P0-3 해소**: 각 handler.apply_to_state()가 ReasoningState까지 업데이트하도록 재설계
4. **P1-3 검증**: Phase 1 직렬화 테스트에 Pydantic + Annotated reducer 조합 추가
5. **P1-2, P1-5 반영**: 설계 문서 수정 (라우팅 함수 개별 유지, preprocessed_input 불변화)
6. **P1-1, P1-4, P1-6**: 설계 문서 보완 (보안 경로 명확화, 시그널 타이밍 규칙, 이력 동기화)
7. **P2~P3**: Phase별 체크리스트에 해당 항목 추가
