"""파이프라인 실행 엔트리포인트 — LangGraph 그래프의 최상위 호출 인터페이스.

API 서버(server.py)나 CLI 에서 호출되어 LangGraph 파이프라인을 초기화·실행하고
최종 PipelineResult 를 반환하는 진입점이다.
커넥터 매니저를 통해 데이터 소스를 연결하고, ``DataCopilotCallbackHandler`` 로
노드·LLM·의사결정을 자동 추적하며, WebSocket 진행률을 전파한다.

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
from typing import Any, Awaitable, Callable

from src.agents.graph.pipeline import get_compiled_app
from src.agents.models.response import PipelineResult
from src.agents.state.state import PipelineState
from src.connectors.manager import get_connector_manager
from src.models.result import SQLResult, VisualizationData
from src.services.insight_builder import build_insight
from src.tools.langsmith import setup_langsmith
from src.utils.logger import (
    bind_query_context,
    clear_query_context,
    get_logger,
    setup_logging,
)
from src.utils.tracker.callback_handler import (
    DataCopilotCallbackHandler,
)
from src.utils.truncate import truncate_trace

logger = get_logger(__name__)

# on_event 콜백 타입 (async 함수)
OnEventCallback = Callable[[dict[str, Any]], Awaitable[None]]


async def run_pipeline(
    user_input: str,
    session_id: str | None = None,
    conversation_history: list[dict[str, str]] | None = None,
    *,
    clarification_state: dict[str, Any] | None = None,
    on_event: OnEventCallback | None = None,
) -> PipelineResult:
    """파이프라인을 실행하고 최종 결과를 반환한다.

    Args:
        user_input: 사용자 자연어 입력.
        session_id: 세션 식별자. 미지정 시 자동 생성.
        conversation_history: 이전 대화 이력.
        clarification_state: 명확화 상태 (turns 등).
        on_event: WebSocket 등으로 실시간 이벤트를 전송하는
            async 콜백. None이면 이벤트를 전송하지 않는다.

    Returns:
        PipelineResult — formatted_response, trace_log, visualization 등.
    """
    if not session_id:
        session_id = str(uuid.uuid4())

    query_id = session_id[-8:]
    bind_query_context(query_id)

    # 콜백 핸들러 생성 (요청별 1개)
    handler = DataCopilotCallbackHandler(
        run_id=session_id,
        on_event=on_event,
    )

    logger.info(
        "파이프라인 실행 시작",
        user_input=user_input,
        session_id=session_id,
    )

    handler.start_run(
        user_input=user_input,
        session_id=session_id,
    )

    manager = get_connector_manager()
    await manager.connect_all()

    # 싱글턴 컴파일 앱 + 요청별 콜백 주입
    app = get_compiled_app()

    cs = clarification_state or {}
    initial_state = PipelineState(
        user_input=user_input,
        session_id=session_id,
        conversation_history=conversation_history or [],
        awaiting_clarification=False,
        clarification_response="",
        clarification_question="",
        preprocessed_input="",
        clarification_turns=cs.get("turns", 0),
    )

    result = await app.ainvoke(
        initial_state,
        config={"callbacks": [handler]},
    )

    response = result.get(
        "formatted_response",
        "응답을 생성할 수 없습니다.",
    )
    trace_log = result.get("trace_log", [])
    viz = result.get("visualization") or VisualizationData()
    sql_result = result.get("sql_result") or SQLResult()

    # 통찰 데이터 구성 (State 접근 가능 시점)
    insight = _build_safe_insight(result)

    # 핸들러 종료 및 SQL 기록 (그래프 외부이므로 직접 호출)
    _record_sql_metrics(handler, result)
    _record_run_end(handler, result, response)
    handler.save()

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
    handler: DataCopilotCallbackHandler,
    result: dict[str, Any],
) -> None:
    """SQL 생성·검증·실행 메트릭을 핸들러에 기록한다."""
    reason = result.get("reason")
    sql_result = result.get("sql_result")
    handler.record_sql({
        "generated_sql": (
            reason.generated_sql or "" if reason else ""
        ),
        "validated": bool(reason and reason.validated_sql),
        "validation_errors": [],
        "retry_count": (
            reason.loop_guard.generate_attempts
            if reason else 0
        ),
        "validation_feedback": "",
        "execution_success": bool(
            sql_result and sql_result.row_count > 0
        ),
        "row_count": (
            sql_result.row_count if sql_result else 0
        ),
        "execution_time_ms": (
            sql_result.execution_time_ms
            if sql_result else 0
        ),
    })


def _record_run_end(
    handler: DataCopilotCallbackHandler,
    result: dict[str, Any],
    response: str,
) -> None:
    """파이프라인 실행 종료를 핸들러에 기록한다."""
    final_status = result.get("status", "")
    intent = result.get("intent")
    handler.end_run(
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
        final_response_summary=truncate_trace(response),
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
