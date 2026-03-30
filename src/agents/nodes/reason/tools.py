"""탐색 도구 래퍼 — 기존 커넥터/서비스를 에이전틱 코어에서 호출.

context_explorer 노드가 ExecutionStep.tool 값에 따라 호출하는 도구 함수들.
각 함수는 ConnectorManager를 통해 실제/Dummy 데이터 소스를 투명하게 전환한다.

도구 목록:
    - search_use_cases: Qdrant 유사 SQL 벡터 검색 (하이브리드 + Reranker 내장)
    - search_table_meta: ES 테이블/컬럼 메타데이터 검색
    - search_code_meta: ES 코드 필드/코드값 검색
    - search_manual: Qdrant 업무 매뉴얼 벡터 검색
    - search_glossary: ES 용어 사전 검색
    - get_sample_rows: 정보계 DB에서 테이블 샘플 데이터 조회 (LIMIT 적용)
    - get_date_distribution: 날짜 컬럼의 MIN/MAX 분포 조회
    - detect_date_pattern: 날짜 컬럼의 포맷 패턴 추론 (YYYYMMDD 등)
    - extract_hints_from_use_cases: 유사 SQL에서 sqlglot 구조적 힌트 추출

    TOOL_MAP: tool 이름 → 함수 매핑 딕셔너리
    execute_tool: TOOL_MAP을 통해 도구를 이름으로 실행하는 디스패처
"""

from __future__ import annotations

import re as _re
from calendar import monthrange as _monthrange
from collections.abc import Awaitable
from typing import Any

from src.connectors.manager import get_connector_manager
from src.utils.sqlglot_analyzer import (
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


async def _safe_search(
    tool_name: str,
    coro: Awaitable[list[dict]],
) -> list[dict]:
    """검색 도구 공통 래퍼 — 예외 시 빈 리스트 반환."""
    try:
        results = await coro
        return results if isinstance(results, list) else []
    except Exception as e:
        logger.warning(f"{tool_name} 실패", error=str(e))
        return []


async def search_use_cases(query: str) -> list[dict]:
    """유사 SQL 활용사례 벡터 검색 + Reranker 재순위."""
    mgr = get_connector_manager()
    return await _safe_search(
        "search_use_cases", mgr.qdrant.search_sql_history(query),
    )


async def search_table_meta(query: str) -> list[dict]:
    """테이블/컬럼 메타 검색 (MongoDB)."""
    mgr = get_connector_manager()
    return await _safe_search(
        "search_table_meta", mgr.mongo.search_table_meta(query),
    )


async def search_code_meta(column_name: str) -> list[dict]:
    """코드값 목록 검색 (MongoDB)."""
    mgr = get_connector_manager()
    return await _safe_search(
        "search_code_meta", mgr.mongo.search_code_meta(column_name),
    )


async def search_manual(query: str) -> list[dict]:
    """업무 매뉴얼 검색 (Qdrant biz_manual 컬렉션)."""
    mgr = get_connector_manager()
    return await _safe_search(
        "search_manual", mgr.qdrant.search_manual(query),
    )


async def search_glossary(term: str) -> list[dict]:
    """금융 용어사전 검색 (MongoDB glossary 컬렉션)."""
    mgr = get_connector_manager()
    return await _safe_search(
        "search_glossary", mgr.mongo.search_glossary(term),
    )


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
    TOOL_MAP에 어댑터(_tool_get_sample_rows)로 등록됨.
    context_explorer 후처리에서도 직접 호출된다.
    """
    if not _IDENT_RE.match(table_name):
        return []
    if schema_name and not _IDENT_RE.match(schema_name):
        return []

    qualified = f"{schema_name}.{table_name}" if schema_name else table_name

    mgr = get_connector_manager()
    db = mgr.get_query_db(db_source=db_source)

    if db.dialect == "tsql":
        sql = f"SELECT TOP {limit} * FROM {qualified}"
    else:
        sql = f"SELECT * FROM {qualified} LIMIT {limit}"

    try:
        result = await db.execute_query(sql)
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
    merged = merge_hints(hints_list)
    return StructuralHints(**merged)


_IDENT_RE = _re.compile(r"^[A-Za-z_]\w*$")


async def get_date_distribution(
    table_name: str,
    date_column: str,
    limit: int = 30,
    schema_name: str = "",
    db_source: str = "",
) -> list[str]:
    """테이블의 날짜 컬럼 DISTINCT 값을 조회한다 (경량).

    SQL 인젝션 방지: 식별자 화이트리스트 검증 후 실행.
    TOOL_MAP에 어댑터(_tool_get_date_distribution)로 등록됨.
    schema_name이 있으면 스키마명.테이블명 형태로 조회한다.
    """
    if not _IDENT_RE.match(table_name):
        return []
    if not _IDENT_RE.match(date_column):
        return []
    if schema_name and not _IDENT_RE.match(schema_name):
        return []

    qualified = f"{schema_name}.{table_name}" if schema_name else table_name

    mgr = get_connector_manager()
    db = mgr.get_query_db(db_source=db_source)

    if db.dialect == "tsql":
        sql = (
            f"SELECT DISTINCT TOP {limit} {date_column} "
            f"FROM {qualified} ORDER BY {date_column}"
        )
    else:
        sql = (
            f"SELECT DISTINCT {date_column} FROM {qualified} "
            f"ORDER BY {date_column} LIMIT {limit}"
        )
    try:
        result = await db.execute_query(sql)
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


# ── TOOL_MAP 어댑터 ───────────────────────────────────
# get_sample_rows, get_date_distribution은 복수 파라미터 함수이므로
# execute_tool(name, str) 시그니처에 맞추는 래퍼를 정의한다.
# input 형식: "테이블명" 또는 "테이블명,컬럼명" (쉼표 구분)

async def _tool_get_sample_rows(tool_input: str) -> Any:
    """get_sample_rows TOOL_MAP 어댑터."""
    parts = [p.strip() for p in tool_input.split(",")]
    table_name = parts[0] if parts else ""
    return await get_sample_rows(table_name)


async def _tool_get_date_distribution(
    tool_input: str,
) -> Any:
    """get_date_distribution TOOL_MAP 어댑터."""
    parts = [p.strip() for p in tool_input.split(",")]
    table_name = parts[0] if parts else ""
    date_column = parts[1] if len(parts) > 1 else ""
    if not table_name or not date_column:
        return []
    return await get_date_distribution(table_name, date_column)


# ── 도구 디스패치 맵 ──────────────────────────────────
TOOL_MAP: dict[str, Any] = {
    "search_use_cases": search_use_cases,
    "search_table_meta": search_table_meta,
    "search_code_meta": search_code_meta,
    "search_manual": search_manual,
    "search_glossary": search_glossary,
    "get_sample_rows": _tool_get_sample_rows,
    "get_date_distribution": _tool_get_date_distribution,
}


async def execute_tool(tool_name: str, tool_input: str) -> Any:
    """도구명으로 해당 함수를 실행한다."""
    tool_fn = TOOL_MAP.get(tool_name)
    if tool_fn:
        return await tool_fn(tool_input)
    logger.warning("알 수 없는 도구", tool=tool_name)
    return None
