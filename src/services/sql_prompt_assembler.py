"""SQL 생성을 위한 프롬프트 조립 및 LLM 호출 서비스.

수집된 컨텍스트(테이블 메타, 보고서 SQL, 과거 SQL, 업무 매뉴얼)와 도메인 사전,
정규화된 질의 구조를 하나의 시스템 프롬프트로 조립하여 LLM에 전달하고,
응답에서 순수 SQL만 추출하여 반환한다. SQL 생성 LLM이 최대한 정확한 쿼리를
작성할 수 있도록, 각 컨텍스트 소스를 구조화된 섹션으로 변환하여 프롬프트에 배치한다.
검증 실패 시 피드백 섹션을 추가하여 재생성을 유도하는 루프도 지원한다.

프롬프트 템플릿(시스템 프롬프트, 피드백 템플릿)은 호출하는 노드에서 인자로 주입받아,
프롬프트 변경이 서비스 코드 수정 없이 가능하도록 설계되었다.

핵심 함수:
    - generate_sql: 프롬프트 조립 → LLM 호출 → SQL 추출의 메인 함수
    - build_table_info: 테이블 메타(컬럼, PII 마킹 포함)를 프롬프트용 문자열로 변환
    - build_past_sqls: 벡터 검색 SQL + 키워드 검색 SQL을 중복 제거 후 병합 (최대 8건)
    - build_report_sqls: 보고서 SQL을 프롬프트용 문자열로 변환 (최대 3건)
    - build_normalization_section: NormalizedQuery의 8-Slot을 자연어 섹션으로 변환
    - clean_sql_response: LLM 응답에서 마크다운 코드 블록을 제거하고 순수 SQL 추출

성능 고려사항: 과거 SQL은 벡터 검색 결과를 우선 배치하고 최대 8건으로 제한하여
프롬프트 토큰 사용량을 관리한다.
"""

from __future__ import annotations

import re
import time

from src.config import settings
from src.models.context import ContextInfo, TableMeta
from src.services.domain.domain_dictionary import (
    format_domain_context,
    lookup_terms,
)
from src.utils.llm import get_llm_client
from src.utils.logger import get_logger

logger = get_logger(__name__)


def build_table_info(table_metas: list[TableMeta]) -> str:
    """컨텍스트에서 테이블 정보를 프롬프트용 문자열로 변환한다."""
    lines = []
    for table in table_metas:
        lines.append(
            f"\n### {table.table_name}"
            f" - {table.table_description}"
        )
        if table.enriched_description:
            lines.append(
                f"[상세 설명] {table.enriched_description}"
            )
        lines.append(f"갱신주기: {table.update_cycle}")
        lines.append("컬럼:")
        for col in table.columns:
            pii_mark = " [PII-마스킹필수]" if col.is_pii else ""
            lines.append(
                f"  - {col.column_name} ({col.data_type}):"
                f" {col.column_description}{pii_mark}"
            )
    return "\n".join(lines)


def build_past_sqls(
    past_sqls: list[str],
    vector_past_sqls: list[str],
) -> str:
    """과거 SQL을 프롬프트용 문자열로 변환한다."""
    seen: set[str] = set()
    merged: list[str] = []

    for sql in vector_past_sqls:
        normalized = sql.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            merged.append(normalized)

    for sql in past_sqls:
        normalized = sql.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            merged.append(normalized)

    if not merged:
        return "(참고할 과거 SQL 없음)"
    return "\n".join(f"- {sql}" for sql in merged[:8])


def build_report_sqls(report_sqls: list[str]) -> str:
    """보고서 SQL을 프롬프트용 문자열로 변환한다."""
    if not report_sqls:
        return "(참고할 보고서 SQL 없음)"
    return "\n".join(
        f"- {sql}" for sql in report_sqls[:3]
    )


def build_manual_refs(manual_references: list[str]) -> str:
    """업무 매뉴얼 참조를 프롬프트용 문자열로 변환한다."""
    if not manual_references:
        return "(참고할 업무 매뉴얼 없음)"
    return "\n".join(manual_references[:3])


def build_domain_terms(domain_terms: dict[str, str]) -> str:
    """도메인 용어를 프롬프트용 문자열로 변환한다."""
    if not domain_terms:
        return "(도메인 용어 없음)"
    return "\n".join(
        f"- {term}: {definition}"
        for term, definition in domain_terms.items()
    )


def build_normalization_section(
    normalized_query: object | None,
) -> str:
    """정규화된 질의 정보를 SQL 생성 프롬프트에 주입할 섹션으로 변환한다."""
    nq = normalized_query
    if nq is None:
        return ""

    lines: list[str] = ["[질의 구조 분석 결과]"]

    intent = getattr(nq, "intent", None)
    if intent:
        primary = getattr(intent, "primary", "")
        secondary = getattr(intent, "secondary", [])
        lines.append(f"- 질의 유형: {primary}")
        if secondary:
            lines.append(
                f"  (부가 유형: {', '.join(secondary)})"
            )

    rewritten = getattr(nq, "rewritten_query", "")
    if rewritten:
        lines.append(f"- 명확화된 질의: {rewritten}")

    entities = getattr(nq, "entities", [])
    if entities:
        ent_strs = []
        for e in entities:
            term = getattr(e, "term", "")
            etype = getattr(e, "type", "")
            ent_strs.append(f"{term}({etype})")
        lines.append(
            f"- 대상 엔티티: {', '.join(ent_strs)}"
        )

    measures = getattr(nq, "measures", [])
    if measures:
        m_strs = []
        for m in measures:
            term = getattr(m, "term", "")
            agg = getattr(m, "agg_function", "")
            note = getattr(m, "note", "")
            s = f"{term}({agg})"
            if note:
                s += f" [{note}]"
            m_strs.append(s)
        lines.append(f"- 측정값: {', '.join(m_strs)}")

    dims = getattr(nq, "dimensions", [])
    if dims:
        d_strs = []
        for d in dims:
            term = getattr(d, "term", "")
            role = getattr(d, "role", "")
            d_strs.append(f"{term}({role})")
        lines.append(f"- 분류 축: {', '.join(d_strs)}")

    filters = getattr(nq, "filters", [])
    if filters:
        f_strs = []
        for f in filters:
            target = getattr(f, "target", "")
            ftype = getattr(f, "filter_type", "")
            values = getattr(f, "values", [])
            s = f"{target} {ftype}"
            if values:
                s += f" {values}"
            f_strs.append(s)
        lines.append(f"- 조건: {', '.join(f_strs)}")

    time_slot = getattr(nq, "time", None)
    if time_slot:
        ttype = getattr(time_slot, "type", "NONE")
        if ttype != "NONE":
            bp = getattr(time_slot, "base_period", None)
            if bp:
                label = getattr(bp, "label", "")
                lines.append(
                    f"- 시간 범위: {ttype} ({label})"
                )

    mods = getattr(nq, "modifiers", [])
    if mods:
        mod_strs = []
        for mod in mods:
            mtype = getattr(mod, "type", "")
            direction = getattr(mod, "direction", "")
            limit = getattr(mod, "limit", None)
            s = mtype
            if direction:
                s += f" {direction}"
            if limit:
                s += f" (상위 {limit}건)"
            mod_strs.append(s)
        lines.append(
            f"- 결과 가공: {', '.join(mod_strs)}"
        )

    oh = getattr(nq, "output_hint", None)
    if oh:
        fmt = getattr(oh, "format", "NONE")
        if fmt != "NONE":
            doc_type = getattr(oh, "doc_type", "")
            cols = getattr(oh, "expected_columns", [])
            lines.append(f"- 출력 형식: {fmt}")
            if doc_type:
                lines.append(f"  문서 유형: {doc_type}")
            if cols:
                lines.append(
                    f"  기대 컬럼: {', '.join(cols)}"
                )

    ambiguities = getattr(nq, "ambiguities", [])
    if ambiguities:
        for a in ambiguities:
            lines.append(f"- [주의] {a}")

    if len(lines) <= 1:
        return ""

    return "\n".join(lines) + "\n"


def clean_sql_response(raw: str) -> str:
    """LLM 응답에서 마크다운 코드 블록을 제거하고 순수 SQL만 추출한다."""
    if "```" not in raw:
        return raw.strip()

    lines = raw.split("\n")
    cleaned: list[str] = []
    in_block = False
    for line in lines:
        if line.strip().startswith("```"):
            in_block = not in_block
            continue
        if in_block:
            cleaned.append(line)
    return "\n".join(cleaned).strip()


async def generate_sql(
    query: str,
    context: ContextInfo,
    normalized_query: object | None,
    validation_feedback: str,
    *,
    system_prompt: str,
    feedback_template: str,
) -> str:
    """LLM을 사용하여 SQL을 생성한다.

    Args:
        query: 전처리된 사용자 입력.
        context: 수집된 컨텍스트.
        normalized_query: 정규화된 질의 (없을 수 있음).
        validation_feedback: 이전 검증 실패 피드백 (재생성 시).
        system_prompt: SQL 생성 시스템 프롬프트 ({table_info} 등 플레이스홀더).
        feedback_template: 검증 피드백 섹션 템플릿 ({feedback} 플레이스홀더).

    Returns:
        생성된 SQL 문자열.
    """
    matched_terms = lookup_terms(query)
    domain_context = format_domain_context(matched_terms)

    normalization_section = build_normalization_section(
        normalized_query,
    )

    feedback_section = ""
    if validation_feedback:
        feedback_section = feedback_template.format(
            feedback=validation_feedback,
        )

    assembled = system_prompt.format(
        table_info=build_table_info(context.table_metas),
        report_sqls=build_report_sqls(context.report_sqls),
        past_sqls=build_past_sqls(
            context.past_sqls, context.vector_past_sqls,
        ),
        manual_refs=build_manual_refs(
            context.manual_references,
        ),
        domain_context=domain_context,
        domain_terms=build_domain_terms(context.domain_terms),
        validation_feedback_section=feedback_section,
    )

    if normalization_section:
        assembled += "\n" + normalization_section

    table_guide = context.table_disambiguation_guide
    if table_guide:
        assembled += "\n" + table_guide

    client = get_llm_client()
    llm_start = time.perf_counter()

    response = await client.messages.create(
        model=settings.llm_model,
        max_tokens=settings.llm_format_max_tokens,
        timeout=settings.llm_long_timeout,
        system=assembled,
        messages=[
            {"role": "user", "content": query},
        ],
    )

    llm_elapsed = (time.perf_counter() - llm_start) * 1000
    logger.info(
        "LLM 호출 완료",
        node="SQL생성",
        model=settings.llm_model,
        latency_ms=round(llm_elapsed, 1),
    )

    if not response.content:
        raise ValueError("SQL 생성 LLM 응답이 비어있음")

    return clean_sql_response(response.content[0].text)
