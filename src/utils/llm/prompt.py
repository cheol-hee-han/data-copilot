"""프롬프트 템플릿 치환 및 직렬화 유틸리티.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

프롬프트 템플릿의 {key} 플레이스홀더를 치환하고 트래킹용 변수 사전도
함께 반환하는 공통 함수를 제공한다.

reason 계층 노드(sql_validator, recovery_planner 등)에서 동일한
치환 패턴(for key in replacements: prompt.replace)이 3+회 반복되었으므로
단일 함수로 통합하여 중복을 제거하고, 치환 결과와 트래킹용 변수 사전을
동시에 반환하여 추적 누락을 방지한다.

Jinja2 등 외부 템플릿 엔진 대신 str.replace()를 사용하는 이유:
폐쇄망 의존성 최소화 + 프롬프트 템플릿이 단순 치환만 필요하기 때문.

핵심 함수:
    - render_prompt: 프롬프트 템플릿의 {key} 플레이스홀더를 치환
    - serialize_decomp_slots: (deprecated) query_decomposition → 프롬프트 치환용 dict
    - serialize_synonym_dict: 동의어 사전 dict → 프롬프트 주입 텍스트
"""

from __future__ import annotations

import json
from typing import Any


def serialize_decomp_slots(
    decomp: dict[str, Any],
) -> dict[str, str]:
    """query_decomposition 슬롯을 프롬프트 치환용 딕셔너리로 변환.

    .. deprecated::
        validator는 ``sql_validator._serialize_normalized_for_validation``
        으로 대체되었다. 이 함수는 다른 소비자가 없으므로 향후 제거 예정.

    Args:
        decomp: query_decomposition dict.

    Returns:
        {"{measures}": "...", "{filters}": "...", ...} 형태.
    """
    result = {
        f"{{{slot}}}": json.dumps(
            decomp.get(slot, []), ensure_ascii=False,
        )
        for slot in (
            "measures", "filters", "group_by", "order_limit",
        )
    }
    result["{output_hint}"] = json.dumps(
        decomp.get("output_hint", {}), ensure_ascii=False,
    )
    return result


def render_prompt(
    template: str,
    replacements: dict[str, str],
) -> tuple[str, dict[str, str]]:
    """프롬프트 템플릿의 플레이스홀더를 치환한다.

    Args:
        template: "{key}" 형태의 플레이스홀더가 포함된 프롬프트 템플릿.
        replacements: {"{key}": "value"} 형태의 치환 사전.

    Returns:
        (치환된 프롬프트, 트래킹용 변수 사전) 튜플.
        트래킹용 사전은 키에서 중괄호를 제거한 형태이다.
    """
    prompt = template
    for key, value in replacements.items():
        prompt = prompt.replace(key, value)

    # 트래킹용 변수 사전 ({} 제거한 키)
    variables = {k.strip("{}"): v for k, v in replacements.items()}
    return prompt, variables


def serialize_synonym_dict(
    synonyms: dict[str, dict[str, list[str]]],
) -> str:
    """동의어 사전을 LLM 프롬프트 주입 텍스트로 직렬화한다.

    Args:
        synonyms: {카테고리: {표준용어: [동의어, ...]}} 구조.

    Returns:
        카테고리별 "표준용어 ← 동의어1, 동의어2" 형태 텍스트.
    """
    lines: list[str] = []
    for category, syn_dict in synonyms.items():
        lines.append(f"\n[{category}]")
        for standard, variants in syn_dict.items():
            lines.append(f'  "{standard}" ← {", ".join(variants)}')
    return "\n".join(lines)


