"""명확화 컨텍스트 빌더 — resolved_signals를 LLM 프롬프트 섹션으로 변환.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

명확화(interrupt/resume) 사이클 후 복귀하는 모든 노드(readiness_gate,
normalize_query, sql_generator 등)가 동일한 형식으로 명확화 결과를
LLM 프롬프트에 주입해야 하므로, 이 로직을 별도 유틸리티로 분리하였다.
노드마다 개별 구현하면 형식 불일치와 중복이 발생하기 때문이다.

핵심 전략:
    - ASK 시그널(사용자 직접 응답)과 INFER 시그널(자동 추론)을
      별도 섹션으로 분리하여 LLM이 확정/추론 정보를 구분할 수 있게 한다.
    - turn_id 기반 필터링으로 현재 턴의 시그널만 포함하여
      이전 턴의 명확화가 혼입되지 않도록 한다.

핵심 함수:
    - build_clarification_context: resolved_signals에서 ASK/INFER를
      분리하여 프롬프트 섹션 문자열을 구성
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


