# 98. Data Generation V2 — 시딩 참고 예시

> **⚠️ 참고용 예시 문서입니다.**
> 
> 본 문서의 PostgreSQL DDL 명령, `seeding/` 패키지 구조, `psycopg[binary]` 등 구체적 스택 선택은 **강제 지침이 아닌 참고 예시**입니다. 실제 구현은 **기존 프로젝트 환경 규약**을 따르세요.
> 
> 반드시 [INTEGRATION_WITH_EXISTING_ENV.md](INTEGRATION_WITH_EXISTING_ENV.md)를 먼저 확인.

본 문서는 **시딩 수행 시 참고할 수 있는 예시 흐름**을 제시한다. `meta/*.json` 5개를 활용해 1,441 테이블, 약 1,425만 행의 synthetic 데이터를 생성하는 일반적 절차를 다룬다.

---

## 1. 메타데이터 입력 파일 (단일 진실 소스)

| 파일 | 용도 |
|---|---|
| `meta/catalog_v2.json` | 1,441T × 12,285C 스키마 정의 + FK 관계 (4.6M) |
| `meta/fk_graph.json` | 레벨별 테이블 생성 순서 DAG + 2-pass 대상 (259K) |
| `meta/distributions.json` | 각 컬럼의 값 생성 분포 스펙 (5.2M) |
| `meta/cardinalities.json` | 테이블별 시딩 볼륨 + 관계 밀도 (305K) |
| `meta/business_rules.json` | 정합성 제약 79개 (hard 72, soft 7) |
| `meta/fk_rules.yaml` | FK 추론 규칙 (참고) |
| `meta/distributions.yaml` | 분포 추론 규칙 (참고) |
| `meta/cardinalities.yaml` | 시딩 스케일 프로파일 (참고) |

**클로드 코드는 이 5개 JSON을 읽어 시딩 스크립트를 작성한다.** yaml은 규칙 설계 근거 문서로만 사용.

---

## 2. 시딩 프로파일

`cardinalities.json`에 3개 프로파일 정의:

| profile | scale | 총 레코드 | 용도 |
|---|---|---|---|
| **prototype** (기본) | 0.001 | **약 1,425만 행** | 로컬 테스트·데모 |
| stress | 0.01 | 약 1.4억 행 | 부하 테스트 |
| full | 1.0 | 약 140억+ 행 | 이론치 (비권장) |

변경 방법: `cardinalities.yaml` 의 `default_profile` 필드 수정 후 `build_cardinalities.py` 재실행.

---

## 3. 시딩 전체 순서

```
Step A. 환경 준비 + DDL 생성     (catalog_v2.json 활용)
Step B. Level 0 시딩             (fk_graph.json 활용, 300 테이블)
Step C. Level 1~7 순차 시딩      (cardinalities.json 볼륨 기준)
Step D. Deferred FK UPDATE       (2-pass, 3쌍)
Step E. 품질결함 주입            (distributions.json quality_defects)
Step F. 파생 관계 계산           (business_rules.json computed)
Step G. 정합성 검증              (99_VERIFICATION_SQL.md)
```

### Step A — 환경 준비

```sql
-- PostgreSQL 16+, 빈 스키마
CREATE SCHEMA adw_v2;
SET search_path TO adw_v2;

-- 권장 설정 (대용량 bulk insert용)
ALTER DATABASE bank_v2 SET statement_timeout = '30min';
ALTER DATABASE bank_v2 SET work_mem = '256MB';
```

**DDL 생성 방식**:
- `catalog_v2.json`의 `tables[].columns`를 순회하며 CREATE TABLE 생성
- FK는 **별도 ALTER TABLE ADD CONSTRAINT**로 추가 (시딩 후)
  - 시딩 중엔 FK 제약 비활성 권장 (성능·의존성)
- PK는 CREATE TABLE에 포함

### Step B — Level 0 시딩 (300 테이블)

Level 0 테이블은 **FK 의존성 없음** → 병렬 가능.

주요 대상:
- 코드 마스터 (CMI025M, CMI026C)
- 영업일/공휴일 (CMI021C~024C)
- 부점 기본 (CMI001M): 100개 시딩
- 직원 기본 (CMI007M): 2,500명
- 상품 (PFP001M): 1,000개
- 고립 테이블 274개 (참조 없는 독립 테이블)

### Step C — Level 1~7 순차 시딩

각 레벨의 테이블은 이전 레벨의 레코드를 참조한다. `fk_graph.json.levels` 순서대로 진행.

**주요 흐름**:
- Level 1: CMI001M 부점 (실제 시딩)
- Level 2~3: 조직/직원 계층, 소형 지원 테이블
- **Level 4**: CSC001M 고객 (10,000명) — 중추 마스터
- **Level 5**: DPG001M 계좌 (18,500개), LNB001M 대출 (4,800건), CLN001M 카드회원 (4,200명)
- Level 6: 이력·거래 로그
- Level 7: 최심 로그 (일부 DPB/DPD 거래)

### Step D — Deferred FK UPDATE (2-pass)

`fk_graph.json.deferred_edges` 3쌍:

```sql
-- AML002L.SRC_CSK_STR_NO ← CSK008L 매핑
UPDATE AML002L a
SET SRC_CSK_STR_NO = c.STR_NO
FROM CSK008L c
WHERE a.ESCL_CSN = c.CSN AND a.FIU_RPT_YMD = c.ESCL_YMD;

-- AML003L.SRC_CSK_CTR_NO ← CSK009L 매핑
UPDATE AML003L a
SET SRC_CSK_CTR_NO = c.CTR_NO
FROM CSK009L c
WHERE a.ESCL_CSN = c.CSN AND a.FIU_RPT_YMD = c.ESCL_YMD;

-- PFP001M.CURR_VER ← PFP014M 최신 버전
UPDATE PFP001M p
SET CURR_VER = (
    SELECT VER FROM PFP014M WHERE PDN = p.PDN ORDER BY EFF_YMD DESC LIMIT 1
);
```

### Step E — 품질결함 주입 (V2 특성)

`distributions.json.quality_defects`의 5개 카테고리:

| 결함 유형 | 비율 | 적용 |
|---|---|---|
| missing_fk_sample | 0.5% | 일부 FK 참조에 존재하지 않는 값 |
| code_value_mismatch | 0.3% | enum 외 코드값 주입 |
| date_outlier | 0.2% | 날짜 범위 벗어난 값 |
| numeric_outlier | 0.1% | 극단값 금액 (음수/최대) |
| duplicate_rows | 0.2% | 지정 테이블에 의도적 중복 행 |

**주입 방식**: 시딩 완료 후 별도 UPDATE/INSERT로 무작위 행에 결함 주입.

### Step F — 파생 관계 계산

`business_rules.json` 중 `category: computed` (4개):
- MVN009S.NPL_BAL ← RSK022M 집계
- FNA029M.CIR ← 비용/수익
- RPI003M.FUND_RTO ← 자산/PBO
- EBB011M 임시한도 범위 검증

**방식**: 원장 시딩 완료 후 마트 테이블은 집계 SQL로 적재.

---

## 4. 대용량 테이블 처리 전략

`cardinalities.json.big_table_caps` (prototype 프로파일):

| 테이블 | 상한 | 원본 규모 | 처리 |
|---|---|---|---|
| `SLE001L` 카드매출 | 50,000 | 12억/년 | 부점×월별 샘플링 |
| `DPF003P` 일별잔액 | 30,000 | 50억+ | 30일 × 1,000 계좌만 |
| `EBS001L` 이체 | 100,000 | 2억/년 | 최근 6개월만 |
| `FXC001L` 환전 | 20,000 | 1억+ | 샘플링 |
| `FXD001L` FX 딜 | 10,000 | 500만 | 샘플링 |
| `SLE002L` 매출 상세 | 30,000 | 12억+ | SLE001L과 1:1 매칭 |
| `DPG010L` 계좌거래 | 50,000 | 50억+ | 샘플링 |
| `CLN004L` 카드 이용내역 | 100,000 | 10억+ | 샘플링 |
| `MVC001S` 카드 일별 | 5,000 | 부점×일 전체 | 100부점×50일 |
| `MVP001S` 수신 일별 | 5,000 | 동상 | 동상 |

**샘플링 원칙**:
1. 시간 축: 최근 기간 우선 가중 (2024-01 ~ 2026-12)
2. 주체 축: 상위 10% 핵심 엔티티 집중
3. 분포 유지: 전체 분포 특성은 반영 (편향 최소화)

---

## 5. 시딩 스크립트 아키텍처 권장

### 5.1. Python 기반

```python
# 권장 라이브러리
faker==24.0.0          # 가짜 데이터 (한국어 로케일 지원)
faker-korean==0.2.0    # 한국인 이름, 주소, 전화번호
numpy==1.26            # 분포 샘플링
psycopg[binary]==3.1   # PostgreSQL 연결
pandas==2.2            # 데이터프레임·bulk insert
tqdm==4.66             # 진행 표시
pyyaml==6.0
```

### 5.2. 스크립트 모듈 구조

```
seeding/
├── __init__.py
├── config.py              # 연결 정보, profile 선택
├── loader.py              # meta/*.json 로드
├── generators/
│   ├── __init__.py
│   ├── base.py            # Generator 추상 클래스
│   ├── audit.py           # ETCL_* 컬럼
│   ├── fk.py              # FK 참조 생성 (부모 sample)
│   ├── amount.py          # lognormal
│   ├── date_range.py      # weighted_recent 등
│   ├── enum_weighted.py
│   ├── korean.py          # 이름/주소/전화
│   └── structured_id.py
├── orchestrator.py        # 레벨별 순차 시딩
├── deferred.py            # 2-pass UPDATE
├── defects.py             # V2 품질결함 주입
├── verify.py              # 시딩 후 검증
└── main.py                # CLI 진입점
```

### 5.3. 시딩 흐름 (의사코드)

```python
def seed_all(profile='prototype'):
    catalog = load('catalog_v2.json')
    graph = load('fk_graph.json')
    distributions = load('distributions.json')
    cardinalities = load('cardinalities.json')
    rules = load('business_rules.json')
    
    # Step A: DDL (FK 없이)
    create_tables_no_fk(catalog)
    
    # Step B~C: 레벨별 시딩
    for level in graph['levels']:
        for table in level['tables']:
            volume = get_volume(cardinalities, table)
            col_specs = get_distributions(distributions, table)
            generate_and_insert(
                table, volume, col_specs,
                rules=filter_rules(rules, table),
                bulk_size=10000
            )
    
    # Step D: Deferred FK
    run_deferred_updates(graph['deferred_edges'])
    
    # Step E: 품질결함 주입
    inject_quality_defects(distributions['quality_defects'])
    
    # Step F: 파생 관계 (컴퓨트 룰)
    apply_computed_rules(rules)
    
    # 마지막: FK 제약 추가
    add_fk_constraints(catalog)
    
    # Step G: 검증
    run_verification()
```

---

## 6. 시드(Seed) 및 재현성

- `cardinalities.json.profile_config.random_seed = 42` (기본)
- 모든 generator는 이 seed를 받아 동일 입력 시 동일 출력
- 부모-자식 FK 매칭은 `parent_id % child_count` 해시 기반 결정론적 매칭

---

## 7. 성능 팁

### 7.1 bulk insert
```python
# psycopg3 COPY 사용 (INSERT 대비 10~50배)
with conn.cursor() as cur:
    with cur.copy("COPY DPG001M (...) FROM STDIN") as copy:
        for row in rows:
            copy.write_row(row)
```

### 7.2 FK 제약 disable
- 시딩 중 `SET session_replication_role = 'replica'` (FK 검증 skip)
- 시딩 완료 후 `RESET` + `VALIDATE CONSTRAINT`

### 7.3 인덱스 지연 생성
- PK는 CREATE TABLE에 포함 (필수)
- 보조 인덱스는 시딩 완료 후 일괄 생성

### 7.4 병렬 처리
- 같은 Level 내 테이블은 부모 의존성 없으므로 병렬 가능
- 권장 워커 수: CPU 코어 × 0.7 (PostgreSQL 연결 pool 고려)

---

## 8. 예상 소요 시간 (prototype 프로파일)

하드웨어 기준: Dell PowerEdge R760, CPU 64코어, RAM 256GB, NVMe SSD

| Step | 예상 시간 |
|---|---|
| A. DDL 생성 | 1분 |
| B. Level 0 시딩 (300T × 평균 800행) | 3분 |
| C. Level 1~7 순차 시딩 | 15분 |
| D. Deferred UPDATE | 2분 |
| E. 품질결함 주입 | 1분 |
| F. 컴퓨트 룰 | 3분 |
| G. 검증 | 2분 |
| **총계** | **약 30분** |

**stress 프로파일은 약 5~8시간 예상.**

---

## 9. 시딩 실행 순서 요약 (체크리스트)

```
[ ] 1. PostgreSQL 16+ 설치, adw_v2 스키마 생성
[ ] 2. 메타 JSON 5개 위치 확인 (meta/ 디렉토리)
[ ] 3. cardinalities.yaml 프로파일 확인 (기본: prototype)
[ ] 4. build_cardinalities.py 실행 (볼륨 재계산 필요 시)
[ ] 5. seeding/main.py 실행
[ ] 6. 로그에서 level별 완료 확인
[ ] 7. 99_VERIFICATION_SQL.md 쿼리 전수 실행
[ ] 8. 검증 리포트 확인 (severity=hard 위반 0건 확인)
```

---

## 10. 참고 사항

- **가상 고객 영역**: CSN 1~1,000,000은 가상 고객 (테스트 목적), 1,000,001 이상이 정상 고객
- **기준일**: 시딩 배치일은 `cardinalities.yaml.profiles.prototype.base_date` 값 (기본 `20261221`)
- **통화**: 기본 KRW. 외화는 `_CCY` 컬럼으로 구분 (USD/EUR/JPY/CNY 등)
- **품질 결함**: V2는 의도적으로 프로덕션 품질 이슈를 재현 (0.5~2% 수준)
- **쌍둥이 관계**: deferred_edges 외에도 `fk_graph.json.self_references` (3건) 처리 필요

---

**다음 문서**: [99_VERIFICATION_SQL.md](99_VERIFICATION_SQL.md) — 시딩 후 정합성 검증 쿼리 모음
