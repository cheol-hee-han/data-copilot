"""데이터 분석 노드(analyze_data_node) 테스트.

테스트 대상:
    SQL 실행 결과를 통계 요약·인사이트·시각화 추천으로 변환하는 노드를 검증한다.
    파싱 유틸 함수(순수)와 analyze_data_node(LLM 호출)를 분리 테스트한다.

입력 예시 (정상):
    - SQLResult(columns=["월", "건수"], rows=[{"월":"2024-01","건수":150}, ...])
    - 기대: AnalysisResult(summary="...", insights=["..."], statistics={...})
    - insights 최소 1개, statistics 최소 1개 항목

결과 예시 (오류 케이스):
    - 빈 데이터 → summary에 "조회된 데이터가 없어" 포함
    - JSON 파싱 오류 → ValueError 없이 기본 결과 반환

실행 스크립트:
    # 순수 함수 테스트만 (LLM 불필요)
    pytest tests/unit/test_analyze_data.py -v -k "parse"

    # LLM 포함 전체 (API 키 필요)
    pytest tests/unit/test_analyze_data.py -v

참고:
    - LLM 테스트는 ANTHROPIC_API_KEY 또는 OPENAI_API_KEY 필요
    - 테스트 대상 소스: src/agents/nodes/analyzer.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import os

import pytest

from tests.unit.conftest import get_test_logger, log_test_case

logger = get_test_logger("test_analyze_data")

_LLM_AVAILABLE = bool(
    os.getenv("ANTHROPIC_API_KEY") or os.getenv("OPENAI_API_KEY")
)

_SKIP_LLM = pytest.mark.skipif(
    not _LLM_AVAILABLE,
    reason="LLM API 키가 없어 건너뜀.",
)


# ──────────────────────────────────────────────────────────────
# 헬퍼
# ──────────────────────────────────────────────────────────────

def _make_state_with_result(rows: list[dict], user_input: str = "데이터 분석해줘"):
    """테스트용 PipelineState 를 생성한다."""
    from src.agents.state.state import ContextInfo, PipelineState, SQLResult

    columns = list(rows[0].keys()) if rows else []
    result = SQLResult(
        columns=columns,
        rows=rows,
        row_count=len(rows),
        execution_time_ms=10.0,
    )
    return PipelineState(
        user_input=user_input,
        preprocessed_input=user_input,
        sql_result=result,
        context=ContextInfo(),
    )


# ──────────────────────────────────────────────────────────────
# _parse_analysis_json 테스트 (LLM 불필요)
# ──────────────────────────────────────────────────────────────

def test_parse_analysis_json_valid():
    """유효한 JSON 문자열을 AnalysisResult 로 파싱한다."""
    from src.services.data_analyzer import parse_analysis_json

    json_str = '{"summary": "요약 내용", "insights": ["인사이트1", "인사이트2"], "statistics": {"평균": "100"}}'
    result = parse_analysis_json(json_str)

    passed = (
        result.summary == "요약 내용"
        and len(result.insights) == 2
        and result.statistics.get("평균") == "100"
    )
    log_test_case(
        logger,
        "test_parse_analysis_json_valid",
        input_data=json_str,
        expected="summary='요약 내용', insights 2개, statistics={'평균': '100'}",
        actual=f"summary={result.summary}, insights={result.insights}, stats={result.statistics}",
        passed=passed,
    )
    assert passed


def test_parse_analysis_json_code_fence():
    """코드 펜스(```json)로 감싸진 JSON 도 파싱된다."""
    from src.services.data_analyzer import parse_analysis_json

    text = '```json\n{"summary": "월별 분석", "insights": ["증가 추세"], "statistics": {"최대": "500"}}\n```'
    result = parse_analysis_json(text)

    passed = (
        result.summary == "월별 분석"
        and "증가 추세" in result.insights
    )
    log_test_case(
        logger,
        "test_parse_analysis_json_code_fence",
        input_data=text[:80],
        expected="summary='월별 분석', insights=['증가 추세']",
        actual=f"summary={result.summary}, insights={result.insights}",
        passed=passed,
    )
    assert passed


def test_parse_analysis_json_inline_json():
    """JSON 블록이 텍스트 안에 있어도 추출해서 파싱한다."""
    from src.services.data_analyzer import parse_analysis_json

    text = '분석 결과입니다. {"summary": "분석", "insights": [], "statistics": {}} 이상입니다.'
    result = parse_analysis_json(text)

    passed = result.summary == "분석"
    log_test_case(
        logger,
        "test_parse_analysis_json_inline_json",
        input_data=text,
        expected="summary='분석'",
        actual=f"summary={result.summary}",
        passed=passed,
    )
    assert passed


def test_parse_analysis_json_invalid():
    """잘못된 JSON 문자열은 ValueError 를 발생시킨다."""
    from src.services.data_analyzer import parse_analysis_json

    bad_input = "이것은 JSON 이 아닙니다"
    passed = False
    error_type = None
    try:
        parse_analysis_json(bad_input)
    except (ValueError, Exception) as e:
        passed = True
        error_type = type(e).__name__

    log_test_case(
        logger,
        "test_parse_analysis_json_invalid",
        input_data=bad_input,
        expected="ValueError 또는 JSONDecodeError 발생",
        actual=f"예외 타입: {error_type}",
        passed=passed,
    )
    assert passed, "잘못된 JSON 에서 예외가 발생하지 않음"


# ──────────────────────────────────────────────────────────────
# _parse_viz_judgment 테스트 (LLM 불필요)
# ──────────────────────────────────────────────────────────────

def test_parse_viz_judgment_valid():
    """CHART_TYPE 과 CHART_TITLE 이 있는 텍스트를 파싱한다."""
    from src.services.data_analyzer import parse_viz_judgment
    from src.agents.state.state import VisualizationType

    # VisualizationType 은 "bar_chart", "line_chart" 등의 값을 사용
    text = "CHART_TYPE: bar_chart\nCHART_TITLE: 월별 대출 건수"
    chart_type, title = parse_viz_judgment(text)

    passed = title == "월별 대출 건수" and chart_type == VisualizationType.BAR_CHART
    log_test_case(
        logger,
        "test_parse_viz_judgment_valid",
        input_data=text,
        expected="chart_type != None, title='월별 대출 건수'",
        actual=f"chart_type={chart_type}, title={title}",
        passed=passed,
    )
    assert passed


def test_parse_viz_judgment_none_chart():
    """CHART_TYPE: none 은 VisualizationType.NONE 을 반환한다."""
    from src.services.data_analyzer import parse_viz_judgment
    from src.agents.state.state import VisualizationType

    text = "CHART_TYPE: none\nCHART_TITLE: 없음"
    chart_type, title = parse_viz_judgment(text)

    passed = chart_type == VisualizationType.NONE
    log_test_case(
        logger,
        "test_parse_viz_judgment_none_chart",
        input_data=text,
        expected="VisualizationType.NONE",
        actual=f"chart_type={chart_type}",
        passed=passed,
    )
    assert passed


def test_parse_viz_judgment_invalid():
    """CHART_TYPE 행이 없으면 ValueError 가 발생한다."""
    from src.services.data_analyzer import parse_viz_judgment

    text = "CHART_TITLE: 제목만 있음"
    passed = False
    error_type = None
    try:
        parse_viz_judgment(text)
    except ValueError as e:
        passed = True
        error_type = "ValueError"

    log_test_case(
        logger,
        "test_parse_viz_judgment_invalid",
        input_data=text,
        expected="ValueError 발생",
        actual=f"예외 타입: {error_type}",
        passed=passed,
    )
    assert passed, "CHART_TYPE 없을 때 ValueError 미발생"


def test_parse_viz_judgment_line_types():
    """bar_chart, line_chart, pie_chart 등 다양한 차트 타입을 파싱한다."""
    from src.services.data_analyzer import parse_viz_judgment

    # VisualizationType 값: bar_chart, line_chart, pie_chart, table_only, none
    test_cases = [
        ("CHART_TYPE: bar_chart\nCHART_TITLE: 바 차트", "bar_chart"),
        ("CHART_TYPE: line_chart\nCHART_TITLE: 라인 차트", "line_chart"),
        ("CHART_TYPE: pie_chart\nCHART_TITLE: 파이 차트", "pie_chart"),
        ("CHART_TYPE: none\nCHART_TITLE: 없음", "none"),
    ]

    all_passed = True
    for text, expected_type in test_cases:
        chart_type, _ = parse_viz_judgment(text)
        if chart_type.value != expected_type:
            all_passed = False
            break

    log_test_case(
        logger,
        "test_parse_viz_judgment_line_types",
        input_data="bar, line, pie, table 입력",
        expected="각 타입이 올바르게 파싱됨",
        actual=f"all_passed={all_passed}",
        passed=all_passed,
    )
    assert all_passed


# ──────────────────────────────────────────────────────────────
# analyze_data_node 동작 테스트 (LLM 불필요 케이스)
# ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_analyze_empty_data():
    """데이터가 없으면 폴백 메시지가 반환된다."""
    from src.agents.nodes.analyzer import analyze_data_node
    from src.agents.state.state import QueryStatus

    state = _make_state_with_result(rows=[])
    result = await analyze_data_node(state)

    analysis = result.get("analysis_result")
    status = result.get("status")

    passed = (
        status == QueryStatus.ANALYZED
        and "없어" in analysis.summary
    )
    log_test_case(
        logger,
        "test_analyze_empty_data",
        input_data="rows=[]",
        expected="status=ANALYZED, summary에 '없어' 포함",
        actual=f"status={status}, summary={analysis.summary if analysis else None}",
        passed=passed,
    )
    assert passed


@pytest.mark.asyncio
async def test_visualization_skipped_few_rows():
    """행 수가 min_rows_for_visualization 미만이면 시각화가 생성되지 않는다."""
    from src.agents.nodes.analyzer import analyze_data_node
    from src.agents.state.state import QueryStatus, VisualizationType
    from src.config import settings

    # min_rows_for_visualization 보다 적은 행 (기본값 3보다 적게)
    rows = [{"항목": "A", "값": 1}]  # 1행
    assert len(rows) < settings.min_rows_for_visualization

    state = _make_state_with_result(rows=rows, user_input="데이터 분석")

    # LLM 이 있으면 실제 분석, 없으면 파싱 오류로 폴백 — 모두 ANALYZED 이어야 함
    if not _LLM_AVAILABLE:
        pytest.skip("LLM 없이는 analyze_data_node 전체 실행 불가")

    result = await analyze_data_node(state)

    # 행 수 미만이므로 visualization 이 비어있어야 함
    viz = result.get("visualization")
    passed = viz is None or not viz.has_visualization
    log_test_case(
        logger,
        "test_visualization_skipped_few_rows",
        input_data=f"rows=1개 (min={settings.min_rows_for_visualization})",
        expected="visualization=없음",
        actual=f"has_visualization={viz.has_visualization if viz else None}",
        passed=passed,
    )
    assert passed


# ──────────────────────────────────────────────────────────────
# LLM 통합 테스트
# ──────────────────────────────────────────────────────────────

@_SKIP_LLM
@pytest.mark.asyncio
async def test_analyze_with_data():
    """실제 LLM 으로 데이터 분석 시 AnalysisResult 가 채워진다."""
    from src.agents.nodes.analyzer import analyze_data_node
    from src.agents.state.state import AnalysisResult, QueryStatus

    rows = [
        {"월": "2024-01", "신규대출건수": 150, "평균금액": 5000000},
        {"월": "2024-02", "신규대출건수": 162, "평균금액": 5200000},
        {"월": "2024-03", "신규대출건수": 145, "평균금액": 4900000},
    ]
    state = _make_state_with_result(rows=rows, user_input="월별 대출 현황 분석")
    result = await analyze_data_node(state)

    analysis = result.get("analysis_result")
    status = result.get("status")

    passed = (
        isinstance(analysis, AnalysisResult)
        and status == QueryStatus.ANALYZED
        and len(analysis.summary) > 0
    )
    log_test_case(
        logger,
        "test_analyze_with_data",
        input_data="월별 대출 데이터 3행",
        expected="AnalysisResult 에 summary 있음",
        actual=f"status={status}, summary 길이={len(analysis.summary) if analysis else 0}",
        passed=passed,
    )
    assert passed


@_SKIP_LLM
@pytest.mark.asyncio
async def test_insights_list_populated():
    """LLM 분석 결과의 insights 리스트가 비어있지 않다."""
    from src.agents.nodes.analyzer import analyze_data_node
    from src.agents.state.state import QueryStatus

    rows = [
        {"지점명": "강남지점", "대출건수": 320, "연체율": 0.02},
        {"지점명": "서초지점", "대출건수": 280, "연체율": 0.015},
        {"지점명": "송파지점", "대출건수": 410, "연체율": 0.025},
        {"지점명": "마포지점", "대출건수": 195, "연체율": 0.03},
    ]
    state = _make_state_with_result(rows=rows, user_input="지점별 대출 현황 분석")
    result = await analyze_data_node(state)

    analysis = result.get("analysis_result")
    passed = len(analysis.insights) > 0

    log_test_case(
        logger,
        "test_insights_list_populated",
        input_data="지점별 대출 데이터 4행",
        expected="insights 리스트 비어있지 않음",
        actual=f"insights 개수={len(analysis.insights) if analysis else 0}",
        passed=passed,
    )
    assert passed


@_SKIP_LLM
@pytest.mark.asyncio
async def test_statistics_dict_populated():
    """LLM 분석 결과의 statistics 딕셔너리가 비어있지 않다."""
    from src.agents.nodes.analyzer import analyze_data_node

    rows = [
        {"항목": "여신", "금액": 100000000},
        {"항목": "수신", "금액": 80000000},
        {"항목": "카드", "금액": 20000000},
    ]
    state = _make_state_with_result(rows=rows, user_input="항목별 금액 분석")
    result = await analyze_data_node(state)

    analysis = result.get("analysis_result")
    passed = isinstance(analysis.statistics, dict) and len(analysis.statistics) > 0

    log_test_case(
        logger,
        "test_statistics_dict_populated",
        input_data="항목별 금액 데이터 3행",
        expected="statistics 딕셔너리 비어있지 않음",
        actual=f"statistics 키 수={len(analysis.statistics) if analysis else 0}",
        passed=passed,
    )
    assert passed
