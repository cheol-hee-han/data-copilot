"""BGE-Reranker-v2-m3 재순위 서비스.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

Cross-Encoder 방식으로 쿼리-문서 쌍을 동시 분석하여
벡터 검색의 Top-N 후보를 정밀 재순위한다.

CPU 최적화 전략 (4단계 누적 가속):
  1. ONNX Runtime O3 — 그래프 최적화 + CPU 멀티스레드 (1.5~2.0x)
  2. INT8 동적 양자화 — 모델 크기 75% 감소 (누적 2.5~3.5x)
  3. 입력 길이 정렬 — 패딩 낭비 최소화 (누적 2.8~4.0x)
  4. 사전 필터링 — 벡터 스코어 하위 후보 제거 (누적 4~6x)

설정 (.env):
  RERANKER_BACKEND=onnx          # "pytorch" 또는 "onnx"
  RERANKER_QUANTIZE=true         # INT8 동적 양자화
  RERANKER_CPU_THREADS=0         # 0=자동감지(물리코어수)
  RERANKER_SCORE_THRESHOLD=0.0   # 사전 필터링 임계값
  RERANKER_MIN_CANDIDATES=15     # 필터 후 최소 보장 수
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.config import settings
from src.utils.logger import get_logger
from src.utils.truncate import truncate_trace

logger = get_logger(__name__)

_reranker: Reranker | None = None


@dataclass
class RerankCandidate:
    """재순위 후보.

    Attributes:
        text: 문서 텍스트 (재순위 대상).
        payload: 원본 페이로드 (SQL, description 등).
        score: 원본 벡터 검색 스코어.
        rerank_score: 재순위 스코어 (Reranker 적용 후).
    """

    text: str
    payload: dict[str, Any]
    score: float = 0.0
    rerank_score: float = 0.0


def _resolve_cpu_threads() -> int:
    """CPU 스레드 수를 결정한다.

    settings.reranker_cpu_threads가 0이면
    물리 코어 수를 자동 감지한다.
    """
    configured = settings.reranker_cpu_threads
    if configured > 0:
        return configured

    # 물리 코어 수 감지
    try:
        import psutil
        physical = psutil.cpu_count(logical=False)
        if physical:
            return physical
    except ImportError:
        pass

    # psutil 없으면 논리 코어 / 2 (HT 가정)
    logical = os.cpu_count() or 4
    return max(logical // 2, 1)


# ──────────────────────────────────────────────────────────────
# ONNX 백엔드
# ──────────────────────────────────────────────────────────────

class _OnnxRerankerBackend:
    """ONNX Runtime 기반 Reranker 백엔드.

    CPU 최적화: O3 그래프 최적화 + INT8 동적 양자화.
    """

    def __init__(self) -> None:
        self._session: Any = None
        self._tokenizer: Any = None

    def load(self) -> None:
        """ONNX 세션과 토크나이저를 초기화한다."""
        import onnxruntime as ort

        onnx_path = self._resolve_onnx_model()
        threads = _resolve_cpu_threads()

        sess_options = ort.SessionOptions()
        sess_options.intra_op_num_threads = threads
        sess_options.inter_op_num_threads = 1
        sess_options.execution_mode = (
            ort.ExecutionMode.ORT_SEQUENTIAL
        )
        sess_options.graph_optimization_level = (
            ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        )

        logger.info(
            "ONNX Reranker 세션 생성",
            model=onnx_path,
            threads=threads,
            quantized=settings.reranker_quantize,
        )

        self._session = ort.InferenceSession(
            str(onnx_path),
            sess_options=sess_options,
            providers=["CPUExecutionProvider"],
        )

        from transformers import AutoTokenizer
        model_name = settings.reranker_model
        cache = settings.reranker_cache_path or None
        self._tokenizer = AutoTokenizer.from_pretrained(
            model_name, cache_dir=cache,
        )

        logger.info("ONNX Reranker 로딩 완료")

    def _resolve_onnx_model(self) -> Path:
        """ONNX 모델 경로를 확인/생성한다."""
        # 명시적 경로가 있으면 사용
        if settings.reranker_onnx_path:
            path = Path(settings.reranker_onnx_path)
            if path.exists():
                return path

        # 캐시 디렉토리에 ONNX 모델 존재 확인
        cache_dir = Path(
            settings.reranker_cache_path
            or _default_cache_dir()
        )
        quantize_suffix = (
            "_int8" if settings.reranker_quantize else ""
        )
        onnx_dir = (
            cache_dir / "onnx_reranker" / (
                settings.reranker_model.replace("/", "_")
                + quantize_suffix
            )
        )
        onnx_path = onnx_dir / "model.onnx"

        if onnx_path.exists():
            logger.info(
                "캐시된 ONNX 모델 사용",
                path=str(onnx_path),
            )
            return onnx_path

        # 없으면 PyTorch → ONNX 변환
        logger.info(
            "ONNX 모델 변환 시작 (최초 1회)",
            source=settings.reranker_model,
        )
        return _export_to_onnx(
            settings.reranker_model,
            onnx_dir,
            quantize=settings.reranker_quantize,
            cache_dir=(
                settings.reranker_cache_path or None
            ),
        )

    def compute_scores(
        self, query: str, documents: list[str],
    ) -> list[float]:
        """(query, document) 쌍의 유사도 스코어를 계산한다."""
        import numpy as np

        # 입력 길이 정렬 (패딩 낭비 최소화)
        pairs, orig_indices = _sort_by_length(
            query, documents,
        )

        # 토크나이즈
        encoded = self._tokenizer(
            pairs,
            padding=True,
            truncation=True,
            max_length=512,
            return_tensors="np",
        )

        # ONNX 추론
        inputs = {
            "input_ids": encoded["input_ids"],
            "attention_mask": encoded["attention_mask"],
        }
        # token_type_ids가 있으면 추가
        if "token_type_ids" in encoded:
            inputs["token_type_ids"] = (
                encoded["token_type_ids"]
            )

        outputs = self._session.run(None, inputs)
        logits = outputs[0]

        # sigmoid normalize
        scores_sorted = (
            1.0 / (1.0 + np.exp(-logits[:, 0]))
        ).tolist()

        # 원래 순서로 복원
        scores = [0.0] * len(documents)
        for sorted_idx, orig_idx in enumerate(orig_indices):
            scores[orig_idx] = scores_sorted[sorted_idx]

        return scores


# ──────────────────────────────────────────────────────────────
# PyTorch 백엔드 (기존 FlagReranker 래퍼)
# ──────────────────────────────────────────────────────────────

class _PyTorchRerankerBackend:
    """FlagEmbedding FlagReranker 기반 백엔드."""

    def __init__(self) -> None:
        self._model: Any = None

    def load(self) -> None:
        """FlagReranker 모델을 초기화한다."""
        from FlagEmbedding import FlagReranker

        # CPU에서는 fp16 비활성화
        use_fp16 = settings.reranker_use_fp16
        threads = _resolve_cpu_threads()

        kwargs: dict[str, Any] = {
            "model_name_or_path": settings.reranker_model,
            "use_fp16": use_fp16,
        }
        if settings.reranker_cache_path:
            kwargs["cache_dir"] = settings.reranker_cache_path

        # PyTorch CPU 스레드 설정
        try:
            import torch
            torch.set_num_threads(threads)
            torch.set_num_interop_threads(1)
        except Exception:
            pass

        logger.info(
            "PyTorch Reranker 로딩 시작",
            model=settings.reranker_model,
            threads=threads,
        )
        self._model = FlagReranker(**kwargs)
        logger.info("PyTorch Reranker 로딩 완료")

    def compute_scores(
        self, query: str, documents: list[str],
    ) -> list[float]:
        """스코어를 계산한다."""
        pairs = [[query, doc] for doc in documents]
        scores = self._model.compute_score(
            pairs, normalize=True,
        )
        if isinstance(scores, (float, int)):
            scores = [scores]
        return [float(s) for s in scores]


# ──────────────────────────────────────────────────────────────
# 메인 Reranker 클래스
# ──────────────────────────────────────────────────────────────

class Reranker:
    """BGE-Reranker-v2-m3 래퍼.

    ONNX/PyTorch 백엔드 자동 선택.
    비활성화 시 원본 스코어 기반 Top-K를 반환한다.
    """

    def __init__(self) -> None:
        self._backend: Any = None
        self._enabled = settings.reranker_enabled

    def _ensure_model(self) -> None:
        """백엔드를 초기화한다 (최초 1회)."""
        if self._backend is not None or not self._enabled:
            return

        try:
            backend_type = settings.reranker_backend
            if backend_type == "onnx":
                self._backend = _OnnxRerankerBackend()
            else:
                self._backend = _PyTorchRerankerBackend()
            self._backend.load()
        except Exception as e:
            logger.warning(
                "Reranker 모델 로딩 실패, 비활성화 폴백",
                backend=settings.reranker_backend,
                error=str(e),
            )
            self._enabled = False

    def rerank(
        self,
        query: str,
        candidates: list[RerankCandidate],
        top_k: int | None = None,
    ) -> list[RerankCandidate]:
        """후보 문서를 재순위한다.

        최적화 파이프라인:
            1. 사전 필터링 (스코어 임계값)
            2. 백엔드 스코어링 (ONNX/PyTorch)
            3. 재순위 정렬
        """
        if top_k is None:
            top_k = settings.reranker_top_k

        if not candidates:
            return []

        if not self._enabled:
            return _sort_by_score(candidates)[:top_k]

        self._ensure_model()
        if not self._enabled or self._backend is None:
            return _sort_by_score(candidates)[:top_k]

        # Step 1: 사전 필터링
        filtered = _prefilter(candidates)

        # Step 2: 스코어링
        start = time.perf_counter()
        documents = [c.text for c in filtered]
        scores = self._backend.compute_scores(
            query, documents,
        )
        elapsed = (time.perf_counter() - start) * 1000

        for candidate, score in zip(filtered, scores):
            candidate.rerank_score = score

        # Step 3: 재순위 정렬
        reranked = sorted(
            filtered,
            key=lambda c: c.rerank_score,
            reverse=True,
        )

        result_count = min(top_k, len(reranked))
        logger.info(
            "Reranker 재순위 완료",
            backend=settings.reranker_backend,
            input_count=len(candidates),
            filtered_count=len(filtered),
            output_count=result_count,
            latency_ms=round(elapsed, 1),
            top_score=(
                round(reranked[0].rerank_score, 4)
                if reranked else 0
            ),
        )

        # 리랭킹 추적: sync 메서드이므로 fire-and-forget
        from src.utils.tracker.dispatch import (
            dispatch_tracking_event,
            CONTEXT_RERANKED,
        )
        import asyncio as _asyncio
        try:
            _loop = _asyncio.get_running_loop()
        except RuntimeError:
            _loop = None
        if _loop and _loop.is_running():
            _loop.create_task(dispatch_tracking_event(
                CONTEXT_RERANKED, {
                    "source": "reranker",
                    "query": truncate_trace(query),
                    "results_count": result_count,
                    "results_summary": [
                        f"backend={settings.reranker_backend}",
                        f"input={len(candidates)}",
                        f"filtered={len(filtered)}",
                        f"output={result_count}",
                        *(
                            truncate_trace(
                                f"{c.payload.get('description', '?')}"
                                f" (score={c.rerank_score:.3f})"
                            )
                            for c in reranked[:3]
                        ),
                    ],
                    "latency_ms": elapsed,
                },
            ))

        return reranked[:top_k]


# ──────────────────────────────────────────────────────────────
# 유틸리티
# ──────────────────────────────────────────────────────────────

def _sort_by_score(
    candidates: list[RerankCandidate],
) -> list[RerankCandidate]:
    """벡터 검색 스코어 기준으로 정렬한다."""
    return sorted(
        candidates, key=lambda c: c.score, reverse=True,
    )


def _prefilter(
    candidates: list[RerankCandidate],
) -> list[RerankCandidate]:
    """벡터 검색 스코어가 낮은 후보를 제거한다.

    threshold가 0이면 모든 후보를 통과시킨다.
    min_candidates로 최소 후보 수를 보장한다.
    """
    threshold = settings.reranker_score_threshold
    min_count = settings.reranker_min_candidates

    if threshold <= 0.0:
        return candidates

    # 스코어 내림차순 정렬
    sorted_cands = _sort_by_score(candidates)

    # 임계값 이상만 필터
    filtered = [
        c for c in sorted_cands if c.score >= threshold
    ]

    # 최소 보장
    if len(filtered) < min_count:
        filtered = sorted_cands[:max(min_count, len(filtered))]

    return filtered


def _sort_by_length(
    query: str, documents: list[str],
) -> tuple[list[list[str]], list[int]]:
    """(query, doc) 쌍을 doc 길이 기준으로 정렬한다.

    비슷한 길이의 입력끼리 묶으면 패딩 낭비가 줄어
    토크나이저·추론 효율이 향상된다.

    Returns:
        (정렬된 pairs, 원래 인덱스 매핑)
    """
    indexed = [
        (i, len(doc), doc) for i, doc in enumerate(documents)
    ]
    indexed.sort(key=lambda x: x[1])

    pairs = [[query, item[2]] for item in indexed]
    orig_indices = [item[0] for item in indexed]

    return pairs, orig_indices


def _default_cache_dir() -> str:
    """기본 캐시 디렉토리를 반환한다."""
    return str(
        Path.home() / ".cache" / "data-copilot"
    )


def _export_to_onnx(
    model_name: str,
    output_dir: Path,
    quantize: bool = True,
    cache_dir: str | None = None,
) -> Path:
    """PyTorch 모델을 ONNX로 변환한다.

    Args:
        model_name: HuggingFace 모델명.
        output_dir: ONNX 저장 디렉토리.
        quantize: INT8 동적 양자화 적용 여부.
        cache_dir: 모델 캐시 디렉토리.

    Returns:
        최종 ONNX 모델 경로.
    """
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    output_dir.mkdir(parents=True, exist_ok=True)

    # 모델 로드
    tokenizer = AutoTokenizer.from_pretrained(
        model_name, cache_dir=cache_dir,
    )
    model = AutoModelForSequenceClassification.from_pretrained(
        model_name, cache_dir=cache_dir,
    )
    model.eval()

    # 더미 입력
    dummy = tokenizer(
        [["query", "document"]],
        padding=True,
        truncation=True,
        max_length=512,
        return_tensors="pt",
    )

    input_names = ["input_ids", "attention_mask"]
    dynamic_axes = {
        "input_ids": {0: "batch", 1: "seq"},
        "attention_mask": {0: "batch", 1: "seq"},
        "logits": {0: "batch"},
    }
    inputs = (
        dummy["input_ids"],
        dummy["attention_mask"],
    )

    if "token_type_ids" in dummy:
        input_names.append("token_type_ids")
        dynamic_axes["token_type_ids"] = {
            0: "batch", 1: "seq",
        }
        inputs = inputs + (dummy["token_type_ids"],)

    raw_path = output_dir / "model_raw.onnx"
    final_path = output_dir / "model.onnx"

    logger.info("ONNX 내보내기 시작", output=str(raw_path))

    # Windows CP949 인코딩 문제 방지
    os.environ["PYTHONIOENCODING"] = "utf-8"

    with torch.no_grad():
        torch.onnx.export(
            model,
            inputs,
            str(raw_path),
            input_names=input_names,
            output_names=["logits"],
            dynamic_axes=dynamic_axes,
            opset_version=18,
            do_constant_folding=True,
            dynamo=False,  # Legacy exporter (안정적)
        )

    if quantize:
        logger.info("INT8 동적 양자화 적용")
        from onnxruntime.quantization import (
            QuantType,
            quantize_dynamic,
        )

        quantize_dynamic(
            str(raw_path),
            str(final_path),
            weight_type=QuantType.QInt8,
        )
        # 원본 삭제
        raw_path.unlink(missing_ok=True)
    else:
        raw_path.rename(final_path)

    size_mb = final_path.stat().st_size / (1024 * 1024)
    logger.info(
        "ONNX 모델 변환 완료",
        path=str(final_path),
        size_mb=round(size_mb, 1),
        quantized=quantize,
    )

    return final_path


def get_reranker() -> Reranker:
    """Reranker 싱글턴 인스턴스를 반환한다."""
    global _reranker
    if _reranker is None:
        _reranker = Reranker()
    return _reranker


def reset_reranker() -> None:
    """싱글턴 인스턴스를 초기화한다 (테스트 격리용)."""
    global _reranker
    _reranker = None
