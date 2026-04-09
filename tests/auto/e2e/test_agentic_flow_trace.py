"""에이전틱 코어 흐름 추적 테스트 — 실제 Dummy 데이터 소스 사용.

각 노드의 in/out과 state 변화를 직접 추적하여
에이전트가 의도대로 동작하는지 검증하고 개선점을 분석한다.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from src.agents.state.state import (
    ConfidenceStatus,
    FailureType,
    FinalStatus,
    HypothesisStatus,
    Phase,
    PipelineState,
    ReasoningState,
    StepStatus,
    TableMeta,
    ColumnInfo,
    ExecutionStep,
    Hypothesis,
    KnowledgeItem,
    LoopGuard,
    StructuralHints,
)
from src.agents.nodes.reason.reasoning_preparer import reasoning_preparer_node
from src.agents.nodes.reason.context_retriever import (
    context_retriever_node,
)
from src.agents.nodes.reason.readiness_gate import (
    readiness_gate_node,
)
from src.agents.nodes.reason.sql_validator import (
    sql_validator_node,
)
from src.agents.nodes.reason.recovery_agent import (
    _handle_hypothesis_transition,
)
from src.agents.nodes.reason.result_finalizer import (
    result_finalizer_node,
)
from src.services.confidence_scorer import (
    ReadinessVerdict,
    calculate_readiness,
    evaluate_readiness,
)

# 보고서 수집기
REPORT: list[dict[str, Any]] = []


def _record(
    category: str, test_name: str,
    input_summary: str, output_summary: str,
    state_trace: dict, verdict: str,
    findings: str = "",
):
    """테스트 결과를 보고서에 기록."""
    REPORT.append({
        "category": category,
        "test": test_name,
        "input": input_summary,
        "output": output_summary,
        "state": state_trace,
        "verdict": verdict,
        "findings": findings,
    })


def _apply(state: PipelineState, updates: dict):
    """노드 반환값을 상태에 적용."""
    d = state.model_dump()
    # reason 필드는 중첩이므로 별도 병합
    if "reason" in updates:
        reason_update = updates.pop("reason")
        if isinstance(reason_update, ReasoningState):
            d["reason"] = reason_update.model_dump()
        elif isinstance(reason_update, dict):
            d["reason"].update(reason_update)
    d.update(updates)
    return PipelineState(**d)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 시나리오 1: reasoning_preparer 노드 — Dummy 데이터로 실제 탐색
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestReasoningPreparerFlowTrace:
    """reasoning_preparer 노드 실제 흐름 추적."""

    @pytest.mark.asyncio
    async def test_reasoning_preparer_with_customer_query(self):
        """고객 관련 질의 → reasoning_preparer가 가설 수립."""
        state = PipelineState(
            preprocessed_input="이번 달 신규 고객 수",
        )

        result = await reasoning_preparer_node(state)
        new_state = _apply(state, result)

        # 상태 추적
        reason = new_state.reason
        trace = {
            "phase": reason.phase,
            "hypotheses_count": len(reason.hypotheses),
            "knowledge_items_count": len(
                reason.knowledge_items
            ),
            "explored_tables_count": len(
                reason.explored_tables
            ),
            "execution_steps": len(
                reason.execution_plan
            ),
            "executed_tool_keys": reason.executed_tool_keys,
        }

        _record(
            "reasoning_preparer", "customer_query",
            "이번 달 신규 고객 수",
            f"가설 {trace['hypotheses_count']}개, "
            f"후보테이블 {trace['explored_tables_count']}개",
            trace, "PASS" if trace["hypotheses_count"] > 0
            else "FAIL",
            findings=(
                "Dummy 데이터에서 '고객' 키워드로 "
                "TB_CUST_INFO 검색 성공 여부 확인"
            ),
        )

        # 검증: reasoning_preparer는 항상 H1 1개 생성
        assert reason.phase == Phase.EXPLORING
        assert len(reason.hypotheses) == 1
        assert reason.current_hypothesis is not None

    @pytest.mark.asyncio
    async def test_reasoning_preparer_with_loan_query(self):
        """여신 관련 질의 → reasoning_preparer가 가설 수립."""
        state = PipelineState(
            preprocessed_input="지점별 여신 잔액 합계",
        )

        result = await reasoning_preparer_node(state)
        new_state = _apply(state, result)

        reason = new_state.reason
        trace = {
            "phase": reason.phase,
            "hypotheses": [
                h.description
                for h in reason.hypotheses
            ],
            "candidates": [
                ct.table_name
                for ct in reason.explored_tables
            ],
        }

        _record(
            "reasoning_preparer", "loan_query",
            "지점별 여신 잔액 합계",
            f"후보: {trace['candidates']}",
            trace, "PASS",
        )

        assert len(reason.hypotheses) == 1


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 시나리오 2: context_explorer — 실행계획 순차 실행
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestExplorerFlowTrace:
    """context_explorer 실제 흐름 추적."""

    @pytest.mark.asyncio
    async def test_explorer_executes_steps(self):
        """실행계획 스텝 순차 실행."""
        state = PipelineState(
            preprocessed_input="고객 수",
            reason=ReasoningState(
                hypotheses=[
                    Hypothesis(
                        hypothesis_id="H1",
                        description="test",
                        status=HypothesisStatus.ACTIVE,
                    ),
                ],
                current_hypothesis=Hypothesis(
                    hypothesis_id="H1",
                    description="test",
                    status=HypothesisStatus.ACTIVE,
                ),
                execution_plan=[
                    ExecutionStep(
                        step=1,
                        tool="search_table_meta",
                        input="고객",
                        purpose="고객 테이블 탐색",
                        status=StepStatus.PENDING,
                    ),
                    ExecutionStep(
                        step=2,
                        tool="lookup_code_meta",
                        input="CUST_TYPE_CD",
                        purpose="고객유형 코드값 확인",
                        status=StepStatus.PENDING,
                    ),
                ],
            ),
        )

        result = await context_retriever_node(state)
        new_state = _apply(state, result)

        reason = new_state.reason
        # 상태 추적
        done_steps = [
            s for s in reason.execution_plan
            if s.status == StepStatus.DONE
        ]
        failed_steps = [
            s for s in reason.execution_plan
            if s.status == StepStatus.FAILED
        ]

        trace = {
            "done": len(done_steps),
            "failed": len(failed_steps),
            "tool_calls": (
                reason.loop_guard.total_tool_calls
            ),
            "new_knowledge": len(
                reason.knowledge_items
            ),
            "new_tables": len(
                reason.explored_tables
            ),
            "insights": [
                s.insight for s in reason.execution_plan
                if s.insight
            ],
        }

        _record(
            "explorer", "sequential_steps",
            "2 steps (table_meta, code_meta)",
            f"완료 {trace['done']}, 실패 {trace['failed']}",
            trace, "PASS",
            findings=(
                "Dummy 커넥터가 키워드 기반으로 "
                "결과를 반환하므로, '고객' 키워드로 "
                "TB_CUST_INFO를 찾을 수 있음"
            ),
        )

        assert trace["tool_calls"] >= 1

    @pytest.mark.asyncio
    async def test_explorer_skips_duplicates(self):
        """이미 검색한 쿼리 스킵."""
        state = PipelineState(
            reason=ReasoningState(
                executed_tool_keys={"search_table_meta:고객"},
                hypotheses=[
                    Hypothesis(
                        hypothesis_id="H1",
                        description="t",
                        status=HypothesisStatus.ACTIVE,
                    ),
                ],
                execution_plan=[
                    ExecutionStep(
                        step=1,
                        tool="search_table_meta",
                        input="고객",
                        purpose="중복 테스트",
                        status=StepStatus.PENDING,
                    ),
                ],
            ),
        )

        result = await context_retriever_node(state)
        new_state = _apply(state, result)

        reason = new_state.reason
        skipped = [
            s for s in reason.execution_plan
            if s.status == StepStatus.SKIPPED
        ]

        trace = {
            "skipped_count": len(skipped),
            "tool_calls": (
                reason.loop_guard.total_tool_calls
            ),
        }

        _record(
            "explorer", "dedup_skip",
            "이미 검색한 '고객' 쿼리 재실행",
            f"스킵 {trace['skipped_count']}건",
            trace,
            "PASS" if trace["skipped_count"] == 1
            else "FAIL",
        )

        assert trace["skipped_count"] == 1
        assert trace["tool_calls"] == 0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 시나리오 3: confidence_evaluator 판정 추적
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestEvaluatorFlowTrace:
    """confidence_evaluator 판정 추적."""

    @pytest.mark.asyncio
    async def test_evaluator_high_confidence(self):
        """높은 확신도 → GENERATING."""
        state = PipelineState(
            reason=ReasoningState(
                knowledge_items=[
                    KnowledgeItem(
                        key="table:TB_CUST",
                        status=ConfidenceStatus.CONFIRMED,
                        confidence=0.9,
                        is_critical=True,
                    ),
                    KnowledgeItem(
                        key="measure:고객수",
                        status=ConfidenceStatus.CONFIRMED,
                        confidence=0.9,
                        is_critical=True,
                    ),
                ],
                explored_tables=[
                    TableMeta(table_name="TB_CSC_001M", name="TB_CSC_001M", description="고객"),
                ],
                explored_use_cases=[
                    {"sql": "SELECT 1", "similarity": 0.85},
                ],
                hypotheses=[
                    Hypothesis(
                        hypothesis_id="H1",
                        description="t",
                        status=HypothesisStatus.ACTIVE,
                    ),
                    Hypothesis(
                        hypothesis_id="H2",
                        description="fallback",
                        status=HypothesisStatus.PENDING,
                    ),
                ],
                current_hypothesis=Hypothesis(
                    hypothesis_id="H1",
                    description="t",
                    status=HypothesisStatus.ACTIVE,
                ),
            ),
        )

        score = calculate_readiness(state.reason)
        verdict = evaluate_readiness(state.reason)
        result = await readiness_gate_node(state)

        trace = {
            "score": score,
            "verdict": verdict.value,
            "phase": result["reason"].phase,
        }

        _record(
            "evaluator", "high_confidence",
            f"score={score:.3f}",
            f"verdict={verdict.value}",
            trace,
            "PASS" if verdict == ReadinessVerdict.GENERATE
            else "FAIL",
        )

        assert result["reason"].phase == Phase.GENERATING

    @pytest.mark.asyncio
    async def test_evaluator_low_confidence(self):
        """낮은 확신도 → REPLANNING/EXPLORE."""
        state = PipelineState(
            reason=ReasoningState(
                knowledge_items=[
                    KnowledgeItem(
                        key="measure:x",
                        status=ConfidenceStatus.UNRESOLVED,
                        is_critical=True,
                    ),
                ],
                hypotheses=[
                    Hypothesis(
                        hypothesis_id="H1",
                        description="t",
                        status=HypothesisStatus.ACTIVE,
                    ),
                    Hypothesis(
                        hypothesis_id="H2",
                        description="fallback",
                        status=HypothesisStatus.PENDING,
                    ),
                ],
                current_hypothesis=Hypothesis(
                    hypothesis_id="H1",
                    description="t",
                    status=HypothesisStatus.ACTIVE,
                ),
            ),
        )

        score = calculate_readiness(state.reason)
        verdict = evaluate_readiness(state.reason)
        result = await readiness_gate_node(state)

        trace = {
            "score": score,
            "verdict": verdict.value,
            "phase": result["reason"].phase,
        }

        _record(
            "evaluator", "low_confidence",
            f"score={score:.3f}, 1 UNRESOLVED",
            f"verdict={verdict.value}",
            trace,
            "PASS" if verdict in (
                ReadinessVerdict.REPLAN,
                ReadinessVerdict.EXPLORE,
            ) else "FAIL",
        )

        assert result["reason"].phase in (
            Phase.REPLANNING, Phase.EXPLORING,
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 시나리오 4: sql_validator 3-레이어 추적
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestValidatorFlowTrace:
    """sql_validator 3-레이어 검증 추적."""

    @pytest.mark.asyncio
    async def test_validator_rejects_dml(self):
        """DML → Layer1 거부."""
        state = PipelineState(
            reason=ReasoningState(
                generated_sql="DELETE FROM users",
                explored_tables=[
                    TableMeta(
                        table_name="users",
                        columns=[ColumnInfo(name="id")],
                    ),
                ],
            ),
        )

        result = await sql_validator_node(state)
        ft = result["reason"].failure_type

        trace = {
            "failure_type": ft,
            "fix": result["reason"].failure_reason or "",
        }

        _record(
            "validator", "dml_rejection",
            "DELETE FROM users",
            f"failure_type={ft}",
            trace,
            "PASS" if ft == FailureType.SQL_SYNTAX
            else "FAIL",
        )

        assert ft == FailureType.SQL_SYNTAX

    @pytest.mark.asyncio
    async def test_validator_layer2a_groupby(self):
        """GROUP BY 누락 → Layer2a 거부."""
        state = PipelineState(
            reason=ReasoningState(
                generated_sql=(
                    "SELECT branch_cd, COUNT(*) "
                    "FROM tb_cust"
                ),
                query_decomposition={
                    "group_by": ["지점"],
                    "measures": [],
                },
                explored_tables=[
                    TableMeta(
                        table_name="tb_cust",
                        columns=[ColumnInfo(name="branch_cd")],
                    ),
                ],
            ),
        )

        result = await sql_validator_node(state)
        ft = result["reason"].failure_type

        trace = {
            "failure_type": ft,
            "feedback": result["reason"].failure_reason or "",
        }

        _record(
            "validator", "missing_groupby",
            "SELECT without GROUP BY (decomp has group_by)",
            f"failure_type={ft}",
            trace,
            "PASS" if ft is not None else "FAIL",
            findings=(
                "Layer2a sanity check가 "
                "GROUP BY 누락을 감지하는지 확인"
            ),
        )

        assert ft is not None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 시나리오 5: recovery_planner 재계획 추적
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestRecoveryFlowTrace:
    """recovery_agent 가설 전이 추적."""

    def test_recovery_with_pending_hypothesis(self):
        """대기 가설 있음 → 다음 가설로 전환."""
        reason = ReasoningState(
            hypotheses=[
                Hypothesis(
                    hypothesis_id="H1",
                    description="활용사례 기반",
                    status=HypothesisStatus.ACTIVE,
                ),
                Hypothesis(
                    hypothesis_id="H2",
                    description="직접 탐색",
                    status=HypothesisStatus.PENDING,
                    required_tables=["TB_NEW"],
                ),
            ],
            current_hypothesis=Hypothesis(
                hypothesis_id="H1",
                description="활용사례 기반",
                status=HypothesisStatus.ACTIVE,
            ),
            failure_type=FailureType.SQL_STRUCTURAL,
            failure_reason="테이블 구조 불일치",
        )

        _handle_hypothesis_transition(reason)

        trace = {
            "current_hyp": (
                reason.current_hypothesis.hypothesis_id
                if reason.current_hypothesis else None
            ),
            "dead_ends": len(reason.dead_ends),
        }

        _record(
            "recovery", "switch_hypothesis",
            "H1 FAILED → H2 PENDING",
            f"다음 가설: {trace['current_hyp']}",
            trace,
            "PASS" if trace["current_hyp"] == "H2"
            else "FAIL",
        )

        assert trace["current_hyp"] == "H2"
        assert trace["dead_ends"] >= 1

    def test_recovery_no_pending_hypothesis(self):
        """모든 가설 소진 → current_hypothesis가 None."""
        reason = ReasoningState(
            hypotheses=[
                Hypothesis(
                    hypothesis_id="H1",
                    description="t",
                    status=HypothesisStatus.FAILED,
                ),
            ],
            current_hypothesis=None,
        )

        _handle_hypothesis_transition(reason)

        trace = {
            "current_hyp": (
                reason.current_hypothesis.hypothesis_id
                if reason.current_hypothesis
                else None
            ),
        }

        _record(
            "recovery", "no_pending_hypothesis",
            "모든 가설 FAILED → None",
            f"hyp={trace['current_hyp']}",
            trace,
            "PASS" if trace["current_hyp"] is None
            else "FAIL",
        )

        assert reason.current_hypothesis is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 시나리오 6: 전체 파이프라인 경계면 추적
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestBoundaryConversionTrace:
    """PipelineState ↔ ReasoningState 경계면 추적."""

    def test_full_boundary_success_flow(self):
        """성공 흐름 경계면 변환."""
        # PipelineState 생성
        state = PipelineState(
            preprocessed_input="지점별 고객 수",
            normalized_query=None,
            conversation_history=[
                {"role": "user", "content": "안녕"},
            ],
        )

        trace_in = {
            "query": state.preprocessed_input,
            "history_len": len(
                state.conversation_history
            ),
        }

        # reason 계층에서 처리 완료된 상태 시뮬
        reason = state.reason.model_copy(deep=True)
        reason.validated_sql = (
            "SELECT branch_cd, COUNT(*) AS cnt "
            "FROM tb_cust GROUP BY branch_cd"
        )
        reason.final_status = FinalStatus.SUCCESS
        reason.explored_tables = [
            TableMeta(
                table_name="tb_cust",
                columns=[
                    ColumnInfo(name="branch_cd"),
                    ColumnInfo(name="cust_no"),
                ],
            ),
        ]
        reason.knowledge_items = [
            KnowledgeItem(
                key="table:tb_cust",
                status=ConfidenceStatus.CONFIRMED,
                confidence=0.9,
            ),
        ]
        state = state.model_copy(update={"reason": reason})

        trace_out = {
            "has_sql": bool(state.reason.validated_sql),
            "table_count": len(
                state.reason.explored_tables
            ),
            "knowledge_count": len(
                state.reason.knowledge_items
            ),
        }

        _record(
            "boundary", "success_roundtrip",
            json.dumps(trace_in, ensure_ascii=False),
            json.dumps(trace_out),
            {"in": trace_in, "out": trace_out},
            "PASS",
        )

        assert state.reason.validated_sql
        assert trace_out["table_count"] == 1
        assert trace_out["knowledge_count"] == 1


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 보고서 생성
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestReportGeneration:
    """마지막에 실행되어 보고서를 출력."""

    def test_zz_generate_report(self):
        """보고서 출력 (마지막 실행)."""
        if not REPORT:
            return

        lines = [
            "",
            "=" * 72,
            "  에이전틱 코어 E2E 흐름 추적 보고서",
            "=" * 72,
            "",
        ]

        categories = {}
        for r in REPORT:
            cat = r["category"]
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(r)

        for cat, items in categories.items():
            lines.append(f"## {cat.upper()}")
            lines.append("-" * 40)
            for item in items:
                v = item["verdict"]
                icon = "PASS" if v == "PASS" else "FAIL"
                lines.append(
                    f"  [{icon}] {item['test']}"
                )
                lines.append(
                    f"    IN:  {item['input'][:60]}"
                )
                lines.append(
                    f"    OUT: {item['output'][:60]}"
                )
                if item.get("findings"):
                    lines.append(
                        f"    NOTE: {item['findings'][:60]}"
                    )
                lines.append("")
            lines.append("")

        # 요약
        total = len(REPORT)
        passed = sum(
            1 for r in REPORT if r["verdict"] == "PASS"
        )
        lines.append(f"TOTAL: {total} | "
                      f"PASS: {passed} | "
                      f"FAIL: {total - passed}")
        lines.append("=" * 72)

        report_text = "\n".join(lines)
        print(report_text)

        assert passed == total, (
            f"{total - passed} tests FAILED"
        )
