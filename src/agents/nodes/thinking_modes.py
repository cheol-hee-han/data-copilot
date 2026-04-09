"""노드별 LLM thinking 모드 설정.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

폐쇄망 배포 시 Qwen3.5, Solar Pro 2 등 thinking 기능을 지원하는
오픈소스 모델에서 노드별로 추론 깊이를 최적화하기 위한 설정 모듈이다.
단순 분류/포맷팅 노드에 thinking을 켜면 불필요한 레이턴시가 발생하고,
복잡한 SQL 생성 노드에 thinking을 끄면 정확도가 떨어지므로
노드 특성에 맞는 모드를 명시적으로 지정한다.

mode 값:
    "off"   -- thinking 비활성화 (단순 분류/포맷팅 태스크)
    "auto"  -- 모델 기본값 사용 (추론이 필요한 태스크)
    "low"   -- 경량 추론
    "high"  -- 최대 추론 (SQL 생성 등 정확도 최우선)

설계 결정:
    - Claude API에서는 thinking 파라미터가 무시되므로 영향 없다.
    - 노드를 추가/삭제할 때 NODE_THINKING_MODES도 함께 관리해야 한다.
    - 등록되지 않은 노드는 DEFAULT_THINKING_MODE("auto")로 폴백한다.
"""

from __future__ import annotations

NODE_THINKING_MODES: dict[str, str] = {
    # ── Interpret 계층 (단순 분류 → thinking 불필요) ──
    "intent_classifier": "off",
    "clarification_handler": "off",

    # ── Reason 계층 (추론 필요) ──
    "query_normalizer":  "auto",
    "reasoning_preparer": "off",
    "context_retriever":   "off",
    "context_interpreter": "auto",
    "readiness_gate":    "off",
    "sql_generator":     "high",
    "sql_validator":     "auto",
    "recovery_agent":    "off",

    # ── Present 계층 (정리/포맷 → thinking 불필요) ──
    "analyzer":          "off",
    "viz_judgment":      "off",
    "formatter":         "off",
}

DEFAULT_THINKING_MODE = "auto"


def get_thinking_mode(node_name: str) -> str:
    """노드의 thinking 모드를 반환한다.

    NODE_THINKING_MODES에 없는 노드는 DEFAULT_THINKING_MODE를 반환한다.
    """
    return NODE_THINKING_MODES.get(node_name, DEFAULT_THINKING_MODE)
