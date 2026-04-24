# 82. 외환 — FX딜링 (FXD, 20테이블)

**주제코드:** FXD
**도메인약어:** FXD
**테이블 수:** 20
**최종갱신:** 2026-04-21
**주제영역 범위:** 딜링룸 FX 스팟·선물환·스왑, 옵션, 딜러 포지션, 시장 리스크, 딜 확인, 체결 세그먼트.

---

## TB_ADW_FXD001L

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_FXD001L |
| 테이블한글명 | FXD_딜링거래 |
| 주제영역 | 외환 |
| 도메인 | FXD |
| 유형 | L (Log) |
| 적재주기 | 실시간 |
| 원천시스템 | 딜링시스템 |
| 예상건수 | 약 850,000/년 |

**[테이블 설명]**

```
[엔티티정의]
딜링룸 FX 딜 총괄 로그. 스팟·선물환·통화스왑·옵션 등 모든 딜 진입점.

[특이사항]
- **Super-Type 구조**: 본 테이블은 모든 FX 딜의 공통 속성만 보관. 딜 유형별 상세는 sub-type에 있음.
  - DEAL_TCD='01' → FXD002L 스팟거래 (spot 상세는 추가 컬럼 없음, 본 테이블만으로 충분)
  - DEAL_TCD='02' → FXD003L 선물환거래 (SPOT_RATE, FWD_POINT, FWD_RATE, TENOR_DAYS)
  - DEAL_TCD='03' → FXD004L 스왑거래 (Near/Far 각 레그)
  - DEAL_TCD='04' → FXD005L 통화옵션 (strike, IV, delta)
  - DEAL_TCD='05' → FXD006L NDF거래 (fixing rate, fixing date)
- "FX 딜 명목금액 합계" → 본 테이블 단독으로 충분 (DEAL_TCD로 구분)
- "선물환 평균 만기" → FXD003L 직접 조회 (혹은 본 테이블 DEAL_TCD='02' JOIN)
- 딜 취소·정정은 FXD014L 거래취소정정에 별도 기록
- **쌍둥이 주의 (FXD ↔ DRV)**: 동일 FX 파생(Forward/Swap/NDF/옵션)이 DRV001M에도 존재.
  - FXD (본 테이블) = **딜링룸 관점**: 실시간 딜 체결·상대방·스프레드 (시장조성)
  - DRV001M = **ALM/회계 관점**: 일별 공정가평가·헤지지정·CVA (IFRS·바젤)
  - "FX 딜 체결·일일 손익" 조회 → FXD. "공정가·헤지지정·CVA" 조회 → DRV.
```

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | DEAL_NO | 딜번호 | VARCHAR | 22 | N | Y | 딜 고유번호 | 상동 |
| 2 | DEAL_TCD | 딜유형 | CHAR | 2 | N | N | 01:SPOT 02:FWD 03:SWAP 04:OPTION 05:NDF | 상동 |
| 3 | DEAL_YMD | 딜체결일 | CHAR | 8 | N | N | 체결일 | 상동 |
| 4 | DEAL_TS | 체결시각 | TIMESTAMP |  | N | N | 체결 시각 | 상동 |
| 5 | VAL_YMD | 결제일 | CHAR | 8 | Y | N | 결제일 | 상동 |
| 6 | BUY_CCY | 매수통화 | CHAR | 3 | N | N | 매수 통화 | 상동 |
| 7 | SELL_CCY | 매도통화 | CHAR | 3 | N | N | 매도 통화 | 상동 |
| 8 | NOMI_AMT | 명목금액 | NUMERIC | 18,2 | N | N | 명목 금액 | 상동 |
| 9 | DEAL_RATE | 체결환율 | NUMERIC | 18,6 | Y | N | 체결 환율 | 상동 |
| 10 | CNTRPT | 상대방 | VARCHAR | 200 | Y | N | 거래 상대방 | 상동 |
| 11 | DEAL_STAT | 상태 | CHAR | 2 | N | N | 01:체결 02:확인 03:결제 04:취소 | 상동 |
| 12 | DEALER_ID | 딜러 | VARCHAR | 10 | Y | N | 딜러 ID | 상동 |
| 13 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 14 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_FXD002L

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_FXD002L |
| 테이블한글명 | FXD_스팟거래 |
| 주제영역 | 외환 |
| 도메인 | FXD |
| 유형 | L (Log) |
| 적재주기 | 실시간 |
| 예상건수 | 약 550,000/년 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | DEAL_NO | 딜번호 | VARCHAR | 22 | N | Y | FK→FXD001L | 상동 |
| 2 | SPOT_RATE | 스팟환율 | NUMERIC | 18,6 | Y | N | 체결 스팟 환율 | 상동 |
| 3 | REF_RATE | 참조환율 | NUMERIC | 18,6 | Y | N | 참조 환율(매매기준) | 상동 |
| 4 | SPRD_BP | 스프레드 | NUMERIC | 8,4 | Y | N | 기준 대비 스프레드(bp) | 상동 |
| 5 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 6 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_FXD003L

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_FXD003L |
| 테이블한글명 | FXD_선물환거래 |
| 주제영역 | 외환 |
| 도메인 | FXD |
| 유형 | L (Log) |
| 적재주기 | 실시간 |
| 예상건수 | 약 180,000/년 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | DEAL_NO | 딜번호 | VARCHAR | 22 | N | Y | FK→FXD001L | 상동 |
| 2 | SPOT_RATE | 스팟환율 | NUMERIC | 18,6 | Y | N | 체결시점 스팟 | 상동 |
| 3 | FWD_POINT | 선물포인트 | NUMERIC | 18,6 | Y | N | 선물 포인트(스왑포인트) | 상동 |
| 4 | FWD_RATE | 선물환율 | NUMERIC | 18,6 | Y | N | 선물 환율 | 상동 |
| 5 | TENOR_DAYS | 만기일수 | INT |  | Y | N | 만기 일수 | 상동 |
| 6 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 7 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_FXD004L

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_FXD004L |
| 테이블한글명 | FXD_스왑거래 |
| 주제영역 | 외환 |
| 도메인 | FXD |
| 유형 | L (Log) |
| 적재주기 | 실시간 |
| 예상건수 | 약 65,000/년 |

**[테이블 설명]**

```
[엔티티정의]
FX 스왑 / 통화스왑(CCS) 거래. 근일물 매도 + 원일물 매수 (또는 반대).
```

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | DEAL_NO | 딜번호 | VARCHAR | 22 | N | Y | FK→FXD001L | 상동 |
| 2 | SWAP_TCD | 스왑유형 | CHAR | 2 | N | N | 01:FXSwap 02:CCS 03:CrossCurrency | 상동 |
| 3 | NEAR_VAL_YMD | 근일결제 | CHAR | 8 | Y | N | 근일 결제일 | 상동 |
| 4 | FAR_VAL_YMD | 원일결제 | CHAR | 8 | Y | N | 원일 결제일 | 상동 |
| 5 | NEAR_RATE | 근일환율 | NUMERIC | 18,6 | Y | N | 근일 환율 | 상동 |
| 6 | FAR_RATE | 원일환율 | NUMERIC | 18,6 | Y | N | 원일 환율 | 상동 |
| 7 | SWAP_POINT | 스왑포인트 | NUMERIC | 18,6 | Y | N | 스왑 포인트 | 상동 |
| 8 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 9 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_FXD005L

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_FXD005L |
| 테이블한글명 | FXD_통화옵션 |
| 주제영역 | 외환 |
| 도메인 | FXD |
| 유형 | L (Log) |
| 적재주기 | 실시간 |
| 예상건수 | 약 18,000/년 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | DEAL_NO | 딜번호 | VARCHAR | 22 | N | Y | FK→FXD001L | 상동 |
| 2 | OPT_TCD | 옵션유형 | CHAR | 2 | N | N | 01:Call 02:Put 03:Barrier 04:구조화 | 상동 |
| 3 | STRIKE | 행사가 | NUMERIC | 18,6 | Y | N | 행사 환율 | 상동 |
| 4 | PREM_PCT | 프리미엄율 | NUMERIC | 8,4 | Y | N | 프리미엄(%) | 상동 |
| 5 | EXPR_YMD | 만기 | CHAR | 8 | Y | N | 옵션 만기 | 상동 |
| 6 | EXC_STYLE | 행사스타일 | CHAR | 1 | Y | N | E:유럽식 A:미국식 | 상동 |
| 7 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 8 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_FXD006L

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_FXD006L |
| 테이블한글명 | FXD_NDF거래 |
| 주제영역 | 외환 |
| 도메인 | FXD |
| 유형 | L (Log) |
| 적재주기 | 실시간 |
| 예상건수 | 약 35,000/년 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | DEAL_NO | 딜번호 | VARCHAR | 22 | N | Y | FK→FXD001L | 상동 |
| 2 | TRGT_CCY | 대상통화 | CHAR | 3 | N | N | 비인도 통화(KRW) | 상동 |
| 3 | SETT_CCY | 결제통화 | CHAR | 3 | N | N | 결제 통화(USD) | 상동 |
| 4 | FIX_YMD | 고시일 | CHAR | 8 | Y | N | Fixing 일 | 상동 |
| 5 | FIX_RATE | 고시환율 | NUMERIC | 18,6 | Y | N | Fixing 환율 | 상동 |
| 6 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 7 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_FXD007M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_FXD007M |
| 테이블한글명 | FXD_딜러포지션 |
| 주제영역 | 외환 |
| 도메인 | FXD |
| 유형 | M (Master) |
| 적재주기 | 실시간 |
| 예상건수 | 약 85,000 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | DEALER_ID | 딜러 | VARCHAR | 10 | N | Y | 딜러 식별 | 상동 |
| 2 | BASE_TS | 기준시각 | TIMESTAMP |  | N | Y | 기준 시각 | 상동 |
| 3 | CCY_CD | 통화 | CHAR | 3 | N | Y | 통화 | 상동 |
| 4 | NET_POS | 순포지션 | NUMERIC | 18,2 | Y | N | 순 포지션 | 상동 |
| 5 | UNRL_PL | 미실현손익 | NUMERIC | 18,2 | Y | N | 미실현 손익 | 상동 |
| 6 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 7 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_FXD008M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_FXD008M |
| 테이블한글명 | FXD_딜러 |
| 주제영역 | 외환 |
| 도메인 | FXD |
| 유형 | M (Master) |
| 적재주기 | 이벤트 |
| 예상건수 | 약 85 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | DEALER_ID | 딜러ID | VARCHAR | 10 | N | Y | 딜러 식별 | 상동 |
| 2 | DEALER_NM | 성명 | VARCHAR | 100 | Y | N | 딜러 성명 | 상동 |
| 3 | DESK | 데스크 | CHAR | 2 | Y | N | 01:스팟 02:선물 03:옵션 04:구조화 | 상동 |
| 4 | LIM_AMT_USD | 포지션한도 | NUMERIC | 18,2 | Y | N | 일 포지션 한도(USD) | 상동 |
| 5 | LOSS_LIM | 손실한도 | NUMERIC | 18,2 | Y | N | 일 손실 한도 | 상동 |
| 6 | USE_YN | 재직여부 | CHAR | 1 | N | N | Y:재직 N:퇴직 | 상동 |
| 7 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 8 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_FXD009L

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_FXD009L |
| 테이블한글명 | FXD_딜확인 |
| 주제영역 | 외환 |
| 도메인 | FXD |
| 유형 | L (Log) |
| 적재주기 | 실시간 |
| 예상건수 | 약 850,000/년 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | DEAL_NO | 딜번호 | VARCHAR | 22 | N | Y | FK→FXD001L | 상동 |
| 2 | CONF_YMD | 확인일 | CHAR | 8 | N | N | 확인일 | 상동 |
| 3 | CONF_METH | 확인방식 | CHAR | 2 | Y | N | 01:SWIFT 02:이메일 03:전화 04:FAX 05:CLS | 상동 |
| 4 | CONF_STAT | 확인상태 | CHAR | 2 | N | N | 01:완료 02:불일치 03:미확인 | 상동 |
| 5 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 6 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_FXD010L

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_FXD010L |
| 테이블한글명 | FXD_딜결제 |
| 주제영역 | 외환 |
| 도메인 | FXD |
| 유형 | L (Log) |
| 적재주기 | 일배치 |
| 예상건수 | 약 850,000/년 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | DEAL_NO | 딜번호 | VARCHAR | 22 | N | Y | FK→FXD001L | 상동 |
| 2 | SETT_YMD | 결제일 | CHAR | 8 | N | N | 결제 실행일 | 상동 |
| 3 | SETT_CCY1 | 결제통화1 | CHAR | 3 | Y | N | 결제 통화1 | 상동 |
| 4 | SETT_AMT1 | 결제금액1 | NUMERIC | 18,2 | Y | N | 결제 금액1 | 상동 |
| 5 | SETT_CCY2 | 결제통화2 | CHAR | 3 | Y | N | 결제 통화2 | 상동 |
| 6 | SETT_AMT2 | 결제금액2 | NUMERIC | 18,2 | Y | N | 결제 금액2 | 상동 |
| 7 | CLS_YN | CLS결제 | CHAR | 1 | Y | N | Y:CLS 결제 N:기존 | 상동 |
| 8 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 9 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_FXD011M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_FXD011M |
| 테이블한글명 | FXD_상대방한도 |
| 주제영역 | 외환 |
| 도메인 | FXD |
| 유형 | M (Master) |
| 적재주기 | 일배치 |
| 예상건수 | 약 4,500 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | CNTRPT_CD | 상대방코드 | VARCHAR | 20 | N | Y | 거래 상대방 | 상동 |
| 2 | BASE_YMD | 기준일 | CHAR | 8 | N | Y | 기준일 | 상동 |
| 3 | LIM_AMT_USD | 한도 | NUMERIC | 18,2 | Y | N | 거래 한도(USD) | 상동 |
| 4 | USED_USD | 사용액 | NUMERIC | 18,2 | Y | N | 현 사용액 | 상동 |
| 5 | COLLTR_REQ | 담보요구여부 | CHAR | 1 | Y | N | Y:담보요구 | 상동 |
| 6 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 7 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_FXD012M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_FXD012M |
| 테이블한글명 | FXD_VaR |
| 주제영역 | 외환 |
| 도메인 | FXD |
| 유형 | M (Master) |
| 적재주기 | 일배치 |
| 예상건수 | 약 8,500 |

**[테이블 설명]**

```
[엔티티정의]
FX 포지션 VaR(Value at Risk). 일별 시장리스크 지표. 99% 신뢰수준 1일 보유기간.
```

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YMD | 기준일 | CHAR | 8 | N | Y | 기준일 | 상동 |
| 2 | PORT_CD | 포트폴리오 | VARCHAR | 20 | N | Y | 포트폴리오 코드 | 상동 |
| 3 | VAR_95 | VaR95 | NUMERIC | 18,2 | Y | N | VaR 95% | 상동 |
| 4 | VAR_99 | VaR99 | NUMERIC | 18,2 | Y | N | VaR 99% | 상동 |
| 5 | CVAR_99 | CVaR99 | NUMERIC | 18,2 | Y | N | 조건부 VaR | 상동 |
| 6 | VAR_LIM | VaR한도 | NUMERIC | 18,2 | Y | N | VaR 한도 | 상동 |
| 7 | BRCH_YN | 한도초과 | CHAR | 1 | Y | N | Y:초과 N:정상 | 상동 |
| 8 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 9 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_FXD013L

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_FXD013L |
| 테이블한글명 | FXD_딜손익 |
| 주제영역 | 외환 |
| 도메인 | FXD |
| 유형 | L (Log) |
| 적재주기 | 일배치 |
| 예상건수 | 약 850,000/년 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | DEAL_NO | 딜번호 | VARCHAR | 22 | N | Y | FK→FXD001L | 상동 |
| 2 | BASE_YMD | 기준일 | CHAR | 8 | N | Y | 기준일 | 상동 |
| 3 | MKT_RATE | 시장환율 | NUMERIC | 18,6 | Y | N | 기준일 시장 환율 | 상동 |
| 4 | UNRL_PL | 미실현손익 | NUMERIC | 18,2 | Y | N | 미실현 손익 | 상동 |
| 5 | REAL_PL | 실현손익 | NUMERIC | 18,2 | Y | N | 실현 손익 | 상동 |
| 6 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 7 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_FXD014L

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_FXD014L |
| 테이블한글명 | FXD_거래취소정정 |
| 주제영역 | 외환 |
| 도메인 | FXD |
| 유형 | L (Log) |
| 적재주기 | 이벤트 |
| 예상건수 | 약 12,000/년 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | ADJ_NO | 정정번호 | VARCHAR | 22 | N | Y | 정정 고유번호 | 상동 |
| 2 | ORIG_DEAL_NO | 원딜번호 | VARCHAR | 22 | N | N | FK→FXD001L | 상동 |
| 3 | ADJ_YMD | 정정일 | CHAR | 8 | N | N | 정정일 | 상동 |
| 4 | ADJ_TCD | 유형 | CHAR | 2 | N | N | 01:취소 02:금액정정 03:환율정정 04:상대방정정 | 상동 |
| 5 | ADJ_RSN | 사유 | VARCHAR | 1000 | Y | N | 정정 사유 | 상동 |
| 6 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 7 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_FXD015M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_FXD015M |
| 테이블한글명 | FXD_옵션구조 |
| 주제영역 | 외환 |
| 도메인 | FXD |
| 유형 | M (Master) |
| 적재주기 | 이벤트 |
| 예상건수 | 약 4,500 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | STRUCT_NO | 구조번호 | VARCHAR | 22 | N | Y | 구조화 상품 번호 | 상동 |
| 2 | DEAL_NO | 딜번호 | VARCHAR | 22 | Y | N | FK→FXD001L | 상동 |
| 3 | STRUCT_TCD | 구조유형 | CHAR | 2 | N | N | 01:KIKO 02:ZeroCost 03:Collar 04:Butterfly 05:Straddle | 상동 |
| 4 | STRCT_DTL | 구조상세 | VARCHAR | 2000 | Y | N | 구조 상세 | 상동 |
| 5 | BARRIER_KI | Knock-in수준 | NUMERIC | 18,6 | Y | N | KI 환율 | 상동 |
| 6 | BARRIER_KO | Knock-out수준 | NUMERIC | 18,6 | Y | N | KO 환율 | 상동 |
| 7 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 8 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_FXD016M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_FXD016M |
| 테이블한글명 | FXD_이자율스왑 |
| 주제영역 | 외환 |
| 도메인 | FXD |
| 유형 | M (Master) |
| 적재주기 | 이벤트 |
| 예상건수 | 약 8,500 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | IRS_NO | IRS번호 | VARCHAR | 22 | N | Y | IRS 고유번호 | 상동 |
| 2 | NOMI_CCY | 명목통화 | CHAR | 3 | N | N | 명목 통화 | 상동 |
| 3 | NOMI_AMT | 명목금액 | NUMERIC | 18,2 | N | N | 명목 금액 | 상동 |
| 4 | PAY_FLT_RCV_FIX_YN | 지급변동수취고정 | CHAR | 1 | Y | N | Y:변동지급/고정수취 N:고정지급/변동수취 | 상동 |
| 5 | FIX_RATE | 고정금리 | NUMERIC | 10,6 | Y | N | 고정 금리 | 상동 |
| 6 | FLT_REF | 변동기준 | CHAR | 10 | Y | N | 변동 기준(SOFR 등) | 상동 |
| 7 | MAT_YMD | 만기일 | CHAR | 8 | Y | N | 만기일 | 상동 |
| 8 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 9 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_FXD017M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_FXD017M |
| 테이블한글명 | FXD_마켓데이터 |
| 주제영역 | 외환 |
| 도메인 | FXD |
| 유형 | M (Master) |
| 적재주기 | 실시간 |
| 예상건수 | 약 25,000,000 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | MKT_SRC | 시장소스 | VARCHAR | 20 | N | Y | Reuters/Bloomberg 등 | 상동 |
| 2 | CCY_PAIR | 통화쌍 | CHAR | 7 | N | Y | USDKRW 등 | 상동 |
| 3 | SNAP_TS | 시각 | TIMESTAMP |  | N | Y | 데이터 시각 | 상동 |
| 4 | BID | 매수호가 | NUMERIC | 18,6 | Y | N | 매수 호가 | 상동 |
| 5 | ASK | 매도호가 | NUMERIC | 18,6 | Y | N | 매도 호가 | 상동 |
| 6 | MID | 중간값 | NUMERIC | 18,6 | Y | N | 중간값 | 상동 |
| 7 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 8 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_FXD018M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_FXD018M |
| 테이블한글명 | FXD_변동성서페이스 |
| 주제영역 | 외환 |
| 도메인 | FXD |
| 유형 | M (Master) |
| 적재주기 | 일배치 |
| 예상건수 | 약 180,000 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YMD | 기준일 | CHAR | 8 | N | Y | 기준일 | 상동 |
| 2 | CCY_PAIR | 통화쌍 | CHAR | 7 | N | Y | 통화쌍 | 상동 |
| 3 | TENOR | 만기구간 | VARCHAR | 5 | N | Y | 1M/3M/6M/1Y 등 | 상동 |
| 4 | DELTA | 델타 | NUMERIC | 5,2 | N | Y | 델타 | 상동 |
| 5 | IV | 내재변동성 | NUMERIC | 8,4 | Y | N | 내재 변동성 | 상동 |
| 6 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 7 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_FXD019M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_FXD019M |
| 테이블한글명 | FXD_일일포지션한도 |
| 주제영역 | 외환 |
| 도메인 | FXD |
| 유형 | M (Master) |
| 적재주기 | 일배치 |
| 예상건수 | 약 85,000 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BASE_YMD | 기준일 | CHAR | 8 | N | Y | 기준일 | 상동 |
| 2 | CCY_CD | 통화 | CHAR | 3 | N | Y | 통화 | 상동 |
| 3 | LONG_LIM | 롱한도 | NUMERIC | 18,2 | Y | N | 롱 포지션 한도 | 상동 |
| 4 | SHORT_LIM | 숏한도 | NUMERIC | 18,2 | Y | N | 숏 포지션 한도 | 상동 |
| 5 | CURR_LONG | 현롱 | NUMERIC | 18,2 | Y | N | 현재 롱 | 상동 |
| 6 | CURR_SHORT | 현숏 | NUMERIC | 18,2 | Y | N | 현재 숏 | 상동 |
| 7 | USE_RTO | 사용률 | NUMERIC | 5,2 | Y | N | 한도 사용률 | 상동 |
| 8 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 9 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_FXD020M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_FXD020M |
| 테이블한글명 | FXD_일일딜링실적 |
| 주제영역 | 외환 |
| 도메인 | FXD |
| 유형 | M (Master) |
| 적재주기 | 일배치 |
| 예상건수 | 약 250,000 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | DEALER_ID | 딜러 | VARCHAR | 10 | N | Y | 딜러 | 상동 |
| 2 | BASE_YMD | 기준일 | CHAR | 8 | N | Y | 기준일 | 상동 |
| 3 | DEAL_CNT | 딜건수 | INT |  | Y | N | 일 딜 건수 | 상동 |
| 4 | DEAL_VOL_USD | 거래액 | NUMERIC | 18,2 | Y | N | USD환산 거래 총액 | 상동 |
| 5 | DAILY_PL | 일손익 | NUMERIC | 18,2 | Y | N | 일 손익 | 상동 |
| 6 | VAR_UTIL | VaR사용률 | NUMERIC | 5,2 | Y | N | VaR 사용률 | 상동 |
| 7 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 8 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## 외환 주제영역 완료

**외환 전체 (FXC 40 + FXR 25 + FXD 20 = 85) 완료.** 다음: `85_EBB_인터넷뱅킹.md` (15)
