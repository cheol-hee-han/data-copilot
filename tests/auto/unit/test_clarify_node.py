"""명확화 질문 생성 노드(clarify_node) 테스트.

테스트 대상:
    모호한 사용자 질의에 대해 선택지 형태의 명확화 질문을 생성하는 노드를 검증한다.
    _build_messages(순수 함수)와 clarify_node(LLM 호출)를 분리 테스트한다.

    ┌─────────────────────────────────────────────────────────────────┐
    │  테스트 구간              테스트 내용                LLM 필요   │
    │  ──────────────────────── ──────────────────────────── ────── │
    │  _build_messages          메시지 조립, 히스토리 제한      X     │
    │  clarify_node             명확화 질문 생성, 상태 설정     O     │
    └─────────────────────────────────────────────────────────────────┘

입력 예시 (정상):
    - preprocessed_input = "데이터 뽑아줘"
    - 기대: clarification_question = "어떤 데이터가 필요하신가요? 1) ... 2) ..."
    - awaiting_clarification = True, status = AWAITING_CLARIFICATION

결과 예시 (오류 케이스):
    - 대화 히스토리 5턴 → 최근 4턴만 포함 (오래된 턴 제외)

실행 스크립트:
    # 순수 함수 테스트만 (LLM 불필요)
    pytest tests/unit/test_clarify_node.py -v -k "build_messages"

    # LLM 포함 전체 (API 키 필요)
    pytest tests/unit/test_clarify_node.py -v

참고:
    - LLM 테스트는 ANTHROPIC_API_KEY 또는 OPENAI_API_KEY 필요
    - 테스트 대상 소스: src/agents/nodes/clarifier.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import os

import pytest

from tests.conftest import get_test_logger, log_test_case

logger = get_test_logger("test_clarify_node")

_LLM_AVAILABLE = bool(
    os.getenv("ANTHROPIC_API_KEY") or os.getenv("OPENAI_API_KEY")
)

_SKIP_LLM = pytest.mark.skipif(
    not _LLM_AVAILABLE,
    reason="LLM API 키가 없어 건너뜀.",
)


# ──────────────────────────────────────────────────────────────
# 헬퍼
# ──────────────────────────────────────────────────────────────

def _make_state(
    preprocessed_input: str = "데이터 뽑아줘",
    conversation_history: list | None = None,
    clarification_turns: int = 0,
):
    """테스트용 PipelineState 를 생성한다."""
    from src.agents.state.state import ContextInfo, PipelineState

    return PipelineState(
        user_input=preprocessed_input,
        preprocessed_input=preprocessed_input,
        context=ContextInfo(),
        conversation_history=conversation_history or [],
        clarification_turns=clarification_turns,
    )


# ──────────────────────────────────────────────────────────────
# _build_messages 순수 함수 테스트
# ──────────────────────────────────────────────────────────────

def test_build_messages_format():
    """_build_messages 가 현재 입력을 user role 메시지로 포함한다."""
    from src.agents.nodes.interpret.clarifier import _build_messages

    state = _make_state(preprocessed_input="어떤 데이터가 있어?")
    messages = _build_messages(state)

    passed = (
        len(messages) >= 1
        and messages[-1]["role"] == "user"
        and messages[-1]["content"] == "어떤 데이터가 있어?"
    )
    log_test_case(
        logger,
        "test_build_messages_format",
        input_data="preprocessed_input='어떤 데이터가 있어?'",
        expected="마지막 메시지 role='user', content='어떤 데이터가 있어?'",
        actual=messages[-1] if messages else "없음",
        passed=passed,
    )
    assert passed


def test_build_messages_limits_history():
    """대화 히스토리가 5턴이어도 최근 4턴만 포함된다."""
    from src.agents.nodes.interpret.clarifier import _build_messages

    # 5턴의 히스토리 생성
    history = [
        {"role": "user", "content": f"질의 {i}"}
        for i in range(1, 6)
    ]
    state = _make_state(
        preprocessed_input="최신 질의",
        conversation_history=history,
    )
    messages = _build_messages(state)

    # 히스토리 4턴 + 현재 입력 1 = 최대 5
    # 히스토리는 최근 4턴이므로 "질의 2" ~ "질의 5" 포함, "질의 1" 제외
    history_contents = [m["content"] for m in messages[:-1]]

    passed = (
        len(messages) <= 5
        and "질의 1" not in history_contents
        and "질의 5" in history_contents
    )
    log_test_case(
        logger,
        "test_build_messages_limits_history",
        input_data="conversation_history 5턴",
        expected="메시지 수 <= 5, '질의 1' 제외, '질의 5' 포함",
        actual=f"메시지 수={len(messages)}, contents={history_contents}",
        passed=passed,
    )
    assert passed


def test_build_messages_no_history():
    """대화 히스토리가 없으면 현재 입력 1개만 포함된다."""
    from src.agents.nodes.interpret.clarifier import _build_messages

    state = _make_state(preprocessed_input="고객 수 알려줘")
    messages = _build_messages(state)

    passed = len(messages) == 1 and messages[0]["content"] == "고객 수 알려줘"
    log_test_case(
        logger,
        "test_build_messages_no_history",
        input_data="conversation_history=[]",
        expected="메시지 1개 (현재 입력만)",
        actual=f"메시지 수={len(messages)}",
        passed=passed,
    )
    assert passed


def test_build_messages_with_history_included():
    """대화 히스토리가 3턴이면 3 + 현재 = 4개가 된다."""
    from src.agents.nodes.interpret.clarifier import _build_messages

    history = [
        {"role": "user", "content": "이전 질의"},
        {"role": "assistant", "content": "명확화 질문"},
        {"role": "user", "content": "답변"},
    ]
    state = _make_state(
        preprocessed_input="후속 질의",
        conversation_history=history,
    )
    messages = _build_messages(state)

    passed = (
        len(messages) == 4
        and messages[0]["content"] == "이전 질의"
        and messages[-1]["content"] == "후속 질의"
    )
    log_test_case(
        logger,
        "test_build_messages_with_history_included",
        input_data="conversation_history 3턴",
        expected="메시지 4개, 첫='이전 질의', 마지막='후속 질의'",
        actual=f"메시지 수={len(messages)}, 첫={messages[0]['content']}, 마지막={messages[-1]['content']}",
        passed=passed,
    )
    assert passed


def test_build_messages_conversation_history_used():
    """_build_messages 가 conversation_history 의 role 을 유지한다."""
    from src.agents.nodes.interpret.clarifier import _build_messages

    history = [
        {"role": "assistant", "content": "무엇이 필요하신가요?"},
        {"role": "user", "content": "연체율 추이"},
    ]
    state = _make_state(
        preprocessed_input="월별로 알려줘",
        conversation_history=history,
    )
    messages = _build_messages(state)

    # assistant role 이 유지되는지 확인
    roles = [m["role"] for m in messages]
    passed = "assistant" in roles

    log_test_case(
        logger,
        "test_build_messages_conversation_history_used",
        input_data="history에 assistant role 포함",
        expected="messages 에 assistant role 유지",
        actual=f"roles={roles}",
        passed=passed,
    )
    assert passed


# ──────────────────────────────────────────────────────────────
# LLM 통합 테스트
# ──────────────────────────────────────────────────────────────

@_SKIP_LLM
@pytest.mark.asyncio
async def test_clarification_question_generated():
    """모호한 입력에 대해 명확화 질문이 생성된다."""
    from src.agents.nodes.interpret.clarifier import clarify_node

    state = _make_state(preprocessed_input="데이터 뽑아줘")
    result = await clarify_node(state)

    question = result.get("clarification_question", "")
    passed = len(question) > 0

    log_test_case(
        logger,
        "test_clarification_question_generated",
        input_data="'데이터 뽑아줘'",
        expected="비어있지 않은 질문 생성",
        actual=question[:200],
        passed=passed,
    )
    assert passed, "명확화 질문이 비어있음"


@_SKIP_LLM
@pytest.mark.asyncio
async def test_awaiting_flag_set():
    """clarify_node 실행 후 awaiting_clarification=True 로 설정된다."""
    from src.agents.nodes.interpret.clarifier import clarify_node

    state = _make_state(preprocessed_input="분석해줘")
    result = await clarify_node(state)

    passed = result.get("awaiting_clarification") is True

    log_test_case(
        logger,
        "test_awaiting_flag_set",
        input_data="'분석해줘'",
        expected="awaiting_clarification=True",
        actual=f"awaiting_clarification={result.get('awaiting_clarification')}",
        passed=passed,
    )
    assert passed


@_SKIP_LLM
@pytest.mark.asyncio
async def test_status_awaiting():
    """clarify_node 결과의 status 가 AWAITING_CLARIFICATION 이다."""
    from src.agents.nodes.interpret.clarifier import clarify_node
    from src.agents.state.state import QueryStatus

    state = _make_state(preprocessed_input="현황 보여줘")
    result = await clarify_node(state)

    passed = result.get("status") == QueryStatus.AWAITING_CLARIFICATION

    log_test_case(
        logger,
        "test_status_awaiting",
        input_data="'현황 보여줘'",
        expected="status=AWAITING_CLARIFICATION",
        actual=f"status={result.get('status')}",
        passed=passed,
    )
    assert passed


@_SKIP_LLM
@pytest.mark.asyncio
async def test_formatted_response_equals_question():
    """formatted_response 가 clarification_question 과 동일하다."""
    from src.agents.nodes.interpret.clarifier import clarify_node

    state = _make_state(preprocessed_input="뭔가 알려줘")
    result = await clarify_node(state)

    question = result.get("clarification_question", "")
    formatted = result.get("formatted_response", "")

    passed = question == formatted and len(question) > 0

    log_test_case(
        logger,
        "test_formatted_response_equals_question",
        input_data="'뭔가 알려줘'",
        expected="clarification_question == formatted_response",
        actual=f"question='{question[:50]}', formatted='{formatted[:50]}'",
        passed=passed,
    )
    assert passed
