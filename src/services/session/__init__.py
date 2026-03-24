"""세션 관리 서비스.

대화 이력과 명확화 상태를 백엔드(memory/redis)에 저장·조회·삭제한다.
settings.session_backend 설정에 따라 구현체를 자동 선택한다.
"""

from src.services.session.store import get_session_store, SessionStore

__all__ = ["get_session_store", "SessionStore"]
