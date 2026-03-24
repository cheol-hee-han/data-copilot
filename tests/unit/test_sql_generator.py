"""SQL 생성 노드 내부 유틸 함수(sql_prompt_assembler) 단위 테스트.

테스트 대상:
    LLM 응답에서 SQL 추출(clean_sql_response), 테이블 메타 포맷팅,
    과거 SQL 중복 제거, 보고서 SQL 제한, 업무 매뉴얼 포맷팅을 검증한다.

입력 예시 (정상):
    - clean_sql_response('```sql\\nSELECT 1\\n```') → "SELECT 1"
    - build_table_info([TableMeta(...)]) → 테이블명·컬럼 포함 텍스트

결과 예시 (오류 케이스):
    - 과거 SQL 없음 → "없음" 안내 메시지
    - 보고서 SQL 10건 → 상위 3건만 포함

실행 스크립트:
    pytest tests/unit/test_sql_generator.py -v

참고:
    - 외부 의존성 없음
    - 테스트 대상 소스: src/services/sql_prompt_assembler.py
"""

from src.services.sql_prompt_assembler import clean_sql_response


class TestCleanSqlResponse:
    """_clean_sql_response 단위 테스트."""

    def test_plain_sql_unchanged(self):
        """마크다운 코드 블록 없는 SQL은 그대로 반환."""
        sql = "SELECT COUNT(*) FROM TB_CUST_INFO"
        assert clean_sql_response(sql) == sql

    def test_extracts_from_code_block(self):
        """```sql ... ``` 블록에서 SQL만 추출."""
        raw = "```sql\nSELECT 1\n```"
        assert clean_sql_response(raw) == "SELECT 1"

    def test_extracts_from_plain_code_block(self):
        """``` ... ``` 블록에서 SQL만 추출."""
        raw = "```\nSELECT 1\n```"
        assert clean_sql_response(raw) == "SELECT 1"

    def test_ignores_text_outside_block(self):
        """코드 블록 외부 텍스트는 무시."""
        raw = (
            "Here is the SQL:\n"
            "```sql\nSELECT 1\n```\n"
            "Hope this helps!"
        )
        assert clean_sql_response(raw) == "SELECT 1"

    def test_multiline_sql_in_block(self):
        """여러 줄 SQL도 정상 추출."""
        raw = (
            "```sql\n"
            "SELECT\n"
            "    COUNT(*)\n"
            "FROM TB_CUST_INFO\n"
            "```"
        )
        result = clean_sql_response(raw)
        assert "SELECT" in result
        assert "COUNT(*)" in result
        assert "TB_CUST_INFO" in result

    def test_strips_whitespace(self):
        """앞뒤 공백 제거."""
        sql = "  SELECT 1  "
        assert clean_sql_response(sql) == "SELECT 1"


# ── 아래: 기존 테스트에서 누락된 케이스 보강 ──

from src.services.sql_prompt_assembler import (
    build_table_info,
    build_past_sqls,
    build_report_sqls,
    build_manual_refs,
)
from src.agents.state.state import (
    ColumnMeta,
    ContextInfo,
    PipelineState,
    TableMeta,
)


class TestBuildTableInfo:
    """_build_table_info 프롬프트 포맷 테스트."""

    def test_includes_table_name_and_columns(self):
        """테이블명과 컬럼 정보가 포맷에 포함된다."""
        state = PipelineState(
            context=ContextInfo(
                table_metas=[
                    TableMeta(
                        table_name="TB_CUST_INFO",
                        table_description="고객 기본 정보",
                        update_cycle="일 1회",
                        columns=[
                            ColumnMeta(column_name="CUST_NO", data_type="VARCHAR", column_description="고객번호"),
                            ColumnMeta(column_name="CUST_NM", data_type="VARCHAR", column_description="고객명"),
                        ],
                    )
                ]
            )
        )
        result = build_table_info(state.context.table_metas)
        assert "TB_CUST_INFO" in result
        assert "CUST_NO" in result
        assert "고객 기본 정보" in result

    def test_pii_column_marked(self):
        """PII 컬럼에 마킹이 표시된다."""
        state = PipelineState(
            context=ContextInfo(
                table_metas=[
                    TableMeta(
                        table_name="TB_CUST_INFO",
                        columns=[
                            ColumnMeta(column_name="JUMIN_NO", data_type="VARCHAR", is_pii=True),
                        ],
                    )
                ]
            )
        )
        result = build_table_info(state.context.table_metas)
        assert "PII" in result

    def test_enriched_description_included(self):
        """보강된 설명이 포맷에 포함된다."""
        state = PipelineState(
            context=ContextInfo(
                table_metas=[
                    TableMeta(
                        table_name="TB_TEST",
                        table_description="테스트",
                        enriched_description="테스트 테이블의 상세 설명입니다.",
                    )
                ]
            )
        )
        result = build_table_info(state.context.table_metas)
        assert "상세 설명" in result


class TestBuildPastSqls:
    """_build_past_sqls 중복 제거 테스트."""

    def test_dedup_vector_and_keyword(self):
        """벡터 검색과 키워드 검색 결과가 중복 제거된다."""
        state = PipelineState(
            context=ContextInfo(
                vector_past_sqls=["SELECT COUNT(*) FROM TB_CUST_INFO", "SELECT SUM(AMT) FROM TB_LOAN"],
                past_sqls=["SELECT COUNT(*) FROM TB_CUST_INFO", "SELECT AVG(AMT) FROM TB_LOAN"],
            )
        )
        result = build_past_sqls(
            state.context.past_sqls,
            state.context.vector_past_sqls,
        )
        # COUNT 쿼리는 한 번만 포함
        assert result.count("COUNT(*)") == 1
        # 세 종류 모두 포함
        assert "SUM" in result
        assert "AVG" in result

    def test_empty_past_sqls(self):
        """과거 SQL 이 없으면 안내 메시지를 반환한다."""
        state = PipelineState(context=ContextInfo())
        result = build_past_sqls(
            state.context.past_sqls,
            state.context.vector_past_sqls,
        )
        assert "없음" in result


class TestBuildReportSqls:
    """_build_report_sqls 테스트."""

    def test_empty_reports(self):
        """보고서 SQL 이 없으면 안내 메시지."""
        state = PipelineState(context=ContextInfo())
        result = build_report_sqls(state.context.report_sqls)
        assert "없음" in result

    def test_reports_limited(self):
        """보고서 SQL 이 3건으로 제한된다."""
        state = PipelineState(
            context=ContextInfo(
                report_sqls=[f"SELECT {i}" for i in range(10)]
            )
        )
        result = build_report_sqls(state.context.report_sqls)
        assert "SELECT 0" in result
        assert "SELECT 2" in result
        assert "SELECT 3" not in result


class TestBuildManualRefs:
    """_build_manual_refs 테스트."""

    def test_empty_manuals(self):
        """업무 매뉴얼이 없으면 안내 메시지."""
        state = PipelineState(context=ContextInfo())
        result = build_manual_refs(
            state.context.manual_references,
        )
        assert "없음" in result
