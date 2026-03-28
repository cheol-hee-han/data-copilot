"""데이터 분석 노드 — SQL 실행 결과에 대한 인사이트 도출 및 시각화 생성.

SQL 실행으로 추출된 데이터(sql_result)를 LLM 에 전달하여 비즈니스 인사이트를 도출하고,
데이터 특성에 따라 시각화(SVG 차트) 생성 여부를 판단·실행한다.
시각화 판단과 SVG 생성은 별도의 프롬프트로 순차 호출되며,
최소 행 수(min_rows_for_visualization) 미만이면 시각화를 건너뛴다.
분석 결과는 AnalysisResult 모델로 state.analysis_result 에 기록된다.

핵심 함수:
    - analyze_data_node: state.preprocessed_input, state.sql_result 을 읽어
      분석·시각화를 수행하고 state.analysis_result 에 기록

위임 구조:
    - 비즈니스 로직: services/data_analyzer.py (analyze_data)
    - 프롬프트: nodes/prompts/system_prompts.py 에서 DATA_ANALYSIS, ANALYSIS_USER,
      VIZ_JUDGMENT_SYSTEM, VIZ_SVG_SYSTEM 등을 로드하여 서비스에 주입

폴백:
    - 분석 LLM 호출 실패 시 에러 메시지를 담은 기본 AnalysisResult 를 반환하여
      파이프라인이 ERROR 상태로 종료되도록 한다.
"""

from __future__ import annotations

from src.agents.models.user_messages import (
    ERR_DATA_ANALYSIS,
    format_error,
)
from src.agents.nodes.system_prompts import (
    ANALYSIS_USER,
    DATA_ANALYSIS,
    VIZ_JUDGMENT_SYSTEM,
    VIZ_JUDGMENT_USER,
    VIZ_SVG_SYSTEM,
    VIZ_SVG_USER,
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

logger = get_logger(__name__)


async def analyze_data_node(
    state: PipelineState,
) -> dict:
    """추출된 데이터를 분석하고 시각화를 생성한다."""
    logger.info("데이터 분석 시작")

    try:
        analysis, viz = await analyze_data(
            user_input=state.preprocessed_input,
            sql_result=state.sql_result,
            system_prompt=DATA_ANALYSIS,
            user_template=ANALYSIS_USER,
            viz_judgment_prompt=VIZ_JUDGMENT_SYSTEM,
            viz_judgment_user=VIZ_JUDGMENT_USER,
            viz_svg_system=VIZ_SVG_SYSTEM,
            viz_svg_user=VIZ_SVG_USER,
            min_rows_for_viz=(
                settings.min_rows_for_visualization
            ),
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

    logger.info(
        "데이터 분석 완료",
        insights_count=len(analysis.insights),
    )

    viz_detail = ""
    if viz.has_visualization:
        viz_detail = (
            f", 시각화: {viz.chart_type.value}"
        )

    return {
        "analysis_result": analysis,
        "visualization": viz,
        "status": QueryStatus.ANALYZED,
        "trace_log": add_trace(
            state, "분석",
            f"데이터 분석 완료 "
            f"(인사이트 {len(analysis.insights)}건"
            f"{viz_detail})",
        ),
    }
