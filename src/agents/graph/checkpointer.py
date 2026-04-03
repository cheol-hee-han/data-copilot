"""Checkpointer 팩토리 — 전역 상태 없는 순수 함수.

생명주기:
    - FastAPI lifespan에서 async context manager로 사용
    - 테스트에서는 MemorySaver 직접 사용 (setup/teardown 불필요)
"""

from __future__ import annotations

import importlib
import inspect
from contextlib import asynccontextmanager
from typing import AsyncIterator

from langgraph.checkpoint.base import BaseCheckpointSaver

from src.config import CheckpointerConfig, DbConnectionInfo
from src.utils.logger import get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def create_checkpointer(
    config: CheckpointerConfig,
    history_db: DbConnectionInfo,
) -> AsyncIterator[BaseCheckpointSaver]:
    """설정에 따라 checkpointer를 생성하고 관리한다.

    AsyncContextManager — lifespan에서 async with로 사용.
    리소스 정리가 자동으로 보장된다.
    """
    if config.backend == "postgres":
        from psycopg_pool import AsyncConnectionPool
        from psycopg.rows import dict_row
        from langgraph.checkpoint.postgres.aio import (
            AsyncPostgresSaver,
        )

        db = config.resolve_db(history_db)

        connection_kwargs = {
            "autocommit": True,       # 필수: psycopg3 기본값 False
            "prepare_threshold": 0,   # PgBouncer/pooler 호환
            "row_factory": dict_row,
            "password": db.password,   # DSN에서 분리하여 로그 노출 방지
        }

        pool = AsyncConnectionPool(
            conninfo=db.dsn,
            min_size=config.pool_min,
            max_size=config.pool_max,
            kwargs=connection_kwargs,
        )
        await pool.open()
        await pool.wait()

        from langgraph.checkpoint.serde.jsonplus import (
            JsonPlusSerializer,
        )

        # 생성자에서 allowed_msgpack_modules를 명시하면
        # 기본값 True(전체 허용+경고)를 대체하여 경고 없이 동작한다.
        # SAFE_MSGPACK_TYPES(Python 빌트인+langchain)는 별도로 항상 허용됨.
        serde = JsonPlusSerializer(
            allowed_msgpack_modules=_collect_src_types(),
        )

        checkpointer = AsyncPostgresSaver(pool, serde=serde)
        await checkpointer.setup()

        logger.info(
            "Checkpointer 초기화: AsyncPostgresSaver",
            host=db.host,
        )
        try:
            yield checkpointer
        finally:
            await pool.close()
            logger.info("Checkpointer 리소스 정리 완료")
    else:
        from langgraph.checkpoint.memory import MemorySaver

        logger.info("Checkpointer 초기화: MemorySaver")
        yield MemorySaver()


# ── msgpack allowlist 동적 수집 ──────────────────────────

_ALLOWLIST_MODULES = (
    "src.models.enums",
    "src.models.context",
    "src.models.result",
    "src.models.trace",
    "src.agents.state.state",
    "src.agents.models.normalization",
    "src.agents.models.clarification",
    "src.agents.models.response",
)


def _collect_src_types() -> list[tuple[str, str]]:
    """State에서 사용하는 커스텀 타입을 (module, classname) 쌍으로 수집한다."""
    result: list[tuple[str, str]] = []
    for mod_name in _ALLOWLIST_MODULES:
        try:
            mod = importlib.import_module(mod_name)
        except ImportError:
            continue
        for name, obj in inspect.getmembers(mod, inspect.isclass):
            if obj.__module__ == mod_name:
                result.append((mod_name, name))
    return result
