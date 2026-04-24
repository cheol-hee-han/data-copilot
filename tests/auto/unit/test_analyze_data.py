"""데이터 분석 노드(analyzer_node) 테스트.

테스트 대상:
    SQL 실행 결과를 통계 요약·인사이트·시각화 추천으로 변환하는 노드를 검증한다.
    파싱 유틸 함수(순수)와 analyzer_node(LLM 호출)를 분리 테스트한다.

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

from tests.conftest import get_test_logger, log_test_case

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
    from src.agents.state.state import PipelineState, SQLResult

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
    """JSON 형식의 시각화 판단 응답을 파싱한다."""
    from src.services.data_analyzer import parse_viz_judgment
    from src.agents.state.state import VisualizationType

    text = '{"chart_type": "bar_chart", "chart_title": "월별 대출 건수", "reason": "테스트 사유 (규칙 4)"}'
    chart_type, title, reason = parse_viz_judgment(text)

    passed = (
        title == "월별 대출 건수"
        and chart_type == VisualizationType.BAR_CHART
        and reason == "테스트 사유 (규칙 4)"
    )
    log_test_case(
        logger,
        "test_parse_viz_judgment_valid",
        input_data=text,
        expected="BAR_CHART, '월별 대출 건수', reason 포함",
        actual=f"chart_type={chart_type}, title={title}, reason={reason}",
        passed=passed,
    )
    assert passed


def test_parse_viz_judgment_none_chart():
    """chart_type: none 은 VisualizationType.NONE 을 반환한다."""
    from src.services.data_analyzer import parse_viz_judgment
    from src.agents.state.state import VisualizationType

    text = '{"chart_type": "none", "chart_title": "", "reason": "시각화 불필요 (N1)"}'
    chart_type, title, reason = parse_viz_judgment(text)

    passed = chart_type == VisualizationType.NONE and reason == "시각화 불필요 (N1)"
    log_test_case(
        logger,
        "test_parse_viz_judgment_none_chart",
        input_data=text,
        expected="VisualizationType.NONE + reason",
        actual=f"chart_type={chart_type}, reason={reason}",
        passed=passed,
    )
    assert passed


def test_parse_viz_judgment_invalid():
    """JSON 파싱 불가능한 텍스트에서 ValueError 가 발생한다."""
    from src.services.data_analyzer import parse_viz_judgment

    text = "이것은 유효한 JSON이 아닙니다"
    passed = False
    error_type = None
    try:
        parse_viz_judgment(text)
    except ValueError:
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
    assert passed, "유효하지 않은 JSON에서 ValueError 미발생"


def test_parse_viz_judgment_line_types():
    """bar_chart, line_chart, pie_chart 등 다양한 차트 타입을 파싱한다."""
    from src.services.data_analyzer import parse_viz_judgment

    test_cases = [
        ('{"chart_type": "bar_chart", "chart_title": "바", "reason": "R"}', "bar_chart"),
        ('{"chart_type": "line_chart", "chart_title": "라인", "reason": "R"}', "line_chart"),
        ('{"chart_type": "pie_chart", "chart_title": "파이", "reason": "R"}', "pie_chart"),
        ('{"chart_type": "none", "chart_title": "", "reason": "R"}', "none"),
    ]

    all_passed = True
    for text, expected_type in test_cases:
        chart_type, _, _ = parse_viz_judgment(text)
        if chart_type.value != expected_type:
            all_passed = False
            break

    log_test_case(
        logger,
        "test_parse_viz_judgment_line_types",
        input_data="bar, line, pie, none JSON 입력",
        expected="각 타입이 올바르게 파싱됨",
        actual=f"all_passed={all_passed}",
        passed=all_passed,
    )
    assert all_passed


# ──────────────────────────────────────────────────────────────
# analyzer_node 동작 테스트 (LLM 불필요 케이스)
# ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_analyze_empty_data():
    """데이터가 없으면 폴백 메시지가 반환된다."""
    from src.agents.nodes.present.analyzer import analyzer_node
    from src.agents.state.state import QueryStatus

    state = _make_state_with_result(rows=[])
    result = await analyzer_node(state)

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
async def test_analyzer_does_not_return_visualization():
    """analyzer_node는 visualization을 반환하지 않는다 (visualizer 노드에서 담당)."""
    from src.agents.nodes.present.analyzer import analyzer_node

    if not _LLM_AVAILABLE:
        pytest.skip("LLM 없이는 analyzer_node 전체 실행 불가")

    rows = [{"항목": "A", "값": 1}]
    state = _make_state_with_result(rows=rows, user_input="데이터 분석")
    result = await analyzer_node(state)

    # analyzer는 visualization 키를 반환하지 않아야 함
    viz = result.get("visualization")
    passed = viz is None
    log_test_case(
        logger,
        "test_analyzer_does_not_return_visualization",
        input_data="rows=1개",
        expected="visualization 키 없음",
        actual=f"visualization={viz}",
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
    from src.agents.nodes.present.analyzer import analyzer_node
    from src.agents.state.state import AnalysisResult, QueryStatus

    rows = [
        {"월": "2024-01", "신규대출건수": 150, "평균금액": 5000000},
        {"월": "2024-02", "신규대출건수": 162, "평균금액": 5200000},
        {"월": "2024-03", "신규대출건수": 145, "평균금액": 4900000},
    ]
    state = _make_state_with_result(rows=rows, user_input="월별 대출 현황 분석")
    result = await analyzer_node(state)

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
    from src.agents.nodes.present.analyzer import analyzer_node
    from src.agents.state.state import QueryStatus

    rows = [
        {"지점명": "강남지점", "대출건수": 320, "연체율": 0.02},
        {"지점명": "서초지점", "대출건수": 280, "연체율": 0.015},
        {"지점명": "송파지점", "대출건수": 410, "연체율": 0.025},
        {"지점명": "마포지점", "대출건수": 195, "연체율": 0.03},
    ]
    state = _make_state_with_result(rows=rows, user_input="지점별 대출 현황 분석")
    result = await analyzer_node(state)

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


# ──────────────────────────────────────────────────────────────
# handoff_note 전파 (Path F' §7.2) — LLM 불필요
# ──────────────────────────────────────────────────────────────

def test_analyzer_user_template_has_handoff_note_placeholder():
    """ANALYZER_USER 템플릿에 {handoff_note} 플레이스홀더가 존재한다 (회귀 방지)."""
    from src.agents.nodes.system_prompts import ANALYZER_USER

    passed = (
        "{handoff_note}" in ANALYZER_USER
        and "{user_input}" in ANALYZER_USER
        and "{query_result}" in ANALYZER_USER
    )
    log_test_case(
        logger,
        "test_analyzer_user_template_placeholders",
        input_data="ANALYZER_USER 템플릿 검사",
        expected="{handoff_note} + {user_input} + {query_result} 모두 존재",
        actual=f"all_present={passed}",
        passed=passed,
    )
    assert passed


def test_analyzer_system_has_handoff_priority_section():
    """ANALYZER_SYSTEM 에 '사용자 연속 처리 지시' 우선 반영 섹션이 존재한다 (§13 Step 13)."""
    from src.agents.nodes.system_prompts import ANALYZER_SYSTEM

    passed = "사용자 연속 처리 지시" in ANALYZER_SYSTEM
    log_test_case(
        logger,
        "test_analyzer_system_handoff_section",
        input_data="ANALYZER_SYSTEM 섹션 검사",
        expected="'사용자 연속 처리 지시' 포함",
        actual=f"section_present={passed}",
        passed=passed,
    )
    assert passed


@pytest.mark.asyncio
async def test_analyze_data_injects_handoff_note():
    """analyze_data 는 handoff_note 를 user_template.format 에 주입한다."""
    from unittest.mock import AsyncMock, patch

    from src.agents.nodes.system_prompts import (
        ANALYZER_SYSTEM,
        ANALYZER_USER,
    )
    from src.agents.state.state import AnalysisResult, SQLResult
    from src.services.data_analyzer import analyze_data

    rows = [{"월": "2024-01", "건수": 10}]
    sql_result = SQLResult(
        columns=["월", "건수"],
        rows=rows,
        row_count=1,
        execution_time_ms=1.0,
    )
    note = "### 분석 초점\n전월 대비 증감률에 초점을 맞춰 분석하세요."

    captured: dict[str, str] = {}

    def _fake_llm_call(*, system, messages, **_kwargs):
        captured["system"] = system
        captured["user_message"] = messages[0]["content"]
        return (
            '{"summary":"s","insights":["i"],'
            '"statistics":{"k":"v"},"action_items":[],'
            '"reasoning_summary":"r"}',
            AnalysisResult(summary="s", insights=["i"]),
        )

    with patch(
        "src.services.data_analyzer.llm_call_with_parse_retry",
        new=AsyncMock(side_effect=_fake_llm_call),
    ):
        result, _delivered, _interaction = await analyze_data(
            user_input="월별 대출 분석",
            sql_result=sql_result,
            system_prompt=ANALYZER_SYSTEM,
            user_template=ANALYZER_USER,
            handoff_note=note,
        )

    passed = (
        "분석 초점" in captured.get("user_message", "")
        and "전월 대비 증감률" in captured.get("user_message", "")
        and isinstance(result, AnalysisResult)
    )
    log_test_case(
        logger,
        "test_analyze_data_handoff_note_injected",
        input_data=f"handoff_note={note!r}",
        expected="user_message 에 '분석 초점' + '전월 대비 증감률' 포함",
        actual=f"user_message snippet: {captured.get('user_message', '')[:200]!r}",
        passed=passed,
    )
    assert passed


@pytest.mark.asyncio
async def test_analyze_data_empty_handoff_note_normalized_to_placeholder():
    """빈 handoff_note 는 '(없음)' 으로 정규화되어 주입된다."""
    from unittest.mock import AsyncMock, patch

    from src.agents.nodes.system_prompts import (
        ANALYZER_SYSTEM,
        ANALYZER_USER,
    )
    from src.agents.state.state import AnalysisResult, SQLResult
    from src.services.data_analyzer import analyze_data

    sql_result = SQLResult(
        columns=["x"],
        rows=[{"x": 1}],
        row_count=1,
        execution_time_ms=1.0,
    )
    captured: dict[str, str] = {}

    def _fake_llm_call(*, system, messages, **_kwargs):
        captured["user_message"] = messages[0]["content"]
        return (
            '{"summary":"s","insights":[],"statistics":{},'
            '"action_items":[],"reasoning_summary":"r"}',
            AnalysisResult(summary="s"),
        )

    with patch(
        "src.services.data_analyzer.llm_call_with_parse_retry",
        new=AsyncMock(side_effect=_fake_llm_call),
    ):
        await analyze_data(
            user_input="요청",
            sql_result=sql_result,
            system_prompt=ANALYZER_SYSTEM,
            user_template=ANALYZER_USER,
            handoff_note="   ",  # 공백만
        )

    passed = "(없음)" in captured.get("user_message", "")
    log_test_case(
        logger,
        "test_analyze_data_empty_handoff_note",
        input_data="handoff_note='   '",
        expected="user_message 에 '(없음)' 포함",
        actual=f"snippet: {captured.get('user_message', '')[:200]!r}",
        passed=passed,
    )
    assert passed


@_SKIP_LLM
@pytest.mark.asyncio
async def test_statistics_dict_populated():
    """LLM 분석 결과의 statistics 딕셔너리가 비어있지 않다."""
    from src.agents.nodes.present.analyzer import analyzer_node

    rows = [
        {"항목": "여신", "금액": 100000000},
        {"항목": "수신", "금액": 80000000},
        {"항목": "카드", "금액": 20000000},
    ]
    state = _make_state_with_result(rows=rows, user_input="항목별 금액 분석")
    result = await analyzer_node(state)

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
