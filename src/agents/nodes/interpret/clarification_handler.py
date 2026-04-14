"""Unified Clarification 노드 — 모든 명확화의 단일 진입점.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

파이프라인의 모든 명확화(AmbiguitySignal)를 단일 노드에서 처리한다.
여러 노드(intent_classifier, normalize_query 등)가 생성한 시그널이
pending_signals에 누적되면, 이 노드가 한 곳에서 ASK/INFER를 판정한다.

핵심 전략:
    1. 가드레일 보정: INFER→ASK 단방향만 허용하여 안전 방향으로만 보정한다.
       금융 산출식(FORMULA) 등 추론 금지 영역은 무조건 ASK로 전환한다.
    2. 우선순위 선택: ASK 시그널이 여러 개일 때 의존 관계를 반영한
       _PRIORITY 맵에 따라 가장 먼저 해소해야 할 1개만 interrupt로 질문한다.
    3. LangGraph interrupt 규칙 준수: interrupt()는 노드 내에서
       항상 동일 순서로 1회만 호출해야 하므로, 조건 분기 후 단일 호출한다.

설계 결정:
    - ASK→INFER 역방향 보정을 금지하여 사용자 확인 없이 추론하는 위험을 방지한다.
    - interrupt 후 resume 시 validate_answer로 응답을 검증하되,
      검증 실패 시에도 원문을 그대로 사용하여 대화 흐름이 끊기지 않도록 한다.

LangGraph 공식 규칙:
    "interrupt calls should happen in the same order every time,
     and you should not conditionally skip interrupt calls within a node."
"""

from __future__ import annotations

from datetime import datetime

from langgraph.types import interrupt

from src.agents.models.clarification import (
    AmbiguitySignal,
    AmbiguityType,
    ConfidenceLevel,
    QuestionType,
)
from src.agents.state.state import PipelineState
from src.utils.logger import get_logger

logger = get_logger(__name__)


# ── 가드레일: INFER→ASK 단방향 보정 (인라인) ──


def _should_override_to_ask(
    signal: AmbiguitySignal,
    state: PipelineState,
) -> str | None:
    """INFER → ASK 보정이 필요하면 사유를 반환한다.

    ASK → INFER 변환은 절대 없음 (안전 방향만 보정).
    LLM 호출 0 — 순수 규칙.
    """
    if signal.decision == "ASK":
        return None
    match signal.ambiguity_type:
        case AmbiguityType.FORMULA:
            return "산출식 관련 모호함은 추론 금지 (금융 규제)"
        case AmbiguityType.TABLE if (
            len(signal.options) >= 2
            and signal.confidence == ConfidenceLevel.LOW
        ):
            return "테이블 선택 확신도 부족"
        case AmbiguityType.INTENT if (
            signal.confidence == ConfidenceLevel.LOW
        ):
            return "의도 판정 확신도 부족"
    return None


# ── ASK 시그널 우선순위 (의존 관계 반영) ──

_PRIORITY: dict[AmbiguityType, int] = {
    AmbiguityType.INTENT: 1,
    AmbiguityType.FORMULA: 1,
    AmbiguityType.TABLE: 2,
    AmbiguityType.VALUE: 2,
    AmbiguityType.TIMEFRAME: 3,
    AmbiguityType.CONTEXT: 4,
    AmbiguityType.CONFLICT: 4,
}


# ── 응답 검증 ──


def validate_answer(
    answer: str,
    signal: AmbiguitySignal,
) -> str:
    """사용자 응답을 검증한다. question_type 기반 2가지 분기."""
    answer = answer.strip()
    if not answer:
        raise ValueError("응답이 비어있습니다.")

    if (
        signal.question_type == QuestionType.SINGLE_SELECT
        and signal.options
    ):
        for i, opt in enumerate(signal.options, 1):
            if answer == str(i) or answer == opt:
                return opt
        raise ValueError(
            f"선택지 중에서 골라주세요: "
            f"{', '.join(f'{i}) {o}' for i, o in enumerate(signal.options, 1))}"
        )
    return answer


# ── interrupt 페이로드에 포함할 필드 ──

_INTERRUPT_FIELDS = {
    "question",
    "question_type",
    "options",
    "ambiguity_type",
    "source_node",
}


# ── 통합 명확화 노드 ──


async def clarification_handler_node(
    state: PipelineState,
) -> dict:
    """통합 명확화 노드 — 가드레일 적용 → ASK/INFER 분리 → interrupt 또는 진행."""
    signals = state.pending_signals
    if not signals:
        return {}

    # 0. 턴 ID 주입 — 모든 시그널에 일괄 적용 (가드레일 보정 전)
    # NOTE: in-place mutation 패턴 (기존 가드레일과 동일).
    #       AmbiguitySignal에 frozen=True 설정 시 이 코드가 깨지므로 주의.
    for s in signals:
        s.turn_id = state.turn_id

    # 1. 가드레일 적용 (인라인)
    for s in signals:
        override = _should_override_to_ask(s, state)
        if override:
            logger.info(
                "가드레일 보정: INFER→ASK",
                ambiguity_type=s.ambiguity_type,
                reason=override,
            )
            s.decision = "ASK"
            s.override_reason = override

    ask = [s for s in signals if s.decision == "ASK"]
    infer = [s for s in signals if s.decision == "INFER"]

    # 2. INFER — 이미 resolved 상태, 그대로 누적
    for s in infer:
        s.resolved_at = datetime.now()

    if not ask:
        return {
            "resolved_signals": [*state.resolved_signals, *infer],
            "pending_signals": [],  # 덮어쓰기로 비움
        }

    # 3. ASK — 우선순위 1개 선택 → interrupt
    best = min(
        ask,
        key=lambda s: _PRIORITY.get(s.ambiguity_type, 99),
    )

    logger.info(
        "명확화 interrupt 발생",
        source_node=best.source_node,
        ambiguity_type=best.ambiguity_type,
    )

    user_answer = interrupt(
        best.model_dump(include=_INTERRUPT_FIELDS),
    )

    # resume 후: 검증 (실패 시 원문을 그대로 사용)
    try:
        best.answer = validate_answer(user_answer, best)
    except ValueError as e:
        logger.warning(
            "응답 검증 실패, 원문 사용",
            error=str(e),
        )
        best.answer = str(user_answer).strip()
    best.resolved_at = datetime.now()

    return {
        "resolved_signals": [*state.resolved_signals, *infer, best],
        "pending_signals": [],
    }


# 하위 호환 별칭
clarify_unified_node = clarification_handler_node
