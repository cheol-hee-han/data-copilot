"""llm_stream_with_parse_retry 단위 테스트.

테스트 대상:
    [src/utils/llm/retry.py :: llm_stream_with_parse_retry]
    - 정상 스트림: start → chunk* → end
    - 파싱 실패 → reset → 재시도 성공: start → chunk* → reset → chunk* → end
    - 최종 실패: end{error=True, error_code="PARSE_FAIL"} + ParseError raise
    - 취소: end{cancelled=True} + CancelledError raise
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.utils.llm.client import StreamEvent
from src.utils.llm.retry import ParseError, llm_stream_with_parse_retry


class _FakeAdapterStream:
    def __init__(self, scripted: list[list[StreamEvent]]) -> None:
        # 각 호출마다 scripted[i] 를 소비
        self._scripts = scripted
        self._call = 0

    def __call__(self, **_kw: Any):
        events = self._scripts[self._call]
        self._call += 1

        async def _gen():
            for ev in events:
                yield ev

        return _gen()


def _patched_client(scripted: list[list[StreamEvent]]) -> Any:
    client = MagicMock()
    client.messages = MagicMock()
    client.messages.stream = _FakeAdapterStream(scripted)
    return client


@pytest.mark.asyncio
async def test_stream_success_emits_start_chunks_end() -> None:
    events: list[tuple[str, dict[str, Any]]] = []

    async def capture(name: str, data: dict[str, Any]) -> None:
        events.append((name, data))

    client = _patched_client([[
        StreamEvent("text", "안녕"),
        StreamEvent("text", "하세요"),
    ]])

    with patch(
        "src.utils.llm.retry.get_llm_client",
        return_value=client,
    ), patch(
        "src.utils.llm.retry.dispatch_tracking_event",
        side_effect=capture,
    ):
        text, parsed = await llm_stream_with_parse_retry(
            system="s", messages=[{"role": "user", "content": "q"}],
            parse_fn=lambda t: t.upper(),
            turn_id="t1", part_id="analysis_0", part_type="analysis",
            node_name="t",
        )

    assert text == "안녕하세요"
    assert parsed == "안녕하세요".upper()
    names = [n for n, _ in events]
    assert names[0] == "llm.delta.start"
    assert names[-1] == "llm.delta.end"
    assert names.count("llm.delta.chunk") == 2
    # end 페이로드에 error/cancelled 없음
    end_payload = events[-1][1]
    assert "error" not in end_payload
    assert "cancelled" not in end_payload


@pytest.mark.asyncio
async def test_stream_parse_failure_then_retry_success() -> None:
    events: list[tuple[str, dict[str, Any]]] = []

    async def capture(name: str, data: dict[str, Any]) -> None:
        events.append((name, data))

    calls = {"n": 0}

    def parse_fn(t: str) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("bad")
        return t

    client = _patched_client([
        [StreamEvent("text", "nope")],
        [StreamEvent("text", "ok")],
    ])

    with patch(
        "src.utils.llm.retry.get_llm_client",
        return_value=client,
    ), patch(
        "src.utils.llm.retry.dispatch_tracking_event",
        side_effect=capture,
    ):
        text, parsed = await llm_stream_with_parse_retry(
            system="s", messages=[{"role": "user", "content": "q"}],
            parse_fn=parse_fn,
            turn_id="t1", part_id="analysis_0", part_type="analysis",
            max_retries=1, node_name="t",
        )

    assert parsed == "ok"
    names = [n for n, _ in events]
    assert "llm.delta.reset" in names
    # reset 은 chunk 보다 뒤, end 보다 앞
    assert names.index("llm.delta.reset") < names.index("llm.delta.end")


@pytest.mark.asyncio
async def test_stream_parse_final_failure_raises_and_emits_error_end() -> None:
    events: list[tuple[str, dict[str, Any]]] = []

    async def capture(name: str, data: dict[str, Any]) -> None:
        events.append((name, data))

    def always_fail(_t: str) -> str:
        raise ValueError("nope")

    client = _patched_client([
        [StreamEvent("text", "x")],
        [StreamEvent("text", "x")],
    ])

    with patch(
        "src.utils.llm.retry.get_llm_client",
        return_value=client,
    ), patch(
        "src.utils.llm.retry.dispatch_tracking_event",
        side_effect=capture,
    ):
        with pytest.raises(ParseError):
            await llm_stream_with_parse_retry(
                system="s", messages=[{"role": "user", "content": "q"}],
                parse_fn=always_fail,
                turn_id="t1", part_id="analysis_0", part_type="analysis",
                max_retries=1, node_name="t",
            )

    end_payloads = [d for n, d in events if n == "llm.delta.end"]
    assert end_payloads, "end 이벤트가 방출되어야 함"
    assert end_payloads[-1].get("error") is True
    assert end_payloads[-1].get("error_code") == "PARSE_FAIL"


@pytest.mark.asyncio
async def test_stream_cancelled_emits_cancelled_end() -> None:
    events: list[tuple[str, dict[str, Any]]] = []

    async def capture(name: str, data: dict[str, Any]) -> None:
        events.append((name, data))

    cancelled_after = {"n": 0}

    async def cancel_after_first_chunk() -> bool:
        cancelled_after["n"] += 1
        return cancelled_after["n"] >= 2

    client = _patched_client([[
        StreamEvent("text", "a"),
        StreamEvent("text", "b"),
        StreamEvent("text", "c"),
    ]])

    with patch(
        "src.utils.llm.retry.get_llm_client",
        return_value=client,
    ), patch(
        "src.utils.llm.retry.dispatch_tracking_event",
        side_effect=capture,
    ):
        with pytest.raises(asyncio.CancelledError):
            await llm_stream_with_parse_retry(
                system="s", messages=[{"role": "user", "content": "q"}],
                parse_fn=lambda t: t,
                turn_id="t1", part_id="analysis_0", part_type="analysis",
                node_name="t",
                is_cancelled=cancel_after_first_chunk,
            )

    end_payloads = [d for n, d in events if n == "llm.delta.end"]
    assert end_payloads[-1].get("cancelled") is True
