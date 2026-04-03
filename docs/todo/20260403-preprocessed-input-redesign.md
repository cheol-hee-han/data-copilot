# preprocessed_input 재설계: 추출 질의 / 분석 질의 분리

> 작성일: 2026-04-03
> 상태: 설계 확정, 구현 대기

## 1. 문제 정의

DATA_ANALYSIS 유형에서 "차트로 보여줘", "분석해줘" 같은 표현 지시어가
`preprocessed_input`을 통해 reason 계층(SQL 생성)에 노이즈로 유입된다.

```
원문: "이번년도 예금신규 top 10 지점 차트로 보여줘"

현재: reason 계층도 present 계층도 동일한 preprocessed_input을 소비
→ sql_generator가 "차트로 보여줘"까지 받음 (노이즈)
```

### 현재 `preprocessed_input` 사용처

| 계층 | 노드 | 파일:라인 | 용도 |
|------|------|-----------|------|
| reason | `reasoning_preparer` | `reasoning_preparer.py:60` | 쿼리 분해, searched_queries 초기화 |
| reason | `sql_generator` | `sql_generator.py:211,216,309` | `{original_query}` 템플릿 치환 |
| reason | `sql_validator` | `sql_validator.py:102` | SQL 검증 시 원본 질의 참조 |
| reason | `knowledge_interpreter` | `knowledge_interpreter.py:111` | 지식 해석 시 원본 질의 참조 |
| present | `formatter` | `formatter.py:65` | DATA_EXTRACTION 결과 포맷팅 |
| present | `analyzer` | `analyzer.py:58` | DATA_ANALYSIS 분석/시각화 |

## 2. 설계 방침

### 3필드 역할 분리

```
원문: "이번년도 예금신규 top 10 지점 차트로 보여줘"

preprocessed_input = "이번년도 예금신규 top 10 지점"
    → 원문 어투 그대로, 표현 지시어만 제거
    → reason 계층 primary (sql_generator 등이 소비)

rewritten_query    = "2026년도 예금 신규 기준 상위 10개 지점을 조회한다"
    → preprocessed_input을 명시적으로 풀어쓴 정규화 질의
    → 기존 역할 그대로 (보조 참조, search_keywords 생성 등)

analysis_query     = "이번년도 예금신규 top 10 지점 차트로 보여줘"
    → 분석/시각화 의도가 포함된 전체 질의 (CONTINUE 시 맥락 반영)
    → present 계층 전용 (analyzer가 소비)
```

### 핵심 원칙

1. **`preprocessed_input`을 추출 중심으로 변경** — 표현 지시어를 제거하여 reason 계층에 노이즈 없이 전달
2. **`analysis_query`를 새로 추가** — DATA_ANALYSIS 전용, 본래 목적(분석/시각화)을 보존하여 present 계층에 전달
3. **`rewritten_query`는 역할 변경 없음** — preprocessed_input(추출 중심)의 정규화 보조
4. **DATA_EXTRACTION은 완전 무변경** — analysis_query 미사용, preprocessed_input도 기존과 동일 (표현 지시어 없음)
5. **CONTINUE 케이스에서도 동일 적용** — context_classifier가 맥락 반영 + 분리를 동시 수행

### `rewritten_query`를 reason 계층 primary로 격상하지 않는 이유

| 리스크 | 설명 |
|--------|------|
| 왜곡 | LLM이 한 번 가공한 결과. "top 10"을 "건수 기준 상위 10"으로 추론하면 확정된 사실처럼 전파 |
| 정보 손실 | 폐쇄망 모델이 재작성 시 미묘한 뉘앙스를 떨어뜨릴 가능성 |
| 이중 추론 | normalize_query LLM의 오해가 sql_generator LLM에 전파 → 디버깅 어려움 |

→ reason 계층은 원문 어투를 유지한 `preprocessed_input`(표현 지시어만 제거)을 직접 읽는 게 가장 안전하다.
→ `rewritten_query`는 보조 참조로 유지한다.

## 3. 생성 시점: `context_classifier`

context_classifier가 이미 **의도 분류**(DATA_ANALYSIS 판정)와 **맥락 해석**(continue_context)을 수행하므로,
추출/분석 분리까지 담당하는 것이 가장 자연스럽다.
한국어 조사 변형("차트로", "차트를", "차트로도")까지 유연하게 처리하려면 LLM 기반이 적합하다.

### 3.1 context_classifier 출력 스키마 변경

DATA_ANALYSIS일 때만 `extraction_focus` 필드를 추가 출력한다.

```json
{
  "continuity": {
    "label": "NEW",
    "confidence": "HIGH",
    "reason": "이전 대화 없는 독립 질의",
    "context": ""
  },
  "intent": {
    "label": "DATA_ANALYSIS",
    "confidence": "HIGH",
    "reason": "시각화(차트) 형태의 분석 결과 요청"
  },
  "extraction_focus": "이번년도 예금신규 top 10 지점"
}
```

- `extraction_focus`: 분석/시각화/출력형식 지시어를 제거한 데이터 추출 중심 질의
- DATA_EXTRACTION/CASUAL_TALK/META_QUESTION에서는 빈 문자열 `""`
- CONTINUE + DATA_ANALYSIS에서는 맥락 반영 + 지시어 제거된 추출 질의

### 3.2 프롬프트 변경: `context_classifier_system.txt`

출력 형식 섹션에 추가:

```
## extraction_focus 작성 규칙

DATA_ANALYSIS일 때만 작성합니다. 그 외 의도에서는 빈 문자열 ""을 출력하세요.

목적: 사용자 질의에서 분석/시각화/출력형식 지시어를 제거하고,
"어떤 데이터를 추출해야 하는가"에 집중한 질의를 작성합니다.
이 질의는 SQL 생성의 입력으로 사용됩니다.

규칙:
1. 아래 표현 지시어를 제거한다:
   - 시각화: "차트로 보여줘", "그래프로", "시각화해줘", "막대차트로", "꺾은선으로"
   - 분석: "분석해줘", "요약해줘", "추론해줘", "인사이트", "해석해줘"
   - 출력형식: "엑셀로", "보고서로", "표로 정리해줘"
2. SQL 구조에 영향을 주는 표현은 반드시 보존한다:
   - "비교", "추이", "월별", "지점별", "상위 N", "증감", "변화율", "분포"
3. 사용자의 원래 어투와 표현을 최대한 유지한다 (재작성/풀어쓰기 금지)
4. CONTINUE일 때는 이전 대화 맥락을 반영한 추출 질의를 작성한다
   (continuity.context와 동일한 맥락 해석, 단 지시어만 제거)
```

### 3.3 프롬프트 Few-shot 예시 추가

기존 few-shot 뒤에 DATA_ANALYSIS 예시를 추가/보강한다.

---

#### 예시 1: NEW + DATA_ANALYSIS (시각화 요청)

```
이전 대화: (없음)
현재 입력: 이번년도 예금신규 top 10 지점 차트로 보여줘
→
{
  "continuity": {
    "label": "NEW",
    "confidence": "HIGH",
    "reason": "이전 대화 없는 독립 질의",
    "context": ""
  },
  "intent": {
    "label": "DATA_ANALYSIS",
    "confidence": "HIGH",
    "reason": "시각화(차트) 형태의 분석 결과 요청"
  },
  "extraction_focus": "이번년도 예금신규 top 10 지점"
}
```

#### 예시 2: NEW + DATA_ANALYSIS (분석 요청, SQL 구조 영향 표현 보존)

```
이전 대화: (없음)
현재 입력: 월별 예금 잔액 추이를 분석해줘
→
{
  "continuity": {
    "label": "NEW",
    "confidence": "HIGH",
    "reason": "이전 대화 없는 독립 질의",
    "context": ""
  },
  "intent": {
    "label": "DATA_ANALYSIS",
    "confidence": "HIGH",
    "reason": "추이 분석을 명시적으로 요청"
  },
  "extraction_focus": "월별 예금 잔액 추이"
}
```

→ "분석해줘"만 제거. "월별", "추이"는 SQL 구조(GROUP BY 시간축)에 영향이므로 보존.

#### 예시 3: NEW + DATA_ANALYSIS (복합 지시어)

```
이전 대화: (없음)
현재 입력: 지점별 실적 비교해서 막대차트로 보여줘
→
{
  "continuity": {
    "label": "NEW",
    "confidence": "HIGH",
    "reason": "이전 대화 없는 독립 질의",
    "context": ""
  },
  "intent": {
    "label": "DATA_ANALYSIS",
    "confidence": "HIGH",
    "reason": "지점별 실적 비교를 시각화(막대차트) 요청"
  },
  "extraction_focus": "지점별 실적 비교"
}
```

→ "막대차트로 보여줘" 제거. "비교"는 SQL 구조(COMPARE) 영향이므로 보존.

#### 예시 4: CONTINUE + DATA_ANALYSIS (이전 결과에 시각화 요청)

```
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
  "extraction_focus": "최근 6개월 수신잔액 추이"
}
```

→ context: 맥락 반영된 전체 질의 (analysis_query로 사용)
→ extraction_focus: 동일 맥락이지만 "차트로 보여줘" 제거 (preprocessed_input으로 사용)

#### 예시 5: CONTINUE + DATA_ANALYSIS (이전 결과에 분석 요청)

```
이전 대화:
  사용자: 이번 달 예금 신규 현황 알려줘
  시스템: (예금 신규 현황 표)
현재 입력: 이 데이터 분석해줘
→
{
  "continuity": {
    "label": "CONTINUE",
    "confidence": "HIGH",
    "reason": "이전 예금 신규 현황 결과에 대한 분석 요청",
    "context": "이번 달 예금 신규 현황을 분석해줘"
  },
  "intent": {
    "label": "DATA_ANALYSIS",
    "confidence": "HIGH",
    "reason": "기존 조회 결과에 대한 분석 요청"
  },
  "extraction_focus": "이번 달 예금 신규 현황"
}
```

#### 예시 6: NEW + DATA_EXTRACTION (지시어 없음 — extraction_focus 빈 문자열)

```
이전 대화: (없음)
현재 입력: 이번 달 신규 고객 수 알려줘
→
{
  "continuity": {
    "label": "NEW",
    "confidence": "HIGH",
    "reason": "이전 대화 없는 독립 질의",
    "context": ""
  },
  "intent": {
    "label": "DATA_EXTRACTION",
    "confidence": "HIGH",
    "reason": "신규 고객 수라는 구체적 수치 조회 요청"
  },
  "extraction_focus": ""
}
```

→ DATA_EXTRACTION이므로 extraction_focus 빈 문자열. preprocessed_input은 기존 그대로.

## 4. context_classifier_node 코드 변경

```python
# context_classifier_node 내부 — 정상 경로 (SKIP / NEW / CONTINUE)

updates: dict = {
    "intent": result.intent,
    "intent_confidence": result.confidence,
    "query_category": result.category,
    "is_continuation": result.resolution == HistoryDecision.CONTINUE,
    "status": QueryStatus.INTENT_CLASSIFIED,
    "trace_log": _build_trace(state, result),
}

if result.resolution == HistoryDecision.CONTINUE:
    updates["continue_context"] = result.continue_context
    if result.continue_context:
        updates["preprocessed_input"] = result.continue_context

# DATA_ANALYSIS: extraction_focus → preprocessed_input, 원본 → analysis_query
if result.intent == IntentType.DATA_ANALYSIS:
    extraction = result.extraction_focus  # LLM이 생성한 추출 중심 질의
    if extraction:
        # analysis_query: 분석/시각화 의도 포함 전체 질의
        if result.resolution == HistoryDecision.CONTINUE and result.continue_context:
            updates["analysis_query"] = result.continue_context
        else:
            updates["analysis_query"] = state.preprocessed_input
        # preprocessed_input: 추출 중심 질의로 교체
        updates["preprocessed_input"] = extraction
```

### context_classifier 서비스 파싱 변경

`_parse_response` (context_classifier.py)에서 `extraction_focus` 파싱 추가:

```python
# 기존 파싱 로직 끝부분에 추가
result["extraction_focus"] = data.get("extraction_focus", "")
```

`ContextClassifyResult`에 필드 추가:

```python
@dataclass
class ContextClassifyResult:
    resolution: HistoryDecision
    intent: IntentType = IntentType.UNKNOWN
    confidence: float = 0.0
    category: str = ""
    reason: str = ""
    continue_reason: str = ""
    continue_context: str = ""
    extraction_focus: str = ""      # NEW: 추출 중심 질의 (DATA_ANALYSIS 전용)
    ambiguities: list[dict] | None = None
    is_error: bool = False
```

## 5. State 변경

```python
# state.py — PipelineState

class PipelineState(BaseModel):
    ...
    # ── Interpret 계층 ──
    preprocessed_input: str = ""          # 추출 중심 질의 (reason 계층 소비)
    # W: CTX (DATA_ANALYSIS 시)  R: ANL (분석/시각화 목적 참조)
    analysis_query: str = ""              # 분석/시각화 의도 포함 전체 질의 (present 계층 소비)
    ...
```

## 6. Present 계층 변경

**analyzer.py** (DATA_ANALYSIS 경로):

```python
# 변경 전
user_input=state.preprocessed_input,

# 변경 후
user_input=state.analysis_query or state.preprocessed_input,
```

**formatter.py** (DATA_EXTRACTION 경로): **변경 없음**.

## 7. 변경 후 흐름

```
[DATA_EXTRACTION — 기존과 완전 동일]

runner.py: preprocessed_input = sanitized.text
    ↓
context_classifier_node:
    intent=DATA_EXTRACTION, extraction_focus="" → preprocessed_input 변경 없음
    analysis_query="" (미설정)
    (CONTINUE 시) preprocessed_input = continue_context
    ↓
normalize_query_node: preprocessed_input → rewritten_query (보조), 8-Slot
    ↓
reason 계층: preprocessed_input 소비 (기존과 완전 동일)
    ↓
formatter: preprocessed_input 소비 (기존과 완전 동일)


[DATA_ANALYSIS — 변경됨]

runner.py: preprocessed_input = sanitized.text
    예: "이번년도 예금신규 top 10 지점 차트로 보여줘"
    ↓
context_classifier_node:
    intent=DATA_ANALYSIS
    extraction_focus = "이번년도 예금신규 top 10 지점"          (LLM 생성)
    ↓
    analysis_query     = "이번년도 예금신규 top 10 지점 차트로 보여줘"  (원본 보존)
    preprocessed_input = "이번년도 예금신규 top 10 지점"               (추출 중심으로 교체)
    ↓
normalize_query_node:
    preprocessed_input("이번년도 예금신규 top 10 지점") → rewritten_query (보조), 8-Slot
    ↓
reason 계층: preprocessed_input 소비 → SQL 생성 (지시어 노이즈 없음)
    ↓
analyzer: analysis_query 소비 → 분석/시각화 (본래 목적 참조)


[DATA_ANALYSIS + CONTINUE]

이전 대화: "최근 6개월 수신잔액 추이 알려줘" → (표)
현재 입력: "차트로 보여줘"
    ↓
context_classifier_node:
    CONTINUE + DATA_ANALYSIS
    continue_context   = "최근 6개월 수신잔액 추이를 차트로 보여줘"    (맥락 반영)
    extraction_focus   = "최근 6개월 수신잔액 추이"                    (맥락 반영 + 지시어 제거)
    ↓
    analysis_query     = "최근 6개월 수신잔액 추이를 차트로 보여줘"    (continue_context)
    preprocessed_input = "최근 6개월 수신잔액 추이"                    (extraction_focus)
    ↓
(이하 동일)
```

## 8. 엣지 케이스 분석

| 케이스 | intent | preprocessed_input (reason) | analysis_query (present) | 비고 |
|--------|--------|----------------------------|--------------------------|------|
| "예금 신규 top 10 알려줘" | EXTRACTION | "예금 신규 top 10 알려줘" (원본) | "" | 완전 무변경 |
| "예금 신규 top 10 차트로 보여줘" | ANALYSIS | "예금 신규 top 10" | "예금 신규 top 10 차트로 보여줘" | 지시어 분리 |
| "월별 추이 분석해줘" | ANALYSIS | "월별 추이" | "월별 추이 분석해줘" | "월별","추이" 보존 |
| "지점별 비교해서 막대차트로" | ANALYSIS | "지점별 비교" | "지점별 비교해서 막대차트로" | "비교" 보존 |
| CONT: "차트로 보여줘" | ANALYSIS | "최근 6개월 수신잔액 추이" | "최근 6개월 수신잔액 추이를 차트로 보여줘" | 맥락 반영 |
| CONT: "부산은?" | EXTRACTION | "부산 지역 예금 잔액 알려줘" (continue_context) | "" | 무변경 |

### 제거/보존 기준

| 구분 | 표현 예시 | 처리 |
|------|----------|------|
| 제거 (순수 표현 지시어) | "차트로 보여줘", "분석해줘", "시각화해줘", "그래프로", "요약해줘", "엑셀로", "막대차트로" | extraction_focus에서 제거 |
| 보존 (SQL 구조 영향) | "비교", "추이", "월별", "지점별", "상위 N", "증감", "변화율", "분포" | extraction_focus에 보존 |

### extraction_focus 생성 실패 시

context_classifier LLM이 `extraction_focus`를 빈 문자열로 반환하거나 파싱 실패 시:
- preprocessed_input은 기존 로직 그대로 (원본 또는 continue_context)
- analysis_query도 설정하지 않음
- 기존과 완전히 동일한 동작으로 폴백

## 9. 변경 영향 요약

| 구분 | 변경 내용 | 난이도 |
|------|----------|--------|
| `context_classifier_system.txt` | extraction_focus 작성 규칙 + few-shot 6개 추가 | 중 |
| `context_classifier.py` | `ContextClassifyResult.extraction_focus` 필드, `_parse_response` 파싱 | 저 |
| `context_classifier_node` | DATA_ANALYSIS 시 preprocessed_input/analysis_query 할당 | 저 |
| `state.py` | `analysis_query: str = ""` 필드 추가 | 1줄 |
| `analyzer.py:58` | `state.analysis_query or state.preprocessed_input` | 1줄 |
| reason 계층 | **변경 없음** | - |
| formatter.py | **변경 없음** | - |
| normalize_query | **변경 없음** (입력이 추출 중심으로 바뀌므로 rewritten_query도 자연스럽게 추출 중심) | - |

## 10. 미결 사항

- [ ] "이 데이터 분석해줘" (CONTINUE, 새 SQL 불필요) 케이스 — reason 계층 스킵 가능 여부 (라우팅 최적화, 별도 이슈)
- [ ] `output_hint`에 시각화/분석 관련 format 값 추가 여부 (현재 SPEC_SHEET/SUMMARY/DETAIL_LIST/REPORT/COMPARISON/NONE)
- [ ] extraction_focus 품질 검증 — 프롬프트 few-shot의 실제 모델별 테스트 (Claude, Solar Pro 2, Qwen3.5)
- [ ] analysis_query 트레이스 기록 추가 (디버깅용)
