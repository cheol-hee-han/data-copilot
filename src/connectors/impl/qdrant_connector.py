"""Qdrant 벡터 스토어 커넥터 — 업무 매뉴얼 및 SQL 수행이력 시맨틱 검색.

2개의 Qdrant 컬렉션을 대상으로 서로 다른 검색 전략을 적용한다.
biz_manual 컬렉션은 Dense 벡터 단일 검색으로 업무 매뉴얼(여신 심사, 연체 관리,
수신 상품, BIS 비율, 고객 등급 등)에서 관련 문서를 검색한다.
sql_history 컬렉션은 BGE-M3 임베딩 기반 Dense(의미 벡터) + Sparse(키워드) 하이브리드
검색을 수행하고, RRF(Reciprocal Rank Fusion)로 두 결과를 융합하여 최종 순위를 산출한다.
Reranker(BGE-Reranker-v2-m3)도 커넥터에 통합하여 검색 후 자동 재순위한다.

임베딩 모델:
    BAAI/bge-m3 (570M, MIT 라이선스)를 내장하여 쿼리 벡터를 직접 생성한다.
    Dense(1024-dim) + Sparse 벡터를 단일 모델로 동시 생성하며,
    Lazy loading으로 최초 검색 호출 시에만 모델을 로드한다.
    임베딩은 검색 인프라의 일부이므로 서비스 계층이 아닌 커넥터에 통합한다.

핵심 함수/클래스:
    - QdrantConnector: SearchConnector 구현체, 2개 컬렉션 검색 + 임베딩 통합 관리
    - search_manual: biz_manual Dense 검색 (업무 규정, 계수산출식 등)
    - search_sql_history: sql_history Dense+Sparse 하이브리드 검색 (RRF 융합) + Reranker 재순위
    - encode / encode_batch / encode_dense_only: BGE-M3 임베딩 생성

Dummy 모드: use_dummy=True(기본값)일 때 Qdrant 연결 없이 동작한다.
biz_manual은 5건의 은행 업무 매뉴얼 샘플을, sql_history는 5건의 SQL 수행이력 샘플을
키워드 점수 기반으로 반환한다. 폐쇄망 배포 시 settings에서 호스트/포트를 전환한다.
"""

from __future__ import annotations

import time as _time
from dataclasses import dataclass, field
from typing import Any

from src.config import settings
from src.connectors.interfaces import SearchConnector
from src.connectors.dummy_data import (
    search_dummy_manuals,
    search_dummy_qdrant_sql_history,
)
from src.utils.logger import get_logger
from src.utils.truncate import truncate_log, truncate_trace

logger = get_logger(__name__)


@dataclass
class EmbeddingResult:
    """임베딩 결과.

    Attributes:
        dense: Dense 벡터 (1024-dim float 리스트).
        sparse_indices: Sparse 벡터의 토큰 인덱스 리스트.
        sparse_values: Sparse 벡터의 가중치 리스트.
    """

    dense: list[float] = field(default_factory=list)
    sparse_indices: list[int] = field(default_factory=list)
    sparse_values: list[float] = field(default_factory=list)


class QdrantConnector(SearchConnector):
    """Qdrant 벡터 스토어 커넥터.

    biz_manual: Dense 검색 (업무 매뉴얼)
    sql_history: Dense+Sparse 하이브리드 검색 (SQL 수행이력)
    임베딩: BGE-M3 모델 내장 (Dense + Sparse 동시 생성)
    Dummy 모드 지원.
    """

    def __init__(self, use_dummy: bool = True) -> None:
        self._use_dummy = use_dummy
        self._client: Any = None
        self._embed_model: Any = None

    async def connect(self) -> None:
        """Qdrant 연결 초기화."""
        if self._use_dummy:
            logger.info("Qdrant Dummy 모드로 초기화")
            return

        from qdrant_client import AsyncQdrantClient

        self._client = AsyncQdrantClient(
            host=settings.qdrant_host,
            port=settings.qdrant_port,
            timeout=settings.qdrant_request_timeout,
        )
        logger.info("Qdrant 연결 완료")

    async def disconnect(self) -> None:
        """Qdrant 연결 종료."""
        if self._client:
            await self._client.close()

    async def health_check(self) -> bool:
        """연결 상태 확인."""
        if self._use_dummy:
            return True
        try:
            await self._client.get_collections()
            return True
        except Exception:
            return False

    async def search(
        self, query: str, **kwargs: Any
    ) -> list[dict[str, Any]]:
        """업무 매뉴얼을 검색한다."""
        return await self.search_manual(
            query,
            top_k=kwargs.get(
                "top_k", settings.qdrant_search_top_k,
            ),
        )

    # ──────────────────────────────────────────────────────
    # BGE-M3 임베딩
    # ──────────────────────────────────────────────────────

    def _ensure_embed_model(self) -> None:
        """BGE-M3 모델을 로드한다 (최초 1회)."""
        if self._embed_model is not None:
            return

        from FlagEmbedding import BGEM3FlagModel

        kwargs: dict[str, Any] = {
            "model_name_or_path": settings.embedding_model,
            "use_fp16": settings.embedding_use_fp16,
        }
        if settings.embedding_cache_path:
            kwargs["cache_dir"] = settings.embedding_cache_path

        logger.info(
            "BGE-M3 모델 로딩 시작",
            model=settings.embedding_model,
            fp16=settings.embedding_use_fp16,
        )
        self._embed_model = BGEM3FlagModel(**kwargs)
        logger.info("BGE-M3 모델 로딩 완료")

    def encode(self, text: str) -> EmbeddingResult:
        """단일 텍스트를 Dense + Sparse 벡터로 변환한다."""
        start = _time.perf_counter()
        results = self.encode_batch([text])
        elapsed = (_time.perf_counter() - start) * 1000

        logger.info(
            "임베딩 생성 완료",
            text_length=len(text),
            dense_dim=len(results[0].dense),
            sparse_nnz=len(results[0].sparse_indices),
            latency_ms=round(elapsed, 1),
        )

        # 임베딩 추적: sync 메서드이므로 fire-and-forget으로 디스패치
        from src.utils.tracker.dispatch import (
            dispatch_tracking_event,
            CONTEXT_EMBEDDING,
        )
        import asyncio as _asyncio
        try:
            loop = _asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            loop.create_task(dispatch_tracking_event(
                CONTEXT_EMBEDDING, {
                    "source": "embedding_encode",
                    "query": truncate_trace(text),
                    "results_count": 1,
                    "results_summary": [
                        f"dense_dim={len(results[0].dense)}",
                        f"sparse_nnz="
                        f"{len(results[0].sparse_indices)}",
                    ],
                    "latency_ms": elapsed,
                },
            ))

        return results[0]

    def encode_batch(
        self, texts: list[str],
    ) -> list[EmbeddingResult]:
        """텍스트 배치를 Dense + Sparse 벡터로 변환한다.

        Args:
            texts: 임베딩할 텍스트 리스트.

        Returns:
            EmbeddingResult 리스트 (Dense + Sparse 벡터 포함).
        """
        self._ensure_embed_model()

        output = self._embed_model.encode(
            texts,
            batch_size=settings.embedding_batch_size,
            return_dense=True,
            return_sparse=True,
            return_colbert_vecs=False,
        )

        results: list[EmbeddingResult] = []
        for i in range(len(texts)):
            dense = output["dense_vecs"][i].tolist()

            # Sparse 벡터 (lexical_weights → indices, values)
            sparse_dict = output["lexical_weights"][i]
            indices: list[int] = []
            values: list[float] = []
            for token_id, weight in sorted(sparse_dict.items()):
                idx = int(token_id)
                val = float(weight)
                if val > 0:
                    indices.append(idx)
                    values.append(val)

            results.append(
                EmbeddingResult(
                    dense=dense,
                    sparse_indices=indices,
                    sparse_values=values,
                )
            )

        return results

    def encode_dense_only(self, text: str) -> list[float]:
        """Dense 벡터만 생성한다 (biz_manual 등 Dense-only 검색용)."""
        self._ensure_embed_model()

        output = self._embed_model.encode(
            [text],
            return_dense=True,
            return_sparse=False,
            return_colbert_vecs=False,
        )
        return output["dense_vecs"][0].tolist()

    # ──────────────────────────────────────────────────────
    # biz_manual 검색 (Dense)
    # ──────────────────────────────────────────────────────

    async def search_manual(
        self, query: str, top_k: int | None = None
    ) -> list[dict[str, Any]]:
        """업무 매뉴얼에서 관련 문서를 검색한다."""
        if top_k is None:
            top_k = settings.qdrant_search_top_k
        if self._use_dummy:
            return search_dummy_manuals(query, top_k)

        embedding = self.encode_dense_only(query)

        start = _time.perf_counter()
        results = await self._client.search(
            collection_name=settings.qdrant_collection_name,
            query_vector=("dense", embedding),
            limit=top_k,
        )
        elapsed = (_time.perf_counter() - start) * 1000
        logger.info(
            "Qdrant 매뉴얼 검색",
            query=truncate_log(query),
            count=len(results),
            latency_ms=round(elapsed, 1),
        )
        return [hit.payload for hit in results]

    # ──────────────────────────────────────────────────────
    # sql_history 검색 (Dense + Sparse 하이브리드 → RRF → Rerank)
    # ──────────────────────────────────────────────────────

    async def search_sql_history(
        self,
        query: str,
        prefetch_limit: int | None = None,
        top_k: int | None = None,
    ) -> list[dict[str, Any]]:
        """SQL 수행이력에서 유사한 SQL을 하이브리드 검색 + 재순위한다.

        1단계: Dense(의미 벡터) + Sparse(키워드) → RRF 융합
        2단계: Reranker Cross-Encoder 재순위 (상위 top_k건)
        Reranker 비활성 시 RRF 스코어 기준 상위 top_k건 폴백.
        반환 dict에 similarity 필드를 추가하여 confidence_scorer 호환성을 보장한다.
        """
        if prefetch_limit is None:
            prefetch_limit = (
                settings.qdrant_sql_history_prefetch_limit
            )
        if top_k is None:
            top_k = settings.qdrant_sql_history_top_k
        if self._use_dummy:
            return search_dummy_qdrant_sql_history(
                query, prefetch_limit,
            )

        from qdrant_client.models import (
            Fusion,
            FusionQuery,
            Prefetch,
            SparseVector,
        )

        emb = self.encode(query)

        sparse_vector = SparseVector(
            indices=emb.sparse_indices,
            values=emb.sparse_values,
        )

        start = _time.perf_counter()
        results = await self._client.query_points(
            collection_name=(
                settings.qdrant_sql_history_collection
            ),
            prefetch=[
                Prefetch(
                    query=emb.dense,
                    using="dense",
                    limit=prefetch_limit,
                ),
                Prefetch(
                    query=sparse_vector,
                    using="sparse",
                    limit=prefetch_limit,
                ),
            ],
            query=FusionQuery(fusion=Fusion.RRF),
            limit=prefetch_limit,
        )
        elapsed = (_time.perf_counter() - start) * 1000

        payloads = [
            {**point.payload, "_score": point.score}
            for point in results.points
        ]
        logger.info(
            "Qdrant sql_history 하이브리드 검색",
            query=truncate_log(query),
            count=len(payloads),
            latency_ms=round(elapsed, 1),
        )

        # Reranker 재순위
        return self._rerank(query, payloads, top_k)

    def _rerank(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        top_k: int,
    ) -> list[dict[str, Any]]:
        """검색 결과를 Reranker로 재순위한다.

        Reranker 비활성 시 RRF _score 기준 상위 top_k건을 반환한다.
        반환 dict에 similarity 필드를 추가한다.
        """
        from src.connectors.impl.reranker import (
            RerankCandidate,
            get_reranker,
        )

        rerank_candidates = [
            RerankCandidate(
                text=(
                    c.get("description", "")
                    + " "
                    + c.get("sql", "")
                ),
                payload=c,
                score=c.get("_score", 0.0),
            )
            for c in candidates
        ]

        reranker = get_reranker()
        reranked = reranker.rerank(
            query, rerank_candidates, top_k=top_k,
        )

        result: list[dict[str, Any]] = []
        for item in reranked:
            d = (
                item.payload.copy()
                if isinstance(item.payload, dict)
                else {}
            )
            d["_score"] = item.rerank_score
            d["similarity"] = item.rerank_score
            result.append(d)

        return result
