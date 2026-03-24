"""report_sql 인덱스 증분 적재 스크립트.

ES report_sql 인덱스(현재 10건)에 140건을 추가 적재한다.
도메인 14개 × 10건씩 = 140건. _id 범위: 10~149.

사용법:
    PYTHONIOENCODING=utf-8 python standalone/scripts/augment_report_sql.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(_env_path, encoding="utf-8")

ES_URL = (
    f"http://{os.getenv('ES_HOST', 'localhost')}"
    f":{os.getenv('ES_PORT', '9200')}"
)
ES_USER = os.getenv("ES_USER", "elastic")
ES_PASSWORD = os.getenv("ES_PASSWORD", "")

INDEX_NAME = "report_sql"
START_ID = 10  # 기존 10건(_id 0~9) 이후부터

# ══════════════════════════════════════════════════════════════
# 도메인별 보고서 SQL 정의 (14도메인 × 10건)
# ══════════════════════════════════════════════════════════════

DOMAIN_REPORTS: dict[str, list[tuple[str, list[str], str, str]]] = {

    "CUS": [
        (
            "신규 고객 월별 추이",
            ["TB_ADW_CSC101M"],
            "월별 신규 등록 고객 수 추이",
            "SELECT DATE_TRUNC('month', join_dt) AS m, COUNT(*) AS cnt"  # noqa: E501
            " FROM biz_schema.tb_adw_csc101m"
            " WHERE std_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 1",
        ),
        (
            "고객 등급별 분포",
            ["TB_ADW_CSC101M"],
            "고객 등급 코드별 인원 수",
            "SELECT cus_grd_cd, COUNT(*) AS cnt"
            " FROM biz_schema.tb_adw_csc101m"
            " WHERE std_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 1",
        ),
        (
            "VIP 고객 목록",
            ["TB_ADW_CSC101M"],
            "VIP 등급(01) 고객 명단",
            "SELECT edps_csn, csm, blng_brcd"
            " FROM biz_schema.tb_adw_csc101m"
            " WHERE std_dt = CURRENT_DATE AND cus_grd_cd = '01'"
            " ORDER BY edps_csn LIMIT 100",
        ),
        (
            "고객유형별 분포",
            ["TB_ADW_CSC101M"],
            "개인/법인 등 고객 구분 코드별 인원 수",
            "SELECT cus_dcd, COUNT(*) AS cnt"
            " FROM biz_schema.tb_adw_csc101m"
            " WHERE std_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 2 DESC",
        ),
        (
            "지점별 고객 수 TOP 10",
            ["TB_ADW_CSC101M", "TB_ADW_COM001M"],
            "부점별 고객 수 상위 10개 부점",
            "SELECT b.br_nm, COUNT(c.edps_csn) AS cnt"
            " FROM biz_schema.tb_adw_csc101m c"
            " JOIN biz_schema.tb_adw_com001m b ON c.blng_brcd = b.blng_brcd"
            " WHERE c.std_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 2 DESC LIMIT 10",
        ),
        (
            "마케팅등급 vs 영업등급 비교",
            ["TB_ADW_CSC101M", "TB_ADW_CSP103M"],
            "고객별 마케팅 등급과 영업 등급 대조",
            "SELECT c.edps_csn, c.cus_grd_cd, p.mkt_grd_cd"
            " FROM biz_schema.tb_adw_csc101m c"
            " JOIN biz_schema.tb_adw_csp103m p ON c.edps_csn = p.edps_csn"
            " WHERE c.std_dt = CURRENT_DATE AND p.std_dt = CURRENT_DATE"
            " ORDER BY c.edps_csn LIMIT 200",
        ),
        (
            "가입 연차별 분포",
            ["TB_ADW_CSC101M"],
            "가입 연도별 고객 수 분포",
            "SELECT EXTRACT(YEAR FROM join_dt) AS join_year, COUNT(*) AS cnt"
            " FROM biz_schema.tb_adw_csc101m"
            " WHERE std_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 1",
        ),
        (
            "기업고객 목록",
            ["TB_ADW_CSC101M"],
            "법인(기업) 고객 목록",
            "SELECT edps_csn, csm, blng_brcd, join_dt"
            " FROM biz_schema.tb_adw_csc101m"
            " WHERE std_dt = CURRENT_DATE AND cus_dcd = '02'"
            " ORDER BY edps_csn LIMIT 200",
        ),
        (
            "고객별 등급 변동 이력",
            ["TB_ADW_CSC101M", "TB_ADW_CSC102H"],
            "고객 등급 변동 내역 조회",
            "SELECT h.edps_csn, h.chg_dt, h.cus_grd_cd"
            " FROM biz_schema.tb_adw_csc102h h"
            " JOIN biz_schema.tb_adw_csc101m c ON h.edps_csn = c.edps_csn"
            " WHERE c.std_dt = CURRENT_DATE"
            " ORDER BY h.edps_csn, h.chg_dt DESC LIMIT 500",
        ),
        (
            "고객 종합 자산 현황",
            ["TB_ADW_CSC101M", "TB_ADW_DEP201P", "TB_ADW_LNB301M"],
            "고객별 수신 잔액과 여신 잔액 종합",
            "SELECT c.edps_csn, c.csm,"
            " COALESCE(SUM(d.bal_amt), 0) AS dep_bal,"
            " COALESCE(SUM(l.ln_bal_amt), 0) AS ln_bal"
            " FROM biz_schema.tb_adw_csc101m c"
            " LEFT JOIN biz_schema.tb_adw_dep201p d ON c.edps_csn = d.edps_csn"
            "  AND d.std_dt = CURRENT_DATE"
            " LEFT JOIN biz_schema.tb_adw_lnb301m l ON c.edps_csn = l.edps_csn"
            "  AND l.std_dt = CURRENT_DATE"
            " WHERE c.std_dt = CURRENT_DATE"
            " GROUP BY 1, 2 ORDER BY dep_bal DESC LIMIT 100",
        ),
    ],

    "DEP": [
        (
            "상품별 수신 잔액 현황",
            ["TB_ADW_DEP201P"],
            "수신 상품 코드별 계좌 수 및 총잔액",
            "SELECT pd_cd, COUNT(*) AS cnt, SUM(bal_amt) AS total_bal"
            " FROM biz_schema.tb_adw_dep201p"
            " WHERE std_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 3 DESC",
        ),
        (
            "잔액 구간별 분포",
            ["TB_ADW_DEP201P"],
            "수신 잔액 구간별 계좌 수",
            "SELECT CASE"
            "  WHEN bal_amt < 1000000 THEN '100만 미만'"
            "  WHEN bal_amt < 10000000 THEN '100만~1000만'"
            "  WHEN bal_amt < 100000000 THEN '1000만~1억'"
            "  ELSE '1억 이상' END AS bal_range,"
            " COUNT(*) AS cnt"
            " FROM biz_schema.tb_adw_dep201p"
            " WHERE std_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 1",
        ),
        (
            "만기 도래 계좌 현황",
            ["TB_ADW_DEP201P"],
            "향후 30일 내 만기 도래 계좌 목록",
            "SELECT acn, edps_csn, bal_amt, mtrty_dt"
            " FROM biz_schema.tb_adw_dep201p"
            " WHERE std_dt = CURRENT_DATE"
            "  AND mtrty_dt BETWEEN CURRENT_DATE"  # noqa: E501
            " AND CURRENT_DATE + INTERVAL '30 days'"
            " ORDER BY mtrty_dt LIMIT 500",
        ),
        (
            "금리 구간별 분포",
            ["TB_ADW_DEP201P"],
            "수신 적용 금리 구간별 계좌 수",
            "SELECT CASE"
            "  WHEN int_rt < 1.0 THEN '1% 미만'"
            "  WHEN int_rt < 2.0 THEN '1~2%'"
            "  WHEN int_rt < 3.0 THEN '2~3%'"
            "  ELSE '3% 이상' END AS rt_range,"
            " COUNT(*) AS cnt"
            " FROM biz_schema.tb_adw_dep201p"
            " WHERE std_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 1",
        ),
        (
            "신규 계좌 현황",
            ["TB_ADW_DEP201P"],
            "이번 달 신규 개설 계좌 현황",
            "SELECT pd_cd, COUNT(*) AS cnt, SUM(bal_amt) AS total_bal"
            " FROM biz_schema.tb_adw_dep201p"
            " WHERE open_dt >= DATE_TRUNC('month', CURRENT_DATE)"
            " GROUP BY 1 ORDER BY 2 DESC",
        ),
        (
            "T+0 vs T+1 잔액 비교",
            ["TB_ADW_DEP201P", "TB_ADW_DEP202S"],
            "일별 잔액(T)과 익일 예상잔액(T+1) 비교",
            "SELECT p.acn, p.bal_amt AS t0_bal, s.bal_amt AS t1_bal,"
            " s.bal_amt - p.bal_amt AS diff"
            " FROM biz_schema.tb_adw_dep201p p"
            " JOIN biz_schema.tb_adw_dep202s s ON p.acn = s.acn"
            "  AND s.base_dt = CURRENT_DATE"
            " WHERE p.std_dt = CURRENT_DATE"
            " ORDER BY diff DESC LIMIT 100",
        ),
        (
            "계좌 상태별 집계",
            ["TB_ADW_DEP201P"],
            "계좌 상태 코드별 계좌 수 및 잔액",
            "SELECT act_stcd, COUNT(*) AS cnt, SUM(bal_amt) AS total_bal"
            " FROM biz_schema.tb_adw_dep201p"
            " WHERE std_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 2 DESC",
        ),
        (
            "지점별 수신 잔액 TOP 10",
            ["TB_ADW_DEP201P", "TB_ADW_COM001M"],
            "부점별 수신 총잔액 상위 10개 부점",
            "SELECT b.br_nm, COUNT(d.acn) AS cnt, SUM(d.bal_amt) AS total_bal"
            " FROM biz_schema.tb_adw_dep201p d"
            " JOIN biz_schema.tb_adw_com001m b ON d.blng_brcd = b.blng_brcd"
            " WHERE d.std_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 3 DESC LIMIT 10",
        ),
        (
            "고잔액 계좌 목록",
            ["TB_ADW_DEP201P"],
            "수신 잔액 상위 100개 계좌 목록",
            "SELECT acn, edps_csn, pd_cd, bal_amt, int_rt"
            " FROM biz_schema.tb_adw_dep201p"
            " WHERE std_dt = CURRENT_DATE"
            " ORDER BY bal_amt DESC LIMIT 100",
        ),
        (
            "월별 수신 잔액 추이",
            ["TB_ADW_DEP201P"],
            "월별 수신 총잔액 변동 추이",
            "SELECT DATE_TRUNC('month', std_dt) AS m,"
            " SUM(bal_amt) AS total_bal,"
            " COUNT(*) AS act_cnt"
            " FROM biz_schema.tb_adw_dep201p"
            " GROUP BY 1 ORDER BY 1",
        ),
    ],

    "LON": [
        (
            "지점별 여신 잔액 TOP 10",
            ["TB_ADW_LNB301M", "TB_ADW_COM001M"],
            "부점별 여신 총잔액 상위 10개 부점",
            "SELECT b.br_nm, COUNT(l.ln_no) AS cnt,"
            " SUM(l.ln_bal_amt) AS total_bal"
            " FROM biz_schema.tb_adw_lnb301m l"
            " JOIN biz_schema.tb_adw_com001m b ON l.blng_brcd = b.blng_brcd"
            " WHERE l.std_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 3 DESC LIMIT 10",
        ),
        (
            "연체 현황 요약",
            ["TB_ADW_LNB301M"],
            "연체 등급별 건수 및 연체 금액 집계",
            "SELECT ovdu_grd_cd, COUNT(*) AS cnt,"
            " SUM(ovdu_amt) AS total_ovdu,"
            " SUM(ln_bal_amt) AS total_bal"
            " FROM biz_schema.tb_adw_lnb301m"
            " WHERE std_dt = CURRENT_DATE"
            "  AND ovdu_grd_cd IS NOT NULL"
            " GROUP BY 1 ORDER BY 1",
        ),
        (
            "여신 상품별 집계",
            ["TB_ADW_LNB301M"],
            "여신 구분 코드별 건수 및 잔액",
            "SELECT ln_dcd, COUNT(*) AS cnt,"
            " SUM(ln_bal_amt) AS total_bal,"
            " AVG(int_rt) AS avg_rt"
            " FROM biz_schema.tb_adw_lnb301m"
            " WHERE std_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 2 DESC",
        ),
        (
            "여신 금리 분포",
            ["TB_ADW_LNB301M"],
            "여신 적용 금리 통계 (평균/최소/최대)",
            "SELECT ln_dcd,"
            " ROUND(AVG(int_rt)::NUMERIC, 2) AS avg_rt,"
            " MIN(int_rt) AS min_rt,"
            " MAX(int_rt) AS max_rt"
            " FROM biz_schema.tb_adw_lnb301m"
            " WHERE std_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 1",
        ),
        (
            "신규 여신 실행 현황",
            ["TB_ADW_LNB301M"],
            "이번 달 신규 실행 여신 현황",
            "SELECT ln_dcd, COUNT(*) AS cnt,"
            " SUM(ln_exc_amt) AS total_exc"
            " FROM biz_schema.tb_adw_lnb301m"
            " WHERE exec_dt >= DATE_TRUNC('month', CURRENT_DATE)"
            " GROUP BY 1 ORDER BY 2 DESC",
        ),
        (
            "만기 도래 여신 현황",
            ["TB_ADW_LNB301M"],
            "향후 30일 내 만기 도래 여신 목록",
            "SELECT ln_no, edps_csn, ln_bal_amt, int_rt, mtrty_dt"
            " FROM biz_schema.tb_adw_lnb301m"
            " WHERE std_dt = CURRENT_DATE"
            "  AND mtrty_dt BETWEEN CURRENT_DATE"  # noqa: E501
            " AND CURRENT_DATE + INTERVAL '30 days'"
            " ORDER BY mtrty_dt LIMIT 500",
        ),
        (
            "담보 현황 집계",
            ["TB_ADW_LNB302M"],
            "담보 구분 코드별 건수 및 담보 금액",
            "SELECT cltr_dcd, COUNT(*) AS cnt, SUM(ln_exc_amt) AS total_amt"
            " FROM biz_schema.tb_adw_lnb302m"
            " WHERE std_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 2 DESC",
        ),
        (
            "연체율 월별 추이",
            ["TB_ADW_LNB301M"],
            "월별 연체금액 / 여신잔액 비율 추이",
            "SELECT DATE_TRUNC('month', std_dt) AS m,"
            " ROUND(SUM(ovdu_amt)::NUMERIC"
            " / NULLIF(SUM(ln_bal_amt), 0) * 100, 2) AS ovdu_rate"
            " FROM biz_schema.tb_adw_lnb301m"
            " GROUP BY 1 ORDER BY 1",
        ),
        (
            "여신 건전성 분류",
            ["TB_ADW_LNB301M"],
            "건전성 등급(연체등급)별 여신 잔액 비중",
            "SELECT ovdu_grd_cd,"
            " COUNT(*) AS cnt,"
            " SUM(ln_bal_amt) AS total_bal,"
            " ROUND(SUM(ln_bal_amt)::NUMERIC"
            " / NULLIF(SUM(SUM(ln_bal_amt)) OVER (), 0) * 100, 2) AS ratio"
            " FROM biz_schema.tb_adw_lnb301m"
            " WHERE std_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 1",
        ),
        (
            "실행금액 vs 승인금액 비교",
            ["TB_ADW_LNB301M", "TB_ADW_LNB302M"],
            "여신별 승인금액 대비 실행금액 비율",
            "SELECT l.ln_no, l.ln_apr_amt, l.ln_exc_amt,"
            " ROUND(l.ln_exc_amt::NUMERIC"
            " / NULLIF(l.ln_apr_amt, 0) * 100, 1) AS exec_ratio"
            " FROM biz_schema.tb_adw_lnb301m l"
            " JOIN biz_schema.tb_adw_lnb302m r ON l.ln_no = r.ln_no"
            " WHERE l.std_dt = CURRENT_DATE AND r.std_dt = CURRENT_DATE"
            " ORDER BY exec_ratio DESC LIMIT 200",
        ),
    ],

    "CRD": [
        (
            "카드 이용 현황",
            ["TB_ADW_CRD401M"],
            "월별 카드 이용 금액 추이",
            "SELECT DATE_TRUNC('month', std_dt) AS m,"
            " SUM(mon_use_amt) AS total_use"
            " FROM biz_schema.tb_adw_crd401m"
            " GROUP BY 1 ORDER BY 1",
        ),
        (
            "카드 유형별 현황",
            ["TB_ADW_CRD401M"],
            "카드 구분 코드별 카드 수 및 이용 금액",
            "SELECT crd_dcd, COUNT(*) AS cnt,"
            " SUM(mon_use_amt) AS total_use"
            " FROM biz_schema.tb_adw_crd401m"
            " WHERE std_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 3 DESC",
        ),
        (
            "카드 한도 현황",
            ["TB_ADW_CRD401M"],
            "카드 구분별 평균/최대 한도",
            "SELECT crd_dcd,"
            " ROUND(AVG(crd_lmt_amt)::NUMERIC, 0) AS avg_lmt,"
            " MAX(crd_lmt_amt) AS max_lmt"
            " FROM biz_schema.tb_adw_crd401m"
            " WHERE std_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 1",
        ),
        (
            "카드 연체 현황",
            ["TB_ADW_CRD401M"],
            "연체 상태 카드 수 및 연체 금액",
            "SELECT COUNT(*) AS cnt, SUM(ovdu_amt) AS total_ovdu"
            " FROM biz_schema.tb_adw_crd401m"
            " WHERE std_dt = CURRENT_DATE AND ovdu_yn = 'Y'",
        ),
        (
            "해외 이용 카드 현황",
            ["TB_ADW_CRD401M"],
            "해외 이용 금액 상위 100개 카드",
            "SELECT crd_no, edps_csn, ovs_use_amt"
            " FROM biz_schema.tb_adw_crd401m"
            " WHERE std_dt = CURRENT_DATE AND ovs_use_amt > 0"
            " ORDER BY ovs_use_amt DESC LIMIT 100",
        ),
        (
            "카드 월별 이용 추이",
            ["TB_ADW_CRD401M"],
            "최근 12개월 카드 이용 건수 및 금액 추이",
            "SELECT DATE_TRUNC('month', std_dt) AS m,"
            " COUNT(*) AS cnt,"
            " SUM(mon_use_amt) AS total_use"
            " FROM biz_schema.tb_adw_crd401m"
            " WHERE std_dt >= CURRENT_DATE - INTERVAL '12 months'"
            " GROUP BY 1 ORDER BY 1",
        ),
        (
            "신규 카드 발급 현황",
            ["TB_ADW_CRD401M"],
            "이번 달 신규 발급 카드 현황",
            "SELECT crd_dcd, COUNT(*) AS cnt"
            " FROM biz_schema.tb_adw_crd401m"
            " WHERE open_dt >= DATE_TRUNC('month', CURRENT_DATE)"
            " GROUP BY 1 ORDER BY 2 DESC",
        ),
        (
            "가맹점 이용 통계",
            ["TB_ADW_CRD401M"],
            "가맹점 유형별 카드 이용 통계",
            "SELECT mcct_dcd, COUNT(*) AS cnt,"
            " SUM(mon_use_amt) AS total_use"
            " FROM biz_schema.tb_adw_crd401m"
            " WHERE std_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 3 DESC LIMIT 20",
        ),
        (
            "포인트 잔액 현황",
            ["TB_ADW_CRD401M"],
            "카드 포인트 잔액 상위 고객 목록",
            "SELECT edps_csn, SUM(pnt_bal) AS total_pnt"
            " FROM biz_schema.tb_adw_crd401m"
            " WHERE std_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 2 DESC LIMIT 100",
        ),
        (
            "법인카드 현황",
            ["TB_ADW_CRD401M"],
            "법인카드 이용 현황 집계",
            "SELECT COUNT(*) AS cnt,"
            " SUM(mon_use_amt) AS total_use,"
            " AVG(crd_lmt_amt) AS avg_lmt"
            " FROM biz_schema.tb_adw_crd401m"
            " WHERE std_dt = CURRENT_DATE AND crd_dcd = '03'",
        ),
    ],

    "FX": [
        (
            "통화별 외환 거래 현황",
            ["TB_ADW_FXD501L"],
            "통화 코드별 외환 거래 건수 및 금액",
            "SELECT ccy_cd, COUNT(*) AS cnt, SUM(dl_amt) AS total_amt"
            " FROM biz_schema.tb_adw_fxd501l"
            " WHERE base_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 3 DESC",
        ),
        (
            "환율 추이",
            ["TB_ADW_FXD502M"],
            "주요 통화 환율 일별 추이",
            "SELECT base_dt, ccy_cd, base_rt"
            " FROM biz_schema.tb_adw_fxd502m"
            " WHERE base_dt >= CURRENT_DATE - INTERVAL '30 days'"
            "  AND ccy_cd IN ('USD', 'JPY', 'EUR', 'CNY')"
            " ORDER BY base_dt, ccy_cd",
        ),
        (
            "딜 유형별 거래 통계",
            ["TB_ADW_FXD501L"],
            "외환 딜 유형 코드별 건수 및 금액",
            "SELECT dl_dcd, COUNT(*) AS cnt, SUM(dl_amt) AS total_amt"
            " FROM biz_schema.tb_adw_fxd501l"
            " WHERE base_dt >= CURRENT_DATE - INTERVAL '7 days'"
            " GROUP BY 1 ORDER BY 2 DESC",
        ),
        (
            "해외송금 현황",
            ["TB_ADW_FXD501L"],
            "해외 송금 거래 건수 및 금액",
            "SELECT ccy_cd, COUNT(*) AS cnt, SUM(dl_amt) AS total_amt"
            " FROM biz_schema.tb_adw_fxd501l"
            " WHERE base_dt = CURRENT_DATE AND dl_dcd = 'RM'"
            " GROUP BY 1 ORDER BY 3 DESC",
        ),
        (
            "외환 포지션 현황",
            ["TB_ADW_FXD502M"],
            "통화별 외환 순포지션 현황",
            "SELECT ccy_cd, SUM(buy_amt - sell_amt) AS net_pos"
            " FROM biz_schema.tb_adw_fxd502m"
            " WHERE base_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY ABS(SUM(buy_amt - sell_amt)) DESC",
        ),
        (
            "외환 일별 거래 통계",
            ["TB_ADW_FXD501L"],
            "최근 30일 외환 거래 일별 통계",
            "SELECT base_dt, COUNT(*) AS cnt, SUM(dl_amt) AS total_amt"
            " FROM biz_schema.tb_adw_fxd501l"
            " WHERE base_dt >= CURRENT_DATE - INTERVAL '30 days'"
            " GROUP BY 1 ORDER BY 1",
        ),
        (
            "외환 월별 거래 통계",
            ["TB_ADW_FXD501L"],
            "월별 외환 거래 건수 및 금액 추이",
            "SELECT DATE_TRUNC('month', base_dt) AS m,"
            " COUNT(*) AS cnt,"
            " SUM(dl_amt) AS total_amt"
            " FROM biz_schema.tb_adw_fxd501l"
            " GROUP BY 1 ORDER BY 1",
        ),
        (
            "고객별 외환 한도 현황",
            ["TB_ADW_FXD501L"],
            "고객별 외환 거래 한도 소진 현황",
            "SELECT edps_csn, SUM(dl_amt) AS used_amt"
            " FROM biz_schema.tb_adw_fxd501l"
            " WHERE base_dt >= DATE_TRUNC('month', CURRENT_DATE)"
            " GROUP BY 1 ORDER BY 2 DESC LIMIT 100",
        ),
        (
            "외환 결제 현황",
            ["TB_ADW_FXD501L"],
            "결제 상태별 외환 거래 건수 및 금액",
            "SELECT setl_stcd, COUNT(*) AS cnt, SUM(dl_amt) AS total_amt"
            " FROM biz_schema.tb_adw_fxd501l"
            " WHERE base_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 2 DESC",
        ),
        (
            "기간별 외환 거래 추이",
            ["TB_ADW_FXD501L"],
            "분기별 외환 거래 건수 및 금액 비교",
            "SELECT DATE_TRUNC('quarter', base_dt) AS q,"
            " COUNT(*) AS cnt,"
            " SUM(dl_amt) AS total_amt"
            " FROM biz_schema.tb_adw_fxd501l"
            " GROUP BY 1 ORDER BY 1",
        ),
    ],

    "TRX": [
        (
            "채널별 거래 통계",
            ["TB_ADW_TRX701L"],
            "채널 코드별 거래 건수 및 금액",
            "SELECT chn_cd, COUNT(*) AS cnt, SUM(tr_amt) AS total_amt"
            " FROM biz_schema.tb_adw_trx701l"
            " WHERE tr_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 2 DESC",
        ),
        (
            "일별 거래 현황",
            ["TB_ADW_TRX701L"],
            "최근 30일 일별 거래 건수 및 금액",
            "SELECT tr_dt, COUNT(*) AS cnt, SUM(tr_amt) AS total_amt"
            " FROM biz_schema.tb_adw_trx701l"
            " WHERE tr_dt >= CURRENT_DATE - INTERVAL '30 days'"
            " GROUP BY 1 ORDER BY 1",
        ),
        (
            "거래 유형별 통계",
            ["TB_ADW_TRX701L"],
            "거래 구분 코드별 건수 및 금액",
            "SELECT tr_dcd, COUNT(*) AS cnt, SUM(tr_amt) AS total_amt"
            " FROM biz_schema.tb_adw_trx701l"
            " WHERE tr_dt >= CURRENT_DATE - INTERVAL '7 days'"
            " GROUP BY 1 ORDER BY 2 DESC",
        ),
        (
            "지점별 거래 현황",
            ["TB_ADW_TRX701L", "TB_ADW_COM001M"],
            "부점별 거래 건수 및 금액",
            "SELECT b.br_nm, COUNT(t.tr_id) AS cnt,"
            " SUM(t.tr_amt) AS total_amt"
            " FROM biz_schema.tb_adw_trx701l t"
            " JOIN biz_schema.tb_adw_com001m b ON t.blng_brcd = b.blng_brcd"
            " WHERE t.tr_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 2 DESC LIMIT 20",
        ),
        (
            "시간대별 거래 분포",
            ["TB_ADW_TRX701L"],
            "시간대별 거래 건수 분포",
            "SELECT EXTRACT(HOUR FROM tr_tm) AS hr,"
            " COUNT(*) AS cnt"
            " FROM biz_schema.tb_adw_trx701l"
            " WHERE tr_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 1",
        ),
        (
            "금액 구간별 거래 분포",
            ["TB_ADW_TRX701L"],
            "거래 금액 구간별 건수",
            "SELECT CASE"
            "  WHEN tr_amt < 100000 THEN '10만 미만'"
            "  WHEN tr_amt < 1000000 THEN '10만~100만'"
            "  WHEN tr_amt < 10000000 THEN '100만~1000만'"
            "  ELSE '1000만 이상' END AS amt_range,"
            " COUNT(*) AS cnt"
            " FROM biz_schema.tb_adw_trx701l"
            " WHERE tr_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 1",
        ),
        (
            "월별 거래 추이",
            ["TB_ADW_TRX701L"],
            "월별 거래 건수 및 금액 추이",
            "SELECT DATE_TRUNC('month', tr_dt) AS m,"
            " COUNT(*) AS cnt,"
            " SUM(tr_amt) AS total_amt"
            " FROM biz_schema.tb_adw_trx701l"
            " GROUP BY 1 ORDER BY 1",
        ),
        (
            "활성 계좌 거래 현황",
            ["TB_ADW_TRX701L"],
            "이달 거래 발생 활성 계좌 수",
            "SELECT COUNT(DISTINCT acn) AS active_acnt_cnt"
            " FROM biz_schema.tb_adw_trx701l"
            " WHERE tr_dt >= DATE_TRUNC('month', CURRENT_DATE)",
        ),
        (
            "취소 거래 현황",
            ["TB_ADW_TRX701L"],
            "취소 처리된 거래 건수 및 금액",
            "SELECT tr_dcd, COUNT(*) AS cnt, SUM(tr_amt) AS total_amt"
            " FROM biz_schema.tb_adw_trx701l"
            " WHERE tr_dt >= CURRENT_DATE - INTERVAL '7 days'"
            "  AND cncl_yn = 'Y'"
            " GROUP BY 1 ORDER BY 2 DESC",
        ),
        (
            "오픈뱅킹 거래 현황",
            ["TB_ADW_TRX701L"],
            "오픈뱅킹 채널 거래 건수 및 금액",
            "SELECT tr_dt, COUNT(*) AS cnt, SUM(tr_amt) AS total_amt"
            " FROM biz_schema.tb_adw_trx701l"
            " WHERE tr_dt >= CURRENT_DATE - INTERVAL '30 days'"
            "  AND chn_cd = 'OB'"
            " GROUP BY 1 ORDER BY 1",
        ),
    ],

    "FND": [
        (
            "펀드 잔고 현황",
            ["TB_ADW_FNB601M"],
            "펀드 코드별 계좌 수 및 평가 잔고",
            "SELECT fund_cd, COUNT(*) AS cnt, SUM(eval_amt) AS total_eval"
            " FROM biz_schema.tb_adw_fnb601m"
            " WHERE std_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 3 DESC",
        ),
        (
            "펀드 수익률 현황",
            ["TB_ADW_FNB601M"],
            "펀드 코드별 평균 수익률",
            "SELECT fund_cd,"
            " ROUND(AVG(erns_rt)::NUMERIC, 2) AS avg_rt,"
            " MIN(erns_rt) AS min_rt,"
            " MAX(erns_rt) AS max_rt"
            " FROM biz_schema.tb_adw_fnb601m"
            " WHERE std_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 2 DESC",
        ),
        (
            "펀드 유형별 현황",
            ["TB_ADW_FNB601M"],
            "펀드 유형 코드별 계좌 수 및 잔고",
            "SELECT fnd_dcd, COUNT(*) AS cnt,"
            " SUM(eval_amt) AS total_eval"
            " FROM biz_schema.tb_adw_fnb601m"
            " WHERE std_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 3 DESC",
        ),
        (
            "리스크 등급별 펀드 현황",
            ["TB_ADW_FNB601M"],
            "리스크 등급별 펀드 계좌 수 및 잔고",
            "SELECT rsk_grd_cd, COUNT(*) AS cnt,"
            " SUM(eval_amt) AS total_eval"
            " FROM biz_schema.tb_adw_fnb601m"
            " WHERE std_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 1",
        ),
        (
            "펀드 가입 및 환매 현황",
            ["TB_ADW_FNB601M"],
            "이달 신규 가입 및 환매 건수",
            "SELECT COUNT(CASE WHEN open_dt >= DATE_TRUNC('month', CURRENT_DATE)"  # noqa: E501
            "  THEN 1 END) AS new_cnt,"
            " COUNT(CASE WHEN rdpt_dt >= DATE_TRUNC('month', CURRENT_DATE)"
            "  THEN 1 END) AS rdpt_cnt"
            " FROM biz_schema.tb_adw_fnb601m",
        ),
        (
            "펀드 평가액 현황",
            ["TB_ADW_FNB601M"],
            "고객별 펀드 총 평가액 상위 100명",
            "SELECT edps_csn, SUM(eval_amt) AS total_eval"
            " FROM biz_schema.tb_adw_fnb601m"
            " WHERE std_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 2 DESC LIMIT 100",
        ),
        (
            "NAV 추이",
            ["TB_ADW_FNB601M"],
            "주요 펀드 기준가(NAV) 일별 추이",
            "SELECT std_dt, fund_cd, nav"
            " FROM biz_schema.tb_adw_fnb601m"
            " WHERE std_dt >= CURRENT_DATE - INTERVAL '90 days'"
            "  AND fund_cd IN (SELECT fund_cd FROM biz_schema.tb_adw_fnb601m"
            "   WHERE std_dt = CURRENT_DATE"
            "   ORDER BY eval_amt DESC LIMIT 5)"
            " ORDER BY std_dt, fund_cd",
        ),
        (
            "분배금 현황",
            ["TB_ADW_FNB601M"],
            "이달 분배금 지급 현황",
            "SELECT fund_cd, SUM(div_amt) AS total_div"
            " FROM biz_schema.tb_adw_fnb601m"
            " WHERE div_dt >= DATE_TRUNC('month', CURRENT_DATE)"
            " GROUP BY 1 ORDER BY 2 DESC",
        ),
        (
            "펀드 포트폴리오 현황",
            ["TB_ADW_FNB601M"],
            "고객별 펀드 유형 포트폴리오 구성 현황",
            "SELECT edps_csn, fnd_dcd,"
            " SUM(eval_amt) AS eval_amt"
            " FROM biz_schema.tb_adw_fnb601m"
            " WHERE std_dt = CURRENT_DATE"
            " GROUP BY 1, 2 ORDER BY 1, 3 DESC",
        ),
        (
            "벤치마크 대비 수익률",
            ["TB_ADW_FNB601M"],
            "펀드 수익률과 벤치마크 수익률 비교",
            "SELECT fund_cd, avg_erns_rt, bm_rt,"
            " avg_erns_rt - bm_rt AS excess_rt"
            " FROM ("
            "  SELECT fund_cd,"
            "   ROUND(AVG(erns_rt)::NUMERIC, 2) AS avg_erns_rt,"
            "   ROUND(AVG(bm_rt)::NUMERIC, 2) AS bm_rt"
            "  FROM biz_schema.tb_adw_fnb601m"
            "  WHERE std_dt = CURRENT_DATE GROUP BY 1"
            " ) t ORDER BY excess_rt DESC",
        ),
    ],

    "INS": [
        (
            "보험 계약 현황",
            ["TB_ADW_INS803M"],
            "보험 상태별 계약 건수 및 보험료",
            "SELECT ins_stcd, COUNT(*) AS cnt,"
            " SUM(ins_prem_amt) AS total_prem"
            " FROM biz_schema.tb_adw_ins803m"
            " WHERE std_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 2 DESC",
        ),
        (
            "보험 유형별 현황",
            ["TB_ADW_INS803M"],
            "보험 구분 코드별 계약 수 및 보험료",
            "SELECT ins_dcd, COUNT(*) AS cnt,"
            " SUM(ins_prem_amt) AS total_prem"
            " FROM biz_schema.tb_adw_ins803m"
            " WHERE std_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 2 DESC",
        ),
        (
            "보험 납입 현황",
            ["TB_ADW_INS803M"],
            "이달 보험료 납입 현황",
            "SELECT ins_dcd, COUNT(*) AS cnt,"
            " SUM(ins_prem_amt) AS total_prem"
            " FROM biz_schema.tb_adw_ins803m"
            " WHERE pay_dt >= DATE_TRUNC('month', CURRENT_DATE)"
            " GROUP BY 1 ORDER BY 3 DESC",
        ),
        (
            "보험 청구 현황",
            ["TB_ADW_INS803M"],
            "이달 보험금 청구 건수 및 금액",
            "SELECT ins_dcd, COUNT(*) AS cnt,"
            " SUM(claim_amt) AS total_claim"
            " FROM biz_schema.tb_adw_ins803m"
            " WHERE claim_dt >= DATE_TRUNC('month', CURRENT_DATE)"
            " GROUP BY 1 ORDER BY 3 DESC",
        ),
        (
            "보험 해약 현황",
            ["TB_ADW_INS803M"],
            "이달 해약 건수 및 해약환급금",
            "SELECT ins_dcd, COUNT(*) AS cnt,"
            " SUM(srnd_amt) AS total_srnd"
            " FROM biz_schema.tb_adw_ins803m"
            " WHERE srnd_dt >= DATE_TRUNC('month', CURRENT_DATE)"
            " GROUP BY 1 ORDER BY 2 DESC",
        ),
        (
            "보험 판매 실적",
            ["TB_ADW_INS803M"],
            "이달 신규 판매 보험 건수 및 초회 보험료",
            "SELECT ins_dcd, COUNT(*) AS cnt,"
            " SUM(ins_prem_amt) AS first_prem"
            " FROM biz_schema.tb_adw_ins803m"
            " WHERE cntr_dt >= DATE_TRUNC('month', CURRENT_DATE)"
            " GROUP BY 1 ORDER BY 2 DESC",
        ),
        (
            "보험 수수료 현황",
            ["TB_ADW_INS803M"],
            "보험 유형별 수수료 현황",
            "SELECT ins_dcd, SUM(fee_amt) AS total_fee"
            " FROM biz_schema.tb_adw_ins803m"
            " WHERE std_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 2 DESC",
        ),
        (
            "갱신 도래 보험 현황",
            ["TB_ADW_INS803M"],
            "향후 30일 내 갱신 도래 보험 목록",
            "SELECT ins_no, edps_csn, ins_dcd, rnwl_dt, ins_prem_amt"
            " FROM biz_schema.tb_adw_ins803m"
            " WHERE rnwl_dt BETWEEN CURRENT_DATE"  # noqa: E501
            " AND CURRENT_DATE + INTERVAL '30 days'"
            " ORDER BY rnwl_dt LIMIT 500",
        ),
        (
            "보험 민원 현황",
            ["TB_ADW_INS803M"],
            "보험 민원 접수 건수 및 유형",
            "SELECT cmplt_dcd, COUNT(*) AS cnt"
            " FROM biz_schema.tb_adw_ins803m"
            " WHERE cmplt_dt >= DATE_TRUNC('month', CURRENT_DATE)"
            " GROUP BY 1 ORDER BY 2 DESC",
        ),
        (
            "보험 상태 변경 이력",
            ["TB_ADW_INS803M"],
            "이달 보험 계약 상태 변경 건수",
            "SELECT ins_stcd, COUNT(*) AS cnt"
            " FROM biz_schema.tb_adw_ins803m"
            " WHERE stat_dt >= DATE_TRUNC('month', CURRENT_DATE)"
            " GROUP BY 1 ORDER BY 2 DESC",
        ),
    ],

    "PEN": [
        (
            "연금 잔고 현황",
            ["TB_ADW_PNB903L"],
            "연금 제도별 잔고 및 계약 수",
            "SELECT pn_dcd, COUNT(*) AS cnt, SUM(pn_bal_amt) AS total_bal"
            " FROM biz_schema.tb_adw_pnb903l"
            " WHERE base_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 3 DESC",
        ),
        (
            "연금 유형별 현황",
            ["TB_ADW_PNB903L"],
            "연금 구분(DB/DC/IRP 등) 코드별 잔고",
            "SELECT cntr_dcd, COUNT(*) AS cnt,"
            " SUM(pn_bal_amt) AS total_bal"
            " FROM biz_schema.tb_adw_pnb903l"
            " WHERE base_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 3 DESC",
        ),
        (
            "연금 납입 현황",
            ["TB_ADW_PNB903L"],
            "이달 연금 납입 건수 및 금액",
            "SELECT pn_dcd, COUNT(*) AS cnt,"
            " SUM(pay_amt) AS total_pay"
            " FROM biz_schema.tb_adw_pnb903l"
            " WHERE pay_dt >= DATE_TRUNC('month', CURRENT_DATE)"
            " GROUP BY 1 ORDER BY 3 DESC",
        ),
        (
            "연금 수익률 현황",
            ["TB_ADW_PNB903L"],
            "연금 유형별 평균 수익률",
            "SELECT pn_dcd,"
            " ROUND(AVG(erns_rt)::NUMERIC, 2) AS avg_rt"
            " FROM biz_schema.tb_adw_pnb903l"
            " WHERE base_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 2 DESC",
        ),
        (
            "IRP 계좌 현황",
            ["TB_ADW_PNB903L"],
            "IRP 계좌 수 및 총잔고",
            "SELECT COUNT(*) AS cnt, SUM(pn_bal_amt) AS total_bal,"
            " ROUND(AVG(erns_rt)::NUMERIC, 2) AS avg_rt"
            " FROM biz_schema.tb_adw_pnb903l"
            " WHERE base_dt = CURRENT_DATE AND pn_dcd = 'IRP'",
        ),
        (
            "연금 지급 현황",
            ["TB_ADW_PNB903L"],
            "이달 연금 지급 건수 및 금액",
            "SELECT pn_dcd, COUNT(*) AS cnt,"
            " SUM(pay_amt) AS total_pay"
            " FROM biz_schema.tb_adw_pnb903l"
            " WHERE pay_dt >= DATE_TRUNC('month', CURRENT_DATE)"
            "  AND pay_dcd = 'PAY'"
            " GROUP BY 1 ORDER BY 3 DESC",
        ),
        (
            "연금 이전 현황",
            ["TB_ADW_PNB903L"],
            "이달 연금 이전 건수 및 금액",
            "SELECT COUNT(*) AS cnt, SUM(pn_bal_amt) AS total_amt"
            " FROM biz_schema.tb_adw_pnb903l"
            " WHERE trns_dt >= DATE_TRUNC('month', CURRENT_DATE)",
        ),
        (
            "연금 투자 선택 현황",
            ["TB_ADW_PNB903L"],
            "연금 투자 상품별 잔고 분포",
            "SELECT invest_pd_cd, COUNT(*) AS cnt,"
            " SUM(pn_bal_amt) AS total_bal"
            " FROM biz_schema.tb_adw_pnb903l"
            " WHERE base_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 3 DESC",
        ),
        (
            "DB/DC 연금 비교",
            ["TB_ADW_PNB903L"],
            "확정급여(DB)와 확정기여(DC) 제도 잔고 비교",
            "SELECT pn_dcd,"
            " COUNT(*) AS cnt,"
            " SUM(pn_bal_amt) AS total_bal"
            " FROM biz_schema.tb_adw_pnb903l"
            " WHERE base_dt = CURRENT_DATE"
            "  AND pn_dcd IN ('DB', 'DC')"
            " GROUP BY 1 ORDER BY 1",
        ),
        (
            "연금 수수료 현황",
            ["TB_ADW_PNB903L"],
            "연금 유형별 수수료 수익 현황",
            "SELECT pn_dcd, SUM(fee_amt) AS total_fee"
            " FROM biz_schema.tb_adw_pnb903l"
            " WHERE base_dt >= DATE_TRUNC('month', CURRENT_DATE)"
            " GROUP BY 1 ORDER BY 2 DESC",
        ),
    ],

    "RSK": [
        (
            "BIS 비율 현황",
            ["TB_ADW_RSK1101M"],
            "기준일 BIS 비율 지표 현황",
            "SELECT ind_cd, ind_val, calc_dt"
            " FROM biz_schema.tb_adw_rsk1101m"
            " WHERE std_dt = CURRENT_DATE AND ind_cd = 'BIS_RATIO'"
            " ORDER BY calc_dt DESC LIMIT 1",
        ),
        (
            "VaR 현황",
            ["TB_ADW_RSK1101M"],
            "시장리스크 VaR 지표 현황",
            "SELECT ind_cd, ind_val, calc_dt"
            " FROM biz_schema.tb_adw_rsk1101m"
            " WHERE ind_cd LIKE 'VAR%' AND std_dt = CURRENT_DATE"
            " ORDER BY ind_cd",
        ),
        (
            "신용 등급 분포",
            ["TB_ADW_RSK1101M"],
            "신용 등급 코드별 여신 건수 분포",
            "SELECT crsc_grd_cd, COUNT(*) AS cnt"
            " FROM biz_schema.tb_adw_rsk1101m"
            " WHERE std_dt = CURRENT_DATE AND crsc_grd_cd IS NOT NULL"
            " GROUP BY 1 ORDER BY 1",
        ),
        (
            "ECL 현황",
            ["TB_ADW_RSK1101M"],
            "예상 신용 손실(ECL) 지표 현황",
            "SELECT rsk_stage_cd,"
            " SUM(ind_val) AS total_ecl"
            " FROM biz_schema.tb_adw_rsk1101m"
            " WHERE ind_cd = 'ECL' AND std_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 1",
        ),
        (
            "시장 리스크 현황",
            ["TB_ADW_RSK1101M"],
            "시장 리스크 주요 지표 현황",
            "SELECT ind_cd, ind_val, calc_dt"
            " FROM biz_schema.tb_adw_rsk1101m"
            " WHERE ind_cd IN ('MKT_RSK', 'DELTA', 'GAMMA', 'VEGA')"
            "  AND std_dt = CURRENT_DATE"
            " ORDER BY ind_cd",
        ),
        (
            "운영 리스크 현황",
            ["TB_ADW_RSK1101M"],
            "운영 리스크 손실 이벤트 집계",
            "SELECT ind_cd, SUM(ind_val) AS total_loss"
            " FROM biz_schema.tb_adw_rsk1101m"
            " WHERE ind_cd LIKE 'OPR%'"
            "  AND std_dt >= DATE_TRUNC('month', CURRENT_DATE)"
            " GROUP BY 1 ORDER BY 2 DESC",
        ),
        (
            "AML 경보 현황",
            ["TB_ADW_AML1116M"],
            "AML 경보 등급별 건수 현황",
            "SELECT alert_lvl_cd, COUNT(*) AS cnt"
            " FROM biz_schema.tb_adw_aml1116m"
            " WHERE base_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 1",
        ),
        (
            "KRI 현황",
            ["TB_ADW_RSK1101M"],
            "핵심 리스크 지표(KRI) 현황",
            "SELECT ind_cd, ind_val,"
            " CASE WHEN ind_val > threshold_val"
            "  THEN '초과' ELSE '정상' END AS status"
            " FROM biz_schema.tb_adw_rsk1101m"
            " WHERE ind_cd LIKE 'KRI%' AND std_dt = CURRENT_DATE"
            " ORDER BY ind_cd",
        ),
        (
            "스트레스 테스트 결과",
            ["TB_ADW_RSK1101M"],
            "스트레스 시나리오별 리스크 지표 결과",
            "SELECT scenario_cd, ind_cd, ind_val"
            " FROM biz_schema.tb_adw_rsk1101m"
            " WHERE ind_cd LIKE 'STRESS%' AND std_dt = CURRENT_DATE"
            " ORDER BY scenario_cd, ind_cd",
        ),
        (
            "LCR 현황",
            ["TB_ADW_RSK1101M"],
            "유동성 커버리지 비율(LCR) 현황",
            "SELECT ind_val AS lcr_ratio, calc_dt"
            " FROM biz_schema.tb_adw_rsk1101m"
            " WHERE ind_cd = 'LCR' AND std_dt = CURRENT_DATE"
            " ORDER BY calc_dt DESC LIMIT 1",
        ),
    ],

    "MKT": [
        (
            "캠페인 성과 현황",
            ["TB_ADW_MKT1201M"],
            "캠페인별 대상 수 및 응답 건수",
            "SELECT camp_cd, COUNT(*) AS target_cnt,"
            " SUM(CASE WHEN resp_yn = 'Y' THEN 1 ELSE 0 END) AS resp_cnt"
            " FROM biz_schema.tb_adw_mkt1201m"
            " WHERE std_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 3 DESC",
        ),
        (
            "캠페인 대상 고객 현황",
            ["TB_ADW_MKT1201M"],
            "캠페인별 대상 고객 유형 분포",
            "SELECT camp_cd, camp_tgt_dcd, COUNT(*) AS cnt"
            " FROM biz_schema.tb_adw_mkt1201m"
            " WHERE std_dt = CURRENT_DATE"
            " GROUP BY 1, 2 ORDER BY 1, 3 DESC",
        ),
        (
            "캠페인 응답률",
            ["TB_ADW_MKT1201M", "TB_ADW_MKT1202M"],
            "캠페인별 응답률 집계",
            "SELECT m.camp_cd,"
            " COUNT(m.edps_csn) AS target_cnt,"
            " COUNT(r.edps_csn) AS resp_cnt,"
            " ROUND(COUNT(r.edps_csn)::NUMERIC"
            " / NULLIF(COUNT(m.edps_csn), 0) * 100, 1) AS resp_rate"
            " FROM biz_schema.tb_adw_mkt1201m m"
            " LEFT JOIN biz_schema.tb_adw_mkt1202m r"
            "  ON m.camp_cd = r.camp_cd AND m.edps_csn = r.edps_csn"
            "  AND r.resp_yn = 'Y'"
            " WHERE m.std_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 4 DESC",
        ),
        (
            "오퍼 현황",
            ["TB_ADW_MKT1201M"],
            "오퍼 유형별 캠페인 건수 및 대상 수",
            "SELECT ofer_dcd, COUNT(DISTINCT camp_cd) AS camp_cnt,"
            " COUNT(*) AS target_cnt"
            " FROM biz_schema.tb_adw_mkt1201m"
            " WHERE std_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 3 DESC",
        ),
        (
            "교차 판매 현황",
            ["TB_ADW_MKT1201M", "TB_ADW_MKT1202M"],
            "교차 판매 캠페인 성과 집계",
            "SELECT m.camp_cd,"
            " COUNT(r.edps_csn) AS cross_sell_cnt"
            " FROM biz_schema.tb_adw_mkt1201m m"
            " JOIN biz_schema.tb_adw_mkt1202m r"
            "  ON m.camp_cd = r.camp_cd AND m.edps_csn = r.edps_csn"
            " WHERE m.camp_tgt_dcd = 'CS' AND r.resp_yn = 'Y'"
            "  AND m.std_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 2 DESC",
        ),
        (
            "이탈 예측 대상 고객",
            ["TB_ADW_MKT1201M"],
            "이탈 위험 고객 캠페인 대상 목록",
            "SELECT edps_csn, camp_cd, churn_scr"
            " FROM biz_schema.tb_adw_mkt1201m"
            " WHERE camp_tgt_dcd = 'CH' AND std_dt = CURRENT_DATE"
            " ORDER BY churn_scr DESC LIMIT 200",
        ),
        (
            "NPS 현황",
            ["TB_ADW_MKT1202M"],
            "순추천지수(NPS) 설문 응답 현황",
            "SELECT nps_grd,"
            " COUNT(*) AS cnt"
            " FROM biz_schema.tb_adw_mkt1202m"
            " WHERE resp_dt >= DATE_TRUNC('month', CURRENT_DATE)"
            "  AND nps_grd IS NOT NULL"
            " GROUP BY 1 ORDER BY 1",
        ),
        (
            "고객 세그먼트별 현황",
            ["TB_ADW_MKT1201M"],
            "마케팅 세그먼트별 고객 수 및 응답 현황",
            "SELECT seg_cd, COUNT(*) AS cnt,"
            " SUM(CASE WHEN resp_yn = 'Y' THEN 1 ELSE 0 END) AS resp_cnt"
            " FROM biz_schema.tb_adw_mkt1201m"
            " WHERE std_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 2 DESC",
        ),
        (
            "이벤트 참여 현황",
            ["TB_ADW_MKT1202M"],
            "이벤트 유형별 참여 고객 수",
            "SELECT evnt_dcd, COUNT(*) AS cnt"
            " FROM biz_schema.tb_adw_mkt1202m"
            " WHERE resp_dt >= DATE_TRUNC('month', CURRENT_DATE)"
            "  AND evnt_dcd IS NOT NULL"
            " GROUP BY 1 ORDER BY 2 DESC",
        ),
        (
            "CLV 상위 고객",
            ["TB_ADW_MKT1201M"],
            "고객 생애 가치(CLV) 상위 100명",
            "SELECT edps_csn, clv_score"
            " FROM biz_schema.tb_adw_mkt1201m"
            " WHERE std_dt = CURRENT_DATE AND clv_score IS NOT NULL"
            " ORDER BY clv_score DESC LIMIT 100",
        ),
    ],

    "FIN": [
        (
            "손익 요약",
            ["TB_ADW_FIN1306S"],
            "손익 항목별 실적 요약",
            "SELECT pl_item_cd, SUM(amt) AS total_amt"
            " FROM biz_schema.tb_adw_fin1306s"
            " WHERE base_ym = TO_CHAR(CURRENT_DATE, 'YYYYMM')"
            " GROUP BY 1 ORDER BY 2 DESC",
        ),
        (
            "KPI 실적 현황",
            ["TB_ADW_FIN1306S"],
            "KPI 항목별 목표 대비 실적",
            "SELECT pl_item_cd, SUM(budget_amt) AS budget,"
            " SUM(amt) AS actual,"
            " ROUND(SUM(amt)::NUMERIC"
            " / NULLIF(SUM(budget_amt), 0) * 100, 1) AS achv_rate"
            " FROM biz_schema.tb_adw_fin1306s"
            " WHERE base_ym = TO_CHAR(CURRENT_DATE, 'YYYYMM')"
            " GROUP BY 1 ORDER BY 4 DESC",
        ),
        (
            "지점별 경영 성과",
            ["TB_ADW_FIN1306S", "TB_ADW_COM001M"],
            "부점별 손익 실적 집계",
            "SELECT b.br_nm, SUM(f.amt) AS total_pl"
            " FROM biz_schema.tb_adw_fin1306s f"
            " JOIN biz_schema.tb_adw_com001m b ON f.blng_brcd = b.blng_brcd"
            " WHERE f.base_ym = TO_CHAR(CURRENT_DATE, 'YYYYMM')"
            " GROUP BY 1 ORDER BY 2 DESC LIMIT 20",
        ),
        (
            "예산 실적 비교",
            ["TB_ADW_FIN1306S"],
            "월별 예산 대비 실적 현황",
            "SELECT base_ym,"
            " SUM(budget_amt) AS budget,"
            " SUM(amt) AS actual"
            " FROM biz_schema.tb_adw_fin1306s"
            " WHERE base_ym >= TO_CHAR("  # noqa: E501
            "CURRENT_DATE - INTERVAL '6 months', 'YYYYMM')"
            " GROUP BY 1 ORDER BY 1",
        ),
        (
            "수수료 수익 현황",
            ["TB_ADW_FIN1306S"],
            "수수료 항목별 수익 현황",
            "SELECT pl_item_cd, SUM(amt) AS total_fee"
            " FROM biz_schema.tb_adw_fin1306s"
            " WHERE base_ym = TO_CHAR(CURRENT_DATE, 'YYYYMM')"
            "  AND pl_item_cd LIKE 'FEE%'"
            " GROUP BY 1 ORDER BY 2 DESC",
        ),
        (
            "충당금 현황",
            ["TB_ADW_FIN1306S"],
            "충당금 설정 및 환입 현황",
            "SELECT pl_item_cd, SUM(amt) AS total_amt"
            " FROM biz_schema.tb_adw_fin1306s"
            " WHERE base_ym = TO_CHAR(CURRENT_DATE, 'YYYYMM')"
            "  AND pl_item_cd LIKE 'PRVS%'"
            " GROUP BY 1 ORDER BY 2",
        ),
        (
            "계정 잔액 현황",
            ["TB_ADW_FIN1306S"],
            "총계정원장 주요 계정 잔액",
            "SELECT gl_acct_cd, SUM(bal_amt) AS total_bal"
            " FROM biz_schema.tb_adw_fin1306s"
            " WHERE base_ym = TO_CHAR(CURRENT_DATE, 'YYYYMM')"
            " GROUP BY 1 ORDER BY 2 DESC LIMIT 30",
        ),
        (
            "NIM 현황",
            ["TB_ADW_FIN1306S"],
            "순이자마진(NIM) 지표 현황",
            "SELECT base_ym,"
            " ROUND(SUM(CASE WHEN pl_item_cd = 'INT_INC' THEN amt ELSE 0 END)"
            "  - SUM(CASE WHEN pl_item_cd = 'INT_EXP' THEN amt ELSE 0 END)"
            "  , 0) AS nim_amt"
            " FROM biz_schema.tb_adw_fin1306s"
            " WHERE base_ym >= TO_CHAR("  # noqa: E501
            "CURRENT_DATE - INTERVAL '12 months', 'YYYYMM')"
            " GROUP BY 1 ORDER BY 1",
        ),
        (
            "직원 성과 현황",
            ["TB_ADW_FIN1306S"],
            "직원별 실적 집계 (부점 기준)",
            "SELECT blng_brcd, SUM(amt) AS perf_amt"
            " FROM biz_schema.tb_adw_fin1306s"
            " WHERE base_ym = TO_CHAR(CURRENT_DATE, 'YYYYMM')"
            " GROUP BY 1 ORDER BY 2 DESC LIMIT 30",
        ),
        (
            "대차 대조표 현황",
            ["TB_ADW_FIN1306S"],
            "자산/부채/자본 주요 계정 잔액",
            "SELECT acct_type_cd, SUM(bal_amt) AS total_bal"
            " FROM biz_schema.tb_adw_fin1306s"
            " WHERE base_ym = TO_CHAR(CURRENT_DATE, 'YYYYMM')"
            "  AND acct_type_cd IN ('ASSET', 'LIAB', 'EQUITY')"
            " GROUP BY 1 ORDER BY 1",
        ),
    ],

    "TRS": [
        (
            "신탁 계좌 현황",
            ["TB_ADW_TRS616M"],
            "신탁 유형별 계좌 수 및 잔고",
            "SELECT trust_dcd, COUNT(*) AS cnt,"
            " SUM(trust_bal_amt) AS total_bal"
            " FROM biz_schema.tb_adw_trs616m"
            " WHERE std_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 3 DESC",
        ),
        (
            "신탁 재산 구성 현황",
            ["TB_ADW_TRS617M"],
            "신탁 재산 자산 유형별 구성",
            "SELECT asset_dcd, COUNT(*) AS cnt,"
            " SUM(asset_amt) AS total_amt"
            " FROM biz_schema.tb_adw_trs617m"
            " WHERE std_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 3 DESC",
        ),
        (
            "신탁 잔고 현황",
            ["TB_ADW_TRS618P"],
            "신탁 계좌별 잔고 상위 100개",
            "SELECT trust_no, edps_csn, trust_bal_amt, eval_dt"
            " FROM biz_schema.tb_adw_trs618p"
            " WHERE std_dt = CURRENT_DATE"
            " ORDER BY trust_bal_amt DESC LIMIT 100",
        ),
        (
            "신탁 거래 내역",
            ["TB_ADW_TRS619L"],
            "이달 신탁 거래 유형별 건수 및 금액",
            "SELECT tr_dcd, COUNT(*) AS cnt,"
            " SUM(tr_amt) AS total_amt"
            " FROM biz_schema.tb_adw_trs619l"
            " WHERE tr_dt >= DATE_TRUNC('month', CURRENT_DATE)"
            " GROUP BY 1 ORDER BY 2 DESC",
        ),
        (
            "신탁 평가 현황",
            ["TB_ADW_TRS620P"],
            "신탁 계좌별 평가 수익률 현황",
            "SELECT trust_no,"
            " ROUND(AVG(erns_rt)::NUMERIC, 2) AS avg_rt"
            " FROM biz_schema.tb_adw_trs620p"
            " WHERE eval_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 2 DESC LIMIT 100",
        ),
        (
            "신탁 보수 현황",
            ["TB_ADW_TRS621L"],
            "이달 신탁 보수 부과 현황",
            "SELECT fee_dcd, COUNT(*) AS cnt,"
            " SUM(fee_amt) AS total_fee"
            " FROM biz_schema.tb_adw_trs621l"
            " WHERE fee_dt >= DATE_TRUNC('month', CURRENT_DATE)"
            " GROUP BY 1 ORDER BY 3 DESC",
        ),
        (
            "신탁 계약 현황",
            ["TB_ADW_TRS622M"],
            "신탁 계약 상태별 건수",
            "SELECT cntr_stcd, COUNT(*) AS cnt,"
            " SUM(cntr_amt) AS total_amt"
            " FROM biz_schema.tb_adw_trs622m"
            " WHERE std_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 2 DESC",
        ),
        (
            "신탁 수익자 현황",
            ["TB_ADW_TRS623M"],
            "수익자 유형별 신탁 계좌 수",
            "SELECT bene_dcd, COUNT(*) AS cnt"
            " FROM biz_schema.tb_adw_trs623m"
            " WHERE std_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 2 DESC",
        ),
        (
            "신탁 만기 도래 현황",
            ["TB_ADW_TRS625M"],
            "향후 30일 내 만기 도래 신탁 목록",
            "SELECT trust_no, edps_csn, trust_bal_amt, mtrty_dt"
            " FROM biz_schema.tb_adw_trs625m"
            " WHERE mtrty_dt BETWEEN CURRENT_DATE"  # noqa: E501
            " AND CURRENT_DATE + INTERVAL '30 days'"
            " ORDER BY mtrty_dt LIMIT 200",
        ),
        (
            "신탁 월별 잔고 추이",
            ["TB_ADW_TRS618P"],
            "월별 신탁 총잔고 및 계좌 수 추이",
            "SELECT DATE_TRUNC('month', std_dt) AS m,"
            " COUNT(*) AS cnt,"
            " SUM(trust_bal_amt) AS total_bal"
            " FROM biz_schema.tb_adw_trs618p"
            " GROUP BY 1 ORDER BY 1",
        ),
    ],

    "WM": [
        (
            "WM 고객 자산 현황",
            ["TB_ADW_WMB1401M"],
            "WM 고객별 총 관리 자산 현황",
            "SELECT edps_csn, wm_grd_cd,"
            " SUM(tot_ast_amt) AS total_ast"
            " FROM biz_schema.tb_adw_wmb1401m"
            " WHERE std_dt = CURRENT_DATE"
            " GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 100",
        ),
        (
            "WM 포트폴리오 현황",
            ["TB_ADW_WMB1401M"],
            "자산 유형별 포트폴리오 구성 비중",
            "SELECT ast_type_cd,"
            " SUM(ast_amt) AS total_ast,"
            " ROUND(SUM(ast_amt)::NUMERIC"
            " / NULLIF(SUM(SUM(ast_amt)) OVER (), 0) * 100, 1) AS ratio"
            " FROM biz_schema.tb_adw_wmb1401m"
            " WHERE std_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 2 DESC",
        ),
        (
            "WM 투자 성향 분포",
            ["TB_ADW_WMB1401M"],
            "고객 투자 성향 코드별 인원 분포",
            "SELECT invest_prfl_cd, COUNT(*) AS cnt"
            " FROM biz_schema.tb_adw_wmb1401m"
            " WHERE std_dt = CURRENT_DATE"
            " GROUP BY 1 ORDER BY 2 DESC",
        ),
        (
            "리밸런싱 대상 고객",
            ["TB_ADW_WMB1401M"],
            "목표 비중 대비 실제 비중 이탈 고객 목록",
            "SELECT edps_csn, ast_type_cd,"
            " tgt_ratio, act_ratio,"
            " ABS(act_ratio - tgt_ratio) AS drift"
            " FROM biz_schema.tb_adw_wmb1401m"
            " WHERE std_dt = CURRENT_DATE"
            "  AND ABS(act_ratio - tgt_ratio) > 5.0"
            " ORDER BY drift DESC LIMIT 100",
        ),
        (
            "WM 상담 현황",
            ["TB_ADW_WMB1401M"],
            "이달 WM 상담 건수 및 유형",
            "SELECT cnslt_dcd, COUNT(*) AS cnt"
            " FROM biz_schema.tb_adw_wmb1401m"
            " WHERE cnslt_dt >= DATE_TRUNC('month', CURRENT_DATE)"
            " GROUP BY 1 ORDER BY 2 DESC",
        ),
        (
            "WM 제안서 현황",
            ["TB_ADW_WMB1401M"],
            "이달 제안서 작성 및 수락 현황",
            "SELECT prop_stcd, COUNT(*) AS cnt"
            " FROM biz_schema.tb_adw_wmb1401m"
            " WHERE prop_dt >= DATE_TRUNC('month', CURRENT_DATE)"
            " GROUP BY 1 ORDER BY 2 DESC",
        ),
        (
            "WM 성과 보고",
            ["TB_ADW_WMB1401M"],
            "고객별 WM 포트폴리오 수익률 현황",
            "SELECT edps_csn, wm_grd_cd,"
            " ROUND(AVG(erns_rt)::NUMERIC, 2) AS avg_rt"
            " FROM biz_schema.tb_adw_wmb1401m"
            " WHERE std_dt = CURRENT_DATE"
            " GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 100",
        ),
        (
            "WM 세미나 참여 현황",
            ["TB_ADW_WMB1401M"],
            "이달 WM 세미나 참여 고객 수",
            "SELECT smn_cd, COUNT(*) AS cnt"
            " FROM biz_schema.tb_adw_wmb1401m"
            " WHERE smn_dt >= DATE_TRUNC('month', CURRENT_DATE)"
            " GROUP BY 1 ORDER BY 2 DESC",
        ),
        (
            "모델 포트폴리오 현황",
            ["TB_ADW_WMB1401M"],
            "모델 포트폴리오 유형별 채택 고객 수",
            "SELECT mdl_pf_cd, COUNT(*) AS cnt,"
            " SUM(tot_ast_amt) AS total_ast"
            " FROM biz_schema.tb_adw_wmb1401m"
            " WHERE std_dt = CURRENT_DATE AND mdl_pf_cd IS NOT NULL"
            " GROUP BY 1 ORDER BY 2 DESC",
        ),
        (
            "WM 월별 관리 자산 추이",
            ["TB_ADW_WMB1401M"],
            "월별 WM 총 관리 자산(AUM) 추이",
            "SELECT DATE_TRUNC('month', std_dt) AS m,"
            " SUM(tot_ast_amt) AS aum"
            " FROM biz_schema.tb_adw_wmb1401m"
            " GROUP BY 1 ORDER BY 1",
        ),
    ],
}


# ══════════════════════════════════════════════════════════════
# 도큐먼트 빌더
# ══════════════════════════════════════════════════════════════

def build_docs() -> list[dict[str, Any]]:
    """DOMAIN_REPORTS에서 ES bulk 적재용 도큐먼트 목록 생성.

    Returns:
        _id START_ID~(START_ID+139) 인 140개 도큐먼트 리스트.
        각 도큐먼트는 report_sql 인덱스 스키마와 일치한다.
    """
    docs: list[dict[str, Any]] = []
    doc_id = START_ID

    for domain_cd, reports in DOMAIN_REPORTS.items():
        for report_nm, tables_used, report_desc, sql_text in reports:
            docs.append({
                "_index": INDEX_NAME,
                "_id": str(doc_id),
                "_source": {
                    "report_nm": report_nm,
                    "report_desc": report_desc,
                    "domain_cd": domain_cd,
                    "tables_used": tables_used,
                    "sql_text": sql_text,
                },
            })
            doc_id += 1

    return docs


# ══════════════════════════════════════════════════════════════
# ES 적재
# ══════════════════════════════════════════════════════════════

def run() -> None:
    """140건을 report_sql 인덱스에 bulk 적재하고 총 건수를 출력."""
    try:
        from elasticsearch import Elasticsearch
        from elasticsearch.helpers import bulk
    except ImportError:
        print("elasticsearch 패키지가 설치되어 있지 않습니다.")
        print("  pip install elasticsearch")
        sys.exit(1)

    es = Elasticsearch(
        ES_URL,
        basic_auth=(ES_USER, ES_PASSWORD),
        request_timeout=30,
    )

    if not es.ping():
        print(f"ES 연결 실패: {ES_URL}")
        sys.exit(1)

    # 인덱스 존재 여부 확인
    if not es.indices.exists(index=INDEX_NAME):
        print(f"인덱스 '{INDEX_NAME}' 가 존재하지 않습니다.")
        print("먼저 seed_elasticsearch.py 를 실행하여 인덱스를 생성하세요.")
        sys.exit(1)

    # 현재 건수 확인
    before_count = es.count(index=INDEX_NAME)["count"]
    print(f"[report_sql] 현재 건수: {before_count}건")

    # bulk 적재
    docs = build_docs()
    last_id = START_ID + len(docs) - 1
    print(
        f"[report_sql] 추가 적재 대상: {len(docs)}건"
        f" (_id {START_ID}~{last_id})"
    )

    ok, errors = bulk(es, docs, raise_on_error=False, stats_only=False)

    # 오류 집계
    error_cnt = 0
    for item in errors:
        op = next(iter(item))
        info = item[op]
        # 버전 충돌(이미 존재하는 _id) 은 경고만 출력
        if info.get("status") == 409:
            print(f"  [경고] _id={info['_id']} 이미 존재 (버전 충돌 무시)")
        else:
            error_cnt += 1
            print(f"  [오류] _id={info.get('_id')} status={info.get('status')} "
                  f"error={info.get('error', {}).get('reason', '')}")

    es.indices.refresh(index=INDEX_NAME)
    after_count = es.count(index=INDEX_NAME)["count"]

    print(f"[report_sql] 적재 완료: {ok}건 성공 / {error_cnt}건 오류")
    print(f"[report_sql] 최종 총 건수: {after_count}건")


if __name__ == "__main__":
    run()
