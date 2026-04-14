#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# download_models.sh — HuggingFace 임베딩/리랭커 모델 다운로드
#
# 사용법:
#   bash download_models.sh <DEST_DIR>
#
# 사전 조건:
#   - huggingface-cli 설치 (pip install -U "huggingface_hub[cli]")
#   - HuggingFace 접속 가능 (온라인)
#
# 산출물:
#   <DEST_DIR>/bge-m3/
#   <DEST_DIR>/bge-reranker-v2-m3/
# ──────────────────────────────────────────────────────────────
set -euo pipefail

DEST_DIR="${1:-./models}"
mkdir -p "$DEST_DIR"

if ! command -v huggingface-cli >/dev/null 2>&1; then
    echo "!! huggingface-cli 가 설치돼 있지 않습니다." >&2
    echo "   설치: pip install -U \"huggingface_hub[cli]\"" >&2
    exit 1
fi

echo "==> BGE-M3 (임베딩) 다운로드 → $DEST_DIR/bge-m3"
huggingface-cli download BAAI/bge-m3 \
    --local-dir "$DEST_DIR/bge-m3" \
    --local-dir-use-symlinks False

echo "==> BGE-Reranker-v2-m3 (리랭커) 다운로드 → $DEST_DIR/bge-reranker-v2-m3"
huggingface-cli download BAAI/bge-reranker-v2-m3 \
    --local-dir "$DEST_DIR/bge-reranker-v2-m3" \
    --local-dir-use-symlinks False

echo "✓ 모델 다운로드 완료"
du -sh "$DEST_DIR"/*
