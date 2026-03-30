"""설정 기반 문자열 절삭 유틸리티.

trace/log 출력 시 하드코딩된 [:N] 대신 이 함수를 사용한다.
기본값 0(무제한)이며, .env에서 제한을 설정할 수 있다.

사용법:
    from src.utils.truncate import truncate_trace, truncate_log, format_sql

    truncate_trace(val)   # trace JSON 기록용 (callback_handler)
    truncate_log(val)     # structlog 필드값 (노드/커넥터 로그)
    format_sql(sql)       # SQL pretty-print (sqlglot 기반)
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
    """
    import sqlglot
    try:
        results = sqlglot.transpile(sql, read=dialect, pretty=True)
        return results[0] if results else sql
    except Exception:
        return sql
