"""테이블 설명 보강 모듈.

테이블 메타의 설명(table_description)이 불충분할 경우,
컬럼 정보·보고서 SQL·과거 SQL 등 보조 정보를 활용하여
LLM이 다음 세 가지 관점의 설명을 생성한다:

    1. 엔티티 집합 정의 — 테이블에 어떤 데이터가 있는지
    2. 기능적 정의 — 데이터가 어디에 어떻게 쓰이는지
    3. 데이터 발생규칙 — 데이터가 언제 생성되어 적재되는지

충분성 판단 기준:
    - 설명이 비어 있거나 20자(문자 수 기준) 미만이면 불충분
    - 세 가지 관점(엔티티/기능/발생규칙) 중 빠진 것이 있으면 불충분

핵심 함수:
    - enrich_table_descriptions: 테이블 목록에서 불충분한 설명을 가진 것만 골라 병렬 보강
    - is_description_sufficient: 길이 + 3관점 키워드 커버리지 기반 충분성 판단
    - _enrich_single_table: 단일 테이블의 보강 설명을 LLM으로 생성
    - _enrich_with_semaphore: Semaphore로 동시 LLM 호출 수를 제한하여 rate limit 방어

fallback 전략: 보강 LLM 호출이 실패하거나 파싱 검증에 실패하면 원본 설명을 유지한다.
"""

from __future__ import annotations

import asyncio

from src.config import settings
from src.models.context import TableMeta
from src.utils.llm import ParseError, llm_call_with_parse_retry
from src.utils.logger import get_logger

logger = get_logger(__name__)

# 충분한 설명으로 판단하기 위한 최소 길이 (문자 수 기준, 한글·영문 동일)
_MIN_DESCRIPTION_LENGTH = settings.min_description_length

# 동시 LLM 호출 최대 수 — API rate limit 방어
_LLM_CONCURRENCY_LIMIT = settings.llm_concurrency_limit

# 세 가지 관점 키워드 — 하나라도 포함되어 있으면 해당 관점이 존재한다고 판단
_ASPECT_KEYWORDS: dict[str, list[str]] = {
    "entity": ["데이터", "정보", "내역", "이력", "목록", "집합", "관리", "저장"],
    "functional": ["사용", "활용", "조회", "분석", "참조", "산출", "보고", "집계"],
    "generation": ["생성", "적재", "배치", "갱신", "업데이트", "실시간", "발생", "수집"],
}


def _count_covered_aspects(description: str) -> int:
    """설명에서 커버되는 관점 수를 반환한다 (최대 3)."""
    covered = 0
    for aspect_keywords in _ASPECT_KEYWORDS.values():
        if any(kw in description for kw in aspect_keywords):
            covered += 1
    return covered


def is_description_sufficient(table: TableMeta) -> bool:
    """테이블 설명이 세 가지 관점을 충분히 커버하는지 판단한다.

    판단 기준 (두 조건 모두 충족해야 충분):
        1. 설명 길이가 _MIN_DESCRIPTION_LENGTH 이상
        2. 세 가지 관점(엔티티/기능/발생규칙) 모두 커버

    enriched_description이 이미 채워져 있으면 그것을 기준으로 판단한다.
    """
    desc = (
        table.enriched_description
        if table.enriched_description
        else table.table_description
    )
    if len(desc) < _MIN_DESCRIPTION_LENGTH:
        return False
    return _count_covered_aspects(desc) >= 3


def _build_column_summary(table: TableMeta) -> str:
    """컬럼 정보를 보강 프롬프트용 문자열로 변환한다."""
    lines: list[str] = []
    for col in table.columns:
        pk_mark = " [PK]" if getattr(col, "is_pk", False) else ""
        pii_mark = " [PII]" if col.is_pii else ""
        lines.append(
            f"  - {col.column_name} ({col.data_type}):"
            f" {col.column_description}{pk_mark}{pii_mark}"
        )
    return "\n".join(lines) if lines else "(컬럼 정보 없음)"


def _find_related_sqls(
    table_name: str,
    report_sqls: list[str],
    past_sqls: list[str],
) -> str:
    """해당 테이블을 참조하는 보고서 SQL과 과거 SQL을 추출한다."""
    related: list[str] = []
    table_upper = table_name.upper()
    for sql in report_sqls:
        if table_upper in sql.upper():
            sanitized = sql[:200].replace("\n", " ").strip()
            related.append(f"  [보고서] {sanitized}")
    for sql in past_sqls:
        if table_upper in sql.upper():
            sanitized = sql[:200].replace("\n", " ").strip()
            related.append(f"  [과거SQL] {sanitized}")
    return "\n".join(related[:5]) if related else "(관련 SQL 없음)"


def _validate_enrichment(text: str) -> str:
    """보강 설명이 최소 품질을 충족하는지 검증한다. 실패 시 ValueError."""
    if len(text.strip()) < _MIN_DESCRIPTION_LENGTH:
        raise ValueError(f"보강 설명이 너무 짧음 ({len(text.strip())}자)")
    return text.strip()


async def _enrich_single_table(
    table: TableMeta,
    report_sqls: list[str],
    past_sqls: list[str],
    prompt_template: str = "",
    system_prompt: str = "",
    format_hint: str = "",
) -> str:
    """단일 테이블의 보강 설명을 LLM으로 생성한다."""
    column_summary = _build_column_summary(table)
    related_sqls = _find_related_sqls(
        table.table_name, report_sqls, past_sqls,
    )

    prompt = prompt_template.format(
        table_name=table.table_name,
        original_description=(
            table.table_description or "(설명 없음)"
        ),
        update_cycle=(
            table.update_cycle or "(갱신주기 미상)"
        ),
        column_summary=column_summary,
        related_sqls=related_sqls,
    )

    try:
        _, enriched = await llm_call_with_parse_retry(
            system=system_prompt,
            messages=[
                {"role": "user", "content": prompt},
            ],
            parse_fn=_validate_enrichment,
            format_hint=format_hint,
            max_tokens=500,
            timeout=settings.llm_default_timeout,
            node_name=(
                f"테이블보강({table.table_name})"
            ),
        )
        return enriched
    except ParseError:
        logger.warning(
            "테이블 설명 보강 파싱 최종 실패, 원본 설명 유지",
            table=table.table_name,
        )
        return ""
    except Exception as e:
        logger.warning(
            "테이블 설명 보강 LLM 호출 실패, 원본 설명 유지",
            table=table.table_name,
            error=str(e),
        )
        return ""


async def _enrich_with_semaphore(
    sem: asyncio.Semaphore,
    table: TableMeta,
    report_sqls: list[str],
    past_sqls: list[str],
    prompt_template: str = "",
    system_prompt: str = "",
    format_hint: str = "",
) -> str:
    """Semaphore 로 동시 LLM 호출 수를 제한한다."""
    async with sem:
        return await _enrich_single_table(
            table, report_sqls, past_sqls,
            prompt_template=prompt_template,
            system_prompt=system_prompt,
            format_hint=format_hint,
        )


async def enrich_table_descriptions(
    tables: list[TableMeta],
    report_sqls: list[str] | None = None,
    past_sqls: list[str] | None = None,
    prompt_template: str = "",
    system_prompt: str = "",
    format_hint: str = "",
) -> list[TableMeta]:
    """불충분한 설명을 가진 테이블들의 설명을 보강한다.

    이미 충분한 설명이 있는 테이블은 건너뛴다.
    보강에 실패한 테이블은 원본 설명을 유지한다.
    동시 LLM 호출은 _LLM_CONCURRENCY_LIMIT 로 제한한다.

    Args:
        tables: 테이블 메타 목록.
        report_sqls: 보고서 SQL 목록 (보조 정보로 활용).
        past_sqls: 과거 SQL 목록 (보조 정보로 활용).

    Returns:
        enriched_description 이 채워진 테이블 메타 목록.
    """
    if not tables:
        return tables

    report_sqls = report_sqls or []
    past_sqls = past_sqls or []

    tables_to_enrich: list[tuple[int, TableMeta]] = []
    for idx, table in enumerate(tables):
        if not is_description_sufficient(table):
            tables_to_enrich.append((idx, table))

    if not tables_to_enrich:
        logger.info("모든 테이블 설명이 충분함, 보강 생략")
        return tables

    logger.info(
        "테이블 설명 보강 시작",
        total=len(tables),
        to_enrich=len(tables_to_enrich),
        targets=[t.table_name for _, t in tables_to_enrich],
    )

    # Semaphore 로 동시 LLM 호출 수 제한
    sem = asyncio.Semaphore(_LLM_CONCURRENCY_LIMIT)
    tasks = [
        _enrich_with_semaphore(
            sem, table, report_sqls, past_sqls,
            prompt_template=prompt_template,
            system_prompt=system_prompt,
            format_hint=format_hint,
        )
        for _, table in tables_to_enrich
    ]
    results = await asyncio.gather(*tasks)

    for (idx, table), enriched_desc in zip(tables_to_enrich, results):
        if enriched_desc:
            tables[idx].enriched_description = enriched_desc
            logger.info(
                "테이블 설명 보강 완료",
                table=table.table_name,
                enriched_length=len(enriched_desc),
            )
        else:
            logger.warning(
                "테이블 설명 보강 실패, 원본 유지",
                table=table.table_name,
            )

    return tables
