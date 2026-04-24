"""데이터 분석 노드 — SQL 실행 결과에 대한 인사이트 도출.

작성자: 한철희 / 최종수정: 2026-04-16

SQL 실행으로 추출된 데이터(sql_result)를 LLM에 전달하여 비즈니스 인사이트를 도출한다.
시각화는 별도의 visualizer 노드에서 독립적으로 수행된다.
분석 결과는 AnalysisResult 모델로 state.analysis_result에 기록된다.

핵심 함수:
    - analyzer_node: state.preprocessed_input, state.sql_result을 읽어
      분석을 수행하고 state.analysis_result에 기록

위임 구조:
    - 비즈니스 로직: services/data_analyzer.py (analyze_data)
    - 프롬프트: nodes/prompts/system_prompts.py에서 ANALYZER_SYSTEM, ANALYZER_USER를
      로드하여 서비스에 주입

폴백:
    - 분석 LLM 호출 실패 시 에러 메시지를 담은 기본 AnalysisResult를 반환하여
      파이프라인이 ERROR 상태로 종료되도록 한다.
"""

from __future__ import annotations

from src.agents.models.user_messages import (
    ERR_DATA_ANALYSIS,
    format_error,
)
from src.agents.nodes.system_prompts import (
    ANALYZER_SYSTEM,
    ANALYZER_USER,
)
from src.agents.state.state import (
    AnalysisResult,
    PipelineState,
    QueryStatus,
    add_trace,
)
from src.config import settings
from src.services.data_analyzer import analyze_data
from src.utils.logger import get_logger
from src.utils.tracker import build_llm_reasoning_payload
from src.utils.tracker.dispatch import (
    dispatch_tracking_event,
    REASONING_STEP,
)

logger = get_logger(__name__)


async def analyzer_node(
    state: PipelineState,
) -> dict:
    """추출된 데이터를 분석한다. DATA_ANALYSIS일 때만 진입."""
    from src.agents.graph.cancel import check_cancel

    logger.info("데이터 분석 시작")

    user_input = state.analysis_query or state.preprocessed_input

    async def _is_cancelled() -> bool:
        return await check_cancel(state.session_id, state.turn_id)

    try:
        analysis, delivered, interaction = await analyze_data(
            user_input=user_input,
            sql_result=state.sql_result,
            system_prompt=ANALYZER_SYSTEM,
            user_template=ANALYZER_USER,
            is_cancelled=_is_cancelled,
            streaming_enabled=state.streaming_enabled,
            turn_id=state.turn_id,
            handoff_note=state.handoff_note,
        )
    except Exception as e:
        logger.error(
            "데이터 분석 오류", error=str(e),
        )
        return {
            "analysis_result": AnalysisResult(
                summary=format_error(ERR_DATA_ANALYSIS),
            ),
            "status": QueryStatus.ERROR,
            "error_message": format_error(
                ERR_DATA_ANALYSIS,
            ),
        }

    if not analysis.reasoning_summary:
        logger.warning(
            "analyzer reasoning_summary 누락 — 깊이 검증 불가",
        )

    logger.info(
        "데이터 분석 완료",
        initial_reading_count=len(analysis.initial_reading),
        insights_count=len(analysis.insights),
        action_items_count=len(analysis.action_items),
        has_reasoning_summary=bool(analysis.reasoning_summary),
    )

    _row_count = (
        state.sql_result.row_count if state.sql_result else 0
    )
    _columns = (
        state.sql_result.columns if state.sql_result else []
    )

    parsed_summary = {
        "summary": (
            analysis.summary[:200]
            if analysis.summary else ""
        ),
        "initial_reading": [
            i[:100]
            for i in (analysis.initial_reading or [])[:5]
        ],
        "insights": [
            i[:100]
            for i in (analysis.insights or [])[:5]
        ],
        "action_items": [
            a[:100]
            for a in (analysis.action_items or [])[:3]
        ],
        "reasoning_summary": (
            analysis.reasoning_summary[:200]
            if analysis.reasoning_summary else ""
        ),
    }
    await dispatch_tracking_event(
        REASONING_STEP,
        build_llm_reasoning_payload(
            node="analyzer",
            phase="present",
            round=0,
            hypothesis_id="",
            interaction=interaction,
            routing={
                "next_node": "visualizer",
                "reason": "분석 완료",
            },
            parsed_summary=parsed_summary,
            extra_inputs={
                "sql_result_row_count": _row_count,
                "sql_result_columns": _columns[:10],
            },
            step_type="analysis",
        ),
    )

    return {
        "analysis_result": analysis,
        "streaming_delivered": delivered,
        "status": QueryStatus.ANALYZED,
        "trace_log": add_trace(
            state, "분석",
            f"데이터 분석 완료 "
            f"(인사이트 {len(analysis.insights)}건"
            f"{', 스트리밍' if delivered else ''})",
        ),
    }
