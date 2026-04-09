"""서울(KST) 타임존 유틸리티 — 프로젝트 전체 시간 기준 통일.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

서버·로그·트레이스 등 프로젝트 전체에서 사용하는 타임존(KST)과
포맷 상수를 한 곳에서 관리하여, datetime.now() 직접 호출로 인한
UTC/KST 불일치를 방지한다. 외부 의존성(pytz 등) 없이 stdlib
timezone만 사용하여 폐쇄망 배포 호환성을 보장한다.

UTC 자정~09시 사이에는 date.today()와 KST 날짜가 하루 차이나므로,
날짜가 필요한 곳에서는 반드시 today_kst()를 사용해야 한다.

핵심 함수:
    - now_kst: 현재 시각을 KST datetime으로 반환
    - today_kst: 오늘 날짜를 KST 기준으로 반환
    - now_stamp: 로그/트레이스용 'yyyy-mm-dd HH:MM:SS.SSS' 포맷
    - now_filesafe: 파일명용 'yyyymmdd_HHMMSS' 포맷
    - to_stamp: 임의 datetime을 KST 간결 포맷으로 변환

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
