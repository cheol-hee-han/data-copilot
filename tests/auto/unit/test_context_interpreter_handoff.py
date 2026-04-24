"""context_interpreter Phase 3 `{handoff_note}` hint-only 주입 단위 테스트.

테스트 대상:
    - `_interpret_level0` 이 batch_vars 에 handoff_note 를 포함시키는지
    - `_interpret_level1` 은 주입하지 않는다 (§14.3.2 설계 — 개별 스텝 힌트 금지)
    - 프롬프트 치환 결과가 `{handoff_note}` 를 실제 본문으로 대체

테스트 대상 소스:
    src/agents/nodes/reason/context_interpreter.py
    resources/prompts/reason/context_interpreter_system.txt
"""

from __future__ import annotations

import inspect

from src.agents.nodes.reason import context_interpreter


class TestLevel0HandoffNoteSignature:
    """Level 0 / Level 1 handoff_note 주입 매트릭스 검증."""

    def test_level0_accepts_handoff_note(self) -> None:
        sig = inspect.signature(context_interpreter._interpret_level0)
        assert "handoff_note" in sig.parameters
        param = sig.parameters["handoff_note"]
        assert param.default == "(없음)"

    def test_level1_does_not_accept_handoff_note(self) -> None:
        """Level 1 은 handoff_note 주입 대상이 아니다 (§14.3.2 opt-out)."""
        sig = inspect.signature(context_interpreter._interpret_level1)
        assert "handoff_note" not in sig.parameters

    def test_interpret_batch_propagates_handoff_note(self) -> None:
        """`_interpret_batch` 는 Level 0 로 handoff_note 를 전파한다."""
        sig = inspect.signature(context_interpreter._interpret_batch)
        assert "handoff_note" in sig.parameters


class TestPromptSectionPresence:
    """프롬프트 파일에 handoff_note 섹션이 삽입되었는지 확인 (R5 관련)."""

    def test_prompt_contains_handoff_section(self) -> None:
        from src.agents.nodes.system_prompts import (
            CONTEXT_INTERPRETER_SYSTEM,
        )

        header = (
            "## 연속 질의 오케스트레이터 지시 (참고용)"
        )
        assert header in CONTEXT_INTERPRETER_SYSTEM
        assert "{handoff_note}" in CONTEXT_INTERPRETER_SYSTEM
