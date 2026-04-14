# Enum PERCENTAGE 슬롯 간 오염(cross-slot contamination) 분석 및 수정 설계

- 작성일: 2026-04-12
- 상태: 분석 완료(prompt-engineer 스킬 1차 리뷰 반영), 미적용
- 관련 파일:
  - [resources/prompts/interpret/query_normalizer_phase1_system.txt](../../resources/prompts/interpret/query_normalizer_phase1_system.txt)
  - [src/agents/models/normalization.py](../../src/agents/models/normalization.py)
  - [src/services/query_normalizer.py](../../src/services/query_normalizer.py)

## 1. 증상

```
2026-04-12 00:33:18.905 [error] Enum 검증 실패
  field=measures[1].agg
  query_id=01643744
  value=PERCENTAGE
```

query_normalizer phase1이 `measures[1].agg_function` 슬롯에 `PERCENTAGE`를 출력했는데,
이는 `AggFunction` enum의 허용값이 아니다. `_validate_enum`은 경고를 남기고 None으로
덮어써 후속 슬롯이 비정상 상태가 된다.

> **로그 원문과 스키마 용어 대응**: 로그는 `field=measures[1].agg`로 찍히는데, 이는
> [src/services/query_normalizer.py:204](../../src/services/query_normalizer.py#L204)에서
> `agg_function` 슬롯 검증 시 사용하는 축약 라벨이다. Pydantic 모델의 실제 필드명은
> [src/agents/models/normalization.py:276](../../src/agents/models/normalization.py#L276)
> `agg_function: str = "UNKNOWN"`. 본 문서 전반에서 `agg_function`이라고 지칭한 대상은
> 로그 원문 `measures[i].agg`와 동일 슬롯이다.

## 2. 구조적 원인

### (가) PERCENTAGE는 다른 슬롯에서 "유효한 값"이다

[src/agents/models/normalization.py](../../src/agents/models/normalization.py) 기준:

- line 82 `AggFunction`: SUM, AVG, COUNT, COUNT_DISTINCT, MAX, MIN, NONE, UNKNOWN
- line 225 `ModifierType`: SORT, LIMIT, RANK, RATIO, DELTA, DELTA_RATE, CUMULATIVE,
  MOVING_AVG, **PERCENTAGE**

즉 PERCENTAGE는 MODIFIER.type 슬롯에는 유효하지만 AggFunction 슬롯에는 유효하지 않은
값이다. Qwen3.5 MoE는 프롬프트 내 enum 허용값을 **슬롯 경계를 넘어 전역 어휘집처럼
취급**하는 경향이 있다. 이것이 "슬롯 간 오염(cross-slot contamination)"이다.

### (나) 프롬프트 phase1_system.txt의 3가지 구조 결함

[resources/prompts/interpret/query_normalizer_phase1_system.txt](../../resources/prompts/interpret/query_normalizer_phase1_system.txt)
기준:

1. **HARD_CONSTRAINTS 5의 허점** (line 17)
   > "모든 enum 필드는 각 슬롯 정의에 명시된 허용값 중에서만 선택한다"

   "각 슬롯 정의에 명시된"이라는 수식어가 있지만, "다른 슬롯의 허용값을 복사하지 말라"는
   적극적 금지가 없다. 모델은 규칙을 "존재하는 값이면 된다"로 오해한다.

2. **agg_function의 의미 경계가 positive form으로 진술되지 않음** (line 118)
   > `agg_function: SUM | AVG | COUNT | COUNT_DISTINCT | MAX | MIN | NONE | UNKNOWN`

   이 슬롯이 "SQL 집계 함수 전용"이고 "비율/퍼센트 연산은 MODIFIER로 간다"는 의미
   경계가 없다. 모델은 "값이 나열되어 있으면 슬롯의 정의가 끝난다"고 해석한다.

3. **RATIO/PERCENTAGE 케이스 few-shot 예시 부재**

   기존 4개 few-shot은 모두 단순 COUNT/SUM 집계 위주. 연체율·증가율·변화율 같이
   RATIO + DELTA_RATE가 결합된 케이스가 한 건도 없다. Qwen3.5는 "이 상황에서 이 슬롯은
   어떻게 채우는가"를 예시에서 학습하는 경향이 강해, 예시 공백이 오염을 유도한다.

## 3. 수정 설계 (prompt-engineer 스킬 1차 리뷰 결과)

### Patch 1 — HARD_CONSTRAINTS 5 정밀화 (3줄 확장, §3.7 + 분류 서술 예외)

기존 ([query_normalizer_phase1_system.txt:17](../../resources/prompts/interpret/query_normalizer_phase1_system.txt#L17)):
```
5. 모든 enum 필드는 각 슬롯 정의에 명시된 허용값 중에서만 선택한다.
```

수정:
```
5. 각 enum 필드는 그 슬롯 정의 바로 아래 나열된 허용값 중에서만 값을 고른다.
   값을 고를 때는 해당 슬롯 바로 아래의 목록만 참조한다.
   예: agg_function을 고를 때 modifiers[].type 아래의 PERCENTAGE·DELTA_RATE는 후보가 아니다.
```

3줄 확장의 역할 분리:

- **1줄**: 원래 일반 규칙 유지 (positive form)
- **2줄**: 동사형 "참조한다"로 행동 지시. "독립"/"영향"/"본 슬롯" 같은 추상 메타포 제거 (§3.11)
- **3줄**: 실제 버그 패턴을 구체 앵커로 박음. §3.10 "추상 라벨 경계 판단은 구체 예시 필수"에 해당. "후보가 아니다"는 **분류 서술**(slot의 타입 경계 정의)이지 행동 금지가 아니므로 §3.7 예외 2 허용 범위

§3.12 체크: 슬롯 간 오염은 [src/services/query_normalizer.py:131-137](../../src/services/query_normalizer.py#L131-L137)의
`_validate_enum`이 정적 탐지하는 형식 규약이므로 HARD_CONSTRAINTS 배치가 정당.

> **사전 상태 주의 (§3.3)**: `[HARD_CONSTRAINTS]` 블록은 이미 6개 규칙으로 §3.3의
> 카테고리당 5개 상한을 초과한 상태다. 본 Patch는 규칙 5에 하위 설명 2줄을 덧붙여
> 번호 개수는 유지하므로 회귀는 없지만, 향후 블록 분리(`[FORMAT_HARD]` / `[SCHEMA_HARD]`
> 등)로 상한을 복원하는 작업이 별도 과제로 남는다.

### Patch 2 — MEASURE 섹션 슬롯 경계 진술 (§3.10 풀어 쓰기, 스키마 준수)

**중요**: 현재 MEASURE 슬롯 스키마는 `term / measure_type / agg_function /
normalized_term / note` **5개 필드**뿐이다
([query_normalizer_phase1_system.txt:115-120](../../resources/prompts/interpret/query_normalizer_phase1_system.txt#L115-L120)).
분자/분모 참조(`numerator_ref` 등) 같은 신규 필드는 본 Patch 범위에 포함하지 않는다.
스키마 확장은 별도 설계 과제로 분리한다.

MEASURE 슬롯의 "추출 규칙 1" 바로 앞에 다음 `### 슬롯 경계` 소블록을 추가한다.

```
### 슬롯 경계 — 집계 vs 파생 vs 결과 가공
- agg_function은 열 값을 하나로 합치는 SQL 집계 함수만 담는다.
  허용값: SUM / AVG / COUNT / COUNT_DISTINCT / MAX / MIN / NONE / UNKNOWN
- 비율·퍼센트 성격 지표(연체율, 점유율, 비중 등)는 measure_type=RATIO로 분류하고,
  agg_function은 NONE으로 둔다. 산출식은 note에 자연어로 기재한다.
  예: term="연체율", measure_type="RATIO", agg_function="NONE",
      note="연체대출잔액 합계 / 총대출잔액 합계"
- 증감/변화/성장률 같은 결과 가공은 measures가 아니라 modifiers로 표현한다.
  - 건수·금액의 증감 → modifiers[].type=DELTA
  - 비율의 증감(연체율 변화 등) → modifiers[].type=DELTA_RATE
  - 누적 → modifiers[].type=CUMULATIVE
- "PERCENTAGE"는 modifiers[].type 슬롯의 값이며 agg_function 슬롯의 값이 아니다.
```

- Positive form(§3.7): "어디에 넣는가"만 기술.
- 마지막 줄 1건은 §3.7 예외(구체 예시 교정) — 오염 패턴을 명시적으로 끊기 위한 최소 negative.
- 추상 라벨 경계 판정이므로 §3.10에 따라 각 라벨 아래 구체 판별 기준과 1개 이상의 예를 붙여 풀어 쓴다.
- 기존 추출 규칙 1(DIMENSION GROUP 조건)은 변경하지 않고 그 위에 경계 블록을 선행 배치(§12.1 상류 배치 원칙).

### Patch 3 — Few-shot 예제 5 추가 (RATIO + COMPARE 케이스, 스키마 준수)

쿼리: "작년 동기 대비 영업점별 연체율 변화 알려줘"

기존 예제와 동일한 최상위 스키마
([query_normalizer_phase1_system.txt:510-523](../../resources/prompts/interpret/query_normalizer_phase1_system.txt#L510-L523))를
그대로 따른다. MEASURE는 단일 원소, 스키마 확장 없음.

```
## 예제 5: 비율 지표 + 시계열 비교 (COMPARE + RATIO)
입력: "작년 동기 대비 영업점별 연체율 변화 알려줘"

출력:
{
  "original_query": "작년 동기 대비 영업점별 연체율 변화 알려줘",
  "rewritten_query": "작년 동기(작년 동일월)와 이번 달을 비교하여 영업점별 연체율의 변화를 조회한다",
  "intent": {
    "primary": "COMPARE",
    "secondary": ["AGGREGATE"]
  },
  "entities": [
    {"term": "영업점", "type": "DIRECT", "normalized_term": "지점", "note": "영업점 = 지점"},
    {"term": "여신", "type": "IMPLIED", "normalized_term": "대출", "note": "연체율 산출을 위해 여신 데이터 필요"}
  ],
  "measures": [
    {"term": "연체율", "measure_type": "RATIO", "agg_function": "NONE", "normalized_term": null, "note": "연체대출잔액 합계 / 총대출잔액 합계"}
  ],
  "dimensions": [
    {"term": "영업점", "role": "GROUP", "granularity": "INDIVIDUAL", "normalized_term": "지점", "is_time_dimension": false, "note": null}
  ],
  "filters": [],
  "time": {
    "type": "COMPARISON",
    "base_period": {"label": "이번 달", "resolve": "THIS_MONTH", "n": null, "absolute_start": null, "absolute_end": null},
    "compare_period": {"label": "작년 동기", "resolve": "LAST_YEAR", "n": null, "absolute_start": null, "absolute_end": null}
  },
  "modifiers": [
    {"type": "DELTA_RATE", "direction": null, "limit": null, "by": "연체율", "note": "작년 동기 대비 연체율 변화율"}
  ],
  "output_hint": {
    "format": "COMPARISON",
    "doc_type": null,
    "expected_columns": [],
    "note": "연체율 시계열 비교"
  },
  "ambiguities": [
    {
      "ambiguity_type": "INTENT",
      "confidence": "MEDIUM",
      "question": "'작년 동기'의 의미를 확인해 주시겠어요?",
      "question_type": "single_select",
      "options": ["작년 동일월 (예: 2025년 4월 ↔ 2026년 4월)", "작년 누적 같은 시점 (YTD)"],
      "inferred_value": "작년 동일월",
      "reasoning": "'동기'는 은행 업무에서 통상 작년 동일월을 의미하는 것이 일반적"
    }
  ],
  "search_keywords": {
    "meta_search": ["영업점", "지점", "연체율", "연체대출", "여신", "총대출"],
    "vector_search": "작년 동기 대비 영업점별 연체율 변화 비교"
  }
}
```

**핵심 학습 신호**:
- 비율 지표(연체율)는 단일 MEASURE 원소, `measure_type=RATIO`, `agg_function=NONE`
- 산출식은 `note`에 자연어로만 기술한다(스키마 필드 신설 금지)
- "변화" 개념은 measures가 아니라 `modifiers[].type=DELTA_RATE`로 간다
- `PERCENTAGE`는 이 예제 어디에도 등장하지 않는다 → agg_function 슬롯 후보 아님을 암묵 학습

### Patch 3의 배치 순서 — §8.4 recency bias 판단

skill §8.4는 "가장 대표적/복잡한 실전 패턴을 마지막에"를 원칙으로 한다.

- **기존 예제 4 (예금 신규 현황)**: 강점은 `ambiguities`를 2건 기재하는 모호성 처리.
  슬롯 복잡도 자체는 낮다(measures 1, dimensions 0, modifiers 0).
- **신규 예제 5 (연체율 변화)**: `intent=COMPARE+AGGREGATE`, `measure_type=RATIO`,
  `time=COMPARISON`, `modifiers=DELTA_RATE`, `dimensions=GROUP`이 한 번에 등장하며
  현재 증상의 직접 원인(RATIO·DELTA_RATE 슬롯 구분)을 커버한다.

본 버그 수정 목적상 **예제 5를 마지막에 배치**(예제 4 → 예제 5 순서)한다. 연체율
케이스가 구조적으로 가장 복잡하고, recency bias로 "RATIO는 agg_function=NONE" 패턴이
TASK 직전에 놓여 오염 억제 효과가 최대화된다.

### Few-shot 커버리지 매트릭스 (§8.2)

축 1: 슬롯 복잡도(주로 등장하는 슬롯 조합) × 축 2: 표현 방식(명시 / 암묵·모호)

| 슬롯 조합 ↓ / 표현 → | 주로 명시 | 주로 암묵·용어 모호 |
|---|---|---|
| ENTITY + DIMENSION(DISPLAY) — 단순 목록 | **EX1** 서울 지점 VIP 고객 | — |
| MEASURE(SUM) + DIMENSION(GROUP) + MODIFIER(RANK) | **EX2** 지점별 수신 잔액 상위 10 | — |
| MEASURE(COUNT) + TIME(COMPARISON) + MODIFIER(DELTA) | **EX3** 전월 대비 여신 실행 건수 | — |
| MEASURE(UNKNOWN 집계) + 다중 ambiguities | — | **EX4** 이번 달 예금 신규 현황 |
| **MEASURE(RATIO) + TIME(COMPARISON) + MODIFIER(DELTA_RATE)** | **EX5 신규** 작년 동기 대비 연체율 변화 | — |

- EX5가 덮는 칸은 기존 예제가 전혀 건드리지 못한 영역(RATIO × DELTA_RATE × COMPARISON).
- 어떤 두 예제도 같은 칸을 중복 점유하지 않음 → §8.2의 중복 제거 기준 통과.
- 상한(§8.1): query_normalizer phase1은 5개 상한. 4 → 5로 상한 내에서 증가.

## 4. SELF_CHECK (prompt-engineer 스킬 §17)

- §0 성능 우선: Patch 1은 오류 방지, Patch 2·3은 커버리지 확장이 목적 — OK
- §0.1 3계층 분리: Patch 1~3은 프롬프트 계층. 코드 계층 폴백은 기존 `_validate_enum`
  경고 로그가 담당(중복 방지) — OK
- §3.3 5-rule 상한: HARD_CONSTRAINTS 기존 6개 → Patch 1이 규칙 5에 하위 설명 1줄 추가.
  번호 개수 유지. 6개 초과 상태는 사전 존재하며 별도 분리 과제로 이월 — 조건부 OK
- §3.7 positive form: 신규 문장은 모두 "어디에 넣는가" 형태. 예외 1건(§Patch 2 마지막 줄
  `"PERCENTAGE"는 modifiers[].type 슬롯의 값`)은 오염 패턴 교정 목적의 허용 예외 — OK
- §3.10 풀어 쓰기: MEASURE 슬롯 경계를 각 라벨+예시 쌍으로 전개 — OK
- §3.11 자기설명 용어: "슬롯 경계" 용어는 일반 한국어 조합이며 동사적 의미 명확 — OK
- §3.12 HARD_CONSTRAINTS vs RULES: Patch 1은 `_validate_enum`이 정적 탐지 가능한
  형식 제약이므로 HARD_CONSTRAINTS 유지. Patch 2(의미 판단 기준)는 슬롯 정의 본문에
  배치하여 RULES와의 중복 회피 — OK
- §8.1 개수 상한: phase1은 5개 상한, 4 → 5로 상한 내 — OK
- §8.2 coverage matrix: 본 문서 Patch 3 끝에 매트릭스 명시. EX5가 새 칸 단독 점유 — OK
- §8.4 recency bias: 예제 5를 마지막 슬롯에 배치. 판단 근거 문서화 — OK
- §8.6 negative example 금지: Patch 3의 예시는 성공 JSON 한 건만 포함 — OK
- §11 모듈 간 정합: Patch 3 예시는 기존 OUTPUT_CONTRACT 최상위 키 전체 포함,
  MEASURE 5 필드 준수, 신설 필드 없음 — OK
- §5.2 reasoning_summary: query_normalizer phase1은 thinking OFF이므로 해당 없음 — OK
- §15 금지 패턴: 인프라 파라미터·이모지·구분선·번호 목록 사용 없음 — OK

## 5. 적용 순서

1. Patch 1 (HARD_CONSTRAINTS 정밀화)
2. Patch 2 (MEASURE 슬롯 경계)
3. Patch 3 (Few-shot 예시 5)
4. 3건 적용 후 동일 쿼리로 재실행하여 정확도 확인. 재발 시 `_validate_enum` 경고 로그를
   근거로 프롬프트 재검토
