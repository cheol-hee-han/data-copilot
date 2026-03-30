# 인터페이스 규약 불일치 상세 리포트

- **검토 일시**: 2026-03-30
- **검토 관점**: 인터페이스는 비슷한데 동작 규약이 제각각인 코드, 변경 전파 범위가 지나치게 큰 코드

---

## 요약

| ID | 카테고리 | 위치 | 한줄 요약 |
|----|---------|------|----------|
| I-01 | 보안 | sql_executor vs sql_validator | validate_sql_safety 이중 구현, 시그니처 불일치 (**Critical C-03**) |
| I-02 | 일관성 | 4곳 LLM 직접 호출 | llm_call_with_parse_retry 미사용, 재시도/에러처리 불일치 |
| I-03 | 보안 | hive/impala execute_query | params 파라미터 무시 (**Critical C-04**) |
| I-04 | 일관성 | 노드별 tracker import | 지연 import vs 상단 import 혼재 |
| P-01 | 유지보수성 | config.py | 단일 클래스 250줄, 모든 설정 집중 |
| P-02 | 정합성 | pipeline.py _handle_error | SQL_MAX_RETRY vs MAX_GENERATES 불일치 (**Critical C-06**) |

> **Note**: 이전 리뷰에서 `connect_all()`이 매 요청마다 재연결한다고 보고했으나, 실제 코드 확인 결과 `_connected` 플래그로 **이미 멱등 처리**되어 있음 (`manager.py:82-84`). 해당 부분은 오탐으로 삭제됨.

---

## I-01. (보안) validate_sql_safety 이중 구현 — 시그니처 불일치

> Critical C-03과 동일 이슈. `20260330-01-critical-issues.md#C-03` 참조.

---

## I-02. (일관성) LLM 호출 방식이 4곳에서 공통 유틸과 불일치

### 위치
- `src/agents/nodes/interpret/clarifier.py:101-108` — `client.messages.create` 직접 호출
- `src/agents/nodes/reason/recovery_planner.py` — `client.messages.create` 직접 호출
- `src/services/data_analyzer.py:152` (generate_svg_via_llm) — `client.messages.create` 직접 호출
- `src/services/response_formatter.py:117` (format_response) — `client.messages.create` 직접 호출

### 문제 상세

프로젝트에는 LLM 호출을 위한 공통 유틸 `llm_call_with_parse_retry`가 있다. 이 유틸은 다음을 제공한다:

| 기능 | llm_call_with_parse_retry | client.messages.create 직접 호출 |
|------|--------------------------|--------------------------------|
| JSON 파싱 실패 시 자동 재시도 | O (교정 메시지 포함 최대 3회) | X |
| 네트워크 에러 재시도 | O | X |
| 타임아웃 설정 | O (settings 기반) | X (AsyncAnthropic 기본값) |
| thinking_mode 적용 | O | X |
| 트래커 자동 기록 | O | X |
| 노드명 컨텍스트 전파 | O | X |

**4곳에서 직접 호출하는 코드는 위 6가지 기능이 모두 누락된다.**

**시나리오**: 폐쇄망 LLM(Solar Pro 2 70B)은 JSON 출력 안정성이 낮아 파싱 실패 확률이 높다. `llm_call_with_parse_retry`를 사용하는 노드는 자동 재시도로 복구되지만, 직접 호출하는 4곳은 첫 번째 실패에서 바로 에러가 발생한다.

### 해결 방안

**4곳 모두 `llm_call_with_parse_retry`로 통일한다.**

```python
# 변경 전 (clarifier.py)
client = get_llm_client()
response = await client.messages.create(
    model=settings.llm_model,
    system=system_prompt,
    messages=[{"role": "user", "content": user_message}],
    max_tokens=settings.llm_max_tokens,
)
return response.content[0].text

# 변경 후
from src.utils.llm import llm_call_with_parse_retry

result = await llm_call_with_parse_retry(
    system_prompt=system_prompt,
    user_message=user_message,
    parse_fn=lambda text: text.strip(),  # 단순 텍스트 반환이면 identity
    node_name="clarifier",
)
```

`generate_svg_via_llm`처럼 JSON 파싱이 불필요한 경우에도, `parse_fn=lambda t: t`로 identity를 전달하면 재시도/타임아웃/트래커 기능은 그대로 활용할 수 있다.

파싱이 필요 없는 경우를 위한 **간소화 래퍼**를 추가하는 것도 방법이다:

```python
# src/utils/llm/retry.py
async def llm_call_simple(
    *, system_prompt: str, user_message: str, node_name: str
) -> str:
    """파싱 없이 LLM 원문 응답을 반환한다. 재시도/트래커는 적용됨."""
    return await llm_call_with_parse_retry(
        system_prompt=system_prompt,
        user_message=user_message,
        parse_fn=lambda t: t,
        node_name=node_name,
    )
```

---

## I-03. (보안) Hive/Impala execute_query에서 params 완전 무시

> Critical C-04와 동일 이슈. `20260330-01-critical-issues.md#C-04` 참조.

---

## I-04. (일관성) 노드별 tracker import 패턴 혼재

### 위치
- `src/agents/nodes/interpret/intent_classifier.py:95` — **함수 내부 지연 import**
- `src/agents/nodes/interpret/query_normalizer.py:93` — **함수 내부 지연 import**
- `src/agents/nodes/reason/confidence_evaluator.py:41` — **파일 상단 import**
- `src/agents/nodes/reason/confidence_evaluator.py:63` — `ReadinessVerdict`도 함수 내부 지연 import

### 문제 상세

동일한 의존성(`src.utils.tracker`)의 import 방식이 **파일마다 다르다**:

```python
# intent_classifier.py — 함수 내부 지연 import
async def classify_intent_node(state):
    from src.utils.tracker import get_current_tracker  # 함수 진입마다 실행
    tracker = get_current_tracker()
    ...

# confidence_evaluator.py — 파일 상단 import
from src.utils.tracker import get_current_tracker  # 모듈 로드 시 1회
```

지연 import가 필요한 이유(순환참조 등)가 있다면 코드에 문서화되어야 한다. 순환참조가 아니라면 파일 상단 import가 Python 관례에 맞고, 성능(매 호출마다 import 해소 비용)에도 유리하다.

### 해결 방안

1. **순환참조 확인**: `src.utils.tracker`가 `src.agents.nodes.*`를 import하는지 확인
2. 순환참조가 **없으면**: 모든 노드에서 파일 상단 import로 통일

```python
# 모든 노드 파일 — 파일 상단
from src.utils.tracker import get_current_tracker
```

3. 순환참조가 **있으면**: `TYPE_CHECKING` 가드와 함께 문서화

```python
# 순환참조 해소를 위한 지연 import (src.utils.tracker → src.agents.nodes 순환 방지)
from src.utils.tracker import get_current_tracker  # noqa: 지연 import 필요
```

---

## P-01. (유지보수성) config.py 단일 클래스에 모든 설정 집중 — 변경 전파 범위 과대

### 위치
- `src/config.py` 전체 (250+ 라인)

### 문제 상세

하나의 `Settings` 클래스에 다음이 모두 포함되어 있다:

| 영역 | 필드 수 (추정) | 예시 |
|------|-------------|------|
| PostgreSQL (정보계) | ~6개 | info_db_host, info_db_port, info_db_password, ... |
| PostgreSQL (이력) | ~6개 | history_db_host, history_db_port, ... |
| ElasticSearch | ~5개 | es_host, es_port, es_password, ... |
| Qdrant | ~4개 | qdrant_host, qdrant_port, ... |
| MongoDB | ~4개 | mongo_host, mongo_port, ... |
| Neo4j | ~4개 | neo4j_uri, neo4j_password, ... |
| Redis | ~3개 | redis_host, redis_port, ... |
| Hive / Impala / Sybase | ~12개 | 각 4개씩 |
| LLM | ~8개 | llm_model, llm_provider, anthropic_api_key, ... |
| 임베딩 / 리랭커 | ~6개 | embedding_model, reranker_model, ... |
| 파이프라인 | ~10개 | max_generates, normalization_enabled, ... |
| UI / 차트 | ~8개 | chart_width, chart_height, ... |

**위험**:
- 새 DB 커넥터를 추가하면 `config.py` + 커넥터 + 테스트가 **동시에 변경**됨
- 필드명 충돌 가능성 증가 (예: 여러 DB의 `_host`, `_port`, `_password` 접두사 관리)
- IDE 자동완성에서 70+ 필드가 한꺼번에 나열됨
- 환경변수 `.env` 파일도 비대해져 관리 부담 증가

### 해결 방안

**Pydantic v2의 nested model을 활용하여 설정을 영역별로 분리한다:**

```python
# src/config.py
from pydantic import SecretStr
from pydantic_settings import BaseSettings

class DatabaseSettings(BaseSettings):
    host: str = "localhost"
    port: int = 5432
    user: str = ""
    password: SecretStr = SecretStr("")
    database: str = ""

class LLMSettings(BaseSettings):
    model: str = "claude-sonnet-4-20250514"
    provider: str = "anthropic"
    api_key: SecretStr = SecretStr("")
    max_tokens: int = 4096
    timeout: int = 30

class PipelineSettings(BaseSettings):
    max_generates: int = 4
    max_replans: int = 3
    normalization_enabled: bool = True
    normalization_phase2_enabled: bool = False

class Settings(BaseSettings):
    # 중첩 설정
    info_db: DatabaseSettings = DatabaseSettings()
    history_db: DatabaseSettings = DatabaseSettings()
    llm: LLMSettings = LLMSettings()
    pipeline: PipelineSettings = PipelineSettings()
    # ...

    model_config = SettingsConfigDict(
        env_file=".env",
        env_nested_delimiter="__",  # INFO_DB__HOST=... 형식
    )
```

`.env` 파일:
```bash
INFO_DB__HOST=localhost
INFO_DB__PORT=5432
INFO_DB__PASSWORD=secret
LLM__MODEL=claude-sonnet-4-20250514
LLM__API_KEY=sk-...
```

**점진적 적용**: 한 번에 전체를 바꾸기보다, 새 커넥터 추가 시점에 해당 영역부터 분리해 나간다.

---

## P-02. (정합성) _handle_error의 SQL_MAX_RETRY vs MAX_GENERATES 불일치

> Critical C-06과 동일 이슈. `20260330-01-critical-issues.md#C-06` 참조.
