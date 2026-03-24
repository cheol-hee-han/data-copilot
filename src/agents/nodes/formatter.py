"""결과 포맷팅 노드 — 최종 응답을 사용자 친화적 보고서 형태로 정리.

SQL 실행 결과(sql_result)를 LLM 에 전달하여 IT 비전문 사용자가 이해할 수 있는
보고서 형태의 자연어 응답을 생성한다.
컨텍스트 소스 수집 실패가 있었다면 경고 문구를 응답 상단에 추가하고,
파이프라인 전체 추론 과정(trace_log)을 접기(details) 태그로 응답 하단에 첨부한다.

핵심 함수:
    - format_response_node: state.user_input, state.sql_result,
      state.context.failed_sources, state.trace_log 를 읽어 포맷팅하고
      state.formatted_response 에 기록

위임 구조:
    - 비즈니스 로직: services/response_formatter.py (format_response)
    - 프롬프트: nodes/prompts/system_prompts.py 에서 RESULT_FORMATTING,
      FORMATTING_USER 를 로드하여 서비스에 주입

폴백:
    - LLM 호출 실패 시 ERR_FORMATTING 에러 메시지를 formatted_response 에 기록하여
      사용자에게 오류 상황을 안내한다.
"""

from __future__ import annotations

from src.agents.models.user_messages import (
    ERR_FORMATTING,
    format_context_warning,
    format_error,
)
from src.agents.nodes.prompts.system_prompts import (
    FORMATTING_USER,
    RESULT_FORMATTING,
)
from src.agents.state.state import (
    PipelineState,
    QueryStatus,
    add_trace,
    format_trace_summary,
)
from src.services.response_formatter import format_response
from src.utils.logger import get_logger

logger = get_logger(__name__)


async def format_response_node(
    state: PipelineState,
) -> dict:
    """결과를 사용자 친화적 형태로 포맷팅한다."""
    logger.info("결과 포맷팅 시작")

    try:
        formatted = await format_response(
            user_input=state.user_input,
            sql_result=state.sql_result,
            system_prompt=RESULT_FORMATTING,
            user_template=FORMATTING_USER,
        )
    except Exception as e:
        logger.error(
            "결과 포맷팅 LLM 호출 오류", error=str(e),
        )
        return {
            "formatted_response": format_error(
                ERR_FORMATTING,
            ),
            "status": QueryStatus.ERROR,
            "error_message": ERR_FORMATTING,
        }

    # 컨텍스트 소스 실패 경고
    ctx_warning = format_context_warning(
        state.context.failed_sources,
    )
    if ctx_warning:
        formatted = f"{ctx_warning}\n\n{formatted}"

    # 조회 과정 요약
    trace_summary = format_trace_summary(state)
    if trace_summary:
        formatted += (
            "\n\n<details>\n"
            "<summary>조회 과정 요약</summary>\n\n"
            f"{trace_summary}\n"
            "</details>"
        )

    logger.info(
        "결과 포맷팅 완료",
        response_length=len(formatted),
    )

    return {
        "formatted_response": formatted,
        "status": QueryStatus.FORMATTED,
        "trace_log": add_trace(
            state, "포맷팅",
            "보고서 형태로 결과 정리 완료",
        ),
    }
