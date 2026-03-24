# 자연어 질의 정규화 전략 (NORMALIZATION_STRATEGY)

> **문서 목적**: 비즈니스 자연어 질의를 정확한 SQL로 변환하기 위한 정규화 시스템의 설계 전략, 아키텍처, Enum 레퍼런스, 프롬프트, 파이프라인, 그리고 실전 예시를 종합적으로 기술합니다.

---

## 1. 왜 정규화가 필요한가

사용자가 "지난 분기 대비 이번 분기 지역별 매출 상위 30개 대리점 실적 좀 뽑아줘"라고 입력하면, 이 한 문장 안에 **시간 조건, 비교 연산, 그룹핑, 정렬, 제한, 대상 엔티티, 측정값**이 모두 섞여 있습니다.

이것을 구조화하지 않으면:
- **메타 검색**(테이블/컬럼 탐색)의 검색어가 부정확해집니다.
- **벡터DB 사례 검색**(유사 SQL 탐색)의 의미 매칭이 흐려집니다.
- **최종 SQL 생성**에서 구조적 판단(GROUP BY? JOIN? 윈도우함수?)이 불명확해집니다.

정규화는 **"비즈니스 언어 → 의미 슬롯(Semantic Slot) 분해 → 구조화된 정규 표현 JSON 재조립"** 과정입니다.

---

## 2. 전체 아키텍처

```
[사용자 자연어 질의]
       │
       ▼
┌──────────────────┐     ┌───────────────────────────────┐
│  Step 0          │     │ CASUAL_TALK    → 대화 응답    │
│  Intent Gate     │────→│ META_QUESTION  → 메타 검색    │
│  (의도 분류)     │────→│ CLARIFICATION  → 이전결과병합 │
│                  │────→│ AMBIGUOUS      → 되물음       │
└────────┬─────────┘     └───────────────────────────────┘
         │ DATA_QUERY만 통과
         ▼
┌─────────────────────────────────────────────────────────────┐
│  Step 1. Preprocessor (전처리)                              │
│  ─ 공백/특수문자 정리                                       │
│  ─ 비즈니스 약어 확장 (YoY→전년동기대비, ARPU→객단가)       │
└────────────────────────┬────────────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  Step 2. Phase 1 LLM (8-Slot 분해)                          │
│  ─ system: 8-Slot 정의 + Enum 허용값 + 문서유형사전         │
│  ─ user: 질의 + 오늘 날짜 + 동의어 사전                     │
│  ─ output: 구조화 JSON (1차)                                │
└────────────────────────┬────────────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  Step 3. Validator (구조 검증)                              │
│  ─ JSON 파싱 + 모든 Enum 필드 허용값 검증                   │
│  ─ 불일치 시 자동 보정 or 기본값 대체                       │
└────────────────────────┬────────────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  Step 4. Phase 2 LLM (교차 검증 R1~R12)                     │
│  ─ 슬롯 간 모순 해소, 누락 보완                             │
│  ─ output_hint ↔ entity/measure 정합성 (R11, R12)           │
│  ─ rewritten_query / search_keywords 최적화                 │
└────────────────────────┬────────────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  Step 5. PostProcessor (후처리)                             │
│  ─ 코드 기반 기계적 정합성 최종 확인                        │
│  ─ expected_columns → meta_search 키워드 병합               │
│  ─ search_keywords 불용어 제거                              │
└────────────────────────┬────────────────────────────────────┘
                         ▼
               [NormalizedQuery JSON]
                         │
         ┌───────────────┼───────────────┐
         ▼               ▼               ▼
    meta_search    vector_search     8-Slot 구조
   (테이블 메타)    (SQL 사례)     (SQL 생성 가이드)
```

---

## 3. Step 0 — Intent Gate (의도 게이트)

### 3.1 존재 이유

기존 파이프라인은 입력이 무조건 데이터 요청이라고 전제하고 8-Slot 분해를 시작합니다. 그러나 실제로는 "안녕하세요", "매출이 뭐야?", "거기서 서울만 뽑아줘" 같은 입력이 들어옵니다. 이것들을 억지로 슬롯 분해하면 엉뚱한 결과가 나오므로, **파이프라인 진입 전에 1차 분류**가 필요합니다.

### 3.2 QueryCategory Enum

| 값 | 의미 | 라우팅 |
|---|---|---|
| `DATA_QUERY` | 데이터 추출/분석 요청 | → 8-Slot 정규화 파이프라인 진행 |
| `CASUAL_TALK` | 인사, 감사, 잡담 | → 대화형 응답 처리 |
| `META_QUESTION` | 데이터/시스템에 대한 질문 | → 메타 검색 라우팅 |
| `CLARIFICATION` | 이전 질의 보충/수정 | → 이전 정규화 결과와 병합 |
| `AMBIGUOUS` | 판단 불가 | → 사용자에게 되물음 |

### 3.3 Intent Gate 프롬프트

```python
INTENT_GATE_SYSTEM_PROMPT = """당신은 사용자 입력의 의도를 분류하는 전문가입니다.
사용자가 데이터 분석 시스템에 입력한 문장을 받아, 아래 5개 카테고리 중 하나로 분류합니다.

[분류 카테고리]

DATA_QUERY — 데이터 추출 또는 분석 요청
  판별 기준: 아래 중 2개 이상 충족
  ✓ 구체적인 비즈니스 엔티티가 언급됨 (고객, 주문, 상품, 매출 등)
  ✓ 수치/지표가 언급되거나 암시됨 (매출, 건수, 비율 등)
  ✓ 조회/추출/분석을 요청하는 동사가 있음 (뽑아줘, 조회, 분석, 비교 등)
  ✓ 조건/범위가 명시됨 (지난달, 서울, VIP 등)
  예: "지역별 매출 뽑아줘", "VIP 고객 리스트 조회"

CASUAL_TALK — 일반 대화 (인사, 감사, 잡담)
  판별 기준: 데이터/비즈니스 엔티티 언급 없이 일상적 대화
  예: "안녕하세요", "감사합니다"

META_QUESTION — 데이터/시스템 자체에 대한 질문
  판별 기준: 데이터를 "조회"하는 게 아니라 데이터에 "대해" 묻는 질문
  예: "매출이 뭘 의미해?", "어떤 데이터 조회 가능해?"

CLARIFICATION — 이전 질의에 대한 보충/수정
  판별 기준: 단독으로는 완전한 질의가 아니며, 이전 맥락 없이 의미 불완전
  예: "거기서 서울만", "기간 3개월로 바꿔줘"

AMBIGUOUS — 판단 불가
  판별 기준: 위 4개 중 어디에도 확신 있게 분류할 수 없음
  예: "고객 관련해서", "매출"

[출력]: JSON만 출력.
{"category": "...", "confidence": "HIGH|MEDIUM|LOW", "reason": "..."}
"""
```

### 3.4 라우팅 코드

```python
def run(self, raw_query: str) -> dict:
    # Step 0: Intent Gate
    gate_result = self.intent_gate.classify(raw_query)
    category = gate_result["category"]
    
    if category == "DATA_QUERY":
        return self.run_normalization(raw_query)  # → 8-Slot 파이프라인
    
    elif category == "CASUAL_TALK":
        return {"category": "CASUAL_TALK", "action": "RESPOND_CHAT",
                "original_query": raw_query}
    
    elif category == "META_QUESTION":
        return {"category": "META_QUESTION", "action": "SEARCH_META",
                "original_query": raw_query}
    
    elif category == "CLARIFICATION":
        return {"category": "CLARIFICATION", "action": "MERGE_WITH_PREVIOUS",
                "original_query": raw_query}
    
    elif category == "AMBIGUOUS":
        return {"category": "AMBIGUOUS", "action": "ASK_USER",
                "original_query": raw_query,
                "suggested_question": "어떤 데이터를 조회하거나 분석하고 싶으신 건가요?"}
```

---

## 4. 8-Slot 분해 체계

### 4.1 슬롯 개요

| # | 슬롯 | 역할 | SQL 대응 |
|---|------|------|---------|
| 1 | **INTENT** | 질의 유형 | 전체 SQL 구조 결정 |
| 2 | **ENTITY** | 대상 테이블/도메인 | FROM / JOIN |
| 3 | **MEASURE** | 측정값/지표 | SELECT (집계함수) |
| 4 | **DIMENSION** | 분류/그룹 축 | GROUP BY |
| 5 | **FILTER** | 조건/범위 | WHERE / HAVING |
| 6 | **TIME** | 시간 범위/기준 | WHERE (날짜) |
| 7 | **MODIFIER** | 정렬/제한/비교 | ORDER BY, LIMIT, 서브쿼리 |
| 8 | **OUTPUT_HINT** | 출력 형식 힌트 | SELECT 컬럼 가이드 |

### 4.2 왜 7에서 8 슬롯으로 확장했는가

"거래명세 조회해줘"에서 "명세"라는 단어는 엔티티도 아니고 측정값도 아닙니다. 이것은 **"출력 스키마 힌트"** — 특정 비즈니스 문서 형식에 관례적으로 포함되어야 하는 컬럼 세트를 암시합니다. 7-Slot 체계에서는 이 정보가 유실되어 SELECT 절이 불완전해지므로, OUTPUT_HINT를 8번째 슬롯으로 추가했습니다.

---

## 5. Enum 제약값 전체 레퍼런스

아래 값들은 로직에서 분기/매칭에 사용되므로 **반드시 이 목록 내에서만 선택**되어야 합니다.

### 5.1 IntentType (질의 유형)

| 값 | 의미 | SQL 패턴 | 비즈니스 시그널 |
|---|---|---|---|
| `EXTRACT` | 단순 조회 | SELECT ... WHERE | "목록", "리스트", "뽑아줘" |
| `AGGREGATE` | 집계 조회 | GROUP BY + 집계함수 | "합계", "평균", "~별" |
| `COMPARE` | 비교 분석 | 셀프조인, LAG, CASE | "대비", "비교", "vs" |
| `TREND` | 추이 분석 | 시계열 GROUP BY + 윈도우 | "추이", "변화", "트렌드" |
| `RANK` | 순위 조회 | ORDER BY + LIMIT / RANK() | "상위", "top", "순위" |
| `DISTRIBUTE` | 분포/비율 | 집계 + 비율 계산 | "분포", "비율", "점유율" |
| `EXIST_CHECK` | 존재 확인 | EXISTS / NOT EXISTS | "~있는지", "~여부" |
| `DEDUP` | 중복 확인 | HAVING COUNT>1 | "중복", "겹치는" |
| `PIVOT` | 교차 분석 | CASE WHEN 피벗 | "교차", "매트릭스" |

### 5.2 EntityType

| 값 | 의미 | 검색 전략 |
|---|---|---|
| `DIRECT` | 테이블 직접 매핑 가능 | 메타 검색으로 테이블명 매칭 |
| `INDIRECT` | 복수 테이블 조합 개념 | 메타 + 벡터 사례 검색 병행 |
| `IMPLIED` | 명시되지 않았으나 필요 | 벡터 사례 검색 중심 |

### 5.3 AggFunction (집계 함수)

| 값 | SQL | 시그널 |
|---|---|---|
| `SUM` | SUM() | "총", "합계", "전체" |
| `AVG` | AVG() | "평균" |
| `COUNT` | COUNT() | "건수", "횟수", "수" |
| `COUNT_DISTINCT` | COUNT(DISTINCT) | "고유 수", "유니크" |
| `MAX` | MAX() | "최대", "최고" |
| `MIN` | MIN() | "최소", "최저" |
| `NONE` | 집계 없음 | EXTRACT에서 행 단위 조회 |
| `UNKNOWN` | 확정 불가 | 모호한 경우 |

### 5.4 MeasureType

| 값 | 의미 | SQL 복잡도 |
|---|---|---|
| `RAW` | 단일 컬럼 직접 참조 | column_name |
| `DERIVED` | 계산식 필요 | col1/col2, col1-col2 |
| `RATIO` | 비율 지표 | 분자/분모 + 비즈니스 로직 |
| `WINDOW` | 윈도우 함수 필요 | OVER() 절 |

### 5.5 DimensionRole

| 값 | SQL 대응 | 예시 |
|---|---|---|
| `GROUP` | GROUP BY | "지역별" |
| `PARTITION` | PARTITION BY | "지역 내 순위" |
| `FILTER` | WHERE (특정 값) | "서울 지역" |
| `DISPLAY` | SELECT에만 포함 | 표시용 차원 |

### 5.6 Granularity

시간 차원: `YEAR`, `QUARTER`, `MONTH`, `WEEK`, `DAY`, `HOUR`, `UNKNOWN`
비시간 차원: `INDIVIDUAL`, `CATEGORY`, `HIERARCHY`, `UNKNOWN`

### 5.7 FilterType

| 값 | SQL | 시그널 |
|---|---|---|
| `EQUALS` | = | "~인", "~에 해당" |
| `NOT_EQUALS` | != | "~아닌" |
| `IN` | IN (...) | "A, B, C" |
| `NOT_IN` | NOT IN | "~제외" |
| `RANGE` | BETWEEN | "~부터 ~까지" |
| `GT` / `GTE` | > / >= | "초과" / "이상" |
| `LT` / `LTE` | < / <= | "미만" / "이하" |
| `LIKE` | LIKE | "포함", "~가 들어간" |
| `IS_NULL` | IS NULL | "~없는", "비어있는" |
| `IS_NOT_NULL` | IS NOT NULL | "~있는" |
| `EXISTS` | EXISTS 서브쿼리 | "~이력이 있는" |
| `NOT_EXISTS` | NOT EXISTS | "~이력이 없는" |
| `IMPLICIT` | 암묵적 조건 | "정상", "활성", "유효" |

### 5.8 FilterPosition

| 값 | SQL | 판별 기준 |
|---|---|---|
| `PRE_AGG` | WHERE | 원본 행에 대한 조건 |
| `POST_AGG` | HAVING | 집계 결과에 대한 조건 |

### 5.9 TimeType

| 값 | 의미 | 필수 필드 |
|---|---|---|
| `ABSOLUTE` | 명시적 날짜 | base_period (absolute_start/end) |
| `RELATIVE` | 현재 기준 상대 | base_period (resolve + n) |
| `COMPARISON` | 두 기간 비교 | base_period + compare_period |
| `CUMULATIVE` | 누적 기간 | base_period (YTD/MTD/QTD) |
| `NONE` | 시간 조건 없음 | — |

### 5.10 TimePeriodResolve

일: `TODAY`, `YESTERDAY`, `LAST_N_DAYS`
주: `THIS_WEEK`, `LAST_WEEK`, `LAST_N_WEEKS`
월: `THIS_MONTH`, `LAST_MONTH`, `LAST_N_MONTHS`
분기: `THIS_QUARTER`, `LAST_QUARTER`, `PREVIOUS_QUARTER`, `LAST_N_QUARTERS`
반기: `THIS_HALF`, `LAST_HALF`
연: `THIS_YEAR`, `LAST_YEAR`, `LAST_N_YEARS`
누적: `YTD`, `MTD`, `QTD`
절대: `ABSOLUTE_RANGE` (absolute_start/end 필드 병행)

### 5.11 ModifierType

| 값 | SQL 패턴 | 시그널 |
|---|---|---|
| `SORT` | ORDER BY | "높은 순", "최신순" |
| `LIMIT` | LIMIT N | "N건만", "처음 N개" |
| `RANK` | RANK() + LIMIT | "상위 N", "top N" |
| `RATIO` | val / SUM() OVER() | "비율", "비중" |
| `DELTA` | curr - prev | "증감", "차이" |
| `DELTA_RATE` | (curr-prev)/prev | "증감률", "성장률" |
| `CUMULATIVE` | SUM() OVER(ORDER BY) | "누적" |
| `MOVING_AVG` | AVG() OVER(ROWS) | "이동평균" |
| `PERCENTAGE` | * 100 / total | "퍼센트", "%" |

### 5.12 SortDirection

`ASC` (오름차순), `DESC` (내림차순)

### 5.13 ConfidenceLevel

| 값 | 의미 | 후속 처리 |
|---|---|---|
| `HIGH` | 명확하게 특정됨 | 그대로 사용 |
| `MEDIUM` | 높은 확률 추정 | 메타/사례 검색으로 보강 |
| `LOW` | 다중 해석 가능 | 사용자 확인 또는 추가 검색 필수 |

### 5.14 OutputFormat (8번째 슬롯)

| 값 | 의미 | 시그널 |
|---|---|---|
| `SPEC_SHEET` | 명세서/명세표 | "명세", "내역서", "세부내역" |
| `SUMMARY` | 요약/현황 | "현황", "요약", "개요" |
| `DETAIL_LIST` | 상세 목록 | "상세", "전체", "전건" |
| `REPORT` | 보고서 | "보고서", "리포트" |
| `COMPARISON` | 비교표 | "비교표", "대조표" |
| `NONE` | 힌트 없음 | 기본값 |

---

## 6. OUTPUT_HINT 슬롯 상세 — "명세" 문제의 해결

### 6.1 문제 정의

"거래명세 조회해줘"에서 단순히 Entity=거래, Intent=EXTRACT로 분해하면 **"명세"가 함의하는 출력 컬럼 세트가 유실됩니다.** "명세"는 특정 비즈니스 문서의 형식이며, 관례적으로 포함되어야 하는 컬럼들이 정해져 있습니다.

### 6.2 문서유형별 기대 컬럼 매핑 사전 (OUTPUT_TEMPLATE_REGISTRY)

```python
OUTPUT_TEMPLATE_REGISTRY = {
    "거래명세": {
        "triggers": ["거래 명세", "거래명세서", "거래명세표", "거래 내역서"],
        "format": "SPEC_SHEET",
        "expected_columns": [
            "거래일자", "거래번호", "거래처명", "품목명",
            "수량", "단가", "공급가액", "세액", "합계금액", "비고"
        ],
        "required_entities": ["거래", "거래처", "상품"],
        "note": "공급가액 = 수량 × 단가, 세액 = 공급가액 × 세율"
    },
    "매출명세": {
        "triggers": ["매출 명세", "매출명세서", "매출내역서", "판매명세"],
        "format": "SPEC_SHEET",
        "expected_columns": [
            "매출일자", "매출번호", "고객명", "상품명",
            "수량", "단가", "매출금액", "할인금액", "실매출금액", "결제수단"
        ],
        "required_entities": ["주문", "고객", "상품"],
        "note": "실매출금액 = 매출금액 - 할인금액"
    },
    "매입명세": {
        "triggers": ["매입 명세", "매입명세서", "매입내역서", "구매명세"],
        "format": "SPEC_SHEET",
        "expected_columns": [
            "매입일자", "매입번호", "공급자명", "품목명",
            "수량", "단가", "공급가액", "세액", "합계금액"
        ],
        "required_entities": ["매입", "공급자", "상품"],
        "note": None
    },
    "급여명세": {
        "triggers": ["급여 명세", "급여명세서", "월급명세", "급여내역"],
        "format": "SPEC_SHEET",
        "expected_columns": [
            "사번", "성명", "부서", "직급",
            "기본급", "직책수당", "식대", "교통비", "시간외수당",
            "국민연금", "건강보험", "고용보험", "소득세", "지방소득세",
            "공제합계", "실수령액"
        ],
        "required_entities": ["직원", "급여"],
        "note": "실수령액 = (기본급 + 수당합계) - 공제합계"
    },
    "입출금명세": {
        "triggers": ["입출금 명세", "입출금내역", "계좌내역", "통장 내역"],
        "format": "SPEC_SHEET",
        "expected_columns": [
            "거래일시", "적요", "입금액", "출금액", "잔액", "거래상대", "메모"
        ],
        "required_entities": ["계좌", "거래"],
        "note": None
    },
    "세금계산서명세": {
        "triggers": ["세금계산서 명세", "세금계산서 내역", "세금계산서 목록"],
        "format": "SPEC_SHEET",
        "expected_columns": [
            "작성일자", "승인번호", "공급자사업자번호", "공급자상호",
            "공급받는자사업자번호", "공급받는자상호",
            "공급가액", "세액", "합계금액", "비고"
        ],
        "required_entities": ["세금계산서"],
        "note": None
    },
    "재고명세": {
        "triggers": ["재고 명세", "재고명세서", "재고현황", "재고 현황"],
        "format": "SPEC_SHEET",
        "expected_columns": [
            "품목코드", "품목명", "카테고리", "단위",
            "기초재고", "입고수량", "출고수량", "현재고", "안전재고", "과부족"
        ],
        "required_entities": ["상품", "재고"],
        "note": "현재고 = 기초재고 + 입고 - 출고, 과부족 = 현재고 - 안전재고"
    },
    "매출현황": {
        "triggers": ["매출 현황", "매출현황표", "판매 현황"],
        "format": "SUMMARY",
        "expected_columns": [
            "기간", "총매출액", "총건수", "평균단가",
            "전기대비증감액", "전기대비증감률"
        ],
        "required_entities": ["주문"],
        "note": None
    },
    "고객현황": {
        "triggers": ["고객 현황", "회원 현황", "고객현황표"],
        "format": "SUMMARY",
        "expected_columns": [
            "기간", "총고객수", "신규고객수", "이탈고객수",
            "활성고객수", "휴면고객수"
        ],
        "required_entities": ["고객"],
        "note": None
    },
}
```

### 6.3 output_hint가 후속 파이프라인에서 하는 역할

**메타/사례 검색 단계**: `expected_columns`에 있는 컬럼명(거래일자, 공급가액, 세액 등)이 검색 키워드로 추가됩니다. "거래명세"로만 검색하면 테이블을 못 찾을 수 있지만, 구체적 컬럼명으로 검색하면 정확한 테이블/컬럼을 찾을 확률이 올라갑니다.

**SQL 생성 단계**: `expected_columns`가 SELECT 절의 가이드가 됩니다. 일반 EXTRACT는 사용자가 명시한 컬럼만 SELECT하지만, SPEC_SHEET 포맷이면 expected_columns 전체를 포함시키고, 부족한 컬럼은 JOIN이나 계산식으로 보완합니다.

---

## 7. 2-Phase LLM 전략

### 7.1 왜 LLM을 2번 호출하는가

단일 프롬프트로 분해 + 검증을 동시에 하면:
- 프롬프트가 과부하되어 LLM이 일부 규칙을 무시
- LLM은 자신이 방금 생성한 결과를 같은 턴에서 비판적으로 검토하기 어려움
- 문제가 "분해 오류"인지 "검증 누락"인지 디버깅 불가

Phase 1은 **추출**에, Phase 2는 **검토**에 집중시켜 정확도를 높입니다.

| 관점 | Phase 1 (분해) | Phase 2 (검증) |
|------|---------------|---------------|
| **역할** | 원문 → 슬롯 배정 | 슬롯 간 정합성 확인 |
| **인지 부하** | 추출에 집중 | 규칙 적용에 집중 |
| **대체 가능성** | 더 강한 모델로 교체 | 코드 규칙 엔진으로 대체 가능 |

### 7.2 교차 검증 규칙 R1~R12

| 규칙 | 조건 | 액션 |
|------|------|------|
| **R1** | GROUP dim 있는데 measure.agg=NONE | agg를 SUM으로 추정, confidence=MEDIUM |
| **R2** | COMPARE인데 time≠COMPARISON | time.type 보정 + 누락 기간 추론 |
| **R3** | RANK인데 modifier.by 비어있음 | 첫 번째 measure로 채움 |
| **R4** | TREND인데 시간 dimension 없음 | 시간 dimension 추가 |
| **R5** | DISTRIBUTE인데 PERCENTAGE modifier 없음 | modifier 추가 |
| **R6** | EXIST_CHECK인데 EXISTS filter 없음 | filter 추가 |
| **R7** | "정상","활성" 표현인데 IMPLICIT filter 없음 | filter 추가 |
| **R8** | 동일 type modifier 중복 | 하나로 합침 |
| **R9** | rewritten_query 품질 부족 | 개선 |
| **R10** | search_keywords에 불용어 포함 | 정제 |
| **R11** | SPEC_SHEET인데 required_entities 누락 | IMPLIED 엔티티 추가 |
| **R12** | expected_columns에 계산 항목 있음 | DERIVED measure 추가 |

---

## 8. 슬롯 간 상호작용 규칙 (Interaction Rules)

단순 키워드 매핑을 넘어 슬롯 간 상호 의존성으로 정확도를 높이는 핵심 규칙들입니다.

- **DIMENSION 존재 → MEASURE에 집계 강제**: "지역별 매출"에서 "별"이 있으면 매출은 반드시 SUM/AVG 등 집계 필요
- **COMPARE → TIME에 2개 기간 필요**: "대비"가 있는데 비교 기간이 하나만 명시되면 나머지 추론
- **RANK + DIMENSION → 랭킹 기준 명확화**: "상위 30개"의 기준이 되는 MEASURE 특정 필수
- **암시 엔티티 → JOIN 예상**: "객단가" → 고객 + 주문 테이블 JOIN 필요
- **부정 표현 → ANTI-JOIN**: "구매 이력이 없는 고객" → LEFT JOIN + IS NULL 패턴
- **비율/점유율 → 윈도우 필요**: "점유율" → 개별값 / SUM() OVER() 구조
- **추이 → 시간 DIMENSION 강제**: "추이" → TIME이 DIMENSION 겸임 + 시간 순 정렬
- **OUTPUT_HINT → ENTITY/MEASURE 보완**: "거래명세" → 거래처·상품 엔티티 + 파생 컬럼 추가

---

## 9. 동의어/패턴 사전

```yaml
measures:
  매출액:     [매출, 세일즈, 판매금액, 판매액, revenue, sales]
  주문건수:   [주문수, 오더건수, 거래건수, 거래횟수]
  고객수:     [회원수, 유저수, 사용자수, 가입자수]
  객단가:     [인당매출, 1인당매출, ARPU, 건당단가]
  전환율:     [컨버전율, CVR, 구매전환율]
  이탈률:     [해지율, 탈퇴율, churn]

entities:
  고객:       [회원, 유저, 사용자, customer, user]
  주문:       [오더, 거래, 결제, 구매, order]
  상품:       [제품, 품목, 아이템, SKU, product]
  대리점:     [딜러, 에이전트, 총판, 거래처, 판매점]

dimension_signals:
  GROUP_BY:   [~별, ~기준, ~단위로, ~마다, 각 ~]

time_patterns:
  RELATIVE:   [지난, 최근, 이번, 올해, 작년, 전월, 당월]
  ABSOLUTE:   [2024년, 1분기, 3월, 1~3월]
  COMPARISON: [대비, 비교, 동기, 전년동월, YoY, MoM, QoQ]

modifier_signals:
  RANK:       [상위, 하위, top, bottom, 1위, 순위]
  SORT:       [높은순, 많은순, 최신순, 오래된순]
  TREND:      [추이, 변화, 증감, 트렌드, 성장률]
  DISTRIBUTE: [분포, 비율, 점유율, 비중, 구성비, 퍼센트]

output_format_signals:
  SPEC_SHEET: [명세, 명세서, 명세표, 내역서, 세부내역]
  SUMMARY:    [현황, 요약, 개요, 대시보드]
  DETAIL_LIST:[상세, 전체, 전건, raw]
  REPORT:     [보고서, 리포트, report]
```

---

## 10. 정규화 출력 JSON 예시

### 예시 1: 복합 분석 — 분기 대비 + 지역별 + 순위

**입력**: "지난 분기 대비 이번 분기 지역별 매출 상위 30개 대리점 실적 좀 뽑아줘"

```json
{
  "original_query": "지난 분기 대비 이번 분기 지역별 매출 상위 30개 대리점 실적 좀 뽑아줘",
  "rewritten_query": "이번 분기와 지난 분기를 비교하여, 지역별로 매출액 합계 기준 상위 30개 대리점의 매출 실적을 조회하고 분기 간 증감을 함께 표시",
  "intent": {
    "primary": "COMPARE",
    "secondary": ["AGGREGATE", "RANK"]
  },
  "entities": [
    { "term": "대리점", "normalized_term": "대리점", "type": "DIRECT", "confidence": "HIGH" }
  ],
  "measures": [
    { "term": "매출", "normalized_term": "매출액", "measure_type": "RAW",
      "agg_function": "SUM", "confidence": "HIGH" }
  ],
  "dimensions": [
    { "term": "지역", "role": "GROUP", "granularity": "UNKNOWN",
      "is_time_dimension": false, "confidence": "HIGH",
      "note": "시/도 vs 시/군/구 단위 불명확" },
    { "term": "대리점", "role": "GROUP", "granularity": "INDIVIDUAL",
      "is_time_dimension": false, "confidence": "HIGH" }
  ],
  "filters": [],
  "time": {
    "type": "COMPARISON",
    "base_period": { "label": "이번 분기", "resolve": "THIS_QUARTER" },
    "compare_period": { "label": "지난 분기", "resolve": "LAST_QUARTER" }
  },
  "modifiers": [
    { "type": "RANK", "direction": "DESC", "limit": 30, "by": "매출" },
    { "type": "DELTA", "by": "매출", "note": "R2: COMPARE에 따라 증감값 추가" }
  ],
  "output_hint": {
    "format": "NONE", "doc_type": null, "expected_columns": [], "confidence": "HIGH"
  },
  "ambiguities": [
    "'지역별'의 세부 단위(시/도, 시/군/구) 불명확 — 메타 검색으로 확인 필요",
    "상위 30개 기준이 이번 분기 매출인지 증감액인지 불명확 — 이번 분기 매출 기준으로 추정"
  ],
  "search_keywords": {
    "meta_search": ["대리점", "매출액", "지역", "분기"],
    "vector_search": "분기별 지역별 대리점 매출 합계 순위를 비교하는 쿼리"
  }
}
```

### 예시 2: 단순 조회 — VIP 고객 목록

**입력**: "VIP 고객 목록 좀 뽑아줘"

```json
{
  "original_query": "VIP 고객 목록 좀 뽑아줘",
  "rewritten_query": "등급이 VIP인 고객의 목록을 조회",
  "intent": { "primary": "EXTRACT", "secondary": [] },
  "entities": [
    { "term": "고객", "normalized_term": "고객", "type": "DIRECT", "confidence": "HIGH" }
  ],
  "measures": [],
  "dimensions": [],
  "filters": [
    { "target": "등급", "filter_type": "EQUALS", "position": "PRE_AGG",
      "values": ["VIP"], "confidence": "HIGH" }
  ],
  "time": { "type": "NONE" },
  "modifiers": [],
  "output_hint": { "format": "NONE" },
  "ambiguities": [
    "'VIP'가 등급 컬럼의 값인지, 별도 VIP 플래그인지 확인 필요"
  ],
  "search_keywords": {
    "meta_search": ["고객", "VIP", "등급"],
    "vector_search": "VIP 등급 고객 목록 조회 쿼리"
  }
}
```

### 예시 3: 명세 조회 — output_hint 활용

**입력**: "3월 거래명세 조회해줘"

```json
{
  "original_query": "3월 거래명세 조회해줘",
  "rewritten_query": "2026년 3월의 거래명세서를 조회 (거래일자, 거래번호, 거래처명, 품목명, 수량, 단가, 공급가액, 세액, 합계금액 포함)",
  "intent": { "primary": "EXTRACT", "secondary": [] },
  "entities": [
    { "term": "거래", "type": "DIRECT", "confidence": "HIGH" },
    { "term": "거래처", "type": "IMPLIED", "confidence": "HIGH",
      "note": "R11: 거래명세 형식에 거래처 정보 필요" },
    { "term": "상품", "type": "IMPLIED", "confidence": "HIGH",
      "note": "R11: 거래명세 형식에 품목 정보 필요" }
  ],
  "measures": [
    { "term": "공급가액", "measure_type": "DERIVED", "agg_function": "NONE",
      "confidence": "HIGH", "note": "R12: 공급가액 = 수량 × 단가" }
  ],
  "dimensions": [],
  "filters": [],
  "time": {
    "type": "ABSOLUTE",
    "base_period": {
      "label": "3월", "resolve": "ABSOLUTE_RANGE",
      "absolute_start": "2026-03-01", "absolute_end": "2026-03-31"
    }
  },
  "modifiers": [],
  "output_hint": {
    "format": "SPEC_SHEET",
    "doc_type": "거래명세",
    "expected_columns": [
      "거래일자", "거래번호", "거래처명", "품목명",
      "수량", "단가", "공급가액", "세액", "합계금액", "비고"
    ],
    "confidence": "HIGH",
    "note": "공급가액 = 수량 × 단가, 세액 = 공급가액 × 세율"
  },
  "ambiguities": [
    "세율 기준(10% 고정 vs 품목별 차등) 확인 필요"
  ],
  "search_keywords": {
    "meta_search": ["거래", "거래처", "상품", "거래명세",
                    "거래일자", "거래번호", "공급가액", "세액"],
    "vector_search": "거래명세서 형식으로 월별 거래 내역을 조회하는 쿼리"
  }
}
```

### 예시 4: 추이 분석 — 채널별 전환율

**입력**: "최근 6개월간 채널별 신규 고객 전환율 추이 좀 분석해줘"

```json
{
  "original_query": "최근 6개월간 채널별 신규 고객 전환율 추이 좀 분석해줘",
  "rewritten_query": "최근 6개월간 유입 채널별로 신규 고객의 전환율을 월별 추이로 분석",
  "intent": { "primary": "TREND", "secondary": ["AGGREGATE"] },
  "entities": [
    { "term": "고객", "type": "DIRECT", "confidence": "HIGH" }
  ],
  "measures": [
    { "term": "전환율", "normalized_term": "전환율", "measure_type": "RATIO",
      "agg_function": "UNKNOWN", "confidence": "MEDIUM",
      "note": "전환의 분자(구매)/분모(방문 or 가입)가 불명확" }
  ],
  "dimensions": [
    { "term": "채널", "role": "GROUP", "granularity": "CATEGORY",
      "is_time_dimension": false, "confidence": "HIGH" },
    { "term": "월", "role": "GROUP", "granularity": "MONTH",
      "is_time_dimension": true, "confidence": "HIGH",
      "note": "R4: TREND 인텐트에 의해 시간 차원 명시" }
  ],
  "filters": [
    { "target": "고객유형", "filter_type": "EQUALS", "position": "PRE_AGG",
      "values": ["신규"], "confidence": "MEDIUM",
      "note": "'신규'의 기준 확인 필요" }
  ],
  "time": {
    "type": "RELATIVE",
    "base_period": { "label": "최근 6개월", "resolve": "LAST_N_MONTHS", "n": 6 }
  },
  "modifiers": [
    { "type": "SORT", "direction": "ASC", "by": "월",
      "note": "추이 분석이므로 시간 순 정렬" }
  ],
  "output_hint": { "format": "NONE" },
  "ambiguities": [
    "전환율의 정의(분자: 구매 / 분모: 방문 or 가입)가 불명확",
    "'신규 고객'의 기준 불명확",
    "추이의 시간 단위를 월별로 추정했으나 주별일 수 있음"
  ],
  "search_keywords": {
    "meta_search": ["고객", "채널", "전환율", "신규"],
    "vector_search": "채널별 신규 고객 전환율을 월별로 추이 분석하는 쿼리"
  }
}
```

### 예시 5: 존재 확인 + 부정

**입력**: "올해 한 번도 구매하지 않은 작년 활성 고객 리스트"

```json
{
  "original_query": "올해 한 번도 구매하지 않은 작년 활성 고객 리스트",
  "rewritten_query": "작년에 구매 이력이 있었으나 올해에는 구매 이력이 전혀 없는 고객 목록을 조회",
  "intent": { "primary": "EXTRACT", "secondary": ["EXIST_CHECK"] },
  "entities": [
    { "term": "고객", "type": "DIRECT", "confidence": "HIGH" },
    { "term": "주문", "type": "IMPLIED", "confidence": "HIGH",
      "note": "구매 이력 확인을 위해 주문 테이블 필요" }
  ],
  "measures": [],
  "dimensions": [],
  "filters": [
    { "target": "구매이력(올해)", "filter_type": "NOT_EXISTS", "position": "PRE_AGG",
      "confidence": "HIGH", "note": "올해 주문 레코드 없어야 함" },
    { "target": "구매이력(작년)", "filter_type": "EXISTS", "position": "PRE_AGG",
      "confidence": "HIGH", "note": "작년 주문 레코드 있어야 함" },
    { "target": "고객상태", "filter_type": "IMPLICIT", "position": "PRE_AGG",
      "values": ["활성"], "confidence": "MEDIUM",
      "note": "'활성'의 정의 확인 필요 — R7 적용" }
  ],
  "time": {
    "type": "COMPARISON",
    "base_period": { "label": "올해", "resolve": "THIS_YEAR" },
    "compare_period": { "label": "작년", "resolve": "LAST_YEAR" }
  },
  "modifiers": [],
  "output_hint": { "format": "NONE" },
  "ambiguities": [
    "'활성 고객'의 정확한 정의 불명확",
    "'구매하지 않은'이 주문 취소 포함인지, 완료 주문만 기준인지 불명확"
  ],
  "search_keywords": {
    "meta_search": ["고객", "주문", "구매", "활성"],
    "vector_search": "작년 구매 고객 중 올해 미구매 고객 목록을 조회하는 쿼리"
  }
}
```

---

## 11. 파일 구조

```
query_normalizer/
├── NORMALIZATION_STRATEGY.md   ← 이 문서
├── __init__.py                 ← 패키지 초기화
├── enums.py                    ← Enum 정의 + 데이터클래스 스키마 (모든 제약값)
├── prompts.py                  ← Intent Gate + Phase 1 + Phase 2 LLM 프롬프트
├── synonyms.py                 ← 동의어 사전 + OUTPUT_TEMPLATE_REGISTRY
└── pipeline.py                 ← 파이프라인 오케스트레이션 (메인 실행)
```

---

## 12. 확장 포인트

**도메인 특화 사전 추가**: synonyms.py의 사전과 OUTPUT_TEMPLATE_REGISTRY에 도메인별 용어/문서 유형을 추가합니다. (예: 금융이면 "여신명세", "수신현황" 등)

**Phase 2를 규칙 엔진으로 대체**: R1~R12가 안정화되면 LLM 호출 대신 코드 기반 규칙 엔진으로 교체하여 비용/지연을 줄일 수 있습니다. PostProcessor.ensure_consistency()를 확장하는 방식입니다.

**CLARIFICATION 처리**: 이전 NormalizedQuery와 새 질의를 병합하는 로직을 구현합니다. "거기서 서울만"이 오면 이전 결과의 filters에 EQUALS(지역, 서울)을 추가하는 방식입니다.

**다중 질의 분리**: "A 좀 뽑아주고 B도 분석해줘"처럼 복수 요청이 하나의 문장에 있는 경우 전처리 단계에서 분리하는 로직을 추가할 수 있습니다.

**피드백 루프**: 정규화 → SQL → 실행 → 사용자 피드백을 수집하여, 동의어 사전과 교차 규칙을 점진적으로 개선합니다.
