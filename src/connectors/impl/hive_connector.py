"""Hive 커넥터 — Cloudera CDP 7.1.9 Hive 3.1.3 읽기 전용 쿼리 실행.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

Cloudera CDP 7.1.9 환경의 HiveServer2에 Thrift 프로토콜로 연결한다.
Impala 커넥터와 동일하게 impyla 라이브러리를 사용하며,
HiveServer2 포트(기본 10000)로 연결하는 점이 다르다.

Hive는 Impala 대비 MapReduce/Tez 기반으로 쿼리 실행이 느리므로
기본 타임아웃을 120초로 설정한다. 대용량 배치 집계·ETL 검증 등
Impala로 처리하기 어려운 쿼리에 활용한다.

핵심 함수/클래스:
    - HiveConnector: DatabaseConnector 구현체, 읽기 전용 쿼리 실행
    - SELECT/WITH 문만 허용 (정규식 사전 검증)
    - LDAP / GSSAPI(Kerberos) / NOSASL 인증 지원

Dummy 모드: use_dummy=True(기본값)일 때 Hive 연결 없이
dummy_data 모듈의 샘플 데이터로 동작한다.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from src.config import settings
from src.connectors.interfaces import DatabaseConnector, sanitize_row
from src.connectors.dummy_data import generate_dummy_data
from src.utils.logger import get_logger
from src.utils.truncate import truncate_log

logger = get_logger(__name__)


class HiveConnector(DatabaseConnector):
    """Cloudera CDP 7.1.9 Hive 3.1.3 커넥터 (읽기 전용).

    HiveServer2 Thrift 프로토콜로 연결한다.
    impyla(동기)를 asyncio.to_thread()로 래핑하여 async 인터페이스를 제공한다.
    """

    @property
    def dialect(self) -> str:
        return "hive"

    @property
    def default_schema(self) -> str:
        return ""

    def __init__(self, use_dummy: bool = True) -> None:
        self._use_dummy = use_dummy
        self._conn: Any = None

    async def connect(self) -> None:
        """Hive 연결을 초기화한다."""
        if self._use_dummy:
            logger.info("Hive Dummy 모드로 초기화")
            return

        def _connect() -> Any:
            from impala.dbapi import connect

            conn_kwargs: dict[str, Any] = {
                "host": settings.hive_host,
                "port": settings.hive_port,
                "auth_mechanism": settings.hive_auth_mechanism,
                "database": settings.hive_database,
                "timeout": settings.hive_query_timeout,
            }
            if settings.hive_auth_mechanism in ("LDAP", "PLAIN"):
                conn_kwargs["user"] = settings.hive_user
                conn_kwargs["password"] = settings.hive_password
            if settings.hive_use_ssl:
                conn_kwargs["use_ssl"] = True
            return connect(**conn_kwargs)

        self._conn = await asyncio.to_thread(_connect)
        logger.info(
            "Hive 연결 완료",
            host=settings.hive_host,
            port=settings.hive_port,
        )

    async def disconnect(self) -> None:
        """Hive 연결을 종료한다."""
        if self._conn:
            await asyncio.to_thread(self._conn.close)
            self._conn = None

    async def health_check(self) -> bool:
        """연결 상태를 확인한다."""
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
            logger.info("Hive Dummy 쿼리 실행", sql=query)
            return generate_dummy_data(query)

        import time as _time

        def _execute() -> list[dict[str, Any]]:
            cursor = self._conn.cursor()
            cursor.execute(query)
            columns = [
                desc[0] for desc in cursor.description
            ]
            rows = [
                sanitize_row(dict(zip(columns, row)))
                for row in cursor.fetchall()
            ]
            cursor.close()
            return rows

        _start = _time.perf_counter()
        rows = await asyncio.to_thread(_execute)
        _elapsed = (_time.perf_counter() - _start) * 1000
        logger.info(
            "Hive 쿼리 실행",
            sql=truncate_log(query),
            row_count=len(rows),
            latency_ms=round(_elapsed, 1),
        )
        return rows
