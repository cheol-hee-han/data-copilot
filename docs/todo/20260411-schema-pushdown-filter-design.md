# 스키마 기반 Push-down 필터링 설계

작성일: 2026-04-11
작성자: 한철희
상태: 검토 요청

## 배경 및 문제

### 관측된 현상
서버 기동 후 질의 처리 중 다음과 같은 로그가 대량으로 출력됨:
```
FORCED 모드: 타겟 외 테이블 제외  table=TB_ADW_CSC101M target=test_db
FORCED 모드: 타겟 외 테이블 제외  table=TB_ADW_CSC102H target=test_db
... (수십~수백 건)
```

### 근본 원인
1. **MongoDB `dpasset_table` 메타 스키마**: 저장 필드는 `_id, schema_name, name, alt_name, desc`만 존재. **`db_source` 필드는 없음**.
2. **`db_source` 태깅 로직** ([ConnectorManager.parse_db_source](../../src/connectors/manager.py#L302-L312)):
   테이블명 접두사(`TB_ADW_*` → `sybase`, `TB_BDP_*` → `impala`, ...)로 파생.
3. **외부망 데이터 구성 불일치**:
   - MongoDB 메타: 572건 전부 `TB_ADW_*` (sybase로 태깅됨)
   - 실제 PostgreSQL `test_db`의 `adwown` 스키마에 같은 572 테이블 호스팅
   - FORCED `target_db_code=DEV`(커넥터 키 `test_db`)이면 **사후 필터에서 572건 전부 제거됨**
4. **필터 위치**:
   현재 [`_filter_by_forced_target`](../../src/agents/nodes/reason/context_retriever.py#L62-L83)는 MongoDB가 모든 테이블을 반환한 뒤 Python 레이어에서 제거 → **사후 필터링(post-filtering)**.

### 폐쇄망 환경의 실제 매핑
| 시스템코드 | 커넥터 | DBMS        | schema_name |
|-----------|--------|-------------|-------------|
| `ADW`     | sybase | Sybase IQ   | `ADWOWN`    |
| `BDP`     | impala | Impala      | `BDPOWN`    |
| `CRP`     | oracle | Oracle      | `CRPOWN`    |
| `DEV`     | test_db | PostgreSQL | `ADWOWN` (외부망 전용, 동일 메타 재사용) |

폐쇄망에서는 **schema_name이 시스템을 자연스럽게 구분**하므로, 테이블명 접두사에 의존하지 않고 schema_name으로 필터링하는 것이 정합적이다.

## 설계 목표

1. **조회 단계 필터링(push-down)**: FORCED 모드에서 MongoDB `$match` 스테이지에 `schema_name` 조건을 주입하여 타겟 스키마의 테이블만 반환받는다.
2. **사후 필터 제거**: 조회 단계에서 보장되므로 `_filter_by_forced_target` 및 호출 지점을 제거한다.
3. **페이지 정확도 복원**: 현재 페이지당 `mongo_table_meta_size=10` 제한에 사후 필터가 섞여 실제로는 훨씬 적은 수만 남는 문제를 해소한다.
4. **폐쇄망/외부망 공통 동작**: 동일 코드 경로로 ADW/BDP/CRP/DEV 모두 처리한다.

## 상세 설계

### 1. 설정 추가 — [src/config.py](../../src/config.py#L243-L265)

**신규 필드**:
```python
# 시스템코드 → schema_name 매핑 (push-down 필터링 및 기본 스키마 조회용)
target_db_schema_map: dict[str, str] = {
    "ADW": "ADWOWN",
    "BDP": "BDPOWN",
    "CRP": "CRPOWN",
    "DEV": "ADWOWN",  # 외부망: test_db에서도 adwown 스키마 재사용
}
```

**파생 프로퍼티**:
```python
@property
def target_schema(self) -> str:
    """target_db_code 에 매핑된 schema_name 을 반환한다.

    미지정이거나 매핑이 없으면 빈 문자열.
    """
    if not self.target_db_code:
        return ""
    return self.target_db_schema_map.get(self.target_db_code, "")
```

### 2. MongoDB 커넥터 — push-down 지원

**대상 파일**: [src/connectors/impl/mongo_connector.py](../../src/connectors/impl/mongo_connector.py#L198-L270)

**변경**: `search_table_meta`가 `schema_names: list[str] | None` kwarg를 수용하여 `$match` 스테이지에 `{"schema_name": {"$in": schema_names}}` 조건을 합성.

```python
async def search_table_meta(
    self, query: str, **kwargs: Any,
) -> list[dict[str, Any]]:
    ...
    table_names: list[str] = kwargs.get("table_names", [])
    schema_names: list[str] | None = kwargs.get("schema_names")
    limit = kwargs.get("limit", settings.mongo_table_meta_size)
    page: int = kwargs.get("page", 1)
    skip = (page - 1) * limit

    if table_names:
        match_conditions: dict[str, Any] = {"name": {"$in": table_names}}
    else:
        match_conditions = dict(_build_regex_match(
            query, fields=["alt_name", "desc", "name"],
        ))
    if schema_names:
        match_conditions["schema_name"] = {"$in": schema_names}
    match_stage = {_MATCH: match_conditions}
    ...
```

**주의**: `_build_regex_match`가 반환하는 구조가 `$or` 등 복합 연산자일 경우를 대비해 dict 래핑 후 `schema_name` 키를 최상위에 병합한다. 필요 시 `$and`로 래핑한다.

**인덱스 검토**: `schema_name` 필드에 단일 인덱스 또는 `(schema_name, name)` 복합 인덱스가 있는지 확인 필요. 현재 데이터 규모(572건)에서는 미영향이지만 폐쇄망 수만 건 규모에서는 필수.

### 3. tools 레이어 — 필터 주입

**대상 파일**: [src/agents/nodes/reason/tools.py](../../src/agents/nodes/reason/tools.py#L125-L152)

**헬퍼 추가**:
```python
from src.config import settings


def _forced_schemas() -> list[str] | None:
    """FORCED 모드일 때 허용되는 schema_name 목록을 반환한다.

    target_db_code 가 설정되어 있으면 해당 스키마 하나를 리스트로 반환.
    미설정이면 None (필터링 없음).
    """
    schema = settings.target_schema
    return [schema] if schema else None
```

**호출부 수정**:
```python
async def lookup_table_meta(table_name: str) -> list[dict]:
    mgr = get_connector_manager()
    return await _safe_search(
        "lookup_table_meta",
        mgr.mongo.search_table_meta(
            table_name,
            table_names=[table_name],
            schema_names=_forced_schemas(),
        ),
    )


async def search_table_meta(
    keywords: str, page: int = 1,
) -> list[dict]:
    mgr = get_connector_manager()
    return await _safe_search(
        "search_table_meta",
        mgr.mongo.search_table_meta(
            keywords,
            page=page,
            schema_names=_forced_schemas(),
        ),
    )
```

### 4. 사후 필터 제거

**대상 파일**: [src/agents/nodes/reason/context_retriever.py](../../src/agents/nodes/reason/context_retriever.py)

**제거 대상**:
- `_forced_target_db()` 함수 ([line 51-59](../../src/agents/nodes/reason/context_retriever.py#L51))
- `_filter_by_forced_target()` 함수 ([line 62-83](../../src/agents/nodes/reason/context_retriever.py#L62))
- 호출 지점 2곳:
  - [line 384](../../src/agents/nodes/reason/context_retriever.py#L384) (enrichment 경로)
  - [line 727](../../src/agents/nodes/reason/context_retriever.py#L727) (`_extract_tables` 반환)

호출 지점은 필터 없이 원본 리스트를 그대로 쓰도록 단순화.

### 5. 설정·문서 일관성

- [.claude/rules/financial-domain.md](../../.claude/rules/financial-domain.md), [docs/architecture/architecture.md](../../docs/architecture/architecture.md) 등에 "schema_name 기반 필터"가 새 규약임을 명시 (후속 PR).
- 현재 `restrict_connectors_to_target` 설정은 **connect/disconnect 레이어의 커넥터 스킵** 용도로만 남긴다 (필터링 로직과 분리).

## 영향 범위

### 수정 파일
- `src/config.py` — 매핑/프로퍼티 추가
- `src/connectors/impl/mongo_connector.py` — `schema_names` kwarg 수용
- `src/agents/nodes/reason/tools.py` — `_forced_schemas` 주입
- `src/agents/nodes/reason/context_retriever.py` — 사후 필터 제거

### 영향 받는 흐름
- `reasoning_preparer` 초기 탐색 → tools를 거치므로 자동 반영
- `recovery_agent` 추가 탐색 → tools를 거치므로 자동 반영
- `context_retriever._extract_tables` enrichment 경로 → 동일

### 테스트 영향
- [tests/auto/unit/](../../tests/auto/unit/)의 `_filter_by_forced_target` 단위 테스트 존재 여부 확인 → 있으면 제거
- MongoDB 커넥터 단위 테스트에 `schema_names` 케이스 추가
- tools 레이어에 `_forced_schemas` 단위 테스트 추가
- E2E: 기동 후 로그에 "타겟 외 테이블 제외"가 사라지는지 확인

## 코드 사전 검증 결과

- **`_build_regex_match`** ([mongo_connector.py:64-87](../../src/connectors/impl/mongo_connector.py#L64)) 반환: `{"$or": [...]}` 또는 단일 `{field: {"$regex": ...}}`. 두 형태 모두 top-level dict에 `schema_name` 키를 병합하면 MongoDB가 AND로 해석하므로 안전.
- **`mongo.search_table_meta` 호출 지점 4곳** (모두 확인):
  - [tools.py:134, 151](../../src/agents/nodes/reason/tools.py#L134) — 본 설계 대상
  - [src/tools/seed_sql_history.py:387](../../src/tools/seed_sql_history.py#L387) — `table_names` kwarg만 사용, 신규 kwarg는 옵션이므로 파급 없음
  - [tests/auto/unit/test_connectors.py:113](../../tests/auto/unit/test_connectors.py#L113), [tests/manual/e2e/test_agentic_real_e2e.py:83](../../tests/manual/e2e/test_agentic_real_e2e.py#L83) — positional 호출, 파급 없음
- **테스트 영향**: `_filter_by_forced_target` / `_forced_target_db`를 직접 호출/참조하는 단위·E2E 테스트 없음 → 함수 제거 안전.
- **`TableMeta.from_meta`** ([state.py:263-300](../../src/agents/state/state.py#L263)): `schema_name=meta.get("schema_name", "")`로 MongoDB 필드를 그대로 읽음. 샘플 문서 조회 결과 `ADWOWN`(대문자)로 저장됨 → 매핑 테이블과 일관.
- **Dummy 모드 경로**: [mongo_connector.py:214-215](../../src/connectors/impl/mongo_connector.py#L214)에서 `search_dummy_table_meta(query)` 호출, `schema_names` 무시됨. 현재 `USE_DUMMY=false`이므로 실질 영향 없음. 추후 dummy 모드 복귀 시 별도 보정 필요(문서에 주석으로 남김).
- **`parse_db_source` 기존 태깅 로직**은 그대로 유지. 본 설계는 **스키마 기반 필터링**을 push-down하는 것이 범위이며, 외부망 DEV에서 `TB_ADW_*`를 `sybase`로 오태깅하는 문제는 **독립 이슈**로 분리(아래 "열린 질문 4번").

## 열린 질문

1. **비-FORCED 모드(혼재 멀티 DB)**: `target_db_code`가 미설정인 상황에서 질의가 여러 DB에 걸치면 어떻게 할 것인가?
   - 현재 설계: `schema_names=None` → 필터 없음 → MongoDB가 모든 schema 반환. context_retriever가 이후 readiness_gate에서 단일 타겟을 결정.
   - 이 동작이 기존과 동일한지 확인 필요.
2. **`restrict_connectors_to_target` 결합도**: 현재 `_forced_target_db()`는 `target_db_code AND restrict_connectors_to_target` 조건에서만 필터를 활성화. 본 설계에서는 `target_db_code`만 있으면 push-down 적용하는 쪽이 자연스럽다 — `restrict_connectors_to_target`은 커넥터 connect 레이어 제어용으로 한정. 해당 디커플링을 반영할지 최종 확인.
3. **DEV→ADWOWN 이중 매핑의 근본 해소**: 외부망 샘플이 실제 Sybase IQ 테이블명(`TB_ADW_*`)을 재사용하는 설계적 결정이 있다면 유지. 장기적으로 `TB_DEV_*` 명명 규약을 도입하면 `parse_db_source` 오태깅이 사라지고 매핑이 깔끔해짐 — 별도 논의 항목.
4. **parse_db_source 오태깅 문제**: 외부망에서 `TB_ADW_*` 572건이 여전히 `db_source="sybase"`로 태깅되는 문제는 남음. push-down 필터로 **결과 집합은 올바르게 제한되지만**, 태깅 값이 내부적으로 부정확한 점은 추가 고려 대상. 단기 영향은 없지만 `sql_executor.get_query_db(db_source=ct.db_source)` 경로에서 잘못된 커넥터로 라우팅될 여지 → 검증 필요.
5. **인덱스**: MongoDB `dpasset_table.schema_name` 필드에 인덱스 존재 여부 확인 필요. 현재 572건 규모에서는 무시 가능하나 폐쇄망 수만 건 시 `(schema_name, name)` 복합 인덱스 권장.

## 작업 단계

1. 본 설계 문서 리뷰 (design-review 서브에이전트)
2. 리뷰 반영 후 구현
3. 단위 테스트 추가
4. 로컬 기동 및 로그 검증
5. PR (시딩 재실행 불필요, 기존 데이터 유지)
