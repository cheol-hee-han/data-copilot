"""LLM 클라이언트 통합 래퍼 — 프로바이더 무관 통합 인터페이스.

작성자: 한철희 / 최종수정: 2026-04-15

UnifiedLLMClient 를 통해 Anthropic / OpenAI 호환 / IBK Custom Gateway 세 프로바이더를
단일 인터페이스(client.messages.create)로 추상화한다.
Anthropic 호출은 AsyncAnthropic 을 그대로 위임하고, OpenAI 호환·IBK 호출은
응답을 LLMResponse 로 래핑하여 Anthropic 형식(response.content[0].text) 과 동일한
접근 패턴을 유지한다.
모든 LLM 호출에 대해 토큰 사용량, 응답 지연, 프롬프트 요약을 자동 추적(tracker)한다.

핵심 함수/클래스:
    - UnifiedLLMClient: 프로바이더 무관 통합 클라이언트 (messages.create 인터페이스)
    - AnthropicMessages: Anthropic AsyncAnthropic.messages 위임 래퍼
    - OpenAICompatibleMessages: OpenAI chat.completions → Anthropic 형식 변환 래퍼
    - IBKCustomMessages: IBK 내부 LLM 게이트웨이 래퍼 (system+messages 조립 후
      extra[placeholder] 단일 키로 전달하는 passthrough 방식)
    - LLMResponse / TextBlock: Anthropic Message 호환 응답 데이터 클래스
    - get_llm_client: settings.llm_provider 에 따라 싱글턴 클라이언트 생성/반환
    - reset_llm_client: 테스트용 싱글턴 초기화

공통 파라미터 (3개 어댑터 동일):
    model, max_tokens, system, messages, timeout, temperature, thinking
    - temperature: 모든 어댑터에 네이티브 전달 (None 이면 생략, 서버 디폴트)
    - thinking: "off"/"auto"/"low"/"medium"/"high" | None
        명시 전달 시 그 값 사용, None 이면 node_name 기반 NODE_THINKING_MODES lookup.

통합 인터페이스가 필요한 이유: 온라인 환경에서는 Anthropic Claude API 를 사용하지만,
폐쇄망 배포 시에는 로컬 중대형 모델(Solar Pro 2 70B, Qwen3.5 397B 등) 을 OpenAI
호환 API 또는 IBK Custom Gateway 로 서빙하게 된다. settings.llm_provider 와 관련
설정만 변경하면 노드 코드 수정 없이 전환 가능하다.
"""

from __future__ import annotations

import re as _re
import uuid as _uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal

from src.config import settings
from src.utils.llm.circuit_breaker import CircuitBreaker
from src.utils.tracker.context import get_current_node
from src.utils.truncate import truncate_trace
from src.utils.tracker.dispatch import (
    dispatch_tracking_event,
    LLM_CALL,
)


# ── Thinking 모드 제어 유틸리티 ──

_THINK_TAG_RE = _re.compile(r"<think>[\s\S]*?</think>\s*", _re.DOTALL)


def _strip_thinking_tags(text: str) -> str:
    """Qwen thinking 태그를 제거한다."""
    return _THINK_TAG_RE.sub("", text).strip()


def _resolve_thinking_mode(explicit: str | None) -> str:
    """명시값 > 노드 lookup 순으로 thinking 모드를 확정한다."""
    if explicit is not None:
        return explicit
    from src.agents.nodes.thinking_modes import get_thinking_mode
    return get_thinking_mode(get_current_node())


def _is_openai_reasoning_model(model: str) -> bool:
    """OpenAI o-series reasoning 모델인지 판별한다.

    o-series는 max_tokens 대신 max_completion_tokens를 사용하고,
    temperature를 지원하지 않으며, reasoning_effort를 지원한다.
    """
    m = model.lower()
    return m.startswith(("o1", "o3", "o4"))


# o-series max_completion_tokens 고정 상한.
# reasoning 토큰(내부 추론)이 이 예산에서 차감되므로, 호출부 max_tokens 기준으로
# 잡으면 추론에 예산을 소진해 빈 응답이 발생한다.
# 넉넉한 고정값을 사용하되, 과금은 실제 사용 토큰 기준이므로 비용 영향 없음.
_REASONING_MAX_COMPLETION: int = 32_768


def _resolve_openai_thinking_params(model: str, mode: str) -> dict[str, Any]:
    """OpenAI 호환 프로바이더용 thinking 파라미터로 변환한다.

    모델별 처리:
        Qwen             → extra_body.chat_template_kwargs.enable_thinking
        o-series/Gemini  → reasoning_effort 파라미터
        일반 GPT 등      → 파라미터 없음 (thinking 미지원)
    """
    model_lower = model.lower()

    if "qwen" in model_lower:
        enabled = mode not in ("off", "none")
        return {
            "extra_body": {
                "chat_template_kwargs": {
                    "enable_thinking": enabled,
                },
            },
        }

    # reasoning_effort: o-series, Gemini만 지원
    if _is_openai_reasoning_model(model) or "gemini" in model_lower:
        effort_map: dict[str, str] = {
            "off": "none",
            "low": "low", "medium": "medium", "high": "high",
        }
        return {"reasoning_effort": effort_map.get(mode, "none")}

    # 일반 모델 (GPT-4.1, Llama 등): thinking 파라미터 미지원
    return {}


def _resolve_anthropic_thinking_params(mode: str) -> dict[str, Any]:
    """Anthropic extended thinking 파라미터로 변환한다.

    Claude 3.7 Sonnet 이상에서만 유효하며, 지원 모델에서 명시 전달 시에만 활성화한다.
    "off"/빈값은 파라미터를 생략(모델 기본 동작 유지)한다.
    """
    if mode in ("off", "none", ""):
        return {}
    budget_map = {"low": 1024, "medium": 4096, "high": 8192}
    budget = budget_map.get(mode)
    if budget is None:
        return {}
    return {"thinking": {"type": "enabled", "budget_tokens": budget}}


# ── 스트리밍 이벤트 / Qwen 경계 필터 ──


@dataclass
class StreamEvent:
    """스트리밍 이벤트 — 어댑터가 방출, retry/analyzer/visualizer 가 소비.

    Attributes:
        kind: "text" (사용자 표출) / "thinking" (내부 트레이스만).
        text: 이 청크의 텍스트 조각.
    """

    kind: Literal["text", "thinking"]
    text: str


_QWEN_OPEN = "<think>"
_QWEN_CLOSE = "</think>"


def _parse_content_block_delta(event: Any) -> list[StreamEvent]:
    """content_block_delta 이벤트를 StreamEvent 로 변환."""
    delta = getattr(event, "delta", None)
    dtype = getattr(delta, "type", None)
    if dtype == "text_delta":
        txt = getattr(delta, "text", "") or ""
        return [StreamEvent("text", txt)] if txt else []
    if dtype == "thinking_delta":
        tk = getattr(delta, "thinking", "") or ""
        return [StreamEvent("thinking", tk)] if tk else []
    return []


def _update_usage_from_event(
    event: Any, etype: str, usage_state: dict[str, int],
) -> None:
    """message_start / message_delta 이벤트에서 usage 를 usage_state 로 누적."""
    if etype == "message_start":
        usage = getattr(getattr(event, "message", None), "usage", None)
        if usage is not None:
            usage_state["input"] = getattr(usage, "input_tokens", 0) or 0
    elif etype == "message_delta":
        usage = getattr(event, "usage", None)
        if usage is not None:
            usage_state["output"] = (
                getattr(usage, "output_tokens", usage_state["output"])
                or usage_state["output"]
            )


def _parse_anthropic_event(
    event: Any, usage_state: dict[str, int],
) -> list[StreamEvent]:
    """Anthropic 스트림 이벤트를 StreamEvent 리스트로 변환 + usage 누적."""
    etype = getattr(event, "type", None)
    if etype == "content_block_delta":
        return _parse_content_block_delta(event)
    _update_usage_from_event(event, etype, usage_state)
    return []


def _build_openai_call_kwargs(
    *,
    model: str,
    max_tokens: int,
    system: str | None,
    messages: list[dict[str, str]],
    timeout: float | None,
    temperature: float | None,
    thinking: str | None,
    stream: bool,
) -> dict[str, Any]:
    """OpenAI chat.completions.create 에 넘길 call_kwargs 를 조립한다.

    - system 파라미터 → OpenAI system role message 로 변환.
    - stream=True 이면 stream_options={"include_usage": True} 부여 (usage 누락 방지).
    - thinking 모드 → 모델별 파라미터로 변환.
    """
    openai_messages: list[dict[str, str]] = []
    if system:
        openai_messages.append({"role": "system", "content": system})
    openai_messages.extend(messages)

    is_reasoning = _is_openai_reasoning_model(model)
    thinking_mode = _resolve_thinking_mode(thinking)

    call_kwargs: dict[str, Any] = {
        "model": model,
        "messages": openai_messages,
    }
    # o-series: max_tokens 미지원 → max_completion_tokens 사용.
    # reasoning 토큰이 예산에 포함되므로 고정 상한을 사용한다.
    if is_reasoning:
        call_kwargs["max_completion_tokens"] = max(
            max_tokens, _REASONING_MAX_COMPLETION,
        )
    else:
        call_kwargs["max_tokens"] = max_tokens

    if stream:
        call_kwargs["stream"] = True
        call_kwargs["stream_options"] = {"include_usage": True}
    if timeout is not None:
        call_kwargs["timeout"] = timeout
    # o-series: temperature 미지원 (고정값)
    if temperature is not None and not is_reasoning:
        call_kwargs["temperature"] = temperature

    call_kwargs.update(
        _resolve_openai_thinking_params(model, thinking_mode),
    )
    return call_kwargs


def _absorb_openai_usage(chunk: Any, usage_state: dict[str, int]) -> None:
    """OpenAI chunk 의 usage 필드를 usage_state 로 누적 (include_usage=True 필수)."""
    usage = getattr(chunk, "usage", None)
    if usage is None:
        return
    usage_state["prompt"] = (
        getattr(usage, "prompt_tokens", usage_state["prompt"])
        or usage_state["prompt"]
    )
    usage_state["completion"] = (
        getattr(usage, "completion_tokens", usage_state["completion"])
        or usage_state["completion"]
    )


def _parse_openai_chunk(
    chunk: Any,
    qwen_filter: QwenThinkFilter | None,
) -> list[StreamEvent]:
    """OpenAI chat.completions stream chunk 를 StreamEvent 리스트로 변환.

    - `delta.content` → text. Qwen 모델은 QwenThinkFilter 로 text/thinking 분리.
    - `delta.reasoning_content` → thinking (DeepSeek-R1 등 reasoning 모델).
    """
    choices = getattr(chunk, "choices", None) or []
    if not choices:
        return []
    delta = getattr(choices[0], "delta", None)
    if delta is None:
        return []

    out: list[StreamEvent] = []
    reasoning = getattr(delta, "reasoning_content", None)
    if reasoning:
        out.append(StreamEvent("thinking", reasoning))

    content = getattr(delta, "content", None)
    if content:
        if qwen_filter is not None:
            out.extend(qwen_filter.feed(content))
        else:
            out.append(StreamEvent("text", content))
    return out


def _tag_prefix_len(buf: str, tag: str) -> int:
    """buf 의 접미사 중 tag 의 접두사와 일치하는 최대 길이.

    예: buf="...abc<th", tag="<think>" → 3 (`<th`).
    토큰 경계에서 태그가 쪼개질 때 hold 할 길이를 계산한다.
    """
    max_n = min(len(buf), len(tag) - 1)
    for n in range(max_n, 0, -1):
        if tag.startswith(buf[-n:]):
            return n
    return 0


class QwenThinkFilter:
    """Qwen `<think>...</think>` 태그를 경계 안전하게 분리하는 상태머신.

    토큰 경계에서 태그가 `<`, `<t`, `<th`, ... 처럼 쪼개질 수 있어 단순
    `str.replace` 는 불가. 버퍼에 부분 일치 접미사가 남으면 다음 청크까지 hold.
    """

    def __init__(self) -> None:
        self._buf = ""
        self._in_think = False

    def feed(self, chunk: str) -> list[StreamEvent]:
        """chunk 하나를 소비하여 0개 이상 StreamEvent 를 생성한다."""
        self._buf += chunk
        out: list[StreamEvent] = []
        while self._buf:
            tag = _QWEN_CLOSE if self._in_think else _QWEN_OPEN
            idx = self._buf.find(tag)
            if idx >= 0:
                self._consume_until_tag(idx, len(tag), out)
            else:
                self._flush_except_prefix(tag, out)
                break
        return out

    def _current_kind(self) -> Literal["text", "thinking"]:
        return "thinking" if self._in_think else "text"

    def _consume_until_tag(
        self, tag_pos: int, tag_len: int, out: list[StreamEvent],
    ) -> None:
        """tag 완전 매치 지점까지의 head 를 방출하고, 태그 자체는 버린 뒤 모드 전환."""
        head = self._buf[:tag_pos]
        if head:
            out.append(StreamEvent(self._current_kind(), head))
        self._buf = self._buf[tag_pos + tag_len:]
        self._in_think = not self._in_think

    def _flush_except_prefix(
        self, tag: str, out: list[StreamEvent],
    ) -> None:
        """태그 미발견: 부분 일치 접미사만 hold, 나머지는 방출."""
        hold = _tag_prefix_len(self._buf, tag)
        emit = self._buf[:-hold] if hold else self._buf
        if emit:
            out.append(StreamEvent(self._current_kind(), emit))
        self._buf = self._buf[-hold:] if hold else ""

    def flush(self) -> list[StreamEvent]:
        """스트림 종료 시 잔여 버퍼를 방출한다 (미닫힌 태그 방어)."""
        if not self._buf:
            return []
        kind: Literal["text", "thinking"] = (
            "thinking" if self._in_think else "text"
        )
        ev = [StreamEvent(kind, self._buf)]
        self._buf = ""
        return ev


def _extract_text(content_blocks: list[Any] | None) -> str:
    """Anthropic content 블록 리스트에서 text 타입 블록만 안전 추출한다.

    extended thinking 활성 시 content[0] 이 ThinkingBlock 일 수 있어
    `content[0].text` 는 AttributeError 를 유발한다. 본 함수는 type=="text"
    블록만 필터링하여 연결하므로 thinking 블록 존재 여부와 무관하게 안전하다.
    """
    if not content_blocks:
        return ""
    return "".join(
        b.text for b in content_blocks
        if getattr(b, "type", None) == "text"
    )


def _build_prompt_summary(
    system: str | None,
    messages: list[dict[str, str]],
) -> str:
    """프롬프트 요약을 생성한다 (추적용)."""
    parts: list[str] = []
    if system:
        parts.append(f"[S] {truncate_trace(system)}")
    for msg in messages[-2:]:
        role = msg.get("role", "?")[0].upper()
        content = msg.get("content", "")
        parts.append(f"[{role}] {truncate_trace(content)}")
    return truncate_trace(" | ".join(parts))


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

    async def close(self) -> None:
        """AsyncAnthropic 내부 httpx 연결을 종료한다 (graceful shutdown 용)."""
        closer = getattr(self._client, "close", None)
        if closer is None:
            return
        result = closer()
        if hasattr(result, "__await__"):
            await result

    async def create(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str | None = None,
        messages: list[dict[str, str]],
        timeout: float | None = None,
        temperature: float | None = None,
        thinking: str | None = None,
    ) -> Any:
        """Anthropic messages.create 를 그대로 호출한다.

        thinking 은 명시 전달 시에만 Anthropic extended thinking 파라미터로 매핑된다
        (Claude 3.7 이상 지원). None 이면 파라미터 생략(모델 기본 동작).
        """
        import time as _time

        call_kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system is not None:
            call_kwargs["system"] = system
        if timeout is not None:
            call_kwargs["timeout"] = timeout
        if temperature is not None:
            call_kwargs["temperature"] = temperature
        if thinking is not None:
            call_kwargs.update(_resolve_anthropic_thinking_params(thinking))

        _start = _time.perf_counter()
        result = await self._client.messages.create(**call_kwargs)
        _elapsed = (_time.perf_counter() - _start) * 1000

        # extended thinking 활성 시 content[0] 이 ThinkingBlock 일 수 있어
        # 호출부의 `response.content[0].text` 접근이 깨짐. text 블록만
        # 연결하여 LLMResponse 로 정규화한다 (thinking 블록 유무 무관 안전).
        text = _extract_text(getattr(result, "content", None))

        _usage = getattr(result, "usage", None)
        await dispatch_tracking_event(LLM_CALL, {
            "node": get_current_node(),
            "prompt_summary": _build_prompt_summary(
                system, messages,
            ),
            "response_text": truncate_trace(text),
            "model": model,
            "prompt_tokens": getattr(
                _usage, "input_tokens", 0,
            ),
            "response_tokens": getattr(
                _usage, "output_tokens", 0,
            ),
            "latency_ms": _elapsed,
        })

        return LLMResponse(
            content=[TextBlock(text=text)],
            model=getattr(result, "model", model) or model,
            stop_reason=getattr(result, "stop_reason", None),
        )

    async def stream(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str | None = None,
        messages: list[dict[str, str]],
        timeout: float | None = None,
        temperature: float | None = None,
        thinking: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Anthropic messages.stream() 을 StreamEvent 로 정규화한다.

        `content_block_delta.text_delta` → kind="text",
        `content_block_delta.thinking_delta` → kind="thinking".
        종료 시 누적 텍스트·토큰·레이턴시로 LLM_CALL 이벤트 1회 방출한다.
        """
        import time as _time

        call_kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system is not None:
            call_kwargs["system"] = system
        if timeout is not None:
            call_kwargs["timeout"] = timeout
        if temperature is not None:
            call_kwargs["temperature"] = temperature
        if thinking is not None:
            call_kwargs.update(_resolve_anthropic_thinking_params(thinking))

        _start = _time.perf_counter()
        accum_text: list[str] = []
        usage_state = {"input": 0, "output": 0}

        try:
            async with self._client.messages.stream(**call_kwargs) as stream:
                async for event in stream:
                    for ev in _parse_anthropic_event(event, usage_state):
                        if ev.kind == "text":
                            accum_text.append(ev.text)
                        yield ev
        finally:
            _elapsed = (_time.perf_counter() - _start) * 1000
            await dispatch_tracking_event(LLM_CALL, {
                "node": get_current_node(),
                "prompt_summary": _build_prompt_summary(system, messages),
                "response_text": truncate_trace("".join(accum_text)),
                "model": model,
                "prompt_tokens": usage_state["input"],
                "response_tokens": usage_state["output"],
                "latency_ms": _elapsed,
            })


class OpenAICompatibleMessages:
    """OpenAI 호환 API 호출을 Anthropic messages.create 인터페이스로 래핑한다.

    내부에서 Anthropic 형식(system 파라미터, messages)을 OpenAI 형식
    (system role message)으로 변환하고, 응답도 LLMResponse 로 변환한다.
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    async def close(self) -> None:
        """AsyncOpenAI 내부 httpx 연결을 종료한다 (graceful shutdown 용)."""
        closer = getattr(self._client, "close", None)
        if closer is None:
            return
        result = closer()
        if hasattr(result, "__await__"):
            await result

    async def create(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str | None = None,
        messages: list[dict[str, str]],
        timeout: float | None = None,
        temperature: float | None = None,
        thinking: str | None = None,
    ) -> LLMResponse:
        """OpenAI chat.completions.create 를 Anthropic 인터페이스로 호출한다.

        thinking 은 명시 전달 > node_name 기반 NODE_THINKING_MODES lookup 순으로
        확정되며, 모델별(Qwen/Gemini) 네이티브 파라미터로 변환된다.
        """
        import time as _time

        # Anthropic 의 system 파라미터 → OpenAI 의 system role message 로 변환
        openai_messages: list[dict[str, str]] = []
        if system:
            openai_messages.append({"role": "system", "content": system})
        openai_messages.extend(messages)

        is_reasoning = _is_openai_reasoning_model(model)
        thinking_mode = _resolve_thinking_mode(thinking)

        call_kwargs: dict[str, Any] = {
            "model": model,
            "messages": openai_messages,
        }
        # o-series: max_tokens 미지원 → max_completion_tokens 사용.
        # reasoning 토큰이 예산에 포함되므로 고정 상한을 사용한다.
        if is_reasoning:
            call_kwargs["max_completion_tokens"] = max(
                max_tokens, _REASONING_MAX_COMPLETION,
            )
        else:
            call_kwargs["max_tokens"] = max_tokens

        if timeout is not None:
            call_kwargs["timeout"] = timeout
        # o-series: temperature 미지원 (고정값)
        if temperature is not None and not is_reasoning:
            call_kwargs["temperature"] = temperature

        # ── Thinking 모드 제어 (명시 > 노드 lookup) ──
        call_kwargs.update(
            _resolve_openai_thinking_params(model, thinking_mode),
        )

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

        _usage = getattr(response, "usage", None)
        await dispatch_tracking_event(LLM_CALL, {
            "node": get_current_node(),
            "prompt_summary": _build_prompt_summary(
                system, messages,
            ),
            "response_text": text,
            "model": model,
            "prompt_tokens": getattr(
                _usage, "prompt_tokens", 0,
            ),
            "response_tokens": getattr(
                _usage, "completion_tokens", 0,
            ),
            "latency_ms": _elapsed,
        })

        return LLMResponse(
            content=[TextBlock(text=text)],
            model=response.model or model,
            stop_reason=response.choices[0].finish_reason,
        )

    async def stream(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str | None = None,
        messages: list[dict[str, str]],
        timeout: float | None = None,
        temperature: float | None = None,
        thinking: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """OpenAI chat.completions.create(stream=True) 를 StreamEvent 로 정규화한다.

        Qwen 모델은 `<think>...</think>` 태그가 응답 본문에 섞여 오므로 경계
        상태머신(QwenThinkFilter) 으로 text/thinking 을 분리한다.
        reasoning 모델의 `delta.reasoning_content` 는 thinking 으로 매핑한다.
        """
        import time as _time

        call_kwargs = _build_openai_call_kwargs(
            model=model, max_tokens=max_tokens, system=system,
            messages=messages, timeout=timeout, temperature=temperature,
            thinking=thinking, stream=True,
        )
        qwen_filter = (
            QwenThinkFilter() if "qwen" in model.lower() else None
        )

        _start = _time.perf_counter()
        accum_text: list[str] = []
        usage_state = {"prompt": 0, "completion": 0}

        try:
            stream = await self._client.chat.completions.create(**call_kwargs)
            async for chunk in stream:
                for ev in _parse_openai_chunk(chunk, qwen_filter):
                    if ev.kind == "text":
                        accum_text.append(ev.text)
                    yield ev
                _absorb_openai_usage(chunk, usage_state)

            if qwen_filter is not None:
                for ev in qwen_filter.flush():
                    if ev.kind == "text":
                        accum_text.append(ev.text)
                    yield ev
        finally:
            _elapsed = (_time.perf_counter() - _start) * 1000
            await dispatch_tracking_event(LLM_CALL, {
                "node": get_current_node(),
                "prompt_summary": _build_prompt_summary(system, messages),
                "response_text": truncate_trace("".join(accum_text)),
                "model": model,
                "prompt_tokens": usage_state["prompt"],
                "response_tokens": usage_state["completion"],
                "latency_ms": _elapsed,
            })


class IBKCustomMessages:
    """IBK 내부 LLM 게이트웨이 호출을 Anthropic messages.create 인터페이스로 래핑한다.

    게이트웨이 규격 (docs/project-requirements.txt 참고 5):
        URL : POST {base_url}/gpt/api/{thread_id}
        Body: {"token": <단일토큰>, "extra": {<placeholder>: <조립된 프롬프트>}}
        Resp: {"question","answer","status","threadId","updatedAt"}

    설계 요점:
        - 시스템 프롬프트 템플릿은 관리툴에 단일 placeholder (`{{prompt}}` 등) 로 등록한다.
        - 코드가 만든 system + messages 를 하나의 문자열로 조립해 extra[placeholder_name]
          에 그대로 싣어 전달한다 (passthrough). 노드 코드 수정 없이 동작한다.
        - temperature/thinking 은 게이트웨이 스펙에 필드가 없어 기본 무시한다
          (관리툴 측 프롬프트별 설정에 위임).
        - thread_id 는 호출마다 uuid4 로 새로 발급한다.
    """

    def __init__(
        self,
        http_client: Any,
        *,
        base_url: str,
        token: str,
        placeholder_name: str,
        default_timeout: float,
    ) -> None:
        self._http = http_client
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._placeholder_name = placeholder_name
        self._default_timeout = default_timeout

    async def close(self) -> None:
        """IBK 게이트웨이용 httpx.AsyncClient 를 종료한다 (graceful shutdown 용)."""
        await self._http.aclose()

    @staticmethod
    def _combine_prompt(
        system: str | None,
        messages: list[dict[str, str]],
    ) -> str:
        """system + messages 를 단일 문자열로 조립한다.

        호출부는 대부분 single-turn (user turn 1개) 이고, 재시도 시 `_append_correction`
        이 user turn 을 추가하여 멀티턴이 되지만 role 구분은 필요 없다 — 이전 실패
        응답이 user 메시지 내용에 이미 인라인돼 있기 때문이다.
        """
        parts: list[str] = []
        if system:
            parts.append(system)
        parts.extend(msg.get("content", "") for msg in messages)
        return "\n\n".join(p for p in parts if p)

    async def create(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str | None = None,
        messages: list[dict[str, str]],
        timeout: float | None = None,
        temperature: float | None = None,
        thinking: str | None = None,
    ) -> LLMResponse:
        """IBK 게이트웨이를 호출하고 Anthropic 호환 LLMResponse 로 반환한다.

        max_tokens/temperature/thinking 은 IBK 게이트웨이 스펙에 대응 필드가 없어
        현재 시점에는 관리툴 측 프롬프트 설정에 위임한다(의도적 무시).
        공통 인터페이스 호환을 위해 시그니처만 유지한다.
        """
        import time as _time

        # 공통 인터페이스 호환용이며 현재 게이트웨이 스펙에서는 미사용.
        _ = (max_tokens, temperature, thinking)

        combined = self._combine_prompt(system, messages)
        body = {
            "token": self._token,
            "extra": {self._placeholder_name: combined},
        }
        thread_id = _uuid.uuid4().hex
        url = f"{self._base_url}/gpt/api/{thread_id}"

        _start = _time.perf_counter()
        resp = await self._http.post(
            url,
            json=body,
            timeout=timeout if timeout is not None else self._default_timeout,
        )
        _elapsed = (_time.perf_counter() - _start) * 1000

        # httpx 는 4xx/5xx 에도 예외를 던지지 않으므로 명시 검증
        resp.raise_for_status()
        data = resp.json()

        status = data.get("status", "")
        answer = data.get("answer", "") or ""
        if status != "success":
            raise RuntimeError(
                f"IBK LLM 호출 실패 (status={status}): {truncate_trace(answer)}"
            )

        # Qwen 계열은 <think> 태그를 응답에 그대로 내려주는 경우가 있어 제거한다.
        if "qwen" in model.lower():
            answer = _strip_thinking_tags(answer)

        await dispatch_tracking_event(LLM_CALL, {
            "node": get_current_node(),
            "prompt_summary": _build_prompt_summary(system, messages),
            "response_text": truncate_trace(answer),
            "model": model,
            "prompt_tokens": 0,       # 게이트웨이가 토큰 사용량 미제공
            "response_tokens": 0,
            "latency_ms": _elapsed,
        })

        return LLMResponse(
            content=[TextBlock(text=answer)],
            model=model,
            stop_reason="stop",
        )

    async def stream(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str | None = None,
        messages: list[dict[str, str]],
        timeout: float | None = None,
        temperature: float | None = None,
        thinking: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """IBK 게이트웨이 스트리밍 fallback — create() 1회 호출 후 단일 청크로 yield.

        게이트웨이가 SSE 스펙을 제공하지 않으므로 실제 스트리밍은 불가능하다.
        호출 측의 스트리밍 인터페이스 호환을 위해 create() 결과를 단일 text 이벤트로
        방출한다. UX 상 "응답이 한 번에 도착" 하지만 기능 경로는 동작한다.
        """
        resp = await self.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            timeout=timeout,
            temperature=temperature,
            thinking=thinking,
        )
        text = "".join(
            block.text for block in resp.content
            if isinstance(block, TextBlock)
        )
        if text:
            yield StreamEvent("text", text)


# ── 통합 클라이언트 ──


_MessagesAdapter = (
    AnthropicMessages | OpenAICompatibleMessages | IBKCustomMessages
)


class _CircuitBreakerMessages:
    """서킷브레이커로 감싼 messages 래퍼.

    내부 messages 어댑터의 create() 호출을 CircuitBreaker.call() 로 위임하여
    연속 실패 시 fast-fail(CircuitOpenError) 을 제공한다.
    """

    def __init__(
        self,
        inner: _MessagesAdapter,
        breaker: CircuitBreaker,
    ) -> None:
        self._inner = inner
        self._breaker = breaker

    async def create(self, **kwargs: Any) -> Any:
        return await self._breaker.call(lambda: self._inner.create(**kwargs))

    async def stream(self, **kwargs: Any) -> AsyncIterator[StreamEvent]:
        """스트리밍 호출을 guard 블록으로 감싼다 (정상 이터레이션 완료 = 성공)."""
        async with self._breaker.guard():
            async for ev in self._inner.stream(**kwargs):
                yield ev

    async def close(self) -> None:
        """내부 어댑터의 close() 를 위임 호출한다."""
        await self._inner.close()


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

    settings.llm_cb_enabled=True 일 때 CircuitBreaker 가 messages 를 투명하게
    감싸며, 외부 LLM API 연속 실패 시 후속 호출을 즉시 CircuitOpenError 로 거부한다.
    """

    def __init__(self, messages: _MessagesAdapter) -> None:
        if settings.llm_cb_enabled:
            breaker = CircuitBreaker(
                fail_threshold=settings.llm_cb_fail_threshold,
                reset_timeout_sec=settings.llm_cb_reset_timeout_sec,
                name=settings.llm_provider,
            )
            self.messages: Any = _CircuitBreakerMessages(messages, breaker)
        else:
            self.messages = messages

    async def close(self) -> None:
        """내부 어댑터(httpx/AsyncAnthropic/AsyncOpenAI) 연결을 정리한다.

        lifespan 종료 시 호출하여 connection leak / graceful shutdown 경고를 방지한다.
        """
        closer = getattr(self.messages, "close", None)
        if closer is None:
            return
        await closer()


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

        raw_client = AsyncAnthropic(
            api_key=settings.anthropic_api_key,
            max_retries=settings.llm_transport_max_retry,
        )
        _client = UnifiedLLMClient(messages=AnthropicMessages(raw_client))

    elif provider == "openai_compatible":
        from openai import AsyncOpenAI

        raw_client_openai = AsyncOpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url or None,
            max_retries=settings.llm_transport_max_retry,
            default_headers={
                "HTTP-Referer": settings.openai_referer,
                "X-OpenRouter-Title": settings.openai_title,
            },
        )
        _client = UnifiedLLMClient(messages=OpenAICompatibleMessages(raw_client_openai))

    elif provider == "ibk_custom":
        import httpx

        if not settings.ibk_base_url or not settings.ibk_token:
            raise ValueError(
                "LLM_PROVIDER=ibk_custom 에는 IBK_BASE_URL 과 IBK_TOKEN 이 필수입니다."
            )
        http_client = httpx.AsyncClient(
            timeout=settings.ibk_default_timeout,
        )
        _client = UnifiedLLMClient(
            messages=IBKCustomMessages(
                http_client,
                base_url=settings.ibk_base_url,
                token=settings.ibk_token,
                placeholder_name=settings.ibk_placeholder_name,
                default_timeout=settings.ibk_default_timeout,
            ),
        )

    else:
        msg = (
            f"지원하지 않는 LLM 프로바이더: '{provider}'. "
            "'anthropic' / 'openai_compatible' / 'ibk_custom' 중 선택하세요."
        )
        raise ValueError(msg)

    return _client


def reset_llm_client() -> None:
    """테스트 등에서 싱글턴을 초기화할 때 사용한다."""
    global _client
    _client = None


async def close_llm_client() -> None:
    """프로세스 종료 시 LLM 클라이언트 연결을 정리하고 싱글턴을 리셋한다.

    lifespan finally 에서 호출. 미초기화 상태면 무시한다.
    """
    global _client
    if _client is None:
        return
    try:
        await _client.close()
    finally:
        _client = None
