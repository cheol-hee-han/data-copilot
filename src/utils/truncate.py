"""설정 기반 문자열 절삭 유틸리티 — trace/log 출력용 문자열 길이 제어.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

LLM 응답, SQL 원문 등 긴 문자열을 trace JSON이나 structlog 필드에
기록할 때, 저장 용량과 가독성을 위해 일정 길이로 절삭한다.
하드코딩된 [:N] 대신 settings 설정값을 참조하여 환경별로 유연하게
절삭 길이를 제어한다. 기본값 0(무제한)이며, .env에서 변경 가능하다.

용도별 분리 이유: trace 기록은 상세(긴 limit), 콘솔 로그는 간결(짧은 limit)
해야 하므로 truncate_trace와 truncate_log를 별도로 제공한다.

핵심 함수:
    - truncate_trace: trace JSON 기록용 절삭 (callback_handler에서 사용)
    - truncate_log: structlog 필드값 절삭 (노드/커넥터 로그에서 사용)
    - format_sql: SQL pretty-print (sqlglot 기반, 파싱 실패 시 원본 반환)
"""

from __future__ import annotations


def _truncate(val: str, limit: int) -> str:
    """limit=0이면 전부 출력, 양수이면 절삭."""
    if limit <= 0 or len(val) <= limit:
        return val
    return val[:limit] + "..."


def truncate_trace(val: str) -> str:
    """trace JSON 기록용 절삭."""
    from src.config import settings
    return _truncate(val, settings.trace_truncate_limit)


def truncate_log(val: str) -> str:
    """structlog 필드값 절삭."""
    from src.config import settings
    return _truncate(val, settings.log_truncate_limit)


def format_sql(sql: str, dialect: str | None = None) -> str:
    """SQL을 들여쓰기 포맷으로 변환한다 (로그/트레이스용).

    sqlglot pretty=True를 사용하며, 파싱 실패 시 원본을 그대로 반환한다.
    dialect 미지정 시 외부망 폴백("postgres")을 사용한다 — 호출부에서
    target_db 결정 직후 dialect 를 명시 전달하는 것을 권장한다.
    """
    import sqlglot
    if dialect is None:
        dialect = "postgres"
    _dialect = dialect
    try:
        results = sqlglot.transpile(
            sql, read=_dialect, write=_dialect, pretty=True,
        )
        return results[0] if results else sql
    except Exception as e:
        from src.utils.logger import get_logger
        get_logger(__name__).debug("SQL 포맷팅 실패", error=str(e))
        return sql
