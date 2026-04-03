"""명확화 컨텍스트 빌더 — resolved_signals를 LLM 프롬프트 섹션으로 변환.

명확화 후 복귀하는 모든 노드(readiness_gate, normalize_query,
sql_generator 등)가 LLM 프롬프트에 명확화 컨텍스트를 주입할 때 사용한다.

핵심 함수:
    - build_clarification_context: resolved_signals에서 ASK/INFER를
      분리하여 프롬프트 섹션 문자열을 구성
    - build_auto_resolved_notice: INFER 항목을 사용자 응답 상단에
      자연어로 안내하는 문자열을 구성
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.utils.logger import get_logger

if TYPE_CHECKING:
    from src.agents.state.state import PipelineState

logger = get_logger(__name__)


def build_clarification_context(state: PipelineState) -> str:
    """resolved_signals를 decision 기준으로 분리하여 프롬프트 섹션을 구성한다.

    복귀 노드의 LLM이 이 컨텍스트를 보고
    ReasoningState 상태 전환(CONFLICTED→CONFIRMED 등)을 재판단한다.

    현재 턴(turn_id)에 해당하는 시그널만 필터링한다.
    """
    tid = state.turn_id
    if not tid:
        logger.warning(
            "turn_id가 비어있음 — runner.py에서 UUID 생성 누락 가능성",
        )
        return ""

    lines: list[str] = []

    # ── ASK 시그널: 명확화 Q&A 쌍 (현재 턴만) ──
    asks = [
        s for s in state.resolved_signals
        if s.decision == "ASK"
        and s.turn_id is not None
        and s.turn_id == tid
    ]
    if asks:
        lines.append("[명확화 대화]")
        for i, s in enumerate(asks, 1):
            lines.append(f"라운드 {i}:")
            lines.append(f"  질문: {s.question}")
            if s.options:
                lines.append(
                    f"  선택지: {', '.join(s.options)}",
                )
            lines.append(f"  답변: {s.answer}")

    # ── INFER 시그널: 자동 추론 결과 (현재 턴만) ──
    infers = [
        s for s in state.resolved_signals
        if s.decision == "INFER"
        and s.turn_id is not None
        and s.turn_id == tid
    ]
    if infers:
        lines.append("\n[자동 추론된 조건]")
        for s in infers:
            lines.append(
                f"- {s.question} → {s.inferred_value} "
                f"(근거: {s.reasoning})",
            )

    logger.debug(
        "resolved_signals 필터",
        total=len(state.resolved_signals),
        current_turn_asks=len(asks),
        current_turn_infers=len(infers),
        turn_id=tid[:8],
    )

    return "\n".join(lines)


def build_auto_resolved_notice(
    state: PipelineState,
) -> str:
    """INFER 항목을 결과 상단에 자연어로 안내한다.

    DTE 패턴: "왜 이렇게 처리했는지" 근거를 포함한다.

    현재 턴(turn_id)에 해당하는 시그널만 필터링한다.
    """
    tid = state.turn_id
    if not tid:
        logger.warning(
            "turn_id가 비어있음 — runner.py에서 UUID 생성 누락 가능성",
        )
        return ""

    infers = [
        s for s in state.resolved_signals
        if s.decision == "INFER"
        and s.turn_id is not None
        and s.turn_id == tid
    ]
    if not infers:
        return ""

    lines = ["조회 기준 안내:"]
    for s in infers:
        lines.append(f"- {s.question} → {s.inferred_value}")
    lines.append("(다른 기준을 원하시면 말씀해 주세요)")
    return "\n".join(lines)
