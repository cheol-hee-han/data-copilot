# -*- coding: utf-8 -*-
"""PostgreSQL 테스트 데이터 시딩 (전면 재작성).

ADWOWN (정보계 DB) + sys_schema (이력 DB)에
572개 테이블 DDL 생성 + ★ 22개 핵심 테이블 데이터 적재.

사용법:
    pip install psycopg2-binary python-dotenv
    python standalone/scripts/seed_postgres.py

요구사항 문서: docs/agent-guides/test-data-requirements.md
"""

from __future__ import annotations

import os
import random
import re
from datetime import date, timedelta
from pathlib import Path

import psycopg2  # type: ignore[import-untyped]
from psycopg2.extras import execute_values  # type: ignore[import-untyped]

# ══════════════════════════════════════════════════════════════
# 연결 정보
# ══════════════════════════════════════════════════════════════

_PG_HOST = os.getenv("INFO_DB_HOST", "localhost")
_PG_PORT = os.getenv("INFO_DB_PORT", "5432")
_PG_USER = os.getenv("PG_SEED_USER", "postgres")
_PG_PASS = os.getenv("PG_SEED_PASSWORD", "postgres")

INFO_DB_CONNINFO = (
    f"host={_PG_HOST} port={_PG_PORT} "
    f"dbname={os.getenv('INFO_DB_NAME', 'info_db')} "
    f"user={_PG_USER} password={_PG_PASS}"
)
HISTORY_DB_CONNINFO = (
    f"host={_PG_HOST} port={_PG_PORT} "
    f"dbname={os.getenv('HISTORY_DB_NAME', 'history_db')} "
    f"user={_PG_USER} password={_PG_PASS}"
)

# requirements doc 경로 (런타임 파싱용)
_REPO_ROOT = Path(__file__).parent.parent.parent
_REQUIREMENTS_DOC = _REPO_ROOT / "docs" / \
    "agent-guides" / "test-data-requirements.md"
RESOURCES_DIR = _REPO_ROOT / "resources"


def _connect(conninfo: str):
    """psycopg2 연결 (autocommit=False)."""
    conn = psycopg2.connect(conninfo)
    conn.autocommit = False
    return conn


# ══════════════════════════════════════════════════════════════
# 상수
# ══════════════════════════════════════════════════════════════

IMPERFECTION_RATE = 0.03  # TYPE-2: 약 3%에 미정의 코드값 삽입

TODAY = date(2026, 3, 21)
STD_DT = TODAY

# PK 컬럼명 → SQL 타입 매핑 (requirements doc 섹션 4 기반)
PK_TYPE_MAP: dict[str, str] = {
    "EDPS_CSN": "VARCHAR(20)", "STD_DT": "DATE", "BASE_DT": "DATE",
    "BASE_YM": "VARCHAR(6)", "ACN": "VARCHAR(20)", "LN_NO": "VARCHAR(20)",
    "CRD_NO": "VARCHAR(20)", "BLNG_BRCD": "VARCHAR(10)", "GRD_CD": "VARCHAR(10)",  # noqa: E501
    "CD_GRP_ID": "VARCHAR(20)", "CD_VAL": "VARCHAR(20)", "SEQ": "INTEGER",
    "DEPT_CD": "VARCHAR(10)", "EMN": "VARCHAR(20)", "HLDY_DT": "DATE",
    "RGN_CD": "VARCHAR(10)", "CNTRY_CD": "VARCHAR(10)", "CCY_CD": "VARCHAR(10)",  # noqa: E501
    "IDX_CD": "VARCHAR(10)", "PARAM_CD": "VARCHAR(20)", "JOB_ID": "VARCHAR(20)",  # noqa: E501
    "EXEC_DT": "DATE", "MENU_ID": "VARCHAR(20)", "AUTH_GRP_CD": "VARCHAR(20)",
    "USER_ID": "VARCHAR(20)", "NOTI_ID": "VARCHAR(20)", "FILE_ID": "VARCHAR(20)",  # noqa: E501
    "LOG_SEQ": "BIGSERIAL", "ERR_SEQ": "BIGSERIAL", "BRCD": "VARCHAR(10)",
    "CHG_DT": "DATE", "CHG_SEQ": "INTEGER", "SYS_CD": "VARCHAR(10)",
    "DL_NO": "VARCHAR(20)", "TR_ID": "VARCHAR(30)", "TR_DT": "DATE",
    "INS_NO": "VARCHAR(20)", "INS_PD_CD": "VARCHAR(20)", "PLAN_NO": "VARCHAR(20)",  # noqa: E501
    "TRUST_NO": "VARCHAR(20)", "FND_ACN": "VARCHAR(20)", "FUND_CD": "VARCHAR(20)",  # noqa: E501
    "IND_CD": "VARCHAR(20)", "CAMP_CD": "VARCHAR(20)", "GL_ACCT_CD": "VARCHAR(20)",  # noqa: E501
    "JOURNAL_NO": "VARCHAR(20)", "KPI_CD": "VARCHAR(20)", "CLAIM_NO": "VARCHAR(20)",  # noqa: E501
    "COVG_CD": "VARCHAR(20)", "PAY_DT": "DATE", "RPAY_DT": "DATE",
    "EVAL_DT": "DATE", "EFF_DT": "DATE", "CALC_DT": "DATE",
    "APPR_NO": "VARCHAR(20)", "APPR_SEQ": "INTEGER", "EXEC_SEQ": "INTEGER",
    "RPAY_SEQ": "INTEGER", "DTL_SEQ": "INTEGER", "MEMO_SEQ": "INTEGER",
    "BENE_SEQ": "INTEGER", "SCORE_DT": "DATE", "VISIT_DT": "DATE",
    "CALL_ID": "VARCHAR(20)", "CMPL_ID": "VARCHAR(20)", "MERGE_SEQ": "INTEGER",
    "HOUSEHOLD_ID": "VARCHAR(20)", "CHN_CD": "VARCHAR(10)", "TAG_CD": "VARCHAR(20)",  # noqa: E501
    "BL_DCD": "VARCHAR(10)", "CONSENT_DCD": "VARCHAR(10)", "VIP_GRD_CD": "VARCHAR(10)",  # noqa: E501
    "BENEFIT_CD": "VARCHAR(20)", "PB_EMN": "VARCHAR(20)", "STD_YR": "VARCHAR(4)",  # noqa: E501
    "DEVICE_ID": "VARCHAR(50)", "NOTI_DCD": "VARCHAR(10)", "AGREE_DT": "DATE",
    "ADR_SEQ": "INTEGER", "CONTACT_SEQ": "INTEGER", "IDENT_DCD": "VARCHAR(10)",
    "REL_CSN": "VARCHAR(20)", "REL_DCD": "VARCHAR(10)", "SEG_DCD": "VARCHAR(10)",  # noqa: E501
    "EVENT_DT": "DATE", "KYC_DT": "DATE", "FIN_YR": "VARCHAR(4)",
    "OWNER_SEQ": "INTEGER", "ITEM_CD": "VARCHAR(20)", "STAT_DT": "DATE",
    "HOLDER_SEQ": "INTEGER", "AUTH_CSN": "VARCHAR(20)", "PD_CD": "VARCHAR(20)",
    "COND_SEQ": "INTEGER", "AUTO_ID": "VARCHAR(20)", "PLEDGE_SEQ": "INTEGER",
    "TAX_YR": "VARCHAR(4)", "CNTR_DT": "DATE", "PLAN_SEQ": "INTEGER",
    "BAL_DT": "DATE", "REISSUE_DT": "DATE", "LINKED_ACN": "VARCHAR(20)",
    "LOCK_DCD": "VARCHAR(10)", "CLTR_NO": "VARCHAR(20)", "GRNT_NO": "VARCHAR(20)",  # noqa: E501
    "LIMIT_DCD": "VARCHAR(10)", "USE_DT": "DATE", "REVIEW_NO": "VARCHAR(20)",
    "RESTRUCTURE_DT": "DATE", "WRITEOFF_DT": "DATE", "RECOVERY_DT": "DATE",
    "EXT_DT": "DATE", "PREPAY_DT": "DATE", "PROP_SEQ": "INTEGER",
    "COVENANT_SEQ": "INTEGER", "DOC_CD": "VARCHAR(20)", "APPRAISAL_NO": "VARCHAR(20)",  # noqa: E501
    "FCAST_DT": "DATE", "GRP_CD": "VARCHAR(20)", "SECTOR_CD": "VARCHAR(20)",
    "WATCH_DT": "DATE", "WARNING_DT": "DATE",
    "USE_SEQ": "BIGSERIAL", "BILL_YM": "VARCHAR(6)", "MCHT_NO": "VARCHAR(20)",
    "CAT_CD": "VARCHAR(20)", "ALERT_ID": "VARCHAR(20)", "FRAUD_ID": "VARCHAR(20)",  # noqa: E501
    "WAIVER_CD": "VARCHAR(20)", "FEE_DCD": "VARCHAR(10)", "FEE_YR": "VARCHAR(4)",  # noqa: E501
    "REQ_DT": "DATE", "AUTO_PAY_SEQ": "INTEGER", "REPORT_DT": "DATE",
    "MAIN_CRD_NO": "VARCHAR(20)", "FAMILY_CRD_NO": "VARCHAR(20)",
    "CTRL_SEQ": "INTEGER", "REMIT_NO": "VARCHAR(20)", "EXCH_NO": "VARCHAR(20)",
    "FWD_NO": "VARCHAR(20)", "SWAP_NO": "VARCHAR(20)", "OPT_NO": "VARCHAR(20)",
    "SETL_NO": "VARCHAR(20)", "SETL_DT": "DATE", "NOSTRO_ACN": "VARCHAR(20)",
    "CORR_BANK_CD": "VARCHAR(20)", "LC_NO": "VARCHAR(20)", "AMEND_SEQ": "INTEGER",  # noqa: E501
    "NEGO_NO": "VARCHAR(20)", "COL_NO": "VARCHAR(20)", "TF_NO": "VARCHAR(20)",
    "DOC_ID": "VARCHAR(20)", "HEDGE_NO": "VARCHAR(20)", "IMPORT_NO": "VARCHAR(20)",  # noqa: E501
    "EXPORT_NO": "VARCHAR(20)", "CHK_NO": "VARCHAR(20)", "REPORT_NO": "VARCHAR(20)",  # noqa: E501
    "MSG_REF_NO": "VARCHAR(30)", "TRX_SEQ": "BIGSERIAL", "NAV_DT": "DATE",
    "DIV_DT": "DATE", "SWITCH_SEQ": "INTEGER", "BM_CD": "VARCHAR(20)",
    "ASSET_SEQ": "INTEGER", "SUIT_DT": "DATE",
    "PAYOUT_SEQ": "INTEGER", "TRANSFER_SEQ": "INTEGER", "IRP_ACN": "VARCHAR(20)",  # noqa: E501
    "EMPLOYER_NO": "VARCHAR(20)", "WITHDRAW_SEQ": "INTEGER", "COUNSEL_SEQ": "INTEGER",  # noqa: E501
    "LOGIN_DT": "DATE", "SESSION_ID": "VARCHAR(50)", "APP_CD": "VARCHAR(20)",
    "VERSION": "VARCHAR(20)", "PUSH_SEQ": "INTEGER", "CERT_NO": "VARCHAR(20)",
    "AUTH_SEQ": "BIGSERIAL", "OTP_SEQ": "INTEGER", "BIO_DCD": "VARCHAR(10)",
    "ORG_CD": "VARCHAR(20)", "ASSET_DCD": "VARCHAR(10)", "API_KEY": "VARCHAR(50)",  # noqa: E501
    "MSG_SEQ": "INTEGER", "SIGN_NO": "VARCHAR(20)", "DOC_NO": "VARCHAR(20)",
    "STMT_YM": "VARCHAR(6)", "TEST_ID": "VARCHAR(20)", "VARIANT_CD": "VARCHAR(20)",  # noqa: E501
    "NOTI_SEQ": "BIGSERIAL", "SMS_SEQ": "BIGSERIAL", "EMAIL_SEQ": "BIGSERIAL",
    "KAKAO_SEQ": "BIGSERIAL", "PARTNER_CD": "VARCHAR(20)", "SCRAP_SEQ": "INTEGER",  # noqa: E501
    "WALLET_ID": "VARCHAR(20)", "TOKEN_ID": "VARCHAR(50)", "FP_ID": "VARCHAR(50)",  # noqa: E501
    "LIMIT_CD": "VARCHAR(20)", "SCENARIO_CD": "VARCHAR(20)", "RATING_DT": "DATE",  # noqa: E501
    "MODEL_CD": "VARCHAR(20)", "PORT_CD": "VARCHAR(20)", "ASSET_CD": "VARCHAR(20)",  # noqa: E501
    "EVENT_NO": "VARCHAR(20)", "LOSS_SEQ": "INTEGER", "KRI_CD": "VARCHAR(20)",
    "CASE_NO": "VARCHAR(20)", "SAR_NO": "VARCHAR(20)", "CTR_NO": "VARCHAR(20)",
    "RULE_CD": "VARCHAR(20)", "WATCH_ID": "VARCHAR(20)", "SCREEN_SEQ": "INTEGER",  # noqa: E501
    "SANCTION_ID": "VARCHAR(20)", "ISSUE_NO": "VARCHAR(20)", "AUDIT_ID": "VARCHAR(20)",  # noqa: E501
    "FINDING_SEQ": "INTEGER", "FU_SEQ": "INTEGER", "CHK_SEQ": "INTEGER",
    "COI_SEQ": "INTEGER", "REG_CHG_NO": "VARCHAR(20)", "BREACH_NO": "VARCHAR(20)",  # noqa: E501
    "BLOCK_SEQ": "INTEGER",
    "OFFER_CD": "VARCHAR(20)", "LEAD_ID": "VARCHAR(20)", "ACT_DT": "DATE",
    "RECOMMEND_DT": "DATE", "PD_GRP_CD": "VARCHAR(20)", "SURVEY_ID": "VARCHAR(20)",  # noqa: E501
    "SURVEY_DT": "DATE", "SEG_CD": "VARCHAR(20)", "EVENT_CD": "VARCHAR(20)",
    "PRIZE_SEQ": "INTEGER", "REFERRAL_SEQ": "INTEGER", "PROGRAM_CD": "VARCHAR(20)",  # noqa: E501
    "LINE_SEQ": "INTEGER", "PL_ITEM_CD": "VARCHAR(20)", "BS_ITEM_CD": "VARCHAR(20)",  # noqa: E501
    "NII_ITEM_CD": "VARCHAR(20)", "CC_CD": "VARCHAR(20)", "COST_CD": "VARCHAR(20)",  # noqa: E501
    "BUDGET_YR": "VARCHAR(4)", "TARGET_YR": "VARCHAR(4)",
    "ASSET_NO": "VARCHAR(20)", "AP_NO": "VARCHAR(20)", "AR_NO": "VARCHAR(20)",
    "PROV_DCD": "VARCHAR(10)", "ADJ_NO": "VARCHAR(20)",
    "DIV_DCD": "VARCHAR(10)", "TAX_DCD": "VARCHAR(10)", "FY": "VARCHAR(4)",
    "MOVE_DT": "DATE", "CORR_DT": "DATE",
    "CONSULT_SEQ": "INTEGER", "PROPOSAL_NO": "VARCHAR(20)",
    "GOAL_SEQ": "INTEGER", "RESEARCH_NO": "VARCHAR(20)",
    "SEMINAR_NO": "VARCHAR(20)", "ADVISORY_NO": "VARCHAR(20)",
    "RE_NO": "VARCHAR(20)", "FO_ID": "VARCHAR(20)",
    "REBAL_DT": "DATE", "AGENT_NO": "VARCHAR(20)",
    "RIDER_CD": "VARCHAR(20)", "RENEWAL_DT": "DATE", "CHK_DT": "DATE",
    "DISC_DT": "DATE", "COMPLAINT_NO": "VARCHAR(20)",
    "LOAN_SEQ": "INTEGER", "CANCEL_DT": "DATE",
    "INVEST_ID": "VARCHAR(20)", "SALE_NO": "VARCHAR(20)",
    "ISSUE_DT": "DATE", "RENEW_DT": "DATE",
    "TRNSFR_NO": "VARCHAR(20)", "RESERVE_NO": "VARCHAR(20)",
    "BATCH_NO": "VARCHAR(20)", "CMS_NO": "VARCHAR(20)",
    "GIRO_NO": "VARCHAR(20)", "TAX_NO": "VARCHAR(20)", "UTIL_NO": "VARCHAR(20)",  # noqa: E501
    "CHECK_NO": "VARCHAR(20)", "CLEAR_DT": "DATE", "BILL_NO": "VARCHAR(20)",
    "PG_TR_ID": "VARCHAR(30)", "QR_TR_ID": "VARCHAR(30)", "OB_TR_ID": "VARCHAR(30)",  # noqa: E501
    "DISPUTE_NO": "VARCHAR(20)", "ORIG_TR_ID": "VARCHAR(30)", "REVERSAL_DT": "DATE",  # noqa: E501
    "ESCROW_NO": "VARCHAR(20)", "VIRTUAL_ACN": "VARCHAR(20)",
    "TR_DCD": "VARCHAR(10)", "ATM_ID": "VARCHAR(20)",
    "CAMP_STCD": "VARCHAR(10)", "OVDU_START_DT": "DATE",
    "TOPUP_DT": "DATE", "WDRW_DT": "DATE",
    "ALERT_LVL_CD": "VARCHAR(10)", "RATIO_CD": "VARCHAR(20)",
    "RESP_YN": "VARCHAR(1)", "CONTACT_DT": "DATE",
    "INVEST_PRFL_CD": "VARCHAR(10)",
    "AUTO_INVEST_ID": "VARCHAR(20)", "ROBO_ID": "VARCHAR(20)",
    "WRAP_NO": "VARCHAR(20)",
    "BOND_CD": "VARCHAR(20)", "COUPON_DT": "DATE",
    "ELS_CD": "VARCHAR(20)", "ELS_ACN": "VARCHAR(20)", "DLS_CD": "VARCHAR(20)",
    "ISA_ACN": "VARCHAR(20)",
    "SETTLOR_SEQ": "INTEGER",
    "CALL_DT": "DATE",
    "LOG_ID": "BIGSERIAL",
    # 추가 PK 컬럼
    "REDEEM_SEQ": "BIGSERIAL", "ASSET_CLASS_CD": "VARCHAR(20)",
    "POINT_DCD": "VARCHAR(10)", "ADV_NO": "VARCHAR(20)",
    "INS_DCD": "VARCHAR(10)", "PN_DCD": "VARCHAR(10)",
    "FEE_DT": "DATE", "CHK_DT": "DATE",
    "BRCD": "VARCHAR(10)", "EMM": "VARCHAR(30)",
}

# 테이블 유형별 표준 추가 컬럼 (PK 외)
_TYPE_EXTRA_COLS: dict[str, list[tuple[str, str]]] = {
    "M": [  # 마스터
        ("STD_DT", "DATE"),
        ("NM", "VARCHAR(100)"),
        ("DCD", "VARCHAR(10)"),
        ("CD", "VARCHAR(20)"),
        ("AMT", "NUMERIC(18,2)"),
        ("USE_YN", "VARCHAR(1) DEFAULT 'Y'"),
        ("RGST_DT", "DATE"),
        ("RGST_USR_ID", "VARCHAR(20)"),
    ],
    "D": [  # 상세
        ("DTL_SEQ", "INTEGER"),
        ("DCD", "VARCHAR(10)"),
        ("AMT", "NUMERIC(18,2)"),
        ("CONT", "VARCHAR(500)"),
        ("RGST_DT", "DATE"),
    ],
    "L": [  # 내역
        ("TR_DT", "DATE"),
        ("EXEC_DT", "DATE"),
        ("AMT", "NUMERIC(18,2)"),
        ("DCD", "VARCHAR(10)"),
        ("CHN_CD", "VARCHAR(10)"),
        ("RGST_DT", "DATE"),
    ],
    "H": [  # 이력
        ("CHG_SEQ", "INTEGER"),
        ("CHG_RSN_DCD", "VARCHAR(10)"),
        ("BEF_VAL", "VARCHAR(200)"),
        ("AFT_VAL", "VARCHAR(200)"),
        ("CHG_USR_ID", "VARCHAR(20)"),
    ],
    "G": [  # 로그
        ("LOG_DT", "TIMESTAMP DEFAULT NOW()"),
        ("USR_ID", "VARCHAR(20)"),
        ("IP_ADR", "VARCHAR(50)"),
        ("ACTN_DCD", "VARCHAR(10)"),
        ("LOG_CONT", "TEXT"),
    ],
    "S": [  # 집계
        ("BASE_YM", "VARCHAR(6)"),
        ("CNT", "INTEGER DEFAULT 0"),
        ("AMT", "NUMERIC(18,2) DEFAULT 0"),
        ("AVG_AMT", "NUMERIC(18,2)"),
        ("CALC_DT", "DATE"),
    ],
    "P": [  # 스냅샷
        ("STD_DT", "DATE"),
        ("BAL_AMT", "NUMERIC(18,2)"),
        ("EVAL_AMT", "NUMERIC(18,2)"),
        ("DCD", "VARCHAR(10)"),
        ("RGST_DT", "DATE"),
    ],
    "C": [  # 코드
        ("CD_NM", "VARCHAR(100)"),
        ("CD_DESC", "VARCHAR(500)"),
        ("USE_YN", "VARCHAR(1) DEFAULT 'Y'"),
        ("ORD_NO", "INTEGER"),
    ],
}


# ══════════════════════════════════════════════════════════════
# 도메인별 업무 컬럼 (비-★ DDL에 사용)
# ══════════════════════════════════════════════════════════════

def _extract_domain(table_name: str) -> str:
    """TB_ADW_CSC101M → 'CSC', TB_ADW_LNCL305M → 'LNCL'."""
    suffix = table_name[7:]  # TB_ADW_ 이후
    for i, c in enumerate(suffix):
        if c.isdigit():
            return suffix[:i]
    return suffix[:-1]


# 3~4글자 도메인 코드 → 그룹 키
_DOMAIN_GROUP: dict[str, str] = {
    "COM": "COM",
    "CSC": "CUS", "CSP": "CUS", "CUS": "CUS",
    "DEP": "DEP", "DEA": "DEP", "DEPS": "DEP",
    "LNB": "LN", "LNR": "LN", "LNA": "LN",
    "LNC": "LN", "LNCL": "LN",
    "CRD": "CRD", "CRU": "CRD", "CRDB": "CRD",
    "FXD": "FX", "FXB": "FX", "TRD": "FX",
    "FND": "FND", "TRS": "FND", "ELS": "FND", "BND": "FND",
    "TRX": "TRX", "TXP": "TRX",
    "INS": "INS", "INSP": "INS",
    "PNB": "PN", "PNI": "PN",
    "DGB": "DG", "DGA": "DG", "MYDT": "DG",
    "RSK": "RSK", "AML": "AML", "FDS": "RSK", "CMP": "RSK",
    "MKT": "MKT", "CRM": "MKT",
    "FIN": "FIN", "GLB": "FIN", "BUDG": "FIN",
    "WMB": "WM", "WMR": "WM",
}

# 도메인 그룹 → 업무 컬럼 (★ 테이블 DDL 기반 + 은행 도메인 지식)
_DOMAIN_BIZ_COLS: dict[str, list[tuple[str, str]]] = {

    # ── COM (공통: 부점, 코드, 캘린더, 파라미터) ──
    "COM": [
        ("NM", "VARCHAR(100)"),
        ("DESC_CONT", "VARCHAR(500)"),
        ("USE_YN", "VARCHAR(1) DEFAULT 'Y'"),
        ("SORT_ORD", "INTEGER"),
        ("RGST_DT", "DATE"),
        ("RGST_USR_ID", "VARCHAR(20)"),
    ],

    # ── CUS (고객: 기본정보, 프로필, KYC, VIP, 세그먼트) ──
    "CUS": [
        ("EDPS_CSN", "VARCHAR(20)"),
        ("CSM", "VARCHAR(100)"),
        ("CUS_DCD", "VARCHAR(10)"),
        ("CUS_GRD_CD", "VARCHAR(10)"),
        ("GNDR_DCD", "VARCHAR(5)"),
        ("AGE_GRP_CD", "VARCHAR(10)"),
        ("BLNG_BRCD", "VARCHAR(10)"),
        ("JOIN_DT", "DATE"),
        ("TEL_NO", "VARCHAR(20)"),
        ("STS_DCD", "VARCHAR(10)"),
    ],

    # ── DEP (수신: 계좌, 상품, 이자, 자동이체, 예금잔고) ──
    "DEP": [
        ("ACN", "VARCHAR(20)"),
        ("EDPS_CSN", "VARCHAR(20)"),
        ("ACT_DCD", "VARCHAR(10)"),
        ("ACT_STCD", "VARCHAR(10)"),
        ("PD_NM", "VARCHAR(100)"),
        ("BAL_AMT", "NUMERIC(18,2)"),
        ("INT_RT", "NUMERIC(10,4)"),
        ("OPEN_DT", "DATE"),
        ("MAT_DT", "DATE"),
        ("BLNG_BRCD", "VARCHAR(10)"),
    ],

    # ── LN (여신: 대출잔고, 담보, 심사, 상환, 구조조정) ──
    "LN": [
        ("LN_NO", "VARCHAR(20)"),
        ("EDPS_CSN", "VARCHAR(20)"),
        ("LN_DCD", "VARCHAR(10)"),
        ("LN_STCD", "VARCHAR(10)"),
        ("LN_BAL_AMT", "NUMERIC(18,2)"),
        ("INT_RT", "NUMERIC(10,4)"),
        ("OVDU_GRD_CD", "VARCHAR(10)"),
        ("CLTR_DCD", "VARCHAR(10)"),
        ("LN_PUSE_CD", "VARCHAR(10)"),
        ("BLNG_BRCD", "VARCHAR(10)"),
    ],

    # ── CRD (카드: 카드마스터, 이용내역, 청구, 포인트, 한도) ──
    "CRD": [
        ("CRD_NO", "VARCHAR(20)"),
        ("EDPS_CSN", "VARCHAR(20)"),
        ("CRD_DCD", "VARCHAR(10)"),
        ("CRD_LIMIT_AMT", "NUMERIC(18,2)"),
        ("USE_AMT", "NUMERIC(18,2)"),
        ("ISSUE_DT", "DATE"),
        ("STS_DCD", "VARCHAR(10)"),
        ("BLNG_BRCD", "VARCHAR(10)"),
    ],

    # ── FX (외환: 외환딜, 환율, 선물환, 스왑, 무역금융) ──
    "FX": [
        ("DL_NO", "VARCHAR(20)"),
        ("EDPS_CSN", "VARCHAR(20)"),
        ("CCY_CD", "VARCHAR(10)"),
        ("FX_DL_DCD", "VARCHAR(10)"),
        ("DL_AMT", "NUMERIC(18,2)"),
        ("EXC_RT", "NUMERIC(18,6)"),
        ("DL_DT", "DATE"),
        ("SETL_DT", "DATE"),
        ("BLNG_BRCD", "VARCHAR(10)"),
    ],

    # ── FND (펀드/신탁/ELS/채권) ──
    "FND": [
        ("FND_ACN", "VARCHAR(20)"),
        ("EDPS_CSN", "VARCHAR(20)"),
        ("FUND_CD", "VARCHAR(20)"),
        ("FND_DCD", "VARCHAR(10)"),
        ("RSK_GRD_CD", "VARCHAR(10)"),
        ("INV_AMT", "NUMERIC(18,2)"),
        ("EVAL_AMT", "NUMERIC(18,2)"),
        ("QTY", "NUMERIC(18,4)"),
        ("BLNG_BRCD", "VARCHAR(10)"),
    ],

    # ── TRX (거래: 거래내역, 결제채널, 이체) ──
    "TRX": [
        ("ACN", "VARCHAR(20)"),
        ("EDPS_CSN", "VARCHAR(20)"),
        ("TR_DCD", "VARCHAR(10)"),
        ("TR_AMT", "NUMERIC(18,2)"),
        ("BAL_AFT_TR", "NUMERIC(18,2)"),
        ("CHN_CD", "VARCHAR(10)"),
        ("TR_DT", "DATE"),
        ("BLNG_BRCD", "VARCHAR(10)"),
    ],

    # ── INS (보험: 보험마스터, 보험금청구, 납입, 심사) ──
    "INS": [
        ("INS_NO", "VARCHAR(20)"),
        ("EDPS_CSN", "VARCHAR(20)"),
        ("INS_DCD", "VARCHAR(10)"),
        ("INS_PD_CD", "VARCHAR(20)"),
        ("PREM_AMT", "NUMERIC(18,2)"),
        ("COV_AMT", "NUMERIC(18,2)"),
        ("PAY_STCD", "VARCHAR(10)"),
        ("INS_ST_DT", "DATE"),
        ("INS_END_DT", "DATE"),
        ("BLNG_BRCD", "VARCHAR(10)"),
    ],

    # ── PN (연금: 퇴직연금마스터, 납입, 운용) ──
    "PN": [
        ("PLAN_NO", "VARCHAR(20)"),
        ("EDPS_CSN", "VARCHAR(20)"),
        ("PN_DCD", "VARCHAR(10)"),
        ("CONTR_AMT", "NUMERIC(18,2)"),
        ("BAL_AMT", "NUMERIC(18,2)"),
        ("RETURN_RT", "NUMERIC(10,4)"),
        ("EMPLOYER_NM", "VARCHAR(100)"),
        ("BLNG_BRCD", "VARCHAR(10)"),
    ],

    # ── DG (디지털뱅킹: 앱로그인, 인증, 푸시, 마이데이터) ──
    "DG": [
        ("EDPS_CSN", "VARCHAR(20)"),
        ("APP_CD", "VARCHAR(20)"),
        ("CHN_CD", "VARCHAR(10)"),
        ("DEVICE_ID", "VARCHAR(50)"),
        ("STS_DCD", "VARCHAR(10)"),
        ("LOGIN_DT", "DATE"),
        ("BLNG_BRCD", "VARCHAR(10)"),
    ],

    # ── RSK (리스크: 리스크지표, Basel III, IFRS9) ──
    "RSK": [
        ("IND_CD", "VARCHAR(20)"),
        ("IND_NM", "VARCHAR(100)"),
        ("IND_VAL", "NUMERIC(18,6)"),
        ("PREV_VAL", "NUMERIC(18,6)"),
        ("CHG_RT", "NUMERIC(10,4)"),
        ("EVAL_DT", "DATE"),
        ("BLNG_BRCD", "VARCHAR(10)"),
    ],

    # ── AML (자금세탁방지: 의심거래보고, 제재, 모니터링) ──
    "AML": [
        ("EDPS_CSN", "VARCHAR(20)"),
        ("ALERT_LVL_CD", "VARCHAR(10)"),
        ("TR_AMT", "NUMERIC(18,2)"),
        ("RSK_GRD_CD", "VARCHAR(10)"),
        ("STS_DCD", "VARCHAR(10)"),
        ("DETECT_DT", "DATE"),
        ("REVIEW_DT", "DATE"),
        ("BLNG_BRCD", "VARCHAR(10)"),
    ],

    # ── MKT (마케팅: 캠페인, CRM, NPS) ──
    "MKT": [
        ("CAMP_CD", "VARCHAR(20)"),
        ("EDPS_CSN", "VARCHAR(20)"),
        ("CAMP_STCD", "VARCHAR(10)"),
        ("CHN_CD", "VARCHAR(10)"),
        ("RESP_YN", "VARCHAR(1)"),
        ("OFFER_AMT", "NUMERIC(18,2)"),
        ("CONTACT_DT", "DATE"),
        ("BLNG_BRCD", "VARCHAR(10)"),
    ],

    # ── FIN (재무: 손익, GL, 분개, KPI, 예산) ──
    "FIN": [
        ("BLNG_BRCD", "VARCHAR(10)"),
        ("PL_ITEM_CD", "VARCHAR(20)"),
        ("PL_ITEM_NM", "VARCHAR(100)"),
        ("AMT", "NUMERIC(18,2)"),
        ("PREV_AMT", "NUMERIC(18,2)"),
        ("YOY_RT", "NUMERIC(10,4)"),
        ("BUDGET_AMT", "NUMERIC(18,2)"),
    ],

    # ── WM (자산관리: WM고객, 포트폴리오, 자문) ──
    "WM": [
        ("EDPS_CSN", "VARCHAR(20)"),
        ("WM_GRD_CD", "VARCHAR(10)"),
        ("INVEST_PRFL_CD", "VARCHAR(10)"),
        ("TOT_ASSET_AMT", "NUMERIC(18,2)"),
        ("PB_EMN", "VARCHAR(20)"),
        ("BLNG_BRCD", "VARCHAR(10)"),
    ],
}

# C, G 타입은 도메인 무관하게 구조적 컬럼만 사용
_TYPE_STRUCTURAL: dict[str, list[tuple[str, str]]] = {
    "H": [
        ("CHG_RSN_DCD", "VARCHAR(10)"),
        ("BEF_VAL", "VARCHAR(200)"),
        ("AFT_VAL", "VARCHAR(200)"),
        ("CHG_USR_ID", "VARCHAR(20)"),
    ],
    "S": [  # 집계 타입은 도메인 컬럼 + 이 컬럼 추가
        ("CNT", "INTEGER DEFAULT 0"),
        ("AVG_AMT", "NUMERIC(18,2)"),
    ],
}

# CODE_META 미등록 코드의 보충 유효값
_EXTRA_CODES: dict[str, list[str]] = {
    "GNDR_DCD": ["M", "F"],
    "AGE_GRP_CD": ["20", "30", "40", "50", "60"],
    "STS_DCD": ["01", "02", "03"],
    "WM_GRD_CD": ["WM_VIP", "WM_PREMIUM", "WM_GOLD", "WM_STANDARD"],
    "INVEST_PRFL_CD": ["1", "2", "3", "4", "5"],
    "ALERT_LVL_CD": ["H", "M", "L"],
    "CHG_RSN_DCD": ["01", "02", "03", "04"],
    "SORT_ORD": [],  # INTEGER, 별도 처리
}

# 도메인별 상품명 풀
_DOMAIN_PRODUCT_NAMES: dict[str, list[str]] = {
    "DEP": [
        "자유입출금통장", "정기예금 1년", "정기예금 2년", "정기적금 12개월",
        "정기적금 24개월", "MMF통장", "CMA통장", "급여이체통장",
        "주거래우대통장", "청년희망적금", "내집마련적금",
    ],
    "LN": [
        "주택담보대출", "신용대출", "전세자금대출", "사업자대출",
        "중소기업대출", "자동차대출", "학자금대출", "마이너스통장",
        "아파트담보대출", "상가담보대출", "정책자금대출",
    ],
    "CRD": [
        "신용카드 플래티넘", "신용카드 골드", "신용카드 클래식",
        "체크카드 기본", "체크카드 캐시백", "법인카드 일반",
        "하이브리드카드", "포인트적립카드", "항공마일리지카드",
    ],
    "FX": [
        "외환송금", "외화예금", "선물환계약", "통화스왑",
        "수출입신용장", "외화대출", "환전서비스",
    ],
    "FND": [
        "글로벌성장펀드", "국공채안정형", "혼합자산운용", "인덱스코스피200",
        "신탁금전형", "ELS원금보장형", "ELS수익추구형", "채권형펀드",
        "하이일드펀드", "리츠부동산펀드", "해외주식펀드",
    ],
    "INS": [
        "종합보험", "건강보험", "저축보험", "변액보험",
        "연금보험", "화재보험", "자동차보험", "여행자보험",
        "상해보험", "실손의료보험",
    ],
    "PN": [
        "퇴직연금DB", "퇴직연금DC", "개인형IRP", "연금저축신탁",
        "연금저축보험", "퇴직연금원리금보장",
    ],
    "WM": [
        "WM종합자산관리", "PB전용포트폴리오", "VIP자산배분형",
        "글로벌자산배분", "안정성장형포트폴리오", "고수익추구형",
    ],
}
# 범용 상품명 (도메인 미매칭 시)
_PRODUCT_NAMES = [
    "자유입출금통장", "정기예금 1년", "정기적금 12개월", "MMF통장",
    "주택담보대출", "신용대출", "전세자금대출", "사업자대출",
    "신용카드 플래티넘", "체크카드 기본", "글로벌성장펀드",
    "국공채안정형", "혼합자산운용", "인덱스코스피200",
    "종합보험", "건강보험", "저축보험", "퇴직연금DB",
]


def _get_extra_cols(
    table_name: str, pk_cols_used: set[str],
) -> list[tuple[str, str]]:
    """비-★ 테이블의 도메인+타입 기반 추가 컬럼 결정.

    PK와 중복되는 컬럼은 자동 제외.
    C, G 타입은 도메인 무관 구조적 컬럼만 반환.
    """
    tbl_type = table_name[-1]
    domain_raw = _extract_domain(table_name)
    group = _DOMAIN_GROUP.get(domain_raw, "COM")

    result: list[tuple[str, str]] = []
    used = set(pk_cols_used)

    # C(코드), G(로그) 타입: 도메인 무관
    if tbl_type == "C":
        for col, typ in _TYPE_EXTRA_COLS["C"]:
            if col not in used:
                result.append((col, typ))
                used.add(col)
        return result

    if tbl_type == "G":
        for col, typ in _TYPE_EXTRA_COLS["G"]:
            if col not in used:
                result.append((col, typ))
                used.add(col)
        return result

    # 도메인 업무 컬럼
    biz = _DOMAIN_BIZ_COLS.get(group, _DOMAIN_BIZ_COLS["COM"])
    for col, typ in biz:
        if col not in used:
            result.append((col, typ))
            used.add(col)

    # 타입별 구조적 추가 컬럼 (H: 변경이력, S: 집계)
    for col, typ in _TYPE_STRUCTURAL.get(tbl_type, []):
        if col not in used:
            result.append((col, typ))
            used.add(col)

    return result


# ══════════════════════════════════════════════════════════════
# 데이터 생성용 상수
# ══════════════════════════════════════════════════════════════

BRANCHES = [
    ("001", "본점영업부", "01", "서울"), ("002", "강남지점", "01", "서울"),
    ("003", "여의도지점", "01", "서울"), ("004", "서초지점", "01", "서울"),
    ("005", "종로지점", "01", "서울"), ("006", "영등포지점", "01", "서울"),
    ("007", "마포지점", "01", "서울"), ("008", "송파지점", "01", "서울"),
    ("009", "분당지점", "02", "경기"), ("010", "수원지점", "02", "경기"),
    ("011", "인천지점", "03", "인천"), ("012", "대전지점", "04", "대전"),
    ("013", "대구지점", "05", "대구"), ("014", "부산지점", "06", "부산"),
    ("015", "광주지점", "07", "광주"), ("016", "울산지점", "08", "울산"),
    ("017", "제주지점", "09", "제주"), ("018", "청주지점", "10", "충북"),
    ("019", "전주지점", "11", "전북"), ("020", "창원지점", "12", "경남"),
]

CUST_GRADES = ["01", "02", "03", "04", "05"]
MKT_GRADES = ["A", "B", "C", "D", "E"]
CUST_TYPES = ["01", "02", "03"]
GENDERS = ["M", "F"]
AGE_GROUPS = ["20", "30", "40", "50", "60"]
LOAN_TYPES = ["01", "02", "03"]
ACCT_TYPES = ["01", "02", "03", "04"]
CARD_TYPES = ["01", "02", "03"]
CHANNELS = ["01", "02", "03", "04"]
PREF_CHANNELS = ["영업점", "인터넷뱅킹", "모바일뱅킹", "ATM", "콜센터"]

FX_DEAL_TYPES = ["01", "02", "03", "04", "05"]
CURRENCIES = ["USD", "EUR", "JPY", "GBP", "CNY"]
FX_IMPERFECT_CCY = "CNH"

FUND_TYPES = ["01", "02", "03", "04"]
RISK_GRADES = ["1", "2", "3", "4", "5"]
FUND_PRODUCTS = [
    ("FP001", "삼성코리아대표주식"), ("FP002", "미래에셋글로벌그로스"),
    ("FP003", "KB국공채안정"), ("FP004", "신한MMF제일호"),
    ("FP005", "하나인덱스코스피200"), ("FP006", "한투글로벌헬스케어"),
    ("FP007", "NH아문디올웨더"), ("FP008", "키움단기채권"),
]

LOAN_PUSE_CODES = ["01", "02", "03", "04", "05"]
CLTR_TYPE_CODES = ["01", "02", "03", "04"]

DEPOSIT_PRODUCTS = [
    ("P001", "자유입출금통장"), ("P002", "정기예금 1년"),
    ("P003", "정기예금 2년"), ("P004", "정기적금 12개월"),
    ("P005", "정기적금 24개월"), ("P006", "MMF 통장"),
    ("P007", "청년희망적금"), ("P008", "주택청약저축"),
]

SURNAMES = [
    "김",
    "이",
    "박",
    "최",
    "정",
    "강",
    "조",
    "윤",
    "장",
    "임",
    "한",
    "오",
    "서",
    "신",
    "권"]
GIVEN_NAMES = [
    "민준", "서윤", "하준", "지우", "서준", "서연", "도윤", "하은",
    "지호", "수빈", "예준", "지민", "시우", "유진", "주원", "채원",
    "지훈", "수현", "건우", "소율", "현우", "다은", "선우", "예은",
    "영호", "미영", "정수", "은정", "상혁", "혜진", "태현", "지영",
]
COMPANY_NAMES = [
    "(주)한국전자", "(주)서울물산", "(주)대한건설", "(주)미래기술",
    "(주)동양식품", "(주)코리아소프트", "(주)글로벌트레이딩", "(주)신세계유통",
]
FAKE_DOMAINS = ["example.com", "test.co.kr", "sample.net", "mail.kr"]
FAKE_ADDRS = [
    "서울시 강남구 테헤란로 123", "서울시 서초구 반포대로 45",
    "경기도 성남시 분당구 판교로 67", "부산시 해운대구 센텀중앙로 89",
    "대전시 유성구 대학로 12", "인천시 연수구 송도대로 34",
    "대구시 수성구 달구벌대로 56", "광주시 서구 상무중앙로 78",
]

INS_TYPES = ["L", "N", "H"]    # 생명/상해/건강 (TYPE-2: E 추가)
PLAN_TYPES = ["DB", "DC", "IRP"]  # 퇴직연금 (TYPE-2: HYB 추가)
RISK_IND_CODES = [
    ("BIS_RATIO", "BIS자기자본비율"),
    ("LCR", "유동성커버리지비율"),
    ("NSFR", "순안정자금조달비율"),
    ("NIM", "순이자마진"),
    ("ROA", "총자산순이익률"),
    ("ROE", "자기자본순이익률"),
    ("NPL_RATIO", "부실채권비율"),
    ("CVA", "신용가치조정"),
    ("LTV_AVG", "평균담보인정비율"),
    ("DSR_AVG", "평균총부채원리금상환비율"),
]
PL_ITEM_CODES = [
    "NII", "NFI", "OPEX", "PROV", "PRETAX", "NET",
    "INT_INC", "FEE_INC", "FX_INC", "FUND_INC",
]
CAMP_CODES = [f"CAMP{i:04d}" for i in range(1, 51)]
WM_GRADES = ["WM_VIP", "WM_PREMIUM", "WM_GOLD", "WM_STANDARD"]


# ══════════════════════════════════════════════════════════════
# 마크다운 파싱: 섹션 5 테이블 카탈로그
# ══════════════════════════════════════════════════════════════

# 파싱 패턴: | N | (★ )?`TB_ADW_XXXXX` | 한글명 | PK예시 |
# 예: | 1 | ★ `TB_ADW_COM001M` | COM부점정보기본 | `BLNG_BRCD` | |
_TABLE_ROW_PATTERN = re.compile(
    r"^\|\s*\d+\s*\|\s*(★\s*)?`(TB_ADW_[A-Z0-9]+)`\s*\|[^|]*\|([^|]*)\|"
)


def parse_table_catalog() -> list[dict]:
    """requirements doc 섹션 5에서 테이블 목록을 런타임 파싱한다.

    반환 형식: [{"name": str, "star": bool, "pk_cols": list[str]}, ...]
    """
    if not _REQUIREMENTS_DOC.exists():
        print(f"  [경고] requirements doc 없음: {_REQUIREMENTS_DOC}")
        print("  → 하드코딩된 테이블 목록을 사용합니다.")
        return _fallback_table_list()

    tables: list[dict] = []
    seen: set[str] = set()
    text = _REQUIREMENTS_DOC.read_text(encoding="utf-8")

    for line in text.splitlines():
        m = _TABLE_ROW_PATTERN.match(line.strip())
        if not m:
            continue
        is_star = bool(m.group(1))
        table_name = m.group(2)
        pk_raw = m.group(3)

        if table_name in seen:
            continue
        seen.add(table_name)

        # PK 컬럼 파싱: `COL1 + COL2` → ["COL1", "COL2"]
        pk_cols = [c.strip().strip("`") for c in re.split(
            r"\s*\+\s*", pk_raw.strip().strip("`")) if c.strip()]

        tables.append({
            "name": table_name,
            "star": is_star,
            "pk_cols": pk_cols,
        })

    print(f"  [파싱] requirements doc에서 {len(tables)}개 테이블 추출")
    return tables


def _fallback_table_list() -> list[dict]:
    """requirements doc 없을 때 최소 하드코딩 폴백."""
    star_tables = [
        ("TB_ADW_COM001M", ["BLNG_BRCD"]),
        ("TB_ADW_COM002M", ["GRD_CD"]),
        ("TB_ADW_CSC101M", ["EDPS_CSN", "STD_DT"]),
        ("TB_ADW_CSC102H", ["EDPS_CSN", "STD_DT"]),
        ("TB_ADW_CSP103M", ["EDPS_CSN"]),
        ("TB_ADW_DEP201P", ["ACN", "STD_DT"]),
        ("TB_ADW_DEP202S", ["ACN", "BASE_DT"]),
        ("TB_ADW_LNB301M", ["LN_NO", "STD_DT"]),
        ("TB_ADW_LNB302M", ["LN_NO"]),
        ("TB_ADW_CRD401M", ["CRD_NO", "STD_DT"]),
        ("TB_ADW_FXD501L", ["DL_NO"]),
        ("TB_ADW_FXB502M", ["CCY_CD", "BASE_DT"]),
        ("TB_ADW_FND601P", ["FND_ACN", "STD_DT"]),
        ("TB_ADW_FND602P", ["FND_ACN", "STD_DT"]),
        ("TB_ADW_TRX701L", ["TR_ID", "TR_DT"]),
        ("TB_ADW_INS803M", ["INS_NO"]),
        ("TB_ADW_PNB904P", ["PLAN_NO", "EDPS_CSN", "STD_DT"]),
        ("TB_ADW_RSK1101M", ["IND_CD", "STD_DT"]),
        ("TB_ADW_MKT1201M", ["CAMP_CD"]),
        ("TB_ADW_MKT1202M", ["CAMP_CD", "EDPS_CSN"]),
        ("TB_ADW_FIN1306S", ["BLNG_BRCD", "BASE_YM", "PL_ITEM_CD"]),
        ("TB_ADW_WMB1401M", ["EDPS_CSN"]),
    ]
    return [{"name": n, "star": True, "pk_cols": pk} for n, pk in star_tables]


# ══════════════════════════════════════════════════════════════
# DDL 자동 생성
# ══════════════════════════════════════════════════════════════

def _infer_table_type(table_name: str) -> str:
    """테이블명 마지막 글자로 유형 추론."""
    return table_name[-1] if table_name[-1] in _TYPE_EXTRA_COLS else "M"


def _pk_col_type(col: str) -> str:
    """PK 컬럼명에서 SQL 타입 결정."""
    return PK_TYPE_MAP.get(col, "VARCHAR(20)")


def _build_ddl(table_name: str, pk_cols: list[str]) -> str:
    """비-★ 테이블용 DDL 자동 생성 (도메인 인식).

    PK 컬럼 + 도메인별 업무 컬럼 + 타입별 구조 컬럼으로 구성.
    기존 테이블이 있으면 DROP 후 재생성 (컬럼 변경 반영).
    """
    col_defs: list[str] = []
    pk_col_names: list[str] = []
    used_names: set[str] = set()

    for col in pk_cols:
        col_type = _pk_col_type(col)
        if col_type == "BIGSERIAL":
            col_defs.append(f"    {col} BIGSERIAL PRIMARY KEY")
            pk_col_names = []
        else:
            col_defs.append(f"    {col} {col_type} NOT NULL")
            pk_col_names.append(col)
        used_names.add(col)

    # 도메인 + 타입 기반 추가 컬럼
    extra_cols = _get_extra_cols(table_name, used_names)
    for extra_name, extra_type in extra_cols:
        col_defs.append(f"    {extra_name} {extra_type}")
        used_names.add(extra_name)

    # 공통 메타 컬럼
    for meta_col, meta_type in [
            ("INS_DTM", "TIMESTAMP DEFAULT NOW()"),
            ("UPD_DTM", "TIMESTAMP")]:
        if meta_col not in used_names:
            col_defs.append(f"    {meta_col} {meta_type}")

    if pk_col_names:
        col_defs.append(
            f"    PRIMARY KEY ({', '.join(pk_col_names)})")

    col_block = ",\n".join(col_defs)
    return (
        f"DROP TABLE IF EXISTS ADWOWN.{table_name} CASCADE;\n"
        f"CREATE TABLE ADWOWN.{table_name} (\n"
        f"{col_block}\n"
        f");"
    )


# ══════════════════════════════════════════════════════════════
# ★ 테이블 상세 DDL 정의 (22개)
# ══════════════════════════════════════════════════════════════

STAR_DDL: dict[str, str] = {

    # ── 공통 ──────────────────────────────────────────────────
    "TB_ADW_COM001M": """
CREATE TABLE IF NOT EXISTS ADWOWN.TB_ADW_COM001M (
    BLNG_BRCD   VARCHAR(10)  NOT NULL,
    BR_NM       VARCHAR(100) NOT NULL,
    RGN_CD      VARCHAR(10),
    RGN_NM      VARCHAR(50),
    BR_DCD      VARCHAR(10),
    INS_DTM     TIMESTAMP DEFAULT NOW(),
    UPD_DTM     TIMESTAMP,
    PRIMARY KEY (BLNG_BRCD)
);""",

    "TB_ADW_COM002M": """
CREATE TABLE IF NOT EXISTS ADWOWN.TB_ADW_COM002M (
    GRD_CD      VARCHAR(10)  NOT NULL,
    GRD_NM      VARCHAR(50)  NOT NULL,
    GRD_DESC    VARCHAR(500),
    INS_DTM     TIMESTAMP DEFAULT NOW(),
    UPD_DTM     TIMESTAMP,
    PRIMARY KEY (GRD_CD)
);""",

    # ── 고객 ──────────────────────────────────────────────────
    "TB_ADW_CSC101M": """
CREATE TABLE IF NOT EXISTS ADWOWN.TB_ADW_CSC101M (
    EDPS_CSN    VARCHAR(20)  NOT NULL,
    STD_DT      DATE         NOT NULL,
    CSM         VARCHAR(100),
    CUS_DCD     VARCHAR(10),
    JOIN_DT     DATE,
    BLNG_BRCD   VARCHAR(10),
    GNDR_DCD    VARCHAR(5),
    AGE_GRP_CD  VARCHAR(10),
    CUS_GRD_CD  VARCHAR(10),
    TEL_NO      VARCHAR(20),
    EMAIL_ADR   VARCHAR(100),
    INS_DTM     TIMESTAMP DEFAULT NOW(),
    UPD_DTM     TIMESTAMP,
    PRIMARY KEY (EDPS_CSN, STD_DT)
);""",

    "TB_ADW_CSC102H": """
CREATE TABLE IF NOT EXISTS ADWOWN.TB_ADW_CSC102H (
    EDPS_CSN    VARCHAR(20)  NOT NULL,
    STD_DT      DATE         NOT NULL,
    CSM         VARCHAR(100),
    CUS_DCD     VARCHAR(10),
    RGST_DT     DATE,
    BLNG_BRCD   VARCHAR(10),
    GNDR_DCD    VARCHAR(5),
    AGE_GRP_CD  VARCHAR(10),
    CUS_GRD_CD  VARCHAR(10),
    CUS_ADR     VARCHAR(300),
    PHONE_NO    VARCHAR(20),
    INS_DTM     TIMESTAMP DEFAULT NOW(),
    UPD_DTM     TIMESTAMP,
    PRIMARY KEY (EDPS_CSN, STD_DT)
);""",

    "TB_ADW_CSP103M": """
CREATE TABLE IF NOT EXISTS ADWOWN.TB_ADW_CSP103M (
    EDPS_CSN    VARCHAR(20)  NOT NULL,
    CSM         VARCHAR(100),
    CUS_DCD     VARCHAR(10),
    MKT_GRD_CD  VARCHAR(10),
    BLNG_BRCD   VARCHAR(10),
    AGE_GRP_CD  VARCHAR(10),
    GNDR_DCD    VARCHAR(5),
    PREF_CHN_DCD VARCHAR(20),
    INS_DTM     TIMESTAMP DEFAULT NOW(),
    UPD_DTM     TIMESTAMP,
    PRIMARY KEY (EDPS_CSN)
);""",

    # ── 수신 ──────────────────────────────────────────────────
    "TB_ADW_DEP201P": """
CREATE TABLE IF NOT EXISTS ADWOWN.TB_ADW_DEP201P (
    ACN         VARCHAR(20)  NOT NULL,
    STD_DT      DATE         NOT NULL,
    EDPS_CSN    VARCHAR(20),
    ACT_DCD     VARCHAR(10),
    BAL_AMT     NUMERIC(18,2),
    OPEN_DT     DATE,
    BLNG_BRCD   VARCHAR(10),
    PD_CD       VARCHAR(20),
    PD_NM       VARCHAR(100),
    APLY_RT     NUMERIC(8,4),
    ACT_STCD    VARCHAR(10),
    INS_DTM     TIMESTAMP DEFAULT NOW(),
    UPD_DTM     TIMESTAMP,
    PRIMARY KEY (ACN, STD_DT)
);""",

    "TB_ADW_DEP202S": """
CREATE TABLE IF NOT EXISTS ADWOWN.TB_ADW_DEP202S (
    ACN         VARCHAR(20)  NOT NULL,
    BASE_DT     DATE         NOT NULL,
    EDPS_CSN    VARCHAR(20),
    ACT_DCD     VARCHAR(10),
    TOT_BAL_AMT NUMERIC(18,2),
    OPEN_DT     DATE,
    BLNG_BRCD   VARCHAR(10),
    PD_CD       VARCHAR(20),
    INS_DTM     TIMESTAMP DEFAULT NOW(),
    UPD_DTM     TIMESTAMP,
    PRIMARY KEY (ACN, BASE_DT)
);""",

    # ── 여신 ──────────────────────────────────────────────────
    "TB_ADW_LNB301M": """
CREATE TABLE IF NOT EXISTS ADWOWN.TB_ADW_LNB301M (
    LN_NO       VARCHAR(20)  NOT NULL,
    STD_DT      DATE         NOT NULL,
    EDPS_CSN    VARCHAR(20),
    LN_EXC_AMT NUMERIC(18,2),
    LN_BAL_AMT NUMERIC(18,2),
    LN_DT       DATE,
    MTRTY_DT    DATE,
    APLY_RT     NUMERIC(8,4),
    LN_DCD      VARCHAR(10),
    LN_STCD     VARCHAR(10),
    OVDU_GRD_CD VARCHAR(10),
    OVDU_DY_CN  INTEGER,
    OVDU_AMT    NUMERIC(18,2),
    BLNG_BRCD   VARCHAR(10),
    INS_DTM     TIMESTAMP DEFAULT NOW(),
    UPD_DTM     TIMESTAMP,
    PRIMARY KEY (LN_NO, STD_DT)
);""",

    "TB_ADW_LNB302M": """
CREATE TABLE IF NOT EXISTS ADWOWN.TB_ADW_LNB302M (
    LN_NO       VARCHAR(20)  NOT NULL,
    EDPS_CSN    VARCHAR(20),
    LN_APR_AMT NUMERIC(18,2),
    APPR_DT     DATE,
    LN_DCD      VARCHAR(10),
    LN_PUSE_CD  VARCHAR(20),
    CLTR_DCD    VARCHAR(10),
    APLY_RT     NUMERIC(8,4),
    MTRTY_DT    DATE,
    BLNG_BRCD   VARCHAR(10),
    INS_DTM     TIMESTAMP DEFAULT NOW(),
    UPD_DTM     TIMESTAMP,
    PRIMARY KEY (LN_NO)
);""",

    # ── 카드 ──────────────────────────────────────────────────
    "TB_ADW_CRD401M": """
CREATE TABLE IF NOT EXISTS ADWOWN.TB_ADW_CRD401M (
    CRD_NO      VARCHAR(20)  NOT NULL,
    STD_DT      DATE         NOT NULL,
    EDPS_CSN    VARCHAR(20),
    CRD_DCD     VARCHAR(10),
    ISS_DT      DATE,
    EXPR_DT     DATE,
    MON_USE_AMT NUMERIC(18,2),
    FLG_YN      VARCHAR(1),
    BLNG_BRCD   VARCHAR(10),
    INS_DTM     TIMESTAMP DEFAULT NOW(),
    UPD_DTM     TIMESTAMP,
    PRIMARY KEY (CRD_NO, STD_DT)
);""",

    # ── 외환 ──────────────────────────────────────────────────
    "TB_ADW_FXD501L": """
CREATE TABLE IF NOT EXISTS ADWOWN.TB_ADW_FXD501L (
    DL_NO       VARCHAR(20)  NOT NULL,
    FX_DL_DCD   VARCHAR(10),
    CCY_CD      VARCHAR(10),
    DL_AMT      NUMERIC(18,2),
    DL_RT       NUMERIC(12,6),
    SETL_DT     DATE,
    EDPS_CSN    VARCHAR(20),
    BLNG_BRCD   VARCHAR(10),
    INS_DTM     TIMESTAMP DEFAULT NOW(),
    UPD_DTM     TIMESTAMP,
    PRIMARY KEY (DL_NO)
);""",

    "TB_ADW_FXB502M": """
CREATE TABLE IF NOT EXISTS ADWOWN.TB_ADW_FXB502M (
    CCY_CD      VARCHAR(10)  NOT NULL,
    BASE_DT     DATE         NOT NULL,
    BASE_RT     NUMERIC(12,6),
    BUY_RT      NUMERIC(12,6),
    SELL_RT     NUMERIC(12,6),
    INS_DTM     TIMESTAMP DEFAULT NOW(),
    UPD_DTM     TIMESTAMP,
    PRIMARY KEY (CCY_CD, BASE_DT)
);""",

    # ── 펀드 ──────────────────────────────────────────────────
    "TB_ADW_FND601P": """
CREATE TABLE IF NOT EXISTS ADWOWN.TB_ADW_FND601P (
    FND_ACN     VARCHAR(20)  NOT NULL,
    STD_DT      DATE         NOT NULL,
    EDPS_CSN    VARCHAR(20),
    FUND_CD     VARCHAR(20),
    BAL_AMT     NUMERIC(18,2),
    ORGNL_AMT   NUMERIC(18,2),
    FND_DCD     VARCHAR(10),
    RSK_GRD_CD  VARCHAR(10),
    BLNG_BRCD   VARCHAR(10),
    INS_DTM     TIMESTAMP DEFAULT NOW(),
    UPD_DTM     TIMESTAMP,
    PRIMARY KEY (FND_ACN, STD_DT)
);""",

    "TB_ADW_FND602P": """
CREATE TABLE IF NOT EXISTS ADWOWN.TB_ADW_FND602P (
    FND_ACN     VARCHAR(20)  NOT NULL,
    STD_DT      DATE         NOT NULL,
    EDPS_CSN    VARCHAR(20),
    FUND_CD     VARCHAR(20),
    EVAL_AMT    NUMERIC(18,2),
    ERNS_RT     NUMERIC(10,6),
    FND_DCD     VARCHAR(10),
    BLNG_BRCD   VARCHAR(10),
    INS_DTM     TIMESTAMP DEFAULT NOW(),
    UPD_DTM     TIMESTAMP,
    PRIMARY KEY (FND_ACN, STD_DT)
);""",

    # ── 거래 ──────────────────────────────────────────────────
    "TB_ADW_TRX701L": """
CREATE TABLE IF NOT EXISTS ADWOWN.TB_ADW_TRX701L (
    TR_ID       VARCHAR(30)  NOT NULL,
    TR_DT       DATE         NOT NULL,
    ACN         VARCHAR(20),
    TR_TM       VARCHAR(6),
    TR_AMT      NUMERIC(18,2),
    TR_DCD      VARCHAR(10),
    BLNG_BRCD   VARCHAR(10),
    CHN_CD      VARCHAR(10),
    INS_DTM     TIMESTAMP DEFAULT NOW(),
    UPD_DTM     TIMESTAMP,
    PRIMARY KEY (TR_ID, TR_DT)
);""",

    # ── 보험 ──────────────────────────────────────────────────
    "TB_ADW_INS803M": """
CREATE TABLE IF NOT EXISTS ADWOWN.TB_ADW_INS803M (
    INS_NO      VARCHAR(20)  NOT NULL,
    EDPS_CSN    VARCHAR(20),
    INS_DCD     VARCHAR(10),
    INS_PD_CD   VARCHAR(20),
    INS_STCD    VARCHAR(10),
    CNTR_DT     DATE,
    EFF_DT      DATE,
    EXP_DT      DATE,
    INS_AMT     NUMERIC(18,2),
    BLNG_BRCD   VARCHAR(10),
    INS_DTM     TIMESTAMP DEFAULT NOW(),
    UPD_DTM     TIMESTAMP,
    PRIMARY KEY (INS_NO)
);""",

    # ── 퇴직연금 ──────────────────────────────────────────────
    "TB_ADW_PNB904P": """
CREATE TABLE IF NOT EXISTS ADWOWN.TB_ADW_PNB904P (
    PLAN_NO     VARCHAR(20)  NOT NULL,
    EDPS_CSN    VARCHAR(20)  NOT NULL,
    STD_DT      DATE         NOT NULL,
    TOT_BAL_AMT NUMERIC(18,2),
    PN_DCD      VARCHAR(10),
    EMPLOYER_NO VARCHAR(20),
    BLNG_BRCD   VARCHAR(10),
    INS_DTM     TIMESTAMP DEFAULT NOW(),
    UPD_DTM     TIMESTAMP,
    PRIMARY KEY (PLAN_NO, EDPS_CSN, STD_DT)
);""",

    # ── 리스크 ────────────────────────────────────────────────
    "TB_ADW_RSK1101M": """
CREATE TABLE IF NOT EXISTS ADWOWN.TB_ADW_RSK1101M (
    IND_CD      VARCHAR(20)  NOT NULL,
    STD_DT      DATE         NOT NULL,
    IND_NM      VARCHAR(100),
    IND_VAL     NUMERIC(18,6),
    IND_UNIT    VARCHAR(20),
    LIMIT_VAL   NUMERIC(18,6),
    CALC_DT     DATE,
    INS_DTM     TIMESTAMP DEFAULT NOW(),
    UPD_DTM     TIMESTAMP,
    PRIMARY KEY (IND_CD, STD_DT)
);""",

    # ── 마케팅 ────────────────────────────────────────────────
    "TB_ADW_MKT1201M": """
CREATE TABLE IF NOT EXISTS ADWOWN.TB_ADW_MKT1201M (
    CAMP_CD     VARCHAR(20)  NOT NULL,
    CAMP_NM     VARCHAR(200),
    CAMP_STCD   VARCHAR(10),
    CAMP_TGT_DCD VARCHAR(10),
    START_DT    DATE,
    END_DT      DATE,
    BUDGET_AMT  NUMERIC(18,2),
    BLNG_BRCD   VARCHAR(10),
    INS_DTM     TIMESTAMP DEFAULT NOW(),
    UPD_DTM     TIMESTAMP,
    PRIMARY KEY (CAMP_CD)
);""",

    "TB_ADW_MKT1202M": """
CREATE TABLE IF NOT EXISTS ADWOWN.TB_ADW_MKT1202M (
    CAMP_CD     VARCHAR(20)  NOT NULL,
    EDPS_CSN    VARCHAR(20)  NOT NULL,
    RESP_YN     VARCHAR(1),
    CONTACT_DT  DATE,
    CONTACT_CHN_CD VARCHAR(10),
    INS_DTM     TIMESTAMP DEFAULT NOW(),
    UPD_DTM     TIMESTAMP,
    PRIMARY KEY (CAMP_CD, EDPS_CSN)
);""",

    # ── 재무 ──────────────────────────────────────────────────
    "TB_ADW_FIN1306S": """
CREATE TABLE IF NOT EXISTS ADWOWN.TB_ADW_FIN1306S (
    BLNG_BRCD   VARCHAR(10)  NOT NULL,
    BASE_YM     VARCHAR(6)   NOT NULL,
    PL_ITEM_CD  VARCHAR(20)  NOT NULL,
    AMT         NUMERIC(18,2),
    PL_ITEM_NM  VARCHAR(100),
    CALC_DT     DATE,
    INS_DTM     TIMESTAMP DEFAULT NOW(),
    UPD_DTM     TIMESTAMP,
    PRIMARY KEY (BLNG_BRCD, BASE_YM, PL_ITEM_CD)
);""",

    # ── WM ────────────────────────────────────────────────────
    "TB_ADW_WMB1401M": """
CREATE TABLE IF NOT EXISTS ADWOWN.TB_ADW_WMB1401M (
    EDPS_CSN    VARCHAR(20)  NOT NULL,
    CSM         VARCHAR(100),
    WM_GRD_CD   VARCHAR(20),
    INVEST_PRFL_CD VARCHAR(10),
    PB_EMN      VARCHAR(20),
    TOT_ASSET_AMT NUMERIC(18,2),
    BLNG_BRCD   VARCHAR(10),
    INS_DTM     TIMESTAMP DEFAULT NOW(),
    UPD_DTM     TIMESTAMP,
    PRIMARY KEY (EDPS_CSN)
);""",
}


# ══════════════════════════════════════════════════════════════
# 헬퍼
# ══════════════════════════════════════════════════════════════

def _rnd_date(start: date, end: date) -> date:
    """임의 날짜 생성."""
    delta = (end - start).days
    return start + timedelta(days=random.randint(0, max(delta, 0)))


def _gen_name(cust_type: str) -> str:
    """고객 성명 생성 (PII 대체 가짜 데이터)."""
    if cust_type == "02":
        return random.choice(COMPANY_NAMES)
    return random.choice(SURNAMES) + random.choice(GIVEN_NAMES)


def _gen_tel() -> str:
    """가짜 전화번호."""
    return f"010-{random.randint(1000, 9999)}-{random.randint(1000, 9999)}"


def _gen_email(name: str) -> str:
    """가짜 이메일."""
    slug = name.lower().replace(" ", "").replace("(주)", "corp")
    return f"{slug}_{random.randint(1, 999)}@{random.choice(FAKE_DOMAINS)}"


def _base_ym(d: date) -> str:
    """날짜를 YYYYMM 문자열로 변환."""
    return d.strftime("%Y%m")


# ══════════════════════════════════════════════════════════════
# 정보계 DB 시딩
# ══════════════════════════════════════════════════════════════

def seed_info_db() -> None:
    """ADWOWN 테이블 DDL 생성 + ★ 테이블 데이터 적재."""
    conn = _connect(INFO_DB_CONNINFO)
    cur = conn.cursor()

    try:
        # 스키마 생성
        cur.execute("CREATE SCHEMA IF NOT EXISTS ADWOWN;")

        # stale biz_schema TB_ADW_* 테이블 정리 (구버전 잔재)
        cur.execute("""
            DO $$ DECLARE r RECORD;
            BEGIN
                FOR r IN SELECT tablename FROM pg_tables
                    WHERE schemaname = 'biz_schema' AND tablename LIKE 'tb\\_adw\\_%'
                LOOP
                    EXECUTE 'DROP TABLE IF EXISTS biz_schema.' || r.tablename || ' CASCADE';
                END LOOP;
            END $$;
        """)
        conn.commit()

        # ── 1단계: 모든 테이블 DDL 생성 ──────────────────────
        catalog = parse_table_catalog()
        star_names = {t["name"] for t in catalog if t["star"]}

        for table_info in catalog:
            tbl = table_info["name"]
            if tbl in STAR_DDL:
                cur.execute(STAR_DDL[tbl])
            else:
                ddl = _build_ddl(tbl, table_info["pk_cols"])
                cur.execute(ddl)

        conn.commit()
        total_tbls = len(catalog)
        print(f"  DDL 생성 완료: {total_tbls}개 테이블 (★ {len(star_names)}개 포함)")

        # ── 2단계: TRUNCATE (★ 테이블만, 역순) ───────────────
        star_order = [
            "TB_ADW_MKT1202M", "TB_ADW_MKT1201M",
            "TB_ADW_FIN1306S", "TB_ADW_WMB1401M",
            "TB_ADW_RSK1101M", "TB_ADW_PNB904P",
            "TB_ADW_INS803M",
            "TB_ADW_TRX701L",
            "TB_ADW_FND602P", "TB_ADW_FND601P",
            "TB_ADW_FXB502M", "TB_ADW_FXD501L",
            "TB_ADW_CRD401M",
            "TB_ADW_LNB302M", "TB_ADW_LNB301M",
            "TB_ADW_DEP202S", "TB_ADW_DEP201P",
            "TB_ADW_CSP103M", "TB_ADW_CSC102H", "TB_ADW_CSC101M",
            "TB_ADW_COM002M", "TB_ADW_COM001M",
        ]
        for tbl in star_order:
            cur.execute(f"TRUNCATE TABLE ADWOWN.{tbl} CASCADE")
        conn.commit()

        # ── 3단계: 데이터 적재 ────────────────────────────────
        _insert_com001m(cur)
        _insert_com002m(cur)
        conn.commit()

        edps_csn_list = _insert_customers(cur)
        conn.commit()

        acn_list = _insert_deposits(cur, edps_csn_list)
        conn.commit()

        _insert_loans(cur, edps_csn_list)
        conn.commit()

        _insert_cards(cur, edps_csn_list)
        conn.commit()

        _insert_trx(cur, acn_list)
        conn.commit()

        _insert_fx_deals(cur, edps_csn_list)
        _insert_fx_rates(cur)
        conn.commit()

        _insert_fund_bal(cur, edps_csn_list)
        _insert_fund_eval(cur, edps_csn_list)
        conn.commit()

        _insert_insurance(cur, edps_csn_list)
        conn.commit()

        _insert_pension(cur, edps_csn_list)
        conn.commit()

        _insert_risk_indicators(cur)
        conn.commit()

        _insert_campaigns(cur, edps_csn_list)
        conn.commit()

        _insert_pl_summary(cur)
        conn.commit()

        _insert_wm_customers(cur, edps_csn_list)
        conn.commit()

        # ── 4단계: 비-★ 테이블 자동 시딩 ─────────────────
        seed_non_star_tables(cur, catalog)
        conn.commit()

        # ── 권한 부여: readonly_user ────────────────────────
        ro_user = os.getenv("INFO_DB_USER", "readonly_user")
        cur.execute(
            f"GRANT USAGE ON SCHEMA adwown TO {ro_user};"
        )
        cur.execute(
            f"GRANT SELECT ON ALL TABLES IN SCHEMA adwown TO {ro_user};"
        )
        cur.execute(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA adwown "
            f"GRANT SELECT ON TABLES TO {ro_user};"
        )
        conn.commit()
        print(f"  GRANT: {ro_user} → adwown (USAGE + SELECT)")

        print("\n  → 정보계 DB (ADWOWN) 시딩 완료!")

    except Exception as e:
        conn.rollback()
        print(f"\n  ✗ 정보계 DB 시딩 실패: {e}")
        raise
    finally:
        cur.close()
        conn.close()


# ── 개별 테이블 적재 함수 ─────────────────────────────────────

def _insert_com001m(cur) -> None:
    """TB_ADW_COM001M: 부점정보기본 (20건)."""
    for b in BRANCHES:
        cur.execute(
            "INSERT INTO ADWOWN.TB_ADW_COM001M "
            "(BLNG_BRCD, BR_NM, RGN_CD, RGN_NM, BR_DCD) "
            "VALUES (%s,%s,%s,%s,%s)",
            (b[0], b[1], b[2], b[3], "02"),
        )
    print(f"  TB_ADW_COM001M   : {len(BRANCHES):>5}건")


def _insert_com002m(cur) -> None:
    """TB_ADW_COM002M: 고객등급코드 (5건)."""
    rows = [
        ("01", "VIP", "연간 거래액 10억 이상 또는 자산 5억 이상 고객"),
        ("02", "우수", "연간 거래액 3억 이상 또는 자산 1억 이상 고객"),
        ("03", "일반", "일반 거래 고객"),
        ("04", "잠재", "최근 6개월 거래 없는 고객"),
        ("05", "관리", "연체 또는 사고 이력이 있는 고객"),
    ]
    for r in rows:
        cur.execute(
            "INSERT INTO ADWOWN.TB_ADW_COM002M (GRD_CD, GRD_NM, GRD_DESC) "
            "VALUES (%s,%s,%s)", r)
    print(f"  TB_ADW_COM002M   : {len(rows):>5}건")


def _insert_customers(cur) -> list[str]:
    """고객 3개 테이블 적재 (500명).

    TYPE-1: CSC101M / CSC102H / CSP103M 컬럼 70~80% 겹침
    TYPE-2: CUS_GRD_CD에 99/NULL (~3%)
    TYPE-4: JOIN_DT vs RGST_DT, CUS_GRD_CD vs MKT_GRD_CD
    """
    N_CUST = 500
    edps_csn_list: list[str] = []
    type2_cnt = 0

    for i in range(1, N_CUST + 1):
        csn = f"EDPS{i:07d}"
        ctype = random.choices(CUST_TYPES, weights=[75, 15, 10])[0]
        name = _gen_name(ctype)
        join_dt = _rnd_date(TODAY - timedelta(days=365 * 5), TODAY)
        brch_cd = random.choice(BRANCHES)[0]
        gender = random.choice(GENDERS) if ctype != "02" else None
        age_grp = random.choice(AGE_GROUPS) if ctype != "02" else None

        # TYPE-2: ~3% 미정의 등급
        if random.random() < IMPERFECTION_RATE:
            cus_grd = random.choice(["99", None])
            type2_cnt += 1
        else:
            cus_grd = random.choices(
                CUST_GRADES, weights=[
                    5, 15, 40, 25, 15])[0]

        # TYPE-4: 마케팅 등급은 영업 등급과 별개 기준
        mkt_grd = random.choice(MKT_GRADES)

        tel = _gen_tel() if ctype != "02" else None
        email = _gen_email(name)

        # CSC101M (현재 기준)
        cur.execute(
            "INSERT INTO ADWOWN.TB_ADW_CSC101M "
            "(EDPS_CSN, STD_DT, CSM, CUS_DCD, JOIN_DT, BLNG_BRCD, "
            "GNDR_DCD, AGE_GRP_CD, CUS_GRD_CD, TEL_NO, EMAIL_ADR) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (csn, STD_DT, name, ctype, join_dt, brch_cd,
             gender, age_grp, cus_grd, tel, email),
        )

        # CSC102H (이력 — TYPE-4: RGST_DT = JOIN_DT, 컬럼명만 다름)
        addr = (
            random.choice(FAKE_ADDRS) if ctype != "02"
            else f"서울시 영등포구 여의도동 {random.randint(1, 100)}"
        )
        cur.execute(
            "INSERT INTO ADWOWN.TB_ADW_CSC102H "
            "(EDPS_CSN, STD_DT, CSM, CUS_DCD, RGST_DT, BLNG_BRCD, "
            "GNDR_DCD, AGE_GRP_CD, CUS_GRD_CD, CUS_ADR, PHONE_NO) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (csn, STD_DT, name, ctype, join_dt, brch_cd,
             gender, age_grp, cus_grd, addr, tel),
        )

        # CSP103M (마케팅 전용 — TYPE-4: MKT_GRD_CD ≠ CUS_GRD_CD)
        pref_ch = random.choice(PREF_CHANNELS)
        cur.execute(
            "INSERT INTO ADWOWN.TB_ADW_CSP103M "
            "(EDPS_CSN, CSM, CUS_DCD, MKT_GRD_CD, BLNG_BRCD, "
            "AGE_GRP_CD, GNDR_DCD, PREF_CHN_DCD) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (csn, name, ctype, mkt_grd, brch_cd, age_grp, gender, pref_ch),
        )

        edps_csn_list.append(csn)

    print(f"  TB_ADW_CSC101M   : {N_CUST:>5}건  (TYPE-2 미정의 등급 {type2_cnt}건)")
    print(f"  TB_ADW_CSC102H   : {N_CUST:>5}건")
    print(f"  TB_ADW_CSP103M   : {N_CUST:>5}건")
    return edps_csn_list


def _insert_deposits(cur, edps_csn_list: list[str]) -> list[str]:
    """수신 계좌 적재 (600건).

    TYPE-2: ACT_DCD에 05/99 (~3%)
    TYPE-4: BAL_AMT(T+0) ≠ TOT_BAL_AMT(T+1)
    """
    N_ACCT = 600
    acn_list: list[str] = []
    type2_cnt = 0

    for i in range(1, N_ACCT + 1):
        acn = f"ACC{i:08d}"
        csn = random.choice(edps_csn_list)
        open_dt = _rnd_date(TODAY - timedelta(days=365 * 3), TODAY)
        brch_cd = random.choice(BRANCHES)[0]
        prod = random.choice(DEPOSIT_PRODUCTS)
        bal_amt = random.randint(10, 50000) * 10000
        int_rate = round(random.uniform(0.1, 4.5), 4)
        stat = random.choices(["01", "02", "03"], weights=[80, 12, 8])[0]

        # TYPE-2
        if random.random() < IMPERFECTION_RATE:
            act_dcd = random.choice(["05", "99"])
            type2_cnt += 1
        else:
            act_dcd = random.choice(ACCT_TYPES)

        cur.execute(
            "INSERT INTO ADWOWN.TB_ADW_DEP201P "
            "(ACN, STD_DT, EDPS_CSN, ACT_DCD, BAL_AMT, OPEN_DT, "
            "BLNG_BRCD, PD_CD, PD_NM, APLY_RT, ACT_STCD) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (acn, STD_DT, csn, act_dcd, bal_amt, open_dt,
             brch_cd, prod[0], prod[1], int_rate, stat),
        )

        # TYPE-4: TOT_BAL_AMT = BAL_AMT ± 최대 1영업일 차이
        daily_change = random.randint(-500, 500) * 10000
        tot_bal_amt = max(0, bal_amt + daily_change)
        base_dt = STD_DT - timedelta(days=1)

        cur.execute(
            "INSERT INTO ADWOWN.TB_ADW_DEP202S "
            "(ACN, BASE_DT, EDPS_CSN, ACT_DCD, TOT_BAL_AMT, OPEN_DT, "
            "BLNG_BRCD, PD_CD) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (acn, base_dt, csn, act_dcd, tot_bal_amt, open_dt, brch_cd, prod[0]),  # noqa: E501
        )

        acn_list.append(acn)

    print(f"  TB_ADW_DEP201P   : {N_ACCT:>5}건  (TYPE-2 미정의 유형 {type2_cnt}건)")
    print(f"  TB_ADW_DEP202S   : {N_ACCT:>5}건")
    return acn_list


def _insert_loans(cur, edps_csn_list: list[str]) -> None:
    """여신 적재 (800건).

    TYPE-2: OVDU_GRD_CD에 F/Z, LN_STCD에 0A (~3%)
    TYPE-4: LN_EXC_AMT(실행) ≠ LN_APR_AMT(승인)
    """
    N_LOAN = 800
    type2_stat = type2_ovdu = 0

    for i in range(1, N_LOAN + 1):
        ln_no = f"LN{i:08d}"
        csn = random.choice(edps_csn_list)
        loan_dt = _rnd_date(TODAY - timedelta(days=365 * 3), TODAY)
        mtrty_dt = loan_dt + \
            timedelta(days=random.choice([365, 730, 1095, 1825]))
        brch_cd = random.choice(BRANCHES)[0]
        ltype = random.choices(LOAN_TYPES, weights=[40, 45, 15])[0]

        if ltype == "01":
            loan_amt = random.randint(5, 100) * 1_000_000
            rate = round(random.uniform(4.0, 12.0), 2)
        elif ltype == "02":
            loan_amt = random.randint(50, 1000) * 1_000_000
            rate = round(random.uniform(2.5, 6.0), 2)
        else:
            loan_amt = random.randint(10, 300) * 1_000_000
            rate = round(random.uniform(3.0, 8.0), 2)

        ln_bal_amt = int(loan_amt * (1 - random.uniform(0.0, 0.7)))

        # TYPE-4: 승인금액 > 실행금액
        ln_apr_amt = int(loan_amt * random.uniform(1.0, 1.3))
        appr_dt = loan_dt - timedelta(days=random.randint(1, 30))

        is_overdue = random.random() < 0.08
        ovdu_days = random.randint(1, 180) if is_overdue else 0
        ovdu_amt = int(
            ln_bal_amt *
            random.uniform(
                0.01,
                0.3)) if is_overdue else 0

        # TYPE-2: OVDU_GRD_CD
        if is_overdue:
            if random.random() < IMPERFECTION_RATE * 5:
                ovdu_grd = random.choice(["F", "Z"])
                type2_ovdu += 1
            else:
                ovdu_grd = random.choice(["A", "B", "C", "D", "E"])
        else:
            ovdu_grd = None

        # TYPE-2: LN_STCD
        if random.random() < IMPERFECTION_RATE:
            ln_stcd = "0A"
            type2_stat += 1
        else:
            ln_stcd = random.choices(["01", "02", "03", "04", "05"], weights=[
                                     60, 15, 10, 10, 5])[0]

        ln_puse = random.choice(LOAN_PUSE_CODES) if ltype in (
            "02", "03") else None
        cltr_dcd = random.choice(CLTR_TYPE_CODES) if ltype == "02" else None

        cur.execute(
            "INSERT INTO ADWOWN.TB_ADW_LNB301M "
            "(LN_NO, STD_DT, EDPS_CSN, LN_EXC_AMT, LN_BAL_AMT, LN_DT, MTRTY_DT, "  # noqa: E501
            "APLY_RT, LN_DCD, LN_STCD, OVDU_GRD_CD, OVDU_DY_CN, OVDU_AMT, BLNG_BRCD) "  # noqa: E501
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (ln_no, STD_DT, csn, loan_amt, ln_bal_amt, loan_dt, mtrty_dt,
             rate, ltype, ln_stcd, ovdu_grd, ovdu_days, ovdu_amt, brch_cd),
        )

        cur.execute(
            "INSERT INTO ADWOWN.TB_ADW_LNB302M "
            "(LN_NO, EDPS_CSN, LN_APR_AMT, APPR_DT, LN_DCD, "
            "LN_PUSE_CD, CLTR_DCD, APLY_RT, MTRTY_DT, BLNG_BRCD) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (ln_no, csn, ln_apr_amt, appr_dt, ltype,
             ln_puse, cltr_dcd, rate, mtrty_dt, brch_cd),
        )

    print(f"  TB_ADW_LNB301M   : {N_LOAN:>5}건  "
          f"(TYPE-2 LN_STCD={type2_stat}, OVDU_GRD={type2_ovdu})")
    print(f"  TB_ADW_LNB302M   : {N_LOAN:>5}건")


def _insert_cards(cur, edps_csn_list: list[str]) -> None:
    """카드 적재 (300건). TYPE-2: CRD_DCD에 04 (~3%)."""
    N_CARD = 300
    type2_cnt = 0

    for i in range(1, N_CARD + 1):
        crd_no = f"CARD{i:08d}"
        csn = random.choice(edps_csn_list)
        iss_dt = _rnd_date(TODAY - timedelta(days=365 * 3), TODAY)
        expr_dt = iss_dt + \
            timedelta(days=random.choice([365, 730, 1095, 1825]))
        mon_use = random.randint(0, 5000) * 10000
        brch_cd = random.choice(BRANCHES)[0]

        if random.random() < IMPERFECTION_RATE:
            crd_dcd = "04"
            type2_cnt += 1
        else:
            crd_dcd = random.choice(CARD_TYPES)

        flg_yn = random.choices(["Y", "N"], weights=[80, 20])[0]

        cur.execute(
            "INSERT INTO ADWOWN.TB_ADW_CRD401M "
            "(CRD_NO, STD_DT, EDPS_CSN, CRD_DCD, ISS_DT, EXPR_DT, "
            "MON_USE_AMT, FLG_YN, BLNG_BRCD) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (crd_no, STD_DT, csn, crd_dcd, iss_dt, expr_dt,
             mon_use, flg_yn, brch_cd),
        )

    print(f"  TB_ADW_CRD401M   : {N_CARD:>5}건  (TYPE-2 미정의 유형 {type2_cnt}건)")


def _insert_trx(cur, acn_list: list[str]) -> None:
    """거래 내역 적재 (3000건). TYPE-2: TR_DCD에 200~299/999 (~3%)."""
    N_TRX = 3000
    type2_cnt = 0
    trx_start = date(2025, 4, 1)

    for i in range(1, N_TRX + 1):
        tr_id = f"TRX{i:010d}"
        acn = random.choice(acn_list)
        tr_dt = _rnd_date(trx_start, TODAY)
        tr_tm = f"{
            random.randint(
                9,
                17):02d}{
            random.randint(
                0,
                59):02d}{
                    random.randint(
                        0,
                        59):02d}"
        tr_amt = random.randint(1, 5000) * 10000
        brch_cd = random.choice(BRANCHES)[0]
        chn_cd = random.choice(CHANNELS)

        if random.random() < IMPERFECTION_RATE:
            tr_dcd = random.choice([str(random.randint(200, 299)), "999"])
            type2_cnt += 1
        else:
            tr_dcd = str(random.randint(100, 199))

        cur.execute(
            "INSERT INTO ADWOWN.TB_ADW_TRX701L "
            "(TR_ID, TR_DT, ACN, TR_TM, TR_AMT, TR_DCD, BLNG_BRCD, CHN_CD) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (tr_id, tr_dt, acn, tr_tm, tr_amt, tr_dcd, brch_cd, chn_cd),
        )

    print(f"  TB_ADW_TRX701L   : {N_TRX:>5}건  (TYPE-2 미정의 유형 {type2_cnt}건)")


def _insert_fx_deals(cur, edps_csn_list: list[str]) -> None:
    """외환딜 적재 (200건). TYPE-2: FX_DL_DCD=06, CCY_CD=CNH (~3%)."""
    N = 200
    type2_cnt = 0

    for i in range(1, N + 1):
        dl_no = f"FXD{i:08d}"
        csn = random.choice(edps_csn_list)
        brch_cd = random.choice(BRANCHES)[0]
        setl_dt = _rnd_date(
            TODAY -
            timedelta(
                days=180),
            TODAY +
            timedelta(
                days=30))

        if random.random() < IMPERFECTION_RATE:
            fx_dl_dcd = "06"
            ccy_cd = FX_IMPERFECT_CCY
            type2_cnt += 1
        else:
            fx_dl_dcd = random.choice(FX_DEAL_TYPES)
            ccy_cd = random.choice(CURRENCIES)

        dl_rt = round(
            random.uniform(
                900,
                1500) if ccy_cd == "KRW" else random.uniform(
                0.5,
                1800),
            4)
        dl_amt = random.randint(100, 5000) * 1000

        cur.execute(
            "INSERT INTO ADWOWN.TB_ADW_FXD501L "
            "(DL_NO, FX_DL_DCD, CCY_CD, DL_AMT, DL_RT, SETL_DT, "
            "EDPS_CSN, BLNG_BRCD) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (dl_no, fx_dl_dcd, ccy_cd, dl_amt, dl_rt, setl_dt, csn, brch_cd),
        )

    print(f"  TB_ADW_FXD501L   : {N:>5}건  (TYPE-2 미정의 코드 {type2_cnt}건)")


def _insert_fx_rates(cur) -> None:
    """환율정보 적재 (100건). 통화별 × 날짜."""
    N = 0
    base_rates = {
        "USD": 1330.0,
        "EUR": 1450.0,
        "JPY": 8.9,
        "GBP": 1680.0,
        "CNY": 184.0}
    dates = [TODAY - timedelta(days=d) for d in range(20)]

    for ccy, base_rt in base_rates.items():
        for dt in dates:
            rt = round(base_rt * random.uniform(0.97, 1.03), 4)
            cur.execute(
                "INSERT INTO ADWOWN.TB_ADW_FXB502M "
                "(CCY_CD, BASE_DT, BASE_RT, BUY_RT, SELL_RT) "
                "VALUES (%s,%s,%s,%s,%s)",
                (ccy, dt, rt, round(rt * 0.99, 4), round(rt * 1.01, 4)),
            )
            N += 1

    print(f"  TB_ADW_FXB502M   : {N:>5}건")


def _insert_fund_bal(cur, edps_csn_list: list[str]) -> None:
    """펀드잔고 적재 (300건). TYPE-2: FND_DCD=99, RSK_GRD_CD=0 (~3%)."""
    N = 300
    type2_cnt = 0

    for i in range(1, N + 1):
        fnd_acn = f"FND{i:08d}"
        csn = random.choice(edps_csn_list)
        fund_cd, _ = random.choice(FUND_PRODUCTS)
        brch_cd = random.choice(BRANCHES)[0]
        orgnl_amt = random.randint(100, 5000) * 10000
        bal_amt = int(orgnl_amt * random.uniform(0.8, 1.5))

        if random.random() < IMPERFECTION_RATE:
            fnd_dcd = "99"
            rsk_grd = "0"
            type2_cnt += 1
        else:
            fnd_dcd = random.choice(FUND_TYPES)
            rsk_grd = random.choice(RISK_GRADES)

        cur.execute(
            "INSERT INTO ADWOWN.TB_ADW_FND601P "
            "(FND_ACN, STD_DT, EDPS_CSN, FUND_CD, BAL_AMT, ORGNL_AMT, "
            "FND_DCD, RSK_GRD_CD, BLNG_BRCD) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (fnd_acn, STD_DT, csn, fund_cd, bal_amt, orgnl_amt,
             fnd_dcd, rsk_grd, brch_cd),
        )

    print(f"  TB_ADW_FND601P   : {N:>5}건  (TYPE-2 미정의 코드 {type2_cnt}건)")


def _insert_fund_eval(cur, edps_csn_list: list[str]) -> None:
    """펀드평가 적재 (300건). TYPE-4: EVAL_AMT ≠ BAL_AMT (FND601P), 다른 기준."""
    N = 300
    for i in range(1, N + 1):
        fnd_acn = f"FND{i:08d}"
        csn = random.choice(edps_csn_list)
        fund_cd, _ = random.choice(FUND_PRODUCTS)
        brch_cd = random.choice(BRANCHES)[0]
        orgnl_amt = random.randint(100, 5000) * 10000
        # TYPE-4: 평가금액은 시가 기준, 잔고(원금)와 다름
        eval_amt = int(orgnl_amt * random.uniform(0.7, 1.8))
        erns_rt = round((eval_amt - orgnl_amt) / orgnl_amt, 6)
        fnd_dcd = random.choice(FUND_TYPES)

        cur.execute(
            "INSERT INTO ADWOWN.TB_ADW_FND602P "
            "(FND_ACN, STD_DT, EDPS_CSN, FUND_CD, EVAL_AMT, ERNS_RT, "
            "FND_DCD, BLNG_BRCD) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (fnd_acn, STD_DT, csn, fund_cd, eval_amt, erns_rt, fnd_dcd, brch_cd),  # noqa: E501
        )

    print(f"  TB_ADW_FND602P   : {N:>5}건")


def _insert_insurance(cur, edps_csn_list: list[str]) -> None:
    """보험계약 적재 (200건). TYPE-2: INS_DCD=E (~3%)."""
    N = 200
    type2_cnt = 0

    for i in range(1, N + 1):
        ins_no = f"INS{i:08d}"
        csn = random.choice(edps_csn_list)
        brch_cd = random.choice(BRANCHES)[0]
        cntr_dt = _rnd_date(TODAY - timedelta(days=365 * 5), TODAY)
        eff_dt = cntr_dt
        exp_dt = cntr_dt + timedelta(days=random.choice([365, 730, 3650]))
        ins_amt = random.randint(10, 500) * 1_000_000

        if random.random() < IMPERFECTION_RATE:
            ins_dcd = "E"   # TYPE-2: 미정의 보험유형
            type2_cnt += 1
        else:
            ins_dcd = random.choice(INS_TYPES)

        ins_stcd = random.choices(["01", "02", "03"], weights=[75, 15, 10])[0]
        ins_pd_cd = f"IP{random.randint(1, 20):03d}"

        cur.execute(
            "INSERT INTO ADWOWN.TB_ADW_INS803M "
            "(INS_NO, EDPS_CSN, INS_DCD, INS_PD_CD, INS_STCD, "
            "CNTR_DT, EFF_DT, EXP_DT, INS_AMT, BLNG_BRCD) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (ins_no, csn, ins_dcd, ins_pd_cd, ins_stcd,
             cntr_dt, eff_dt, exp_dt, ins_amt, brch_cd),
        )

    print(f"  TB_ADW_INS803M   : {N:>5}건  (TYPE-2 미정의 INS_DCD {type2_cnt}건)")


def _insert_pension(cur, edps_csn_list: list[str]) -> None:
    """퇴직연금 잔고 적재 (200건). TYPE-2: PN_DCD=HYB (~3%)."""
    N = 200
    type2_cnt = 0

    for i in range(1, N + 1):
        plan_no = f"PLAN{i:06d}"
        csn = random.choice(edps_csn_list)
        brch_cd = random.choice(BRANCHES)[0]
        employer_no = f"EMP{random.randint(1, 50):04d}"
        tot_bal_amt = random.randint(100, 10000) * 10000

        if random.random() < IMPERFECTION_RATE:
            pn_dcd = "HYB"  # TYPE-2: 미정의 코드
            type2_cnt += 1
        else:
            pn_dcd = random.choice(PLAN_TYPES)

        cur.execute(
            "INSERT INTO ADWOWN.TB_ADW_PNB904P "
            "(PLAN_NO, EDPS_CSN, STD_DT, TOT_BAL_AMT, PN_DCD, "
            "EMPLOYER_NO, BLNG_BRCD) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (plan_no, csn, STD_DT, tot_bal_amt, pn_dcd, employer_no, brch_cd),
        )

    print(f"  TB_ADW_PNB904P   : {N:>5}건  (TYPE-2 미정의 PN_DCD {type2_cnt}건)")


def _insert_risk_indicators(cur) -> None:
    """리스크지표 적재 (100건). IND_VAL은 실무 근사값."""
    IND_CONFIG: dict[str, tuple[float, float, float, str]] = {
        "BIS_RATIO": (14.0, 16.0, 8.0, "%"),
        "LCR": (110.0, 130.0, 100.0, "%"),
        "NSFR": (108.0, 125.0, 100.0, "%"),
        "NIM": (1.8, 2.5, 0.0, "%"),
        "ROA": (0.5, 0.9, 0.0, "%"),
        "ROE": (7.0, 10.0, 0.0, "%"),
        "NPL_RATIO": (0.5, 1.2, 0.0, "%"),
        "CVA": (50.0, 200.0, 0.0, "억원"),
        "LTV_AVG": (55.0, 70.0, 0.0, "%"),
        "DSR_AVG": (30.0, 45.0, 0.0, "%"),
    }
    dates = [TODAY - timedelta(days=d * 30) for d in range(10)]
    N = 0

    for ind_cd, ind_nm in RISK_IND_CODES:
        lo, hi, limit_val, unit = IND_CONFIG.get(ind_cd, (0.0, 100.0, 0.0, ""))
        for dt in dates:
            ind_val = round(random.uniform(lo, hi), 4)
            cur.execute(
                "INSERT INTO ADWOWN.TB_ADW_RSK1101M "
                "(IND_CD, STD_DT, IND_NM, IND_VAL, IND_UNIT, LIMIT_VAL, CALC_DT) "  # noqa: E501
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (ind_cd, dt, ind_nm, ind_val, unit, limit_val, dt),
            )
            N += 1

    print(f"  TB_ADW_RSK1101M  : {N:>5}건")


def _insert_campaigns(cur, edps_csn_list: list[str]) -> None:
    """캠페인 마스터/대상고객 적재 (50캠페인, 500건 대상).

    TYPE-2: CAMP_STCD=04/99 (~3%)
    """
    n_camp = 50
    type2_cnt = 0

    for i in range(1, n_camp + 1):
        camp_cd = f"CAMP{i:04d}"
        camp_nm = f"캠페인_{
            i:04d}_{
            '우량고객유치' if i %
            3 == 0 else '이탈방지' if i %
            3 == 1 else '교차판매'}"
        start_dt = _rnd_date(TODAY - timedelta(days=180), TODAY)
        end_dt = start_dt + timedelta(days=random.choice([30, 60, 90]))
        budget = random.randint(100, 5000) * 100000
        brch_cd = random.choice(BRANCHES)[0]

        if random.random() < IMPERFECTION_RATE:
            camp_stcd = random.choice(["04", "99"])  # TYPE-2
            type2_cnt += 1
        else:
            camp_stcd = random.choices(
                ["01", "02", "03"], weights=[20, 50, 30])[0]

        cur.execute(
            "INSERT INTO ADWOWN.TB_ADW_MKT1201M "
            "(CAMP_CD, CAMP_NM, CAMP_STCD, START_DT, END_DT, BUDGET_AMT, BLNG_BRCD) "  # noqa: E501
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (camp_cd, camp_nm, camp_stcd, start_dt, end_dt, budget, brch_cd),
        )

    print(
        f"  TB_ADW_MKT1201M  : {
            n_camp:>5}건  (TYPE-2 미정의 CAMP_STCD {type2_cnt}건)")

    # 대상 고객 (각 캠페인당 평균 10명)
    n_target = 0
    for i in range(1, n_camp + 1):
        camp_cd = f"CAMP{i:04d}"
        target_csns = random.sample(edps_csn_list, min(10, len(edps_csn_list)))
        for csn in target_csns:
            resp_yn = random.choices(["Y", "N"], weights=[20, 80])[0]
            contact_dt = _rnd_date(TODAY - timedelta(days=90), TODAY)
            chn_cd = random.choice(CHANNELS)
            cur.execute(
                "INSERT INTO ADWOWN.TB_ADW_MKT1202M "
                "(CAMP_CD, EDPS_CSN, RESP_YN, CONTACT_DT, CONTACT_CHN_CD) "
                "VALUES (%s,%s,%s,%s,%s)",
                (camp_cd, csn, resp_yn, contact_dt, chn_cd),
            )
            n_target += 1

    print(f"  TB_ADW_MKT1202M  : {n_target:>5}건")


def _insert_pl_summary(cur) -> None:
    """손익요약 적재 (200건). 지점 × 기준년월 × 손익항목."""
    N = 0
    months = [_base_ym(TODAY - timedelta(days=d * 30)) for d in range(10)]

    for brch in BRANCHES:
        brch_cd = brch[0]
        for base_ym in months:
            for item_cd in PL_ITEM_CODES:
                amt = random.randint(-500, 2000) * 1_000_000
                pl_nm = f"손익항목_{item_cd}"
                cur.execute(
                    "INSERT INTO ADWOWN.TB_ADW_FIN1306S "
                    "(BLNG_BRCD, BASE_YM, PL_ITEM_CD, AMT, PL_ITEM_NM, CALC_DT) "  # noqa: E501
                    "VALUES (%s,%s,%s,%s,%s,%s)",
                    (brch_cd, base_ym, item_cd, amt, pl_nm, TODAY),
                )
                N += 1

    print(f"  TB_ADW_FIN1306S  : {N:>5}건")


def _insert_wm_customers(cur, edps_csn_list: list[str]) -> None:
    """WM 고객정보 적재 (100건). TYPE-2: INVEST_PRFL_CD=0 (~3%)."""
    wm_csns = random.sample(edps_csn_list, min(100, len(edps_csn_list)))
    type2_cnt = 0

    for csn in wm_csns:
        wm_grd = random.choice(WM_GRADES)
        pb_emn = f"PB{random.randint(1, 20):04d}"
        tot_asset = random.randint(500, 100000) * 1_000_000
        brch_cd = random.choice(BRANCHES)[0]

        if random.random() < IMPERFECTION_RATE:
            invest_prfl = "0"  # TYPE-2: 미평가
            type2_cnt += 1
        else:
            invest_prfl = str(random.randint(1, 5))

        cur.execute(
            "INSERT INTO ADWOWN.TB_ADW_WMB1401M "
            "(EDPS_CSN, WM_GRD_CD, INVEST_PRFL_CD, PB_EMN, TOT_ASSET_AMT, BLNG_BRCD) "  # noqa: E501
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (csn, wm_grd, invest_prfl, pb_emn, tot_asset, brch_cd),
        )

    print(
        f"  TB_ADW_WMB1401M  : {
            len(wm_csns):>5}건  (TYPE-2 미평가 {type2_cnt}건)")


# ══════════════════════════════════════════════════════════════
# 비-★ 테이블 자동 시딩 (메타데이터 기반)
# ══════════════════════════════════════════════════════════════

try:
    from seed_elasticsearch import CODE_META_DOCS as _CODE_META
except ImportError:
    _CODE_META: list[dict] = []  # type: ignore[no-redef]

# code_field → [유효값] 빠른 검색
_CODE_LOOKUP: dict[str, list[str]] = {
    doc["code_field"]: list(doc["codes"].keys()) for doc in _CODE_META
}

# 도메인 접두사(테이블명 7~10자) → (최소금액, 최대금액)
_DOMAIN_AMT: dict[str, tuple[int, int]] = {
    "COM": (1_000, 100_000_000),
    "CSC": (100_000, 5_000_000_000),
    "CSP": (100_000, 5_000_000_000),
    "CUS": (100_000, 5_000_000_000),
    "DEP": (100_000, 5_000_000_000),
    "DEA": (100_000, 5_000_000_000),
    "DEP": (100_000, 5_000_000_000),
    "LNB": (1_000_000, 10_000_000_000),
    "LNR": (1_000_000, 10_000_000_000),
    "LNA": (1_000_000, 10_000_000_000),
    "LNC": (1_000_000, 10_000_000_000),
    "CRD": (10_000, 50_000_000),
    "CRU": (10_000, 50_000_000),
    "CRD": (10_000, 50_000_000),
    "FXD": (1_000, 100_000_000),
    "FXB": (1_000, 100_000_000),
    "TRD": (1_000, 100_000_000),
    "FND": (100_000, 10_000_000_000),
    "TRS": (100_000, 10_000_000_000),
    "ELS": (100_000, 10_000_000_000),
    "BND": (100_000, 10_000_000_000),
    "TRX": (1_000, 100_000_000),
    "TXP": (1_000, 100_000_000),
    "INS": (100_000, 1_000_000_000),
    "PNB": (100_000, 1_000_000_000),
    "PNI": (100_000, 1_000_000_000),
    "DGB": (1_000, 10_000_000),
    "DGA": (1_000, 10_000_000),
    "MYD": (1_000, 10_000_000),
    "RSK": (0, 100),
    "AML": (100_000, 10_000_000_000),
    "FDS": (100_000, 10_000_000_000),
    "CMP": (100_000, 10_000_000_000),
    "MKT": (10_000, 100_000_000),
    "CRM": (10_000, 100_000_000),
    "FIN": (1_000_000, 500_000_000_000),
    "GLB": (1_000_000, 500_000_000_000),
    "BUD": (1_000_000, 500_000_000_000),
    "WMB": (1_000_000, 50_000_000_000),
    "WMR": (1_000_000, 50_000_000_000),
}

# 처리 순서: 코드 → 마스터 → 상세 → 스냅샷 → 집계 → 이력 → 내역 → 로그
_TYPE_ORDER = {
    "C": 0, "M": 1, "D": 2, "P": 3,
    "S": 4, "H": 5, "L": 6, "G": 7,
}

# 유형별 목표 행 수 (min, max)
_TYPE_ROWS: dict[str, tuple[int, int]] = {
    "C": (5, 30),       # 코드: 유효값 수만큼
    "M": (100, 150),    # 마스터: 100+
    "D": (200, 500),    # 상세: 마스터 × 2~5
    "L": (500, 1000),   # 내역: 최대 1000
    "H": (300, 800),    # 이력: 3개월~1년
    "S": (36, 60),      # 집계: 월별 3~5년
    "P": (100, 300),    # 스냅샷: 일별 3개월
    "G": (200, 500),    # 로그: 시스템 로그
}

# FK-like 컬럼의 별칭 → 값 풀 키 매핑
_POOL_ALIAS: dict[str, str] = {
    "BRCD": "BLNG_BRCD",
    "REL_CSN": "EDPS_CSN",
    "AUTH_CSN": "EDPS_CSN",
    "LINKED_ACN": "ACN",
    "NOSTRO_ACN": "ACN",
    "MAIN_CRD_NO": "CRD_NO",
    "FAMILY_CRD_NO": "CRD_NO",
    "ELS_ACN": "ACN",
    "ISA_ACN": "ACN",
    "IRP_ACN": "ACN",
    "PB_EMN": "EDPS_CSN",
}


def _collect_star_values(cur) -> dict[str, list]:
    """★ 테이블에서 주요 PK 값을 수집."""
    pools: dict[str, list] = {}
    qs = [
        ("EDPS_CSN",
         "SELECT DISTINCT EDPS_CSN FROM ADWOWN.TB_ADW_CSC101M"),
        ("ACN",
         "SELECT DISTINCT ACN FROM ADWOWN.TB_ADW_DEP201P"),
        ("LN_NO",
         "SELECT DISTINCT LN_NO FROM ADWOWN.TB_ADW_LNB301M"),
        ("CRD_NO",
         "SELECT DISTINCT CRD_NO FROM ADWOWN.TB_ADW_CRD401M"),
        ("BLNG_BRCD",
         "SELECT DISTINCT BLNG_BRCD FROM ADWOWN.TB_ADW_COM001M"),
        ("GRD_CD",
         "SELECT DISTINCT GRD_CD FROM ADWOWN.TB_ADW_COM002M"),
        ("DL_NO",
         "SELECT DISTINCT DL_NO FROM ADWOWN.TB_ADW_FXD501L"),
        ("FND_ACN",
         "SELECT DISTINCT FND_ACN FROM ADWOWN.TB_ADW_FND601P"),
        ("INS_NO",
         "SELECT DISTINCT INS_NO FROM ADWOWN.TB_ADW_INS803M"),
        ("PLAN_NO",
         "SELECT DISTINCT PLAN_NO FROM ADWOWN.TB_ADW_PNB904P"),
        ("CAMP_CD",
         "SELECT DISTINCT CAMP_CD FROM ADWOWN.TB_ADW_MKT1201M"),
        ("IND_CD",
         "SELECT DISTINCT IND_CD FROM ADWOWN.TB_ADW_RSK1101M"),
        ("CCY_CD",
         "SELECT DISTINCT CCY_CD FROM ADWOWN.TB_ADW_FXB502M"),
    ]
    for col, sql in qs:
        cur.execute(sql)
        pools[col] = [r[0] for r in cur.fetchall()]
    # BRCD는 BLNG_BRCD의 별칭
    pools["BRCD"] = list(pools["BLNG_BRCD"])
    return pools


def _resolve_cols(
    table_info: dict,
) -> tuple[list[tuple[str, str]], bool]:
    """비-★ 테이블의 (컬럼명, 타입) 리스트를 재구성.

    _build_ddl()의 _get_extra_cols()와 동일 로직 사용.
    BIGSERIAL·TIMESTAMP 컬럼 제외.
    Returns: (columns, has_bigserial)
    """
    cols: list[tuple[str, str]] = []
    used: set[str] = set()
    has_bs = False

    for col in table_info["pk_cols"]:
        ctype = _pk_col_type(col)
        if ctype == "BIGSERIAL":
            has_bs = True
            continue
        cols.append((col, ctype))
        used.add(col)

    extras = _get_extra_cols(table_info["name"], used)
    for name, typ in extras:
        if "TIMESTAMP" in typ.upper():
            continue
        base = typ.split(" DEFAULT")[0].strip()
        cols.append((name, base))
        used.add(name)

    return cols, has_bs


def _gen_col_value(
    col: str, ctype: str, domain: str,
    pools: dict[str, list], idx: int,
) -> object:
    """컬럼 이름·타입 패턴에 따른 값 생성."""
    # FK 별칭 해소 후 값 풀 참조
    pool_key = _POOL_ALIAS.get(col, col)
    if pool_key in pools and pools[pool_key]:
        return random.choice(pools[pool_key])

    # 코드 메타에 정의된 코드 컬럼 (24개)
    if col in _CODE_LOOKUP:
        if random.random() < IMPERFECTION_RATE:
            return "99"
        return random.choice(_CODE_LOOKUP[col])

    # 보충 코드값 (_EXTRA_CODES)
    if col in _EXTRA_CODES and _EXTRA_CODES[col]:
        return random.choice(_EXTRA_CODES[col])

    # _YN 플래그
    if col.endswith("_YN") or col == "RESP_YN":
        return random.choice(["Y", "N"])

    # 코드 계열 (code_meta/extra 미등록)
    if col.endswith(("_DCD", "_STCD")):
        return f"{random.randint(1, 5):02d}"
    if col.endswith("_CD") and "VARCHAR" in ctype:
        return f"{random.randint(1, 10):02d}"

    # 고객명 (CSM 컬럼)
    if col == "CSM":
        return (
            f"{random.choice(SURNAMES)}"
            f"{random.choice(GIVEN_NAMES)}")

    # 상품명 (PD_NM) — 도메인별 상품명
    if col == "PD_NM":
        group = _DOMAIN_GROUP.get(domain, "COM")
        names = _DOMAIN_PRODUCT_NAMES.get(group, _PRODUCT_NAMES)
        return random.choice(names)

    # 고용사명 (EMPLOYER_NM)
    if col == "EMPLOYER_NM":
        return random.choice(COMPANY_NAMES)

    # 지표명 (IND_NM)
    if col == "IND_NM":
        return random.choice(RISK_IND_CODES)[1]

    # 손익항목명 (PL_ITEM_NM)
    if col == "PL_ITEM_NM":
        items = [
            "이자이익", "수수료이익", "외환이익", "펀드이익",
            "영업비용", "충당금전입", "세전이익", "당기순이익",
        ]
        return random.choice(items)

    # 금액
    if col.endswith("_AMT") or col == "AMT":
        lo, hi = _DOMAIN_AMT.get(domain, (10_000, 1_000_000_000))
        return random.randint(lo, hi)

    # 비율 — 도메인별 범위
    if col.endswith("_RT"):
        group = _DOMAIN_GROUP.get(domain, "COM")
        if col == "INT_RT":
            if group == "DEP":
                return round(random.uniform(1.0, 5.0), 4)
            if group == "LN":
                return round(random.uniform(2.5, 12.0), 4)
        if col == "RETURN_RT":
            return round(random.uniform(-10.0, 25.0), 4)
        if col == "YOY_RT":
            return round(random.uniform(-30.0, 50.0), 4)
        if col == "CHG_RT":
            return round(random.uniform(-20.0, 20.0), 4)
        return round(random.uniform(0.01, 15.0), 2)

    # 수량
    if col == "QTY":
        return round(random.uniform(1, 10000), 4)

    # 건수
    if col.endswith("_CNT") or col == "CNT":
        return random.randint(0, 9999)

    # 이름
    if col.endswith("_NM") or col == "NM":
        return (
            f"{random.choice(SURNAMES)}"
            f"{random.choice(GIVEN_NAMES)}")

    # 설명/내용
    if col.endswith("_CONT") or col == "CONT":
        return f"내용_{idx:05d}"
    if col == "DESC_CONT":
        return f"설명_{idx:05d}"

    # IP 주소
    if col == "IP_ADR":
        return (
            f"10.{random.randint(0, 255)}"
            f".{random.randint(0, 255)}"
            f".{random.randint(1, 254)}")

    # 사용자 ID
    if col.endswith("_USR_ID") or col == "USR_ID":
        return f"user{random.randint(1, 50):02d}"

    # 전화번호
    if col == "TEL_NO":
        return _gen_tel()

    # 환율 (6자리 소수)
    if col == "EXC_RT":
        return round(random.uniform(900, 1400), 6)

    # DATE
    if ctype == "DATE":
        return TODAY - timedelta(days=random.randint(0, 365))

    # VARCHAR (기본)
    if "VARCHAR" in ctype:
        m = re.match(r"VARCHAR\((\d+)\)", ctype)
        maxl = int(m.group(1)) if m else 20
        val = f"V{idx:06d}"
        return val[:maxl]

    # NUMERIC
    if "NUMERIC" in ctype:
        lo, hi = _DOMAIN_AMT.get(domain, (10_000, 1_000_000_000))
        return random.randint(lo, hi)

    # INTEGER
    if ctype == "INTEGER":
        return random.randint(1, 1000)

    # TEXT
    if ctype == "TEXT":
        return f"텍스트_{idx:05d}"

    return None


def _fix_row_constraints(
    row: dict[str, object],
    col_names: list[str],
    domain_group: str,
) -> None:
    """행 내 크로스-컬럼 논리적 정합성 보정 (in-place).

    - 날짜 순서: MAT_DT > OPEN_DT, INS_END_DT > INS_ST_DT, REVIEW_DT >= DETECT_DT
    - 금액 관계: CRD_LIMIT_AMT >= USE_AMT, COV_AMT >= PREM_AMT, BAL_AFT_TR 계산
    - 투자 평가: EVAL_AMT ≈ INV_AMT (±30%)
    - 재무 전기대비: PREV_AMT과 AMT/YOY_RT 연동
    - 리스크: PREV_VAL과 IND_VAL/CHG_RT 연동
    """
    cols_set = set(col_names)

    # ── 날짜 순서 보정 ──────────────────────────────────
    _date_pairs = [
        ("OPEN_DT", "MAT_DT", 30, 730),       # 만기: 개시 후 1개월~2년
        ("INS_ST_DT", "INS_END_DT", 365, 7300),  # 보험: 1~20년
        ("DL_DT", "SETL_DT", 1, 180),          # FX: 결제까지 1일~6개월
        ("DETECT_DT", "REVIEW_DT", 1, 30),      # AML: 탐지 후 1~30일
        ("JOIN_DT", "LOGIN_DT", 1, 1825),        # 가입~로그인
    ]
    for start_col, end_col, min_gap, max_gap in _date_pairs:
        if start_col in cols_set and end_col in cols_set:
            s = row.get(start_col)
            e = row.get(end_col)
            if isinstance(s, date) and isinstance(e, date):
                if e <= s:
                    gap = timedelta(days=random.randint(min_gap, max_gap))
                    row[end_col] = s + gap

    # ── 카드: 한도 >= 사용금액 ──────────────────────────
    if "CRD_LIMIT_AMT" in cols_set and "USE_AMT" in cols_set:
        limit_v = row.get("CRD_LIMIT_AMT")
        use_v = row.get("USE_AMT")
        if (isinstance(limit_v, (int, float))
                and isinstance(use_v, (int, float))
                and use_v > limit_v):
            row["USE_AMT"] = int(limit_v * random.uniform(0.1, 0.95))

    # ── 보험: 보장금액 >= 보험료 ────────────────────────
    if "COV_AMT" in cols_set and "PREM_AMT" in cols_set:
        cov = row.get("COV_AMT")
        prem = row.get("PREM_AMT")
        if (isinstance(cov, (int, float))
                and isinstance(prem, (int, float))
                and prem > cov):
            row["COV_AMT"] = int(prem * random.randint(10, 50))

    # ── 거래: 거래 후 잔액 계산 ─────────────────────────
    if "BAL_AFT_TR" in cols_set and "TR_AMT" in cols_set:
        tr = row.get("TR_AMT")
        if isinstance(tr, (int, float)):
            base_bal = random.randint(100_000, 100_000_000)
            row["BAL_AFT_TR"] = base_bal + int(tr * random.choice([-1, 1]))

    # ── 펀드/신탁: 평가금액 ≈ 투자금액 (±30%) ──────────
    if "INV_AMT" in cols_set and "EVAL_AMT" in cols_set:
        inv = row.get("INV_AMT")
        if isinstance(inv, (int, float)) and inv > 0:
            row["EVAL_AMT"] = int(inv * random.uniform(0.7, 1.3))

    # ── 재무: PREV_AMT → YOY_RT 연동 ──────────────────
    if "AMT" in cols_set and "PREV_AMT" in cols_set and "YOY_RT" in cols_set:
        amt = row.get("AMT")
        prev = row.get("PREV_AMT")
        if (isinstance(amt, (int, float)) and isinstance(prev, (int, float))
                and prev != 0):
            yoy = (amt - prev) / prev * 100
            row["YOY_RT"] = round(max(-999999, min(999999, yoy)), 4)

    # ── 리스크: PREV_VAL → CHG_RT 연동 ────────────────
    if "IND_VAL" in cols_set and "PREV_VAL" in cols_set and "CHG_RT" in cols_set:
        val = row.get("IND_VAL")
        prev = row.get("PREV_VAL")
        if (isinstance(val, (int, float)) and isinstance(prev, (int, float))
                and prev != 0):
            chg = (val - prev) / prev * 100
            row["CHG_RT"] = round(max(-999999, min(999999, chg)), 4)

    # ── 대출: 연체등급에 따른 잔액 조정 (연체 없으면 정상) ─
    if "OVDU_GRD_CD" in cols_set and "LN_STCD" in cols_set:
        ovdu = row.get("OVDU_GRD_CD")
        if ovdu in ("01", "정상", None):
            row["LN_STCD"] = "01"  # 정상
        elif ovdu in ("04", "05"):
            row["LN_STCD"] = "03"  # 연체

    # ── SORT_ORD (정렬순서): INTEGER 값 부여 ─────────────
    if "SORT_ORD" in cols_set:
        row["SORT_ORD"] = random.randint(1, 999)


def seed_non_star_tables(cur, catalog: list[dict]) -> None:
    """비-★ 테이블 전체에 메타데이터 기반 데이터 자동 생성.

    1. ★ 테이블에서 PK 값 풀 수집
    2. 유형별 순서(코드→마스터→상세→이력)로 처리
    3. 각 테이블: 컬럼 재구성 → PK 조합 생성 → 벌크 INSERT
    """
    rng_state = random.getstate()
    random.seed(42)  # 재현성 보장

    pools = _collect_star_values(cur)
    pool_total = sum(len(v) for v in pools.values())
    print(f"\n  [값풀] ★ 테이블에서 {pool_total}개 PK값 수집")

    non_star = [t for t in catalog if not t["star"]]
    non_star.sort(key=lambda t: (
        _TYPE_ORDER.get(t["name"][-1], 9), t["name"],
    ))

    total_rows = 0
    seeded = 0
    skipped = 0

    for tbl_info in non_star:
        tbl_name = tbl_info["name"]
        domain = tbl_name[7:10]  # TB_ADW_XXX
        domain_group = _DOMAIN_GROUP.get(
            _extract_domain(tbl_name), "COM")

        cols, has_bs = _resolve_cols(tbl_info)
        if not cols and not has_bs:
            skipped += 1
            continue

        col_names = [c[0] for c in cols]
        col_types = {c[0]: c[1] for c in cols}
        n_rows = random.randint(*_TYPE_ROWS.get(
            tbl_name[-1], (100, 150)))

        # ── PK 컬럼 분류 ──────────────────────────────────
        pk_set = set(tbl_info["pk_cols"])
        pk_in = [c for c in col_names if c in pk_set]
        non_pk = [c for c in col_names if c not in pk_set]

        date_pks = [
            c for c in pk_in if col_types[c] == "DATE"]
        ym_pks = [
            c for c in pk_in
            if c.endswith(("_YM", "YM"))
            and "VARCHAR" in col_types[c]]
        yr_pks = [
            c for c in pk_in
            if (c.endswith(("_YR", "YR")) or c == "FY")
            and "VARCHAR" in col_types[c]
            and c not in ym_pks]
        entity_pks = [
            c for c in pk_in
            if c not in date_pks
            and c not in ym_pks
            and c not in yr_pks]

        # ── 엔티티 PK 값 준비 ──────────────────────────────
        for col in entity_pks:
            alias = _POOL_ALIAS.get(col, col)
            if alias in pools and pools[alias]:
                if col not in pools:
                    pools[col] = pools[alias]
                continue
            if col in pools and pools[col]:
                continue
            # 신규 엔티티 — 값 풀에 새 ID 추가
            col_type = _pk_col_type(col)
            if col_type == "INTEGER":
                new_ids = list(
                    range(1, max(n_rows, 150) + 1))
            else:
                prefix = col.replace("_", "")[:5].upper()
                new_ids = [
                    f"{prefix}{i:05d}"
                    for i in range(1, max(n_rows, 150) + 1)
                ]
            pools[col] = new_ids

        # ── 날짜 범위 결정 ──────────────────────────────────
        ttype = tbl_name[-1]
        if ttype in ("L", "H"):
            max_days = 365
        elif ttype in ("P", "S"):
            max_days = 90
        else:
            max_days = 365

        # ── 행 생성 ────────────────────────────────────────
        rows: list[tuple] = []
        seen: set[tuple] = set()
        max_att = n_rows * 20

        for _ in range(max_att):
            if len(rows) >= n_rows:
                break

            row: dict[str, object] = {}

            for col in entity_pks:
                alias = _POOL_ALIAS.get(col, col)
                src = pools.get(col) or pools.get(alias, [])
                row[col] = random.choice(src) if src else (
                    f"X{len(rows):06d}")
            for col in date_pks:
                row[col] = TODAY - timedelta(
                    days=random.randint(0, max_days))
            for col in ym_pks:
                m_back = random.randint(0, 11)
                d = TODAY.replace(day=1) - timedelta(
                    days=30 * m_back)
                row[col] = d.strftime("%Y%m")
            for col in yr_pks:
                row[col] = str(random.randint(2023, 2026))

            # 유니크 체크
            pk_key = tuple(
                str(row.get(c, "")) for c in pk_in)
            if pk_in and pk_key in seen:
                continue
            seen.add(pk_key)

            # 비-PK 값 생성
            idx = len(rows)
            for col in non_pk:
                row[col] = _gen_col_value(
                    col, col_types[col], domain, pools, idx)

            # 크로스-컬럼 논리 정합성 보정
            _fix_row_constraints(row, col_names, domain_group)

            rows.append(
                tuple(row.get(c) for c in col_names))

        if not rows:
            skipped += 1
            continue

        # ── TRUNCATE + 벌크 INSERT (SAVEPOINT로 실패 격리) ─
        sp = f"sp_{tbl_name}"
        try:
            cur.execute(f"SAVEPOINT {sp}")
            cur.execute(
                f"TRUNCATE TABLE ADWOWN.{tbl_name} CASCADE")
            insert_sql = (
                f"INSERT INTO ADWOWN.{tbl_name} "
                f"({', '.join(col_names)}) VALUES %s"
            )
            execute_values(cur, insert_sql, rows,
                           page_size=500)
            cur.execute(f"RELEASE SAVEPOINT {sp}")
            total_rows += len(rows)
            seeded += 1
            if seeded % 50 == 0:
                print(
                    f"  ... {seeded}개 테이블 "
                    f"({total_rows:,}건)")
        except Exception as e:
            cur.execute(
                f"ROLLBACK TO SAVEPOINT {sp}")
            skipped += 1
            # 첫 10개 실패만 출력
            if skipped <= 10:
                print(f"  [SKIP] {tbl_name}: {e}")

    random.setstate(rng_state)  # 랜덤 상태 복원

    print(
        f"\n  비-★ 테이블 시딩 완료: "
        f"{seeded}개 테이블, 총 {total_rows:,}건"
        f" (스킵 {skipped}개)")


# ══════════════════════════════════════════════════════════════
# 이력 DB 시딩
# ══════════════════════════════════════════════════════════════

def setup_checkpoint_dc_tables(conn) -> None:  # type: ignore[type-arg]
    """Data Copilot 커스텀 테이블을 초기화한다.

    checkpoint_dc_turn_texts (파티션), checkpoint_dc_session_index,
    mask_pii() 함수를 생성한다.
    """
    ddl_path = (
        RESOURCES_DIR / "connectors" / "postgres" / "checkpoint" / "03_dc_custom_tables.sql"
    )
    if not ddl_path.exists():
        print(f"  [WARN] DDL 파일 미존재: {ddl_path}")
        return

    cur = conn.cursor()
    try:
        cur.execute("SET search_path TO bdptbl, public")
        cur.execute(ddl_path.read_text(encoding="utf-8"))
        conn.commit()
        print(
            "  checkpoint_dc_turn_texts, checkpoint_dc_session_index,"
            " mask_pii() 초기화 완료"
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def seed_history_db() -> None:
    """sys_schema.sql_exec_log에 SQL 실행 이력 적재 (신규 테이블명 반영)."""
    conn = _connect(HISTORY_DB_CONNINFO)
    cur = conn.cursor()

    # 테이블 DDL
    cur.execute("""
        CREATE SCHEMA IF NOT EXISTS sys_schema;
        CREATE TABLE IF NOT EXISTS sys_schema.sql_exec_log (
            LOG_ID      BIGSERIAL PRIMARY KEY,
            NL_QUERY    TEXT,
            GEN_SQL     TEXT,
            EXEC_YN     BOOLEAN,
            EXEC_RESULT VARCHAR(20),
            USER_ID     VARCHAR(20),
            EXEC_MS     INTEGER,
            ERROR_MSG   TEXT,
            REG_DTM     TIMESTAMP DEFAULT NOW()
        );
    """)

    # (NL_QUERY, GEN_SQL, EXEC_YN, EXEC_RESULT, USER_ID, EXEC_MS, ERROR_MSG)
    history_records = [
        (
            "이번 달 신규 가입 고객 수",
            "SELECT COUNT(*) AS new_cust_cnt FROM ADWOWN.TB_ADW_CSC101M "
            "WHERE STD_DT = CURRENT_DATE AND JOIN_DT >= DATE_TRUNC('month', CURRENT_DATE)",  # noqa: E501
            True, "SUCCESS", "user01", 45, None,
        ),
        (
            "지점별 여신 잔액 TOP 10",
            "SELECT b.BR_NM, SUM(l.LN_BAL_AMT) AS total_bal "
            "FROM ADWOWN.TB_ADW_LNB301M l "
            "JOIN ADWOWN.TB_ADW_COM001M b ON l.BLNG_BRCD = b.BLNG_BRCD "
            "WHERE l.STD_DT = CURRENT_DATE "
            "GROUP BY b.BR_NM ORDER BY total_bal DESC LIMIT 10",
            True, "SUCCESS", "user02", 120, None,
        ),
        (
            "연체 등급 C 이상 고객 목록",
            "SELECT ci.EDPS_CSN, ci.CSM, li.OVDU_GRD_CD, li.OVDU_AMT "
            "FROM ADWOWN.TB_ADW_LNB301M li "
            "JOIN ADWOWN.TB_ADW_CSC101M ci "
            "  ON li.EDPS_CSN = ci.EDPS_CSN AND ci.STD_DT = CURRENT_DATE "
            "WHERE li.STD_DT = CURRENT_DATE AND li.OVDU_GRD_CD IN ('C','D','E') "  # noqa: E501
            "ORDER BY li.OVDU_AMT DESC",
            True, "SUCCESS", "user03", 200, None,
        ),
        (
            "VIP 고객 보유 상품 현황",
            "SELECT ci.EDPS_CSN, ci.CSM, ci.CUS_GRD_CD, "
            "ab.ACN, ab.PD_NM, ab.BAL_AMT, "
            "li.LN_NO, li.LN_BAL_AMT, cd.CRD_NO "
            "FROM ADWOWN.TB_ADW_CSC101M ci "
            "LEFT JOIN ADWOWN.TB_ADW_DEP201P ab "
            "  ON ci.EDPS_CSN = ab.EDPS_CSN AND ab.STD_DT = CURRENT_DATE "
            "LEFT JOIN ADWOWN.TB_ADW_LNB301M li "
            "  ON ci.EDPS_CSN = li.EDPS_CSN AND li.STD_DT = CURRENT_DATE "
            "LEFT JOIN ADWOWN.TB_ADW_CRD401M cd "
            "  ON ci.EDPS_CSN = cd.EDPS_CSN AND cd.STD_DT = CURRENT_DATE "
            "WHERE ci.STD_DT = CURRENT_DATE AND ci.CUS_GRD_CD = '01'",
            True, "SUCCESS", "user01", 350, None,
        ),
        (
            "전월 대비 카드 이용금액 증감",
            "WITH monthly AS ("
            "  SELECT EDPS_CSN, STD_DT, SUM(MON_USE_AMT) AS tot_use "
            "  FROM ADWOWN.TB_ADW_CRD401M GROUP BY EDPS_CSN, STD_DT"
            ") SELECT EDPS_CSN, tot_use, "
            "LAG(tot_use) OVER (PARTITION BY EDPS_CSN ORDER BY STD_DT) AS prev_use, "  # noqa: E501
            "tot_use - LAG(tot_use) OVER (PARTITION BY EDPS_CSN ORDER BY STD_DT) AS diff "  # noqa: E501
            "FROM monthly ORDER BY diff DESC NULLS LAST LIMIT 20",
            True, "SUCCESS", "user04", 280, None,
        ),
        (
            "평균 잔액보다 높은 계좌 목록",
            "SELECT ACN, EDPS_CSN, BAL_AMT FROM ADWOWN.TB_ADW_DEP201P "
            "WHERE STD_DT = CURRENT_DATE "
            "AND BAL_AMT > (SELECT AVG(BAL_AMT) FROM ADWOWN.TB_ADW_DEP201P "  # noqa: E501
            "               WHERE STD_DT = CURRENT_DATE) "
            "ORDER BY BAL_AMT DESC",
            True, "SUCCESS", "user02", 150, None,
        ),
        (
            "거래 10건 이상 활성 계좌",
            "SELECT ACN, COUNT(*) AS trx_cnt "
            "FROM ADWOWN.TB_ADW_TRX701L "
            "WHERE TR_DT >= DATE_TRUNC('month', CURRENT_DATE) "
            "GROUP BY ACN HAVING COUNT(*) >= 10 "
            "ORDER BY trx_cnt DESC",
            True, "SUCCESS", "user05", 180, None,
        ),
        (
            "여신 유형별 실행 건수",
            "SELECT LN_DCD, COUNT(*) AS ln_cnt, SUM(LN_EXC_AMT) AS total_amt "
            "FROM ADWOWN.TB_ADW_LNB301M "
            "WHERE STD_DT = CURRENT_DATE AND LN_DT >= DATE_TRUNC('month', CURRENT_DATE) "  # noqa: E501
            "GROUP BY LN_DCD",
            True, "SUCCESS", "user01", 95, None,
        ),
        (
            "지점별 고객 수",
            "SELECT b.BR_NM, COUNT(ci.EDPS_CSN) AS cust_cnt "
            "FROM ADWOWN.TB_ADW_CSC101M ci "
            "JOIN ADWOWN.TB_ADW_COM001M b ON ci.BLNG_BRCD = b.BLNG_BRCD "
            "WHERE ci.STD_DT = CURRENT_DATE "
            "GROUP BY b.BR_NM ORDER BY cust_cnt DESC LIMIT 10",
            True, "SUCCESS", "user02", 85, None,
        ),
        (
            "연령대별 고객 분포",
            "SELECT AGE_GRP_CD, COUNT(*) AS cust_cnt "
            "FROM ADWOWN.TB_ADW_CSC101M "
            "WHERE STD_DT = CURRENT_DATE AND CUS_DCD = '01' "
            "GROUP BY AGE_GRP_CD ORDER BY AGE_GRP_CD",
            True, "SUCCESS", "user05", 55, None,
        ),
        (
            "담보대출 평균 금리",
            "SELECT ROUND(AVG(APLY_RT)::NUMERIC, 2) AS avg_rate "
            "FROM ADWOWN.TB_ADW_LNB301M "
            "WHERE STD_DT = CURRENT_DATE AND LN_DCD = '02'",
            True, "SUCCESS", "user01", 40, None,
        ),
        (
            "고객별 총 자산",
            "SELECT ci.EDPS_CSN, ci.CSM, "
            "COALESCE(SUM(ab.BAL_AMT), 0) AS deposit_total, "
            "COALESCE(SUM(li.LN_BAL_AMT), 0) AS loan_total "
            "FROM ADWOWN.TB_ADW_CSC101M ci "
            "LEFT JOIN ADWOWN.TB_ADW_DEP201P ab "
            "  ON ci.EDPS_CSN = ab.EDPS_CSN AND ab.STD_DT = CURRENT_DATE "
            "LEFT JOIN ADWOWN.TB_ADW_LNB301M li "
            "  ON ci.EDPS_CSN = li.EDPS_CSN AND li.STD_DT = CURRENT_DATE "
            "WHERE ci.STD_DT = CURRENT_DATE "
            "GROUP BY ci.EDPS_CSN, ci.CSM ORDER BY deposit_total DESC LIMIT 50",  # noqa: E501
            True, "SUCCESS", "user01", 420, None,
        ),
        (
            "계좌 유형별 잔액 합계",
            "SELECT ACT_DCD, COUNT(*) AS acct_cnt, SUM(BAL_AMT) AS total_bal "
            "FROM ADWOWN.TB_ADW_DEP201P "
            "WHERE STD_DT = CURRENT_DATE "
            "GROUP BY ACT_DCD ORDER BY total_bal DESC",
            True, "SUCCESS", "user03", 90, None,
        ),
        (
            "기업 고객 대출 비중",
            "SELECT ci.CUS_DCD, COUNT(*) AS ln_cnt, SUM(li.LN_EXC_AMT) AS total_amt "  # noqa: E501
            "FROM ADWOWN.TB_ADW_LNB301M li "
            "JOIN ADWOWN.TB_ADW_CSC101M ci "
            "  ON li.EDPS_CSN = ci.EDPS_CSN AND ci.STD_DT = CURRENT_DATE "
            "WHERE li.STD_DT = CURRENT_DATE "
            "GROUP BY ci.CUS_DCD",
            True, "SUCCESS", "user05", 110, None,
        ),
        (
            "리스크 지표 BIS 비율 최근 3개월",
            "SELECT STD_DT, IND_VAL AS bis_ratio "
            "FROM ADWOWN.TB_ADW_RSK1101M "
            "WHERE IND_CD = 'BIS_RATIO' "
            "AND STD_DT >= CURRENT_DATE - INTERVAL '90 days' "
            "ORDER BY STD_DT",
            True, "SUCCESS", "user06", 30, None,
        ),
        (
            "캠페인 대상 고객 중 응답 비율",
            "SELECT m.CAMP_CD, COUNT(*) AS total_cnt, "
            "SUM(CASE WHEN t.RESP_YN = 'Y' THEN 1 ELSE 0 END) AS resp_cnt, "
            "ROUND(SUM(CASE WHEN t.RESP_YN = 'Y' THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 2) AS resp_rt "  # noqa: E501
            "FROM ADWOWN.TB_ADW_MKT1201M m "
            "JOIN ADWOWN.TB_ADW_MKT1202M t ON m.CAMP_CD = t.CAMP_CD "
            "GROUP BY m.CAMP_CD ORDER BY resp_rt DESC LIMIT 10",
            True, "SUCCESS", "user07", 150, None,
        ),
        (
            "지점별 이번 달 손익",
            "SELECT f.BLNG_BRCD, b.BR_NM, SUM(f.AMT) AS tot_pl "
            "FROM ADWOWN.TB_ADW_FIN1306S f "
            "JOIN ADWOWN.TB_ADW_COM001M b ON f.BLNG_BRCD = b.BLNG_BRCD "
            "WHERE f.BASE_YM = TO_CHAR(CURRENT_DATE, 'YYYYMM') "
            "GROUP BY f.BLNG_BRCD, b.BR_NM ORDER BY tot_pl DESC",
            True, "SUCCESS", "user01", 200, None,
        ),
        # 실패 케이스 — 에이전트 학습용
        (
            "최근 거래 내역 보여줘",
            "SELECT * FROM ADWOWN.TB_ADW_TRX701L",
            False, "TIMEOUT", "user08", None,
            "기간 조건 누락으로 전체 파티션 스캔 - 타임아웃 발생",
        ),
        (
            "이번 달 연체 고객",
            "SELECT ci.EDPS_CSN, ci.CSM, li.OVDU_GRD_CD, li.OVDU_AMT "
            "FROM ADWOWN.TB_ADW_LNB301M li "
            "JOIN ADWOWN.TB_ADW_CSC101M ci "
            "  ON li.EDPS_CSN = ci.EDPS_CSN AND ci.STD_DT = CURRENT_DATE "
            "WHERE li.STD_DT = CURRENT_DATE "
            "AND li.OVDU_GRD_CD IN ('A','B','C','D','E','F','Z')",
            True, "SUCCESS", "user09", 210, None,
        ),
    ]

    try:
        cur.execute("TRUNCATE TABLE sys_schema.sql_exec_log RESTART IDENTITY")

        for row in history_records:
            cur.execute(
                "INSERT INTO sys_schema.sql_exec_log "
                "(NL_QUERY, GEN_SQL, EXEC_YN, EXEC_RESULT, USER_ID, EXEC_MS, ERROR_MSG) "  # noqa: E501
                "VALUES (%s,%s,%s,%s,%s,%s,%s)", row)

        conn.commit()
        print(f"  sql_exec_log     : {len(history_records):>5}건  (실패 1건 포함)")
        print("\n  → 이력 DB (sys_schema) 시딩 완료!")

    except Exception as e:
        conn.rollback()
        print(f"\n  ✗ 이력 DB 시딩 실패: {e}")
        raise
    finally:
        cur.close()
        conn.close()


# ══════════════════════════════════════════════════════════════
# main
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("PostgreSQL 테스트 데이터 시딩 (신규 명명규칙)")
    print("=" * 60)
    print("\n[정보계 DB - ADWOWN]")
    seed_info_db()
    print("\n[이력 DB - sys_schema]")
    seed_history_db()
    print("\n[이력 DB - Data Copilot 커스텀 테이블]")
    _dc_conn = _connect(HISTORY_DB_CONNINFO)
    try:
        setup_checkpoint_dc_tables(_dc_conn)
    finally:
        _dc_conn.close()
    print("\n" + "=" * 60)
    print("전체 PostgreSQL 시딩 완료!")
    print("=" * 60)
