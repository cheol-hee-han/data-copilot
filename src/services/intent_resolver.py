"""사용자 질의의 의도를 분류하는 서비스.

사용자의 자연어 질의가 데이터 추출/분석 요청인지, 일반 대화인지, 메타 질문인지를
판별하여 파이프라인의 분기를 결정하는 게이트 역할을 한다.
두 가지 분류 경로를 제공한다:
  1. Intent Gate 경로 — 6개 카테고리(DATA_EXTRACTION, DATA_ANALYSIS,
     CASUAL_TALK, META_QUESTION, CLARIFICATION, AMBIGUOUS)로 LLM이 직접 분류한다.
     기존 DATA_QUERY는 하위 호환을 위해 DATA_EXTRACTION으로 매핑된다.
  2. Legacy 경로 — 정규화 비활성화 시 사용하며, llm_call_with_parse_retry를
     통해 "INTENT:/CONFIDENCE:" 형식의 텍스트 응답을 파싱한다.

프롬프트(시스템/유저 템플릿)는 호출하는 노드에서 인자로 주입받아,
프롬프트 변경이 서비스 코드 수정 없이 가능하도록 설계되었다.

핵심 함수:
    - classify_with_gate: Intent Gate LLM 호출 → JSON 파싱 → 세분류까지의 메인 경로
    - classify_legacy: 기존 텍스트 파싱 기반 의도 분류 (폴백/호환용)
    - subclassify_data_query: DATA_QUERY를 분석 신호어 기반으로 추출/분석 세분류
    - get_intent_label: IntentType을 한국어 라벨로 변환

fallback 전략: Intent Gate JSON 파싱 실패 시 AMBIGUOUS로 보정하여 명확화 질문을 유도한다.
Legacy 경로에서 파싱 최종 실패 시 UNKNOWN(confidence=0.0)을 반환한다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from src.agents.models.normalization import VALID_QUERY_CATEGORIES
from src.config import settings
from src.models.enums import IntentType
from src.utils.llm import ParseError, get_llm_client, llm_call_with_parse_retry
from src.utils.tracker import record_prompt_variables
from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class IntentResult:
    """의도 분류 결과."""

    intent: IntentType
    confidence: float
    category: str = ""
    reason: str = ""
    is_error: bool = False
    error_message: str = ""


# ── Intent Gate ──


async def classify_with_gate(
    query: str,
    *,
    system_prompt: str,
    user_template: str,
) -> IntentResult:
    """Intent Gate를 사용하여 5개 카테고리로 분류한다.

    Args:
        query: 전처리된 사용자 입력.
        system_prompt: Intent Gate 시스템 프롬프트.
        user_template: 유저 프롬프트 템플릿 ({query} 플레이스홀더).
    """
    try:
        client = get_llm_client()
        user_prompt = user_template.format(query=query)
        response = await client.messages.create(
            model=settings.llm_model,
            max_tokens=settings.llm_default_max_tokens,
            timeout=settings.llm_default_timeout,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )

        record_prompt_variables({"query": query})

        if not response.content:
            raise ValueError("Intent Gate 응답이 비어있습니다")

        raw = response.content[0].text
        gate_result = _parse_gate_response(raw)
        category = gate_result.get("category", "AMBIGUOUS")
        confidence_str = gate_result.get("confidence", "MEDIUM")
        reason = gate_result.get("reason", "")

    except Exception as e:
        logger.error("Intent Gate 호출 실패", error=str(e))
        return IntentResult(
            intent=IntentType.UNKNOWN,
            confidence=0.0,
            is_error=True,
            error_message=str(e),
        )

    intent, confidence = _map_category_to_intent(
        category, confidence_str,
    )

    return IntentResult(
        intent=intent,
        confidence=confidence,
        category=category,
        reason=reason,
    )


async def classify_legacy(
    query: str,
    *,
    system_prompt: str,
) -> IntentResult:
    """기존 의도 분류 로직 (정규화 비활성화 시 사용).

    Args:
        query: 전처리된 사용자 입력.
        system_prompt: 의도 분류 시스템 프롬프트.
    """
    try:
        _, (intent, confidence) = await llm_call_with_parse_retry(
            system=system_prompt,
            messages=[{"role": "user", "content": query}],
            parse_fn=_parse_intent_response,
            max_tokens=50,
            timeout=settings.llm_default_timeout,
            node_name="의도분류",
        )
    except ParseError as e:
        logger.error(
            "의도 분류 포맷 파싱 최종 실패",
            last_response=e.last_response,
        )
        return IntentResult(
            intent=IntentType.UNKNOWN,
            confidence=0.0,
        )
    except Exception as e:
        logger.error("의도 분류 LLM 호출 오류", error=str(e))
        return IntentResult(
            intent=IntentType.UNKNOWN,
            confidence=0.0,
            is_error=True,
            error_message=str(e),
        )

    return IntentResult(intent=intent, confidence=confidence)


# ── 내부 함수 ──


def _parse_gate_response(raw: str) -> dict:
    """Intent Gate LLM 응답을 JSON으로 파싱한다."""
    cleaned = re.sub(r"```(?:json)?\s*", "", raw)
    cleaned = cleaned.replace("```", "").strip()
    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.error("Intent Gate JSON 파싱 실패")
        return {
            "category": "AMBIGUOUS",
            "confidence": "LOW",
            "reason": "Intent Gate 응답 파싱 실패",
        }

    cat = result.get("category", "").upper()
    if cat not in VALID_QUERY_CATEGORIES:
        logger.warning(
            "미인식 카테고리 → AMBIGUOUS 보정",
            original=cat,
        )
        result["category"] = "AMBIGUOUS"

    return result


def _map_category_to_intent(
    category: str,
    confidence_str: str,
) -> tuple[IntentType, float]:
    """Intent Gate 카테고리를 IntentType으로 매핑한다."""
    conf_map = {"HIGH": 0.95, "MEDIUM": 0.7, "LOW": 0.4}
    confidence = conf_map.get(confidence_str, 0.5)

    category_intent_map = {
        "DATA_EXTRACTION": IntentType.DATA_EXTRACTION,
        "DATA_ANALYSIS": IntentType.DATA_ANALYSIS,
        "DATA_QUERY": IntentType.DATA_EXTRACTION,  # 하위 호환
        "CASUAL_TALK": IntentType.CASUAL_TALK,
        "META_QUESTION": IntentType.META_QUESTION,
        "CLARIFICATION": IntentType.CLARIFICATION_NEEDED,
        "AMBIGUOUS": IntentType.CLARIFICATION_NEEDED,
    }
    intent = category_intent_map.get(
        category, IntentType.CLARIFICATION_NEEDED,
    )
    return intent, confidence


def subclassify_data_query(
    query: str,
    base_confidence: float,
) -> tuple[IntentType, float]:
    """DATA_QUERY를 DATA_EXTRACTION / DATA_ANALYSIS로 세분류한다."""
    analysis_signals = {
        "분석", "추이", "트렌드", "비교", "대비",
        "증감", "변화", "통계", "상관", "예측",
    }
    if any(signal in query for signal in analysis_signals):
        return IntentType.DATA_ANALYSIS, base_confidence

    return IntentType.DATA_EXTRACTION, base_confidence


def _parse_intent_response(
    text: str,
) -> tuple[IntentType, float]:
    """LLM 응답에서 의도와 신뢰도를 파싱한다."""
    intent: IntentType | None = None
    confidence: float | None = None

    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("INTENT:"):
            intent_str = line.replace("INTENT:", "").strip().lower()
            try:
                intent = IntentType(intent_str)
            except ValueError:
                pass
        elif line.startswith("CONFIDENCE:"):
            try:
                confidence = float(
                    line.replace("CONFIDENCE:", "").strip(),
                )
                confidence = max(0.0, min(1.0, confidence))
            except ValueError:
                pass

    if intent is None:
        raise ValueError(
            f"INTENT 행을 파싱할 수 없음: {text[:100]}"
        )

    if confidence is None:
        confidence = 0.5

    return intent, confidence


def get_intent_label(intent: IntentType) -> str:
    """IntentType을 한국어 라벨로 변환한다."""
    labels = {
        IntentType.DATA_EXTRACTION: "데이터 추출",
        IntentType.DATA_ANALYSIS: "데이터 분석",
        IntentType.CLARIFICATION_NEEDED: "명확화 필요",
        IntentType.GENERAL_QUESTION: "일반 질문",
        IntentType.CASUAL_TALK: "일반 대화",
        IntentType.META_QUESTION: "메타 질의",
    }
    return labels.get(intent, str(intent))
