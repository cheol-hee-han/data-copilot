"""커넥터 Dummy 모드용 샘플 데이터 — 폐쇄망 개발/테스트 지원.

외부 인프라(ElasticSearch, PostgreSQL, Qdrant) 없이도 전체 파이프라인을
end-to-end로 실행할 수 있도록 은행 도메인 기반 내장 샘플 데이터를 제공한다.
ES용으로 테이블 메타 6종(고객/여신/수신/거래/지점/연체통계), 보고서 SQL 3종,
코드 메타 7종(고객유형/등급/대출유형/거래유형/계좌상태/성별/연체여부)을 포함하고,
PostgreSQL용으로 과거 SQL 수행이력 5건, Qdrant용으로 업무 매뉴얼 5건 및
SQL 수행이력(벡터 검색용) 5건을 제공한다.
SQL 실행 결과 Dummy 생성 시에는 SQL을 파싱하여 SELECT alias에 맞는 타입별
랜덤 데이터(금액, 건수, 비율, 날짜, 코드값 등)를 자동 생성한다.

핵심 함수/클래스:
    - generate_dummy_data: SQL 파싱 후 alias 기반 랜덤 행 데이터 생성
    - search_dummy_table_meta: 키워드 매칭 기반 테이블 메타 검색
    - search_dummy_report_sql: 키워드 매칭 기반 보고서 SQL 검색
    - search_dummy_code_meta: 코드 필드명 매칭 기반 코드 메타 검색
    - search_dummy_sql_history: 키워드 매칭 기반 과거 SQL 이력 검색
    - search_dummy_manuals: 키워드 점수 기반 업무 매뉴얼 검색
    - search_dummy_qdrant_sql_history: 키워드 점수 기반 SQL 수행이력 검색

폐쇄망 대응: 온라인 개발 환경에서 외부 시스템 없이 파이프라인 검증이 가능하며,
실제 배포 시에는 각 커넥터의 use_dummy=False 전환으로 실 데이터 소스에 연결한다.
"""

from __future__ import annotations

import random
import re
from datetime import timedelta

from src.utils.timezone import today_kst
from typing import Any


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ElasticSearch Dummy 데이터
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _col(
    name: str,
    dtype: str,
    description: str,
    alt_name: str = "",
    is_pk: bool = False,
    pii: bool = False,
    **flags: Any,
) -> dict[str, Any]:
    """컬럼 메타 dict 를 간결하게 생성한다.

    pipeline_table_meta.json 스키마 기준:
      name, alt_name, type, description, is_pk
    pii 플래그는 보안 마스킹 전용으로 별도 유지한다.
    """
    col: dict[str, Any] = {
        "name": name,
        "alt_name": alt_name,
        "type": dtype,
        "description": description,
        "is_pk": is_pk,
    }
    if pii:
        col["pii"] = True
    col.update(flags)
    return col


DUMMY_TABLE_META: list[dict[str, Any]] = [
    {
        "name": "TB_CUST_INFO",
        "alt_name": "고객기본정보",
        "description": "고객 기본 정보 테이블",
        # 하위 호환: context_explorer, planner가 table_name/table_description 참조
        "table_name": "TB_CUST_INFO",
        "table_description": "고객 기본 정보 테이블",
        "schema_name": "DW",
        "columns": [
            _col("CUST_NO", "VARCHAR(20)",
                 "고객번호", alt_name="고객번호", is_pk=True),
            _col("CUST_NM", "VARCHAR(100)",
                 "고객명", alt_name="고객명", pii=True),
            _col("REG_DT", "DATE",
                 "등록일자", alt_name="등록일자"),
            _col("CUST_TYPE_CD", "VARCHAR(2)",
                 "고객유형코드 (01:개인, 02:기업)",
                 alt_name="고객유형코드"),
            _col("BRCH_CD", "VARCHAR(10)",
                 "관리지점코드", alt_name="관리지점코드"),
            _col("GENDER_CD", "CHAR(1)",
                 "성별코드 (M/F)", alt_name="성별코드"),
            _col("AGE_GRP_CD", "VARCHAR(2)",
                 "연령대코드 (20:20대, 30:30대 ...)",
                 alt_name="연령대코드"),
            _col("CUST_GRADE_CD", "VARCHAR(10)",
                 "고객등급코드 (VIP, Gold, Silver, General)",
                 alt_name="고객등급코드"),
        ],
    },
    {
        "name": "TB_LOAN_INFO",
        "alt_name": "여신정보",
        "description": "여신(대출) 정보 테이블",
        "table_name": "TB_LOAN_INFO",
        "table_description": "여신(대출) 정보 테이블",
        "schema_name": "DW",
        "columns": [
            _col("LOAN_NO", "VARCHAR(20)",
                 "대출번호", alt_name="대출번호", is_pk=True),
            _col("CUST_NO", "VARCHAR(20)",
                 "고객번호", alt_name="고객번호",
                 fk="TB_CUST_INFO.CUST_NO"),
            _col("LOAN_AMT", "NUMERIC(18,0)",
                 "대출금액(원)", alt_name="대출금액"),
            _col("LOAN_BAL", "NUMERIC(18,0)",
                 "대출잔액(원)", alt_name="대출잔액"),
            _col("LOAN_DT", "DATE",
                 "대출실행일자", alt_name="대출실행일자"),
            _col("MTRTY_DT", "DATE",
                 "만기일자", alt_name="만기일자"),
            _col("INT_RATE", "NUMERIC(5,2)",
                 "적용금리(%)", alt_name="적용금리"),
            _col("LOAN_TYPE_CD", "VARCHAR(2)",
                 "대출유형코드 (01:신용, 02:담보, 03:보증)",
                 alt_name="대출유형코드"),
            _col("OVERDUE_YN", "CHAR(1)",
                 "연체여부 (Y/N)", alt_name="연체여부"),
            _col("OVERDUE_DAYS", "INTEGER",
                 "연체일수", alt_name="연체일수"),
            _col("OVERDUE_AMT", "NUMERIC(18,0)",
                 "연체금액(원)", alt_name="연체금액"),
        ],
    },
    {
        "name": "TB_DEPOSIT_INFO",
        "alt_name": "수신예금정보",
        "description": "수신(예금) 정보 테이블",
        "table_name": "TB_DEPOSIT_INFO",
        "table_description": "수신(예금) 정보 테이블",
        "schema_name": "DW",
        "columns": [
            _col("ACCT_NO", "VARCHAR(20)",
                 "계좌번호", alt_name="계좌번호",
                 is_pk=True, pii=True),
            _col("CUST_NO", "VARCHAR(20)",
                 "고객번호", alt_name="고객번호",
                 fk="TB_CUST_INFO.CUST_NO"),
            _col("ACCT_BAL", "NUMERIC(18,0)",
                 "계좌잔액(원)", alt_name="계좌잔액"),
            _col("OPEN_DT", "DATE",
                 "개설일자", alt_name="개설일자"),
            _col("PROD_CD", "VARCHAR(10)",
                 "상품코드", alt_name="상품코드"),
            _col("PROD_NM", "VARCHAR(100)",
                 "상품명", alt_name="상품명"),
            _col("INT_RATE", "NUMERIC(5,4)",
                 "적용금리", alt_name="적용금리"),
            _col("ACCT_STATUS_CD", "VARCHAR(2)",
                 "계좌상태코드 (01:정상, 02:해지, 03:휴면)",
                 alt_name="계좌상태코드"),
        ],
    },
    {
        "name": "TB_TRANSACTION",
        "alt_name": "거래내역",
        "description": (
            "거래 내역 테이블"
            " (대용량, 반드시 날짜 조건 필요)"
        ),
        "table_name": "TB_TRANSACTION",
        "table_description": (
            "거래 내역 테이블"
            " (대용량, 반드시 날짜 조건 필요)"
        ),
        "schema_name": "DW",
        "columns": [
            _col("TXN_NO", "VARCHAR(30)",
                 "거래번호", alt_name="거래번호", is_pk=True),
            _col("ACCT_NO", "VARCHAR(20)",
                 "계좌번호", alt_name="계좌번호", pii=True),
            _col("TXN_DT", "DATE",
                 "거래일자", alt_name="거래일자"),
            _col("TXN_TM", "VARCHAR(6)",
                 "거래시각(HHMMSS)", alt_name="거래시각"),
            _col("TXN_AMT", "NUMERIC(18,0)",
                 "거래금액(원)", alt_name="거래금액"),
            _col("TXN_TYPE_CD", "VARCHAR(2)",
                 "거래유형코드 (01:입금, 02:출금, 03:이체)",
                 alt_name="거래유형코드"),
            _col("BRCH_CD", "VARCHAR(10)",
                 "거래지점코드", alt_name="거래지점코드"),
        ],
    },
    {
        "name": "TB_BRANCH_INFO",
        "alt_name": "지점정보",
        "description": "지점 정보 테이블",
        "table_name": "TB_BRANCH_INFO",
        "table_description": "지점 정보 테이블",
        "schema_name": "DW",
        "columns": [
            _col("BRCH_CD", "VARCHAR(10)",
                 "지점코드", alt_name="지점코드", is_pk=True),
            _col("BRCH_NM", "VARCHAR(100)",
                 "지점명", alt_name="지점명"),
            _col("REGION_CD", "VARCHAR(4)",
                 "지역코드", alt_name="지역코드"),
            _col("REGION_NM", "VARCHAR(50)",
                 "지역명", alt_name="지역명"),
        ],
    },
    {
        "name": "TB_LOAN_OVERDUE_STAT",
        "alt_name": "여신연체통계",
        "description": (
            "여신 연체 통계 테이블 (월말 기준 집계)"
        ),
        "table_name": "TB_LOAN_OVERDUE_STAT",
        "table_description": (
            "여신 연체 통계 테이블 (월말 기준 집계)"
        ),
        "schema_name": "DW",
        "columns": [
            _col("BASE_YM", "VARCHAR(6)",
                 "기준년월 (YYYYMM)", alt_name="기준년월"),
            _col("BRCH_CD", "VARCHAR(10)",
                 "지점코드", alt_name="지점코드"),
            _col("LOAN_TYPE_CD", "VARCHAR(2)",
                 "대출유형코드", alt_name="대출유형코드"),
            _col("TOTAL_LOAN_CNT", "INTEGER",
                 "총 대출건수", alt_name="총대출건수"),
            _col("TOTAL_LOAN_AMT", "NUMERIC(18,0)",
                 "총 대출금액(원)", alt_name="총대출금액"),
            _col("OVERDUE_CNT", "INTEGER",
                 "연체건수", alt_name="연체건수"),
            _col("OVERDUE_AMT", "NUMERIC(18,0)",
                 "연체금액(원)", alt_name="연체금액"),
            _col("OVERDUE_RATE", "NUMERIC(5,2)",
                 "연체율(%)", alt_name="연체율"),
        ],
    },
]

DUMMY_REPORT_SQLS: list[dict[str, Any]] = [
    {
        "report_name": "월간 신규 고객 현황",
        "description": "월별 신규 등록 고객 수 집계",
        "sql": (
            "SELECT DATE_TRUNC('month', REG_DT) "
            "AS base_month, CUST_TYPE_CD, "
            "COUNT(*) AS new_cust_cnt "
            "FROM TB_CUST_INFO "
            "WHERE REG_DT >= DATE_TRUNC("
            "'month', CURRENT_DATE) "
            "- INTERVAL '12 months' "
            "GROUP BY DATE_TRUNC('month', REG_DT)"
            ", CUST_TYPE_CD ORDER BY base_month"
        ),
    },
    {
        "report_name": "대출 실행 현황",
        "description": "기간별 대출 유형별 실행 건수 및 금액",
        "sql": (
            "SELECT LOAN_TYPE_CD, "
            "COUNT(*) AS loan_cnt, "
            "SUM(LOAN_AMT) AS total_amt, "
            "AVG(INT_RATE) AS avg_rate "
            "FROM TB_LOAN_INFO "
            "WHERE LOAN_DT >= DATE_TRUNC("
            "'month', CURRENT_DATE) "
            "GROUP BY LOAN_TYPE_CD"
        ),
    },
    {
        "report_name": "연체율 추이",
        "description": "월별 연체율 추이 (최근 12개월)",
        "sql": (
            "SELECT BASE_YM, "
            "SUM(OVERDUE_CNT) AS total_overdue, "
            "SUM(TOTAL_LOAN_CNT) AS total_loan, "
            "ROUND(SUM(OVERDUE_AMT)::NUMERIC "
            "/ NULLIF(SUM(TOTAL_LOAN_AMT), 0) "
            "* 100, 2) AS overdue_rate "
            "FROM TB_LOAN_OVERDUE_STAT "
            "GROUP BY BASE_YM ORDER BY BASE_YM"
        ),
    },
]

DUMMY_CODE_META: dict[str, dict[str, str]] = {
    "CUST_TYPE_CD": {
        "01": "개인", "02": "기업", "03": "개인사업자",
    },
    "CUST_GRADE_CD": {
        "VIP": "VIP등급", "Gold": "골드등급",
        "Silver": "실버등급", "General": "일반등급",
    },
    "LOAN_TYPE_CD": {
        "01": "신용대출", "02": "담보대출", "03": "보증대출",
    },
    "TXN_TYPE_CD": {
        "01": "입금", "02": "출금", "03": "이체",
    },
    "ACCT_STATUS_CD": {
        "01": "정상", "02": "해지", "03": "휴면",
    },
    "GENDER_CD": {"M": "남성", "F": "여성"},
    "OVERDUE_YN": {"Y": "연체", "N": "정상"},
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PostgreSQL Dummy 데이터
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_BRANCH_NAMES = [
    "본점영업부", "강남지점", "여의도지점", "서초지점",
    "종로지점", "영등포지점", "마포지점", "송파지점",
    "분당지점", "수원지점", "인천지점", "대전지점",
    "대구지점", "부산지점", "광주지점",
]

_CODE_SAMPLES: dict[str, list[str]] = {
    "CUST_TYPE_CD": ["01", "02", "03"],
    "LOAN_TYPE_CD": ["01", "02", "03"],
    "TXN_TYPE_CD": ["01", "02", "03"],
    "ACCT_STATUS_CD": ["01", "02", "03"],
    "CUST_GRADE_CD": ["VIP", "Gold", "Silver", "General"],
    "GENDER_CD": ["M", "F"],
    "AGE_GRP_CD": ["20", "30", "40", "50", "60"],
    "OVERDUE_YN": ["Y", "N"],
    "REGION_CD": ["01", "02", "03", "04", "05"],
}


def _parse_select_aliases(sql: str) -> list[str]:
    """SELECT 절에서 컬럼 alias 목록을 추출한다."""
    m = re.search(
        r"SELECT\s+(.*?)\s+FROM\s",
        sql, re.IGNORECASE | re.DOTALL,
    )
    if not m:
        return []

    select_clause = m.group(1)
    aliases = []
    parts = _split_select_columns(select_clause)

    for part in parts:
        part = part.strip()
        as_match = re.search(
            r"\bAS\s+[\"']?(\w+)[\"']?",
            part, re.IGNORECASE,
        )
        if as_match:
            aliases.append(as_match.group(1))
            continue
        tokens = re.findall(r"\w+", part)
        if tokens:
            aliases.append(tokens[-1])

    return aliases


def _split_select_columns(clause: str) -> list[str]:
    """괄호 깊이를 고려하여 SELECT 컬럼들을 분리한다."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in clause:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(ch)
    if current:
        parts.append("".join(current))
    return parts


def _has_group_by(sql: str) -> bool:
    """GROUP BY 절이 있는지 확인한다."""
    return bool(re.search(
        r"\bGROUP\s+BY\b", sql, re.IGNORECASE,
    ))


def _extract_limit(sql: str) -> int:
    """LIMIT 값을 추출한다. 없으면 기본 5."""
    m = re.search(r"\bLIMIT\s+(\d+)", sql, re.IGNORECASE)
    return int(m.group(1)) if m else 5


def _is_agg_alias(alias: str) -> bool:
    """집계 함수 결과 컬럼인지 추정한다."""
    agg_hints = [
        "cnt", "count", "sum", "avg", "total",
        "amt", "bal", "rate", "건수", "금액",
        "잔액", "합계", "비율", "평균",
    ]
    lower = alias.lower()
    return any(h in lower for h in agg_hints)


def _is_branch_alias(alias: str) -> bool:
    """지점 관련 컬럼인지 추정한다."""
    hints = ["brch", "지점"]
    lower = alias.lower()
    return any(h in lower for h in hints)


def _is_date_alias(alias: str) -> bool:
    """날짜/기간 관련 컬럼인지 추정한다."""
    hints = [
        "ym", "month", "dt", "date", "년월",
        "기준", "base",
    ]
    lower = alias.lower()
    return any(h in lower for h in hints)


def _gen_value_for_alias(alias: str) -> Any:
    """alias 이름을 보고 적절한 랜덤 값을 생성한다."""
    lower = alias.lower()

    for code_col, values in _CODE_SAMPLES.items():
        if code_col.lower() in lower:
            return random.choice(values)

    if _is_branch_alias(alias):
        return random.choice(_BRANCH_NAMES)

    if _is_date_alias(alias):
        today = today_kst()
        d = today.replace(day=1) - timedelta(
            days=30 * random.randint(0, 11),
        )
        if "ym" in lower or "년월" in lower:
            return d.strftime("%Y%m")
        return d.strftime("%Y-%m")

    if any(h in lower for h in [
        "amt", "bal", "금액", "잔액", "합계",
        "total", "sum", "신규",
    ]):
        return random.randint(10, 500) * 100000000

    if any(h in lower for h in ["rate", "비율", "율"]):
        return round(random.uniform(0.5, 8.0), 2)

    if any(h in lower for h in [
        "cnt", "count", "건수", "수",
    ]):
        return random.randint(50, 3000)

    if any(h in lower for h in ["nm", "name", "명"]):
        return random.choice(_BRANCH_NAMES)

    if "cust_no" in lower:
        return f"C{random.randint(1, 500):08d}"

    return random.randint(100, 9999)


def generate_dummy_data(
    sql: str,
) -> list[dict[str, Any]]:
    """SQL을 파싱하여 SELECT alias에 맞는 Dummy 데이터를 생성한다."""
    aliases = _parse_select_aliases(sql)
    if not aliases:
        return [{"result": random.randint(100, 9999)}]

    has_group = _has_group_by(sql)
    limit = _extract_limit(sql)

    if not has_group:
        row_count = 1
        if limit > 1 and not any(
            _is_agg_alias(a) for a in aliases
        ):
            row_count = min(limit, 10)
    else:
        row_count = min(limit, 15)

    branch_aliases = [
        a for a in aliases if _is_branch_alias(a)
    ]
    branch_pool = list(_BRANCH_NAMES)
    random.shuffle(branch_pool)

    rows: list[dict[str, Any]] = []
    for i in range(row_count):
        row: dict[str, Any] = {}
        for alias in aliases:
            if alias in branch_aliases:
                row[alias] = (
                    branch_pool[i]
                    if i < len(branch_pool)
                    else f"지점{i + 1}"
                )
            else:
                row[alias] = _gen_value_for_alias(alias)
        rows.append(row)

    agg_cols = [a for a in aliases if _is_agg_alias(a)]
    if agg_cols:
        rows.sort(
            key=lambda r: r.get(agg_cols[0], 0),
            reverse=True,
        )

    return rows


DUMMY_SQL_HISTORY = [
    {
        "query_text": "이번 달 신규 고객 수",
        "sql": (
            "SELECT COUNT(*) AS new_cust_cnt "
            "FROM TB_CUST_INFO "
            "WHERE REG_DT >= DATE_TRUNC("
            "'month', CURRENT_DATE)"
        ),
        "executed_at": "2024-03-10",
        "success": True,
    },
    {
        "query_text": "대출 유형별 실행 건수",
        "sql": (
            "SELECT LOAN_TYPE_CD, "
            "COUNT(*) AS loan_cnt, "
            "SUM(LOAN_AMT) AS total_amt "
            "FROM TB_LOAN_INFO "
            "WHERE LOAN_DT >= DATE_TRUNC("
            "'month', CURRENT_DATE) "
            "GROUP BY LOAN_TYPE_CD"
        ),
        "executed_at": "2024-03-08",
        "success": True,
    },
    {
        "query_text": "지점별 고객 수",
        "sql": (
            "SELECT b.BRCH_NM, "
            "COUNT(c.CUST_NO) AS cust_cnt "
            "FROM TB_CUST_INFO c "
            "JOIN TB_BRANCH_INFO b "
            "ON c.BRCH_CD = b.BRCH_CD "
            "GROUP BY b.BRCH_NM "
            "ORDER BY cust_cnt DESC LIMIT 10"
        ),
        "executed_at": "2024-03-05",
        "success": True,
    },
    {
        "query_text": "연체율 추이",
        "sql": (
            "SELECT BASE_YM, "
            "ROUND(SUM(OVERDUE_AMT)::NUMERIC "
            "/ NULLIF(SUM(TOTAL_LOAN_AMT), 0) "
            "* 100, 2) AS overdue_rate "
            "FROM TB_LOAN_OVERDUE_STAT "
            "WHERE BASE_YM >= TO_CHAR("
            "CURRENT_DATE - INTERVAL '12 months'"
            ", 'YYYYMM') "
            "GROUP BY BASE_YM ORDER BY BASE_YM"
        ),
        "executed_at": "2024-03-01",
        "success": True,
    },
    {
        "query_text": "예금 잔액 현황",
        "sql": (
            "SELECT PROD_CD, "
            "COUNT(*) AS acct_cnt, "
            "SUM(ACCT_BAL) AS total_bal "
            "FROM TB_DEPOSIT_INFO "
            "WHERE ACCT_STATUS_CD = '01' "
            "GROUP BY PROD_CD "
            "ORDER BY total_bal DESC"
        ),
        "executed_at": "2024-02-28",
        "success": True,
    },
]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Qdrant Dummy 데이터
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

DUMMY_MANUALS = [
    {
        "title": "여신 심사 절차",
        "content": (
            "여신 심사 절차는 다음과 같습니다:\n"
            "1. 대출 신청 접수\n"
            "2. 신용 평가 (CSS 점수 기반)\n"
            "3. 담보 평가 (담보대출의 경우)\n"
            "4. 심사역 심사\n"
            "5. 승인/반려 결정\n"
            "6. 대출 실행\n\n"
            "신용대출은 CSS 점수 600점 이상, "
            "담보대출은 LTV 70% 이하가 기본 조건입니다."
        ),
        "category": "여신",
    },
    {
        "title": "연체 관리 기준",
        "content": (
            "연체 분류 기준:\n"
            "- 1~29일: 단기연체\n"
            "- 30~89일: 장기연체\n"
            "- 90일 이상: 부실채권\n\n"
            "연체율 산출식: 연체금액 / 총 대출금액 × 100\n"
            "부실채권비율: "
            "90일 이상 연체금액 / 총 대출금액 × 100\n\n"
            "연체 발생 시 SMS 통보 → 전화 독촉 → "
            "내용증명 → 법적 조치 순서로 관리합니다."
        ),
        "category": "여신",
    },
    {
        "title": "수신 상품 안내",
        "content": (
            "주요 수신 상품:\n"
            "- 보통예금: 입출금 자유, 기본 이율 적용\n"
            "- 정기예금: 만기 지정, 고정금리\n"
            "- 정기적금: 매월 일정액 적립\n"
            "- MMF: 단기 금융상품 투자\n\n"
            "예금자보호법에 따라 "
            "1인당 5,000만원까지 보호됩니다."
        ),
        "category": "수신",
    },
    {
        "title": "BIS 비율 산출",
        "content": (
            "BIS 자기자본비율 산출식:\n"
            "BIS비율 = 자기자본 / 위험가중자산 × 100\n\n"
            "- 자기자본 = "
            "기본자본(Tier1) + 보완자본(Tier2)\n"
            "- 위험가중자산 = 신용위험가중자산 "
            "+ 시장위험가중자산 + 운영위험가중자산\n\n"
            "은행업감독규정상 최소 BIS비율: 8% 이상\n"
            "바젤III 기준 보통주자본비율: 4.5% 이상"
        ),
        "category": "경영지표",
    },
    {
        "title": "고객 등급 분류 체계",
        "content": (
            "고객 등급 분류:\n"
            "- VIP: 총 자산 10억 이상 "
            "또는 월 거래 1억 이상\n"
            "- Gold: 총 자산 3억 이상 "
            "또는 월 거래 3천만 이상\n"
            "- Silver: 총 자산 1억 이상 "
            "또는 월 거래 1천만 이상\n"
            "- General: 기타\n\n"
            "등급별 우대금리, 수수료 면제 등 "
            "차등 혜택 제공"
        ),
        "category": "고객관리",
    },
]

DUMMY_QDRANT_SQL_HISTORY = [
    {
        "sql": (
            "SELECT LN_DCD, COUNT(*) AS cnt, "
            "SUM(LN_BAL_AMT) AS total "
            "FROM biz_schema.TB_ADW_LNB301M "
            "WHERE STD_DT = CURRENT_DATE "
            "GROUP BY LN_DCD"
        ),
        "description": "대출유형별 대출건수 및 총잔액 집계",
        "tables": ["TB_ADW_LNB301M"],
        "domain": "LON",
    },
    {
        "sql": (
            "SELECT BLNG_BRCD, COUNT(*) AS cnt, "
            "SUM(BAL_AMT) AS total "
            "FROM biz_schema.TB_ADW_DEP201P "
            "WHERE STD_DT = CURRENT_DATE "
            "GROUP BY BLNG_BRCD "
            "ORDER BY total DESC"
        ),
        "description": "지점별 예금 계좌수 및 총잔액 현황",
        "tables": ["TB_ADW_DEP201P"],
        "domain": "DEP",
    },
    {
        "sql": (
            "SELECT CUS_GRD_CD, COUNT(*) AS cnt "
            "FROM biz_schema.TB_ADW_CSC101M "
            "WHERE STD_DT = CURRENT_DATE "
            "GROUP BY CUS_GRD_CD"
        ),
        "description": "고객등급별 고객 수 집계",
        "tables": ["TB_ADW_CSC101M"],
        "domain": "CUS",
    },
    {
        "sql": (
            "SELECT AGE_GRP_CD, GENDER_CD, "
            "COUNT(*) AS cnt "
            "FROM biz_schema.TB_ADW_CSC101M "
            "WHERE STD_DT = CURRENT_DATE "
            "AND CUS_DCD = '01' "
            "GROUP BY AGE_GRP_CD, GENDER_CD"
        ),
        "description": (
            "개인 고객의 연령대별 성별 인원수 분포"
        ),
        "tables": ["TB_ADW_CSC101M"],
        "domain": "CUS",
    },
    {
        "sql": (
            "SELECT OVDU_GRD_CD, COUNT(*) AS cnt, "
            "SUM(OVDU_AMT) AS total_ovdu "
            "FROM biz_schema.TB_ADW_LNB301M "
            "WHERE STD_DT = CURRENT_DATE "
            "AND OVDU_DAYS > 0 "
            "GROUP BY OVDU_GRD_CD"
        ),
        "description": "연체등급별 연체건수 및 연체금액 현황",
        "tables": ["TB_ADW_LNB301M"],
        "domain": "LON",
    },
]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Dummy 검색 헬퍼 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def search_dummy_table_meta(
    query: str,
) -> list[dict[str, Any]]:
    """키워드 매칭 기반 Dummy 테이블 메타 검색.

    name, alt_name, description 및 각 컬럼의 description,
    alt_name을 대상으로 키워드 매칭을 수행한다.
    """
    query_lower = query.lower()
    results = []
    for table in DUMMY_TABLE_META:
        table_text = " ".join([
            table.get("name", ""),
            table.get("alt_name", ""),
            table.get("description", ""),
        ]).lower()
        col_text = " ".join(
            " ".join([
                c.get("description", ""),
                c.get("alt_name", ""),
            ])
            for c in table["columns"]
        ).lower()
        searchable = table_text + " " + col_text
        if any(
            word in searchable
            for word in query_lower.split()
        ):
            results.append(table)
    return results if results else DUMMY_TABLE_META


def search_dummy_report_sql(
    query: str,
) -> list[dict[str, Any]]:
    """키워드 매칭 기반 Dummy 보고서 SQL 검색."""
    query_lower = query.lower()
    matched = [
        r
        for r in DUMMY_REPORT_SQLS
        if any(
            word in (
                f"{r['report_name']} "
                f"{r['description']}"
            ).lower()
            for word in query_lower.split()
        )
    ]
    return matched or DUMMY_REPORT_SQLS


def search_dummy_code_meta(
    query: str,
) -> list[dict[str, Any]]:
    """키워드 매칭 기반 Dummy 코드 메타 검색."""
    matched = [
        {"code_field": k, "codes": v}
        for k, v in DUMMY_CODE_META.items()
        if query.upper() in k
    ]
    if matched:
        return matched
    return [
        {"code_field": k, "codes": v}
        for k, v in DUMMY_CODE_META.items()
    ]


def search_dummy_sql_history(
    query: str,
) -> list[dict[str, Any]]:
    """키워드 매칭 기반 Dummy 과거 SQL 검색 (PostgreSQL)."""
    query_lower = query.lower()
    matched = [
        h
        for h in DUMMY_SQL_HISTORY
        if any(
            word in h["query_text"].lower()
            for word in query_lower.split()
        )
    ]
    return matched or DUMMY_SQL_HISTORY[:3]


def search_dummy_manuals(
    query: str, top_k: int,
) -> list[dict[str, Any]]:
    """Dummy 키워드 매칭 검색 (biz_manual)."""
    query_lower = query.lower()
    scored: list[tuple[int, dict[str, Any]]] = []
    for manual in DUMMY_MANUALS:
        searchable = (
            f"{manual['title']} "
            f"{manual['content']} "
            f"{manual['category']}"
        ).lower()
        score = sum(
            1
            for word in query_lower.split()
            if word in searchable
        )
        if score > 0:
            scored.append((score, manual))
    scored.sort(key=lambda x: x[0], reverse=True)
    if scored:
        return [m for _, m in scored[:top_k]]
    return DUMMY_MANUALS[:top_k]


def search_dummy_qdrant_sql_history(
    query: str, top_k: int,
) -> list[dict[str, Any]]:
    """Dummy 키워드 매칭 검색 (sql_history)."""
    query_lower = query.lower()
    scored: list[tuple[int, dict[str, Any]]] = []
    for item in DUMMY_QDRANT_SQL_HISTORY:
        searchable = (
            f"{item['description']} "
            f"{item['domain']} "
            f"{' '.join(item.get('tables', []))}"
        ).lower()
        score = sum(
            1
            for word in query_lower.split()
            if word in searchable
        )
        scored.append((score, {**item, "_score": score}))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [m for _, m in scored[:top_k]]
