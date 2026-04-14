#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# postgres/init.sh — PostgreSQL 스키마/체크포인트/이력 테이블 생성
#
# resources/connectors/postgres/checkpoint/*.sql 을 파일명 순서대로
# psql 로 적용한다. 원본 SQL은 수정하지 않는다.
#
# 필요 환경변수:
#   PGHOST, PGPORT(옵션), PGUSER, PGPASSWORD(옵션), PGDATABASE
#
# 실행 위치: 프로젝트 루트(/opt/data-copilot) 에서 실행 권장
# ──────────────────────────────────────────────────────────────
set -euo pipefail

: "${PGHOST:?PGHOST 미설정}"
: "${PGUSER:?PGUSER 미설정}"
: "${PGDATABASE:?PGDATABASE 미설정}"
PGPORT="${PGPORT:-5432}"

# 프로젝트 루트 추정 (이 스크립트 기준)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/../../.." && pwd)}"
SQL_DIR="$PROJECT_ROOT/resources/connectors/postgres/checkpoint"

if [[ ! -d "$SQL_DIR" ]]; then
    echo "!! SQL 디렉토리를 찾을 수 없습니다: $SQL_DIR" >&2
    exit 1
fi

echo "==> PostgreSQL 초기화 시작"
echo "   HOST:PORT      = $PGHOST:$PGPORT"
echo "   DATABASE       = $PGDATABASE"
echo "   USER           = $PGUSER"
echo "   SQL_DIR        = $SQL_DIR"

# rename/rollback 스크립트는 선택 적용이므로 제외, 01~04 만 자동 적용한다.
APPLY_PATTERNS=(
    "01_schema_and_permissions.sql"
    "02_checkpointer_tables.sql"
    "03_dc_custom_tables.sql"
    "04_partman_setup.sql"
    "05_rename_turn_to_message.sql"
)

for fname in "${APPLY_PATTERNS[@]}"; do
    fpath="$SQL_DIR/$fname"
    if [[ ! -f "$fpath" ]]; then
        echo "   (건너뜀 — 파일 없음: $fname)"
        continue
    fi
    echo "   > 적용: $fname"
    PGPASSWORD="${PGPASSWORD:-}" psql \
        -v ON_ERROR_STOP=1 \
        -h "$PGHOST" -p "$PGPORT" \
        -U "$PGUSER" -d "$PGDATABASE" \
        -f "$fpath"
done

echo "✓ PostgreSQL 초기화 완료"
echo "  (rollback 스크립트는 수동 적용 대상이므로 자동 실행하지 않음)"
