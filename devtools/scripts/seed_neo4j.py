"""Neo4j 온톨로지 그래프 시딩.

MongoDB의 메타데이터(테이블/컬럼/코드/용어사전)를 읽어서
Neo4j 온톨로지 그래프에 노드와 관계를 생성한다.

3단계 시딩:
  Phase 1: MongoDB → Neo4j 노드 생성 (Table, Column, CodeDefinition, DomainConcept)
  Phase 2: 관계 추론 (FK_TO, APPLIES_TO, IN_AREA)
  Phase 3: 업무 규칙 — 계수산출식 (COMPOSED_OF, MEASURED_BY)

사전 조건:
  - Neo4j 서버 가동 + 스키마 초기화 (init_neo4j.cypher 실행)
  - MongoDB에 메타데이터 적재 완료 (seed_mongodb.py 실행)

사용법:
    python devtools/scripts/seed_neo4j.py                  # 전체 시딩
    python devtools/scripts/seed_neo4j.py --phases 1,2     # Phase 1+2만
    python devtools/scripts/seed_neo4j.py --full-reset      # 기존 그래프 삭제 후 재시딩
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

_env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(_env_path, encoding="utf-8")

# ── 환경 변수 ──
MONGO_HOST = os.getenv("MONGO_HOST", "localhost")
MONGO_PORT = int(os.getenv("MONGO_PORT", "27017"))
MONGO_USER = os.getenv("MONGO_USER", "mongoadmin")
MONGO_PASSWORD = os.getenv("MONGO_PASSWORD", "mongo_pass")
MONGO_DATABASE = os.getenv("MONGO_DATABASE", "meta_db")

NEO4J_HOST = os.getenv("NEO4J_HOST", "localhost")
NEO4J_PORT = int(os.getenv("NEO4J_PORT", "7687"))
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "neo4j_pass")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

BATCH_SIZE = int(os.getenv("NEO4J_BATCH_SIZE", "500"))


# ══════════════════════════════════════════════════════════════
# MongoDB 읽기 (동기, pymongo)
# ══════════════════════════════════════════════════════════════

def _get_mongo_client_and_db():
    """동기 MongoDB 클라이언트와 DB를 반환한다."""
    from pymongo import MongoClient

    client = MongoClient(
        host=MONGO_HOST,
        port=MONGO_PORT,
        username=MONGO_USER,
        password=MONGO_PASSWORD,
        authSource="admin",
    )
    return client, client[MONGO_DATABASE]


# ══════════════════════════════════════════════════════════════
# Neo4j 쓰기 (동기, neo4j 드라이버)
# ══════════════════════════════════════════════════════════════

def _get_neo4j_driver():
    """동기 Neo4j 드라이버를 반환한다."""
    from neo4j import GraphDatabase

    uri = f"bolt://{NEO4J_HOST}:{NEO4J_PORT}"
    return GraphDatabase.driver(
        uri,
        auth=(NEO4J_USER, NEO4J_PASSWORD),
    )


def _run_cypher(driver, cypher: str, params: dict | None = None) -> list[dict]:
    """단일 Cypher 쿼리를 실행하고 결과를 반환한다."""
    with driver.session(database=NEO4J_DATABASE) as session:
        result = session.run(cypher, params or {})
        return [dict(record) for record in result]


def _run_cypher_batch(
    driver, cypher: str, batch: list[dict], label: str = "",
) -> int:
    """배치 단위로 Cypher를 실행한다 (UNWIND $batch 패턴)."""
    total = 0
    for i in range(0, len(batch), BATCH_SIZE):
        chunk = batch[i : i + BATCH_SIZE]
        _run_cypher(driver, cypher, {"batch": chunk})
        total += len(chunk)
        if label:
            print(f"  [{label}] {total}/{len(batch)}")
    return total


# ══════════════════════════════════════════════════════════════
# Phase 1: MongoDB → Neo4j 노드 생성
# ══════════════════════════════════════════════════════════════

def seed_phase1(driver, mongo_db) -> None:
    """MongoDB 메타데이터에서 Neo4j 노드를 생성한다."""
    print("\n=== Phase 1: MongoDB → Neo4j 노드 생성 ===")

    # 1a. Table 노드
    tables = list(mongo_db["dpasset_table"].find({}, {"_id": 0}))
    if tables:
        batch = [
            {
                "name": t.get("name", ""),
                "alt_name": t.get("alt_name", ""),
                "schema_name": t.get("schema_name", ""),
                "desc": t.get("desc", ""),
                "update_cycle": t.get("update_cycle", ""),
                "db_source": _infer_db_source(t.get("name", "")),
            }
            for t in tables if t.get("name")
        ]
        cypher = """
        UNWIND $batch AS row
        MERGE (t:Table {name: row.name})
        SET t.alt_name = row.alt_name,
            t.schema_name = row.schema_name,
            t.description = row.desc,
            t.update_cycle = coalesce(row.update_cycle, ''),
            t.db_source = coalesce(row.db_source, '')
        """
        _run_cypher_batch(driver, cypher, batch, "Table")
    print(f"  Table 노드: {len(tables)}건")

    # 1b. Column 노드 + BELONGS_TO
    columns = list(mongo_db["dpasset_column"].find({}, {"_id": 0}))
    if columns:
        batch = [
            {
                "name": c.get("name", ""),
                "alt_name": c.get("alt_name", ""),
                "data_type": c.get("data_type", ""),
                "desc": c.get("desc", ""),
                "pk": c.get("pk", False),
                "table_name": c.get("table_name", ""),
            }
            for c in columns if c.get("name") and c.get("table_name")
        ]
        cypher = """
        UNWIND $batch AS row
        MERGE (c:Column {table_name: row.table_name, name: row.name})
        SET c.alt_name = row.alt_name,
            c.data_type = coalesce(row.data_type, ''),
            c.description = coalesce(row.desc, ''),
            c.is_pk = coalesce(row.pk, false)
        WITH c, row
        MATCH (t:Table {name: row.table_name})
        MERGE (c)-[:BELONGS_TO]->(t)
        """
        _run_cypher_batch(driver, cypher, batch, "Column")
    print(f"  Column 노드: {len(columns)}건")

    # 1c. CodeDefinition 노드
    codes = list(mongo_db["standard_code"].find({}, {"_id": 0}))
    code_values = list(mongo_db["standard_code_value"].find({}, {"_id": 0}))

    # code_id → code_name 매핑 (ObjectId 기준)
    code_id_map = {}
    for c in mongo_db["standard_code"].find({}):
        code_id_map[c["_id"]] = c.get("name", "")

    if code_values:
        batch = [
            {
                "code_field": code_id_map.get(cv.get("code_id"), ""),
                "code_value": cv.get("code_value", ""),
                "code_name": cv.get("code_name", ""),
            }
            for cv in code_values
            if code_id_map.get(cv.get("code_id"))
        ]
        cypher = """
        UNWIND $batch AS row
        MERGE (cd:CodeDefinition {code_field: row.code_field, code_value: row.code_value})
        SET cd.code_name = row.code_name
        """
        _run_cypher_batch(driver, cypher, batch, "CodeDefinition")
    print(f"  CodeDefinition 노드: {len(code_values)}건")

    # 1d. DomainConcept (biz_term)
    biz_term = list(mongo_db["biz_term"].find({}))

    # biz_term.table_ids → 테이블명 역매핑
    table_id_map = {}
    for t in mongo_db["dpasset_table"].find({}):
        table_id_map[t["_id"]] = t.get("name", "")

    if biz_term:
        batch = []
        for g in biz_term:
            table_ids = g.get("table_ids", [])
            # 첫 번째 연결 테이블만 PRIMARY로
            first_table = table_id_map.get(table_ids[0]) if table_ids else ""
            batch.append({
                "name": g.get("name", ""),
                "definition": g.get("biz_term_definition", ""),
                "synonyms": g.get("synonyms", []),
                "table_name": first_table,
            })
        cypher = """
        UNWIND $batch AS row
        MERGE (d:DomainConcept {name: row.name})
        SET d.definition = coalesce(row.definition, ''),
            d.synonyms = coalesce(row.synonyms, []),
            d.category = 'biz_term',
            d.source = 'mongodb_seed'
        WITH d, row
        WHERE row.table_name IS NOT NULL AND row.table_name <> ''
        MATCH (t:Table {name: row.table_name})
        MERGE (d)-[:RESOLVED_BY {role: 'PRIMARY'}]->(t)
        """
        _run_cypher_batch(driver, cypher, batch, "DomainConcept")
    print(f"  DomainConcept 노드: {len(biz_term)}건")


# ══════════════════════════════════════════════════════════════
# Phase 2: 관계 추론
# ══════════════════════════════════════════════════════════════

def seed_phase2(driver, mongo_db) -> None:
    """테이블 간 FK, 코드 바인딩, 주제영역을 추론하여 관계를 생성한다."""
    print("\n=== Phase 2: 관계 추론 ===")

    # 2a. FK 추론 — 동일 컬럼명 + PK 기반
    tables = list(mongo_db["dpasset_table"].find({}, {"_id": 0, "name": 1}))
    table_names = {t["name"] for t in tables if t.get("name")}

    # PK 컬럼 수집: {column_name: [table_name, ...]}
    pk_columns: dict[str, list[str]] = defaultdict(list)
    all_columns: dict[str, list[dict]] = defaultdict(list)

    for col in mongo_db["dpasset_column"].find({}, {"_id": 0}):
        tbl = col.get("table_name", "")
        name = col.get("name", "")
        if not tbl or not name:
            continue
        all_columns[tbl].append(col)
        if col.get("pk"):
            pk_columns[name].append(tbl)

    fk_batch = []
    seen_fk = set()

    for tbl, cols in all_columns.items():
        for col in cols:
            col_name = col.get("name", "")
            if col.get("pk"):
                continue  # PK 자체는 건너뜀

            # 이 컬럼명이 다른 테이블의 PK라면 → FK 추론
            if col_name in pk_columns:
                for pk_table in pk_columns[col_name]:
                    if pk_table == tbl:
                        continue
                    fk_key = f"{tbl}→{pk_table}:{col_name}"
                    if fk_key in seen_fk:
                        continue
                    seen_fk.add(fk_key)
                    fk_batch.append({
                        "source": tbl,
                        "target": pk_table,
                        "from_column": col_name,
                        "to_column": col_name,
                        "confidence": "CONFIRMED" if col.get("pk") is False else "INFERRED",
                        "evidence": f"컬럼명 '{col_name}'이 {pk_table}의 PK와 일치",
                    })

    if fk_batch:
        cypher = """
        UNWIND $batch AS row
        MATCH (a:Table {name: row.source}), (b:Table {name: row.target})
        MERGE (a)-[r:FK_TO]->(b)
        SET r.from_column = row.from_column,
            r.to_column = row.to_column,
            r.join_type = 'INNER',
            r.confidence = row.confidence,
            r.evidence = row.evidence
        """
        _run_cypher_batch(driver, cypher, fk_batch, "FK_TO")
    print(f"  FK_TO 관계: {len(fk_batch)}건")

    # 2b. 코드 컬럼 바인딩 — 컬럼명 = 코드 물리명
    code_names = {
        c.get("name", "")
        for c in mongo_db["standard_code"].find({}, {"_id": 0, "name": 1})
        if c.get("name")
    }

    code_bind_batch = []
    for tbl, cols in all_columns.items():
        for col in cols:
            col_name = col.get("name", "")
            if col_name in code_names:
                code_bind_batch.append({
                    "code_field": col_name,
                    "table_name": tbl,
                    "column_name": col_name,
                })

    if code_bind_batch:
        cypher = """
        UNWIND $batch AS row
        MATCH (cd:CodeDefinition {code_field: row.code_field})
        MATCH (col:Column {table_name: row.table_name, name: row.column_name})
        MERGE (cd)-[:APPLIES_TO]->(col)
        """
        _run_cypher_batch(driver, cypher, code_bind_batch, "APPLIES_TO")
    print(f"  APPLIES_TO 관계: {len(code_bind_batch)}건")

    # 2c. 주제영역 군집화 — 테이블명 접두사 패턴
    area_map = _infer_subject_areas(table_names)

    area_batch = [
        {
            "area_name": area_name,
            "area_desc": f"{area_name} 주제영역",
            "table_names": list(tbl_set),
        }
        for area_name, tbl_set in area_map.items()
    ]

    if area_batch:
        cypher = """
        UNWIND $batch AS row
        MERGE (s:SubjectArea {name: row.area_name})
        SET s.description = row.area_desc
        WITH s, row
        UNWIND row.table_names AS tbl
        MATCH (t:Table {name: tbl})
        MERGE (t)-[:IN_AREA]->(s)
        """
        _run_cypher_batch(driver, cypher, area_batch, "SubjectArea")
    print(f"  SubjectArea: {len(area_map)}개 영역, 테이블 {sum(len(v) for v in area_map.values())}건 매핑")


# ══════════════════════════════════════════════════════════════
# Phase 3: 계수산출식 (수동 정의)
# ══════════════════════════════════════════════════════════════

# 은행 도메인 핵심 계수산출식 시드 데이터
FORMULA_SEEDS: list[dict] = [
    {
        "name": "연체율",
        "definition": "연체원금 합계 / 여신잔액 합계 × 100",
        "category": "금융지표",
        "components": [
            {"name": "연체원금", "position": "NUMERATOR", "operator": "DIVIDE", "synonyms": ["연체잔액", "납기경과대출액"]},
            {"name": "여신잔액", "position": "DENOMINATOR", "operator": "DIVIDE", "synonyms": ["대출잔액", "대출총액"]},
        ],
    },
    {
        "name": "NIM",
        "definition": "(이자수익 - 이자비용) / 이자수익자산 평잔 × 100",
        "category": "금융지표",
        "components": [
            {"name": "순이자마진", "position": "NUMERATOR", "operator": "DIVIDE", "synonyms": ["이자수익 - 이자비용"]},
            {"name": "이자수익자산평잔", "position": "DENOMINATOR", "operator": "DIVIDE", "synonyms": []},
        ],
    },
    {
        "name": "예대율",
        "definition": "여신잔액 합계 / 수신잔액 합계 × 100",
        "category": "금융지표",
        "components": [
            {"name": "여신잔액", "position": "NUMERATOR", "operator": "DIVIDE", "synonyms": ["대출잔액"]},
            {"name": "수신잔액", "position": "DENOMINATOR", "operator": "DIVIDE", "synonyms": ["예금잔액"]},
        ],
    },
]

# 계수산출식 구성요소 → 컬럼/테이블 매핑 시드
MEASURED_BY_SEEDS: list[dict] = [
    {"concept_name": "연체원금", "table_name": "TB_LN_BAL_D", "column_name": "OVRD_PRINC_AMT", "agg_function": "SUM"},
    {"concept_name": "여신잔액", "table_name": "TB_LN_BAL_D", "column_name": "LOAN_BAL_AMT", "agg_function": "SUM"},
    {"concept_name": "수신잔액", "table_name": "TB_DP_BAL_D", "column_name": "DEP_BAL_AMT", "agg_function": "SUM"},
]


def seed_phase3(driver) -> None:
    """계수산출식 + 컬럼 매핑을 생성한다."""
    print("\n=== Phase 3: 계수산출식 ===")

    # 3a. COMPOSED_OF 관계
    for formula in FORMULA_SEEDS:
        cypher = """
        MERGE (root:DomainConcept {name: $formula.name})
        SET root.definition = $formula.definition,
            root.category = coalesce($formula.category, '금융지표'),
            root.source = 'seed'
        WITH root
        UNWIND $formula.components AS comp
        MERGE (sub:DomainConcept {name: comp.name})
        SET sub.synonyms = coalesce(comp.synonyms, [])
        MERGE (root)-[r:COMPOSED_OF]->(sub)
        SET r.position = comp.position,
            r.operator = comp.operator
        """
        _run_cypher(driver, cypher, {"formula": formula})
    print(f"  계수산출식: {len(FORMULA_SEEDS)}건")

    # 3b. MEASURED_BY 관계 (테이블/컬럼이 존재하는 경우만)
    applied = 0
    for m in MEASURED_BY_SEEDS:
        cypher = """
        MATCH (d:DomainConcept {name: $concept_name})
        MATCH (col:Column {table_name: $table_name, name: $column_name})
        MERGE (d)-[r:MEASURED_BY]->(col)
        SET r.agg_function = $agg_function
        """
        result = _run_cypher(driver, cypher, m)
        applied += 1
    print(f"  MEASURED_BY 매핑: {applied}건 시도 (테이블/컬럼 존재 시에만 반영)")


# ══════════════════════════════════════════════════════════════
# 유틸리티
# ══════════════════════════════════════════════════════════════

# 테이블명 시스템코드 → DB 소스 매핑
_DB_SOURCE_PREFIXES = {
    "ADW": "adw", "DW": "adw",
    "BDP": "bigdata", "BIG": "bigdata",
    "INF": "info", "IF": "info",
}


def _infer_db_source(table_name: str) -> str:
    """테이블명에서 DB 소스를 추론한다."""
    parts = table_name.split("_")
    if len(parts) >= 2:
        prefix = parts[1].upper()
        return _DB_SOURCE_PREFIXES.get(prefix, "")
    return ""


# 테이블명 접두사 → 주제영역 매핑
_AREA_PATTERNS: list[tuple[str, str]] = [
    (r"TB_(?:LN|LOAN)", "여신"),
    (r"TB_(?:DP|DEP)", "수신"),
    (r"TB_(?:CUST|CUS)", "고객"),
    (r"TB_(?:BR|BRANCH|ORG)", "조직"),
    (r"TB_(?:TXN|TX|TRAN)", "거래"),
    (r"TB_(?:CD|CARD)", "카드"),
    (r"TB_(?:FX|FOREX)", "외환"),
    (r"TB_(?:RISK|CAP)", "리스크"),
]


def _infer_subject_areas(table_names: set[str]) -> dict[str, set[str]]:
    """테이블명 패턴에서 주제영역을 추론한다."""
    area_map: dict[str, set[str]] = defaultdict(set)
    unclassified = set()

    for name in table_names:
        matched = False
        for pattern, area in _AREA_PATTERNS:
            if re.match(pattern, name, re.IGNORECASE):
                # 접미사로 세부 영역 구분: _D(일별), _M(월별), _HIST(이력)
                suffix = ""
                if name.endswith("_D"):
                    suffix = "_일별"
                elif name.endswith("_M"):
                    suffix = "_월별"
                elif "_HIST" in name:
                    suffix = "_이력"
                elif "_BAL" in name:
                    suffix = "_잔액"
                elif "_EXEC" in name:
                    suffix = "_실행"

                full_area = f"{area}{suffix}" if suffix else area
                area_map[full_area].add(name)
                matched = True
                break

        if not matched:
            unclassified.add(name)

    if unclassified:
        area_map["미분류"] = unclassified

    return area_map


def full_reset(driver) -> None:
    """기존 그래프 전체 삭제 (배치 단위로 OOM 방지)."""
    print("\n!!! 기존 그래프 전체 삭제 !!!")
    total = 0
    while True:
        result = _run_cypher(driver, """
            MATCH (n)
            WITH n LIMIT 10000
            DETACH DELETE n
            RETURN count(*) AS deleted
        """)
        deleted = result[0]["deleted"] if result else 0
        if deleted == 0:
            break
        total += deleted
        print(f"  삭제 중... {total}건")
    print("  삭제 완료")


# ══════════════════════════════════════════════════════════════
# 메인
# ══════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(description="Neo4j 온톨로지 시딩")
    parser.add_argument(
        "--phases", default="1,2,3",
        help="실행할 Phase (기본: 1,2,3)",
    )
    parser.add_argument(
        "--full-reset", action="store_true",
        help="기존 그래프 삭제 후 재시딩",
    )
    args = parser.parse_args()

    phases = {int(p.strip()) for p in args.phases.split(",")}

    print("=" * 60)
    print("Neo4j 온톨로지 시딩 시작")
    print(f"  Neo4j: bolt://{NEO4J_HOST}:{NEO4J_PORT}")
    print(f"  MongoDB: {MONGO_HOST}:{MONGO_PORT}/{MONGO_DATABASE}")
    print(f"  Phases: {sorted(phases)}")
    print(f"  Batch Size: {BATCH_SIZE}")
    print("=" * 60)

    driver = _get_neo4j_driver()
    mongo_client, mongo_db = _get_mongo_client_and_db()

    start = time.time()

    try:
        if args.full_reset:
            full_reset(driver)

        if 1 in phases:
            seed_phase1(driver, mongo_db)

        if 2 in phases:
            seed_phase2(driver, mongo_db)

        if 3 in phases:
            seed_phase3(driver)
    finally:
        mongo_client.close()

    elapsed = time.time() - start

    # 최종 통계
    print("\n" + "=" * 60)
    print("시딩 완료!")

    stats = _run_cypher(driver, """
    MATCH (n)
    RETURN labels(n)[0] AS label, count(n) AS count
    ORDER BY count DESC
    """)
    for s in stats:
        print(f"  {s['label']}: {s['count']}건")

    rel_stats = _run_cypher(driver, """
    MATCH ()-[r]->()
    RETURN type(r) AS rel_type, count(r) AS count
    ORDER BY count DESC
    """)
    for s in rel_stats:
        print(f"  [{s['rel_type']}]: {s['count']}건")

    print(f"\n  소요 시간: {elapsed:.1f}초")
    print("=" * 60)

    driver.close()


if __name__ == "__main__":
    main()
