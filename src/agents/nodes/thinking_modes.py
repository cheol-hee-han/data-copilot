"""LLM 호출 단위별 thinking 모드 설정.

작성자: 한철희 / 최종수정: 2026-04-16

폐쇄망 배포 시 Qwen3.5, Solar Pro 2 등 thinking 기능을 지원하는
오픈소스 모델에서 LLM 호출 단위별 추론 깊이를 최적화하기 위한 설정 모듈이다.
단순 분류/포맷팅에 thinking을 켜면 불필요한 레이턴시가 발생하고,
복잡한 SQL 생성에 thinking을 끄면 정확도가 떨어지므로
호출 특성에 맞는 모드를 명시적으로 지정한다.

설계 결정:
    - LLM을 호출하는 지점(프롬프트 단위)만 등록한다.
      LLM을 호출하지 않는 그래프 노드(reasoning_preparer 등)는 포함하지 않는다.
    - 한 그래프 노드 안에 여러 LLM 호출이 있으면 프롬프트 단위로 분리 등록한다.
      예: normalize_query → NORMALIZE_QUERY_PHASE1, NORMALIZE_QUERY_PHASE2
    - 같은 프롬프트를 공유하는 호출(context_interpreter batch/step)은 하나로 통합한다.
    - 미등록 노드는 DEFAULT_THINKING_MODE(OFF)로 폴백한다.
"""

from __future__ import annotations

from enum import StrEnum


class ThinkingMode(StrEnum):
    """LLM thinking 추론 깊이."""

    OFF = "off"          # thinking 비활성화 (단순 분류/포맷팅)
    LOW = "low"          # 경량 추론 (교차 검증 등)
    MEDIUM = "medium"    # 중간 추론 (해석/검증 등)
    HIGH = "high"        # 최대 추론 (SQL 생성 등 정확도 최우선)


class LLMNode(StrEnum):
    """LLM 호출 단위 식별자.

    그래프 노드명을 기본으로 하되, 한 노드 안에
    별도 프롬프트를 사용하는 호출은 접미사로 구분한다.
    """

    # ── Interpret 계층 ──
    INTENT_CLASSIFIER = "intent_classifier"
    CONTINUE_ORCHESTRATOR = "continue_orchestrator"

    # ── Reason 계층 ──
    NORMALIZE_QUERY_PHASE1 = "normalize_query_phase1"
    NORMALIZE_QUERY_PHASE2 = "normalize_query_phase2"
    CONTEXT_INTERPRETER = "context_interpreter"
    SQL_GENERATOR = "sql_generator"
    SQL_VALIDATOR = "sql_validator"
    RECOVERY_AGENT = "recovery_agent"

    # ── Present 계층 ──
    ANALYZER = "analyzer"
    VISUALIZER_JUDGMENT = "visualizer_judgment"
    VISUALIZER_SVG = "visualizer_svg"


NODE_THINKING_MODES: dict[LLMNode, ThinkingMode] = {
    # ── Interpret 계층 ──
    LLMNode.INTENT_CLASSIFIER:       ThinkingMode.HIGH,     # 다단계 분류 (연속성+의도+모호성)
    LLMNode.CONTINUE_ORCHESTRATOR:   ThinkingMode.MEDIUM,   # 참조 턴 결정 + 3+1 라우팅 분류

    # ── Reason 계층 ──
    LLMNode.NORMALIZE_QUERY_PHASE1:  ThinkingMode.HIGH,     # 8슬롯 의미 분해
    LLMNode.NORMALIZE_QUERY_PHASE2:  ThinkingMode.MEDIUM,   # 12규칙 교차 검증
    LLMNode.CONTEXT_INTERPRETER:     ThinkingMode.MEDIUM,   # 수집된 증거 기반 적합성 판정
    LLMNode.SQL_GENERATOR:           ThinkingMode.HIGH,     # SQL 합성 (정확도 최우선)
    LLMNode.SQL_VALIDATOR:           ThinkingMode.MEDIUM,   # 8체크 품질 검증
    LLMNode.RECOVERY_AGENT:          ThinkingMode.HIGH,     # 실패 진단 + 복구 전략

    # ── Present 계층 ──
    LLMNode.ANALYZER:                ThinkingMode.MEDIUM,   # 패턴 마이닝/인사이트
    LLMNode.VISUALIZER_JUDGMENT:     ThinkingMode.OFF,      # 규칙 기반 차트 분류
    LLMNode.VISUALIZER_SVG:          ThinkingMode.OFF,      # SVG 좌표 변환/생성
}

DEFAULT_THINKING_MODE: ThinkingMode = ThinkingMode.OFF


def get_thinking_mode(node_name: str) -> ThinkingMode:
    """LLM 호출 단위의 thinking 모드를 반환한다.

    NODE_THINKING_MODES에 없는 노드는 DEFAULT_THINKING_MODE(OFF)를 반환한다.
    """
    try:
        key = LLMNode(node_name)
    except ValueError:
        return DEFAULT_THINKING_MODE
    return NODE_THINKING_MODES.get(key, DEFAULT_THINKING_MODE)
