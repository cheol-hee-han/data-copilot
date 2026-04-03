-- ============================================================================
-- LangGraph Checkpoint PostgreSQL DDL
-- ============================================================================
--
-- 출처: langgraph-checkpoint-postgres 패키지
--       GitHub: github.com/langchain-ai/langgraph/libs/checkpoint-postgres/
--              langgraph/checkpoint/postgres/base.py  (MIGRATIONS 리스트)
--
-- 용도: LangGraph 그래프 실행 상태를 PostgreSQL에 저장/복원하는 체크포인터.
--       노드 경계마다 자동 저장되며, 파이프라인 중단/재개를 지원한다.
--
-- 실행 시점: AsyncPostgresSaver.setup() 호출 시 자동 실행됨.
--           수동 실행이 필요한 경우 이 파일을 사용한다.
--
-- ============================================================================
-- 유의사항
-- ============================================================================
--
-- 1. 실행 순서: 마이그레이션 버전(v0~v9) 순서대로 실행해야 한다.
--    setup()은 checkpoint_migrations 테이블로 이미 적용된 버전을 추적한다.
--
-- 2. CREATE INDEX CONCURRENTLY: 트랜잭션 블록 안에서 실행 불가.
--    psql에서 수동 실행 시 BEGIN/COMMIT 없이 개별 실행해야 한다.
--    (setup()은 autocommit=True로 실행하므로 문제 없음)
--
-- 3. blob 컬럼: msgpack 바이너리로 직렬화된 Python 객체가 ��장된다.
--    SQL로 직접 읽을 수 ���으며, LangGraph API(aget_state 등)로 조회해야 한다.
--
-- 4. 커스텀 타입 역직렬화: checkpointer.py의 _collect_src_types()가
--    allowlist를 등록해야 msgpack → Python 객체 복원이 가능하다.
--    allowlist 누락 시 역직렬화 경고 또는 실패 발생.
--
-- 5. thread_id = session_id: 세션별로 별도 체크포인트 체인이 생성된다.
--    thread_ttl_days 설정으로 오래된 세션 데이터를 정리할 수 있다.
--
-- 6. 폐쇄망 배포 시: langgraph-checkpoint-postgres 패키지(.whl)를
--    오프라인 반입하고, 이 DDL을 DBA가 사전 실행하거나
--    setup() 자동 실행을 허용해야 한다.
--
-- ============================================================================

-- v0: 마이그레이션 버전 관리
CREATE TABLE IF NOT EXISTS checkpoint_migrations (
    v INTEGER PRIMARY KEY
);

-- v1: 체크포인트 메타
-- checkpoint JSONB에는 채널 버전 맵이 저장된다 (실제 state 데이터가 아님).
-- 실제 데이터는 checkpoint_blobs 테이블에 채널별로 분리 저장된다.
CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id            TEXT NOT NULL,
    checkpoint_ns        TEXT NOT NULL DEFAULT '',
    checkpoint_id        TEXT NOT NULL,
    parent_checkpoint_id TEXT,
    type                 TEXT,
    checkpoint           JSONB NOT NULL,
    metadata             JSONB NOT NULL DEFAULT '{}',
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);

-- v2: 채널별 실제 state 데이터 (msgpack 바이너리)
-- 변경되지 않은 채널은 이전 blob을 재참조하므로 전체 복사가 발생하지 않는다.
CREATE TABLE IF NOT EXISTS checkpoint_blobs (
    thread_id     TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    channel       TEXT NOT NULL,
    version       TEXT NOT NULL,
    type          TEXT NOT NULL,
    blob          BYTEA,
    PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
);

-- v3: 노드 실행 중 임시 기록 (pending writes)
-- 노드가 완료되기 전 중간 결과를 기록한다.
-- Task.cancel() 등으로 중단되면 미완성 writes는 다음 실행에서 무시된다.
CREATE TABLE IF NOT EXISTS checkpoint_writes (
    thread_id     TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    task_id       TEXT NOT NULL,
    idx           INTEGER NOT NULL,
    channel       TEXT NOT NULL,
    type          TEXT,
    blob          BYTEA NOT NULL,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);

-- v4: blob nullable 허용 (empty 채널 값 표현)
ALTER TABLE checkpoint_blobs ALTER COLUMN blob DROP NOT NULL;

-- v5: no-op (예약)
-- SELECT 1;

-- v6: checkpoints 인덱스
-- thread_id 단독 조회 성능 개선 (PK는 복합키이므로 별도 인덱스 필요)
CREATE INDEX CONCURRENTLY IF NOT EXISTS checkpoints_thread_id_idx
    ON checkpoints(thread_id);

-- v7: checkpoint_blobs 인덱스
CREATE INDEX CONCURRENTLY IF NOT EXISTS checkpoint_blobs_thread_id_idx
    ON checkpoint_blobs(thread_id);

-- v8: checkpoint_writes 인덱스
CREATE INDEX CONCURRENTLY IF NOT EXISTS checkpoint_writes_thread_id_idx
    ON checkpoint_writes(thread_id);

-- v9: task_path 컬럼 추가 (서브그래프 태스크 경로 추적)
ALTER TABLE checkpoint_writes
    ADD COLUMN IF NOT EXISTS task_path TEXT NOT NULL DEFAULT '';
