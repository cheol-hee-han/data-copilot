# Checkpointer Multi-turn Phase 1~3 구현 코드 리뷰

- 리뷰 일자: 2026-03-31
- 리뷰어: Code Reviewer Agent
- 대상: Phase 1~3 핵심 파일 16개

---

## 1. 임포트 일관성 (삭제된 모듈 참조)

**OK** --- 삭제된 모듈(clarifier.py, preprocessor.py) 참조가 전체 `src/` 디렉토리에 남아있지 않음.
`synthesize_clarification`, `preprocess_node`, `preprocessor_node`, `clarify_node`, `clarifier_node` 모두 grep 결과 0건.

---

## 2. 타입 안전성

### OK --- AmbiguitySignal 필드 일관성

- `state.py:569` `pending_signals: list[AmbiguitySignal]` (덮어쓰기)
- `state.py:574` `resolved_signals: Annotated[list[AmbiguitySignal], operator.add]` (누적)
- `clarification_handler.py`가 반환하는 `{"resolved_signals": ..., "pending_signals": []}` 구조가 State 정의와 일치

### OK --- PipelineResult.clarification_request

- `response.py:49` `clarification_request: dict[str, Any] | None`
- `runner.py:182` `clarification_request=clarification_data` --- interrupt 페이로드(dict)를 그대로 전달, 타입 일치

### 🟡 Warning --- PipelineState.original_query 설정 시점 제한

- **파일**: `state.py:548`, `runner.py:142`
- **현상**: `original_query`는 `runner.py`의 새 턴(3b분기)에서만 `user_input`으로 설정됨. interrupt 재개(3a분기, `runner.py:134`)에서는 `Command(resume=sanitized.text)`만 전달하므로 `original_query`는 체크포인터에 저장된 이전 값이 유지됨.
- **판정**: 설계 의도 부합 (original_query는 첫 턴의 원본을 보존하는 필드). OK이나, docstring에 "첫 턴에서만 설정, immutable" 의미가 명시되어 있으므로 문제 없음.

---

## 3. LangGraph interrupt() 규칙 준수

### 🔴 Critical --- interrupt() 호출 순서 조건부 스킵 위험

- **파일**: `clarification_handler.py:137~141`, `clarification_handler.py:155`
- **현상**: `if not ask: return {...}` 분기에서 interrupt()를 호출하지 않고 즉시 반환함. ASK 시그널이 있을 때만 `interrupt()` 호출.
- **LangGraph 규칙**: "interrupt calls should happen in the same order every time, and you should not conditionally skip interrupt calls within a node."
- **분석**: 이 규칙은 **하나의 노드 내에 여러 interrupt()를 배치할 때** 순서가 달라지면 재개 시 잘못된 interrupt에 resume이 매핑되는 문제를 방지하기 위한 것임. `clarification_handler_node`는 interrupt()를 **최대 1회**만 호출하며, 호출 0회 또는 1회만 존재. LangGraph에서 단일 interrupt의 조건부 호출은 허용됨 (interrupt가 없으면 노드가 정상 완료되어 resume 매핑 문제가 발생하지 않음).
- **판정**: 현재 구현은 안전함. 단, 향후 interrupt를 2개 이상 추가할 경우 이 패턴이 위험해지므로, docstring에 "단일 interrupt 보장" 제약을 명시할 것을 권장.
- **수정 등급 변경**: 🟢 Info (현재 안전, 예방적 문서화 권장)

---

## 4. Dead Code

### 🟡 Warning --- session_clarify_ttl 미사용

- **파일**: `config.py:141`
- **현상**: `session_clarify_ttl: int = 300` 설정이 정의되어 있으나, checkpointer + interrupt 패턴으로 명확화 상태 관리가 이관된 후 이 설정을 참조하는 코드가 전체 `src/`에서 `config.py` 자체의 정의 1건 외에 0건.
- **조치**: Phase 3 완료 후 제거 대상. 현재는 `# Deprecated: checkpointer로 이관됨` 주석 추가 권장.

### 🟡 Warning --- 레거시 명확화 필드 잔존 (설계상 의도적)

- **파일**: `state.py:580-587`
- **현상**: `clarification_question`, `clarification_response`, `awaiting_clarification`, `clarification_turns` 필드가 "Phase 3에서 제거 예정" 주석과 함께 남아있음.
- **참조 현황**: `pipeline.py:262` `state.awaiting_clarification`, `runner.py:217-228` 결과 구성에서 참조.
- **판정**: 하위 호환을 위한 의도적 잔존. 제거 일정을 추적할 수 있도록 TODO 태그 권장: `# TODO(Phase3): 레거시 명확화 필드 제거`

### 🟢 Info --- store.py deprecated 메서드 (의도적)

- **파일**: `store.py:63-83`
- **현상**: `get_clarification`, `set_clarification` 메서드가 `warnings.warn`으로 deprecated 처리됨.
- **판정**: `store.py` 외부에서 호출하는 코드 0건 확인됨. 정상 deprecated 절차.

---

## 5. 보안

### OK --- sanitize 적용 범위

- **파일**: `runner.py:78`
- **현상**: `sanitize(user_input)`이 함수 최상단에서 1회 실행됨. 새 턴(3b)과 interrupt 재개(3a) 모두 이 지점을 거침.
- 새 턴: `sanitized.text` -> `PipelineState.preprocessed_input`
- interrupt 재개: `sanitized.text` -> `Command(resume=sanitized.text)`
- **판정**: 모든 입력 경로에 sanitize가 적용됨. OK.

### 🟡 Warning --- interrupt resume 응답에 대한 추가 검증 부재

- **파일**: `clarification_handler.py:160`
- **현상**: `validate_answer(user_answer, best)` 함수가 빈 문자열 및 선택지 매칭만 검증. sanitize는 runner.py에서 이미 적용되므로 SQL 인젝션/프롬프트 인젝션은 차단됨.
- **그러나**: `validate_answer`에서 `ValueError`를 raise하면 interrupt 재개 후 노드 실행이 예외로 중단됨. 이 예외가 runner.py에서 어떻게 처리되는지 확인 필요.
- **분석**: `runner.py:134` `await app.ainvoke(Command(resume=...))` 호출에서 노드 내 예외가 발생하면 LangGraph가 이를 전파하고, runner.py에는 이에 대한 try-except가 없음. 결과적으로 500 에러가 발생할 수 있음.
- **조치**: `validate_answer` 실패 시 ValueError를 다시 interrupt로 재질문하거나, try-except로 감싸서 안전한 폴백 처리 필요.

```python
# 권장 수정 (clarification_handler.py:159~161)
try:
    best.answer = validate_answer(user_answer, best)
except ValueError as e:
    # 재질문: 검증 실패 시 같은 interrupt를 다시 발생
    user_answer = interrupt({
        **best.model_dump(include=_INTERRUPT_FIELDS),
        "validation_error": str(e),
    })
    best.answer = validate_answer(user_answer, best)
```

---

## 6. 라우팅 정확성

### OK --- pipeline.py 조건부 엣지 매핑 일관성

모든 라우팅 함수가 반환하는 문자열이 `add_conditional_edges`의 매핑 딕셔너리에 존재하는지 검증:

| 라우팅 함수 | 반환값 | 매핑 존재 |
|---|---|---|
| `_route_after_resolve_history` | "clarification_handler", "clarify_end", "classify_intent" | OK (L376-380) |
| `_route_after_intent` | "clarification_handler", "normalize_query", "planner", "error_end" | OK (L386-392) |
| `_route_after_normalize` | "clarification_handler", "planner" | OK (L397-400) |
| `_route_after_confidence_evaluator` | "clarification_handler", "explore", "generate_sql", "replan", "conclude_failure", "ask_user" | OK (L420-428) |
| `_route_after_result_finalizer` | "execute_sql", "clarification_handler", "clarify_end", "error_end" | OK (L459-465) |
| `_route_after_clarify` | _VALID_RETURN_TARGETS + "resolve_history" | OK (L469-476) |

### 🔴 Critical --- _route_after_clarify의 복귀 대상 불완전

- **파일**: `pipeline.py:288-306`
- **현상**: `_VALID_RETURN_TARGETS`에 `"planner"`, `"result_finalizer"` 가 포함되어 있지 않음.
- **시나리오**: 만약 confidence_evaluator에서 `ask_user`로 분기 -> result_finalizer -> `state.pending_signals` 존재 -> clarification_handler 진입 -> 해소 후 복귀 시 `source_node`가 "result_finalizer"면 `_VALID_RETURN_TARGETS`에 없어 "resolve_history"로 폴백됨.
- **분석**: 현재 구현에서 `result_finalizer`가 `pending_signals`를 직접 생성하는 경로가 있다면 문제. `_route_after_result_finalizer`(L256-268)에서 `state.pending_signals`를 체크하므로, result_finalizer 노드 자체가 pending_signals를 반환할 수 있음을 전제함.
- **조치**: result_finalizer가 pending_signals를 생성한다면 `_VALID_RETURN_TARGETS`에 "result_finalizer" 추가 필요. 또는 confidence_evaluator의 "ask_user" 경로에서 pending_signals가 생성되는 경우 source_node를 "confidence_evaluator"로 설정해야 함.
- **확인 필요**: result_finalizer 노드의 구현에서 실제로 pending_signals를 반환하는지 확인 필요.

### 🟡 Warning --- _route_after_resolve_history의 clarify_end 경로

- **파일**: `pipeline.py:114-115`
- **현상**: `state.status == QueryStatus.AWAITING_CLARIFICATION` 체크가 `pending_signals` 체크 뒤에 있음. 이 경로는 레거시 명확화에서만 도달 가능한데, 새 구현에서는 UNSURE가 `pending_signals`로 처리되므로 이 분기에 도달하는 시나리오가 불명확.
- **판정**: 레거시 호환 보호용으로 존재하나, 실제로 resolve_history_node가 `status=AWAITING_CLARIFICATION`을 반환하는 경로가 없음 (UNSURE는 이제 pending_signals 반환). Phase 3 제거 대상.

---

## 7. State 리듀서

### OK --- pending_signals (덮어쓰기)

- `state.py:569` `pending_signals: list[AmbiguitySignal] = Field(default_factory=list)` --- Annotated + operator 없음 -> LangGraph 기본 동작 = 덮어쓰기. 정상.

### OK --- resolved_signals (operator.add 누적)

- `state.py:574` `resolved_signals: Annotated[list[AmbiguitySignal], operator.add]` --- operator.add로 리스트 append 누적. 정상.
- `clarification_handler.py`에서 `{"resolved_signals": infer + [best]}` 반환 시 기존 resolved_signals에 append됨.

---

## 8. 추가 발견 사항

### 🔴 Critical --- checkpointer.py: DSN에 비밀번호 평문 포함

- **파일**: `config.py:29-31`, `checkpointer.py:47`
- **현상**: `DbConnectionInfo.dsn` 프로퍼티가 `f"postgresql://{self.user}:{self.password}@..."` 형태로 DSN을 구성. 이 DSN이 `AsyncConnectionPool(conninfo=db.dsn, ...)`에 전달됨.
- **위험**: DSN 문자열이 로그에 출력되거나 예외 traceback에 노출될 경우 비밀번호 평문 유출 가능.
- **조치**: `checkpointer.py:58`의 로그에서 `host=db.host`만 출력하고 있어 현재는 안전하나, psycopg 내부 에러 메시지에 conninfo가 포함될 수 있음. `password` 파라미터를 `kwargs`로 분리하는 것을 권장.

```python
# 권장: DSN에서 password를 분리
pool = AsyncConnectionPool(
    conninfo=f"postgresql://{db.user}@{db.host}:{db.port}/{db.name}",
    min_size=config.pool_min,
    max_size=config.pool_max,
    kwargs={**connection_kwargs, "password": db.password},
)
```

### 🟡 Warning --- checkpointer.py: pool.open() 타임아웃 미설정

- **파일**: `checkpointer.py:52-53`
- **현상**: `await pool.open()` 및 `await pool.wait()` 호출에 타임아웃이 없음. DB 연결 불가 시 서버 기동이 무한 대기.
- **조치**: `pool.open(wait=True, timeout=30)` 패턴 또는 `asyncio.wait_for` 래핑 권장.

### 🟡 Warning --- runner.py: interrupt 감지 예외 무시

- **파일**: `runner.py:121-126`, `runner.py:155-165`
- **현상**: `aget_state` 호출 실패를 `except Exception: pass`로 무시. 체크포인터가 정상인데 다른 이유(네트워크 타임아웃 등)로 실패하면 interrupt 대기 상태를 놓치고 새 턴으로 진행하여 기존 대화 맥락이 유실될 수 있음.
- **조치**: 최소한 `logger.warning`으로 기록하고, 체크포인터가 활성화된 상태에서의 실패는 더 신중하게 처리할 것을 권장.

```python
except Exception as e:
    logger.warning(
        "aget_state 실패 — 새 턴으로 진행",
        error=str(e),
        session_id=session_id,
    )
```

### 🟢 Info --- .env.example에 checkpointer 관련 환경변수 미기재

- **파일**: `.env.example`
- **현상**: `CheckpointerConfig`의 `backend`, `dedicated_db`, `pool_min`, `pool_max`, `thread_ttl_days` 등에 대응하는 환경변수 예시가 `.env.example`에 없음.
- **영향**: pydantic-settings의 중첩 모델 환경변수 바인딩은 `CHECKPOINTER__BACKEND=postgres` 형태(더블 언더스코어)를 사용해야 하는데, 이 컨벤션이 문서화되지 않음.
- **조치**: `.env.example`에 주석과 함께 추가 권장.

### 🟢 Info --- clarification_handler.py: 미선택 ASK 시그널 유실

- **파일**: `clarification_handler.py:143-147`, `clarification_handler.py:164`
- **현상**: ASK 시그널이 2개 이상일 때 우선순위가 높은 1개만 선택되어 interrupt. 나머지 ASK 시그널은 `resolved_signals`에 포함되지 않고, `pending_signals`도 []로 비워짐. 즉, 선택되지 않은 ASK 시그널이 유실됨.
- **영향**: 다음 그래프 실행에서 해당 모호성이 다시 감지되지 않으면 무시됨.
- **조치**: 미선택 ASK를 `pending_signals`에 유지하거나, 별도 큐에 보관하는 방안 검토 필요. 혹은 설계 의도대로 "1턴 1질문" 원칙이면, 복귀 노드가 미해소 모호성을 재감지하는 것을 전제로 한 것인지 문서화 필요.

---

## 요약

| 등급 | 건수 | 핵심 사항 |
|------|------|-----------|
| 🔴 Critical | 2 | (1) _route_after_clarify 복귀 대상 불완전 가능성, (2) DSN 비밀번호 평문 노출 위험 |
| 🟡 Warning | 5 | (1) session_clarify_ttl 미사용, (2) 레거시 필드 TODO 태그 부재, (3) validate_answer 예외 미처리, (4) pool.open 타임아웃 미설정, (5) aget_state 예외 무시 |
| 🟢 Info | 3 | (1) .env.example 미기재, (2) 미선택 ASK 시그널 유실 문서화, (3) deprecated 메서드 정상 처리 |

**전체 평가**: 임포트 정리, State 리듀서 설계, sanitize 적용 범위, 라우팅 매핑은 일관성 있게 잘 구현되어 있음. Critical 이슈 2건은 실제 런타임 영향 여부를 확인한 뒤 Phase 3 완료 전 해소 권장.
