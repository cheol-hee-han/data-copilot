"""연속 여부 판정 + 의도 분류 통합 서비스.

단일 프롬프트로 CONTINUE/NEW/UNSURE + 카테고리를 동시 판정한다.
LLM 응답은 continuity/intent 중첩 JSON 구조이며,
질의 재작성은 수행하지 않는다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from src.models.enums import HistoryDecision, IntentType
from src.config import settings
from src.utils.llm import ParseError, llm_call_with_parse_retry
from src.utils.logger import get_logger
from src.utils.tracker import record_prompt_variables

logger = get_logger(__name__)


# ── 카테고리 → IntentType 변환 ──

_CONF_MAP: dict[str, float] = {
    "HIGH": 0.95, "MEDIUM": 0.7, "LOW": 0.4,
}
_CATEGORY_INTENT_MAP: dict[str, IntentType] = {
    "DATA_EXTRACTION": IntentType.DATA_EXTRACTION,
    "DATA_ANALYSIS": IntentType.DATA_ANALYSIS,
    "DATA_QUERY": IntentType.DATA_EXTRACTION,  # 하위 호환
    "CASUAL_TALK": IntentType.CASUAL_TALK,
    "META_QUESTION": IntentType.META_QUESTION,
    "CLARIFICATION": IntentType.CLARIFICATION_NEEDED,
    "AMBIGUOUS": IntentType.CLARIFICATION_NEEDED,
}


def _map_category_to_intent(
    category: str,
    confidence_str: str,
) -> tuple[IntentType, float]:
    """Intent Gate 카테고리를 IntentType으로 매핑한다."""
    confidence = _CONF_MAP.get(confidence_str, 0.5)
    intent = _CATEGORY_INTENT_MAP.get(
        category, IntentType.CLARIFICATION_NEEDED,
    )
    return intent, confidence


# ── 대화 이력 포맷팅 ──


def _format_history(
    conversation_history: list[dict[str, str]],
    max_turns: int = 4,
) -> str:
    """대화 이력을 프롬프트 주입용 텍스트로 포맷팅한다.

    type="clarification" 항목은 제외하여 LLM이
    일반 질의/응답 맥락만 참조하도록 한다.
    """
    filtered = [
        t for t in conversation_history
        if t.get("type", "query") != "clarification"
    ]
    recent = filtered[-max_turns:]
    lines: list[str] = []
    for turn in recent:
        role = "사용자" if turn["role"] == "user" else "시스템"
        content = turn["content"]
        lines.append(f"  {role}: {content}")
    return "\n".join(lines)


@dataclass
class ContextClassifyResult:
    """통합 판정 결과."""

    # 연속 여부 판정 (질의 재작성 없음)
    resolution: HistoryDecision

    # 의도 분류
    intent: IntentType = IntentType.UNKNOWN
    confidence: float = 0.0
    category: str = ""
    reason: str = ""  # 의도 분류 근거 또는 continuity 판정 사유

    # CONTINUE 전용 필드
    continue_reason: str = ""    # 왜 CONTINUE라고 판단했는지
    continue_context: str = ""   # 대화 맥락을 반영한 실제 질문 풀어쓰기

    # UNSURE / AMBIGUOUS 전용 필드
    ambiguities: list[dict] | None = None  # LLM이 생성한 구조화 모호성

    def __post_init__(self) -> None:
        if self.ambiguities is None:
            self.ambiguities = []

    is_error: bool = False


async def context_classifier(
    query: str,
    conversation_history: list[dict[str, str]],
    *,
    system_prompt: str,
    user_template: str,
    clarification_history: str = "",
) -> ContextClassifyResult:
    """연속 여부 판정 + 의도 분류를 단일 LLM 호출로 수행한다.

    LLM은 continuity/intent 중첩 JSON으로 응답하며,
    CONTINUE/NEW/UNSURE 3가지를 판정한다.
    SKIP은 LLM 호출 실패 시 에러 반환용으로만 사용된다.

    질의 재작성은 수행하지 않는다.
    """
    # 유저 프롬프트 조립 — 이력 있으면 포함, 없으면 생략
    history_text = (
        _format_history(conversation_history)
        if conversation_history
        else ""
    )
    user_prompt = user_template.format(
        history=history_text,
        query=query,
        clarification_history=clarification_history,
    )

    try:
        _, parsed = await llm_call_with_parse_retry(
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            parse_fn=_parse_response,
            max_tokens=settings.llm_default_max_tokens,
            timeout=settings.llm_default_timeout,
            node_name="context_classifier",
        )
        await record_prompt_variables({
            "query": query,
            "history": history_text,
            "clarification_history": clarification_history,
        })
    except (ParseError, Exception) as e:
        logger.error("context_classifier LLM 호출 실패", error=str(e))
        return ContextClassifyResult(
            resolution=HistoryDecision.SKIP,
            is_error=True,
        )

    resolution = parsed["resolution"]
    category = parsed.get("category", "AMBIGUOUS")

    # UNSURE는 의도 미분류 → continuity 신뢰도 사용
    if resolution == HistoryDecision.UNSURE:
        confidence_str = parsed.get("continuity_confidence", "MEDIUM")
    else:
        confidence_str = parsed.get("intent_confidence", "MEDIUM")

    intent, confidence = _map_category_to_intent(
        category, confidence_str,
    )

    if resolution == HistoryDecision.CONTINUE:
        return ContextClassifyResult(
            resolution=resolution,
            intent=intent,
            confidence=confidence,
            category=category,
            continue_reason=parsed.get("continue_reason", ""),
            continue_context=parsed.get("continue_context", ""),
            reason=parsed.get("intent_reason", ""),
        )

    return ContextClassifyResult(
        resolution=resolution,
        intent=intent,
        confidence=confidence,
        category=category,
        reason=(
            parsed.get("intent_reason", "")
            or parsed.get("continuity_reason", "")
        ),
        ambiguities=parsed.get("ambiguities", []),
    )


# ── 파싱 함수 ──

def _parse_response(raw: str) -> dict:
    """LLM 응답 중첩 JSON 파싱.

    입력 형식:
    {
      "continuity": { "label", "confidence", "reason", "context" },
      "intent":     { "label", "confidence", "reason" }
    }

    평탄화된 dict로 반환한다.
    """
    cleaned = re.sub(r"```(?:json)?\s*", "", raw)
    cleaned = cleaned.replace("```", "").strip()
    data = json.loads(cleaned)

    # ── 중첩 구조에서 추출 ──
    continuity = data.get("continuity", {})
    intent_obj = data.get("intent", {})

    # resolution (continuity.label)
    raw_resolution = continuity.get("label", "").upper()
    try:
        resolution = HistoryDecision(raw_resolution)
    except ValueError:
        raise ValueError(
            f"허용되지 않는 continuity.label: {raw_resolution}"
        )

    # category (intent.label)
    from src.agents.models.normalization import (
        VALID_QUERY_CATEGORIES,
    )
    cat = intent_obj.get("label", "").upper()
    if cat == "CLARIFICATION":
        cat = "AMBIGUOUS"
    # UNSURE일 때 의도 미분류 허용
    if resolution == HistoryDecision.UNSURE and not cat:
        cat = "AMBIGUOUS"
    if cat not in VALID_QUERY_CATEGORIES:
        cat = "AMBIGUOUS"

    result: dict = {
        "resolution": resolution,
        "category": cat,
        "continuity_confidence": (
            continuity.get("confidence", "MEDIUM").upper()
        ),
        "intent_confidence": (
            intent_obj.get("confidence", "MEDIUM").upper()
        ),
    }

    if resolution == HistoryDecision.CONTINUE:
        result["continue_reason"] = continuity.get("reason", "")
        result["continue_context"] = continuity.get("context", "")
        result["intent_reason"] = intent_obj.get("reason", "")
    else:
        result["continuity_reason"] = continuity.get("reason", "")
        result["intent_reason"] = intent_obj.get("reason", "")

    # UNSURE / AMBIGUOUS: LLM 생성 구조화 모호성
    ambiguities = data.get("ambiguities", [])
    if ambiguities:
        result["ambiguities"] = ambiguities

    return result
