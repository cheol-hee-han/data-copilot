# Validator 체크 구조 재설계 비판적 리뷰 (Expert A)

> 작성일: 2026-04-13
> 역할: SQL 검증 아키텍처 전문가
> 대상 문서: 20260413-validator-check-restructuring.md

---

## 라운드 1: 체크 구조 재편 비판

### 1.1 체크 1(measure) → 체크 7 흡수의 안전성

**결론: 조건부 안전. 단, RATIO/WINDOW 케이스에서 regression 위험 존재.**

todo 문서의 흡수 근거는 타당한 부분이 있다. 현재 decomposition에서 `measure_type`과 `note`(산출식)가 손실되므로, 체크 1이 실질적으로 검증할 수 있는 범위가 이미 제한적이다. EXTRACT 질의에서는 measures가 빈 배열이라 검증 대상 자체가 없고, RATIO 질의에서는 `agg_function=NONE`이라 "적절한 집계함수" 판정이 불가능하다.

그러나 Qwen3.5가 "논리적 정합성" 하나로 집계 방식까지 정확히 잡을 수 있는가에 대해서는 **회의적**이다:

- **AGGREGATE 질의의 명시적 스캐폴딩 소실**: 현재 체크 1은 `measures=[{term: "건수", agg: "COUNT"}]`를 보고 SQL에 COUNT가 있는지 1:1 대조한다. 이것은 LLM에게 "이 하나만 확인하라"는 명시적 지시다. 체크 7에 흡수되면 이 1:1 대조가 "전체 로직이 맞는가"라는 open-ended 질문 속에 묻힌다.
- **Qwen3.5의 attention 분산**: 체크 7은 이미 기간, 질의 유형, 대상, AI 추론, 코드값, 0건 판정 등 최소 6개 관점을 동시 검증한다. 여기에 "지표/산출 방식"과 "결과 가공/정렬"을 추가하면 8개 관점이 된다. Claude 수준 모델은 이를 처리할 수 있지만, Qwen3.5 397B에서 관점이 늘어날수록 개별 관점의 검증 깊이가 얕아질 가능성이 높다.
- **RATIO 지표 복원 시 흡수 필요성 재평가 필요**: 만약 직렬화 개선으로 `measure_type=RATIO`와 `note(산출식)`가 전달된다면, 체크 1을 독립 유지하는 것이 오히려 더 정확해질 수 있다. 즉, 흡수 결정은 직렬화 선택지와 연동되어야 한다.

**권고**: 체크 1 흡수는 직렬화 개선이 `measure_type`/`note` 복원을 포함하지 않는 경우에만 진행. 복원이 포함되면 독립 유지를 재검토.

### 1.2 체크 4(order_limit) → 체크 7 흡수 시 RANK 케이스 위험

**결론: SORT/LIMIT 케이스는 안전하나, RANK는 위험.**

todo 문서의 흡수 근거 중 "DELTA/DELTA_RATE는 ORDER BY/LIMIT과 무관"이라는 점은 정확하다. 현재 체크 4는 이질적인 modifier 타입들을 하나로 묶고 있어 검증 기준이 모호하다.

그러나 **RANK(상위 N건)는 명확한 구조적 패턴**이다:

- "상위 10개 지점" → `ORDER BY ... DESC LIMIT 10`이 반드시 있어야 한다
- 이것은 체크 7의 "질의 유형 반영"에서 다루기로 되어 있지만, 프롬프트에서 "순위 질의: ORDER BY + LIMIT 구조 필수"라는 규칙은 이미 존재한다
- 문제는 **decomposition에서 RANK modifier의 direction/limit/by가 손실**되므로, 체크 7이 원문만으로 "상위"인지 "하위"인지, 기준 지표가 무엇인지 추론해야 한다는 점이다

현재 프롬프트 예시 5를 보면, `order_limit=[{type: "RANK", value: "10"}]`가 전달되어 체크 4가 `ORDER BY DESC LIMIT 10`을 대조한다. 이 정보가 사라지면 체크 7은 원문 "top 10"에서 추론해야 하는데, 원문이 "상위 10개"처럼 명확한 경우는 괜찮지만 "실적 기준 상위권"처럼 모호한 경우 direction과 기준 지표를 놓칠 수 있다.

**권고**: 흡수 자체는 진행하되, 체크 7 보강 시 RANK/SORT 패턴에 대한 명시적 검증 지시를 추가. decomposition에서 modifier 정보가 개선되면 독립 체크 복원 가능성 유보.

### 1.3 체크 2(filter), 체크 3(group_by) 유지 시 데이터 손실 해결

**결론: 이 두 체크의 유지는 정당하며, 데이터 손실 해결이 재설계의 핵심이다.**

현재 손실되는 정보와 해결 방안:

**체크 2 (filter)**:
- `position(PRE_AGG/POST_AGG)` 손실 → HAVING 조건을 WHERE로 오판
  - **해결**: `_build_decomposition_from_normalized`에 position 추가하거나, 직렬화 선택지 B/C에서 NormalizedQuery.filters를 직접 참조
  - 금융 도메인에서 "지점별 잔액 합계가 100억 이상"은 POST_AGG 필터이며, 이를 WHERE로 판정하면 false positive FAIL 발생
- `note(비즈니스 규칙)` 손실 → IMPLICIT 필터 맥락 소실
  - **해결**: IMPLICIT 필터의 경우 note가 없으면 validator가 "왜 이 조건이 있는지" 판단 불가. 최소한 filter_type=IMPLICIT인 항목에는 note 전달 필수

**체크 3 (group_by)**:
- `PARTITION role` 미전달 → PARTITION BY 검증 불가
  - **해결 방안 A**: group_by에 `{"term": "지점", "role": "GROUP"}` / `{"term": "지점", "role": "PARTITION"}` 형태로 role 포함
  - **해결 방안 B**: PARTITION은 체크 7에 위임 (WINDOW 함수 관련이므로 measure 검증과 연계)
  - **권고**: 방안 A 채택. PARTITION BY 누락은 흔한 오류이며 구조적으로 감지 가능

### 1.4 8개 → 6개 축소의 긍정적/부정적 시나리오

**긍정적 시나리오 (정확도 향상)**:

1. **RATIO 질의**: 현재 체크 1이 `agg_function=NONE`을 보고 "집계함수 없음"으로 잘못 FAIL 판정하는 경우가 있다. 체크 1을 제거하면 이 false positive가 사라진다.
2. **DELTA/CUMULATIVE 질의**: 현재 체크 4가 `order_limit=[{type: "DELTA", value: ""}]`를 보고 ORDER BY가 없다고 FAIL 판정하는 경우가 있다. DELTA는 ORDER BY와 무관하므로 이 false positive가 사라진다.
3. **Qwen3.5 JSON 출력 안정성**: 9개 체크를 JSON으로 출력하면 구조가 복잡해져 JSON 파싱 실패 확률이 올라간다. 7개(6개 + 통합된 체크 7)로 줄이면 출력 안정성 개선.

**부정적 시나리오 (정확도 하락)**:

1. **단순 AGGREGATE 질의**: "지점별 대출 건수"에서 COUNT가 SUM으로 잘못 생성된 경우, 현재는 체크 1이 `agg=COUNT`와 SQL의 SUM을 대조하여 즉시 감지. 체크 7로 흡수되면 이 단순 불일치를 "전체 로직" 관점에서 놓칠 수 있다.
2. **정렬 방향 오류**: "하위 10건"인데 DESC로 정렬된 경우, 현재 체크 4가 명시적 대조로 감지 가능. 흡수 후에는 체크 7이 원문에서 "하위"를 파악해야 하는데, Qwen3.5가 "상위"와 혼동할 가능성이 있다.
3. **체크 7의 과부하**: 관점이 너무 많아지면 LLM이 "전반적으로 괜찮다"는 피상적 판단으로 흐를 위험. 특히 Qwen3.5는 instruction following에서 Claude 대비 약하므로 개별 관점을 빠짐없이 확인하기 어렵다.

### 1.5 대안적 체크 구조

**3개 축소안 (비권고)**:

```
체크 1: 구조적 정합성 (filter + group_by + measure + order_limit 통합)
체크 2: 논리적 정합성 (원문 의도 대조 + 코드값 + AI 추론)
체크 3: 실행 검증 (미확인 값 + dead_end + DB 실행 + 코드 명칭)
```

- 장점: 토큰 절감 극대화, JSON 출력 단순화
- 단점: 각 체크가 너무 많은 관점을 포함하여 FAIL 시 어떤 관점이 문제인지 fix_instruction이 모호해진다. failure_classification 정확도 급락.
- **비권고 이유**: fix_instruction의 구체성이 local_fix 성공률에 직결된다. 3개로 축소하면 "무엇을 고쳐야 하는지" 특정이 어렵다.

**10개 확장안 (비권고)**:

```
현재 9개 + 시간 조건 전용 체크
```

- 장점: 시간 조건 오류는 금융 도메인에서 매우 흔한 오류이며 전용 체크로 분리하면 감지율 향상
- 단점: Qwen3.5 토큰 효율 악화, JSON 출력 복잡도 증가, 유지보수 비용 증가
- **비권고 이유**: 시간 조건은 체크 7의 "기간 반영"에서 이미 상세히 다루고 있으며, 체크 2의 filter에서도 기간 필터로 검증됨. 중복 체크가 될 가능성이 높다.

**7개 권고안 (아래 라운드 4에서 상세 기술)**:

현재 9개에서 체크 4(order_limit)만 체크 7에 흡수하고, 체크 1(measure)은 직렬화 개선 결과에 따라 결정하는 점진적 접근.

---

## 라운드 2: 직렬화 방식 비판

### 2.1 선택지별 장단점과 구현 복잡도

#### 선택지 A: serialize_decomp_slots 가독성 개선

**장점**:
- 영향 범위 최소: `prompt.py`의 직렬화 포맷 변경 + `reasoning_preparer.py`에 누락 필드 추가
- 기존 아키텍처(decomposition → 직렬화 → 플레이스홀더 치환) 유지
- sql_generator 등 다른 소비자에도 자동 적용

**단점**:
- `_build_decomposition_from_normalized`의 근본 문제(중간 dict로의 변환 시 정보 손실)가 잔존
- 필드를 추가할수록 decomposition dict가 NormalizedQuery와 거의 동일해져 "왜 변환하는가"라는 의문 발생
- 가독성 개선만으로는 `null`, 빈 배열 등 노이즈 제거에 한계

**구현 복잡도**: 낮음 (1~2일)

#### 선택지 B: validator 전용 직렬화

**장점**:
- validator에 최적화된 정보만 전달 — 불필요한 필드(confidence, normalized_term 등) 제거
- NormalizedQuery에서 직접 읽으므로 정보 손실 없음
- 체크별로 필요한 정보를 정확하게 제어 가능

**단점**:
- sql_validator.py에 전용 직렬화 함수 추가 → validator와 데이터 모델 간 커플링 증가
- NormalizedQuery 스키마 변경 시 validator 직렬화도 함께 수정 필요
- sql_generator, recovery_agent 등 다른 소비자는 여전히 기존 decomposition 사용 → 이중 경로

**구현 복잡도**: 중간 (2~3일)

#### 선택지 C: decomposition 폐기, NormalizedQuery 요약 블록

**장점**:
- 중간 변환 계층(decomposition) 완전 제거 → 단일 진실 소스(NormalizedQuery)
- 프롬프트에 하나의 구조화된 블록으로 전달 → LLM이 전체 맥락을 한눈에 파악
- 유지보수 비용 최소화: NormalizedQuery 변경만 추적하면 됨

**단점**:
- NormalizedQuery 전체를 넣으면 토큰 과다 (8슬롯 전체 = 상당한 크기)
- "요약 블록"의 설계가 핵심인데, 요약 수준에 따라 다시 정보 손실 발생 가능
- 체크 2(filter), 체크 3(group_by)이 참조할 데이터를 어떻게 분리 제공할지 설계 복잡
- sql_generator 등도 decomposition을 참조하므로 일괄 전환 필요 → 영향 범위 대폭 확대

**구현 복잡도**: 높음 (4~5일, 테스트 포함)

### 2.2 Qwen3.5 토큰 효율성 관점

**최적안: 선택지 B (validator 전용 직렬화)**

근거:
- Qwen3.5에서는 토큰당 정보 밀도가 중요하다. 불필요한 필드(confidence, normalized_term 등)를 제거하고 체크에 직접 필요한 정보만 전달하면 토큰 대비 검증 정확도가 높아진다.
- 선택지 C의 "요약 블록"은 설계에 따라 A보다 토큰 효율적일 수 있지만, 요약 과정에서 정보가 왜곡될 위험이 있다.
- 선택지 A는 가독성 개선으로 Qwen3.5의 해석 정확도를 높이지만, 불필요한 필드까지 전달되므로 토큰 효율은 B보다 낮다.

구체적 비교 (예: filter 슬롯 하나):

```
# 현재 (json.dumps)
[{"term": "기간", "operator": "EQUALS", "value": ["이번 달"]}]

# 선택지 A (가독성 개선)
- 필터: 기간 = 이번 달 (연산자: EQUALS)

# 선택지 B (전용 직렬화)
- 기간: 이번 달 [WHERE] — 연산자: EQUALS
  (PRE_AGG 필터, note: 없음)

# 선택지 C (NQ 요약 블록)
[필터 조건]
- target: 기간, type: EQUALS, position: PRE_AGG, values: [이번 달]
```

선택지 B가 체크 2에 필요한 `position` 정보를 포함하면서도 불필요한 `confidence` 등을 제거하여 가장 효율적이다.

### 2.3 유지보수 관점

**최적안: 선택지 A (가독성 개선) 또는 B (전용 직렬화)**

- 선택지 C는 decomposition 전체를 폐기하므로 sql_generator, recovery_agent 등 모든 소비자를 동시에 마이그레이션해야 한다. 이는 유지보수 관점에서 일회성 비용이 크고 regression 위험이 높다.
- 선택지 A는 기존 구조를 유지하면서 개선하므로 안전하지만, decomposition이라는 중간 계층의 존재 이유가 점점 희박해지는 기술적 부채를 축적한다.
- 선택지 B는 validator에만 전용 경로를 추가하므로 영향 범위가 제한적이면서도 정보 손실을 해결한다. 다만 "이중 경로" 문제가 있다.

### 2.4 제안되지 않은 대안

**선택지 D: 하이브리드 — 선택지 A + validator 보조 블록**

```
기존 decomposition 가독성 개선 (선택지 A) + 
validator 전용으로 NormalizedQuery에서 체크 2/3 보조 정보만 추출
```

구체적으로:
1. `serialize_decomp_slots()`를 가독성 좋은 텍스트 포맷으로 변경 (선택지 A)
2. `_build_decomposition_from_normalized()`에 `position`, `note`, `role` 추가
3. validator 프롬프트에 `{filter_detail}`, `{grouping_detail}` 플레이스홀더를 추가하여, NormalizedQuery에서 직접 추출한 보조 정보를 별도 제공

이 접근의 장점:
- 기존 아키텍처를 유지하면서 validator에 필요한 정보만 보강
- sql_generator 등 다른 소비자에도 개선된 decomposition 혜택
- 점진적 마이그레이션 가능: 나중에 decomposition을 완전히 폐기하더라도 보조 블록은 재사용

---

## 라운드 3: 위험 분석

### 3.1 검증 정확도가 오히려 떨어질 수 있는 시나리오

**시나리오 1: 단순 집계함수 불일치 감지 실패**

사용자: "고객 수" (COUNT 기대)
SQL: `SELECT SUM(CST_CNT)` (SUM 생성)
- 현재: 체크 1이 `measures=[{term: "고객 수", agg: "COUNT"}]`와 SQL의 SUM을 대조 → 즉시 FAIL
- 재설계 후: 체크 7이 "원문의 '고객 수'가 SQL에 올바르게 반영되었는가"를 판단 → "SUM(CST_CNT)로 고객 수를 산출하고 있으며 의도에 부합"이라고 잘못 판단할 가능성

**시나리오 2: 체크 7 과부하로 인한 전반적 검증 깊이 저하**

기존에 독립 체크로 분산되던 검증 관점이 체크 7에 집중되면, Qwen3.5가 프롬프트의 모든 확인 포인트를 빠짐없이 수행하지 못할 수 있다. 특히 SQL이 복잡한 경우(서브쿼리, CTE, 윈도우 함수 조합) 체크 7의 토큰 예산 내에서 모든 관점을 소화하기 어렵다.

**시나리오 3: fix_instruction 구체성 저하**

체크 1의 FAIL: `"SUM이 사용되었으나 COUNT가 필요"` → 명확한 수정 지시
체크 7의 FAIL: `"집계 방식이 의도와 다름"` → 모호한 수정 지시
sql_generator가 local_fix를 시도할 때, 모호한 fix_instruction으로 인해 같은 실수를 반복하거나 다른 부분을 잘못 수정할 위험.

### 3.2 불필요한 재시도 증감 케이스

**재시도 감소 (긍정적)**:
- RATIO 질의에서 현재 체크 1이 `agg_function=NONE`을 보고 false FAIL → local_fix 시도 → 원래 맞는 SQL을 변경 → 악화. 체크 1 제거로 이 재시도 루프 제거.
- DELTA 질의에서 현재 체크 4가 ORDER BY 부재를 false FAIL → 불필요한 재시도. 체크 4 제거로 해결.
- 추정 영향: false positive 기반 재시도가 10~20% 감소 (RATIO/DELTA/CUMULATIVE 질의 비중에 비례)

**재시도 증가 (부정적)**:
- 체크 7이 과부하로 진짜 오류를 놓쳐 PASS 판정 → SQL 실행 → 잘못된 결과 전달 → 사용자 불신. 이 경우 재시도는 "증가"가 아니라 "감지 자체를 못함"이 문제.
- 다만 이는 체크 8(DB 실행)과 체크 9(코드 명칭)가 여전히 독립적으로 구조적 오류를 잡으므로 완전한 누락은 드물다.

### 3.3 failure_classification 정확도 영향

현재 failure_classification은 LLM이 전체 체크 결과를 보고 판정한다. 체크 수 축소의 영향:

**local_fix 정확도**: 개별 체크가 구체적일수록 "이 부분만 고치면 된다"는 판단이 쉽다. 체크 1이 "COUNT를 SUM으로 변경"이라고 명시하면 local_fix가 자연스럽다. 체크 7이 "전체 로직이 안 맞는다"고 하면 structural로 오분류될 수 있다.

**structural 정확도**: 큰 영향 없음. structural 판정은 주로 체크 5(미확인 값), 체크 6(dead_end)에 의존하며 이들은 유지된다.

**권고**: 체크 7 보강 시 fix_instruction 생성 지시를 구체화. "FAIL인 경우, 어떤 관점(지표/가공/정렬/기간/대상/추론)에서 불일치인지 명시하고 수정 방법을 구체적으로 기재하라"는 지시 추가.

### 3.4 마이그레이션 리스크: 하류 코드 영향

코드 검색 결과, 체크 키 이름에 직접 의존하는 하류 코드는 2곳:

1. **`src/services/insight_builder.py`** (509~517행, 1031~1039행): 체크 키 이름 → 한국어 라벨 매핑. 체크 키가 변경되면 이 매핑도 업데이트 필요. 2곳에 중복 정의되어 있어 하나만 수정하면 불일치 발생.

2. **`src/agents/nodes/reason/sql_validator.py`** (683~688행): `checks.items()`를 순회하며 pass/fail을 분류하는데, 키 이름에 직접 의존하지 않으므로 영향 없음.

**리스크 수준**: 낮음. insight_builder의 매핑만 업데이트하면 되며, 키 이름 변경은 명확한 find-and-replace 작업.

**권고**: insight_builder의 체크 키 매핑을 하드코딩에서 설정 또는 상수로 분리하여 유지보수성 개선.

---

## 라운드 4: 최종 권고

### 4.1 체크 구조 최종 권고안: 7개 체크

현재 9개에서 2개를 조정하여 7개로 재편한다.

```
체크 1: filter 반영       (현 체크 2, position/note 보강)
체크 2: group_by 반영     (현 체크 3, PARTITION role 포함)
체크 3: order/rank 반영   (현 체크 4에서 SORT/RANK/LIMIT만 잔존, DELTA/CUMULATIVE 제외)
체크 4: 미확인 값 사용    (현 체크 5, 변경 없음)
체크 5: dead_end 반복     (현 체크 6, 변경 없음)
체크 6: 논리적 정합성     (현 체크 7, 보강 — 현 체크 1의 measure 관점 흡수 + DELTA/CUMULATIVE 관점 흡수)
체크 7: DB 실행 결과 + 코드 명칭   (현 체크 8 + 현 체크 9 통합)
```

**핵심 변경점과 근거**:

1. **체크 1(measure) 흡수**: todo 문서와 동일. decomposition에서 measure_type/note 손실이 심각하여 독립 체크의 실효성이 낮다. 직렬화 개선 후 measure_type이 복원되면 독립 체크 복원을 재검토한다.

2. **체크 4(order_limit) 분리 유지 (축소 형태)**: todo 문서와 다른 권고. SORT/RANK/LIMIT은 `ORDER BY + LIMIT` 구조 존재 여부라는 명확한 구조적 패턴이며, LLM이 단순 대조로 처리 가능하다. DELTA/DELTA_RATE/CUMULATIVE는 ORDER BY와 무관하므로 이들만 체크 6(논리적 정합성)으로 이동한다.

   - 근거: RANK 질의("상위 N건")는 금융 도메인에서 매우 빈번하며(실적 순위, 거래량 순위 등), ORDER BY DESC LIMIT N 패턴 누락은 흔한 오류. 체크 6에 흡수하면 이 단순 패턴 감지를 open-ended 추론에 의존하게 되어 위험하다.
   - DELTA/CUMULATIVE 제외로 현재의 "이질적 modifier 혼재" 문제 해결.

3. **체크 8 + 체크 9 통합**: DB 실행 결과 확인과 코드 명칭 동반 확인은 모두 "SQL 실행/출력 레벨" 검증이다. 체크 9(코드 명칭)는 항상 local_fix이고 판정이 단순하므로 체크 8과 합쳐도 LLM 부담이 적다. 9개 → 7개로의 체크 수 감소 효과.

**8개 → 7개인 이유 (6개가 아닌 이유)**:

todo 문서의 6개안은 체크 4를 완전 흡수하지만, RANK/SORT 패턴 감지의 명시적 스캐폴딩을 포기한다. 금융 도메인에서 순위 질의 빈도와 ORDER BY/LIMIT 누락 빈도를 고려하면, 하나의 독립 체크를 유지하는 비용(JSON 필드 1개, 프롬프트 2~3줄)이 감지 누락의 위험보다 훨씬 낮다.

### 4.2 직렬화 최종 권고안: 선택지 D (하이브리드)

**1단계 (즉시 적용)**: 선택지 A 기반 가독성 개선
- `serialize_decomp_slots()`를 `json.dumps` → 사람/LLM 가독성 텍스트 포맷으로 변경
- `_build_decomposition_from_normalized()`에 누락 필드 추가:
  - filters: `position`, `note` (IMPLICIT일 때만)
  - group_by: `role` (GROUP/PARTITION 구분)
  - order_limit: SORT/RANK/LIMIT만 유지, `direction`, `limit`, `by` 포함
- null/빈 배열 노이즈 제거: 값이 없는 필드는 출력에서 제외

**2단계 (선택적)**: validator 보조 블록 추가
- 체크 6(논리적 정합성)에 NormalizedQuery의 `intent`, `time`, `ambiguities` 정보를 별도 블록으로 전달
- `{intent_and_time}` 플레이스홀더로 체크 6이 질의 유형과 기간을 정확히 참조할 수 있게 지원

이 접근이 최적인 이유:
- 1단계만으로도 현재 대비 큰 개선 (정보 손실 해결 + 가독성 향상)
- 2단계는 체크 6 보강 효과를 측정한 후 필요 시 적용
- 기존 아키텍처를 유지하면서 점진적 개선 가능

### 4.3 구현 우선순위

```
P0 (즉시): 직렬화 1단계 — 가독성 개선 + 누락 필드 추가
  - prompt.py: serialize_decomp_slots 텍스트 포맷 변경
  - reasoning_preparer.py: _build_decomposition_from_normalized 필드 보강
  - 예상 공수: 1일

P1 (직후): 프롬프트 재작성 — 7개 체크 구조 반영
  - sql_validator_system.txt: 체크 구조 재편 + 체크 6 보강 포인트 추가
  - 예시 업데이트 (최소 3개)
  - 예상 공수: 1~2일

P2 (동시): 하류 코드 업데이트
  - insight_builder.py: 체크 키 매핑 업데이트 (2곳)
  - 예상 공수: 0.5일

P3 (검증): 골든셋 테스트
  - 기존 validator 테스트 케이스를 새 체크 구조에 맞게 업데이트
  - RATIO, DELTA, RANK 질의에 대한 검증 정확도 비교
  - 예상 공수: 1~2일

P4 (선택적): 직렬화 2단계 — validator 보조 블록
  - P3 결과에서 체크 6의 정확도가 불충분한 경우에만 진행
  - 예상 공수: 1일
```

총 예상 공수: 4~6일 (P4 제외)

### 4.4 남아있는 리스크와 완화 방안

| 리스크 | 심각도 | 완화 방안 |
|--------|--------|-----------|
| 체크 6(논리적 정합성) 과부하로 measure 불일치 누락 | 중 | 체크 6 프롬프트에 "지표/산출 방식" 전용 서브섹션 추가, FAIL 시 어떤 관점인지 명시 의무화. P3에서 AGGREGATE 질의 집중 테스트 |
| Qwen3.5가 7개 체크 JSON 출력 시 구조 오류 | 중 | 현재 9개에서 7개로 감소하므로 개선 방향. 추가로 JSON 스키마를 프롬프트에 명시하고 extract_json의 복원 로직 강화 |
| RANK 질의에서 direction 정보 손실로 오판 | 중 | decomposition에 direction/limit/by 복원 (P0에서 해결). 체크 3이 `RANK, DESC, 10`을 명시적으로 대조 가능 |
| insight_builder 매핑 불일치 (2곳 중복) | 낮 | P2에서 매핑을 상수 모듈로 분리하여 단일 소스화 |
| 체크 8+9 통합 시 코드 명칭 누락 감지율 저하 | 낮 | 코드 명칭 확인은 규칙 기반(컬럼명 패턴 매칭)이므로 LLM 판단 부담 미미. 통합해도 감지율 변화 없음 |
| decomposition 완전 폐기 시점 불명확 | 낮 | decomposition은 sql_generator, recovery_agent도 참조. 이번 재설계에서는 보강에 집중하고, 전면 폐기는 NormalizedQuery 직접 참조 패턴이 충분히 검증된 후 별도 작업으로 진행 |

---

## 부록: 체크 키 이름 매핑 (현재 → 권고안)

| 현재 키 | 현재 번호 | 권고안 키 | 권고안 번호 |
|---------|----------|----------|------------|
| measure_reflected | 1 | (흡수 → logical_consistency) | - |
| filters_reflected | 2 | filters_reflected | 1 |
| group_by_reflected | 3 | group_by_reflected | 2 |
| order_limit_reflected | 4 | order_rank_reflected | 3 |
| no_unconfirmed_values | 5 | no_unconfirmed_values | 4 |
| no_dead_end_repeat | 6 | no_dead_end_repeat | 5 |
| logical_consistency | 7 | logical_consistency | 6 |
| db_execution | 8 | execution_and_output | 7 |
| code_name_paired | 9 | (흡수 → execution_and_output) | - |
