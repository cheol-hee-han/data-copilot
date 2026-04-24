# C8. 마트 — 임원·규제보고 (MRR, 10테이블)

**주제코드:** MRR
**도메인약어:** MRR
**테이블 수:** 10
**최종갱신:** 2026-04-21
**주제영역 범위:** 최고경영진·이사회·감독당국 보고용 최상위 집계 마트. 월차경영보고·분기규제지표·업권peer비교·ESG·임원성과·중점관리지표.

> **마트 특성:** MRR은 하위 마트(MVP/MVN/MVC/MVF/MVB/MRC/MRP/MRO)의 재집계. 경영전략·IR·감독기관 대응.

---

## TB_ADW_MRR001S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRR001S |
| 테이블한글명 | MRR_월차경영보고 |
| 주제영역 | 마트 |
| 도메인 | MRR |
| 유형 | S (Summary) |
| 적재주기 | 월배치 |
| 예상건수 | 약 450 |

**[테이블 설명]**

```
[엔티티정의]
월차 경영진 보고 대시보드 원천. 자산·수익·리스크·고객·디지털 전영역 핵심 지표 1행으로 요약. 월말 회계 마감 후 D+3 생성.
```

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 2 | TOT_ASSET | 총자산 | NUMERIC | 18,2 | Y | N | 월말 총자산 | 상동 |
| 3 | TOT_DEP | 총수신 | NUMERIC | 18,2 | Y | N | 월말 총수신 | 상동 |
| 4 | TOT_LOAN | 총여신 | NUMERIC | 18,2 | Y | N | 월말 총여신 | 상동 |
| 5 | NET_PROFIT | 순이익 | NUMERIC | 18,2 | Y | N | 월 순이익 | 상동 |
| 6 | NIM | NIM | NUMERIC | 10,6 | Y | N | NIM | 상동 |
| 7 | ROA | ROA | NUMERIC | 8,4 | Y | N | ROA | 상동 |
| 8 | ROE | ROE | NUMERIC | 8,4 | Y | N | ROE | 상동 |
| 9 | NPL_RTO | NPL비율 | NUMERIC | 5,2 | Y | N | NPL | 상동 |
| 10 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 11 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRR002S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRR002S |
| 테이블한글명 | MRR_분기규제지표 |
| 주제영역 | 마트 |
| 도메인 | MRR |
| 유형 | S (Summary) |
| 적재주기 | 분기배치 |
| 예상건수 | 약 150 |

**[테이블 설명]**

```
[엔티티정의]
분기 규제지표 요약. BIS·LCR·NSFR·예대율·유동성 비율 일괄. 감독당국 공시 전 최종 점검.
```

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YQ | 기준분기 | CHAR | 6 | N | Y | 기준 분기 | 상동 |
| 2 | CET1 | CET1비율 | NUMERIC | 6,4 | Y | N | CET1(보통주자본) | 상동 |
| 3 | TIER1 | Tier1비율 | NUMERIC | 6,4 | Y | N | Tier1 | 상동 |
| 4 | BIS_TOT | 총자본비율 | NUMERIC | 6,4 | Y | N | 총자본비율 | 상동 |
| 5 | LCR | LCR | NUMERIC | 6,4 | Y | N | 유동성커버리지 | 상동 |
| 6 | NSFR | NSFR | NUMERIC | 6,4 | Y | N | 순안정자금조달 | 상동 |
| 7 | LTD | 예대율 | NUMERIC | 6,4 | Y | N | Loan-to-Deposit | 상동 |
| 8 | LEV_RATIO | 레버리지 | NUMERIC | 6,4 | Y | N | 레버리지비율 | 상동 |
| 9 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 10 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRR003S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRR003S |
| 테이블한글명 | MRR_임원경영성과 |
| 주제영역 | 마트 |
| 도메인 | MRR |
| 유형 | S (Summary) |
| 적재주기 | 분기배치 |
| 예상건수 | 약 450 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | EXEC_ID | 임원ID | VARCHAR | 10 | N | Y | 임원 식별 | 상동 |
| 2 | BASE_YQ | 기준분기 | CHAR | 6 | N | Y | 기준 분기 | 상동 |
| 3 | EXEC_NM | 임원명 | VARCHAR | 100 | Y | N | 임원명 | 상동 |
| 4 | POSITION | 직책 | VARCHAR | 100 | Y | N | CEO/부행장/본부장 등 | 상동 |
| 5 | TGT_ACH_RTO | 목표달성률 | NUMERIC | 5,2 | Y | N | 개별 KPI 달성률 | 상동 |
| 6 | TOT_SCR | 종합점수 | NUMERIC | 6,2 | Y | N | 종합 평가 점수 | 상동 |
| 7 | BONUS_AMT | 성과급 | NUMERIC | 18,2 | Y | N | 분기 성과급 | 상동 |
| 8 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 9 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRR004S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRR004S |
| 테이블한글명 | MRR_이사회안건 |
| 주제영역 | 마트 |
| 도메인 | MRR |
| 유형 | S (Summary) |
| 적재주기 | 이벤트 |
| 예상건수 | 약 1,200 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | AGENDA_NO | 안건번호 | VARCHAR | 22 | N | Y | 안건 고유번호 | 상동 |
| 2 | BOARD_YMD | 이사회일 | CHAR | 8 | N | N | 개최일 | 상동 |
| 3 | AGENDA_TCD | 안건유형 | CHAR | 2 | N | N | 01:의결 02:보고 03:승인 04:논의 | 상동 |
| 4 | TITLE | 안건명 | VARCHAR | 500 | Y | N | 안건명 | 상동 |
| 5 | RSLT_CD | 결과 | CHAR | 2 | Y | N | 01:원안가결 02:수정가결 03:부결 04:보고완료 05:연기 | 상동 |
| 6 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 7 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRR005S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRR005S |
| 테이블한글명 | MRR_경영계획대비 |
| 주제영역 | 마트 |
| 도메인 | MRR |
| 유형 | S (Summary) |
| 적재주기 | 월배치 |
| 예상건수 | 약 4,500 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 2 | ITEM_CD | 항목 | VARCHAR | 20 | N | Y | 경영계획 항목 | 상동 |
| 3 | PLAN_VAL | 계획 | NUMERIC | 18,4 | Y | N | 연간 계획값 | 상동 |
| 4 | YTD_VAL | YTD실적 | NUMERIC | 18,4 | Y | N | YTD 실적 | 상동 |
| 5 | PROG_RTO | 진도율 | NUMERIC | 5,2 | Y | N | 진도율 | 상동 |
| 6 | FY_PROJ | 연말예상 | NUMERIC | 18,4 | Y | N | 연말 예상값 | 상동 |
| 7 | RISK_LV | 리스크수준 | CHAR | 1 | Y | N | H:고 M:중 L:저 | 상동 |
| 8 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 9 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRR006S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRR006S |
| 테이블한글명 | MRR_중점관리지표 |
| 주제영역 | 마트 |
| 도메인 | MRR |
| 유형 | S (Summary) |
| 적재주기 | 일배치 |
| 예상건수 | 약 12,000 |

**[테이블 설명]**

```
[엔티티정의]
경영진 워치리스트(Watch List). 기준치 대비 이탈 경보. 매일 CRO/CFO/CEO 대시보드 노출.
```

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YMD | 기준일 | CHAR | 8 | N | Y | 기준일 | 상동 |
| 2 | IND_CD | 지표코드 | VARCHAR | 20 | N | Y | 지표 식별 | 상동 |
| 3 | IND_NM | 지표명 | VARCHAR | 200 | Y | N | 지표명 | 상동 |
| 4 | CURRENT_VAL | 현재값 | NUMERIC | 18,4 | Y | N | 현재 값 | 상동 |
| 5 | THRESHOLD | 기준치 | NUMERIC | 18,4 | Y | N | 경보 기준치 | 상동 |
| 6 | ALERT_LV | 경보수준 | CHAR | 1 | Y | N | R:적색 Y:황색 G:녹색 | 상동 |
| 7 | TREND | 추세 | CHAR | 1 | Y | N | U:상승 D:하락 F:횡보 | 상동 |
| 8 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 9 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRR007S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRR007S |
| 테이블한글명 | MRR_업권비교 |
| 주제영역 | 마트 |
| 도메인 | MRR |
| 유형 | S (Summary) |
| 적재주기 | 분기배치 |
| 예상건수 | 약 450 |

**[테이블 설명]**

```
[엔티티정의]
시중은행 peer 비교. 금감원 업권 공시 기반 외부 데이터 결합. 자산·순이익·NIM·NPL·CIR 비교 포지션.
```

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YQ | 기준분기 | CHAR | 6 | N | Y | 기준 분기 | 상동 |
| 2 | IND_CD | 지표 | VARCHAR | 20 | N | Y | 지표 | 상동 |
| 3 | OWN_VAL | 당행값 | NUMERIC | 18,4 | Y | N | 당행 | 상동 |
| 4 | PEER_AVG | peer평균 | NUMERIC | 18,4 | Y | N | 동종 평균 | 상동 |
| 5 | PEER_MED | peer중간값 | NUMERIC | 18,4 | Y | N | 동종 중간값 | 상동 |
| 6 | RANK | 순위 | INT |  | Y | N | 시중은행 내 순위 | 상동 |
| 7 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 8 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRR008S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRR008S |
| 테이블한글명 | MRR_주주총회지표 |
| 주제영역 | 마트 |
| 도메인 | MRR |
| 유형 | S (Summary) |
| 적재주기 | 연배치 |
| 예상건수 | 약 85 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | FY | 연도 | CHAR | 4 | N | Y | 회계연도 | 상동 |
| 2 | TOT_ASSET | 총자산 | NUMERIC | 18,2 | Y | N | 연말 총자산 | 상동 |
| 3 | NET_PROFIT | 연순이익 | NUMERIC | 18,2 | Y | N | 연간 순이익 | 상동 |
| 4 | EPS | EPS | NUMERIC | 18,4 | Y | N | 주당순이익 | 상동 |
| 5 | BPS | BPS | NUMERIC | 18,4 | Y | N | 주당순자산 | 상동 |
| 6 | DPS | DPS | NUMERIC | 18,4 | Y | N | 주당배당금 | 상동 |
| 7 | PAYOUT_RTO | 배당성향 | NUMERIC | 5,2 | Y | N | 배당성향 | 상동 |
| 8 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 9 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRR009S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRR009S |
| 테이블한글명 | MRR_규제한도관리 |
| 주제영역 | 마트 |
| 도메인 | MRR |
| 유형 | S (Summary) |
| 적재주기 | 일배치 |
| 예상건수 | 약 12,000 |

**[테이블 설명]**

```
[엔티티정의]
규제 한도 현황. 동일차주·대주주·해외투자·부동산PF 등 은행법·감독규정 상 법정 한도 대비 이용률 일일 모니터링.
```

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YMD | 기준일 | CHAR | 8 | N | Y | 기준일 | 상동 |
| 2 | LIMIT_CD | 한도코드 | VARCHAR | 20 | N | Y | 한도 식별 | 상동 |
| 3 | LIMIT_NM | 한도명 | VARCHAR | 200 | Y | N | 한도명 | 상동 |
| 4 | LEGAL_LIMIT | 법정한도 | NUMERIC | 18,2 | Y | N | 법정 한도 | 상동 |
| 5 | INT_LIMIT | 내부한도 | NUMERIC | 18,2 | Y | N | 내부 운영 한도 | 상동 |
| 6 | USED | 사용액 | NUMERIC | 18,2 | Y | N | 사용 금액 | 상동 |
| 7 | USED_RTO | 이용률 | NUMERIC | 5,2 | Y | N | 법정한도 대비 이용률 | 상동 |
| 8 | WARN_YN | 경보 | CHAR | 1 | Y | N | Y:경보 발령 | 상동 |
| 9 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 10 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRR010S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRR010S |
| 테이블한글명 | MRR_ESG지표 |
| 주제영역 | 마트 |
| 도메인 | MRR |
| 유형 | S (Summary) |
| 적재주기 | 분기배치 |
| 예상건수 | 약 450 |

**[테이블 설명]**

```
[엔티티정의]
ESG(환경·사회·거버넌스) 공시지표. 탄소배출·녹색금융·사회공헌·지배구조 점수. TCFD·SASB·K-ESG 기준.
```

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YQ | 기준분기 | CHAR | 6 | N | Y | 기준 분기 | 상동 |
| 2 | ESG_CAT | ESG분류 | CHAR | 1 | N | Y | E:환경 S:사회 G:거버넌스 | 상동 |
| 3 | IND_CD | 지표 | VARCHAR | 20 | N | Y | 지표 | 상동 |
| 4 | IND_VAL | 값 | NUMERIC | 18,4 | Y | N | 지표 값 | 상동 |
| 5 | TGT_VAL | 목표 | NUMERIC | 18,4 | Y | N | 목표값 | 상동 |
| 6 | DISC_YN | 공시여부 | CHAR | 1 | Y | N | Y:외부공시 | 상동 |
| 7 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 8 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## 주제영역 마트 완료

**마트 전체 200 테이블 완료:** MVP(35)+MVN(35)+MVC(20)+MVF(15)+MVB(25)+MRC(30)+MRP(15)+MRO(15)+MRR(10)

---

## 🎉 v2 Phase B 전체 설계 완료

**1,441개 테이블 정의서 작성 완료.**
