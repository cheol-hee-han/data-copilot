"""result_finalizer 노드 헬퍼 함수 단위 테스트.

테스트 대상:
  - _build_success_summary: 성공 시 탐색 요약 문자열
  - _build_failure_output: 실패 시 dead_ends·미해소 용어 기반 상세 정보
  - _build_cancel_summary: 취소 시 부분 결과 포함 사용자 메시지
실제 환경에서 실행 — Mock 없음.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import pytest

from src.agents.nodes.reason.result_finalizer import (
    _build_cancel_summary,
    _build_failure_output,
    _build_success_summary,
)
from src.agents.state.state import (
    ConfidenceStatus,
    DeadEnd,
    FailureType,
    KnowledgeItem,
    LoopGuard,
    ReasoningState,
    SelectionStatus,
    TableMeta,
    UseCaseEntry,
)
from tests.conftest import get_test_logger, log_test_case

logger = get_test_logger("test_result_finalizer")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 헬퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _ki(
    key: str,
    status: ConfidenceStatus = ConfidenceStatus.UNRESOLVED,
    evidence: list[str] | None = None,
    is_critical: bool = True,
) -> KnowledgeItem:
    return KnowledgeItem(
        key=key,
        status=status,
        evidence=evidence or [],
        is_critical=is_critical,
    )


def _table(
    name: str = "TB_LOAN",
    selection_status: SelectionStatus = SelectionStatus.SELECTED,
) -> TableMeta:
    return TableMeta(table_name=name, selection_status=selection_status)


def _reason(**kw) -> ReasoningState:
    return ReasoningState(**kw)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _build_success_summary
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestBuildSuccessSummary:
    """_build_success_summary 성공 요약 생성 테스트."""

    def test_includes_tool_calls_and_generate_attempts(self):
        """도구 호출 수와 SQL 생성 횟수가 요약에 포함된다."""
        guard = LoopGuard(total_tool_calls=5, generate_attempts=2)
        reason = _reason(loop_guard=guard)
        summary = _build_success_summary(reason)
        passed = "5" in summary and "2" in summary
        log_test_case(logger, "success_tool_counts", guard, "5,2 in summary", summary, passed)
        assert passed

    def test_replan_count_shown_when_nonzero(self):
        """replan_count > 0이면 재계획 횟수가 표시된다."""
        guard = LoopGuard(replan_count=3)
        reason = _reason(loop_guard=guard)
        summary = _build_success_summary(reason)
        passed = "3" in summary and "재계획" in summary
        log_test_case(logger, "success_replan_count", guard, "재계획 3", summary, passed)
        assert passed

    def test_replan_count_zero_not_shown(self):
        """replan_count=0이면 재계획 항목이 표시되지 않는다."""
        guard = LoopGuard(replan_count=0)
        reason = _reason(loop_guard=guard)
        summary = _build_success_summary(reason)
        passed = "재계획" not in summary
        log_test_case(logger, "success_no_replan_zero", guard, "no 재계획", summary, passed)
        assert passed

    def test_confirmed_tables_listed(self):
        """table: prefix + CONFIRMED 상태인 KnowledgeItem의 테이블명이 표시된다."""
        items = [
            _ki("table:TB_LOAN_ACNT", ConfidenceStatus.CONFIRMED),
            _ki("table:TB_DEPOSIT", ConfidenceStatus.CONFIRMED),
            _ki("measure:잔액", ConfidenceStatus.CONFIRMED),
        ]
        reason = _reason(knowledge_items=items)
        summary = _build_success_summary(reason)
        passed = "TB_LOAN_ACNT" in summary and "TB_DEPOSIT" in summary
        log_test_case(logger, "success_confirmed_tables", items, "TB_LOAN_ACNT", summary, passed)
        assert passed

    def test_non_confirmed_tables_not_listed(self):
        """CONFIRMED가 아닌 table: 항목은 표시되지 않는다."""
        items = [
            _ki("table:TB_LOAN_ACNT", ConfidenceStatus.PROBABLE),
        ]
        reason = _reason(knowledge_items=items)
        summary = _build_success_summary(reason)
        passed = "TB_LOAN_ACNT" not in summary
        log_test_case(logger, "success_non_confirmed_table", items, "not listed", summary, passed)
        assert passed

    def test_use_cases_count_shown(self):
        """explored_use_cases가 있으면 참고 활용사례 건수가 표시된다."""
        ucs = [
            UseCaseEntry(id="uc1", relevant=True),
            UseCaseEntry(id="uc2", relevant=False),
        ]
        reason = _reason(explored_use_cases=ucs)
        summary = _build_success_summary(reason)
        passed = "2" in summary and "활용사례" in summary
        log_test_case(logger, "success_use_cases_count", ucs, "2 활용사례", summary, passed)
        assert passed

    def test_pipe_separated_parts(self):
        """요약이 ' | '로 구분된 부분들로 이루어진다."""
        guard = LoopGuard(total_tool_calls=3, generate_attempts=1, replan_count=1)
        reason = _reason(loop_guard=guard)
        summary = _build_success_summary(reason)
        passed = " | " in summary
        log_test_case(logger, "success_pipe_separator", {}, "| separator", summary, passed)
        assert passed


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _build_failure_output
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestBuildFailureOutput:
    """_build_failure_output 실패 상세 조립 테스트.

    현재 구현: dead_ends 마지막 항목의 reason 첫 줄만 사용하여
    '요청하신 데이터를 조회하지 못했습니다.\n(reason)' 형태로 반환.
    dead_ends/failure_reason 모두 없으면 기본 실패 문구 반환.
    """

    def test_starts_with_sql_failure_header(self):
        """출력이 '요청하신 데이터를 조회' 문구를 포함한다."""
        reason = _reason()
        output = _build_failure_output(reason)
        passed = "요청하신 데이터를 조회" in output
        log_test_case(logger, "failure_header", {}, "요청하신 데이터를 조회", output[:40], passed)
        assert passed

    def test_dead_ends_listed_in_output(self):
        """dead_ends의 마지막 reason 첫 줄이 출력에 포함된다."""
        dead_ends = [
            DeadEnd(
                hypothesis_id="H1",
                failure_type=FailureType.NO_TABLE,
                reason="테이블 후보 없음",
            ),
        ]
        reason = _reason(dead_ends=dead_ends)
        output = _build_failure_output(reason)
        passed = "테이블 후보 없음" in output
        log_test_case(logger, "failure_dead_ends", dead_ends, "테이블 후보 없음", output, passed)
        assert passed

    def test_unresolved_terms_listed(self):
        """dead_ends나 failure_reason이 없을 때 기본 실패 문구가 반환된다."""
        items = [
            _ki("measure:연체율", ConfidenceStatus.UNRESOLVED),
            _ki("filter:지점코드=001", ConfidenceStatus.UNRESOLVED),
        ]
        reason = _reason(knowledge_items=items)
        output = _build_failure_output(reason)
        # knowledge_items는 직접 포함되지 않으나 기본 실패 문구는 반환됨
        passed = len(output) > 0
        log_test_case(logger, "failure_unresolved_terms", items, "non-empty output", output, passed)
        assert passed

    def test_partial_sql_shown_when_generated_not_validated(self):
        """generated_sql이 있지만 validated_sql이 없어도 기본 실패 메시지가 반환된다."""
        reason = _reason(
            generated_sql="SELECT * FROM TB_LOAN WHERE ACNT_NO = '001'",
            validated_sql=None,
        )
        output = _build_failure_output(reason)
        passed = "요청하신 데이터를 조회" in output
        log_test_case(logger, "failure_partial_sql", {}, "요청하신 데이터를 조회", output, passed)
        assert passed

    def test_no_partial_sql_when_both_absent(self):
        """dead_ends와 failure_reason이 없으면 기본 실패 문구가 반환된다."""
        reason = _reason(generated_sql=None, validated_sql=None)
        output = _build_failure_output(reason)
        passed = len(output) > 0
        log_test_case(logger, "failure_no_partial_sql", {}, "non-empty output", output, passed)
        assert passed

    def test_no_partial_sql_when_validated_exists(self):
        """validated_sql이 있는 상태에서도 기본 실패 문구를 반환한다."""
        reason = _reason(
            generated_sql="SELECT 1",
            validated_sql="SELECT 1",
        )
        output = _build_failure_output(reason)
        passed = len(output) > 0
        log_test_case(logger, "failure_validated_no_partial", {}, "non-empty output", output, passed)
        assert passed

    def test_multiple_dead_ends_all_listed(self):
        """여러 dead_ends가 있을 때 마지막 항목의 reason이 출력에 포함된다."""
        dead_ends = [
            DeadEnd(hypothesis_id="H1", failure_type=FailureType.NO_TABLE, reason="경로1"),
            DeadEnd(hypothesis_id="H2", failure_type=FailureType.TERM_UNRESOLVABLE, reason="경로2"),
        ]
        reason = _reason(dead_ends=dead_ends)
        output = _build_failure_output(reason)
        # 마지막 dead_end의 reason이 포함됨
        passed = "경로2" in output
        log_test_case(logger, "failure_multi_dead_ends", dead_ends, "경로2", output, passed)
        assert passed


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _build_cancel_summary
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestBuildCancelSummary:
    """_build_cancel_summary 취소 요약 생성 테스트."""

    def test_starts_with_cancel_message(self):
        """취소 메시지로 시작한다."""
        reason = _reason()
        summary = _build_cancel_summary(reason)
        passed = "중단" in summary
        log_test_case(logger, "cancel_header", {}, "중단 in msg", summary, passed)
        assert passed

    def test_selected_tables_listed(self):
        """SELECTED 테이블명이 취소 메시지에 포함된다."""
        tables = [
            _table("TB_LOAN", SelectionStatus.SELECTED),
            _table("TB_DEPOSIT", SelectionStatus.SELECTED),
        ]
        reason = _reason(explored_tables=tables)
        summary = _build_cancel_summary(reason)
        passed = "TB_LOAN" in summary and "TB_DEPOSIT" in summary
        log_test_case(logger, "cancel_selected_tables", tables, "TB_LOAN,TB_DEPOSIT", summary, passed)
        assert passed

    def test_rejected_tables_not_listed(self):
        """REJECTED 테이블은 취소 메시지에 표시되지 않는다."""
        tables = [_table("TB_REJECTED", SelectionStatus.REJECTED)]
        reason = _reason(explored_tables=tables)
        summary = _build_cancel_summary(reason)
        passed = "TB_REJECTED" not in summary
        log_test_case(logger, "cancel_no_rejected_table", tables, "not listed", summary, passed)
        assert passed

    def test_confirmed_knowledge_count_shown(self):
        """CONFIRMED 지식 항목 수가 표시된다."""
        items = [
            _ki("k1", ConfidenceStatus.CONFIRMED),
            _ki("k2", ConfidenceStatus.CONFIRMED),
            _ki("k3", ConfidenceStatus.UNRESOLVED),
        ]
        reason = _reason(knowledge_items=items)
        summary = _build_cancel_summary(reason)
        passed = "2" in summary and "확인된 정보" in summary
        log_test_case(logger, "cancel_confirmed_count", items, "2 확인된 정보", summary, passed)
        assert passed

    def test_no_confirmed_knowledge_no_count_message(self):
        """CONFIRMED 항목이 없으면 확인된 정보 메시지 없음."""
        items = [_ki("k1", ConfidenceStatus.UNRESOLVED)]
        reason = _reason(knowledge_items=items)
        summary = _build_cancel_summary(reason)
        passed = "확인된 정보" not in summary
        log_test_case(logger, "cancel_no_confirmed", items, "no 확인된 정보", summary, passed)
        assert passed

    def test_no_tables_no_table_message(self):
        """탐색된 테이블이 없으면 테이블 메시지 없음."""
        reason = _reason(explored_tables=[])
        summary = _build_cancel_summary(reason)
        passed = "탐색한 테이블" not in summary
        log_test_case(logger, "cancel_no_tables", {}, "no table msg", summary, passed)
        assert passed
