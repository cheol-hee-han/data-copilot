"""LLM 서킷브레이커 단위 테스트.

테스트 대상:
    [circuit_breaker.py]
    - CircuitBreaker 상태 전이: CLOSED → OPEN → HALF_OPEN → CLOSED
    - 연속 실패 임계 도달 시 OPEN 전이
    - OPEN 상태에서 CircuitOpenError 로 즉시 거부
    - 리셋 타임아웃 경과 후 HALF_OPEN 시험 통과
    - HALF_OPEN 에서 실패 시 OPEN 재진입
    - 5xx 상태 오류 카운트 / 4xx 미카운트
    - 비카운트 예외(ValueError 등) 통과 시 상태 유지
    - reset() 강제 리셋

실행:
    pytest tests/auto/unit/test_circuit_breaker.py -v
"""

from __future__ import annotations

import asyncio

import pytest

from src.utils.llm.circuit_breaker import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
)


class _StatusError(Exception):
    """Anthropic/OpenAI SDK 의 APIStatusError 모사."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"status {status_code}")
        self.status_code = status_code


async def _fail_factory(exc: BaseException):
    raise exc


async def _ok_factory():
    return "ok"


@pytest.mark.asyncio
async def test_closed_state_passes_through() -> None:
    """정상 호출은 CLOSED 상태를 유지한다."""
    cb = CircuitBreaker(fail_threshold=3, reset_timeout_sec=0.1)
    result = await cb.call(lambda: _ok_factory())
    assert result == "ok"
    assert cb.state is CircuitState.CLOSED


@pytest.mark.asyncio
async def test_opens_after_consecutive_failures() -> None:
    """연속 실패 임계 도달 시 OPEN 으로 전이한다."""
    cb = CircuitBreaker(fail_threshold=3, reset_timeout_sec=10.0)
    for _ in range(3):
        with pytest.raises(asyncio.TimeoutError):
            await cb.call(lambda: _fail_factory(asyncio.TimeoutError()))
    assert cb.state is CircuitState.OPEN


@pytest.mark.asyncio
async def test_open_state_fast_fails() -> None:
    """OPEN 상태에서는 CircuitOpenError 로 즉시 거부된다."""
    cb = CircuitBreaker(fail_threshold=1, reset_timeout_sec=10.0)
    with pytest.raises(asyncio.TimeoutError):
        await cb.call(lambda: _fail_factory(asyncio.TimeoutError()))
    assert cb.state is CircuitState.OPEN
    with pytest.raises(CircuitOpenError):
        await cb.call(lambda: _ok_factory())


@pytest.mark.asyncio
async def test_half_open_after_reset_timeout_closes_on_success() -> None:
    """리셋 타임아웃 경과 후 HALF_OPEN 에서 성공하면 CLOSED 로 복귀."""
    cb = CircuitBreaker(fail_threshold=1, reset_timeout_sec=0.05)
    with pytest.raises(asyncio.TimeoutError):
        await cb.call(lambda: _fail_factory(asyncio.TimeoutError()))
    assert cb.state is CircuitState.OPEN
    await asyncio.sleep(0.06)
    result = await cb.call(lambda: _ok_factory())
    assert result == "ok"
    assert cb.state is CircuitState.CLOSED


@pytest.mark.asyncio
async def test_half_open_failure_reopens() -> None:
    """HALF_OPEN 에서 실패하면 OPEN 으로 즉시 재진입."""
    cb = CircuitBreaker(fail_threshold=1, reset_timeout_sec=0.05)
    with pytest.raises(asyncio.TimeoutError):
        await cb.call(lambda: _fail_factory(asyncio.TimeoutError()))
    await asyncio.sleep(0.06)
    with pytest.raises(asyncio.TimeoutError):
        await cb.call(lambda: _fail_factory(asyncio.TimeoutError()))
    assert cb.state is CircuitState.OPEN


@pytest.mark.asyncio
async def test_5xx_counted_4xx_not_counted() -> None:
    """5xx 상태 오류는 카운트, 4xx 는 카운트하지 않는다."""
    cb = CircuitBreaker(fail_threshold=2, reset_timeout_sec=10.0)

    # 4xx: 카운트되지 않음
    for _ in range(5):
        with pytest.raises(_StatusError):
            await cb.call(lambda: _fail_factory(_StatusError(400)))
    assert cb.state is CircuitState.CLOSED

    # 5xx: 카운트되어 임계 도달 시 OPEN
    for _ in range(2):
        with pytest.raises(_StatusError):
            await cb.call(lambda: _fail_factory(_StatusError(503)))
    assert cb.state is CircuitState.OPEN


@pytest.mark.asyncio
async def test_uncounted_exception_passes_through() -> None:
    """비카운트 예외(ValueError 등)는 re-raise 하되 상태를 변경하지 않는다."""
    cb = CircuitBreaker(fail_threshold=2, reset_timeout_sec=10.0)
    for _ in range(5):
        with pytest.raises(ValueError):
            await cb.call(lambda: _fail_factory(ValueError("bad input")))
    assert cb.state is CircuitState.CLOSED


@pytest.mark.asyncio
async def test_success_resets_failure_count() -> None:
    """중간 성공 시 연속 실패 카운트가 리셋된다."""
    cb = CircuitBreaker(fail_threshold=3, reset_timeout_sec=10.0)
    for _ in range(2):
        with pytest.raises(asyncio.TimeoutError):
            await cb.call(lambda: _fail_factory(asyncio.TimeoutError()))
    await cb.call(lambda: _ok_factory())
    # 다시 2건 실패해도 OPEN 이 아니어야 함 (카운트가 리셋됐으므로)
    for _ in range(2):
        with pytest.raises(asyncio.TimeoutError):
            await cb.call(lambda: _fail_factory(asyncio.TimeoutError()))
    assert cb.state is CircuitState.CLOSED


@pytest.mark.asyncio
async def test_manual_reset() -> None:
    """reset() 호출 시 CLOSED 로 강제 복귀."""
    cb = CircuitBreaker(fail_threshold=1, reset_timeout_sec=10.0)
    with pytest.raises(asyncio.TimeoutError):
        await cb.call(lambda: _fail_factory(asyncio.TimeoutError()))
    assert cb.state is CircuitState.OPEN
    cb.reset()
    assert cb.state is CircuitState.CLOSED
    result = await cb.call(lambda: _ok_factory())
    assert result == "ok"
