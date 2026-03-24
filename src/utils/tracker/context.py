"""중앙 집중식 트래커 전파 — contextvars 기반.

파이프라인 실행 시 EvaluationTracker와 현재 노드 이름을 contextvars에 설정하면,
LLM 클라이언트·커넥터·서비스 등 호출 스택 어디서든 tracker에 접근할 수 있다.

사용 흐름:
    1. EvaluationTracker.track() 데코레이터가 노드 실행 전 set_current_node 호출
    2. EvaluationTracker.inject() 가 set_current_tracker 호출
    3. 노드 내부에서 호출되는 llm_client, connector, service 등이
       get_current_tracker() / get_current_node() 로 읽어서 자동 추적
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.utils.tracker.evaluation import EvaluationTracker

_current_tracker: ContextVar[EvaluationTracker | None] = ContextVar(
    "_current_tracker", default=None,
)
_current_node: ContextVar[str] = ContextVar(
    "_current_node", default="",
)


def set_current_tracker(tracker: EvaluationTracker | None) -> None:
    """현재 실행 컨텍스트에 트래커를 설정한다."""
    _current_tracker.set(tracker)


def get_current_tracker() -> EvaluationTracker | None:
    """현재 실행 컨텍스트의 트래커를 반환한다."""
    return _current_tracker.get()


def set_current_node(node: str) -> None:
    """현재 실행 중인 노드 이름을 설정한다."""
    _current_node.set(node)


def get_current_node() -> str:
    """현재 실행 중인 노드 이름을 반환한다."""
    return _current_node.get()
