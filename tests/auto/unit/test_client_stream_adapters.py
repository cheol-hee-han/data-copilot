"""스트리밍 어댑터 단위 테스트 — Anthropic / OpenAI-호환 / IBK.

테스트 대상:
    [src/utils/llm/client.py :: AnthropicMessages.stream,
     OpenAICompatibleMessages.stream, IBKCustomMessages.stream,
     _CircuitBreakerMessages.stream]

실행:
    pytest tests/auto/unit/test_client_stream_adapters.py -v
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.utils.llm.circuit_breaker import CircuitBreaker
from src.utils.llm.client import (
    AnthropicMessages,
    IBKCustomMessages,
    OpenAICompatibleMessages,
    StreamEvent,
    _CircuitBreakerMessages,
)


# ── Anthropic 어댑터 ─────────────────────────────────────────────────────


@dataclass
class _FakeDelta:
    type: str
    text: str = ""
    thinking: str = ""


@dataclass
class _FakeAnthropicEvent:
    type: str
    delta: _FakeDelta | None = None
    message: Any = None
    usage: Any = None


class _FakeAnthropicStream:
    def __init__(self, events: list[_FakeAnthropicEvent]) -> None:
        self._events = events

    async def __aenter__(self) -> "_FakeAnthropicStream":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    def __aiter__(self) -> "_FakeAnthropicStream":
        self._it = iter(self._events)
        return self

    async def __anext__(self) -> _FakeAnthropicEvent:
        try:
            return next(self._it)
        except StopIteration as e:
            raise StopAsyncIteration from e


def _make_anthropic_client(events: list[_FakeAnthropicEvent]) -> Any:
    stream_cm = _FakeAnthropicStream(events)
    client = MagicMock()
    client.messages = MagicMock()
    client.messages.stream = MagicMock(return_value=stream_cm)
    return client


@pytest.mark.asyncio
async def test_anthropic_stream_emits_text_and_thinking() -> None:
    events = [
        _FakeAnthropicEvent(
            "message_start",
            message=MagicMock(usage=MagicMock(input_tokens=12)),
        ),
        _FakeAnthropicEvent(
            "content_block_delta",
            delta=_FakeDelta(type="thinking_delta", thinking="사고"),
        ),
        _FakeAnthropicEvent(
            "content_block_delta",
            delta=_FakeDelta(type="text_delta", text="안녕"),
        ),
        _FakeAnthropicEvent(
            "content_block_delta",
            delta=_FakeDelta(type="text_delta", text="하세요"),
        ),
        _FakeAnthropicEvent(
            "message_delta",
            usage=MagicMock(output_tokens=34),
        ),
    ]
    client = _make_anthropic_client(events)
    adapter = AnthropicMessages(client)

    out: list[StreamEvent] = []
    async for ev in adapter.stream(
        model="claude-sonnet-4-6", max_tokens=100,
        messages=[{"role": "user", "content": "q"}],
    ):
        out.append(ev)

    kinds = [(e.kind, e.text) for e in out]
    assert kinds == [("thinking", "사고"), ("text", "안녕"), ("text", "하세요")]


# ── OpenAI 호환 어댑터 ──────────────────────────────────────────────────


@dataclass
class _FakeOAIDelta:
    content: str | None = None
    reasoning_content: str | None = None


@dataclass
class _FakeOAIChoice:
    delta: _FakeOAIDelta


@dataclass
class _FakeOAIChunk:
    choices: list[_FakeOAIChoice]
    usage: Any = None


class _FakeOAIStream:
    def __init__(self, chunks: list[_FakeOAIChunk]) -> None:
        self._chunks = chunks

    def __aiter__(self) -> "_FakeOAIStream":
        self._it = iter(self._chunks)
        return self

    async def __anext__(self) -> _FakeOAIChunk:
        try:
            return next(self._it)
        except StopIteration as e:
            raise StopAsyncIteration from e


def _make_oai_client(chunks: list[_FakeOAIChunk]) -> Any:
    client = MagicMock()
    client.chat = MagicMock()
    client.chat.completions = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=_FakeOAIStream(chunks),
    )
    return client


@pytest.mark.asyncio
async def test_openai_stream_plain_model_passes_content() -> None:
    chunks = [
        _FakeOAIChunk([_FakeOAIChoice(_FakeOAIDelta(content="안녕"))]),
        _FakeOAIChunk([_FakeOAIChoice(_FakeOAIDelta(content="하세요"))]),
        _FakeOAIChunk(
            choices=[],
            usage=MagicMock(prompt_tokens=10, completion_tokens=5),
        ),
    ]
    adapter = OpenAICompatibleMessages(_make_oai_client(chunks))

    out: list[StreamEvent] = []
    async for ev in adapter.stream(
        model="llama-3.3-70b", max_tokens=100,
        messages=[{"role": "user", "content": "q"}],
    ):
        out.append(ev)

    assert [(e.kind, e.text) for e in out] == [
        ("text", "안녕"),
        ("text", "하세요"),
    ]


@pytest.mark.asyncio
async def test_openai_stream_qwen_filters_think_tags() -> None:
    # Qwen 모델이 <think>...</think> 블록을 응답에 혼합해 내려준다고 가정
    chunks = [
        _FakeOAIChunk([_FakeOAIChoice(_FakeOAIDelta(content="시작<th"))]),
        _FakeOAIChunk([_FakeOAIChoice(_FakeOAIDelta(content="ink>추론</think>"))]),
        _FakeOAIChunk([_FakeOAIChoice(_FakeOAIDelta(content="끝"))]),
    ]
    adapter = OpenAICompatibleMessages(_make_oai_client(chunks))

    out: list[StreamEvent] = []
    async for ev in adapter.stream(
        model="qwen3.5-397b", max_tokens=100,
        messages=[{"role": "user", "content": "q"}],
    ):
        out.append(ev)

    kinds = [(e.kind, e.text) for e in out]
    # 경계 상태머신이 정상 분리하면:
    assert ("text", "시작") in kinds
    assert ("thinking", "추론") in kinds
    assert ("text", "끝") in kinds


@pytest.mark.asyncio
async def test_openai_stream_reasoning_content_mapped_to_thinking() -> None:
    chunks = [
        _FakeOAIChunk([_FakeOAIChoice(
            _FakeOAIDelta(reasoning_content="내부 추론"),
        )]),
        _FakeOAIChunk([_FakeOAIChoice(_FakeOAIDelta(content="답변"))]),
    ]
    adapter = OpenAICompatibleMessages(_make_oai_client(chunks))

    out: list[StreamEvent] = []
    async for ev in adapter.stream(
        model="deepseek-r1", max_tokens=100,
        messages=[{"role": "user", "content": "q"}],
    ):
        out.append(ev)

    kinds = [(e.kind, e.text) for e in out]
    assert ("thinking", "내부 추론") in kinds
    assert ("text", "답변") in kinds


@pytest.mark.asyncio
async def test_openai_stream_passes_include_usage_option() -> None:
    chunks: list[_FakeOAIChunk] = []
    client = _make_oai_client(chunks)
    adapter = OpenAICompatibleMessages(client)

    async for _ in adapter.stream(
        model="llama-3.3-70b", max_tokens=100,
        messages=[{"role": "user", "content": "q"}],
    ):
        pass

    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["stream"] is True
    assert kwargs["stream_options"] == {"include_usage": True}


# ── IBK 차단 ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ibk_stream_yields_single_text_event() -> None:
    """IBKCustomMessages.stream() — create() 결과를 단일 text 이벤트로 yield한다.

    IBK 게이트웨이는 SSE 스트리밍을 지원하지 않는다.
    stream() 은 NotImplementedError 를 raise하지 않고, create() 를 1회 호출한 뒤
    응답 텍스트를 StreamEvent("text", ...) 로 yield한다.
    """
    mock_resp = AsyncMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value={"status": "success", "answer": "응답"})

    http_client = AsyncMock()
    http_client.post = AsyncMock(return_value=mock_resp)

    adapter = IBKCustomMessages(
        http_client=http_client,
        base_url="http://ibk.example",
        token="tk",
        placeholder_name="prompt",
        default_timeout=30.0,
    )

    out: list[StreamEvent] = []
    async for ev in adapter.stream(
        model="solar-pro-2-70b", max_tokens=100,
        messages=[{"role": "user", "content": "q"}],
    ):
        out.append(ev)

    assert len(out) == 1
    assert out[0].kind == "text"
    assert out[0].text == "응답"


# ── CircuitBreaker 래핑 ─────────────────────────────────────────────────


class _InnerAdapter:
    def __init__(self, events: list[StreamEvent], exc: Exception | None = None) -> None:
        self._events = events
        self._exc = exc

    async def stream(self, **_kw: Any):
        if self._exc:
            raise self._exc
        for ev in self._events:
            yield ev


@pytest.mark.asyncio
async def test_cb_stream_successful_iteration_counts_success() -> None:
    cb = CircuitBreaker(fail_threshold=1, reset_timeout_sec=10.0)
    inner = _InnerAdapter([StreamEvent("text", "ok")])
    cb_msg = _CircuitBreakerMessages(inner, cb)

    out = []
    async for ev in cb_msg.stream(
        model="x", max_tokens=1, messages=[],
    ):
        out.append(ev)

    assert [(e.kind, e.text) for e in out] == [("text", "ok")]


@pytest.mark.asyncio
async def test_cb_stream_exception_counts_failure() -> None:
    import asyncio
    cb = CircuitBreaker(fail_threshold=1, reset_timeout_sec=10.0)
    inner = _InnerAdapter([], exc=asyncio.TimeoutError())
    cb_msg = _CircuitBreakerMessages(inner, cb)

    with pytest.raises(asyncio.TimeoutError):
        async for _ in cb_msg.stream(
            model="x", max_tokens=1, messages=[],
        ):
            pass

    from src.utils.llm.circuit_breaker import CircuitState
    assert cb.state is CircuitState.OPEN
