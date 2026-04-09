"""_qualify_table_in_input 단위 테스트.

DB 직접 조회 도구 호출 시 스키마 미지정 테이블명을
explored_tables에서 자동 보충하는 로직을 검증한다.
"""

from __future__ import annotations

from src.agents.nodes.reason.context_retriever import (
    _qualify_table_in_input,
)
from src.agents.state.state import TableMeta


def _table(name: str, schema: str = "") -> TableMeta:
    return TableMeta(table_name=name, schema_name=schema)


class TestQualifyTableInInput:

    def test_column_profile_adds_schema(self):
        tables = [_table("TB_ADW_DEP201P", "ADWOWN")]
        result = _qualify_table_in_input(
            "get_column_profile", "TB_ADW_DEP201P,OPEN_DT", tables,
        )
        assert result == "ADWOWN.TB_ADW_DEP201P,OPEN_DT"

    def test_date_distribution_adds_schema(self):
        tables = [_table("TB_ADW_DEP201P", "ADWOWN")]
        result = _qualify_table_in_input(
            "get_date_distribution", "TB_ADW_DEP201P,OPEN_DT", tables,
        )
        assert result == "ADWOWN.TB_ADW_DEP201P,OPEN_DT"

    def test_sample_rows_adds_schema(self):
        tables = [_table("TB_ADW_COM001M", "ADWOWN")]
        result = _qualify_table_in_input(
            "get_sample_rows", "TB_ADW_COM001M", tables,
        )
        assert result == "ADWOWN.TB_ADW_COM001M"

    def test_get_column_values_adds_schema(self):
        tables = [_table("TB_ADW_DEP201P", "ADWOWN")]
        result = _qualify_table_in_input(
            "get_column_values", "TB_ADW_DEP201P,ACT_STCD,01", tables,
        )
        assert result == "ADWOWN.TB_ADW_DEP201P,ACT_STCD,01"

    def test_already_qualified_no_change(self):
        """이미 스키마가 있으면 그대로 반환."""
        tables = [_table("TB_ADW_DEP201P", "ADWOWN")]
        result = _qualify_table_in_input(
            "get_column_profile", "ADWOWN.TB_ADW_DEP201P,OPEN_DT", tables,
        )
        assert result == "ADWOWN.TB_ADW_DEP201P,OPEN_DT"

    def test_table_not_in_explored(self):
        """explored_tables에 없는 테이블은 원본 그대로."""
        result = _qualify_table_in_input(
            "get_column_profile", "UNKNOWN_TABLE,COL1", [],
        )
        assert result == "UNKNOWN_TABLE,COL1"

    def test_non_db_tool_no_change(self):
        """DB 직접 조회가 아닌 도구는 변경 없음."""
        tables = [_table("TB_ADW_DEP201P", "ADWOWN")]
        result = _qualify_table_in_input(
            "search_table_meta", "TB_ADW_DEP201P", tables,
        )
        assert result == "TB_ADW_DEP201P"

    def test_table_without_schema_in_meta(self):
        """메타에 schema_name이 빈 경우 원본 그대로."""
        tables = [_table("TB_LOCAL", "")]
        result = _qualify_table_in_input(
            "get_column_profile", "TB_LOCAL,COL1", tables,
        )
        assert result == "TB_LOCAL,COL1"
