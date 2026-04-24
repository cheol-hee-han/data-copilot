#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# neo4j/init.sh — Neo4j 온톨로지 그래프 스키마 초기화
#
# 대상: Neo4j 5.26 LTS (driver neo4j>=5.20.0)
# 노드 6종 / 엣지 9종: init_neo4j.cypher 참조
#
# 실행 순서 (파일 알파벳 순이 아닌 의존성 순):
#   1) init_neo4j.cypher           — 제약조건·인덱스 (idempotent)
#   2) cypher_domain_tables.cypher — SubjectArea ← Table 매핑
#   3) cypher_table_relations.cypher — Table-Column, FK_TO
#   4) cypher_code_hierarchy.cypher  — CodeDefinition 계층
#   5) cypher_join_paths.cypher      — JOIN 경로 메타
#   6) cypher_formula.cypher         — 계수산출식 정의
#
# seed_queries.cypher 는 실 데이터 로드용이므로 이 스크립트에서 실행하지 않음.
# 실 데이터 투입은 이관자 몫이며 devtools/scripts/seed_neo4j.py 를 참조.
#
# 필요 환경변수:
#   NEO4J_HOST, NEO4J_PORT, NEO4J_USER, NEO4J_PASSWORD, NEO4J_DATABASE
# ──────────────────────────────────────────────────────────────
set -euo pipefail

: "${NEO4J_HOST:?NEO4J_HOST 미설정}"
: "${NEO4J_USER:?NEO4J_USER 미설정}"
: "${NEO4J_PASSWORD:?NEO4J_PASSWORD 미설정}"
NEO4J_PORT="${NEO4J_PORT:-7687}"
NEO4J_DATABASE="${NEO4J_DATABASE:-neo4j}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v cypher-shell >/dev/null 2>&1; then
    echo "!! cypher-shell CLI 가 설치돼 있지 않습니다." >&2
    echo "   Neo4j 서버 호스트 또는 neo4j-client RPM 에서 제공됩니다." >&2
    echo "   대안: docker exec <neo4j-container> cypher-shell ... 로 수동 실행" >&2
    exit 1
fi

NEO4J_URI="bolt://${NEO4J_HOST}:${NEO4J_PORT}"

echo "==> Neo4j 초기화 시작"
echo "   URI            = $NEO4J_URI"
echo "   DATABASE       = $NEO4J_DATABASE"

# 의존성 순서 고정
CYPHER_FILES=(
    "init_neo4j.cypher"
    "cypher_domain_tables.cypher"
    "cypher_table_relations.cypher"
    "cypher_code_hierarchy.cypher"
    "cypher_join_paths.cypher"
    "cypher_formula.cypher"
)

for f in "${CYPHER_FILES[@]}"; do
    path="$SCRIPT_DIR/$f"
    if [[ ! -f "$path" ]]; then
        echo "!! Cypher 파일 누락: $path" >&2
        exit 1
    fi
    echo "  → 실행: $f"
    cypher-shell \
        -a "$NEO4J_URI" \
        -u "$NEO4J_USER" \
        -p "$NEO4J_PASSWORD" \
        -d "$NEO4J_DATABASE" \
        --fail-fast \
        --format plain \
        < "$path"
done

echo "✓ Neo4j 초기화 완료 (db=$NEO4J_DATABASE)"
