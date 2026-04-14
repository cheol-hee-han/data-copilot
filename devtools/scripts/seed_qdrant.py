"""Qdrant 벡터 스토어 시딩.

biz_manual (500+) + sql_history (10,000+) 컬렉션 생성.
BGE-M3 모델로 Dense + Sparse 하이브리드 임베딩.
sql_history는 Named Vectors (dense, sparse)로 저장.

사용법 (Python 3.12 Docker):
    MSYS_NO_PATHCONV=1 docker run --rm --network host \
      -v "$(pwd)/standalone/scripts:/app/standalone/scripts:ro" \
      -v "$(pwd)/.env:/app/.env:ro" \
      -w /app python:3.12-slim \
      sh -c "pip install -q FlagEmbedding qdrant-client python-dotenv && \
             python standalone/scripts/seed_qdrant.py"
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_env_path = Path(__file__).resolve().parent.parent.parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path, encoding="utf-8")

QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
QDRANT_URL = f"http://{QDRANT_HOST}:{QDRANT_PORT}"
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1024"))
BATCH_SIZE = int(os.getenv("EMBEDDING_BATCH_SIZE", "64"))
USE_FP16 = os.getenv("EMBEDDING_USE_FP16", "false").lower() == "true"


def _load_bge_m3():
    """BGE-M3 모델을 로드한다."""
    from FlagEmbedding import BGEM3FlagModel

    print(f"  모델 로딩: {EMBEDDING_MODEL}")
    cache_dir = os.getenv("EMBEDDING_CACHE_PATH", "")
    kwargs = {
        "model_name_or_path": EMBEDDING_MODEL,
        "use_fp16": USE_FP16,
    }
    if cache_dir:
        kwargs["cache_dir"] = cache_dir
    return BGEM3FlagModel(**kwargs)


def _encode_hybrid(model, texts: list[str]):
    """Dense + Sparse 벡터를 동시에 생성한다."""
    output = model.encode(
        texts,
        batch_size=BATCH_SIZE,
        return_dense=True,
        return_sparse=True,
        return_colbert_vecs=False,
    )
    return output["dense_vecs"], output["lexical_weights"]


def seed_qdrant():
    """Qdrant 컬렉션(업무매뉴얼·SQL이력)을 생성하고 벡터 데이터를 적재한다."""
    from qdrant_client import QdrantClient
    from qdrant_client.models import (
        Distance,
        PointStruct,
        SparseIndexParams,
        SparseVector,
        SparseVectorParams,
        VectorParams,
    )

    # 데이터 생성기 임포트
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from qdrant_data_generators import (
        generate_biz_manual_data,
        generate_sql_history_data,
    )

    print(f"  Qdrant: {QDRANT_URL}")
    print(f"  임베딩: {EMBEDDING_MODEL} (dim={EMBEDDING_DIM})")

    model = _load_bge_m3()
    client = QdrantClient(url=QDRANT_URL, timeout=120)

    # ── biz_manual (Dense-only, Named Vector) ──
    print("\n[biz_manual]")
    biz_data = generate_biz_manual_data()
    print(f"  생성: {len(biz_data)}건")

    if client.collection_exists("biz_manual"):
        client.delete_collection("biz_manual")
    client.create_collection(
        collection_name="biz_manual",
        vectors_config={
            "dense": VectorParams(
                size=EMBEDDING_DIM,
                distance=Distance.COSINE,
            ),
        },
    )

    texts = [d["content"] for d in biz_data]
    # biz_manual은 Dense만 사용
    dense_vecs = model.encode(
        texts,
        batch_size=BATCH_SIZE,
        return_dense=True,
        return_sparse=False,
        return_colbert_vecs=False,
    )["dense_vecs"]

    points = [
        PointStruct(
            id=i,
            vector={"dense": vec.tolist()},
            payload=d,
        )
        for i, (d, vec) in enumerate(zip(biz_data, dense_vecs))
    ]
    for start in range(0, len(points), BATCH_SIZE):
        batch = points[start:start + BATCH_SIZE]
        client.upsert(collection_name="biz_manual", points=batch)
    info_biz = client.get_collection("biz_manual")
    print(f"  적재: {info_biz.points_count}건")

    # ── sql_history (Dense + Sparse, Named Vectors) ──
    print("\n[sql_history]")
    sql_data = generate_sql_history_data(10000)
    print(f"  생성: {len(sql_data)}건")

    if client.collection_exists("sql_history"):
        client.delete_collection("sql_history")
    client.create_collection(
        collection_name="sql_history",
        vectors_config={
            "dense": VectorParams(
                size=EMBEDDING_DIM,
                distance=Distance.COSINE,
            ),
        },
        sparse_vectors_config={
            "sparse": SparseVectorParams(
                index=SparseIndexParams(),
            ),
        },
    )

    # description을 임베딩 대상으로 사용
    # enriched 필드가 있으면 우선 사용 (문서 보강 적용 시)
    descriptions = [
        d.get("enriched", d["description"])
        for d in sql_data
    ]
    print(
        f"  임베딩 중... ({len(descriptions)}건, "
        f"배치 {BATCH_SIZE})"
    )

    dense_vecs, sparse_weights = _encode_hybrid(
        model, descriptions,
    )
    print(f"  임베딩 완료: {len(dense_vecs)}건")

    for start in range(0, len(sql_data), BATCH_SIZE):
        end = min(start + BATCH_SIZE, len(sql_data))
        batch_points = []
        for j in range(end - start):
            idx = start + j
            # Sparse 벡터 변환
            sw = sparse_weights[idx]
            s_indices = []
            s_values = []
            for token_id, weight in sorted(sw.items()):
                val = float(weight)
                if val > 0:
                    s_indices.append(int(token_id))
                    s_values.append(val)

            batch_points.append(
                PointStruct(
                    id=idx,
                    vector={
                        "dense": dense_vecs[idx].tolist(),
                        "sparse": SparseVector(
                            indices=s_indices,
                            values=s_values,
                        ),
                    },
                    payload=sql_data[idx],
                )
            )
        client.upsert(
            collection_name="sql_history",
            points=batch_points,
        )
        if (start // BATCH_SIZE) % 10 == 0:
            print(f"    upsert {end}/{len(sql_data)}")

    info_sql = client.get_collection("sql_history")
    print(f"  적재: {info_sql.points_count}건")

    # ── 결과 ──
    print("\n[적재 결과]")
    print(f"  biz_manual  : {info_biz.points_count}건 (Dense)")
    print(
        f"  sql_history : {info_sql.points_count}건 "
        "(Dense + Sparse)"
    )


if __name__ == "__main__":
    print("=" * 60)
    print("Qdrant 벡터 스토어 시딩 (BGE-M3 하이브리드)")
    print("=" * 60)
    seed_qdrant()
    print("\n" + "=" * 60)
    print("Qdrant 시딩 완료!")
    print("=" * 60)
