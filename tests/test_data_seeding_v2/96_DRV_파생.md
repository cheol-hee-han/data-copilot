# 96. 투자 — 파생상품 (DRV, 25테이블)

**주제코드:** DRV
**도메인약어:** DRV
**테이블 수:** 25
**최종갱신:** 2026-04-21
**주제영역 범위:** 파생상품. 금리스왑(IRS)/통화스왑(CRS)/선도/옵션/FRA/CDS. 헤지목적·투기목적 분리. 장내·장외 모두 포함.

---

## TB_ADW_DRV001M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_DRV001M |
| 테이블한글명 | DRV_파생기본 |
| 주제영역 | 투자 |
| 도메인 | DRV |
| 유형 | M (Master) |
| 적재주기 | 이벤트 |
| 원천시스템 | 파생거래시스템 |
| 예상건수 | 약 28,000 |

**[테이블 설명]**

```
[엔티티정의]
파생상품 거래 마스터. 본행이 당사자로 참여한 파생계약. 장외(OTC)·장내(Exchange) 구분.

[대상내]
- IRS(금리스왑), CRS(통화스왑), Basis Swap
- FX Forward, FX Swap, NDF
- 옵션 (콜/풋/엑조틱)
- CDS(신용부도스왑)
- FRA, 금리선물

[특이사항]
- 헤지회계 적용 여부가 손익 인식에 중대한 영향
- 장외는 ISDA CSA 담보 관리 대상
- 공정가치 평가 매일 수행
- **쌍둥이 주의 (FXD ↔ DRV)**: FX Forward/Swap/NDF/통화옵션은 FXD001L 딜링거래에도 존재.
  - FXD (외환 주제영역) = **딜링룸 관점**: 실시간 딜 체결·상대방·스프레드 관리 (시장조성·호가)
  - DRV (투자 주제영역) = **ALM/회계 관점**: 일별 공정가평가·헤지지정·CVA/DVA·바젤 규제보고
- 동일 FX 파생 거래가 양쪽에 기록되며 거래번호(DEAL_NO ↔ DRV_NO)로 연계 가능
- "FX 딜 체결·일일 손익" 조회 → FXD. "헤지지정·IFRS 평가·CVA" 조회 → DRV.
- 순수 금리파생(IRS/CRS)·신용파생(CDS)·KIKO 등은 FXD 대상외로 DRV 단독 관리
```

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | DRV_NO | 파생번호 | VARCHAR | 20 | N | Y | 파생계약 고유번호 | 상동 |
| 2 | DRV_TCD | 파생유형 | CHAR | 2 | N | N | 01:IRS 02:CRS 03:FXF 04:FXS 05:옵션 06:CDS 07:FRA 08:선물 | 상동 |
| 3 | TRAD_VENUE | 거래장소 | CHAR | 1 | N | N | O:OTC E:거래소 | 상동 |
| 4 | CTR_YMD | 체결일 | CHAR | 8 | N | N | 계약 체결일 | 상동 |
| 5 | EFT_YMD | 개시일 | CHAR | 8 | Y | N | 계약 개시일 | 상동 |
| 6 | MAT_YMD | 만기일 | CHAR | 8 | Y | N | 만기일 | 상동 |
| 7 | NOTIONAL | 명목금액 | NUMERIC | 18,2 | Y | N | 명목 원금 | 상동 |
| 8 | CCY_CD | 통화 | CHAR | 3 | Y | N | 계약 통화 | 상동 |
| 9 | PURPOSE_CD | 목적 | CHAR | 2 | N | N | 01:헤지 02:매매 03:고객대응 04:중개 | 상동 |
| 10 | HEDGE_ACT_YN | 헤지회계 | CHAR | 1 | Y | N | Y:헤지회계적용 | 상동 |
| 11 | CNTRPRTY_CSN | 상대방 | NUMERIC | 10 | Y | N | 거래 상대방 | 상동 |
| 12 | STATUS_CD | 상태 | CHAR | 2 | N | N | 01:유효 02:만기종결 03:조기종결 04:행사 05:해지 | 상동 |
| 13 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 14 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_DRV002M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_DRV002M |
| 테이블한글명 | DRV_금리스왑 |
| 주제영역 | 투자 |
| 도메인 | DRV |
| 유형 | M (Master) |
| 적재주기 | 이벤트 |
| 예상건수 | 약 12,000 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | DRV_NO | 파생번호 | VARCHAR | 20 | N | Y | FK→DRV001M | 상동 |
| 2 | PAY_LEG_TCD | 지급방향 | CHAR | 1 | N | N | F:고정지급 L:변동지급 | 상동 |
| 3 | FXD_RATE | 고정금리 | NUMERIC | 10,6 | Y | N | 고정 금리 | 상동 |
| 4 | FLT_IDX | 변동금리지수 | VARCHAR | 20 | Y | N | CD91/LIBOR/SOFR 등 | 상동 |
| 5 | FLT_SPRD | 변동스프레드 | NUMERIC | 10,6 | Y | N | 변동 스프레드(bp) | 상동 |
| 6 | PAY_FREQ | 지급주기 | CHAR | 1 | Y | N | M:월 Q:분기 S:반기 A:연 | 상동 |
| 7 | DAY_COUNT | 일수계산 | VARCHAR | 10 | Y | N | Act/360, Act/365 등 | 상동 |
| 8 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 9 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_DRV003M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_DRV003M |
| 테이블한글명 | DRV_통화스왑 |
| 주제영역 | 투자 |
| 도메인 | DRV |
| 유형 | M (Master) |
| 적재주기 | 이벤트 |
| 예상건수 | 약 3,500 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | DRV_NO | 파생번호 | VARCHAR | 20 | N | Y | FK→DRV001M | 상동 |
| 2 | PAY_CCY | 지급통화 | CHAR | 3 | N | N | 지급 통화 | 상동 |
| 3 | RCV_CCY | 수취통화 | CHAR | 3 | N | N | 수취 통화 | 상동 |
| 4 | PAY_NOTIONAL | 지급명목 | NUMERIC | 18,2 | Y | N | 지급 측 명목 | 상동 |
| 5 | RCV_NOTIONAL | 수취명목 | NUMERIC | 18,2 | Y | N | 수취 측 명목 | 상동 |
| 6 | PAY_RATE | 지급금리 | NUMERIC | 10,6 | Y | N | 지급 금리 | 상동 |
| 7 | RCV_RATE | 수취금리 | NUMERIC | 10,6 | Y | N | 수취 금리 | 상동 |
| 8 | PRIN_EXCH_YN | 원금교환 | CHAR | 1 | Y | N | Y:원금 교환 N:이자만 | 상동 |
| 9 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 10 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_DRV004M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_DRV004M |
| 테이블한글명 | DRV_선도거래 |
| 주제영역 | 투자 |
| 도메인 | DRV |
| 유형 | M (Master) |
| 적재주기 | 이벤트 |
| 예상건수 | 약 8,500 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | DRV_NO | 파생번호 | VARCHAR | 20 | N | Y | FK→DRV001M | 상동 |
| 2 | FWD_TCD | 선도유형 | CHAR | 2 | N | N | 01:FX선도 02:NDF 03:FRA 04:상품선도 | 상동 |
| 3 | BUY_CCY | 매입통화 | CHAR | 3 | Y | N | 매입 통화(FX) | 상동 |
| 4 | SELL_CCY | 매도통화 | CHAR | 3 | Y | N | 매도 통화(FX) | 상동 |
| 5 | FWD_RATE | 선도환율 | NUMERIC | 18,6 | Y | N | 선도환율/가격 | 상동 |
| 6 | DELIV_YMD | 인도일 | CHAR | 8 | Y | N | 인도일 | 상동 |
| 7 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 8 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_DRV005M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_DRV005M |
| 테이블한글명 | DRV_옵션 |
| 주제영역 | 투자 |
| 도메인 | DRV |
| 유형 | M (Master) |
| 적재주기 | 이벤트 |
| 예상건수 | 약 4,500 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | DRV_NO | 파생번호 | VARCHAR | 20 | N | Y | FK→DRV001M | 상동 |
| 2 | OPT_TCD | 옵션유형 | CHAR | 1 | N | N | C:콜 P:풋 | 상동 |
| 3 | OPT_STYLE | 스타일 | CHAR | 2 | Y | N | 01:유럽식 02:미국식 03:아시아 04:배리어 | 상동 |
| 4 | UNDERLY | 기초자산 | VARCHAR | 200 | Y | N | 기초 자산 | 상동 |
| 5 | STRIKE | 행사가 | NUMERIC | 18,6 | Y | N | 행사 가격 | 상동 |
| 6 | EXP_YMD | 행사기한 | CHAR | 8 | Y | N | 만기(행사) | 상동 |
| 7 | POS_TCD | 포지션 | CHAR | 1 | Y | N | L:매입 S:매도 | 상동 |
| 8 | PREM_AMT | 프리미엄 | NUMERIC | 18,2 | Y | N | 프리미엄 | 상동 |
| 9 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 10 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_DRV006M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_DRV006M |
| 테이블한글명 | DRV_CDS |
| 주제영역 | 투자 |
| 도메인 | DRV |
| 유형 | M (Master) |
| 적재주기 | 이벤트 |
| 예상건수 | 약 1,200 |

**[테이블 설명]**

```
[엔티티정의]
신용부도스왑(CDS). 채권·대출의 신용 위험 이전 계약. 참조 entity의 부도 시 보장 지급.
```

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | DRV_NO | 파생번호 | VARCHAR | 20 | N | Y | FK→DRV001M | 상동 |
| 2 | REF_ENTITY | 참조자 | VARCHAR | 300 | Y | N | 참조 entity | 상동 |
| 3 | SPRD_BPS | 스프레드 | NUMERIC | 8,2 | Y | N | 스프레드(bp) | 상동 |
| 4 | POS_TCD | 포지션 | CHAR | 1 | Y | N | B:보장매입(Long) S:보장매도(Short) | 상동 |
| 5 | TRIG_EVT | 신용사건 | VARCHAR | 500 | Y | N | 신용사건 정의 | 상동 |
| 6 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 7 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_DRV007M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_DRV007M |
| 테이블한글명 | DRV_공정가평가 |
| 주제영역 | 투자 |
| 도메인 | DRV |
| 유형 | M (Master) |
| 적재주기 | 일배치 |
| 예상건수 | 약 700만/년 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | DRV_NO | 파생번호 | VARCHAR | 20 | N | Y | 파생 | 상동 |
| 2 | BASE_YMD | 기준일 | CHAR | 8 | N | Y | 기준일 | 상동 |
| 3 | MTM_VAL | MtM가치 | NUMERIC | 18,2 | Y | N | Mark-to-Market 가치 | 상동 |
| 4 | DELTA | 델타 | NUMERIC | 12,6 | Y | N | 델타 | 상동 |
| 5 | GAMMA | 감마 | NUMERIC | 12,6 | Y | N | 감마 | 상동 |
| 6 | VEGA | 베가 | NUMERIC | 12,6 | Y | N | 베가 | 상동 |
| 7 | THETA | 세타 | NUMERIC | 12,6 | Y | N | 세타 | 상동 |
| 8 | RHO | 로 | NUMERIC | 12,6 | Y | N | 로 | 상동 |
| 9 | VAL_LEVEL | 평가레벨 | CHAR | 1 | Y | N | 1:활성시장 2:관찰가능 3:비관찰 | 상동 |
| 10 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 11 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_DRV008M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_DRV008M |
| 테이블한글명 | DRV_헤지지정 |
| 주제영역 | 투자 |
| 도메인 | DRV |
| 유형 | M (Master) |
| 적재주기 | 이벤트 |
| 예상건수 | 약 8,500 |

**[테이블 설명]**

```
[엔티티정의]
헤지회계 지정. 파생을 헤지수단으로, 특정 자산/부채/예상거래를 헤지대상으로 지정.
```

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | HEDGE_NO | 헤지지정번호 | VARCHAR | 22 | N | Y | 헤지 고유번호 | 상동 |
| 2 | DRV_NO | 파생 | VARCHAR | 20 | N | N | 헤지수단 | 상동 |
| 3 | TGT_TCD | 대상유형 | CHAR | 2 | N | N | 01:공정가헤지 02:현금흐름헤지 03:해외사업순투자헤지 | 상동 |
| 4 | HEDGE_ITEM | 헤지대상 | VARCHAR | 500 | Y | N | 헤지 대상 명시 | 상동 |
| 5 | DOC_YMD | 지정문서일 | CHAR | 8 | Y | N | 헤지문서 작성일 | 상동 |
| 6 | EFFECTIVE_YN | 유효여부 | CHAR | 1 | Y | N | Y:효과적 N:비효과 | 상동 |
| 7 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 8 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_DRV009M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_DRV009M |
| 테이블한글명 | DRV_헤지효과성 |
| 주제영역 | 투자 |
| 도메인 | DRV |
| 유형 | M (Master) |
| 적재주기 | 월배치 |
| 예상건수 | 약 85,000 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | HEDGE_NO | 헤지번호 | VARCHAR | 22 | N | Y | 헤지 | 상동 |
| 2 | TEST_YM | 테스트월 | CHAR | 6 | N | Y | 효과성 테스트 월 | 상동 |
| 3 | OFFSET_RTO | 상계비율 | NUMERIC | 6,2 | Y | N | 상계 비율(%) | 상동 |
| 4 | EFF_RANGE_YN | 범위내여부 | CHAR | 1 | Y | N | Y:80-125% 이내 | 상동 |
| 5 | TEST_METH | 테스트방법 | VARCHAR | 50 | Y | N | Dollar-offset/Regression | 상동 |
| 6 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 7 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_DRV010L

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_DRV010L |
| 테이블한글명 | DRV_이자교환 |
| 주제영역 | 투자 |
| 도메인 | DRV |
| 유형 | L (Log) |
| 적재주기 | 이벤트 |
| 예상건수 | 약 85,000/년 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | PAY_NO | 지급번호 | VARCHAR | 22 | N | Y | 지급 고유번호 | 상동 |
| 2 | DRV_NO | 파생 | VARCHAR | 20 | N | N | IRS/CRS | 상동 |
| 3 | PAY_YMD | 지급일 | CHAR | 8 | N | N | 이자 교환일 | 상동 |
| 4 | DIR_CD | 방향 | CHAR | 1 | N | N | P:지급 R:수취 | 상동 |
| 5 | FIXING_DT | 픽싱일 | CHAR | 8 | Y | N | 변동금리 픽싱일 | 상동 |
| 6 | FIXED_RATE | 확정금리 | NUMERIC | 10,6 | Y | N | 당회차 적용 금리 | 상동 |
| 7 | AMT | 금액 | NUMERIC | 18,2 | Y | N | 교환 금액 | 상동 |
| 8 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 9 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_DRV011M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_DRV011M |
| 테이블한글명 | DRV_ISDA계약 |
| 주제영역 | 투자 |
| 도메인 | DRV |
| 유형 | M (Master) |
| 적재주기 | 이벤트 |
| 예상건수 | 약 450 |

**[테이블 설명]**

```
[엔티티정의]
ISDA Master Agreement. 장외파생 거래상대방과의 기본 계약. CSA는 담보 조건.
```

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | CP_CSN | 상대방 | NUMERIC | 10 | N | Y | 상대방 CSN | 상동 |
| 2 | ISDA_YMD | 계약일 | CHAR | 8 | Y | N | ISDA 체결일 | 상동 |
| 3 | CSA_YN | CSA여부 | CHAR | 1 | Y | N | Y:CSA체결 | 상동 |
| 4 | THRESHOLD | 임계치 | NUMERIC | 18,2 | Y | N | 담보 임계치 | 상동 |
| 5 | MTA | 최소이전액 | NUMERIC | 18,2 | Y | N | 최소 담보 이전액 | 상동 |
| 6 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 7 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_DRV012M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_DRV012M |
| 테이블한글명 | DRV_담보관리 |
| 주제영역 | 투자 |
| 도메인 | DRV |
| 유형 | M (Master) |
| 적재주기 | 일배치 |
| 예상건수 | 약 85,000 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | CP_CSN | 상대방 | NUMERIC | 10 | N | Y | 상대방 | 상동 |
| 2 | BASE_YMD | 기준일 | CHAR | 8 | N | Y | 기준일 | 상동 |
| 3 | EXPOSURE | 익스포저 | NUMERIC | 18,2 | Y | N | 순 MtM 익스포저 | 상동 |
| 4 | COLL_POSTED | 제공담보 | NUMERIC | 18,2 | Y | N | 본행이 제공한 담보 | 상동 |
| 5 | COLL_RECV | 수령담보 | NUMERIC | 18,2 | Y | N | 본행이 수령한 담보 | 상동 |
| 6 | NET_VAR_MARG | 순변동증거금 | NUMERIC | 18,2 | Y | N | 순 변동증거금 | 상동 |
| 7 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 8 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_DRV013L

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_DRV013L |
| 테이블한글명 | DRV_담보이전 |
| 주제영역 | 투자 |
| 도메인 | DRV |
| 유형 | L (Log) |
| 적재주기 | 이벤트 |
| 예상건수 | 약 18,000/년 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | XFR_NO | 이전번호 | VARCHAR | 22 | N | Y | 이전 고유번호 | 상동 |
| 2 | CP_CSN | 상대방 | NUMERIC | 10 | N | N | 상대방 | 상동 |
| 3 | XFR_YMD | 이전일 | CHAR | 8 | N | N | 이전일 | 상동 |
| 4 | DIR_CD | 방향 | CHAR | 1 | N | N | P:제공 R:수령 | 상동 |
| 5 | AMT | 금액 | NUMERIC | 18,2 | Y | N | 이전 금액 | 상동 |
| 6 | COLL_TCD | 담보유형 | CHAR | 2 | Y | N | 01:현금 02:국채 03:우량채 | 상동 |
| 7 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 8 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_DRV014M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_DRV014M |
| 테이블한글명 | DRV_청산 |
| 주제영역 | 투자 |
| 도메인 | DRV |
| 유형 | M (Master) |
| 적재주기 | 이벤트 |
| 예상건수 | 약 12,000 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | DRV_NO | 파생번호 | VARCHAR | 20 | N | Y | 파생 | 상동 |
| 2 | CCP_CD | 청산기관 | VARCHAR | 20 | Y | N | CCP(한국거래소/CME/LCH) | 상동 |
| 3 | CLR_YMD | 청산일 | CHAR | 8 | Y | N | 청산일 | 상동 |
| 4 | INITIAL_MARG | 개시증거금 | NUMERIC | 18,2 | Y | N | 개시 증거금 | 상동 |
| 5 | VARI_MARG | 변동증거금 | NUMERIC | 18,2 | Y | N | 변동 증거금 | 상동 |
| 6 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 7 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_DRV015L

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_DRV015L |
| 테이블한글명 | DRV_조기종결 |
| 주제영역 | 투자 |
| 도메인 | DRV |
| 유형 | L (Log) |
| 적재주기 | 이벤트 |
| 예상건수 | 약 1,200/년 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | UNW_NO | 종결번호 | VARCHAR | 22 | N | Y | 종결 고유번호 | 상동 |
| 2 | DRV_NO | 파생 | VARCHAR | 20 | N | N | 파생 | 상동 |
| 3 | UNW_YMD | 종결일 | CHAR | 8 | N | N | 조기 종결일 | 상동 |
| 4 | UNW_RSN | 사유 | VARCHAR | 500 | Y | N | 조기종결 사유 | 상동 |
| 5 | SETT_AMT | 정산금액 | NUMERIC | 18,2 | Y | N | 정산 금액 | 상동 |
| 6 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 7 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_DRV016M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_DRV016M |
| 테이블한글명 | DRV_상대방CVA |
| 주제영역 | 투자 |
| 도메인 | DRV |
| 유형 | M (Master; 월말) |
| 적재주기 | 월배치 |
| 예상건수 | 약 5,500 |

**[테이블 설명]**

```
[엔티티정의]
CVA(신용가치조정). 상대방 부도 시 예상 손실을 반영한 공정가 조정.
```

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | CP_CSN | 상대방 | NUMERIC | 10 | N | Y | 상대방 | 상동 |
| 2 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 3 | GROSS_MTM | 총MtM | NUMERIC | 18,2 | Y | N | 총 MtM | 상동 |
| 4 | CVA_AMT | CVA금액 | NUMERIC | 18,2 | Y | N | CVA 조정액 | 상동 |
| 5 | DVA_AMT | DVA금액 | NUMERIC | 18,2 | Y | N | 자사 DVA | 상동 |
| 6 | NET_FV | 순공정가 | NUMERIC | 18,2 | Y | N | 조정후 공정가 | 상동 |
| 7 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 8 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_DRV017M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_DRV017M |
| 테이블한글명 | DRV_고객파생 |
| 주제영역 | 투자 |
| 도메인 | DRV |
| 유형 | M (Master) |
| 적재주기 | 이벤트 |
| 예상건수 | 약 18,000 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | DRV_NO | 파생번호 | VARCHAR | 20 | N | Y | 파생 | 상동 |
| 2 | CUST_CSN | 고객 | NUMERIC | 10 | N | N | 고객(기업/개인) | 상동 |
| 3 | CUST_TCD | 고객유형 | CHAR | 1 | Y | N | I:개인 B:기업 | 상동 |
| 4 | MID_SPRD | 중개마진 | NUMERIC | 10,6 | Y | N | 중개 마진 | 상동 |
| 5 | EXPL_MEMO | 설명요약 | VARCHAR | 1000 | Y | N | 설명의무 이행 요약 | 상동 |
| 6 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 7 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_DRV018M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_DRV018M |
| 테이블한글명 | DRV_KIKO |
| 주제영역 | 투자 |
| 도메인 | DRV |
| 유형 | M (Master) |
| 적재주기 | 이벤트 |
| 예상건수 | 약 650 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | DRV_NO | 파생번호 | VARCHAR | 20 | N | Y | 파생 | 상동 |
| 2 | KNOCK_IN | 녹인환율 | NUMERIC | 18,6 | Y | N | 녹인 배리어 | 상동 |
| 3 | KNOCK_OUT | 녹아웃환율 | NUMERIC | 18,6 | Y | N | 녹아웃 배리어 | 상동 |
| 4 | LEVERAGE | 레버리지 | NUMERIC | 5,2 | Y | N | 레버리지 배수 | 상동 |
| 5 | TRIG_YN | 발동여부 | CHAR | 1 | Y | N | Y:녹인발동 | 상동 |
| 6 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 7 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_DRV019L

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_DRV019L |
| 테이블한글명 | DRV_손익실현 |
| 주제영역 | 투자 |
| 도메인 | DRV |
| 유형 | L (Log) |
| 적재주기 | 이벤트 |
| 예상건수 | 약 35,000/년 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | PL_NO | 손익번호 | VARCHAR | 22 | N | Y | 손익 고유번호 | 상동 |
| 2 | DRV_NO | 파생 | VARCHAR | 20 | N | N | 파생 | 상동 |
| 3 | REAL_YMD | 실현일 | CHAR | 8 | N | N | 실현일 | 상동 |
| 4 | REAL_AMT | 실현액 | NUMERIC | 18,2 | Y | N | 실현 손익 | 상동 |
| 5 | PL_TCD | 손익유형 | CHAR | 2 | Y | N | 01:이자 02:평가 03:정산 04:만기 | 상동 |
| 6 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 7 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_DRV020M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_DRV020M |
| 테이블한글명 | DRV_파생한도 |
| 주제영역 | 투자 |
| 도메인 | DRV |
| 유형 | M (Master) |
| 적재주기 | 일배치 |
| 예상건수 | 약 250 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | LIM_CD | 한도코드 | VARCHAR | 20 | N | Y | 한도 고유 | 상동 |
| 2 | LIM_TCD | 유형 | CHAR | 2 | N | N | 01:상대방 02:통화 03:상품 04:만기별 | 상동 |
| 3 | TGT | 대상 | VARCHAR | 100 | Y | N | 한도 대상 | 상동 |
| 4 | LIM_AMT | 한도액 | NUMERIC | 18,2 | Y | N | 한도 | 상동 |
| 5 | USED_AMT | 사용액 | NUMERIC | 18,2 | Y | N | 사용액 | 상동 |
| 6 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 7 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_DRV021M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_DRV021M |
| 테이블한글명 | DRV_VaR |
| 주제영역 | 투자 |
| 도메인 | DRV |
| 유형 | M (Master; 일말) |
| 적재주기 | 일배치 |
| 예상건수 | 약 450,000 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | BOOK_CD | Book | VARCHAR | 20 | N | Y | 거래 Book | 상동 |
| 2 | BASE_YMD | 기준일 | CHAR | 8 | N | Y | 기준일 | 상동 |
| 3 | VAR_95 | 95%VaR | NUMERIC | 18,2 | Y | N | 95% VaR | 상동 |
| 4 | VAR_99 | 99%VaR | NUMERIC | 18,2 | Y | N | 99% VaR | 상동 |
| 5 | ES | 기대손실 | NUMERIC | 18,2 | Y | N | Expected Shortfall | 상동 |
| 6 | HORIZON_DAY | 보유기간 | INT |  | Y | N | VaR 산정 기간 | 상동 |
| 7 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 8 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_DRV022M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_DRV022M |
| 테이블한글명 | DRV_스트레스테스트 |
| 주제영역 | 투자 |
| 도메인 | DRV |
| 유형 | M (Master) |
| 적재주기 | 월배치 |
| 예상건수 | 약 25,000 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | ST_NO | 테스트번호 | VARCHAR | 22 | N | Y | 테스트 고유번호 | 상동 |
| 2 | BOOK_CD | Book | VARCHAR | 20 | N | N | Book | 상동 |
| 3 | SCEN_CD | 시나리오 | VARCHAR | 50 | Y | N | 시나리오 명 | 상동 |
| 4 | EST_LOSS | 예상손실 | NUMERIC | 18,2 | Y | N | 예상 손실 | 상동 |
| 5 | RUN_YMD | 실행일 | CHAR | 8 | Y | N | 테스트 실행일 | 상동 |
| 6 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 7 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_DRV023L

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_DRV023L |
| 테이블한글명 | DRV_규제보고 |
| 주제영역 | 투자 |
| 도메인 | DRV |
| 유형 | L (Log) |
| 적재주기 | 일배치 |
| 예상건수 | 약 450,000/년 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | RPT_NO | 보고번호 | VARCHAR | 22 | N | Y | 보고 고유번호 | 상동 |
| 2 | DRV_NO | 파생 | VARCHAR | 20 | N | N | 파생 | 상동 |
| 3 | RPT_AUTH | 보고기관 | VARCHAR | 100 | Y | N | 금감원/금결원/거래정보저장소 | 상동 |
| 4 | RPT_YMD | 보고일 | CHAR | 8 | N | N | 보고일 | 상동 |
| 5 | UTI | 거래식별자 | VARCHAR | 52 | Y | N | Unique Trade Identifier | 상동 |
| 6 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 7 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_DRV024M

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_DRV024M |
| 테이블한글명 | DRV_신용위험노출 |
| 주제영역 | 투자 |
| 도메인 | DRV |
| 유형 | M (Master; 월말) |
| 적재주기 | 월배치 |
| 예상건수 | 약 5,500 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | CP_CSN | 상대방 | NUMERIC | 10 | N | Y | 상대방 | 상동 |
| 2 | BASE_YM | 기준년월 | CHAR | 6 | N | Y | 기준월 | 상동 |
| 3 | CUR_EXPS | 현재익스포저 | NUMERIC | 18,2 | Y | N | 현 익스포저(MtM) | 상동 |
| 4 | POT_FUT_EXPS | 잠재익스포저 | NUMERIC | 18,2 | Y | N | Potential Future Exposure | 상동 |
| 5 | EAD | EAD | NUMERIC | 18,2 | Y | N | 부도노출액 | 상동 |
| 6 | RWA | 위험가중자산 | NUMERIC | 18,2 | Y | N | RWA | 상동 |
| 7 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 8 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## TB_ADW_DRV025H

| 속성 | 값 |
|---|---|
| 테이블명 | TB_ADW_DRV025H |
| 테이블한글명 | DRV_파생상태이력 |
| 주제영역 | 투자 |
| 도메인 | DRV |
| 유형 | H (History) |
| 적재주기 | 이벤트 |
| 예상건수 | 약 85,000 |

**[컬럼 정의]**

| # | 컬럼명 | 컬럼한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
|---|---|---|---|---|---|---|---|---|
| 1 | DRV_NO | 파생 | VARCHAR | 20 | N | Y | 파생 | 상동 |
| 2 | MDFC_TS | 변경일시 | TIMESTAMP |  | N | Y | 변경 시점 | 상동 |
| 3 | BFM_STAT | 변경전 | CHAR | 2 | Y | N | 이전 | 상동 |
| 4 | AFM_STAT | 변경후 | CHAR | 2 | N | N | 신규 | 상동 |
| 5 | MDFC_CACD | 사유 | CHAR | 3 | Y | N | 사유 | 상동 |
| 6 | ETCL_BASE_YMD | ETL기준년월일 | DATE |  | N | N | 신규 또는 변경되는 데이터 추출 기준일자 | 상동 |
| 7 | ETCL_JOB_TS | ETL작업일시 | TIMESTAMP |  | N | N | 데이터가 신규 또는 변경된 DW의 ETL작업일시 | 상동 |

---

## 다음 파일

`97a_FNA_회계기본.md` (FNA001~017, 17 테이블)
