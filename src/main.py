"""FastAPI 서버 — Data Copilot 프로세스 진입점.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

자연어 데이터 추출/분석 요청을 받아 LangGraph 파이프라인을 실행하고
결과를 반환하는 웹 서버이다. WebSocket(/ws/{session_id})을 통한 실시간 챗봇 통신과
REST API(/api/query)를 통한 단건 요청을 모두 지원한다.
세션별 대화 이력을 메모리에 관리하며(최대 세션 수 제한), 모든 입력에 대해
프롬프트 인젝션 감지와 PII 마스킹을 적용한다.
서버 기동 시(lifespan) 로깅 초기화, LangSmith 트레이싱 설정, 커넥터 일괄 연결을 수행하고,
종료 시 커넥터를 일괄 해제한다.

핵심 함수/클래스:
    - lifespan: FastAPI 수명주기 관리 (로깅/LangSmith/커넥터 초기화 및 정리)
    - websocket_endpoint: WebSocket 기반 실시간 챗봇 (세션 관리, 보안 검증 포함)
    - query_endpoint: REST POST 기반 단건 쿼리 (시각화/trace 옵션 지원)
    - health_check: 커넥터 및 LLM API 상태 확인
    - QueryRequest: Pydantic v2 요청 모델 (입력 길이/타입 자동 검증)

설정 커스터마이징: settings.max_sessions로 최대 세션 수를 제어하고,
session_id는 영숫자/하이픈/밑줄만 허용하여 경로 순회 및 주입을 차단한다.

기동 예시::

    # 개발 (hot-reload)uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
    

    # 운영
    uvicorn src.main:app --host 0.0.0.0 --port 8000 --workers 4
"""

from __future__ import annotations

import csv
import io
import json
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError
from starlette.middleware.base import BaseHTTPMiddleware

from src.agents.graph.checkpointer import create_checkpointer
from src.agents.graph.pipeline import get_compiled_app
from src.agents.models.user_messages import ERR_GENERIC, format_error
from src.config import settings
from src.connectors.manager import get_connector_manager
from src.agents.graph.runner import run_pipeline
from src.routers.sessions import router as sessions_router
from src.services.session import get_session_store
from src.services.session.store import HistoryEntryType
from src.services.turn_text_store import get_conversation_history
from src.tools.langsmith import setup_langsmith
from src.utils.logger import get_logger, setup_logging, shutdown_logging
from src.utils.security import detect_prompt_injection, mask_pii
from src.utils.truncate import truncate_log

logger = get_logger(__name__)

# WebSocket session_id 허용 패턴 — 영숫자·하이픈·밑줄만 허용
# 경로 순회(../../etc/passwd)·주입 문자 차단
_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,128}$")


def _is_valid_session_id(session_id: str) -> bool:
    """session_id가 허용된 형식인지 검증한다."""
    return bool(_SESSION_ID_RE.match(session_id))


class QueryRequest(BaseModel):
    """REST API 쿼리 요청 모델."""

    query: str = Field(
        ..., min_length=1, max_length=2000, description="자연어 질의"
    )
    session_id: str | None = Field(
        default=None, description="세션 ID (없으면 자동 생성)"
    )
    include_trace: bool = Field(
        default=False, description="추론 과정 추적 로그 포함 여부"
    )
    include_insight: bool = Field(
        default=False, description="통찰 데이터 포함 여부",
    )


# 필수 커넥터 — 실패 시 서버 기동 중단
_REQUIRED_CONNECTORS = {"mongodb", "info_db", "history_db", "qdrant"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """서버 시작/종료 시 리소스 관리.

    try/finally로 기동 실패 시에도 리소스 정리를 보장한다.
    필수 커넥터 연결 실패 시 서버 기동을 중단한다.
    """
    setup_logging()
    setup_langsmith()
    manager = get_connector_manager()
    store = get_session_store()

    try:
        await manager.connect_all()

        # 기동 시 실제 연결 검증 — lazy 초기화 커넥터의 연결 실패를 조기 감지.
        # health_check_all()에 커넥터별 타임아웃(5초)이 적용되어 hang하지 않는다.
        statuses = await manager.health_check_all()
        for name in _REQUIRED_CONNECTORS:
            if not statuses.get(name, False):
                logger.error(
                    "필수 커넥터 연결 실패 — 서버 기동 중단",
                    connector=name,
                )
                raise RuntimeError(
                    f"필수 커넥터 연결 실패: {name}",
                )
        for name, ok in statuses.items():
            if not ok and name not in _REQUIRED_CONNECTORS:
                logger.warning(
                    "선택 커넥터 연결 실패 (degraded 모드)",
                    connector=name,
                )

        try:
            await store.connect()
        except Exception:
            if settings.session_backend.lower() == "redis":
                logger.warning(
                    "Redis 연결 실패 — Memory 세션 스토어로 전환 "
                    "(서버 재시작 시 대화 이력 소실)",
                )
                from src.services.session.store import (
                    _replace_store,
                )
                store = _replace_store()
            else:
                raise

        # Checkpointer 초기화 + 그래프 컴파일 (DI, async context manager)
        # create_checkpointer는 (checkpointer, pool) 튜플을 yield한다.
        # pool은 turn_text_store 등 커스텀 테이블 접근 시 재사용한다.
        async with create_checkpointer(
            settings.history_db,
        ) as (checkpointer, pool):
            if pool is not None:
                manager.set_checkpointer_pool(pool)
            get_compiled_app(checkpointer=checkpointer)

            # CancelStore 초기화 (SessionStore와 동일 백엔드)
            from src.agents.graph.cancel import set_cancel_store
            from src.services.cancel_store import (
                MemoryCancelStore,
                RedisCancelStore,
            )
            _redis = getattr(store, "_client", None)
            if settings.session_backend == "redis" and _redis:
                set_cancel_store(RedisCancelStore(_redis))
            else:
                set_cancel_store(MemoryCancelStore())

            logger.info("서버 시작 완료", connectors=statuses)
            yield

    finally:
        # 기동 실패/정상 종료 모두에서 리소스 정리 보장
        await store.disconnect()
        await manager.disconnect_all()
        logger.info("서버 종료")
        shutdown_logging()


app = FastAPI(
    title="Data Copilot",
    description="자연어 기반 데이터 추출/분석 AI 에이전트",
    version="0.1.0",
    lifespan=lifespan,
)

# 세션/턴 관리 라우터 등록 (/api/sessions, /api/turns)
app.include_router(sessions_router)


# ── 보안 헤더 미들웨어 ────────────────────────────────────


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """응답에 보안 헤더를 추가하는 미들웨어.

    /vendor 정적 파일 경로는 Cache-Control을 적용하지 않는다.
    정적 파일은 브라우저 캐싱이 성능에 중요하므로 no-store를 제외한다.
    WebSocket upgrade 요청도 통과하지만 HTTP 응답 헤더만 의미 있다.
    """

    async def dispatch(
        self, request: Request, call_next: Any,
    ) -> Any:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = (
            "strict-origin-when-cross-origin"
        )
        # 정적 파일은 브라우저 캐싱 허용
        if not request.url.path.startswith("/vendor"):
            response.headers["Cache-Control"] = "no-store"
        return response


app.add_middleware(SecurityHeadersMiddleware)


# ── CORS 미들웨어 ────────────────────────────────────────
# 프론트엔드 분리 배포 시 필수. 폐쇄망에서는 allow_origins를 특정 도메인으로 제한한다.
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],           # 운영 시 특정 도메인으로 제한 필요
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ── GZip 압축 미들웨어 ───────────────────────────────────
# 1KB 이상 응답을 자동 압축하여 네트워크 전송량을 절감한다.
from fastapi.middleware.gzip import GZipMiddleware

app.add_middleware(GZipMiddleware, minimum_size=1000)


# ── 글로벌 예외 핸들러 ────────────────────────────────────
# 기존 엔드포인트의 try/except에서 잡히지 않은 예외만 여기에 도달한다.
# WebSocket은 이 핸들러를 거치지 않으므로 기존 예외 처리와 충돌 없음.


@app.exception_handler(ValidationError)
async def validation_exception_handler(
    request: Request, exc: ValidationError,
) -> JSONResponse:
    """Pydantic 검증 실패 시 사용자 친화적 메시지를 반환한다."""
    logger.warning("요청 검증 실패", path=request.url.path, error=str(exc))
    return JSONResponse(
        status_code=422,
        content={"error": "입력값이 올바르지 않습니다. 확인 후 다시 시도해주세요."},
    )


@app.exception_handler(Exception)
async def global_exception_handler(
    request: Request, exc: Exception,
) -> JSONResponse:
    """미처리 예외에 대한 안전망.

    사용자에게 내부 정보를 노출하지 않는다 (code-style.md 규칙 준수).
    """
    logger.error("처리되지 않은 예외", path=request.url.path, error=str(exc))
    return JSONResponse(
        status_code=500,
        content={"error": "내부 오류가 발생했습니다. 잠시 후 다시 시도해주세요."},
    )


# ── 정적 파일 마운트 ──────────────────────────────────────

_vendor_dir = Path(__file__).parent.parent / "static" / "vendor"
if _vendor_dir.exists():
    app.mount(
        "/vendor",
        StaticFiles(directory=str(_vendor_dir)),
        name="vendor",
    )


# ── 엔드포인트 ────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def root():
    """챗봇 UI 페이지를 반환한다."""
    html_path = Path(__file__).parent.parent / "static" / "index.html"
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
    return HTMLResponse(content=_get_embedded_html())


@app.get("/health")
async def health_check():
    """서비스 상태를 확인한다 (커넥터 + LLM API)."""
    manager = get_connector_manager()
    statuses = await manager.health_check_all()

    if settings.llm_provider == "anthropic":
        llm_ok = bool(settings.anthropic_api_key)
    else:
        llm_ok = bool(settings.openai_api_key)
    statuses["llm_api"] = llm_ok

    all_ok = all(statuses.values())
    return {
        "status": "ok" if all_ok else "degraded",
        "connectors": statuses,
    }


@app.get("/health/live")
async def liveness():
    """프로세스 생존 확인 (Kubernetes liveness probe).

    프로세스가 살아있으면 항상 200을 반환한다.
    커넥터 상태와 무관하므로 hang 위험이 없다.
    """
    return {"status": "ok"}


@app.get("/health/ready")
async def readiness():
    """서비스 준비 상태 확인 (Kubernetes readiness probe).

    필수 커넥터만 검사하여 readiness를 판단한다.
    health_check_all()에 타임아웃이 적용되어 있으므로 hang하지 않는다.
    필수 커넥터 실패 시 503을 반환하여 로드밸런서가 트래픽을 차단하도록 한다.
    """
    manager = get_connector_manager()
    statuses = await manager.health_check_all()
    all_required_ok = all(
        statuses.get(k, False) for k in _REQUIRED_CONNECTORS
    )
    return JSONResponse(
        status_code=200 if all_required_ok else 503,
        content={
            "status": "ready" if all_required_ok else "not_ready",
            "connectors": statuses,
        },
    )


async def _handle_slash_command(
    command: str,
    session_id: str,
    websocket: WebSocket,
) -> bool:
    """슬래시 명령어를 처리한다. 처리했으면 True 반환."""
    store = get_session_store()

    if command == "/reset":
        await store.clear_session(session_id)
        await websocket.send_json({
            "type": "system",
            "message": "대화가 초기화되었습니다. 새로운 질문을 입력해주세요.",
        })
        return True

    if command == "/history":
        history = await store.get_history(session_id)
        if not history:
            msg = "(대화 이력이 없습니다)"
        else:
            lines = [
                f"{i}. [{'사용자' if h['role'] == 'user' else '시스템'}] "
                f"{truncate_log(h['content'])}"
                for i, h in enumerate(history, 1)
            ]
            msg = "\n".join(lines)
        await websocket.send_json({"type": "system", "message": msg})
        return True

    return False


async def _run_ws_pipeline(
    data: str,
    session_id: str,
    websocket: WebSocket,
    client_ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    """WebSocket 메시지에 대해 파이프라인을 실행하고 응답을 전송한다.

    on_event 콜백을 통해 파이프라인 실행 중 실시간 진행 상황을 전송하고,
    완료 후 시각화·통찰·다운로드 가능 알림을 분리 전송한다.
    """
    store = get_session_store()

    _ws_closed = False

    async def _safe_send(msg: dict[str, Any]) -> bool:
        """WebSocket 전송 — 끊겼으면 False 반환, 예외 없음."""
        nonlocal _ws_closed
        if _ws_closed:
            return False
        try:
            await websocket.send_text(
                json.dumps(msg, default=str, ensure_ascii=False),
            )
            return True
        except (WebSocketDisconnect, RuntimeError):
            _ws_closed = True
            logger.warning("WS 전송 실패 (연결 종료)", session_id=session_id)
            return False
        except Exception as e:
            _ws_closed = True
            logger.warning(
                "WS 전송 실패 (연결 오류)",
                error=type(e).__name__,
                detail=str(e)[:200],
                session_id=session_id,
            )
            return False

    async def on_event(msg: dict[str, Any]) -> None:
        """파이프라인 진행 이벤트를 WebSocket으로 전송한다."""
        await _safe_send(msg)

    try:
        conversation_history = await store.get_history(session_id)
        if not conversation_history:
            try:
                pool = get_connector_manager().checkpointer_pool
                if pool:
                    conversation_history = await get_conversation_history(
                        pool, session_id,
                    )
            except Exception:
                logger.warning("DB 대화이력 fallback 실패", exc_info=True)
        pipeline_result = await run_pipeline(
            data,
            session_id,
            conversation_history=conversation_history,
            client_ip=client_ip,
            user_agent=user_agent,
            on_event=on_event,
        )
    finally:
        await store.append_history(
            session_id,
            {
                "role": "user",
                "content": mask_pii(data),
                "type": HistoryEntryType.QUERY,
            },
        )

    masked_response = mask_pii(pipeline_result.response)

    # 대화 이력에 응답 기록 (명확화 질문 또는 최종 응답)
    await store.append_history(
        session_id,
        {
            "role": "assistant",
            "content": masked_response,
            "type": (
                HistoryEntryType.CLARIFICATION
                if pipeline_result.awaiting_clarification
                else HistoryEntryType.RESPONSE
            ),
        },
    )

    # WS가 이미 끊겼으면 이력 저장만 하고 전송 생략
    if _ws_closed:
        return

    # 시각화 분리 전송 (텍스트 응답보다 먼저)
    viz = pipeline_result.visualization
    if viz.has_visualization:
        viz_msg: dict[str, Any] = {
            "type": "viz",
            "title": viz.title,
            "code": viz.svg_code,
            "chart_type": viz.chart_type.value,
        }
        if not await _safe_send(viz_msg):
            return

    # 스트리밍 응답 전송 (start → chunk → end)
    # stream.start 전송 실패 시 stream.end도 불필요 — 프론트엔드가 스트림 시작을 모름
    if not await _safe_send({
        "type": "stream",
        "action": "start",
        "label": "답변 작성 중",
    }):
        return

    # stream.start 전송 후에는 stream.end를 반드시 전송하여
    # 프론트엔드의 setBusy(false) 호출을 보장한다.
    try:
        await _safe_send({
            "type": "stream",
            "action": "chunk",
            "text": masked_response,
        })
    finally:
        # 통찰(insight) — runner에서 State 접근 시점에 구성됨
        # turn_id/user_turn_id는 UI가 좋아요·다운로드 기록 API 호출에 사용한다.
        end_msg: dict[str, Any] = {
            "type": "stream",
            "action": "end",
            "status": (
                "cancelled" if pipeline_result.cancelled
                else "success"
            ),
            "insight": pipeline_result.insight,
            "turn_id": pipeline_result.turn_id,
            "user_turn_id": pipeline_result.user_turn_id,
            "trace_files": pipeline_result.trace_files or [],
        }
        if pipeline_result.result_data:
            end_msg["result_data"] = pipeline_result.result_data
        if pipeline_result.process_summary:
            end_msg["process_summary"] = (
                pipeline_result.process_summary
            )
        sent = await _safe_send(end_msg)
        if sent:
            logger.info(
                "stream.end 전송 완료",
                session_id=session_id,
                status=end_msg["status"],
            )
        else:
            logger.warning(
                "stream.end 전송 실패",
                session_id=session_id,
            )

    # 다운로드 가능 알림 (SQL 결과가 있는 경우)
    result_stats = pipeline_result.insight.get(
        "result_stats", {},
    )
    row_count = result_stats.get("row_count", 0)
    if (
        not pipeline_result.awaiting_clarification
        and row_count > 0
    ):
        _cache_sql_result(
            session_id, pipeline_result.sql_result,
        )
        await _safe_send({
            "type": "download_ready",
            "session_id": session_id,
            "row_count": row_count,
            "formats": ["csv", "json"],
            "turn_id": pipeline_result.turn_id,
        })


@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    """WebSocket 기반 챗봇 통신.

    슬래시 명령어:
    - /reset: 대화 이력 초기화
    - /history: 현재 대화 이력 조회

    보안:
    - session_id는 영숫자·하이픈·밑줄만 허용 (경로 순회·주입 차단)
    - 수신 메시지에 프롬프트 인젝션 감지 적용
    - 응답 전송 전 PII 마스킹 적용
    - 내부 예외 메시지는 사용자에게 노출하지 않음
    """
    await websocket.accept()

    if not _is_valid_session_id(session_id):
        logger.warning(
            "유효하지 않은 session_id",
            session_id=session_id[:64],
        )
        await websocket.close(code=1008)
        return
    logger.info("WebSocket 연결", session_id=session_id)

    store = get_session_store()
    await store.ensure_session(session_id)

    try:
        while True:
            raw = await websocket.receive_text()

            # JSON 프로토콜 파싱 (하위호환: plain text fallback)
            user_text = raw
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    user_text = parsed.get("text", raw)
                    # Phase 2/3 확장 필드 (현재 미사용, 향후 활용)
                    # user_model = parsed.get("model")
                    # user_thinking = parsed.get("thinking_mode")
                    # is_regen = parsed.get("action") == "regen"
                    # original_turn_id = parsed.get("original_turn_id")
            except (json.JSONDecodeError, ValueError):
                pass  # plain text fallback

            # 슬래시 명령어 처리
            if await _handle_slash_command(
                user_text.strip(), session_id, websocket,
            ):
                continue

            # 프롬프트 인젝션 감지
            if detect_prompt_injection(user_text):
                await websocket.send_json({
                    "type": "error",
                    "message": "허용되지 않는 입력 패턴이 감지되었습니다.",
                })
                continue

            # 파이프라인 실행
            try:
                await _run_ws_pipeline(
                    user_text,
                    session_id,
                    websocket,
                    client_ip=(
                        websocket.client.host if websocket.client else None
                    ),
                    user_agent=websocket.headers.get("user-agent"),
                )
            except WebSocketDisconnect:
                raise
            except Exception as e:
                logger.error(
                    "파이프라인 오류",
                    error=str(e),
                    session_id=session_id,
                )
                try:
                    await websocket.send_json({
                        "type": "stream",
                        "action": "end",
                        "status": "error",
                    })
                    await websocket.send_json({
                        "type": "error",
                        "message": format_error(ERR_GENERIC),
                    })
                except (WebSocketDisconnect, RuntimeError):
                    break

    except (WebSocketDisconnect, RuntimeError):
        logger.info("WebSocket 연결 종료", session_id=session_id)


@app.post(
    "/api/query",
    responses={
        400: {"description": "유효하지 않은 세션 ID"},
        422: {"description": "허용되지 않는 입력 패턴"},
        500: {"description": "서버 내부 오류"},
    },
)
async def query_endpoint(http_request: Request, request: QueryRequest):
    """REST API 기반 쿼리 엔드포인트.

    session_id를 전달하면 대화 이력·명확화 상태가 세션 스토어에서
    관리되어 멀티턴 대화가 가능하다. 미전달 시 1회성 대화.
    """
    user_input = request.query
    session_id = request.session_id or str(uuid.uuid4())

    if request.session_id and not _is_valid_session_id(
        request.session_id,
    ):
        raise HTTPException(
            status_code=400,
            detail="유효하지 않은 세션 ID입니다.",
        )

    if detect_prompt_injection(user_input):
        raise HTTPException(
            status_code=422,
            detail="허용되지 않는 입력 패턴이 감지되었습니다.",
        )

    store = get_session_store()
    await store.ensure_session(session_id)

    # /reset 명령 (REST에서도 세션 초기화 가능)
    if user_input.strip() == "/reset":
        await store.clear_session(session_id)
        return {"session_id": session_id, "response": "대화가 초기화되었습니다."}

    try:
        try:
            conversation_history = await store.get_history(session_id)
            if not conversation_history:
                try:
                    pool = get_connector_manager().checkpointer_pool
                    if pool:
                        conversation_history = await get_conversation_history(
                            pool, session_id,
                        )
                except Exception:
                    logger.warning("DB 대화이력 fallback 실패", exc_info=True)
            pipeline_result = await run_pipeline(
                user_input,
                session_id,
                conversation_history=conversation_history,
                client_ip=(
                    http_request.client.host
                    if http_request.client
                    else None
                ),
                user_agent=http_request.headers.get("user-agent"),
            )
        finally:
            await store.append_history(
                session_id,
                {
                    "role": "user",
                    "content": mask_pii(user_input),
                    "type": HistoryEntryType.QUERY,
                },
            )

        masked_response = mask_pii(pipeline_result.response)

        # 대화 이력에 응답 기록
        await store.append_history(
            session_id,
            {
                "role": "assistant",
                "content": masked_response,
                "type": (
                    HistoryEntryType.CLARIFICATION
                    if pipeline_result.awaiting_clarification
                    else HistoryEntryType.RESPONSE
                ),
            },
        )

        result_body: dict[str, Any] = {
            "session_id": session_id,
            "response": masked_response,
        }
        if pipeline_result.result_data:
            result_body["result_data"] = (
                pipeline_result.result_data
            )
        if pipeline_result.process_summary:
            result_body["process_summary"] = (
                pipeline_result.process_summary
            )
        if request.include_insight and pipeline_result.insight:
            result_body["insight"] = pipeline_result.insight
        if pipeline_result.visualization.has_visualization:
            result_body["visualization"] = {
                "type": "svg",
                "code": pipeline_result.visualization.svg_code,
                "chart_type": pipeline_result.visualization.chart_type.value,
                "title": pipeline_result.visualization.title,
            }
        if request.include_trace:
            result_body["trace"] = [
                {
                    "node": e.node,
                    "action": e.action,
                    "detail": e.detail,
                    "timestamp": e.timestamp,
                }
                for e in pipeline_result.trace_log
            ]
        return result_body
    except Exception as e:
        logger.error("API 오류", error=str(e))
        raise HTTPException(
            status_code=500,
            detail=ERR_GENERIC,
        ) from e


# 세션별 최근 SQL 결과 캐시 (다운로드용, 메모리 관리)
_sql_result_cache: dict[str, dict[str, Any]] = {}
_MAX_CACHE = 100


def _cache_sql_result(
    session_id: str,
    sql_result: Any,
) -> None:
    """다운로드를 위해 세션의 SQL 결과를 캐시한다."""
    if sql_result is None:
        return
    if len(_sql_result_cache) >= _MAX_CACHE:
        # 가장 오래된 항목 제거
        oldest = next(iter(_sql_result_cache))
        del _sql_result_cache[oldest]
    _sql_result_cache[session_id] = {
        "columns": (
            sql_result.columns
            if hasattr(sql_result, "columns")
            else []
        ),
        "rows": (
            sql_result.rows
            if hasattr(sql_result, "rows")
            else []
        ),
    }


class DownloadRequest(BaseModel):
    """데이터 다운로드 요청 모델."""

    session_id: str = Field(
        ..., description="세션 ID",
    )
    format: Literal["csv", "json"] = Field(
        default="csv",
        description="다운로드 포맷",
    )


@app.post(
    "/api/download",
    responses={
        404: {"description": "다운로드할 데이터가 없음"},
    },
)
async def download_data(request: DownloadRequest):
    """최근 조회 결과를 파일로 다운로드한다."""
    cached = _sql_result_cache.get(request.session_id)
    if not cached or not cached["rows"]:
        raise HTTPException(
            status_code=404,
            detail="다운로드할 데이터가 없습니다.",
        )

    columns = cached["columns"]
    rows = cached["rows"]

    if request.format == "json":
        return {
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
        }

    # CSV 생성
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=columns,
        extrasaction="ignore",
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(row)

    content = buf.getvalue().encode("utf-8-sig")
    return StreamingResponse(
        io.BytesIO(content),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                "attachment; "
                "filename=data-copilot-export.csv"
            ),
        },
    )


def _get_embedded_html() -> str:
    """내장 HTML 챗봇 UI (static/index.html 이 없을 때 폴백)."""
    fallback_path = (
        Path(__file__).parent.parent / "static" / "embedded.html"
    )
    if fallback_path.exists():
        return fallback_path.read_text(encoding="utf-8")
    return (
        "<html><body><h1>Data Copilot</h1>"
        "<p>static/embedded.html 파일을 찾을 수 없습니다.</p>"
        "</body></html>"
    )
