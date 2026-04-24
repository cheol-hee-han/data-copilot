# DATA_ANALYSIS 내 analyzer 필요성 판정 플래그 도입

> **작성일**: 2026-04-17
> **관련 파일**:
> - `src/services/intent_classifier.py`
> - `src/agents/nodes/interpret/intent_classifier.py`
> - `src/agents/graph/pipeline.py`
> - `src/agents/state/state.py`
> - `resources/prompts/interpret/intent_classifier_system.txt`
> **목적**: DATA_ANALYSIS로 분류되어도 **명시적 분석 요청**이 없으면 `analyzer_node`를 스킵하여
>           불필요한 LLM 호출(인사이트 텍스트 생성)과 저차원 분석 결과를 제거한다.

---

## 0. 정책 변경 이력 (2026-04-17 후반 — opt-in 반전)

### 0.1 원래 결정 (초기안)

- `needs_analyzer` 기본값 **True**
- LLM 응답 누락·빈 문자열·값 불분명 시 **True로 보수적 폴백** (analyzer 실행)
- 프롬프트 경계 규칙: "모호하면 true (누락보다 과잉이 안전)"
- 근거: 기존 동작(DATA_ANALYSIS → 항상 analyzer) 유지·회귀 방지

### 0.2 반전 근거 (도메인 컨텍스트 재확인 후)

본 서비스의 주 업무는 **명세(데이터) 추출**이며 analyzer는 opt-in으로 전환.
사용자 피드백:

> "분석해달라는 요청이 명시적으로 없는 경우 굳이 분석할 필요는 없어.
> 이 서비스는 명세를 뽑는게 더 주요한 업무여서."
> "괜히 실제 의미가 없는 저차원 분석결과가 어떤 의미를 가질지 의미가 없기도 해.
> 분석은 필요한 데이터에만 하면 돼."

### 0.3 반전된 현재 정책

| 항목 | 초기안 | 현재 (유효) |
| --- | --- | --- |
| `IntentClassifyResult.needs_analyzer` 기본값 | True | **False** |
| `PipelineState.needs_analyzer` 기본값 | True | **False** |
| 파서 폴백 (필드 누락·빈 문자열) | True | **False** |
| 파서 매칭 로직 | falsy 목록 매칭 → 그 외 True | **명시 true/yes/1만 True** (opt-in) |
| 프롬프트 경계 규칙 | 모호하면 true | **모호하면 false — 무의미 분석 방지** |
| `turn_reset_updates` 값 | True | **False** |
| 프롬프트 OUTPUT_CONTRACT 스키마 예시값 | `"needs_analyzer": true` | **`"needs_analyzer": false`** |

### 0.4 프롬프트 EXAMPLES 동시 조정 (분량 최적화)

- **제거** — CONTINUE + DATA_EXTRACTION "상위 5개 지점" (조건 추가 #1과 교훈 중복)
- **제거** — NEW + CASUAL_TALK "됐어" (HALLUCINATION_GUARD positive form과 중복)
- **추가** — NEW + DATA_ANALYSIS "지난달 대비 예금 잔액이 크게 빠졌는데 무슨 일이야?"
  (구어체 "무슨 일이야?" ↔ 원인 키워드 매핑 학습, 비교+원인 2기준 동시 충족 사례)

### 0.5 하위 섹션 해석 가이드

아래 섹션 1~7은 **초기안 당시의 의사결정 기록**으로 보존한다.
필드 구조·네이밍·라우터 AND 조건 등 **골격 설계는 현재도 유효**하나,
`기본값`·`폴백 방향`·`경계 규칙` 관련 서술은 **§0.3 표를 정답으로 간주**한다.

---

## 1. 문제 정의

### 1.1 현재 구조

현재 `analyzer_node` 실행 여부는 `intent == DATA_ANALYSIS` 단일 조건에 의존한다:

```python
# src/agents/graph/pipeline.py:334
if state.intent == IntentType.DATA_ANALYSIS:
    return "analyzer"
return "visualizer"
```

### 1.2 엣지 케이스

사용자가 "막대차트로 보여줘", "파이차트로 바꿔줘" 같은 **시각화 형식 지정만** 요청하는 경우:

- `intent_classifier`는 "차트로", "시각화" 키워드를 DATA_ANALYSIS의 판별 기준으로 삼아 **DATA_ANALYSIS**로 분류 ([system.txt:48](resources/prompts/interpret/intent_classifier_system.txt#L48))
- 현재 라우터는 DATA_ANALYSIS만 보고 `analyzer_node`로 분기
- 결과:
  1. **불필요한 LLM 호출** — analyzer가 인사이트 텍스트를 생성 (수 초 레이턴시 + 토큰 비용)
  2. **사용자 경험 저하** — 원치 않은 해석 텍스트가 결과 하단에 출력됨
  3. 한편 `visualizer_node`는 **intent 무관 + 데이터 특성 기반**으로 독립 실행됨 ([visualizer.py:7](src/agents/nodes/present/visualizer.py#L7)) → 시각화 자체는 정상 처리

### 1.3 해결해야 할 축 정리

두 축이 **독립**되어 있음을 State/프롬프트에 반영해야 한다:

| 축 | 결정 주체 | 결정 시점 | 비고 |
|----|-----------|-----------|------|
| 시각화 생성 여부 | `visualizer_node` (데이터 특성 기반) | execute_sql 이후 | **이미 intent 무관으로 전환됨** |
| 분석 텍스트 생성 여부 | `intent_classifier` (사용자 의도 기반) | interpret 단계 | **본 설계의 대상** |

---

## 2. 설계 결정

### 2.1 대안 비교

| 대안 | 방식 | 표현력 | 비용 | 의미 명확성 |
|------|------|--------|------|-------------|
| A. IntentType 3분할 (DATA_VISUALIZATION 신설) | 배타적 enum 확장 | 제한적 (조합 불가, "차트+분석"은?) | 중간 (enum·프롬프트·라우팅·테스트·DB 전면) | 낮음 — visualizer가 intent 무관 설계인데 intent 축에 시각화 재도입 → 철학 충돌 |
| B. `needs_analyzer: bool` 플래그 추가 | intent + 직교 boolean | 4조합 전부 표현 (viz만/분석만/둘다/둘다아님) | **낮음 (필드 2개 + 라우터 AND 1줄)** | **높음 — visualizer 축 분리 설계와 대칭** |
| D. Multi-select enum (`list[IntentType]`) | multi-label 분류 | B와 동등 | 높음 (필드 타입 변경, 라우팅 전면 수정, DB 스키마, 70B multi-label 불안정) | 낮음 — 의미 없는 조합 多 (ex. `[VIZ]` 단독 = 추출 없이 시각화?), 축을 enum 이름에 숨김 |

### 2.2 B안 선택 근거

**결정적 근거**: `visualizer_node`가 이미 "intent 무관, 데이터 특성 기반"으로 자리 잡은 상태.
A/D안을 택하면 `DATA_VISUALIZATION` intent 또는 `VISUALIZATION` enum 원소가 생기는데:
- visualizer는 여전히 데이터 특성으로 판단 → intent 축 원소가 실질적 의미 없음
- 결국 "analyzer 스킵 신호"로만 기능 → 본질은 "분석 필요성"인데 이름만 "시각화"
- → 이름과 실체가 불일치. B안을 intent 축으로 위장한 형태.

B안은 **이름과 실체가 일치**한다. 시각화는 데이터가 결정, 분석은 사용자 의도가 결정 —
두 축이 각자 자기 자리에 놓인다.

**부차적 근거**:
- 변경 비용: 한 자릿수 파일(B) vs 두 자릿수(A/D)
- 70B LLM 안정성: single-choice + boolean (B) > 3-way 또는 multi-label (A/D)
- 점진적 성장 경로: 나중에 시각화도 사용자 의도로 판정할 필요가 생기면 `needs_visualizer: bool` 추가 가능 — 축이 명확

### 2.3 네이밍 결정: `reason` → `label_reason`

`intent` 블록에 `needs_analyzer_reason`이 추가되면서 기존 `reason`과 공존 → 70B LLM이 두 필드 내용을 섞어 쓸 위험.

**해결**: JSON 키만 `intent.reason` → `intent.label_reason`으로 변경.

| 관점 | 현재 (`reason` + `needs_analyzer_reason`) | 변경 (`label_reason` + `needs_analyzer_reason`) |
|------|-------------------------------------------|------------------------------------------------|
| 대칭성 | ❌ 비대칭 | ✅ 두 필드 모두 `{subject}_reason` 패턴 |
| LLM 혼동 | ⚠️ 내용 뒤섞기 가능 | ✅ 필드명이 소유 대상 명시 |

`continuity.reason`은 단일이라 유지 (혼동 대상 없음 → 변경 스코프 최소화).

Python 속성 `IntentClassifyResult.reason`은 **유지** — JSON 키만 변경하므로 파이썬 레벨 호환성 보존.

---

## 3. 구현 계획

### 3.1 변경 파일 요약

| 파일 | 변경 규모 |
|------|-----------|
| `src/services/intent_classifier.py` | 필드 2개 + 파서 2줄 + 반환 2줄 |
| `src/agents/state/state.py` | `needs_analyzer: bool = True` 필드 1줄 |
| `src/agents/nodes/interpret/intent_classifier.py` | updates dict에 필드 1줄 |
| `src/agents/graph/pipeline.py` | 라우터 AND 조건 1개 |
| `resources/prompts/interpret/intent_classifier_system.txt` | RULES 1줄 + OUTPUT_CONTRACT 확장 + 예제 키 치환 + 1개 예제 필드 추가 |

### 3.2 `IntentClassifyResult` — 필드 2개 추가

**파일**: `src/services/intent_classifier.py:101-125`

```python
@dataclass
class IntentClassifyResult:
    resolution: HistoryDecision
    intent: IntentType = IntentType.UNKNOWN
    confidence: float = 0.0
    category: str = ""
    reason: str = ""                       # 파이썬 속성명은 유지 (호환성)
    continue_reason: str = ""
    continue_context: str = ""
    ambiguities: list[dict] | None = None

    # ── 추가 ──
    needs_analyzer: bool = True            # 기본값 True (보수적, 기존 동작 유지)
    needs_analyzer_reason: str = ""

    def __post_init__(self) -> None:
        if self.ambiguities is None:
            self.ambiguities = []

    is_error: bool = False
```

**기본값 `True` 근거**: LLM 응답 누락/구버전 시에도 기존 동작(analyzer 실행)을 유지하여 회귀 방지.

### 3.3 파서 — `_parse_response` 변경

**파일**: `src/services/intent_classifier.py:219-283`

```python
# 기존
result["intent_reason"] = intent_obj.get("reason", "")

# 변경
result["intent_reason"] = intent_obj.get("label_reason", "")
result["needs_analyzer"] = bool(intent_obj.get("needs_analyzer", True))
result["needs_analyzer_reason"] = intent_obj.get("needs_analyzer_reason", "")
```

CONTINUE 분기도 동일하게:

```python
if resolution == HistoryDecision.CONTINUE:
    result["continue_reason"] = continuity.get("reason", "")
    result["continue_context"] = continuity.get("context", "")
    result["intent_reason"] = intent_obj.get("label_reason", "")   # 변경
    ...
```

### 3.4 반환부 — `intent_classifier` 함수

**파일**: `src/services/intent_classifier.py:193-214`

두 분기(CONTINUE / 그 외) 모두 `IntentClassifyResult` 생성 시 두 필드 전달:

```python
return IntentClassifyResult(
    resolution=resolution,
    intent=intent,
    confidence=confidence,
    category=category,
    reason=parsed.get("intent_reason", ""),
    ambiguities=parsed.get("ambiguities", []),
    needs_analyzer=parsed.get("needs_analyzer", True),          # 추가
    needs_analyzer_reason=parsed.get("needs_analyzer_reason", ""),  # 추가
)
```

### 3.5 노드 — state로 전달

**파일**: `src/agents/nodes/interpret/intent_classifier.py:248-256`

```python
updates: dict = {
    "intent": result.intent,
    "intent_confidence": result.confidence,
    "query_category": result.category,
    "is_continuation": result.resolution == HistoryDecision.CONTINUE,
    "needs_analyzer": result.needs_analyzer,   # 추가
    "status": QueryStatus.INTENT_CLASSIFIED,
    "trace_log": _build_trace(state, result),
}
```

### 3.6 State 필드

**파일**: `src/agents/state/state.py`

`intent`, `intent_confidence` 근처에 추가:

```python
needs_analyzer: bool = True
```

`turn_reset` 대상에 포함되도록 (턴마다 재판정).

### 3.7 라우터 — AND 조건 1개

**파일**: `src/agents/graph/pipeline.py:324-336`

```python
def _route_after_execution(state: PipelineState) -> str:
    """SQL 실행 후 라우팅.

    DATA_ANALYSIS + needs_analyzer → analyzer → visualizer → formatter
    그 외                          → visualizer → formatter
    """
    if state.status in (QueryStatus.ERROR, QueryStatus.CANCELLED):
        return "error_end"
    if state.intent == IntentType.DATA_ANALYSIS and state.needs_analyzer:
        return "analyzer"
    return "visualizer"
```

docstring의 라우팅 다이어그램도 함께 갱신:
- [pipeline.py:22](src/agents/graph/pipeline.py#L22)
- [pipeline.py:329](src/agents/graph/pipeline.py#L329)

---

## 4. 프롬프트 수정안

### 4.1 `[RULES]` — DATA_ANALYSIS 블록 말미에 1줄 추가

**파일**: `resources/prompts/interpret/intent_classifier_system.txt:41-50`

**현재** ([system.txt:41-51](resources/prompts/interpret/intent_classifier_system.txt#L41-L51))
```
DATA_ANALYSIS — 데이터에 대한 분석/비교/인사이트/판단 요청
  판별 기준 (2개 이상 충족):
  ...
  핵심 구분: 사용자가 원하는 것이 숫자 자체가 아닌 "해석/인사이트/비교 결과"
  단순히 "~ 보고서"로 요청되는 것은 출력 요청으로 본다
```

**변경 후** — 핵심 구분 뒤에 `needs_analyzer` 판정 규칙을 positive form으로 추가 (§3.7 준수 — 70B negative priming 회피):

```
  핵심 구분: 사용자가 원하는 것이 숫자 자체가 아닌 "해석/인사이트/비교 결과"
  단순히 "~ 보고서"로 요청되는 것은 출력 요청으로 본다

  ▶ needs_analyzer 판정 (DATA_ANALYSIS일 때만):
    - 분석/해석/인사이트/원인/평가/비교/추이 키워드가 하나라도 포함되면 true.
    - 시각화 형식 지시어("차트로", "막대로", "파이차트로", "그래프로", "시각화")만 포함되고 위 분석 키워드가 함께 나타나지 않으면 false.
    - 분석 키워드와 시각화 지시어가 함께 나타나면 true (분석 키워드 우선).
    - 경계가 모호하면 true (보수적 기본값 — analyzer 실행이 과잉이라도 누락보다 안전).
    - DATA_ANALYSIS가 아닌 카테고리는 항상 false.
```

**positive form 재작성 근거**: 초기 안은 "키워드 없이 ~만 있으면 false" 같은 negative 문장이었음. Qwen3.5 MoE·Solar Pro 2 계열에서 negative 문장은 부정 대상 토큰을 먼저 priming해 분류 오류를 유발하므로, "~이면 true"로 뒤집어 허용 조건을 선언형으로 나열한다.

### 4.2 `[OUTPUT_CONTRACT]` — intent 블록 확장 + 키 이름 변경

**파일**: `resources/prompts/interpret/intent_classifier_system.txt:493-508`

**현재**
```json
{
  "continuity": {
    "label": "CONTINUE | NEW | UNSURE",
    "confidence": "HIGH | MEDIUM | LOW",
    "reason": "판정 이유",
    "context": "..."
  },
  "intent": {
    "label": "DATA_ANALYSIS | DATA_EXTRACTION | CASUAL_TALK | META_QUESTION | AMBIGUOUS",
    "confidence": "HIGH | MEDIUM | LOW",
    "reason": "의도 분류 근거를 1줄로 설명. UNSURE일 때는 빈 문자열."
  },
  "ambiguities": "..."
}
```

**변경 후**
```json
{
  "continuity": {
    "label": "CONTINUE | NEW | UNSURE",
    "confidence": "HIGH | MEDIUM | LOW",
    "reason": "판정 이유",
    "context": "..."
  },
  "intent": {
    "label": "DATA_ANALYSIS | DATA_EXTRACTION | CASUAL_TALK | META_QUESTION | AMBIGUOUS",
    "confidence": "HIGH | MEDIUM | LOW",
    "label_reason": "라벨 분류 근거를 1줄로 설명. UNSURE일 때는 빈 문자열.",
    "needs_analyzer": "true | false — DATA_ANALYSIS일 때만 의미 있음. 그 외 카테고리는 항상 false.",
    "needs_analyzer_reason": "needs_analyzer 판정 근거 1줄. DATA_ANALYSIS가 아니면 빈 문자열."
  },
  "ambiguities": "..."
}
```

### 4.3 예제 — 키 치환 + 1개 예제에 neg 케이스 필드 추가

**전체 치환 대상** (리네이밍 누락 방지 — LLM이 옛 키로 출력하지 않도록 프롬프트 전 영역 동기화):

1. **예제 intent 블록의 `"reason"` → `"label_reason"`** — 약 15곳
2. **[HALLUCINATION_GUARD] L97** — `"intent의 label, confidence, reason을 모두 빈 문자열로 출력한다"` → `"intent의 label, confidence, label_reason을 모두 빈 문자열로 출력한다"`

두 영역 모두 치환하지 않으면 LLM이 예제/Guard의 옛 키를 재현할 위험이 있다. 특히 HALLUCINATION_GUARD의 올바른 대응 문장은 모델이 포맷 준수 시 강하게 참조하는 지점이라 누락 불가.

**`needs_analyzer` 필드 추가 규칙**:
- `DATA_ANALYSIS` 예제 → `needs_analyzer`, `needs_analyzer_reason` 추가
- `DATA_EXTRACTION` / `CASUAL_TALK` / `META_QUESTION` / `AMBIGUOUS` / `UNSURE` 예제 → `needs_analyzer: false`, `needs_analyzer_reason: ""`

#### 4.3.1 수정 예제 — CONTINUE + DATA_ANALYSIS (시각화만, 신규 엣지 커버)

[system.txt:167-187](resources/prompts/interpret/intent_classifier_system.txt#L167-L187)

**Before**
```json
--- CONTINUE + DATA_ANALYSIS (시각화 요청) ---

이전 대화:
  사용자: 최근 6개월 수신잔액 추이 알려줘
  시스템: (월별 수신잔액 표)
현재 입력: 차트로 보여줘
→
{
  "continuity": {
    "label": "CONTINUE",
    "confidence": "HIGH",
    "reason": "이전 수신잔액 추이 결과를 차트 형태로 변환 요청",
    "context": "최근 6개월 수신잔액 추이를 차트로 보여줘"
  },
  "intent": {
    "label": "DATA_ANALYSIS",
    "confidence": "HIGH",
    "reason": "시각화(차트) 형태의 분석 결과 요청"
  },
  "ambiguities": []
}
```

**After** — 시각화만 요청 → `needs_analyzer: false` 로 명시 (본 설계의 핵심 엣지 케이스)
```json
--- CONTINUE + DATA_ANALYSIS (시각화 형식만 요청, analyzer 불필요) ---

이전 대화:
  사용자: 최근 6개월 수신잔액 추이 알려줘
  시스템: (월별 수신잔액 표)
현재 입력: 차트로 보여줘
→
{
  "continuity": {
    "label": "CONTINUE",
    "confidence": "HIGH",
    "reason": "이전 수신잔액 추이 결과를 차트 형태로 변환 요청",
    "context": "최근 6개월 수신잔액 추이를 차트로 보여줘"
  },
  "intent": {
    "label": "DATA_ANALYSIS",
    "confidence": "HIGH",
    "label_reason": "시각화(차트) 형태 변환 요청",
    "needs_analyzer": false,
    "needs_analyzer_reason": "분석/해석 키워드 없이 시각화 형식 지시어만 포함"
  },
  "ambiguities": []
}
```

#### 4.3.2 수정 예제 — CONTINUE + DATA_ANALYSIS (비교 요청, 기존 true 케이스)

[system.txt:123-143](resources/prompts/interpret/intent_classifier_system.txt#L123-L143)

**After**
```json
--- CONTINUE + DATA_ANALYSIS (비교 요청) ---

이전 대화:
  사용자: 지점별 여신잔액 현황 보여줘
  시스템: (지점별 여신잔액 표)
현재 입력: 지난달이랑 비교해줘
→
{
  "continuity": {
    "label": "CONTINUE",
    "confidence": "HIGH",
    "reason": "이전 지점별 여신잔액 현황에 대해 지난달과의 비교를 추가 요청",
    "context": "지점별 여신잔액을 이번 달과 지난달 비교해줘"
  },
  "intent": {
    "label": "DATA_ANALYSIS",
    "confidence": "HIGH",
    "label_reason": "지난달 대비 비교 분석 요청",
    "needs_analyzer": true,
    "needs_analyzer_reason": "비교 분석 명시 요청 — 해석 텍스트 필요"
  },
  "ambiguities": []
}
```

#### 4.3.3 수정 예제 — NEW + DATA_ANALYSIS (주제 전환)

[system.txt:253-273](resources/prompts/interpret/intent_classifier_system.txt#L253-L273)

**After** (후반 intent 블록)
```json
  "intent": {
    "label": "DATA_ANALYSIS",
    "confidence": "HIGH",
    "label_reason": "추이 분석을 명시적으로 요청",
    "needs_analyzer": true,
    "needs_analyzer_reason": "'분석해줘' 명시 요청"
  },
```

#### 4.3.4 수정 예제 — DATA_EXTRACTION 계열 (모든 예제 공통)

모든 DATA_EXTRACTION / CASUAL_TALK / META_QUESTION / AMBIGUOUS / UNSURE 예제의 `intent` 블록에 다음 2줄 추가:

```json
  "intent": {
    "label": "DATA_EXTRACTION",
    "confidence": "HIGH",
    "label_reason": "...",
    "needs_analyzer": false,
    "needs_analyzer_reason": ""
  },
```

**대상 예제 목록** (파일 내 상대 위치):
- L101 CONTINUE + DATA_EXTRACTION (조건 추가)
- L145 CONTINUE + DATA_EXTRACTION (범위 조정)
- L189 CONTINUE + META_QUESTION
- L211 CONTINUE + DATA_EXTRACTION (조건 변경)
- L233 NEW + DATA_EXTRACTION (이력 없음)
- L275 NEW + CASUAL_TALK (이력 없음)
- L295 NEW + CASUAL_TALK (맥락 종료)
- L316 NEW + META_QUESTION
- L336 NEW + DATA_EXTRACTION (이력 있으나 다른 주제)
- L358 NEW + AMBIGUOUS
- L386 CONTINUE + DATA_EXTRACTION (MEDIUM)
- L470 CONTINUE + DATA_EXTRACTION (명확화 응답)

**UNSURE 예제** (L408, L440) — intent.label이 빈 문자열인 경우도 `needs_analyzer: false`, `needs_analyzer_reason: ""` 로 채움 (누락 방지):

```json
  "intent": {
    "label": "",
    "confidence": "",
    "label_reason": "",
    "needs_analyzer": false,
    "needs_analyzer_reason": ""
  },
```

### 4.4 `[HALLUCINATION_GUARD]` — 신규 규칙 추가는 보류, 기존 L97 키 치환만 수행

**신규 Guard 추가는 보류**: RULES의 판정 규칙이 positive form으로 허용 조건을 명시하므로 Guard로 중복 강조할 필요 없음. 실제 평가 실패 패턴이 수집되면 그때 추가한다.

**단, 기존 L97의 키 치환은 필수** (§4.3 전체 치환 대상 참고).

---

## 5. 검증 체크리스트

### 5.1 단위 동작

- [ ] "막대차트로 보여줘" → `intent=DATA_ANALYSIS`, `needs_analyzer=false`
- [ ] "파이차트로 바꿔줘" → `intent=DATA_ANALYSIS`, `needs_analyzer=false`
- [ ] "그래프로 시각화해줘" → `intent=DATA_ANALYSIS`, `needs_analyzer=false`
- [ ] "분석해줘" → `intent=DATA_ANALYSIS`, `needs_analyzer=true`
- [ ] "추이 분석해줘" → `intent=DATA_ANALYSIS`, `needs_analyzer=true`
- [ ] "차트로 보여주고 분석도 해줘" → `needs_analyzer=true` (분석 키워드 우선)
- [ ] "지점별 여신잔액" → `intent=DATA_EXTRACTION`, `needs_analyzer=false`

### 5.2 라우팅 검증

- [ ] DATA_ANALYSIS + needs_analyzer=true → `analyzer` → `visualizer` → `formatter`
- [ ] DATA_ANALYSIS + needs_analyzer=false → `visualizer` → `formatter` (analyzer 스킵 확인)
- [ ] DATA_EXTRACTION → `visualizer` → `formatter`

### 5.3 하위 호환

- [ ] LLM 응답에서 `needs_analyzer` 필드 누락 시 `True` 기본값으로 기존 동작 유지
- [ ] `label_reason` 필드 누락 시 빈 문자열로 처리 (파서 기본값 동작)
- [ ] 기존 DATA_ANALYSIS 골든셋 회귀 — 대부분 `needs_analyzer=true`로 유지되어야 함

### 5.4 70B LLM 안정성 (closed-network 대비)

- [ ] Solar Pro 2 70B에서 JSON 파싱 실패율 변동 없음 확인
- [ ] `needs_analyzer` 문자열 반환 케이스(`"true"`/`"True"`) 파서 `bool()` 캐스팅으로 흡수 확인
- [ ] 파서 안전장치: `if cat != "DATA_ANALYSIS": needs_analyzer=False` 강제 — 불필요 (라우터 AND 조건이 동일 역할) / 도입하지 않음

### 5.5 트레이스/로그

- [ ] `[intent_classifier.py:317-350](src/agents/nodes/interpret/intent_classifier.py#L317-L350)` REASONING_STEP 이벤트에 `needs_analyzer` 포함 (선택 사항)
- [ ] 라우터 trace 로그에 `analyzer_skipped_reason` 필드로 needs_analyzer_reason 전달 고려 (선택 사항)

---

## 6. 향후 확장 가능성

본 설계는 `needs_{subject}: bool` 패턴의 점진 확장 여지를 남긴다:

| 향후 필드 | 용도 | 현재 상태 |
|-----------|------|-----------|
| `needs_analyzer` | 해석 텍스트 필요 여부 | **본 설계 대상** |
| `needs_visualizer` | 시각화 필요 여부 (사용자 의도 기반) | 현재는 데이터 특성 기반, 필요 시 추가 |
| `needs_download` | 엑셀/CSV 다운로드 요청 여부 | 필요 시 추가 |

각 축은 독립적으로 추가·제거 가능하며, enum 수정이 불필요하다.

---

## 7. 결정 요약

- **B안 채택**: `IntentClassifyResult.needs_analyzer: bool` + `needs_analyzer_reason: str`
- **키 리네이밍**: `intent.reason` → `intent.label_reason` (LLM 혼동 방지)
- **`continuity.reason`은 유지** (혼동 대상 없음, 스코프 최소화)
- **변경 규모**: 파이썬 5개 파일 / 프롬프트 1개 파일 / 실질 15줄 내외 + 예제 15곳 키 치환
- **기본값 `True`**: 하위 호환 + 보수적 동작 (회귀 위험 제거)
- **라우터 AND 조건**: `if intent == DATA_ANALYSIS and needs_analyzer`
