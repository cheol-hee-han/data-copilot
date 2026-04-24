# 증분 시딩 도구 요구사항 — `seed_postgres_incremental.py`

> **작성일**: 2026-04-20
> **작성자**: Claude (아키텍트 위임)
> **구현 주체**: 별도 구현 담당자 (에이전트 또는 사람)
> **구현 대상**: `devtools/scripts/seed_postgres_incremental.py` (신규 스크립트)
> **관련 이슈**: K-01 등 `CURRENT_DATE` 의존 시나리오가 `STD_DT = 2026-03-21` 고정 시드와 30일 이상 괴리 → 0-row 실패.
> **관련 메모리**: feedback_holistic_working_principle, feedback_stay_within_agreed_design.

## 목차

- [§1. 배경 & 목적](#1-배경--목적) — 왜 필요한가, 전체 재시딩과의 차이
- [§2. 범위 & 비범위](#2-범위--비범위) — 건드리는/건드리지 않는 영역
- [§3. 설계 원칙](#3-설계-원칙) — 6관점(디자인·일관성·유지보수·효율·기능·성능)
- [§4. 테이블 전수 분류](#4-테이블-전수-분류) — 4카테고리 × 처리 전략
- [§5. 카테고리별 생성 전략](#5-카테고리별-생성-전략) — 정적/SCD/스냅샷/이력
- [§6. 무결성 규칙](#6-무결성-규칙) — PK/FK/도메인 제약
- [§7. 일자별 파이프라인](#7-일자별-파이프라인) — per-day 실행 순서·트랜잭션
- [§8. 결정성 & 재현성](#8-결정성--재현성) — seeded RNG·idempotency
- [§9. 테이블 자동 탐지](#9-테이블-자동-탐지) — 카탈로그/information_schema 기반
- [§10. CLI 설계](#10-cli-설계) — 인자·기본값·모드
- [§11. 검증 & 관찰성](#11-검증--관찰성) — 로그·집계·사후 점검 쿼리
- [§12. 테스트 계획](#12-테스트-계획) — 단위/통합/회귀
- [§13. 기존 코드 연계 & 적용 경로](#13-기존-코드-연계--적용-경로) — `seed_postgres.py` 재사용·호출 관계
- [§14. 리스크 & 열린 질문](#14-리스크--열린-질문) — 결정이 필요한 항목
- [§15. 구현 체크리스트](#15-구현-체크리스트) — PR 분할 권고안

---

## 1. 배경 & 목적

### 1.1 문제
- 현행 `devtools/scripts/seed_postgres.py`는 **실행 시점 `TODAY = date.today()` 를 스냅샷 기준일로 사용**한다 (seed_postgres.py:67).
- 재시딩(=`TRUNCATE` 후 전량 재삽입)은 다음 비용이 크다:
  - 572개 테이블 DDL + 22개 ★ 테이블 데이터 생성 (수 분)
  - Qdrant·MongoDB 와의 정합성 (참고 SQL의 `'2026-03-21'` 리터럴 동기화 여부)
  - 기존 테스트 결과(reports)와의 이전성 단절
  - 기존 테이블에 생성된 FK 의존 데이터 손실 가능성
- 결과적으로 **특정 기준일 구간만 채우고 싶은 경우**가 반복적으로 발생한다 (예: 3/21 ~ 4/20 구간 `STD_DT` 스냅샷 + `CURRENT_DATE` 기준일 보강).

### 1.2 목표
- **전체 재시딩 없이** 원하는 기준일 범위(예: `--from 2026-03-22 --to 2026-04-20`)의 **스냅샷·이력·변동을 안전하게 증분 적재**.
- 마스터 테이블은 **UPSERT**, 이력·스냅샷 테이블은 **INSERT (PK 중복 시 skip 또는 옵션으로 replace)**.
- **실제 운영계 데이터와 유사한 품질** — 도메인 규칙, 잔액 추이, 상태 전이, 이벤트 빈도 등.
- 재실행 가능(idempotent). 동일 범위·동일 seed 면 동일 결과.

### 1.3 비목표 (이번 스코프 아님)
- Qdrant/MongoDB 증분 — 별도 작업.
- 운영계 DDL 변경 — 현행 DDL 그대로 사용.
- 제3자 BI·ETL 경로 — `seed_postgres_incremental.py` 단일 바이너리 스크립트로 한정.

---

## 2. 범위 & 비범위

### 2.1 범위
- ADWOWN 스키마 **전체 572 테이블을 동등하게 중요**한 대상으로 간주하여 증분 커버한다. ★/비-★ 구분 없음.
- 마스터(M)·파라미터(P)·스냅샷(S)·이력(H/L)·로그(G) 등 **테이블 유형·PK 일자 컬럼** 유무만으로 카테고리를 자동 분기.
- 지점·고객·상품 등 기준 마스터는 기존 풀을 재사용. 신규 고객·계좌 발생은 **옵션**으로 지원.

### 2.2 비범위
- `sys_schema`(체크포인터 DC 테이블)는 건드리지 않음 — 애플리케이션 소유.
- `ADWOWN` 이외 스키마는 대상 외.
- 운영계 실제 데이터로부터의 샘플링·익명화는 대상 외 (합성 데이터만).

### 2.3 안전 가드
- **운영 DB 접근 금지**: `TEST_DB_NAME` 기본값이 `test_db` 가 아니면 `--force` 필수.
- `readonly_user` 계정으로 접속 시 즉시 실패.
- `--dry-run` 플래그로 SQL 생성만 수행(실행 안 함).

---

## 3. 설계 원칙

### 3.1 6관점 적용
- **디자인**: 기존 `seed_postgres.py` 의 생성 함수(`_insert_customers`, `_insert_deposits` 등)와 **재사용 가능 부분은 import**, 증분 특화 로직만 신규로 추가. 별도 레이어로 분리하지 않음(과도한 계층 금지).
- **일관성**: 상수(`IMPERFECTION_RATE`, `_DOMAIN_AMT`, `_CODE_LOOKUP`, 풀 상수)·유틸(`_rnd_date`, `_gen_name`)·제약 보정(`_fix_row_constraints`)을 모두 **현행 스크립트로부터 import** — 증분 로직이 풀 시드의 도메인 규칙과 어긋나지 않아야 함.
- **유지보수성**: `seed_postgres.py` 가 변경되면 자동 반영되도록 얇은 래퍼 중심. 카테고리 분류를 문서화(§4)하고 코드 주석에 참조.
- **효율성**: per-day per-table 벌크 INSERT (`execute_values`). 일자 수 × 테이블 수의 O(N·M) 루프를 단순 Python loop로 구현(최적화는 관찰 후).
- **기능**: `STD_DT`·`BASE_DT`·`EVAL_DT`·`TR_DT` 등 **일자 의미 컬럼 전수 커버**. 누락 없음이 1순위.
- **성능**: 30일 × 22 ★ + 일부 비-★ 스냅샷 → 예상 10~30만 행. 수십 초 내 완료 목표. 실측 후 배치 크기 튜닝.

### 3.2 Anti-patterns (하지 말 것)
- ❌ `DELETE + INSERT` 방식(기존 PK 삭제 → 기존 FK 의존 이력 손실 위험).
- ❌ 전량 재시딩을 부분적으로 반복(증분 의미 상실).
- ❌ ★ 테이블 1~2개만 증분하고 나머지 방치(현실감·조인 불일치).
- ❌ 날짜 필드를 문자열 리터럴(`'2026-04-20'`)로 박아넣기(dialect 이식성 저하).
- ❌ 시드된 값이 매 실행마다 달라지는 비결정성(테스트 회귀 디버깅 불가).

---

## 4. 테이블 전수 분류

> **접미 문자 규칙**: 테이블명 마지막 1글자가 유형을 나타낸다.
> `M`=마스터, `H`=이력(History), `S`=스냅샷/집계(Snapshot/Summary), `P`=파라미터/상품(Product), `L`=로그/거래라인(Log/Line), `C`=코드(Code), `G`=일반 로그(Generic log).
>
> **중요 원칙**: 572개 테이블 **전부 동등 중요**. ★/비-★ 구분 없음. 아래 카테고리는 "처리 전략" 기준이지 중요도 기준이 아니다.

### 4.1 카테고리 (증분 처리 전략 관점)

| # | 카테고리 | 분류 규칙 | 증분 처리 | 대표 테이블 | 비고 |
|---|---------|----------|-----------|------------|------|
| B | **Slow-change 마스터** | 유형 `M`·`C`·`P`(`STD_DT` 등 일자 **비-PK** 컬럼 보유, 또는 일자 컬럼 없음) | 일자 컬럼 있으면 **UPSERT(STD_DT=target_to)** / 없으면 **무변동 유지** | CSC101M, DEP201P, LNB301M, CRD401M, COM001M, 코드 마스터 전체 | 일자 컬럼 없으면 실제 I/O 발생 없음. 강제 skip 화이트리스트는 두지 않는다. |
| C | **Daily/Monthly 스냅샷** | 유형 `S` 또는 PK에 `STD_DT`/`BASE_DT`/`BASE_YM`/`EVAL_DT`/`CALC_DT`/`BAL_DT` | **일자별 INSERT** | DEP202S, CSC102H, FND601P/602P, PNB904P, FIN1306S | PK = (business_key, date). 기존 중복 시 skip. |
| D | **이력/거래 이벤트** | 유형 `H`·`L`·`G`, 또는 PK에 `TR_DT`·`SETL_DT`·`CONTACT_DT`·`EVENT_DT` 등 | **일자별 이벤트 INSERT** | TRX701L, FXD501L, MKT1202M | 영업일 기준 분포. 신규 이벤트만 추가. |
| E | **시계열 파라미터** | 유형 `P` + PK에 일자 컬럼 | **일자별 INSERT (random walk)** | FXB502M, RSK1101M, MKT1201M(기간 겹침) | 통화·지표·상품 × 일자 곱. |

분류 우선순위: **PK 일자 컬럼 유무 → 유형 접미 문자 → 컬럼 집합**. `is_static()` 화이트리스트나 "정적 마스터 A 카테고리" 는 **폐기**한다 (모든 테이블이 자연스러운 규칙으로 분류되어야 함).

### 4.2 주요 시계열 테이블 일자 컬럼 참조표

자동 카테고리 분류와 별개로, 일자 컬럼 선택 시 아래 컬럼 우선순위를 참고한다. `_gen_col_value`·`_fix_row_constraints` 의 금융 도메인 규칙은 **유형 구분 없이** 모든 테이블에 동일 적용.

| 테이블 | 유형 | 카테고리 | 대표 일자 컬럼 | 증분 전략 | 참고 |
|--------|------|---------|---------------|-----------|------|
| TB_ADW_COM001M | 마스터 | B | - | 일자 컬럼 없음 → 변동 없음 | 부점 20건 |
| TB_ADW_COM002M | 마스터 | B | - | 일자 컬럼 없음 → 변동 없음 | 등급코드 5건 |
| TB_ADW_CSC101M | 고객현재 | B | STD_DT | UPSERT (STD_DT 갱신) | 잔액·등급 진행 |
| TB_ADW_CSC102H | 고객이력 | C+D | STD_DT | 일자별 INSERT | 월말 스냅샷 + 변동일 |
| TB_ADW_CSP103M | 고객마케팅 | B | - | 변동 없음 (신규 row 옵션) | |
| TB_ADW_DEP201P | 수신현재 | B | STD_DT | UPSERT (잔액·STD_DT) | random walk |
| TB_ADW_DEP202S | 수신스냅샷 | C | BASE_DT | 일자별 INSERT | |
| TB_ADW_LNB301M | 여신현재 | B | STD_DT | UPSERT | 상환·연체 |
| TB_ADW_LNB302M | 여신승인 | B | - | 변동 없음 | |
| TB_ADW_CRD401M | 카드 | B | STD_DT | UPSERT | |
| TB_ADW_FXD501L | 외환딜 | D | SETL_DT | 일자별 INSERT | 일 1~5건 |
| TB_ADW_FXB502M | 환율 | E | BASE_DT | 일자별 INSERT | 5통화 × 일자 |
| TB_ADW_FND601P | 펀드잔고 | C | STD_DT | 일자별 INSERT | |
| TB_ADW_FND602P | 펀드평가 | C | STD_DT | 일자별 INSERT | |
| TB_ADW_TRX701L | 거래 | D | TR_DT | 일자별 INSERT | 평일 100/주말 20 |
| TB_ADW_INS803M | 보험 | B | - | 변동 없음 | |
| TB_ADW_PNB904P | 연금 | C | STD_DT | 일자별 INSERT | |
| TB_ADW_RSK1101M | 리스크 | E | STD_DT | 일자별 INSERT | 10종 × 일자 |
| TB_ADW_MKT1201M | 캠페인 | E/B | START_DT | 기간 겹침 건만 | |
| TB_ADW_MKT1202M | 캠페인대상 | D | CONTACT_DT | 일자별 INSERT | |
| TB_ADW_FIN1306S | 손익월 | C | BASE_YM | 월별 INSERT | |
| TB_ADW_WMB1401M | WM고객 | B | - | 변동 없음 (신규 row 옵션) | |

### 4.3 전체 572개 테이블 동일 처리 규칙
- `_infer_table_type(name)` (seed_postgres.py:807)로 유형 자동 판정.
- PK 컬럼 집합을 스캔해 `DATE_PK_COLS`(STD_DT·BASE_DT·BASE_YM·EVAL_DT·CALC_DT·TR_DT·SETL_DT·EVENT_DT·BAL_DT·EFF_DT 등) 포함 여부 판정.
- 분류 결과:
  - PK 일자 컬럼 있음 + 유형 `S`/`M`/`P` → **C (일자별 INSERT)**
  - PK 일자 컬럼 있음 + 유형 `H`/`L`/`G` → **D (이벤트 INSERT)**
  - PK 일자 컬럼 있음 + 유형 `P` 중 시계열 상수(환율·지표·NAV) → **E**
  - PK 일자 컬럼 없음 → **B (UPSERT: 비-PK 일자 컬럼 있으면 갱신, 없으면 그대로 유지)**
- **금융 도메인 값 생성 규칙(`_gen_col_value`·`_fix_row_constraints`·도메인 풀)은 ★/비-★ 구분 없이 동일 적용**. 비-★ 도 ★와 동일 수준으로 금액 도메인·상태 전이·FK 정합성 규칙을 거친다.

---

## 5. 카테고리별 생성 전략

> **폐기**: 이전 설계의 "카테고리 A(정적 마스터 skip)" 은 제거한다. 일자 컬럼이 없는 테이블은 자연스럽게 B 로 분류되어 변동 없이 유지된다 (별도 skip 로직 불필요).

### 5.2 카테고리 B (Slow-change 마스터)
- **UPSERT 대상 컬럼**:
  - `STD_DT` (있을 경우) → `target_date_to`로 일괄 갱신.
  - 잔액성 컬럼 (`BAL_AMT`, `LN_BAL_AMT`, `MON_USE_AMT`, `TOT_ASSET_AMT`):
    - **Random walk**: `new = max(0, prev + normal(mean=0, std=prev*0.05))` 또는 `prev * uniform(0.9, 1.1)`.
    - 여신 `LN_BAL_AMT` 는 상환 방향 편향(`uniform(0.98, 1.00)`).
    - 수신 `BAL_AMT` 는 중립(`uniform(0.92, 1.08)`).
  - 상태 컬럼 (`LN_STCD`, `ACT_STCD`): 전이 확률 행렬 기반.
    - 정상(01) → 정상 95%, 연체(03) 3%, 해지(02/04) 2%.
    - 연체(03) → 정상 15%, 연체 80%, 상각(05) 5%.
  - 연체 관련(`OVDU_DY_CN`, `OVDU_AMT`): 연체면 `prev + 1`, 아니면 0.

### 5.3 카테고리 C (Daily/Monthly 스냅샷)
- **Per-day INSERT** (일자 루프).
- PK = `(business_key, date_col)`. 기존 `(csn/acn/ln_no, STD_DT)` 조합이 DB 에 이미 있으면 **skip**.
- 기준일의 값:
  - 잔액성 → 전일 값 기반 random walk (§5.2 규칙 재사용).
  - 평가성 (`EVAL_AMT`, `NAV`) → 시장 변동 시뮬레이션 (일 등락률 `normal(0, 1%)`).
- **월 스냅샷** (FIN1306S, BASE_YM PK): 달별로 1건. 증분 범위에 월말이 포함된 경우에만 해당 월 생성.

### 5.4 카테고리 D (이력/거래)
- **Per-day event INSERT** (일자 × 건수 루프).
- 건수 분포:
  - 거래(TRX701L): 영업일 기준 일평균 100건, 주말 20건.
  - 외환딜(FXD501L): 일 1~5건.
  - 등급이력(CSC102H 변동일): 전체 고객의 0.5%/일.
  - 캠페인 반응(MKT1202M): 활성 캠페인 × 일 0.5~2건/건당.
- **영업일 판정**: 간단한 평일 필터(월~금). 공휴일 테이블(COM 공휴일)이 있다면 참조.
- **시계열 요소**:
  - `TR_DT` = 대상일.
  - `TR_TM` = 업무 시간(09:00~17:00)에 편중.
  - FK 풀은 시드 전에 수집(`_collect_star_values` 재사용).

### 5.5 카테고리 E (시계열 파라미터)
- **환율 FXB502M**: 5통화 × 증분 구간 일자. 전일 대비 `uniform(0.97, 1.03)` random walk.
- **리스크 RSK1101M**: 10 지표 × 증분 구간 (일 또는 월). 허용 범위 내 (`IND_CONFIG`, seed_postgres.py:1924) 머무름.
- **캠페인 MKT1201M**: 증분 기간과 캠페인 기간이 **겹치는** 캠페인만 대상. 신규 캠페인 주당 1건 옵션.

---

## 6. 무결성 규칙

### 6.1 PK 제약
- 카테고리 B(UPSERT): `INSERT ... ON CONFLICT (pk) DO UPDATE SET ...`.
- 카테고리 C/D/E(INSERT): `INSERT ... ON CONFLICT DO NOTHING`.
- `BIGSERIAL` PK 는 시퀀스 자동 증가에 맡김.

### 6.2 FK 풀 재사용
- 실행 시작 시 `_collect_star_values(cur)` (seed_postgres.py:2155) 호출하여 **기존 EDPS_CSN·ACN·LN_NO 등을 모두 수집**.
- 신규 고객·계좌를 만들지 않는 한 이 풀로부터만 샘플링 → FK 깨질 염려 없음.
- 풀이 비어있으면 **전체 재시딩을 먼저 수행하라고 친절히 안내**하고 종료.

### 6.3 도메인 제약 (`_fix_row_constraints` 재사용)
- 개시일 < 만기일, 보장금액 >= 보험료, 연체 등급 → 상태 코드 등 모든 기존 규칙 그대로 적용.
- 증분 특유 규칙 추가:
  - `STD_DT` >= 계좌 `OPEN_DT`: 기준일이 개시일보다 이전이면 해당 row skip.
  - `STD_DT` <= 만기일(`MTRTY_DT`, `INS_END_DT`): 초과 시 skip 또는 상태를 만기(`04`)로.
  - `PAY_DT` <= 기준일: 미래 결제일은 생성하지 않음.

### 6.4 TYPE-2 불완전성 유지
- `IMPERFECTION_RATE = 0.03`(약 3%) 기존 규칙 그대로 — 증분에서도 미정의 코드값이 자연스럽게 섞이게.

### 6.5 TYPE-4 쌍 테이블 일관성
- CSC101M ↔ CSC102H ↔ CSP103M: CSM/CUS_DCD/BLNG_BRCD 동일성 유지.
- DEP201P ↔ DEP202S: TOT_BAL_AMT = BAL_AMT ± 일변동.
- FND601P(잔고) ↔ FND602P(평가): 동일 FND_ACN/STD_DT 키 맞춤.
- LNB301M ↔ LNB302M: LN_NO 동일, 승인금액 >= 실행금액.

---

## 7. 일자별 파이프라인

### 7.1 실행 순서 (per-day)
```
# 선-작업 (day 루프 진입 전): 카테고리 B (UPSERT) — target_to 기준 1회만 실행
#   순서: COM → CSC101M → DEP201P → LNB301M → CRD401M → INS → WMB → 기타 B

for d in target_dates:
    1. 카테고리 C: INSERT 스냅샷
       순서: DEP202S → CSC102H → FND601P → FND602P → PNB → (월말이면 FIN1306S) → 기타 C
    2. 카테고리 D: INSERT 이벤트
       순서: TRX701L → FXD501L → MKT1202M → 기타 D
    3. 카테고리 E: INSERT 시계열
       순서: FXB502M → RSK1101M → MKT1201M(신규) → 기타 E
    4. commit
```

### 7.2 트랜잭션 경계
- **일자별 커밋** — 중단·재개 편의성. 30일 × 22 테이블 × 1 커밋 = 약 30회 커밋.
- 실패 시 해당 일자 rollback, 이후 일자 진행 여부는 `--on-error {stop, skip-day, continue}` 로 제어.

### 7.3 카테고리 B 의 "가장 최근 일자" 처리
- UPSERT 이므로 기간의 **마지막 일자(target_to)** 만 실제로 DB 에 반영됨 (이전 일자 덮어씀).
- 하지만 카테고리 C 스냅샷은 일자별로 쌓이므로, **B 는 매 반복 갱신 → C 는 그 시점 값을 기록**하는 형태로 구현.
- 성능상 최적화: B 갱신은 per-day 대신 **일자 루프 내 C 삽입 전 in-memory 상태 갱신 → 마지막에 DB B 테이블 1회 UPSERT**.

---

## 8. 결정성 & 재현성

### 8.1 RNG seed
- CLI 인자 `--seed 42` (기본).
- 실행 시작 직후 `random.seed(args.seed)` — 동일 seed + 동일 범위 = 동일 결과.
- 병렬화는 현재 대상 아님(단일 프로세스).

### 8.2 Idempotency
- `ON CONFLICT DO NOTHING` (C/D/E) 로 재실행 안전.
- `ON CONFLICT DO UPDATE` (B) 는 **동일 seed 면 동일 결과**. 다른 seed 로 재실행하면 덮어쓰기 발생 → 경고 출력.

### 8.3 상태 진행의 일관성
- Random walk 의 연속성을 위해 **일자별 상태(`state_by_key`) 를 메모리에 유지**.
- 기준 시작값 = DB 의 현재값(`SELECT ... FROM TB_ADW_DEP201P WHERE ACN=...`).
- 증분 시작 시점부터의 random walk 는 seed 고정이면 재현 가능.

---

## 9. 테이블 자동 탐지

### 9.1 탐지 방법
- `parse_table_catalog()` (seed_postgres.py:734) 재사용 → 572 테이블 + PK 메타.
- `_infer_table_type()` 로 유형 판정.
- `pk_cols` 에 날짜 컬럼(`STD_DT`, `BASE_DT`, `BASE_YM` 등) 포함 여부로 카테고리 자동 분류.

### 9.2 자동 분류 규칙
```python
def categorize(table_info) -> Literal["B","C","D","E"]:
    """일자 PK 유무 + 유형 접미 문자만으로 결정. 화이트리스트·정적 skip 없음."""
    name = table_info["name"]
    type_char = name[-1]       # M/H/S/P/L/C/G
    pk_cols = table_info["pk_cols"]
    has_date_pk = any(c in DATE_PK_COLS for c in pk_cols)

    if has_date_pk:
        if type_char in ("H", "L", "G"): return "D"
        if type_char == "P" and name in TIMESERIES_P_TABLES: return "E"
        return "C"  # S, M, P, C with date PK
    # 일자 PK 없음 → Slow-change 마스터로 통일 (일자 컬럼이 비-PK 로 있으면 UPSERT, 없으면 무변동)
    return "B"

DATE_PK_COLS = {
    "STD_DT","BASE_DT","BASE_YM","EVAL_DT","CALC_DT","TR_DT","SETL_DT",
    "EVENT_DT","BAL_DT","EFF_DT","CONTACT_DT","TR_TM","NAV_DT","DIV_DT",
    "LOGIN_DT","SCORE_DT","VISIT_DT","PAY_DT","RPAY_DT","AGREE_DT","REQ_DT"
}
TIMESERIES_P_TABLES = {"TB_ADW_FXB502M", "TB_ADW_RSK1101M"}
```

### 9.3 분류 결과 로그
- 실행 시작 시 카테고리별 개수를 출력. 예:
  ```
  카테고리 분류: B(slow/master)=353  C(snapshot)=128  D(history)=77  E(timeseries)=14
  ```

---

## 10. CLI 설계

### 10.1 인자
```
python devtools/scripts/seed_postgres_incremental.py \
    --from 2026-03-22 \
    --to   2026-04-20 \
    [--seed 42] \
    [--tables TB_ADW_DEP202S,TB_ADW_TRX701L]  # 특정 테이블만
    [--categories C,D]                          # 특정 카테고리만
    [--new-customers 0]                         # 신규 고객 수 (기본 0)
    [--new-accounts 0]                          # 신규 계좌 수 (기본 0)
    [--on-conflict skip|replace]                # PK 충돌 정책 (기본 skip)
    [--on-error stop|skip-day|continue]         # 일자 단위 에러 정책 (기본 stop)
    [--dry-run]                                 # SQL 생성만
    [--force]                                   # 비-test DB 대상 안전장치 해제
    [--verbose]                                 # 일자별 상세 로그
```

### 10.2 기본값 & 검증
- `--from`/`--to` 없으면 에러 (명시성 강제).
- `--to > --from` 검증, 최대 구간 365일(초과 시 경고 + `--force` 요구).
- `test_db` 이외 DB 는 `--force` 없으면 거부.
- `readonly_user` 는 즉시 거부.

### 10.3 출력 예시
```
[2026-04-20 15:00:00] incremental seed 시작
  대상 구간: 2026-03-22 ~ 2026-04-20 (30일)
  seed=42, on-conflict=skip, on-error=stop
  카테고리 분류: A=12 B=341 C=128 D=77 E=14
  FK 풀: EDPS_CSN=500 ACN=600 LN_NO=800 ...

[2026-03-22]
  B: UPSERT  CSC101M (500), DEP201P (600), LNB301M (800) ...
  C: INSERT  DEP202S (+600), CSC102H (+12), FND601P (+300) ...
  D: INSERT  TRX701L (+105), FXD501L (+3), MKT1202M (+8) ...
  E: INSERT  FXB502M (+5), RSK1101M (+10) ...
  commit.

... (반복)

[2026-04-20]
  ... commit.

[요약]
  총 커밋: 30
  총 INSERT: 42,180
  총 UPSERT: 4,620
  총 SKIP (PK 충돌): 120
  소요: 38.4s
```

---

## 11. 검증 & 관찰성

### 11.1 사후 점검 쿼리 (자동 실행)
스크립트 종료 시 다음을 자동 수행하여 출력:

```sql
-- 1. 기준일 분포 확인
SELECT MIN(STD_DT), MAX(STD_DT), COUNT(*) FROM ADWOWN.TB_ADW_DEP201P;
SELECT MIN(BASE_DT), MAX(BASE_DT), COUNT(*) FROM ADWOWN.TB_ADW_DEP202S;
SELECT MIN(TR_DT), MAX(TR_DT), COUNT(*) FROM ADWOWN.TB_ADW_TRX701L;
...

-- 2. FK 고아 체크
SELECT COUNT(*) FROM ADWOWN.TB_ADW_DEP201P d
 LEFT JOIN ADWOWN.TB_ADW_CSC101M c ON d.EDPS_CSN = c.EDPS_CSN
 WHERE c.EDPS_CSN IS NULL;
-- 0 이어야 함

-- 3. 일자 분포 균일성
SELECT STD_DT, COUNT(*)
 FROM ADWOWN.TB_ADW_DEP202S
 WHERE BASE_DT BETWEEN :from AND :to
 GROUP BY STD_DT ORDER BY 1;
```

### 11.2 로그
- 모든 로그는 stderr (진행 상황). stdout 은 요약(JSON) — CI 파싱 용이.
- `--verbose` 시 각 테이블 per-day INSERT 건수.

### 11.3 메트릭
- 종료 시 JSON 요약 파일 `devtools/scripts/incremental_seed_report_{from}_{to}_{seed}.json` 생성.
  ```json
  {
    "from": "2026-03-22",
    "to":   "2026-04-20",
    "seed": 42,
    "duration_sec": 38.4,
    "tables": {
      "TB_ADW_DEP202S": {"inserted": 18000, "skipped": 0},
      ...
    },
    "integrity": {
      "fk_orphans": 0,
      "type2_rate": 0.028
    }
  }
  ```

---

## 12. 테스트 계획

### 12.1 단위 테스트 (`tests/unit/devtools/test_incremental_seed.py`)
- `categorize()` 분류 결정 테이블 (테이블 유형 × PK 패턴).
- `random_walk()` 순수 함수 결정성.
- 상태 전이 확률 분포 (대수의 법칙으로 검증).
- 영업일 판정.

### 12.2 통합 테스트 (`tests/integration/devtools/test_incremental_seed_e2e.py`)
- 작은 구간(3일) 증분 실행 → 예상 건수 검증.
- 재실행(동일 seed, 동일 구간) → skip 수 = 이전 INSERT 수.
- 구간 겹침(겹치는 3일만) → 겹친 일자는 skip, 신규 일자만 INSERT.
- FK 고아 0건 회귀 테스트.

### 12.3 회귀 테스트
- 전체 재시딩 후 증분 실행 → 기존 골든셋 SQL 샘플 10종 실행 결과가 예상 범위 내.
- K-01 시나리오(`지점별 여신 잔액`) 는 증분 실행 후 **0-row 가 아니게** 복구되어야 함 (acceptance criteria).

### 12.4 실사용 (acceptance)
- `--from 2026-03-22 --to 2026-04-20` 실행 → K-01, V-01, N-04 등 CURRENT_DATE 의존 시나리오 재실행 통과.

---

## 13. 기존 코드 연계 & 적용 경로

### 13.1 Import 관계
```python
# seed_postgres_incremental.py 상단
from devtools.scripts.seed_postgres import (
    TEST_DB_CONNINFO,
    IMPERFECTION_RATE,
    PK_TYPE_MAP,
    _connect,
    _rnd_date,
    _gen_name,
    _gen_tel,
    _gen_email,
    _base_ym,
    _extract_domain,
    _DOMAIN_GROUP,
    _DOMAIN_BIZ_COLS,
    _DOMAIN_PRODUCT_NAMES,
    _collect_star_values,
    _resolve_cols,
    _gen_col_value,
    _fix_row_constraints,
    _infer_table_type,
    parse_table_catalog,
    # 상수: BRANCHES, CUST_TYPES, DEPOSIT_PRODUCTS, LOAN_TYPES, ... (약 40개)
)
```

### 13.2 변경 영향
- `seed_postgres.py` 는 **import 가능하도록 최소 보정** 필요:
  - 현재 `TODAY = date.today()` 는 **모듈 로드 시점** 에 고정된다 (중요). 증분 모듈도 이 값을 공유.
  - 기존 풀 시드 동작에 영향 없음.
- 신규 스크립트 외 기존 코드 수정은 **원칙적으로 0**.

### 13.3 적용 경로
1. 구현 담당자가 `seed_postgres_incremental.py` 를 `devtools/scripts/` 에 추가.
2. `devtools/scripts/seed_all.sh` 는 그대로 풀 시드 용도 유지.
3. `devtools/scripts/seed_incremental.sh` 신설: 환경변수(`FROM`, `TO`)로 증분 스크립트 호출.
4. `docs/guides/dev-guidelines.md` 에 사용법 섹션 추가 (담당자 주의).

---

## 14. 리스크 & 열린 질문

### 14.1 리스크
| # | 리스크 | 영향 | 완화 |
|---|-------|------|------|
| R1 | 기존 ★ 테이블 `STD_DT` 와 새 기준일 간 **해석 충돌** | 카테고리 B UPSERT 가 기존 값을 덮어씀 | `--on-conflict skip` 기본. `replace` 는 명시적 옵션. |
| R2 | FK 풀 수집 후 **다른 세션의 동시 INSERT** 로 FK 불일치 | 고아 레코드 발생 | 실행 중 DB 독점 권고. 점검 쿼리로 사후 검증. |
| R3 | Random walk 이 비현실적 값(예: 음수 잔액) 생성 | 도메인 규칙 위반 | `max(0, ...)` 와 `_fix_row_constraints` 로 보정. |
| R4 | 30일 × 572 테이블 = 17,160 작업 → 성능 저하 | 실행 시간 급증 | 카테고리 A skip, per-day 벌크 INSERT, 프로파일링 |
| R5 | Qdrant 참고 SQL의 `'2026-03-21'` 리터럴 괴리 | LLM 이 과거 기준일로 SQL 생성 | 이번 범위 아님. 별도 문서로 이관. |

### 14.2 열린 질문 (구현자가 결정하거나 사용자에게 확인 필요)
- **Q1**: 카테고리 B 의 "마지막 일자만 반영" vs "일자별 UPSERT" — 후자는 이력 손실이 크지만 관찰성 ↑. 성능·용도 기준 결정.
- **Q2**: 신규 고객·계좌 생성 기본값 0 vs 일 평균 (예: 일 2명) — 실제 운영 근사도 고려.
- **Q3**: 공휴일 캘린더 — 한국 공휴일 하드코딩 vs 공휴일 테이블(TB_ADW_COM***H?) 참조.
- **Q4**: 증분 범위가 이미 데이터가 있는 구간을 포함할 때 — 전면 overwrite 옵션 필요한가?
- **Q5**: Qdrant 재임베딩과의 동기화 시점 — 본 범위 외지만 참고 SQL의 `current_date` placeholder 화 여부 결정 필요.

---

## 15. 구현 체크리스트

### 15.1 PR 분할 권고
1. **PR-1 (setup)**: 스크립트 스켈레톤, 인자 파싱, `seed_postgres.py` import 구조 확립, `--dry-run` 만 동작.
2. **PR-2 (classification)**: `categorize()` + 테이블 카탈로그 로드 + 분류 로그 출력까지. **572 테이블 전수 대상**.
3. **PR-3 (category E)**: 시계열 파라미터(환율·리스크) 일자별 INSERT.
4. **PR-4 (category C)**: 일자별 스냅샷 전수 (DEP202S·CSC102H·FND·PNB·FIN 외 일자 PK 보유 모든 테이블).
5. **PR-5 (category D)**: 이벤트 전수 (TRX701L·FXD501L·MKT1202M 외 `H`/`L`/`G` 일자 PK 전수).
6. **PR-6 (category B)**: Slow-change UPSERT 전수 (CSC101M·DEP201P·LNB301M·CRD401M 외 STD_DT 비-PK 컬럼 보유 모든 마스터).
7. **PR-7 (integrity & observability)**: 사후 점검 쿼리 + 요약 JSON + 테스트. 카테고리·테이블별 건수 + FK 고아 0건 회귀.

각 PR 마다:
- [ ] 본 문서(§4 표)의 해당 테이블 상태를 체크 표시(⬜→✅).
- [ ] 통합 테스트 추가 및 녹색 확인.
- [ ] 문서(`§10 CLI`, `§11 메트릭`) 예시 로그 갱신.

### 15.2 완료 정의 (DoD)
- [ ] K-01 시나리오(`지점별 여신 잔액`) 가 증분 실행 후 0-row 없이 통과.
- [ ] FK 고아 0건.
- [ ] 동일 seed 2회 실행 시 동일 행 수.
- [ ] `docs/guides/dev-guidelines.md` 에 사용법 1섹션 추가.
- [ ] 요약 JSON 이 CI 에서 파싱 가능.

### 15.3 참고 파일 (구현자 요약 로드용)
- 도메인 풀: [seed_postgres.py:148-260](devtools/scripts/seed_postgres.py#L148-L260)
- 카테고리별 유형 추론: [_infer_table_type](devtools/scripts/seed_postgres.py#L807)
- DDL 빌더: [_build_ddl](devtools/scripts/seed_postgres.py#L817)
- 도메인 값 생성기: [_gen_col_value](devtools/scripts/seed_postgres.py#L2226-L2368)
- 제약 보정: [_fix_row_constraints](devtools/scripts/seed_postgres.py#L2371-L2462)
- 비-★ 자동 시딩: [seed_non_star_tables](devtools/scripts/seed_postgres.py#L2465)
- 카테고리 분류 규칙: [PK_TYPE_MAP](devtools/scripts/seed_postgres.py#L71-L148), [_DOMAIN_GROUP](devtools/scripts/seed_postgres.py#L276)
- 요구사항(원본 테이블 정의): [docs/agent-guides/test-data-requirements.md](docs/agent-guides/test-data-requirements.md)

---

**문서 종료.** 구현 담당자는 §15.1 순서로 진행하되, 각 PR 착수 전에 §14.2 열린 질문을 사용자와 합의하여 결정치를 문서 상단(§1.3 비목표 아래)에 추가 기재한다.
