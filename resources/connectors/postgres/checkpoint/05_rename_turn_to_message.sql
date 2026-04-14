-- ============================================================================
-- 05. checkpoint_dc_turn_texts → checkpoint_dc_messages 리네이밍
-- ============================================================================
-- 실행 대상: history_db / BDPTBL 스키마 / BDPETL 계정
-- 설계 근거: docs/todo/20260414-message-table-rename.md
-- 멱등성: IF 체크로 재실행 안전
--
-- 적용 순서:
--   기존 DB: 이 스크립트 실행 → 03 신규 정의와 정합
--   신규 DB: 03 스크립트만 실행 (이 스크립트는 불필요, 멱등이므로 실행해도 no-op)
-- ============================================================================

SET search_path TO BDPTBL, public;

-- ──────────────────────────────────────────────────────────────
-- 1. 테이블 리네이밍 (부모 파티션 테이블)
-- ──────────────────────────────────────────────────────────────
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_tables
        WHERE schemaname = 'bdptbl'
          AND tablename = 'checkpoint_dc_turn_texts'
    ) THEN
        ALTER TABLE checkpoint_dc_turn_texts RENAME TO checkpoint_dc_messages;
    END IF;
END $$;

-- ──────────────────────────────────────────────────────────────
-- 2. 컬럼 리네이밍 (파티션 자동 전파)
-- ──────────────────────────────────────────────────────────────
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'bdptbl'
          AND table_name = 'checkpoint_dc_messages'
          AND column_name = 'turn_seq'
    ) THEN
        ALTER TABLE checkpoint_dc_messages RENAME COLUMN turn_seq TO seq;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'bdptbl'
          AND table_name = 'checkpoint_dc_messages'
          AND column_name = 'turn_id'
    ) THEN
        ALTER TABLE checkpoint_dc_messages RENAME COLUMN turn_id TO message_uuid;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'bdptbl'
          AND table_name = 'checkpoint_dc_messages'
          AND column_name = 'turn_type'
    ) THEN
        ALTER TABLE checkpoint_dc_messages RENAME COLUMN turn_type TO message_type;
    END IF;
END $$;

-- ──────────────────────────────────────────────────────────────
-- 3. 인덱스 리네이밍
-- ──────────────────────────────────────────────────────────────
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_turn_texts_turn_id')
        THEN ALTER INDEX idx_turn_texts_turn_id RENAME TO idx_messages_message_uuid; END IF;
    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_turn_texts_thread_created')
        THEN ALTER INDEX idx_turn_texts_thread_created RENAME TO idx_messages_thread_created; END IF;
    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_turn_texts_status_created')
        THEN ALTER INDEX idx_turn_texts_status_created RENAME TO idx_messages_status_created; END IF;
    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_turn_texts_request_id')
        THEN ALTER INDEX idx_turn_texts_request_id RENAME TO idx_messages_request_id; END IF;
    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_turn_texts_liked')
        THEN ALTER INDEX idx_turn_texts_liked RENAME TO idx_messages_liked; END IF;
END $$;

-- ──────────────────────────────────────────────────────────────
-- 4. 파티션 자식 테이블 리네이밍
-- ──────────────────────────────────────────────────────────────
DO $$
DECLARE
    r RECORD;
    new_name TEXT;
BEGIN
    FOR r IN
        SELECT tablename
        FROM pg_tables
        WHERE schemaname = 'bdptbl'
          AND tablename LIKE 'checkpoint_dc_turn_texts_%'
    LOOP
        new_name := replace(r.tablename, 'checkpoint_dc_turn_texts_', 'checkpoint_dc_messages_');
        EXECUTE format('ALTER TABLE %I RENAME TO %I', r.tablename, new_name);
    END LOOP;
END $$;

-- ──────────────────────────────────────────────────────────────
-- 4b. 파티션 자식 PK/상속 인덱스 이름 리네이밍
-- ──────────────────────────────────────────────────────────────
-- Postgres 는 부모 PK/인덱스를 자식 파티션마다 자동 복제하나,
-- 자식 테이블 RENAME TO 해도 PK 인덱스 이름(`..._pkey`)과 상속 로컬 인덱스
-- 이름(`idx_turn_texts_*_YYYYMM`)은 자동 변경되지 않음. 일관성 위해 일괄 rename.
DO $$
DECLARE
    r RECORD;
    new_name TEXT;
BEGIN
    FOR r IN
        SELECT c.relname AS idx_name
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'bdptbl'
          AND c.relkind = 'i'
          AND c.relname LIKE 'checkpoint_dc_turn_texts_%_pkey'
    LOOP
        new_name := replace(r.idx_name, 'checkpoint_dc_turn_texts_', 'checkpoint_dc_messages_');
        EXECUTE format('ALTER INDEX %I RENAME TO %I', r.idx_name, new_name);
    END LOOP;

    FOR r IN
        SELECT c.relname AS idx_name
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'bdptbl'
          AND c.relkind = 'i'
          AND c.relname LIKE 'idx_turn_texts_%'
    LOOP
        new_name := replace(r.idx_name, 'idx_turn_texts_', 'idx_messages_');
        EXECUTE format('ALTER INDEX %I RENAME TO %I', r.idx_name, new_name);
    END LOOP;
END $$;

-- ──────────────────────────────────────────────────────────────
-- 5. pg_partman 설정 업데이트 (pg_partman 사용 시)
-- ──────────────────────────────────────────────────────────────
-- 주의: 04_partman_setup.sql 에서 'BDPTBL.checkpoint_dc_turn_texts' (대문자)로
-- create_parent() 호출했고, pg_partman 은 전달된 문자열을 part_config.parent_table
-- TEXT 컬럼에 정규화 없이 저장하는 버전이 다수임. 따라서 대소문자 무관 비교 필수.
-- plpgsql 은 DO 블록의 정적 SQL 을 첫 실행 시 파싱하므로, partman.part_config
-- 가 존재하지 않는 환경에서 정적 참조 시 파싱 오류가 발생한다. EXECUTE 동적 SQL
-- 로 감싸 IF 분기 안에 들어갔을 때만 파싱되도록 한다.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'partman' AND table_name = 'part_config'
    ) THEN
        EXECUTE $sql$
            UPDATE partman.part_config
            SET parent_table = 'BDPTBL.checkpoint_dc_messages'
            WHERE lower(parent_table) = 'bdptbl.checkpoint_dc_turn_texts'
        $sql$;
    END IF;
END $$;

-- ──────────────────────────────────────────────────────────────
-- 6. 검증
-- ──────────────────────────────────────────────────────────────
-- SELECT tablename FROM pg_tables WHERE schemaname='bdptbl' AND tablename LIKE 'checkpoint_dc_%';
-- SELECT column_name FROM information_schema.columns
--   WHERE table_name='checkpoint_dc_messages' ORDER BY ordinal_position;
-- SELECT indexname FROM pg_indexes WHERE tablename LIKE 'checkpoint_dc_messages%';
