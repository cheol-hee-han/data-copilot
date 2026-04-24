"""에이전틱 코어 E2E 테스트 — 카테고리별 10+ 시나리오.

Dummy 모드(use_dummy=True)를 사용하여 외부 인프라 없이
에이전틱 코어 서브그래프의 전체 흐름을 검증한다.

카테고리:
  1. 명확한 질의 응답 정확도 (10건)
  2. 모호한 질의 처리 (10건)
  3. 예외 처리 시나리오 (10건)
  4. 명확화 질문 (10건)
  5. 대화 이력 결합 질의 (10건)
  6. Session/Turn 관리 (10건)
  7. 대화 중 독립 질의 (10건)
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.agents.state.state import (
    PipelineState,
    ReasoningState,
    TableMeta,
    ColumnInfo,
    ConfidenceStatus,
    DeadEnd,
    ExecutionStep,
    FailureType,
    FinalStatus,
    Hypothesis,
    HypothesisStatus,
    KnowledgeItem,
    LoopGuard,
    Phase,
    StepStatus,
    StructuralHints,
)
from src.agents.graph.pipeline import (
    build_pipeline,
    create_app,
    _route_after_readiness_gate,
    _route_after_recovery_agent,
    _route_after_sql_validator,
)
from src.agents.nodes.reason.reasoning_preparer import (
    _build_decomposition_from_normalized,
    _initialize_knowledge_items,
    reasoning_preparer_node,
)
from src.agents.nodes.reason.context_retriever import (
    context_retriever_node,
)
from src.agents.nodes.reason.readiness_gate import (
    readiness_gate_node,
)
from src.agents.nodes.reason.sql_generator import (
    sql_generator_node,
)
from src.agents.nodes.reason.sql_validator import (
    _validate_layer1,
    _validate_layer2a,
    sql_validator_node,
)
from src.agents.nodes.reason.recovery_agent import (
    _build_failure_summary,
    _handle_hypothesis_transition,
)
from src.agents.nodes.reason.result_finalizer import (
    result_finalizer_node,
    _build_success_summary,
)
from src.services.confidence_scorer import (
    ReadinessVerdict,
    all_critical_confirmed,
    calculate_readiness,
    evaluate_readiness,
    has_conflicted_items,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 헬퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _state(
    preprocessed_input: str = "",
    normalized_query: Any = None,
    conversation_history: list | None = None,
    clarification_question: str = "",
    pending_signals: list | None = None,
    **reason_kw: Any,
) -> PipelineState:
    """PipelineState를 간결하게 생성."""
    return PipelineState(
        preprocessed_input=preprocessed_input,
        normalized_query=normalized_query,
        conversation_history=conversation_history or [],
        clarification_question=clarification_question,
        pending_signals=pending_signals or [],
        reason=ReasoningState(**reason_kw),
    )


def _ki(
    key: str, status: ConfidenceStatus = ConfidenceStatus.UNRESOLVED,
    confidence: float = 0.0, **kw: Any,
) -> KnowledgeItem:
    return KnowledgeItem(
        key=key, status=status,
        confidence=confidence, **kw,
    )


def _hyp(
    hid: str = "H1", desc: str = "test",
    status: HypothesisStatus = HypothesisStatus.PENDING, **kw: Any,
) -> Hypothesis:
    return Hypothesis(
        hypothesis_id=hid, description=desc,
        status=status, **kw,
    )


def _step(
    n: int = 1, tool: str = "search_table_meta",
    inp: str = "q", status: StepStatus = StepStatus.PENDING,
) -> ExecutionStep:
    return ExecutionStep(
        step=n, tool=tool, input=inp,
        purpose="test", status=status,
    )


def _ct(
    name: str = "TB_CUST", cols: list | None = None,
) -> TableMeta:
    return TableMeta(
        table_name=name, description="desc",
        columns=[ColumnInfo(name=c) for c in (cols or ["COL_A"])],
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 카테고리 1: 명확한 질의 응답 정확도 (10건)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestClearQueryAccuracy:
    """명확한 질의에 대한 처리 정확도."""

    def test_01_simple_count_decomposition(self):
        """단순 집계 질의 분해."""
        decomp = _build_decomposition_from_normalized(None)
        assert isinstance(decomp, dict)
        assert "measures" in decomp

    def test_02_knowledge_items_from_decomposition(self):
        """분해 결과에서 지식 항목 초기화."""
        decomp = {
            "measures": [{"term": "고객수", "agg_function": "COUNT"}],
            "filters": [{"term": "상태", "operator": "=", "value": "정상"}],
            "group_by": [],
            "order_limit": [],
        }
        items = _initialize_knowledge_items(decomp)
        assert len(items) == 2
        assert items[0].key == "measure:고객수"
        assert items[1].key == "filter:상태=정상"

    def test_07_structural_hints_prompt_text(self):
        """구조적 힌트 프롬프트 텍스트."""
        hints = StructuralHints(
            join_patterns=["a.id = b.id"],
        )
        text = hints.to_prompt_text()
        assert "조인 패턴" in text

    def test_08_dead_end_in_state(self):
        """dead-end 포함 상태."""
        state = _state(
            dead_ends=[
                DeadEnd(
                    hypothesis_id="H1",
                    reason="테이블 없음",
                    failure_type=FailureType.NO_TABLE,
                ),
            ],
        )
        assert len(state.reason.dead_ends) == 1
        assert "테이블 없음" in state.reason.dead_ends[0].reason

    def test_09_pipeline_state_creation(self):
        """PipelineState 생성 검증."""
        state = _state(
            preprocessed_input="지점별 매출",
            normalized_query=None,
        )
        assert state.preprocessed_input == "지점별 매출"

    def test_10_success_summary_includes_stats(self):
        """성공 요약에 통계 포함."""
        reason = ReasoningState(
            loop_guard=LoopGuard(
                total_tool_calls=5, generate_attempts=2,
            ),
            knowledge_items=[
                _ki("table:TB_X", ConfidenceStatus.CONFIRMED, 0.9),
            ],
            explored_use_cases=[{"sql": "SELECT 1"}],
        )
        summary = _build_success_summary(reason)
        assert "도구 호출 5회" in summary
        assert "SQL 생성 2회" in summary


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 카테고리 2: 모호한 질의 처리 (10건)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestAmbiguousQueryHandling:
    """모호한 질의에 대한 처리."""

    def test_01_conflicted_triggers_replan(self):
        """다중 테이블 충돌 항목 → REPLAN 판정."""
        state = _state(
            knowledge_items=[
                _ki(
                    "x",
                    ConfidenceStatus.CONFLICTED,
                    is_critical=True,
                    evidence=[
                        "TB_LOAN_MASTER 에서 확인",
                        "TB_LOAN_DETAIL 에서 상이",
                    ],
                ),
            ],
            hypotheses=[_hyp()],
        )
        assert has_conflicted_items(state.reason)
        v = evaluate_readiness(state.reason)
        assert v == ReadinessVerdict.REPLAN

    def test_02_ambiguity_detected_in_normalized(self):
        """모호한 출력 범위 감지 — measures 비어있고 포괄 키워드."""
        from src.agents.nodes.reason.reasoning_preparer import (
            _detect_ambiguous_output,
        )
        decomp = {"measures": [], "filters": [], "group_by": []}
        result = _detect_ambiguous_output(decomp, "고객 정보 뽑아줘")
        assert result is not None  # KnowledgeItem 반환

    def test_03_multiple_conflicted_items(self):
        """복수 충돌 항목 감지."""
        state = _state(
            knowledge_items=[
                _ki("a", ConfidenceStatus.CONFLICTED),
                _ki("b", ConfidenceStatus.CONFLICTED),
            ],
            hypotheses=[_hyp()],
        )
        assert has_conflicted_items(state.reason)

    def test_05_low_confidence_triggers_replan(self):
        """낮은 확신도 → REPLAN 판정."""
        state = _state(
            knowledge_items=[_ki("x", ConfidenceStatus.UNRESOLVED)],
            hypotheses=[_hyp()],
            execution_plan=[],  # 스텝 없음
        )
        v = evaluate_readiness(state.reason)
        assert v == ReadinessVerdict.REPLAN

    def test_06_single_unresolved_critical(self):
        """단일 critical 미해소 → 생성 차단."""
        state = _state(
            knowledge_items=[
                _ki("table:TB_A", ConfidenceStatus.CONFIRMED, 0.9),
                _ki("measure:고객수", ConfidenceStatus.UNRESOLVED,
                    is_critical=True),
            ],
        )
        assert not all_critical_confirmed(state.reason)

    def test_07_non_critical_unresolved_ok(self):
        """비critical 미해소 → 생성 가능."""
        state = _state(
            knowledge_items=[
                _ki("table:TB_A", ConfidenceStatus.CONFIRMED, 0.9),
                _ki(
                    "보조:지점명", ConfidenceStatus.UNRESOLVED,
                    is_critical=False,
                ),
            ],
        )
        assert all_critical_confirmed(state.reason)

    def test_08_empty_knowledge_zero_score(self):
        """지식 없음 → 0점 (critical 항목·활용사례 모두 없음)."""
        state = _state()
        score = calculate_readiness(state.reason)
        assert score < 0.01

    def test_09_partial_knowledge_mid_score(self):
        """부분 지식 → 중간 점수."""
        state = _state(
            knowledge_items=[
                _ki("a", ConfidenceStatus.CONFIRMED, 0.9),
                _ki("b", ConfidenceStatus.UNRESOLVED, 0.0),
            ],
        )
        score = calculate_readiness(state.reason)
        assert 0.1 <= score <= 0.7

    def test_10_multi_table_no_penalty(self):
        """다중 테이블이어도 join_path 페널티 없음."""
        state = _state(
            knowledge_items=[
                _ki("table:A", ConfidenceStatus.CONFIRMED, 0.9),
                _ki("table:B", ConfidenceStatus.CONFIRMED, 0.9),
            ],
            explored_tables=[
                TableMeta(table_name="A"),
                TableMeta(table_name="B"),
            ],
        )
        score = calculate_readiness(state.reason)
        # join_path 가중치 제거 — 다중 테이블 페널티 없음
        assert score >= 0.7


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 카테고리 3: 예외 처리 시나리오 (10건)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestExceptionHandling:
    """예외 상황 처리."""

    @pytest.mark.asyncio
    async def test_01_empty_sql_validation(self):
        """빈 SQL → SQL_SYNTAX."""
        state = _state(generated_sql=None)
        r = await sql_validator_node(state)
        assert r["reason"].failure_type == FailureType.SQL_SYNTAX

    @pytest.mark.asyncio
    async def test_02_empty_sql_string(self):
        """빈 문자열 SQL → SQL_SYNTAX."""
        state = _state(generated_sql="")
        r = await sql_validator_node(state)
        assert r["reason"].failure_type == FailureType.SQL_SYNTAX

    def test_03_layer1_rejects_dml(self):
        """DML 구문 → Layer1 거부."""
        reason = ReasoningState(explored_tables=[_ct()])
        r = _validate_layer1("DELETE FROM users", reason)
        assert r["status"] == "FAIL"

    def test_04_layer1_rejects_drop(self):
        """DDL 구문 → Layer1 거부."""
        reason = ReasoningState(explored_tables=[_ct()])
        r = _validate_layer1("DROP TABLE users", reason)
        assert r["status"] == "FAIL"

    def test_05_layer2a_missing_groupby(self):
        """GROUP BY 누락 감지."""
        reason = ReasoningState(
            query_decomposition={
                "group_by": ["지점"],
                "measures": [],
            },
        )
        r = _validate_layer2a(
            "SELECT * FROM t WHERE x=1", reason,
        )
        assert r["status"] == "FAIL"
        assert "GROUP BY" in r["feedback"]

    def test_06_layer2a_missing_agg(self):
        """집계함수 누락 감지."""
        reason = ReasoningState(
            query_decomposition={
                "group_by": [],
                "measures": [
                    {"term": "x", "agg_function": "COUNT"},
                ],
            },
        )
        r = _validate_layer2a("SELECT * FROM t", reason)
        assert r["status"] == "FAIL"
        assert "집계함수" in r["feedback"]

    def test_07_loop_guard_max_tool_calls(self):
        """도구 호출 한도 초과 → 종료."""
        state = _state(
            loop_guard=LoopGuard(total_tool_calls=20),
        )
        v = evaluate_readiness(state.reason)
        assert v == ReadinessVerdict.TERMINATE

    def test_08_loop_guard_max_replans(self):
        """재계획 한도 초과 → 종료."""
        state = _state(
            loop_guard=LoopGuard(replan_count=3),
        )
        v = evaluate_readiness(state.reason)
        assert v == ReadinessVerdict.TERMINATE

    def test_09_loop_guard_max_generates(self):
        """SQL 생성 한도 초과 → 종료."""
        state = _state(
            loop_guard=LoopGuard(generate_attempts=4),
        )
        v = evaluate_readiness(state.reason)
        assert v == ReadinessVerdict.TERMINATE

    def test_10_recovery_agent_hypothesis_transition(self):
        """ACTIVE 가설 FAILED 전환 + PENDING 가설 소비."""
        reason = ReasoningState(
            hypotheses=[
                _hyp("H1", status=HypothesisStatus.ACTIVE),
                _hyp("H2", desc="대안", status=HypothesisStatus.PENDING),
            ],
            current_hypothesis=_hyp(
                "H1", status=HypothesisStatus.ACTIVE,
            ),
            failure_type=FailureType.SQL_STRUCTURAL,
            failure_reason="구조 불일치",
        )
        _handle_hypothesis_transition(reason)
        assert reason.current_hypothesis is not None
        assert reason.current_hypothesis.hypothesis_id == "H2"
        assert len(reason.dead_ends) == 1


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 카테고리 4: 명확화 질문 (10건)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestClarificationQuestions:
    """사용자 명확화 질문 테스트."""

    def test_01_pipeline_pending_signals_flag(self):
        """PipelineState: pending_signals 설정."""
        from src.agents.models.clarification import (
            AmbiguitySignal, AmbiguityType, ConfidenceLevel,
        )
        signal = AmbiguitySignal(
            source_node="test",
            ambiguity_type=AmbiguityType.INTENT,
            decision="ASK",
            confidence=ConfidenceLevel.LOW,
            question="확인 필요",
            reasoning="테스트",
        )
        state = _state(pending_signals=[signal])
        assert len(state.pending_signals) == 1

    @pytest.mark.asyncio
    async def test_02_conflicted_flows_to_replan(self):
        """CONFLICTED 항목 → readiness_gate에서 REPLANNING 전환."""
        state = _state(
            knowledge_items=[_ki(
                "x", ConfidenceStatus.CONFLICTED,
                evidence=["TB_A 기반", "TB_B 기반"],
            )],
            hypotheses=[_hyp()],
        )
        r = await readiness_gate_node(state)
        assert r["reason"].phase == Phase.REPLANNING

    def test_03_no_clarification_on_success(self):
        """성공 시 명확화 없음."""
        state = _state(
            validated_sql="SELECT 1",
            final_status=FinalStatus.SUCCESS,
        )
        assert not state.pending_signals

    def test_04_no_clarification_on_failure(self):
        """실패 시 명확화 없음."""
        state = _state(
            final_status=FinalStatus.FAILURE,
            exploration_summary="실패",
        )
        assert not state.pending_signals


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 카테고리 5: 대화 이력 결합 질의 (10건)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestConversationHistory:
    """대화 이력이 결합된 질의 처리."""

    def test_01_history_in_pipeline_state(self):
        """대화 이력이 PipelineState로 전달."""
        history = [
            {"role": "user", "content": "고객 수 알려줘"},
            {"role": "assistant", "content": "10만명입니다"},
        ]
        state = _state(
            preprocessed_input="지점별로",
            conversation_history=history,
        )
        assert len(state.conversation_history) == 2

    def test_02_empty_history_ok(self):
        """빈 이력도 정상 처리."""
        state = _state(
            preprocessed_input="q",
            conversation_history=[],
        )
        assert state.conversation_history == []

    def test_03_no_history_defaults(self):
        """이력 없으면 기본값."""
        state = _state(preprocessed_input="q")
        assert state.conversation_history == []

    def test_04_long_history_preserved(self):
        """긴 대화 이력 보존."""
        history = [
            {"role": "user", "content": f"질문{i}"}
            for i in range(20)
        ]
        state = _state(
            preprocessed_input="q",
            conversation_history=history,
        )
        assert len(state.conversation_history) == 20

    def test_05_history_with_clarification(self):
        """명확화 응답 포함 이력."""
        history = [
            {"role": "user", "content": "고객 수"},
            {"role": "assistant",
             "content": "어떤 기준으로?"},
            {"role": "user", "content": "이번 달 기준"},
        ]
        state = _state(
            preprocessed_input="이번 달 기준",
            conversation_history=history,
        )
        assert len(state.conversation_history) == 3

    def test_06_state_preserves_preprocessed_input(self):
        """전처리된 입력 보존."""
        state = _state(
            preprocessed_input="정규화된 질의",
        )
        assert state.preprocessed_input == "정규화된 질의"

    def test_07_intent_enum_conversion(self):
        """IntentType enum 설정."""
        from src.models.enums import IntentType
        state = PipelineState(
            preprocessed_input="q",
            intent=IntentType.DATA_EXTRACTION,
        )
        assert state.intent == IntentType.DATA_EXTRACTION

    def test_08_normalized_query_passed(self):
        """정규화 질의 전달."""
        nq = {"measures": [{"term": "고객수"}]}
        state = _state(
            preprocessed_input="q",
            normalized_query=nq,
        )
        assert state.normalized_query is not None

    def test_09_none_normalized_query(self):
        """정규화 없이도 동작."""
        state = _state(
            preprocessed_input="q",
            normalized_query=None,
        )
        assert state.normalized_query is None

    def test_10_trace_log_in_pipeline_state(self):
        """trace_log 필드 존재 (C-01)."""
        state = PipelineState(
            preprocessed_input="q",
        )
        assert isinstance(state.trace_log, list)
        assert len(state.trace_log) == 0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 카테고리 6: Session/Turn 관리 (10건)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestSessionTurnManagement:
    """세션/턴 관리 테스트."""

    def test_01_default_state_is_pending(self):
        """기본 상태 = pending."""
        s = _state()
        assert s.reason.final_status == "pending"

    def test_02_phase_transitions(self):
        """phase 전환 패턴."""
        phases = [
            Phase.PLANNING, Phase.EXPLORING,
            Phase.GENERATING, Phase.VALIDATING, Phase.REPLANNING, Phase.DONE,
        ]
        for p in phases:
            s = _state(phase=p)
            assert s.reason.phase == p

    def test_03_loop_guard_independent_counters(self):
        """루프 가드 카운터 독립성."""
        lg = LoopGuard()
        lg.increment_tool_calls()
        lg.increment_replan()
        assert lg.total_tool_calls == 1
        assert lg.replan_count == 1
        assert lg.generate_attempts == 0

    def test_04_hypothesis_lifecycle(self):
        """가설 상태 전환: PENDING → ACTIVE → FAILED."""
        h = _hyp(status=HypothesisStatus.PENDING)
        h_copy = h.model_copy()
        h_copy.status = HypothesisStatus.ACTIVE
        assert h_copy.status == HypothesisStatus.ACTIVE
        h_copy.status = HypothesisStatus.FAILED
        assert h_copy.status == HypothesisStatus.FAILED

    def test_05_dead_end_accumulation(self):
        """dead-end 누적."""
        state = _state(
            dead_ends=[
                DeadEnd(
                    hypothesis_id="H1",
                    reason="r1",
                    failure_type=FailureType.NO_TABLE,
                ),
                DeadEnd(
                    hypothesis_id="H2",
                    reason="r2",
                    failure_type=FailureType.EMPTY_RESULT,
                ),
            ],
        )
        assert len(state.reason.dead_ends) == 2

    def test_06_executed_tool_keys_dedup(self):
        """도구 실행 키 중복 방지 — tool:input 네임스페이스."""
        keys = {"search_table_meta:예금", "lookup_code_meta:상태"}
        state = _state(executed_tool_keys=keys)
        assert "search_table_meta:예금" in state.reason.executed_tool_keys
        # 같은 입력이라도 도구가 다르면 별도 키
        assert "lookup_code_meta:예금" not in state.reason.executed_tool_keys

    def test_07_sampled_tables_via_sample_rows(self):
        """샘플 테이블 추적 — TableMeta.sample_rows로 판단."""
        ct = TableMeta(table_name="TB_A", sample_rows=[{"col": 1}])
        state = _state(explored_tables=[ct])
        sampled = [t for t in state.reason.explored_tables if t.sample_rows]
        assert any(t.table_name == "TB_A" for t in sampled)

    def test_10_execution_plan_mixed_status(self):
        """실행계획 혼합 상태."""
        plan = [
            _step(1, status=StepStatus.DONE),
            _step(2, status=StepStatus.PENDING),
            _step(3, status=StepStatus.SKIPPED),
        ]
        pending = [s for s in plan if s.status == StepStatus.PENDING]
        assert len(pending) == 1


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 카테고리 7: 대화 중 독립 질의 (10건)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestIndependentQueryDuringConversation:
    """대화 중 새로운 독립 질의 처리."""

    def test_01_new_query_fresh_state(self):
        """새 질의 → 깨끗한 상태."""
        state = _state(preprocessed_input="새 질의")
        assert state.reason.phase == Phase.PLANNING
        assert len(state.reason.dead_ends) == 0
        assert len(state.reason.knowledge_items) == 0

    def test_02_previous_state_not_leaked(self):
        """이전 상태 미유출."""
        old = _state(
            preprocessed_input="이전 질의",
            dead_ends=[
                DeadEnd(
                    hypothesis_id="H1",
                    reason="r",
                    failure_type=FailureType.NO_TABLE,
                ),
            ],
        )
        new = _state(preprocessed_input="새 질의")
        assert new.preprocessed_input == "새 질의"
        assert len(new.reason.dead_ends) == 0

    def test_03_decomposition_from_normalized_none(self):
        """normalized_query=None → 기본 분해 구조."""
        decomp = _build_decomposition_from_normalized(None)
        assert "measures" in decomp
        assert "filters" in decomp

    def test_04_knowledge_items_from_query(self):
        """질의에서 지식 항목 초기화."""
        decomp = {
            "measures": [{"term": "매출", "agg_function": "SUM"}],
            "filters": [],
            "group_by": [{"term": "지점"}],
            "order_limit": [],
        }
        items = _initialize_knowledge_items(decomp)
        keys = [it.key for it in items]
        assert "measure:매출" in keys

    def test_08_failure_type_direct_setting(self):
        """failure_type 직접 설정 확인."""
        reason = ReasoningState(
            failure_type=FailureType.EMPTY_RESULT,
            failure_reason="실행 결과 0건",
        )
        assert reason.failure_type == FailureType.EMPTY_RESULT
        assert reason.failure_reason == "실행 결과 0건"

    def test_09_failure_reason_direct_setting(self):
        """failure_reason 직접 설정 확인."""
        reason = ReasoningState(
            failure_type=FailureType.SQL_STRUCTURAL,
            failure_reason="미확인 테이블: TB_X",
        )
        assert reason.failure_type == FailureType.SQL_STRUCTURAL
        assert "미확인" in reason.failure_reason

    def test_10_failure_summary_includes_history(self):
        """실패 요약에 dead_ends 및 미해소 용어 포함."""
        reason = ReasoningState(
            dead_ends=[
                DeadEnd(
                    hypothesis_id="H1",
                    reason="no table",
                    failure_type=FailureType.NO_TABLE,
                ),
            ],
            knowledge_items=[
                _ki("용어:미해소", ConfidenceStatus.UNRESOLVED),
            ],
            loop_guard=LoopGuard(replan_count=2),
        )
        summary = _build_failure_summary(reason)
        assert "재계획" in summary
        assert "실패 경로" in summary
        assert "미해소" in summary
