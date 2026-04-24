"""추출된 데이터에 대한 LLM 분석 및 시각화 생성 서비스.

작성자: 한철희 / 최종수정: 2026-04-16

SQL 실행 결과를 LLM에 전달하여 요약(summary), 인사이트(insights),
통계(statistics)를 포함하는 구조화된 분석 결과를 생성한다.
시각화 파이프라인(판단 → SVG 생성 → 템플릿 폴백)은 독립 함수로 제공되며,
visualizer 노드에서 직접 호출한다:
  1. 시각화 판단(judge) — LLM이 데이터 특성에 맞는 차트 유형과 제목을 결정
  2. LLM SVG 생성 — LLM이 직접 SVG 코드를 생성
  3. 템플릿 폴백 — LLM SVG 생성 실패 시 chart_generator의 템플릿 기반 SVG 사용

프롬프트(분석/시각화 판단/SVG 생성)는 호출하는 노드에서 인자로 주입받아,
프롬프트 변경이 서비스 코드 수정 없이 가능하도록 설계되었다.

핵심 함수:
    - analyze_data: LLM 기반 분석 (analyzer 노드에서 호출)
    - build_visualization: SQL 결과로부터 시각화 생성 (visualizer 노드에서 호출)
    - judge_visualization: LLM에게 차트 유형/제목 판단을 위임
    - generate_svg_via_llm: LLM에게 SVG 코드를 직접 생성시키고 유효성 검증
    - parse_analysis_json: 분석 LLM 응답에서 JSON 추출 및 AnalysisResult 변환
    - parse_viz_judgment: 시각화 판단 JSON 응답을 VisualizationType + 제목 + 사유로 파싱

fallback 전략: 분석 JSON 파싱 실패 시 원문 텍스트를 summary로 사용한다.
시각화 판단/SVG 생성 실패 시 VisualizationType.NONE 또는 템플릿 폴백으로 처리한다.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable

from src.agents.utils.handoff import normalize_handoff_note
from src.config import settings
from src.models.enums import VisualizationType
from src.models.result import AnalysisResult, SQLResult, VisualizationData
from src.services.visualization.chart_generator import (
    generate_chart_from_result,
)
from src.agents.nodes.thinking_modes import LLMNode
from src.utils.llm import (
    ParseError,
    get_llm_client,
    llm_call_with_parse_retry,
)
from src.utils.llm.retry import llm_stream_with_parse_retry
from src.utils.logger import get_logger
from src.utils.llm.prompt import render_prompt
from src.utils.tracker import (
    LLMInteraction,
    llm_failure_sentinel,
    llm_skip_sentinel,
)
from src.utils.truncate import truncate_log

logger = get_logger(__name__)


def parse_viz_judgment(
    text: str,
) -> tuple[VisualizationType, str, str]:
    """시각화 판단 JSON 응답을 파싱한다. 실패 시 ValueError.

    Returns:
        (chart_type, chart_title, reason) 3-tuple.
    """
    from src.utils.llm.response import extract_json

    data = extract_json(text, strict=True)
    assert data is not None  # strict=True 보장

    raw_type = str(data.get("chart_type", "")).strip().lower()
    if not raw_type:
        raise ValueError(
            f"chart_type 필드가 비어 있음: {text[:200]}"
        )

    try:
        chart_type = VisualizationType(raw_type)
    except ValueError:
        raise ValueError(
            f"허용되지 않는 chart_type: {raw_type}"
        )

    chart_title = str(data.get("chart_title", "")).strip()
    reason = str(data.get("reason", "")).strip()

    return chart_type, chart_title, reason


_MD_SECTION_RE = re.compile(
    r"^##[ \t]+(핵심 요약|데이터 현황|분석 인사이트|후속 조치)[ \t]*$",
    re.MULTILINE,
)
_MD_BULLET_RE = re.compile(
    r"^[ \t]*(?:[-*]|\d+\.)[ \t]+(.+?)[ \t]*$",
    re.MULTILINE,
)


def _split_markdown_sections(text: str) -> dict[str, str]:
    """4개 섹션 본문을 {섹션명: 본문} dict 로 분리."""
    matches = list(_MD_SECTION_RE.finditer(text))
    sections: dict[str, str] = {}
    for i, m in enumerate(matches):
        name = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections[name] = text[start:end].strip()
    return sections


def _extract_bullets(body: str) -> list[str]:
    """섹션 본문에서 불릿 항목만 추출한다 (빈 항목 제거)."""
    items = [
        m.group(1).strip()
        for m in _MD_BULLET_RE.finditer(body)
    ]
    return [x for x in items if x]


def parse_analysis_markdown(text: str) -> AnalysisResult:
    """분석 응답(마크다운 4섹션)을 AnalysisResult로 파싱한다.

    섹션 구성::

        ## 핵심 요약      → summary (str, 문단)
        ## 데이터 현황    → initial_reading (list[str], 불릿)
        ## 분석 인사이트  → insights (list[str], 불릿)
        ## 후속 조치      → action_items (list[str], 불릿)

    실패 시 ``ValueError`` 를 올린다. ``statistics`` 와
    ``reasoning_summary`` 는 Markdown 경로에서 생성하지 않는다.
    """
    sections = _split_markdown_sections(text)
    if "핵심 요약" not in sections:
        raise ValueError("'## 핵심 요약' 섹션을 찾을 수 없음")

    return AnalysisResult(
        summary=sections.get("핵심 요약", "").strip(),
        initial_reading=_extract_bullets(sections.get("데이터 현황", "")),
        insights=_extract_bullets(sections.get("분석 인사이트", "")),
        statistics={},
        action_items=_extract_bullets(sections.get("후속 조치", "")),
        reasoning_summary="",
    )


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
    handoff_note: str = "",
    user_input: str = "",
) -> tuple[VisualizationType, str, str, LLMInteraction]:
    """LLM에게 시각화 필요 여부와 차트 유형을 판단시킨다.

    Args:
        handoff_note: CONTINUE 오케스트레이터의 연속 처리 지시 (REDISPLAY 등).
            빈 문자열이면 `(없음)`으로 프롬프트에 주입되어 기존 데이터 특성
            기반 판단으로 동작한다. 내용이 있으면 `### 시각화/포맷 지시`
            섹션을 우선 반영한다.
        user_input: 사용자의 원 질의(rewrite 된 경우 ``analysis_query``).
            "파이차트로 보여줘" 등 명시적 시각화 지시를 판단에 반영하기 위함.

    Returns:
        (chart_type, chart_title, reason, LLMInteraction) 4-tuple.
        판단 실패 시 chart_type=NONE, raw_response 에 실패 사유 기록.
    """
    handoff_note_text = normalize_handoff_note(handoff_note)
    user_input_text = user_input.strip() or "(없음)"
    user_message, prompt_vars = render_prompt(user_template, {
        "{data}": data_summary,
        "{handoff_note}": handoff_note_text,
        "{user_input}": user_input_text,
    })
    try:
        raw_text, (chart_type, chart_title, reason) = (
            await llm_call_with_parse_retry(
                system=system_prompt,
                messages=[
                    {"role": "user", "content": user_message},
                ],
                parse_fn=parse_viz_judgment,
                max_tokens=256,
                timeout=settings.llm_default_timeout,
                node_name=LLMNode.VISUALIZER_JUDGMENT,
            )
        )
        return (
            chart_type,
            chart_title,
            reason,
            LLMInteraction(
                prompt_variables=prompt_vars,
                raw_response=raw_text,
            ),
        )
    except ParseError as e:
        logger.warning(
            "시각화 판단 포맷 파싱 최종 실패, 시각화 건너뜀",
        )
        return (
            VisualizationType.NONE,
            "",
            "",
            LLMInteraction(
                prompt_variables=prompt_vars,
                raw_response=(
                    e.last_response
                    or llm_failure_sentinel("판단 ParseError", e)
                ),
            ),
        )
    except Exception as e:
        logger.warning(
            "시각화 판단 LLM 호출 실패, 시각화 건너뜀",
            error=str(e),
        )
        return (
            VisualizationType.NONE,
            "",
            "",
            LLMInteraction(
                prompt_variables=prompt_vars,
                raw_response=llm_failure_sentinel(
                    "판단 LLM 실패", e,
                ),
            ),
        )


_FENCE_SVG = "```svg"
_FENCE_XML = "```xml"
_FENCE_PLAIN = "```"


def _strip_code_fence(text: str) -> str:
    """LLM 응답의 코드 펜스(```svg / ```xml / ```) 내부 내용만 꺼낸다."""
    for marker in (_FENCE_SVG, _FENCE_XML, _FENCE_PLAIN):
        if marker in text:
            return text.split(marker, 1)[1].split(_FENCE_PLAIN, 1)[0].strip()
    return text


def _extract_svg_block(text: str) -> str:
    """LLM 응답에서 ``<svg>...</svg>`` 블록을 추출한다.

    코드 펜스를 먼저 벗겨내고, 정규식으로 SVG 블록을 찾는다. 추출 실패 시
    ``ValueError`` 를 올린다.
    """
    cleaned = _strip_code_fence(text.strip())
    if "<svg" not in cleaned or "</svg>" not in cleaned:
        raise ValueError("유효한 SVG 블록을 찾을 수 없음")
    svg_match = re.search(
        r"<svg[\s\S]*?</svg>", cleaned, re.IGNORECASE,
    )
    if not svg_match:
        raise ValueError("SVG 정규식 매칭 실패")
    return svg_match.group(0)


async def _generate_svg_streaming(
    *,
    system_prompt: str,
    user_message: str,
    turn_id: str,
    is_cancelled: Callable[[], Awaitable[bool]] | None,
) -> str:
    """스트리밍 경로로 SVG 를 생성한다. 실패 시 빈 문자열."""
    try:
        _, svg = await llm_stream_with_parse_retry(
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
            parse_fn=_extract_svg_block,
            turn_id=turn_id,
            part_id="svg_0",
            part_type="svg",
            max_tokens=settings.llm_svg_max_tokens,
            timeout=settings.llm_long_timeout,
            node_name=LLMNode.VISUALIZER_SVG,
            is_cancelled=is_cancelled,
        )
        return svg
    except ParseError:
        logger.warning("SVG 스트리밍 파싱 실패, 템플릿 폴백 예정")
        return ""
    except Exception as e:
        logger.warning("SVG 스트리밍 호출 실패", error=str(e))
        return ""


async def generate_svg_via_llm(
    chart_type: str,
    chart_title: str,
    data_summary: str,
    *,
    system_base: str,
    system_examples: dict[str, str],
    user_template: str,
    streaming_enabled: bool = False,
    turn_id: str = "",
    is_cancelled: Callable[[], Awaitable[bool]] | None = None,
    user_input: str = "",
) -> str:
    """LLM에게 SVG 코드를 직접 생성시킨다.

    chart_type에 해당하는 예제 1개만 system 프롬프트에 주입하여
    전체 토큰을 15K → 약 5K로 줄인다. 매핑되지 않은 chart_type은
    빈 문자열을 반환하여 템플릿 폴백으로 넘어간다.

    ``streaming_enabled=True`` 이고 ``turn_id`` 가 있으면 ``llm.delta.*`` 이벤트를
    뿌리며 스트리밍한다. 그 외에는 단일 호출 경로.
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

    user_message = user_template.format(
        chart_type=chart_type,
        chart_title=chart_title,
        data=data_summary,
        user_input=user_input.strip() or "(없음)",
    )

    if streaming_enabled and turn_id:
        return await _generate_svg_streaming(
            system_prompt=system_prompt,
            user_message=user_message,
            turn_id=turn_id,
            is_cancelled=is_cancelled,
        )

    client = get_llm_client()
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
    try:
        return _extract_svg_block(text)
    except ValueError as e:
        logger.warning("LLM 응답에 유효한 SVG가 없음", reason=str(e))
        return ""


async def build_visualization(
    result: SQLResult,
    *,
    viz_judgment_prompt: str,
    viz_judgment_user: str,
    viz_svg_base: str,
    viz_svg_examples: dict[str, str],
    viz_svg_user: str,
    is_cancelled: Callable[[], Awaitable[bool]] | None = None,
    streaming_enabled: bool = False,
    turn_id: str = "",
    handoff_note: str = "",
    user_input: str = "",
) -> tuple[VisualizationData, LLMInteraction]:
    """SQL 결과로부터 시각화 데이터를 생성한다.

    Args:
        handoff_note: CONTINUE 오케스트레이터의 연속 처리 지시.
            judge_visualization 단계에만 전달되며, SVG 생성 단계는
            chart_type 확정 후 결정적 단계이므로 주입하지 않는다 (§7.1).
        user_input: 사용자의 원 질의(rewrite 된 경우 ``analysis_query``).
            judge 단계는 차트 유형 판단, SVG 단계는 스타일·강조·라벨 지시
            반영을 위해 양 단계 모두 주입한다.

    Returns:
        (VisualizationData, LLMInteraction) — LLMInteraction 은 판단(judge)
        단계의 프롬프트 변수 / raw 응답. REASONING_STEP 디스패치 입력.
    """
    from src.services.response_formatter import (
        format_report_table,
    )

    data_summary = format_report_table(
        result.columns,
        result.rows,
        column_formats={},
        max_rows=settings.analysis_max_rows,
    )

    chart_type, chart_title, reason, judge_interaction = (
        await judge_visualization(
            data_summary,
            system_prompt=viz_judgment_prompt,
            user_template=viz_judgment_user,
            handoff_note=handoff_note,
            user_input=user_input,
        )
    )
    if chart_type == VisualizationType.NONE:
        return (
            VisualizationData(judgment_reason=reason),
            judge_interaction,
        )

    if is_cancelled and await is_cancelled():
        return VisualizationData(), judge_interaction

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
        streaming_enabled=streaming_enabled,
        turn_id=turn_id,
        is_cancelled=is_cancelled,
        user_input=user_input,
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
        return (
            VisualizationData(
                svg_code=svg_code,
                chart_type=chart_type,
                title=chart_title,
                judgment_reason=reason,
            ),
            judge_interaction,
        )

    logger.warning(
        "시각화 생성 실패 (LLM + 템플릿 모두)",
    )
    return VisualizationData(), judge_interaction


async def analyze_data(
    user_input: str,
    sql_result: SQLResult,
    *,
    system_prompt: str,
    user_template: str,
    is_cancelled: Callable[[], Awaitable[bool]] | None = None,
    streaming_enabled: bool = False,
    turn_id: str = "",
    handoff_note: str = "",
) -> tuple[AnalysisResult, bool, LLMInteraction]:
    """추출 데이터를 분석한다 (시각화는 visualizer 노드에서 별도 수행).

    Args:
        user_input: 원본 사용자 입력.
        sql_result: SQL 실행 결과.
        system_prompt: 분석 시스템 프롬프트.
        user_template: 분석 유저 프롬프트 템플릿.
        handoff_note: CONTINUE 오케스트레이터의 연속 처리 지시
            (ANALYZE/REFINE 경로). 빈 문자열이면 `(없음)`으로 주입된다.

    Returns:
        (AnalysisResult, streaming_delivered, LLMInteraction) 튜플.
        LLM 미호출(데이터 없음)인 경우 LLMInteraction 은 비어 있는 값을 반환.
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
            False,
            LLMInteraction(
                prompt_variables={},
                raw_response=llm_skip_sentinel(
                    "데이터 없음 — LLM 호출 생략",
                ),
            ),
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
    handoff_note_text = normalize_handoff_note(handoff_note)
    user_message, prompt_vars = render_prompt(user_template, {
        "{user_input}": user_input,
        "{query_result}": query_result_str,
        "{handoff_note}": handoff_note_text,
    })

    parse_fn = (
        parse_analysis_markdown
        if settings.analyzer_output_format == "markdown"
        else parse_analysis_json
    )
    # 스트리밍 경로는 Markdown 포맷 + turn_id 가 있을 때만 활성화
    use_streaming = (
        streaming_enabled
        and bool(turn_id)
        and settings.analyzer_output_format == "markdown"
    )
    delivered = False
    raw_text = ""
    try:
        if use_streaming:
            raw_text, analysis = await llm_stream_with_parse_retry(
                system=system_prompt,
                messages=[
                    {"role": "user", "content": user_message},
                ],
                parse_fn=parse_fn,
                turn_id=turn_id,
                part_id="analysis_0",
                part_type="analysis",
                max_tokens=settings.llm_format_max_tokens,
                timeout=settings.llm_long_timeout,
                node_name=LLMNode.ANALYZER,
                is_cancelled=is_cancelled,
            )
            delivered = True
        else:
            raw_text, analysis = await llm_call_with_parse_retry(
                system=system_prompt,
                messages=[
                    {"role": "user", "content": user_message},
                ],
                parse_fn=parse_fn,
                max_tokens=settings.llm_format_max_tokens,
                timeout=settings.llm_long_timeout,
                node_name=LLMNode.ANALYZER,
            )
    except ParseError as e:
        logger.warning(
            "분석 응답 파싱 최종 실패, 텍스트 폴백 사용",
            last_response=truncate_log(e.last_response),
        )
        raw_text = e.last_response
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

    return (
        analysis,
        delivered,
        LLMInteraction(
            prompt_variables=prompt_vars,
            raw_response=raw_text,
        ),
    )
