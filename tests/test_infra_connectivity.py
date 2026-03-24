"""인프라 연결 및 단순 조회 검증 스크립트.

폐쇄망 배포 후 각 데이터 소스의 연결 상태와 기본 조회가 정상인지 확인한다.
대상: Qdrant, ElasticSearch, PostgreSQL (정보계/이력), Impala, Sybase IQ

사용법:
    # 전체 실행
    python -m pytest tests/test_infra_connectivity.py -v

    # 특정 인프라만 실행
    python -m pytest tests/test_infra_connectivity.py -v -k "postgres"
    python -m pytest tests/test_infra_connectivity.py -v -k "elastic"
    python -m pytest tests/test_infra_connectivity.py -v -k "qdrant"
    python -m pytest tests/test_infra_connectivity.py -v -k "impala"
    python -m pytest tests/test_infra_connectivity.py -v -k "sybase"

    # 독립 실행 (pytest 없이)
    python tests/test_infra_connectivity.py
"""

from __future__ import annotations

import os
import sys

import pytest

# ──────────────────────────────────────────────────────────────
# .env 로드
# ──────────────────────────────────────────────────────────────

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ──────────────────────────────────────────────────────────────
# 환경변수에서 접속 정보 읽기
# ──────────────────────────────────────────────────────────────

# PostgreSQL (정보계)
INFO_DB = {
    "host": os.getenv("INFO_DB_HOST", "localhost"),
    "port": int(os.getenv("INFO_DB_PORT", "5432")),
    "dbname": os.getenv("INFO_DB_NAME", "info_db"),
    "user": os.getenv("INFO_DB_USER", "readonly_user"),
    "password": os.getenv("INFO_DB_PASSWORD", ""),
}

# PostgreSQL (이력)
HISTORY_DB = {
    "host": os.getenv("HISTORY_DB_HOST", "localhost"),
    "port": int(os.getenv("HISTORY_DB_PORT", "5432")),
    "dbname": os.getenv("HISTORY_DB_NAME", "history_db"),
    "user": os.getenv("HISTORY_DB_USER", "history_user"),
    "password": os.getenv("HISTORY_DB_PASSWORD", ""),
}

# ElasticSearch
ES_HOST = os.getenv("ES_HOST", "localhost")
ES_PORT = int(os.getenv("ES_PORT", "9200"))
ES_USER = os.getenv("ES_USER", "elastic")
ES_PASSWORD = os.getenv("ES_PASSWORD", "")

# Qdrant
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))

# Impala (폐쇄망 전용, 기본값은 비활성)
IMPALA_HOST = os.getenv("IMPALA_HOST", "")
IMPALA_PORT = int(os.getenv("IMPALA_PORT", "21050"))
IMPALA_AUTH = os.getenv("IMPALA_AUTH", "LDAP")  # NOSASL | LDAP
IMPALA_USER = os.getenv("IMPALA_USER", "")
IMPALA_PASSWORD = os.getenv("IMPALA_PASSWORD", "")
IMPALA_DATABASE = os.getenv("IMPALA_DATABASE", "default")

# Sybase IQ 16.1 (폐쇄망 전용, 기본값은 비활성)
# ODBC 드라이버명: 폐쇄망 서버에 설치된 드라이버명과 일치해야 함
SYBASE_HOST = os.getenv("SYBASE_HOST", "")
SYBASE_PORT = int(os.getenv("SYBASE_PORT", "2638"))
SYBASE_USER = os.getenv("SYBASE_USER", "")
SYBASE_PASSWORD = os.getenv("SYBASE_PASSWORD", "")
SYBASE_DATABASE = os.getenv("SYBASE_DATABASE", "")
SYBASE_ODBC_DRIVER = os.getenv(
    "SYBASE_ODBC_DRIVER", "SQL Anywhere 16",
)


# ──────────────────────────────────────────────────────────────
# PostgreSQL 헬퍼 — psycopg3 우선, 없으면 psycopg2 폴백
# ──────────────────────────────────────────────────────────────

def _pg_connect(*, host: str, port: int, dbname: str, user: str, password: str):
    """PostgreSQL 연결. psycopg3 우선, 없으면 psycopg2."""
    try:
        import psycopg
        return psycopg.connect(
            host=host, port=port, dbname=dbname,
            user=user, password=password,
            connect_timeout=5,
        )
    except ImportError:
        import psycopg2
        return psycopg2.connect(
            host=host, port=port, dbname=dbname,
            user=user, password=password,
            connect_timeout=5,
            options="-c client_encoding=UTF8",
        )


# ──────────────────────────────────────────────────────────────
# 헬퍼
# ──────────────────────────────────────────────────────────────

def _print_result(name: str, success: bool, detail: str = "") -> None:
    """결과를 컬러 출력한다."""
    mark = "PASS" if success else "FAIL"
    symbol = "+" if success else "!"
    line = f"  [{symbol}] {name}: {mark}"
    if detail:
        line += f"  ({detail})"
    print(line)


# ──────────────────────────────────────────────────────────────
# 1. PostgreSQL (정보계 DB)
# ──────────────────────────────────────────────────────────────

class TestPostgresInfoDB:
    """정보계 PostgreSQL 연결 및 단순 조회."""

    @staticmethod
    def _connect():
        return _pg_connect(**INFO_DB)

    def test_connection(self):
        """정보계 DB 연결 확인."""
        conn = self._connect()
        assert conn is not None
        conn.close()

    def test_simple_query(self):
        """SELECT 1 단순 조회."""
        conn = self._connect()
        cur = conn.cursor()
        cur.execute("SELECT 1 AS ping")
        row = cur.fetchone()
        assert row is not None and row[0] == 1
        cur.close()
        conn.close()

    def test_list_tables(self):
        """테이블 목록 조회 (최소 1개 이상 존재)."""
        conn = self._connect()
        cur = conn.cursor()
        cur.execute("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
            ORDER BY table_name
            LIMIT 10
        """)
        tables = [row[0] for row in cur.fetchall()]
        print(f"    정보계 테이블 ({len(tables)}건): {tables}")
        assert len(tables) >= 1, "테이블이 없음"
        cur.close()
        conn.close()

    def test_sample_select(self):
        """첫 번째 테이블에서 1건 조회."""
        conn = self._connect()
        cur = conn.cursor()
        cur.execute("""
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
            LIMIT 1
        """)
        row = cur.fetchone()
        assert row is not None, "테이블 없음"
        schema_name, table_name = row[0], row[1]

        cur.execute(f'SELECT * FROM {schema_name}."{table_name}" LIMIT 1')
        sample = cur.fetchone()
        print(f"    {schema_name}.{table_name} 샘플: {sample}")
        assert sample is not None, f"{table_name} 데이터 없음"
        cur.close()
        conn.close()


# ──────────────────────────────────────────────────────────────
# 2. PostgreSQL (이력 DB)
# ──────────────────────────────────────────────────────────────

class TestPostgresHistoryDB:
    """이력 PostgreSQL 연결 및 단순 조회."""

    @staticmethod
    def _connect():
        return _pg_connect(**HISTORY_DB)

    def test_connection(self):
        """이력 DB 연결 확인."""
        conn = self._connect()
        assert conn is not None
        conn.close()

    def test_simple_query(self):
        """SELECT 1 단순 조회."""
        conn = self._connect()
        cur = conn.cursor()
        cur.execute("SELECT 1 AS ping")
        row = cur.fetchone()
        assert row is not None and row[0] == 1
        cur.close()
        conn.close()

    def test_list_tables(self):
        """테이블 목록 조회."""
        conn = self._connect()
        cur = conn.cursor()
        cur.execute("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
            ORDER BY table_name
            LIMIT 10
        """)
        tables = [row[0] for row in cur.fetchall()]
        print(f"    이력 테이블 ({len(tables)}건): {tables}")
        assert len(tables) >= 1, "테이블이 없음"
        cur.close()
        conn.close()


# ──────────────────────────────────────────────────────────────
# 3. ElasticSearch
# ──────────────────────────────────────────────────────────────

class TestElasticSearch:
    """ElasticSearch 연결 및 인덱스 확인."""

    @staticmethod
    def _client():
        from elasticsearch import Elasticsearch
        return Elasticsearch(
            f"http://{ES_HOST}:{ES_PORT}",
            basic_auth=(ES_USER, ES_PASSWORD) if ES_PASSWORD else None,
            request_timeout=5,
        )

    def test_connection(self):
        """ES 클러스터 연결 (ping)."""
        es = self._client()
        assert es.ping(), "ES ping 실패"

    def test_cluster_health(self):
        """클러스터 상태 확인 (yellow 이상)."""
        es = self._client()
        health = es.cluster.health()
        status = health["status"]
        print(f"    클러스터 상태: {status}")
        assert status in ("green", "yellow"), f"클러스터 상태 비정상: {status}"

    def test_list_indices(self):
        """인덱스 목록 조회."""
        es = self._client()
        indices = list(es.indices.get_alias(index="*").keys())
        # 시스템 인덱스 제외
        user_indices = [i for i in indices if not i.startswith(".")]
        print(f"    인덱스 ({len(user_indices)}건): {user_indices}")
        assert len(user_indices) >= 1, "사용자 인덱스 없음"

    def test_table_meta_search(self):
        """table_meta 인덱스 단순 검색."""
        es = self._client()
        index = os.getenv("ES_TABLE_META_INDEX", "table_meta")
        if not es.indices.exists(index=index):
            pytest.skip(f"인덱스 '{index}' 없음")

        resp = es.search(
            index=index,
            body={"query": {"match_all": {}}, "size": 1},
        )
        total = resp["hits"]["total"]["value"]
        print(f"    {index} 문서 수: {total}")
        assert total >= 1, f"{index} 문서 없음"

    def test_report_sql_search(self):
        """report_sql 인덱스 단순 검색."""
        es = self._client()
        index = os.getenv("ES_REPORT_SQL_INDEX", "report_sql")
        if not es.indices.exists(index=index):
            pytest.skip(f"인덱스 '{index}' 없음")

        resp = es.search(
            index=index,
            body={"query": {"match_all": {}}, "size": 1},
        )
        total = resp["hits"]["total"]["value"]
        print(f"    {index} 문서 수: {total}")
        assert total >= 1, f"{index} 문서 없음"

    def test_code_meta_search(self):
        """code_meta 인덱스 단순 검색."""
        es = self._client()
        index = os.getenv("ES_CODE_META_INDEX", "code_meta")
        if not es.indices.exists(index=index):
            pytest.skip(f"인덱스 '{index}' 없음")

        resp = es.search(
            index=index,
            body={"query": {"match_all": {}}, "size": 1},
        )
        total = resp["hits"]["total"]["value"]
        print(f"    {index} 문서 수: {total}")
        assert total >= 1, f"{index} 문서 없음"


# ──────────────────────────────────────────────────────────────
# 4. Qdrant
# ──────────────────────────────────────────────────────────────

class TestQdrant:
    """Qdrant 연결 및 컬렉션 확인."""

    @staticmethod
    def _client():
        from qdrant_client import QdrantClient
        return QdrantClient(
            host=QDRANT_HOST,
            port=QDRANT_PORT,
            timeout=5,
        )

    def test_connection(self):
        """Qdrant 서버 연결 확인."""
        client = self._client()
        collections = client.get_collections()
        assert collections is not None

    def test_list_collections(self):
        """컬렉션 목록 조회."""
        client = self._client()
        collections = client.get_collections().collections
        names = [c.name for c in collections]
        print(f"    컬렉션 ({len(names)}건): {names}")
        assert len(names) >= 1, "컬렉션 없음"

    def test_collection_info(self):
        """각 컬렉션의 벡터 수 확인."""
        client = self._client()
        collections = client.get_collections().collections
        for col in collections:
            info = client.get_collection(col.name)
            count = info.points_count
            print(f"    {col.name}: {count}건")
            assert count >= 0

    def test_simple_scroll(self):
        """첫 번째 컬렉션에서 1건 스크롤 조회."""
        client = self._client()
        collections = client.get_collections().collections
        if not collections:
            pytest.skip("컬렉션 없음")

        name = collections[0].name
        points, _ = client.scroll(
            collection_name=name,
            limit=1,
            with_payload=True,
        )
        assert len(points) >= 1, f"{name} 데이터 없음"
        print(f"    {name} 샘플 payload 키: {list(points[0].payload.keys())}")


# ──────────────────────────────────────────────────────────────
# 5. Impala (폐쇄망 전용)
# ──────────────────────────────────────────────────────────────

@pytest.mark.skipif(
    not IMPALA_HOST,
    reason="IMPALA_HOST 미설정 (폐쇄망 전용)",
)
class TestImpala:
    """Impala (Cloudera) 연결 및 단순 조회."""

    @staticmethod
    def _connect():
        from impala.dbapi import connect
        conn_kwargs = {
            "host": IMPALA_HOST,
            "port": IMPALA_PORT,
            "database": IMPALA_DATABASE,
            "timeout": 10,
        }
        if IMPALA_AUTH == "LDAP":
            conn_kwargs["auth_mechanism"] = "LDAP"
            conn_kwargs["user"] = IMPALA_USER
            conn_kwargs["password"] = IMPALA_PASSWORD
        else:
            conn_kwargs["auth_mechanism"] = "NOSASL"
        return connect(**conn_kwargs)

    def test_connection(self):
        """Impala 연결 확인."""
        conn = self._connect()
        assert conn is not None
        conn.close()

    def test_simple_query(self):
        """SELECT 1 단순 조회."""
        conn = self._connect()
        cur = conn.cursor()
        cur.execute("SELECT 1 AS ping")
        row = cur.fetchone()
        assert row is not None and row[0] == 1
        cur.close()
        conn.close()

    def test_show_databases(self):
        """데이터베이스 목록 조회."""
        conn = self._connect()
        cur = conn.cursor()
        cur.execute("SHOW DATABASES")
        dbs = [row[0] for row in cur.fetchall()]
        print(f"    Impala DB ({len(dbs)}건): {dbs[:10]}")
        assert len(dbs) >= 1, "데이터베이스 없음"
        cur.close()
        conn.close()

    def test_show_tables(self):
        """현재 DB의 테이블 목록 조회."""
        conn = self._connect()
        cur = conn.cursor()
        cur.execute("SHOW TABLES")
        tables = [row[0] for row in cur.fetchall()]
        print(f"    Impala 테이블 ({len(tables)}건): {tables[:10]}")
        assert len(tables) >= 1, "테이블 없음"
        cur.close()
        conn.close()

    def test_sample_select(self):
        """첫 번째 테이블에서 1건 조회."""
        conn = self._connect()
        cur = conn.cursor()
        cur.execute("SHOW TABLES")
        tables = [row[0] for row in cur.fetchall()]
        if not tables:
            pytest.skip("테이블 없음")

        cur.execute(f"SELECT * FROM {tables[0]} LIMIT 1")
        sample = cur.fetchone()
        print(f"    {tables[0]} 샘플: {sample}")
        assert sample is not None
        cur.close()
        conn.close()


# ──────────────────────────────────────────────────────────────
# 6. Sybase IQ (폐쇄망 전용)
# ──────────────────────────────────────────────────────────────

@pytest.mark.skipif(
    not SYBASE_HOST,
    reason="SYBASE_HOST 미설정 (폐쇄망 전용)",
)
class TestSybaseIQ:
    """Sybase IQ 16.1 연결 및 단순 조회 (pyodbc 사용)."""

    @staticmethod
    def _connect():
        import pyodbc
        conn_str = (
            f"DRIVER={{{SYBASE_ODBC_DRIVER}}};"
            f"HOST={SYBASE_HOST}:{SYBASE_PORT};"
            f"UID={SYBASE_USER};"
            f"PWD={SYBASE_PASSWORD};"
            f"DBN={SYBASE_DATABASE};"
        )
        return pyodbc.connect(conn_str, timeout=10)

    def test_connection(self):
        """Sybase IQ 연결 확인."""
        conn = self._connect()
        assert conn is not None
        conn.close()

    def test_simple_query(self):
        """SELECT 1 단순 조회."""
        conn = self._connect()
        cur = conn.cursor()
        cur.execute("SELECT 1 AS ping")
        row = cur.fetchone()
        assert row is not None and row[0] == 1
        cur.close()
        conn.close()

    def test_list_tables(self):
        """사용자 테이블 목록 조회."""
        conn = self._connect()
        cur = conn.cursor()
        cur.execute("""
            SELECT table_name
            FROM sys.systable
            WHERE table_type = 'BASE'
              AND creator NOT IN (0, 1)
            ORDER BY table_name
        """)
        tables = [row[0] for row in cur.fetchall()[:10]]
        print(f"    Sybase IQ 테이블 ({len(tables)}건): {tables}")
        assert len(tables) >= 1, "테이블 없음"
        cur.close()
        conn.close()

    def test_sample_select(self):
        """첫 번째 테이블에서 1건 조회."""
        conn = self._connect()
        cur = conn.cursor()
        cur.execute("""
            SELECT TOP 1 table_name
            FROM sys.systable
            WHERE table_type = 'BASE'
              AND creator NOT IN (0, 1)
        """)
        row = cur.fetchone()
        if row is None:
            pytest.skip("테이블 없음")
        table_name = row[0]

        cur.execute(f"SELECT TOP 1 * FROM {table_name}")
        sample = cur.fetchone()
        print(f"    {table_name} 샘플: {sample}")
        assert sample is not None
        cur.close()
        conn.close()

    def test_odbc_driver_exists(self):
        """ODBC 드라이버가 시스템에 등록되어 있는지 확인."""
        import pyodbc
        drivers = pyodbc.drivers()
        print(f"    설치된 ODBC 드라이버: {drivers}")
        assert any("SQL Anywhere" in d or "Sybase" in d for d in drivers), (
            f"Sybase ODBC 드라이버 미설치. "
            f"현재 드라이버: {drivers}"
        )


# ──────────────────────────────────────────────────────────────
# 독립 실행 모드 (pytest 없이)
# ──────────────────────────────────────────────────────────────

def _run_standalone() -> None:
    """pytest 없이 직접 실행할 때 전체 인프라를 점검한다."""
    print()
    print("=" * 60)
    print("  인프라 연결 점검 (폐쇄망 배포 검증용)")
    print("=" * 60)

    results: list[tuple[str, bool, str]] = []

    # ── PostgreSQL (정보계) ──
    try:
        t = TestPostgresInfoDB()
        t.test_connection()
        t.test_simple_query()
        results.append(("PostgreSQL (정보계)", True, f"{INFO_DB['host']}:{INFO_DB['port']}"))
    except Exception as e:
        results.append(("PostgreSQL (정보계)", False, str(e)[:80]))

    # ── PostgreSQL (이력) ──
    try:
        t = TestPostgresHistoryDB()
        t.test_connection()
        t.test_simple_query()
        results.append(("PostgreSQL (이력)", True, f"{HISTORY_DB['host']}:{HISTORY_DB['port']}"))
    except Exception as e:
        results.append(("PostgreSQL (이력)", False, str(e)[:80]))

    # ── ElasticSearch ──
    try:
        t = TestElasticSearch()
        t.test_connection()
        t.test_cluster_health()
        results.append(("ElasticSearch", True, f"{ES_HOST}:{ES_PORT}"))
    except Exception as e:
        results.append(("ElasticSearch", False, str(e)[:80]))

    # ── Qdrant ──
    try:
        t = TestQdrant()
        t.test_connection()
        t.test_list_collections()
        results.append(("Qdrant", True, f"{QDRANT_HOST}:{QDRANT_PORT}"))
    except Exception as e:
        results.append(("Qdrant", False, str(e)[:80]))

    # ── Impala ──
    if IMPALA_HOST:
        try:
            t = TestImpala()
            t.test_connection()
            t.test_simple_query()
            results.append(("Impala", True, f"{IMPALA_HOST}:{IMPALA_PORT}"))
        except Exception as e:
            results.append(("Impala", False, str(e)[:80]))
    else:
        results.append(("Impala", False, "IMPALA_HOST 미설정 (건너뜀)"))

    # ── Sybase IQ ──
    if SYBASE_HOST:
        try:
            t = TestSybaseIQ()
            t.test_connection()
            t.test_simple_query()
            results.append(("Sybase IQ", True, f"{SYBASE_HOST}:{SYBASE_PORT}"))
        except Exception as e:
            results.append(("Sybase IQ", False, str(e)[:80]))
    else:
        results.append(("Sybase IQ", False, "SYBASE_HOST 미설정 (건너뜀)"))

    # ── 결과 출력 ──
    print()
    print("-" * 60)
    for name, success, detail in results:
        _print_result(name, success, detail)
    print("-" * 60)

    passed = sum(1 for _, s, _ in results if s)
    total = len(results)
    print(f"\n  결과: {passed}/{total} 통과")
    print("=" * 60)

    if passed < total:
        sys.exit(1)


if __name__ == "__main__":
    _run_standalone()
