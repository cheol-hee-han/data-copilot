"""config.py 단위 테스트.

테스트 대상:
    - DbConnectionInfo.dsn 프로퍼티 — DSN 문자열 구성 검증
    - DbConnectionInfo 기본값 검증
    - Settings.postgres_db 프로퍼티 — Value Object 반환

외부 의존성 없음 (순수 Pydantic 모델 테스트).
"""

from __future__ import annotations

from src.config import DbConnectionInfo, Settings


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. DbConnectionInfo.dsn 프로퍼티
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestDbConnectionInfoDsn:
    """DbConnectionInfo.dsn — DSN 문자열 구성 검증."""

    def test_dsn_basic_format(self):
        """기본 DSN 문자열 형식 검증."""
        conn = DbConnectionInfo(
            host="db.example.com",
            port=5432,
            name="mydb",
            user="myuser",
            password="secret",
        )
        dsn = conn.dsn
        assert "host=db.example.com" in dsn
        assert "port=5432" in dsn
        assert "dbname=mydb" in dsn
        assert "user=myuser" in dsn

    def test_dsn_excludes_password(self):
        """DSN에 비밀번호가 포함되지 않는다 (로깅 안전)."""
        conn = DbConnectionInfo(
            host="localhost",
            port=5432,
            name="test_db",
            user="readonly",
            password="supersecret",
        )
        dsn = conn.dsn
        assert "supersecret" not in dsn
        assert "password" not in dsn

    def test_dsn_default_values(self):
        """기본값으로 생성한 DbConnectionInfo의 DSN."""
        conn = DbConnectionInfo()
        dsn = conn.dsn
        assert "host=localhost" in dsn
        assert "port=5432" in dsn
        assert "dbname=" in dsn
        assert "user=" in dsn

    def test_dsn_non_standard_port(self):
        """비표준 포트 번호가 DSN에 올바르게 포함된다."""
        conn = DbConnectionInfo(
            host="db.internal",
            port=15432,
            name="prod_db",
            user="admin",
            password="",
        )
        dsn = conn.dsn
        assert "port=15432" in dsn

    def test_dsn_empty_name_and_user(self):
        """빈 name, user도 DSN에 포함된다."""
        conn = DbConnectionInfo(host="localhost", port=5432, name="", user="")
        dsn = conn.dsn
        assert "dbname=" in dsn
        assert "user=" in dsn

    def test_dsn_string_type(self):
        """dsn은 문자열 타입을 반환한다."""
        conn = DbConnectionInfo()
        assert isinstance(conn.dsn, str)

    def test_dsn_format_consistency(self):
        """key=value 쌍이 공백으로 구분된 형식임을 검증."""
        conn = DbConnectionInfo(
            host="h",
            port=1234,
            name="n",
            user="u",
            password="p",
        )
        # "host=h port=1234 dbname=n user=u" 형태
        parts = conn.dsn.split()
        keys = [p.split("=")[0] for p in parts]
        assert "host" in keys
        assert "port" in keys
        assert "dbname" in keys
        assert "user" in keys

    def test_dsn_two_instances_are_independent(self):
        """서로 다른 인스턴스의 DSN이 독립적으로 생성된다."""
        conn1 = DbConnectionInfo(host="host1", name="db1")
        conn2 = DbConnectionInfo(host="host2", name="db2")
        assert "host1" in conn1.dsn
        assert "db1" in conn1.dsn
        assert "host2" in conn2.dsn
        assert "db2" in conn2.dsn
        assert "host1" not in conn2.dsn


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. DbConnectionInfo 기본값 및 필드 검증
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestDbConnectionInfoDefaults:
    """DbConnectionInfo 기본값 검증."""

    def test_default_host(self):
        conn = DbConnectionInfo()
        assert conn.host == "localhost"

    def test_default_port(self):
        conn = DbConnectionInfo()
        assert conn.port == 5432

    def test_default_name_empty(self):
        conn = DbConnectionInfo()
        assert conn.name == ""

    def test_default_user_empty(self):
        conn = DbConnectionInfo()
        assert conn.user == ""

    def test_default_password_empty(self):
        conn = DbConnectionInfo()
        assert conn.password == ""

    def test_explicit_values_override_defaults(self):
        conn = DbConnectionInfo(
            host="myhost",
            port=9999,
            name="mydb",
            user="myuser",
            password="mypass",
        )
        assert conn.host == "myhost"
        assert conn.port == 9999
        assert conn.name == "mydb"
        assert conn.user == "myuser"
        assert conn.password == "mypass"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. Settings.postgres_db 프로퍼티
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestSettingsPostgresDb:
    """Settings.postgres_db — DbConnectionInfo Value Object 반환 검증."""

    def test_postgres_db_returns_db_connection_info(self):
        """postgres_db 프로퍼티가 DbConnectionInfo를 반환한다."""
        s = Settings(
            postgres_db_host="pg.host",
            postgres_db_port=5433,
            postgres_db_name="pg_db",
            postgres_db_user="pg_user",
            postgres_db_password="pg_pass",
        )
        info = s.postgres_db
        assert isinstance(info, DbConnectionInfo)

    def test_postgres_db_fields_match_settings(self):
        """반환된 DbConnectionInfo 필드가 Settings 값과 일치한다."""
        s = Settings(
            postgres_db_host="pg.example.com",
            postgres_db_port=5433,
            postgres_db_name="sql_history",
            postgres_db_user="postgres_user",
            postgres_db_password="pg_secret",
        )
        info = s.postgres_db
        assert info.host == "pg.example.com"
        assert info.port == 5433
        assert info.name == "sql_history"
        assert info.user == "postgres_user"
        assert info.password == "pg_secret"

    def test_postgres_db_dsn_excludes_password(self):
        """postgres_db.dsn에 비밀번호가 포함되지 않는다."""
        s = Settings(
            postgres_db_host="localhost",
            postgres_db_password="topsecret",
        )
        dsn = s.postgres_db.dsn
        assert "topsecret" not in dsn
