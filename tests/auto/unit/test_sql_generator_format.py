"""sql_generator 순수 포맷 함수 단위 테스트.

테스트 대상:
    - _format_table_for_sql_prompt: TableMeta → 프롬프트용 전체 텍스트 변환
    - _format_table_header: 테이블 헤더 라인 생성
    - _format_columns: 컬럼 상세 라인 목록 생성
    - _format_column_line: 단일 ColumnInfo 포맷
    - _parse_sql_response: LLM 응답 JSON 파싱 (status, sql, failure_reasons, assumptions)
    - _build_assumption_signals: assumption 문자열 → AmbiguitySignal 변환

설계 원칙:
    - LLM 호출 없음, DB 연결 없음
    - TableMeta, ColumnInfo Pydantic 모델을 직접 생성하여 검증

실행:
    pytest tests/auto/unit/test_sql_generator_format.py -v
"""

from __future__ import annotations

import json

import pytest

from src.agents.state.state import ColumnInfo, TableMeta, SelectionStatus
from src.agents.nodes.reason.sql_generator import (
    _format_table_for_sql_prompt,
    _format_table_header,
    _format_columns,
    _format_column_line,
    _parse_sql_response,
    _build_assumption_signals,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 헬퍼 팩토리
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _col(
    name: str,
    alt_name: str = "",
    col_type: str = "",
    is_pk: bool = False,
    description: str = "",
) -> ColumnInfo:
    return ColumnInfo(
        name=name,
        alt_name=alt_name,
        col_type=col_type,
        is_pk=is_pk,
        description=description,
    )


def _table(
    table_name: str,
    alt_name: str = "",
    description: str = "",
    schema_name: str = "",
    subject_area: str = "",
    columns: list[ColumnInfo] | None = None,
    selection_status: SelectionStatus = SelectionStatus.SELECTED,
) -> TableMeta:
    return TableMeta(
        table_name=table_name,
        alt_name=alt_name,
        description=description,
        schema_name=schema_name,
        subject_area=subject_area,
        columns=columns or [],
        selection_status=selection_status,
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _format_column_line
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_format_column_line_minimal():
    """최소 정보(이름만)도 포맷 가능."""
    c = _col("CUST_NO")
    result = _format_column_line(c)
    assert "CUST_NO" in result


def test_format_column_line_with_alt_name():
    """alt_name이 있으면 괄호 안에 포함."""
    c = _col("CUST_NO", alt_name="고객번호")
    result = _format_column_line(c)
    assert "CUST_NO" in result
    assert "(고객번호)" in result


def test_format_column_line_with_col_type():
    """col_type이 있으면 포함."""
    c = _col("CUST_NO", col_type="VARCHAR(20)")
    result = _format_column_line(c)
    assert "VARCHAR(20)" in result


def test_format_column_line_pk_marker():
    """is_pk=True이면 [PK] 마커 포함."""
    c = _col("CUST_NO", is_pk=True)
    result = _format_column_line(c)
    assert "[PK]" in result


def test_format_column_line_no_pk_marker():
    """is_pk=False이면 [PK] 마커 없음."""
    c = _col("CUST_NM", is_pk=False)
    result = _format_column_line(c)
    assert "[PK]" not in result


def test_format_column_line_with_description():
    """description이 있으면 ' — ' 뒤에 포함."""
    c = _col("REG_DT", description="고객 등록일자")
    result = _format_column_line(c)
    assert "고객 등록일자" in result
    assert "—" in result


def test_format_column_line_full():
    """모든 필드가 있을 때 올바른 순서로 조합."""
    c = _col("LOAN_NO", alt_name="대출번호", col_type="VARCHAR(20)",
             is_pk=True, description="대출 일련번호")
    result = _format_column_line(c)
    assert "LOAN_NO" in result
    assert "(대출번호)" in result
    assert "VARCHAR(20)" in result
    assert "[PK]" in result
    assert "대출 일련번호" in result


def test_format_column_line_starts_with_indent():
    """컬럼 라인은 4칸 들여쓰기로 시작."""
    c = _col("CUST_NO")
    result = _format_column_line(c)
    assert result.startswith("    ")


def test_format_column_line_no_description():
    """description이 없으면 '—' 문자 없음."""
    c = _col("CUST_NO", description="")
    result = _format_column_line(c)
    assert "—" not in result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _format_columns
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_format_columns_empty():
    """컬럼이 없으면 빈 리스트 반환."""
    ct = _table("TB_X")
    result = _format_columns(ct)
    assert result == []


def test_format_columns_header_line():
    """컬럼이 있으면 첫 번째 라인이 '  컬럼:'."""
    ct = _table("TB_CUST_INFO", columns=[_col("CUST_NO")])
    result = _format_columns(ct)
    assert result[0] == "  컬럼:"


def test_format_columns_count():
    """컬럼 개수만큼 라인이 생성되어야 한다 (헤더 1 + 컬럼 수)."""
    cols = [_col("COL_A"), _col("COL_B"), _col("COL_C")]
    ct = _table("TB_X", columns=cols)
    result = _format_columns(ct)
    assert len(result) == 1 + len(cols)  # 헤더 + 각 컬럼


def test_format_columns_all_names_present():
    """모든 컬럼명이 라인 목록에 포함되어야 한다."""
    cols = [_col("LOAN_NO"), _col("CUST_NO"), _col("LOAN_AMT")]
    ct = _table("TB_LOAN", columns=cols)
    lines = _format_columns(ct)
    combined = "\n".join(lines)
    assert "LOAN_NO" in combined
    assert "CUST_NO" in combined
    assert "LOAN_AMT" in combined


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _format_table_header
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_format_table_header_minimal():
    """최소 구성 테이블은 '- 테이블명'으로 시작."""
    ct = _table("TB_CUST_INFO")
    result = _format_table_header(ct)
    assert result.startswith("- TB_CUST_INFO")


def test_format_table_header_with_schema():
    """schema_name이 있으면 'schema.table' 형태로 표시."""
    ct = _table("TB_CUST_INFO", schema_name="DW")
    result = _format_table_header(ct)
    assert "DW.TB_CUST_INFO" in result


def test_format_table_header_with_alt_name():
    """alt_name이 있으면 괄호 안에 포함."""
    ct = _table("TB_CUST_INFO", alt_name="고객기본정보")
    result = _format_table_header(ct)
    assert "(고객기본정보)" in result


def test_format_table_header_with_subject_area():
    """subject_area가 있으면 대괄호 안에 포함."""
    ct = _table("TB_CUST_INFO", subject_area="고객")
    result = _format_table_header(ct)
    assert "[고객]" in result


def test_format_table_header_with_description():
    """description이 있으면 ': ' 뒤에 포함."""
    ct = _table("TB_CUST_INFO", description="고객 기본 정보 테이블")
    result = _format_table_header(ct)
    assert "고객 기본 정보 테이블" in result
    assert ": " in result


def test_format_table_header_reference_status_annotation():
    """REFERENCE 상태 테이블은 참고용 안내 포함."""
    ct = _table("TB_REF_TABLE", selection_status=SelectionStatus.REFERENCE)
    result = _format_table_header(ct)
    assert "[참고]" in result
    assert "SQL 이력 해석용" in result


def test_format_table_header_selected_no_reference_annotation():
    """SELECTED 상태 테이블은 참고용 안내 없음."""
    ct = _table("TB_SELECTED", selection_status=SelectionStatus.SELECTED)
    result = _format_table_header(ct)
    assert "[참고]" not in result


def test_format_table_header_full():
    """모든 옵션이 있을 때 올바르게 조합."""
    ct = _table(
        "TB_LOAN_INFO",
        alt_name="여신정보",
        description="대출 정보 테이블",
        schema_name="DW",
        subject_area="여신",
    )
    result = _format_table_header(ct)
    assert "DW.TB_LOAN_INFO" in result
    assert "(여신정보)" in result
    assert "[여신]" in result
    assert "대출 정보 테이블" in result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _format_table_for_sql_prompt
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_format_table_for_sql_prompt_returns_string():
    """반환 타입이 str이어야 한다."""
    ct = _table("TB_CUST_INFO")
    result = _format_table_for_sql_prompt(ct)
    assert isinstance(result, str)


def test_format_table_for_sql_prompt_contains_table_name():
    """테이블명이 포함되어야 한다."""
    ct = _table("TB_CUST_INFO")
    result = _format_table_for_sql_prompt(ct)
    assert "TB_CUST_INFO" in result


def test_format_table_for_sql_prompt_with_columns():
    """컬럼이 있으면 컬럼 섹션도 포함."""
    ct = _table(
        "TB_CUST_INFO",
        columns=[
            _col("CUST_NO", alt_name="고객번호", col_type="VARCHAR(20)", is_pk=True),
            _col("CUST_NM", alt_name="고객명", description="고객 이름"),
        ],
    )
    result = _format_table_for_sql_prompt(ct)
    assert "  컬럼:" in result
    assert "CUST_NO" in result
    assert "CUST_NM" in result
    assert "[PK]" in result


def test_format_table_for_sql_prompt_no_columns():
    """컬럼이 없어도 테이블 헤더는 포함."""
    ct = _table("TB_EMPTY")
    result = _format_table_for_sql_prompt(ct)
    assert "TB_EMPTY" in result
    assert "컬럼" not in result


def test_format_table_for_sql_prompt_multiline():
    """여러 컬럼이 있으면 결과가 복수 라인이어야 한다."""
    ct = _table(
        "TB_LOAN_INFO",
        columns=[_col("LOAN_NO"), _col("CUST_NO"), _col("LOAN_AMT")],
    )
    result = _format_table_for_sql_prompt(ct)
    lines = result.split("\n")
    assert len(lines) > 1


def test_format_table_for_sql_prompt_sample_rows():
    """sample_rows가 있으면 샘플 데이터 섹션 포함."""
    from src.agents.state.state import ObservedDateColumn

    ct = _table(
        "TB_CUST_INFO",
        columns=[_col("CUST_NO")],
    )
    ct.sample_rows = [
        {"CUST_NO": "C00000001"},
        {"CUST_NO": "C00000002"},
    ]
    result = _format_table_for_sql_prompt(ct)
    assert "샘플 데이터" in result


def test_format_table_for_sql_prompt_observed_date_columns():
    """observed_date_columns가 있으면 기준컬럼 정보 포함."""
    from src.agents.state.state import ObservedDateColumn

    ct = _table("TB_STAT", columns=[_col("BASE_YM")])
    ct.observed_date_columns = [
        ObservedDateColumn(
            column_name="BASE_YM",
            date_range="202301~202312",
            date_pattern="YYYYMM",
        )
    ]
    result = _format_table_for_sql_prompt(ct)
    assert "BASE_YM" in result
    assert "202301~202312" in result
    assert "YYYYMM" in result


def test_format_table_for_sql_prompt_column_with_discovered_values():
    """discovered_values가 있는 컬럼은 실제 값 목록 포함."""
    ct = _table(
        "TB_CUST_INFO",
        columns=[
            ColumnInfo(
                name="CUST_TYPE_CD",
                alt_name="고객유형",
                col_type="VARCHAR(2)",
                discovered_values=["01", "02", "03"],
            )
        ],
    )
    result = _format_table_for_sql_prompt(ct)
    assert "01" in result
    assert "02" in result
    assert "03" in result


def test_format_table_for_sql_prompt_column_with_stats():
    """컬럼 통계(min_val, max_val, distinct_count, null_rate)가 있으면 포함."""
    ct = _table(
        "TB_LOAN_INFO",
        columns=[
            ColumnInfo(
                name="LOAN_AMT",
                alt_name="대출금액",
                col_type="NUMERIC(18,0)",
                min_val="1000000",
                max_val="5000000000",
                distinct_count=12345,
                null_rate=0.02,
            )
        ],
    )
    result = _format_table_for_sql_prompt(ct)
    assert "MIN=1000000" in result
    assert "MAX=5000000000" in result
    assert "고유값=12,345" in result
    assert "NULL=2.0%" in result


def test_format_table_for_sql_prompt_reference_table_annotation():
    """REFERENCE 상태 테이블 포맷에 참고 안내 포함."""
    ct = _table("TB_REF", selection_status=SelectionStatus.REFERENCE)
    result = _format_table_for_sql_prompt(ct)
    assert "[참고]" in result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 경계 조건
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_format_column_line_special_chars_in_description():
    """설명에 특수문자가 있어도 정상 처리."""
    c = _col("AMT", description="금액(원) — 세금 포함")
    result = _format_column_line(c)
    assert "금액(원)" in result


def test_format_table_header_empty_alt_name():
    """alt_name이 빈 문자열이면 괄호 없음."""
    ct = _table("TB_X", alt_name="")
    result = _format_table_header(ct)
    assert "()" not in result


def test_format_table_header_empty_subject_area():
    """subject_area가 빈 문자열이면 대괄호 없음."""
    ct = _table("TB_X", subject_area="")
    result = _format_table_header(ct)
    assert "[]" not in result


def test_format_columns_pk_and_non_pk_order():
    """PK 컬럼이 먼저 오더라도 정의된 순서대로 렌더링."""
    cols = [
        _col("CUST_NO", is_pk=True),
        _col("CUST_NM", is_pk=False),
        _col("REG_DT", is_pk=False),
    ]
    ct = _table("TB_CUST_INFO", columns=cols)
    lines = _format_columns(ct)
    combined = "\n".join(lines)
    # PK 컬럼이 CUST_NM보다 앞에 있어야 한다
    pos_pk = combined.index("CUST_NO")
    pos_nm = combined.index("CUST_NM")
    assert pos_pk < pos_nm


def test_format_table_for_sql_prompt_large_discovered_values_truncated():
    """discovered_values가 100개를 초과하면 '외 N건' 표시."""
    values = [str(i) for i in range(150)]
    ct = _table(
        "TB_X",
        columns=[ColumnInfo(name="CODE_COL", discovered_values=values)],
    )
    result = _format_table_for_sql_prompt(ct)
    assert "외 50건" in result


class TestParseSqlResponse:
    """_parse_sql_response 파싱 테스트."""

    def test_success_with_assumptions(self):
        """success + assumptions 파싱."""
        raw = json.dumps({
            "status": "success",
            "sql": "SELECT * FROM TB_X LIMIT 100",
            "failure_reasons": [],
            "assumptions": ["'최근'의 해석 → 최근 1개월"],
            "explanation": "단순 조회",
        })
        result = _parse_sql_response(raw)
        assert result["status"] == "success"
        assert result["sql"] == "SELECT * FROM TB_X LIMIT 100"
        assert result["failure_reasons"] == []
        assert result["assumptions"] == ["'최근'의 해석 → 최근 1개월"]
        assert result["explanation"] == "단순 조회"

    def test_success_without_assumptions(self):
        """assumptions 필드 없는 LLM 응답 (하위 호환)."""
        raw = json.dumps({
            "status": "success",
            "sql": "SELECT 1",
            "failure_reasons": [],
            "explanation": "",
        })
        result = _parse_sql_response(raw)
        assert result["assumptions"] == []

    def test_success_with_multiple_assumptions(self):
        """복수 assumptions 파싱."""
        raw = json.dumps({
            "status": "success",
            "sql": "SELECT ... ORDER BY amt DESC LIMIT 10",
            "failure_reasons": [],
            "assumptions": [
                "'예금신규 금액'의 해석 → 요청 기간 내 신규된 예금의 전체 잔액",
                "'상위' 기준 → 금액 합계 기준 내림차순",
            ],
            "explanation": "상위 10개 지점 집계",
        })
        result = _parse_sql_response(raw)
        assert len(result["assumptions"]) == 2

    def test_fail_with_failure_reasons(self):
        """fail + failure_reasons 파싱."""
        raw = json.dumps({
            "status": "fail",
            "sql": "",
            "failure_reasons": ["연체율 산출식 미확인"],
            "assumptions": [],
            "explanation": "산출식 불명",
        })
        result = _parse_sql_response(raw)
        assert result["status"] == "fail"
        assert result["failure_reasons"] == ["연체율 산출식 미확인"]
        assert result["assumptions"] == []

    def test_backward_compat_reasons_key(self):
        """기존 reasons 키도 failure_reasons로 매핑 (하위 호환)."""
        raw = json.dumps({
            "status": "fail",
            "sql": "",
            "reasons": ["구 버전 사유"],
            "explanation": "",
        })
        result = _parse_sql_response(raw)
        assert result["failure_reasons"] == ["구 버전 사유"]
        assert result["assumptions"] == []

    def test_invalid_status_defaults_to_fail(self):
        """status가 success/fail이 아니면 fail로 처리."""
        raw = json.dumps({
            "status": "partial",
            "sql": "SELECT 1",
            "failure_reasons": [],
            "assumptions": [],
            "explanation": "",
        })
        result = _parse_sql_response(raw)
        assert result["status"] == "fail"


class TestBuildAssumptionSignals:
    """_build_assumption_signals 변환 테스트."""

    def test_empty_assumptions(self):
        """빈 리스트 입력 시 빈 리스트 반환."""
        assert _build_assumption_signals([], None) == []

    def test_single_assumption_with_arrow(self):
        """→ 구분자가 있는 assumption 변환."""
        signals = _build_assumption_signals(
            ["'잔액'의 해석 → 기말잔액(BAL_AMT)"],
            turn_id="test-turn-001",
        )
        assert len(signals) == 1
        s = signals[0]
        assert s.source_node == "sql_generator"
        assert s.decision == "INFER"
        assert s.question == "'잔액'의 해석"
        assert s.inferred_value == "기말잔액(BAL_AMT)"
        assert s.turn_id == "test-turn-001"

    def test_assumption_without_arrow(self):
        """→ 없는 assumption은 question과 inferred_value가 동일."""
        signals = _build_assumption_signals(
            ["최근 1개월로 해석"],
            turn_id=None,
        )
        assert len(signals) == 1
        s = signals[0]
        assert s.question == "최근 1개월로 해석"
        assert s.inferred_value == "최근 1개월로 해석"
        assert s.turn_id is None

    def test_multiple_assumptions(self):
        """복수 assumptions 변환."""
        signals = _build_assumption_signals(
            [
                "'신규' 해석 → 당월 실행 건",
                "'상위' 기준 → 금액 기준 내림차순",
            ],
            turn_id="turn-002",
        )
        assert len(signals) == 2
        assert all(s.source_node == "sql_generator" for s in signals)
        assert all(s.decision == "INFER" for s in signals)

    def test_arrow_in_value(self):
        """inferred_value 내부에 → 가 있는 경우 첫 번째만 분리."""
        signals = _build_assumption_signals(
            ["'금액' 해석 → A → B 기준"],
            turn_id=None,
        )
        assert len(signals) == 1
        assert signals[0].question == "'금액' 해석"
        assert signals[0].inferred_value == "A → B 기준"

    def test_ascii_arrow_fallback(self):
        """폐쇄망 모델이 ASCII '->' 를 출력하는 경우 대응."""
        signals = _build_assumption_signals(
            ["'잔액' 해석 -> 기말잔액"],
            turn_id="turn-ascii",
        )
        assert len(signals) == 1
        assert signals[0].question == "'잔액' 해석"
        assert signals[0].inferred_value == "기말잔액"
