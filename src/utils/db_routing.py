"""멀티 DB 라우팅 유틸리티 — 테이블명 시스템코드 기반.

테이블명에서 시스템코드(3자리)를 추출하여 DB 소스, SQL dialect, 스키마명을 결정한다.
폐쇄망 환경에서 ADW(Sybase IQ)와 BDP(Impala) 간 라우팅에 사용된다.
개발 환경(external/PostgreSQL)에서는 deployment_mode에 따라 postgres dialect을 사용한다.
"""

from __future__ import annotations

from src.config import settings

# 시스템코드(3자리) → DB 소스 매핑
DB_SOURCE_MAP: dict[str, str] = {
    "ADW": "adw",       # Sybase IQ (정보계 DW)
    "BDP": "bigdata",   # Impala (빅데이터 플랫폼)
}

# DB 소스 → sqlglot dialect 매핑
DB_DIALECT_MAP: dict[str, str] = {
    "adw":     "tsql",       # Sybase IQ → tsql 근사 매핑
    "bigdata": "hive",       # Impala → hive 근사 매핑
    "postgres": "postgres",  # 개발 환경 PostgreSQL
}

# DB 소스 → 기본 스키마명 매핑
# 폐쇄망에서 SQL 실행 시 스키마명.테이블명 형태가 필수
# MongoDB 메타에 schema_name이 있으면 그 값을 우선 사용하고,
# 없으면 이 매핑에서 db_source 기반으로 기본 스키마를 결정한다.
DB_SCHEMA_MAP: dict[str, str] = {
    "adw":     "ADWOWN",    # Sybase IQ 기본 스키마
    "bigdata": "BDPOWN",    # Impala 기본 스키마
}


def _default_db_source() -> str:
    """deployment_mode에 따라 기본 DB 소스를 결정한다.

    external(개발/PostgreSQL) → "postgres"
    internal(폐쇄망)          → settings.default_db_source (기본 "adw")
    """
    if settings.deployment_mode == "external":
        return "postgres"
    return settings.default_db_source


def parse_db_source(table_name: str) -> str:
    """테이블명에서 시스템코드를 추출하여 DB 소스를 반환한다.

    TB_ADW_CSC101M → "adw"
    TB_BDP_LCT001L → "bigdata"
    기타(개발환경)  → deployment_mode에 따라 결정
    """
    parts = table_name.upper().split("_")
    if len(parts) >= 3:
        sys_code = parts[1]
        source = DB_SOURCE_MAP.get(sys_code)
        if source:
            return source
    return _default_db_source()


def get_dialect_for_source(db_source: str) -> str:
    """DB 소스에 대응하는 sqlglot dialect을 반환한다.

    db_source가 빈 문자열이거나 매핑에 없으면
    deployment_mode에 따라 기본 dialect을 결정한다.
    """
    if db_source and db_source in DB_DIALECT_MAP:
        return DB_DIALECT_MAP[db_source]
    # 기본 소스에서 dialect 결정
    default_source = _default_db_source()
    return DB_DIALECT_MAP.get(default_source, "postgres")


def get_schema_for_source(db_source: str) -> str:
    """DB 소스에 대응하는 기본 스키마명을 반환한다.

    MongoDB 메타에 schema_name이 없을 때 fallback으로 사용한다.
    개발 환경(PostgreSQL)에서는 스키마 없이 동작하므로 빈 문자열 반환.
    """
    return DB_SCHEMA_MAP.get(db_source, "")
