"""R5 — NEW 턴 프롬프트 헤더 부재 회귀 방어 테스트 (§14.9.4).

NEW 턴 렌더링 시:
    - 정적 헤더 3종(`## 6. 연속 질의 오케스트레이터 지시` /
      `## 연속 질의 오케스트레이터 지시 (참고용)` /
      `## 직전 턴 참고 SQL`) 은 그대로 존재한다.
    - `{handoff_note}` · `{previous_sql}` 플레이스홀더는 모두 치환된다
      (`"(없음)"` 삽입).
    - handoff_note 본문 기원의 `###` 서브헤더 (연속 처리 의도 / SQL 생성 지시 /
      분석 초점 / 시각화/포맷 지시) 의 **등장 횟수가 정적 프롬프트 대비 증가하지
      않는다** — 즉 본문 주입으로 신규 서브헤더가 삽입되지 않음을 확인.

프롬프트 드리프트(Qwen 이 NEW 턴에서 handoff_note 본문을 환각해 서브헤더를
참고하는 시나리오) 를 조기 감지한다.

테스트 대상 소스:
    resources/prompts/interpret/query_normalizer_phase1_system.txt
    resources/prompts/reason/context_interpreter_system.txt
    resources/prompts/reason/sql_generator_system_{dialect}.txt
    resources/prompts/reason/recovery_agent_system.txt
"""

from __future__ import annotations

from src.agents.nodes.reason import recovery_agent as ra_mod
from src.agents.nodes.system_prompts import (
    CONTEXT_INTERPRETER_SYSTEM,
    QUERY_NORMALIZER_PHASE1_SYSTEM,
    get_sql_generator_system,
)
from src.agents.utils.handoff import normalize_handoff_note
from src.utils.llm.prompt import render_prompt

_SUB_HEADERS = (
    "### 연속 처리 의도",
    "### SQL 생성 지시",
    "### 분석 초점",
    "### 시각화/포맷 지시",
)


def _count_subheaders(text: str) -> dict[str, int]:
    return {sub: text.count(sub) for sub in _SUB_HEADERS}


def _assert_no_placeholder(rendered: str, placeholder: str) -> None:
    assert placeholder not in rendered, (
        f"placeholder {placeholder!r} 가 치환되지 않고 남아있다"
    )


def _assert_subheader_count_unchanged(
    static_prompt: str, rendered_prompt: str,
) -> None:
    """rendered prompt 의 서브헤더 카운트가 static prompt 와 동일해야 한다."""
    before = _count_subheaders(static_prompt)
    after = _count_subheaders(rendered_prompt)
    assert before == after, (
        f"NEW 턴에 서브헤더 추가 등장 — static={before}, rendered={after}"
    )


class TestQueryNormalizerPhase1:
    """Phase 1 normalizer NEW 턴 렌더링."""

    def test_new_turn_handoff_note_empty(self) -> None:
        static = QUERY_NORMALIZER_PHASE1_SYSTEM
        rendered, _ = render_prompt(
            static,
            {"{handoff_note}": normalize_handoff_note("")},
        )
        assert (
            "## 6. 연속 질의 오케스트레이터 지시"
            in rendered
        )
        _assert_no_placeholder(rendered, "{handoff_note}")
        assert "(없음)" in rendered
        _assert_subheader_count_unchanged(static, rendered)


class TestContextInterpreter:
    """context_interpreter Level 0 NEW 턴 렌더링."""

    def test_new_turn_handoff_note_empty(self) -> None:
        static = CONTEXT_INTERPRETER_SYSTEM
        rendered, _ = render_prompt(
            static,
            {"{handoff_note}": normalize_handoff_note("")},
        )
        header = (
            "## 연속 질의 오케스트레이터 지시 (참고용)"
        )
        assert header in rendered
        _assert_no_placeholder(rendered, "{handoff_note}")
        _assert_subheader_count_unchanged(static, rendered)


class TestSqlGenerator:
    """sql_generator 4-dialect NEW 턴 렌더링."""

    def test_new_turn_all_dialects(self) -> None:
        for dialect in ["tsql", "hive", "oracle", "postgres"]:
            static = get_sql_generator_system(dialect)
            rendered, _ = render_prompt(
                static,
                {
                    "{handoff_note}": normalize_handoff_note(""),
                    "{previous_sql}": "(없음)",
                    "{previous_sql_explanation}": "(없음)",
                },
            )
            assert (
                "## 직전 턴 참고 SQL" in rendered
            ), f"dialect={dialect}"
            assert (
                "## 연속 질의 오케스트레이터 지시"
                in rendered
            ), f"dialect={dialect}"
            _assert_no_placeholder(rendered, "{handoff_note}")
            _assert_no_placeholder(rendered, "{previous_sql}")
            _assert_no_placeholder(
                rendered, "{previous_sql_explanation}",
            )
            _assert_subheader_count_unchanged(static, rendered)


class TestRecoveryAgent:
    """recovery_agent NEW 턴 렌더링 (empty ReasoningState)."""

    def test_new_turn_empty_state(self) -> None:
        from src.agents.nodes.system_prompts import (
            RECOVERY_AGENT_SYSTEM,
        )
        from src.agents.state.state import ReasoningState

        reason = ReasoningState()
        rendered, _ = ra_mod._build_prompt(reason)
        assert "## 직전 턴 참고 SQL" in rendered
        assert (
            "## 연속 질의 오케스트레이터 지시 (참고용)"
            in rendered
        )
        _assert_no_placeholder(rendered, "{handoff_note}")
        _assert_no_placeholder(rendered, "{previous_sql}")
        _assert_no_placeholder(
            rendered, "{previous_sql_explanation}",
        )
        _assert_subheader_count_unchanged(
            RECOVERY_AGENT_SYSTEM, rendered,
        )
