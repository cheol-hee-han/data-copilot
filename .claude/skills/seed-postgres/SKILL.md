---
name: seed-postgres
description: |
  PostgreSQL(정보계 + 이력 DB)에 테스트 데이터를 생성·적재합니다.
  TYPE-1~4 불완전성 케이스를 포함한 대규모 랜덤 데이터를 삽입합니다.
  DB 초기 구축, 테이블 추가, 데이터 증강 시 사용하세요.
user-invocable: true
---
# 역할

PostgreSQL 테스트 데이터 시딩 전문가. 정보계 업무 테이블과 이력 DB에 불완전성이 포함된 실무 유사 데이터를 생성·적재.

# 핵심 원칙

- **요구사항 문서가 단일 진실 소스(Single Source of Truth)**
  - 테이블 수, ★ 테이블 목록, 데이터 규모, TYPE-1/2/4 상세 요건 등 모든 구체적 수치는 요구사항 문서를 참조
  - SKILL.md에는 절차와 원칙만 기술하고, 구체적 데이터 상세는 하드코딩하지 않음
- 반드시 `docs/agent-guides/test-data-requirements.md`의 **최신** 요구사항을 준수
- 반드시 `docs/agent-guides/test-data-seeding-reference.py`의 `seed_postgres()` 구조·연결 방식·데이터 포맷을 참고
- **테이블/컬럼 명명규칙**: 요구사항 문서 섹션 2의 폐쇄망 실환경 명명규칙을 따름
- `.env` 기반 연결 정보 사용 (하드코딩 금지)
- 재실행 안전성: `TRUNCATE CASCADE` 또는 `ON CONFLICT DO NOTHING` 방식 유지
- PII 컬럼은 가짜 데이터로 생성 (실제 개인정보 사용 금지)

# 대상 DB 및 스키마

## PG ↔ MongoDB 스키마 일관성 (핵심 원칙)

- 요구사항 문서 섹션 5에 정의된 **모든 테이블은 PG에 DDL이 존재**해야 한다
- MongoDB `table_meta`/`column_meta`에 등록된 테이블과 PG DDL은 반드시 1:1 대응
- 에이전트가 MongoDB 메타를 참조하여 SQL을 생성하면, 해당 SQL이 PG에서 실행 가능해야 함
- 데이터가 없는 테이블은 빈 결과(`0 rows`)를 반환 — 테이블이 없어서 에러가 발생하면 안 됨

## 정보계 DB (`PG_INFO_DSN`) — `biz_schema`

- **DDL 생성 대상**: 요구사항 문서 섹션 5.0~5.14의 **모든** 테이블
- **데이터 적재 대상 (★)**: 섹션 5에서 ★ 표시된 핵심 테이블만
- **나머지**: DDL만 존재, 데이터 없음 (빈 테이블)
- 정확한 테이블 목록·개수·데이터 규모는 요구사항 문서 섹션 5, 6 참조

## 이력 DB (`PG_HISTORY_DSN`) — `sys_schema`

- `sql_exec_log` (★ 데이터 적재) — 요구사항 문서 섹션 5.15 참조

# 불완전성 재현 (필수)

각 TYPE은 반드시 데이터에 포함되어야 합니다.
**구체적인 테이블·컬럼·코드값·비율은 요구사항 문서 섹션 4를 참조합니다.**

- **TYPE-1: 테이블 선택 모호성** — 유사 테이블 간 동일 키(EDPS_CSN, ACN, LN_NO) 공유, 컬럼 70~80% 겹침
- **TYPE-2: 코드값 불일치** — 랜덤 데이터의 약 3%에 MongoDB 메타에 미정의 코드 삽입
- **TYPE-3**: PG에서 직접 해당 없음 (MongoDB 메타 연계)
- **TYPE-4: 데이터 이중화** — 동일 비즈니스 개념이 여러 테이블에 분산, 값 미묘하게 차이

# 작업 절차

> **절대 규칙:** 이미 데이터가 존재하더라도 스킬이 호출되면 반드시 Phase 1(사전 검증)을 수행한다.
> 요구사항과 현재 상태가 하나라도 불일치하면 전체 삭제 후 재생성한다.
> 시딩 완료 후에는 반드시 Phase 3(사후 검증)을 수행하고 결과 테이블을 출력한다.

## Phase 1: 사전 검증 (Drift Detection)

1. `.env` 파일 존재 여부 및 PG 연결 정보 확인
2. `test-data-requirements.md`의 **최신** 요구사항을 읽고 파악:
   - 섹션 5의 전체 테이블 카탈로그 (DDL 대상 총 수)
   - 섹션 6의 ★ 테이블 목록 및 개수
   - 각 테이블의 컬럼 구조 (PK, 기준일 컬럼, 주요 컬럼)
   - 데이터 규모 목표 (최소 건수)
   - 섹션 4의 TYPE-1/2/4 불완전성 세부 요건
3. 현재 PG에 적재된 데이터와 요구사항을 **SQL로 비교**:
   - **DDL 존재 여부**: `biz_schema` 내 테이블 수가 요구사항의 총 테이블 수와 일치하는지
   - 컬럼 구조 일치 여부 (★ 테이블 대상)
   - 각 ★ 테이블 행 수가 최소 건수 이상인지
   - TYPE-2 코드 불일치 비율이 약 3% 범위인지
   - TYPE-4 이중화 데이터가 올바르게 차이나는지
4. **불일치 항목이 하나라도 있으면** → Phase 2로 진행 (전체 재생성)
5. **모든 항목 일치** → Phase 3(사후 검증)만 수행하고 "변경 없음" 보고 후 종료

## Phase 2: 시딩 실행

1. `test-data-requirements.md` 섹션 5의 전체 테이블 카탈로그를 기반으로 DDL 생성
2. **DDL 생성 (전체)**:
   - 섹션 5.0~5.14의 모든 테이블에 대해 `CREATE TABLE IF NOT EXISTS` 실행
   - 각 테이블은 PK 컬럼 + 한글명에서 유추한 업무 컬럼 5~15개로 구성
   - ★ 테이블은 기존 `init_postgres.sql`의 상세 DDL 유지
   - 비-★ 테이블은 PK + 주요 컬럼으로 자동 생성
   - 파티션 테이블(TB_ADW_TRX701L 등)은 파티션 정의 포함
3. **데이터 적재 (★ 테이블만)**:
   - 기존 ★ 테이블 데이터 전체 삭제 (`TRUNCATE CASCADE`)
   - 시딩 스크립트(`scripts/seed_postgres.py`) 수정 (필요 시) 및 실행
4. 적재 건수 즉시 출력

## Phase 3: 사후 검증 (필수 — 절대 생략 금지)

시딩 완료 후 **반드시** 아래 항목을 SQL로 검증하고 결과 테이블을 출력한다:

1. **DDL 수 검증**: `biz_schema` 내 테이블 수가 요구사항 섹션 6의 합계와 일치하는지
2. **★ 테이블 행 수 검증**: 데이터 적재 대상 테이블의 `COUNT(*)` ≥ 최소 건수
3. **TYPE-2 검증**: 각 코드 컬럼별 미정의 코드값 건수·비율 (목표: ~3%)
4. **TYPE-4 검증**: 이중화 테이블 간 값 차이 건수 (목표: 거의 100%)
5. **TYPE-1 검증**: 모호성 대상 테이블 쌍의 동일 키 공유 확인
6. **이력 DB 검증**: `sql_exec_log` 건수 및 실패 케이스 포함 여부

검증 결과는 아래 형식으로 출력:

```
| 검증 항목 | 기대값 | 실제값 | 판정 |
|----------|--------|--------|------|
| biz_schema DDL 수 | ≥N (문서 기준) | N | ✅ |
| TB_ADW_CSC101M 행수 | ≥500 | 500 | ✅ |
| TYPE-2 CUS_GRD_CD | ~3% | 2.8% | ✅ |
| TYPE-4 BAL≠SMRY | ~100% | 100% | ✅ |
| ...      | ...    | ...    | ... |
```

**하나라도 ❌ 판정이면** 원인을 파악하고 해당 부분만 수정 후 Phase 3을 재실행한다.

# 기술 참고사항 (트러블슈팅)

## 실행 환경

- **postgres 컨테이너(dc-postgres)에는 Python이 없음** — `docker exec dc-postgres pip install` 불가
- **호스트에서 직접 실행**: `python devtools/scripts/seed_postgres.py`
- 연결 정보: `INFO_DB_HOST=localhost`, `PG_SEED_USER=postgres`, `PG_SEED_PASSWORD=postgres`
- 실행 명령:
  ```bash
  PYTHONIOENCODING=utf-8 PG_SEED_USER=postgres PG_SEED_PASSWORD=postgres python devtools/scripts/seed_postgres.py
  ```

## Windows 인코딩 이슈

- Windows 콘솔 기본 인코딩은 `cp949`이므로, Python print 문에 한글·em dash(—) 등 유니코드가 포함되면 `UnicodeEncodeError` 발생
- **해결**: `PYTHONIOENCODING=utf-8` 환경변수를 설정하여 실행
- 스크립트 내부에서도 `sys.stdout.reconfigure(encoding='utf-8')` 추가를 고려

## 스키마 권한

- `biz_schema`를 DROP/CREATE 할 때 `readonly_user`에는 권한이 없음 → **postgres superuser로 실행**:
  ```bash
  docker exec dc-postgres psql -U postgres -d info_db -c "DROP SCHEMA IF EXISTS biz_schema CASCADE; CREATE SCHEMA biz_schema AUTHORIZATION readonly_user;"
  ```
- 시딩 스크립트는 `PG_SEED_USER=postgres`로 실행하여 DDL 생성 권한 확보
- 시딩 후 **readonly_user에게 SELECT 권한 부여 필수**:
  ```bash
  docker exec dc-postgres psql -U postgres -d info_db -c "GRANT USAGE ON SCHEMA biz_schema TO readonly_user; GRANT SELECT ON ALL TABLES IN SCHEMA biz_schema TO readonly_user;"
  ```

## 테이블명 대소문자

- PostgreSQL은 따옴표 없는 식별자를 **소문자로 폴딩**
- `CREATE TABLE biz_schema.TB_ADW_CSC101M (...)` → 실제 저장: `tb_adw_csc101m`
- `information_schema.tables`에서 조회 시 소문자로 검색해야 함
- SQL에서 참조 시 대소문자 무관 (따옴표 미사용 시)

## DDL 자동 생성 패턴

- `test-data-requirements.md` 섹션 5의 테이블 카탈로그를 **런타임에 파싱**하여 테이블 목록 추출
- 파싱 정규식: `r'\| \d+ \| .*?`(TB_ADW_\w+)`.*?\| `(.*?)` \|'`
- PK 컬럼은 PK 예시 문자열을 `+` 기준으로 split하여 추출
- PK 컬럼 타입은 `PK_TYPE_MAP` 딕셔너리로 매핑 (미매핑 시 `VARCHAR(20)` fallback)
- 비-★ 테이블은 PK + 테이블 유형별 표준 컬럼(M/D/L/H/G/S/P) 자동 생성
- 이 방식으로 요구사항 문서와 DDL이 **자동 동기화**됨

## Phase 3 검증 시 주의

- `readonly_user`로 `information_schema.tables` 조회 시 GRANT 미부여 상태면 **0건** 반환될 수 있음
- **검증은 postgres superuser로 수행**하거나, 사전에 GRANT 부여 확인
- ★ 테이블 행 수 검증은 `UNION ALL + count(*)` 패턴이 가장 안정적 (xml 방식은 PG 버전에 따라 불안정)

# 산출물 위치

- 시딩 스크립트: `devtools/scripts/seed_postgres.py`
- DDL: `devtools/scripts/ddl/`

# 인자 사용법

- `$ARGUMENTS` 없이 호출: 전체 테이블 시딩
- `$ARGUMENTS`에 테이블명 지정: 해당 테이블만 시딩 (예: `/seed-postgres TB_ADW_CSC101M`)
- `$ARGUMENTS`에 `augment` 지정: 기존 데이터에 불완전성 케이스 추가
