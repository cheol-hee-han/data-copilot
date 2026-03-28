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

from prototype.agentic_state import StructuralHints


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
    """SQL 원문에서 4가지 구조적 힌트를 추출한다.

    파싱 실패 시 빈 힌트를 반환한다 (agent 흐름 차단 없음).
    """
    ast = parse_sql_safe(sql, dialect)
    if ast is None:
        return StructuralHints()

    return StructuralHints(
        join_patterns=_extract_join_patterns(ast),
        code_columns=_extract_code_columns(ast),
        agg_expressions=_extract_agg_expressions(ast),
        date_filters=_extract_date_filters(ast),
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

    return merged


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 내부 추출 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _extract_join_patterns(ast: sqlglot.Expression) -> list[str]:
    """JOIN ON 절에서 조인 조건을 추출한다."""
    patterns: list[str] = []
    for join_node in ast.find_all(exp.Join):
        on_clause = join_node.args.get("on")
        if on_clause:
            patterns.append(on_clause.sql())
    return patterns


def _extract_code_columns(ast: sqlglot.Expression) -> dict[str, list[str]]:
    """WHERE/HAVING 절에서 코드성 컬럼과 리터럴 값을 추출한다.

    col = 'literal' 또는 col IN ('a', 'b') 패턴을 감지한다.
    """
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


def _extract_agg_expressions(ast: sqlglot.Expression) -> list[str]:
    """SELECT 절에서 집계 함수를 추출한다."""
    aggs: list[str] = []
    for agg_node in ast.find_all(exp.AggFunc):
        agg_sql = agg_node.sql()
        if agg_sql and agg_sql not in aggs:
            aggs.append(agg_sql)
    return aggs


def _extract_date_filters(ast: sqlglot.Expression) -> list[dict[str, str]]:
    """WHERE 절에서 날짜 컬럼과 포맷을 추출한다.

    날짜 컬럼 패턴: *_DT, *_DATE, *_YMD, *_YYMM
    날짜 리터럴 포맷: YYYYMMDD, YYYY-MM-DD 등
    """
    date_col_pattern = re.compile(
        r"(DT|DATE|YMD|YYMM|_DT$|_DATE$)", re.IGNORECASE,
    )
    date_filters: list[dict[str, str]] = []

    for eq_node in ast.find_all(exp.EQ, exp.GTE, exp.LTE, exp.Between):
        for col_node in eq_node.find_all(exp.Column):
            col_name = col_node.name
            if date_col_pattern.search(col_name):
                # 포맷 추론
                for lit in eq_node.find_all(exp.Literal):
                    fmt = _infer_date_format(lit.this)
                    if fmt:
                        entry = {"column": col_name, "format": fmt}
                        if entry not in date_filters:
                            date_filters.append(entry)
                        break

    return date_filters


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 유틸리티
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _preprocess_dialect_quirks(sql: str, dialect: str | None) -> str:
    """방언별 비표준 구문 전처리."""
    if dialect == "hive":
        # Impala 힌트 제거
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
