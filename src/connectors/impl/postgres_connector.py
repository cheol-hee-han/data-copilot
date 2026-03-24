"""PostgreSQL 커넥터 — 정보계 DB 쿼리 실행 및 SQL 이력 검색.

두 가지 역할의 커넥터를 제공한다.
InfoDBConnector는 정보계 DB에 대해 읽기 전용(SELECT/WITH만 허용) 쿼리를 실행하며,
SQL 문 앞부분을 정규식으로 검증하여 DML/DDL을 원천 차단한다.
HistoryDBConnector는 과거 성공한 SQL 수행이력 DB에서 키워드 ILIKE 매칭으로
유사한 SQL을 최대 5건 검색하여 SQL 생성 시 참조 자료로 활용한다.
SQL 템플릿은 resources/queries/search_similar_sql.sql 에서 로드한다.
두 커넥터 모두 async SQLAlchemy(asyncpg)를 사용하며 풀 타임아웃과 쿼리 타임아웃을 설정으로 제어한다.

핵심 함수/클래스:
    - InfoDBConnector: 정보계 DB 읽기 전용 쿼리 실행 (SELECT 문만 허용)
    - HistoryDBConnector: SQL 이력 DB에서 유사 과거 SQL 키워드 검색
    - HistoryDBConnector.search_similar_sql: 자연어 질의를 키워드 분해 후 ILIKE 검색

Dummy 모드: use_dummy=True(기본값)일 때 DB 연결 없이 동작한다.
InfoDBConnector는 SQL의 SELECT 절을 파싱하여 컬럼 alias에 맞는 랜덤 샘플 데이터를 생성하고,
HistoryDBConnector는 내장된 5건의 샘플 SQL 이력을 키워드 매칭으로 반환한다.
폐쇄망 전환 시 settings의 DB 접속 정보를 변경하면 Sybase IQ/Impala 등으로 대체 가능하다.
"""

from __future__ import annotations

import re
from typing import Any

from src.config import settings
from src.connectors.interfaces import DatabaseConnector
from src.connectors.dummy_data import (
    generate_dummy_data,
    search_dummy_sql_history,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)


class InfoDBConnector(DatabaseConnector):
    """정보계 DB 커넥터 (읽기 전용)."""

    def __init__(self, use_dummy: bool = True) -> None:
        self._use_dummy = use_dummy
        self._engine: Any = None

    async def connect(self) -> None:
        """DB 연결 초기화."""
        if self._use_dummy:
            logger.info("정보계 DB Dummy 모드로 초기화")
            return

        from sqlalchemy.ext.asyncio import (
            create_async_engine,
        )

        url = (
            f"postgresql+asyncpg://"
            f"{settings.info_db_user}"
            f":{settings.info_db_password}"
            f"@{settings.info_db_host}"
            f":{settings.info_db_port}"
            f"/{settings.info_db_name}"
        )
        self._engine = create_async_engine(
            url,
            echo=False,
            pool_timeout=settings.db_pool_timeout,
            connect_args={
                "command_timeout": settings.db_query_timeout,
            },
        )
        logger.info("정보계 DB 연결 완료")

    async def disconnect(self) -> None:
        """DB 연결 종료."""
        if self._engine:
            await self._engine.dispose()

    async def health_check(self) -> bool:
        """연결 상태 확인."""
        if self._use_dummy:
            return True
        try:
            from sqlalchemy import text

            async with self._engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:
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
            logger.info(
                "Dummy 쿼리 실행", sql=query,
            )
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
                dict(zip(columns, row))
                for row in result.fetchall()
            ]
        _elapsed = (_time.perf_counter() - _start) * 1000
        logger.info(
            "정보계 DB 쿼리 실행",
            sql=query[:80],
            row_count=len(rows),
            latency_ms=round(_elapsed, 1),
        )
        return rows


class HistoryDBConnector(DatabaseConnector):
    """SQL 이력 DB 커넥터."""

    def __init__(self, use_dummy: bool = True) -> None:
        self._use_dummy = use_dummy
        self._engine: Any = None

    async def connect(self) -> None:
        """DB 연결 초기화."""
        if self._use_dummy:
            logger.info("이력 DB Dummy 모드로 초기화")
            return

        from sqlalchemy.ext.asyncio import (
            create_async_engine,
        )

        url = (
            f"postgresql+asyncpg://"
            f"{settings.history_db_user}"
            f":{settings.history_db_password}"
            f"@{settings.history_db_host}"
            f":{settings.history_db_port}"
            f"/{settings.history_db_name}"
        )
        self._engine = create_async_engine(
            url,
            echo=False,
            pool_timeout=settings.db_pool_timeout,
            connect_args={
                "command_timeout": settings.db_query_timeout,
            },
        )

    async def disconnect(self) -> None:
        """DB 연결 종료."""
        if self._engine:
            await self._engine.dispose()

    async def health_check(self) -> bool:
        """연결 상태 확인."""
        if self._use_dummy:
            return True
        try:
            from sqlalchemy import text

            async with self._engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    async def execute_query(
        self,
        query: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """쿼리 실행."""
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
                dict(zip(columns, row))
                for row in result.fetchall()
            ]

    async def search_similar_sql(
        self, query_text: str,
    ) -> list[dict[str, Any]]:
        """유사한 과거 SQL을 검색한다."""
        if self._use_dummy:
            return search_dummy_sql_history(query_text)

        import time as _time

        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import AsyncSession

        from src.utils.resource_loader import (
            load_sql_template,
        )

        keywords = [
            w for w in query_text.split() if len(w) >= 2
        ]
        if not keywords:
            return []

        conditions = " OR ".join(
            f"query_text ILIKE :kw{i}"
            for i in range(len(keywords))
        )
        params = {
            f"kw{i}": f"%{kw}%"
            for i, kw in enumerate(keywords)
        }
        template = load_sql_template(
            "queries/search_similar_sql.sql",
        )
        sql = template.format(conditions=conditions)
        _start = _time.perf_counter()
        async with AsyncSession(self._engine) as session:
            result = await session.execute(
                text(sql), params,
            )
            columns = list(result.keys())
            rows = [
                dict(zip(columns, row))
                for row in result.fetchall()
            ]
        _elapsed = (_time.perf_counter() - _start) * 1000
        logger.info(
            "이력DB 유사 SQL 검색",
            query=query_text[:60],
            count=len(rows),
            latency_ms=round(_elapsed, 1),
        )
        return rows
