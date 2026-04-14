"""ConnectorManager 단위 테스트.

테스트 대상:
    - ConnectorManager.parse_db_source: 테이블명 → 시스템 코드 파싱 (static method)
    - ConnectorManager.__init__: dummy 모드 초기화 및 업무 DB 커넥터 등록
    - ConnectorManager.get_query_db: 시스템 코드 → 업무 DB 커넥터 해석
    - reset_connector_manager: 싱글턴 초기화
    - settings.target_db_code, settings.resolve_system_connector

설계 원칙:
    - 실제 DB 연결 없음 (use_dummy=True)
    - parse_db_source 는 순수 함수이므로 외부 의존성 없음
    - 시스템 코드(ADW/BDP/CRP) 가 단일 식별자로 수렴

실행:
    pytest tests/auto/unit/test_connector_manager.py -v
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.config import Settings, settings
from src.connectors.manager import (
    ConnectorManager,
    get_connector_manager,
    reset_connector_manager,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 공통 fixture: 각 테스트 전 싱글턴 초기화
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.fixture(autouse=True)
def reset_manager():
    reset_connector_manager()
    yield
    reset_connector_manager()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# parse_db_source — 테이블명에서 시스템 코드 파싱
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_parse_adw_table():
    """TB_ADW_CSC101M → 'ADW' 반환."""
    assert ConnectorManager.parse_db_source("TB_ADW_CSC101M") == "ADW"


def test_parse_bdp_table():
    """TB_BDP_LCT001L → 'BDP' 반환."""
    assert ConnectorManager.parse_db_source("TB_BDP_LCT001L") == "BDP"


def test_parse_crp_table():
    """TB_CRP_XXX → 'CRP' 반환."""
    assert ConnectorManager.parse_db_source("TB_CRP_ACCT001") == "CRP"


def test_parse_lowercase_normalized():
    """소문자 테이블명도 대문자로 변환 후 파싱."""
    assert ConnectorManager.parse_db_source("tb_adw_dep201p") == "ADW"
    assert ConnectorManager.parse_db_source("tb_bdp_log001l") == "BDP"


def test_parse_mixed_case():
    assert ConnectorManager.parse_db_source("Tb_Adw_Test001") == "ADW"


def test_parse_no_system_code_returns_empty():
    """시스템 코드가 없는 테이블명은 빈 문자열 반환."""
    assert ConnectorManager.parse_db_source("TB_CUST_INFO") == ""


def test_parse_unknown_system_code_returns_empty():
    """target_db_schema_map 에 없는 시스템 코드는 빈 문자열 반환."""
    assert ConnectorManager.parse_db_source("TB_XYZ_TABLE001") == ""


def test_parse_too_short_table_name():
    """언더스코어 분리 후 파트가 3개 미만이면 빈 문자열 반환."""
    assert ConnectorManager.parse_db_source("TB_ADW") == ""
    assert ConnectorManager.parse_db_source("MYTABLE") == ""


def test_parse_empty_string():
    assert ConnectorManager.parse_db_source("") == ""


def test_parse_returns_string_type():
    assert isinstance(ConnectorManager.parse_db_source("TB_ADW_TEST"), str)
    assert isinstance(ConnectorManager.parse_db_source("UNKNOWN"), str)


def test_parse_schema_prefixed_adw():
    """ADWOWN.TB_ADW_LNB333M → 'ADW' (스키마 접두사 제거)."""
    assert ConnectorManager.parse_db_source(
        "ADWOWN.TB_ADW_LNB333M",
    ) == "ADW"


def test_parse_schema_prefixed_bdp():
    """BDPOWN.TB_BDP_XXX001L → 'BDP' (스키마 접두사 제거)."""
    assert ConnectorManager.parse_db_source(
        "BDPOWN.TB_BDP_XXX001L",
    ) == "BDP"


def test_parse_schema_prefixed_lowercase():
    """adwown.tb_adw_dep201p → 'ADW' (소문자 + 스키마)."""
    assert ConnectorManager.parse_db_source(
        "adwown.tb_adw_dep201p",
    ) == "ADW"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ConnectorManager 초기화 (dummy 모드)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_init_creates_infra_connectors():
    """인프라 커넥터(mongo/qdrant/postgres/neo4j) 는 항상 생성된다."""
    manager = ConnectorManager(use_dummy=True)
    assert manager.mongo is not None
    assert manager.qdrant is not None
    assert manager.postgres is not None
    assert manager.neo4j is not None


def test_init_populates_db_connectors():
    """target_db_schema_map 키를 resolve 한 결과로 _db_connectors 가 채워진다.

    기본 설정(system_db_overrides={"ADW":"TEST"}) 에서는 ADW→TEST, BDP→BDP, CRP→CRP
    로 매핑되어 BDP, CRP, TEST 세 개의 커넥터가 등록된다.
    """
    manager = ConnectorManager(use_dummy=True)
    keys = set(manager._db_connectors.keys())
    expected = {
        settings.resolve_system_connector(code)
        for code in settings.target_db_schema_map.keys()
    }
    assert keys == expected


def test_init_not_connected_initially():
    """초기화 직후에는 connect_all 전까지 _connected 가 False."""
    manager = ConnectorManager(use_dummy=True)
    assert manager._connected is False


def test_init_use_dummy_flag_stored():
    manager = ConnectorManager(use_dummy=True)
    assert manager._use_dummy is True


def test_set_checkpointer_pool():
    manager = ConnectorManager(use_dummy=True)
    mock_pool = object()
    manager.set_checkpointer_pool(mock_pool)
    assert manager.checkpointer_pool is mock_pool


def test_checkpointer_pool_initially_none():
    manager = ConnectorManager(use_dummy=True)
    assert manager.checkpointer_pool is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# get_query_db — 시스템 코드 라우팅
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_get_query_db_with_adw_source_resolves_override():
    """db_source='ADW' 는 override 를 거쳐 TEST 커넥터로 해석된다."""
    manager = ConnectorManager(use_dummy=True)
    db = manager.get_query_db(db_source="ADW")
    expected = manager._db_connectors[
        settings.resolve_system_connector("ADW")
    ]
    assert db is expected


def test_get_query_db_with_bdp_source_identity():
    """override 가 없는 BDP 는 identity 로 BDP 커넥터 반환."""
    manager = ConnectorManager(use_dummy=True)
    db = manager.get_query_db(db_source="BDP")
    assert db is manager._db_connectors["BDP"]


def test_get_query_db_unknown_source_falls_back_or_raises():
    """알 수 없는 시스템 코드는 단일 매핑 폴백 또는 RuntimeError."""
    manager = ConnectorManager(use_dummy=True)
    if len(manager._db_connectors) == 1:
        db = manager.get_query_db(db_source="UNKNOWN")
        assert db is next(iter(manager._db_connectors.values()))
    else:
        with pytest.raises(RuntimeError):
            manager.get_query_db(db_source="UNKNOWN")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 싱글턴 관리
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_get_connector_manager_returns_same_instance():
    m1 = get_connector_manager(use_dummy=True)
    m2 = get_connector_manager(use_dummy=True)
    assert m1 is m2


def test_reset_connector_manager_clears_singleton():
    m1 = get_connector_manager(use_dummy=True)
    reset_connector_manager()
    m2 = get_connector_manager(use_dummy=True)
    assert m1 is not m2


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# settings.target_db_code 검증
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_target_db_code_accepts_valid_system_code():
    """유효한 시스템 코드(ADW) 는 통과하고 대문자로 보존된다."""
    s = Settings(target_db_code="ADW")
    assert s.target_db_code == "ADW"


def test_target_db_code_normalizes_lowercase():
    """소문자 입력도 허용하고 대문자로 정규화한다."""
    s = Settings(target_db_code="bdp")
    assert s.target_db_code == "BDP"


def test_target_db_code_rejects_unknown_code():
    """target_db_schema_map 에 없는 시스템 코드는 거부된다."""
    with pytest.raises(ValidationError):
        Settings(target_db_code="XYZ")


def test_target_db_code_empty_allowed():
    """미지정(빈 문자열) 은 통과한다 (동적 결정 모드)."""
    s = Settings(target_db_code="")
    assert s.target_db_code == ""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# settings.resolve_system_connector 및 override 적용
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_resolve_system_connector_identity_by_default():
    """override 가 없는 시스템은 identity 매핑."""
    s = Settings(_env_file=None, system_db_overrides={})
    assert s.resolve_system_connector("ADW") == "ADW"
    assert s.resolve_system_connector("BDP") == "BDP"
    assert s.resolve_system_connector("CRP") == "CRP"


def test_resolve_system_connector_applies_override():
    """system_db_overrides 에 등록된 시스템은 override 된 커넥터로 해석."""
    s = Settings(_env_file=None, system_db_overrides={"ADW": "TEST"})
    assert s.resolve_system_connector("ADW") == "TEST"
    assert s.resolve_system_connector("BDP") == "BDP"
