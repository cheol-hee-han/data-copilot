"""LLM 응답 JSON 추출 유틸리티.

LLM이 반환하는 텍스트에서 JSON 객체를 추출하여 dict로 변환한다.
코드펜스(```json ... ```) 감싸기, 전후 설명 텍스트 등을 처리한다.

핵심 함수:
    - extract_json: LLM 텍스트에서 첫 번째 JSON 객체를 추출하여 dict로 반환
"""

from __future__ import annotations

import json
import re
from typing import Any

# 코드펜스 내부 추출 패턴
_CODE_FENCE_PATTERN = re.compile(
    r"```(?:json)?\s*([\s\S]*?)```",
)

# 첫 번째 JSON 객체를 매칭하는 패턴 (중첩 브레이스 포함)
_JSON_PATTERN = re.compile(r"\{[\s\S]*\}")


def _strip_code_fence(raw: str) -> str:
    """코드펜스가 있으면 내부 텍스트만 추출한다."""
    match = _CODE_FENCE_PATTERN.search(raw)
    if match:
        return match.group(1).strip()
    return raw


def extract_json(
    raw: str,
    *,
    strict: bool = False,
) -> dict[str, Any] | None:
    """LLM 응답 텍스트에서 첫 번째 JSON 객체를 추출한다.

    코드펜스(```json ... ```)가 있으면 내부만 추출한 뒤 파싱한다.
    코드펜스가 없으면 전체 텍스트에서 {…}를 정규식으로 탐색한다.

    Args:
        raw: LLM 응답 원문.
        strict: True이면 파싱 실패 시 ValueError를 raise.

    Returns:
        파싱된 dict. strict=False일 때 실패 시 None.

    Raises:
        ValueError: strict=True이고 JSON 파싱에 실패한 경우.
    """
    # 1. 코드펜스 내부 추출 시도
    stripped = _strip_code_fence(raw)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    # 2. 정규식으로 {…} 탐색 (코드펜스 밖 설명 텍스트 대응)
    match = _JSON_PATTERN.search(raw)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    if strict:
        raise ValueError(
            "LLM이 유효한 JSON을 반환하지 않았습니다",
        )
    return None
