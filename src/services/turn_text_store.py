"""경량 대화 이력 저장소.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

checkpoint_dc_turn_texts 테이블에 턴별 메시지와 UI 복원 데이터를 저장하고,
LLM 맥락 전달 / UI 과거 대화 복원 / 감사 조회에 사용한다.

turn_seq 채번:
    INSERT 서브쿼리로 DB 레벨 원자적 채번 (MAX(turn_seq) + 1).
    SELECT + INSERT 분리 시 race condition 가능 → 단일 SQL로 해결.

실패 버퍼:
    save_turn() 실패 시 인메모리 _pending_turns 버퍼에 보관하고,
    다음 save_turn() 호출 시 함께 저장을 재시도한다.
    서비스 중단 없이 이력 누락을 최소화하는 best-effort 전략.

pool 직접 전달 패턴:
    노드 계층은 get_connector_manager() 싱글턴을 내부 호출하지만,
    서비스 계층인 이 모듈은 pool을 파라미터로 받는다.
    (1) 테스트 시 mock pool 주입 용이 (2) 싱글턴 의존도 최소화.
"""

from __future__ import annotations

import logging
from typing import Any

from psycopg.types.json import Json

logger = logging.getLogger(__name__)

# ── 실패 버퍼: 세션별 미저장 턴 보관 ──
_pending_turns: dict[str, list[dict[str, Any]]] = {}


# ============================================================================
# 턴 저장
# ============================================================================

async def save_turn(
    pool: Any,
    *,
    thread_id: str,
    role: str,
    content: str,
    client_ip: str | None = None,
    user_agent: str | None = None,
    turn_type: str = "normal",
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
    metadata: dict | None = None,
) -> str | None:
    """턴을 저장한다. turn_seq는 DB 레벨 원자적 채번.

    Returns:
        저장된 턴의 turn_id (UUID). 실패 시 None.

    실패 시 _pending_turns 버퍼에 보관하고 다음 호출 시 재시도.
    """
    turn_data = {
        "thread_id": thread_id,
        "role": role,
        "content": content,
        "client_ip": client_ip,
        "user_agent": user_agent,
        "turn_type": turn_type,
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
        "metadata": Json(metadata or {}),
    }

    # 이전 실패 턴 + 현재 턴을 모아서 저장
    pending = _pending_turns.pop(thread_id, [])
    pending.append(turn_data)

    saved_count = 0
    last_turn_id: str | None = None
    try:
        async with pool.connection() as conn:
            for turn in pending:
                row = await conn.execute(
                    """
                    INSERT INTO checkpoint_dc_turn_texts (
                        thread_id, turn_seq, role, content,
                        client_ip, user_agent,
                        turn_type, intent, token_count, latency_ms,
                        request_id, status, error_type, error_message,
                        exit_node, model_id, trace_id, metadata
                    ) VALUES (
                        %(thread_id)s,
                        COALESCE(
                            (SELECT MAX(turn_seq) + 1
                             FROM checkpoint_dc_turn_texts
                             WHERE thread_id = %(thread_id)s),
                            1
                        ),
                        %(role)s, %(content)s,
                        %(client_ip)s, %(user_agent)s,
                        %(turn_type)s, %(intent)s, %(token_count)s, %(latency_ms)s,
                        %(request_id)s, %(status)s, %(error_type)s, %(error_message)s,
                        %(exit_node)s, %(model_id)s, %(trace_id)s, %(metadata)s
                    )
                    RETURNING turn_id::text
                    """,
                    turn,
                )
                result = await row.fetchone()
                last_turn_id = result["turn_id"] if result else None
                saved_count += 1
    except Exception:
        unsaved = pending[saved_count:]
        if unsaved:
            _pending_turns[thread_id] = unsaved
        logger.warning(
            "턴 저장 실패 — %d/%d건 저장, %d건 버퍼 보관",
            saved_count, len(pending), len(unsaved),
            exc_info=True,
        )
        return None

    return last_turn_id


# ============================================================================
# 대화 이력 조회 (LLM 맥락 전달용)
# ============================================================================

async def get_conversation_history(
    pool: Any,
    session_id: str,
) -> list[dict[str, str]]:
    """LLM 맥락 전달용 대화 이력을 조회한다.

    TEXT 기반이므로 역직렬화 없이 즉시 반환된다.
    """
    async with pool.connection() as conn:
        rows = await conn.execute(
            """
            SELECT role, content
            FROM checkpoint_dc_turn_texts
            WHERE thread_id = %(thread_id)s
            ORDER BY turn_seq
            """,
            {"thread_id": session_id},
        )
        results = await rows.fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in results]


# ============================================================================
# UI 과거 대화 복원: 2-tier 로딩
# ============================================================================

async def get_session_turns_for_ui(
    pool: Any,
    session_id: str,
) -> list[dict[str, Any]]:
    """UI에서 과거 세션을 열 때 전체 턴의 경량 데이터를 반환한다 (Tier 1).

    metadata(SVG 등)는 포함하지 않는다 → Tier 2로 개별 로드.
    """
    async with pool.connection() as conn:
        rows = await conn.execute(
            """
            SELECT turn_id::text AS turn_id, turn_seq, role, content,
                   turn_type, is_liked, feedback, is_downloaded, status,
                   created_at,
                   (metadata != '{}'::jsonb) AS has_metadata,
                   metadata->'trace_files' AS trace_files,
                   metadata->'process_summary' AS process_summary,
                   metadata->'result_data' AS result_data_meta
            FROM checkpoint_dc_turn_texts
            WHERE thread_id = %(thread_id)s
            ORDER BY turn_seq
            """,
            {"thread_id": session_id},
        )
        results = await rows.fetchall()
        return [dict(r) for r in results]


async def get_turn_metadata(
    pool: Any,
    turn_id: str,
) -> dict[str, Any] | None:
    """특정 턴의 metadata를 반환한다 (Tier 2).

    UI에서 사용자가 특정 턴의 상세(차트, 추론흐름, SQL)를 볼 때 호출.
    """
    async with pool.connection() as conn:
        row = await conn.execute(
            """
            SELECT metadata
            FROM checkpoint_dc_turn_texts
            WHERE turn_id = %(turn_id)s::uuid
            """,
            {"turn_id": turn_id},
        )
        result = await row.fetchone()
        return result["metadata"] if result else None


# ============================================================================
# UI 사용자 액션 UPDATE
# ============================================================================

async def toggle_like(
    pool: Any,
    turn_id: str,
    is_liked: bool | None,
    feedback: str | None = None,
) -> dict[str, Any] | None:
    """턴에 좋아요/싫어요를 설정하거나 해제한다.

    Returns:
        업데이트된 턴 정보 dict. turn_id가 존재하지 않으면 None.
    """
    async with pool.connection() as conn:
        row = await conn.execute(
            """
            UPDATE checkpoint_dc_turn_texts
            SET is_liked = %(is_liked)s,
                feedback = %(feedback)s,
                liked_at = CASE WHEN %(has_like)s THEN now() ELSE NULL END
            WHERE turn_id = %(turn_id)s::uuid
            RETURNING turn_id::text, is_liked, feedback, liked_at
            """,
            {
                "turn_id": turn_id,
                "is_liked": is_liked,
                "feedback": feedback,
                "has_like": is_liked is not None,
            },
        )
        result = await row.fetchone()
        return dict(result) if result else None


async def mark_downloaded(
    pool: Any,
    turn_id: str,
) -> dict[str, Any] | None:
    """턴의 결과를 다운로드했음을 기록한다 (최초 1회만 시각 기록)."""
    async with pool.connection() as conn:
        row = await conn.execute(
            """
            UPDATE checkpoint_dc_turn_texts
            SET is_downloaded = true,
                downloaded_at = COALESCE(downloaded_at, now())
            WHERE turn_id = %(turn_id)s::uuid
            RETURNING turn_id::text, is_downloaded, downloaded_at
            """,
            {"turn_id": turn_id},
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
    _has_turns = (
        "EXISTS ("
        "SELECT 1 FROM checkpoint_dc_turn_texts t "
        "WHERE t.thread_id = s.thread_id"
        ")"
    )
    async with pool.connection() as conn:
        # 전체 건수 (턴이 있는 세션만)
        count_row = await conn.execute(
            f"""
            SELECT COUNT(*) AS cnt
            FROM checkpoint_dc_session_index s
            WHERE s.user_id = %(user_id)s AND s.is_archived = false
              AND {_has_turns}
            """,
            {"user_id": user_id},
        )
        total = (await count_row.fetchone())["cnt"]

        # 세션 목록 (턴이 있는 세션만)
        rows = await conn.execute(
            f"""
            SELECT s.thread_id, s.title, s.last_active, s.created_at
            FROM checkpoint_dc_session_index s
            WHERE s.user_id = %(user_id)s AND s.is_archived = false
              AND {_has_turns}
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
        return result.rowcount > 0


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
        return result.rowcount > 0


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
        return result.rowcount > 0
