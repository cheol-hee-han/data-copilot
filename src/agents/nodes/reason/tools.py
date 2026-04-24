"""탐색 도구 래퍼 — 기존 커넥터/서비스를 에이전틱 코어에서 호출.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

context_explorer·recovery_agent 노드가 ExecutionStep.tool 값에 따라
호출하는 도구 함수들. 각 함수는 ConnectorManager를 통해
실제/Dummy 데이터 소스를 투명하게 전환한다.

호출 구조:
    LLM이 execution_plan에 {"tool": "도구명", "input": "문자열"} 을 생성하면,
    context_retriever가 execute_tool(name, input)을 호출한다.
    모든 도구는 TOOL_MAP에 _tool_ 접두사 어댑터로 등록된다.
    어댑터가 쉼표 구분 문자열을 파싱하여 원본 함수 시그니처에 맞춘다.

도구 목록:
    lookup 도구 (이름/키 지정 조회):
    - lookup_table_meta: 영문 테이블명으로 MongoDB 테이블 메타 조회 (단건)
    - lookup_code_meta: 코드 컬럼명으로 MongoDB 코드값 매핑 조회

    search 도구 (키워드/의미 기반 탐색, page 지원):
    - search_table_meta: 한글 키워드로 MongoDB 테이블/컬럼 메타 검색
    - search_use_cases: Qdrant 유사 SQL 벡터 검색 (하이브리드 + Reranker)
    - search_manual: Qdrant 업무 매뉴얼 벡터 검색
    - search_biz_terms: MongoDB 비즈니스 용어 사전 검색

    get 도구 (DB에서 데이터 직접 가져오기):
    - get_sample_rows: 테이블 샘플 데이터 조회 (LIMIT 적용)
    - get_column_values: 특정 컬럼 키워드 LIKE 검색 (필터 값 탐색)
    - get_column_profile: 컬럼 통계 조회 (건수, 고유값, NULL율, MIN/MAX)
    - get_date_distribution: 날짜 컬럼 DISTINCT 분포 조회

    분석 도구 (내부 전용, TOOL_MAP 미등록):
    - detect_date_pattern: 날짜 DISTINCT 값에서 입도 패턴 추론
    - extract_hints_from_use_cases: 유사 SQL에서 sqlglot 구조적 힌트 추출

    디스패처:
    - TOOL_MAP: tool 이름 → 함수 매핑 딕셔너리
    - execute_tool: TOOL_MAP을 통해 도구를 이름으로 실행

SQL 인젝션 방지:
    DB 직접 조회 도구는 모두 _IDENT_RE(식별자 화이트리스트)로
    테이블명·컬럼명을 검증한 뒤 실행한다.
"""

from __future__ import annotations

import re as _re
from calendar import monthrange as _monthrange
from collections.abc import Awaitable
from typing import Any

from src.connectors.manager import ConnectorManager, get_connector_manager
from src.utils.logger import get_logger

logger = get_logger(__name__)

__all__ = [
    "lookup_table_meta",
    "search_table_meta",
    "search_use_cases",
    "lookup_code_meta",
    "search_manual",
    "search_biz_terms",
    "get_sample_rows",
    "get_column_values",
    "get_column_profile",
    "get_date_distribution",
    "detect_date_pattern",
    "TOOL_MAP",
    "execute_tool",
    "_TABLE_META_TOOLS",
    "_QDRANT_TOOLS",
]


# ── 도구 분류 상수 ──────────────────────────────────────
_TABLE_META_TOOLS = frozenset({"lookup_table_meta", "search_table_meta"})
_QDRANT_TOOLS = frozenset({"search_use_cases", "search_manual"})


# ── page 파싱 헬퍼 ──────────────────────────────────────
_MAX_PAGE = 5


def _extract_page(parts: list[str]) -> tuple[list[str], int]:
    """파라미터 목록에서 page=N을 추출하고 나머지를 반환한다.

    ["여신", "page=2"] → (["여신"], 2)
    ["테이블명", "컬럼명", "page=3"] → (["테이블명", "컬럼명"], 3)
    ["여신"] → (["여신"], 1)

    page가 비정수/음수/0이면 기본값 1, _MAX_PAGE 초과 시 클램핑.
    """
    page = 1
    remaining: list[str] = []
    for part in parts:
        if part.startswith("page="):
            try:
                p = int(part.split("=", 1)[1])
                page = max(1, min(p, _MAX_PAGE))
            except (ValueError, IndexError):
                pass
        else:
            remaining.append(part)
    return remaining, page


async def _safe_search(
    coro: Awaitable[list[dict]],
) -> list[dict]:
    """검색 도구 공통 래퍼 — 예외는 상위(_run_step)로 전파하여 텔레메트리에 정확히 기록."""
    results = await coro
    return results if isinstance(results, list) else []


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 원본 함수 — 외부 직접 호출 호환 시그니처 유지
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def lookup_table_meta(table_name: str) -> list[dict]:
    """특정 테이블의 메타 정보를 조회한다 (영문 테이블명 정확 매칭).

    enrichment, recovery_agent에서 특정 테이블을 지정 조회할 때 사용.
    커넥터의 table_names kwargs를 통해 $in 정확 매칭으로 동작한다.
    """
    mgr = get_connector_manager()
    return await _safe_search(
        mgr.mongo.search_table_meta(
            table_name, table_names=[table_name],
        ),
    )


async def search_table_meta(
    keywords: str, page: int = 1,
) -> list[dict]:
    """한글 키워드로 테이블/컬럼 메타를 검색한다 (regex + keyword score).

    reasoning_preparer의 초기 탐색, recovery_agent의 추가 탐색에서 사용.
    page=N으로 다음 결과 블록을 조회할 수 있다.
    """
    mgr = get_connector_manager()
    return await _safe_search(
        mgr.mongo.search_table_meta(keywords, page=page),
    )


async def search_use_cases(
    query: str,
    *,
    exclude_ids: list[str | int] | None = None,
) -> list[dict]:
    """유사 SQL 활용사례 벡터 검색 + Reranker 재순위."""
    mgr = get_connector_manager()
    return await _safe_search(
        mgr.qdrant.search_sql_history(
            query, exclude_ids=exclude_ids,
        ),
    )


async def lookup_code_meta(
    column_name: str, page: int = 1,
) -> list[dict]:
    """코드값 목록 조회 (MongoDB). 컬럼명 지정 조회."""
    mgr = get_connector_manager()
    return await _safe_search(
        mgr.mongo.search_code_meta(column_name, page=page),
    )


async def search_manual(
    query: str,
    *,
    exclude_ids: list[str | int] | None = None,
) -> list[dict]:
    """업무 매뉴얼 검색 (Qdrant biz_manual 컬렉션)."""
    mgr = get_connector_manager()
    return await _safe_search(
        mgr.qdrant.search_manual(
            query, exclude_ids=exclude_ids,
        ),
    )


async def search_biz_terms(
    term: str, page: int = 1,
) -> list[dict]:
    """비즈니스 용어사전 검색 (MongoDB biz_term 컬렉션)."""
    mgr = get_connector_manager()
    return await _safe_search(
        mgr.mongo.search_biz_terms(term, page=page),
    )


# SQL 인젝션 방지 — 영문자/언더스코어로 시작하는 식별자만 허용
_IDENT_RE = _re.compile(r"^[A-Za-z_]\w*$")


async def get_sample_rows(
    table_name: str,
    schema_name: str = "",
    db_source: str = "",
    limit: int = 10,
) -> list[dict]:
    """테이블의 샘플 데이터를 조회한다 (dialect 인식).

    ADW(tsql): SELECT TOP N * FROM schema.table
    BDP(hive) / CRP(oracle) / TEST(postgres):
        SELECT * FROM schema.table LIMIT N

    SQL 인젝션 방지: 식별자 화이트리스트 검증 후 실행.
    TOOL_MAP에 어댑터(_tool_get_sample_rows)로 등록됨.
    context_explorer 후처리에서도 직접 호출된다.
    """
    if not _IDENT_RE.match(table_name):
        return []
    if schema_name and not _IDENT_RE.match(schema_name):
        return []

    qualified = (
        f"{schema_name}.{table_name}"
        if schema_name else table_name
    )

    mgr = get_connector_manager()
    db = mgr.get_query_db(db_source=db_source)

    if db.dialect == "tsql":
        sql = f"SELECT TOP {limit} * FROM {qualified}"
    else:
        sql = f"SELECT * FROM {qualified} LIMIT {limit}"

    result = await db.execute_query(sql)
    return result if isinstance(result, list) else []


async def get_column_values(
    table_name: str,
    column_name: str,
    keyword: str,
    limit: int = 20,
    schema_name: str = "",
    db_source: str = "",
    page: int = 1,
) -> list[str]:
    """특정 컬럼에서 키워드를 포함하는 고유값을 검색한다.

    WHERE column LIKE '%keyword%' 로 실제 DB 값을 조회하여
    필터 조건에 사용할 정확한 값을 찾는다.

    SQL 인젝션 방지: 식별자 화이트리스트 + 키워드 sanitize.
    TOOL_MAP에 어댑터(_tool_get_column_values)로 등록됨.
    """
    if not _IDENT_RE.match(table_name):
        return []
    if not _IDENT_RE.match(column_name):
        return []
    if schema_name and not _IDENT_RE.match(schema_name):
        return []

    sanitized_kw = keyword.replace("'", "''").replace(
        "\\", "\\\\",
    )

    qualified = (
        f"{schema_name}.{table_name}"
        if schema_name else table_name
    )
    offset = (page - 1) * limit

    mgr = get_connector_manager()
    db = mgr.get_query_db(db_source=db_source)

    if db.dialect == "tsql":
        start_at = offset + 1
        sql = (
            f"SELECT DISTINCT TOP {limit} START AT {start_at} "
            f"{column_name} FROM {qualified} "
            f"WHERE CAST({column_name} AS VARCHAR) "
            f"LIKE '%{sanitized_kw}%' "
            f"ORDER BY {column_name}"
        )
    elif db.dialect == "hive":
        # BDP(hive): TEXT 타입 없음, STRING 사용
        sql = (
            f"SELECT DISTINCT {column_name} "
            f"FROM {qualified} "
            f"WHERE CAST({column_name} AS STRING) "
            f"LIKE '%{sanitized_kw}%' "
            f"ORDER BY {column_name} "
            f"LIMIT {limit} OFFSET {offset}"
        )
    else:
        # PostgreSQL
        sql = (
            f"SELECT DISTINCT {column_name} "
            f"FROM {qualified} "
            f"WHERE CAST({column_name} AS TEXT) "
            f"LIKE '%{sanitized_kw}%' "
            f"ORDER BY {column_name} "
            f"LIMIT {limit} OFFSET {offset}"
        )
    result = await db.execute_query(sql)
    if isinstance(result, list):
        return [
            str(row.get(column_name, ""))
            for row in result
        ]
    return []


async def get_column_profile(
    table_name: str,
    column_name: str,
    schema_name: str = "",
    db_source: str = "",
) -> dict:
    """컬럼 통계를 조회한다 (건수, 고유값 수, NULL율, MIN/MAX).

    recovery_agent가 0건 원인 진단, 컬럼 특성 파악에 사용한다.
    SQL 인젝션 방지: 식별자 화이트리스트 검증 후 실행.
    TOOL_MAP에 어댑터(_tool_get_column_profile)로 등록됨.
    """
    if not _IDENT_RE.match(table_name):
        return {}
    if not _IDENT_RE.match(column_name):
        return {}
    if schema_name and not _IDENT_RE.match(schema_name):
        return {}

    qualified = (
        f"{schema_name}.{table_name}"
        if schema_name else table_name
    )

    mgr = get_connector_manager()
    db = mgr.get_query_db(db_source=db_source)

    sql = (
        f"SELECT "
        f"COUNT(*) AS total_rows, "
        f"COUNT({column_name}) AS non_null_count, "
        f"COUNT(DISTINCT {column_name}) AS distinct_count, "
        f"MIN({column_name}) AS min_val, "
        f"MAX({column_name}) AS max_val "
        f"FROM {qualified}"
    )
    result = await db.execute_query(sql)
    if isinstance(result, list) and result:
        row = result[0]
        total = int(row.get("total_rows", 0))
        non_null = int(row.get("non_null_count", 0))
        return {
            "total_rows": total,
            "non_null_count": non_null,
            "null_count": total - non_null,
            "null_rate": round(
                (total - non_null) / total, 3,
            ) if total > 0 else 0.0,
            "distinct_count": int(
                row.get("distinct_count", 0),
            ),
            "min_val": str(row.get("min_val", "")),
            "max_val": str(row.get("max_val", "")),
        }
    return {}


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

    qualified = (
        f"{schema_name}.{table_name}"
        if schema_name else table_name
    )

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
    result = await db.execute_query(sql)
    if isinstance(result, list):
        return [
            str(row.get(date_column, ""))
            for row in result
        ]
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
# 모든 도구가 _tool_ 어댑터를 통해 TOOL_MAP에 등록된다.
# 어댑터가 tool_input: str을 파싱하여 원본 함수에 전달한다.


def _split_qualified_name(
    qualified: str,
) -> tuple[str, str]:
    """'schema.table' → (schema, table), 'table' → ('', table)."""
    if "." in qualified:
        schema, _, table = qualified.rpartition(".")
        return schema, table
    return "", qualified


# ── lookup/search 도구 어댑터 ──

async def _tool_lookup_table_meta(
    tool_input: str,
) -> list[dict]:
    """lookup_table_meta TOOL_MAP 어댑터 — 영문 테이블명 조회."""
    table_name = tool_input.strip()
    return await lookup_table_meta(table_name)


async def _tool_search_table_meta(
    tool_input: str,
) -> list[dict]:
    """search_table_meta TOOL_MAP 어댑터 — 한글 키워드 검색 + page."""
    parts, page = _extract_page(
        [p.strip() for p in tool_input.split(",")],
    )
    return await search_table_meta(
        ", ".join(parts), page=page,
    )


async def _tool_search_use_cases(
    tool_input: str,
    *,
    exclude_ids: list[str | int] | None = None,
) -> list[dict]:
    """search_use_cases TOOL_MAP 어댑터."""
    parts, _page = _extract_page(
        [p.strip() for p in tool_input.split(",")],
    )
    return await search_use_cases(
        ", ".join(parts), exclude_ids=exclude_ids,
    )


async def _tool_lookup_code_meta(
    tool_input: str,
) -> list[dict]:
    """lookup_code_meta TOOL_MAP 어댑터."""
    parts, page = _extract_page(
        [p.strip() for p in tool_input.split(",")],
    )
    return await lookup_code_meta(
        ", ".join(parts), page=page,
    )


async def _tool_search_manual(
    tool_input: str,
    *,
    exclude_ids: list[str | int] | None = None,
) -> list[dict]:
    """search_manual TOOL_MAP 어댑터."""
    parts, _page = _extract_page(
        [p.strip() for p in tool_input.split(",")],
    )
    return await search_manual(
        ", ".join(parts), exclude_ids=exclude_ids,
    )


async def _tool_search_biz_terms(
    tool_input: str,
) -> list[dict]:
    """search_biz_terms TOOL_MAP 어댑터."""
    parts, page = _extract_page(
        [p.strip() for p in tool_input.split(",")],
    )
    return await search_biz_terms(
        ", ".join(parts), page=page,
    )


# ── DB 직접 조회 도구 어댑터 ──

async def _tool_get_sample_rows(tool_input: str) -> Any:
    """get_sample_rows TOOL_MAP 어댑터."""
    parts = [p.strip() for p in tool_input.split(",")]
    raw_table = parts[0] if parts else ""
    schema_name, table_name = _split_qualified_name(raw_table)
    db_source = ConnectorManager.parse_db_source(table_name)
    return await get_sample_rows(
        table_name, schema_name=schema_name, db_source=db_source,
    )


async def _tool_get_column_values(
    tool_input: str,
) -> Any:
    """get_column_values TOOL_MAP 어댑터."""
    parts, page = _extract_page(
        [p.strip() for p in tool_input.split(",")],
    )
    raw_table = parts[0] if parts else ""
    column_name = parts[1] if len(parts) > 1 else ""
    keyword = parts[2] if len(parts) > 2 else ""
    if not raw_table or not column_name or not keyword:
        return []
    schema_name, table_name = _split_qualified_name(raw_table)
    db_source = ConnectorManager.parse_db_source(table_name)
    return await get_column_values(
        table_name, column_name, keyword,
        schema_name=schema_name, db_source=db_source, page=page,
    )


async def _tool_get_column_profile(
    tool_input: str,
) -> Any:
    """get_column_profile TOOL_MAP 어댑터.

    입력 형식: "테이블명,컬럼명" 또는 "스키마.테이블명,컬럼명"
    """
    parts = [p.strip() for p in tool_input.split(",")]
    raw_table = parts[0] if parts else ""
    column_name = parts[1] if len(parts) > 1 else ""
    if not raw_table or not column_name:
        return {}
    schema_name, table_name = _split_qualified_name(raw_table)
    db_source = ConnectorManager.parse_db_source(table_name)
    return await get_column_profile(
        table_name, column_name,
        schema_name=schema_name, db_source=db_source,
    )


async def _tool_get_date_distribution(
    tool_input: str,
) -> Any:
    """get_date_distribution TOOL_MAP 어댑터."""
    parts = [p.strip() for p in tool_input.split(",")]
    raw_table = parts[0] if parts else ""
    date_column = parts[1] if len(parts) > 1 else ""
    if not raw_table or not date_column:
        return []
    schema_name, table_name = _split_qualified_name(raw_table)
    db_source = ConnectorManager.parse_db_source(table_name)
    return await get_date_distribution(
        table_name, date_column,
        schema_name=schema_name, db_source=db_source,
    )


# ── 도구 디스패치 맵 ──────────────────────────────────
TOOL_MAP: dict[str, Any] = {
    # lookup/search 도구 — 어댑터
    "search_use_cases":     _tool_search_use_cases,
    "lookup_table_meta":    _tool_lookup_table_meta,
    "search_table_meta":    _tool_search_table_meta,
    "lookup_code_meta":     _tool_lookup_code_meta,
    "search_manual":        _tool_search_manual,
    "search_biz_terms":     _tool_search_biz_terms,
    # DB 직접 도구 — 어댑터
    "get_sample_rows":      _tool_get_sample_rows,
    "get_column_values":    _tool_get_column_values,
    "get_column_profile":   _tool_get_column_profile,
    "get_date_distribution": _tool_get_date_distribution,
}


async def execute_tool(
    tool_name: str, tool_input: str, **kwargs: Any,
) -> Any:
    """TOOL_MAP에서 도구명으로 함수를 찾아 실행한다.

    context_retriever가 ExecutionStep.tool 값으로 호출하는 진입점.
    미등록 도구는 경고 로그 후 None을 반환한다.
    Qdrant 도구만 exclude_ids를 명시적으로 전달한다.
    """
    tool_fn = TOOL_MAP.get(tool_name)
    if tool_fn is None:
        logger.warning("알 수 없는 도구", tool=tool_name)
        return None

    # Qdrant 도구만 exclude_ids를 명시적으로 전달
    if tool_name in _QDRANT_TOOLS and "exclude_ids" in kwargs:
        return await tool_fn(
            tool_input, exclude_ids=kwargs["exclude_ids"],
        )
    return await tool_fn(tool_input)
