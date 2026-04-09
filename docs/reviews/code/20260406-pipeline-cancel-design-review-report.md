# Pipeline Cancel 구현 상세 설계 검토 보고서

> 검토일: 2026-04-06
> 검토 대상: `docs/todo/20260404-pipeline-cancel-design.md`
> 참조 코드: `src/agents/graph/pipeline.py`, `src/agents/graph/runner.py`, `src/services/session/store.py`, `src/agents/state/state.py`, `src/models/enums.py`

---

## 1. 정합성 (Consistency)

### 판정: 경미한 불일치 2건

**[1-1] check_cancel 시그니처 일관성** -- PASS

`check_cancel(session_id, turn_id)` 시그니처가 5.3절 정의, 5.5절 노드 호출부(`state.session_id, state.turn_id`), 5.9절 보완 모두 일관적. `PipelineState.turn_id: str = ""`가 이미 존재하며, runner에서 `turn_id=str(uuid.uuid4())`로 초기화. 전달 경로 완전.

**[1-2] make_cancel_updates 반환값과 라우팅 호환성** -- PASS

반환값 `{reason, status: CANCELLED, error_message}`가 방식 A 라우팅과 정합:
- `_route_after_readiness_gate`: `status == CANCELLED` -> `conclude_failure` -> `result_finalizer` (엣지맵 존재)
- `_route_after_result_finalizer`: `status == CANCELLED` -> `error_end` (엣지맵 존재)

**[1-3] pop_cancel / clear_cancel / is_cancelled 역할 분담** -- PASS (단, clear_cancel 사용처 부재)

- `is_cancelled`: 노드/라우팅 체크 (읽기 전용)
- `pop_cancel`: runner에서 원자적 읽기+삭제 (interrupt cancel 판단)
- `clear_cancel`: cancel.py 래퍼에 정의되어 있으나 **설계 문서 내 호출 지점 없음**. `pop_cancel`이 실질적으로 대체. 죽은 코드가 될 가능성. 향후 필요 시 사용할 여지가 있으므로 Info 수준.

**[1-4] check_cancel 두 버전 공존 (5.3절 vs 5.9절)** -- 🟡 Warning

5.3절에서 `check_cancel`을 정의한 후, 5.9절에서 `"*"` 와일드카드 폴백을 추가한 보완 버전을 별도 제시. **최종 버전이 5.9절인지 명시되지 않아** 구현 시 혼동 가능. 5.3절 코드를 5.9절 보완 버전으로 교체하는 형태로 통합 권장.

**[1-5] REST 엔드포인트 turn_id_param 미정의** -- 🔴 Critical

5.9절 `cancel_pipeline()` 함수에서 `turn_id = turn_id_param or "*"`를 사용하지만, `turn_id_param`이 함수 시그니처에 선언되지 않음. `Query` 파라미터로 선언해야 함:

```python
async def cancel_pipeline(
    session_id: str,
    turn_id: str = Query("*", alias="turn_id"),
):
```

---

## 2. 경로 검증 (CANCELLED -> END 도달)

### 판정: PASS (보완안 반영 전제)

**[2-1] 7.1 Reason 계층 경로** -- PASS

`context_retriever(B) -> context_interpreter -> readiness_gate(B) -> _route_after_readiness_gate(A): CANCELLED -> conclude_failure -> result_finalizer: CANCELLED -> _route_after_result_finalizer: CANCELLED -> error_end -> END`

실제 pipeline.py 엣지맵과 대조:
- `readiness_gate` -> `conclude_failure` -> `result_finalizer` (462-472행, 존재)
- `result_finalizer` -> `error_end` (508-516행, 존재)
- `error_end` -> `END` (542행, 존재)

**[2-2] 7.2 execute_sql 경로 -- 보완안 미적용 시 버그** -- 🟡 Warning

7.2절에서 스스로 식별한 문제: `execute_sql`에서 cancel 시 `QueryStatus.CANCELLED`를 설정하지만, 현재 `_route_after_execution`은 `ERROR`만 체크 -> `DATA_ANALYSIS` intent 시 `analyze_data`로 빠져 불필요한 LLM 호출 발생.

보완안(`_route_after_execution`에 CANCELLED 체크 + `_handle_error`에 CANCELLED 분기)이 제시되어 있으나, 이것이 **4.1 파일 목록에서 "라우팅 함수 3개"로 기술**되어 있어 불일치. 10절 구현순서 9번에서는 "라우팅 함수 4개"로 정정되었으나, **4.1절은 미갱신 상태**.

**[2-3] context_interpreter 통과 안전성** -- PASS

7.1절에서 `context_retriever`가 CANCELLED 반환 후 `context_interpreter`를 통과한다고 기술. `context_interpreter`는 `add_edge`(무조건 엣지)로 `readiness_gate`에 연결되며, `context_interpreter` 내부에서 `status` 체크를 하지 않아도 `readiness_gate`에서 재감지. 다만 **`context_interpreter`에서 불필요한 LLM 호출이 1회 발생**할 수 있음. 설계 문서에서 이를 인지하고 있으나 방어 체크를 추가하지 않은 것은 의도적 판단(노드 수 최소화)으로 보임.

---

## 3. 누락 (Missing Items)

### 판정: 3건 누락

**[3-1] conftest.py에 reset_cancel_store fixture** -- 🟡 Warning

cancel.py에 `reset_cancel_store()`를 제공(5.3절)하고, 10절 Phase 5에서 테스트를 계획하지만, `tests/conftest.py`에 `reset_cancel_store` fixture 추가가 **변경 파일 목록(4.1)에 없음**. 기존 테스트에서 cancel 싱글턴이 오염되어 테스트 간 격리 실패 가능.

**[3-2] turn_text_store.py에 cancel 턴 저장** -- 🟡 Warning

runner.py의 정상/에러 경로에서 `save_turn()`으로 턴을 저장하고 있으나, **CANCELLED 경로에서의 턴 저장 처리가 설계에 없음**. CANCELLED 결과도 `_build_result()` 경로를 타므로 기존 턴 저장 로직으로 커버될 수 있지만, `turn_type="cancelled"`같은 구분이 필요한지 명시적 판단이 빠져 있음.

**[3-3] _build_result()에서 cancelled 플래그 설정** -- 🟡 Warning

5.11절에서 `PipelineResult.cancelled` 필드 추가 및 `_build_result`에서의 설정을 제안하지만, 이것이 4.1 파일 목록에 `src/agents/models/response.py` 변경으로 반영되지 않음. 10절 구현순서 Phase 3의 13번 항목에서 언급되지만, 4.1절과 불일치.

---

## 4. 일관성 (Naming, Error Handling, Import Pattern)

### 판정: PASS (경미 1건)

**[4-1] 명명 규칙** -- PASS

`cancel_store.py` / `cancel.py` 네이밍이 기존 패턴(`session/store.py`, 유틸은 `graph/` 하위)과 일치. `CancelStore` 프로토콜, `MemoryCancelStore`, `RedisCancelStore` 네이밍이 `SessionStore`, `MemorySessionStore`, `RedisSessionStore`와 동일 패턴.

**[4-2] 에러 처리 패턴** -- PASS

`check_cancel`에서 예외 catch -> False 반환은 "cancel 불가 > 파이프라인 실패" 원칙과 일치. 기존 코드의 방어적 예외 처리 패턴과 동일.

**[4-3] Import 패턴** -- 🟢 Info

5.5절 노드 코드에서 `from src.agents.graph.cancel import check_cancel, make_cancel_updates`를 함수 내부(지연 import)가 아닌 모듈 레벨로 보여주고 있으나, runner.py 5.8절에서는 `from src.agents.graph.cancel import pop_cancel`을 함수 내부에서 사용. 기존 runner.py에서도 `from src.services.turn_text_store import save_turn`을 함수 내부에서 import하므로 runner 쪽은 일관적. 노드 쪽은 모듈 레벨 import가 기존 패턴과 일치하므로 문제 없음.

---

## 5. 모듈화 적정성 (cancel_store.py vs cancel.py)

### 판정: PASS -- 적절한 분리

| 모듈 | 책임 | 계층 |
|------|------|------|
| `cancel_store.py` | 저장소 추상화 (Protocol + Redis/Memory 구현) | `src/services/` (인프라) |
| `cancel.py` | 싱글턴 관리 + 비즈니스 로직 래퍼 (check, pop, clear) + state 업데이트 생성 | `src/agents/graph/` (파이프라인) |

기존 `session/store.py`(인프라) vs `runner.py`(파이프라인 로직) 분리 패턴과 동일. `cancel.py`가 `make_cancel_updates()`로 state 변환까지 담당하여 노드 코드를 단순하게 유지. 적정.

---

## 6. 확장성: astream() 전환 시 변경 범위

### 판정: PASS -- 변경 최소

설계 문서 8절에서 간략히 언급. cancel 플래그 방식은 `ainvoke()`/`astream()` 무관하게 동작. `astream()` 전환 시 변경 필요 사항:

- `runner.py`: `ainvoke()` -> `astream()` 전환 (cancel과 무관한 변경)
- `cancel.py`, `cancel_store.py`: 변경 없음
- 노드 시작부 체크(방식 B): 변경 없음
- 라우팅 함수(방식 A): 변경 없음

`astream()` 사용 시 스트리밍 도중 cancel 감지가 더 빨라질 수 있으나(이벤트 루프 제어권 반환 빈도 증가), cancel 로직 자체의 변경은 불필요. 확장성 양호.

---

## 종합 요약

| # | 등급 | 항목 | 조치 |
|---|------|------|------|
| 1-5 | 🔴 Critical | REST 엔드포인트 `turn_id_param` 미정의 | 함수 시그니처에 Query 파라미터 추가 |
| 1-4 | 🟡 Warning | `check_cancel` 두 버전 공존 (5.3 vs 5.9) | 5.9절 보완을 5.3절에 통합, 단일 버전으로 정리 |
| 2-2 | 🟡 Warning | 4.1절 "라우팅 함수 3개" vs 10절 "4개" 불일치 | 4.1절을 "라우팅 함수 4개 + _handle_error 보완"으로 갱신 |
| 3-1 | 🟡 Warning | conftest.py fixture 누락 | 4.1 변경 목록에 conftest.py 추가 |
| 3-2 | 🟡 Warning | CANCELLED 턴 저장 처리 미명시 | turn_type 구분 여부 설계 판단 추가 |
| 3-3 | 🟡 Warning | PipelineResult.cancelled 필드가 4.1에 미반영 | 4.1 파일 목록에 response.py 추가 |
| 1-3 | 🟢 Info | clear_cancel 사용처 부재 | 향후 필요 시 사용 가능하므로 유지 허용 |
| 2-3 | 🟢 Info | context_interpreter 통과 시 불필요 LLM 호출 1회 | 의도적 판단으로 보이나, 고비용 노드라면 체크 추가 고려 |
| 4-3 | 🟢 Info | Import 패턴 runner vs 노드 차이 | 기존 패턴과 일관적이므로 문제 없음 |
