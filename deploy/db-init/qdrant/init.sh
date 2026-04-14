#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# qdrant/init.sh — Qdrant 컬렉션 생성 (biz_manual, sql_history)
#
# devtools/scripts/seed_qdrant.py 는 "컬렉션 생성 + 데이터 시딩"이
# 하나의 함수로 묶여 있어 그대로는 재사용이 어렵다.
# 본 스크립트는 Qdrant HTTP API 를 직접 호출하여 **컬렉션 스키마만**
# 생성한다. 실데이터 임베딩/업로드는 별도 운영 절차로 수행한다.
#
# 필요 환경변수:
#   QDRANT_HOST (기본 localhost)
#   QDRANT_PORT (기본 6333)
#   EMBEDDING_DIM (기본 1024 — BGE-M3)
# ──────────────────────────────────────────────────────────────
set -euo pipefail

QDRANT_HOST="${QDRANT_HOST:-localhost}"
QDRANT_PORT="${QDRANT_PORT:-6333}"
EMBEDDING_DIM="${EMBEDDING_DIM:-1024}"
QDRANT_URL="http://${QDRANT_HOST}:${QDRANT_PORT}"

echo "==> Qdrant 초기화 시작"
echo "   URL            = $QDRANT_URL"
echo "   EMBEDDING_DIM  = $EMBEDDING_DIM"

# 연결 확인
if ! curl -sf "$QDRANT_URL/readyz" >/dev/null; then
    echo "!! Qdrant 연결 실패: $QDRANT_URL" >&2
    exit 1
fi

# ── biz_manual: Named Vector "dense" (seed_qdrant.py 와 동일) ─
echo "   > 컬렉션 생성: biz_manual"
curl -sf -X PUT "$QDRANT_URL/collections/biz_manual" \
    -H "Content-Type: application/json" \
    -d @- <<EOF >/dev/null
{
  "vectors": {
    "dense": {
      "size": ${EMBEDDING_DIM},
      "distance": "Cosine"
    }
  }
}
EOF

# ── sql_history: Named Vectors (dense + sparse 하이브리드) ───
echo "   > 컬렉션 생성: sql_history"
curl -sf -X PUT "$QDRANT_URL/collections/sql_history" \
    -H "Content-Type: application/json" \
    -d @- <<EOF >/dev/null
{
  "vectors": {
    "dense": {
      "size": ${EMBEDDING_DIM},
      "distance": "Cosine"
    }
  },
  "sparse_vectors": {
    "sparse": {
      "index": { "on_disk": false }
    }
  }
}
EOF

echo "✓ Qdrant 컬렉션 생성 완료"
echo "  실데이터 임베딩/업로드는 운영 시딩 절차로 별도 수행하세요."
