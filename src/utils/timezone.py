"""서울(KST) 타임존 유틸리티.

프로젝트 전체에서 사용하는 타임존·포맷 상수를 한 곳에서 관리한다.
외부 의존성 없이 stdlib만 사용 — 폐쇄망 배포 호환.

사용 예::

    from src.utils.timezone import now_kst, now_stamp, now_filesafe

    ts = now_stamp()       # "2026-03-27 17:45:56.123"
    fs = now_filesafe()    # "20260327_174556"
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

# Asia/Seoul (UTC+9) — stdlib만으로 정의
KST = timezone(timedelta(hours=9), name="KST")

# 간결 타임스탬프: "yyyy-mm-dd HH:MM:SS.SSS"
_STAMP_FMT = "%Y-%m-%d %H:%M:%S"

# 파일명용: "yyyymmdd_HHMMSS"
_FILE_FMT = "%Y%m%d_%H%M%S"


def now_kst() -> datetime:
    """현재 시각을 KST datetime으로 반환한다."""
    return datetime.now(KST)


def today_kst() -> date:
    """오늘 날짜를 KST 기준으로 반환한다.

    UTC 자정~09시 사이에는 date.today()와 하루 차이가 나므로
    반드시 이 함수를 사용해야 한다.
    """
    return now_kst().date()


def now_stamp() -> str:
    """현재 시각을 'yyyy-mm-dd HH:MM:SS.SSS' 형태로 반환."""
    dt = now_kst()
    base = dt.strftime(_STAMP_FMT)
    ms = f"{dt.microsecond // 1000:03d}"
    return f"{base}.{ms}"


def now_filesafe() -> str:
    """파일명에 안전한 'yyyymmdd_HHMMSS' 형태로 반환."""
    return now_kst().strftime(_FILE_FMT)


def to_stamp(dt: datetime) -> str:
    """datetime 객체를 KST 간결 포맷으로 변환한다.

    tz-naive이면 UTC로 가정하고 KST로 변환한다.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    kst_dt = dt.astimezone(KST)
    base = kst_dt.strftime(_STAMP_FMT)
    ms = f"{kst_dt.microsecond // 1000:03d}"
    return f"{base}.{ms}"
