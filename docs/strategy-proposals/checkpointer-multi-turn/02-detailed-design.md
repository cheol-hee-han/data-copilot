# Checkpointer 도입 — 상세 구현 설계

- **작성일**: 2026-03-30
- **최종 갱신**: 2026-03-31 (v4: 2계층 판정 + AmbiSQL 7종 분류 + 가드레일 통합)
- **상위 문서**: `01-strategy.md`
- **구현 순서**: Phase 1 → Phase 2 → Phase 3 → Phase 4

---

## Phase 1: Core Checkpointer 연결

### 1.1 설정 추가 — `config.py`

> **설계 원칙 (R-06 반영)**:
> - 5개 개별 폴백 필드(host/port/name/user/password) → **Value Object** `DbConnectionInfo` + `dedicated_db: ... | None`으로 축소
> - `""`, `0` 매직 값 제거 — `None`이 "설정 안 함"을 타입으로 명시
> - 폐쇄망에서 별도 DB가 필요한 경우에도 대응 가능하되, 기본값은 history_db 공유

```python
from pydantic import BaseModel, BaseSettings


class DbConnectionInfo(BaseModel):
    """DB 연결에 필요한 최소 정보 묶음 (Value Object)."""
    host: str
    port: int
    name: str
    user: str
    password: str

    @property
    def dsn(self) -> str:
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"


class CheckpointerConfig(BaseModel):
    """Checkpointer 설정 — 별도 DB는 선택적."""
    backend: str = "memory"                        # "memory" | "postgres"
    dedicated_db: DbConnectionInfo | None = None   # None이면 history_db 공유
    pool_min: int = 2
    pool_max: int = 10
    thread_ttl_days: int = 30                      # 0=무제한

    def resolve_db(self, history_db: DbConnectionInfo) -> DbConnectionInfo:
        """dedicated_db가 없으면 history_db를 그대로 사용."""
        return self.dedicated_db or history_db


class Settings(BaseSettings):
    # ... 기존 설정 ...

    # ── History DB (필수) ──
    history_db: DbConnectionInfo  # 기존 history_db 설정을 Value Object로 통합

    # ── Checkpointer ──
    checkpointer: CheckpointerConfig = CheckpointerConfig()
```

**설계 근거**: `dedicated_db: ... | None`으로 "전부 주거나 / 아예 안 주거나"를 타입 레벨에서 강제.
반쪽짜리 설정(host만 있고 password 없음)이 구조적으로 불가능. 폐쇄망에서 별도 DB가 필요하면 `dedicated_db` 블록을 설정하면 됨.

### 1.2 Checkpointer 팩토리 — `src/agents/graph/checkpointer.py` (신규)

> **설계 원칙 (R-06 반영)**:
> - 전역 mutable 싱글턴(`_checkpointer`, `_pool`) 제거 → Lifespan에서 직접 생성, DI로 전달
> - `create_checkpointer()` async context manager로 생명주기 관리
> - `Any` 타입 → `BaseCheckpointSaver` 타입 명시

```python
"""Checkpointer 팩토리 — 전역 상태 없는 순수 함수.

생명주기:
    - FastAPI lifespan에서 async context manager로 사용
    - 테스트에서는 MemorySaver 직접 사용 (setup/teardown 불필요)
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from langgraph.checkpoint.base import BaseCheckpointSaver

from src.config import CheckpointerConfig, DbConnectionInfo
from src.utils.logger import get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def create_checkpointer(
    config: CheckpointerConfig,
    history_db: DbConnectionInfo,
) -> AsyncIterator[BaseCheckpointSaver]:
    """설정에 따라 checkpointer를 생성하고 관리한다.

    AsyncContextManager — lifespan에서 async with로 사용.
    리소스 정리가 자동으로 보장된다.
    """
    if config.backend == "postgres":
        from psycopg_pool import AsyncConnectionPool
        from psycopg.rows import dict_row
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

        db = config.resolve_db(history_db)

        connection_kwargs = {
            "autocommit": True,       # 필수: psycopg3 기본값 False
            "prepare_threshold": 0,   # PgBouncer/pooler 호환
            "row_factory": dict_row,
        }

        pool = AsyncConnectionPool(
            conninfo=db.dsn,
            min_size=config.pool_min,
            max_size=config.pool_max,
            kwargs=connection_kwargs,
        )
        await pool.open()
        await pool.wait()

        # R-12 반영: 패키지 레벨로 일괄 허용
        serde = JsonPlusSerializer().with_msgpack_allowlist([
            ("src.",),  # src 패키지 전체 허용
        ])

        checkpointer = AsyncPostgresSaver(pool, serde=serde)
        await checkpointer.setup()

        logger.info("Checkpointer 초기화: AsyncPostgresSaver", host=db.host)
        try:
            yield checkpointer
        finally:
            await pool.close()
            logger.info("Checkpointer 리소스 정리 완료")
    else:
        from langgraph.checkpoint.memory import MemorySaver
        logger.info("Checkpointer 초기화: MemorySaver")
        yield MemorySaver()
```

### 1.3 Pipeline 수정 — `pipeline.py`

```python
# 변경점: create_app()에 checkpointer 인자 추가

def create_app(checkpointer: Any = None) -> Any:
    """컴파일된 LangGraph 앱을 생성한다.

    Args:
        checkpointer: LangGraph checkpointer 인스턴스.
            None이면 체크포인트 없이 컴파일 (하위 호환).
    """
    workflow = build_pipeline()
    return workflow.compile(checkpointer=checkpointer)


# 싱글턴도 checkpointer를 주입받도록 변경
_compiled_app: Any = None


def get_compiled_app(checkpointer: Any = None) -> Any:
    """컴파일된 LangGraph 앱 싱글턴을 반환한다.

    최초 호출 시 checkpointer를 주입하여 컴파일한다.
    이후 호출에서는 checkpointer 인자를 무시하고 캐시된 앱을 반환한다.
    """
    global _compiled_app
    if _compiled_app is None:
        _compiled_app = create_app(checkpointer=checkpointer)
        logger.info("LangGraph 파이프라인 컴파일 완료 (싱글턴)")
    return _compiled_app
```

### 1.4 Runner 수정 — `runner.py` (Phase 1 최소 변경)

```python
# 변경점: thread_id를 config에 주입 (Phase 1에서는 이것만)

async def run_pipeline(
    user_input: str,
    session_id: str | None = None,
    conversation_history: list[dict[str, str]] | None = None,
    *,
    clarification_state: dict[str, Any] | None = None,
    on_event: OnEventCallback | None = None,
) -> PipelineResult:
    # ... 기존 초기화 ...

    app = get_compiled_app()

    # 체크포인터 config 구성 — thread_id = session_id
    config: dict[str, Any] = {
        "callbacks": [handler],
    }
    if session_id:
        config["configurable"] = {"thread_id": session_id}

    result = await app.ainvoke(
        initial_state,
        config=config,
    )

    # ... 기존 후처리 ...
```

### 1.5 Lifespan 수정 — `main.py`

```python
from src.agents.graph.checkpointer import create_checkpointer
from src.config import settings
from src.agents.graph.pipeline import get_compiled_app


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    setup_langsmith()

    # 커넥터 초기화
    manager = get_connector_manager()
    await manager.connect_all()

    # 세션 스토어 초기화
    store = get_session_store()
    await store.connect()

    # Checkpointer 초기화 + 그래프 컴파일 (R-06: DI, async context manager)
    async with create_checkpointer(settings.checkpointer, settings.history_db) as checkpointer:
        get_compiled_app(checkpointer=checkpointer)

        logger.info("서버 시작 완료")
        yield

    # create_checkpointer의 __aexit__에서 pool.close() 자동 정리
    await store.disconnect()
    await manager.disconnect_all()
    logger.info("서버 종료")
```

### 1.6 직렬화 검증 테스트

> **검증 완료** (LangGraph 1.1.2, Pydantic v2):
> Pydantic BaseModel + `Annotated[list[X], operator.add]` reducer 조합이
> MemorySaver, JsonPlusSerializer(PostgresSaver 내부 serde), interrupt/resume 시나리오 모두에서 정상 동작 확인됨.
> 단, 체크포인터 역직렬화 시 커스텀 Pydantic 모델에 대해 `allowed_msgpack_modules` 설정이 필요하며,
> 미설정 시 경고 후 **향후 버전에서 차단 예정**.

```python
# tests/unit/test_state_serialization.py

import operator
from typing import Annotated

import pytest
from langgraph.checkpoint.memory import MemorySaver

from src.agents.graph.pipeline import build_pipeline
from src.agents.state.state import PipelineState, ReasoningState


@pytest.mark.asyncio
async def test_pipeline_state_checkpoint_roundtrip():
    """PipelineState가 checkpointer를 통해 직렬화/역직렬화되는지 검증."""
    checkpointer = MemorySaver()
    workflow = build_pipeline()
    app = workflow.compile(checkpointer=checkpointer)

    config = {"configurable": {"thread_id": "test-roundtrip"}}

    state = PipelineState(user_input="테스트 질의", session_id="test")

    result = await app.ainvoke(state, config=config)

    # 체크포인트에서 상태 복원
    saved_state = await app.aget_state(config)
    assert saved_state is not None
    assert saved_state.values.get("user_input") == "테스트 질의"


@pytest.mark.asyncio
async def test_reasoning_state_serialization():
    """ReasoningState의 중첩 Pydantic 모델 직렬화를 검증."""
    from src.agents.state.state import (
        KnowledgeItem, Hypothesis, CandidateTable, LoopGuard,
        ConfidenceStatus, HypothesisStatus,
    )
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    serde = JsonPlusSerializer()

    reason = ReasoningState(
        knowledge_items=[
            KnowledgeItem(
                key="table:TB_CUST",
                value="고객 마스터",
                status=ConfidenceStatus.CONFIRMED,
            ),
        ],
        hypotheses=[
            Hypothesis(
                hypothesis_id="h1",
                description="테스트 가설",
                status=HypothesisStatus.ACTIVE,
            ),
        ],
        candidate_tables=[
            CandidateTable(
                table_name="TB_CUST",
                description="고객 기본정보",
            ),
        ],
        loop_guard=LoopGuard(total_tool_calls=5),
    )

    # 직렬화 → 역직렬화 왕복 (dumps_typed/loads_typed가 실제 API)
    serialized = serde.dumps_typed(reason)
    deserialized = serde.loads_typed(serialized)

    assert deserialized.knowledge_items[0].key == "table:TB_CUST"
    assert deserialized.loop_guard.total_tool_calls == 5


@pytest.mark.asyncio
async def test_annotated_reducer_with_checkpoint():
    """Annotated reducer가 체크포인터 환경에서 정상 동작하는지 검증.

    노드가 [new_item]만 반환해도 기존 리스트에 자동 append되어야 한다.
    수동 append(list(state.field) + [new]) 패턴이 불필요함을 확인.
    """
    from pydantic import BaseModel, Field
    from langgraph.graph import StateGraph, START, END

    class Signal(BaseModel):
        source: str
        category: str

    class TestState(BaseModel):
        signals: Annotated[list[Signal], operator.add] = Field(default_factory=list)

    def node_a(state: TestState) -> dict:
        return {"signals": [Signal(source="a", category="ASK")]}

    def node_b(state: TestState) -> dict:
        return {"signals": [Signal(source="b", category="INFER")]}

    g = StateGraph(TestState)
    g.add_node("a", node_a)
    g.add_node("b", node_b)
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_edge("b", END)

    cp = MemorySaver()
    app = g.compile(checkpointer=cp)
    config = {"configurable": {"thread_id": "reducer-test"}}

    await app.ainvoke({}, config=config)
    state = await app.aget_state(config)

    signals = state.values["signals"]
    assert len(signals) == 2  # append됨, overwrite 아님
    assert signals[0]["source"] == "a" or signals[0].source == "a"
    assert signals[1]["source"] == "b" or signals[1].source == "b"


@pytest.mark.asyncio
async def test_allowed_msgpack_modules():
    """커스텀 Pydantic 모델 역직렬화 시 allowed_msgpack_modules 설정 검증."""
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    serde = JsonPlusSerializer().with_msgpack_allowlist([
        ("src.agents.state.state",),
        ("src.agents.models.clarification",),
    ])
    # allowlist가 정상 설정되었는지 확인
    assert serde._allowed_msgpack_modules is not None
```

---

## Phase 2: Unified Clarification + 2계층 판정 + 순수 interrupt

### 2.1 핵심 아키텍처

**모든 명확화를 단일 `clarification_handler` 노드 + `interrupt()`로 통합하고,**
**LLM 판정 + 규칙 가드레일 2계층 구조**로 ASK/INFER를 결정한다.
기존 `preprocess` 노드는 제거하고, 보안 검증(`sanitize`)은 `runner.py`로 이동한다.

```text
[각 노드 LLM] ─ 업무 수행 중 모호함 발견
  │  ① 감지 + ② 분류(AmbiguityType) + ③ ASK/INFER 판정 + ④ 근거
  │  → AmbiguitySignal 생성 → state.pending_signals에 추가
  ▼
[clarification_handler 노드 진입]
  │  → _should_override_to_ask(): INFER→ASK 단방향 보정 (LLM 호출 0)
  │  → ASK/INFER 분리
  │
  ├─ INFER → resolved_signals에 누적, 진행 (결과 상단에 추론 근거 안내)
  └─ ASK → 우선순위로 1개 선택 → interrupt(signal)
          → 그래프 중단 (체크포인트 자동 저장)

[사용자 응답]
            → run_pipeline() → sanitize(user_input)
            → aget_state() → interrupt 대기 중 감지
            → Command(resume=sanitized_text)
            → clarification_handler 재개 → validate_answer() 검증
            → resolved_signals에 누적 (original_query 보존)
            → source_node 필드로 원래 노드 복귀
```

### 2.2 Clarification 스키마 정의 — `src/agents/models/clarification.py` (신규)

> **설계 원칙 (R-02 반영)**: 모호성의 전체 생명주기(감지→가드레일→ASK/INFER→해소)를
> **AmbiguitySignal 단일 모델**로 관리한다.
> 기존 4단계 변환 체인(UncertaintySignal→ClarificationRequest→ClarificationEntry→AuditEntry)을
> 제거하고, `decision`과 `answer` 유무로 상태를 판별한다.

```python
"""Unified Clarification Framework 스키마 정의.

단일 AmbiguitySignal 모델이 모호성의 전체 생명주기를 커버한다:
  감지 → 가드레일 보정 → ASK(interrupt→응답) / INFER(자동추론)

프론트엔드는 interrupt 페이로드의 question_type으로 UI를 자동 렌더링한다.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


# ── 모호성 분류 (AmbiSQL 7종, 명칭 단순화) ──


class AmbiguityType(str, StrEnum):
    """모호성 유형 분류 — AmbiSQL(arXiv 2508.15276) 기반, 단일 영어 대문자로 재명명.

    LLM JSON 출력 오기(typo) 방지 + 금융 도메인 의미 직결.
    """

    TABLE = "TABLE"          # AmbiSchema: 테이블/컬럼 참조 모호
    INTENT = "INTENT"        # AmbiIntent: 의도/연산 방식 모호
    VALUE = "VALUE"          # AmbiValue: 코드값 매칭 실패
    FORMULA = "FORMULA"      # AmbiSource: 산출식 출처 모호
    TIMEFRAME = "TIMEFRAME"  # AmbiRef: 기간/시점 모호
    CONTEXT = "CONTEXT"      # AmbiContext: 추론 근거 부족
    CONFLICT = "CONFLICT"    # AmbiFallacy: 모순된 전제


class ConfidenceLevel(str, StrEnum):
    """LLM의 판정 확신도 — float 대신 이산값으로 제한.

    근거: LLM self-calibration 부정확(arXiv 2508.14056),
    모델 교체 시(Solar→Qwen) float 임계값 재튜닝 불필요.
    """

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class QuestionType(StrEnum):
    """명확화 질문 유형 — 프론트엔드 UI 렌더링 기준."""

    FREE_TEXT = "free_text"          # 자유 입력 텍스트박스
    SINGLE_SELECT = "single_select"  # 라디오 버튼 / 선택지
    CONFIRM = "confirm"              # 예/아니오 확인


# ── 단일 모델: AmbiguitySignal ──


class AmbiguitySignal(BaseModel):
    """모호성의 전체 생명주기를 하나의 객체로 관리.

    감지 → 가드레일 보정 → ASK(interrupt→응답) / INFER(자동추론)
    모두 이 모델 하나로 처리한다. decision과 answer 유무로 상태를 판별.

    왜 단일 모델인가:
    1. ASK/INFER 혼재: 노드가 [signal_ASK, signal_INFER]를 한꺼번에 반환 — 타입이 같아야 하나의 리스트에 담김
    2. 가드레일 보정: INFER→ASK 변환 시 필드 변경(decision="ASK")만으로 충분
    3. 감사 추적: 하나의 객체에 감지~해소 전 과정이 기록됨 — 별도 AuditEntry 불필요
    4. interrupt 페이로드: model_dump(include=_INTERRUPT_FIELDS) — 변환 함수 불필요
    """

    # ── 감지 시점 (노드가 설정) ──
    source_node: str                                # 발생 노드명
    ambiguity_type: AmbiguityType                   # 7종 분류
    decision: Literal["ASK", "INFER"]               # LLM의 판정
    confidence: ConfidenceLevel                     # 판정 확신도
    question: str                                   # DTE 패턴: "왜 묻는지" + 무엇이 모호한지
    question_type: QuestionType = QuestionType.FREE_TEXT  # 프론트엔드 UI 렌더링 기준
    options: list[str] = Field(default_factory=list) # 후보 (테이블명, 코드값 등)
    inferred_value: str | None = None               # INFER 시 추론값
    reasoning: str = ""                             # 판정 근거 (한국어)
    override_reason: str | None = None              # 가드레일 보정 시 사유

    # ── 해소 시점 (clarification_handler가 설정) ──
    answer: str | None = None                       # ASK: interrupt resume 후 채워짐
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

#### 필드 용도 분기 — ASK vs INFER

동일 모델의 `decision` 필드를 기준으로 용도가 달라진다:

```text
AmbiguitySignal
├── decision == "ASK"  (명확화 Q&A)
│   ├── question       → 사용자에게 보여지는 질문
│   ├── options        → 선택지 (있으면 SINGLE_SELECT)
│   ├── answer         → 사용자 응답 ← interrupt 후 채워짐
│   ├── source_node    → 복귀 노드 (return_to 역할)
│   └── ambiguity_type → 감사 추적
│
└── decision == "INFER"  (자동 추론 결과)
    ├── question       → "무엇이 모호했는지" 설명
    ├── inferred_value → 추론된 값 (프롬프트 + 사용자 안내)
    ├── reasoning      → 추론 근거 (프롬프트 + 사용자 안내)
    ├── source_node    → 어떤 노드에서 추론했는지
    └── answer         → None (사용자 개입 없음)
```

| 필드 | ASK (명확화 Q&A) | INFER (자동 추론) |
| ---- | ---- | ---- |
| `question` | 사용자에게 한 질문 | 무엇이 모호했는지 설명 |
| `options` | 선택지 | (보통 비어있음) |
| `answer` | 사용자 응답 (채워짐) | `None` (사용자 개입 없음) |
| `inferred_value` | (보통 `None`) | 추론된 값 |
| `reasoning` | 질문 배경 (DTE) | 추론 근거 |
| `display_value` | `answer` 반환 | `inferred_value` 반환 |

### 2.3 State 필드 변경 — `state.py`

> **설계 원칙 (R-03, R-05 반영)**:
> - 신규 필드를 **3개**(`original_query`, `pending_signals`, `resolved_signals`)로 최소화
> - `pending_clarification`, `clarification_return_to`, `selected_db_source`, `user_schema_selection` 제거
>   — 복귀 노드는 `resolved_signals[-1].source_node`에서 도출, 핸들러 전용 필드는 `resolved_signals`의 answer에서 LLM이 참조
> - `_add_or_clear` 커스텀 reducer 제거 — `pending_signals`는 일반 필드(덮어쓰기), `resolved_signals`는 `operator.add`(누적)

```python
from typing import Annotated
import operator

from src.agents.models.clarification import AmbiguitySignal
from src.models.context import NormalizedQuery  # 기존 import


class PipelineState(BaseModel):
    # ... 기존 필드 ...

    # [신규] 원본 질의 (immutable, 명확화 시에도 수정 금지)
    original_query: str = ""

    # [신규] 현재 턴의 미처리 시그널 — 노드가 반환, clarification_handler가 소비 후 [] 로 비움
    # 일반 필드 (reducer 없음, 덮어쓰기) → 커스텀 reducer 불필요
    pending_signals: list[AmbiguitySignal] = Field(default_factory=list)

    # [신규] 처리 완료된 시그널 누적 — ASK(answer 채워짐) + INFER 모두 append
    # operator.add: LangGraph 표준 누적 패턴
    resolved_signals: Annotated[list[AmbiguitySignal], operator.add] = Field(default_factory=list)

    # [변경] Any → 정확한 타입
    normalized_query: NormalizedQuery | None = None

    # [제거] clarification_origin — resolved_signals[-1].source_node로 대체
    # [제거] clarification_response — AmbiguitySignal.answer로 대체
    # [제거] clarification_turns — len([s for s in resolved_signals if s.decision == "ASK"])로 대체
    # [제거] pending_clarification — interrupt 페이로드가 역할 대체
    # [제거] clarification_return_to — resolved_signals[-1].source_node에서 도출
    # [제거] selected_db_source — 복귀 노드 LLM이 resolved_signals에서 참조
    # [제거] user_schema_selection — 복귀 노드 LLM이 resolved_signals에서 참조
    # [제거] uncertainty_signals — pending_signals로 대체 (커스텀 reducer 제거)
    # [제거] auto_resolved — [s for s in resolved_signals if s.decision == "INFER"]로 도출
```

> **Reducer 사용 규칙**:
> - `pending_signals` — reducer 없음 (덮어쓰기). 노드가 `[signal]` 반환 → 덮어쓰기. clarification_handler가 `[]` 반환 → 비워짐.
> - `resolved_signals` — `operator.add` (누적 전용). ASK(answer 채워짐) + INFER 모두 append.
> - 커스텀 reducer(`_add_or_clear`) **사용하지 않음** — LangGraph 표준 패턴만 사용.
> - 수동 append(`list(state.field) + [new]`) 패턴 **사용 금지** — reducer가 대체함.

**필드 설계 원칙**:

| 필드 | 역할 | 불변/가변 |
| ---- | ---- | --------- |
| `original_query` | 사용자 최초 질의 원본 | **불변** — 감사 추적용 |
| `user_input` | 현재 턴 입력 (첫 턴: 질의, 이후: 명확화 응답) | 턴마다 갱신 |
| `preprocessed_input` | sanitize 처리된 입력 | 턴마다 갱신 |
| `pending_signals` | 노드별 모호성 시그널 (clarification_handler 입력) | 덮어쓰기 (일반 필드) |
| `resolved_signals` | 해소 완료 시그널 누적 (ASK+INFER 모두) | append only (`operator.add`) |

**도출 가능한 값** (별도 필드 불필요):

| 기존 필드 | 도출 방법 |
| ---- | ---- |
| `clarification_return_to` | `resolved_signals[-1].source_node` |
| `clarification_turns` | `len([s for s in resolved_signals if s.decision == "ASK"])` |
| `auto_resolved` | `[s for s in resolved_signals if s.decision == "INFER"]` |
| `selected_db_source` | 복귀 노드 LLM이 `resolved_signals`의 answer 참조 |

### 2.4 응답 검증 함수 — `clarification_handler.py` 내 인라인

> **설계 원칙 (R-01 반영)**: Handler ABC + Strategy 패턴 + HandlerRegistry를 제거한다.
> 실제 행동은 FREE_TEXT vs SINGLE_SELECT 2가지뿐이므로,
> `question_type` 기반 분기의 단일 함수로 충분하다.
> `apply_to_state()`는 7개 중 6개가 `{}`를 반환했으므로 제거한다.
> 복귀 노드의 LLM이 `resolved_signals`를 참조하여 스스로 재판단한다.

```python
# clarification_handler.py 내 인라인 함수

from src.agents.models.clarification import AmbiguitySignal, QuestionType


def validate_answer(answer: str, signal: AmbiguitySignal) -> str:
    """사용자 응답을 검증한다.

    question_type 기반 분기 (2가지: FREE_TEXT vs SINGLE_SELECT).
    AmbiguityType별 핸들러가 아니라 QuestionType별 검증이 본질.

    Args:
        answer: 사용자 원본 응답.
        signal: 원래 명확화 시그널.

    Returns:
        정규화된 응답 문자열.

    Raises:
        ValueError: 응답이 유효하지 않은 경우 (재질문 필요).
    """
    answer = answer.strip()
    if not answer:
        raise ValueError("응답이 비어있습니다.")

    if signal.question_type == QuestionType.SINGLE_SELECT and signal.options:
        for i, opt in enumerate(signal.options, 1):
            if answer == str(i) or answer == opt:
                return opt
        raise ValueError(
            f"선택지 중에서 골라주세요: "
            f"{', '.join(f'{i}) {o}' for i, o in enumerate(signal.options, 1))}"
        )
    return answer
```

> **제거된 항목**:
>
> - `ClarificationHandler` ABC — 추상화 대상이 2가지 분기밖에 없음
> - `FreeTextHandler`, `SingleSelectHandler`, `FormulaHandler`, `ConflictHandler` — FormulaHandler ≈ SingleSelectHandler, ConflictHandler ≈ FreeTextHandler (코드 중복)
> - `HandlerRegistry` — 등록/조회 인프라 불필요
> - `apply_to_state()` — 7개 중 6개가 `{}` 반환. 유일한 예외(`selected_db_source`)도 R-03에서 필드 자체 제거
> - `handlers.py` 파일 자체 — clarification_handler.py에 인라인

### 2.5 Unified Clarification 노드 — `src/agents/nodes/interpret/clarification_handler.py` (신규)

> **설계 원칙 (R-04, R-09 반영)**:
> - `guardrails.py` 별도 모듈 제거 — 가드레일 로직(7줄 match/case)을 clarification_handler.py에 인라인
> - `QueryContext` 모델 제거 — 2개 bool 필드를 Pydantic BaseModel로 만들 필요 없음
> - `_to_auto_resolved()`, `_build_clarification_request()` 변환 함수 제거 — AmbiguitySignal 단일 모델(R-02)로 불필요
> - `HandlerRegistry` 디스패치 제거 — `validate_answer()` 단일 함수(R-01)로 대체
> - 함수 크기: ~80줄 → ~25줄

```python
"""Unified Clarification 노드 — 모든 명확화의 단일 진입점.

2계층 판정: 가드레일 적용 → ASK/INFER 분리 → ASK 시 interrupt() 1회만 호출.

LangGraph 공식 규칙:
    "interrupt calls should happen in the same order every time,
     and you should not conditionally skip interrupt calls within a node."
"""
from __future__ import annotations

from datetime import datetime

from langgraph.types import interrupt

from src.agents.models.clarification import (
    AmbiguitySignal,
    AmbiguityType,
    ConfidenceLevel,
    QuestionType,
)
from src.agents.state.state import PipelineState
from src.utils.logger import get_logger

logger = get_logger(__name__)


# ── 가드레일: INFER→ASK 단방향 보정 (인라인, R-09) ──

def _should_override_to_ask(signal: AmbiguitySignal, state: PipelineState) -> str | None:
    """INFER → ASK 보정이 필요하면 사유를 반환한다.

    ASK → INFER 변환은 절대 없음 (안전 방향만 보정).
    LLM 호출 0 — 순수 규칙.
    """
    if signal.decision == "ASK":
        return None
    match signal.ambiguity_type:
        case AmbiguityType.FORMULA:
            return "산출식 관련 모호함은 추론 금지 (금융 규제)"
        case AmbiguityType.TABLE if len(signal.options) >= 2 and signal.confidence == ConfidenceLevel.LOW:
            return "테이블 선택 확신도 부족"
        case AmbiguityType.INTENT if signal.confidence == ConfidenceLevel.LOW:
            return "의도 판정 확신도 부족"
        # VALUE, TIMEFRAME 등: 가드레일 규칙 세분화는 Phase 2B에서 운영 데이터 기반으로 추가
    return None


# ── ASK 시그널 우선순위 (의존 관계 반영) ──

_PRIORITY = {
    AmbiguityType.INTENT: 1,
    AmbiguityType.FORMULA: 1,
    AmbiguityType.TABLE: 2,
    AmbiguityType.VALUE: 2,
    AmbiguityType.TIMEFRAME: 3,
    AmbiguityType.CONTEXT: 4,
    AmbiguityType.CONFLICT: 4,
}


# ── 응답 검증 (인라인, R-01) ──

def validate_answer(answer: str, signal: AmbiguitySignal) -> str:
    """사용자 응답을 검증한다. question_type 기반 2가지 분기."""
    answer = answer.strip()
    if not answer:
        raise ValueError("응답이 비어있습니다.")

    if signal.question_type == QuestionType.SINGLE_SELECT and signal.options:
        for i, opt in enumerate(signal.options, 1):
            if answer == str(i) or answer == opt:
                return opt
        raise ValueError(
            f"선택지 중에서 골라주세요: "
            f"{', '.join(f'{i}) {o}' for i, o in enumerate(signal.options, 1))}"
        )
    return answer


# ── interrupt 페이로드에 포함할 필드 ──

_INTERRUPT_FIELDS = {"question", "question_type", "options", "ambiguity_type", "source_node"}


# ── 통합 명확화 노드 ──

async def clarification_handler_node(state: PipelineState) -> dict:
    """통합 명확화 노드 — 가드레일 적용 → ASK/INFER 분리 → interrupt 또는 진행."""
    signals = state.pending_signals
    if not signals:
        return {}

    # 1. 가드레일 적용 (인라인)
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

    # resume 후: 검증 (validate_answer 함수)
    best.answer = validate_answer(user_answer, best)
    best.resolved_at = datetime.now()

    return {
        "resolved_signals": infer + [best],  # INFER + 방금 해소된 ASK 모두 누적
        "pending_signals": [],
    }
```

> **제거된 항목**:
>
> - `guardrails.py` 별도 파일 — `_should_override_to_ask()` 인라인
> - `QueryContext(BaseModel)` — 2개 bool 필드를 위한 모델 불필요
> - `build_query_context()` — placeholder 구현이 비어있었음
> - `_to_auto_resolved()` 변환 함수 — INFER 시그널이 곧 auto_resolved
> - `_build_clarification_request()` 변환 함수 — signal이 곧 interrupt 페이로드
> - `ClarificationEntry` 생성 — signal.answer 채우면 끝
> - `HandlerRegistry.get()` 디스패치 — `validate_answer()` 단일 함수로 대체
> - `handler.apply_to_state()` — 복귀 노드 LLM이 resolved_signals 참조
> - `list(state.auto_resolved) + new_auto_entries` 수동 append — reducer 신뢰

### 2.6 Runner 수정 — `runner.py` (Phase 2 전체 변경)

```python
"""파이프라인 실행 엔트리포인트 — sanitize + interrupt 감지 + ainvoke.

계층 구조:
    main.py (서버) → run_pipeline() → graph (비즈니스 로직)

main.py는 그래프 내부(interrupt, checkpointer)를 모른다.
interrupt 감지와 Command(resume=) 분기는 이 모듈에서 처리한다.
"""
from __future__ import annotations

from typing import Any

from langgraph.types import Command

from src.agents.graph.pipeline import get_compiled_app
from src.agents.models.response import PipelineResult
from src.agents.state.state import PipelineState
from src.services.input_sanitizer import sanitize
from src.utils.logger import get_logger

logger = get_logger(__name__)


async def run_pipeline(
    user_input: str,
    session_id: str | None = None,
    conversation_history: list[dict[str, str]] | None = None,
    *,
    on_event: OnEventCallback | None = None,
) -> PipelineResult:
    """파이프라인을 실행하고 최종 결과를 반환한다.

    모든 입력(첫 턴 + 명확화 응답)에 대해 sanitize를 1회 실행한다.
    interrupt 대기 중이면 Command(resume=)로 재개하고,
    아니면 새 PipelineState로 ainvoke한다.

    Args:
        user_input: 사용자 자연어 입력 (첫 턴 또는 명확화 응답).
        session_id: 세션 식별자.
        conversation_history: 이전 대화 이력.
        on_event: WebSocket 이벤트 콜백.

    Returns:
        PipelineResult.
    """
    # ── 1. sanitize: 모든 입력에 1회 적용 ──
    sanitized = sanitize(user_input)
    if sanitized.is_error:
        return PipelineResult(
            status="error",
            formatted_response=sanitized.error_message,
        )

    app = get_compiled_app()
    config: dict[str, Any] = {}
    if session_id:
        config["configurable"] = {"thread_id": session_id}

    # ── 2. interrupt 대기 중 감지 ──
    state_snapshot = await app.aget_state(config)
    is_interrupt_pending = (
        state_snapshot is not None
        and state_snapshot.next  # 다음 실행할 노드 존재 = interrupt 대기
    )

    if is_interrupt_pending:
        # ── 3a. interrupt 재개: Command(resume=sanitized_text) ──
        logger.info(
            "interrupt 재개",
            session_id=session_id,
            pending_nodes=state_snapshot.next,
        )
        result = await app.ainvoke(
            Command(resume=sanitized.text),
            config=config,
        )
    else:
        # ── 3b. 새 턴: 초기 state 생성 + ainvoke ──
        initial_state = PipelineState(
            user_input=user_input,
            original_query=user_input,  # 최초 질의 보존
            preprocessed_input=sanitized.text,
            session_id=session_id or "",
            conversation_history=conversation_history or [],
        )
        result = await app.ainvoke(initial_state, config=config)

    # ── 4. 결과 구성 ──
    # interrupt 발생 여부 확인 (ainvoke 후 상태 재조회)
    after_state = await app.aget_state(config)
    if after_state and after_state.next:
        # interrupt 발생 → 명확화 대기 중
        # interrupt 페이로드에서 AmbiguitySignal 데이터 추출
        clarification_data = None
        for task in after_state.tasks:
            if hasattr(task, "interrupts") and task.interrupts:
                for intr in task.interrupts:
                    clarification_data = intr.value
                    break

        return PipelineResult(
            status="awaiting_clarification",
            awaiting_clarification=True,
            clarification_request=clarification_data,
            formatted_response=clarification_data.get("question", "")
                if clarification_data else "",
        )

    # 정상 완료
    return _build_result(result)
```

**핵심 설계 포인트**:

1. **sanitize 1회**: `run_pipeline()` 진입 시 무조건 실행. 새 턴이든 interrupt resume이든 동일한 보안 검증 경로.
2. **aget_state로 분기**: interrupt 대기 중이면 `Command(resume=)`, 아니면 `ainvoke(initial_state)`.
3. **main.py는 그래프 무지**: `run_pipeline()`이 `PipelineResult`를 반환하므로 main.py는 interrupt/checkpointer를 알 필요 없음.

### 2.7 Pipeline 그래프 변경 — `pipeline.py`

```python
def build_pipeline() -> StateGraph:
    """LangGraph 파이프라인을 구성한다."""
    workflow = StateGraph(PipelineState)

    # ── 노드 등록 ──
    # [제거] preprocess 노드 — sanitize는 runner.py로 이동
    # [제거] clarify 노드 — clarification_handler로 대체
    workflow.add_node("resolve_history", resolve_history_node)
    workflow.add_node("classify_intent", classify_intent_node)
    workflow.add_node("normalize_query", normalize_query_node)
    workflow.add_node("planner", planner_node)
    workflow.add_node("context_explorer", context_explorer_node)
    workflow.add_node("confidence_evaluator", confidence_evaluator_node)
    workflow.add_node("result_finalizer", result_finalizer_node)
    workflow.add_node("sql_generator", sql_generator_node)
    workflow.add_node("execute_sql", execute_sql_node)
    workflow.add_node("response_formatter", response_formatter_node)

    # [신규] 통합 명확화 노드
    workflow.add_node("clarification_handler", clarification_handler_node)

    # ── 엔트리포인트 ──
    # preprocess 제거 → resolve_history가 시작 노드
    workflow.set_entry_point("resolve_history")

    # ── 엣지 ──
    # ... 기존 엣지 유지 (preprocess 관련 제거) ...

    # ── 명확화 라우팅 ──
    # 각 트리거 노드별 개별 라우팅 함수에서 pending_signals 검사를 수행한다.
    # 공통 _route_after_trigger 함수는 사용하지 않는다 — 각 노드의 정상 라우팅 로직이
    # 다르므로(분기 수, 판정 기준 등) 개별 함수가 기존 로직 + signals 검사를 모두 담당한다.
    workflow.add_conditional_edges("resolve_history", _route_after_resolve_history)
    workflow.add_conditional_edges("classify_intent", _route_after_intent)
    workflow.add_conditional_edges("normalize_query", _route_after_normalize)
    workflow.add_conditional_edges("sql_generator", _route_after_sql_generator)
    workflow.add_conditional_edges("confidence_evaluator", _route_after_confidence_evaluator)

    # ── clarification_handler 후속: source_node로 복귀 ──
    workflow.add_conditional_edges(
        "clarification_handler",
        _route_after_clarify,
    )

    return workflow


# ── 개별 라우팅 함수 ──
# 각 함수는 pending_signals 검사(2줄)를 기존 라우팅 로직 상단에 추가한다.
# 공통 추상화(_route_after_trigger) 대신 개별 함수를 유지하는 이유:
# - 각 노드의 정상 라우팅 로직이 서로 다름 (분기 수, 판정 기준, 반환값)
# - 공통 함수에서 _default_next(state) 같은 마법 함수로는 이를 처리할 수 없음

def _route_after_resolve_history(state: PipelineState) -> str:
    if state.pending_signals:
        return "clarification_handler"
    # 기존 라우팅: classify_intent 등
    return _existing_resolve_history_routing(state)


def _route_after_intent(state: PipelineState) -> str:
    if state.pending_signals:
        return "clarification_handler"
    # 기존 라우팅: turn limit + normalization gate
    return _existing_intent_routing(state)


def _route_after_normalize(state: PipelineState) -> str:
    if state.pending_signals:
        return "clarification_handler"
    return "planner"


def _route_after_sql_generator(state: PipelineState) -> str:
    if state.pending_signals:
        return "clarification_handler"
    # 기존 라우팅: sql_validator 등
    return _existing_sql_generator_routing(state)


def _route_after_confidence_evaluator(state: PipelineState) -> str:
    if state.pending_signals:
        return "clarification_handler"
    # 기존 라우팅: evaluate_readiness(state.reason).value
    return evaluate_readiness(state.reason).value


# ── clarification_handler 후속 라우팅 (R-03, R-11 반영) ──
# clarification_return_to 전용 필드 제거 → resolved_signals[-1].source_node에서 도출
# 화이트리스트 검증 추가

_VALID_RETURN_TARGETS = frozenset({
    "resolve_history", "classify_intent", "normalize_query",
    "sql_generator", "confidence_evaluator",
})

def _route_after_clarify(state: PipelineState) -> str:
    """clarification_handler 후 라우팅 — 마지막 resolved signal의 source_node로 복귀."""
    target = state.resolved_signals[-1].source_node if state.resolved_signals else ""
    if target not in _VALID_RETURN_TARGETS:
        logger.error("Invalid return target", target=target)
        return "resolve_history"
    return target
```

### 2.8 트리거 노드 마이그레이션 — AmbiguitySignal 방식

기존 5개 트리거 노드에서 직접 명확화를 처리하던 로직을 `AmbiguitySignal` 생성으로 변경한다.

> **R-07 반영**: 노드별 판정 복잡도 차등화 — 5개 노드 중 3개만 LLM 판정 필요, 2개는 규칙 기반 하드코딩.

| 노드                   | 실제 필요한 판정              | 방식                          |
| ---------------------- | ----------------------------- | ----------------------------- |
| history_resolver       | UNSURE → 항상 ASK             | 하드코딩. LLM 판정 불필요     |
| classify_intent        | AMBIGUOUS → ASK/INFER 가능    | LLM 판정 (INTENT 타입만)      |
| normalize_query        | 복수 모호성 → ASK/INFER 가능  | LLM 판정 (전체 7종)           |
| sql_generator          | Cross-DB → 항상 ASK           | 하드코딩. LLM 판정 불필요     |
| confidence_evaluator   | CONFLICTED → ASK/INFER 가능   | LLM 판정 (TABLE/VALUE만)      |

#### 공통: LLM 프롬프트에 추가할 판정 기준 (LLM 판정 노드 3개만 적용)

```text
업무 수행 중 모호함을 발견하면, 다음 기준으로 ASK/INFER를 판정하세요.

[ASK — 사용자에게 질문 필요]:
- 추론이 틀리면 완전히 다른 데이터가 나오는 경우
- 산출식/지표 정의가 달라지는 경우
- 후보가 여러 개이고 맥락으로 좁힐 수 없는 경우

[INFER — 추론 후 진행]:
- 업무 관행상 일반적인 해석이 있는 경우
- 포괄적으로 조회하면 해소되는 경우 (컬럼 선택 등, PRACTIQ 억제)
- 기간 등 기본값을 적용하고 안내하면 되는 경우

판단이 애매하면 ASK를 선택하세요.

[모호성 유형 분류]:
TABLE / INTENT / VALUE / FORMULA / TIMEFRAME / CONTEXT / CONFLICT

[도메인 기본값]
- "여신 실적" → 일반적으로 "실행 금액" (과거 SQL 이력 85%)
- "최근" → 일반적으로 "최근 1개월" (업무 매뉴얼)
```

#### T1: history_resolver — UNSURE (하드코딩, LLM 판정 불필요)

```python
# history_resolver.py

async def resolve_history_node(state: PipelineState) -> dict:
    # ... 기존 로직 ...

    if resolution_status == "UNSURE":
        # UNSURE → 항상 ASK (맥락 추론 불가). LLM 판정 프롬프트 불필요.
        signal = AmbiguitySignal(
            source_node="resolve_history",
            ambiguity_type=AmbiguityType.CONTEXT,
            decision="ASK",  # 하드코딩
            confidence=ConfidenceLevel.LOW,
            question="이전 대화 맥락을 파악하기 어렵습니다. 질문을 좀 더 구체적으로 말씀해주시겠어요?",
            reasoning="대화 이력에서 맥락을 추론할 수 없음",
        )
        return {
            "pending_signals": [signal],  # 덮어쓰기 (일반 필드)
        }

    # 정상 처리 ...
```

#### T2: classify_intent — AMBIGUOUS (LLM 판정, INTENT 타입만)

```python
# intent_classifier.py

async def classify_intent_node(state: PipelineState) -> dict:
    # ... 기존 분류 로직 + LLM에 ASK/INFER 판정 기준 포함 ...

    if intent in (IntentType.CLARIFICATION_NEEDED, IntentType.GENERAL_QUESTION):
        signal = AmbiguitySignal(
            source_node="classify_intent",
            ambiguity_type=AmbiguityType.INTENT,
            decision=llm_decision,  # LLM이 판정 ("ASK" 또는 "INFER")
            confidence=llm_confidence,
            question=clarification_question,  # DTE: "왜 묻는지" 포함
            options=intent_candidates,
            inferred_value=llm_inferred if llm_decision == "INFER" else None,
            reasoning=llm_reasoning,
        )
        return {
            "pending_signals": [signal],
            **updates,
        }

    return updates
```

#### T3: normalize_query — ambiguities (LLM 판정, 전체 7종)

```python
# query_normalizer.py

async def normalize_query_node(state: PipelineState) -> dict:
    # ... 기존 정규화 로직 ...

    ask_count = len([s for s in state.resolved_signals if s.decision == "ASK"])
    if nq.ambiguities and ask_count < max_turns:
        signals = []
        for ambiguity in nq.ambiguities:
            signal = AmbiguitySignal(
                source_node="normalize_query",
                ambiguity_type=ambiguity.type,  # LLM이 분류한 유형
                decision=ambiguity.decision,
                confidence=ambiguity.confidence,
                question=ambiguity.description,  # DTE 패턴
                options=ambiguity.candidates,
                inferred_value=ambiguity.inferred_value,
                reasoning=ambiguity.reasoning,
            )
            signals.append(signal)
        return {
            "pending_signals": signals,
            **updates,
        }

    return updates
```

#### T4: sql_generator — Cross-DB (하드코딩, LLM 판정 불필요)

```python
# sql_generator.py

async def sql_generator_node(state: PipelineState) -> dict:
    # ... 기존 SQL 생성 로직 ...

    if cross_db_candidates:
        # Cross-DB → 항상 ASK (후보 DB 존재). LLM 판정 프롬프트 불필요.
        signal = AmbiguitySignal(
            source_node="sql_generator",
            ambiguity_type=AmbiguityType.TABLE,
            decision="ASK",  # 하드코딩
            confidence=ConfidenceLevel.LOW,
            question=(
                "조회 대상 데이터베이스가 여러 개입니다. "
                "정확한 데이터를 제공하기 위해 확인이 필요합니다."
            ),
            question_type=QuestionType.SINGLE_SELECT,
            options=[f"{db}: {desc}" for db, desc in cross_db_candidates],
            reasoning=f"후보 DB {len(cross_db_candidates)}개 발견",
        )
        return {
            "pending_signals": [signal],
        }

    return updates
```

#### T5: confidence_evaluator — CONFLICTED (LLM 판정, TABLE/VALUE만)

```python
# confidence_evaluator.py

async def confidence_evaluator_node(state: PipelineState) -> dict:
    # 명확화 컨텍스트가 있으면 LLM 프롬프트에 주입하여 재판단
    clarify_ctx = build_clarification_context(state)

    # LLM이 명확화 대화를 참조하여 기존 CONFLICTED 항목을 재평가
    # LLM이 전체 맥락을 보고 판단한다 (resolved_signals 참조).

    # ... 기존 판정 로직 (clarify_ctx를 프롬프트에 포함) ...

    conflicted = [
        ki for ki in state.reason.knowledge_items
        if ki.status == ConfidenceStatus.CONFLICTED
    ]
    if conflicted:
        signal = AmbiguitySignal(
            source_node="confidence_evaluator",
            ambiguity_type=AmbiguityType.TABLE,
            decision="ASK",
            confidence=ConfidenceLevel.LOW,
            question="다음 항목들의 스키마 정보가 충돌합니다. 어느 쪽을 사용할지 확인이 필요합니다.",
            question_type=QuestionType.SINGLE_SELECT,
            options=[f"{ki.key}: {ki.value}" for ki in conflicted],
            reasoning=f"충돌 항목 {len(conflicted)}개",
        )
        return {
            "pending_signals": [signal],
        }

    return updates
```

### 2.9 Structured Context 전달 — 모든 복귀 노드 공통

명확화 후 복귀하는 **모든 복귀 노드**는 LLM 프롬프트에 명확화 Q&A를 구조화된 섹션으로 주입한다.
복귀 노드의 LLM이 사용자 답변을 참조하여 상태를 스스로 재판단하는 것이 핵심 원칙이다.

#### 2.9.1 공통 컨텍스트 빌더

```python
# src/agents/utils/clarification_context.py

def build_clarification_context(state: PipelineState) -> str:
    """resolved_signals를 decision 기준으로 분리하여 프롬프트 섹션을 구성한다.

    모든 복귀 노드(confidence_evaluator, normalize_query, sql_generator 등)가
    LLM 프롬프트 구성 시 이 함수를 호출하여 명확화 컨텍스트를 주입한다.
    복귀 노드의 LLM이 이 컨텍스트를 보고
    ReasoningState 상태 전환(CONFLICTED→CONFIRMED 등)을 스스로 재판단한다.
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

#### 2.9.2 SQL 생성 프롬프트 (sql_generator 적용 예시)

```python
# sql_generator.py 또는 프롬프트 빌더

def _build_sql_prompt(state: PipelineState) -> str:
    """SQL 생성 프롬프트를 구성한다.

    원본 질의와 명확화 Q&A, 자동 추론 결과를 분리된 섹션으로 전달하여
    엔티티 소실 없이 전체 문맥을 LLM에 제공한다.
    """
    sections = []

    # 섹션 1: 원본 질의 (불변)
    sections.append(f"[사용자 원본 질의]\n{state.original_query}")

    # 섹션 2-3: 명확화 대화 + 자동 추론 결과 (공통 빌더 사용)
    clarify_ctx = build_clarification_context(state)
    if clarify_ctx:
        sections.append(clarify_ctx)

    # 섹션 4: 스키마 컨텍스트
    sections.append(f"[스키마 컨텍스트]\n{_format_schema(state)}")

    # 섹션 5: 참조 SQL (있을 때만)
    if state.reference_sqls:
        sections.append(f"[참조 SQL]\n{_format_references(state)}")

    return "\n\n".join(sections)
```

#### 2.9.3 복귀 노드 적용 패턴

명확화 후 복귀하는 모든 노드는 동일한 패턴으로 컨텍스트를 주입한다:

```python
# confidence_evaluator.py — 복귀 시 CONFLICTED 재판정 예시

async def confidence_evaluator_node(state: PipelineState) -> dict:
    # 명확화 컨텍스트가 있으면 LLM 프롬프트에 주입
    clarify_ctx = build_clarification_context(state)

    prompt = CONFIDENCE_EVALUATOR_SYSTEM.format(
        knowledge_items=_format_knowledge_items(state.reason),
        clarification_context=clarify_ctx,  # LLM이 사용자 답변을 참조하여 재판정
    )

    # LLM이 명확화 대화를 보고 CONFLICTED → CONFIRMED 전환 등을 스스로 결정
    result = await llm.ainvoke(prompt)
    # ...
```

> **적용 대상 노드**: confidence_evaluator, normalize_query, resolve_history,
> classify_intent, sql_generator — 명확화 후 복귀 가능한 모든 노드.
> 각 노드의 시스템 프롬프트에 `{clarification_context}` 슬롯을 추가한다.

### 2.9-2 응답 포맷터에서 INFER 항목 안내

```python
# response_formatter.py

def _build_auto_resolved_notice(state: PipelineState) -> str:
    """INFER 항목을 결과 상단에 자연어로 안내한다.

    DTE 패턴: "왜 이렇게 처리했는지" 근거를 포함한다.
    """
    infers = [s for s in state.resolved_signals if s.decision == "INFER"]
    if not infers:
        return ""

    lines = ["조회 기준 안내:"]
    for s in infers:
        lines.append(f"- {s.question} → {s.inferred_value}")
    lines.append("(다른 기준을 원하시면 말씀해 주세요)")
    return "\n".join(lines)
```

### 2.10 기존 파일 제거

| 파일 | 처리 |
| ---- | ---- |
| `src/agents/nodes/interpret/preprocessor.py` | **제거** — sanitize는 runner.py로 이동 |
| `src/agents/nodes/interpret/clarifier.py` | **제거** — clarification_handler로 대체 |
| `src/services/input_sanitizer.py` 내 `synthesize_clarification()` | **제거** — 미사용 dead code |
| `src/services/input_sanitizer.py` 내 `ClarificationSynthesisResult` | **제거** — 미사용 dead code |

---

## Phase 3: 세션 관리 통합

### 3.1 SessionStore 역할 축소

```python
# session/store.py — 변경 후

class SessionStore(ABC):
    """세션 스토어 인터페이스 (경량화).

    체크포인터 도입 후:
    - get_history / append_history: UI 표시용 대화 이력 관리 (유지)
    - get_clarification / set_clarification: deprecated → 제거
      (체크포인터가 명확화 상태를 관리)
    """

    @abstractmethod
    async def get_history(self, session_id: str) -> list[dict[str, str]]:
        """대화 이력을 반환한다 (UI 사이드바 표시용)."""

    @abstractmethod
    async def append_history(self, session_id: str, entry: dict[str, str]) -> None:
        """대화 이력에 1건을 추가한다."""

    # deprecated — 체크포인터가 상태 관리
    async def get_clarification(self, session_id: str) -> dict | None:
        return None

    async def set_clarification(self, session_id: str, state: dict) -> None:
        pass

    # ... 기존 connect/disconnect/ensure/clear/health 유지 ...
```

### 3.2 main.py 정리

```python
# main.py — clarification 관련 코드 제거

async def _run_ws_pipeline(data, session_id, websocket) -> None:
    # [제거] store.get_clarification() 호출
    # [제거] store.set_clarification() 호출

    pipeline_result = await run_pipeline(
        data,
        session_id,
        conversation_history=await store.get_history(session_id),
        on_event=on_event,
    )

    if pipeline_result.awaiting_clarification:
        # clarification_request를 프론트엔드에 전달
        await websocket.send_json({
            "type": "clarification",
            "data": pipeline_result.clarification_request,
        })
        return

    # ... 기존 응답 전송 로직 (stream, viz, download) ...
```

### 3.3 runner.py 정리

```python
# runner.py — clarification_state 파라미터 완전 제거

async def run_pipeline(
    user_input: str,
    session_id: str | None = None,
    conversation_history: list[dict[str, str]] | None = None,
    *,
    # [제거] clarification_state: dict[str, Any] | None = None,
    on_event: OnEventCallback | None = None,
) -> PipelineResult:
    # 체크포인터가 명확화 상태를 자동 관리
    ...
```

### 3.4 대화 이력 이중 소스 전략

```text
┌─────────────────────────────────────────────────┐
│ 1. Checkpointer (정본)                           │
│    - PipelineState 전체 (reasoning state 포함)   │
│    - resolved_signals (명확화 Q&A 누적, 구조화)   │
│    - 그래프 상태의 일부로 자동 체크포인트          │
│                                                 │
│ 2. SessionStore (경량 인덱스)                    │
│    - role + content + type 쌍                    │
│    - type: "query" | "response" | "clarification"│
│    - 명확화 Q&A도 type="clarification"으로 기록   │
│    - 빠른 조회 (Redis O(1) vs PG 쿼리)          │
│                                                 │
│ 역할 분리:                                       │
│  - resolved_signals: 파이프라인 내부 노드 로직 전용│
│    (ambiguity_type, source_node, decision 등)    │
│  - conversation_history: UI 렌더링 + 이력 해소용  │
│    (resolve_history는 type≠"clarification"만 참조)│
│                                                 │
│ 동기화: main.py에서 append_history 유지           │
│  - 일반 질의: type="query"                       │
│  - 일반 응답: type="response"                    │
│  - 명확화 질문/응답: type="clarification"         │
└─────────────────────────────────────────────────┘
```

---

## Phase 4: 고급 기능

### 4.1 Thread TTL 정리

```python
# src/agents/graph/thread_manager.py (신규)

"""Thread 생명주기 관리 — 오래된 체크포인트 자동 정리."""

from datetime import datetime, timedelta
from src.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)


async def cleanup_expired_threads() -> int:
    """TTL이 만료된 스레드를 정리한다.

    settings.checkpoint_thread_ttl_days 기준으로
    마지막 활동 이후 경과된 스레드를 삭제한다.

    Returns:
        삭제된 스레드 수.
    """
    if settings.checkpointer.backend != "postgres":
        return 0

    ttl_days = settings.checkpointer.thread_ttl_days
    if ttl_days <= 0:
        return 0

    # Phase 4 시점에는 lifespan에서 생성된 checkpointer를 주입받는 구조.
    # 아래는 독립 실행(cron) 시나리오를 위한 예시.
    from src.agents.graph.checkpointer import create_checkpointer

    async with create_checkpointer(settings.checkpointer, settings.history_db) as checkpointer:
        cutoff = datetime.utcnow() - timedelta(days=ttl_days)
        deleted = 0

        async with checkpointer.conn.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT DISTINCT thread_id
                    FROM checkpoints
                    WHERE checkpoint_id IN (
                        SELECT MAX(checkpoint_id)
                        FROM checkpoints
                        GROUP BY thread_id
                    )
                    AND created_at < %s
                    """,
                    (cutoff,),
                )
                rows = await cur.fetchall()
                old_thread_ids = [row["thread_id"] for row in rows]

        for tid in old_thread_ids:
            try:
                await checkpointer.adelete_thread(tid)
                deleted += 1
            except Exception as e:
                logger.warning(
                    "스레드 삭제 실패",
                    thread_id=tid,
                    error=str(e),
                )

        logger.info(
            "체크포인트 정리 완료",
            deleted=deleted,
            cutoff=cutoff.isoformat(),
        )
        return deleted
```

### 4.2 RetryPolicy 적용

```python
# pipeline.py — 외부 I/O 노드에 RetryPolicy 적용

from langgraph.pregel import RetryPolicy

# ES/Qdrant/DB 접근 노드에 재시도 정책 적용
_io_retry = RetryPolicy(
    max_attempts=2,
    initial_interval=1.0,
    backoff_factor=2.0,
    max_interval=5.0,
    jitter=True,
)

def build_pipeline() -> StateGraph:
    workflow = StateGraph(PipelineState)

    # 외부 I/O가 있는 노드에 RetryPolicy 적용
    workflow.add_node(
        "context_explorer", context_explorer_node,
        retry=_io_retry,
    )
    workflow.add_node(
        "execute_sql", execute_sql_node,
        retry=RetryPolicy(max_attempts=2),
    )

    # LLM 호출 노드는 이미 내부에서 재시도하므로 RetryPolicy 미적용
    workflow.add_node("classify_intent", classify_intent_node)
    # ...
```

### 4.3 SQL 승인 interrupt (선택적)

```python
# sql_executor.py — 선택적 SQL 승인 interrupt

from langgraph.types import interrupt

async def execute_sql_node(state: PipelineState) -> dict:
    sql = state.reason.validated_sql

    if settings.sql_approval_enabled:
        approval = interrupt({
            "type": "sql_approval",
            "sql": sql,
            "message": "아래 SQL을 실행하겠습니다. 확인해주세요.",
        })
        if isinstance(approval, dict) and approval.get("action") == "cancel":
            return {
                "formatted_response": "SQL 실행이 취소되었습니다.",
                "status": QueryStatus.ERROR,
            }

    # SQL 실행 ...
```

### 4.4 EncryptedSerializer (금융 데이터 보호)

체크포인트에 저장되는 PipelineState에 민감 데이터(SQL 결과 등)가 포함되므로,
프로덕션 환경에서는 암호화 직렬화를 적용한다.

```python
# checkpointer.py에 추가

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from cryptography.fernet import Fernet


class EncryptedSerializer(JsonPlusSerializer):
    """체크포인트 데이터를 AES-128로 암호화하는 직렬화기."""

    def __init__(self, key: bytes) -> None:
        super().__init__()
        self._fernet = Fernet(key)

    def dumps(self, obj: Any) -> bytes:
        plain = super().dumps(obj)
        return self._fernet.encrypt(plain)

    def loads(self, data: bytes) -> Any:
        plain = self._fernet.decrypt(data)
        return super().loads(plain)
```

---

## 구현 체크리스트

### Phase 1 (Core Checkpointer)

- [ ] `config.py`: `DbConnectionInfo` Value Object + `CheckpointerConfig` 추가 (R-06)
- [ ] `src/agents/graph/checkpointer.py`: async context manager 팩토리 구현 (R-06)
- [ ] `pipeline.py`: `create_app()`, `get_compiled_app()` 수정
- [ ] `runner.py`: `thread_id` config 주입
- [ ] `main.py`: lifespan에 `async with create_checkpointer()` 적용
- [ ] `pyproject.toml`: 의존성 추가 (`langgraph-checkpoint-postgres`, `psycopg`)
- [ ] 직렬화 round-trip 테스트 작성
- [ ] 기존 테스트 통과 확인

### Phase 2A: interrupt 인프라 (기존 흐름 유지하면서 인프라만 준비)

> **R-08 반영**: 기존 모놀리식 Phase 2A(12개 작업)를 2A/2B/2C 3단계로 분리.
> 각 단계마다 독립적으로 테스트 + 롤백 가능.

- [ ] `src/agents/models/clarification.py`: `AmbiguityType`, `ConfidenceLevel`, `QuestionType`, `AmbiguitySignal` 스키마 정의 (R-02: 단일 모델)
- [ ] `state.py`: `original_query`, `pending_signals`, `resolved_signals` 추가 (R-03: 3개 필드만)
- [ ] `state.py`: `normalized_query: Any` → `NormalizedQuery | None` 타입 명시화
- [ ] `state.py`: `clarification_origin`, `clarification_response` 제거
- [ ] `src/agents/nodes/interpret/clarification_handler.py`: 통합 명확화 노드 (빈 구현 — pending_signals 없으면 통과)
- [ ] `clarification_handler.py` 내 `validate_answer()` 인라인 함수 (R-01: 핸들러 제거)
- [ ] `clarification_handler.py` 내 `_should_override_to_ask()` 가드레일 인라인 (R-09)
- [ ] `runner.py`: sanitize 통합 + interrupt 감지(aget_state) + Command(resume=) 분기
- [ ] `pipeline.py`: preprocess 노드 제거, clarification_handler 노드 추가, 엔트리포인트 변경
- [ ] `pipeline.py`: clarification_handler 후 `_route_after_clarify` 라우팅 (R-11: 화이트리스트 검증)
- [ ] 각 트리거별 명확화 E2E 테스트 (빈 구현이므로 기존 동작 유지 확인)

### Phase 2B: Interpret 계층 마이그레이션 (T1~T3)

- [ ] `history_resolver.py`: UNSURE 시 `AmbiguitySignal` 생성 (하드코딩 ASK, R-07)
- [ ] `intent_classifier.py`: AMBIGUOUS 시 `AmbiguitySignal` 생성 + ASK/INFER LLM 판정 프롬프트 (R-07)
- [ ] `query_normalizer.py`: ambiguities 시 `AmbiguitySignal` 생성 + ASK/INFER LLM 판정 프롬프트 (R-07: 전체 7종)
- [ ] `pipeline.py`: T1~T3 트리거 노드 후 conditional edge (pending_signals → clarification_handler)
- [ ] `preprocessor.py` 제거
- [ ] `clarifier.py` 제거
- [ ] `input_sanitizer.py`: `synthesize_clarification()` + `ClarificationSynthesisResult` 제거
- [ ] Interpret 계층 명확화 E2E 테스트 (T1~T3 × 정상/에러)

### Phase 2C: Reason 계층 마이그레이션 (T4~T5)

- [ ] `sql_generator.py`: Cross-DB 시 `AmbiguitySignal` 생성 (하드코딩 ASK, R-07)
- [ ] `confidence_evaluator.py`: CONFLICTED 시 `AmbiguitySignal` 생성 + ASK/INFER LLM 판정 프롬프트 (R-07)
- [ ] `pipeline.py`: T4~T5 트리거 노드 후 conditional edge
- [ ] SQL 생성 프롬프트에 Structured Context (원본 + Q&A + INFER) 전달 구현
- [ ] `response_formatter.py`: INFER 항목 결과 상단 안내 구현
- [ ] Reason 계층 명확화 E2E 테스트 (T4~T5 × 정상/에러)
- [ ] 전체 ASK/INFER 판정 정확도 검증 (가드레일 보정 포함)

### Phase 2D (안정화 후 개선)

- [ ] `resources/domain_defaults.yaml`: 도메인 기본값 사전 초기 시딩
- [ ] 도메인 기본값 LLM 컨텍스트 주입 (각 노드 프롬프트)
- [ ] 가드레일 규칙 세분화 (`VALUE` 코드 매칭, `TIMEFRAME` 산출식 연관)
- [ ] **[TODO]** 정정 임계값 — INFER 정정 감지 로직 + ASK 전환 (정정 판별 기준 미정의, 운영 데이터 축적 후 재검토)
- [ ] ASK 시그널 우선순위 정교화 (의존 관계 반영)

### Phase 3 (세션 관리 통합)

- [ ] `session/store.py`: clarification 메서드 deprecated → 제거
- [ ] `main.py`: clarification_state 관련 Redis 호출 제거
- [ ] `runner.py`: `clarification_state` 파라미터 제거
- [ ] `session/redis_store.py`: clarify 키 관련 코드 정리

### Phase 4 (고급 기능)

- [ ] `src/agents/graph/thread_manager.py`: TTL 정리 구현
- [ ] `pipeline.py`: RetryPolicy 적용
- [ ] execute_sql interrupt (선택적)
- [ ] EncryptedSerializer 도입 (금융 데이터 보호)
- [ ] State time-travel 디버깅 API

---

## 의존성 변경 (pyproject.toml)

```toml
[project]
dependencies = [
    # 기존 의존성 ...
    "langgraph-checkpoint-postgres>=2.0.0",
    "psycopg[binary,pool]>=3.1.0",
]

[project.optional-dependencies]
# 폐쇄망에서 binary 빌드가 불가능한 경우
closed-network = [
    "psycopg[pool]>=3.1.0",  # binary 제외, C 확장 직접 빌드
]
# 암호화 직렬화 (Phase 4)
encryption = [
    "cryptography>=42.0.0",
]
```

---

## 마이그레이션 주의사항

### State 클래스 경로 변경 금지

체크포인터의 `JsonPlusSerializer`는 Pydantic 모델의 **모듈 경로**를 직렬화에 포함한다.
따라서 다음 경로는 변경 금지:

```
src.agents.state.state.PipelineState
src.agents.state.state.ReasoningState
src.agents.state.state.KnowledgeItem
src.agents.state.state.CandidateTable
# ... 등 모든 State 서브타입
```

경로 변경이 불가피한 경우, 기존 경로에 import 별칭을 유지하여 역직렬화 호환성 보장.

### msgpack allowlist 설정 필수

> **설계 원칙 (R-12 반영)**: 모듈별 개별 등록 대신 **패키지 레벨 단일 접두사**로 단순화한다.

LangGraph의 `JsonPlusSerializer`는 msgpack으로 커스텀 Pydantic 모델을 직렬화한다.
역직렬화 시 **등록되지 않은 모듈의 타입은 경고가 발생**하며, 향후 버전에서 **차단(blocked) 예정**.

체크포인터 생성 시 반드시 allowlist를 설정해야 한다:

```python
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

serde = JsonPlusSerializer().with_msgpack_allowlist([("src.",)])
```

패키지 접두사 `"src."` 하나로 `src.agents.state.state`, `src.agents.models.clarification` 등
모든 프로젝트 내 모듈을 커버한다. 새 모듈 추가 시 별도 등록 불필요.

### 롤링 배포 시 버전 혼재

구/신 서버가 동시에 실행되는 롤링 배포 환경에서:
- 구 버전 서버: 체크포인트 없이 동작 (기존 방식)
- 신 버전 서버: 체크포인트 사용

신 버전이 만든 체크포인트를 구 버전이 읽지 않으므로 충돌 없음.
구 버전 세션은 기존 SessionStore 기반으로 동작하다가 TTL 만료 후 자연 소멸.

### interrupt resume 시 sanitize 보장

**모든 사용자 입력이 `run_pipeline()`을 경유**하므로 sanitize가 누락될 수 없다:

```text
main.py (WebSocket/REST)
  → run_pipeline(user_input)        ← 유일한 진입점
    → sanitize(user_input)          ← 무조건 실행
    → interrupt 대기 중?
      → YES: Command(resume=sanitized_text)
      → NO:  ainvoke(PipelineState(preprocessed_input=sanitized_text))
```

직접 `app.ainvoke(Command(resume=...))` 호출은 금지. 반드시 `run_pipeline()`을 경유한다.
