"""LangGraph 커스텀 이벤트 디스패치 유틸리티 — 안전한 추적 이벤트 전송.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

LLM 클라이언트, 커넥터 등 그래프 노드 바깥의 유틸리티에서
DataCopilotCallbackHandler로 추적 이벤트를 전달하는 단일 진입점이다.
LangGraph 실행 컨텍스트 내에서만 이벤트를 전달하며,
그래프 외부(단위 테스트 등)에서 호출되면 RuntimeError를 잡아 조용히 무시한다.

직접 adispatch_custom_event를 호출하지 않고 이 모듈을 경유하는 이유:
  - 그래프 외부 호출 시 RuntimeError를 안전하게 억제
  - 이벤트 이름 상수를 한 곳에서 관리하여 오타 방지
  - Python 3.12에서 contextvars 기반 RunnableConfig 자동 추출을
    활용하므로 명시적 config 전달이 불필요

핵심 함수:
    - dispatch_tracking_event: 그래프 컨텍스트 안전 이벤트 디스패치

사용법::

    from src.utils.tracker.dispatch import dispatch_tracking_event, LLM_CALL

    await dispatch_tracking_event(LLM_CALL, {
        "node": "resolve_history",
        "model": "claude-sonnet-4-20250514",
        ...
    })
"""

from __future__ import annotations

from typing import Any

from src.utils.logger import get_logger

logger = get_logger(__name__)


# ── 이벤트 이름 상수 ─────────────────────────────────

# decision.*
DECISION_INTENT = "decision.intent"
DECISION_NORMALIZATION = "decision.normalization"
DECISION_READINESS = "decision.readiness"
DECISION_TABLE_COMPARISON = "decision.table_comparison"

# context.*
CONTEXT_TOOL_SUCCESS = "context.tool_success"
CONTEXT_TOOL_ERROR = "context.tool_error"
CONTEXT_SQL_EXECUTED = "context.sql_executed"
CONTEXT_EMBEDDING = "context.embedding"
CONTEXT_RERANKED = "context.reranked"

# llm.*
LLM_CALL = "llm.call"

# llm.delta.* — 실제 LLM 토큰 스트리밍 (analyzer / visualize)
# 페이로드 공통 필드: turn_id, part_id, part_type ∈ {"analysis","svg"}, node
# - start:  {turn_id, part_id, part_type, node}
# - chunk:  {turn_id, part_id, text, node}
# - reset:  {turn_id, part_id, node, reason}
# - end:    {turn_id, part_id, node, cancelled?, error?, error_code?}
LLM_DELTA_START = "llm.delta.start"
LLM_DELTA_CHUNK = "llm.delta.chunk"
LLM_DELTA_END = "llm.delta.end"
LLM_DELTA_RESET = "llm.delta.reset"

# sql.*
SQL_RECORDED = "sql.recorded"

# reasoning.*
REASONING_STEP = "reasoning.step"


async def dispatch_tracking_event(
    name: str,
    data: dict[str, Any],
) -> None:
    """LangGraph 실행 컨텍스트 내에서만 커스텀 이벤트를 디스패치한다.

    Python 3.12에서 LangGraph가 설정한 ``RunnableConfig`` 를
    contextvars에서 자동 추출하므로 명시적 config 전달이 불필요하다.

    그래프 외부(단위 테스트 등)에서 호출되면 ``RuntimeError`` 를
    잡아서 조용히 무시한다.

    Args:
        name: 이벤트 식별자 (``{도메인}.{행위}`` 형식).
        data: 자유 형식 페이로드 dict.
    """
    try:
        from langchain_core.callbacks.manager import (
            adispatch_custom_event,
        )

        await adispatch_custom_event(name, data)
    except RuntimeError:
        # LangGraph 실행 컨텍스트 밖 — 무시
        pass
    except Exception:
        logger.debug("tracking event dispatch 실패", event_name=name)
