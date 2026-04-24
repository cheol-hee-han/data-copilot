# Bank ADW V2 — 클로드 코드 핸드오프 가이드

> **⚠️ 최우선 원칙: 기존 환경이 우선입니다.**
> 
> 본 패키지는 **MongoDB(메타) + PostgreSQL(데이터) 이원 구조**의 기존 환경에 통합됩니다. 
> 저장소 방식·시딩 아키텍처·스크립트 구조는 **기존 프로젝트 규약을 따릅니다.**
> 
> 본 패키지의 가치는 **메타데이터(5 JSON) + 정합성 규칙(79개)** 이며,
> 문서 내 PostgreSQL DDL 명령·seeding/ 패키지 구조·실행 CLI는 **참고용 예시**입니다.
> 
> 👉 **작업 시작 전 반드시 [INTEGRATION_WITH_EXISTING_ENV.md](INTEGRATION_WITH_EXISTING_ENV.md) 를 먼저 읽으세요.**

---

본 패키지는 **한국 은행 정보계(ADW) 합성 테스트 환경**을 구축하기 위한 메타데이터와 규칙의 완전한 세트다. 이 자료만으로 **1,441 테이블, 약 1,425만 행**의 synthetic 은행 데이터를 시딩할 수 있도록 설계되어 있다.

---

## 📚 문서 내비게이션 (순서대로 읽으세요)

| # | 문서 | 역할 |
|---|---|---|
| 0 | **INTEGRATION_WITH_EXISTING_ENV.md** | ⭐ **최우선** — 기존 환경 통합 원칙 |
| 1 | **README_FOR_CLAUDE_CODE.md** | 본 문서 — 핸드오프 개요 |
| 2 | **FILE_INDEX.md** | 전체 파일 용도별 인덱스 |
| 3 | **HOW_TO_INSTRUCT_CLAUDE_CODE.md** | 클로드 코드 지시 프롬프트 (환경 적응 버전) |
| 4 | **98_DATA_GENERATION_V2.md** | 시딩 **참고 예시** (강제 아님) |
| 5 | **99_VERIFICATION_SQL.md** | 시딩 후 검증 방법 |

---

## 🎯 목표

사용자는 NL-to-SQL 에이전트 시스템(Data Copilot)을 개발 중이며, 본 스키마는 에이전트의 **테스트·평가용 벤치마크 환경**으로 사용된다. 프로덕션 데이터의 **의도적 품질 결함**(결측 FK, 코드 불일치, 중복행 등 0.1~0.5%)을 재현한 것이 V2의 핵심 특성이다.

---

## 📁 파일 구조

```
bank_v2/
├── README_FOR_CLAUDE_CODE.md      ← 본 문서
│
├── 00_README_PLAN.md               주제영역 전체 계획
├── 01_FORMAT_SPEC.md               .md 테이블 정의 포맷 사양
├── 02_MASTER_CATALOG.md            1,441 테이블 마스터 카탈로그
├── 10~C8_*.md                      13개 주제영역 × 75개 파일 (테이블 정의)
│
├── 98_DATA_GENERATION_V2.md        ⭐ 시딩 운영 가이드 (필독)
├── 99_VERIFICATION_SQL.md          ⭐ 시딩 후 검증 가이드
│
├── meta/                           ⭐ 시딩 메타데이터 (단일 진실 소스)
│   ├── catalog_v2.json             1,441T × 12,285C 스키마 + FK  [4.6 MB]
│   ├── fk_graph.json               8-레벨 위상 정렬 DAG          [259 KB]
│   ├── distributions.json          컬럼별 값 분포 스펙           [5.2 MB]
│   ├── cardinalities.json          시딩 볼륨 + 관계 밀도         [305 KB]
│   ├── business_rules.json         정합성 제약 79개              [23 KB]
│   ├── verification_queries.sql    검증 쿼리 301개 (자동생성)    [46 KB]
│   │
│   ├── fk_rules.yaml               FK 추론 규칙 (재생성용)
│   ├── distributions.yaml          분포 추론 규칙
│   ├── cardinalities.yaml          카디널리티 규칙 + 프로파일
│   ├── business_rules.yaml         정합성 규칙 원본
│   │
│   ├── *_stats.md                  각 산출물 통계 리포트
│   ├── unmatched_columns.tsv       FK 규칙 미매칭 컬럼 (분석용)
│   └── dist_unmatched.tsv          분포 미매칭 (금융 특수 지표)
│
└── scripts/                        메타데이터 빌드 스크립트
    ├── build_catalog.py            MD → catalog_v2.json
    ├── build_fk_graph.py           catalog → fk_graph.json
    ├── build_distributions.py      catalog → distributions.json
    ├── build_cardinalities.py      catalog + graph → cardinalities.json
    ├── build_business_rules.py     YAML → business_rules.json
    └── build_verification_sql.py   rules → verification_queries.sql
```

---

## 🚀 클로드 코드가 해야 할 일

### 1단계. 환경 준비

```bash
# Python 3.10+ 권장
pip install faker faker-ko numpy psycopg[binary] pandas tqdm pyyaml

# PostgreSQL 16+ 준비
createdb bank_v2
psql -d bank_v2 -c "CREATE SCHEMA adw_v2;"
```

### 2단계. 시딩 스크립트 작성

**98_DATA_GENERATION_V2.md를 필독**하세요. 섹션 5에 권장 아키텍처가 있습니다:

```
seeding/
├── config.py              # 프로파일 선택, DB 연결
├── loader.py              # meta/*.json 로드
├── generators/
│   ├── base.py            # Generator 추상 클래스
│   ├── audit.py           # ETCL_BASE_YMD, ETCL_JOB_TS
│   ├── fk.py              # FK 참조로 생성
│   ├── amount.py          # lognormal
│   ├── date_range.py      # weighted_recent
│   ├── enum_weighted.py   # 가중치 enum
│   ├── korean.py          # faker-ko 한국인 이름·주소·전화
│   └── structured_id.py   # 계좌번호 20자리 등
├── orchestrator.py        # 레벨별 순차 시딩
├── deferred.py            # 2-pass UPDATE (3쌍)
├── defects.py             # V2 품질결함 주입
├── verify.py              # 시딩 후 검증
└── main.py                # CLI 진입점
```

### 3단계. 시딩 실행

시딩 순서는 `fk_graph.json.levels` 의 0→7 순차:

```
Level 0 (300T, 24만 행)    마스터·코드·고립 테이블
Level 1 (79T, 92만 행)     CMI001M 등 핵심 마스터
Level 2 (217T, 10만 행)    소형 지원 테이블
Level 3 (38T, 21만 행)     직원 이력 등
Level 4 (247T, 272만 행)   ⭐ CSC001M 고객 (10K) 레벨
Level 5 (226T, 356만 행)   ⭐ DPG001M 계좌 (18.5K) 레벨
Level 6 (301T, 242만 행)   이력·거래 로그
Level 7 (33T, 409만 행)    최심 로그
────────────────────────────────────
총 1,441T, 1,425만 행 (prototype 프로파일)
```

**각 테이블 시딩 시:**
1. `cardinalities.json.volumes[tbl].volume` 개수만큼
2. `distributions.json.tables[tbl].columns[].distribution` 스펙대로 값 생성
3. FK 컬럼은 부모 테이블에서 샘플링
4. `business_rules.json.rules` 제약 만족하도록 생성

### 4단계. Deferred FK UPDATE (2-pass)

`fk_graph.json.deferred_edges`의 3쌍:

```sql
UPDATE AML002L a SET SRC_CSK_STR_NO = c.STR_NO
FROM CSK008L c WHERE a.ESCL_CSN = c.CSN AND a.FIU_RPT_YMD = c.ESCL_YMD;

UPDATE AML003L a SET SRC_CSK_CTR_NO = c.CTR_NO
FROM CSK009L c WHERE a.ESCL_CSN = c.CSN AND a.FIU_RPT_YMD = c.ESCL_YMD;

UPDATE PFP001M p SET CURR_VER = (
    SELECT VER FROM PFP014M WHERE PDN = p.PDN ORDER BY EFF_YMD DESC LIMIT 1
);
```

### 5단계. V2 품질결함 주입

`distributions.json.quality_defects` 5개 카테고리. 시딩 완료 후 별도 UPDATE/INSERT:
- missing_fk: 0.5% FK orphan
- code_value_mismatch: 0.3% enum 외 코드
- date_outlier: 0.2% 범위 벗어난 날짜
- numeric_outlier: 0.1% 극단 금액 (음수)
- duplicate_rows: 0.2% CSC006M/DPG022L/EBS001L 중복

### 6단계. 검증

```bash
psql -d bank_v2 -f meta/verification_queries.sql > verification_report.txt
# hard severity 위반 = 0이면 성공
```

---

## ⚠️ 주의사항 (놓치기 쉬운 포인트)

### 1. 프로파일 설정

`cardinalities.yaml`의 `default_profile`:
- `prototype` (기본, 1/1000): 로컬 테스트 약 30분 시딩
- `stress` (1/100): 부하 테스트 약 5~8시간
- `full` (1/1): 140억 행, **비권장**

변경 시 `scripts/build_cardinalities.py` 재실행해서 cardinalities.json 갱신.

### 2. 대용량 테이블 상한

`cardinalities.json.profile_config.big_table_caps`의 10개 테이블은 시딩 시 **반드시 샘플링** 필요. 예:
- SLE001L 카드매출: 12억/년 → 50,000건만 (시간축·가맹점 샘플링)
- DPF003P 일별잔액: 50억+ → 30,000건만 (1,000 계좌 × 30일)

### 3. FK 제약 처리

- 시딩 중에는 **FK 제약 비활성**: `SET session_replication_role = 'replica';`
- 시딩 완료 후 **ALTER TABLE ADD CONSTRAINT** 추가
- V2 품질결함 (missing_fk 0.5%) 때문에 모든 FK가 유효하지는 않음 → 제약 추가 시 NOT VALID 옵션 사용

### 4. 한국어 데이터

faker-ko 0.2+ 권장:
```python
from faker import Faker
fake = Faker('ko_KR')
fake.name()         # '김철수'
fake.address()      # '서울특별시 ...'
fake.phone_number() # '010-1234-5678'
```

### 5. 구조화된 ID 생성

일부 컬럼은 패턴 있는 ID 필요 (단순 랜덤 X):
- ACN (20자리): `PPP` 상품그룹 3 + 난수 13 + 체크섬 4
- LON_NO: `LN` + YYYYMM + 순차10
- CARD_NO_ALTR: 마스킹된 16자리
- BIZ_NO: `XXX-XX-XXXXX`

`distributions.json`의 각 컬럼 `generator: structured_id`와 `pattern` 필드 참조.

### 6. 가상 고객 영역

CSN 1~1,000,000은 가상 고객 (테스트용). 1,000,001 이상이 정상 고객 CSN.
`distributions.json.global_name_rules` CSN 규칙 참조.

### 7. 시딩 기준일

`cardinalities.yaml.profiles.prototype.base_date = '20261221'`
- 모든 ETCL_BASE_YMD 이 값
- 날짜 컬럼의 상한 (이 날짜 이후 데이터 없어야 함)
- business_rules의 `ETCL_BASE_YMD` 참조 검증에서 사용

---

## 🔧 메타데이터 재생성 (규칙 수정 시)

규칙을 수정하면 아래 순서로 재생성:

```bash
# 1. FK 규칙 수정 → catalog + graph 재생성
python3 scripts/build_catalog.py
python3 scripts/build_fk_graph.py

# 2. 분포 규칙 수정 → distributions 재생성
python3 scripts/build_distributions.py

# 3. 카디널리티 수정 → volumes 재계산
python3 scripts/build_cardinalities.py

# 4. 비즈니스 룰 수정 → 검증 SQL 재생성
python3 scripts/build_business_rules.py
python3 scripts/build_verification_sql.py
```

---

## 📞 다음 단계에서 검토할 사안 (선택)

다음 항목은 현재 미포함이지만, 필요 시 추가 보강 가능:

1. **codes_v2.json**: 코드값 분포 비중 정밀화
   - 현재 `distributions.json`의 enum은 **uniform 분포 기본**
   - 실제 분포(ex: 성별 M:F=51:49, 대출유형 주담대 30%) 수동 정의 필요 시
   - 시딩 품질에 영향은 있으나 블로커는 아님

2. **twins.json**: 쌍둥이 관계 GT
   - STR↔AML, CTR↔AML, FXD↔DRV, DPN↔TRS 등
   - 에이전트 평가 GT용 (시딩과 무관)

3. **미매칭 컬럼 255건**: 금융 특수 지표 (ROE, CIR, PD, AUC, NAV 등)
   - 현재 타입 기본값으로 fallback 처리됨
   - 특수 규칙 필요 시 `distributions.yaml` 확장

---

## 📊 메타데이터 품질 수치

| 품질 지표 | 값 | 평가 |
|---|---|---|
| 테이블 수 | 1,441 | 목표 1,435 대비 +6 |
| 컬럼 수 | 12,285 | 파싱 99.6% |
| FK 커버리지 | 11.63% | 주요 마스터 관계 대부분 |
| FK DAG 완전성 | 100% (순환 0) | 8 레벨로 정렬 |
| 분포 스펙 매칭률 | 97.92% | 255건만 fallback |
| 정합성 규칙 | 79개 | 13개 도메인 커버 |
| 검증 SQL 자동생성 | 301 쿼리 | 규칙 재생성 가능 |

---

## ✅ 최종 체크리스트 (클로드 코드 수행용)

```
[ ] 1. 본 패키지 수령 후 전체 파일 구조 확인
[ ] 2. 98_DATA_GENERATION_V2.md 정독
[ ] 3. meta/*.json 5개 로드 확인 (catalog, fk_graph, distributions, cardinalities, business_rules)
[ ] 4. PostgreSQL 16+ 환경 준비
[ ] 5. seeding/ 패키지 아키텍처 생성
[ ] 6. Generator 구현 (audit, fk, amount, date, enum, korean, structured_id)
[ ] 7. Orchestrator 구현 (레벨별 시딩 + bulk insert + 진행 표시)
[ ] 8. DDL 생성 스크립트 (FK 없이 CREATE TABLE)
[ ] 9. Level 0 시딩 → verify 쿼리로 확인
[ ] 10. Level 1~7 순차 시딩
[ ] 11. Deferred FK UPDATE (2-pass)
[ ] 12. V2 품질결함 주입
[ ] 13. FK 제약 추가 (ALTER TABLE ... NOT VALID)
[ ] 14. verification_queries.sql 실행 → 결과 분석
[ ] 15. hard severity 위반 = 0 확인
[ ] 16. 최종 통계 리포트 작성
```

---

**End of Handoff Document**
