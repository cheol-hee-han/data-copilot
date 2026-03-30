"""프롬프트 템플릿 치환 및 직렬화 유틸리티.

프롬프트 템플릿의 {key} 플레이스홀더를 치환하고
트래킹용 변수 사전도 함께 반환한다.
reason 계층 노드(sql_validator, recovery_planner 등)에서 동일한
치환 패턴(for key in replacements: prompt.replace)이 3+회 반복되었으므로
단일 함수로 통합한다.

핵심 함수:
    - render_prompt: 프롬프트 템플릿의 {key} 플레이스홀더를 치환
    - serialize_synonym_dict: 동의어 사전 dict → 프롬프트 주입 텍스트
    - serialize_template_registry: 출력 템플릿 dict → 프롬프트 주입 텍스트
"""

from __future__ import annotations

import json
from typing import Any


def serialize_decomp_slots(
    decomp: dict[str, Any],
) -> dict[str, str]:
    """query_decomposition 4슬롯을 프롬프트 치환용 딕셔너리로 변환.

    Args:
        decomp: query_decomposition dict.

    Returns:
        {"{measures}": "...", "{filters}": "...", ...} 형태.
    """
    return {
        f"{{{slot}}}": json.dumps(
            decomp.get(slot, []), ensure_ascii=False,
        )
        for slot in (
            "measures", "filters", "group_by", "order_limit",
        )
    }


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


def serialize_template_registry(
    templates: dict[str, dict],
) -> str:
    """출력 템플릿 레지스트리를 LLM 프롬프트 주입 텍스트로 직렬화한다.

    Args:
        templates: {문서유형: {triggers, format, expected_columns, ...}} 구조.

    Returns:
        문서유형별 트리거·포맷·기대컬럼 형태 텍스트.
    """
    lines: list[str] = []
    for doc_type, info in templates.items():
        triggers = ", ".join(info["triggers"])
        columns = ", ".join(info["expected_columns"])
        lines.append(f'\n  "{doc_type}":')
        lines.append(f"    트리거: {triggers}")
        lines.append(f'    format: {info["format"]}')
        lines.append(f"    기대컬럼: [{columns}]")
        lines.append(f'    필요엔티티: {info["required_entities"]}')
        if info.get("note"):
            lines.append(f'    참고: {info["note"]}')
    return "\n".join(lines)
