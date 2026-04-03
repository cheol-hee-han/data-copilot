# 상세 설계: resolved_signals 턴 간 격리

- **일자**: 2026-04-03
- **상태**: 리뷰 반영 완료 (구현 대기)
- **관련 이슈**: 멀티턴 대화에서 이전 턴의 INFER 시그널이 현재 턴 응답에 오염

---

## 1. 문제 정의

### 1.1 현상

세션 `1775202804874`에서 4번째 질의("시각화 해줘")의 `format_response` 출력에
1~2번째 턴("예금신규", "금액으로")의 INFER 시그널이 포함됨:

```
조회 기준 안내:
- '예금 신규'를 어떤 지표로 집계할까요? → 신규 개설 건수
- 집계 기준을 무엇으로 할까요? → 신규 가입 금액 합계
- 조회할 캠페인의 기간 범위를 어떻게 설정할까요? → 당월 진행 캠페인  ← 현재 턴
```

### 1.2 근본 원인

`resolved_signals`는 `Annotated[list, operator.add]` 리듀서를 사용한다.
LangGraph 체크포인터가 같은 `thread_id`로 새 턴을 실행하면:

```
체크포인트(이전 턴): resolved_signals = [Signal_A, Signal_B]
새 입력(현재 턴):    resolved_signals = []  (기본값)
operator.add 적용:  [] + [Signal_A, Signal_B] = [Signal_A, Signal_B]
```

`operator.add`는 누적 전용이므로 빈 리스트를 전달해도 이전 값을 지울 수 없다.

### 1.3 영향받는 소비자

| 소비자 | 위치 | 오염 여부 | 필요한 범위 |
|--------|------|:---:|-----------|
| `build_auto_resolved_notice` | `clarification_context.py:68` | **오염** | 현재 턴만 |
| `build_clarification_context` | `clarification_context.py:30` | **오염** | 현재 턴만 |
| `ask_count` 무한루프 방어 | `context_classifier.py:109` | 정상 | 세션 전체 |
| `_build_clarification_history` | `context_classifier.py:39` | 정상 | 세션 전체 |
| `_route_after_clarify` | `pipeline.py:334` | 정상 | 직전 시그널 |

---

## 2. 설계 결정

### 2.1 선택한 방안: AmbiguitySignal에 `turn_id` 필드 추가 + 소비자 필터링

### 2.2 대안 비교 (검토 완료)

| 방안 | 탈락 사유 |
|------|----------|
| B: 별도 필드 `turn_resolved_signals` | 한 턴 내 다중 시그널 시 덮어쓰기/누적 딜레마 |
| C: 턴 시작 오프셋 카운터 | context_classifier 타이밍 문제, 의미 불투명 |
| D: 커스텀 리듀서 함수 | `ask_count`와 `_build_clarification_history`가 세션 전체 이력을 필요로 하므로 리듀서 단에서 필터링 시 이 요구사항 충족 불가. 소비자별 요구 범위(턴/세션)가 다르므로 리듀서가 아닌 소비자 측 필터가 적합. |
| 단순 초기화 | `operator.add`라 빈 리스트로 리셋 불가 |

---

## 3. 변경 사항 상세

### 3.1 AmbiguitySignal 모델 — `turn_id` 필드 추가

**파일**: `src/agents/models/clarification.py`

```python
class AmbiguitySignal(BaseModel):
    # ── 감지 시점 (노드가 설정) ──
    source_node: str
    ambiguity_type: AmbiguityType
    decision: Literal["ASK", "INFER"]
    confidence: ConfidenceLevel
    question: str
    question_type: QuestionType = QuestionType.FREE_TEXT
    options: list[str] = Field(default_factory=list)
    inferred_value: str | None = None
    reasoning: str = ""
    override_reason: str | None = None

    # ── 해소 시점 (clarification_handler가 설정) ──
    answer: str | None = None
    resolved_at: datetime | None = None

    # ── 턴 격리 ──
    turn_id: str | None = None                   # ★ 신규: 소속 턴 식별자
```

**설계 근거**:
- `str | None`으로 선언하여 기존 체크포인터 데이터와 하위 호환 유지
- 기존 직렬화 데이터는 `turn_id=None`으로 역직렬화됨
- 소비자 필터 `s.turn_id == state.turn_id`에서 `None != "turn-xxx"`이므로
  이전 세션의 시그널은 자연스럽게 제외됨

---

### 3.2 PipelineState — `turn_id` 필드 추가

**파일**: `src/agents/state/state.py`

```python
class PipelineState(BaseModel):
    # ── 공통 ──
    user_input: str = ""
    session_id: str = ""
    original_query: str = ""
    conversation_history: list[dict[str, str]] = Field(default_factory=list)

    # ── 턴 격리 ──
    turn_id: str = ""                            # ★ 신규: 현재 턴 식별자
```

**설계 근거**:
- 일반 필드 (reducer 없음) → 새 턴마다 덮어쓰기
- 빈 문자열 기본값 → CLI 단독 실행 시에도 정상 동작 (필터가 `""` == `None`이 아니므로 구턴 시그널 제외)

> **방어 조치** (W-03 반영): `clarification_context.py`에서 `turn_id`가 빈 문자열일 때
> 경고 로그를 남겨 `runner.py`에서 UUID 생성이 누락된 경우를 조기 감지한다.

---

### 3.3 runner.py — 새 턴 시 `turn_id` 생성

**파일**: `src/agents/graph/runner.py` (라인 139~146)

```python
# 변경 전:
initial_state = PipelineState(
    user_input=user_input,
    original_query=user_input,
    preprocessed_input=sanitized.text,
    session_id=session_id,
    conversation_history=conversation_history or [],
)

# 변경 후:
import uuid as _uuid

initial_state = PipelineState(
    user_input=user_input,
    original_query=user_input,
    preprocessed_input=sanitized.text,
    session_id=session_id,
    conversation_history=conversation_history or [],
    turn_id=str(_uuid.uuid4()),                  # ★ 턴마다 고유 ID 생성
)
```

**설계 근거**:
- UUID4는 충돌 가능성 사실상 0
- `turn_id`는 일반 필드(덮어쓰기)이므로 체크포인터 merge 시
  이전 턴의 값을 새 값으로 교체 → 현재 턴만 식별 가능

---

### 3.4 clarification_handler.py — 시그널에 turn_id 주입

**파일**: `src/agents/nodes/interpret/clarification_handler.py`

pending_signals → resolved_signals 전환 시 `state.turn_id`를 주입한다.
이 경로는 context_classifier, sql_generator, result_finalizer가 생산한 시그널을 커버한다.

```python
async def clarification_handler_node(state: PipelineState) -> dict:
    signals = state.pending_signals
    if not signals:
        return {}

    # ★ 턴 ID 주입 (모든 시그널에 일괄 적용)
    for s in signals:
        s.turn_id = state.turn_id

    # 1. 가드레일 적용 (기존 로직 유지)
    for s in signals:
        override = _should_override_to_ask(s, state)
        ...
```

**주입 위치 근거**:
- 가드레일 보정 전에 주입 (decision 변경과 무관하게 turn_id는 동일)
- 모든 pending_signals가 이 노드를 반드시 거침 (파이프라인 라우팅 규칙)

---

### 3.5 query_normalizer.py — 직접 생산 시그널에 turn_id 설정

**파일**: `src/agents/nodes/interpret/query_normalizer.py` (라인 151~164)

query_normalizer는 clarification_handler를 거치지 않고 `resolved_signals`에 직접 쓰므로
시그널 생성 시 `turn_id`를 명시해야 한다.

```python
# 변경 전:
signals = [
    AmbiguitySignal(
        source_node="normalize_query",
        decision="INFER",
        ambiguity_type=amb.get("ambiguity_type", "CONTEXT"),
        confidence=amb.get("confidence", "LOW"),
        question=amb.get("question", ""),
        question_type=amb.get("question_type", "single_select"),
        options=amb.get("options", []),
        inferred_value=amb.get("inferred_value"),
        reasoning=amb.get("reasoning", ""),
    )
    for amb in normalized.ambiguities
]

# 변경 후:
signals = [
    AmbiguitySignal(
        source_node="normalize_query",
        decision="INFER",
        ambiguity_type=amb.get("ambiguity_type", "CONTEXT"),
        confidence=amb.get("confidence", "LOW"),
        question=amb.get("question", ""),
        question_type=amb.get("question_type", "single_select"),
        options=amb.get("options", []),
        inferred_value=amb.get("inferred_value"),
        reasoning=amb.get("reasoning", ""),
        turn_id=state.turn_id,                   # ★ 턴 ID 직접 설정
    )
    for amb in normalized.ambiguities
]
```

---

### 3.6 clarification_context.py — 소비자 필터링

**파일**: `src/agents/utils/clarification_context.py`

두 함수 모두 `state.turn_id`를 기준으로 현재 턴 시그널만 필터링한다.

```python
def build_clarification_context(state: PipelineState) -> str:
    """resolved_signals를 decision 기준으로 분리하여 프롬프트 섹션을 구성한다."""
    tid = state.turn_id
    if not tid:                                     # ★ W-03 방어: turn_id 미설정 감지
        logger.warning("turn_id가 비어있음 — runner.py에서 UUID 생성 누락 가능성")
    lines: list[str] = []

    # ── ASK 시그널: 명확화 Q&A 쌍 ──
    asks = [
        s for s in state.resolved_signals
        if s.decision == "ASK" and s.turn_id == tid
    ]
    if asks:
        lines.append("[명확화 대화]")
        for i, s in enumerate(asks, 1):
            lines.append(f"라운드 {i}:")
            lines.append(f"  질문: {s.question}")
            if s.options:
                lines.append(f"  선택지: {', '.join(s.options)}")
            lines.append(f"  답변: {s.answer}")

    # ── INFER 시그널: 자동 추론 결과 ──
    infers = [
        s for s in state.resolved_signals
        if s.decision == "INFER" and s.turn_id == tid
    ]
    if infers:
        lines.append("\n[자동 추론된 조건]")
        for s in infers:
            lines.append(
                f"- {s.question} → {s.inferred_value} "
                f"(근거: {s.reasoning})",
            )

    return "\n".join(lines)


def build_auto_resolved_notice(state: PipelineState) -> str:
    """INFER 항목을 결과 상단에 자연어로 안내한다."""
    tid = state.turn_id
    infers = [
        s for s in state.resolved_signals
        if s.decision == "INFER" and s.turn_id == tid
    ]
    if not infers:
        return ""

    lines = ["조회 기준 안내:"]
    for s in infers:
        lines.append(f"- {s.question} → {s.inferred_value}")
    lines.append("(다른 기준을 원하시면 말씀해 주세요)")
    return "\n".join(lines)
```

---

### 3.7 pipeline.py — `_route_after_clarify` turn_id 필터 적용

**파일**: `src/agents/graph/pipeline.py` (라인 329~342)

> **C-01 반영**: 기존 설계에서는 "변경하지 않는 파일"로 분류했으나,
> `resolved_signals[-1]`이 항상 현재 턴 시그널이라는 전제는
> 향후 라우팅 변경이나 예외 상황에서 깨질 수 있다.
> 방어적 프로그래밍 관점에서 turn_id 기반 필터를 적용한다.

```python
# 변경 전:
def _route_after_clarify(state: PipelineState) -> str:
    if state.resolved_signals:
        target = state.resolved_signals[-1].source_node
        target = _LEGACY_TARGET_MAP.get(target, target)
        if target in _VALID_RETURN_TARGETS:
            return target
        logger.error("Invalid return target", target=target)
    return "context_classifier"

# 변경 후:
def _route_after_clarify(state: PipelineState) -> str:
    current_signals = [
        s for s in state.resolved_signals
        if s.turn_id == state.turn_id
    ]
    if current_signals:
        target = current_signals[-1].source_node
        target = _LEGACY_TARGET_MAP.get(target, target)
        if target in _VALID_RETURN_TARGETS:
            return target
        logger.error("Invalid return target", target=target)
    return "context_classifier"
```

---

## 4. 변경하지 않는 파일 (명시적 확인)

| 파일 | 이유 |
|------|------|
| `context_classifier.py` (라인 109) | `ask_count`는 세션 전체 ASK 횟수를 세야 함 (무한루프 방어). 턴 필터 적용 시 방어 깨짐. |
| `context_classifier.py` (라인 39) | `_build_clarification_history`는 `source_node == "context_classifier"`로 이미 필터. 이전 턴의 ASK Q&A도 맥락으로 유용. |
| `formatter.py` | `build_auto_resolved_notice(state)` 호출만. 필터는 clarification_context.py에서 처리. |
| `sql_generator.py` | `build_clarification_context(state)` 호출만 (라인 304). 시그널 생산은 `pending_signals`에 기록 → `clarification_handler`에서 turn_id 주입됨. |
| `result_finalizer.py` | `pending_signals`에 T5 ASK 기록 (라인 188~217). `clarification_handler`에서 turn_id 주입됨. |
| `reasoning_preparer.py` | 주석에서만 `resolved_signals` 언급 (읽기/쓰기 없음). 영향 없음. |
| 테스트 3개 | `turn_id: str | None = None`이므로 기존 테스트에서 미설정 시 None → 호환. |

---

## 5. 체크포인터 호환성

### 5.1 기존 직렬화 데이터

체크포인터(PostgreSQL)에 이미 저장된 `AmbiguitySignal`에는 `turn_id` 필드가 없다.
Pydantic v2의 역직렬화 규칙에 따라 `turn_id=None`으로 복원된다.

### 5.2 필터 동작

```python
# state.turn_id = "turn-abc-123" (현재 턴)
# 이전 턴 시그널: s.turn_id = None (체크포인터에서 복원)
s.turn_id == tid  →  None == "turn-abc-123"  →  False  →  제외됨 ✓
```

### 5.3 `_ALLOWLIST_MODULES` 영향

`src/agents/models/clarification` 모듈은 이미 `_ALLOWLIST_MODULES`에 등록되어 있다
(`checkpointer.py` 라인 98). `turn_id` 필드 추가는 새 클래스가 아닌 기존 클래스의
필드 변경이므로 allowlist 수정 불필요.

### 5.4 Forward Compatibility 제약 (B-01/F-01 반영)

`PipelineState`와 `AmbiguitySignal`에 `model_config = ConfigDict(extra="forbid")`를
설정하면 롤링 배포/롤백 시 체크포인터 역직렬화가 실패한다.
**이 두 모델에는 `extra="forbid"` 설정을 금지한다.**

### 5.5 interrupt/resume 시 turn_id 보존 (I-01 반영)

interrupt 발생 시 LangGraph는 현재 상태를 체크포인트에 저장한다.
resume 시 `runner.py`는 `Command(resume=...)` 경로를 타며 새 `PipelineState`를
생성하지 않는다. 따라서 **turn_id는 체크포인터에서 복원**되어 동일한 값이 유지된다.
이는 올바른 동작이다 (같은 턴의 interrupt/resume이므로).

---

## 6. 데이터 흐름 검증

### 6.1 Turn 1: "예금신규 top 10 지점"

```
runner.py: turn_id = "turn-001" 생성
    ↓
context_classifier: 정상 분류 (ASK 없음)
    ↓
normalize_query: INFER 시그널 생성
    → AmbiguitySignal(question="예금 신규 지표", turn_id="turn-001")
    → resolved_signals에 직접 추가
    ↓
... (SQL 생성/실행/응답)
    ↓
format_response:
    build_auto_resolved_notice(state)
    → tid = "turn-001"
    → 필터: s.turn_id == "turn-001" → [Signal_예금신규] ✓
    → "조회 기준 안내: 예금 신규 지표 → 신규 개설 건수"
```

### 6.2 Turn 4: "시각화 해줘" (이전 턴 시그널 오염 방지 검증)

```
runner.py: turn_id = "turn-004" 생성
    ↓
체크포인터 merge:
    resolved_signals = [Signal_001, Signal_002, Signal_003]  (이전 3턴)
    turn_id = "turn-004"  (덮어쓰기)
    ↓
normalize_query: INFER 시그널 생성
    → AmbiguitySignal(question="캠페인 기간", turn_id="turn-004")
    → resolved_signals = [...이전3건, Signal_004]
    ↓
format_response:
    build_auto_resolved_notice(state)
    → tid = "turn-004"
    → 필터: s.turn_id == "turn-004" → [Signal_004] ✓  (이전 턴 제외)
    → "조회 기준 안내: 캠페인 기간 → 당월 진행 캠페인"
```

### 6.3 다중 ASK 시나리오 (W-02 반영)

같은 턴 내에서 ASK가 여러 번 발생하는 시나리오를 검증한다.

```
runner.py: turn_id = "turn-005" 생성
    ↓
context_classifier: ASK 시그널 생성
    → pending_signals = [Signal_ASK_1(turn_id 미설정)]
    ↓
clarification_handler:
    → Signal_ASK_1.turn_id = "turn-005" 주입
    → interrupt → 사용자 응답 → resume
    → resolved_signals = [...이전턴, Signal_ASK_1(turn_id="turn-005")]
    ↓
_route_after_clarify:
    → current_signals = [Signal_ASK_1]  (turn_id 필터)
    → target = "context_classifier" 복귀 ✓
    ↓
normalize_query: 추가 ASK 발견 → pending_signals = [Signal_ASK_2]
    ↓
clarification_handler:
    → Signal_ASK_2.turn_id = "turn-005" 주입
    → interrupt → 사용자 응답 → resume
    → resolved_signals = [...이전턴, Signal_ASK_1, Signal_ASK_2]
    ↓
_route_after_clarify:
    → current_signals = [Signal_ASK_1, Signal_ASK_2]  (둘 다 turn-005)
    → target = Signal_ASK_2.source_node = "normalize_query" 복귀 ✓
    ↓
sql_generator:
    build_clarification_context(state)
    → asks = [Signal_ASK_1, Signal_ASK_2]  (둘 다 turn-005) ✓
    → 두 Q&A 쌍 모두 SQL 생성 프롬프트에 포함 ✓
```

---

## 7. 테스트 계획 (W-04 반영)

### 7.1 단위 테스트

| # | 테스트 케이스 | 검증 대상 |
|--:| ------------- | -------- |
| 1 | `build_auto_resolved_notice`가 현재 턴 INFER만 반환 | 이전 턴 시그널(turn_id 다름) 제외 확인 |
| 2 | `build_clarification_context`가 현재 턴 시그널만 반환 | ASK/INFER 모두 turn_id 필터 적용 확인 |
| 3 | `_route_after_clarify`가 현재 턴 시그널의 source_node 사용 | 이전 턴 시그널 무시 확인 |
| 4 | `ask_count`가 세션 전체 ASK 카운트 유지 | turn_id 필터 미적용 회귀 방지 |
| 5 | `turn_id` 빈 문자열일 때 경고 로그 발생 | W-03 방어 코드 동작 확인 |

### 7.2 통합 테스트

| # | 시나리오 | 검증 대상 |
|--:| ------- | -------- |
| 1 | 멀티턴(3턴 이상) 시나리오 | 각 턴의 format_response에 해당 턴 INFER만 포함 |
| 2 | 다중 ASK 시나리오 | 같은 턴 내 2회 interrupt/resume 후 올바른 라우팅 |
| 3 | 기존 체크포인트 호환 | turn_id=None인 이전 시그널이 필터에서 자동 제외 |

---

## 8. 변경 파일 요약

| # | 파일 | 변경 | 라인 |
|--:|------|------|------|
| 1 | `src/agents/models/clarification.py` | `turn_id` 필드 추가 | 77 부근 |
| 2 | `src/agents/state/state.py` | `turn_id` 필드 추가 | 558 부근 |
| 3 | `src/agents/graph/runner.py` | `turn_id=uuid4()` 생성 | 140 |
| 4 | `src/agents/nodes/interpret/clarification_handler.py` | 시그널에 turn_id 주입 | 119 부근 |
| 5 | `src/agents/nodes/interpret/query_normalizer.py` | 시그널 생성 시 turn_id 전달 | 152 |
| 6 | `src/agents/utils/clarification_context.py` | 두 함수에 turn_id 필터 + 빈 turn_id 경고 | 30, 68 |
| 7 | `src/agents/graph/pipeline.py` | `_route_after_clarify`에 turn_id 필터 적용 | 329~342 |

---

## 9. Known Limitation (C-01/C-02 반영)

### interrupt 포기 후 새 질의 시 resume 오처리

사용자가 interrupt(명확화 질문) 대기 중 이전 질문에 응답하지 않고 완전히 새로운
질의를 입력한 경우, `runner.py`는 `state_snapshot.next`가 존재하므로 이를
`Command(resume=새질의)` 경로로 처리한다. 사용자의 새 질의가 이전 interrupt의
"답변"으로 사용되어 의도하지 않은 동작이 발생할 수 있다.

이 문제는 turn_id 도입 이전부터 존재하는 기존 한계이며, turn_id 변경이
이를 악화시키지는 않는다. 중기적으로는 `sanitize()` 결과에서
"이전 질문에 대한 답변인지 / 완전히 새로운 질의인지" 감지 로직을 별도 이슈로
추가해야 한다.

---

## 10. 향후 확장 가능성 (I-02 반영)

- **로그/감사 추적**: `turn_id`를 로그에 포함하면 멀티턴 디버깅이 크게 개선된다
- **콜백 핸들러**: `DataCopilotCallbackHandler`에 `turn_id`를 전달하면 추적 리포트에서 턴 단위 분석이 가능
- **세션 이력 API**: 향후 세션 이력 조회 API에서 턴 단위 필터링에 활용 가능

현재 구현 범위에서는 격리 목적으로만 사용하며, 위 활용은 별도 작업으로 진행한다.
