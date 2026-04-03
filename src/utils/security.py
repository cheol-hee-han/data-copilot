"""보안 유틸리티 — 다층 방어를 위한 프롬프트 인젝션·SQL 인젝션·PII 마스킹 모듈.

사용자 입력부터 SQL 실행까지 전 구간에 걸쳐 보안 위협을 차단하는
다층 방어(Multi-layer Defense) 함수들을 제공한다.

핵심 방어 계층:
    - 프롬프트 인젝션 감지(detect_prompt_injection): 영어·한국어·간접 인젝션 등
      총 82개 정규식 패턴으로 시스템 프롬프트 우회 시도를 탐지한다.
      유니코드 정규화 후 재탐지하여 전각 문자 우회도 차단한다.
    - SQL 이중 방어(check_sql_safety_quick): sql_safety_checker의 정밀 검증과
      독립적으로 동작하는 경량 이중 방어 레이어로, FORBIDDEN_SQL_PATTERNS 공유
      상수를 기반으로 DML/DDL·시간 지연·파일 I/O·다중 쿼리·시스템 카탈로그
      접근·주석 인젝션을 차단한다.
    - PII 마스킹(mask_pii): 주민등록번호·카드번호·계좌번호·전화번호·이메일을
      구분자 형식을 보존하면서 마스킹하여 로그 및 응답에서 개인정보를 보호한다.

공유 상수:
    - FORBIDDEN_SQL_PATTERNS: SQL 금지 패턴 목록 (정규식, 오류 메시지) 튜플.
      sql_safety_checker(서비스 계층)와 check_sql_safety_quick(유틸 계층) 모두
      이 단일 목록을 참조하여 패턴 불일치를 방지한다.

전처리 함수:
    - normalize_unicode: NFKC 정규화로 전각 문자(ｓｅｌｅｃｔ 등) 우회를 방지하고
      제어 문자를 제거하는 공통 전처리 단계. 다른 보안 함수들이 내부적으로 활용한다.
"""

from __future__ import annotations

import re
import unicodedata


# 시스템 카탈로그 접근 감지 패턴
# pg_catalog 는 pg_\w+ 로 이미 커버되므로 별도 나열하지 않는다
_CATALOG_PATTERN = re.compile(
    r"\b(information_schema|pg_\w+|sys\.\w+|mysql\.\w+)\b", re.IGNORECASE
)

# PII 마스킹 패턴 - 적용 순서가 중요하다(구체적인 패턴을 먼저 적용)
# 주민등록번호: 생년월일 6자리 + 구분자(선택) + 성별코드(1~4) + 뒤 6자리
# 카드번호: 4-4-4-4 자리 (카드번호를 계좌번호보다 먼저 검사)
# 계좌번호: 은행별 형식(국민 9-2-4, 신한 3-3-6 등) — 하이픈 포함 시만 매칭하여 오탐 감소
# 전화번호: 지역번호(02,031,...) 또는 휴대폰(010,011,...)
# 이메일: RFC5321 기본 형식
PII_PATTERNS: dict[str, re.Pattern[str]] = {
    "주민등록번호": re.compile(
        r"(?<!\d)\d{6}-?[1-4]\d{6}(?!\d)"
    ),
    "카드번호": re.compile(
        r"(?<!\d)\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}(?!\d)"
    ),
    "계좌번호_하이픈": re.compile(
        r"(?<!\d)\d{3,6}-\d{2,6}-\d{2,6}(?:-\d{2})?(?!\d)"
    ),
    "전화번호": re.compile(
        r"(?<!\d)0(?:2|[3-6][1-5]|70|1[016789])-?\d{3,4}-?\d{4}(?!\d)"
    ),
    "이메일": re.compile(
        r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
    ),
}

# 프롬프트 인젝션 감지 패턴 — 영어 + 한국어 + 우회 변형 포함
_PROMPT_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    # 영어 고전 패턴
    re.compile(r"ignore\s+(previous|above|all)\s+(instructions?|prompts?|rules?|system)", re.IGNORECASE),
    re.compile(r"disregard\s+(previous|above|all|your)", re.IGNORECASE),
    re.compile(r"forget\s+(everything|all|your|previous)", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+(a|an|the)?", re.IGNORECASE),
    re.compile(r"pretend\s+(you\s+are|to\s+be|that)", re.IGNORECASE),
    re.compile(r"act\s+as\s+(if|a|an|the)", re.IGNORECASE),
    re.compile(r"override\s+(your|the|all)\s+(instructions?|rules?|constraints?)", re.IGNORECASE),
    re.compile(r"new\s+(instruction|directive|rule|prompt|system)", re.IGNORECASE),
    re.compile(r"system\s*:\s*", re.IGNORECASE),
    re.compile(r"<\s*system\s*>", re.IGNORECASE),
    re.compile(r"\[\s*system\s*\]", re.IGNORECASE),
    re.compile(r"jailbreak", re.IGNORECASE),
    re.compile(r"\bDAN\s+mode\b", re.IGNORECASE),
    re.compile(r"developer\s+mode", re.IGNORECASE),
    re.compile(r"do\s+anything\s+now", re.IGNORECASE),
    re.compile(r"prompt\s+(leak|injection|hack)", re.IGNORECASE),
    re.compile(r"reveal\s+(your|the)\s+(system\s+)?prompt", re.IGNORECASE),
    re.compile(r"print\s+(your|the)\s+(system\s+)?prompt", re.IGNORECASE),
    re.compile(r"show\s+(me\s+)?(your|the)\s+(system\s+)?prompt", re.IGNORECASE),
    re.compile(r"what\s+(are|were)\s+your\s+(original\s+)?instructions", re.IGNORECASE),
    # 한국어 패턴 — 직접 지시 무시 유도
    re.compile(r"이전\s*(지시|명령|규칙|프롬프트|설정|안내)를?\s*(무시|ignore|잊)", re.IGNORECASE),
    re.compile(r"(지시|명령|규칙|프롬프트)\s*[를을은는]?\s*(모두|전부|다|완전히)?\s*(무시|잊어)", re.IGNORECASE),
    re.compile(r"(무시|잊어버려|잊어|잊고).*?(지시|규칙|프롬프트|시스템)", re.IGNORECASE),
    re.compile(r"지금부터\s*(너는|당신은|당신)\s*(다른|새로운|새)", re.IGNORECASE),
    re.compile(r"(너는|당신은|당신이)\s*(이제|지금부터|앞으로)\s*(다른|새로운)", re.IGNORECASE),
    re.compile(r"시스템\s*(프롬프트|설정|명령)를?\s*(보여줘|출력|알려줘|공개)", re.IGNORECASE),
    re.compile(r"(원래|기존|처음)\s*(지시|명령|프롬프트|설정)를?\s*(알려줘|보여줘|공개)", re.IGNORECASE),
    re.compile(r"역할\s*[을를]?\s*(바꿔|변경|무시)", re.IGNORECASE),
    re.compile(r"(비밀번호|패스워드|모든\s*데이터|전체\s*데이터)를?\s*(출력|보여줘|알려줘)", re.IGNORECASE),
    re.compile(r"(관리자|admin|administrator)\s*(모드|권한|계정)", re.IGNORECASE),
    # 간접 인젝션 — 데이터에 삽입된 형태
    re.compile(r"\}\s*\{[^}]*system[^}]*\}", re.IGNORECASE),  # JSON 구조 탈출
    re.compile(r"```\s*(system|instruction|prompt)", re.IGNORECASE),  # 코드블록 위장
    re.compile(r"<\s*\/?(?:system|prompt|instruction|human|assistant)\s*>", re.IGNORECASE),
    re.compile(r"\[\s*INST\s*\]", re.IGNORECASE),  # Llama 형식 인젝션
    re.compile(r"<\s*\|[^|]+\|\s*>", re.IGNORECASE),  # 특수 태그 인젝션
]

# ── SQL 금지 패턴 (공유 상수) ──────────────────────────────────
# sql_safety_checker(서비스)와 check_sql_safety_quick(유틸) 모두 참조한다.
# 패턴 추가·수정 시 이 목록만 변경하면 양쪽에 동시 반영된다.
_MSG_TIME_DELAY = "시간 지연 함수는 허용되지 않습니다"

FORBIDDEN_SQL_PATTERNS: list[tuple[str, str]] = [
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


def _make_masked(match: str) -> str:
    """매치된 문자열에서 마스킹 값을 생성한다.

    구분자(하이픈, 공백)는 보존하고 숫자/문자만 마스킹하여
    형식(전화번호 3자리-4자리-4자리 등)이 유지되도록 한다.
    """
    result = []
    revealed_start = 2  # 앞 2자리 노출
    revealed_end = 2    # 뒤 2자리 노출
    digits_only = [c for c in match if c.isdigit() or c.isalpha()]
    total = len(digits_only)

    digit_idx = 0
    for ch in match:
        if not (ch.isdigit() or ch.isalpha()):
            result.append(ch)  # 구분자는 그대로 유지
        else:
            if digit_idx < revealed_start or digit_idx >= total - revealed_end:
                result.append(ch)
            else:
                result.append("*")
            digit_idx += 1
    return "".join(result)


def normalize_unicode(text: str) -> str:
    """유니코드 정규화로 동형 문자 우회를 방지한다.

    - NFKC 정규화: 전각 문자(ｓｅｌｅｃｔ 등)를 반각 ASCII로 변환
    - 제어 문자(U+0000~U+001F, U+007F~U+009F) 제거
    """
    normalized = unicodedata.normalize("NFKC", text)
    # 제어 문자 제거 (탭·개행 제외)
    normalized = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", "", normalized)
    return normalized


def mask_pii(text: str) -> str:
    """텍스트에서 개인정보를 마스킹한다.

    str.replace() 대신 re.sub()를 사용하여 패턴이 매치된 위치만 정확히 치환한다.
    str.replace()는 마스킹 대상과 동일한 문자열이 다른 위치에 있을 경우
    의도하지 않은 치환이 발생하는 버그가 있다.

    적용 순서: 주민등록번호 → 카드번호 → 계좌번호 → 전화번호 → 이메일
    (더 구체적인 패턴을 먼저 적용하여 부분 오탐 방지)
    """
    result = text
    for _pii_type, pattern in PII_PATTERNS.items():
        result = pattern.sub(lambda m: _make_masked(m.group(0)), result)
    return result


def detect_prompt_injection(text: str) -> bool:
    """프롬프트 인젝션 시도를 감지한다.

    탐지 범위:
    - 영어 고전 패턴 (ignore instructions, jailbreak 등)
    - 한국어 직접 지시 무시 유도 패턴
    - 간접 인젝션 (JSON/코드블록/XML 태그 위장)
    - 유니코드 동형 문자 정규화 후 재탐지
    """
    # 1차: 원문 탐지
    if any(pattern.search(text) for pattern in _PROMPT_INJECTION_PATTERNS):
        return True
    # 2차: 유니코드 정규화 후 탐지 (전각 문자 우회 방어)
    normalized = normalize_unicode(text)
    if normalized != text:
        if any(pattern.search(normalized) for pattern in _PROMPT_INJECTION_PATTERNS):
            return True
    return False


def check_sql_safety_quick(sql: str) -> tuple[bool, list[str]]:
    """SQL의 안전성을 경량 검증한다 (이중 방어 레이어).

    sql_safety_checker.validate_sql_safety()의 정밀 검증과 독립적으로 동작하는
    경량 이중 방어 레이어. SQL 실행 직전(sql_executor)에서 호출된다.
    FORBIDDEN_SQL_PATTERNS 공유 상수를 사용하여 패턴 불일치를 방지한다.
    """
    errors: list[str] = []
    sql = normalize_unicode(sql)
    sql_upper = sql.upper().strip()

    # SELECT 또는 WITH(CTE)만 허용
    if not (sql_upper.startswith("SELECT") or sql_upper.startswith("WITH")):
        errors.append("SELECT 문만 허용됩니다")

    # 공유 금지 패턴 검사
    for pattern, msg in FORBIDDEN_SQL_PATTERNS:
        if re.search(pattern, sql, re.IGNORECASE):
            errors.append(msg)

    return len(errors) == 0, errors
