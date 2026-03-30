"""MongoDB 메타데이터 시딩.

PG biz_schema의 실제 테이블/컬럼 구조를 읽어서
MongoDB meta_db의 5개 컬렉션에 적재한다.

대상 컬렉션:
  1. dpasset_table       — 테이블 메타 (PG 스키마 기반)
  2. dpasset_column      — 컬럼 메타 (PG 스키마 기반)
  3. standard_code       — 코드 메타 (ES seed_elasticsearch.py의 CODE_META_DOCS와 동일)
  4. standard_code_value — 코드값 (CODE_META_DOCS의 codes 펼침)
  5. glossary            — 업무 용어사전 (ES seed_elasticsearch.py의 TERM_DICT_DOCS와 동일)

TYPE-2: code_meta에 공식 코드만 등록 (PG 미정의 코드 의도적 누락)
TYPE-3: table/column 설명 품질 혼재 (BEST 15% / GOOD 25% / POOR 40% / MISSING 20%)

사전 조건:
  - MongoDB 컬렉션 스키마가 생성되어 있어야 함 (init_mongodb.js 실행)
  - PG biz_schema에 테이블이 존재해야 함 (seed_postgres.py 실행)

사용법:
    pip install pymongo psycopg2-binary python-dotenv
    python standalone/scripts/seed_mongodb.py
"""

from __future__ import annotations

import hashlib
import os
import random
import re
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

_env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(_env_path, encoding="utf-8")

MONGO_HOST = os.getenv("MONGO_HOST", "localhost")
MONGO_PORT = int(os.getenv("MONGO_PORT", "27017"))
MONGO_USER = os.getenv("MONGO_USER", "mongoadmin")
MONGO_PASSWORD = os.getenv("MONGO_PASSWORD", "mongo_pass")
MONGO_DATABASE = os.getenv("MONGO_DATABASE", "meta_db")

SCHEMA = "ADWOWN"


# ══════════════════════════════════════════════════════════════
# seed_elasticsearch.py 공유 데이터/함수 import
# ══════════════════════════════════════════════════════════════

# 같은 디렉토리의 seed_elasticsearch.py에서 공통 데이터 재사용
sys.path.insert(0, str(Path(__file__).resolve().parent))
from seed_elasticsearch import (  # noqa: E402
    CODE_META_DOCS,
    TERM_DICT_DOCS,
    _col_desc_quality,
    _col_ko_name,
    _get_pg_schema,
    _parse_requirements,
    _pg_type_to_es,
    _table_desc_quality,
    PII_COLS,
)


# ══════════════════════════════════════════════════════════════
# MongoDB 시딩
# ══════════════════════════════════════════════════════════════

def seed_mongodb() -> None:
    """MongoDB meta_db에 메타데이터를 시딩한다."""
    from pymongo import MongoClient

    random.seed(42)  # 재실행 시 동일 결과

    uri = (
        f"mongodb://{MONGO_USER}:{MONGO_PASSWORD}"
        f"@{MONGO_HOST}:{MONGO_PORT}"
        f"/{MONGO_DATABASE}?authSource=admin"
    )
    client = MongoClient(uri, serverSelectionTimeoutMS=10000)
    db = client[MONGO_DATABASE]

    # 연결 확인
    try:
        client.admin.command("ping")
        print("  MongoDB 연결 확인")
    except Exception as e:
        print(f"ERROR: MongoDB 연결 실패: {e}")
        sys.exit(1)

    # ── 요구사항 파싱 ──
    req_tables = _parse_requirements()
    print(f"  요구사항 테이블 수: {len(req_tables)}")

    # ── PG 스키마 추출 ──
    pg_schema = _get_pg_schema()
    total_cols = sum(len(v) for v in pg_schema.values())
    print(f"  PG 테이블 수: {len(pg_schema)}, 총 컬럼 수: {total_cols}")

    # ========================================
    # 1. dpasset_table — 테이블 메타
    # ========================================
    print("\n[dpasset_table]")
    coll_table = db["dpasset_table"]
    coll_table.delete_many({})

    table_docs = []
    for tbl_upper, cols in pg_schema.items():
        # 파티션 자식 테이블 건너뛰기
        if re.match(r"TB_ADW_TRX701L_\d{6}", tbl_upper):
            continue

        req = req_tables.get(tbl_upper, {})
        ko_name = req.get("ko_name", tbl_upper)
        domain = req.get("domain", "COM")
        desc = _table_desc_quality(tbl_upper, ko_name, domain)

        table_docs.append({
            "schema_name": SCHEMA,
            "name": tbl_upper,
            "alt_name": ko_name,
            "desc": desc or "",
        })

    if table_docs:
        result = coll_table.insert_many(table_docs, ordered=False)
        print(f"  dpasset_table: {len(result.inserted_ids)}건 적재")
    else:
        print("  dpasset_table: 0건 (PG 스키마 비어있음)")

    # ========================================
    # 2. dpasset_column — 컬럼 메타
    # ========================================
    print("\n[dpasset_column]")
    coll_column = db["dpasset_column"]
    coll_column.delete_many({})

    col_docs = []
    for tbl_upper, cols in pg_schema.items():
        if re.match(r"TB_ADW_TRX701L_\d{6}", tbl_upper):
            continue

        for c in cols:
            col_desc = _col_desc_quality(tbl_upper, c["name"])
            ko = _col_ko_name(c["name"])
            es_type = _pg_type_to_es(c["data_type"], c["max_length"])

            col_docs.append({
                "table_name": tbl_upper,
                "name": c["name"],
                "alt_name": ko,
                "data_type": es_type,
                "desc": col_desc or "",
                "pk": c["is_pk"],
            })

    if col_docs:
        result = coll_column.insert_many(col_docs, ordered=False)
        print(f"  dpasset_column: {len(result.inserted_ids)}건 적재")
    else:
        print("  dpasset_column: 0건")

    # ========================================
    # 3. standard_code + standard_code_value — 코드 메타
    # ========================================
    print("\n[standard_code + standard_code_value]")
    coll_code = db["standard_code"]
    coll_code_val = db["standard_code_value"]
    coll_code.delete_many({})
    coll_code_val.delete_many({})

    code_count = 0
    code_value_count = 0
    for doc in CODE_META_DOCS:
        # standard_code 삽입
        code_doc = {
            "name": doc["code_field"],
            "alt_name": doc.get("code_field_desc", ""),
        }
        code_result = coll_code.insert_one(code_doc)
        code_id = code_result.inserted_id
        code_count += 1

        # standard_code_value 삽입
        code_values = []
        for code_val, code_name in doc.get("codes", {}).items():
            code_values.append({
                "code_id": code_id,
                "code_value": str(code_val),
                "code_name": code_name,
            })
        if code_values:
            val_result = coll_code_val.insert_many(
                code_values, ordered=False,
            )
            code_value_count += len(val_result.inserted_ids)

    print(f"  standard_code: {code_count}건 적재")
    print(f"  standard_code_value: {code_value_count}건 적재")

    # ========================================
    # 4. glossary — 업무 용어사전
    # ========================================
    print("\n[glossary]")
    coll_glossary = db["glossary"]
    coll_glossary.delete_many({})

    # table_hint → dpasset_table._id 매핑 구성
    table_id_map = {}
    for t in coll_table.find({}, {"_id": 1, "name": 1}):
        table_id_map[t["name"]] = t["_id"]

    glossary_docs = []
    for doc in TERM_DICT_DOCS:
        # table_hint 문자열에서 테이블명 추출 → ObjectId 매핑
        table_ids = []
        hint = doc.get("table_hint", "")
        for tbl_name in [t.strip() for t in hint.split(",")]:
            tbl_upper = tbl_name.upper()
            if tbl_upper in table_id_map:
                table_ids.append(table_id_map[tbl_upper])

        # synonyms: 쉼표 분리
        synonyms = [
            s.strip()
            for s in doc.get("synonym", "").split(",")
            if s.strip()
        ]

        glossary_docs.append({
            "name": doc["term_ko"],
            "synonyms": synonyms,
            "glossary_definition": doc.get("definition", ""),
            "table_ids": table_ids,
        })

    if glossary_docs:
        result = coll_glossary.insert_many(glossary_docs, ordered=False)
        print(f"  glossary: {len(result.inserted_ids)}건 적재")
    else:
        print("  glossary: 0건")

    # ── 최종 건수 확인 ──
    print("\n[적재 결과]")
    for coll_name in (
        "dpasset_table", "dpasset_column",
        "standard_code", "standard_code_value", "glossary",
    ):
        cnt = db[coll_name].count_documents({})
        print(f"  {coll_name:<25}: {cnt:>5}건")

    client.close()


if __name__ == "__main__":
    print("=" * 60)
    print("MongoDB 메타데이터 시딩")
    print("=" * 60)
    seed_mongodb()
    print("\n" + "=" * 60)
    print("MongoDB 시딩 완료!")
    print("=" * 60)
