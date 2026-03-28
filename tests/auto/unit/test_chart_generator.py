"""차트 생성(chart_generator) 모듈 단위 테스트.

테스트 대상:
    SQLResult 데이터를 SVG 막대·꺾은선·원형 차트로 변환하는 기능과
    생성된 SVG의 보안(XSS 방어, script 태그 미포함)을 검증한다.

    ┌──────────────────────────────────────────────────────────┐
    │  테스트 클래스                테스트 대상                 │
    │  ─────────────────────────── ─────────────────────────── │
    │  TestGenerateBarChart        막대 차트 생성              │
    │  TestGenerateLineChart       꺾은선 차트 생성            │
    │  TestGeneratePieChart        원형 차트 생성              │
    │  TestGenerateChartFromResult SQLResult → 자동 차트       │
    │  TestSVGSecurity             XSS 방어, viewBox 사용      │
    └──────────────────────────────────────────────────────────┘

입력 예시 (정상):
    - labels=["강남지점", "서초지점"], values=[4520, 3180]
    - 기대: "<svg" 로 시작하는 SVG 문자열, 레이블 포함

결과 예시 (오류 케이스):
    - 빈 입력([], []) → 빈 문자열 반환
    - XSS 레이블("<script>alert(1)</script>") → "&lt;script&gt;" 이스케이프

실행 스크립트:
    pytest tests/unit/test_chart_generator.py -v

참고:
    - 외부 의존성 없음
    - 테스트 대상 소스: src/services/visualization/chart_generator.py
"""

from __future__ import annotations

import pytest

from src.services.visualization.chart_generator import (
    generate_bar_chart,
    generate_chart_from_result,
    generate_line_chart,
    generate_pie_chart,
)
from src.agents.state.state import SQLResult, VisualizationType


# ---------------------------------------------------------------------------
# generate_bar_chart
# ---------------------------------------------------------------------------

class TestGenerateBarChart:
    """막대 차트 생성 테스트."""

    def test_basic_bar_chart(self):
        svg = generate_bar_chart(
            labels=["강남지점", "서초지점", "송파지점"],
            values=[4520, 3180, 2850],
            title="지점별 실적",
        )
        assert svg.startswith("<svg")
        assert "</svg>" in svg
        assert "강남지점" in svg
        assert "지점별 실적" in svg

    def test_single_bar(self):
        svg = generate_bar_chart(["A"], [100], "단일")
        assert "<rect" in svg

    def test_empty_input_returns_empty(self):
        assert generate_bar_chart([], [], "빈") == ""

    def test_zero_values(self):
        svg = generate_bar_chart(["A", "B"], [0, 0], "제로")
        assert "<svg" in svg

    def test_no_script_tags(self):
        svg = generate_bar_chart(["A"], [100])
        assert "<script" not in svg


# ---------------------------------------------------------------------------
# generate_line_chart
# ---------------------------------------------------------------------------

class TestGenerateLineChart:
    """꺾은선 차트 생성 테스트."""

    def test_basic_line_chart(self):
        svg = generate_line_chart(
            labels=["1월", "2월", "3월", "4월"],
            values=[100, 150, 130, 200],
            title="월별 추이",
        )
        assert "<polyline" in svg
        assert "월별 추이" in svg

    def test_two_points_minimum(self):
        svg = generate_line_chart(["A", "B"], [10, 20])
        assert "<svg" in svg

    def test_single_point_returns_empty(self):
        assert generate_line_chart(["A"], [10]) == ""

    def test_empty_returns_empty(self):
        assert generate_line_chart([], []) == ""

    def test_contains_circles_for_data_points(self):
        svg = generate_line_chart(["A", "B", "C"], [10, 20, 30])
        assert "<circle" in svg


# ---------------------------------------------------------------------------
# generate_pie_chart
# ---------------------------------------------------------------------------

class TestGeneratePieChart:
    """원형 차트 생성 테스트."""

    def test_basic_pie_chart(self):
        svg = generate_pie_chart(
            labels=["신용대출", "담보대출", "보증대출"],
            values=[40, 35, 25],
            title="대출유형별 비중",
        )
        assert "<path" in svg
        assert "대출유형별 비중" in svg

    def test_single_slice(self):
        svg = generate_pie_chart(["전체"], [100])
        assert "<svg" in svg

    def test_zero_total_returns_empty(self):
        assert generate_pie_chart(["A", "B"], [0, 0]) == ""

    def test_empty_returns_empty(self):
        assert generate_pie_chart([], []) == ""

    def test_has_legend(self):
        svg = generate_pie_chart(["A", "B"], [60, 40])
        # 범례에 레이블이 포함되어야 함
        assert "A" in svg
        assert "B" in svg


# ---------------------------------------------------------------------------
# generate_chart_from_result
# ---------------------------------------------------------------------------

class TestGenerateChartFromResult:
    """SQLResult 기반 자동 차트 생성 테스트."""

    def test_bar_chart_from_result(self):
        result = SQLResult(
            columns=["부서명", "실적금액"],
            rows=[
                {"부서명": "강남", "실적금액": 100},
                {"부서명": "서초", "실적금액": 200},
                {"부서명": "송파", "실적금액": 150},
            ],
            row_count=3,
        )
        svg = generate_chart_from_result(
            result, VisualizationType.BAR_CHART, "테스트",
        )
        assert "<svg" in svg
        assert "강남" in svg

    def test_line_chart_from_result(self):
        result = SQLResult(
            columns=["월", "건수"],
            rows=[
                {"월": "1월", "건수": 10},
                {"월": "2월", "건수": 20},
                {"월": "3월", "건수": 15},
            ],
            row_count=3,
        )
        svg = generate_chart_from_result(
            result, VisualizationType.LINE_CHART, "추이",
        )
        assert "<polyline" in svg

    def test_none_type_returns_empty(self):
        result = SQLResult(columns=["a"], rows=[{"a": 1}], row_count=1)
        assert generate_chart_from_result(result, VisualizationType.NONE) == ""

    def test_empty_rows_returns_empty(self):
        result = SQLResult(columns=["a"], rows=[], row_count=0)
        assert generate_chart_from_result(
            result, VisualizationType.BAR_CHART,
        ) == ""

    def test_no_numeric_column_returns_empty(self):
        result = SQLResult(
            columns=["이름", "설명"],
            rows=[{"이름": "A", "설명": "B"}],
            row_count=1,
        )
        assert generate_chart_from_result(
            result, VisualizationType.BAR_CHART,
        ) == ""

    def test_numeric_only_uses_index_labels(self):
        """문자열 컬럼이 없으면 항목1, 항목2... 레이블 사용."""
        result = SQLResult(
            columns=["금액"],
            rows=[{"금액": 100}, {"금액": 200}, {"금액": 300}],
            row_count=3,
        )
        svg = generate_chart_from_result(
            result, VisualizationType.BAR_CHART, "숫자만",
        )
        assert "항목1" in svg


# ---------------------------------------------------------------------------
# SVG 보안 검증
# ---------------------------------------------------------------------------

class TestSVGSecurity:
    """생성된 SVG에 위험 요소가 없는지 검증."""

    @pytest.mark.parametrize("gen_func,args", [
        (generate_bar_chart, (["A", "B"], [10, 20], "T")),
        (generate_line_chart, (["A", "B"], [10, 20], "T")),
        (generate_pie_chart, (["A", "B"], [10, 20], "T")),
    ])
    def test_no_dangerous_elements(self, gen_func, args):
        svg = gen_func(*args)
        assert "<script" not in svg.lower()
        assert "onclick" not in svg.lower()
        assert "onerror" not in svg.lower()
        assert "javascript:" not in svg.lower()
        assert "<foreignObject" not in svg

    @pytest.mark.parametrize("gen_func,args", [
        (generate_bar_chart, (["A", "B"], [10, 20], "T")),
        (generate_line_chart, (["A", "B"], [10, 20], "T")),
        (generate_pie_chart, (["A", "B"], [10, 20], "T")),
    ])
    def test_uses_viewbox_not_fixed_size(self, gen_func, args):
        svg = gen_func(*args)
        assert 'viewBox="' in svg

    def test_html_escape_in_labels(self):
        """XSS 시도가 담긴 레이블이 이스케이프되는지 확인."""
        svg = generate_bar_chart(
            labels=["<script>alert(1)</script>", "정상"],
            values=[10, 20],
        )
        assert "<script>" not in svg
        assert "&lt;script&gt;" in svg
