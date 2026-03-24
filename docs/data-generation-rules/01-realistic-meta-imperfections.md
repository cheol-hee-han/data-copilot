# 01. 실무 메타데이터 불완전성 가이드

> 실제 은행 정보계 DB의 메타데이터는 교과서처럼 깔끔하지 않다.
> 테스트 데이터가 실무 환경을 재현하려면, **메타 자체의 불완전함**까지 반영해야 한다.

---

## 1. 컬럼 설명(Description)의 현실

### 1.1 설명이 아예 없는 컬럼

실무에서 30~50%의 컬럼은 설명이 비어 있거나 "해당 없음"으로 채워져 있다.
특히 아래 유형이 설명 누락 빈도가 높다:

```
# 설명이 없는 컬럼 예시
TB_LOAN_INFO.ATTR1       — VARCHAR(20)  — (설명 없음)
TB_LOAN_INFO.ATTR2       — VARCHAR(20)  — (설명 없음)
TB_LOAN_INFO.ATTR3       — VARCHAR(50)  — (설명 없음)
TB_CUST_INFO.REMARK      — VARCHAR(500) — "비고"
TB_DEPOSIT_INFO.FLAG1    — CHAR(1)      — (설명 없음)
TB_TRANSACTION.MEMO      — VARCHAR(200) — "메모"
```

**테스트 데이터 반영 방법:**
- ES table_meta에 일부 컬럼의 `desc`를 빈 문자열("")이나 "비고", "기타" 같은 무의미한 설명으로 설정
- ATTR1, ATTR2, FLAG1 같은 범용 컬럼을 일부 테이블에 추가

### 1.2 약어·축약어로만 된 설명

DBA나 개발자가 자신만 아는 약어로 설명을 적어둔 경우:

```
# 약어 투성이 설명 예시
TB_LOAN_INFO.INT_RATE        — "이율"           (적용금리인지 연체금리인지 불명확)
TB_LOAN_INFO.OVDU_RT         — "연체율"         (건수 기준? 금액 기준?)
TB_CUST_INFO.GRD_CD          — "등급"           (고객등급? 신용등급? 자산등급?)
TB_DEPOSIT_INFO.STCD          — "상태"           (계좌상태? 거래상태?)
TB_TRANSACTION.TP_CD          — "유형"           (거래유형이라는 맥락 없이 "유형"만 적힘)
TB_BRANCH_INFO.RGN            — "지역"           (지역코드인지 지역명인지 불명확)
```

**테스트 데이터 반영 방법:**
- ES table_meta에서 일부 컬럼 desc를 의도적으로 축약된 형태로 작성
- 동일 의미지만 다른 약어를 쓰는 컬럼 쌍 생성 (INT_RATE vs INTR_RT vs IR)

### 1.3 설명이 틀리거나 오래된 경우

초기 설계 후 업무가 바뀌었지만 메타는 갱신되지 않은 경우:

```
# 설명과 실제가 다른 예시
TB_CUST_INFO.CUST_TYPE_CD    — 설명: "01:개인, 02:법인"
                                실제: 03(개인사업자), 04(비거주자)도 존재하지만 메타에 없음

TB_LOAN_INFO.LOAN_TYPE_CD    — 설명: "대출구분코드"
                                실제: 01~03 외에 04(할인어음), 05(지급보증) 추가됨

TB_DEPOSIT_INFO.PROD_CD      — 설명: "상품코드 (P001~P005)"
                                실제: P006~P008도 나중에 추가됨
```

**테스트 데이터 반영 방법:**
- 코드 메타에 등록된 값보다 실제 데이터에 더 많은 코드값이 존재하는 상황 생성
- table_meta의 코드 설명에 "01:개인, 02:기업" 만 적고, 실제 데이터에는 03, 04도 존재

### 1.4 동일 의미를 다른 이름으로 부르는 경우

같은 비즈니스 개념이 테이블마다 다른 컬럼명으로 존재:

```
# 같은 의미, 다른 이름
고객번호:   CUST_NO / CUST_ID / CUSTOMER_NO / CST_NO / C_NO
지점코드:   BRCH_CD / BR_CD / BRANCH_CD / DEPT_CD / ORG_CD
거래일자:   TXN_DT / TRADE_DT / TR_DT / DEAL_DT / BIZ_DT
대출금액:   LOAN_AMT / LN_AMT / LEND_AMT / CREDIT_AMT
계좌번호:   ACCT_NO / AC_NO / ACCOUNT_NO / ACNO
상태코드:   STATUS_CD / STCD / STS_CD / STAT_CD / ST
```

**테스트 데이터 반영 방법:**
- 새로 추가하는 테이블에서 기존 테이블과 다른 컬럼명 사용
  (예: TB_CUST_INFO는 CUST_NO, TB_FX_ACCT는 CST_NO)
- ES table_meta의 FK 관계에서 이 불일치를 그대로 반영

---

## 2. 테이블 설명의 현실

### 2.1 테이블 설명이 무의미한 경우

```
# 실무에서 흔한 테이블 설명
TB_CM001          — "공통코드마스터"        (어떤 코드인지 불명확)
TB_LN_BASE        — "여신기본"             (기본이 무엇인지 불명확)
TB_DP_DTL         — "수신상세"             (무엇의 상세인지 불명확)
TB_IF_LOG         — "인터페이스로그"        (어디서 어디로의 인터페이스인지 불명확)
TB_WORK_TEMP      — "업무임시"             (임시 무엇인지 불명확, 그런데 운영에서 사용 중)
```

### 2.2 테이블명이 코드화된 경우

레거시 시스템에서 마이그레이션된 테이블은 이름 자체가 코드:

```
# 레거시 테이블명 예시
TB_D1010          — 수신원장 (D=Deposit, 1010=원장)
TB_L2020          — 여신거래내역 (L=Loan, 2020=거래내역)
TB_C0100          — 고객마스터 (C=Customer, 0100=마스터)
TB_E3010          — 전자금융거래로그 (E=E-banking, 3010=거래로그)

# 코드 규칙을 아는 사람이 퇴사하면 해독 불가능
TB_X9901          — ??? (아무도 모르지만 배치에서 참조 중)
```

**테스트 데이터 반영 방법:**
- 일부 테이블에 레거시 스타일 이름 사용 (예: TB_D1010_DEPOSIT_ACCT)
- 테이블 설명을 의도적으로 모호하게 작성

---

## 3. 코드 메타의 현실

### 3.1 코드 등록이 누락된 값

코드 테이블에는 01~03만 있지만, 실제 데이터에는 09(기타), 99(미분류)가 존재:

```
# code_meta 등록: LOAN_TYPE_CD → 01:신용, 02:담보, 03:보증
# 실제 데이터에 존재하는 값: 01, 02, 03, 04, 05, 09, 99
# 04, 05는 나중에 추가됐고, 09는 "기타", 99는 "마이그레이션 데이터"

# 업무팀은 09를 쓰고, 시스템은 99를 쓰고, 둘 다 "정리 예정"이지만 영원히 안 됨
```

### 3.2 같은 의미를 다른 코드로 표현

```
# 성별을 표현하는 서로 다른 방식
TB_CUST_INFO.GENDER_CD      — M/F
TB_PENSION_MEMBER.SEX_CD     — 1:남, 2:여          (주민번호 7번째 자리 관행)
TB_EBANK_USER.GNDR           — 01:남성, 02:여성     (2자리 코드 표준)

# 여부(Y/N)를 표현하는 서로 다른 방식
TB_LOAN_INFO.OVERDUE_YN      — Y/N
TB_CUST_AML.EDD_필요여부      — 1/0
TB_EBANK_USER.OTP_REG_YN     — 'Y'/'N'
TB_DEPOSIT_INFO.자동연장여부    — 'T'/'F'             (True/False, 개발자 취향)
```

### 3.3 코드값에 공백이나 특수문자

```
# 실무에서 흔한 코드값 오염
CUST_GRADE_CD:  'VIP', 'VIP ', ' VIP'    — 앞뒤 공백 불일치
REGION_CD:      '01', '1', ' 01'          — 자릿수 불일치
STATUS_CD:      'A', 'ACTIVE', 'active'   — 표현 방식 불일치 (레거시 혼재)
```

**테스트 데이터 반영 방법:**
- 일부 코드값에 의도적으로 앞뒤 공백 포함
- 동일 의미의 코드를 테이블마다 다른 형식으로 사용
- code_meta에 등록되지 않은 코드값(09, 99 등)을 실제 데이터에 삽입

---

## 4. FK 관계와 조인의 현실

### 4.1 물리적 FK가 없는 논리적 관계

성능상의 이유로 FK 제약조건을 걸지 않는 경우가 대부분:

```sql
-- 논리적으로는 FK지만 물리적 제약조건 없음
TB_LOAN_INFO.CUST_NO  →  TB_CUST_INFO.CUST_NO     -- FK 제약 없음
TB_TRANSACTION.ACCT_NO →  TB_DEPOSIT_INFO.ACCT_NO   -- FK 제약 없음

-- 결과: 부모 없는 자식 레코드 존재 가능
-- TB_LOAN_INFO에 CUST_NO = 'C999999999' (TB_CUST_INFO에 없는 고객)
-- 원인: 고객 정보 삭제 후 대출 정보가 남은 경우, 배치 타이밍 차이
```

### 4.2 조인 키가 명확하지 않은 경우

```sql
-- 단순한 경우: 컬럼명이 같으므로 조인이 명확
TB_LOAN_INFO.CUST_NO = TB_CUST_INFO.CUST_NO

-- 복잡한 경우: 컬럼명이 다르고 복합키 조인
TB_LOAN_OVERDUE_STAT.BRCH_CD = TB_BRANCH_INFO.BRCH_CD
  AND TB_LOAN_OVERDUE_STAT.LOAN_TYPE_CD = TB_LOAN_INFO.LOAN_TYPE_CD
  AND TB_LOAN_OVERDUE_STAT.BASE_YM = ???  -- 어떤 날짜와 매핑?

-- 더 복잡한 경우: 변환이 필요한 조인
TB_TRANSACTION.TXN_DT (DATE)  vs  TB_LOAN_OVERDUE_STAT.BASE_YM (VARCHAR 'YYYYMM')
-- TO_CHAR(TXN_DT, 'YYYYMM') = BASE_YM  이런 변환 조인 필요
```

### 4.3 동일 컬럼이 여러 테이블을 참조

```
TB_TRANSACTION.BRCH_CD는
  - TB_BRANCH_INFO.BRCH_CD를 참조할 수도 있고
  - 더 상세한 TB_BRANCH_DETAIL.BRCH_CD를 참조할 수도 있고
  - 폐점된 지점은 TB_BRANCH_HIST에만 존재

어느 테이블과 조인해야 하는지 맥락에 따라 달라짐
```

**테스트 데이터 반영 방법:**
- 일부 FK 관계에서 부모 레코드가 없는 고아(orphan) 데이터 삽입
- 날짜↔문자열 변환이 필요한 조인 패턴 추가
- 복합키 조인이 필요한 테이블 쌍 추가

---

## 5. 갱신주기와 시점 불일치

### 5.1 배치 타이밍에 따른 데이터 불일치

```
TB_CUST_INFO        — 일배치 (새벽 2시 기준)
TB_LOAN_INFO        — 일배치 (새벽 3시 기준)
TB_TRANSACTION      — 실시간
TB_LOAN_OVERDUE_STAT — 월배치 (매월 1일 전월말 기준)

문제 상황:
- 3/1 오전에 TB_CUST_INFO를 조회하면 2/28 기준 데이터
- 같은 시점에 TB_TRANSACTION은 실시간이므로 3/1 거래가 존재
- TB_LOAN_OVERDUE_STAT의 최신 데이터는 2월 기준 (3월 데이터는 4/1에 생성)
- 결과: "이번 달 연체율"을 조회하면 데이터가 없거나 전월 데이터만 나옴
```

### 5.2 이력 테이블과 현행 테이블의 관계

```
TB_CUST_INFO        — 현행 (최신 상태만 보관)
TB_CUST_INFO_HIST   — 이력 (변경 시마다 스냅샷 보관)

TB_LOAN_RATE_HIST   — 금리 변동이력 (시작일, 종료일 관리)
TB_LOAN_INFO.INT_RATE — 현재 적용 금리만 보관

주의: 이력 테이블의 최신 레코드 ≠ 현행 테이블의 값 (배치 타이밍 차이)
```

**테스트 데이터 반영 방법:**
- 이력 테이블의 최신 레코드와 현행 테이블 값이 미세하게 다른 케이스 포함
- 월배치 테이블에 "이번 달" 데이터가 아직 없는 상태를 재현
- 배치 시점 차이로 인한 일시적 불일치 데이터 포함
