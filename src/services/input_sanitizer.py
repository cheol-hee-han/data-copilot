"""사용자 자연어 입력의 안전성 검증 및 정제 서비스.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

은행 직원의 자연어 입력이 LLM 파이프라인에 진입하기 전에 반드시 거쳐야 하는
방어 계층이다. 세 단계 파이프라인(normalize → length check → injection check)을
순차 적용하며, 어느 단계에서든 실패하면 즉시 에러를 반환하여 후속 처리를 차단한다.
유니코드 NFKC 정규화를 선행하여 전각 문자 우회 공격을 사전 차단하고,
SQL 인젝션(DDL/DML/시스템 카탈로그 등 12개 패턴)과 프롬프트 인젝션을 이중으로 감지한다.

핵심 함수:
    - sanitize: 정규화 → 길이 검사 → 인젝션 검사를 순차 실행하는 메인 파이프라인
    - normalize_input: 유니코드 NFKC 정규화 + 연속 공백 단일화
    - check_length: 설정 기반 최대 입력 길이 검증
    - check_injection: SQL/프롬프트 인젝션 의심 패턴 감지 (컴파일된 정규식 사용)
    - mask_for_logging: 로깅 시 PII 마스킹 처리

설계 결정: 인젝션 패턴은 모듈 로드 시 사전 컴파일하여 반복 호출 성능을 확보한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.config import settings
from src.utils.logger import get_logger
from src.utils.security import (
    detect_prompt_injection,
    mask_pii,
    normalize_unicode,
)

logger = get_logger(__name__)

MAX_INPUT_LENGTH = settings.max_input_length

# SQL 인젝션 의심 패턴 — 우회 공격 포함
# 유니코드 정규화(normalize_unicode) 후 검사하므로 전각 문자 우회는 사전 차단됨
_SUSPICIOUS_PATTERNS = [
    (r";\s*(DROP|DELETE|INSERT|UPDATE|ALTER|TRUNCATE|CREATE|GRANT|REVOKE)\b", "다중 쿼리/DDL 패턴"),
    (r";\s*SELECT\b", "세미콜론 후 SELECT"),
    (r"--", "SQL 단행 주석 패턴"),
    (r"/\*", "SQL 블록 주석 패턴"),
    (r"\bUNION\s+(?:ALL\s+)?SELECT\b", "UNION SELECT 인젝션"),
    (r"\(\s*(DELETE|INSERT|UPDATE|DROP|TRUNCATE)\b", "서브쿼리 내 DML"),
    (r"\b(SLEEP|WAITFOR|BENCHMARK|PG_SLEEP)\s*\(", "시간 지연 함수"),
    (r"\b(LOAD_FILE|INTO\s+OUTFILE|INTO\s+DUMPFILE|LOAD\s+DATA)\b", "파일 I/O"),
    (r"\bxp_\w+", "확장 저장 프로시저"),
    (r"\bEXEC(?:UTE)?\s*\(", "프로시저 실행"),
    (r"\b(information_schema|pg_catalog|pg_\w+|sys\.\w+)\b", "시스템 카탈로그 접근"),
]

_COMPILED_SUSPICIOUS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(pattern, re.IGNORECASE | re.DOTALL), label)
    for pattern, label in _SUSPICIOUS_PATTERNS
]


@dataclass
class SanitizeResult:
    """입력 정제 결과."""

    text: str = ""
    is_error: bool = False
    error_message: str = ""


def normalize_input(text: str) -> str:
    """텍스트를 정규화한다.

    1. 유니코드 NFKC 정규화: 전각 문자 → 반각 ASCII 변환
    2. 연속 공백 단일화
    """
    normalized = normalize_unicode(text.strip())
    return re.sub(r"\s+", " ", normalized)


def check_length(text: str) -> str | None:
    """입력 길이를 검사한다. 초과 시 에러 메시지를 반환한다."""
    if len(text) > MAX_INPUT_LENGTH:
        logger.warning("입력 길이 초과", length=len(text))
        return (
            f"입력이 너무 깁니다 ({len(text)}자). "
            f"{MAX_INPUT_LENGTH}자 이내로 줄여서 다시 입력해주세요."
        )
    return None


def check_injection(text: str) -> str | None:
    """SQL 인젝션 및 프롬프트 인젝션 의심 패턴을 검사한다.

    발견 시 에러 메시지를 반환한다.
    """
    if detect_prompt_injection(text):
        logger.warning("프롬프트 인젝션 시도 감지")
        return "입력에 허용되지 않는 패턴이 포함되어 있습니다."

    for compiled_pattern, label in _COMPILED_SUSPICIOUS:
        if compiled_pattern.search(text):
            logger.warning(
                "SQL 인젝션 의심 패턴 감지",
                pattern_label=label,
            )
            return "입력에 허용되지 않는 패턴이 포함되어 있습니다."
    return None


def sanitize(text: str) -> SanitizeResult:
    """입력을 정규화하고 안전성을 검사한다.

    Returns:
        SanitizeResult: 정제된 텍스트 또는 에러 정보.
    """
    normalized = normalize_input(text)

    length_err = check_length(normalized)
    if length_err:
        return SanitizeResult(is_error=True, error_message=length_err)

    injection_err = check_injection(normalized)
    if injection_err:
        return SanitizeResult(
            is_error=True, error_message=injection_err,
        )

    return SanitizeResult(text=normalized)


def mask_for_logging(text: str, max_len: int = 100) -> str:
    """로깅용 PII 마스킹."""
    return mask_pii(text[:max_len])
