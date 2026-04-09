# SQL 수행이력 유사도 검색 향상 — 통합 아키텍처

> **버전**: 1.2
> **최종 수정**: 2026-04-01
> **기반 전략**: `docs/strategy-proposals/embedding-search-strategy.md`
> **통합 대상**: data-copilot LangGraph 파이프라인
> **작성일**: 2026-03-21

---

## 1. 현황 분석 및 Gap

### 현재 아키텍처

```
[사용자 질의]
  → query_normalizer 노드 (8-Slot 정규화, services/query_normalizer.py 위임)
  → context_retriever 노드 (reason 계층, 도구 기반 병렬 수집)
     ├── ES table_meta     ← es_table_query
     ├── ES report_sql     ← es_report_query
     ├── History DB (ILIKE) ← history_db_query (키워드 기반)
     ├── Qdrant biz_manual ← qdrant_query (Dense-only, MiniLM 384-dim)
     └── ES code_meta      ← 전체 로드
```

### 식별된 Gap

| # | Gap | 영향 |
|---|-----|------|
| 1 | `sql_history` 컬렉션이 Qdrant에 존재하나 커넥터에서 미사용 | 10,000건 임베딩 데이터 사장 |
| 2 | 과거 SQL 검색이 PostgreSQL ILIKE 전용 | 의미 유사 쿼리 검색 불가 ("매출 현황" ↔ "revenue summary") |
| 3 | Dense-only 벡터 검색 (MiniLM 384-dim) | 비즈니스 키워드 정확 매칭 약함, Sparse 부재 |
| 4 | NormalizedQuery.search_keywords가 단순 문자열 | 구조화된 8-Slot 정보가 벡터 검색에 충분히 활용되지 않음 |
| 5 | Reranker 부재 | Recall → Precision 변환 병목 |
| 6 | 문서 보강 없이 원본 description만 임베딩 | 동의어·다국어 검색 한계 |

---

## 2. 통합 아키텍처

### 2.1 목표 아키텍처

```
[사용자 질의]
  → query_normalizer 노드 (8-Slot 정규화, services/query_normalizer.py 위임)
  → reasoning_preparer 노드 (규칙 기반 가설 생성·탐색 계획)
  → context_retriever 노드 (도구 기반 병렬 수집)
     ├── ES table_meta
     ├── ES report_sql
     ├── History DB (ILIKE)        ← 기존 유지 (키워드 매칭 보완)
     ├── Qdrant biz_manual         ← BGE-M3 Dense
     ├── ES code_meta
     └── ★ Qdrant sql_history     ← BGE-M3 Hybrid (Dense 0.6 + Sparse 0.4)
           → Top-50 후보
           → ★ BGE-Reranker-v2-m3 → Top-5~10
  → sql_generator 노드 (보강된 컨텍스트로 SQL 생성)
```

### 2.2 레이어 구성

| 레이어 | 컴포넌트 | 역할 | 파일 |
|--------|---------|------|------|
| **L-1: 오프라인** | Document Enrichment | LLM 기반 동의어·다국어 보강 | `devtools/scripts/enrich_sql_history.py` |
| **L0: 쿼리 합성** | NormalizedQuery → 벡터 쿼리 | 구조화된 슬롯에서 비즈니스 목적 문장 합성 | `src/services/query_normalizer.py` (합성 로직 포함 예정) |
| **L1: 임베딩** | QdrantConnector 내장 (BGE-M3) | Dense(1024-dim) + Sparse 동시 생성 | `src/connectors/impl/qdrant_connector.py` |
| **L2: 하이브리드 검색** | QdrantConnector | Dense(0.6) + Sparse(0.4) → RRF | `src/connectors/impl/qdrant_connector.py` |
| **L3: 재순위** | Reranker (BGE-Reranker-v2-m3) | Cross-Encoder Top-50 → Top-5~10 | `src/connectors/impl/reranker.py` |

### 2.3 모델 스택

| 용도 | 모델 | 크기 | 특성 |
|------|------|------|------|
| **임베딩** | BAAI/bge-m3 | 570M / ~2GB | Dense(1024) + Sparse + ColBERT, 100개 언어 |
| **재순위** | BAAI/bge-reranker-v2-m3 | ~560M / ~2GB | Cross-Encoder, 한/영 모두 지원 |
| **문서 보강** | Claude / GPT-4o (온라인) | - | 오프라인 1회 수행, 동의어·다국어 표현 생성 |

> BGE-M3는 폐쇄망에서도 로컬 모델 파일로 바로 사용 가능하다.
> fastembed 의존성을 제거하고 FlagEmbedding(BGE-M3)으로 전면 전환한다.

---

## 3. 핵심 설계: NormalizedQuery → sql_history 벡터 검색 쿼리 합성

### 3.1 문제

sql_history의 description은 비즈니스 목적 문장이다:
```
"부서별 분기 매출 실적 집계 — 전년 동기 대비 성장률 포함"
"개인 고객의 연령대별, 성별 인원수 분포"
"대출유형별 대출건수, 총잔액, 평균금리"
```

사용자 입력은 구어체다:
```
"이번 달 지점별 대출 잔액 좀 뽑아줘"
```

이 두 공간의 거리를 좁히는 것이 핵심이다.

### 3.2 합성 전략

NormalizedQuery의 구조화된 슬롯을 활용하여 description과 동일한 형식의 벡터 검색 쿼리를 합성한다.

```
NormalizedQuery 슬롯 예시:
  rewritten_query = "이번 달 지점별 대출 잔액 조회"
  intent.primary  = "AGGREGATE"
  entities        = [{ term: "대출", normalized_term: "여신" }]
  measures        = [{ term: "잔액", normalized_term: "대출잔액" }]
  dimensions      = [{ term: "지점", role: "GROUP" }]
  time            = { base_period: { resolve: "THIS_MONTH" } }

합성 결과 (sql_history 벡터 검색 쿼리):
  "이번 달 지점별 대출 잔액 조회 집계 여신 대출잔액 지점별"
```

### 3.3 합성 규칙

| 순서 | 슬롯 | 합성 기여 | 이유 |
|------|------|----------|------|
| 1 | `rewritten_query` | 기본 텍스트 | LLM이 정제한 깔끔한 비즈니스 표현, description과 가장 유사한 형태 |
| 2 | `intent.primary` | 동작 유형 힌트 ("집계", "비교", "추이") | description의 핵심 목적어와 매칭 |
| 3 | `entities[].normalized_term` | 도메인 엔티티 보강 | "대출" → "여신" 동의어 커버리지 확대 |
| 4 | `measures[].term/normalized_term` | 지표 명칭 | description에 항상 포함되는 핵심 용어 |
| 5 | `dimensions[].term` | "~별" 분류축 | "부서별", "지점별" 패턴은 description의 대표 구조 |
| 6 | `search_keywords.vector_search` | 보충 | LLM이 직접 생성한 벡터 검색 텍스트, 위 합성과 중복되지 않는 부분만 추가 |

### 3.4 기존 vector_search 필드와의 관계

기존 `SearchKeywords.vector_search`는 biz_manual (업무 매뉴얼) 검색에 최적화되어 있다.
sql_history 검색은 목적과 형식이 다르므로 별도 필드 `sql_history_search`를 추가한다.

| 속성 | `vector_search` (기존) | `sql_history_search` (신규) |
|------|----------------------|---------------------------|
| 대상 | biz_manual 컬렉션 | sql_history 컬렉션 |
| 생성 | LLM Phase 1 (자유 형식) | 규칙 기반 슬롯 합성 + vector_search 보강 |
| 형식 | 업무 규정·절차 질의 형태 | SQL description과 동형의 비즈니스 목적 문장 |

---

## 4. 하이브리드 검색 상세

### 4.1 Qdrant 컬렉션 스키마 (sql_history)

```
컬렉션: sql_history
벡터:
  - "dense": BGE-M3 Dense (1024-dim, HNSW, Cosine)
  - "sparse": BGE-M3 Sparse (가변 차원, Sparse Index)
페이로드:
  - sql: str           (원본 SQL)
  - description: str   (원본 비즈니스 설명)
  - enriched: str      (보강된 설명, 임베딩 대상)
  - tables: list[str]  (사용 테이블)
  - domain: str        (도메인 코드)
```

### 4.2 검색 흐름

```python
# 1. 쿼리 임베딩 (BGE-M3)
query_dense, query_sparse = search_query_embedder.encode(sql_history_query)

# 2. Qdrant 하이브리드 검색 (RRF)
results = qdrant.query(
    prefetch=[
        Prefetch(query=query_dense, using="dense", limit=50),
        Prefetch(query=query_sparse, using="sparse", limit=50),
    ],
    query=FusionQuery(fusion=Fusion.RRF),  # Reciprocal Rank Fusion
    limit=50,
)

# 3. Reranker (Cross-Encoder)
reranked = reranker.rerank(
    query=sql_history_query,
    documents=[r.payload["description"] for r in results],
    top_k=10,
)
```

### 4.3 가중치 설계

| 검색 방식 | 가중치 | 담당 케이스 |
|----------|--------|------------|
| Dense (의미 벡터) | RRF 동등 기여 | 동의어·패러프레이징 ("사업부 매출" ↔ "부서별 revenue") |
| Sparse (키워드) | RRF 동등 기여 | 고유 비즈니스 용어 정확 매칭 ("영업이익률" ↔ "영업이익률") |

> Qdrant의 RRF는 별도 가중치 없이 순위 기반 융합을 수행한다.
> Dense/Sparse 각각의 prefetch limit으로 후보풀 크기를 제어한다.

---

## 5. History DB (ILIKE) vs Qdrant sql_history 공존

| 소스 | 검색 방식 | 강점 | 약점 |
|------|----------|------|------|
| History DB | ILIKE 키워드 | 정확한 테이블명/컬럼명 매칭 | 의미 유사 검색 불가 |
| Qdrant sql_history | 벡터 유사도 | 의미·동의어·다국어 매칭 | 키워드 정밀도 낮음 |

**결론**: 두 소스 모두 유지하며 결과를 병합한다.
- History DB: 테이블명·컬럼명이 직접 언급된 경우 강점
- Qdrant: 비즈니스 의도가 유사한 경우 강점
- 중복 SQL은 `context_retriever` 노드에서 dedup 처리

---

## 6. 오프라인 문서 보강 (구현 우선순위 1위)

### 6.1 전략

전략 문서에서 밝힌 바와 같이, 모델 교체보다 데이터 보강이 먼저다.

```
[원본 SQL description]
  "부서별 분기 매출 실적 집계"
    ↓
[LLM 보강] 동의어 + 영어 표현 + 관련 비즈니스 용어 생성
    ↓
[보강 텍스트]
  "부서별 분기 매출 실적 집계 | 사업부 분기 매출 현황, 팀별 분기 실적,
   quarterly revenue by department, division quarterly performance"
    ↓
[BGE-M3 임베딩] Dense + Sparse 동시 생성 → Qdrant 저장
```

### 6.2 구현

독립 배치 스크립트 (`devtools/scripts/enrich_sql_history.py`):
1. sql_history 원본 데이터 로드
2. LLM으로 description 보강 (동의어·영어표현·관련 비즈니스 용어)
3. 보강된 텍스트를 BGE-M3로 Dense+Sparse 임베딩
4. Qdrant sql_history 컬렉션 재생성 (Named Vectors 스키마)

---

## 7. Reranker 통합

### 7.1 위치 (관심사 분리)

```
QdrantConnector.search_sql_history()   → Top-50 후보 (Raw)
         ↓
context_retriever 노드 (reason/context_retriever.py)
         ↓
Reranker.rerank(query, candidates)     → Top-5~10 (Precise)
         ↓
ContextInfo.vector_past_sqls
```

Reranker(`src/connectors/impl/reranker.py`)는 커넥터 계층에 배치되며,
`context_retriever` 노드에서 Qdrant 검색 후 재순위를 호출한다.
임베딩·재순위 기능은 QdrantConnector에 통합되었다(`src/services/__init__.py` 참조).

### 7.2 폴백

Reranker 모델이 없거나 비활성화 상태면 벡터 검색 스코어 기반 Top-K를 그대로 사용한다.
`settings.reranker_enabled = False`로 제어.

---

## 8. 파일 변경 목록

### 신규 생성

| 파일 | 역할 | 상태 |
|------|------|------|
| `src/connectors/impl/reranker.py` | BGE-Reranker-v2-m3 래퍼 (폴백 포함) | 구현 완료 |
| `devtools/scripts/enrich_sql_history.py` | LLM 기반 오프라인 문서 보강 배치 | 구현 완료 |

> **Note:** 초기 설계의 독립 임베딩 서비스(`search_query_embedder.py`)는
> `src/connectors/impl/qdrant_connector.py`에 통합되었다.
> 검색 쿼리 빌더와 컨텍스트 조립 기능은 독립 서비스 대신
> `SearchKeywords` 모델(`src/agents/models/normalization.py`)과
> `query_normalizer` 서비스, `context_retriever` 노드에 분산 통합되었다.

### 수정

| 파일 | 변경 내용 | 상태 |
|------|----------|------|
| `src/config.py` | BGE-M3 모델 경로, Reranker 설정, sql_history 컬렉션명, 하이브리드 가중치 | 구현 완료 |
| `src/agents/models/normalization.py` | `SearchKeywords`에 `sql_history_search: str` 필드 추가 | 구현 완료 |
| `src/models/context.py` | `ContextInfo`에 `vector_past_sqls: list[str]` 필드 추가 | 구현 완료 |
| `src/connectors/impl/qdrant_connector.py` | `search_sql_history()` + 하이브리드 검색 + BGE-M3 임베딩 전환 | 구현 완료 |
| `src/services/query_normalizer.py` | `sql_history_search` 벡터 쿼리 합성 로직 (슬롯 기반) | 통합 예정 |
| `src/agents/nodes/reason/context_retriever.py` | sql_history 벡터 검색 호출, 병렬 수집에 포함 | 통합 예정 |
| `src/agents/nodes/interpret/query_normalizer.py` | `sql_history_search` 생성 로직 (후처리 단계) | 통합 예정 |
| `devtools/scripts/seed_qdrant.py` | BGE-M3 하이브리드 임베딩, Named Vectors 스키마 | 구현 완료 |
| `pyproject.toml` | `FlagEmbedding` 의존성 추가, `fastembed` 제거 | 구현 완료 |

---

## 9. 구현 우선순위 (ROI 기준)

```
1순위: NormalizedQuery 슬롯 기반 sql_history 벡터 쿼리 합성
       → 기존 인프라로 즉시 효과. 구조화된 정보 활용이 핵심 차별점.

2순위: BGE-M3 Dense+Sparse 하이브리드 검색
       → 비즈니스 키워드 정확 매칭 + 의미 유사도 동시 확보.

3순위: LLM 기반 문서 보강 (오프라인 배치)
       → 모델 교체 없이 Recall 대폭 향상. 오프라인 1회 비용.

4순위: BGE-Reranker-v2-m3 추가
       → Recall@50 → Precision@5~10 변환. 사용자 체감 품질 직접 향상.
```

---

*이 문서는 구현과 함께 갱신됩니다.*

---

## 변경 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|----------|
| 1.0 | 2026-03-21 | 초안 작성 |
| 1.1 | 2026-04-01 | v3 파이프라인 리팩터링 반영: Context Service → context_retriever 노드, SQL Generator → sql_generator 노드, SearchQueryBuilder → query_normalizer 서비스 통합, 임베딩·재순위 QdrantConnector 통합, 파일 경로 현행화 (reranker.py 위치 변경, ContextInfo → models/context.py), 구현 상태 칼럼 추가 |
| 1.2 | 2026-04-02 | planner → reasoning_preparer 리네임 반영 (규칙 기반, LLM/프롬프트 미사용) |
