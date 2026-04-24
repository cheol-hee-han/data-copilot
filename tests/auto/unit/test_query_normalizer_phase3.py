"""query_normalizer Phase 3 `{handoff_note}` directive 주입 단위 테스트.

테스트 대상:
    - run_normalization 이 Phase1 system prompt 의 `{handoff_note}`
      플레이스홀더를 전달받은 handoff_note 값으로 치환해 LLM 에 전달하는지
    - NEW 턴(기본값 `"(없음)"`) 치환이 default 로 동작하는지
    - Phase2 system prompt 는 handoff_note 치환 대상이 아니어야 함 (§14.3.1)
    - 노드 단에서 `normalize_handoff_note(state.handoff_note)` 를 통해 전파되는지

테스트 대상 소스:
    src/services/query_normalizer.py::run_normalization
    src/agents/nodes/interpret/query_normalizer.py::query_normalizer_node
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.services.query_normalizer import run_normalization

_PHASE2_ENABLED_PATH = (
    "src.services.query_normalizer"
    ".settings.normalization_phase2_enabled"
)

# ──────────────────────────────────────────────────────────────────
# 테스트용 최소 프롬프트 — 실제 프롬프트 파일 로드를 피해 격리한다.
# ──────────────────────────────────────────────────────────────────

_PHASE1_SYSTEM_WITH_PLACEHOLDER = """\
[RULES]
슬롯 1~8 추출.

## 6. 연속 질의 오케스트레이터 지시

{handoff_note}
"""

_PHASE1_SYSTEM_NO_PLACEHOLDER = """[RULES]\n슬롯 1~8 추출."""
_PHASE2_SYSTEM = """[RULES]\n교차 검증."""

# LLM 이 반환할 최소 정합 JSON (Phase1 → Phase2 skip 설정 가정)
_STUB_JSON = {
    "intent": {"primary": "EXTRACT", "modifiers": []},
    "query_category": "DATA_QUERY",
    "original_query": "지점별 여신 현황",
    "rewritten_query": "지점별 여신 현황",
    "entities": [],
    "measures": [],
    "filters": [],
    "time": {"type": "NONE"},
    "group_by": {"dimensions": []},
    "output_hint": {
        "format": "NONE",
        "doc_type": None,
        "expected_columns": [],
        "note": None,
    },
    "ambiguities": [],
    "search_keywords": {
        "meta_search": [],
        "vector_search": "",
    },
}


@pytest.mark.asyncio
class TestRunNormalizationHandoffNote:
    """`run_normalization` 이 `{handoff_note}` 치환을 올바르게 수행하는지 검증."""

    @patch("src.services.query_normalizer._call_llm_and_parse")
    async def test_handoff_note_substituted_into_phase1_system(
        self, mock_call: AsyncMock,
    ) -> None:
        mock_call.return_value = ("{}", dict(_STUB_JSON))
        note_body = "### 연속 처리 의도\n서울 지점 조건을 추가하세요."
        with patch(_PHASE2_ENABLED_PATH, False):
            await run_normalization(
                "지점별 여신 현황",
                phase1_system=_PHASE1_SYSTEM_WITH_PLACEHOLDER,
                phase2_system=_PHASE2_SYSTEM,
                handoff_note=note_body,
            )

        # 첫 호출 (Phase1) 의 system_prompt 에 치환된 handoff_note 본문이 있어야 한다.
        assert mock_call.call_count == 1
        p1_system_arg = mock_call.call_args_list[0].args[0]
        assert "{handoff_note}" not in p1_system_arg
        assert "서울 지점 조건을 추가하세요." in p1_system_arg

    @patch("src.services.query_normalizer._call_llm_and_parse")
    async def test_default_handoff_note_renders_placeholder_literal(
        self, mock_call: AsyncMock,
    ) -> None:
        """NEW 턴 — 기본값 `"(없음)"` 이 Phase1 system prompt 에 주입된다."""
        mock_call.return_value = ("{}", dict(_STUB_JSON))
        with patch(_PHASE2_ENABLED_PATH, False):
            await run_normalization(
                "지점별 여신 현황",
                phase1_system=_PHASE1_SYSTEM_WITH_PLACEHOLDER,
                phase2_system=_PHASE2_SYSTEM,
            )

        p1_system_arg = mock_call.call_args_list[0].args[0]
        assert "{handoff_note}" not in p1_system_arg
        assert "(없음)" in p1_system_arg

    @patch("src.services.query_normalizer._call_llm_and_parse")
    async def test_phase2_system_not_rendered_with_handoff_note(
        self, mock_call: AsyncMock,
    ) -> None:
        """Phase2 system prompt 에는 handoff_note 치환이 일어나지 않아야 함."""
        mock_call.return_value = ("{}", dict(_STUB_JSON))
        with patch(_PHASE2_ENABLED_PATH, True):
            await run_normalization(
                "지점별 여신 현황",
                phase1_system=_PHASE1_SYSTEM_WITH_PLACEHOLDER,
                phase2_system=_PHASE2_SYSTEM + "\n{handoff_note}",
                handoff_note="### 연속 처리 의도\n의도 본문",
            )

        # Phase2 호출은 두 번째 _call_llm_and_parse (_run_phase2 내부).
        assert mock_call.call_count == 2
        p2_system_arg = mock_call.call_args_list[1].args[0]
        # Phase2 는 치환 대상이 아니므로 placeholder 가 그대로 남아 있어야 함.
        assert "{handoff_note}" in p2_system_arg
        assert "의도 본문" not in p2_system_arg

    @patch("src.services.query_normalizer._call_llm_and_parse")
    async def test_prompt_without_placeholder_is_preserved(
        self, mock_call: AsyncMock,
    ) -> None:
        """placeholder 가 없는 프롬프트는 str.replace 가 no-op 으로 동작해야 한다."""
        mock_call.return_value = ("{}", dict(_STUB_JSON))
        with patch(_PHASE2_ENABLED_PATH, False):
            await run_normalization(
                "지점별 여신 현황",
                phase1_system=_PHASE1_SYSTEM_NO_PLACEHOLDER,
                phase2_system=_PHASE2_SYSTEM,
                handoff_note="### 연속 처리 의도\n의도",
            )

        p1_system_arg = mock_call.call_args_list[0].args[0]
        assert p1_system_arg == _PHASE1_SYSTEM_NO_PLACEHOLDER


@pytest.mark.asyncio
class TestQueryNormalizerNodeHandoffPropagation:
    """노드에서 `normalize_handoff_note(state.handoff_note)` 가 서비스로 전달되는지 검증."""

    async def test_node_propagates_handoff_note_from_state(self) -> None:
        from src.agents.nodes.interpret.query_normalizer import (
            query_normalizer_node,
        )
        from src.agents.state.state import PipelineState

        state = PipelineState(
            session_id="sess-1",
            user_message="지점별 여신 현황",
            preprocessed_input="지점별 여신 현황",
            handoff_note="### 연속 처리 의도\n서울 지점 추가",
        )

        with patch(
            "src.agents.nodes.interpret.query_normalizer.run_normalization",
            new=AsyncMock(),
        ) as mock_run:
            from src.agents.models.normalization import NormalizedQuery

            mock_run.return_value = (
                NormalizedQuery(
                    original_query="지점별 여신 현황",
                    rewritten_query="지점별 여신 현황",
                ),
                [],
            )
            await query_normalizer_node(state)

        kwargs = mock_run.call_args.kwargs
        assert kwargs["handoff_note"] == "### 연속 처리 의도\n서울 지점 추가"

    async def test_node_passes_placeholder_when_state_note_empty(self) -> None:
        from src.agents.nodes.interpret.query_normalizer import (
            query_normalizer_node,
        )
        from src.agents.state.state import PipelineState

        state = PipelineState(
            session_id="sess-1",
            user_message="지점별 여신 현황",
            preprocessed_input="지점별 여신 현황",
            # handoff_note 기본값 "" — NEW 턴 시나리오
        )

        with patch(
            "src.agents.nodes.interpret.query_normalizer.run_normalization",
            new=AsyncMock(),
        ) as mock_run:
            from src.agents.models.normalization import NormalizedQuery

            mock_run.return_value = (
                NormalizedQuery(
                    original_query="지점별 여신 현황",
                    rewritten_query="지점별 여신 현황",
                ),
                [],
            )
            await query_normalizer_node(state)

        kwargs = mock_run.call_args.kwargs
        assert kwargs["handoff_note"] == "(없음)"
