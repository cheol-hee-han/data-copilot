# Vector Embedding Search Strategy
## SQL 수행이력 유사도 검색 향상 전략

> **목적**: 
서비스 구동 전에 미리 SQL 수행이력에서 비즈니스 용어 기반 설명을 추론하여 해당 설명을 임베딩하고(별도 독립 배치 프로그램으로 프로젝트 내 개발 필요), 
데이터 추출 요청이 왔을 때 유사한 목적의 SQL 레퍼런스를 유사도 검색으로 조회하도록 기능 제공.
이 때 최대한 의미가 유사한 레퍼런스를 추출하기 위한 다양한 전략을 구현.  

---

## 1. 문제 구조 분석

### 1.1 핵심 특성

이 유스케이스는 일반적인 텍스트 검색과 세 가지 측면에서 근본적으로 다르다.

**① 의미 다형성 (Semantic Polymorphism)**

동일한 비즈니스 개념이 수십 가지 표현으로 존재한다.

```
"영업이익" = "operating income" = "영업익" = "operating profit" = "EBIT (일부 문맥)"
"부서별"   = "사업부별" = "by division" = "by department" = "팀별"
```

키워드 매칭만으로는 절대 해결 불가능하다.

**② 문서-쿼리 비대칭 (Asymmetric Nature)**

| 구분 | 특성 | 예시 |
|------|------|------|
| **문서 (SQL 설명)** | 짧고 명확, 구조적, 비즈니스 용어 정제됨 | "부서별 분기 매출 실적 집계 — 전년 동기 대비 성장률 포함" |
| **쿼리 (사용자 요청)** | 모호하고 다양한 표현, 다국어 혼용 가능 | LLM 정제 후 → "department quarterly revenue / 부서별 분기 매출 현황 / Q-over-Q growth" |

문서와 쿼리를 동일한 방식으로 임베딩하면 안 된다.

**③ 언어 비결정성**

문서가 한국어일 수도, 영어일 수도 있고 향후 결정 예정이다. 따라서 단일 언어 특화 모델을 선택하면 재설계 위험이 있다.

### 1.2 IT 비전문가 사용자 구조

```
[사용자] 모호한 비즈니스 질문
    ↓
[앞단 LLM] 의도 분석 + 쿼리 확장 (유사어, 다국어 표현 생성)
    ↓
[임베딩 모델] 정제된 비즈니스 자연어 → 벡터
    ↓
[Vector DB] 유사 SQL 레퍼런스 검색
```

이 구조에서 임베딩 모델이 받는 입력은 항상 **LLM이 정제한 깔끔한 비즈니스 자연어**이다. SQL/코드 이해력은 불필요하다.

---

## 2. 핵심 설계 원칙

### 원칙 1: 모델보다 데이터 보강이 먼저

FinMTEB 연구(EMNLP 2025)에서 밝혀진 반직관적 발견이 이 유스케이스에 직접 적용된다.

> 금융/비즈니스 도메인 STS 태스크에서 BOW(TF-IDF)가 모든 Dense 임베딩 모델을 뛰어넘는다 (BOW 0.4845 vs Dense 최대 0.4380).

이유는 비즈니스 용어의 반복성과 전문 용어의 고유성 때문이다. 임베딩 모델이 "영업이익 = operating income"을 몰라도, **문서에 두 표현이 모두 있으면 어떤 표현으로 검색해도 히트한다.**

### 원칙 2: Dense + Sparse 하이브리드

| 검색 방식 | 담당 케이스 | 예시 |
|----------|------------|------|
| **Dense (의미 벡터)** | 동의어·패러프레이징 매칭 | "사업부 매출" ↔ "부서별 revenue" |
| **Sparse (키워드)** | 고유 비즈니스 용어 정확 매칭 | "영업이익률" ↔ "영업이익률" |

두 방식을 결합해야 비즈니스 도메인에서 높은 Recall을 달성할 수 있다.

### 원칙 3: 2단계 검색 (Recall → Precision)

임베딩은 Recall을 담당하고 (후보 50개), Reranker가 Precision을 담당한다 (최종 5~10개). 이 역할 분리가 핵심이다.

---

## 3. 최종 추천 조합

### 현재 (언어 미결정) 최적 조합

```
BGE-M3 (Dense + Sparse)
  + LLM 문서 보강 (Document Enrichment)
  + BGE-Reranker-v2-m3 (Cross-Encoder Reranker)
```

### 레이어별 구성

| 레이어 | 모델/방법 | 역할 | 근거 |
|--------|----------|------|------|
| **Layer 0** | LLM (GPT-4o / Claude) | 문서 오프라인 보강 | 동의어·유의어·다국어 표현 생성, 인덱싱 시 1회 수행 |
| **Layer 1** | BGE-M3 | 임베딩 + 인덱싱 | Dense + Sparse 동시 생성, 한/영 100개 언어, 570M / ~2GB |
| **Layer 2** | Qdrant 하이브리드 검색 | Top-50 후보 탐색 | Dense (0.6) + Sparse (0.4) → RRF 합산 |
| **Layer 3** | BGE-Reranker-v2-m3 | Top-50 → Top-5~10 정밀 재순위 | Cross-Encoder, 쿼리-문서 쌍 동시 분석 |

---

## 4. 파이프라인 상세 설계

### 4.1 오프라인: 문서 인덱싱 파이프라인

```
[원본 SQL 설명]
    ↓
[LLM 보강] 동의어 + 영어 표현 + 관련 비즈니스 용어 생성
    ↓
[보강 텍스트 concat] "원문 | 동의어1, 동의어2, 영어표현, ..."
    ↓
[BGE-M3 임베딩] Dense 벡터 + Sparse 가중치 동시 생성
    ↓
[Qdrant 저장] Dense 인덱스 (HNSW) + Sparse 인덱스 (BM25) 동시 저장
```

### 4.2 온라인: 쿼리 검색 파이프라인

```
[사용자 모호한 질문]
    ↓
[LLM 의도 분석 + 쿼리 확장]
  → 한국어 표현 1~2개
  → 영어 표현 1~2개
  → 핵심 비즈니스 용어 추출
    ↓
[BGE-M3 쿼리 임베딩] Dense + Sparse (실시간)
    ↓
[Qdrant 하이브리드 검색]
  Dense 유사도 × 0.6 + BM25 스코어 × 0.4 → RRF → Top-50
    ↓
[BGE-Reranker-v2-m3] Top-50 재채점 → Top-5~10
    ↓
[유사 SQL 레퍼런스 반환]
```

### 4.3 문서 보강 구현 예시

```python
def enrich_sql_description(original: str, llm_client) -> str:
    prompt = f"""
다음 SQL 설명에 대해 동의어·유의어·영어 표현·관련 비즈니스 용어를 생성하세요.
원문: {original}

요구사항:
- 한국어 동의어 2~3개
- 영어 번역 및 유사 표현 2~3개
- 관련 비즈니스/금융 용어

출력: 쉼표 구분 단일 라인
"""
    synonyms = llm_client.generate(prompt)
    return f"{original} | {synonyms}"

# 사용 예시
original = "부서별 분기 매출 실적 집계"
enriched = enrich_sql_description(original, llm)
# → "부서별 분기 매출 실적 집계 | 사업부 분기 매출 현황, 팀별 분기 실적,
#    quarterly revenue by department, sales performance by division,
#    Q-over-Q revenue aggregation, division quarterly performance"

doc_to_embed = enriched
```

### 4.4 BGE-M3 하이브리드 임베딩 구현 예시

```python
from FlagEmbedding import BGEM3FlagModel

model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=True)

# 문서 임베딩 (인덱싱 시)
doc_embeddings = model.encode(
    [enriched_doc],
    return_dense=True,
    return_sparse=True,
    return_colbert_vecs=False  # 메모리 절약 시 False
)

# 쿼리 임베딩 (검색 시, LLM 확장 쿼리)
query_embeddings = model.encode(
    [expanded_query],
    return_dense=True,
    return_sparse=True
)

# 하이브리드 스코어 계산
dense_score  = query_embeddings["dense_vecs"] @ doc_embeddings["dense_vecs"].T
sparse_score = model.compute_lexical_matching_score(
    query_embeddings["lexical_weights"],
    doc_embeddings["lexical_weights"]
)
final_score = 0.6 * dense_score + 0.4 * sparse_score
```

---

## 5. 모델 선택 근거 상세

### BGE-M3를 핵심 임베딩 모델로 선택한 이유

**벤치마크 근거**

- 한국어 RAG 벤치마크 (AutoRAG, Allganize 데이터): Recall@50 = 1.0 달성
- MTEB Multilingual: 다국어 최상위 그룹
- Dense + Sparse + ColBERT 3가지 검색 모드를 단일 모델로 지원하는 유일한 경량 오픈소스

**실용적 근거**

- 570M 파라미터 / ~2GB GPU (CPU 서빙도 가능)
- 앞단 LLM이 GPU를 점유하므로 임베딩은 경량이 유리
- MIT 라이선스, 상업 이용 가능

**Qwen3-Embedding-8B 대신 선택한 이유**

Qwen3-8B는 MTEB Multilingual 1위(70.58), MTEB Code 1위(80.68)이지만, 이 유스케이스에는 Code 이해력이 불필요하다. 또한 ~16GB GPU 요구, 언어 미결정 상태에서 한/영 성능 차이 리스크가 존재한다.

**Fin-E5 대신 선택한 이유**

Fin-E5는 FinMTEB 1위(0.6767)로 금융 도메인 최강이지만 영어 전용이다. 언어가 미결정 상태인 현재 시점에서는 리스크가 있다.

### BGE-Reranker-v2-m3를 Reranker로 선택한 이유

Cross-Encoder 방식으로 쿼리와 문서를 동시에 분석해 임베딩의 구조적 한계를 보완한다. BGE-M3와 동일한 생태계로 한/영 모두 지원한다. Precision@5 향상이 이 유스케이스에서 가장 중요한 지표이다.

---

## 6. 언어 결정 시 업그레이드 경로

### 영어 확정 시 — 임베딩 레이어만 교체

```
문서 임베딩:  BGE-M3  →  Fin-E5 (FinMTEB 1위, 0.6767)
쿼리 임베딩:  BGE-M3  →  e5-mistral-7b-instruct (동일 계열, instruction 지원)
Reranker:    BGE-Reranker-v2-m3 유지
하이브리드:   Dense(0.6) + BM25(0.4) 유지
```

금융/비즈니스 용어 이해 최상위. GPU 7B 모델 2개 필요.

### 한국어 확정 시 — 현 조합 유지 또는 강화

```
기본 조합:   BGE-M3 유지 (한국어 성능 이미 최상위 오픈소스)
강화 옵션:   Upstage solar-embedding 검토 (한국어 특화)
             단, Sparse 미지원으로 하이브리드 구성 불가
문서 보강:   한국어 중심으로 설계 (영어 표현도 포함)
```

---

## 7. 구현 우선순위

성능 향상 ROI 기준 우선순위

```
1순위 (가장 효과적): LLM 기반 문서 보강 (Document Enrichment)
   → 모델 교체 없이 Recall 대폭 향상. 오프라인 1회 비용.

2순위: Dense + Sparse 하이브리드 검색
   → 비즈니스 키워드 정확 매칭 + 의미 유사도 결합.

3순위: BGE-Reranker-v2-m3 추가
   → Recall@50 → Precision@5 변환. 사용자 체감 품질 직접 향상.

4순위 (언어 확정 후): 도메인 특화 파인튜닝 또는 모델 교체
   → 영어 확정 시 Fin-E5, 한국어 확정 시 현 조합 유지.
```

---

## 8. 핵심 요약

> **"임베딩 모델 선택보다 데이터 설계가 먼저다."**
>
> 비즈니스/금융 도메인에서는 어떤 최신 임베딩 모델도 동의어 다형성 문제를 완전히 해결하지 못한다.
> LLM 기반 문서 보강으로 이 한계를 우회하고,
> BGE-M3의 Dense+Sparse 하이브리드로 키워드와 의미를 동시에 커버하며,
> Reranker로 최종 정밀도를 확보하는 3단 구조가
> 현재 이 유스케이스에서 가장 현실적이고 성능 상한선이 높은 전략이다.

| 구분 | 선택 |
|------|------|
| 임베딩 모델 | BGE-M3 (BAAI/bge-m3) |
| 검색 방식 | Dense (0.6) + Sparse/BM25 (0.4) → RRF |
| Reranker | BGE-Reranker-v2-m3 |
| 문서 보강 | LLM 기반 동의어·다국어 표현 생성 |
| Vector DB | Qdrant (HNSW + Sparse 인덱스) |
| 언어 전략 | 현재 한/영 모두 대응, 확정 시 임베딩 레이어만 교체 |

---

*작성 기준: 2026년 3월*
*참조 벤치마크: FinMTEB (EMNLP 2025), MTEB Multilingual (MMTEB 2025), AutoRAG 한국어 벤치마크*
