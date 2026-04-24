"""analyzer_node 단위 테스트.

테스트 대상:
    [src/agents/nodes/present/analyzer.py :: analyzer_node]
    - handoff_note 가 analyze_data 에 키워드 인자로 전달됨 (Path F' §7.2)
    - 빈 handoff_note 도 그대로 빈 문자열로 전달

실행:
    pytest tests/auto/unit/test_analyzer_node.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


def _make_state(
    rows: list[dict] | None = None,
    user_input: str = "데이터 분석",
):
    """테스트용 PipelineState를 생성한다."""
    from src.agents.state.state import PipelineState, SQLResult

    if rows is None:
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


def _make_analysis_result():
    """테스트용 AnalysisResult 기본 인스턴스."""
    from src.agents.state.state import AnalysisResult
    return AnalysisResult(
        summary="요약",
        initial_reading=["A"],
        insights=["I"],
        action_items=["B"],
        reasoning_summary="판단 근거",
    )


def _make_interaction():
    """테스트용 빈 LLMInteraction 생성."""
    from src.utils.tracker import LLMInteraction
    return LLMInteraction(
        prompt_variables={},
        raw_response="(mock)",
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# handoff_note 전파 (Path F' §7.2)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestAnalyzerNodeHandoffNote:
    """analyzer_node → analyze_data 로 state.handoff_note 가 전달되는지 확인."""

    @pytest.mark.asyncio
    async def test_handoff_note_forwarded_to_analyze_data(self):
        """ANALYZE 경로의 분석 초점 지시가 analyze_data 키워드로 주입된다."""
        from src.agents.nodes.present.analyzer import analyzer_node

        note = "### 분석 초점\n지점별 불균형 원인을 탐색하세요."
        rows = [{"branch": "A", "amt": 100}, {"branch": "B", "amt": 50}]
        state = _make_state(rows=rows)
        state.handoff_note = note

        mock_analysis = _make_analysis_result()

        with patch(
            "src.agents.nodes.present.analyzer.analyze_data",
            new_callable=AsyncMock,
            return_value=(mock_analysis, False, _make_interaction()),
        ) as mock_fn:
            await analyzer_node(state)

        kwargs = mock_fn.call_args.kwargs
        assert kwargs.get("handoff_note") == note

    @pytest.mark.asyncio
    async def test_empty_handoff_note_forwarded_as_empty(self):
        """handoff_note 가 비어있으면 그대로 빈 문자열로 전달된다 (정규화는 서비스 레이어)."""
        from src.agents.nodes.present.analyzer import analyzer_node

        rows = [{"k": "A", "v": 1}, {"k": "B", "v": 2}]
        state = _make_state(rows=rows)  # handoff_note 기본 ""

        mock_analysis = _make_analysis_result()

        with patch(
            "src.agents.nodes.present.analyzer.analyze_data",
            new_callable=AsyncMock,
            return_value=(mock_analysis, False, _make_interaction()),
        ) as mock_fn:
            await analyzer_node(state)

        kwargs = mock_fn.call_args.kwargs
        assert kwargs.get("handoff_note") == ""
