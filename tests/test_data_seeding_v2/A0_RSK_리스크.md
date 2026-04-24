# A0. 리스크 — 리스크관리 (RSK, 30테이블)

**주제코드:** RSK
**도메인약어:** RSK
**테이블 수:** 30
**최종갱신:** 2026-04-21
**주제영역 범위:** 신용/시장/운영/유동성/금리 리스크. VaR, RWA, 자기자본비율, 스트레스테스트, ICAAP, 경제자본.

---

## TB_ADW_RSK001M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_RSK001M |
| 테이블한글명 | RSK_신용RWA |
| 주제영역 | 리스크 |
| 도메인 | RSK |
| 유형 | M (Master; 월말) |
| 적재주기 | 월배치 |
| 원천시스템 | 리스크시스템 |
| 예상건수 | 약 2,200,000 |

**[테이블 설명]**

```
[엔티티정의]
신용 위험가중자산(Credit RWA). 익스포저별 위험가중치 적용. 바젤III 표준법·내부등급법(AIRB).

[대상내]
- 여신 익스포저 RWA
- 유가증권 RWA
- 파생·RP RWA
- 장외거래 EAD 기반 RWA

[특이사항]
- 자기자본비율 분모 계산의 핵심
- 표준법: 외부등급 기반, 내부등급법: 자체 PD·LGD
```

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | EXPS_NO | 익스포저번호 | VARCHAR | 20 | N | Y | 익스포저 식별 | 상동 |
| 2 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 3 | CSN | 차주 | NUMERIC | 10 | Y | N | 차주 CSN | 상동 |
| 4 | EXPS_TCD | 익스포저유형 | CHAR | 2 | N | N | 01:법인대출 02:개인대출 03:부동산담보 04:유가증권 05:파생 06:지급보증 | 상동 |
| 5 | EAD | 부도노출액 | NUMERIC | 18,2 | Y | N | Exposure at Default | 상동 |
| 6 | RW_RTO | 위험가중치 | NUMERIC | 6,4 | Y | N | 위험가중치(%) | 상동 |
| 7 | RWA | RWA | NUMERIC | 18,2 | Y | N | EAD × 위험가중치 | 상동 |
| 8 | METHOD_CD | 산정방법 | CHAR | 1 | Y | N | S:표준법 F:FIRB A:AIRB | 상동 |
| 9 | PD | PD | NUMERIC | 7,6 | Y | N | 부도확률 | 상동 |
| 10 | LGD | LGD | NUMERIC | 5,2 | Y | N | 손실률 | 상동 |
| 11 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 12 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_RSK002M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_RSK002M |
| 테이블한글명 | RSK_시장RWA |
| 주제영역 | 리스크 |
| 도메인 | RSK |
| 유형 | M (Master; 월말) |
| 적재주기 | 월배치 |
| 예상건수 | 약 45,000 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BOOK_CD | Book | VARCHAR | 20 | N | Y | 거래 Book | 상동 |
| 2 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 3 | RSK_TCD | 위험유형 | CHAR | 2 | N | Y | 01:금리 02:주식 03:외환 04:상품 05:옵션 | 상동 |
| 4 | CAP_CHG | 자본차감액 | NUMERIC | 18,2 | Y | N | 자본 차감액 | 상동 |
| 5 | RWA | RWA | NUMERIC | 18,2 | Y | N | 시장 RWA | 상동 |
| 6 | METHOD_CD | 산정방법 | CHAR | 1 | Y | N | S:표준 I:내부모형 | 상동 |
| 7 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 8 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_RSK003M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_RSK003M |
| 테이블한글명 | RSK_운영RWA |
| 주제영역 | 리스크 |
| 도메인 | RSK |
| 유형 | M (Master; 월말) |
| 적재주기 | 월배치 |
| 예상건수 | 약 450 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 2 | BIZ_LINE | 사업라인 | VARCHAR | 50 | N | Y | 사업 라인 | 상동 |
| 3 | GROSS_INC | 총이익 | NUMERIC | 18,2 | Y | N | 3년 평균 총이익 | 상동 |
| 4 | BETA | 베타 | NUMERIC | 5,4 | Y | N | 베타 계수 | 상동 |
| 5 | CAP_CHG | 자본차감 | NUMERIC | 18,2 | Y | N | 자본 차감액 | 상동 |
| 6 | RWA | RWA | NUMERIC | 18,2 | Y | N | 운영 RWA | 상동 |
| 7 | METHOD_CD | 산정방법 | CHAR | 1 | Y | N | B:기초 S:표준 A:고급 | 상동 |
| 8 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 9 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_RSK004M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_RSK004M |
| 테이블한글명 | RSK_자기자본 |
| 주제영역 | 리스크 |
| 도메인 | RSK |
| 유형 | M (Master; 월말) |
| 적재주기 | 월배치 |
| 예상건수 | 약 850 |

**[테이블 설명]**

```
[엔티티정의]
자기자본 구성. Tier1(보통주자본 + 기타Tier1) + Tier2. BIS 자본비율 분자.
```

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 2 | CAP_TCD | 자본유형 | CHAR | 2 | N | Y | 01:보통주CET1 02:기타Tier1 03:Tier2 04:총자본 | 상동 |
| 3 | CAP_AMT | 자본액 | NUMERIC | 18,2 | Y | N | 자본 금액 | 상동 |
| 4 | DEDUCT_AMT | 차감액 | NUMERIC | 18,2 | Y | N | 차감 항목 | 상동 |
| 5 | NET_CAP | 순자본 | NUMERIC | 18,2 | Y | N | 순 자본액 | 상동 |
| 6 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 7 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_RSK005M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_RSK005M |
| 테이블한글명 | RSK_자본비율 |
| 주제영역 | 리스크 |
| 도메인 | RSK |
| 유형 | M (Master; 월말) |
| 적재주기 | 월배치 |
| 예상건수 | 약 450 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 2 | CET1_RTO | CET1비율 | NUMERIC | 7,4 | Y | N | 보통주자본비율(%) | 상동 |
| 3 | TIER1_RTO | Tier1비율 | NUMERIC | 7,4 | Y | N | Tier1 비율 | 상동 |
| 4 | TOT_RTO | 총자본비율 | NUMERIC | 7,4 | Y | N | BIS 총자본비율 | 상동 |
| 5 | LEV_RTO | 레버리지 | NUMERIC | 7,4 | Y | N | 레버리지비율 | 상동 |
| 6 | REGUL_MIN | 규제최소 | NUMERIC | 5,2 | Y | N | 감독 최소 요구치 | 상동 |
| 7 | BUFFER | 완충자본 | NUMERIC | 5,2 | Y | N | 완충 자본 요구 | 상동 |
| 8 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 9 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_RSK006M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_RSK006M |
| 테이블한글명 | RSK_PD모델 |
| 주제영역 | 리스크 |
| 도메인 | RSK |
| 유형 | M (Master) |
| 적재주기 | 월배치 |
| 예상건수 | 약 85,000,000 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | CSN | 차주 | NUMERIC | 10 | N | Y | 차주 | 상동 |
| 2 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 3 | MDL_CD | 모델코드 | VARCHAR | 20 | Y | N | PD 모델 코드 | 상동 |
| 4 | MDL_VER | 모델버전 | VARCHAR | 10 | Y | N | 모델 버전 | 상동 |
| 5 | PD_1Y | 1년PD | NUMERIC | 7,6 | Y | N | 1년 PD | 상동 |
| 6 | PD_CYCLE | 경기조정PD | NUMERIC | 7,6 | Y | N | 경기 조정 PD | 상동 |
| 7 | RATING | 등급 | VARCHAR | 5 | Y | N | 내부 등급 | 상동 |
| 8 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 9 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_RSK007M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_RSK007M |
| 테이블한글명 | RSK_LGD모델 |
| 주제영역 | 리스크 |
| 도메인 | RSK |
| 유형 | M (Master) |
| 적재주기 | 월배치 |
| 예상건수 | 약 85,000,000 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | EXPS_NO | 익스포저 | VARCHAR | 20 | N | Y | 익스포저 | 상동 |
| 2 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 3 | LGD_EST | LGD추정 | NUMERIC | 5,2 | Y | N | LGD 추정값 | 상동 |
| 4 | LGD_DOWNTURN | 경기하강LGD | NUMERIC | 5,2 | Y | N | 경기 하강기 LGD | 상동 |
| 5 | COLL_VAL | 담보가치 | NUMERIC | 18,2 | Y | N | 담보 가치 | 상동 |
| 6 | RECOV_RTO | 회수율 | NUMERIC | 5,2 | Y | N | 과거 회수율 | 상동 |
| 7 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 8 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_RSK008M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_RSK008M |
| 테이블한글명 | RSK_EAD산정 |
| 주제영역 | 리스크 |
| 도메인 | RSK |
| 유형 | M (Master) |
| 적재주기 | 월배치 |
| 예상건수 | 약 85,000,000 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | EXPS_NO | 익스포저 | VARCHAR | 20 | N | Y | 익스포저 | 상동 |
| 2 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 3 | OUTSTD_BAL | 잔액 | NUMERIC | 18,2 | Y | N | 실행잔액 | 상동 |
| 4 | UNUSED_COMMIT | 미사용약정 | NUMERIC | 18,2 | Y | N | 미사용 약정 | 상동 |
| 5 | CCF | 신용환산계수 | NUMERIC | 5,2 | Y | N | CCF | 상동 |
| 6 | EAD | EAD | NUMERIC | 18,2 | Y | N | 최종 EAD | 상동 |
| 7 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 8 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_RSK009M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_RSK009M |
| 테이블한글명 | RSK_유동성LCR |
| 주제영역 | 리스크 |
| 도메인 | RSK |
| 유형 | M (Master) |
| 적재주기 | 일배치 |
| 예상건수 | 약 180,000 |

**[테이블 설명]**

```
[엔티티정의]
LCR(유동성커버리지비율). 향후 30일간 예상 유출 대비 고유동성자산 충분성. 바젤III 기준.
```

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YMD | 기준일 | CHAR | 8 | N | Y | 기준일 | 상동 |
| 2 | CCY_CD | 통화 | CHAR | 3 | N | Y | 통화 | 상동 |
| 3 | HQLA | 고유동성자산 | NUMERIC | 18,2 | Y | N | High Quality Liquid Assets | 상동 |
| 4 | OUTFLOW | 예상유출 | NUMERIC | 18,2 | Y | N | 30일 예상 유출 | 상동 |
| 5 | INFLOW | 예상유입 | NUMERIC | 18,2 | Y | N | 30일 예상 유입 | 상동 |
| 6 | NET_OUTFLOW | 순유출 | NUMERIC | 18,2 | Y | N | 순 유출 | 상동 |
| 7 | LCR_RTO | LCR비율 | NUMERIC | 7,4 | Y | N | LCR(%) | 상동 |
| 8 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 9 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_RSK010M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_RSK010M |
| 테이블한글명 | RSK_유동성NSFR |
| 주제영역 | 리스크 |
| 도메인 | RSK |
| 유형 | M (Master) |
| 적재주기 | 월배치 |
| 예상건수 | 약 1,200 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 2 | AVAIL_SF | 가용안정조달 | NUMERIC | 18,2 | Y | N | Available SF | 상동 |
| 3 | REQUIRED_SF | 필요안정조달 | NUMERIC | 18,2 | Y | N | Required SF | 상동 |
| 4 | NSFR_RTO | NSFR비율 | NUMERIC | 7,4 | Y | N | NSFR(%) | 상동 |
| 5 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 6 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_RSK011M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_RSK011M |
| 테이블한글명 | RSK_유동성갭 |
| 주제영역 | 리스크 |
| 도메인 | RSK |
| 유형 | M (Master) |
| 적재주기 | 월배치 |
| 예상건수 | 약 45,000 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 2 | BUCKET | 기간구간 | CHAR | 3 | N | Y | 0-7D/8-30D/1-3M/3M-1Y/1-5Y/5Y+ | 상동 |
| 3 | CCY_CD | 통화 | CHAR | 3 | N | Y | 통화 | 상동 |
| 4 | INFLOW | 유입 | NUMERIC | 18,2 | Y | N | 기간 유입 | 상동 |
| 5 | OUTFLOW | 유출 | NUMERIC | 18,2 | Y | N | 기간 유출 | 상동 |
| 6 | NET_GAP | 순갭 | NUMERIC | 18,2 | Y | N | 순 갭 | 상동 |
| 7 | CUM_GAP | 누적갭 | NUMERIC | 18,2 | Y | N | 누적 갭 | 상동 |
| 8 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 9 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_RSK012M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_RSK012M |
| 테이블한글명 | RSK_금리갭 |
| 주제영역 | 리스크 |
| 도메인 | RSK |
| 유형 | M (Master; 월말) |
| 적재주기 | 월배치 |
| 예상건수 | 약 85,000 |

**[테이블 설명]**

```
[엔티티정의]
금리 리스크 갭 분석. 재설정주기별 자산·부채 금리 익스포저. IRRBB 관리 기본.
```

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 2 | RESET_BUCKET | 재설정구간 | CHAR | 3 | N | Y | 0-1M/1-3M/3-6M/6-12M/1-3Y/3-5Y/5Y+ | 상동 |
| 3 | CCY_CD | 통화 | CHAR | 3 | N | Y | 통화 | 상동 |
| 4 | RATE_SENS_ASSET | 금리민감자산 | NUMERIC | 18,2 | Y | N | 금리 민감 자산 | 상동 |
| 5 | RATE_SENS_LIAB | 금리민감부채 | NUMERIC | 18,2 | Y | N | 금리 민감 부채 | 상동 |
| 6 | GAP | 갭 | NUMERIC | 18,2 | Y | N | 자산-부채 | 상동 |
| 7 | EVE_IMPACT | EVE영향 | NUMERIC | 18,2 | Y | N | 100bp 상승시 EVE 변화 | 상동 |
| 8 | NII_IMPACT | NII영향 | NUMERIC | 18,2 | Y | N | 100bp 상승시 NII 변화 | 상동 |
| 9 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 10 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_RSK013M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_RSK013M |
| 테이블한글명 | RSK_스트레스시나리오 |
| 주제영역 | 리스크 |
| 도메인 | RSK |
| 유형 | M (Master) |
| 적재주기 | 분기배치 |
| 예상건수 | 약 850 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | SCEN_CD | 시나리오 | VARCHAR | 30 | N | Y | 시나리오 코드 | 상동 |
| 2 | SCEN_NM | 시나리오명 | VARCHAR | 200 | Y | N | 시나리오명 | 상동 |
| 3 | SCEN_TCD | 유형 | CHAR | 2 | Y | N | 01:기본 02:중대 03:악성 04:역시나리오 | 상동 |
| 4 | GDP_SHOCK | GDP충격 | NUMERIC | 5,2 | Y | N | GDP 성장률 충격(%p) | 상동 |
| 5 | RATE_SHOCK | 금리충격 | NUMERIC | 6,4 | Y | N | 금리 충격(bp) | 상동 |
| 6 | FX_SHOCK | 환율충격 | NUMERIC | 6,4 | Y | N | 환율 충격(%) | 상동 |
| 7 | STOCK_SHOCK | 주가충격 | NUMERIC | 6,4 | Y | N | 주가 충격(%) | 상동 |
| 8 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 9 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_RSK014M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_RSK014M |
| 테이블한글명 | RSK_스트레스결과 |
| 주제영역 | 리스크 |
| 도메인 | RSK |
| 유형 | M (Master) |
| 적재주기 | 분기배치 |
| 예상건수 | 약 28,000 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | SCEN_CD | 시나리오 | VARCHAR | 30 | N | Y | 시나리오 | 상동 |
| 2 | BASE_YQ | 기준분기 | CHAR | 6 | N | Y | 기준 분기 | 상동 |
| 3 | RSK_TCD | 위험유형 | CHAR | 2 | N | Y | 01:신용 02:시장 03:운영 04:유동성 | 상동 |
| 4 | PROJ_LOSS | 예상손실 | NUMERIC | 18,2 | Y | N | 예상 손실 | 상동 |
| 5 | CET1_POST | CET1추정 | NUMERIC | 7,4 | Y | N | 충격후 CET1 비율 | 상동 |
| 6 | CAP_NEEDED | 추가자본 | NUMERIC | 18,2 | Y | N | 추가 자본 필요액 | 상동 |
| 7 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 8 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_RSK015M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_RSK015M |
| 테이블한글명 | RSK_ICAAP |
| 주제영역 | 리스크 |
| 도메인 | RSK |
| 유형 | M (Master) |
| 적재주기 | 연배치 |
| 예상건수 | 약 450 |

**[테이블 설명]**

```
[엔티티정의]
ICAAP(내부자본적정성평가). 은행이 자체 리스크 평가 기반으로 필요 자본 산정. Pillar 2.
```

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | FY | 연도 | CHAR | 4 | N | Y | 연도 | 상동 |
| 2 | RSK_TCD | 위험유형 | CHAR | 2 | N | Y | 01:신용 02:시장 03:운영 04:금리 05:집중 06:평판 07:전략 | 상동 |
| 3 | ECON_CAP | 경제자본 | NUMERIC | 18,2 | Y | N | 경제 자본 | 상동 |
| 4 | REGUL_CAP | 규제자본 | NUMERIC | 18,2 | Y | N | 규제 필요 자본 | 상동 |
| 5 | BUFFER | 완충 | NUMERIC | 18,2 | Y | N | 추가 완충 | 상동 |
| 6 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 7 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_RSK016M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_RSK016M |
| 테이블한글명 | RSK_집중도위험 |
| 주제영역 | 리스크 |
| 도메인 | RSK |
| 유형 | M (Master; 월말) |
| 적재주기 | 월배치 |
| 예상건수 | 약 180,000 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 2 | CONC_DIM | 집중차원 | CHAR | 2 | N | Y | 01:차주 02:업종 03:지역 04:상품 05:통화 | 상동 |
| 3 | DIM_VAL | 차원값 | VARCHAR | 100 | N | Y | 차원 값 | 상동 |
| 4 | EXPS_AMT | 익스포저 | NUMERIC | 18,2 | Y | N | 익스포저 | 상동 |
| 5 | CONC_RTO | 집중도 | NUMERIC | 5,2 | Y | N | 자본 대비 비중 | 상동 |
| 6 | HHI_IDX | HHI지수 | NUMERIC | 8,4 | Y | N | Herfindahl 지수 | 상동 |
| 7 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 8 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_RSK017L

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_RSK017L |
| 테이블한글명 | RSK_한도초과 |
| 주제영역 | 리스크 |
| 도메인 | RSK |
| 유형 | L (Log) |
| 적재주기 | 이벤트 |
| 예상건수 | 약 8,500/년 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BRCH_NO | 위반번호 | VARCHAR | 22 | N | Y | 위반 고유번호 | 상동 |
| 2 | LIM_CD | 한도코드 | VARCHAR | 20 | N | N | 한도 | 상동 |
| 3 | OCC_YMD | 발생일 | CHAR | 8 | N | N | 초과 발생일 | 상동 |
| 4 | EXCESS_AMT | 초과액 | NUMERIC | 18,2 | Y | N | 초과 금액 | 상동 |
| 5 | RSLV_YMD | 해소일 | CHAR | 8 | Y | N | 해소일 | 상동 |
| 6 | STATUS_CD | 상태 | CHAR | 2 | N | N | 01:진행 02:해소 03:승인 04:허용 | 상동 |
| 7 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 8 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_RSK018M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_RSK018M |
| 테이블한글명 | RSK_운영손실 |
| 주제영역 | 리스크 |
| 도메인 | RSK |
| 유형 | M (Master) |
| 적재주기 | 이벤트 |
| 예상건수 | 약 18,000 |

**[테이블 설명]**

```
[엔티티정의]
운영리스크 손실사건. 오류·사기·시스템장애·법적·외부사건 등. AMA 모델 기초 데이터.
```

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | LOSS_NO | 손실번호 | VARCHAR | 22 | N | Y | 손실 고유번호 | 상동 |
| 2 | OCC_YMD | 발생일 | CHAR | 8 | N | N | 발생일 | 상동 |
| 3 | LOSS_TCD | 손실유형 | CHAR | 2 | N | N | 01:내부사기 02:외부사기 03:고용관행 04:고객관행 05:시스템 06:실행오류 07:외부사건 | 상동 |
| 4 | BRCD | 부점 | CHAR | 7 | Y | N | 발생 부점 | 상동 |
| 5 | GROSS_LOSS | 총손실 | NUMERIC | 18,2 | Y | N | 총 손실 | 상동 |
| 6 | RECOV_AMT | 회수 | NUMERIC | 18,2 | Y | N | 회수 금액 | 상동 |
| 7 | NET_LOSS | 순손실 | NUMERIC | 18,2 | Y | N | 순 손실 | 상동 |
| 8 | STATUS_CD | 상태 | CHAR | 2 | N | N | 01:신고 02:조사 03:확정 04:완료 | 상동 |
| 9 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 10 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_RSK019M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_RSK019M |
| 테이블한글명 | RSK_KRI |
| 주제영역 | 리스크 |
| 도메인 | RSK |
| 유형 | M (Master; 월말) |
| 적재주기 | 월배치 |
| 예상건수 | 약 85,000 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | KRI_CD | KRI코드 | VARCHAR | 20 | N | Y | KRI 식별 | 상동 |
| 2 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 3 | KRI_NM | KRI명 | VARCHAR | 200 | Y | N | 지표명 | 상동 |
| 4 | CUR_VAL | 현재값 | NUMERIC | 18,4 | Y | N | 현재 값 | 상동 |
| 5 | GREEN_THR | 녹색임계 | NUMERIC | 18,4 | Y | N | Green 경계 | 상동 |
| 6 | YELLOW_THR | 황색임계 | NUMERIC | 18,4 | Y | N | Yellow 경계 | 상동 |
| 7 | RED_THR | 적색임계 | NUMERIC | 18,4 | Y | N | Red 경계 | 상동 |
| 8 | STAT_CD | 상태 | CHAR | 1 | Y | N | G:정상 Y:주의 R:경고 | 상동 |
| 9 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 10 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_RSK020M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_RSK020M |
| 테이블한글명 | RSK_RAF |
| 주제영역 | 리스크 |
| 도메인 | RSK |
| 유형 | M (Master) |
| 적재주기 | 연배치 |
| 예상건수 | 약 850 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | FY | 연도 | CHAR | 4 | N | Y | 연도 | 상동 |
| 2 | METRIC_CD | 지표 | VARCHAR | 20 | N | Y | RAF 지표 | 상동 |
| 3 | METRIC_NM | 지표명 | VARCHAR | 200 | Y | N | 지표명 | 상동 |
| 4 | APPETITE | 목표치 | NUMERIC | 18,4 | Y | N | 목표값 | 상동 |
| 5 | LIMIT | 한도 | NUMERIC | 18,4 | Y | N | 한도값 | 상동 |
| 6 | CUR_VAL | 현재값 | NUMERIC | 18,4 | Y | N | 현재 값 | 상동 |
| 7 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 8 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_RSK021M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_RSK021M |
| 테이블한글명 | RSK_자산건전성분류 |
| 주제영역 | 리스크 |
| 도메인 | RSK |
| 유형 | M (Master; 월말) |
| 적재주기 | 월배치 |
| 예상건수 | 약 85,000,000 |

**[테이블 설명]**

```
[엔티티정의]
자산건전성 5단계 분류(정상/요주의/고정/회수의문/추정손실). 감독회계 분류. 충당금 산정 기초.
```

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | LON_NO | 대출번호 | CHAR | 20 | N | Y | 대출 | 상동 |
| 2 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 3 | CLS_CD | 분류코드 | CHAR | 1 | N | N | 1:정상 2:요주의 3:고정 4:회수의문 5:추정손실 | 상동 |
| 4 | CLS_RSN | 분류사유 | VARCHAR | 500 | Y | N | 분류 사유 | 상동 |
| 5 | PROV_RTO | 충당금률 | NUMERIC | 5,2 | Y | N | 충당금 적립률 | 상동 |
| 6 | PROV_AMT | 충당금 | NUMERIC | 18,2 | Y | N | 충당금 적립액 | 상동 |
| 7 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 8 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_RSK022M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_RSK022M |
| 테이블한글명 | RSK_IFRS9Stage |
| 주제영역 | 리스크 |
| 도메인 | RSK |
| 유형 | M (Master; 월말) |
| 적재주기 | 월배치 |
| 예상건수 | 약 85,000,000 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | LON_NO | 대출번호 | CHAR | 20 | N | Y | 대출 | 상동 |
| 2 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 3 | STAGE | Stage | CHAR | 1 | N | N | 1:정상 2:신용위험증가 3:손상 | 상동 |
| 4 | ECL_12M | 12개월ECL | NUMERIC | 18,2 | Y | N | 12개월 ECL | 상동 |
| 5 | ECL_LT | 전생애ECL | NUMERIC | 18,2 | Y | N | Lifetime ECL | 상동 |
| 6 | APPLIED_ECL | 적용ECL | NUMERIC | 18,2 | Y | N | 최종 ECL 인식액 | 상동 |
| 7 | SICR_YN | 신용위험증가 | CHAR | 1 | Y | N | Y:SICR 발생 | 상동 |
| 8 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 9 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_RSK023M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_RSK023M |
| 테이블한글명 | RSK_충당금원장 |
| 주제영역 | 리스크 |
| 도메인 | RSK |
| 유형 | M (Master; 월말) |
| 적재주기 | 월배치 |
| 예상건수 | 약 2,200,000 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 2 | BRCD | 부점 | CHAR | 7 | N | Y | 부점 | 상동 |
| 3 | PROV_TCD | 충당금유형 | CHAR | 2 | N | Y | 01:IFRS9 02:감독회계 03:일반 04:특별 | 상동 |
| 4 | BEG_BAL | 기초 | NUMERIC | 18,2 | Y | N | 월초 잔액 | 상동 |
| 5 | ADD_AMT | 전입 | NUMERIC | 18,2 | Y | N | 월 전입 | 상동 |
| 6 | REV_AMT | 환입 | NUMERIC | 18,2 | Y | N | 월 환입 | 상동 |
| 7 | END_BAL | 기말 | NUMERIC | 18,2 | Y | N | 월말 잔액 | 상동 |
| 8 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 9 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_RSK024M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_RSK024M |
| 테이블한글명 | RSK_커버리지비율 |
| 주제영역 | 리스크 |
| 도메인 | RSK |
| 유형 | M (Master; 월말) |
| 적재주기 | 월배치 |
| 예상건수 | 약 850 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 2 | NPL_BAL | NPL잔액 | NUMERIC | 18,2 | Y | N | NPL(고정이하) 잔액 | 상동 |
| 3 | PROV_BAL | 충당금 | NUMERIC | 18,2 | Y | N | 충당금 잔액 | 상동 |
| 4 | COVERAGE_RTO | 커버리지 | NUMERIC | 7,4 | Y | N | 충당금/NPL (%) | 상동 |
| 5 | NPL_RTO | NPL비율 | NUMERIC | 7,4 | Y | N | NPL 비율 | 상동 |
| 6 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 7 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_RSK025M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_RSK025M |
| 테이블한글명 | RSK_예수금변동성 |
| 주제영역 | 리스크 |
| 도메인 | RSK |
| 유형 | M (Master) |
| 적재주기 | 월배치 |
| 예상건수 | 약 180,000 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 2 | DEP_TCD | 예금유형 | CHAR | 2 | N | Y | 예금 유형 | 상동 |
| 3 | AVG_BAL | 평잔 | NUMERIC | 18,2 | Y | N | 월 평균 잔액 | 상동 |
| 4 | STD_DEV | 표준편차 | NUMERIC | 18,2 | Y | N | 일별 잔액 표준편차 | 상동 |
| 5 | VOLATILE_RTO | 변동비율 | NUMERIC | 5,2 | Y | N | 변동성 비율 | 상동 |
| 6 | CORE_RTO | 코어비율 | NUMERIC | 5,2 | Y | N | 코어예금 비율 | 상동 |
| 7 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 8 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_RSK026L

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_RSK026L |
| 테이블한글명 | RSK_백테스트 |
| 주제영역 | 리스크 |
| 도메인 | RSK |
| 유형 | L (Log) |
| 적재주기 | 일배치 |
| 예상건수 | 약 4,500/년 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BT_NO | 백테스트번호 | VARCHAR | 22 | N | Y | 테스트 고유번호 | 상동 |
| 2 | MDL_CD | 모델 | VARCHAR | 20 | Y | N | 대상 모델 | 상동 |
| 3 | BT_YMD | 테스트일 | CHAR | 8 | N | N | 테스트일 | 상동 |
| 4 | PREDICTED | 예측값 | NUMERIC | 18,2 | Y | N | 모델 예측 | 상동 |
| 5 | ACTUAL | 실제값 | NUMERIC | 18,2 | Y | N | 실현 값 | 상동 |
| 6 | BREACH_YN | 초과여부 | CHAR | 1 | Y | N | Y:VaR 초과 | 상동 |
| 7 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 8 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_RSK027M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_RSK027M |
| 테이블한글명 | RSK_모델검증 |
| 주제영역 | 리스크 |
| 도메인 | RSK |
| 유형 | M (Master) |
| 적재주기 | 연배치 |
| 예상건수 | 약 250 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | MDL_CD | 모델 | VARCHAR | 20 | N | Y | 모델 | 상동 |
| 2 | VLD_YMD | 검증일 | CHAR | 8 | N | Y | 검증일 | 상동 |
| 3 | VLD_TCD | 검증유형 | CHAR | 2 | Y | N | 01:초기승인 02:정기 03:수정 | 상동 |
| 4 | VLD_RSLT | 검증결과 | CHAR | 1 | Y | N | A:승인 R:재작업 D:거절 | 상동 |
| 5 | FINDINGS | 발견사항 | VARCHAR | 4000 | Y | N | 검증 발견사항 | 상동 |
| 6 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 7 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_RSK028M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_RSK028M |
| 테이블한글명 | RSK_평판리스크 |
| 주제영역 | 리스크 |
| 도메인 | RSK |
| 유형 | M (Master) |
| 적재주기 | 월배치 |
| 예상건수 | 약 4,500 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | REP_NO | 평판사건번호 | VARCHAR | 22 | N | Y | 사건 고유번호 | 상동 |
| 2 | OCC_YMD | 발생일 | CHAR | 8 | N | N | 발생일 | 상동 |
| 3 | SRC_CD | 출처 | CHAR | 2 | Y | N | 01:언론 02:SNS 03:민원 04:감독기관 05:내부 | 상동 |
| 4 | SEVERITY | 심각도 | CHAR | 1 | Y | N | H:높음 M:중간 L:낮음 | 상동 |
| 5 | DESC_TXT | 내용 | VARCHAR | 4000 | Y | N | 사건 요약 | 상동 |
| 6 | ACT_PLAN | 대응 | VARCHAR | 2000 | Y | N | 대응 계획 | 상동 |
| 7 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 8 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_RSK029M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_RSK029M |
| 테이블한글명 | RSK_ESG리스크 |
| 주제영역 | 리스크 |
| 도메인 | RSK |
| 유형 | M (Master; 월말) |
| 적재주기 | 월배치 |
| 예상건수 | 약 450,000 |

**[테이블 설명]**

```
[엔티티정의]
ESG 리스크 노출 측정. 기후·사회·지배구조 위험에 따른 익스포저 분류. 녹색금융·지속가능금융 관리.
```

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | CSN | 차주 | NUMERIC | 10 | N | Y | 차주 | 상동 |
| 2 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 3 | CLIMATE_RSK | 기후위험 | CHAR | 1 | Y | N | 1:낮음 2:중간 3:높음 | 상동 |
| 4 | TRANS_RSK | 전환위험 | CHAR | 1 | Y | N | 저탄소 전환 위험 | 상동 |
| 5 | PHYS_RSK | 물리적위험 | CHAR | 1 | Y | N | 물리적 기후 위험 | 상동 |
| 6 | SOC_SCR | 사회점수 | NUMERIC | 5,2 | Y | N | 사회 점수 | 상동 |
| 7 | GOV_SCR | 지배구조점수 | NUMERIC | 5,2 | Y | N | 지배구조 점수 | 상동 |
| 8 | CO2_SCOPE1_2 | CO2배출 | NUMERIC | 18,2 | Y | N | CO2 배출량(tCO2e) | 상동 |
| 9 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 10 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_RSK030H

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_RSK030H |
| 테이블한글명 | RSK_리스크지표이력 |
| 주제영역 | 리스크 |
| 도메인 | RSK |
| 유형 | H (History) |
| 적재주기 | 월배치 |
| 예상건수 | 약 250,000 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | IND_CD | 지표 | VARCHAR | 20 | N | Y | 리스크 지표 | 상동 |
| 2 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 3 | IND_VAL | 값 | NUMERIC | 18,4 | Y | N | 지표 값 | 상동 |
| 4 | TREND_CD | 추세 | CHAR | 1 | Y | N | U:상승 D:하락 S:안정 | 상동 |
| 5 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 6 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## 다음 파일

`A1_RPT_규제.md` (20 테이블)
