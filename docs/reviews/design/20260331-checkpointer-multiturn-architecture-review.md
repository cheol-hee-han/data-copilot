# 체크포인터 멀티턴 설계 아키텍처 리뷰

- **리뷰 대상**: `docs/strategy-proposals/checkpointer-multi-turn/01-strategy.md`, `02-detailed-design.md`
- **리뷰 일자**: 2026-03-31
- **관점**: 디자인 패턴 최적화, 과설계 제거, 유지보수성, 심플 아키텍처 지향
- **심각도**: 🔴 Critical (설계 재검토 필요) / 🟡 Major (개선 권장) / 🟢 Minor (선택적 개선)

---

## 요약

설계문서는 학술 논문(AmbiSQL, Sphinteract, PRACTIQ, DTE 등)과 LangGraph 공식 패턴을 충실히 반영하여 **요구사항 충족도는 높다**. 그러나 요구사항을 그대로 설계에 매핑하면서 **불필요한 추상화 계층, 모델 중복, 전역 상태 오염, 모놀리식 Phase** 등의 문제가 발생했다. 핵심 문제는 "정확한 것을 만들려다 복잡한 것을 만든" 패턴이 반복된다는 점이다.

**총 12건** 식별: 🔴 4건, 🟡 5건, 🟢 3건

---

## 🔴 R-01. Handler ABC + Strategy 패턴 과설계

### 현상

7개 AmbiguityType에 대해 5개 핸들러 클래스(FreeTextHandler, SingleSelectHandler, FormulaHandler, ConflictHandler + ABC)와 HandlerRegistry를 정의한다.

### 문제

실제 구현을 보면 **행동이 3가지**밖에 없다:

| 실제 행동 | 해당 핸들러 | validate 본질 | apply_to_state 본질 |
|-----------|------------|---------------|---------------------|
| 자유텍스트 | FreeText, Conflict | `strip()` | `{}` (아무것도 안 함) |
| 선택지 | SingleSelect | 번호/텍스트 매칭 | `TABLE → selected_db_source`, 나머지 `{}` |
| 산출식 선택 | Formula | 번호/텍스트 매칭 (SingleSelect와 동일) | `{}` |

- **FormulaHandler ≈ SingleSelectHandler**: 코드가 거의 동일하고 에러 메시지만 다름
- **ConflictHandler ≈ FreeTextHandler**: 완전 동일
- ABC + Registry + 5개 클래스 = **~180줄 코드**가 실제로는 **15줄 함수**로 대체 가능
- `apply_to_state()`는 7개 중 6개가 `{}` 반환. 1개만 `selected_db_source` 설정

### 권장안

```python
# handlers.py 전체를 이것으로 대체
def validate_answer(answer: str, request: ClarificationRequest) -> str:
    """사용자 응답을 검증한다."""
    answer = answer.strip()
    if not answer:
        raise ValueError("응답이 비어있습니다.")

    if request.question_type == QuestionType.SINGLE_SELECT and request.options:
        for i, opt in enumerate(request.options, 1):
            if answer == str(i) or answer == opt:
                return opt
        raise ValueError(
            f"선택지 중에서 골라주세요: "
            f"{', '.join(f'{i}) {o}' for i, o in enumerate(request.options, 1))}"
        )
    return answer
```

- **question_type 기반 분기 (2가지: FREE_TEXT vs SINGLE_SELECT)** 로 충분
- AmbiguityType별 핸들러가 아니라 QuestionType별 검증이 본질
- 나중에 정말 유형별 커스텀 로직이 필요해지면 **그때 Strategy로 리팩토링** (YAGNI)
- `apply_to_state()` → 현재 `selected_db_source` 1건만 필요하므로 clarification_handler 노드에 인라인

### 영향

- `handlers.py` 180줄 → 20줄 함수 1개
- HandlerRegistry 제거
- ABC 상속 체계 제거
- 테스트 대상: 5개 클래스 → 1개 함수

---

## 🔴 R-02. 모델 4단계 변환 체인 (UncertaintySignal → ClarificationRequest → ClarificationEntry → AuditEntry)

### 현상

모호성 하나가 발견되면 아래 4개 모델을 순서대로 거친다:

```
노드 LLM → UncertaintySignal(9필드)
       → ClarificationRequest(7필드)  [clarification_handler에서 변환]
       → ClarificationEntry(5필드)    [응답 후 변환]
       → AuditEntry(6필드)            [감사 추적용 변환]
```

### 문제

1. **UncertaintySignal과 ClarificationRequest의 중복 필드**: `ambiguity_type`, `description(=question)`, `candidates(=options)`, `source_node`, `reasoning(=context_summary)` — 5개 필드가 이름만 다르게 복사됨
2. **_build_clarification_request()는 필드 매핑 함수일 뿐** — 변환 로직이 아니라 rename 작업
3. **AuditEntry는 정의만 있고 저장 로직 없음** — 사용처가 없는 aspirational 모델
4. 전체적으로 **한 도메인 객체의 생명주기를 4개 클래스로 분리**한 것은 DDD anti-pattern (Anemic Domain Model)

### 권장안: 단일 모델 (AmbiguitySignal)

노드에서 생성 시점에는 ASK/INFER가 섞여서 나오고, 가드레일 이후에야 분리된다.
**처음부터 두 타입으로 나누는 것은 불가능** — 같은 리스트에 ASK/INFER가 공존하므로
하나의 모델이 전체 생명주기를 커버해야 한다.

```
생명주기:
  노드 생성 (ASK or INFER 혼재) → 가드레일 (INFER→ASK 보정)
  → INFER: inferred_value 이미 있음, answer=None 상태로 보존
  → ASK:   interrupt → 사용자 응답 → answer 채워짐
```

```python
class AmbiguitySignal(BaseModel):
    """모호성의 전체 생명주기를 하나의 객체로 관리.

    감지 → 가드레일 보정 → ASK(interrupt→응답) / INFER(자동추론)
    모두 이 모델 하나로 처리한다. decision과 answer 유무로 상태를 판별.
    """
    # ── 감지 시점 (노드가 설정) ──
    source_node: str
    ambiguity_type: AmbiguityType
    decision: Literal["ASK", "INFER"]
    confidence: ConfidenceLevel
    question: str                              # DTE 패턴 포함
    question_type: QuestionType = QuestionType.FREE_TEXT
    options: list[str] = Field(default_factory=list)
    inferred_value: str | None = None          # INFER 시 추론값
    reasoning: str = ""
    override_reason: str | None = None         # 가드레일 보정 시

    # ── 해소 시점 (clarification_handler가 설정) ──
    answer: str | None = None                  # ASK: interrupt resume 후 채워짐
    resolved_at: datetime | None = None

    # ── 판별 프로퍼티 ──
    @property
    def is_resolved(self) -> bool:
        """ASK는 answer가 있어야, INFER는 항상 resolved."""
        return self.decision == "INFER" or self.answer is not None

    @property
    def display_value(self) -> str:
        """결과 안내용: ASK면 answer, INFER면 inferred_value."""
        if self.decision == "INFER":
            return self.inferred_value or ""
        return self.answer or ""
```

**왜 1개 모델이 최적인가**:

1. **ASK/INFER 혼재**: 노드가 `[signal_ASK, signal_INFER]`를 한꺼번에 반환 — 타입이 같아야 하나의 리스트에 담김
2. **가드레일 보정**: INFER→ASK 변환 시 객체 타입이 바뀌면 안 됨 — 필드 변경(`decision="ASK"`)만으로 충분
3. **감사 추적**: 하나의 객체에 감지~해소 전 과정이 기록됨 — AuditEntry 별도 모델 불필요
4. **auto_resolved 안내**: `[s for s in signals if s.decision == "INFER"]` → response_formatter에서 직접 사용
5. **interrupt 페이로드**: `signal.model_dump(include={"question", "question_type", "options", "ambiguity_type"})` — 변환 함수 불필요

- AuditEntry 제거 — AmbiguitySignal 자체가 완전한 감사 기록
- `_build_clarification_request()`, `_to_auto_resolved()` 변환 함수 모두 제거

### 필드 대응 관계 — ASK vs INFER 용도 분기

동일 모델의 `decision` 필드를 기준으로 LLM 프롬프트와 사용자 안내에서 읽는 방식이 달라진다:

```
AmbiguitySignal
├── decision == "ASK"  (명확화 Q&A)
│   ├── question       → 프롬프트 [명확화 대화] 섹션의 "질문"
│   ├── options        → 선택지 (있었다면)
│   ├── answer         → 프롬프트 [명확화 대화] 섹션의 "답변"  ← interrupt 후 채워짐
│   ├── source_node    → return_to (복귀 노드)
│   └── ambiguity_type → 어떤 유형의 모호함이었는지 (감사 추적)
│
└── decision == "INFER"  (자동 추론 결과)
    ├── question       → "무엇이 모호했는지" 설명
    ├── inferred_value → 추론된 값 (프롬프트 + 사용자 안내 모두 사용)
    ├── reasoning      → 추론 근거 (프롬프트 + 사용자 안내 모두 사용)
    ├── source_node    → 어떤 노드에서 추론했는지
    └── answer         → None (사용자 개입 없었음)
```

| 필드 | ASK (명확화 Q&A) | INFER (자동 추론) |
| ---- | ---- | ---- |
| `question` | 사용자에게 한 질문 | 무엇이 모호했는지 설명 |
| `options` | 선택지 | (보통 비어있음) |
| `answer` | 사용자 응답 (채워짐) | `None` (사용자 개입 없음) |
| `inferred_value` | (보통 `None`) | 추론된 값 |
| `reasoning` | 질문 배경 (DTE) | 추론 근거 |
| `display_value` | `answer` 반환 | `inferred_value` 반환 |

> **핵심**: `question` 필드가 양쪽 모두 "무엇이 모호했는지"를 설명하는 동일한 역할.
> ASK일 때는 그 설명이 곧 사용자에게 보여지는 질문, INFER일 때는 LLM이 참고하는 컨텍스트.

#### 복귀 노드의 LLM 프롬프트 컨텍스트 구성

```python
def build_clarification_context(state: PipelineState) -> str:
    """복귀 노드의 LLM 프롬프트에 주입할 명확화 컨텍스트를 구성한다.

    resolved_signals를 decision 기준으로 분리하여 각각 다른 포맷으로 출력.
    """
    lines = []

    # ── ASK 시그널: 명확화 Q&A 쌍 ──
    asks = [s for s in state.resolved_signals if s.decision == "ASK"]
    if asks:
        lines.append("[명확화 대화]")
        for i, s in enumerate(asks, 1):
            lines.append(f"라운드 {i}:")
            lines.append(f"  질문: {s.question}")
            if s.options:
                lines.append(f"  선택지: {', '.join(s.options)}")
            lines.append(f"  답변: {s.answer}")

    # ── INFER 시그널: 자동 추론 결과 ──
    infers = [s for s in state.resolved_signals if s.decision == "INFER"]
    if infers:
        lines.append("\n[자동 추론된 조건]")
        for s in infers:
            lines.append(f"- {s.question} → {s.inferred_value} (근거: {s.reasoning})")

    return "\n".join(lines)
```

복귀 노드의 프롬프트에 주입되면 아래와 같이 렌더링된다:

```text
[사용자 원본 질의]
여신 데이터 뽑아줘

[명확화 대화]
라운드 1:
  질문: 정보계에 유사한 테이블이 두 개 있어서 확인이 필요합니다: 1) 일별 잔액 2) 월말 기준 잔액
  선택지: 일별 잔액 테이블 (매일 갱신), 월말 기준 잔액 테이블 (월 1회 갱신)
  답변: 일별 잔액 테이블이요

[자동 추론된 조건]
- "여신 실적"의 기준이 모호합니다 → 실행 금액 (근거: 과거 SQL 이력 85%가 실행 금액 기준)
- 조회 기간이 명시되지 않았습니다 → 이번 달(2026년 3월) (근거: 업무 관행상 당월 기준)
```

#### 사용자 결과 안내 (response_formatter)

```python
def _build_auto_resolved_notice(state: PipelineState) -> str:
    """INFER 항목을 결과 상단에 자연어로 안내한다."""
    infers = [s for s in state.resolved_signals if s.decision == "INFER"]
    if not infers:
        return ""
    lines = ["조회 기준 안내:"]
    for s in infers:
        lines.append(f"- {s.question} → {s.inferred_value}")
    lines.append("(다른 기준을 원하시면 말씀해 주세요)")
    return "\n".join(lines)
```

### State 필드 변경

```python
class PipelineState(BaseModel):
    # 4개 필드(uncertainty_signals, clarifications, auto_resolved, pending_clarification)를
    # 2개로 통합:
    pending_signals: list[AmbiguitySignal] = []    # 현재 턴의 미처리 시그널 (덮어쓰기)
    resolved_signals: Annotated[list[AmbiguitySignal], operator.add] = []  # 처리 완료 누적
```

- `pending_signals`: 노드가 반환 → clarification_handler가 소비 → 비움 (일반 필드, reducer 불필요)
- `resolved_signals`: ASK(answer 채워짐) + INFER 모두 누적 (operator.add)
- `auto_resolved`는 `[s for s in resolved_signals if s.decision == "INFER"]`로 도출 — 별도 필드 불필요

### 영향

- 4개 모델 → **1개 모델**
- 변환 함수 2개 제거
- State 필드 4개 → 2개 (`pending_signals`, `resolved_signals`)
- `_add_or_clear` 커스텀 reducer 불필요 (R-05 자동 해소)

---

## 🔴 R-03. PipelineState 전역 오염 — 핸들러 전용 필드가 최상위에 노출

### 현상

State에 아래 필드가 추가된다:

```python
selected_db_source: str = ""          # Cross-DB 핸들러 전용 (T4)
user_schema_selection: str = ""       # 스키마 충돌 핸들러 전용 (T5)
pending_clarification: ClarificationRequest | None = None
clarification_return_to: str = ""
```

### 문제

1. **`selected_db_source`, `user_schema_selection`**: 18개 노드 중 각각 1개 노드만 사용하는 필드가 최상위 State에 존재 → 30+개 필드를 가진 PipelineState가 더 비대해짐
2. **`pending_clarification`**: clarification_handler 내부에서만 사용되고, interrupt 페이로드로도 전달됨 → 이중 저장
3. **`clarification_return_to`**: ClarificationRequest.return_to에도 있고, ClarificationEntry.return_to에도 있음 → 3중 저장
4. 핸들러별 전용 필드가 추가될 때마다 State가 계속 팽창하는 구조

### 권장안

1. **`selected_db_source`, `user_schema_selection` 제거** — 명확화 Q&A는 `clarifications` 리스트에 이미 누적됨. 복귀 노드의 LLM이 clarifications에서 읽으면 됨 (설계문서 자체의 원칙 "handler는 clarifications 누적만 담당"과 일치)
2. **`pending_clarification` 제거** — interrupt 페이로드가 이 역할을 함. `aget_state().tasks[].interrupts[].value`로 접근 가능
3. **`clarification_return_to` 제거** — 마지막 signal의 `source_node`에서 도출 가능. 또는 clarification_handler가 라우팅 시 직접 반환

```python
# State 추가 필드를 4개 → 0개로 축소
# clarification_handler → return 값으로 라우팅 정보 전달
# 복귀 노드 → state.clarifications[-1] 참조
```

### 영향

- PipelineState 신규 필드: 8개 → 3개 (`original_query`, `pending_signals`, `resolved_signals`) — R-02의 단일 모델 적용 시
- 핸들러 `apply_to_state()` 메서드 자체가 불필요해짐 (R-01과 연동)
- `clarification_return_to` → `resolved_signals[-1].source_node`로 도출

---

## 🔴 R-04. clarification_handler 노드 = God Node

### 현상

clarification_handler_node 함수 하나가 아래를 모두 처리한다:

1. 가드레일 적용 (apply_guardrails 루프)
2. ASK/INFER 분리
3. INFER → auto_resolved 변환 + 기록
4. ASK 우선순위 선택
5. ClarificationRequest 생성
6. interrupt() 호출
7. resume 후 HandlerRegistry 디스패치
8. validate + apply_to_state
9. ClarificationEntry 생성
10. State 업데이트 조합

### 문제

- **interrupt() 전후가 완전히 다른 실행 컨텍스트**인데 하나의 함수에 있음
  - interrupt 전: 가드레일 + 분류 → "이번 턴에서 할 일 결정"
  - interrupt 후: 검증 + 상태 업데이트 → "다음 턴에서 할 일 실행"
- 함수가 ~80줄이고, 테스트 시 interrupt 전후를 모킹하기 어려움
- **Annotated reducer를 쓰면서 수동 list 조작**: `list(state.auto_resolved) + new_auto_entries` — reducer를 신뢰하지 않는 코드

### 권장안: 단일 모델 + reducer 신뢰 + 단순 흐름

R-02의 `AmbiguitySignal` 단일 모델을 적용하면 변환 함수가 전부 사라진다:

```python
_INTERRUPT_FIELDS = {"question", "question_type", "options", "ambiguity_type", "source_node"}

async def clarification_handler_node(state: PipelineState) -> dict:
    """가드레일 적용 → ASK/INFER 분리 → interrupt 또는 진행."""
    signals = state.pending_signals
    if not signals:
        return {}

    # 1. 가드레일 적용 (인라인, R-09)
    for s in signals:
        override = _should_override_to_ask(s, state)
        if override:
            s.decision = "ASK"
            s.override_reason = override

    ask = [s for s in signals if s.decision == "ASK"]
    infer = [s for s in signals if s.decision == "INFER"]

    # 2. INFER — 이미 resolved 상태, 그대로 누적
    for s in infer:
        s.resolved_at = datetime.now()

    if not ask:
        return {
            "resolved_signals": infer,      # operator.add가 append
            "pending_signals": [],           # 덮어쓰기로 비움
        }

    # 3. ASK — 우선순위 1개 선택 → interrupt
    best = min(ask, key=lambda s: _PRIORITY.get(s.ambiguity_type, 99))
    user_answer = interrupt(best.model_dump(include=_INTERRUPT_FIELDS))

    # resume 후: 검증 (R-01의 validate_answer 함수)
    best.answer = validate_answer(user_answer, best)
    best.resolved_at = datetime.now()

    return {
        "resolved_signals": infer + [best],  # INFER + 방금 해소된 ASK 모두 누적
        "pending_signals": [],
    }
```

**R-02 적용 효과**:
- `_to_auto_resolved()` 변환 함수 제거 — INFER 시그널이 곧 auto_resolved
- `_build_clarification_request()` 변환 함수 제거 — signal이 곧 interrupt 페이로드
- ClarificationEntry 생성 제거 — signal.answer 채우면 끝
- handler dispatch 제거 (R-01과 연동)
- **함수 크기: 80줄 → 25줄**

---

## 🟡 R-05. _add_or_clear 커스텀 reducer — R-02 적용 시 자동 해소

### 현상

`uncertainty_signals`는 노드에서 생성 → clarification_handler에서 소비 → 즉시 비움. 이를 위해 커스텀 `_add_or_clear` reducer를 만들었다.

### 문제

- `uncertainty_signals`는 **노드 간 일시적 메시지**이지 **영속 상태**가 아님
- 커스텀 reducer는 LangGraph 내부 동작에 의존하는 미묘한 코드 (빈 리스트 반환 시 clear)
- 체크포인트에도 불필요하게 저장됨

### R-02 적용 시 자동 해소

R-02에서 `pending_signals`(일반 필드, 덮어쓰기) + `resolved_signals`(operator.add, 누적)로 분리하면:

```python
# pending_signals — reducer 없음, 덮어쓰기
pending_signals: list[AmbiguitySignal] = Field(default_factory=list)

# resolved_signals — operator.add, 누적 전용
resolved_signals: Annotated[list[AmbiguitySignal], operator.add] = Field(default_factory=list)
```

- `pending_signals`: 노드가 반환 → 덮어쓰기 → clarification_handler가 `[]`로 비움 → **커스텀 reducer 불필요**
- `resolved_signals`: ASK(answer 채워짐) + INFER 모두 append → **operator.add로 충분**
- `_add_or_clear` 함수 완전 제거

### 영향

- 커스텀 reducer 제거
- 2종 reducer만 사용: 없음(덮어쓰기) + `operator.add`(누적) — LangGraph 표준 패턴

---

## 🟡 R-06. Checkpointer 팩토리 — 전역 싱글턴 + 5개 폴백 설정

### 현상 (1): 전역 mutable 싱글턴

```python
_checkpointer: Any = None
_pool: Any = None

async def create_checkpointer() -> Any:
    global _checkpointer, _pool
    ...
```

### 문제 (1)

- 전역 mutable state → 테스트에서 격리 어려움 (모듈 import 시 상태 공유)
- `get_checkpointer()`가 None이면 RuntimeError — 초기화 순서 의존성
- `Any` 타입 — 타입 안전성 없음

### 권장안 (1): Lifespan에서 직접 생성, DI로 전달

```python
# main.py lifespan
async with create_checkpointer(settings) as checkpointer:
    app_state.compiled_graph = build_pipeline().compile(checkpointer=checkpointer)
    yield

# 팩토리는 순수 함수 — 전역 상태 없음
async def create_checkpointer(settings: Settings) -> AsyncContextManager[BaseCheckpointSaver]:
    ...
```

### 현상 (2): 5개 폴백 설정 필드

```python
checkpoint_db_host: str = ""       # 빈 문자열이면 history_db_host 사용
checkpoint_db_port: int = 0        # 0이면 history_db_port 사용
checkpoint_db_name: str = ""       # 빈 문자열이면 history_db_name 사용
checkpoint_db_user: str = ""       # 빈 문자열이면 history_db_user 사용
checkpoint_db_password: str = ""   # 빈 문자열이면 history_db_password 사용
```

### 문제 (2)

- 설계문서 자체가 "history_db에 체크포인트 테이블 공존"으로 결정했음
- 5개 폴백 필드는 **기각된 대안(별도 DB)**을 코드로 지원하는 것
- 폐쇄망에서 별도 DB를 쓸 일이 없다고 판단한 결정과 모순

### 권장안 (2): 설정 2개로 축소

```python
checkpointer_backend: str = "memory"          # "memory" | "postgres"
checkpoint_db_url: str = ""                    # 빈 문자열이면 history_db_url 재사용
```

- 별도 DB가 필요하면 `checkpoint_db_url` 하나만 설정
- 5개 필드 → 1개 URL 필드

---

## 🟡 R-07. 7종 AmbiSQL 분류의 프롬프트 부담

### 현상

모든 트리거 노드(5개)의 LLM 프롬프트에 다음을 추가해야 한다:
- 7종 모호성 분류 정의 + 예시
- ASK/INFER 판정 기준 + few-shot 3개+
- ConfidenceLevel 기준
- 도메인 기본값 사전

### 문제

1. **프롬프트 토큰 증가**: 각 노드 프롬프트에 ~300토큰 추가 × 5개 노드 = 1500토큰/요청
2. **폐쇄망 LLM(Solar Pro 2 70B)의 컨텍스트 윈도우 부담**: 이미 스키마 + 매뉴얼 + 이력이 주입되는 노드에 판정 기준까지 추가
3. **history_resolver는 항상 ASK**: UNSURE면 무조건 질문 (맥락 추론 불가). 7종 분류가 불필요
4. **sql_generator Cross-DB는 항상 TABLE + ASK**: 후보 DB가 있으면 무조건 질문. 분류/판정 불필요

### 권장안: 노드별 판정 복잡도 차등화

| 노드 | 실제 필요한 판정 | 권장 |
|------|-----------------|------|
| history_resolver | UNSURE → 항상 ASK | 하드코딩. LLM 판정 불필요 |
| classify_intent | AMBIGUOUS → ASK/INFER 가능 | LLM 판정 유지 (INTENT 타입만) |
| normalize_query | 복수 모호성 → ASK/INFER 가능 | LLM 판정 유지 (전체 7종) |
| sql_generator | Cross-DB → 항상 ASK | 하드코딩. LLM 판정 불필요 |
| confidence_evaluator | CONFLICTED → ASK/INFER 가능 | LLM 판정 유지 (TABLE/VALUE만) |

- **3개 노드만 LLM 판정** 필요, 2개는 규칙 기반 하드코딩
- 프롬프트 부담: 5개 노드 → 3개 노드로 축소
- normalize_query만 전체 7종 분류 필요, 나머지는 해당 유형만

---

## 🟡 R-08. Phase 2A가 모놀리식 — 12개 작업을 하나의 Phase로 묶음

### 현상

Phase 2A에 아래 12개 작업이 포함된다:

1. 스키마 6종 정의 (AmbiguityType, ConfidenceLevel, UncertaintySignal, AutoResolvedEntry, AuditEntry, etc.)
2. ClarificationRequest/Entry/QuestionType 스키마
3. Handler ABC + 5개 핸들러 + Registry
4. apply_guardrails()
5. clarification_handler 노드
6. pipeline.py preprocess 제거 + clarification_handler 추가 + 라우팅 변경
7. runner.py sanitize + interrupt + Command
8. state.py 필드 8개 추가
9. 5개 트리거 노드 UncertaintySignal 마이그레이션
10. 각 노드 프롬프트 변경
11. response_formatter auto_resolved 안내
12. clarifier.py, preprocessor.py 제거

### 문제

- 이 중 하나라도 실패하면 **전체가 동작하지 않는 빅뱅 배포**
- 기존 명확화 흐름(clarify → END)과 새 흐름(interrupt)이 공존할 수 없는 구조
- 리뷰/테스트 범위가 너무 넓어 품질 확보 어려움

### 권장안: Phase 2를 3단계로 분리

**Phase 2A: interrupt 인프라** (기존 흐름 유지하면서 인프라만 준비)
- 스키마 정의 (AmbiguitySignal — 단일 모델)
- clarification_handler 노드 (빈 구현 — pending_signals 없으면 통과)
- runner.py interrupt 감지 + Command(resume=) 분기
- pipeline.py에 clarification_handler 노드 추가 (아직 아무도 시그널을 보내지 않음)

**Phase 2B: Interpret 계층 마이그레이션** (T1~T3)
- history_resolver, classify_intent, normalize_query → UncertaintySignal 방식
- 가드레일 규칙 (Interpret 계층용)
- preprocessor.py 제거, clarifier.py 제거

**Phase 2C: Reason 계층 마이그레이션** (T4~T5)
- sql_generator, confidence_evaluator → UncertaintySignal 방식
- 가드레일 규칙 (Reason 계층용)

각 단계마다 **독립적으로 테스트 + 롤백 가능**.

---

## 🟡 R-09. Guardrails 모듈의 과도한 구조화

### 현상

`guardrails.py`에 아래가 포함된다:
- `QueryContext(BaseModel)` — 2개 bool 필드
- `build_query_context(state)` — state에서 컨텍스트 추출
- `apply_guardrails(signal, query_context)` — match/case 분기
- `_PRIORITY` dict
- `select_by_priority()` 함수

### 문제

- `QueryContext`는 2개 bool 필드 — Pydantic BaseModel로 만들 필요 없음
- `build_query_context()`는 구현이 `_check_code_match(state)`, `_check_calculation(state)` 호출인데 이들의 구현은 비어있음 (placeholder)
- 가드레일 규칙 자체는 **7줄의 match/case** — 별도 모듈로 분리할 규모가 아님

### 권장안: clarification_handler 노드에 인라인

```python
def _should_override_to_ask(signal: UncertaintySignal, state: PipelineState) -> str | None:
    """INFER → ASK 보정이 필요하면 사유를 반환."""
    if signal.decision == "ASK":
        return None
    match signal.ambiguity_type:
        case AmbiguityType.FORMULA:
            return "산출식 관련 모호함은 추론 금지"
        case AmbiguityType.TABLE if len(signal.candidates) >= 2 and signal.confidence == ConfidenceLevel.LOW:
            return "테이블 선택 확신도 부족"
        case AmbiguityType.INTENT if signal.confidence == ConfidenceLevel.LOW:
            return "의도 판정 확신도 부족"
        # ... 나머지
    return None

_PRIORITY = {AmbiguityType.INTENT: 1, AmbiguityType.FORMULA: 1, ...}
```

- 20줄 함수 1개 + dict 1개 → 별도 파일 불필요
- QueryContext 모델 제거
- 가드레일 규칙이 복잡해지면 그때 분리 (현재 7줄)

---

## ~~🟢 R-10. conversation_history와 clarifications의 이중 이력 관리~~ — 오진(false positive), 이미 해결됨

### 현상 (리뷰 당시 지적)

- `SessionStore`: conversation_history 관리 (Redis/Memory)
- `Checkpointer`: clarifications 리스트 관리 (체크포인트 내)
- 설계문서: "SessionStore에서 clarify 메서드 제거, conversation_history만 유지"

### 정정: 설계문서와 현행 코드 모두 이미 해결됨

**설계문서** (02-detailed-design.md, "대화 이력 이중 소스 전략"):

- `HistoryEntryType`에 `"clarification"` 타입을 정의하여 명확화 Q&A를 conversation_history에 통합 기록하도록 명시

**현행 코드** (`main.py`, `store.py`):

- `HistoryEntryType.CLARIFICATION` enum이 존재
- 시스템 명확화 질문 → `type=CLARIFICATION`으로 conversation_history에 append (`main.py:239-248`)
- 사용자 명확화 답변 → `type=CLARIFICATION`으로 conversation_history에 append (`main.py:195-206`)

**결론**: 프론트엔드는 conversation_history 하나만 읽으면 되며 (`type` 필드로 렌더링 구분), 두 소스를 합칠 필요가 없음. Checkpointer의 clarifications는 그래프 내부 State용이고, UI 대화 이력은 SessionStore에 이미 통합되어 있음.

---

## ~~🟢 R-11. clarification_handler 라우팅의 암시적 결합~~ — R-03 적용 시 자동 해소

### 현상

```python
def _route_after_clarify(state: PipelineState) -> str:
    return_to = state.clarification_return_to
    if not return_to:
        return "resolve_history"  # 폴백
    return return_to
```

- `return_to`는 문자열로, 그래프에 해당 노드가 존재하는지 컴파일 타임에 검증 불가
- 5개 트리거 노드 이름이 바뀌면 런타임 에러

### R-03 적용 시 자동 해소

R-03에서 `clarification_return_to` 필드 자체를 제거하고 `resolved_signals[-1].source_node`에서 도출하도록 권고하였으므로, 이 항목의 핵심 문제(전용 문자열 필드의 암시적 결합)는 R-03 적용 시 자동으로 해소된다.

라우팅 시 화이트리스트 검증은 R-03 구현 과정에서 함께 적용:

```python
_VALID_RETURN_TARGETS = frozenset({
    "resolve_history", "classify_intent", "normalize_query",
    "sql_generator", "confidence_evaluator",
})

def _route_after_clarify(state: PipelineState) -> str:
    target = state.resolved_signals[-1].source_node if state.resolved_signals else ""
    if target not in _VALID_RETURN_TARGETS:
        logger.error("Invalid return target", target=target)
        return "resolve_history"
    return target
```

---

## 🟢 R-12. Checkpointer 직렬화 설정의 하드코딩

### 현상

```python
serde = JsonPlusSerializer().with_msgpack_allowlist([
    ("src.agents.state.state",),
    ("src.agents.models.clarification",),
])
```

### 문제

- 새 Pydantic 모델이 State에 추가될 때마다 이 allowlist를 수동 갱신해야 함
- 누락 시 **향후 LangGraph 버전에서 역직렬화 차단** (설계문서 자체가 경고)

### 권장안

```python
# 패키지 레벨로 일괄 허용
serde = JsonPlusSerializer().with_msgpack_allowlist([
    ("src.",),  # src 패키지 전체 허용
])
```

- 또는 LangGraph가 제공하는 `allow_all=True` 옵션 확인 (버전별 상이)
- 최소한 모듈 단위(`"src.agents"`)로 확장하여 개별 모듈 나열 제거

---

## 종합 개선안 — 적용 시 구조 비교

### Before (현재 설계)

```
신규 파일: 6개
  checkpointer.py, clarification.py (6개 모델), handlers.py (ABC + 5 handler + Registry),
  guardrails.py (QueryContext + 3 함수), clarification_handler.py, thread_manager.py

신규 모델: 8개
  AmbiguityType, ConfidenceLevel, UncertaintySignal, AutoResolvedEntry,
  ClarificationRequest, ClarificationEntry, AuditEntry, QueryContext

State 신규 필드: 8개
  original_query, clarifications, pending_clarification, clarification_return_to,
  selected_db_source, user_schema_selection, uncertainty_signals, auto_resolved

Config 신규 필드: 9개
  checkpointer_backend, checkpoint_db_host/port/name/user/password, checkpoint_pool_min/max, checkpoint_thread_ttl_days
```

### After (권장)

```
신규 파일: 3개
  checkpointer.py (순수 팩토리), clarification.py (AmbiguitySignal 단일 모델 + validate 함수),
  clarification_handler.py (가드레일 인라인)

신규 모델: 3개
  AmbiguityType, ConfidenceLevel, AmbiguitySignal

State 신규 필드: 3개
  original_query, pending_signals, resolved_signals

Config 신규 필드: 3개
  checkpointer_backend, checkpoint_db_url, checkpoint_thread_ttl_days
```

### 축소 효과

| 항목 | Before | After | 축소 |
|------|--------|-------|------|
| 신규 파일 | 6개 | 3개 | -50% |
| 신규 모델 | 8개 | 3개 | -63% |
| State 필드 | 8개 | 3개 | -63% |
| Config 필드 | 9개 | 3개 | -67% |
| Handler 코드 | ~180줄 | ~20줄 | -89% |
| 커스텀 reducer | 1개 (_add_or_clear) | 0개 | -100% |
| 모델 변환 함수 | 2개 | 0개 | -100% |
| 전체 신규 코드 추정 | ~800줄 | ~300줄 | -63% |

---

## 우선순위 로드맵

| 순위 | 항목 | 이유 |
| ---- | ---- | ---- |
| 1 | R-02 단일 모델 (4모델→1) | 전체 설계의 기반 스키마, 나머지 모든 변경에 영향. R-05 자동 해소 |
| 2 | R-01 핸들러 단순화 | R-02와 함께 적용하면 clarification_handler가 극적으로 단순해짐 |
| 3 | R-03 State 필드 축소 | R-01, R-02 적용 후 불필요한 필드 자동 식별 |
| 4 | R-04 clarification_handler 리팩토링 | R-01~R-03 반영한 최종 노드 구현 |
| 5 | R-08 Phase 분리 | 구현 시작 전 마일스톤 재설정 |
| 6 | R-06~R-09 기타 | 구현 진행하면서 적용 |
