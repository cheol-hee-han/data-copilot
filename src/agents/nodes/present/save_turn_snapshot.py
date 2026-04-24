"""턴 스냅샷 저장 노드 — formatter 직후 실행.

작성자: 한철희 / 최종수정: 2026-04-20 16:27:03

Multi-Turn CONTINUE Orchestrator 설계(§3.1, Step 6 / Path F' §11.5)에 따라,
포맷팅이 완료된 현재 턴 상태를 TurnSnapshot 1개로 추출하여
PipelineState.turn_snapshots에 append한다.

턴 수명주기 대칭 쌍:
    - 턴 시작: `PipelineState.turn_reset_updates()` (state.py) — 턴 스코프 필드 초기화
    - 턴 종료: `save_turn_snapshot` (이 파일) — 직전 턴 상태를 13필드로 아카이빙
    두 로직은 서로 대칭이며, 새 필드 추가 시 양쪽의 포함/제외 여부를 동시에 결정해야 한다.

설계 근거:
    - 무제한 누적(CHANGELOG #6): 저장 단계 FIFO 제거. 사용자가 "아까 처음 뽑았던 거"
      같이 오래된 턴을 명시 참조하는 케이스를 훼손하지 않기 위해 저장 단계에서는
      자르지 않는다. 참조 범위는 intent_classifier 산출 `reference_turns`가 제한.
    - REDISPLAY 저장 (Path F' §11.5): REDISPLAY 경로도 스냅샷을 저장한다 —
      handoff_note 에 의해 visualization 이 새 결과로 갱신될 수 있고, 다음 턴의
      오케스트레이터는 "가장 최근 visualization" 을 참조해야 하기 때문.
      (구 설계의 REDISPLAY skip 은 Path F' 에서 폐기됨.)
    - 예외 삼킴: 스냅샷 저장 실패가 사용자 응답 차단으로 이어지지 않도록
      모든 예외를 경고 로그로만 처리 (I3 규칙)
    - 비데이터 턴 스킵: validated_sql이 없으면 SQL 기반 CONTINUE가 불가능하므로
      스냅샷 저장 자체를 스킵 (I4 규칙)

참조: docs/todo/20260418-continue-orchestrator-4way-redesign.md §4.3, §11.5
"""

from __future__ import annotations

from typing import Any

from src.agents.models.snapshot import TurnSnapshot
from src.agents.state.state import (
    KnowledgeItem,
    PipelineState,
    SelectionStatus,
    TableMeta,
)
from src.models.enums import VisualizationType
from src.services.turn_snapshot_store import extract_snapshot_result_data
from src.utils.logger import get_logger

logger = get_logger(__name__)


async def save_turn_snapshot(state: PipelineState) -> dict[str, Any]:
    """턴 완료 시 현재 상태를 TurnSnapshot으로 추출하여 turn_snapshots에 저장한다.

    formatter 직후 파이프라인 마지막 단계에서 실행된다.
    예외 발생 시 경고 로그를 남기고 빈 dict를 반환하여 파이프라인을 계속 진행한다.

    Args:
        state: 포맷팅까지 완료된 현재 턴의 파이프라인 전체 상태.

    Returns:
        {"turn_snapshots": list[TurnSnapshot]} — LangGraph state update dict.
        스킵/실패 시 {} (state 변경 없음).
    """
    try:
        return _do_save(state)
    except Exception:
        logger.warning(
            "turn_snapshot 저장 실패 — 파이프라인 계속 진행",
            exc_info=True,
        )
        return {}


def _do_save(state: PipelineState) -> dict[str, Any]:
    """스냅샷 저장 핵심 로직. 예외는 호출자(save_turn_snapshot)가 처리."""
    reason = state.reason

    # ── 비데이터 턴 스킵 (I4): validated_sql 없으면 SQL 기반 CONTINUE 불가 ──
    if not reason.validated_sql:
        logger.debug(
            "turn_snapshot 스킵 — validated_sql 없음 (비데이터 턴)",
            turn_id=state.turn_id,
        )
        return {}

    # Path F' §11.5: REDISPLAY 경로도 저장한다 — visualization 갱신분 보존.

    snapshot = _build_snapshot(state)

    # ── 무제한 누적 (CHANGELOG #6): 저장 단계 FIFO 없음 ──
    # 참조 범위는 intent_classifier 산출 reference_turns 가 제한한다.
    new_list: list[Any] = [*state.turn_snapshots, snapshot]

    logger.debug(
        "turn_snapshot 저장 완료",
        turn_id=state.turn_id,
        user_message_seq=snapshot.user_message_seq,
        snapshot_count=len(new_list),
    )
    return {"turn_snapshots": new_list}


def _build_snapshot(state: PipelineState) -> TurnSnapshot:
    """현재 state에서 TurnSnapshot 13개 필드를 추출한다.

    Args:
        state: 현재 턴의 파이프라인 전체 상태.

    Returns:
        완성된 TurnSnapshot 인스턴스.
    """
    reason = state.reason

    # ── 필드 1: user_message_seq ──
    user_message_seq: int = state.current_user_message_seq or 0

    # ── 필드 2: intent ──
    intent = state.intent

    # ── 필드 3: generated_sql ──
    # validated_sql을 우선하고, 없으면 generated_sql 사용 (§3.1: "reason.validated_sql")
    generated_sql: str | None = reason.validated_sql or reason.generated_sql or None

    # ── 필드 4: sql_explanation ──
    sql_explanation: str = reason.sql_explanation or ""

    # ── 필드 5: result_data (rows 제외 — 용량 방어) ──
    # turn_snapshot_store.SNAPSHOT_RESULT_DATA_KEYS 단일 진실 공급원 사용.
    result_data: dict[str, Any] | None = extract_snapshot_result_data(
        state.result_data,
    )

    # ── 필드 6: visualization ──
    # VisualizationData를 dict로 변환 (judgment_reason 포함)
    visualization: dict[str, Any] | None = _extract_visualization(state)

    # ── 필드 7: selected_tables ──
    # reason.explored_tables 중 SelectionStatus.SELECTED만 필터링
    selected_tables: list[TableMeta] = [
        t for t in reason.explored_tables
        if t.selection_status == SelectionStatus.SELECTED
    ]

    # ── 필드 8: explored_codes ──
    # 이미 dict[str, CodeMeta] 형태 — 그대로 복사 (MongoDB 재조회 불필요)
    explored_codes = dict(reason.explored_codes)

    # ── 필드 9: inferred_signals ──
    # decision="INFER"이고 source_node != "intent_classifier"인 것만 (I4 규칙)
    inferred_signals = _extract_inferred_signals(state)

    # ── 필드 10: normalized_query (Path F' REGENERATE 복원용) ──
    # 정규화가 수행되지 않은 턴(비데이터/스킵)이면 None 유지
    normalized_query = state.normalized_query

    # ── 필드 11: knowledge_items (Path F' REGENERATE 복원용) ──
    # reason.knowledge_items 전량 복사. 지식 근거 복원용.
    knowledge_items: list[KnowledgeItem] = list(reason.knowledge_items)

    # ── 필드 12: query_decomposition (Path F' REGENERATE 복원용) ──
    # reason.query_decomposition 원본 dict 그대로 복사.
    query_decomposition: dict[str, Any] = dict(reason.query_decomposition)

    # ── 필드 13: target_db (Path F' REGENERATE 복원용) ──
    # target_db_resolver 결과 — REGENERATE 시 재호출 없이 동일 DB 로 재생성.
    target_db: str = reason.target_db or ""

    return TurnSnapshot(
        user_message_seq=user_message_seq,
        intent=intent,
        generated_sql=generated_sql,
        sql_explanation=sql_explanation,
        result_data=result_data,
        visualization=visualization,
        selected_tables=selected_tables,
        explored_codes=explored_codes,
        normalized_query=normalized_query,
        knowledge_items=knowledge_items,
        query_decomposition=query_decomposition,
        target_db=target_db,
        inferred_signals=inferred_signals,
    )


def _extract_visualization(state: PipelineState) -> dict[str, Any] | None:
    """state.visualization을 dict로 변환한다.

    VisualizationData 모델이 비어있으면 None을 반환한다.

    Args:
        state: 현재 파이프라인 상태.

    Returns:
        visualization dict 또는 None.
    """
    viz = state.visualization
    if viz is None:
        return None

    # VisualizationData Pydantic 모델인 경우 dict 변환
    if hasattr(viz, "model_dump"):
        viz_dict: dict[str, Any] = viz.model_dump(mode="json")
    elif isinstance(viz, dict):
        viz_dict = viz
    else:
        return None

    # 빈 시각화(chart_type이 없거나 NONE)이면 None 반환.
    # VisualizationType.NONE enum 값을 사용해 리네이밍 시 침묵 실패 방지.
    chart_type = viz_dict.get("chart_type", "")
    if not chart_type or chart_type == VisualizationType.NONE.value:
        return None

    return viz_dict


def _extract_inferred_signals(state: PipelineState) -> list[dict[str, Any]]:
    """자동 추론(INFER) 시그널만 필터링하여 dict list로 변환한다.

    필터 규칙 (§3.1 I4):
    - decision == "INFER" 인 것만 포함
    - source_node == "intent_classifier" 인 것은 제외
      (매 CONTINUE 턴마다 생성되어 스냅샷 축적 시 반복 노출 위험)
    - 현재 턴(turn_id)에 속하는 시그널만 포함

    Args:
        state: 현재 파이프라인 상태.

    Returns:
        INFER 시그널의 dict 리스트.
    """
    result: list[dict[str, Any]] = []
    tid = state.turn_id

    for s in state.resolved_signals:
        # decision 필터: INFER만
        if s.decision != "INFER":
            continue
        # source_node 필터: intent_classifier 제외 (I4)
        if s.source_node == "intent_classifier":
            continue
        # 턴 격리: 현재 턴 소속만 (None이면 포함 — 하위 호환)
        if tid and s.turn_id is not None and s.turn_id != tid:
            continue

        result.append({
            "question": s.question,
            "value": s.inferred_value or "",
            "source_node": s.source_node,
            "reason": s.reasoning or "",
        })

    return result
