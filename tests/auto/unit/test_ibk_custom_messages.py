"""IBKCustomMessages 어댑터 단위 테스트.

테스트 대상:
    [src/utils/llm/client.py :: IBKCustomMessages]
    - 정상 응답: extra 에 placeholder 단일 키로 조립된 프롬프트 전달
    - 프롬프트 조립: system + messages content 를 \\n\\n 로 이어붙임
    - Qwen 모델 응답에서 <think> 태그 제거
    - status != "success" 시 RuntimeError
    - HTTP 4xx/5xx 응답 시 raise_for_status 로 예외
    - temperature/thinking/max_tokens 는 게이트웨이 스펙상 전달되지 않음
    - timeout override 전달

실행:
    pytest tests/auto/unit/test_ibk_custom_messages.py -v
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.utils.llm.client import IBKCustomMessages


def _make_http_mock(
    *, status_code: int = 200, json_body: dict[str, Any] | None = None,
) -> tuple[AsyncMock, MagicMock]:
    """httpx.AsyncClient 모킹. (http_client, response) 튜플 반환."""
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.json = MagicMock(
        return_value=json_body or {"status": "success", "answer": "ok"},
    )
    if status_code >= 400:
        response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "error", request=MagicMock(), response=response,
            ),
        )
    else:
        response.raise_for_status = MagicMock(return_value=None)

    http = AsyncMock()
    http.post = AsyncMock(return_value=response)
    return http, response


def _make_adapter(http: AsyncMock, placeholder: str = "prompt") -> IBKCustomMessages:
    return IBKCustomMessages(
        http,
        base_url="http://ibk.example/",
        token="TK-TEST",
        placeholder_name=placeholder,
        default_timeout=30.0,
    )


@pytest.mark.asyncio
async def test_success_response_returns_answer_text() -> None:
    """정상 응답에서 answer 텍스트가 LLMResponse.content[0].text 로 반환된다."""
    http, _ = _make_http_mock(
        json_body={"status": "success", "answer": "테스트 응답입니다"},
    )
    adapter = _make_adapter(http)

    result = await adapter.create(
        model="solar-pro-2-70b",
        max_tokens=1000,
        system="시스템 프롬프트",
        messages=[{"role": "user", "content": "질문"}],
    )

    assert result.content[0].text == "테스트 응답입니다"
    assert result.model == "solar-pro-2-70b"


@pytest.mark.asyncio
async def test_prompt_assembly_combines_system_and_messages() -> None:
    """system + messages.content 가 \\n\\n 로 조립되어 placeholder 에 담긴다."""
    http, _ = _make_http_mock()
    adapter = _make_adapter(http, placeholder="prompt")

    await adapter.create(
        model="qwen3.5-397b",
        max_tokens=1000,
        system="SYS",
        messages=[
            {"role": "user", "content": "USR1"},
            {"role": "user", "content": "USR2"},
        ],
    )

    _, kwargs = http.post.call_args
    body = kwargs["json"]
    assert body["token"] == "TK-TEST"
    assert set(body["extra"].keys()) == {"prompt"}
    assert body["extra"]["prompt"] == "SYS\n\nUSR1\n\nUSR2"


@pytest.mark.asyncio
async def test_url_uses_base_url_and_thread_id_path() -> None:
    """URL 은 {base_url}/gpt/api/{thread_id} 형식이며 trailing slash 제거된다."""
    http, _ = _make_http_mock()
    adapter = _make_adapter(http)

    await adapter.create(
        model="solar-pro-2-70b",
        max_tokens=1000,
        messages=[{"role": "user", "content": "hi"}],
    )

    url = http.post.call_args[0][0]
    assert url.startswith("http://ibk.example/gpt/api/")
    # trailing slash 제거 검증: http://ibk.example//gpt/... 아니어야 함
    assert "//gpt/" not in url.replace("http://", "")


@pytest.mark.asyncio
async def test_qwen_thinking_tags_stripped_from_answer() -> None:
    """Qwen 모델 응답의 <think>...</think> 블록이 제거된다."""
    http, _ = _make_http_mock(
        json_body={
            "status": "success",
            "answer": "<think>내부 추론</think>\n최종 답변",
        },
    )
    adapter = _make_adapter(http)

    result = await adapter.create(
        model="qwen3.5-397b",
        max_tokens=1000,
        messages=[{"role": "user", "content": "q"}],
    )

    assert result.content[0].text == "최종 답변"


@pytest.mark.asyncio
async def test_non_qwen_keeps_think_tags_as_is() -> None:
    """Qwen 이 아닌 모델은 <think> 제거 로직이 적용되지 않는다."""
    http, _ = _make_http_mock(
        json_body={
            "status": "success",
            "answer": "<think>x</think>결과",
        },
    )
    adapter = _make_adapter(http)

    result = await adapter.create(
        model="solar-pro-2-70b",
        max_tokens=1000,
        messages=[{"role": "user", "content": "q"}],
    )

    assert "<think>" in result.content[0].text


@pytest.mark.asyncio
async def test_error_status_raises_runtime_error() -> None:
    """status != 'success' 응답은 RuntimeError 로 전파된다."""
    http, _ = _make_http_mock(
        json_body={"status": "error", "answer": "인증 실패"},
    )
    adapter = _make_adapter(http)

    with pytest.raises(RuntimeError, match="IBK LLM 호출 실패"):
        await adapter.create(
            model="solar-pro-2-70b",
            max_tokens=1000,
            messages=[{"role": "user", "content": "q"}],
        )


@pytest.mark.asyncio
async def test_http_5xx_raises_via_raise_for_status() -> None:
    """HTTP 5xx 응답은 raise_for_status 로 HTTPStatusError 를 던진다."""
    http, _ = _make_http_mock(
        status_code=500,
        json_body={"status": "error", "answer": ""},
    )
    adapter = _make_adapter(http)

    with pytest.raises(httpx.HTTPStatusError):
        await adapter.create(
            model="solar-pro-2-70b",
            max_tokens=1000,
            messages=[{"role": "user", "content": "q"}],
        )


@pytest.mark.asyncio
async def test_timeout_override_passed_to_http() -> None:
    """명시 timeout 이 httpx.post 에 그대로 전달된다."""
    http, _ = _make_http_mock()
    adapter = _make_adapter(http)

    await adapter.create(
        model="solar-pro-2-70b",
        max_tokens=1000,
        messages=[{"role": "user", "content": "q"}],
        timeout=5.0,
    )

    assert http.post.call_args.kwargs["timeout"] == 5.0


@pytest.mark.asyncio
async def test_unsupported_params_silently_ignored() -> None:
    """temperature/thinking 은 인터페이스 호환용이며 body 에 포함되지 않는다."""
    http, _ = _make_http_mock()
    adapter = _make_adapter(http)

    await adapter.create(
        model="solar-pro-2-70b",
        max_tokens=2000,
        messages=[{"role": "user", "content": "q"}],
        temperature=0.0,
        thinking="high",
    )

    body = http.post.call_args.kwargs["json"]
    assert body.keys() == {"token", "extra"}
    assert "temperature" not in body["extra"]
    assert "thinking" not in body["extra"]
