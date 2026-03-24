# 05. 실무 데이터 품질 이슈

> 실무 정보계 DB의 데이터는 완벽하지 않다.
> 시스템 마이그레이션, 수작업 입력, 배치 오류, 업무 변경 등으로 인해
> **구조적으로 발생하는 품질 이슈**가 존재한다.
> 테스트 데이터에 이런 이슈를 의도적으로 삽입해야 NL-to-SQL 시스템의 견고성을 검증할 수 있다.

---

## 1. 레거시 마이그레이션 잔재

### 1.1 구 시스템 데이터의 형태 차이

2015년 차세대 시스템 전환 시, 구 시스템 데이터가 그대로 이관된 경우:

```
TB_CUST_INFO에 존재하는 마이그레이션 잔재:

# 구 시스템 고객번호 체계 (8자리 숫자)
CUST_NO = '10045678'      — 구 시스템 (2015년 이전 가입)
CUST_NO = 'C00000123'     — 신 시스템 (2015년 이후 가입)

# 구 시스템 날짜 형식
REG_DT = '2010-01-01'     — 정상
REG_DT = '1900-01-01'     — 구 시스템 기본값 (실제 날짜 불명)
REG_DT = '2000-12-31'     — 마이그레이션 일괄 변환 날짜

# 구 시스템 코드 체계
CUST_TYPE_CD = '01'       — 신 시스템 코드
CUST_TYPE_CD = 'P'        — 구 시스템 코드 (P=Personal, C=Corporate)
                            → 변환 배치에서 누락된 건
```

**시드 데이터 반영:**
- 전체 고객의 10~15%를 "구 시스템 이관" 데이터로 생성
- REG_DT가 특정 날짜(2015-07-01 = 마이그레이션 일괄 변환일)에 집중
- 일부 코드값이 구 체계로 남아 있는 레코드 포함

### 1.2 삭제되지 않은 테스트 데이터

운영 환경에 개발/테스트 데이터가 남아 있는 경우:

```
# 테스트 고객 (삭제되어야 하지만 FK 때문에 삭제 불가)
CUST_NO = 'TEST001', CUST_NM = '테스트고객1'
CUST_NO = 'TEST002', CUST_NM = 'TEST USER'
CUST_NO = 'C99999999', CUST_NM = '시스템테스트'

# 테스트 거래 (1원 거래)
TXN_AMT = 1, TXN_TYPE_CD = '01'   — 계좌 확인용 테스트
TXN_AMT = 0                        — 잔액 조회용 더미 거래
```

**시드 데이터 반영:**
- 2~3건의 테스트성 고객 데이터 삽입
- 금액 0원이나 1원인 거래 내역 포함

---

## 2. 참조 무결성 위반

### 2.1 부모 없는 자식 레코드 (Orphan)

FK 제약조건이 물리적으로 없기 때문에 발생:

```sql
-- 고객이 삭제되었지만 대출이 남은 경우
SELECT l.LOAN_NO, l.CUST_NO
FROM TB_LOAN_INFO l
LEFT JOIN TB_CUST_INFO c ON l.CUST_NO = c.CUST_NO
WHERE c.CUST_NO IS NULL;
-- 결과: 5~10건 존재 (정리 예정이지만 방치)

-- 폐점된 지점의 거래 내역
SELECT t.TXN_NO, t.BRCH_CD
FROM TB_TRANSACTION t
LEFT JOIN TB_BRANCH_INFO b ON t.BRCH_CD = b.BRCH_CD
WHERE b.BRCH_CD IS NULL;
-- 결과: BRCH_CD = '099'(폐점된 지점), '000'(본부), '999'(비대면)
```

**시드 데이터 반영:**
- TB_LOAN_INFO에 TB_CUST_INFO에 없는 CUST_NO를 가진 레코드 3~5건 삽입
- TB_TRANSACTION에 TB_BRANCH_INFO에 없는 BRCH_CD 사용 (999=비대면, 000=본부)

### 2.2 논리적 정합성 위반

데이터 간 업무적으로 맞아야 하는 관계가 깨진 경우:

```sql
-- 대출 실행일이 고객 등록일보다 빠른 경우 (마이그레이션 잔재)
SELECT l.LOAN_NO, l.LOAN_DT, c.REG_DT
FROM TB_LOAN_INFO l
JOIN TB_CUST_INFO c ON l.CUST_NO = c.CUST_NO
WHERE l.LOAN_DT < c.REG_DT;
-- 원인: 구 시스템 대출 이관 시 고객 REG_DT가 마이그레이션 날짜로 덮어씀

-- 해지된 계좌에 이후 거래가 남은 경우
SELECT d.ACCT_NO, d.ACCT_STATUS_CD, t.TXN_DT
FROM TB_DEPOSIT_INFO d
JOIN TB_TRANSACTION t ON d.ACCT_NO = t.ACCT_NO
WHERE d.ACCT_STATUS_CD = '02'  -- 해지
AND t.TXN_DT > d.해지일;       -- 해지 이후 거래
-- 원인: 해지 처리와 거래 기록의 배치 타이밍 차이

-- 만기일이 실행일보다 빠른 대출
SELECT LOAN_NO, LOAN_DT, MTRTY_DT
FROM TB_LOAN_INFO
WHERE MTRTY_DT < LOAN_DT;
-- 원인: 구 시스템 날짜 변환 오류 (1~2건 존재)
```

**시드 데이터 반영:**
- 대출일 < 고객등록일인 레코드 2~3건
- 해지 계좌에 해지 전후 거래가 모두 있는 케이스
- 만기일 < 실행일인 레코드 1건

---

## 3. 중복과 불일치

### 3.1 동일 고객의 중복 등록

```sql
-- 같은 사람이 다른 CUST_NO로 2개 등록
CUST_NO = 'C00000100', CUST_NM = '김영희', REG_DT = '2018-03-15'
CUST_NO = 'C00000487', CUST_NM = '김영희', REG_DT = '2020-06-20'
-- 원인: 지점 방문 시 기존 고객 검색 없이 신규 등록

-- 기업 고객의 중복 (사업자번호 변경, 법인 분할 등)
CUST_NO = 'C00000200', CUST_NM = '(주)한국전자', CUST_TYPE_CD = '02'
CUST_NO = 'C00000201', CUST_NM = '한국전자(주)', CUST_TYPE_CD = '02'
-- 같은 회사지만 이름 표기가 달라 별도 등록
```

### 3.2 집계값과 원장의 불일치

```sql
-- TB_LOAN_OVERDUE_STAT의 집계값과 TB_LOAN_INFO 직접 집계가 다름
-- 원인: 통계는 월말 마감 기준, 원장은 일배치 기준

-- 통계 테이블: 202602 기준 강남지점 신용대출 연체건수 = 5건
SELECT OVERDUE_CNT FROM TB_LOAN_OVERDUE_STAT
WHERE BASE_YM = '202602' AND BRCH_CD = '002' AND LOAN_TYPE_CD = '01';

-- 원장 직접 집계: 현재 기준 = 4건 (1건이 3월에 정상 회복)
SELECT COUNT(*) FROM TB_LOAN_INFO l
JOIN TB_CUST_INFO c ON l.CUST_NO = c.CUST_NO
WHERE c.BRCH_CD = '002' AND l.LOAN_TYPE_CD = '01' AND l.OVERDUE_YN = 'Y';
```

**시드 데이터 반영:**
- TB_LOAN_OVERDUE_STAT의 연체건수와 TB_LOAN_INFO 직접 집계를 **의도적으로 불일치**시킴
  (통계는 월말 기준 확정, 원장은 이후 변동 반영)
- 동명이인 고객 2~3쌍 포함 (이름 같고 CUST_NO 다름)

---

## 4. NULL 패턴과 기본값 문제

### 4.1 NULL vs 빈 문자열 vs 기본값 혼용

```
같은 "정보 없음"을 표현하는 3가지 방식이 혼재:

TB_CUST_INFO.GENDER_CD:
  NULL          — 기업고객 (성별 해당 없음)
  ''            — 미입력 (개인인데 안 넣은 경우)
  'U'           — Unknown (구 시스템 이관 데이터)

TB_CUST_DETAIL.ANNUAL_INCOME:
  NULL          — 미신고
  0             — 무소득 (전업주부, 학생)
  -1            — 구 시스템 기본값 (미확인 표시)

TB_LOAN_INFO.MTRTY_DT:
  NULL          — 마이너스통장 등 만기 미확정 상품
  '9999-12-31'  — 구 시스템 "무한대" 표현
  '2099-12-31'  — 개발자가 임의로 넣은 먼 미래 날짜
```

### 4.2 선택적 컬럼의 NULL 비율 현실

```
테이블.컬럼                   NULL 비율   원인
──────────────────────────   ──────────  ──────────────
TB_CUST_INFO.GENDER_CD       15%         기업고객
TB_CUST_INFO.AGE_GRP_CD      15%         기업고객
TB_CUST_INFO.CUST_GRADE_CD    2%         신규 고객 (분기 평가 전)
TB_LOAN_INFO.MTRTY_DT        10%         마이너스통장, 한도대출
TB_DEPOSIT_INFO.PROD_NM        5%        레거시 데이터
TB_TRANSACTION.BRCH_CD         0%        항상 채워짐 (비대면=999)
TB_TRANSACTION.TXN_TM          0%        항상 채워짐 (배치=000000)
TB_CUST_DETAIL.ANNUAL_INCOME  30%        미신고
TB_CUST_DETAIL.OCCUPATION     20%        미기재
TB_CUST_DETAIL.EMAIL          40%        비등록
TB_CUST_CONTACT.마케팅동의     0%         NOT NULL (법적 필수)
```

---

## 5. 데이터 타입 불일치와 형변환 이슈

### 5.1 날짜 표현의 다양성

```
같은 "날짜"를 저장하는 서로 다른 방식:

TB_CUST_INFO.REG_DT:            DATE       ('2024-03-15')
TB_LOAN_OVERDUE_STAT.BASE_YM:   VARCHAR(6) ('202403')
TB_TRANSACTION.TXN_DT:          DATE       ('2024-03-15')
TB_TRANSACTION.TXN_TM:          VARCHAR(6) ('143025' = 14시30분25초)
TB_EBANK_AUTH_LOG.AUTH_DTTM:     TIMESTAMP  ('2024-03-15 14:30:25')
TB_FINANCIAL_STAT.기준년월:       VARCHAR(6) ('202403')
TB_DAILY_TXN_SUMMARY.BIZ_DT:    VARCHAR(8) ('20240315')

-- 조인 시 형변환 필요:
WHERE TO_CHAR(TXN_DT, 'YYYYMM') = BASE_YM
WHERE TO_DATE(BIZ_DT, 'YYYYMMDD') = TXN_DT
```

### 5.2 금액 단위 불일치

```
같은 "금액"인데 단위가 다른 경우:

TB_LOAN_INFO.LOAN_AMT:          원 단위 (50000000 = 5천만원)
TB_LOAN_OVERDUE_STAT.TOTAL_LOAN_AMT: 원 단위
TB_FINANCIAL_STAT.금액:           천원 단위 (50000 = 5천만원)
TB_MGMT_INDEX.지표값 (NIM 등):    % 단위 (1.85)
TB_BIS_CAPITAL.위험가중자산:       백만원 단위

-- 레거시 테이블에서 간혹:
TB_RPT_LOAN_MONTHLY.대출금액:     만원 단위 (5000 = 5천만원)

-- 실수: 단위를 모르고 JOIN하면 수십~수만 배 차이
```

### 5.3 문자열 숫자(VARCHAR)로 저장된 숫자값

```
# 숫자인데 VARCHAR로 저장된 경우 (정렬·비교·집계 시 문제)
TB_CUST_INFO.AGE_GRP_CD:  VARCHAR(2) — '20', '30', '40'
  → ORDER BY AGE_GRP_CD → 문자열 정렬이므로 '9' > '60' 문제 없지만 주의 필요

TB_TRANSACTION.TXN_TM:    VARCHAR(6) — '093025'
  → 시간 비교 시 문자열 비교로도 작동 (HHMMSS 형태이므로)
  → 하지만 시간 연산(+ 1시간)은 불가

TB_LOAN_OVERDUE_STAT.BASE_YM: VARCHAR(6) — '202403'
  → 월 비교: '202403' > '202312' → 문자열 비교로 작동
  → 월 연산(- 3개월)은 불가, TO_DATE 변환 필요
```

---

## 6. 실무 데이터 품질 이슈 체크리스트

테스트 데이터 생성 시 아래 이슈를 의도적으로 삽입하여 시스템 견고성을 검증:

```
□ 메타에 없는 코드값 (09:기타, 99:미분류)
□ 구 시스템 코드 잔재 ('P' = 개인, 'C' = 기업)
□ 컬럼 설명 누락 또는 모호 (ATTR1, FLAG1, STCD)
□ 동일 의미 다른 컬럼명 (CUST_NO vs CST_NO)
□ 부모 없는 자식 레코드 (존재하지 않는 CUST_NO 참조)
□ 동명이인 / 중복 등록 고객
□ 날짜 < 논리적 최소일 (1900-01-01, 마이그레이션 기본값)
□ 만기일 < 시작일 (날짜 변환 오류)
□ 집계 테이블과 원장 불일치 (배치 타이밍)
□ NULL vs '' vs 기본값 혼용
□ 날짜 형식 혼재 (DATE vs VARCHAR YYYYMM vs VARCHAR YYYYMMDD)
□ 금액 단위 차이 (원 vs 천원 vs 만원)
□ 테스트 데이터 잔재 (CUST_NM='테스트', TXN_AMT=1)
□ 공백 포함 코드값 ('VIP ' vs 'VIP')
□ Y/N 표현 불일치 (Y/N vs 1/0 vs T/F)
□ 비대면 거래 지점코드 (999, 000 — BRANCH_INFO에 없음)
□ 폐점 지점의 과거 거래 데이터
□ 당월 미생성 통계 (월배치 전 시점)
□ 이력 테이블 최신값 ≠ 현행 테이블 값
□ 금리 0% 레코드 (직원 우대, 정책 대출)
```
