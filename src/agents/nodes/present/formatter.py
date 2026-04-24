"""결과 포맷팅 노드 — 최종 응답을 사용자 친화적 보고서 형태로 정리.

작성자: 한철희 / 최종수정: 2026-04-07

SQL 실행 결과(sql_result)를 rule-based 로직으로 IT 비전문 사용자가 이해할 수 있는
핵심 수치 요약 텍스트를 생성한다. LLM 호출 없이 결정론적 포맷팅을 수행한다.

테이블 원본 데이터(result_data)와 조회 과정 요약(process_summary)은
구조화 dict로 State에 저장하여 stream.end JSON으로 프론트엔드에 전송한다.

핵심 함수:
    - formatter_node: state.sql_result, state.reason 등을 읽어
      rule-based 포맷팅을 수행하고 state에 기록

위임 구조:
    - 포맷팅 로직: services/response_formatter.py (rule-based 함수들)
    - 조회 과정 요약: services/process_summary_builder.py
"""

from __future__ import annotations

from typing import Any

from src.agents.models.user_messages import (
    ERR_FORMATTING,
    format_error,
)
from src.agents.state.state import (
    PipelineState,
    QueryStatus,
    add_trace,
)
from src.config import settings
from src.services.process_summary_builder import (
    build_process_summary,
)
from src.services.response_formatter import (
    apply_code_mappings,
    build_analysis_report,
    build_summary_line,
    detect_column_formats,
)
from src.utils.logger import get_logger
from src.utils.tracker.dispatch import (
    dispatch_tracking_event,
    REASONING_STEP,
)

logger = get_logger(__name__)


def _build_result_data(
    columns: list[str],
    rows: list[dict[str, Any]],
    column_formats: dict[str, str],
) -> dict[str, Any] | None:
    """UI 전송용 구조화 테이블 데이터를 조립한다.

    코드값 변환이 적용된 rows를 받아 행 제한 후 dict로 반환한다.
    """
    if not columns or not rows:
        return None

    max_rows = settings.ui_result_max_rows
    truncated = rows[:max_rows]

    return {
        "columns": columns,
        "rows": truncated,
        "column_formats": column_formats,
        "total_count": len(rows),
        "displayed_count": len(truncated),
    }


def _render_formatted_text(
    state: PipelineState,
    rows: list[dict[str, Any]],
    column_formats: dict[str, str],
) -> str:
    """analysis_result 가 있으면 마크다운 보고서를, 없으면 요약 1줄을 반환."""
    analysis = state.analysis_result
    has_analysis = bool(
        analysis and (
            analysis.initial_reading
            or analysis.insights
            or analysis.action_items
        )
    )
    if has_analysis:
        text = build_analysis_report(analysis)
    elif analysis and analysis.summary:
        text = analysis.summary
    else:
        text = build_summary_line(
            state.sql_result.columns, rows, column_formats,
        )
    return text or (
        "SQL 작성을 완료하였으나, 실제 조회 시 결과가 0건입니다.\n"
        "실행된 SQL의 필터 조건 또는 사용 테이블의 데이터 존재여부 확인이 필요합니다."
    )


async def formatter_node(
    state: PipelineState,
) -> dict:
    """결과를 사용자 친화적 형태로 포맷팅한다."""
    logger.info("결과 포맷팅 시작")

    # ── 가드: simple_responder가 이미 응답 완성한 경우 ──
    if state.formatted_response and not (
        state.sql_result and state.sql_result.rows
    ):
        logger.info("경량 응답 통과 — 포맷팅 스킵")
        return {
            "trace_log": add_trace(state, "포맷팅", "경량 응답 통과"),
        }

    try:
        column_formats = detect_column_formats(
            state.reason.validated_sql or "",
        )
        rows = apply_code_mappings(
            state.sql_result.rows,
            state.reason.explored_codes,
            state.reason.validated_sql or "",
        )
        formatted = _render_formatted_text(state, rows, column_formats)
        result_data = _build_result_data(
            state.sql_result.columns, rows, column_formats,
        )

    except Exception as e:
        logger.error("결과 포맷팅 오류", error=str(e))
        return {
            "formatted_response": format_error(ERR_FORMATTING),
            "status": QueryStatus.ERROR,
            "error_message": ERR_FORMATTING,
            "trace_log": add_trace(state, "포맷팅", f"오류: {ERR_FORMATTING}"),
        }

    # ── 5. force-generate 경고 (기존 유지) ──
    if state.reason.is_force_generated:
        formatted = (
            "**참고**: 확인된 정보가 충분하지 않아 "
            "일부 추론을 포함하여 조회하였습니다. "
            "결과가 예상과 다를 경우 구체적으로 요청해 주세요."
            f"\n\n{formatted}"
        )

    # ── 6. 조회 과정 요약 (구조화 dict) ──
    process_summary = build_process_summary(state)

    logger.info(
        "결과 포맷팅 완료",
        response_length=len(formatted),
        streaming_delivered=state.streaming_delivered,
    )

    # ── 트래킹 ──
    try:
        await dispatch_tracking_event(REASONING_STEP, {
            "node": "formatter",
            "phase": "present",
            "step_type": "rule_based",
            "round": 0,
            "hypothesis_id": "",
            "inputs": {
                "user_input": state.preprocessed_input,
                "sql_result": (
                    f"{state.sql_result.row_count if state.sql_result else 0}건"
                ),
            },
            "output": {
                "format": "rule-based 구조화 응답",
                "is_force_generated": state.reason.is_force_generated,
                "process_summary_included": bool(
                    process_summary,
                ),
                "result_data_included": bool(result_data),
            },
            "routing": {
                "next_node": "(완료)",
                "reason": "최종 응답 생성 완료",
            },
        })
    except Exception as e:
        logger.warning("포맷팅 트래킹 이벤트 전송 실패", error=str(e))

    trace_note = (
        "보고서 형태로 결과 정리 완료"
        + (" (스트리밍 완료 후 최종본 합성)" if state.streaming_delivered else "")
    )
    return {
        "formatted_response": formatted,
        "result_data": result_data,
        "process_summary": process_summary,
        "status": QueryStatus.FORMATTED,
        "trace_log": add_trace(state, "포맷팅", trace_note),
    }
