"""연속 여부 판정 + 의도 분류 서비스.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

멀티턴 대화에서 사용자 입력이 이전 맥락의 연속인지(CONTINUE),
새 독립 질의인지(NEW), 판단 불가인지(UNSURE)를 판정하고,
동시에 의도 카테고리(DATA_EXTRACTION, DATA_ANALYSIS, CASUAL_TALK 등)를
분류한다. 두 판정을 단일 LLM 호출로 수행하여 레이턴시를 최소화한다.

LLM 응답은 continuity/intent 중첩 JSON 구조이며,
_parse_response에서 평탄화된 dict로 변환 후 IntentClassifyResult로 반환한다.
CONTINUE 판정 시 continue_context에 맥락 반영된 질문 풀어쓰기를 포함한다.

DATA_ANALYSIS 질의의 시각화/분석 지시어 제거는 본 서비스가 아닌
`src/services/query_normalizer.py` 의 `extraction_query_rewriter` 에서
수행한다. 이유: CONTINUE 턴 오케스트레이터 라우팅은 본 노드에서 일어나며,
그 시점에 재작성된 질의가 주입되면 맥락 해석이 왜곡되기 때문.

핵심 함수:
    - intent_classifier: 연속 여부 + 의도 분류 통합 판정 (단일 LLM 호출)
"""

from __future__ import annotations

from dataclasses import dataclass

from src.models.enums import HistoryDecision, IntentType
from src.config import settings
from src.agents.nodes.thinking_modes import LLMNode
from src.utils.llm import ParseError, llm_call_with_parse_retry
from src.utils.llm.prompt import render_prompt
from src.utils.llm.response import extract_json
from src.utils.logger import get_logger
from src.utils.tracker import LLMInteraction, llm_failure_sentinel

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

    명확화 Q&A도 포함하여 LLM이 이전 턴에서 해소된 용어를
    참조할 수 있도록 한다. 명확화 메시지는 [명확화] 태그로
    구분하여 일반 질의/응답과 혼동되지 않도록 한다.

    Args:
        max_turns: 포함할 최근 턴 수. 단방향 메시지 기준
            (사용자 1건 + AI 1건 = 2턴). 0이면 전체 이력.
    """
    if max_turns > 0:
        conversation_history = conversation_history[-max_turns:]
    lines: list[str] = []
    for turn in conversation_history:
        role = "사용자" if turn["role"] == "user" else "시스템"
        content = turn["content"]
        is_clarification = turn.get("type") == "clarification"
        prefix = "[명확화] " if is_clarification else ""
        lines.append(f"  {prefix}{role}: {content}")
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

    # ── analyzer 실행 판정 ──
    # 기본값 False: 본 서비스는 명세 추출이 주 업무이며 analyzer(해석 텍스트 생성)는
    # 명시적 분석 요청("분석해줘", "비교", "추이", "원인" 등)이 있을 때만 실행.
    # LLM이 true를 명시해야만 analyzer 호출.
    needs_analyzer: bool = False
    needs_analyzer_reason: str = ""

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
) -> tuple[IntentClassifyResult, LLMInteraction]:
    """연속 여부 판정 + 의도 분류를 단일 LLM 호출로 수행한다.

    LLM은 continuity/intent 중첩 JSON으로 응답하며,
    CONTINUE/NEW/UNSURE 3가지를 판정한다.
    SKIP은 LLM 호출 실패 시 에러 반환용으로만 사용된다.

    질의 재작성은 수행하지 않는다.

    Returns:
        (IntentClassifyResult, LLMInteraction): 분류 결과와 프롬프트 변수·원본
        응답 쌍. REASONING_STEP payload 구성에 사용된다 (Option B §trace-input-
        output-redesign). LLM 실패 시 interaction.raw_response 에 예외 메시지가
        기록되어 장애 원인이 trace 에 보존된다.
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
    user_prompt, prompt_vars = render_prompt(user_template, {
        "{history}": history_text,
        "{query}": query,
        "{clarification_history}": clarification_history,
    })

    try:
        raw_text, parsed = await llm_call_with_parse_retry(
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            parse_fn=_parse_response,
            max_tokens=settings.llm_default_max_tokens,
            timeout=settings.llm_default_timeout,
            node_name=LLMNode.INTENT_CLASSIFIER,
        )
    except (ParseError, Exception) as e:
        logger.error("intent_classifier LLM 호출 실패", error=str(e))
        return (
            IntentClassifyResult(
                resolution=HistoryDecision.SKIP,
                is_error=True,
            ),
            LLMInteraction(
                prompt_variables=prompt_vars,
                raw_response=llm_failure_sentinel("LLM 실패", e),
            ),
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

    interaction = LLMInteraction(
        prompt_variables=prompt_vars,
        raw_response=raw_text,
    )

    if resolution == HistoryDecision.CONTINUE:
        return (
            IntentClassifyResult(
                resolution=resolution,
                intent=intent,
                confidence=confidence,
                category=category,
                continue_reason=parsed.get("continue_reason", ""),
                continue_context=parsed.get("continue_context", ""),
                reason=parsed.get("intent_reason", ""),
                needs_analyzer=parsed.get("needs_analyzer", True),
                needs_analyzer_reason=parsed.get("needs_analyzer_reason", ""),
            ),
            interaction,
        )

    return (
        IntentClassifyResult(
            resolution=resolution,
            intent=intent,
            confidence=confidence,
            category=category,
            reason=(
                parsed.get("intent_reason", "")
                or parsed.get("continuity_reason", "")
            ),
            ambiguities=parsed.get("ambiguities", []),
            needs_analyzer=parsed.get("needs_analyzer", True),
            needs_analyzer_reason=parsed.get("needs_analyzer_reason", ""),
        ),
        interaction,
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

    # needs_analyzer: 본 서비스는 명세 추출이 주 업무이므로 analyzer는 opt-in.
    # LLM이 true(또는 "true"/"True"/"yes"/"1")를 명시 반환할 때만 True.
    # 필드 누락·빈 문자열·null은 모두 False (analyzer 스킵).
    # "false" 문자열은 Python에서 truthy이므로 문자열/비문자열을 구분하여 처리.
    raw_needs = intent_obj.get("needs_analyzer", False)
    if isinstance(raw_needs, str):
        needs_analyzer = raw_needs.strip().lower() in ("true", "1", "yes")
    else:
        needs_analyzer = bool(raw_needs)

    # 공통 — CONTINUE/NEW 무관하게 동일하게 채우는 intent 필드
    result["intent_reason"] = intent_obj.get("label_reason", "")
    result["needs_analyzer"] = needs_analyzer
    result["needs_analyzer_reason"] = intent_obj.get("needs_analyzer_reason", "")

    # 분기 — CONTINUE만 context 보존, 그 외는 continuity.reason 보존
    if resolution == HistoryDecision.CONTINUE:
        result["continue_reason"] = continuity.get("reason", "")
        result["continue_context"] = continuity.get("context", "")
    else:
        result["continuity_reason"] = continuity.get("reason", "")

    # UNSURE / AMBIGUOUS: LLM 생성 구조화 모호성
    ambiguities = data.get("ambiguities", [])
    if ambiguities:
        result["ambiguities"] = ambiguities

    return result
