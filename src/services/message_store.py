"""경량 대화 이력 저장소.

작성자: 한철희 / 최종수정: 2026-04-14

checkpoint_dc_messages 테이블에 메시지(턴)별 데이터와 UI 복원 데이터를 저장하고,
LLM 맥락 전달 / UI 과거 대화 복원 / 감사 조회에 사용한다.

seq 채번:
    INSERT 서브쿼리로 DB 레벨 원자적 채번 (MAX(seq) + 1).
    SELECT + INSERT 분리 시 race condition 가능 → 단일 SQL로 해결.

실패 버퍼:
    save_message() 실패 시 인메모리 _pending_messages 버퍼에 보관하고,
    다음 save_message() 호출 시 함께 저장을 재시도한다.
    서비스 중단 없이 이력 누락을 최소화하는 best-effort 전략.

pool 직접 전달 패턴:
    노드 계층은 get_connector_manager() 싱글턴을 내부 호출하지만,
    서비스 계층인 이 모듈은 pool을 파라미터로 받는다.
    (1) 테스트 시 mock pool 주입 용이 (2) 싱글턴 의존도 최소화.
"""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from src.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

# ── 실패 버퍼: 세션별 미저장 메시지 보관 ──
_pending_messages: dict[str, list[dict[str, Any]]] = {}


# ============================================================================
# 메시지 저장
# ============================================================================

async def save_message(
    pool: Any,
    *,
    thread_id: str,
    role: str,
    content: str,
    client_ip: str | None = None,
    user_agent: str | None = None,
    message_type: str = "normal",
    intent: str | None = None,
    token_count: int | None = None,
    latency_ms: int | None = None,
    request_id: str | None = None,
    status: str = "success",
    error_type: str | None = None,
    error_message: str | None = None,
    exit_node: str | None = None,
    model_id: str | None = None,
    trace_id: str | None = None,
    executed_sql: str | None = None,
    sql_explanation: str | None = None,
    target_db: str | None = None,
    metadata: dict | None = None,
) -> tuple[str, int] | None:
    """메시지를 저장한다. seq는 DB 레벨 원자적 채번.

    Returns:
        (message_uuid, seq) 튜플. 실패 시 None.

    실패 시 _pending_messages 버퍼에 보관하고 다음 호출 시 재시도.
    """
    message_data = {
        "thread_id": thread_id,
        "role": role,
        "content": content,
        "client_ip": client_ip,
        "user_agent": user_agent,
        "message_type": message_type,
        "intent": intent,
        "token_count": token_count,
        "latency_ms": latency_ms,
        "request_id": request_id,
        "status": status,
        "error_type": error_type,
        "error_message": error_message,
        "exit_node": exit_node,
        "model_id": model_id,
        "trace_id": trace_id,
        "executed_sql": executed_sql,
        "sql_explanation": sql_explanation,
        "target_db": target_db,
        "metadata": Jsonb(metadata or {}),
    }

    # 이전 실패 메시지 + 현재 메시지를 모아서 저장
    pending = _pending_messages.pop(thread_id, [])
    pending.append(message_data)

    saved_count = 0
    last_message_uuid: str | None = None
    last_seq: int = 0
    try:
        async with pool.connection() as conn:
            for msg in pending:
                row = await conn.execute(
                    """
                    INSERT INTO checkpoint_dc_messages (
                        thread_id, seq, role, content,
                        client_ip, user_agent,
                        message_type, intent, token_count, latency_ms,
                        request_id, status, error_type, error_message,
                        exit_node, model_id, trace_id,
                        executed_sql, sql_explanation,
                        target_db, metadata
                    ) VALUES (
                        %(thread_id)s,
                        COALESCE(
                            (SELECT MAX(seq) + 1
                             FROM checkpoint_dc_messages
                             WHERE thread_id = %(thread_id)s),
                            1
                        ),
                        %(role)s, %(content)s,
                        %(client_ip)s, %(user_agent)s,
                        %(message_type)s, %(intent)s, %(token_count)s, %(latency_ms)s,
                        %(request_id)s, %(status)s, %(error_type)s, %(error_message)s,
                        %(exit_node)s, %(model_id)s, %(trace_id)s,
                        %(executed_sql)s, %(sql_explanation)s,
                        %(target_db)s, %(metadata)s
                    )
                    RETURNING message_uuid::text, seq
                    """,
                    msg,
                )
                result = await row.fetchone()
                if result:
                    last_message_uuid = result["message_uuid"]
                    last_seq = result["seq"]
                else:
                    logger.error(
                        "INSERT RETURNING 결과 없음",
                        thread_id=msg.get("thread_id"),
                        role=msg.get("role"),
                    )
                saved_count += 1
    except Exception:
        unsaved = pending[saved_count:]
        max_pending = settings.message_store_pending_max
        dropped = 0
        if len(unsaved) > max_pending:
            # 상한 초과 시 오래된 항목부터 드롭 (최신 이력 우선 보존)
            dropped = len(unsaved) - max_pending
            unsaved = unsaved[-max_pending:]
        if unsaved:
            _pending_messages[thread_id] = unsaved
        logger.warning(
            "메시지 저장 실패",
            saved=saved_count,
            total=len(pending),
            buffered=len(unsaved),
            dropped=dropped,
            exc_info=True,
        )
        return None

    return (last_message_uuid, last_seq)


# ============================================================================
# 메시지 metadata 보강
# ============================================================================

async def update_message_metadata(
    pool: Any,
    *,
    message_uuid: str,
    metadata: dict,
) -> None:
    """저장된 메시지의 metadata 컬럼을 갱신한다.

    trace 파일명 등 _build_result 이후에만 확정되는 정보를 보강할 때 사용한다.
    """
    if not message_uuid:
        logger.error("update_message_metadata 호출 시 message_uuid 누락", message_uuid=message_uuid)
        return
    try:
        async with pool.connection() as conn:
            await conn.execute(
                """
                UPDATE checkpoint_dc_messages
                   SET metadata = COALESCE(metadata, '{}'::jsonb) || %(metadata)s
                 WHERE message_uuid = %(message_uuid)s::uuid
                """,
                {"message_uuid": message_uuid, "metadata": Jsonb(metadata)},
            )
    except Exception:
        logger.warning(
            "메시지 metadata 갱신 실패",
            message_uuid=message_uuid,
            exc_info=True,
        )


# ============================================================================
# 대화 이력 조회 (LLM 맥락 전달용)
# ============================================================================

async def get_conversation_history(
    pool: Any,
    session_id: str,
) -> list[dict[str, str]]:
    """LLM 맥락 전달용 대화 이력을 조회한다.

    TEXT 기반이므로 역직렬화 없이 즉시 반환된다.
    message_type을 "type" 키로 매핑하여 반환한다.
    intent_classifier._format_history()에서 type="clarification" 필터링에 사용.
    """
    async with pool.connection() as conn:
        rows = await conn.execute(
            """
            SELECT role, content, message_type
            FROM checkpoint_dc_messages
            WHERE thread_id = %(thread_id)s
            ORDER BY seq
            """,
            {"thread_id": session_id},
        )
        results = await rows.fetchall()
        return [
            {
                "role": r["role"],
                "content": r["content"],
                "type": r["message_type"] or "normal",
            }
            for r in results
        ]


# ============================================================================
# UI 과거 대화 복원: 2-tier 로딩
# ============================================================================

async def get_session_messages_for_ui(
    pool: Any,
    session_id: str,
) -> list[dict[str, Any]]:
    """UI에서 과거 세션을 열 때 전체 메시지의 경량 데이터를 반환한다 (Tier 1).

    metadata 중 visualization은 Tier 1에 포함. 나머지(trace 등)는 Tier 2로 개별 로드.

    Path F' §3.5.3: process_summary 의 hydration 전용 필드(`_raw`,
    `_knowledge_items`, `_query_decomposition`)는 UI 에 전송할 필요가 없으므로
    jsonb `#-` 연산자로 제거하여 Tier 1 payload 를 경량화한다.
    """
    async with pool.connection() as conn:
        rows = await conn.execute(
            """
            SELECT message_uuid::text AS message_uuid, seq, role, content,
                   message_type, is_liked, feedback, is_downloaded, status,
                   created_at,
                   (metadata != '{}'::jsonb) AS has_metadata,
                   metadata->'trace_files' AS trace_files,
                   (metadata->'process_summary')
                     #- '{interpretation,_raw}'
                     #- '{context,_knowledge_items}'
                     #- '{_query_decomposition}'
                     AS process_summary,
                   metadata->'result_data' AS result_data_meta,
                   metadata->'visualization' AS visualization,
                   metadata->'clarification' AS clarification
            FROM checkpoint_dc_messages
            WHERE thread_id = %(thread_id)s
            ORDER BY seq
            """,
            {"thread_id": session_id},
        )
        results = await rows.fetchall()
        return [dict(r) for r in results]


async def get_message_metadata(
    pool: Any,
    message_uuid: str,
) -> dict[str, Any] | None:
    """특정 메시지의 metadata를 반환한다 (Tier 2).

    UI에서 사용자가 특정 메시지의 상세(차트, 추론흐름, SQL)를 볼 때 호출.
    """
    async with pool.connection() as conn:
        row = await conn.execute(
            """
            SELECT metadata
            FROM checkpoint_dc_messages
            WHERE message_uuid = %(message_uuid)s::uuid
            """,
            {"message_uuid": message_uuid},
        )
        result = await row.fetchone()
        return result["metadata"] if result else None


# ============================================================================
# UI 사용자 액션 UPDATE
# ============================================================================

async def toggle_like(
    pool: Any,
    message_uuid: str,
    is_liked: bool | None,
    feedback: str | None = None,
) -> dict[str, Any] | None:
    """메시지에 좋아요/싫어요를 설정하거나 해제한다.

    Returns:
        업데이트된 메시지 정보 dict. message_uuid가 존재하지 않으면 None.
    """
    async with pool.connection() as conn:
        row = await conn.execute(
            """
            UPDATE checkpoint_dc_messages
            SET is_liked = %(is_liked)s,
                feedback = %(feedback)s,
                liked_at = CASE WHEN %(has_like)s THEN now() ELSE NULL END
            WHERE message_uuid = %(message_uuid)s::uuid
            RETURNING message_uuid::text, is_liked, feedback, liked_at
            """,
            {
                "message_uuid": message_uuid,
                "is_liked": is_liked,
                "feedback": feedback,
                "has_like": is_liked is not None,
            },
        )
        result = await row.fetchone()
        return dict(result) if result else None


async def mark_downloaded(
    pool: Any,
    message_uuid: str,
) -> dict[str, Any] | None:
    """메시지의 결과를 다운로드했음을 기록한다 (최초 1회만 시각 기록)."""
    async with pool.connection() as conn:
        row = await conn.execute(
            """
            UPDATE checkpoint_dc_messages
            SET is_downloaded = true,
                downloaded_at = COALESCE(downloaded_at, now())
            WHERE message_uuid = %(message_uuid)s::uuid
            RETURNING message_uuid::text, is_downloaded, downloaded_at
            """,
            {"message_uuid": message_uuid},
        )
        result = await row.fetchone()
        return dict(result) if result else None


# ============================================================================
# 세션 인덱스 관리
# ============================================================================

async def upsert_session_index(
    pool: Any,
    *,
    thread_id: str,
    user_id: str = "anonymous",
    user_dept: str | None = None,
    title: str | None = None,
) -> None:
    """세션 인덱스를 등록하거나 last_active를 갱신한다."""
    async with pool.connection() as conn:
        await conn.execute(
            """
            INSERT INTO checkpoint_dc_session_index
                (thread_id, user_id, user_dept, title)
            VALUES (%(thread_id)s, %(user_id)s, %(user_dept)s, %(title)s)
            ON CONFLICT (thread_id) DO UPDATE
            SET last_active = now()
            """,
            {
                "thread_id": thread_id,
                "user_id": user_id,
                "user_dept": user_dept,
                "title": title,
            },
        )


async def get_sessions_for_user(
    pool: Any,
    user_id: str,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """사용자의 세션 목록과 전체 건수를 반환한다.

    Returns:
        (sessions, total_count) 튜플.
    """
    _has_messages = (
        "EXISTS ("
        "SELECT 1 FROM checkpoint_dc_messages m "
        "WHERE m.thread_id = s.thread_id"
        ")"
    )
    async with pool.connection() as conn:
        # 전체 건수 (메시지가 있는 세션만)
        count_row = await conn.execute(
            f"""
            SELECT COUNT(*) AS cnt
            FROM checkpoint_dc_session_index s
            WHERE s.user_id = %(user_id)s AND s.is_archived = false
              AND {_has_messages}
            """,
            {"user_id": user_id},
        )
        total = (await count_row.fetchone())["cnt"]

        # 세션 목록 (메시지가 있는 세션만)
        rows = await conn.execute(
            f"""
            SELECT s.thread_id, s.title, s.last_active, s.created_at
            FROM checkpoint_dc_session_index s
            WHERE s.user_id = %(user_id)s AND s.is_archived = false
              AND {_has_messages}
            ORDER BY s.last_active DESC
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            {"user_id": user_id, "limit": limit, "offset": offset},
        )
        results = await rows.fetchall()
        sessions = [dict(r) for r in results]

    return sessions, total


async def get_session_title(
    pool: Any,
    session_id: str,
) -> str | None:
    """세션 제목을 반환한다. 세션이 없으면 None."""
    async with pool.connection() as conn:
        row = await conn.execute(
            """
            SELECT title
            FROM checkpoint_dc_session_index
            WHERE thread_id = %(thread_id)s AND is_archived = false
            """,
            {"thread_id": session_id},
        )
        result = await row.fetchone()
        return result["title"] if result else None


async def archive_session(
    pool: Any,
    session_id: str,
) -> bool:
    """세션을 아카이브 처리한다 (soft delete).

    Returns:
        True: 아카이브 성공. False: 세션 미존재 또는 이미 아카이브됨.
    """
    async with pool.connection() as conn:
        result = await conn.execute(
            """
            UPDATE checkpoint_dc_session_index
            SET is_archived = true
            WHERE thread_id = %(thread_id)s AND is_archived = false
            """,
            {"thread_id": session_id},
        )
        return bool(result.rowcount > 0)


async def unarchive_session(
    pool: Any,
    session_id: str,
) -> bool:
    """아카이브된 세션을 복원한다 (GAP-01: Undo 삭제용).

    Returns:
        True: 복원 성공. False: 세션 미존재 또는 이미 활성 상태.
    """
    async with pool.connection() as conn:
        result = await conn.execute(
            """
            UPDATE checkpoint_dc_session_index
            SET is_archived = false
            WHERE thread_id = %(thread_id)s AND is_archived = true
            """,
            {"thread_id": session_id},
        )
        return bool(result.rowcount > 0)


async def update_session_title(
    pool: Any,
    session_id: str,
    title: str,
) -> bool:
    """세션 제목을 수정한다 (GAP-02: 사이드바 제목 편집용).

    Returns:
        True: 수정 성공. False: 세션 미존재.
    """
    async with pool.connection() as conn:
        result = await conn.execute(
            """
            UPDATE checkpoint_dc_session_index
            SET title = %(title)s
            WHERE thread_id = %(thread_id)s AND is_archived = false
            """,
            {"thread_id": session_id, "title": title},
        )
        return bool(result.rowcount > 0)
