"""공유 데이터 모델.

작성자: 한철희 / 최종수정: 2026-04-20 16:27:03

전 계층(graph, nodes, services, connectors)에서 참조하는 모델을 제공한다.
의존성 방향: graph → nodes → services → connectors → models (역참조 없음).
"""

from src.models.enums import IntentType, QueryStatus, VisualizationType
from src.models.result import AnalysisResult, SQLResult, VisualizationData
from src.models.trace import TraceEntry, add_trace

__all__ = [
    # enums
    "IntentType",
    "QueryStatus",
    "VisualizationType",
    # result
    "AnalysisResult",
    "SQLResult",
    "VisualizationData",
    # trace
    "TraceEntry",
    "add_trace",
]
