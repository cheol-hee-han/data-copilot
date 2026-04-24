"""실행 결과 관련 공유 모델.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

SQL 실행 결과(SQLResult), 데이터 분석 결과(AnalysisResult),
시각화 데이터(VisualizationData)를 정의한다.

서비스·노드 양쪽에서 참조하므로 의존성 방향을 지키기 위해
agents/state/ 가 아닌 독립 모듈에 위치한다.

핵심 클래스:
    - SQLResult: SQL 실행 결과 (컬럼명, 행 데이터, 행 수, 실행 시간)
    - VisualizationData: 시각화 컨테이너 (SVG 코드, 차트 유형, 제목)
    - AnalysisResult: 데이터 분석 결과 (요약, 인사이트, 통계)
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, field_validator

from src.models.enums import VisualizationType


class SQLResult(BaseModel):
    """SQL 실행 결과."""

    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    execution_time_ms: float = 0.0

    @field_validator("rows", mode="before")
    @classmethod
    def ensure_json_serializable(
        cls, v: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """rows 내 Decimal 값을 int/float로 변환한다.

        커넥터에서 이미 sanitize_row를 적용하지만,
        테스트·캐시 복원 등 커넥터를 거치지 않는 경로를 방어한다.
        """
        if not v:
            return v
        # 첫 행만 검사하여 Decimal 미포함 시 전체 변환을 건너뛴다 (성능)
        first = v[0]
        if not any(isinstance(val, Decimal) for val in first.values()):
            return v
        return [
            {
                k: (
                    int(val)
                    if isinstance(val, Decimal)
                    and val == val.to_integral_value()
                    else float(val)
                    if isinstance(val, Decimal)
                    else val
                )
                for k, val in row.items()
            }
            for row in v
        ]


class VisualizationData(BaseModel):
    """시각화 데이터 컨테이너."""

    svg_code: str = ""
    chart_type: VisualizationType = VisualizationType.NONE
    title: str = ""
    judgment_reason: str = ""

    @property
    def has_visualization(self) -> bool:
        """시각화 데이터 존재 여부."""
        return bool(self.svg_code)


class AnalysisResult(BaseModel):
    """데이터 분석 결과."""

    summary: str = ""
    initial_reading: list[str] = Field(default_factory=list)
    insights: list[str] = Field(default_factory=list)
    statistics: dict[str, Any] = Field(default_factory=dict)
    action_items: list[str] = Field(default_factory=list)
    reasoning_summary: str = ""
