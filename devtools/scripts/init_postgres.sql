-- ============================================================
-- PostgreSQL 초기화: DB 생성 + 스키마 + 테이블 + 사용자 권한
-- docker-entrypoint-initdb.d 에 마운트되어 컨테이너 최초 기동 시 실행
--
-- 스키마 구조:
--   test_db     > biz_schema  — 업무 테이블 (개발/테스트용, 폐쇄망 전환 시 제거)
--   postgres_db > sys_schema  — SQL 실행 이력 (폐쇄망에서도 유지)
-- ============================================================

-- 개발/테스트용 정보계 DB
CREATE DATABASE test_db;

-- 공통 PostgreSQL DB (SQL 이력·체크포인터)
CREATE DATABASE postgres_db;

-- 읽기 전용 사용자 (정보계)
CREATE USER readonly_user WITH PASSWORD 'readonly_pass';
GRANT CONNECT ON DATABASE test_db TO readonly_user;

-- 공통 PostgreSQL 사용자
CREATE USER postgres_user WITH PASSWORD 'postgres_pass';
GRANT CONNECT ON DATABASE postgres_db TO postgres_user;

-- ============================================================
-- 테스트 DB (biz_schema)
-- ============================================================
\connect test_db;

CREATE SCHEMA IF NOT EXISTS biz_schema;

-- ── 지점 ──
CREATE TABLE biz_schema.TB_BRANCH (
    BRCH_CD     VARCHAR(10)  PRIMARY KEY,
    BRCH_NM     VARCHAR(100) NOT NULL,
    REGION_CD   VARCHAR(4),
    REGION_NM   VARCHAR(50)
);

-- ── 고객 등급 코드 마스터 ──
CREATE TABLE biz_schema.TB_CUST_GRD_CD (
    GRD_CD      VARCHAR(10)  PRIMARY KEY,
    GRD_NM      VARCHAR(50)  NOT NULL,
    GRD_DESC    VARCHAR(200)
);

-- ── 고객 기본 정보 (현재 기준, TYPE-1 대상) ──
CREATE TABLE biz_schema.TB_CUST_INFO (
    CIF_NO        VARCHAR(20)   NOT NULL,
    STD_DT        DATE          NOT NULL,
    CUST_NM       VARCHAR(100)  NOT NULL,
    CUST_TYPE_CD  VARCHAR(2)    NOT NULL,   -- 01:개인, 02:기업, 03:개인사업자
    JOIN_DT       DATE,                     -- TYPE-4: TB_CUST_MST.REG_DT 와 동일 의미
    BRCH_CD       VARCHAR(10),
    GENDER_CD     CHAR(1),                  -- M/F
    AGE_GRP_CD    VARCHAR(2),               -- 20,30,40,50,60
    CUST_GRD_CD   VARCHAR(10),              -- TYPE-2: 99, NULL 가능 / TYPE-4: PROFILE.MKT_GRD_CD 와 다름
    TEL_NO        VARCHAR(20),              -- PII — 마스킹 대상
    EMAIL         VARCHAR(100),             -- PII — 마스킹 대상
    PRIMARY KEY (CIF_NO, STD_DT)
);

-- ── 고객 마스터 (이력 포함, TYPE-1 대상) ──
CREATE TABLE biz_schema.TB_CUST_MST (
    CIF_NO        VARCHAR(20)   NOT NULL,
    STD_DT        DATE          NOT NULL,
    CUST_NM       VARCHAR(100)  NOT NULL,
    CUST_TYPE_CD  VARCHAR(2)    NOT NULL,
    REG_DT        DATE,                     -- TYPE-4: TB_CUST_INFO.JOIN_DT 와 동일 의미, 컬럼명 다름
    BRCH_CD       VARCHAR(10),
    GENDER_CD     CHAR(1),
    AGE_GRP_CD    VARCHAR(2),
    CUST_GRD_CD   VARCHAR(10),
    ADDR          VARCHAR(300),             -- PII — 마스킹 대상
    PRIMARY KEY (CIF_NO, STD_DT)
);

-- ── 고객 프로필 (마케팅 전용, TYPE-1 대상) ──
CREATE TABLE biz_schema.TB_CUST_PROFILE (
    CIF_NO        VARCHAR(20)   PRIMARY KEY,
    CUST_NM       VARCHAR(100)  NOT NULL,
    CUST_TYPE_CD  VARCHAR(2)    NOT NULL,
    MKT_GRD_CD    VARCHAR(10),              -- TYPE-4: TB_CUST_INFO.CUST_GRD_CD 와 다른 기준
    BRCH_CD       VARCHAR(10),
    AGE_GRP_CD    VARCHAR(2),
    GENDER_CD     CHAR(1),
    PREF_CHANNEL  VARCHAR(20)               -- 선호 채널
);

-- ── 계좌 잔액 (T+0 당일, TYPE-1 대상) ──
CREATE TABLE biz_schema.TB_ACCT_BAL (
    ACCT_NO       VARCHAR(20)   NOT NULL,
    STD_DT        DATE          NOT NULL,
    CIF_NO        VARCHAR(20)   NOT NULL,
    ACCT_TYPE_CD  VARCHAR(2),               -- TYPE-2: 05, 99 가능
    BAL_AMT       NUMERIC(18,0) NOT NULL,   -- TYPE-4: TB_ACCT_SMRY.TOT_BAL_AMT 와 값 차이
    OPEN_DT       DATE,
    BRCH_CD       VARCHAR(10),
    PROD_CD       VARCHAR(10),
    PROD_NM       VARCHAR(100),
    INT_RATE      NUMERIC(5,4),
    ACCT_STAT_CD  VARCHAR(2)    DEFAULT '01', -- 01:정상, 02:해지, 03:휴면
    PRIMARY KEY (ACCT_NO, STD_DT)
);

-- ── 계좌 요약 (T+1 전일, TYPE-1 대상) ──
CREATE TABLE biz_schema.TB_ACCT_SMRY (
    ACCT_NO       VARCHAR(20)   NOT NULL,
    BASE_DT       DATE          NOT NULL,   -- ⚠️ STD_DT 아님 (TYPE-1)
    CIF_NO        VARCHAR(20)   NOT NULL,
    ACCT_TYPE_CD  VARCHAR(2),
    TOT_BAL_AMT   NUMERIC(18,0) NOT NULL,   -- TYPE-4: TB_ACCT_BAL.BAL_AMT 와 값 차이
    OPEN_DT       DATE,
    BRCH_CD       VARCHAR(10),
    PROD_CD       VARCHAR(10),
    PRIMARY KEY (ACCT_NO, BASE_DT)
);

-- ── 여신(대출) 정보 — 잔액 포함 (TYPE-1 대상) ──
CREATE TABLE biz_schema.TB_LOAN_INFO (
    LOAN_NO       VARCHAR(20)   NOT NULL,
    STD_DT        DATE          NOT NULL,
    CIF_NO        VARCHAR(20)   NOT NULL,
    LOAN_AMT      NUMERIC(18,0) NOT NULL,   -- TYPE-4: 실행금액 (TB_LOAN_MST.APPR_AMT 와 다름)
    LOAN_BAL      NUMERIC(18,0) NOT NULL,
    LOAN_DT       DATE          NOT NULL,
    MTRTY_DT      DATE,
    INT_RATE      NUMERIC(5,2),
    LOAN_TYPE_CD  VARCHAR(2)    NOT NULL,   -- 01:신용, 02:담보, 03:보증
    LOAN_STAT_CD  VARCHAR(4)    DEFAULT '01', -- TYPE-2: 0A 가능
    OVDU_GRD_CD   VARCHAR(2),               -- TYPE-2: F, Z 가능
    OVDU_DAYS     INTEGER       DEFAULT 0,
    OVDU_AMT      NUMERIC(18,0) DEFAULT 0,
    BRCH_CD       VARCHAR(10),
    PRIMARY KEY (LOAN_NO, STD_DT)
);

-- ── 여신 마스터 — 승인 정보 (TYPE-1 대상) ──
CREATE TABLE biz_schema.TB_LOAN_MST (
    LOAN_NO       VARCHAR(20)   PRIMARY KEY,
    CIF_NO        VARCHAR(20)   NOT NULL,
    APPR_AMT      NUMERIC(18,0) NOT NULL,   -- TYPE-4: 승인금액 (TB_LOAN_INFO.LOAN_AMT 와 다름)
    APPR_DT       DATE,
    LOAN_TYPE_CD  VARCHAR(2)    NOT NULL,
    LOAN_PUSE_CD  VARCHAR(4),               -- TYPE-3: 메타 설명 null
    CLTR_TYPE_CD  VARCHAR(4),               -- TYPE-3: 메타 설명 null
    INT_RATE      NUMERIC(5,2),
    MTRTY_DT      DATE,
    BRCH_CD       VARCHAR(10)
);

-- ── 카드 정보 ──
CREATE TABLE biz_schema.TB_CARD_INFO (
    CARD_NO       VARCHAR(20)   NOT NULL,
    STD_DT        DATE          NOT NULL,
    CIF_NO        VARCHAR(20)   NOT NULL,
    CARD_TYPE_CD  VARCHAR(2),               -- TYPE-2: 04 가능
    ISS_DT        DATE,
    EXPR_DT       DATE,
    MON_USE_AMT   NUMERIC(18,0) DEFAULT 0,
    FLG_YN        CHAR(1)       DEFAULT 'Y', -- TYPE-3: 해외사용가능여부인데 설명 불명
    BRCH_CD       VARCHAR(10),
    PRIMARY KEY (CARD_NO, STD_DT)
);

-- ── 거래 이력 (파티션 테이블) ──
CREATE TABLE biz_schema.TB_TRX_HST (
    TRX_ID        VARCHAR(30)   NOT NULL,
    TRX_DT        DATE          NOT NULL,   -- 파티션 키
    ACCT_NO       VARCHAR(20),
    TRX_TM        VARCHAR(6),
    TRX_AMT       NUMERIC(18,0) NOT NULL,
    TRX_TYPE_CD   VARCHAR(4),               -- TYPE-2: 200~299, 999 가능
    BRCH_CD       VARCHAR(10),
    CHNL_CD       VARCHAR(4),               -- 채널: 01:영업점, 02:인뱅, 03:모뱅, 04:ATM
    PRIMARY KEY (TRX_ID, TRX_DT)
) PARTITION BY RANGE (TRX_DT);

-- 파티션: 최근 12개월 (월별)
CREATE TABLE biz_schema.TB_TRX_HST_202504 PARTITION OF biz_schema.TB_TRX_HST
    FOR VALUES FROM ('2025-04-01') TO ('2025-05-01');
CREATE TABLE biz_schema.TB_TRX_HST_202505 PARTITION OF biz_schema.TB_TRX_HST
    FOR VALUES FROM ('2025-05-01') TO ('2025-06-01');
CREATE TABLE biz_schema.TB_TRX_HST_202506 PARTITION OF biz_schema.TB_TRX_HST
    FOR VALUES FROM ('2025-06-01') TO ('2025-07-01');
CREATE TABLE biz_schema.TB_TRX_HST_202507 PARTITION OF biz_schema.TB_TRX_HST
    FOR VALUES FROM ('2025-07-01') TO ('2025-08-01');
CREATE TABLE biz_schema.TB_TRX_HST_202508 PARTITION OF biz_schema.TB_TRX_HST
    FOR VALUES FROM ('2025-08-01') TO ('2025-09-01');
CREATE TABLE biz_schema.TB_TRX_HST_202509 PARTITION OF biz_schema.TB_TRX_HST
    FOR VALUES FROM ('2025-09-01') TO ('2025-10-01');
CREATE TABLE biz_schema.TB_TRX_HST_202510 PARTITION OF biz_schema.TB_TRX_HST
    FOR VALUES FROM ('2025-10-01') TO ('2025-11-01');
CREATE TABLE biz_schema.TB_TRX_HST_202511 PARTITION OF biz_schema.TB_TRX_HST
    FOR VALUES FROM ('2025-11-01') TO ('2025-12-01');
CREATE TABLE biz_schema.TB_TRX_HST_202512 PARTITION OF biz_schema.TB_TRX_HST
    FOR VALUES FROM ('2025-12-01') TO ('2026-01-01');
CREATE TABLE biz_schema.TB_TRX_HST_202601 PARTITION OF biz_schema.TB_TRX_HST
    FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
CREATE TABLE biz_schema.TB_TRX_HST_202602 PARTITION OF biz_schema.TB_TRX_HST
    FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
CREATE TABLE biz_schema.TB_TRX_HST_202603 PARTITION OF biz_schema.TB_TRX_HST
    FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');

-- 인덱스
CREATE INDEX idx_trx_acct ON biz_schema.TB_TRX_HST(ACCT_NO);
CREATE INDEX idx_cust_info_brch ON biz_schema.TB_CUST_INFO(BRCH_CD);
CREATE INDEX idx_loan_info_cif ON biz_schema.TB_LOAN_INFO(CIF_NO);
CREATE INDEX idx_acct_bal_cif ON biz_schema.TB_ACCT_BAL(CIF_NO);

-- readonly_user 에 SELECT 권한 부여
GRANT USAGE ON SCHEMA biz_schema TO readonly_user;
GRANT SELECT ON ALL TABLES IN SCHEMA biz_schema TO readonly_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA biz_schema GRANT SELECT ON TABLES TO readonly_user;

-- ============================================================
-- 공통 PostgreSQL DB (sys_schema + bdptbl)
-- ============================================================
\connect postgres_db;

CREATE SCHEMA IF NOT EXISTS sys_schema;

-- 체크포인터 + Data Copilot 커스텀 테이블 스키마
CREATE SCHEMA IF NOT EXISTS bdptbl;
GRANT USAGE, CREATE ON SCHEMA bdptbl TO postgres_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA bdptbl GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO postgres_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA bdptbl GRANT USAGE, SELECT ON SEQUENCES TO postgres_user;

CREATE TABLE sys_schema.sql_exec_log (
    LOG_ID       SERIAL       PRIMARY KEY,
    NL_QUERY     TEXT         NOT NULL,
    GEN_SQL      TEXT         NOT NULL,
    EXEC_YN      BOOLEAN      DEFAULT TRUE,
    EXEC_RESULT  VARCHAR(20)  DEFAULT 'SUCCESS', -- SUCCESS / FAIL / TIMEOUT
    USER_ID      VARCHAR(50),
    EXEC_MS      INTEGER,
    CREATED_AT   TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    ERROR_MSG    TEXT
);

GRANT USAGE ON SCHEMA sys_schema TO postgres_user;
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA sys_schema TO postgres_user;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA sys_schema TO postgres_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA sys_schema GRANT SELECT, INSERT ON TABLES TO postgres_user;
