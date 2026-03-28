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
from typing import Any

from src.config import settings
from src.models.result import SQLResult
from src.utils.llm import get_llm_client
from src.utils.logger import get_logger
from src.utils.tracker import record_prompt_variables

logger = get_logger(__name__)


def rows_to_markdown_table(
    columns: list[str],
    rows: list[dict[str, Any]],
    max_rows: int = 100,
) -> str:
    """dict 행 목록을 markdown table 문자열로 변환한다.

    숫자는 천 단위 쉼표를 추가하고, None은 빈 문자열로 표시한다.
    """
    if not columns or not rows:
        return "(데이터 없음)"

    def _fmt(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, float):
            if value == int(value):
                return f"{int(value):,}"
            return f"{value:,.2f}"
        if isinstance(value, int):
            return f"{value:,}"
        return str(value)

    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join(
        "---" for _ in columns
    ) + " |"
    body_lines: list[str] = []
    for row in rows[:max_rows]:
        cells = [_fmt(row.get(col, "")) for col in columns]
        body_lines.append("| " + " | ".join(cells) + " |")

    table = "\n".join([header, separator, *body_lines])
    total = len(rows)
    if total > max_rows:
        table += f"\n\n(총 {total}건 중 상위 {max_rows}건 표시)"
    else:
        table += f"\n\n(총 {total}건)"
    return table


def format_result_for_prompt(
    sql_result: SQLResult,
    max_rows: int | None = None,
) -> str:
    """SQL 결과를 markdown table 형태의 프롬프트용 문자열로 변환한다."""
    if max_rows is None:
        max_rows = settings.format_max_rows
    if not sql_result.rows:
        return "(조회 결과 없음)"

    return rows_to_markdown_table(
        sql_result.columns,
        sql_result.rows,
        max_rows=max_rows,
    )


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

    result_text = format_result_for_prompt(sql_result)
    record_prompt_variables({
        "user_input": user_input,
        "query_result": result_text[:300] + "..." if len(result_text) > 300 else result_text,
    })

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
