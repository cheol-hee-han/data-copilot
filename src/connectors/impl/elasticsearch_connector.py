"""ElasticSearch 커넥터 — 보고서 SQL 검색 전용 게이트웨이.

ES 인덱스(report_sql)에 대한 보고서 SQL/요건 검색을 제공한다.
테이블/컬럼 메타, 코드 메타 검색은 MongoDB로 일원화되었으며,
ES의 table_meta, code_meta 메서드는 하위 호환용으로 보존한다.

핵심 함수/클래스:
    - ElasticSearchConnector: SearchConnector 구현체
    - search_report_sql: 보고서 SQL 및 요건 검색 (multi_match) — 주 용도
    - search_table_meta: (하위 호환용 보존, 파이프라인에서 미사용)
    - search_code_meta: (하위 호환용 보존, 파이프라인에서 미사용)

Dummy 모드: use_dummy=True(기본값)일 때 ES 연결 없이 dummy_data 모듈의
샘플 데이터를 키워드 매칭으로 반환한다.
폐쇄망 배포 시 ES 호스트/포트/인증 정보를 settings에서 전환하면 실제 연결로 동작한다.
"""

from __future__ import annotations

from typing import Any

from src.config import settings
from src.connectors.interfaces import SearchConnector
from src.connectors.dummy_data import (
    search_dummy_code_meta,
    search_dummy_report_sql,
    search_dummy_table_meta,
)
from src.utils.logger import get_logger
from src.utils.truncate import truncate_log

logger = get_logger(__name__)


class ElasticSearchConnector(SearchConnector):
    """ElasticSearch 커넥터 (Dummy 모드 지원)."""

    def __init__(self, use_dummy: bool = True) -> None:
        self._use_dummy = use_dummy
        self._client: Any = None

    async def connect(self) -> None:
        """ES 연결 초기화."""
        if self._use_dummy:
            logger.info("ElasticSearch Dummy 모드로 초기화")
            return

        from elasticsearch import AsyncElasticsearch

        self._client = AsyncElasticsearch(
            hosts=[
                f"http://{settings.es_host}"
                f":{settings.es_port}"
            ],
            basic_auth=(
                settings.es_user,
                settings.es_password,
            ),
        )
        logger.info("ElasticSearch 연결 완료")

    async def disconnect(self) -> None:
        """ES 연결 종료."""
        if self._client:
            await self._client.close()

    async def health_check(self) -> bool:
        """연결 상태 확인."""
        if self._use_dummy:
            return True
        try:
            return await self._client.ping()
        except Exception:
            return False

    async def search(
        self, query: str, **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """통합 검색."""
        search_type = kwargs.get(
            "search_type", "table_meta",
        )
        if search_type == "table_meta":
            return await self.search_table_meta(query)
        elif search_type == "report_sql":
            return await self.search_report_sql(query)
        elif search_type == "code_meta":
            return await self.search_code_meta(query)
        return []

    async def search_table_meta(
        self, query: str,
    ) -> list[dict[str, Any]]:
        """테이블 메타 정보를 검색한다."""
        if self._use_dummy:
            return search_dummy_table_meta(query)

        import time as _time

        from src.config import settings
        from src.utils.resource_loader import load_es_query

        body = load_es_query(
            "connectors/elasticsearch/table_meta_query.json",
            query,
        )
        body["size"] = settings.es_table_meta_size

        _start = _time.perf_counter()
        resp = await self._client.search(
            index=settings.es_table_meta_index,
            body=body,
            request_timeout=settings.es_request_timeout,
        )
        results = [
            hit["_source"]
            for hit in resp["hits"]["hits"]
        ]
        _elapsed = (_time.perf_counter() - _start) * 1000
        logger.info(
            "ES 테이블 메타 검색",
            query=truncate_log(query),
            count=len(results),
            latency_ms=round(_elapsed, 1),
        )
        return results

    async def search_report_sql(
        self, query: str,
    ) -> list[dict[str, Any]]:
        """보고서 SQL을 검색한다."""
        if self._use_dummy:
            return search_dummy_report_sql(query)

        import time as _time

        from src.config import settings
        from src.utils.resource_loader import load_es_query

        body = load_es_query(
            "connectors/elasticsearch/report_sql_query.json",
            query,
        )
        body["size"] = settings.es_report_sql_size

        _start = _time.perf_counter()
        resp = await self._client.search(
            index=settings.es_report_sql_index,
            body=body,
            request_timeout=settings.es_request_timeout,
        )
        results = [
            hit["_source"]
            for hit in resp["hits"]["hits"]
        ]
        _elapsed = (_time.perf_counter() - _start) * 1000
        logger.info(
            "ES 보고서 SQL 검색",
            query=truncate_log(query),
            count=len(results),
            latency_ms=round(_elapsed, 1),
        )
        return results

    async def search_code_meta(
        self, query: str,
    ) -> list[dict[str, Any]]:
        """코드 메타 정보를 검색한다."""
        if self._use_dummy:
            return search_dummy_code_meta(query)

        import time as _time

        from src.config import settings
        from src.utils.resource_loader import load_es_query

        body = load_es_query(
            "connectors/elasticsearch/code_meta_query.json",
            query,
        )
        body["size"] = settings.es_code_meta_size

        _start = _time.perf_counter()
        resp = await self._client.search(
            index=settings.es_code_meta_index,
            body=body,
            request_timeout=settings.es_request_timeout,
        )
        results = [
            hit["_source"]
            for hit in resp["hits"]["hits"]
        ]
        _elapsed = (_time.perf_counter() - _start) * 1000
        logger.info(
            "ES 코드 메타 검색",
            query=truncate_log(query),
            count=len(results),
            latency_ms=round(_elapsed, 1),
        )
        return results
