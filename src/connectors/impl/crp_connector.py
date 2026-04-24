"""CRP 커넥터 — Oracle 19c/21c 기반 CRP 업무 시스템 읽기 전용 쿼리 실행.

작성자: 한철희 / 최종수정: 2026-04-20 16:27:03

python-oracledb(구 cx_Oracle의 공식 후속 드라이버)를 사용한다.
thin mode 기본, thick mode는 Instant Client 설치 시 settings.oracle_thick_mode=True로 전환.

비동기: python-oracledb 2.x의 `oracledb.create_pool_async`로 비동기 커넥션 풀을
생성하여 asyncio 네이티브로 동작한다. 동기 to_thread 래핑 불필요.

핵심 함수/클래스:
    - CRPConnector: DatabaseConnector 구현체, 읽기 전용 쿼리 실행
    - SELECT/WITH 문만 허용 (정규식 사전 검증)

Dummy 모드: use_dummy=True(기본값)일 때 Oracle 연결 없이
dummy_data 모듈의 샘플 데이터로 동작한다.

드라이버 설정 필드(settings.oracle_*)는 드라이버/물리 축이므로 그대로 유지한다.
"""

from __future__ import annotations

import re
import time as _time
from typing import Any

from src.config import settings
from src.connectors.dummy_data import generate_dummy_data
from src.connectors.interfaces import DatabaseConnector, sanitize_row
from src.utils.logger import get_logger
from src.utils.truncate import truncate_log

logger = get_logger(__name__)


class CRPConnector(DatabaseConnector):
    """CRP 업무 시스템 커넥터 — Oracle 19c/21c 드라이버 기반 (읽기 전용).

    python-oracledb 2.x의 async 인터페이스를 사용한다.
    thin mode에서는 Instant Client 없이 순수 Python으로 동작하며,
    thick mode는 settings.oracle_thick_mode=True로 전환(대규모 LOB 등 특수 기능용).
    """

    @property
    def dialect(self) -> str:
        """SQL 방언 식별자."""
        return "oracle"

    @property
    def default_schema(self) -> str:
        """기본 스키마 접두사 (settings 참조)."""
        return settings.oracle_default_schema or ""

    def __init__(self, use_dummy: bool = True) -> None:
        self._use_dummy = use_dummy
        self._pool: Any = None

    async def connect(self) -> None:
        """Oracle 연결 풀을 초기화한다."""
        if self._use_dummy:
            logger.info("CRP(Oracle) Dummy 모드로 초기화")
            return

        import oracledb

        if settings.oracle_thick_mode:
            oracledb.init_oracle_client()

        dsn = oracledb.makedsn(
            settings.oracle_host,
            settings.oracle_port,
            service_name=settings.oracle_service_name,
        )
        pool = oracledb.create_pool_async(
            user=settings.oracle_user,
            password=settings.oracle_password,
            dsn=dsn,
            min=1,
            max=settings.db_pool_size,
            increment=1,
        )

        # 실접속 검증 — 풀에서 커넥션을 꺼내 TCP 핸드셰이크를 수행
        try:
            async with pool.acquire() as conn:
                with conn.cursor() as cursor:
                    await cursor.execute("SELECT 1 FROM DUAL")
                    await cursor.fetchall()
        except Exception:
            await pool.close()
            raise

        self._pool = pool
        logger.info(
            "CRP(Oracle) 연결 완료",
            host=settings.oracle_host,
            port=settings.oracle_port,
            service_name=settings.oracle_service_name,
        )

    async def disconnect(self) -> None:
        """Oracle 연결 풀을 종료한다."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def health_check(self) -> bool:
        """연결 상태를 확인한다."""
        if self._use_dummy:
            return True
        if self._pool is None:
            return False
        try:
            async with self._pool.acquire() as conn:
                with conn.cursor() as cursor:
                    await cursor.execute("SELECT 1 FROM DUAL")
                    await cursor.fetchall()
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
            logger.info("CRP(Oracle) Dummy 쿼리 실행", sql=query)
            return generate_dummy_data(query)

        if self._pool is None:
            raise RuntimeError("Oracle 연결이 초기화되지 않았습니다")

        _start = _time.perf_counter()
        async with self._pool.acquire() as conn:
            with conn.cursor() as cursor:
                cursor.call_timeout = settings.oracle_query_timeout * 1000
                if params:
                    await cursor.execute(query, params)
                else:
                    await cursor.execute(query)
                columns = [desc[0] for desc in cursor.description]
                rows_raw = await cursor.fetchall()
        rows = [
            sanitize_row(dict(zip(columns, row)))
            for row in rows_raw
        ]
        _elapsed = (_time.perf_counter() - _start) * 1000
        logger.info(
            "CRP(Oracle) 쿼리 실행",
            sql=truncate_log(query),
            row_count=len(rows),
            latency_ms=round(_elapsed, 1),
        )
        return rows
