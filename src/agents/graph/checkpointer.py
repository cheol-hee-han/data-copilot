"""Checkpointer 팩토리 — 전역 상태 없는 순수 함수.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

LangGraph의 체크포인터(checkpoint saver)를 설정에 따라 생성·관리한다.
체크포인터는 그래프 실행 중 상태를 영속화하여 다중턴 대화와
interrupt/resume 흐름을 가능하게 하는 핵심 인프라 컴포넌트이다.

설정 기반 백엔드 전환 전략:
    - postgres: AsyncPostgresSaver + AsyncConnectionPool (운영 환경)
    - memory: MemorySaver (테스트·개발 환경, 영속화 없음)

msgpack 직렬화 시 State에서 사용하는 커스텀 타입(Enum, Pydantic 모델 등)을
allowlist에 동적으로 등록하여 역직렬화 안정성을 보장한다.

생명주기:
    - FastAPI lifespan에서 async context manager로 사용
    - 테스트에서는 MemorySaver 직접 사용 (setup/teardown 불필요)

핵심 함수:
    - create_checkpointer: 설정 기반 체크포인터 생성 (async context manager)
    - _collect_src_types: State 커스텀 타입을 msgpack allowlist로 수집
"""

from __future__ import annotations

import importlib
import inspect
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from langgraph.checkpoint.base import BaseCheckpointSaver

from src.config import DbConnectionInfo, settings
from src.utils.logger import get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def create_checkpointer(
    history_db: DbConnectionInfo,
) -> AsyncIterator[tuple[BaseCheckpointSaver, Any]]:
    """설정에 따라 checkpointer를 생성하고 관리한다.

    AsyncContextManager — lifespan에서 async with로 사용.
    리소스 정리가 자동으로 보장된다.

    Yields:
        (checkpointer, pool) 튜플.
        postgres 백엔드: (AsyncPostgresSaver, AsyncConnectionPool)
        memory 백엔드: (MemorySaver, None)
        pool은 turn_text_store 등 커스텀 테이블 접근 시 재사용한다.
    """
    if settings.checkpointer_backend == "postgres":
        from psycopg_pool import AsyncConnectionPool
        from psycopg.rows import dict_row
        from langgraph.checkpoint.postgres.aio import (
            AsyncPostgresSaver,
        )

        connection_kwargs = {
            "autocommit": True,       # 필수: psycopg3 기본값 False
            "prepare_threshold": 0,   # PgBouncer/pooler 호환
            "row_factory": dict_row,
            "password": history_db.password,   # DSN에서 분리하여 로그 노출 방지
            "options": "-c search_path=bdptbl,public",  # checkpoint_dc_* 테이블 스키마 해석
        }

        pool = AsyncConnectionPool(
            conninfo=history_db.dsn,
            min_size=settings.checkpointer_pool_min,
            max_size=settings.checkpointer_pool_max,
            kwargs=connection_kwargs,
            open=False,
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
            host=history_db.host,
        )
        try:
            yield checkpointer, pool
        finally:
            await pool.close()
            logger.info("Checkpointer 리소스 정리 완료")
    else:
        from langgraph.checkpoint.memory import MemorySaver

        logger.info("Checkpointer 초기화: MemorySaver")
        yield MemorySaver(), None


# ── msgpack allowlist 동적 수집 ──────────────────────────

_ALLOWLIST_MODULES = (
    "src.models.enums",
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
