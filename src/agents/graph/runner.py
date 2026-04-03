"""파이프라인 실행 엔트리포인트 — sanitize + interrupt 감지 + ainvoke.

계층 구조:
    main.py (서버) → run_pipeline() → graph (비즈니스 로직)

main.py는 그래프 내부(interrupt, checkpointer)를 모른다.
interrupt 감지와 Command(resume=) 분기는 이 모듈에서 처리한다.

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

from langgraph.types import Command

from src.agents.graph.pipeline import get_compiled_app
from src.agents.models.response import PipelineResult
from src.agents.state.state import PipelineState
from src.connectors.manager import get_connector_manager
from src.models.result import SQLResult, VisualizationData
from src.services.input_sanitizer import sanitize
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
    on_event: OnEventCallback | None = None,
) -> PipelineResult:
    """파이프라인을 실행하고 최종 결과를 반환한다.

    모든 입력(첫 턴 + 명확화 응답)에 대해 sanitize를 1회 실행한다.
    interrupt 대기 중이면 Command(resume=)로 재개하고,
    아니면 새 PipelineState로 ainvoke한다.

    Args:
        user_input: 사용자 자연어 입력 (첫 턴 또는 명확화 응답).
        session_id: 세션 식별자. 미지정 시 자동 생성.
        conversation_history: 이전 대화 이력.
        on_event: WebSocket 등으로 실시간 이벤트를 전송하는
            async 콜백. None이면 이벤트를 전송하지 않는다.

    Returns:
        PipelineResult — formatted_response, trace_log, visualization 등.
    """
    if not session_id:
        session_id = str(uuid.uuid4())

    # ── 1. sanitize: 모든 입력에 1회 적용 ──
    sanitized = sanitize(user_input)
    if sanitized.is_error:
        return PipelineResult(
            response=sanitized.error_message,
        )

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

    app = get_compiled_app()

    # 체크포인터 config 구성 — thread_id = session_id
    run_config: dict[str, Any] = {
        "callbacks": [handler],
    }
    if session_id:
        run_config["configurable"] = {"thread_id": session_id}

    # ── 2. interrupt 대기 중 감지 ──
    is_interrupt_pending = False
    try:
        state_snapshot = await app.aget_state(run_config)
        is_interrupt_pending = bool(
            state_snapshot is not None
            and state_snapshot.next
        )
    except Exception as e:
        # 체크포인터 미사용 또는 새 세션 → 새 턴으로 진행
        logger.debug("aget_state 조회 실패 (새 세션)", error=str(e))

    if is_interrupt_pending:
        # ── 3a. interrupt 재개: Command(resume=sanitized_text) ──
        logger.info(
            "interrupt 재개",
            session_id=session_id,
        )
        result = await app.ainvoke(
            Command(resume=sanitized.text),
            config=run_config,
        )
    else:
        # ── 3b. 새 턴: 초기 state 생성 + ainvoke ──
        initial_state = PipelineState(
            user_input=user_input,
            original_query=user_input,
            preprocessed_input=sanitized.text,
            session_id=session_id,
            conversation_history=conversation_history or [],
            turn_id=str(uuid.uuid4()),
        )

        result = await app.ainvoke(
            initial_state,
            config=run_config,
        )

    # ── 4. interrupt 발생 여부 확인 (ainvoke 후 상태 재조회) ──
    clarification_data = None
    try:
        after_state = await app.aget_state(run_config)
        if after_state and after_state.next:
            # interrupt 발생 → 명확화 대기 중
            for task in after_state.tasks:
                if hasattr(task, "interrupts") and task.interrupts:
                    for intr in task.interrupts:
                        clarification_data = intr.value
                        break
    except Exception as e:
        logger.debug("ainvoke 후 상태 조회 실패", error=str(e))

    if clarification_data is not None:
        # interrupt 페이로드에서 AmbiguitySignal 데이터 추출
        _record_run_end_safe(handler, result or {}, "")
        handler.save()
        clear_query_context()

        question = (
            clarification_data.get("question", "")
            if isinstance(clarification_data, dict)
            else ""
        )
        return PipelineResult(
            response=question,
            awaiting_clarification=True,
            clarification_request=clarification_data,
        )

    # ── 5. 정상 완료 결과 구성 ──
    return _build_result(handler, result)


def _build_result(
    handler: DataCopilotCallbackHandler,
    result: dict[str, Any],
) -> PipelineResult:
    """그래프 실행 결과에서 PipelineResult를 구성한다."""
    response = result.get(
        "formatted_response",
        "응답을 생성할 수 없습니다.",
    )
    trace_log = result.get("trace_log", [])
    viz = result.get("visualization") or VisualizationData()
    sql_result = result.get("sql_result") or SQLResult()

    insight = _build_safe_insight(result)

    _record_sql_metrics(handler, result)
    _record_run_end(handler, result, response)
    handler.save()

    logger.info("파이프라인 실행 완료")
    clear_query_context()

    return PipelineResult(
        response=response,
        trace_log=trace_log,
        visualization=viz,
        insight=insight,
        sql_result=sql_result,
        preprocessed_input=result.get(
            "preprocessed_input", "",
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


def _record_run_end_safe(
    handler: DataCopilotCallbackHandler,
    result: dict[str, Any],
    response: str,
) -> None:
    """핸들러 종료를 안전하게 기록한다."""
    try:
        _record_run_end(handler, result, response)
    except Exception:
        logger.debug("핸들러 종료 기록 실패")


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
