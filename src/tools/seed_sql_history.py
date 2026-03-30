"""SQL 수행이력 벡터 시딩 배치 도구.

PostgreSQL에서 SQL 수행이력을 추출하고, LLM으로 비즈니스 설명을 추론한 뒤,
BGE-M3 임베딩(Dense+Sparse)을 생성하여 Qdrant sql_history 컬렉션에 적재한다.

기동 예시::

    # 전체 시딩
    python -m src.tools.seed_sql_history

    # 건수 제한 + 드라이런
    python -m src.tools.seed_sql_history --limit 100 --dry-run

    # 검증만 실행
    python -m src.tools.seed_sql_history --verify-only

    # 컬렉션 재생성 후 시딩
    python -m src.tools.seed_sql_history --recreate-collection

임베딩 최적화 전략 (embedding-search-strategy.md):
    - LLM이 간결한 비즈니스 설명을 생성 → 임베딩 품질 향상
    - 동의어·영문번역·관련 금융용어 보강(enrichment) → Recall 개선
    - Dense(의미) + Sparse(키워드) 하이브리드 → Precision 개선
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass, field
from src.utils.timezone import now_stamp
from pathlib import Path
from typing import Any

import sqlglot

from src.config import settings
from src.utils.logger import get_logger, setup_logging

logger = get_logger(__name__)

# ── 상수 ──

_SEED_VERSION = "1.0.0"

_EXTRACT_SQL = """
SELECT sql_text
FROM sql_query_history
WHERE success = TRUE
ORDER BY executed_at DESC
""".strip()

_INFER_SYSTEM_PROMPT = """\
당신은 SQL 분석 전문가입니다.
주어진 SQL 쿼리가 어떤 데이터를 어떤 조건으로 추출하는지
비즈니스 관점에서 **한 문장**으로 간결하게 설명하세요.

규칙:
- SQL 키워드(SELECT, JOIN 등)를 사용하지 마세요
- 테이블명·컬럼명 대신 업무 용어를 사용하세요
- 20자~60자 이내의 한국어 문장으로 작성하세요
- 설명 뒤에 보강 정보를 추가하세요

출력 형식 (반드시 아래 형식을 따르세요):
DESCRIPTION: (한국어 비즈니스 설명)
ENRICHMENT: (한국어 동의어 2~3개), (영문 표현 2~3개), (관련 금융용어 1~2개)

TODO:
- 한글, 영어, IT용어혼용된 설명 등으로 다양한 임베딩 생성 → 검색 시 다양한 표현과 매칭 가능
- 어떤 업무 주제영역에 대한 쿼리인지 보강 → 검색 결과 필터링/가중치 조정 가능
- 저장된 Value 의 포맷이 qdrant_connectors.py 에서 일관되게 처리되고 있는지 점검 필요

"""

_VERIFY_QUERIES = [
    "이번 달 신규 대출 건수",
    "고객 유형별 예금 잔액 현황",
    "분기별 연체율 추이",
]


# ── 데이터 모델 ──


@dataclass
class SQLHistoryRecord:
    """시딩 대상 SQL 이력 레코드."""

    sql: str
    description: str = ""
    enriched: str = ""
    tables_used: list[str] = field(default_factory=list)
    source: str = "history_db"
    seed_version: str = _SEED_VERSION
    seed_dt: str = ""

    def to_payload(self) -> dict[str, Any]:
        """Qdrant 포인트 payload 딕셔너리로 변환한다."""
        return {
            "sql": self.sql,
            "description": self.description,
            "enriched": self.enriched,
            "tables_used": self.tables_used,
            "source": self.source,
            "seed_version": self.seed_version,
            "seed_dt": self.seed_dt,
        }


# ── 유틸 ──


def _extract_tables(sql: str) -> list[str]:
    """SQL에서 참조 테이블명을 추출한다."""
    try:
        parsed = sqlglot.parse_one(sql)
        return sorted({
            t.name.upper()
            for t in parsed.find_all(sqlglot.exp.Table)
            if t.name
        })
    except Exception:
        return []


def _parse_llm_response(text: str, sql: str) -> tuple[str, str]:
    """LLM 응답에서 description과 enriched 텍스트를 파싱한다."""
    description = ""
    enrichment = ""

    for line in text.strip().splitlines():
        line = line.strip()
        if line.upper().startswith("DESCRIPTION:"):
            description = line.split(":", 1)[1].strip()
        elif line.upper().startswith("ENRICHMENT:"):
            enrichment = line.split(":", 1)[1].strip()

    if not description:
        description = text.strip().splitlines()[0][:100] if text.strip() else ""

    enriched = f"{description} | {enrichment}" if enrichment else description
    return description, enriched


# ── 메인 시더 클래스 ──


class SQLHistorySeeder:
    """SQL 수행이력 벡터 시딩 오케스트레이터."""

    def __init__(
        self,
        *,
        batch_size: int = 20,
        embed_batch_size: int = 64,
        limit: int = 0,
        offset: int = 0,
        dry_run: bool = False,
        recreate_collection: bool = False,
        checkpoint_path: Path | None = None,
    ) -> None:
        self._batch_size = batch_size
        self._embed_batch_size = embed_batch_size
        self._limit = limit
        self._offset = offset
        self._dry_run = dry_run
        self._recreate = recreate_collection
        self._checkpoint_path = checkpoint_path or Path(
            "logs/seed_checkpoint.json"
        )
        self._semaphore = asyncio.Semaphore(3)
        self._now = now_stamp()

        # 통계
        self._stats: dict[str, int] = {
            "extracted": 0,
            "inferred": 0,
            "infer_failed": 0,
            "embedded": 0,
            "stored": 0,
        }

    # ── Step 1: DB에서 SQL 이력 추출 ──

    async def _extract_sql_history(self) -> list[str]:
        """PostgreSQL에서 SQL 수행이력을 추출한다."""
        from sqlalchemy.ext.asyncio import create_async_engine

        dsn = (
            f"postgresql+asyncpg://"
            f"{settings.history_db_user}:{settings.history_db_password}"
            f"@{settings.history_db_host}:{settings.history_db_port}"
            f"/{settings.history_db_name}"
        )
        engine = create_async_engine(dsn)

        query = _EXTRACT_SQL
        if self._limit > 0:
            query += f"\nLIMIT {self._limit}"
        if self._offset > 0:
            query += f"\nOFFSET {self._offset}"

        logger.info("SQL 이력 추출 시작", dsn=dsn.split("@")[1])

        try:
            async with engine.connect() as conn:
                result = await conn.execute(
                    __import__("sqlalchemy").text(query)
                )
                rows = [row[0] for row in result.fetchall() if row[0]]
        finally:
            await engine.dispose()

        self._stats["extracted"] = len(rows)
        logger.info("SQL 이력 추출 완료", count=len(rows))
        return rows

    # ── Step 2+3: LLM으로 설명 추론 + 보강 (단일 호출) ──

    async def _infer_description(self, sql_text: str) -> tuple[str, str]:
        """LLM으로 SQL의 비즈니스 설명을 추론하고 보강한다.

        Returns:
            (description, enriched) 튜플.
        """
        from src.utils.llm import get_llm_client

        client = get_llm_client()

        async with self._semaphore:
            try:
                response = await client.messages.create(
                    model=settings.llm_model,
                    max_tokens=settings.llm_default_max_tokens,
                    system=_INFER_SYSTEM_PROMPT,
                    messages=[{
                        "role": "user",
                        "content": f"SQL:\n{sql_text[:2000]}",
                    }],
                    timeout=settings.llm_default_timeout,
                )
                text = response.content[0].text
                desc, enriched = _parse_llm_response(text, sql_text)
                self._stats["inferred"] += 1
                return desc, enriched
            except Exception as exc:
                logger.warning(
                    "LLM 설명 추론 실패, SQL 원문으로 대체",
                    error=str(exc),
                    sql=sql_text[:80],
                )
                self._stats["infer_failed"] += 1
                fallback = sql_text[:200]
                return fallback, fallback

    # ── Step 4: 배치 처리 (추론 → 레코드 생성) ──

    async def _process_batch(
        self, sql_texts: list[str],
    ) -> list[SQLHistoryRecord]:
        """SQL 텍스트 배치를 레코드로 변환한다."""
        tasks = [self._infer_description(sql) for sql in sql_texts]
        results = await asyncio.gather(*tasks)

        records: list[SQLHistoryRecord] = []
        for sql, (desc, enriched) in zip(sql_texts, results):
            records.append(SQLHistoryRecord(
                sql=sql,
                description=desc,
                enriched=enriched,
                tables_used=_extract_tables(sql),
                seed_dt=self._now,
            ))
        return records

    # ── Step 5: 임베딩 + Qdrant 저장 ──

    async def _embed_and_store(
        self, records: list[SQLHistoryRecord],
    ) -> int:
        """레코드를 임베딩하고 Qdrant에 저장한다."""
        from qdrant_client import QdrantClient
        from qdrant_client.models import (
            Distance,
            PointStruct,
            SparseIndexParams,
            SparseVector,
            SparseVectorParams,
            VectorParams,
        )

        from src.connectors.impl.qdrant_connector import (
            QdrantConnector,
        )

        # 임베딩은 QdrantConnector에 통합됨
        qdrant = QdrantConnector(use_dummy=False)
        collection = settings.qdrant_sql_history_collection

        # Qdrant 클라이언트 (동기)
        client = QdrantClient(
            host=settings.qdrant_host, port=settings.qdrant_port,
        )

        # 컬렉션 생성/재생성
        if self._recreate:
            if client.collection_exists(collection):
                client.delete_collection(collection)
                logger.info("기존 컬렉션 삭제", collection=collection)

        if not client.collection_exists(collection):
            client.create_collection(
                collection_name=collection,
                vectors_config={
                    "dense": VectorParams(
                        size=settings.embedding_dim,
                        distance=Distance.COSINE,
                    ),
                },
                sparse_vectors_config={
                    "sparse": SparseVectorParams(
                        index=SparseIndexParams(),
                    ),
                },
            )
            logger.info("컬렉션 생성 완료", collection=collection)

        # 배치 임베딩 + 업서트
        total_stored = 0
        for i in range(0, len(records), self._embed_batch_size):
            chunk = records[i : i + self._embed_batch_size]
            texts = [r.enriched for r in chunk]

            start = time.perf_counter()
            embeddings = qdrant.encode_batch(texts)
            embed_ms = (time.perf_counter() - start) * 1000

            points: list[PointStruct] = []
            for j, (rec, emb) in enumerate(zip(chunk, embeddings)):
                point_id = self._offset + i + j
                points.append(PointStruct(
                    id=point_id,
                    vector={
                        "dense": emb.dense,
                        "sparse": SparseVector(
                            indices=emb.sparse_indices,
                            values=emb.sparse_values,
                        ),
                    },
                    payload=rec.to_payload(),
                ))

            client.upsert(collection_name=collection, points=points)
            total_stored += len(points)
            self._stats["embedded"] += len(points)
            self._stats["stored"] += len(points)

            logger.info(
                "배치 임베딩+저장 완료",
                batch=f"{i // self._embed_batch_size + 1}",
                count=len(points),
                embed_ms=round(embed_ms, 1),
                total=total_stored,
            )

            # 체크포인트 저장
            self._save_checkpoint(self._offset + i + len(chunk))

        client.close()
        return total_stored

    # ── Step 6: 검증 ──

    async def _verify_storage(self) -> None:
        """샘플 질의로 저장 결과를 검증한다."""
        from qdrant_client import QdrantClient
        from qdrant_client.models import (
            Fusion,
            FusionQuery,
            Prefetch,
            SparseVector,
        )

        from src.connectors.impl.qdrant_connector import (
            QdrantConnector,
        )

        qdrant = QdrantConnector(use_dummy=False)
        collection = settings.qdrant_sql_history_collection

        client = QdrantClient(
            host=settings.qdrant_host, port=settings.qdrant_port,
        )

        logger.info("저장 검증 시작", sample_count=len(_VERIFY_QUERIES))

        for query in _VERIFY_QUERIES:
            emb = qdrant.encode(query)

            results = client.query_points(
                collection_name=collection,
                prefetch=[
                    Prefetch(
                        query=emb.dense,
                        using="dense",
                        limit=10,
                    ),
                    Prefetch(
                        query=SparseVector(
                            indices=emb.sparse_indices,
                            values=emb.sparse_values,
                        ),
                        using="sparse",
                        limit=10,
                    ),
                ],
                query=FusionQuery(fusion=Fusion.RRF),
                limit=3,
            )

            logger.info(
                "검증 질의 결과",
                query=query,
                hit_count=len(results.points),
            )
            for k, pt in enumerate(results.points, 1):
                desc = pt.payload.get("description", "")[:80]
                sql_preview = pt.payload.get("sql", "")[:80]
                logger.info(
                    f"  Top-{k}",
                    score=round(pt.score, 4),
                    description=desc,
                    sql=sql_preview,
                )

        client.close()
        logger.info("저장 검증 완료")

    # ── 체크포인트 ──

    def _save_checkpoint(self, last_offset: int) -> None:
        """진행 상황을 체크포인트 파일에 저장한다."""
        self._checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "last_offset": last_offset,
            "stats": self._stats,
            "timestamp": self._now,
        }
        self._checkpoint_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load_checkpoint(self) -> int | None:
        """체크포인트에서 마지막 오프셋을 로드한다."""
        if not self._checkpoint_path.exists():
            return None
        try:
            data = json.loads(self._checkpoint_path.read_text("utf-8"))
            return data.get("last_offset")
        except Exception:
            return None

    # ── 실행 ──

    async def run(self) -> None:
        """전체 시딩 워크플로우를 실행한다."""
        start = time.perf_counter()
        logger.info(
            "SQL 이력 시딩 시작",
            batch_size=self._batch_size,
            embed_batch_size=self._embed_batch_size,
            limit=self._limit,
            offset=self._offset,
            dry_run=self._dry_run,
        )

        # 1. 추출
        sql_texts = await self._extract_sql_history()
        if not sql_texts:
            logger.warning("추출된 SQL 이력이 없습니다")
            return

        # 2+3. 배치별 LLM 추론
        all_records: list[SQLHistoryRecord] = []
        for i in range(0, len(sql_texts), self._batch_size):
            batch = sql_texts[i : i + self._batch_size]
            records = await self._process_batch(batch)
            all_records.extend(records)

            logger.info(
                "LLM 추론 배치 완료",
                batch=f"{i // self._batch_size + 1}",
                processed=len(all_records),
                total=len(sql_texts),
            )

            # LLM 레이트 리밋 방어
            if i + self._batch_size < len(sql_texts):
                await asyncio.sleep(1.0)

        if self._dry_run:
            logger.info(
                "드라이런 완료 (저장 생략)",
                records=len(all_records),
                stats=self._stats,
            )
            # 드라이런 결과를 JSON으로 출력
            output = [
                {"sql": r.sql[:200], "description": r.description, "enriched": r.enriched}
                for r in all_records[:10]
            ]
            logger.info("샘플 결과", sample=json.dumps(output, ensure_ascii=False, indent=2))
            return

        # 4. 임베딩 + 저장
        stored = await self._embed_and_store(all_records)

        # 5. 검증
        await self._verify_storage()

        elapsed = time.perf_counter() - start
        logger.info(
            "SQL 이력 시딩 완료",
            stored=stored,
            elapsed_sec=round(elapsed, 1),
            stats=self._stats,
        )


# ── CLI 진입점 ──


def _parse_args() -> argparse.Namespace:
    """CLI 인자를 파싱한다."""
    parser = argparse.ArgumentParser(
        description="SQL 수행이력 벡터 시딩 배치 도구",
    )
    parser.add_argument(
        "--batch-size", type=int, default=20,
        help="LLM 추론 배치 크기 (기본: 20)",
    )
    parser.add_argument(
        "--embed-batch-size", type=int, default=64,
        help="임베딩 배치 크기 (기본: 64)",
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="최대 처리 건수 (0=전체, 기본: 0)",
    )
    parser.add_argument(
        "--offset", type=int, default=0,
        help="시작 오프셋 (기본: 0)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="추출+추론만 수행, 저장하지 않음",
    )
    parser.add_argument(
        "--verify-only", action="store_true",
        help="저장 검증만 실행",
    )
    parser.add_argument(
        "--recreate-collection", action="store_true",
        help="컬렉션 삭제 후 재생성",
    )
    parser.add_argument(
        "--checkpoint", type=str, default="logs/seed_checkpoint.json",
        help="체크포인트 파일 경로",
    )
    return parser.parse_args()


async def async_main() -> None:
    """비동기 메인 함수."""
    setup_logging()
    args = _parse_args()

    if args.verify_only:
        seeder = SQLHistorySeeder()
        await seeder._verify_storage()
        return

    seeder = SQLHistorySeeder(
        batch_size=args.batch_size,
        embed_batch_size=args.embed_batch_size,
        limit=args.limit,
        offset=args.offset,
        dry_run=args.dry_run,
        recreate_collection=args.recreate_collection,
        checkpoint_path=Path(args.checkpoint),
    )
    await seeder.run()


def main() -> None:
    """CLI 엔트리포인트."""
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
