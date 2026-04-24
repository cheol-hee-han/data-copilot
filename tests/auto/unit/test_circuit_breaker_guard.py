"""CircuitBreaker.guard() primitive 단위 테스트.

테스트 대상:
    [circuit_breaker.py :: CircuitBreaker.guard]
    - asynccontextmanager 로 블록 감싸기 (call/stream 공용)
    - 정상 종료 → _on_success
    - counted 예외 → _on_failure 후 re-raise
    - 5xx 상태 오류 → _on_failure 후 re-raise
    - 4xx 상태 오류 → 카운트 안 함
    - OPEN 상태 → 블록 진입 전 CircuitOpenError
    - call() 이 guard() 위에서 동작 (외부 API 보존)

실행:
    pytest tests/auto/unit/test_circuit_breaker_guard.py -v
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
    def __init__(self, status_code: int) -> None:
        super().__init__(f"status {status_code}")
        self.status_code = status_code


@pytest.mark.asyncio
async def test_guard_success_path() -> None:
    """정상 종료 시 consecutive_failures 가 0, 상태 CLOSED."""
    cb = CircuitBreaker(fail_threshold=3)
    async with cb.guard():
        pass  # 정상 블록
    assert cb.state is CircuitState.CLOSED


@pytest.mark.asyncio
async def test_guard_counted_exception_trips_circuit() -> None:
    """counted 예외 연속 발생 시 OPEN 으로 전이."""
    cb = CircuitBreaker(fail_threshold=2, reset_timeout_sec=10.0)
    for _ in range(2):
        with pytest.raises(asyncio.TimeoutError):
            async with cb.guard():
                raise asyncio.TimeoutError
    assert cb.state is CircuitState.OPEN


@pytest.mark.asyncio
async def test_guard_5xx_status_error_counted() -> None:
    """5xx 상태 오류는 실패로 카운트된다."""
    cb = CircuitBreaker(fail_threshold=1, reset_timeout_sec=10.0)
    with pytest.raises(_StatusError):
        async with cb.guard():
            raise _StatusError(503)
    assert cb.state is CircuitState.OPEN


@pytest.mark.asyncio
async def test_guard_4xx_status_error_not_counted() -> None:
    """4xx 상태 오류는 실패로 카운트하지 않는다 (프롬프트·인증 문제)."""
    cb = CircuitBreaker(fail_threshold=1, reset_timeout_sec=10.0)
    with pytest.raises(_StatusError):
        async with cb.guard():
            raise _StatusError(401)
    assert cb.state is CircuitState.CLOSED


@pytest.mark.asyncio
async def test_guard_open_state_rejects_immediately() -> None:
    """OPEN 상태 진입 시 블록 진입 전 CircuitOpenError."""
    cb = CircuitBreaker(fail_threshold=1, reset_timeout_sec=10.0)
    with pytest.raises(asyncio.TimeoutError):
        async with cb.guard():
            raise asyncio.TimeoutError
    assert cb.state is CircuitState.OPEN

    entered = False
    with pytest.raises(CircuitOpenError):
        async with cb.guard():
            entered = True  # pragma: no cover
    assert entered is False


@pytest.mark.asyncio
async def test_guard_half_open_success_closes_circuit() -> None:
    """HALF_OPEN 에서 성공 시 CLOSED 로 복귀."""
    cb = CircuitBreaker(fail_threshold=1, reset_timeout_sec=0.01)
    with pytest.raises(asyncio.TimeoutError):
        async with cb.guard():
            raise asyncio.TimeoutError
    assert cb.state is CircuitState.OPEN

    await asyncio.sleep(0.02)
    async with cb.guard():
        pass
    assert cb.state is CircuitState.CLOSED


@pytest.mark.asyncio
async def test_guard_half_open_failure_reopens() -> None:
    """HALF_OPEN 에서 실패 시 OPEN 재진입."""
    cb = CircuitBreaker(fail_threshold=1, reset_timeout_sec=0.01)
    with pytest.raises(asyncio.TimeoutError):
        async with cb.guard():
            raise asyncio.TimeoutError
    await asyncio.sleep(0.02)
    with pytest.raises(asyncio.TimeoutError):
        async with cb.guard():
            raise asyncio.TimeoutError
    assert cb.state is CircuitState.OPEN


@pytest.mark.asyncio
async def test_call_uses_guard_primitive() -> None:
    """기존 call() API 가 guard() 위에서 정상 동작한다 (외부 API 보존)."""
    cb = CircuitBreaker(fail_threshold=2, reset_timeout_sec=10.0)

    async def _ok():
        return 42

    assert await cb.call(_ok) == 42
    assert cb.state is CircuitState.CLOSED

    async def _fail():
        raise asyncio.TimeoutError

    for _ in range(2):
        with pytest.raises(asyncio.TimeoutError):
            await cb.call(_fail)
    assert cb.state is CircuitState.OPEN

    with pytest.raises(CircuitOpenError):
        await cb.call(_ok)


@pytest.mark.asyncio
async def test_guard_non_counted_exception_passes_through() -> None:
    """비-카운트 예외는 상태를 변경하지 않고 전파된다."""
    cb = CircuitBreaker(fail_threshold=1, reset_timeout_sec=10.0)
    with pytest.raises(ValueError):
        async with cb.guard():
            raise ValueError("not counted")
    assert cb.state is CircuitState.CLOSED
