"""AnthropicMessages.create() text 블록 추출 근본 수정 검증.

테스트 대상:
    [src/utils/llm/client.py :: AnthropicMessages.create]
    - thinking 블록이 content[0] 에 있어도 AttributeError 없이 text 추출
    - thinking 블록이 아예 없는 일반 응답도 정상 동작
    - 반환 객체는 LLMResponse (호출부 `.content[0].text` 접근 호환)
    - 복수 text 블록은 연결됨

실행:
    pytest tests/auto/unit/test_anthropic_create_extracts_text_only.py -v
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.utils.llm.client import AnthropicMessages, LLMResponse


@dataclass
class _FakeThinking:
    thinking: str
    type: str = "thinking"


@dataclass
class _FakeText:
    text: str
    type: str = "text"


@dataclass
class _FakeUsage:
    input_tokens: int = 10
    output_tokens: int = 20


def _make_client(content_blocks: list[Any]) -> AsyncMock:
    msg = MagicMock()
    msg.content = content_blocks
    msg.model = "claude-sonnet-4-6"
    msg.stop_reason = "end_turn"
    msg.usage = _FakeUsage()
    client = MagicMock()
    client.messages = MagicMock()
    client.messages.create = AsyncMock(return_value=msg)
    return client


@pytest.mark.asyncio
async def test_thinking_block_first_does_not_crash() -> None:
    """ThinkingBlock 이 content[0] 이어도 text 를 안전 추출한다."""
    client = _make_client([
        _FakeThinking(thinking="내부 추론 ..."),
        _FakeText(text="최종 답변입니다"),
    ])
    adapter = AnthropicMessages(client)

    result = await adapter.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        messages=[{"role": "user", "content": "hi"}],
    )

    assert isinstance(result, LLMResponse)
    assert result.content[0].text == "최종 답변입니다"
    assert result.model == "claude-sonnet-4-6"
    assert result.stop_reason == "end_turn"


@pytest.mark.asyncio
async def test_no_thinking_block_normal_path() -> None:
    """일반 응답 (text 블록만) 도 기존 호환 유지."""
    client = _make_client([_FakeText(text="단순 답변")])
    adapter = AnthropicMessages(client)

    result = await adapter.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        messages=[{"role": "user", "content": "hi"}],
    )

    assert result.content[0].text == "단순 답변"


@pytest.mark.asyncio
async def test_multiple_text_blocks_are_concatenated() -> None:
    """다수 text 블록은 순서대로 연결된다."""
    client = _make_client([
        _FakeText(text="앞부분"),
        _FakeThinking(thinking="중간 추론"),
        _FakeText(text="뒷부분"),
    ])
    adapter = AnthropicMessages(client)

    result = await adapter.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        messages=[{"role": "user", "content": "hi"}],
    )

    assert result.content[0].text == "앞부분뒷부분"


@pytest.mark.asyncio
async def test_empty_content_returns_empty_text() -> None:
    """content 빈 응답은 빈 문자열로 정규화."""
    client = _make_client([])
    adapter = AnthropicMessages(client)

    result = await adapter.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        messages=[{"role": "user", "content": "hi"}],
    )

    assert result.content[0].text == ""
