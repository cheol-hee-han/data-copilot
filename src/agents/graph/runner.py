"""파이프라인 실행 엔트리포인트 — LangGraph 그래프의 최상위 호출 인터페이스.

API 서버(server.py)나 CLI 에서 호출되어 LangGraph 파이프라인을 초기화·실행하고
최종 PipelineResult 를 반환하는 진입점이다.
커넥터 매니저를 통해 데이터 소스(실제/Dummy)를 연결하고, EvaluationTracker 로
노드별 실행 계측을 수행하며, 실행 완료 후 SQL 생성·검증·실행 결과를 트래커에 기록한다.
시각화 데이터가 있으면 VisualizationData 에 담아 함께 반환한다.
on_event 콜백을 통해 WebSocket으로 실시간 진행 상황을 전달할 수 있다.

핵심 함수:
    - run_pipeline: 사용자 입력을 받아 파이프라인을 실행하고 PipelineResult 를 반환
    - main: CLI 엔트리포인트 (python -m src.agents.graph.runner '질의 내용')

사용법:
    python -m src.agents.graph.runner "이번 달 신규 고객 수 알려줘"
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from typing import Any, Callable, Awaitable

from src.connectors.manager import get_connector_manager
from src.agents.graph.pipeline import create_app
from src.agents.models.response import PipelineResult
from src.models.result import SQLResult, VisualizationData
from src.agents.state.state import PipelineState
from src.services.insight_builder import build_insight
from src.utils.tracker import EvaluationTracker
from src.tools.langsmith import setup_langsmith
from src.utils.logger import (
    bind_query_context,
    clear_query_context,
    get_logger,
    setup_logging,
)

logger = get_logger(__name__)

# 파이프라인 노드 → 사용자 친화적 진행 단계 매핑 (IT 용어 배제)
NODE_PROGRESS_MAP: dict[str, dict[str, str]] = {
    "classify_intent": {
        "label": "🔍 질문을 분석하고 있습니다",
        "thinking": "질문 의도 파악 중",
    },
    "normalize_query": {
        "label": "🔍 질문을 정리하고 있습니다",
        "thinking": "질문 정규화 중",
    },
    "reason_plan": {
        "label": "🧠 데이터 탐색 전략을 세우고 있습니다",
        "thinking": "탐색 계획 수립 중",
    },
    "reason_explore": {
        "label": "📂 관련 테이블과 데이터를 찾고 있습니다",
        "thinking": "데이터 소스 탐색 중",
    },
    "reason_verify_tables": {
        "label": "🔗 테이블 구성을 검증하고 있습니다",
        "thinking": "테이블 충족성 검증 중",
    },
    "reason_generate_sql": {
        "label": "⚙️ 조회 조건을 작성하고 있습니다",
        "thinking": "SQL 생성 중",
    },
    "reason_validate_sql": {
        "label": "✅ 조회 조건을 검증하고 있습니다",
        "thinking": "SQL 검증 중",
    },
    "reason_recover": {
        "label": "🔄 다른 방법을 시도하고 있습니다",
        "thinking": "대안 탐색 중",
    },
    "execute_sql": {
        "label": "🗄️ 데이터를 조회하고 있습니다",
        "thinking": "데이터베이스 조회 중",
    },
    "analyze_data": {
        "label": "📊 결과를 분석하고 있습니다",
        "thinking": "데이터 분석 중",
    },
    "format_response": {
        "label": "📝 보고서를 작성하고 있습니다",
        "thinking": "결과 정리 중",
    },
}

# on_event 콜백 타입 (async 함수)
OnEventCallback = Callable[[dict[str, Any]], Awaitable[None]]


async def _emit_progress(
    on_event: OnEventCallback | None,
    node_name: str,
    action: str,
    *,
    explore_count: int = 0,
) -> None:
    """노드 시작/완료 시 progress 이벤트를 전송한다."""
    if on_event is None or node_name not in NODE_PROGRESS_MAP:
        return

    info = NODE_PROGRESS_MAP[node_name]
    label = info["label"]

    # reason_explore 루프 반복 시 카운터 표시
    if node_name == "reason_explore" and explore_count > 1:
        label = f"📂 추가 데이터를 탐색하고 있습니다 ({explore_count}차)"

    msg: dict[str, Any] = {
        "type": "progress",
        "action": action,
        "label": label,
        "thinkingLabel": info["thinking"],
    }
    try:
        await on_event(msg)
    except Exception:
        logger.debug("progress 이벤트 전송 실패", node=node_name)


async def run_pipeline(
    user_input: str,
    session_id: str | None = None,
    conversation_history: list[dict[str, str]] | None = None,
    tracker: EvaluationTracker | None = None,
    *,
    clarification_state: dict[str, Any] | None = None,
    on_event: OnEventCallback | None = None,
) -> PipelineResult:
    """파이프라인을 실행하고 최종 결과를 반환한다.

    Args:
        tracker: 평가 트래커. 제공 시 노드 계측이 적용된다.
            None이면 기본 트래커를 생성한다.
        on_event: WebSocket 등으로 실시간 이벤트를 전송하는
            async 콜백. None이면 이벤트를 전송하지 않는다.

    반환값의 str() 은 기존처럼 formatted_response 문자열이므로
    기존 호출부와 하위 호환된다.
    """
    if not session_id:
        session_id = str(uuid.uuid4())

    query_id = session_id[-8:]
    bind_query_context(query_id)

    if tracker is None:
        tracker = EvaluationTracker(run_id=session_id)

    # on_event 콜백을 트래커에 주입하여 노드 실행 시 자동 호출
    tracker.on_node_event = on_event  # type: ignore[attr-defined]

    logger.info(
        "파이프라인 실행 시작",
        user_input=user_input,
        session_id=session_id,
    )

    tracker.start_run(
        user_input=user_input,
        session_id=session_id,
    )

    manager = get_connector_manager()
    await manager.connect_all()

    app = create_app(tracker=tracker)

    cs = clarification_state or {}
    initial_state = PipelineState(
        user_input=user_input,
        session_id=session_id,
        conversation_history=conversation_history or [],
        awaiting_clarification=cs.get("awaiting", False),
        clarification_response=(
            user_input if cs.get("awaiting") else ""
        ),
        clarification_question=cs.get("question", ""),
        preprocessed_input=cs.get("preprocessed_input", ""),
        clarification_turns=cs.get("turns", 0),
    )

    result = await app.ainvoke(initial_state)

    response = result.get(
        "formatted_response",
        "응답을 생성할 수 없습니다.",
    )
    trace_log = result.get("trace_log", [])
    viz = result.get("visualization") or VisualizationData()
    sql_result = result.get("sql_result") or SQLResult()

    # 통찰 데이터 구성 (State 접근 가능 시점)
    insight = _build_safe_insight(result)

    # 트래커 종료 및 SQL 기록
    _record_sql_metrics(tracker, result)
    _record_run_end(tracker, result, response)
    tracker.save()

    logger.info("파이프라인 실행 완료", session_id=session_id)
    clear_query_context()

    return PipelineResult(
        response=response,
        trace_log=trace_log,
        visualization=viz,
        insight=insight,
        sql_result=sql_result,
        awaiting_clarification=result.get(
            "awaiting_clarification", False,
        ),
        clarification_question=result.get(
            "clarification_question", "",
        ),
        preprocessed_input=result.get(
            "preprocessed_input", "",
        ),
        clarification_turns=result.get(
            "clarification_turns", 0,
        ),
    )


def _build_safe_insight(
    result: dict[str, Any],
) -> dict[str, Any]:
    """State에서 통찰 데이터를 안전하게 구성한다."""
    try:
        return build_insight(result)
    except Exception:
        logger.debug("통찰 데이터 구성 실패")
        return {}


def _record_sql_metrics(
    tracker: EvaluationTracker,
    result: dict[str, Any],
) -> None:
    """SQL 생성·검증·실행 메트릭을 트래커에 기록한다."""
    reason = result.get("reason")
    sql_result = result.get("sql_result")
    tracker.track_sql(
        generated_sql=(
            reason.generated_sql or "" if reason else ""
        ),
        validated=bool(reason and reason.validated_sql),
        validation_errors=[],
        retry_count=(
            reason.loop_guard.generate_attempts
            if reason else 0
        ),
        validation_feedback="",
        execution_success=bool(
            sql_result and sql_result.row_count > 0
        ),
        row_count=(
            sql_result.row_count if sql_result else 0
        ),
        execution_time_ms=(
            sql_result.execution_time_ms
            if sql_result else 0
        ),
    )


def _record_run_end(
    tracker: EvaluationTracker,
    result: dict[str, Any],
    response: str,
) -> None:
    """파이프라인 실행 종료를 트래커에 기록한다."""
    final_status = result.get("status", "")
    intent = result.get("intent")
    tracker.end_run(
        final_intent=(
            intent.value
            if hasattr(intent, "value")
            else str(intent or "")
        ),
        final_status=(
            final_status.value
            if hasattr(final_status, "value")
            else str(final_status)
        ),
        final_response_summary=response[:500],
        error_message=result.get("error_message", ""),
    )


def main() -> None:
    """CLI 엔트리포인트."""
    setup_logging()
    setup_langsmith()

    if len(sys.argv) < 2:
        print("사용법: python -m src.agents.graph.runner '질의 내용'")
        sys.exit(1)

    user_input = " ".join(sys.argv[1:])
    print(f"\n📝 질의: {user_input}\n")
    print("처리 중...\n")

    pipeline_result = asyncio.run(run_pipeline(user_input))
    print(f"\n📊 결과:\n{pipeline_result.response}")

    if pipeline_result.trace_log:
        print("\n📋 추론 과정:")
        for i, entry in enumerate(pipeline_result.trace_log, 1):
            detail = f": {entry.detail}" if entry.detail else ""
            print(f"  {i}. [{entry.node}] {entry.action}{detail}")


if __name__ == "__main__":
    main()
