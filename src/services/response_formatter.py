"""SQL 실행 결과를 IT 비전문가 대상 보고서 형태로 포맷팅하는 서비스.

작성자: 한철희 / 최종수정: 2026-04-07

은행 일반 직원이 SQL이나 DB 개념을 몰라도 결과를 이해할 수 있도록,
rule-based 로직으로 조회 결과를 자연어 보고서로 변환한다.
금액은 만원/억원 단위, 날짜는 "2024년 3월" 형태, 비율은 % 등
사용자 친화적 포맷으로 재구성하며, SQL 자체는 노출하지 않는다.

핵심 함수:
    - detect_column_formats: SQL SELECT alias에서 포맷 타입을 추론
    - format_report_table: dict 행 목록을 마크다운 테이블로 변환
      (column_formats 지정 시 한국어 단위 변환, 빈 dict면 천단위 구분자만 적용)
    - build_summary_line: 핵심 수치를 1~2줄로 요약
    - apply_code_mappings: 결과 행의 코드값을 한글 명칭으로 변환 (fallback)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.utils.logger import get_logger

if TYPE_CHECKING:
    from src.agents.state.state import CodeMeta

logger = get_logger(__name__)

_CURRENCY_SUFFIXES = ("_AMT", "_BAL", "_PRIN", "_INT_AMT", "_TAMT", "_QTY_AMT")
_RATE_SUFFIXES = ("_RT", "_RATE", "_RTO", "_RATIO")
_COUNT_SUFFIXES = ("_CNT", "_NUM", "_QTY")





# ── 셀 단위 포맷팅 ──────────────────────────────────────

def format_currency(value: int | float) -> str:
    """금액을 한국어 단위로 변환한다."""
    abs_val = round(abs(value))  # 소수점 방어
    sign = "-" if value < 0 and abs_val > 0 else ""
    if abs_val >= 1_000_000_000_000:
        jo = abs_val // 1_000_000_000_000
        eok = (abs_val % 1_000_000_000_000) // 100_000_000
        return f"{sign}{jo}조 {eok:,}억원" if eok else f"{sign}{jo}조원"
    if abs_val >= 100_000_000:
        eok = abs_val // 100_000_000
        man = (abs_val % 100_000_000) // 10_000
        return f"{sign}{eok:,}억 {man:,}만원" if man else f"{sign}{eok:,}억원"
    if abs_val >= 10_000:
        man = abs_val // 10_000
        return f"{sign}{man:,}만원"
    return f"{sign}{abs_val:,}원"


def format_rate(value: float) -> str:
    """비율을 퍼센트로 포맷팅한다."""
    return f"{value:.1f}%"


def format_count(value: int | float) -> str:
    """건수를 천 단위 구분자 + '건'으로 포맷팅한다."""
    return f"{int(value):,}건"


def _format_value(value: Any, fmt: str) -> str:
    """포맷 타입에 따라 값을 포맷팅한다."""
    if not isinstance(value, (int, float)):
        return str(value)
    if fmt == "currency":
        return format_currency(value)
    if fmt == "rate":
        return format_rate(value)
    if fmt == "count":
        return format_count(value)
    # text 또는 미판별 숫자: 정수값 float은 정수로 표시
    if isinstance(value, float):
        return f"{int(value):,}" if value == int(value) else f"{value:,.2f}"
    return f"{value:,}"


# ── 컬럼 타입 판별 ──────────────────────────────────────

def _infer_from_column(col: str) -> str:
    """원본 컬럼명의 접미사로 포맷 타입을 추론한다."""
    upper = col.upper()
    if any(upper.endswith(s) for s in _CURRENCY_SUFFIXES):
        return "currency"
    if any(upper.endswith(s) for s in _RATE_SUFFIXES):
        return "rate"
    if any(upper.endswith(s) for s in _COUNT_SUFFIXES):
        return "count"
    return "text"


def _infer_from_alias(alias: str) -> str:
    """한글 alias 키워드로 포맷 타입을 추론한다 (폴백)."""
    if any(k in alias for k in ("건수", "수량", "횟수", "인원")):
        return "count"
    if any(k in alias for k in ("금액", "잔액", "합계", "원금", "이자", "실적")):
        return "currency"
    if any(k in alias for k in ("율", "비율", "비중")):
        return "rate"
    return "text"


def detect_column_formats(
    sql: str,
    dialect: str | None = None,
) -> dict[str, str]:
    """SQL의 SELECT alias에서 포맷 타입을 추론한다.

    Returns:
        {한글alias: "currency"|"rate"|"count"|"text"}
    """
    from src.utils.sqlglot_analyzer import extract_select_alias_map

    if not sql:
        return {}
    alias_map = extract_select_alias_map(sql, dialect)
    formats: dict[str, str] = {}
    for alias, orig_col in alias_map.items():
        if orig_col is None:
            formats[alias] = _infer_from_alias(alias)
        else:
            formats[alias] = _infer_from_column(orig_col)
    return formats


# ── 보고서 테이블 포맷팅 ─────────────────────────────────

def format_report_table(
    columns: list[str],
    rows: list[dict[str, Any]],
    column_formats: dict[str, str],
    max_rows: int = 100,
) -> str:
    """dict 행 목록을 포맷팅된 마크다운 테이블로 변환한다.

    column_formats의 타입에 따라 셀 값을 한국어 단위로 변환한다.
    판별 불가 숫자는 천 단위 구분자만 적용한다.
    """
    if not columns or not rows:
        return "(조회 결과 없음)"

    def _fmt_cell(col: str, value: Any) -> str:
        if value is None:
            return ""
        return _format_value(value, column_formats.get(col, "text"))

    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join("---" for _ in columns) + " |"
    body_lines: list[str] = []
    for row in rows[:max_rows]:
        cells = [_fmt_cell(col, row.get(col, "")) for col in columns]
        body_lines.append("| " + " | ".join(cells) + " |")

    table = "\n".join([header, separator, *body_lines])
    total = len(rows)
    if total > max_rows:
        table += f"\n\n(총 {total:,}건 중 상위 {max_rows}건 표시)"
    else:
        table += f"\n\n(총 {total:,}건)"
    return table


def build_summary_line(
    columns: list[str],
    rows: list[dict[str, Any]],
    column_formats: dict[str, str],
) -> str:
    """핵심 수치를 1~2줄로 요약한다."""
    row_count = len(rows)
    if row_count == 0:
        return ""

    metric_col = next(
        (c for c in columns if column_formats.get(c) in ("currency", "count")),
        None,
    )
    label_col = next(
        (c for c in columns if column_formats.get(c) == "text"),
        None,
    )

    if row_count == 1 and metric_col:
        val = rows[0].get(metric_col)
        if val is not None:
            formatted = _format_value(val, column_formats.get(metric_col, "text"))
            return f"{metric_col}은(는) {formatted}입니다."

    if row_count > 1 and metric_col:
        values = [
            (r, r.get(metric_col, 0))
            for r in rows
            if r.get(metric_col) is not None
        ]
        if values:
            top_row, top_val = max(values, key=lambda x: x[1])
            formatted = _format_value(top_val, column_formats.get(metric_col, "text"))
            label = top_row.get(label_col, "") if label_col else ""
            if label:
                return (
                    f"총 {row_count:,}건 조회되었으며, "
                    f"{label}이(가) {formatted}로 가장 큽니다."
                )

    return f"총 {row_count:,}건이 조회되었습니다."


def apply_code_mappings(
    rows: list[dict[str, Any]],
    code_map: dict[str, CodeMeta],
    sql: str,
    dialect: str | None = None,
) -> list[dict[str, Any]]:
    """결과 행의 코드값을 한글 명칭으로 변환한다 (fallback).

    SQL Generator가 명칭 컬럼을 함께 포함하도록 지시하고 있으므로
    이 함수는 명칭 컬럼 누락 시의 안전장치이다.
    """
    if not code_map or not sql:
        return rows

    from src.utils.sqlglot_analyzer import extract_select_alias_map

    alias_map = extract_select_alias_map(sql, dialect)
    col_to_codes: dict[str, dict[str, str]] = {}
    for alias, orig in alias_map.items():
        if orig and orig in code_map and code_map[orig].codes:
            col_to_codes[alias] = code_map[orig].codes

    if not col_to_codes:
        return rows

    converted = []
    for row in rows:
        new_row = dict(row)
        for col, codes in col_to_codes.items():
            if col in new_row and str(new_row[col]) in codes:
                new_row[col] = codes[str(new_row[col])]
        converted.append(new_row)
    return converted
