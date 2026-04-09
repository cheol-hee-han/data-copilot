# 검색 도구 페이징 설계 — recovery_agent 추가 탐색 지원

- **작성일**: 2026-04-05
- **상태**: 상세설계 완료, 구현 대기
- **참조 리서치**: `docs/research/20260405-mongodb-sort-stability-qdrant-pagination.md`
- **영향 범위**: `tools.py`, `tool_renderers.py`, `context_retriever.py`, `context_interpreter.py`, `state.py`, `mongo_connector.py`, `qdrant_connector.py`, `reasoning_preparer.py`, `recovery_agent.py`, `trace_analyzer.py`, `config.py`, 프롬프트 2개, 테스트 14개 파일

---

## 목차

| # | 섹션 | 요약 |
|---|------|------|
| 1 | [문제 정의](#1-문제-정의) | 현재 검색 도구 한계, 문제 시나리오, 중복 방지 메커니즘 |
| 2 | [설계 원칙](#2-설계-원칙) | `page=N` 인터페이스 통일, 커넥터 계층 분리, 변경 최소화 |
| 3 | [커넥터별 페이징 전략](#3-커넥터별-페이징-전략) | MongoDB `$skip`+`$sort` 안정화, Qdrant `exclude_ids`+prefetch 보정, PostgreSQL `OFFSET` |
| 4 | [도구 인터페이스 통합](#4-도구-인터페이스-통합) | `lookup_table_meta`/`search_table_meta` 분리, 네이밍 컨벤션, 어댑터 패턴, `execute_tool` **kwargs |
| 5 | [상태 모델 변경](#5-상태-모델-변경) | `point_id` + `source_step` 필드 추가 (4개 엔티티), `ToolExecutionLog` 별도 모델 불필요 (동적 집계) |
| 6 | [context_retriever 변경](#6-context_retriever-변경) | `_run_step`에 `seen_ids` dict 전달, batch 전 1회 수집 |
| 6b | [reasoning_preparer 변경](#6b-reasoning_preparer-변경) | `_build_execution_plan`에서 검색 도구 input에 `, page=1` 부기 |
| 7 | [recovery_agent 프롬프트 변경](#7-recovery_agent-프롬프트-변경) | `{exploration_history}`+`{discovered_facts}` → `{tool_execution_history}` 통합, 관련성 동적 집계, 페이징 안내, few-shot |
| 8 | [config.py 변경](#8-configpy-변경) | `mongo_biz_term_size`, `qdrant_max_prefetch`, `qdrant_manual_max_limit` 신규 설정 |
| 9 | [죽은 코드 정리](#9-죽은-코드-정리) | 파라미터명 변경 영향, docstring 갱신, 제거 대상 없음 |
| 10 | [파일별 변경 매트릭스](#10-파일별-변경-매트릭스) | 소스 11개 + 프롬프트 2개 + 테스트 14개 변경 유형·내용 요약 |
| 11 | [검증 계획](#11-검증-계획) | unit 11건 + integration 2건 + e2e 1건 + unit 1건 |
| 12 | [구현 순서](#12-구현-순서) | 의존성 기반 9단계 순서 |
| 13 | [비판적 검토](#13-비판적-검토--발견된-이슈-해결방안-판단-기준) | Critical 3건 + Warning 6건 + 추가 발견 3건 (각 해결방안·권장안 포함) |

---

## 1. 문제 정의

### 1.1 현재 상태

모든 검색 도구가 **상위 N건만 반환**하며, 그 이후의 결과에 접근할 방법이 없다.

| 도구 | 커넥터 | 현재 limit | offset | 페이징 | TOOL_MAP 등록 |
|------|--------|:-----:|:------:|:------:|:------:|
| search_table_meta | MongoDB | `settings.mongo_table_meta_size` (10) | - | - | 직접 함수 |
| search_code_meta | MongoDB | `settings.mongo_code_meta_size` (10) | - | - | 직접 함수 |
| search_biz_terms | MongoDB | 없음 (전체 반환) | - | - | 직접 함수 |
| search_use_cases | Qdrant | `qdrant_sql_history_top_k` (5, rerank 후) | - | - | 직접 함수 |
| search_manual | Qdrant | `qdrant_search_top_k` (3) | - | - | 직접 함수 |
| get_sample_rows | DB 직접 | 10 (하드코딩) | - | - | `_tool_get_sample_rows` 어댑터 |
| search_column_values | DB 직접 | 20 (하드코딩) | - | - | `_tool_search_column_values` 어댑터 |
| get_column_profile | DB 직접 | 1 (집계, 항상 단건) | - | - | `_tool_get_column_profile` 어댑터 |
| get_date_distribution | DB 직접 | 30 (하드코딩) | - | - | `_tool_get_date_distribution` 어댑터 |

> **TOOL_MAP 구조 참고**: 검색 도구(search_*)는 `async def fn(query: str) -> list[dict]` 시그니처로 직접 등록.
> DB 직접 조회 도구(get_*)는 `_tool_` 접두사 어댑터가 쉼표 구분 문자열을 파싱하여 실제 함수에 전달.
> `search_column_values`는 이번 변경에서 `get_column_values`로 리네이밍된다 (섹션 4.2 네이밍 컨벤션 참조).

### 1.2 문제 시나리오

```
1차 탐색: search_table_meta("여신") → 상위 10건 반환
    → readiness_gate: 정보 부족 → recovery_agent → 재탐색 계획
2차 탐색: search_table_meta("여신") → 동일한 상위 10건 반환 (변화 없음)
    → 11번째 테이블이 정답이어도 영원히 도달 불가

> **참고**: 이 문서에서 도구명이 `lookup_table_meta` / `search_table_meta` / `lookup_code_meta`로
> 변경된다. 현재 코드베이스의 도구명(`search_table_meta`, `search_code_meta`)은 섹션 4.2에서 재명명된다.
```

recovery_agent가 키워드를 바꿔서 시도할 수 있지만, LLM이 항상 적절한 대안 키워드를
생각해내지는 못한다. 같은 키워드로 "다음 페이지"를 볼 수 있어야 한다.

### 1.3 현재 중복 방지 메커니즘

`context_retriever._should_skip_step()`이 `executed_tool_keys` set에서
`"{step.tool}:{step.input}"` 형식으로 중복을 검사한다 (`context_retriever.py:58`).
동일 tool+input 조합은 SKIPPED 처리되므로, `page=` 접미사가 없으면
recovery_agent가 같은 검색어로 재시도해도 스킵된다.

**page 도입 시**: `"search_table_meta:여신, page=2"`는 기존 키
`"search_table_meta:여신"`과 다르므로 자연스럽게 실행이 허용된다.
(도구명 변경 후에도 동일한 원리가 적용된다.)

---

## 2. 설계 원칙

### 2.1 인터페이스 통일 — 모든 도구에 `page=N` 방식 적용

MongoDB는 `$skip` 기반, Qdrant는 `seen_ids` 기반으로 내부 구현이 다르지만,
**도구 계층(tools.py)에서 LLM에 노출하는 인터페이스는 `page=N`으로 통일**한다.

이유:
1. 폐쇄망 70B 모델(Solar Pro 2/Qwen3.5)이 도구별로 다른 페이징 방식을 학습하기 어렵다
2. recovery_agent 프롬프트를 단순하게 유지할 수 있다
3. `executed_tool_keys` 중복 방지가 동일 패턴으로 동작한다

Qdrant 도구의 `page → seen_ids` 변환은 `context_retriever`가 담당한다.

### 2.2 커넥터 계층 분리

커넥터(`mongo_connector.py`, `qdrant_connector.py`)는 페이징의 물리적 구현만 담당한다.
`page` 파라미터 해석이나 `seen_ids` 조합 같은 오케스트레이션 로직은
도구 계층(`tools.py`)이나 실행 계층(`context_retriever.py`)에서 처리한다.

### 2.3 변경 최소화

- `ExecutionStep` 모델 필드 추가 없음 — page는 `input` 문자열에 포함
- `execute_tool` 시그니처 변경 최소화 — Qdrant 도구용 `**kwargs` 확장만 추가
- 기존 `_safe_search`, `_IDENT_RE` 검증 등 보안/안전 패턴 유지

---

## 3. 커넥터별 페이징 전략

### 3.1 MongoDB — `$skip` + `$sort` 안정화

#### 리서치 결과

MongoDB의 `$sort`는 **안정 정렬을 보장하지 않는다** (SERVER-51498, "Works as Designed").
동일 score 문서의 순서가 쿼리마다 바뀔 수 있어 `$skip` 기반 페이징 시 중복/누락이 발생한다.

**해결**: `_id`를 tiebreaker로 추가하면 안정 정렬이 보장된다.
`_id`(ObjectId)는 항상 유니크하고 자동 인덱싱되어 추가 비용이 없다.

#### 핵심 제약: `$project: {_id: 0}`과 `$sort: {_id: 1}` 충돌

현재 3개의 파이프라인 템플릿 모두 `$project` 스테이지에서 `"_id": 0`을 설정한다:
- `resources/connectors/mongo/pipeline_table_meta.json`: `{"$project": {"_id": 0, "name": 1, ...}}`
- `resources/connectors/mongo/pipeline_code_meta.json`: `{"$project": {"_id": 0, "code_field": ...}}`
- `resources/connectors/mongo/pipeline_biz_term.json`: `{"$project": {"_id": 0, "name": ...}}`

**해결**: `$sort` → `$skip` → `$limit`를 `$project` **이전**에 배치한다.
`$project`에서 `_id`를 제거해도 정렬과 페이징은 이미 완료된 상태이므로 문제없다.

#### 변경 대상: `src/connectors/impl/mongo_connector.py`

##### search_table_meta — 변경 후

```python
async def search_table_meta(
    self, query: str, **kwargs: Any,
) -> list[dict[str, Any]]:
    # ... (기존 dummy 분기, match_stage, lookup, score_stages 구성 동일) ...

    limit = kwargs.get("limit", settings.mongo_table_meta_size)
    page = kwargs.get("page", 1)
    skip = (page - 1) * limit

    # $sort 안정화: _id tiebreaker 추가
    # MongoDB $sort는 동일 score에서 순서를 보장하지 않으므로 (SERVER-51498)
    # _id를 2차 정렬 키로 추가하여 페이지 간 중복/누락을 방지한다.
    sort_stage = (
        {"$sort": {"_keyword_score": -1, "_id": 1}}
        if score_stages
        else {"$sort": {"_id": 1}}
    )

    # 순서 핵심: sort → skip → limit → project
    # $project에서 _id: 0으로 제거하므로, 정렬/페이징은 반드시 project 이전에 수행
    pipeline = [
        match_stage,
        *score_stages,
        lookup,
        sort_stage,
        {"$skip": skip},
        {"$limit": limit},
        _TPL_TABLE_META["project"],      # _id: 0 제거는 마지막
    ]
```

**기존 파이프라인 순서 대비 변경점**:
```
기존: match → score → lookup → project → limit
변경: match → score → lookup → sort → skip → limit → project
```

##### search_code_meta — 동일 패턴 적용

```python
async def search_code_meta(
    self, query: str, **kwargs: Any,
) -> list[dict[str, Any]]:
    # ... (기존 dummy 분기, match_stage 구성 동일) ...

    limit = kwargs.get("limit", settings.mongo_code_meta_size)
    page = kwargs.get("page", 1)
    skip = (page - 1) * limit

    pipeline = [
        match_stage,
        lookup,
        {"$sort": {"_id": 1}},           # code_meta는 score_stages 없음
        {"$skip": skip},
        {"$limit": limit},
        _TPL_CODE_META["project"],
    ]
```

**기존 파이프라인 순서 대비 변경점**:
```
기존: match → lookup → project → limit
변경: match → lookup → sort → skip → limit → project
```

##### search_biz_terms — limit 추가 + 페이징

현재 search_biz_terms는 **limit이 없어 전체 결과를 반환**한다.
페이징과 함께 기본 limit을 추가한다. config에 `mongo_biz_term_size` 설정을 새로 추가한다.

```python
async def search_biz_terms(
    self, query: str, **kwargs: Any,
) -> list[dict[str, Any]]:
    # ... (기존 dummy 분기, match_stage 구성 동일) ...

    limit = kwargs.get("limit", settings.mongo_biz_term_size)   # 신규 설정
    page = kwargs.get("page", 1)
    skip = (page - 1) * limit

    # biz_term은 $unwind가 $lookup 이후에 필요
    # unwind 후에 sort/skip/limit 적용
    pipeline = [
        match_stage,
        lookup,
        _TPL_BIZ_TERM["unwind"],
        {"$sort": {"_id": 1, "name": 1}},
        {"$skip": skip},
        {"$limit": limit},
        _TPL_BIZ_TERM["project"],        # _id: 0 제거는 마지막
    ]
```

**기존 파이프라인 순서 대비 변경점**:
```
기존: match → lookup → unwind → project (limit 없음)
변경: match → lookup → unwind → sort(_id, name) → skip → limit → project
```

> **주의**: `$unwind`가 `_id`를 변경하지는 않는다. 원본 문서의 `_id`가 유지되므로
> 같은 `_id`를 가진 여러 행(unwind로 펼쳐진)이 생길 수 있다.
> 이 경우 `_id` tiebreaker만으로는 완전한 안정 정렬이 보장되지 않는다.
> 단, biz_term의 용어 수가 200건 이내(CLAUDE.md 명시)이므로 실질적 영향은 미미하다.

---

### 3.2 Qdrant — `exclude_ids` 필터 + `prefetch_limit` 보정

#### 리서치 결과

Qdrant의 `offset`은 내부적으로 `offset + limit`개를 HNSW에서 모두 탐색 후 앞을 버리는 방식이다.
hybrid search(RRF)에서는 prefetch → fusion → rerank 3단계가 있어 offset 적용 위치에 따라
결과 일관성이 깨진다.

**채택**: `exclude_ids` 필터 + `prefetch_limit` 보정 방식.

#### 설계 핵심

1. 이전에 반환된 point id를 `Filter(must_not=[HasIdCondition])` 으로 제외
2. 제외된 만큼 `prefetch_limit`을 늘려서 reranker에 항상 동일한 수의 후보가 유입
3. `page` 파라미터는 LLM 인터페이스에만 존재 — 커넥터에는 `exclude_ids`만 전달
4. `_rerank()` 메서드의 반환값에 `_point_id` 필드 추가 (seen_ids 누적용)

#### 변경 대상: `src/connectors/impl/qdrant_connector.py`

##### search_sql_history — 변경 후

```python
async def search_sql_history(
    self,
    query: str,
    prefetch_limit: int | None = None,
    top_k: int | None = None,
    exclude_ids: list[str | int] | None = None,
) -> list[dict[str, Any]]:
    """SQL 수행이력 하이브리드 검색 (Dense + Sparse RRF + Reranker).

    Args:
        exclude_ids: 이전 검색에서 반환된 point id 목록.
            지정하면 해당 id를 HNSW 탐색에서 제외하고,
            제외된 만큼 prefetch_limit을 보정하여
            reranker에 항상 동일한 수의 후보가 들어가도록 한다.
    """
    if prefetch_limit is None:
        prefetch_limit = settings.qdrant_sql_history_prefetch_limit
    if top_k is None:
        top_k = settings.qdrant_sql_history_top_k
    if self._use_dummy:
        return search_dummy_qdrant_sql_history(query, prefetch_limit)

    from qdrant_client.models import (
        Filter, Fusion, FusionQuery, HasIdCondition,
        Prefetch, SparseVector,
    )

    # seen_ids 필터 + prefetch 보정
    query_filter = None
    effective_prefetch = prefetch_limit
    if exclude_ids:
        query_filter = Filter(
            must_not=[HasIdCondition(has_id=exclude_ids)],
        )
        effective_prefetch = min(
            prefetch_limit + len(exclude_ids),
            settings.qdrant_max_prefetch,       # 상한 제어 (신규 설정)
        )

    emb = self.encode(query)
    sparse_vector = SparseVector(
        indices=emb.sparse_indices,
        values=emb.sparse_values,
    )

    start = _time.perf_counter()
    results = await self._client.query_points(
        collection_name=settings.qdrant_sql_history_collection,
        prefetch=[
            Prefetch(
                query=emb.dense, using="dense",
                limit=effective_prefetch,
                filter=query_filter,
            ),
            Prefetch(
                query=sparse_vector, using="sparse",
                limit=effective_prefetch,
                filter=query_filter,
            ),
        ],
        query=FusionQuery(fusion=Fusion.RRF),
        limit=effective_prefetch,
        filter=query_filter,
    )
    elapsed = (_time.perf_counter() - start) * 1000

    # _point_id 포함하여 다음 호출 시 exclude_ids로 전달 가능
    payloads = [
        {**point.payload, "_score": point.score, "_point_id": point.id}
        for point in results.points
    ]
    logger.info(
        "Qdrant sql_history 하이브리드 검색",
        query=truncate_log(query),
        count=len(payloads),
        excluded=len(exclude_ids) if exclude_ids else 0,
        latency_ms=round(elapsed, 1),
    )
    return self._rerank(query, payloads, top_k)
```

**기존 코드 대비 변경점**:
1. `exclude_ids` 파라미터 추가
2. `Filter(must_not=[HasIdCondition])` 적용 (prefetch 각각 + 메인 쿼리)
3. `effective_prefetch` 보정 + 상한(`settings.qdrant_max_prefetch`)
4. `_point_id` 필드를 payload에 추가

##### _rerank — `_point_id` 전달 보존

현재 `_rerank` 메서드는 `item.payload.copy()`로 원본 payload를 복사하므로,
payload에 포함된 `_point_id`는 자동으로 반환값에 포함된다. **추가 변경 불필요.**

```python
# 기존 _rerank (변경 없음)
d = item.payload.copy() if isinstance(item.payload, dict) else {}
# → payload에 _point_id가 있으면 d에도 포함됨
```

##### search_manual — 동일 패턴 적용

```python
async def search_manual(
    self,
    query: str,
    top_k: int | None = None,
    exclude_ids: list[str | int] | None = None,
) -> list[dict[str, Any]]:
    """업무 매뉴얼에서 관련 문서를 검색한다."""
    if top_k is None:
        top_k = settings.qdrant_search_top_k
    if self._use_dummy:
        return search_dummy_manuals(query, top_k)

    query_filter = None
    effective_limit = top_k
    if exclude_ids:
        from qdrant_client.models import Filter, HasIdCondition
        query_filter = Filter(
            must_not=[HasIdCondition(has_id=exclude_ids)],
        )
        effective_limit = min(
            top_k + len(exclude_ids),
            settings.qdrant_manual_max_limit,
        )

    embedding = self.encode_dense_only(query)

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
        {**hit.payload, "_point_id": hit.id}
        for hit in results[:top_k]
    ]
```

**기존 코드 대비 변경점**:
1. `exclude_ids` 파라미터 추가
2. `query_filter` 적용 + `effective_limit` 보정
3. `_point_id` 필드 추가 (기존: `hit.payload`만 반환)

---

### 3.3 PostgreSQL DB 직접 쿼리 — `OFFSET` 절 추가

#### 변경 대상: `src/agents/nodes/reason/tools.py`

##### get_sample_rows — 변경 없음 (페이징 미지원)

`get_sample_rows`는 **page 파라미터를 추가하지 않는다**.
ORDER BY 없이 OFFSET은 중복/누락이 불가피하고,
"더 보고 싶다"면 `get_column_values`를 사용하는 것이 정확한 접근이다.
기존 함수 시그니처와 동작을 그대로 유지한다.

##### get_column_values (구 search_column_values) — 변경 후

```python
async def get_column_values(
    table_name: str,
    column_name: str,
    keyword: str,
    limit: int = 20,
    schema_name: str = "",
    db_source: str = "",
    page: int = 1,
) -> list[str]:
    # ... (기존 _IDENT_RE 검증 + sanitize 로직 100% 유지) ...

    sanitized_kw = keyword.replace("'", "''").replace("\\", "\\\\")
    qualified = f"{schema_name}.{table_name}" if schema_name else table_name
    offset = (page - 1) * limit

    mgr = get_connector_manager()
    db = mgr.get_query_db(db_source=db_source)

    if db.dialect == "tsql":
        start_at = offset + 1
        sql = (
            f"SELECT DISTINCT TOP {limit} START AT {start_at} "
            f"{column_name} FROM {qualified} "
            f"WHERE {column_name} LIKE '%{sanitized_kw}%' "
            f"ORDER BY {column_name}"
        )
    else:
        sql = (
            f"SELECT DISTINCT {column_name} FROM {qualified} "
            f"WHERE {column_name} LIKE '%{sanitized_kw}%' "
            f"ORDER BY {column_name} LIMIT {limit} OFFSET {offset}"
        )
    # ... (기존 try/except 동일) ...
```

> **주의**: `get_column_values`는 이미 `ORDER BY` 절이 있으므로 OFFSET 기반 페이징이 안정적이다.

##### get_date_distribution — 변경 없음 (페이징 미지원)

`get_date_distribution`은 **page 파라미터를 추가하지 않는다**.
날짜 범위를 더 보고 싶다면 조건을 좁히거나 `get_column_values`를 사용한다.

---

## 4. 도구 인터페이스 통합

### 4.1 `_extract_page` 헬퍼 추가

**위치**: `src/agents/nodes/reason/tools.py` (모듈 상단, `_split_qualified_name` 근처)

모든 어댑터에서 공용으로 사용하는 단일 헬퍼를 추가한다.
호출부에서 `[p.strip() for p in tool_input.split(",")]`로 분리한 뒤 `_extract_page`에 전달한다.
검색 도구는 반환된 `remaining`을 `", ".join(parts)`로 다시 합쳐 원본 함수에 전달한다.

```python
_MAX_PAGE = 5

def _extract_page(parts: list[str]) -> tuple[list[str], int]:
    """파라미터 목록에서 page=N을 추출하고 나머지를 반환한다.

    ["여신", "page=2"] → (["여신"], 2)
    ["테이블명", "컬럼명", "page=3"] → (["테이블명", "컬럼명"], 3)
    ["여신"] → (["여신"], 1)

    page가 비정수/음수/0이면 기본값 1, _MAX_PAGE 초과 시 _MAX_PAGE로 클램핑한다.
    """
    page = 1
    remaining: list[str] = []
    for part in parts:
        if part.startswith("page="):
            try:
                p = int(part.split("=", 1)[1])
                page = max(1, min(p, _MAX_PAGE))
            except (ValueError, IndexError):
                pass
        else:
            remaining.append(part)
    return remaining, page
```

### 4.2 `lookup_table_meta` / `search_table_meta` 도구 분리 + 네이밍 컨벤션

기존 `search_table_meta`는 한글 키워드 탐색과 영문 테이블명 조회 두 용도로 사용되었다.
페이징은 키워드 탐색에만 의미가 있으므로 **두 도구로 분리**하고, 역할에 맞는 동사를 부여한다.

#### 네이밍 컨벤션: `lookup_` / `search_` / `get_`

| 접두사 | 의미 | 예시 |
|--------|------|------|
| `lookup_` | 키/이름을 알고 있는 **지정 조회** | `lookup_table_meta`, `lookup_code_meta` |
| `search_` | 키워드/의미 기반 **탐색** (다건, page 지원) | `search_table_meta`, `search_manual`, `search_use_cases` |
| `get_` | DB에서 데이터 **직접 가져오기** | `get_sample_rows`, `get_column_profile` |

이 컨벤션에 따라 기존 도구명을 다음과 같이 변경한다:

| 기존 도구명 | 새 도구명 | 이유 |
|------------|----------|------|
| `search_table_meta` (영문 정확 매칭) | `lookup_table_meta` | 테이블명 지정 조회 → lookup |
| `search_table_metas` (한글 키워드) | `search_table_meta` | 키워드 탐색 → search |
| `search_code_meta` | `lookup_code_meta` | 컬럼명 지정 조회 → lookup |

> **주의**: MongoDB 커넥터 메서드명(`mongo.search_table_meta`, `mongo.search_code_meta`)은 변경하지 않는다.
> 도구 계층(tools.py)의 함수명과 TOOL_MAP 키만 변경한다.

| 도구 | input | 커넥터 경로 | page | 용도 |
|------|-------|-----------|:----:|------|
| `lookup_table_meta` | 영문 테이블명 | `table_names=[name]` → `$in` 정확 매칭 | 불필요 | 특정 테이블 상세 조회 |
| `search_table_meta` | 한글 키워드 | `_build_regex_match` → regex + keyword score | 지원 | 키워드 기반 넓은 탐색 |

도구명 기반 분기에 사용할 상수:

```python
_TABLE_META_TOOLS = frozenset({"lookup_table_meta", "search_table_meta"})
```

#### 원본 함수 (외부 직접 호출 호환)

```python
async def lookup_table_meta(table_name: str) -> list[dict]:
    """특정 테이블의 메타 정보를 조회한다 (영문 테이블명 정확 매칭).

    enrichment, recovery_agent에서 특정 테이블을 지정 조회할 때 사용.
    커넥터의 table_names kwargs를 통해 $in 정확 매칭으로 동작한다.
    """
    mgr = get_connector_manager()
    return await _safe_search(
        "lookup_table_meta",
        mgr.mongo.search_table_meta(table_name, table_names=[table_name]),
    )


async def search_table_meta(keywords: str, page: int = 1) -> list[dict]:
    """한글 키워드로 테이블/컬럼 메타를 검색한다 (regex + keyword score).

    reasoning_preparer의 초기 탐색, recovery_agent의 추가 탐색에서 사용.
    page=N으로 다음 결과 블록을 조회할 수 있다.
    """
    mgr = get_connector_manager()
    return await _safe_search(
        "search_table_meta",
        mgr.mongo.search_table_meta(keywords, page=page),
    )
```

#### TOOL_MAP 어댑터 (C-2 방안 C 적용)

```python
async def _tool_lookup_table_meta(tool_input: str) -> list[dict]:
    """lookup_table_meta TOOL_MAP 어댑터 — 영문 테이블명 조회."""
    table_name = tool_input.strip()
    return await lookup_table_meta(table_name)


async def _tool_search_table_meta(tool_input: str) -> list[dict]:
    """search_table_meta TOOL_MAP 어댑터 — 한글 키워드 검색 + page."""
    parts, page = _extract_page([p.strip() for p in tool_input.split(",")])
    return await search_table_meta(", ".join(parts), page=page)
```

> **`lookup_table_meta` 어댑터에서 `_extract_page`를 호출하지 않는다** —
> 영문 테이블명에 page가 없으므로 파싱 불필요. `tool_input.strip()`만 수행.

#### 나머지 MongoDB 검색 도구 — 어댑터 패턴 동일 적용

```python
# ── 원본 함수 ──

async def lookup_code_meta(column_name: str, page: int = 1) -> list[dict]:
    """코드값 목록 조회 (MongoDB). 컬럼명 지정 조회이므로 lookup_ 접두사."""
    mgr = get_connector_manager()
    return await _safe_search(
        "lookup_code_meta",
        mgr.mongo.search_code_meta(column_name, page=page),
    )

async def search_biz_terms(term: str, page: int = 1) -> list[dict]:
    """비즈니스 용어사전 검색 (MongoDB biz_term 컬렉션)."""
    mgr = get_connector_manager()
    return await _safe_search(
        "search_biz_terms",
        mgr.mongo.search_biz_terms(term, page=page),
    )

# ── TOOL_MAP 어댑터 ──

async def _tool_lookup_code_meta(tool_input: str) -> list[dict]:
    parts, page = _extract_page([p.strip() for p in tool_input.split(",")])
    return await lookup_code_meta(", ".join(parts), page=page)

async def _tool_search_biz_terms(tool_input: str) -> list[dict]:
    parts, page = _extract_page([p.strip() for p in tool_input.split(",")])
    return await search_biz_terms(", ".join(parts), page=page)
```

#### Qdrant 검색 도구 — 어댑터 + `exclude_ids`

```python
# ── 원본 함수 ──

async def search_use_cases(
    query: str,
    *, exclude_ids: list[str] | None = None,
) -> list[dict]:
    """유사 SQL 활용사례 벡터 검색 + Reranker 재순위."""
    mgr = get_connector_manager()
    return await _safe_search(
        "search_use_cases",
        mgr.qdrant.search_sql_history(query, exclude_ids=exclude_ids),
    )

async def search_manual(
    query: str,
    *, exclude_ids: list[str] | None = None,
) -> list[dict]:
    """업무 매뉴얼 검색 (Qdrant biz_manual 컬렉션)."""
    mgr = get_connector_manager()
    return await _safe_search(
        "search_manual",
        mgr.qdrant.search_manual(query, exclude_ids=exclude_ids),
    )

# ── TOOL_MAP 어댑터 ──

async def _tool_search_use_cases(
    tool_input: str,
    *, exclude_ids: list[str] | None = None,
) -> list[dict]:
    parts, _page = _extract_page([p.strip() for p in tool_input.split(",")])
    return await search_use_cases(", ".join(parts), exclude_ids=exclude_ids)

async def _tool_search_manual(
    tool_input: str,
    *, exclude_ids: list[str] | None = None,
) -> list[dict]:
    parts, _page = _extract_page([p.strip() for p in tool_input.split(",")])
    return await search_manual(", ".join(parts), exclude_ids=exclude_ids)
```

> **Qdrant 도구의 `_page`**: 사용하지 않지만 `_extract_page`로 파싱은 수행한다.
> `executed_tool_keys`에 `"search_use_cases:여신, page=2"` 형태로 기록되어
> 중복 방지가 동작해야 하므로. 커넥터에는 `exclude_ids`만 전달.

### 4.3 DB 도구 어댑터 변경

DB 도구 어댑터(`_tool_*`)는 섹션 4.1의 `_extract_page` 헬퍼를 공용으로 사용한다.
`_extract_page_from_parts`는 별도로 존재하지 않으며, `_extract_page`가 모든 어댑터의 page 추출을 담당한다.

> **get_sample_rows, get_date_distribution은 page를 지원하지 않는다.**
> ORDER BY 없이 OFFSET은 중복/누락이 불가피하고,
> "더 보고 싶다"면 `get_column_values`를 사용하는 것이 정확한 접근이다.
> 날짜 범위를 더 보고 싶다면 조건을 좁히거나 `get_column_values`를 사용한다.

```python
async def _tool_get_sample_rows(tool_input: str) -> Any:
    """get_sample_rows TOOL_MAP 어댑터."""
    parts = [p.strip() for p in tool_input.split(",")]
    raw_table = parts[0] if parts else ""
    schema_name, table_name = _split_qualified_name(raw_table)
    return await get_sample_rows(table_name, schema_name=schema_name)


async def _tool_get_column_values(tool_input: str) -> Any:
    """get_column_values TOOL_MAP 어댑터."""
    parts, page = _extract_page([p.strip() for p in tool_input.split(",")])
    raw_table = parts[0] if parts else ""
    column_name = parts[1] if len(parts) > 1 else ""
    keyword = parts[2] if len(parts) > 2 else ""
    if not raw_table or not column_name or not keyword:
        return []
    schema_name, table_name = _split_qualified_name(raw_table)
    return await get_column_values(
        table_name, column_name, keyword,
        schema_name=schema_name, page=page,
    )


async def _tool_get_date_distribution(tool_input: str) -> Any:
    """get_date_distribution TOOL_MAP 어댑터."""
    parts = [p.strip() for p in tool_input.split(",")]
    raw_table = parts[0] if parts else ""
    date_column = parts[1] if len(parts) > 1 else ""
    if not raw_table or not date_column:
        return []
    schema_name, table_name = _split_qualified_name(raw_table)
    return await get_date_distribution(
        table_name, date_column, schema_name=schema_name,
    )
```

> **`_tool_get_column_profile`은 페이징 불필요** — 집계 통계(COUNT, MIN/MAX)이므로
> 결과가 항상 1행이다. page 파라미터를 추가하지 않는다.

### 4.4 execute_tool 시그니처 변경 (방안 A 적용)

Qdrant 도구에 `exclude_ids`를 전달하기 위해 `**kwargs`를 추가하되,
**Qdrant 도구에만 명시적으로 전달**하여 비-Qdrant 도구에 TypeError가 발생하지 않도록 한다.
(섹션 13.1 C-1의 방안 A 적용)

```python
_QDRANT_TOOLS = frozenset({"search_use_cases", "search_manual"})

async def execute_tool(
    tool_name: str, tool_input: str, **kwargs: Any,
) -> Any:
    """TOOL_MAP에서 도구명으로 함수를 찾아 실행한다."""
    tool_fn = TOOL_MAP.get(tool_name)
    if tool_fn is None:
        logger.warning("알 수 없는 도구", tool=tool_name)
        return None

    # Qdrant 도구만 exclude_ids를 명시적으로 전달
    if tool_name in _QDRANT_TOOLS and "exclude_ids" in kwargs:
        return await tool_fn(tool_input, exclude_ids=kwargs["exclude_ids"])
    return await tool_fn(tool_input)
```

**호환성**: `_QDRANT_TOOLS`에 포함되지 않는 도구에는 `kwargs`가 전파되지 않으므로
비-Qdrant 도구 호출 시 TypeError가 발생하지 않는다.
호출부(`context_retriever`)에서 도구 종류를 신경 쓰지 않고
`execute_tool(name, input, exclude_ids=ids)`를 무차별 호출해도 안전하다.

### 4.5 TOOL_MAP — 어댑터로 통일

모든 도구가 `_tool_` 어댑터를 통해 등록된다 (DB 도구 기존 패턴과 통일).

```python
TOOL_MAP: dict[str, Any] = {
    # 검색/조회 도구 — 어댑터 (신규 패턴)
    "search_use_cases":     _tool_search_use_cases,
    "lookup_table_meta":    _tool_lookup_table_meta,      # 영문 테이블명 지정 조회 (단건)
    "search_table_meta":    _tool_search_table_meta,      # 한글 키워드 탐색 + page (다건)
    "lookup_code_meta":     _tool_lookup_code_meta,       # 컬럼명 지정 코드 조회
    "search_manual":        _tool_search_manual,
    "search_biz_terms":     _tool_search_biz_terms,
    # DB 직접 도구 — 어댑터 (기존 패턴 유지)
    "get_sample_rows":      _tool_get_sample_rows,
    "get_column_values":    _tool_get_column_values,
    "get_column_profile":   _tool_get_column_profile,     # 변경 없음
    "get_date_distribution": _tool_get_date_distribution,
}
```

> **`__all__` export**: `lookup_table_meta`, `search_table_meta`, `lookup_code_meta`를 추가한다.
> enrichment에서 `lookup_table_meta`를 직접 import하므로 기존 export도 유지.

---

## 5. 상태 모델 변경

### 5.1 ExecutionStep — 변경 없음

page는 `input` 문자열에 포함되므로 모델 필드 추가 불필요.
```
"여신, page=2" → step.input에 그대로 저장
```

### 5.2 executed_tool_keys — page 포함으로 자연 해결

```
1차: "search_table_meta:여신, page=1" → executed_tool_keys에 추가
2차: "search_table_meta:여신, page=2" → 다른 키 → 실행 허용
```

`context_retriever._should_skip_step()`의 기존 로직 (`tool_key = f"{step.tool}:{step.input}"`)이
그대로 동작한다.

### 5.3 UseCaseEntry — `point_id` 필드 추가

Qdrant의 point id를 `explored_use_cases`에 직접 보관한다.
`_clear_raw_results` 이후에도 id가 보존되므로 **별도 `qdrant_seen_ids` 필드 불필요**.

```python
# state.py — UseCaseEntry에 추가
class UseCaseEntry(BaseModel):
    """탐색 중 발견된 유사 SQL 활용사례."""

    id: str = ""
    description: str = ""
    sql: str = ""
    domain: str = ""
    score: float = 0.0

    # ── Qdrant point id (페이징 시 exclude 대상) ──
    point_id: str = ""                   # ← 신규
    # ── 발견 스텝 번호 (tool_execution_history 크로스 레퍼런스용) ──
    source_step: int = 0                 # ← 신규

    # ── LLM 판정 (context_interpreter) ──
    relevant: bool = False
    eval_reason: str = ""

    # ── enrichment (context_retriever) ──
    enrichment_tables: list[dict] = Field(default_factory=list)
    enrichment_codes: dict[str, dict] = Field(default_factory=dict)

    model_config = {"extra": "allow"}
```

### 5.4 BizManualEntry — `point_id` 필드 추가

```python
# state.py — BizManualEntry에 추가
class BizManualEntry(BaseModel):
    """업무 매뉴얼 검색 결과."""

    biz_manual_id: str = ""
    content: str = ""
    score: float = 0.0
    source: str = ""
    point_id: str = ""                   # ← 신규
    source_step: int = 0                 # ← 신규
    selection_status: SelectionStatus = SelectionStatus.PENDING
    selection_reason: str = ""
```

> **참고**: `TableMeta`, `BizTermEntry`에도 동일하게 `source_step: int = 0` 필드를 추가한다.
> `TableMeta`는 `search_table_meta`/`lookup_table_meta` hydrate 시, `BizTermEntry`는
> `search_biz_terms` hydrate 시 해당 `step.step` 값을 저장한다.

### 5.5 ReasoningState — 변경 없음 (별도 qdrant_seen_ids 불필요)

Qdrant point id가 `explored_use_cases`와 `explored_biz_manuals`에 직접 보관되므로,
`ReasoningState`에 별도 `qdrant_seen_ids` 필드를 추가하지 않는다.

**exclude_ids 수집 시**: `explored_use_cases`/`explored_biz_manuals`에서
같은 검색 쿼리의 기존 결과를 필터링하여 `point_id` 목록을 추출한다.
(수집 로직은 섹션 6.1 참조)

### 5.6 context_interpreter hydrate 함수 변경

#### `_hydrate_use_cases_from_raw` — `point_id`, `source_step` 저장 추가

```python
def _hydrate_use_cases_from_raw(
    raw: Any,
    explored_use_cases: list[UseCaseEntry],
    existing_ids: set[str],
    source_step: int,                                      # ← 신규 파라미터
) -> None:
    """search_use_cases의 raw_result에서 UseCaseEntry를 적재한다."""
    if not isinstance(raw, dict):
        return
    for uc_data in raw.get("use_cases", []):
        uc_id = uc_data.get("id", "")
        if not uc_id or uc_id in existing_ids:
            continue
        explored_use_cases.append(UseCaseEntry(
            id=uc_id,
            description=uc_data.get("description", ""),
            sql=uc_data.get("sql", ""),
            domain=uc_data.get("domain", ""),
            score=uc_data.get("score", 0.0),
            point_id=str(uc_data.get("_point_id", "")),    # ← 신규
            source_step=source_step,                        # ← 신규
        ))
        existing_ids.add(uc_id)
```

**변경점**: `point_id` + `source_step` 추가. 호출부에서 `step.step`을 전달한다.
커넥터에서 `_point_id`를 payload에 포함하므로, enrichment를 거쳐
`step.raw_result["use_cases"]` 각 dict에 `_point_id`가 전달된다.

#### `_hydrate_biz_manuals_from_raw` — `point_id`, `source_step` 저장 추가

```python
def _hydrate_biz_manuals_from_raw(
    raw: Any,
    explored_biz_manuals: list[BizManualEntry],
    existing_ids: set[str],
    counter: int,
    source: str,
    source_step: int,                                      # ← 신규 파라미터
) -> int:
    items = raw if isinstance(raw, list) else []
    for item in items:
        content = item.get("content", "") or item.get("text", "")
        if not content:
            continue
        counter += 1
        bm_id = f"bm_{counter:03d}"
        if bm_id in existing_ids:
            continue
        explored_biz_manuals.append(BizManualEntry(
            biz_manual_id=bm_id,
            content=content,
            score=item.get("score", 0.0),
            source=source,
            point_id=str(item.get("_point_id", "")),       # ← 신규
            source_step=source_step,                        # ← 신규
        ))
        existing_ids.add(bm_id)
    return counter
```

**변경점**: `point_id` + `source_step` 추가. 호출부에서 `step.step`을 전달한다.
`search_manual` 결과는 enrichment 없이 `step.raw_result`에 list로 저장되므로,
각 dict의 `_point_id`가 그대로 전달된다.

### 5.7 ToolExecutionLog 별도 모델 불필요 — 동적 집계 방식

recovery_agent 프롬프트에 표시할 도구 실행 이력은 **기존 state 필드를 동적으로 집계**하여 생성한다.
별도 `ToolExecutionLog` 모델을 추가하지 않는다.

**근거**: 관련성 평가 정보가 이미 각 엔티티 state에 저장되어 있다.

| 엔티티 | 관련성 평가 필드 | 저장 위치 |
|--------|-----------------|-----------|
| `UseCaseEntry` | `relevant: bool`, `eval_reason: str` | `explored_use_cases` |
| `TableMeta` | `selection_status: SelectionStatus`, `selection_reason: str` | `explored_tables` |
| `BizManualEntry` | `selection_status: SelectionStatus`, `selection_reason: str` | `explored_biz_manuals` |
| `BizTermEntry` | `selection_status: SelectionStatus`, `selection_reason: str` | `explored_biz_terms` |

각 엔티티 모델에 `source_step: int = 0` 필드를 추가하여, hydrate 시 해당 `ExecutionStep.step` 번호를 저장한다.
`_build_tool_execution_history()`는 `ExecutionStep` 목록을 순회하며,
`source_step == step.step`인 엔티티들을 필터링하여 **스텝별 정확한 관련성 집계**를 생성한다.

```python
# 예시: 스텝 2의 테이블 관련성 집계
step_tables = [t for t in reason.explored_tables if t.source_step == step.step]
selected = [t for t in step_tables if t.selection_status == SelectionStatus.SELECTED]
```

이 방식의 장점:
- 중복 저장 없음 (관련성 정보의 single source of truth 유지)
- 스텝별 정확한 집계 가능 (어떤 스텝에서 어떤 엔티티가 발견되었는지 명확)
- 기존 state 모델 변경 최소화
- `discovered_facts` 리스트와 `exploration_history` 빌더 함수를 별도 유지할 필요 없음

---

## 6. context_retriever 변경

### 6.1 `seen_ids` dict 수집 — batch 실행 전 1회

`context_retriever_node`에서 batch 실행 전에 한 번만 수집하여 `seen_ids` dict로 `_run_step`에 전달한다.
`_run_step` 내부에서는 `seen_ids.get(step.tool)`로 해당 도구의 exclude_ids를 조회한다.

```python
# context_retriever_node 내부 — batch 실행 전 1회 수집
seen_ids: dict[str, list[str]] = {
    "search_use_cases": [uc.point_id for uc in reason.explored_use_cases if uc.point_id],
    "search_manual": [bm.point_id for bm in reason.explored_biz_manuals if bm.point_id],
}
```

> **query 단위 분리를 하지 않는 이유**: Qdrant point id는 컬렉션 내에서 유니크하다.
> "여신"으로 검색한 결과와 "대출"로 검색한 결과에 같은 point가 포함될 수 있다.
> query와 무관하게 이미 본 모든 point를 제외해야 진정한 "새로운 결과"를 얻는다.

### 6.2 `_run_step` 변경

`seen_ids` dict를 받아서 Qdrant 도구 호출 시 exclude_ids를 전달한다.

```python
async def _run_step(
    step: Any,
    executed_tool_keys: set[str],
    explored_tables: list[TableMeta],
    code_map: dict[str, CodeMeta] | None = None,
    seen_ids: dict[str, list[str]] | None = None,
) -> tuple[Any, Any, int]:
    # ... 기존 로직 ...
    
    try:
        exclude_ids = (seen_ids or {}).get(step.tool) or None
        if exclude_ids:
            result = await execute_tool(
                step.tool, step.input, exclude_ids=exclude_ids,
            )
        else:
            result = await execute_tool(step.tool, step.input)

        # ... 기존 후처리 (변경 없음) ...
        # _accumulate_qdrant_seen_ids 호출 불필요 —
        # point_id는 context_interpreter의 hydrate 단계에서
        # explored_use_cases/explored_biz_manuals에 자동 저장된다.

    except Exception as e:
        # ... 기존 에러 처리 동일 ...
```

**기존 `_run_step` 대비 변경점**:
- `qdrant_seen_ids` 파라미터 **삭제** → `seen_ids: dict[str, list[str]]` 파라미터 추가
- `_accumulate_qdrant_seen_ids()` 호출 **삭제** — hydrate에서 자동 저장되므로 불필요

### 6.3 `context_retriever_node` 변경

```python
async def context_retriever_node(state: PipelineState) -> dict:
    reason = state["reason"]
    # ... 기존 로컬 복사 ...

    # ... pending 스텝 필터링 ...

    # batch 실행 전 1회: seen_ids 수집
    seen_ids: dict[str, list[str]] = {
        "search_use_cases": [uc.point_id for uc in reason.explored_use_cases if uc.point_id],
        "search_manual": [bm.point_id for bm in reason.explored_biz_manuals if bm.point_id],
    }

    # 병렬 실행 시 seen_ids 전달
    tasks = [
        _run_step(
            step, executed_tool_keys, explored_tables,
            code_map, seen_ids,
        )
        for step in pending
    ]
    results = await asyncio.gather(*tasks)

    # ... 기존 집계 ...

    # 반환값에 qdrant_seen_ids 불필요 — explored_*에 이미 포함
    return {
        "reason": {
            # ... 기존 반환 필드 ...
        }
    }
```

> **주의**: `_run_step` 시점에는 **아직 hydrate가 실행되지 않았다**.
> `explored_use_cases`에 `point_id`가 저장되는 것은 다음 노드인
> `context_interpreter`의 `_hydrate_use_cases_from_raw` 단계이다.
>
> 따라서 **같은 batch 내 첫 번째 search_use_cases 호출에서는 exclude_ids가 비어있고**,
> hydrate → recovery → 다음 batch 실행 시에야 이전 결과의 point_id가 수집된다.
> 이는 의도된 동작이다: 같은 batch에서 같은 Qdrant 도구를 2번 실행하는 것은
> `executed_tool_keys` 중복 방지로 이미 차단되므로 문제없다.
>
> **흐름 요약**:
> ```
> reasoning_preparer: _build_execution_plan
>   → ExecutionStep(tool="search_use_cases", input="여신, page=1")
>   ↓
> batch 1: search_use_cases("여신, page=1") → 5건 반환 → raw_result에 _point_id 포함
>   → executed_tool_keys에 "search_use_cases:여신, page=1" 기록
>   ↓
> context_interpreter: hydrate → explored_use_cases에 point_id 저장
>   ↓
> recovery_agent: tool_execution_history에서 "page=1 실행 완료" 확인
>   → replan: ExecutionStep(tool="search_use_cases", input="여신, page=2")
>   ↓
> batch 2: seen_ids dict → explored_use_cases에서 5건의 point_id 수집
>          → execute_tool(exclude_ids=[5건]) → 새로운 5건 반환
>   → executed_tool_keys에 "search_use_cases:여신, page=2" 기록
> ```

---

## 6b. reasoning_preparer 변경

### 6b.1 `_build_execution_plan` — 검색 도구 input에 `page=1` 부기

**위치**: `src/agents/nodes/reason/reasoning_preparer.py`

`reasoning_preparer`는 룰베이스로 `ExecutionStep.input`을 직접 구성한다.
모든 검색 도구의 input에 `, page=1`을 명시하여
`executed_tool_keys`와 `{tool_execution_history}`에서 일관된 page 표기를 보장한다.

```python
def _build_execution_plan(
    knowledge_items: list[KnowledgeItem],
    executed_tool_keys: set[str],
    nq: Any,
    original_query: str = "",
) -> list[ExecutionStep]:
    steps: list[ExecutionStep] = []
    step_num = 1

    # (1) 유사 SQL 조회
    if original_query:
        steps.append(ExecutionStep(
            step=step_num,
            tool="search_use_cases",
            input=f"{original_query}, page=1",         # ← page=1 부기
            purpose="유사 SQL 조회 → 관련 테이블 메타 + 코드 자동 수집",
        ))
        step_num += 1

    # (2) 8-slot 키워드로 테이블 메타 검색 (키워드 탐색)
    meta_query = _extract_meta_search_query(nq, original_query)
    tool_key = f"search_table_meta:{meta_query}, page=1"   # ← 키워드 탐색 + page=1
    if meta_query and tool_key not in executed_tool_keys:
        steps.append(ExecutionStep(
            step=step_num,
            tool="search_table_meta",                  # ← 키워드 탐색 도구
            input=f"{meta_query}, page=1",
            purpose="8-slot 키워드 기반 테이블 메타 검색",
        ))
        step_num += 1

    # (3) UNRESOLVED filter 컬럼의 코드 메타 조회
    for ki in knowledge_items:
        if ki.status == ConfidenceStatus.UNRESOLVED and "filter:" in ki.key:
            col_name = ki.key.split(":")[1].split("=")[0]
            steps.append(ExecutionStep(
                step=step_num,
                tool="lookup_code_meta",               # ← 컬럼명 지정 조회
                input=f"{col_name}, page=1",
                purpose=f"{col_name}의 코드값 확인",
            ))
            step_num += 1

    return steps
```

**기존 대비 변경점**: 각 `ExecutionStep.input`에 `, page=1` 접미사 추가 (3곳).
`executed_tool_keys` 중복 체크 키도 `page=1` 포함 형태로 변경 (1곳).

> **recovery_agent와의 일관성**: recovery_agent(LLM)가 생성하는 execution_plan에서도
> `"여신, page=2"` 형태를 사용하므로, 최초 호출과 재시도 모두 동일한 page= 형식이 된다.
>
> ```
> executed_tool_keys 이력:
>   "search_table_meta:여신, page=1"      ← reasoning_preparer (룰베이스, 키워드 탐색)
>   "search_table_meta:여신, page=2"      ← recovery_agent (LLM, 키워드 탐색)
>   "lookup_table_meta:TB_ADW_LNB301M"    ← enrichment 또는 recovery (테이블명 지정, page 없음)
> ```

---

## 7. recovery_agent 프롬프트 변경

### 7.1 `{exploration_history}` + `{discovered_facts}` → `{tool_execution_history}` 통합

**문제**: 기존 `{exploration_history}`는 `explored_use_cases`만 표시하고,
`{discovered_facts}`는 DONE+insight인 스텝만 나열한다.
두 섹션이 search_use_cases 결과를 중복 표시하며, 테이블/비즈메타의 관련성 평가 정보가 빠져 있다.

**해결**: 세 변수(`{exploration_history}`, `{discovered_facts}`, 기존 `{tool_execution_history}`)를
**단일 `{tool_execution_history}`로 통합**한다.

삭제되는 프롬프트 변수:
- `{exploration_history}` — `_build_exploration_history()` 함수 제거
- `{discovered_facts}` — `_build_discovered_facts()` 함수 제거

유지되는 프롬프트 변수:
- `{explored_tables_summary}` — 테이블 상세 (컬럼, 날짜분포 등)는 별도 유지
- `{sample_data_summary}` — 샘플 데이터 요약은 별도 유지

### 7.2 `_build_tool_execution_history()` 구현

```python
def _build_tool_execution_history(reason: ReasoningState) -> str:
    """ExecutionStep + 각 엔티티 state의 관련성 평가를 동적 집계한다."""
    lines: list[str] = []

    # ── 관련성 요약 매핑 (도구별 대응 state) ──
    _RELEVANCE_BUILDERS: dict[str, Callable] = {
        "search_use_cases": _summarize_use_cases,
        "search_table_meta": _summarize_tables,
        "lookup_table_meta": _summarize_tables,
        "search_biz_terms": _summarize_biz_terms,
        "search_manual": _summarize_biz_manuals,
    }

    for step in reason.execution_steps:
        if step.status == StepStatus.SKIPPED:
            continue

        status_mark = "✓" if step.status == StepStatus.DONE else "✗"
        lines.append(f"[스텝 {step.step}] {status_mark} {step.tool}(\"{step.input}\")")

        # 결과 건수 (raw_result가 clear 되었을 수 있으므로 안전 처리)
        if step.raw_result:
            count = _count_results(step.raw_result)
            lines.append(f"  결과: {count}건")

        # 관련성 요약 (source_step으로 해당 스텝의 엔티티만 필터)
        builder = _RELEVANCE_BUILDERS.get(step.tool)
        if builder:
            summary = builder(reason, step.step)
            if summary:
                lines.append(f"  관련성: {summary}")

        # insight (context_interpreter가 채운 값)
        if step.insight:
            lines.append(f"  발견: {step.insight}")

    return "\n".join(lines) or "(실행 이력 없음)"
```

### 7.3 관련성 요약 헬퍼 함수

```python
def _summarize_use_cases(reason: ReasoningState, step_num: int) -> str:
    """search_use_cases 결과의 관련성 요약 (해당 스텝에서 발견된 것만)."""
    step_ucs = [uc for uc in reason.explored_use_cases if uc.source_step == step_num]
    relevant = [uc for uc in step_ucs if uc.relevant]
    not_relevant = [uc for uc in step_ucs if not uc.relevant and uc.eval_reason]
    if not relevant and not not_relevant:
        return ""
    parts: list[str] = []
    if relevant:
        descs = ", ".join(f'"{uc.description[:30]}"' for uc in relevant[:3])
        parts.append(f"관련 {len(relevant)}건({descs})")
    if not_relevant:
        parts.append(f"비관련 {len(not_relevant)}건")
    return ", ".join(parts)


def _summarize_tables(reason: ReasoningState, step_num: int) -> str:
    """search_table_meta/lookup_table_meta 결과의 관련성 요약 (해당 스텝에서 발견된 것만)."""
    step_tables = [t for t in reason.explored_tables if t.source_step == step_num]
    selected = [t for t in step_tables if t.selection_status == SelectionStatus.SELECTED]
    rejected = [t for t in step_tables if t.selection_status == SelectionStatus.REJECTED]
    pending = [t for t in step_tables if t.selection_status == SelectionStatus.PENDING]
    if not selected and not rejected:
        return ""
    parts: list[str] = []
    if selected:
        names = ", ".join(t.table_name for t in selected[:3])
        parts.append(f"SELECTED {len(selected)}건({names})")
    if rejected:
        parts.append(f"REJECTED {len(rejected)}건")
    if pending:
        parts.append(f"PENDING {len(pending)}건")
    return ", ".join(parts)


def _summarize_biz_manuals(reason: ReasoningState, step_num: int) -> str:
    """search_manual 결과의 관련성 요약 (해당 스텝에서 발견된 것만)."""
    step_manuals = [m for m in reason.explored_biz_manuals if m.source_step == step_num]
    selected = [m for m in step_manuals if m.selection_status == SelectionStatus.SELECTED]
    rejected = [m for m in step_manuals if m.selection_status == SelectionStatus.REJECTED]
    if not selected and not rejected:
        return ""
    parts: list[str] = []
    if selected:
        parts.append(f"SELECTED {len(selected)}건")
    if rejected:
        parts.append(f"REJECTED {len(rejected)}건")
    return ", ".join(parts)


def _summarize_biz_terms(reason: ReasoningState, step_num: int) -> str:
    """search_biz_terms 결과의 관련성 요약 (해당 스텝에서 발견된 것만)."""
    step_terms = [t for t in reason.explored_biz_terms if t.source_step == step_num]
    selected = [t for t in step_terms if t.selection_status == SelectionStatus.SELECTED]
    rejected = [t for t in step_terms if t.selection_status == SelectionStatus.REJECTED]
    if not selected and not rejected:
        return ""
    parts: list[str] = []
    if selected:
        names = ", ".join(t.term for t in selected[:3])
        parts.append(f"SELECTED {len(selected)}건({names})")
    if rejected:
        parts.append(f"REJECTED {len(rejected)}건")
    return ", ".join(parts)


def _count_results(raw_result: Any) -> int:
    """raw_result에서 결과 건수를 추출한다."""
    if isinstance(raw_result, list):
        return len(raw_result)
    if isinstance(raw_result, dict):
        # search_use_cases: {"use_cases": [...]}
        for key in ("use_cases", "results", "items"):
            if key in raw_result and isinstance(raw_result[key], list):
                return len(raw_result[key])
    return 0
```

### 7.4 프롬프트 출력 예시

```
[스텝 1] ✓ search_use_cases("여신 실행 건수, page=1")
  결과: 5건
  관련성: 관련 2건("여신 실행건수 월별 집계", "대출 실행 현황 조회"), 비관련 3건
  발견: TB_ADW_LNB301M 사용 패턴 확인
[스텝 2] ✓ search_table_meta("여신 원장, page=1")
  결과: 10건
  관련성: SELECTED 3건(TB_ADW_LNB301M, TB_ADW_LNB302M, TB_ADW_LNB303M), REJECTED 5건, PENDING 2건
  발견: 여신기본 테이블 후보 3건 확인, 일별 원장은 미발견
[스텝 3] ✓ lookup_code_meta("여신상태코드")
  결과: 8건
  발견: LN_STAT_CD 값 매핑 확인 (01:정상, 02:연체, 03:상환완료)
[스텝 4] ✓ get_column_values("TB_ADW_LNB301M, LN_STAT_CD")
  발견: 실제 DB 값 01, 02, 03, 04, 09 확인
[스텝 5] ✗ search_table_meta("여신 일별, page=1")
  결과: 0건
```

### 7.5 프롬프트 템플릿 변경

`resources/prompts/reason/recovery_agent_system.txt`에서:

**삭제할 섹션**:
```
## 유사 SQL 탐색 이력
{exploration_history}
```
```
## 발견 사항
{discovered_facts}
```

**추가할 섹션** (기존 `{explored_tables_summary}` 앞에 배치):
```
## 도구 실행 이력
{tool_execution_history}
  위는 지금까지의 모든 도구 실행 결과입니다.
  - 이미 실행한 검색을 반복하지 마세요.
  - 결과가 부족했다면 page=N으로 다음 페이지를 조회하세요.
  - 관련성이 낮은 결과가 많다면 키워드를 변경하세요.
```

### 7.6 도구 설명에 페이징 안내 추가

**위치**: `resources/prompts/reason/recovery_agent_system.txt`의 "사용 가능한 도구" 섹션

```
## 도구 페이징

이전 검색에서 충분한 결과를 얻지 못했다면, 같은 검색어로 다음 페이지를 조회할 수 있다.
input에 "page=N"을 추가하면 N번째 페이지의 결과를 반환한다.

예시:
  - search_table_meta("여신, page=2") → "여신" 키워드 검색 결과의 11~20번째
  - search_use_cases("여신 실행 건수, page=2") → 유사 SQL 이력의 추가 결과

주의:
  - 모든 검색에 page=1을 명시하라. 다음 결과가 필요하면 page=2로 증가시킨다.
  - lookup_table_meta(영문 테이블명)는 단건 조회이므로 page 불필요.
  - page=3 이상은 일반적으로 불필요하다. 키워드를 변경하는 것이 더 효과적이다.
  - get_sample_rows, get_date_distribution, get_column_profile은 page를 지원하지 않는다.
  - 특정 컬럼의 값을 더 보고 싶다면 get_column_values를 사용하라.
```

### 7.7 few-shot 예시 추가

기존 5개 예시 다음에 페이징 활용 예시를 추가한다.

```
## 예시 6: 페이징을 활용한 추가 탐색

상황: search_table_meta("여신, page=1")으로 10건을 확인했으나 일별 원장을 찾지 못함.
분석: 상위 10건은 모두 월별 집계 테이블이었다. 11번째 이후에 일별 원장이 있을 수 있다.

{
  "analysis": "테이블 메타 검색 상위 10건이 모두 월별 집계 테이블. 일별 원장은 다음 페이지에 있을 가능성.",
  "lessons_learned": "여신 관련 테이블이 10건 이상 존재. 페이징으로 추가 확인 필요.",
  "action": "replan",
  "execution_plan": [
    {"tool": "search_table_meta", "input": "여신, page=2", "purpose": "여신 관련 테이블 11~20번째 확인"}
  ]
}
```

### 7.8 도구 목록 리라이트

**위치**: `recovery_agent_system.txt` L44-55

**변경 전**:
```
## 사용 가능한 도구 (execution_plan에서 사용 가능)
- search_table_meta(query): 테이블/컬럼 메타데이터 검색
- search_code_meta(column_name): 코드값 매핑 조회
- search_manual(query): 업무 매뉴얼 검색
- search_biz_terms(term): 금융 용어사전 조회
- get_sample_rows(table_name, schema_name?, db_source?, limit?): 샘플 데이터 조회
- get_date_distribution(table_name, date_column, schema_name?, db_source?): 날짜 컬럼 분포 조회 (MIN/MAX, 최근값 10개, 포맷 유형)
- search_column_values(table_name, column_name, keyword): 특정 컬럼을 키워드 LIKE 검색하여 실제 해당 컬럼 값을 distinct 출력 (필터 컬럼 값 탐색)
- get_column_profile(table_name, column_name): 컬럼 통계 조회 (총건수, NULL이 아닌 건수, 고유값수, NULL비율(%), MIN/MAX)
- search_use_cases(query): 과거 유사 SQL 벡터 검색 (의미 + 키워드 하이브리드)
  ※ 저장된 데이터는 SQL의 서술형 설명이므로 "~을 조회한다", "~을 집계하여 조회한다" 등 서술형 형태의 문장으로 검색해야 매칭 품질이 높음
  ※ 짧은 키워드("연체율")보다 서술형 문장("부서별 대출 연체율을 연체금액 대비 대출잔액으로 산출하여 조회")이 효과적임
  ※ 핵심 업무 용어와 테이블/컬럼 관련 키워드를 문장에 포함시키면 키워드 매칭도 함께 작동함
```

**변경 후**:
```
## 사용 가능한 도구 (execution_plan에서 사용 가능)

lookup 도구 (이름/키를 알고 있는 지정 조회):
- lookup_table_meta(table_name): 영문 테이블명으로 단일 테이블 메타 조회 (page 미지원)
- lookup_code_meta(column_name): 코드 컬럼명으로 코드값 매핑 조회 (page 지원)

search 도구 (키워드/의미 기반 탐색, page 지원):
- search_table_meta(query, page=N): 업무 키워드로 테이블/컬럼 메타 검색
- search_use_cases(query, page=N): 과거 유사 SQL 벡터 검색 (의미 + 키워드 하이브리드)
  ※ 저장된 데이터는 SQL의 서술형 설명이므로 "~을 조회한다", "~을 집계하여 조회한다" 등 서술형 형태의 문장으로 검색해야 매칭 품질이 높음
  ※ 짧은 키워드("연체율")보다 서술형 문장("부서별 대출 연체율을 연체금액 대비 대출잔액으로 산출하여 조회")이 효과적임
  ※ 핵심 업무 용어와 테이블/컬럼 관련 키워드를 문장에 포함시키면 키워드 매칭도 함께 작동함
- search_manual(query, page=N): 업무 매뉴얼 검색
- search_biz_terms(term, page=N): 금융 용어사전 검색

get 도구 (DB에서 데이터 직접 가져오기):
- get_sample_rows(table_name): 샘플 데이터 10건 조회 (page 미지원)
- get_column_values(table_name, column_name, keyword, page=N): 특정 컬럼을 키워드 LIKE 검색하여 실제 해당 컬럼 값을 distinct 출력 (필터 컬럼 값 탐색)
- get_column_profile(table_name, column_name): 컬럼 통계 조회 — 총건수, NULL이 아닌 건수, 고유값수, NULL비율(%), MIN/MAX (page 미지원)
- get_date_distribution(table_name, date_column): 날짜 컬럼 분포 조회 — MIN/MAX, 최근값 10개, 포맷 유형 (page 미지원)
```

### 7.9 우선순위 가이드 재구성

**위치**: `recovery_agent_system.txt` L57-66

**변경 전**:
```
## 도구 우선순위 가이드
1. search_table_meta: 테이블/컬럼 구조 확인 (SQL 생성에 직접적 힌트, 현재 탐색된 테이블만으로 SQL 생성이 불가능하다고 판단될 때)
2. get_sample_rows: 실제 데이터 패턴 확인 (일부 데이터 조회 및 확인이 필요할 때)
3. search_code_meta: 코드 컬럼의 값-설명 매핑 확인 (코드 컬럼 해석이 필요할 때)
4. search_column_values: 일반 컬럼의 필터 값 탐색 (지점명, 상품명 등 필터 컬럼이 텍스트여서 값 조회가 필요할 때)
5. get_column_profile: 컬럼 통계 확인 (NULL율, 카디널리티, 값 범위 — 0건 원인 진단)
6. get_date_distribution: 시간 조건이 있는 질의에서 날짜 컬럼의 데이터 범위·적재 주기 확인 (시간 조건 질의 시 우선도 상향)
7. search_use_cases: 유사한 과거 SQL 벡터 검색 (서술형 문장으로 검색할 것)
8. search_biz_terms: 금융 용어 정의 확인 (용어사전이 부실하여 결과가 없을 수 있음)
9. search_manual: 업무 프로세스 확인 (SQL 추론에 간접적 참고만 됨)
```

**변경 후**:
```
## 도구 우선순위 가이드
1. search_table_meta: 테이블을 찾아야 할 때 (키워드 기반, page로 추가 결과 조회 가능)
2. lookup_table_meta: 특정 테이블의 상세 정보가 필요할 때 (영문명 지정)
3. get_sample_rows: 테이블의 전반적 데이터 패턴 빠른 확인
4. lookup_code_meta: 코드 컬럼의 값-설명 매핑 필요 시
5. get_column_values: 필터 컬럼의 실제 값 탐색 (텍스트 컬럼, page 지원)
6. get_column_profile: 컬럼 통계 (NULL율, 카디널리티 — 0건 원인 진단)
7. get_date_distribution: 날짜 컬럼 범위·적재 주기 확인 (시간 조건 질의 시 우선도 상향)
8. search_use_cases: 유사 과거 SQL 검색 (서술형 문장으로)
9. search_biz_terms: 금융 용어 정의 (용어사전이 부실할 수 있음)
10. search_manual: 업무 프로세스 (간접 참고)
```

### 7.10 input 형식 규칙 갱신

**위치**: `recovery_agent_system.txt` L68-74

**변경 전**:
```
### 도구 input 형식 규칙
- 단일 파라미터 도구: "input": "검색어" | "input": "테이블명"
- 복수 파라미터 도구: 쉼표(,)로 구분하여 순서대로 입력
  - get_sample_rows: "input": "테이블명"
  - get_date_distribution: "input": "테이블명,날짜컬럼명"
  - search_column_values: "input": "테이블명,컬럼명,키워드"
  - get_column_profile: "input": "테이블명,컬럼명"
```

**변경 후**:
```
### 도구 input 형식 규칙
- lookup 도구: "input": "이름" (예: "TB_ADW_LNB301M", "LN_DCD")
- search 도구: "input": "검색어, page=N" (예: "여신, page=1")
  - page를 생략하면 page=1로 동작. 다음 결과가 필요하면 page=2로 증가.
- get 도구 (복수 파라미터): 쉼표(,)로 구분하여 순서대로 입력
  - get_sample_rows: "input": "테이블명"
  - get_date_distribution: "input": "테이블명,날짜컬럼명"
  - get_column_values: "input": "테이블명,컬럼명,키워드" 또는 "테이블명,컬럼명,키워드,page=N"
  - get_column_profile: "input": "테이블명,컬럼명"
```

### 7.11 기존 few-shot 예시 도구명 갱신

**위치**: `recovery_agent_system.txt` 예시 1 (L105-120), 예시 3 (L147-162)

**예시 1 변경점** (L117):
```
변경 전: {"tool": "search_code_meta", "input": "LN_DCD", "purpose": "대출구분코드의 코드값 목록 확인"},
변경 후: {"tool": "lookup_code_meta", "input": "LN_DCD", "purpose": "대출구분코드의 코드값 목록 확인"},
```

**예시 3 변경점** (L159-160):
```
변경 전:
    {"tool": "search_column_values", "input": "TB_ADW_COM001M,BR_NM,서울", "purpose": "지점명에 '서울'이 포함된 실제 값 목록 조회"},
    {"tool": "search_code_meta", "input": "RGN_CD", "purpose": "지역코드 컬럼이 있다면 코드값 매핑 확인"}

변경 후:
    {"tool": "get_column_values", "input": "TB_ADW_COM001M,BR_NM,서울", "purpose": "지점명에 '서울'이 포함된 실제 값 목록 조회"},
    {"tool": "lookup_code_meta", "input": "RGN_CD", "purpose": "지역코드 컬럼이 있다면 코드값 매핑 확인"}
```

> **참고**: 예시 2, 4, 5는 도구명 변경 대상이 없으므로 무변경.

### 7.12 지시 섹션 문구 갱신

**위치**: `recovery_agent_system.txt` L96

**변경 전**:
```
3. 유사 SQL 탐색 이력과 누적 인사이트를 참고하여 이미 탐색한 경로를 반복하지 마세요.
```

**변경 후**:
```
3. 도구 실행 이력을 참고하여 이미 탐색한 경로를 반복하지 마세요. page=N으로 다음 페이지를 조회할 수 있습니다.
```

### 7.13 context_interpreter_system.txt 변경 상세

**위치**: `resources/prompts/reason/context_interpreter_system.txt`

**변경 1 — 필드 포함 규칙** (L142):
```
변경 전: - explored_tables: search_use_cases, search_table_meta 스텝에서 항상 포함 (결과 없으면 빈 배열)
변경 후: - explored_tables: search_use_cases, search_table_meta, lookup_table_meta 스텝에서 항상 포함 (결과 없으면 빈 배열)
```

**변경 2 — 관찰 도구 목록** (L99, L146):
```
변경 전: 관찰 도구(get_sample_rows, get_date_distribution, search_column_values, get_column_profile)
변경 후: 관찰 도구(get_sample_rows, get_date_distribution, get_column_values, get_column_profile)
```
(L99과 L146 두 곳 모두 `search_column_values` → `get_column_values`로 변경)

**변경 3 — few-shot 예시 확인**:
현재 예시에는 `search_table_meta`, `search_use_cases`, `search_biz_terms` 도구만 사용되어 있어
도구명 변경 대상이 없음. 단, `lookup_table_meta`가 사용되는 예시가 추가될 경우 대응 필요.

---

## 8. config.py 변경

### 8.1 신규 설정 항목

```python
# src/config.py — Settings 클래스에 추가

# MongoDB — biz_term 기본 limit (기존에는 limit 없었음)
mongo_biz_term_size: int = 20

# Qdrant — prefetch 상한 (exclude_ids 누적 시 무한 증가 방지)
qdrant_max_prefetch: int = 100

# Qdrant — search_manual limit 상한 (exclude_ids 누적 시 무한 증가 방지)
qdrant_manual_max_limit: int = 30
```

### 8.2 기존 설정 — 변경 없음

| 설정 | 현재 값 | 용도 | 변경 |
|------|---------|------|------|
| `mongo_table_meta_size` | 10 | lookup_table_meta / search_table_meta limit (= page_size) | 없음 |
| `mongo_code_meta_size` | 10 | lookup_code_meta limit (= page_size) | 없음 |
| `qdrant_sql_history_top_k` | 5 | search_sql_history rerank 후 반환 건수 | 없음 |
| `qdrant_sql_history_prefetch_limit` | 20 | search_sql_history prefetch 기본값 | 없음 |
| `qdrant_search_top_k` | 3 | search_manual 반환 건수 | 없음 |

---

## 9. 죽은 코드 정리

### 9.1 tools.py — 어댑터 패턴 통일

방안 C(어댑터 분리)를 채택하여, 원본 함수의 시그니처는 **유지**된다.
`lookup_table_meta(table_name: str)`, `search_table_meta(keywords: str, page: int = 1)`,
`lookup_code_meta(column_name: str, page: int = 1)` 등의 원본 시그니처가 보존되므로,
enrichment 등 직접 호출하는 외부 코드에 영향 없음.

TOOL_MAP에는 `_tool_` 접두사 어댑터만 등록되며,
어댑터가 `tool_input: str`을 파싱하여 원본 함수에 전달한다.

확인 사항:
- enrichment에서 `lookup_table_meta(t)` 직접 호출 → 호환 (시그니처 유지)
- enrichment에서 `lookup_code_meta(col)` 직접 호출 → 호환 (시그니처 유지)
- `seed_sql_history.py`에서 `mongo.search_table_meta()` 커넥터 직접 호출 → 무관 (커넥터 메서드명 무변경)

### 9.2 기존 search 도구의 docstring 파라미터 설명

기존 docstring에서 `query: str`, `column_name: str`, `term: str` 설명을
`tool_input: str` 기반으로 업데이트해야 한다.

### 9.3 제거 대상 없음

이 변경에서 기존 함수/클래스/설정을 제거하는 항목은 없다.
신규 추가만 있고, 기존 코드의 시그니처를 변경하는 것이 전부이다.

---

## 10. 파일별 변경 매트릭스

### 10.1 소스 코드

| # | 파일 | 변경 유형 | 내용 |
|---|------|----------|------|
| 1 | `src/config.py` | 수정 | `mongo_biz_term_size`, `qdrant_max_prefetch`, `qdrant_manual_max_limit` 설정 추가 |
| 2 | `src/agents/state/state.py` | 수정 | `UseCaseEntry`/`BizManualEntry`에 `point_id: str` + `source_step: int` 추가, `TableMeta`/`BizTermEntry`에 `source_step: int` 추가 |
| 3 | `src/connectors/impl/mongo_connector.py` | 수정 | search_table_meta/code_meta/biz_terms에 `page` kwargs + `$sort` 안정화 + `$skip` + 파이프라인 순서 변경 |
| 4 | `src/connectors/impl/qdrant_connector.py` | 수정 | search_sql_history/manual에 `exclude_ids` + `effective_prefetch` 보정 + `str(point.id)` → `_point_id` 반환 |
| 5 | `src/agents/nodes/reason/tools.py` | **대폭 수정** | `lookup_table_meta` + `search_table_meta` 분리, `search_code_meta` → `lookup_code_meta` 이름 변경, `search_column_values` → `get_column_values` 리네이밍, 전 검색 도구 어댑터(`_tool_*`) 추가, `_extract_page` 통합 헬퍼, `_TABLE_META_TOOLS` 상수, `execute_tool` **kwargs (방안 A), TOOL_MAP 재구성, `__all__` 갱신 |
| 6 | `src/agents/nodes/reason/tool_renderers.py` | 수정 | `_TOOL_RENDERERS`에 `"lookup_table_meta"` 추가 (같은 렌더러 `_render_table_meta` 공유), `"search_code_meta"` → `"lookup_code_meta"` 변경, `"search_column_values"` → `"get_column_values"` 키 변경, 헤더 문자열에 `step.tool` 동적 사용 |
| 7 | `src/agents/nodes/reason/context_retriever.py` | 수정 | _run_step에 `seen_ids` dict 파라미터 추가, `context_retriever_node`에서 `seen_ids` 수집, `_DB_QUERY_TOOLS`에 `"get_column_values"` 반영, `step.tool` 분기 조건에 `_TABLE_META_TOOLS` 사용 (4곳: L109, L113, L531 + enrichment 키) |
| 8 | `src/agents/nodes/reason/context_interpreter.py` | 수정 | `_hydrate_*_from_raw`에 `point_id` 저장 (2곳), `_OBS_DISPATCHERS`에 `"get_column_values"` 키 변경, `step.tool` 분기에 `_TABLE_META_TOOLS` 사용 (L833) |
| 9 | `src/agents/nodes/reason/reasoning_preparer.py` | 수정 | `_build_execution_plan`에서 키워드 탐색을 `search_table_meta` + `, page=1`로 변경, 코드 조회를 `lookup_code_meta`로 변경, 전 검색 도구 input에 `, page=1` 부기 |
| 10 | `src/agents/nodes/reason/recovery_agent.py` | **대폭 수정** | `_build_exploration_history()` 제거, `_build_discovered_facts()` 제거, `_build_tool_execution_history()` 신규 (ExecutionStep + 엔티티 state 관련성 동적 집계), 관련성 요약 헬퍼 4개 추가 (`_summarize_use_cases`, `_summarize_tables`, `_summarize_biz_manuals`, `_summarize_biz_terms`), `_build_prompt`에서 `{exploration_history}`/`{discovered_facts}` → `{tool_execution_history}` 교체 |
| 11 | `src/utils/tracker/trace_analyzer.py` | 수정 | 테이블 메타 검색 누락 체크에 `_TABLE_META_TOOLS` 조건 사용 (L228) |

### 10.2 프롬프트

| # | 파일 | 내용 |
|---|------|------|
| 12 | `resources/prompts/reason/recovery_agent_system.txt` | 도구 목록에 `lookup_table_meta`/`search_table_meta`/`lookup_code_meta` 분리 설명, `search_column_values` → `get_column_values` 도구명 변경 반영, 우선순위 가이드 수정, input 형식 규칙 수정, 페이징 안내 + few-shot 추가, `{exploration_history}`/`{discovered_facts}` 삭제 → `{tool_execution_history}` 통합 섹션 교체 |
| 13 | `resources/prompts/reason/context_interpreter_system.txt` | 필드 규칙에 `lookup_table_meta` 추가 (L142), `search_column_values` → `get_column_values` 도구명 변경 반영, few-shot 예시 확인/수정 (도구명) |

### 10.3 테스트 (확인/수정 필요)

| # | 파일 | 확인 포인트 |
|---|------|-----------|
| 14 | `tests/auto/unit/test_connectors.py` | 커넥터 직접 테스트 — **무변경** (커넥터 메서드명 동일) |
| 15 | `tests/auto/unit/test_recovery_agent.py` | 예시의 도구명 → 문맥별 `lookup_table_meta`/`search_table_meta`/`lookup_code_meta` 판단 |
| 16 | `tests/auto/unit/test_simplify_changes.py` | step 도구명 확인 |
| 17 | `tests/auto/unit/test_three_aspect_enrichment.py` | enrichment 테스트 — `lookup_table_meta` 사용 |
| 18 | `tests/auto/e2e/test_agentic_core.py` | 도구명 문맥 확인 (`lookup_table_meta` / `search_table_meta` 구분) |
| 19 | `tests/auto/e2e/test_agentic_e2e.py` | `executed_tool_keys` 키 형식 수정 필요 |
| 20 | `tests/auto/e2e/test_agentic_flow_trace.py` | 동일 |
| 21 | `tests/manual/e2e/test_agentic_real_e2e.py` | 실 e2e 도구명 확인 |
| 22 | `tests/manual/e2e/test_connector_real.py` | 커넥터 직접 테스트 — **무변경** |
| 23 | `tests/auto/unit/test_qualify_table.py` | `search_column_values` → `get_column_values` 도구명 갱신 |
| 24 | `tests/auto/unit/test_tool_renderers.py` | `"search_column_values"` → `"get_column_values"` 렌더러 키 갱신 |
| 25 | `tests/auto/unit/test_dummy_data.py` | `search_code_meta` (9건) → `lookup_code_meta`로 갱신 |
| 26 | `tests/auto/unit/test_insight_builder.py` | `search_code_meta`, `search_column_values` → `lookup_code_meta`, `get_column_values` 도구명 갱신 |
| 27 | `tests/auto/unit/test_reasoning_preparer.py` | `search_code_meta` 스텝 생성 검증 → `lookup_code_meta` + `, page=1` 갱신 |

### 10.4 변경 불필요

| 파일 | 이유 |
|------|------|
| `src/connectors/impl/elasticsearch_connector.py` | 미사용 (하위 호환 보존) |
| `src/tools/seed_sql_history.py` | 커넥터 직접 호출 (`mongo.search_table_meta`) — 커넥터 메서드명 무변경 |
| `src/agents/state/state.py` (주석) | line 141-142, 162의 `search_column_values` 참조 주석 → `get_column_values`로 갱신. 필드 자체는 무변경 |
| `tests/manual/e2e/test_search_es_schema.py` | ES 커넥터 직접 호출 (`search_code_meta`) — 커넥터 메서드명 무변경 |
| `docs/` | 설계 문서만 갱신 |

---

## 11. 검증 계획

| 테스트 | 검증 내용 | 범위 |
|--------|----------|------|
| `test_lookup_table_meta_exact_match` | `lookup_table_meta("TB_ADW_LNB301M")` → `$in` 경로, 1건 반환 | unit |
| `test_search_table_meta_regex` | `search_table_meta("여신")` → regex 경로, N건 반환 | unit |
| `test_search_table_meta_pagination` | page=1, page=2 호출 시 다른 결과 반환, 중복 없음 확인 | unit |
| `test_mongo_sort_stability` | 동일 쿼리 반복 호출 시 정렬 순서 일관성 (`_id` tiebreaker) | unit |
| `test_mongo_pipeline_order` | `$sort/$skip/$limit`이 `$project` 이전에 위치하는지 파이프라인 검증 | unit |
| `test_qdrant_exclude_ids` | `exclude_ids` 전달 시 해당 id가 결과에 없음 확인 | unit |
| `test_qdrant_prefetch_compensation` | exclude 후에도 reranker 후보 수가 동일한지 확인 | unit |
| `test_qdrant_max_prefetch_cap` | `prefetch_limit + len(exclude_ids)` > `qdrant_max_prefetch` 시 상한 적용 | unit |
| `test_extract_page` | "여신, page=2", "테이블명, 컬럼명, page=3", 비정수 방어, _MAX_PAGE 클램핑 검증 | unit |
| `test_executed_tool_keys_with_page` | 같은 쿼리의 page=1과 page=2가 다른 키로 인식되어 스킵 안 됨 | unit |
| `test_use_case_point_id_hydration` | hydrate 시 `_point_id`가 `UseCaseEntry.point_id`에 저장되는지 | unit |
| `test_biz_manual_point_id_hydration` | hydrate 시 `_point_id`가 `BizManualEntry.point_id`에 저장되는지 | unit |
| `test_seen_ids_construction` | context_retriever_node에서 seen_ids dict가 올바르게 구성되는지 검증 | unit |
| `test_point_id_survives_clear_raw` | `_clear_raw_results` 후에도 `explored_use_cases.point_id` 유지 | integration |
| `test_recovery_agent_page_usage` | recovery_agent LLM이 `page=2`를 execution_plan에 포함하는 시나리오 | e2e |
| `test_tool_execution_history_in_prompt` | recovery_agent 프롬프트에 실행 이력이 정확히 반영되는지 | unit |

---

## 12. 구현 순서

1. **config.py** — `mongo_biz_term_size`, `qdrant_max_prefetch`, `qdrant_manual_max_limit` 설정 추가
2. **state.py** — `UseCaseEntry`/`BizManualEntry`에 `point_id` + `source_step` 추가, `TableMeta`/`BizTermEntry`에 `source_step` 추가
3. **mongo_connector.py** — 3개 search 메서드에 page/sort/skip 추가
4. **qdrant_connector.py** — 2개 search 메서드에 exclude_ids/_point_id 추가
5. **tools.py** — `_extract_page` 통합 헬퍼 + 도구 시그니처 변경 + `execute_tool` **kwargs (방안 A)
6. **context_retriever.py** — `context_retriever_node`에서 `seen_ids` dict 수집 + `_run_step`에 `seen_ids` 전달
7. **context_interpreter.py** — `_hydrate_use_cases_from_raw`/`_hydrate_biz_manuals_from_raw`에 `point_id` 저장 추가
8. **reasoning_preparer.py** — `_build_execution_plan`에서 검색 도구 input에 `, page=1` 부기
9. **recovery_agent.py** — `_build_exploration_history()` / `_build_discovered_facts()` 제거, `_build_tool_execution_history()` 신규 구현 (관련성 요약 헬퍼 4개 포함), `_build_prompt`에서 변수 교체
10. **recovery_agent_system.txt** — 도구 설명 분리 + 페이징 안내 + few-shot + `{exploration_history}`/`{discovered_facts}` → `{tool_execution_history}` 섹션 교체
11. **context_interpreter_system.txt** — 필드 규칙 + few-shot 예시에 `lookup_table_meta` 반영
12. **tool_renderers.py** — `_TOOL_RENDERERS`에 `"lookup_table_meta"` 추가, `"search_code_meta"` → `"lookup_code_meta"` 변경
13. **trace_analyzer.py** — 테이블 메타 검색 누락 체크에 `_TABLE_META_TOOLS` 사용
14. **테스트 수정** — 도구명 단수/복수 판단 + 신규 테스트 추가 (위 검증 계획 참조)

---

## 13. 비판적 검토 — 발견된 이슈, 해결방안, 판단 기준

### 13.1 Critical — 반드시 해결 후 구현

#### C-1. `execute_tool` **kwargs가 비-Qdrant 도구에 전파되면 TypeError

**문제**: `execute_tool`에 `**kwargs`를 추가하면, `context_retriever`에서
Qdrant가 아닌 도구 호출 시에도 실수로 `exclude_ids`가 전파될 수 있다.
비-Qdrant 도구는 `exclude_ids` 파라미터를 받지 않으므로 TypeError가 발생한다.

**해결방안 A (권장)**: `execute_tool` 내부에서 도구 이름 기반 분기

```python
_QDRANT_TOOLS = frozenset({"search_use_cases", "search_manual"})

async def execute_tool(
    tool_name: str, tool_input: str, **kwargs: Any,
) -> Any:
    tool_fn = TOOL_MAP.get(tool_name)
    if tool_fn is None:
        logger.warning("알 수 없는 도구", tool=tool_name)
        return None
    # Qdrant 도구만 exclude_ids를 명시적으로 전달
    if tool_name in _QDRANT_TOOLS and "exclude_ids" in kwargs:
        return await tool_fn(tool_input, exclude_ids=kwargs["exclude_ids"])
    return await tool_fn(tool_input)
```

- **장점**: 호출부(`context_retriever`)가 도구 종류를 신경 쓰지 않아도 됨.
  `execute_tool(name, input, exclude_ids=ids)`를 모든 도구에 무차별 호출해도 안전.
- **단점**: `_QDRANT_TOOLS` 상수를 `tools.py`와 `context_retriever.py` 두 곳에서
  유지해야 함. 새 Qdrant 도구 추가 시 두 곳 모두 갱신 필요.

**해결방안 B**: `context_retriever`에서 호출 시 분기

```python
# context_retriever._run_step 내부
if step.tool in _QDRANT_TOOLS and exclude_ids:
    result = await execute_tool(step.tool, step.input, exclude_ids=exclude_ids)
else:
    result = await execute_tool(step.tool, step.input)
```

- **장점**: `execute_tool` 시그니처를 건드리지 않음. 분기 로직이 한 곳에만 존재.
- **단점**: `execute_tool`에 여전히 `**kwargs`가 필요하거나,
  혹은 `execute_tool` 시그니처를 변경하지 않고 Qdrant 도구를 직접 호출해야 함.

**해결방안 C**: `execute_tool`은 변경하지 않고, `context_retriever`에서 Qdrant 도구 직접 호출

```python
# context_retriever._run_step 내부
if step.tool in _QDRANT_TOOLS:
    from src.agents.nodes.reason.tools import search_use_cases, search_manual
    _qdrant_fns = {"search_use_cases": search_use_cases, "search_manual": search_manual}
    result = await _qdrant_fns[step.tool](step.input, exclude_ids=exclude_ids)
else:
    result = await execute_tool(step.tool, step.input)
```

- **장점**: `execute_tool` 완전 무변경.
- **단점**: TOOL_MAP 우회 — 도구 추가/삭제 시 두 곳 관리.

**판단 기준**: 변경 범위 최소화 vs. `execute_tool`의 범용성 유지.
`execute_tool`이 단순 디스패처인 현재 구조에서는 **방안 A**가 가장 명확하다.
`_QDRANT_TOOLS`를 `tools.py`에 한 번만 정의하고 export하면 중복도 제거된다.

**→ 섹션 4.4에 방안 A 코드 적용 완료**

---

#### C-2. 검색 도구 시그니처 변경으로 외부 keyword 호출 깨짐

**문제**: `lookup_table_meta(query: str)` → `lookup_table_meta(tool_input: str)`로
파라미터명이 바뀌면, `lookup_table_meta(query="여신")` 같은 keyword 호출이 TypeError.

**해결방안 A (권장)**: 파라미터명 유지 — 시그니처 변경하지 않고 내부만 수정

```python
async def search_table_meta(query: str) -> list[dict]:
    """테이블/컬럼 메타 키워드 검색 (MongoDB)."""
    actual_query, page = _parse_query_and_page(query)
    mgr = get_connector_manager()
    return await _safe_search(
        "search_table_meta",
        mgr.mongo.search_table_meta(actual_query, page=page),
    )
```

- **장점**: 외부 호환성 100% 유지. `search_table_meta("여신")`과
  `search_table_meta(query="여신")` 모두 동작. `__all__` export도 무변경.
- **단점**: `query` 파라미터에 `"여신, page=2"` 같은 혼합 문자열이 들어오는 것이
  의미론적으로 어색함. 하지만 `execute_tool`이 tool_input을 그대로 전달하는
  구조이므로 실질적으로 문제없음.

**해결방안 B**: `tool_input`으로 이름 변경 + grep으로 모든 외부 호출부 수정

- **장점**: 파라미터 의미가 명확.
- **단점**: 외부 호출부 수정 범위가 넓을 수 있음. 테스트 코드 포함.

**해결방안 C**: 어댑터 분리 — 검색 도구도 DB 도구처럼 `_tool_` 접두사 어댑터 추가

```python
# 원본 함수 유지 (lookup_ / search_ 네이밍 적용)
async def lookup_table_meta(table_name: str) -> list[dict]:
    mgr = get_connector_manager()
    return await _safe_search(
        "lookup_table_meta",
        mgr.mongo.search_table_meta(table_name, table_names=[table_name]),
    )

async def search_table_meta(query: str, page: int = 1) -> list[dict]:
    mgr = get_connector_manager()
    return await _safe_search(
        "search_table_meta",
        mgr.mongo.search_table_meta(query, page=page),
    )

# TOOL_MAP용 어댑터 추가
async def _tool_lookup_table_meta(tool_input: str) -> list[dict]:
    return await lookup_table_meta(tool_input.strip())

async def _tool_search_table_meta(tool_input: str) -> list[dict]:
    query, page = _parse_query_and_page(tool_input)
    return await search_table_meta(query, page=page)

TOOL_MAP = {
    "lookup_table_meta": _tool_lookup_table_meta,   # 어댑터로 변경
    "search_table_meta": _tool_search_table_meta,   # 어댑터로 변경
    ...
}
```

- **장점**: 원본 함수 시그니처 완전 유지. 외부 호출부 무변경. 의미론적으로 가장 깔끔.
  DB 도구(`_tool_get_sample_rows` 등)의 기존 어댑터 패턴과 일관됨.
- **단점**: 어댑터 함수 5개 추가 (코드량 증가).

**판단 기준**: 외부 호환성 vs. 코드 간결성 vs. 기존 패턴 일관성.

**방안 C를 권장한다.** 이유:
1. DB 도구가 이미 `_tool_` 어댑터 패턴을 사용 중이므로 검색 도구도 같은 패턴으로 통일하면
   TOOL_MAP 구조가 일관됨: "TOOL_MAP에는 어댑터만, 원본 함수는 직접 호출용"
2. `lookup_table_meta(table_name)` / `search_table_meta(query, page=page)` 시그니처가 의미론적으로 명확
3. `context_retriever._enrich_use_cases()`에서 원본 `lookup_table_meta`를
   직접 호출하더라도 호환됨
4. 코드 5개 추가는 각 3줄 수준이므로 부담 미미

---

#### C-3. Qdrant point id 타입 안전성 — `str` 강제 변환 ~~(해결 완료)~~

**문제**: Qdrant point id가 UUID 문자열 또는 정수일 수 있다.
`UseCaseEntry.point_id`/`BizManualEntry.point_id`에 저장 시 타입 불일치가 발생하면
`HasIdCondition(has_id=[...])` 필터가 오작동할 수 있다.

**채택 방안**: 커넥터에서 `str(point.id)` 강제 변환

```python
# qdrant_connector.py — search_sql_history 내부
payloads = [
    {**point.payload, "_score": point.score, "_point_id": str(point.id)}
    for point in results.points
]

# qdrant_connector.py — search_manual 내부
return [
    {**hit.payload, "_point_id": str(hit.id)}
    for hit in results[:top_k]
]
```

Entry 모델 필드도 `point_id: str`로 통일 (섹션 5.3, 5.4에 반영됨).

Qdrant `HasIdCondition(has_id=)`는 문자열 UUID도 정수 id도 수용하므로,
`str`로 전달해도 문제없다. **별도 `qdrant_seen_ids` 필드가 제거**되었으므로
LangGraph checkpointer 직렬화 이슈도 자연 해소.

구현 전 검증: `test_qdrant_str_id_in_has_id_condition` 테스트 추가하여
`HasIdCondition(has_id=["uuid-string"])` 필터가 정상 동작하는지 확인.

---

### 13.2 Warning — 구현 시 주의

#### W-1. `search_biz_terms`의 `$unwind` + `$sort: {_id: 1}` 불안정성

**문제**: `$unwind`로 펼쳐진 행은 원본 문서의 `_id`를 공유한다.
같은 `_id`를 가진 여러 행에서 `$sort: {_id: 1}`은 순서를 보장하지 않는다.

**현실적 영향**: biz_term 용어 수 200건 이내(CLAUDE.md 명시)이므로
전체가 대부분 1~2페이지에 수용된다.

**해결방안 A (권장)**: 복합 tiebreaker 사용

```python
{"$sort": {"_id": 1, "name": 1}}
```

`$unwind` 이후 `_id`가 동일한 행들도 `name`(용어명)으로 2차 정렬하면
완전한 안정 정렬이 보장된다.

- **비용**: 없음. `name`은 `$match`에서 이미 사용하는 필드이므로 추가 인덱스 불필요.
- **구현**: `mongo_connector.py`의 `search_biz_terms` 파이프라인에서
  `{"$sort": {"_id": 1}}`를 `{"$sort": {"_id": 1, "name": 1}}`로 변경.

**해결방안 B**: 페이징 생략 — biz_terms는 전체 반환 유지

용어 수가 200건 이내이므로 limit/skip 없이 전체 반환하되,
`settings.mongo_biz_term_size`를 충분히 크게 설정(예: 200).
recovery_agent가 `page=2`를 시도하더라도 page 1에서 이미 전부 반환되므로
page 2는 빈 결과.

- **장점**: 불안정 정렬 이슈 자체가 발생하지 않음.
- **단점**: 인터페이스 통일성이 깨짐. LLM이 혼란할 수 있음.

**판단 기준**: 200건 전체 반환의 성능 부담 유무.
MongoDB에서 200건 반환은 무시할 수준이므로 **방안 B도 합리적**이지만,
인터페이스 통일 원칙(섹션 2.1)에 따라 **방안 A를 권장**한다.

**→ 방안 A 채택: 섹션 3.1 search_biz_terms에 `{"$sort": {"_id": 1, "name": 1}}` 적용 완료**

---

#### W-2. ~~병렬 실행 시 `qdrant_seen_ids` dict mutation~~ (해소)

방안 A 채택으로 `qdrant_seen_ids` 별도 dict가 제거되었다.
`point_id`는 `context_interpreter`의 hydrate 단계에서 `explored_*`에 저장되므로,
`_run_step` 병렬 실행 시 mutation 문제가 발생하지 않는다.

**남은 주의점**: `seen_ids` dict가 참조하는 `explored_use_cases`/`explored_biz_manuals`는
batch 실행 전 시점의 스냅샷이다. 같은 batch 내에서 새로 반환된 결과의 `point_id`는
아직 hydrate되지 않았으므로 exclude 대상에 포함되지 않는다.
이는 의도된 동작이다 (섹션 6.3 주의사항 참조).

---

#### W-3. `lookup_code_meta`의 페이징 실효성

**문제**: `lookup_code_meta`는 `code_names` 리스트로 정확 매칭하는 패턴이 대부분이다.
recovery_agent가 코드명을 모른 채 자유 검색하는 경우는 드물다.
(기존 `search_code_meta`에서 `lookup_code_meta`로 이름 변경 — 컬럼명 지정 조회이므로 `lookup_` 접두사가 적절.)

**해결방안 A (권장)**: 인터페이스 통일 차원에서 page 추가하되, 프롬프트에서 비강조

recovery_agent 프롬프트의 페이징 안내에서 `lookup_code_meta`를 예시에서 제외한다.
LLM이 자연스럽게 `lookup_code_meta`에는 page를 사용하지 않게 유도.

```
예시:
  - search_table_meta("여신, page=2") → "여신" 검색 결과의 11~20번째
  - search_use_cases("여신 실행 건수, page=2") → 유사 SQL 이력의 추가 결과
  # lookup_code_meta는 예시에서 제외
```

**해결방안 B**: `lookup_code_meta`만 page 지원에서 제외

- **단점**: 인터페이스 불일치. `_extract_page`를 적용하지 않으면
  `"STATUS_CD, page=2"` 입력 시 `"STATUS_CD, page=2"` 전체가 검색어로 인식됨.
  실패가 아닌 예상치 못한 결과 반환으로 디버깅이 어려움.

**판단 기준**: **방안 A**. 파싱은 통일하되 프롬프트에서 유도하는 것이 안전.

---

#### W-4. LLM이 `page=2` 대신 `page 2`나 `page:2`를 생성할 가능성

**문제**: 폐쇄망 70B 모델(Solar Pro 2, Qwen3.5)이 정확한 `page=N` 형식을
생성하지 못할 수 있다.

**해결방안 A (권장 — 2단계)**: 엄격 파싱 + 모니터링 → 필요 시 fuzzy 파싱

**1단계** (즉시): `page=N` 엄격 파싱 + 프롬프트 few-shot 강화
```python
# _extract_page — 엄격 모드
if part.startswith("page="):
    try:
        p = int(part.split("=", 1)[1])
        page = max(1, min(p, _MAX_PAGE))
    except (ValueError, IndexError):
        pass
```

프롬프트에 형식을 명시:
```
형식: "검색어, page=N" (반드시 page= 형태를 사용하세요)
올바른 예: search_table_meta("여신, page=2"), lookup_table_meta("TB_ADW_LNB301M")
잘못된 예: search_table_meta("여신, page 2"), search_table_meta("여신, 2")
```

**2단계** (모니터링 후 필요 시): fuzzy 파싱 추가
```python
_PAGE_RE = _re.compile(r"page\s*[=:\s]\s*(\d+)", _re.IGNORECASE)

def _extract_page(parts: list[str]) -> tuple[list[str], int]:
    page = 1
    remaining: list[str] = []
    for part in parts:
        m = _PAGE_RE.match(part)
        if m:
            page = max(1, min(int(m.group(1)), _MAX_PAGE))
        else:
            remaining.append(part)
    return remaining, page
```

- **모니터링 방법**: `_extract_page` 반환값에서 page=1이지만
  tool_input에 "page" 문자열이 포함된 경우를 로깅하여 파싱 실패 비율 추적.

```python
parts, page = _extract_page([p.strip() for p in tool_input.split(",")])
if page == 1 and "page" in tool_input.lower():
    logger.warning("page 파싱 실패 의심", tool_input=tool_input)
```

**판단 기준**: 과도한 fuzzy 파싱은 오파싱 리스크(예: "homepage=2" 같은 문자열)가 있으므로
**1단계 엄격 모드**로 시작하고, 실 데이터 기반으로 판단.

---

#### W-5. `get_sample_rows` ORDER BY 부재

**문제**: `ORDER BY`가 없어 `OFFSET` 기반 페이지 간 중복이 발생할 수 있다.
PK를 모르는 상태에서 범용 ORDER BY를 넣기 어렵다.

**해결방안 A (권장)**: 현행 유지 — 중복 수용

sample_rows는 "다양한 값 탐색" 용도이므로 약간의 중복은 치명적이지 않다.
recovery_agent가 `get_sample_rows page=2`를 사용하는 경우는
"page 1에서 패턴을 파악하지 못해 더 많은 행을 보고 싶은 경우"이다.
중복 행이 일부 있어도 새로운 행이 함께 포함되므로 목적 달성에 문제없다.

**해결방안 B**: `ORDER BY 1` (첫 번째 컬럼 기준 정렬)

```python
if db.dialect == "tsql":
    sql = f"SELECT TOP {limit} START AT {start_at} * FROM {qualified} ORDER BY 1"
else:
    sql = f"SELECT * FROM {qualified} ORDER BY 1 LIMIT {limit} OFFSET {offset}"
```

- **장점**: 안정 정렬 보장. 페이지 간 중복 제거.
- **단점**: `ORDER BY 1`이 의미 있는 정렬인지 보장 없음. 대용량 테이블에서
  정렬 비용 증가 (인덱스 없는 컬럼이면 full scan + sort). Impala에서
  `ORDER BY ordinal` 지원 여부 확인 필요.

**해결방안 C**: PK 컬럼 동적 탐지

```python
async def get_sample_rows(..., page: int = 1) -> list[dict]:
    # explored_tables에서 해당 테이블의 PK 컬럼 찾기
    pk_cols = [c["name"] for c in table_meta.get("columns", []) if c.get("is_pk")]
    order_clause = f"ORDER BY {', '.join(pk_cols)}" if pk_cols else ""
    sql = f"SELECT * FROM {qualified} {order_clause} LIMIT {limit} OFFSET {offset}"
```

- **장점**: PK 기반 안정 정렬 + 인덱스 활용으로 성능도 양호.
- **단점**: `get_sample_rows`는 tools.py의 단순 함수. `explored_tables`에 접근하려면
  함수 시그니처를 바꾸거나 context_retriever에서 PK 정보를 전달해야 함.
  현재 구조에서는 과도한 결합.

**판단 기준**: 성능 리스크(대용량 테이블 ORDER BY) vs. 중복 수용 가능성.

**→ 해결: get_sample_rows 페이징 제거. 특정 컬럼의 값을 더 보고 싶다면 get_column_values로 유도.**
섹션 3.3에서 get_sample_rows의 page 파라미터를 제거하고,
섹션 7.1 프롬프트에 get_column_values 사용 안내를 추가하였다.

---

#### W-6. `_build_exploration_history`와 `{discovered_facts}`와 `{tool_execution_history}` 정보 중복

**문제**: `{exploration_history}`는 `explored_use_cases`만 표시하고,
`{discovered_facts}`는 DONE+insight 스텝만 나열하고,
`{tool_execution_history}`는 검색 도구 실행 키만 표시한다.
세 섹션이 search_use_cases 정보를 중복하며, 테이블/비즈메타의 관련성 평가가 누락되어 있다.

**→ 해결: 세 섹션을 단일 `{tool_execution_history}`로 통합.**

통합 방식은 섹션 7에 상세 기술. 핵심:
- `_build_exploration_history()`, `_build_discovered_facts()` 제거
- `_build_tool_execution_history()`가 `ExecutionStep` 순회 + 각 엔티티 state의 관련성 동적 집계
- 프롬프트에서 `{exploration_history}`, `{discovered_facts}` 삭제 → `{tool_execution_history}` 교체
- 토큰 절약 + 정보 누락 해소 + single source of truth 유지

---

### 13.3 추가 발견 이슈

#### A-1. `search_manual` dummy 모드에서 `_point_id` 미반환

현재 `search_dummy_manuals()`은 payload dict만 반환한다.
dummy 모드에서는 Qdrant point.id가 없으므로 `_point_id`가 누락된다.
`_accumulate_qdrant_seen_ids`가 `_point_id`를 찾지 못해 seen_ids가 비어있게 된다.

**해결방안**: dummy 데이터에 가짜 `_point_id`를 추가.

```python
# dummy_data.py — search_dummy_manuals 반환값에 _point_id 추가
def search_dummy_manuals(query: str, top_k: int) -> list[dict]:
    results = [...]
    for i, item in enumerate(results):
        item["_point_id"] = f"dummy-manual-{i}"
    return results[:top_k]
```

**영향**: dummy 모드에서 페이징 테스트를 할 수 있게 됨. 실제 Qdrant 모드와 동일한 흐름 보장.

#### A-2. `_rerank`이 `_point_id`를 보존하는지 재확인 — **확인 완료, 변경 불필요**

`_rerank`에서 `RerankCandidate(payload=c)`로 원본 dict를 참조 전달하고,
반환 시 `item.payload.copy()`로 복사하므로 `_point_id`는 보존된다.
`d["_score"]`와 `d["similarity"]`만 덮어쓰고, `_point_id`는 건드리지 않아 안전.
이후 `_hydrate_use_cases_from_raw`에서 `uc_data.get("_point_id")`로 추출 가능.

#### A-3. 페이징 최대 depth 제한

recovery_agent가 `page=10` 같은 과도한 페이징을 시도할 수 있다.
`max_tool_calls` (현재 20)에 의해 간접 제한되지만,
`_extract_page`에서 명시적 상한을 두는 것이 방어적이다.

**→ 섹션 4.1의 `_extract_page`에 `_MAX_PAGE = 5` 클램핑 적용 완료.**

프롬프트에도 "page=3 이상은 일반적으로 불필요" 안내가 있으므로
`_MAX_PAGE = 5` 정도의 소프트 리밋이 적절하다.
