# 모듈 배치 / 책임 혼재 상세 리포트

- **검토 일시**: 2026-03-30
- **검토 관점**: 구현된 모듈과 성격이 맞지 않은 코드 배치, 비즈니스 역할과 책임이 과하게 섞인 코드

---

## 요약

| ID | 카테고리 | 위치 | 한줄 요약 |
|----|---------|------|----------|
| M-01 | 아키텍처 | connectors/impl/reranker.py | ML 추론 서비스가 커넥터 패키지에 위치 (**Critical C-08**) |
| M-02 | 가독성 | insight_builder.py | 범용 유틸 함수가 서비스 내부에 매몰 |
| M-03 | 유지보수성 | planner.py | 도메인 판정 상수가 노드 파일에 하드코딩 |
| R-01 | 유지보수성 | context_explorer.py (1,171줄) | 6-Phase + 날짜탐지 + 배치해석 + 직렬화 단일 파일 |
| R-02 | 유지보수성 | qdrant_connector.py | 벡터검색 + 임베딩 + 리랭킹 3가지 책임 |
| R-03 | 유지보수성 | query_normalizer.py (662줄) | 검증 + 후처리 + LLM 호출 혼재 |
| R-04 | 아키텍처 | client.py → thinking_modes | utils 계층에서 agents 계층 import (레이어 역전) |
| R-05 | 일관성 | clarifier.py, recovery_planner.py | 서비스 레이어 없이 노드에서 LLM 직접 호출 |

---

## M-01. (아키텍처) Reranker가 connectors/impl에 위치

> Critical C-08과 동일 이슈. `20260330-01-critical-issues.md#C-08` 참조.

---

## M-02. (가독성) 범용 유틸 함수가 서비스 내부에 매몰

### 위치
- `src/services/insight_builder.py:387` — `_get_attr_or_key(obj, key, default)`

### 문제 상세

```python
def _get_attr_or_key(obj: Any, key: str, default: Any = None) -> Any:
    """Pydantic 모델이면 getattr, dict이면 get으로 접근한다."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)
```

이 함수는 `insight_builder.py` 내부에서만 사용되고 있지만, "Pydantic 모델과 dict를 투명하게 접근하는" 패턴은 프로젝트 전반에서 재사용할 수 있는 범용 유틸이다. `runner.py`에서도 `result.get("key")`와 `result.key` 사이를 오가는 코드가 있어 동일 유틸이 필요하다.

**위험**: 다른 모듈에서 같은 패턴이 필요할 때 `insight_builder`에서 이 함수를 찾기 어렵고, 각자 인라인 구현하게 된다.

### 해결 방안

`src/utils/` 하위로 이동한다.

```python
# src/utils/__init__.py 또는 src/utils/helpers.py
def get_attr_or_key(obj: Any, key: str, default: Any = None) -> Any:
    """Pydantic 모델이면 getattr, dict이면 dict.get으로 접근한다."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)
```

`insight_builder.py`에서는 `from src.utils import get_attr_or_key`로 import한다. 다만 현재 사용처가 1곳뿐이라면 즉시 이동 대신 주석으로 위치를 안내하고, 두 번째 사용처가 생길 때 이동하는 것도 합리적이다.

---

## M-03. (유지보수성) 도메인 판정 상수가 노드 파일에 하드코딩

### 위치
- `src/agents/nodes/reason/planner.py:276-283` — `VAGUE_OUTPUT_KEYWORDS`, `EXTRACTION_KEYWORDS`

### 문제 상세

```python
VAGUE_OUTPUT_KEYWORDS = {"현황", "추이", "분석", "리스트", "목록", "비교", "요약", ...}
EXTRACTION_KEYWORDS = {"조회", "추출", "다운로드", "엑셀", "데이터", ...}
```

이 상수들은 **사용자 질의의 출력 의도를 판정하는 도메인 규칙**이다. 현재 `planner_system.txt` 프롬프트에도 주입되며 "SSOT" 주석이 달려있다.

문제는 이 상수가 **노드 파일(planner.py)** 안에 있다는 점이다. 노드의 역할은 "state에서 읽고 → 서비스 호출 → state에 쓰기"인데, 도메인 판정 로직과 상수까지 포함하면 **노드의 책임이 과해진다**.

또한 `subclassify_data_query()`의 `analysis_signals`(`intent_resolver.py`)와 `VAGUE_OUTPUT_KEYWORDS`가 의미적으로 겹치지만 별도 관리되고 있다.

### 해결 방안

**방안 A (권장)**: `resources/domain/` YAML로 이동

```yaml
# resources/domain/output_intent_keywords.yaml
vague_output:
  - 현황
  - 추이
  - 분석
  - 리스트
  # ...

extraction:
  - 조회
  - 추출
  - 다운로드
  # ...
```

`planner.py`에서는 `load_yaml("domain/output_intent_keywords.yaml")`로 로드한다. 비개발자(금융 도메인 전문가)도 YAML 수정만으로 키워드를 조정할 수 있다.

**방안 B**: `services/intent_resolver.py`에 통합

`intent_resolver.py`가 이미 의도 분류를 담당하므로, 출력 의도 판정 함수도 이 서비스에 추가하고, `planner.py`는 이 서비스를 호출한다.

---

## R-01. (유지보수성) context_explorer.py 1,171줄 — 책임 과다

### 위치
- `src/agents/nodes/reason/context_explorer.py` 전체

### 문제 상세

단일 파일에 **4가지 성격이 다른 로직**이 혼재한다:

| 책임 | 함수 수 | 줄 수 (추정) | 성격 |
|------|--------|-------------|------|
| 6-Phase 오케스트레이션 | ~5개 | ~300줄 | 노드 메인 로직 |
| 테이블 관찰 (날짜 분포, 샘플) | ~8개 | ~250줄 | 데이터 수집 |
| 배치 LLM 해석 + 직렬화 | ~10개 | ~400줄 | LLM 연동 |
| 테이블 비교 판정 (레거시) | ~4개 | ~120줄 | 죽은 코드 |

`BatchInterpretResult`가 일반 클래스로 구현되어 프로젝트의 Pydantic v2 사용 패턴과도 불일치한다.

**위험**:
- 한 Phase의 버그를 수정하려면 1,171줄 전체를 이해해야 함
- 날짜 관찰 로직을 다른 노드에서 재사용하려면 이 거대한 파일을 import해야 함
- 코드 리뷰 시 변경 범위 파악이 어려움

### 해결 방안

**최소 3개 모듈로 분리한다:**

```
src/agents/nodes/reason/
├── context_explorer.py          # 메인 노드 + 6-Phase 오케스트레이션 (~300줄)
├── _table_observation.py        # 날짜 분포/샘플 수집, 날짜 패턴 탐지 (~250줄)
│   ├── DATE_SUFFIXES, KOREAN_DATE_KEYWORDS
│   ├── _observe_all_date_distributions()
│   ├── _sample_unsampled_tables()
│   ├── _identify_key_date_columns()
│   └── _detect_date_pattern()
└── _batch_interpreter.py        # 배치 LLM 해석 + 직렬화 (~400줄)
    ├── BatchInterpretResult (→ Pydantic BaseModel로 전환)
    ├── _interpret_batch()
    ├── _serialize_knowledge_items()
    └── _serialize_candidates()
```

`context_explorer.py`는 각 모듈의 함수를 import하여 6-Phase 루프를 오케스트레이션하는 역할만 담당한다. 접두사 `_`는 모듈 내부 전용임을 표시한다.

---

## R-02. (유지보수성) QdrantConnector가 3가지 책임을 가짐

### 위치
- `src/connectors/impl/qdrant_connector.py`

### 문제 상세

단일 클래스가 3가지 독립적인 책임을 담당한다:

| 책임 | 메서드 | 의존성 |
|------|--------|--------|
| 벡터 검색 | `search()`, `search_manual()`, `search_sql_history()` | Qdrant 클라이언트 |
| 임베딩 생성 | `encode()`, `encode_batch()`, `encode_dense_only()` | BGE-M3 모델 |
| 리랭킹 | `_rerank()` | Reranker 서비스 |

docstring에 "임베딩은 검색 인프라의 일부이므로 커넥터에 통합한다"고 설명하고 있으나, `encode()` / `encode_batch()`는 **시딩 스크립트(seed_sql_history.py)**에서도 사용되는 범용 기능이다.

**위험**:
- 임베딩 모델을 교체하면 검색 커넥터까지 영향
- 리랭킹 로직 변경 시 벡터 검색 테스트도 재실행 필요
- `encode()`를 다른 서비스에서 사용하려면 QdrantConnector 전체를 주입받아야 함

### 해결 방안

**점진적 분리 (3단계):**

1단계 (즉시): Reranker는 이미 별도 파일(`reranker.py`)이므로, `_rerank()` 메서드를 제거하고 직접 `get_reranker().rerank()`를 호출

2단계 (중기): 임베딩을 별도 서비스로 분리
```python
# src/services/embedding.py
class EmbeddingService:
    """BGE-M3 임베딩 생성 서비스."""
    def encode(self, text: str) -> EmbeddingResult: ...
    def encode_batch(self, texts: list[str]) -> list[EmbeddingResult]: ...
    def encode_dense_only(self, text: str) -> list[float]: ...
```

3단계: QdrantConnector는 순수 벡터 검색만 담당
```python
class QdrantConnector(SearchConnector):
    def __init__(self, embedding_service, reranker): ...
    def search(self, query, **kwargs) -> list[dict]: ...
```

---

## R-03. (유지보수성) query_normalizer.py 662줄 — 검증/후처리/LLM 혼재

### 위치
- `src/services/query_normalizer.py` 전체

### 문제 상세

662줄의 파일에 3가지 성격이 다른 로직이 섞여 있다:

| 책임 | 함수 | 줄 수 (추정) |
|------|------|-------------|
| LLM 호출 + 오케스트레이션 | `run_normalization`, `_run_phase2`, `_call_llm_and_parse` | ~150줄 |
| 구조 검증 | `_validate_structure`, `_validate_intent`, `_validate_entities` 등 8개 | ~250줄 |
| 후처리 | `_post_aggregate_fix`, `_post_rank_fix` 등 5개 | ~200줄 |

검증 함수 8개는 NormalizedQuery의 각 슬롯을 독립적으로 검증하는 순수 함수이며, 후처리 함수 5개도 마찬가지다. 이들은 LLM 호출과 무관한 **데이터 변환 로직**이다.

### 해결 방안

**2개 모듈로 분리한다:**

```
src/services/
├── query_normalizer.py           # LLM 호출 + 오케스트레이션 (~200줄)
│   ├── run_normalization()
│   ├── _run_phase2()
│   └── _call_llm_and_parse()
├── _normalization_validator.py   # 슬롯별 검증 (~250줄)
│   ├── validate_structure()
│   ├── _validate_intent()
│   ├── _validate_entities()
│   └── ... (8개 검증 함수)
└── _normalization_postprocess.py # 후처리 (~200줄)
    ├── postprocess()
    ├── _post_aggregate_fix()
    └── ... (5개 후처리 함수)
```

`query_normalizer.py`는 `_normalization_validator.validate_structure()`와 `_normalization_postprocess.postprocess()`를 호출하는 오케스트레이터 역할만 담당한다.

---

## R-04. (아키텍처) utils 계층에서 agents 계층 import — 레이어 역전

### 위치
- `src/utils/llm/client.py:202-203`

### 문제 상세

```python
# client.py (utils 계층)
from src.agents.nodes.thinking_modes import get_thinking_mode
```

프로젝트의 레이어 구조:
```
agents (상위) → services (중간) → utils (하위)
```

`utils`는 하위 계층으로서 `agents` 계층에 의존하면 안 된다. 현재 `client.py`가 `thinking_modes`를 import하는 것은 **상향 의존(layer inversion)** 이며, 순환 의존의 원인이 될 수 있다.

### 해결 방안

**방안 A (권장)**: thinking_mode를 호출자가 파라미터로 전달

```python
# client.py — thinking_mode를 인자로 받음
async def create(self, *, thinking_mode: str | None = None, **kwargs):
    if thinking_mode is None:
        thinking_mode = "auto"  # 디폴트
    ...
```

```python
# 노드에서 호출 시
from src.agents.nodes.thinking_modes import get_thinking_mode
result = await client.create(thinking_mode=get_thinking_mode("planner"), ...)
```

**방안 B**: `thinking_modes.py`를 `src/config/` 또는 `src/utils/llm/`으로 이동

```bash
git mv src/agents/nodes/thinking_modes.py src/utils/llm/thinking_modes.py
```

방안 A가 의존 방향을 깨끗하게 유지하므로 권장한다.

---

## R-05. (일관성) clarifier/recovery_planner가 서비스 레이어 없이 LLM 직접 호출

### 위치
- `src/agents/nodes/interpret/clarifier.py:101-108`
- `src/agents/nodes/reason/recovery_planner.py` (내부 LLM 호출부)

### 문제 상세

프로젝트의 노드-서비스 위임 패턴:

| 계층 | 노드 | 위임 서비스 | 패턴 준수 |
|------|------|-----------|----------|
| interpret | preprocessor | input_sanitizer | O |
| interpret | history_resolver | history_resolver | O |
| interpret | intent_classifier | intent_resolver | O |
| interpret | query_normalizer | query_normalizer | O |
| **interpret** | **clarifier** | **(없음 — 직접 호출)** | **X** |
| reason | confidence_evaluator | confidence_scorer | O |
| **reason** | **recovery_planner** | **(없음 — 직접 호출)** | **X** |
| present | analyzer | data_analyzer | O |
| present | formatter | response_formatter | O |

`clarifier`와 `recovery_planner`만 **서비스 레이어 없이 노드에서 직접 `client.messages.create`를 호출**한다. 이로 인해:
1. `llm_call_with_parse_retry`의 재시도/에러처리가 적용되지 않음
2. thinking_mode 설정이 누락됨
3. 노드 파일에 프롬프트 조립 + 응답 파싱 + 비즈니스 로직이 혼재

### 해결 방안

각각에 대응하는 서비스 모듈을 신설한다:

```python
# src/services/clarifier.py
async def generate_clarification(
    query: str,
    normalized: NormalizedQuery | None,
    *,
    system_prompt: str,
    user_template: str,
) -> str:
    """모호한 요청에 대한 명확화 질문을 생성한다."""
    return await llm_call_with_parse_retry(
        system_prompt=system_prompt,
        user_message=user_template.format(query=query),
        parse_fn=lambda text: text.strip(),
        node_name="clarifier",
    )
```

```python
# src/services/recovery_planner.py (이름 충돌 주의 — 노드와 구분)
async def plan_recovery(
    dead_ends: list[DeadEnd],
    knowledge_items: list[KnowledgeItem],
    *,
    system_prompt: str,
) -> RecoveryPlan:
    """실패한 가설을 분석하고 새 탐색 계획을 수립한다."""
    ...
```

노드 파일은 "state에서 읽기 → 서비스 호출 → state에 쓰기"의 얇은 래퍼 역할만 수행한다.
