"""thinking_modes.py 단위 테스트.

테스트 대상:
    - get_thinking_mode() 함수 — 등록된 노드, 미등록 노드, 기본값 반환
    - NODE_THINKING_MODES 딕셔너리 구조 검증
    - DEFAULT_THINKING_MODE 값 검증

외부 의존성 없음 (순수 함수 테스트).
"""

from __future__ import annotations

from src.agents.nodes.thinking_modes import (
    DEFAULT_THINKING_MODE,
    NODE_THINKING_MODES,
    get_thinking_mode,
)

# 허용 모드 집합 — 소스에 명시된 값
_VALID_MODES = {"off", "auto", "low", "high"}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. NODE_THINKING_MODES 구조 검증
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestNodeThinkingModesDict:
    """NODE_THINKING_MODES 딕셔너리 불변 구조 검증."""

    def test_all_values_are_valid_modes(self):
        """등록된 모든 노드의 모드값이 허용 집합에 속한다."""
        for node, mode in NODE_THINKING_MODES.items():
            assert mode in _VALID_MODES, (
                f"노드 '{node}'의 mode '{mode}'가 허용 목록에 없음"
            )

    def test_sql_generator_is_high(self):
        """sql_generator는 최대 추론 모드('high')를 사용한다."""
        assert NODE_THINKING_MODES.get("sql_generator") == "high"

    def test_intent_classifier_is_off(self):
        """intent_classifier는 thinking 불필요('off')."""
        assert NODE_THINKING_MODES.get("intent_classifier") == "off"

    def test_formatter_is_off(self):
        """formatter는 단순 포맷팅이므로 'off'."""
        assert NODE_THINKING_MODES.get("formatter") == "off"

    def test_context_interpreter_is_auto(self):
        """context_interpreter는 추론이 필요하므로 'auto'."""
        assert NODE_THINKING_MODES.get("context_interpreter") == "auto"

    def test_dict_is_not_empty(self):
        """딕셔너리에 최소 1개 이상의 노드가 등록되어 있다."""
        assert len(NODE_THINKING_MODES) > 0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. DEFAULT_THINKING_MODE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestDefaultThinkingMode:

    def test_default_is_auto(self):
        """기본 thinking 모드는 'auto'."""
        assert DEFAULT_THINKING_MODE == "auto"

    def test_default_is_valid_mode(self):
        """기본값도 허용 모드 집합에 속한다."""
        assert DEFAULT_THINKING_MODE in _VALID_MODES


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. get_thinking_mode() 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestGetThinkingMode:
    """get_thinking_mode() — 등록/미등록 노드, 반환값 검증."""

    def test_registered_node_returns_correct_mode(self):
        """등록된 노드는 지정된 모드를 반환한다."""
        for node, expected_mode in NODE_THINKING_MODES.items():
            assert get_thinking_mode(node) == expected_mode

    def test_unregistered_node_returns_default(self):
        """등록되지 않은 노드명은 DEFAULT_THINKING_MODE를 반환한다."""
        assert get_thinking_mode("nonexistent_node") == DEFAULT_THINKING_MODE

    def test_empty_string_returns_default(self):
        """빈 문자열 노드명은 기본값 반환."""
        assert get_thinking_mode("") == DEFAULT_THINKING_MODE

    def test_case_sensitive(self):
        """노드명은 대소문자를 구분한다 — 'SQL_GENERATOR'는 미등록."""
        assert get_thinking_mode("SQL_GENERATOR") == DEFAULT_THINKING_MODE

    def test_return_type_is_str(self):
        """반환값은 항상 문자열 타입이다."""
        assert isinstance(get_thinking_mode("sql_generator"), str)
        assert isinstance(get_thinking_mode("unknown_node"), str)

    def test_all_registered_nodes_return_valid_mode(self):
        """등록된 모든 노드에 대해 유효한 모드가 반환된다."""
        for node in NODE_THINKING_MODES:
            mode = get_thinking_mode(node)
            assert mode in _VALID_MODES

    def test_sql_generator_returns_high(self):
        """sql_generator는 'high' 모드를 반환한다."""
        assert get_thinking_mode("sql_generator") == "high"

    def test_intent_classifier_returns_off(self):
        """intent_classifier는 'off' 모드를 반환한다."""
        assert get_thinking_mode("intent_classifier") == "off"
