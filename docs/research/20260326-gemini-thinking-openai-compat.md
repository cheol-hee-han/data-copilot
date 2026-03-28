# Gemini Thinking/Reasoning Budget — OpenAI 호환 API 설정 가이드

**작성일**: 2026-03-26
**리서치 범위**: Gemini thinking 기능, OpenAI 호환 엔드포인트 파라미터, 지원 모델, 요금/Rate Limit

---

## 요약 (Executive Summary)

Gemini 2.5/3 시리즈는 내부 추론(thinking) 기능을 제공하며, Google의 OpenAI 호환 엔드포인트(`https://generativelanguage.googleapis.com/v1beta/openai/`)를 통해 `AsyncOpenAI` 클라이언트로 제어할 수 있다. 제어 방식은 크게 두 가지이다.

1. **`reasoning_effort`** 파라미터 (OpenAI 표준 방식, Gemini 3 계열 권장)
2. **`extra_body.google.thinking_config`** (세밀한 제어, Gemini 2.5 계열 토큰 수 지정 시 필수)

두 방식은 **동시에 사용 불가**. 하나만 선택해야 한다.

---

## 1. Thinking 기능 개요

### 작동 원리

Gemini 2.5/3 모델은 최종 응답을 생성하기 전에 내부적으로 추론 토큰(thinking tokens)을 소비한다. 이 과정은 응답에 포함되지 않지만 과금 대상이다 (output 토큰과 동일 단가). `include_thoughts: true`로 설정하면 요약된 사고 흔적(thought summary)을 응답에 포함할 수 있다.

### Thinking Budget

- **고정 예산(Fixed)**: 정수 토큰 값 지정 → 해당 값이 상한선
- **동적 예산(Dynamic)**: `-1` 지정 → 쿼리 복잡도에 따라 모델이 자동 조정 (대부분 모델의 기본값)
- **비활성화**: `0` 지정 (Gemini 2.5 Flash만 지원, Pro는 비활성화 불가)

---

## 2. 지원 모델 및 Thinking Budget 범위

| 모델 | Budget 범위 | 기본값 | 비활성화 가능 |
|------|-------------|--------|---------------|
| `gemini-2.5-pro` | 128 ~ 32,768 tokens | Dynamic (-1) | 불가 |
| `gemini-2.5-flash` | 0 ~ 24,576 tokens | Dynamic (-1) | 가능 (0) |
| `gemini-2.5-flash-lite` | 512 ~ 24,576 tokens | 비활성화 상태 | - |
| Gemini 3 시리즈 | N/A (레벨 방식) | High (dynamic) | 불가 |

**주의**: Gemini 2.5 Pro 및 Gemini 3 계열은 thinking을 끌 수 없다. `reasoning_effort: "none"`은 2.5 Flash에만 적용된다.

---

## 3. OpenAI 호환 API 설정 방법

### 3-1. 방법 A: `reasoning_effort` 파라미터 (권장 — Gemini 3 계열)

OpenAI SDK의 표준 파라미터. Gemini 3 모델에서 권장되며 2.5 모델에도 적용된다.

```python
from openai import AsyncOpenAI

client = AsyncOpenAI(
    api_key="GEMINI_API_KEY",
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
)

response = await client.chat.completions.create(
    model="gemini-2.5-flash",
    reasoning_effort="low",   # "none" | "low" | "medium" | "high"
    messages=[
        {"role": "user", "content": "연체율 산출식을 설명해줘"}
    ]
)
```

**레벨 매핑 (Gemini 2.5 기준)**:

| `reasoning_effort` 값 | 내부 thinking_budget | 특징 |
|-----------------------|----------------------|------|
| `"none"` | 0 (비활성화) | 2.5 Flash 전용. 최고속/최저비용 |
| `"low"` | ~1,024 tokens | 간단한 추론 |
| `"medium"` | ~8,192 tokens | 균형 |
| `"high"` | ~24,576 tokens | 복잡한 추론 |

---

### 3-2. 방법 B: `extra_body` + `thinking_config` (세밀 제어, Gemini 2.5 권장)

토큰 수를 직접 지정하거나 `include_thoughts`를 제어할 때 사용.

```python
# 1) thinking 활성화 + 예산 800 tokens + 사고 요약 포함
response = await client.chat.completions.create(
    model="gemini-2.5-flash",
    messages=[{"role": "user", "content": "NIM 계산식을 분석해줘"}],
    extra_body={
        "google": {
            "thinking_config": {
                "thinking_budget": 800,    # 0~24576 (Flash 기준)
                "include_thoughts": True   # 사고 흔적 응답에 포함
            }
        }
    }
)

# 2) thinking 비활성화 (2.5 Flash 전용)
response = await client.chat.completions.create(
    model="gemini-2.5-flash",
    messages=[{"role": "user", "content": "오늘 날짜가 뭐야?"}],
    extra_body={
        "google": {
            "thinking_config": {
                "include_thoughts": False
                # thinking_budget을 명시 안 하면 비활성화가 보장되지 않음
                # 명시적으로 0을 쓰거나 reasoning_effort="none" 사용 권장
            }
        }
    }
)

# 3) 동적 thinking (복잡도 자동 조정)
response = await client.chat.completions.create(
    model="gemini-2.5-flash",
    messages=[{"role": "user", "content": "복잡한 여신 심사 로직 분석"}],
    extra_body={
        "google": {
            "thinking_config": {
                "thinking_budget": -1,     # dynamic 모드
                "include_thoughts": True
            }
        }
    }
)
```

---

### 3-3. Gemini 3 계열에서의 `thinking_level` (extra_body 방식)

Gemini 3 모델은 토큰 수 대신 레벨 방식을 사용한다.

```python
response = await client.chat.completions.create(
    model="gemini-3-flash-preview",
    messages=[{"role": "user", "content": "분석해줘"}],
    extra_body={
        "google": {
            "thinking_config": {
                "thinking_level": "medium",  # "minimal" | "low" | "medium" | "high"
                "include_thoughts": True
            }
        }
    }
)
```

---

## 4. 핵심 제약사항 및 주의점

### 4-1. 파라미터 충돌 금지

`reasoning_effort`와 `thinking_level`/`thinking_budget`은 **동시에 사용 불가**. 동시 사용 시 API 에러 반환.

```python
# 잘못된 예 — 에러 발생
response = await client.chat.completions.create(
    model="gemini-2.5-flash",
    reasoning_effort="low",               # 이것과
    extra_body={
        "google": {
            "thinking_config": {
                "thinking_budget": 800    # 이것을 동시에 쓰면 안 됨
            }
        }
    }
)
```

### 4-2. 모델별 비활성화 가능 여부

- `gemini-2.5-flash`: `reasoning_effort="none"` 또는 `thinking_budget=0` 으로 비활성화 가능
- `gemini-2.5-pro`: thinking 비활성화 **불가**
- Gemini 3 계열: thinking 비활성화 **불가**

### 4-3. `thinking_budget=0` 동작 주의

`thinking_budget=0`을 설정해도 **모델이 내부적으로 약간의 추론을 수행할 수 있다**. 완전한 비활성화가 중요하다면 `reasoning_effort="none"`이 더 명시적이다.

### 4-4. `include_thoughts` 응답 파싱

`include_thoughts: true`를 설정하면 응답 메시지 content 배열에 `thought: true` 플래그가 붙은 별도 파트가 포함된다. 일반 텍스트 파싱 시 이 파트를 걸러야 한다.

```python
for choice in response.choices:
    content = choice.message.content
    # content가 list인 경우 (include_thoughts=True)
    # 각 파트에 thought 플래그가 있을 수 있음
    # 실제 응답만 사용하려면 thought=False 파트만 추출
```

---

## 5. Rate Limits (2025년 기준)

### Gemini Developer API (Google AI Studio)

| 모델 | 무료 Tier | 유료 Tier 1 |
|------|----------|-------------|
| `gemini-2.5-flash` | 10 RPM, 250K TPM, 250 RPD | ~150-300 RPM, 비율 증가 |
| `gemini-2.5-pro` | 5 RPM, 250K TPM, 100 RPD | ~150 RPM |

**Thinking 전용 Rate Limit은 별도 없음.** thinking_budget 크기와 무관하게 동일한 RPM/TPM 제한 적용. 단, thinking 토큰이 TPM에 카운트되므로 높은 budget 설정 시 TPM 한도에 더 빨리 도달.

### 2025-12-07 이후 변경 사항

Google이 Free Tier 및 Tier 1 쿼터를 일부 하향 조정했다. 최신 한도는 [Google AI Studio Rate Limit 페이지](https://aistudio.google.com/rate-limit)에서 직접 확인 필요 (인증 필요).

---

## 6. 과금 구조

| 모델 | Input | Output (thinking 포함) |
|------|-------|------------------------|
| `gemini-2.5-flash` | $0.30/1M tokens | $2.50/1M tokens |
| `gemini-2.5-pro` | $1.25/1M tokens (≤200K ctx) | $10.00/1M tokens (≤200K ctx) |
| `gemini-2.5-pro` | $2.50/1M tokens (>200K ctx) | $15.00/1M tokens (>200K ctx) |

**Thinking 토큰은 output 토큰과 동일 단가로 과금.** 별도 surcharge 없음. 실제 thinking 토큰 수는 응답의 `usage.completion_tokens_details` 또는 `thoughtsTokenCount` 필드에서 확인 가능.

---

## 7. 프로젝트 적용 권고안

### 권고 구성

본 프로젝트(`data-copilot`)에서 Gemini를 `AsyncOpenAI` 클라이언트로 호출할 때의 권고 설정:

```python
from openai import AsyncOpenAI
from typing import Literal

ThinkingMode = Literal["none", "low", "medium", "high"]

async def call_gemini(
    prompt: str,
    model: str = "gemini-2.5-flash",
    thinking_mode: ThinkingMode = "low",
) -> str:
    """
    Gemini OpenAI 호환 엔드포인트 호출.

    Args:
        prompt: 사용자 질의
        model: Gemini 모델 ID
        thinking_mode: thinking 강도. "none"은 gemini-2.5-flash 전용.
    """
    client = AsyncOpenAI(
        api_key=settings.gemini_api_key,
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
    )

    response = await client.chat.completions.create(
        model=model,
        reasoning_effort=thinking_mode,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.choices[0].message.content
```

### 태스크별 권장 설정

| 태스크 | 권장 모델 | thinking_mode | 이유 |
|--------|-----------|---------------|------|
| SQL 생성 (복잡한 조인/집계) | `gemini-2.5-flash` | `"medium"` | 정확도-속도 균형 |
| SQL 생성 (단순 필터) | `gemini-2.5-flash` | `"none"` | 비용 최소화 |
| 금융 지표 산출식 추론 | `gemini-2.5-pro` | `"high"` | 최고 정확도 필요 |
| 의도 분류 / 명확화 질문 | `gemini-2.5-flash` | `"none"` | 빠른 응답 필요 |
| 결과 포매팅 / 설명 생성 | `gemini-2.5-flash` | `"low"` | 약간의 추론으로 충분 |

### 기각된 대안

- **`extra_body` 방식 (Gemini 2.5)**: 토큰 수 직접 지정이 필요한 경우 유효하지만, `reasoning_effort` 레벨 매핑이 이미 충분하고 코드가 단순하므로 일반 케이스에서는 기각.
- **Gemini Native SDK (`google-generativeai`)**: `ThinkingConfig` 객체 방식이 더 명시적이나, 프로젝트가 이미 `AsyncOpenAI` 클라이언트 패턴을 사용 중이므로 기각.

---

## 출처

- [Gemini Thinking — Google AI for Developers](https://ai.google.dev/gemini-api/docs/thinking)
- [OpenAI Compatibility — Gemini API](https://ai.google.dev/gemini-api/docs/openai)
- [Gemini 2.5 Flash Thinking Tokens using OpenAI API — Google AI Forum](https://discuss.ai.google.dev/t/gemini-2-5-flash-thinking-tokens-using-openai-api/79985)
- [Gemini Developer API Pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [Rate Limits — Gemini API](https://ai.google.dev/gemini-api/docs/rate-limits)
- [Thinking — Vertex AI](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/thinking)
- [OpenAI Compatibility — Vertex AI](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/start/openai)
- [How to disable thinking — Google Gemini Forum](https://discuss.ai.google.dev/t/how-to-disable-thinking-using-gemini-2-5-flash-thinkingbudget-0-not-working/80149)
