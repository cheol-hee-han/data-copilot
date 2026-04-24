"""턴 스냅샷 모델.

작성자: 한철희 / 최종수정: 2026-04-20 16:27:03

Multi-Turn CONTINUE Orchestrator 설계(§3.1)의 TurnSnapshot Pydantic 모델을 정의한다.

사용자가 이전 턴의 답변을 보고 이어가는 "CONTINUE 턴"에서 재활용할 수 있도록,
이전 턴의 핵심 시스템 내부 상태를 구조화하여 보존하는 불변 체크포인트다.

설계 원칙:
    - rows 제외: result_data에서 행 데이터는 checkpoint_dc_messages 단일 원천.
      스냅샷이 LangGraph checkpointer writes 테이블에 누적되므로 용량 방어 필수.
    - 풀 메타 객체 보존: selected_tables·explored_codes는 이름이 아닌 풀 객체로
      저장하여 CONTINUE 재주입 시 매번 MongoDB 재탐색을 방지한다.
    - INFER 시그널만 보존: ASK 명확화는 ConversationHistory [명확화] 태그가 커버.
      intent_classifier 생성 INFER(source_node="intent_classifier")는 제외(I4).
    - frozen=True: 스냅샷은 생성 후 변경 불가. 하류 노드가 실수로 수정하는 것을 방지.
    - extra="forbid": 알 수 없는 필드 주입 시 즉시 ValidationError 발생.
      JSON 직렬화 회귀를 조기에 감지한다.

참조: docs/todo/20260416-multi-turn-continue-orchestrator-design.md §3.1
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.agents.models.normalization import NormalizedQuery
from src.agents.state.state import CodeMeta, KnowledgeItem, TableMeta
from src.models.enums import IntentType


class TurnSnapshot(BaseModel):
    """턴 완료 시 생성되는 불변 체크포인트.

    save_turn_snapshot 노드가 format_response 직후 state 필드에서 직접 추출하여
    PipelineState.turn_snapshots(세션 지속 리스트)에 append한다.
    무제한 누적 — intent_classifier가 산출한 `reference_turns`가 참조 범위를 제한한다
    (저장 단계 FIFO는 오래된 턴 명시 참조 케이스를 훼손하므로 두지 않는다).

    Attributes:
        user_message_seq: checkpoint_dc_messages 의 해당 user 턴 seq.
            TurnSnapshot ↔ ConversationHistory 1:1 매핑 키.
        intent: 해당 턴의 최종 의도 유형.
            ANALYZE 경로에서 오라우팅 방지(C2)를 위해 Command(update)에 포함 필수.
        generated_sql: reason.validated_sql 기준 SQL 전문.
            REFINE 모드에서 확정 기준 SQL로 활용한다.
            비데이터 턴(SQL 없음)이면 None.
        sql_explanation: reason.sql_explanation — SQL 1줄 구조화 설명.
            REFINE 수정 지시(handoff_note)와 함께 sql_generator 프롬프트에 주입된다.
        result_data: formatter._build_result_data 결과에서 rows 제외한 메타데이터.
            포함 키: columns, column_formats, total_count, displayed_count.
            rows는 checkpoint_dc_messages.metadata.result_data.rows 가 단일 원천.
            REDISPLAY/ANALYZE 진입 시 오케스트레이터가 JIT fetch 하여 hydrate.
        visualization: Visualization 전체 dict (chart_type, config, series 등).
            REDISPLAY 경로에서 시각화 변경 시 기준값으로 활용한다.
        selected_tables: reason.explored_tables 중 SELECTED 된 TableMeta 풀 객체.
            REFINE 경로에서 query_normalizer/reasoning_preparer가 초기 탐색 시드로 활용한다.
        explored_codes: reason.explored_codes 와 동일한 dict 시맨틱.
            키: 코드 컬럼명(예: "LOAN_STS_CD"), 값: CodeMeta 풀 객체.
            context_interpreter가 초기 지식(knowledge_items)으로 주입한다.
        inferred_signals: 자동 추론(INFER) 시그널만 보존한 리스트.
            ASK 명확화는 ConversationHistory [명확화] 태그로 커버되므로 제외.
            source_node == "intent_classifier" 인 INFER도 제외(I4):
            매 CONTINUE 턴마다 생성되어 스냅샷 축적 시 반복 노출 위험.
        normalized_query: 해당 턴의 최종 NormalizedQuery 전체 구조체.
            REGENERATE 경로에서 정규화 결과를 그대로 복원(no slot patching)하여
            reasoning_preparer 없이 sql_generator 로 직행할 때 재사용한다.
            비데이터 턴(정규화 미수행) 또는 과거 턴(필드 도입 전)에는 None.
        knowledge_items: reason.knowledge_items 전량 — REGENERATE 시
            reasoning/validator 의 지식 근거(reasoning_decisions·confirmed_terms
            렌더링)를 복원하기 위해 보존한다.
        query_decomposition: reason.query_decomposition 원본 dict.
            키(`decomposed_subqueries`, `join_strategy`, `complexity` 등) 구조는
            reasoning_preparer 의 산출물과 동일하며, hydration 시 그대로 주입된다.
            과거 턴(필드 도입 전) 또는 분해 미수행 턴은 빈 dict.
        target_db: 해당 턴의 실행 대상 DB 식별자(`reason.target_db`).
            REGENERATE 시 target_db_resolver 재호출 없이 동일 DB 로 재생성하기
            위해 보존한다. 과거 턴에서는 `""`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # ── 매핑 키 ──
    user_message_seq: int

    # ── 라우팅/재실행 ──
    intent: IntentType
    generated_sql: str | None = None
    sql_explanation: str = ""

    # ── 사용자가 본 구조화 데이터 (rows 제외 — 용량 방어) ──
    result_data: dict | None = None  # type: ignore[type-arg]
    visualization: dict | None = None  # type: ignore[type-arg]

    # ── CONTINUE 재사용을 위한 풀 메타 ──
    selected_tables: list[TableMeta] = Field(default_factory=list)
    explored_codes: dict[str, CodeMeta] = Field(default_factory=dict)

    # ── REGENERATE 복원용 정규화 결과 (Path F') ──
    normalized_query: NormalizedQuery | None = None
    knowledge_items: list[KnowledgeItem] = Field(default_factory=list)
    query_decomposition: dict[str, Any] = Field(default_factory=dict)
    target_db: str = ""

    # ── 자동 추론 시그널 ──
    inferred_signals: list[dict] = Field(default_factory=list)  # type: ignore[type-arg]
