# 무료 LLM API 제공업체 리서치 (2026년 3월 기준)

**작성일**: 2026-03-26
**목적**: NL-to-SQL 파이프라인 테스트용 OpenAI 호환 무료 LLM API 선정
**요구조건**: 무료(신용카드 불필요), OpenAI 호환, 30+ RPM, 70B+ 급 모델, 시스템 프롬프트 + JSON 출력 지원

---

## 요약 권고안

**1순위 (즉시 사용 추천)**: Groq — Llama 3.3 70B, 30 RPM, 무료, OpenAI 완전 호환
**2순위 (백업)**: Cerebras — Llama 3.3 70B / Qwen3 235B, 30 RPM, 1M tokens/day
**3순위 (대용량 토큰 필요 시)**: Google Gemini AI Studio — Gemini 2.5 Flash, 10 RPM (30 RPM 요구 미충족이나 TPM 최고)
**기각**: OpenRouter (무료 모델 200 req/day 상한), SambaNova 무료 70B (20 RPM 미달)

---

## 1. 제공업체별 상세 분석

### 1.1 Groq

| 항목 | 값 |
|------|-----|
| 신용카드 불필요 | YES |
| OpenAI 호환 | YES (base_url 교체만으로 적용) |
| 주요 모델 | llama-3.3-70b-versatile, llama-3.1-8b-instant, qwen/qwen3-32b |
| RPM (70B) | **30 RPM** |
| TPM (70B) | 12,000 TPM |
| RPD (70B) | 1,000 req/day |
| JSON 모드 | YES |
| 시스템 프롬프트 | YES |
| 추론 속도 | ~500-2,600 tokens/sec (가장 빠른 무료 추론) |

**특이사항**:
- moonshotai/kimi-k2-instruct: 60 RPM (더 관대)
- qwen/qwen3-32b: 60 RPM, 6K TPM, 1K RPD
- llama-3.1-8b-instant: 30 RPM, 14.4K RPD (빠른 prototyping용)
- 속도가 절대적으로 빠르므로 12K TPM 한계도 실제 체감이 낮음
- API 키: console.groq.com 에서 무료 발급

**제한사항**:
- 70B 모델 TPM이 12K로 낮음 — 긴 프롬프트(3K+ tokens) 반복 시 병목 가능
- RPD 1,000건 — 집중 테스트 시 당일 소진 가능

**코드 예시**:
```python
from openai import AsyncOpenAI

client = AsyncOpenAI(
    api_key="gsk_...",
    base_url="https://api.groq.com/openai/v1"
)
response = await client.chat.completions.create(
    model="llama-3.3-70b-versatile",
    messages=[{"role": "user", "content": "..."}],
    response_format={"type": "json_object"}
)
```

---

### 1.2 Cerebras

| 항목 | 값 |
|------|-----|
| 신용카드 불필요 | YES |
| OpenAI 호환 | YES |
| 주요 모델 | llama-3.3-70b, qwen-3-235b-a22b, gpt-oss-120b |
| RPM | **30 RPM** |
| TPM | 60,000-64,000 TPM |
| RPD | 14,400 req/day |
| 일일 토큰 | 1,000,000 tokens/day |
| JSON 모드 | YES |
| 시스템 프롬프트 | YES |

**특이사항**:
- TPM 60K는 Groq 12K 대비 **5배** — 긴 프롬프트 반복 테스트에 유리
- RPD 14,400건은 Groq 1K 대비 **14배** — 대규모 배치 평가에 적합
- qwen-3-235b-a22b: MoE 235B 파라미터 (고품질 추론)
- gpt-oss-120b: 임시 rate limit 축소 중 (2026-03 기준)
- API 키: cloud.cerebras.ai 에서 무료 발급

**제한사항**:
- 속도는 Groq 대비 느림
- zai-glm-4.7, gpt-oss-120b 일부 모델 임시 rate limit 축소

---

### 1.3 Google Gemini AI Studio

| 항목 | 값 |
|------|-----|
| 신용카드 불필요 | YES |
| OpenAI 호환 | YES (openai 라이브러리 + base_url) |
| 주요 모델 | gemini-2.5-pro, gemini-2.5-flash, gemini-2.5-flash-lite |
| RPM (Flash) | **10 RPM** (요구조건 30 미충족) |
| RPM (Flash-Lite) | 15 RPM (요구조건 미충족) |
| RPM (Pro) | 5 RPM |
| TPM | 250,000 TPM (모든 모델 공통) |
| RPD (Flash) | 250 req/day |
| JSON 모드 | YES (response_mime_type) |
| 시스템 프롬프트 | YES |
| 컨텍스트 윈도우 | 1,000,000 tokens |

**중요 이슈**:
- 2025-12-07 Google이 무료 tier 쿼터를 **50-80% 삭감** (사기/남용 이유)
- RPM 30 요구조건을 충족하지 못함 — 병렬 처리 없이 단순 순차 테스트에만 적합
- TPM 250K는 모든 제공업체 중 최고 — 대형 프롬프트 단발성 테스트에 유리
- 프롬프트/응답이 Google 제품 개선에 사용됨 (무료 tier 약관)

**코드 예시**:
```python
from openai import AsyncOpenAI

client = AsyncOpenAI(
    api_key="AIza...",
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
)
```

---

### 1.4 OpenRouter

| 항목 | 값 |
|------|-----|
| 신용카드 불필요 | YES (기본) |
| OpenAI 호환 | YES |
| 무료 모델 수 | 27개 (2026-03 기준) |
| RPM | **20 RPM** (요구조건 30 미충족) |
| RPD (기본) | **50 req/day** (매우 낮음) |
| RPD ($10 충전 후) | 1,000 req/day |
| 주요 무료 모델 | Qwen3 Coder 480B, NVIDIA Nemotron 120B, Llama 3.3 70B, DeepSeek R1, gpt-oss-120B |
| JSON 모드 | YES |
| 시스템 프롬프트 | YES |

**기각 이유**:
- 무료 신용카드 없이는 50 req/day — 파이프라인 테스트에 절대 부족
- RPM 20으로 30 요구조건 미달
- $10 충전 필요 시 "무료" 조건 위배

**단, 다음 경우 재고 가능**:
- 소량(50건/day 이내) 모델 품질 비교 테스트
- Qwen3 Coder 480B 등 대형 모델 접근이 필요한 경우

---

### 1.5 SambaNova

| 항목 | 값 |
|------|-----|
| 신용카드 불필요 | YES (Free Tier) |
| OpenAI 호환 | YES |
| 주요 모델 | Llama 3.1 70B/405B, Llama 3.3 70B |
| RPM (70B) | **20 RPM** (요구조건 미충족) |
| RPM (8B) | 30 RPM |
| TPM | 200,000 tokens/day |
| JSON 모드 | 제한적 |
| 시스템 프롬프트 | YES |

**기각 이유**:
- 70B 모델 RPM이 20으로 30 요구조건 미달
- Developer Tier(카드 등록 시) 전환해야 더 높은 rate limit
- Community 이슈: Developer Tier도 20 RPM에 머무는 사례 다수 보고

---

### 1.6 Cloudflare Workers AI

| 항목 | 값 |
|------|-----|
| 신용카드 불필요 | YES (계정 필요) |
| OpenAI 호환 | **부분적** (자체 포맷, openai SDK 직접 호환 아님) |
| 주요 모델 | Llama 3.2 11B, Mistral 7B, Qwen 14B |
| RPM | 150-300 RPM (GA 기준) |
| 무료 일일 할당 | 10,000 Neurons/day |
| JSON 모드 | YES |
| 시스템 프롬프트 | YES |

**기각 이유**:
- 최대 모델 크기: Qwen 14B (2026-03 기준 공식 stable) — 70B+ 요구 미충족
- OpenAI SDK 직접 호환이 아님 (Cloudflare 자체 SDK 또는 REST 직접 호출 필요)
- 10K Neurons/day 소진 속도가 불투명 (모델별 Neurons 소비량 상이)

**단, 다음 경우 재고 가능**:
- Cloudflare에 이미 배포된 서비스와 통합 시
- 2026-04 이후 Kimi K2.5 (MoE 대형 모델) GA 예정

---

### 1.7 Together AI

| 항목 | 값 |
|------|-----|
| 신용카드 불필요 | 조건부 (가입 시 $1~5 크레딧, 소진 후 요금 부과) |
| OpenAI 호환 | YES |
| 주요 모델 | Llama 4, DeepSeek-V3, Qwen, Mixtral |
| 무료 신규 크레딧 | $1 (소진 후 유료) |
| JSON 모드 | YES |
| 시스템 프롬프트 | YES |

**기각 이유**:
- 지속적 무료 tier 없음 — 크레딧 소진 후 유료 전환 필수
- "무료, 신용카드 불필요" 요구조건 위배

---

### 1.8 NVIDIA NIM

| 항목 | 값 |
|------|-----|
| 신용카드 불필요 | YES (signup 시 1,000 credits) |
| OpenAI 호환 | YES |
| 주요 모델 | Llama 3.3 70B, DeepSeek R1/V3, Kimi K2.5 |
| RPM | 40 RPM |
| 무료 크레딧 | 1,000 credits (추가 요청 시 최대 5,000) |
| JSON 모드 | YES |
| 시스템 프롬프트 | YES |

**평가**:
- RPM 40으로 요구조건 충족
- 단, 크레딧 소진 후 유료 — 지속적 무료 tier 아님
- 장기 테스트보다 단기 품질 검증에 적합

---

## 2. 비교 매트릭스

| 제공업체 | 모델 (최대) | RPM | RPD | TPM | 신용카드 | OpenAI 호환 | 지속 무료 | 종합 |
|---------|-----------|-----|-----|-----|---------|------------|---------|------|
| **Groq** | Llama 3.3 70B | **30** | 1,000 | 12K | 불필요 | 완전 | YES | **최우선** |
| **Cerebras** | Qwen3 235B | **30** | 14,400 | 60K | 불필요 | 완전 | YES | **2순위** |
| Gemini AI Studio | Gemini 2.5 Flash | 10 | 250 | 250K | 불필요 | 완전 | YES | 3순위 (RPM 미달) |
| NVIDIA NIM | Llama 3.3 70B | 40 | 무제한 | 높음 | 불필요 | 완전 | NO (크레딧) | 단기 검증용 |
| OpenRouter | Qwen3 480B 등 | 20 | 50 | 높음 | 불필요 | 완전 | YES | 기각 (RPD 너무 낮음) |
| SambaNova | Llama 3.1 70B | 20 | 높음 | 200K | 불필요 | 완전 | YES | 기각 (RPM 미달) |
| Cloudflare Workers AI | Qwen 14B | 150+ | 10K neurons | 높음 | 불필요 | 부분 | YES | 기각 (모델 크기, 호환성) |
| Together AI | Llama 4 | 높음 | 높음 | 높음 | 필요 | 완전 | NO | 기각 (유료) |

---

## 3. 권고 전략: 이중화 구성

NL-to-SQL 파이프라인 테스트 목적에 최적화된 구성:

```
1차: Groq (llama-3.3-70b-versatile)
   - 빠른 추론 속도 → 개발 중 빠른 피드백
   - 30 RPM, 1K RPD
   - RPD 1K 소진 시 → 2차로 폴백

2차: Cerebras (llama-3.3-70b or qwen3-235b)
   - 14.4K RPD → 배치 평가에 충분
   - TPM 60K → 긴 프롬프트 반복 테스트 가능
```

**환경변수 설정 예시**:
```python
LLM_PRIMARY_BASE_URL = "https://api.groq.com/openai/v1"
LLM_PRIMARY_MODEL    = "llama-3.3-70b-versatile"
LLM_FALLBACK_BASE_URL = "https://api.cerebras.ai/v1"
LLM_FALLBACK_MODEL    = "llama-3.3-70b"
```

---

## 4. 한국어 NL-to-SQL 특화 고려사항

| 제공업체 | 한국어 성능 | 비고 |
|---------|-----------|------|
| Groq (Llama 3.3 70B) | 양호 | Meta 학습 데이터에 한국어 포함, 금융 용어 추론 가능 |
| Cerebras (Qwen3 235B) | 우수 | Qwen 시리즈는 아시아권 언어 강점, 235B MoE |
| Gemini 2.5 Flash | 우수 | Google 다국어 학습 강점, 한국어 처리 안정적 |

**SQL 생성 품질 순위 (추정)**:
1. Cerebras Qwen3 235B — 파라미터 최대, 추론 품질 최고
2. Groq Llama 3.3 70B — SQL 생성 검증된 실적
3. Gemini 2.5 Flash — 속도/품질 균형, RPM 한계

---

## 5. 구현 시 주의사항

### 5.1 JSON 출력 모드 활성화

```python
# Groq / Cerebras 공통
response = await client.chat.completions.create(
    model="llama-3.3-70b-versatile",
    response_format={"type": "json_object"},  # 필수
    messages=[
        {"role": "system", "content": "..."},
        {"role": "user", "content": "..."}
    ]
)
```

### 5.2 Rate Limit 핸들링 (지수 백오프)

```python
import asyncio
from openai import RateLimitError

async def call_with_retry(client, **kwargs):
    for attempt in range(5):
        try:
            return await client.chat.completions.create(**kwargs)
        except RateLimitError:
            await asyncio.sleep(2 ** attempt)
    raise RuntimeError("Rate limit 초과")
```

### 5.3 Groq TPM 병목 완화

- 시스템 프롬프트 토큰 최소화 (12K TPM 내 여유 확보)
- Few-shot 예시를 2개 이내로 제한
- SQL 생성 단계에서 max_tokens=512 제한 권장

---

## 6. 출처

| 제공업체 | 공식 문서 |
|---------|---------|
| Groq | https://console.groq.com/docs/rate-limits |
| Cerebras | https://inference-docs.cerebras.ai/support/rate-limits |
| Google Gemini | https://ai.google.dev/gemini-api/docs/rate-limits |
| OpenRouter | https://openrouter.ai/docs/api/reference/limits |
| SambaNova | https://docs.sambanova.ai/docs/en/models/rate-limits |
| Cloudflare Workers AI | https://developers.cloudflare.com/workers-ai/platform/limits/ |
| NVIDIA NIM | https://developer.nvidia.com/nim |

**참조 리서치 자료**:
- [Every Free AI API in 2026: The Complete Guide](https://awesomeagents.ai/tools/free-ai-inference-providers-2026/)
- [OpenRouter Free Models: All 27 Listed (Mar 2026)](https://costgoat.com/pricing/openrouter-free-models)
- [Gemini API Free Tier Rate Limits Guide 2026](https://www.aifreeapi.com/en/posts/gemini-api-free-tier-rate-limits)
- [Groq API Free Tier Rate Limits Analysis](https://www.zilgist.com/2026/02/groq-api-free-tier-rate-limits-best.html)
- [Cerebras Free 1M tokens/day Tier](https://adam.holter.com/cerebras-opens-a-free-1m-tokens-per-day-inference-tier-and-claims-20x-faster-than-nvidia-real-benchmarks-model-limits-and-why-ui2-matters/)
