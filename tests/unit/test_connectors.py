"""커넥터 Dummy 모드(ES, PostgreSQL, Qdrant) 단위 테스트.

테스트 대상:
    use_dummy=True 설정으로 실제 인프라 없이 각 커넥터의
    헬스체크·검색·쿼리 실행이 정상 동작하는지 검증한다.
    정보계 DB의 비-SELECT 거부(보안)도 확인한다.

입력 예시 (정상):
    - ES: search_table_meta("고객") → CUST 포함 결과
    - InfoDB: execute_query("SELECT COUNT(*) FROM TB_CUST_INFO") → 1건 이상

결과 예시 (오류 케이스):
    - InfoDB: execute_query("DROP TABLE ...") → ValueError (SELECT만 허용)

실행 스크립트:
    pytest tests/unit/test_connectors.py -v

참고:
    - 외부 인프라 불필요 (Dummy 모드)
    - 테스트 대상 소스: src/connectors/impl/*.py
"""

import pytest

from src.connectors.impl.elasticsearch_connector import ElasticSearchConnector
from src.connectors.impl.postgres_connector import HistoryDBConnector, InfoDBConnector
from src.connectors.impl.qdrant_connector import QdrantConnector


@pytest.mark.asyncio
async def test_es_health_check():
    """ES 커넥터 Dummy 헬스체크."""
    es = ElasticSearchConnector(use_dummy=True)
    await es.connect()
    assert await es.health_check()


@pytest.mark.asyncio
async def test_es_search_table_meta():
    """ES 테이블 메타 검색."""
    es = ElasticSearchConnector(use_dummy=True)
    await es.connect()
    results = await es.search_table_meta("고객")
    assert len(results) > 0
    assert any("CUST" in r["table_name"] for r in results)


@pytest.mark.asyncio
async def test_es_search_report_sql():
    """ES 보고서 SQL 검색."""
    es = ElasticSearchConnector(use_dummy=True)
    await es.connect()
    results = await es.search_report_sql("고객")
    assert len(results) > 0


@pytest.mark.asyncio
async def test_es_search_code_meta():
    """ES 코드 메타 검색."""
    es = ElasticSearchConnector(use_dummy=True)
    await es.connect()
    results = await es.search_code_meta("CUST_TYPE_CD")
    assert len(results) > 0


@pytest.mark.asyncio
async def test_info_db_execute_select():
    """정보계 DB Dummy 쿼리 실행."""
    db = InfoDBConnector(use_dummy=True)
    await db.connect()
    rows = await db.execute_query("SELECT COUNT(*) FROM TB_CUST_INFO")
    assert len(rows) > 0


@pytest.mark.asyncio
async def test_info_db_reject_non_select():
    """정보계 DB 비-SELECT 거부."""
    db = InfoDBConnector(use_dummy=True)
    await db.connect()
    with pytest.raises(ValueError, match="SELECT"):
        await db.execute_query("DROP TABLE TB_CUST_INFO")


@pytest.mark.asyncio
async def test_history_db_search():
    """이력 DB 유사 SQL 검색."""
    db = HistoryDBConnector(use_dummy=True)
    await db.connect()
    results = await db.search_similar_sql("신규 고객")
    assert len(results) > 0


@pytest.mark.asyncio
async def test_qdrant_search_manual():
    """Qdrant 업무 매뉴얼 검색."""
    qdrant = QdrantConnector(use_dummy=True)
    await qdrant.connect()
    results = await qdrant.search_manual("연체 관리")
    assert len(results) > 0
    assert any("연체" in r.get("title", "") for r in results)
