"""평가 트래커 및 컨텍스트 전파 유틸리티.

노드별 입출력·의사결정·LLM 호출을 기록하는 EvaluationTracker와
contextvars 기반 트래커 전파 메커니즘, 타임라인 시각화를 제공한다.
"""

from src.utils.tracker.context import (
    get_current_node,
    get_current_tracker,
    set_current_node,
    set_current_tracker,
)
from src.utils.tracker.evaluation import (
    BatchEvaluationTracker,
    EvaluationTracker,
    TimelineEntry,
)
from src.utils.tracker.trace_analyzer import (
    analyze_batch,
    analyze_trace,
    BatchReport,
    Finding,
    TraceReport,
)
from src.utils.tracker.visualizer import (
    render_detail_table,
    render_from_json,
    render_full_report,
    render_gantt,
    render_mermaid,
    save_report,
)


def record_prompt_variables(
    variables: dict[str, str],
) -> None:
    """직전 LLM 호출 기록에 프롬프트 치환 변수를 보강한다.

    LLM 호출 직후 호출하면
    tracker.trace.llm_calls[-1]에 변수를 기록한다.
    tracker가 비활성이거나 llm_calls가 비어있으면 무시.

    사용 예::

        prompt = template.replace("{query}", query)
        response = await client.messages.create(
            system=prompt, ...
        )
        record_prompt_variables(
            {"query": query, "tables": tables_text}
        )
    """
    tracker = get_current_tracker()
    if (
        tracker
        and tracker.enabled
        and tracker.trace.llm_calls
    ):
        last = tracker.trace.llm_calls[-1]
        last.prompt_variables = variables


__all__ = [
    # tracker core
    "BatchEvaluationTracker",
    "EvaluationTracker",
    "TimelineEntry",
    # context propagation
    "get_current_node",
    "get_current_tracker",
    "set_current_node",
    "set_current_tracker",
    "record_prompt_variables",
    # analysis
    "analyze_trace",
    "analyze_batch",
    "BatchReport",
    "Finding",
    "TraceReport",
    # visualization
    "render_mermaid",
    "render_gantt",
    "render_detail_table",
    "render_full_report",
    "render_from_json",
    "save_report",
]
