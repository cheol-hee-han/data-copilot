"""SQL 수행이력 벡터 시딩 배치 도구.

PostgreSQL(tb_sys_sql_descriptions)에서 SQL 수행이력을 관리하고,
BGE-M3 임베딩(Dense+Sparse)을 생성하여 Qdrant sql_history 컬렉션에 적재한다.

두 가지 배치 모드를 지원한다:
  - infer:  description이 없는 SQL을 추출하여 메타 컨텍스트(테이블/컬럼/코드값)와
            함께 LLM으로 비즈니스 설명을 생성하고 PostgreSQL에 저장
  - embed:  description이 있는 SQL을 추출하여 임베딩을 생성하고 Qdrant에 적재
  - all:    infer → embed 순차 실행 (기본값)

기동 예시::

    # 전체 시딩 (infer → embed)
    python -m src.tools.seed_sql_history

    # 설명 생성만
    python -m src.tools.seed_sql_history --mode infer

    # 임베딩 시딩만
    python -m src.tools.seed_sql_history --mode embed

    # 시스템 코드 필터링
    python -m src.tools.seed_sql_history --system-code BDP

    # 건수 제한 + 드라이런
    python -m src.tools.seed_sql_history --limit 100 --dry-run

    # 검증만 실행
    python -m src.tools.seed_sql_history --verify-only

    # 컬렉션 재생성 후 시딩
    python -m src.tools.seed_sql_history --mode embed --recreate-collection

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
from pathlib import Path
from typing import Any

from src.config import settings
from src.connectors.manager import get_connector_manager
from src.utils.logger import get_logger, setup_logging
from src.utils.sqlglot_analyzer import get_real_tables_with_fallback
from src.utils.timezone import now_stamp

logger = get_logger(__name__)

# ── 상수 ──

_SEED_VERSION = "2.0.0"

# ── SQL: description이 없는 레코드 추출 (infer 대상) ──
_EXTRACT_NO_DESC_SQL = """
SELECT system_code, sql_text
FROM tb_sys_sql_descriptions
WHERE description IS NULL OR TRIM(description) = ''
ORDER BY system_code
""".strip()

# ── SQL: description이 있는 레코드 추출 (embed 대상) ──
_EXTRACT_WITH_DESC_SQL = """
SELECT system_code, sql_text, description, enrichment
FROM tb_sys_sql_descriptions
WHERE description IS NOT NULL AND TRIM(description) != ''
ORDER BY system_code
""".strip()

# ── SQL: description + enrichment 업데이트 ──
_UPDATE_DESC_SQL = """
UPDATE tb_sys_sql_descriptions
SET description = :description, enrichment = :enrichment
WHERE system_code = :system_code AND sql_text = :sql_text
""".strip()

_INFER_SYSTEM_PROMPT = """\
당신은 은행 SQL 분석 전문가입니다.
주어진 SQL 쿼리가 어떤 데이터를 어떤 조건으로 추출하는지
비즈니스 관점에서 간결하게 설명하세요.

## 출력 규칙
- description: 20자~60자 이내의 한국어 비즈니스 설명
- enrichment: 한국어 동의어 2~3개, 영문 표현 2~3개, 관련 금융용어 1~2개를 쉼표로 나열
- SQL 키워드(SELECT, JOIN 등)를 사용하지 마세요
- 테이블명·컬럼명 대신 업무 용어를 사용하세요
- 참조 정보(테이블/컬럼 설명, 코드값)가 있으면 이를 적극 활용하세요
- 반드시 아래 순수 JSON 형식만 출력하세요. JSON 외 마크다운 코드블록(```)이나 설명 텍스트를 포함하지 마세요

## 출력 형식
{
  "description": "20~60자 이내의 한국어 SQL 비즈니스 설명", 
  "enrichment": "..."
}

## Few-shot 예시 (임의 SQL과 임의 테이블로 작성됨)

---

SQL: SELECT CUST_NO, CUST_NM, ACCT_BAL FROM TB_ADW_CST001M a JOIN TB_ADW_ACT001M b ON a.CUST_NO = b.CUST_NO WHERE b.ACCT_BAL > 50000000
{
  "description": "잔액 5천만원 초과 고객의 기본정보와 계좌잔고 조회",
  "enrichment": "고액 예금, VIP 고객, high balance customers, deposit balance, 수신잔액, 자산관리"
}

---

SQL: SELECT BRANCH_CD, COUNT(*) CNT, SUM(LOAN_AMT) TOT FROM TB_BDP_LON001T WHERE EXEC_DT >= '20240101' GROUP BY BRANCH_CD
{
  "description": "2024년 이후 지점별 대출 실행 건수와 금액 합계",
  "enrichment": "여신 실적, 지점별 대출현황, branch loan summary, loan execution, 여신관리, 대출실행"
}

---

SQL: SELECT CUST_NO, OVERDUE_DAYS, OVERDUE_AMT FROM TB_ADW_OVD001M WHERE OVERDUE_DAYS > 90 AND STATUS_CD = '01'
{
  "description": "90일 초과 장기연체 고객의 연체 현황 조회", 
  "enrichment": "장기연체, 부실채권, long-term overdue, delinquent loans, 연체관리, 여신건전성"
}
"""

_INFER_USER_TEMPLATE = """\
[시스템 코드]
{system_code}

[SQL]
{sql_text}

[참조 테이블 목록]
{tables_used}

[테이블/컬럼 설명]
{table_meta}

[관련 코드값]
{code_meta}
"""

_VERIFY_QUERIES = [
    "이번 달 신규 대출 건수",
    "고객 유형별 예금 잔액 현황",
    "분기별 연체율 추이",
]


# ── 데이터 모델 ──


@dataclass
class SQLRecord:
    """시딩 대상 SQL 이력 레코드."""

    system_code: str
    sql_text: str
    description: str = ""
    enriched: str = ""
    tables_used: list[str] = field(default_factory=list)
    source: str = "history_db"
    seed_version: str = _SEED_VERSION
    seed_dt: str = ""

    def to_payload(self) -> dict[str, Any]:
        """Qdrant 포인트 payload 딕셔너리로 변환한다."""
        return {
            "system_code": self.system_code,
            "sql": self.sql_text,
            "description": self.description,
            "enriched": self.enriched,
            "tables_used": self.tables_used,
            "source": self.source,
            "seed_version": self.seed_version,
            "seed_dt": self.seed_dt,
        }


# ── 유틸 ──


def _parse_llm_response(text: str) -> tuple[str, str]:
    """LLM 응답 JSON에서 description과 enrichment를 파싱한다.

    JSON 파싱 실패 시 텍스트 첫 줄을 description으로 사용한다.
    """
    from src.utils.llm import extract_json

    parsed = extract_json(text)
    if isinstance(parsed, dict):
        desc = parsed.get("description", "")
        enrichment = parsed.get("enrichment", "")
        if desc:
            return str(desc), str(enrichment)

    # fallback: JSON 파싱 실패 시 첫 줄 사용
    first_line = text.strip().splitlines()[0][:100] if text.strip() else ""
    return first_line, ""


def _format_table_meta(metas: list[dict]) -> str:
    """MongoDB 테이블 메타를 LLM 프롬프트용 텍스트로 포맷한다."""
    if not metas:
        return "(없음)"
    parts: list[str] = []
    for m in metas:
        name = m.get("name", "")
        alt = m.get("alt_name", "")
        desc = m.get("description", "")
        header = f"- {name}"
        if alt:
            header += f" ({alt})"
        if desc:
            header += f": {desc}"
        parts.append(header)

        columns = m.get("columns", [])
        for col in columns[:20]:  # 컬럼 수 제한
            col_name = col.get("name", "")
            col_alt = col.get("alt_name", "")
            col_desc = col.get("description", "")
            col_line = f"    · {col_name}"
            if col_alt:
                col_line += f" ({col_alt})"
            if col_desc:
                col_line += f": {col_desc}"
            parts.append(col_line)
    return "\n".join(parts)


def _format_code_meta(metas: list[dict]) -> str:
    """MongoDB 코드 메타를 LLM 프롬프트용 텍스트로 포맷한다."""
    if not metas:
        return "(없음)"
    parts: list[str] = []
    for m in metas:
        field_name = m.get("code_field", "")
        field_desc = m.get("code_field_desc", "")
        codes = m.get("codes", {})
        header = f"- {field_name}"
        if field_desc:
            header += f" ({field_desc})"
        codes_str = ", ".join(
            f"{k}={v}" for k, v in list(codes.items())[:10]
        )
        if codes_str:
            header += f": {codes_str}"
        parts.append(header)
    return "\n".join(parts)


def _add_query_filters(
    base_sql: str,
    *,
    system_code: str = "",
    limit: int = 0,
    offset: int = 0,
) -> str:
    """기본 SQL에 system_code 필터, LIMIT, OFFSET을 추가한다."""
    query = base_sql
    if system_code:
        # WHERE 절이 이미 있으므로 AND로 추가
        query += f"\nAND system_code = '{system_code}'"
    if limit > 0:
        query += f"\nLIMIT {limit}"
    if offset > 0:
        query += f"\nOFFSET {offset}"
    return query


# ── 메인 시더 클래스 ──


class SQLHistorySeeder:
    """SQL 수행이력 벡터 시딩 오케스트레이터."""

    def __init__(
        self,
        *,
        mode: str = "all",
        system_code: str = "",
        batch_size: int = 20,
        embed_batch_size: int = 64,
        limit: int = 0,
        offset: int = 0,
        dry_run: bool = False,
        recreate_collection: bool = False,
        checkpoint_path: Path | None = None,
    ) -> None:
        self._mode = mode
        self._system_code = system_code.upper()
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
        self._mgr = get_connector_manager(use_dummy=False)

        # 통계
        self._stats: dict[str, int] = {
            "extracted": 0,
            "inferred": 0,
            "infer_failed": 0,
            "saved_to_db": 0,
            "embedded": 0,
            "stored": 0,
        }

    # ── DB 쿼리 실행 헬퍼 ──

    async def _execute_query(self, query: str) -> list[dict]:
        """HistoryDB 커넥터로 SELECT 쿼리를 실행한다."""
        return await self._mgr.history_db.execute_query(query)

    async def _execute_update(
        self, query: str, params: dict[str, Any],
    ) -> None:
        """HistoryDB 커넥터로 UPDATE 쿼리를 실행한다."""
        from sqlalchemy import text as sa_text
        from sqlalchemy.ext.asyncio import AsyncSession

        engine = self._mgr.history_db._engine
        async with AsyncSession(engine) as session:
            await session.execute(sa_text(query), params)
            await session.commit()

    # ── Step 1a: description 없는 SQL 추출 (infer용) ──

    async def _extract_no_desc(self) -> list[dict]:
        """description이 없는 SQL 레코드를 추출한다."""
        query = _add_query_filters(
            _EXTRACT_NO_DESC_SQL,
            system_code=self._system_code,
            limit=self._limit,
            offset=self._offset,
        )
        logger.info("설명 미생성 SQL 추출 시작")
        rows = await self._execute_query(query)
        self._stats["extracted"] = len(rows)
        logger.info("설명 미생성 SQL 추출 완료", count=len(rows))
        return rows

    # ── Step 1b: description 있는 SQL 추출 (embed용) ──

    async def _extract_with_desc(self) -> list[dict]:
        """description이 있는 SQL 레코드를 추출한다."""
        query = _add_query_filters(
            _EXTRACT_WITH_DESC_SQL,
            system_code=self._system_code,
            limit=self._limit,
            offset=self._offset,
        )
        logger.info("설명 보유 SQL 추출 시작")
        rows = await self._execute_query(query)
        self._stats["extracted"] = len(rows)
        logger.info("설명 보유 SQL 추출 완료", count=len(rows))
        return rows

    # ── Step 2: 테이블 추출 + Mongo 메타 수집 ──

    async def _fetch_table_metas(
        self, tables: list[str],
    ) -> tuple[list[dict], set[str]]:
        """테이블 메타를 조회하고 코드성 컬럼을 식별한다."""
        mongo = self._mgr.mongo
        raw_results = await asyncio.gather(
            *(mongo.search_table_meta("", table_names=[t]) for t in tables),
            return_exceptions=True,
        )
        all_meta: list[dict] = []
        code_columns: set[str] = set()
        for result in raw_results:
            if isinstance(result, Exception) or not isinstance(result, list):
                continue
            all_meta.extend(result)
            for m in result:
                for col in m.get("columns", []):
                    col_name = col.get("name", "").upper()
                    if col_name.endswith(("_CD", "_CODE", "_TYPE", "_YN")):
                        code_columns.add(col_name)
        return all_meta, code_columns

    async def _fetch_code_metas(
        self, code_columns: set[str],
    ) -> list[dict]:
        """코드 메타를 조회한다."""
        if not code_columns:
            return []
        mongo = self._mgr.mongo
        raw_results = await asyncio.gather(
            *(mongo.search_code_meta("", code_names=[c])
              for c in list(code_columns)[:10]),
            return_exceptions=True,
        )
        metas: list[dict] = []
        for result in raw_results:
            if isinstance(result, Exception) or not isinstance(result, list):
                continue
            metas.extend(result)
        return metas

    async def _fetch_meta_context(
        self, sql_text: str,
    ) -> tuple[list[str], str, str]:
        """SQL에서 테이블을 추출하고 MongoDB에서 메타를 수집한다.

        Returns:
            (tables_used, table_meta_text, code_meta_text)
        """
        tables = get_real_tables_with_fallback(sql_text)
        if not tables:
            return [], "(없음)", "(없음)"

        all_table_meta, code_columns = await self._fetch_table_metas(tables)
        code_metas = await self._fetch_code_metas(code_columns)

        return (
            tables,
            _format_table_meta(all_table_meta),
            _format_code_meta(code_metas),
        )

    # ── Step 3: LLM 설명 추론 ──

    async def _infer_description(
        self, row: dict,
    ) -> tuple[str, str, list[str]]:
        """LLM으로 SQL의 비즈니스 설명을 추론한다.

        Returns:
            (description, enriched, tables_used)
        """
        from src.utils.llm import get_llm_client

        system_code = row.get("system_code", "")
        sql_text = row.get("sql_text", "")

        # 메타 컨텍스트 수집
        tables, table_meta_text, code_meta_text = (
            await self._fetch_meta_context(sql_text)
        )

        user_content = _INFER_USER_TEMPLATE.format(
            system_code=system_code,
            sql_text=sql_text[:3000],
            tables_used=", ".join(tables) if tables else "(추출 실패)",
            table_meta=table_meta_text,
            code_meta=code_meta_text,
        )

        client = get_llm_client()

        async with self._semaphore:
            try:
                response = await client.messages.create(
                    model=settings.llm_model,
                    max_tokens=settings.llm_default_max_tokens,
                    system=_INFER_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_content}],
                    timeout=settings.llm_default_timeout,
                )
                text = response.content[0].text
                desc, enriched = _parse_llm_response(text)
                self._stats["inferred"] += 1
                return desc, enriched, tables
            except Exception as exc:
                logger.warning(
                    "LLM 설명 추론 실패",
                    error=str(exc),
                    sql=sql_text[:80],
                )
                self._stats["infer_failed"] += 1
                return "", "", tables

    # ── Step 4a: infer 모드 — 배치 추론 + PG 저장 ──

    async def _run_infer(self) -> None:
        """description 미생성 SQL을 추론하고 PG에 저장한다."""
        rows = await self._extract_no_desc()
        if not rows:
            logger.warning("추론 대상 SQL이 없습니다")
            return

        for i in range(0, len(rows), self._batch_size):
            batch = rows[i : i + self._batch_size]
            tasks = [self._infer_description(row) for row in batch]
            results = await asyncio.gather(*tasks)

            for row, (desc, enrichment, _) in zip(batch, results):
                if not desc:
                    continue
                if not self._dry_run:
                    await self._execute_update(_UPDATE_DESC_SQL, {
                        "description": desc,
                        "enrichment": enrichment,
                        "system_code": row["system_code"],
                        "sql_text": row["sql_text"],
                    })
                    self._stats["saved_to_db"] += 1

            logger.info(
                "infer 배치 완료",
                batch=f"{i // self._batch_size + 1}",
                processed=min(i + self._batch_size, len(rows)),
                total=len(rows),
            )

            # LLM 레이트 리밋 방어
            if i + self._batch_size < len(rows):
                await asyncio.sleep(1.0)

        if self._dry_run:
            logger.info("드라이런 완료 (DB 저장 생략)", stats=self._stats)

    # ── Step 4b: embed 모드 — 임베딩 + Qdrant 저장 ──

    async def _run_embed(self) -> None:
        """description 보유 SQL을 임베딩하여 Qdrant에 저장한다."""
        rows = await self._extract_with_desc()
        if not rows:
            logger.warning("임베딩 대상 SQL이 없습니다")
            return

        # 레코드 생성
        records: list[SQLRecord] = []
        for row in rows:
            sql_text = row.get("sql_text", "")
            desc = row.get("description", "")
            enrichment = row.get("enrichment", "")
            enriched = f"{desc} | {enrichment}" if enrichment else desc
            tables = get_real_tables_with_fallback(sql_text)
            records.append(SQLRecord(
                system_code=row.get("system_code", ""),
                sql_text=sql_text,
                description=desc,
                enriched=enriched,
                tables_used=tables,
                seed_dt=self._now,
            ))

        if self._dry_run:
            sample = [
                {
                    "system_code": r.system_code,
                    "sql": r.sql_text[:200],
                    "description": r.description,
                    "tables_used": r.tables_used,
                }
                for r in records[:10]
            ]
            logger.info(
                "드라이런 완료 (저장 생략)",
                records=len(records),
                sample=json.dumps(sample, ensure_ascii=False, indent=2),
            )
            return

        # 임베딩 + Qdrant 저장
        stored = self._embed_and_store(records)
        logger.info("임베딩 저장 완료", stored=stored)

    def _embed_and_store(
        self, records: list[SQLRecord],
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

        qdrant_connector = self._mgr.qdrant
        collection = settings.qdrant_sql_history_collection

        # Qdrant 클라이언트 (동기, 배치 업서트용)
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
            embeddings = qdrant_connector.encode_batch(texts)
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

    # ── 검증 ──

    def _verify_storage(self) -> None:
        """샘플 질의로 저장 결과를 검증한다."""
        from qdrant_client import QdrantClient
        from qdrant_client.models import (
            Fusion,
            FusionQuery,
            Prefetch,
            SparseVector,
        )

        qdrant_connector = self._mgr.qdrant
        collection = settings.qdrant_sql_history_collection

        client = QdrantClient(
            host=settings.qdrant_host, port=settings.qdrant_port,
        )

        logger.info("저장 검증 시작", sample_count=len(_VERIFY_QUERIES))

        for query in _VERIFY_QUERIES:
            emb = qdrant_connector.encode(query)

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

    # ── 실행 ──

    async def run(self) -> None:
        """배치 모드에 따라 워크플로우를 실행한다."""
        start = time.perf_counter()
        logger.info(
            "SQL 이력 시딩 시작",
            mode=self._mode,
            system_code=self._system_code or "(전체)",
            batch_size=self._batch_size,
            limit=self._limit,
            offset=self._offset,
            dry_run=self._dry_run,
        )

        # 커넥터 초기화
        await self._mgr.connect_all()

        try:
            if self._mode in ("infer", "all"):
                await self._run_infer()

            if self._mode in ("embed", "all"):
                await self._run_embed()
                if not self._dry_run:
                    self._verify_storage()
        finally:
            await self._mgr.disconnect_all()

        elapsed = time.perf_counter() - start
        logger.info(
            "SQL 이력 시딩 완료",
            mode=self._mode,
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
        "--mode", choices=["infer", "embed", "all"], default="all",
        help="실행 모드: infer(설명 생성), embed(임베딩 시딩), all(전체) (기본: all)",
    )
    parser.add_argument(
        "--system-code", type=str, default="",
        help="시스템 코드 필터 (BDP/ADW/CRP, 미지정 시 전체)",
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
        help="Qdrant 컬렉션 삭제 후 재생성 (embed 모드에서만 유효)",
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
        mgr = get_connector_manager(use_dummy=False)
        await mgr.connect_all()
        try:
            seeder._verify_storage()
        finally:
            await mgr.disconnect_all()
        return

    seeder = SQLHistorySeeder(
        mode=args.mode,
        system_code=args.system_code,
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
