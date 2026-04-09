"""SQL 실행 노드(execute_sql_node) 테스트.

테스트 대상:
    검증 완료된 SQL을 DB에 실행하고 SQLResult를 반환하는 노드를 검증한다.
    보안 이중 방어(DML 차단)와 실행 결과 정확성을 모두 테스트한다.

    ┌─────────────────────────────────────────────────────────────────┐
    │  테스트 구간              테스트 내용              인프라 필요   │
    │  ──────────────────────── ──────────────────────── ────────── │
    │  보안 이중 방어           INSERT/DROP/DELETE/UPDATE 차단   X    │
    │  Dummy DB 실행           유효 SELECT → SQLResult 반환     X    │
    │  라이브 DB 실행           실제 PostgreSQL SELECT 실행      O    │
    └─────────────────────────────────────────────────────────────────┘

입력 예시 (정상):
    - validated_sql = "SELECT 1 AS val"
    - 기대: SQLResult(columns=["val"], rows=[{"val": 1}], row_count=1)
    - execution_time_ms > 0

결과 예시 (오류 케이스):
    - INSERT/DROP/DELETE/UPDATE → status=ERROR (보안 차단)
    - 구문 오류 SQL → status=ERROR

실행 스크립트:
    pytest tests/unit/test_execute_sql.py -v

    # 라이브 DB 테스트 포함
    TEST_LIVE_INFRA=true pytest tests/unit/test_execute_sql.py -v

참고:
    - Dummy 모드(기본): PostgreSQL 없이 내장 Dummy 커넥터로 실행
    - 라이브 모드: TEST_LIVE_INFRA=true 환경변수 필요
    - 테스트 대상 소스: src/agents/nodes/sql_executor.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import os

import pytest

from tests.conftest import get_test_logger, log_test_case

logger = get_test_logger("test_execute_sql")

_LIVE_INFRA_AVAILABLE = (
    os.getenv("TEST_LIVE_INFRA", "false").lower() == "true"
)

_SKIP_LIVE = pytest.mark.skipif(
    not _LIVE_INFRA_AVAILABLE,
    reason="라이브 PostgreSQL 이 없어 건너뜀. TEST_LIVE_INFRA=true 로 활성화.",
)


# ──────────────────────────────────────────────────────────────
# 헬퍼
# ──────────────────────────────────────────────────────────────

def _make_state(validated_sql: str):
    """테스트용 PipelineState 를 생성한다."""
    from src.agents.state.state import PipelineState, ReasoningState

    return PipelineState(
        user_input="테스트 질의",
        preprocessed_input="테스트 질의",
        reason=ReasoningState(validated_sql=validated_sql),
    )


# ──────────────────────────────────────────────────────────────
# 보안 이중 방어 테스트 (Dummy 모드, 인프라 불필요)
# ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unsafe_sql_blocked_insert():
    """INSERT 문은 이중 보안 검증에서 차단된다."""
    from src.agents.nodes.present.sql_executor import execute_sql_node
    from src.agents.state.state import QueryStatus

    state = _make_state("INSERT INTO TB_CUST_INFO VALUES (1, 'test')")
    result = await execute_sql_node(state)

    passed = result.get("status") == QueryStatus.ERROR
    log_test_case(
        logger,
        "test_unsafe_sql_blocked_insert",
        input_data="INSERT INTO TB_CUST_INFO VALUES (1, 'test')",
        expected="status=ERROR (보안 차단)",
        actual=f"status={result.get('status')}",
        passed=passed,
    )
    assert passed, f"INSERT 문이 차단되지 않음: {result}"


@pytest.mark.asyncio
async def test_unsafe_sql_blocked_drop():
    """DROP 문은 이중 보안 검증에서 차단된다."""
    from src.agents.nodes.present.sql_executor import execute_sql_node
    from src.agents.state.state import QueryStatus

    state = _make_state("DROP TABLE TB_CUST_INFO")
    result = await execute_sql_node(state)

    passed = result.get("status") == QueryStatus.ERROR
    log_test_case(
        logger,
        "test_unsafe_sql_blocked_drop",
        input_data="DROP TABLE TB_CUST_INFO",
        expected="status=ERROR (보안 차단)",
        actual=f"status={result.get('status')}",
        passed=passed,
    )
    assert passed, f"DROP 문이 차단되지 않음: {result}"


@pytest.mark.asyncio
async def test_unsafe_sql_blocked_delete():
    """DELETE 문은 이중 보안 검증에서 차단된다."""
    from src.agents.nodes.present.sql_executor import execute_sql_node
    from src.agents.state.state import QueryStatus

    state = _make_state("DELETE FROM TB_CUST_INFO WHERE 1=1")
    result = await execute_sql_node(state)

    passed = result.get("status") == QueryStatus.ERROR
    log_test_case(
        logger,
        "test_unsafe_sql_blocked_delete",
        input_data="DELETE FROM TB_CUST_INFO WHERE 1=1",
        expected="status=ERROR (보안 차단)",
        actual=f"status={result.get('status')}",
        passed=passed,
    )
    assert passed


@pytest.mark.asyncio
async def test_unsafe_sql_blocked_update():
    """UPDATE 문은 이중 보안 검증에서 차단된다."""
    from src.agents.nodes.present.sql_executor import execute_sql_node
    from src.agents.state.state import QueryStatus

    state = _make_state("UPDATE TB_CUST_INFO SET NAME='test' WHERE CUST_NO='001'")
    result = await execute_sql_node(state)

    passed = result.get("status") == QueryStatus.ERROR
    log_test_case(
        logger,
        "test_unsafe_sql_blocked_update",
        input_data="UPDATE TB_CUST_INFO SET NAME='test'",
        expected="status=ERROR",
        actual=f"status={result.get('status')}",
        passed=passed,
    )
    assert passed


# ──────────────────────────────────────────────────────────────
# Dummy DB 실행 테스트
# ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_execute_valid_sql_dummy():
    """Dummy 모드에서 유효한 SELECT 실행이 SQLResult 를 반환한다."""
    from src.agents.nodes.present.sql_executor import execute_sql_node
    from src.agents.state.state import QueryStatus, SQLResult

    # Dummy 커넥터가 처리할 수 있는 범용 SELECT
    state = _make_state("SELECT LOAN_NO, LOAN_AMT FROM TB_LOAN_INFO LIMIT 5")
    result = await execute_sql_node(state)

    sql_result = result.get("sql_result")
    status = result.get("status")

    # Dummy 모드에서는 성공하거나 예외 오류(테이블 없음)가 올 수 있음
    # 어느 경우든 sql_result 가 SQLResult 타입이어야 함
    passed = isinstance(sql_result, SQLResult)
    log_test_case(
        logger,
        "test_execute_valid_sql_dummy",
        input_data="SELECT LOAN_NO, LOAN_AMT FROM TB_LOAN_INFO LIMIT 5",
        expected="SQLResult 타입 반환",
        actual=f"타입={type(sql_result).__name__}, status={status}",
        passed=passed,
    )
    assert passed, f"sql_result 가 SQLResult 타입이 아님: {type(sql_result)}"


@pytest.mark.asyncio
async def test_result_has_columns_and_rows_on_success():
    """성공적으로 실행된 결과는 columns 와 rows 필드를 가진다."""
    from src.agents.nodes.present.sql_executor import execute_sql_node
    from src.agents.state.state import QueryStatus, SQLResult

    state = _make_state("SELECT LOAN_NO FROM TB_LOAN_INFO LIMIT 1")
    result = await execute_sql_node(state)

    sql_result: SQLResult = result.get("sql_result")
    status = result.get("status")

    if status == QueryStatus.EXECUTED:
        passed = (
            isinstance(sql_result.columns, list)
            and isinstance(sql_result.rows, list)
        )
        log_test_case(
            logger,
            "test_result_has_columns_and_rows_on_success",
            input_data="SELECT LOAN_NO FROM TB_LOAN_INFO LIMIT 1",
            expected="columns: list, rows: list",
            actual=f"columns={sql_result.columns}, row_count={sql_result.row_count}",
            passed=passed,
        )
        assert passed
    else:
        # Dummy DB 에 TB_LOAN_INFO 가 없으면 ERROR 도 허용
        pytest.skip(f"Dummy DB 에서 실행 오류 (status={status}) — 라이브 DB 필요")


@pytest.mark.asyncio
async def test_execution_time_recorded():
    """실행 성공 시 execution_time_ms 가 0 보다 크다."""
    from src.agents.nodes.present.sql_executor import execute_sql_node
    from src.agents.state.state import QueryStatus

    state = _make_state("SELECT DEPOSIT_NO FROM TB_DEPOSIT_INFO LIMIT 1")
    result = await execute_sql_node(state)

    if result.get("status") == QueryStatus.EXECUTED:
        sql_result = result["sql_result"]
        passed = sql_result.execution_time_ms > 0
        log_test_case(
            logger,
            "test_execution_time_recorded",
            input_data="SELECT DEPOSIT_NO FROM TB_DEPOSIT_INFO LIMIT 1",
            expected="execution_time_ms > 0",
            actual=f"execution_time_ms={sql_result.execution_time_ms}",
            passed=passed,
        )
        assert passed
    else:
        pytest.skip("Dummy DB 실행 오류 — 라이브 DB 필요")


@pytest.mark.asyncio
async def test_invalid_sql_returns_error():
    """구문 오류 SQL 은 ERROR 상태를 반환한다."""
    from src.agents.nodes.present.sql_executor import execute_sql_node
    from src.agents.state.state import QueryStatus

    # 보안 검증은 통과하지만 DB 에서 구문 오류가 발생할 SQL
    state = _make_state("SELECT FROM WHERE INVALID SYNTAX !!!!")
    result = await execute_sql_node(state)

    status = result.get("status")
    # 보안 오류 또는 실행 오류 모두 ERROR
    passed = status == QueryStatus.ERROR
    log_test_case(
        logger,
        "test_invalid_sql_returns_error",
        input_data="SELECT FROM WHERE INVALID SYNTAX !!!!",
        expected="status=ERROR",
        actual=f"status={status}",
        passed=passed,
    )
    assert passed, f"잘못된 SQL 이 ERROR 를 반환하지 않음: status={status}"


# ──────────────────────────────────────────────────────────────
# 라이브 DB 테스트
# ──────────────────────────────────────────────────────────────

@_SKIP_LIVE
@pytest.mark.asyncio
async def test_execute_select_1_live():
    """라이브: 'SELECT 1 AS val' 이 1행을 반환한다."""
    from src.agents.nodes.present.sql_executor import execute_sql_node
    from src.agents.state.state import QueryStatus

    state = _make_state("SELECT 1 AS val")
    result = await execute_sql_node(state)

    sql_result = result.get("sql_result")
    status = result.get("status")

    passed = (
        status == QueryStatus.EXECUTED
        and sql_result.row_count == 1
        and "val" in sql_result.columns
    )
    log_test_case(
        logger,
        "test_execute_select_1_live",
        input_data="SELECT 1 AS val",
        expected="row_count=1, columns=['val']",
        actual=f"status={status}, row_count={sql_result.row_count if sql_result else 'N/A'}",
        passed=passed,
    )
    assert passed


@_SKIP_LIVE
@pytest.mark.asyncio
async def test_empty_result_live():
    """라이브: 결과가 없는 쿼리는 row_count=0 인 SQLResult 를 반환한다."""
    from src.agents.nodes.present.sql_executor import execute_sql_node
    from src.agents.state.state import QueryStatus

    state = _make_state(
        "SELECT 1 AS val WHERE 1=0"
    )
    result = await execute_sql_node(state)

    sql_result = result.get("sql_result")
    status = result.get("status")

    passed = (
        status == QueryStatus.EXECUTED
        and sql_result.row_count == 0
    )
    log_test_case(
        logger,
        "test_empty_result_live",
        input_data="SELECT 1 AS val WHERE 1=0",
        expected="row_count=0",
        actual=f"status={status}, row_count={sql_result.row_count if sql_result else 'N/A'}",
        passed=passed,
    )
    assert passed


@_SKIP_LIVE
@pytest.mark.asyncio
async def test_row_count_limit_live():
    """라이브: 결과가 max_query_rows 를 초과하면 잘린다."""
    from src.agents.nodes.present.sql_executor import execute_sql_node
    from src.agents.state.state import QueryStatus
    from src.config import settings

    # generate_series 로 max_query_rows + 10 행 생성
    limit = settings.max_query_rows
    state = _make_state(
        f"SELECT generate_series(1, {limit + 10}) AS n"
    )
    result = await execute_sql_node(state)

    sql_result = result.get("sql_result")
    status = result.get("status")

    passed = (
        status == QueryStatus.EXECUTED
        and sql_result.row_count <= limit
    )
    log_test_case(
        logger,
        "test_row_count_limit_live",
        input_data=f"generate_series(1, {limit + 10})",
        expected=f"row_count <= {limit}",
        actual=f"row_count={sql_result.row_count if sql_result else 'N/A'}",
        passed=passed,
    )
    assert passed
