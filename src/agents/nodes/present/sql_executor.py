"""SQL 실행 노드 — 검증 완료된 SQL을 업무 DB 에서 실행하는 LangGraph 노드.

작성자: 한철희 / 최종수정: 2026-04-10

이 노드는 직접 DB 드라이버를 다루지 않고 ConnectorManager.get_query_db() 가
반환하는 업무 DB 커넥터(ADW/BDP/CRP/TEST 중 system_db_overrides 적용 결과)에
위임하는 얇은(thin) 노드로, 실행 전후 로직에 집중한다.

이중 방어(Double Defense) 전략:
    상위 파이프라인(sql_validator)에서 이미 안전성 검증을 통과한 SQL이지만,
    실행 직전에 check_sql_safety_quick을 한 번 더 호출하여 파이프라인 우회나
    상태 변조로 인한 위험을 방지한다.

실행 후에는 Tracker를 통해 쿼리 소스, 결과 건수, 소요 시간, 절삭 여부 등
실행 메트릭을 기록하며, 설정된 max_query_rows를 초과하는 결과는 자동 절삭한다.
"""

from __future__ import annotations

import time

from src.agents.models.user_messages import (
    ERR_SQL_EXECUTION,
    ERR_SQL_SECURITY,
    format_error,
)
from src.agents.state.state import (
    PipelineState,
    QueryStatus,
    SQLResult,
    add_trace,
)
from src.config import settings
from src.connectors.manager import get_connector_manager
from src.utils.logger import get_logger
from src.utils.security import check_sql_safety_quick
from src.utils.sql_formatter import format_sql_tabular
from src.utils.tracker.dispatch import (
    dispatch_tracking_event,
    CONTEXT_SQL_EXECUTED,
)

logger = get_logger(__name__)


async def execute_sql_node(
    state: PipelineState,
) -> dict:
    """검증된 SQL을 실행한다."""
    manager = get_connector_manager()
    # readiness_gate 가 결정한 reason.target_db 를 그대로 신뢰한다.
    db = manager.get_query_db(state.reason)
    logger.info(
        "SQL 실행 시작",
        sql="\n" + format_sql_tabular(state.reason.validated_sql or ""),
    )

    # 이중 방어
    is_safe, safety_errors = check_sql_safety_quick(
        state.reason.validated_sql or "",
    )
    if not is_safe:
        logger.warning(
            "SQL 이중 검증 실패", errors=safety_errors,
        )
        return {
            "sql_result": SQLResult(),
            "status": QueryStatus.ERROR,
            "error_message": ERR_SQL_SECURITY,
        }
    start_time = time.time()

    try:
        rows = await db.execute_query(
            state.reason.validated_sql or "",
        )
        elapsed = (time.time() - start_time) * 1000

        max_rows = settings.max_query_rows
        truncated = len(rows) > max_rows
        if truncated:
            rows = rows[:max_rows]

        columns = list(rows[0].keys()) if rows else []

        result = SQLResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            execution_time_ms=elapsed,
        )

        await dispatch_tracking_event(CONTEXT_SQL_EXECUTED, {
            "source": "query_db_execute",
            "query": format_sql_tabular(state.reason.validated_sql or ""),
            "results_count": result.row_count,
            "results_summary": [
                f"컬럼: {', '.join(columns)}",
                f"행 수: {result.row_count}건",
                f"소요: {round(elapsed, 1)}ms",
                f"절삭: {truncated}",
                *(
                    [f"샘플: {rows[0]}"]
                    if rows
                    else []
                ),
            ],
            "latency_ms": elapsed,
        })

        logger.info(
            "SQL 실행 완료",
            row_count=result.row_count,
            execution_time_ms=round(elapsed, 2),
        )

        return {
            "sql_result": result,
            "status": QueryStatus.EXECUTED,
            "trace_log": add_trace(
                state, "SQL실행",
                f"쿼리 실행 완료 "
                f"({result.row_count}건, "
                f"{round(elapsed, 1)}ms)",
            ),
        }

    except Exception as e:
        logger.error("SQL 실행 오류", error=str(e))
        return {
            "sql_result": SQLResult(),
            "status": QueryStatus.ERROR,
            "error_message": format_error(
                ERR_SQL_EXECUTION,
            ),
        }
