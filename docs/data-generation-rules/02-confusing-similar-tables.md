# 02. 유사/혼동 테이블 설계 가이드

> 은행 정보계 DB에는 이름·구조·용도가 비슷해 보이지만 목적이 다른 테이블이 대량으로 존재한다.
> NL-to-SQL 시스템이 정확한 테이블을 선택하도록 훈련하려면,
> **의도적으로 혼동을 유발하는 유사 테이블**을 테스트 데이터에 포함해야 한다.

---

## 1. 유사 테이블이 발생하는 원인

### 1.1 원장 vs 집계 vs 통계 vs 이력

하나의 업무 도메인에 대해 **4가지 이상의 관점 테이블**이 존재한다:

```
[대출 도메인 예시]

TB_LOAN_INFO           — 대출 원장 (건별 현재 상태, 가장 상세)
TB_LOAN_OVERDUE_STAT   — 연체 통계 (월말 지점별 집계)
TB_LOAN_MONTHLY_SUM    — 대출 월간 요약 (월별 대출유형별 합산)
TB_LOAN_INFO_HIST      — 대출 이력 (변경 시마다 스냅샷, 과거 상태 추적)
TB_LOAN_DAILY_BAL      — 대출 일별잔액 (일별 잔액 스냅샷, 일배치)
TB_LOAN_EXEC_LOG       — 대출 실행이력 (실행/해지 이벤트만 기록)
```

**혼동 포인트:**
- "이번 달 대출 건수" → TB_LOAN_INFO에서 LOAN_DT 조건? TB_LOAN_MONTHLY_SUM에서 조회?
- "연체율 추이" → TB_LOAN_OVERDUE_STAT? TB_LOAN_INFO에서 직접 계산?
- "작년 3월 기준 대출 잔액" → TB_LOAN_INFO(현재값만 있음)? TB_LOAN_DAILY_BAL?

### 1.2 마스터 vs 서브 vs 부가정보

하나의 엔티티가 여러 테이블로 분산:

```
[고객 도메인 예시]

TB_CUST_INFO           — 고객 마스터 (기본 인적사항)
TB_CUST_DETAIL         — 고객 상세 (직업, 소득, 주소 등 추가 정보)
TB_CUST_CONTACT        — 고객 연락처 (전화, 이메일 — 1:N 관계)
TB_CUST_GRADE_HIST     — 등급 변동이력
TB_CUST_SEGMENT        — 세그먼트 (마케팅용 분류)
TB_CUST_RELATION       — 고객 간 관계 (가족, 보증 등)
TB_CUST_AML            — AML 정보 (자금세탁방지)
```

**혼동 포인트:**
- "고객 연락처" → TB_CUST_INFO에도 전화번호 컬럼이 있고, TB_CUST_CONTACT에도 있음
  (TB_CUST_INFO.PHONE은 대표번호, TB_CUST_CONTACT는 전체 연락처 목록)
- "고객 등급" → TB_CUST_INFO.CUST_GRADE_CD(현재)와 TB_CUST_GRADE_HIST(이력) 중 어디?

### 1.3 운영계 vs 정보계 vs 분석계 테이블 혼재

정보계 DB에 다른 계층의 테이블이 함께 존재:

```
TB_LOAN_INFO           — 정보계 (일배치, 정제된 데이터)
TB_ODS_LOAN_RAW        — ODS (운영계에서 그대로 복제, 원본 그대로)
TB_DM_LOAN_SUMMARY     — 데이터마트 (분석용으로 가공, 비정규화)
TB_RPT_LOAN_MONTHLY    — 리포트 테이블 (보고서용으로 사전 집계)
```

**혼동 포인트:**
- 같은 질문에 대해 4개 테이블 모두 답할 수 있지만, 결과가 미세하게 다를 수 있음
- ODS는 실시간에 가깝지만 정제되지 않음
- DM은 미리 집계되어 있어 빠르지만 상세 드릴다운 불가
- RPT는 보고서 기준으로 가공되어 있어 특수 필터가 이미 적용됨

---

## 2. 실무에서 흔한 유사 테이블 패턴

### 패턴 A: "현행" vs "이력" 쌍

거의 모든 주요 엔티티에 이력 테이블이 존재한다:

```
TB_CUST_INFO        ↔  TB_CUST_INFO_HIST
TB_LOAN_INFO        ↔  TB_LOAN_INFO_HIST
TB_DEPOSIT_INFO     ↔  TB_DEPOSIT_INFO_HIST
TB_LOAN_RATE        ↔  TB_LOAN_RATE_HIST
TB_CUST_GRADE       ↔  TB_CUST_GRADE_HIST
```

**구분 기준:**
| 구분 | 현행 테이블 | 이력 테이블 |
|------|-------------|-------------|
| 데이터 시점 | 최신 상태만 | 변경 시점마다 스냅샷 |
| PK | 업무키 (CUST_NO) | 업무키 + 변경일시 |
| 데이터 건수 | 엔티티 수와 동일 | 엔티티 수 × 변경 횟수 |
| 조회 용도 | "현재 상태" 질문 | "과거 특정 시점" 질문 |
| 예시 질문 | "VIP 고객 몇 명?" | "작년에 VIP였던 고객 몇 명?" |

### 패턴 B: "건별 원장" vs "집계 통계"

```
TB_LOAN_INFO           — 건별 (대출 1건 = 1행)
TB_LOAN_OVERDUE_STAT   — 집계 (지점×대출유형×월 = 1행)
TB_LOAN_MONTHLY_SUM    — 집계 (대출유형×월 = 1행)
```

**구분 기준:**
| 구분 | 건별 원장 | 집계 통계 |
|------|-----------|-----------|
| 데이터 수준 | 개별 건 | 그룹 합산 |
| 주요 컬럼 | 개별 금액, 날짜, 상태 | COUNT, SUM, AVG, RATE |
| 조회 용도 | "특정 고객 대출 목록" | "지점별 연체율" |
| 정확도 | 실시간 기준(일배치) | 집계 기준 시점 데이터 |
| 주의 | 직접 집계하면 느릴 수 있음 | 이미 집계된 결과와 직접 집계 결과 차이 가능 |

### 패턴 C: "일반" vs "특화" 테이블

```
TB_DEPOSIT_INFO        — 수신 계좌 전체 (보통예금, 정기예금, 적금 모두 포함)
TB_TIME_DEPOSIT        — 정기예금만 (추가 컬럼: 약정기간, 만기금리, 자동연장)
TB_INSTALLMENT_SAVING  — 적금만 (추가 컬럼: 회차, 납입예정일, 납입상태)
```

**혼동 포인트:**
- "예금 잔액 합계" → TB_DEPOSIT_INFO만 사용 (전체 포함)
- "정기예금 만기 도래" → TB_TIME_DEPOSIT 필요 (만기일 컬럼이 여기에만 있음)
- "적금 납입 현황" → TB_INSTALLMENT_SAVING 필요
- TB_DEPOSIT_INFO에서 PROD_TYPE_CD로 필터해도 비슷한 결과를 얻을 수 있어서 더 혼란

### 패턴 D: "관리 단위"가 다른 테이블

```
[연체를 바라보는 3가지 관점]

TB_LOAN_INFO           — 대출 건별 연체 정보 (OVERDUE_YN, OVERDUE_DAYS, OVERDUE_AMT)
TB_LOAN_OVERDUE        — 연체 관리 전용 (독촉단계, 연체시작일, 법적조치일 등 상세)
TB_LOAN_OVERDUE_STAT   — 연체 통계 (지점별, 월별 집계)

"연체 대출 목록" → TB_LOAN_INFO WHERE OVERDUE_YN = 'Y'
"연체 독촉 현황" → TB_LOAN_OVERDUE (독촉단계별 현황)
"지점별 연체율" → TB_LOAN_OVERDUE_STAT (사전 집계된 연체율)
```

### 패턴 E: "코드 테이블" 중복

```
TB_COMMON_CODE         — 전사 공통코드 (GROUP_CD + CODE 로 모든 코드 관리)
TB_LOAN_TYPE_CODE      — 여신부서가 별도 관리하는 대출유형코드
TB_PROD_CATEGORY       — 상품부서가 별도 관리하는 상품분류코드

# 같은 대출유형을 조회하는 3가지 경로:
# 1) TB_COMMON_CODE WHERE GROUP_CD = 'LOAN_TYPE'
# 2) TB_LOAN_TYPE_CODE
# 3) TB_PROD_CATEGORY WHERE CATEGORY_LEVEL = 2 AND PARENT_CD = 'LOAN'
# 세 곳의 코드값이 미묘하게 다를 수 있음
```

---

## 3. 테스트 데이터 증강 시 추가해야 할 유사 테이블 목록

현재 프로젝트에 이미 존재하는 6개 테이블을 기반으로,
아래 유사 테이블을 추가하여 혼동 상황을 재현한다:

### 3.1 대출 도메인 확장

```
기존: TB_LOAN_INFO, TB_LOAN_OVERDUE_STAT

추가 권장:
TB_LOAN_INFO_HIST        — 대출 이력 (변경일시 포함, 과거 시점 조회용)
TB_LOAN_MONTHLY_SUM      — 대출 월간 요약 (대출유형×지점×월 집계)
TB_LOAN_DAILY_BAL        — 대출 일별잔액 (대출번호×기준일, 잔액 스냅샷)
TB_LOAN_EXEC_LOG         — 대출 실행/해지 이력 (이벤트 기록)
TB_CREDIT_LOAN_DETAIL    — 신용대출 상세 (CSS점수, 소득증빙유형 등 신용대출 전용 컬럼)
```

**혼동 시나리오:**
- "지점별 대출 잔액" → TB_LOAN_INFO에서 SUM? TB_LOAN_MONTHLY_SUM? TB_LOAN_DAILY_BAL?
- "신용대출 현황" → TB_LOAN_INFO WHERE LOAN_TYPE_CD='01'? TB_CREDIT_LOAN_DETAIL?

### 3.2 수신 도메인 확장

```
기존: TB_DEPOSIT_INFO

추가 권장:
TB_DEPOSIT_INFO_HIST     — 수신 이력
TB_TIME_DEPOSIT          — 정기예금 상세 (약정기간, 자동연장 등)
TB_INSTALLMENT_SAVING    — 적금 상세 (회차별 납입)
TB_DEPOSIT_DAILY_BAL     — 수신 일별잔액 (계좌×기준일)
TB_DEPOSIT_INT_CALC      — 이자 계산 내역 (이자산출일, 적용금리, 이자금액)
```

**혼동 시나리오:**
- "정기예금 평균 금리" → TB_DEPOSIT_INFO에서 PROD 필터? TB_TIME_DEPOSIT?
- "적금 연체 현황" → TB_INSTALLMENT_SAVING.납입상태? TB_DEPOSIT_INFO에는 이 정보 없음

### 3.3 고객 도메인 확장

```
기존: TB_CUST_INFO

추가 권장:
TB_CUST_DETAIL           — 고객 상세 (직업, 연소득, 주소 등)
TB_CUST_INFO_HIST        — 고객 이력
TB_CUST_GRADE_HIST       — 등급 변동이력
TB_CUST_SEGMENT          — 마케팅 세그먼트
TB_CUST_ASSET_SUMMARY    — 고객별 자산/부채 요약 (예금합계, 대출합계, 순자산)
```

**혼동 시나리오:**
- "고객 연소득" → TB_CUST_INFO에는 없고 TB_CUST_DETAIL에만 있음
- "고객별 총 자산" → TB_CUST_ASSET_SUMMARY(사전계산)? 직접 JOIN 후 SUM?
  (두 결과가 배치 타이밍 차이로 다를 수 있음)

### 3.4 거래 도메인 확장

```
기존: TB_TRANSACTION

추가 권장:
TB_TRANSACTION_DETAIL    — 거래 상세 (적요, 수수료, 채널 등 추가 정보)
TB_DAILY_TXN_SUMMARY     — 일별 거래 요약 (지점×거래유형×일 집계)
TB_MONTHLY_TXN_SUMMARY   — 월별 거래 요약 (지점×거래유형×월 집계)
TB_CARD_TXN              — 카드 거래 (카드 결제 내역, TB_TRANSACTION과 별도 관리)
```

**혼동 시나리오:**
- "이번 달 입금 총액" → TB_TRANSACTION에서 SUM? TB_MONTHLY_TXN_SUMMARY?
- "카드 거래" → TB_TRANSACTION에 포함? TB_CARD_TXN이 별도?

---

## 4. ES table_meta에서 유사 테이블 구분을 돕는 설명 작성법

유사 테이블의 ES 메타데이터는 **차이점을 명확히** 기술해야 한다.
단, 실무에서는 이 설명마저 불충분한 경우가 많으므로, 일부는 의도적으로 모호하게 작성한다.

### 잘 구분된 설명 (이상적)

```python
{
    "table_name": "TB_LOAN_INFO",
    "table_description": (
        "대출 원장. 현재 유효한 대출 계약 건별 정보를 관리한다. "
        "1행 = 1대출건. 현재 상태만 보관하며 과거 이력은 TB_LOAN_INFO_HIST 참조. "
        "건별 상세 조회, 현재 잔액 확인에 사용."
    ),
}
{
    "table_name": "TB_LOAN_OVERDUE_STAT",
    "table_description": (
        "여신 연체 통계. 월말 마감 기준 지점별·대출유형별 연체 현황 집계 테이블. "
        "1행 = 1지점×1대출유형×1월. 연체율 추이, 지점간 비교에 사용. "
        "건별 연체 정보는 TB_LOAN_INFO.OVERDUE_YN 또는 TB_LOAN_OVERDUE 참조."
    ),
}
```

### 불충분한 설명 (현실적 — 테스트용으로 일부 의도적 적용)

```python
{
    "table_name": "TB_LOAN_MONTHLY_SUM",
    "table_description": "대출 월간 요약",   # 무엇을 요약한 건지 불명확
}
{
    "table_name": "TB_LOAN_DAILY_BAL",
    "table_description": "대출 일별잔액",     # 개별 건? 전체 합산?
}
{
    "table_name": "TB_CREDIT_LOAN_DETAIL",
    "table_description": "신용대출 상세 정보", # TB_LOAN_INFO와 뭐가 다른지?
}
```

---

## 5. 유사 테이블 선택 판단을 위한 질문-테이블 매핑 예시

에이전트가 올바른 테이블을 선택하는 능력을 테스트하기 위한 매핑:

| 사용자 질문 | 올바른 테이블 | 흔한 오답 | 판단 근거 |
|-------------|--------------|-----------|-----------|
| "현재 VIP 고객 수" | TB_CUST_INFO | TB_CUST_INFO_HIST | "현재"이므로 현행 테이블 |
| "작년에 VIP였다가 강등된 고객" | TB_CUST_GRADE_HIST | TB_CUST_INFO | 과거 시점이므로 이력 테이블 |
| "이번 달 지점별 연체율" | TB_LOAN_OVERDUE_STAT | TB_LOAN_INFO에서 직접 계산 | 이미 집계된 통계 테이블 사용이 적절 |
| "특정 고객의 연체 대출 목록" | TB_LOAN_INFO | TB_LOAN_OVERDUE_STAT | 건별 상세가 필요하므로 원장 |
| "정기예금 만기 도래 현황" | TB_TIME_DEPOSIT | TB_DEPOSIT_INFO | 만기일 컬럼이 TB_TIME_DEPOSIT에만 존재 |
| "고객별 총 예금 잔액" | TB_DEPOSIT_INFO | TB_CUST_ASSET_SUMMARY | 정확한 현재 값은 원장 집계, 빠른 조회는 요약 테이블 |
| "월별 대출 잔액 추이" | TB_LOAN_DAILY_BAL 또는 TB_LOAN_MONTHLY_SUM | TB_LOAN_INFO | 과거 시점 잔액은 현행 테이블로 볼 수 없음 |
| "신용대출 CSS 점수 분포" | TB_CREDIT_LOAN_DETAIL | TB_LOAN_INFO | CSS 점수는 신용대출 상세 테이블에만 존재 |
