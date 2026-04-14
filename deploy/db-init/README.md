# db-init — 기구축 DB 초기화

폐쇄망에 **이미 구축된** PostgreSQL / MongoDB / Qdrant 에 대해
Data Copilot 운영에 필요한 스키마·컬렉션·인덱스만 생성하는 스크립트 모음입니다.

> DB 서버 자체 설치는 본 스크립트 범위가 아닙니다. (인프라팀이 구축한다고 가정)

## 실행 순서 (권장)

1. `.env` 가 `/opt/data-copilot/` 에 배치되어 있을 것
   (혹은 현재 셸에서 환경변수가 export 되어 있을 것)
2. 각 DB 에 대해 아래 순서로 실행:

```bash
cd /opt/data-copilot

# PostgreSQL (이력·체크포인트 테이블)
bash deploy/db-init/postgres/init.sh

# MongoDB (메타 컬렉션 + 인덱스)
bash deploy/db-init/mongo/init.sh

# Qdrant (biz_manual, sql_history 컬렉션)
bash deploy/db-init/qdrant/init.sh
```

## 필요한 환경변수

| DB | 변수 |
|---|---|
| PostgreSQL | `PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD`, `PGDATABASE` |
| MongoDB    | `MONGO_HOST`, `MONGO_PORT`, `MONGO_USER`, `MONGO_PASSWORD`, `MONGO_DATABASE` |
| Qdrant     | `QDRANT_HOST`, `QDRANT_PORT` (기본 6333), `EMBEDDING_DIM` (기본 1024) |

`.env` 로드는 각 스크립트가 자체적으로 처리하지 않으므로 필요 시
`set -a; source /opt/data-copilot/.env; set +a` 로 수동 export 후 실행합니다.

## 주의

- **본 스크립트는 스키마·컬렉션·인덱스 생성만 수행**하며,
  실데이터 시딩(업무 매뉴얼, SQL 이력 임베딩 등)은 별도 운영 절차로 수행합니다.
- PG 스크립트는 `resources/connectors/postgres/checkpoint/` 의 SQL 파일을
  순서대로 실행하는 **래퍼**입니다. 원본 SQL은 수정하지 않습니다.
