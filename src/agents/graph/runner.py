"""파이프라인 실행 엔트리포인트 — LangGraph 그래프의 최상위 호출 인터페이스.

API 서버(server.py)나 CLI 에서 호출되어 LangGraph 파이프라인을 초기화·실행하고
최종 PipelineResult 를 반환하는 진입점이다.
커넥터 매니저를 통해 데이터 소스(실제/Dummy)를 연결하고, EvaluationTracker 로
노드별 실행 계측을 수행하며, 실행 완료 후 SQL 생성·검증·실행 결과를 트래커에 기록한다.
시각화 데이터가 있으면 VisualizationData 에 담아 함께 반환한다.

핵심 함수:
    - run_pipeline: 사용자 입력을 받아 파이프라인을 실행하고 PipelineResult 를 반환
    - main: CLI 엔트리포인트 (python -m src.agents.graph.runner '질의 내용')

사용법:
    python -m src.agents.graph.runner "이번 달 신규 고객 수 알려줘"
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from typing import Any

from src.connectors.manager import get_connector_manager
from src.agents.graph.pipeline import create_app
from src.agents.models.response import PipelineResult
from src.models.result import VisualizationData
from src.agents.state.state import PipelineState
from src.utils.tracker import EvaluationTracker
from src.tools.langsmith import setup_langsmith
from src.utils.logger import (
    bind_query_context,
    clear_query_context,
    get_logger,
    setup_logging,
)

logger = get_logger(__name__)


async def run_pipeline(
    user_input: str,
    session_id: str | None = None,
    conversation_history: list[dict[str, str]] | None = None,
    tracker: EvaluationTracker | None = None,
    *,
    clarification_state: dict[str, Any] | None = None,
) -> PipelineResult:
    """파이프라인을 실행하고 최종 결과를 반환한다.

    Args:
        tracker: 평가 트래커. 제공 시 노드 계측이 적용된다.
            None이면 기본 트래커를 생성한다 (설정에 따라 활성/비활성).

    반환값의 str() 은 기존처럼 formatted_response 문자열이므로
    기존 호출부와 하위 호환된다.
    """
    if not session_id:
        session_id = str(uuid.uuid4())

    # 질의 ID를 로그 컨텍스트에 바인딩 (이후 모든 로그에 자동 포함)
    query_id = session_id[-8:]  # 마지막 8자리로 간결하게
    bind_query_context(query_id)

    # 트래커 생성 (외부에서 주입하지 않은 경우)
    if tracker is None:
        tracker = EvaluationTracker(run_id=session_id)

    logger.info(
        "파이프라인 실행 시작",
        user_input=user_input,
        session_id=session_id,
    )

    tracker.start_run(user_input=user_input, session_id=session_id)

    # 커넥터 초기화 (settings.use_dummy 에 따라 Dummy/Real 자동 전환)
    manager = get_connector_manager()
    await manager.connect_all()

    app = create_app(tracker=tracker)

    # 명확화 재진입: 이전 파이프라인이 awaiting_clarification으로 끝났으면
    # 현재 사용자 입력을 clarification_response로 주입한다
    cs = clarification_state or {}
    initial_state = PipelineState(
        user_input=user_input,
        session_id=session_id,
        conversation_history=conversation_history or [],
        awaiting_clarification=cs.get("awaiting", False),
        clarification_response=user_input if cs.get("awaiting") else "",
        clarification_question=cs.get("question", ""),
        preprocessed_input=cs.get("preprocessed_input", ""),
        clarification_turns=cs.get("turns", 0),
    )

    result = await app.ainvoke(initial_state)

    response = result.get("formatted_response", "응답을 생성할 수 없습니다.")
    trace_log = result.get("trace_log", [])

    # 시각화 데이터 추출
    viz = result.get("visualization") or VisualizationData()

    # 트래커 종료 및 SQL 기록
    final_status = result.get("status", "")
    tracker.track_sql(
        generated_sql=result.get("generated_sql", ""),
        validated=not result.get("sql_validation_errors"),
        validation_errors=result.get("sql_validation_errors", []),
        retry_count=result.get("sql_retry_count", 0),
        validation_feedback=result.get("validation_feedback", ""),
        execution_success=bool(
            result.get("sql_result")
            and result["sql_result"].row_count > 0
        ),
        row_count=(
            result["sql_result"].row_count
            if result.get("sql_result") else 0
        ),
        execution_time_ms=(
            result["sql_result"].execution_time_ms
            if result.get("sql_result") else 0
        ),
    )
    intent = result.get("intent")
    tracker.end_run(
        final_intent=intent.value if hasattr(intent, "value") else str(intent or ""),
        final_status=(
            final_status.value
            if hasattr(final_status, "value")
            else str(final_status)
        ),
        final_response_summary=response[:500],
        error_message=result.get("error_message", ""),
    )
    tracker.save()

    logger.info("파이프라인 실행 완료", session_id=session_id)

    # 질의 컨텍스트 해제
    clear_query_context()

    return PipelineResult(
        response=response,
        trace_log=trace_log,
        visualization=viz,
        awaiting_clarification=result.get(
            "awaiting_clarification", False,
        ),
        clarification_question=result.get(
            "clarification_question", "",
        ),
        preprocessed_input=result.get(
            "preprocessed_input", "",
        ),
        clarification_turns=result.get(
            "clarification_turns", 0,
        ),
    )


def main() -> None:
    """CLI 엔트리포인트."""
    setup_logging()
    setup_langsmith()

    if len(sys.argv) < 2:
        print("사용법: python -m src.agents.graph.runner '질의 내용'")
        sys.exit(1)

    user_input = " ".join(sys.argv[1:])
    print(f"\n📝 질의: {user_input}\n")
    print("처리 중...\n")

    pipeline_result = asyncio.run(run_pipeline(user_input))
    print(f"\n📊 결과:\n{pipeline_result.response}")

    if pipeline_result.trace_log:
        print("\n📋 추론 과정:")
        for i, entry in enumerate(pipeline_result.trace_log, 1):
            detail = f": {entry.detail}" if entry.detail else ""
            print(f"  {i}. [{entry.node}] {entry.action}{detail}")


if __name__ == "__main__":
    main()
