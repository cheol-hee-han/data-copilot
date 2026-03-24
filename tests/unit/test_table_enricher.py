"""테이블 메타 설명 보강(table_meta_enricher) 모듈 단위 테스트.

테스트 대상:
    테이블 설명의 충분성 판정(3관점: 엔티티·기능·갱신주기),
    컬럼 요약 생성, 관련 SQL 탐색, LLM 기반 설명 보강을 검증한다.
    LLM 호출은 Mock으로 대체하여 API 키 없이 실행 가능하다.

입력 예시 (정상):
    - 충분한 설명: "고객별 대출 건의 상태 데이터, 분석 활용, 일배치 갱신"
      → is_description_sufficient = True (3관점 충족)
    - 불충분한 설명: "고객 정보" → LLM 호출하여 보강

결과 예시 (오류 케이스):
    - LLM 호출 실패(Exception) → enriched_description="" (원본 유지)
    - LLM 파싱 실패(ParseError) → enriched_description="" (빈 문자열)

실행 스크립트:
    pytest tests/unit/test_table_enricher.py -v

참고:
    - 외부 의존성 없음 (LLM은 AsyncMock으로 대체)
    - 테스트 대상 소스: src/services/table_meta_enricher.py
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.services.table_meta_enricher import (
    _build_column_summary,
    _count_covered_aspects,
    _find_related_sqls,
    enrich_table_descriptions,
    is_description_sufficient,
)
from src.agents.state.state import ColumnMeta, TableMeta


# ──────────────────────────────────────────────────
# _count_covered_aspects
# ──────────────────────────────────────────────────


def test_count_zero_aspects():
    """관점 키워드가 전혀 없으면 0."""
    assert _count_covered_aspects("단순 테이블") == 0


def test_count_one_aspect_entity():
    """엔티티 관점 키워드만 있으면 1."""
    assert _count_covered_aspects("고객 기본 정보 데이터") >= 1


def test_count_two_aspects():
    """엔티티+기능 관점 키워드가 있으면 2."""
    desc = "고객 기본 정보 데이터, 조회 분석용"
    assert _count_covered_aspects(desc) >= 2


def test_count_three_aspects():
    """세 가지 관점 모두 포함하면 3."""
    desc = "고객 정보 데이터를 저장하며, 조회 분석에 활용되고, 일배치로 갱신된다"
    assert _count_covered_aspects(desc) == 3


# ──────────────────────────────────────────────────
# is_description_sufficient
# ──────────────────────────────────────────────────


def test_sufficient_with_all_aspects():
    """세 가지 관점 + 충분한 길이면 True."""
    table = TableMeta(
        table_name="TB_TEST",
        table_description="고객별 대출 건의 현재 상태 데이터를 저장하며, 여신 업무에서 조회 분석에 활용되고, 일배치로 갱신 적재된다.",
    )
    assert is_description_sufficient(table) is True


def test_insufficient_short_description():
    """짧은 설명은 False."""
    table = TableMeta(
        table_name="TB_TEST",
        table_description="고객 정보",
    )
    assert is_description_sufficient(table) is False


def test_insufficient_missing_aspects():
    """관점이 부족하면 False (길이는 충분해도)."""
    table = TableMeta(
        table_name="TB_TEST",
        table_description="이 테이블은 아주 아주 아주 아주 긴 설명이지만 키워드가 없다",
    )
    assert is_description_sufficient(table) is False


def test_sufficient_uses_enriched_description():
    """enriched_description이 있으면 그것을 기준으로 판단."""
    table = TableMeta(
        table_name="TB_TEST",
        table_description="짧은 설명",
        enriched_description="고객별 대출 건의 현재 상태 데이터를 저장하며, 여신 업무에서 조회 분석에 활용되고, 일배치로 갱신 적재된다.",
    )
    assert is_description_sufficient(table) is True


# ──────────────────────────────────────────────────
# _build_column_summary
# ──────────────────────────────────────────────────


def test_build_column_summary():
    table = TableMeta(
        table_name="TB_TEST",
        columns=[
            ColumnMeta(column_name="COL_A", data_type="VARCHAR(10)", column_description="설명A"),
            ColumnMeta(column_name="COL_B", data_type="INTEGER", column_description="설명B", is_pii=True),
        ],
    )
    result = _build_column_summary(table)
    assert "COL_A" in result
    assert "COL_B" in result
    assert "[PII]" in result


def test_build_column_summary_empty():
    table = TableMeta(table_name="TB_TEST", columns=[])
    result = _build_column_summary(table)
    assert "컬럼 정보 없음" in result


# ──────────────────────────────────────────────────
# _find_related_sqls
# ──────────────────────────────────────────────────


def test_find_related_sqls_matches():
    report = ["SELECT * FROM TB_LOAN_INFO WHERE X = 1"]
    past = ["SELECT COUNT(*) FROM TB_LOAN_INFO"]
    result = _find_related_sqls("TB_LOAN_INFO", report, past)
    assert "보고서" in result
    assert "과거SQL" in result


def test_find_related_sqls_no_match():
    report = ["SELECT * FROM TB_DEPOSIT_INFO"]
    past = ["SELECT COUNT(*) FROM TB_CUST_INFO"]
    result = _find_related_sqls("TB_LOAN_INFO", report, past)
    assert "관련 SQL 없음" in result


def test_find_related_sqls_case_insensitive():
    report = ["select * from tb_loan_info where x = 1"]
    result = _find_related_sqls("TB_LOAN_INFO", report, [])
    assert "보고서" in result


# ──────────────────────────────────────────────────
# enrich_table_descriptions (async)
# ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_enrich_skips_sufficient_tables():
    """충분한 설명이 있는 테이블은 LLM 호출 없이 그대로 반환."""
    table = TableMeta(
        table_name="TB_TEST",
        table_description="고객별 대출 건의 현재 상태 데이터를 저장하며, 여신 업무에서 조회 분석에 활용되고, 일배치로 갱신 적재된다.",
    )
    with patch("src.services.table_meta_enricher.llm_call_with_parse_retry", new_callable=AsyncMock) as mock_llm:
        result = await enrich_table_descriptions([table])
        mock_llm.assert_not_called()
    assert result[0].enriched_description == ""


@pytest.mark.asyncio
async def test_enrich_calls_llm_for_insufficient():
    """불충분한 설명은 LLM을 호출하여 보강."""
    table = TableMeta(
        table_name="TB_LOAN_INFO",
        table_description="여신(대출) 정보 테이블",
        columns=[
            ColumnMeta(column_name="LOAN_NO", data_type="VARCHAR(20)", column_description="대출번호"),
        ],
    )

    enriched_text = "보강된 설명: 대출 건별 데이터를 저장하며 분석에 활용되고 일배치로 갱신된다."

    with patch(
        "src.services.table_meta_enricher.llm_call_with_parse_retry",
        new_callable=AsyncMock,
        return_value=(enriched_text, enriched_text),
    ):
        result = await enrich_table_descriptions([table])

    assert result[0].enriched_description != ""
    assert "보강된 설명" in result[0].enriched_description


@pytest.mark.asyncio
async def test_enrich_handles_llm_failure_gracefully():
    """LLM 호출 실패 시 원본 설명을 유지."""
    table = TableMeta(
        table_name="TB_LOAN_INFO",
        table_description="대출 테이블",
    )

    with patch(
        "src.services.table_meta_enricher.llm_call_with_parse_retry",
        new_callable=AsyncMock,
        side_effect=Exception("API 오류"),
    ):
        result = await enrich_table_descriptions([table])

    assert result[0].enriched_description == ""


@pytest.mark.asyncio
async def test_enrich_passes_related_sqls():
    """보고서/과거 SQL이 프롬프트에 포함되는지 확인."""
    table = TableMeta(
        table_name="TB_LOAN_INFO",
        table_description="대출 테이블",
        columns=[
            ColumnMeta(column_name="LOAN_NO", data_type="VARCHAR(20)", column_description="대출번호"),
        ],
    )
    report_sqls = ["SELECT COUNT(*) FROM TB_LOAN_INFO"]
    past_sqls = ["SELECT LOAN_AMT FROM TB_LOAN_INFO WHERE LOAN_DT >= '2024-01-01'"]

    enriched_text = "보강된 설명입니다."

    # prompt_template에 테이블명/SQL이 반영되도록 플레이스홀더 포함
    template = (
        "테이블: {table_name}\n설명: {original_description}\n"
        "갱신주기: {update_cycle}\n컬럼:\n{column_summary}\n"
        "관련SQL:\n{related_sqls}"
    )

    with patch(
        "src.services.table_meta_enricher.llm_call_with_parse_retry",
        new_callable=AsyncMock,
        return_value=(enriched_text, enriched_text),
    ) as mock_llm:
        await enrich_table_descriptions(
            [table],
            report_sqls=report_sqls,
            past_sqls=past_sqls,
            prompt_template=template,
        )

    # LLM에 전달된 messages 인자에 관련 SQL이 포함되었는지 확인
    call_args = mock_llm.call_args
    messages = call_args.kwargs["messages"]
    user_content = messages[0]["content"]
    assert "TB_LOAN_INFO" in user_content
    assert "보고서" in user_content


@pytest.mark.asyncio
async def test_enrich_multiple_tables_parallel():
    """여러 불충분 테이블을 병렬로 보강."""
    tables = [
        TableMeta(table_name="TB_A", table_description="테이블A"),
        TableMeta(table_name="TB_B", table_description="테이블B"),
    ]

    enriched_text = "보강 완료"

    with patch(
        "src.services.table_meta_enricher.llm_call_with_parse_retry",
        new_callable=AsyncMock,
        return_value=(enriched_text, enriched_text),
    ) as mock_llm:
        result = await enrich_table_descriptions(tables)

    assert mock_llm.call_count == 2
    assert all(t.enriched_description == "보강 완료" for t in result)


@pytest.mark.asyncio
async def test_enrich_empty_list():
    """빈 목록 입력 시 그대로 반환."""
    result = await enrich_table_descriptions([])
    assert result == []


@pytest.mark.asyncio
async def test_enrich_handles_parse_failure():
    """LLM 응답 파싱이 최종 실패(ParseError)하면 빈 문자열 반환."""
    from src.utils.llm import ParseError

    table = TableMeta(
        table_name="TB_TEST",
        table_description="테스트 테이블",
        columns=[
            ColumnMeta(column_name="COL_A", data_type="VARCHAR(10)", column_description="컬럼A"),
        ],
    )

    with patch(
        "src.services.table_meta_enricher.llm_call_with_parse_retry",
        new_callable=AsyncMock,
        side_effect=ParseError("파싱 실패"),
    ):
        result = await enrich_table_descriptions([table])

    assert result[0].enriched_description == ""


@pytest.mark.asyncio
async def test_enrich_both_descriptions_empty():
    """table_description과 enriched_description 모두 빈 경우 불충분 판정."""
    table = TableMeta(table_name="TB_EMPTY", table_description="")
    assert is_description_sufficient(table) is False
