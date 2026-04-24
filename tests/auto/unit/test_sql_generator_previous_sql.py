"""sql_generator `{previous_sql}` 주입 단위 테스트 (Phase 3 §14.3.6).

테스트 대상:
    - 5개 프롬프트 파일(generic + 4 dialect) 모두 `{previous_sql}`·
      `{previous_sql_explanation}` placeholder 및 "## 직전 턴 참고 SQL" 섹션 포함
    - previous_sql 섹션이 handoff_note 섹션 앞에 배치 (§14.3.6 인접 규칙)
    - `normalize_previous_sql` 통합 함수 — 빈값/정상 SQL 모두 재사용

테스트 대상 소스:
    src/agents/nodes/reason/sql_generator.py::_build_prompt
    resources/prompts/reason/sql_generator_system*.txt
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.agents.nodes.reason import sql_generator as sg_mod

_PROMPT_DIR = Path("resources/prompts/reason")
_PROMPT_FILES = [
    "sql_generator_system.txt",
    "sql_generator_system_sybase_iq.txt",
    "sql_generator_system_impala.txt",
    "sql_generator_system_oracle.txt",
    "sql_generator_system_postgres.txt",
]


class TestPromptPlaceholders:
    """5개 SQL 생성기 프롬프트에 placeholder 가 모두 존재하는지 검증."""

    @pytest.mark.parametrize("prompt_file", _PROMPT_FILES)
    def test_prompt_has_previous_sql_placeholders(
        self, prompt_file: str,
    ) -> None:
        prompt = (_PROMPT_DIR / prompt_file).read_text(encoding="utf-8")
        assert "{previous_sql}" in prompt
        assert "{previous_sql_explanation}" in prompt
        assert "## 직전 턴 참고 SQL" in prompt
        assert "복사 금지" in prompt

    @pytest.mark.parametrize("prompt_file", _PROMPT_FILES)
    def test_previous_sql_section_precedes_handoff_note(
        self, prompt_file: str,
    ) -> None:
        """§14.3.6 배치 — previous_sql 섹션이 handoff_note 섹션 앞에 온다."""
        prompt = (_PROMPT_DIR / prompt_file).read_text(encoding="utf-8")
        prev_idx = prompt.index("## 직전 턴 참고 SQL")
        handoff_idx = prompt.index(
            "## 연속 질의 오케스트레이터 지시",
        )
        assert prev_idx < handoff_idx


class TestNormalizationInSqlGenerator:
    """sql_generator 모듈 네임스페이스에서 normalize_previous_sql 재노출 확인."""

    def test_new_turn_empty_renders_placeholder(self) -> None:
        """NEW 턴은 previous_turn_sql = "" → "(없음)" 치환."""
        assert sg_mod.normalize_previous_sql("") == "(없음)"

    def test_continue_turn_renders_actual_sql(self) -> None:
        """CONTINUE 턴은 직전 턴 SQL 본문을 그대로 치환."""
        sql_body = "SELECT a FROM t WHERE d = '2026-01-01'"
        assert sg_mod.normalize_previous_sql(sql_body) == sql_body

    def test_continue_turn_renders_explanation(self) -> None:
        """SQL 설명도 동일 함수로 정규화 (중복 함수 없음)."""
        exp = "지점별 여신 잔액 집계"
        assert sg_mod.normalize_previous_sql(exp) == exp
