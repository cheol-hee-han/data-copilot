"""세션 스토어 제거 검증 테스트.

테스트 대상:
    - message_store.get_conversation_history: turn_type 필드 반환 검증
    - intent_classifier._format_history: clarification 필터링 호환성
    - config.py: session_backend 제거, redis_backend 신설 확인
    - main.py: session import 제거 확인

외부 의존성 없음 (DB/Redis 불필요, mock 기반).

=== 단독 실행 ===
    python -m pytest tests/auto/unit/test_session_store_removal.py -v
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. config.py — 세션 스토어 설정 제거, redis_backend 신설
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestConfigSessionRemoval:
    """config.py에서 세션 스토어 관련 설정이 제거되었는지 검증."""

    def test_session_backend_removed(self):
        """session_backend 필드가 제거되었다."""
        from src.config import Settings
        assert not hasattr(Settings.model_fields, "session_backend") or \
            "session_backend" not in Settings.model_fields

    def test_session_ttl_removed(self):
        """session_ttl 필드가 제거되었다."""
        from src.config import Settings
        assert "session_ttl" not in Settings.model_fields

    def test_session_clarify_ttl_removed(self):
        """session_clarify_ttl 필드가 제거되었다."""
        from src.config import Settings
        assert "session_clarify_ttl" not in Settings.model_fields

    def test_session_max_history_removed(self):
        """session_max_history 필드가 제거되었다."""
        from src.config import Settings
        assert "session_max_history" not in Settings.model_fields

    def test_max_sessions_removed(self):
        """max_sessions 필드가 제거되었다."""
        from src.config import Settings
        assert "max_sessions" not in Settings.model_fields

    def test_redis_backend_exists(self):
        """redis_backend 필드가 신설되었다."""
        from src.config import Settings
        assert "redis_backend" in Settings.model_fields

    def test_redis_backend_default_memory(self):
        """redis_backend 기본값이 'memory'이다."""
        from src.config import Settings
        s = Settings()
        assert s.redis_backend == "memory"

    def test_prompt_history_window_preserved(self):
        """prompt_history_window 필드가 유지되었다."""
        from src.config import Settings
        assert "prompt_history_window" in Settings.model_fields

    def test_active_run_ttl_preserved(self):
        """active_run_ttl_seconds 필드가 유지되었다."""
        from src.config import Settings
        assert "active_run_ttl_seconds" in Settings.model_fields

    def test_redis_connection_settings_preserved(self):
        """Redis 접속 정보(host/port/db/password)가 유지되었다."""
        from src.config import Settings
        for field in ("redis_host", "redis_port", "redis_db", "redis_password"):
            assert field in Settings.model_fields, f"{field} 누락"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. 세션 스토어 모듈 제거 확인
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestSessionModuleRemoved:
    """src/services/session/ 디렉토리가 제거되었는지 검증."""

    def test_session_directory_removed(self):
        """src/services/session/ 디렉토리가 존재하지 않는다."""
        session_dir = (
            Path(__file__).resolve().parents[3]
            / "src" / "services" / "session"
        )
        assert not session_dir.exists(), (
            f"session 디렉토리가 아직 존재합니다: {session_dir}"
        )

    def test_session_store_import_fails(self):
        """src.services.session 모듈을 import할 수 없다."""
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("src.services.session")

    def test_session_store_not_in_main_imports(self):
        """main.py 소스에서 session store import가 없다."""
        main_path = (
            Path(__file__).resolve().parents[3]
            / "src" / "main.py"
        )
        source = main_path.read_text(encoding="utf-8")
        assert "get_session_store" not in source
        assert "HistoryEntryType" not in source
        assert "from src.services.session" not in source


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. message_store.get_conversation_history — turn_type 반환
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestGetConversationHistory:
    """get_conversation_history()가 turn_type을 type 키로 매핑 반환."""

    @pytest.fixture
    def mock_pool(self):
        """mock psycopg pool."""
        conn = AsyncMock()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        pool = MagicMock()
        pool.connection.return_value = ctx
        return pool, conn

    @pytest.mark.asyncio
    async def test_returns_type_field_from_turn_type(self, mock_pool):
        """turn_type 값이 type 키로 매핑되어 반환된다."""
        pool, conn = mock_pool
        # DB 결과 시뮬레이션
        cursor = AsyncMock()
        cursor.fetchall = AsyncMock(return_value=[
            {"role": "user", "content": "질문1", "message_type": "normal"},
            {"role": "assistant", "content": "답변1", "message_type": "normal"},
            {"role": "user", "content": "명확화 답변", "message_type": "clarification"},
        ])
        conn.execute = AsyncMock(return_value=cursor)

        from src.services.message_store import get_conversation_history
        result = await get_conversation_history(pool, "test-session")

        assert len(result) == 3
        assert result[0]["type"] == "normal"
        assert result[1]["type"] == "normal"
        assert result[2]["type"] == "clarification"

    @pytest.mark.asyncio
    async def test_null_turn_type_defaults_to_normal(self, mock_pool):
        """turn_type이 NULL이면 'normal'로 기본값 적용."""
        pool, conn = mock_pool
        cursor = AsyncMock()
        cursor.fetchall = AsyncMock(return_value=[
            {"role": "user", "content": "질문", "message_type": None},
        ])
        conn.execute = AsyncMock(return_value=cursor)

        from src.services.message_store import get_conversation_history
        result = await get_conversation_history(pool, "test-session")

        assert result[0]["type"] == "normal"

    @pytest.mark.asyncio
    async def test_empty_history(self, mock_pool):
        """이력이 없으면 빈 리스트 반환."""
        pool, conn = mock_pool
        cursor = AsyncMock()
        cursor.fetchall = AsyncMock(return_value=[])
        conn.execute = AsyncMock(return_value=cursor)

        from src.services.message_store import get_conversation_history
        result = await get_conversation_history(pool, "test-session")

        assert result == []

    @pytest.mark.asyncio
    async def test_result_contains_role_and_content(self, mock_pool):
        """반환값에 role, content 필드가 포함된다."""
        pool, conn = mock_pool
        cursor = AsyncMock()
        cursor.fetchall = AsyncMock(return_value=[
            {"role": "user", "content": "테스트", "message_type": "normal"},
        ])
        conn.execute = AsyncMock(return_value=cursor)

        from src.services.message_store import get_conversation_history
        result = await get_conversation_history(pool, "test-session")

        assert result[0]["role"] == "user"
        assert result[0]["content"] == "테스트"
        assert "type" in result[0]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. _format_history — clarification 필터링 호환성
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestFormatHistoryCompatibility:
    """_format_history()가 DB 반환값의 type 필드로 올바르게 필터링."""

    def test_normal_type_passes_filter(self):
        """type='normal'인 항목은 필터링되지 않는다."""
        from src.services.intent_classifier import _format_history

        history = [
            {"role": "user", "content": "질문1", "type": "normal"},
            {"role": "assistant", "content": "답변1", "type": "normal"},
        ]
        result = _format_history(history)
        assert "질문1" in result
        assert "답변1" in result

    def test_clarification_type_filtered(self):
        """type='clarification'인 항목은 제외된다."""
        from src.services.intent_classifier import _format_history

        history = [
            {"role": "user", "content": "일반 질문", "type": "normal"},
            {"role": "assistant", "content": "명확화 질문", "type": "clarification"},
            {"role": "user", "content": "명확화 답변", "type": "clarification"},
            {"role": "user", "content": "후속 질문", "type": "normal"},
        ]
        result = _format_history(history)
        assert "일반 질문" in result
        assert "후속 질문" in result
        assert "명확화 질문" not in result
        assert "명확화 답변" not in result

    def test_missing_type_defaults_to_query(self):
        """type 키가 없으면 기본값 'query'로 처리 (필터 통과)."""
        from src.services.intent_classifier import _format_history

        history = [
            {"role": "user", "content": "타입없는 질문"},
        ]
        result = _format_history(history)
        assert "타입없는 질문" in result

    def test_max_turns_windowing(self):
        """max_turns로 최근 N턴만 포함."""
        from src.services.intent_classifier import _format_history

        history = [
            {"role": "user", "content": f"질문{i}", "type": "normal"}
            for i in range(10)
        ]
        result = _format_history(history, max_turns=2)
        assert "질문8" in result
        assert "질문9" in result
        assert "질문7" not in result

    def test_max_turns_zero_returns_all(self):
        """max_turns=0이면 전체 이력 반환."""
        from src.services.intent_classifier import _format_history

        history = [
            {"role": "user", "content": f"질문{i}", "type": "normal"}
            for i in range(20)
        ]
        result = _format_history(history, max_turns=0)
        assert "질문0" in result
        assert "질문19" in result

    def test_mixed_types_only_normal_counted(self):
        """clarification 제외 후 max_turns 적용."""
        from src.services.intent_classifier import _format_history

        history = [
            {"role": "user", "content": "질문1", "type": "normal"},
            {"role": "assistant", "content": "답변1", "type": "normal"},
            {"role": "assistant", "content": "명확화", "type": "clarification"},
            {"role": "user", "content": "질문2", "type": "normal"},
            {"role": "assistant", "content": "답변2", "type": "normal"},
        ]
        # clarification 제외 후 4건 중 최근 2건
        result = _format_history(history, max_turns=2)
        assert "질문2" in result
        assert "답변2" in result
        assert "질문1" not in result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. main.py — /reset 명령 제거 확인
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestSlashCommandCleanup:
    """슬래시 명령어에서 /reset이 제거되었는지 검증."""

    def test_reset_command_removed_from_source(self):
        """/reset 관련 코드가 main.py에서 제거되었다."""
        main_path = (
            Path(__file__).resolve().parents[3]
            / "src" / "main.py"
        )
        source = main_path.read_text(encoding="utf-8")
        # /reset 명령어 처리 코드가 없어야 함
        assert 'command == "/reset"' not in source
        assert "clear_session" not in source

    def test_history_command_preserved(self):
        """/history 명령어는 유지되었다."""
        main_path = (
            Path(__file__).resolve().parents[3]
            / "src" / "main.py"
        )
        source = main_path.read_text(encoding="utf-8")
        assert '"/history"' in source


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 6. main.py — lifespan에서 Redis 직접 연결 패턴
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestLifespanRedisPattern:
    """lifespan에서 redis_backend 설정 기반 초기화 검증."""

    def test_redis_backend_setting_used_in_main(self):
        """main.py에서 settings.redis_backend을 참조한다."""
        main_path = (
            Path(__file__).resolve().parents[3]
            / "src" / "main.py"
        )
        source = main_path.read_text(encoding="utf-8")
        assert "settings.redis_backend" in source

    def test_session_backend_not_used_in_main(self):
        """main.py에서 session_backend을 더 이상 참조하지 않는다."""
        main_path = (
            Path(__file__).resolve().parents[3]
            / "src" / "main.py"
        )
        source = main_path.read_text(encoding="utf-8")
        assert "session_backend" not in source

    def test_redis_close_in_finally(self):
        """lifespan finally 블록에 _redis.close()가 있다."""
        main_path = (
            Path(__file__).resolve().parents[3]
            / "src" / "main.py"
        )
        source = main_path.read_text(encoding="utf-8")
        assert "_redis.close()" in source

    def test_redis_none_guard_in_finally(self):
        """_redis가 None일 때 close를 호출하지 않는 가드가 있다."""
        main_path = (
            Path(__file__).resolve().parents[3]
            / "src" / "main.py"
        )
        source = main_path.read_text(encoding="utf-8")
        assert "_redis is not None" in source
