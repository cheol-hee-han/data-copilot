# 코드 리뷰 보고서 — 설계/구조/일관성 점검

- **작성일**: 2026-03-23
- **대상 범위**: `src/` 전체 (77개 Python 파일, 1.9MB)
- **관점**: OOP 설계 패턴, 네이밍 일관성, 중복 구현, 불필요한 복잡성, 유지보수성, 가독성

---

## 1. 총평

전체적으로 **설계 수준이 높고 일관성이 잘 유지된 프로젝트**입니다.
계층 구조(connectors → services → nodes → graph)가 명확하고,
Pydantic v2 기반의 타입 안전한 모델, async/await 일관 적용,
Defense-in-Depth 보안 설계, 외부 프롬프트 관리 등 엔터프라이즈급 설계 원칙이 잘 적용되어 있습니다.

아래는 기능적으로 문제가 없더라도 **유지보수·가독성·관리 관점에서 개선 여지가 있는 16개 항목**입니다.

---

## 2. 발견 항목

### [CR-01] 보안 검증 로직 3중 중복

**파일**:
- `src/utils/security.py` — `validate_sql_safety()`, L173-235
- `src/services/sql_safety_checker.py` — `FORBIDDEN_PATTERNS`, L33-81
- `src/services/input_sanitizer.py` — `_SUSPICIOUS_PATTERNS`, L40-52

**현상**:
SQL/인젝션 방어 패턴이 3개 모듈에 걸쳐 유사하게 반복됩니다.

| 패턴 | security.py | sql_safety_checker.py | input_sanitizer.py |
|---|---|---|---|
| DML/DDL 금지 | `validate_sql_safety()` L189-196 | `FORBIDDEN_PATTERNS` L33-37 | `_SUSPICIOUS_PATTERNS` L41 |
| 시간지연 함수 | L199-208 | L70-73 | L47 |
| 파일 I/O | L211-220 | L55-69 | L48 |
| 시스템 카탈로그 | `_CATALOG_PATTERN` L29-31 | L46-49 | L51 |
| SQL 주석 | L232-233 | L74-75 | L43-44 |

**현상유지**:
- 장점: Defense-in-Depth 의도가 명확. 각 계층이 독립적으로 동작하여 하나가 무력화되어도 다른 계층이 방어.
- 단점: 패턴 추가/수정 시 3곳을 동시에 변경해야 함. 불일치 발생 가능성 높음. 실제로 `security.py`의 `forbidden_keywords` 리스트와 `sql_safety_checker.py`의 `FORBIDDEN_PATTERNS`가 미묘하게 다름 (CALL, EXEC 포함 여부 등).

**개선안**:
공통 패턴 정의를 `src/utils/security_patterns.py`에 단일 소스로 관리하고,
각 계층이 용도에 맞는 서브셋을 import하여 사용.
계층별 고유 패턴만 로컬에서 추가하는 구조.

**심각도**: 중 / **개선 난이도**: 중

---

### [CR-02] `PipelineState.normalized_query`가 `Any` 타입

**파일**: `src/agents/state/state.py`, L88

**현상**:
```python
normalized_query: Any = None  # NormalizedQuery 인스턴스 (순환 import 방지로 Any)
```

`NormalizedQuery`는 `src/agents/models/normalization.py`에 정의되어 있으나,
순환 import 문제로 `Any`로 선언. 이로 인해:
1. `mypy --strict` 정책과 상충
2. IDE 자동완성 불가
3. `sql_prompt_assembler.py`에서 `getattr()` 30+회 사용하는 원인 제공

**현상유지**:
- 장점: 순환 import 문제 즉시 회피
- 단점: 프로젝트 전반의 타입 안전성 약화. `NormalizedQuery`의 필드가 변경될 때 컴파일 타임에 감지 불가.

**개선안**:
방법 A — `NormalizedQuery`를 `src/models/normalization.py`로 이동 (순환 자체 해소)
방법 B — `TYPE_CHECKING` 가드 사용:
```python
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.agents.models.normalization import NormalizedQuery
```

방법 A를 권장. `src/models/`는 이미 공유 모델 패키지이며, `NormalizedQuery`는 서비스 계층에서도 참조하므로 위치가 적합.

**심각도**: 높음 / **개선 난이도**: 중

---

### [CR-03] `SearchConnector.search()` 메서드의 dead code 성격

**파일**:
- `src/connectors/interfaces.py`, L39-44 — `SearchConnector.search()` 추상 메서드
- `src/connectors/impl/elasticsearch_connector.py`, L78-91 — kwargs 기반 라우팅

**현상**:
`search()` 메서드가 `search_type` kwarg으로 3종 검색을 분기하지만,
실제 호출 코드는 전부 `search_table_meta()`, `search_report_sql()`, `search_code_meta()`를 직접 호출합니다.
`QdrantConnector`도 `search_manual()`, `search_sql_history()`를 직접 노출하며 `search()`는 형식적으로만 구현.

**현상유지**:
- 장점: `SearchConnector` 인터페이스 계약 형식 준수
- 단점: 사용되지 않는 `search()` 메서드 유지 비용. kwargs 라우팅은 타입 안전하지 않음. ES와 Qdrant의 검색 API가 이질적이어서 공통 `search()` 인터페이스가 실질적 추상화를 제공하지 못함.

**개선안**:
`SearchConnector`에서 `search()` 추상 메서드를 제거하고,
각 커넥터 구현체가 용도별 전용 메서드만 노출하도록 변경.
또는 `SearchConnector`를 `TableMetaSearchable`, `SQLHistorySearchable` 등 역할 기반 프로토콜로 분리.

**심각도**: 낮음 / **개선 난이도**: 낮음

---

### [CR-04] `QueryCategory` vs `IntentType` 개념 중첩

**파일**:
- `src/models/enums.py` — `IntentType` (7개 값)
- `src/agents/models/normalization.py`, L29-39 — `QueryCategory` (5개 값)
- `src/services/intent_resolver.py`, L187-205 — `_map_category_to_intent()` 수동 매핑

**현상**:
두 enum이 유사한 분류 체계를 다른 이름과 값으로 표현합니다.

| QueryCategory | → IntentType 매핑 |
|---|---|
| DATA_QUERY | DATA_EXTRACTION (→ 키워드로 DATA_ANALYSIS 세분류) |
| CASUAL_TALK | CASUAL_TALK |
| META_QUESTION | META_QUESTION |
| CLARIFICATION | CLARIFICATION_NEEDED |
| AMBIGUOUS | CLARIFICATION_NEEDED |

**현상유지**:
- 장점: 각 단계의 관심사 분리 (Gate 5-category vs Pipeline 7-type)
- 단점: 새 카테고리 추가 시 양쪽 enum + 매핑 함수 모두 수정 필요. 개발자가 어느 enum을 사용해야 하는지 즉시 판단 어려움.

**개선안**:
`QueryCategory`를 `IntentType`의 상위 그룹으로 재정의하고,
`IntentType`에 `@classmethod from_category()` 매핑을 통합.
또는 `QueryCategory`를 `IntentType`의 subset으로 통합하여 단일 enum 유지.

**심각도**: 중 / **개선 난이도**: 높음

---

### [CR-05] `NormIntentType` 네이밍의 모호함

**파일**: `src/agents/models/normalization.py`, L46-57

**현상**:
프로젝트에 "의도" 관련 enum이 3종 존재합니다:
1. `IntentType` — 파이프라인 라우팅용 (DATA_EXTRACTION, CASUAL_TALK, ...)
2. `QueryCategory` — Intent Gate 출력 (DATA_QUERY, AMBIGUOUS, ...)
3. `NormIntentType` — 8-Slot 정규화의 질의 유형 (EXTRACT, AGGREGATE, COMPARE, ...)

`NormIntentType`의 `Norm` 접두사가 "정규화된 질의의 SQL 패턴 유형"임을 즉시 전달하지 못합니다.

**현상유지**:
- 장점: 기존 코드 변경 불필요
- 단점: 3종 enum 간 관계와 역할 구분이 이름만으로 명확하지 않음

**개선안**:
`NormIntentType` → `SQLPatternType` 또는 `QueryShape`로 리네이밍하여
"SQL 뼈대를 결정하는 유형"이라는 의미를 명확화.

**심각도**: 중 / **개선 난이도**: 중 (전체 rename 필요)

---

### [CR-06] 슬롯 모델의 Enum 미활용 (str 타입 필드)

**파일**: `src/agents/models/normalization.py`, L256-378

**현상**:
Enum을 정의했지만 슬롯 모델 필드가 전부 `str`입니다:
```python
class EntitySlot(BaseModel):
    type: str = "DIRECT"       # ← EntityType enum이 있지만 str
    confidence: str = "MEDIUM"  # ← ConfidenceLevel enum이 있지만 str
```
그리고 별도의 `VALID_*` 집합 (L384-399)으로 문자열 검증을 수동 수행합니다.

**현상유지**:
- 장점: LLM이 대소문자 불일치/오타를 반환할 때 파싱 실패 방지. 유연한 처리 가능.
- 단점: Enum을 정의한 의미가 퇴색. Pydantic validator 체인 활용 불가. 검증 로직이 `query_normalizer.py` 서비스 계층에 분산.

**개선안**:
Pydantic `@field_validator`에서 `str.upper() → Enum` 변환 + 실패 시 기본값 fallback 로직을 슬롯 모델 내부에 캡슐화:
```python
class EntitySlot(BaseModel):
    type: EntityType = EntityType.DIRECT

    @field_validator("type", mode="before")
    @classmethod
    def normalize_type(cls, v):
        if isinstance(v, str):
            try:
                return EntityType(v.upper())
            except ValueError:
                return EntityType.DIRECT
        return v
```
LLM 출력 파싱 유연성과 타입 안전성을 동시 확보.

**심각도**: 중 / **개선 난이도**: 중

---

### [CR-07] `search_context_assembler.py`의 `_fetch_*` 함수 구조 반복

**파일**: `src/services/search_context_assembler.py`, L39-371

**현상**:
6개의 `_fetch_*` 함수가 거의 동일한 구조를 반복합니다:
```python
async def _fetch_XXX(query, tracker, failed_sources):
    start = time.perf_counter()
    try:
        manager = get_connector_manager()
        results = await manager.XXX.search_YYY(query)
        # ... 결과 변환 (소스별로 다름) ...
        elapsed = (time.perf_counter() - start) * 1000
        logger.info("XXX 검색 완료", query=query, results_count=len(...), latency_ms=...)
        if tracker and tracker.enabled:
            tracker.track_context_retrieval(source="XXX", ...)
        return results
    except Exception as e:
        logger.warning("XXX 실패, 빈 목록으로 폴백", error=str(e))
        if failed_sources is not None:
            failed_sources.append("XXX")
        return []  # 또는 {}
```
이 패턴이 ~300줄에 걸쳐 6벌 반복됩니다.

**현상유지**:
- 장점: 각 소스별 결과 변환 로직이 다르므로 명시적. 디버깅 시 스택트레이스 위치가 명확. 새 개발자가 개별 함수만 읽어도 흐름을 이해 가능.
- 단점: 보일러플레이트 ~300줄. 새 소스 추가 시 복붙 유혹. tracker 추적 로직 6벌 중복. 타이밍/로깅 형식 변경 시 6곳 수정 필요.

**개선안**:
공통 래퍼 함수로 try/except + timing + tracker + failed_sources 로직 추출:
```python
async def _fetch_with_tracking(
    source_key: str,
    fetch_fn: Callable,
    query: str,
    tracker: EvaluationTracker | None,
    failed_sources: list[str] | None,
    default: T,
) -> T:
    ...
```
소스별 차이(결과 변환, 로깅 detail)만 콜백/파라미터로 주입.

**심각도**: 낮음 / **개선 난이도**: 낮음

---

### [CR-08] `ConnectorManager.connect_all()`의 순차 연결

**파일**: `src/connectors/manager.py`, L42-55

**현상**:
```python
await self.es.connect()
await self.info_db.connect()
await self.history_db.connect()
await self.qdrant.connect()
```
4개 커넥터가 서로 독립적임에도 순차 await.

**현상유지**:
- 장점: 실패 시 어느 커넥터에서 실패했는지 즉시 파악 가능. 디버깅 용이.
- 단점: 초기화 시간이 커넥터 수에 비례하여 증가. (예: 각 300ms이면 1.2초 → 병렬 시 ~300ms)

**개선안**:
```python
results = await asyncio.gather(
    self.es.connect(),
    self.info_db.connect(),
    self.history_db.connect(),
    self.qdrant.connect(),
    return_exceptions=True,
)
# 개별 실패 처리
for name, result in zip(["es", "info_db", "history_db", "qdrant"], results):
    if isinstance(result, Exception):
        logger.error(f"{name} 연결 실패", error=str(result))
```

**심각도**: 낮음 / **개선 난이도**: 낮음

---

### [CR-09] `table_selection_verdict`가 str (Enum 미사용)

**파일**:
- `src/agents/state/state.py`, L107 — `table_selection_verdict: str = ""`
- `src/agents/graph/pipeline.py`, L144 — `state.table_selection_verdict == "ambiguous"` 문자열 비교
- `src/services/similar_table_resolver.py`, L36-41 — `TableVerdict` enum 정의 있음

**현상**:
`TableVerdict` enum(`PASS`, `WARNING`, `AMBIGUOUS`)이 존재하지만,
`PipelineState`에서는 `str`로 선언하고, 라우팅에서 문자열 리터럴로 비교합니다.

**현상유지**:
- 장점: 없음. 단순 누락으로 판단됩니다.
- 단점: 오타에 취약 (`"ambigous"` 등). IDE 자동완성 불가. enum을 정의한 의미가 퇴색.

**개선안**:
```python
# state.py
from src.services.similar_table_resolver import TableVerdict

table_selection_verdict: TableVerdict = TableVerdict.PASS

# pipeline.py
if state.table_selection_verdict == TableVerdict.AMBIGUOUS:
```

**심각도**: 높음 / **개선 난이도**: 낮음

---

### [CR-10] 모델 위치의 이원화와 re-export 혼동

**파일**:
- `src/models/` — enums.py, context.py, result.py, trace.py (공유 모델)
- `src/agents/models/` — normalization.py, response.py, user_messages.py (에이전트 모델)
- `src/agents/state/state.py`, L33-52 — `src/models/*`을 `# noqa: F401` re-export

**현상**:
`state.py`에서 `TraceEntry`, `IntentType`, `ContextInfo` 등을 re-export하므로,
동일 객체를 두 가지 경로로 import 가능합니다:
```python
from src.agents.state.state import TraceEntry  # re-export 경로
from src.models.trace import TraceEntry         # 원본 경로
```

**현상유지**:
- 장점: 기존 import 경로와의 하위 호환. 노드 코드에서 `state`만 import하면 모든 모델 접근 가능.
- 단점: grep/코드 검색 시 정의 위치 혼동. 새 개발자가 "어디서 import해야 하는가?" 판단 어려움. re-export 목록 관리 부담.

**개선안**:
방법 A (점진적) — 코딩 컨벤션 문서에 "원본 경로에서 import" 원칙 명시. re-export는 유지하되 deprecation 주석 추가.
방법 B (완전 정리) — re-export 제거. 모든 import를 원본 경로로 통일. IDE 일괄 치환으로 수행 가능.

**심각도**: 중 / **개선 난이도**: 낮음

---

### [CR-11] `sql_prompt_assembler.py`의 과도한 `getattr()` 사용

**파일**: `src/services/sql_prompt_assembler.py`, L115-236

**현상**:
`build_normalization_section()` 함수에서 `getattr()` 30+회 사용:
```python
intent = getattr(nq, "intent", None)
if intent:
    primary = getattr(intent, "primary", "")
    secondary = getattr(intent, "secondary", [])
    ...
entities = getattr(nq, "entities", [])
if entities:
    for e in entities:
        term = getattr(e, "term", "")
        etype = getattr(e, "type", "")
        ...
```

**원인**: `normalized_query`의 타입이 `object | None`이기 때문 (CR-02의 파생 문제).

**현상유지**:
- 장점: `Any` 타입에 대한 런타임 안전한 접근
- 단점: 가독성 극히 떨어짐. 속성명 오타를 컴파일 타임에 감지 불가. IDE 리팩토링 불가. 속성 추가/변경 시 누락 위험.

**개선안**:
CR-02를 해결하여 `normalized_query: NormalizedQuery | None`로 선언하면 자동으로 해소:
```python
def build_normalization_section(nq: NormalizedQuery | None) -> str:
    if nq is None:
        return ""
    lines = ["[질의 구조 분석 결과]"]
    lines.append(f"- 질의 유형: {nq.intent.primary}")  # 직접 접근
    ...
```

**심각도**: 높음 / **개선 난이도**: 중 (CR-02 선행 필요)

---

### [CR-12] 프롬프트 로딩의 모듈 초기화 시점 실행

**파일**: `src/agents/nodes/prompts/system_prompts.py`, L53-78

**현상**:
모듈 수준에서 `_read()`를 20+회 호출. import 시점에 파일 I/O 발생:
```python
INTENT_CLASSIFICATION = _read("intent_classification.txt")  # import 시 실행
CLARIFICATION = _read("clarification.txt")
SQL_GENERATION_RULES = _read("sql_generation.txt")
# ... 20+개 더
```

**현상유지**:
- 장점: 런타임에 항상 프롬프트가 준비됨. 추가 호출 비용 없음. 프롬프트 파일 누락을 앱 시작 시 즉시 감지.
- 단점: 테스트 시 특정 프롬프트만 모킹하기 어려움. 파일 하나라도 누락되면 import 자체가 실패하여 전체 앱 미기동. 개발 중 프롬프트 수정 후 프로세스 재시작 필수.

**개선안**:
`@functools.lru_cache()` 또는 Lazy Descriptor로 지연 로딩:
```python
class _PromptLoader:
    @functools.cached_property
    def INTENT_CLASSIFICATION(self) -> str:
        return _read("intent_classification.txt")
    ...

prompts = _PromptLoader()
```
개발 환경에서 reload 옵션도 고려 가능.
다만 현행 eager loading이 "시작 시 전체 검증"이라는 장점을 가지므로, 개발 편의성 vs 안전성 트레이드오프 판단 필요.

**심각도**: 낮음 / **개선 난이도**: 중

---

### [CR-13] 싱글턴 패턴의 일관성 부족

**파일**:
- `src/connectors/manager.py` — `_manager` + `get_connector_manager()` + `reset_connector_manager()`
- `src/utils/llm/client.py` — `_client` + `get_llm_client()` + `reset_llm_client()`
- `src/services/search_query_embedder.py` — 동일 패턴

**현상**:
3개의 싱글턴이 동일한 보일러플레이트 구조를 반복합니다:
```python
_instance: T | None = None

def get_instance() -> T:
    global _instance
    if _instance is None:
        _instance = T(...)
    return _instance

def reset_instance() -> None:
    global _instance
    _instance = None
```

**현상유지**:
- 장점: 각 모듈이 독립적. 외부 의존성 없음. 패턴이 단순하여 이해하기 쉬움.
- 단점: 3곳에 동일한 보일러플레이트. reset 함수 누락 시 테스트 격리 실패. 새 싱글턴 추가 시 복붙.

**개선안**:
경미한 이슈. 현행 유지해도 무방합니다.
만약 개선한다면, 테스트 격리를 위한 공통 `SingletonRegistry`를 도입하여
`reset_all_singletons()` 하나로 모든 싱글턴을 초기화하는 방식도 가능.

**심각도**: 정보 / **개선 난이도**: 낮음

---

### [CR-14] `ElasticSearchConnector`의 실모드 메서드 중복

**파일**: `src/connectors/impl/elasticsearch_connector.py`, L93-202

**현상**:
`search_table_meta()`, `search_report_sql()`, `search_code_meta()` 3개 메서드가 동일한 구조:
```python
body = load_es_query("elasticsearch/XXX_query.json", query)
body["size"] = settings.es_XXX_size
_start = _time.perf_counter()
resp = await self._client.search(
    index=settings.es_XXX_index, body=body, request_timeout=settings.es_request_timeout,
)
results = [hit["_source"] for hit in resp["hits"]["hits"]]
_elapsed = (_time.perf_counter() - _start) * 1000
logger.info("ES XXX 검색", query=..., count=..., latency_ms=...)
return results
```

**현상유지**:
- 장점: 각 인덱스별 커스터마이징 여지. 개별 메서드가 자기 완결적이라 이해하기 쉬움.
- 단점: 보일러플레이트 반복. 로깅 패턴 3벌 중복. 타임아웃 설정 변경 시 3곳 수정.

**개선안**:
내부 `_search_index(index, query_template, size)` 헬퍼로 추출:
```python
async def _search_index(self, index: str, template: str, query: str, size: int) -> list[dict]:
    body = load_es_query(template, query)
    body["size"] = size
    resp = await self._client.search(index=index, body=body, ...)
    return [hit["_source"] for hit in resp["hits"]["hits"]]
```

**심각도**: 낮음 / **개선 난이도**: 낮음

---

### [CR-15] `ContextInfo.domain_terms` 필드명의 의미 모호

**파일**:
- `src/models/context.py`, L45 — `domain_terms: dict[str, str]`
- `src/services/search_context_assembler.py`, L323-371 — `_fetch_code_meta()`

**현상**:
`domain_terms`는 코드 메타에서 가져온 "코드 설명→SQL 조건" 매핑 + 하드코딩 기본값의 혼합 데이터:
```python
domain_terms = {
    "신규 고객": "REG_DT가 해당 기간 내인 고객",  # 하드코딩 기본값
    "연체": "OVERDUE_YN = 'Y'",                    # 하드코딩 기본값
    "보통예금": "ACCT_TYPE_CD = '01'",             # ES 코드 메타에서 로드
}
```
필드명 `domain_terms`만으로는 "금융 용어 사전"인지, "SQL 매핑 사전"인지 파악하기 어렵습니다.
또한 `domain_dictionary.py`의 용어 사전과 별개의 데이터 소스가 되어 관리 포인트가 분산됩니다.

**현상유지**:
- 장점: 기존 인터페이스 유지. 변경 범위 없음.
- 단점: 필드명이 실제 데이터 구조를 반영하지 않음.

**개선안**:
`code_value_mappings` 또는 `term_to_condition_map`으로 리네이밍.
하드코딩 기본값은 `domain_dictionary.py` 또는 `resources/domain/` YAML로 통합.

**심각도**: 정보 / **개선 난이도**: 낮음

---

### [CR-16] 에러 처리 패턴의 비일관성

**파일**: 프로젝트 전반

**현상**:
계층별로 에러 처리 패턴이 다릅니다:

| 계층 | 패턴 | 예시 |
|---|---|---|
| 노드 | `try/except → status=ERROR + error_message` dict 반환 | `preprocess_node`, `generate_sql_node` |
| 서비스 (결과 객체) | dataclass 반환 (`is_error` 필드) | `SanitizeResult`, `IntentResult`, `SafetyCheckResult` |
| 서비스 (예외) | `raise ValueError()` / `raise` | `generate_sql()` — L333 |
| LLM 유틸 | 커스텀 예외 `ParseError` | `llm_call_with_parse_retry()` |

**현상유지**:
- 장점: 각 계층의 관습에 맞는 처리. 서비스의 결과 객체는 성공/실패를 하나의 반환값으로 표현하여 호출 측 코드가 간결.
- 단점: 호출자가 에러 형태를 예측하기 어려움. 새 노드/서비스 작성 시 어떤 패턴을 따를지 불명확. 일부 서비스가 결과 객체와 예외를 혼합 사용.

**개선안**:
코딩 가이드에 아래 원칙을 명시:
- **서비스 계층**: 복구 가능한 실패 → 결과 객체 (`is_error` 패턴). 복구 불가능한 오류 → 예외 발생.
- **노드 계층**: 모든 예외를 catch하여 `status=ERROR + error_message`로 변환.
- **유틸 계층**: 도메인 예외 클래스 사용 (`ParseError`, `SecurityError` 등).

**심각도**: 정보 / **개선 난이도**: 문서화 수준

---

## 3. 우선순위 종합

| 순위 | 번호 | 영역 | 심각도 | 난이도 | 비고 |
|------|------|------|--------|--------|------|
| 1 | CR-02 | `normalized_query: Any` | 높음 | 중 | 타입 안전성의 핵심 병목 |
| 2 | CR-09 | `table_selection_verdict: str` | 높음 | 낮음 | 즉시 수정 가능 |
| 3 | CR-11 | `getattr()` 30+회 | 높음 | 중 | CR-02 선행 시 자동 해소 |
| 4 | CR-01 | 보안 패턴 3중 중복 | 중 | 중 | 불일치 사고 예방 |
| 5 | CR-06 | 슬롯 Enum 미활용 | 중 | 중 | 타입 안전성 개선 |
| 6 | CR-04 | Intent enum 중첩 | 중 | 높음 | 영향 범위가 넓음 |
| 7 | CR-05 | NormIntentType 네이밍 | 중 | 중 | CR-04와 함께 진행 |
| 8 | CR-10 | re-export 혼동 | 중 | 낮음 | 컨벤션 정립으로 해소 가능 |
| 9 | CR-07 | `_fetch_*` 보일러플레이트 | 낮음 | 낮음 | 리팩토링 시 함께 진행 |
| 10 | CR-14 | ES 메서드 중복 | 낮음 | 낮음 | 리팩토링 시 함께 진행 |
| 11 | CR-03 | `search()` dead code | 낮음 | 낮음 | 인터페이스 정리 시 함께 |
| 12 | CR-08 | 순차 connect | 낮음 | 낮음 | 성능 개선 시 함께 |
| 13 | CR-12 | 프롬프트 eager loading | 낮음 | 중 | 트레이드오프 존재 |
| 14 | CR-13 | 싱글턴 패턴 차이 | 정보 | 낮음 | 현행 유지 가능 |
| 15 | CR-15 | domain_terms 네이밍 | 정보 | 낮음 | 기회 있을 때 진행 |
| 16 | CR-16 | 에러 처리 일관성 | 정보 | 문서화 | 가이드 문서 작성 |

---

## 4. 권장 실행 순서

**Phase 1 — 타입 안전성 (CR-02 → CR-11 → CR-09)**
`NormalizedQuery` 위치 이동 → `PipelineState` 타입 힌트 교정 → `getattr()` 직접 접근으로 전환 → `TableVerdict` enum 적용.
이 3건만 처리해도 코드 품질이 체감될 정도로 개선됩니다.

**Phase 2 — 보안 패턴 통합 (CR-01)**
3개 모듈의 중복 패턴을 단일 소스로 통합. 불일치 사고 사전 예방.

**Phase 3 — 모델 정리 (CR-06 → CR-04/05 → CR-10)**
슬롯 Enum 활용, Intent enum 통합, re-export 정리를 일괄 진행.

**Phase 4 — 구조 개선 (CR-07, CR-14, CR-03, CR-08)**
보일러플레이트 축소, dead code 제거, 성능 최적화. 기능 추가 시 함께 진행.
