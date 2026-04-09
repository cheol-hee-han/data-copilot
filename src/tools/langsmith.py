"""LangSmith 트레이싱 설정 모듈 — LangGraph 파이프라인 관측성 관리.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

LangSmith 트레이싱의 활성화/비활성화를 환경변수 기반으로 제어한다.
settings.langsmith_enabled=True이고 API 키가 설정된 경우에만 LANGCHAIN_TRACING_V2,
LANGCHAIN_API_KEY, LANGCHAIN_PROJECT, LANGCHAIN_ENDPOINT 환경변수를 설정하여
LangChain/LangGraph가 자동으로 트레이스를 LangSmith 서버에 전송하도록 한다.
비활성화 시에는 관련 환경변수를 명시적으로 제거하여 의도치 않은 외부 통신을 차단한다.
서버 기동 시 lifespan에서 setup_langsmith()를 호출하여 초기화한다.

핵심 함수/클래스:
    - setup_langsmith: 트레이싱 환경변수 설정 및 초기화 (멱등, 중복 호출 안전)
    - teardown_langsmith: 트레이싱 환경변수 정리
    - is_langsmith_available: 사용 가능 여부 확인
    - reset_langsmith: 테스트용 초기화 상태 리셋

폐쇄망 대응: settings.langsmith_enabled=False(기본값)로 설정하면 외부 API 호출 없이
동작하며, 환경변수도 명시적으로 제거되어 폐쇄망에서 안전하게 운영된다.
"""

from __future__ import annotations

import os

from src.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

_initialized = False


def is_langsmith_available() -> bool:
    """LangSmith 사용 가능 여부를 확인한다."""
    return (
        settings.langsmith_enabled
        and bool(settings.langsmith_api_key)
    )


def setup_langsmith() -> bool:
    """LangSmith 트레이싱을 초기화한다.

    환경변수를 설정하여 LangChain/LangGraph가 자동으로
    LangSmith에 트레이스를 전송하도록 한다.

    Returns:
        True: 초기화 성공, False: 비활성 또는 실패
    """
    global _initialized

    if _initialized:
        return is_langsmith_available()

    if not settings.langsmith_enabled:
        logger.info("LangSmith 비활성화 (폐쇄망 모드)")
        # 환경변수를 명시적으로 제거하여 의도치 않은 활성화 방지
        os.environ.pop("LANGCHAIN_TRACING_V2", None)
        os.environ.pop("LANGCHAIN_API_KEY", None)
        _initialized = True
        return False

    if not settings.langsmith_api_key:
        logger.warning("LangSmith 활성화 설정이지만 API 키가 없음, 비활성화")
        os.environ.pop("LANGCHAIN_TRACING_V2", None)
        _initialized = True
        return False

    # LangSmith 환경변수 설정
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_API_KEY"] = settings.langsmith_api_key
    os.environ["LANGCHAIN_PROJECT"] = settings.langsmith_project
    os.environ["LANGCHAIN_ENDPOINT"] = settings.langsmith_endpoint

    logger.info(
        "LangSmith 트레이싱 활성화",
        project=settings.langsmith_project,
        endpoint=settings.langsmith_endpoint,
    )
    _initialized = True
    return True


def teardown_langsmith() -> None:
    """LangSmith 환경변수를 정리한다."""
    global _initialized
    for key in (
        "LANGCHAIN_TRACING_V2",
        "LANGCHAIN_API_KEY",
        "LANGCHAIN_PROJECT",
        "LANGCHAIN_ENDPOINT",
    ):
        os.environ.pop(key, None)
    _initialized = False


def reset_langsmith() -> None:
    """테스트에서 초기화 상태를 리셋한다."""
    global _initialized
    _initialized = False
