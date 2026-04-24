"""파이프라인 응답 모델 — 최종 실행 결과를 표현하는 데이터 클래스.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

파이프라인 실행이 완료된 뒤 호출자(server.py, graph/runner.py)에게 반환되는
결과 컨테이너를 정의한다.
PipelineResult 는 포맷팅된 응답 문자열, 추론 추적 로그, 시각화 데이터를 통합하며,
str() 변환 시 response 문자열을 반환하여 기존 문자열 기반 호출부와 하위 호환된다.

핵심 클래스:
    - PipelineResult: 최종 응답(response), 추적 로그(trace_log), 시각화(visualization) 통합
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from src.agents.state.state import TraceEntry
from src.models.result import SQLResult, VisualizationData


class PipelineResult(BaseModel):
    """파이프라인 실행 결과.

    response: 사용자에게 보여줄 최종 포맷팅된 응답
    trace_log: 추론 과정 추적 로그 (디버그/관리자용)
    visualization: 시각화 데이터 (SVG 코드, 차트 유형, 제목)
    awaiting_clarification: 명확화 응답 대기 중 여부
    clarification_request: interrupt 페이로드 (AmbiguitySignal 데이터)
    preprocessed_input: 전처리된 입력 (재진입 시 원본 질의 복원용)
    """

    response: str = ""
    trace_log: list[TraceEntry] = Field(default_factory=list)
    visualization: VisualizationData = Field(
        default_factory=VisualizationData,
    )
    insight: dict[str, Any] = Field(
        default_factory=dict,
        description="통찰 데이터 (분석 과정, 테이블 선택 근거 등)",
    )
    sql_result: SQLResult = Field(
        default_factory=SQLResult,
        description="SQL 실행 결과 (다운로드 캐싱용)",
    )
    trace_files: list[dict[str, str]] = Field(
        default_factory=list,
        description="생성된 trace 파일 목록 [{name, filename}, ...]",
    )
    cancelled: bool = False
    error: bool = False
    streaming_delivered: bool = False
    awaiting_clarification: bool = False
    clarification_request: dict[str, Any] | None = Field(
        default=None,
        description="interrupt 페이로드 (AmbiguitySignal 데이터)",
    )
    preprocessed_input: str = ""
    message_uuid: str | None = Field(
        default=None,
        description="assistant 메시지 message_uuid (WebSocket stream.end에 포함)",
    )
    user_message_uuid: str | None = Field(
        default=None,
        description="user 메시지 message_uuid (WebSocket stream.end에 포함)",
    )
    result_data: dict[str, Any] | None = Field(
        default=None,
        description=(
            "UI 테이블 렌더링용 SQL 결과 원본 "
            "(columns, rows, column_formats, total_count, displayed_count). "
            "sql_result(다운로드 캐싱용 전체 결과)와 별도로, "
            "코드값 변환·행 제한이 적용된 UI 전송 전용 데이터."
        ),
    )
    process_summary: dict[str, Any] | None = Field(
        default=None,
        description=(
            "5단계 조회 과정 요약 — 본문 하단 접기 렌더링용 "
            "(intent, interpretation, context, ai_decisions, validation)"
        ),
    )

    def __str__(self) -> str:
        return self.response
