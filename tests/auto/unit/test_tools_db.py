"""tools.py DB 직접 조회 함수 회귀 테스트.

수정 이력:
    hasattr(result, "rows") 버그(항상 빈 결과 반환) →
    isinstance(result, list) 로 수정.
    이 파일은 해당 수정이 회귀하지 않도록 검증한다.

대상 함수:
    - get_sample_rows
    - get_column_values
    - get_column_profile
    - get_date_distribution

실행:
    pytest tests/auto/unit/test_tools_db.py -v
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.nodes.reason.tools import (
    get_column_profile,
    get_column_values,
    get_date_distribution,
    get_sample_rows,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 공통 헬퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _make_mock_mgr(rows: list) -> MagicMock:
    """execute_query 가 rows 를 반환하는 mock ConnectorManager."""
    mock_db = MagicMock()
    mock_db.dialect = "postgres"
    mock_db.execute_query = AsyncMock(return_value=rows)

    mock_mgr = MagicMock()
    mock_mgr.get_query_db.return_value = mock_db
    return mock_mgr


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# get_sample_rows
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestGetSampleRows:
    """get_sample_rows 회귀 테스트."""

    @pytest.mark.asyncio
    async def test_exception_propagates(self, monkeypatch):
        """execute_query 예외는 호출자로 전파된다.

        get_sample_rows는 예외를 catch하지 않고 전파한다.
        복구는 상위(_run_step / context_explorer)에서 처리한다.
        """
        mock_mgr = _make_mock_mgr([])
        mock_mgr.get_query_db.return_value.execute_query = (
            AsyncMock(side_effect=RuntimeError("timeout"))
        )
        monkeypatch.setattr(
            "src.agents.nodes.reason.tools"
            ".get_connector_manager",
            lambda: mock_mgr,
        )
        with pytest.raises(RuntimeError, match="timeout"):
            await get_sample_rows("SAMPLE_TABLE")

    @pytest.mark.asyncio
    async def test_invalid_ident_returns_empty(self):
        """식별자 검증 실패 시 빈 리스트를 반환한다."""
        result = await get_sample_rows("DROP TABLE;")
        assert result == []

    @pytest.mark.asyncio
    async def test_normal_return(self, monkeypatch):
        """execute_query 가 행을 반환하면 그 리스트를 그대로 반환한다.

        hasattr(result, "rows") 버그 회귀 방지:
        list 결과를 올바르게 통과시키는지 검증.
        """
        rows = [{"COL_A": "1", "COL_B": "2"}]
        mock_mgr = _make_mock_mgr(rows)

        monkeypatch.setattr(
            "src.agents.nodes.reason.tools.get_connector_manager",
            lambda: mock_mgr,
        )

        result = await get_sample_rows("SAMPLE_TABLE")

        assert result == rows

    @pytest.mark.asyncio
    async def test_empty_return(self, monkeypatch):
        """execute_query 가 빈 리스트를 반환하면 [] 를 반환한다."""
        mock_mgr = _make_mock_mgr([])

        monkeypatch.setattr(
            "src.agents.nodes.reason.tools.get_connector_manager",
            lambda: mock_mgr,
        )

        result = await get_sample_rows("SAMPLE_TABLE")

        assert result == []


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# get_column_values
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestGetColumnValues:
    """get_column_values 회귀 테스트."""

    @pytest.mark.asyncio
    async def test_normal_return(self, monkeypatch):
        """execute_query 가 코드 컬럼 행을 반환하면 값 목록을 추출한다."""
        rows = [{"LN_DCD": "01"}, {"LN_DCD": "02"}]
        mock_mgr = _make_mock_mgr(rows)

        monkeypatch.setattr(
            "src.agents.nodes.reason.tools.get_connector_manager",
            lambda: mock_mgr,
        )

        result = await get_column_values(
            table_name="LOAN_TABLE",
            column_name="LN_DCD",
            keyword="0",
        )

        assert result == ["01", "02"]

    @pytest.mark.asyncio
    async def test_empty_return(self, monkeypatch):
        """execute_query 가 빈 리스트를 반환하면 [] 를 반환한다."""
        mock_mgr = _make_mock_mgr([])

        monkeypatch.setattr(
            "src.agents.nodes.reason.tools.get_connector_manager",
            lambda: mock_mgr,
        )

        result = await get_column_values(
            table_name="LOAN_TABLE",
            column_name="LN_DCD",
            keyword="없는값",
        )

        assert result == []

    @pytest.mark.asyncio
    async def test_exception_propagates(self, monkeypatch):
        """execute_query 예외는 호출자로 전파된다."""
        mock_mgr = _make_mock_mgr([])
        mock_mgr.get_query_db.return_value.execute_query = (
            AsyncMock(side_effect=RuntimeError("timeout"))
        )
        monkeypatch.setattr(
            "src.agents.nodes.reason.tools"
            ".get_connector_manager",
            lambda: mock_mgr,
        )
        with pytest.raises(RuntimeError, match="timeout"):
            await get_column_values(
                table_name="LOAN_TABLE",
                column_name="LN_DCD",
                keyword="0",
            )

    @pytest.mark.asyncio
    async def test_invalid_table_returns_empty(self):
        """테이블명 식별자 검증 실패 시 빈 리스트를 반환한다."""
        result = await get_column_values(
            table_name="DROP TABLE;",
            column_name="LN_DCD",
            keyword="0",
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_invalid_column_returns_empty(self):
        """컬럼명 식별자 검증 실패 시 빈 리스트를 반환한다."""
        result = await get_column_values(
            table_name="LOAN_TABLE",
            column_name="1; DROP",
            keyword="0",
        )
        assert result == []


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# get_column_profile
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestGetColumnProfile:
    """get_column_profile 회귀 테스트."""

    @pytest.mark.asyncio
    async def test_normal_return(self, monkeypatch):
        """execute_query 가 통계 행을 반환하면 가공된 dict 를 반환한다.

        null_count = total_rows - non_null_count 파생 필드,
        null_rate = null_count / total_rows 파생 필드 검증.
        """
        rows = [{
            "total_rows": 100,
            "non_null_count": 90,
            "distinct_count": 5,
            "min_val": "A",
            "max_val": "Z",
        }]
        mock_mgr = _make_mock_mgr(rows)

        monkeypatch.setattr(
            "src.agents.nodes.reason.tools.get_connector_manager",
            lambda: mock_mgr,
        )

        result = await get_column_profile(
            table_name="ACCT_TABLE",
            column_name="STAT_CD",
        )

        assert result["total_rows"] == 100
        assert result["non_null_count"] == 90
        assert result["null_count"] == 10
        assert result["null_rate"] == pytest.approx(0.1, abs=1e-6)
        assert result["distinct_count"] == 5
        assert result["min_val"] == "A"
        assert result["max_val"] == "Z"

    @pytest.mark.asyncio
    async def test_empty_return(self, monkeypatch):
        """execute_query 가 빈 리스트를 반환하면 {} 를 반환한다."""
        mock_mgr = _make_mock_mgr([])

        monkeypatch.setattr(
            "src.agents.nodes.reason.tools.get_connector_manager",
            lambda: mock_mgr,
        )

        result = await get_column_profile(
            table_name="ACCT_TABLE",
            column_name="STAT_CD",
        )

        assert result == {}

    @pytest.mark.asyncio
    async def test_exception_propagates(self, monkeypatch):
        """execute_query 예외는 호출자로 전파된다."""
        mock_mgr = _make_mock_mgr([])
        mock_mgr.get_query_db.return_value.execute_query = (
            AsyncMock(side_effect=RuntimeError("timeout"))
        )
        monkeypatch.setattr(
            "src.agents.nodes.reason.tools"
            ".get_connector_manager",
            lambda: mock_mgr,
        )
        with pytest.raises(RuntimeError, match="timeout"):
            await get_column_profile(
                table_name="ACCT_TABLE",
                column_name="STAT_CD",
            )

    @pytest.mark.asyncio
    async def test_invalid_table_returns_empty(self):
        """테이블명 식별자 검증 실패 시 빈 dict를 반환한다."""
        result = await get_column_profile(
            table_name="DROP TABLE;",
            column_name="STAT_CD",
        )
        assert result == {}

    @pytest.mark.asyncio
    async def test_invalid_column_returns_empty(self):
        """컬럼명 식별자 검증 실패 시 빈 dict를 반환한다."""
        result = await get_column_profile(
            table_name="ACCT_TABLE",
            column_name="1; DROP",
        )
        assert result == {}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# get_date_distribution
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestGetDateDistribution:
    """get_date_distribution 회귀 테스트."""

    @pytest.mark.asyncio
    async def test_normal_return(self, monkeypatch):
        """execute_query 가 날짜 행을 반환하면 날짜 문자열 목록을 반환한다."""
        rows = [{"BAL_DT": "20250101"}, {"BAL_DT": "20250201"}]
        mock_mgr = _make_mock_mgr(rows)

        monkeypatch.setattr(
            "src.agents.nodes.reason.tools.get_connector_manager",
            lambda: mock_mgr,
        )

        result = await get_date_distribution(
            table_name="BAL_TABLE",
            date_column="BAL_DT",
        )

        assert result == ["20250101", "20250201"]

    @pytest.mark.asyncio
    async def test_empty_return(self, monkeypatch):
        """execute_query 가 빈 리스트를 반환하면 [] 를 반환한다."""
        mock_mgr = _make_mock_mgr([])

        monkeypatch.setattr(
            "src.agents.nodes.reason.tools.get_connector_manager",
            lambda: mock_mgr,
        )

        result = await get_date_distribution(
            table_name="BAL_TABLE",
            date_column="BAL_DT",
        )

        assert result == []

    @pytest.mark.asyncio
    async def test_exception_propagates(self, monkeypatch):
        """execute_query 예외는 호출자로 전파된다."""
        mock_mgr = _make_mock_mgr([])
        mock_mgr.get_query_db.return_value.execute_query = (
            AsyncMock(side_effect=RuntimeError("timeout"))
        )
        monkeypatch.setattr(
            "src.agents.nodes.reason.tools"
            ".get_connector_manager",
            lambda: mock_mgr,
        )
        with pytest.raises(RuntimeError, match="timeout"):
            await get_date_distribution(
                table_name="BAL_TABLE",
                date_column="BAL_DT",
            )

    @pytest.mark.asyncio
    async def test_invalid_table_returns_empty(self):
        """테이블명 식별자 검증 실패 시 빈 리스트를 반환한다."""
        result = await get_date_distribution(
            table_name="DROP TABLE;",
            date_column="BAL_DT",
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_invalid_column_returns_empty(self):
        """날짜컬럼명 식별자 검증 실패 시 빈 리스트를 반환한다."""
        result = await get_date_distribution(
            table_name="BAL_TABLE",
            date_column="1; DROP",
        )
        assert result == []
