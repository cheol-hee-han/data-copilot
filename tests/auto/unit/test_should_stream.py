"""_should_stream 단위 테스트.

테스트 대상:
    [src/main.py :: _should_stream]
    - 클라이언트가 streaming=False → False
    - 클라이언트가 streaming=True + 일반 질의 → True
    - 빈 입력 / 슬래시 명령 → False
"""

from __future__ import annotations

from src.main import _should_stream


def test_false_when_client_disabled() -> None:
    assert _should_stream(False, "여신 현황 알려줘") is False


def test_true_when_client_enabled_and_normal_text() -> None:
    assert _should_stream(True, "여신 현황 알려줘") is True


def test_false_on_slash_command() -> None:
    assert _should_stream(True, "/history") is False
    assert _should_stream(True, "  /cancel ") is False


def test_false_on_empty_input() -> None:
    assert _should_stream(True, "") is False
    assert _should_stream(True, "   ") is False
