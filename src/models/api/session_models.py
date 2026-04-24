"""세션/메시지 REST API 요청·응답 모델.

작성자: 한철희 / 최종수정: 2026-04-14

대화 이력 관리 REST API(/api/sessions, /api/messages)의
요청 바디와 응답 스키마를 정의한다.

핵심 모델:
    - SessionSummary: 세션 목록의 개별 항목
    - SessionListResponse: GET /api/sessions 응답
    - MessageSummary: 세션 상세의 개별 메시지 (Tier 1 — metadata 제외)
    - SessionDetailResponse: GET /api/sessions/{id} 응답
    - MessageMetadataResponse: GET /api/messages/{id}/metadata 응답 (Tier 2)
    - LikeRequest / LikeResponse: PATCH /api/messages/{id}/like
    - DownloadResponse: PATCH /api/messages/{id}/download
    - ArchiveResponse: DELETE /api/sessions/{id}
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class SessionSummary(BaseModel):
    """세션 목록의 개별 항목."""

    session_id: str
    title: str | None = None
    last_active: datetime
    created_at: datetime


class SessionListResponse(BaseModel):
    """GET /api/sessions 응답."""

    sessions: list[SessionSummary]
    total_count: int


class MessageSummary(BaseModel):
    """세션 상세의 개별 메시지 (Tier 1 — metadata 제외)."""

    message_uuid: str
    seq: int
    role: str
    content: str
    message_type: str = "normal"
    status: str = "success"
    is_liked: bool | None = None
    feedback: str | None = None
    is_downloaded: bool = False
    has_metadata: bool = False
    created_at: datetime
    trace_files: list[dict[str, str]] = Field(
        default_factory=list,
        description="Trace 파일 목록 [{name, filename}, ...]",
    )
    process_summary: dict[str, Any] | None = None
    result_data_meta: dict[str, Any] | None = None
    visualization: dict[str, Any] | None = Field(
        default=None,
        description="시각화 데이터 (Tier 1 — 세션 복원 시 즉시 렌더링).",
    )
    clarification: dict[str, Any] | None = Field(
        default=None,
        description="명확화 요청 데이터 (Tier 1 — 세션 복원 시 카드 렌더링).",
    )


class SessionDetailResponse(BaseModel):
    """GET /api/sessions/{session_id} 응답."""

    session_id: str
    title: str | None = None
    messages: list[MessageSummary]


class MessageMetadataResponse(BaseModel):
    """GET /api/messages/{message_uuid}/metadata 응답 (Tier 2)."""

    message_uuid: str
    metadata: dict[str, Any]


class LikeRequest(BaseModel):
    """PATCH /api/messages/{message_uuid}/like 요청."""

    is_liked: bool | None = Field(
        ..., description="true=좋아요, false=싫어요, null=취소",
    )
    feedback: str | None = Field(
        None, description="피드백 사유 (좋아요: 정확한 데이터/유용한 분석 등, 싫어요: 잘못된 데이터/SQL 오류 등)",
    )


class LikeResponse(BaseModel):
    """PATCH /api/messages/{message_uuid}/like 응답."""

    message_uuid: str
    is_liked: bool | None
    feedback: str | None = None
    liked_at: datetime | None = None


class DownloadResponse(BaseModel):
    """PATCH /api/messages/{message_uuid}/download 응답."""

    message_uuid: str
    is_downloaded: bool
    downloaded_at: datetime | None = None


class ArchiveResponse(BaseModel):
    """DELETE /api/sessions/{session_id} 응답."""

    session_id: str
    archived: bool


class SessionActiveResponse(BaseModel):
    """GET /api/sessions/{session_id}/active 응답.

    해당 세션에 현재 실행 중인 파이프라인이 있는지 bool 로 반환한다.
    서버 프로세스 메모리(또는 Redis) 기반 조회이므로 서버 재기동 후에는
    자동으로 false 가 되어 크래시로 버려진 턴을 구분할 수 있다.
    """

    session_id: str
    active: bool
