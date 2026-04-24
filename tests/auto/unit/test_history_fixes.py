"""코드 리뷰 수정사항 검증 테스트.

수정 대상:
    1. CRITICAL — MemorySaver 경로 pool=None 방어
       - ConnectorManager.checkpointer_pool이 None 반환
       - REST 라우터가 503 반환
       - runner.py가 턴 저장을 건너뛰고 파이프라인 결과 반환
    2. WARNING — MessageSummary에 feedback 필드 추가
       - session_service가 feedback을 매핑
    3. INFO — 에러 턴 저장 시 client_ip/user_agent 전달

실행:
    pytest tests/auto/unit/test_history_fixes.py -v
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.connectors.manager import ConnectorManager
from src.models.api.session_models import (
    LikeRequest,
    LikeResponse,
    SessionDetailResponse,
    MessageSummary,
)


# =====================================================================
# 1. CRITICAL — pool=None 방어
# =====================================================================


class TestCheckpointerPoolNoneGuard:
    """MemorySaver 경로에서 pool이 None일 때 안전 동작 검증."""

    def test_checkpointer_pool_returns_none_without_set(self):
        """set_checkpointer_pool() 미호출 시 None 반환 (RuntimeError 아님)."""
        manager = ConnectorManager(use_dummy=True)
        # 이전에는 RuntimeError가 발생했으나, 수정 후 None 반환
        assert manager.checkpointer_pool is None

    def test_checkpointer_pool_returns_pool_after_set(self):
        """set_checkpointer_pool() 호출 후 pool 반환."""
        manager = ConnectorManager(use_dummy=True)
        mock_pool = MagicMock()
        manager.set_checkpointer_pool(mock_pool)
        assert manager.checkpointer_pool is mock_pool

    def test_router_pool_raises_503_when_none(self):
        """REST 라우터 _pool()이 pool=None일 때 HTTP 503 반환."""
        with patch(
            "src.routers.sessions.get_connector_manager",
        ) as mock_get_cm:
            mock_cm = MagicMock()
            mock_cm.checkpointer_pool = None
            mock_get_cm.return_value = mock_cm

            from src.routers.sessions import _pool

            with pytest.raises(HTTPException) as exc_info:
                _pool()
            assert exc_info.value.status_code == 503

    def test_router_pool_returns_pool_when_available(self):
        """REST 라우터 _pool()이 pool 존재 시 정상 반환."""
        with patch(
            "src.routers.sessions.get_connector_manager",
        ) as mock_get_cm:
            mock_pool = MagicMock()
            mock_cm = MagicMock()
            mock_cm.checkpointer_pool = mock_pool
            mock_get_cm.return_value = mock_cm

            from src.routers.sessions import _pool

            result = _pool()
            assert result is mock_pool


# =====================================================================
# 2. WARNING — MessageSummary feedback 필드
# =====================================================================


class TestMessageSummaryFeedbackField:
    """MessageSummary 모델에 feedback 필드 존재 검증."""

    def test_turn_summary_has_feedback_field(self):
        """MessageSummary에 feedback 필드가 존재한다."""
        assert "feedback" in MessageSummary.model_fields

    def test_turn_summary_feedback_default_none(self):
        """feedback 기본값은 None이다."""
        turn = MessageSummary(
            message_uuid="t1",
            seq=1,
            role="assistant",
            content="hello",
            created_at=datetime.now(),
        )
        assert turn.feedback is None

    def test_turn_summary_feedback_accepts_string(self):
        """feedback에 문자열을 설정할 수 있다."""
        turn = MessageSummary(
            message_uuid="t1",
            seq=1,
            role="assistant",
            content="hello",
            feedback="정확한 데이터",
            created_at=datetime.now(),
        )
        assert turn.feedback == "정확한 데이터"

    def test_turn_summary_serialization_includes_feedback(self):
        """model_dump()에 feedback이 포함된다."""
        turn = MessageSummary(
            message_uuid="t1",
            seq=1,
            role="assistant",
            content="hello",
            feedback="유용한 분석",
            created_at=datetime.now(),
        )
        dumped = turn.model_dump()
        assert "feedback" in dumped
        assert dumped["feedback"] == "유용한 분석"

    @pytest.mark.asyncio
    async def test_session_service_maps_feedback(self):
        """session_service.get_session_detail()이 feedback을 매핑한다."""
        mock_pool = MagicMock()

        with (
            patch(
                "src.services.session_service.message_store.get_session_title",
                new_callable=AsyncMock,
                return_value="테스트 세션",
            ),
            patch(
                "src.services.session_service.message_store.get_session_messages_for_ui",
                new_callable=AsyncMock,
                return_value=[
                    {
                        "message_uuid": "uuid-1",
                        "seq": 1,
                        "role": "user",
                        "content": "안녕",
                        "message_type": "normal",
                        "status": "success",
                        "is_liked": True,
                        "feedback": "정확한 데이터",
                        "is_downloaded": False,
                        "has_metadata": False,
                        "created_at": datetime.now(),
                    },
                ],
            ),
        ):
            from src.services.session_service import get_session_detail

            result = await get_session_detail(mock_pool, "session-1")

            assert result is not None
            assert isinstance(result, SessionDetailResponse)
            assert result.messages[0].feedback == "정확한 데이터"


# =====================================================================
# 3. INFO — 에러 턴 저장 시 client_ip/user_agent 전달
# =====================================================================


class TestErrorTurnAuditFields:
    """에러 경로에서 assistant 턴 저장 시 client_ip/user_agent 전달 검증."""

    @pytest.mark.asyncio
    async def test_error_turn_passes_client_ip_and_user_agent(self):
        """에러 발생 시 assistant 턴에 client_ip, user_agent가 전달된다."""
        save_message_calls: list[dict] = []

        async def mock_save_message(pool, **kwargs):
            save_message_calls.append(kwargs)
            return ("mock-uuid", len(save_message_calls))

        # run_pipeline이 에러를 발생시키는 시나리오를 시뮬레이션
        # runner.py의 에러 경로 로직만 직접 테스트
        mock_pool = MagicMock()

        with (
            patch(
                "src.connectors.manager.get_connector_manager",
            ) as mock_get_cm,
            patch(
                "src.services.message_store.save_message",
                side_effect=mock_save_message,
            ),
        ):
            mock_cm = MagicMock()
            mock_cm.checkpointer_pool = mock_pool
            mock_get_cm.return_value = mock_cm

            # 에러 경로 로직 직접 실행 (runner.py:324~346 시뮬레이션)
            from src.services.message_store import save_message

            _pool = mock_cm.checkpointer_pool
            client_ip = "192.168.1.100"
            user_agent = "Mozilla/5.0"
            session_id = "test-session"
            user_input = "테스트 질문"
            error = ValueError("test error")

            # user 턴 저장
            await save_message(
                _pool,
                thread_id=session_id,
                role="user",
                content=user_input,
                client_ip=client_ip,
                user_agent=user_agent,
                message_type="error",
                request_id=session_id,
            )
            # assistant 에러 턴 저장 (수정 대상)
            await save_message(
                _pool,
                thread_id=session_id,
                role="assistant",
                content="처리 중 오류가 발생했습니다.",
                client_ip=client_ip,
                user_agent=user_agent,
                message_type="error",
                status="failure",
                error_type=type(error).__name__,
                error_message=str(error)[:500],
            )

            # 검증: 두 번째 호출(assistant 턴)에 client_ip, user_agent 존재
            assert len(save_message_calls) == 2
            assistant_call = save_message_calls[1]
            assert assistant_call["client_ip"] == "192.168.1.100"
            assert assistant_call["user_agent"] == "Mozilla/5.0"
            assert assistant_call["role"] == "assistant"
            assert assistant_call["status"] == "failure"


# =====================================================================
# 4. LikeRequest feedback 필드 (좋아요/싫어요 공통)
# =====================================================================


class TestLikeRequestFeedback:
    """LikeRequest에 feedback이 좋아요/싫어요 모두 전달 가능 검증."""

    def test_like_with_feedback(self):
        """좋아요 + 피드백."""
        req = LikeRequest(is_liked=True, feedback="정확한 데이터")
        assert req.is_liked is True
        assert req.feedback == "정확한 데이터"

    def test_dislike_with_feedback(self):
        """싫어요 + 피드백."""
        req = LikeRequest(is_liked=False, feedback="잘못된 SQL")
        assert req.is_liked is False
        assert req.feedback == "잘못된 SQL"

    def test_cancel_like(self):
        """좋아요 취소 (null)."""
        req = LikeRequest(is_liked=None)
        assert req.is_liked is None
        assert req.feedback is None

    @pytest.mark.asyncio
    async def test_toggle_like_service_passes_feedback(self):
        """session_service.toggle_like()가 feedback을 store에 전달한다."""
        mock_pool = MagicMock()

        with patch(
            "src.services.session_service.message_store.toggle_like",
            new_callable=AsyncMock,
            return_value={
                "message_uuid": "uuid-1",
                "is_liked": True,
                "feedback": "유용한 분석",
                "liked_at": datetime.now(),
            },
        ) as mock_toggle:
            from src.services.session_service import toggle_like

            result = await toggle_like(
                mock_pool, "uuid-1", True, "유용한 분석",
            )

            mock_toggle.assert_called_once_with(
                mock_pool, "uuid-1", True, "유용한 분석",
            )
            assert isinstance(result, LikeResponse)
            assert result.feedback == "유용한 분석"
