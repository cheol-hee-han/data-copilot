"""DataCopilotCallbackHandler.on_custom_event 의 llm.delta.* 라우팅 테스트.

테스트 대상:
    [src/utils/tracker/callback_handler.py :: on_custom_event / _emit_llm_delta]
    - llm.delta.start → WS {type:"llm_delta", event:"start", part_type}
    - llm.delta.chunk → WS {type:"llm_delta", event:"delta", text} (chunk→delta 정규화)
    - llm.delta.reset → WS {type:"llm_delta", event:"reset", reason}
    - llm.delta.end   → WS {type:"llm_delta", event:"end", cancelled?/error?/error_code?}
    - on_event 미설정 시 조용히 pass

실행:
    pytest tests/auto/unit/test_callback_handler_llm_delta.py -v
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest

from src.utils.tracker.callback_handler import DataCopilotCallbackHandler


def _make_handler(on_event: Any) -> DataCopilotCallbackHandler:
    return DataCopilotCallbackHandler(
        enabled=True, on_event=on_event,
    )


@pytest.mark.asyncio
async def test_llm_delta_start_routes_with_part_type() -> None:
    on_event = AsyncMock()
    h = _make_handler(on_event)
    await h.on_custom_event(
        name="llm.delta.start",
        data={
            "turn_id": "t_001",
            "part_id": "analysis_0",
            "part_type": "analysis",
            "node": "analyzer",
        },
        run_id=uuid4(),
    )
    on_event.assert_awaited_once()
    payload = on_event.await_args.args[0]
    assert payload == {
        "type": "llm_delta",
        "turn_id": "t_001",
        "part_id": "analysis_0",
        "event": "start",
        "part_type": "analysis",
    }


@pytest.mark.asyncio
async def test_llm_delta_chunk_normalized_to_delta_event_with_text() -> None:
    on_event = AsyncMock()
    h = _make_handler(on_event)
    await h.on_custom_event(
        name="llm.delta.chunk",
        data={
            "turn_id": "t_001",
            "part_id": "analysis_0",
            "text": "안녕",
            "node": "analyzer",
        },
        run_id=uuid4(),
    )
    payload = on_event.await_args.args[0]
    assert payload["event"] == "delta"
    assert payload["text"] == "안녕"
    assert payload["part_id"] == "analysis_0"


@pytest.mark.asyncio
async def test_llm_delta_reset_carries_reason() -> None:
    on_event = AsyncMock()
    h = _make_handler(on_event)
    await h.on_custom_event(
        name="llm.delta.reset",
        data={
            "turn_id": "t_001",
            "part_id": "analysis_0",
            "reason": "parse_error",
        },
        run_id=uuid4(),
    )
    payload = on_event.await_args.args[0]
    assert payload["event"] == "reset"
    assert payload["reason"] == "parse_error"


@pytest.mark.asyncio
async def test_llm_delta_end_with_error_fields() -> None:
    on_event = AsyncMock()
    h = _make_handler(on_event)
    await h.on_custom_event(
        name="llm.delta.end",
        data={
            "turn_id": "t_001",
            "part_id": "analysis_0",
            "error": True,
            "error_code": "PARSE_FAIL",
        },
        run_id=uuid4(),
    )
    payload = on_event.await_args.args[0]
    assert payload["event"] == "end"
    assert payload["error"] is True
    assert payload["error_code"] == "PARSE_FAIL"


@pytest.mark.asyncio
async def test_llm_delta_end_cancelled_true() -> None:
    on_event = AsyncMock()
    h = _make_handler(on_event)
    await h.on_custom_event(
        name="llm.delta.end",
        data={
            "turn_id": "t_001",
            "part_id": "analysis_0",
            "cancelled": True,
        },
        run_id=uuid4(),
    )
    payload = on_event.await_args.args[0]
    assert payload["event"] == "end"
    assert payload["cancelled"] is True


@pytest.mark.asyncio
async def test_on_event_none_does_not_raise() -> None:
    h = _make_handler(on_event=None)
    # 예외 없이 통과해야 함
    await h.on_custom_event(
        name="llm.delta.chunk",
        data={"turn_id": "t_001", "part_id": "analysis_0", "text": "x"},
        run_id=uuid4(),
    )
