"""MongoDB 커넥터 — 테이블 메타·코드 메타·용어사전 조회 게이트웨이.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

5개의 MongoDB 컬렉션에 대한 조회를 통합 제공한다:
  - dpasset_table + dpasset_column: 테이블/컬럼 메타 (aggregation $lookup)
  - standard_code + standard_code_value: 코드값 정의 (aggregation $lookup)
  - biz_term: 업무 용어사전 (dpasset_table과 $lookup)

파이프라인 템플릿: resources/connectors/mongo/pipeline_*.json 에서 로드.
$match 단계는 입력 조건에 따라 코드에서 동적으로 구성한다.

한글 검색: MongoDB 기본 텍스트 인덱스는 한글 형태소 분석을 지원하지 않으므로
$regex 기반 부분 매칭을 사용한다. 테이블 메타 규모(수천 건 이내)에서 성능 문제 없음.

Dummy 모드: use_dummy=True(기본값)일 때 MongoDB 연결 없이 dummy_data 모듈의
샘플 데이터를 키워드 매칭으로 반환한다.
"""

from __future__ import annotations

import re as _re
import time as _time
from typing import Any

from src.config import settings
from src.connectors.dummy_data import (
    search_dummy_code_meta,
    search_dummy_table_meta,
)
from src.connectors.interfaces import SearchConnector
from src.utils.logger import get_logger
from src.utils.resource_loader import load_mongo_pipeline
from src.utils.truncate import truncate_log

logger = get_logger(__name__)

# ── MongoDB 연산자 상수 ──
_MATCH = "$match"
_SORT = "$sort"
_SKIP = "$skip"
_LIMIT = "$limit"

# ── 파이프라인 템플릿 로드 (모듈 초기화 시 1회) ──
_TPL_TABLE_META = load_mongo_pipeline(
    "connectors/mongo/pipeline_table_meta.json",
)
_TPL_CODE_META = load_mongo_pipeline(
    "connectors/mongo/pipeline_code_meta.json",
)
_TPL_BIZ_TERM = load_mongo_pipeline(
    "connectors/mongo/pipeline_biz_term.json",
)


def _split_keywords(query: str) -> list[str]:
    """검색 쿼리를 개별 키워드로 분리한다."""
    return [
        kw.strip() for kw in _re.split(r"[\s,]+", query)
        if kw.strip() and len(kw.strip()) >= 2
    ] or [query.strip()]


def _build_regex_match(
    query: str,
    fields: list[str],
) -> dict[str, Any]:
    """검색 키워드를 $regex 기반 $or 조건으로 변환한다.

    MongoDB 기본 텍스트 인덱스는 한글 형태소 분석을 지원하지 않으므로
    $regex로 부분 문자열 매칭을 수행한다.

    키워드 분리 → 각 키워드를 각 필드에 대해 $regex 매칭 → $or로 결합.
    """
    keywords = _split_keywords(query)

    or_conditions: list[dict[str, Any]] = []
    for kw in keywords:
        escaped = _re.escape(kw)
        for field in fields:
            or_conditions.append(
                {field: {"$regex": escaped, "$options": "i"}}
            )

    if len(or_conditions) == 1:
        return or_conditions[0]
    return {"$or": or_conditions}


def _build_keyword_score_stages(
    query: str,
    fields: list[str],
) -> list[dict[str, Any]]:
    """키워드 매칭 개수로 스코어링하는 aggregation 스테이지를 반환한다.

    "여신 지점"으로 검색 시 "여신"과 "지점" 모두 포함하는 테이블이
    하나만 포함하는 테이블보다 상위에 정렬된다.

    반환: [$addFields, $sort] 스테이지 리스트
    """
    keywords = _split_keywords(query)
    if len(keywords) <= 1:
        return []

    # 각 키워드가 어느 필드에든 매칭되면 1점
    score_terms: list[dict[str, Any]] = []
    for kw in keywords:
        escaped = _re.escape(kw)
        field_checks = [
            {"$regexMatch": {"input": {"$ifNull": [f"${f}", ""]}, "regex": escaped, "options": "i"}}
            for f in fields
        ]
        # 이 키워드가 어느 필드에든 매칭되면 1
        score_terms.append(
            {"$cond": [{"$or": field_checks}, 1, 0]}
        )

    return [
        {"$addFields": {"_keyword_score": {"$add": score_terms}}},
        {"$sort": {"_keyword_score": -1}},
    ]


def _resolve_collection_ref(
    stage: dict[str, Any], placeholder: str, actual: str,
) -> dict[str, Any]:
    """$lookup 등에 포함된 ${collection} 플레이스홀더를 실제 컬렉션명으로 치환."""
    import json
    raw = json.dumps(stage)
    replaced = raw.replace(placeholder, actual)
    return json.loads(replaced)


class MongoConnector(SearchConnector):
    """MongoDB 커넥터 (Dummy 모드 지원)."""

    def __init__(self, use_dummy: bool = True) -> None:
        self._use_dummy = use_dummy
        self._client: Any = None
        self._db: Any = None

    async def connect(self) -> None:
        """MongoDB 연결을 초기화한다."""
        if self._use_dummy:
            logger.info("MongoDB Dummy 모드로 초기화")
            return

        from urllib.parse import quote_plus

        from motor.motor_asyncio import AsyncIOMotorClient

        connection_uri = (
            f"mongodb://{quote_plus(settings.mongo_user)}:{quote_plus(settings.mongo_password)}"
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
        """MongoDB 연결을 종료한다."""
        if self._client:
            self._client.close()
            logger.info("MongoDB 연결 종료")

    async def health_check(self) -> bool:
        """연결 상태를 확인한다."""
        if self._use_dummy:
            return True
        try:
            await self._client.admin.command("ping")
            return True
        except Exception as e:
            logger.debug("health_check 실패", error=str(e))
            return False

    async def search(
        self, query: str, **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """search_type 파라미터에 따라 적절한 검색 메서드로 디스패치한다."""
        search_type = kwargs.get("search_type", "table_meta")
        if search_type == "table_meta":
            return await self.search_table_meta(query, **kwargs)
        elif search_type == "code_meta":
            return await self.search_code_meta(query, **kwargs)
        elif search_type == "biz_term":
            return await self.search_biz_terms(query, **kwargs)
        return []

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 테이블 메타 검색 (dpasset_table + dpasset_column $lookup)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def search_table_meta(
        self, query: str, **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """테이블 메타 정보를 검색한다.

        반환 형식:
          [{
            "name": str,           # 테이블명 (신규)
            "alt_name": str,       # 한글 테이블명 (신규)
            "description": str,    # 테이블 설명 (신규)
            "schema_name": str,
            "columns": [{"name", "alt_name", "type", "description", "is_pk"}]
          }]
          ※ 하위 호환: table_name, table_description 키도 병존 제공됨.

        """
        if self._use_dummy:
            return search_dummy_table_meta(query)

        table_names: list[str] = kwargs.get("table_names", [])
        limit = kwargs.get("limit", settings.mongo_table_meta_size)
        page: int = kwargs.get("page", 1)
        skip = (page - 1) * limit
        _start = _time.perf_counter()

        if table_names:
            match_stage = {_MATCH: {"name": {"$in": table_names}}}
        else:
            match_stage = {_MATCH: _build_regex_match(
                query, fields=["alt_name", "desc", "name"],
            )}

        lookup = _resolve_collection_ref(
            _TPL_TABLE_META["lookup"],
            "${column_meta_collection}",
            settings.mongo_column_meta_collection,
        )

        # 키워드 매칭 수 기반 스코어링 (복수 키워드 시 관련성 순 정렬)
        score_stages = _build_keyword_score_stages(
            query, fields=["alt_name", "desc", "name"],
        ) if not table_names else []

        # $sort 안정화: _id tiebreaker 추가 (SERVER-51498 대응)
        # 순서 핵심: sort → skip → limit → project
        # $project에서 _id: 0으로 제거하므로, 정렬/페이징은 반드시 project 이전에 수행
        sort_stage = (
            {_SORT: {"_keyword_score": -1, "_id": 1}}
            if score_stages
            else {_SORT: {"_id": 1}}
        )

        pipeline = [
            match_stage,
            *score_stages,
            lookup,
            sort_stage,
            {_SKIP: skip},
            {_LIMIT: limit},
            _TPL_TABLE_META["project"],
        ]

        collection = self._db[settings.mongo_table_meta_collection]
        results = await collection.aggregate(pipeline).to_list(length=None)
        _elapsed = (_time.perf_counter() - _start) * 1000

        logger.info(
            "MongoDB 테이블 메타 검색",
            query=truncate_log(query) if not table_names else str(table_names),
            count=len(results),
            latency_ms=round(_elapsed, 1),
        )
        return results

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 코드 메타 검색 (standard_code + standard_code_value $lookup)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def search_code_meta(
        self, query: str, **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """코드 메타 정보를 검색한다.

        반환 형식:
          [{"code_field": "STATUS_CD", "codes": {"01": "정상", "02": "휴면"}}]
        """
        if self._use_dummy:
            return search_dummy_code_meta(query)

        code_names: list[str] = kwargs.get("code_names", [])
        limit = kwargs.get("limit", settings.mongo_code_meta_size)
        page: int = kwargs.get("page", 1)
        skip = (page - 1) * limit
        _start = _time.perf_counter()

        if code_names:
            match_stage = {_MATCH: {"name": {"$in": code_names}}}
        else:
            match_stage = {_MATCH: {}}

        lookup = _resolve_collection_ref(
            _TPL_CODE_META["lookup"],
            "${code_value_collection}",
            settings.mongo_code_value_collection,
        )

        pipeline = [
            match_stage,
            lookup,
            {_SORT: {"_id": 1}},
            {_SKIP: skip},
            {_LIMIT: limit},
            _TPL_CODE_META["project"],
        ]

        collection = self._db[settings.mongo_code_meta_collection]
        raw_results = await collection.aggregate(pipeline).to_list(length=None)
        _elapsed = (_time.perf_counter() - _start) * 1000

        results: list[dict[str, Any]] = []
        for item in raw_results:
            codes = {
                cv["code_value"]: cv["code_name"]
                for cv in item.get("code_values", [])
            }
            results.append({
                "code_field": item.get("code_field", ""),
                "code_field_desc": item.get("code_field_desc", ""),
                "codes": codes,
            })

        logger.info(
            "MongoDB 코드 메타 검색",
            query=truncate_log(query) if not code_names else str(code_names),
            count=len(results),
            latency_ms=round(_elapsed, 1),
        )
        return results

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 비즈니스 용어 검색 (biz_term + dpasset_table $lookup)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def search_biz_terms(
        self, query: str, **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """비즈니스 용어사전을 검색한다.

        반환 형식:
          [{"name", "synonyms", "biz_term_definition", "table_name", "table_description"}]
        """
        if self._use_dummy:
            return search_dummy_table_meta(query)  # 임시 fallback

        term_names: list[str] = kwargs.get("term_names", [])
        limit = kwargs.get("limit", settings.mongo_biz_term_size)
        page: int = kwargs.get("page", 1)
        skip = (page - 1) * limit
        _start = _time.perf_counter()

        if term_names:
            match_stage = {_MATCH: {"name": {"$in": term_names}}}
        else:
            match_stage = {_MATCH: _build_regex_match(
                query, fields=["name", "biz_term_definition"],
            )}

        lookup = _resolve_collection_ref(
            _TPL_BIZ_TERM["lookup"],
            "${table_meta_collection}",
            settings.mongo_table_meta_collection,
        )

        pipeline = [
            match_stage,
            lookup,
            _TPL_BIZ_TERM["unwind"],
            {_SORT: {"_id": 1, "name": 1}},
            {_SKIP: skip},
            {_LIMIT: limit},
            _TPL_BIZ_TERM["project"],
        ]

        collection = self._db[settings.mongo_biz_term_collection]
        results = await collection.aggregate(pipeline).to_list(length=None)
        _elapsed = (_time.perf_counter() - _start) * 1000

        logger.info(
            "MongoDB 비즈니스 용어 검색",
            query=truncate_log(query) if not term_names else str(term_names),
            count=len(results),
            latency_ms=round(_elapsed, 1),
        )
        return results
