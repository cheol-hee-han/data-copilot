# Checkpointer Multi-Turn 구현 최종 검토 보고서

- **작성일**: 2026-03-31
- **대상**: `src/` 전체, `tests/` 레거시 참조
- **기준 문서**: `docs/strategy-proposals/checkpointer-multi-turn/01-strategy.md` (v4)
- **검토 관점**: (1) 레거시 잔여물 확인, (2) 전략 문서 충실도 검증

---

## 1. 레거시 잔여물 검색 결과

### 1.1 src/ 디렉토리 (Production Code)

| 검색 키워드 | 결과 |
|---|---|
| `from src.agents.nodes.interpret.clarifier` | 없음 |
| `from src.agents.nodes.interpret.preprocessor` | 없음 |
| `preprocess_node` | 없음 |
| `clarify_node` | 없음 |
| `synthesize_clarification` | 없음 |
| `ClarificationSynthesisResult` | 없음 |
| `clarification_state` | 없음 |

**판정**: src/ 디렉토리에 레거시 잔여물 **없음**. 삭제/이관이 깨끗하게 완료됨.

### 1.2 tests/ 디렉토리 (깨진 테스트 참조)

| 파일 | 레거시 키워드 | 심각도 |
|---|---|---|
| `tests/conftest.py:219` | `preprocess_node` (SLA 타이머 예시 주석) | Info |
| `tests/auto/unit/test_preprocessor.py` (전체) | `from src.agents.nodes.interpret.preprocessor import preprocess_node` | Critical |
| `tests/auto/unit/test_clarify_node.py` (전체) | `from src.agents.nodes.interpret.clarifier import clarify_node, _build_messages` | Critical |
| `tests/auto/e2e/test_node_chain.py:59,221` | `from src.agents.nodes.interpret.preprocessor import preprocess_node` | Critical |
| `tests/auto/e2e/test_pipeline_e2e.py:22,99,129,235,555,617` | `from src.agents.nodes.interpret.preprocessor import preprocess_node` | Critical |
| `tests/manual/e2e/test_input_to_normalization.py:44,66` | `from src.agents.nodes.interpret.preprocessor import preprocess_node` | Critical |

**총 깨진 테스트 파일 수**: 5개

#### 깨진 테스트 상세 목록

1. **test_preprocessor.py** (단위 테스트) -- 삭제된 `preprocessor.py`의 전용 테스트. 파일 전체가 `preprocess_node`를 import/호출하므로 **전체 삭제 또는 재작성 필요**.

2. **test_clarify_node.py** (단위 테스트) -- 삭제된 `clarifier.py`의 전용 테스트. `_build_messages`, `clarify_node`를 import하므로 **전체 삭제 후 `clarification_handler_node` 테스트로 대체 필요**.

3. **test_node_chain.py** (E2E) -- 2곳에서 `preprocess_node`를 import하여 파이프라인 체인 테스트. **import 제거 + 테스트 흐름을 `resolve_history_node`부터 시작하도록 수정 필요**.

4. **test_pipeline_e2e.py** (E2E) -- 6곳에서 `preprocess_node`를 사용. **가장 영향이 큰 파일**. 전처리를 `sanitize()` + `resolve_history_node`로 교체 필요.

5. **test_input_to_normalization.py** (수동 E2E) -- `preprocess_node` import 및 호출. `sanitize()` + `resolve_history_node` 체인으로 교체 필요.

6. **conftest.py:219** -- SLA 타이머 fixture 내 docstring 예시에서 `preprocess_node` 언급. 주석 수정만으로 해결 가능.

---

## 2. 전략 문서 충실도 검증

### Phase 1: Core Checkpointer

| # | 체크리스트 항목 | 상태 | 검증 내용 |
|---|---|---|---|
| 1 | `config.py`: `DbConnectionInfo` + `CheckpointerConfig` 추가 | [x] | `DbConnectionInfo` (L17-32): `host`, `port`, `name`, `user`, `password` + `dsn` property. `CheckpointerConfig` (L35-47): `backend`, `dedicated_db`, `pool_min/max`, `thread_ttl_days` + `resolve_db()`. `Settings.checkpointer` (L80) 로 통합. |
| 2 | `checkpointer.py`: 팩토리 생성 (MemorySaver / AsyncPostgresSaver) | [x] | `create_checkpointer()` async context manager. postgres 모드: `AsyncConnectionPool` + `autocommit=True` + `prepare_threshold=0` (PgBouncer 호환) + `await checkpointer.setup()`. memory 모드: `MemorySaver()`. 리소스 정리 보장(`finally: pool.close()`). |
| 3 | `pipeline.py`: checkpointer DI | [x] | `create_app(checkpointer=None)` (L496) — `workflow.compile(checkpointer=checkpointer)`. `get_compiled_app(checkpointer=None)` 싱글턴 캐시 (L512). |
| 4 | `runner.py`: thread_id config | [x] | `run_config["configurable"] = {"thread_id": session_id}` (L113-114). `aget_state(run_config)`로 interrupt 감지 (L119-123). |
| 5 | `main.py`: lifespan에 checkpointer 초기화 | [x] | `async with create_checkpointer(settings.checkpointer, settings.history_db) as checkpointer:` (L95-98). `get_compiled_app(checkpointer=checkpointer)` 호출. |

**Phase 1 판정**: 5/5 완료. 전략 문서와 완전히 일치.

---

### Phase 2A: 인프라 (스키마 + State + 노드 골격)

| # | 체크리스트 항목 | 상태 | 검증 내용 |
|---|---|---|---|
| 1 | `clarification.py`: AmbiguitySignal (7종 AmbiSQL + ASK/INFER + lifecycle) | [x] | `AmbiguityType` 7종 StrEnum (TABLE/INTENT/VALUE/FORMULA/TIMEFRAME/CONTEXT/CONFLICT). `ConfidenceLevel` 3단계. `QuestionType` 3종 (FREE_TEXT/SINGLE_SELECT/CONFIRM). `AmbiguitySignal`: `source_node`, `ambiguity_type`, `decision` Literal["ASK","INFER"], `confidence`, `question`, `options`, `inferred_value`, `reasoning`, `override_reason`, `answer`, `resolved_at`. `is_resolved`, `display_value` property. |
| 2 | `state.py`: `original_query`, `pending_signals`, `resolved_signals` 필드 | [x] | `original_query: str = ""` (L548). `pending_signals: list[AmbiguitySignal]` (L569, 일반 필드 덮어쓰기). `resolved_signals: Annotated[list[AmbiguitySignal], operator.add]` (L574, 누적 전용). |
| 3 | `clarification_handler.py`: 통합 명확화 노드 (가드레일 + interrupt) | [x] | `_should_override_to_ask()`: FORMULA 무조건 ASK, TABLE(2+ options + LOW) ASK, INTENT(LOW) ASK. `_PRIORITY` dict. `validate_answer()`. `clarification_handler_node()`: 가드레일 → ASK/INFER 분리 → INFER resolved_signals 누적 → ASK 우선순위 1개 → `interrupt()` 1회만. |
| 4 | `runner.py`: sanitize 통합 + interrupt 감지 + Command(resume=) | [x] | `sanitize(user_input)` 1회 (L78). `aget_state()` interrupt 감지 (L119-123). `Command(resume=sanitized.text)` (L134-137). ainvoke 후 interrupt 발생 확인 (L154-166). `clarification_request` 페이로드 추출. |
| 5 | `pipeline.py`: preprocess 제거 + clarification_handler 라우팅 | [x] | preprocess 노드 없음. `set_entry_point("resolve_history")` (L370). `clarification_handler` 노드 등록 (L348). `_route_after_clarify()`: `_VALID_RETURN_TARGETS` 기반 source_node 복귀. 모든 라우팅 함수에서 `pending_signals` 확인 → `clarification_handler` 분기. |

**Phase 2A 판정**: 5/5 완료.

---

### Phase 2B: Interpret 계층 (T1~T3 트리거 전환)

| # | 체크리스트 항목 | 상태 | 검증 내용 |
|---|---|---|---|
| 1 | `history_resolver.py`: UNSURE -> AmbiguitySignal 전환 | [x] | `resolve_history_node()` UNSURE 분기 (L107-132): `AmbiguitySignal(source_node="resolve_history", ambiguity_type=AmbiguityType.CONTEXT, decision="ASK", confidence=ConfidenceLevel.LOW)`. `pending_signals: [signal]` 반환. |
| 2 | `clarifier.py` 삭제 | [x] | 파일 미존재 확인 완료. |
| 3 | `preprocessor.py` 삭제 | [x] | 파일 미존재 확인 완료. |
| 4 | `input_sanitizer.py`: synthesize_clarification 제거 | [x] | 파일 내 `synthesize_clarification` 함수 없음 확인. |

**Phase 2B 판정**: 4/4 완료.

---

### Phase 2C: Reason 계층 + 결과 포맷

| # | 체크리스트 항목 | 상태 | 검증 내용 |
|---|---|---|---|
| 1 | `clarification_context.py`: build_clarification_context + build_auto_resolved_notice | [x] | `build_clarification_context(state)`: resolved_signals를 ASK/INFER 분리, `[명확화 대화]` + `[자동 추론된 조건]` 프롬프트 섹션 생성. `build_auto_resolved_notice(state)`: INFER 항목을 `조회 기준 안내:` 형식으로 사용자 안내 문자열 구성. |
| 2 | `formatter.py`: INFER 안내 상단 삽입 | [x] | `format_response_node()` L74-77: `build_auto_resolved_notice(state)` 호출 → 비어있지 않으면 `formatted` 상단에 삽입. |

**Phase 2C 판정**: 2/2 완료.

---

### Phase 3: 세션 관리 통합 (정리)

| # | 체크리스트 항목 | 상태 | 검증 내용 |
|---|---|---|---|
| 1 | `store.py`: get_clarification/set_clarification deprecated | [x] | `get_clarification()` (L62-71): `warnings.warn()` + `DeprecationWarning` + `return None`. `set_clarification()` (L74-83): `warnings.warn()` + `DeprecationWarning` + no-op. |
| 2 | `memory_store.py`: clarify 로직 제거 | [x] | 대화 이력(`_history`)만 관리. clarify 관련 dict/메서드 없음. |
| 3 | `redis_store.py`: clarify 로직 제거 | [x] | 키 구조 `session:{sid}:history`만 사용. `session:{sid}:clarify` 키 없음. clarify TTL 로직 없음. |
| 4 | `main.py`: clarification_state 파라미터 및 Redis 호출 제거 | [x] | `clarification_state`, `get_clarification`, `set_clarification` 모두 미사용 확인. |

**Phase 3 판정**: 4/4 완료.

---

## 3. 종합 판정

### 구현 충실도 요약

| Phase | 체크리스트 항목 수 | 완료 | 미완료 |
|---|---|---|---|
| Phase 1 (Core Checkpointer) | 5 | 5 | 0 |
| Phase 2A (인프라) | 5 | 5 | 0 |
| Phase 2B (Interpret 계층) | 4 | 4 | 0 |
| Phase 2C (Reason + 포맷) | 2 | 2 | 0 |
| Phase 3 (세션 정리) | 4 | 4 | 0 |
| **합계** | **20** | **20** | **0** |

**전략 문서 대비 구현 충실도: 100% (20/20)**

---

## 4. 발견 사항

### Critical (빨간색) -- 즉시 조치 필요

**C-01: 깨진 테스트 파일 5개 (삭제된 모듈 참조)**

| 파일 | 참조하는 삭제 모듈 | 조치 |
|---|---|---|
| `tests/auto/unit/test_preprocessor.py` | `preprocessor.preprocess_node` | 파일 삭제 |
| `tests/auto/unit/test_clarify_node.py` | `clarifier.clarify_node, _build_messages` | 파일 삭제, `test_clarification_handler.py` 신규 작성 |
| `tests/auto/e2e/test_node_chain.py` | `preprocessor.preprocess_node` (2곳) | import 제거 + `resolve_history_node` 시작으로 수정 |
| `tests/auto/e2e/test_pipeline_e2e.py` | `preprocessor.preprocess_node` (6곳) | `sanitize()` + `resolve_history_node` 체인으로 교체 |
| `tests/manual/e2e/test_input_to_normalization.py` | `preprocessor.preprocess_node` (2곳) | 동일 교체 |

이 테스트들은 현재 `pytest` 실행 시 `ModuleNotFoundError`로 즉시 실패한다.

### Warning (노란색) -- 개선 권장

**W-01: conftest.py SLA 타이머 docstring 레거시 참조**

`tests/conftest.py:219`의 SLA 타이머 fixture docstring 예시에서 `preprocess_node`를 언급한다. 기능 영향은 없으나 문서 정확성을 위해 `resolve_history_node`로 수정 권장.

**W-02: state.py 레거시 명확화 필드 잔존**

`state.py` L578-583에 레거시 명확화 필드 4개가 `TODO` 주석과 함께 남아 있다:
```python
# TODO: interrupt/AmbiguitySignal 패턴으로 완전 이관 후 제거
clarification_question: str = ""
clarification_response: str = ""
awaiting_clarification: bool = False
clarification_turns: int = 0
```

전략 문서에서 Phase 3 범위로 명시했으나, `clarification_handler_node`가 `awaiting_clarification: False`를 반환하고(L173), `runner.py`의 `_build_result()`가 `awaiting_clarification`과 `clarification_question`을 참조하므로(L217-228) 현 시점에서 즉시 제거는 불가. **Phase 4 또는 프론트엔드 interrupt 페이로드 직접 처리 전환 시 제거 가능**.

**W-03: pipeline.py docstring 레거시 흐름도**

`pipeline.py` L8-9의 모듈 docstring에 아직 `preprocess`가 언급되어 있다:
```
사용자 입력 -> preprocess -> resolve_history -> classify_intent -> ...
```
실제 흐름은 `resolve_history`부터 시작하므로 docstring 갱신 필요.

### Info (초록색) -- 참고 사항

**I-01: VALUE 타입 가드레일 규칙 미구현**

전략 문서 가드레일 매트릭스에서 `VALUE` 타입은 "ES 코드 매칭 실패" 시 INFER->ASK 보정으로 명시되어 있으나, `_should_override_to_ask()`에는 FORMULA/TABLE/INTENT 3가지만 구현되어 있다. 전략 문서 Phase 2D "가드레일 규칙 세분화"에서 처리 예정이므로 현 시점에서는 의도된 미구현.

**I-02: TIMEFRAME 산출식 연관 가드레일 미구현**

동일하게 Phase 2D 범위. 현 시점에서 의도된 미구현.

**I-03: `clarification_handler_node` 나머지 ASK 시그널 처리**

전략 문서에서 "복수 ASK 시그널 중 1개만 선택, 나머지는 source_node 복귀 후 재수집"으로 명시. 현재 구현에서 미선택된 ASK 시그널은 `pending_signals: []`로 비워지므로 사라진다. 이는 source_node 복귀 시 해당 노드가 재실행되면서 모호성을 재감지하는 설계에 의존한다. 노드가 재실행 시 이전과 동일한 모호성을 감지하지 못할 가능성이 있으나, Phase 2D 안정화에서 검증 예정.

---

## 5. 최종 결론

Production 코드(`src/`)는 전략 문서 Phase 1~3의 20개 체크리스트 항목을 모두 충족하며, 삭제/이관된 코드의 잔여 참조가 없다.

**즉시 조치가 필요한 사항은 테스트 코드(`tests/`) 5개 파일의 레거시 import 수정**이다. 이 파일들은 `pytest` 실행 시 `ModuleNotFoundError`를 발생시켜 CI 파이프라인을 차단한다.
