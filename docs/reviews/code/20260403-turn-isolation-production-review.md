# Production-Grade Critical Review: resolved_signals Turn Isolation

- **일자**: 2026-04-03
- **대상**: `docs/reviews/design/20260403-resolved-signals-turn-isolation.md`
- **리뷰어**: Code Reviewer Agent
- **등급 기준**: BLOCKER > HIGH > MEDIUM > LOW > OK

---

## 요약

설계 문서의 전반적인 접근(AmbiguitySignal에 turn_id 추가 + 소비자 필터링)은 올바르다. operator.add 리듀서의 누적 특성을 우회하는 최소 침습적 방안이며, 변경 파일 7개 범위도 적절하다. 그러나 프로덕션 배포 전 반드시 해결해야 할 BLOCKER 1건, HIGH 3건을 포함하여 총 15건의 이슈를 식별했다.

---

## A. Race Conditions and Concurrency

### A-01. 동일 세션 동시 턴 실행 시 turn_id 충돌 [MEDIUM]

**현상**: WebSocket과 REST API 모두 동일 session_id로 동시에 `run_pipeline()`을 호출할 수 있다. `main.py`에 세션 단위 직렬화(lock)가 없으므로, 두 요청이 동시에 `aget_state()`를 조회하고 둘 다 "새 턴"으로 판정하여 서로 다른 turn_id로 같은 체크포인터 thread를 ainvoke할 수 있다.

**영향**: LangGraph의 체크포인터가 내부적으로 직렬화를 보장하는지 여부에 따라 state corruption 가능성이 있다. turn_id 자체는 각 요청에서 독립적으로 생성되므로 corruption 시 "양쪽 턴의 시그널이 모두 필터링되지 않는" 상황은 아니지만, 체크포인터의 낙관적 동시성 제어가 없다면 한쪽 결과가 유실될 수 있다.

**판정**: 이 이슈는 turn_id 도입과 무관하게 기존에도 존재하는 문제이다. turn_id 변경이 이를 악화시키지는 않으므로 MEDIUM으로 분류하되, 별도 이슈로 세션 레벨 lock 도입을 권고한다.

**권고**: `run_pipeline()`에 세션 단위 `asyncio.Lock` 딕셔너리를 두거나, WebSocket 핸들러에서 한 세션의 동시 파이프라인 실행을 방지하는 가드 추가.

### A-02. WebSocket 재접속 중 이전 턴 실행 중 시나리오 [OK]

WebSocket 재접속 시 `run_pipeline()`이 새로 호출된다. `aget_state()` → interrupt 감지 로직이 있으므로, 이전 턴이 interrupt 대기 중이면 resume으로 처리되고, 완료된 상태면 새 턴으로 시작된다. turn_id는 새 턴에서만 생성되므로 재접속 자체가 turn_id를 오염시키지 않는다.

---

## B. Checkpointer Edge Cases

### B-01. 롤링 배포 시 구버전 코드가 turn_id가 있는 체크포인트를 로드하는 경우 [HIGH]

**현상**: 롤링 배포 중 구버전 서버가 신버전이 저장한 체크포인트(turn_id 포함)를 로드할 수 있다. Pydantic v2는 `model_config`에 `extra = "forbid"`가 아닌 한 알 수 없는 필드를 무시한다.

**검증 필요**: `PipelineState`와 `AmbiguitySignal`에 `model_config = ConfigDict(extra="forbid")`가 설정되어 있는지 확인해야 한다. 설정되어 있다면 구버전 코드가 `turn_id` 필드를 역직렬화할 때 `ValidationError`가 발생하여 **전체 체크포인트 로드가 실패**할 수 있다.

**현재 코드 확인 결과**: `PipelineState`와 `AmbiguitySignal`에 `model_config`가 명시적으로 선언되어 있지 않다. Pydantic v2 BaseModel의 기본값은 `extra="ignore"`이므로 구버전 코드에서 `turn_id`를 무시하고 정상 동작할 것이다.

**판정**: 기본 설정이라면 forward-compatible하다. 그러나 향후 누군가 `extra="forbid"`를 추가하면 이 호환성이 깨지므로, 설계 문서에 **"PipelineState, AmbiguitySignal에 extra='forbid' 설정 금지"** 제약 조건을 명시할 것을 권고한다.

**권고**: 설계 문서 5절(체크포인터 호환성)에 "forward compatibility 전제: `extra` 설정이 `'ignore'`(기본값)이어야 함" 조건 추가.

### B-02. 구버전 체크포인트(turn_id 없음) + 신버전 코드 [OK]

설계 문서 5.1절에서 올바르게 분석됨. `turn_id=None`으로 역직렬화 → `None != "turn-xxx"` → 이전 턴 시그널 자동 제외. 정상 동작한다.

### B-03. 노드 실패 후 체크포인트 복원 (mid-turn) [OK]

LangGraph는 노드 실행 전에 체크포인트를 저장한다. 노드 실패 시 같은 체크포인트에서 재시작한다. `turn_id`는 PipelineState의 일반 필드(덮어쓰기)이므로 runner.py에서 설정한 값이 체크포인트에 저장되어 있고, 재시작 시에도 동일한 turn_id가 유지된다.

---

## C. LangGraph interrupt/resume Semantics

### C-01. interrupt 시점의 체크포인트 상태와 turn_id 보존 [BLOCKER]

**현상**: `clarification_handler_node`에서 `interrupt()` 호출 시, LangGraph는 현재 노드 실행을 중단하고 **노드 실행 전의 체크포인트 상태**를 유지한다. `interrupt()` 전에 수행한 in-place mutation(`s.turn_id = state.turn_id`, 가드레일 보정)은 **체크포인트에 반영되지 않는다**.

resume 시 LangGraph는 노드를 **처음부터 다시 실행**한다. 이때:
1. `state.pending_signals`는 체크포인트에 저장된 원본(turn_id 미설정 상태)
2. `s.turn_id = state.turn_id`가 다시 실행됨 → turn_id가 다시 주입됨

**분석**: 이 경우 turn_id 주입 자체는 문제없이 다시 실행된다. 그러나 **가드레일 보정(INFER→ASK 변환)도 resume 시 다시 실행**된다. 가드레일은 멱등(idempotent)하므로 결과가 동일하다. 또한 `interrupt()` 반환값(사용자 응답)은 resume 시에만 유효하므로 로직 흐름이 정상이다.

**그러나 핵심 문제**: 설계 문서 3.4절에서 turn_id 주입을 가드레일 **전**에 배치했다:

```python
# 설계 문서 3.4절 코드:
for s in signals:
    s.turn_id = state.turn_id    # ★ 턴 ID 주입 (가드레일 전)

for s in signals:
    override = _should_override_to_ask(s, state)
```

그런데 **현재 실제 코드**(clarification_handler.py line 118~128)에서는 가드레일이 먼저 실행된다:

```python
# 현재 실제 코드:
for s in signals:
    override = _should_override_to_ask(s, state)  # 가드레일 먼저
    if override:
        s.decision = "ASK"
        s.override_reason = override
```

설계 문서는 turn_id 주입을 가드레일 전에 배치했지만, 실제 구현에서 이 순서를 정확히 지키는지에 대한 가이드가 불명확하다. 더 중요한 것은:

**`state.turn_id`가 resume 시점에 올바른 값을 유지하는가?**

- runner.py에서 `turn_id`는 `initial_state`에 설정됨 (line 140~146)
- 그러나 interrupt 후 resume 시 runner.py는 `Command(resume=sanitized.text)`로 호출 (line 134~136)
- **이 경로에서는 새 `PipelineState`를 생성하지 않으므로 `turn_id`를 새로 설정하지 않는다**
- resume 시 state는 체크포인트에서 복원된다
- 체크포인터가 저장한 state에 `turn_id`가 포함되어 있는가?

**검증**: runner.py line 139~146에서 `turn_id`를 `initial_state`에 포함하여 `ainvoke()`에 전달한다. LangGraph는 이 initial_state를 체크포인터에 저장한다. resume 시 체크포인터에서 `turn_id`를 복원하므로 값은 유지된다.

**최종 판정**: 동작은 정상이지만, 이 분석이 설계 문서에 없다. **interrupt→resume 시 turn_id 보존 메커니즘**을 설계 문서 6절(데이터 흐름 검증)에 명시적 시나리오로 추가해야 한다. resume 경로에서 turn_id가 새로 생성되지 않는다는 점이 핵심이며, 이는 올바른 동작이다(같은 턴의 interrupt/resume이므로).

**하지만 진짜 BLOCKER**: resume 경로(line 128~136)에서 `is_interrupt_pending = True`일 때 `Command(resume=...)`를 호출한다. 이때 **사용자가 interrupt에 응답하지 않고 완전히 새로운 질의를 입력한 경우**를 고려해야 한다. 현재 코드는 `state_snapshot.next`가 존재하면 무조건 resume으로 처리한다. 사용자가 "아 됐고, 이번 달 대출 현황 알려줘"라고 입력해도 이전 interrupt의 응답으로 처리된다.

이 자체는 기존 버그이지만, turn_id 변경과 결합하면 영향이 달라진다:
- 기존: 이전 턴의 시그널이 누적되므로 새 질의가 오염됨 (원래 버그)
- 변경 후: resume이 완료되면 이전 턴의 시그널은 turn_id 필터로 제외됨. 그러나 사용자의 새 질의가 이전 interrupt의 "답변"으로 처리되어 엉뚱한 라우팅이 발생할 수 있다

**권고**: 이 BLOCKER는 turn_id 설계 자체의 결함이 아니라 기존 interrupt 처리 로직의 한계이다. 그러나 turn_id 도입과 함께 반드시 문서화하고, 향후 "interrupt 포기 감지" 로직 추가를 별도 이슈로 등록해야 한다. 최소한 설계 문서에 **"현재 제약: interrupt 대기 중 새 질의 입력 시 resume으로 처리됨"** 경고를 추가할 것.

**등급 조정**: 기존 버그이므로 turn_id 변경의 BLOCKER가 아닌 **HIGH**로 하향.

### C-02. 사용자가 interrupt를 포기하고 새 질의를 시작한 경우 turn_id 불일치 [HIGH]

C-01에서 식별한 시나리오의 turn_id 관점 분석:

1. Turn 1: turn_id = "aaa", interrupt 발생, 대기 중
2. 사용자가 완전히 새로운 질의 입력
3. runner.py: `is_interrupt_pending = True` → `Command(resume="새 질의")`
4. clarification_handler: "새 질의"를 이전 시그널의 answer로 설정
5. `_route_after_clarify`: 이전 시그널의 source_node로 복귀
6. 이후 노드: turn_id = "aaa" (이전 턴의 ID)로 계속 실행

사용자는 새 질의를 기대하지만 이전 턴의 맥락에서 실행이 계속된다. turn_id 필터 자체는 정상 동작하지만, 사용자 경험 관점에서 심각한 문제다.

**권고**: 설계 문서에 "Known Limitation" 섹션 추가. 중기적으로는 `sanitize()` 결과에서 "이전 질문에 대한 답변인지 / 완전히 새로운 질의인지" 감지 로직 필요.

---

## D. Mutation Safety

### D-01. pending_signals의 in-place mutation 안전성 [HIGH]

**현상**: clarification_handler.py에서 제안된 변경:

```python
for s in signals:
    s.turn_id = state.turn_id
```

`signals = state.pending_signals`이므로 `state.pending_signals` 리스트의 원소를 직접 mutate한다.

**위험 1 - Pydantic frozen model**: `AmbiguitySignal`에 `model_config = ConfigDict(frozen=True)`가 설정되어 있다면 `s.turn_id = ...` 할당이 `ValidationError`를 발생시킨다. 현재 코드에서는 `frozen=True`가 없으므로 동작하지만, 이미 `s.decision = "ASK"`(가드레일 보정, line 127)에서 같은 패턴의 in-place mutation을 사용 중이므로 **기존 패턴과 일관성은 있다**.

**위험 2 - LangGraph state 공유**: LangGraph가 노드에 전달하는 state 객체는 복사본이 아닌 참조일 수 있다. `state.pending_signals`의 원소를 mutate하면, 다른 곳에서 같은 객체를 참조하는 경우 예기치 않은 부작용이 발생할 수 있다. 그러나:
- `pending_signals`는 일반 필드(덮어쓰기)이므로 이전 체크포인트의 값과는 별개
- 같은 노드 실행 내에서만 사용되므로 다른 노드가 동시에 접근하지 않음
- 기존 가드레일 코드도 동일한 mutation 패턴 사용 중

**판정**: 기존 패턴과 일관성이 있으므로 새로운 위험을 도입하지는 않는다. 그러나 방어적 프로그래밍 관점에서 `model_copy()`를 사용하는 것이 더 안전하다.

**권고**: 현재 구현은 수용 가능하지만, 향후 `AmbiguitySignal`에 `frozen=True`를 추가할 가능성을 고려하여 코드 주석에 "in-place mutation 의존: frozen 설정 시 깨짐" 경고를 남길 것.

### D-02. query_normalizer.py의 직접 생성 시그널은 안전 [OK]

query_normalizer는 `AmbiguitySignal(..., turn_id=state.turn_id)`로 새 객체를 생성하므로 mutation 이슈 없음.

---

## E. Filter Correctness

### E-01. `s.turn_id == state.turn_id` 비교의 정확성 [MEDIUM]

**현상**: 설계 문서의 필터 로직:

```python
asks = [s for s in state.resolved_signals if s.decision == "ASK" and s.turn_id == tid]
```

여기서 `tid = state.turn_id`이다.

**엣지 케이스 1 - 빈 문자열 비교**: `PipelineState.turn_id`의 기본값은 `""` (빈 문자열). CLI 실행 등에서 runner.py의 turn_id 생성 코드가 누락되면 `state.turn_id = ""`. 이때 이전 턴 시그널의 `turn_id`도 `""`(같은 누락 상황)라면 필터가 **모든 시그널을 통과**시킨다.

설계 문서 3.2절에서 "CLI 단독 실행 시에도 정상 동작 (필터가 "" == None이 아니므로 구턴 시그널 제외)"라고 했지만, 이는 **구턴 시그널의 turn_id가 None인 경우**에만 해당한다. 구턴 시그널의 turn_id도 `""`이면 `"" == ""`으로 필터를 통과한다.

**발생 가능성**: runner.py에서 turn_id 생성을 누락하는 경우. 설계 문서의 W-03 방어(빈 turn_id 경고 로그)가 이를 감지하므로 운영 중 발견은 가능하지만, 실제 오염은 이미 발생한 후다.

**권고**: `PipelineState.turn_id`의 기본값을 `""` 대신 사용하되, `clarification_context.py`에서 빈 turn_id일 때 경고만 남기는 것이 아니라 **빈 turn_id인 시그널을 필터에서 제외**하는 방어 코드 추가:

```python
tid = state.turn_id
if not tid:
    logger.warning("turn_id가 비어있음")
    return ""  # 안전하게 빈 결과 반환
```

### E-02. state.turn_id가 소비자 읽기 전에 덮어쓰여지는 경우 [OK]

`turn_id`는 runner.py에서 1회 설정되고, 같은 턴 내에서는 변경되지 않는다. LangGraph 노드는 순차 실행이므로 한 노드가 turn_id를 읽는 동안 다른 노드가 덮어쓰는 일은 없다. 다음 턴이 시작되기 전까지 동일한 값이 유지된다.

---

## F. Rollback Safety

### F-01. 배포 후 롤백 시 체크포인트 호환성 [MEDIUM]

**시나리오**: 신버전 배포 → 문제 발견 → 구버전으로 롤백

1. 신버전이 저장한 AmbiguitySignal에는 `turn_id` 필드가 포함
2. 구버전의 AmbiguitySignal 모델에는 `turn_id`가 없음
3. 체크포인터에서 역직렬화 시:
   - `extra="ignore"` (기본값): 구버전에서 `turn_id`를 무시하고 정상 로드 → **안전**
   - `extra="forbid"`: `ValidationError` → **체크포인트 로드 실패**

4. 구버전 코드에서 `build_auto_resolved_notice`, `build_clarification_context`에 turn_id 필터가 없으므로 **모든 resolved_signals를 출력** → 원래 버그 상태로 복귀

**판정**: 롤백 시 원래 버그 상태로 복귀하는 것은 수용 가능하다 (기존 동작). 체크포인트 로드 실패만 방지하면 된다.

**권고**: B-01과 동일 — `extra="forbid"` 금지 제약 명시.

### F-02. PipelineState.turn_id 필드의 롤백 호환성 [OK]

`turn_id`는 일반 필드(리듀서 없음)이므로, 구버전에서 이 필드가 없는 채로 ainvoke하면 체크포인터가 해당 필드를 무시한다. 신버전 체크포인트의 turn_id 값은 구버전에서 접근하지 않으므로 부작용 없다.

---

## G. Completeness

### G-01. 미사용 파일의 AmbiguitySignal 참조 [LOW]

`src/agents/nodes/interpret/미사용_intent_classifier.py`에서 `AmbiguitySignal`과 `pending_signals`를 사용한다 (grep 결과 확인). 파일명에 "미사용"이 명시되어 있으므로 현재 파이프라인에 영향은 없지만, 향후 이 파일을 재활용할 경우 turn_id 누락 가능성이 있다.

**권고**: 죽은 코드 제거 원칙에 따라 이 파일 삭제. 또는 파일 상단에 `# DEPRECATED: 사용하지 않음` 주석 추가.

### G-02. readiness_gate.py — resolved_signals 미참조 확인 [OK]

grep 결과 readiness_gate.py에서 `resolved_signals`, `pending_signals`, `AmbiguitySignal`을 참조하지 않음. 다만 `pending_signals`를 직접 설정하지 않고 라우팅 함수(`_route_after_readiness_gate`)에서 `state.pending_signals`를 검사한다. 이는 pipeline.py의 라우팅 로직이며 turn_id와 무관하다.

### G-03. formatter.py — build_auto_resolved_notice 호출 확인 [OK]

`formatter.py` line 84에서 `build_auto_resolved_notice(state)`를 호출한다. 필터 로직은 `clarification_context.py`에서 처리되므로 formatter 자체는 변경 불필요. 설계 문서의 판단과 일치.

### G-04. state.model_dump() / 직렬화 경로의 간접 접근 [OK]

`runner.py`의 `_build_result()`에서 `result.get("resolved_signals")`로 접근하지 않는다. `result`는 LangGraph가 반환하는 dict이며 `formatted_response`, `trace_log` 등만 읽는다. `main.py`도 `run_pipeline()` 결과인 `PipelineResult`만 사용하며 state 직접 접근 없음.

### G-05. sql_generator.py의 Cross-DB AmbiguitySignal 생성 시 turn_id 누락 [HIGH]

**현상**: `sql_generator.py` line 179~197에서 Cross-DB 감지 시 `AmbiguitySignal`을 생성하여 `pending_signals`에 넣는다. 이 시그널은 `clarification_handler`를 거치므로 거기서 turn_id가 주입된다.

**그러나**: 이 시그널은 `decision="INFER"`이므로 clarification_handler의 `infer` 리스트에 들어간다 (line 131). infer 리스트는 `resolved_signals`로 직접 반환된다 (line 139). 설계 문서 3.4절의 turn_id 주입 로직이 가드레일 처리 **전**에 모든 시그널에 주입한다고 되어 있으므로, INFER 시그널에도 turn_id가 주입된다.

**확인**: 설계 문서 3.4절에서 "모든 시그널에 일괄 적용"이라고 명시. 맞다.

**재확인 — readiness_gate의 T5 경로**: `readiness_gate_node`가 `pending_signals`를 설정하는 경우가 있는가? grep 결과 없음. readiness_gate는 `pending_signals`를 직접 설정하지 않고, 라우팅 함수가 기존 `pending_signals`를 검사할 뿐이다.

**재확인 — result_finalizer의 T5 시그널**: `result_finalizer.py` line 63에서 `updates["pending_signals"] = signals`로 설정한다. 이 시그널들은 clarification_handler를 거치므로 turn_id가 주입된다. OK.

**최종 판정**: 모든 pending_signals → clarification_handler 경로에서 turn_id가 주입된다. OK.

(등급을 OK로 수정)

### G-06. context_classifier.py의 ask_count 필터 비적용 확인 [OK]

설계 문서의 판단과 일치. `ask_count`(line 109~111)는 세션 전체의 ASK 횟수를 세어 무한루프를 방어하므로, turn_id 필터를 적용하면 방어가 깨진다. 변경하지 않는 것이 올바르다.

### G-07. context_classifier.py의 _build_clarification_history 필터 비적용 확인 [OK]

line 39~48에서 `source_node == "context_classifier"` 필터만 적용. 이전 턴의 명확화 이력도 맥락으로 유용하므로 turn_id 필터 미적용이 올바르다.

---

## H. Production Deployment Risks

### H-01. 최악의 실패 모드 [MEDIUM]

**시나리오**: runner.py에서 `turn_id=str(uuid.uuid4())`를 빠뜨리고 배포.

**결과**: `state.turn_id = ""` → `clarification_context.py`에서 경고 로그 발생 → 모든 resolved_signals의 `turn_id`도 `""`이므로 필터가 모든 시그널을 통과 → **원래 버그 상태와 동일** (graceful degradation).

W-03 방어 코드(빈 turn_id 경고)가 동작하므로 탐지 가능. 최악의 경우에도 기존 동작으로 fallback되므로 데이터 손실이나 잘못된 SQL 생성은 없다.

**권고**: 단위 테스트에서 runner.py의 turn_id 생성을 반드시 검증하는 테스트 추가 (설계 문서 7절에 이미 포함).

### H-02. Feature flag 부재 [LOW]

현재 설계에는 turn_id 필터링을 활성화/비활성화하는 feature flag가 없다. 문제 발생 시 전체 코드 롤백이 필요하다.

**권고**: `settings.turn_isolation_enabled: bool = True` 플래그를 추가하고, `clarification_context.py`에서 플래그가 False이면 기존 동작(전체 시그널 반환)으로 fallback하는 옵션 제공. 단, 변경 범위가 작고 graceful degradation이 보장되므로 필수는 아님.

### H-03. 모니터링 부재 [MEDIUM]

turn_id 필터가 실제로 시그널을 제외하고 있는지, 예상대로 동작하는지 모니터링할 방법이 없다.

**권고**:
1. `clarification_context.py`에서 필터된 시그널 수와 전체 시그널 수를 로그에 기록:
   ```python
   logger.debug(
       "resolved_signals 필터",
       total=len(state.resolved_signals),
       current_turn=len(asks),
       turn_id=tid[:8] if tid else "empty",
   )
   ```
2. DataCopilotCallbackHandler에 turn_id를 전달하여 추적 리포트에 포함 (설계 문서 9절의 향후 확장과 일치)

---

## I. 설계 문서 자체의 이슈

### I-01. resume 경로에서 turn_id가 새로 생성되지 않는다는 분석 누락 [MEDIUM]

설계 문서 6절의 데이터 흐름 검증에서 interrupt/resume 시나리오(6.3)를 다루고 있으나, **resume 경로에서 turn_id가 새로 생성되지 않고 체크포인터에서 복원된다**는 점이 명시되어 있지 않다. 이는 올바른 동작이지만 명시적 검증이 필요하다.

**권고**: 6.3절에 "resume 시 turn_id는 체크포인터에서 복원됨 (runner.py line 134~136 참조)" 설명 추가.

### I-02. 테스트 계획의 기존 테스트 회귀 검증 부족 [LOW]

설계 문서 7절에서 신규 테스트만 다루고, 기존 테스트 3개(test_agentic_core.py, test_agentic_e2e.py, test_clarify_node.py)의 회귀 검증 방안이 "turn_id: str | None = None이므로 기존 테스트에서 미설정 시 None → 호환"이라고 한 줄로 처리되어 있다. 실제로 기존 테스트가 turn_id 없이 통과하는지 CI에서 검증해야 한다.

---

## 종합 판정 요약

| ID | 등급 | 요약 |
|:---|:---:|------|
| C-01 | ~~BLOCKER~~ **HIGH** | interrupt 포기 후 새 질의 시 resume으로 오처리 (기존 버그, turn_id 무관하지만 문서화 필요) |
| C-02 | HIGH | interrupt 포기 시 turn_id 불일치 — Known Limitation 문서화 필요 |
| D-01 | HIGH | in-place mutation 패턴은 기존과 일관성 있으나 frozen 설정 시 깨짐 — 주석 경고 필요 |
| G-05 | ~~HIGH~~ OK | sql_generator Cross-DB 시그널 → clarification_handler에서 turn_id 주입 확인됨 |
| B-01 | HIGH | 롤링 배포 시 extra="forbid" 설정 존재 시 체크포인트 로드 실패 가능 — 제약 조건 명시 |
| E-01 | MEDIUM | 빈 turn_id 동일 비교로 필터 무력화 가능 — 빈 turn_id 시 빈 결과 반환 방어 추가 |
| A-01 | MEDIUM | 동일 세션 동시 실행 — 기존 이슈, 별도 작업 권고 |
| F-01 | MEDIUM | 롤백 호환성 — extra="forbid" 금지 제약 명시 |
| H-01 | MEDIUM | 최악 실패 시 원래 버그로 fallback — graceful degradation 확인됨 |
| H-03 | MEDIUM | 모니터링 부재 — 필터 결과 로그 추가 권고 |
| I-01 | MEDIUM | resume 경로 turn_id 보존 분석 누락 — 설계 문서 보완 필요 |
| G-01 | LOW | 미사용 파일의 AmbiguitySignal 참조 — 파일 삭제 또는 deprecation 표시 |
| H-02 | LOW | Feature flag 부재 — graceful degradation으로 대체 가능 |
| I-02 | LOW | 기존 테스트 회귀 검증 — CI에서 자동 확인 |
| A-02, B-02, B-03, D-02, E-02, F-02, G-02, G-03, G-04, G-06, G-07 | OK | 검토 완료, 이슈 없음 |

---

## 배포 전 필수 조치 (Action Items)

### 반드시 수행 (HIGH)

1. **설계 문서에 "Known Limitation: interrupt 포기 시 resume 오처리" 경고 추가** (C-01, C-02)
2. **설계 문서에 "PipelineState, AmbiguitySignal에 `extra='forbid'` 설정 금지" 제약 명시** (B-01, F-01)
3. **clarification_handler.py의 in-place mutation에 주석 경고 추가**: `# CAUTION: AmbiguitySignal이 frozen=True로 변경되면 이 코드가 깨짐` (D-01)

### 권고 (MEDIUM)

4. `clarification_context.py`에서 빈 turn_id일 때 경고 + 빈 결과 반환으로 방어 강화 (E-01)
5. 필터 결과 디버그 로그 추가 (H-03)
6. 설계 문서 6절에 interrupt/resume 시 turn_id 보존 경로 명시 (I-01)
7. 별도 이슈: 세션 단위 동시 실행 방지 (A-01)

### 선택 (LOW)

8. `미사용_intent_classifier.py` 삭제 (G-01)
9. `settings.turn_isolation_enabled` feature flag (H-02)
10. CI에서 기존 테스트 회귀 확인 (I-02)

---

## 최종 결론

설계의 핵심 접근 방식은 올바르며 최소 침습적이다. BLOCKER는 없다 (C-01은 기존 버그이므로 HIGH로 하향). HIGH 이슈 3건은 모두 **문서 보완 + 주석 추가** 수준이며 코드 로직 변경은 필요하지 않다. MEDIUM 이슈 중 E-01(빈 turn_id 방어)만 코드 변경이 필요하며, 이는 2줄 추가로 해결 가능하다.

**배포 권고**: HIGH/MEDIUM 조치 후 배포 가능.
