"""추론 추적 모델 및 헬퍼.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

각 노드가 수행한 주요 결정·판단을 TraceEntry 로 기록하여
파이프라인 추론 과정의 투명성을 사용자에게 제공한다.

핵심 함수:
    - add_trace: 노드 return dict 에서 trace_log 에 항목 추가
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from src.agents.state.state import PipelineState


class TraceEntry(BaseModel):
    """파이프라인 추론 추적 항목.

    각 노드가 수행한 주요 결정·판단을 기록하여
    추론 과정의 투명성을 제공한다.
    """

    node: str  # 노드 이름 (preprocess, classify_intent, ...)
    action: str  # 수행한 작업 요약
    detail: str = ""  # 상세 내용 (선택)
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


def add_trace(
    state: PipelineState,
    node: str,
    action: str,
    detail: str = "",
) -> list[TraceEntry]:
    """기존 trace_log 에 새 항목을 추가한 목록을 반환한다.

    노드의 return dict 에 ``"trace_log": add_trace(state, ...)`` 형태로 사용.
    """
    entry = TraceEntry(node=node, action=action, detail=detail)
    return [*state.trace_log, entry]


