"""에이전틱 코어 통합 테스트 — 기본 결함 검증 + E2E 시나리오.

Phase 1: 기본 결함 검증 (모듈 임포트, 상태 모델, 서비스, 노드, 그래프)
Phase 2: E2E 시나리오 테스트 (카테고리별 10+ 케이스)
"""

from __future__ import annotations

import asyncio
import json
import sys
import traceback
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Phase 1: 기본 결함 검증
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestImports:
    """모듈 임포트 검증."""

    def test_agentic_state_imports(self):
        from src.agents.state.state import (
            PipelineState,
            ReasoningState,
            TableMeta,
            DeadEnd,
            ExecutionStep,
            Hypothesis,
            KnowledgeItem,
            LoopGuard,
            Phase,
            StructuralHints,
            should_terminate,
        )
        assert PipelineState is not None

    def test_confidence_scorer_imports(self):
        from src.services.confidence_scorer import (
            ReadinessVerdict,
            VERDICT_TO_PHASE,
            calculate_readiness,
            evaluate_readiness,
        )
        assert ReadinessVerdict.GENERATE.value == "generate_sql"

    def test_sqlglot_analyzer_imports(self):
        from src.utils.sqlglot_analyzer import (
            extract_structural_hints,
            merge_hints,
            parse_sql_safe,
        )
        assert extract_structural_hints is not None

    def test_agentic_core_imports(self):
        from src.agents.graph.pipeline import (
            build_pipeline,
            create_app,
        )
        assert build_pipeline is not None

    def test_agentic_nodes_imports(self):
        from src.agents.nodes.reason.reasoning_preparer import reasoning_preparer_node
        from src.agents.nodes.reason.context_retriever import (
            context_retriever_node,
        )
        from src.agents.nodes.reason.context_interpreter import (
            context_interpreter_node,
        )
        from src.agents.nodes.reason.readiness_gate import (
            readiness_gate_node,
        )
        from src.agents.nodes.reason.sql_generator import (
            sql_generator_node,
        )
        from src.agents.nodes.reason.sql_validator import (
            sql_validator_node,
        )
        from src.agents.nodes.reason.recovery_agent import (
            recovery_agent_node,
        )
        from src.agents.nodes.reason.result_finalizer import (
            result_finalizer_node,
        )
        assert reasoning_preparer_node is not None

    def test_pipeline_imports(self):
        from src.agents.graph.pipeline import (
            build_pipeline,
            create_app,
        )
        assert build_pipeline is not None


class TestReasoningState:
    """ReasoningState 모델 검증."""

    def test_default_creation(self):
        from src.agents.state.state import (
            PipelineState,
            ReasoningState,
            Phase,
        )
        state = PipelineState(reason=ReasoningState())
        assert state.reason.phase == Phase.PLANNING
        from src.agents.state.state import FinalStatus
        assert state.reason.final_status == FinalStatus.PENDING
        assert state.reason.loop_guard.total_tool_calls == 0
        assert state.reason.phase == Phase.PLANNING

    def test_knowledge_item_promote(self):
        from src.agents.state.state import KnowledgeItem, ConfidenceStatus
        ki = KnowledgeItem(key="test:key")
        assert ki.status == ConfidenceStatus.UNRESOLVED
        ki.promote(
            ConfidenceStatus.CONFIRMED, "value", 0.9, "샘플", "근거",
        )
        assert ki.status == ConfidenceStatus.CONFIRMED
        assert ki.confidence == 0.9
        assert len(ki.evidence) == 1

    def test_loop_guard_escalation(self):
        from src.agents.state.state import LoopGuard
        lg = LoopGuard()
        assert not lg.should_escalate_to_structural()
        lg.increment_local_fix()
        lg.increment_local_fix()
        assert lg.should_escalate_to_structural()

    def test_structural_hints_empty(self):
        from src.agents.state.state import StructuralHints
        hints = StructuralHints()
        assert hints.is_empty()
        assert hints.to_prompt_text() == ""

    def test_structural_hints_not_empty(self):
        from src.agents.state.state import StructuralHints
        hints = StructuralHints(join_patterns=["a.id = b.id"])
        assert not hints.is_empty()
        assert "조인 패턴" in hints.to_prompt_text()

    def test_should_terminate_tool_calls(self):
        from src.agents.state.state import (
            PipelineState,
            ReasoningState,
            LoopGuard,
            should_terminate,
        )
        state = PipelineState(
            reason=ReasoningState(
                loop_guard=LoopGuard(total_tool_calls=20),
            ),
        )
        assert should_terminate(state.reason)

    def test_should_terminate_no_hypotheses(self):
        from src.agents.state.state import (
            PipelineState,
            ReasoningState,
            should_terminate,
        )
        state = PipelineState(
            reason=ReasoningState(
                hypotheses=[], current_hypothesis=None,
            ),
        )
        assert should_terminate(state.reason)

    def test_should_not_terminate_normal(self):
        from src.agents.state.state import (
            PipelineState,
            ReasoningState,
            Hypothesis,
            HypothesisStatus,
            should_terminate,
        )
        state = PipelineState(
            reason=ReasoningState(
                hypotheses=[
                    Hypothesis(
                        hypothesis_id="H1",
                        description="test",
                        status=HypothesisStatus.PENDING,
                    ),
                ],
            ),
        )
        assert not should_terminate(state.reason)

    def test_get_confirmed_knowledge(self):
        from src.agents.state.state import (
            PipelineState,
            ReasoningState,
            KnowledgeItem,
            ConfidenceStatus,
        )
        state = PipelineState(
            reason=ReasoningState(
                knowledge_items=[
                    KnowledgeItem(
                        key="a", status=ConfidenceStatus.CONFIRMED, confidence=0.9,
                    ),
                    KnowledgeItem(
                        key="b", status=ConfidenceStatus.UNRESOLVED,
                    ),
                ],
            ),
        )
        confirmed = state.reason.get_confirmed_knowledge()
        assert len(confirmed) == 1
        assert confirmed[0].key == "a"


class TestConfidenceScorer:
    """확신도 계산 및 판정 검증."""

    def test_calculate_readiness_empty(self):
        from src.agents.state.state import (
            PipelineState,
            ReasoningState,
        )
        from src.services.confidence_scorer import (
            calculate_readiness,
        )
        state = PipelineState(reason=ReasoningState())
        score = calculate_readiness(state.reason)
        assert 0.0 <= score <= 1.0

    def test_evaluate_readiness_terminate(self):
        from src.agents.state.state import (
            PipelineState,
            ReasoningState,
            LoopGuard,
        )
        from src.services.confidence_scorer import (
            ReadinessVerdict,
            evaluate_readiness,
        )
        state = PipelineState(
            reason=ReasoningState(
                loop_guard=LoopGuard(total_tool_calls=20),
            ),
        )
        assert evaluate_readiness(state.reason) == (
            ReadinessVerdict.TERMINATE
        )

    def test_evaluate_readiness_ask_user(self):
        from src.agents.state.state import (
            PipelineState,
            ReasoningState,
            Hypothesis,
            HypothesisStatus,
            KnowledgeItem,
            ConfidenceStatus,
        )
        from src.services.confidence_scorer import (
            ReadinessVerdict,
            evaluate_readiness,
        )
        state = PipelineState(
            reason=ReasoningState(
                knowledge_items=[
                    KnowledgeItem(
                        key="x",
                        status=ConfidenceStatus.CONFLICTED,
                        is_critical=True,
                        evidence=[
                            "TB_LOAN_MASTER 확인",
                            "TB_LOAN_DETAIL 상이",
                        ],
                    ),
                ],
                hypotheses=[
                    Hypothesis(
                        hypothesis_id="H1",
                        description="t",
                        status=HypothesisStatus.PENDING,
                    ),
                ],
            ),
        )
        assert evaluate_readiness(state.reason) == (
            ReadinessVerdict.ASK_USER
        )

    def test_evaluate_readiness_explore(self):
        from src.agents.state.state import (
            PipelineState,
            ReasoningState,
            ExecutionStep,
            Hypothesis,
            HypothesisStatus,
            StepStatus,
        )
        from src.services.confidence_scorer import (
            ReadinessVerdict,
            evaluate_readiness,
        )
        state = PipelineState(
            reason=ReasoningState(
                execution_plan=[
                    ExecutionStep(
                        step=1,
                        tool="search_table_meta",
                        input="test",
                        purpose="test",
                        status=StepStatus.PENDING,
                    ),
                ],
                hypotheses=[
                    Hypothesis(
                        hypothesis_id="H1",
                        description="t",
                        status=HypothesisStatus.PENDING,
                    ),
                ],
            ),
        )
        assert evaluate_readiness(state.reason) == (
            ReadinessVerdict.EXPLORE
        )


class TestSqlHintExtractor:
    """sqlglot 기반 힌트 추출 검증."""

    def test_parse_simple_sql(self):
        from src.utils.sqlglot_analyzer import (
            parse_sql_safe,
        )
        ast = parse_sql_safe("SELECT 1 FROM t")
        assert ast is not None

    def test_parse_invalid_sql(self):
        from src.utils.sqlglot_analyzer import (
            parse_sql_safe,
        )
        ast = parse_sql_safe("NOT VALID SQL !!!")
        assert ast is None

    def test_extract_join_patterns(self):
        from src.utils.sqlglot_analyzer import (
            extract_structural_hints,
        )
        sql = (
            "SELECT a.id FROM t1 a "
            "JOIN t2 b ON a.id = b.t1_id"
        )
        hints = extract_structural_hints(sql)
        assert len(hints["join_patterns"]) >= 1

    def test_extract_agg_expressions(self):
        from src.utils.sqlglot_analyzer import (
            extract_structural_hints,
        )
        sql = "SELECT COUNT(*), SUM(amt) FROM t"
        hints = extract_structural_hints(sql)
        assert len(hints["agg_expressions"]) >= 1

    def test_merge_hints(self):
        from src.utils.sqlglot_analyzer import merge_hints
        h1 = {
            "join_patterns": ["a.id = b.id"],
            "agg_expressions": [],
        }
        h2 = {
            "join_patterns": ["a.id = b.id"],
            "agg_expressions": ["COUNT(*)"],
        }
        merged = merge_hints([h1, h2])
        assert len(merged["join_patterns"]) == 1
        assert len(merged["agg_expressions"]) == 1

    def test_get_real_tables(self):
        from src.utils.sqlglot_analyzer import get_real_tables
        sql = (
            "SELECT * FROM users u JOIN orders o "
            "ON u.id = o.user_id"
        )
        tables = get_real_tables(sql)
        assert "users" in tables
        assert "orders" in tables


class TestStateConversion:
    """PipelineState ↔ ReasoningState 변환 검증."""

    def test_pipeline_state_creation(self):
        from src.agents.state.state import (
            PipelineState,
            ReasoningState,
        )
        state = PipelineState(
            preprocessed_input="이번 달 신규 고객 수",
            normalized_query=None,
            conversation_history=[
                {"role": "user", "content": "안녕"},
            ],
        )
        assert state.preprocessed_input == "이번 달 신규 고객 수"
        assert len(state.conversation_history) == 1

    def test_reason_success_state(self):
        from src.agents.state.state import (
            PipelineState,
            ReasoningState,
            TableMeta,
            ColumnInfo,
            FinalStatus,
            KnowledgeItem,
            ConfidenceStatus,
        )
        state = PipelineState(
            reason=ReasoningState(
                validated_sql="SELECT 1",
                generated_sql="SELECT 1",
                final_status=FinalStatus.SUCCESS,
                explored_tables=[
                    TableMeta(
                        table_name="TB_CUST",
                        description="고객",
                        columns=[
                            ColumnInfo(name="CUST_NO"),
                            ColumnInfo(name="NAME"),
                        ],
                    ),
                ],
                knowledge_items=[
                    KnowledgeItem(
                        key="table:TB_CUST",
                        status=ConfidenceStatus.CONFIRMED,
                        confidence=0.9,
                    ),
                ],
            ),
        )
        assert state.reason.validated_sql == "SELECT 1"
        assert len(state.reason.explored_tables) == 1
        assert (
            state.reason.explored_tables[0].table_name
            == "TB_CUST"
        )

    def test_reason_failure_state(self):
        from src.agents.state.state import (
            PipelineState,
            ReasoningState,
            FinalStatus,
        )
        state = PipelineState(
            reason=ReasoningState(
                final_status=FinalStatus.FAILURE,
                exploration_summary="테이블 미발견",
            ),
        )
        assert state.reason.final_status == FinalStatus.FAILURE
        assert "테이블 미발견" in state.reason.exploration_summary

    def test_reason_ask_user_state(self):
        from src.agents.state.state import PipelineState
        from src.agents.models.clarification import (
            AmbiguitySignal, AmbiguityType, ConfidenceLevel,
        )
        signal = AmbiguitySignal(
            source_node="result_finalizer",
            ambiguity_type=AmbiguityType.TABLE,
            decision="ASK",
            confidence=ConfidenceLevel.LOW,
            question="어떤 테이블을 원하시나요?",
            reasoning="테이블 선택 불확실",
        )
        state = PipelineState(pending_signals=[signal])
        assert len(state.pending_signals) == 1
        assert "테이블" in state.pending_signals[0].question


class TestGraphBuilding:
    """LangGraph 그래프 구성 검증."""

    def test_build_pipeline(self):
        from src.agents.graph.pipeline import build_pipeline
        graph = build_pipeline()
        compiled = graph.compile()
        assert compiled is not None

    def test_build_pipeline_duplicate(self):
        from src.agents.graph.pipeline import build_pipeline
        graph = build_pipeline()
        compiled = graph.compile()
        assert compiled is not None

    def test_pipeline_has_reasoning_preparer(self):
        from src.agents.graph.pipeline import build_pipeline
        graph = build_pipeline()
        node_names = list(graph.nodes.keys())
        assert "reasoning_preparer" in node_names

    def test_pipeline_no_legacy_nodes(self):
        from src.agents.graph.pipeline import build_pipeline
        graph = build_pipeline()
        node_names = list(graph.nodes.keys())
        assert "collect_context" not in node_names
        assert "enrich_context" not in node_names
        assert "reason_plan" not in node_names
        assert "reason_explore" not in node_names


class TestRoutingFunctions:
    """라우팅 함수 검증."""

    def test_route_from_validator_success(self):
        from src.agents.state.state import (
            PipelineState,
            ReasoningState,
        )
        from src.agents.graph.pipeline import (
            _route_after_sql_validator,
        )
        state = PipelineState(
            reason=ReasoningState(
                failure_type=None,
            ),
        )
        assert (
            _route_after_sql_validator(state)
            == "conclude_success"
        )

    def test_route_from_validator_syntax_fail(self):
        from src.agents.state.state import (
            PipelineState,
            ReasoningState,
            FailureType,
        )
        from src.agents.graph.pipeline import (
            _route_after_sql_validator,
        )
        state = PipelineState(
            reason=ReasoningState(
                failure_type=FailureType.SQL_SYNTAX,
            ),
        )
        result = _route_after_sql_validator(state)
        assert result in ("fix_syntax", "conclude_failure")

    def test_route_from_validator_structural(self):
        from src.agents.state.state import (
            PipelineState,
            ReasoningState,
            FailureType,
        )
        from src.agents.graph.pipeline import (
            _route_after_sql_validator,
        )
        state = PipelineState(
            reason=ReasoningState(
                failure_type=FailureType.SQL_STRUCTURAL,
            ),
        )
        assert (
            _route_after_sql_validator(state)
            == "replan"
        )

    def test_route_from_replan_done(self):
        from src.agents.state.state import (
            PipelineState,
            ReasoningState,
            Phase,
        )
        from src.agents.graph.pipeline import (
            _route_after_recovery_agent,
        )
        state = PipelineState(
            reason=ReasoningState(phase=Phase.DONE),
        )
        assert (
            _route_after_recovery_agent(state)
            == "result_finalizer"
        )

    def test_route_from_replan_continue(self):
        from src.agents.state.state import (
            PipelineState,
            ReasoningState,
            Phase,
        )
        from src.agents.graph.pipeline import (
            _route_after_recovery_agent,
        )
        state = PipelineState(
            reason=ReasoningState(phase=Phase.GENERATING),
        )
        assert (
            _route_after_recovery_agent(state)
            == "sql_generator"
        )


class TestNodeLogic:
    """개별 노드 로직 검증."""

    @pytest.mark.asyncio
    async def test_readiness_gate_node(self):
        from src.agents.state.state import (
            PipelineState,
            ReasoningState,
            LoopGuard,
            Phase,
        )
        from src.agents.nodes.reason.readiness_gate import (
            readiness_gate_node,
        )
        state = PipelineState(
            reason=ReasoningState(
                loop_guard=LoopGuard(total_tool_calls=20),
            ),
        )
        result = await readiness_gate_node(state)
        assert result["reason"].phase == Phase.DONE

    @pytest.mark.asyncio
    async def test_result_finalizer_success(self):
        from src.agents.state.state import (
            PipelineState,
            ReasoningState,
            Phase,
        )
        from src.agents.nodes.reason.result_finalizer import (
            result_finalizer_node,
        )
        state = PipelineState(
            reason=ReasoningState(validated_sql="SELECT 1"),
        )
        result = await result_finalizer_node(state)
        from src.agents.state.state import FinalStatus
        assert result["reason"].final_status == FinalStatus.SUCCESS
        assert result["reason"].phase == Phase.DONE

    @pytest.mark.asyncio
    async def test_result_finalizer_failure(self):
        from src.agents.state.state import (
            PipelineState,
            ReasoningState,
            FinalStatus,
        )
        from src.agents.nodes.reason.result_finalizer import (
            result_finalizer_node,
        )
        state = PipelineState(
            reason=ReasoningState(
                validated_sql=None, generated_sql=None,
            ),
        )
        result = await result_finalizer_node(state)
        assert result["reason"].final_status == FinalStatus.FAILURE

    @pytest.mark.asyncio
    async def test_result_finalizer_ask_user(self):
        from src.agents.state.state import (
            PipelineState,
            ReasoningState,
            KnowledgeItem,
            ConfidenceStatus,
            Phase,
        )
        from src.agents.nodes.reason.result_finalizer import (
            result_finalizer_node,
        )
        state = PipelineState(
            reason=ReasoningState(
                phase=Phase.VERIFYING,
                knowledge_items=[
                    KnowledgeItem(
                        key="test",
                        status=ConfidenceStatus.CONFLICTED,
                        evidence=["소스A: 값1", "소스B: 값2"],
                    ),
                ],
            ),
        )
        result = await result_finalizer_node(state)
        signals = result.get("pending_signals", [])
        assert len(signals) >= 1
        from src.agents.state.state import FinalStatus
        assert result["reason"].final_status == FinalStatus.PENDING

    def test_recovery_agent_hypothesis_transition(self):
        """recovery_agent: ACTIVE 가설 FAILED 전환 + PENDING 소비."""
        from src.agents.state.state import (
            ReasoningState,
            Hypothesis,
            HypothesisStatus,
            FailureType,
        )
        from src.agents.nodes.reason.recovery_agent import (
            _handle_hypothesis_transition,
        )
        reason = ReasoningState(
            hypotheses=[
                Hypothesis(
                    hypothesis_id="H1",
                    description="t",
                    status=HypothesisStatus.ACTIVE,
                ),
                Hypothesis(
                    hypothesis_id="H2",
                    description="대안",
                    status=HypothesisStatus.PENDING,
                ),
            ],
            current_hypothesis=Hypothesis(
                hypothesis_id="H1",
                description="t",
                status=HypothesisStatus.ACTIVE,
            ),
            failure_type=FailureType.SQL_STRUCTURAL,
            failure_reason="구조 불일치",
        )
        _handle_hypothesis_transition(reason)
        # H1 → FAILED, H2 → ACTIVE
        assert reason.current_hypothesis is not None
        assert reason.current_hypothesis.hypothesis_id == "H2"
        assert reason.current_hypothesis.status == HypothesisStatus.ACTIVE
        assert len(reason.dead_ends) == 1
        h1 = [h for h in reason.hypotheses if h.hypothesis_id == "H1"][0]
        assert h1.status == HypothesisStatus.FAILED

    @pytest.mark.asyncio
    async def test_sql_validator_empty_sql(self):
        from src.agents.state.state import (
            PipelineState,
            ReasoningState,
            FailureType,
        )
        from src.agents.nodes.reason.sql_validator import (
            sql_validator_node,
        )
        state = PipelineState(
            reason=ReasoningState(generated_sql=None),
        )
        result = await sql_validator_node(state)
        assert result["reason"].failure_type == FailureType.SQL_SYNTAX

    def test_reasoning_preparer_decomposition(self):
        from src.agents.nodes.reason.reasoning_preparer import (
            _build_decomposition_from_normalized,
        )
        result = _build_decomposition_from_normalized(None)
        assert "measures" in result
        assert "filters" in result
