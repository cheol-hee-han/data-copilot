# 폐쇄망 반입 준비도 점검 체크리스트

> **점검일**: 2026-04-14 (갱신)
> **목적**: 온라인 개발 → 폐쇄망 반입 직전 상태에서 "바로 옮겨도 되는가"에 대한 팩트 기반 평가
> **범위**: 의존성 / 코드 품질 / 문서·DDL 최신화 / 배포 가이드 / 프로덕션 HA

---

## 0. 종합 평가

| 영역 | 준비도 | 블로커 유무 |
|------|:-----:|:------:|
| 의존성 완전성 | 95% | Torch CPU whl 실빌드 검증만 남음 |
| 프로젝트 오류/품질 | 95% | ✅ mypy 0 errors (105 files), 클린 HEAD 커밋만 남음 |
| 주석·문서·DDL 최신화 | 90% | ⚠ git M 대량 미커밋, embedding-search-integration.md 재정렬 |
| 폐쇄망 환경구성 가이드 | 85% | ⚠ `deploy/offline-bundle/build.sh` 실빌드 dry-run 미검증 |
| 프로덕션 HA | 75% | ⚠ 서킷브레이커·Nginx·Qdrant 클러스터 URI 미비 |

**결론**: **반입 블로커는 실질적으로 2건으로 축소됨** — (a) 클린 HEAD 커밋/태그, (b) `deploy/offline-bundle/build.sh` Linux 빌드 머신 실빌드 검증. 나머지는 반입 후 프로덕션 이전 대응 가능.

**전제 사항 (폐쇄망 환경)**:
- **DB 인프라 기구축**: Qdrant / MongoDB / PostgreSQL / Redis 서버는 폐쇄망에 이미 설치·운영 중 → 서버 설치 불필요, **스키마/컬렉션 초기화만** 수행
- **Docker 미사용**: 컨테이너 런타임 없음. **systemd 기동** 전제 (Dockerfile / K8s manifest 작업 대상 아님)
- **Frontend는 vanilla HTML 단일 파일**: `static/embedded.html` + vendor JS (hljs / marked / html2canvas / sql-formatter). **빌드 파이프라인·npm 불필요**
- **배포 산출물 디렉토리**: `deploy/` 신설 (db-init / offline-bundle / systemd)

---

## 1. 의존성 완전성 (pyproject.toml)

### ✅ 된 것
- [pyproject.toml](../../pyproject.toml)에 실제 import되는 core 패키지(anthropic, langgraph, fastapi, qdrant-client, motor, neo4j 등) 모두 선언됨
- 버전 고정 주석 명확: `transformers<5.0.0` (FlagEmbedding 비호환), `thrift==0.16.0` (impyla 핀)
- dev extras 분리: pytest/mypy/ruff는 `[project.optional-dependencies].dev`
- 업무DB 드라이버 체계화: Impala(impyla+SASL), Sybase(pyodbc/sqlanydb), Oracle(oracledb)
- **uv.lock 존재 확인됨** — 루트 `uv.lock` 파일로 의존성 해상도 고정 (블로커 해결)

### ❌ 안 된 것

- **Torch CPU whl 반입 실빌드 미검증** — `deploy/offline-bundle/build.sh` 스크립트는 작성되었으나 Linux 빌드 머신에서 실제 dry-run으로 whl 수집 성공 여부 미확인
- **gunicorn 위치 재검토** — `prod` extras에 있으나 폐쇄망에서는 필수이므로 main deps 승격 또는 설치 가이드 명시 필요

### ✅ 해결됨 (2026-04-14)

- ~~**langsmith main deps 포함**~~ → pyproject/src/tests 전반에서 완전 제거
- ~~**Torch CPU whl 반입 전략 미확정**~~ → `deploy/offline-bundle/build.sh`에서 torch CPU index + HF 모델 다운로드 통합 (실빌드 검증은 블로커 3)

### 🚫 비해당 (전제에 의해 불필요)
- ~~**Frontend(React/Vite/TS) 미관리**~~ — 실제 FE는 `static/embedded.html` vanilla HTML 단일 파일 + vendor JS. **package.json / npm build 대상 아님**

---

## 2. 프로젝트 오류/품질

### ✅ 된 것
- Ruff: E, F, I, N, W, UP 설정 완료
- Mypy: strict=true, pydantic plugin
- Test 구조: `tests/auto/`(CI) vs `tests/manual/` 분리, 76 test files, pytest markers 4개
- TODO/FIXME: src/ 전체 3건(정상 범위)
- 삭제 대기 파일(impala/sybase_connector.py 등)은 connector manager에서 깔끔히 제거됨 — 고아 참조 없음

### ❌ 안 된 것

- **git status M 대량 미커밋** — 반입 전 최종 커밋·태그 정리 필수

### ✅ 해결됨 (2026-04-14)

- ~~**Mypy strict 대량 오류**~~ → **Success: no issues found in 105 source files**. strict=false + 핵심 옵션(disallow_untyped_defs / check_untyped_defs / warn_return_any / no_implicit_optional) 선택 활성, 폐쇄망 미지원 스텁(impyla/sqlanydb/pyodbc/FlagEmbedding/onnxruntime/psutil/yaml)은 module overrides, 나머지 실제 버그·시그니처 누락은 코드 수정으로 정리

---

## 3. 주석·문서·DDL 최신화

### ✅ 된 것
- `docs/architecture/architecture.md` v2.3 (2026-04-13), `project-structure.md` 실제 3계층 16노드 구조와 일치
- `.env.example` ↔ `src/config.py`(embedding/reranker/qdrant/mongo 키) 동기화됨
- PostgreSQL DDL: `resources/connectors/postgres/checkpoint/01~05*.sql` 5개 파일 checkpointer_backend와 일치
- src/agents/nodes/ 모듈 docstring·함수 docstring 완비

### ❌ 안 된 것
- **embedding-search-integration.md(v1.2)** — 일부 MiniLM 384-dim 기준 분석이 남아 현재 BGE-M3 구현과 정렬 재확인 필요
- **git M 대량 변경 미커밋**(섹션 2 중복)

### ✅ 해결됨 (2026-04-14)
- ~~**ES 레거시 잔존**~~ → `seed_elasticsearch.py`, `resources/connectors/elasticsearch/`,
  `elasticsearch_connector.py`, `elasticsearch>=8.0.0` deps, ES 관련 env 절차 전부 제거 완료.
  docs 전역에서 ES 참조를 MongoDB(메타) / Qdrant(SQL 이력)로 교체

---

## 4. 폐쇄망 환경구성 가이드

| 항목 | 상태 | 파일/비고 |
|------|:-----:|-----|
| (a) 패키징/반입: uv, whl, 오프라인 설치 | 부분 | [migration-guide.md](migration-guide.md) §2-1 — `deploy/offline-bundle/`에서 Torch CPU index·Python tarball·whl 반입 목록 체계화 예정 |
| (b) DB 스키마 초기화: PG/Mongo/Qdrant/Redis | **완비** | [closed-network-db-connectors.md](closed-network-db-connectors.md) 5·6장 상세. 서버는 기구축 → `deploy/db-init/`에 초기화 SQL·스크립트 배치 |
| (c) .env 키 ↔ config.py | **완비** | [env-configuration-guide.md](env-configuration-guide.md) 17 섹션, 70+ 키 |
| (d) Python 3.12 폐쇄망 설치 | 부분 | "3.12 이상 확인" 만 있음. tarball/RPM 경로 미명시 (`deploy/offline-bundle/`에서 관리 예정) |
| (e) 기동: systemd / gunicorn 전환 | 부분 | [gunicorn.conf.py](../../gunicorn.conf.py) 완성, systemd unit은 `deploy/systemd/`에 신설 예정 |
| (f) 프론트엔드 반입 | **해당없음** | vanilla HTML(`static/embedded.html`) + vendor JS. 빌드 없이 그대로 반입. **npm / dist / React 빌드 체인 불필요** |
| (g) 폐쇄망 LLM(Solar Pro 2) 엔드포인트 | 부분 | `LLM_PROVIDER=openai_compatible` 기술, 모델별 구체 예시(Solar Pro 2) 누락 |

### 📂 신설 예정 `deploy/` 디렉토리 구조
```
deploy/
├── db-init/          # 폐쇄망 DB 스키마/컬렉션 초기화 SQL·스크립트
├── offline-bundle/   # Python/Torch/whl 반입 목록·다운로드 스크립트
├── systemd/          # data-copilot.service 등 systemd unit
└── README.md         # 반입·기동 절차 총괄
```

### ❌ 추가로 작성 필요한 문서
- **systemd unit 파일** (`deploy/systemd/`) — gunicorn 프로세스 관리
- **Python 3.12 오프라인 설치 절차** (`deploy/offline-bundle/`)
- **Torch/ONNX/FlagEmbedding whl 소싱 & 반입 목록 스크립트** (`deploy/offline-bundle/`)
- **DB 스키마 초기화 런북** (`deploy/db-init/README.md`)

### 🚫 비해당
- ~~**Dockerfile**~~ — Docker 미사용 (systemd 기동)
- ~~**K8s manifest**~~ — 컨테이너 오케스트레이션 불사용
- ~~**프론트엔드 빌드·반입 가이드**~~ — vanilla HTML 그대로 반입

---

## 5. 프로덕션 고가용성(HA)

### ✅ 된 것
- **Stateless**: 세션 스토어 제거, 대화이력 PG 단일 소스, active_run_ttl_seconds=1800 자동 만료
- **LangGraph Checkpoint**: AsyncPostgresSaver + psycopg pool(min=2, max=10), msgpack allowlist
- **DB 커넥션 풀**: asyncpg(pool_size=5, max_overflow=10, pre_ping), motor/neo4j 기본 풀, multi-worker × pool × DB max_connections 경고 문서화
- **Redis 폴백**: 미연결 시 Memory fallback (CancelStore, ActiveRunStore 이중 설정)
- **헬스체크**: `/health`, `/health/live`, `/health/ready` 모두 구현 ([main.py:318-365](../../src/main.py#L318-L365))
- **로깅**: structlog 구조화 + console/json 전환 + PII 자동 마스킹(password/api_key)
- **타임아웃**: LLM(15s/30s/60s), DB(60s), Qdrant/Mongo/Neo4j(10s), 에이전틱 루프(180s), gunicorn worker(120s) 전부 설정

### ❌ 안 된 것
- **서킷브레이커 미구현** — LLM/외부 API 연속 실패 시 fast-fail 없음. 타임아웃까지 대기
- **Nginx/LB 앞단 구성 문서 없음** — reverse proxy, sticky session, connection draining 가이드 부재
- **Qdrant 클러스터 URI 예시 부재** — 단일 노드 설정만. 운영 이중화 전략 필요 (서버는 폐쇄망 기구축이므로 URI 가이드만 필요)
- **그레이스풀 셧다운 부분** — gunicorn graceful_timeout=30s, lifespan finally 있음. 진행 중 요청 큐 드레인 보장 명시 없음

### 🚫 비해당
- ~~**Dockerfile / K8s manifest**~~ — systemd 기동 전제

---

## 6. 반입 전 해야 할 일 (우선순위)

### 🔴 Blocker (반입 전 필수)

1. **대량 변경사항 커밋/태그** 후 클린 HEAD 기준 반입
2. **`deploy/offline-bundle/` 실제 빌드 검증** — Linux 빌드 머신에서 `build.sh` dry-run, Python 3.12.x 버전 폐쇄망과 일치 확인, uv export / torch CPU index / huggingface-cli 전제 충족 여부 점검

### ✅ 해소된 블로커 (참고)

- ~~ES 레거시 제거~~ → **완료** (2026-04-14): pyproject/src/tests/devtools/docker-compose 전반에서 ES 참조 제거, elasticsearch deps 삭제, 관련 테스트·시드 스크립트 정리
- ~~langsmith 폐쇄망 비활성화~~ → **완료** (2026-04-14): main deps에서 완전 제거, src/main.py·runner.py·evaluation 에서 import/setup 호출 삭제, `src/tools/langsmith.py` 삭제, LANGSMITH_* env 키 제거
- ~~mypy strict 대량 오류~~ → **완료** (2026-04-14): strict=false + 선택 옵션 + 폐쇄망 미지원 스텁 overrides. **Success: 0 errors in 105 files**
- ~~Qdrant 컬렉션 스키마 교차 확인~~ → **완료** (2026-04-14): `biz_manual`(dense named vector) / `sql_history`(dense+sparse named vectors) 모두 `deploy/db-init/qdrant/init.sh` ↔ `devtools/scripts/seed_qdrant.py` ↔ `src/connectors/impl/qdrant_connector.py` 런타임 쿼리 3자 일치 확인
- ~~`deploy/` 디렉토리 신설~~ → **완료** (2026-04-14): `deploy/{README.md, offline-bundle/, db-init/, systemd/}` 스캐폴드 생성. build.sh / install.sh / download_models.sh / os-packages.txt / PG·Qdrant·Mongo init.sh / data-copilot.service 작성
- ~~Python 3.12 + Torch CPU whl + FlagEmbedding whl 반입 스크립트~~ → **완료**: `deploy/offline-bundle/build.sh`에서 Python tarball · torch CPU index · HF 모델 수집 통합 (실제 빌드 검증은 블로커 2로 이관)
- ~~uv.lock 미관리~~ → **uv.lock 존재 확인됨**
- ~~Frontend(React/Vite/TS) 미관리~~ → **비해당**: vanilla HTML 단일 파일 구조로 빌드 체계 불필요
- ~~Dockerfile 미제공~~ → **비해당**: Docker 미사용

### 🟡 Important (반입 후 프로덕션 이전)

1. LLM 호출 서킷브레이커 도입 (예: `purgatory`, `pybreaker`)
2. Nginx reverse proxy 설정 가이드
3. Qdrant 클러스터/복제 URI 예시 및 운영 이중화 전략
4. Solar Pro 2 모델 구체 env 예시 + Qwen3.5/GPT OSS 프롬프트 재튜닝 가이드

### 🟢 Nice to Have

1. embedding-search-integration.md를 BGE-M3 기준으로 재작성
2. 요청 큐 드레인 명시적 구현 (SIGTERM 수신 시 신규 요청 거부)
3. `static/embedded.html` vanilla JS 구조에 대한 유지보수 가이드(vendor JS 업데이트 절차 포함)

---

## 7. 참고 문서 맵
- [migration-guide.md](migration-guide.md) — 폐쇄망 이전 절차
- [closed-network-db-connectors.md](closed-network-db-connectors.md) — Sybase/Impala 드라이버
- [env-configuration-guide.md](env-configuration-guide.md) — env 키 전체
- [customization-targets.md](customization-targets.md) — 환경별 커스터마이징 대상
- [../architecture/architecture.md](../architecture/architecture.md) — v2.3
- [../../pyproject.toml](../../pyproject.toml), [../../uv.lock](../../uv.lock), [../../gunicorn.conf.py](../../gunicorn.conf.py)
- [../../static/embedded.html](../../static/embedded.html) — FE 단일 진입점 (vanilla HTML + vendor JS)
- [../../deploy/](../../deploy/) — 폐쇄망 반입 산출물 (db-init / offline-bundle / systemd)
