"""파이프라인 추론 추적(TraceEntry, add_trace, format_trace_summary) 단위 테스트.

테스트 대상:
    파이프라인 각 노드의 실행 이력을 TraceEntry로 기록하고,
    사용자에게 보여줄 요약 텍스트를 생성하는 기능을 검증한다.
    add_trace의 불변성(원본 state 미변경)도 확인한다.

입력 예시 (정상):
    - add_trace(state, "전처리", "입력 정규화") → trace_log에 1건 추가
    - format_trace_summary → "1. 입력 정규화 완료" 형태 번호 매기기

결과 예시 (오류 케이스):
    - 빈 trace_log → format_trace_summary 빈 문자열 반환

실행 스크립트:
    pytest tests/unit/test_trace.py -v

참고:
    - 외부 의존성 없음
    - 테스트 대상 소스: src/agents/state/state.py (TraceEntry, add_trace, format_trace_summary)
"""

from __future__ import annotations

from src.agents.state.state import (
    PipelineState,
    TraceEntry,
    add_trace,
)
from src.models.trace import format_trace_summary


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
# format_trace_summary
# ──────────────────────────────────────────────────


def test_format_trace_summary_empty():
    """빈 trace_log면 빈 문자열 반환."""
    state = PipelineState()
    assert format_trace_summary(state) == ""


def test_format_trace_summary_single():
    """단일 항목 포맷팅."""
    state = PipelineState(
        trace_log=[TraceEntry(node="전처리", action="입력 정규화 완료")]
    )
    result = format_trace_summary(state)
    assert "1. 입력 정규화 완료" in result


def test_format_trace_summary_with_detail():
    """상세 내용 포함 포맷팅."""
    state = PipelineState(
        trace_log=[
            TraceEntry(node="컨텍스트수집", action="참조 정보 수집", detail="테이블 3건"),
        ]
    )
    result = format_trace_summary(state)
    assert "참조 정보 수집: 테이블 3건" in result


def test_format_trace_summary_multiple():
    """여러 항목 번호 순서대로 포맷팅."""
    state = PipelineState(
        trace_log=[
            TraceEntry(node="전처리", action="A"),
            TraceEntry(node="의도분류", action="B"),
            TraceEntry(node="SQL생성", action="C"),
        ]
    )
    result = format_trace_summary(state)
    lines = result.strip().split("\n")
    assert len(lines) == 3
    assert lines[0].startswith("1.")
    assert lines[2].startswith("3.")


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
