"""SQL 생성 노드 — LLM 기반 SELECT SQL 생성.

수집·보강된 컨텍스트와 정규화된 질의를 조합한 프롬프트로 LLM 을 호출하여
사용자 요청에 대응하는 SELECT SQL 을 생성한다.
검증 실패로 인한 재시도 시 validation_feedback 을 프롬프트에 추가 주입하여
이전 오류를 반복하지 않도록 유도한다.
생성된 SQL 은 state.generated_sql 에 기록되고 sql_retry_count 가 증가한다.

핵심 함수:
    - generate_sql_node: state.preprocessed_input, state.context,
      state.normalized_query, state.validation_feedback 을 읽어 SQL 을 생성하고
      state.generated_sql, state.sql_retry_count 에 기록

위임 구조:
    - 비즈니스 로직: services/sql_prompt_assembler.py (generate_sql)
    - 프롬프트: nodes/prompts/system_prompts.py 에서 SQL_GENERATION_RULES,
      SQL_VALIDATION_FEEDBACK_SECTION 을 로드하여 서비스에 주입

재시도:
    - sql_retry_count 를 매 호출마다 증가시키며, 검증 노드와의 루프를 통해
      최대 SQL_MAX_RETRY(2) 회까지 재생성을 시도한다.
    - 재생성 시 validation_feedback 을 초기화하여 다음 검증 결과를 새로 수신한다.
"""

from __future__ import annotations

from src.agents.models.user_messages import (
    ERR_SQL_GENERATION,
    format_error,
)
from src.agents.nodes.prompts.system_prompts import (
    SQL_GENERATION_RULES,
    SQL_VALIDATION_FEEDBACK_SECTION,
)
from src.agents.state.state import (
    PipelineState,
    QueryStatus,
    add_trace,
)
from src.services.sql_prompt_assembler import generate_sql
from src.utils.logger import get_logger

logger = get_logger(__name__)


async def generate_sql_node(
    state: PipelineState,
) -> dict:
    """LLM을 사용하여 SQL을 생성한다."""
    retry_count = state.sql_retry_count + 1
    is_retry = retry_count > 1

    logger.info(
        "SQL 생성 시작",
        input=state.preprocessed_input[:80],
        retry_count=retry_count,
        is_retry=is_retry,
    )

    try:
        generated = await generate_sql(
            query=state.preprocessed_input,
            context=state.context,
            normalized_query=state.normalized_query,
            validation_feedback=state.validation_feedback,
            system_prompt=SQL_GENERATION_RULES,
            feedback_template=SQL_VALIDATION_FEEDBACK_SECTION,
        )
    except Exception as e:
        logger.error(
            "SQL 생성 LLM 호출 오류",
            error=str(e),
            retry_count=retry_count,
        )
        return {
            "generated_sql": "",
            "sql_retry_count": retry_count,
            "status": QueryStatus.ERROR,
            "error_message": format_error(ERR_SQL_GENERATION),
        }

    logger.info(
        "SQL 생성 완료",
        sql=generated,
        retry_count=retry_count,
    )

    tables_in_sql = [
        t.table_name
        for t in state.context.table_metas
        if t.table_name.upper() in generated.upper()
    ]
    trace_action = "SQL 재생성" if is_retry else "SQL 생성"
    trace_detail = (
        f"사용 테이블: "
        f"{', '.join(tables_in_sql) if tables_in_sql else '미식별'}"
    )

    return {
        "generated_sql": generated,
        "sql_retry_count": retry_count,
        "sql_validation_errors": [],
        "validation_feedback": "",
        "status": QueryStatus.SQL_GENERATED,
        "trace_log": add_trace(
            state, "SQL생성", trace_action, trace_detail,
        ),
    }
