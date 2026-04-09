"""현재 노드 이름 전파 — contextvars 기반 비동기 안전 상태 공유.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

LangGraph 파이프라인에서 현재 실행 중인 노드 이름을 contextvars를 통해
비동기 안전하게 전파한다. ``DataCopilotCallbackHandler``가
``on_chain_start``에서 ``set_current_node``를 호출하면, LLM 클라이언트
(client.py), 추적 디스패치(dispatch.py), 리트라이(retry.py) 등
노드 함수 바깥의 유틸리티에서도 현재 노드를 식별할 수 있다.

threading.local 대신 contextvars를 사용하는 이유:
asyncio 태스크 간 격리가 보장되어, 동시에 여러 파이프라인이
실행되어도 노드 이름이 섞이지 않는다.

핵심 함수:
    - set_current_node: 콜백 핸들러가 노드 진입 시 호출
    - get_current_node: LLM 클라이언트 등에서 현재 노드명 조회
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
