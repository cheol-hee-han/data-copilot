"""Phase 3 프롬프트 위생 회귀 방어 테스트.

목적:
    Gap A 대칭화(REFINE/REGENERATE 다중 섹션 허용) · Gap B (placeholder 중괄호
    이름을 프롬프트에서 지칭 금지) · 섹션 제목 꼬리 `(previous_sql)` /
    `(handoff_note)` 제거의 회귀를 방어한다.

검증 범위:
    1. orchestrator 프롬프트(`continue_orchestrator_system.txt`) 본문·예시에
       placeholder 중괄호 이름(`{previous_sql}`, `{previous_sql_explanation}`,
       `{handoff_note}`) 직접 인용이 없다.
    2. 하류 소비자 프롬프트(sql_generator × 5, recovery_agent,
       context_interpreter, sql_validator, query_normalizer) 본문에서도
       placeholder 중괄호 이름의 backtick 직접 인용이 없다.
       (placeholder 주입 라인 `{handoff_note}` / `{previous_sql}` 자체는 허용 —
        이는 렌더링 시 치환되는 템플릿 위치 표식이므로)
    3. 섹션 제목 꼬리 `(previous_sql)` · `(handoff_note)` 가 프롬프트 어느
       파일에도 남아있지 않다.
    4. REFINE / REGENERATE 필수 섹션 검증기가 선택 섹션 추가로 실패하지 않는다
       (Gap A 대칭화 — substring 검증 특성 확인).

테스트 대상:
    resources/prompts/interpret/continue_orchestrator_system.txt
    resources/prompts/interpret/query_normalizer_phase1_system.txt
    resources/prompts/reason/context_interpreter_system.txt
    resources/prompts/reason/recovery_agent_system.txt
    resources/prompts/reason/sql_generator_system{_dialect}.txt
    resources/prompts/reason/sql_validator_system.txt
    src/agents/nodes/interpret/continue_orchestrator.py::_validate_handoff_note_headers
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.agents.nodes.interpret.continue_orchestrator import (
    _validate_handoff_note_headers,
)
from src.models.enums import ContinueRoute

_PROMPT_ROOT = Path("resources/prompts")

# 본문 인용 금지 대상 — 렌더링 후 치환되어 사라지므로 LLM 가 읽을 때 의미가 깨진다.
_FORBIDDEN_QUOTED_PLACEHOLDERS = (
    "`{previous_sql}`",
    "`{previous_sql_explanation}`",
    "`{handoff_note}`",
)

# 섹션 제목에 placeholder 이름 꼬리가 달리지 않도록.
_FORBIDDEN_TITLE_TAILS = (
    "(previous_sql)",
    "(handoff_note)",
    "(handoff_note, 참고용)",
)


class TestOrchestratorPromptHygiene:
    """continue_orchestrator_system.txt — 작성 규칙·예시에 placeholder 중괄호
    직접 인용 없음 (Gap B)."""

    _ORCH_PATH = (
        _PROMPT_ROOT / "interpret" / "continue_orchestrator_system.txt"
    )

    def test_no_quoted_placeholder_names(self) -> None:
        text = self._ORCH_PATH.read_text(encoding="utf-8")
        for token in _FORBIDDEN_QUOTED_PLACEHOLDERS:
            assert token not in text, (
                f"orchestrator 프롬프트에 placeholder 직접 인용 {token} 잔존"
            )

    def test_no_snapshot_generated_sql_reference_in_handoff_examples(
        self,
    ) -> None:
        """handoff_note 예시에 'snapshot.generated_sql' 단어 인용 금지.

        하류 sql_generator 는 snapshot 을 직접 보지 않고 `## 직전 턴 참고 SQL`
        섹션으로 주입받으므로, 하류 LLM 기준의 참조 표현으로 작성해야 한다.
        """
        text = self._ORCH_PATH.read_text(encoding="utf-8")
        # handoff_note 필드 안에 들어가는 예시 문구에서만 금지.
        # 설계 문서 성격 line 은 없지만, 방어적으로 strict 검사.
        assert "snapshot.generated_sql" not in text, (
            "handoff_note 예시에 snapshot.generated_sql 문구가 남아있다 "
            "— 하류 LLM 기준 '## 직전 턴 참고 SQL' 섹션으로 치환해야 한다"
        )


class TestDownstreamPromptHygiene:
    """하류 소비자 프롬프트들에도 placeholder 중괄호 직접 인용이 없어야 한다.

    단, placeholder 주입 라인 자체(예: `{handoff_note}` 단독 라인)는 렌더링 시
    실제 값으로 치환되는 템플릿 위치 표식이므로 허용. 검사는 **backtick 으로
    감싸진 인용 형태** 만 금지한다.
    """

    _TARGETS = [
        "interpret/query_normalizer_phase1_system.txt",
        "reason/context_interpreter_system.txt",
        "reason/recovery_agent_system.txt",
        "reason/sql_generator_system.txt",
        "reason/sql_generator_system_sybase_iq.txt",
        "reason/sql_generator_system_impala.txt",
        "reason/sql_generator_system_oracle.txt",
        "reason/sql_generator_system_postgres.txt",
        "reason/sql_validator_system.txt",
    ]

    @pytest.mark.parametrize("rel_path", _TARGETS)
    def test_no_quoted_placeholder_names(self, rel_path: str) -> None:
        text = (_PROMPT_ROOT / rel_path).read_text(encoding="utf-8")
        for token in _FORBIDDEN_QUOTED_PLACEHOLDERS:
            assert token not in text, (
                f"{rel_path} 에 placeholder 직접 인용 {token} 잔존"
            )


class TestSectionTitleTailRemoved:
    """섹션 제목 꼬리 `(previous_sql)` · `(handoff_note)` 재도입 방어."""

    _TARGETS = [
        "interpret/continue_orchestrator_system.txt",
        "interpret/query_normalizer_phase1_system.txt",
        "reason/context_interpreter_system.txt",
        "reason/recovery_agent_system.txt",
        "reason/sql_generator_system.txt",
        "reason/sql_generator_system_sybase_iq.txt",
        "reason/sql_generator_system_impala.txt",
        "reason/sql_generator_system_oracle.txt",
        "reason/sql_generator_system_postgres.txt",
        "reason/sql_validator_system.txt",
    ]

    @pytest.mark.parametrize("rel_path", _TARGETS)
    def test_no_placeholder_name_title_tails(self, rel_path: str) -> None:
        text = (_PROMPT_ROOT / rel_path).read_text(encoding="utf-8")
        # 단, continue_orchestrator_system.txt 에는 "지시문(handoff_note)" 같이
        # orchestrator 자신의 출력 필드 이름을 본문에서 언급하는 케이스가 있다.
        # 이는 섹션 제목 꼬리가 아니라 필드 설명이므로 허용.
        # "## ... (previous_sql)" / "## ... (handoff_note)" 형태만 금지.
        for line in text.splitlines():
            if not line.startswith("## "):
                continue
            for tail in _FORBIDDEN_TITLE_TAILS:
                assert tail not in line, (
                    f"{rel_path} 에 섹션 제목 꼬리 {tail!r} 잔존: {line}"
                )


class TestGapARouteHeaderValidation:
    """Gap A — REFINE/REGENERATE 에 선택 섹션 추가해도 검증 통과.

    `_validate_handoff_note_headers` 는 필수 헤더 substring "not in" 검사이므로
    선택 섹션 추가가 기존 검증을 깨지 않는다. 회귀 방어용.
    """

    def test_refine_with_visualization_section_passes(self) -> None:
        note = (
            "### 연속 처리 의도\n- 1분기로 축소\n"
            "\n### 시각화/포맷 지시\n- line_chart → bar_chart"
        )
        assert _validate_handoff_note_headers(
            ContinueRoute.REFINE, note,
        ) == []

    def test_refine_with_analyze_section_passes(self) -> None:
        note = (
            "### 연속 처리 의도\n- 상품별로 확장\n"
            "\n### 분석 초점\n- 상위 3개 상품 중심"
        )
        assert _validate_handoff_note_headers(
            ContinueRoute.REFINE, note,
        ) == []

    def test_regenerate_with_visualization_section_passes(self) -> None:
        note = (
            "### SQL 생성 지시\n- SELECT 별칭 변경\n"
            "\n### 시각화/포맷 지시\n- table → bar_chart"
        )
        assert _validate_handoff_note_headers(
            ContinueRoute.REGENERATE, note,
        ) == []

    def test_regenerate_with_analyze_section_passes(self) -> None:
        note = (
            "### SQL 생성 지시\n- 단위 표기 교정\n"
            "\n### 분석 초점\n- 월별 추이 해석"
        )
        assert _validate_handoff_note_headers(
            ContinueRoute.REGENERATE, note,
        ) == []


class TestGapAOrchestratorRulesMention:
    """Gap A — orchestrator 프롬프트에 REGENERATE/REFINE 선택 섹션 허용 명시."""

    _ORCH_PATH = (
        _PROMPT_ROOT / "interpret" / "continue_orchestrator_system.txt"
    )

    def test_regenerate_mentions_optional_viz_section(self) -> None:
        text = self._ORCH_PATH.read_text(encoding="utf-8")
        # OUTPUT_CONTRACT 라인에 REGENERATE 가 선택 섹션을 허용한다는 기술 필요.
        assert (
            "regenerate:" in text
            and "`### 시각화/포맷 지시`" in text
            and "`### 분석 초점`" in text
        )

    def test_refine_mentions_optional_viz_section(self) -> None:
        text = self._ORCH_PATH.read_text(encoding="utf-8")
        assert "refine:" in text
        # refine 행에 선택 섹션 언급이 있어야 한다 — substring 존재 검사.
        refine_lines = [
            ln for ln in text.splitlines() if "refine:" in ln
        ]
        assert refine_lines, "OUTPUT_CONTRACT 에 refine 항목이 없다"
        assert any(
            "시각화/포맷 지시" in ln and "분석 초점" in ln
            for ln in refine_lines
        ), "refine 항목에 선택 섹션 언급이 누락됨"
