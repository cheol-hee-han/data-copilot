"""sqlglot 기반 SQL 구조적 힌트 추출 모듈.

유사 SQL에서 조인 패턴, 코드성 컬럼 값, 집계 패턴, 날짜 조건을 추출하여
LLM에 압축된 구조 정보를 제공한다.

주요 기능:
  - extract_structural_hints: SQL 원문 → StructuralHints
  - parse_sql_safe: 방언별 안전한 파싱 (실패 시 None)
  - merge_hints: 다수 SQL에서 추출한 힌트 병합
"""

from __future__ import annotations

import re
from typing import Optional

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

from src.agents.state.state import StructuralHints


# ── 방언 매핑 ────────────────────────────────────────
DIALECT_MAP: dict[str, str] = {
    "postgresql": "postgres",   # 공식 지원 — 99%
    "impala":     "hive",       # 근사 매핑 — 95%+
    "sybase_iq":  "tsql",       # 근사 매핑 — 85~90%
}


def extract_structural_hints(
    sql: str,
    dialect: str | None = None,
) -> StructuralHints:
    """SQL 원문에서 12가지 구조적 힌트를 추출한다.

    파싱 실패 시 빈 힌트를 반환한다 (agent 흐름 차단 없음).
    """
    ast = parse_sql_safe(sql, dialect)
    if ast is None:
        return StructuralHints()

    # alias → 테이블명 매핑 구성
    alias_map = _build_alias_map(ast)

    return StructuralHints(
        # 기존 4가지 (join_patterns는 테이블명으로 치환)
        join_patterns=_extract_join_patterns(ast, alias_map),
        code_columns=_extract_code_columns(ast),
        agg_expressions=_extract_agg_expressions(ast, alias_map),
        date_filters=_extract_date_filters(ast),
        # 신규: 테이블 정보
        source_tables=get_real_tables(ast),
        # 신규: SELECT 출력 구조
        select_columns=_extract_select_columns(ast, alias_map),
        group_by_columns=_extract_group_by(ast, alias_map),
        order_by_columns=_extract_order_by(ast),
        limit_value=_extract_limit(ast),
        has_distinct=_has_distinct(ast),
        has_subquery=_has_subquery(ast),
        has_having=_has_having(ast),
    )


def parse_sql_safe(
    sql: str,
    dialect: str | None = None,
) -> Optional[sqlglot.Expression]:
    """sqlglot 안전 파싱 — 실패 시 None 반환.

    힌트 추출은 보조 정보이므로 파싱 실패가 전체 흐름을 차단하지 않는다.
    """
    try:
        mapped_dialect = DIALECT_MAP.get(dialect, dialect) if dialect else None
        cleaned = _preprocess_dialect_quirks(sql, mapped_dialect)
        ast = sqlglot.parse_one(
            cleaned,
            dialect=mapped_dialect,
            error_level=sqlglot.ErrorLevel.RAISE,
        )
        # Command 노드는 미지원 구문 — 빈 힌트 폴백
        if isinstance(ast, exp.Command):
            return None
        return ast
    except sqlglot.errors.ParseError:
        return None


def merge_hints(hints_list: list[StructuralHints]) -> StructuralHints:
    """다수 SQL에서 추출한 힌트를 병합한다.

    중복 제거 + 빈도 기반 우선순위 부여.
    """
    merged = StructuralHints()

    seen_joins: set[str] = set()
    seen_aggs: set[str] = set()

    for hints in hints_list:
        for jp in hints.join_patterns:
            normalized = _normalize_join(jp)
            if normalized not in seen_joins:
                seen_joins.add(normalized)
                merged.join_patterns.append(jp)

        for col, vals in hints.code_columns.items():
            if col not in merged.code_columns:
                merged.code_columns[col] = []
            for v in vals:
                if v not in merged.code_columns[col]:
                    merged.code_columns[col].append(v)

        for agg in hints.agg_expressions:
            if agg.upper() not in seen_aggs:
                seen_aggs.add(agg.upper())
                merged.agg_expressions.append(agg)

        for df in hints.date_filters:
            if df not in merged.date_filters:
                merged.date_filters.append(df)

        # 신규 필드 병합
        for t in hints.source_tables:
            if t not in merged.source_tables:
                merged.source_tables.append(t)
        for sc in hints.select_columns:
            if sc not in merged.select_columns:
                merged.select_columns.append(sc)
        for gb in hints.group_by_columns:
            if gb not in merged.group_by_columns:
                merged.group_by_columns.append(gb)
        for ob in hints.order_by_columns:
            if ob not in merged.order_by_columns:
                merged.order_by_columns.append(ob)
        if hints.limit_value is not None and merged.limit_value is None:
            merged.limit_value = hints.limit_value
        if hints.has_distinct:
            merged.has_distinct = True
        if hints.has_subquery:
            merged.has_subquery = True
        if hints.has_having:
            merged.has_having = True

    return merged


def get_real_tables(ast: sqlglot.Expression) -> list[str]:
    """CTE 오인을 방지하여 실제 테이블명만 추출한다.

    find_all(exp.Table) 대신 traverse_scope()를 사용하여
    CTE 별칭을 실제 테이블로 잘못 반환하는 문제를 해소한다.
    """
    tables: list[str] = []
    for scope in traverse_scope(ast):
        for _alias, (node, source) in scope.selected_sources.items():
            if isinstance(source, exp.Table):
                name = source.name
                if name and name not in tables:
                    tables.append(name)
    return tables


def get_real_columns(ast: sqlglot.Expression) -> list[str]:
    """SQL AST에서 실제 참조되는 컬럼명만 추출한다.

    테이블 alias, 리터럴, 함수명, 와일드카드(*)는 제외한다.
    SELECT/WHERE/JOIN/GROUP BY/ORDER BY 등 모든 절의 컬럼을 수집한다.
    """
    columns: set[str] = set()
    for col_node in ast.find_all(exp.Column):
        name = col_node.name
        if name and name != "*":
            columns.add(name)
    return sorted(columns)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 내부 추출 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _build_alias_map(ast: sqlglot.Expression) -> dict[str, str]:
    """SQL AST에서 alias → 테이블명 매핑을 구성한다."""
    alias_map: dict[str, str] = {}
    for scope in traverse_scope(ast):
        for alias, (_node, source) in scope.selected_sources.items():
            if isinstance(source, exp.Table) and source.name:
                alias_map[alias] = source.name
    return alias_map


def _resolve_column_table(
    col: exp.Column, alias_map: dict[str, str],
) -> str:
    """Column 노드의 alias를 실제 테이블명으로 치환한 문자열을 반환한다."""
    table_ref = col.table or ""
    col_name = col.name
    real_table = alias_map.get(table_ref, table_ref)
    if real_table:
        return f"{real_table}.{col_name}"
    return col_name


def _extract_join_patterns(
    ast: sqlglot.Expression,
    alias_map: dict[str, str] | None = None,
) -> list[str]:
    """JOIN ON 절에서 조인 조건을 추출한다.

    alias_map이 있으면 alias를 실제 테이블명으로 치환한다.
    예: ci.BLNG_BRCD = b.BLNG_BRCD → TB_ADW_CSC101M.BLNG_BRCD = TB_ADW_COM001M.BLNG_BRCD
    """
    if alias_map is None:
        alias_map = {}
    patterns: list[str] = []
    for join_node in ast.find_all(exp.Join):
        on_clause = join_node.args.get("on")
        if not on_clause:
            continue
        # EQ 조건에서 좌/우 컬럼을 테이블명으로 치환
        for eq in on_clause.find_all(exp.EQ):
            left = eq.this
            right = eq.expression
            if isinstance(left, exp.Column) and isinstance(right, exp.Column):
                l_str = _resolve_column_table(left, alias_map)
                r_str = _resolve_column_table(right, alias_map)
                pattern = f"{l_str} = {r_str}"
                if pattern not in patterns:
                    patterns.append(pattern)
    return patterns


def _extract_code_columns(ast: sqlglot.Expression) -> dict[str, list[str]]:
    """WHERE/HAVING 절에서 코드성 컬럼과 리터럴 값을 추출한다."""
    code_cols: dict[str, list[str]] = {}

    for eq_node in ast.find_all(exp.EQ):
        col, val = _extract_col_literal(eq_node)
        if col and val:
            code_cols.setdefault(col, [])
            if val not in code_cols[col]:
                code_cols[col].append(val)

    for in_node in ast.find_all(exp.In):
        col_expr = in_node.this
        if isinstance(col_expr, exp.Column):
            col_name = col_expr.name
            for lit in in_node.find_all(exp.Literal):
                val = lit.this
                if val:
                    code_cols.setdefault(col_name, [])
                    if val not in code_cols[col_name]:
                        code_cols[col_name].append(val)

    return code_cols


def _extract_agg_expressions(
    ast: sqlglot.Expression,
    alias_map: dict[str, str] | None = None,
) -> list[str]:
    """SELECT 절에서 집계 함수를 추출한다 (alias → 테이블명 치환)."""
    if alias_map is None:
        alias_map = {}
    aggs: list[str] = []
    for agg_node in ast.find_all(exp.AggFunc):
        agg_sql = agg_node.sql()
        for alias_key, real_table in alias_map.items():
            if f"{alias_key}." in agg_sql:
                agg_sql = agg_sql.replace(
                    f"{alias_key}.", f"{real_table}.",
                )
        if agg_sql and agg_sql not in aggs:
            aggs.append(agg_sql)
    return aggs


def _extract_date_filters(ast: sqlglot.Expression) -> list[dict[str, str]]:
    """WHERE 절에서 날짜 컬럼과 포맷을 추출한다."""
    date_col_pattern = re.compile(
        r"(DT|DATE|YMD|YYMM|_DT$|_DATE$)", re.IGNORECASE,
    )
    date_filters: list[dict[str, str]] = []

    for eq_node in ast.find_all(exp.EQ, exp.GTE, exp.LTE, exp.Between):
        for col_node in eq_node.find_all(exp.Column):
            col_name = col_node.name
            if date_col_pattern.search(col_name):
                for lit in eq_node.find_all(exp.Literal):
                    fmt = _infer_date_format(lit.this)
                    if fmt:
                        entry = {"column": col_name, "format": fmt}
                        if entry not in date_filters:
                            date_filters.append(entry)
                        break

    return date_filters


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 신규 추출 함수 (SELECT 구조, GROUP BY, ORDER BY 등)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _extract_select_columns(
    ast: sqlglot.Expression,
    alias_map: dict[str, str],
) -> list[str]:
    """SELECT 절의 출력 컬럼을 추출한다.

    alias가 있으면 포함: "COUNT(DISTINCT TB_ADW_CSC101M.EDPS_CSN) AS 고객수"
    """
    select = ast.find(exp.Select)
    if not select:
        return []
    columns: list[str] = []
    for sel_expr in select.expressions:
        # alias 치환: col.table → 실제 테이블명
        sql_text = sel_expr.sql()
        for alias_key, real_table in alias_map.items():
            if f"{alias_key}." in sql_text:
                sql_text = sql_text.replace(
                    f"{alias_key}.", f"{real_table}.",
                )
        if sql_text and sql_text not in columns:
            columns.append(sql_text)
    return columns


def _extract_group_by(
    ast: sqlglot.Expression,
    alias_map: dict[str, str],
) -> list[str]:
    """GROUP BY 컬럼을 추출한다 (alias → 테이블명 치환)."""
    group = ast.find(exp.Group)
    if not group:
        return []
    columns: list[str] = []
    for g_expr in group.expressions:
        sql_text = g_expr.sql()
        for alias_key, real_table in alias_map.items():
            if f"{alias_key}." in sql_text:
                sql_text = sql_text.replace(
                    f"{alias_key}.", f"{real_table}.",
                )
        if sql_text and sql_text not in columns:
            columns.append(sql_text)
    return columns


def _extract_order_by(ast: sqlglot.Expression) -> list[str]:
    """ORDER BY 절을 추출한다."""
    order = ast.find(exp.Order)
    if not order:
        return []
    return [o.sql() for o in order.expressions]


def _extract_limit(ast: sqlglot.Expression) -> int | None:
    """LIMIT 값을 추출한다."""
    limit = ast.find(exp.Limit)
    if limit and limit.expression:
        try:
            return int(limit.expression.sql())
        except (ValueError, TypeError):
            return None
    return None


def _has_distinct(ast: sqlglot.Expression) -> bool:
    """DISTINCT 사용 여부를 확인한다."""
    return bool(ast.find(exp.Distinct))


def _has_subquery(ast: sqlglot.Expression) -> bool:
    """서브쿼리 존재 여부를 확인한다."""
    return bool(ast.find(exp.Subquery))


def _has_having(ast: sqlglot.Expression) -> bool:
    """HAVING 절 존재 여부를 확인한다."""
    return bool(ast.find(exp.Having))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 유틸리티
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _preprocess_dialect_quirks(sql: str, dialect: str | None) -> str:
    """방언별 비표준 구문 전처리."""
    if dialect == "hive":
        sql = re.sub(r"/\*\s*\+.*?\*/", "", sql)
        sql = re.sub(r"\[\s*(broadcast|shuffle|noshuffle)\s*\]", "", sql)
    return sql


def _extract_col_literal(
    eq_node: exp.EQ,
) -> tuple[Optional[str], Optional[str]]:
    """EQ 노드에서 Column = Literal 패턴을 추출."""
    left, right = eq_node.this, eq_node.expression
    if isinstance(left, exp.Column) and isinstance(right, exp.Literal):
        return left.name, right.this
    if isinstance(right, exp.Column) and isinstance(left, exp.Literal):
        return right.name, left.this
    return None, None


def _normalize_join(join_str: str) -> str:
    """조인 조건 정규화 (비교 순서 무관하게 동일 취급)."""
    parts = join_str.replace(" ", "").split("=")
    return "=".join(sorted(parts))


def _infer_date_format(value: str) -> Optional[str]:
    """리터럴 값에서 날짜 포맷을 추론한다."""
    if not value:
        return None
    cleaned = value.strip("'\"")
    if re.match(r"^\d{8}$", cleaned):
        return "YYYYMMDD"
    if re.match(r"^\d{4}-\d{2}-\d{2}$", cleaned):
        return "YYYY-MM-DD"
    if re.match(r"^\d{6}$", cleaned):
        return "YYYYMM"
    if re.match(r"^\d{4}-\d{2}$", cleaned):
        return "YYYY-MM"
    return None
