"""Multi-Turn CONTINUE 오케스트레이터 노드 (Path F', 4-way).

작성자: 한철희 / 최종수정: 2026-04-20 16:27:03

CONTINUE 판정 이후 단일 LLM 호출로:
  1. 라우팅 카테고리(4-way) 결정 — redisplay / analyze / regenerate / refine
  2. 하류 노드용 지시문(handoff_note) 생성 — route별 강제 섹션 헤더 포함
  3. 근거(reasoning) 기록

참조 턴은 intent_classifier가 산출한 `state.reference_turns` 를 그대로 전파하며,
대표 스냅샷은 하류에서 `primary_reference_snapshot(state, history)` 헬퍼로 조회한다.

LangGraph 제약 1 준수 (설계 §4.3):
  이 노드에는 정적 엣지(add_edge/add_conditional_edges)를 연결하지 않는다.
  모든 라우팅을 Command(update=..., goto=...) 반환값으로만 처리한다.

라우팅 매핑 (모두 하류 노드 — 상류 회귀 없음, 순환 불가):
  redisplay  → visualizer       (SQL 재실행 없이 시각화·포맷 재렌더)
  analyze    → analyzer         (기존 결과 분석, DB 재조회 없음)
  regenerate → sql_generator    (정규화 동일, SQL 표현만 재작성)
  refine     → query_normalizer (질의 수정, 정상 플로우 합류하여 재정규화)

폴백 정책:
  - LLM 파싱 실패·빈 스냅샷 등 판정 불가 상황은 `error_end` 로 즉시 종료한다.
  - REGENERATE 판정이지만 대표 스냅샷에 normalized_query 가 없으면(과거 턴 호환)
    REFINE 으로 자동 다운그레이드한다. 새 섹션 헤더 규칙은 downgrade 이후에는
    재검증하지 않는다(note 본문은 REFINE 처리에서도 참고 힌트로 유용).

참조: docs/todo/20260418-continue-orchestrator-4way-redesign.md §3, §4, §5
"""

from __future__ import annotations

import asyncio
import copy
import re
from typing import Any

from langgraph.types import Command

from src.agents.models.snapshot import TurnSnapshot
from src.agents.nodes.system_prompts import (
    CONTINUE_ORCHESTRATOR_SYSTEM,
    CONTINUE_ORCHESTRATOR_USER,
)
from src.agents.nodes.thinking_modes import LLMNode
from src.agents.state.state import (
    PipelineState,
    QueryStatus,
    ReasoningState,
    add_trace,
)
from src.models.enums import ContinueRoute, IntentType
from src.models.result import SQLResult, VisualizationData
from src.utils.llm import llm_call_with_parse_retry
from src.utils.llm.response import extract_json
from src.utils.logger import get_logger
from src.utils.tracker.dispatch import REASONING_STEP, dispatch_tracking_event

logger = get_logger(__name__)

# trace_log 기록 시 node 레이블 (단일 진실 공급원)
_TRACE_NODE_LABEL = "연속질의오케스트레이터"

# 라우팅 키 → 다음 노드 매핑 (단일 진실 공급원, 설계 §3.2)
# 모두 하류 노드 — 상류 회귀 없음 → 순환 불가 → 재진입 가드 불필요.
# code-style.md "Enum 사용 의무": raw string 대신 ContinueRoute 멤버 사용.
_ROUTE_TO_NODE: dict[ContinueRoute, str] = {
    ContinueRoute.REDISPLAY:  "visualizer",
    ContinueRoute.ANALYZE:    "analyzer",
    ContinueRoute.REGENERATE: "sql_generator",
    ContinueRoute.REFINE:     "query_normalizer",
}

# route별 handoff_note 필수 섹션 헤더 (설계 §5).
# LLM이 route에 맞는 섹션을 생성하지 못하면 하류가 명확한 지시를 받지 못하므로 error_end.
# 모든 헤더는 "### " 시작(프롬프트 포맷과 동일) — 정확 일치 기준.
#
# REGENERATE: `### SQL 생성 지시`만 필요. NormalizedQuery 는 스냅샷에서 그대로
#   hydrate 되므로 "정규화 변경 요약" 섹션은 불필요(정의상 정규화 무변경 경로).
_ROUTE_REQUIRED_HEADERS: dict[ContinueRoute, tuple[str, ...]] = {
    ContinueRoute.REDISPLAY:  ("### 시각화/포맷 지시",),
    ContinueRoute.ANALYZE:    ("### 분석 초점",),
    ContinueRoute.REGENERATE: ("### SQL 생성 지시",),
    ContinueRoute.REFINE:     ("### 연속 처리 의도",),
}

# Enum 완전성 보증 — 새 route 추가 시 매핑 누락을 즉시 감지 (import 시점).
assert set(_ROUTE_TO_NODE.keys()) == set(ContinueRoute), (
    "_ROUTE_TO_NODE 에 누락된 ContinueRoute 있음"
)
assert set(_ROUTE_REQUIRED_HEADERS.keys()) == set(ContinueRoute), (
    "_ROUTE_REQUIRED_HEADERS 에 누락된 ContinueRoute 있음"
)


def _validate_handoff_note_headers(route: ContinueRoute, note: str) -> list[str]:
    """route별 필수 섹션 헤더를 검증하여 위반 메시지 리스트를 반환한다.

    정확 일치(exact substring) 기준 — 오타·공백 차이를 엄격히 잡는다.
    위반이 없으면 빈 리스트를 반환한다.

    Args:
        route: 오케스트레이터가 판정한 라우팅 카테고리.
        note: LLM이 생성한 handoff_note 본문.

    Returns:
        ["필수 헤더 누락: ###..."] 형태 메시지 리스트.
    """
    errors: list[str] = []
    for required in _ROUTE_REQUIRED_HEADERS[route]:
        if required not in note:
            errors.append(f"필수 헤더 누락: {required!r}")
    return errors

# 판정 불가 폴백 시 사용자에게 노출할 에러 메시지 (user_interaction.md 준수).
_ORCHESTRATOR_FALLBACK_ERROR = (
    "연속 질의를 이해하지 못했습니다. 새 질문으로 다시 시도해주세요."
)


def _summarize_result_data(result_data: dict | None) -> str:
    """스냅샷 result_data 에서 columns·total_count 만 1줄 요약."""
    if result_data and isinstance(result_data, dict):
        cols = result_data.get("columns", [])
        total = result_data.get("total_count", "?")
        return f"컬럼: {cols}, 결과건수: {total}"
    return "(결과 없음)"


def _summarize_inferred_signals(inferred: list[dict] | None) -> str:
    """INFER 시그널 첫 2개만 '질문 → 값' 형태로 연결."""
    if not inferred:
        return ""
    return "; ".join(
        f"{s.get('question', '')} → {s.get('value', '')}"
        for s in inferred[:2]
    )


def _summarize_normalized_query(nq: Any) -> str:
    """NormalizedQuery 요약 — rewritten(또는 original) 120자."""
    if nq is None:
        return ""
    rewritten = (
        getattr(nq, "rewritten_query", "")
        or getattr(nq, "original_query", "")
    )
    return rewritten[:120] if rewritten else ""


def _snapshot_to_lines(snap: TurnSnapshot) -> list[str]:
    """단일 스냅샷을 YAML 유사 텍스트 라인 리스트로 변환 (rows 절대 포함 X)."""
    seq = snap.user_message_seq
    intent_val = snap.intent.value if snap.intent else IntentType.UNKNOWN.value
    sql_exp = snap.sql_explanation or ""
    data_summary = _summarize_result_data(snap.result_data)
    table_names = [t.table_name for t in snap.selected_tables]
    infer_summary = _summarize_inferred_signals(snap.inferred_signals)
    nq_summary = _summarize_normalized_query(snap.normalized_query)

    lines = [f"- user_message_seq: {seq}", f"  intent: {intent_val}"]
    if sql_exp:
        lines.append(f"  sql_explanation: \"{sql_exp}\"")
    if nq_summary:
        lines.append(f"  normalized_query: \"{nq_summary}\"")
    lines.append(f"  result_data: {{{data_summary}}}")
    if table_names:
        lines.append(f"  selected_tables: {table_names}")
    if infer_summary:
        lines.append(f"  inferred_signals: \"{infer_summary}\"")
    return lines


_SERIALIZE_SNAPSHOT_LIMIT = 4
"""LLM 프롬프트 조립 시 직렬화할 스냅샷 최대 개수(M13, 방어적 상한).

설계 §5 의 본래 의도는 `intent_classifier.reference_turns` 의 T-라벨을
ConversationHistory 에서 user_message_seq 로 resolve 하여 해당 스냅샷만
선택적으로 직렬화하는 것이다. 그러나 ConversationHistory 클래스는 아직
미구현(Phase 3 작업) 상태이므로 현재는 `state.turn_snapshots` 전량을 넘기며,
누적 시 토큰 폭증·오케스트레이터 혼동 위험이 존재한다.

임시 방어선으로 '가장 최근 N턴'만 보내고, ConversationHistory 도입 시 본
상한을 제거하고 reference_turns 기반 선택으로 교체한다.
turn_snapshot_store._MAX_SNAPSHOTS(4) 와 동일 값으로 맞춰 의미적 정합을 유지.
"""


def _serialize_snapshots(snapshots: list[Any]) -> str:
    """TurnSnapshot 목록을 LLM 프롬프트용 YAML 유사 텍스트로 직렬화한다.

    rows는 절대 포함하지 않으며, 핵심 필드만 요약하여 토큰 예산을 절약한다.
    폐쇄망 Qwen3.5 397B 대응을 위해 1턴당 5~8줄 이내로 제한한다 (설계 §5).

    M13(임시 방어 상한): 가장 최근 `_SERIALIZE_SNAPSHOT_LIMIT` 턴만 포함.
    ConversationHistory 도입 후 `reference_turns` 기반 선택으로 대체 예정.

    Args:
        snapshots: PipelineState.turn_snapshots (list[TurnSnapshot]).
            Any로 선언된 필드이므로 런타임 캐스팅 없이 속성 접근.

    Returns:
        스냅샷 요약 텍스트. 비어있으면 "(이전 턴 스냅샷 없음)".
    """
    if not snapshots:
        return "(이전 턴 스냅샷 없음)"

    recent = snapshots[-_SERIALIZE_SNAPSHOT_LIMIT:]
    lines: list[str] = []
    for snap in recent:
        lines.extend(_snapshot_to_lines(snap))
    return "\n".join(lines)


def _parse_orchestrator_response(raw: str) -> dict:
    """LLM 응답 JSON을 파싱하고 필드를 검증한다.

    정규식 기반 백업 파싱 포함 (폐쇄망 70B 모델 JSON 불안정 대응, 설계 §5.3).

    Args:
        raw: LLM 원본 응답 텍스트.

    Returns:
        검증된 파싱 결과 dict.

    Raises:
        ValueError: 필수 필드 누락 또는 허용되지 않는 값.
    """
    # 1차: extract_json (strict=False — 마크다운 펜스 등 제거)
    data = extract_json(raw, strict=False)

    # 2차: 정규식 기반 백업 파싱 (JSON 파싱 실패 시)
    if data is None:
        data = _regex_fallback_parse(raw)

    if not isinstance(data, dict):
        raise ValueError(f"JSON 파싱 실패 (길이={len(raw)})")

    # ── 필수 필드 검증 (설계 §3.2.3: 3 필드 OUTPUT — route/handoff_note/reasoning) ──
    raw_route = str(data.get("route", "")).lower().strip()
    try:
        route = ContinueRoute(raw_route)
    except ValueError as exc:
        raise ValueError(f"허용되지 않는 route: {raw_route!r}") from exc

    return {
        "route": route,
        "handoff_note": str(data.get("handoff_note", "")).strip(),
        "reasoning": str(data.get("reasoning", "")).strip(),
    }


def _regex_fallback_parse(raw: str) -> dict | None:
    """정규식으로 JSON 필드를 개별 추출한다 (폐쇄망 LLM 대응).

    extract_json 실패 시 마지막 수단으로 호출된다. 3 필드만 추출한다.
    """
    def _extract(pattern: str, default: str = "") -> str:
        m = re.search(pattern, raw, re.DOTALL | re.IGNORECASE)
        return m.group(1).strip().strip('"') if m else default

    route = _extract(r'"route"\s*:\s*"([^"]+)"')
    if not route:
        return None

    return {
        "route": route,
        "handoff_note": _extract(r'"handoff_note"\s*:\s*"([^"]*)"'),
        "reasoning": _extract(r'"reasoning"\s*:\s*"([^"]*)"'),
    }


class _HydrationRowsError(RuntimeError):
    """REDISPLAY/ANALYZE 경로에서 rows 복원에 최종 실패했음을 알리는 예외.

    metadata JIT fetch 와 SQL 재실행 fallback 이 모두 실패한 경우에만 발생.
    caller(`continue_orchestrator`)가 catch 하여 error_end 로 전환한다.
    """


# metadata JIT fetch 타임아웃 — checkpointer_pool 이 hang 되었을 때 파이프라인을
# 가로막지 않도록 컨넥션 풀 대기 상한과 동일한 값을 상한으로 사용한다.
_METADATA_ROWS_FETCH_TIMEOUT_SEC = 30


async def _fetch_rows_from_metadata(
    session_id: str,
    snapshot_user_seq: int,
) -> list[dict[str, Any]] | None:
    """스냅샷의 user_message_seq 직후 assistant 응답에서 rows 를 JIT 조회한다.

    Path F' §3.4: rows 는 스냅샷이 아닌 checkpoint_dc_messages.metadata.result_data.rows
    가 단일 원천. REDISPLAY/ANALYZE 진입 시에만 호출하여 용량 누적을 회피한다.

    명확화 턴(executed_sql IS NULL)은 매핑 대상에서 제외한다.

    **반환값 의미 구분(C2)**:
        - `list` (비어있을 수 있음): 조회 성공, metadata 의 rows 값 그대로.
        - `None`: 조회 실패(세션 누락·pool 미초기화·타임아웃·예외·matching row 없음·
          redaction 으로 rows 가 NULL). caller 가 SQL 재실행 fallback 을 시도.

    Args:
        session_id: 세션 ID (thread_id).
        snapshot_user_seq: 스냅샷의 user_message_seq.

    Returns:
        rows 리스트(성공) 또는 None(실패).
    """
    if not session_id or snapshot_user_seq <= 0:
        return None

    # 지연 import — 모듈 레벨 순환 의존 방지.
    from src.connectors.manager import get_connector_manager  # noqa: PLC0415

    pool = get_connector_manager().checkpointer_pool
    if pool is None:
        logger.warning(
            "rows JIT fetch 실패 — checkpointer_pool 미초기화",
            session_id=session_id,
        )
        return None

    try:
        return await asyncio.wait_for(
            _fetch_rows_from_metadata_inner(pool, session_id, snapshot_user_seq),
            timeout=_METADATA_ROWS_FETCH_TIMEOUT_SEC,
        )
    except TimeoutError:
        logger.warning(
            "rows JIT fetch 타임아웃",
            session_id=session_id,
            snapshot_user_seq=snapshot_user_seq,
            timeout=_METADATA_ROWS_FETCH_TIMEOUT_SEC,
        )
        return None
    except Exception:
        logger.warning(
            "rows JIT fetch 예외 — fallback 시도",
            session_id=session_id,
            snapshot_user_seq=snapshot_user_seq,
            exc_info=True,
        )
        return None


async def _fetch_rows_from_metadata_inner(
    pool: Any,
    session_id: str,
    snapshot_user_seq: int,
) -> list[dict[str, Any]] | None:
    """metadata SELECT 본체 — 타임아웃 wrapper 와 분리하여 가독성 유지."""
    async with pool.connection() as conn:
        result = await conn.execute(
            """
            SELECT metadata->'result_data'->'rows' AS rows
            FROM checkpoint_dc_messages
            WHERE thread_id       = %(thread_id)s
              AND role             = 'assistant'
              AND message_type     = 'normal'
              AND status           = 'success'
              AND executed_sql IS NOT NULL
              AND seq              > %(user_seq)s
            ORDER BY seq ASC
            LIMIT 1
            """,
            {"thread_id": session_id, "user_seq": snapshot_user_seq},
        )
        row = await result.fetchone()
        if row is None or row["rows"] is None:
            # matching message 없음 or redaction 으로 NULL — 실패로 간주해
            # caller 가 SQL 재실행 fallback 을 시도하도록 한다.
            return None
        raw = row["rows"]
        if isinstance(raw, list):
            return raw
        return None


async def _fetch_rows_via_sql_reexecute(
    snapshot: TurnSnapshot,
) -> list[dict[str, Any]] | None:
    """metadata JIT fetch 실패 시 타겟 DB 에 SQL 을 재실행하여 rows 를 복원한다.

    Path F' §3.4 C2 fallback: metadata 가 소실/재액션되었거나 pool hang 으로
    JIT fetch 가 실패한 경우의 최후 수단. 스냅샷의 generated_sql 과 target_db
    조합을 그대로 재사용한다(동일 SQL → 동일 결과 가정).

    읽기 전용 계정으로 실행되며, 각 커넥터 구현이 자체 timeout 을 이미 적용한다.

    Args:
        snapshot: 대표 TurnSnapshot (generated_sql, target_db 필수).

    Returns:
        rows 리스트(성공) 또는 None(SQL 없음·target_db 없음·실행 실패).
    """
    sql = (snapshot.generated_sql or "").strip()
    target_db = (snapshot.target_db or "").strip()
    if not sql or not target_db:
        logger.warning(
            "SQL 재실행 fallback 스킵 — 스냅샷 필수 필드 누락",
            has_sql=bool(sql),
            has_target_db=bool(target_db),
            user_message_seq=snapshot.user_message_seq,
        )
        return None

    # 지연 import — 모듈 레벨 순환 의존 방지.
    from src.connectors.manager import get_connector_manager  # noqa: PLC0415

    try:
        conn = get_connector_manager().get_query_db(db_source=target_db)
        return await conn.execute_query(sql)
    except Exception:
        logger.warning(
            "SQL 재실행 fallback 실패",
            target_db=target_db,
            user_message_seq=snapshot.user_message_seq,
            exc_info=True,
        )
        return None


async def _build_hydration_updates(
    route: ContinueRoute,
    snapshot: TurnSnapshot | None,
    state: PipelineState,
) -> dict[str, Any]:
    """route별로 스냅샷을 현재 state로 복원한다 (Path F' §4.4.3).

    원칙:
        - Route-agnostic 전량 복원 — REFINE 을 제외한 모든 경로에서 동일한
          ReasoningState 기반 복원을 수행한다. 각 route 가 필요한 필드만
          부분 복원하는 기존 방식은 조건 분기가 누적되어 유지보수성이 낮아지므로
          Path F' 에서 폐기됨.
        - REFINE 만 예외 — query_normalizer 가 새 정규화를 수행해야 하므로
          스냅샷 hydration 을 건너뛴다 (오염 방지).

    경로별 복원 대상:
        REDISPLAY  : ReasoningState + sql_result(+JIT rows) + visualization
        ANALYZE    : ReasoningState + sql_result(+JIT rows)
                     (analyzer→visualizer 가 새로 시각화)
        REGENERATE : ReasoningState + normalized_query (정규화 재사용)
                     reasoning_preparer·query_normalizer 를 건너뛰고 sql_generator 직행.
        REFINE     : 빈 dict — query_normalizer 가 새로 정규화 수행.

    Args:
        route: 오케스트레이터가 판정한 라우팅 카테고리 (폴백 다운그레이드 반영된 값).
        snapshot: 대표 스냅샷 (없으면 빈 dict 반환).
        state: 현재 파이프라인 상태 (rows JIT fetch 시 session_id 참조).

    Returns:
        Command(update=...)에 병합할 hydration 필드 dict.
    """
    if snapshot is None:
        return {}

    # REFINE: query_normalizer 가 새 정규화 — 스냅샷 hydration 건너뛴다.
    if route is ContinueRoute.REFINE:
        return {}

    updates: dict[str, Any] = {}

    # ── 최상위 normalized_query (모든 non-REFINE 경로 공통) ──
    # REDISPLAY/ANALYZE 에서도 reasoning_decisions 렌더링·validator context 에서
    # NQ 를 참조할 수 있으므로 복원한다. REGENERATE 에서는 sql_generator 재사용 핵심.
    # 하류 노드의 우발적 변경이 스냅샷(frozen 오브젝트의 mutable 하위 필드)까지
    # 오염시키지 않도록 deep copy 로 분리한다 (M9).
    if snapshot.normalized_query is not None:
        updates["normalized_query"] = snapshot.normalized_query.model_copy(deep=True)

    # ── ReasoningState 통째 복원 (target_db 포함, Path F' §4.4.3) ──
    # CONTINUE 턴은 turn_reset_updates() 로 ReasoningState() 가 이미 초기화된
    # 상태. 스냅샷의 풀 메타를 주입한 신규 인스턴스로 교체한다.
    # mutable 컨테이너(list/dict)와 Pydantic 모델은 모두 deep copy (M9).
    reason = ReasoningState(
        knowledge_items=copy.deepcopy(snapshot.knowledge_items),
        query_decomposition=copy.deepcopy(snapshot.query_decomposition),
        target_db=snapshot.target_db,
        explored_tables=copy.deepcopy(snapshot.selected_tables),
        explored_codes=copy.deepcopy(snapshot.explored_codes),
    )
    # 모든 CONTINUE 경로에서 직전 턴 SQL 을 previous_turn_sql 로 복원 →
    # sql_generator / recovery_agent 가 {previous_sql} 로 read-only 참조한다 (§14.3.6).
    # 현재 턴 결과(generated_sql/sql_explanation) 와는 직교 분리 — hydration 에서
    # 현재 턴 필드를 건드리지 않는다(R3: hydration 외 write 금지).
    if snapshot.generated_sql:
        reason.previous_turn_sql = snapshot.generated_sql
        reason.previous_turn_sql_explanation = snapshot.sql_explanation or ""
    updates["reason"] = reason

    # ── REDISPLAY / ANALYZE: sql_result + JIT rows fetch (+SQL 재실행 fallback) ──
    if route in {ContinueRoute.REDISPLAY, ContinueRoute.ANALYZE}:
        snap_result_data = snapshot.result_data or {}
        # Path F' §3.4 C2: 1차 metadata JIT fetch → 실패 시 2차 SQL 재실행.
        # 둘 다 실패하면 rows 없이 진행할 수 없으므로 _HydrationRowsError 발생
        # → caller 가 error_end 로 전환.
        rows = await _fetch_rows_from_metadata(
            state.session_id,
            snapshot.user_message_seq,
        )
        if rows is None:
            logger.info(
                "metadata JIT fetch 실패 — SQL 재실행 fallback 시도",
                session_id=state.session_id,
                user_message_seq=snapshot.user_message_seq,
            )
            rows = await _fetch_rows_via_sql_reexecute(snapshot)
        if rows is None:
            raise _HydrationRowsError(
                f"rows 복원 실패 — metadata JIT fetch 및 SQL 재실행 모두 실패 "
                f"(route={route.value}, "
                f"user_message_seq={snapshot.user_message_seq})",
            )
        updates["sql_result"] = SQLResult(
            columns=list(snap_result_data.get("columns", [])),
            rows=rows,
            row_count=snap_result_data.get("total_count", 0)
                or snap_result_data.get("displayed_count", 0),
        )

    # ── REDISPLAY 전용: 기존 visualization 복원 (ANALYZE 는 새로 생성) ──
    if route is ContinueRoute.REDISPLAY and snapshot.visualization:
        from pydantic import ValidationError  # noqa: PLC0415
        try:
            # VisualizationData 생성 시점에 스냅샷 dict 를 복사 — 이후 하류가
            # 모델의 mutable sub-field 를 수정해도 스냅샷에 영향 없음.
            updates["visualization"] = VisualizationData(
                **copy.deepcopy(snapshot.visualization),
            )
        except (ValidationError, TypeError) as viz_exc:
            # 스냅샷 visualization 스키마가 현재 모델과 mismatch (폐쇄망 운영
            # 중 모델 업그레이드 등) — visualizer 재생성에 맡기고 hydrate 스킵.
            logger.warning(
                "visualization hydrate 실패 — visualizer가 재생성",
                error=str(viz_exc),
            )

    return updates


def _build_error_end_command(state: PipelineState, reason: str) -> Command:
    """판정 불가 상황에서 error_end로 즉시 종료하는 Command를 생성한다.

    4개 라우트 모두 하류 노드이므로 상류 회귀 경로가 없다.
    빈 스냅샷·LLM 파싱 실패 등 판정 불가 상황은 즉시 종료한다.

    Args:
        state: 현재 파이프라인 상태.
        reason: error_end 전환 사유 (로그/trace 용).

    Returns:
        goto="error_end" Command 인스턴스.
    """
    logger.error("continue_orchestrator → error_end", reason=reason)
    trace = add_trace(state, _TRACE_NODE_LABEL, "ERROR_END", reason)

    return Command(
        update={
            "status": QueryStatus.ERROR,
            "error_message": _ORCHESTRATOR_FALLBACK_ERROR,
            "trace_log": trace,
        },
        goto="error_end",
    )


def _build_interpretation_block(state: PipelineState) -> str:
    """`## A. 해석` 본문을 state 최소 필드로 조립한다.

    설계 §3.2.1 A 블록은 intent_classifier 산출물(intent·query_category·
    continue_context·pending_signals·analysis_query 등)을 포함하도록 규정하나,
    현재 state에는 일부만 존재한다. 가용 필드만 직렬화하고 누락은 `(없음)`으로 표기.
    intent_classifier 가 추가 산출물을 낼 때 이 함수를 직접 확장한다.
    """
    intent_val = state.intent.value if state.intent else "(없음)"
    preprocessed = (state.preprocessed_input or "").strip() or "(없음)"
    ref_turns = list(state.reference_turns) if state.reference_turns else []
    ref_display = "[" + ", ".join(f'"{t}"' for t in ref_turns) + "]" if ref_turns else "(없음)"

    lines = [
        "### 질의 유형",
        f"- 질의 유형: {intent_val}",
        f"- 맥락 결합 발화: {preprocessed}",
        "",
        "### 연속성 — 참조 턴",
        f"- 참조 턴: {ref_display}",
    ]
    return "\n".join(lines)


def _resolve_primary_snapshot(
    state: PipelineState,
) -> TurnSnapshot | None:
    """대표 스냅샷을 반환한다 (설계 §3.5).

    설계상 `reference_turns[-1]` T-라벨 → ConversationHistory.seq_of →
    turn_snapshots 매핑이 원칙. 그러나 ConversationHistory 클래스가
    아직 구현되지 않아(별도 설계 20260417-conversation-history-class-design.md),
    **현재는 일관되게 가장 최근 스냅샷으로 폴백**한다 — reference_turns 유무로
    분기하지 않는다. 분기해도 어차피 동일한 fallback 경로를 타므로 혼동만 준다.
    클래스 도입 후 설계 §3.5 헬퍼 `primary_reference_snapshot(state, history)`
    로 교체 예정 (L4).
    """
    if not state.turn_snapshots:
        return None
    return state.turn_snapshots[-1]


async def continue_orchestrator_node(
    state: PipelineState,
) -> Command:
    """CONTINUE 판정 후 라우팅 전담 노드 (설계 §3.2, §3.5, §4.3).

    LLM 1회 호출로 3 필드(route/handoff_note/reasoning)를 출력하고,
    참조 턴은 intent_classifier 산출물인 `state.reference_turns`를 그대로
    전파한다 (단일 진실 공급원, 설계 §3.5).

    Returns:
        Command 인스턴스. goto는 route에 따른 다음 노드명.
    """
    # ── 스냅샷 없음 → error_end (4개 라우트 모두 하류이므로 상류 회귀 불가) ──
    if not state.turn_snapshots:
        return _build_error_end_command(
            state,
            "turn_snapshots가 비어있음 — CONTINUE 판정 후 참조 가능한 이전 턴 없음",
        )

    # ── 3블록 조립 (프롬프트 §3.2.1 placeholders 정합) ──
    # ConversationHistory 클래스 미구현 상태에서는 A 블록을 state 최소 필드,
    # B 블록을 turn_snapshots 직렬화로 조립한다. 설계 §3.2.1 완전 준수는
    # ConversationHistory 도입 후 `_build_interpretation_block` 확장으로 처리.
    interpretation_block = _build_interpretation_block(state)
    reference_turns_block = _serialize_snapshots(state.turn_snapshots)
    current_utterance = (state.preprocessed_input or "").strip()

    user_prompt = CONTINUE_ORCHESTRATOR_USER.format(
        interpretation_block=interpretation_block,
        reference_turns_block=reference_turns_block,
        current_utterance=current_utterance,
    )

    # ── LLM 호출 ──
    try:
        _, parsed = await llm_call_with_parse_retry(
            system=CONTINUE_ORCHESTRATOR_SYSTEM,
            messages=[{"role": "user", "content": user_prompt}],
            parse_fn=_parse_orchestrator_response,
            node_name=LLMNode.CONTINUE_ORCHESTRATOR,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("continue_orchestrator LLM 호출/파싱 실패", error=str(exc))
        return _build_error_end_command(state, f"LLM 호출/파싱 실패: {exc}")

    route: ContinueRoute = parsed["route"]
    handoff_note: str = parsed["handoff_note"]
    reasoning: str = parsed["reasoning"]

    # ── handoff_note 섹션 헤더 규칙 검증 (§5) ──
    # 빈 값 + 모든 route 필수 헤더 조건을 일괄 검증. 어느 한 규칙이라도 위반하면
    # 하류가 명확한 지시를 받을 수 없으므로 error_end.
    header_errors = _validate_handoff_note_headers(route, handoff_note)
    if header_errors:
        return _build_error_end_command(
            state,
            f"{route.value} route handoff_note 헤더 규칙 위반: {header_errors}",
        )

    # ── 대표 스냅샷 조회 (intent_classifier 산출 reference_turns 기반) ──
    snapshot = _resolve_primary_snapshot(state)

    # ── REGENERATE 폴백 가드 — 재생성 필수 필드 누락 시 REFINE 다운그레이드 ──
    # 과거 턴(필드 도입 전) 호환. downgrade 이후에는 헤더 규칙을 재검증하지 않으며
    # (REFINE 필수 헤더가 없어도) note 본문은 힌트로 그대로 하류에 전달된다.
    # 체크 필드:
    #   - normalized_query: sql_generator 재사용의 핵심 입력. 없으면 재생성 불가.
    #   - target_db: dialect 결정자(M8). 비어있으면 sql_generator 가 커넥터를
    #     선택할 수 없어 downstream 실패. REFINE 으로 돌려 target_db_resolver 재실행.
    if route is ContinueRoute.REGENERATE:
        snap_nq = snapshot.normalized_query if snapshot else None
        snap_target_db = snapshot.target_db.strip() if snapshot else ""
        if snap_nq is None or not snap_target_db:
            logger.warning(
                "REGENERATE → REFINE 다운그레이드 (필수 필드 누락)",
                turn_id=state.turn_id,
                has_normalized_query=snap_nq is not None,
                has_target_db=bool(snap_target_db),
            )
            route = ContinueRoute.REFINE

    # ── intent 결정 (설계 §3.5 C2: 오라우팅 방지) ──
    #   ANALYZE    : DATA_ANALYSIS 로 교체 (스냅샷 intent 무시)
    #   REDISPLAY  : 대표 스냅샷 intent 유지 (재시각화·포맷만 변경)
    #   REGENERATE : 대표 스냅샷 intent 유지 (SQL 표현만 재작성, 정규화 동일)
    #   REFINE     : 대표 스냅샷 intent 유지 (기본 DATA_EXTRACTION)
    if route is ContinueRoute.ANALYZE:
        new_intent: IntentType = IntentType.DATA_ANALYSIS
    elif snapshot is not None:
        new_intent = snapshot.intent or state.intent
    else:
        new_intent = state.intent or IntentType.DATA_EXTRACTION

    # ── route별 state hydration (헬퍼 위임, Path F' §4.4.3) ──
    # Route-agnostic 전량 복원 (REFINE 제외).
    # REDISPLAY/ANALYZE: ReasoningState + sql_result(+JIT rows) (+REDISPLAY viz).
    # REGENERATE: ReasoningState + normalized_query — sql_generator 직행.
    # REFINE: 빈 dict — query_normalizer 가 새로 정규화 수행.
    # Path F' §3.4 C2: rows 복원 실패(metadata JIT + SQL 재실행 모두 실패)는
    # 하류에 진행할 데이터가 없다는 의미이므로 error_end 로 즉시 종료한다.
    try:
        hydration_updates = await _build_hydration_updates(route, snapshot, state)
    except _HydrationRowsError as exc:
        return _build_error_end_command(state, str(exc))

    # ── 다음 노드 결정 ──
    next_node = _ROUTE_TO_NODE[route]

    # ── trace_log 기록 ──
    ref_label = state.reference_turns[-1] if state.reference_turns else "(없음)"
    trace = add_trace(
        state,
        _TRACE_NODE_LABEL,
        f"{route.value.upper()} → {next_node}",
        f"참조턴={ref_label}, 지시={handoff_note[:60]}, 근거={reasoning[:80]}",
    )

    # ── 추적 이벤트 ──
    await dispatch_tracking_event(REASONING_STEP, {
        "node": "continue_orchestrator",
        "phase": "interpret",
        "step_type": "llm_decision",
        "round": 0,
        "hypothesis_id": "",
        "inputs": {
            "query": state.preprocessed_input,
            "snapshots_count": len(state.turn_snapshots),
            "reference_turns": list(state.reference_turns),
        },
        "output": {
            "route": route.value,
            "handoff_note": handoff_note,
            "reasoning": reasoning,
        },
        "routing": {
            "next_node": next_node,
            "reason": f"route={route.value}",
        },
    })

    # ── Command 반환 (설계 §3.5 단일 저장소 원칙) ──
    # reference_snapshot 중간 필드는 두지 않는다. 하류 노드는
    # primary_reference_snapshot(state, history) 헬퍼로 turn_snapshots에서 직접 조회.
    update: dict[str, Any] = {
        "route":           route,
        "handoff_note":    handoff_note,
        "reference_turns": list(state.reference_turns),  # intent_classifier 산출 전파
        "intent":          new_intent,                    # ★ 오라우팅 방지 (C2)
        "status":          QueryStatus.INTENT_CLASSIFIED,
        "trace_log":       trace,
        **hydration_updates,
    }

    return Command(update=update, goto=next_node)
