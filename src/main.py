"""FastAPI 서버 — Data Copilot 프로세스 진입점.

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

    # 개발 (hot-reload)
    uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload

    # 운영
    uvicorn src.main:app --host 0.0.0.0 --port 8000 --workers 4
"""

from __future__ import annotations

import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from src.agents.models.user_messages import ERR_GENERIC, format_error
from src.config import settings
from src.connectors.manager import get_connector_manager
from src.agents.graph.runner import run_pipeline
from src.services.session import get_session_store
from src.tools.langsmith import setup_langsmith
from src.utils.logger import get_logger, setup_logging
from src.utils.security import detect_prompt_injection, mask_pii

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """서버 시작/종료 시 리소스 관리."""
    setup_logging()
    setup_langsmith()
    manager = get_connector_manager()
    await manager.connect_all()
    store = get_session_store()
    await store.connect()
    logger.info("서버 시작 완료")
    yield
    await store.disconnect()
    await manager.disconnect_all()
    logger.info("서버 종료")


app = FastAPI(
    title="Data Copilot",
    description="자연어 기반 데이터 추출/분석 AI 에이전트",
    version="0.1.0",
    lifespan=lifespan,
)


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

    # LLM API 연결 확인 (프로바이더에 따라 API 키 확인)
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
                f"{h['content'][:100]}"
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
) -> None:
    """WebSocket 메시지에 대해 파이프라인을 실행하고 응답을 전송한다."""
    store = get_session_store()

    await store.append_history(
        session_id,
        {"role": "user", "content": mask_pii(data)},
    )

    await websocket.send_json({
        "type": "status",
        "message": "요청을 처리하고 있습니다...",
    })

    pipeline_result = await run_pipeline(
        data,
        session_id,
        conversation_history=await store.get_history(session_id),
        clarification_state=await store.get_clarification(session_id),
    )

    if pipeline_result.awaiting_clarification:
        await store.set_clarification(
            session_id,
            {
                "awaiting": True,
                "question": pipeline_result.clarification_question,
                "preprocessed_input": pipeline_result.preprocessed_input,
                "turns": pipeline_result.clarification_turns,
            },
        )

    masked_response = mask_pii(pipeline_result.response)

    await store.append_history(
        session_id,
        {"role": "assistant", "content": masked_response},
    )

    response_payload: dict[str, Any] = {
        "type": "response",
        "message": masked_response,
    }
    if pipeline_result.visualization.has_visualization:
        response_payload["visualization"] = {
            "type": "svg",
            "code": pipeline_result.visualization.svg_code,
            "chart_type": pipeline_result.visualization.chart_type.value,
            "title": pipeline_result.visualization.title,
        }

    await websocket.send_json(response_payload)


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
    if not _is_valid_session_id(session_id):
        logger.warning(
            "유효하지 않은 session_id",
            session_id=session_id[:64],
        )
        await websocket.close(code=1008)
        return

    await websocket.accept()
    logger.info("WebSocket 연결", session_id=session_id)

    store = get_session_store()
    await store.ensure_session(session_id)

    try:
        while True:
            data = await websocket.receive_text()

            # 슬래시 명령어 처리
            if await _handle_slash_command(
                data.strip(), session_id, websocket,
            ):
                continue

            # 프롬프트 인젝션 감지
            if detect_prompt_injection(data):
                await websocket.send_json({
                    "type": "error",
                    "message": "허용되지 않는 입력 패턴이 감지되었습니다.",
                })
                continue

            # 파이프라인 실행
            try:
                await _run_ws_pipeline(data, session_id, websocket)
            except Exception as e:
                logger.error(
                    "파이프라인 오류",
                    error=str(e),
                    session_id=session_id,
                )
                await websocket.send_json({
                    "type": "error",
                    "message": format_error(ERR_GENERIC),
                })

    except WebSocketDisconnect:
        logger.info("WebSocket 연결 종료", session_id=session_id)


@app.post(
    "/api/query",
    responses={
        400: {"description": "유효하지 않은 세션 ID"},
        422: {"description": "허용되지 않는 입력 패턴"},
        500: {"description": "서버 내부 오류"},
    },
)
async def query_endpoint(request: QueryRequest):
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

    # 대화 이력 추가 + 파이프라인 실행
    await store.append_history(
        session_id,
        {"role": "user", "content": mask_pii(user_input)},
    )

    try:
        pipeline_result = await run_pipeline(
            user_input,
            session_id,
            conversation_history=await store.get_history(session_id),
            clarification_state=await store.get_clarification(session_id),
        )

        if pipeline_result.awaiting_clarification:
            await store.set_clarification(
                session_id,
                {
                    "awaiting": True,
                    "question": pipeline_result.clarification_question,
                    "preprocessed_input": pipeline_result.preprocessed_input,
                    "turns": pipeline_result.clarification_turns,
                },
            )

        masked_response = mask_pii(pipeline_result.response)

        await store.append_history(
            session_id,
            {"role": "assistant", "content": masked_response},
        )

        result_body: dict[str, Any] = {
            "session_id": session_id,
            "response": masked_response,
        }
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
