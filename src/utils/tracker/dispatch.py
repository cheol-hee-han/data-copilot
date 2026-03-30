"""LangGraph 커스텀 이벤트 디스패치 유틸리티.

LangGraph 실행 컨텍스트 내에서만 이벤트를 전달하며,
그래프 외부(단위 테스트 등)에서 호출되면 조용히 무시한다.

사용법::

    from src.utils.tracker.dispatch import dispatch_tracking_event

    # 커넥터/유틸리티에서 — config 자동 추출 (Python 3.12)
    await dispatch_tracking_event("llm.call", {
        "node": "resolve_history",
        "model": "claude-sonnet-4-20250514",
        ...
    })
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


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
LLM_PROMPT_VARIABLES = "llm.prompt_variables"

# sql.*
SQL_RECORDED = "sql.recorded"


async def record_prompt_variables(
    variables: dict[str, str],
) -> None:
    """직전 LLM 호출 기록에 프롬프트 치환 변수를 보강한다.

    ``llm_call_with_parse_retry`` 호출 직후에 사용하면
    핸들러가 ``llm.prompt_variables`` 이벤트를 수신하여
    마지막 ``LLMCallRecord.prompt_variables`` 에 병합한다.
    """
    await dispatch_tracking_event(
        LLM_PROMPT_VARIABLES, {"variables": variables},
    )


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
        logger.debug(
            "tracking event dispatch 실패",
            extra={"event": name},
        )
