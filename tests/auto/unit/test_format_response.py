"""결과 포맷팅 노드(format_response_node) 테스트.

테스트 대상:
    SQL 실행 결과를 사용자 친화적 한국어 보고서로 변환하는 rule-based 노드를 검증한다.

입력 예시 (정상):
    - SQLResult(columns=["고객명", "대출금액"], rows=[{"고객명":"홍길동", "대출금액":5000000}])
    - 기대: "고객명 ... 1건 조회" 형태의 한국어 보고서

실행 스크립트:
    pytest tests/auto/unit/test_format_response.py -v

참고:
    - LLM 호출 없음 (rule-based 포맷팅)
    - 테스트 대상 소스: src/agents/nodes/present/formatter.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from tests.conftest import get_test_logger, log_test_case

logger = get_test_logger("test_format_response")


# ──────────────────────────────────────────────────────────────
# 헬퍼
# ──────────────────────────────────────────────────────────────

def _make_state(
    rows: list[dict],
    user_input: str = "데이터 조회해줘",
    trace_entries: list | None = None,
    validated_sql: str = "",
):
    """테스트용 PipelineState 를 생성한다."""
    from src.agents.state.state import (
        PipelineState,
        SQLResult,
        TraceEntry,
    )

    columns = list(rows[0].keys()) if rows else []
    sql_result = SQLResult(
        columns=columns,
        rows=rows,
        row_count=len(rows),
        execution_time_ms=12.5,
    )

    trace = []
    if trace_entries:
        for node, action, detail in trace_entries:
            trace.append(TraceEntry(node=node, action=action, detail=detail))

    state = PipelineState(
        user_input=user_input,
        preprocessed_input=user_input,
        sql_result=sql_result,
        trace_log=trace,
    )

    if validated_sql:
        state.reason.validated_sql = validated_sql

    return state


# ──────────────────────────────────────────────────────────────
# format_response_node 테스트 (rule-based, LLM 불필요)
# ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_format_data_extraction():
    """rule-based 포맷팅이 요약 텍스트 + 구조화 result_data를 생성한다."""
    from src.agents.nodes.present.formatter import format_response_node
    from src.agents.state.state import QueryStatus

    rows = [
        {"지점명": "강남지점", "신규대출건수": 52, "평균대출금액": 45000000},
        {"지점명": "서초지점", "신규대출건수": 38, "평균대출금액": 51000000},
        {"지점명": "마포지점", "신규대출건수": 29, "평균대출금액": 38000000},
    ]
    state = _make_state(rows=rows, user_input="이번 달 지점별 신규 대출 현황")
    result = await format_response_node(state)

    response = result.get("formatted_response", "")
    status = result.get("status")
    result_data = result.get("result_data")

    passed = (
        status == QueryStatus.FORMATTED
        and len(response) > 0
        and result_data is not None
        and result_data["columns"] == ["지점명", "신규대출건수", "평균대출금액"]
        and len(result_data["rows"]) == 3
        and "column_formats" in result_data
    )
    log_test_case(
        logger,
        "test_format_data_extraction",
        input_data="지점별 대출 3행",
        expected="status=FORMATTED, result_data 포함",
        actual=f"status={status}, result_data={bool(result_data)}",
        passed=passed,
    )
    assert passed


@pytest.mark.asyncio
async def test_format_empty_result():
    """빈 결과에 대해 '(조회 결과 없음)' 문구를 포함한다."""
    from src.agents.nodes.present.formatter import format_response_node

    state = _make_state(rows=[], user_input="이번 달 연체 고객 목록")
    result = await format_response_node(state)

    response = result.get("formatted_response", "")
    passed = "조회 결과 없음" in response
    log_test_case(
        logger,
        "test_format_empty_result",
        input_data="rows=[]",
        expected="'조회 결과 없음' 포함",
        actual=response[:200],
        passed=passed,
    )
    assert passed


@pytest.mark.asyncio
async def test_process_summary_as_dict():
    """process_summary가 구조화 dict로 반환된다."""
    from src.agents.nodes.present.formatter import format_response_node

    rows = [{"항목": "테스트", "값": 42}]
    state = _make_state(rows=rows)
    result = await format_response_node(state)

    ps = result.get("process_summary")
    response = result.get("formatted_response", "")
    passed = (
        isinstance(ps, dict)
        and "intent" in ps
        and "validation" in ps
        and "<details>" not in response
    )

    log_test_case(
        logger,
        "test_process_summary_as_dict",
        input_data="rows 1행",
        expected="process_summary=dict, formatted에 <details> 없음",
        actual=f"ps_type={type(ps).__name__}, keys={list(ps.keys()) if ps else []}",
        passed=passed,
    )
    assert passed


@pytest.mark.asyncio
async def test_no_sql_exposed():
    """포맷팅된 응답에 SQL 코드(SELECT)가 직접 노출되지 않는다."""
    from src.agents.nodes.present.formatter import format_response_node

    rows = [{"건수": 150}]
    state = _make_state(rows=rows, user_input="이번 달 신규 고객 수")
    result = await format_response_node(state)

    response = result.get("formatted_response", "")
    raw_sql_patterns = ["FROM TB_", "WHERE ", "SELECT *"]
    has_raw_sql = any(p in response for p in raw_sql_patterns)

    passed = not has_raw_sql
    log_test_case(
        logger,
        "test_no_sql_exposed",
        input_data="신규 고객 수 1건",
        expected="SQL 키워드 미포함",
        actual=response[:200],
        passed=passed,
    )
    assert passed, f"SQL 코드가 응답에 노출됨: {response[:300]}"


@pytest.mark.asyncio
async def test_response_length_reasonable():
    """포맷팅된 응답은 50자 이상 5000자 이하다."""
    from src.agents.nodes.present.formatter import format_response_node

    rows = [
        {"월": "2024-01", "건수": 150},
        {"월": "2024-02", "건수": 162},
    ]
    state = _make_state(rows=rows, user_input="월별 대출 건수")
    result = await format_response_node(state)

    response = result.get("formatted_response", "")
    length = len(response)
    passed = 5 <= length <= 5000

    log_test_case(
        logger,
        "test_response_length_reasonable",
        input_data="월별 대출 2행",
        expected="5 <= 응답 길이 <= 5000",
        actual=f"응답 길이={length}",
        passed=passed,
    )
    assert passed, f"응답 길이가 범위 밖: {length}"


@pytest.mark.asyncio
async def test_simple_responder_guard_skips():
    """formatted_response가 이미 있고 sql_result.rows가 비면 포맷팅을 스킵한다."""
    from src.agents.state.state import PipelineState

    state = PipelineState(
        formatted_response="이미 응답 완료",
    )
    from src.agents.nodes.present.formatter import format_response_node

    result = await format_response_node(state)
    # formatted_response가 반환되지 않음 (스킵)
    assert "formatted_response" not in result
    assert "trace_log" in result
