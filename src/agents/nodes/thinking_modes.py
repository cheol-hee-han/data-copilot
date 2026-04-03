"""노드별 LLM thinking 모드 설정.

모델이 thinking 기능을 지원하는 경우(Gemini, Qwen 등),
노드별로 thinking 활성화 여부를 제어한다.

mode 값:
    "off"   — thinking 비활성화 (단순 분류/포맷팅 태스크)
    "auto"  — 모델 기본값 사용 (추론이 필요한 태스크)
    "low"   — 경량 추론
    "high"  — 최대 추론 (SQL 생성 등 정확도 최우선)

노드를 추가/삭제할 때 이 파일도 함께 관리한다.
"""

from __future__ import annotations

NODE_THINKING_MODES: dict[str, str] = {
    # ── Interpret 계층 (단순 분류 → thinking 불필요) ──
    "context_classifier": "off",
    "clarification_handler": "off",

    # ── Reason 계층 (추론 필요) ──
    "query_normalizer":  "auto",
    "reasoning_preparer": "off",
    "knowledge_fetcher":   "off",
    "knowledge_interpreter": "auto",
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
