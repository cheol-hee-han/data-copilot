"""ADW 커넥터 — SAP Sybase IQ 16.1 기반 ADW 업무 시스템 읽기 전용 쿼리 실행.

작성자: 한철희 / 최종수정: 2026-04-11

두 가지 연결 방식을 settings.sybase_driver 설정으로 전환할 수 있다.

1. native (기본값): sqlanydb — SAP 공식 Python DB-API 2.0 드라이버.
   순수 Python + ctypes 구조로 ODBC 설정 없이 네이티브 라이브러리
   (libdbcapi_r.so / dbcapi.dll)만 있으면 동작한다.

2. odbc: pyodbc — ODBC 프로토콜 기반.
   unixODBC + SAP Sybase IQ ODBC 드라이버(libdbodbc16_r.so) 설치 및
   odbcinst.ini 등록이 필요하다.

두 방식 모두 동기 드라이버이므로 asyncio.to_thread()로 래핑하여
기존 async 파이프라인과 일관된 인터페이스를 유지한다.

핵심 함수/클래스:
    - ADWConnector: DatabaseConnector 구현체, 읽기 전용 쿼리 실행
    - _create_native_connection: sqlanydb 연결 생성
    - _create_odbc_connection: pyodbc ODBC 연결 생성
    - SELECT/WITH 문만 허용 (정규식 사전 검증)

Dummy 모드: use_dummy=True(기본값)일 때 Sybase IQ 연결 없이
dummy_data 모듈의 샘플 데이터로 동작한다.

드라이버 설정 필드(settings.sybase_*)는 드라이버/물리 축이므로 그대로 유지한다.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from src.config import settings
from src.connectors.interfaces import DatabaseConnector, sanitize_row
from src.connectors.dummy_data import generate_dummy_data
from src.utils.logger import get_logger

logger = get_logger(__name__)

_VALID_DRIVERS = ("native", "odbc")


class ADWConnector(DatabaseConnector):
    """ADW 업무 시스템 커넥터 — SAP Sybase IQ 16.1 드라이버 기반 (읽기 전용).

    settings.sybase_driver로 연결 방식을 선택한다.
      - "native": sqlanydb (ODBC 불필요, libdbcapi 필요)
      - "odbc":   pyodbc   (unixODBC + ODBC 드라이버 필요)
    """

    @property
    def dialect(self) -> str:
        """SQL 방언 식별자."""
        return "tsql"

    @property
    def default_schema(self) -> str:
        """기본 스키마 접두사."""
        return "ADWOWN"

    def __init__(self, use_dummy: bool = True) -> None:
        self._use_dummy = use_dummy
        self._conn: Any = None
        self._driver = settings.sybase_driver.lower()
        if self._driver not in _VALID_DRIVERS:
            raise ValueError(
                f"sybase_driver는 {_VALID_DRIVERS} 중 하나여야 합니다: "
                f"'{self._driver}'"
            )

    # ──────────────────────────────────────────────
    # 연결 생성 (방식별 분기)
    # ──────────────────────────────────────────────

    @staticmethod
    def _create_native_connection() -> Any:
        """sqlanydb(네이티브) 연결을 생성한다."""
        import sqlanydb

        return sqlanydb.connect(
            host=settings.sybase_host,
            port=str(settings.sybase_port),
            userid=settings.sybase_user,
            password=settings.sybase_password,
            dbn=settings.sybase_database,
            charset=settings.sybase_charset,
        )

    @staticmethod
    def _create_odbc_connection() -> Any:
        """pyodbc(ODBC) 연결을 생성한다."""
        import pyodbc

        conn_str = (
            f"DRIVER={{{settings.sybase_odbc_driver}}};"
            f"HOST={settings.sybase_host};"
            f"PORT={settings.sybase_port};"
            f"DatabaseName={settings.sybase_database};"
            f"UID={settings.sybase_user};"
            f"PWD={settings.sybase_password};"
            f"CHARSET={settings.sybase_charset};"
        )
        return pyodbc.connect(
            conn_str,
            timeout=settings.sybase_query_timeout,
            autocommit=True,
        )

    # ──────────────────────────────────────────────
    # 라이프사이클
    # ──────────────────────────────────────────────

    async def connect(self) -> None:
        """Sybase IQ 연결을 초기화한다."""
        if self._use_dummy:
            logger.info("ADW(Sybase IQ) Dummy 모드로 초기화")
            return

        if self._driver == "native":
            self._conn = await asyncio.to_thread(
                self._create_native_connection,
            )
        else:
            self._conn = await asyncio.to_thread(
                self._create_odbc_connection,
            )

        logger.info(
            "ADW(Sybase IQ) 연결 완료",
            driver=self._driver,
            host=settings.sybase_host,
            port=settings.sybase_port,
        )

    async def disconnect(self) -> None:
        """Sybase IQ 연결을 종료한다."""
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

    # ──────────────────────────────────────────────
    # 쿼리 실행
    # ──────────────────────────────────────────────

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
                "ADW(Sybase IQ) Dummy 쿼리 실행", sql=query,
            )
            return generate_dummy_data(query)

        import time as _time

        def _execute() -> list[dict[str, Any]]:
            cursor = self._conn.cursor()
            if params:
                cursor.execute(query, list(params.values()))
            else:
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
            "ADW(Sybase IQ) 쿼리 실행",
            driver=self._driver,
            sql=query[:80],
            row_count=len(rows),
            latency_ms=round(_elapsed, 1),
        )
        return rows
