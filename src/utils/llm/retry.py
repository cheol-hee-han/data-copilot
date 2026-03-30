"""LLM 응답 포맷 파싱 재시도 유틸리티.

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

from typing import Any, Callable, TypeVar

from src.config import settings
from src.utils.llm.client import get_llm_client
from src.utils.logger import get_logger
from src.utils.tracker import (
    get_current_node,
    set_current_node,
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

    # 세부 노드명이 있으면 contextvars에 임시 설정
    _prev_node = get_current_node()
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
