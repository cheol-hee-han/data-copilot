"""커넥터 추상 인터페이스 — 외부 시스템 연동을 위한 계약 정의.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

impl/ 패키지의 모든 커넥터 구현체가 준수해야 하는 공통 인터페이스를
3단계 계층 구조로 정의한다.

계층 구조:
    - BaseConnector: 최상위 ABC. connect/disconnect/health_check 라이프사이클을 강제한다.
    - SearchConnector(BaseConnector): 검색 기능(search)을 추가로 요구하는 인터페이스.
      Qdrant, MongoDB 등 검색 엔진 커넥터가 이를 구현한다.
    - DatabaseConnector(BaseConnector): 읽기 전용 쿼리 실행(execute_query)을 요구하는
      인터페이스. 정보계 DB, 이력 DB 등 SQL 실행 커넥터가 이를 구현한다.

이 분리를 통해 manager.py(ConnectorManager)가 타입에 따라 커넥터를
일관되게 관리·교체할 수 있으며, 폐쇄망 전환 시 impl/ 내 구현체만 교체하면 된다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID


class BaseConnector(ABC):
    """외부 시스템 커넥터 추상 기본 클래스."""

    @abstractmethod
    async def connect(self) -> None:
        """커넥션을 초기화한다."""

    @abstractmethod
    async def disconnect(self) -> None:
        """커넥션을 종료한다."""

    @abstractmethod
    async def health_check(self) -> bool:
        """연결 상태를 확인한다."""


class SearchConnector(BaseConnector):
    """검색 기능을 제공하는 커넥터 인터페이스."""

    @abstractmethod
    async def search(self, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        """검색을 수행한다."""


class DatabaseConnector(BaseConnector):
    """DB 커넥터 인터페이스."""

    @property
    @abstractmethod
    def dialect(self) -> str:
        """sqlglot 호환 dialect을 반환한다 ('postgres' | 'tsql' | 'hive')."""

    @property
    @abstractmethod
    def default_schema(self) -> str:
        """이 커넥터의 기본 스키마명을 반환한다 (없으면 빈 문자열)."""

    @abstractmethod
    async def execute_query(
        self, query: str, params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """읽기 전용 쿼리를 실행하고 list[dict]를 반환한다.

        반환값은 항상 list[dict[str, Any]] 이다.
        래퍼 객체(.rows 등)로 감싸지 않는다.
        호출자는 isinstance(result, list) 로 검증해야 한다.
        """


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DB row 타입 정규화
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def sanitize_row(row: dict[str, Any]) -> dict[str, Any]:
    """DB row의 값을 JSON 직렬화 가능한 타입으로 정규화한다.

    asyncpg 등 DB 드라이버가 반환하는 Python 전용 타입(Decimal, date 등)을
    JSON 표준 타입(int, float, str)으로 변환한다.
    모든 DatabaseConnector 구현체는 execute_query 결과를 반환하기 전에
    이 함수를 적용해야 한다.
    """
    return {k: _to_json_safe(v) for k, v in row.items()}


def _to_json_safe(value: Any) -> Any:
    """단일 값을 JSON 직렬화 가능한 타입으로 변환한다."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        if value.is_nan() or value.is_infinite():
            return None
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, memoryview):
        return bytes(value).decode("utf-8", errors="replace")
    return value
