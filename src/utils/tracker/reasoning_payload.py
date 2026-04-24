"""LLM 노드용 REASONING_STEP 이벤트 표준 payload 구성 유틸리티.

작성자: 한철희 / 최종수정: 2026-04-23

모든 LLM 노드가 프롬프트 [INPUT] 치환 변수와 [OUTPUT_CONTRACT] 원본 응답을
동일한 스키마로 REASONING_STEP 이벤트에 담도록 강제한다.

원칙:
    - inputs.prompt_variables: ``render_prompt`` 가 반환한 variables dict 를
      그대로 사용. 프롬프트 템플릿의 ``{placeholder}`` 와 1:1 매칭된다.
    - output.raw_response: ``llm_call_with_parse_retry`` 가 반환한 raw_text.
      LLM 이 실제로 방출한 JSON 문자열(parse 전) 을 그대로 보존한다.
    - output.parsed: 렌더링·분기에 쓰이는 요약 (선택). 원본 재구성에는
      불필요하므로 경량 dict 권장.

LLM 을 호출하지 않은 경로(스킵/실패) 에서는 ``raw_response`` 자리에 반드시
``llm_skip_sentinel`` / ``llm_failure_sentinel`` 결과를 사용하여 trace 소비자가
``[SKIP]`` / ``[FAIL]`` 접두로 한 눈에 식별할 수 있게 한다.

사용법::

    from src.utils.tracker.reasoning_payload import (
        LLMInteraction,
        build_llm_reasoning_payload,
        llm_failure_sentinel,
        llm_skip_sentinel,
    )

    raw_text, parsed = await llm_call_with_parse_retry(...)
    interaction = LLMInteraction(
        prompt_variables=variables,
        raw_response=raw_text,
    )
    await dispatch_tracking_event(
        REASONING_STEP,
        build_llm_reasoning_payload(
            node="intent_classifier",
            phase="interpret",
            round=0,
            hypothesis_id="",
            interaction=interaction,
            routing={"next_node": "...", "reason": "..."},
            parsed_summary={"intent": parsed.intent.primary},
        ),
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ── LLM 미호출 자리의 raw_response 센티넬 ─────────────────────

_SKIP_PREFIX = "[SKIP]"
_FAIL_PREFIX = "[FAIL]"


def llm_skip_sentinel(reason: str) -> str:
    """LLM 호출을 의도적으로 생략한 경로용 raw_response 센티넬.

    데이터 없음·최소행 미달·사전조건 불충족 등 LLM 판단 없이 바이패스한
    경우에 사용한다. trace 소비자는 접두(``[SKIP]``)만으로 스킵을 식별한다.

    Args:
        reason: 스킵 사유(짧은 한글 설명 권장).

    Returns:
        ``"[SKIP] {reason}"`` 형태 문자열.
    """
    return f"{_SKIP_PREFIX} {reason}".strip()


def llm_failure_sentinel(kind: str, error: Exception | str) -> str:
    """LLM 호출 또는 후처리 실패 경로용 raw_response 센티넬.

    ParseError·네트워크·타임아웃 등으로 원본 응답 없이 폴백한 경우에 사용한다.
    trace 소비자는 접두(``[FAIL]``)만으로 실패를 식별한다.

    Args:
        kind: 실패 분류(예: ``"LLM 실패"``, ``"ParseError"``).
        error: 예외 인스턴스 또는 메시지.

    Returns:
        ``"[FAIL] {kind}: {error}"`` 형태 문자열.
    """
    return f"{_FAIL_PREFIX} {kind}: {error}"


@dataclass
class LLMInteraction:
    """LLM 노드의 프롬프트·응답 한 쌍.

    Attributes:
        prompt_variables: 프롬프트 템플릿의 ``{placeholder}`` 치환에 사용된
            실제 값 dict. ``render_prompt(template, replacements)`` 두 번째
            반환값을 그대로 담는다.
        raw_response: LLM 이 반환한 원본 응답 텍스트 (parse 전).
            ``llm_call_with_parse_retry`` 첫 번째 반환값을 그대로 담는다.
    """

    prompt_variables: dict[str, Any] = field(default_factory=dict)
    raw_response: str = ""


def build_llm_reasoning_payload(
    *,
    node: str,
    phase: str,
    round: int,
    hypothesis_id: str,
    interaction: LLMInteraction,
    routing: dict[str, Any],
    parsed_summary: dict[str, Any] | None = None,
    extra_inputs: dict[str, Any] | None = None,
    step_type: str = "llm_decision",
) -> dict[str, Any]:
    """LLM 노드용 REASONING_STEP payload 를 구성한다.

    Args:
        node: 노드 이름 (예: "query_normalizer").
        phase: 파이프라인 단계 (예: "interpret", "reason", "present").
        round: 탐색 라운드 (재계획 반복 횟수). 단일 실행 노드는 0.
        hypothesis_id: 현재 가설 id. 해당 없으면 빈 문자열.
        interaction: 프롬프트 변수 + 원본 응답 쌍.
        routing: ``{"next_node": ..., "reason": ...}`` 형태.
        parsed_summary: 파싱된 결과 중 렌더링·분기 판단에 쓰일 요약.
            None 이면 output 에 포함하지 않는다.
        extra_inputs: 프롬프트 변수 외 입력 메타 (예: raw_query, dialect).
            ``prompt_variables`` 와 키 중복 금지 — 중복되면 ValueError.
        step_type: visualizer 분기용. LLM 노드는 기본 "llm_decision".
            recovery_agent 등 특수 분기는 "recovery" 사용.

    Returns:
        REASONING_STEP 이벤트 페이로드 dict.

    Raises:
        ValueError: extra_inputs 키가 prompt_variables 키와 충돌할 때.
    """
    inputs: dict[str, Any] = {
        "prompt_variables": dict(interaction.prompt_variables),
    }
    if extra_inputs:
        overlap = set(extra_inputs) & set(interaction.prompt_variables)
        if overlap:
            raise ValueError(
                "extra_inputs 는 prompt_variables 와 키가 중복되면 안 됩니다: "
                f"{sorted(overlap)}",
            )
        inputs.update(extra_inputs)

    output: dict[str, Any] = {
        "raw_response": interaction.raw_response,
    }
    if parsed_summary is not None:
        output["parsed"] = dict(parsed_summary)

    return {
        "node": node,
        "phase": phase,
        "step_type": step_type,
        "round": round,
        "hypothesis_id": hypothesis_id,
        "inputs": inputs,
        "output": output,
        "routing": routing,
    }
