"""rule-based 포맷팅 함수 단위 테스트.

테스트 대상:
    src/services/response_formatter.py 의 rule-based 포맷팅 함수들을 검증한다.
    - format_currency: 금액 한국어 단위 변환 (조/억/만/원)
    - format_rate: 비율 퍼센트 포맷팅
    - format_count: 건수 천단위 구분 포맷팅
    - detect_column_formats: SQL alias 기반 컬럼 타입 추론
    - format_report_table: 마크다운 테이블 생성 (셀 단위 포맷팅)
    - build_summary_line: 핵심 수치 1~2줄 요약
    - apply_code_mappings: 코드값 한글 변환 (fallback)

실행 스크립트:
    pytest tests/auto/unit/test_response_formatter_rules.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from src.services.response_formatter import (
    format_currency,
    format_rate,
    format_count,
    detect_column_formats,
    format_report_table,
    build_summary_line,
)
from tests.conftest import get_test_logger, log_test_case

logger = get_test_logger("test_response_formatter_rules")


# ══════════════════════════════════════════════════════════════
# format_currency
# ══════════════════════════════════════════════════════════════

class TestFormatCurrency:
    """금액 한국어 단위 변환 테스트."""

    def test_zero(self):
        assert format_currency(0) == "0원"

    def test_small_amount(self):
        assert format_currency(9999) == "9,999원"

    def test_man_unit(self):
        """1만 이상 1억 미만."""
        assert format_currency(10000) == "1만원"
        assert format_currency(50000) == "5만원"
        assert format_currency(12345678) == "1,234만원"

    def test_eok_unit(self):
        """1억 이상 1조 미만."""
        assert format_currency(100000000) == "1억원"
        assert format_currency(150000000) == "1억 5,000만원"
        assert format_currency(1234567890) == "12억 3,456만원"

    def test_jo_unit(self):
        """1조 이상."""
        assert format_currency(1000000000000) == "1조원"
        assert format_currency(1500000000000) == "1조 5,000억원"

    def test_negative(self):
        assert format_currency(-50000) == "-5만원"
        assert format_currency(-100000000) == "-1억원"

    def test_negative_zero_defense(self):
        """-0.4 같은 값이 round 후 0이 되면 '-0원'이 아닌 '0원'."""
        assert format_currency(-0.4) == "0원"

    def test_float_defense(self):
        """소수점 금액은 반올림."""
        assert format_currency(99999.7) == "10만원"


# ══════════════════════════════════════════════════════════════
# format_rate / format_count
# ══════════════════════════════════════════════════════════════

class TestFormatRateCount:
    """비율/건수 포맷팅 테스트."""

    def test_rate_normal(self):
        assert format_rate(3.14) == "3.1%"

    def test_rate_zero(self):
        assert format_rate(0.0) == "0.0%"

    def test_rate_negative(self):
        assert format_rate(-1.5) == "-1.5%"

    def test_count_normal(self):
        assert format_count(1234) == "1,234건"

    def test_count_zero(self):
        assert format_count(0) == "0건"

    def test_count_float(self):
        """float 건수는 int 변환."""
        assert format_count(100.0) == "100건"


# ══════════════════════════════════════════════════════════════
# detect_column_formats
# ══════════════════════════════════════════════════════════════

class TestDetectColumnFormats:
    """SQL alias 기반 컬럼 타입 추론 테스트."""

    def test_suffix_amt(self):
        """_AMT 접미사는 currency."""
        result = detect_column_formats(
            "SELECT A.LN_BAL_AMT AS 잔액 FROM TB_LOAN A"
        )
        assert result.get("잔액") == "currency"

    def test_suffix_rate(self):
        """_RT 접미사는 rate."""
        result = detect_column_formats(
            "SELECT A.OVDU_RT AS 연체율 FROM TB_LOAN A"
        )
        assert result.get("연체율") == "rate"

    def test_suffix_cnt(self):
        """_CNT 접미사는 count."""
        result = detect_column_formats(
            "SELECT A.LN_CNT AS 건수 FROM TB_LOAN A"
        )
        assert result.get("건수") == "count"

    def test_alias_fallback_currency(self):
        """원본 컬럼명 없으면 한글 alias로 추론."""
        result = detect_column_formats(
            "SELECT SUM(A.COL1) AS 금액합계 FROM TB_TEST A"
        )
        assert result.get("금액합계") == "currency"

    def test_unknown_column_is_text(self):
        """판별 불가 컬럼은 text."""
        result = detect_column_formats(
            "SELECT A.BRANCH_NM AS 지점명 FROM TB_BRANCH A"
        )
        assert result.get("지점명") == "text"

    def test_empty_sql(self):
        """빈 SQL은 빈 dict."""
        assert detect_column_formats("") == {}


# ══════════════════════════════════════════════════════════════
# format_report_table
# ══════════════════════════════════════════════════════════════

class TestFormatReportTable:
    """마크다운 테이블 생성 테스트."""

    def test_basic_table(self):
        columns = ["지점", "건수"]
        rows = [{"지점": "강남", "건수": 100}]
        result = format_report_table(columns, rows, {"건수": "count"})
        assert "| 지점 | 건수 |" in result
        assert "100건" in result

    def test_currency_formatting(self):
        columns = ["항목", "금액"]
        rows = [{"항목": "여신", "금액": 150000000}]
        result = format_report_table(columns, rows, {"금액": "currency"})
        assert "1억 5,000만원" in result

    def test_empty_rows(self):
        result = format_report_table(["col"], [], {})
        assert "조회 결과 없음" in result

    def test_empty_column_formats_raw_numbers(self):
        """column_formats={}이면 천단위 구분자만 적용 (LLM 프롬프트용)."""
        columns = ["금액"]
        rows = [{"금액": 150000000}]
        result = format_report_table(columns, rows, column_formats={})
        assert "150,000,000" in result
        assert "억" not in result

    def test_max_rows_truncation(self):
        columns = ["id"]
        rows = [{"id": i} for i in range(200)]
        result = format_report_table(columns, rows, {}, max_rows=50)
        assert "총 200건 중 상위 50건 표시" in result

    def test_total_count_shown(self):
        columns = ["id"]
        rows = [{"id": 1}, {"id": 2}]
        result = format_report_table(columns, rows, {})
        assert "총 2건" in result

    def test_none_value_empty_cell(self):
        columns = ["col"]
        rows = [{"col": None}]
        result = format_report_table(columns, rows, {})
        assert "|  |" in result


# ══════════════════════════════════════════════════════════════
# build_summary_line
# ══════════════════════════════════════════════════════════════

class TestBuildSummaryLine:
    """핵심 수치 요약 테스트."""

    def test_single_row_metric(self):
        result = build_summary_line(
            ["항목", "금액"], [{"항목": "총합", "금액": 500000000}],
            {"항목": "text", "금액": "currency"},
        )
        assert "금액" in result
        assert "5억원" in result

    def test_multi_row_top_value(self):
        """다건에서 가장 큰 값의 label 표시, '가장 큽니다' 사용."""
        rows = [
            {"지점": "강남", "건수": 100},
            {"지점": "서초", "건수": 200},
        ]
        result = build_summary_line(
            ["지점", "건수"], rows,
            {"지점": "text", "건수": "count"},
        )
        assert "서초" in result
        assert "가장 큽니다" in result

    def test_empty_rows(self):
        assert build_summary_line(["col"], [], {}) == ""

    def test_no_metric_column(self):
        """metric 컬럼이 없으면 건수만 표시."""
        result = build_summary_line(
            ["이름"], [{"이름": "홍길동"}, {"이름": "김철수"}],
            {"이름": "text"},
        )
        assert "2건" in result
