# Search Tool Pagination 코드 리뷰 보고서

- **작성일**: 2026-04-06
- **대상**: search 도구 페이지네이션 구현 전반
- **리뷰 범위**: config.py, state.py, mongo_connector.py, qdrant_connector.py, tools.py, context_retriever.py, context_interpreter.py, reasoning_preparer.py, recovery_agent.py, tool_renderers.py, trace_analyzer.py, dummy_data.py

---

## CRITICAL (반드시 수정)

### C-01. get_column_values의 LIKE 키워드에 파라미터 바인딩 미사용

**파일**: `src/agents/nodes/reason/tools.py` L281~L309

```python
sanitized_kw = keyword.replace("'", "''").replace("\\", "\\\\")
# ...
f"WHERE {column_name} LIKE '%{sanitized_kw}%' "
```

`_IDENT_RE`가 `table_name`, `column_name`은 검증하지만, `keyword`는 사용자 입력에서 유래할 수 있는 값이다. 현재 단순 문자열 이스케이프(`'` → `''`, `\\` → `\\\\`)만 수행하지만, DB 방언별 이스케이프 규칙이 상이하여 Sybase IQ/Impala에서 우회될 수 있다. `execute_query`가 `params` 인자를 지원하므로 **반드시 파라미터 바인딩으로 전환**해야 한다.

**개선안**:
```python
# PostgreSQL/Impala
sql = (
    f"SELECT DISTINCT {column_name} FROM {qualified} "
    f"WHERE {column_name} LIKE $1 "
    f"ORDER BY {column_name} LIMIT {limit} OFFSET {offset}"
)
result = await db.execute_query(sql, params={"$1": f"%{keyword}%"})
```

단, `LIKE` 패턴 내의 `%`, `_` 와일드카드 문자도 별도 이스케이프가 필요하다 (`keyword.replace('%', '\\%').replace('_', '\\_')`). 이 부분은 DB 방언별 `ESCAPE` 절 지원 여부에 따라 분기해야 하므로, 커넥터 인터페이스에 `like_escape` 헬퍼를 추가하는 것을 검토한다.

**심각도 근거**: 프로젝트 보안 규칙(`data-security.md`)에 "SQL은 반드시 파라미터 바인딩 사용 (f-string 금지)"로 명시되어 있으며, 사용자 자연어 입력이 LLM을 거쳐 `keyword` 파라미터로 전달될 수 있다.

---

### C-02. get_sample_rows/get_column_profile/get_date_distribution의 f-string SQL

**파일**: `src/agents/nodes/reason/tools.py` L237~L240, L357~L363, L422~L430

```python
sql = f"SELECT TOP {limit} * FROM {qualified}"
sql = f"SELECT * FROM {qualified} LIMIT {limit}"
```

`table_name`, `column_name`은 `_IDENT_RE`로 검증되어 SQL 인젝션 위험은 실질적으로 없으나, `limit`, `offset` 값은 `int`로 강제 변환되지 않은 경우가 있다. `get_sample_rows`의 `limit` 파라미터는 기본값 10이고 어댑터에서 고정 호출되므로 현재는 안전하지만, **향후 외부 입력이 유입될 경우를 대비하여 `int()` 캐스팅을 명시**하는 것이 방어적이다.

```python
limit = int(limit)  # 방어적 타입 강제
```

**심각도 근거**: 현재 경로에서는 외부 입력이 직접 유입되지 않으나, 도구 확장 시 안전망이 필요하다. _IDENT_RE 검증이 있으므로 `table_name`/`column_name`에 대해서는 파라미터 바인딩 전환이 불가능하다 (식별자는 바인딩 불가). 현 구현은 수용 가능하나, `limit`/`offset`에 대해서만 방어 강화가 필요하다.

---

## WARNING (개선 권장)

### W-01. _safe_search에서 f-string 로거 사용

**파일**: `src/agents/nodes/reason/tools.py` L115

```python
logger.warning(f"{tool_name} 실패", error=str(e))
```

structlog 패턴에서 f-string을 메시지에 사용하면 메시지 집계(aggregation)가 불가능해진다. 도구 이름은 구조화 필드로 분리해야 한다.

**개선안**:
```python
logger.warning("도구 검색 실패", tool=tool_name, error=str(e))
```

---

### W-02. MongoDB 코드 메타 검색에서 빈 쿼리 시 전체 스캔

**파일**: `src/connectors/impl/mongo_connector.py` L294

```python
if code_names:
    match_stage = {_MATCH: {"name": {"$in": code_names}}}
else:
    match_stage = {_MATCH: {}}  # 전체 스캔
```

`code_names`가 빈 리스트이고 `query`도 의미 없는 값이면 `$match: {}` 전체 스캔이 발생한다. 코드 메타 컬렉션이 소규모라 성능 문제는 없겠으나, `query` 기반 `$regex` 검색으로 전환하거나 빈 쿼리 방어가 필요하다.

**개선안**:
```python
else:
    match_stage = {_MATCH: _build_regex_match(
        query, fields=["name", "code_field_desc"],
    )}
```

---

### W-03. Qdrant search_manual의 effective_limit 계산 로직 비대칭

**파일**: `src/connectors/impl/qdrant_connector.py` L265~L273

```python
effective_limit = min(
    top_k + len(exclude_ids),
    settings.qdrant_manual_max_limit,
)
```

`search_sql_history`에서는 `prefetch_limit + len(exclude_ids)`로 보정하고 상한이 `qdrant_max_prefetch`(100)인 반면, `search_manual`에서는 `top_k + len(exclude_ids)`로 보정하고 상한이 `qdrant_manual_max_limit`(30)이다. 이 차이는 의도된 것이나, **exclude_ids가 30을 초과하면 effective_limit이 top_k보다 작아질 수 있다**는 점이 문서화되어 있지 않다.

`qdrant_manual_max_limit = 30`이고 `top_k = 3`, `exclude_ids = 28`이면 `min(31, 30) = 30`이고 `results[:top_k]`로 슬라이싱하여 3건을 반환하므로 동작은 정상이다. 단, `exclude_ids > 27`이면 새 결과가 사실상 0건이 될 수 있다. 이 경계 조건에 대한 주석 보충을 권장한다.

---

### W-04. _extract_page가 page 파라미터를 소비하되 검증이 부족한 경계

**파일**: `src/agents/nodes/reason/tools.py` L83~L103

LLM이 `page=0`이나 `page=-1`을 생성할 수 있는데, `max(1, min(p, _MAX_PAGE))`로 클램핑되어 1로 보정된다. 이는 올바른 방어이다.

그러나 `page=abc`(비정수) 입력 시 `except (ValueError, IndexError): pass`로 무시하고 기본값 1을 사용하므로, **LLM이 잘못된 page 값을 반복 생성해도 피드백 없이 항상 1페이지를 반환**한다. 에러 로깅 추가를 권장한다.

```python
except (ValueError, IndexError):
    logger.debug("page 파라미터 파싱 실패, 기본값 1 사용", raw=part)
```

---

### W-05. reasoning_preparer에서 page=1이 tool_input에 하드코딩

**파일**: `src/agents/nodes/reason/reasoning_preparer.py` L349~L353

```python
input=f"{original_query}, page=1",
```

`executed_tool_keys` 중복 검사가 `f"search_table_meta:{meta_query}, page=1"`과 정확 문자열 매칭인데(L357), LLM이 recovery_agent에서 재계획 시 `page=2`를 생성하면 `"search_table_meta:여신, page=2"`는 기존 키와 불일치하여 중복 방지에 걸리지 않는다. 이는 **의도된 설계**(page가 다르면 새 결과를 가져옴)이나, 키 패턴에 대한 문서화 주석이 부족하다.

---

### W-06. context_retriever의 seen_ids 수집 시 빈 point_id 필터 누락

**파일**: `src/agents/nodes/reason/context_retriever.py` L440~L448

```python
seen_ids: dict[str, list[str]] = {
    "search_use_cases": [
        uc.point_id for uc in reason.explored_use_cases
        if uc.point_id
    ],
```

`point_id`가 빈 문자열(`""`)인 경우 `if uc.point_id`가 `False`로 평가되어 올바르게 필터링된다. 그러나 `"0"`이나 `" "`(공백) 같은 edge case는 통과할 수 있다. Qdrant point_id가 항상 UUID 형태라면 문제없으나, dummy 데이터에서 `"dummy-sql-0"` 형태가 사용되므로 현재는 안전하다. 다만 타입 안전성을 위해 `str(uc.point_id).strip()`으로 정규화를 권장한다.

---

### W-07. _hydrate_use_cases_from_raw에서 score 키 불일치 가능성

**파일**: `src/agents/nodes/reason/context_interpreter.py` L905

```python
score=uc_data.get("score", 0.0),
```

`qdrant_connector.py`의 `_rerank`에서 반환하는 dict에는 `_score`(RRF)와 `similarity`(reranker) 두 키가 있다. `search_use_cases` → `_safe_search` → `search_sql_history` → `_rerank` 경로에서 최종적으로 `similarity` 키가 설정되지만, `score`라는 키는 없다. dummy 데이터에서는 `score` 키가 직접 설정될 수 있으므로 dummy 모드에서는 동작하나, **실제 Qdrant 모드에서 score가 항상 0.0이 될 수 있다**.

**개선안**:
```python
score=uc_data.get("score", uc_data.get("similarity", uc_data.get("_score", 0.0))),
```

---

### W-08. recovery_agent의 _build_tool_execution_history에서 Unicode 마크 사용

**파일**: `src/agents/nodes/reason/recovery_agent.py` L469

```python
status_mark = "✓" if step.status == StepStatus.DONE else "✗"
```

LLM 프롬프트에 유니코드 특수 문자를 사용하면 소형 LLM(Solar Pro 2 70B 등)에서 토크나이저가 다중 토큰으로 분할하여 불필요한 토큰을 소비할 수 있다. 폐쇄망 배포 시 ASCII 문자(`[OK]`/`[FAIL]`)로 전환을 검토한다.

---

## INFO (참고/개선 제안)

### I-01. context_retriever.py 주석의 구 도구명 잔존

**파일**: `src/agents/nodes/reason/context_retriever.py` L56

```python
관측 도구(get_sample_rows, search_column_values 등)는 멱등이므로
```

`search_column_values`라는 도구는 현재 존재하지 않는다. `get_column_values`가 올바른 명칭이다. 주석을 수정해야 한다.

---

### I-02. trace_analyzer.py의 _check_pipeline_flow에서 구 노드명 참조

**파일**: `src/utils/tracker/trace_analyzer.py` L423

```python
replan_count = node_path.count("recovery_planner")
```

현재 노드명은 `recovery_agent`이다. `recovery_planner`는 리네이밍 이전의 이름으로 보인다. 트레이스 분석 시 재계획 횟수가 항상 0으로 집계될 수 있다.

---

### I-03. dummy_data에서 _point_id 형식 일관성

**파일**: `src/connectors/dummy_data.py` L872, L897

```python
{**m, "_point_id": f"dummy-manual-{i}"}
{**m, "_point_id": f"dummy-sql-{i}"}
```

실제 Qdrant에서는 UUID 형태의 point_id가 반환되지만, dummy에서는 `dummy-manual-0` 같은 문자열이 사용된다. `search_sql_history`의 `HasIdCondition`에서 타입 불일치가 발생할 수 있다. dummy 모드에서는 exclude_ids가 Qdrant API에 전달되지 않으므로 현재는 문제없으나, 향후 dummy 모드에서도 페이지네이션을 테스트하려면 `search_dummy_qdrant_sql_history`에서 exclude_ids 필터링 로직을 추가해야 한다.

---

### I-04. config.py 설정값 3건의 역할 명확화 필요

**파일**: `src/config.py` L98~L99, L115

```python
qdrant_max_prefetch: int = 100      # exclude_ids 누적 시 prefetch 상한
qdrant_manual_max_limit: int = 30   # search_manual exclude_ids 누적 시 limit 상한
mongo_biz_term_size: int = 20       # 비즈니스 용어 기본 limit
```

3개 설정이 "페이지네이션 보정 상한"이라는 공통 맥락을 가지지만, 이름만으로는 용도를 파악하기 어렵다. 주석은 충분하나, 설정 그룹핑(예: `# ── Pagination Guard ──` 섹션 분리)을 권장한다.

---

### I-05. TOOL_MAP과 _TOOL_RENDERERS, _RELEVANCE_BUILDERS 키 동기화

**파일**: tools.py, tool_renderers.py, recovery_agent.py

3개 딕셔너리의 키가 모두 동일한 도구명을 참조하는데, 도구가 추가/삭제될 때 3곳을 모두 수정해야 한다. 향후 도구 추가 시 누락을 방지하려면 `TOOL_MAP.keys()`를 기준으로 검증하는 테스트를 추가하거나, 도구 등록을 단일 레지스트리 패턴으로 통합하는 것을 검토한다.

---

### I-06. tools.py의 __all__ 누락 항목

**파일**: `src/agents/nodes/reason/tools.py` L55~L71

`__all__`에 `_extract_page`, `_safe_search`, `_MAX_PAGE`가 포함되지 않은 것은 올바르다 (내부용). 다만 `extract_hints_from_use_cases` 함수가 docstring의 "분석 도구" 목록에 언급되어 있으나 코드에 존재하지 않는다. `src/utils/sqlglot_analyzer.py`의 `extract_structural_hints`가 실제 함수이다. docstring 수정을 권장한다.

---

### I-07. MongoConnector의 search_biz_terms에서 search_dummy_table_meta 폴백

**파일**: `src/connectors/impl/mongo_connector.py` L348

```python
if self._use_dummy:
    return search_dummy_table_meta(query)  # 임시 fallback
```

biz_term 전용 dummy 함수가 없어 table_meta dummy를 대신 사용하고 있다. `# 임시 fallback` 주석이 있으나, 이 상태가 장기간 유지되면 테스트 정확도가 떨어진다. biz_term 전용 dummy 데이터 추가를 검토한다.

---

### I-08. tools.py 어댑터의 exclude_ids 전달 패턴 통일성

**파일**: `src/agents/nodes/reason/tools.py` L530~L567

`_tool_search_use_cases`와 `_tool_search_manual`은 `*, exclude_ids` 키워드 인자를 받고, `execute_tool`에서 `_QDRANT_TOOLS`에 해당하는 경우에만 전달한다. 이 패턴은 일관되게 구현되어 있다. 다만 `_tool_search_use_cases`에서 `_page`를 파싱하되 사용하지 않는데(`_page`로 무시), Qdrant 도구에서 page 개념은 exclude_ids로 대체되므로 의도된 것이다. `_page` 대신 `_` 변수명을 사용하면 의도가 더 명확해진다.

---

## 종합 평가

### 잘 된 점
1. **MongoDB 파이프라인 순서**: `$sort` -> `$skip` -> `$limit` -> `$project` 순서가 올바르게 적용됨 (SERVER-51498 대응 포함)
2. **Qdrant prefetch 보정**: exclude_ids 누적 시 설정값 기반 상한(`qdrant_max_prefetch`)으로 클램핑하여 무한 확장 방지
3. **point_id str() 강제 변환**: `str(hit.id)`, `str(uc_data.get("_point_id", ""))`로 일관되게 문자열 변환
4. **_extract_page 방어 로직**: max(1, min(p, _MAX_PAGE)) 클램핑, 비정수 폴백
5. **_safe_search 래퍼**: 모든 검색 도구에 일관되게 적용하여 예외 전파 방지
6. **어댑터 패턴 통일**: 모든 도구가 `_tool_` 접두사 어댑터를 통해 TOOL_MAP에 등록
7. **도구명 컨벤션**: lookup/search/get 3종 분류가 명확
8. **외부 호출 호환성**: `lookup_table_meta`가 직접 호출 시그니처를 유지하여 enrichment 등 기존 호출부와 호환
9. **seen_ids 수집**: context_retriever에서 배치 실행 전 1회 수집하여 동일 배치 내 중복 방지
10. **source_step 추적**: 4개 모델(UseCaseEntry, BizManualEntry, BizTermEntry, TableMeta)에 일관되게 source_step 필드 추가

### 우선 조치 사항
| 등급 | 건수 | 요약 |
|------|------|------|
| CRITICAL | 2 | SQL 파라미터 바인딩 미사용 (get_column_values LIKE 키워드, limit/offset int 캐스팅) |
| WARNING | 8 | f-string 로거, 빈 쿼리 전체 스캔, effective_limit 경계, page 에러 로깅 미비 등 |
| INFO | 8 | 구 도구명 주석, dummy fallback, 설정 그룹핑, 레지스트리 통합 등 |
