"""커넥터 실 연결 테스트 (use_dummy=False).

Docker 인프라가 기동된 상태에서 모든 커넥터의 실제 연결·헬스체크·기본 쿼리를 검증한다.

사전 조건:
    docker compose -f devtools/docker/docker-compose.dev.yml up -d

실행:
    python -m pytest tests/manual/e2e/test_connector_real.py -v
    python -m pytest tests/manual/e2e/test_connector_real.py -v -k "neo4j"
"""

from __future__ import annotations

import os
import sys

import pytest

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# 환경 변수 기본값 설정 (docker-compose.dev.yml 기준)
_DEFAULTS = {
    "ES_HOST": "localhost", "ES_PORT": "9200",
    "ES_USER": "elastic", "ES_PASSWORD": "elastic_pass",
    "MONGO_HOST": "localhost", "MONGO_PORT": "27017",
    "MONGO_USER": "mongoadmin", "MONGO_PASSWORD": "mongo_pass",
    "MONGO_DATABASE": "meta_db",
    "NEO4J_HOST": "localhost", "NEO4J_PORT": "7687",
    "NEO4J_USER": "neo4j", "NEO4J_PASSWORD": "neo4j_pass",
    "INFO_DB_HOST": "localhost", "INFO_DB_PORT": "5432",
    "INFO_DB_USER": "readonly_user", "INFO_DB_PASSWORD": "",
    "QDRANT_HOST": "localhost", "QDRANT_PORT": "6333",
}
for key, val in _DEFAULTS.items():
    os.environ.setdefault(key, val)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ElasticSearch
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.asyncio
async def test_es_real_connection():
    """ES 실 연결 + 헬스체크."""
    from src.connectors.impl.elasticsearch_connector import ElasticSearchConnector

    es = ElasticSearchConnector(use_dummy=False)
    await es.connect()
    try:
        healthy = await es.health_check()
        print(f"  ES health_check: {healthy}")
        assert healthy
    finally:
        await es.disconnect()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MongoDB
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.asyncio
async def test_mongo_real_connection():
    """MongoDB 실 연결 + 헬스체크."""
    from src.connectors.impl.mongo_connector import MongoConnector

    mongo = MongoConnector(use_dummy=False)
    await mongo.connect()
    try:
        healthy = await mongo.health_check()
        print(f"  MongoDB health_check: {healthy}")
        assert healthy
    finally:
        await mongo.disconnect()


@pytest.mark.asyncio
async def test_mongo_real_search_table_meta():
    """MongoDB 테이블 메타 검색 (실 DB)."""
    from src.connectors.impl.mongo_connector import MongoConnector

    mongo = MongoConnector(use_dummy=False)
    await mongo.connect()
    try:
        # $text 검색 — 데이터가 없어도 에러 없이 빈 리스트 반환되어야 함
        results = await mongo.search_table_meta("고객")
        print(f"  MongoDB table_meta('고객'): {len(results)}건")
        assert isinstance(results, list)
    finally:
        await mongo.disconnect()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Neo4j
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.asyncio
async def test_neo4j_real_connection():
    """Neo4j 실 연결 + 헬스체크."""
    from src.connectors.impl.neo4j_connector import Neo4jConnector

    neo4j = Neo4jConnector(use_dummy=False)
    await neo4j.connect()
    try:
        healthy = await neo4j.health_check()
        print(f"  Neo4j health_check: {healthy}")
        assert healthy
    finally:
        await neo4j.disconnect()


@pytest.mark.asyncio
async def test_neo4j_real_cypher_execution():
    """Neo4j Cypher 실행 — 기본 RETURN 1."""
    from src.connectors.impl.neo4j_connector import Neo4jConnector

    neo4j = Neo4jConnector(use_dummy=False)
    await neo4j.connect()
    try:
        results = await neo4j._execute_cypher("RETURN 1 AS value")
        print(f"  Neo4j RETURN 1: {results}")
        assert len(results) == 1
        assert results[0]["value"] == 1
    finally:
        await neo4j.disconnect()


@pytest.mark.asyncio
async def test_neo4j_real_search_empty():
    """Neo4j 검색 — 데이터 없는 상태에서도 에러 없이 빈 리스트 반환."""
    from src.connectors.impl.neo4j_connector import Neo4jConnector

    neo4j = Neo4jConnector(use_dummy=False)
    await neo4j.connect()
    try:
        results = await neo4j.search_domain_tables("여신")
        print(f"  Neo4j domain_tables('여신'): {len(results)}건")
        assert isinstance(results, list)

        results = await neo4j.search_formula("연체율")
        print(f"  Neo4j formula('연체율'): {len(results)}건")
        assert isinstance(results, list)

        results = await neo4j.search_code_hierarchy("정상")
        print(f"  Neo4j code_hierarchy('정상'): {len(results)}건")
        assert isinstance(results, list)
    finally:
        await neo4j.disconnect()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PostgreSQL
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.asyncio
async def test_postgres_real_connection():
    """PostgreSQL 실 연결 + 헬스체크."""
    from src.connectors.impl.postgres_connector import InfoDBConnector

    db = InfoDBConnector(use_dummy=False)
    await db.connect()
    try:
        healthy = await db.health_check()
        print(f"  PostgreSQL health_check: {healthy}")
        assert healthy
    finally:
        await db.disconnect()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Qdrant
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.asyncio
async def test_qdrant_real_connection():
    """Qdrant 실 연결 + 헬스체크."""
    from src.connectors.impl.qdrant_connector import QdrantConnector

    qdrant = QdrantConnector(use_dummy=False)
    await qdrant.connect()
    try:
        healthy = await qdrant.health_check()
        print(f"  Qdrant health_check: {healthy}")
        assert healthy
    finally:
        await qdrant.disconnect()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Redis
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.asyncio
async def test_redis_real_connection():
    """Redis 실 연결 + PING."""
    import redis.asyncio as aioredis

    r = aioredis.from_url(
        f"redis://{os.environ.get('REDIS_HOST', 'localhost')}:"
        f"{os.environ.get('REDIS_PORT', '6379')}",
    )
    try:
        pong = await r.ping()
        print(f"  Redis PING: {pong}")
        assert pong
    finally:
        await r.close()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ConnectorManager 통합
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.asyncio
async def test_connector_manager_real():
    """ConnectorManager 전체 실 연결 + 헬스체크."""
    from src.connectors.manager import ConnectorManager

    mgr = ConnectorManager(use_dummy=False)
    await mgr.connect_all()
    try:
        health = await mgr.health_check_all()
        print(f"  ConnectorManager health_check_all: {health}")
        for name, status in health.items():
            print(f"    {name}: {'OK' if status else 'FAIL'}")
    finally:
        await mgr.disconnect_all()
