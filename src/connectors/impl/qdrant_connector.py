"""Qdrant 벡터 스토어 커넥터 — 업무 매뉴얼 및 SQL 수행이력 시맨틱 검색.

2개의 Qdrant 컬렉션을 대상으로 서로 다른 검색 전략을 적용한다.
biz_manual 컬렉션은 Dense 벡터 단일 검색으로 업무 매뉴얼(여신 심사, 연체 관리,
수신 상품, BIS 비율, 고객 등급 등)에서 관련 문서를 검색한다.
sql_history 컬렉션은 BGE-M3 임베딩 기반 Dense(의미 벡터) + Sparse(키워드) 하이브리드
검색을 수행하고, RRF(Reciprocal Rank Fusion)로 두 결과를 융합하여 최종 순위를 산출한다.
Reranker는 이 커넥터 외부(search_context_assembler)에서 별도로 적용한다.

핵심 함수/클래스:
    - QdrantConnector: SearchConnector 구현체, 2개 컬렉션 검색 통합 관리
    - search_manual: biz_manual Dense 검색 (업무 규정, 계수산출식 등)
    - search_sql_history: sql_history Dense+Sparse 하이브리드 검색 (RRF 융합)

Dummy 모드: use_dummy=True(기본값)일 때 Qdrant 연결 없이 동작한다.
biz_manual은 5건의 은행 업무 매뉴얼 샘플을, sql_history는 5건의 SQL 수행이력 샘플을
키워드 점수 기반으로 반환한다. 폐쇄망 배포 시 settings에서 호스트/포트를 전환한다.
"""

from __future__ import annotations

from typing import Any

from src.config import settings
from src.connectors.interfaces import SearchConnector
from src.connectors.dummy_data import (
    search_dummy_manuals,
    search_dummy_qdrant_sql_history,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)


class QdrantConnector(SearchConnector):
    """Qdrant 벡터 스토어 커넥터.

    biz_manual: Dense 검색 (업무 매뉴얼)
    sql_history: Dense+Sparse 하이브리드 검색 (SQL 수행이력)
    Dummy 모드 지원.
    """

    def __init__(self, use_dummy: bool = True) -> None:
        self._use_dummy = use_dummy
        self._client: Any = None

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

        from src.services.search_query_embedder import (
            get_search_query_embedder,
        )

        embedding = get_search_query_embedder().encode_dense_only(
            query,
        )
        import time as _time

        _start = _time.perf_counter()
        results = await self._client.search(
            collection_name=settings.qdrant_collection_name,
            query_vector=("dense", embedding),
            limit=top_k,
        )
        _elapsed = (_time.perf_counter() - _start) * 1000
        logger.info(
            "Qdrant 매뉴얼 검색",
            query=query[:60],
            count=len(results),
            latency_ms=round(_elapsed, 1),
        )
        return [hit.payload for hit in results]

    # ──────────────────────────────────────────────────────
    # sql_history 검색 (Dense + Sparse 하이브리드 → RRF)
    # ──────────────────────────────────────────────────────

    async def search_sql_history(
        self,
        query: str,
        prefetch_limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """SQL 수행이력에서 유사한 SQL을 하이브리드 검색한다.

        Dense(의미 벡터) + Sparse(키워드) → RRF 융합.
        Reranker는 이 메서드 외부(search_context_assembler)에서 적용한다.
        """
        if prefetch_limit is None:
            prefetch_limit = (
                settings.qdrant_sql_history_prefetch_limit
            )
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

        from src.services.search_query_embedder import (
            get_search_query_embedder,
        )

        emb = get_search_query_embedder().encode(query)

        sparse_vector = SparseVector(
            indices=emb.sparse_indices,
            values=emb.sparse_values,
        )

        import time as _time

        _start = _time.perf_counter()
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
        _elapsed = (_time.perf_counter() - _start) * 1000

        payloads = [
            {**point.payload, "_score": point.score}
            for point in results.points
        ]
        logger.info(
            "Qdrant sql_history 하이브리드 검색",
            query=query[:60],
            count=len(payloads),
            latency_ms=round(_elapsed, 1),
        )
        return payloads
