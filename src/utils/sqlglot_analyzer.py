"""sqlglot 기반 SQL 파싱 및 구조 분석 유틸리티.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

유사 SQL 이력에서 조인 패턴, 코드성 컬럼 값, 집계 패턴, 날짜 조건 등
12가지 구조적 힌트를 추출하여 LLM에 압축된 구조 정보를 제공한다.

LLM에 SQL 원문을 그대로 전달하면 토큰 낭비가 크고 핵심 패턴이 묻히므로,
sqlglot AST를 활용해 조인/집계/필터 등 구조적 특징만 추출하여 프롬프트에
주입한다. 이를 통해 LLM이 유사 SQL의 테이블 조합, 코드값 사용 패턴 등을
효율적으로 참조할 수 있다.

테이블 추출 전략: sqlglot AST 파싱을 우선 시도하되, Sybase IQ의
BEGIN...END / IF...THEN 등 비표준 PL/SQL 블록은 파싱 실패하므로
폐쇄망 테이블 네이밍 규칙(TB_XXX_XXXXXXX)을 regex fallback으로 보완한다.

핵심 함수:
  - extract_structural_hints: SQL 원문 → dict (12가지 구조 힌트)
  - parse_sql_safe: 방언별 안전한 파싱 (실패 시 None, 흐름 차단 없음)
  - merge_hints: 다수 SQL에서 추출한 힌트 dict 병합 (중복 제거 + 빈도 기반)
  - get_real_tables: AST + regex fallback으로 테이블 추출
  - get_real_columns: AST에서 실제 참조 컬럼만 추출 (alias/함수 제외)
  - extract_select_alias_map: SELECT 절의 출력alias → 원본컬럼 매핑
"""

from __future__ import annotations

import re
from typing import Any, Optional

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope


def extract_structural_hints(
    sql: str,
    dialect: str | None = None,
) -> dict[str, Any]:
    """SQL 원문에서 12가지 구조적 힌트를 추출한다.

    파싱 실패 시 빈 dict를 반환한다 (agent 흐름 차단 없음).

    Returns:
        StructuralHints 필드명을 키로 하는 dict.
        호출측에서 StructuralHints(**result)로 변환 가능.
    """
    # 테이블은 AST 실패 시에도 regex fallback으로 추출 가능
    source_tables = get_real_tables(sql, dialect)

    ast = parse_sql_safe(sql, dialect)
    if ast is None:
        # AST 파싱 실패 — 테이블만이라도 반환
        return {"source_tables": source_tables} if source_tables else {}

    # alias -> 테이블명 매핑 구성
    alias_map = _build_alias_map(ast)

    return {
        # 기존 4가지 (join_patterns는 테이블명으로 치환)
        "join_patterns": _extract_join_patterns(ast, alias_map),
        "code_columns": _extract_code_columns(ast),
        "agg_expressions": _extract_agg_expressions(
            ast, alias_map,
        ),
        "date_filters": _extract_date_filters(ast),
        # 테이블 정보
        "source_tables": source_tables,
        # SELECT 출력 구조
        "select_columns": _extract_select_columns(
            ast, alias_map,
        ),
        "group_by_columns": _extract_group_by(ast, alias_map),
        "order_by_columns": _extract_order_by(ast),
        "limit_value": _extract_limit(ast),
        "has_distinct": _has_distinct(ast),
        "has_subquery": _has_subquery(ast),
        "has_having": _has_having(ast),
    }


def parse_sql_safe(
    sql: str,
    dialect: str | None = None,
) -> Optional[sqlglot.Expression]:
    """sqlglot 안전 파싱 -- 실패 시 None 반환.

    힌트 추출은 보조 정보이므로 파싱 실패가 전체 흐름을 차단하지 않는다.
    """
    try:
        cleaned = _preprocess_dialect_quirks(sql, dialect)
        ast = sqlglot.parse_one(
            cleaned,
            dialect=dialect,
            error_level=sqlglot.ErrorLevel.RAISE,
        )
        # Command 노드는 미지원 구문 -- 빈 힌트 폴백
        if isinstance(ast, exp.Command):
            return None
        return ast
    except sqlglot.errors.ParseError:
        return None


def merge_hints(
    hints_list: list[dict[str, Any]],
) -> dict[str, Any]:
    """다수 SQL에서 추출한 힌트 dict를 병합한다.

    중복 제거 + 빈도 기반 우선순위 부여.
    """
    merged: dict[str, Any] = {
        "join_patterns": [],
        "code_columns": {},
        "agg_expressions": [],
        "date_filters": [],
        "source_tables": [],
        "select_columns": [],
        "group_by_columns": [],
        "order_by_columns": [],
        "limit_value": None,
        "has_distinct": False,
        "has_subquery": False,
        "has_having": False,
    }

    seen_joins: set[str] = set()
    seen_aggs: set[str] = set()

    for hints in hints_list:
        if not hints:
            continue

        for jp in hints.get("join_patterns", []):
            normalized = _normalize_join(jp)
            if normalized not in seen_joins:
                seen_joins.add(normalized)
                merged["join_patterns"].append(jp)

        for col, vals in hints.get("code_columns", {}).items():
            if col not in merged["code_columns"]:
                merged["code_columns"][col] = []
            for v in vals:
                if v not in merged["code_columns"][col]:
                    merged["code_columns"][col].append(v)

        for agg in hints.get("agg_expressions", []):
            if agg.upper() not in seen_aggs:
                seen_aggs.add(agg.upper())
                merged["agg_expressions"].append(agg)

        for df in hints.get("date_filters", []):
            if df not in merged["date_filters"]:
                merged["date_filters"].append(df)

        for t in hints.get("source_tables", []):
            if t not in merged["source_tables"]:
                merged["source_tables"].append(t)
        for sc in hints.get("select_columns", []):
            if sc not in merged["select_columns"]:
                merged["select_columns"].append(sc)
        for gb in hints.get("group_by_columns", []):
            if gb not in merged["group_by_columns"]:
                merged["group_by_columns"].append(gb)
        for ob in hints.get("order_by_columns", []):
            if ob not in merged["order_by_columns"]:
                merged["order_by_columns"].append(ob)
        if (
            hints.get("limit_value") is not None
            and merged["limit_value"] is None
        ):
            merged["limit_value"] = hints["limit_value"]
        if hints.get("has_distinct"):
            merged["has_distinct"] = True
        if hints.get("has_subquery"):
            merged["has_subquery"] = True
        if hints.get("has_having"):
            merged["has_having"] = True

    return merged


def extract_select_alias_map(
    sql: str,
    dialect: str | None = None,
) -> dict[str, str | None]:
    """SELECT 절에서 {출력alias: 원본컬럼명} 매핑을 추출한다.

    예: SELECT A.LOAN_DCD AS 대출구분 → {"대출구분": "LOAN_DCD"}
        SELECT COUNT(*) AS 건수       → {"건수": None}  (집계/함수는 None)
        SELECT A.LOAN_DCD             → {"LOAN_DCD": "LOAN_DCD"}
    """
    ast = parse_sql_safe(sql, dialect)
    if ast is None:
        return {}

    alias_map: dict[str, str | None] = {}

    select = ast.find(exp.Select)
    if not select:
        return {}

    for sel_expr in select.expressions:
        if isinstance(sel_expr, exp.Alias):
            alias_name = sel_expr.alias
            child = sel_expr.this
            if isinstance(child, exp.Column):
                alias_map[alias_name] = child.name
            else:
                alias_map[alias_name] = None
        elif isinstance(sel_expr, exp.Column):
            alias_map[sel_expr.name] = sel_expr.name

    return alias_map


def _extract_tables_from_ast(ast: sqlglot.Expression) -> list[str]:
    """CTE 오인을 방지하여 AST에서 실제 테이블명만 추출한다 (내부용).

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


# ── 폐쇄망 테이블 네이밍 패턴 (TB_{시스템3자리}_{영숫자7자리}) ──
_TABLE_NAME_RE = re.compile(r"\b(TB_[A-Z]{3}_[A-Z0-9]{7})\b", re.IGNORECASE)

# 주석·문자열 리터럴 제거 (regex fallback 오탐 방지)
_COMMENT_OR_LITERAL_RE = re.compile(
    r"--[^\n]*"           # 한줄 주석
    r"|/\*.*?\*/"         # 블록 주석
    r"|'(?:[^']|'')*'"    # 작은따옴표 문자열 ('' 이스케이프 포함)
    r'|"(?:[^"]|"")*"',   # 큰따옴표 문자열
    re.DOTALL,
)


def _strip_comments_and_literals(sql: str) -> str:
    """SQL에서 주석과 문자열 리터럴을 제거한다."""
    return _COMMENT_OR_LITERAL_RE.sub(" ", sql)


def get_real_tables(
    sql: str,
    dialect: str | None = None,
) -> list[str]:
    """AST 파싱 + regex fallback으로 테이블명을 추출한다.

    1차: sqlglot AST 파싱 성공 시 정밀 추출 (CTE 제외).
    2차: 테이블 네이밍 규칙(TB_XXX_XXXXXXX) regex로 보완.

    Sybase IQ의 BEGIN...END / IF...THEN 등 PL/SQL 블록은
    sqlglot이 파싱하지 못하므로 regex fallback이 필수다.
    주석과 문자열 리터럴 내 테이블명은 제거 후 매칭하여 오탐을 방지한다.
    """
    ast = parse_sql_safe(sql, dialect)

    # 1차: AST 파싱 성공 시 정밀 추출
    ast_tables: list[str] = []
    if ast is not None:
        ast_tables = _extract_tables_from_ast(ast)

    # 2차: regex fallback — 주석·리터럴 제거 후 매칭
    cleaned = _strip_comments_and_literals(sql)
    regex_tables = _TABLE_NAME_RE.findall(cleaned)
    # 대소문자 무관 중복 제거
    seen_upper: set[str] = {t.upper() for t in ast_tables}
    for t in regex_tables:
        if t.upper() not in seen_upper:
            seen_upper.add(t.upper())
            ast_tables.append(t)

    return ast_tables


def get_real_columns(ast: sqlglot.Expression) -> list[str]:
    """SQL AST에서 실제 참조되는 컬럼명만 추출한다.

    테이블 alias, 리터럴, 함수명, 와일드카드(*)는 제외한다.
    SELECT/WHERE/JOIN/GROUP BY/ORDER BY 등 모든 절의 컬럼을 수집한다.
    ORDER BY/HAVING에서 SELECT alias를 참조하는 경우는 제외한다.
    """
    # SELECT 절의 alias 수집 (ORDER BY에서 alias 참조 시 오탐 방지)
    select_aliases: set[str] = set()
    for alias_node in ast.find_all(exp.Alias):
        if alias_node.alias:
            select_aliases.add(alias_node.alias)

    columns: set[str] = set()
    for col_node in ast.find_all(exp.Column):
        name = col_node.name
        if name and name != "*" and name not in select_aliases:
            columns.add(name)
    return sorted(columns)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 내부 추출 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _build_alias_map(ast: sqlglot.Expression) -> dict[str, str]:
    """SQL AST에서 alias -> 테이블명 매핑을 구성한다."""
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
    """JOIN ON 절에서 조인 조건을 추출한다."""
    if alias_map is None:
        alias_map = {}
    patterns: list[str] = []
    for join_node in ast.find_all(exp.Join):
        on_clause = join_node.args.get("on")
        if not on_clause:
            continue
        for eq in on_clause.find_all(exp.EQ):
            left = eq.this
            right = eq.expression
            if isinstance(left, exp.Column) and isinstance(
                right, exp.Column,
            ):
                l_str = _resolve_column_table(left, alias_map)
                r_str = _resolve_column_table(right, alias_map)
                pattern = f"{l_str} = {r_str}"
                if pattern not in patterns:
                    patterns.append(pattern)
    return patterns


def _extract_code_columns(
    ast: sqlglot.Expression,
) -> dict[str, list[str]]:
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
    """SELECT 절에서 집계 함수를 추출한다."""
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


def _extract_date_filters(
    ast: sqlglot.Expression,
) -> list[dict[str, str]]:
    """WHERE 절에서 날짜 컬럼과 포맷을 추출한다."""
    date_col_pattern = re.compile(
        r"(DT|DATE|YMD|YYMM|_DT$|_DATE$)", re.IGNORECASE,
    )
    date_filters: list[dict[str, str]] = []

    for eq_node in ast.find_all(
        exp.EQ, exp.GTE, exp.LTE, exp.Between,
    ):
        for col_node in eq_node.find_all(exp.Column):
            col_name = col_node.name
            if date_col_pattern.search(col_name):
                for lit in eq_node.find_all(exp.Literal):
                    fmt = _infer_date_format(lit.this)
                    if fmt:
                        entry = {
                            "column": col_name,
                            "format": fmt,
                        }
                        if entry not in date_filters:
                            date_filters.append(entry)
                        break

    return date_filters


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SELECT 구조, GROUP BY, ORDER BY 등
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _extract_select_columns(
    ast: sqlglot.Expression,
    alias_map: dict[str, str],
) -> list[str]:
    """SELECT 절의 출력 컬럼을 추출한다."""
    select = ast.find(exp.Select)
    if not select:
        return []
    columns: list[str] = []
    for sel_expr in select.expressions:
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
    """GROUP BY 컬럼을 추출한다."""
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

def _preprocess_dialect_quirks(
    sql: str, dialect: str | None,
) -> str:
    """방언별 비표준 구문 전처리."""
    if dialect == "hive":
        sql = re.sub(r"/\*\s*\+.*?\*/", "", sql)
        sql = re.sub(
            r"\[\s*(broadcast|shuffle|noshuffle)\s*\]",
            "", sql,
        )
    return sql


def _extract_col_literal(
    eq_node: exp.EQ,
) -> tuple[Optional[str], Optional[str]]:
    """EQ 노드에서 Column = Literal 패턴을 추출."""
    left, right = eq_node.this, eq_node.expression
    if isinstance(left, exp.Column) and isinstance(
        right, exp.Literal,
    ):
        return left.name, right.this
    if isinstance(right, exp.Column) and isinstance(
        left, exp.Literal,
    ):
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
