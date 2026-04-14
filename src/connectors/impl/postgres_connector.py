"""PostgreSQL 공통 DB 커넥터.

작성자: 한철희 / 최종수정: 2026-04-11

PostgresConnector 는 SQL 이력·체크포인터 등 공통 메타 저장용 PostgreSQL DB 에
대한 범용 쿼리 실행을 제공한다. 폐쇄망에서도 그대로 사용된다.

외부망 테스트용 업무 DB 커넥터(TESTConnector)는 test_connector.py 로 분리되었다.

async SQLAlchemy(asyncpg)를 사용하며 풀 타임아웃과 쿼리 타임아웃을 설정으로 제어한다.

핵심 함수/클래스:
    - PostgresConnector: PostgreSQL 공통 DB 범용 쿼리 실행 (SQL 이력 등)

Dummy 모드: use_dummy=True(기본값)일 때 DB 연결 없이 내장 샘플 데이터를 반환한다.
"""

from __future__ import annotations

from typing import Any

from src.config import settings
from src.connectors.interfaces import DatabaseConnector, sanitize_row
from src.connectors.dummy_data import generate_dummy_data
from src.utils.logger import get_logger

logger = get_logger(__name__)


class PostgresConnector(DatabaseConnector):
    """PostgreSQL 공통 DB 커넥터 (SQL 이력·체크포인터 등).

    폐쇄망에서도 그대로 사용된다. SQL 이력 적재용으로 INSERT 등 범용 쿼리를 허용한다.
    """

    @property
    def dialect(self) -> str:
        """SQL 방언 식별자."""
        return "postgres"

    @property
    def default_schema(self) -> str:
        """기본 스키마 (없음 — 범용 커넥터)."""
        return ""

    def __init__(self, use_dummy: bool = True) -> None:
        self._use_dummy = use_dummy
        self._engine: Any = None

    async def connect(self) -> None:
        """DB 연결을 초기화한다."""
        if self._use_dummy:
            logger.info("Postgres 공통 DB Dummy 모드로 초기화")
            return

        from sqlalchemy import URL
        from sqlalchemy.ext.asyncio import (
            create_async_engine,
        )

        url = URL.create(
            drivername="postgresql+asyncpg",
            username=settings.postgres_db_user,
            password=settings.postgres_db_password,
            host=settings.postgres_db_host,
            port=settings.postgres_db_port,
            database=settings.postgres_db_name,
        )
        self._engine = create_async_engine(
            url,
            echo=False,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_pool_max_overflow,
            pool_recycle=settings.db_pool_recycle,
            pool_pre_ping=True,
            pool_timeout=settings.db_pool_timeout,
            connect_args={
                "command_timeout": settings.db_query_timeout,
            },
        )

    async def disconnect(self) -> None:
        """DB 연결을 종료한다."""
        if self._engine:
            await self._engine.dispose()

    async def health_check(self) -> bool:
        """연결 상태를 확인한다."""
        if self._use_dummy:
            return True
        try:
            from sqlalchemy import text

            async with self._engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception as e:
            logger.debug("health_check 실패", error=str(e))
            return False

    async def execute_query(
        self,
        query: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """범용 쿼리를 실행한다 (SELECT 제한 없음, 이력 적재용)."""
        if self._use_dummy:
            return generate_dummy_data(query)
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import AsyncSession

        async with AsyncSession(self._engine) as session:
            result = await session.execute(
                text(query), params or {},
            )
            columns = list(result.keys())
            return [
                sanitize_row(dict(zip(columns, row)))
                for row in result.fetchall()
            ]
