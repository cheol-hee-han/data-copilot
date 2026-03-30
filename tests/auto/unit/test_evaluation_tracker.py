"""DataCopilotCallbackHandler 단위 테스트.

테스트 대상:
    파이프라인 실행 추적(노드·LLM 호출·의사결정·컨텍스트 수집·SQL),
    JSON 저장을 검증한다.
    비활성화 시 아무것도 기록하지 않는 no-op 동작도 확인한다.

입력 예시 (정상):
    - start_run → _record_llm_call → end_run
    - 기대: trace에 노드/LLM/토큰/지연시간 기록

결과 예시 (오류 케이스):
    - 비활성화(enabled=False) → 모든 기록 호출 무시
    - save() → None 반환

실행 스크립트:
    pytest tests/auto/unit/test_evaluation_tracker.py -v

참고:
    - 외부 의존성 없음 (tmp_path로 파일 저장)
    - 테스트 대상 소스: src/utils/tracker/callback_handler.py,
      src/utils/tracker/evaluation.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.utils.tracker.callback_handler import DataCopilotCallbackHandler
from src.utils.tracker.evaluation import (
    EvaluationTrace,
    NodeRecord,
)


@pytest.fixture()
def handler() -> DataCopilotCallbackHandler:
    """활성화된 콜백 핸들러 인스턴스."""
    return DataCopilotCallbackHandler(
        run_id="test-001",
        enabled=True,
    )


@pytest.fixture()
def disabled_handler() -> DataCopilotCallbackHandler:
    """비활성화된 콜백 핸들러 인스턴스."""
    return DataCopilotCallbackHandler(
        run_id="test-disabled",
        enabled=False,
    )


# ── 기본 동작 ──


def test_start_and_end_run(handler: DataCopilotCallbackHandler) -> None:
    """실행 시작/종료가 정상 기록된다."""
    handler.start_run(user_input="테스트 질의", session_id="sess-1")
    handler.end_run(
        final_intent="data_extraction",
        final_status="completed",
        final_response_summary="결과 요약",
    )

    trace = handler.trace
    assert trace.user_input == "테스트 질의"
    assert trace.session_id == "sess-1"
    assert trace.final_intent == "data_extraction"
    assert trace.start_time
    assert trace.end_time
    assert trace.total_duration_ms > 0


def test_track_node(handler: DataCopilotCallbackHandler) -> None:
    """노드 실행이 기록된다 (NodeRecord 직접 추가)."""
    handler.start_run(user_input="test")

    handler.trace.nodes.append(
        NodeRecord(
            node="preprocess",
            input_summary={"user_input": "test"},
            output_summary={"preprocessed": "test"},
            duration_ms=12.5,
            status="success",
        )
    )
    handler.trace.node_path.append("preprocess")

    assert len(handler.trace.nodes) == 1
    node = handler.trace.nodes[0]
    assert node.node == "preprocess"
    assert node.status == "success"
    assert node.duration_ms >= 0


def test_track_llm_call(handler: DataCopilotCallbackHandler) -> None:
    """LLM 호출이 기록된다."""
    handler.start_run(user_input="test")
    handler._record_llm_call(
        {
            "node": "classify_intent",
            "prompt_summary": "의도 분류 프롬프트",
            "response_text": '{"intent": "data_extraction"}',
            "model": "claude-sonnet",
            "prompt_tokens": 100,
            "response_tokens": 20,
            "latency_ms": 450.5,
        },
    )

    assert handler.trace.total_llm_calls == 1
    assert handler.trace.total_llm_tokens == 120
    assert handler.trace.total_llm_latency_ms == 450.5

    call = handler.trace.llm_calls[0]
    assert call.node == "classify_intent"
    assert call.model == "claude-sonnet"


def test_track_decision(handler: DataCopilotCallbackHandler) -> None:
    """의사결정이 기록된다."""
    handler.start_run(user_input="test")
    handler._record_decision(
        node="classify_intent",
        data={
            "decision_type": "intent_classification",
            "chosen": "data_extraction",
            "alternatives": ["data_analysis", "clarification_needed"],
            "confidence": 0.95,
            "reason": "키워드 '알려줘'",
        },
    )

    assert len(handler.trace.decisions) == 1
    d = handler.trace.decisions[0]
    assert d.chosen == "data_extraction"
    assert d.confidence == 0.95


def test_track_context_retrieval(handler: DataCopilotCallbackHandler) -> None:
    """컨텍스트 수집이 기록된다."""
    handler.start_run(user_input="test")
    handler._record_context_retrieval(
        node="context_explorer",
        data={
            "source": "es_meta",
            "query": "신규 고객",
            "results_count": 3,
            "results_summary": ["TB_CUST_INFO", "TB_CUST_ARCHIVE", "TB_CUST_M"],
            "latency_ms": 120.0,
        },
    )

    assert len(handler.trace.context_retrievals) == 1
    assert handler.trace.context_retrievals[0].results_count == 3


def test_track_sql(handler: DataCopilotCallbackHandler) -> None:
    """SQL 기록이 저장된다."""
    handler.start_run(user_input="test")
    handler.record_sql(
        {
            "generated_sql": "SELECT COUNT(*) FROM TB_CUST_INFO",
            "validated": True,
            "row_count": 1,
            "execution_time_ms": 45.2,
            "execution_success": True,
        },
    )

    sql = handler.trace.sql
    assert sql.validated is True
    assert sql.row_count == 1


def test_node_path_tracking(handler: DataCopilotCallbackHandler) -> None:
    """노드 실행 순서가 기록된다."""
    handler.start_run(user_input="test")
    for name in ["preprocess", "classify_intent", "collect_context"]:
        handler.trace.node_path.append(name)
        handler.trace.nodes.append(
            NodeRecord(node=name, status="success"),
        )

    assert handler.trace.node_path == [
        "preprocess", "classify_intent", "collect_context",
    ]


def test_node_error_tracking(handler: DataCopilotCallbackHandler) -> None:
    """노드 에러가 기록된다."""
    handler.start_run(user_input="test")
    handler.trace.nodes.append(
        NodeRecord(
            node="generate_sql",
            duration_ms=10.0,
            status="error",
            error_message="LLM timeout",
        ),
    )

    node = handler.trace.nodes[0]
    assert node.status == "error"
    assert node.error_message == "LLM timeout"


# ── 비활성화 ──


def test_disabled_handler_does_nothing(
    disabled_handler: DataCopilotCallbackHandler,
) -> None:
    """비활성화 시 public 기록 메서드가 아무것도 기록하지 않는다."""
    assert disabled_handler.enabled is False

    disabled_handler.start_run(user_input="test")
    disabled_handler.record_sql(
        {"generated_sql": "SELECT 1", "validated": True},
    )
    disabled_handler.end_run()

    assert len(disabled_handler.trace.llm_calls) == 0
    assert disabled_handler.trace.sql.generated_sql == ""


def test_disabled_save_returns_none(
    disabled_handler: DataCopilotCallbackHandler,
    tmp_path: Path,
) -> None:
    """비활성화 시 save()가 None을 반환한다."""
    result = disabled_handler.save(output_dir=str(tmp_path))
    assert result is None


# ── 저장 ──


def test_save_creates_json(
    handler: DataCopilotCallbackHandler,
    tmp_path: Path,
) -> None:
    """save()가 JSON 파일을 생성한다."""
    handler.start_run(user_input="테스트 질의")
    handler.end_run(final_status="completed")

    path = handler.save(output_dir=str(tmp_path), with_report=False)
    assert path is not None
    assert path.exists()
    assert path.suffix == ".json"

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["run_id"] == "test-001"
    assert data["user_input"] == "테스트 질의"


def test_to_dict(handler: DataCopilotCallbackHandler) -> None:
    """to_dict()가 올바른 딕셔너리를 반환한다."""
    handler.start_run(user_input="test")
    handler.end_run()

    d = handler.to_dict()
    assert isinstance(d, dict)
    assert d["run_id"] == "test-001"
