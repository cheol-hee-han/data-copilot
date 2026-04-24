"""Qdrant 벡터 스토어 커넥터 — 업무 매뉴얼 및 SQL 수행이력 시맨틱 검색.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

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

import asyncio
import os
import time as _time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

# ── HuggingFace/Transformers 로딩 노이즈 억제 ──
# FlagEmbedding/transformers import 전에 환경변수를 설정해야
# 모델 로딩 시 "Fetching N files" tqdm 바는 출력하고, tokenizer 경고는 억제한다.
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "0")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")

from src.config import settings  # noqa: E402
from src.connectors.interfaces import SearchConnector
from src.connectors.dummy_data import (
    search_dummy_manuals,
    search_dummy_qdrant_sql_history,
)
from src.utils.logger import get_logger
from src.utils.truncate import truncate_log, truncate_trace

logger = get_logger(__name__)

_EXECUTOR_NOT_READY_MSG = (
    "임베딩 executor 미초기화 — connect() 미호출 또는 dummy 모드"
)


def _parse_point_ids(
    ids: list[str | int],
) -> list[int | str]:
    """문자열로 저장된 point ID를 정수로 복원한다.

    Qdrant는 정수 ID 컬렉션에 문자열 ID 필터를 넘기면
    condition 파싱 오류(400)를 반환하므로, int 변환을 시도한다.
    """
    parsed: list[int | str] = []
    for v in ids:
        if isinstance(v, int):
            parsed.append(v)
            continue
        try:
            parsed.append(int(v))
        except (ValueError, TypeError):
            parsed.append(v)
    return parsed


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
        # 임베딩/리랭커 전용 단일 워커 executor.
        # BGE-M3, Reranker는 모델 인스턴스 동시 접근이 안전하지 않으므로
        # max_workers=1로 직렬화한다.
        self._embed_executor: ThreadPoolExecutor | None = None
        # tracker dispatch용 fire-and-forget task 강참조 보관
        # (Python 공식: create_task 반환 Task를 참조하지 않으면 GC로 취소됨)
        self._background_tasks: set[asyncio.Task[None]] = set()

    async def connect(self) -> None:
        """Qdrant 연결과 임베딩/리랭커 모델을 초기화한다.

        기동 시점에 BGE-M3 모델을 선로딩하고 워밍업하여,
        첫 질의 시 이벤트 루프가 16초 블록되는 문제를 방지한다.
        로딩 실패는 상위로 예외 전파 (fail-fast).
        """
        if self._use_dummy:
            logger.info("Qdrant Dummy 모드로 초기화")
            return

        # 멱등 가드: 중복 호출 시 executor/client 누수 방지
        if self._client is not None:
            return

        from qdrant_client import AsyncQdrantClient

        client = AsyncQdrantClient(
            host=settings.qdrant_host,
            port=settings.qdrant_port,
            timeout=settings.qdrant_request_timeout,
        )

        # 실접속 검증 — lazy client가 실제 TCP 핸드셰이크를 수행하도록 강제
        try:
            await client.get_collections()
        except Exception:
            await client.close()
            raise

        self._client = client
        logger.info("Qdrant 연결 완료")

        # 임베딩 전용 executor (워커 수는 settings 로 조정; 기본 2)
        self._embed_executor = ThreadPoolExecutor(
            max_workers=settings.qdrant_embed_workers,
            thread_name_prefix="qdrant-embed",
        )

        # BGE-M3 선로딩 + 워밍업 (이벤트 루프 블로킹 없이)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            self._embed_executor, self._ensure_embed_model,
        )
        await loop.run_in_executor(
            self._embed_executor, self._warmup_embed_model,
        )

    async def disconnect(self) -> None:
        """Qdrant 연결과 임베딩 executor를 종료한다.

        종료 순서:
            1. 진행 중인 tracker dispatch task 완료 대기 (2초 타임아웃)
            2. executor shutdown (in-flight encode/rerank 완료 대기)
            3. Qdrant client close
        """
        # (1) tracker dispatch task 정리
        if self._background_tasks:
            pending = list(self._background_tasks)
            _, still_pending = await asyncio.wait(
                pending, timeout=2.0,
            )
            for t in still_pending:
                t.cancel()
            self._background_tasks.clear()

        # (2) executor 종료 (in-flight encode/rerank 완료 대기)
        if self._embed_executor is not None:
            self._embed_executor.shutdown(wait=True)
            self._embed_executor = None

        # (3) client close
        if self._client:
            await self._client.close()
            self._client = None

    async def health_check(self) -> bool:
        """연결 상태를 확인한다."""
        if self._use_dummy:
            return True
        try:
            await self._client.get_collections()
            return True
        except Exception as e:
            logger.debug("health_check 실패", error=str(e))
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
        """BGE-M3 모델을 로드한다 (최초 1회).

        로딩 실패는 상위로 예외 전파 (fail-fast).
        """
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

    def _warmup_embed_model(self) -> None:
        """BGE-M3 첫 forward pass 비용을 기동 시에 소진한다.

        encode_batch를 그대로 호출하여 실사용 경로(dense+sparse 생성 및
        결과 변환)를 전부 예열한다. 내부 JIT, 토크나이저 캐시,
        sparse 정렬 루프까지 한 번씩 돌린다.
        """
        self.encode_batch(["워밍업"])
        logger.info("BGE-M3 워밍업 완료")

    def _spawn_background(self, coro: Any) -> None:
        """create_task + 강참조 보관으로 GC로 인한 취소 방지.

        Python 공식: create_task의 반환 Task를 강참조하지 않으면 GC에
        의해 코루틴이 실행 전 취소될 수 있다.
        """
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def encode(self, text: str) -> EmbeddingResult:
        """단일 텍스트를 Dense + Sparse 벡터로 변환한다 (sync).

        이 메서드는 sync 컨텍스트(배치 스크립트 등)에서도 직접 호출 가능.
        async 코드에서는 _encode_async()를 사용할 것.

        latency 로깅은 유지 (seed_sql_history.py 검증 단계 의존).
        tracker dispatch는 async 호출자(_encode_async)가 수행.
        """
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
        return results[0]

    async def _encode_async(self, text: str) -> EmbeddingResult:
        """executor 경유 안전 encode + tracker dispatch.

        encode() 내부에서 latency 로그를 찍으므로 여기선 로깅 생략.
        tracker dispatch는 async 컨텍스트에서 GC 안전하게 fire-and-forget.
        """
        if self._embed_executor is None:
            raise RuntimeError(_EXECUTOR_NOT_READY_MSG)

        loop = asyncio.get_running_loop()
        start = _time.perf_counter()
        result = await loop.run_in_executor(
            self._embed_executor, self.encode, text,
        )
        elapsed = (_time.perf_counter() - start) * 1000

        from src.utils.tracker.dispatch import (
            CONTEXT_EMBEDDING,
            dispatch_tracking_event,
        )
        self._spawn_background(dispatch_tracking_event(
            CONTEXT_EMBEDDING, {
                "source": "embedding_encode",
                "query": truncate_trace(text),
                "results_count": 1,
                "results_summary": [
                    f"dense_dim={len(result.dense)}",
                    f"sparse_nnz={len(result.sparse_indices)}",
                ],
                "latency_ms": elapsed,
            },
        ))
        return result

    async def _encode_dense_async(self, text: str) -> list[float]:
        """executor 경유 안전 dense-only encode.

        tracker dispatch 없음 — 기존 비대칭 유지 (P2에서 해소).
        """
        if self._embed_executor is None:
            raise RuntimeError(_EXECUTOR_NOT_READY_MSG)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._embed_executor, self.encode_dense_only, text,
        )

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
        result: list[float] = output["dense_vecs"][0].tolist()
        return result

    # ──────────────────────────────────────────────────────
    # biz_manual 검색 (Dense)
    # ──────────────────────────────────────────────────────

    async def search_manual(
        self,
        query: str,
        top_k: int | None = None,
        exclude_ids: list[str | int] | None = None,
    ) -> list[dict[str, Any]]:
        """업무 매뉴얼에서 관련 문서를 검색한다.

        Args:
            exclude_ids: 이전 검색에서 반환된 point id 목록.
                지정하면 해당 id를 검색에서 제외하고,
                제외된 만큼 limit을 보정한다.
        """
        if top_k is None:
            top_k = settings.qdrant_search_top_k
        if self._use_dummy:
            return search_dummy_manuals(query, top_k)

        query_filter = None
        effective_limit = top_k
        if exclude_ids:
            from qdrant_client.models import Filter, HasIdCondition
            query_filter = Filter(
                must_not=[HasIdCondition(has_id=_parse_point_ids(exclude_ids))],
            )
            effective_limit = min(
                top_k + len(exclude_ids),
                settings.qdrant_manual_max_limit,
            )

        embedding = await self._encode_dense_async(query)

        start = _time.perf_counter()
        results = await self._client.search(
            collection_name=settings.qdrant_collection_name,
            query_vector=("dense", embedding),
            limit=effective_limit,
            query_filter=query_filter,
        )
        elapsed = (_time.perf_counter() - start) * 1000
        logger.info(
            "Qdrant 매뉴얼 검색",
            query=truncate_log(query),
            count=len(results),
            excluded=len(exclude_ids) if exclude_ids else 0,
            latency_ms=round(elapsed, 1),
        )
        return [
            {**hit.payload, "_point_id": str(hit.id)}
            for hit in results[:top_k]
        ]

    # ──────────────────────────────────────────────────────
    # sql_history 검색 (Dense + Sparse 하이브리드 → RRF → Rerank)
    # ──────────────────────────────────────────────────────

    async def search_sql_history(
        self,
        query: str,
        prefetch_limit: int | None = None,
        top_k: int | None = None,
        exclude_ids: list[str | int] | None = None,
    ) -> list[dict[str, Any]]:
        """SQL 수행이력에서 유사한 SQL을 하이브리드 검색 + 재순위한다.

        1단계: Dense(의미 벡터) + Sparse(키워드) → RRF 융합
        2단계: Reranker Cross-Encoder 재순위 (상위 top_k건)
        Reranker 비활성 시 RRF 스코어 기준 상위 top_k건 폴백.
        반환 dict에 similarity 필드를 추가하여 confidence_scorer 호환성을 보장한다.

        Args:
            exclude_ids: 이전 검색에서 반환된 point id 목록.
                지정하면 해당 id를 HNSW 탐색에서 제외하고,
                제외된 만큼 prefetch_limit을 보정하여
                reranker에 항상 동일한 수의 후보가 들어가도록 한다.
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
            Filter,
            Fusion,
            FusionQuery,
            HasIdCondition,
            Prefetch,
            SparseVector,
        )

        # seen_ids 필터 + prefetch 보정
        query_filter = None
        effective_prefetch = prefetch_limit
        if exclude_ids:
            query_filter = Filter(
                must_not=[HasIdCondition(has_id=_parse_point_ids(exclude_ids))],
            )
            effective_prefetch = min(
                prefetch_limit + len(exclude_ids),
                settings.qdrant_max_prefetch,
            )

        emb = await self._encode_async(query)

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
                    limit=effective_prefetch,
                    filter=query_filter,
                ),
                Prefetch(
                    query=sparse_vector,
                    using="sparse",
                    limit=effective_prefetch,
                    filter=query_filter,
                ),
            ],
            query=FusionQuery(fusion=Fusion.RRF),
            limit=effective_prefetch,
            query_filter=query_filter,
        )
        elapsed = (_time.perf_counter() - start) * 1000

        # _point_id 포함하여 다음 호출 시 exclude_ids로 전달 가능
        payloads = [
            {**point.payload, "_score": point.score,
             "_point_id": str(point.id)}
            for point in results.points
        ]
        logger.info(
            "Qdrant sql_history 하이브리드 검색",
            query=truncate_log(query),
            count=len(payloads),
            excluded=len(exclude_ids) if exclude_ids else 0,
            latency_ms=round(elapsed, 1),
        )

        # Reranker 재순위
        return await self._rerank(query, payloads, top_k)

    async def _rerank(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        top_k: int,
    ) -> list[dict[str, Any]]:
        """검색 결과를 Reranker로 재순위한다 (async).

        Reranker 비활성 시 RRF _score 기준 상위 top_k건을 반환한다.
        반환 dict에 similarity 필드를 추가한다.

        Reranker.rerank()는 sync + CPU 바운드이므로 executor 경유로 호출.
        tracker dispatch는 여기서 수행 (async 컨텍스트, GC 안전).
        """
        if self._embed_executor is None:
            raise RuntimeError(_EXECUTOR_NOT_READY_MSG)

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
        loop = asyncio.get_running_loop()
        # 주의: lambda 내부에서 raise된 예외는 Future로 래핑되어
        # await 지점에서 재발생한다. try/except 없이 상위로 전파한다.
        reranked, stats = await loop.run_in_executor(
            self._embed_executor,
            lambda: reranker.rerank(
                query, rerank_candidates, top_k=top_k,
            ),
        )

        # tracker dispatch (async 컨텍스트, GC 안전)
        from src.utils.tracker.dispatch import (
            CONTEXT_RERANKED,
            dispatch_tracking_event,
        )
        self._spawn_background(dispatch_tracking_event(
            CONTEXT_RERANKED, {
                "source": "reranker",
                "query": truncate_trace(query),
                "results_count": len(reranked),
                "results_summary": [
                    f"backend={settings.reranker_backend}",
                    f"input={stats.input_count}",
                    f"filtered={stats.filtered_count}",
                    f"output={len(reranked)}",
                    *(
                        truncate_trace(
                            f"{c.payload.get('description', '?')}"
                            f" (score={c.rerank_score:.3f})"
                        )
                        for c in reranked[:3]
                    ),
                ],
                "latency_ms": stats.latency_ms,
            },
        ))

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
