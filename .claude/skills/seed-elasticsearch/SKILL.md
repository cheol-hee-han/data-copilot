---
name: seed-elasticsearch
description: |
  ElasticSearch에 테이블/컬럼 메타, 코드 메타, 보고서 SQL, 용어사전 인덱스를 생성·적재합니다.
  TYPE-2(코드값 불일치), TYPE-3(메타 설명 부실) 불완전성을 의도적으로 포함합니다.
  ES 메타 초기 구축, 인덱스 추가, 메타 품질 조정 시 사용하세요.
user-invocable: true
---
# 역할

ElasticSearch 메타데이터 시딩 전문가. 에이전트가 참조하는 테이블/컬럼 메타, 코드 정의, 보고서 SQL, 용어사전을 적재하되 실무 환경의 불완전성을 재현.

# 핵심 원칙

- **요구사항 문서가 단일 진실 소스(Single Source of Truth)**
  - 인덱스별 목표 건수, TYPE-2/3 상세 요건, 품질 분포 비율 등 모든 구체적 수치는 요구사항 문서를 참조
  - SKILL.md에는 절차와 원칙만 기술하고, 구체적 데이터 상세는 하드코딩하지 않음
- 반드시 `docs/agent-guides/test-data-requirements.md`의 **최신** 요구사항을 준수
  - 섹션 7: ES 인덱스 명세 (인덱스별 목표 건수, 불완전성 요건)
  - 섹션 4: TYPE-2/3 상세 요건 (코드값 목록, 품질 분포 비율, POOR/MISSING 케이스)
  - 섹션 5: 전체 테이블 카탈로그 (table_meta/column_meta 생성 원본)
- 반드시 `docs/agent-guides/test-data-seeding-reference.py`의 `seed_elasticsearch()` 구조·매핑·데이터 포맷을 참고
- `.env` 기반 연결 정보 사용 (`ES_URL`, `ES_USER`, `ES_PASSWORD`)
- 재실행 안전성: 인덱스 삭제 후 재생성 방식

# 대상 인덱스

| 인덱스 | 역할 | 핵심 불완전성 |
|--------|------|-------------|
| `table_meta` | 테이블·컬럼 정의 (nested 구조) | TYPE-3: 설명 품질 혼재 |
| `column_meta` | 컬럼별 상세 정의 | TYPE-3: POOR/MISSING 혼재 |
| `code_meta` | 코드 그룹별 값 정의 | TYPE-2: **공식 코드만** 정의 (PG 실데이터와 불일치) |
| `report_sql` | 보고서 SQL 참조용 | 도메인별 분포 |
| `term_dict` | 자연어↔컬럼명 매핑 용어사전 | 혼동 위험 용어 포함 |

> 각 인덱스의 목표 건수와 상세 요건은 요구사항 문서 섹션 7 참조

# 불완전성 재현 (필수)

## TYPE-2: 코드값 불일치 (code_meta)
- `code_meta`에는 **공식 코드만** 정의
- PG 실데이터에 존재하는 미정의 코드는 의도적으로 누락
- **구체적인 코드값 목록은 요구사항 문서 섹션 4 TYPE-2 참조**

## TYPE-3: 메타 설명 부실 (table_meta, column_meta)
- 설명 품질을 BEST/GOOD/POOR/MISSING 4단계로 혼재
- **품질 분포 비율과 필수 POOR/MISSING 케이스는 요구사항 문서 섹션 4 TYPE-3 참조**

# 매핑 구조 참고

## table_meta (nested 컬럼 구조)
```json
{
  "table_name": "keyword",
  "table_nm_ko": "text",
  "table_desc": "text",
  "schema": "keyword",
  "domain_cd": "keyword",
  "std_dt_col": "keyword",
  "is_partitioned": "boolean",
  "columns": {
    "type": "nested",
    "properties": {
      "name": "keyword", "type": "keyword",
      "desc": "text", "pk": "boolean",
      "pii": "boolean", "fk": "keyword",
      "code_ref": "keyword"
    }
  }
}
```

## code_meta
```json
{
  "code_field": "keyword",
  "code_field_desc": "text",
  "table_name": "keyword",
  "codes": { "type": "object", "enabled": false }
}
```

# 작업 절차

> **절대 규칙:** 이미 데이터가 존재하더라도 스킬이 호출되면 반드시 Phase 1(사전 검증)을 수행한다.
> 요구사항과 현재 상태가 하나라도 불일치하면 전체 삭제 후 재생성한다.
> 시딩 완료 후에는 반드시 Phase 3(사후 검증)을 수행하고 결과 테이블을 출력한다.

## Phase 1: 사전 검증 (Drift Detection)

1. `.env` 파일에서 ES 연결 정보 확인
2. `test-data-requirements.md`의 **최신** 요구사항을 읽고 파악:
   - 섹션 7의 인덱스별 목표 문서 수
   - 섹션 4의 TYPE-2 코드값 불일치 요건 (공식 코드만 정의)
   - 섹션 4의 TYPE-3 메타 설명 품질 분포 비율
3. 현재 ES에 적재된 데이터와 요구사항을 **API로 비교**:
   - 각 인덱스 존재 여부 및 문서 수 (`_count`)
   - 매핑 구조 일치 여부
   - TYPE-3 품질 분포 (POOR/MISSING 비율 샘플링)
   - code_meta에 미정의 코드가 잘못 포함되어 있지 않은지
4. **불일치 항목이 하나라도 있으면** → Phase 2로 진행 (전체 재생성)
5. **모든 항목 일치** → Phase 3(사후 검증)만 수행하고 "변경 없음" 보고 후 종료

## Phase 2: 시딩 실행

1. `test-data-seeding-reference.py`의 `seed_elasticsearch()` 구조 참고
2. 기존 인덱스 삭제 후 매핑 재생성
3. 문서 적재 (불완전성 TYPE-2, TYPE-3 비율 준수)
4. 적재 건수 즉시 출력

## Phase 3: 사후 검증 (필수 — 절대 생략 금지)

시딩 완료 후 **반드시** 아래 항목을 검증하고 결과 테이블을 출력한다:

1. **문서 수 검증**: 각 인덱스 `_count`가 요구사항 섹션 7의 목표 이상인지
2. **TYPE-3 검증**: table_meta/column_meta의 설명 품질 분포가 요구사항 섹션 4의 비율과 일치하는지
3. **TYPE-2 검증**: code_meta에 미정의 코드(요구사항 섹션 4 참조)가 **없는지** 확인
4. **매핑 검증**: 각 인덱스의 매핑이 예상 구조와 일치하는지

검증 결과는 아래 형식으로 출력:

```
| 검증 항목 | 기대값 | 실제값 | 판정 |
|----------|--------|--------|------|
| table_meta 문서수 | ≥N (문서 기준) | N | ✅ |
| column_meta 문서수 | ≥N (문서 기준) | N | ✅ |
| TYPE-3 POOR/MISSING 비율 | ~N% (문서 기준) | N% | ✅ |
| code_meta에 미정의 코드 없음 | 0건 | 0건 | ✅ |
| ...      | ...    | ...    | ... |
```

**하나라도 ❌ 판정이면** 원인을 파악하고 해당 부분만 수정 후 Phase 3을 재실행한다.

# 기술 참고사항 (트러블슈팅)

## 실행 환경

- 호스트에서 직접 실행 (postgres 컨테이너 아님)
- PG 스키마 추출은 `docker exec dc-postgres psql -U postgres` 사용
- 실행 명령:
  ```bash
  PYTHONIOENCODING=utf-8 python devtools/scripts/seed_elasticsearch.py
  ```

## 스크립트 아키텍처

- `seed_elasticsearch.py`는 **PG biz_schema의 실제 테이블/컬럼 구조를 런타임에 읽어서** table_meta/column_meta를 자동 생성
- 요구사항 문서도 파싱하여 도메인/한글명/★ 정보를 매핑
- PG에 DDL이 먼저 존재해야 하므로 **seed-postgres 이후에 실행**

## report_sql / term_dict 증강

- `seed_elasticsearch.py`는 핵심 인덱스(table_meta, column_meta, code_meta) + 기본 report_sql(10건)/term_dict(20건) 적재
- 추가 증강은 별도 스크립트로 분리:
  - `devtools/scripts/augment_report_sql.py` — report_sql 140건 추가 (총 150건)
  - `devtools/scripts/augment_term_dict.py` — term_dict 180건 추가 (총 200건)
- 증강 스크립트는 기존 인덱스를 **삭제하지 않고 upsert** 방식으로 추가
- 실행 순서: seed_elasticsearch.py → augment_report_sql.py → augment_term_dict.py

## table_meta의 docs.count 해석

- `_cat/indices`에서 table_meta의 docs.count가 실제 테이블 수보다 큰 것은 **nested 컬럼이 별도 Lucene 문서로 저장**되기 때문
- 실제 테이블 문서 수는 `_count` API로 확인: `curl -u elastic:elastic_pass http://localhost:9200/table_meta/_count`
- 예: docs.count=6605 = table 572건 + nested column 6033건

## TYPE-3 품질 분포

- **해시 기반 결정론적 분포**: `hashlib.md5(table_name)` 기반으로 BEST/GOOD/POOR/MISSING 결정
- 동일 테이블명이면 재실행해도 동일한 품질 등급 배정
- FORCED_POOR_MISSING 딕셔너리로 필수 케이스 강제 지정

## nori 분석기

- ES에 nori(한국어 형태소 분석) 플러그인이 설치되어 있어야 table_desc/col_desc 분석기 동작
- 미설치 시 인덱스 생성 실패 → `docker exec dc-elasticsearch bin/elasticsearch-plugin install analysis-nori` 후 재시작

# 산출물 위치

- 시딩 스크립트: `devtools/scripts/seed_elasticsearch.py`
- 증강 스크립트: `devtools/scripts/augment_report_sql.py`, `devtools/scripts/augment_term_dict.py`

# 인자 사용법

- `$ARGUMENTS` 없이 호출: 전체 인덱스 시딩
- `$ARGUMENTS`에 인덱스명 지정: 해당 인덱스만 시딩 (예: `/seed-elasticsearch code_meta`)
- `$ARGUMENTS`에 `augment` 지정: 기존 인덱스에 문서 추가
