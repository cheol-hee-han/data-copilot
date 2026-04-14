"""GET /api/sessions/{session_id}/active 엔드포인트 테스트.

테스트 범위:
  1. store 가 active=True 이면 응답에 active=True
  2. store 미설정 시 active=False (안전한 기본값)
  3. path parameter 정규식 검증 — 부적절한 session_id 는 422
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.agents.graph.active_run import (
    reset_active_run_store,
    set_active_run_store,
)
from src.routers.sessions import router
from src.services.active_run_store import MemoryActiveRunStore


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    reset_active_run_store()


class TestGetSessionActive:
    def test_active_true(self, client: TestClient):
        store = MemoryActiveRunStore()
        # 동기 이벤트 루프에서 직접 dict 조작 — MemoryStore 의 내부 상태를
        # 테스트용으로 세팅 (실 운영에서는 mark_active 로 세팅)
        store._active["session-123"] = "turn_x"
        set_active_run_store(store)

        resp = client.get("/api/sessions/session-123/active")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"session_id": "session-123", "active": True}

    def test_active_false_when_not_registered(self, client: TestClient):
        set_active_run_store(MemoryActiveRunStore())
        resp = client.get("/api/sessions/session-999/active")
        assert resp.status_code == 200
        assert resp.json() == {"session_id": "session-999", "active": False}

    def test_active_false_when_store_missing(self, client: TestClient):
        """스토어 미설정 시 안전한 기본값 False."""
        reset_active_run_store()
        resp = client.get("/api/sessions/session-abc/active")
        assert resp.status_code == 200
        assert resp.json()["active"] is False

    @pytest.mark.parametrize("bad_id", [
        "foo:bar",          # 콜론 (Redis 키 오염)
        "foo*bar",          # 와일드카드
        "foo bar",          # 공백
        "x" * 129,          # 너무 김
    ])
    def test_invalid_session_id_returns_422(
        self, client: TestClient, bad_id: str,
    ):
        """경로 정규식을 위반하는 session_id 는 422."""
        set_active_run_store(MemoryActiveRunStore())
        resp = client.get(f"/api/sessions/{bad_id}/active")
        assert resp.status_code in (404, 422)

    @pytest.mark.parametrize("good_id", [
        "session-1775801643744",  # 현재 embedded.html 포맷
        "abc123",
        "uuid-like-string",
        "session_underscore",
    ])
    def test_valid_session_id_formats(
        self, client: TestClient, good_id: str,
    ):
        set_active_run_store(MemoryActiveRunStore())
        resp = client.get(f"/api/sessions/{good_id}/active")
        assert resp.status_code == 200
