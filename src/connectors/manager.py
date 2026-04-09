"""커넥터 매니저 — 외부 시스템 커넥터의 싱글턴 통합 관리자.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

검색 커넥터:
  - ElasticSearch: 보고서 SQL 검색 (table_meta/code_meta는 하위 호환용 보존)
  - MongoDB: 테이블/컬럼/코드/용어사전 메타 검색 (메타 주 소스)
  - Qdrant: 업무 매뉴얼·SQL 이력 벡터 검색
  - Neo4j: 온톨로지 그래프 (테이블 관계, JOIN 경로, 계수산출식)

업무 DB 커넥터 (멀티 DB 라우팅):
  - 외부망(external): PostgreSQL (info_db) 단일
  - 내부망(internal): ADW(Sybase IQ) + BDP(Impala), Hive 예비

이력 DB:
  - PostgreSQL (history_db): SQL 수행 이력

멀티 DB 라우팅:
  테이블명의 시스템코드 3자리(TB_{SYS}_...)에서 DB 소스를 파싱하고,
  get_query_db()가 올바른 커넥터를 반환한다.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

# from src.connectors.impl.elasticsearch_connector import (
#     ElasticSearchConnector,
# )
from src.connectors.impl.mongo_connector import MongoConnector
from src.connectors.impl.postgres_connector import (
    HistoryDBConnector,
    InfoDBConnector,
)
from src.connectors.impl.neo4j_connector import Neo4jConnector
from src.connectors.impl.qdrant_connector import QdrantConnector
from src.config import settings
from src.connectors.interfaces import BaseConnector, DatabaseConnector
from src.utils.logger import get_logger

if TYPE_CHECKING:
    from src.agents.state.state import ReasoningState

logger = get_logger(__name__)



# ── 커넥터 레지스트리 ─────────────────────────────────
# (config 이름, attribute 이름) 매핑.
# enabled_connectors에 config 이름이 포함된 커넥터만
# connect/disconnect/health_check를 수행한다.
_CONNECTORS: list[tuple[str, str]] = [
    # ("elasticsearch", "es"),  # 미사용
    ("mongodb", "mongo"),
    ("qdrant", "qdrant"),
    ("neo4j", "neo4j"),
    ("info_db", "info_db"),
    ("history_db", "history_db"),
]


class ConnectorManager:
    """외부 시스템 커넥터 통합 관리자.

    검색 커넥터(ES, MongoDB, Qdrant, Neo4j)와 업무/이력 DB 커넥터를
    생성·초기화·종료하며, 배포 모드(external/internal)에 따라
    멀티 DB 라우팅(ADW/BDP)을 수행한다.

    모든 커넥터는 __init__에서 인스턴스를 생성하되,
    settings.enabled_connectors에 포함된 커넥터만 실제 connect를 수행한다.
    비활성 커넥터는 dummy 모드 인스턴스로 유지되어 빈 결과를 반환한다.

    Attributes:
        es: ElasticSearch 커넥터 (보고서 SQL 검색).
        mongo: MongoDB 커넥터 (테이블/코드 메타, 용어사전).
        qdrant: Qdrant 커넥터 (업무 매뉴얼, SQL 이력 벡터 검색).
        neo4j: Neo4j 커넥터 (온톨로지 그래프).
        info_db: 정보계 DB 커넥터 (읽기 전용).
        history_db: SQL 이력 DB 커넥터.
    """

    def __init__(self, use_dummy: bool = True) -> None:
        from src.config import settings

        self._use_dummy = use_dummy
        self._connected = False
        self._deployment = settings.deployment_mode

        # ── 검색 커넥터 (공통) ──
        self.es: Any = None  # ES 미사용, 향후 재사용 시 복원
        self.mongo = MongoConnector(use_dummy=use_dummy)
        self.qdrant = QdrantConnector(use_dummy=use_dummy)
        self.neo4j = Neo4jConnector(use_dummy=use_dummy)

        # ── 이력 DB (공통) ──
        self.history_db = HistoryDBConnector(
            use_dummy=use_dummy,
        )

        # ── 업무 DB (배포 모드에 따라 분기) ──
        self.info_db = InfoDBConnector(use_dummy=use_dummy)

        self._adw_db: DatabaseConnector | None = None
        self._bigdata_db: DatabaseConnector | None = None

        # ── Checkpointer pool (외부 주입) ──
        self._checkpointer_pool: Any = None

        if self._deployment == "internal" and not use_dummy:
            from src.connectors.impl.sybase_connector import (
                SybaseIQConnector,
            )
            from src.connectors.impl.impala_connector import (
                ImpalaConnector,
            )
            self._adw_db = SybaseIQConnector()
            self._bigdata_db = ImpalaConnector()

    def set_checkpointer_pool(self, pool: Any) -> None:
        """checkpointer가 생성한 pool을 주입받는다.

        main.py lifespan에서 create_checkpointer() 후 호출.
        turn_text_store 등 커스텀 테이블 접근에 이 pool을 재사용한다.
        """
        self._checkpointer_pool = pool

    @property
    def checkpointer_pool(self) -> Any | None:
        """checkpoint_dc_* 테이블 접근용 pool (checkpointer와 공유).

        MemorySaver 경로(backend=memory)에서는 None을 반환한다.
        호출부에서 None 체크 후 적절히 처리해야 한다.
        """
        return self._checkpointer_pool

    async def connect_all(self) -> None:
        """활성 커넥터를 초기화한다 (멱등).

        settings.enabled_connectors에 포함된 커넥터만 connect를 수행한다.
        비활성 커넥터는 dummy 모드 인스턴스로 유지된다.
        """
        if self._connected:
            return
        enabled = settings.enabled_connectors
        logger.info("커넥터 초기화 시작", enabled=sorted(enabled))

        for cfg_name, attr in _CONNECTORS:
            if cfg_name in enabled:
                await getattr(self, attr).connect()

        if self._adw_db:
            await self._adw_db.connect()
        if self._bigdata_db:
            await self._bigdata_db.connect()

        self._connected = True
        logger.info(
            "커넥터 초기화 완료",
            deployment=self._deployment,
            enabled=sorted(enabled),
        )

    async def disconnect_all(self) -> None:
        """활성 커넥터 연결을 종료한다."""
        enabled = settings.enabled_connectors
        for cfg_name, attr in _CONNECTORS:
            if cfg_name in enabled:
                await getattr(self, attr).disconnect()

        if self._adw_db:
            await self._adw_db.disconnect()
        if self._bigdata_db:
            await self._bigdata_db.disconnect()

        self._connected = False
        logger.info("커넥터 연결 종료")

    async def health_check_all(self) -> dict[str, bool]:
        """모든 커넥터의 상태를 확인한다.

        개별 커넥터에 타임아웃을 적용하여 hang을 방지한다.
        커넥터가 응답하지 않으면 타임아웃 후 False를 반환한다.
        asyncio.gather로 병렬 실행하여 전체 소요 시간을 단축한다.
        """
        timeout = settings.health_check_timeout

        async def _safe_check(
            name: str, connector: BaseConnector,
        ) -> tuple[str, bool]:
            try:
                result = await asyncio.wait_for(
                    connector.health_check(), timeout=timeout,
                )
                return name, result
            except asyncio.TimeoutError:
                logger.warning(
                    "health_check 타임아웃",
                    connector=name, timeout=timeout,
                )
                return name, False
            except Exception as e:
                logger.debug(
                    "health_check 실패",
                    connector=name, error=str(e),
                )
                return name, False

        enabled = settings.enabled_connectors
        checks = [
            _safe_check(cfg_name, getattr(self, attr))
            for cfg_name, attr in _CONNECTORS
            if cfg_name in enabled
        ]
        if self._adw_db:
            checks.append(_safe_check("adw_db", self._adw_db))
        if self._bigdata_db:
            checks.append(
                _safe_check("bigdata_db", self._bigdata_db),
            )

        results = await asyncio.gather(*checks)
        return dict(results)

    # ── 테이블명 → DB 소스 파싱 ──────────────────────────
    _DB_SOURCE_MAP: dict[str, str] = {
        "ADW": "adw",       # Sybase IQ (정보계 DW)
        "BDP": "bigdata",   # Impala (빅데이터 플랫폼)
    }

    @staticmethod
    def parse_db_source(table_name: str) -> str:
        """테이블명에서 시스템코드를 추출하여 DB 소스를 반환한다.

        TB_ADW_CSC101M → "adw"
        TB_BDP_LCT001L → "bigdata"
        매핑 없으면 빈 문자열 반환.
        """
        parts = table_name.upper().split("_")
        if len(parts) >= 3:
            source = ConnectorManager._DB_SOURCE_MAP.get(parts[1])
            if source:
                return source
        return ""

    # ── 업무 DB 라우팅 ────────────────────────────────────
    def get_query_db(
        self,
        reason: ReasoningState | None = None,
        db_source: str = "",
    ) -> DatabaseConnector:
        """올바른 업무 DB 커넥터를 반환한다.

        외부망(external): 항상 info_db (PostgreSQL)
        내부망(internal): db_source 또는 reason의 explored_tables로 라우팅

        db_source를 직접 지정하면 reason보다 우선한다.
        """
        # 외부 테스트 환경에서는 postgres 사용
        if self._deployment == "external":
            return self.info_db

        # db_source 직접 지정
        if db_source == "bigdata":
            return self._bigdata_db or self.info_db
        if db_source:
            return self._adw_db or self.info_db

        # reason에서 추출
        if reason is not None:
            sources: set[str] = set()
            for ct in reason.explored_tables:
                src = ct.db_source or self.parse_db_source(
                    ct.table_name,
                )
                if src:
                    sources.add(src)
            if "bigdata" in sources:
                return self._bigdata_db or self.info_db

        return self._adw_db or self.info_db


# ── 글로벌 싱글턴 ──────────────────────────────────────
_manager: ConnectorManager | None = None


def get_connector_manager(
    use_dummy: bool | None = None,
) -> ConnectorManager:
    """커넥터 매니저 싱글턴 인스턴스를 반환한다."""
    from src.config import settings

    global _manager
    if use_dummy is None:
        use_dummy = settings.use_dummy

    if _manager is None:
        _manager = ConnectorManager(use_dummy=use_dummy)
    elif _manager._use_dummy != use_dummy:
        logger.warning(
            "ConnectorManager 이미 생성됨, use_dummy 변경 무시",
            current=_manager._use_dummy,
            requested=use_dummy,
        )
    return _manager


def reset_connector_manager() -> None:
    """싱글턴 인스턴스를 초기화한다 (테스트 격리용)."""
    global _manager
    _manager = None
