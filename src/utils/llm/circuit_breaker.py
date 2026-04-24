"""LLM 호출용 서킷브레이커 — 외부 API 연속 실패 시 fast-fail 제공.

작성자: 한철희 / 최종수정: 2026-04-15

외부 LLM 서비스(Anthropic / OpenAI 호환 / 내부 LLM Gateway)가 타임아웃·네트워크
장애·5xx 오류를 연속으로 발생시킬 때, 이후 호출을 일정 시간 즉시 실패 처리하여
워커 풀 고갈·장애 전파를 방지한다.

상태 전이:
    CLOSED  — 정상. 모든 호출 통과.
    OPEN    — 연속 실패 임계 초과. 모든 호출을 즉시 CircuitOpenError 로 거부.
    HALF_OPEN — 리셋 대기 경과 후 1건만 시험 통과. 성공 시 CLOSED, 실패 시 OPEN 연장.

카운트 대상(기본값):
    - asyncio.TimeoutError / httpx.TimeoutException / httpx.NetworkError
    - anthropic.APIStatusError 중 5xx
    - openai.APIStatusError 중 5xx
    - 그 외 transport 실패 (connection reset 등)

제외 대상 (의도적으로 카운트하지 않음):
    - 4xx (프롬프트·인증 문제, 서버 탓 아님)
    - Pydantic 검증 실패 (클라이언트 버그)

활성화/비활성화:
    settings.llm_cb_enabled = False 로 내리면 CB 래퍼가 투명 통과 (기존 동작 유지).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import httpx
import structlog


class CircuitState(str, Enum):
    """서킷 상태."""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitOpenError(RuntimeError):
    """서킷이 OPEN 상태여서 호출이 즉시 거부됨을 나타낸다.

    사용자에게 노출되는 메시지는 "LLM 서비스 일시 불가" 수준으로 간결하게 유지한다.
    """


@dataclass
class CircuitBreaker:
    """비동기 호출용 서킷브레이커.

    Attributes:
        fail_threshold: 연속 실패 횟수가 이 값 이상이면 OPEN 으로 전이.
        reset_timeout_sec: OPEN 진입 후 HALF_OPEN 으로 전이하기까지 대기 시간.
        counted_excs: 실패로 카운트할 예외 튜플.
        name: 로깅 식별자 (provider 이름 등).
    """

    fail_threshold: int = 5
    reset_timeout_sec: float = 30.0
    counted_excs: tuple[type[BaseException], ...] = field(default=(
        asyncio.TimeoutError,
        httpx.TimeoutException,
        httpx.NetworkError,
    ))
    name: str = "llm"

    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _consecutive_failures: int = field(default=0, init=False)
    _opened_at: float = field(default=0.0, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _log: Any = field(
        default_factory=lambda: structlog.get_logger("llm.circuit_breaker"),
        init=False,
        repr=False,
    )

    @property
    def state(self) -> CircuitState:
        """현재 서킷 상태를 반환한다."""
        return self._state

    @asynccontextmanager
    async def guard(self) -> AsyncIterator[None]:
        """보호된 블록을 감싼다 (call/stream 공용 primitive).

        OPEN 상태면 즉시 CircuitOpenError. 블록이 예외 없이 정상 종료하면 성공으로
        카운트, `counted_excs` 또는 5xx 상태 오류로 종료하면 실패로 카운트한다.

        Raises:
            CircuitOpenError: 서킷 OPEN 상태에서 리셋 타임아웃 미경과.
            원본 예외: 블록 내 예외는 그대로 re-raise.
        """
        async with self._lock:
            self._maybe_transition_to_half_open()
            if self._state is CircuitState.OPEN:
                raise CircuitOpenError(
                    f"LLM 서비스 일시 불가 (circuit={self.name}, "
                    f"재시도 가능 {self._remaining_reset_sec():.1f}초 후)",
                )
        try:
            yield
        except self.counted_excs as exc:
            await self._on_failure(exc)
            raise
        except Exception as exc:
            if _is_server_side_status_error(exc):
                await self._on_failure(exc)
            raise
        else:
            await self._on_success()

    async def call(
        self,
        coro_factory: Callable[[], Awaitable[Any]],
    ) -> Any:
        """보호된 코루틴을 호출한다 (외부 API 보존, guard() 위에서 동작).

        Args:
            coro_factory: 호출 시 코루틴을 반환하는 factory (lambda 또는 함수).
                재시도 시 코루틴을 새로 생성해야 하므로 factory 형태로 받는다.

        Returns:
            coro_factory() 의 결과.

        Raises:
            CircuitOpenError: 서킷이 OPEN 상태이고 리셋 타임아웃이 경과하지 않은 경우.
            원본 예외: coro_factory() 실행 중 발생한 예외를 그대로 re-raise.
        """
        async with self.guard():
            return await coro_factory()

    # ──────────────────────────────── 내부 전이 ────────────────────────────────

    def _maybe_transition_to_half_open(self) -> None:
        """OPEN 상태에서 리셋 타임아웃이 경과했으면 HALF_OPEN 으로 전이한다."""
        if self._state is not CircuitState.OPEN:
            return
        if self._remaining_reset_sec() > 0.0:
            return
        self._state = CircuitState.HALF_OPEN
        self._log.info(
            "circuit.half_open",
            name=self.name,
            consecutive_failures=self._consecutive_failures,
        )

    def _remaining_reset_sec(self) -> float:
        """OPEN → HALF_OPEN 전이까지 남은 초."""
        return max(0.0, self.reset_timeout_sec - (time.monotonic() - self._opened_at))

    async def _on_success(self) -> None:
        """호출 성공 시 처리."""
        async with self._lock:
            if self._state is CircuitState.HALF_OPEN:
                self._log.info(
                    "circuit.closed",
                    name=self.name,
                )
            self._state = CircuitState.CLOSED
            self._consecutive_failures = 0
            self._opened_at = 0.0

    async def _on_failure(self, exc: BaseException) -> None:
        """호출 실패 시 처리."""
        async with self._lock:
            self._consecutive_failures += 1
            exc_name = type(exc).__name__
            if (
                self._state is CircuitState.HALF_OPEN
                or self._consecutive_failures >= self.fail_threshold
            ):
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()
                self._log.warning(
                    "circuit.open",
                    name=self.name,
                    consecutive_failures=self._consecutive_failures,
                    exc_type=exc_name,
                    reset_timeout_sec=self.reset_timeout_sec,
                )
            else:
                self._log.debug(
                    "circuit.failure",
                    name=self.name,
                    consecutive_failures=self._consecutive_failures,
                    exc_type=exc_name,
                )

    def reset(self) -> None:
        """테스트·수동 개입용 강제 리셋 — CLOSED 로 복귀."""
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at = 0.0


def _is_server_side_status_error(exc: BaseException) -> bool:
    """Anthropic / OpenAI SDK 의 5xx 상태 오류 여부 판정.

    SDK import 는 느릴 수 있으므로 문자열 기반 duck typing 으로 판정한다.
    (벤더 SDK 업그레이드 시 예외 클래스 교체·이름 변경에도 강인.)
    """
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        return 500 <= status_code < 600
    return False
