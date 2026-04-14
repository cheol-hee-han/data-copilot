"""세션/메시지 비즈니스 로직 서비스.

작성자: 한철희 / 최종수정: 2026-04-14

Router(HTTP 변환) → Service(비즈니스 로직 조합) → Store(DB 접근) 계층 구조에서
중간 계층을 담당한다. Router가 전달한 파라미터를 받아 message_store의
저수준 DB 함수를 조합하고, API 응답 모델(session_models)로 변환하여 반환한다.

핵심 함수:
    - list_sessions: 사용자별 세션 목록 조회 + SessionListResponse 변환
    - get_session_detail: 세션 메시지 목록 조회 (Tier 1 — 경량)
    - get_message_metadata: 메시지 상세 메타데이터 조회 (Tier 2 — SQL·분석 결과 포함)
    - toggle_like / mark_downloaded: 메시지 피드백 토글
    - archive_session / unarchive_session: 세션 아카이브/복원
    - update_session_title: 세션 제목 수정
"""

from __future__ import annotations

import json as _json
from typing import Any

from src.models.api.session_models import (
    ArchiveResponse,
    DownloadResponse,
    LikeResponse,
    MessageMetadataResponse,
    MessageSummary,
    SessionDetailResponse,
    SessionListResponse,
    SessionSummary,
)
from src.services import message_store


async def list_sessions(
    pool: Any,
    user_id: str,
    limit: int = 20,
    offset: int = 0,
) -> SessionListResponse:
    """사용자의 세션 목록을 조회한다."""
    sessions, total = await message_store.get_sessions_for_user(
        pool, user_id, limit, offset,
    )
    return SessionListResponse(
        sessions=[
            SessionSummary(
                session_id=s["thread_id"],
                title=s["title"],
                last_active=s["last_active"],
                created_at=s["created_at"],
            )
            for s in sessions
        ],
        total_count=total,
    )


async def get_session_detail(
    pool: Any,
    session_id: str,
) -> SessionDetailResponse | None:
    """세션의 전체 메시지 목록을 반환한다 (Tier 1)."""
    title = await message_store.get_session_title(pool, session_id)
    if title is None:
        # session_index에 없으면 None (아카이브됨 또는 미존재)
        return None

    messages = await message_store.get_session_messages_for_ui(pool, session_id)
    return SessionDetailResponse(
        session_id=session_id,
        title=title,
        messages=[
            MessageSummary(
                message_uuid=m["message_uuid"],
                seq=m["seq"],
                role=m["role"],
                content=m["content"],
                message_type=m["message_type"],
                status=m["status"],
                is_liked=m["is_liked"],
                feedback=m.get("feedback"),
                is_downloaded=m["is_downloaded"],
                has_metadata=m["has_metadata"],
                created_at=m["created_at"],
                trace_files=_parse_trace_files(m.get("trace_files")),
                process_summary=m.get("process_summary"),
                result_data_meta=m.get("result_data_meta"),
                visualization=m.get("visualization"),
            )
            for m in messages
        ],
    )


def _parse_trace_files(raw: Any) -> list[dict[str, str]]:
    """JSONB trace_files 값을 list[dict]로 안전 변환한다.

    psycopg3는 JSONB를 Python 객체로 자동 변환하지만,
    환경에 따라 문자열로 반환될 수 있으므로 방어적으로 처리한다.
    """
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            parsed = _json.loads(raw)
            return parsed if isinstance(parsed, list) else []
        except (ValueError, TypeError):
            return []
    return []


async def get_message_metadata(
    pool: Any,
    message_uuid: str,
) -> MessageMetadataResponse | None:
    """특정 메시지의 metadata를 반환한다 (Tier 2)."""
    metadata = await message_store.get_message_metadata(pool, message_uuid)
    if metadata is None:
        return None
    return MessageMetadataResponse(message_uuid=message_uuid, metadata=metadata)


async def toggle_like(
    pool: Any,
    message_uuid: str,
    is_liked: bool | None,
    feedback: str | None = None,
) -> LikeResponse | None:
    """메시지의 좋아요/싫어요를 토글한다."""
    result = await message_store.toggle_like(pool, message_uuid, is_liked, feedback)
    if result is None:
        return None
    return LikeResponse(
        message_uuid=result["message_uuid"],
        is_liked=result["is_liked"],
        feedback=result["feedback"],
        liked_at=result["liked_at"],
    )


async def mark_downloaded(
    pool: Any,
    message_uuid: str,
) -> DownloadResponse | None:
    """메시지의 다운로드를 기록한다."""
    result = await message_store.mark_downloaded(pool, message_uuid)
    if result is None:
        return None
    return DownloadResponse(
        message_uuid=result["message_uuid"],
        is_downloaded=result["is_downloaded"],
        downloaded_at=result["downloaded_at"],
    )


async def archive_session(
    pool: Any,
    session_id: str,
) -> ArchiveResponse | None:
    """세션을 아카이브한다 (soft delete)."""
    success = await message_store.archive_session(pool, session_id)
    if not success:
        return None
    return ArchiveResponse(session_id=session_id, archived=True)


async def unarchive_session(
    pool: Any,
    session_id: str,
) -> ArchiveResponse | None:
    """아카이브된 세션을 복원한다 (Undo 삭제)."""
    success = await message_store.unarchive_session(pool, session_id)
    if not success:
        return None
    return ArchiveResponse(session_id=session_id, archived=False)


async def update_session_title(
    pool: Any,
    session_id: str,
    title: str,
) -> bool:
    """세션 제목을 수정한다."""
    return await message_store.update_session_title(pool, session_id, title)
