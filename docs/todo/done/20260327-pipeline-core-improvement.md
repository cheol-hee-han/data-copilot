# 파이프라인 코어 로직 개선 설계안

## 현상: 3건 연속 conclude_failure

최근 3건의 trace에서 동일한 패턴으로 실패:

```
질의 → normalize → plan → explore → evaluate(replan) → recover
     → explore → evaluate(replan) → recover → explore → evaluate(conclude_failure) → END
```

| Trace | 질의 | readiness 점수 | 루프 | 종료 |
|-------|------|---------------|------|------|
| 18:35 | 지점별 고객 수 상위 5개 | 41%→52%→50%→58% | 3회 replan | conclude_failure |
| 18:18 | 예금신규 TOP 10 지점 | 72%→60%→65%→67% | 3회 replan | conclude_failure |
| 08:00 | 예금신규 TOP 3 지점 | 29%→29%→29%→29% | 3회 replan | conclude_failure |

---

## 근본 원인 분석 (6가지)

### 원인 1: normalizer LLM이 모호한 용어를 자체 확정

**Trace 증거:**
```json
// 18:18 — "예금신규 TOP 10 지점"
"measures": [{"term": "예금신규", "agg_function": "COUNT", "confidence": "HIGH",
              "note": "예금 신규 개설 건수(가입 건수)를 의미함"}]
"ambiguities": []  // ← 비어있음!
```

LLM이 "예금신규"를 "신규 개설 건수"로 자체 확정하고, confidence: HIGH로 설정하고,
ambiguities에 넣지 않았음. "건수인지 금액인지"는 사용자에게 물어봐야 하는 모호성임.

**구조적 원인 3가지:**

1. **rewritten_query 규칙이 자체 확정을 유도**: 프롬프트에 "모호성을 최대한 해소하여 재작성"이라고 지시.
   LLM이 rewritten_query에서 "예금 신규 개설 건수가 많은 순서대로"로 풀어쓰면서 이미 의미를 확정.
   이후 measures와 ambiguities는 rewritten_query 기반으로 채우므로 모호성이 사라짐.

2. **confidence 필드가 자체 확정을 강화**: "HIGH|MEDIUM|LOW" 선택지에서 LLM이 HIGH를 채우면
   ambiguities에 넣을 이유가 없어짐. 그런데 이 confidence는 downstream에서 아무도 참조하지 않음
   (planner, scorer, explorer 어디서도 안 씀). 순수 장식 필드.

3. **"빈 슬롯 금지" 규칙이 추측을 유도**: "빈 배열 []이라도 적절한 기본값으로 포함시키세요"가
   LLM에게 "비어있으면 안 된다, 채워라"로 작용. ambiguities를 빈 배열로 두려는 경향 강화.

### 원인 2: 정규화 직후 명확화 경로 부재

**현재 그래프:**
```python
workflow.add_edge("normalize_query", "reason_plan")  # 무조건 직행!
```

ambiguities가 있어도 reason_plan으로 직행. 명확화 질문으로 가려면:
normalize → plan → explore → evaluate → ASK_USER → finalize → clarify (불필요한 탐색 1회)

### 원인 3: readiness 점수가 75% 임계값에 도달 못함

```
term_resolution (50%): knowledge_items confirmed 비율 낮음 (29~58%)
use_case_match  (30%): Qdrant 유사도 점수 의존, 테스트 데이터 커버리지 부족
join_path       (20%): 다중 테이블인데 confirmed_join_path 없음 → 0점
→ 합계 29~67% < THRESHOLD_GENERATE(75%)
```

### 원인 4: all_critical_confirmed()가 과도하게 블로킹

readiness 72%(18:18 trace)에서도 critical 항목 하나 때문에 GENERATE 불가.
"시도→검증→복구"가 더 효율적인데, 시도 자체를 차단.

### 원인 5: replan이 실질적 개선을 못함

3회 replan 동안 점수 변화 없음 (29→29→29→29 사례).
recovery_planner가 유사한 가설을 반복 생성. 동일 탐색 반복.

### 원인 6: 소형 LLM의 batch_interpret 품질 한계

gemini-flash-lite가 테이블 메타 해석 시 confidence를 낮게 설정하거나 조인 경로를 추론 못함.

---

## 개선안

### 개선 1: normalizer 프롬프트 재설계 — "모호성을 드러내고 가는" 컨셉

**목표:** 정규화의 역할을 "사용자 질의의 모호성을 모두 드러내고, 확정 가능한 것만 확정하여 넘긴다"로 재정의.

**위치:** `resources/prompts/interpret/query_normalizer_phase1_system.txt`

**변경 사항:**

#### 1-1. confidence 필드 제거

```
AS-IS: measures[].confidence: "HIGH | MEDIUM | LOW"
       entities[].confidence: "HIGH | MEDIUM | LOW"
       output_hint.confidence: "HIGH | MEDIUM | LOW"

TO-BE: 해당 필드 삭제. 모호하면 ambiguities에만 기재.
```

이유: downstream에서 참조하는 곳이 없고, LLM의 자체 확정을 강화하는 부작용만 있음.

#### 1-2. rewritten_query 규칙 변경

```
AS-IS: "모호성을 최대한 해소하여 재작성한 명확한 한국어 질의"
TO-BE: "확정 가능한 부분만 재작성하고, 모호한 부분은 원문 그대로 유지한 한국어 질의.
        '~로 해석', '~를 의미함' 같은 추측을 포함하지 마라."
```

#### 1-3. "빈 슬롯 금지" 규칙 수정

```
AS-IS: "빈 슬롯이라도 빈 배열 [] 또는 적절한 기본값으로 포함시키세요."
TO-BE: "빈 슬롯은 빈 배열 [] 또는 null로 유지하라.
        확신이 없는 값을 추측으로 채우지 마라."
```

#### 1-4. "확신 없으면 note에 기재" 규칙 삭제

```
AS-IS: "확신이 없는 항목은 confidence를 LOW로 설정하고 note에 이유를 기재하세요."
TO-BE: 삭제. "모르겠다"의 출구를 ambiguities 하나로 통일.
        note는 확정된 사실의 보충 설명에만 사용.
```

#### 1-5. ambiguities 판단 체크리스트 추가 (금융 도메인 특화)

```
[ambiguities 판단 체크리스트]
다음 중 하나라도 해당하면 반드시 ambiguities에 기재하라:

□ 동일 용어가 2가지 이상 해석 가능
  예: "신규" → 신규 개설 건수 / 신규 유입 금액 / 신규 가입 고객 수
  예: "이체" → 이체 건수 / 이체 금액
  예: "잔액" → 평균잔액 / 기말잔액 / 최저잔액

□ 집계 기준이 불명확
  예: "상위" → 금액 기준 상위 / 건수 기준 상위
  예: "많은" → 금액이 많은 / 건수가 많은

□ 기간 조건이 2가지 이상 해석 가능
  예: "최근" → 최근 1개월 / 최근 분기 / 최근 1년
  예: "이번 달" → 당월 1일~오늘 / 당월 1일~말일

□ 대상 범위가 불명확
  예: "고객" → 전체 고객 / 유효 고객 / VIP 고객
  예: "지점" → 본점 포함 / 영업점만
```

#### 1-6. 자체 판단 금지 규칙 추가

```
[절대 규칙]
- measure나 entity의 의미를 추론하여 note에 "~를 의미함", "~로 해석" 같은
  판단을 적지 마라.
- "우선", "기본적으로", "일반적으로", "통상" 같은 가정을 하지 마라.
- 용어의 의미가 1가지로 확정되지 않으면, 반드시 ambiguities에 기재하라.
```

#### 1-7. 입력 의도 모호 few-shot 추가

```
■ 예제 N: 입력 의도 모호
입력: "이번 달 예금 신규 현황 알려줘"

출력:
{
  "measures": [{"term": "예금신규", "agg_function": null, "note": null}],
  "ambiguities": [
    "예금 신규의 기준이 '신규 개설 건수'인지 '신규 유입 금액'인지 확인 필요",
    "'현황'이 요약 통계인지 상세 목록인지 확인 필요"
  ]
}
```

### 개선 2: 정규화 직후 명확화 분기 추가

**위치:** `src/agents/graph/pipeline.py`

**현재:**
```python
workflow.add_edge("normalize_query", "reason_plan")  # 무조건 직행
```

**변경:**
```python
workflow.add_conditional_edges(
    "normalize_query",
    _route_after_normalize,
    {
        "clarify": "clarify",
        "reason_plan": "reason_plan",
    },
)

def _route_after_normalize(state: PipelineState) -> str:
    """정규화 후 라우팅 — ambiguities가 있으면 즉시 명확화."""
    nq = state.normalized_query
    if nq and hasattr(nq, "ambiguities") and nq.ambiguities:
        if state.clarification_turns < CLARIFICATION_MAX_TURNS:
            return "clarify"
    return "reason_plan"
```

**효과:** 불필요한 탐색 비용 없이 즉시 명확화 질문으로 전환.

**변경 후 흐름:**
```
normalize_query
  ├→ ambiguities 있음 → clarify → END (사용자에게 질문)
  └→ ambiguities 없음 → reason_plan (탐색 진행)
```

### 개선 3: planner에서 ambiguity → CONFLICTED 설정

**위치:** `src/agents/nodes/reason/planner.py` line 233-238

개선 2가 동작하면 ambiguities가 있는 질의는 planner에 도달하지 않음.
하지만 **안전망**으로 planner에서도 ambiguity를 CONFLICTED로 설정:

```python
# AS-IS (line 237)
status="UNRESOLVED"

# TO-BE
status="CONFLICTED"
```

+ `is_critical=True` 유지 (기본값).

이렇게 하면 개선 2를 우회해서 planner에 도달하더라도
evaluate_readiness → has_conflicted_items() → ASK_USER 경로로 명확화 가능.

### 개선 4: readiness 임계값 + 점수 체계 조정

**위치:** `src/services/confidence_scorer.py`

```python
# 임계값 완화
THRESHOLD_GENERATE = 0.65   # 75% → 65%
THRESHOLD_REPLAN = 0.25     # 30% → 25%

# 가중치 재조정
# AS-IS: term_resolution(50%), use_case_match(30%), join_path(20%)
# TO-BE: term_resolution(55%), table_coverage(25%), join_path(20%)
#
# use_case_match → table_coverage (후보 테이블 중 메타 확인된 비율)
# → 유사 SQL 이력 부족해도 테이블 메타가 충분하면 진행 가능

# 세부 조정
# - confidence 기준: 0.8 → 0.7 (소형 모델 confidence 범위 고려)
# - 조인 미확정 시: 0.0 → 0.3 (완전 블로킹 방지, 시도는 허용)
```

### 개선 5: all_critical_confirmed 완화

**위치:** `src/services/confidence_scorer.py`

```python
def evaluate_readiness(reason):
    score = calculate_readiness(reason)

    if score >= THRESHOLD_GENERATE:
        if all_critical_confirmed(reason):
            return ReadinessVerdict.GENERATE
        # score 70% 이상이면 critical 미확정이어도 '도전적 생성' 허용
        if score >= 0.70:
            return ReadinessVerdict.GENERATE
```

**효과:** "시도→검증→복구"가 "영원히 탐색→실패"보다 나음.

### 개선 6: replan 실효성 강화

**위치:** `src/agents/nodes/reason/recovery_planner.py`, `confidence_evaluator.py`

1. **금지 목록**: dead_end 기반으로 이미 시도한 접근법 명시적 금지
2. **강제 생성**: 2회 replan 후 점수 40% 이상이면 GENERATE 강제

```python
# confidence_evaluator에서
if reason.loop_guard.replan_count >= 2:
    score = calculate_readiness(reason)
    if score >= 0.40:
        return ReadinessVerdict.GENERATE  # 강제 SQL 생성 시도
```

### 개선 7: batch_interpret 프롬프트 소형 모델 최적화 (P2)

- JSON 구조 단순화 (nested → flat)
- few-shot 예시 추가
- 조인 경로 추론을 별도 프롬프트로 분리

---

## LLM vs Rule-based 판단 재정의

| 판단 | 현재 | 개선 | 이유 |
|------|------|------|------|
| **용어 모호성 감지** | LLM (normalizer) | LLM + 체크리스트 + 자체 확정 금지 | LLM이 자체 해석하면 놓침 |
| **명확화 필요 여부** | Rule (CONFLICTED 체크, 탐색 후) | **정규화 직후 분기** + Rule 안전망 | 불필요한 탐색 방지 |
| **readiness 점수** | Rule (scorer) | Rule 유지 + 임계값/가중치 조정 | 예측 가능성 유지 |
| **SQL 생성 시도 여부** | Rule (all_critical) | Rule 완화 (도전적 생성) | 시도→검증→복구가 더 효율적 |
| **테이블 선택** | LLM (batch_interpret) | LLM 유지 (프롬프트 개선) | 메타 해석은 LLM이 우위 |
| **replan 방향** | LLM (recovery_planner) | LLM + 금지 목록 + 강제 생성 | 반복 방지 |

---

## 구현 우선순위

| 순서 | 개선 | 변경 파일 | 난이도 | 효과 |
|------|------|----------|--------|------|
| **P0** | 1. normalizer 프롬프트 재설계 | `query_normalizer_phase1_system.txt`, `phase2_system.txt` | 중 | 모호성 감지 근본 해결 |
| **P0** | 2. 정규화 직후 명확화 분기 | `pipeline.py` | 하 | 불필요한 탐색 제거 |
| **P0** | 3. planner ambiguity→CONFLICTED | `planner.py` | 하 | 안전망 |
| **P0** | 4. query_normalizer.py 파싱 수정 | `query_normalizer.py` | 하 | confidence 제거 대응 |
| **P1** | 5. readiness 임계값 + 가중치 | `confidence_scorer.py` | 하 | 점수 도달 가능성 향상 |
| **P1** | 6. all_critical 완화 | `confidence_scorer.py` | 하 | 도전적 생성 허용 |
| **P1** | 7. replan 강제 생성 | `confidence_evaluator.py` | 하 | 반복 루프 탈출 |
| **P2** | 8. batch_interpret 최적화 | 프롬프트 파일 | 중 | 소형 모델 정확도 |

**예상 결과:**
- **08:00 (29%):** 개선 1+2로 "예금신규" ambiguity 감지 → 즉시 명확화 질문
- **18:18 (72%):** 개선 5로 임계값 65% → GENERATE 진입 → SQL 시도
- **18:35 (58%):** 개선 6+7로 도전적 생성 → SQL 시도 → validate에서 검증
