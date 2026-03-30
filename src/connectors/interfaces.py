"""커넥터 추상 인터페이스 — 외부 시스템 연동을 위한 계약 정의.

impl/ 패키지의 모든 커넥터 구현체가 준수해야 하는 공통 인터페이스를
3단계 계층 구조로 정의한다.

계층 구조:
    - BaseConnector: 최상위 ABC. connect/disconnect/health_check 라이프사이클을 강제한다.
    - SearchConnector(BaseConnector): 검색 기능(search)을 추가로 요구하는 인터페이스.
      ElasticSearch, Qdrant 등 검색 엔진 커넥터가 이를 구현한다.
    - DatabaseConnector(BaseConnector): 읽기 전용 쿼리 실행(execute_query)을 요구하는
      인터페이스. 정보계 DB, 이력 DB 등 SQL 실행 커넥터가 이를 구현한다.

이 분리를 통해 manager.py(ConnectorManager)가 타입에 따라 커넥터를
일관되게 관리·교체할 수 있으며, 폐쇄망 전환 시 impl/ 내 구현체만 교체하면 된다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


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
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """읽기 전용 쿼리를 실행한다."""
