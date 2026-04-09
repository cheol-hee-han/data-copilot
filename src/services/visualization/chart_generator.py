"""템플릿 기반 SVG 차트 생성기.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

LLM이 직접 SVG를 생성하지 못하는 환경(소형 로컬 LLM, 폐쇄망 배포 등)에서
데이터와 차트 유형만으로 SVG를 서버사이드에서 생성하는 폴백 모듈이다.
data_analyzer의 시각화 파이프라인에서 LLM SVG 생성 실패 시 호출된다.
막대 차트(BAR_CHART, STACKED_BAR), 꺾은선 차트(LINE_CHART),
원형 차트(PIE_CHART)의 세 가지 차트 유형을 지원하며,
SQLResult에서 첫 번째 문자열 컬럼을 레이블, 첫 번째 숫자 컬럼을 값으로 자동 감지한다.

resources/domain/chart_config.yaml 이 존재하면 폰트·색상 설정을
외부 파일에서 로드한다. 파일이 없으면 기본 설정(맑은 고딕, 7색 팔레트)을 사용한다.

핵심 함수:
    - generate_chart_from_result: SQLResult에서 컬럼 자동 감지 후 차트 유형에 맞는 SVG 생성
    - generate_bar_chart: 막대 차트 SVG 생성 (눈금선, 값 레이블 포함)
    - generate_line_chart: 꺾은선 차트 SVG 생성 (영역 채우기, 포인트 마커 포함)
    - generate_pie_chart: 원형 차트 SVG 생성 (12시 방향 시작, 오른쪽 범례)

성능 고려사항: 숫자 포맷팅 시 한국 금융 단위(만원/억원)를 자동 적용하여
사용자 친화적 레이블을 생성한다.
"""

from __future__ import annotations

import html
import math
from typing import Any

from src.config import settings
from src.models.enums import VisualizationType
from src.models.result import SQLResult

# ── 기본값 정의 ──

_DEFAULT_COLORS = [
    "#1a56db", "#3b82f6", "#60a5fa", "#93c5fd",
    "#f59e0b", "#10b981", "#ef4444",
]

_DEFAULT_FONT = "'Malgun Gothic','맑은 고딕',sans-serif"


# ── resources/domain/chart_config.yaml 로드 ──

def _load_chart_config() -> tuple[list[str], str]:
    """차트 설정을 로드한다. (colors, font_family)"""
    from src.utils.resource_loader import load_yaml

    data = load_yaml("domain/chart_config.yaml", None)
    if data is None:
        return _DEFAULT_COLORS, _DEFAULT_FONT

    # 폰트
    fonts = data.get("fonts", {})
    if fonts:
        parts = [
            fonts.get("primary", "Malgun Gothic"),
            fonts.get("fallback", "맑은 고딕"),
            fonts.get("system", "sans-serif"),
        ]
        font = ",".join(f"'{p}'" for p in parts)
    else:
        font = _DEFAULT_FONT

    # 색상
    colors_cfg = data.get("colors", {})
    palette = colors_cfg.get("palette", _DEFAULT_COLORS)

    return palette, font


_COLORS, _FONT = _load_chart_config()


def _esc(text: Any) -> str:
    """SVG 내 텍스트 이스케이프."""
    return html.escape(str(text))


def _format_number(value: float | int) -> str:
    """숫자를 한국 금융 단위(만원/억원)로 포맷팅한다."""
    # 한국 금융 고정 단위 기준값 (settings에서 로드)
    eok = settings.krw_eok_threshold
    man = settings.krw_man_threshold
    if isinstance(value, float):
        if abs(value) >= eok:
            return f"{value / eok:,.1f}억"
        if abs(value) >= man:
            return f"{value / man:,.0f}만"
        return f"{value:,.1f}"
    if abs(value) >= eok:
        return f"{value / eok:,.1f}억"
    if abs(value) >= man:
        return f"{value / man:,.0f}만"
    return f"{value:,}"


def generate_bar_chart(
    labels: list[str],
    values: list[float],
    title: str = "",
) -> str:
    """막대 차트 SVG를 생성한다."""
    if not labels or not values:
        return ""

    n = len(labels)
    max_val = max(values) if values else 1
    if max_val == 0:
        max_val = 1

    # 3순위: 레이아웃 상수 — 프론트엔드에서 조절하는 게 맞아 변경 빈도 낮음
    w, h = settings.chart_width, settings.chart_height
    margin_left = settings.chart_margin_left
    margin_right = settings.chart_margin_right
    margin_top = settings.chart_margin_top
    margin_bottom = settings.chart_margin_bottom
    chart_w = w - margin_left - margin_right
    chart_h = h - margin_top - margin_bottom
    bar_gap = 20
    bar_w = max(20, min(80, (chart_w - bar_gap * (n + 1)) / n))
    total_bars_w = n * bar_w + (n + 1) * bar_gap
    start_x = margin_left + (chart_w - total_bars_w) / 2 + bar_gap

    parts = [
        f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg">',
    ]

    # 제목
    if title:
        parts.append(
            f'  <text x="{w / 2}" y="30" text-anchor="middle" font-size="16" '
            f'font-weight="bold" font-family="{_FONT}" fill="#1f2937">'
            f'{_esc(title)}</text>'
        )

    # 축
    parts.append(
        f'  <line x1="{margin_left}" y1="{margin_top}" '
        f'x2="{margin_left}" y2="{h - margin_bottom}" '
        f'stroke="#e5e7eb" stroke-width="1"/>'
    )
    parts.append(
        f'  <line x1="{margin_left}" y1="{h - margin_bottom}" '
        f'x2="{w - margin_right}" y2="{h - margin_bottom}" '
        f'stroke="#e5e7eb" stroke-width="1"/>'
    )

    # 가로 눈금선 (4개)
    for i in range(1, 5):
        y = h - margin_bottom - (chart_h * i / 4)
        val = max_val * i / 4
        parts.append(
            f'  <line x1="{margin_left}" y1="{y}" '
            f'x2="{w - margin_right}" y2="{y}" '
            f'stroke="#f3f4f6" stroke-width="1" stroke-dasharray="4,4"/>'
        )
        parts.append(
            f'  <text x="{margin_left - 8}" y="{y + 4}" text-anchor="end" '
            f'font-size="10" font-family="{_FONT}" fill="#9ca3af">'
            f'{_format_number(val)}</text>'
        )

    # 막대
    for i, (label, val) in enumerate(zip(labels, values)):
        x = start_x + i * (bar_w + bar_gap)
        bar_h = (val / max_val) * chart_h if max_val else 0
        y = h - margin_bottom - bar_h
        color = _COLORS[i % len(_COLORS)]

        parts.append(
            f'  <rect x="{x}" y="{y}" width="{bar_w}" '
            f'height="{bar_h}" fill="{color}" rx="4"/>'
        )
        # 값 레이블
        parts.append(
            f'  <text x="{x + bar_w / 2}" y="{y - 8}" text-anchor="middle" '
            f'font-size="11" font-family="{_FONT}" fill="#1f2937">'
            f'{_format_number(val)}</text>'
        )
        # 카테고리 레이블
        parts.append(
            f'  <text x="{x + bar_w / 2}" y="{h - margin_bottom + 20}" '
            f'text-anchor="middle" font-size="11" font-family="{_FONT}" '
            f'fill="#6b7280">{_esc(label)}</text>'
        )

    parts.append("</svg>")
    return "\n".join(parts)


def generate_line_chart(
    labels: list[str],
    values: list[float],
    title: str = "",
) -> str:
    """꺾은선 차트 SVG를 생성한다."""
    if not labels or not values or len(labels) < 2:
        return ""

    n = len(labels)
    max_val = max(values) if values else 1
    min_val = min(values) if values else 0
    if max_val == min_val:
        max_val = min_val + 1

    # 3순위: 레이아웃 상수 — 프론트엔드에서 조절하는 게 맞아 변경 빈도 낮음
    w, h = settings.chart_width, settings.chart_height
    margin_left = settings.chart_margin_left
    margin_right = settings.chart_margin_right
    margin_top = settings.chart_margin_top
    margin_bottom = settings.chart_margin_bottom
    chart_w = w - margin_left - margin_right
    chart_h = h - margin_top - margin_bottom

    parts = [
        f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg">',
    ]

    if title:
        parts.append(
            f'  <text x="{w / 2}" y="30" text-anchor="middle" font-size="16" '
            f'font-weight="bold" font-family="{_FONT}" fill="#1f2937">'
            f'{_esc(title)}</text>'
        )

    # 축
    parts.append(
        f'  <line x1="{margin_left}" y1="{margin_top}" '
        f'x2="{margin_left}" y2="{h - margin_bottom}" '
        f'stroke="#e5e7eb" stroke-width="1"/>'
    )
    parts.append(
        f'  <line x1="{margin_left}" y1="{h - margin_bottom}" '
        f'x2="{w - margin_right}" y2="{h - margin_bottom}" '
        f'stroke="#e5e7eb" stroke-width="1"/>'
    )

    # 데이터 포인트 좌표 계산
    points: list[tuple[float, float]] = []
    step_x = chart_w / (n - 1) if n > 1 else 0
    for i, val in enumerate(values):
        x = margin_left + i * step_x
        y = h - margin_bottom - ((val - min_val) / (max_val - min_val)) * chart_h
        points.append((x, y))

    # 영역 채우기
    area_points = " ".join(f"{x},{y}" for x, y in points)
    area_bottom = f"{points[-1][0]},{h - margin_bottom} {points[0][0]},{h - margin_bottom}"
    parts.append(
        f'  <polygon points="{area_points} {area_bottom}" '
        f'fill="#1a56db" fill-opacity="0.1"/>'
    )

    # 선
    line_points = " ".join(f"{x},{y}" for x, y in points)
    parts.append(
        f'  <polyline points="{line_points}" fill="none" '
        f'stroke="#1a56db" stroke-width="2.5" stroke-linejoin="round"/>'
    )

    # 포인트 + 값 + x축 레이블
    for i, ((x, y), label, val) in enumerate(zip(points, labels, values)):
        parts.append(
            f'  <circle cx="{x}" cy="{y}" r="4" fill="white" '
            f'stroke="#1a56db" stroke-width="2"/>'
        )
        parts.append(
            f'  <text x="{x}" y="{y - 12}" text-anchor="middle" '
            f'font-size="10" font-family="{_FONT}" fill="#1f2937">'
            f'{_format_number(val)}</text>'
        )
        parts.append(
            f'  <text x="{x}" y="{h - margin_bottom + 20}" '
            f'text-anchor="middle" font-size="10" font-family="{_FONT}" '
            f'fill="#6b7280">{_esc(label)}</text>'
        )

    parts.append("</svg>")
    return "\n".join(parts)


def generate_pie_chart(
    labels: list[str],
    values: list[float],
    title: str = "",
) -> str:
    """원형 차트 SVG를 생성한다."""
    if not labels or not values:
        return ""

    total = sum(values)
    if total == 0:
        return ""

    # 3순위: 레이아웃 상수 — 프론트엔드에서 조절하는 게 맞아 변경 빈도 낮음
    w, h = settings.chart_width, settings.chart_height
    cx, cy, r = 260, 210, 140

    parts = [
        f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg">',
    ]

    if title:
        parts.append(
            f'  <text x="{w / 2}" y="30" text-anchor="middle" font-size="16" '
            f'font-weight="bold" font-family="{_FONT}" fill="#1f2937">'
            f'{_esc(title)}</text>'
        )

    # 파이 슬라이스
    start_angle = -math.pi / 2  # 12시 방향 시작
    for i, (label, val) in enumerate(zip(labels, values)):
        ratio = val / total
        sweep = ratio * 2 * math.pi
        end_angle = start_angle + sweep

        x1 = cx + r * math.cos(start_angle)
        y1 = cy + r * math.sin(start_angle)
        x2 = cx + r * math.cos(end_angle)
        y2 = cy + r * math.sin(end_angle)

        large_arc = 1 if sweep > math.pi else 0
        color = _COLORS[i % len(_COLORS)]

        d = (
            f"M {cx},{cy} L {x1},{y1} "
            f"A {r},{r} 0 {large_arc},1 {x2},{y2} Z"
        )
        parts.append(f'  <path d="{d}" fill="{color}"/>')

        # 레이블 위치 (슬라이스 중앙)
        mid_angle = start_angle + sweep / 2
        label_r = r * 0.65
        lx = cx + label_r * math.cos(mid_angle)
        ly = cy + label_r * math.sin(mid_angle)

        pct = f"{ratio * 100:.1f}%"
        parts.append(
            f'  <text x="{lx}" y="{ly}" text-anchor="middle" '
            f'font-size="11" font-family="{_FONT}" fill="white" '
            f'font-weight="bold">{pct}</text>'
        )

        start_angle = end_angle

    # 범례 (오른쪽)
    legend_x = 440
    for i, (label, val) in enumerate(zip(labels, values)):
        ly = 80 + i * 28
        color = _COLORS[i % len(_COLORS)]
        parts.append(
            f'  <rect x="{legend_x}" y="{ly - 10}" width="14" height="14" '
            f'fill="{color}" rx="2"/>'
        )
        parts.append(
            f'  <text x="{legend_x + 22}" y="{ly + 2}" font-size="12" '
            f'font-family="{_FONT}" fill="#374151">'
            f'{_esc(label)} ({_format_number(val)})</text>'
        )

    parts.append("</svg>")
    return "\n".join(parts)


def generate_chart_from_result(
    result: SQLResult,
    chart_type: VisualizationType,
    title: str = "",
) -> str:
    """SQLResult에서 자동으로 레이블/값을 추출하여 차트를 생성한다.

    첫 번째 문자열 컬럼을 레이블, 첫 번째 숫자 컬럼을 값으로 사용한다.
    """
    if chart_type == VisualizationType.NONE or not result.rows:
        return ""

    # 레이블 컬럼 (첫 번째 문자열)과 값 컬럼 (첫 번째 숫자) 자동 감지
    label_col: str | None = None
    value_col: str | None = None

    for col in result.columns:
        sample = result.rows[0].get(col)
        if label_col is None and isinstance(sample, str):
            label_col = col
        if value_col is None and isinstance(sample, (int, float)):
            value_col = col

    if value_col is None:
        return ""

    labels = [str(row.get(label_col, f"항목{i+1}")) for i, row in enumerate(result.rows)]
    values = [float(row.get(value_col, 0)) for row in result.rows]

    generators = {
        VisualizationType.BAR_CHART: generate_bar_chart,
        VisualizationType.STACKED_BAR: generate_bar_chart,
        VisualizationType.LINE_CHART: generate_line_chart,
        VisualizationType.PIE_CHART: generate_pie_chart,
    }

    gen = generators.get(chart_type)
    if gen is None:
        return ""

    return gen(labels, values, title)
