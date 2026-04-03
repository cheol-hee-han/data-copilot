"""테이블 3측면 설명 보강 유닛 테스트.

테스트 대상:
  1. 기준 날짜 컬럼 식별 (PK 접미사 / alt_name 보조)
  2. 날짜 분포 패턴 탐지 (detect_date_pattern)
  3. 유사 테이블 비교 트리거 판정 (_find_comparison_groups)
  4. 비교 결과 반영 (rejected 테이블 제거)
  5. LLM 추론 필드 병합 (_merge_llm_inferred_fields)
  6. 프롬프트 포맷팅 (_build_table_block, _format_table_for_sql_prompt)
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.agents.state.state import (
    CandidateTable,
    ColumnInfo,
    KeyDateColumn,
    ObservedDateColumn,
)
from src.agents.nodes.reason.knowledge_fetcher import (
    _identify_key_date_columns,
    _identify_key_date_by_alt_name,
    _parse_meta_columns,
    _resolve_key_date_columns,
    _extract_tables,
)
from src.agents.nodes.reason.knowledge_interpreter import (
    _find_comparison_groups,
    _merge_llm_inferred_fields,
    _build_table_block,
    _build_comparison_block,
)
from src.agents.nodes.reason.tools import (
    detect_date_pattern,
)
from src.agents.nodes.reason.sql_generator import (
    _format_table_for_sql_prompt,
)


# ── 1. 기준 날짜 컬럼 식별 ──────────────────────────────────────


class TestIdentifyKeyDateColumns:
    """PK 컬럼에서 행내표준 날짜 접미사로 기준 컬럼을 식별한다."""

    def test_ymd_suffix(self):
        """YMD 접미사 매칭."""
        result = _identify_key_date_columns(["STRD_YMD", "LOAN_NO"])
        assert len(result) == 1
        assert result[0].column_name == "STRD_YMD"
        assert result[0].suffix == "YMD"
        assert result[0].source == "pk_rule"

    def test_ym_suffix(self):
        """YM 접미사 매칭."""
        result = _identify_key_date_columns(["BASE_YM", "CUST_ID"])
        assert len(result) == 1
        assert result[0].suffix == "YM"

    def test_multiple_date_columns(self):
        """PK에 날짜 컬럼이 2개인 경우 모두 반환."""
        result = _identify_key_date_columns(["STRD_YMD", "TRN_DT"])
        assert len(result) == 2
        suffixes = {r.suffix for r in result}
        assert suffixes == {"YMD", "DT"}

    def test_no_date_column(self):
        """날짜 접미사 없는 PK → 빈 목록."""
        result = _identify_key_date_columns(["LOAN_NO", "SEQ_NO"])
        assert result == []

    def test_case_insensitive(self):
        """대소문자 구분 없이 매칭."""
        result = _identify_key_date_columns(["strd_ymd"])
        assert len(result) == 1

    def test_empty_input(self):
        """빈 입력."""
        result = _identify_key_date_columns([])
        assert result == []


class TestIdentifyKeyDateByAltName:
    """한글 컬럼명(alt_name)에서 기준 날짜 컬럼을 보조 식별한다."""

    def test_korean_keyword_match(self):
        """'기준일' 키워드 매칭."""
        cols = [
            {"name": "BASE_DATE", "alt_name": "기준일자"},
            {"name": "CUST_ID", "alt_name": "고객번호"},
        ]
        result = _identify_key_date_by_alt_name(cols)
        assert len(result) == 1
        assert result[0].column_name == "BASE_DATE"
        assert result[0].source == "alt_name_rule"

    def test_no_match(self):
        """기준 키워드 없음 → 빈 목록."""
        cols = [
            {"name": "AMT", "alt_name": "금액"},
        ]
        result = _identify_key_date_by_alt_name(cols)
        assert result == []

    def test_missing_alt_name(self):
        """alt_name 필드 없음 → 스킵."""
        cols = [{"name": "BASE_YMD"}]
        result = _identify_key_date_by_alt_name(cols)
        assert result == []


# ── 2. 메타 컬럼 파싱 ──────────────────────────────────────────


class TestParseMetaColumns:
    """_parse_meta_columns 함수 테스트."""

    def test_normal_columns(self):
        raw = [
            {"name": "COL_A", "alt_name": "컬럼A", "is_pk": True},
            {"name": "COL_B", "alt_name": "컬럼B", "is_pk": False},
        ]
        column_infos, pk_cols = _parse_meta_columns(raw)
        assert [c.name for c in column_infos] == ["COL_A", "COL_B"]
        assert {c.name: c.alt_name for c in column_infos} == {
            "COL_A": "컬럼A", "COL_B": "컬럼B",
        }
        assert pk_cols == ["COL_A"]

    def test_non_list_input(self):
        column_infos, pk_cols = _parse_meta_columns(None)
        assert column_infos == []
        assert pk_cols == []


# ── 3. 날짜 분포 패턴 탐지 ─────────────────────────────────────


class TestDetectDatePattern:
    """detect_date_pattern 함수 테스트."""

    def test_daily_pattern(self):
        dates = [f"2024030{i}" for i in range(1, 10)]
        result = detect_date_pattern(dates)
        assert "매일" in result

    def test_monthly_end_pattern(self):
        dates = [
            "20240131", "20240229", "20240331",
            "20240430", "20240531", "20240630",
        ]
        result = detect_date_pattern(dates)
        assert "매월 말일" in result

    def test_monthly_ym_pattern(self):
        dates = ["202401", "202402", "202403"]
        result = detect_date_pattern(dates)
        assert "매월" in result

    def test_yearly_pattern(self):
        dates = ["2022", "2023", "2024"]
        result = detect_date_pattern(dates)
        assert "매년" in result

    def test_empty(self):
        assert detect_date_pattern([]) == "0건"

    def test_single_date(self):
        assert detect_date_pattern(["20240101"]) == "1건"


# ── 4. 유사 테이블 비교 트리거 판정 ────────────────────────────


class TestFindComparisonGroups:
    """_find_comparison_groups 함수 테스트."""

    def test_keyword_based_grouping(self):
        """inferred_entity_scope에서 키워드 공유 → 비교 그룹 생성."""
        tables = [
            CandidateTable(
                table_name="TB_LN_BAL_D",
                inferred_entity_scope="전체 여신 일별 잔액",
            ),
            CandidateTable(
                table_name="TB_LN_BAL_M",
                inferred_entity_scope="전체 여신 월별 잔액",
            ),
            CandidateTable(
                table_name="TB_CUST_INFO",
                inferred_entity_scope="고객 기본정보",
            ),
        ]
        groups = _find_comparison_groups(tables)
        # 여신/잔액 키워드를 공유하는 D/M 테이블이 같은 그룹
        assert len(groups) >= 1
        group_names = {t.table_name for t in groups[0]}
        assert "TB_LN_BAL_D" in group_names
        assert "TB_LN_BAL_M" in group_names

    def test_prefix_fallback(self):
        """entity_scope 없으면 접두사 매칭으로 fallback."""
        tables = [
            CandidateTable(table_name="TB_LN_BAL_D"),
            CandidateTable(table_name="TB_LN_BAL_M"),
        ]
        groups = _find_comparison_groups(tables)
        assert len(groups) == 1

    def test_single_table_no_group(self):
        """후보가 1개면 비교 불필요."""
        tables = [CandidateTable(table_name="TB_LN_BAL_D")]
        groups = _find_comparison_groups(tables)
        assert groups == []

    def test_no_similarity(self):
        """유사성 없는 테이블들 → 빈 그룹."""
        tables = [
            CandidateTable(
                table_name="TB_CUST_INFO",
                inferred_entity_scope="고객 기본정보",
            ),
            CandidateTable(
                table_name="TB_BRANCH_M",
                inferred_entity_scope="조직 마스터",
            ),
        ]
        groups = _find_comparison_groups(tables)
        assert groups == []


# ── 5. LLM 추론 필드 병합 ──────────────────────────────────────


class TestMergeLlmInferredFields:
    """_merge_llm_inferred_fields 함수 테스트."""

    def test_merge_all_fields(self):
        """3측면 필드 모두 독립 병합 확인."""
        tables = [CandidateTable(table_name="TB_LN_BAL_D")]
        llm_tables = [{
            "table_name": "TB_LN_BAL_D",
            "entity_scope": "전체 여신 계좌의 일별 잔액",
            "functional_usage": "잔액 조회용",
            "data_refresh_hint": "일별 적재(D+1)",
        }]
        _merge_llm_inferred_fields(tables, llm_tables)
        ct = tables[0]
        assert ct.inferred_entity_scope == "전체 여신 계좌의 일별 잔액"
        assert ct.inferred_functional_usage == "잔액 조회용"
        assert ct.inferred_data_refresh_hint == "일별 적재(D+1)"

    def test_data_refresh_hint_not_inlined(self):
        """data_refresh_hint가 entity_scope에 인라인 병합되지 않음."""
        tables = [CandidateTable(table_name="TB_X")]
        llm_tables = [{
            "table_name": "TB_X",
            "entity_scope": "데이터 범위",
            "data_refresh_hint": "월배치",
        }]
        _merge_llm_inferred_fields(tables, llm_tables)
        assert "(갱신:" not in tables[0].inferred_entity_scope
        assert tables[0].inferred_data_refresh_hint == "월배치"

    def test_unmatched_table_ignored(self):
        """매칭되지 않는 llm_table은 무시."""
        tables = [CandidateTable(table_name="TB_A")]
        llm_tables = [{"table_name": "TB_B", "entity_scope": "other"}]
        _merge_llm_inferred_fields(tables, llm_tables)
        assert tables[0].inferred_entity_scope == ""

    def test_empty_fields_not_overwritten(self):
        """빈 문자열은 기존 값을 덮어쓰지 않음."""
        tables = [CandidateTable(
            table_name="TB_A",
            inferred_entity_scope="기존값",
        )]
        llm_tables = [{"table_name": "TB_A", "entity_scope": ""}]
        _merge_llm_inferred_fields(tables, llm_tables)
        assert tables[0].inferred_entity_scope == "기존값"


# ── 6. _extract_tables ──────────────────────────────────────────


class TestExtractTables:
    """_extract_tables 함수 테스트."""

    def test_extracts_with_alt_names(self):
        """한글 컬럼명(alt_name)을 ColumnInfo.alt_name에 저장."""
        step = SimpleNamespace(tool="search_table_meta")
        result = [{
            "name": "TB_LN_BAL_D",
            "description": "여신잔액일별",
            "columns": [
                {"name": "BASE_YMD", "alt_name": "기준일자",
                 "type": "CHAR", "is_pk": True},
                {"name": "LOAN_NO", "alt_name": "대출번호",
                 "type": "VARCHAR", "is_pk": True},
                {"name": "BAL_AMT", "alt_name": "잔액",
                 "type": "DECIMAL", "is_pk": False},
            ],
        }]
        tables = _extract_tables(step, result)
        assert len(tables) == 1
        ct = tables[0]
        alt_map = {c.name: c.alt_name for c in ct.columns}
        assert alt_map["BASE_YMD"] == "기준일자"
        assert alt_map["BAL_AMT"] == "잔액"
        # PK 날짜 컬럼 식별
        assert len(ct.key_date_columns) == 1
        assert ct.key_date_columns[0].column_name == "BASE_YMD"

    def test_non_table_meta_tool_returns_empty(self):
        """search_table_meta가 아닌 도구 → 빈 목록."""
        step = SimpleNamespace(tool="search_code_meta")
        tables = _extract_tables(step, [{"name": "TB_X"}])
        assert tables == []


# ── 7. 프롬프트 포맷팅 ──────────────────────────────────────────


class TestBuildTableBlock:
    """비교 프롬프트용 테이블 블록 포맷팅 테스트."""

    def test_includes_alt_names_in_columns(self):
        """주요 컬럼에 한글명이 포함된다."""
        ct = CandidateTable(
            table_name="TB_LN_BAL_D",
            description="여신잔액일별",
            columns=[
                ColumnInfo(name="BASE_YMD", alt_name="기준일자"),
                ColumnInfo(name="LOAN_NO"),
                ColumnInfo(name="BAL_AMT", alt_name="잔액"),
            ],
        )
        lines = _build_table_block(ct)
        block = "\n".join(lines)
        assert "BASE_YMD(기준일자)" in block
        assert "BAL_AMT(잔액)" in block
        # alt_name 없는 컬럼은 영문명만
        assert "LOAN_NO" in block

    def test_includes_data_refresh_hint(self):
        """갱신 주기가 별도 라인으로 출력된다."""
        ct = CandidateTable(
            table_name="TB_X",
            description="테스트",
            inferred_data_refresh_hint="일별 적재(D+1)",
        )
        lines = _build_table_block(ct)
        block = "\n".join(lines)
        assert "갱신 주기" in block
        assert "일별 적재(D+1)" in block

    def test_includes_observed_date_columns(self):
        """관찰된 날짜 분포가 출력된다."""
        ct = CandidateTable(
            table_name="TB_X",
            description="테스트",
            key_date_columns=[
                KeyDateColumn(column_name="BASE_YMD", suffix="YMD"),
            ],
            observed_date_columns=[
                ObservedDateColumn(
                    column_name="BASE_YMD",
                    date_range="2024-01-01 ~ 2024-03-24",
                    date_pattern="매일 (84건)",
                ),
            ],
        )
        lines = _build_table_block(ct)
        block = "\n".join(lines)
        assert "2024-01-01 ~ 2024-03-24" in block
        assert "매일 (84건)" in block


class TestFormatTableForSqlPrompt:
    """SQL 생성 프롬프트용 테이블 포맷팅 테스트."""

    def test_includes_3aspect_info(self):
        """3측면 정보가 SQL 프롬프트에 포함된다."""
        ct = CandidateTable(
            table_name="TB_LN_BAL_D",
            description="여신잔액일별",
            columns=[
                ColumnInfo(name="BASE_YMD", alt_name="기준일자"),
                ColumnInfo(name="BAL_AMT", alt_name="잔액"),
            ],
            inferred_entity_scope="전체 여신 계좌의 일별 잔액",
            inferred_functional_usage="잔액 조회용",
            inferred_data_refresh_hint="일별 적재(D+1)",
            observed_date_columns=[
                ObservedDateColumn(
                    column_name="BASE_YMD",
                    date_range="2024-01-01 ~ 2024-03-24",
                    date_pattern="매일 (84건)",
                ),
            ],
        )
        text = _format_table_for_sql_prompt(ct)
        assert "BASE_YMD(기준일자)" in text
        assert "엔티티:" in text
        assert "용도:" in text
        assert "갱신:" in text
        assert "기준컬럼 BASE_YMD:" in text

    def test_minimal_table(self):
        """3측면 정보 없으면 헤더 + 컬럼만 출력."""
        ct = CandidateTable(
            table_name="TB_X",
            description="테스트",
            columns=[ColumnInfo(name="COL_A")],
        )
        text = _format_table_for_sql_prompt(ct)
        assert "TB_X" in text
        assert "COL_A" in text

    def test_schema_qualified_name(self):
        """스키마명이 있으면 스키마명.테이블명으로 출력된다."""
        ct = CandidateTable(
            table_name="TB_ADW_LNB301M",
            schema_name="ADWOWN",
            description="여신잔액월별",
            columns=[
                ColumnInfo(name="BASE_YM"),
                ColumnInfo(name="BAL_AMT"),
            ],
        )
        text = _format_table_for_sql_prompt(ct)
        assert "ADWOWN.TB_ADW_LNB301M" in text

    def test_no_schema_table_only(self):
        """스키마명 없으면 테이블명만 출력된다."""
        ct = CandidateTable(
            table_name="TB_LOAN_INFO",
            description="여신기본",
            columns=[ColumnInfo(name="LOAN_NO")],
        )
        text = _format_table_for_sql_prompt(ct)
        assert "TB_LOAN_INFO" in text
        assert "." not in text.split(":")[0]  # 첫 번째 콜론 전에 점 없음


# ── 8. qualified_name 프로퍼티 ──────────────────────────────────


class TestQualifiedName:
    """CandidateTable.qualified_name 프로퍼티 테스트."""

    def test_with_schema(self):
        ct = CandidateTable(
            table_name="TB_ADW_LNB301M",
            schema_name="ADWOWN",
        )
        assert ct.qualified_name == "ADWOWN.TB_ADW_LNB301M"

    def test_without_schema(self):
        ct = CandidateTable(table_name="TB_LOAN_INFO")
        assert ct.qualified_name == "TB_LOAN_INFO"

    def test_empty_schema(self):
        ct = CandidateTable(
            table_name="TB_X",
            schema_name="",
        )
        assert ct.qualified_name == "TB_X"


class TestBuildTableBlockWithSchema:
    """비교 프롬프트에 스키마명이 포함되는지 테스트."""

    def test_comparison_block_uses_qualified_name(self):
        ct = CandidateTable(
            table_name="TB_ADW_LNB301M",
            schema_name="ADWOWN",
            description="여신잔액월별",
        )
        lines = _build_table_block(ct)
        block = "\n".join(lines)
        assert "### ADWOWN.TB_ADW_LNB301M" in block
