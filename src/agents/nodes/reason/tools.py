"""탐색 도구 래퍼 — 기존 커넥터/서비스를 에이전틱 코어에서 호출.

context_explorer 노드가 실행계획 스텝에 따라 호출하는 도구 함수들.
각 함수는 기존 커넥터 매니저를 통해 실제/Dummy 데이터 소스를 투명하게 전환한다.
"""

from __future__ import annotations

import re as _re
from calendar import monthrange as _monthrange
from typing import Any

from src.config import settings
from src.connectors.manager import get_connector_manager
from src.services.sql_hint_extractor import (
    extract_structural_hints,
    merge_hints,
)
from src.agents.state.state import StructuralHints
from src.utils.logger import get_logger

logger = get_logger(__name__)

__all__ = [
    "search_use_cases",
    "search_table_meta",
    "search_code_meta",
    "search_manual",
    "search_glossary",
    "get_sample_rows",
    "extract_hints_from_use_cases",
    "get_date_distribution",
    "detect_date_pattern",
    "TOOL_MAP",
    "execute_tool",
]


async def search_use_cases(query: str) -> list[dict]:
    """유사 SQL 활용사례 벡터 검색 + Reranker 재순위.

    1단계: Qdrant Dense+Sparse 하이브리드 검색 (RRF, prefetch_limit건)
    2단계: Reranker Cross-Encoder 재순위 (sql_history_top_k건)
    Reranker 비활성 또는 실패 시 → RRF 점수 상위 top_k건 폴백
    """
    mgr = get_connector_manager()
    try:
        raw_candidates = await mgr.qdrant.search_sql_history(query)
        if not isinstance(raw_candidates, list) or not raw_candidates:
            return []

        # Reranker 적용
        reranked = _rerank_use_cases(query, raw_candidates)
        return reranked
    except Exception as e:
        logger.warning("search_use_cases 실패", error=str(e))
        return []


def _rerank_use_cases(
    query: str,
    candidates: list[dict],
) -> list[dict]:
    """활용사례를 Reranker로 재순위하고 상위 top_k건을 반환한다.

    Reranker 비활성 시 RRF _score 기준 상위 top_k건을 반환한다.
    반환 dict에 similarity 필드를 추가하여 confidence_scorer 호환성을 보장한다.
    """
    from src.services.reranker import RerankCandidate, get_reranker

    top_k = settings.qdrant_sql_history_top_k

    # dict → RerankCandidate 변환
    rerank_candidates = [
        RerankCandidate(
            text=(
                c.get("description", "") + " " + c.get("sql", "")
            ),
            payload=c,
            score=c.get("_score", 0.0),
        )
        for c in candidates
    ]

    # Reranker 실행 (비활성 시 내부에서 score 기반 정렬 폴백)
    reranker = get_reranker()
    reranked = reranker.rerank(
        query, rerank_candidates, top_k=top_k,
    )

    # RerankCandidate → dict 변환 + similarity 필드 추가
    result: list[dict] = []
    for item in reranked:
        d = item.payload.copy() if isinstance(item.payload, dict) else {}
        d["_score"] = item.rerank_score
        d["similarity"] = item.rerank_score
        result.append(d)

    return result


async def search_table_meta(query: str) -> list[dict]:
    """테이블/컬럼 메타 검색 (MongoDB)."""
    mgr = get_connector_manager()
    try:
        results = await mgr.mongo.search_table_meta(query)
        return results if isinstance(results, list) else []
    except Exception as e:
        logger.warning(
            "search_table_meta 실패", error=str(e),
        )
        return []


async def search_code_meta(column_name: str) -> list[dict]:
    """코드값 목록 검색 (MongoDB)."""
    mgr = get_connector_manager()
    try:
        results = await mgr.mongo.search_code_meta(column_name)
        return results if isinstance(results, list) else []
    except Exception as e:
        logger.warning(
            "search_code_meta 실패", error=str(e),
        )
        return []


async def search_manual(query: str) -> list[dict]:
    """업무 매뉴얼 검색 (Qdrant biz_manual 컬렉션)."""
    mgr = get_connector_manager()
    try:
        results = await mgr.qdrant.search_manual(query)
        return results if isinstance(results, list) else []
    except Exception as e:
        logger.warning(
            "search_manual 실패", error=str(e),
        )
        return []


async def search_glossary(term: str) -> list[dict]:
    """금융 용어사전 검색 (MongoDB glossary 컬렉션)."""
    mgr = get_connector_manager()
    try:
        results = await mgr.mongo.search_glossary(term)
        return results if isinstance(results, list) else []
    except Exception as e:
        logger.warning(
            "search_glossary 실패", error=str(e),
        )
        return []


async def get_sample_rows(
    table_name: str,
    schema_name: str = "",
    db_source: str = "",
    limit: int = 10,
) -> list[dict]:
    """테이블의 샘플 데이터를 조회한다 (dialect 인식).

    Sybase IQ(tsql): SELECT TOP N * FROM schema.table
    Impala/PostgreSQL: SELECT * FROM schema.table LIMIT N

    SQL 인젝션 방지: 식별자 화이트리스트 검증 후 실행.
    context_explorer 후처리에서 rule-based로 자동 호출된다.
    """
    if not _IDENT_RE.match(table_name):
        return []
    if schema_name and not _IDENT_RE.match(schema_name):
        return []

    qualified = f"{schema_name}.{table_name}" if schema_name else table_name

    # dialect 결정
    from src.utils.db_routing import get_dialect_for_source
    dialect = get_dialect_for_source(db_source)

    if dialect == "tsql":
        sql = f"SELECT TOP {limit} * FROM {qualified}"
    else:
        sql = f"SELECT * FROM {qualified} LIMIT {limit}"

    mgr = get_connector_manager()
    try:
        result = await mgr.info_db.execute_query(sql)
        if hasattr(result, "rows") and isinstance(result.rows, list):
            return result.rows
        return []
    except Exception as e:
        logger.warning(
            "get_sample_rows 실패",
            table=table_name, error=str(e),
        )
        return []


def extract_hints_from_use_cases(use_cases: list[dict]) -> StructuralHints:
    """유사 SQL 목록에서 sqlglot 기반 구조적 힌트를 추출한다."""
    hints_list = [
        extract_structural_hints(uc.get("sql", ""))
        for uc in use_cases if uc.get("sql")
    ]
    if not hints_list:
        return StructuralHints()
    return merge_hints(hints_list)


_IDENT_RE = _re.compile(r"^[A-Za-z_]\w*$")


async def get_date_distribution(
    table_name: str,
    date_column: str,
    limit: int = 30,
    schema_name: str = "",
) -> list[str]:
    """테이블의 날짜 컬럼 DISTINCT 값을 조회한다 (경량).

    SQL 인젝션 방지: 식별자 화이트리스트 검증 후 실행.
    TOOL_MAP에 등록하지 않음 — planner 계획 대상이 아니라 직접 호출.
    schema_name이 있으면 스키마명.테이블명 형태로 조회한다.
    """
    if not _IDENT_RE.match(table_name):
        return []
    if not _IDENT_RE.match(date_column):
        return []
    if schema_name and not _IDENT_RE.match(schema_name):
        return []

    qualified = f"{schema_name}.{table_name}" if schema_name else table_name
    sql = (
        f"SELECT DISTINCT {date_column} FROM {qualified} "
        f"ORDER BY {date_column} LIMIT {limit}"
    )
    mgr = get_connector_manager()
    try:
        result = await mgr.info_db.execute_query(sql)
        if hasattr(result, "rows") and isinstance(result.rows, list):
            return [str(row.get(date_column, "")) for row in result.rows]
        return []
    except Exception as e:
        logger.warning(
            "get_date_distribution 실패",
            table=table_name, column=date_column, error=str(e),
        )
        return []


def _is_month_end(date_str: str) -> bool:
    """날짜 문자열이 해당 월의 말일인지 확인한다."""
    cleaned = date_str.replace("-", "").replace("/", "")
    if len(cleaned) != 8:
        return False
    try:
        year = int(cleaned[:4])
        month = int(cleaned[4:6])
        day = int(cleaned[6:8])
        _, last_day = _monthrange(year, month)
        return day == last_day
    except (ValueError, OverflowError):
        return False


def detect_date_pattern(dates: list[str]) -> str:
    """날짜 DISTINCT 값 목록에서 입도 패턴을 탐지한다.

    반환 예시: "매일 (90건)", "매월 말일 (12건)", "매월 (12건)"
    context_explorer에서 get_date_distribution 결과를 해석할 때 직접 호출.
    """
    if not dates:
        return "0건"
    if len(dates) < 2:
        return f"{len(dates)}건"

    sample = dates[0].replace("-", "").replace("/", "")

    if len(sample) == 6:  # YYYYMM
        return f"매월 ({len(dates)}건)"

    if len(sample) == 4:  # YYYY
        return f"매년 ({len(dates)}건)"

    if len(sample) == 8:  # YYYYMMDD
        if all(_is_month_end(d) for d in dates):
            return f"매월 말일 ({len(dates)}건)"
        return f"매일 ({len(dates)}건)"

    return f"{len(dates)}건"


# ── 도구 디스패치 맵 ──────────────────────────────────
TOOL_MAP: dict[str, Any] = {
    "search_use_cases": search_use_cases,
    "search_table_meta": search_table_meta,
    "search_code_meta": search_code_meta,
    "search_manual": search_manual,
    "search_glossary": search_glossary,
}


async def execute_tool(tool_name: str, tool_input: str) -> Any:
    """도구명으로 해당 함수를 실행한다."""
    tool_fn = TOOL_MAP.get(tool_name)
    if tool_fn:
        return await tool_fn(tool_input)
    logger.warning("알 수 없는 도구", tool=tool_name)
    return None
