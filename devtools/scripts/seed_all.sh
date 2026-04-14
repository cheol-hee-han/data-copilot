#!/usr/bin/env bash
# ============================================================
# 로컬 개발 환경 구축: Docker 기동 → 데이터 시딩
# 사용법: bash devtools/scripts/seed_all.sh
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

echo "=============================================="
echo " Data Copilot - 로컬 인프라 구축"
echo "=============================================="
echo " 순서: Docker → PG → MongoDB → Qdrant"

# ── 1. Docker Compose 기동 ──
echo ""
echo "[1/5] Docker 컨테이너 기동..."
docker compose -f devtools/docker/docker-compose.dev.yml up -d

# ── 2. PostgreSQL 준비 대기 ──
echo ""
echo "[2/5] PostgreSQL 준비 대기..."
for i in $(seq 1 30); do
    if docker exec dc-postgres pg_isready -U postgres > /dev/null 2>&1; then
        echo "  PostgreSQL 준비 완료!"
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "  ERROR: PostgreSQL 시작 타임아웃 (30초)"
        exit 1
    fi
    sleep 1
done

# init_postgres.sql 실행 대기 (DB 생성 완료 확인)
for i in $(seq 1 15); do
    if docker exec dc-postgres psql -U postgres -d test_db -c "SELECT 1" > /dev/null 2>&1; then
        echo "  test_db 초기화 확인!"
        break
    fi
    sleep 1
done

# ── 3. PostgreSQL 시딩 ──
echo ""
echo "[3/5] PostgreSQL 가짜 데이터 시딩..."
python devtools/scripts/seed_postgres.py

# ── 4. MongoDB 시딩 ──
echo ""
echo "[4/5] MongoDB 메타 데이터 시딩..."

# MongoDB 준비 대기
for i in $(seq 1 30); do
    if docker exec dc-mongodb mongosh --quiet --eval "db.runCommand({ping:1})" > /dev/null 2>&1; then
        echo "  MongoDB 준비 완료!"
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "  ERROR: MongoDB 시작 타임아웃 (30초)"
        exit 1
    fi
    sleep 1
done

python devtools/scripts/seed_mongodb.py

# ── 5. Qdrant 시딩 ──
echo ""
echo "[5/5] Qdrant 업무 매뉴얼 시딩..."

# Qdrant 준비 대기
for i in $(seq 1 30); do
    if curl -sf http://localhost:6333/healthz > /dev/null 2>&1; then
        echo "  Qdrant 준비 완료!"
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "  ERROR: Qdrant 시작 타임아웃 (30초)"
        exit 1
    fi
    sleep 1
done

python devtools/scripts/seed_qdrant.py

# ── 완료 ──
echo ""
echo "=============================================="
echo " 로컬 인프라 구축 완료!"
echo "=============================================="
echo ""
echo "서비스 상태:"
echo "  PostgreSQL     : localhost:5432 (test_db / postgres_db)"
echo "  MongoDB        : localhost:27017 (dpasset_column / dpasset_code / biz_term)"
echo "  Qdrant         : localhost:6333 (business_manual / sql_history)"
echo ""
echo "다음 단계:"
echo "  1. .env 에서 USE_DUMMY=false 로 변경"
echo "  2. uvicorn src.main:app --reload"
echo "  3. 또는: python -m src.agents.graph.runner '이번 달 신규 고객 수 알려줘'"
echo ""
