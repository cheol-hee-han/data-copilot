"""biz_schema 전체 테이블 DDL 생성.

test-data-requirements.md 섹션 4의 테이블 카탈로그를 파싱하여
535개 전체 테이블의 CREATE TABLE 문을 생성·실행한다.

★ 테이블(init_postgres.sql에 이미 정의됨)은 건너뛰고,
나머지 비-★ 테이블은 PK + 업무 컬럼으로 자동 생성한다.

사용법:
    docker exec dc-postgres sh -c 'python3 /standalone/scripts/generate_all_ddl.py'
"""
from __future__ import annotations

import os
import re

import psycopg2  # type: ignore[import-untyped]

CONNINFO = (
    f"host={os.getenv('INFO_DB_HOST', 'localhost')} "
    f"port={os.getenv('INFO_DB_PORT', '5432')} "
    f"dbname={os.getenv('INFO_DB_NAME', 'info_db')} "
    f"user={os.getenv('PG_SEED_USER', 'postgres')} "
    f"password={os.getenv('PG_SEED_PASSWORD', 'postgres')}"
)

SCHEMA = "biz_schema"

# ★ 테이블: init_postgres.sql에서 이미 생성됨 — 건너뛴다
STAR_TABLES = {
    "TB_BRANCH", "TB_CUST_GRD_CD",
    "TB_CUST_INFO", "TB_CUST_MST", "TB_CUST_PROFILE",
    "TB_ACCT_BAL", "TB_ACCT_SMRY",
    "TB_LOAN_INFO", "TB_LOAN_MST",
    "TB_CARD_INFO",
    "TB_TRX_HST",
    # 파티션 자식 테이블
    "TB_TRX_HST_202504", "TB_TRX_HST_202505", "TB_TRX_HST_202506",
    "TB_TRX_HST_202507", "TB_TRX_HST_202508", "TB_TRX_HST_202509",
    "TB_TRX_HST_202510", "TB_TRX_HST_202511", "TB_TRX_HST_202512",
    "TB_TRX_HST_202601", "TB_TRX_HST_202602", "TB_TRX_HST_202603",
}

# PK 컬럼명 → 데이터 타입 매핑
PK_TYPE_MAP = {
    "SEQ": "SERIAL",
    "LOG_SEQ": "SERIAL",
    "ERR_SEQ": "SERIAL",
    "CALL_ID": "SERIAL",
    "CMPL_ID": "SERIAL",
    "MERGE_SEQ": "SERIAL",
    "LEAD_ID": "SERIAL",
    "ALERT_ID": "SERIAL",
    "CASE_NO": "SERIAL",
    "EVENT_NO": "SERIAL",
    "REVIEW_NO": "SERIAL",
    "DEAL_NO": "SERIAL",
    "SWAP_NO": "SERIAL",
    "OPT_NO": "SERIAL",
    "FWD_NO": "SERIAL",
    "SETL_NO": "SERIAL",
    "TRX_SEQ": "SERIAL",
    "SWITCH_SEQ": "SERIAL",
    "REDEEM_SEQ": "SERIAL",
    "CHG_SEQ": "SERIAL",
    "TRANSFER_SEQ": "SERIAL",
    "PAYOUT_SEQ": "SERIAL",
    "WITHDRAW_SEQ": "SERIAL",
    "COUNSEL_SEQ": "SERIAL",
    "CONSULT_SEQ": "SERIAL",
    "SALE_NO": "SERIAL",
    "CLAIM_NO": "SERIAL",
    "COMPLAINT_NO": "SERIAL",
    "SCREEN_SEQ": "SERIAL",
    "BLOCK_SEQ": "SERIAL",
    "AUTH_SEQ": "SERIAL",
    "PUSH_SEQ": "SERIAL",
    "LOG_SEQ": "SERIAL",
    "SMS_SEQ": "SERIAL",
    "EMAIL_SEQ": "SERIAL",
    "KAKAO_SEQ": "SERIAL",
    "NOTI_SEQ": "SERIAL",
    "SCRAP_SEQ": "SERIAL",
    "REFERRAL_SEQ": "SERIAL",
    "DISPUTE_NO": "SERIAL",
    "BATCH_NO": "SERIAL",
    "ESCROW_NO": "SERIAL",
    "REPORT_NO": "SERIAL",
    "COI_SEQ": "SERIAL",
    "BREACH_NO": "SERIAL",
    "ADJ_NO": "SERIAL",
    "APPRAISAL_NO": "SERIAL",
    "FRAUD_ID": "SERIAL",
    "CHK_NO": "SERIAL",
    "ISSUE_NO": "SERIAL",
    "WATCH_ID": "SERIAL",
    "SANCTION_ID": "SERIAL",
    "FINDING_SEQ": "INTEGER",
    "FU_SEQ": "INTEGER",
}

# 날짜 관련 PK 컬럼
DATE_COLS = {
    "STD_DT", "BASE_DT", "STAT_DT", "EFF_DT", "CHG_DT", "VISIT_DT",
    "EVENT_DT", "AGREE_DT", "SCORE_DT", "KYC_DT", "CONTRIB_DT",
    "EVAL_DT", "CALC_DT", "EXEC_DT", "RPAY_DT", "OVDU_START_DT",
    "CANCEL_DT", "RENEW_DT", "REISSUE_DT", "CHK_DT", "REPORT_DT",
    "ISSUE_DT", "PAY_DT", "CLEAR_DT", "DISC_DT", "CONTACT_DT",
    "REBAL_DT", "NAV_DT", "DIV_DT", "LOGIN_DT", "CALL_DT",
    "BAL_DT", "TOPUP_DT", "WDRW_DT", "TRANSFER_DT", "PAYOUT_DT",
    "RESTRUCTURE_DT", "WRITEOFF_DT", "RECOVERY_DT", "EXT_DT",
    "PREPAY_DT", "WARNING_DT", "WATCH_DT", "FCAST_DT", "APPR_DT",
    "OPEN_DT", "ISS_DT", "HLDY_DT", "COUNSEL_DT", "SIGN_DT",
    "SURVEY_DT",
}

# 연월 관련 PK 컬럼
YM_COLS = {"BASE_YM", "BILL_YM", "STMT_YM", "FEE_YR", "FIN_YR",
           "TAX_YR", "BUDGET_YR", "TARGET_YR", "STD_YR"}


def _col_type(col_name: str) -> str:
    """PK 컬럼명으로 데이터 타입 추론."""
    if col_name in PK_TYPE_MAP:
        return PK_TYPE_MAP[col_name]
    if col_name in DATE_COLS:
        return "DATE"
    if col_name in YM_COLS:
        return "VARCHAR(6)"
    if col_name.endswith("_SEQ"):
        return "INTEGER"
    if col_name.endswith("_AMT"):
        return "NUMERIC(18,0)"
    if col_name.endswith("_RATE") or col_name.endswith("_RT"):
        return "NUMERIC(10,4)"
    if col_name.endswith("_DT"):
        return "DATE"
    if col_name.endswith("_YN"):
        return "CHAR(1)"
    if col_name.endswith("_CNT"):
        return "INTEGER"
    # 대부분의 코드/ID 컬럼
    return "VARCHAR(50)"


def _extra_columns(table_name: str, pk_cols: list[str]) -> list[str]:
    """테이블명과 PK를 기반으로 공통 업무 컬럼 SQL 조각 생성."""
    cols = []
    pk_set = set(pk_cols)

    # 날짜 컬럼: STD_DT가 PK에 없으면 추가
    if "STD_DT" not in pk_set and "BASE_DT" not in pk_set:
        if any(k in table_name for k in ("_HST", "_LOG", "_STAT", "_SMRY")):
            cols.append("    REG_DT DATE DEFAULT CURRENT_DATE")

    # CIF_NO가 PK에 없지만 고객 관련 테이블이면 추가
    if "CIF_NO" not in pk_set and any(
        k in table_name for k in ("_CUST", "CUST_", "_MEMBER", "_HOUSEHOLD")
    ):
        cols.append("    CIF_NO VARCHAR(20)")

    # BRCH_CD: 지점 관련
    if "BRCH_CD" not in pk_set and any(
        k in table_name for k in ("_BRCH", "BRCH_", "_BRANCH")
    ):
        cols.append("    BRCH_CD VARCHAR(10)")

    # 금액 컬럼
    if any(k in table_name for k in ("_BAL", "_AMT", "_PAY", "_FEE", "_INCOME",
                                      "_COST", "_PROVISION", "_LOSS", "_PROFIT")):
        if not any(c.endswith("_AMT") for c in pk_cols):
            cols.append("    AMT NUMERIC(18,0)")

    # 상태 코드
    if any(k in table_name for k in ("_INFO", "_MST", "_DTL", "_CONTRACT")):
        cols.append("    STAT_CD VARCHAR(4)")

    # 공통: 설명/비고
    cols.append("    RMRK VARCHAR(500)")
    cols.append("    CRET_DT TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    cols.append("    UPDT_DT TIMESTAMP DEFAULT CURRENT_TIMESTAMP")

    # 테이블에 따라 추가 컬럼 (5~15개 목표 충족)
    if len(cols) + len(pk_cols) < 6:
        cols.append("    TYPE_CD VARCHAR(4)")
        cols.append("    USE_YN CHAR(1) DEFAULT 'Y'")
        cols.append("    NM VARCHAR(200)")

    return cols


def parse_tables_from_requirements(filepath: str) -> list[tuple[str, list[str]]]:
    """requirements.md에서 테이블명과 PK 컬럼 목록을 파싱."""
    tables = []
    # 패턴: | 숫자 | [★] `TB_XXX` | 한글명 | PK 예시 | 비고 |
    pattern = re.compile(
        r'\|\s*\d+\s*\|\s*(?:★\s*)?`(TB_\w+)`\s*\|\s*[^|]+\|\s*`([^`]+)`\s*\|'
    )

    with open(filepath, encoding="utf-8") as f:
        for line in f:
            m = pattern.search(line)
            if m:
                tbl_name = m.group(1)
                pk_str = m.group(2)
                pk_cols = [c.strip() for c in pk_str.split("+")]
                tables.append((tbl_name, pk_cols))

    return tables


def generate_ddl(table_name: str, pk_cols: list[str]) -> str:
    """단일 테이블의 CREATE TABLE IF NOT EXISTS 문 생성."""
    lines = [f"CREATE TABLE IF NOT EXISTS {SCHEMA}.{table_name} ("]

    # PK 컬럼 정의
    for col in pk_cols:
        ctype = _col_type(col)
        not_null = " NOT NULL" if ctype != "SERIAL" else ""
        lines.append(f"    {col} {ctype}{not_null},")

    # 추가 업무 컬럼
    extras = _extra_columns(table_name, pk_cols)
    for extra in extras:
        lines.append(f"{extra},")

    # PRIMARY KEY
    # SERIAL 타입은 PK에서 이름만 사용
    pk_col_names = [c for c in pk_cols]
    lines.append(f"    PRIMARY KEY ({', '.join(pk_col_names)})")
    lines.append(");")

    return "\n".join(lines)


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    req_path = os.path.join(
        script_dir, "..", "docs", "agent-guides", "test-data-requirements.md"
    )

    if not os.path.exists(req_path):
        # 컨테이너 내부에서 실행 시 경로 조정
        req_path = "/scripts/../docs/agent-guides/test-data-requirements.md"
        if not os.path.exists(req_path):
            print("ERROR: test-data-requirements.md not found")
            return

    all_tables = parse_tables_from_requirements(req_path)
    print(f"요구사항에서 파싱한 테이블 수: {len(all_tables)}")

    # 기존 ★ 테이블 제외
    new_tables = [
        (name, pks) for name, pks in all_tables
        if name not in STAR_TABLES
    ]
    print(f"DDL 생성 대상 (비-★): {len(new_tables)}")

    conn = psycopg2.connect(CONNINFO)
    conn.autocommit = True
    cur = conn.cursor()

    created = 0
    skipped = 0
    errors = []

    for tbl_name, pk_cols in new_tables:
        ddl = generate_ddl(tbl_name, pk_cols)
        try:
            cur.execute(ddl)
            created += 1
        except Exception as e:
            err_msg = str(e).strip()
            # 이미 존재하면 무시 (IF NOT EXISTS 처리)
            if "already exists" in err_msg:
                skipped += 1
            else:
                errors.append((tbl_name, err_msg))
                conn.rollback() if not conn.autocommit else None

    # readonly_user 권한 부여
    try:
        cur.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA {SCHEMA} TO readonly_user;")
    except Exception:
        pass

    cur.close()
    conn.close()

    # 결과
    print(f"\n생성 완료: {created}개")
    print(f"이미 존재 (건너뜀): {skipped}개")
    print(f"★ 테이블 (건너뜀): {len(STAR_TABLES)}개")
    if errors:
        print(f"\n오류 {len(errors)}건:")
        for tbl, err in errors[:10]:
            print(f"  {tbl}: {err[:100]}")

    # 최종 테이블 수 확인
    conn2 = psycopg2.connect(CONNINFO)
    cur2 = conn2.cursor()
    cur2.execute(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema = 'biz_schema' AND table_type = 'BASE TABLE'"
    )
    total = cur2.fetchone()[0]
    print(f"\nbiz_schema 총 테이블 수: {total}")
    cur2.close()
    conn2.close()


if __name__ == "__main__":
    main()
