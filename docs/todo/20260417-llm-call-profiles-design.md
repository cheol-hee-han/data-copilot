# LLM 호출 단위 프로파일 통합 설계서

> **작성일**: 2026-04-17
> **선행 문서**: `docs/todo/20260326-thinking-mode-control-design.md`
> **목적**: thinking·temperature·max_tokens·timeout을 LLM 호출 단위별로 중앙 관리하여,
>           모델 교체(Claude → Qwen3.5 → GPT OSS)·튜닝 시 **이 파일 하나만 수정**하면 되도록 통합

---

## 1. 현황 및 문제

### 1.1 LLM 속성이 3곳에 분산

| 속성 | 현재 관리 위치 | 문제 |
|------|-------------|------|
| thinking | `src/agents/nodes/thinking_modes.py` | 노드별 중앙 관리 (양호) |
| max_tokens | `src/config.py` 글로벌 기본값 + 각 호출지점 하드코딩 | 모델 교체 시 13개 호출지점 수정 필요 |
| timeout | `src/config.py` 글로벌 기본값 + 각 호출지점 하드코딩 | 동일 |
| temperature | 각 호출지점에서 개별 전달 (대부분 미지정 → 서버 기본값) | 노드별 최적값 관리 불가 |

### 1.2 호출지점별 현재 파라미터 (전수 조사)

| LLMNode | 파일 | max_tokens | timeout | temperature | thinking |
|---------|------|-----------|---------|-------------|---------|
| INTENT_CLASSIFIER | `services/intent_classifier.py:166` | `settings.llm_default_max_tokens` | `settings.llm_default_timeout` | 미지정 | node lookup |
| NORMALIZE_QUERY_PHASE1 | `services/query_normalizer.py:622` | `settings.normalization_max_tokens` | `settings.llm_long_timeout` | 미지정 | node lookup |
| NORMALIZE_QUERY_PHASE2 | `services/query_normalizer.py:668` | `settings.normalization_max_tokens` | `settings.llm_long_timeout` | 미지정 | node lookup |
| CONTEXT_INTERPRETER (batch) | `agents/nodes/reason/context_interpreter.py:484` | `2048` 하드코딩 | `settings.llm_long_timeout` | 미지정 | node lookup |
| CONTEXT_INTERPRETER (step) | `agents/nodes/reason/context_interpreter.py:581` | `1024` 하드코딩 | `settings.llm_long_timeout` | 미지정 | node lookup |
| SQL_GENERATOR | `agents/nodes/reason/sql_generator.py:583` | `settings.llm_format_max_tokens` | `settings.llm_long_timeout` | 미지정 | node lookup |
| SQL_VALIDATOR | `agents/nodes/reason/sql_validator.py:836` | `1024` 하드코딩 | `settings.llm_default_timeout` | 미지정 | node lookup |
| RECOVERY_AGENT | `agents/nodes/reason/recovery_agent.py:466` | `1024` 하드코딩 | `settings.llm_long_timeout` | 미지정 | node lookup |
| ANALYZER (stream) | `services/data_analyzer.py:501` | `settings.llm_format_max_tokens` | `settings.llm_long_timeout` | 미지정 | node lookup |
| ANALYZER (non-stream) | `services/data_analyzer.py:514` | `settings.llm_format_max_tokens` | `settings.llm_long_timeout` | 미지정 | node lookup |
| VISUALIZER_JUDGMENT | `services/data_analyzer.py:198` | `256` 하드코딩 | `settings.llm_default_timeout` | 미지정 | node lookup |
| VISUALIZER_SVG (stream) | `services/data_analyzer.py:266` | `settings.llm_svg_max_tokens` | `settings.llm_long_timeout` | 미지정 | node lookup |

**직접 client.messages.create 호출 (wrapper 미경유):**

| 호출지점 | 파일 | LLMNode 미사용 |
|----------|------|---------------|
| 분석 질의 재작성 | `services/intent_classifier.py:314` | max_tokens=default, timeout=default |
| SVG 생성 (non-stream) | `services/data_analyzer.py:326` | max_tokens=svg, timeout=long |
| SQL 이력 설명 추론 | `tools/seed_sql_history.py:475` | max_tokens=default, timeout=default |

### 1.3 모델별 파라미터 지원 현황

| 파라미터 | Anthropic (Claude) | OpenAI (GPT) | OpenAI (o-series) | Qwen (vLLM) | Gemini | IBK Gateway |
|----------|-------------------|-------------|------------------|-------------|--------|------------|
| temperature | 네이티브 | 네이티브 | **미지원** (무시) | 네이티브 | 네이티브 | **무시** |
| thinking | budget_tokens | 미지원 (무시) | reasoning_effort | enable_thinking (bool) | reasoning_effort | **무시** |
| max_tokens | 네이티브 | 네이티브 | max_completion_tokens (32K floor) | 네이티브 | 네이티브 | **무시** |
| timeout | 네이티브 | 네이티브 | 네이티브 | 네이티브 | 네이티브 | 네이티브 |

---

## 2. 설계

### 2.1 핵심 아이디어

`thinking_modes.py`를 `llm_call_profiles.py`로 확장하여, **LLM 호출 단위별 모든 속성을 하나의 프로파일 객체**로 묶는다.

```
Before:  thinking → thinking_modes.py (중앙)
         max_tokens → config.py + 호출지점 (분산)
         timeout → config.py + 호출지점 (분산)
         temperature → 없음

After:   thinking + temperature + max_tokens + timeout → llm_call_profiles.py (중앙)
         config.py → 글로벌 기본값만 유지 (폴백용)
```

### 2.2 파일 구조

```
src/agents/nodes/
  thinking_modes.py        → 삭제 (llm_call_profiles.py로 통합)
  llm_call_profiles.py     → 신규
```

### 2.3 데이터 모델

```python
"""LLM 호출 단위별 속성 프로파일.

각 LLM 호출 지점(프롬프트 단위)의 thinking·temperature·max_tokens·timeout을
한 곳에서 관리한다. 모델 교체(Claude → Qwen3.5 → GPT OSS)·튜닝 시
이 파일만 수정하면 된다.

설계 결정:
    - LLM을 호출하는 지점(프롬프트 단위)만 등록한다.
    - None 필드는 config.py의 글로벌 기본값으로 폴백한다.
    - 어댑터별 파라미터 변환(temperature 무시, thinking 매핑 등)은
      기존 client.py 어댑터가 담당한다. 이 모듈은 모델 무관한 "의도"만 선언.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ThinkingMode(StrEnum):
    """LLM thinking 추론 깊이."""
    OFF = "off"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class LLMNode(StrEnum):
    """LLM 호출 단위 식별자."""
    # ── Interpret 계층 ──
    INTENT_CLASSIFIER = "intent_classifier"

    # ── Reason 계층 ──
    NORMALIZE_QUERY_PHASE1 = "normalize_query_phase1"
    NORMALIZE_QUERY_PHASE2 = "normalize_query_phase2"
    CONTEXT_INTERPRETER = "context_interpreter"
    SQL_GENERATOR = "sql_generator"
    SQL_VALIDATOR = "sql_validator"
    RECOVERY_AGENT = "recovery_agent"

    # ── Present 계층 ──
    ANALYZER = "analyzer"
    VISUALIZER_JUDGMENT = "visualizer_judgment"
    VISUALIZER_SVG = "visualizer_svg"


@dataclass(frozen=True, slots=True)
class LLMCallProfile:
    """LLM 호출 단위의 속성 프로파일.

    None 필드는 config.py의 글로벌 기본값(settings.llm_default_*)으로 폴백한다.
    호출지점에서 명시적으로 override할 수도 있다.
    """
    thinking: ThinkingMode = ThinkingMode.OFF
    temperature: float | None = None     # None → 서버 기본값
    max_tokens: int | None = None        # None → settings.llm_default_max_tokens
    timeout: float | None = None         # None → settings.llm_default_timeout


# ─── 노드별 프로파일 레지스트리 ───
NODE_PROFILES: dict[LLMNode, LLMCallProfile] = {
    # ── Interpret 계층 ──
    LLMNode.INTENT_CLASSIFIER: LLMCallProfile(
        thinking=ThinkingMode.HIGH,      # 다단계 분류 (연속성+의도+모호성)
        temperature=0.0,                 # 분류 → 결정적
    ),

    # ── Reason 계층 ──
    LLMNode.NORMALIZE_QUERY_PHASE1: LLMCallProfile(
        thinking=ThinkingMode.HIGH,      # 8슬롯 의미 분해
        temperature=0.0,
        max_tokens=3000,                 # normalization_max_tokens 대체
        timeout=30.0,                    # long
    ),
    LLMNode.NORMALIZE_QUERY_PHASE2: LLMCallProfile(
        thinking=ThinkingMode.MEDIUM,    # 12규칙 교차 검증
        temperature=0.0,
        max_tokens=3000,
        timeout=30.0,
    ),
    LLMNode.CONTEXT_INTERPRETER: LLMCallProfile(
        thinking=ThinkingMode.MEDIUM,    # 수집된 증거 기반 적합성 판정
        temperature=0.0,
        max_tokens=2048,                 # batch 기준 (step은 호출 시 override)
        timeout=30.0,
    ),
    LLMNode.SQL_GENERATOR: LLMCallProfile(
        thinking=ThinkingMode.HIGH,      # SQL 합성 (정확도 최우선)
        temperature=0.0,
        max_tokens=3000,
        timeout=30.0,
    ),
    LLMNode.SQL_VALIDATOR: LLMCallProfile(
        thinking=ThinkingMode.MEDIUM,    # 8체크 품질 검증
        temperature=0.0,
        max_tokens=1024,
    ),
    LLMNode.RECOVERY_AGENT: LLMCallProfile(
        thinking=ThinkingMode.HIGH,      # 실패 진단 + 복구 전략
        temperature=0.0,
        max_tokens=1024,
        timeout=30.0,
    ),

    # ── Present 계층 ──
    LLMNode.ANALYZER: LLMCallProfile(
        thinking=ThinkingMode.MEDIUM,    # 패턴 마이닝/인사이트
        temperature=0.3,                 # 인사이트 다양성 허용
        max_tokens=3000,
        timeout=30.0,
    ),
    LLMNode.VISUALIZER_JUDGMENT: LLMCallProfile(
        thinking=ThinkingMode.OFF,       # 규칙 기반 차트 분류
        temperature=0.0,
        max_tokens=256,
    ),
    LLMNode.VISUALIZER_SVG: LLMCallProfile(
        thinking=ThinkingMode.OFF,       # SVG 좌표 변환/생성
        temperature=0.0,
        max_tokens=4000,
        timeout=30.0,
    ),
}

DEFAULT_PROFILE = LLMCallProfile()


def get_profile(node_name: str) -> LLMCallProfile:
    """LLM 호출 단위의 프로파일을 반환한다.

    NODE_PROFILES에 없는 노드는 DEFAULT_PROFILE을 반환한다.
    """
    try:
        key = LLMNode(node_name)
    except ValueError:
        return DEFAULT_PROFILE
    return NODE_PROFILES.get(key, DEFAULT_PROFILE)


# ── 하위 호환 함수 (마이그레이션 완료 후 제거 예정) ──
def get_thinking_mode(node_name: str) -> ThinkingMode:
    """기존 thinking_modes.get_thinking_mode() 호환 래퍼."""
    return get_profile(node_name).thinking
```

### 2.4 config.py 역할 변경

| 필드 | 변경 전 | 변경 후 |
|------|--------|--------|
| `llm_default_max_tokens` | 호출지점에서 직접 참조 | 프로파일 None 폴백용 기본값 (유지) |
| `llm_default_timeout` | 호출지점에서 직접 참조 | 프로파일 None 폴백용 기본값 (유지) |
| `llm_long_timeout` | 호출지점에서 직접 참조 | **삭제 후보** — 프로파일에 흡수 |
| `llm_format_max_tokens` | 호출지점에서 직접 참조 | **삭제 후보** — 프로파일에 흡수 |
| `llm_svg_max_tokens` | 호출지점에서 직접 참조 | **삭제 후보** — 프로파일에 흡수 |
| `normalization_max_tokens` | query_normalizer에서 참조 | **삭제 후보** — 프로파일에 흡수 |

> **판단**: `llm_default_max_tokens`와 `llm_default_timeout`만 유지 (프로파일 미등록 노드의 폴백).
> 나머지 용도별 설정은 프로파일로 이관. 단, 기존 .env 호환을 위해 1 릴리스 동안 deprecated 유지 가능.

### 2.5 retry.py 변경 — 프로파일 자동 적용

```python
# ── 변경 전 (retry.py:93-111) ──
if max_tokens is None:
    max_tokens = settings.llm_default_max_tokens
if timeout is None:
    timeout = settings.llm_default_timeout
if thinking is None:
    thinking = get_thinking_mode(node_name or _prev_node)

# ── 변경 후 ──
profile = get_profile(node_name or _prev_node)
if max_tokens is None:
    max_tokens = profile.max_tokens or settings.llm_default_max_tokens
if timeout is None:
    timeout = profile.timeout or settings.llm_default_timeout
if temperature is None:
    temperature = profile.temperature   # None이면 서버 기본값 유지
if thinking is None:
    thinking = profile.thinking
```

**핵심 원칙**: 호출지점에서 명시적으로 전달한 값 > 프로파일 값 > config.py 글로벌 기본값

```
우선순위: 호출지점 explicit > LLMCallProfile > settings.llm_default_*
```

### 2.6 호출지점 변경 — Before/After

**Before** (context_interpreter.py:479-484):
```python
raw, result = await llm_call_with_parse_retry(
    system=system_prompt,
    messages=[{"role": "user", "content": user_content}],
    parse_fn=_parse_fn,
    max_tokens=2048,                          # 하드코딩
    timeout=settings.llm_long_timeout,        # config 참조
    node_name=LLMNode.CONTEXT_INTERPRETER,    # thinking만 lookup
)
```

**After**:
```python
raw, result = await llm_call_with_parse_retry(
    system=system_prompt,
    messages=[{"role": "user", "content": user_content}],
    parse_fn=_parse_fn,
    node_name=LLMNode.CONTEXT_INTERPRETER,
    # max_tokens, timeout, temperature, thinking → 프로파일에서 자동 적용
)
```

**step 호출처럼 프로파일과 다른 값이 필요한 경우** (context_interpreter.py:579):
```python
raw, result = await llm_call_with_parse_retry(
    system=system_prompt,
    messages=[{"role": "user", "content": user_content}],
    parse_fn=_parse_step,
    node_name=LLMNode.CONTEXT_INTERPRETER,
    max_tokens=1024,    # 프로파일(2048) 대신 명시적 override
)
```

### 2.7 모델별 확장성 — 어댑터 레이어 불변

프로파일은 **모델 무관한 의도(intent)**만 선언한다.
어댑터별 파라미터 변환은 기존 `client.py`가 담당하므로 **어댑터 코드 변경 없음**.

```
LLMCallProfile (모델 무관 의도)
    ↓ retry.py에서 resolve
    ↓
client.messages.create(temperature=0.0, thinking="high", ...)
    ↓ 어댑터가 모델별 변환 (기존 로직 그대로)
    ↓
AnthropicMessages  → temperature=0.0, thinking={"type":"enabled","budget_tokens":8192}
OpenAIMessages     → temperature=0.0, reasoning_effort="high" (o-series는 temp 무시)
   (Qwen)         → temperature=0.0, extra_body.enable_thinking=True
   (Gemini)       → temperature=0.0, reasoning_effort="high"
IBKCustomMessages  → 모두 무시 (게이트웨이 위임)
```

**향후 모델 추가 시**:
- `client.py`에 새 어댑터 추가 (또는 OpenAICompatibleMessages 확장)
- `llm_call_profiles.py`는 **변경 불필요** (모델 무관)
- 모델 특성에 따라 프로파일 값 튜닝만 (temperature, thinking 수준 조정)

---

## 3. 영향도 분석

### 3.1 변경 대상 파일 목록

| # | 파일 | 변경 유형 | 변경 내용 |
|---|------|----------|----------|
| 1 | `src/agents/nodes/thinking_modes.py` | **삭제** | llm_call_profiles.py로 대체 |
| 2 | `src/agents/nodes/llm_call_profiles.py` | **신규** | ThinkingMode + LLMNode + LLMCallProfile + NODE_PROFILES + get_profile() |
| 3 | `src/utils/llm/retry.py` | 수정 | get_thinking_mode → get_profile 전환, 프로파일 기반 기본값 해석 |
| 4 | `src/utils/llm/client.py` | 수정 | import 경로 변경 (thinking_modes → llm_call_profiles) |
| 5 | `src/services/intent_classifier.py` | 수정 | import 변경 + max_tokens/timeout 하드코딩 제거 |
| 6 | `src/services/query_normalizer.py` | 수정 | import 변경 + normalization_max_tokens 참조 제거 |
| 7 | `src/services/data_analyzer.py` | 수정 | import 변경 + 4개 호출지점 max_tokens/timeout 제거 |
| 8 | `src/agents/nodes/reason/context_interpreter.py` | 수정 | import 변경 + 2개 호출지점 파라미터 정리 |
| 9 | `src/agents/nodes/reason/sql_generator.py` | 수정 | import 변경 + 호출지점 파라미터 정리 |
| 10 | `src/agents/nodes/reason/sql_validator.py` | 수정 | import 변경 + 호출지점 파라미터 정리 |
| 11 | `src/agents/nodes/reason/recovery_agent.py` | 수정 | import 변경 + 호출지점 파라미터 정리 |
| 12 | `src/config.py` | 수정 | llm_format_max_tokens 등 deprecated 주석 추가 (1차에선 제거 안 함) |
| 13 | `tests/auto/unit/test_thinking_modes.py` | 수정 | import 경로 변경 + 프로파일 검증 테스트 추가 |

### 3.2 변경하지 않는 파일

| 파일 | 이유 |
|------|------|
| `src/utils/llm/client.py` 어댑터 로직 | 모델별 변환 로직 유지 (import 경로만 변경, 어댑터 구현은 불변) |
| `src/utils/llm/circuit_breaker.py` | 프로파일과 무관 |
| `src/utils/llm/response.py` | 프로파일과 무관 |
| `src/tools/seed_sql_history.py` | LLMNode 미사용 직접 호출 (변경 대상이나 우선순위 낮음) |
| `devtools/scripts/enrich_sql_history.py` | DevTools 스크립트 (프로덕션 외) |

### 3.3 직접 client.messages.create 호출 (3건) 처리 방침

| 호출지점 | 판단 |
|----------|------|
| `intent_classifier.py:314` rewrite_analysis_query | LLMNode 미등록 → 2차에서 LLMNode.QUERY_REWRITER 추가 검토 |
| `data_analyzer.py:326` generate_svg_via_llm non-stream | 이미 streaming 경로가 주 경로 → 2차에서 통합 검토 |
| `tools/seed_sql_history.py:475` | 개발용 도구 → 프로파일 적용 불필요 |

---

## 4. 구현 계획

### Phase 1: 프로파일 모듈 생성 + retry.py 통합

1. `llm_call_profiles.py` 생성 (§2.3 코드)
2. `retry.py` 수정 — get_profile() 기반 기본값 해석 (§2.5) + resolve 디버그 로깅 (§7.3)
3. `client.py` import 경로 변경
4. `test_thinking_modes.py` → `test_llm_call_profiles.py` 전환 + 프로파일 검증 추가

**완료 기준**: `test_llm_call_profiles.py` 통과 + 기존 전체 단위 테스트 통과 + mypy 통과

### Phase 2: 호출지점 정리

5. 12개 호출지점에서 하드코딩된 max_tokens/timeout 제거 (§3.1 #5~#11)
6. context_interpreter step 호출처럼 프로파일과 다른 값이 필요한 곳만 명시적 override 유지
7. `rewrite_analysis_query` 직접 호출을 wrapper 경유로 전환 + LLMNode.QUERY_REWRITER 추가

**완료 기준**: 전체 단위 테스트 통과 + mypy 통과 + E2E 파이프라인 정상 동작

### Phase 3: config.py 정리

8. `llm_format_max_tokens`, `llm_svg_max_tokens`, `normalization_max_tokens` deprecated WARNING 추가 (§7.4)
9. .env.example 업데이트 + 글로벌 기본값 동작 문서화 (��7.5)

**완료 기준**: deprecated 변수 커스텀 시 WARNING 로그 출력 확인 + 전체 테스트 통과

### Phase 4 (선택): 직접 호출 통합

10. `generate_svg_via_llm` non-stream 경로를 wrapper 경유로 전환
11. 필요 시 LLMNode 추가

---

## 5. 테스트 계획

### 5.1 단위 테스트

| 테스트 | 검증 내용 |
|--------|----------|
| `test_llm_call_profiles.py` | LLMCallProfile 생성, get_profile() 등록/미등록 노드, get_thinking_mode() 하위 호환 |
| 프로파일 커버리지 검증 | 모든 LLMNode enum 멤버가 NODE_PROFILES에 등록되어 있는지 자동 검출 |
| 3단 폴백 해석 검증 | retry.py에서 명시적 > 프로파일 > config 폴백 우선순위 동작 확인 |
| 프로파일-어댑터 전달 검증 | mock LLM client로 resolve된 값이 실제 API 호출 파라미터에 반영되는지 확인 |
| 기존 단위 테스트 | import 경로 변경 후 전체 테스트 통과 확인 |

### 5.2 통합/E2E 테스트

| 테스트 | 검증 내용 |
|--------|----------|
| E2E 파이프라인 | 질의 처리 전체 흐름 정상 동작 확인 |
| 성능 회귀 기준선 | 도입 전후 각 노드의 응답 시간/토큰 소비량 비교 (기존 eval_trace_json 활용) |

### 5.3 폐쇄망 smoke 테스트 시나리오

| # | 시나리오 | 검증 포인트 |
|---|---------|-----------|
| 1 | 단순 추출 질의 | INTENT_CLASSIFIER + SQL_GENERATOR 경로 정상 응답 |
| 2 | 분석 질의 | ANALYZER + VISUALIZER 경로 정상 응답 |
| 3 | 프로파일 로그 | DEBUG 레벨에서 각 노드의 resolve된 값 출력 확인 |
| 4 | deprecated 변수 | 기존 .env에 커스텀 값 설정 시 WARNING 로그 출력 확인 |

---

## 6. 리스크 및 대안

### 6.1 리스크

| 리스크 | 영향 | 완화 |
|--------|------|------|
| import 경로 일괄 변경 시 누락 | 런타임 ImportError | mypy + 전체 테스트로 검출 |
| context_interpreter step 호출의 max_tokens override 누락 | 프로파일 값(2048) 적용 → 토큰 낭비 | override 필요 호출지점 명시 목록 관리 |
| config.py 설정 삭제 시 기존 .env 호환성 깨짐 | 배포 실패 | Phase 3에서 deprecated 유지 → 다음 릴리스에서 삭제 |
| ANALYZER temperature=0.3 이 모델별로 다른 효과 | 인사이트 품질 변동 | 모델 교체 시 프로파일 값 재튜닝 (이것이 바로 중앙 관리의 이점) |

### 6.2 대안: YAML/JSON 외부 설정

프로파일을 코드가 아닌 `resources/config/llm_profiles.yaml`로 외부화하는 방안.

**장점**: 코드 수정 없이 튜닝 가능
**단점**: 타입 안전성 상실, IDE 자동완성 불가, 모델별 분기 시 YAML이 복잡해짐
**판단**: 현재 11개 노드로 규모가 작고, 프로파일 변경은 모델 교체 시에만 발생.
Python dataclass로 충분하며, 타입 안전성이 더 중요. **불채택**.

### 6.3 대안: 모델별 프로파일 오버레이

```python
MODEL_OVERRIDES: dict[str, dict[LLMNode, Partial[LLMCallProfile]]] = {
    "qwen": {LLMNode.SQL_GENERATOR: {"thinking": ThinkingMode.HIGH, "temperature": 0.1}},
}
```

**판단**: 현 시점에서는 YAGNI. 모델 교체 시 NODE_PROFILES 자체를 수정하는 것으로 충분.
향후 A/B 테스트나 멀티 모델 병렬 운영이 필요해지면 그때 도입. **보류**.

---

## 7. 전문가 리뷰 결과 및 반영

> 아키텍처 전문가(A), 폐쇄망·운영 전문가(B) 2인의 독립 리뷰 수행.

### 7.1 리뷰 종합

| # | 항목 | 판정 | 출처 | 반영 |
|---|------|------|------|------|
| 1 | `or` 연산 → `is None` 체크 | 위험(버그) | A+B | §2.5 코드 수정 |
| 2 | 프로파일 resolve 디버그 로깅 미설계 | Critical | B | §7.3 신규 추가 |
| 3 | deprecated .env 변수 WARNING 로그 | 개선필요 | B | §7.4 신규 추가 |
| 4 | config.py 글로벌 기본값 이원화 혼란 | 개선필요 | B | §7.5 문서화 강화 |
| 5 | 모델 교체 시 temperature 튜닝 가이드 부재 | 개선필요 | B | §7.6 가이드 추가 |
| 6 | 테스트 커버리지 부족 (프로파일 통합, 커버리지, smoke) | Critical | A+B | §5 테스트 계획 보강 |
| 7 | Phase별 완료 기준 미정의 | 개선필요 | B | §4 구현계획 보강 |
| 8 | 영향도 테이블 3.1 vs 3.2 client.py 기술 불일치 | 정보 | B | §3.2 명확화 |
| 9 | rewrite_analysis_query 우선순위 (Phase 4 → Phase 2) | 정보 | B | 검토 후 Phase 2로 이동 |
| 10 | YAML 외부화 대신 .env 노드별 오버라이드 절충안 | 개선필요 | B | §7.7 향후 검토 사항으로 기록 |

### 7.2 [반영] §2.5 폴백 체인 — `or` → `is None` 수정

```python
# ── 수정 후 (or 연산의 falsy 문제 해소) ──
profile = get_profile(node_name or _prev_node)
if max_tokens is None:
    max_tokens = profile.max_tokens if profile.max_tokens is not None else settings.llm_default_max_tokens
if timeout is None:
    timeout = profile.timeout if profile.timeout is not None else settings.llm_default_timeout
if temperature is None:
    temperature = profile.temperature   # None이면 서버 기본값 유지
if thinking is None:
    thinking = profile.thinking
```

### 7.3 [신규] 프로파일 resolve 디버그 로깅

retry.py의 프로파일 resolve 직후에 DEBUG 레벨 로그를 추가한다.
운영 환경에서는 INFO 이상만 출력하므로 성능 영향 없고,
문제 발생 시 `LOG_LEVEL=DEBUG`로 전환하여 즉시 확인 가능.

```python
logger.debug(
    "llm_profile_resolved",
    node=node_name,
    max_tokens=max_tokens,
    timeout=timeout,
    temperature=temperature,
    thinking=str(thinking),
    source_max_tokens="explicit" if orig_max_tokens else ("profile" if profile.max_tokens is not None else "config"),
)
```

기존 트래커(`dispatch_tracking_event`)의 `LLM_CALL` 이벤트에도
`resolved_profile` 필드를 추가하여 trace JSON에서 사후 분석 가능하게 한다.

### 7.4 [신규] deprecated .env 변수 WARNING 로그

config.py에서 deprecated 필드를 즉시 삭제하지 않되,
기본값과 다른 값이 감지되면 WARNING 로그를 출력한다.

```python
@model_validator(mode="after")
def _warn_deprecated_llm_fields(self) -> "Settings":
    _DEPRECATED = {
        "llm_format_max_tokens": 3000,
        "llm_svg_max_tokens": 4000,
        "normalization_max_tokens": 3000,
    }
    for field_name, default_val in _DEPRECATED.items():
        current = getattr(self, field_name)
        if current != default_val:
            logger.warning(
                "deprecated_env_override_ignored",
                field=field_name, value=current,
                hint="이 설정은 llm_call_profiles.py로 이관되었습니다."
            )
    return self
```

### 7.5 [신규] config.py 글로벌 기본값 동작 문서화

`.env.example` 및 config.py docstring에 다음을 명시:

> `LLM_DEFAULT_MAX_TOKENS`, `LLM_DEFAULT_TIMEOUT`은
> llm_call_profiles.py에 해당 필드가 None인 노드에만 적용됩니다.
> 현재 INTENT_CLASSIFIER만 해당. 개별 노드 튜닝은 llm_call_profiles.py를 수정하세요.

### 7.6 [신규] 모델 교체 시 프로파일 튜닝 가이드

| 속성 | Claude → Qwen3.5 | Claude → Solar Pro 2 | 비고 |
|------|------------------|---------------------|------|
| temperature (분류/SQL) | 0.0 유지 | 0.0 유지 | 결정적 태스크는 모델 무관 |
| temperature (ANALYZER) | 0.3 → **0.1~0.2**로 시작 | 0.3 → **0.1~0.2**로 시작 | Solar/Qwen이 temp sensitivity 높음 |
| max_tokens | 현재값 × **1.2~1.5** | 현재값 유지 | Qwen 한국어 토큰 효율 낮을 수 있음 |
| thinking | HIGH → enable_thinking=True | 해당 없음 (Solar 미지원) | Qwen은 budget 개념 없이 on/off |

> 모델 교체 후 벤치마크 수행 → 프로파일 값 조정 → 재배포 사이클이 필요하다.

### 7.7 [향후 검토] .env 노드별 오버라이드

폐쇄망에서 모델 교체 후 코드 재배포 없이 `temperature`, `max_tokens`를 튜닝하려면
`.env`에서 노드별 오버라이드가 가능해야 한다. 현 시점에서는 구현하지 않으나,
반복 튜닝이 필요해지면 다음 형태의 `.env` 오버라이드 레이어를 추가한다:

```
LLM_PROFILE_ANALYZER_TEMPERATURE=0.2
LLM_PROFILE_SQL_GENERATOR_MAX_TOKENS=4000
```

코드의 타입 안전한 기본값은 유지하면서, `.env` 한 줄 수정으로 튜닝 가능.
전체 YAML 외부화보다 범위가 작고 pydantic-settings와 자연스럽게 통합된다.

---

## 8. 핵심 설계 결정 요약

| 결정 | 근거 |
|------|------|
| dataclass(frozen=True) 사용 | 런타임 불변 보장 + slots으로 메모리 최적화 |
| None 폴백 → config.py | 기존 .env 오버라이드 체계 유지 |
| 모델 무관한 의도만 선언 | 어댑터 레이어 변경 불필요, 관심사 분리 |
| 호출지점 explicit override 허용 | context_interpreter step 등 예외 대응 |
| thinking_modes.py 삭제 (코드 이관) | 두 모듈 병존 시 혼란, 단일 진실 원천 유지 |
| get_thinking_mode() 하위 호환 함수 유지 | 마이그레이션 중 import 오류 방지 |
