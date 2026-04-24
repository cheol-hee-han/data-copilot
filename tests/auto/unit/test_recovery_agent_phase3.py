"""recovery_agent Phase 3 `{handoff_note}` + `{previous_sql}` 주입 단위 테스트
(§14.3.3 + §14.3.6).

테스트 대상:
    - `_build_prompt` 이 handoff_note·previous_sql·previous_sql_explanation
      3개 placeholder 를 치환
    - NEW 턴(handoff_note·previous_turn_sql 모두 "") → 전부 "(없음)"
    - CONTINUE 턴(REGENERATE 등) → handoff_note·previous_sql 본문 주입
    - 프롬프트 파일에 3개 placeholder 모두 존재

테스트 대상 소스:
    src/agents/nodes/reason/recovery_agent.py::_build_prompt
    resources/prompts/reason/recovery_agent_system.txt
"""

from __future__ import annotations

import inspect
from pathlib import Path

from src.agents.nodes.reason import recovery_agent as ra_mod
from src.agents.state.state import ReasoningState

_PROMPT_PATH = Path(
    "resources/prompts/reason/recovery_agent_system.txt",
)


class TestPromptSectionPresence:
    """프롬프트 파일에 previous_sql·handoff_note 섹션이 모두 있는지."""

    def test_prompt_has_previous_sql_section(self) -> None:
        text = _PROMPT_PATH.read_text(encoding="utf-8")
        assert "## 직전 턴 참고 SQL" in text
        assert "{previous_sql}" in text
        assert "{previous_sql_explanation}" in text
        assert "연속성 판단 근거, 실패 분석용 아님" in text

    def test_prompt_has_handoff_note_section(self) -> None:
        text = _PROMPT_PATH.read_text(encoding="utf-8")
        header = "## 연속 질의 오케스트레이터 지시 (참고용)"
        assert header in text
        assert "{handoff_note}" in text

    def test_previous_sql_section_precedes_handoff_note(self) -> None:
        """§14.3.3 지시 — previous_sql 섹션을 handoff_note 섹션 바로 앞에 배치."""
        text = _PROMPT_PATH.read_text(encoding="utf-8")
        prev_idx = text.index("## 직전 턴 참고 SQL")
        handoff_idx = text.index(
            "## 연속 질의 오케스트레이터 지시 (참고용)",
        )
        assert prev_idx < handoff_idx


class TestBuildPromptSignature:
    """`_build_prompt` 시그니처에 handoff_note 파라미터 전파."""

    def test_handoff_note_param_exists(self) -> None:
        sig = inspect.signature(ra_mod._build_prompt)
        assert "handoff_note" in sig.parameters
        param = sig.parameters["handoff_note"]
        assert param.default == ""


class TestBuildPromptRendering:
    """NEW 턴 / CONTINUE 턴 양 시나리오에서 치환 결과 검증."""

    def test_new_turn_renders_all_empty(self) -> None:
        """NEW 턴 — handoff_note·previous_turn_sql 모두 "" → "(없음)" 치환."""
        reason = ReasoningState()
        _, full_vars = ra_mod._build_prompt(reason)
        assert full_vars["handoff_note"] == "(없음)"
        assert full_vars["previous_sql"] == "(없음)"
        assert full_vars["previous_sql_explanation"] == "(없음)"

    def test_continue_turn_renders_handoff_note(self) -> None:
        """CONTINUE 턴 — 실제 값 치환."""
        reason = ReasoningState()
        reason.previous_turn_sql = (
            "SELECT branch, SUM(amt) FROM t GROUP BY branch"
        )
        reason.previous_turn_sql_explanation = "지점별 잔액 집계"
        _, full_vars = ra_mod._build_prompt(
            reason,
            handoff_note="### 연속 처리 의도\n상품별로 확장",
        )
        assert "### 연속 처리 의도" in full_vars["handoff_note"]
        assert "GROUP BY branch" in full_vars["previous_sql"]
        assert full_vars["previous_sql_explanation"] == "지점별 잔액 집계"

    def test_handoff_note_only_previous_sql_empty(self) -> None:
        """CONTINUE(REFINE) 시나리오에서 직전 SQL 이 없을 수도 있음."""
        reason = ReasoningState()
        _, full_vars = ra_mod._build_prompt(
            reason, handoff_note="### 연속 처리 의도\n재표현",
        )
        assert "재표현" in full_vars["handoff_note"]
        assert full_vars["previous_sql"] == "(없음)"
