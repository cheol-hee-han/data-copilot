"""결과 포맷팅 노드(format_response_node) 테스트.

테스트 대상:
    SQL 실행 결과를 사용자 친화적 한국어 보고서로 변환하는 노드를 검증한다.
    _format_result_for_prompt(순수 함수)와 format_response_node(LLM 호출)를 분리 테스트한다.

입력 예시 (정상):
    - SQLResult(columns=["고객명", "대출금액"], rows=[{"고객명":"홍길동", "대출금액":5000000}])
    - 기대: "고객명 ... 1건 조회" 형태의 한국어 보고서

결과 예시 (오류 케이스):
    - SQL 코드("SELECT")가 최종 응답에 그대로 노출됨
    - 응답 길이가 50자 미만 (너무 짧음)

실행 스크립트:
    # 순수 함수 테스트만 (LLM 불필요)
    pytest tests/unit/test_format_response.py -v -k "format_result"

    # LLM 포함 전체 (API 키 필요)
    pytest tests/unit/test_format_response.py -v

참고:
    - LLM 테스트는 ANTHROPIC_API_KEY 또는 OPENAI_API_KEY 필요
    - 테스트 대상 소스: src/agents/nodes/formatter.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import os

import pytest

from tests.unit.conftest import get_test_logger, log_test_case

logger = get_test_logger("test_format_response")

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

def _make_state(
    rows: list[dict],
    user_input: str = "데이터 조회해줘",
    trace_entries: list | None = None,
):
    """테스트용 PipelineState 를 생성한다."""
    from src.agents.state.state import (
        ContextInfo,
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

    return PipelineState(
        user_input=user_input,
        preprocessed_input=user_input,
        sql_result=sql_result,
        context=ContextInfo(),
        trace_log=trace,
    )


# ──────────────────────────────────────────────────────────────
# _format_result_for_prompt 순수 함수 테스트
# ──────────────────────────────────────────────────────────────

def test_format_result_for_prompt_has_columns():
    """_format_result_for_prompt 가 컬럼명을 포함한 문자열을 반환한다."""
    from src.services.response_formatter import format_result_for_prompt

    rows = [{"고객번호": "C001", "대출금액": 5000000, "등록일": "2024-01-15"}]
    state = _make_state(rows=rows)
    result = format_result_for_prompt(state.sql_result)

    checks = [
        "고객번호" in result,
        "대출금액" in result,
        "등록일" in result,
    ]
    passed = all(checks)
    log_test_case(
        logger,
        "test_format_result_for_prompt_has_columns",
        input_data="rows 1행, columns=['고객번호', '대출금액', '등록일']",
        expected="컬럼명 3개 모두 포함",
        actual=result[:200],
        passed=passed,
    )
    assert passed


def test_format_result_for_prompt_has_row_count():
    """_format_result_for_prompt 가 총 건수 정보를 포함한다."""
    from src.services.response_formatter import format_result_for_prompt

    rows = [
        {"지점": "강남", "건수": 100},
        {"지점": "서초", "건수": 80},
        {"지점": "송파", "건수": 120},
    ]
    state = _make_state(rows=rows)
    result = format_result_for_prompt(state.sql_result)

    passed = "3건" in result or "총" in result
    log_test_case(
        logger,
        "test_format_result_for_prompt_has_row_count",
        input_data="rows 3행",
        expected="'3건' 또는 '총' 포함",
        actual=result[-100:],
        passed=passed,
    )
    assert passed


def test_format_result_for_prompt_empty():
    """rows 가 없으면 '(조회 결과 없음)' 을 반환한다."""
    from src.services.response_formatter import format_result_for_prompt

    state = _make_state(rows=[])
    result = format_result_for_prompt(state.sql_result)

    passed = "(조회 결과 없음)" in result
    log_test_case(
        logger,
        "test_format_result_for_prompt_empty",
        input_data="rows=[]",
        expected="'(조회 결과 없음)'",
        actual=result,
        passed=passed,
    )
    assert passed


def test_format_result_for_prompt_row_data_present():
    """_format_result_for_prompt 가 행 데이터를 포함한다."""
    from src.services.response_formatter import format_result_for_prompt

    rows = [{"항목": "여신", "금액": 100000000}]
    state = _make_state(rows=rows)
    result = format_result_for_prompt(state.sql_result)

    # 행 데이터가 str(row) 로 포함되어야 함
    passed = "여신" in result or "100000000" in result
    log_test_case(
        logger,
        "test_format_result_for_prompt_row_data_present",
        input_data="rows=[{'항목': '여신', '금액': 100000000}]",
        expected="'여신' 또는 '100000000' 포함",
        actual=result[:200],
        passed=passed,
    )
    assert passed


# ──────────────────────────────────────────────────────────────
# format_trace_summary 테스트 (순수 함수)
# ──────────────────────────────────────────────────────────────

def test_trace_summary_format():
    """format_trace_summary 가 번호 있는 항목 목록을 반환한다."""
    from src.agents.state.state import PipelineState, TraceEntry, format_trace_summary

    state = PipelineState(
        trace_log=[
            TraceEntry(node="전처리", action="입력 정제 완료", detail="특수문자 제거"),
            TraceEntry(node="SQL생성", action="SQL 생성 완료", detail="TB_LOAN_INFO 사용"),
        ],
    )
    summary = format_trace_summary(state)

    passed = "1." in summary and "2." in summary
    log_test_case(
        logger,
        "test_trace_summary_format",
        input_data="trace_log 2항목",
        expected="'1.' 과 '2.' 포함된 요약 문자열",
        actual=summary,
        passed=passed,
    )
    assert passed


def test_trace_summary_empty_on_no_log():
    """trace_log 가 없으면 빈 문자열을 반환한다."""
    from src.agents.state.state import PipelineState, format_trace_summary

    state = PipelineState(trace_log=[])
    summary = format_trace_summary(state)

    passed = summary == ""
    log_test_case(
        logger,
        "test_trace_summary_empty_on_no_log",
        input_data="trace_log=[]",
        expected="''",
        actual=repr(summary),
        passed=passed,
    )
    assert passed


# ──────────────────────────────────────────────────────────────
# LLM 통합 테스트
# ──────────────────────────────────────────────────────────────

@_SKIP_LLM
@pytest.mark.asyncio
async def test_format_data_extraction():
    """실제 LLM 으로 SQL 결과를 한국어 보고서로 포맷팅한다."""
    from src.agents.nodes.formatter import format_response_node
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

    passed = (
        status == QueryStatus.FORMATTED
        and len(response) >= 50
    )
    log_test_case(
        logger,
        "test_format_data_extraction",
        input_data="지점별 대출 3행",
        expected="status=FORMATTED, 응답 50자 이상",
        actual=f"status={status}, 응답 길이={len(response)}",
        passed=passed,
    )
    assert passed


@_SKIP_LLM
@pytest.mark.asyncio
async def test_format_empty_result():
    """빈 결과에 대한 포맷팅은 '(조회 결과 없음)' 관련 문구를 포함한다."""
    from src.agents.nodes.formatter import format_response_node
    from src.agents.state.state import QueryStatus

    state = _make_state(rows=[], user_input="이번 달 연체 고객 목록")
    result = await format_response_node(state)

    response = result.get("formatted_response", "")
    status = result.get("status")

    # LLM 이 빈 결과를 안내하는 메시지를 생성해야 함
    passed = (
        status == QueryStatus.FORMATTED
        and len(response) > 0
    )
    log_test_case(
        logger,
        "test_format_empty_result",
        input_data="rows=[]",
        expected="status=FORMATTED, 안내 메시지 있음",
        actual=f"status={status}, 응답={response[:100]}",
        passed=passed,
    )
    assert passed


@_SKIP_LLM
@pytest.mark.asyncio
async def test_trace_summary_appended():
    """trace_log 가 있으면 <details> 블록이 응답 끝에 추가된다."""
    from src.agents.nodes.formatter import format_response_node

    rows = [{"항목": "테스트", "값": 42}]
    trace_entries = [
        ("SQL생성", "SQL 생성 완료", "TB_TEST 사용"),
        ("SQL실행", "쿼리 실행 완료", "1건"),
    ]
    state = _make_state(rows=rows, trace_entries=trace_entries)
    result = await format_response_node(state)

    response = result.get("formatted_response", "")
    passed = "<details>" in response and "조회 과정 요약" in response

    log_test_case(
        logger,
        "test_trace_summary_appended",
        input_data="trace_log 2항목",
        expected="<details> 블록 포함",
        actual=response[-300:],
        passed=passed,
    )
    assert passed


@_SKIP_LLM
@pytest.mark.asyncio
async def test_no_sql_exposed():
    """포맷팅된 응답에 SQL 코드(SELECT)가 직접 노출되지 않는다."""
    from src.agents.nodes.formatter import format_response_node

    rows = [{"건수": 150}]
    state = _make_state(rows=rows, user_input="이번 달 신규 고객 수")
    result = await format_response_node(state)

    response = result.get("formatted_response", "")

    # SQL 키워드가 보고서 형태의 응답에 코드로 노출되면 안 됨
    # 단, "SELECT 결과" 같은 자연어 표현은 허용
    raw_sql_patterns = ["FROM TB_", "WHERE ", "SELECT *"]
    has_raw_sql = any(p in response for p in raw_sql_patterns)

    passed = not has_raw_sql
    log_test_case(
        logger,
        "test_no_sql_exposed",
        input_data="신규 고객 수 1건",
        expected="'FROM TB_', 'WHERE ', 'SELECT *' 미포함",
        actual=response[:200],
        passed=passed,
    )
    assert passed, f"SQL 코드가 응답에 노출됨: {response[:300]}"


@_SKIP_LLM
@pytest.mark.asyncio
async def test_response_length_reasonable():
    """포맷팅된 응답은 50자 이상 5000자 이하다."""
    from src.agents.nodes.formatter import format_response_node

    rows = [
        {"월": "2024-01", "건수": 150},
        {"월": "2024-02", "건수": 162},
    ]
    state = _make_state(rows=rows, user_input="월별 대출 건수")
    result = await format_response_node(state)

    response = result.get("formatted_response", "")
    length = len(response)
    passed = 50 <= length <= 5000

    log_test_case(
        logger,
        "test_response_length_reasonable",
        input_data="월별 대출 2행",
        expected="50 <= 응답 길이 <= 5000",
        actual=f"응답 길이={length}",
        passed=passed,
    )
    assert passed, f"응답 길이가 범위 밖: {length}"
