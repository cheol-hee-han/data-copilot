"""시각화 노드 — SQL 실행 결과에 대한 시각화 판단 및 SVG 생성.

작성자: 한철희 / 최종수정: 2026-04-16

SQL 실행 결과의 **데이터 특성**(행 수, 컬럼 구조, 시계열 여부)을 기반으로
시각화 여부를 판단하고 SVG 차트를 생성한다.
intent(DATA_ANALYSIS / DATA_EXTRACTION)에 무관하게 항상 실행되며,
시각화 결과는 state.visualization에 기록된다.

핵심 함수:
    - visualizer_node: state.sql_result을 읽어 시각화를 수행하고
      state.visualization에 기록

위임 구조:
    - 비즈니스 로직: services/data_analyzer.py (build_visualization)
    - 프롬프트: nodes/prompts/system_prompts.py에서 VISUALIZER_* 를
      로드하여 서비스에 주입
"""

from __future__ import annotations

from src.agents.nodes.system_prompts import (
    VISUALIZER_JUDGMENT_SYSTEM,
    VISUALIZER_JUDGMENT_USER,
    VISUALIZER_SVG_EXAMPLES,
    VISUALIZER_SVG_SYSTEM_BASE,
    VISUALIZER_SVG_USER,
)
from src.agents.state.state import (
    PipelineState,
    VisualizationData,
    add_trace,
)
from src.config import settings
from src.services.data_analyzer import build_visualization
from src.utils.logger import get_logger
from src.utils.tracker import (
    LLMInteraction,
    build_llm_reasoning_payload,
    llm_skip_sentinel,
)
from src.utils.tracker.dispatch import (
    dispatch_tracking_event,
    REASONING_STEP,
)

logger = get_logger(__name__)


async def visualizer_node(
    state: PipelineState,
) -> dict:
    """시각화 판단 + SVG 생성. intent 무관 항상 실행."""
    from src.agents.graph.cancel import check_cancel

    sql_result = state.sql_result
    row_count = sql_result.row_count if sql_result else 0

    if row_count < settings.min_rows_for_visualization:
        logger.info(
            "시각화 스킵 — 최소 행 수 미달",
            row_count=row_count,
            min_rows=settings.min_rows_for_visualization,
        )
        viz = VisualizationData()
        skip_interaction = LLMInteraction(
            prompt_variables={},
            raw_response=llm_skip_sentinel(
                f"최소 행 수 미달: "
                f"{row_count} < {settings.min_rows_for_visualization}",
            ),
        )
        await _dispatch_viz_event(state, viz, skip_interaction)
        return {"visualization": viz}

    async def _is_cancelled() -> bool:
        return await check_cancel(
            state.session_id, state.turn_id,
        )

    logger.info("시각화 판단 시작", row_count=row_count)

    user_input = state.analysis_query or state.preprocessed_input or ""

    viz, interaction = await build_visualization(
        sql_result,
        viz_judgment_prompt=VISUALIZER_JUDGMENT_SYSTEM,
        viz_judgment_user=VISUALIZER_JUDGMENT_USER,
        viz_svg_base=VISUALIZER_SVG_SYSTEM_BASE,
        viz_svg_examples=VISUALIZER_SVG_EXAMPLES,
        viz_svg_user=VISUALIZER_SVG_USER,
        is_cancelled=_is_cancelled,
        streaming_enabled=state.streaming_enabled,
        turn_id=state.turn_id,
        handoff_note=state.handoff_note,
        user_input=user_input,
    )

    viz_detail = ""
    if viz.has_visualization:
        viz_detail = f", 시각화: {viz.chart_type.value}"
        logger.info(
            "시각화 생성 완료",
            chart_type=viz.chart_type.value,
        )
    else:
        logger.info("시각화 불필요로 판단됨")

    await _dispatch_viz_event(state, viz, interaction)

    return {
        "visualization": viz,
        "trace_log": add_trace(
            state, "시각화",
            f"시각화 판단 완료{viz_detail}",
        ),
    }


async def _dispatch_viz_event(
    state: PipelineState,
    viz: VisualizationData,
    interaction: LLMInteraction,
) -> None:
    """시각화 추적 이벤트를 발행한다 (Option B).

    judge 단계의 prompt_variables / raw_response 를 trace 에 보존하고,
    판단 결과(chart_type, judgment_reason)는 parsed_summary 로 요약한다.
    """
    row_count = (
        state.sql_result.row_count if state.sql_result else 0
    )
    columns = (
        state.sql_result.columns if state.sql_result else []
    )
    viz_judgment = "SKIPPED"
    if viz.has_visualization:
        viz_judgment = f"APPROVED — {viz.chart_type.value}"

    parsed_summary: dict[str, str | bool] = {
        "viz_judgment": viz_judgment,
    }
    if viz.judgment_reason:
        parsed_summary["judgment_reason"] = viz.judgment_reason

    await dispatch_tracking_event(
        REASONING_STEP,
        build_llm_reasoning_payload(
            node="visualizer",
            phase="present",
            round=0,
            hypothesis_id="",
            interaction=interaction,
            routing={
                "next_node": "formatter",
                "reason": (
                    f"시각화 완료 — {viz.chart_type.value}"
                    if viz.has_visualization
                    else "시각화 불필요"
                ),
            },
            parsed_summary=parsed_summary,
            extra_inputs={
                "sql_result_row_count": row_count,
                "sql_result_columns": columns[:10],
                "viz_eligible": (
                    row_count >= settings.min_rows_for_visualization
                ),
            },
            step_type="visualization",
        ),
    )
