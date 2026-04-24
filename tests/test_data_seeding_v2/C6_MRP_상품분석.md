# C6. 마트 — 상품분석 (MRP, 15테이블)

**주제코드:** MRP
**도메인약어:** MRP
**테이블 수:** 15
**최종갱신:** 2026-04-21
**주제영역 범위:** 상품(Product) 분석 마트. 상품 라이프사이클·경쟁력·수익성·만족도·AB테스트·신청전환.

---

## TB_ADW_MRP001S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRP001S |
| 테이블한글명 | MRP_수신잔액 |
| 주제영역 | 마트 |
| 도메인 | MRP |
| 유형 | S (Summary) |
| 적재주기 | 월배치 |
| 예상건수 | 약 180,000 |

**[테이블 설명]**

```
[엔티티정의]
상품별 수신 잔액·신규·해지 월간 집계. 상품 라이프사이클 평가. MVP002S와 PK 동일하나 상품 관점 분석 전용.
```

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | PDN | 상품 | VARCHAR | 10 | N | Y | 상품 | 상동 |
| 2 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 3 | EOM_BAL | 월말잔액 | NUMERIC | 18,2 | Y | N | 월말 잔액 | 상동 |
| 4 | AVG_BAL | 평잔 | NUMERIC | 18,2 | Y | N | 평잔 | 상동 |
| 5 | NEW_CNT | 신규수 | INT |  | Y | N | 월 신규 | 상동 |
| 6 | TERM_CNT | 해지수 | INT |  | Y | N | 월 해지 | 상동 |
| 7 | LC_STAGE | 라이프사이클 | CHAR | 1 | Y | N | I:도입 G:성장 M:성숙 D:쇠퇴 | 상동 |
| 8 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 9 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRP002S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRP002S |
| 테이블한글명 | MRP_여신잔액 |
| 주제영역 | 마트 |
| 도메인 | MRP |
| 유형 | S (Summary) |
| 적재주기 | 월배치 |
| 예상건수 | 약 150,000 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | PDN | 상품 | VARCHAR | 10 | N | Y | 상품 | 상동 |
| 2 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 3 | EOM_BAL | 월말잔액 | NUMERIC | 18,2 | Y | N | 여신 월말 잔액 | 상동 |
| 4 | NEW_AMT | 신규 | NUMERIC | 18,2 | Y | N | 월 신규 실행 | 상동 |
| 5 | DLQ_RTO | 연체율 | NUMERIC | 5,2 | Y | N | 연체율 | 상동 |
| 6 | NPL_RTO | NPL비율 | NUMERIC | 5,2 | Y | N | NPL 비율 | 상동 |
| 7 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 8 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRP003S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRP003S |
| 테이블한글명 | MRP_수익성 |
| 주제영역 | 마트 |
| 도메인 | MRP |
| 유형 | S (Summary) |
| 적재주기 | 월배치 |
| 예상건수 | 약 250,000 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | PDN | 상품 | VARCHAR | 10 | N | Y | 상품 | 상동 |
| 2 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 3 | REVENUE | 수익 | NUMERIC | 18,2 | Y | N | 상품 수익 | 상동 |
| 4 | COST | 비용 | NUMERIC | 18,2 | Y | N | 상품 비용 | 상동 |
| 5 | PROFIT | 이익 | NUMERIC | 18,2 | Y | N | 순이익 | 상동 |
| 6 | PROFIT_MARGIN | 이익률 | NUMERIC | 5,2 | Y | N | 이익률 | 상동 |
| 7 | CONTRIB_RTO | 기여도 | NUMERIC | 5,2 | Y | N | 전체 이익 기여도 | 상동 |
| 8 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 9 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRP004S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRP004S |
| 테이블한글명 | MRP_가입해지 |
| 주제영역 | 마트 |
| 도메인 | MRP |
| 유형 | S (Summary) |
| 적재주기 | 월배치 |
| 예상건수 | 약 450,000 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | PDN | 상품 | VARCHAR | 10 | N | Y | 상품 | 상동 |
| 2 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 3 | CH_CD | 채널 | CHAR | 2 | N | Y | 가입 채널 | 상동 |
| 4 | NEW_CNT | 신규 | INT |  | Y | N | 신규 건수 | 상동 |
| 5 | TERM_CNT | 해지 | INT |  | Y | N | 해지 건수 | 상동 |
| 6 | RENEW_CNT | 재가입 | INT |  | Y | N | 재가입 건수 | 상동 |
| 7 | EARLY_TERM_RTO | 중도해지율 | NUMERIC | 5,2 | Y | N | 중도해지 비율 | 상동 |
| 8 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 9 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRP005S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRP005S |
| 테이블한글명 | MRP_신규상품 |
| 주제영역 | 마트 |
| 도메인 | MRP |
| 유형 | S (Summary) |
| 적재주기 | 월배치 |
| 예상건수 | 약 450 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | PDN | 상품 | VARCHAR | 10 | N | Y | 상품 | 상동 |
| 2 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 3 | LAUNCH_YMD | 출시일 | CHAR | 8 | Y | N | 출시일 | 상동 |
| 4 | DAYS_SINCE | 경과일 | INT |  | Y | N | 출시후 경과일 | 상동 |
| 5 | TOT_NEW | 누적신규 | INT |  | Y | N | 출시 후 누적 신규 | 상동 |
| 6 | TOT_BAL | 누적잔액 | NUMERIC | 18,2 | Y | N | 현재 잔액 | 상동 |
| 7 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 8 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRP006S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRP006S |
| 테이블한글명 | MRP_단종상품 |
| 주제영역 | 마트 |
| 도메인 | MRP |
| 유형 | S (Summary) |
| 적재주기 | 월배치 |
| 예상건수 | 약 2,500 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | PDN | 상품 | VARCHAR | 10 | N | Y | 상품 | 상동 |
| 2 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 3 | EOS_YMD | 단종일 | CHAR | 8 | Y | N | 단종일 | 상동 |
| 4 | RESID_CNT | 잔여계좌 | INT |  | Y | N | 미해지 잔여 | 상동 |
| 5 | RESID_BAL | 잔여잔액 | NUMERIC | 18,2 | Y | N | 잔여 잔액 | 상동 |
| 6 | MIG_PDN | 전환대상상품 | VARCHAR | 10 | Y | N | 전환 대상 상품 | 상동 |
| 7 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 8 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRP007S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRP007S |
| 테이블한글명 | MRP_상품전환 |
| 주제영역 | 마트 |
| 도메인 | MRP |
| 유형 | S (Summary) |
| 적재주기 | 월배치 |
| 예상건수 | 약 25,000 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | FROM_PDN | 전환전 | VARCHAR | 10 | N | Y | 전환 전 상품 | 상동 |
| 2 | TO_PDN | 전환후 | VARCHAR | 10 | N | Y | 전환 후 상품 | 상동 |
| 3 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 4 | TRANS_CNT | 전환수 | INT |  | Y | N | 월 전환 수 | 상동 |
| 5 | TRANS_AMT | 전환잔액 | NUMERIC | 18,2 | Y | N | 전환 총 잔액 | 상동 |
| 6 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 7 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRP008S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRP008S |
| 테이블한글명 | MRP_라이프사이클 |
| 주제영역 | 마트 |
| 도메인 | MRP |
| 유형 | S (Summary) |
| 적재주기 | 월배치 |
| 예상건수 | 약 45,000 |

**[테이블 설명]**

```
[엔티티정의]
상품 라이프사이클 스테이지 평가. 신규수·성장률·포화도·이탈률 기반 도입·성장·성숙·쇠퇴 분류.
```

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | PDN | 상품 | VARCHAR | 10 | N | Y | 상품 | 상동 |
| 2 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 3 | LC_STAGE | 단계 | CHAR | 1 | Y | N | I:도입 G:성장 M:성숙 D:쇠퇴 | 상동 |
| 4 | GROWTH_RTO | 성장률 | NUMERIC | 8,4 | Y | N | YoY 성장률 | 상동 |
| 5 | SATURATION | 포화도 | NUMERIC | 5,2 | Y | N | 시장 포화 수준 | 상동 |
| 6 | NET_ADD | 순증수 | INT |  | Y | N | 순증 | 상동 |
| 7 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 8 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRP009S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRP009S |
| 테이블한글명 | MRP_만족도 |
| 주제영역 | 마트 |
| 도메인 | MRP |
| 유형 | S (Summary) |
| 적재주기 | 월배치 |
| 예상건수 | 약 45,000 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | PDN | 상품 | VARCHAR | 10 | N | Y | 상품 | 상동 |
| 2 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 3 | SAT_AVG | 평균만족 | NUMERIC | 5,2 | Y | N | 1~5 평균 | 상동 |
| 4 | NPS_SCORE | NPS | NUMERIC | 6,2 | Y | N | 상품 NPS | 상동 |
| 5 | RESP_CNT | 응답수 | INT |  | Y | N | 설문 응답수 | 상동 |
| 6 | COMP_CNT | 민원수 | INT |  | Y | N | 상품 관련 민원 | 상동 |
| 7 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 8 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRP010S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRP010S |
| 테이블한글명 | MRP_KPI |
| 주제영역 | 마트 |
| 도메인 | MRP |
| 유형 | S (Summary) |
| 적재주기 | 월배치 |
| 예상건수 | 약 85,000 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | PDN | 상품 | VARCHAR | 10 | N | Y | 상품 | 상동 |
| 2 | KPI_CD | KPI | VARCHAR | 20 | N | Y | KPI 식별 | 상동 |
| 3 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 4 | TGT_VAL | 목표 | NUMERIC | 18,4 | Y | N | 목표값 | 상동 |
| 5 | ACT_VAL | 실적 | NUMERIC | 18,4 | Y | N | 실적값 | 상동 |
| 6 | ACHIEVE_RTO | 달성률 | NUMERIC | 5,2 | Y | N | 달성률 | 상동 |
| 7 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 8 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRP011S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRP011S |
| 테이블한글명 | MRP_포트폴리오 |
| 주제영역 | 마트 |
| 도메인 | MRP |
| 유형 | S (Summary) |
| 적재주기 | 분기배치 |
| 예상건수 | 약 1,200 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YQ | 기준분기 | CHAR | 6 | N | Y | 기준 분기 | 상동 |
| 2 | PDN_GRP | 상품군 | VARCHAR | 20 | N | Y | 상품군 | 상동 |
| 3 | TOT_BAL | 총잔액 | NUMERIC | 18,2 | Y | N | 상품군 잔액 | 상동 |
| 4 | PORT_RTO | 포트비중 | NUMERIC | 5,2 | Y | N | 전체 포트 비중 | 상동 |
| 5 | YOY_GRW | YoY성장 | NUMERIC | 8,4 | Y | N | YoY 성장률 | 상동 |
| 6 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 7 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRP012S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRP012S |
| 테이블한글명 | MRP_원가이익 |
| 주제영역 | 마트 |
| 도메인 | MRP |
| 유형 | S (Summary) |
| 적재주기 | 월배치 |
| 예상건수 | 약 45,000 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | PDN | 상품 | VARCHAR | 10 | N | Y | 상품 | 상동 |
| 2 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 3 | DIRECT_COST | 직접원가 | NUMERIC | 18,2 | Y | N | 직접 원가 | 상동 |
| 4 | ALLOC_COST | 배부원가 | NUMERIC | 18,2 | Y | N | 배부 | 상동 |
| 5 | FTP_MARGIN | FTP마진 | NUMERIC | 18,2 | Y | N | FTP 마진 | 상동 |
| 6 | RISK_COST | 리스크원가 | NUMERIC | 18,2 | Y | N | ECL 기반 리스크 원가 | 상동 |
| 7 | NET_PROFIT | 순이익 | NUMERIC | 18,2 | Y | N | 순이익 | 상동 |
| 8 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 9 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRP013S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRP013S |
| 테이블한글명 | MRP_금리경쟁력 |
| 주제영역 | 마트 |
| 도메인 | MRP |
| 유형 | S (Summary) |
| 적재주기 | 주배치 |
| 예상건수 | 약 25,000 |

**[테이블 설명]**

```
[엔티티정의]
타행 대비 금리 경쟁력 비교. 주요 동일 상품군 타행 금리(외부 수집) 대비 당행 금리 위치. 주간 갱신.
```

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | PDN | 상품 | VARCHAR | 10 | N | Y | 상품 | 상동 |
| 2 | BASE_YMD | 기준일 | CHAR | 8 | N | Y | 주간 기준일 | 상동 |
| 3 | OWN_RATE | 당행금리 | NUMERIC | 10,6 | Y | N | 당행 금리 | 상동 |
| 4 | PEER_AVG | 타행평균 | NUMERIC | 10,6 | Y | N | 타행 평균 | 상동 |
| 5 | PEER_MIN | 타행최저 | NUMERIC | 10,6 | Y | N | 타행 최저 | 상동 |
| 6 | PEER_MAX | 타행최고 | NUMERIC | 10,6 | Y | N | 타행 최고 | 상동 |
| 7 | RANK | 순위 | INT |  | Y | N | 시중은행 내 순위 | 상동 |
| 8 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 9 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRP014S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRP014S |
| 테이블한글명 | MRP_AB테스트 |
| 주제영역 | 마트 |
| 도메인 | MRP |
| 유형 | S (Summary) |
| 적재주기 | 일배치 |
| 예상건수 | 약 12,000 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | EXP_NO | 실험 | VARCHAR | 22 | N | Y | 실험 | 상동 |
| 2 | VARIANT_CD | 변형 | VARCHAR | 20 | N | Y | A/B/C… | 상동 |
| 3 | BASE_YMD | 기준일 | CHAR | 8 | N | Y | 기준일 | 상동 |
| 4 | PDN | 상품 | VARCHAR | 10 | Y | N | 대상 상품 | 상동 |
| 5 | EXPOSED | 노출 | BIGINT |  | Y | N | 노출수 | 상동 |
| 6 | APPLIED | 신청 | BIGINT |  | Y | N | 신청수 | 상동 |
| 7 | APPROVED | 승인 | BIGINT |  | Y | N | 승인수 | 상동 |
| 8 | CONV_RTO | 전환율 | NUMERIC | 8,4 | Y | N | 전환율 | 상동 |
| 9 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 10 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_MRP015S

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_MRP015S |
| 테이블한글명 | MRP_신청전환 |
| 주제영역 | 마트 |
| 도메인 | MRP |
| 유형 | S (Summary) |
| 적재주기 | 일배치 |
| 예상건수 | 약 180,000 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | PDN | 상품 | VARCHAR | 10 | N | Y | 상품 | 상동 |
| 2 | BASE_YMD | 기준일 | CHAR | 8 | N | Y | 기준일 | 상동 |
| 3 | CH_CD | 채널 | CHAR | 2 | N | Y | 신청 채널 | 상동 |
| 4 | VIEW_CNT | 조회수 | INT |  | Y | N | 상품 상세 조회 | 상동 |
| 5 | APPLY_CNT | 신청수 | INT |  | Y | N | 신청 수 | 상동 |
| 6 | APPROVE_CNT | 승인수 | INT |  | Y | N | 승인 수 | 상동 |
| 7 | VIEW_TO_APPLY | 조회→신청 | NUMERIC | 8,4 | Y | N | 조회→신청 전환 | 상동 |
| 8 | APPLY_TO_APPROVE | 신청→승인 | NUMERIC | 8,4 | Y | N | 신청→승인 전환 | 상동 |
| 9 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 10 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## 다음 파일

`C7_MRO_부점분석.md` (15 테이블)
