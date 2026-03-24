"""MongoDB 커넥터 — 테이블 메타·코드 메타·비즈 메타 조회 게이트웨이.

3개의 MongoDB 컬렉션(table_meta, code_meta, biz_meta)에 대한 조회를 통합 제공한다.
search() 메서드에서 search_type 파라미터로 3가지 검색을 라우팅한다.

핵심 함수/클래스:
    - MongoConnector: SearchConnector 구현체, 3종 컬렉션 검색 통합 관리
    - search_table_meta: 테이블/컬럼 메타정보 조회
    - search_code_meta: 코드값 정의 조회
    - search_biz_meta: 비즈니스 메타(업무 규칙, 계수산출식 등) 조회

Dummy 모드: use_dummy=True(기본값)일 때 MongoDB 연결 없이 dummy_data 모듈의
샘플 데이터를 키워드 매칭으로 반환한다.
"""

from __future__ import annotations

import time as _time
from typing import Any

from src.config import settings
from src.connectors.dummy_data import (
    search_dummy_code_meta,
    search_dummy_table_meta,
)
from src.connectors.interfaces import SearchConnector
from src.utils.logger import get_logger

logger = get_logger(__name__)


class MongoConnector(SearchConnector):
    """MongoDB 커넥터 (Dummy 모드 지원)."""

    def __init__(self, use_dummy: bool = True) -> None:
        self._use_dummy = use_dummy
        self._client: Any = None
        self._db: Any = None

    async def connect(self) -> None:
        """MongoDB 연결 초기화."""
        if self._use_dummy:
            logger.info("MongoDB Dummy 모드로 초기화")
            return

        from motor.motor_asyncio import AsyncIOMotorClient

        connection_uri = (
            f"mongodb://{settings.mongo_user}:{settings.mongo_password}"
            f"@{settings.mongo_host}:{settings.mongo_port}"
            f"/{settings.mongo_database}?authSource=admin"
        )
        self._client = AsyncIOMotorClient(
            connection_uri,
            serverSelectionTimeoutMS=settings.mongo_request_timeout * 1000,
        )
        self._db = self._client[settings.mongo_database]
        logger.info("MongoDB 연결 완료", database=settings.mongo_database)

    async def disconnect(self) -> None:
        """MongoDB 연결 종료."""
        if self._client:
            self._client.close()
            logger.info("MongoDB 연결 종료")

    async def health_check(self) -> bool:
        """연결 상태 확인."""
        if self._use_dummy:
            return True
        try:
            await self._client.admin.command("ping")
            return True
        except Exception:
            return False

    async def search(
        self, query: str, **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """통합 검색."""
        search_type = kwargs.get("search_type", "table_meta")
        if search_type == "table_meta":
            return await self.search_table_meta(query, **kwargs)
        elif search_type == "code_meta":
            return await self.search_code_meta(query, **kwargs)
        elif search_type == "biz_meta":
            return await self.search_biz_meta(query, **kwargs)
        return []

    async def search_table_meta(
        self, query: str, **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """테이블 메타 정보를 검색한다."""
        if self._use_dummy:
            return search_dummy_table_meta(query)

        collection = self._db[settings.mongo_table_meta_collection]
        text_filter = {"$text": {"$search": query}}

        _start = _time.perf_counter()
        cursor = collection.find(
            text_filter,
            {"score": {"$meta": "textScore"}, "_id": 0},
        ).sort([("score", {"$meta": "textScore"})]).limit(
            kwargs.get("limit", settings.es_table_meta_size)
        )
        results = await cursor.to_list(length=None)
        _elapsed = (_time.perf_counter() - _start) * 1000

        logger.info(
            "MongoDB 테이블 메타 검색",
            query=query[:60],
            count=len(results),
            latency_ms=round(_elapsed, 1),
        )
        return results

    async def search_code_meta(
        self, query: str, **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """코드 메타 정보를 검색한다."""
        if self._use_dummy:
            return search_dummy_code_meta(query)

        collection = self._db[settings.mongo_code_meta_collection]
        text_filter = {"$text": {"$search": query}}

        _start = _time.perf_counter()
        cursor = collection.find(
            text_filter,
            {"score": {"$meta": "textScore"}, "_id": 0},
        ).sort([("score", {"$meta": "textScore"})]).limit(
            kwargs.get("limit", settings.es_code_meta_size)
        )
        results = await cursor.to_list(length=None)
        _elapsed = (_time.perf_counter() - _start) * 1000

        logger.info(
            "MongoDB 코드 메타 검색",
            query=query[:60],
            count=len(results),
            latency_ms=round(_elapsed, 1),
        )
        return results

    async def search_biz_meta(
        self, query: str, **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """비즈 메타(업무 규칙, 계수산출식 등)를 검색한다."""
        if self._use_dummy:
            # biz_meta 더미는 table_meta 더미를 재활용 (추후 전용 더미 추가)
            return search_dummy_table_meta(query)

        collection = self._db[settings.mongo_biz_meta_collection]
        text_filter = {"$text": {"$search": query}}

        _start = _time.perf_counter()
        cursor = collection.find(
            text_filter,
            {"score": {"$meta": "textScore"}, "_id": 0},
        ).sort([("score", {"$meta": "textScore"})]).limit(
            kwargs.get("limit", settings.es_table_meta_size)
        )
        results = await cursor.to_list(length=None)
        _elapsed = (_time.perf_counter() - _start) * 1000

        logger.info(
            "MongoDB 비즈 메타 검색",
            query=query[:60],
            count=len(results),
            latency_ms=round(_elapsed, 1),
        )
        return results
