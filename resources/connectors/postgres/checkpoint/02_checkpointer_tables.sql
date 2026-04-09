-- ============================================================================
-- 02. LangGraph 체크포인터 테이블
-- ============================================================================
--
-- 실행 대상 DB: postgres
-- 실행 스키마:  BDPTBL
-- 실행 계정:    BDPETL (01번에서 권한 부여 완료)
--
-- 목적:
--   LangGraph 그래프 실행 상태를 PostgreSQL에 영속화하는 체크포인터 테이블.
--   노드 경계마다 자동 저장되며, 다중턴 대화와 파이프라인 중단/재개를 지원한다.
--
-- 출처:
--   langgraph-checkpoint-postgres 패키지 (v2.x)
--   GitHub: github.com/langchain-ai/langgraph/libs/checkpoint-postgres/
--           langgraph/checkpoint/postgres/base.py (MIGRATIONS 리스트)
--
-- 참고:
--   - AsyncPostgresSaver.setup() 호출 시에도 자동 실행됨
--   - 폐쇄망에서 DBA가 사전에 수동 실행하는 경우 이 파일 사용
--   - 마이그레이션 버전(v0~v9) 순서대로 실행해야 함
--   - CREATE INDEX CONCURRENTLY는 트랜잭션 블록 안에서 실행 불가
--     → psql에서 수동 실행 시 BEGIN/COMMIT 없이 개별 실행
--     → setup()은 autocommit=True로 실행하므로 문제 없음
--
-- ============================================================================

-- search_path 설정 — 이후 모든 DDL이 BDPTBL 스키마에 생성됨
SET search_path TO BDPTBL, public;

-- ============================================================================
-- v0: 마이그레이션 버전 관리
-- ============================================================================
-- setup()이 이미 적용된 버전을 추적하는 테이블.
-- 수동 실행 후 setup()을 호출하면 중복 실행을 건너뛴다.

CREATE TABLE IF NOT EXISTS checkpoint_migrations (
    v INTEGER PRIMARY KEY
);

-- ============================================================================
-- v1: 체크포인트 메타
-- ============================================================================
-- checkpoint JSONB: 채널 버전 맵 저장 (실제 state 데이터가 아님)
-- 실제 데이터는 checkpoint_blobs에 채널별로 분리 저장됨
-- thread_id = session_id: 세션별로 별도 체크포인트 체인 생성

CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id            TEXT NOT NULL,       -- 세션 식별자 (= session_id)
    checkpoint_ns        TEXT NOT NULL DEFAULT '',  -- 서브그래프 네임스페이스
    checkpoint_id        TEXT NOT NULL,       -- 체크포인트 고유 ID
    parent_checkpoint_id TEXT,                -- 이전 체크포인트 (체인 구성)
    type                 TEXT,                -- 직렬화 타입
    checkpoint           JSONB NOT NULL,      -- 채널 버전 맵
    metadata             JSONB NOT NULL DEFAULT '{}',  -- 노드명, 단계 등 메타
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);

-- ============================================================================
-- v2: 채널별 실제 state 데이터 (msgpack 바이너리)
-- ============================================================================
-- 변경되지 않은 채널은 이전 blob을 재참조 → 전체 복사 방지
-- blob 컬럼: msgpack 직렬화된 Python 객체
--   → SQL로 직접 읽을 수 없으며, LangGraph API(aget_state)로 조회
--   → checkpointer.py의 _collect_src_types()가 allowlist 등록 필요

CREATE TABLE IF NOT EXISTS checkpoint_blobs (
    thread_id     TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    channel       TEXT NOT NULL,       -- state 필드명 (예: "explored_tables")
    version       TEXT NOT NULL,       -- 채널 버전
    type          TEXT NOT NULL,       -- 직렬화 타입
    blob          BYTEA,               -- v4에서 nullable 허용 (empty 채널 값)
    PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
);

-- ============================================================================
-- v3: 노드 실행 중 임시 기록 (pending writes)
-- ============================================================================
-- 노드 완료 전 중간 결과 기록
-- Task.cancel() 등으로 중단되면 미완성 writes는 다음 실행에서 무시

CREATE TABLE IF NOT EXISTS checkpoint_writes (
    thread_id     TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    task_id       TEXT NOT NULL,
    idx           INTEGER NOT NULL,
    channel       TEXT NOT NULL,
    type          TEXT,
    blob          BYTEA NOT NULL,
    task_path     TEXT NOT NULL DEFAULT '',   -- v9: 서브그래프 태스크 경로 추적
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);

-- ============================================================================
-- 마이그레이션 버전 등록
-- ============================================================================
-- setup() 호출 시 이미 적용된 버전으로 인식하여 중복 실행 방지
-- v4(blob nullable)는 위에서 이미 반영, v5는 no-op

INSERT INTO checkpoint_migrations (v)
VALUES (1), (2), (3), (4), (5), (6), (7), (8), (9)
ON CONFLICT (v) DO NOTHING;

-- ============================================================================
-- 인덱스 (v6~v8)
-- ============================================================================
-- PK는 복합키이므로 thread_id 단독 조회 성능 개선을 위해 별도 인덱스 필요
-- ※ CONCURRENTLY는 트랜잭션 블록 안에서 실행 불가
--   psql -f 로 실행하면 트랜잭션 밖이므로 문제 없음

CREATE INDEX CONCURRENTLY IF NOT EXISTS checkpoints_thread_id_idx
    ON checkpoints(thread_id);

CREATE INDEX CONCURRENTLY IF NOT EXISTS checkpoint_blobs_thread_id_idx
    ON checkpoint_blobs(thread_id);

CREATE INDEX CONCURRENTLY IF NOT EXISTS checkpoint_writes_thread_id_idx
    ON checkpoint_writes(thread_id);

-- ============================================================================
-- 검증 쿼리
-- ============================================================================
-- SELECT tablename FROM pg_tables WHERE schemaname = 'bdptbl';
-- SELECT * FROM checkpoint_migrations ORDER BY v;
