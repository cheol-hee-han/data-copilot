"""대화 이력 기반 질의 맥락 해소 서비스.

이전 대화를 참조하는 후속 질의(follow-up)와 명확화 응답을 감지하고,
LLM을 호출하여 3가지 판정(CONTINUE/NEW/UNSURE)을 수행한다.

통합 처리 범위:
    1. 후속 질의 감지 + 재작성 ("그 중에서 VIP는?" → 이전 맥락 병합)
    2. 명확화 응답 판단 ("2번" → 명확화 답변인지, 새 질의인지)
    3. 모호한 맥락 감지 (중간에 인사 턴이 끼어 맥락이 불확실할 때)

핵심 함수:
    - resolve_history: 감지 → LLM 판정 → 결과 반환 전체 파이프라인
    - needs_history_resolve: 규칙 기반 후속 질의 감지 (LLM 호출 전 게이트)
    - parse_decision: LLM 응답에서 DECISION/QUERY 파싱

프롬프트는 호출하는 노드에서 인자로 주입받아,
프롬프트 변경이 서비스 코드 수정 없이 가능하도록 설계되었다.
"""

from __future__ import annotations

import re
import json
from dataclasses import dataclass
from enum import Enum

from src.config import settings
from src.utils.llm import ParseError, llm_call_with_parse_retry
from src.utils.tracker import record_prompt_variables
from src.utils.logger import get_logger

logger = get_logger(__name__)


class HistoryDecision(str, Enum):
    """대화 이력 해소 판정."""

    CONTINUE = "CONTINUE"  # 이전 맥락 이어짐 → 재작성
    NEW = "NEW"  # 새 독립 질의 → 원본 유지
    UNSURE = "UNSURE"  # 불확실 → 명확화 질문
    SKIP = "SKIP"  # LLM 호출 없이 통과 (이력 없음 등)


@dataclass
class HistoryResolveResult:
    """대화 이력 해소 결과."""

    decision: HistoryDecision
    resolved_query: str
    reason: str = ""


# ── 규칙 기반 감지 패턴 ──

# 지시대명사/참조 표현
_REFERENCE_PATTERNS = re.compile(
    r"그\s|거기서|거기|아까|위에서|그거|그것|그건|그게|그걸|이것|저것"
    r"|방금|그중|그\s?중에서|여기서",
)
# 추가/수정/제외 표현
_MODIFY_PATTERNS = re.compile(
    r"추가로|더\s|빼고|대신|말고|바꿔|제외|포함|변경|수정"
    r"|넣어|빼줘|추가해|제거|바꿔줘|고쳐"
    r"|나눠|나눠서|분류해|분류해서|합쳐|합쳐서",
)
# 짧은 입력 기준
_SHORT_INPUT_THRESHOLD = 10

# 명확화 답변 패턴 (번호 선택, 짧은 응답)
_CLARIFICATION_ANSWER_RE = re.compile(
    r"^[1-9]번?$|^[1-9]\)$|^[1-9]$",
)


def needs_history_resolve(
    query: str,
    conversation_history: list[dict[str, str]],
    awaiting_clarification: bool = False,
) -> tuple[bool, str]:
    """규칙 기반으로 LLM 호출이 필요한지 판단한다.

    Returns:
        (LLM 호출 필요 여부, 감지 사유)
    """
    # 이력도 없고 명확화 대기도 아니면 스킵
    if not conversation_history and not awaiting_clarification:
        return False, ""

    # 이력이 있거나 명확화 대기 중이면 항상 LLM 판단 필요
    # — 규칙 기반으로 놓치는 케이스(독립 질의를 SKIP하거나,
    #   패턴에 없는 후속 표현을 놓치는 것)를 방지
    # — LLM이 NEW로 판단하면 원본 유지이므로 부작용 없음

    # 감지 사유를 구체적으로 기록 (디버깅용)
    if awaiting_clarification:
        reason = "명확화 응답 판단 필요"
    elif _REFERENCE_PATTERNS.search(query):
        reason = "지시대명사/참조 표현 감지"
    elif _MODIFY_PATTERNS.search(query):
        reason = "추가/수정/제외 표현 감지"
    elif _CLARIFICATION_ANSWER_RE.match(query.strip()):
        reason = "번호 선택 패턴 감지"
    elif len(query.strip()) <= _SHORT_INPUT_THRESHOLD:
        reason = f"짧은 입력({len(query.strip())}자)"
    else:
        reason = "이력 존재 — LLM 맥락 판단"

    return True, reason


def _format_history(
    conversation_history: list[dict[str, str]],
    max_turns: int = 4,
) -> str:
    """대화 이력을 프롬프트 주입용 텍스트로 포맷팅한다."""
    recent = conversation_history[-max_turns:]
    lines: list[str] = []
    for turn in recent:
        role = "사용자" if turn["role"] == "user" else "시스템"
        content = turn["content"]
        if role == "시스템" and len(content) > 200:
            content = content[:200] + "..."
        lines.append(f"  {role}: {content}")
    return "\n".join(lines)


def parse_decision(text: str) -> tuple[HistoryDecision, str]:
    """LLM 응답 JSON에서 decision과 query를 파싱한다.

    코드 펜스(```json ... ```)가 포함되어 있으면 자동 제거한다.
    파싱 실패 시 ValueError를 발생시켜 llm_call_with_parse_retry가
    자동 재시도할 수 있게 한다.
    """
    cleaned = re.sub(r"```(?:json)?\s*", "", text)
    cleaned = cleaned.replace("```", "").strip()

    parsed = json.loads(cleaned)

    raw_decision = parsed.get("decision", "").upper()
    try:
        decision = HistoryDecision(raw_decision)
    except ValueError:
        raise ValueError(
            f"허용되지 않는 decision 값: {raw_decision}"
        )

    query = parsed.get("query", "")
    return decision, query


def _find_last_data_query(
    conversation_history: list[dict[str, str]],
) -> str:
    """이력에서 마지막 데이터 관련 사용자 질의를 찾는다.

    UNSURE 시 명확화 질문 조립에 사용한다.
    """
    for turn in reversed(conversation_history):
        if turn["role"] != "user":
            continue
        content = turn["content"]
        # 인사/잡담이 아닌 데이터 질의인지 간단히 판단
        if len(content) > 5 and any(
            kw in content
            for kw in (
                "알려", "보여", "뽑아", "조회", "현황", "건수",
                "잔액", "실적", "추이", "분석", "비교", "고객",
                "대출", "여신", "수신", "예금", "카드", "지점",
            )
        ):
            return content
    return ""


def build_unsure_clarification(
    conversation_history: list[dict[str, str]],
) -> str:
    """UNSURE 판정 시 맥락 인지형 명확화 질문을 조립한다."""
    last_data_query = _find_last_data_query(conversation_history)

    if last_data_query:
        return (
            f"혹시 이전에 대화했던 '{last_data_query[:40]}'에 "
            f"이어서 질문하신 건가요?\n"
            f"1) 네, 이전 내용에 이어서 진행해주세요\n"
            f"2) 아니요, 새로운 데이터를 찾고 있어요\n"
            f"3) 직접 입력할게요"
        )
    return (
        "이전 대화에 이어서 질문하신 건지, "
        "새로운 데이터를 찾으시는 건지 알려주시겠어요?\n"
        "1) 이전 대화에 이어서 진행\n"
        "2) 새로운 데이터 요청\n"
        "3) 직접 입력할게요"
    )


async def resolve_history(
    query: str,
    conversation_history: list[dict[str, str]],
    *,
    system_prompt: str,
    user_template: str,
    awaiting_clarification: bool = False,
) -> HistoryResolveResult:
    """대화 이력 해소 전체 파이프라인.

    1. 규칙 기반으로 LLM 호출 필요 여부 판단
    2. LLM에게 JSON으로 판정(CONTINUE/NEW/UNSURE) + query 요청
    3. 파싱 실패 시 자동 재시도
    4. 결과 반환
    """
    needs, reason = needs_history_resolve(
        query, conversation_history, awaiting_clarification,
    )

    if not needs:
        return HistoryResolveResult(
            decision=HistoryDecision.SKIP,
            resolved_query=query,
            reason="독립 질의 (LLM 호출 불필요)",
        )

    logger.info("이력 해소 LLM 판정 시작", reason=reason)

    history_text = _format_history(conversation_history)
    user_prompt = user_template.format(
        history=history_text,
        query=query,
    )

    try:
        _, (decision, resolved_query) = (
            await llm_call_with_parse_retry(
                system=system_prompt,
                messages=[
                    {"role": "user", "content": user_prompt},
                ],
                parse_fn=parse_decision,
                max_tokens=settings.llm_default_max_tokens,
                timeout=settings.llm_default_timeout,
                node_name="이력해소",
            )
        )
        record_prompt_variables({
            "query": query,
            "history": history_text[:300] + "..." if len(history_text) > 300 else history_text,
        })
    except ParseError as e:
        logger.warning(
            "이력 해소 JSON 파싱 최종 실패, NEW로 폴백",
            last_response=e.last_response[:100],
        )
        return HistoryResolveResult(
            decision=HistoryDecision.NEW,
            resolved_query=query,
            reason="JSON 파싱 실패 → NEW 폴백",
        )
    except Exception as e:
        logger.error(
            "이력 해소 LLM 호출 실패, NEW로 폴백",
            error=str(e),
        )
        return HistoryResolveResult(
            decision=HistoryDecision.NEW,
            resolved_query=query,
            reason=f"LLM 실패: {e}",
        )

    # 빈 query 방어
    if not resolved_query:
        resolved_query = query

    logger.info(
        "이력 해소 판정 완료",
        decision=decision.value,
        original=query[:50],
        resolved=resolved_query[:50],
    )

    return HistoryResolveResult(
        decision=decision,
        resolved_query=resolved_query,
        reason=reason,
    )
