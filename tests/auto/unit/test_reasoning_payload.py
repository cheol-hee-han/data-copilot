"""LLM 노드용 REASONING_STEP payload 빌더 단위 테스트.

작성자: 한철희 / 최종수정: 2026-04-22

``LLMInteraction`` / ``build_llm_reasoning_payload`` 가 프롬프트 [INPUT]/
[OUTPUT_CONTRACT] 원본을 손실 없이 payload 에 실어 보내는지 검증한다.
"""

from __future__ import annotations

import pytest

from src.utils.tracker.reasoning_payload import (
    LLMInteraction,
    build_llm_reasoning_payload,
)


class TestLLMInteraction:
    """LLMInteraction 데이터클래스 동작."""

    def test_defaults(self) -> None:
        """기본값은 빈 dict/빈 문자열."""
        interaction = LLMInteraction()
        assert interaction.prompt_variables == {}
        assert interaction.raw_response == ""

    def test_preserves_nested_variables(self) -> None:
        """중첩 dict·list 값도 그대로 보존한다."""
        variables = {
            "tool_results": "### [Step 1] ...",
            "unresolved_items": "(K1) measure:수익률 — UNRESOLVED",
            "filters": [{"target": "수익률", "values": ["0"]}],
        }
        interaction = LLMInteraction(
            prompt_variables=variables,
            raw_response='{"intent": "data_extraction"}',
        )
        assert interaction.prompt_variables == variables
        assert interaction.raw_response == '{"intent": "data_extraction"}'


class TestBuildLLMReasoningPayload:
    """build_llm_reasoning_payload 스키마."""

    def test_minimal_payload(self) -> None:
        """필수 필드만 전달했을 때 기본 구조."""
        interaction = LLMInteraction(
            prompt_variables={"query": "모든 거래"},
            raw_response='{"intent": "data_extraction"}',
        )
        payload = build_llm_reasoning_payload(
            node="intent_classifier",
            phase="interpret",
            round=0,
            hypothesis_id="",
            interaction=interaction,
            routing={"next_node": "query_normalizer", "reason": "NEW"},
        )
        assert payload["node"] == "intent_classifier"
        assert payload["phase"] == "interpret"
        assert payload["step_type"] == "llm_decision"
        assert payload["round"] == 0
        assert payload["hypothesis_id"] == ""
        assert payload["inputs"] == {
            "prompt_variables": {"query": "모든 거래"},
        }
        assert payload["output"] == {
            "raw_response": '{"intent": "data_extraction"}',
        }
        assert payload["routing"]["next_node"] == "query_normalizer"

    def test_prompt_variables_exact_copy(self) -> None:
        """prompt_variables 가 render_prompt 반환값과 동일하게 보존된다."""
        variables = {
            "tool_results": "### [Step 1] search_use_cases(...)",
            "unresolved_items": "(K1) measure:수익률 — UNRESOLVED",
            "handoff_note": "(없음)",
        }
        interaction = LLMInteraction(
            prompt_variables=variables,
            raw_response="",
        )
        payload = build_llm_reasoning_payload(
            node="context_interpreter",
            phase="reason",
            round=1,
            hypothesis_id="H1",
            interaction=interaction,
            routing={"next_node": "readiness_gate", "reason": "done"},
        )
        assert payload["inputs"]["prompt_variables"] == variables

    def test_parsed_summary_included(self) -> None:
        """parsed_summary 가 output.parsed 에 담긴다."""
        interaction = LLMInteraction(
            prompt_variables={"q": "x"},
            raw_response="{}",
        )
        payload = build_llm_reasoning_payload(
            node="query_normalizer",
            phase="interpret",
            round=0,
            hypothesis_id="",
            interaction=interaction,
            routing={"next_node": "reasoning_preparer", "reason": "완료"},
            parsed_summary={"intent": "EXTRACT", "ambiguity_count": 0},
        )
        assert payload["output"]["parsed"] == {
            "intent": "EXTRACT",
            "ambiguity_count": 0,
        }

    def test_extra_inputs_merged(self) -> None:
        """extra_inputs 는 prompt_variables 와 병렬로 inputs 에 들어간다."""
        interaction = LLMInteraction(
            prompt_variables={"query": "x"},
            raw_response="{}",
        )
        payload = build_llm_reasoning_payload(
            node="sql_generator",
            phase="reason",
            round=0,
            hypothesis_id="H1",
            interaction=interaction,
            routing={"next_node": "sql_validator", "reason": ""},
            extra_inputs={"dialect": "postgres", "attempt": 2},
        )
        assert payload["inputs"]["prompt_variables"] == {"query": "x"}
        assert payload["inputs"]["dialect"] == "postgres"
        assert payload["inputs"]["attempt"] == 2

    def test_extra_inputs_key_conflict_raises(self) -> None:
        """extra_inputs 가 prompt_variables 와 키가 겹치면 ValueError."""
        interaction = LLMInteraction(
            prompt_variables={"query": "x", "dialect": "postgres"},
            raw_response="{}",
        )
        with pytest.raises(ValueError, match="dialect"):
            build_llm_reasoning_payload(
                node="sql_generator",
                phase="reason",
                round=0,
                hypothesis_id="",
                interaction=interaction,
                routing={"next_node": "", "reason": ""},
                extra_inputs={"dialect": "oracle"},
            )

    def test_step_type_override(self) -> None:
        """recovery_agent 같은 특수 노드는 step_type 을 바꿀 수 있다."""
        interaction = LLMInteraction(
            prompt_variables={"unresolved_items": "(K1)"},
            raw_response='{"action": "give_up"}',
        )
        payload = build_llm_reasoning_payload(
            node="recovery_agent",
            phase="reason",
            round=10,
            hypothesis_id="H10",
            interaction=interaction,
            routing={"next_node": "result_finalizer", "reason": "한도 초과"},
            step_type="recovery",
        )
        assert payload["step_type"] == "recovery"

    def test_prompt_variables_copied(self) -> None:
        """payload 의 prompt_variables 는 호출측 원본과 분리된 복사본."""
        source = {"key": "v1"}
        interaction = LLMInteraction(
            prompt_variables=source,
            raw_response="",
        )
        payload = build_llm_reasoning_payload(
            node="x",
            phase="interpret",
            round=0,
            hypothesis_id="",
            interaction=interaction,
            routing={"next_node": "", "reason": ""},
        )
        source["key"] = "v2"
        assert payload["inputs"]["prompt_variables"] == {"key": "v1"}

    def test_parsed_summary_none_excluded(self) -> None:
        """parsed_summary=None 이면 output 에 parsed 키가 없다."""
        interaction = LLMInteraction(
            prompt_variables={"q": "x"},
            raw_response='{"x":1}',
        )
        payload = build_llm_reasoning_payload(
            node="n",
            phase="reason",
            round=0,
            hypothesis_id="",
            interaction=interaction,
            routing={"next_node": "", "reason": ""},
        )
        assert "parsed" not in payload["output"]
