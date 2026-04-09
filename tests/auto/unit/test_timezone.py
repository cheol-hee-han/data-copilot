"""서울(KST) 타임존 유틸리티 단위 테스트.

테스트 대상:
    - now_kst: 현재 시각을 KST datetime으로 반환
    - today_kst: 오늘 날짜를 KST 기준으로 반환
    - now_stamp: 'yyyy-mm-dd HH:MM:SS.SSS' 포맷 반환
    - now_filesafe: 'yyyymmdd_HHMMSS' 파일명용 포맷 반환
    - to_stamp: 임의 datetime을 KST 포맷으로 변환

결정론적 테스트 방법:
    datetime.now 를 unittest.mock.patch 로 고정하여
    포맷 정확성을 환경 독립적으로 검증한다.

실행 스크립트:
    pytest tests/auto/unit/test_timezone.py -v

참고:
    - 외부 의존성 없음 (stdlib만 사용)
    - UTC 자정~09시 경계를 포함하는 케이스 검증
"""

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tests.conftest import get_test_logger, log_test_case
from src.utils.timezone import KST, now_filesafe, now_kst, now_stamp, to_stamp, today_kst

logger = get_test_logger("test_timezone")

# 고정 KST 시각: 2026-04-06 17:45:56.123000
_FIXED_KST = datetime(2026, 4, 6, 17, 45, 56, 123000, tzinfo=KST)
# UTC 자정 직전에서 KST는 오전 9시 (같은 날)
_UTC_MIDNIGHT = datetime(2026, 4, 6, 0, 0, 0, tzinfo=timezone.utc)
# UTC 00:30 → KST 09:30 (같은 날)
_UTC_00_30 = datetime(2026, 4, 6, 0, 30, 0, tzinfo=timezone.utc)
# UTC 22:00 (전날) → KST 07:00 (전날 + 9h = 같은 날 아침)
_UTC_PREV_22 = datetime(2026, 4, 5, 22, 0, 0, tzinfo=timezone.utc)


def _patch_now_kst(fixed_dt: datetime):
    """now_kst() 반환값을 고정하는 패치 컨텍스트를 반환한다."""
    return patch("src.utils.timezone.now_kst", return_value=fixed_dt)


# ════════════════════════════════════════════════════════════
# KST 상수
# ════════════════════════════════════════════════════════════

class TestKstConstant:
    """KST timezone 상수 검증."""

    def test_kst_offset_is_9_hours(self):
        """KST는 UTC+9이다."""
        offset = KST.utcoffset(None)
        passed = offset == timedelta(hours=9)
        log_test_case(logger, "kst_offset", "KST", timedelta(hours=9), offset, passed)
        assert passed

    def test_kst_name(self):
        """KST timezone 이름은 'KST'이다."""
        name = KST.tzname(None)
        passed = name == "KST"
        log_test_case(logger, "kst_name", "KST", "KST", name, passed)
        assert passed


# ════════════════════════════════════════════════════════════
# now_kst
# ════════════════════════════════════════════════════════════

class TestNowKst:
    """now_kst: KST 시각 반환."""

    def test_returns_datetime_with_kst_tzinfo(self):
        """now_kst()는 KST tzinfo를 가진 datetime을 반환한다."""
        result = now_kst()
        passed = result.tzinfo is not None and result.utcoffset() == timedelta(hours=9)
        log_test_case(logger, "now_kst_tzinfo", "now_kst()", "UTC+9", result.utcoffset(), passed)
        assert passed

    def test_now_kst_is_recent(self):
        """now_kst()는 현재 시각에 가까운 값을 반환한다 (5초 이내)."""
        import time as _time
        before = datetime.now(KST)
        _time.sleep(0.01)
        result = now_kst()
        after = datetime.now(KST)
        passed = before <= result <= after + timedelta(seconds=5)
        log_test_case(logger, "now_kst_recent", "최근 시각", "5초 이내", result, passed)
        assert passed


# ════════════════════════════════════════════════════════════
# today_kst
# ════════════════════════════════════════════════════════════

class TestTodayKst:
    """today_kst: KST 기준 오늘 날짜."""

    def test_returns_date_type(self):
        """today_kst()는 date 타입을 반환한다."""
        result = today_kst()
        passed = isinstance(result, date) and not isinstance(result, datetime)
        log_test_case(logger, "today_kst_type", "today_kst()", "date 타입", type(result).__name__, passed)
        assert passed

    def test_utc_midnight_kst_is_same_day(self):
        """UTC 자정은 KST 09:00이므로 동일 날짜이다."""
        with _patch_now_kst(_UTC_MIDNIGHT.astimezone(KST)):
            result = today_kst()
        expected = date(2026, 4, 6)
        passed = result == expected
        log_test_case(logger, "today_kst_utc_midnight", "UTC 00:00", expected, result, passed)
        assert passed

    def test_utc_prev_evening_kst_is_next_day(self):
        """UTC 전날 22:00은 KST 다음날 07:00이므로 다음 날짜이다."""
        with _patch_now_kst(_UTC_PREV_22.astimezone(KST)):
            result = today_kst()
        expected = date(2026, 4, 6)  # UTC 22:00 + 9h = 익일 07:00 KST
        passed = result == expected
        log_test_case(logger, "today_kst_utc_22", "UTC 전날 22:00", expected, result, passed)
        assert passed

    def test_fixed_kst_date(self):
        """고정된 KST 시각에 대해 올바른 날짜를 반환한다."""
        with _patch_now_kst(_FIXED_KST):
            result = today_kst()
        expected = date(2026, 4, 6)
        passed = result == expected
        log_test_case(logger, "today_kst_fixed", "2026-04-06", expected, result, passed)
        assert passed


# ════════════════════════════════════════════════════════════
# now_stamp
# ════════════════════════════════════════════════════════════

class TestNowStamp:
    """now_stamp: 'yyyy-mm-dd HH:MM:SS.mmm' 포맷."""

    def test_format_pattern(self):
        """now_stamp() 결과가 'yyyy-mm-dd HH:MM:SS.mmm' 형식이다."""
        import re
        result = now_stamp()
        pattern = r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}$"
        passed = bool(re.match(pattern, result))
        log_test_case(logger, "now_stamp_format", "now_stamp()", "yyyy-mm-dd HH:MM:SS.mmm", result, passed)
        assert passed, f"포맷 불일치: {result}"

    def test_fixed_kst_stamp(self):
        """고정된 KST 시각에서 정확한 stamp 문자열을 반환한다."""
        with _patch_now_kst(_FIXED_KST):
            result = now_stamp()
        expected = "2026-04-06 17:45:56.123"
        passed = result == expected
        log_test_case(logger, "now_stamp_fixed", "2026-04-06 17:45:56.123000", expected, result, passed)
        assert passed

    def test_milliseconds_zero_padded(self):
        """마이크로초가 5000이면 milliseconds는 '005'으로 패딩된다."""
        fixed = datetime(2026, 1, 1, 0, 0, 0, 5000, tzinfo=KST)  # 5000μs = 5ms
        with _patch_now_kst(fixed):
            result = now_stamp()
        passed = result.endswith(".005")
        log_test_case(logger, "now_stamp_ms_padding", "5000μs", ".005", result[-4:], passed)
        assert passed

    def test_milliseconds_999(self):
        """마이크로초 999000이면 '999'으로 표현된다."""
        fixed = datetime(2026, 6, 15, 12, 30, 0, 999000, tzinfo=KST)
        with _patch_now_kst(fixed):
            result = now_stamp()
        passed = result.endswith(".999")
        log_test_case(logger, "now_stamp_ms_999", "999000μs", ".999", result[-4:], passed)
        assert passed


# ════════════════════════════════════════════════════════════
# now_filesafe
# ════════════════════════════════════════════════════════════

class TestNowFilesafe:
    """now_filesafe: 'yyyymmdd_HHMMSS' 파일명용 포맷."""

    def test_format_pattern(self):
        """now_filesafe() 결과가 'yyyymmdd_HHMMSS' 형식이다."""
        import re
        result = now_filesafe()
        pattern = r"^\d{8}_\d{6}$"
        passed = bool(re.match(pattern, result))
        log_test_case(logger, "now_filesafe_format", "now_filesafe()", "yyyymmdd_HHMMSS", result, passed)
        assert passed, f"포맷 불일치: {result}"

    def test_fixed_kst_filesafe(self):
        """고정된 KST 시각에서 정확한 파일명 문자열을 반환한다."""
        with _patch_now_kst(_FIXED_KST):
            result = now_filesafe()
        expected = "20260406_174556"
        passed = result == expected
        log_test_case(logger, "now_filesafe_fixed", "2026-04-06 17:45:56", expected, result, passed)
        assert passed

    def test_no_special_chars(self):
        """파일명에 위험한 문자(공백, 콜론 등)가 없다."""
        result = now_filesafe()
        forbidden = set(" :/-.")
        passed = not any(c in forbidden for c in result)
        log_test_case(logger, "now_filesafe_no_special", "now_filesafe()", "특수문자 없음", result, passed)
        assert passed


# ════════════════════════════════════════════════════════════
# to_stamp
# ════════════════════════════════════════════════════════════

class TestToStamp:
    """to_stamp: 임의 datetime → KST stamp 변환."""

    def test_tz_aware_utc_to_kst(self):
        """UTC timezone-aware datetime이 KST로 변환된다."""
        utc_dt = datetime(2026, 4, 6, 8, 45, 56, 123000, tzinfo=timezone.utc)
        result = to_stamp(utc_dt)
        # UTC 08:45:56 + 9h = KST 17:45:56
        expected = "2026-04-06 17:45:56.123"
        passed = result == expected
        log_test_case(logger, "to_stamp_utc_to_kst", "UTC 08:45:56", expected, result, passed)
        assert passed

    def test_tz_naive_assumed_utc(self):
        """timezone-naive datetime은 UTC로 가정하고 KST로 변환된다."""
        naive_dt = datetime(2026, 4, 6, 8, 0, 0, 0)
        result = to_stamp(naive_dt)
        # naive → UTC → UTC+9 = 17:00:00
        expected = "2026-04-06 17:00:00.000"
        passed = result == expected
        log_test_case(logger, "to_stamp_naive_utc", "naive 08:00:00", expected, result, passed)
        assert passed

    def test_kst_aware_stays_kst(self):
        """KST timezone-aware datetime은 그대로 포맷된다."""
        kst_dt = datetime(2026, 4, 6, 17, 45, 56, 0, tzinfo=KST)
        result = to_stamp(kst_dt)
        expected = "2026-04-06 17:45:56.000"
        passed = result == expected
        log_test_case(logger, "to_stamp_kst_aware", "KST 17:45:56", expected, result, passed)
        assert passed

    def test_midnight_boundary(self):
        """UTC 00:00은 KST 09:00으로 변환된다."""
        utc_midnight = datetime(2026, 4, 6, 0, 0, 0, 0, tzinfo=timezone.utc)
        result = to_stamp(utc_midnight)
        expected = "2026-04-06 09:00:00.000"
        passed = result == expected
        log_test_case(logger, "to_stamp_midnight", "UTC 00:00:00", expected, result, passed)
        assert passed

    def test_output_format_pattern(self):
        """to_stamp() 결과는 항상 'yyyy-mm-dd HH:MM:SS.mmm' 형식이다."""
        import re
        dt = datetime(2026, 12, 31, 23, 59, 59, 999000, tzinfo=KST)
        result = to_stamp(dt)
        pattern = r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}$"
        passed = bool(re.match(pattern, result))
        log_test_case(logger, "to_stamp_format_pattern", str(dt), "yyyy-mm-dd HH:MM:SS.mmm", result, passed)
        assert passed
