# HTML 시각화 생성 설계

> 상태: TODO
> 작성: 2026-03-30

## 배경

현재 시각화 파이프라인은 SVG만 지원한다. LLM이 `<svg>` 코드를 직접 생성하고,
실패 시 `chart_generator.py` 템플릿 폴백을 사용한다.
그러나 SVG로는 인터랙티브 테이블, 트리맵, 히트맵, 복잡한 대시보드 등을 표현하기 어렵다.
HTML 시각화를 SVG와 동일한 LLM 생성 방식으로 추가한다.

## 현재 구조 (SVG only)

```
analyzer_node
  └→ data_analyzer.build_visualization()
       ├→ judge_visualization()        → (chart_type, chart_title)
       ├→ generate_svg_via_llm()       → svg_code (LLM 직접 생성)
       └→ generate_chart_from_result() → svg_code (템플릿 폴백)
       └→ VisualizationData(svg_code=..., chart_type=..., title=...)
```

**서버 전송** (`main.py`):
```python
{"type": "viz", "title": ..., "code": svg_code, "chart_type": ...}
```

**프론트 수신** (`embedded.html`):
```
handleViz(data) → msg.visualization = {title, code, html}
renderViz()     → isSvg=!!viz.code, isHtml=!!viz.html
```

> 프론트엔드는 이미 `data.html` 수신 → `isHtml` 분기 → `sanitizeHTML` 렌더링 경로가 구현되어 있다.
> html2canvas 라이브러리도 `static/vendor/html2canvas.min.js`에 번들링 완료.
> 우측 상단 이미지 복사 버튼도 HTML viz용으로 구현 완료.

## 목표 구조

```
analyzer_node
  └→ data_analyzer.build_visualization()
       ├→ judge_visualization()        → (viz_type, title)
       │    viz_type ∈ {BAR_CHART, LINE_CHART, PIE_CHART,   ← 기존 SVG
       │                RICH_TABLE, HEATMAP, TREEMAP, ...}   ← 신규 HTML
       │
       ├─ [SVG 계열] ──→ generate_svg_via_llm() → svg_code
       │                  └→ 폴백: chart_generator
       │
       └─ [HTML 계열] ─→ generate_html_via_llm() → html_code  ← 신규
                         └→ 폴백: html_template_generator      ← 신규

       └→ VisualizationData(svg_code=..., html_code=..., ...)
```

## 변경 대상 및 작업 항목

### 1. 모델 확장

**`src/models/enums.py`** — VisualizationType에 HTML 유형 추가:
```python
class VisualizationType(str, Enum):
    # 기존 SVG
    BAR_CHART = "bar_chart"
    LINE_CHART = "line_chart"
    PIE_CHART = "pie_chart"
    STACKED_BAR = "stacked_bar"
    TABLE_ONLY = "table_only"
    # 신규 HTML
    RICH_TABLE = "rich_table"      # 정렬/하이라이트 등 리치 테이블
    HEATMAP = "heatmap"            # 색상 매트릭스
    SUMMARY_CARD = "summary_card"  # KPI 카드 레이아웃
```

**`src/models/result.py`** — VisualizationData에 html_code 필드 추가:
```python
class VisualizationData(BaseModel):
    svg_code: str = ""
    html_code: str = ""          # ← 신규
    chart_type: VisualizationType = VisualizationType.NONE
    title: str = ""

    @property
    def has_visualization(self) -> bool:
        return bool(self.svg_code or self.html_code)

    @property
    def is_html(self) -> bool:
        return bool(self.html_code) and not self.svg_code
```

### 2. 시각화 판단 분기 (`data_analyzer.py`)

`build_visualization()`에서 chart_type에 따라 SVG/HTML 분기:
```python
HTML_TYPES = {VisualizationType.RICH_TABLE, VisualizationType.HEATMAP, ...}

if chart_type in HTML_TYPES:
    html_code = await generate_html_via_llm(...)
    return VisualizationData(html_code=html_code, ...)
else:
    svg_code = await generate_svg_via_llm(...)
    return VisualizationData(svg_code=svg_code, ...)
```

### 3. HTML 생성 함수 (`data_analyzer.py` 또는 신규 모듈)

`generate_html_via_llm()` — SVG 생성과 동일한 패턴:
- 시스템 프롬프트: HTML/CSS 생성 규칙 (인라인 스타일만, JS 금지, XSS 방지)
- 유저 프롬프트: chart_type + title + data_summary
- 응답에서 `<div>...</div>` 또는 `<table>...</table>` 추출
- sanitizeHTML()로 정제 후 반환

### 4. HTML 템플릿 폴백 (선택)

`src/services/visualization/html_template_generator.py` — LLM 실패 시:
- RICH_TABLE: 정렬 가능한 HTML 테이블 생성 (CSS only, JS 없음)
- HEATMAP: 셀 배경색으로 값 크기 표현
- SUMMARY_CARD: 주요 지표를 카드형으로 배치

### 5. 서버 전송 (`main.py`)

```python
if viz.has_visualization:
    viz_msg = {"type": "viz", "title": viz.title, "chart_type": viz.chart_type.value}
    if viz.svg_code:
        viz_msg["code"] = viz.svg_code
    if viz.html_code:
        viz_msg["html"] = viz.html_code
    await websocket.send_json(viz_msg)
```

### 6. 프롬프트 파일 추가

```
resources/prompts/present/viz_html_system.txt    — HTML 생성 규칙
resources/prompts/present/viz_html_user.txt      — 생성 요청 템플릿
```

**HTML 생성 프롬프트 핵심 제약**:
- 인라인 CSS만 사용 (외부 스타일시트/CDN 금지 — 폐쇄망)
- `<script>` 금지 (XSS 방지, sanitizeHTML에서 제거됨)
- 최대 너비 100%, 반응형
- 폰트: `'Malgun Gothic','맑은 고딕',sans-serif` (차트와 통일)
- 색상 팔레트: chart_config.yaml의 설정 참조

### 7. 프론트엔드 — 이미 구현 완료

| 항목 | 상태 |
|------|------|
| `handleViz()` — `data.html` 수신 | ✅ 구현됨 |
| `renderViz()` — `isHtml` 분기 | ✅ 구현됨 |
| `sanitizeHTML()` — XSS 정제 | ✅ 구현됨 |
| `.content-copy` — 우측 상단 이미지 복사 | ✅ 구현됨 |
| `_htmlCapture()` — html2canvas 캡처 | ✅ 구현됨 |
| `html2canvas.min.js` — 로컬 번들 | ✅ 다운로드됨 |

## 구현 우선순위

1. **모델 확장** (enums.py, result.py) — 30분
2. **서버 전송 분기** (main.py) — 15분
3. **judge_visualization 프롬프트 수정** — HTML 유형 판단 추가
4. **generate_html_via_llm** 구현 + 프롬프트 작성
5. **HTML 템플릿 폴백** (폐쇄망 소형 모델 대비)
6. **테스트** — 기존 SVG 테스트 + HTML 유형 테스트 추가

## 고려사항

- **보안**: `sanitizeHTML()`이 `<script>`, `<iframe>`, `on*` 이벤트를 제거하므로
  LLM이 JS를 포함해도 안전. 단, `<style>` 태그도 제거되므로 인라인 스타일만 가능
- **폐쇄망**: 외부 폰트/CDN 불가. 인라인 스타일 + 시스템 폰트만 사용
- **소형 LLM 호환**: HTML이 SVG보다 생성 난이도가 낮아 소형 모델에서 더 안정적일 수 있음
- **TABLE_ONLY vs RICH_TABLE**: 기존 TABLE_ONLY는 "시각화 불필요, 텍스트 응답의 표"를 의미.
  RICH_TABLE은 "시각적으로 강화된 HTML 테이블"로 구분
