#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# mongo/init.sh — MongoDB 컬렉션 및 인덱스 생성
#
# 대상 DB: meta_db (MONGO_DATABASE 로 덮어쓰기 가능)
# 생성 컬렉션:
#   - dpasset_table         (테이블 메타)
#   - dpasset_column        (컬럼 메타)
#   - standard_code         (코드 메타)
#   - standard_code_value   (코드값)
#   - biz_term              (업무 용어사전)
#
# 필요 환경변수:
#   MONGO_HOST, MONGO_PORT, MONGO_USER, MONGO_PASSWORD, MONGO_DATABASE
# ──────────────────────────────────────────────────────────────
set -euo pipefail

: "${MONGO_HOST:?MONGO_HOST 미설정}"
: "${MONGO_USER:?MONGO_USER 미설정}"
: "${MONGO_PASSWORD:?MONGO_PASSWORD 미설정}"
MONGO_PORT="${MONGO_PORT:-27017}"
MONGO_DATABASE="${MONGO_DATABASE:-meta_db}"

MONGO_URI="mongodb://${MONGO_USER}:${MONGO_PASSWORD}@${MONGO_HOST}:${MONGO_PORT}/?authSource=admin"

echo "==> MongoDB 초기화 시작"
echo "   HOST:PORT      = $MONGO_HOST:$MONGO_PORT"
echo "   DATABASE       = $MONGO_DATABASE"

# mongosh 우선, 없으면 mongo 사용
if command -v mongosh >/dev/null 2>&1; then
    MONGO_CLI="mongosh"
elif command -v mongo >/dev/null 2>&1; then
    MONGO_CLI="mongo"
else
    echo "!! mongosh/mongo CLI 가 설치돼 있지 않습니다." >&2
    exit 1
fi

"$MONGO_CLI" "$MONGO_URI" --quiet <<JS
// ──────────────────────────────────────────────────────────
// meta_db 컬렉션/인덱스 생성 (idempotent)
// ──────────────────────────────────────────────────────────
const dbName = "${MONGO_DATABASE}";
const target = db.getSiblingDB(dbName);

const collections = [
  "dpasset_table",
  "dpasset_column",
  "standard_code",
  "standard_code_value",
  "biz_term",
];

for (const name of collections) {
  if (!target.getCollectionNames().includes(name)) {
    target.createCollection(name);
    print("  + created: " + name);
  } else {
    print("  = exists : " + name);
  }
}

// 인덱스 — 검색·조인 패턴에 맞춰 최소 집합만 생성
target.dpasset_table.createIndex({ schema: 1, table_name: 1 }, { unique: true });
target.dpasset_table.createIndex({ subject_area: 1 });

target.dpasset_column.createIndex({ schema: 1, table_name: 1, column_name: 1 }, { unique: true });
target.dpasset_column.createIndex({ schema: 1, table_name: 1 });

target.standard_code.createIndex({ code_id: 1 }, { unique: true });
target.standard_code_value.createIndex({ code_id: 1, code_value: 1 }, { unique: true });

target.biz_term.createIndex({ term: 1 }, { unique: true });

print("✓ MongoDB 초기화 완료 (db=" + dbName + ")");
JS
