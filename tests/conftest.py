"""단위 테스트 공통 픽스처 및 로깅 유틸리티.

모든 단위 테스트 모듈에서 공유하는 테스트 Tracker 로거를 제공한다.
로그는 logs/test/{yyyymmdd}-{모듈명}.log 에 기록된다.

제공 기능:
    - get_test_logger: 모듈별 파일 로거
    - log_test_case: 테스트 in/out Tracker 로깅
    - snapshot_cache fixture: LLM 응답 스냅샷 캐시 (flaky 방지)
    - sla_timer fixture: 성능 SLA assertion 헬퍼
    - real_queries fixture: 실 사용 로그 기반 테스트 데이터
    - 로그 로테이션: 7일 초과 로그 자동 정리
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

# 프로젝트 루트
PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = PROJECT_ROOT / "logs" / "test"


def get_test_logger(module_name: str) -> logging.Logger:
    """테스트 모듈 전용 파일 로거를 생성한다.

    로그 파일 경로: logs/test/{yyyymmdd}-{module_name}.log

    Args:
        module_name: 테스트 모듈명 (예: "test_preprocessor")

    Returns:
        logging.Logger: 파일 핸들러가 부착된 로거
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    log_file = LOG_DIR / f"{today}-{module_name}.log"

    logger = logging.getLogger(f"test_tracker.{module_name}")
    logger.setLevel(logging.DEBUG)

    # 중복 핸들러 방지
    if not logger.handlers:
        fh = logging.FileHandler(str(log_file), encoding="utf-8", mode="a")
        fh.setLevel(logging.DEBUG)
        formatter = logging.Formatter(
            "[%(asctime)s] %(levelname)-8s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        fh.setFormatter(formatter)
        logger.addHandler(fh)

        # 콘솔 출력 (pytest -s 모드에서 확인용)
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(formatter)
        logger.addHandler(ch)

    return logger


def log_test_case(
    logger: logging.Logger,
    test_name: str,
    input_data: object,
    expected: object,
    actual: object,
    passed: bool,
) -> None:
    """테스트 케이스의 입출력을 Tracker 수준으로 로깅한다.

    Args:
        logger: 테스트 모듈 로거
        test_name: 테스트 함수명
        input_data: 테스트 입력 데이터
        expected: 기대 결과
        actual: 실제 결과
        passed: 테스트 통과 여부
    """
    status = "PASS ✓" if passed else "FAIL ✗"
    logger.info(
        f"{'=' * 72}\n"
        f"  TEST: {test_name}\n"
        f"  STATUS: {status}\n"
        f"  INPUT:    {_truncate(input_data)}\n"
        f"  EXPECTED: {_truncate(expected)}\n"
        f"  ACTUAL:   {_truncate(actual)}\n"
        f"{'=' * 72}"
    )


def _truncate(obj: object, max_len: int = 500) -> str:
    """로그용 객체 문자열을 적절한 길이로 자른다."""
    s = str(obj)
    if len(s) > max_len:
        return s[:max_len] + "...(truncated)"
    return s


@pytest.fixture(autouse=True)
def _reset_cancel_store():
    """각 테스트 전후로 cancel store 싱글턴을 초기화한다."""
    from src.agents.graph.cancel import reset_cancel_store

    reset_cancel_store()
    yield
    reset_cancel_store()


@pytest.fixture(scope="session", autouse=True)
def ensure_log_dir():
    """로그 디렉토리가 존재하는지 확인한다."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)


# ══════════════════════════════════════════════════════════════
# 개선 1: LLM 응답 스냅샷 캐시 (flaky test 방지)
# ══════════════════════════════════════════════════════════════

@pytest.fixture
def llm_cache(request):
    """LLM 응답 스냅샷 캐시 픽스처.

    첫 실행 시 실제 LLM 응답을 저장하고, 이후 실행에서 재사용한다.
    강제 갱신: LLM_SNAPSHOT_UPDATE=1 환경 변수 설정.

    사용 예:
        async def test_intent(llm_cache):
            cached = llm_cache.get("casual_talk")
            if cached:
                result = cached
            else:
                result = await classify_intent_node(state)
                llm_cache.save("casual_talk", result)
    """
    from tests.fixtures.llm_snapshot import snapshot_cache

    module_name = request.module.__name__.split(".")[-1]
    return snapshot_cache(module_name)


# ══════════════════════════════════════════════════════════════
# 개선 5: 성능 SLA assertion 헬퍼
# ══════════════════════════════════════════════════════════════

class SLATimer:
    """성능 SLA 측정 컨텍스트 매니저.

    사용 예:
        def test_something(sla_timer):
            with sla_timer("SQL 생성", max_ms=5000):
                result = await generate_sql_node(state)
    """

    def __init__(self, logger: logging.Logger | None = None):
        self._logger = logger
        self._results: list[dict[str, Any]] = []

    def __call__(
        self, label: str, max_ms: float = 5000.0
    ) -> "_SLAContext":
        return _SLAContext(label, max_ms, self._results, self._logger)

    @property
    def results(self) -> list[dict[str, Any]]:
        """측정 결과 목록."""
        return self._results


class _SLAContext:
    """SLA 타이머 컨텍스트 매니저."""

    def __init__(
        self,
        label: str,
        max_ms: float,
        results: list,
        logger: logging.Logger | None,
    ):
        self._label = label
        self._max_ms = max_ms
        self._results = results
        self._logger = logger
        self._start = 0.0

    def __enter__(self):
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc):
        elapsed_ms = (time.perf_counter() - self._start) * 1000
        passed = elapsed_ms <= self._max_ms
        result = {
            "label": self._label,
            "elapsed_ms": round(elapsed_ms, 1),
            "max_ms": self._max_ms,
            "passed": passed,
        }
        self._results.append(result)

        if self._logger:
            status = "OK" if passed else "SLOW"
            self._logger.info(
                f"[SLA {status}] {self._label}: "
                f"{elapsed_ms:.1f}ms (limit: {self._max_ms}ms)"
            )

        assert passed, (
            f"SLA 위반: {self._label} — "
            f"{elapsed_ms:.1f}ms > {self._max_ms}ms"
        )
        return False


@pytest.fixture
def sla_timer():
    """성능 SLA 측정 픽스처.

    사용 예:
        def test_perf(sla_timer):
            with sla_timer("전처리", max_ms=100):
                result = await preprocess_node(state)
    """
    return SLATimer()


# ══════════════════════════════════════════════════════════════
# 개선 6: 실 사용 로그 기반 테스트 데이터
# ══════════════════════════════════════════════════════════════

@pytest.fixture(scope="session")
def real_queries() -> list[dict]:
    """실 사용 로그 기반 대표 질의 데이터를 로드한다.

    데이터 파일: tests/fixtures/real_queries.json
    없으면 빈 리스트 반환.
    """
    import json

    path = PROJECT_ROOT / "tests" / "fixtures" / "real_queries.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


# ══════════════════════════════════════════════════════════════
# 개선 7: 로그 로테이션 (7일 초과 로그 자동 정리)
# ══════════════════════════════════════════════════════════════

LOG_RETENTION_DAYS = int(os.getenv("TEST_LOG_RETENTION_DAYS", "7"))


@pytest.fixture(scope="session", autouse=True)
def cleanup_old_logs():
    """7일 이상 된 테스트 로그 파일을 자동 삭제한다.

    환경 변수 TEST_LOG_RETENTION_DAYS 로 보관 일수 조절 가능 (기본 7일).
    """
    if not LOG_DIR.exists():
        return

    cutoff = datetime.now() - timedelta(days=LOG_RETENTION_DAYS)
    removed = 0

    for log_file in LOG_DIR.glob("*.log"):
        try:
            # 파일명에서 날짜 추출: {yyyymmdd}-{module}.log
            date_str = log_file.name.split("-")[0]
            if len(date_str) == 8 and date_str.isdigit():
                file_date = datetime.strptime(date_str, "%Y%m%d")
                if file_date < cutoff:
                    log_file.unlink()
                    removed += 1
        except (ValueError, IndexError):
            continue

    if removed > 0:
        print(
            f"[conftest] 오래된 로그 {removed}개 정리 완료 "
            f"(보관: {LOG_RETENTION_DAYS}일)"
        )
