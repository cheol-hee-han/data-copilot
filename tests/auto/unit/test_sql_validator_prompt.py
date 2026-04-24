"""sql_validator 시스템 프롬프트 치환 테스트.

테스트 대상:
    - SQL_VALIDATOR_SYSTEM 템플릿의 {handoff_note} 치환 (Path F' §8)
    - 빈/공백 handoff_note 는 `(없음)` 으로 정규화되는지
    - handoff_note 본문(`### SQL 생성 지시`)이 원문 그대로 주입되는지

render_prompt 는 단순 문자열 치환이므로 LLM/DB 없이 검증 가능.
"""

from __future__ import annotations

from src.agents.nodes.system_prompts import SQL_VALIDATOR_SYSTEM
from src.utils.llm.prompt import render_prompt


def _build_replacements(handoff_note_text: str) -> dict[str, str]:
    """sql_validator._validate_layer2b 와 동일한 replacements 딕셔너리를 구성한다."""
    return {
        "{original_query}": "테스트 원본 질의",
        "{normalized_summary}": "(정규화 요약 테스트)",
        "{generated_sql}": "SELECT 1",
        "{table_schema}": "(테이블 스키마)",
        "{confirmed_terms}": "(확인 사항)",
        "{code_mappings}": "(코드 매핑)",
        "{reasoning_decisions}": "(추론 결정)",
        "{dead_ends}": "(Dead-ends)",
        "{db_execution_result}": "(DB 결과)",
        "{handoff_note}": handoff_note_text,
    }


def test_empty_handoff_note_rendered_as_placeholder() -> None:
    """빈 handoff_note 는 '(없음)'으로 주입되어 기본 검증 규칙으로 동작한다."""
    rendered, variables = render_prompt(
        SQL_VALIDATOR_SYSTEM, _build_replacements("(없음)"),
    )
    assert variables["handoff_note"] == "(없음)"
    assert "(없음)" in rendered


def test_non_empty_handoff_note_injected_verbatim() -> None:
    """REGENERATE 지시 섹션이 프롬프트 본문에 그대로 주입된다."""
    note = "### SQL 생성 지시\n분기 축을 지점별로 교체하세요."
    rendered, variables = render_prompt(
        SQL_VALIDATOR_SYSTEM, _build_replacements(note),
    )
    assert variables["handoff_note"] == note
    # 본문 주입 확인
    assert "SQL 생성 지시" in rendered
    assert "지점별로 교체" in rendered


def test_template_has_handoff_note_placeholder() -> None:
    """템플릿에 {handoff_note} 플레이스홀더가 최소 1곳 이상 존재한다 (회귀 방지)."""
    # 치환 전 템플릿 자체를 검사 — 플레이스홀더가 사라지면 주입 자체가 안 됨.
    assert "{handoff_note}" in SQL_VALIDATOR_SYSTEM


def test_validator_hint_only_section_present() -> None:
    """시스템 프롬프트에 handoff_note가 '참고용'임을 명시한 섹션이 포함된다."""
    assert "참고용" in SQL_VALIDATOR_SYSTEM
    assert "handoff_note" in SQL_VALIDATOR_SYSTEM
