"""평가 트래커(EvaluationTracker, BatchEvaluationTracker) 단위 테스트.

테스트 대상:
    파이프라인 실행 추적(노드·LLM 호출·의사결정·컨텍스트 수집·SQL),
    골든셋 평가 결과 기록, JSON 저장, 배치 요약 생성을 검증한다.
    비활성화 시 아무것도 기록하지 않는 no-op 동작도 확인한다.

입력 예시 (정상):
    - start_run → track_node → track_llm_call → end_run
    - 기대: trace에 노드/LLM/토큰/지연시간 기록

결과 예시 (오류 케이스):
    - 비활성화(eval_tracker_enabled=False) → 모든 track 호출 무시
    - save() → None 반환

실행 스크립트:
    pytest tests/unit/test_evaluation_tracker.py -v

참고:
    - 외부 의존성 없음 (monkeypatch로 설정 주입, tmp_path로 파일 저장)
    - 테스트 대상 소스: src/utils/tracker/evaluation.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.utils.tracker import (
    BatchEvaluationTracker,
    EvaluationTracker,
)


@pytest.fixture()
def tracker(monkeypatch):
    """활성화된 트래커 인스턴스."""
    monkeypatch.setattr(
        "src.utils.tracker.evaluation.settings.eval_tracker_enabled", True,
    )
    return EvaluationTracker(run_id="test-001")


@pytest.fixture()
def disabled_tracker(monkeypatch):
    """비활성화된 트래커 인스턴스."""
    monkeypatch.setattr(
        "src.utils.tracker.evaluation.settings.eval_tracker_enabled", False,
    )
    return EvaluationTracker(run_id="test-disabled")


# ── 기본 동작 ──


def test_start_and_end_run(tracker):
    """실행 시작/종료가 정상 기록된다."""
    tracker.start_run(user_input="테스트 질의", session_id="sess-1")
    tracker.end_run(
        final_intent="data_extraction",
        final_status="completed",
        final_response_summary="결과 요약",
    )

    trace = tracker.trace
    assert trace.user_input == "테스트 질의"
    assert trace.session_id == "sess-1"
    assert trace.final_intent == "data_extraction"
    assert trace.start_time
    assert trace.end_time
    assert trace.total_duration_ms > 0


def test_track_node(tracker):
    """노드 실행이 기록된다."""
    tracker.start_run(user_input="test")
    tracker.start_node("preprocess")
    tracker.end_node(
        "preprocess",
        input_summary={"user_input": "test"},
        output_summary={"preprocessed": "test"},
    )

    assert len(tracker.trace.nodes) == 1
    node = tracker.trace.nodes[0]
    assert node.node == "preprocess"
    assert node.status == "success"
    assert node.duration_ms >= 0


def test_track_llm_call(tracker):
    """LLM 호출이 기록된다."""
    tracker.start_run(user_input="test")
    tracker.track_llm_call(
        node="classify_intent",
        prompt_summary="의도 분류 프롬프트",
        response_text='{"intent": "data_extraction"}',
        model="claude-sonnet",
        prompt_tokens=100,
        response_tokens=20,
        latency_ms=450.5,
    )

    assert tracker.trace.total_llm_calls == 1
    assert tracker.trace.total_llm_tokens == 120
    assert tracker.trace.total_llm_latency_ms == 450.5

    call = tracker.trace.llm_calls[0]
    assert call.node == "classify_intent"
    assert call.model == "claude-sonnet"


def test_track_decision(tracker):
    """의사결정이 기록된다."""
    tracker.start_run(user_input="test")
    tracker.track_decision(
        node="classify_intent",
        decision_type="intent_classification",
        chosen="data_extraction",
        alternatives=["data_analysis", "clarification_needed"],
        confidence=0.95,
        reason="키워드 '알려줘'",
    )

    assert len(tracker.trace.decisions) == 1
    d = tracker.trace.decisions[0]
    assert d.chosen == "data_extraction"
    assert d.confidence == 0.95


def test_track_context_retrieval(tracker):
    """컨텍스트 수집이 기록된다."""
    tracker.start_run(user_input="test")
    tracker.track_context_retrieval(
        source="es_meta",
        query="신규 고객",
        results_count=3,
        results_summary=["TB_CUST_INFO", "TB_CUST_ARCHIVE", "TB_CUST_M"],
        latency_ms=120.0,
    )

    assert len(tracker.trace.context_retrievals) == 1
    assert tracker.trace.context_retrievals[0].results_count == 3


def test_track_sql(tracker):
    """SQL 기록이 저장된다."""
    tracker.start_run(user_input="test")
    tracker.track_sql(
        generated_sql="SELECT COUNT(*) FROM TB_CUST_INFO",
        validated=True,
        row_count=1,
        execution_time_ms=45.2,
        execution_success=True,
    )

    sql = tracker.trace.sql
    assert sql.validated is True
    assert sql.row_count == 1


def test_track_eval_result(tracker):
    """골든셋 평가 결과가 기록된다."""
    tracker.start_run(user_input="test", golden_id="GS001")
    tracker.track_eval_result(
        passed=False,
        errors=["테이블 불일치: expected=['TB_CUST_INFO']"],
    )

    assert tracker.trace.eval_passed is False
    assert len(tracker.trace.eval_errors) == 1


def test_node_path_tracking(tracker):
    """노드 실행 순서가 기록된다."""
    tracker.start_run(user_input="test")
    for name in ["preprocess", "classify_intent", "collect_context"]:
        tracker.start_node(name)
        tracker.end_node(name)

    assert tracker.trace.node_path == [
        "preprocess", "classify_intent", "collect_context",
    ]


def test_node_error_tracking(tracker):
    """노드 에러가 기록된다."""
    tracker.start_run(user_input="test")
    tracker.start_node("generate_sql")
    tracker.end_node(
        "generate_sql",
        status="error",
        error_message="LLM timeout",
    )

    node = tracker.trace.nodes[0]
    assert node.status == "error"
    assert node.error_message == "LLM timeout"


# ── 비활성화 ──


def test_disabled_tracker_does_nothing(disabled_tracker):
    """비활성화 시 아무것도 기록하지 않는다."""
    disabled_tracker.start_run(user_input="test")
    disabled_tracker.start_node("preprocess")
    disabled_tracker.end_node("preprocess")
    disabled_tracker.track_llm_call(node="test")
    disabled_tracker.track_decision(
        node="test", decision_type="test", chosen="test",
    )
    disabled_tracker.end_run()

    assert len(disabled_tracker.trace.nodes) == 0
    assert len(disabled_tracker.trace.llm_calls) == 0


def test_disabled_save_returns_none(disabled_tracker, tmp_path):
    """비활성화 시 save()가 None을 반환한다."""
    result = disabled_tracker.save(output_dir=str(tmp_path))
    assert result is None


# ── 저장 ──


def test_save_creates_json(tracker, tmp_path):
    """save()가 JSON 파일을 생성한다."""
    tracker.start_run(user_input="테스트 질의")
    tracker.end_run(final_status="completed")

    path = tracker.save(output_dir=str(tmp_path))
    assert path is not None
    assert path.exists()
    assert path.suffix == ".json"

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["run_id"] == "test-001"
    assert data["user_input"] == "테스트 질의"


def test_to_dict(tracker):
    """to_dict()가 올바른 딕셔너리를 반환한다."""
    tracker.start_run(user_input="test")
    tracker.end_run()

    d = tracker.to_dict()
    assert isinstance(d, dict)
    assert d["run_id"] == "test-001"


# ── 배치 트래커 ──


def test_batch_tracker_summary(monkeypatch, tmp_path):
    """배치 트래커가 올바른 요약을 생성한다."""
    monkeypatch.setattr(
        "src.utils.tracker.evaluation.settings.eval_tracker_enabled", True,
    )

    batch = BatchEvaluationTracker(batch_id="batch-test")
    batch.start_batch()

    # 통과 케이스
    t1 = EvaluationTracker(run_id="GS001")
    t1.start_run(user_input="test1", golden_id="GS001")
    t1.start_node("preprocess")
    t1.end_node("preprocess")
    t1.track_llm_call(
        node="classify_intent",
        prompt_tokens=100, response_tokens=20, latency_ms=500,
    )
    t1.track_sql(generated_sql="SELECT 1", validated=True)
    t1.track_eval_result(passed=True)
    t1.end_run(final_intent="data_extraction", final_status="completed")
    batch.add_trace(t1)

    # 실패 케이스
    t2 = EvaluationTracker(run_id="GS002")
    t2.start_run(user_input="test2", golden_id="GS002")
    t2.track_sql(
        generated_sql="SELECT 1",
        validated=False,
        validation_errors=["테이블 불일치"],
        retry_count=1,
    )
    t2.track_eval_result(
        passed=False,
        errors=["테이블 불일치"],
    )
    t2.end_run(final_status="completed")
    batch.add_trace(t2)

    summary = batch.generate_summary()
    assert summary["summary"]["total"] == 2
    assert summary["summary"]["passed"] == 1
    assert summary["summary"]["failed"] == 1
    assert summary["summary"]["pass_rate"] == 50.0


def test_batch_save(monkeypatch, tmp_path):
    """배치 저장이 올바른 디렉토리 구조를 생성한다."""
    monkeypatch.setattr(
        "src.utils.tracker.evaluation.settings.eval_tracker_enabled", True,
    )

    batch = BatchEvaluationTracker(batch_id="batch-save-test")
    batch.start_batch()

    t1 = EvaluationTracker(run_id="GS001")
    t1.start_run(user_input="test")
    t1.track_eval_result(passed=False, errors=["에러"])
    t1.end_run()
    batch.add_trace(t1)

    result_path = batch.save(output_dir=str(tmp_path))
    assert result_path is not None
    assert (result_path / "summary.json").exists()
    assert (result_path / "traces" / "GS001.json").exists()
    assert (result_path / "failures.json").exists()

    # failures.json 내용 확인
    failures = json.loads(
        (result_path / "failures.json").read_text(encoding="utf-8")
    )
    assert len(failures) == 1
    assert failures[0]["golden_id"] == ""  # golden_id 미설정
