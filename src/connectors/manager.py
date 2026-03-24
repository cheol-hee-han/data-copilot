"""커넥터 매니저 — 외부 시스템 커넥터의 싱글턴 통합 관리자.

ElasticSearch(메타/보고서 검색), PostgreSQL(정보계 DB·이력 DB),
Qdrant(업무 매뉴얼 벡터 스토어) 커넥터를 하나의 매니저로 묶어 관리한다.

싱글턴 패턴을 사용하는 이유는 커넥션 풀이 프로세스당 하나만 존재해야
리소스 누수를 방지할 수 있기 때문이다. get_connector_manager()로 인스턴스를
얻으며, 최초 호출 시 생성된 설정(use_dummy)은 이후 변경할 수 없다.

주요 기능:
    - connect_all / disconnect_all: 전체 커넥터 라이프사이클을 멱등하게 관리
    - health_check_all: 모든 커넥터의 연결 상태를 딕셔너리로 반환
    - use_dummy 모드: True로 설정하면 실제 외부 시스템 없이 더미 커넥터로
      동작하여 폐쇄망 개발·테스트 환경에서 독립적으로 실행할 수 있다.
    - reset_connector_manager: 테스트 격리를 위해 싱글턴을 초기화
"""

from __future__ import annotations

from src.connectors.impl.elasticsearch_connector import ElasticSearchConnector
from src.connectors.impl.mongo_connector import MongoConnector
from src.connectors.impl.postgres_connector import (
    HistoryDBConnector,
    InfoDBConnector,
)
from src.connectors.impl.qdrant_connector import QdrantConnector
from src.utils.logger import get_logger

logger = get_logger(__name__)


class ConnectorManager:
    """외부 시스템 커넥터 통합 관리자."""

    def __init__(self, use_dummy: bool = True) -> None:
        self._use_dummy = use_dummy
        self._connected = False
        self.es = ElasticSearchConnector(use_dummy=use_dummy)
        self.mongo = MongoConnector(use_dummy=use_dummy)
        self.info_db = InfoDBConnector(use_dummy=use_dummy)
        self.history_db = HistoryDBConnector(use_dummy=use_dummy)
        self.qdrant = QdrantConnector(use_dummy=use_dummy)

    async def connect_all(self) -> None:
        """모든 커넥터를 초기화한다 (멱등).

        이미 연결된 상태면 재연결하지 않는다.
        """
        if self._connected:
            return
        logger.info("전체 커넥터 초기화 시작")
        await self.es.connect()
        await self.mongo.connect()
        await self.info_db.connect()
        await self.history_db.connect()
        await self.qdrant.connect()
        self._connected = True
        logger.info("전체 커넥터 초기화 완료")

    async def disconnect_all(self) -> None:
        """모든 커넥터 연결을 종료한다."""
        await self.es.disconnect()
        await self.mongo.disconnect()
        await self.info_db.disconnect()
        await self.history_db.disconnect()
        await self.qdrant.disconnect()
        self._connected = False
        logger.info("전체 커넥터 연결 종료")

    async def health_check_all(self) -> dict[str, bool]:
        """모든 커넥터의 상태를 확인한다."""
        return {
            "elasticsearch": await self.es.health_check(),
            "mongodb": await self.mongo.health_check(),
            "info_db": await self.info_db.health_check(),
            "history_db": await self.history_db.health_check(),
            "qdrant": await self.qdrant.health_check(),
        }


# 글로벌 싱글턴
_manager: ConnectorManager | None = None


def get_connector_manager(
    use_dummy: bool | None = None,
) -> ConnectorManager:
    """커넥터 매니저 싱글턴 인스턴스를 반환한다.

    use_dummy 를 명시하지 않으면 settings.use_dummy 를 따른다.
    최초 호출 시 값으로 생성되며, 이후 호출에서
    다른 use_dummy 값이 전달되면 경고를 로깅한다.
    """
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
