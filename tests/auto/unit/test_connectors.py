"""커넥터 Dummy 모드(PostgreSQL, Qdrant, MongoDB, Neo4j) 단위 테스트.

테스트 대상:
    use_dummy=True 설정으로 실제 인프라 없이 각 커넥터의
    헬스체크·검색·쿼리 실행이 정상 동작하는지 검증한다.
    정보계 DB의 비-SELECT 거부(보안)도 확인한다.

실행 스크립트:
    pytest tests/auto/unit/test_connectors.py -v

참고:
    - 외부 인프라 불필요 (Dummy 모드)
    - 테스트 대상 소스: src/connectors/impl/*.py
"""

import pytest

from src.connectors.impl.mongo_connector import MongoConnector
from src.connectors.impl.neo4j_connector import Neo4jConnector
from src.connectors.impl.postgres_connector import PostgresConnector
from src.connectors.impl.test_connector import TESTConnector
from src.connectors.impl.qdrant_connector import QdrantConnector


@pytest.mark.asyncio
async def test_test_db_execute_select():
    """테스트 DB Dummy 쿼리 실행."""
    db = TESTConnector(use_dummy=True)
    await db.connect()
    rows = await db.execute_query("SELECT COUNT(*) FROM TB_CUST_INFO")
    assert len(rows) > 0


@pytest.mark.asyncio
async def test_test_db_reject_non_select():
    """테스트 DB 비-SELECT 거부."""
    db = TESTConnector(use_dummy=True)
    await db.connect()
    with pytest.raises(ValueError, match="SELECT"):
        await db.execute_query("DROP TABLE TB_CUST_INFO")


@pytest.mark.asyncio
async def test_postgres_health():
    """Postgres 공통 DB 연결 확인."""
    db = PostgresConnector(use_dummy=True)
    await db.connect()
    ok = await db.health_check()
    assert ok is True


@pytest.mark.asyncio
async def test_qdrant_search_manual():
    """Qdrant 업무 매뉴얼 검색."""
    qdrant = QdrantConnector(use_dummy=True)
    await qdrant.connect()
    results = await qdrant.search_manual("연체 관리")
    assert len(results) > 0
    assert any("연체" in r.get("title", "") for r in results)


"""MongoDB 커넥터 Dummy 테스트."""

@pytest.mark.asyncio
async def test_mongo_health_check():
    """MongoDB 커넥터 Dummy 헬스체크."""
    mongo = MongoConnector(use_dummy=True)
    await mongo.connect()
    assert await mongo.health_check()


@pytest.mark.asyncio
async def test_mongo_search_table_meta():
    """MongoDB 테이블 메타 검색 (Dummy)."""
    mongo = MongoConnector(use_dummy=True)
    await mongo.connect()
    results = await mongo.search_table_meta("고객")
    assert isinstance(results, list)
    assert len(results) > 0


@pytest.mark.asyncio
async def test_mongo_search_code_meta():
    """MongoDB 코드 메타 검색 (Dummy)."""
    mongo = MongoConnector(use_dummy=True)
    await mongo.connect()
    results = await mongo.search_code_meta("CUST_TYPE_CD")
    assert isinstance(results, list)
    assert len(results) > 0


@pytest.mark.asyncio
async def test_mongo_search_biz_terms():
    """MongoDB 비즈니스 용어 검색 (Dummy)."""
    mongo = MongoConnector(use_dummy=True)
    await mongo.connect()
    results = await mongo.search_biz_terms("여신")
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_mongo_search_dispatch():
    """MongoDB 통합 search() 라우팅 (Dummy)."""
    mongo = MongoConnector(use_dummy=True)
    await mongo.connect()
    results = await mongo.search("고객", search_type="table_meta")
    assert isinstance(results, list)


"""Neo4j 커넥터 Dummy 테스트."""

@pytest.mark.asyncio
async def test_neo4j_health_check():
    """Neo4j 커넥터 Dummy 헬스체크."""
    neo4j = Neo4jConnector(use_dummy=True)
    await neo4j.connect()
    assert await neo4j.health_check()


@pytest.mark.asyncio
async def test_neo4j_search_join_paths():
    """Neo4j JOIN 경로 탐색 (Dummy)."""
    neo4j = Neo4jConnector(use_dummy=True)
    await neo4j.connect()
    results = await neo4j.search_join_paths("TB_LN_BAL_D", "TB_CUST_INFO")
    assert len(results) > 0
    assert "tables" in results[0]
    assert "joins" in results[0]


@pytest.mark.asyncio
async def test_neo4j_search_domain_tables():
    """Neo4j 도메인 테이블 탐색 (Dummy)."""
    neo4j = Neo4jConnector(use_dummy=True)
    await neo4j.connect()
    results = await neo4j.search_domain_tables("여신")
    assert len(results) > 0
    assert results[0]["table_name"] == "TB_LN_BAL_D"
    assert "joinable_tables" in results[0]


@pytest.mark.asyncio
async def test_neo4j_search_formula():
    """Neo4j 계수산출식 분해 (Dummy)."""
    neo4j = Neo4jConnector(use_dummy=True)
    await neo4j.connect()
    results = await neo4j.search_formula("연체율")
    assert len(results) > 0
    assert results[0]["formula_name"] == "연체율"
    assert len(results[0]["components"]) == 2


@pytest.mark.asyncio
async def test_neo4j_search_code_hierarchy():
    """Neo4j 코드 계층 조회 (Dummy)."""
    neo4j = Neo4jConnector(use_dummy=True)
    await neo4j.connect()
    results = await neo4j.search_code_hierarchy("정상")
    assert len(results) > 0
    assert results[0]["code_field"] == "STAT_CD"
    assert len(results[0]["children"]) >= 2


@pytest.mark.asyncio
async def test_neo4j_search_table_relations():
    """Neo4j 테이블 관계 조회 (Dummy)."""
    neo4j = Neo4jConnector(use_dummy=True)
    await neo4j.connect()
    results = await neo4j.search_table_relations("TB_LN_BAL_D")
    assert len(results) > 0
    assert any(r["rel_type"] == "FK_TO" for r in results)


@pytest.mark.asyncio
async def test_neo4j_search_dispatch():
    """Neo4j 통합 search() 라우팅 (Dummy)."""
    neo4j = Neo4jConnector(use_dummy=True)
    await neo4j.connect()
    results = await neo4j.search("여신", search_type="domain_tables")
    assert isinstance(results, list)
    assert len(results) > 0


@pytest.mark.asyncio
async def test_neo4j_search_empty_concept():
    """Neo4j 매칭 없는 개념 검색 (Dummy)."""
    neo4j = Neo4jConnector(use_dummy=True)
    await neo4j.connect()
    results = await neo4j.search_domain_tables("존재하지않는개념")
    assert results == []


@pytest.mark.asyncio
async def test_neo4j_cache_invalidation():
    """Neo4j 캐시 무효화."""
    neo4j = Neo4jConnector(use_dummy=True)
    await neo4j.connect()

    # Dummy 모드에서는 _execute_cypher_cached를 안 거치므로
    # 수동으로 캐시 항목을 삽입하여 invalidate 동작을 검증
    neo4j._cache["test_key"] = (0.0, [{"dummy": True}])
    assert len(neo4j._cache) == 1

    neo4j.invalidate_cache()
    assert len(neo4j._cache) == 0
