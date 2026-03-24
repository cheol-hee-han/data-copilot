"""검색 쿼리 임베더.

검색 쿼리를 Dense(1024-dim) + Sparse 벡터로 변환한다.
Qdrant 하이브리드 검색(Dense + Sparse → RRF)에 사용된다.

모델: BAAI/bge-m3 (570M, MIT 라이선스)
- 100개 언어 지원 (한/영 모두 최상위급)
- Dense + Sparse + ColBERT 3가지 검색 모드 단일 모델 지원
- 폐쇄망에서 로컬 모델 파일로 바로 사용 가능
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

# 싱글턴 인스턴스
_service: SearchQueryEmbedder | None = None


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


class SearchQueryEmbedder:
    """BGE-M3 기반 임베딩 서비스.

    Dense + Sparse 벡터를 동시에 생성한다.
    Lazy loading으로 모델을 최초 호출 시에만 로드한다.
    """

    def __init__(self) -> None:
        self._model: Any = None

    def _ensure_model(self) -> None:
        """BGE-M3 모델을 로드한다 (최초 1회)."""
        if self._model is not None:
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
        self._model = BGEM3FlagModel(**kwargs)
        logger.info("BGE-M3 모델 로딩 완료")

    def encode(self, text: str) -> EmbeddingResult:
        """단일 텍스트를 Dense + Sparse 벡터로 변환한다."""
        import time as _time

        _start = _time.perf_counter()
        results = self.encode_batch([text])
        _elapsed = (_time.perf_counter() - _start) * 1000

        logger.info(
            "임베딩 생성 완료",
            text_length=len(text),
            dense_dim=len(results[0].dense),
            sparse_nnz=len(results[0].sparse_indices),
            latency_ms=round(_elapsed, 1),
        )

        from src.utils.tracker import (
            get_current_tracker,
        )
        _tracker = get_current_tracker()
        if _tracker and _tracker.enabled:
            _tracker.track_context_retrieval(
                source="embedding_encode",
                query=text[:200],
                results_count=1,
                results_summary=[
                    f"dense_dim={len(results[0].dense)}",
                    f"sparse_nnz="
                    f"{len(results[0].sparse_indices)}",
                ],
                latency_ms=_elapsed,
            )

        return results[0]

    def encode_batch(self, texts: list[str]) -> list[EmbeddingResult]:
        """텍스트 배치를 Dense + Sparse 벡터로 변환한다.

        Args:
            texts: 임베딩할 텍스트 리스트.

        Returns:
            EmbeddingResult 리스트 (Dense + Sparse 벡터 포함).
        """
        self._ensure_model()

        output = self._model.encode(
            texts,
            batch_size=settings.embedding_batch_size,
            return_dense=True,
            return_sparse=True,
            return_colbert_vecs=False,  # ColBERT 불필요, 메모리 절약
        )

        results: list[EmbeddingResult] = []
        for i in range(len(texts)):
            # Dense 벡터
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
        self._ensure_model()

        output = self._model.encode(
            [text],
            return_dense=True,
            return_sparse=False,
            return_colbert_vecs=False,
        )
        return output["dense_vecs"][0].tolist()


def get_search_query_embedder() -> SearchQueryEmbedder:
    """임베딩 서비스 싱글턴 인스턴스를 반환한다."""
    global _service
    if _service is None:
        _service = SearchQueryEmbedder()
    return _service


def reset_search_query_embedder() -> None:
    """싱글턴 인스턴스를 초기화한다 (테스트 격리용)."""
    global _service
    _service = None
