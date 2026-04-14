"""연속 여부 판정 + 의도 분류 + 분석 질의 재작성 서비스.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

멀티턴 대화에서 사용자 입력이 이전 맥락의 연속인지(CONTINUE),
새 독립 질의인지(NEW), 판단 불가인지(UNSURE)를 판정하고,
동시에 의도 카테고리(DATA_EXTRACTION, DATA_ANALYSIS, CASUAL_TALK 등)를
분류한다. 두 판정을 단일 LLM 호출로 수행하여 레이턴시를 최소화한다.

LLM 응답은 continuity/intent 중첩 JSON 구조이며,
_parse_response에서 평탄화된 dict로 변환 후 IntentClassifyResult로 반환한다.
CONTINUE 판정 시 continue_context에 맥락 반영된 질문 풀어쓰기를 포함한다.

DATA_ANALYSIS 판정 시 rewrite_analysis_query()로 2차 LLM 호출하여
시각화/분석 지시어를 제거한 데이터 추출 중심 질의를 생성한다.
이 재작성 질의가 후속 SQL 생성의 입력으로 사용된다.

핵심 함수:
    - intent_classifier: 연속 여부 + 의도 분류 통합 판정 (단일 LLM 호출)
    - rewrite_analysis_query: 분석 질의 → 추출 질의 재작성 (2차 LLM 호출)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from src.models.enums import HistoryDecision, IntentType
from src.config import settings
from src.utils.llm import ParseError, get_llm_client, llm_call_with_parse_retry
from src.utils.llm.response import extract_json
from src.utils.logger import get_logger
from src.utils.tracker import (
    get_current_node,
    record_prompt_variables,
    set_current_node,
)

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
    max_turns: int = 0,
) -> str:
    """대화 이력을 프롬프트 주입용 텍스트로 포맷팅한다.

    type="clarification" 항목은 제외하여 LLM이
    일반 질의/응답 맥락만 참조하도록 한다.
    명확화 Q&A가 연속 여부 판정을 오염시키는 것을 방지한다.

    Args:
        max_turns: 포함할 최근 턴 수. 단방향 메시지 기준
            (사용자 1건 + AI 1건 = 2턴). 0이면 전체 이력.
    """
    filtered = [
        t for t in conversation_history
        if t.get("type", "query") != "clarification"
    ]
    if max_turns > 0:
        filtered = filtered[-max_turns:]
    lines: list[str] = []
    for turn in filtered:
        role = "사용자" if turn["role"] == "user" else "시스템"
        content = turn["content"]
        lines.append(f"  {role}: {content}")
    return "\n".join(lines)


@dataclass
class IntentClassifyResult:
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


async def intent_classifier(
    query: str,
    conversation_history: list[dict[str, str]],
    *,
    system_prompt: str,
    user_template: str,
    clarification_history: str = "",
) -> IntentClassifyResult:
    """연속 여부 판정 + 의도 분류를 단일 LLM 호출로 수행한다.

    LLM은 continuity/intent 중첩 JSON으로 응답하며,
    CONTINUE/NEW/UNSURE 3가지를 판정한다.
    SKIP은 LLM 호출 실패 시 에러 반환용으로만 사용된다.

    질의 재작성은 수행하지 않는다.
    """
    # 유저 프롬프트 조립 — 이력 있으면 포함, 없으면 생략
    history_text = (
        _format_history(
            conversation_history,
            max_turns=settings.prompt_history_window,
        )
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
            node_name="intent_classifier",
        )
        await record_prompt_variables({
            "query": query,
            "history": history_text,
            "clarification_history": clarification_history,
        })
    except (ParseError, Exception) as e:
        logger.error("intent_classifier LLM 호출 실패", error=str(e))
        return IntentClassifyResult(
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
        return IntentClassifyResult(
            resolution=resolution,
            intent=intent,
            confidence=confidence,
            category=category,
            continue_reason=parsed.get("continue_reason", ""),
            continue_context=parsed.get("continue_context", ""),
            reason=parsed.get("intent_reason", ""),
        )

    return IntentClassifyResult(
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
    data = extract_json(raw, strict=True)
    assert data is not None  # strict=True 보장

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


# ── 분석 질의 재작성 ──


async def rewrite_analysis_query(
    query: str,
    *,
    system_prompt: str,
) -> str:
    """DATA_ANALYSIS 질의에서 시각화/분석 지시어를 제거한 추출 질의를 생성한다.

    plain text 출력이므로 JSON 파싱 재시도가 불필요하여
    LLM 클라이언트를 직접 호출한다.

    Args:
        query: 원본 사용자 질의 (시각화/분석 지시어 포함).
        system_prompt: 재작성 전용 시스템 프롬프트.

    Returns:
        데이터 추출 중심으로 재작성된 질의.

    Raises:
        Exception: LLM 호출 실패 시 (호출자에서 폴백 처리).
    """
    prev_node = get_current_node()
    set_current_node("intent_classifier_rewriter")

    try:
        client = get_llm_client()
        response = await client.messages.create(
            model=settings.llm_model,
            max_tokens=settings.llm_default_max_tokens,
            timeout=settings.llm_default_timeout,
            system=system_prompt,
            messages=[{"role": "user", "content": query}],
        )

        result = (
            response.content[0].text.strip()
            if response.content else ""
        )

        logger.info(
            "분석 질의 재작성 완료",
            original=query,
            rewritten=result,
        )

        await record_prompt_variables({
            "query": query,
            "rewritten": result,
        })

        return result
    finally:
        set_current_node(prev_node)
