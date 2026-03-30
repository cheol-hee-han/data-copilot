# LangChain Custom Event Dispatch 메커니즘 리서치

**날짜**: 2026-03-30
**대상**: data-copilot 프로젝트 — LangGraph 노드 내 도메인 특화 텔레메트리 구현
**조사 범위**: langchain-core callbacks custom events API, LangGraph config 전파, async 지원

---

## 요약

langchain-core `0.2.15`부터 `adispatch_custom_event` / `dispatch_custom_event` API가 도입되었다.
이를 통해 LangGraph 노드 내부에서 도메인 결정(SQL 생성, 검색 결과, 신뢰도 판정 등)을
`BaseCallbackHandler.on_custom_event`로 전달하는 구조적 텔레메트리가 가능하다.

**핵심 제약**: 이 함수들은 반드시 **기존 Runnable 실행 컨텍스트 안에서만** 호출 가능하다.
LangGraph 노드는 그래프 실행 시 LangChain Runnable로 래핑되므로 조건을 자동으로 만족한다.

---

## 1. API 정확한 시그니처

### 1-1. `adispatch_custom_event` (비동기)

```python
# langchain_core.callbacks.manager
async def adispatch_custom_event(
    name: str,
    data: Any,
    *,
    config: RunnableConfig | None = None,
) -> None
```

| 파라미터 | 타입 | 필수 | 설명 |
|---------|------|------|------|
| `name` | `str` | 필수 | 이벤트 식별자. 수신 측 분기 기준이 됨 |
| `data` | `Any` | 필수 | 자유 형식 페이로드. JSON 직렬화 가능 권장 (강제 아님) |
| `config` | `RunnableConfig \| None` | 선택 | **Python 3.10 이하 async 환경에서는 반드시 명시** |

### 1-2. `dispatch_custom_event` (동기)

```python
def dispatch_custom_event(
    name: str,
    data: Any,
    *,
    config: RunnableConfig | None = None,
) -> None
```

시그니처 동일. 동기 실행 컨텍스트에서 사용.

### 1-3. 임포트 경로

```python
from langchain_core.callbacks.manager import adispatch_custom_event
from langchain_core.callbacks.manager import dispatch_custom_event
```

공식 docs에서는 `langchain_core.callbacks.dispatch` 경로도 언급되나,
실제 구현체는 `langchain_core.callbacks.manager`에 위치.

---

## 2. `BaseCallbackHandler.on_custom_event` 핸들러

### 2-1. 동기 핸들러 (BaseCallbackHandler)

```python
# langchain_core.callbacks.base.BaseCallbackHandler
def on_custom_event(
    self,
    name: str,
    data: Any,
    *,
    run_id: UUID,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    **kwargs: Any,
) -> Any:
    """Override to define a handler for a custom event."""
```

| 파라미터 | 설명 |
|---------|------|
| `name` | dispatch 시 지정한 이벤트 이름 |
| `data` | dispatch 시 전달한 페이로드 |
| `run_id` | **부모 run의 UUID** (새 run ID 생성 안 함 — 부모 run에 귀속) |
| `tags` | 해당 run에 연결된 태그 목록 |
| `metadata` | 상속된 메타데이터 (그래프 설정 등) |

### 2-2. 비동기 핸들러 (AsyncCallbackHandler)

```python
# langchain_core.callbacks.base.AsyncCallbackHandler
async def on_custom_event(
    self,
    name: str,
    data: Any,
    *,
    run_id: UUID,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    **kwargs: Any,
) -> None:
    """Override to define a handler for custom events."""
```

`BaseCallbackHandler`의 동기 버전과 파라미터 동일.
차이: `async def`, 반환 타입 `None` (동기는 `Any`).

**권고**: data-copilot은 전체 async/await 패턴이므로 `AsyncCallbackHandler`를 상속하고
`async def on_custom_event`를 오버라이드해야 한다.

---

## 3. 내부 동작 원리

### 3-1. 구현 흐름 (adispatch_custom_event 기준)

```
adispatch_custom_event(name, data, config=config)
  │
  ├─ ensure_config(config)
  │    └─ contextvars에서 현재 run config 추출 (config=None이면 자동)
  │
  ├─ get_async_callback_manager_for_config(config)
  │    └─ config["callbacks"]에서 CallbackManager 추출
  │
  ├─ if callback_manager.parent_run_id is None:
  │    raise RuntimeError("... must be called from within an existing run")
  │
  └─ callback_manager.on_custom_event(
         name=name,
         data=data,
         run_id=callback_manager.parent_run_id  ← 새 UUID 생성 안 함
     )
```

핵심 설계 원칙: **custom event는 독립 run을 생성하지 않는다.**
`parent_run_id`를 재사용하여 부모 노드 실행에 메타데이터로 귀속된다.
이는 텔레메트리 트레이스 계층 구조를 깨뜨리지 않는다.

### 3-2. `parent_run_id` 요건

이 함수가 호출될 수 있는 컨텍스트:
- LangGraph 노드 함수 (그래프 실행 중)
- `@tool` 데코레이터 함수
- `RunnableLambda`
- `RunnableGenerator`

일반 Python async 함수에서 직접 호출하면 RuntimeError 발생.

---

## 4. LangGraph 노드에서의 config 전파

### 4-1. config 전파 메커니즘

LangGraph는 그래프 실행 시 각 노드 함수를 Runnable로 래핑한다.
노드 함수가 두 번째 인수로 `config: RunnableConfig`를 선언하면
LangGraph가 현재 실행 config를 **자동으로 주입**한다.

```python
from langchain_core.runnables import RunnableConfig
from langchain_core.callbacks.manager import adispatch_custom_event

async def my_node(state: AgentState, config: RunnableConfig) -> AgentState:
    # config는 LangGraph가 자동 주입 — callbacks 포함
    await adispatch_custom_event(
        "sql_generated",
        {"sql": generated_sql, "confidence": 0.92},
        config=config,  # Python 3.11+에서는 생략 가능, 명시 권장
    )
    return state
```

### 4-2. callback 등록 시점

```python
from langchain_core.callbacks import BaseCallbackHandler

class DomainTelemetryHandler(AsyncCallbackHandler):
    async def on_custom_event(self, name, data, *, run_id, tags, metadata, **kwargs):
        ...

handler = DomainTelemetryHandler()

# 방법 1: graph.compile() 시 등록
app = graph.compile(callbacks=[handler])

# 방법 2: invoke/astream_events 시점에 주입
result = await app.ainvoke(
    input_state,
    config={"callbacks": [handler]}
)
```

기존 메모리(`project_langgraph_production_patterns.md`)에서 확인된 패턴:
compile() 시 callbacks=[] 주입은 **모든 노드에 전파**된다.
per-request 핸들러가 필요하면 `ainvoke(config={"callbacks": [...]})` 방식 사용.

### 4-3. Python 버전별 config 명시 요건

| Python 버전 | config 파라미터 | 동작 |
|-------------|----------------|------|
| 3.11+ | 생략 가능 | contextvars에서 자동 추출 |
| 3.10 이하 | **반드시 명시** | asyncio 컨텍스트 전파 미지원 |

data-copilot은 Python 3.12이므로 생략 가능하나, **명시적 전달이 가독성·안전성 측면에서 권장**된다.

---

## 5. astream_events와의 연동

custom event는 `astream_events(version="v2")`를 통해서도 수신 가능하다.

```python
async for event in app.astream_events(input_state, version="v2"):
    if event["event"] == "on_custom_event":
        print(event["name"])   # dispatch 시 지정한 name
        print(event["data"])   # dispatch 시 전달한 data
```

**주의**: `version="v1"`에서는 custom event가 **노출되지 않는다**.
반드시 `version="v2"` 사용.

---

## 6. 도메인 특화 텔레메트리 적용 패턴 (data-copilot)

### 6-1. 이벤트 네임스페이스 설계

```python
# 이벤트 명칭 규약 예시
"intent.classified"       # 의도 분류 결과
"context.retrieved"       # ES/Qdrant 검색 결과
"sql.generated"           # SQL 생성 완료
"sql.validated"           # SQL 검증 결과
"sql.executed"            # SQL 실행 완료
"confidence.evaluated"    # 신뢰도 판정
"clarification.triggered" # 명확화 질문 발생
"recovery.triggered"      # 복구 플래너 진입
```

### 6-2. 노드 구현 패턴

```python
from langchain_core.runnables import RunnableConfig
from langchain_core.callbacks.manager import adispatch_custom_event

async def sql_generator_node(
    state: AgentState,
    config: RunnableConfig,
) -> AgentState:
    """SQL 생성 노드."""
    # ... SQL 생성 로직 ...

    await adispatch_custom_event(
        "sql.generated",
        {
            "sql": generated_sql,
            "tables_used": ["TB_LOAN_MASTER", "TB_BRANCH"],
            "generation_time_ms": elapsed_ms,
            "model": config.get("configurable", {}).get("model_name"),
        },
        config=config,
    )
    return state
```

### 6-3. AsyncCallbackHandler 구현

```python
from uuid import UUID
from typing import Any
from langchain_core.callbacks.base import AsyncCallbackHandler


class DataCopilotTelemetryHandler(AsyncCallbackHandler):
    """도메인 결정 텔레메트리 핸들러."""

    async def on_custom_event(
        self,
        name: str,
        data: Any,
        *,
        run_id: UUID,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        if name == "sql.generated":
            await self._log_sql_event(data, run_id)
        elif name == "confidence.evaluated":
            await self._log_confidence_event(data, run_id)
        elif name == "context.retrieved":
            await self._log_retrieval_event(data, run_id)

    async def _log_sql_event(self, data: dict, run_id: UUID) -> None:
        # 감사 로그 기록, 평가 DB 저장 등
        ...
```

### 6-4. 폐쇄망 고려사항

기존 메모리(`project_langgraph_production_patterns.md`)에서 확정된 사항:
- LangSmith/LangFuse 없는 폐쇄망에서는 `BaseCallbackHandler` + config 주입이 유일한 트레이싱 수단
- `adispatch_custom_event` 기반 커스텀 핸들러가 폐쇄망 텔레메트리의 **공식 권장 패턴**

---

## 7. 버전 요건 정리

| 항목 | 최소 버전 | 비고 |
|------|----------|------|
| `adispatch_custom_event` 도입 | langchain-core `0.2.15` | 2024년 7월 출시 |
| `on_custom_event` 핸들러 | langchain-core `0.2.15` | 동시 도입 |
| `astream_events` v2 custom event | langchain-core `0.2.x` | `version="v2"` 필수 |
| Python config 자동 추출 | Python 3.11+ | 3.10 이하는 config 명시 필수 |

현재 data-copilot 환경: Python 3.12, langchain-core 버전 pyproject.toml 기준 확인 필요.

---

## 8. 기각된 대안

| 대안 | 기각 이유 |
|------|----------|
| `on_llm_start` / `on_llm_end` 오버라이드로 SQL 추적 | LLM 호출과 도메인 결정(테이블 선택, 신뢰도)은 분리됨. LLM 이벤트에 끼워넣으면 구조 왜곡 |
| 노드 반환 state에 텔레메트리 필드 포함 | AgentState 오염. 텔레메트리는 비즈니스 로직과 직교(orthogonal)해야 함 |
| 별도 로깅 큐(asyncio.Queue) 사용 | config 전파 없이 구현 가능하나, LangGraph run 계층 구조와 연결 끊김. 디버그 어려움 |
| LangSmith 트레이싱 | 폐쇄망 배포 불가 |

---

## 9. 알려진 이슈 및 주의사항

1. **동기 블로킹 호출과 혼용 금지**: `adispatch_custom_event` 호출 전 동기 블로킹 I/O가 있으면 이벤트가 지연 배치 전송될 수 있다 (GitHub Issue #2574 확인). 모든 I/O는 `await` 사용.

2. **parent_run_id 없는 컨텍스트**: 단위 테스트에서 노드 함수를 직접 호출하면 `RuntimeError` 발생. 테스트 시 `RunnableLambda`로 래핑하거나 `MagicMock`으로 config 주입 필요.

3. **astream_events v1 미지원**: `version="v1"` (기본값이었던 구버전)에서는 custom event가 스트림에 나타나지 않는다.

4. **LangGraph Issue #5698**: `RunnableConfig`가 callable에 전달되지 않는 엣지케이스가 보고됨 (2025). 노드 함수 시그니처에 `config: RunnableConfig` 명시가 가장 안전한 해결책.

---

## 출처

- [LangChain 공식 문서: How to dispatch custom callback events](https://python.langchain.com/docs/how_to/callbacks_custom_events/)
- [adispatch_custom_event API Reference](https://python.langchain.com/api_reference/core/callbacks/langchain_core.callbacks.manager.adispatch_custom_event.html)
- [dispatch_custom_event API Reference](https://api.python.langchain.com/en/latest/core/callbacks/langchain_core.callbacks.manager.dispatch_custom_event.html)
- [BaseCallbackHandler API Reference](https://python.langchain.com/api_reference/core/callbacks/langchain_core.callbacks.base.BaseCallbackHandler.html)
- [langchain-core callbacks/base.py (GitHub 소스)](https://github.com/langchain-ai/langchain/blob/master/libs/core/langchain_core/callbacks/base.py)
- [langchain-core callbacks/manager.py (GitHub 소스)](https://github.com/langchain-ai/langchain/blob/master/libs/core/langchain_core/callbacks/manager.py)
- [LangGraph Issue #2574: adispatch_custom_event 타이밍 이슈](https://github.com/langchain-ai/langgraph/issues/2574)
- [LangGraph Issue #5698: RunnableConfig callable 전달 문제](https://github.com/langchain-ai/langgraph/issues/5698)
- [LangChain Changelog](https://changelog.langchain.com/)
