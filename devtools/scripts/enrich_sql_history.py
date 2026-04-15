"""SQL 수행이력 문서 보강 및 Qdrant 적재 배치 스크립트.

두 가지 소스를 지원한다:
  - dummy  : 더미 데이터 10,000건 + LLM 보강 후 JSON 출력 (기존 동작, 하위호환)
  - postgres: 폐쇄망 PostgreSQL sql_exec_history 테이블 → BGE-M3 임베딩 → Qdrant upsert

모드(--mode):
  - generate-desc : description 없는 행에 LLM으로 설명 생성 후 임베딩
  - direct        : description 있는 행만 임베딩, 없으면 skip

증분 전략(--since-last-run 기본):
  - --full            : 전체 재처리
  - --since <ISO>     : 지정 시점 이후 행만 처리
  - --since-last-run  : 상태파일 last_updated_at 이후 행 처리 (기본)
  - --resume-from <id>: 특정 id 이후부터 재시작

point_id 전략 (분리형 write-back):
  - Postgres PK는 로깅/WHERE 식별용으로만 사용. Qdrant point_id로 쓰지 않는다.
  - 임베딩 시점에 배치가 uuid4()를 생성하여 Qdrant upsert 후 Postgres에 write-back.
  - qdrant_point_id 컬럼(기본: qdrant_point_id)에 기록. IS NULL이면 신규, NOT NULL이면 재사용(덮어쓰기).
  - --point-id-column 으로 컬럼명 변경 가능.

전략 문서 참조: docs/strategy-proposals/embedding-search-strategy.md

사용법:
    # [하위호환] 인자 없이 실행 → dummy 더미 + LLM 보강
    python devtools/scripts/enrich_sql_history.py

    # [폐쇄망 운영] Postgres → BGE-M3 → Qdrant (증분)
    python devtools/scripts/enrich_sql_history.py \\
        --source postgres --mode direct --since-last-run

    # dry-run (대상 건수만 출력, Qdrant·Postgres 미변경)
    python devtools/scripts/enrich_sql_history.py \\
        --source postgres --mode direct --full --dry-run

    # reconcile-only (Qdrant 잔류 고아 point 삭제)
    python devtools/scripts/enrich_sql_history.py \\
        --source postgres --mode direct --reconcile-deletes

    # generate-desc 모드, description write-back 비활성
    python devtools/scripts/enrich_sql_history.py \\
        --source postgres --mode generate-desc --no-description-write-back
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# ── .env 로드 ─────────────────────────────────────────────────────────────
_env_path = Path(__file__).resolve().parent.parent.parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path, encoding="utf-8")

# ── 기본 경로 상수 ────────────────────────────────────────────────────────
_SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_PATH = _SCRIPT_DIR / "enriched_sql_history.json"
DEFAULT_STATE_FILE = _SCRIPT_DIR / ".reembed_state.json"

# ── 임베딩·Qdrant 설정 ────────────────────────────────────────────────────
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
QDRANT_URL = f"http://{QDRANT_HOST}:{QDRANT_PORT}"
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1024"))
EMBEDDING_BATCH_SIZE = int(os.getenv("EMBEDDING_BATCH_SIZE", "64"))
USE_FP16 = os.getenv("EMBEDDING_USE_FP16", "false").lower() == "true"

# ── LLM 보강 프롬프트 (기존 로직 유지) ───────────────────────────────────
ENRICHMENT_PROMPT = """\
다음 SQL 설명에 대해 동의어·유의어·영어 표현·관련 비즈니스 용어를 생성하세요.

원문: {description}

요구사항:
- 한국어 동의어/유의어 2~3개
- 영어 번역 및 유사 표현 2~3개
- 관련 금융/비즈니스 용어 1~2개
- 쉼표 구분 단일 라인으로 출력
- 원문을 반복하지 말 것

출력 예시: 사업부 분기 매출 현황, 팀별 분기 실적, \
quarterly revenue by department, division quarterly performance"""


# ══════════════════════════════════════════════════════════════════════════
# 유틸리티
# ══════════════════════════════════════════════════════════════════════════

class BatchFailedError(Exception):
    """배치 처리 실패 — 상태파일을 갱신하지 않아 다음 실행이 동일 커서에서 재시작한다."""


_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _validate_identifier(name: str, label: str) -> str:
    """SQL identifier(테이블명·컬럼명) 화이트리스트 검증.

    f-string으로 SQL에 삽입되는 식별자에 대해 영문/숫자/언더스코어만 허용하여
    CLI 운영자 오타·악의 입력으로 인한 SQL 파괴를 방어한다.
    """
    if not _IDENT_RE.fullmatch(name):
        raise SystemExit(f"잘못된 SQL identifier: {label}={name!r}")
    return name


def _new_point_id() -> str:
    """배치 임베딩 시점에 사용할 신규 Qdrant point_id(uuid4)를 생성한다.

    Postgres PK와 분리된 독립적 UUID를 사용한다. 생성된 UUID는
    Qdrant upsert 후 Postgres qdrant_point_id 컬럼에 write-back한다.

    Returns:
        UUID4 문자열.
    """
    return str(uuid.uuid4())


def _coerce_point_id(raw: Any) -> str:
    """DB에서 읽은 qdrant_point_id 값을 문자열로 정규화한다.

    Args:
        raw: asyncpg가 반환한 uuid.UUID 객체 또는 문자열.

    Returns:
        소문자 하이픈 포함 UUID 문자열.
    """
    if isinstance(raw, uuid.UUID):
        return str(raw)
    return str(raw)


async def _with_retries(
    coro_factory: Any,
    attempts: int = 3,
    base_delay: float = 2.0,
    max_delay: float = 30.0,
) -> Any:
    """지수 백오프로 코루틴을 재시도한다.

    Args:
        coro_factory: 호출 시 코루틴을 반환하는 callable (lambda 또는 함수).
        attempts: 최대 시도 횟수.
        base_delay: 첫 재시도 대기 시간(초).
        max_delay: 최대 대기 시간(초).

    Returns:
        코루틴 결과.

    Raises:
        마지막 시도 실패 시 원본 예외를 re-raise.
    """
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return await coro_factory()
        except Exception as exc:
            last_exc = exc
            if attempt < attempts - 1:
                delay = min(base_delay * (2 ** attempt), max_delay)
                await asyncio.sleep(delay)
    raise last_exc  # type: ignore[misc]


def _load_state(state_file: Path) -> dict[str, Any]:
    """상태파일을 로드한다. 없으면 빈 dict 반환."""
    if state_file.exists():
        with open(state_file, encoding="utf-8") as f:
            result: dict[str, Any] = json.load(f)
            return result
    return {}


def _save_state(state_file: Path, state: dict[str, Any]) -> None:
    """상태파일을 atomic write로 저장한다.

    tmp → fsync → rename 패턴으로 부분 쓰기를 방지한다.
    """
    tmp = state_file.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(state_file)


# ══════════════════════════════════════════════════════════════════════════
# BGE-M3 임베딩 (seed_qdrant.py에서 로직 인라인 복사)
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class _EmbedResult:
    """BGE-M3 임베딩 결과.

    Attributes:
        dense: Dense 벡터 (1024-dim float 리스트).
        sparse_indices: Sparse 토큰 인덱스 리스트.
        sparse_values: Sparse 토큰 가중치 리스트.
    """

    dense: list[float] = field(default_factory=list)
    sparse_indices: list[int] = field(default_factory=list)
    sparse_values: list[float] = field(default_factory=list)


class _EmbedEngine:
    """BGE-M3 임베딩 엔진 (seed_qdrant.py 패턴 재사용).

    BGE-M3는 CPU 추론이므로 asyncio.to_thread로 이벤트 루프 블로킹을 방지한다.
    """

    def __init__(self) -> None:
        self._model: Any = None

    def _ensure_loaded(self) -> None:
        """BGE-M3 모델을 최초 1회 로드한다."""
        if self._model is not None:
            return
        from FlagEmbedding import BGEM3FlagModel

        cache_dir = os.getenv("EMBEDDING_CACHE_PATH", "")
        kwargs: dict[str, Any] = {
            "model_name_or_path": EMBEDDING_MODEL,
            "use_fp16": USE_FP16,
        }
        if cache_dir:
            kwargs["cache_dir"] = cache_dir
        print(f"  모델 로딩: {EMBEDDING_MODEL}")
        self._model = BGEM3FlagModel(**kwargs)
        print("  모델 로딩 완료")

    def encode_batch_sync(
        self, texts: list[str], batch_size: int
    ) -> list[_EmbedResult]:
        """텍스트 배치를 Dense + Sparse 벡터로 동기 변환한다 (CPU 바운드).

        Args:
            texts: 임베딩할 텍스트 리스트.
            batch_size: 인코딩 배치 크기.

        Returns:
            _EmbedResult 리스트.
        """
        self._ensure_loaded()
        output = self._model.encode(
            texts,
            batch_size=batch_size,
            return_dense=True,
            return_sparse=True,
            return_colbert_vecs=False,
        )
        dense_vecs = output["dense_vecs"]
        sparse_weights = output["lexical_weights"]

        results: list[_EmbedResult] = []
        for i in range(len(texts)):
            dense: list[float] = dense_vecs[i].tolist()
            sw = sparse_weights[i]
            indices: list[int] = []
            values: list[float] = []
            for token_id, weight in sorted(sw.items()):
                val = float(weight)
                if val > 0:
                    indices.append(int(token_id))
                    values.append(val)
            results.append(
                _EmbedResult(
                    dense=dense,
                    sparse_indices=indices,
                    sparse_values=values,
                )
            )
        return results

    async def encode_batch(
        self, texts: list[str], batch_size: int
    ) -> list[_EmbedResult]:
        """asyncio.to_thread로 이벤트 루프 비블로킹 임베딩.

        Args:
            texts: 임베딩할 텍스트 리스트.
            batch_size: 인코딩 배치 크기.

        Returns:
            _EmbedResult 리스트.
        """
        return await asyncio.to_thread(
            self.encode_batch_sync, texts, batch_size,
        )


# ══════════════════════════════════════════════════════════════════════════
# LLM 클라이언트 (기존 로직 유지)
# ══════════════════════════════════════════════════════════════════════════

def _get_llm_client() -> tuple[str, Any]:
    """LLM 클라이언트를 생성한다."""
    provider = os.getenv("LLM_PROVIDER", "anthropic")
    if provider == "anthropic":
        import anthropic
        return "anthropic", anthropic.Anthropic(
            api_key=os.getenv("ANTHROPIC_API_KEY"),
        )
    import openai
    return "openai", openai.OpenAI(
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_BASE_URL", ""),
    )


def _call_anthropic(client: Any, description: str) -> str:
    """Anthropic API로 문서 보강을 수행한다."""
    model = os.getenv("LLM_MODEL", "claude-sonnet-4-20250514")
    response = client.messages.create(
        model=model,
        max_tokens=200,
        messages=[{
            "role": "user",
            "content": ENRICHMENT_PROMPT.format(description=description),
        }],
    )
    return str(response.content[0].text.strip())


def _call_openai(client: Any, description: str) -> str:
    """OpenAI Compatible API로 문서 보강을 수행한다."""
    model = os.getenv("LLM_MODEL", "gpt-4o")
    response = client.chat.completions.create(
        model=model,
        max_tokens=200,
        messages=[{
            "role": "user",
            "content": ENRICHMENT_PROMPT.format(description=description),
        }],
    )
    content: str = response.choices[0].message.content.strip()
    return content


def enrich_descriptions(sql_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """sql_history 데이터의 description을 LLM으로 보강한다 (dummy 모드용).

    Args:
        sql_data: description 필드를 가진 SQL 이력 딕셔너리 리스트.

    Returns:
        enriched 필드가 추가된 딕셔너리 리스트.
    """
    provider, client = _get_llm_client()
    call_fn = _call_anthropic if provider == "anthropic" else _call_openai

    total = len(sql_data)
    enriched_count = 0
    error_count = 0

    for i, item in enumerate(sql_data):
        desc = item.get("description", "")
        if not desc:
            continue
        try:
            synonyms = call_fn(client, desc)
            item["enriched"] = f"{desc} | {synonyms}"
            enriched_count += 1
        except Exception as e:
            item["enriched"] = desc
            error_count += 1
            if error_count <= 5:
                print(f"  [오류] {i}: {e}")

        if (i + 1) % 100 == 0:
            print(
                f"  진행: {i + 1}/{total} "
                f"(성공 {enriched_count}, 실패 {error_count})"
            )
            time.sleep(1)

    return sql_data


# ══════════════════════════════════════════════════════════════════════════
# Qdrant 컬렉션 보장
# ══════════════════════════════════════════════════════════════════════════

def _ensure_collection(client: Any, collection: str) -> None:
    """sql_history 컬렉션이 없으면 Dense + Sparse Named Vectors로 생성한다.

    seed_qdrant.py의 컬렉션 스키마와 동일하게 생성한다.
    """
    from qdrant_client.models import (
        Distance,
        SparseIndexParams,
        SparseVectorParams,
        VectorParams,
    )

    if not client.collection_exists(collection):
        client.create_collection(
            collection_name=collection,
            vectors_config={
                "dense": VectorParams(
                    size=EMBEDDING_DIM,
                    distance=Distance.COSINE,
                ),
            },
            sparse_vectors_config={
                "sparse": SparseVectorParams(
                    index=SparseIndexParams(),
                ),
            },
        )
        print(f"  컬렉션 생성: {collection}")


# ══════════════════════════════════════════════════════════════════════════
# Postgres 처리 (폐쇄망 운영 배치)
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class _RunStats:
    """배치 실행 통계.

    Attributes:
        upserted: Qdrant upsert 성공 건수.
        deleted: Qdrant delete 건수.
        skipped: skip(description 없음 등) 건수.
        failed: 재시도 소진 후 실패 건수.
        error_samples: 실패 샘플 (최대 5건).
        last_updated_at: 처리된 마지막 updated_at.
    """

    upserted: int = 0
    deleted: int = 0
    skipped: int = 0
    failed: int = 0
    error_samples: list[str] = field(default_factory=list)
    last_updated_at: str = ""

    def add_error(self, row_id: Any, error: str) -> None:
        """에러 샘플을 최대 5건까지 기록한다."""
        self.failed += 1
        if len(self.error_samples) < 5:
            self.error_samples.append(f"row_id={row_id} error={error}")


@dataclass
class _CliArgs:
    """파싱된 CLI 인자.

    필드는 argparse 결과를 그대로 담는다.
    """

    mode: str
    source: str
    pg_table: str
    id_column: str
    text_column: str
    description_column: str
    embed_flag_column: str
    embed_flag_active_value: str
    updated_at_column: str
    embedded_at_column: str
    embed_version_column: str
    point_id_column: str
    embed_version: str
    pg_fetch_size: int
    no_description_write_back: bool
    full: bool
    since: str | None
    since_last_run: bool
    state_file: Path
    resume_from: str | None
    reconcile_deletes: bool
    reconcile_chunk_size: int
    collection: str
    no_upsert: bool
    json_output: Path | None
    embed_batch_size: int
    llm_batch_size: int
    concurrency: int
    retry_attempts: int
    retry_base_delay: float
    retry_max_delay: float
    write_back_status: bool
    dry_run: bool
    limit: int | None


def _build_incremental_query(args: _CliArgs, since_dt: str) -> tuple[str, dict[str, Any]]:
    """증분 SELECT 쿼리와 파라미터를 빌드한다.

    qdrant_point_id 컬럼을 함께 조회하여 배치에서 신규/재사용 여부를 판단한다.

    Args:
        args: CLI 인자.
        since_dt: updated_at 하한 (ISO datetime 문자열).

    Returns:
        (query_string, params_dict) 튜플.
    """
    cols = ", ".join([
        args.id_column,
        args.text_column,
        args.description_column,
        args.embed_flag_column,
        args.updated_at_column,
        args.point_id_column,
    ])
    limit_clause = f"LIMIT {args.limit}" if args.limit else ""
    query = (
        f"SELECT {cols} FROM {args.pg_table} "
        f"WHERE {args.updated_at_column} > $1 "
        f"ORDER BY {args.updated_at_column}, {args.id_column} "
        f"{limit_clause}"
    )
    return query, {"since": since_dt}


async def _generate_description_llm(
    description: str,
    call_fn: Any,
    client: Any,
    args: _CliArgs,
) -> str:
    """LLM으로 description을 보강한다 (asyncio.to_thread 경유).

    Args:
        description: 원본 설명 텍스트.
        call_fn: LLM 호출 함수 (_call_anthropic 또는 _call_openai).
        client: LLM 클라이언트 인스턴스.
        args: 재시도 설정이 담긴 CLI 인자.

    Returns:
        보강된 설명 텍스트.
    """
    async def _call() -> str:
        return await asyncio.to_thread(call_fn, client, description)

    enriched: str = await _with_retries(
        _call,
        attempts=args.retry_attempts,
        base_delay=args.retry_base_delay,
        max_delay=args.retry_max_delay,
    )
    return f"{description} | {enriched}"


async def _upsert_batch(
    qdrant_client: Any,
    collection: str,
    rows: list[dict[str, Any]],
    embed_results: list[_EmbedResult],
    args: _CliArgs,
) -> None:
    """행 배치를 Qdrant에 upsert한다.

    각 row의 '_point_id' 필드(배치가 결정한 uuid4 문자열)를 Qdrant point_id로 사용한다.
    Postgres PK(_raw_id)는 payload에만 포함하며 point_id로 쓰지 않는다.

    Args:
        qdrant_client: QdrantClient 인스턴스.
        collection: 컬렉션 이름.
        rows: DB row 딕셔너리 리스트 (payload). '_point_id' 필드 필수.
        embed_results: rows에 대응하는 임베딩 결과 리스트.
        args: 재시도 설정이 담긴 CLI 인자.
    """
    from qdrant_client.models import PointStruct, SparseVector

    points = []
    for row, emb in zip(rows, embed_results):
        point_id = row["_point_id"]  # 배치가 결정한 uuid4 — PK와 분리
        payload = {
            k: v for k, v in row.items()
            if not k.startswith("_")
        }
        points.append(
            PointStruct(
                id=point_id,
                vector={
                    "dense": emb.dense,
                    "sparse": SparseVector(
                        indices=emb.sparse_indices,
                        values=emb.sparse_values,
                    ),
                },
                payload=payload,
            )
        )

    async def _do_upsert() -> None:
        qdrant_client.upsert(collection_name=collection, points=points)

    await _with_retries(
        _do_upsert,
        attempts=args.retry_attempts,
        base_delay=args.retry_base_delay,
        max_delay=args.retry_max_delay,
    )


async def _delete_batch(
    qdrant_client: Any,
    collection: str,
    point_ids: list[int | str],
    args: _CliArgs,
) -> None:
    """point_id 목록을 Qdrant에서 삭제한다.

    Args:
        qdrant_client: QdrantClient 인스턴스.
        collection: 컬렉션 이름.
        point_ids: 삭제할 Qdrant point_id 리스트.
        args: 재시도 설정이 담긴 CLI 인자.
    """
    from qdrant_client.models import PointIdsList

    async def _do_delete() -> None:
        qdrant_client.delete(
            collection_name=collection,
            points_selector=PointIdsList(points=point_ids),
        )

    await _with_retries(
        _do_delete,
        attempts=args.retry_attempts,
        base_delay=args.retry_base_delay,
        max_delay=args.retry_max_delay,
    )


async def _write_back_upsert(
    pg_conn: Any,
    table: str,
    id_col: str,
    point_id_col: str,
    embedded_at_col: str,
    embed_version_col: str,
    description_col: str,
    embed_version: str,
    rows: list[dict[str, Any]],
    write_description: bool,
) -> None:
    """Qdrant upsert 성공 행의 qdrant_point_id, embedded_at, embed_version을 갱신한다.

    각 행을 개별 UPDATE로 처리하여 point_id를 정확히 기록한다.
    write_description=True이면 sql_description 컬럼도 함께 갱신한다(generate-desc 모드).

    실패 시 BatchFailedError를 발생시킨다. 호출 전 Qdrant upsert가 성공했으므로
    실패 시 고아 발생 가능 — 경고 메시지를 출력하고 reconcile 모드 안내.

    Args:
        pg_conn: asyncpg 연결 객체.
        table: 대상 테이블명.
        id_col: PK 컬럼명.
        point_id_col: qdrant_point_id 컬럼명.
        embedded_at_col: 처리 시각 컬럼명.
        embed_version_col: 임베딩 버전 컬럼명.
        description_col: sql_description 컬럼명.
        embed_version: 임베딩 버전 문자열 (예: 'bge-m3-v1').
        rows: upsert 완료 row 딕셔너리 리스트. '_raw_id', '_point_id', 'description' 필드 필수.
        write_description: True이면 description 컬럼도 write-back.
    """
    if not rows:
        return

    async with pg_conn.transaction():
        for row in rows:
            raw_id = row["_raw_id"]
            point_id = row["_point_id"]
            if write_description:
                await pg_conn.execute(
                    f"UPDATE {table} SET "
                    f"{point_id_col} = $1, "
                    f"{embedded_at_col} = NOW(), "
                    f"{embed_version_col} = $2, "
                    f"{description_col} = $3 "
                    f"WHERE {id_col} = $4",
                    uuid.UUID(point_id),
                    embed_version,
                    row.get("description", ""),
                    raw_id,
                )
            else:
                await pg_conn.execute(
                    f"UPDATE {table} SET "
                    f"{point_id_col} = $1, "
                    f"{embedded_at_col} = NOW(), "
                    f"{embed_version_col} = $2 "
                    f"WHERE {id_col} = $3",
                    uuid.UUID(point_id),
                    embed_version,
                    raw_id,
                )


async def _write_back_delete(
    pg_conn: Any,
    table: str,
    id_col: str,
    point_id_col: str,
    embedded_at_col: str,
    embed_version_col: str,
    embed_version: str,
    raw_ids: list[Any],
) -> None:
    """Qdrant delete 성공 행의 qdrant_point_id를 NULL로 갱신한다.

    Args:
        pg_conn: asyncpg 연결 객체.
        table: 대상 테이블명.
        id_col: PK 컬럼명.
        point_id_col: qdrant_point_id 컬럼명.
        embedded_at_col: 처리 시각 컬럼명.
        embed_version_col: 임베딩 버전 컬럼명.
        embed_version: 임베딩 버전 문자열.
        raw_ids: 갱신 대상 PK 값 리스트.
    """
    if not raw_ids:
        return
    async with pg_conn.transaction():
        await pg_conn.execute(
            f"UPDATE {table} SET "
            f"{point_id_col} = NULL, "
            f"{embedded_at_col} = NOW(), "
            f"{embed_version_col} = $1 "
            f"WHERE {id_col} = ANY($2)",
            embed_version,
            raw_ids,
        )


async def _run_postgres_mode(args: _CliArgs) -> None:
    """Postgres 소스 배치 처리 메인 루프.

    증분 조회 → 임베딩 → Qdrant upsert/delete → write-back → 상태파일 갱신.

    Args:
        args: CLI 인자.

    Raises:
        BatchFailedError: 재시도 소진 후 실패 시 (상태파일 미갱신).
    """
    import asyncpg

    # ── 상태파일 / 증분 커서 결정 ────────────────────────────────────────
    state = _load_state(args.state_file)
    if args.full:
        since_dt = "1970-01-01T00:00:00"
    elif args.since:
        since_dt = args.since
    elif args.since_last_run:
        since_dt = state.get("last_updated_at", "1970-01-01T00:00:00")
    else:
        since_dt = state.get("last_updated_at", "1970-01-01T00:00:00")

    # ── Postgres 연결 ──────────────────────────────────────────────────
    pg_dsn = _build_pg_dsn()
    pg_conn = await asyncpg.connect(pg_dsn)

    # ── dry-run: 대상 건수만 출력 후 종료 ────────────────────────────
    if args.dry_run:
        count_query = (
            f"SELECT COUNT(*) FROM {args.pg_table} "
            f"WHERE {args.updated_at_column} > $1"
        )
        if args.limit:
            count_query += f" LIMIT {args.limit}"
        count = await pg_conn.fetchval(count_query, since_dt)
        await pg_conn.close()
        print(f"[dry-run] 대상 건수: {count}")
        print(f"[dry-run] since: {since_dt}")
        print(f"[dry-run] 쿼리 예시:")
        q, _ = _build_incremental_query(args, since_dt)
        print(f"  {q}")
        return

    # ── BGE-M3 모델 초기화 ────────────────────────────────────────────
    embed_engine = _EmbedEngine()

    # ── Qdrant 클라이언트 초기화 ──────────────────────────────────────
    qdrant_client: Any = None
    if not args.no_upsert:
        from qdrant_client import QdrantClient
        qdrant_client = QdrantClient(url=QDRANT_URL, timeout=120)
        _ensure_collection(qdrant_client, args.collection)

    # ── LLM 클라이언트 초기화 (generate-desc 모드) ────────────────────
    llm_call_fn: Any = None
    llm_client: Any = None
    if args.mode == "generate-desc":
        provider, llm_client = _get_llm_client()
        llm_call_fn = (
            _call_anthropic if provider == "anthropic" else _call_openai
        )

    # ── Semaphore 초기화 ──────────────────────────────────────────────
    sem = asyncio.Semaphore(args.concurrency)

    stats = _RunStats()
    upsert_queue: list[dict[str, Any]] = []
    # 삭제 큐: (raw_id, existing_point_id | None) 튜플 리스트
    delete_queue: list[tuple[Any, str | None]] = []
    # upsert write-back 대상: row 전체 딕셔너리 (point_id 포함)
    upsert_rows_for_writeback: list[dict[str, Any]] = []
    # delete write-back 대상: raw_id 리스트
    delete_ids_for_writeback: list[Any] = []

    async def _flush_upsert(force: bool = False) -> None:
        """임베딩 큐를 배치 단위로 처리한다.

        각 row의 '_point_id'(uuid4 문자열)로 Qdrant upsert 후,
        성공 시 upsert_rows_for_writeback에 쌓아 write-back 단계에서 처리한다.
        Qdrant upsert 실패 시 재시도 3회 → 실패 시 배치 중단 (Postgres UPDATE 미실행).

        Args:
            force: True이면 잔여 건수 무관 즉시 처리.
        """
        nonlocal upsert_queue, upsert_rows_for_writeback

        while upsert_queue and (
            force or len(upsert_queue) >= args.embed_batch_size
        ):
            batch = upsert_queue[:args.embed_batch_size]
            upsert_queue = upsert_queue[args.embed_batch_size:]

            embed_texts = [r["_embed_text"] for r in batch]
            async with sem:
                try:
                    embed_results = await _with_retries(
                        lambda texts=embed_texts: embed_engine.encode_batch(
                            texts, args.embed_batch_size
                        ),
                        attempts=args.retry_attempts,
                        base_delay=args.retry_base_delay,
                        max_delay=args.retry_max_delay,
                    )
                except Exception as exc:
                    for r in batch:
                        stats.add_error(r["_raw_id"], str(exc))
                    continue

            # JSON 중간 저장
            if args.json_output:
                _append_json_output(
                    args.json_output,
                    batch,
                    embed_results,
                )

            if not args.no_upsert and qdrant_client is not None:
                async with sem:
                    try:
                        await _upsert_batch(
                            qdrant_client,
                            args.collection,
                            batch,
                            embed_results,
                            args,
                        )
                    except Exception as exc:
                        for r in batch:
                            stats.add_error(r["_raw_id"], str(exc))
                        raise BatchFailedError(
                            f"Qdrant upsert 실패 (재시도 소진): {exc}\n"
                            "  → Postgres UPDATE 미실행. 다음 실행이 동일 커서에서 재시작합니다."
                        )

            stats.upserted += len(batch)
            upsert_rows_for_writeback.extend(batch)

    async def _flush_delete() -> None:
        """삭제 큐를 Qdrant에서 일괄 삭제하고 delete write-back 대상을 수집한다.

        qdrant_point_id IS NULL 행은 Qdrant 삭제 없이 no-op.
        qdrant_point_id IS NOT NULL 행은 해당 point_id로 Qdrant delete 후
        delete_ids_for_writeback에 쌓아 write-back에서 NULL 처리.
        """
        nonlocal delete_queue, delete_ids_for_writeback

        if not delete_queue:
            return

        chunk = delete_queue[:]
        delete_queue = []

        # qdrant_point_id가 있는 행만 실제 Qdrant delete 대상
        to_delete_in_qdrant: list[str] = []
        to_writeback_raw_ids: list[Any] = []

        for raw_id, existing_point_id in chunk:
            if existing_point_id is not None:
                to_delete_in_qdrant.append(existing_point_id)
                to_writeback_raw_ids.append(raw_id)
            # IS NULL → 애초에 임베딩 안 된 행, no-op

        if not args.no_upsert and qdrant_client is not None and to_delete_in_qdrant:
            async with sem:
                try:
                    await _delete_batch(
                        qdrant_client,
                        args.collection,
                        to_delete_in_qdrant,
                        args,
                    )
                    stats.deleted += len(to_delete_in_qdrant)
                    delete_ids_for_writeback.extend(to_writeback_raw_ids)
                except Exception as exc:
                    for raw_id, _ in chunk:
                        stats.add_error(raw_id, str(exc))
        else:
            stats.deleted += len(to_delete_in_qdrant)
            delete_ids_for_writeback.extend(to_writeback_raw_ids)

    # ── 서버 커서 스트리밍 ────────────────────────────────────────────
    query, _ = _build_incremental_query(args, since_dt)
    total_fetched = 0
    max_updated_at = since_dt

    async with pg_conn.transaction():
        cursor = pg_conn.cursor(query, since_dt, prefetch=args.pg_fetch_size)
        async for row in cursor:
            total_fetched += 1
            raw_id = row[args.id_column]
            sql_text = row[args.text_column] or ""
            description = row[args.description_column] or ""
            flag = row[args.embed_flag_column] or ""
            updated_at = row[args.updated_at_column]
            existing_point_id_raw = row[args.point_id_column]
            existing_point_id: str | None = (
                _coerce_point_id(existing_point_id_raw)
                if existing_point_id_raw is not None
                else None
            )

            if updated_at:
                max_updated_at = str(updated_at.isoformat()
                    if hasattr(updated_at, "isoformat") else updated_at)

            if flag == args.embed_flag_active_value:
                # ── 임베딩 대상 ─────────────────────────────────────
                embed_text = description or sql_text
                if not embed_text:
                    stats.skipped += 1
                    continue

                if args.mode == "generate-desc" and not description:
                    # LLM으로 설명 생성
                    async with sem:
                        try:
                            embed_text = await _generate_description_llm(
                                sql_text, llm_call_fn, llm_client, args
                            )
                            description = embed_text  # write-back용 갱신
                        except Exception as exc:
                            stats.add_error(raw_id, str(exc))
                            continue
                elif args.mode == "direct" and not description:
                    stats.skipped += 1
                    if len(stats.error_samples) < 5:
                        stats.error_samples.append(
                            f"row_id={raw_id} error=description_null_skip"
                        )
                    continue

                # point_id 결정: IS NULL → 신규 uuid4, NOT NULL → 기존 재사용
                point_id = (
                    existing_point_id
                    if existing_point_id is not None
                    else _new_point_id()
                )

                upsert_queue.append({
                    "_raw_id": raw_id,
                    "_point_id": point_id,
                    "_embed_text": embed_text,
                    "sql": sql_text,
                    "description": description,
                })
                await _flush_upsert()
            else:
                # ── 삭제 대상 ───────────────────────────────────────
                delete_queue.append((raw_id, existing_point_id))
                if len(delete_queue) >= args.embed_batch_size:
                    await _flush_delete()

            if total_fetched % 5000 == 0:
                print(
                    f"  progress: {total_fetched}/?, "
                    f"upsert {stats.upserted}, "
                    f"delete {stats.deleted}, "
                    f"skip {stats.skipped}"
                )

    # ── 잔여 큐 처리 ──────────────────────────────────────────────────
    await _flush_upsert(force=True)
    await _flush_delete()

    # ── write-back (upsert 성공 행: qdrant_point_id + embedded_at + embed_version) ──
    if args.write_back_status and upsert_rows_for_writeback:
        try:
            await _with_retries(
                lambda: _write_back_upsert(
                    pg_conn,
                    args.pg_table,
                    args.id_column,
                    args.point_id_column,
                    args.embedded_at_column,
                    args.embed_version_column,
                    args.description_column,
                    args.embed_version,
                    upsert_rows_for_writeback,
                    write_description=(
                        args.mode == "generate-desc"
                        and not args.no_description_write_back
                    ),
                ),
                attempts=args.retry_attempts,
                base_delay=args.retry_base_delay,
                max_delay=args.retry_max_delay,
            )
        except Exception as exc:
            print(
                f"  [경고] Postgres write-back(upsert) 실패: {exc}\n"
                "  주의: Qdrant에는 선적재됨. reconcile 모드로 정리 권장."
            )
            raise BatchFailedError(
                f"Postgres write-back 실패 — 상태파일 미갱신: {exc}"
            )

    # ── write-back (delete 성공 행: qdrant_point_id = NULL) ──────────
    if args.write_back_status and delete_ids_for_writeback:
        try:
            await _with_retries(
                lambda: _write_back_delete(
                    pg_conn,
                    args.pg_table,
                    args.id_column,
                    args.point_id_column,
                    args.embedded_at_column,
                    args.embed_version_column,
                    args.embed_version,
                    delete_ids_for_writeback,
                ),
                attempts=args.retry_attempts,
                base_delay=args.retry_base_delay,
                max_delay=args.retry_max_delay,
            )
        except Exception as exc:
            print(f"  [경고] Postgres write-back(delete) 실패: {exc}")
            raise BatchFailedError(
                f"Postgres write-back(delete) 실패 — 상태파일 미갱신: {exc}"
            )

    await pg_conn.close()

    # ── 에러 샘플 출력 ────────────────────────────────────────────────
    for sample in stats.error_samples:
        print(f"  [오류] {sample}")

    # ── 상태파일 갱신 (모든 단계 성공 시만) ──────────────────────────
    if stats.failed == 0:
        new_state: dict[str, Any] = {
            "last_run_at": datetime.now(timezone.utc).isoformat(),
            "last_updated_at": max_updated_at,
            "mode": args.mode,
            "rows_upserted": stats.upserted,
            "rows_deleted": stats.deleted,
            "embed_version": args.embed_version,
        }
        _save_state(args.state_file, new_state)
        print(f"  상태파일 갱신: {args.state_file}")
    else:
        raise BatchFailedError(
            f"배치 실패 {stats.failed}건 — 상태파일 미갱신, "
            "다음 실행에서 동일 커서 재시작"
        )

    stats.last_updated_at = max_updated_at


async def _run_reconcile(args: _CliArgs) -> None:
    """Qdrant 잔류 고아 point를 감지·삭제한다 (Hard-delete 복구).

    Postgres의 qdrant_point_id 집합과 Qdrant point_id 집합을 비교한다.
    - qdrant - postgres 차집합 → Qdrant delete (고아 point 정리)
    - postgres - qdrant 차집합 → 경고 로그만 출력 (자동 재임베딩 X, 정합성 이슈 리포트)

    Args:
        args: CLI 인자.
    """
    import asyncpg
    from qdrant_client import QdrantClient
    from qdrant_client.models import PointIdsList

    pg_dsn = _build_pg_dsn()
    pg_conn = await asyncpg.connect(pg_dsn)

    print("  [reconcile] Postgres qdrant_point_id 수집 중...")
    rows = await pg_conn.fetch(
        f"SELECT {args.point_id_column} FROM {args.pg_table} "
        f"WHERE {args.point_id_column} IS NOT NULL "
        f"AND {args.embed_flag_column} = $1",
        args.embed_flag_active_value,
    )
    await pg_conn.close()

    postgres_point_ids: set[str] = {
        _coerce_point_id(r[args.point_id_column]) for r in rows
    }
    print(f"  [reconcile] Postgres qdrant_point_id 보유: {len(postgres_point_ids)}건")

    client = QdrantClient(url=QDRANT_URL, timeout=120)
    qdrant_ids: set[str] = set()
    offset: str | None = None

    print("  [reconcile] Qdrant point_id 수집 중...")
    while True:
        result, next_offset = client.scroll(
            collection_name=args.collection,
            limit=args.reconcile_chunk_size,
            offset=offset,
            with_payload=False,
            with_vectors=False,
        )
        for point in result:
            qdrant_ids.add(str(point.id))
        if next_offset is None:
            break
        offset = next_offset  # type: ignore[assignment]

    print(f"  [reconcile] Qdrant 전체: {len(qdrant_ids)}건")

    orphan_in_qdrant = list(qdrant_ids - postgres_point_ids)
    missing_in_qdrant = list(postgres_point_ids - qdrant_ids)

    print(f"  [reconcile] Qdrant 고아(삭제 대상): {len(orphan_in_qdrant)}건")
    if missing_in_qdrant:
        print(
            f"  [경고] Postgres에는 있으나 Qdrant에 없는 point: {len(missing_in_qdrant)}건\n"
            "         → 재임베딩 필요. --full 옵션으로 전체 재처리 권장."
        )

    if not orphan_in_qdrant or args.dry_run:
        if args.dry_run:
            print(f"  [dry-run] Qdrant 고아 삭제 예정 {len(orphan_in_qdrant)}건")
        return

    chunk_size = args.reconcile_chunk_size
    deleted = 0
    for i in range(0, len(orphan_in_qdrant), chunk_size):
        chunk = orphan_in_qdrant[i:i + chunk_size]
        client.delete(
            collection_name=args.collection,
            points_selector=PointIdsList(points=chunk),
        )
        deleted += len(chunk)
        print(f"  [reconcile] 삭제 {deleted}/{len(orphan_in_qdrant)}")

    print(f"  [reconcile] 완료: {deleted}건 삭제")


def _build_pg_dsn() -> str:
    """환경변수에서 Postgres DSN을 구성한다.

    Returns:
        asyncpg 연결용 DSN 문자열.
    """
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "postgres")
    user = os.getenv("POSTGRES_USER", "postgres")
    password = os.getenv("POSTGRES_PASSWORD", "")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


def _append_json_output(
    json_path: Path,
    rows: list[dict[str, Any]],
    embed_results: list[_EmbedResult],
) -> None:
    """JSON 중간 저장 파일에 upsert 배치를 append한다.

    Args:
        json_path: 출력 JSON 파일 경로.
        rows: DB row 딕셔너리 리스트.
        embed_results: 대응하는 임베딩 결과 리스트.
    """
    records: list[dict[str, Any]] = []
    if json_path.exists():
        with open(json_path, encoding="utf-8") as f:
            try:
                records = json.load(f)
            except json.JSONDecodeError:
                records = []

    for row, emb in zip(rows, embed_results):
        records.append({
            "id": str(row["_raw_id"]),
            "sql": row.get("sql", ""),
            "description": row.get("description", ""),
            "dense_dim": len(emb.dense),
            "sparse_nnz": len(emb.sparse_indices),
        })

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


# ══════════════════════════════════════════════════════════════════════════
# CLI 파싱
# ══════════════════════════════════════════════════════════════════════════

def _parse_args() -> _CliArgs:
    """argparse로 CLI 인자를 파싱한다.

    Returns:
        파싱된 _CliArgs 인스턴스.
    """
    parser = argparse.ArgumentParser(
        description="SQL 수행이력 임베딩 배치",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--mode",
        choices=["generate-desc", "direct"],
        default="generate-desc",
        help="처리 모드",
    )
    parser.add_argument(
        "--source",
        choices=["dummy", "postgres"],
        default="dummy",
        help="데이터 소스",
    )

    # Postgres 스키마
    pg = parser.add_argument_group("Postgres 스키마")
    pg.add_argument("--pg-table", default="sql_exec_history")
    pg.add_argument("--id-column", default="id")
    pg.add_argument("--text-column", default="sql_text")
    pg.add_argument("--description-column", default="sql_description")
    pg.add_argument("--embed-flag-column", default="embed_flag")
    pg.add_argument("--embed-flag-active-value", default="Y")
    pg.add_argument("--updated-at-column", default="updated_at")
    pg.add_argument("--embedded-at-column", default="embedded_at")
    pg.add_argument("--embed-version-column", default="embed_version")
    pg.add_argument(
        "--point-id-column",
        default="qdrant_point_id",
        help="Qdrant point_id write-back 대상 컬럼명 (UUID NULL 허용)",
    )
    pg.add_argument("--embed-version", default="bge-m3-v1")
    pg.add_argument("--pg-fetch-size", type=int, default=5000)

    # 증분
    inc = parser.add_argument_group("증분 전략 (택1)")
    inc_group = inc.add_mutually_exclusive_group()
    inc_group.add_argument("--full", action="store_true", help="전체 재처리")
    inc_group.add_argument("--since", type=str, help="지정 ISO 시점 이후")
    inc_group.add_argument(
        "--since-last-run",
        action="store_true",
        default=True,
        help="상태파일 last_updated_at 이후 (기본)",
    )
    inc.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_STATE_FILE,
    )
    inc.add_argument("--resume-from", type=str, default=None)

    # reconcile
    rec = parser.add_argument_group("Reconcile")
    rec.add_argument("--reconcile-deletes", action="store_true")
    rec.add_argument("--reconcile-chunk-size", type=int, default=10000)

    # Qdrant
    q = parser.add_argument_group("Qdrant")
    q.add_argument("--collection", default="sql_history")
    q.add_argument(
        "--no-upsert",
        action="store_true",
        help="Qdrant 미변경, JSON만 출력",
    )
    q.add_argument("--json-output", type=Path, default=None)

    # 배치·병렬·재시도
    b = parser.add_argument_group("배치·병렬·재시도")
    b.add_argument("--embed-batch-size", type=int, default=64)
    b.add_argument("--llm-batch-size", type=int, default=20)
    b.add_argument("--concurrency", type=int, default=8)
    b.add_argument("--retry-attempts", type=int, default=3)
    b.add_argument("--retry-base-delay", type=float, default=2.0)
    b.add_argument("--retry-max-delay", type=float, default=30.0)

    # 운영
    op = parser.add_argument_group("운영")
    op.add_argument(
        "--no-write-back-status",
        action="store_true",
        help="qdrant_point_id / embedded_at / embed_version write-back 전체 비활성",
    )
    op.add_argument(
        "--no-description-write-back",
        action="store_true",
        help="generate-desc 모드에서 sql_description write-back 비활성",
    )
    op.add_argument("--dry-run", action="store_true")
    op.add_argument("--limit", type=int, default=None)

    ns = parser.parse_args()

    # SQL identifier 화이트리스트 검증 — 테이블명·컬럼명이 쿼리에 f-string으로
    # 삽입되므로(parameterized 불가), 영문/숫자/언더스코어만 허용한다.
    # 운영자 오타나 악의 입력으로 인한 쿼리 파괴를 방어한다.
    for _label, _value in (
        ("--pg-table", ns.pg_table),
        ("--id-column", ns.id_column),
        ("--text-column", ns.text_column),
        ("--description-column", ns.description_column),
        ("--embed-flag-column", ns.embed_flag_column),
        ("--updated-at-column", ns.updated_at_column),
        ("--embedded-at-column", ns.embedded_at_column),
        ("--embed-version-column", ns.embed_version_column),
        ("--point-id-column", ns.point_id_column),
    ):
        _validate_identifier(_value, _label)

    return _CliArgs(
        mode=ns.mode,
        source=ns.source,
        pg_table=ns.pg_table,
        id_column=ns.id_column,
        text_column=ns.text_column,
        description_column=ns.description_column,
        embed_flag_column=ns.embed_flag_column,
        embed_flag_active_value=ns.embed_flag_active_value,
        updated_at_column=ns.updated_at_column,
        embedded_at_column=ns.embedded_at_column,
        embed_version_column=ns.embed_version_column,
        point_id_column=ns.point_id_column,
        embed_version=ns.embed_version,
        pg_fetch_size=ns.pg_fetch_size,
        no_description_write_back=ns.no_description_write_back,
        full=ns.full,
        since=ns.since,
        since_last_run=ns.since_last_run,
        state_file=ns.state_file,
        resume_from=ns.resume_from,
        reconcile_deletes=ns.reconcile_deletes,
        reconcile_chunk_size=ns.reconcile_chunk_size,
        collection=ns.collection,
        no_upsert=ns.no_upsert,
        json_output=ns.json_output,
        embed_batch_size=ns.embed_batch_size,
        llm_batch_size=ns.llm_batch_size,
        concurrency=ns.concurrency,
        retry_attempts=ns.retry_attempts,
        retry_base_delay=ns.retry_base_delay,
        retry_max_delay=ns.retry_max_delay,
        write_back_status=not ns.no_write_back_status,
        dry_run=ns.dry_run,
        limit=ns.limit,
    )


# ══════════════════════════════════════════════════════════════════════════
# 기존 dummy main (하위호환)
# ══════════════════════════════════════════════════════════════════════════

def _run_dummy_mode() -> None:
    """dummy 소스 처리: 더미 데이터 생성 + LLM 보강 + JSON 출력 (기존 동작).

    인자 없이 실행하거나 --source dummy 지정 시 호출된다.
    """
    sys.path.insert(0, str(_SCRIPT_DIR))
    from qdrant_data_generators import generate_sql_history_data  # type: ignore[import]

    print("SQL 수행이력 문서 보강 시작")
    print(f"  출력: {OUTPUT_PATH}")

    sql_data = generate_sql_history_data(10000)
    print(f"  데이터 생성: {len(sql_data)}건")

    start = time.time()
    enriched = enrich_descriptions(sql_data)
    elapsed = time.time() - start

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(enriched, f, ensure_ascii=False, indent=2)

    enriched_count = sum(
        1 for d in enriched
        if d.get("enriched", "") != d.get("description", "")
    )
    print(f"\n보강 완료: {enriched_count}/{len(enriched)}건")
    print(f"소요 시간: {elapsed:.1f}초")
    print(f"저장: {OUTPUT_PATH}")
    print("\n다음 단계: python standalone/scripts/seed_qdrant.py")


# ══════════════════════════════════════════════════════════════════════════
# 진입점
# ══════════════════════════════════════════════════════════════════════════

def main() -> None:
    """CLI 진입점.

    --source dummy (기본): 기존 dummy 흐름 그대로 실행.
    --source postgres: Postgres → BGE-M3 → Qdrant 운영 배치.
    """
    # 인자가 없으면 기존 dummy 동작 (하위호환)
    if len(sys.argv) == 1:
        _run_dummy_mode()
        return

    args = _parse_args()

    if args.source == "dummy":
        _run_dummy_mode()
        return

    # postgres 모드
    start_ts = time.time()
    print("=" * 60)
    print("SQL 수행이력 Qdrant 적재 배치 (BGE-M3 하이브리드)")
    print("=" * 60)
    print(f"  mode     : {args.mode}")
    print(f"  source   : {args.source}")
    print(f"  table    : {args.pg_table}")
    print(f"  collection: {args.collection}")
    print(f"  state    : {args.state_file}")

    try:
        if args.reconcile_deletes:
            asyncio.run(_run_reconcile(args))
        else:
            asyncio.run(_run_postgres_mode(args))
    except BatchFailedError as exc:
        print(f"\n[WARN] {exc}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n[중단] 사용자 인터럽트")
        sys.exit(130)
    except Exception as exc:
        print(f"\n[ERROR] 예기치 않은 오류: {exc}")
        raise

    elapsed = time.time() - start_ts
    print(f"\n소요 시간: {elapsed:.1f}초")
    print("=" * 60)
    print("배치 완료")
    print("=" * 60)


if __name__ == "__main__":
    main()
