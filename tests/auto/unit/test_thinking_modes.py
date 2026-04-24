"""thinking_modes.py 단위 테스트.

테스트 대상:
    - ThinkingMode / LLMNode StrEnum 정의
    - NODE_THINKING_MODES 딕셔너리 — 모든 LLMNode 등록 여부, 값 유효성
    - get_thinking_mode() — 등록 노드, 미등록 노드, 기본값 반환
"""

from __future__ import annotations

from src.agents.nodes.thinking_modes import (
    DEFAULT_THINKING_MODE,
    NODE_THINKING_MODES,
    LLMNode,
    ThinkingMode,
    get_thinking_mode,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. Enum 정의 검증
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestThinkingModeEnum:

    def test_all_members(self):
        """ThinkingMode 에 OFF/LOW/MEDIUM/HIGH 4개 멤버가 있다."""
        assert set(ThinkingMode) == {
            ThinkingMode.OFF,
            ThinkingMode.LOW,
            ThinkingMode.MEDIUM,
            ThinkingMode.HIGH,
        }

    def test_str_compatible(self):
        """StrEnum 이므로 문자열 비교가 가능하다."""
        assert ThinkingMode.HIGH == "high"
        assert ThinkingMode.OFF == "off"


class TestLLMNodeEnum:

    def test_min_members(self):
        """LLMNode 에 최소 10개 멤버가 있다."""
        assert len(LLMNode) >= 10

    def test_str_compatible(self):
        """StrEnum 이므로 문자열 비교가 가능하다."""
        assert LLMNode.SQL_GENERATOR == "sql_generator"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. NODE_THINKING_MODES 구조 검증
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestNodeThinkingModesDict:
    """NODE_THINKING_MODES 딕셔너리 구조 검증."""

    def test_all_llm_nodes_registered(self):
        """모든 LLMNode 멤버가 NODE_THINKING_MODES 에 등록되어 있다."""
        missing = set(LLMNode) - set(NODE_THINKING_MODES.keys())
        assert not missing, f"미등록 LLMNode: {missing}"

    def test_all_values_are_thinking_mode(self):
        """등록된 모든 값이 ThinkingMode 인스턴스이다."""
        for node, mode in NODE_THINKING_MODES.items():
            assert isinstance(mode, ThinkingMode), (
                f"노드 '{node}'의 값 '{mode}'가 ThinkingMode 가 아님"
            )

    def test_sql_generator_is_high(self):
        """sql_generator 는 최대 추론 모드(HIGH)."""
        assert NODE_THINKING_MODES[LLMNode.SQL_GENERATOR] is ThinkingMode.HIGH

    def test_intent_classifier_is_high(self):
        """intent_classifier 는 다단계 분류이므로 HIGH."""
        key = LLMNode.INTENT_CLASSIFIER
        assert NODE_THINKING_MODES[key] is ThinkingMode.HIGH

    def test_context_interpreter_is_medium(self):
        """context_interpreter 는 수집된 증거 기반 판정이므로 MEDIUM."""
        key = LLMNode.CONTEXT_INTERPRETER
        assert NODE_THINKING_MODES[key] is ThinkingMode.MEDIUM

    def test_analyzer_is_medium(self):
        """analyzer 는 분석/인사이트이므로 MEDIUM."""
        key = LLMNode.ANALYZER
        assert NODE_THINKING_MODES[key] is ThinkingMode.MEDIUM


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. DEFAULT_THINKING_MODE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestDefaultThinkingMode:

    def test_default_is_off(self):
        """기본 thinking 모드는 OFF (안전 기본값)."""
        assert DEFAULT_THINKING_MODE is ThinkingMode.OFF

    def test_default_is_thinking_mode_instance(self):
        """기본값도 ThinkingMode 인스턴스이다."""
        assert isinstance(DEFAULT_THINKING_MODE, ThinkingMode)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. get_thinking_mode() 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestGetThinkingMode:
    """get_thinking_mode() — 등록/미등록 노드, 반환값 검증."""

    def test_registered_node_returns_correct_mode(self):
        """등록된 노드는 지정된 모드를 반환한다."""
        for node, expected_mode in NODE_THINKING_MODES.items():
            assert get_thinking_mode(node) == expected_mode

    def test_unregistered_node_returns_default(self):
        """등록되지 않은 노드명은 DEFAULT_THINKING_MODE 를 반환한다."""
        assert get_thinking_mode("nonexistent_node") is DEFAULT_THINKING_MODE

    def test_empty_string_returns_default(self):
        """빈 문자열 노드명은 기본값 반환."""
        assert get_thinking_mode("") is DEFAULT_THINKING_MODE

    def test_case_sensitive(self):
        """노드명은 대소문자를 구분한다."""
        assert get_thinking_mode("SQL_GENERATOR") is DEFAULT_THINKING_MODE

    def test_return_type_is_thinking_mode(self):
        """반환값은 항상 ThinkingMode 인스턴스이다."""
        assert isinstance(get_thinking_mode("sql_generator"), ThinkingMode)
        assert isinstance(get_thinking_mode("unknown_node"), ThinkingMode)

    def test_sql_generator_returns_high(self):
        """sql_generator 는 HIGH 모드를 반환한다."""
        assert get_thinking_mode("sql_generator") is ThinkingMode.HIGH

    def test_intent_classifier_returns_high(self):
        """intent_classifier 는 HIGH 모드를 반환한다."""
        assert get_thinking_mode("intent_classifier") is ThinkingMode.HIGH

    def test_str_value_lookup(self):
        """문자열 값으로도 조회할 수 있다 (StrEnum 호환)."""
        assert get_thinking_mode(LLMNode.SQL_GENERATOR) is ThinkingMode.HIGH
        assert get_thinking_mode(LLMNode.RECOVERY_AGENT) is ThinkingMode.HIGH
