"""현재 노드 이름 전파 — contextvars 기반.

``DataCopilotCallbackHandler`` 가 ``on_chain_start`` 에서
``set_current_node`` 를 호출하면, LLM 클라이언트 등에서
``get_current_node()`` 로 현재 실행 노드를 식별할 수 있다.
"""

from __future__ import annotations

from contextvars import ContextVar

_current_node: ContextVar[str] = ContextVar(
    "_current_node", default="",
)


def set_current_node(node: str) -> None:
    """현재 실행 중인 노드 이름을 설정한다."""
    _current_node.set(node)


def get_current_node() -> str:
    """현재 실행 중인 노드 이름을 반환한다."""
    return _current_node.get()
