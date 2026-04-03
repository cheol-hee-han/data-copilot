# HITL 명확화 트리거 통합 아키텍처 — LangGraph 기반 권고안

> **작성일**: 2026-03-30
> **리서치 애널리스트**: Research Analyst Agent
> **적용 대상**: Data Copilot v3 (`src/agents/graph/pipeline.py`)

---

## 0. 현황 진단 — 분산된 5개 명확화 트리거

현재 파이프라인에는 명확화 응답 생성 로직이 5곳에 독립적으로 산재한다.

| # | 위치 | 트리거 조건 | 응답 형태 | 현재 처리 방식 |
|---|------|-----------|----------|--------------|
| T1 | `resolve_history` | UNSURE (대화 맥락 불분명) | 자유 입력 | 노드 내부에서 직접 응답 반환 후 종료 |
| T2 | `classify_intent` → `clarify` 노드 | AMBIGUOUS (의도 불분명) / 비데이터 의도 | 자유 입력 또는 선택지 | 별도 `clarify` 노드에서 처리 후 종료 |
| T3 | `confidence_evaluator` → ASK_USER | CONFLICTED knowledge_item 존재 | 선택지 (테이블 선택 등) | `result_finalizer`로 전달 후 자체 응답 생성 |
| T4 | `sql_generator` | 교차 DB 감지 | 선택지 (DB 소스 선택) | 노드 내부에서 직접 응답 반환 |
| T5 | `result_finalizer` | CONFLICTED 재판정 | 자체 생성 질문 | clarify 노드 미경유, 직접 종료 |

**문제점 요약:**
- 명확화 응답 스키마가 통일되지 않아 프론트엔드가 각 케이스를 별도 분기 처리해야 함
- 응답 후 재진입 지점(resume 시 어느 노드로 돌아갈지)이 명시적이지 않음
- 선택지 형태와 자유 입력 형태가 뒤섞여 있고, 검증 로직이 분산됨
- interrupt() 메커니즘 미사용 — LangGraph의 체크포인터 기반 상태 보존이 활용되지 않음

---

## 1. 리서치 범위 및 출처 분류

### Tier 1 — 학술 논문

| 논문 | 출처 | 핵심 기여 |
|------|------|---------|
| Sphinteract: Resolving Ambiguities in NL2SQL Through User Interaction (Zhao et al., 2025) | PVLDB Vol.18 | SRA 패러다임, 명확화 질문 유형 분류, 정확도 +42% |
| Boundary-Aware NL2SQL: Integrating Reliability through Hybrid (BAR-SQL, 2025) | arXiv:2601.10318 | 4가지 명확화 트리거 분류 (ambiguity/constraint/knowledge/dimension), 명확화 vs 진행 판정 |
| Data-Aware Socratic Query Refinement in Database Systems (DASG, 2025) | arXiv:2508.05061 | VoC > CoD 명확화 비용 모델, facet 기반 질문 최소화 |
| Human-In-the-Loop Software Development Agents (HULA, 2024) | arXiv:2411.12924 | Atlassian JIRA 프로덕션 배포 사례, 단계별 HITL 개입 설계 |

### Tier 2 — 공식 문서 / 기술 블로그

| 출처 | 핵심 내용 |
|------|---------|
| LangChain 공식 문서 — Interrupts | interrupt() 인덱스 기반 매칭, 다중 interrupt 순서 고정 필요성 |
| LangChain 블로그 — Making it easier to build HITL agents with interrupt | interrupt()의 4가지 워크플로 패턴 |
| Cloudflare Agents HITL Guide | JSON Schema 기반 elicitation 스키마, approval lifecycle |
| LangGraph Best Practices (Swarnendu De) | interrupt 전 상태 스냅샷 필수, 사이드이펙트 후치 원칙 |

---

## 2. 핵심 발견 사항

### 2.1 LangGraph interrupt() 공식 메커니즘

LangChain 공식 문서에 따르면 `interrupt()`는 내부적으로 예외를 발생시켜 런타임이 상태를 체크포인터에 저장하고 대기하는 방식으로 동작한다. 재개 시 해당 노드의 **처음부터 재실행**된다 — 이것이 설계의 핵심 제약이자 기회다.

**다중 interrupt 순서 규칙 (공식 문서 명시):**
> "When a node contains multiple interrupt calls, LangGraph keeps a list of resume values specific to the task executing the node. Matching is **strictly index-based**, so the order of interrupt calls within the node is important."

즉, 단일 노드 안에서 여러 `interrupt()` 호출 시 순서가 완전히 고정되어야 한다. 조건부로 skip하는 것은 인덱스 불일치를 유발한다.

**금지 패턴 (공식 문서):**
```python
# 절대 금지: interrupt를 try/except로 감싸면 예외가 잡혀 pause 불가
try:
    answer = interrupt("질문")
except:
    pass

# 절대 금지: 조건부 interrupt (인덱스 불일치)
if condition:
    a1 = interrupt("질문 1")
    a2 = interrupt("질문 2")  # condition=False면 a2 인덱스가 0이 됨
```

**허용 패턴 (공식 문서):**
```python
# 모든 interrupt는 항상 동일한 순서로 실행되어야 함
a1 = interrupt("질문 1")
a2 = interrupt("질문 2")
# 또는 항상 하나만 사용 (단일 interrupt 노드)
```

**결론**: 명확화 질문의 유형이 다르다면, 각 유형을 별도 노드 또는 별도 서브그래프로 분리하는 것이 공식 권장 아키텍처다.

### 2.2 Sphinteract SRA 패러다임

Zhao et al. (PVLDB 2025)은 NL2SQL 명확화를 위한 **Summarize-Review-Ask (SRA)** 패러다임을 제안하며 KaggleDBQA, BIRD 벤치마크에서 정확도 +42%를 달성했다:

1. **Summarize**: 현재까지 이해한 내용 요약
2. **Review**: 모호성 검토 — 어떤 종류의 모호성인지 분류
3. **Ask**: 사용자 친화적 질문 생성 (SQL 구문 포함 금지)

모호성 분류:
- **Lexical**: 단어 의미 다의성 ("상위 고객" = 매출 상위? 등급 상위?)
- **Schema**: 테이블/컬럼 다중 후보 ("여신잔액" → LN_BAL_D vs LN_BAL_M)
- **Temporal**: 기간 불특정 ("최근", "이번", "작년")
- **Scope**: 범위 불특정 ("전체", "모든 지점")

### 2.3 DASG 명확화 비용 모델

arXiv:2508.05061 (Data-Aware Socratic Query Refinement)은 명확화 요청을 비용으로 분석하는 프레임워크를 제시한다:

**Value of Clarification (VoC) > Cost of Dialogue (CoD)** 일 때만 명확화 트리거

- VoC = 명확화로 인한 SQL 정확도 향상 기댓값
- CoD = 사용자 부담 (인터랙션 횟수 × 복잡도)
- 단순 쿼리, 이미 컨텍스트가 풍부한 경우 → 진행 우선
- CONFLICTED 항목, 유사 테이블 다수 → 명확화 우선

이는 현재 시스템의 `confidence_evaluator`의 CONFLICTED 판정과 직접 대응된다.

### 2.4 BAR-SQL 4-카테고리 분류

arXiv:2601.10318은 명확화 트리거를 4가지로 분류한다:

| 카테고리 | 정의 | 대응 Action |
|---------|------|-----------|
| Ambiguity Clarification | 용어 의미 다의성 | 선택지 제시 |
| Constraint Follow-Up | 필수 파라미터 누락 | 자유 입력 요청 |
| Knowledge Rejection | 존재하지 않는 메트릭/지표 | 거절 + 대안 제시 |
| Dimension Rejection | 스키마에 없는 컬럼/테이블 | 거절 + 유사 항목 제시 |

### 2.5 프로덕션 시스템의 패턴 — Cloudflare Agents

Cloudflare Agents HITL 가이드는 JSON Schema 기반 elicitation 스키마를 명세한다:

```json
{
  "message": "어떤 지점을 조회하시겠습니까?",
  "requestedSchema": {
    "type": "object",
    "properties": {
      "selection": {
        "type": "string",
        "enum": ["서울지점", "부산지점", "전체"]
      }
    },
    "required": ["selection"]
  }
}
```

핵심: **interrupt 페이로드 자체가 UI 렌더링 명세를 포함**한다. 프론트엔드는 스키마를 보고 자동으로 input 컴포넌트를 결정한다.

---

## 3. 아키텍처 패턴 비교 평가

### 3.1 패턴 A: 분산 interrupt (현재 방식)

각 노드가 독립적으로 명확화를 처리.

```
resolve_history → [자체 응답 반환]
classify_intent → clarify 노드 → [응답 반환]
confidence_evaluator → result_finalizer → [응답 반환]
sql_generator → [자체 응답 반환]
```

| 기준 | 평가 |
|------|------|
| 다른 질문 유형 지원 | 가능하나 비일관적 |
| 응답 검증 분리 | 불가 (노드 내 혼합) |
| 비즈니스 로직 분리 | 불가 |
| interrupt 수명주기 관리 | 없음 (체크포인터 미사용) |

**결론**: 기각. 프론트엔드 부담 과중, 재진입 지점 불명확.

### 3.2 패턴 B: 단일 중앙집중 clarify 노드 + interrupt()

모든 명확화 트리거가 단일 노드로 라우팅. 해당 노드에서 interrupt() 호출.

```python
async def clarify_node(state: PipelineState) -> PipelineState:
    request = state["pending_clarification"]
    answer = interrupt(request)  # 단일 interrupt
    return {"clarification_answer": answer}
```

| 기준 | 평가 |
|------|------|
| 다른 질문 유형 지원 | 가능 (request 객체가 유형 포함) |
| 응답 검증 분리 | 가능 (노드 내 validator 호출) |
| 비즈니스 로직 분리 | 우수 |
| interrupt 수명주기 관리 | 체크포인터 활용 |
| resume 후 라우팅 | 명확화 전 노드로 복귀 가능 |

**결론**: 권장 기반 패턴. 단, resume 후 라우팅을 위한 `return_to` 필드 필요.

### 3.3 패턴 C: 명확화 서브그래프 (Subgraph Interrupt)

명확화 전체를 독립 서브그래프로 캡슐화.

```python
clarify_subgraph = StateGraph(ClarificationState)
clarify_subgraph.add_node("ask", ask_node)       # interrupt()
clarify_subgraph.add_node("validate", validate_node)
clarify_subgraph.add_edge("ask", "validate")
```

| 기준 | 평가 |
|------|------|
| 다른 질문 유형 지원 | 우수 |
| 응답 검증 분리 | 우수 (서브그래프 내 독립 노드) |
| 비즈니스 로직 분리 | 우수 |
| interrupt 수명주기 관리 | 우수 |
| 복잡도 | 높음 — 서브그래프 상태 매핑 필요 |

**결론**: 장기적으로 이상적이나, 현재 파이프라인 구조에서 부모-자식 상태 매핑 오버헤드가 있음. 단, CONFLICTED 케이스처럼 검증 후 재처리가 필요한 경우에는 우위.

### 3.4 패턴 D: Strategy + Command 패턴 (권고안)

**Strategy 패턴**: 명확화 유형별 핸들러를 등록 가능한 전략 객체로 분리.
**Command 패턴**: interrupt() resume 값을 Command 객체로 처리, 복귀 지점을 Command.goto로 지정.

이것이 본 리서치의 권고 패턴이다. 상세는 4절에서 기술.

---

## 4. 권고 아키텍처 — Unified Clarification Framework

### 4.1 설계 원칙

1. **단일 interrupt 노드** — 모든 명확화는 `clarification_handler` 노드 하나를 통과
2. **타입 기반 스키마** — `ClarificationRequest` 모델이 질문 유형과 UI 렌더링 명세를 포함
3. **Strategy 핸들러** — 유형별 검증 로직을 등록 가능한 핸들러로 분리
4. **return_to 필드** — resume 후 복귀할 노드를 명확화 요청 시 명시
5. **interrupt() 전 상태 스냅샷** — 공식 Best Practice: 사이드이펙트는 interrupt() 이후에 실행

### 4.2 ClarificationRequest 스키마

```python
# src/agents/state/clarification.py

from enum import Enum
from typing import Any, Literal
from pydantic import BaseModel, Field


class QuestionType(str, Enum):
    """명확화 질문의 렌더링 유형."""
    FREE_TEXT = "free_text"          # 자유 입력 텍스트박스
    SINGLE_SELECT = "single_select"  # 단일 선택 (라디오)
    MULTI_SELECT = "multi_select"    # 다중 선택 (체크박스)
    CONFIRM = "confirm"              # 예/아니오 확인


class ClarifyTrigger(str, Enum):
    """명확화를 유발한 원인 — 로깅 및 routing 용도."""
    HISTORY_UNSURE = "history_unsure"       # T1: resolve_history UNSURE
    INTENT_AMBIGUOUS = "intent_ambiguous"   # T2: classify_intent AMBIGUOUS
    SCHEMA_CONFLICT = "schema_conflict"     # T3: CONFLICTED knowledge_item
    DB_SOURCE_AMBIGUOUS = "db_source_ambiguous"  # T4: 교차 DB 감지
    FINALIZER_CONFLICT = "finalizer_conflict"    # T5: result_finalizer 재판정


class SelectOption(BaseModel):
    """선택지 항목."""
    value: str
    label: str
    description: str | None = None


class ClarificationRequest(BaseModel):
    """
    명확화 요청 페이로드.
    interrupt() 에 전달되는 값이자 프론트엔드 렌더링 명세.
    """
    trigger: ClarifyTrigger
    question_type: QuestionType
    message: str = Field(..., description="사용자에게 보여줄 질문 (비기술적 한국어)")
    options: list[SelectOption] | None = Field(
        None, description="SINGLE_SELECT/MULTI_SELECT 시 선택지"
    )
    context_summary: str | None = Field(
        None, description="현재까지 이해한 내용 요약 (SRA 패러다임의 Summarize 단계)"
    )
    return_to: str = Field(..., description="resume 후 복귀할 노드명")
    max_turns: int = Field(default=3, description="최대 명확화 왕복 횟수")
    validation_hint: str | None = Field(
        None, description="응답 검증 규칙 (핸들러가 사용)"
    )


class ClarificationResponse(BaseModel):
    """
    명확화 응답 페이로드.
    Command(resume=...) 로 전달되는 값.
    """
    trigger: ClarifyTrigger
    raw_answer: str | list[str]
    validated: bool = False
    normalized_value: Any = None  # 핸들러가 파싱 후 채움
```

### 4.3 Strategy 핸들러 인터페이스

```python
# src/agents/nodes/interpret/handlers.py

from abc import ABC, abstractmethod
from .clarification import ClarificationRequest, ClarificationResponse


class ClarificationHandler(ABC):
    """
    명확화 유형별 처리 전략 인터페이스.
    각 핸들러는 응답 검증과 정규화를 담당한다.
    """

    @property
    @abstractmethod
    def trigger(self) -> ClarifyTrigger:
        """이 핸들러가 처리하는 트리거 유형."""
        ...

    @abstractmethod
    def validate(self, response: ClarificationResponse,
                 request: ClarificationRequest) -> bool:
        """응답이 유효한지 검증. 실패 시 재질문 트리거."""
        ...

    @abstractmethod
    def normalize(self, response: ClarificationResponse,
                  request: ClarificationRequest) -> Any:
        """응답을 파이프라인 상태 업데이트에 적합한 형태로 정규화."""
        ...

    @abstractmethod
    def apply_to_state(self, state: "PipelineState",
                       response: ClarificationResponse) -> dict:
        """정규화된 응답을 파이프라인 상태 업데이트로 변환."""
        ...


class SchemaConflictHandler(ClarificationHandler):
    """
    T3: CONFLICTED knowledge_item — 테이블/컬럼 선택.
    사용자가 선택지 중 하나를 선택하면 해당 knowledge_item을 CONFIRMED로 전환.
    """

    @property
    def trigger(self) -> ClarifyTrigger:
        return ClarifyTrigger.SCHEMA_CONFLICT

    def validate(self, response: ClarificationResponse,
                 request: ClarificationRequest) -> bool:
        if request.options is None:
            return True
        valid_values = {opt.value for opt in request.options}
        answer = response.raw_answer
        if isinstance(answer, str):
            return answer in valid_values
        return all(a in valid_values for a in answer)

    def normalize(self, response: ClarificationResponse,
                  request: ClarificationRequest) -> str:
        """선택된 테이블/컬럼명을 반환."""
        if isinstance(response.raw_answer, list):
            return response.raw_answer[0]
        return str(response.raw_answer)

    def apply_to_state(self, state: "PipelineState",
                       response: ClarificationResponse) -> dict:
        """CONFLICTED knowledge_item을 CONFIRMED로 갱신."""
        selected_table = response.normalized_value
        updated_items = []
        for item in state["reasoning_state"].knowledge_items:
            if item.status == "CONFLICTED":
                # 선택된 후보만 CONFIRMED, 나머지 제거
                new_item = item.model_copy(update={
                    "status": "CONFIRMED",
                    "resolved_value": selected_table
                })
                updated_items.append(new_item)
            else:
                updated_items.append(item)
        return {"reasoning_state": {"knowledge_items": updated_items}}


class HistoryUnsureHandler(ClarificationHandler):
    """
    T1: resolve_history UNSURE — 대화 맥락 불분명.
    자유 입력을 받아 conversation_history에 명확화 컨텍스트로 추가.
    """

    @property
    def trigger(self) -> ClarifyTrigger:
        return ClarifyTrigger.HISTORY_UNSURE

    def validate(self, response: ClarificationResponse,
                 request: ClarificationRequest) -> bool:
        answer = str(response.raw_answer).strip()
        return len(answer) >= 2  # 최소 2자 이상

    def normalize(self, response: ClarificationResponse,
                  request: ClarificationRequest) -> str:
        return str(response.raw_answer).strip()

    def apply_to_state(self, state: "PipelineState",
                       response: ClarificationResponse) -> dict:
        return {
            "clarification_context": response.normalized_value,
            "awaiting_clarification": False
        }
```

### 4.4 HandlerRegistry

```python
# src/agents/nodes/interpret/registry.py

from typing import ClassVar


class ClarificationHandlerRegistry:
    """
    Strategy 패턴의 Context 역할.
    트리거 유형별 핸들러를 등록하고 조회한다.
    """
    _handlers: ClassVar[dict[ClarifyTrigger, ClarificationHandler]] = {}

    @classmethod
    def register(cls, handler: ClarificationHandler) -> None:
        cls._handlers[handler.trigger] = handler

    @classmethod
    def get(cls, trigger: ClarifyTrigger) -> ClarificationHandler:
        if trigger not in cls._handlers:
            raise ValueError(f"등록된 핸들러 없음: {trigger}")
        return cls._handlers[trigger]


# 애플리케이션 시작 시 등록 (FastAPI lifespan 또는 모듈 레벨)
_registry = ClarificationHandlerRegistry()
_registry.register(HistoryUnsureHandler())
_registry.register(IntentAmbiguousHandler())
_registry.register(SchemaConflictHandler())
_registry.register(DbSourceAmbiguousHandler())
_registry.register(FinalizerConflictHandler())
```

### 4.5 통합 명확화 노드

```python
# src/agents/graph/nodes/clarification_handler.py

from langgraph.types import interrupt
from ..state.state import PipelineState
from ..clarification.registry import ClarificationHandlerRegistry


async def clarification_handler_node(state: PipelineState) -> dict:
    """
    모든 명확화 트리거의 단일 진입점.

    설계 결정:
    - interrupt()는 항상 단 한 번만 호출 (인덱스 고정, 공식 권장)
    - interrupt() 이전에 사이드이펙트 없음 (재실행 안전성)
    - 응답 검증 실패 시 clarification_turns를 증가시키고 재interrupt
    - max_turns 초과 시 best-effort로 진행 (사용자 부담 최소화, DASG 원칙)
    """
    request: ClarificationRequest = state["pending_clarification"]
    handler = ClarificationHandlerRegistry.get(request.trigger)

    # interrupt() 이전: 순수 읽기만 수행 (사이드이펙트 없음)
    current_turns = state.get("clarification_turns", 0)

    if current_turns >= request.max_turns:
        # DASG 원칙: 명확화 비용이 너무 크면 best-effort로 진행
        return {
            "pending_clarification": None,
            "clarification_answer": None,
            "clarification_turns": 0,
        }

    # 단일 interrupt() 호출 — 항상 동일한 위치에서 호출되어야 함
    raw_answer = interrupt(request.model_dump())

    # interrupt() 이후: 사이드이펙트 및 상태 변환 수행
    response = ClarificationResponse(
        trigger=request.trigger,
        raw_answer=raw_answer,
    )

    if not handler.validate(response, request):
        # 검증 실패 — turns 증가 후 재진입 (노드가 다시 interrupt 호출)
        # 주의: 이 경로는 실제로 도달하지 않음.
        # Command(resume=...) 재전송으로 인해 노드 처음부터 재실행되므로
        # turns 증가를 state에 기록해두어야 한다.
        return {
            "clarification_turns": current_turns + 1,
            "pending_clarification": request,  # 동일 요청 유지
        }

    response.normalized_value = handler.normalize(response, request)
    response.validated = True

    state_update = handler.apply_to_state(state, response)
    state_update.update({
        "pending_clarification": None,
        "clarification_answer": response,
        "clarification_turns": 0,
    })

    return state_update
```

### 4.6 라우팅 통합

```python
# src/agents/graph/pipeline.py (관련 부분)

from langgraph.types import Command


def _route_after_clarification_handler(state: PipelineState) -> str:
    """
    명확화 완료 후 복귀 노드 결정.
    pending_clarification이 남아 있으면 재진입 (검증 실패 케이스).
    """
    if state.get("pending_clarification") is not None:
        # 검증 실패 또는 max_turns 미초과 재질문
        return "clarification_handler"

    request = state.get("clarification_answer")
    if request is None:
        # max_turns 초과 — best-effort 진행
        # return_to가 state에 없으면 confidence_evaluator로 기본값
        return state.get("clarification_return_to", "confidence_evaluator")

    return state.get("clarification_return_to", "confidence_evaluator")


# 각 명확화 트리거 노드에서의 상태 업데이트 패턴:
def _trigger_clarification(
    trigger: ClarifyTrigger,
    message: str,
    question_type: QuestionType,
    return_to: str,
    options: list[SelectOption] | None = None,
    context_summary: str | None = None,
) -> dict:
    """
    명확화 트리거 노드에서 호출하는 헬퍼.
    pending_clarification 상태를 설정하고 clarification_handler로 라우팅.
    """
    request = ClarificationRequest(
        trigger=trigger,
        question_type=question_type,
        message=message,
        options=options,
        context_summary=context_summary,
        return_to=return_to,
        max_turns=3,
    )
    return {
        "pending_clarification": request,
        "clarification_return_to": return_to,
    }
```

### 4.7 트리거 노드별 변경사항 (Before → After)

**T1: resolve_history — UNSURE 케이스**

```python
# Before (현재): 노드 내부에서 직접 응답 반환 후 종료
if resolution == "UNSURE":
    return {"response": "어떤 내용을 말씀하시는 건가요?", "done": True}

# After: pending_clarification 설정 후 clarification_handler로 라우팅
if resolution == "UNSURE":
    return _trigger_clarification(
        trigger=ClarifyTrigger.HISTORY_UNSURE,
        message="이전 대화 맥락이 불분명합니다. 어떤 데이터를 원하시는지 구체적으로 말씀해 주세요.",
        question_type=QuestionType.FREE_TEXT,
        return_to="resolve_history",
        context_summary=f"이전 대화: {state['conversation_history'][-1]['content'][:100]}",
    )
```

**T3: confidence_evaluator — ASK_USER (CONFLICTED)**

```python
# Before (현재): result_finalizer에 위임
if decision == "ASK_USER":
    return {"action": "ASK_USER", "conflicted_items": conflicted}

# After: 즉시 명확화 요청 구성
if decision == "ASK_USER":
    conflicted_item = conflicted_items[0]  # 우선순위 1개씩 처리
    options = [
        SelectOption(value=c.table_name, label=c.table_desc, description=c.data_range)
        for c in conflicted_item.candidates
    ]
    return _trigger_clarification(
        trigger=ClarifyTrigger.SCHEMA_CONFLICT,
        message=f"'{conflicted_item.term}'에 해당하는 테이블이 여러 개 있습니다. 어떤 데이터를 원하시나요?",
        question_type=QuestionType.SINGLE_SELECT,
        return_to="confidence_evaluator",  # resume 후 재판정
        options=options,
        context_summary=f"현재까지 파악된 조건: {state['normalized_query'].time_range}",
    )
```

**T4: sql_generator — 교차 DB 감지**

```python
# Before (현재): 노드 내 직접 응답
return {"error": "어떤 DB를 사용할지 선택해주세요", "done": True}

# After
db_options = [
    SelectOption(value="postgresql", label="정보계 PostgreSQL", description="실시간 데이터"),
    SelectOption(value="impala", label="Impala (DW)", description="집계/이력 데이터"),
]
return _trigger_clarification(
    trigger=ClarifyTrigger.DB_SOURCE_AMBIGUOUS,
    message="요청하신 데이터가 여러 시스템에 있습니다. 어떤 시스템의 데이터를 원하시나요?",
    question_type=QuestionType.SINGLE_SELECT,
    return_to="sql_generator",
    options=db_options,
)
```

### 4.8 그래프 구조 변경

```python
# pipeline.py — 그래프 빌더 수정

# 기존 clarify 노드 교체
builder.add_node("clarification_handler", clarification_handler_node)

# 조건부 엣지
builder.add_conditional_edges(
    "resolve_history",
    _route_after_resolve_history,
    {
        "CONTINUE": "classify_intent",
        "NEW": "classify_intent",
        "UNSURE": "clarification_handler",   # 변경: clarify → clarification_handler
    }
)

builder.add_conditional_edges(
    "classify_intent",
    _route_after_classify_intent,
    {
        "DATA": "normalize_query",
        "AMBIGUOUS": "clarification_handler",  # 변경: clarify → clarification_handler
        "ERROR": "error_end",
    }
)

builder.add_conditional_edges(
    "confidence_evaluator",
    _route_after_confidence_evaluator,
    {
        "EXPLORE": "context_explorer",
        "GENERATE": "sql_generator",
        "REPLAN": "recovery_planner",
        "TERMINATE": "result_finalizer",
        "ASK_USER": "clarification_handler",   # 변경: result_finalizer → clarification_handler
    }
)

# clarification_handler 복귀 라우팅
builder.add_conditional_edges(
    "clarification_handler",
    _route_after_clarification_handler,
    {
        "clarification_handler": "clarification_handler",      # 재질문
        "resolve_history": "resolve_history",       # T1 복귀
        "classify_intent": "classify_intent",       # T2 복귀
        "confidence_evaluator": "confidence_evaluator",  # T3 복귀
        "sql_generator": "sql_generator",           # T4 복귀
        "result_finalizer": "result_finalizer",     # T5 복귀
    }
)
```

---

## 5. 기각된 대안과 이유

### 5.1 기각: 다중 interrupt() 단일 노드

단일 `clarification_handler` 노드 안에서 유형별로 분기하여 여러 `interrupt()` 호출:

```python
# 기각된 패턴
if request.trigger == ClarifyTrigger.SCHEMA_CONFLICT:
    answer = interrupt(request_a)  # index 0
elif request.trigger == ClarifyTrigger.HISTORY_UNSURE:
    answer = interrupt(request_b)  # index 0 — 충돌!
```

**기각 이유**: LangChain 공식 문서 명시 — "interrupt calls should happen in the same order every time, and you should not conditionally skip interrupt calls within a node." 조건 분기로 인해 인덱스가 변동되면 resume 시 잘못된 값이 매핑된다.

### 5.2 기각: interrupt_before/interrupt_after (정적 interrupt)

각 노드에 `interrupt_before` 컴파일 옵션으로 정적 중단점 설정.

**기각 이유**: 정적 interrupt는 노드의 비즈니스 로직과 무관하게 항상 중단된다. "CONFLICTED 항목이 있을 때만 중단"과 같은 조건부 트리거가 불가능하다. 동적 interrupt()가 이 용도에 적합하다.

### 5.3 기각: 별도 LangGraph 서브그래프 (단기)

명확화를 완전히 독립된 서브그래프로 분리하는 방식.

**기각 이유**:
- 부모-자식 상태 타입 매핑 오버헤드가 현재 `PipelineState` 크기(~30 필드) 대비 과도함
- LangGraph 공식 문서의 서브그래프 interrupt 이슈(GitHub #1222, #3562)에서 보고된 상태 동기화 복잡성
- 단기적으로 패턴 D(Strategy + 단일 노드)가 동일한 목표를 더 낮은 복잡도로 달성 가능
- **장기 로드맵**: 명확화 왕복이 3회 이상 필요한 복잡 케이스(예: 다단계 스키마 탐색)가 증가하면 서브그래프로 전환 고려

### 5.4 기각: 세션 기반 외부 큐 패턴

interrupt() 대신 Redis 큐에 명확화 요청을 적재하고 웹소켓 외부에서 관리.

**기각 이유**: LangGraph 체크포인터가 이미 상태 직렬화를 담당한다. 외부 큐를 추가하면 "명확화 요청 적재 후 파이프라인 상태 저장" 사이의 원자성이 깨진다. LangGraph interrupt()가 이 문제를 체크포인터 단일 트랜잭션으로 해결한다.

---

## 6. 한국 금융 도메인 특화 고려사항

### 6.1 금융 용어 모호성 클래스 (T3 SCHEMA_CONFLICT 특화)

BAR-SQL의 4-카테고리를 금융 도메인에 매핑:

| BAR-SQL 카테고리 | 금융 도메인 예시 | 권장 QuestionType |
|-----------------|----------------|-----------------|
| Ambiguity Clarification | "여신잔액" → LN_BAL_D(일) vs LN_BAL_M(월) | SINGLE_SELECT |
| Constraint Follow-Up | "최근 실적" → 기간 미지정 | FREE_TEXT or SINGLE_SELECT (범위 선택) |
| Knowledge Rejection | "BIS비율" → 업무 매뉴얼 확인 필요 | FREE_TEXT (확인 후 재질의 안내) |
| Dimension Rejection | 존재하지 않는 컬럼명 | CONFIRM (유사 항목 제안) |

### 6.2 사용자 경험 원칙 (user-interaction.md 준수)

1. **명확화 질문은 최대 2~3개 선택지**: `max_turns=3`, 선택지는 `options` 최대 4개
2. **SQL 구문 노출 금지**: `message` 필드에 테이블명/컬럼명이 아닌 업무 용어 사용
   - BAD: "LN_BAL_D 또는 LN_BAL_M 중 선택하세요"
   - GOOD: "일별 잔액과 월별 잔액 중 어떤 데이터를 원하시나요?"
3. **context_summary 활용**: SRA 패러다임의 Summarize 단계 — "현재까지 파악된 내용: 이번달 여신, 서울지점"

### 6.3 폐쇄망 LLM 특화

Solar Pro 2 70B / Qwen3.5 등 오픈소스 모델은 JSON 출력 안정성이 낮다. `ClarificationRequest` 생성 시 LLM을 거치는 경우 structured output을 강제해야 한다.

```python
# 명확화 질문 생성 시 structured output 강제
response = await llm.ainvoke(
    prompt,
    response_format={"type": "json_object"},  # Qwen/Solar 대응
)
request = ClarificationRequest.model_validate_json(response.content)
```

---

## 7. 구현 로드맵

| 단계 | 작업 | 우선순위 | 예상 공수 |
|------|------|---------|---------|
| 1 | `ClarificationRequest` / `ClarificationResponse` Pydantic 모델 정의 | 높음 | 0.5일 |
| 2 | `ClarificationHandler` ABC + `HandlerRegistry` 구현 | 높음 | 1일 |
| 3 | `clarification_handler_node` 구현 + 기존 `clarify` 노드 교체 | 높음 | 1일 |
| 4 | T1(resolve_history), T2(classify_intent) 트리거 마이그레이션 | 높음 | 0.5일 |
| 5 | T3(confidence_evaluator ASK_USER) 트리거 마이그레이션 | 높음 | 1일 |
| 6 | T4(sql_generator 교차 DB), T5(result_finalizer) 마이그레이션 | 중간 | 1일 |
| 7 | 프론트엔드 — `ClarificationRequest` 스키마 기반 동적 렌더링 | 중간 | 2일 |
| 8 | 골든셋 테스트 — 각 트리거 케이스별 resume 정확성 검증 | 높음 | 1일 |

**총 예상 공수: 8일**

---

## 8. 핵심 권고 요약

1. **모든 명확화는 `clarification_handler` 노드 단일 경유** — interrupt() 호출 위치 고정
2. **`ClarificationRequest` 스키마가 UI 렌더링 명세를 포함** — 프론트엔드가 유형별 분기 처리 불필요
3. **Strategy 패턴으로 핸들러 분리** — 새 트리거 추가 시 핸들러 1개 등록만으로 완료
4. **`return_to` 필드로 resume 복귀 지점 명시** — 체크포인터 상태에 안전하게 보존
5. **interrupt() 전 사이드이펙트 없음** — 노드 재실행 안전성 보장 (공식 Best Practice)
6. **max_turns + DASG 비용 모델** — 명확화 3회 초과 시 best-effort 진행, 사용자 부담 최소화
7. **금융 용어로 message 작성** — SQL/테이블명 노출 금지 (user-interaction.md 준수)

---

## 참고 문헌

### Tier 1 논문
- Zhao, F. et al. (2025). *Sphinteract: Resolving Ambiguities in NL2SQL Through User Interaction*. PVLDB Vol.18. https://dl.acm.org/doi/10.14778/3717755.3717772
- BAR-SQL Team (2025). *Boundary-Aware NL2SQL: Integrating Reliability through Hybrid*. arXiv:2601.10318. https://arxiv.org/pdf/2601.10318
- DASG Team (2025). *Data-Aware Socratic Query Refinement in Database Systems*. arXiv:2508.05061. https://arxiv.org/html/2508.05061
- Schafer et al. (2024). *Human-In-the-Loop Software Development Agents*. arXiv:2411.12924. https://arxiv.org/abs/2411.12924

### 공식 문서 / 기술 문서
- LangChain. (2025). *Interrupts — LangGraph Documentation*. https://docs.langchain.com/oss/python/langgraph/interrupts
- LangChain Blog. (2025). *Making it easier to build human-in-the-loop agents with interrupt*. https://blog.langchain.com/making-it-easier-to-build-human-in-the-loop-agents-with-interrupt/
- Cloudflare. (2025). *Human-in-the-loop patterns — Cloudflare Agents*. https://developers.cloudflare.com/agents/guides/human-in-the-loop/
- De, S. (2025). *LangGraph Best Practices*. https://www.swarnendu.de/blog/langgraph-best-practices/
- Chandravanshi, P. (2025). *LangGraph HITL Design Patterns: Multiple Interrupts*. https://medium.com/fundamentals-of-artificial-intelligence/langgraph-hitl-design-patterns-multiple-interrupts-45fc9b549ec5
