# 모호한 출력 대상에 대한 기본 출력 사양 전략

> 프로젝트 설계 분석 기반 (2026-03-21)
> 대상: "명세", "정보", "현황", "목록" 등 컬럼 단위가 아닌 모호한 출력 요청 처리

---

## 1. 문제 정의

### 1.1 현상

사용자가 "대출 명세 뽑아줘", "사업자 정보 알려줘"처럼 요청할 때,
"명세"나 "정보"는 **특정 컬럼이 아닌 추상적 데이터 범위**를 의미한다.

현재 시스템은 SQL 생성 시 출력 컬럼 선택을 **전적으로 LLM에 위임**하고 있어,
다음 문제가 발생할 수 있다:

| 문제 | 설명 |
|------|------|
| 일관성 부재 | 같은 "대출 명세" 요청에 매번 다른 컬럼 구성 |
| 과소 출력 | 사용자가 기대하는 핵심 컬럼 누락 |
| 과다 출력 | 불필요한 내부 코드·시스템 컬럼까지 포함 |
| 소형 모델 취약 | 폐쇄망 소형 LLM(7B~70B)은 컬럼 선택 판단력 저하 |

### 1.2 핵심 어려움

같은 "명세"라도 **도메인 엔터티에 따라 기대 컬럼이 완전히 다르다**:

| 요청 | 대상 엔터티 | 기대 출력 컬럼 |
|------|------------|---------------|
| "대출 명세 뽑아줘" | 여신 | 고객명, 대출종류, 실행일, 실행금액, 잔액, 금리, 만기일 |
| "수신 명세 뽑아줘" | 수신 | 고객명, 상품명, 계좌개설일, 잔액, 금리, 만기일 |
| "고객 정보 알려줘" | 고객 | 고객명, 고객유형, 등급, 가입일, 관리점 |
| "사업자 정보 알려줘" | 기업고객 | 사업자명, 사업자번호, 업종, 대표자, 설립일 |
| "거래 내역 뽑아줘" | 거래 | 거래일시, 거래유형, 금액, 잔액, 적요 |

따라서 **무조건적인 고정 컬럼 정의가 아닌, 질의 맥락에 따라 유연하게 조합되는 구조**가 필요하다.

---

## 2. 설계: Output Profile 시스템

### 2.1 핵심 개념 — 3계층 매칭

```
[출력 의도 키워드] × [도메인 엔터티] → [컬럼 역할 프로파일]
```

- **1계층 — 출력 의도**: 명세, 정보, 현황, 목록, 내역 등 출력 형태를 나타내는 키워드
- **2계층 — 도메인 엔터티**: 고객, 여신, 수신, 거래, 카드, 외환 등 업무 대상
- **3계층 — 컬럼 역할**: 식별(identifier), 금액(amount), 일자(date), 상태(status), 상세(detail) 등 의미적 역할

### 2.2 동작 흐름

```
사용자: "대출 명세 뽑아줘"
    │
    ├─ 1. 출력 의도 추출: "명세" → output_type: statement
    ├─ 2. 도메인 엔터티 추출: "대출" → entity: loan
    ├─ 3. 프로파일 매칭: (statement, loan) → 매칭 성공
    │     → default_columns: [고객식별, 대출종류, 실행일, 실행금액, 잔액, 금리, 만기일]
    └─ 4. SQL 생성 시 프로파일을 "권장 출력 컬럼"으로 프롬프트에 주입


사용자: "고객 뭐 좀 뽑아줘"
    │
    ├─ 1. 출력 의도 추출: 없음 (모호)
    ├─ 2. 도메인 엔터티 추출: "고객" → entity: customer
    ├─ 3. 프로파일 매칭: (?, customer) → 의도 불명확
    └─ 4. 명확화 질문 생성:
         "어떤 고객 데이터가 필요하신가요?
          1) 고객 기본정보 (이름, 유형, 등급, 가입일)
          2) 고객별 거래 현황 (거래건수, 총금액)
          3) 고객별 상품 보유 현황
          4) 직접 입력해 주세요"


사용자: "뭐 좀 뽑아줘"
    │
    ├─ 1. 출력 의도 추출: 없음
    ├─ 2. 도메인 엔터티 추출: 없음
    ├─ 3. 프로파일 매칭: 실패 (둘 다 불명확)
    └─ 4. 일반 명확화 질문:
         "조금 더 구체적으로 알려주시겠어요?
          예를 들어 '대출 명세', '고객 현황', '이번 달 거래내역' 등으로
          말씀해 주시면 정확한 데이터를 찾아드리겠습니다."
```

### 2.3 매칭 결과별 분기

| 매칭 상태 | 의도 | 엔터티 | 처리 |
|-----------|------|--------|------|
| **완전 매칭** | ✓ | ✓ | 프로파일 기반 권장 컬럼을 SQL 생성 프롬프트에 주입 |
| **엔터티만 매칭** | ✗ | ✓ | 해당 엔터티의 출력 의도 선택지 제시 (명확화 질문) |
| **의도만 매칭** | ✓ | ✗ | 해당 의도에 적합한 엔터티 선택지 제시 (명확화 질문) |
| **매칭 실패** | ✗ | ✗ | 자유 입력 유도형 명확화 질문 |
| **명시적 컬럼 지정** | — | — | 프로파일 무시, 사용자 지정 컬럼 우선 |

---

## 3. 설정 파일 설계: `resources/domain/output_profiles.yaml`

### 3.1 출력 의도 키워드 매핑

```yaml
output_intents:
  statement:  # 명세
    keywords: ["명세", "명세서", "내역서", "스케줄"]
    description: "항목별 상세 데이터를 건별로 나열"
    column_depth: detail     # 상세 컬럼까지 포함

  info:  # 정보
    keywords: ["정보", "인적사항", "기본정보", "프로필"]
    description: "엔터티의 속성 정보를 조회"
    column_depth: standard   # 주요 컬럼만

  summary:  # 현황/요약
    keywords: ["현황", "요약", "총괄", "개요"]
    description: "집계/통계 형태의 요약 데이터"
    column_depth: aggregate  # 집계 컬럼 위주

  list:  # 목록
    keywords: ["목록", "리스트", "건수", "대상"]
    description: "필터 조건에 맞는 건을 나열"
    column_depth: standard

  history:  # 이력
    keywords: ["이력", "내역", "히스토리", "변동"]
    description: "시간순 변동 이력"
    column_depth: detail
```

### 3.2 도메인 엔터티별 컬럼 역할 정의

```yaml
entity_profiles:
  loan:  # 여신(대출)
    entity_keywords: ["대출", "여신", "론", "차입"]
    column_roles:
      identifier: ["고객명/고객번호", "대출종류", "대출번호"]
      amount:     ["실행금액", "대출잔액", "이자금액"]
      rate:       ["적용금리", "연체금리"]
      date:       ["실행일", "만기일", "최종이자납입일"]
      status:     ["대출상태", "연체여부", "연체일수"]
      detail:     ["담보유형", "상환방식", "취급점"]

  deposit:  # 수신(예금)
    entity_keywords: ["예금", "수신", "적금", "예적금", "저축"]
    column_roles:
      identifier: ["고객명/고객번호", "상품명", "계좌번호"]
      amount:     ["잔액", "월납입액", "이자금액"]
      rate:       ["적용금리", "우대금리"]
      date:       ["개설일", "만기일", "최종거래일"]
      status:     ["계좌상태", "자동이체여부"]
      detail:     ["가입기간", "과세구분", "관리점"]

  customer:  # 고객
    entity_keywords: ["고객", "회원", "가입자", "차주", "예금주"]
    column_roles:
      identifier:     ["고객명", "고객번호", "고객유형"]
      classification: ["고객등급", "세그먼트", "직업"]
      date:           ["최초가입일", "최종거래일"]
      status:         ["상태", "휴면여부"]
      detail:         ["관리점", "담당자", "마케팅동의"]

  business:  # 기업/사업자
    entity_keywords: ["사업자", "기업", "법인", "업체", "거래처"]
    column_roles:
      identifier:     ["사업자명", "사업자번호", "법인번호"]
      classification: ["업종", "기업규모", "상장여부"]
      date:           ["설립일", "거래개시일"]
      status:         ["기업상태", "신용등급"]
      detail:         ["대표자명", "소재지", "관리점"]

  transaction:  # 거래
    entity_keywords: ["거래", "입출금", "이체", "송금"]
    column_roles:
      identifier: ["거래번호", "거래유형"]
      amount:     ["거래금액", "수수료", "거래후잔액"]
      date:       ["거래일시", "처리일시"]
      status:     ["처리상태"]
      detail:     ["적요", "거래채널", "상대계좌"]
```

### 3.3 출력 깊이(column_depth)별 포함 역할 규칙

```yaml
profile_rules:
  # column_depth별 포함할 역할
  detail:     ["identifier", "amount", "rate", "date", "status", "detail", "classification"]
  standard:   ["identifier", "amount", "date", "status", "classification"]
  aggregate:  ["identifier", "amount"]  # GROUP BY 대상
```

**동작 원리**:
- `(statement, loan)` 매칭 → column_depth = `detail`
- `detail` 규칙 → identifier + amount + rate + date + status + detail 역할 포함
- loan의 해당 역할 컬럼들 조합 → 고객명/고객번호, 대출종류, 대출번호, 실행금액, 대출잔액, 적용금리, 실행일, 만기일, 대출상태, 연체여부, 담보유형, 상환방식, 취급점

### 3.4 특수 조합 오버라이드

특정 (의도, 엔터티) 조합에 대해 일반 규칙보다 정밀한 권장을 제공한다:

```yaml
overrides:
  - intent: summary
    entity: loan
    note: "여신 현황은 건수/잔액 집계가 기본. 대출종류별 GROUP BY 권장"
    recommended_columns: ["대출종류", "건수", "실행금액합계", "잔액합계", "평균금리"]

  - intent: history
    entity: transaction
    note: "거래 이력은 시간순 정렬 필수. 날짜 조건 필수 적용"
    recommended_columns: ["거래일시", "거래유형", "거래금액", "거래후잔액", "적요"]

  - intent: info
    entity: business
    note: "사업자 정보는 식별+분류 중심. 거래 관계 정보 포함"
    recommended_columns: ["사업자명", "사업자번호", "업종", "기업규모", "대표자명", "설립일", "신용등급"]
```

### 3.5 매칭 실패 시 명확화 질문 템플릿

```yaml
fallback_clarifications:
  # 엔터티만 매칭, 의도 불명확
  entity_only:
    template: |
      {entity_name} 관련 데이터를 찾고 있습니다.
      어떤 형태의 데이터가 필요하신가요?
    choices_from: output_intents  # 의도 선택지 자동 생성

  # 의도만 매칭, 엔터티 불명확
  intent_only:
    template: |
      {intent_description} 형태로 데이터를 준비하겠습니다.
      어떤 업무 데이터를 원하시나요?
    choices_from: entity_profiles  # 엔터티 선택지 자동 생성

  # 둘 다 불명확
  both_unclear:
    template: |
      조금 더 구체적으로 알려주시겠어요?
      예를 들어 "대출 명세", "고객 현황", "이번 달 거래내역" 등으로
      말씀해 주시면 정확한 데이터를 찾아드리겠습니다.
```

---

## 4. 파이프라인 통합

### 4.1 신규 노드: Output Profile Resolver

기존 파이프라인의 Intent Classification과 Context Collection 사이에 위치한다:

```
                    ┌─────────────────────┐
                    │   Preprocessing     │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │ Intent Classification│
                    └──────────┬──────────┘
                               │
                ┌──────────────▼──────────────┐
                │  ★ Output Profile Resolver  │  ← 신규 노드
                │                              │
                │  1. 출력 의도 키워드 매칭     │
                │  2. 도메인 엔터티 매칭        │
                │  3. 프로파일 조합 해석        │
                │                              │
                │  결과:                       │
                │  ├─ 완전 매칭 → column_hints │
                │  ├─ 부분 매칭 → 선택지 생성  │ → Clarifier
                │  └─ 매칭 실패 → 일반 흐름    │
                └──────────────┬──────────────┘
                               │
                    ┌──────────▼──────────┐
                    │  Context Collection  │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │    SQL Generation    │  ← column_hints 주입
                    └──────────┬──────────┘
```

### 4.2 PipelineState 확장

```python
class OutputProfile(BaseModel):
    """출력 프로파일 매칭 결과"""
    output_intent: str | None = None         # statement, info, summary, list, history
    entity_type: str | None = None           # loan, deposit, customer, business, transaction
    column_hints: list[str] = []             # 권장 출력 컬럼 목록 (역할 기반 조합 결과)
    column_depth: str = "standard"           # detail, standard, aggregate
    match_status: str = "none"               # full, entity_only, intent_only, none
    override_note: str | None = None         # 특수 조합 시 추가 안내

class PipelineState(BaseModel):
    # ... 기존 필드 ...
    output_profile: OutputProfile | None = None  # ← 추가
```

### 4.3 SQL Generation 프롬프트 주입

프로파일이 매칭되면, SQL 생성 프롬프트에 **권장 출력 컬럼 섹션**을 동적 삽입한다:

```
## 권장 출력 컬럼 (참고용 — 실제 테이블 컬럼에 매핑하여 사용)

사용자가 "대출 명세"를 요청했습니다.
이 유형의 요청에는 일반적으로 다음 정보가 포함됩니다:
- 고객 식별: 고객명/고객번호, 대출종류, 대출번호
- 금액: 실행금액, 대출잔액
- 금리: 적용금리
- 일자: 실행일, 만기일
- 상태: 대출상태, 연체여부

위 항목을 실제 테이블의 컬럼명에 매핑하여 SELECT 절을 구성하세요.
테이블에 해당 컬럼이 없으면 생략하고, 추가로 유용한 컬럼이 있으면 포함할 수 있습니다.
```

**핵심 설계 원칙: "강제"가 아닌 "권장"**

- 프로파일은 LLM에게 **가이드라인**을 제공할 뿐, 최종 컬럼 선택은 실제 테이블 스키마에 따라 LLM이 판단
- 프로파일에 없는 유용한 컬럼도 포함 가능
- 프로파일에 있지만 테이블에 없는 컬럼은 자연스럽게 생략

---

## 5. 기존 구성 요소와의 역할 분담

### 5.1 resources/domain/ 파일 간 관계

| 구성 요소 | 역할 | 파이프라인 단계 |
|-----------|------|----------------|
| `domain_dictionary.yaml` | 자연어 → 테이블/컬럼/조건 매핑 | 검색 쿼리 생성 (Context Collection) |
| `output_profiles.yaml` | 출력 의도 × 엔터티 → 기대 출력 컬럼 구성 | SQL 생성 가이드 (SQL Generation) |
| `similar_tables.yaml` | 유사 테이블 간 선택 기준 | 테이블 선택 (Table Disambiguation) |
| `example_codes.yaml` | 코드값 → 한국어 의미 매핑 | 결과 포맷팅 (Result Formatting) |

세 파일이 파이프라인의 **서로 다른 단계**에서 **독립적으로** 작동하며,
`output_profiles.yaml`은 `domain_dictionary.yaml`의 도메인 용어 매칭 결과를 **재활용**하여
별도의 NLP 파싱 없이 엔터티를 식별한다.

### 5.2 domain_dictionary.yaml과의 연동

`domain_dictionary.yaml`의 도메인 용어 매칭 결과(카테고리: 고객, 여신, 수신 등)를
Output Profile Resolver가 엔터티 식별에 재활용한다:

```
QueryStrategyBuilder가 "대출" → category: "여신" 으로 매칭
    ↓
Output Profile Resolver가 category: "여신" → entity: loan 으로 변환
    ↓
별도의 키워드 매칭 중복 없이 엔터티 확정
```

### 5.3 Clarifier와의 연동

Output Profile Resolver의 부분 매칭 결과를 Clarifier에 전달하여,
**일반적인 모호성 질문이 아닌 프로파일 기반 구체적 선택지**를 생성한다:

```
기존 Clarifier:
  "어떤 데이터가 필요하신가요?" (LLM이 자유롭게 생성)

프로파일 연동 Clarifier:
  "고객 관련 데이터를 찾고 있습니다. 어떤 형태가 필요하신가요?
   1) 고객 기본정보 (이름, 유형, 등급, 가입일)    ← info + customer
   2) 고객별 거래 현황 (거래건수, 총금액)          ← summary + customer
   3) 고객 목록 (조건별 필터링)                    ← list + customer
   4) 직접 입력해 주세요"
```

---

## 6. 소형 LLM 대응

### 6.1 소형 모델에서의 이점

| 관점 | 대형 LLM (Claude/GPT-4) | 소형 LLM (7B~70B) |
|------|------------------------|--------------------|
| 프로파일 없이 | 맥락 이해로 적절한 컬럼 선택 가능 | 과소/과다 출력 빈발 |
| 프로파일 있을 때 | 가이드로 활용, 추가 판단 가능 | **명시적 컬럼 목록에서 매핑만 수행** → 안정적 |

소형 모델은 "어떤 컬럼을 포함할지 판단"하는 것보다
"주어진 권장 컬럼을 실제 테이블 컬럼에 매핑"하는 것이 훨씬 쉽다.

### 6.2 프롬프트 차이

```
# 소형 모델용 (더 명시적)
아래 권장 컬럼을 반드시 포함하세요. 실제 테이블에 없는 항목만 제외합니다:
- 고객명 → CUST_NM
- 대출종류 → LOAN_TYPE_CD
- 실행금액 → EXEC_AMT
...

# 대형 모델용 (유연성 허용)
아래는 참고용 권장 컬럼입니다. 실제 테이블 구조에 맞게 조정하세요:
- 고객 식별: 고객명/고객번호, 대출종류
- 금액: 실행금액, 잔액
...
```

---

## 7. 확장성 고려

### 7.1 새로운 엔터티 추가

`output_profiles.yaml`에 엔터티 블록을 추가하면 코드 수정 없이 확장 가능:

```yaml
entity_profiles:
  foreign_exchange:  # 외환
    entity_keywords: ["외환", "환전", "송금", "외화"]
    column_roles:
      identifier: ["거래번호", "거래유형"]
      amount:     ["원화금액", "외화금액", "적용환율"]
      date:       ["거래일시", "결제예정일"]
      status:     ["처리상태", "수취확인"]
      detail:     ["통화", "수취인", "송금목적"]
```

### 7.2 사용자 피드백 반영

운영 중 사용자 피드백("이 정보도 포함해주세요")을 수집하여
프로파일을 점진적으로 개선할 수 있다:

```
사용자: "대출 명세에 상환방식도 나왔으면 좋겠어요"
    → loan entity의 detail 역할에 이미 포함되어 있으나
      standard depth에서 누락 → column_depth 조정 또는 override 추가
```

### 7.3 프로파일 미매칭 시 안전망

프로파일이 전혀 매칭되지 않는 경우(새로운 유형의 요청),
기존 LLM 기반 컬럼 선택 로직이 **그대로 동작**한다.
프로파일 시스템은 기존 흐름을 대체하는 것이 아니라 **보강**하는 구조이다.

---

## 8. 구현 우선순위

| 순서 | 작업 | 난이도 | 효과 |
|------|------|--------|------|
| 1 | `output_profiles.yaml` 설정 파일 작성 | 낮음 | 도메인 지식 정의의 기반 |
| 2 | Output Profile Resolver 노드 구현 | 중간 | 핵심 매칭 로직 |
| 3 | SQL Generation 프롬프트에 column_hints 주입 | 낮음 | 즉각적 품질 향상 |
| 4 | Clarifier 연동 (프로파일 기반 선택지) | 중간 | 사용자 경험 향상 |
| 5 | 특수 조합 오버라이드 확장 | 낮음 | 도메인별 정밀 튜닝 |
| 6 | 소형 LLM 전용 프롬프트 분기 | 낮음 | 폐쇄망 대응 |
| 7 | 골든셋에 출력 프로파일 검증 케이스 추가 | 중간 | 품질 보증 |
