"""SQL 실행 결과를 IT 비전문가 대상 보고서 형태로 포맷팅하는 서비스.

은행 일반 직원이 SQL이나 DB 개념을 몰라도 결과를 이해할 수 있도록,
LLM을 활용하여 조회 결과를 자연어 보고서로 변환한다.
금액은 만원/억원 단위, 날짜는 "2024년 3월" 형태, 비율은 % 등
사용자 친화적 포맷으로 재구성하며, SQL 자체는 노출하지 않는다.

프롬프트(시스템/유저 템플릿)는 호출하는 노드에서 인자로 주입받아,
프롬프트 변경이 서비스 코드 수정 없이 가능하도록 설계되었다.

핵심 함수:
    - format_response: LLM 호출을 통한 보고서 형태 포맷팅 메인 함수
    - format_result_for_prompt: SQLResult를 LLM 프롬프트에 주입할 텍스트로 변환 (최대 행 수 제한)

성능 고려사항: format_result_for_prompt에서 settings.format_max_rows로 프롬프트에
포함할 최대 행 수를 제한하여 토큰 사용량을 관리한다.
"""

from __future__ import annotations

import time

from src.config import settings
from src.models.result import SQLResult
from src.utils.llm import get_llm_client
from src.utils.logger import get_logger

logger = get_logger(__name__)


def format_result_for_prompt(
    sql_result: SQLResult,
    max_rows: int | None = None,
) -> str:
    """SQL 결과를 프롬프트용 문자열로 변환한다."""
    if max_rows is None:
        max_rows = settings.format_max_rows
    if not sql_result.rows:
        return "(조회 결과 없음)"

    lines = [f"컬럼: {', '.join(sql_result.columns)}"]
    for row in sql_result.rows[:max_rows]:
        lines.append(str(row))
    lines.append(f"\n총 {sql_result.row_count}건 조회됨")
    return "\n".join(lines)


async def format_response(
    user_input: str,
    sql_result: SQLResult,
    *,
    system_prompt: str,
    user_template: str,
) -> str:
    """LLM을 사용하여 결과를 보고서 형태로 포맷팅한다.

    Args:
        user_input: 원본 사용자 입력.
        sql_result: SQL 실행 결과.
        system_prompt: 포맷팅 시스템 프롬프트.
        user_template: 유저 프롬프트 템플릿.

    Returns:
        포맷팅된 응답 문자열.
    """
    user_message = user_template.format(
        user_input=user_input,
        query_result=format_result_for_prompt(sql_result),
    )

    client = get_llm_client()
    llm_start = time.perf_counter()

    response = await client.messages.create(
        model=settings.llm_model,
        max_tokens=settings.llm_format_max_tokens,
        timeout=settings.llm_long_timeout,
        system=system_prompt,
        messages=[
            {"role": "user", "content": user_message},
        ],
    )

    llm_elapsed = (
        (time.perf_counter() - llm_start) * 1000
    )
    logger.info(
        "LLM 호출 완료",
        node="포맷팅",
        model=settings.llm_model,
        latency_ms=round(llm_elapsed, 1),
    )

    return response.content[0].text.strip()
