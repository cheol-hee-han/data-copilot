-- ============================================================================
-- 03. Data Copilot 커스텀 테이블
-- ============================================================================
--
-- 실행 대상 DB: history_db
-- 실행 스키마:  BDPTBL
-- 실행 계정:    BDPETL
--
-- 목적:
--   LangGraph 체크포인터가 파이프라인 상태의 "단일 진실 공급원"이라면,
--   이 테이블은 UI 복원 / LLM 맥락 전달 / 감사 조회를 위한 "경량 보조 저장소"이다.
--
--   체크포인터의 get_state_history()는 체크포인트당 500KB~1.2MB로
--   UI 대화 목록 로딩에 과도한 I/O가 발생한다.
--   메시지별 TEXT 기반 경량 테이블로 이를 해결한다.
--
-- 테이블 목록:
--   1. checkpoint_dc_messages        — 메시지별 대화 이력 (월별 파티션)
--   2. checkpoint_dc_session_index   — 사용자별 세션 목록
--
-- 네이밍 규칙:
--   checkpoint_dc_ prefix
--   → "checkpoint" 패밀리에 속하면서 "_dc_"로 Data Copilot 사용자 정의임을 표시
--
-- 부속 함수:
--   mask_pii() — 감사 로그 조회 시 PII(개인식별정보) 마스킹
--
-- ============================================================================

SET search_path TO BDPTBL, public;

-- ============================================================================
-- 1. PII 마스킹 함수
-- ============================================================================
-- 용도: 감사 로그에서 대화 내용 조회 시 개인정보를 마스킹
-- 사용 예: SELECT mask_pii(content) FROM checkpoint_dc_messages WHERE ...
--
-- 마스킹 대상:
--   - 주민등록번호 (123456-1234567 → ***-*******)
--   - 계좌번호     (123-45-678901 → ***-**-****)
--   - 카드번호     (1234-5678-9012-3456 → ****-****-****-****)
--   - 전화번호     (010-1234-5678 → 010-****-****)

CREATE OR REPLACE FUNCTION mask_pii(text_input TEXT)
RETURNS TEXT AS $$
BEGIN
    RETURN regexp_replace(
        regexp_replace(
            regexp_replace(
                regexp_replace(text_input,
                    '\d{6}-[1-4]\d{6}', '***-*******', 'g'),
                '\d{3,6}-\d{2,6}-\d{4,8}', '***-**-****', 'g'),
            '\d{4}-\d{4}-\d{4}-\d{4}', '****-****-****-****', 'g'),
        '01[0-9]-\d{3,4}-\d{4}', '010-****-****', 'g');
END;
$$ LANGUAGE plpgsql IMMUTABLE;


-- ============================================================================
-- 2. 대화 이력 테이블 (월별 파티션)
-- ============================================================================
-- 파티션 키: base_ymd (CHAR(8), 'YYYYMMDD')
-- 파티션 단위: 월별 (YYYYMM01 ~ 다음달 01)
--
-- seq 채번:
--   INSERT 서브쿼리로 DB 레벨 원자적 채번 (MAX(seq) + 1)
--   SELECT + INSERT 분리 시 race condition 가능 → 단일 SQL로 해결
--
-- metadata (JSONB):
--   SVG 차트, trace_log, 분석 인사이트, SQL 실행 결과 등
--   UI에서 Tier-2 로딩으로 개별 요청 (목록에서는 has_metadata 플래그만 반환)

CREATE TABLE IF NOT EXISTS checkpoint_dc_messages (
    -- ── 식별 ──
    thread_id     TEXT NOT NULL,                -- checkpointer thread_id (= session_id)
    seq           SMALLINT NOT NULL,            -- 세션 내 순번 (1부터 시작, 원자적 채번)
    message_uuid  UUID NOT NULL DEFAULT gen_random_uuid(),  -- 외부 참조용 전역 유일 ID

    -- ── 대화 내용 ──
    role          TEXT NOT NULL                 -- 발화자
                  CHECK (role IN ('user', 'assistant')),
    content       TEXT NOT NULL,                -- 사용자 입력 또는 포맷팅된 응답 전문

    -- ── 감사 (5W: Where) ──
    client_ip     INET,                         -- 접속 IP
    user_agent    TEXT,                          -- 브라우저/클라이언트 정보

    -- ── 대화 분류 ──
    message_type  TEXT NOT NULL DEFAULT 'normal' -- 메시지 유형
                  CHECK (message_type IN ('normal', 'clarification', 'error')),
    intent        TEXT,                          -- IntentType (EXTRACT, AGGREGATE 등)

    -- ── 운영 메트릭 ──
    token_count   INT,                          -- LLM 토큰 사용량 (입력+출력)
    latency_ms    INT,                          -- 해당 메시지 전체 처리 소요 시간 (ms)

    -- ── 이슈 트래킹 ──
    request_id    TEXT,                          -- 서버 로그 교차 조회용 요청 ID
    status        TEXT NOT NULL DEFAULT 'success' -- 메시지 처리 결과
                  CHECK (status IN ('success', 'failure', 'cancelled', 'timeout')),
    error_type    TEXT,                          -- 에러 분류 코드
    error_message TEXT,                          -- 간략 에러 메시지 (PII 제외)
    exit_node     TEXT,                          -- 마지막 실행 노드명
    model_id      TEXT,                          -- 사용된 LLM 모델 ID
    trace_id      TEXT,                          -- LangSmith run_id (트레이싱)

    -- ── UI 사용자 액션 (사후 UPDATE) ──
    is_liked      BOOLEAN,                      -- NULL=미평가, true=좋아요, false=싫어요
    liked_at      TIMESTAMPTZ,                  -- 평가 시각
    feedback      TEXT,                          -- 피드백 사유 (자유 텍스트 또는 카테고리)
    is_downloaded BOOLEAN NOT NULL DEFAULT false, -- 결과 다운로드 여부
    downloaded_at TIMESTAMPTZ,                  -- 최초 다운로드 시각

    -- ── SQL 이력 (감사·재실행·이력 검색용) ──
    executed_sql    TEXT,                        -- 검증 완료된 최종 실행 SQL (validated_sql)
    sql_explanation TEXT,                        -- LLM이 생성한 SQL 1줄 요약 설명
    target_db       TEXT,                        -- 실행 대상 DB 식별자 (postgres, sybase_iq, impala 등)

    -- ── UI 복원 + 확장용 ──
    metadata      JSONB DEFAULT '{}',           -- SVG, trace_log, insight, sql_result 등

    -- ── 시간 ──
    base_ymd      CHAR(8) NOT NULL DEFAULT to_char(now(), 'YYYYMMDD'),  -- 파티션 키
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (thread_id, seq, base_ymd)
) PARTITION BY RANGE (base_ymd);


-- 초기 파티션: 현재 월 + 3개월 선행 생성
-- pg_partman 설정 시(04번) 자동 관리되므로 이 블록은 최초 1회만 의미 있음
-- pg_partman 미사용 환경에서는 이 블록을 주기적으로 수동 실행하거나
-- 배치 스크립트로 선행 파티션을 생성해야 함
DO $$
DECLARE
    m INT;
    ym TEXT;
    next_ym TEXT;
    tbl TEXT;
BEGIN
    FOR m IN 0..3 LOOP
        ym := to_char(now() + (m || ' months')::interval, 'YYYYMM');
        next_ym := to_char(now() + ((m + 1) || ' months')::interval, 'YYYYMM');
        tbl := 'checkpoint_dc_messages_' || ym;
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS %I PARTITION OF checkpoint_dc_messages FOR VALUES FROM (%L) TO (%L)',
            tbl, ym || '01', next_ym || '01'
        );
    END LOOP;
END $$;


-- 인덱스 (파티션 테이블에 자동 전파)
CREATE INDEX IF NOT EXISTS idx_messages_message_uuid
    ON checkpoint_dc_messages (message_uuid);             -- 개별 메시지 조회 (Tier-2 메타데이터)
CREATE INDEX IF NOT EXISTS idx_messages_thread_created
    ON checkpoint_dc_messages (thread_id, created_at);    -- 세션별 대화 이력 조회
CREATE INDEX IF NOT EXISTS idx_messages_status_created
    ON checkpoint_dc_messages (status, created_at DESC);  -- 운영 모니터링 (실패/타임아웃)
CREATE INDEX IF NOT EXISTS idx_messages_request_id
    ON checkpoint_dc_messages (request_id);               -- 서버 로그 교차 조회
CREATE INDEX IF NOT EXISTS idx_messages_liked
    ON checkpoint_dc_messages (is_liked)                  -- 사용자 피드백 분석
    WHERE is_liked IS NOT NULL;


-- ============================================================================
-- 3. 사용자별 세션 인덱스
-- ============================================================================
-- UI 사이드바 세션 목록, 세션 제목 편집, 세션 삭제(아카이브)에 사용
-- thread_id = checkpointer의 thread_id (= session_id)
-- is_archived: soft delete — 삭제 후 Undo 복원 지원

CREATE TABLE IF NOT EXISTS checkpoint_dc_session_index (
    thread_id    TEXT PRIMARY KEY,                       -- 세션 식별자
    user_id      TEXT NOT NULL DEFAULT 'anonymous',      -- 사용자 ID
    user_dept    TEXT,                                    -- 사용자 부서
    title        TEXT,                                    -- 세션 제목 (첫 질의 자동 요약)
    is_archived  BOOLEAN NOT NULL DEFAULT false,          -- soft delete 플래그
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),      -- 세션 생성 시각
    last_active  TIMESTAMPTZ NOT NULL DEFAULT now()       -- 마지막 활동 시각 (메시지 저장 시 갱신)
);

-- 세션 목록 조회: 사용자별 최근 활동순, 아카이브 제외
CREATE INDEX IF NOT EXISTS idx_session_index_user_active
    ON checkpoint_dc_session_index (user_id, last_active DESC)
    WHERE is_archived = false;


-- ============================================================================
-- 마이그레이션: executed_sql, sql_explanation 컬럼 추가
-- ============================================================================
-- 기존 테이블에 컬럼이 없으면 추가하고, metadata에서 기존 데이터를 backfill한다.
-- 멱등(idempotent) — 이미 컬럼이 존재하면 무시.

DO $$
BEGIN
    -- executed_sql 컬럼 추가
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'bdptbl'
          AND table_name = 'checkpoint_dc_messages'
          AND column_name = 'executed_sql'
    ) THEN
        ALTER TABLE checkpoint_dc_messages ADD COLUMN executed_sql TEXT;
    END IF;

    -- sql_explanation 컬럼 추가
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'bdptbl'
          AND table_name = 'checkpoint_dc_messages'
          AND column_name = 'sql_explanation'
    ) THEN
        ALTER TABLE checkpoint_dc_messages ADD COLUMN sql_explanation TEXT;
    END IF;

    -- target_db 컬럼 추가
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'bdptbl'
          AND table_name = 'checkpoint_dc_messages'
          AND column_name = 'target_db'
    ) THEN
        ALTER TABLE checkpoint_dc_messages ADD COLUMN target_db TEXT;
    END IF;
END $$;

-- 기존 데이터 backfill: metadata->'executed_sql' → executed_sql 컬럼
UPDATE checkpoint_dc_messages
SET executed_sql = metadata->>'executed_sql'
WHERE executed_sql IS NULL
  AND metadata->>'executed_sql' IS NOT NULL;


-- ============================================================================
-- 검증 쿼리
-- ============================================================================
-- SELECT tablename FROM pg_tables WHERE schemaname = 'bdptbl' AND tablename LIKE 'checkpoint_dc_%';
-- SELECT * FROM checkpoint_dc_session_index LIMIT 1;
-- SELECT mask_pii('주민번호 850101-1234567 계좌 123-45-6789012');
-- SELECT executed_sql, sql_explanation FROM checkpoint_dc_messages WHERE executed_sql IS NOT NULL LIMIT 5;
