# C5. 마트 — 고객분석 (MRC, 30테이블)

**주제코드:** MRC
**도메인약어:** MRC
**테이블 수:** 30
**최종갱신:** 2026-04-21
**주제영역 범위:** 고객(Member/Customer) 분석 마트. 세그먼트·코호트·활성도·수익성·LTV·RFM·이탈·유치 채널. CRM/전략기획 활용.

> **마트 특성:** 모두 유형 S(Summary). 원천은 CSC/CSI/CMG 등 고객 주제영역 M/L 테이블. 회계 월마감 후 배치 생성.

---

## TB_ADW_MRC001S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRC001S |
| 테이블한글명 | MRC_고객수월 |
| 주제영역 | 마트 |
| 도메인 | MRC |
| 유형 | S (Summary) |
| 적재주기 | 월배치 |
| 예상건수 | 약 450 |

**[테이블 설명]**

```
[엔티티정의]
월말 기준 전체 고객 수 집계. 개인/법인/개인사업자 구분. 전월대비·YoY 증감. 경영보고 핵심 지표.
```

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 2 | CUST_TCD | 고객구분 | CHAR | 1 | N | Y | I:개인 C:법인 S:개인사업자 P:공공 A:전체 | 상동 |
| 3 | TOT_CNT | 총고객수 | INT |  | Y | N | 월말 총고객수 | 상동 |
| 4 | NEW_CNT | 신규 | INT |  | Y | N | 월 신규 가입 | 상동 |
| 5 | TERM_CNT | 해지 | INT |  | Y | N | 월 해지 | 상동 |
| 6 | NET_INC | 순증 | INT |  | Y | N | 순증 | 상동 |
| 7 | MOM_RTO | 전월비 | NUMERIC | 8,4 | Y | N | 전월 대비 증감률 | 상동 |
| 8 | YOY_RTO | 전년비 | NUMERIC | 8,4 | Y | N | 전년 동월 대비 | 상동 |
| 9 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 10 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRC002S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRC002S |
| 테이블한글명 | MRC_활성고객 |
| 주제영역 | 마트 |
| 도메인 | MRC |
| 유형 | S (Summary) |
| 적재주기 | 월배치 |
| 예상건수 | 약 4,500 |

**[테이블 설명]**

```
[엔티티정의]
활성고객 집계. 기준월 이내 거래 실적 기준 활성/비활성/휴면 구분. 활성 정의는 채널별 상이.
```

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 2 | ACT_TCD | 활성유형 | CHAR | 1 | N | Y | A:활성 I:비활성 D:휴면후보 M:휴면 | 상동 |
| 3 | CUST_CNT | 고객수 | INT |  | Y | N | 해당 유형 고객수 | 상동 |
| 4 | RTO | 비중 | NUMERIC | 5,2 | Y | N | 전체 대비 비중 | 상동 |
| 5 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 6 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRC003S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRC003S |
| 테이블한글명 | MRC_세그먼트분포 |
| 주제영역 | 마트 |
| 도메인 | MRC |
| 유형 | S (Summary) |
| 적재주기 | 월배치 |
| 예상건수 | 약 120,000 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | SEG_CD | 세그먼트 | VARCHAR | 20 | N | Y | 세그먼트 코드 | 상동 |
| 2 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 3 | CUST_CNT | 고객수 | INT |  | Y | N | 세그먼트 고객수 | 상동 |
| 4 | AUM_TOT | 총AUM | NUMERIC | 18,2 | Y | N | 총 AUM | 상동 |
| 5 | PROFIT_TOT | 총이익 | NUMERIC | 18,2 | Y | N | 세그먼트 총 이익 | 상동 |
| 6 | AUM_PER | 인당AUM | NUMERIC | 18,2 | Y | N | 인당 AUM | 상동 |
| 7 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 8 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRC004S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRC004S |
| 테이블한글명 | MRC_신규유치월 |
| 주제영역 | 마트 |
| 도메인 | MRC |
| 유형 | S (Summary) |
| 적재주기 | 월배치 |
| 예상건수 | 약 15,000 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 2 | CH_CD | 채널 | CHAR | 2 | N | Y | 01:창구 02:앱 03:인뱅 04:제휴 05:콜센터 | 상동 |
| 3 | NEW_CNT | 신규수 | INT |  | Y | N | 월 신규 고객수 | 상동 |
| 4 | ACQ_COST | 유치비용 | NUMERIC | 18,2 | Y | N | 총 유치 비용 | 상동 |
| 5 | CAC | CAC | NUMERIC | 18,2 | Y | N | 고객획득비용(인당) | 상동 |
| 6 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 7 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRC005S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRC005S |
| 테이블한글명 | MRC_이탈월 |
| 주제영역 | 마트 |
| 도메인 | MRC |
| 유형 | S (Summary) |
| 적재주기 | 월배치 |
| 예상건수 | 약 15,000 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 2 | CHURN_TCD | 이탈유형 | CHAR | 2 | N | Y | 01:전체해지 02:휴면전환 03:주거래이탈 04:PB이탈 05:VIP이탈 | 상동 |
| 3 | CHURN_CNT | 이탈수 | INT |  | Y | N | 월 이탈수 | 상동 |
| 4 | CHURN_AUM | 이탈AUM | NUMERIC | 18,2 | Y | N | 이탈 AUM | 상동 |
| 5 | CHURN_RTO | 이탈률 | NUMERIC | 5,2 | Y | N | 월 이탈률 | 상동 |
| 6 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 7 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRC006S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRC006S |
| 테이블한글명 | MRC_연령대분포 |
| 주제영역 | 마트 |
| 도메인 | MRC |
| 유형 | S (Summary) |
| 적재주기 | 월배치 |
| 예상건수 | 약 4,500 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 2 | AGE_GRP | 연령대 | CHAR | 2 | N | Y | 10/20/30/40/50/60/70+ | 상동 |
| 3 | CUST_CNT | 고객수 | INT |  | Y | N | 고객수 | 상동 |
| 4 | RTO | 비중 | NUMERIC | 5,2 | Y | N | 비중 | 상동 |
| 5 | AUM_TOT | AUM | NUMERIC | 18,2 | Y | N | 연령대 AUM | 상동 |
| 6 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 7 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRC007S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRC007S |
| 테이블한글명 | MRC_지역분포 |
| 주제영역 | 마트 |
| 도메인 | MRC |
| 유형 | S (Summary) |
| 적재주기 | 월배치 |
| 예상건수 | 약 12,000 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 2 | REGION_CD | 지역 | CHAR | 5 | N | Y | 지역 | 상동 |
| 3 | CUST_CNT | 고객수 | INT |  | Y | N | 고객수 | 상동 |
| 4 | AUM_TOT | AUM | NUMERIC | 18,2 | Y | N | AUM | 상동 |
| 5 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 6 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRC008S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRC008S |
| 테이블한글명 | MRC_직업분포 |
| 주제영역 | 마트 |
| 도메인 | MRC |
| 유형 | S (Summary) |
| 적재주기 | 월배치 |
| 예상건수 | 약 6,000 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 2 | OCCUP_CD | 직업 | CHAR | 3 | N | Y | 직업 코드 | 상동 |
| 3 | CUST_CNT | 고객수 | INT |  | Y | N | 고객수 | 상동 |
| 4 | AVG_INC | 평균소득 | NUMERIC | 18,2 | Y | N | 평균 신고 소득 | 상동 |
| 5 | AUM_PER | 인당AUM | NUMERIC | 18,2 | Y | N | 인당 AUM | 상동 |
| 6 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 7 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRC009S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRC009S |
| 테이블한글명 | MRC_수익성분포 |
| 주제영역 | 마트 |
| 도메인 | MRC |
| 유형 | S (Summary) |
| 적재주기 | 월배치 |
| 예상건수 | 약 2,500 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 2 | PROFIT_TIER | 수익등급 | CHAR | 1 | N | Y | 1:A 2:B 3:C 4:D 5:손실 | 상동 |
| 3 | CUST_CNT | 고객수 | INT |  | Y | N | 고객수 | 상동 |
| 4 | PROFIT_SUM | 수익합 | NUMERIC | 18,2 | Y | N | 해당 등급 총 수익 | 상동 |
| 5 | CONTRIB_RTO | 기여도 | NUMERIC | 5,2 | Y | N | 기여 비중 | 상동 |
| 6 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 7 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRC010S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRC010S |
| 테이블한글명 | MRC_RFM분석 |
| 주제영역 | 마트 |
| 도메인 | MRC |
| 유형 | S (Summary) |
| 적재주기 | 월배치 |
| 예상건수 | 약 18,500,000 |

**[테이블 설명]**

```
[엔티티정의]
RFM 세그먼트 (Recency·Frequency·Monetary). 고객별 최근성·빈도·금액 점수(1~5). 125개 조합 셀 분류.
```

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | CSN | 고객 | NUMERIC | 10 | N | Y | 고객 | 상동 |
| 2 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 3 | R_SCORE | R점수 | CHAR | 1 | Y | N | 1~5 | 상동 |
| 4 | F_SCORE | F점수 | CHAR | 1 | Y | N | 1~5 | 상동 |
| 5 | M_SCORE | M점수 | CHAR | 1 | Y | N | 1~5 | 상동 |
| 6 | CELL_CD | 셀코드 | VARCHAR | 10 | Y | N | RFM 셀(RFM:555 등) | 상동 |
| 7 | PERSONA | 페르소나 | VARCHAR | 50 | Y | N | Champion/Loyal/AtRisk/Lost/NewBie | 상동 |
| 8 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 9 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRC011S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRC011S |
| 테이블한글명 | MRC_LTV분포 |
| 주제영역 | 마트 |
| 도메인 | MRC |
| 유형 | S (Summary) |
| 적재주기 | 분기배치 |
| 예상건수 | 약 2,500 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YQ | 기준분기 | CHAR | 6 | N | Y | 기준 분기 | 상동 |
| 2 | LTV_TIER | LTV등급 | CHAR | 1 | N | Y | 1~5 | 상동 |
| 3 | CUST_CNT | 고객수 | INT |  | Y | N | 고객수 | 상동 |
| 4 | LTV_TOT | LTV총액 | NUMERIC | 18,2 | Y | N | 등급 총 LTV | 상동 |
| 5 | AVG_LTV | 인당LTV | NUMERIC | 18,2 | Y | N | 인당 LTV | 상동 |
| 6 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 7 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRC012S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRC012S |
| 테이블한글명 | MRC_VIP고객수 |
| 주제영역 | 마트 |
| 도메인 | MRC |
| 유형 | S (Summary) |
| 적재주기 | 월배치 |
| 예상건수 | 약 2,500 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 2 | VIP_GRD | VIP등급 | CHAR | 2 | N | Y | 01:VVIP 02:VIP 03:프리미엄 04:골드 | 상동 |
| 3 | CUST_CNT | 고객수 | INT |  | Y | N | 등급 고객수 | 상동 |
| 4 | AUM_TOT | AUM | NUMERIC | 18,2 | Y | N | 총 AUM | 상동 |
| 5 | PROFIT_TOT | 이익 | NUMERIC | 18,2 | Y | N | 총 이익 | 상동 |
| 6 | PB_CNT | PB할당수 | INT |  | Y | N | PB 전담수 | 상동 |
| 7 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 8 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRC013S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRC013S |
| 테이블한글명 | MRC_휴면고객 |
| 주제영역 | 마트 |
| 도메인 | MRC |
| 유형 | S (Summary) |
| 적재주기 | 월배치 |
| 예상건수 | 약 1,200 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 2 | DORMANT_YRS | 휴면연수 | CHAR | 1 | N | Y | 1/2/3/4/5+ | 상동 |
| 3 | CUST_CNT | 고객수 | INT |  | Y | N | 해당 연수 고객수 | 상동 |
| 4 | TOT_AMT | 휴면잔액 | NUMERIC | 18,2 | Y | N | 휴면 잔액 합 | 상동 |
| 5 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 6 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRC014S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRC014S |
| 테이블한글명 | MRC_생존분포 |
| 주제영역 | 마트 |
| 도메인 | MRC |
| 유형 | S (Summary) |
| 적재주기 | 분기배치 |
| 예상건수 | 약 1,200 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YQ | 기준분기 | CHAR | 6 | N | Y | 기준 분기 | 상동 |
| 2 | TENURE_BUCKET | 거래연수구간 | CHAR | 2 | N | Y | 01:1년미만 02:1-3 03:3-5 04:5-10 05:10년이상 | 상동 |
| 3 | CUST_CNT | 고객수 | INT |  | Y | N | 구간 고객수 | 상동 |
| 4 | SURV_RTO | 생존율 | NUMERIC | 5,2 | Y | N | 해당 구간 생존율 | 상동 |
| 5 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 6 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRC015S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRC015S |
| 테이블한글명 | MRC_코호트분석 |
| 주제영역 | 마트 |
| 도메인 | MRC |
| 유형 | S (Summary) |
| 적재주기 | 월배치 |
| 예상건수 | 약 380,000 |

**[테이블 설명]**

```
[엔티티정의]
가입월(코호트) 기준 잔존·활성·수익 추적. 코호트별 리텐션 커브. 마케팅·상품전략 핵심 지표.
```

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | COHORT_YM | 가입월 | CHAR | 6 | N | Y | 가입 코호트 월 | 상동 |
| 2 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 관찰 월 | 상동 |
| 3 | MONTHS_OUT | 경과월 | INT |  | Y | N | 가입 후 경과 월 | 상동 |
| 4 | INIT_CNT | 초기수 | INT |  | Y | N | 코호트 초기 고객수 | 상동 |
| 5 | RETAINED | 잔존수 | INT |  | Y | N | 관찰월 잔존 수 | 상동 |
| 6 | RETAIN_RTO | 잔존율 | NUMERIC | 5,2 | Y | N | 잔존 비율 | 상동 |
| 7 | COHORT_AUM | 코호트AUM | NUMERIC | 18,2 | Y | N | 코호트 AUM | 상동 |
| 8 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 9 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRC016S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRC016S |
| 테이블한글명 | MRC_교차거래 |
| 주제영역 | 마트 |
| 도메인 | MRC |
| 유형 | S (Summary) |
| 적재주기 | 월배치 |
| 예상건수 | 약 18,500,000 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | CSN | 고객 | NUMERIC | 10 | N | Y | 고객 | 상동 |
| 2 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 3 | PD_HOLD_CNT | 보유상품수 | INT |  | Y | N | 보유 상품 수 | 상동 |
| 4 | DEPO_YN | 수신보유 | CHAR | 1 | Y | N | Y:보유 | 상동 |
| 5 | LOAN_YN | 여신보유 | CHAR | 1 | Y | N | Y:보유 | 상동 |
| 6 | CARD_YN | 카드보유 | CHAR | 1 | Y | N | Y:보유 | 상동 |
| 7 | FUND_YN | 펀드보유 | CHAR | 1 | Y | N | Y:보유 | 상동 |
| 8 | FX_YN | 외환보유 | CHAR | 1 | Y | N | Y:보유 | 상동 |
| 9 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 10 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRC017S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRC017S |
| 테이블한글명 | MRC_디지털전환율 |
| 주제영역 | 마트 |
| 도메인 | MRC |
| 유형 | S (Summary) |
| 적재주기 | 월배치 |
| 예상건수 | 약 450 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 2 | DIG_ACT_CNT | 디지털활성수 | INT |  | Y | N | 디지털 활성 고객수 | 상동 |
| 3 | DIG_ACT_RTO | 디지털비율 | NUMERIC | 5,2 | Y | N | 디지털 활성 비율 | 상동 |
| 4 | OMNI_CNT | 옴니채널수 | INT |  | Y | N | 2채널 이상 활용 고객수 | 상동 |
| 5 | DIG_ONLY_CNT | 디지털전용 | INT |  | Y | N | 오직 디지털만 | 상동 |
| 6 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 7 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRC018S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRC018S |
| 테이블한글명 | MRC_유치채널성과 |
| 주제영역 | 마트 |
| 도메인 | MRC |
| 유형 | S (Summary) |
| 적재주기 | 월배치 |
| 예상건수 | 약 15,000 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 2 | CH_CD | 채널 | CHAR | 2 | N | Y | 유치 채널 | 상동 |
| 3 | NEW_CNT | 신규수 | INT |  | Y | N | 월 신규 | 상동 |
| 4 | SURV_3M | 3개월생존수 | INT |  | Y | N | 3개월 잔존 | 상동 |
| 5 | AVG_3M_AUM | 3개월인당AUM | NUMERIC | 18,2 | Y | N | 3개월 차 인당 AUM | 상동 |
| 6 | ROI | ROI | NUMERIC | 8,4 | Y | N | 채널 ROI | 상동 |
| 7 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 8 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRC019S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRC019S |
| 테이블한글명 | MRC_크로스셀성과 |
| 주제영역 | 마트 |
| 도메인 | MRC |
| 유형 | S (Summary) |
| 적재주기 | 월배치 |
| 예상건수 | 약 45,000 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 2 | SRC_PDN | 기존상품 | VARCHAR | 10 | N | Y | 보유 상품 | 상동 |
| 3 | TGT_PDN | 추천상품 | VARCHAR | 10 | N | Y | 추천 상품 | 상동 |
| 4 | OFFERED | 오퍼수 | INT |  | Y | N | 제안 수 | 상동 |
| 5 | CONVERTED | 전환수 | INT |  | Y | N | 실제 가입 | 상동 |
| 6 | CONV_RTO | 전환율 | NUMERIC | 5,2 | Y | N | 전환율 | 상동 |
| 7 | LIFT | Lift | NUMERIC | 8,4 | Y | N | 기저 대비 Lift | 상동 |
| 8 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 9 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRC020S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRC020S |
| 테이블한글명 | MRC_이탈예측적중 |
| 주제영역 | 마트 |
| 도메인 | MRC |
| 유형 | S (Summary) |
| 적재주기 | 월배치 |
| 예상건수 | 약 450 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 2 | MODEL_VER | 모델버전 | VARCHAR | 20 | N | Y | 모델 버전 | 상동 |
| 3 | PRED_CHURN | 예측이탈수 | INT |  | Y | N | 예측 이탈 수 | 상동 |
| 4 | ACT_CHURN | 실제이탈수 | INT |  | Y | N | 실제 이탈 수 | 상동 |
| 5 | PRECISION | 정밀도 | NUMERIC | 5,4 | Y | N | Precision | 상동 |
| 6 | RECALL | 재현율 | NUMERIC | 5,4 | Y | N | Recall | 상동 |
| 7 | AUC | AUC | NUMERIC | 5,4 | Y | N | AUC | 상동 |
| 8 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 9 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRC021S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRC021S |
| 테이블한글명 | MRC_유형별수익 |
| 주제영역 | 마트 |
| 도메인 | MRC |
| 유형 | S (Summary) |
| 적재주기 | 월배치 |
| 예상건수 | 약 12,000 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 2 | CUST_TCD | 고객유형 | CHAR | 1 | N | Y | I/C/S/P | 상동 |
| 3 | SEG_CD | 세그먼트 | VARCHAR | 20 | N | Y | 세그먼트 | 상동 |
| 4 | NII_SUM | NII | NUMERIC | 18,2 | Y | N | 순이자수익 | 상동 |
| 5 | FEE_SUM | 수수료 | NUMERIC | 18,2 | Y | N | 수수료 | 상동 |
| 6 | TOT_PROFIT | 총이익 | NUMERIC | 18,2 | Y | N | 총 이익 | 상동 |
| 7 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 8 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRC022S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRC022S |
| 테이블한글명 | MRC_NPS분포 |
| 주제영역 | 마트 |
| 도메인 | MRC |
| 유형 | S (Summary) |
| 적재주기 | 월배치 |
| 예상건수 | 약 1,500 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 2 | NPS_CAT | NPS | CHAR | 1 | N | Y | P/N/D | 상동 |
| 3 | CUST_CNT | 고객수 | INT |  | Y | N | 응답자 수 | 상동 |
| 4 | NPS_SCORE | NPS점수 | NUMERIC | 6,2 | Y | N | NPS 점수 | 상동 |
| 5 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 6 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRC023S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRC023S |
| 테이블한글명 | MRC_상담분포 |
| 주제영역 | 마트 |
| 도메인 | MRC |
| 유형 | S (Summary) |
| 적재주기 | 월배치 |
| 예상건수 | 약 3,500 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 2 | CH_CD | 채널 | CHAR | 2 | N | Y | 상담 채널 | 상동 |
| 3 | CAT_CD | 분류 | CHAR | 3 | N | Y | 상담 분류 | 상동 |
| 4 | CNSL_CNT | 상담건수 | INT |  | Y | N | 월 상담수 | 상동 |
| 5 | AVG_DURATION | 평균시간 | NUMERIC | 10,2 | Y | N | 평균 통화/상담 초 | 상동 |
| 6 | SAT_AVG | 평균만족도 | NUMERIC | 5,2 | Y | N | 평균 만족도 | 상동 |
| 7 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 8 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRC024S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRC024S |
| 테이블한글명 | MRC_민원분포 |
| 주제영역 | 마트 |
| 도메인 | MRC |
| 유형 | S (Summary) |
| 적재주기 | 월배치 |
| 예상건수 | 약 1,500 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 2 | SOURCE_CD | 접수경로 | CHAR | 2 | N | Y | 01~05 | 상동 |
| 3 | COMP_CNT | 민원수 | INT |  | Y | N | 월 민원 | 상동 |
| 4 | AVG_DAYS_TO_CLOSE | 평균종결일 | NUMERIC | 6,2 | Y | N | 평균 종결 일수 | 상동 |
| 5 | COMP_RATIO | 고객대비비율 | NUMERIC | 8,6 | Y | N | 10만명당 민원수 | 상동 |
| 6 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 7 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRC025S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRC025S |
| 테이블한글명 | MRC_복수상품보유 |
| 주제영역 | 마트 |
| 도메인 | MRC |
| 유형 | S (Summary) |
| 적재주기 | 월배치 |
| 예상건수 | 약 3,500 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 2 | PROD_CNT | 보유상품수 | INT |  | N | Y | 1~10+ | 상동 |
| 3 | CUST_CNT | 고객수 | INT |  | Y | N | 해당 보유수 고객수 | 상동 |
| 4 | AVG_AUM | 평균AUM | NUMERIC | 18,2 | Y | N | 평균 AUM | 상동 |
| 5 | AVG_PROFIT | 평균이익 | NUMERIC | 18,2 | Y | N | 평균 이익 | 상동 |
| 6 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 7 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRC026S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRC026S |
| 테이블한글명 | MRC_활성도변동 |
| 주제영역 | 마트 |
| 도메인 | MRC |
| 유형 | S (Summary) |
| 적재주기 | 월배치 |
| 예상건수 | 약 1,200 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 2 | FROM_STAT | 이전상태 | CHAR | 1 | N | Y | A/I/D/M | 상동 |
| 3 | TO_STAT | 변경후 | CHAR | 1 | N | Y | A/I/D/M | 상동 |
| 4 | TRANS_CNT | 전이수 | INT |  | Y | N | 월 전이 고객수 | 상동 |
| 5 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 6 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRC027S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRC027S |
| 테이블한글명 | MRC_자녀고객 |
| 주제영역 | 마트 |
| 도메인 | MRC |
| 유형 | S (Summary) |
| 적재주기 | 월배치 |
| 예상건수 | 약 450 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 2 | MINOR_CNT | 미성년수 | INT |  | Y | N | 미성년 고객수 | 상동 |
| 3 | PARENT_LINKED | 부모연결수 | INT |  | Y | N | 부모 연계 수 | 상동 |
| 4 | AUM_TOT | AUM | NUMERIC | 18,2 | Y | N | 미성년 AUM | 상동 |
| 5 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 6 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRC028S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRC028S |
| 테이블한글명 | MRC_가족관계 |
| 주제영역 | 마트 |
| 도메인 | MRC |
| 유형 | S (Summary) |
| 적재주기 | 분기배치 |
| 예상건수 | 약 850 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YQ | 기준분기 | CHAR | 6 | N | Y | 기준 분기 | 상동 |
| 2 | FAM_SIZE | 가족수 | CHAR | 1 | N | Y | 1~5+ | 상동 |
| 3 | FAM_CNT | 가구수 | INT |  | Y | N | 해당 규모 가구수 | 상동 |
| 4 | FAM_AUM | 가구AUM | NUMERIC | 18,2 | Y | N | 가구 AUM 합 | 상동 |
| 5 | PRIMARY_CNT | 주거래가구 | INT |  | Y | N | 주거래 가구 수 | 상동 |
| 6 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 7 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRC029S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRC029S |
| 테이블한글명 | MRC_소득수준별 |
| 주제영역 | 마트 |
| 도메인 | MRC |
| 유형 | S (Summary) |
| 적재주기 | 월배치 |
| 예상건수 | 약 1,500 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 2 | INC_BUCKET | 소득구간 | CHAR | 2 | N | Y | 01:3천미만 02:3-5천 03:5-7천 04:7천-1억 05:1억이상 | 상동 |
| 3 | CUST_CNT | 고객수 | INT |  | Y | N | 고객수 | 상동 |
| 4 | AVG_AUM | 평균AUM | NUMERIC | 18,2 | Y | N | 평균 AUM | 상동 |
| 5 | AVG_LOAN | 평균여신 | NUMERIC | 18,2 | Y | N | 평균 여신 | 상동 |
| 6 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 7 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRC030S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRC030S |
| 테이블한글명 | MRC_주거래은행화율 |
| 주제영역 | 마트 |
| 도메인 | MRC |
| 유형 | S (Summary) |
| 적재주기 | 분기배치 |
| 예상건수 | 약 450 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YQ | 기준분기 | CHAR | 6 | N | Y | 기준 분기 | 상동 |
| 2 | CUST_TCD | 고객구분 | CHAR | 1 | N | Y | I/C/S/P | 상동 |
| 3 | PRIMARY_CNT | 주거래수 | INT |  | Y | N | 주거래 고객수 | 상동 |
| 4 | TOT_CNT | 전체수 | INT |  | Y | N | 전체 고객수 | 상동 |
| 5 | PRIMARY_RTO | 주거래비율 | NUMERIC | 5,2 | Y | N | 주거래 비율 | 상동 |
| 6 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 7 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## 다음 파일

`C6_MRP_상품분석.md` (15 테이블)
