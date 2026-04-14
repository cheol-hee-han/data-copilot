"""세션/메시지 관리 REST API 라우터.

작성자: 한철희 / 최종수정: 2026-04-14

엔드포인트:
    GET    /api/sessions                        — 세션 목록
    GET    /api/sessions/{session_id}           — 세션 메시지 목록 (Tier 1)
    GET    /api/messages/{message_uuid}/metadata  — 메시지 metadata (Tier 2)
    PATCH  /api/messages/{message_uuid}/like      — 좋아요/싫어요 토글
    PATCH  /api/messages/{message_uuid}/download  — 다운로드 기록
    GET    /api/traces/{filename}               — Trace 파일 다운로드
    DELETE /api/sessions/{session_id}           — 세션 아카이브
    PATCH  /api/sessions/{session_id}/unarchive — 세션 복원 (Undo 삭제)
    PATCH  /api/sessions/{session_id}/title     — 세션 제목 수정
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from fastapi import Path as PathParam
from fastapi.responses import FileResponse
from pydantic import BaseModel

from src.connectors.manager import get_connector_manager
from src.models.api.session_models import (
    ArchiveResponse,
    DownloadResponse,
    LikeRequest,
    LikeResponse,
    MessageMetadataResponse,
    SessionActiveResponse,
    SessionDetailResponse,
    SessionListResponse,
)
from src.services import session_service
from src.utils.logger import get_logger

# Redis 키 오염 방지: 영숫자·하이픈·언더스코어만 허용.
# 현재 session_id 포맷: `session-{epoch_ms}` (embedded.html) 및 UUID 허용.
_SESSION_ID_PATTERN = r"^[A-Za-z0-9_-]{1,128}$"

logger = get_logger(__name__)

router = APIRouter(
    prefix="/api",
    tags=["sessions"],
    responses={503: {"description": "대화 이력 DB 미연결"}},
)


def _pool() -> Any:
    """checkpointer DB 풀을 반환하고, 미연결 시 503을 발생시킨다."""
    pool = get_connector_manager().checkpointer_pool
    if pool is None:
        raise HTTPException(
            503, "대화 이력 서비스를 사용할 수 없습니다 (DB 미연결).",
        )
    return pool


# ── 세션 ──

@router.get("/sessions", response_model=SessionListResponse)
async def list_sessions(
    user_id: str = Query("anonymous"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> Any:
    """사용자의 세션 목록을 조회한다."""
    return await session_service.list_sessions(_pool(), user_id, limit, offset)


@router.get("/sessions/{session_id}", response_model=SessionDetailResponse)
async def get_session(session_id: str) -> Any:
    """특정 세션의 메시지 목록을 조회한다 (Tier 1)."""
    result = await session_service.get_session_detail(_pool(), session_id)
    if result is None:
        raise HTTPException(404, "세션을 찾을 수 없습니다.")
    return result


@router.get(
    "/sessions/{session_id}/active",
    response_model=SessionActiveResponse,
)
async def get_session_active(
    session_id: Annotated[str, PathParam(pattern=_SESSION_ID_PATTERN)],
) -> Any:
    """해당 세션에 현재 실행 중인 파이프라인이 있는지 반환한다.

    서버 프로세스 메모리(또는 Redis) 기반 조회. DB 접근 없음.
    서버 재기동 시 자동으로 비어있으므로 크래시로 버려진 턴과 실제
    실행 중인 턴을 구분하는 용도.
    """
    from src.agents.graph.active_run import check_active
    active = await check_active(session_id)
    return SessionActiveResponse(session_id=session_id, active=active)


@router.delete("/sessions/{session_id}", response_model=ArchiveResponse)
async def delete_session(session_id: str) -> Any:
    """세션을 아카이브한다 (soft delete)."""
    result = await session_service.archive_session(_pool(), session_id)
    if result is None:
        raise HTTPException(404, "세션을 찾을 수 없습니다.")
    return result


@router.patch("/sessions/{session_id}/unarchive", response_model=ArchiveResponse)
async def unarchive_session(session_id: str) -> Any:
    """아카이브된 세션을 복원한다 (Undo 삭제)."""
    result = await session_service.unarchive_session(_pool(), session_id)
    if result is None:
        raise HTTPException(404, "복원할 세션을 찾을 수 없습니다.")
    return result


class TitleUpdateRequest(BaseModel):
    """세션 제목 수정 요청 바디."""

    title: str


@router.patch("/sessions/{session_id}/title")
async def update_session_title(session_id: str, body: TitleUpdateRequest) -> Any:
    """세션 제목을 수정한다."""
    success = await session_service.update_session_title(
        _pool(), session_id, body.title,
    )
    if not success:
        raise HTTPException(404, "세션을 찾을 수 없습니다.")
    return {"session_id": session_id, "title": body.title}


# ── 메시지 ──

@router.get("/messages/{message_uuid}/metadata", response_model=MessageMetadataResponse)
async def get_message_metadata(message_uuid: str) -> Any:
    """특정 메시지의 metadata를 조회한다 (Tier 2)."""
    result = await session_service.get_message_metadata(_pool(), message_uuid)
    if result is None:
        raise HTTPException(404, "메시지를 찾을 수 없습니다.")
    return result


@router.patch("/messages/{message_uuid}/like", response_model=LikeResponse)
async def toggle_like(message_uuid: str, body: LikeRequest) -> Any:
    """메시지에 좋아요/싫어요를 설정한다."""
    result = await session_service.toggle_like(
        _pool(), message_uuid, body.is_liked, body.feedback,
    )
    if result is None:
        raise HTTPException(404, "메시지를 찾을 수 없습니다.")
    return result


@router.patch("/messages/{message_uuid}/download", response_model=DownloadResponse)
async def mark_download(message_uuid: str) -> Any:
    """메시지의 다운로드를 기록한다."""
    result = await session_service.mark_downloaded(_pool(), message_uuid)
    if result is None:
        raise HTTPException(404, "메시지를 찾을 수 없습니다.")
    return result


# ── Trace 파일 다운로드 ──


@router.get("/traces/{filename}")
async def download_trace(filename: str) -> Any:
    """Trace 파일을 다운로드한다.

    경로 순회 공격을 방지하기 위해 파일명에서 디렉토리 구분자를 제거하고,
    ``settings.eval_tracker_output_dir`` 내 파일만 허용한다.
    """
    from src.config import settings

    # 경로 순회 방어: 파일명에서 디렉토리 구분자 제거
    safe_name = Path(filename).name
    if not safe_name or safe_name != filename:
        raise HTTPException(400, "잘못된 파일명입니다.")

    trace_dir = Path(settings.eval_tracker_output_dir).resolve()
    filepath = (trace_dir / safe_name).resolve()

    # resolve 후 경로가 trace_dir 내에 있는지 먼저 확인 (TOCTOU 방어)
    try:
        filepath.relative_to(trace_dir)
    except ValueError:
        raise HTTPException(400, "잘못된 파일 경로입니다.")

    if not filepath.is_file():
        raise HTTPException(404, "파일을 찾을 수 없습니다.")

    media_type = (
        "application/json"
        if filepath.suffix == ".json"
        else "text/markdown; charset=utf-8"
    )
    return FileResponse(
        path=filepath,
        filename=safe_name,
        media_type=media_type,
    )


# ── 파이프라인 취소 ──


@router.post("/sessions/{session_id}/cancel")
async def cancel_pipeline(
    session_id: str,
    turn_id: str = Query(
        default="*",
        description="취소 대상 턴 ID. 미지정 시 현재 활성 턴 취소.",
    ),
) -> Any:
    """파이프라인 실행 중단을 요청한다.

    취소 플래그를 설정하면 다음 노드 진입 시
    파이프라인이 정상 종료(CANCELLED)된다.
    """
    from src.agents.graph.cancel import get_cancel_store

    store = get_cancel_store()
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="취소 기능 미활성",
        )

    await store.set_cancel(session_id, turn_id)
    logger.info(
        "파이프라인 취소 요청",
        session_id=session_id,
        turn_id=turn_id,
    )
    return {
        "status": "cancel_requested",
        "session_id": session_id,
        "turn_id": turn_id,
    }