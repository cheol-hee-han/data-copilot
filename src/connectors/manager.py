"""커넥터 매니저 — 외부 시스템 커넥터의 싱글턴 통합 관리자.

작성자: 한철희 / 최종수정: 2026-04-11

인프라 커넥터 (항상 활성):
  - MongoDB: 테이블/컬럼/코드/용어사전 메타 검색 (메타 주 소스)
  - Qdrant: 업무 매뉴얼·SQL 이력 벡터 검색
  - PostgreSQL: 공통 메타 DB (체크포인터, SQL 이력 영속화)

업무 DB 커넥터 (시스템 정체성 1:1):
  - ADWConnector: ADW 업무 시스템 (Sybase IQ 드라이버)
  - BDPConnector: BDP 업무 시스템 (Impala 드라이버)
  - CRPConnector: CRP 업무 시스템 (Oracle 드라이버)
  - TESTConnector: 외부망 테스트 전용 (PostgreSQL 드라이버)

라우팅 원칙:
  - 시스템 코드(ADW/BDP/CRP/TEST)가 단일 식별자로 수렴.
  - settings.system_db_overrides 로 identity 기본 + 선택적 override.
  - get_query_db 가 resolve_system_connector 를 호출하여
    override 적용 후 _db_connectors 에서 커넥터를 꺼낸다.
  - parse_db_source 는 테이블명(TB_ADW_*)에서 시스템 코드를 슬라이스.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from src.connectors.impl.mongo_connector import MongoConnector
from src.connectors.impl.postgres_connector import PostgresConnector
from src.connectors.impl.neo4j_connector import Neo4jConnector
from src.connectors.impl.qdrant_connector import QdrantConnector
from src.config import settings
from src.connectors.interfaces import BaseConnector, DatabaseConnector
from src.utils.logger import get_logger

if TYPE_CHECKING:
    from src.agents.state.state import ReasoningState

logger = get_logger(__name__)


class ConnectorManager:
    """외부 시스템 커넥터 통합 관리자.

    인프라 커넥터(MongoDB, Qdrant, PostgreSQL)와 업무 DB 커넥터를
    생성·초기화·종료하며, 시스템 코드 기반 멀티 DB 라우팅을 수행한다.

    업무 DB 커넥터는 settings.target_db_schema_map 에 등록된 시스템과
    settings.system_db_overrides 를 조합하여 필요한 커넥터만 생성한다.

    Attributes:
        mongo: MongoDB 커넥터 (테이블/코드 메타, 용어사전).
        qdrant: Qdrant 커넥터 (업무 매뉴얼, SQL 이력 벡터 검색).
        postgres: PostgreSQL 공통 DB 커넥터 (SQL 이력·체크포인터 등).
        neo4j: Neo4j 커넥터 (향후 온톨로지 그래프 활성화용).
    """

    def __init__(self, use_dummy: bool = True) -> None:
        self._use_dummy = use_dummy
        self._connected = False

        # 인프라 커넥터 (항상 존재)
        self.mongo = MongoConnector(use_dummy=use_dummy)
        self.qdrant = QdrantConnector(use_dummy=use_dummy)
        self.postgres = PostgresConnector(use_dummy=use_dummy)
        self.neo4j = Neo4jConnector(use_dummy=use_dummy)  # 향후 활성화용

        # 업무 DB 커넥터 (매핑에 등장하는 것만)
        self._db_connectors: dict[str, DatabaseConnector] = {}
        self._init_system_db_connectors(use_dummy)

        # Checkpointer pool (외부 주입)
        self._checkpointer_pool: Any = None

    def _init_system_db_connectors(self, use_dummy: bool) -> None:
        """target_db_schema_map 에 등록된 시스템을 순회하여
        override 적용 후 실제 커넥터만 생성한다.

        target_db_schema_map 은 '알려진 시스템 코드의 단일 진실원' 역할을 겸한다.
        시스템 추가는 이 dict + factory 두 곳에만 등록하면 된다.
        """
        def make_factory(ud: bool) -> dict[str, Any]:
            """시스템 코드별 커넥터 생성 lambda를 매핑한 dict를 반환한다."""
            return {
                "ADW": lambda: _import_adw()(use_dummy=ud),
                "BDP": lambda: _import_bdp()(use_dummy=ud),
                "CRP": lambda: _import_crp()(use_dummy=ud),
                "TEST": lambda: _import_test()(use_dummy=ud),
                # "HIVE": lambda: _import_hive()(use_dummy=ud),
                # 향후 Hive 기반 시스템 추가 시 활성화
            }

        required_names: set[str] = set()
        for sys_code in settings.target_db_schema_map.keys():
            required_names.add(settings.resolve_system_connector(sys_code))

        factory = make_factory(use_dummy)
        for name in required_names:
            if name in factory:
                self._db_connectors[name] = factory[name]()

    def set_checkpointer_pool(self, pool: Any) -> None:
        """checkpointer가 생성한 pool을 주입받는다.

        main.py lifespan에서 create_checkpointer() 후 호출.
        message_store 등 커스텀 테이블 접근에 이 pool을 재사용한다.
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
        """모든 커넥터를 초기화한다 (멱등).

        인프라 커넥터(mongo, qdrant, postgres)와 업무 DB 커넥터를
        순서대로 connect 한다. Qdrant 실접속 모드에서는 Reranker도 워밍업한다.
        """
        if self._connected:
            return

        # 인프라
        for attr in ("mongo", "qdrant", "postgres"):
            await getattr(self, attr).connect()
        # await self.neo4j.connect()  # 향후 활성화 시 주석 해제

        # 업무 DB (생성된 것만)
        for conn in self._db_connectors.values():
            await conn.connect()

        if not self._use_dummy:
            await self._warmup_reranker()

        self._connected = True
        logger.info(
            "커넥터 초기화 완료",
            db_connectors=sorted(self._db_connectors.keys()),
        )

    async def _warmup_reranker(self) -> None:
        """Reranker 모델 선로딩 + 워밍업.

        QdrantConnector._embed_executor를 재사용한다(단일 워커 직렬화
        원칙 유지). Reranker가 명시적으로 비활성 설정된 경우는 워밍업을
        건너뛴다. 로딩 실패는 상위로 예외 전파 → lifespan이 기동 중단.
        """
        from src.connectors.impl.reranker import (
            RerankCandidate,
            get_reranker,
        )

        reranker = get_reranker()
        if not reranker.enabled:
            logger.info("Reranker 비활성 설정 — 워밍업 스킵")
            return

        executor = self.qdrant._embed_executor
        if executor is None:
            logger.warning("Reranker 워밍업 스킵 — qdrant executor 없음")
            return

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(executor, reranker.warmup)

        dummy = [RerankCandidate(text="워밍업", payload={}, score=0.0)]
        await loop.run_in_executor(
            executor,
            lambda: reranker.rerank("워밍업", dummy, top_k=1),
        )
        logger.info("Reranker 워밍업 완료")

    async def disconnect_all(self) -> None:
        """모든 커넥터 연결을 종료한다."""
        for attr in ("mongo", "qdrant", "postgres"):
            await getattr(self, attr).disconnect()
        # await self.neo4j.disconnect()  # 향후 활성화 시 주석 해제

        for conn in self._db_connectors.values():
            await conn.disconnect()

        self._connected = False
        logger.info("커넥터 연결 종료")

    async def health_check_all(self) -> dict[str, bool]:
        """모든 커넥터의 상태를 확인한다.

        반환 키 규격:
          - 인프라: "mongodb", "qdrant", "postgres"
          - 업무 DB: _db_connectors.keys() (예: "ADW", "TEST")

        개별 커넥터에 타임아웃을 적용하여 hang을 방지한다.
        asyncio.gather 로 병렬 실행하여 전체 소요 시간을 단축한다.
        """
        timeout = settings.health_check_timeout

        async def _safe(
            name: str, conn: BaseConnector,
        ) -> tuple[str, bool]:
            try:
                return name, await asyncio.wait_for(
                    conn.health_check(), timeout=timeout,
                )
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

        targets: list[tuple[str, BaseConnector]] = [
            ("mongodb", self.mongo),
            ("qdrant", self.qdrant),
            ("postgres", self.postgres),
            # ("neo4j", self.neo4j),  # 향후 활성화 시 주석 해제
        ]
        for name, conn in self._db_connectors.items():
            targets.append((name, conn))

        results = await asyncio.gather(
            *[_safe(n, c) for n, c in targets],
        )
        return dict(results)

    def get_query_db(
        self,
        reason: ReasoningState | None = None,
        db_source: str = "",
    ) -> DatabaseConnector:
        """시스템 코드를 업무 DB 커넥터로 해석한다.

        우선순위:
          1. db_source 인자 직접 지정
          2. reason.target_db (readiness_gate 결정)
          3. 단일 매핑 자동 선택 (폴백)

        시스템 코드는 settings.resolve_system_connector 를 통해
        override 적용 후 _db_connectors 에서 조회한다.
        """
        system = db_source or (
            reason.target_db if reason and reason.target_db else ""
        )
        if system:
            real = settings.resolve_system_connector(system)
            conn = self._db_connectors.get(real)
            if conn is not None:
                return conn
            logger.warning(
                "알 수 없는 system code — 폴백",
                requested=system, resolved=real,
            )

        if len(self._db_connectors) == 1:
            return next(iter(self._db_connectors.values()))

        raise RuntimeError("업무 DB 커넥터를 결정할 수 없습니다")

    @staticmethod
    def parse_db_source(table_name: str) -> str:
        """테이블명 → 시스템 코드 (슬라이스 한 줄).

        TB_ADW_CSC101M → "ADW"
        TB_BDP_LCT001L → "BDP"
        ADWOWN.TB_ADW_CSC101M → "ADW"

        target_db_schema_map 을 '알려진 시스템 코드 목록'으로 겸용한다.
        신규 시스템 추가 시 이 dict 에 엔트리를 넣으면 parse 도 자동 인식.
        """
        name = table_name.upper().split(".")[-1]
        parts = name.split("_")
        if len(parts) >= 3 and parts[0] == "TB":
            code = parts[1]
            if code in settings.target_db_schema_map:
                return code
        return ""


# ── 지연 import 헬퍼 (factory lambda 내 순환 참조 방지) ──────────────
def _import_adw() -> type:
    from src.connectors.impl.adw_connector import ADWConnector
    return ADWConnector


def _import_bdp() -> type:
    from src.connectors.impl.bdp_connector import BDPConnector
    return BDPConnector


def _import_crp() -> type:
    from src.connectors.impl.crp_connector import CRPConnector
    return CRPConnector


def _import_test() -> type:
    from src.connectors.impl.test_connector import TESTConnector
    return TESTConnector


# ── 글로벌 싱글턴 ──────────────────────────────────────────────────────
_manager: ConnectorManager | None = None


def get_connector_manager(
    use_dummy: bool | None = None,
) -> ConnectorManager:
    """커넥터 매니저 싱글턴 인스턴스를 반환한다."""
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
