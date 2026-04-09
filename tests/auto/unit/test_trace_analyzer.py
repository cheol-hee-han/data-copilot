"""트레이스 자동 분석 유틸리티 단위 테스트.

테스트 대상:
    - analyze_trace_data: dict 형태 트레이스 분석 → TraceReport 반환
    - BatchReport.success_rate: 성공률 계산 (0건 분모 방어)
    - BatchReport.summary: 사람이 읽을 수 있는 요약 문자열

검증 범위:
    - 컨텍스트 수집 분석: _check_context_retrieval
    - LLM 호출 분석: _check_llm_calls
    - 결정 지점 분석: _check_decisions
    - SQL 품질 분석: _check_sql_quality
    - 파이프라인 흐름 분석: _check_pipeline_flow
    - 노드 성능 분석: _check_node_performance
    - 타임라인 분석: _check_timeline

실행 스크립트:
    pytest tests/auto/unit/test_trace_analyzer.py -v

참고:
    - 외부 의존성 없음 — 모든 테스트는 dict 입력으로 동작
    - FinalStatus enum 사용
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tests.conftest import get_test_logger, log_test_case
from src.models.enums import FinalStatus
from src.utils.tracker.trace_analyzer import (
    BatchReport,
    Finding,
    TraceReport,
    analyze_trace_data,
)

logger = get_test_logger("test_trace_analyzer")


# ── 트레이스 픽스처 팩토리 ──

def _make_trace(
    *,
    run_id: str = "run-001",
    user_input: str = "이번 달 여신 잔액 알려줘",
    final_status: str = FinalStatus.SUCCESS,
    context_retrievals: list | None = None,
    llm_calls: list | None = None,
    decisions: list | None = None,
    sql: dict | None = None,
    nodes: list | None = None,
    node_path: list | None = None,
    timeline: list | None = None,
) -> dict:
    """테스트용 기본 트레이스 dict를 생성한다."""
    # str Enum의 .value로 직렬화 (str(enum)은 'ClassName.VALUE' 형태가 됨)
    status_val = final_status.value if isinstance(final_status, FinalStatus) else final_status
    return {
        "run_id": run_id,
        "user_input": user_input,
        "final_status": status_val,
        "context_retrievals": context_retrievals or [],
        "llm_calls": llm_calls or [],
        "decisions": decisions or [],
        "sql": sql or {},
        "nodes": nodes or [],
        "node_path": node_path or [],
        "timeline": timeline or [],
    }


def _make_retrieval(source: str, results_count: int = 5, latency_ms: float = 200.0) -> dict:
    return {"source": source, "results_count": results_count, "latency_ms": latency_ms}


def _make_llm_call(node: str = "sql_generator", response_text: str = "SELECT 1", latency_ms: float = 1000.0) -> dict:
    return {
        "node": node,
        "response_text": response_text,
        "latency_ms": latency_ms,
        "prompt_tokens": 100,
        "response_tokens": 50,
    }


def _make_node(name: str, duration_ms: float = 1000.0, status: str = "success") -> dict:
    return {"node": name, "duration_ms": duration_ms, "status": status, "error_message": ""}


def _make_timeline_event(event_type: str, node: str = "sql_generator", status: str = "ok") -> dict:
    return {"event_type": event_type, "node": node, "status": status, "summary": f"{event_type}:{node}"}


# ════════════════════════════════════════════════════════════
# TraceReport 기본 구조
# ════════════════════════════════════════════════════════════

class TestTraceReportBasic:
    """analyze_trace_data: 기본 TraceReport 생성 검증."""

    def test_returns_trace_report(self):
        """analyze_trace_data는 TraceReport 객체를 반환한다."""
        data = _make_trace()
        result = analyze_trace_data(data)
        passed = isinstance(result, TraceReport)
        log_test_case(logger, "trace_report_type", data["run_id"], "TraceReport", type(result).__name__, passed)
        assert passed

    def test_run_id_preserved(self):
        """run_id가 TraceReport에 정확히 저장된다."""
        data = _make_trace(run_id="test-run-999")
        result = analyze_trace_data(data)
        passed = result.run_id == "test-run-999"
        log_test_case(logger, "trace_report_run_id", "test-run-999", "test-run-999", result.run_id, passed)
        assert passed

    def test_final_status_preserved(self):
        """final_status가 TraceReport에 정확히 저장된다."""
        data = _make_trace(final_status=FinalStatus.FAILURE)
        result = analyze_trace_data(data)
        # _make_trace가 .value로 직렬화하므로 "failure" 문자열과 비교
        passed = result.final_status == FinalStatus.FAILURE.value
        log_test_case(logger, "trace_report_status", FinalStatus.FAILURE.value, FinalStatus.FAILURE.value, result.final_status, passed)
        assert passed

    def test_findings_is_list(self):
        """findings는 list 타입이다."""
        data = _make_trace()
        result = analyze_trace_data(data)
        passed = isinstance(result.findings, list)
        log_test_case(logger, "trace_report_findings_list", "빈 트레이스", "list 타입", type(result.findings).__name__, passed)
        assert passed

    def test_metrics_collected(self):
        """metrics dict가 수집된다."""
        data = _make_trace(llm_calls=[_make_llm_call()])
        result = analyze_trace_data(data)
        passed = "llm_call_count" in result.metrics
        log_test_case(logger, "trace_report_metrics", "1 llm_call", "llm_call_count 포함", result.metrics, passed)
        assert passed


# ════════════════════════════════════════════════════════════
# _check_context_retrieval
# ════════════════════════════════════════════════════════════

class TestCheckContextRetrieval:
    """컨텍스트 수집 분석 규칙 검증."""

    def test_no_retrieval_sql_pipeline_is_critical(self):
        """SQL 파이프라인에서 컨텍스트 수집 기록이 없으면 CRITICAL 발생."""
        data = _make_trace(final_status=FinalStatus.SUCCESS, context_retrievals=[])
        result = analyze_trace_data(data)
        critical = [f for f in result.findings if f.severity == "CRITICAL" and f.category == "context"]
        passed = len(critical) > 0
        log_test_case(logger, "ctx_no_retrieval_critical", "ctx=[]", "CRITICAL", critical, passed)
        assert passed

    def test_no_retrieval_casual_is_ok(self):
        """casual_response 파이프라인은 컨텍스트 수집 없어도 정상이다."""
        data = _make_trace(final_status="casual_response", context_retrievals=[])
        result = analyze_trace_data(data)
        ctx_critical = [f for f in result.findings if f.severity == "CRITICAL" and f.category == "context"]
        passed = len(ctx_critical) == 0
        log_test_case(logger, "ctx_casual_no_critical", "casual_response", "CRITICAL 없음", ctx_critical, passed)
        assert passed

    def test_zero_result_tool_is_warning(self):
        """검색 결과 0건 도구는 WARNING 발생."""
        data = _make_trace(
            context_retrievals=[
                _make_retrieval("search_table_meta", results_count=0),
            ]
        )
        result = analyze_trace_data(data)
        warnings = [f for f in result.findings if f.severity == "WARNING" and "0건" in f.message]
        passed = len(warnings) > 0
        log_test_case(logger, "ctx_zero_result_warning", "results_count=0", "WARNING", warnings, passed)
        assert passed

    def test_slow_tool_is_warning(self):
        """5초 초과 도구 응답은 WARNING 발생."""
        data = _make_trace(
            context_retrievals=[
                _make_retrieval("search_table_meta", latency_ms=6000),
            ]
        )
        result = analyze_trace_data(data)
        slow_warnings = [f for f in result.findings if "지연" in f.message]
        passed = len(slow_warnings) > 0
        log_test_case(logger, "ctx_slow_tool_warning", "latency=6000ms", "WARNING", slow_warnings, passed)
        assert passed

    def test_missing_search_table_meta_is_critical(self):
        """테이블 메타 검색 미호출은 CRITICAL 발생."""
        data = _make_trace(
            context_retrievals=[
                _make_retrieval("search_biz_term", results_count=3),
            ]
        )
        result = analyze_trace_data(data)
        critical = [
            f for f in result.findings
            if "테이블 메타 검색" in f.message and f.severity == "CRITICAL"
        ]
        passed = len(critical) > 0
        log_test_case(logger, "ctx_no_table_meta_critical", "table_meta 없음", "CRITICAL", critical, passed)
        assert passed

    def test_normal_context_no_critical(self):
        """정상 컨텍스트 수집은 CRITICAL이 없다."""
        data = _make_trace(
            context_retrievals=[
                _make_retrieval("search_table_meta", results_count=5, latency_ms=300),
                _make_retrieval("search_biz_term", results_count=3, latency_ms=100),
            ]
        )
        result = analyze_trace_data(data)
        critical = [f for f in result.findings if f.severity == "CRITICAL" and f.category == "context"]
        passed = len(critical) == 0
        log_test_case(logger, "ctx_normal_no_critical", "정상 ctx", "CRITICAL 없음", critical, passed)
        assert passed


# ════════════════════════════════════════════════════════════
# _check_llm_calls
# ════════════════════════════════════════════════════════════

class TestCheckLlmCalls:
    """LLM 호출 분석 규칙 검증."""

    def test_no_llm_calls_is_warning(self):
        """LLM 호출 기록이 없으면 WARNING 발생."""
        data = _make_trace(llm_calls=[])
        result = analyze_trace_data(data)
        llm_warnings = [f for f in result.findings if f.category == "llm" and f.severity == "WARNING"]
        passed = len(llm_warnings) > 0
        log_test_case(logger, "llm_no_calls_warning", "llm_calls=[]", "WARNING", llm_warnings, passed)
        assert passed

    def test_empty_response_is_critical(self):
        """LLM 빈 응답은 CRITICAL 발생."""
        data = _make_trace(
            llm_calls=[_make_llm_call(response_text="")]
        )
        result = analyze_trace_data(data)
        critical = [f for f in result.findings if "빈 응답" in f.message and f.severity == "CRITICAL"]
        passed = len(critical) > 0
        log_test_case(logger, "llm_empty_response_critical", "response_text=''", "CRITICAL", critical, passed)
        assert passed

    def test_slow_llm_call_is_warning(self):
        """LLM 응답 10초 초과는 WARNING 발생."""
        data = _make_trace(
            llm_calls=[_make_llm_call(latency_ms=12000)]
        )
        result = analyze_trace_data(data)
        slow = [f for f in result.findings if "10초" in f.message]
        passed = len(slow) > 0
        log_test_case(logger, "llm_slow_warning", "latency=12000", "WARNING", slow, passed)
        assert passed

    def test_excessive_llm_calls_is_warning(self):
        """LLM 호출이 15회 초과이면 WARNING 발생."""
        calls = [_make_llm_call() for _ in range(16)]
        data = _make_trace(llm_calls=calls)
        result = analyze_trace_data(data)
        excessive = [f for f in result.findings if "16회" in f.message or "LLM 호출" in f.message and "최적화" in f.message]
        passed = len(excessive) > 0
        log_test_case(logger, "llm_excessive_warning", "16 calls", "WARNING", excessive, passed)
        assert passed

    def test_normal_llm_calls_no_critical(self):
        """정상 LLM 호출에는 CRITICAL이 없다."""
        data = _make_trace(
            llm_calls=[_make_llm_call(response_text="SELECT ...", latency_ms=2000)]
        )
        result = analyze_trace_data(data)
        critical = [f for f in result.findings if f.severity == "CRITICAL" and f.category == "llm"]
        passed = len(critical) == 0
        log_test_case(logger, "llm_normal_no_critical", "정상 호출", "CRITICAL 없음", critical, passed)
        assert passed


# ════════════════════════════════════════════════════════════
# _check_decisions
# ════════════════════════════════════════════════════════════

class TestCheckDecisions:
    """결정 지점 분석 규칙 검증."""

    def test_no_readiness_decision_is_info(self):
        """readiness 판정 기록 없으면 INFO 발생."""
        data = _make_trace(decisions=[])
        result = analyze_trace_data(data)
        info = [f for f in result.findings if f.severity == "INFO" and "readiness" in f.message]
        passed = len(info) > 0
        log_test_case(logger, "decision_no_readiness_info", "decisions=[]", "INFO", info, passed)
        assert passed

    def test_low_confidence_generate_is_warning(self):
        """낮은 확신도(0.5)로 generate_sql 진입 시 WARNING 발생."""
        data = _make_trace(decisions=[{
            "decision_type": "readiness_verdict",
            "chosen": "generate_sql",
            "confidence": 0.5,
            "reason": "부족한 메타",
        }])
        result = analyze_trace_data(data)
        low_conf = [f for f in result.findings if "확신도" in f.message and f.severity == "WARNING"]
        passed = len(low_conf) > 0
        log_test_case(logger, "decision_low_confidence_warning", "confidence=0.5", "WARNING", low_conf, passed)
        assert passed

    def test_high_confidence_generate_no_warning(self):
        """높은 확신도(0.9)로 generate_sql 진입 시 확신도 WARNING이 없다."""
        data = _make_trace(decisions=[{
            "decision_type": "readiness_verdict",
            "chosen": "generate_sql",
            "confidence": 0.9,
        }])
        result = analyze_trace_data(data)
        low_conf = [f for f in result.findings if "확신도" in f.message and f.severity == "WARNING"]
        passed = len(low_conf) == 0
        log_test_case(logger, "decision_high_confidence_ok", "confidence=0.9", "WARNING 없음", low_conf, passed)
        assert passed

    def test_low_intent_confidence_is_warning(self):
        """의도 분류 확신도 0.5 미만이면 WARNING 발생."""
        data = _make_trace(decisions=[{
            "decision_type": "intent_classification",
            "confidence": 0.4,
        }])
        result = analyze_trace_data(data)
        intent_warning = [f for f in result.findings if "의도 분류" in f.message]
        passed = len(intent_warning) > 0
        log_test_case(logger, "decision_intent_low_conf", "intent conf=0.4", "WARNING", intent_warning, passed)
        assert passed


# ════════════════════════════════════════════════════════════
# _check_sql_quality
# ════════════════════════════════════════════════════════════

class TestCheckSqlQuality:
    """SQL 품질 분석 규칙 검증."""

    def test_no_sql_in_sql_pipeline_is_critical(self):
        """SQL 파이프라인에서 SQL 미생성은 CRITICAL 발생."""
        data = _make_trace(final_status=FinalStatus.SUCCESS, sql={})
        result = analyze_trace_data(data)
        critical = [f for f in result.findings if "SQL 미생성" in f.message]
        passed = len(critical) > 0
        log_test_case(logger, "sql_not_generated_critical", "sql={}", "CRITICAL", critical, passed)
        assert passed

    def test_no_sql_in_casual_is_ok(self):
        """casual_response에서 SQL 없음은 CRITICAL이 아니다."""
        data = _make_trace(final_status="casual_response", sql={})
        result = analyze_trace_data(data)
        sql_critical = [f for f in result.findings if "SQL 미생성" in f.message]
        passed = len(sql_critical) == 0
        log_test_case(logger, "sql_casual_no_critical", "casual+no sql", "CRITICAL 없음", sql_critical, passed)
        assert passed

    def test_validation_failed_is_critical(self):
        """SQL 검증 실패는 CRITICAL 발생."""
        data = _make_trace(sql={
            "generated_sql": "SELECT ...",
            "validated": False,
            "validation_errors": ["읽기 전용이 아닌 쿼리"],
        })
        result = analyze_trace_data(data)
        critical = [f for f in result.findings if "검증 실패" in f.message]
        passed = len(critical) > 0
        log_test_case(logger, "sql_validation_failed_critical", "validated=False", "CRITICAL", critical, passed)
        assert passed

    def test_execution_failed_after_validation_is_critical(self):
        """검증은 통과했으나 실행 실패는 CRITICAL 발생."""
        data = _make_trace(sql={
            "generated_sql": "SELECT ...",
            "validated": True,
            "execution_success": False,
        })
        result = analyze_trace_data(data)
        critical = [f for f in result.findings if "실행 실패" in f.message]
        passed = len(critical) > 0
        log_test_case(logger, "sql_exec_fail_critical", "exec_success=False", "CRITICAL", critical, passed)
        assert passed

    def test_high_retry_count_is_warning(self):
        """SQL 재생성 2회 이상은 WARNING 발생."""
        data = _make_trace(sql={
            "generated_sql": "SELECT ...",
            "validated": True,
            "execution_success": True,
            "retry_count": 3,
        })
        result = analyze_trace_data(data)
        retry_warning = [f for f in result.findings if "재생성" in f.message]
        passed = len(retry_warning) > 0
        log_test_case(logger, "sql_high_retry_warning", "retry_count=3", "WARNING", retry_warning, passed)
        assert passed

    def test_zero_rows_is_warning(self):
        """SQL 실행 성공 후 결과 0건은 WARNING 발생."""
        data = _make_trace(sql={
            "generated_sql": "SELECT ...",
            "validated": True,
            "execution_success": True,
            "row_count": 0,
        })
        result = analyze_trace_data(data)
        zero_warning = [f for f in result.findings if "0건" in f.message and f.category == "accuracy"]
        passed = len(zero_warning) > 0
        log_test_case(logger, "sql_zero_rows_warning", "row_count=0", "WARNING", zero_warning, passed)
        assert passed

    def test_successful_sql_no_findings(self):
        """완전히 성공한 SQL에는 SQL 관련 CRITICAL이 없다."""
        data = _make_trace(sql={
            "generated_sql": "SELECT ...",
            "validated": True,
            "execution_success": True,
            "row_count": 100,
            "retry_count": 0,
        })
        result = analyze_trace_data(data)
        sql_critical = [f for f in result.findings if f.severity == "CRITICAL" and f.category == "sql"]
        passed = len(sql_critical) == 0
        log_test_case(logger, "sql_success_no_critical", "완전 성공 SQL", "CRITICAL 없음", sql_critical, passed)
        assert passed


# ════════════════════════════════════════════════════════════
# _check_pipeline_flow
# ════════════════════════════════════════════════════════════

class TestCheckPipelineFlow:
    """파이프라인 흐름 분석 규칙 검증."""

    def test_replan_twice_is_warning(self):
        """recovery_planner 2회 이상은 WARNING 발생."""
        data = _make_trace(
            node_path=["context_explorer", "sql_generator", "recovery_planner", "sql_generator", "recovery_planner"]
        )
        result = analyze_trace_data(data)
        replan_warning = [f for f in result.findings if "재계획" in f.message]
        passed = len(replan_warning) > 0
        log_test_case(logger, "pipeline_replan_warning", "replan x2", "WARNING", replan_warning, passed)
        assert passed

    def test_final_failure_is_critical(self):
        """파이프라인 최종 실패는 CRITICAL 발생."""
        data = _make_trace(
            final_status=FinalStatus.FAILURE,
            sql={"generated_sql": "SELECT ..."},
        )
        data["error_message"] = "SQL 실행 오류"
        result = analyze_trace_data(data)
        failure_critical = [f for f in result.findings if "최종 실패" in f.message]
        passed = len(failure_critical) > 0
        log_test_case(logger, "pipeline_final_failure_critical", "FAILURE", "CRITICAL", failure_critical, passed)
        assert passed

    def test_success_no_pipeline_critical(self):
        """성공 파이프라인에는 파이프라인 CRITICAL이 없다."""
        data = _make_trace(
            final_status=FinalStatus.SUCCESS,
            node_path=["context_explorer", "sql_generator"],
        )
        result = analyze_trace_data(data)
        pipeline_critical = [f for f in result.findings if f.severity == "CRITICAL" and f.category == "pipeline"]
        passed = len(pipeline_critical) == 0
        log_test_case(logger, "pipeline_success_no_critical", "성공 파이프라인", "CRITICAL 없음", pipeline_critical, passed)
        assert passed


# ════════════════════════════════════════════════════════════
# _check_node_performance
# ════════════════════════════════════════════════════════════

class TestCheckNodePerformance:
    """노드 성능 분석 규칙 검증."""

    def test_slow_node_is_warning(self):
        """30초 초과 노드는 WARNING 발생."""
        data = _make_trace(
            nodes=[_make_node("context_explorer", duration_ms=35000)]
        )
        result = analyze_trace_data(data)
        slow = [f for f in result.findings if "30초" in f.message]
        passed = len(slow) > 0
        log_test_case(logger, "node_slow_warning", "duration=35000", "WARNING", slow, passed)
        assert passed

    def test_error_node_is_critical(self):
        """status=error 노드는 CRITICAL 발생."""
        data = _make_trace(
            nodes=[_make_node("sql_validator", status="error")]
        )
        result = analyze_trace_data(data)
        critical = [f for f in result.findings if f.category == "pipeline" and f.severity == "CRITICAL" and "sql_validator" in f.message]
        passed = len(critical) > 0
        log_test_case(logger, "node_error_critical", "status=error", "CRITICAL", critical, passed)
        assert passed

    def test_normal_node_no_warning(self):
        """정상 노드(1초)는 성능 WARNING이 없다."""
        data = _make_trace(
            nodes=[_make_node("sql_generator", duration_ms=1000)]
        )
        result = analyze_trace_data(data)
        perf_warnings = [f for f in result.findings if "30초" in f.message]
        passed = len(perf_warnings) == 0
        log_test_case(logger, "node_normal_no_warning", "duration=1000", "WARNING 없음", perf_warnings, passed)
        assert passed


# ════════════════════════════════════════════════════════════
# _check_timeline
# ════════════════════════════════════════════════════════════

class TestCheckTimeline:
    """타임라인 분석 규칙 검증."""

    def test_empty_timeline_is_info(self):
        """timeline이 없으면 INFO 발생."""
        data = _make_trace(timeline=[])
        result = analyze_trace_data(data)
        info = [f for f in result.findings if "timeline" in f.message.lower() or "timeline" in f.message]
        passed = len(info) > 0
        log_test_case(logger, "timeline_empty_info", "timeline=[]", "INFO", info, passed)
        assert passed

    def test_failed_tool_is_warning(self):
        """도구 호출 실패는 WARNING 발생."""
        data = _make_trace(
            timeline=[
                _make_timeline_event("tool_call", status="error"),
            ]
        )
        result = analyze_trace_data(data)
        tool_warnings = [f for f in result.findings if "도구 호출 실패" in f.message]
        passed = len(tool_warnings) > 0
        log_test_case(logger, "timeline_tool_fail_warning", "tool_call error", "WARNING", tool_warnings, passed)
        assert passed

    def test_consecutive_llm_calls_warning(self):
        """도구 없이 LLM 3회 연속 호출은 WARNING 발생."""
        data = _make_trace(
            timeline=[
                _make_timeline_event("llm_call"),
                _make_timeline_event("llm_call"),
                _make_timeline_event("llm_call"),
            ]
        )
        result = analyze_trace_data(data)
        consec = [f for f in result.findings if "연속 LLM" in f.message]
        passed = len(consec) > 0
        log_test_case(logger, "timeline_consec_llm_warning", "3 consecutive llm", "WARNING", consec, passed)
        assert passed

    def test_two_consecutive_llm_calls_no_warning(self):
        """LLM 2회 연속은 경고 없음 (3회 미만)."""
        data = _make_trace(
            timeline=[
                _make_timeline_event("llm_call"),
                _make_timeline_event("llm_call"),
                _make_timeline_event("tool_call"),
            ]
        )
        result = analyze_trace_data(data)
        consec = [f for f in result.findings if "연속 LLM" in f.message]
        passed = len(consec) == 0
        log_test_case(logger, "timeline_two_llm_ok", "2 consecutive llm", "WARNING 없음", consec, passed)
        assert passed

    def test_empty_node_is_info(self):
        """내부 이벤트 없는 노드는 INFO 발생."""
        data = _make_trace(
            timeline=[
                _make_timeline_event("node_start", node="dummy_node"),
                _make_timeline_event("node_end", node="dummy_node"),
            ]
        )
        result = analyze_trace_data(data)
        empty_info = [f for f in result.findings if "이벤트 없는 노드" in f.message]
        passed = len(empty_info) > 0
        log_test_case(logger, "timeline_empty_node_info", "빈 노드", "INFO", empty_info, passed)
        assert passed


# ════════════════════════════════════════════════════════════
# BatchReport
# ════════════════════════════════════════════════════════════

class TestBatchReport:
    """BatchReport: success_rate와 summary 검증."""

    def test_success_rate_zero_on_empty(self):
        """total_runs=0이면 success_rate는 0.0이다 (분모 0 방어)."""
        report = BatchReport()
        passed = report.success_rate == 0.0
        log_test_case(logger, "batch_success_rate_empty", "total_runs=0", 0.0, report.success_rate, passed)
        assert passed

    def test_success_rate_full_success(self):
        """모든 케이스 성공이면 success_rate는 1.0이다."""
        report = BatchReport(total_runs=5, success_count=5, failure_count=0)
        passed = report.success_rate == 1.0
        log_test_case(logger, "batch_success_rate_100", "5/5", 1.0, report.success_rate, passed)
        assert passed

    def test_success_rate_partial(self):
        """3/5 성공이면 success_rate는 0.6이다."""
        report = BatchReport(total_runs=5, success_count=3, failure_count=2)
        passed = abs(report.success_rate - 0.6) < 1e-9
        log_test_case(logger, "batch_success_rate_partial", "3/5", 0.6, report.success_rate, passed)
        assert passed

    def test_summary_contains_total_runs(self):
        """summary에 total_runs 수가 포함된다."""
        report = BatchReport(total_runs=10, success_count=8, failure_count=2)
        s = report.summary
        passed = "10" in s
        log_test_case(logger, "batch_summary_total", "total_runs=10", "10 포함", s[:80], passed)
        assert passed

    def test_summary_contains_success_rate(self):
        """summary에 성공률(%)이 포함된다."""
        report = BatchReport(total_runs=4, success_count=3, failure_count=1)
        s = report.summary
        passed = "75%" in s or "3/4" in s
        log_test_case(logger, "batch_summary_rate", "3/4 성공", "75% 또는 3/4", s[:100], passed)
        assert passed

    def test_summary_shows_severity_counts(self):
        """summary에 CRITICAL/WARNING 발견 수가 포함된다."""
        finding = Finding(severity="CRITICAL", category="sql", stage="sql_generator", message="테스트")
        report = BatchReport(
            total_runs=1,
            success_count=0,
            failure_count=1,
            findings_by_severity={"CRITICAL": 2, "WARNING": 1},
            top_findings=[finding],
        )
        s = report.summary
        passed = "CRITICAL" in s and "2" in s
        log_test_case(logger, "batch_summary_severity", "CRITICAL:2", "CRITICAL 2 포함", s[:120], passed)
        assert passed

    def test_summary_empty_batch(self):
        """빈 배치에서도 summary가 생성된다."""
        report = BatchReport()
        s = report.summary
        passed = isinstance(s, str) and len(s) > 0
        log_test_case(logger, "batch_summary_empty", "빈 배치", "비어있지 않음", s[:60], passed)
        assert passed

    def test_top_findings_in_summary(self):
        """top_findings의 메시지가 summary에 포함된다."""
        finding = Finding(severity="WARNING", category="context", stage="ctx", message="검색 결과 0건")
        report = BatchReport(
            total_runs=1,
            success_count=1,
            failure_count=0,
            top_findings=[finding],
        )
        s = report.summary
        passed = "검색 결과 0건" in s
        log_test_case(logger, "batch_summary_top_findings", "top_findings", "메시지 포함", s[:120], passed)
        assert passed
