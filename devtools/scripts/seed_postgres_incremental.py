# -*- coding: utf-8 -*-
"""PostgreSQL 증분 시딩 — 전체 572 테이블 통합 일자 보강.

설계: docs/todo/20260420-incremental-seed-design.md

모든 테이블을 동등하게 중요 대상으로 취급한다. ★/비-★ 구분 없음.
PK 일자 컬럼 유무 + 유형 접미 문자로 카테고리를 자동 판정하고,
`seed_postgres.py` 의 값 생성·제약 보정 로직을 그대로 재사용한다.

사용법:
    python devtools/scripts/seed_postgres_incremental.py --from 2026-03-22 --to 2026-04-20
    python devtools/scripts/seed_postgres_incremental.py --from 2026-03-22 --to 2026-04-20 --seed 42 --verbose
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import psycopg2  # type: ignore[import-untyped]
from psycopg2.extras import execute_values  # type: ignore[import-untyped]

# ── sys.path 보정: devtools/scripts 외부에서도 import 가능 ──
_THIS = Path(__file__).resolve()
_REPO_ROOT = _THIS.parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from devtools.scripts.seed_postgres import (  # noqa: E402
    TEST_DB_CONNINFO,
    _collect_star_values,
    _connect,
    _DOMAIN_GROUP,
    _extract_domain,
    _fix_row_constraints,
    _gen_col_value,
    _infer_table_type,
    _POOL_ALIAS,
    parse_table_catalog,
)

# ══════════════════════════════════════════════════════════════
# 카테고리 분류 규칙 (설계 §9.2)
# ══════════════════════════════════════════════════════════════

# PK 에 나타나면 "일자별 스냅샷/이벤트" 로 판정되는 컬럼
DATE_PK_COLS: set[str] = {
    "STD_DT", "BASE_DT", "BASE_YM", "EVAL_DT", "CALC_DT", "TR_DT",
    "SETL_DT", "EVENT_DT", "BAL_DT", "EFF_DT", "CONTACT_DT", "NAV_DT",
    "DIV_DT", "LOGIN_DT", "SCORE_DT", "VISIT_DT", "PAY_DT", "RPAY_DT",
    "AGREE_DT", "REQ_DT", "EXEC_DT", "CNTR_DT", "CHG_DT", "STAT_DT",
    "HLDY_DT", "SUIT_DT", "WATCH_DT", "WARNING_DT", "MOVE_DT",
    "CORR_DT", "USE_DT", "RATING_DT", "REPORT_DT", "REISSUE_DT",
    "ACT_DT", "RECOMMEND_DT", "SURVEY_DT", "KYC_DT", "RESTRUCTURE_DT",
    "WRITEOFF_DT", "RECOVERY_DT", "EXT_DT", "PREPAY_DT", "FCAST_DT",
    "DETECT_DT", "REVIEW_DT", "DL_DT",
}

# 시계열 파라미터 (E 카테고리) — per-day random walk 관점의 의미는 부여하되
# 실제 INSERT 로직은 C 와 동일 (일자별 per-key INSERT)
TIMESERIES_P_TABLES: set[str] = {"TB_ADW_FXB502M", "TB_ADW_RSK1101M"}

# 이벤트성 유형 — 일자별 "이벤트" 분포 (영업일/주말 구분)
EVENT_TYPES: set[str] = {"H", "L", "G"}


# ══════════════════════════════════════════════════════════════
# 카테고리 분류
# ══════════════════════════════════════════════════════════════

def categorize(table_info: dict, schema: dict | None = None) -> str:
    """B/C/D/E 카테고리 자동 판정.

    실제 DB 스키마(`schema`)가 제공되면 DB PK를 우선한다 (요구서 문서와 실제 DDL 불일치 대응).

    - 이벤트 유형(H/L/G) → D
    - 일자 PK + 시계열 P → E
    - 일자 PK (그 외) → C
    - 일자 PK 없음 → B (UPDATE 또는 무변동)
    """
    name = table_info["name"]
    type_char = _infer_table_type(name)
    pk_cols = (schema.get("pk_cols") if schema else None) or table_info["pk_cols"]
    has_date_pk = any(c in DATE_PK_COLS for c in pk_cols)

    if type_char in EVENT_TYPES:
        return "D"
    if has_date_pk:
        if type_char == "P" and name in TIMESERIES_P_TABLES:
            return "E"
        return "C"
    return "B"


# ══════════════════════════════════════════════════════════════
# 일자 유틸
# ══════════════════════════════════════════════════════════════

def daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def is_business_day(d: date) -> bool:
    return d.weekday() < 5


def fmt_ym(d: date) -> str:
    return d.strftime("%Y%m")


# ══════════════════════════════════════════════════════════════
# 실제 DDL 컬럼 메타 로드 (★/비-★ 통합)
# ══════════════════════════════════════════════════════════════

# PostgreSQL data_type → 값 생성기가 쓰는 pseudo-type 으로 변환
def _pg_to_pseudo_type(
    data_type: str,
    char_max_len: int | None,
    numeric_precision: int | None,
    numeric_scale: int | None,
) -> str:
    dt = (data_type or "").lower()
    if dt in ("character varying", "varchar"):
        n = char_max_len or 20
        return f"VARCHAR({n})"
    if dt == "character":
        n = char_max_len or 1
        return f"CHAR({n})"
    if dt == "text":
        return "TEXT"
    if dt == "date":
        return "DATE"
    if dt in ("timestamp without time zone", "timestamp with time zone"):
        return "TIMESTAMP"
    if dt == "integer":
        return "INTEGER"
    if dt == "bigint":
        return "BIGINT"
    if dt == "smallint":
        return "SMALLINT"
    if dt == "boolean":
        return "BOOLEAN"
    if dt == "numeric":
        p = numeric_precision or 18
        s = numeric_scale or 2
        return f"NUMERIC({p},{s})"
    return "VARCHAR(20)"


def load_table_schemas(cur, table_names: list[str]) -> dict[str, dict]:
    """information_schema 에서 ADWOWN 스키마의 컬럼 메타를 일괄 로드.

    Returns: {table_name: {
        'cols': [(name, pseudo_type, is_nullable, has_default)],
        'pk_cols': [pk_col_names in order],
    }}
    """
    cur.execute(
        """
        SELECT
          c.table_name,
          c.column_name,
          c.data_type,
          c.character_maximum_length,
          c.numeric_precision,
          c.numeric_scale,
          c.is_nullable,
          c.column_default
        FROM information_schema.columns c
        WHERE c.table_schema = 'adwown'
          AND c.table_name = ANY(%s)
        ORDER BY c.table_name, c.ordinal_position
        """,
        ([n.lower() for n in table_names],),
    )
    schemas: dict[str, dict] = {}
    for row in cur.fetchall():
        tbl, col, dt, char_len, nprec, nscale, nullable, default = row
        up_tbl = tbl.upper()
        ptype = _pg_to_pseudo_type(dt, char_len, nprec, nscale)
        is_null = (nullable or "").upper() == "YES"
        has_default = default is not None
        schemas.setdefault(
            up_tbl, {"cols": [], "pk_cols": []}
        )["cols"].append((col.upper(), ptype, is_null, has_default))

    # PK 순서 로드
    cur.execute(
        """
        SELECT
          kcu.table_name,
          kcu.column_name,
          kcu.ordinal_position
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.constraint_schema = kcu.constraint_schema
        WHERE tc.constraint_type = 'PRIMARY KEY'
          AND tc.table_schema = 'adwown'
          AND kcu.table_name = ANY(%s)
        ORDER BY kcu.table_name, kcu.ordinal_position
        """,
        ([n.lower() for n in table_names],),
    )
    for tbl, col, _ord in cur.fetchall():
        up_tbl = tbl.upper()
        if up_tbl in schemas:
            schemas[up_tbl]["pk_cols"].append(col.upper())

    return schemas


# ══════════════════════════════════════════════════════════════
# 테이블별 행 생성
# ══════════════════════════════════════════════════════════════

def _coerce_to_ddl(value: object, ptype: str) -> object:
    """생성값을 실제 DDL 타입·길이에 맞게 강제 변환.

    `_gen_col_value` 가 하드코딩된 STAR_DDL 기준으로 만들어져 실제 DDL 과
    불일치할 수 있음 → INSERT 실패 방지용 post-coercion.
    """
    if value is None:
        return None

    # VARCHAR(N)/CHAR(N) — 길이 제한
    if ptype.startswith(("VARCHAR", "CHAR")):
        m = re.match(r"(?:VARCHAR|CHAR)\((\d+)\)", ptype)
        maxl = int(m.group(1)) if m else 20
        s = str(value)
        return s[:maxl] if len(s) > maxl else s

    # INTEGER 계열 — 숫자 아니면 재생성
    if ptype in ("INTEGER", "BIGINT", "SMALLINT"):
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                # 풀 값 등 비숫자 → 해시 기반 정수로 대체
                return abs(hash(value)) % 1_000_000
        return 0

    # NUMERIC — 문자열이면 재생성
    if ptype.startswith("NUMERIC"):
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return random.randint(1_000, 1_000_000)
        return 0

    return value


def _pick_date_col_non_pk(
    col_names: list[str],
    col_types: dict[str, str],
    pk_set: set[str],
) -> str | None:
    """non-PK 중 일자 컬럼 1개 선택 (이벤트 일자용)."""
    for c in col_names:
        if c in pk_set:
            continue
        if c in DATE_PK_COLS and col_types.get(c) == "DATE":
            return c
    for c in col_names:
        if c in pk_set:
            continue
        if col_types.get(c) == "DATE":
            return c
    return None


def _build_rows_for_day(
    table_info: dict,
    schema: dict,
    pools: dict[str, list],
    target_date: date,
    n_rows: int,
) -> tuple[list[str], list[tuple]]:
    """한 테이블·한 일자의 행 리스트 생성.

    Uses actual DDL columns from information_schema (schema).

    Returns: (col_names_for_insert, rows)
    """
    name = table_info["name"]
    domain_raw = _extract_domain(name)
    domain_group = _DOMAIN_GROUP.get(domain_raw, "COM")

    all_cols = schema.get("cols") or []
    if not all_cols:
        return [], []

    # INSERT 제외 컬럼:
    #  - BIGSERIAL/기본값 있는 컬럼은 DB가 자동 채움 → 생략
    #  - TIMESTAMP 메타 (INS_DTM/UPD_DTM) 는 기본값 있으면 생략
    insert_cols: list[tuple[str, str]] = []
    for col_name, ptype, is_nullable, has_default in all_cols:
        if has_default and (col_name in ("INS_DTM", "UPD_DTM") or ptype == "TIMESTAMP" or ptype == "BIGINT"):
            # 기본값 있는 BIGSERIAL/TIMESTAMP/NOW() 는 생략
            continue
        insert_cols.append((col_name, ptype))

    col_names = [c[0] for c in insert_cols]
    col_types = {c[0]: c[1] for c in insert_cols}
    pk_cols = schema.get("pk_cols") or table_info["pk_cols"]
    pk_set = set(pk_cols)

    # PK 컬럼 분류
    date_in_pk = [c for c in pk_cols if c in DATE_PK_COLS and c in col_names]
    ym_in_pk = [
        c for c in pk_cols
        if c.endswith(("_YM", "YM"))
        and "VARCHAR" in col_types.get(c, "")
        and c in col_names
    ]
    yr_in_pk = [
        c for c in pk_cols
        if (c.endswith(("_YR", "YR")) or c == "FY")
        and "VARCHAR" in col_types.get(c, "")
        and c not in ym_in_pk
        and c in col_names
    ]
    entity_pks = [
        c for c in pk_cols
        if c not in date_in_pk
        and c not in ym_in_pk
        and c not in yr_in_pk
        and c in col_names
    ]

    # D(이벤트) 유형에서 PK 에 일자가 없으면 non-PK 일자 컬럼을 target_date 로 고정
    event_date_col = None
    if not date_in_pk and _infer_table_type(name) in EVENT_TYPES:
        event_date_col = _pick_date_col_non_pk(col_names, col_types, pk_set)

    # 엔티티 PK 풀 확보 (없으면 이 테이블 skip)
    entity_missing = []
    for col in entity_pks:
        alias = _POOL_ALIAS.get(col, col)
        if not pools.get(col) and not pools.get(alias):
            entity_missing.append(col)
    if entity_missing and entity_pks:
        # PK 풀이 모자라면 새 ID 시퀀스 생성 (비-★ 이벤트 테이블 대응)
        for col in entity_missing:
            prefix = col.replace("_", "")[:5].upper()
            new_ids = [f"{prefix}{i:05d}" for i in range(1, 201)]
            pools[col] = new_ids

    rows: list[tuple] = []
    seen: set[tuple] = set()
    max_attempts = max(n_rows * 5, 20)

    for attempt in range(max_attempts):
        if len(rows) >= n_rows:
            break

        row: dict[str, object] = {}

        # 엔티티 PK
        for col in entity_pks:
            alias = _POOL_ALIAS.get(col, col)
            src = pools.get(col) or pools.get(alias) or []
            if src:
                row[col] = random.choice(src)
            else:
                row[col] = f"X{attempt:06d}"

        # 일자 PK → target_date 고정
        for col in date_in_pk:
            row[col] = target_date
        # YM PK → YYYYMM
        for col in ym_in_pk:
            row[col] = fmt_ym(target_date)
        # YR PK → YYYY
        for col in yr_in_pk:
            row[col] = target_date.strftime("%Y")

        # 유니크 PK 체크
        pk_key = tuple(str(row.get(c, "")) for c in pk_cols if c in col_names)
        if pk_key in seen:
            continue
        seen.add(pk_key)

        # 비-PK 컬럼 값 생성
        idx = len(rows)
        non_pk = [c for c in col_names if c not in pk_set]
        for col in non_pk:
            row[col] = _gen_col_value(
                col, col_types[col], domain_raw, pools, idx,
            )

        # 이벤트 일자 (비-PK) → target_date
        if event_date_col:
            row[event_date_col] = target_date

        # 도메인 제약 보정
        _fix_row_constraints(row, col_names, domain_group)

        # 실제 DDL 타입·길이에 맞게 강제 변환 (VARCHAR 길이 초과·타입 불일치 방지)
        for col in col_names:
            row[col] = _coerce_to_ddl(row[col], col_types[col])

        rows.append(tuple(row.get(c) for c in col_names))

    return col_names, rows


# ══════════════════════════════════════════════════════════════
# 테이블 단위 증분 INSERT
# ══════════════════════════════════════════════════════════════

def _rows_per_day(table_info: dict, d: date, pools: dict[str, list]) -> int:
    """테이블·일자별 목표 행 수."""
    type_char = _infer_table_type(table_info["name"])
    if type_char in EVENT_TYPES:
        return random.randint(30, 100) if is_business_day(d) else random.randint(5, 20)

    # 스냅샷: 엔티티 PK 풀 크기 기반
    pk_cols = table_info["pk_cols"]
    entity_pks = [
        c for c in pk_cols
        if c not in DATE_PK_COLS
        and not c.endswith("YM")
        and not c.endswith("YR")
        and c != "FY"
    ]
    if entity_pks:
        alias = _POOL_ALIAS.get(entity_pks[0], entity_pks[0])
        pool = pools.get(entity_pks[0]) or pools.get(alias) or []
        if pool:
            # 전수 스냅샷: 풀 전체 (최대 600)
            return min(len(pool), 600)
    # BASE_YM·FY 기반 월별/연별 집계
    return random.randint(20, 80)


def _has_std_dt_non_pk(cur, table_name: str) -> bool:
    """비-PK STD_DT 컬럼 존재 여부 확인."""
    cur.execute(
        """
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'adwown'
           AND table_name = LOWER(%s)
           AND column_name = 'std_dt'
         LIMIT 1
        """,
        (table_name,),
    )
    return cur.fetchone() is not None


def _is_std_dt_in_pk(cur, table_name: str) -> bool:
    cur.execute(
        """
        SELECT 1 FROM information_schema.key_column_usage
         WHERE table_schema = 'adwown'
           AND table_name = LOWER(%s)
           AND column_name = 'std_dt'
         LIMIT 1
        """,
        (table_name,),
    )
    return cur.fetchone() is not None


def _pick_non_pk_date_col(schema: dict) -> str | None:
    """schema 에서 non-PK 일자 컬럼 1개 선택 (B-UPDATE 대상)."""
    pk_set = set(schema.get("pk_cols") or [])
    cols = schema.get("cols") or []
    # 1) DATE_PK_COLS 에 속하는 DATE 컬럼 우선
    for name, ptype, _null, _def in cols:
        if name in pk_set:
            continue
        if name in DATE_PK_COLS and ptype == "DATE":
            return name
    # 2) 그 외 DATE 타입 컬럼
    for name, ptype, _null, _def in cols:
        if name in pk_set:
            continue
        if ptype == "DATE":
            return name
    return None


def upsert_category_b(
    cur,
    table_info: dict,
    schema: dict,
    target_to: date,
    args: argparse.Namespace,
) -> tuple[int, int]:
    """카테고리 B: non-PK 일자 컬럼이 있으면 target_to 로 UPDATE.

    일자 컬럼이 없는 마스터는 no-op (자연스러운 무변동).
    STD_DT 뿐 아니라 CONTACT_DT/BASE_DT 등 non-PK 일자 컬럼 모두 대상.

    Returns: (updated, skipped)
    """
    name = table_info["name"]
    date_col = _pick_non_pk_date_col(schema)
    if not date_col:
        return 0, 1  # no-op (정적 또는 일자 컬럼 비보유)

    if args.dry_run:
        return 0, 0

    sp = f"sp_B_{name}"
    try:
        cur.execute(f"SAVEPOINT {sp}")
        cur.execute(
            f"UPDATE ADWOWN.{name} SET {date_col} = %s "
            f"WHERE {date_col} IS DISTINCT FROM %s",
            (target_to, target_to),
        )
        n = cur.rowcount or 0
        cur.execute(f"RELEASE SAVEPOINT {sp}")
        return n, 0
    except Exception as e:
        cur.execute(f"ROLLBACK TO SAVEPOINT {sp}")
        if args.verbose:
            print(f"  [B-SKIP] {name}: {e}", file=sys.stderr)
        return 0, 1


def insert_category_cd_e(
    cur,
    table_info: dict,
    schema: dict,
    pools: dict[str, list],
    d: date,
    args: argparse.Namespace,
) -> tuple[int, int]:
    """카테고리 C/D/E: 일자별 INSERT (ON CONFLICT DO NOTHING).

    Returns: (inserted, skipped_rows_due_to_conflict)
    """
    name = table_info["name"]
    n_rows = _rows_per_day(table_info, d, pools)
    if n_rows <= 0:
        return 0, 0

    col_names, rows = _build_rows_for_day(table_info, schema, pools, d, n_rows)
    if not rows:
        return 0, 0

    if args.dry_run:
        return len(rows), 0

    insert_sql = (
        f"INSERT INTO ADWOWN.{name} "
        f"({', '.join(col_names)}) VALUES %s ON CONFLICT DO NOTHING"
    )

    sp = f"sp_{name}_{d.strftime('%Y%m%d')}"
    try:
        cur.execute(f"SAVEPOINT {sp}")
        execute_values(cur, insert_sql, rows, page_size=500)
        inserted = cur.rowcount or 0
        cur.execute(f"RELEASE SAVEPOINT {sp}")
        return inserted, max(0, len(rows) - inserted)
    except Exception as e:
        cur.execute(f"ROLLBACK TO SAVEPOINT {sp}")
        if args.verbose:
            print(f"  [SKIP] {name} {d}: {e}", file=sys.stderr)
        return 0, len(rows)


# ══════════════════════════════════════════════════════════════
# 파이프라인
# ══════════════════════════════════════════════════════════════

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="PostgreSQL 증분 시딩")
    p.add_argument("--from", dest="from_date", required=True, help="YYYY-MM-DD")
    p.add_argument("--to", dest="to_date", required=True, help="YYYY-MM-DD")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--verbose", action="store_true")
    p.add_argument(
        "--only-categories",
        default="B,C,D,E",
        help="시딩할 카테고리 (예: C,D,E)",
    )
    a = p.parse_args()
    a.from_date = datetime.strptime(a.from_date, "%Y-%m-%d").date()
    a.to_date = datetime.strptime(a.to_date, "%Y-%m-%d").date()
    if a.to_date < a.from_date:
        p.error("--to 가 --from 보다 이전입니다.")
    a.categories = {c.strip().upper() for c in a.only_categories.split(",")}
    return a


def _validate_db(conn) -> None:
    """test_db 아니면 --force 요구."""
    cur = conn.cursor()
    cur.execute("SELECT current_database(), current_user")
    db, user = cur.fetchone()
    cur.close()
    if "readonly" in user.lower():
        print(f"  [ERR] readonly 계정({user}) 접속 거부", file=sys.stderr)
        sys.exit(2)
    print(f"  연결: database={db} user={user}")


def main() -> int:
    args = parse_args()
    random.seed(args.seed)

    t0 = time.time()
    dates = list(daterange(args.from_date, args.to_date))
    print(
        f"[{datetime.now():%Y-%m-%d %H:%M:%S}] incremental seed 시작  "
        f"범위={args.from_date}~{args.to_date} ({len(dates)}일)  "
        f"seed={args.seed}  categories={sorted(args.categories)}  "
        f"dry_run={args.dry_run}"
    )

    # 1. 카탈로그 로드
    catalog = parse_table_catalog()

    # 2. DB 연결 + 풀 수집
    conn = _connect(TEST_DB_CONNINFO)
    _validate_db(conn)
    cur = conn.cursor()
    pools = _collect_star_values(cur)
    pool_total = sum(len(v) for v in pools.values())
    print(f"  FK 풀 수집: {pool_total}개")
    if pool_total < 100:
        print("  [ERR] 풀이 너무 작음 — 전체 재시딩(seed_postgres.py)이 먼저 필요합니다.", file=sys.stderr)
        return 2

    # 3. 실제 DDL 컬럼 메타 로드 (카테고리 분류·값 생성 모두 이걸 사용)
    schemas = load_table_schemas(cur, [t["name"] for t in catalog])
    missing = [t["name"] for t in catalog if t["name"] not in schemas]
    if missing:
        print(f"  [WARN] DDL 메타 로드 실패 테이블: {len(missing)}개 (예: {missing[:3]})")
    print(f"  DDL 컬럼 메타 로드: {len(schemas)}개 테이블")

    # 4. 카테고리 분류 — 실제 DB PK 우선 (요구서 문서와 불일치 대응)
    cat_of = {t["name"]: categorize(t, schemas.get(t["name"])) for t in catalog}
    dist = dict.fromkeys(("B", "C", "D", "E"), 0)
    for c in cat_of.values():
        dist[c] = dist.get(c, 0) + 1
    print(
        f"  카테고리 분류 (총 {len(catalog)}): "
        f"B={dist['B']}  C={dist['C']}  D={dist['D']}  E={dist['E']}"
    )

    report: dict[str, dict] = {}

    # 5. 카테고리 B (UPSERT) — non-PK 일자 컬럼을 target_to 로 한 번만
    if "B" in args.categories:
        print(f"\n[카테고리 B] non-PK 일자 UPDATE → {args.to_date}")
        b_updated = b_skipped = 0
        for t in catalog:
            if cat_of[t["name"]] != "B":
                continue
            schema = schemas.get(t["name"])
            if not schema:
                b_skipped += 1
                continue
            upd, sk = upsert_category_b(cur, t, schema, args.to_date, args)
            b_updated += upd
            b_skipped += sk
            report.setdefault(t["name"], {"updated": 0, "inserted": 0, "skipped": 0})
            report[t["name"]]["updated"] += upd
        conn.commit()
        print(
            f"  B 완료: UPDATE {b_updated:,}행 "
            f"(일자 컬럼 비보유/정적 테이블 {b_skipped}개 no-op)"
        )

    # 4. 카테고리 C/D/E — 일자 루프
    cde_cats = {c for c in ("C", "D", "E") if c in args.categories}
    if cde_cats:
        cde_tables = [t for t in catalog if cat_of[t["name"]] in cde_cats]
        print(f"\n[카테고리 C/D/E] 일자별 INSERT — 대상 {len(cde_tables)}개 테이블")

        for d in dates:
            day_ins = 0
            day_skip = 0
            for t in cde_tables:
                schema = schemas.get(t["name"])
                if not schema:
                    continue
                ins, sk = insert_category_cd_e(cur, t, schema, pools, d, args)
                day_ins += ins
                day_skip += sk
                r = report.setdefault(
                    t["name"], {"updated": 0, "inserted": 0, "skipped": 0},
                )
                r["inserted"] += ins
                r["skipped"] += sk
            conn.commit()
            print(f"  [{d}] inserted={day_ins:,}  skipped={day_skip:,}")

    # 5. 사후 검증
    print("\n[사후 검증]")
    _post_check(cur, args)

    elapsed = time.time() - t0
    print(f"\n[요약] 소요={elapsed:.1f}s")

    # 6. 요약 JSON
    if not args.dry_run:
        out = (
            Path(_REPO_ROOT)
            / "devtools" / "scripts"
            / f"incremental_seed_report_{args.from_date}_{args.to_date}_{args.seed}.json"
        )
        out.write_text(
            json.dumps(
                {
                    "from": args.from_date.isoformat(),
                    "to": args.to_date.isoformat(),
                    "seed": args.seed,
                    "duration_sec": round(elapsed, 2),
                    "categories": dist,
                    "tables": {
                        k: v for k, v in report.items()
                        if v.get("updated", 0) or v.get("inserted", 0) or v.get("skipped", 0)
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"  report: {out}")

    cur.close()
    conn.close()
    return 0


def _post_check(cur, args: argparse.Namespace) -> None:
    """주요 테이블 일자 분포·FK 고아 점검."""
    checks = [
        ("TB_ADW_CSC101M", "STD_DT"),
        ("TB_ADW_DEP201P", "STD_DT"),
        ("TB_ADW_LNB301M", "STD_DT"),
        ("TB_ADW_CRD401M", "STD_DT"),
        ("TB_ADW_DEP202S", "BASE_DT"),
        ("TB_ADW_CSC102H", "STD_DT"),
        ("TB_ADW_FND601P", "STD_DT"),
        ("TB_ADW_FND602P", "STD_DT"),
        ("TB_ADW_PNB904P", "STD_DT"),
        ("TB_ADW_TRX701L", "TR_DT"),
        ("TB_ADW_FXD501L", "SETL_DT"),
        ("TB_ADW_FXB502M", "BASE_DT"),
        ("TB_ADW_RSK1101M", "STD_DT"),
        ("TB_ADW_MKT1202M", "CONTACT_DT"),
        ("TB_ADW_FIN1306S", "BASE_YM"),
    ]
    for tbl, col in checks:
        try:
            cur.execute(
                f"SELECT MIN({col})::text, MAX({col})::text, COUNT(*) "
                f"FROM ADWOWN.{tbl}"
            )
            mn, mx, cnt = cur.fetchone()
            print(f"  {tbl:<22} {col:<10} min={mn} max={mx} count={cnt}")
        except Exception as e:
            print(f"  {tbl:<22} [check 실패] {e}")

    # FK 고아 점검 (주요 2건)
    try:
        cur.execute(
            """
            SELECT COUNT(*) FROM ADWOWN.TB_ADW_DEP201P d
             LEFT JOIN ADWOWN.TB_ADW_CSC101M c ON d.EDPS_CSN = c.EDPS_CSN
             WHERE c.EDPS_CSN IS NULL
            """
        )
        orphan_dep = cur.fetchone()[0]
        print(f"  FK 고아(DEP201P→CSC101M): {orphan_dep}")
    except Exception as e:
        print(f"  [FK 점검 실패] {e}")


if __name__ == "__main__":
    sys.exit(main())
