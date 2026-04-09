-- ============================================================================
-- 01. BDPTBL 스키마 생성 + BDPETL 계정 권한 설정
-- ============================================================================
--
-- 실행 대상 DB: history_db
-- 실행 계정:    DBA (슈퍼유저) 또는 CREATE 권한이 있는 계정
--
-- 목적:
--   Data Copilot의 체크포인터 + 대화이력 테이블이 사용할 스키마를 생성하고,
--   애플리케이션 접속 계정(BDPETL)에 필요한 권한을 부여한다.
--
-- 스키마 구조:
--   history_db
--     └── BDPTBL (스키마)
--           ├── checkpoints             -- LangGraph 체크포인터
--           ├── checkpoint_blobs        -- LangGraph 상태 데이터 (msgpack)
--           ├── checkpoint_writes       -- LangGraph 임시 기록
--           ├── checkpoint_migrations   -- LangGraph 마이그레이션 버전
--           ├── checkpoint_dc_turn_texts      -- 대화 이력 (파티션)
--           └── checkpoint_dc_session_index   -- 세션 인덱스
--
-- 애플리케이션 연결 시 search_path:
--   options="-c search_path=BDPTBL,public"
--   → SQL에서 스키마 접두어 없이 테이블명만으로 접근 가능
--
-- ============================================================================

-- 스키마 생성 (이미 존재하면 무시)
CREATE SCHEMA IF NOT EXISTS BDPTBL;

-- BDPETL 계정에 BDPTBL 스키마 전체 권한 부여
-- ※ 테이블 권한 제한 없음 (SELECT/INSERT/UPDATE/DELETE + DDL)
GRANT ALL PRIVILEGES ON SCHEMA BDPTBL TO BDPETL;

-- 향후 BDPTBL 스키마에 생성되는 모든 테이블에 대해 자동 권한 부여
-- (02, 03번 DDL에서 생성하는 테이블에 적용)
ALTER DEFAULT PRIVILEGES IN SCHEMA BDPTBL
    GRANT ALL PRIVILEGES ON TABLES TO BDPETL;

-- 시퀀스 권한 (gen_random_uuid 등 내장 함수는 시퀀스 불필요하나, 향후 확장 대비)
ALTER DEFAULT PRIVILEGES IN SCHEMA BDPTBL
    GRANT ALL PRIVILEGES ON SEQUENCES TO BDPETL;

-- 함수 실행 권한 (mask_pii 등 사용자 정의 함수)
ALTER DEFAULT PRIVILEGES IN SCHEMA BDPTBL
    GRANT EXECUTE ON FUNCTIONS TO BDPETL;

-- ============================================================================
-- 검증 쿼리 (실행 후 확인용)
-- ============================================================================
-- SELECT nspname FROM pg_namespace WHERE nspname = 'bdptbl';
-- SELECT has_schema_privilege('BDPETL', 'BDPTBL', 'USAGE');
-- SELECT has_schema_privilege('BDPETL', 'BDPTBL', 'CREATE');
