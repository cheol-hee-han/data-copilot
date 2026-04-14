-- ============================================================================
-- 05_rollback. checkpoint_dc_messages → checkpoint_dc_turn_texts 롤백
-- ============================================================================
-- 05_rename_turn_to_message.sql 의 역방향 대칭 스크립트.
-- Phase 0 리허설에서 `05 rename → 05 rollback → 05 rename` 왕복 실행으로
-- 멱등·대칭성 검증.
-- ============================================================================

SET search_path TO BDPTBL, public;

-- 1. 부모 테이블 rename (역방향)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_tables WHERE schemaname='bdptbl' AND tablename='checkpoint_dc_messages') THEN
        ALTER TABLE checkpoint_dc_messages RENAME TO checkpoint_dc_turn_texts;
    END IF;
END $$;

-- 2. 컬럼 역방향
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema='bdptbl' AND table_name='checkpoint_dc_turn_texts' AND column_name='seq')
        THEN ALTER TABLE checkpoint_dc_turn_texts RENAME COLUMN seq TO turn_seq; END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema='bdptbl' AND table_name='checkpoint_dc_turn_texts' AND column_name='message_uuid')
        THEN ALTER TABLE checkpoint_dc_turn_texts RENAME COLUMN message_uuid TO turn_id; END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema='bdptbl' AND table_name='checkpoint_dc_turn_texts' AND column_name='message_type')
        THEN ALTER TABLE checkpoint_dc_turn_texts RENAME COLUMN message_type TO turn_type; END IF;
END $$;

-- 3. 인덱스 역방향
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname='idx_messages_message_uuid')
        THEN ALTER INDEX idx_messages_message_uuid RENAME TO idx_turn_texts_turn_id; END IF;
    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname='idx_messages_thread_created')
        THEN ALTER INDEX idx_messages_thread_created RENAME TO idx_turn_texts_thread_created; END IF;
    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname='idx_messages_status_created')
        THEN ALTER INDEX idx_messages_status_created RENAME TO idx_turn_texts_status_created; END IF;
    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname='idx_messages_request_id')
        THEN ALTER INDEX idx_messages_request_id RENAME TO idx_turn_texts_request_id; END IF;
    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname='idx_messages_liked')
        THEN ALTER INDEX idx_messages_liked RENAME TO idx_turn_texts_liked; END IF;
END $$;

-- 4. 파티션 자식 테이블 역방향
DO $$
DECLARE r RECORD; new_name TEXT;
BEGIN
    FOR r IN SELECT tablename FROM pg_tables
             WHERE schemaname='bdptbl' AND tablename LIKE 'checkpoint_dc_messages_%'
    LOOP
        new_name := replace(r.tablename, 'checkpoint_dc_messages_', 'checkpoint_dc_turn_texts_');
        EXECUTE format('ALTER TABLE %I RENAME TO %I', r.tablename, new_name);
    END LOOP;
END $$;

-- 4b. 자식 PK/상속 인덱스 역방향
DO $$
DECLARE r RECORD; new_name TEXT;
BEGIN
    FOR r IN SELECT c.relname AS idx_name
             FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
             WHERE n.nspname='bdptbl' AND c.relkind='i'
               AND c.relname LIKE 'checkpoint_dc_messages_%_pkey'
    LOOP
        new_name := replace(r.idx_name, 'checkpoint_dc_messages_', 'checkpoint_dc_turn_texts_');
        EXECUTE format('ALTER INDEX %I RENAME TO %I', r.idx_name, new_name);
    END LOOP;

    FOR r IN SELECT c.relname AS idx_name
             FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
             WHERE n.nspname='bdptbl' AND c.relkind='i'
               AND c.relname LIKE 'idx_messages_%'
    LOOP
        new_name := replace(r.idx_name, 'idx_messages_', 'idx_turn_texts_');
        EXECUTE format('ALTER INDEX %I RENAME TO %I', r.idx_name, new_name);
    END LOOP;
END $$;

-- 5. partman 역방향 (대소문자 무관)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name='partman')
       AND EXISTS (SELECT 1 FROM partman.part_config
                   WHERE lower(parent_table)='bdptbl.checkpoint_dc_messages') THEN
        UPDATE partman.part_config
        SET parent_table='BDPTBL.checkpoint_dc_turn_texts'
        WHERE lower(parent_table)='bdptbl.checkpoint_dc_messages';
    END IF;
END $$;
