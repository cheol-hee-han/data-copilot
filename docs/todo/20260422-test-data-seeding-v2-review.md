# bank_v2 시딩 패키지 검토 피드백 반영본 (2026-04-22)

## 목차
- [1. 배경](#1-배경) — 검토 대상과 사용자 피드백 요지
- [2. 재현 매커니즘 정의](#2-재현-매커니즘-정의) — 피드백 ①에 대한 답변
- [3. 메타 퀄리티 재평가](#3-메타-퀄리티-재평가) — 피드백 ②-1~②-6에 대한 답변
  - [3.1 MD 엔티티정의 채움률 실측](#31-md-엔티티정의-채움률-실측) — 영업점 마트 MD 자체가 부실
  - [3.2 영업점 관점 재평가 (②-5 핵심)](#32-영업점-관점-재평가-2-5-핵심) — business_rules 도메인 분포와 공백
  - [3.3 부적합 판정 정정](#33-부적합-판정-정정) — AML/INV/FNS 부족 판단 철회
  - [3.4 TYPE-3 분포 완화](#34-type-3-분포-완화) — GOOD 60/POOR 30/MISSING 10
  - [3.5 쌍둥이 테이블과 duplicate_rows](#35-쌍둥이-테이블과-duplicate_rows) — V2 내재 구조 + RDB 위반 제거
- [4. Mongo 시딩 호환성 (③-1)](#4-mongo-시딩-호환성-3-1) — 스키마 무변경 + 추가 필드 저장
- [5. PG 시딩 논의 지연 (③-2)](#5-pg-시딩-논의-지연-3-2) — 메타 정리 완료 후 재논의
- [6. Step 0~2 로드맵](#6-step-02-로드맵) — 착수 순서 및 Step 0 세부
- [7. 확정 사항 체크리스트](#7-확정-사항-체크리스트) — 합의된 변경점 요약

---

## 1. 배경

- 검토 대상: `tests/test_data_seeding_v2/` 패키지 (1,441T × 12,285C + meta/*.json 5종 + 79 business_rules)
- 1차 감사 결과(domain-expert + general-purpose 병렬): 설계 컨셉·Mongo 호환성은 합격, 단 MD 엔티티정의 채움률과 business_rules 도메인 커버리지에 공백이 큼
- 사용자 피드백 핵심: **서비스 대상은 본부부서가 아니라 영업점 직원**. 마케팅/고객명세/경평실적 등 영업 관련 데이터가 주 유스케이스 → 재평가 관점 교체 필요
- 본 문서는 ①~③-2 피드백에 대한 점별 답변을 기록하고 후속 Step 0~2 로드맵을 확정한다.

---

## 2. 재현 매커니즘 정의

피드백 ①: "재현 매커니즘이 실무환경의 데이터·메타 로스를 반영하는가?"

**답변: 맞다.** V2가 주입하는 불완전성은 실무 정보계 환경에서 관찰되는 3축 로스를 의도적으로 재현한다.

| 축 | V2 매커니즘 | 실무 대응 |
|---|---|---|
| 메타 로스 | 엔티티정의 일부 누락, 컬럼 detail 0.3%만 채움, 인라인 enum 951건 | DA 미작성·부분 작성 상태 |
| 데이터 로스 | `quality_defects.missing_fk` / `code_value_mismatch` / `date_outlier` / `numeric_outlier` | 마스터·코드 테이블 동기화 실패, 범위 밖 값 |
| 혼동 유발 | 쌍둥이 테이블 유형 M/D/L/H/P/S/G/C, 14자 네이밍 규칙 | 같은 도메인에 마스터·스냅샷·이력·집계 공존 |

즉 "덜 채워진 메타"와 "혼동스러운 데이터"를 동시에 재현하여, NL-to-SQL 에이전트가 실제로 겪는 명확화·추론 부담을 벤치마크 가능한 형태로 제공한다.

---

## 3. 메타 퀄리티 재평가

### 3.1 MD 엔티티정의 채움률 실측

피드백 ②-1: "설명 누락 부분은 업무 시나리오 추론으로 채워야 하는데, 문서만 보고 가능한가?"

**답변: 부분적으로만 가능. MD 자체가 부실한 영역이 많다.** 실측 결과:

| MD 파일 | 엔티티정의 채움률 | 유형 |
|---|---|---|
| 20_CSC (고객) | 75% (27/36) | 비교적 충실 |
| C0_MVP (수신실적마트) | **8.6% (3/35)** | 영업점 핵심인데 부실 |
| C7_MRO (부점분석마트) | **20% (3/15)** | 영업점 핵심인데 부실 |
| B0_MKT (마케팅) | **24% (6/25)** | 영업점 핵심인데 부실 |

catalog_v2 전체 기준으로는 entity_def 30.5% (439/1441), scope_in 4.4% (64/1441), col.definition 100%, col.detail 0.3% (38/12285).

즉 "MD → catalog 반영 누락"이 아니라 **MD 원본이 덜 작성된 상태**. 따라서:

1. `build_catalog.py` 재실행으로 MD에 이미 적힌 설명을 catalog에 반영 (현재 반영 안된 경우)
2. **부족분은 LLM 배치 생성 + 도메인 검수** 필요 (C0_MVP/C7_MRO/B0_MKT 우선)
3. 컬럼 detail 0.3%는 현실적으로 전수 채움 불가 → 영업점 유스케이스에서 실제 사용 가능성이 높은 핵심 컬럼만 선별

### 3.2 영업점 관점 재평가 (②-5 핵심)

피드백 ②-5: "서비스 대상은 본부부서가 아니라 영업점 직원. 영업 관련 데이터가 주 유스케이스인 관점에서 재평가하라."

**답변: 테이블 수는 충분, business_rules 커버리지가 진짜 공백이다.**

**테이블 관점 (영업점 주제영역 현황):**

| 주제 | 영역코드 | 용도 | V2 테이블 수 (추정) |
|---|---|---|---|
| 수신실적마트 | MVP | 영업점 일·월 수신 실적 | 30+ |
| 여신실적마트 | MVN | 영업점 여신 실적 | 30+ |
| 카드실적마트 | MVC | 카드 영업 실적 | 20+ |
| 외환실적마트 | MVF | 외환 영업 실적 | 15+ |
| 부점종합마트 | MVB | 부점 단위 종합 | 20+ |
| 고객분석마트 | MRC | 고객 세그먼트·행동 분석 | 30+ |
| 부점분석마트 | MRO | 부점 성과 분석 | 15+ |
| 마케팅 | MKT | 캠페인·타겟팅·반응 | 25+ |
| 합계 | — | — | **240+** |

이 정도면 영업점 유스케이스 벤치마크에 충분.

**business_rules 관점 (진짜 문제):**

79개 rule의 도메인 분포 (실측):

```
LNB 13, CLN 9, DPG 8, LNO 8, CSC 5, RPC 4, FXC 4, SLE 3, EBB 3,
DPF 2, LNH 2, TRS 2, EBS 1, DRV 1, RPD 1, FNA 1, RPI 1,
cross_table 11 (mvp_bal/mvn_bal 일부 포함)
```

**MVP / MVN / MVC / MVB / MRC / MRO / MKT = 모두 0건** (cross_table 일부 제외).

영업점 유스케이스 마트에 정합성 규칙이 비어 있다 → 시딩해도 NL-to-SQL 에이전트가 "이 마트의 합계가 원장과 맞는가"를 검증할 근거가 없다. **영업점 관점 규칙 10~15개 보강이 필수.**

### 3.3 부적합 판정 정정

이전 감사에서 "AML/INV/FNS 규칙 0개"를 문제로 지적했으나 사용자 피드백대로 **철회**. 이 도메인은 본부부서 업무이며 영업점 대상 서비스에서는 유스케이스가 희박하다. 리소스는 MVP/MVN/MVC/MVB/MRC/MRO/MKT 보강에 집중한다.

### 3.4 TYPE-3 분포 완화

피드백 ②-3: "기존 15%/25%/40%/20% 분포는 과했다. 대부분 POOR~GOOD 수준으로 적당히."

**합의안:**

| 품질 | 비율 | 의미 |
|---|---|---|
| GOOD | 60% | 설명 충실, detail 일부 포함 |
| POOR | 30% | 설명 짧음·추상적, detail 없음 |
| MISSING | 10% | 설명 비었거나 placeholder |

EXCELLENT 구간은 제거 — 실무에서도 100% 완벽한 메타는 드물다.

### 3.5 쌍둥이 테이블과 duplicate_rows

피드백 ②-4: "테이블 유형·특성별 유사 데이터 집계 부분이 V2에 있는지? pk_dup_rows는 RDB 설계 오류이므로 제거."

**답변:**

- **있다.** `01_FORMAT_SPEC.md §7.4`에 쌍둥이 테이블 구조가 명시되어 있고, catalog_v2에서 `DPG001M` (마스터) / `DPG003P` (일별스냅샷) / `DPG004P` / `DPG009H` (이력) / `DPG005L` (내역) 같은 세트가 실제로 존재한다. 유형 태그는 접미사 M/D/L/H/P/S/G/C로 구분되어 있다.
- 추가 작업은 **catalog에 `ambiguity_group` 태그만 붙이기** — NL-to-SQL 에이전트가 "수신잔액 집계"를 물었을 때 M/P/S 중 어느 테이블을 골라야 하는지 벤치마크 가능.
- **duplicate_rows 제거 확정.** PK 위반 INSERT는 실패하므로 RDB 설계상 존재할 수 없음. `quality_defects.duplicate_rows` 엔트리 삭제하고 시딩 로직에서도 해당 분기 제거.

---

## 4. Mongo 시딩 호환성 (③-1)

피드백 ③-1: "Mongo 스키마를 그대로 유지하면서 카탈로그 보완으로 진행."

**답변: 가능. 스키마 무변경 확정.**

- `resources/connectors/mongo/init_mongodb.js`의 5 컬렉션(dpasset_table / dpasset_column / standard_code / standard_code_value / biz_term) validator가 모두 `additionalProperties` 허용 상태.
- V2가 추가로 가지는 필드(`fk`, `nullable`, `seq`, `ambiguity_group`, `entity_def`의 4태그 MD 링크 등)는 **스키마 변경 없이 그대로 저장 가능**.
- `entity_def`의 `[엔티티정의]/[대상내]/[대상외]/[특이사항]` 4태그는 `dpasset_table.description`에, `[공통정의]/[상세정의]`는 `dpasset_column.description`에 연결.
- 따라서 시더는 기존 `devtools/scripts/seed_mongodb.py` 구조를 유지하고, 입력 소스를 기존 PG 미러가 아닌 `catalog_v2.json`으로 스위치하는 형태로 변경.

---

## 5. PG 시딩 논의 지연 (③-2)

피드백 ③-2: "메타부터 정리하고 PG는 후논의."

**답변: 동의.** Step 1 Mongo 시딩 검증이 완료된 뒤 별도 논의로 분리. 이유:

- PG DDL 1,441개는 기존 `generate_all_ddl.py`의 스키마 네이밍과 FK NOT VALID 전략에 맞춰 재생성해야 함 → 선행 논의 필요
- `verification_queries.sql`이 V2 가정의 스키마 prefix로 작성되어 있어, 우리 스키마명으로 재생성해야 실행 가능
- Mongo 메타가 먼저 안정화되어야 PG 시딩 시 참조 정합성 검증이 의미를 가짐

---

## 6. Step 0~2 로드맵

### Step 0 — 메타 보완 (선행)

1. `build_catalog.py` 재실행 — MD 최신 상태를 catalog_v2에 반영
2. **LLM 배치 생성 + 도메인 검수**로 MD 엔티티정의 보강
   - 우선순위: C0_MVP (8.6%) → B0_MKT (24%) → C7_MRO (20%)
   - 영업점 유스케이스 관점에서 4태그 작성 후 도메인 전문가 검수
3. 쌍둥이 테이블 세트에 `ambiguity_group` 태그 추가 (catalog_v2)
4. `business_rules.yaml`에 영업점 관점 규칙 **10~15개** 추가
   - 대상: MVP/MVN/MVC/MVB/MRC/MRO/MKT
   - 유형: 마트 합계 ↔ 원장 합계 정합성, 기간별 스냅샷 단조성, 고객·부점 FK 무결성 등
5. `quality_defects.duplicate_rows` 엔트리 제거
6. `verification_queries.sql` 우리 스키마명 기준으로 재생성

### Step 1 — Mongo 시딩 검증

- 보완된 catalog dry-run: 5 컬렉션 전수 로드 후 validator 통과 여부 확인
- 샘플 질의로 "쌍둥이 테이블 명확화" 시나리오 실행 — 에이전트가 `ambiguity_group` 단서를 실제로 활용하는지 확인
- 문제 있으면 Step 0으로 회귀

### Step 2 — PG 시딩 (별도 논의)

- DDL 생성 전략·스키마 네이밍·FK NOT VALID 정책·scale_factor 프로파일 등을 Step 1 완료 후 별도 세션에서 합의

---

## 7. 확정 사항 체크리스트

- [x] 재현 매커니즘은 메타 로스 / 데이터 로스 / 혼동 유발 3축으로 정의
- [x] MD 엔티티정의 부족분은 LLM 배치 생성 + 도메인 검수로 보강
- [x] 영업점 관점 business_rules 10~15개 추가 (MVP/MVN/MVC/MVB/MRC/MRO/MKT)
- [x] AML/INV/FNS 공백은 문제 아님 — 본부 업무, 영업점 대상 서비스 범위 외
- [x] TYPE-3 분포 GOOD 60 / POOR 30 / MISSING 10 완화
- [x] `quality_defects.duplicate_rows` 제거 (RDB 설계 위반)
- [x] 쌍둥이 테이블 `ambiguity_group` 태그 추가
- [x] Mongo 스키마 무변경 — `additionalProperties` 허용 활용하여 V2 필드 추가 저장
- [x] 4태그 MD → Mongo description 매핑: table 4태그 / column 2태그
- [x] `verification_queries.sql` 우리 스키마명으로 재생성
- [x] PG 시딩은 Step 1 완료 후 별도 논의
