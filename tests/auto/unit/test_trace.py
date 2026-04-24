"""파이프라인 추론 추적(TraceEntry, add_trace) 단위 테스트.

테스트 대상:
    파이프라인 각 노드의 실행 이력을 TraceEntry로 기록하는 기능과
    add_trace의 불변성(원본 state 미변경)을 검증한다.
"""

from __future__ import annotations

from src.agents.state.state import (
    PipelineState,
    TraceEntry,
    add_trace,
)


# ──────────────────────────────────────────────────
# TraceEntry 모델
# ──────────────────────────────────────────────────


def test_trace_entry_defaults():
    """기본값으로 TraceEntry 생성."""
    entry = TraceEntry(node="test", action="테스트 액션")
    assert entry.node == "test"
    assert entry.action == "테스트 액션"
    assert entry.detail == ""
    assert entry.timestamp  # 자동 생성


def test_trace_entry_with_detail():
    """상세 내용 포함 TraceEntry 생성."""
    entry = TraceEntry(node="SQL생성", action="SQL 생성", detail="TB_LOAN_INFO 사용")
    assert "TB_LOAN_INFO" in entry.detail


# ──────────────────────────────────────────────────
# add_trace
# ──────────────────────────────────────────────────


def test_add_trace_to_empty():
    """빈 trace_log에 항목 추가."""
    state = PipelineState()
    result = add_trace(state, "전처리", "입력 정규화")
    assert len(result) == 1
    assert result[0].node == "전처리"


def test_add_trace_preserves_existing():
    """기존 trace_log를 유지하면서 새 항목 추가."""
    state = PipelineState(
        trace_log=[TraceEntry(node="전처리", action="기존 항목")]
    )
    result = add_trace(state, "의도분류", "새 항목")
    assert len(result) == 2
    assert result[0].node == "전처리"
    assert result[1].node == "의도분류"


def test_add_trace_does_not_mutate_state():
    """add_trace는 원본 state.trace_log를 변경하지 않는다."""
    state = PipelineState()
    add_trace(state, "전처리", "테스트")
    assert len(state.trace_log) == 0


# ──────────────────────────────────────────────────
# PipelineState trace_log 필드
# ──────────────────────────────────────────────────


def test_pipeline_state_trace_log_default():
    """PipelineState 기본 trace_log는 빈 리스트."""
    state = PipelineState()
    assert state.trace_log == []


def test_pipeline_state_trace_log_serialization():
    """trace_log가 직렬화/역직렬화 가능한지 확인."""
    state = PipelineState(
        trace_log=[TraceEntry(node="test", action="test action")]
    )
    data = state.model_dump()
    assert len(data["trace_log"]) == 1
    assert data["trace_log"][0]["node"] == "test"
