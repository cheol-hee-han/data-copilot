"""Impala 커넥터 — Cloudera CDP 7.1.9 Impala 읽기 전용 쿼리 실행.

Cloudera CDP 7.1.9 환경의 Impala 데몬에 HiveServer2 Thrift 프로토콜로 연결한다.
impyla 라이브러리(impala.dbapi)를 사용하며, LDAP 인증을 기본으로 지원한다.

impyla는 동기 드라이버이므로 asyncio.to_thread()로 래핑하여
기존 async 파이프라인과 일관된 인터페이스를 유지한다.

핵심 함수/클래스:
    - ImpalaConnector: DatabaseConnector 구현체, 읽기 전용 쿼리 실행
    - SELECT/WITH 문만 허용 (정규식 사전 검증)
    - LDAP / GSSAPI(Kerberos) / NOSASL 인증 지원

Dummy 모드: use_dummy=True(기본값)일 때 Impala 연결 없이
postgres_connector와 동일한 dummy_data 모듈의 샘플 데이터로 동작한다.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from src.config import settings
from src.connectors.interfaces import DatabaseConnector
from src.connectors.dummy_data import generate_dummy_data
from src.utils.logger import get_logger
from src.utils.truncate import truncate_log

logger = get_logger(__name__)


class ImpalaConnector(DatabaseConnector):
    """Cloudera CDP 7.1.9 Impala 커넥터 (읽기 전용).

    HiveServer2 Thrift 프로토콜로 Impala 데몬에 연결한다.
    impyla(동기)를 asyncio.to_thread()로 래핑하여 async 인터페이스를 제공한다.
    """

    @property
    def dialect(self) -> str:
        return "hive"

    @property
    def default_schema(self) -> str:
        return "BDPOWN"

    def __init__(self, use_dummy: bool = True) -> None:
        self._use_dummy = use_dummy
        self._conn: Any = None

    async def connect(self) -> None:
        """Impala 연결 초기화."""
        if self._use_dummy:
            logger.info("Impala Dummy 모드로 초기화")
            return

        def _connect() -> Any:
            from impala.dbapi import connect

            conn_kwargs: dict[str, Any] = {
                "host": settings.impala_host,
                "port": settings.impala_port,
                "auth_mechanism": settings.impala_auth_mechanism,
                "database": settings.impala_database,
                "timeout": settings.impala_query_timeout,
            }
            if settings.impala_auth_mechanism in ("LDAP", "PLAIN"):
                conn_kwargs["user"] = settings.impala_user
                conn_kwargs["password"] = settings.impala_password
            if settings.impala_use_ssl:
                conn_kwargs["use_ssl"] = True
            return connect(**conn_kwargs)

        self._conn = await asyncio.to_thread(_connect)
        logger.info(
            "Impala 연결 완료",
            host=settings.impala_host,
            port=settings.impala_port,
        )

    async def disconnect(self) -> None:
        """Impala 연결 종료."""
        if self._conn:
            await asyncio.to_thread(self._conn.close)
            self._conn = None

    async def health_check(self) -> bool:
        """연결 상태 확인."""
        if self._use_dummy:
            return True
        try:
            def _ping() -> bool:
                cursor = self._conn.cursor()
                cursor.execute("SELECT 1")
                cursor.fetchall()
                cursor.close()
                return True

            return await asyncio.to_thread(_ping)
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
            logger.info("Impala Dummy 쿼리 실행", sql=query)
            return generate_dummy_data(query)

        import time as _time

        def _execute() -> list[dict[str, Any]]:
            cursor = self._conn.cursor()
            cursor.execute(query)
            columns = [
                desc[0] for desc in cursor.description
            ]
            rows = [
                dict(zip(columns, row))
                for row in cursor.fetchall()
            ]
            cursor.close()
            return rows

        _start = _time.perf_counter()
        rows = await asyncio.to_thread(_execute)
        _elapsed = (_time.perf_counter() - _start) * 1000
        logger.info(
            "Impala 쿼리 실행",
            sql=truncate_log(query),
            row_count=len(rows),
            latency_ms=round(_elapsed, 1),
        )
        return rows
