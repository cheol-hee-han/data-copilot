"""SQL 안전성 검증 서비스 — LLM이 생성한 SQL의 다층 방어 검증 파이프라인.

LLM이 생성한 SQL이 실행되기 전에 5단계 검증 파이프라인을 통과시켜
보안 위협과 데이터 유출을 사전 차단하는 심층 방어(Defense-in-Depth) 전략을 구현한다.

검증 파이프라인 (validate_sql_safety):
    1단계 - 유니코드 정규화 및 SELECT/WITH 시작 여부 확인
    2단계 - 금지 패턴 검사 (DML/DDL, 시스템 카탈로그, 다중 쿼리, 시간 지연 등 17개 패턴)
    3단계 - sqlglot 기반 SQL 구문 파싱 검증
    4단계 - PII 컬럼 직접 노출 검사 (주민번호, 카드번호, 계좌번호, 비밀번호 등)
    5단계 - LIMIT 절 존재 여부 확인 (집계 쿼리는 예외 처리)

PII 컬럼 목록은 resources/domain/pii_columns.yaml에서 로드하며,
YAML 파일이 없으면 내장 기본값(_DEFAULT_PII_COLUMNS, _DEFAULT_MASKING_COLUMNS)을 사용한다.
검증 실패 시 SafetyCheckResult에 오류 목록과 LLM 재생성용 피드백 문자열을 담아 반환한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.utils.logger import get_logger
from src.utils.sqlglot_analyzer import parse_sql_safe
from src.utils.resource_loader import load_yaml
from src.utils.security import normalize_unicode

logger = get_logger(__name__)

_MSG_TIME_DELAY = "시간 지연 함수는 허용되지 않습니다"

FORBIDDEN_PATTERNS = [
    (
        r"\b(INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|CREATE|GRANT|REVOKE)\b",
        "DML/DDL 문은 허용되지 않습니다",
    ),
    (
        r"\bEXEC(?:UTE)?\b",
        "프로시저 실행은 허용되지 않습니다",
    ),
    (
        r"\bCALL\b",
        "프로시저 호출은 허용되지 않습니다",
    ),
    (
        r"\b(information_schema|pg_\w+|sys\.\w+|mysql\.\w+)\b",
        "시스템 카탈로그 접근은 금지됩니다",
    ),
    (
        r";\s*\w+",
        "다중 쿼리는 허용되지 않습니다",
    ),
    (
        r"\bINTO\s+OUTFILE\b",
        "파일 출력은 허용되지 않습니다",
    ),
    (
        r"\bINTO\s+DUMPFILE\b",
        "파일 덤프는 허용되지 않습니다",
    ),
    (
        r"\bLOAD_FILE\s*\(",
        "파일 읽기는 허용되지 않습니다",
    ),
    (
        r"\bLOAD\s+DATA\b",
        "데이터 로드는 허용되지 않습니다",
    ),
    (r"\bSLEEP\s*\(", _MSG_TIME_DELAY),
    (r"\bWAITFOR\s+DELAY\b", _MSG_TIME_DELAY),
    (r"\bBENCHMARK\s*\(", _MSG_TIME_DELAY),
    (r"\bPG_SLEEP\s*\(", _MSG_TIME_DELAY),
    (r"--", "SQL 주석은 허용되지 않습니다"),
    (r"/\*", "SQL 블록 주석은 허용되지 않습니다"),
    (r"\bxp_\w+", "확장 저장 프로시저는 허용되지 않습니다"),
    (
        r"\bUNION\s+(?:ALL\s+)?SELECT\b",
        "UNION SELECT는 허용되지 않습니다",
    ),
]

_DEFAULT_PII_COLUMNS = {
    "JUMIN_NO", "JUMIN_NUM", "SSN", "RESIDENT_NO",
    "RESIDENT_NUM", "RRNO", "RESI_NO", "CUST_RRNO",
    "CARD_NO", "CARD_NUM", "CARD_NUMBER",
    "ACCT_NO", "ACCT_NUM", "ACCOUNT_NO",
    "ACCOUNT_NUMBER", "BANK_ACCT_NO",
    "ACCT_PWD", "PASSWORD", "PASSWD", "PWD", "PIN_NO",
    "CVC", "CVV", "CVC_NO", "CVV_NO",
    "FRNO", "FRNR_NO",
}

_DEFAULT_MASKING_COLUMNS = {
    "PHONE_NO", "TEL_NO", "HP_NO", "MOBILE_NO",
    "CUST_TEL_NO", "CUST_HP_NO",
    "EMAIL", "EMAIL_ADDR", "CUST_EMAIL",
    "BIRTH_DT", "BIRTHDAY", "BIRTH_YMD",
    "CUST_BIRTH_DT",
    "ADDR", "ADDRESS", "HOME_ADDR", "WORK_ADDR",
    "ADDR_DTL",
    "CUST_NM", "CUST_NAME",
}

_AGG_PATTERN = re.compile(
    r"\b(COUNT|SUM|AVG|MIN|MAX)\s*\(", re.IGNORECASE,
)


def _load_pii_columns() -> tuple[set[str], set[str]]:
    """resources/domain/pii_columns.yaml 에서 PII 정의를 로드한다."""
    data = load_yaml("domain/pii_columns.yaml", None)
    if data is None:
        return _DEFAULT_PII_COLUMNS, _DEFAULT_MASKING_COLUMNS

    forbidden = set(data.get("forbidden", []))
    masking_entries = data.get("masking", [])
    masking_cols: set[str] = set()
    for entry in masking_entries:
        pattern = entry.get("pattern", "")
        masking_cols.update(pattern.split("|"))

    conditional = data.get("conditional", [])
    for entry in conditional:
        pattern = entry.get("pattern", "")
        masking_cols.update(pattern.split("|"))

    return (
        forbidden if forbidden else _DEFAULT_PII_COLUMNS,
        masking_cols if masking_cols else _DEFAULT_MASKING_COLUMNS,
    )


PII_COLUMNS, MASKING_COLUMNS = _load_pii_columns()


@dataclass
class SafetyCheckResult:
    """SQL 안전성 검증 결과."""

    is_safe: bool = True
    errors: list[str] = field(default_factory=list)
    feedback: str = ""


def is_aggregate_query(sql_upper: str) -> bool:
    """집계 쿼리 여부를 판별한다."""
    if not _AGG_PATTERN.search(sql_upper):
        return False
    if "GROUP BY" in sql_upper:
        return True
    select_match = re.search(
        r"\bSELECT\b(.+?)\bFROM\b",
        sql_upper,
        re.DOTALL,
    )
    if not select_match:
        return False
    select_clause = select_match.group(1)
    stripped = re.sub(
        r"\bAS\s+\w+", "", select_clause,
        flags=re.IGNORECASE,
    )
    stripped = re.sub(
        r"\b(COUNT|SUM|AVG|MIN|MAX)\s*\([^)]*\)",
        "", stripped, flags=re.IGNORECASE,
    )
    stripped = re.sub(r"[(),\s\d*']+", "", stripped)
    return stripped.strip() == ""


def check_forbidden_patterns(sql: str) -> list[str]:
    """금지 패턴을 검사한다."""
    return [
        msg
        for pattern, msg in FORBIDDEN_PATTERNS
        if re.search(pattern, sql, re.IGNORECASE)
    ]


def check_sql_syntax(
    sql: str, dialect: str = "postgres",
) -> list[str]:
    """SQL 구문을 파싱 검증한다."""
    ast = parse_sql_safe(sql, dialect)
    if ast is None:
        return ["SQL 구문을 파싱할 수 없습니다"]
    return []


def check_pii_columns(sql_upper: str) -> list[str]:
    """PII 컬럼 직접 노출을 검사한다."""
    return [
        f"개인정보 컬럼 '{col}'은 조회할 수 없습니다"
        for col in PII_COLUMNS
        if re.search(rf"\b{col}\b", sql_upper)
    ]


def build_validation_feedback(
    errors: list[str], sql: str,
) -> str:
    """검증 오류를 LLM 재생성 피드백 문자열로 변환한다."""
    error_list = "\n".join(
        f"{i + 1}. {err}" for i, err in enumerate(errors)
    )
    return (
        f"실패한 SQL:\n{sql}\n\n"
        f"발견된 문제:\n{error_list}"
    )


def validate_sql_safety(
    raw_sql: str,
    dialect: str = "postgres",
) -> SafetyCheckResult:
    """생성된 SQL의 안전성을 종합 검증한다.

    1. 유니코드 정규화
    2. 비어있는지 확인
    3. SELECT/WITH 시작 여부
    4. 금지 패턴
    5. SQL 구문 파싱
    6. PII 컬럼
    7. LIMIT 존재 여부 (집계 쿼리 예외)
    """
    sql = normalize_unicode(raw_sql.strip())

    if not sql:
        return SafetyCheckResult(
            is_safe=False,
            errors=["SQL이 비어 있습니다"],
            feedback=(
                "SQL 이 비어 있습니다. "
                "반드시 유효한 SELECT 문을 출력하세요."
            ),
        )

    sql_upper = sql.upper()
    if not (
        sql_upper.startswith("SELECT")
        or sql_upper.startswith("WITH")
    ):
        errors = ["SELECT 문만 허용됩니다"]
        return SafetyCheckResult(
            is_safe=False,
            errors=errors,
            feedback=build_validation_feedback(errors, sql),
        )

    errors: list[str] = []
    errors.extend(check_forbidden_patterns(sql))
    errors.extend(check_sql_syntax(sql, dialect))
    errors.extend(check_pii_columns(sql_upper))

    has_row_limit = (
        "LIMIT" in sql_upper
        or (dialect == "tsql" and "TOP " in sql_upper)
    )
    if not has_row_limit and not is_aggregate_query(sql_upper):
        errors.append(
            "LIMIT 절이 없습니다. "
            "대량 데이터 조회를 방지하기 위해 "
            "LIMIT을 포함해주세요"
        )

    if errors:
        return SafetyCheckResult(
            is_safe=False,
            errors=errors,
            feedback=build_validation_feedback(errors, sql),
        )

    return SafetyCheckResult(is_safe=True)
