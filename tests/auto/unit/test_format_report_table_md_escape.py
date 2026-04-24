"""format_report_table 의 '##' 프롬프트 인젝션 방어 테스트.

테스트 대상:
    [src/services/response_formatter.py :: format_report_table]
    - 셀 값이 '##' 로 시작하면 ZWSP(U+200B) 가 앞에 삽입되어야 한다.
    - 셀 중간 '\\n##' 도 동일하게 이스케이프된다.
"""

from __future__ import annotations

from src.services.response_formatter import format_report_table


def test_cell_starting_with_hash_gets_zwsp_prefix() -> None:
    out = format_report_table(
        columns=["제목"],
        rows=[{"제목": "## 핵심 요약"}],
        column_formats={},
    )
    assert "\u200b## 핵심 요약" in out
    # ZWSP 없는 원본 헤딩은 표에 포함되지 않아야 한다.
    assert "| ## 핵심 요약 |" not in out


def test_cell_with_inline_newline_hash_is_escaped() -> None:
    out = format_report_table(
        columns=["내용"],
        rows=[{"내용": "line1\n## fake"}],
        column_formats={},
    )
    assert "\n\u200b## fake" in out


def test_benign_cell_unchanged() -> None:
    out = format_report_table(
        columns=["지점"],
        rows=[{"지점": "강남"}],
        column_formats={},
    )
    assert "\u200b" not in out
