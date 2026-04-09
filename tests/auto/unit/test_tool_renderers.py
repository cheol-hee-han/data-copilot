"""tool_renderers 단위 테스트.

테스트 대상:
    - serialize_tool_results_by_step: DONE 스텝 목록 -> 블록 조립
    - serialize_single_step: 단일 스텝 렌더링
    - 개별 렌더러: search_use_cases, search_table_meta, lookup_code_meta,
      search_biz_terms, search_manual, get_sample_rows,
      get_date_distribution, get_column_values, get_column_profile,
      미등록 도구 fallback

설계 원칙:
    - LLM 호출 없음, 외부 의존성 없음
    - ExecutionStep 목 객체를 직접 생성하여 순수 텍스트 변환 로직만 검증

실행:
    pytest tests/auto/unit/test_tool_renderers.py -v
"""

from __future__ import annotations

from src.agents.state.state import ExecutionStep, StepStatus
from src.agents.nodes.reason.tool_renderers import (
    serialize_tool_results_by_step,
    serialize_single_step,
)


def _step(
    tool: str,
    raw_result,
    step: int = 1,
    input: str = "테스트입력",
    purpose: str = "테스트 목적",
    status: StepStatus = StepStatus.DONE,
) -> ExecutionStep:
    return ExecutionStep(
        step=step,
        tool=tool,
        input=input,
        purpose=purpose,
        status=status,
        raw_result=raw_result,
    )


def test_serialize_empty_plan_returns_no_result_message():
    """실행 계획이 비어 있으면 '도구 실행 결과 없음' 반환."""
    result = serialize_tool_results_by_step([])
    assert result == "(도구 실행 결과 없음)"


def test_serialize_skips_non_done_steps():
    """DONE이 아닌 스텝(PENDING, FAILED)은 직렬화에서 제외."""
    steps = [
        _step("search_table_meta", {"tables": []}, status=StepStatus.PENDING),
        _step("search_table_meta", {"tables": []}, status=StepStatus.FAILED),
    ]
    result = serialize_tool_results_by_step(steps)
    assert result == "(도구 실행 결과 없음)"


def test_serialize_skips_step_with_none_raw_result():
    """raw_result가 None인 DONE 스텝은 제외."""
    step = ExecutionStep(
        step=1, tool="search_table_meta", input="고객",
        purpose="테스트", status=StepStatus.DONE, raw_result=None,
    )
    result = serialize_tool_results_by_step([step])
    assert result == "(도구 실행 결과 없음)"


def test_serialize_multiple_done_steps_joined_by_double_newline():
    """복수 DONE 스텝은 이중 개행으로 연결."""
    steps = [
        _step("search_table_meta", {"tables": []}, step=1),
        _step("lookup_code_meta", [], step=2),
    ]
    result = serialize_tool_results_by_step(steps)
    assert "\n\n" in result


def test_serialize_single_step_empty_for_non_done():
    """serialize_single_step은 DONE이 아니면 빈 문자열 반환."""
    step = _step(
        "search_table_meta", {"tables": []}, status=StepStatus.PENDING,
    )
    assert serialize_single_step(step) == ""


def test_serialize_single_step_empty_for_none_result():
    """serialize_single_step은 raw_result=None이면 빈 문자열 반환."""
    step = ExecutionStep(
        step=1, tool="search_table_meta", input="고객",
        purpose="테스트", status=StepStatus.DONE, raw_result=None,
    )
    assert serialize_single_step(step) == ""


def test_serialize_single_step_returns_nonempty_for_done():
    """serialize_single_step은 DONE + valid raw_result -> 비어있지 않은 텍스트."""
    step = _step("lookup_code_meta", [], step=1)
    result = serialize_single_step(step)
    assert isinstance(result, str)
    assert len(result) > 0


def test_render_table_meta_no_tables():
    """tables가 빈 리스트이면 '결과 없음' 포함."""
    step = _step("search_table_meta", {"tables": []})
    result = serialize_single_step(step)
    assert "결과 없음" in result


def test_render_table_meta_wrong_type_returns_empty():
    """raw_result가 list이면 빈 문자열 반환 (dict 타입 검사)."""
    step = _step("search_table_meta", ["not", "a", "dict"])
    result = serialize_single_step(step)
    assert result == ""


def test_render_table_meta_with_tables():
    """테이블이 존재하면 테이블명, 판단 안내 포함."""
    raw = {
        "tables": [
            {
                "table_name": "TB_CUST_INFO",
                "alt_name": "고객기본정보",
                "description": "고객 기본 정보",
                "columns": [
                    {
                        "name": "CUST_NO",
                        "alt_name": "고객번호",
                        "is_pk": True,
                    },
                    {
                        "name": "CUST_NM",
                        "alt_name": "고객명",
                        "is_pk": False,
                        "col_type": "VARCHAR(100)",
                    },
                ],
            }
        ]
    }
    step = _step("search_table_meta", raw, input="고객")
    result = serialize_single_step(step)
    assert "TB_CUST_INFO" in result
    assert "고객기본정보" in result
    assert "PK" in result
    assert "→" in result


def test_render_table_meta_with_sample_rows():
    """sample_rows가 있으면 샘플 데이터 헤더 포함."""
    raw = {
        "tables": [
            {
                "table_name": "TB_CUST_INFO",
                "alt_name": "",
                "description": "",
                "columns": [],
                "sample_rows": [
                    {"CUST_NO": "C00000001", "CUST_NM": "홍길동"},
                ],
            }
        ]
    }
    step = _step("search_table_meta", raw)
    result = serialize_single_step(step)
    assert "샘플" in result


def test_render_table_meta_header_contains_step_and_tool():
    """헤더에 스텝 번호와 도구명이 포함되어야 한다."""
    raw = {
        "tables": [
            {
                "table_name": "TB_X",
                "alt_name": "",
                "description": "",
                "columns": [],
            }
        ]
    }
    step = _step("search_table_meta", raw, step=3, input="대출")
    result = serialize_single_step(step)
    assert "[Step 3]" in result
    assert "search_table_meta" in result
    assert "대출" in result


def test_render_code_meta_empty_list():
    """빈 리스트이면 '결과 없음' 포함."""
    step = _step("lookup_code_meta", [])
    result = serialize_single_step(step)
    assert "결과 없음" in result


def test_render_code_meta_wrong_type_returns_no_result():
    """dict이면 '결과 없음' 포함 (list 타입 검사)."""
    step = _step("lookup_code_meta", {"unexpected": True})
    result = serialize_single_step(step)
    assert "결과 없음" in result


def test_render_code_meta_with_codes():
    """코드값이 있으면 각 code_field와 코드값·설명 포함."""
    raw = [
        {
            "code_field": "CUST_TYPE_CD",
            "code_field_desc": "고객유형코드",
            "codes": {"01": "개인", "02": "기업"},
        }
    ]
    step = _step("lookup_code_meta", raw)
    result = serialize_single_step(step)
    assert "CUST_TYPE_CD" in result
    assert "01" in result
    assert "개인" in result
    assert "→" in result


def test_render_code_meta_multiple_fields():
    """복수 code_field가 모두 렌더링되어야 한다."""
    raw = [
        {
            "code_field": "LOAN_TYPE_CD",
            "code_field_desc": "대출유형",
            "codes": {"01": "신용"},
        },
        {
            "code_field": "GENDER_CD",
            "code_field_desc": "성별",
            "codes": {"M": "남성"},
        },
    ]
    step = _step("lookup_code_meta", raw)
    result = serialize_single_step(step)
    assert "LOAN_TYPE_CD" in result
    assert "GENDER_CD" in result


def test_render_biz_terms_empty():
    """빈 리스트이면 '결과 없음' 포함."""
    step = _step("search_biz_terms", [])
    result = serialize_single_step(step)
    assert "결과 없음" in result


def test_render_biz_terms_with_entry():
    """용어명·정의·동의어·관련 테이블이 모두 포함."""
    raw = [
        {
            "name": "연체율",
            "biz_term_definition": "연체금액 / 총대출금액 x 100",
            "synonyms": ["NPL비율"],
            "table_name": "TB_LOAN_OVERDUE_STAT",
        }
    ]
    step = _step("search_biz_terms", raw)
    result = serialize_single_step(step)
    assert "연체율" in result
    assert "NPL비율" in result
    assert "TB_LOAN_OVERDUE_STAT" in result
    assert "→" in result


def test_render_biz_terms_no_synonyms():
    """동의어 없는 항목도 정상 렌더링, 동의어 섹션 없음."""
    raw = [
        {
            "name": "여신",
            "biz_term_definition": "대출 자산",
            "synonyms": [],
            "table_name": "",
        }
    ]
    step = _step("search_biz_terms", raw)
    result = serialize_single_step(step)
    assert "여신" in result
    assert "동의어" not in result


def test_render_biz_manuals_empty():
    """빈 리스트이면 '결과 없음' 포함."""
    step = _step("search_manual", [])
    result = serialize_single_step(step)
    assert "결과 없음" in result


def test_render_biz_manuals_with_content():
    """content와 score가 렌더링에 포함."""
    raw = [{"content": "연체 관리 기준: 1~29일 단기연체", "score": 0.87}]
    step = _step("search_manual", raw)
    result = serialize_single_step(step)
    assert "0.87" in result
    assert "연체 관리 기준" in result
    assert "→" in result


def test_render_biz_manuals_multiple_items():
    """복수 항목에 순번이 올바르게 매겨져야 한다."""
    raw = [
        {"content": "첫 번째 매뉴얼", "score": 0.9},
        {"content": "두 번째 매뉴얼", "score": 0.7},
    ]
    step = _step("search_manual", raw)
    result = serialize_single_step(step)
    assert "1." in result
    assert "2." in result


def test_render_sample_rows_empty():
    """빈 리스트이면 '결과 없음' 포함."""
    step = _step("get_sample_rows", [], input="TB_CUST_INFO")
    result = serialize_single_step(step)
    assert "결과 없음" in result


def test_render_sample_rows_with_data():
    """샘플 행이 있으면 컬럼명과 값 포함, 날짜 포맷 확인 안내 포함."""
    raw = [
        {"CUST_NO": "C00000001", "CUST_NM": "홍길동", "REG_DT": "2024-01-15"},
        {"CUST_NO": "C00000002", "CUST_NM": "김영희", "REG_DT": "2024-02-20"},
    ]
    step = _step("get_sample_rows", raw, input="TB_CUST_INFO, 5")
    result = serialize_single_step(step)
    assert "TB_CUST_INFO" in result
    assert "CUST_NO" in result
    assert "C00000001" in result
    assert "→" in result


def test_render_sample_rows_table_name_extracted_from_input():
    """input 첫 번째 토큰이 테이블명으로 추출되어야 한다."""
    raw = [{"COL_A": "val1"}]
    step = _step("get_sample_rows", raw, input="MY_TABLE, 10")
    result = serialize_single_step(step)
    assert "MY_TABLE" in result


def test_render_date_distribution_none_returns_empty():
    """raw_result=None은 serialize_single_step에서 일찍 빈 문자열 반환."""
    step = ExecutionStep(
        step=1,
        tool="get_date_distribution",
        input="TB_CUST_INFO, REG_DT",
        purpose="날짜 분포 확인",
        status=StepStatus.DONE,
        raw_result=None,
    )
    assert serialize_single_step(step) == ""


def test_render_date_distribution_empty_dict_returns_no_data_message():
    """빈 dict(falsy)이면 '결과 없음' 메시지가 포함된 비어있지 않은 텍스트."""
    step = _step(
        "get_date_distribution", {}, input="TB_CUST_INFO, REG_DT",
    )
    result = serialize_single_step(step)
    assert len(result) > 0
    assert "결과 없음" in result


def test_render_date_distribution_dict_with_dates():
    """dict 형식 결과에서 날짜 범위와 패턴 추출."""
    raw = {
        "dates": ["20240101", "20240201", "20240301"],
        "recent_values": ["20240301"],
    }
    step = _step("get_date_distribution", raw, input="TB_CUST_INFO, REG_DT")
    result = serialize_single_step(step)
    assert "20240101" in result
    assert "20240301" in result
    assert "YYYYMMDD" in result


def test_render_date_distribution_list_format():
    """list 형식 결과도 처리."""
    raw = ["20240101", "20240201"]
    step = _step("get_date_distribution", raw, input="TB_LOAN_INFO, LOAN_DT")
    result = serialize_single_step(step)
    assert "20240101" in result


def test_render_date_distribution_yyyymm_pattern():
    """6자리 날짜는 YYYYMM 패턴으로 감지."""
    raw = {"dates": ["202401", "202402", "202403"], "recent_values": []}
    step = _step("get_date_distribution", raw, input="TB_STAT, BASE_YM")
    result = serialize_single_step(step)
    assert "YYYYMM" in result


def test_render_date_distribution_iso_pattern():
    """YYYY-MM-DD 패턴 감지."""
    raw = {"dates": ["2024-01-01", "2024-02-01"], "recent_values": []}
    step = _step("get_date_distribution", raw, input="TB_X, COL")
    result = serialize_single_step(step)
    assert "YYYY-MM-DD" in result


def test_render_column_values_empty():
    """빈 리스트이면 '결과 없음' 포함."""
    step = _step(
        "get_column_values", [], input="TB_CUST_INFO, BRCH_CD, 강남",
    )
    result = serialize_single_step(step)
    assert "결과 없음" in result


def test_render_column_values_with_data():
    """값 목록이 있으면 검색 설명과 값 포함."""
    raw = ["강남지점", "강남역지점"]
    step = _step(
        "get_column_values",
        raw,
        input="TB_BRANCH_INFO, BRCH_NM, 강남",
    )
    result = serialize_single_step(step)
    assert "강남지점" in result
    assert "강남역지점" in result
    assert "→" in result


def test_render_column_values_search_term_in_description():
    """검색어가 설명에 포함되어야 한다."""
    raw = ["01", "02"]
    step = _step(
        "get_column_values",
        raw,
        input="TB_CUST_INFO, CUST_TYPE_CD, 개인",
    )
    result = serialize_single_step(step)
    assert "개인" in result


def test_render_column_profile_empty_dict_returns_no_result():
    """빈 dict는 '결과 없음' 포함."""
    step = _step("get_column_profile", {}, input="TB_CUST_INFO, CUST_NO")
    result = serialize_single_step(step)
    assert "결과 없음" in result


def test_render_column_profile_wrong_type_returns_no_result():
    """list이면 '결과 없음' 포함 (dict 타입 검사)."""
    step = _step(
        "get_column_profile", [1, 2, 3], input="TB_CUST_INFO, CUST_NO",
    )
    result = serialize_single_step(step)
    assert "결과 없음" in result


def test_render_column_profile_with_stats():
    """통계 수치가 모두 포함되어야 한다."""
    raw = {
        "total_rows": 100000,
        "non_null_count": 99000,
        "null_rate": 0.01,
        "distinct_count": 5,
        "min_val": "01",
        "max_val": "03",
    }
    step = _step(
        "get_column_profile", raw, input="TB_CUST_INFO, CUST_TYPE_CD",
    )
    result = serialize_single_step(step)
    assert "100,000" in result
    assert "1.0%" in result
    assert "MIN" in result
    assert "MAX" in result
    assert "→" in result


def test_render_column_profile_null_rate_omitted_when_none():
    """null_rate가 없으면 비율(%) 표시 라인 생략."""
    raw = {"total_rows": 50, "non_null_count": 50, "distinct_count": 3}
    step = _step("get_column_profile", raw, input="TB_X, COL")
    result = serialize_single_step(step)
    # null_rate가 없으면 'X.X%' 형태의 비율 라인 없음
    import re
    assert not re.search(r"\d+\.\d+%", result)


def test_render_use_cases_empty():
    """use_cases가 빈 리스트이면 '결과 없음' 포함."""
    step = _step("search_use_cases", {"use_cases": []})
    result = serialize_single_step(step)
    assert "결과 없음" in result


def test_render_use_cases_wrong_type_returns_empty():
    """raw_result가 list이면 빈 문자열 반환 (dict 타입 검사)."""
    step = _step("search_use_cases", ["not", "a", "dict"])
    result = serialize_single_step(step)
    assert result == ""


def test_render_use_cases_with_entries():
    """유사 SQL이 있으면 설명·유사도·SQL 포함."""
    raw = {
        "use_cases": [
            {
                "description": "월별 신규 고객 수",
                "score": 0.93,
                "domain": "CUS",
                "sql": "SELECT COUNT(*) FROM TB_CUST_INFO",
                "enrichment_tables": [],
            }
        ]
    }
    step = _step("search_use_cases", raw, input="고객")
    result = serialize_single_step(step)
    assert "월별 신규 고객 수" in result
    assert "0.93" in result
    assert "SELECT COUNT" in result
    assert "→" in result


def test_render_use_cases_deduplicates_enrichment_tables():
    """같은 enrichment 테이블이 여러 use_case에 등장해도 중복 생략."""
    shared_table = {
        "table_name": "TB_CUST_INFO",
        "alt_name": "고객",
        "columns": [],
    }
    raw = {
        "use_cases": [
            {
                "description": "UC1",
                "score": 0.9,
                "sql": "SELECT 1 FROM T",
                "enrichment_tables": [shared_table],
            },
            {
                "description": "UC2",
                "score": 0.8,
                "sql": "SELECT 2 FROM T",
                "enrichment_tables": [shared_table],
            },
        ]
    }
    step = _step("search_use_cases", raw)
    result = serialize_single_step(step)
    assert "중복 생략" in result


def test_render_unknown_tool_fallback():
    """미등록 도구는 fallback 렌더러가 도구명과 렌더러 미등록 메시지 포함."""
    step = _step("unknown_future_tool", {"data": 123}, input="some_input")
    result = serialize_single_step(step)
    assert "unknown_future_tool" in result
    assert "렌더러 미등록" in result


def test_render_unknown_tool_in_batch_serialize():
    """미등록 도구도 배치 직렬화에서 정상적으로 블록에 포함."""
    steps = [_step("unknown_future_tool", {"data": 1})]
    result = serialize_tool_results_by_step(steps)
    assert "unknown_future_tool" in result
    assert result != "(도구 실행 결과 없음)"


def test_purpose_is_included_in_all_renderers():
    """모든 렌더러는 step.purpose를 결과 텍스트에 포함해야 한다."""
    cases = [
        ("search_table_meta", {"tables": []}),
        ("lookup_code_meta", []),
        ("search_biz_terms", []),
        ("search_manual", []),
        ("get_sample_rows", []),
        ("get_date_distribution", {}),
        ("get_column_values", []),
        ("search_use_cases", {"use_cases": []}),
    ]
    for tool, raw in cases:
        step = _step(tool, raw, purpose="고유한목적설명_ABC")
        result = serialize_single_step(step)
        assert "고유한목적설명_ABC" in result, (
            f"{tool} 렌더러가 purpose를 포함하지 않음"
        )
