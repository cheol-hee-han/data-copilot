"""Neo4j 온톨로지 그래프 커넥터 — 테이블 관계/업무 규칙/JOIN 경로 탐색.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

5종의 그래프 탐색을 제공한다:
  - search_join_paths: 두 테이블 간 최단 JOIN 경로
  - search_domain_tables: 도메인 개념 → 관련 테이블 + FK 이웃 확장
  - search_formula: 계수산출식 재귀 분해 (컬럼/테이블 매핑)
  - search_code_hierarchy: 코드값 계층 + 적용 컬럼/테이블
  - search_table_relations: 특정 테이블의 직접 연결 관계

Cypher 쿼리: resources/connectors/neo4j/cypher_*.cypher 에서 로드.
Dummy 모드: use_dummy=True일 때 Neo4j 연결 없이 샘플 온톨로지 데이터를 반환한다.
"""

from __future__ import annotations

import time as _time
from typing import Any

from src.config import settings
from src.connectors.interfaces import SearchConnector
from src.models.enums import ConfidenceStatus
from src.utils.logger import get_logger
from src.utils.resource_loader import load_cypher

logger = get_logger(__name__)

# ── Cypher 쿼리 로드 (모듈 초기화 시 1회, // 주석 제거됨) ──
_CYPHER_DOMAIN_TABLES = load_cypher(
    "connectors/neo4j/cypher_domain_tables.cypher",
)
_CYPHER_FORMULA = load_cypher(
    "connectors/neo4j/cypher_formula.cypher",
)
_CYPHER_CODE_HIERARCHY = load_cypher(
    "connectors/neo4j/cypher_code_hierarchy.cypher",
)
_CYPHER_TABLE_RELATIONS = load_cypher(
    "connectors/neo4j/cypher_table_relations.cypher",
)
# join_paths는 {max_hops} 동적 치환이 필요하므로 호출 시 로드


class Neo4jConnector(SearchConnector):
    """Neo4j 온톨로지 그래프 커넥터 (Dummy 모드 지원)."""

    def __init__(self, use_dummy: bool = True) -> None:
        self._use_dummy = use_dummy
        self._driver: Any = None
        self._cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}

    async def connect(self) -> None:
        """Neo4j 연결을 초기화한다."""
        if self._use_dummy:
            logger.info("Neo4j Dummy 모드로 초기화")
            return

        from neo4j import AsyncGraphDatabase

        self._driver = AsyncGraphDatabase.driver(
            f"bolt://{settings.neo4j_host}:{settings.neo4j_port}",
            auth=(settings.neo4j_user, settings.neo4j_password),
            max_connection_pool_size=settings.neo4j_pool_size,
            connection_timeout=settings.neo4j_request_timeout,
            connection_acquisition_timeout=settings.db_pool_timeout,
        )
        logger.info("Neo4j 연결 완료")

    async def disconnect(self) -> None:
        """Neo4j 연결을 종료한다."""
        if self._driver:
            await self._driver.close()
            logger.info("Neo4j 연결 종료")

    async def health_check(self) -> bool:
        """연결 상태를 확인한다."""
        if self._use_dummy:
            return True
        if not self._driver:
            return False
        try:
            await self._driver.verify_connectivity()
            return True
        except Exception as e:
            logger.debug("health_check 실패", error=str(e))
            return False

    async def search(
        self, query: str, **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """통합 검색 — search_type으로 분기."""
        search_type = kwargs.get("search_type", "domain_tables")
        if search_type == "join_paths":
            return await self.search_join_paths(
                kwargs.get("source_table", ""),
                kwargs.get("target_table", ""),
            )
        elif search_type == "domain_tables":
            return await self.search_domain_tables(query)
        elif search_type == "formula":
            return await self.search_formula(query)
        elif search_type == "code_hierarchy":
            return await self.search_code_hierarchy(query)
        elif search_type == "table_relations":
            return await self.search_table_relations(query)
        return []

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 1. JOIN 경로 탐색
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def search_join_paths(
        self,
        source_table: str,
        target_table: str,
        max_hops: int | None = None,
    ) -> list[dict[str, Any]]:
        """두 테이블 간 최단 JOIN 경로를 탐색한다."""
        if self._use_dummy:
            return _dummy_join_paths(source_table, target_table)

        raw = max_hops if isinstance(max_hops, int) else None
        hops = min(
            raw or settings.neo4j_max_path_hops,
            settings.neo4j_max_path_hops,
        )
        hops = max(1, min(hops, 10))

        cypher = load_cypher(
            "connectors/neo4j/cypher_join_paths.cypher",
            max_hops=str(hops),
        )

        return await self._execute_cypher_cached(
            f"join:{source_table}:{target_table}",
            cypher,
            {"source": source_table, "target": target_table},
        )

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 2. 도메인 개념 → 관련 테이블 확장
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def search_domain_tables(
        self, concept: str,
    ) -> list[dict[str, Any]]:
        """도메인 개념에서 관련 테이블 + FK 이웃을 확장 탐색한다."""
        if self._use_dummy:
            return _dummy_domain_tables(concept)

        return await self._execute_cypher_cached(
            f"domain:{concept}",
            _CYPHER_DOMAIN_TABLES,
            {"concept": concept},
        )

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 3. 계수산출식 재귀 분해
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def search_formula(
        self, formula_name: str,
    ) -> list[dict[str, Any]]:
        """계수산출식을 재귀 분해하여 컬럼/테이블까지 매핑한다."""
        if self._use_dummy:
            return _dummy_formula(formula_name)

        return await self._execute_cypher_cached(
            f"formula:{formula_name}",
            _CYPHER_FORMULA,
            {"name": formula_name},
        )

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 4. 코드값 계층 + 적용 컬럼/테이블
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def search_code_hierarchy(
        self, code_name: str,
    ) -> list[dict[str, Any]]:
        """코드값 계층 + 적용 컬럼/테이블을 조회한다."""
        if self._use_dummy:
            return _dummy_code_hierarchy(code_name)

        return await self._execute_cypher_cached(
            f"code:{code_name}",
            _CYPHER_CODE_HIERARCHY,
            {"code_name": code_name},
        )

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 5. 테이블 직접 관계 조회
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def search_table_relations(
        self, table_name: str,
    ) -> list[dict[str, Any]]:
        """특정 테이블의 직접 연결 관계(FK, 주제영역)를 조회한다."""
        if self._use_dummy:
            return _dummy_table_relations(table_name)

        return await self._execute_cypher_cached(
            f"rel:{table_name}",
            _CYPHER_TABLE_RELATIONS,
            {"name": table_name},
        )

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 캐시 관리
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def invalidate_cache(self) -> None:
        """캐시 전체 무효화 (온톨로지 업데이트 후 호출)."""
        self._cache.clear()

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 내부 헬퍼
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def _execute_cypher(
        self, cypher: str, params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Cypher 쿼리를 실행한다."""
        import neo4j as _neo4j

        _start = _time.perf_counter()
        try:
            async with self._driver.session(
                database=settings.neo4j_database,
                default_access_mode=_neo4j.READ_ACCESS,
            ) as session:
                result = await session.run(
                    cypher, params or {},
                    timeout=settings.neo4j_request_timeout,
                )
                records = [dict(record) async for record in result]
        except Exception as e:
            logger.error("Neo4j Cypher 실행 오류", error=str(e))
            return []

        _elapsed = (_time.perf_counter() - _start) * 1000
        logger.info(
            "Neo4j Cypher 실행",
            records=len(records),
            latency_ms=round(_elapsed, 1),
        )
        return records

    async def _execute_cypher_cached(
        self,
        cache_key: str,
        cypher: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """TTL 캐시 적용 Cypher 실행."""
        now = _time.time()
        if cache_key in self._cache:
            ts, data = self._cache[cache_key]
            if now - ts < settings.neo4j_cache_ttl:
                return data

        result = await self._execute_cypher(cypher, params)
        self._cache[cache_key] = (now, result)
        return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Dummy 데이터 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _dummy_join_paths(
    source: str, target: str,
) -> list[dict[str, Any]]:
    """Dummy JOIN 경로."""
    return [{
        "tables": [source, target],
        "joins": [{
            "from_col": "CUST_NO",
            "to_col": "CUST_NO",
            "join_type": "INNER",
            "confidence": ConfidenceStatus.CONFIRMED.value,
        }],
    }]


def _dummy_domain_tables(concept: str) -> list[dict[str, Any]]:
    """Dummy 도메인 테이블."""
    if "여신" in concept or "대출" in concept:
        return [{
            "table_name": "TB_LN_BAL_D",
            "alt_name": "여신잔액일별",
            "granularity": "일별",
            "refresh_cycle": "D+1 배치",
            "role": "PRIMARY",
            "joinable_tables": ["TB_CUST_INFO", "TB_BRANCH_M"],
        }]
    if "수신" in concept or "예금" in concept:
        return [{
            "table_name": "TB_DP_BAL_D",
            "alt_name": "수신잔액일별",
            "granularity": "일별",
            "refresh_cycle": "D+1 배치",
            "role": "PRIMARY",
            "joinable_tables": ["TB_CUST_INFO", "TB_BRANCH_M"],
        }]
    return []


def _dummy_formula(name: str) -> list[dict[str, Any]]:
    """Dummy 계수산출식."""
    if "연체율" in name:
        return [{
            "formula_name": "연체율",
            "formula_text": "연체원금 합계 / 여신잔액 합계 × 100",
            "components": [
                {
                    "component": "연체원금",
                    "column": "OVRD_PRINC_AMT",
                    "table": "TB_LN_BAL_D",
                    "agg_function": "SUM",
                    "position": "NUMERATOR",
                    "operator": "DIVIDE",
                },
                {
                    "component": "여신잔액",
                    "column": "LOAN_BAL_AMT",
                    "table": "TB_LN_BAL_D",
                    "agg_function": "SUM",
                    "position": "DENOMINATOR",
                    "operator": "DIVIDE",
                },
            ],
        }]
    return []


def _dummy_code_hierarchy(code_name: str) -> list[dict[str, Any]]:
    """Dummy 코드 계층."""
    if "연체" in code_name or "정상" in code_name:
        return [{
            "code_field": "STAT_CD",
            "code_value": "01",
            "code_name": "정상",
            "column_name": "STAT_CD",
            "table_name": "TB_LN_BAL_D",
            "children": [
                {"value": "01", "name": "정상"},
                {"value": "02", "name": "요주의"},
                {"value": "03", "name": "연체"},
            ],
        }]
    return []


def _dummy_table_relations(table_name: str) -> list[dict[str, Any]]:
    """Dummy 테이블 관계."""
    return [
        {
            "rel_type": "FK_TO",
            "neighbor_label": "Table",
            "neighbor_name": "TB_CUST_INFO",
            "from_column": "CUST_NO",
            "to_column": "CUST_NO",
            "confidence": ConfidenceStatus.CONFIRMED.value,
        },
        {
            "rel_type": "IN_AREA",
            "neighbor_label": "SubjectArea",
            "neighbor_name": "여신_잔액",
            "from_column": None,
            "to_column": None,
            "confidence": None,
        },
    ]
