"""파이프라인 텔레메트리 — 콜백 핸들러 + 분석 + 시각화.

작성자: 한철희 / 최종수정: 2026-04-20 16:27:03

``DataCopilotCallbackHandler`` 를 통해 노드·LLM·의사결정·State 변화를
자동 추적하고, 트레이스 분석 및 7섹션 보고서를 생성한다.
"""

from src.utils.tracker.callback_handler import (
    DataCopilotCallbackHandler,
)
from src.utils.tracker.context import (
    get_current_node,
    set_current_node,
)
from src.utils.tracker.dispatch import (
    dispatch_tracking_event,
)
from src.utils.tracker.evaluation import (
    TimelineEntry,
)
from src.utils.tracker.reasoning_payload import (
    LLMInteraction,
    build_llm_reasoning_payload,
    llm_failure_sentinel,
    llm_skip_sentinel,
)
from src.utils.tracker.trace_analyzer import (
    BatchReport,
    Finding,
    TraceReport,
    analyze_batch,
    analyze_trace,
)
from src.utils.tracker.visualizer import (
    render_detail_table,
    render_from_json,
    render_full_report,
    save_report,
)

__all__ = [
    # handler
    "DataCopilotCallbackHandler",
    # dispatch
    "dispatch_tracking_event",
    # reasoning payload
    "LLMInteraction",
    "build_llm_reasoning_payload",
    "llm_failure_sentinel",
    "llm_skip_sentinel",
    # context
    "get_current_node",
    "set_current_node",
    # models
    "TimelineEntry",
    # analysis
    "analyze_trace",
    "analyze_batch",
    "BatchReport",
    "Finding",
    "TraceReport",
    # visualization
    "render_detail_table",
    "render_full_report",
    "render_from_json",
    "save_report",
]
