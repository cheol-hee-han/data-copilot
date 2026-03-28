"""LLM 클라이언트 통합 래퍼 — 프로바이더 무관 통합 인터페이스.

UnifiedLLMClient를 통해 Anthropic Claude API와 OpenAI 호환 API(Groq, OpenRouter 등)를
단일 인터페이스(client.messages.create)로 추상화한다.
Anthropic 호출은 AsyncAnthropic을 그대로 위임하고, OpenAI 호환 호출은
system 파라미터를 system role 메시지로 변환하고 응답을 LLMResponse로 래핑하여
Anthropic 형식(response.content[0].text)과 동일한 접근 패턴을 유지한다.
모든 LLM 호출에 대해 토큰 사용량, 응답 지연, 프롬프트 요약을 자동 추적(tracker)한다.

핵심 함수/클래스:
    - UnifiedLLMClient: 프로바이더 무관 통합 클라이언트 (messages.create 인터페이스)
    - AnthropicMessages: Anthropic AsyncAnthropic.messages 위임 래퍼
    - OpenAICompatibleMessages: OpenAI chat.completions → Anthropic 형식 변환 래퍼
    - LLMResponse / TextBlock: Anthropic Message 호환 응답 데이터 클래스
    - get_llm_client: settings.llm_provider에 따라 싱글턴 클라이언트 생성/반환
    - reset_llm_client: 테스트용 싱글턴 초기화

통합 인터페이스가 필요한 이유: 온라인 환경에서는 Anthropic Claude API를 사용하지만,
폐쇄망 배포 시에는 로컬 소형 모델(7B~70B)을 OpenAI 호환 API로 서빙하게 된다.
settings.llm_provider와 관련 설정만 변경하면 노드 코드 수정 없이 전환 가능하다.
"""

from __future__ import annotations

import re as _re
from dataclasses import dataclass, field
from typing import Any

from src.config import settings
from src.utils.tracker import (
    get_current_node,
    get_current_tracker,
)


# ── Thinking 모드 제어 유틸리티 ──

_THINK_TAG_RE = _re.compile(r"<think>[\s\S]*?</think>\s*", _re.DOTALL)


def _strip_thinking_tags(text: str) -> str:
    """Qwen thinking 태그를 제거한다."""
    return _THINK_TAG_RE.sub("", text).strip()


def _resolve_thinking_params(model: str, mode: str) -> dict[str, Any]:
    """모델과 thinking 모드에 맞는 API 파라미터를 반환한다.

    모델별 처리:
        Gemini  → reasoning_effort 파라미터
        Qwen    → extra_body.chat_template_kwargs.enable_thinking
        기타    → 파라미터 없음 (thinking 미지원)
    """
    if mode == "auto":
        return {}

    model_lower = model.lower()

    if "gemini" in model_lower:
        effort_map = {
            "off": "none", "on": "medium",
            "low": "low", "medium": "medium", "high": "high",
        }
        return {"reasoning_effort": effort_map.get(mode, "medium")}

    if "qwen" in model_lower:
        enabled = mode not in ("off", "none")
        return {
            "extra_body": {
                "chat_template_kwargs": {
                    "enable_thinking": enabled,
                },
            },
        }

    return {}


def _build_prompt_summary(
    system: str | None,
    messages: list[dict[str, str]],
) -> str:
    """프롬프트 요약을 생성한다 (추적용)."""
    parts: list[str] = []
    if system:
        parts.append(f"[S] {system[:150]}")
    for msg in messages[-2:]:
        role = msg.get("role", "?")[0].upper()
        content = msg.get("content", "")
        parts.append(f"[{role}] {content[:150]}")
    return " | ".join(parts)[:500]


# ── Anthropic 응답 형태를 흉내내는 데이터 클래스 ──


@dataclass
class TextBlock:
    """Anthropic ContentBlock 호환 텍스트 블록."""

    text: str
    type: str = "text"


@dataclass
class LLMResponse:
    """Anthropic Message 호환 응답 객체.

    노드에서 response.content[0].text 로 접근하는 패턴을 유지한다.
    """

    content: list[TextBlock] = field(default_factory=list)
    model: str = ""
    stop_reason: str | None = None


# ── Messages 네임스페이스 (client.messages.create 패턴 유지) ──


class AnthropicMessages:
    """Anthropic AsyncAnthropic.messages 를 그대로 위임한다."""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def create(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str | None = None,
        messages: list[dict[str, str]],
        timeout: float | None = None,
        **kwargs: Any,
    ) -> Any:
        """Anthropic messages.create 를 그대로 호출한다."""
        import time as _time

        call_kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
            **kwargs,
        }
        if system is not None:
            call_kwargs["system"] = system
        if timeout is not None:
            call_kwargs["timeout"] = timeout

        _start = _time.perf_counter()
        result = await self._client.messages.create(**call_kwargs)
        _elapsed = (_time.perf_counter() - _start) * 1000

        _tracker = get_current_tracker()
        if _tracker and _tracker.enabled:
            _usage = getattr(result, "usage", None)
            _tracker.track_llm_call(
                node=get_current_node(),
                prompt_summary=_build_prompt_summary(
                    system, messages,
                ),
                response_text=(
                    result.content[0].text[:1000]
                    if result.content else ""
                ),
                model=model,
                prompt_tokens=getattr(
                    _usage, "input_tokens", 0,
                ),
                response_tokens=getattr(
                    _usage, "output_tokens", 0,
                ),
                latency_ms=_elapsed,
            )

        return result


class OpenAICompatibleMessages:
    """OpenAI 호환 API 호출을 Anthropic messages.create 인터페이스로 래핑한다.

    내부에서 Anthropic 형식(system 파라미터, messages)을 OpenAI 형식
    (system role message)으로 변환하고, 응답도 LLMResponse 로 변환한다.
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    async def create(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str | None = None,
        messages: list[dict[str, str]],
        timeout: float | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """OpenAI chat.completions.create 를 Anthropic 인터페이스로 호출한다."""
        import time as _time

        from src.agents.nodes.thinking_modes import (
            get_thinking_mode,
        )

        # Anthropic 의 system 파라미터 → OpenAI 의 system role message 로 변환
        openai_messages: list[dict[str, str]] = []
        if system:
            openai_messages.append({"role": "system", "content": system})
        openai_messages.extend(messages)

        call_kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": openai_messages,
        }
        if timeout is not None:
            call_kwargs["timeout"] = timeout

        # ── Thinking 모드 제어 (모델별 자동 감지) ──
        node_name = get_current_node()
        thinking_mode = get_thinking_mode(node_name)
        thinking_params = _resolve_thinking_params(
            model, thinking_mode,
        )
        call_kwargs.update(thinking_params)

        _start = _time.perf_counter()
        response = await self._client.chat.completions.create(
            **call_kwargs,
        )
        _elapsed = (_time.perf_counter() - _start) * 1000

        # OpenAI 응답 → Anthropic 호환 LLMResponse 로 변환
        text = response.choices[0].message.content or ""

        # Qwen <think> 태그 제거
        if "qwen" in model.lower():
            text = _strip_thinking_tags(text)

        _tracker = get_current_tracker()
        if _tracker and _tracker.enabled:
            _usage = getattr(response, "usage", None)
            _tracker.track_llm_call(
                node=get_current_node(),
                prompt_summary=_build_prompt_summary(
                    system, messages,
                ),
                response_text=text,
                model=model,
                prompt_tokens=getattr(
                    _usage, "prompt_tokens", 0,
                ),
                response_tokens=getattr(
                    _usage, "completion_tokens", 0,
                ),
                latency_ms=_elapsed,
            )

        return LLMResponse(
            content=[TextBlock(text=text)],
            model=response.model or model,
            stop_reason=response.choices[0].finish_reason,
        )


# ── 통합 클라이언트 ──


class UnifiedLLMClient:
    """프로바이더에 관계없이 client.messages.create() 인터페이스를 제공한다.

    노드 코드에서 기존과 동일하게 사용 가능:
        client = get_llm_client()
        response = await client.messages.create(
            model=settings.llm_model,
            max_tokens=2000,
            system="...",
            messages=[{"role": "user", "content": "..."}],
        )
        text = response.content[0].text
    """

    def __init__(self, messages: AnthropicMessages | OpenAICompatibleMessages) -> None:
        self.messages = messages


# ── 싱글턴 팩토리 ──


_client: UnifiedLLMClient | None = None


def get_llm_client() -> UnifiedLLMClient:
    """설정된 프로바이더에 맞는 LLM 클라이언트 싱글턴을 반환한다."""
    global _client
    if _client is not None:
        return _client

    provider = settings.llm_provider.lower()

    if provider == "anthropic":
        from anthropic import AsyncAnthropic

        raw_client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        _client = UnifiedLLMClient(messages=AnthropicMessages(raw_client))

    elif provider == "openai_compatible":
        from openai import AsyncOpenAI

        raw_client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url or None,
            default_headers={
                "HTTP-Referer": settings.openai_referer,
                "X-OpenRouter-Title": settings.openai_title,
            },
        )
        _client = UnifiedLLMClient(messages=OpenAICompatibleMessages(raw_client))

    else:
        msg = (
            f"지원하지 않는 LLM 프로바이더: '{provider}'. "
            "'anthropic' 또는 'openai_compatible' 중 선택하세요."
        )
        raise ValueError(msg)

    return _client


def reset_llm_client() -> None:
    """테스트 등에서 싱글턴을 초기화할 때 사용한다."""
    global _client
    _client = None
