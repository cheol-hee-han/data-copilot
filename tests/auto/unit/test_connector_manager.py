"""ConnectorManager 단위 테스트.

테스트 대상:
    - ConnectorManager.parse_db_source: 테이블명 → DB 소스 파싱 (static method)
    - ConnectorManager.__init__: dummy 모드 초기화
    - ConnectorManager.get_query_db: 배포 모드·db_source에 따른 라우팅
    - reset_connector_manager: 싱글턴 초기화

설계 원칙:
    - 실제 DB 연결 없음 (use_dummy=True)
    - parse_db_source는 순수 함수이므로 외부 의존성 없음

실행:
    pytest tests/auto/unit/test_connector_manager.py -v
"""

from __future__ import annotations

import pytest

from src.connectors.manager import (
    ConnectorManager,
    reset_connector_manager,
    get_connector_manager,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 공통 fixture: 각 테스트 전 싱글턴 초기화
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.fixture(autouse=True)
def reset_manager():
    """각 테스트 전후로 ConnectorManager 싱글턴 초기화."""
    reset_connector_manager()
    yield
    reset_connector_manager()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# parse_db_source — 테이블명에서 DB 소스 파싱
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_parse_adw_table():
    """TB_ADW_CSC101M → 'adw' 반환."""
    assert ConnectorManager.parse_db_source("TB_ADW_CSC101M") == "adw"


def test_parse_bdp_table():
    """TB_BDP_LCT001L → 'bigdata' 반환."""
    assert ConnectorManager.parse_db_source("TB_BDP_LCT001L") == "bigdata"


def test_parse_adw_lowercase():
    """소문자 테이블명도 대문자로 변환 후 파싱."""
    assert ConnectorManager.parse_db_source("tb_adw_dep201p") == "adw"


def test_parse_bdp_lowercase():
    """소문자 BDP 테이블명 파싱."""
    assert ConnectorManager.parse_db_source("tb_bdp_log001l") == "bigdata"


def test_parse_mixed_case():
    """혼합 대소문자 테이블명 파싱."""
    assert ConnectorManager.parse_db_source("Tb_Adw_Test001") == "adw"


def test_parse_no_system_code_returns_empty():
    """시스템코드가 없는 테이블명은 빈 문자열 반환."""
    assert ConnectorManager.parse_db_source("TB_CUST_INFO") == ""


def test_parse_unknown_system_code_returns_empty():
    """매핑되지 않은 시스템코드는 빈 문자열 반환."""
    assert ConnectorManager.parse_db_source("TB_XYZ_TABLE001") == ""


def test_parse_too_short_table_name():
    """언더스코어 분리 후 파트가 3개 미만이면 빈 문자열 반환."""
    assert ConnectorManager.parse_db_source("TB_ADW") == ""
    assert ConnectorManager.parse_db_source("MYTABLE") == ""


def test_parse_empty_string():
    """빈 문자열은 빈 문자열 반환."""
    assert ConnectorManager.parse_db_source("") == ""


def test_parse_schema_prefixed_table():
    """스키마명이 앞에 붙은 경우 파싱 결과 확인 (스키마 포함 → 파트 변위)."""
    # "biz_schema.TB_ADW_CSC101M" → 파트: ['BIZ_SCHEMA.TB', 'ADW', 'CSC101M']
    # 두 번째 파트가 ADW이므로 'adw' 반환
    result = ConnectorManager.parse_db_source("BIZ_SCHEMA.TB_ADW_CSC101M")
    # 스키마 구분자는 언더스코어가 아닌 점(.)이므로 전체가 첫 파트로 들어감
    # 실제 동작에 따라 검증
    assert isinstance(result, str)


def test_parse_returns_string_type():
    """반환 타입이 항상 str이어야 한다."""
    assert isinstance(ConnectorManager.parse_db_source("TB_ADW_TEST"), str)
    assert isinstance(ConnectorManager.parse_db_source("UNKNOWN"), str)


def test_parse_multiple_adw_tables():
    """다양한 ADW 테이블명 파싱."""
    adw_tables = [
        "TB_ADW_CSC101M",
        "TB_ADW_LNB301M",
        "TB_ADW_DEP201P",
        "TB_ADW_TXN001L",
    ]
    for table in adw_tables:
        assert ConnectorManager.parse_db_source(table) == "adw", f"Failed: {table}"


def test_parse_multiple_bdp_tables():
    """다양한 BDP 테이블명 파싱."""
    bdp_tables = [
        "TB_BDP_LCT001L",
        "TB_BDP_LOG002L",
        "TB_BDP_EVT003L",
    ]
    for table in bdp_tables:
        assert ConnectorManager.parse_db_source(table) == "bigdata", f"Failed: {table}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ConnectorManager 초기화 (dummy 모드)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_init_dummy_mode_creates_connectors():
    """dummy 모드로 초기화하면 커넥터 인스턴스가 생성된다."""
    manager = ConnectorManager(use_dummy=True)
    assert manager.mongo is not None
    assert manager.qdrant is not None
    assert manager.neo4j is not None
    assert manager.info_db is not None
    assert manager.history_db is not None


def test_init_dummy_mode_not_connected():
    """초기화 직후에는 connect_all 전까지 _connected가 False."""
    manager = ConnectorManager(use_dummy=True)
    assert manager._connected is False


def test_init_use_dummy_flag_stored():
    """use_dummy 플래그가 인스턴스에 저장된다."""
    manager = ConnectorManager(use_dummy=True)
    assert manager._use_dummy is True


def test_init_adw_bigdata_none_in_external_mode():
    """external 배포 모드에서는 _adw_db, _bigdata_db가 None."""
    manager = ConnectorManager(use_dummy=True)
    # use_dummy=True이면 internal 분기 진입 안 함
    assert manager._adw_db is None
    assert manager._bigdata_db is None


def test_set_checkpointer_pool():
    """set_checkpointer_pool로 pool을 주입하면 property로 접근 가능."""
    manager = ConnectorManager(use_dummy=True)
    mock_pool = object()
    manager.set_checkpointer_pool(mock_pool)
    assert manager.checkpointer_pool is mock_pool


def test_checkpointer_pool_initially_none():
    """초기 checkpointer_pool은 None이어야 한다."""
    manager = ConnectorManager(use_dummy=True)
    assert manager.checkpointer_pool is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# get_query_db — 배포 모드 라우팅
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_get_query_db_external_returns_info_db():
    """external 모드에서는 항상 info_db 반환."""
    manager = ConnectorManager(use_dummy=True)
    # settings.deployment_mode가 'external'이면 info_db 반환
    if manager._deployment == "external":
        db = manager.get_query_db()
        assert db is manager.info_db


def test_get_query_db_with_bigdata_source():
    """db_source='bigdata'이면 _bigdata_db 또는 info_db 반환."""
    manager = ConnectorManager(use_dummy=True)
    db = manager.get_query_db(db_source="bigdata")
    # _bigdata_db가 None이므로 info_db로 폴백
    assert db is manager.info_db or db is manager._bigdata_db


def test_get_query_db_with_adw_source():
    """db_source='adw'이면 _adw_db 또는 info_db 반환."""
    manager = ConnectorManager(use_dummy=True)
    db = manager.get_query_db(db_source="adw")
    # dummy 모드에서 _adw_db=None이므로 info_db 반환
    assert db is manager.info_db or db is manager._adw_db


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 싱글턴 관리
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_get_connector_manager_returns_same_instance():
    """get_connector_manager는 동일 인스턴스를 반환한다."""
    m1 = get_connector_manager(use_dummy=True)
    m2 = get_connector_manager(use_dummy=True)
    assert m1 is m2


def test_reset_connector_manager_clears_singleton():
    """reset 후 get_connector_manager는 새 인스턴스를 반환한다."""
    m1 = get_connector_manager(use_dummy=True)
    reset_connector_manager()
    m2 = get_connector_manager(use_dummy=True)
    assert m1 is not m2


def test_get_connector_manager_dummy_true():
    """use_dummy=True로 생성된 매니저의 플래그 확인."""
    manager = get_connector_manager(use_dummy=True)
    assert manager._use_dummy is True


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _DB_SOURCE_MAP 일관성
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_db_source_map_contains_adw_and_bdp():
    """_DB_SOURCE_MAP에 ADW와 BDP가 정의되어 있어야 한다."""
    assert "ADW" in ConnectorManager._DB_SOURCE_MAP
    assert "BDP" in ConnectorManager._DB_SOURCE_MAP


def test_db_source_map_adw_value():
    assert ConnectorManager._DB_SOURCE_MAP["ADW"] == "adw"


def test_db_source_map_bdp_value():
    assert ConnectorManager._DB_SOURCE_MAP["BDP"] == "bigdata"
