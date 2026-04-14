"""state.py 헬퍼 메서드 단위 테스트.

테스트 대상:
    - TableMeta.from_meta() 팩토리 메서드
    - TableMeta.qualified_name 프로퍼티
    - LoopGuard 카운터 메서드 및 should_escalate_to_structural
    - StructuralHints.is_empty() 및 to_prompt_text()
    - should_terminate() 함수
    - ReasoningState.get_unresolved_knowledge(), get_pending_hypotheses()
    - KnowledgeItem.promote() 메서드

테스트 대상 소스: src/agents/state/state.py
외부 의존성 없음 (ConnectorManager.parse_db_source는 순수 정적 함수).
"""

from __future__ import annotations

import pytest

from src.agents.state.state import (
    ConfidenceStatus,
    DeadEnd,
    FailureType,
    FinalStatus,
    Hypothesis,
    HypothesisStatus,
    KnowledgeItem,
    LoopGuard,
    ReasoningState,
    StructuralHints,
    TableMeta,
    should_terminate,
)
from src.config import settings as _settings


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. TableMeta.from_meta() 팩토리 메서드
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestTableMetaFromMeta:
    """TableMeta.from_meta() — 다양한 입력 패턴 검증."""

    def test_full_valid_meta(self):
        """모든 필드가 채워진 정상 메타 dict."""
        meta = {
            "name": "TB_ADW_CUST001M",
            "alt_name": "고객기본",
            "description": "고객 기본 정보 테이블",
            "schema_name": "dbo",
            "subject_area": "고객",
            "columns": [
                {
                    "name": "CUST_NO",
                    "alt_name": "고객번호",
                    "description": "고객 식별 번호",
                    "type": "VARCHAR",
                    "is_pk": True,
                },
                {
                    "name": "CUST_NM",
                    "alt_name": "고객명",
                    "description": "고객 성명",
                    "type": "VARCHAR",
                    "is_pk": False,
                },
            ],
        }
        result = TableMeta.from_meta(meta)
        assert result is not None
        assert result.table_name == "TB_ADW_CUST001M"
        assert result.alt_name == "고객기본"
        assert result.description == "고객 기본 정보 테이블"
        assert result.schema_name == "dbo"
        assert result.subject_area == "고객"
        assert len(result.columns) == 2
        assert result.columns[0].name == "CUST_NO"
        assert result.columns[0].is_pk is True
        assert result.columns[1].name == "CUST_NM"
        assert result.columns[1].is_pk is False

    def test_legacy_table_name_field(self):
        """하위 호환 — 'table_name' 키를 fallback으로 사용."""
        meta = {
            "table_name": "TB_ADW_LOAN001M",
            "table_description": "여신기본",
        }
        result = TableMeta.from_meta(meta)
        assert result is not None
        assert result.table_name == "TB_ADW_LOAN001M"
        assert result.description == "여신기본"

    def test_name_takes_priority_over_table_name(self):
        """'name'이 'table_name'보다 우선 적용된다."""
        meta = {
            "name": "TB_ADW_CUST_NEW",
            "table_name": "TB_ADW_CUST_OLD",
        }
        result = TableMeta.from_meta(meta)
        assert result is not None
        assert result.table_name == "TB_ADW_CUST_NEW"

    def test_missing_table_name_returns_none(self):
        """테이블명 없으면 None 반환."""
        result = TableMeta.from_meta({})
        assert result is None

    def test_empty_string_name_returns_none(self):
        """빈 문자열 name → None 반환."""
        result = TableMeta.from_meta({"name": "", "table_name": ""})
        assert result is None

    def test_name_empty_fallback_to_table_name(self):
        """name이 빈 문자열이면 table_name으로 대체."""
        meta = {"name": "", "table_name": "TB_BDP_LCT001L"}
        result = TableMeta.from_meta(meta)
        assert result is not None
        assert result.table_name == "TB_BDP_LCT001L"

    def test_columns_empty_list(self):
        """컬럼이 없는 경우 빈 columns 반환."""
        meta = {"name": "TB_ADW_EMPTY", "columns": []}
        result = TableMeta.from_meta(meta)
        assert result is not None
        assert result.columns == []

    def test_columns_missing(self):
        """columns 키 자체 없으면 빈 columns 반환."""
        meta = {"name": "TB_ADW_NOCOL"}
        result = TableMeta.from_meta(meta)
        assert result is not None
        assert result.columns == []

    def test_columns_skip_entries_without_name(self):
        """컬럼 dict에 'name'이 없으면 해당 항목을 스킵."""
        meta = {
            "name": "TB_ADW_PARTIAL",
            "columns": [
                {"alt_name": "이름없는컬럼"},    # name 없음 → 스킵
                {"name": "VALID_COL"},           # 정상
            ],
        }
        result = TableMeta.from_meta(meta)
        assert result is not None
        assert len(result.columns) == 1
        assert result.columns[0].name == "VALID_COL"

    def test_columns_non_list_ignored(self):
        """columns가 리스트가 아닌 경우 — 빈 columns 반환."""
        meta = {"name": "TB_ADW_BADCOL", "columns": "not_a_list"}
        result = TableMeta.from_meta(meta)
        assert result is not None
        assert result.columns == []

    def test_db_source_adw_parsed(self):
        """TB_ADW_* 형태 테이블명 → db_source='ADW'."""
        meta = {"name": "TB_ADW_CUST001M"}
        result = TableMeta.from_meta(meta)
        assert result is not None
        assert result.db_source == "ADW"

    def test_db_source_bdp_parsed(self):
        """TB_BDP_* 형태 테이블명 → db_source='BDP'."""
        meta = {"name": "TB_BDP_LCT001L"}
        result = TableMeta.from_meta(meta)
        assert result is not None
        assert result.db_source == "BDP"

    def test_db_source_unknown_empty(self):
        """알 수 없는 시스템코드 → db_source 빈 문자열."""
        meta = {"name": "TB_XYZ_CUST001M"}
        result = TableMeta.from_meta(meta)
        assert result is not None
        assert result.db_source == ""

    def test_description_fallback_from_table_description(self):
        """description 없고 table_description만 있으면 fallback."""
        meta = {
            "name": "TB_ADW_TST",
            "table_description": "레거시 설명",
        }
        result = TableMeta.from_meta(meta)
        assert result is not None
        assert result.description == "레거시 설명"

    def test_description_priority_over_table_description(self):
        """description이 있으면 table_description보다 우선."""
        meta = {
            "name": "TB_ADW_TST",
            "description": "신규 설명",
            "table_description": "레거시 설명",
        }
        result = TableMeta.from_meta(meta)
        assert result is not None
        assert result.description == "신규 설명"

    def test_column_type_mapped_to_col_type(self):
        """컬럼 'type' 키가 ColumnInfo.col_type으로 매핑된다."""
        meta = {
            "name": "TB_ADW_TST",
            "columns": [{"name": "AMT", "type": "DECIMAL"}],
        }
        result = TableMeta.from_meta(meta)
        assert result is not None
        assert result.columns[0].col_type == "DECIMAL"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. TableMeta.qualified_name 프로퍼티
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestTableMetaQualifiedName:
    """TableMeta.qualified_name — 스키마.테이블 조합 검증."""

    def test_with_schema(self):
        """스키마명이 있으면 'schema.table' 형태 반환."""
        t = TableMeta(table_name="TB_CUST", schema_name="dbo")
        assert t.qualified_name == "dbo.TB_CUST"

    def test_without_schema(self):
        """스키마명 없으면 테이블명만 반환."""
        t = TableMeta(table_name="TB_CUST")
        assert t.qualified_name == "TB_CUST"

    def test_empty_schema_returns_table_only(self):
        """schema_name이 빈 문자열이면 테이블명만 반환."""
        t = TableMeta(table_name="TB_LOAN", schema_name="")
        assert t.qualified_name == "TB_LOAN"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. LoopGuard 카운터 메서드
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestLoopGuardIncrements:
    """LoopGuard 카운터 증가 동작 검증."""

    def test_initial_all_zero(self):
        """초기값은 모두 0."""
        g = LoopGuard()
        assert g.total_tool_calls == 0
        assert g.replan_count == 0
        assert g.generate_attempts == 0
        assert g.local_fix_count == 0

    def test_increment_tool_calls(self):
        g = LoopGuard()
        g.increment_tool_calls()
        assert g.total_tool_calls == 1
        g.increment_tool_calls()
        assert g.total_tool_calls == 2

    def test_increment_replan(self):
        g = LoopGuard()
        g.increment_replan()
        assert g.replan_count == 1

    def test_increment_generate(self):
        g = LoopGuard()
        g.increment_generate()
        assert g.generate_attempts == 1

    def test_increment_local_fix(self):
        g = LoopGuard()
        g.increment_local_fix()
        assert g.local_fix_count == 1

    def test_multiple_increments_independent(self):
        """각 카운터는 독립적으로 증가."""
        g = LoopGuard()
        for _ in range(3):
            g.increment_tool_calls()
        for _ in range(2):
            g.increment_replan()
        g.increment_generate()
        assert g.total_tool_calls == 3
        assert g.replan_count == 2
        assert g.generate_attempts == 1
        assert g.local_fix_count == 0

    def test_should_escalate_false_below_limit(self):
        """local_fix_count가 한도 미만이면 False."""
        g = LoopGuard()
        max_fixes = _settings.max_local_fixes
        for _ in range(max_fixes - 1):
            g.increment_local_fix()
        assert g.should_escalate_to_structural() is False

    def test_should_escalate_true_at_limit(self):
        """local_fix_count가 한도(max_local_fixes)에 도달하면 True."""
        g = LoopGuard()
        max_fixes = _settings.max_local_fixes
        for _ in range(max_fixes):
            g.increment_local_fix()
        assert g.should_escalate_to_structural() is True

    def test_should_escalate_true_beyond_limit(self):
        """한도 초과 시에도 True 유지."""
        g = LoopGuard()
        max_fixes = _settings.max_local_fixes
        for _ in range(max_fixes + 5):
            g.increment_local_fix()
        assert g.should_escalate_to_structural() is True

    def test_should_escalate_false_initial(self):
        """초기 상태에서는 항상 False (max_local_fixes >= 1 가정)."""
        g = LoopGuard()
        assert g.should_escalate_to_structural() is False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. StructuralHints.is_empty() 및 to_prompt_text()
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestStructuralHintsIsEmpty:
    """StructuralHints.is_empty() 다양한 필드 조합 검증."""

    def test_fully_empty(self):
        """아무 필드도 없으면 True."""
        h = StructuralHints()
        assert h.is_empty() is True

    def test_join_patterns_makes_not_empty(self):
        h = StructuralHints(join_patterns=["TB_A JOIN TB_B ON A.ID = B.ID"])
        assert h.is_empty() is False

    def test_code_columns_makes_not_empty(self):
        h = StructuralHints(code_columns={"LOAN_STS_CD": ["01", "02"]})
        assert h.is_empty() is False

    def test_agg_expressions_makes_not_empty(self):
        h = StructuralHints(agg_expressions=["SUM(AMT)", "COUNT(*)"])
        assert h.is_empty() is False

    def test_date_filters_makes_not_empty(self):
        h = StructuralHints(
            date_filters=[{"column": "STDR_YMD", "format": "YYYYMMDD"}],
        )
        assert h.is_empty() is False

    def test_source_tables_makes_not_empty(self):
        h = StructuralHints(source_tables=["TB_ADW_LOAN001M"])
        assert h.is_empty() is False

    def test_only_select_columns_still_empty(self):
        """select_columns만 있으면 is_empty()는 True (판정 기준 외)."""
        h = StructuralHints(select_columns=["CUST_NO", "AMT"])
        assert h.is_empty() is True

    def test_only_group_by_still_empty(self):
        """group_by_columns만 있으면 is_empty()는 True (판정 기준 외)."""
        h = StructuralHints(group_by_columns=["BRANCH_CD"])
        assert h.is_empty() is True

    def test_limit_value_does_not_affect_empty(self):
        """limit_value만 있으면 is_empty()는 True."""
        h = StructuralHints(limit_value=100)
        assert h.is_empty() is True

    def test_has_distinct_does_not_affect_empty(self):
        h = StructuralHints(has_distinct=True)
        assert h.is_empty() is True


class TestStructuralHintsToPromptText:
    """StructuralHints.to_prompt_text() 출력 내용 검증."""

    def test_empty_returns_empty_string(self):
        """아무것도 없으면 빈 문자열."""
        h = StructuralHints()
        assert h.to_prompt_text() == ""

    def test_source_tables_included(self):
        h = StructuralHints(source_tables=["TB_ADW_CUST001M", "TB_ADW_LOAN001M"])
        text = h.to_prompt_text()
        assert "활용사례 테이블" in text
        assert "TB_ADW_CUST001M" in text
        assert "TB_ADW_LOAN001M" in text

    def test_join_patterns_included(self):
        h = StructuralHints(join_patterns=["TB_A JOIN TB_B ON A.ID = B.ID"])
        text = h.to_prompt_text()
        assert "검증된 조인 패턴" in text
        assert "TB_A JOIN TB_B" in text

    def test_code_columns_included(self):
        h = StructuralHints(
            code_columns={"LOAN_STS_CD": ["01", "02"]},
        )
        text = h.to_prompt_text()
        assert "과거 사용된 코드값" in text
        assert "LOAN_STS_CD" in text
        assert "'01'" in text
        assert "'02'" in text

    def test_agg_expressions_included(self):
        h = StructuralHints(agg_expressions=["SUM(LOAN_AMT)", "COUNT(*)"])
        text = h.to_prompt_text()
        assert "유사 질의 집계 방식" in text
        assert "SUM(LOAN_AMT)" in text

    def test_select_columns_included(self):
        h = StructuralHints(select_columns=["CUST_NO", "LOAN_AMT"])
        text = h.to_prompt_text()
        assert "유사 질의 출력 컬럼" in text
        assert "CUST_NO" in text

    def test_group_by_columns_included(self):
        h = StructuralHints(group_by_columns=["BRANCH_CD", "PROD_CD"])
        text = h.to_prompt_text()
        assert "유사 질의 GROUP BY" in text
        assert "BRANCH_CD" in text

    def test_date_filters_included(self):
        h = StructuralHints(
            date_filters=[{"column": "STDR_YMD", "format": "YYYYMMDD"}],
        )
        text = h.to_prompt_text()
        assert "날짜 조건" in text
        assert "STDR_YMD" in text
        assert "YYYYMMDD" in text

    def test_date_filters_missing_keys_uses_question_mark(self):
        """column, format 키 없으면 '?' 대체."""
        h = StructuralHints(date_filters=[{}])
        text = h.to_prompt_text()
        assert "?" in text

    def test_all_fields_combined(self):
        """모든 필드가 있을 때 각 섹션이 모두 포함."""
        h = StructuralHints(
            source_tables=["TB_ADW_CUST001M"],
            join_patterns=["TB_A JOIN TB_B ON A.ID = B.ID"],
            code_columns={"STS_CD": ["01"]},
            agg_expressions=["COUNT(*)"],
            select_columns=["CUST_NO"],
            group_by_columns=["BRANCH_CD"],
            date_filters=[{"column": "STDR_YMD", "format": "YYYYMMDD"}],
        )
        text = h.to_prompt_text()
        assert "활용사례 테이블" in text
        assert "검증된 조인 패턴" in text
        assert "과거 사용된 코드값" in text
        assert "유사 질의 집계 방식" in text
        assert "유사 질의 출력 컬럼" in text
        assert "유사 질의 GROUP BY" in text
        assert "날짜 조건" in text

    def test_multiple_code_columns(self):
        """코드 컬럼이 복수일 때 세미콜론으로 구분."""
        h = StructuralHints(
            code_columns={
                "LOAN_STS_CD": ["01", "02"],
                "PROD_CD": ["A"],
            },
        )
        text = h.to_prompt_text()
        assert "LOAN_STS_CD" in text
        assert "PROD_CD" in text


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. should_terminate() 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestShouldTerminate:
    """should_terminate() — 5가지 종료 조건 검증."""

    def _reason_with_pending_hypothesis(self) -> ReasoningState:
        """PENDING 가설이 한 개 있는 ReasoningState를 반환한다."""
        return ReasoningState(
            hypotheses=[Hypothesis(hypothesis_id="H1", description="test")],
            current_hypothesis=None,
        )

    def test_initial_state_not_terminated(self):
        """초기 상태에서는 종료하지 않는다.

        단, 가설이 없으면 소진 조건이 걸리므로 PENDING 가설을 주입한다.
        """
        reason = self._reason_with_pending_hypothesis()
        assert should_terminate(reason) is False

    def test_terminates_when_tool_calls_at_limit(self):
        """total_tool_calls >= MAX_TOOL_CALLS 이면 종료."""
        reason = self._reason_with_pending_hypothesis()
        reason.loop_guard.total_tool_calls = _settings.max_tool_calls
        assert should_terminate(reason) is True

    def test_terminates_when_tool_calls_exceeds_limit(self):
        """한도 초과 시에도 종료."""
        reason = self._reason_with_pending_hypothesis()
        reason.loop_guard.total_tool_calls = _settings.max_tool_calls + 1
        assert should_terminate(reason) is True

    def test_not_terminated_one_below_tool_calls_limit(self):
        """한도 -1이면 종료하지 않는다."""
        reason = self._reason_with_pending_hypothesis()
        reason.loop_guard.total_tool_calls = _settings.max_tool_calls - 1
        assert should_terminate(reason) is False

    def test_terminates_when_replan_at_limit(self):
        """replan_count >= MAX_REPLANS 이면 종료."""
        reason = self._reason_with_pending_hypothesis()
        reason.loop_guard.replan_count = _settings.max_replans
        assert should_terminate(reason) is True

    def test_not_terminated_one_below_replan_limit(self):
        reason = self._reason_with_pending_hypothesis()
        reason.loop_guard.replan_count = _settings.max_replans - 1
        assert should_terminate(reason) is False

    def test_generate_limit_disabled_when_zero(self):
        """max_generates == 0 이면 generate_attempts 조건이 비활성화된다."""
        reason = self._reason_with_pending_hypothesis()
        reason.loop_guard.generate_attempts = 100  # 아무리 높아도
        # max_generates 기본값이 0이므로 이 조건만으로는 종료되지 않음
        assert _settings.max_generates == 0
        assert should_terminate(reason) is False

    def test_terminates_when_generate_at_limit_if_positive(self, monkeypatch):
        """max_generates > 0 이면 generate_attempts 한도에서 종료."""
        import src.agents.state.state as state_mod
        monkeypatch.setattr(state_mod, "MAX_GENERATES", 5)
        reason = self._reason_with_pending_hypothesis()
        reason.loop_guard.generate_attempts = 5
        assert should_terminate(reason) is True

    def test_terminates_when_final_status_failure(self):
        """final_status == FAILURE 이면 종료."""
        reason = self._reason_with_pending_hypothesis()
        reason.final_status = FinalStatus.FAILURE
        assert should_terminate(reason) is True

    def test_not_terminated_when_final_status_success(self):
        """final_status == SUCCESS는 종료 조건이 아니다."""
        reason = self._reason_with_pending_hypothesis()
        reason.final_status = FinalStatus.SUCCESS
        # SUCCESS는 should_terminate의 조건이 아님 — 다른 조건도 없으면 False
        assert should_terminate(reason) is False

    def test_terminates_when_no_pending_hypotheses_and_no_current(self):
        """PENDING 가설도 없고 current_hypothesis도 None이면 가설 소진으로 종료."""
        reason = ReasoningState(
            hypotheses=[],
            current_hypothesis=None,
        )
        assert should_terminate(reason) is True

    def test_not_terminated_when_current_hypothesis_set(self):
        """PENDING 가설이 없어도 current_hypothesis가 있으면 종료하지 않는다."""
        reason = ReasoningState(
            hypotheses=[],
            current_hypothesis=Hypothesis(hypothesis_id="H1", description="active"),
        )
        assert should_terminate(reason) is False

    def test_not_terminated_when_pending_hypotheses_remain(self):
        """PENDING 가설이 남아있으면 종료하지 않는다."""
        reason = ReasoningState(
            hypotheses=[
                Hypothesis(
                    hypothesis_id="H1",
                    description="test",
                    status=HypothesisStatus.PENDING,
                ),
            ],
            current_hypothesis=None,
        )
        assert should_terminate(reason) is False

    def test_terminates_when_all_hypotheses_not_pending(self):
        """모든 가설이 FAILED 또는 SUCCESS이면 PENDING 없음 → 종료."""
        reason = ReasoningState(
            hypotheses=[
                Hypothesis(
                    hypothesis_id="H1",
                    description="test",
                    status=HypothesisStatus.FAILED,
                ),
                Hypothesis(
                    hypothesis_id="H2",
                    description="test2",
                    status=HypothesisStatus.SUCCESS,
                ),
            ],
            current_hypothesis=None,
        )
        assert should_terminate(reason) is True

    def test_multiple_conditions_any_triggers_termination(self):
        """여러 조건이 동시에 충족될 때도 True."""
        reason = ReasoningState(
            hypotheses=[],
            current_hypothesis=None,
            final_status=FinalStatus.FAILURE,
        )
        reason.loop_guard.total_tool_calls = _settings.max_tool_calls
        assert should_terminate(reason) is True


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 6. ReasoningState.get_unresolved_knowledge()
#    ReasoningState.get_pending_hypotheses()
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestReasoningStateHelpers:
    """get_unresolved_knowledge 및 get_pending_hypotheses 필터링 검증."""

    # ── get_unresolved_knowledge ──────────────────────────

    def test_get_unresolved_empty(self):
        """지식 항목 없으면 빈 리스트."""
        reason = ReasoningState()
        assert reason.get_unresolved_knowledge() == []

    def test_get_unresolved_filters_correctly(self):
        """UNRESOLVED 상태만 반환, CONFIRMED 제외."""
        reason = ReasoningState(
            knowledge_items=[
                KnowledgeItem(
                    key="K1",
                    status=ConfidenceStatus.UNRESOLVED,
                ),
                KnowledgeItem(
                    key="K2",
                    status=ConfidenceStatus.CONFIRMED,
                ),
                KnowledgeItem(
                    key="K3",
                    status=ConfidenceStatus.UNRESOLVED,
                ),
            ],
        )
        result = reason.get_unresolved_knowledge()
        keys = [ki.key for ki in result]
        assert "K1" in keys
        assert "K3" in keys
        assert "K2" not in keys

    def test_get_unresolved_all_confirmed(self):
        """모두 CONFIRMED면 빈 리스트."""
        reason = ReasoningState(
            knowledge_items=[
                KnowledgeItem(
                    key="K1",
                    status=ConfidenceStatus.CONFIRMED,
                ),
            ],
        )
        assert reason.get_unresolved_knowledge() == []

    def test_get_unresolved_all_unresolved(self):
        """모두 UNRESOLVED면 전체 반환."""
        reason = ReasoningState(
            knowledge_items=[
                KnowledgeItem(key="K1", status=ConfidenceStatus.UNRESOLVED),
                KnowledgeItem(key="K2", status=ConfidenceStatus.UNRESOLVED),
            ],
        )
        assert len(reason.get_unresolved_knowledge()) == 2

    # ── get_pending_hypotheses ────────────────────────────

    def test_get_pending_hypotheses_empty(self):
        """가설 없으면 빈 리스트."""
        reason = ReasoningState()
        assert reason.get_pending_hypotheses() == []

    def test_get_pending_hypotheses_filters_correctly(self):
        """PENDING 상태만 반환, ACTIVE/FAILED/SUCCESS 제외."""
        reason = ReasoningState(
            hypotheses=[
                Hypothesis(
                    hypothesis_id="H1",
                    description="d1",
                    status=HypothesisStatus.PENDING,
                ),
                Hypothesis(
                    hypothesis_id="H2",
                    description="d2",
                    status=HypothesisStatus.ACTIVE,
                ),
                Hypothesis(
                    hypothesis_id="H3",
                    description="d3",
                    status=HypothesisStatus.FAILED,
                ),
                Hypothesis(
                    hypothesis_id="H4",
                    description="d4",
                    status=HypothesisStatus.PENDING,
                ),
            ],
        )
        result = reason.get_pending_hypotheses()
        ids = [h.hypothesis_id for h in result]
        assert "H1" in ids
        assert "H4" in ids
        assert "H2" not in ids
        assert "H3" not in ids

    def test_get_pending_hypotheses_all_active(self):
        """모두 ACTIVE면 빈 리스트."""
        reason = ReasoningState(
            hypotheses=[
                Hypothesis(
                    hypothesis_id="H1",
                    description="d",
                    status=HypothesisStatus.ACTIVE,
                ),
            ],
        )
        assert reason.get_pending_hypotheses() == []

    def test_get_pending_hypotheses_preserves_order(self):
        """반환 순서가 입력 순서를 유지한다."""
        reason = ReasoningState(
            hypotheses=[
                Hypothesis(hypothesis_id="H1", description="d1"),
                Hypothesis(hypothesis_id="H2", description="d2"),
                Hypothesis(hypothesis_id="H3", description="d3"),
            ],
        )
        result = reason.get_pending_hypotheses()
        assert [h.hypothesis_id for h in result] == ["H1", "H2", "H3"]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 7. KnowledgeItem.promote() 메서드
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestKnowledgeItemPromote:
    """KnowledgeItem.promote() — 상태 승격 및 필드 갱신 검증."""

    def test_promote_updates_all_fields(self):
        """모든 필드가 올바르게 갱신된다."""
        ki = KnowledgeItem(key="table:TB_CUST", status=ConfidenceStatus.UNRESOLVED)
        ki.promote(
            new_status=ConfidenceStatus.CONFIRMED,
            value="고객기본",
            confidence=0.95,
            source="MongoDB",
            evidence="TB_ADW_CUST001M 검색 결과",
        )
        assert ki.status == ConfidenceStatus.CONFIRMED
        assert ki.value == "고객기본"
        assert ki.confidence == 0.95
        assert ki.source == "MongoDB"
        assert "TB_ADW_CUST001M 검색 결과" in ki.evidence

    def test_promote_appends_evidence(self):
        """evidence는 덮어쓰지 않고 append한다."""
        ki = KnowledgeItem(
            key="K1",
            evidence=["초기 증거"],
        )
        ki.promote(
            new_status=ConfidenceStatus.CONFIRMED,
            value="v",
            confidence=0.8,
            source="ES",
            evidence="추가 증거",
        )
        assert len(ki.evidence) == 2
        assert "초기 증거" in ki.evidence
        assert "추가 증거" in ki.evidence

    def test_promote_multiple_times(self):
        """반복 promote 시 evidence가 누적된다."""
        ki = KnowledgeItem(key="K1")
        ki.promote(
            new_status=ConfidenceStatus.CONFIRMED,
            value="v1",
            confidence=0.7,
            source="ES",
            evidence="증거1",
        )
        ki.promote(
            new_status=ConfidenceStatus.CONFIRMED,
            value="v2",
            confidence=0.9,
            source="MongoDB",
            evidence="증거2",
        )
        assert len(ki.evidence) == 2
        assert ki.value == "v2"
        assert ki.confidence == 0.9
        assert ki.source == "MongoDB"

    def test_promote_from_unresolved_to_confirmed(self):
        """일반적인 UNRESOLVED → CONFIRMED 승격 시나리오."""
        ki = KnowledgeItem(
            key="col:LOAN_AMT",
            status=ConfidenceStatus.UNRESOLVED,
        )
        assert ki.status == ConfidenceStatus.UNRESOLVED
        ki.promote(
            new_status=ConfidenceStatus.CONFIRMED,
            value="여신금액",
            confidence=1.0,
            source="MongoDB",
            evidence="컬럼 메타 직접 확인",
        )
        assert ki.status == ConfidenceStatus.CONFIRMED

    def test_promote_empty_evidence_list_starts_appending(self):
        """초기 evidence가 빈 리스트일 때 첫 promote 후 길이 1."""
        ki = KnowledgeItem(key="K1")
        assert ki.evidence == []
        ki.promote(
            new_status=ConfidenceStatus.CONFIRMED,
            value="v",
            confidence=0.5,
            source="s",
            evidence="첫 증거",
        )
        assert len(ki.evidence) == 1
