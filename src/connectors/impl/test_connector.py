"""TEST 커넥터 — 외부망 테스트 전용 PostgreSQL 읽기 전용 쿼리 실행.

작성자: 한철희 / 최종수정: 2026-04-11

외부망 개발/테스트 환경에서만 사용하는 PostgreSQL 커넥터.
폐쇄망에서는 system_db_overrides 를 비워두면 ADWConnector 등 업무 커넥터로
자동 라우팅되므로 이 커넥터는 인스턴스화되지 않는다.

system_db_overrides={"ADW":"TEST"} 설정을 통해 외부망에서
ADW 업무 요청을 이 커넥터로 redirect 한다.

핵심 함수/클래스:
    - TESTConnector: DatabaseConnector 구현체, 읽기 전용 쿼리 실행
    - SELECT/WITH 문만 허용 (정규식 사전 검증)

Dummy 모드: use_dummy=True(기본값)일 때 DB 연결 없이
dummy_data 모듈의 샘플 데이터로 동작한다.

드라이버 설정 필드(settings.test_db_*)는 드라이버/물리 축이므로 그대로 유지한다.
"""

from __future__ import annotations

import re
from typing import Any

from src.config import settings
from src.connectors.interfaces import DatabaseConnector, sanitize_row
from src.connectors.dummy_data import generate_dummy_data
from src.utils.logger import get_logger
from src.utils.truncate import truncate_log

logger = get_logger(__name__)


class TESTConnector(DatabaseConnector):
    """외부망 테스트 전용 PostgreSQL 커넥터 (읽기 전용).

    폐쇄망 전환 시 system_db_overrides 를 비우면 ADWConnector 등으로
    자동 라우팅되며 이 커넥터는 인스턴스화조차 되지 않는다.
    """

    @property
    def dialect(self) -> str:
        """SQL 방언 식별자."""
        return "postgres"

    @property
    def default_schema(self) -> str:
        """기본 스키마 접두사 (settings 참조)."""
        return settings.test_db_default_schema or ""

    def __init__(self, use_dummy: bool = True) -> None:
        self._use_dummy = use_dummy
        self._engine: Any = None

    async def connect(self) -> None:
        """DB 연결을 초기화한다."""
        if self._use_dummy:
            logger.info("TEST DB Dummy 모드로 초기화")
            return

        from sqlalchemy import URL
        from sqlalchemy.ext.asyncio import create_async_engine

        url = URL.create(
            drivername="postgresql+asyncpg",
            username=settings.test_db_user,
            password=settings.test_db_password,
            host=settings.test_db_host,
            port=settings.test_db_port,
            database=settings.test_db_name,
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
        logger.info("TEST DB 연결 완료")

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
        """읽기 전용 쿼리를 실행한다."""
        if not re.match(
            r"^\s*(SELECT|WITH)\b", query, re.IGNORECASE,
        ):
            raise ValueError(
                "SELECT 문만 실행할 수 있습니다"
            )

        if self._use_dummy:
            logger.info("TEST DB Dummy 쿼리 실행", sql=query)
            return generate_dummy_data(query)

        import time as _time

        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import AsyncSession

        _start = _time.perf_counter()
        async with AsyncSession(self._engine) as session:
            result = await session.execute(
                text(query), params or {},
            )
            columns = list(result.keys())
            rows = [
                sanitize_row(dict(zip(columns, row)))
                for row in result.fetchall()
            ]
        _elapsed = (_time.perf_counter() - _start) * 1000
        logger.info(
            "TEST DB 쿼리 실행",
            sql=truncate_log(query),
            row_count=len(rows),
            latency_ms=round(_elapsed, 1),
        )
        return rows
