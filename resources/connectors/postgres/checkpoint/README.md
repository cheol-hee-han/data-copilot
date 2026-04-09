# 폐쇄망 PostgreSQL 체크포인터 환경 셋업

Data Copilot 체크포인터 + 대화이력 DB 환경을 구성하기 위한 DDL 모음.

## 실행 순서

```
01_schema_and_permissions.sql   -- 스키마 생성 + BDPETL 계정 권한
02_checkpointer_tables.sql      -- LangGraph 체크포인터 테이블 (상태 영속화)
03_dc_custom_tables.sql         -- Data Copilot 대화이력 + 세션 인덱스 + PII 마스킹
04_partman_setup.sql            -- pg_partman 파티션 자동 관리 (선택)
```

## 전제 조건

- PostgreSQL 16+
- DB: `history_db` (이미 존재)
- 계정: `BDPETL` (이미 존재, DBA가 사전 생성)
- 모든 DDL은 `BDPETL` 계정으로 실행하거나, DBA가 실행 후 소유권 이전

## 실행 방법

```bash
# BDPETL 계정으로 순차 실행
psql -U BDPETL -d history_db -f 01_schema_and_permissions.sql
psql -U BDPETL -d history_db -f 02_checkpointer_tables.sql
psql -U BDPETL -d history_db -f 03_dc_custom_tables.sql
psql -U BDPETL -d history_db -f 04_partman_setup.sql   # pg_partman 설치 시에만
```
