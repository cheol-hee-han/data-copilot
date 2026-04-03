"""결과 포맷팅 노드 — 최종 응답을 사용자 친화적 보고서 형태로 정리.

SQL 실행 결과(sql_result)를 LLM 에 전달하여 IT 비전문 사용자가 이해할 수 있는
보고서 형태의 자연어 응답을 생성한다.
컨텍스트 소스 수집 실패가 있었다면 경고 문구를 응답 상단에 추가하고,
파이프라인 전체 추론 과정(trace_log)을 접기(details) 태그로 응답 하단에 첨부한다.

핵심 함수:
    - format_response_node: state.preprocessed_input, state.sql_result,
      state.context.failed_sources, state.trace_log 를 읽어 포맷팅하고
      state.formatted_response 에 기록

위임 구조:
    - 비즈니스 로직: services/response_formatter.py (format_response)
    - 프롬프트: nodes/prompts/system_prompts.py 에서 FORMATTER_SYSTEM,
      FORMATTER_USER 를 로드하여 서비스에 주입

폴백:
    - LLM 호출 실패 시 ERR_FORMATTING 에러 메시지를 formatted_response 에 기록하여
      사용자에게 오류 상황을 안내한다.
"""

from __future__ import annotations

from src.agents.models.user_messages import (
    ERR_FORMATTING,
    format_context_warning,
    format_error,
)
from src.agents.nodes.system_prompts import (
    FORMATTER_USER,
    FORMATTER_SYSTEM,
)
from src.agents.state.state import (
    CodeMeta,
    PipelineState,
    QueryStatus,
    add_trace,
    format_trace_summary,
)
from src.agents.utils.clarification_context import (
    build_auto_resolved_notice,
)
from src.services.response_formatter import format_response
from src.utils.logger import get_logger
from src.utils.sqlglot_analyzer import extract_select_alias_map

logger = get_logger(__name__)


async def format_response_node(
    state: PipelineState,
) -> dict:
    """결과를 사용자 친화적 형태로 포맷팅한다."""
    logger.info("결과 포맷팅 시작")

    # code_map에서 SQL 결과 컬럼에 관련된 코드만 필터링
    code_mappings = _build_code_mappings(
        code_map=state.reason.code_map,
        sql=state.reason.validated_sql,
    )

    try:
        formatted = await format_response(
            user_input=state.preprocessed_input,
            sql_result=state.sql_result,
            system_prompt=FORMATTER_SYSTEM,
            user_template=FORMATTER_USER,
            code_mappings=code_mappings,
        )
    except Exception as e:
        logger.error(
            "결과 포맷팅 LLM 호출 오류", error=str(e),
        )
        return {
            "formatted_response": format_error(
                ERR_FORMATTING,
            ),
            "status": QueryStatus.ERROR,
            "error_message": ERR_FORMATTING,
        }

    # INFER 자동추론 항목 안내 (결과 상단)
    infer_notice = build_auto_resolved_notice(state)
    if infer_notice:
        formatted = f"{infer_notice}\n\n{formatted}"

    # inference_notes 면책 고지 (recovery_agent 추론 포함 시)
    inference_disclaimer = _build_inference_disclaimer(
        state.reason.inference_notes,
        state.reason.is_force_generated,
    )
    if inference_disclaimer:
        formatted = f"{inference_disclaimer}\n\n{formatted}"

    # 컨텍스트 소스 실패 경고
    ctx_warning = format_context_warning(
        state.context.failed_sources,
    )
    if ctx_warning:
        formatted = f"{ctx_warning}\n\n{formatted}"

    # 조회 과정 요약
    trace_summary = format_trace_summary(state)
    if trace_summary:
        formatted += (
            "\n\n<details>\n"
            "<summary>조회 과정 요약</summary>\n\n"
            f"{trace_summary}\n"
            "</details>"
        )

    logger.info(
        "결과 포맷팅 완료",
        response_length=len(formatted),
    )

    return {
        "formatted_response": formatted,
        "status": QueryStatus.FORMATTED,
        "trace_log": add_trace(
            state, "포맷팅",
            "보고서 형태로 결과 정리 완료",
        ),
    }


def _build_inference_disclaimer(
    inference_notes: list[str],
    is_force_generated: bool,
) -> str:
    """inference_notes 기반 면책 고지를 생성한다."""
    if not inference_notes:
        return ""

    if is_force_generated:
        header = "**참고**: 일부 추론을 포함하여 조회하였습니다."
    elif len(inference_notes) >= 3:
        header = "**참고**: 아래 항목은 추론에 기반합니다."
    else:
        header = "**참고**:"

    items = "\n".join(
        f"- {note}" for note in inference_notes
    )
    return f"{header}\n{items}"


_NO_CODE_MAPPINGS = "해당 없음"


def _build_code_mappings(
    code_map: dict[str, CodeMeta],
    sql: str | None,
) -> str:
    """SQL SELECT 컬럼과 관련된 코드값만 필터링하여 프롬프트 텍스트로 직렬화한다."""
    if not code_map or not sql:
        return _NO_CODE_MAPPINGS

    # SELECT alias → 원본 컬럼명 매핑 추출
    alias_map = extract_select_alias_map(sql)

    # SELECT * 등으로 alias 추출 불가 시 code_map 전체를 폴백 직렬화
    if not alias_map:
        return _serialize_code_map(code_map)

    # alias_map에서 원본 컬럼명 수집 (대소문자 무시)
    result_columns: dict[str, str] = {}  # upper(원본컬럼) → alias
    for alias, orig_col in alias_map.items():
        if orig_col:
            result_columns[orig_col.upper()] = alias

    # code_map에서 결과 컬럼과 매칭되는 것만 필터
    filtered: dict[str, CodeMeta] = {}
    for col_name, meta in code_map.items():
        if not meta.codes:
            continue
        if col_name.upper() in result_columns:
            filtered[col_name] = meta

    if not filtered:
        return _NO_CODE_MAPPINGS

    return _serialize_code_map(filtered, result_columns)


def _serialize_code_map(
    code_map: dict[str, CodeMeta],
    display_names: dict[str, str] | None = None,
) -> str:
    """code_map을 프롬프트용 텍스트로 직렬화한다.

    Args:
        code_map: 컬럼명 → CodeMeta 매핑.
        display_names: upper(원본컬럼) → 출력alias 매핑. None이면 컬럼명을 그대로 사용.

    Returns:
        "- 대출구분(LOAN_DCD): 01=정상, 02=연체" 형태의 줄 구분 문자열.
        코드가 없으면 "해당 없음".
    """
    lines: list[str] = []
    for col_name, meta in code_map.items():
        if not meta.codes:
            continue
        display = col_name
        if display_names:
            display = display_names.get(
                col_name.upper(), col_name,
            )
        pairs = ", ".join(
            f"{k}={v}"
            for k, v in list(meta.codes.items())[:20]
        )
        lines.append(f"- {display}({col_name}): {pairs}")

    return "\n".join(lines) if lines else _NO_CODE_MAPPINGS
