# 📂 FILE INDEX — 모든 파일 용도별 정리

본 문서는 `bank_v2_complete.zip` 내 **전체 파일을 용도별로 분류**한 완전한 인덱스다. 
클로드 코드가 어떤 파일을 언제, 어떻게 사용해야 하는지 명확히 기술.

---

## 🎯 빠른 참조 (Quick Reference)

**시딩을 수행하려면 이 순서로 읽고 사용하세요:**

| 순서 | 파일 | 목적 |
|---|---|---|
| **0** | **`INTEGRATION_WITH_EXISTING_ENV.md`** ⭐ | **기존 환경 우선 원칙 (최우선)** |
| 1 | `README_FOR_CLAUDE_CODE.md` | 핸드오프 개요 |
| 2 | `FILE_INDEX.md` | 본 문서 — 전체 파일 맵 |
| 3 | `HOW_TO_INSTRUCT_CLAUDE_CODE.md` | 환경 적응 버전 지시 프롬프트 |
| 4 | `98_DATA_GENERATION_V2.md` | 시딩 **참고 예시** (강제 아님) |
| 5 | `meta/*.json` 5개 | 실제 시딩에 사용할 메타데이터 |
| 6 | `99_VERIFICATION_SQL.md` | 시딩 후 검증 |

---

## 📁 Layer 0: 통합 원칙 문서 ⭐ (1개)

**역할**: 본 패키지가 기존 환경과 어떻게 통합되어야 하는지 정의. **모든 작업의 전제**.

| 파일 | 역할 |
|---|---|
| `INTEGRATION_WITH_EXISTING_ENV.md` | ⭐ **최우선** — 기존 환경(MongoDB 메타 + PG 데이터) 우선 원칙, 본 패키지의 "강제 vs 참고" 구분 |

---

## 📁 Layer 1: 최상위 가이드 문서 (5개)

**역할**: 사용자 또는 클로드 코드가 먼저 읽는 문서. 패키지 전체 이해 + 작업 흐름 안내.

| 파일 | 크기 | 역할 | 언제 사용 |
|---|---|---|---|
| `README_FOR_CLAUDE_CODE.md` | 11K | ⭐ **핸드오프 가이드** (필수 1순위) | 패키지 수령 직후 |
| `FILE_INDEX.md` | - | **본 문서** (전체 파일 맵) | 필요 파일 찾을 때 |
| `HOW_TO_INSTRUCT_CLAUDE_CODE.md` | - | **클로드 코드 지시 프롬프트집** | 사용자가 프롬프트 짤 때 |
| `98_DATA_GENERATION_V2.md` | 11K | 시딩 운영 가이드 (DDL→시딩→UPDATE→결함주입) | 시딩 스크립트 작성 전 |
| `99_VERIFICATION_SQL.md` | 4.5K | 시딩 후 정합성 검증 가이드 | 시딩 완료 후 |
| `00_README_PLAN.md` | 7K | 17개 주제영역 설계 계획 | 전체 구조 이해용 |

---

## 📁 Layer 2: 시딩 메타 JSON — 🔑 **단일 진실 소스 (Single Source of Truth)** (5개)

**역할**: 클로드 코드가 **실제로 읽고 파싱하여 시딩 스크립트를 생성**하는 핵심 데이터.

⚠️ **중요: MD 파일들은 사람이 읽는 설계 문서이고, 실제 시딩은 이 5개 JSON만으로 가능합니다.**

| 파일 | 크기 | 내용 | 사용 단계 |
|---|---|---|---|
| `meta/catalog_v2.json` | 4.6M | **1,441 테이블 × 12,285 컬럼 스키마 + FK** | DDL 생성 (Step A) |
| `meta/fk_graph.json` | 259K | **8-레벨 시딩 DAG + Deferred FK 3쌍** | 시딩 순서 결정 (Step B~D) |
| `meta/distributions.json` | 5.2M | **컬럼별 값 생성 규칙** (97.92% 커버) | 값 생성 (Step B~C) |
| `meta/cardinalities.json` | 305K | **테이블별 시딩 볼륨** (prototype: 1,425만 행) | 몇 행 생성할지 결정 |
| `meta/business_rules.json` | 23K | **정합성 제약 79개** (hard 72, soft 7) | 시딩 중 + 검증 |

### 📌 각 JSON의 상세 구조

**catalog_v2.json**
```json
{
  "tables": [{
    "table_id": "TB_ADW_CSC001M",
    "table_kor": "CSC_고객기본",
    "domain": "CSC",
    "columns": [
      {"name": "CSN", "type": "NUMERIC", "length": "10", 
       "pk": true, "nullable": false,
       "fk": {"table": "CSC001M", "column": "CSN", "source": "tier1"}}
    ]
  }]
}
```

**fk_graph.json**
```json
{
  "levels": [
    {"level": 0, "count": 300, "tables": ["AML005M", "CMI004M", ...]},
    {"level": 1, "count": 79, "tables": ["CMI001M", ...]}
  ],
  "deferred_edges": [
    {"from": "CSK008L", "to": "AML002L", "via_column": "AML_STR_NO"}
  ]
}
```

**distributions.json**
```json
{
  "tables": [{
    "table_id": "TB_ADW_CSC001M",
    "columns": [{
      "name": "BRTH_YMD",
      "distribution": {
        "generator": "date_range",
        "range": ["19400101", "20101231"],
        "distribution": "weighted_normal",
        "peak_date": "19800101",
        "source": "override"
      }
    }]
  }]
}
```

**cardinalities.json**
```json
{
  "profile": "prototype",
  "scale_factor": 0.001,
  "volumes": [
    {"table": "CSC001M", "volume": 10000, "level": 4, "source": "base"}
  ]
}
```

**business_rules.json**
```json
{
  "rules": [{
    "rule_id": "lnb001m_date_03",
    "category": "date_order",
    "table": "LNB001M",
    "expr": "EXEC_YMD < MAT_YMD",
    "severity": "hard"
  }]
}
```

---

## 📁 Layer 3: 검증 SQL (자동 생성) (2개)

**역할**: 시딩 완료 후 정합성 검증.

| 파일 | 크기 | 용도 |
|---|---|---|
| `meta/verification_queries.sql` | 46K | **301개 검증 쿼리** (business_rules.json에서 자동 생성) |
| `meta/verification_index.md` | 2K | 쿼리 섹션 인덱스 |

---

## 📁 Layer 4: 규칙 YAML — 재생성 트리거 (4개)

**역할**: 사람이 편집 → 빌드 스크립트 실행 → JSON 재생성. 평소엔 사용 안 함.

| 파일 | 편집 시 재실행할 스크립트 |
|---|---|
| `meta/fk_rules.yaml` | `build_catalog.py` + `build_fk_graph.py` |
| `meta/distributions.yaml` | `build_distributions.py` |
| `meta/cardinalities.yaml` | `build_cardinalities.py` |
| `meta/business_rules.yaml` | `build_business_rules.py` + `build_verification_sql.py` |

---

## 📁 Layer 5: 통계 리포트 (8개)

**역할**: 메타 생성 품질 확인용. 시딩에는 직접 사용 안 하지만 품질 검수에 유용.

| 파일 | 내용 |
|---|---|
| `meta/fk_stats.md` | FK 추론 결과 통계 |
| `meta/fk_graph_stats.md` | DAG 통계 (레벨별·피참조) |
| `meta/distributions_stats.md` | 분포 매칭 통계 (97.92%) |
| `meta/cardinalities_stats.md` | 시딩 볼륨 시뮬레이션 |
| `meta/business_rules_stats.md` | 규칙 카테고리·도메인 집계 |
| `meta/unmatched_columns.tsv` | FK 미매칭 컬럼 분석 |
| `meta/dist_unmatched.tsv` | 분포 미매칭 (255건 금융 지표) |

---

## 📁 Layer 6: 빌드 스크립트 — 메타 재생성 도구 (7개)

**역할**: 규칙 YAML 변경 시 JSON 재생성.

| 스크립트 | 입력 | 출력 |
|---|---|---|
| `scripts/build_catalog.py` | 75개 MD + `fk_rules.yaml` | `catalog_v2.json` |
| `scripts/build_fk_graph.py` | `catalog_v2.json` | `fk_graph.json` |
| `scripts/build_distributions.py` | `catalog_v2.json` + `distributions.yaml` | `distributions.json` |
| `scripts/build_cardinalities.py` | `catalog_v2.json` + `fk_graph.json` + `cardinalities.yaml` | `cardinalities.json` |
| `scripts/build_business_rules.py` | `business_rules.yaml` + `catalog_v2.json` | `business_rules.json` |
| `scripts/build_verification_sql.py` | `business_rules.json` + `catalog_v2.json` | `verification_queries.sql` |
| `scripts/build_master_catalog.py` | `catalog_v2.json` | `02_MASTER_CATALOG.md` |

### 재생성 순서 (모두 재생성할 경우)

```bash
# 의존 관계를 따른 순서
python3 scripts/build_catalog.py          # 1. MD → catalog
python3 scripts/build_fk_graph.py         # 2. catalog → graph
python3 scripts/build_distributions.py    # 3. catalog → distributions  
python3 scripts/build_cardinalities.py    # 4. catalog+graph → cardinalities
python3 scripts/build_business_rules.py   # 5. yaml → business_rules
python3 scripts/build_verification_sql.py # 6. business_rules → SQL
python3 scripts/build_master_catalog.py   # 7. catalog → MD (문서화)
```

---

## 📁 Layer 7: 주제영역별 테이블 정의 MD (75개) — **원본 설계 문서**

**역할**: 사람이 테이블 스키마를 설계·이해하는 원본 문서.
**⚠️ 클로드 코드는 이 75개 MD를 직접 읽을 필요 없음 — 이미 catalog_v2.json에 파싱되어 있음.**

### 도메인별 배치

| 파일 prefix | 도메인 | 개수 | 주요 내용 |
|---|---|---|---|
| `10_CMI` `11_CMO_CMS` | 공통·조직 | 2개 | 부점, 직원, 코드마스터 |
| `20_CSC` `21_CSI` `22_CSK` | 고객·CIF | 3개 | CIF, 신용정보, KYC/AML 원천 |
| `30_PFP` `31_PFR_PFC` | 상품·금리 | 2개 | 상품마스터, 약관, 금리 |
| `40a~45` | 수신 | 6개 | 계좌/정기/적금/요구불/신탁/외화 |
| `50a~55` | 여신 | 8개 | 대출/주담대/전세/신용/기업/정책 |
| `56a~56b` | 연체 | 2개 | 연체관리, 연체분석 |
| `60a~61` | 담보·보증 | 3개 | 담보, 담보평가, 보증 |
| `70a~71c` | 카드 | 5개 | 카드회원, 이용, 매출, 매출집계, 가맹점 |
| `80a~82` | 외환 | 4개 | 외환거래, 무역외환, 외화대출, FX딜링 |
| `85~89` | 전자금융 | 5개 | 인뱅, 모뱅, API, 오뱅, 이체결제 |
| `90~96` | 자산운용 | 7개 | 퇴직연금, 신탁, 펀드, 투자, 파생 |
| `97a~99` | 재무 | 5개 | 회계, 예산, 세무 |
| `A0~A2` | 리스크·규제 | 3개 | 리스크, 규제보고, AML |
| `B0~B2` | CRM | 3개 | 마케팅, 고객관계, NBA |
| `C0~C8` | 마트 | 9개 | 수신/여신/카드/외환/부점/고객/상품 분석 |

### 포맷 예시

각 파일은 이 구조로 테이블을 정의:
```
## TB_ADW_CSC001M

| 속성 | 값 |
|---|---|
| 테이블한글명 | CSC_고객기본 |
| 주제영역 | 고객·CIF·신용평가 |
| 도메인 | CSC |
...

[테이블 설명]
```
[엔티티정의] ...
[대상내] ...
[대상외] ...
[특이사항] ...
```

[컬럼 정의]
| # | 컬럼명 | 한글명 | 타입 | 길이 | Null | PK | [공통정의] | [상세정의] |
```

---

## 🎯 클로드 코드가 실제로 사용하는 파일만 정리

시딩을 수행할 때 **반드시 로드·파싱**해야 하는 파일:

```python
# 필수 5개 JSON
import json
catalog        = json.load(open('meta/catalog_v2.json'))
fk_graph       = json.load(open('meta/fk_graph.json'))
distributions  = json.load(open('meta/distributions.json'))
cardinalities  = json.load(open('meta/cardinalities.json'))
business_rules = json.load(open('meta/business_rules.json'))

# 시딩 완료 후 실행
# psql -f meta/verification_queries.sql
```

MD 파일은 **참조용**으로만 사용. 75개 테이블 정의 MD는 이미 catalog_v2.json으로 변환되어 있으므로 **직접 읽을 필요 없음**.

---

## 📊 메타데이터 품질 지표

| 지표 | 값 |
|---|---|
| 테이블 수 | 1,441 (목표 1,435 대비 +6) |
| 컬럼 수 | 12,285 (파싱 99.6%) |
| FK 엣지 | 1,386 유니크 + 3 Deferred |
| 의존성 레벨 | 8 (순환 0) |
| 분포 스펙 매칭률 | 97.92% |
| 정합성 규칙 | 79개 (13 도메인) |
| 검증 쿼리 | 301개 (자동생성) |
| 시딩 볼륨 (prototype) | 14,257,023 행 |

---

## 🔗 문서간 관계도

```
사용자
  ↓
README_FOR_CLAUDE_CODE.md  ← 시작점
  ├─→ FILE_INDEX.md           (파일 맵)
  ├─→ HOW_TO_INSTRUCT_CLAUDE_CODE.md  (지시 프롬프트)
  ├─→ 98_DATA_GENERATION_V2.md  (운영 가이드)
  │     ↓
  │   meta/*.json 5개  ← 실제 시딩 데이터
  │     ↓
  │   [시딩 실행]
  │     ↓
  └─→ 99_VERIFICATION_SQL.md
        ↓
      verification_queries.sql  ← 검증 실행
```

---

**End of FILE_INDEX**
