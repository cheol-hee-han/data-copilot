"""추출된 데이터에 대한 LLM 분석 및 시각화 생성 서비스.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

SQL 실행 결과를 LLM에 전달하여 요약(summary), 인사이트(insights),
통계(statistics)를 포함하는 구조화된 분석 결과를 생성한다.
분석 후 시각화 파이프라인을 통해 차트를 추가한다:
  1. 시각화 판단(judge) — LLM이 데이터 특성에 맞는 차트 유형과 제목을 결정
  2. LLM SVG 생성 — LLM이 직접 SVG 코드를 생성
  3. 템플릿 폴백 — LLM SVG 생성 실패 시 chart_generator의 템플릿 기반 SVG 사용

프롬프트(분석/시각화 판단/SVG 생성)는 호출하는 노드에서 인자로 주입받아,
프롬프트 변경이 서비스 코드 수정 없이 가능하도록 설계되었다.

핵심 함수:
    - analyze_data: 분석 + 시각화를 포함하는 전체 파이프라인 오케스트레이터
    - judge_visualization: LLM에게 차트 유형/제목 판단을 위임 (CHART_TYPE:/CHART_TITLE: 파싱)
    - generate_svg_via_llm: LLM에게 SVG 코드를 직접 생성시키고 유효성 검증
    - build_visualization: SQL 결과로부터 독립적인 VisualizationData를 생성
    - parse_analysis_json: 분석 LLM 응답에서 JSON 추출 및 AnalysisResult 변환
    - parse_viz_judgment: 시각화 판단 응답을 VisualizationType + 제목으로 파싱

fallback 전략: 분석 JSON 파싱 실패 시 원문 텍스트를 summary로 사용한다.
시각화 판단/SVG 생성 실패 시 VisualizationType.NONE 또는 템플릿 폴백으로 처리한다.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable

from src.config import settings
from src.models.enums import VisualizationType
from src.models.result import AnalysisResult, SQLResult, VisualizationData
from src.services.visualization.chart_generator import (
    generate_chart_from_result,
)
from src.utils.llm import (
    ParseError,
    get_llm_client,
    llm_call_with_parse_retry,
)
from src.utils.logger import get_logger
from src.utils.tracker import record_prompt_variables
from src.utils.truncate import truncate_log

logger = get_logger(__name__)


def parse_viz_judgment(
    text: str,
) -> tuple[VisualizationType, str]:
    """시각화 판단 응답을 파싱한다. 실패 시 ValueError."""
    chart_type: VisualizationType | None = None
    chart_title = ""
    for line in text.splitlines():
        line = line.strip()
        if line.upper().startswith("CHART_TYPE:"):
            raw = line.split(":", 1)[1].strip().lower()
            try:
                chart_type = VisualizationType(raw)
            except ValueError:
                pass
        elif line.upper().startswith("CHART_TITLE:"):
            chart_title = line.split(":", 1)[1].strip()

    if chart_type is None:
        raise ValueError(
            f"CHART_TYPE 행을 파싱할 수 없음: {text[:100]}"
        )

    return chart_type, chart_title


def parse_analysis_json(text: str) -> AnalysisResult:
    """분석 JSON 응답을 파싱한다. 실패 시 ValueError."""
    if "```json" in text:
        json_str = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        parts = text.split("```")
        if len(parts) >= 3:
            json_str = parts[1]
        else:
            json_str = text
    elif "{" in text:
        start = text.index("{")
        end = text.rindex("}") + 1
        json_str = text[start:end]
    else:
        json_str = text

    parsed = json.loads(json_str)

    if not isinstance(parsed, dict):
        raise ValueError("JSON 최상위가 dict 가 아님")

    return AnalysisResult(
        summary=parsed.get("summary", ""),
        initial_reading=parsed.get("initial_reading", []),
        insights=parsed.get("insights", []),
        statistics=parsed.get("statistics", {}),
        action_items=parsed.get("action_items", []),
        reasoning_summary=parsed.get("reasoning_summary", ""),
    )


async def judge_visualization(
    data_summary: str,
    *,
    system_prompt: str,
    user_template: str,
) -> tuple[VisualizationType, str]:
    """LLM에게 시각화 필요 여부와 차트 유형을 판단시킨다."""
    user_message = user_template.format(data=data_summary)
    try:
        _, (chart_type, chart_title) = (
            await llm_call_with_parse_retry(
                system=system_prompt,
                messages=[
                    {"role": "user", "content": user_message},
                ],
                parse_fn=parse_viz_judgment,
                max_tokens=100,
                timeout=settings.llm_default_timeout,
                node_name="시각화판단",
            )
        )
        await record_prompt_variables({
            "data_summary": data_summary[:200],
        })
        return chart_type, chart_title
    except ParseError:
        logger.warning(
            "시각화 판단 포맷 파싱 최종 실패, 시각화 건너뜀",
        )
        return VisualizationType.NONE, ""
    except Exception as e:
        logger.warning(
            "시각화 판단 LLM 호출 실패, 시각화 건너뜀",
            error=str(e),
        )
        return VisualizationType.NONE, ""


async def generate_svg_via_llm(
    chart_type: str,
    chart_title: str,
    data_summary: str,
    *,
    system_base: str,
    system_examples: dict[str, str],
    user_template: str,
) -> str:
    """LLM에게 SVG 코드를 직접 생성시킨다.

    chart_type에 해당하는 예제 1개만 system 프롬프트에 주입하여
    전체 토큰을 15K → 약 5K로 줄인다. 매핑되지 않은 chart_type은
    빈 문자열을 반환하여 템플릿 폴백으로 넘어간다.
    """
    example = system_examples.get(chart_type)
    if example is None:
        logger.warning(
            "SVG 예제 미매핑, LLM 호출 건너뜀",
            chart_type=chart_type,
        )
        return ""
    system_prompt = system_base.replace(
        "{example_block}", example,
    )

    client = get_llm_client()
    user_message = user_template.format(
        chart_type=chart_type,
        chart_title=chart_title,
        data=data_summary,
    )
    try:
        response = await client.messages.create(
            model=settings.llm_model,
            max_tokens=settings.llm_svg_max_tokens,
            timeout=settings.llm_long_timeout,
            system=system_prompt,
            messages=[
                {"role": "user", "content": user_message},
            ],
        )
    except Exception as e:
        logger.warning(
            "SVG 생성 LLM 호출 실패", error=str(e),
        )
        return ""

    text = response.content[0].text.strip()

    # LLM이 코드 블록으로 감싸는 경우 내부 내용만 추출
    if "```svg" in text:
        text = (
            text.split("```svg")[1].split("```")[0].strip()
        )
    elif "```xml" in text:
        text = (
            text.split("```xml")[1].split("```")[0].strip()
        )
    elif "```" in text:
        text = (
            text.split("```")[1].split("```")[0].strip()
        )

    if "<svg" not in text or "</svg>" not in text:
        logger.warning("LLM 응답에 유효한 SVG가 없음")
        return ""

    svg_match = re.search(
        r"<svg[\s\S]*?</svg>", text, re.IGNORECASE,
    )
    if not svg_match:
        return ""

    return svg_match.group(0)


async def build_visualization(
    result: SQLResult,
    *,
    viz_judgment_prompt: str,
    viz_judgment_user: str,
    viz_svg_base: str,
    viz_svg_examples: dict[str, str],
    viz_svg_user: str,
    is_cancelled: Callable[[], Awaitable[bool]] | None = None,
) -> VisualizationData:
    """SQL 결과로부터 시각화 데이터를 생성한다."""
    from src.services.response_formatter import (
        format_report_table,
    )

    data_summary = format_report_table(
        result.columns,
        result.rows,
        column_formats={},
        max_rows=settings.analysis_max_rows,
    )

    chart_type, chart_title = await judge_visualization(
        data_summary,
        system_prompt=viz_judgment_prompt,
        user_template=viz_judgment_user,
    )
    if chart_type == VisualizationType.NONE:
        return VisualizationData()

    if is_cancelled and await is_cancelled():
        return VisualizationData()

    logger.info(
        "시각화 생성 시작",
        chart_type=chart_type.value,
        title=chart_title,
    )

    svg_code = await generate_svg_via_llm(
        chart_type.value,
        chart_title,
        data_summary,
        system_base=viz_svg_base,
        system_examples=viz_svg_examples,
        user_template=viz_svg_user,
    )

    if not svg_code:
        logger.info("LLM SVG 생성 실패, 템플릿 폴백 사용")
        svg_code = generate_chart_from_result(
            result, chart_type, chart_title,
        )

    if svg_code:
        logger.info(
            "시각화 생성 완료",
            chart_type=chart_type.value,
        )
        return VisualizationData(
            svg_code=svg_code,
            chart_type=chart_type,
            title=chart_title,
        )

    logger.warning(
        "시각화 생성 실패 (LLM + 템플릿 모두)",
    )
    return VisualizationData()


async def analyze_data(
    user_input: str,
    sql_result: SQLResult,
    *,
    system_prompt: str,
    user_template: str,
    viz_judgment_prompt: str,
    viz_judgment_user: str,
    viz_svg_base: str,
    viz_svg_examples: dict[str, str],
    viz_svg_user: str,
    min_rows_for_viz: int,
    is_cancelled: Callable[[], Awaitable[bool]] | None = None,
) -> tuple[AnalysisResult, VisualizationData]:
    """추출 데이터를 분석하고 시각화를 생성한다.

    Args:
        user_input: 원본 사용자 입력.
        sql_result: SQL 실행 결과.
        system_prompt: 분석 시스템 프롬프트.
        user_template: 분석 유저 프롬프트 템플릿.
        viz_svg_base: SVG 생성 base 프롬프트(예제 슬롯 `{example_block}` 포함).
        viz_svg_examples: chart_type → 예제 본문 매핑.
        viz_svg_user: SVG 생성 유저 프롬프트 템플릿 ({chart_type},{data} 등).
        min_rows_for_viz: 시각화 최소 행 수.

    Returns:
        (AnalysisResult, VisualizationData) 튜플.
    """
    if not sql_result.rows:
        return (
            AnalysisResult(
                summary=(
                    "조회된 데이터가 없어 "
                    "분석을 수행할 수 없습니다."
                ),
                insights=[
                    "데이터 조건을 변경하여 다시 시도해보세요."
                ],
            ),
            VisualizationData(),
        )

    from src.services.response_formatter import (
        format_report_table,
    )

    query_result_str = format_report_table(
        sql_result.columns,
        sql_result.rows,
        column_formats={},
        max_rows=settings.analysis_max_rows,
    )
    user_message = user_template.format(
        user_input=user_input,
        query_result=query_result_str,
    )

    try:
        _, analysis = await llm_call_with_parse_retry(
            system=system_prompt,
            messages=[
                {"role": "user", "content": user_message},
            ],
            parse_fn=parse_analysis_json,
            max_tokens=settings.llm_format_max_tokens,
            timeout=settings.llm_long_timeout,
            node_name="데이터분석",
        )
        await record_prompt_variables({
            "user_input": user_input,
            "query_result": truncate_log(query_result_str),
        })
    except ParseError as e:
        logger.warning(
            "분석 JSON 파싱 최종 실패, 텍스트 폴백 사용",
            last_response=truncate_log(e.last_response),
        )
        analysis = AnalysisResult(
            summary=e.last_response,
            insights=[],
            statistics={},
        )
    except Exception as e:
        logger.error(
            "데이터 분석 LLM 호출 오류", error=str(e),
        )
        raise

    viz = VisualizationData()
    if sql_result.row_count >= min_rows_for_viz:
        if is_cancelled and await is_cancelled():
            return analysis, viz
        viz = await build_visualization(
            sql_result,
            viz_judgment_prompt=viz_judgment_prompt,
            viz_judgment_user=viz_judgment_user,
            viz_svg_base=viz_svg_base,
            viz_svg_examples=viz_svg_examples,
            viz_svg_user=viz_svg_user,
            is_cancelled=is_cancelled,
        )

    return analysis, viz
