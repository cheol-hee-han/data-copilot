"""visualizer_node 단위 테스트.

테스트 대상:
    [src/agents/nodes/present/visualizer.py :: visualizer_node]
    - 최소 행 수 미달 시 시각화 스킵 (빈 VisualizationData 반환)
    - sql_result 없는 경우 스킵
    - 정상 시각화 판단/생성 흐름 (build_visualization mock)
    - 시각화 결과가 state.visualization에 기록됨
    - analyzer_node 반환값에는 visualization 미포함

    [src/services/data_analyzer.py :: parse_viz_judgment / build_visualization]
    - info_card 타입 파싱
    - info_card 템플릿 폴백 SVG 생성

    [src/services/visualization/chart_generator.py :: _generate_info_card]
    - 단일행 info_card SVG 생성
    - 2행 info_card SVG 생성
    - 빈 행 info_card → 빈 문자열
    - 숫자 포맷팅 (int, float)

실행:
    pytest tests/auto/unit/test_visualizer_node.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tests.conftest import get_test_logger, log_test_case

logger = get_test_logger("test_visualizer_node")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 헬퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _make_state(
    rows: list[dict] | None = None,
    user_input: str = "데이터 조회",
    min_rows: int = 1,
):
    """테스트용 PipelineState를 생성한다."""
    from src.agents.state.state import PipelineState, SQLResult

    if rows is None:
        # sql_result가 기본 빈 상태 (row_count=0)
        sql_result = SQLResult()
    else:
        columns = list(rows[0].keys()) if rows else []
        sql_result = SQLResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            execution_time_ms=5.0,
        )

    return PipelineState(
        user_input=user_input,
        preprocessed_input=user_input,
        sql_result=sql_result,
    )


def _build_viz_return(viz):
    """build_visualization mock 반환값 — (VisualizationData, LLMInteraction)."""
    from src.utils.tracker import LLMInteraction
    return viz, LLMInteraction(
        prompt_variables={},
        raw_response="(mock judge)",
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. visualizer_node — 스킵 조건
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestVisualizerNodeSkip:
    """visualizer_node가 시각화를 건너뛰는 조건 검증."""

    @pytest.mark.asyncio
    async def test_skip_when_no_sql_result(self):
        """sql_result가 None이면 시각화를 건너뛴다."""
        from src.agents.nodes.present.visualizer import visualizer_node
        from src.agents.state.state import VisualizationData

        state = _make_state(rows=None)
        result = await visualizer_node(state)

        viz = result.get("visualization")
        passed = isinstance(viz, VisualizationData) and not viz.has_visualization
        log_test_case(
            logger, "test_skip_no_sql_result",
            "sql_result=None", "빈 VisualizationData",
            f"has_viz={viz.has_visualization if viz else None}", passed,
        )
        assert passed

    @pytest.mark.asyncio
    async def test_skip_when_empty_rows(self):
        """행이 0건이면 시각화를 건너뛴다."""
        from src.agents.nodes.present.visualizer import visualizer_node
        from src.agents.state.state import VisualizationData

        state = _make_state(rows=[])
        result = await visualizer_node(state)

        viz = result.get("visualization")
        passed = isinstance(viz, VisualizationData) and not viz.has_visualization
        log_test_case(
            logger, "test_skip_empty_rows",
            "rows=[]", "빈 VisualizationData",
            f"has_viz={viz.has_visualization if viz else None}", passed,
        )
        assert passed

    @pytest.mark.asyncio
    async def test_skip_when_below_min_rows(self):
        """행 수가 min_rows_for_visualization 미만이면 건너뛴다."""
        from src.agents.nodes.present.visualizer import visualizer_node
        from src.agents.state.state import VisualizationData

        # min_rows_for_visualization=2로 설정된 환경에서 1행인 경우
        with patch("src.agents.nodes.present.visualizer.settings") as mock_settings:
            mock_settings.min_rows_for_visualization = 3
            state = _make_state(rows=[{"항목": "A", "값": 100}, {"항목": "B", "값": 200}])
            result = await visualizer_node(state)

        viz = result.get("visualization")
        passed = isinstance(viz, VisualizationData) and not viz.has_visualization
        log_test_case(
            logger, "test_skip_below_min_rows",
            "rows=2, min_rows=3", "빈 VisualizationData",
            f"has_viz={viz.has_visualization if viz else None}", passed,
        )
        assert passed


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. visualizer_node — 정상 실행 (mock)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestVisualizerNodeExecution:
    """visualizer_node 정상 실행 흐름 검증 (build_visualization mock)."""

    @pytest.mark.asyncio
    async def test_returns_visualization_on_success(self):
        """build_visualization이 정상 결과를 반환하면 visualization이 설정된다."""
        from src.agents.nodes.present.visualizer import visualizer_node
        from src.models.enums import VisualizationType
        from src.models.result import VisualizationData

        mock_viz = VisualizationData(
            svg_code="<svg>test</svg>",
            chart_type=VisualizationType.BAR_CHART,
            title="테스트 차트",
        )

        rows = [
            {"월": "2024-01", "건수": 10},
            {"월": "2024-02", "건수": 20},
            {"월": "2024-03", "건수": 30},
        ]
        state = _make_state(rows=rows)

        with patch(
            "src.agents.nodes.present.visualizer.build_visualization",
            new_callable=AsyncMock,
            return_value=_build_viz_return(mock_viz),
        ), patch(
            "src.agents.nodes.present.visualizer.settings",
        ) as mock_settings:
            mock_settings.min_rows_for_visualization = 1
            result = await visualizer_node(state)

        viz = result.get("visualization")
        passed = (
            viz is not None
            and viz.has_visualization
            and viz.chart_type == VisualizationType.BAR_CHART
            and viz.svg_code == "<svg>test</svg>"
        )
        log_test_case(
            logger, "test_returns_visualization",
            "rows=3, mock BAR_CHART", "has_visualization=True",
            f"chart_type={viz.chart_type if viz else None}", passed,
        )
        assert passed

    @pytest.mark.asyncio
    async def test_includes_trace_log(self):
        """정상 실행 시 trace_log가 반환값에 포함된다."""
        from src.agents.nodes.present.visualizer import visualizer_node
        from src.models.enums import VisualizationType
        from src.models.result import VisualizationData

        mock_viz = VisualizationData(
            svg_code="<svg></svg>",
            chart_type=VisualizationType.LINE_CHART,
            title="트레이스 테스트",
        )

        rows = [{"x": 1}, {"x": 2}]
        state = _make_state(rows=rows)

        with patch(
            "src.agents.nodes.present.visualizer.build_visualization",
            new_callable=AsyncMock,
            return_value=_build_viz_return(mock_viz),
        ), patch(
            "src.agents.nodes.present.visualizer.settings",
        ) as mock_settings:
            mock_settings.min_rows_for_visualization = 1
            result = await visualizer_node(state)

        passed = "trace_log" in result
        log_test_case(
            logger, "test_includes_trace_log",
            "정상 실행", "trace_log 포함",
            f"keys={list(result.keys())}", passed,
        )
        assert passed

    @pytest.mark.asyncio
    async def test_viz_none_result(self):
        """build_visualization이 빈 VisualizationData를 반환하면 has_visualization=False."""
        from src.agents.nodes.present.visualizer import visualizer_node
        from src.models.result import VisualizationData

        mock_viz = VisualizationData()  # 빈 결과

        rows = [{"x": 1}, {"x": 2}]
        state = _make_state(rows=rows)

        with patch(
            "src.agents.nodes.present.visualizer.build_visualization",
            new_callable=AsyncMock,
            return_value=_build_viz_return(mock_viz),
        ), patch(
            "src.agents.nodes.present.visualizer.settings",
        ) as mock_settings:
            mock_settings.min_rows_for_visualization = 1
            result = await visualizer_node(state)

        viz = result.get("visualization")
        passed = viz is not None and not viz.has_visualization
        log_test_case(
            logger, "test_viz_none_result",
            "build_visualization → 빈 결과", "has_visualization=False",
            f"has_viz={viz.has_visualization if viz else None}", passed,
        )
        assert passed


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. analyzer_node — visualization 미포함 확인
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestAnalyzerNodeNoVisualization:
    """analyzer_node 반환값에 visualization 키가 없음을 검증."""

    @pytest.mark.asyncio
    async def test_empty_data_no_visualization_key(self):
        """빈 데이터로 analyzer_node 호출 시 visualization 키가 없다."""
        from src.agents.nodes.present.analyzer import analyzer_node

        state = _make_state(rows=[])
        result = await analyzer_node(state)

        passed = "visualization" not in result
        log_test_case(
            logger, "test_empty_data_no_viz",
            "rows=[]", "visualization 키 없음",
            f"keys={list(result.keys())}", passed,
        )
        assert passed

    @pytest.mark.asyncio
    async def test_analyzer_returns_analysis_result(self):
        """analyzer_node는 analysis_result를 반환한다."""
        from src.agents.nodes.present.analyzer import analyzer_node
        from src.models.result import AnalysisResult

        state = _make_state(rows=[])
        result = await analyzer_node(state)

        analysis = result.get("analysis_result")
        passed = isinstance(analysis, AnalysisResult)
        log_test_case(
            logger, "test_returns_analysis_result",
            "rows=[]", "AnalysisResult 인스턴스",
            f"type={type(analysis).__name__}", passed,
        )
        assert passed


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. parse_viz_judgment — info_card 타입
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestParseVizJudgmentInfoCard:
    """parse_viz_judgment의 info_card 타입 파싱 검증."""

    def test_info_card_type_parsed(self):
        """JSON에서 info_card가 올바르게 파싱된다."""
        from src.services.data_analyzer import parse_viz_judgment
        from src.models.enums import VisualizationType

        text = '{"chart_type": "info_card", "chart_title": "총 대출 잔액", "reason": "단일 집계값 (K1)"}'
        chart_type, title, reason = parse_viz_judgment(text)

        passed = (
            chart_type == VisualizationType.INFO_CARD
            and title == "총 대출 잔액"
            and "K1" in reason
        )
        log_test_case(
            logger, "test_info_card_parsed",
            text, "INFO_CARD + 총 대출 잔액 + reason",
            f"{chart_type}, {title}, {reason}", passed,
        )
        assert passed

    def test_info_card_case_insensitive(self):
        """chart_type 값은 대소문자를 구분하지 않는다."""
        from src.services.data_analyzer import parse_viz_judgment
        from src.models.enums import VisualizationType

        text = '{"chart_type": "INFO_CARD", "chart_title": "테스트", "reason": "R"}'
        chart_type, _, _ = parse_viz_judgment(text)

        passed = chart_type == VisualizationType.INFO_CARD
        log_test_case(
            logger, "test_info_card_case_insensitive",
            "INFO_CARD (대문자)", "INFO_CARD enum",
            f"{chart_type}", passed,
        )
        assert passed


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. _generate_info_card 템플릿 폴백
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestGenerateInfoCard:
    """chart_generator._generate_info_card 템플릿 SVG 생성 검증."""

    def test_single_row_generates_svg(self):
        """1행 데이터로 유효한 SVG가 생성된다."""
        from src.services.visualization.chart_generator import _generate_info_card
        from src.models.result import SQLResult

        result = SQLResult(
            columns=["총건수", "총금액"],
            rows=[{"총건수": 1234, "총금액": 56789000}],
            row_count=1,
        )
        svg = _generate_info_card(result, "대출 현황")

        passed = (
            svg.startswith("<svg")
            and "</svg>" in svg
            and "대출 현황" in svg
            and "1,234" in svg
        )
        log_test_case(
            logger, "test_single_row_svg",
            "1행 {총건수:1234, 총금액:56789000}", "유효한 SVG + 숫자 포맷",
            f"svg길이={len(svg)}, 포함여부={passed}", passed,
        )
        assert passed

    def test_two_rows_generates_svg(self):
        """2행 데이터도 SVG가 생성된다."""
        from src.services.visualization.chart_generator import _generate_info_card
        from src.models.result import SQLResult

        result = SQLResult(
            columns=["구분", "금액"],
            rows=[
                {"구분": "여신", "금액": 100000000},
                {"구분": "수신", "금액": 80000000},
            ],
            row_count=2,
        )
        svg = _generate_info_card(result, "여수신 비교")

        passed = "<svg" in svg and "</svg>" in svg and "여수신 비교" in svg
        log_test_case(
            logger, "test_two_rows_svg",
            "2행 여신/수신", "유효한 SVG",
            f"svg길이={len(svg)}", passed,
        )
        assert passed

    def test_empty_rows_returns_empty(self):
        """빈 행이면 빈 문자열을 반환한다."""
        from src.services.visualization.chart_generator import _generate_info_card
        from src.models.result import SQLResult

        result = SQLResult(columns=["x"], rows=[], row_count=0)
        svg = _generate_info_card(result, "빈 데이터")

        passed = svg == ""
        log_test_case(
            logger, "test_empty_rows_empty_string",
            "rows=[]", "빈 문자열",
            f"svg='{svg}'", passed,
        )
        assert passed

    def test_float_formatting(self):
        """float 값이 소수점 2자리로 포맷팅된다."""
        from src.services.visualization.chart_generator import _generate_info_card
        from src.models.result import SQLResult

        result = SQLResult(
            columns=["비율"],
            rows=[{"비율": 3.14159}],
            row_count=1,
        )
        svg = _generate_info_card(result, "비율")

        passed = "3.14" in svg
        log_test_case(
            logger, "test_float_formatting",
            "비율=3.14159", "3.14 포함",
            f"포함여부={passed}", passed,
        )
        assert passed

    def test_string_value(self):
        """문자열 값도 카드에 표시된다."""
        from src.services.visualization.chart_generator import _generate_info_card
        from src.models.result import SQLResult

        result = SQLResult(
            columns=["상태"],
            rows=[{"상태": "정상"}],
            row_count=1,
        )
        svg = _generate_info_card(result, "상태 카드")

        passed = "정상" in svg
        log_test_case(
            logger, "test_string_value",
            "상태=정상", "정상 포함",
            f"포함여부={passed}", passed,
        )
        assert passed


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 6. generate_chart_from_result — INFO_CARD 분기
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestGenerateChartFromResultInfoCard:
    """generate_chart_from_result에서 INFO_CARD 분기 검증."""

    def test_info_card_dispatches_to_template(self):
        """INFO_CARD 타입이 _generate_info_card로 라우팅된다."""
        from src.services.visualization.chart_generator import generate_chart_from_result
        from src.models.enums import VisualizationType
        from src.models.result import SQLResult

        result = SQLResult(
            columns=["KPI", "값"],
            rows=[{"KPI": "대출건수", "값": 500}],
            row_count=1,
        )
        svg = generate_chart_from_result(result, VisualizationType.INFO_CARD, "KPI 현황")

        passed = "<svg" in svg and "KPI 현황" in svg
        log_test_case(
            logger, "test_info_card_dispatch",
            "INFO_CARD + 1행", "SVG with title",
            f"svg길이={len(svg)}", passed,
        )
        assert passed

    def test_none_type_returns_empty(self):
        """NONE 타입은 빈 문자열을 반환한다."""
        from src.services.visualization.chart_generator import generate_chart_from_result
        from src.models.enums import VisualizationType
        from src.models.result import SQLResult

        result = SQLResult(
            columns=["x"], rows=[{"x": 1}], row_count=1,
        )
        svg = generate_chart_from_result(result, VisualizationType.NONE, "무시")

        passed = svg == ""
        log_test_case(
            logger, "test_none_type_empty",
            "NONE type", "빈 문자열",
            f"svg='{svg}'", passed,
        )
        assert passed


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 7. 파이프라인 라우팅 — execution 후 분기 (보강)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestRouteAfterExecutionEdgeCases:
    """_route_after_execution 엣지 케이스 보강."""

    def test_data_analysis_with_needs_analyzer_goes_to_analyzer_first(self):
        """DATA_ANALYSIS + needs_analyzer=True는 analyzer로 먼저 라우팅된다 (opt-in)."""
        from src.agents.graph.pipeline import _route_after_execution
        from src.agents.state.state import IntentType, QueryStatus, PipelineState

        state = PipelineState(
            intent=IntentType.DATA_ANALYSIS,
            status=QueryStatus.EXECUTED,
            needs_analyzer=True,
        )
        result = _route_after_execution(state)

        passed = result == "analyzer"
        log_test_case(
            logger, "test_analysis_to_analyzer_not_visualizer",
            "DATA_ANALYSIS+needs_analyzer=True", "analyzer (not visualizer)",
            result, passed,
        )
        assert passed

    def test_unknown_intent_goes_to_visualizer(self):
        """UNKNOWN 의도도 visualizer로 라우팅된다 (분석 아닌 모든 경우)."""
        from src.agents.graph.pipeline import _route_after_execution
        from src.agents.state.state import IntentType, QueryStatus, PipelineState

        state = PipelineState(
            intent=IntentType.UNKNOWN,
            status=QueryStatus.EXECUTED,
        )
        result = _route_after_execution(state)

        passed = result == "visualizer"
        log_test_case(
            logger, "test_unknown_to_visualizer",
            "UNKNOWN intent", "visualizer",
            result, passed,
        )
        assert passed


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 9. handoff_note 전파 (Path F' §7.1)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestVisualizerNodeHandoffNote:
    """visualizer_node → build_visualization 로 state.handoff_note 가 전달되는지 확인."""

    @pytest.mark.asyncio
    async def test_handoff_note_forwarded_to_build_visualization(self):
        """REDISPLAY 경로의 시각화/포맷 지시가 build_visualization 키워드로 주입된다."""
        from src.agents.nodes.present.visualizer import visualizer_node
        from src.models.enums import VisualizationType
        from src.models.result import VisualizationData

        note = "### 시각화/포맷 지시\n막대 차트로 다시 보여주세요."
        rows = [{"x": 1, "y": 10}, {"x": 2, "y": 20}]
        state = _make_state(rows=rows)
        state.handoff_note = note

        mock_viz = VisualizationData(
            svg_code="<svg/>",
            chart_type=VisualizationType.BAR_CHART,
            title="T",
        )

        with patch(
            "src.agents.nodes.present.visualizer.build_visualization",
            new_callable=AsyncMock,
            return_value=_build_viz_return(mock_viz),
        ) as mock_build, patch(
            "src.agents.nodes.present.visualizer.settings",
        ) as mock_settings:
            mock_settings.min_rows_for_visualization = 1
            await visualizer_node(state)

        kwargs = mock_build.call_args.kwargs
        passed = kwargs.get("handoff_note") == note
        log_test_case(
            logger, "test_handoff_note_forwarded",
            f"state.handoff_note={note!r}",
            "build_visualization(handoff_note=note)",
            f"kwargs.handoff_note={kwargs.get('handoff_note')!r}", passed,
        )
        assert passed

    @pytest.mark.asyncio
    async def test_empty_handoff_note_forwarded_as_empty(self):
        """handoff_note 가 비어있으면 그대로 빈 문자열로 전달되며, 정규화는 서비스 레이어가 수행한다."""
        from src.agents.nodes.present.visualizer import visualizer_node
        from src.models.enums import VisualizationType
        from src.models.result import VisualizationData

        rows = [{"x": 1, "y": 10}, {"x": 2, "y": 20}]
        state = _make_state(rows=rows)  # handoff_note 기본값 ""

        mock_viz = VisualizationData(
            svg_code="<svg/>",
            chart_type=VisualizationType.LINE_CHART,
            title="T",
        )

        with patch(
            "src.agents.nodes.present.visualizer.build_visualization",
            new_callable=AsyncMock,
            return_value=_build_viz_return(mock_viz),
        ) as mock_build, patch(
            "src.agents.nodes.present.visualizer.settings",
        ) as mock_settings:
            mock_settings.min_rows_for_visualization = 1
            await visualizer_node(state)

        kwargs = mock_build.call_args.kwargs
        passed = kwargs.get("handoff_note") == ""
        log_test_case(
            logger, "test_empty_handoff_note_forwarded",
            "state.handoff_note=''",
            "build_visualization(handoff_note='')",
            f"kwargs.handoff_note={kwargs.get('handoff_note')!r}", passed,
        )
        assert passed

    def test_visualizer_judgment_user_template_has_handoff_note_placeholder(self):
        """템플릿에 {handoff_note}/{data} 플레이스홀더가 둘 다 존재한다 (회귀 방지)."""
        from src.agents.nodes.system_prompts import VISUALIZER_JUDGMENT_USER

        passed = (
            "{handoff_note}" in VISUALIZER_JUDGMENT_USER
            and "{data}" in VISUALIZER_JUDGMENT_USER
        )
        log_test_case(
            logger, "test_viz_judgment_user_placeholders",
            "VISUALIZER_JUDGMENT_USER 템플릿 검사",
            "{handoff_note} + {data} 둘 다 존재",
            f"both_present={passed}", passed,
        )
        assert passed
