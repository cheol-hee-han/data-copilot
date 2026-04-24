# ConversationHistory 클래스 설계

> 작성일: 2026-04-17
> 상태: 설계 (독립 구현·배포 가능)
> 범위: 대화 이력 표현·렌더링·세션 재개 복원까지. 오케스트레이터 사용 API는 별도 문서로 분리.

---

## 0. 본 문서의 범위

**포함**: 대화 이력을 구조화된 뷰로 표현하는 클래스 자체의 설계, T 라벨링, window 슬라이싱, 첫 소비자인 intent_classifier의 렌더 포맷, 세션 재개 시 복원 동작.

**제외 (별도 문서)**:

- `TurnSnapshot` 모델 정의 및 생성 규칙 — 20260416 본문 §3.1
- CONTINUE Orchestrator의 참조 턴 해석·주입 로직 및 state 필드(`reference_turn_ids`, `reference_snapshot`, `continue_route`, `continue_hint`) — 20260416
- 오케스트레이터 전용 렌더 API (예: "이전 대화 — T2" 메타 라인 포맷) — 20260416

본 문서는 **오케스트레이터가 없어도** 단독 구현·배포·검증 가능하도록 설계한다. 배포 후 intent_classifier가 `[T{n}]` prefix를 포함한 이력을 LLM에 보여주는 것까지 완성되며, `reference_turns` 필드는 LLM이 채우더라도 소비자가 없을 뿐 파이프라인은 정상 동작한다.

---

## 1. 배경과 문제

### 1.1 현재 상태

- `PipelineState.conversation_history: list[dict[str, str]]` — role/content/type만 담긴 평면 dict.
- `services/intent_classifier._format_history`가 단순 텍스트로 렌더링하여 LLM에 전달. 각 턴을 식별할 번호가 없다.
- 대화 이력 아이템은 DB 원천의 `seq` 같은 키를 전혀 보존하지 않는다.

### 1.2 한계

1. LLM이 "어떤 이전 턴을 참조했는지" 구조적으로 응답할 수단이 없다 (문장 해석에 의존).
2. 명확화 Q&A가 섞이면 "업무 턴 N개"를 일관되게 잘라내기 어렵다 (현 window는 메시지 개수 기준).
3. 세션 재접속 시 DB 메시지 이력과 체크포인트 기반 데이터를 **같은 뷰로 취급**하는 추상화가 없다.

### 1.3 목표

- 대화 이력을 **T 번호가 매겨진 불변 뷰**로 표현.
- 원천(`PipelineState.conversation_history` + DB)에 대한 **조립 전용 어댑터**로 구현하여 저장 레이어를 새로 만들지 않는다.
- 이후 CONTINUE Orchestrator가 이 클래스를 소비할 수 있도록 **확장 지점을 남긴다** (본 문서는 확장 지점만 노출, 소비자 구현은 20260416에 위임).

---

## 2. 데이터 모델

### 2.1 MessageEntry

```python
# src/agents/models/conversation.py (신규)
from pydantic import BaseModel
from typing import Literal

MessageType = Literal["normal", "clarification", "error"]
Role = Literal["user", "assistant"]

class MessageEntry(BaseModel):
    """대화 이력 메시지 단위. DB 원천 컬럼의 뷰 표현. 불변."""
    model_config = {"frozen": True}

    role: Role
    content: str
    type: MessageType = "normal"
    seq: int                                  # checkpoint_dc_messages.seq
    t_label: str | None = None                # 조립 시 부여 (예: "T2"). 없으면 None
```

**설계 판단**

- `seq`를 필수로 보관 — 후속 소비자(오케스트레이터)가 스냅샷 매핑 키로 사용 가능하게 함. 본 문서 범위에서는 쓰지 않아도 구조적 일관성을 위해 포함.
- `type`은 DB 원본(`message_type`) 그대로. `error`는 실패 턴 표식이며 T 증가 대상 아님.
- `t_label`은 인스턴스 단위로 window-relative. 불변 보장을 위해 조립 시점에 한 번만 부여.

### 2.2 ConversationHistory

```python
class ConversationHistory(BaseModel):
    """메시지 이력 뷰 어댑터. 불변. 팩토리로만 생성."""
    model_config = {"frozen": True}

    messages: list[MessageEntry]               # window 적용 후 결과
    window: int = 0                            # 0 = 전체

    # 내부 인덱스 (직렬화 제외)
    _label_to_seq: dict[str, int] = {}         # {"T2": 7}
    _seq_to_label: dict[int, str] = {}         # {7: "T2"}

    # ── 팩토리 ──
    @classmethod
    def from_state(
        cls,
        state: "PipelineState",
        window: int = 0,
    ) -> "ConversationHistory": ...

    @classmethod
    def from_messages(
        cls,
        messages: list[MessageEntry],
        window: int = 0,
    ) -> "ConversationHistory": ...

    # ── 조회 API (본 문서 범위) ──
    def render_for_llm(self) -> str: ...
    def seq_of(self, t_label: str) -> int | None: ...
    def label_of(self, seq: int) -> str | None: ...
    def latest_t_label(self) -> str | None: ...
    def has_turn(self, t_label: str) -> bool: ...
```

**스냅샷과의 결합은 본 문서에서 다루지 않는다.** `seq_of`/`label_of`가 "T ↔ seq" 매핑을 제공하며, 스냅샷을 실제로 찾아 연결하는 일은 소비자(orchestrator) 쪽 책임으로 분리한다. 이렇게 하면 ConversationHistory가 TurnSnapshot을 import할 필요가 없어 순환 의존이 생기지 않는다.

---

## 3. T 라벨링 알고리즘

### 3.1 규칙

```
counter = 0
current = None
for msg in windowed_messages:                  # chronological order
    if msg.role == "user" and msg.type == "normal":
        counter += 1
        current = f"T{counter}"
        label_to_seq[current] = msg.seq
        seq_to_label[msg.seq] = current
    msg.t_label = current                      # clarification/assistant/error 는 직전 T 상속
```

### 3.2 경계 케이스

| 케이스 | 처리 |
|---|---|
| window 슬라이스 선두가 assistant 또는 clarification | 해당 메시지는 `t_label=None` (이전 T가 없음). 렌더에서 T prefix 생략. |
| `message_type="error"` (실패 턴) | T 증가 **안 함**. 참조 가능한 업무 턴이 아니므로 라벨 부여 의미 없음. |
| role="user" && type="clarification" (사용자의 명확화 응답) | T 증가 **안 함**. 직전 T 상속. |
| user "normal" 메시지가 전혀 없는 window | 라벨 없음. `latest_t_label()`는 None. |

### 3.3 Window 슬라이싱 — "T 개수" 기준

**핵심 방침**: `window=N`은 "비-clarification user 메시지 N개"를 포함하는 최소 길이 꼬리 구간을 의미한다. 명확화/assistant는 자연스럽게 딸려온다.

```python
def _slice_by_turn_window(msgs: list[MessageEntry], window: int) -> list[MessageEntry]:
    if window <= 0:
        return msgs
    anchors = [i for i, m in enumerate(msgs)
               if m.role == "user" and m.type == "normal"]
    if len(anchors) <= window:
        return msgs
    return msgs[anchors[-window]:]
```

**이유**

- `prompt_history_window` 설정의 의도는 "최근 N턴 업무 맥락"이지 "최근 N개 메시지"가 아니다.
- 명확화 Q&A가 길어질수록 메시지 기준 슬라이스는 업무 턴을 자의적으로 잘라낸다.
- T 기준이어야 `T1..TN`이 LLM에 항상 깔끔히 제시된다.

### 3.4 라벨 일관성 원칙

- 라벨은 **인스턴스 단위 window-relative**. 동일 window로 조립된 인스턴스 간에만 라벨이 일치.
- `from_state(state, window=W)`를 호출하는 노드는 동일 세션 내에서 같은 `W`를 사용해야 한다. `settings.prompt_history_window` 단일 값에 고정.
- 라벨을 state에 영속하지 않는다 — 턴 경계에서 state가 변하면 라벨 의미가 바뀔 수 있어 오해 유발. 라벨은 **조립 → 사용 → 파기**의 단명(ephemeral) 값.

---

## 4. 공개 API

### 4.1 `from_state(state, window)`

```python
@classmethod
def from_state(cls, state: PipelineState, window: int = 0) -> ConversationHistory:
    raw = state.conversation_history           # list[dict]
    messages = [
        MessageEntry(
            role=r["role"],
            content=r["content"],
            type=r.get("type", "normal"),
            seq=r["seq"],                      # ★ DB 조회 시 포함 필요 (§6.2)
        )
        for r in raw
    ]
    return cls.from_messages(messages, window)
```

### 4.2 `from_messages(messages, window)`

- Window 슬라이싱 → T 라벨링 → 인덱스 구성 → 불변 인스턴스 반환.
- 테스트 용이성을 위해 PipelineState에 의존하지 않는 순수 생성 경로 제공.

### 4.3 `render_for_llm()`

용도: intent_classifier가 LLM에게 주는 history 블록을 생성한다.

형식 (예시):

```
  [T1] 사용자: 이번년도 예금신규 top10 지점 알려줘
  [T1] [명확화] 시스템: '예금신규'는 신규 개설 건수요, 잔액 증가요?
  [T1] [명확화] 사용자: 신규 개설 건수
  [T1] 시스템: 대전지점 1,234건 등 10개 지점 결과 조회 완료
  [T2] 사용자: 시각화 해줘
  [T2] 시스템: 막대그래프로 렌더링 완료
```

**규칙**

- prefix 순서: `[T{n}] ` → `[명확화] ` → `역할: `
- `t_label=None` 메시지는 T prefix 생략 (역할/내용만 출력).
- 각 줄 앞 공백 2칸(현 `_format_history`와 동일).
- 빈 인스턴스는 빈 문자열 반환. "이전 대화 없음" 같은 문구는 여기서 붙이지 않는다 (프롬프트 템플릿 책임).

### 4.4 `seq_of(t_label)` / `label_of(seq)`

- `seq_of("T2")` → DB seq (없으면 None). 오케스트레이터가 "T2에 해당하는 user 메시지의 seq"를 얻어 스냅샷을 찾을 때 사용할 기초 API.
- `label_of(7)` → "T2". 역방향 질의가 필요한 로깅·디버깅용.

### 4.5 `latest_t_label()`

- 가장 최근 T 라벨을 반환. LLM이 `reference_turns`를 비워 반환하는 등 명시 참조가 없을 때 소비자의 기본 선택용.

### 4.6 `has_turn(t_label)`

- 단순 존재 여부 확인 (`t_label in _label_to_seq`). LLM이 존재하지 않는 라벨을 반환했을 때 조기 분기에 사용.

---

## 5. PipelineState 영향

### 5.1 기존 필드 유지

```python
conversation_history: list[dict[str, str]]     # 타입 유지 (seq 키 추가만 약속)
```

**이유**: 타입을 `list[MessageEntry]`로 바꾸면 main.py(3곳), runner.py(4곳), message_store.py, intent_classifier(service/node), 테스트까지 전면 수정 필요. ConversationHistory가 `from_state` 시점에 파싱하므로 타입 유지로 이관 비용 최소화.

**필요한 계약(문서 주석)**

- dict에 `seq: int` 키가 항상 포함되어야 함 (DB 조회 경로에서 주입).
- 향후 추가 키가 생겨도 미지정 키는 무시 (forward compatible).

### 5.2 신규 필드 — 본 문서 범위는 없음

- `turn_snapshots`, `reference_turn_ids` 등은 20260416 소관. ConversationHistory 자체는 신규 state 필드를 요구하지 않는다.

---

## 6. DB 조회 확장 (단일 소스)

### 6.1 변경 대상

`src/services/message_store.py:get_conversation_history`

### 6.2 변경 내용

```python
SELECT role, content, message_type, seq        # ★ seq 추가
FROM checkpoint_dc_messages
WHERE thread_id = %(thread_id)s
ORDER BY seq
```

반환 매핑:

```python
return [
    {
        "role": r["role"],
        "content": r["content"],
        "type": r["message_type"] or "normal",
        "seq": r["seq"],                        # ★ 추가
    }
    for r in results
]
```

- 기존 소비자(없는 키 참조)는 새 키를 몰라도 동작 → 하위 호환.
- 스키마 마이그레이션 불필요 (`seq`는 기존 컬럼).

### 6.3 호출 경로 검증

- `main.py` 3곳에서 호출되고 runner로 전달만 됨. 신규 키가 자동으로 state에 흘러 들어감.
- 테스트 fixture가 dict를 수동 조립하는 경우에만 `seq` 추가 필요.

---

## 7. 세션 재개 대응

### 7.1 경로

1. 사용자가 세션 재접속 → `main.py`가 `get_conversation_history(pool, session_id)` 호출.
2. 반환된 `list[dict]`(seq 포함)가 runner → PipelineState에 주입.
3. intent_classifier_node가 `ConversationHistory.from_state(state, window=settings.prompt_history_window)` 조립.
4. 이후 `render_for_llm()` 결과가 LLM 프롬프트에 삽입.

### 7.2 복원 가능/불가능 관점

| 데이터 | 원천 | 재접속 후 상태 |
|---|---|---|
| role/content/type/seq | `checkpoint_dc_messages` | 그대로 복원 |
| T 라벨 | 조립 시 재계산 | 인스턴스가 매번 재조립되므로 "재계산"이 곧 "복원" |
| turn_snapshots (본 문서 범위 외) | LangGraph checkpointer | 20260416에서 다룸 |

### 7.3 열화 시나리오

- DB 조회 실패: 기존과 동일하게 빈 history로 진입.
- messages에는 있는데 seq 컬럼 누락(스키마 불일치): Pydantic ValidationError로 조기 실패 → 배포 누락 조기 탐지.

---

## 8. 첫 소비자: intent_classifier

### 8.1 서비스 시그니처 변경

```python
# src/services/intent_classifier.py
async def intent_classifier(
    query: str,
    history: ConversationHistory,              # ★ 변경 (기존 list[dict])
    *, system_prompt, user_template, clarification_history,
) -> IntentClassifyResult:
    history_text = history.render_for_llm()
    ...
```

- 기존 `_format_history`는 제거 (`render_for_llm`으로 이관).
- `IntentClassifyResult`에 `reference_turns: list[str] = []` 필드 추가. 프롬프트가 LLM에게 `T{n}` 배열을 요구하여 응답에서 수집.
- **소비자가 아직 없어도 무방**. 본 문서 범위에서는 채워진 리스트를 그대로 state에 남기지 않아도 되며, 로깅만 해도 충분.

### 8.2 노드 변경

```python
# src/agents/nodes/interpret/intent_classifier.py
from src.agents.models.conversation import ConversationHistory
from src.config import settings

history = ConversationHistory.from_state(
    state, window=settings.prompt_history_window,
)
result = await intent_classifier(query, history, ...)
```

### 8.3 프롬프트 템플릿 변경

`resources/prompts/interpret/intent_classifier_user.txt`

- history 예시를 `[T1] 사용자: ...` / `[T1] 시스템: ...` 형태로 갱신.
- 출력 스키마에 `reference_turns: string[]` 필드 추가. 지시 문구: "이전 대화와 연관이 있으면 `T-번호`를 관련도 순으로 배열에 담아라. 연관 없으면 `[]`. 번호가 붙지 않은 턴은 참조 불가."
- 배열 상한 3(§10 Q1).

---

## 9. 실패 모드 및 불변식

### 9.1 불변식

- 동일 `(messages, window)` 입력에 대해 `render_for_llm()`과 `_label_to_seq` 결정적.
- window 적용 후 T 번호는 항상 `T1`부터 시작하며 빈 자리 없음(monotone).
- `label_to_seq`의 값은 messages 내 유일 (seq 자체가 유일하므로 자동 보장).

### 9.2 실패 모드

| 상황 | 처리 | 로그 |
|---|---|---|
| messages dict에 seq 누락 | Pydantic ValidationError | ERROR (스키마 불일치) |
| role이 user/assistant 외 값 | Pydantic ValidationError | ERROR |
| window가 messages 길이보다 큼 | 전체 사용, 경고 없음 | — |
| LLM이 존재하지 않는 T 라벨 반환 | `has_turn()=False`로 호출측에서 걸러냄 | INFO (본 문서 소비자 범위 밖이나 API는 제공) |

### 9.3 성능

- 조립 비용: window 크기 N에 대해 O(N). 대개 N ≤ 20이므로 무시.
- 턴당 조립 횟수: 본 문서 범위에서는 intent_classifier_node에서 1회. 매 턴 재조립 원칙.

---

## 10. 미해결 질문

- **Q1**: `reference_turns` 배열 상한을 프롬프트에 3으로 지정 vs 클래스 상수로 노출. 프롬프트 쪽 문자열만으로 충분할 가능성 높음 — 기능 구현은 후속 문서에서 결정.
- **Q2**: 빈 history일 때 프롬프트 템플릿이 `(이전 대화 없음)` 같은 문구를 넣을지. 현 클래스는 빈 문자열만 반환하고, 템플릿이 `{% if history %}...{% else %}없음{% endif %}` 식으로 처리하는 게 책임 분리에 맞음.
- **Q3**: DB에 seq 키가 추가된 뒤 fixture 수정 범위. 기존 단위 테스트가 dict를 수동 조립하는 곳을 일괄 업데이트해야 함.

---

## 11. 테스트 계획

### 11.1 단위 테스트 (`tests/auto/unit/test_conversation_history.py`)

- T 라벨링
  - 정상/명확화/에러 혼합 이력에서 T 증가 규칙 검증.
  - window 슬라이싱: T 기준 커팅 정확성 (명확화가 많은 case 포함).
  - 빈 이력, user 없이 assistant만, 명확화만 등 경계.
- render_for_llm
  - 결정적 골든 문자열 비교.
  - `t_label=None` 메시지 렌더 생략 확인.
- seq_of / label_of / has_turn / latest_t_label
  - 존재/비존재 케이스, 라운드트립.
- from_state
  - `PipelineState`에 `seq` 키 있는 dict와 없는 dict 혼재 시 None 처리(→ ValidationError 기대).

### 11.2 통합 테스트

- `get_conversation_history` → `from_state` → `render_for_llm` 경로를 실제 DB fixture로 검증.
- 세션 재개 시뮬레이션: 기존 session_id에 대한 재조립 결과가 이전과 동일한지 골든 비교.

### 11.3 회귀

- 기존 `_format_history` 출력과 `render_for_llm` 출력이 `[T{n}]` prefix만 추가된 상태로 유사함을 스냅샷 비교로 검증 (기존 intent_classifier의 LLM 거동 드리프트 최소 확인).

---

## 12. 구현 순서 (단독 배포)

본 클래스는 오케스트레이터 없이도 단독 배포 가능하다.

| 단계 | 파일 | 내용 |
|---|---|---|
| 1 | `src/agents/models/conversation.py` | **신규** — MessageEntry, ConversationHistory |
| 2 | `src/services/message_store.py` | `get_conversation_history` SELECT 및 반환에 `seq` 추가 |
| 3 | `src/agents/state/state.py` | `conversation_history` 필드 주석만 보강 (`seq` 키 필수 명시) |
| 4 | `src/services/intent_classifier.py` | 시그니처 변경(`list[dict]`→`ConversationHistory`), `_format_history` 제거 |
| 5 | `src/agents/nodes/interpret/intent_classifier.py` | `ConversationHistory.from_state` 조립 후 서비스 호출 |
| 6 | `resources/prompts/interpret/intent_classifier_user.txt` | `[T{n}]` prefix 예시, `reference_turns` 출력 필드 추가 |
| 7 | `tests/auto/unit/test_conversation_history.py` | 단위 테스트 |

**단일 PR 권장**: 1~7 모두 한 번에. 단계별 분할은 7 없이 배포하면 프롬프트와 서비스 시그니처가 어긋나 회귀 유발.

---

## 13. 확장 지점 (후속 문서가 사용할 API)

- `seq_of(t_label)` / `label_of(seq)`: T ↔ seq 변환. 오케스트레이터가 TurnSnapshot(별도 정의, 필드 `user_message_seq`)과 조인할 때 활용.
- `has_turn(t_label)`: 존재 확인 후 처리 분기.
- `latest_t_label()`: LLM이 명시 참조를 비웠을 때 기본 선택값.

본 클래스는 TurnSnapshot을 import하지 않는다. 조인 책임은 소비자(오케스트레이터 노드)가 가져간다. 이로써 ConversationHistory의 테스트·교체·리팩터링이 스냅샷 모델 변경에 영향받지 않는다.

---

## 14. 요약

- 대화 이력을 `[T{n}]` 라벨과 window 슬라이싱이 적용된 **불변 뷰**로 표현하는 어댑터.
- PipelineState 필드 타입 변경 없음. DB는 SELECT에 `seq` 한 컬럼 추가만.
- 첫 소비자는 intent_classifier이며 본 문서 범위로 단독 구현·배포 가능.
- TurnSnapshot이나 오케스트레이터 상태 필드와의 결합은 별도 문서(20260416)에서 다룬다.
