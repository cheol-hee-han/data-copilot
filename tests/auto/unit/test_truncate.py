"""설정 기반 문자열 절삭 유틸리티 단위 테스트.

테스트 대상:
    - truncate_trace: trace JSON 기록용 절삭 (settings.trace_truncate_limit)
    - truncate_log: structlog 필드값 절삭 (settings.log_truncate_limit)
    - format_sql: SQL pretty-print (sqlglot 기반, 파싱 실패 시 원본)

경계 조건:
    - limit=0 → 무제한 (전체 반환)
    - limit=양수 → 초과 시 절삭 + "..." 접미사
    - len == limit → 절삭 없음 (경계값)
    - len == limit+1 → 절삭 발생

실행 스크립트:
    pytest tests/auto/unit/test_truncate.py -v

참고:
    - settings.trace_truncate_limit / log_truncate_limit 값에 의존
    - format_sql은 sqlglot.transpile에 위임, 파싱 실패 시 원본 반환
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tests.conftest import get_test_logger, log_test_case
from src.utils.truncate import _truncate, format_sql

logger = get_test_logger("test_truncate")


# ════════════════════════════════════════════════════════════
# _truncate 내부 함수 (공개 인터페이스의 핵심 로직)
# ════════════════════════════════════════════════════════════

class TestTruncateInternal:
    """_truncate(val, limit): 핵심 절삭 로직."""

    def test_zero_limit_returns_full(self):
        """limit=0이면 전체 문자열을 그대로 반환한다."""
        val = "A" * 1000
        result = _truncate(val, 0)
        passed = result == val
        log_test_case(logger, "_truncate_zero_limit", f"len={len(val)}", "전체 반환", len(result), passed)
        assert passed

    def test_negative_limit_returns_full(self):
        """limit 음수이면 전체 문자열을 그대로 반환한다."""
        val = "hello world"
        result = _truncate(val, -1)
        passed = result == val
        log_test_case(logger, "_truncate_negative_limit", val, "전체 반환", result, passed)
        assert passed

    def test_exact_boundary_no_truncation(self):
        """len(val) == limit이면 절삭하지 않는다."""
        val = "X" * 100
        result = _truncate(val, 100)
        passed = result == val and not result.endswith("...")
        log_test_case(logger, "_truncate_exact_boundary", f"len={len(val)}", "절삭 없음", len(result), passed)
        assert passed

    def test_one_over_boundary_truncates(self):
        """len(val) == limit+1이면 절삭되고 '...' 접미사가 붙는다."""
        val = "X" * 101
        result = _truncate(val, 100)
        passed = result == "X" * 100 + "..."
        log_test_case(logger, "_truncate_one_over", f"len={len(val)}", "절삭+'...'", result[:20], passed)
        assert passed

    def test_long_string_truncates_with_ellipsis(self):
        """limit보다 긴 문자열은 limit 길이로 자르고 '...'를 붙인다."""
        val = "가" * 500
        result = _truncate(val, 200)
        passed = len(result) == 203 and result.endswith("...")
        log_test_case(logger, "_truncate_long", f"len=500,limit=200", "len=203", len(result), passed)
        assert passed

    def test_empty_string(self):
        """빈 문자열은 그대로 반환한다."""
        result = _truncate("", 100)
        passed = result == ""
        log_test_case(logger, "_truncate_empty", "", "", result, passed)
        assert passed

    def test_short_string_within_limit(self):
        """limit보다 짧은 문자열은 변경 없이 반환한다."""
        val = "short"
        result = _truncate(val, 1000)
        passed = result == val
        log_test_case(logger, "_truncate_short", val, val, result, passed)
        assert passed


# ════════════════════════════════════════════════════════════
# truncate_trace (settings 기반)
# ════════════════════════════════════════════════════════════

class TestTruncateTrace:
    """truncate_trace: settings.trace_truncate_limit 기반 절삭."""

    def test_respects_nonzero_limit(self):
        """trace_truncate_limit > 0이면 초과 문자열을 절삭한다."""
        from src.utils.truncate import truncate_trace
        from src.config import settings

        if settings.trace_truncate_limit <= 0:
            # 무제한 설정이면 전체 반환 확인
            result = truncate_trace("A" * 5000)
            passed = len(result) == 5000
        else:
            val = "B" * (settings.trace_truncate_limit + 100)
            result = truncate_trace(val)
            passed = len(result) <= settings.trace_truncate_limit + 3  # "..." 포함
        log_test_case(logger, "truncate_trace_limit", "긴 문자열", "limit 적용", len(result), passed)
        assert passed

    def test_short_string_unchanged(self):
        """짧은 문자열은 절삭 없이 반환된다."""
        from src.utils.truncate import truncate_trace
        val = "짧은 텍스트"
        result = truncate_trace(val)
        passed = result == val
        log_test_case(logger, "truncate_trace_short", val, val, result, passed)
        assert passed

    def test_zero_limit_returns_all(self):
        """trace_truncate_limit=0이면 전체 문자열을 반환한다."""
        from src.utils.truncate import truncate_trace
        from src.config import settings

        with patch.object(settings, "trace_truncate_limit", 0):
            val = "A" * 9999
            result = truncate_trace(val)
            passed = result == val
        log_test_case(logger, "truncate_trace_zero_limit", "len=9999", "전체 반환", len(result), passed)
        assert passed


# ════════════════════════════════════════════════════════════
# truncate_log (settings 기반)
# ════════════════════════════════════════════════════════════

class TestTruncateLog:
    """truncate_log: settings.log_truncate_limit 기반 절삭."""

    def test_respects_nonzero_limit(self):
        """log_truncate_limit > 0이면 초과 문자열을 절삭한다."""
        from src.utils.truncate import truncate_log
        from src.config import settings

        if settings.log_truncate_limit <= 0:
            result = truncate_log("A" * 3000)
            passed = len(result) == 3000
        else:
            val = "C" * (settings.log_truncate_limit + 50)
            result = truncate_log(val)
            passed = len(result) <= settings.log_truncate_limit + 3
        log_test_case(logger, "truncate_log_limit", "긴 문자열", "limit 적용", len(result), passed)
        assert passed

    def test_short_string_unchanged(self):
        """짧은 문자열은 변경 없이 반환된다."""
        from src.utils.truncate import truncate_log
        val = "로그 메시지"
        result = truncate_log(val)
        passed = result == val
        log_test_case(logger, "truncate_log_short", val, val, result, passed)
        assert passed

    def test_zero_limit_returns_all(self):
        """log_truncate_limit=0이면 전체 문자열을 반환한다."""
        from src.utils.truncate import truncate_log
        from src.config import settings

        with patch.object(settings, "log_truncate_limit", 0):
            val = "Z" * 8888
            result = truncate_log(val)
            passed = result == val
        log_test_case(logger, "truncate_log_zero_limit", "len=8888", "전체 반환", len(result), passed)
        assert passed


# ════════════════════════════════════════════════════════════
# format_sql
# ════════════════════════════════════════════════════════════

class TestFormatSql:
    """format_sql: SQL pretty-print."""

    def test_simple_select_formatted(self):
        """단순 SELECT는 pretty-print 결과를 반환한다."""
        sql = "SELECT a.id,a.name FROM users a WHERE a.id=1"
        result = format_sql(sql)
        passed = isinstance(result, str) and len(result) > 0
        log_test_case(logger, "format_sql_simple", sql, "포맷된 SQL", result, passed)
        assert passed

    def test_multiline_output(self):
        """pretty=True로 인해 결과에 줄바꿈이 포함된다."""
        sql = "SELECT a.id,a.name FROM tb_users a JOIN tb_orders b ON a.id=b.user_id WHERE a.status='A'"
        result = format_sql(sql)
        passed = "\n" in result
        log_test_case(logger, "format_sql_multiline", sql[:40], "줄바꿈 포함", result[:60], passed)
        assert passed

    def test_invalid_sql_returns_original(self):
        """파싱 불가 SQL은 원본을 그대로 반환한다."""
        sql = "NOT A VALID SQL @@@"
        result = format_sql(sql)
        passed = result == sql
        log_test_case(logger, "format_sql_invalid", sql, sql, result, passed)
        assert passed

    def test_empty_string_returns_original(self):
        """빈 문자열은 빈 문자열을 반환한다."""
        result = format_sql("")
        passed = result == ""
        log_test_case(logger, "format_sql_empty", "", "", result, passed)
        assert passed

    def test_with_dialect(self):
        """dialect 파라미터를 전달해도 정상 동작한다."""
        sql = "SELECT id FROM TB_CRM_CUSTOMER WHERE CUST_STAT_CD='1'"
        result = format_sql(sql, dialect="postgres")
        passed = isinstance(result, str) and len(result) > 0
        log_test_case(logger, "format_sql_dialect", sql[:40], "포맷된 SQL", result, passed)
        assert passed
