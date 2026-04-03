# Thinking 모드 노드별 제어 — 설계서

> **작성일**: 2026-03-26
> **관련 문서**: `docs/todo/qwen-closed-network-adaptation.md` 작업 3
> **대상 모델**: Qwen 3.5 397B-A17B (thinking 모드 기본 활성화)
> **목적**: LLM 호출 노드별로 thinking on/off를 제어하여 비용/지연 최적화

---

## 1. 배경

### 1.1 Qwen 3.5 Thinking 모드란

Qwen 3.5는 응답 생성 전 `<think>...</think>` 블록에서 내부 추론을 수행한다.
이 모드는 기본 활성화되어 있으며, 비활성화하면 추론 없이 바로 답변을 생성한다.

```
# Thinking ON (기본)
<think>
사용자가 의도 분류를 요청했다. 질의 내용을 분석하면...
DATA_EXTRACTION에 해당한다. 확신도는 HIGH이다.
</think>
{"category": "DATA_EXTRACTION", "confidence": "HIGH", "reason": "..."}

# Thinking OFF
{"category": "DATA_EXTRACTION", "confidence": "HIGH", "reason": "..."}
```

### 1.2 트레이드오프

| | Thinking ON | Thinking OFF |
|---|---|---|
| 정확도 | 높음 (특히 복잡 추론) | 낮음 (단순 태스크에선 차이 미미) |
| 응답 지연 | 길음 (think 토큰 생성 시간) | 짧음 |
| 토큰 비용 | 높음 (think 토큰도 과금) | 낮음 |
| 파싱 위험 | `<think>` 태그 내 JSON 오인 가능 | 없음 |

### 1.3 현재 파이프라인의 LLM 호출 수

1개 질의 처리에 **최소 5회, 최대 13회** LLM 호출이 발생한다.
전부 thinking ON이면 불필요한 토큰 소비가 크므로 노드별 제어가 필요하다.

---

## 2. 설계 원칙

### 2.1 클라이언트 레이어에서 투명하게 처리

- **호출 사이트 코드 변경 최소화** — 기존 `client.messages.create()` 시그니처 유지
- thinking 제어는 `UnifiedLLMClient` 내부에서 처리
- Anthropic provider에서는 thinking 파라미터를 무시 (Claude는 thinking 모드 없음)

### 2.2 설정 기반 제어 (코드 변경 없이 전환)

- `config.py`에서 전역/노드별 thinking 설정 관리
- `.env` 파일 수정만으로 노드별 thinking on/off 조절 가능

### 2.3 응답에서 `<think>` 태그 안전 제거

- thinking ON이든 OFF이든, 응답에서 `<think>...</think>` 태그는 항상 제거
- 파싱 로직이 think 텍스트 내부의 JSON을 오인하는 문제 방지

---

## 3. 상세 설계

### 3.1 설정 모델 (`src/config.py`)

```python
# ── Qwen Thinking 모드 제어 ──
# openai_compatible provider + Qwen 모델에서만 적용
# anthropic provider에서는 무시됨
llm_thinking_default: bool = True    # 전역 기본값

# 노드별 오버라이드 (설정하지 않으면 llm_thinking_default 사용)
# 형식: 쉼표 구분 "node_name:bool" 목록
# 예: "intent_classifier:false,clarifier:false,viz_judgment:false"
llm_thinking_overrides: str = ""
```

**노드명 규약**: 기존 `set_current_node()` / `get_current_node()`에서 사용하는
tracker용 노드명과 동일한 값을 사용한다.

파싱 후 내부 캐시:

```python
@cached_property
def thinking_config(self) -> dict[str, bool]:
    """노드별 thinking 설정을 파싱한다."""
    config: dict[str, bool] = {}
    for entry in self.llm_thinking_overrides.split(","):
        entry = entry.strip()
        if ":" not in entry:
            continue
        node, val = entry.split(":", 1)
        config[node.strip()] = val.strip().lower() in ("true", "1", "yes")
    return config

def is_thinking_enabled(self, node_name: str = "") -> bool:
    """주어진 노드에서 thinking이 활성화되어 있는지 반환한다."""
    if node_name and node_name in self.thinking_config:
        return self.thinking_config[node_name]
    return self.llm_thinking_default
```

### 3.2 클라이언트 수정 (`src/utils/llm/client.py`)

#### 3.2.1 `<think>` 태그 제거 유틸리티

```python
import re

_THINK_RE = re.compile(r"<think>[\s\S]*?</think>\s*", re.DOTALL)

def _strip_thinking_tags(text: str) -> str:
    """Qwen thinking 태그를 제거한다.

    <think>...</think> 블록을 안전하게 제거하여
    파싱 로직이 think 내부의 JSON/SQL을 오인하지 않도록 한다.
    """
    return _THINK_RE.sub("", text).strip()
```

#### 3.2.2 `OpenAICompatibleMessages.create()` 수정

```python
async def create(self, *, model, max_tokens, system=None, messages, timeout=None, **kwargs):
    openai_messages = []
    if system:
        openai_messages.append({"role": "system", "content": system})
    openai_messages.extend(messages)

    call_kwargs = {"model": model, "max_tokens": max_tokens, "messages": openai_messages}
    if timeout is not None:
        call_kwargs["timeout"] = timeout

    # ── Thinking 모드 제어 ──
    # kwargs에서 명시적으로 전달되면 그 값을 사용,
    # 아니면 현재 노드의 설정을 참조
    thinking = kwargs.pop("thinking", None)
    if thinking is None:
        from src.config import settings
        node_name = get_current_node()
        thinking = settings.is_thinking_enabled(node_name)

    if not thinking:
        call_kwargs.setdefault("extra_body", {})
        call_kwargs["extra_body"]["chat_template_kwargs"] = {
            "enable_thinking": False,
        }

    response = await self._client.chat.completions.create(**call_kwargs)
    text = response.choices[0].message.content or ""

    # ── <think> 태그 항상 제거 (thinking ON이어도 안전 처리) ──
    text = _strip_thinking_tags(text)

    return LLMResponse(content=[TextBlock(text=text)], ...)
```

#### 3.2.3 `AnthropicMessages.create()` — 변경 없음

Claude는 thinking 모드가 없으므로 `thinking` kwargs를 조용히 무시한다.

```python
async def create(self, *, model, max_tokens, system=None, messages, timeout=None, **kwargs):
    # thinking 파라미터 무시 (Claude에서는 불필요)
    kwargs.pop("thinking", None)
    # ... 기존 로직 그대로
```

### 3.3 `llm_call_with_parse_retry()` — 변경 없음

이 함수는 내부에서 `client.messages.create(**kwargs)`를 호출하므로,
호출 사이트에서 `thinking=False`를 전달하면 자동으로 전파된다.
변경 불필요.

### 3.4 호출 사이트 — 변경 없음

모든 호출 사이트가 `get_llm_client()` → `client.messages.create()`를 사용하고,
thinking 제어는 `config.py`의 노드별 설정 + `get_current_node()`로 자동 결정되므로
**호출 사이트 코드 변경이 필요 없다.**

특정 호출에서 명시적으로 제어하고 싶으면 kwargs로 전달 가능:

```python
# 예: 명시적으로 thinking OFF
response = await client.messages.create(
    model=settings.llm_model,
    max_tokens=200,
    system=prompt,
    messages=[...],
    thinking=False,  # 명시적 오버라이드
)
```

---

## 4. 노드별 권장 설정

### 4.1 기본값: `llm_thinking_default = true`

복잡 추론이 필요한 노드가 더 많으므로 기본값은 ON으로 한다.
단순 태스크 노드만 명시적으로 OFF한다.

### 4.2 권장 오버라이드 (폐쇄망 초기 설정)

```env
LLM_THINKING_OVERRIDES=history_resolver:false,intent_classifier:false,clarifier:false,analyzer:false,viz_judgment:false,formatter:false
```

상세 근거:

| 노드 (node_name) | Thinking | 근거 |
|---|---|---|
| `history_resolver` | **OFF** | 3-way 분류 (CONTINUE/NEW/UNSURE), 추론 불필요 |
| `intent_classifier` | **OFF** | 6-way 분류, 단순 패턴 매칭 수준 |
| `query_normalizer` | ON | 8슬롯 추출에 단계적 추론 필요 |
| `clarifier` | **OFF** | 자유 텍스트 질문 생성, 추론보다 생성 태스크 |
| `planner` | On | 가설 수립에 전략적 추론 필요 |
| `context_explorer` | On | 메타 데이터 의미 해석, 3측면 추론 |
| `table_comparison` | On | 유사 테이블 비교에 대조 추론 필요 |
| `sql_generator` | On | **핵심** — thinking이 SQL 정확도에 직결 |
| `sql_validator` | On | 의미적 검증에 추론 필요 (Layer 2b) |
| `recovery_planner` | On | 실패 원인 분석에 추론 필요 |
| `analyzer` | **OFF** | 결과 요약/통계, 추론보다 정리 태스크 |
| `viz_judgment` | **OFF** | 차트 유형 분류, 단순 판정 |
| `formatter` | **OFF** | 텍스트 포맷팅, 추론 불필요 |

**결과**: 13개 호출 중 6개 OFF → thinking 토큰 ~46% 절감 (단순 태스크 비중)

### 4.3 운영 조정 가이드

폐쇄망 테스트 후 다음 기준으로 조정:

- **정확도 하락 감지**: 특정 노드에서 파싱 실패/잘못된 분류가 증가하면 thinking ON으로 전환
- **지연 과다**: SQL Generator의 thinking이 너무 길면 `max_tokens` 조정 (think 토큰 포함)
- **하위 모델 사용 시**: 35B/27B에서는 thinking ON이 더 중요 — 전역 기본값 유지

---

## 5. vLLM 서빙 설정 참고

Qwen 3.5 thinking 제어를 위한 vLLM 서빙 옵션:

```bash
# vLLM 서빙 시
python -m vllm.entrypoints.openai.api_server \
  --model Qwen/Qwen3.5-397B-A17B \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_coder \
  --tensor-parallel-size 8
```

클라이언트에서 thinking 비활성화:

```python
# 방법 1: extra_body (본 설계에서 채택)
extra_body={"chat_template_kwargs": {"enable_thinking": False}}

# 방법 2: 시스템 프롬프트 앞에 /no_think 삽입 (대안)
system = "/no_think\n" + system
```

본 설계는 방법 1을 채택한다. 이유:
- API 레벨에서 명시적 제어 → 프롬프트 오염 없음
- vLLM, SGLang 등 주요 서빙 프레임워크에서 `extra_body` 지원
- 프롬프트에 `/no_think`를 삽입하면 프롬프트 해싱/캐싱에 영향

---

## 6. 구현 순서

| 순서 | 작업 | 영향 범위 |
|------|------|----------|
| 1 | `config.py`에 `llm_thinking_default`, `llm_thinking_overrides`, `is_thinking_enabled()` 추가 | config.py |
| 2 | `client.py`에 `_strip_thinking_tags()` 유틸리티 추가 | client.py |
| 3 | `OpenAICompatibleMessages.create()`에 thinking 제어 + `<think>` 제거 적용 | client.py |
| 4 | `AnthropicMessages.create()`에서 `thinking` kwargs 무시 처리 | client.py |
| 5 | 유닛 테스트 — `_strip_thinking_tags()`, `is_thinking_enabled()` | tests/ |

**호출 사이트 코드 변경: 0곳** — 클라이언트 레이어에서 투명하게 처리

---

## 7. 체크리스트

- [ ] `config.py`: `llm_thinking_default`, `llm_thinking_overrides` 설정 추가
- [ ] `config.py`: `is_thinking_enabled(node_name)` 메서드 추가
- [ ] `client.py`: `_strip_thinking_tags()` 유틸리티 추가
- [ ] `client.py`: `OpenAICompatibleMessages.create()`에 thinking 제어 로직 추가
- [ ] `client.py`: `OpenAICompatibleMessages.create()`에서 `<think>` 태그 항상 제거
- [ ] `client.py`: `AnthropicMessages.create()`에서 `thinking` kwargs 무시
- [ ] 테스트: `_strip_thinking_tags()` 기본/중첩/미존재 케이스
- [ ] 테스트: `is_thinking_enabled()` 기본값/오버라이드/미설정 케이스
- [ ] `.env.example`: thinking 설정 예시 추가
