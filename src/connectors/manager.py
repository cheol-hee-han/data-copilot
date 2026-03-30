"""커넥터 매니저 — 외부 시스템 커넥터의 싱글턴 통합 관리자.

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

from typing import TYPE_CHECKING

from src.connectors.impl.elasticsearch_connector import (
    ElasticSearchConnector,
)
from src.connectors.impl.mongo_connector import MongoConnector
from src.connectors.impl.postgres_connector import (
    HistoryDBConnector,
    InfoDBConnector,
)
from src.connectors.impl.neo4j_connector import Neo4jConnector
from src.connectors.impl.qdrant_connector import QdrantConnector
from src.connectors.interfaces import DatabaseConnector
from src.utils.logger import get_logger

if TYPE_CHECKING:
    from src.agents.state.state import ReasoningState

logger = get_logger(__name__)


class ConnectorManager:
    """외부 시스템 커넥터 통합 관리자."""

    def __init__(self, use_dummy: bool = True) -> None:
        from src.config import settings

        self._use_dummy = use_dummy
        self._connected = False
        self._deployment = settings.deployment_mode

        # ── 검색 커넥터 (공통) ──
        self.es = ElasticSearchConnector(use_dummy=use_dummy)
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

        if self._deployment == "internal" and not use_dummy:
            from src.connectors.impl.sybase_connector import (
                SybaseIQConnector,
            )
            from src.connectors.impl.impala_connector import (
                ImpalaConnector,
            )
            self._adw_db = SybaseIQConnector()
            self._bigdata_db = ImpalaConnector()

    async def connect_all(self) -> None:
        """모든 커넥터를 초기화한다 (멱등)."""
        if self._connected:
            return
        logger.info("전체 커넥터 초기화 시작")
        await self.es.connect()
        await self.mongo.connect()
        await self.info_db.connect()
        await self.history_db.connect()
        await self.qdrant.connect()
        await self.neo4j.connect()

        if self._adw_db:
            await self._adw_db.connect()
        if self._bigdata_db:
            await self._bigdata_db.connect()

        self._connected = True
        logger.info(
            "전체 커넥터 초기화 완료",
            deployment=self._deployment,
        )

    async def disconnect_all(self) -> None:
        """모든 커넥터 연결을 종료한다."""
        await self.es.disconnect()
        await self.mongo.disconnect()
        await self.info_db.disconnect()
        await self.history_db.disconnect()
        await self.qdrant.disconnect()
        await self.neo4j.disconnect()

        if self._adw_db:
            await self._adw_db.disconnect()
        if self._bigdata_db:
            await self._bigdata_db.disconnect()

        self._connected = False
        logger.info("전체 커넥터 연결 종료")

    async def health_check_all(self) -> dict[str, bool]:
        """모든 커넥터의 상태를 확인한다."""
        result = {
            "elasticsearch": await self.es.health_check(),
            "mongodb": await self.mongo.health_check(),
            "info_db": await self.info_db.health_check(),
            "history_db": await self.history_db.health_check(),
            "qdrant": await self.qdrant.health_check(),
            "neo4j": await self.neo4j.health_check(),
        }
        if self._adw_db:
            result["adw_db"] = (
                await self._adw_db.health_check()
            )
        if self._bigdata_db:
            result["bigdata_db"] = (
                await self._bigdata_db.health_check()
            )
        return result

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
        내부망(internal): db_source 또는 reason의 candidate_tables로 라우팅

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
            for ct in reason.candidate_tables:
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
