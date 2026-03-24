# BGE-Reranker-v2-m3 CPU 병렬화 최적화 전략 리서치

- **작성일**: 2026-03-21
- **작성자**: 기술 리서치 애널리스트 (Claude)
- **주제**: GPU 없는 폐쇄망 환경에서 BGE-Reranker-v2-m3 CPU 추론 최적화
- **컨텍스트**: FlagEmbedding FlagReranker, 50쌍 Qdrant 하이브리드 검색 후 리랭킹, Python 3.12, LangGraph async 아키텍처

---

## Executive Summary

폐쇄망 CPU 전용 환경에서 bge-reranker-v2-m3(~568M params)의 50쌍 리랭킹 레이턴시를
최적화하는 방법은 **ONNX Runtime + INT8 동적 양자화** 조합이 가장 높은 투자 대비 효과를 제공한다.

- **기대 개선폭**: ONNX O3 + INT8 양자화 기준 vanilla PyTorch 대비 **3~4x 속도 향상**
- **핵심 전제**: FlagEmbedding의 `FlagReranker`는 ONNX를 지원하지 않으므로,
  `optimum.onnxruntime.ORTModelForSequenceClassification` 기반으로 직접 래핑 필요
- **권고 우선순위**: ONNX 양자화 → torch.set_num_threads 튜닝 → 입력 정렬 → 배치분할(최후 수단)

---

## 1. 현황 분석: FlagEmbedding FlagReranker의 한계

### 1.1 FlagReranker 소스코드 분석

`BaseReranker.compute_score_single_gpu` 구현을 분석한 결과:

| 파라미터 | 기본값 | 비고 |
|---|---|---|
| `batch_size` | 128 | `compute_score()` 호출 시 변경 가능 |
| `max_length` | 512 | 토큰 최대 길이 |
| `use_fp16` | False | CPU에서는 효과 없음 (GPU 전용) |
| `normalize` | False | sigmoid 적용 여부 |

**핵심 발견**: `FlagReranker`는 HuggingFace `AutoModelForSequenceClassification`을 사용하며,
ONNX/양자화 지원이 **내장되어 있지 않다**. `use_fp16=True`는 GPU에서만 유효하므로
CPU 전용 환경에서는 무의미하다.

**출처**: [FlagEmbedding inference/reranker/encoder_only/base.py](https://bge-model.com/_modules/FlagEmbedding/inference/reranker/encoder_only/base.html)

### 1.2 내장 경량화 옵션: LightWeightFlagLLMReranker

FlagEmbedding에는 `LightWeightFlagLLMReranker`가 존재하나, 이는 **bge-reranker-v2.5-gemma2-lightweight** 전용으로:
- Gemma-2-9B 기반 (9B 파라미터) — bge-reranker-v2-m3보다 **16배 큰** 모델
- `cutoff_layers=[28]`, `compress_ratio=2` 등으로 레이어 조기종료 및 토큰 압축 지원
- CPU 전용 폐쇄망 환경에서는 오히려 더 느림 → **해당 없음**

---

## 2. ONNX Runtime 백엔드

### 2.1 BAAI 공식 ONNX 모델 제공 여부

BAAI는 `BAAI/bge-reranker-v2-m3` 리포지토리에서 ONNX를 **직접 제공하지 않는다**.
그러나 커뮤니티 변환 모델이 다수 존재한다:

| 모델 리포지토리 | 최적화 수준 | 특이사항 |
|---|---|---|
| `onnx-community/bge-reranker-v2-m3-ONNX` | 양자화 포함 | Transformers.js v3 대상, 8.33GB |
| `hooman650/bge-reranker-v2-m3-onnx-o4` | O4 (혼합 정밀도) | CPU에서 O4는 GPU 전용이므로 주의 |

**중요 주의사항**: O4 최적화(`--optimize O4`)는 fp16 혼합정밀도로 **GPU 전용**이다.
CPU 환경에서는 반드시 `--optimize O3`까지만 사용해야 한다.

직접 변환 명령:
```bash
# 설치
pip install optimum[exporters] onnxruntime

# O3 수준 ONNX 내보내기 (CPU 전용)
optimum-cli export onnx \
  --model BAAI/bge-reranker-v2-m3 \
  --optimize O3 \
  --opset 13 \
  --batch_size 1 \
  --sequence_length 512 \
  ./bge-reranker-v2-m3-onnx-o3/
```

최적화 수준별 차이:
- **O1**: 기본 ONNX 그래프 최적화
- **O2**: O1 + Transformer 특화 연산 융합 (LayerNorm, Attention 등)
- **O3**: O2 + GELU 근사 (CPU 권장 최고 수준)
- **O4**: O3 + fp16 혼합정밀도 (**GPU 전용**)

**출처**:
- [bge-reranker-v2-m3-onnx-o3-cpu (PromptLayer)](https://www.promptlayer.com/models/bge-reranker-v2-m3-onnx-o3-cpu)
- [HuggingFace Optimum ONNX Export Guide](https://huggingface.co/docs/optimum/en/exporters/onnx/usage_guides/export_a_model)

### 2.2 ONNX Runtime CPU 멀티스레딩 설정

ONNX Runtime에는 두 가지 스레드 풀이 있다:

| 파라미터 | 역할 | 권장값 |
|---|---|---|
| `intra_op_num_threads` | 단일 연산 내부 병렬화 (GEMM 등) | 물리 코어 수 |
| `inter_op_num_threads` | 연산 간 병렬화 | 1 (Sequential 모드에서) |
| `execution_mode` | 실행 모드 | `ORT_SEQUENTIAL` (Transformer에 유리) |

**Transformer 모델 권장 설정**:
```python
import onnxruntime as ort

opts = ort.SessionOptions()
opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
opts.intra_op_num_threads = os.cpu_count()  # 물리 코어 수
opts.inter_op_num_threads = 1
opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
opts.enable_mem_pattern = True
opts.enable_mem_reuse = True

session = ort.InferenceSession(model_path, sess_options=opts)
```

**중요 발견**: `ORT_PARALLEL` 모드는 비선형 그래프 구조에서만 유리하다.
Transformer의 선형 시퀀스 구조에서는 `ORT_SEQUENTIAL`이 일관되게 빠르다.

**출처**: [ONNX Runtime Thread Management](https://onnxruntime.ai/docs/performance/tune-performance/threading.html)

### 2.3 INT8 동적 양자화

Microsoft ONNX Runtime 공식 블로그의 Intel CPU BERT 벤치마크:
- BERT 12-layer: **2.9x 속도 향상** (Intel DL Boost VNNI, INT8)
- DistilBERT: **3.38x 속도 향상**
- BGE-M3 임베딩 모델 기준: **3x 속도 향상**, 정확도 손실 0.15%(sparse) / 0.65%(dense)
- 모델 크기 2272MB → 571MB (75% 감소)

```python
from optimum.onnxruntime import ORTModelForSequenceClassification
from optimum.onnxruntime.configuration import AutoQuantizationConfig
from optimum.onnxruntime import ORTQuantizer

# 동적 양자화 (캘리브레이션 데이터 불필요)
model = ORTModelForSequenceClassification.from_pretrained(
    "bge-reranker-v2-m3-onnx-o3",
    export=False
)
quantizer = ORTQuantizer.from_pretrained(model)
dqconfig = AutoQuantizationConfig.avx512_vnni(is_static=False, per_channel=False)
quantizer.quantize(save_dir="bge-reranker-v2-m3-onnx-int8", quantization_config=dqconfig)
```

**AVX 지원 수준별 양자화 설정**:
- `arm64`: ARM 서버
- `avx2`: 구형 Intel/AMD
- `avx512`: 최신 Intel Xeon
- `avx512_vnni`: Intel Cascade Lake 이후 (최고 성능)

**출처**:
- [Optimizing BERT for Intel CPU - Microsoft OSS Blog](https://opensource.microsoft.com/blog/2021/03/01/optimizing-bert-model-for-intel-cpu-cores-using-onnx-runtime-default-execution-provider)
- [Sentence Transformers Cross Encoder Efficiency](https://sbert.net/docs/cross_encoder/usage/efficiency.html)

### 2.4 Sentence Transformers ONNX 백엔드 활용

sentence-transformers v4.1+에서 CrossEncoder의 ONNX 백엔드를 직접 지원한다.
bge-reranker-v2-m3는 `AutoModelForSequenceClassification` 기반이므로 호환된다:

```python
from sentence_transformers import CrossEncoder

# ONNX 백엔드로 직접 로드
model = CrossEncoder(
    "BAAI/bge-reranker-v2-m3",
    backend="onnx",
    model_kwargs={
        "file_name": "model_optimized_quantized.onnx",
        "provider": "CPUExecutionProvider",
        "session_options": opts,  # 위의 SessionOptions 재사용
    }
)
scores = model.predict(pairs, batch_size=16)
```

단, FlagEmbedding과 sentence-transformers는 내부 토크나이저 처리가 다를 수 있으므로
점수 스케일 검증 필요.

---

## 3. PyTorch CPU 병렬성

### 3.1 torch.set_num_threads() 효과

PyTorch는 기본적으로 모든 논리 코어를 사용하도록 설정되지만,
하이퍼스레딩 활성화 환경에서는 오히려 성능이 저하되는 사례가 확인되었다.

```python
import torch
import os

# 권장: 물리 코어 수로 제한 (논리 코어 수의 절반)
physical_cores = os.cpu_count() // 2
torch.set_num_threads(physical_cores)
torch.set_num_interop_threads(1)  # Transformer는 순차 실행이 유리
```

**기대 효과**: 단독으로는 1.1~1.3x 수준으로 제한적.
ONNX 전환 없이 PyTorch를 유지할 경우의 최소 최적화 조치.

**주의**: 72코어 서버에서 모든 코어 사용 시 오히려 성능 저하 보고됨
(캐시 경합, NUMA 오버헤드).

**출처**: [PyTorch issue #93247 - set_num_threads not accelerating](https://github.com/pytorch/pytorch/issues/93247)

### 3.2 torch.compile (PyTorch 2.x)

2025년 3월 실측 벤치마크 (GPU 기준, cross-encoder):

| 구성 | 평균 시간 | 속도 배수 |
|---|---|---|
| Baseline (정렬 없음) | 0.3566s | 1.0x |
| Baseline + 입력정렬 | 0.3245s | 1.10x |
| Flash Attention + 정렬 | 0.2658s | 1.34x |
| torch.compile (정렬 없음) | 0.2595s | 1.38x |
| **torch.compile + 입력정렬** | **0.2089s** | **1.71x** |

CPU에서 torch.compile의 특이사항:
- 첫 실행 시 **수십 초 컴파일 웜업** 필요 (콜드스타트 문제)
- `dynamic=True` 필수 (입력 길이 가변)
- Flash Attention과 함께 사용 시 **비호환** (동적 텐서 형상 문제)
- Inductor 백엔드가 CPU에서 AVX2/AVX-512 벡터화 자동 적용

```python
import torch

model = model_loaded  # HuggingFace AutoModelForSequenceClassification
model.eval()
compiled_model = torch.compile(
    model,
    backend="inductor",
    mode="max-autotune",
    dynamic=True
)

# 버킷 패딩: 16의 배수로 패딩하여 컴파일 그래프 재추적 최소화
BUCKETS = list(range(16, 528, 16))
```

**출처**: [Faster Cross-Encoder Inference with torch.compile - Shreyansh Singh (2025-03)](https://shreyansh26.github.io/post/2025-03-02_cross-encoder-inference-torch-compile/)

---

## 4. 배치 수준 병렬화 전략

### 4.1 GIL과 ONNX Runtime

Python 3.12에서 ONNX Runtime의 C++ 추론 커널은 **GIL을 해제**하고 실행된다.
따라서 `ThreadPoolExecutor`로 50쌍을 N개 청크로 분할해 동시 제출이 이론상 가능하다.

**그러나 실제로는 역효과 가능성 높음**:
- ONNX Runtime 세션 자체가 이미 `intra_op_num_threads`로 내부 병렬화 수행 중
- ThreadPool으로 추가 분할 시 → 스레드 과구독(oversubscription) 발생
- 메모리 대역폭 포화로 인한 성능 저하 실측 보고 있음

**출처**: [ONNX models with Python multiprocessing issue #10786](https://github.com/microsoft/onnxruntime/issues/10786)

### 4.2 ProcessPoolExecutor 병렬화

50쌍을 N 프로세스로 분할하는 방식:

| 구성 | 메모리 오버헤드 | 속도 | 권장 여부 |
|---|---|---|---|
| 1 프로세스 (기본) | ~2.3GB | 기준 | 권장 |
| 4 프로세스 (각 12쌍) | ~9.2GB (+4x) | 이론 4x, 실제 1.2~1.5x | 비권장 |
| ThreadPoolExecutor (4 스레드) | +22MB | 1.0~1.2x | 제한적 |

**ProcessPoolExecutor 문제점**:
- 각 worker 프로세스가 모델 가중치(~2.3GB)를 개별 로드 → 메모리 급증
- Pickle 직렬화 오버헤드 (50쌍은 너무 작은 페이로드)
- 실측에서 순차 실행보다 느린 사례 다수

**권장하지 않는 이유**: 50쌍은 ProcessPoolExecutor의 IPC 오버헤드를 정당화하기에
너무 작은 작업이다. 1,000쌍 이상에서만 고려 대상.

**출처**: [Mitigating GIL Bottlenecks in Edge AI (arxiv 2601.10582)](https://arxiv.org/html/2601.10582v3)

### 4.3 입력 정렬 (Input Sorting) — 고효율 무비용 최적화

FlagEmbedding의 `BaseReranker`는 이미 길이 기준 정렬을 내부 구현하고 있다.
그러나 ONNX/torch.compile 직접 사용 시 이 혜택이 사라지므로 직접 구현 필요:

```python
def rerank_with_sorting(
    model: ORTModelForSequenceClassification,
    tokenizer: AutoTokenizer,
    pairs: list[tuple[str, str]],
    batch_size: int = 16
) -> list[float]:
    """길이 정렬 + 버킷 패딩으로 패딩 오버헤드 최소화"""
    # 길이 기준 정렬 (원래 인덱스 보존)
    indexed_pairs = sorted(enumerate(pairs), key=lambda x: len(x[1][0]) + len(x[1][1]))
    indices, sorted_pairs = zip(*indexed_pairs)

    # 버킷 패딩 (16의 배수)
    BUCKETS = list(range(16, 528, 16))
    all_scores = []

    for i in range(0, len(sorted_pairs), batch_size):
        batch = list(sorted_pairs[i:i+batch_size])
        max_len = max(len(p[0]) + len(p[1]) for p in batch)
        bucket_len = next(b for b in BUCKETS if b >= max_len // 4)  # 대략적 토큰 추정

        inputs = tokenizer(
            batch, padding="max_length", truncation=True,
            max_length=min(bucket_len, 512), return_tensors="pt"
        )
        with torch.no_grad():
            scores = model(**inputs).logits.view(-1).float().tolist()
        all_scores.extend(scores)

    # 원래 순서 복원
    result = [0.0] * len(pairs)
    for orig_idx, score in zip(indices, all_scores):
        result[orig_idx] = score
    return result
```

**기대 효과**: 단독으로 **1.1~1.25x** (torch.compile과 결합 시 최대 1.71x)

---

## 5. 사전 필터링 / 조기 종료

### 5.1 벡터 검색 점수 기반 임계값 필터링

Qdrant 하이브리드 검색은 각 청크에 유사도 점수를 부여한다.
이 점수를 활용해 리랭커 입력을 사전 축소할 수 있다:

```python
async def adaptive_rerank(
    query: str,
    qdrant_results: list[ScoredPoint],
    reranker: ORTModelForSequenceClassification,
    min_score_threshold: float = 0.3,
    max_candidates: int = 30,
) -> list[ScoredPoint]:
    """
    Qdrant 점수 기반 적응형 후보 필터링.
    50개 → 20~30개로 줄여 리랭킹 시간 단축.
    """
    # 1단계: 임계값 필터 (전형적인 하이브리드 점수 분포 기준)
    candidates = [r for r in qdrant_results if r.score >= min_score_threshold]

    # 2단계: 상위 N개 제한
    candidates = sorted(candidates, key=lambda x: x.score, reverse=True)[:max_candidates]

    # 3단계: 점수 분포 분석 (급격한 점수 하락 감지)
    if len(candidates) > 10:
        scores = [c.score for c in candidates]
        score_diffs = [scores[i] - scores[i+1] for i in range(len(scores)-1)]
        # 점수 차이가 갑자기 2배 이상 벌어지는 지점에서 컷
        for cutoff_idx, diff in enumerate(score_diffs):
            if cutoff_idx > 5 and diff > 2 * (sum(score_diffs[:cutoff_idx]) / cutoff_idx):
                candidates = candidates[:cutoff_idx+1]
                break

    # 리랭킹 실행
    pairs = [(query, c.payload["text"]) for c in candidates]
    scores = reranker_score(pairs)

    # 점수 병합 및 재정렬
    for c, s in zip(candidates, scores):
        c.score = s
    return sorted(candidates, key=lambda x: x.score, reverse=True)
```

**기대 효과**: 50쌍 → 20~30쌍으로 줄이면 **1.7~2.5x 처리 속도 향상**
(레이턴시가 후보 수에 선형 비례하므로)

**주의**: 금융 도메인처럼 전문 용어가 많은 경우, Qdrant 벡터 점수의 신뢰도가
일반 도메인보다 낮을 수 있다. 임계값을 보수적으로 설정하거나(0.2~0.25)
최소 후보 수(minimum 15개)를 보장하는 로직 추가 권장.

**출처**: [Reranking for Better Search - Qdrant](https://qdrant.tech/documentation/search-precision/reranking-semantic-search/)

---

## 6. 경량화 대안 모델

### 6.1 bge-reranker-v2-m3 vs 더 작은 대안

| 모델 | 파라미터 | BEIR nDCG@10 | 한국어 지원 | CPU 레이턴시(50쌍 추정) |
|---|---|---|---|---|
| bge-reranker-v2-m3 | 568M | ~51.8 | 우수 (다국어) | ~400~600ms |
| bge-reranker-large | 560M | ~53.8 (+2pt) | 제한적 | ~400~600ms |
| **bge-reranker-v2-m3** (ONNX INT8) | 568M | ~51.0 (-0.8pt) | 우수 | **~120~180ms** |
| dragonkue/bge-reranker-v2-m3-ko | 568M | 한국어 특화 파인튜닝 | 최우수 | ~400~600ms |
| bge-reranker-base | 278M | ~49.0 | 제한적 | ~200~300ms |
| ms-marco-MiniLM-L-6-v2 | 22M | ~43.0 | 영어만 | ~20~50ms |

**출처**: [Speed Showdown: Reranker on CPU/GPU/TPU (Medium)](https://medium.com/@xiweizhou/speed-showdown-reranker-1f7987400077)

### 6.2 Korean-specific: dragonkue/bge-reranker-v2-m3-ko

`dragonkue/bge-reranker-v2-m3-ko`는 bge-reranker-v2-m3를 한국어 데이터셋으로
추가 파인튜닝한 모델이다. 금융 도메인 한국어에서 기본 다국어 모델보다 높은 성능을
기대할 수 있으나, 실측 금융 도메인 벤치마크는 확인되지 않았다.

**권고**: 폐쇄망 배포 전 금융 업무 쿼리 샘플로 직접 A/B 테스트 권장.

**출처**: [dragonkue/bge-reranker-v2-m3-ko (HuggingFace)](https://huggingface.co/dragonkue/bge-reranker-v2-m3-ko)

### 6.3 기각된 대안

**bge-reranker-v2.5-gemma2-lightweight**:
- 9B 파라미터 — CPU 전용 환경에서 실용적이지 않음
- `cutoff_layers`와 `compress_ratio`로 부분 경감 가능하나, 기본 모델 크기가 너무 큼
- **기각 이유**: 폐쇄망 CPU 환경에서 단독 배포 불가

**ms-marco-MiniLM-L-6-v2**:
- 영어 전용, 한국어 성능 미검증
- **기각 이유**: 은행 한국어 쿼리 지원 불가

---

## 7. LangGraph async 아키텍처 통합 패턴

### 7.1 asyncio와 CPU 바운드 작업 통합

LangGraph 노드는 async 함수이며, ONNX 추론은 CPU 바운드 작업이다.
asyncio 이벤트 루프를 블록하지 않으려면 `run_in_executor` 패턴 사용:

```python
import asyncio
from functools import partial
from concurrent.futures import ThreadPoolExecutor

# 전역 스레드 풀 (모델 1개, 스레드 1개가 최적)
_executor = ThreadPoolExecutor(max_workers=1)

async def rerank_node(state: GraphState) -> GraphState:
    """LangGraph 노드: CPU 추론을 별도 스레드로 위임"""
    loop = asyncio.get_event_loop()

    pairs = [(state["query"], doc.page_content) for doc in state["candidates"]]

    # CPU 바운드 추론을 executor로 위임 (이벤트 루프 비블록)
    scores = await loop.run_in_executor(
        _executor,
        partial(rerank_with_sorting, model, tokenizer, pairs, batch_size=16)
    )

    # 점수 기준 재정렬
    ranked = sorted(zip(scores, state["candidates"]), reverse=True)
    state["reranked"] = [doc for _, doc in ranked[:10]]
    return state
```

**핵심**: `max_workers=1`로 설정하는 이유는 ONNX Runtime이 이미 내부적으로
`intra_op_num_threads`를 통해 모든 CPU 코어를 사용하기 때문이다.
추가 worker를 만들면 스레드 과구독이 발생한다.

---

## 8. 종합 권고안

### 8.1 구현 로드맵 (우선순위 순)

| 단계 | 최적화 방법 | 예상 속도 향상 | 구현 복잡도 | 품질 손실 |
|---|---|---|---|---|
| **1단계** | ONNX O3 변환 | 1.5~2.0x | 낮음 | 없음 |
| **2단계** | INT8 동적 양자화 | 누적 2.5~3.5x | 낮음 | <1% |
| **3단계** | 입력 길이 정렬 | 누적 2.8~4.0x | 매우 낮음 | 없음 |
| **4단계** | 사전 필터링 (50→25쌍) | 누적 4~6x | 중간 | 소폭 (모니터링 필요) |
| 선택적 | torch.compile | +1.3~1.7x 추가 | 높음 (웜업 이슈) | 없음 |

### 8.2 목표 레이턴시

현재 추정 기준값 (vanilla PyTorch, 50쌍, 8코어 CPU):
- **약 400~800ms** (시퀀스 길이, 하드웨어에 따라 편차 큼)

1~3단계 적용 후 목표:
- **약 100~200ms** (ONNX INT8 + 정렬)

4단계 추가 시:
- **약 60~120ms** (후보 25쌍 기준)

### 8.3 배포 고려사항 (폐쇄망)

1. **오프라인 ONNX 변환**: 온라인 환경에서 변환 후 `.onnx` 파일 반입
2. **필요 패키지**: `onnxruntime`, `optimum` (transformers 의존성 확인)
3. **CPU 아키텍처 확인**: AVX512_VNNI 지원 여부에 따라 양자화 설정 변경
4. **토크나이저**: `sentencepiece`, `tokenizers` 라이브러리 오프라인 설치 필요

---

## 9. 참고 문헌

### Tier 1 논문

1. **"Enhancing Q&A Text Retrieval with Ranking Models"** (arXiv:2409.07691, 2024)
   - 리랭킹 모델 품질-레이턴시 트레이드오프 체계적 분석
   - [https://arxiv.org/html/2409.07691v1](https://arxiv.org/html/2409.07691v1)

2. **"Mitigating GIL Bottlenecks in Edge AI Systems"** (arXiv:2601.10582, 2026)
   - Python GIL과 AI 추론 멀티프로세싱 상세 분석
   - [https://arxiv.org/html/2601.10582v3](https://arxiv.org/html/2601.10582v3)

3. **"Accelerating Deep Learning Inference: A Comparative Analysis"** (Electronics, MDPI 14(15), 2025)
   - ONNX Runtime vs PyTorch 추론 속도 비교 체계적 분석
   - [https://www.mdpi.com/2079-9292/14/15/2977](https://www.mdpi.com/2079-9292/14/15/2977)

### 구현 사례 및 기술 문서

- [FlagEmbedding GitHub - FlagOpen/FlagEmbedding](https://github.com/FlagOpen/FlagEmbedding)
- [BAAI/bge-reranker-v2-m3 HuggingFace](https://huggingface.co/BAAI/bge-reranker-v2-m3)
- [onnx-community/bge-reranker-v2-m3-ONNX](https://huggingface.co/onnx-community/bge-reranker-v2-m3-ONNX/tree/a3046abee880d6e78833e4e885939754355156bd)
- [hooman650/bge-reranker-v2-m3-onnx-o4](https://huggingface.co/hooman650/bge-reranker-v2-m3-onnx-o4)
- [Sentence Transformers Cross Encoder Efficiency Docs](https://sbert.net/docs/cross_encoder/usage/efficiency.html)
- [ONNX Runtime Thread Management](https://onnxruntime.ai/docs/performance/tune-performance/threading.html)
- [Faster Cross-Encoder Inference: torch.compile (2025-03)](https://shreyansh26.github.io/post/2025-03-02_cross-encoder-inference-torch-compile/)
- [HuggingFace Optimum ONNX Optimization](https://huggingface.co/docs/optimum-onnx/onnxruntime/usage_guides/optimization)
- [Optimizing BERT on Intel CPU - Microsoft OSS Blog](https://opensource.microsoft.com/blog/2021/03/01/optimizing-bert-model-for-intel-cpu-cores-using-onnx-runtime-default-execution-provider)
- [Speed Showdown: Reranker CPU/GPU/TPU Benchmark](https://medium.com/@xiweizhou/speed-showdown-reranker-1f7987400077)
- [dragonkue/bge-reranker-v2-m3-ko](https://huggingface.co/dragonkue/bge-reranker-v2-m3-ko)
- [Qdrant Cross-Encoder GSOC Integration](https://qdrant.tech/articles/cross-encoder-integration-gsoc/)
