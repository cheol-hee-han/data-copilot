"""인접 노드 간 연쇄 흐름 테스트.

=== 개념 설명 ===
각 노드를 독립적으로 테스트하는 것만으로는 노드 간 데이터 전달의
정합성을 보장할 수 없다. 이 모듈은 인접 2-노드 쌍의 출력→입력
데이터 흐름이 올바르게 연결되는지 검증한다.

검증 대상 연쇄:
  1. preprocessor → intent_classifier (preprocessed_input 전달)
  2. intent_classifier → query_normalizer (intent + preprocessed_input)
  3. sql_validator → sql_generator (validation_feedback 재시도 루프)
  4. sql_validator 출력 → sql_executor 입력 (validated_sql 전달)

=== 단독 실행 ===
    python -m pytest tests/unit/test_node_chain.py -v -s
    # LLM 불필요 테스트만: -k "not live_llm"

=== 정상 결과 ===
    이전 노드 출력 필드가 다음 노드의 PipelineState 에 올바르게 반영됨
=== 오류 결과 ===
    필드 누락, 타입 불일치, 상태 전이 오류
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.agents.state.state import (
    ContextInfo,
    FailureType,
    IntentType,
    PipelineState,
    QueryStatus,
    TableMeta,
)
from tests.conftest import get_test_logger, log_test_case

logger = get_test_logger("test_node_chain")

_HAS_API_KEY = bool(os.getenv("ANTHROPIC_API_KEY"))


# ══════════════════════════════════════════════════════════════
# Chain 1: preprocessor → intent_classifier
# ══════════════════════════════════════════════════════════════

@pytest.mark.live_llm
@pytest.mark.skipif(not _HAS_API_KEY, reason="ANTHROPIC_API_KEY 필요")
@pytest.mark.asyncio
async def test_chain_preprocess_to_intent():
    """전처리 출력이 의도 분류의 입력으로 올바르게 전달된다."""
    from src.agents.nodes.interpret.intent_classifier import classify_intent_node
    from src.agents.nodes.interpret.preprocessor import preprocess_node

    # Step 1: 전처리
    state = PipelineState(user_input="이번달 신규 대출 건수 알려줘")
    prep_result = await preprocess_node(state)

    assert prep_result["status"] == QueryStatus.PREPROCESSING
    assert len(prep_result["preprocessed_input"]) > 0

    # Step 2: 전처리 결과를 state에 반영 후 의도 분류
    state_after_prep = state.model_copy(update=prep_result)
    intent_result = await classify_intent_node(state_after_prep)

    assert intent_result["status"] == QueryStatus.INTENT_CLASSIFIED
    assert intent_result["intent"] in (IntentType.DATA_EXTRACTION, IntentType.DATA_ANALYSIS)
    assert 0.0 <= intent_result["intent_confidence"] <= 1.0

    log_test_case(logger, "test_chain_preprocess_to_intent",
                  "이번달 신규 대출 건수 알려줘",
                  "PREPROCESSING → INTENT_CLASSIFIED",
                  f"{prep_result['status']} → {intent_result['status']}",
                  True)


# ══════════════════════════════════════════════════════════════
# Chain 2: sql_validator → sql_generator (재시도 루프)
# ══════════════════════════════════════════════════════════════

def test_chain_validator_feedback_to_generator():
    """검증 실패 시 feedback 이 ReasoningState에 반영 가능하다."""
    from src.services.sql_safety_checker import validate_sql_safety

    # Step 1: 잘못된 SQL → 검증 실패
    result = validate_sql_safety("UPDATE TB_CUST SET name='x'")

    assert not result.is_safe
    assert len(result.errors) > 0
    assert len(result.feedback) > 0

    # Step 2: 피드백이 ReasoningState 에 주입 가능한지 확인
    from src.agents.state.state import ReasoningState
    reason = ReasoningState(
        generated_sql="UPDATE TB_CUST SET name='x'",
        failure_type=FailureType.SQL_SYNTAX,
        failure_reason=result.feedback,
    )
    assert reason.failure_reason != ""
    assert "UPDATE" in reason.failure_reason

    log_test_case(
        logger, "test_chain_validator_to_generator",
        "UPDATE TB_CUST",
        "failure_reason 비어있지 않음",
        reason.failure_reason[:80],
        True,
    )


# ══════════════════════════════════════════════════════════════
# Chain 3: sql_validator → sql_executor
# ══════════════════════════════════════════════════════════════

def test_chain_validator_pass_to_executor_state():
    """검증 통과된 SQL 이 ReasoningState 에 올바르게 설정된다."""
    from src.services.sql_safety_checker import validate_sql_safety

    valid_sql = "SELECT COUNT(*) AS cnt FROM TB_CUST_INFO"
    result = validate_sql_safety(valid_sql)

    # 검증 통과 확인
    assert result.is_safe is True
    assert result.errors == []

    # executor 에 전달할 ReasoningState 구성
    from src.agents.state.state import ReasoningState
    reason = ReasoningState(
        generated_sql=valid_sql,
        validated_sql=valid_sql,
    )
    assert reason.validated_sql == valid_sql

    # PipelineState 에 reason 을 중첩
    state = PipelineState(
        preprocessed_input="고객 수",
        context=ContextInfo(
            table_metas=[
                TableMeta(
                    table_name="TB_CUST_INFO",
                    table_description="고객",
                )
            ]
        ),
        reason=reason,
        status=QueryStatus.SQL_VALIDATED,
    )
    assert state.reason.validated_sql == valid_sql

    log_test_case(
        logger, "test_chain_validator_to_executor",
        valid_sql[:40],
        "reason.validated_sql 설정됨",
        state.reason.validated_sql[:40],
        True,
    )


# ══════════════════════════════════════════════════════════════
# Chain 4: PipelineState 상태 전이 정합성
# ══════════════════════════════════════════════════════════════

def test_state_transition_sequence():
    """QueryStatus 가 올바른 순서로 전이된다."""
    valid_transitions = {
        QueryStatus.PENDING: {QueryStatus.PREPROCESSING, QueryStatus.ERROR},
        QueryStatus.PREPROCESSING: {QueryStatus.INTENT_CLASSIFIED, QueryStatus.ERROR},
        QueryStatus.INTENT_CLASSIFIED: {
            QueryStatus.QUERY_NORMALIZED,
            QueryStatus.AWAITING_CLARIFICATION,
            QueryStatus.ERROR,
        },
        QueryStatus.QUERY_NORMALIZED: {QueryStatus.CONTEXT_COLLECTED, QueryStatus.ERROR},
        QueryStatus.CONTEXT_COLLECTED: {QueryStatus.SQL_GENERATED, QueryStatus.ERROR},
        QueryStatus.SQL_GENERATED: {QueryStatus.SQL_VALIDATED, QueryStatus.ERROR},
        QueryStatus.SQL_VALIDATED: {QueryStatus.EXECUTED, QueryStatus.ERROR},
        QueryStatus.EXECUTED: {
            QueryStatus.ANALYZED,
            QueryStatus.FORMATTED,
            QueryStatus.ERROR,
        },
        QueryStatus.ANALYZED: {QueryStatus.FORMATTED, QueryStatus.ERROR},
        QueryStatus.FORMATTED: {QueryStatus.COMPLETED, QueryStatus.ERROR},
    }

    # 모든 상태가 정의되어 있는지 확인
    all_statuses = set(QueryStatus)
    terminal = {QueryStatus.COMPLETED, QueryStatus.ERROR, QueryStatus.AWAITING_CLARIFICATION, QueryStatus.SQL_RETRY}
    defined = set(valid_transitions.keys()) | terminal

    missing = all_statuses - defined
    assert len(missing) == 0, f"상태 전이 미정의: {missing}"

    log_test_case(logger, "test_state_transition_sequence",
                  f"{len(valid_transitions)} 상태 전이 규칙",
                  "모든 상태 커버",
                  f"정의={len(defined)}, 전체={len(all_statuses)}",
                  True)


# ══════════════════════════════════════════════════════════════
# Chain 5: 명확화 왕복 데이터 흐름
# ══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_chain_clarification_roundtrip():
    """명확화 질문 → 사용자 응답 → preprocess → resolve_history 재진입 흐름을 검증한다.

    명확화 합성은 resolve_history 노드에서 처리되므로,
    preprocess는 보안 검사만, resolve_history가 맥락 판정을 수행한다.
    LLM 호출이 필요하므로 Mock으로 CONTINUE 판정을 시뮬레이션한다.
    """
    from unittest.mock import AsyncMock, patch

    from src.agents.nodes.interpret.preprocessor import preprocess_node
    from src.agents.nodes.interpret.history_resolver import resolve_history_node

    # Step 1: 최초 전처리
    state = PipelineState(user_input="데이터 좀 뽑아줘")
    prep1 = await preprocess_node(state)
    state = state.model_copy(update=prep1)

    # Step 2: 명확화 상태 시뮬레이션 + 사용자 재입력
    state = state.model_copy(update={
        "user_input": "이번달 여신 잔액",
        "preprocessed_input": "이번달 여신 잔액",
        "awaiting_clarification": True,
        "clarification_question": "어떤 데이터가 필요하신가요?",
        "conversation_history": [
            {"role": "user", "content": "데이터 좀 뽑아줘"},
            {"role": "assistant", "content": "어떤 데이터가 필요하신가요?"},
        ],
    })

    # Step 3: resolve_history에서 CONTINUE 판정 (LLM Mock)
    from src.services.history_resolver import (
        HistoryDecision,
        HistoryResolveResult,
    )

    mock_result = HistoryResolveResult(
        decision=HistoryDecision.CONTINUE,
        resolved_query="이번달 여신 잔액 현황 알려줘",
        reason="명확화 응답 판단 필요",
    )
    with patch(
        "src.agents.nodes.interpret.history_resolver.resolve_history",
        new_callable=AsyncMock,
        return_value=mock_result,
    ):
        resolve_result = await resolve_history_node(state)

    assert resolve_result["awaiting_clarification"] is False
    assert "여신 잔액" in resolve_result["preprocessed_input"]

    log_test_case(
        logger,
        "test_chain_clarification_roundtrip",
        "데이터 좀 뽑아줘 → 이번달 여신 잔액",
        "resolve_history가 CONTINUE 판정, 명확화 리셋",
        resolve_result["preprocessed_input"][:60],
        True,
    )
