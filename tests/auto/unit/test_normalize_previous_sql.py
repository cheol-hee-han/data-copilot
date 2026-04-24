"""normalize_previous_sql 유틸 단위 테스트 (Phase 3, §14.3.6).

테스트 대상:
    - None / "" / 공백만 / 정상 SQL 4 케이스의 폴백 동작
    - SQL 본문과 설명 양쪽에 동일 함수가 적용되는지(중복 금지 단일 함수)

테스트 대상 소스:
    src/agents/utils/handoff.py::normalize_previous_sql
"""

from __future__ import annotations

import pytest

from src.agents.utils.handoff import (
    PREVIOUS_SQL_EMPTY_PLACEHOLDER,
    normalize_previous_sql,
)


class TestNormalizePreviousSql:
    """`normalize_previous_sql` 공용 정규화 규칙 검증."""

    def test_none_returns_placeholder(self) -> None:
        assert normalize_previous_sql(None) == PREVIOUS_SQL_EMPTY_PLACEHOLDER

    def test_empty_string_returns_placeholder(self) -> None:
        assert normalize_previous_sql("") == PREVIOUS_SQL_EMPTY_PLACEHOLDER

    def test_whitespace_only_returns_placeholder(self) -> None:
        assert normalize_previous_sql("   \n\t  ") == PREVIOUS_SQL_EMPTY_PLACEHOLDER

    def test_valid_sql_is_stripped(self) -> None:
        assert normalize_previous_sql("  SELECT 1 FROM TB  ") == "SELECT 1 FROM TB"

    @pytest.mark.parametrize(
        "value",
        [
            "설명 본문",
            "  다중 줄 설명\n두 번째 줄  ",
        ],
    )
    def test_explanation_follows_same_rule(self, value: str) -> None:
        """SQL 본문·설명 모두 동일 함수로 정규화(중복 구현 금지)."""
        assert normalize_previous_sql(value) == value.strip()

    def test_placeholder_value_is_fallback_constant(self) -> None:
        assert PREVIOUS_SQL_EMPTY_PLACEHOLDER == "(없음)"
