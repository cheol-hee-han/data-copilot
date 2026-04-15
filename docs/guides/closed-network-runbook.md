# 폐쇄망 반입·기동 런북 (명령어 기반 Step-by-Step)

> **작성일**: 2026-04-15
> **대상 독자**: 반입·설치를 직접 수행하는 엔지니어
> **성격**: **명령어만 복붙해서 순서대로 따라가는 실행 문서**
>   (배경·설계 근거는 [migration-guide.md](migration-guide.md) 참조)
> **전제**: 폐쇄망에 DB 서버(PG/Mongo/Qdrant/Redis)·실데이터가 이미 존재. 본 작업은 **앱 반입 + 스키마 초기화 + SQL 이력 재임베딩** 중심.

---

## 0. 전체 순서 (개요)

```
[빌드머신(온라인)]               [반입]            [타겟(폐쇄망)]
  1. 사전 점검                                     4. OS/Python 점검
  2. 번들 생성(build.sh) ─── tar.gz ─── 5. 반입 검증
                                                   6. 설치(install.sh)
                                                   7. .env 작성
                                                   8. DB 스키마 초기화
                                                   9. SQL 이력 재임베딩
                                                  10. LLM 엔드포인트 검증
                                                  11. systemd 기동
                                                  12. 스모크 테스트
                                                  13. (이슈 시) 롤백
```

각 단계는 **독립적으로 재실행 가능**하도록 설계되어 있습니다. 단, 순서를 건너뛰지 마세요.

---

## 1. 빌드머신 사전 점검 (온라인 Linux)

**전제**: Rocky 9 / RHEL 9 계열. 인터넷 접속 가능. 디스크 20GB↑.

```bash
# 1-1. 기본 도구 확인
curl --version && tar --version && sha256sum --version && dnf --version

# 1-2. uv 설치 (없으면)
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.cargo/env   # 또는 ~/.bashrc 재로드
uv --version              # 0.4.x 이상 확인

# 1-3. huggingface-cli 설치
python3 -m pip install -U "huggingface_hub[cli]"
huggingface-cli --version

# 1-4. (선택) HF 토큰 — gated 모델 접근 시
# huggingface-cli login

# 1-5. 디스크 여유 확인 (20GB 이상 권장)
df -h $(pwd)

# 1-6. 프로젝트 체크아웃
git clone <repo-url> data-copilot
cd data-copilot
git log -1 --oneline   # 반입 기준 커밋 기록
```

---

## 2. 번들 생성 (빌드머신)

```bash
cd data-copilot

# 2-1. 번들 빌드 (Python tarball + wheels + torch CPU + HF 모델 + RPM + 앱 소스)
bash deploy/offline-bundle/build.sh

# 2-2. 산출물 확인
ls -lh deploy/offline-bundle/data-copilot-bundle-*.tar.gz

# 2-3. 해시 기록 (반입 검증용)
sha256sum deploy/offline-bundle/data-copilot-bundle-*.tar.gz \
  | tee deploy/offline-bundle/BUNDLE.sha256

# 2-4. 내용물 간이 점검
cat deploy/offline-bundle/dist/MANIFEST.txt
```

### 실패 시 부분 재실행

`build.sh`는 7단계로 구성되어 있습니다. 실패 단계만 재실행하려면 스크립트를 복사해 일부 단계를 주석 처리하거나, 수동 실행하세요:

```bash
# 예: torch wheel 다운로드만 재시도
uv pip download \
  --python-version 3.12.7 \
  --dest deploy/offline-bundle/dist/wheels \
  --index-url https://download.pytorch.org/whl/cpu \
  torch torchvision

# 예: HF 모델만 재다운로드
bash deploy/offline-bundle/download_models.sh deploy/offline-bundle/dist/models
```

---

## 3. 반입 (매체 전달)

```bash
# 3-1. 번들과 해시 파일을 반입 매체에 복사
cp deploy/offline-bundle/data-copilot-bundle-*.tar.gz /path/to/media/
cp deploy/offline-bundle/BUNDLE.sha256                /path/to/media/
```

반입 경로는 조직 보안 규정 준수.

---

## 4. 타겟 호스트 사전 점검 (폐쇄망)

```bash
# 4-1. OS 확인
cat /etc/redhat-release        # Rocky/RHEL 9.x 계열인지
uname -a

# 4-2. Python 3.12 여부
command -v python3.12 && python3.12 --version   # 없으면 5-3에서 tarball 빌드

# 4-3. 방화벽·DB 접속 점검 (인프라팀 선행 확인)
nc -zv $PGHOST $PGPORT
nc -zv $MONGO_HOST $MONGO_PORT
nc -zv $QDRANT_HOST $QDRANT_PORT
nc -zv $REDIS_HOST $REDIS_PORT
# (옵션) 업무DB
# nc -zv $IMPALA_HOST $IMPALA_PORT
# nc -zv $ADW_HOST $ADW_PORT

# 4-4. LDAP 계정 확인 (Impala LDAP 인증 사용 시)
# id $IMPALA_USER   # 또는 ldapsearch ...

# 4-5. 설치 위치 디스크 확인 (번들 해제 + /opt 설치 합계 ≥ 30GB)
df -h /opt /tmp
```

---

## 5. 번들 해제 및 검증 (폐쇄망)

```bash
# 5-1. 매체에서 타겟으로 복사
mkdir -p /tmp/dc-install && cd /tmp/dc-install
cp /path/to/media/data-copilot-bundle-*.tar.gz .
cp /path/to/media/BUNDLE.sha256 .

# 5-2. 해시 검증 (필수)
sha256sum -c BUNDLE.sha256
# OK 출력 확인. 실패 시 매체 재복사.

# 5-3. 해제
tar -xzf data-copilot-bundle-*.tar.gz
ls dist/   # python/ wheels/ models/ os-packages/ app/ MANIFEST.txt

# 5-4. (Python 3.12 미설치인 경우에만) tarball로 수동 설치
#     — 정책상 install.sh 는 자동 컴파일하지 않음
# sudo dnf install -y gcc openssl-devel bzip2-devel libffi-devel zlib-devel
# tar -xzf dist/python/Python-3.12.7.tgz -C /tmp && cd /tmp/Python-3.12.7
# ./configure --enable-optimizations --prefix=/usr/local && sudo make -j altinstall
# command -v python3.12 && python3.12 --version
```

---

## 6. 설치 (폐쇄망)

```bash
cd /tmp/dc-install    # dist/ 상위

# 6-1. install.sh 실행 (root)
sudo bash dist/app/deploy/offline-bundle/install.sh

# 6-2. 설치 결과 점검
ls /opt/data-copilot/                          # 앱 배치 확인
ls /opt/data-copilot/.venv/bin/python          # .venv 생성 확인
/opt/data-copilot/.venv/bin/python --version   # 3.12.x
ls $EMBEDDING_MODEL_CACHE_PATH 2>/dev/null \
  || ls /opt/data-copilot/models/              # 모델 가중치 위치
id datacopilot                                 # 서비스 계정 생성 확인
```

실패 시 [§13 롤백](#13-롤백-및-트러블슈팅) 참조.

---

## 7. `.env` 작성 (폐쇄망)

```bash
sudo -u datacopilot cp /opt/data-copilot/.env.example /opt/data-copilot/.env
sudo -u datacopilot chmod 600 /opt/data-copilot/.env
sudo -u datacopilot vi /opt/data-copilot/.env
```

### 7-1. 폐쇄망 최소 필수 키 템플릿

아래 블록을 `.env` 에 반영하세요. **플레이스홀더(`<…>`)를 모두 실값으로 교체**해야 합니다.

```bash
# ── LLM: 폐쇄망 LLM Gateway (OpenAI Compatible) ─────────────
LLM_PROVIDER=openai_compatible
OPENAI_BASE_URL=<SOLAR_PRO_2_ENDPOINT_URL>     # ← 별도 지급
OPENAI_API_KEY=<SOLAR_PRO_2_API_KEY>           # ← 별도 지급
LLM_MODEL=<SOLAR_PRO_2_MODEL_NAME>             # 예: solar-pro-2
# (향후 Qwen3.5 397B 전환 시 §10-3 참조)

# ── DB: 실데이터 기보유. 접속 정보만 ───────────────────────
USE_DUMMY=false

POSTGRES_DB_HOST=<pg-host>
POSTGRES_DB_PORT=5432
POSTGRES_DB_NAME=<pg-db>
POSTGRES_DB_USER=<pg-user>
POSTGRES_DB_PASSWORD=<pg-pw>

MONGO_HOST=<mongo-host>
MONGO_PORT=27017
MONGO_USER=<mongo-user>
MONGO_PASSWORD=<mongo-pw>
MONGO_DATABASE=<mongo-db>

QDRANT_HOST=<qdrant-host>
QDRANT_PORT=6333

REDIS_HOST=<redis-host>
REDIS_PORT=6379
REDIS_PASSWORD=<redis-pw>
REDIS_BACKEND=redis                # 멀티 워커 운영 필수

# ── 업무DB 라우팅 (ADW/BDP/CRP) ────────────────────────────
TARGET_DB_CODE=                    # 비우면 동적 결정
SYSTEM_DB_OVERRIDES={}             # 폐쇄망은 identity 매핑

# ADW (Sybase IQ)
SYBASE_HOST=<adw-host>
SYBASE_PORT=2638
SYBASE_USER=<adw-user>
SYBASE_PASSWORD=<adw-pw>
SYBASE_DATABASE=<adw-db>
SYBASE_ODBC_DRIVER=SQL Anywhere 16

# BDP (Impala, LDAP)
IMPALA_HOST=<bdp-host>
IMPALA_PORT=21050
IMPALA_AUTH=LDAP
IMPALA_USER=<ldap-user>
IMPALA_PASSWORD=<ldap-pw>
IMPALA_DATABASE=default

# CRP (필요 시) — 실제 커넥터 env는 src/connectors/impl/crp_connector.py 참조

# ── 임베딩 / Reranker (CPU 추론) ───────────────────────────
EMBEDDING_USE_FP16=false
EMBEDDING_CACHE_PATH=/opt/data-copilot/models/bge-m3
RERANKER_ENABLED=true
RERANKER_USE_FP16=false
RERANKER_CACHE_PATH=/opt/data-copilot/models/bge-reranker-v2-m3

# ── 운영 ────────────────────────────────────────────────
GUNICORN_BIND=0.0.0.0:8000
GUNICORN_WORKERS=4
GUNICORN_TIMEOUT=120
LOG_LEVEL=INFO
LOG_FORMAT=json
MAX_QUERY_ROWS=10000
```

전체 70+ 키 상세는 [env-configuration-guide.md](env-configuration-guide.md) 참조.

### 7-2. env 로드 검증

```bash
sudo -u datacopilot bash -c '
  set -a; source /opt/data-copilot/.env; set +a
  cd /opt/data-copilot
  ./.venv/bin/python -c "from src.config import settings; print(settings.llm_provider, settings.llm_model, settings.postgres_db.host)"
'
```

---

## 8. DB 스키마 초기화 (폐쇄망, 순서 고정)

**데이터는 기보유. 이 단계는 Data Copilot 운영에 필요한 스키마·컬렉션·인덱스만 생성합니다.**

```bash
cd /opt/data-copilot
set -a; source .env; set +a

# 8-1. PostgreSQL (checkpoint_dc_*, message_store 등)
sudo -u datacopilot bash deploy/db-init/postgres/init.sh
# 내부: resources/connectors/postgres/checkpoint/01~05*.sql 순차 실행

# 8-2. MongoDB (메타 컬렉션 + 인덱스)
sudo -u datacopilot bash deploy/db-init/mongo/init.sh

# 8-3. Qdrant (biz_manual + sql_history 컬렉션, Named Vectors)
sudo -u datacopilot bash deploy/db-init/qdrant/init.sh

# 8-4. 초기화 결과 검증
sudo -u datacopilot ./.venv/bin/python -c "
from qdrant_client import QdrantClient
c = QdrantClient(host='$QDRANT_HOST', port=$QDRANT_PORT)
for n in ['biz_manual','sql_history']:
    print(n, c.get_collection(n).status)
"
```

**⚠ 순서 주의**:
- PG → Mongo → Qdrant 순서는 앱 기동 시 초기 연결 체크 순서와 일치. 역순 실행은 가능하지만 권장하지 않음.
- 재실행: 모든 init.sh 는 **idempotent** (존재하면 skip). 단, Qdrant는 기존 컬렉션을 drop 후 재생성하므로 **이미 벡터가 들어있다면 덮어씌워집니다** → §9 재임베딩 전 실행.

---

## 9. SQL 이력 재임베딩 (BGE-M3, 약 10만 건)

**배경**: 폐쇄망 Postgres `sql_exec_history` 테이블의 SQL 이력을 BGE-M3로 임베딩하여 Qdrant `sql_history` 컬렉션(dense+sparse Named Vectors)에 적재합니다. 스크립트는 [devtools/scripts/enrich_sql_history.py](../../devtools/scripts/enrich_sql_history.py) 하나로 통합되어 **초기 전량 적재·일일 증분·삭제·reconcile** 을 모두 처리합니다.

### 9-0. 테이블 선행 조건

`sql_exec_history` (또는 CLI로 지정한 테이블)에 다음 컬럼이 있어야 합니다.

| 컬럼 | 타입 | 용도 |
|---|---|---|
| `<PK>` | 테이블 고유 PK | SQL 수행이력 고유 식별자 (이름 자유) |
| `sql_text` | TEXT | 임베딩 원문 (필수) |
| `sql_description` | TEXT | NULL 허용 — `--mode generate-desc` 시 LLM이 생성 |
| `embed_flag` | CHAR(1) | `Y` = 임베딩 대상, 그 외 = Qdrant에서 삭제 |
| `updated_at` | TIMESTAMP | 증분 커서 키 (필수) |
| `qdrant_point_id` | UUID NULL | **임베딩 성공 시 배치가 기록**. 재임베딩·삭제 시 재사용 |
| `embedded_at` | TIMESTAMP | 배치가 write-back |
| `embed_version` | VARCHAR(32) | 배치가 write-back (예: `bge-m3-v1`) |

**point_id 수명주기**:
- 첫 임베딩: `uuid4()` 생성 → Qdrant upsert → Postgres에 기록
- 재임베딩(같은 행 수정): 저장된 `qdrant_point_id` 재사용 → 동일 point 덮어쓰기
- Y→N 전환: 저장된 `qdrant_point_id` 로 Qdrant delete → Postgres에서 `NULL` 복원

컬럼명은 CLI 옵션으로 모두 override 가능. 테이블명이 변경되면 `--pg-table` 로 지정.

**기존 테이블에 부족한 컬럼만 추가하려면**(이관자 DDL, 앱 소유 아님):

```sql
ALTER TABLE sql_exec_history
  ADD COLUMN IF NOT EXISTS embed_flag      CHAR(1) DEFAULT 'Y',
  ADD COLUMN IF NOT EXISTS qdrant_point_id UUID,
  ADD COLUMN IF NOT EXISTS embedded_at     TIMESTAMP,
  ADD COLUMN IF NOT EXISTS embed_version   VARCHAR(32);

CREATE INDEX IF NOT EXISTS idx_sql_exec_history_updated_at
  ON sql_exec_history(updated_at);
CREATE INDEX IF NOT EXISTS idx_sql_exec_history_point_id
  ON sql_exec_history(qdrant_point_id) WHERE qdrant_point_id IS NOT NULL;
```

`updated_at` 인덱스는 증분 커서 스캔 성능, `qdrant_point_id` 부분 인덱스는 재임베딩·삭제 조회용.

### 9-1. 사전 점검

```bash
cd /opt/data-copilot
set -a; source .env; set +a

# 9-1-1. 대상 건수 확인 (활성 플래그 기준)
sudo -u datacopilot ./.venv/bin/python -c "
import asyncio, asyncpg
async def main():
    conn = await asyncpg.connect(
        host='$POSTGRES_DB_HOST', port=$POSTGRES_DB_PORT,
        database='$POSTGRES_DB_NAME', user='$POSTGRES_DB_USER',
        password='$POSTGRES_DB_PASSWORD')
    active = await conn.fetchval(
        \"SELECT COUNT(*) FROM sql_exec_history WHERE embed_flag='Y'\")
    total = await conn.fetchval('SELECT COUNT(*) FROM sql_exec_history')
    print(f'total: {total}, active(Y): {active}')
    await conn.close()
asyncio.run(main())
"

# 9-1-2. Qdrant sql_history 기존 point 수 (init.sh 직후면 0)
sudo -u datacopilot ./.venv/bin/python -c "
from qdrant_client import QdrantClient
c = QdrantClient(host='$QDRANT_HOST', port=$QDRANT_PORT)
print('before:', c.get_collection('sql_history').points_count)
"

# 9-1-3. Dry-run: 처리 대상 쿼리·건수만 출력
sudo -u datacopilot ./.venv/bin/python \
  devtools/scripts/enrich_sql_history.py \
  --source postgres --mode direct --full --dry-run
```

### 9-2. 초기 전량 적재

description이 **이미 채워져 있는** 경우 (Case 2 — 권장):

```bash
sudo -u datacopilot nohup ./.venv/bin/python \
  devtools/scripts/enrich_sql_history.py \
  --source postgres --mode direct --full \
  --concurrency 8 --embed-batch-size 64 \
  --embed-version bge-m3-v1 \
  > logs/reembed_sql_history.log 2>&1 &

tail -f logs/reembed_sql_history.log
```

description이 **NULL**이라 LLM으로 생성해야 하는 경우 (Case 1):

```bash
sudo -u datacopilot nohup ./.venv/bin/python \
  devtools/scripts/enrich_sql_history.py \
  --source postgres --mode generate-desc --full \
  --concurrency 4 --llm-batch-size 20 --embed-batch-size 64 \
  --embed-version bge-m3-v1 \
  > logs/reembed_sql_history.log 2>&1 &
```

> CPU 추론 기준 10만 건 소요시간: Case 2 약 2~4시간, Case 1은 LLM 호출 비중 커서 6~12시간 (Solar Pro 2 rate limit 의존). 실패 시 지수 백오프 3회 자동 재시도.

### 9-3. 적재 검증

```bash
# Qdrant 적재 결과
sudo -u datacopilot ./.venv/bin/python -c "
from qdrant_client import QdrantClient
c = QdrantClient(host='$QDRANT_HOST', port=$QDRANT_PORT)
info = c.get_collection('sql_history')
print('points_count :', info.points_count)
print('vectors_config:', info.config.params.vectors)
print('sparse_config:', info.config.params.sparse_vectors)
"
# 기대: points_count ≈ Postgres embed_flag='Y' 건수, dense + sparse Named Vectors 존재

# Postgres write-back 확인
sudo -u datacopilot ./.venv/bin/python -c "
import asyncio, asyncpg
async def main():
    conn = await asyncpg.connect(
        host='$POSTGRES_DB_HOST', port=$POSTGRES_DB_PORT,
        database='$POSTGRES_DB_NAME', user='$POSTGRES_DB_USER',
        password='$POSTGRES_DB_PASSWORD')
    row = await conn.fetchrow(
        \"SELECT COUNT(*) AS n, MAX(embedded_at) AS last \"
        \"FROM sql_exec_history WHERE embed_version='bge-m3-v1'\")
    print(dict(row))
    await conn.close()
asyncio.run(main())
"
```

### 9-4. 일일 증분 (운영 cron)

상태파일(`devtools/scripts/.reembed_state.json`)에 저장된 `last_updated_at` 이후 변경분만 처리. `embed_flag` Y→N 전환은 자동으로 Qdrant 삭제.

```bash
# 매일 02:00 증분 (crontab 예시)
0 2 * * * cd /opt/data-copilot \
  && ./.venv/bin/python devtools/scripts/enrich_sql_history.py \
     --source postgres --mode direct --since-last-run \
     --concurrency 8 \
     >> logs/reembed_sql_history.log 2>&1
```

### 9-5. Reconcile (주간 안전장치)

`updated_at` 누락 등으로 Qdrant에 남은 고아 point를 제거. **증분과 독립적으로** 실행:

```bash
# 매주 일요일 03:00
0 3 * * 0 cd /opt/data-copilot \
  && ./.venv/bin/python devtools/scripts/enrich_sql_history.py \
     --source postgres --mode direct --reconcile-deletes \
     --reconcile-chunk-size 10000 \
     >> logs/reembed_sql_history.log 2>&1
```

### 9-6. 중단·재개

배치가 중단되면 **상태파일이 갱신되지 않았으므로** 다음 실행이 같은 커서부터 재시작. 특정 id 이후부터 재처리하려면:

```bash
--resume-from <last_processed_id>
```

### 9-7. 주요 CLI 옵션

전체 옵션은 `--help` 참조. 자주 쓰는 것:

| 옵션 | 용도 |
|---|---|
| `--mode {direct\|generate-desc}` | description 직접 사용 / LLM 생성 |
| `--full` / `--since <ISO>` / `--since-last-run` | 증분 커서 선택 |
| `--pg-table`, `--*-column` | 테이블·컬럼명 override |
| `--embed-version bge-m3-v1` | write-back 버전 태그 |
| `--concurrency 8` | 병렬도 |
| `--retry-attempts 3` | 재시도 횟수 |
| `--reconcile-deletes` | Qdrant 고아 point 제거 모드 |
| `--dry-run` | 대상 조회만 |
| `--no-upsert --json-output <path>` | Qdrant 미수정, JSON 덤프만 |
| `--no-write-back-status` | Postgres `embedded_at` 갱신 생략 |

---

## 10. LLM 엔드포인트 검증

### 10-1. Solar Pro 2 (현재)

```bash
set -a; source /opt/data-copilot/.env; set +a

# 10-1-1. 네트워크 도달성
curl -sS -o /dev/null -w "HTTP %{http_code}\n" "$OPENAI_BASE_URL/models"

# 10-1-2. 채팅 응답 확인
curl -sS "$OPENAI_BASE_URL/chat/completions" \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{
    \"model\": \"$LLM_MODEL\",
    \"messages\": [{\"role\":\"user\",\"content\":\"ping\"}],
    \"max_tokens\": 20
  }" | python3 -m json.tool
```

### 10-2. 앱 경유 검증

```bash
sudo -u datacopilot bash -c '
  set -a; source /opt/data-copilot/.env; set +a
  cd /opt/data-copilot
  ./.venv/bin/python -c "
import asyncio
from src.utils.llm.client import get_llm_client
async def main():
    client = get_llm_client()
    r = await client.complete([{\"role\":\"user\",\"content\":\"say ok\"}], max_tokens=10)
    print(r)
asyncio.run(main())
"
'
```

### 10-3. Qwen3.5 397B 전환 가이드 (향후 적용)

Qwen3.5 397B도 OpenAI Compatible 엔드포인트로 노출된다는 전제입니다. 전환 시 `.env`만 교체하면 코드 변경 없이 동작합니다.

```bash
# Qwen3.5 전환 (게이트웨이/엔드포인트 준비 후)
LLM_PROVIDER=openai_compatible
OPENAI_BASE_URL=<QWEN35_ENDPOINT_URL>
OPENAI_API_KEY=<QWEN35_API_KEY>
LLM_MODEL=<QWEN35_MODEL_NAME>     # 예: qwen3.5-397b-instruct
```

**체크포인트**:
1. **thinking 모드**: Qwen3는 `<think>...</think>` 블록을 응답에 포함할 수 있음. 현재 파서는 JSON 출력 모드에서 think 블록이 섞이면 파싱 실패 가능 → 전환 직전 샘플 응답으로 JSON 안정성 확인. 필요 시 시스템 프롬프트에 `"답변에 <think> 블록을 포함하지 마세요"` 명시.
2. **max context**: Qwen3.5 실효 컨텍스트가 Solar Pro 2 와 다를 수 있음 → `src/config.py` 의 context window 관련 설정 재확인.
3. **프롬프트 재튜닝**: 현재 프롬프트(resources/prompts/)는 Claude/Solar 기준. Qwen 전환 시 골든셋 회귀 테스트 1회 필수. ([sql-evaluator 스킬](../../.claude/skills/sql-evaluator/SKILL.md) 활용)
4. **토크나이저**: BGE-M3 임베딩은 LLM과 독립 — Qwen 전환 영향 없음.
5. **호출 파라미터**: `temperature`, `top_p`, `repetition_penalty` 기본값이 Qwen에서 더 민감. 전환 후 샘플 질의 10~20건으로 스냅샷 비교 권장.

전환 후 검증:

```bash
# 10-3-1. 엔드포인트만 바꾸고 curl 재실행 (§10-1-2)
# 10-3-2. 골든셋 회귀 테스트
sudo -u datacopilot ./.venv/bin/python devtools/evaluation/run_evaluation.py \
  --golden-set evaluation/golden_set/<set-name>.csv \
  --report logs/eval_qwen35.json
# 10-3-3. 정확도 저하 시 프롬프트/파라미터 튜닝 후 재평가
```

---

## 11. systemd 기동

```bash
# 11-1. unit 파일 등록
sudo cp /opt/data-copilot/deploy/systemd/data-copilot.service /etc/systemd/system/
sudo systemctl daemon-reload

# 11-2. enable + start
sudo systemctl enable data-copilot.service
sudo systemctl start  data-copilot.service

# 11-3. 상태 확인
sudo systemctl status data-copilot.service --no-pager
sudo journalctl -u data-copilot.service -n 100 --no-pager

# 11-4. 기동 로그에서 DB 연결 성공 확인
sudo journalctl -u data-copilot.service -f
# → "Checkpointer 초기화", "Qdrant 연결", "MongoDB 연결" 메시지 확인 후 Ctrl-C
```

---

## 12. 스모크 테스트

### 12-1. 헬스체크

```bash
curl -sS http://localhost:8000/health        | python3 -m json.tool
curl -sS http://localhost:8000/health/live   | python3 -m json.tool
curl -sS http://localhost:8000/health/ready  | python3 -m json.tool
# /health/ready: 모든 의존성(PG/Mongo/Qdrant/Redis/LLM) status=ok
```

### 12-2. 샘플 질의 (non-WS 엔드포인트)

```bash
curl -sS -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"user_id":"smoke","query":"최근 한 달 신규 여신 건수"}' \
  | python3 -m json.tool | head -80
```

기대: `session_id`, `status`, `sql` 필드가 포함된 응답. SQL 생성 실패여도 구조화된 에러면 OK.

### 12-3. Qdrant 검색 경로 확인

```bash
curl -sS -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"user_id":"smoke","query":"작년 분기별 예수금 추이를 보고서 형태로"}' \
  | python3 -m json.tool | head -120
# 로그에서 sql_history 검색 hit 확인
sudo journalctl -u data-copilot.service -n 200 | grep -i "sql_history\|reranker"
```

### 12-4. 프론트엔드 확인

```bash
# 브라우저에서 http://<host>:8000/static/embedded.html 접속
# 또는
curl -sS http://localhost:8000/static/embedded.html | head -20
```

---

## 13. 롤백 및 트러블슈팅

### 13-1. 설치 실패 롤백

```bash
sudo systemctl stop data-copilot.service 2>/dev/null || true
sudo systemctl disable data-copilot.service 2>/dev/null || true
sudo rm -f /etc/systemd/system/data-copilot.service
sudo systemctl daemon-reload

# 앱 제거 (모델/로그 보존하려면 /opt/data-copilot/{models,logs} 백업)
sudo rm -rf /opt/data-copilot

# 서비스 계정 제거 (선택)
sudo userdel -r datacopilot 2>/dev/null || true
```

### 13-2. DB 스키마 롤백

- **PostgreSQL**: `resources/connectors/postgres/checkpoint/05_rollback_message_to_turn.sql` 및 각 파일의 DROP 스테이트먼트 참조.
- **Qdrant**: `sql_history` / `biz_manual` 컬렉션 drop 후 init.sh 재실행.
- **MongoDB**: 인덱스만 추가되었으므로 `db.<collection>.dropIndex(...)` 로 개별 제거.

### 13-3. 자주 발생하는 이슈

| 증상 | 원인 | 조치 |
|---|---|---|
| `uv sync --offline` 실패 | wheel 누락 (플랫폼 불일치 등) | 빌드머신 OS/Python 버전을 타겟과 일치시켜 재번들 |
| BGE-M3 로드 시 `torch` ABI 오류 | CUDA 빌드 wheel 혼입 | `deploy/offline-bundle/dist/wheels/` 의 torch wheel 파일명이 `+cpu` 포함인지 확인 |
| Qdrant upsert `Bad Request` | Named Vector 이름 불일치 | `init.sh` 와 `seed/reembed` 스크립트의 vector 이름(`dense`/`sparse`) 일치 확인 |
| Impala LDAP 인증 실패 | SASL 라이브러리 누락 | `dnf install -y cyrus-sasl-devel` 후 재설치 |
| systemd `start` 후 즉시 exit | `.env` 파일 권한/위치 오류 | `EnvironmentFile=/opt/data-copilot/.env` 가 루트가 아닌 `datacopilot` 으로 read 가능한지 |
| `/health/ready` 에서 LLM unreachable | 방화벽/프록시 | §10-1-1 curl 재확인, `NO_PROXY` 설정 |
| 재임베딩 중단(OOM) | BGE-M3 배치 과다 | `--batch-size 32 → 16` 로 하향 후 재개 (`--resume-from <id>`) |

### 13-4. 로그 수집 (이슈 리포트)

```bash
sudo journalctl -u data-copilot.service --since "1 hour ago" > /tmp/dc.journal.log
sudo tar -czf /tmp/dc-diag-$(date +%Y%m%d%H%M).tar.gz \
  /tmp/dc.journal.log \
  /opt/data-copilot/logs/ \
  /opt/data-copilot/.env.example \
  2>/dev/null
# .env 는 민감정보 포함 — 공유 전 반드시 마스킹
```

---

## 14. 참고 문서

- [migration-guide.md](migration-guide.md) — 배경·설계 레퍼런스
- [closed-network-readiness-checklist.md](closed-network-readiness-checklist.md) — 반입 전 준비도 체크
- [env-configuration-guide.md](env-configuration-guide.md) — `.env` 키 전체 상세
- [closed-network-db-connectors.md](closed-network-db-connectors.md) — ADW(Sybase)/BDP(Impala) 드라이버
- [customization-targets.md](customization-targets.md) — 환경별 커스터마이징 지점
- [../../deploy/README.md](../../deploy/README.md) — deploy/ 디렉토리 총괄
- [../../deploy/offline-bundle/README.md](../../deploy/offline-bundle/README.md) — 번들 상세
- [../../deploy/db-init/README.md](../../deploy/db-init/README.md) — DB 초기화 상세
- [../../deploy/systemd/README.md](../../deploy/systemd/README.md) — systemd unit 상세

---

## 15. 체크리스트 (인쇄용)

반입 당일 확인용. 각 박스 체크 후 다음 단계로.

- [ ] §1 빌드머신 도구 OK (uv/hf-cli/dnf/curl)
- [ ] §2 번들 생성 성공 + MANIFEST 확인
- [ ] §2-3 해시 기록 완료
- [ ] §3 반입 매체 전달 완료
- [ ] §4 타겟 OS/Python/DB 접속/디스크 OK
- [ ] §5-2 해시 검증 OK
- [ ] §6 install.sh 성공 + .venv 재현
- [ ] §7 `.env` 작성 + env 로드 검증 OK
- [ ] §8 PG/Mongo/Qdrant 스키마 초기화 OK
- [ ] §9 SQL 이력 재임베딩 ≈100K 완료
- [ ] §10-1 Solar Pro 2 엔드포인트 curl OK
- [ ] §10-2 앱 경유 LLM 호출 OK
- [ ] §11 systemd active(running)
- [ ] §12-1 /health/ready 모든 의존성 ok
- [ ] §12-2 샘플 질의 구조화 응답 OK
- [ ] (향후) §10-3 Qwen3.5 전환 시 골든셋 회귀 통과
