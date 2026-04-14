-- ============================================================================
-- 04. pg_partman 파티션 자동 관리 (선택 사항)
-- ============================================================================
--
-- 실행 대상 DB: history_db
-- 실행 스키마:  BDPTBL
-- 실행 계정:    DBA (슈퍼유저) — CREATE EXTENSION 권한 필요
--
-- 전제:
--   - PostgreSQL에 pg_partman 패키지가 설치되어 있어야 함
--     (RPM: pg_partman_16, DEB: postgresql-16-partman)
--   - 03번 DDL이 먼저 실행되어 checkpoint_dc_messages 테이블이 존재해야 함
--
-- 목적:
--   checkpoint_dc_messages 테이블의 월별 파티션을 자동으로
--   선행 생성하고, 보관 기간이 지난 파티션을 정리한다.
--
-- pg_partman 미설치 시:
--   이 파일을 실행하지 않아도 서비스 운영에 문제 없음.
--   단, 03번의 DO 블록(3개월 선행)이 소진되기 전에
--   수동으로 파티션을 추가해야 한다.
--
-- ============================================================================

SET search_path TO BDPTBL, public;

-- ============================================================================
-- 1. pg_partman 확장 설치
-- ============================================================================
-- partman 스키마에 격리 설치 (BDPTBL 스키마와 분리)
-- ※ 이미 설치되어 있으면 무시

CREATE SCHEMA IF NOT EXISTS partman;
CREATE EXTENSION IF NOT EXISTS pg_partman SCHEMA partman;


-- ============================================================================
-- 2. 파티션 자동 관리 등록
-- ============================================================================
-- p_parent_table: 파티션 부모 테이블 (스키마 포함)
-- p_control:      파티션 키 컬럼 (base_ymd, CHAR(8), 'YYYYMMDD')
-- p_interval:     파티션 간격 (월별)
-- p_type:         파티션 유형 (RANGE)
-- p_premake:      선행 생성 개수 (4개월 앞까지 미리 생성)

SELECT partman.create_parent(
    p_parent_table   := 'BDPTBL.checkpoint_dc_messages',
    p_control        := 'base_ymd',
    p_interval       := 'monthly',
    p_type           := 'range',
    p_premake        := 4
);


-- ============================================================================
-- 3. 보관 정책 설정
-- ============================================================================
-- retention:              12개월 초과 파티션 정리
-- retention_keep_table:   true → DROP 대신 DETACH (데이터 보존, 별도 백업 가능)
-- infinite_time_partitions: true → 미래 파티션 무한 선행 생성

UPDATE partman.part_config
SET retention                = '12 months',
    retention_keep_table     = true,
    infinite_time_partitions = true
WHERE parent_table = 'BDPTBL.checkpoint_dc_messages';


-- ============================================================================
-- 4. 즉시 maintenance 실행
-- ============================================================================
-- 등록 직후 선행 파티션을 즉시 생성
-- 이후에는 pg_cron 또는 OS cron으로 주기적 실행 필요

SELECT partman.run_maintenance('BDPTBL.checkpoint_dc_messages');


-- ============================================================================
-- 5. 주기적 maintenance 스케줄 등록 (pg_cron 사용 시)
-- ============================================================================
-- pg_cron이 설치되어 있으면 아래 주석을 해제하여 실행
-- 매일 03:00에 파티션 자동 생성 + 보관 기간 초과 파티션 DETACH
--
-- CREATE EXTENSION IF NOT EXISTS pg_cron;
-- SELECT cron.schedule(
--     'partman-maintenance-dc-messages',
--     '0 3 * * *',
--     $$SELECT partman.run_maintenance('BDPTBL.checkpoint_dc_messages')$$
-- );
--
-- pg_cron 미사용 시 OS crontab으로 대체:
--   0 3 * * * psql -U BDPETL -d history_db -c "SELECT partman.run_maintenance('BDPTBL.checkpoint_dc_messages')"


-- ============================================================================
-- 검증 쿼리
-- ============================================================================
-- SELECT * FROM partman.part_config WHERE parent_table = 'BDPTBL.checkpoint_dc_messages';
-- SELECT tableowner, tablename FROM pg_tables WHERE schemaname = 'bdptbl' AND tablename LIKE 'checkpoint_dc_messages_%' ORDER BY tablename;
