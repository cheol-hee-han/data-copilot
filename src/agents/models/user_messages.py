"""사용자에게 노출되는 메시지 상수 — 에러/경고 문구의 단일 관리 지점.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

파이프라인 전 노드에서 사용자에게 전달되는 에러 메시지, 재시도 안내를
한 곳에서 정의하여 일관된 톤을 유지한다.
내부 기술 용어(SQL, 스택 트레이스 등)를 노출하지 않으며,
IT 비전문 은행 직원이 이해할 수 있는 친절한 한국어 표현만 사용한다.

핵심 함수:
    - format_error: 에러 메시지에 RETRY_GUIDE 를 부착하여 반환

상수:
    - ERR_SQL_GENERATION, ERR_SQL_EXECUTION 등: 단계별 에러 메시지
"""

from __future__ import annotations


# ── 에러 메시지 (QueryStatus.ERROR 와 함께 사용) ──

ERR_SQL_GENERATION = "SQL 생성 중 오류가 발생했습니다."
ERR_SQL_EXECUTION = "데이터 조회 중 오류가 발생했습니다."
ERR_SQL_SECURITY = "보안 검증에 실패했습니다."
ERR_DATA_ANALYSIS = "데이터 분석 중 오류가 발생했습니다."
ERR_FORMATTING = "결과 포맷팅 중 오류가 발생했습니다."
ERR_GENERIC = "처리 중 오류가 발생했습니다."

# 사용자에게 재시도를 안내하는 공통 suffix
RETRY_GUIDE = "잠시 후 다시 시도해주세요."
REPHRASE_GUIDE = "다시 시도하시거나, 요청을 좀 더 구체적으로 입력해주세요."

# SQL 재시도 소진
ERR_SQL_RETRY_EXHAUSTED = (
    "죄송합니다. 여러 번 시도했지만 안전한 SQL을 생성하지 못했습니다.\n"
    "요청을 좀 더 구체적으로 다시 입력해주시겠어요?"
)


def format_error(msg: str, *, with_retry: bool = True) -> str:
    """에러 메시지에 재시도 안내를 붙여 반환한다."""
    if with_retry:
        return f"{msg} {RETRY_GUIDE}"
    return msg
