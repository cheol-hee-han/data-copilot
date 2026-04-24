"""LLM 응답 포맷 파싱 재시도 유틸리티.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

소형/로컬 LLM이 지정된 출력 포맷을 준수하지 못할 경우,
이전 실패 응답을 대화에 포함하여 재시도한다.
출력 형식은 system 프롬프트에 이미 명시되어 있으므로
별도 format_hint 없이 "형식을 지켜달라"는 교정 메시지만 추가한다.

재시도 전략:
    1차: 원본 프롬프트로 LLM 호출
    2차~: 이전 실패 응답 + 교정 메시지를 대화에 추가하여 재호출
    최종 실패: ParseError 를 raise 하거나 호출자가 폴백 처리

사용 예시:
    text, parsed = await llm_call_with_parse_retry(
        system="...",
        messages=[{"role": "user", "content": "..."}],
        parse_fn=_parse_intent_response,
    )
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Any, Callable, Literal, TypeVar

from src.agents.nodes.thinking_modes import get_thinking_mode
from src.config import settings
from src.utils.llm.client import get_llm_client
from src.utils.logger import get_logger
from src.utils.tracker import (
    get_current_node,
    set_current_node,
)
from src.utils.tracker.dispatch import (
    LLM_DELTA_CHUNK,
    LLM_DELTA_END,
    LLM_DELTA_RESET,
    LLM_DELTA_START,
    dispatch_tracking_event,
)
from src.utils.truncate import truncate_log

logger = get_logger(__name__)

T = TypeVar("T")


class ParseError(Exception):
    """LLM 응답 파싱 실패 (최대 재시도 후에도 실패)."""

    def __init__(self, message: str, last_response: str = "") -> None:
        super().__init__(message)
        self.last_response = last_response


async def llm_call_with_parse_retry(
    *,
    system: str,
    messages: list[dict[str, str]],
    parse_fn: Callable[[str], T],
    max_tokens: int | None = None,
    timeout: float | None = None,
    max_retries: int | None = None,
    node_name: str = "",
    temperature: float | None = None,
    thinking: str | None = None,
) -> tuple[str, T]:
    """LLM 호출 + 파싱을 재시도하는 통합 유틸리티.

    Args:
        system: 시스템 프롬프트.
        messages: 사용자 메시지 리스트.
        parse_fn: LLM 응답 텍스트를 파싱하는 함수.
            성공 시 파싱 결과를 반환, 실패 시 ValueError 를 raise 해야 한다.
        max_tokens: 최대 생성 토큰 수.
        timeout: LLM 호출 타임아웃 (초).
        max_retries: 최대 재시도 횟수. None 이면 settings.llm_parse_max_retry 사용.
        node_name: 로깅용 노드 이름.
        temperature: 샘플링 temperature. None 이면 어댑터/서버 기본값.
        thinking: thinking 모드 명시값 ("off"/"auto"/"low"/"medium"/"high").
            None 이면 node_name 기반 NODE_THINKING_MODES lookup 으로 폴백.

    Returns:
        (raw_text, parsed_result) 튜플.

    Raises:
        ParseError: 최대 재시도 후에도 파싱 실패 시.
        Exception: LLM 호출 자체가 실패할 경우 (네트워크 등).
    """
    if max_tokens is None:
        max_tokens = settings.llm_default_max_tokens
    if timeout is None:
        timeout = settings.llm_default_timeout
    if max_retries is None:
        max_retries = settings.llm_parse_max_retry

    client = get_llm_client()
    model = settings.llm_model

    # 원본 메시지를 복사하여 재시도 시 교정 메시지를 추가
    current_messages = list(messages)
    last_text = ""

    # thinking 미지정 시 node_name(LLM 호출 단위) 기준으로 확정.
    # node_name 이 없으면 콜백핸들러가 설정한 그래프 노드명으로 폴백.
    _prev_node = get_current_node()
    if thinking is None:
        thinking = get_thinking_mode(node_name or _prev_node)

    if node_name:
        set_current_node(node_name)

    import time as _time

    for attempt in range(1 + max_retries):
        _llm_start = _time.perf_counter()
        response = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            timeout=timeout,
            system=system,
            messages=current_messages,
            temperature=temperature,
            thinking=thinking,
        )
        _llm_elapsed = (_time.perf_counter() - _llm_start) * 1000

        last_text = (
            response.content[0].text.strip()
            if response.content else ""
        )

        logger.info(
            "LLM 호출 완료",
            node=node_name,
            model=model,
            attempt=attempt + 1,
            latency_ms=round(_llm_elapsed, 1),
            response_preview=truncate_log(last_text) if last_text else "(빈 응답)",
        )

        if not last_text:
            logger.warning(
                "LLM 응답이 비어있음",
                node=node_name,
                attempt=attempt + 1,
            )
            # 빈 응답도 재시도 대상
            if attempt < max_retries:
                current_messages = _append_correction(
                    current_messages, last_text, "응답이 비어있음",
                )
            continue

        # 파싱 시도
        try:
            parsed = parse_fn(last_text)
            if attempt > 0:
                logger.info(
                    "파싱 재시도 성공",
                    node=node_name,
                    attempt=attempt + 1,
                )
            # 세부 노드명 복원
            if node_name:
                set_current_node(_prev_node)
            return last_text, parsed
        except ValueError as e:
            logger.warning(
                "LLM 응답 포맷 불일치",
                node=node_name,
                attempt=attempt + 1,
                max_retries=max_retries,
                error=str(e),
                response_preview=truncate_log(last_text),
            )
            if attempt < max_retries:
                current_messages = _append_correction(
                    current_messages, last_text, str(e),
                )

    # 세부 노드명 복원
    if node_name:
        set_current_node(_prev_node)

    raise ParseError(
        f"[{node_name}] {max_retries + 1}회 시도 후에도 포맷 파싱 실패",
        last_response=last_text,
    )


async def _emit_delta(
    event: str, *, turn_id: str, part_id: str,
    **extra: Any,
) -> None:
    payload: dict[str, Any] = {
        "turn_id": turn_id, "part_id": part_id,
    }
    payload.update(extra)
    await dispatch_tracking_event(event, payload)


async def _stream_accumulate_once(
    *,
    system: str,
    messages: list[dict[str, str]],
    max_tokens: int,
    timeout: float,
    temperature: float | None,
    thinking: str | None,
    turn_id: str,
    part_id: str,
    is_cancelled: Callable[[], Awaitable[bool]] | None,
) -> str:
    """한 번의 스트리밍으로 텍스트를 누적하고 delta.chunk 를 뿌린다.

    취소 감지 시 asyncio.CancelledError 를 raise. 네트워크 등 예외는 그대로 전파.
    thinking kind 이벤트는 사용자에게 전송하지 않는다 (내부 추론은 숨김).
    """
    client = get_llm_client()
    accumulated: list[str] = []
    async for ev in client.messages.stream(
        model=settings.llm_model,
        max_tokens=max_tokens,
        timeout=timeout,
        system=system,
        messages=messages,
        temperature=temperature,
        thinking=thinking,
    ):
        if is_cancelled and await is_cancelled():
            raise asyncio.CancelledError()
        if ev.kind != "text" or not ev.text:
            continue
        accumulated.append(ev.text)
        await _emit_delta(
            LLM_DELTA_CHUNK,
            turn_id=turn_id, part_id=part_id,
            text=ev.text,
        )
    return "".join(accumulated)


async def _stream_parse_with_retries(
    *,
    parse_fn: Callable[[str], T],
    messages: list[dict[str, str]],
    max_retries: int,
    node_name: str,
    turn_id: str,
    part_id: str,
    stream_kwargs: dict[str, Any],
) -> tuple[str, T]:
    """스트리밍→누적→파싱 시도를 최대 ``max_retries+1`` 회 반복.

    최종 실패 시 end{error_code="PARSE_FAIL"} 방출 후 ParseError raise.
    """
    current_messages = messages
    last_text = ""
    for attempt in range(1 + max_retries):
        if attempt > 0:
            await _emit_delta(
                LLM_DELTA_RESET,
                turn_id=turn_id, part_id=part_id,
                reason="parse_error",
            )
        last_text = await _stream_accumulate_once(
            messages=current_messages, **stream_kwargs,
        )
        try:
            return last_text, parse_fn(last_text)
        except ValueError as e:
            logger.warning(
                "스트리밍 응답 파싱 실패",
                node=node_name,
                attempt=attempt + 1,
                error=str(e),
                response_preview=truncate_log(last_text),
            )
            if attempt < max_retries:
                current_messages = _append_correction(
                    current_messages, last_text, str(e),
                )
    await _emit_delta(
        LLM_DELTA_END,
        turn_id=turn_id, part_id=part_id,
        error=True, error_code="PARSE_FAIL",
    )
    raise ParseError(
        f"[{node_name}] 스트리밍 {max_retries + 1}회 시도 후 파싱 실패",
        last_response=last_text,
    )


async def llm_stream_with_parse_retry(
    *,
    system: str,
    messages: list[dict[str, str]],
    parse_fn: Callable[[str], T],
    turn_id: str,
    part_id: str,
    part_type: Literal["analysis", "svg"],
    max_tokens: int | None = None,
    timeout: float | None = None,
    max_retries: int | None = None,
    node_name: str = "",
    temperature: float | None = None,
    thinking: str | None = None,
    is_cancelled: Callable[[], Awaitable[bool]] | None = None,
) -> tuple[str, T]:
    """스트리밍 LLM 호출 + 파싱 재시도 통합 유틸리티.

    델타 이벤트 시퀀스::

        start → chunk* → (parse 실패 시 reset → chunk*)* → end

    Returns:
        (raw_text, parsed) 튜플. 호출자는 성공 복귀 시
        ``streaming_delivered=True`` 로 상태를 기록한다.

    Raises:
        ParseError: 최대 재시도 후에도 파싱 실패 (end{error=True} 방출).
        asyncio.CancelledError: 취소 요청 감지 (end{cancelled=True} 방출).
        Exception: 네트워크 등 외부 오류 (end{error=True} 방출).
    """
    if max_tokens is None:
        max_tokens = settings.llm_default_max_tokens
    if timeout is None:
        timeout = settings.llm_default_timeout
    if max_retries is None:
        max_retries = settings.llm_parse_max_retry

    # thinking 미지정 시 node_name(LLM 호출 단위) 기준으로 확정
    _prev_node = get_current_node()
    if thinking is None:
        thinking = get_thinking_mode(node_name or _prev_node)

    if node_name:
        set_current_node(node_name)

    await _emit_delta(
        LLM_DELTA_START,
        turn_id=turn_id, part_id=part_id,
        part_type=part_type,
    )

    stream_kwargs = {
        "system": system,
        "max_tokens": max_tokens,
        "timeout": timeout,
        "temperature": temperature,
        "thinking": thinking,
        "turn_id": turn_id,
        "part_id": part_id,
        "is_cancelled": is_cancelled,
    }
    try:
        last_text, parsed = await _stream_parse_with_retries(
            parse_fn=parse_fn,
            messages=list(messages),
            max_retries=max_retries,
            node_name=node_name,
            turn_id=turn_id,
            part_id=part_id,
            stream_kwargs=stream_kwargs,
        )
        await _emit_delta(
            LLM_DELTA_END,
            turn_id=turn_id, part_id=part_id,
        )
        return last_text, parsed
    except asyncio.CancelledError:
        await _emit_delta(
            LLM_DELTA_END,
            turn_id=turn_id, part_id=part_id,
            cancelled=True,
        )
        raise
    except ParseError:
        raise
    except Exception as e:
        await _emit_delta(
            LLM_DELTA_END,
            turn_id=turn_id, part_id=part_id,
            error=True, error_code="STREAM_ERROR",
        )
        logger.error(
            "스트리밍 LLM 호출 오류",
            node=node_name, error=str(e),
        )
        raise
    finally:
        if node_name:
            set_current_node(_prev_node)


def _build_correction_msg(
    failed_response: str,
    parse_error: str,
) -> str:
    """출력 형식 오류 교정 메시지를 생성한다."""
    parts = ["[출력 형식 오류]"]
    if failed_response:
        preview = truncate_log(failed_response)
        parts.append(f"당신의 응답: {preview}")
    if parse_error:
        parts.append(f"파싱 실패 원인: {parse_error}")
    parts.append(
        "시스템 프롬프트의 [출력 형식]에 맞는 JSON만 출력하세요."
    )
    return "\n".join(parts)


def _append_correction(
    messages: list[dict[str, str]],
    failed_response: str,
    parse_error: str = "",
) -> list[dict[str, str]]:
    """실패한 응답과 교정 요청을 대화에 추가한다.

    LLM 이 자신의 이전 응답을 보고 올바른 형식으로 교정하도록 유도한다.
    실패 원인을 명시하여 소형 모델도 교정할 수 있도록 한다.
    """
    corrected = list(messages)

    correction = _build_correction_msg(
        failed_response, parse_error,
    )
    corrected.append({"role": "user", "content": correction})

    return corrected
