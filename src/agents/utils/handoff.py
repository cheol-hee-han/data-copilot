"""handoff_note 프롬프트 주입 공용 유틸.

작성자: 한철희 / 최종수정: 2026-04-20 16:27:03

CONTINUE 오케스트레이터가 생성한 `state.handoff_note` 를 consumer LLM 프롬프트의
`{handoff_note}` 플레이스홀더에 주입할 때, 빈 값 표기 규칙을 단일화한다.

consumer opt-in 단일 패턴(설계 §4·§7):
    - 비어있거나 공백만 있으면 `(없음)` 으로 치환
    - 내용이 있으면 trim 후 그대로 전달

이 규칙이 여러 노드(sql_generator, sql_validator, visualizer, analyzer)에
inline 되면서 drift 위험이 있어 단일 함수로 추출.
"""

from __future__ import annotations

HANDOFF_NOTE_EMPTY_PLACEHOLDER = "(없음)"
PREVIOUS_SQL_EMPTY_PLACEHOLDER = "(없음)"


def normalize_handoff_note(note: str | None) -> str:
    """handoff_note 를 프롬프트 주입 가능한 문자열로 정규화한다.

    Args:
        note: PipelineState.handoff_note 또는 파라미터로 전달된 값.

    Returns:
        내용이 있으면 strip 한 본문, 비어있으면 `(없음)`.
    """
    return (note or "").strip() or HANDOFF_NOTE_EMPTY_PLACEHOLDER


def normalize_previous_sql(value: str | None) -> str:
    """직전 턴 참고 SQL 또는 그 설명을 프롬프트 주입 가능한 문자열로 정규화한다.

    SQL 본문과 설명 모두 동일한 정규화 규칙을 적용하므로 단일 함수로 통합한다(중복 금지).
    sql_generator / recovery_agent 가 `{previous_sql}` / `{previous_sql_explanation}`
    플레이스홀더를 치환할 때 공용으로 호출한다(설계 §14.3.6).

    Args:
        value: ReasoningState.previous_turn_sql 또는 previous_turn_sql_explanation.

    Returns:
        내용이 있으면 strip 한 본문, 비어있으면 `(없음)`.
    """
    return (value or "").strip() or PREVIOUS_SQL_EMPTY_PLACEHOLDER
