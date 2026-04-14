"""파이프라인 실행 엔트리포인트 — sanitize + interrupt 감지 + ainvoke.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

서버(main.py)와 LangGraph 파이프라인 사이의 실행 계층으로,
입력 전처리(sanitize), interrupt 감지/재개, 결과 조립을 담당한다.
main.py는 그래프 내부(interrupt, checkpointer)를 알 필요 없이
이 모듈의 run_pipeline()만 호출하면 된다.

실행 흐름:
    1. sanitize — 모든 입력(첫 턴 + 명확화 응답)에 1회 적용
    2. interrupt 대기 감지 — aget_state로 중단 상태 확인
    3a. 중단 상태 → Command(resume=sanitized_text)로 재개
    3b. 새 턴 → PipelineState 초기화 + ainvoke
    4. ainvoke 후 interrupt 발생 여부 재확인 → 명확화 대기 응답 반환
    5. 정상 완료 → PipelineResult 조립 (응답, 추적, 시각화, 인사이트)

계층 구조:
    main.py (서버) → run_pipeline() → graph (비즈니스 로직)

핵심 함수:
    - run_pipeline: 사용자 입력을 받아 파이프라인을 실행하고 PipelineResult를 반환
    - _build_result: 그래프 결과를 PipelineResult로 조립
    - main: CLI 엔트리포인트 (python -m src.agents.graph.runner '질의 내용')

사용법:
    python -m src.agents.graph.runner "이번 달 신규 고객 수 알려줘"
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from langgraph.types import Command

from src.agents.graph.pipeline import get_compiled_app
from src.agents.models.response import PipelineResult
from src.agents.state.state import PipelineState, QueryStatus
from src.connectors.manager import get_connector_manager
from src.models.result import SQLResult, VisualizationData
from src.services.input_sanitizer import sanitize
from src.services.insight_builder import build_insight
from src.utils.logger import (
    bind_query_context,
    clear_query_context,
    get_logger,
    setup_logging,
)
from src.utils.sql_formatter import format_sql_tabular
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
    client_ip: str | None = None,
    user_agent: str | None = None,
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
        client_ip: 클라이언트 IP 주소 (턴 저장 감사 추적용).
        user_agent: 클라이언트 User-Agent 문자열 (턴 저장 감사 추적용).
        on_event: WebSocket 등으로 실시간 이벤트를 전송하는
            async 콜백. None이면 이벤트를 전송하지 않는다.

    Returns:
        PipelineResult — formatted_response, trace_log, visualization 등.
    """
    if not session_id:
        session_id = str(uuid.uuid4())

    # ── 활성 파이프라인 등록 ──
    # 런마다 고유한 CAS 토큰을 생성해, 동시 재전송 시 A턴 finally 가
    # B턴 등록을 덮어쓰지 않도록 한다 (turn_id 가 확정되기 전 시점이므로
    # 별도 토큰 사용). 래퍼가 예외를 흡수하므로 Redis 장애가 파이프라인을
    # 막지 않는다. 유저 턴 저장보다 앞에 두어 저장 단계 크래시도 커버한다.
    from src.agents.graph.active_run import clear_active, mark_active
    _run_key = str(uuid.uuid4())
    try:
        await mark_active(session_id, _run_key)

        # ── 0. 입력 전처리 (sanitize + cancel 플래그) ──
        prepared = await _prepare_input(user_input, session_id)
        if prepared.early_result is not None:
            return prepared.early_result

        # ── 0-1. 유저 메시지 조기 저장 (서버 크래시 시 메시지 유실 방지) ──
        _user_message_saved_early = False
        try:
            _pool = get_connector_manager().checkpointer_pool
            if _pool is not None:
                from src.services.message_store import save_message
                await save_message(
                    _pool,
                    thread_id=session_id, role="user", content=user_input,
                    client_ip=client_ip, user_agent=user_agent,
                    message_type="normal", request_id=session_id,
                )
                _user_message_saved_early = True
        except Exception:
            logger.warning(
                "유저 메시지 조기 저장 실패 — 파이프라인 완료 후 재시도",
                exc_info=True,
            )

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

        run_config: dict[str, Any] = {
            "callbacks": [handler],
        }
        if session_id:
            run_config["configurable"] = {"thread_id": session_id}

        # ── 1. interrupt 대기 중 감지 ──
        is_interrupt_pending = await _check_interrupt(
            app, run_config, session_id, prepared.previous_cancel_turn_id,
        )

        # ── 2. 그래프 실행 ──
        return await _execute_and_finalize(
            app=app,
            run_config=run_config,
            handler=handler,
            user_input=user_input,
            sanitized_text=prepared.sanitized_text,
            session_id=session_id,
            conversation_history=conversation_history,
            is_interrupt_pending=is_interrupt_pending,
            client_ip=client_ip,
            user_agent=user_agent,
            user_message_saved_early=_user_message_saved_early,
        )
    finally:
        await clear_active(session_id, _run_key)


class _PreparedInput:
    """_prepare_input의 반환 구조체."""

    __slots__ = (
        "sanitized_text",
        "previous_cancel_turn_id",
        "early_result",
    )

    def __init__(
        self,
        sanitized_text: str,
        previous_cancel_turn_id: str | None,
        early_result: PipelineResult | None = None,
    ) -> None:
        self.sanitized_text = sanitized_text
        self.previous_cancel_turn_id = previous_cancel_turn_id
        self.early_result = early_result


async def _prepare_input(
    user_input: str,
    session_id: str,
) -> _PreparedInput:
    """입력 정제, cancel 플래그 확인, 세션 인덱스 갱신."""
    from src.agents.graph.cancel import pop_cancel

    previous_cancel_turn_id = await pop_cancel(session_id)

    sanitized = sanitize(user_input)
    if sanitized.is_error:
        return _PreparedInput(
            sanitized_text="",
            previous_cancel_turn_id=previous_cancel_turn_id,
            early_result=PipelineResult(
                response=sanitized.error_message,
            ),
        )

    query_id = session_id[-8:]
    bind_query_context(query_id)

    try:
        _pool = get_connector_manager().checkpointer_pool
        if _pool is not None:
            from src.services.message_store import upsert_session_index
            _title = user_input[:50] + ("..." if len(user_input) > 50 else "")
            await upsert_session_index(
                _pool,
                thread_id=session_id,
                user_id="anonymous",
                title=_title,
            )
    except Exception:
        logger.warning("세션 인덱스 등록 실패", exc_info=True)

    return _PreparedInput(
        sanitized_text=sanitized.text,
        previous_cancel_turn_id=previous_cancel_turn_id,
    )


async def _check_interrupt(
    app: Any,
    run_config: dict[str, Any],
    session_id: str,
    previous_cancel_turn_id: str | None,
) -> bool:
    """interrupt 대기 상태를 확인한다."""
    is_interrupt_pending = False
    try:
        state_snapshot = await app.aget_state(run_config)
        is_interrupt_pending = bool(
            state_snapshot is not None
            and state_snapshot.next
        )
    except Exception as e:
        logger.debug("aget_state 조회 실패 (새 세션)", error=str(e))

    if is_interrupt_pending and previous_cancel_turn_id is not None:
        logger.info(
            "이전 턴 cancel 감지 — interrupt 무시, 새 턴 시작",
            session_id=session_id,
            cancelled_turn_id=previous_cancel_turn_id,
        )
        is_interrupt_pending = False

    return is_interrupt_pending


async def _execute_and_finalize(
    *,
    app: Any,
    run_config: dict[str, Any],
    handler: DataCopilotCallbackHandler,
    user_input: str,
    sanitized_text: str,
    session_id: str,
    conversation_history: list[dict[str, str]] | None,
    is_interrupt_pending: bool,
    client_ip: str | None,
    user_agent: str | None,
    user_message_saved_early: bool = False,
) -> PipelineResult:
    """그래프 실행, interrupt/정상 분기, 메시지 저장, 에러 처리."""
    user_message_saved = user_message_saved_early

    try:
        if is_interrupt_pending:
            # 이전 트레이스 복원 (명확화 전후 사고 과정 연결)
            _load_previous_trace(handler, session_id)

            logger.info(
                "interrupt 재개",
                session_id=session_id,
            )
            raw_state = await app.ainvoke(
                Command(resume=sanitized_text),
                config=run_config,
            )
        else:
            initial_state = PipelineState(
                user_input=user_input,
                original_query=user_input,
                preprocessed_input=sanitized_text,
                session_id=session_id,
                conversation_history=conversation_history or [],
                turn_id=str(uuid.uuid4()),
            )

            raw_state = await app.ainvoke(
                initial_state,
                config=run_config,
            )

        # ── interrupt 발생 여부 확인 (ainvoke 후 상태 재조회) ──
        clarification_data = None
        try:
            after_state = await app.aget_state(run_config)
            if after_state and after_state.next:
                for task in after_state.tasks:
                    if hasattr(task, "interrupts") and task.interrupts:
                        for intr in task.interrupts:
                            clarification_data = intr.value
                            break
        except Exception as e:
            logger.debug("ainvoke 후 상태 조회 실패", error=str(e))

        if clarification_data is not None:
            _record_run_end_safe(handler, raw_state or {}, "")
            _turn_id = ""
            if isinstance(raw_state, dict):
                _turn_id = raw_state.get("turn_id", "")
            handler.save(turn_id=_turn_id)
            clear_query_context()

            question = (
                clarification_data.get("question", "")
                if isinstance(clarification_data, dict)
                else ""
            )
            try:
                from src.services.message_store import save_message
                _pool = get_connector_manager().checkpointer_pool
                if _pool is None:
                    raise RuntimeError("pool unavailable")
                _user_message_uuid = None
                if not user_message_saved:
                    _user_message_uuid = await save_message(
                        _pool,
                        thread_id=session_id, role="user", content=user_input,
                        client_ip=client_ip, user_agent=user_agent,
                        message_type="clarification", request_id=session_id,
                    )
                    user_message_saved = True
                _assistant_message_uuid = await save_message(
                    _pool,
                    thread_id=session_id, role="assistant", content=question,
                    client_ip=client_ip, user_agent=user_agent,
                    message_type="clarification", request_id=session_id,
                    status="success",
                )
            except Exception:
                logger.warning("명확화 메시지 저장 실패", exc_info=True)
                _user_message_uuid = None
                _assistant_message_uuid = None

            clarification_result = PipelineResult(
                response=question,
                awaiting_clarification=True,
                clarification_request=clarification_data,
            )
            clarification_result.user_message_uuid = _user_message_uuid
            clarification_result.message_uuid = _assistant_message_uuid
            return clarification_result

        # ── 정상 완료 결과 구성 ──
        pipeline_result = _build_result(handler, raw_state)

        # ── 메시지 저장 (실패해도 파이프라인 결과에 영향 없음) ──
        try:
            from src.config import settings
            from src.services.message_store import save_message
            _pool = get_connector_manager().checkpointer_pool
            if _pool is None:
                raise RuntimeError("pool unavailable")

            _user_message_uuid = None
            if not user_message_saved:
                _user_message_uuid = await save_message(
                    _pool,
                    thread_id=session_id, role="user", content=user_input,
                    client_ip=client_ip, user_agent=user_agent,
                    message_type="normal", request_id=session_id,
                )
                user_message_saved = True

            _reason = raw_state.get("reason")
            _assistant_message_uuid = await save_message(
                _pool,
                thread_id=session_id, role="assistant",
                content=pipeline_result.response,
                client_ip=client_ip, user_agent=user_agent,
                message_type="normal",
                intent=str(raw_state.get("intent", "")),
                latency_ms=(
                    int(handler.trace.total_duration_ms)
                    if handler.trace.total_duration_ms
                    else None
                ),
                request_id=session_id,
                status=(
                    "cancelled" if pipeline_result.cancelled
                    else "success"
                ),
                exit_node=(
                    handler.trace.node_path[-1]
                    if handler.trace.node_path
                    else None
                ),
                model_id=settings.llm_model,
                trace_id=handler.run_id,
                executed_sql=(
                    format_sql_tabular(_reason.validated_sql)
                    if _reason and hasattr(_reason, "validated_sql")
                    and _reason.validated_sql
                    else None
                ),
                sql_explanation=(
                    _reason.sql_explanation
                    if _reason and hasattr(_reason, "sql_explanation")
                    else None
                ),
                target_db=(
                    _reason.target_db
                    if _reason and hasattr(_reason, "target_db")
                    and _reason.target_db
                    else None
                ),
                metadata={
                    "trace_log": (
                        [e.model_dump() for e in pipeline_result.trace_log]
                        if pipeline_result.trace_log
                        else []
                    ),
                    "insight": pipeline_result.insight,
                    "visualization": (
                        pipeline_result.visualization.model_dump(mode="json")
                        if pipeline_result.visualization
                        else None
                    ),
                    "sql_result": {
                        "columns": (
                            pipeline_result.sql_result.columns
                            if pipeline_result.sql_result
                            else []
                        ),
                        "row_count": (
                            pipeline_result.sql_result.row_count
                            if pipeline_result.sql_result
                            else 0
                        ),
                    },
                    "trace_files": pipeline_result.trace_files,
                    "result_data": (
                        pipeline_result.result_data
                    ),
                    "process_summary": (
                        pipeline_result.process_summary
                    ),
                },
            )

            pipeline_result.message_uuid = _assistant_message_uuid
            pipeline_result.user_message_uuid = _user_message_uuid
        except Exception:
            logger.warning("메시지 저장 실패 — 파이프라인 결과는 정상 반환", exc_info=True)

        return pipeline_result

    except Exception as e:
        # ── 에러 트레이스 저장 ──
        try:
            handler.end_run(
                final_status="error",
                error_message=str(e),
            )
            handler.save()
        except Exception:
            logger.debug("에러 trace 저장 실패", exc_info=True)

        # ── 에러 메시지 기록 (실패해도 예외 전파) ──
        try:
            from src.services.message_store import save_message
            _pool = get_connector_manager().checkpointer_pool
            if _pool is None:
                raise  # noqa: PLE0704 — pool 없으면 메시지 저장 건너뛰기
            if not user_message_saved:
                await save_message(
                    _pool,
                    thread_id=session_id, role="user", content=user_input,
                    client_ip=client_ip, user_agent=user_agent,
                    message_type="error", request_id=session_id,
                )
            await save_message(
                _pool,
                thread_id=session_id, role="assistant",
                content="처리 중 오류가 발생했습니다.",
                client_ip=client_ip, user_agent=user_agent,
                message_type="error", status="failure",
                error_type=type(e).__name__,
                error_message=str(e),
            )
        except Exception:
            logger.warning("에러 메시지 저장 실패", exc_info=True)
        raise


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
    _turn_id = ""
    if isinstance(result, dict):
        _turn_id = result.get("turn_id", "")
    trace_files = handler.save(turn_id=_turn_id)

    logger.info("파이프라인 실행 완료")
    clear_query_context()

    _status = result.get("status")
    _cancelled = (
        _status == QueryStatus.CANCELLED
        or _status == QueryStatus.CANCELLED.value
    )

    return PipelineResult(
        response=response,
        trace_log=trace_log,
        visualization=viz,
        insight=insight,
        sql_result=sql_result,
        trace_files=trace_files,
        cancelled=_cancelled,
        preprocessed_input=result.get(
            "preprocessed_input", "",
        ),
        result_data=result.get("result_data"),
        process_summary=result.get("process_summary"),
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
    """SQL 생성·검증·실행 메트릭을 핸들러에 기록한다.

    reason과 sql_result가 None일 수 있으므로 방어적으로 접근한다.
    """
    reason = result.get("reason")
    sql_result = result.get("sql_result")
    handler.record_sql({
        "generated_sql": (
            format_sql_tabular(reason.generated_sql)
            if reason and reason.generated_sql else ""
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
            intent.value  # type: ignore[union-attr]
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


def _load_previous_trace(
    handler: DataCopilotCallbackHandler,
    session_id: str,
) -> None:
    """이전 턴의 트레이스를 로드하여 핸들러에 복원한다.

    명확화 인터럽트 후 재개 시, 이전 사고 과정을 이어받기 위해
    가장 최근 텔레메트리 JSON을 찾아 resume_from으로 복원한다.
    """
    import json
    from pathlib import Path

    from src.config import settings

    try:
        trace_dir = Path(settings.eval_tracker_output_dir)
        if not trace_dir.exists():
            return

        # session_id가 포함된 가장 최근 텔레메트리 파일
        candidates = sorted(
            trace_dir.glob(
                f"trace_telemetry_*_{session_id}_*.json",
            ),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            return

        data = json.loads(
            candidates[0].read_text(encoding="utf-8"),
        )
        handler.resume_from(data)
        logger.debug(
            "이전 트레이스 복원",
            path=str(candidates[0]),
        )
    except Exception:
        logger.debug(
            "이전 트레이스 로드 실패", exc_info=True,
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
