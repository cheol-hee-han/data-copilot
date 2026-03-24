"""LangSmith 트레이싱 설정(setup/teardown) 단위 테스트.

테스트 대상:
    LangSmith 연동 활성화/비활성화에 따른 환경변수(LANGCHAIN_*)
    설정·해제·멱등성을 검증한다.

입력 예시 (정상):
    - enabled=True, api_key="ls-test-key" → LANGCHAIN_TRACING_V2="true" 설정
    - teardown → 환경변수 제거

결과 예시 (오류 케이스):
    - enabled=True, api_key="" → setup 실패 (False 반환)
    - enabled=False → 기존 환경변수 제거

실행 스크립트:
    pytest tests/unit/test_langsmith.py -v

참고:
    - 외부 의존성 없음 (실제 LangSmith 연결 불필요, monkeypatch로 설정 주입)
    - 테스트 대상 소스: src/tools/langsmith.py
"""

from __future__ import annotations

import os

import pytest

from src.tools.langsmith import (
    is_langsmith_available,
    reset_langsmith,
    setup_langsmith,
    teardown_langsmith,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """각 테스트 전후로 LangSmith 환경변수와 상태를 정리한다."""
    reset_langsmith()
    for key in (
        "LANGCHAIN_TRACING_V2",
        "LANGCHAIN_API_KEY",
        "LANGCHAIN_PROJECT",
        "LANGCHAIN_ENDPOINT",
    ):
        monkeypatch.delenv(key, raising=False)
    yield
    reset_langsmith()
    teardown_langsmith()


def test_disabled_by_default(monkeypatch):
    """기본 설정은 비활성화."""
    monkeypatch.setattr("src.tools.langsmith.settings.langsmith_enabled", False)
    assert not is_langsmith_available()
    result = setup_langsmith()
    assert result is False
    assert os.environ.get("LANGCHAIN_TRACING_V2") is None


def test_enabled_without_api_key(monkeypatch):
    """활성화했지만 API 키가 없으면 비활성화."""
    monkeypatch.setattr("src.tools.langsmith.settings.langsmith_enabled", True)
    monkeypatch.setattr("src.tools.langsmith.settings.langsmith_api_key", "")
    result = setup_langsmith()
    assert result is False


def test_enabled_with_api_key(monkeypatch):
    """활성화 + API 키 설정 시 환경변수가 설정된다."""
    monkeypatch.setattr("src.tools.langsmith.settings.langsmith_enabled", True)
    monkeypatch.setattr("src.tools.langsmith.settings.langsmith_api_key", "ls-test-key")
    monkeypatch.setattr("src.tools.langsmith.settings.langsmith_project", "test-project")
    result = setup_langsmith()
    assert result is True
    assert os.environ["LANGCHAIN_TRACING_V2"] == "true"
    assert os.environ["LANGCHAIN_API_KEY"] == "ls-test-key"
    assert os.environ["LANGCHAIN_PROJECT"] == "test-project"


def test_teardown_removes_env_vars(monkeypatch):
    """teardown이 환경변수를 제거한다."""
    monkeypatch.setattr("src.tools.langsmith.settings.langsmith_enabled", True)
    monkeypatch.setattr("src.tools.langsmith.settings.langsmith_api_key", "ls-test-key")
    setup_langsmith()
    assert os.environ.get("LANGCHAIN_TRACING_V2") == "true"

    teardown_langsmith()
    assert os.environ.get("LANGCHAIN_TRACING_V2") is None
    assert os.environ.get("LANGCHAIN_API_KEY") is None


def test_idempotent_setup(monkeypatch):
    """setup_langsmith을 여러 번 호출해도 안전하다."""
    monkeypatch.setattr("src.tools.langsmith.settings.langsmith_enabled", True)
    monkeypatch.setattr("src.tools.langsmith.settings.langsmith_api_key", "ls-test-key")
    assert setup_langsmith() is True
    assert setup_langsmith() is True  # 두 번째 호출도 정상


def test_disabled_clears_existing_env(monkeypatch):
    """비활성화 시 기존 환경변수가 제거된다."""
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_API_KEY"] = "old-key"
    monkeypatch.setattr("src.tools.langsmith.settings.langsmith_enabled", False)
    setup_langsmith()
    assert os.environ.get("LANGCHAIN_TRACING_V2") is None
    assert os.environ.get("LANGCHAIN_API_KEY") is None
