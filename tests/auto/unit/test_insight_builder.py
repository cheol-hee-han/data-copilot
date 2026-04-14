"""insight_builder.build_insight 단위 테스트.

테스트 대상:
    - build_insight: PipelineState dict → 통찰 데이터 구성
    - 내부 헬퍼: _get_attr_or_key, _to_dict, _assess_confidence,
      _build_query_interpretation, _build_result_stats,
      _build_failure_narrative, _build_dead_end_trail,
      _build_reasoning_trail, _build_caveats, _calc_total_elapsed

설계 원칙:
    - LLM 호출 없음, DB 연결 없음
    - 최소 상태(빈 state)와 완전한 상태 양쪽 모두 검증

실행:
    pytest tests/auto/unit/test_insight_builder.py -v
"""

from __future__ import annotations

import pytest

from src.services.insight_builder import (
    build_insight,
    _get_attr_or_key,
    _to_dict,
    _assess_confidence,
    _build_query_interpretation,
    _build_result_stats,
    _build_failure_narrative,
    _build_dead_end_trail,
    _build_reasoning_trail,
    _calc_total_elapsed,
)
from src.models.enums import SelectionStatus


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 헬퍼 픽스처
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _minimal_state() -> dict:
    """최소 구성 state: reason 없음."""
    return {}


def _state_with_sql(sql: str = "SELECT 1 FROM DUAL") -> dict:
    """validated_sql이 있는 state."""
    return {
        "user_input": "고객 수 조회",
        "reason": {
            "validated_sql": sql,
            "generated_sql": sql,
            "explored_tables": [],
            "explored_use_cases": [],
            "knowledge_items": [],
            "execution_plan": [],
            "loop_guard": {"replan_count": 0, "generate_attempts": 1},
            "dead_ends": [],
            "validation_checks": {},
            "exploration_summary": "",
        },
        "normalized_query": {
            "period": "이번 달",
            "target": "고객",
            "metric": "건수",
        },
        "trace_log": [],
        "sql_result": None,
        "query_category": "DATA_EXTRACTION",
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# build_insight — 반환 구조 검증
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_build_insight_returns_dict():
    """build_insight는 항상 dict를 반환한다."""
    result = build_insight(_minimal_state())
    assert isinstance(result, dict)


def test_build_insight_has_required_keys():
    """반환 dict에 필수 키가 모두 포함되어야 한다."""
    required_keys = [
        "is_success", "query_interpretation", "reasoning_trail",
        "tables_used", "tables_candidate", "tables_rejected",
        "validation_detail", "sql_summary", "sql_code",
        "references", "confidence", "caveats",
        "total_elapsed", "step_timings", "result_stats",
        "failure_narrative", "dead_end_trail",
    ]
    result = build_insight(_minimal_state())
    for key in required_keys:
        assert key in result, f"필수 키 '{key}'가 없음"


def test_build_insight_minimal_state_is_not_success():
    """reason이 없는 최소 state는 is_success=False."""
    result = build_insight(_minimal_state())
    assert result["is_success"] is False


def test_build_insight_with_sql_is_success():
    """validated_sql이 있는 state는 is_success=True."""
    result = build_insight(_state_with_sql())
    assert result["is_success"] is True


def test_build_insight_no_reason_returns_empty_lists():
    """reason 없으면 tables_used, tables_candidate, tables_rejected가 빈 리스트."""
    result = build_insight(_minimal_state())
    assert result["tables_used"] == []
    assert result["tables_candidate"] == []
    assert result["tables_rejected"] == []
    assert result["reasoning_trail"] == []


def test_build_insight_no_sql_result_returns_empty_stats():
    """sql_result 없으면 result_stats가 빈 dict."""
    result = build_insight(_minimal_state())
    assert result["result_stats"] == {}


def test_build_insight_confidence_default():
    """reason 없으면 confidence='보통' 반환."""
    result = build_insight(_minimal_state())
    assert result["confidence"] == "보통"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _get_attr_or_key
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_get_attr_or_key_from_dict():
    obj = {"key": "value", "num": 42}
    assert _get_attr_or_key(obj, "key") == "value"
    assert _get_attr_or_key(obj, "num") == 42


def test_get_attr_or_key_dict_missing_key_returns_default():
    obj = {"key": "value"}
    assert _get_attr_or_key(obj, "missing", default="fallback") == "fallback"


def test_get_attr_or_key_from_object_attribute():
    class Obj:
        name = "test_name"
    assert _get_attr_or_key(Obj(), "name") == "test_name"


def test_get_attr_or_key_object_missing_attr_returns_default():
    class Obj:
        pass
    assert _get_attr_or_key(Obj(), "nonexistent", default=99) == 99


def test_get_attr_or_key_none_returns_default():
    assert _get_attr_or_key(None, "any_key", default="default_val") == "default_val"


def test_get_attr_or_key_none_default_is_none():
    assert _get_attr_or_key(None, "key") is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _to_dict
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_to_dict_from_dict():
    """dict 입력은 그대로 반환."""
    d = {"a": 1, "b": 2}
    assert _to_dict(d) == d


def test_to_dict_from_pydantic():
    """Pydantic 모델 입력은 model_dump() 결과 반환."""
    from pydantic import BaseModel

    class Sample(BaseModel):
        x: int = 5
        y: str = "hello"

    obj = Sample()
    result = _to_dict(obj)
    assert isinstance(result, dict)
    assert result["x"] == 5
    assert result["y"] == "hello"


def test_to_dict_from_other_type_returns_none():
    """dict도 Pydantic도 아닌 타입은 None 반환."""
    assert _to_dict("string") is None
    assert _to_dict(42) is None
    assert _to_dict([1, 2, 3]) is None


def test_to_dict_none_returns_none():
    assert _to_dict(None) is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _assess_confidence
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_assess_confidence_no_reason():
    """reason 없으면 '보통' 반환."""
    assert _assess_confidence(None) == "보통"


def test_assess_confidence_no_score():
    """confidence_score 없으면 '보통' 반환."""
    assert _assess_confidence({}) == "보통"


def test_assess_confidence_high():
    """score >= 0.8이면 '높음 (점수)'."""
    reason = {"confidence_score": 0.92}
    assert _assess_confidence(reason) == "높음 (0.92)"


def test_assess_confidence_high_boundary():
    """score == 0.8이면 '높음 (점수)'."""
    reason = {"confidence_score": 0.8}
    assert _assess_confidence(reason) == "높음 (0.80)"


def test_assess_confidence_medium():
    """0.5 <= score < 0.8이면 '보통 (점수)'."""
    reason = {"confidence_score": 0.65}
    assert _assess_confidence(reason) == "보통 (0.65)"


def test_assess_confidence_low():
    """score < 0.5이면 '낮음 (점수)'."""
    reason = {"confidence_score": 0.35}
    assert _assess_confidence(reason) == "낮음 (0.35)"


def test_assess_confidence_zero_score():
    """score == 0.0이면 폴백 '보통' 반환."""
    reason = {"confidence_score": 0.0}
    assert _assess_confidence(reason) == "보통"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _build_query_interpretation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_query_interpretation_from_dict_normalized():
    """normalized_query가 dict이면 period, target, metric 추출."""
    state = {
        "user_input": "이번 달 신규 고객 수",
        "query_category": "DATA_EXTRACTION",
    }
    normalized = {"period": "이번 달", "target": "고객", "metric": "건수"}
    result = _build_query_interpretation(state, normalized)
    assert result["original"] == "이번 달 신규 고객 수"
    assert result["period"] == "이번 달"
    assert result["target"] == "고객"
    assert result["metric"] == "건수"
    assert result["category"] == "DATA_EXTRACTION"


def test_query_interpretation_empty_normalized():
    """normalized_query가 빈 dict이면 빈 문자열 기본값."""
    state = {"user_input": "질의", "query_category": ""}
    result = _build_query_interpretation(state, {})
    assert result["period"] == ""
    assert result["target"] == ""
    assert result["metric"] == ""


def test_query_interpretation_none_normalized():
    """normalized_query가 None이면 빈 값 반환."""
    state = {"user_input": "질의", "query_category": ""}
    result = _build_query_interpretation(state, None)
    assert result["period"] == ""
    assert result["metric"] == ""


def test_query_interpretation_pydantic_normalized():
    """normalized_query가 Pydantic 모델이면 model_dump() 경로로 처리."""
    from pydantic import BaseModel

    class FakeNormalized(BaseModel):
        period: str = "2024년 3월"
        target: str = "여신"
        metric: str = "잔액"

    state = {"user_input": "여신 잔액", "query_category": "DATA_EXTRACTION"}
    result = _build_query_interpretation(state, FakeNormalized())
    assert result["period"] == "2024년 3월"
    assert result["target"] == "여신"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _build_result_stats
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_result_stats_none_returns_empty():
    assert _build_result_stats(None) == {}


def test_result_stats_false_returns_empty():
    assert _build_result_stats(False) == {}


def test_result_stats_dict_input():
    """dict sql_result에서 row_count, column_count, execution_time_ms 추출."""
    sql_result = {
        "row_count": 150,
        "columns": ["COL_A", "COL_B", "COL_C"],
        "execution_time_ms": 342.5,
    }
    result = _build_result_stats(sql_result)
    assert result["row_count"] == 150
    assert result["column_count"] == 3
    assert result["execution_time_ms"] == 342.5


def test_result_stats_zero_rows():
    """빈 결과셋도 정상 처리."""
    sql_result = {"row_count": 0, "columns": [], "execution_time_ms": 12.0}
    result = _build_result_stats(sql_result)
    assert result["row_count"] == 0
    assert result["column_count"] == 0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _build_failure_narrative
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_failure_narrative_no_reason_uses_error_message():
    """reason 없으면 state.error_message 반환."""
    state = {"error_message": "DB 연결 실패"}
    assert _build_failure_narrative(state, None) == "DB 연결 실패"


def test_failure_narrative_no_reason_no_error_empty():
    """reason과 error_message 모두 없으면 빈 문자열."""
    assert _build_failure_narrative({}, None) == ""


def test_failure_narrative_with_exploration_summary():
    """exploration_summary가 있으면 그것을 반환."""
    reason = {"exploration_summary": "테이블을 찾지 못했습니다."}
    state = {"error_message": "무시됨"}
    result = _build_failure_narrative(state, reason)
    assert result == "테이블을 찾지 못했습니다."


def test_failure_narrative_empty_summary_falls_back_to_error():
    """exploration_summary가 빈 문자열이면 error_message 폴백."""
    reason = {"exploration_summary": ""}
    state = {"error_message": "SQL 생성 실패"}
    result = _build_failure_narrative(state, reason)
    assert result == "SQL 생성 실패"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _build_dead_end_trail
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_dead_end_trail_no_reason():
    assert _build_dead_end_trail(None) == []


def test_dead_end_trail_empty():
    assert _build_dead_end_trail({"dead_ends": []}) == []


def test_dead_end_trail_with_entries():
    """dead_ends 항목이 trail 형식으로 변환되어야 한다."""
    reason = {
        "dead_ends": [
            {
                "failure_type": "NO_TABLE",
                "reason": "관련 테이블 없음",
                "lessons_learned": "다른 키워드로 재시도",
            }
        ]
    }
    result = _build_dead_end_trail(reason)
    assert len(result) == 1
    assert result[0]["failure_type"] == "NO_TABLE"
    assert result[0]["reason"] == "관련 테이블 없음"
    assert result[0]["lessons_learned"] == "다른 키워드로 재시도"


def test_dead_end_trail_multiple_entries():
    """복수 dead_ends가 모두 포함되어야 한다."""
    reason = {
        "dead_ends": [
            {"failure_type": "NO_TABLE", "reason": "A", "lessons_learned": ""},
            {"failure_type": "SQL_SYNTAX", "reason": "B", "lessons_learned": ""},
        ]
    }
    result = _build_dead_end_trail(reason)
    assert len(result) == 2


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _build_reasoning_trail
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_reasoning_trail_no_reason():
    assert _build_reasoning_trail(None) == []


def test_reasoning_trail_empty_plan():
    assert _build_reasoning_trail({"execution_plan": []}) == []


def test_reasoning_trail_skips_steps_without_insight():
    """insight가 없는 스텝은 trail에 포함되지 않는다."""
    reason = {
        "execution_plan": [
            {"tool": "search_table_meta", "insight": None},
            {"tool": "lookup_code_meta", "insight": ""},
        ]
    }
    result = _build_reasoning_trail(reason)
    assert result == []


def test_reasoning_trail_includes_steps_with_insight():
    """insight가 있는 스텝만 trail에 포함."""
    reason = {
        "execution_plan": [
            {"tool": "search_table_meta", "insight": "TB_CUST_INFO 적합"},
            {"tool": "lookup_code_meta", "insight": ""},
            {"tool": "search_use_cases", "insight": "유사 SQL 발견"},
        ]
    }
    result = _build_reasoning_trail(reason)
    assert len(result) == 2
    texts = [r["text"] for r in result]
    assert "TB_CUST_INFO 적합" in texts
    assert "유사 SQL 발견" in texts


def test_reasoning_trail_warning_flag():
    """경고 키워드가 포함된 insight는 warning=True."""
    reason = {
        "execution_plan": [
            {"tool": "search_table_meta", "insight": "테이블 부재로 정보 없음"},
        ]
    }
    result = _build_reasoning_trail(reason)
    assert len(result) == 1
    assert result[0]["warning"] is True


def test_reasoning_trail_no_warning_flag():
    """경고 키워드 없는 insight는 warning=False."""
    reason = {
        "execution_plan": [
            {"tool": "search_table_meta", "insight": "TB_CUST_INFO 적합 판정"},
        ]
    }
    result = _build_reasoning_trail(reason)
    assert result[0]["warning"] is False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _calc_total_elapsed
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_calc_elapsed_empty():
    assert _calc_total_elapsed([]) == 0.0


def test_calc_elapsed_single_entry():
    assert _calc_total_elapsed([{"timestamp": "2024-01-01T00:00:00"}]) == 0.0


def test_calc_elapsed_two_entries():
    """두 개의 timestamp가 있으면 차이를 초 단위로 반환."""
    trace_log = [
        {"timestamp": "2024-01-01T00:00:00", "node": "preprocess"},
        {"timestamp": "2024-01-01T00:00:05", "node": "classify_intent"},
    ]
    result = _calc_total_elapsed(trace_log)
    assert result == pytest.approx(5.0, abs=0.1)


def test_calc_elapsed_multiple_entries():
    """첫 번째와 마지막 timestamp 차이를 반환."""
    trace_log = [
        {"timestamp": "2024-01-01T00:00:00", "node": "a"},
        {"timestamp": "2024-01-01T00:00:03", "node": "b"},
        {"timestamp": "2024-01-01T00:00:10", "node": "c"},
    ]
    result = _calc_total_elapsed(trace_log)
    assert result == pytest.approx(10.0, abs=0.1)


def test_calc_elapsed_invalid_timestamps():
    """파싱 불가능한 timestamp가 있으면 0.0 반환."""
    trace_log = [
        {"timestamp": "invalid", "node": "a"},
        {"timestamp": "also_invalid", "node": "b"},
    ]
    result = _calc_total_elapsed(trace_log)
    assert result == 0.0


def test_calc_elapsed_entries_without_timestamp():
    """timestamp 없는 항목은 무시."""
    trace_log = [
        {"node": "a"},
        {"timestamp": "2024-01-01T00:00:00", "node": "b"},
    ]
    result = _calc_total_elapsed(trace_log)
    assert result == 0.0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# build_insight — 테이블 분류 검증
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_build_insight_tables_used_empty_when_no_sql_tables():
    """explored_tables가 있어도 sql_tables가 비어 있으면 tables_used=[]."""
    state = {
        "reason": {
            "validated_sql": "",
            "generated_sql": "",
            "explored_tables": [
                {"table_name": "TB_CUST_INFO", "alt_name": "고객", "selection_status": "SELECTED",
                 "description": "", "selection_reason": "", "columns_used": []}
            ],
            "explored_use_cases": [],
            "knowledge_items": [],
            "execution_plan": [],
            "loop_guard": {"replan_count": 0, "generate_attempts": 1},
            "dead_ends": [],
            "validation_checks": {},
            "exploration_summary": "",
        }
    }
    result = build_insight(state)
    assert result["tables_used"] == []


def test_build_insight_tables_rejected_classification():
    """REJECTED 상태 테이블은 tables_rejected에 포함."""
    state = {
        "reason": {
            "validated_sql": "",
            "generated_sql": "",
            "explored_tables": [
                {
                    "table_name": "TB_UNWANTED",
                    "alt_name": "불필요",
                    "selection_status": SelectionStatus.REJECTED,
                    "description": "관련 없는 테이블",
                    "selection_reason": "질의와 무관",
                    "columns_used": [],
                }
            ],
            "explored_use_cases": [],
            "knowledge_items": [],
            "execution_plan": [],
            "loop_guard": {"replan_count": 0, "generate_attempts": 1},
            "dead_ends": [],
            "validation_checks": {},
            "exploration_summary": "",
        }
    }
    result = build_insight(state)
    assert len(result["tables_rejected"]) == 1
    assert result["tables_rejected"][0]["name"] == "TB_UNWANTED"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# build_insight — caveats (주의사항)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_build_insight_caveats_replan_warning():
    """replan_count > 0이면 caveats에 재시도 안내 포함."""
    state = {
        "reason": {
            "validated_sql": "SELECT 1",
            "explored_tables": [],
            "explored_use_cases": [],
            "knowledge_items": [],
            "execution_plan": [],
            "loop_guard": {"replan_count": 1, "generate_attempts": 2},
            "dead_ends": [],
            "validation_checks": {},
            "exploration_summary": "",
        }
    }
    result = build_insight(state)
    assert any("다른 접근" in c for c in result["caveats"])


def test_build_insight_caveats_multi_table_warning():
    """선택된 테이블이 2개 이상이면 JOIN 주의사항 포함."""
    state = {
        "reason": {
            "validated_sql": "SELECT 1",
            "explored_tables": [
                {"table_name": "TB_A", "alt_name": "", "selection_status": SelectionStatus.SELECTED,
                 "description": "", "selection_reason": "", "columns_used": []},
                {"table_name": "TB_B", "alt_name": "", "selection_status": SelectionStatus.SELECTED,
                 "description": "", "selection_reason": "", "columns_used": []},
            ],
            "explored_use_cases": [],
            "knowledge_items": [],
            "execution_plan": [],
            "loop_guard": {"replan_count": 0, "generate_attempts": 1},
            "dead_ends": [],
            "validation_checks": {},
            "exploration_summary": "",
        }
    }
    result = build_insight(state)
    assert any("다중 테이블" in c for c in result["caveats"])


def test_build_insight_caveats_dead_ends_warning():
    """dead_ends가 있으면 우회 안내 포함."""
    state = {
        "reason": {
            "validated_sql": "SELECT 1",
            "explored_tables": [],
            "explored_use_cases": [],
            "knowledge_items": [],
            "execution_plan": [],
            "loop_guard": {"replan_count": 0, "generate_attempts": 1},
            "dead_ends": [
                {"failure_type": "NO_TABLE", "reason": "A", "lessons_learned": ""}
            ],
            "validation_checks": {},
            "exploration_summary": "",
        }
    }
    result = build_insight(state)
    assert any("막다른" in c for c in result["caveats"])


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# build_insight — references
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_build_insight_references_with_use_cases():
    """explored_use_cases가 있으면 references에 use_cases 항목 포함."""
    state = {
        "reason": {
            "validated_sql": "SELECT 1",
            "explored_tables": [],
            "explored_use_cases": [
                {"description": "유사 SQL", "sql": "SELECT 1", "relevant": True}
            ],
            "knowledge_items": [],
            "execution_plan": [],
            "loop_guard": {"replan_count": 0, "generate_attempts": 1},
            "dead_ends": [],
            "validation_checks": {},
            "exploration_summary": "",
        }
    }
    result = build_insight(state)
    sources = [r["source"] for r in result["references"]]
    assert "use_cases" in sources


def test_build_insight_references_empty_when_no_data():
    """탐색 데이터가 없으면 references가 빈 리스트."""
    state = {
        "reason": {
            "validated_sql": "SELECT 1",
            "explored_tables": [],
            "explored_use_cases": [],
            "knowledge_items": [],
            "execution_plan": [],
            "loop_guard": {"replan_count": 0, "generate_attempts": 1},
            "dead_ends": [],
            "validation_checks": {},
            "exploration_summary": "",
        }
    }
    result = build_insight(state)
    assert result["references"] == []
