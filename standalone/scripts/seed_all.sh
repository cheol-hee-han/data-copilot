#!/usr/bin/env bash
# ============================================================
# 로컬 개발 환경 구축: Docker 기동 → 데이터 시딩
# 사용법: bash standalone/scripts/seed_all.sh
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

echo "=============================================="
echo " Data Copilot - 로컬 인프라 구축"
echo "=============================================="

# ── 1. Docker Compose 기동 ──
echo ""
echo "[1/5] Docker 컨테이너 기동..."
docker compose -f standalone/docker/docker-compose.dev.yml up -d

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
    if docker exec dc-postgres psql -U postgres -d info_db -c "SELECT 1" > /dev/null 2>&1; then
        echo "  info_db 초기화 확인!"
        break
    fi
    sleep 1
done

# ── 3. PostgreSQL 시딩 ──
echo ""
echo "[3/5] PostgreSQL 가짜 데이터 시딩..."
python standalone/scripts/seed_postgres.py

# ── 4. ElasticSearch 시딩 ──
echo ""
echo "[4/5] ElasticSearch 메타 데이터 시딩..."

# ES는 기동이 느려서 넉넉하게 대기 (최대 60초)
for i in $(seq 1 60); do
    if curl -sf -u elastic:elastic_pass http://localhost:9200/_cluster/health > /dev/null 2>&1; then
        echo "  ElasticSearch 준비 완료!"
        break
    fi
    if [ "$i" -eq 60 ]; then
        echo "  ERROR: ElasticSearch 시작 타임아웃 (60초)"
        exit 1
    fi
    sleep 1
done

python standalone/scripts/seed_elasticsearch.py

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

python standalone/scripts/seed_qdrant.py

# ── 완료 ──
echo ""
echo "=============================================="
echo " 로컬 인프라 구축 완료!"
echo "=============================================="
echo ""
echo "서비스 상태:"
echo "  PostgreSQL     : localhost:5432 (info_db / history_db)"
echo "  ElasticSearch  : localhost:9200 (table_meta / report_sql / code_meta)"
echo "  Qdrant         : localhost:6333 (business_manual)"
echo ""
echo "다음 단계:"
echo "  1. .env 에서 USE_DUMMY=false 로 변경"
echo "  2. uvicorn src.main:app --reload"
echo "  3. 또는: python -m src.agents.graph.runner '이번 달 신규 고객 수 알려줘'"
echo ""
