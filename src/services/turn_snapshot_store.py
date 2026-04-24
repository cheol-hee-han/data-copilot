"""턴 스냅샷 복원 서비스 — 세션 재접속 시 DB에서 TurnSnapshot 재구성.

작성자: 한철희 / 최종수정: 2026-04-17

Multi-Turn CONTINUE Orchestrator 설계(§3.1, §7 Step 7)에 따라,
서버 재시작 또는 세션 재접속 시 checkpoint_dc_messages 테이블에서
최근 4개 성공 턴을 조회하여 TurnSnapshot 리스트를 재구성한다.

복원 전략 (Partial Hydration):
    - 전체 복원 실패(DB 오류): 경고 로그 후 빈 리스트 반환.
      파이프라인 차단 없음.
    - 개별 턴 복원 실패: 경고 로그 후 해당 턴만 스킵.
      나머지 턴은 정상 복원.
    - MongoDB 메타 조회 실패 (일부 테이블/코드):
      해당 항목만 제외하고 나머지로 스냅샷 구성. 스냅샷 자체는 유지.

MongoDB 재조회 전략 (fan-out):
    - 전체 4턴의 테이블명 집합을 수집 후 asyncio.gather로 동시 조회.
    - 중복 테이블/코드는 1회만 조회 (비용 절감).
    - lookup_table_meta / lookup_code_meta는 기존 tools.py 함수 재사용.

DB 복원 가능한 13개 필드 원천:
    1. user_message_seq:     해당 assistant row 직전 user 메시지의 seq
    2. intent:               checkpoint_dc_messages.intent 컬럼
    3. generated_sql:        checkpoint_dc_messages.executed_sql 컬럼
    4. sql_explanation:      checkpoint_dc_messages.sql_explanation 컬럼
    5. result_data:          metadata.result_data (rows 제외)
    6. visualization:        metadata.visualization
    7. selected_tables:      process_summary.context.tables[used=true].name
                             → lookup_table_meta fan-out → TableMeta 풀 객체
    8. explored_codes:       executed_sql에서 sqlglot으로 코드 컬럼 추출
                             → lookup_code_meta fan-out → CodeMeta 풀 객체
    9. inferred_signals:     process_summary.ai_decisions.inferences
                             (source_node="intent_classifier" 제외)
    10. normalized_query:    process_summary.interpretation._raw
                             (Path F' REGENERATE hydration 전용)
    11. knowledge_items:     process_summary.context._knowledge_items
                             (Path F' REGENERATE hydration 전용)
    12. query_decomposition: process_summary._query_decomposition
                             (Path F' REGENERATE hydration 전용)
    13. target_db:           checkpoint_dc_messages.target_db 컬럼 (직접 컬럼)

참조:
    docs/todo/20260418-continue-orchestrator-4way-redesign.md §3.1, §4.11
"""

from __future__ import annotations

import asyncio
import re as _re
from typing import Any

from src.agents.models.normalization import NormalizedQuery
from src.agents.models.snapshot import TurnSnapshot
from src.agents.state.state import CodeMeta, KnowledgeItem, TableMeta
from src.models.enums import IntentType
from src.utils.logger import get_logger
from src.utils.sqlglot_analyzer import get_real_columns, parse_sql_safe

logger = get_logger(__name__)

# ── 코드성 컬럼 식별 패턴 ──────────────────────────────────
# "_CD", "_TP", "_FG" 등 코드 컬럼 접미사 (대소문자 무시)
_CODE_COLUMN_PATTERN = _re.compile(
    r"^.+(_CD|_TP|_FG|_TYPE|_CODE|_FLAG|_KD|_KND|_STAT|_STS|_GB|_GBN)$",
    _re.IGNORECASE,
)

_MAX_SNAPSHOTS = 4


async def restore_from_db(
    pool: Any,
    session_id: str,
    limit: int = _MAX_SNAPSHOTS,
) -> list[TurnSnapshot]:
    """DB에서 최근 성공 턴을 조회하여 TurnSnapshot 리스트를 복원한다.

    서버 재시작 또는 세션 재접속 시 호출된다.
    복원 실패 시 빈 리스트를 반환하여 파이프라인을 차단하지 않는다.

    Args:
        pool: psycopg async connection pool (checkpointer_pool).
        session_id: 복원할 세션 ID (checkpoint_dc_messages.thread_id).
        limit: 복원할 최대 턴 수 (기본 4 — CoE-SQL 근거).

    Returns:
        seq ASC(시간 오름차순)로 정렬된 TurnSnapshot 리스트.
        복원 실패 시 빈 리스트.
    """
    if pool is None:
        logger.debug(
            "turn_snapshot 복원 스킵 — pool 없음",
            session_id=session_id,
        )
        return []

    try:
        rows = await _fetch_assistant_rows(pool, session_id, limit)
    except Exception:
        logger.warning(
            "turn_snapshot DB 조회 실패 — 빈 리스트 반환",
            session_id=session_id,
            exc_info=True,
        )
        return []

    if not rows:
        logger.debug(
            "turn_snapshot 복원 대상 없음 (성공 턴 없음)",
            session_id=session_id,
        )
        return []

    # ── user_message_seq 매핑 ──────────────────────────────
    try:
        assistant_seqs = [r["seq"] for r in rows]
        user_seq_map = await _fetch_user_seqs(pool, session_id, assistant_seqs)
    except Exception:
        logger.warning(
            "turn_snapshot user_seq 조회 실패 — seq=0으로 폴백",
            session_id=session_id,
            exc_info=True,
        )
        user_seq_map = {}

    # ── MongoDB fan-out: 4턴 전체 이름 집합 수집 후 동시 조회 ──
    all_table_names: set[str] = set()
    all_code_columns: set[str] = set()

    for row in rows:
        all_table_names.update(_extract_table_names(row))
        all_code_columns.update(
            _extract_code_columns(row.get("executed_sql") or ""),
        )

    table_index, code_index = await _fanout_mongo_lookups(
        all_table_names, all_code_columns,
    )

    # ── 개별 턴 스냅샷 빌드 (실패 시 해당 턴만 스킵) ──────
    snapshots: list[TurnSnapshot] = []
    for row in rows:
        try:
            snapshot = _build_snapshot_from_row(
                row=row,
                user_seq_map=user_seq_map,
                table_index=table_index,
                code_index=code_index,
            )
            snapshots.append(snapshot)
        except Exception:
            logger.warning(
                "turn_snapshot 개별 빌드 실패 — 해당 턴 스킵",
                session_id=session_id,
                row_seq=row.get("seq"),
                exc_info=True,
            )

    # rows는 DESC 정렬로 가져왔으므로 ASC(시간 오름차순)로 재정렬
    snapshots.sort(key=lambda s: s.user_message_seq)

    logger.info(
        "turn_snapshot 복원 완료",
        session_id=session_id,
        restored_count=len(snapshots),
        total_rows=len(rows),
    )
    return snapshots


# ============================================================================
# 내부 DB 조회 함수
# ============================================================================

async def _fetch_assistant_rows(
    pool: Any,
    session_id: str,
    limit: int,
) -> list[dict[str, Any]]:
    """성공한 assistant normal 턴을 최신 순으로 조회한다.

    필터 조건:
        - role = 'assistant': LLM이 생성한 응답 행
        - message_type = 'normal': 명확화/에러 턴 제외
        - status = 'success': 실패/취소 턴 제외
        - executed_sql IS NOT NULL: 비데이터 턴 제외

    Args:
        pool: psycopg async connection pool.
        session_id: 세션 ID.
        limit: 최대 조회 건수.

    Returns:
        최신 순(DESC)으로 정렬된 행 리스트.
    """
    async with pool.connection() as conn:
        rows = await conn.execute(
            """
            SELECT
                seq,
                intent,
                executed_sql,
                sql_explanation,
                target_db,
                metadata->'result_data'        AS result_data,
                metadata->'visualization'      AS visualization,
                metadata->'process_summary'    AS process_summary
            FROM checkpoint_dc_messages
            WHERE thread_id       = %(thread_id)s
              AND role             = 'assistant'
              AND message_type     = 'normal'
              AND status           = 'success'
              AND executed_sql IS NOT NULL
            ORDER BY seq DESC
            LIMIT %(limit)s
            """,
            {"thread_id": session_id, "limit": limit},
        )
        results = await rows.fetchall()
        return [dict(r) for r in results]


async def _fetch_user_seqs(
    pool: Any,
    session_id: str,
    assistant_seqs: list[int],
) -> dict[int, int]:
    """assistant 행 seq 목록에 대해 직전 user 턴 seq를 매핑한다.

    각 assistant 행(seq=N)에 대해 seq < N 조건의 가장 큰 user normal
    메시지 seq를 찾는다. UNNEST로 일괄 처리하여 왕복 횟수를 최소화한다.

    Args:
        pool: psycopg async connection pool.
        session_id: 세션 ID.
        assistant_seqs: assistant 행의 seq 목록.

    Returns:
        {assistant_seq: user_seq} 매핑 dict.
        직전 user 행이 없으면 해당 키 누락.
    """
    if not assistant_seqs:
        return {}

    async with pool.connection() as conn:
        rows = await conn.execute(
            """
            SELECT
                a_seq,
                (SELECT MAX(seq)
                 FROM checkpoint_dc_messages
                 WHERE thread_id     = %(thread_id)s
                   AND seq           < a_seq
                   AND role          = 'user'
                   AND message_type  = 'normal') AS user_seq
            FROM UNNEST(%(assistant_seqs)s::smallint[]) AS a_seq
            """,
            {
                "thread_id": session_id,
                "assistant_seqs": assistant_seqs,
            },
        )
        results = await rows.fetchall()

    mapping: dict[int, int] = {}
    for r in results:
        if r["user_seq"] is not None:
            mapping[r["a_seq"]] = r["user_seq"]
    return mapping


# ============================================================================
# MongoDB fan-out 재조회
# ============================================================================

async def _fanout_mongo_lookups(
    table_names: set[str],
    code_columns: set[str],
) -> tuple[dict[str, TableMeta], dict[str, CodeMeta]]:
    """테이블명·코드 컬럼 집합에 대해 MongoDB를 동시 fan-out 조회한다.

    실패한 개별 항목은 index에서 누락되고 경고 로그를 남긴다.
    return_exceptions=True로 실패가 다른 조회를 차단하지 않는다.

    Args:
        table_names: 복원할 테이블명 집합 (4턴 전체 유니크 합집합).
        code_columns: 복원할 코드 컬럼명 집합 (4턴 전체 유니크 합집합).

    Returns:
        (table_index, code_index) 튜플.
        - table_index: {table_name: TableMeta} (조회 실패 항목 제외)
        - code_index: {column_name: CodeMeta} (조회 실패 항목 제외)
    """
    from src.agents.nodes.reason.tools import (
        lookup_code_meta,
        lookup_table_meta,
    )

    table_name_list = sorted(table_names)
    code_column_list = sorted(code_columns)

    # 동시 fan-out — 실패 시 Exception 객체로 대체
    table_results, code_results = await asyncio.gather(
        asyncio.gather(
            *[lookup_table_meta(name) for name in table_name_list],
            return_exceptions=True,
        ),
        asyncio.gather(
            *[lookup_code_meta(col) for col in code_column_list],
            return_exceptions=True,
        ),
    )

    table_index: dict[str, TableMeta] = {}
    for name, result in zip(table_name_list, table_results):
        if isinstance(result, Exception):
            logger.warning(
                "turn_snapshot 테이블 메타 조회 실패 — 해당 테이블 제외",
                table_name=name,
                error=str(result),
            )
            continue
        if isinstance(result, list) and result:
            meta = TableMeta.from_meta(result[0])
            if meta is not None:
                table_index[name] = meta

    code_index: dict[str, CodeMeta] = {}
    for col, result in zip(code_column_list, code_results):
        if isinstance(result, Exception):
            logger.warning(
                "turn_snapshot 코드 메타 조회 실패 — 해당 컬럼 제외",
                column_name=col,
                error=str(result),
            )
            continue
        if isinstance(result, list) and result:
            raw = result[0]
            code_index[col] = _build_code_meta(col, raw)

    return table_index, code_index


# ============================================================================
# 개별 스냅샷 빌드
# ============================================================================

def _build_snapshot_from_row(
    row: dict[str, Any],
    user_seq_map: dict[int, int],
    table_index: dict[str, TableMeta],
    code_index: dict[str, CodeMeta],
) -> TurnSnapshot:
    """DB 행 + MongoDB 인덱스에서 TurnSnapshot 1개를 구성한다.

    Args:
        row: _fetch_assistant_rows 반환 행 (dict).
        user_seq_map: {assistant_seq: user_seq} 매핑.
        table_index: {table_name: TableMeta} MongoDB 재조회 결과.
        code_index: {column_name: CodeMeta} MongoDB 재조회 결과.

    Returns:
        완성된 TurnSnapshot. 필수 필드 누락 시 ValidationError 발생.
    """
    assistant_seq: int = row["seq"]

    # 필드 1: user_message_seq
    user_message_seq: int = user_seq_map.get(assistant_seq, 0)

    # 필드 2: intent
    intent = _parse_intent(row.get("intent") or "")

    # 필드 3: generated_sql
    generated_sql: str | None = row.get("executed_sql") or None

    # 필드 4: sql_explanation
    sql_explanation: str = row.get("sql_explanation") or ""

    # 필드 5: result_data (rows 제외)
    result_data: dict[str, Any] | None = extract_snapshot_result_data(
        row.get("result_data"),
    )

    # 필드 6: visualization
    visualization: dict[str, Any] | None = _safe_dict(row.get("visualization"))

    # 필드 7: selected_tables
    table_names = _extract_table_names(row)
    selected_tables: list[TableMeta] = [
        table_index[name]
        for name in table_names
        if name in table_index
    ]

    # 필드 8: explored_codes
    code_columns = _extract_code_columns(generated_sql or "")
    explored_codes: dict[str, CodeMeta] = {
        col: code_index[col]
        for col in code_columns
        if col in code_index
    }

    # 필드 9: inferred_signals
    inferred_signals: list[dict[str, Any]] = _extract_inferred_signals(
        row.get("process_summary"),
    )

    # ── Path F' REGENERATE hydration 전용 3 필드 (+ 직접 컬럼 target_db) ──

    # 필드 10: normalized_query (process_summary.interpretation._raw)
    normalized_query: NormalizedQuery | None = _extract_normalized_query(
        row.get("process_summary"),
    )

    # 필드 11: knowledge_items (process_summary.context._knowledge_items)
    knowledge_items: list[KnowledgeItem] = _extract_knowledge_items(
        row.get("process_summary"),
    )

    # 필드 12: query_decomposition (process_summary._query_decomposition)
    query_decomposition: dict[str, Any] = _extract_query_decomposition(
        row.get("process_summary"),
    )

    # 필드 13: target_db (직접 컬럼)
    target_db: str = row.get("target_db") or ""

    return TurnSnapshot(
        user_message_seq=user_message_seq,
        intent=intent,
        generated_sql=generated_sql,
        sql_explanation=sql_explanation,
        result_data=result_data,
        visualization=visualization,
        selected_tables=selected_tables,
        explored_codes=explored_codes,
        normalized_query=normalized_query,
        knowledge_items=knowledge_items,
        query_decomposition=query_decomposition,
        target_db=target_db,
        inferred_signals=inferred_signals,
    )


# ============================================================================
# 파싱 헬퍼
# ============================================================================

def _parse_intent(raw: str) -> IntentType:
    """intent 문자열을 IntentType으로 변환한다.

    M12(strict): 알 수 없는 값은 silent fallback 하지 않고 ValueError 를 발생.
    `_build_snapshot_from_row` 를 감싸는 restore_from_db 의 except 가 해당 턴만
    스킵하므로, 파이프라인 전체는 차단되지 않으면서도 정확도는 보존된다.
    빈 문자열(컬럼 미기록)은 UNKNOWN 으로 허용 — 비데이터/구버전 호환.

    Args:
        raw: checkpoint_dc_messages.intent 컬럼 원본 문자열.

    Returns:
        IntentType enum 값.

    Raises:
        ValueError: raw 가 비어있지 않은데 어떤 IntentType 과도 일치하지 않을 때.
    """
    if not raw:
        return IntentType.UNKNOWN
    try:
        return IntentType(raw)
    except ValueError:
        for member in IntentType:
            if member.value == raw:
                return member
        raise ValueError(f"알 수 없는 intent: {raw!r}") from None


def _extract_table_names(row: dict[str, Any]) -> list[str]:
    """process_summary.context.tables에서 used=true인 테이블명을 추출한다.

    설계 §3.1: process_summary.context.tables[*].name (used=true).
    process_summary 파싱 실패 시 빈 리스트 반환 (Partial Hydration).

    Args:
        row: DB 행 dict (process_summary 키 포함).

    Returns:
        used=true 테이블명 리스트.
    """
    ps = _safe_dict(row.get("process_summary"))
    if ps is None:
        return []

    context = ps.get("context") or {}
    tables = context.get("tables") or []

    names: list[str] = []
    for t in tables:
        if not isinstance(t, dict):
            continue
        if t.get("used") and t.get("name"):
            names.append(t["name"])
    return names


def _extract_code_columns(sql: str) -> list[str]:
    """SQL에서 코드성 컬럼명을 추출한다.

    sqlglot AST로 실제 참조 컬럼을 추출하고 코드 컬럼 패턴으로 필터링한다.
    파싱 실패 시 빈 리스트 반환 (Partial Hydration).

    Args:
        sql: executed_sql 원본 SQL 문자열.

    Returns:
        코드 컬럼명 리스트 (대문자 정규화).
    """
    if not sql:
        return []
    try:
        ast = parse_sql_safe(sql)
        if ast is None:
            return []
        columns = get_real_columns(ast)
        return [
            col.upper()
            for col in columns
            if _CODE_COLUMN_PATTERN.match(col)
        ]
    except Exception:
        return []


# TurnSnapshot.result_data 로 보존하는 정규 키 집합 (단일 진실 공급원).
# rows 는 checkpoint_dc_messages.metadata.result_data.rows 가 단일 원천이므로 제외.
# 저장(save_turn_snapshot) ↔ 복원(restore_from_db) 양쪽에서 동일 계약 유지.
SNAPSHOT_RESULT_DATA_KEYS: tuple[str, ...] = (
    "columns",
    "column_formats",
    "total_count",
    "displayed_count",
    "execution_time_ms",
)


def extract_snapshot_result_data(
    raw: Any,
) -> dict[str, Any] | None:
    """result_data 에서 rows 를 제외한 스냅샷 보존용 메타데이터만 추출한다.

    저장 경로(Python dict 입력) 와 복원 경로(JSONB raw 입력) 양쪽에서 동일하게
    사용되며, 보존 키는 SNAPSHOT_RESULT_DATA_KEYS 로 고정.

    Args:
        raw: formatter가 생성한 dict 또는 DB JSONB 컬럼 원본 값.

    Returns:
        보존 키만 담긴 dict. 대상 키가 하나도 없으면 None.
    """
    data = _safe_dict(raw) if not isinstance(raw, dict) else raw
    if data is None:
        return None

    extracted: dict[str, Any] = {
        key: data[key] for key in SNAPSHOT_RESULT_DATA_KEYS if key in data
    }
    return extracted if extracted else None


def _extract_inferred_signals(
    process_summary: Any,
) -> list[dict[str, Any]]:
    """process_summary.ai_decisions.inferences에서 INFER 시그널을 추출한다.

    필터 규칙 (§3.1 I4):
        - source_node == "intent_classifier"인 INFER는 제외.
          매 CONTINUE 턴마다 생성되어 스냅샷 축적 시 반복 노출 위험.

    process_summary 파싱 실패 시 빈 리스트 반환 (Partial Hydration).

    Args:
        process_summary: metadata.process_summary JSON (dict 또는 None).

    Returns:
        INFER 시그널 dict 리스트.
    """
    ps = _safe_dict(process_summary)
    if ps is None:
        return []

    ai_decisions = ps.get("ai_decisions") or {}
    inferences = ai_decisions.get("inferences") or []

    result: list[dict[str, Any]] = []
    for inf in inferences:
        if not isinstance(inf, dict):
            continue
        if inf.get("source_node") == "intent_classifier":
            continue
        result.append({
            "question": inf.get("question", ""),
            "value": inf.get("value", ""),
            "source_node": inf.get("source_node", ""),
            "reason": inf.get("reason") or inf.get("reasoning", ""),
        })
    return result


def _extract_normalized_query(
    process_summary: Any,
) -> NormalizedQuery | None:
    """process_summary.interpretation._raw 에서 NormalizedQuery 를 복원한다.

    Path F' §3.5: hydration 전용 언더스코어 필드. 과거 턴(필드 도입 전)은
    None 반환. 스키마 변경 등으로 validate 실패 시에도 None 반환 후 경고.

    Args:
        process_summary: metadata.process_summary JSONB (dict 또는 None).

    Returns:
        복원된 NormalizedQuery 또는 None.
    """
    ps = _safe_dict(process_summary)
    if ps is None:
        return None

    interpretation = ps.get("interpretation") or {}
    raw = interpretation.get("_raw")
    if not isinstance(raw, dict) or not raw:
        return None
    # M12(strict): validation 실패는 더 이상 silent None 로 처리하지 않는다.
    # NormalizedQuery 는 REGENERATE 복원의 핵심 입력이므로, 구조가 틀어졌다면
    # 해당 스냅샷을 아예 제외하는 편이 오염된 재사용보다 안전하다.
    return NormalizedQuery.model_validate(raw)


def _extract_knowledge_items(
    process_summary: Any,
) -> list[KnowledgeItem]:
    """process_summary.context._knowledge_items 에서 KnowledgeItem 리스트를 복원한다.

    Path F' §3.5: hydration 전용 언더스코어 필드. 과거 턴(필드 도입 전)은
    빈 리스트 반환. 개별 항목 validate 실패 시 해당 항목만 스킵.

    Args:
        process_summary: metadata.process_summary JSONB.

    Returns:
        복원된 KnowledgeItem 리스트 (실패 항목 제외).
    """
    ps = _safe_dict(process_summary)
    if ps is None:
        return []

    context = ps.get("context") or {}
    raw_list = context.get("_knowledge_items") or []
    if not isinstance(raw_list, list):
        return []

    # M12(strict): 개별 항목 validate 실패도 silent skip 하지 않고 전파한다.
    # knowledge_items 는 REGENERATE 시 reasoning_decisions·confirmed_terms 렌더링의
    # 근거이므로 일부만 결여된 상태로 복원되면 LLM 에게 왜곡된 컨텍스트를 전달한다.
    # dict 가 아닌 원소는 forward-compat 여지로 skip 허용.
    result: list[KnowledgeItem] = []
    for raw in raw_list:
        if not isinstance(raw, dict):
            continue
        result.append(KnowledgeItem.model_validate(raw))
    return result


def _extract_query_decomposition(
    process_summary: Any,
) -> dict[str, Any]:
    """process_summary._query_decomposition 을 그대로 dict 로 반환한다.

    Path F' §3.5: hydration 전용 언더스코어 필드. dict 시맨틱 그대로 복원
    (별도 Pydantic 검증 없음). 과거 턴(필드 도입 전)은 빈 dict.

    Args:
        process_summary: metadata.process_summary JSONB.

    Returns:
        query_decomposition dict (빈 dict 포함).
    """
    ps = _safe_dict(process_summary)
    if ps is None:
        return {}

    raw = ps.get("_query_decomposition")
    if isinstance(raw, dict):
        return dict(raw)
    return {}


def _build_code_meta(column_name: str, raw: dict[str, Any]) -> CodeMeta:
    """MongoDB code_meta 원본 dict에서 CodeMeta를 구성한다.

    MongoDB search_code_meta 반환 구조:
        {column_name, column_desc, codes: {code: label}}

    Args:
        column_name: 코드 컬럼명.
        raw: MongoDB 반환 raw dict.

    Returns:
        CodeMeta 인스턴스.
    """
    return CodeMeta(
        column_name=column_name,
        column_desc=raw.get("column_desc") or raw.get("description") or "",
        codes=raw.get("codes") or {},
    )


def _safe_dict(raw: Any) -> dict[str, Any] | None:
    """raw 값이 dict이면 그대로, None이면 None을 반환한다.

    psycopg가 JSONB를 Python dict로 이미 변환하므로 대부분 즉시 반환된다.
    예외적으로 str이 넘어오는 경우를 대비해 json.loads를 시도한다.

    M12(strict): json 파싱 실패(str 이지만 유효 JSON 이 아님)는 silent None 로
    가리지 않고 예외를 전파한다. restore_from_db 가 해당 스냅샷만 스킵한다.

    Args:
        raw: JSONB 컬럼 값 (dict, str, 또는 None).

    Returns:
        dict 또는 None.

    Raises:
        ValueError: raw 가 str 인데 유효한 JSON dict 로 파싱되지 않을 때.
    """
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        import json
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError(
                "JSONB 컬럼 파싱 결과가 dict 아님 — 스냅샷 복원 중단",
            )
        return parsed
    return None
