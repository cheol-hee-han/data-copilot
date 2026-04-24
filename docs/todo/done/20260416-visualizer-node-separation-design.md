# Visualizer 노드 분리 + info_card 시각화 설계

작성일: 2026-04-16
최종 수정: 2026-04-16


## 배경

### 문제 1: 시각화가 intent에 묶여 있다

현재 시각화 파이프라인은 `analyze_data` 노드 안에 포함되어 있고,
이 노드는 `DATA_ANALYSIS` intent일 때만 실행된다.

```
execute_sql
  ├─ DATA_ANALYSIS  → analyze_data(분석+시각화) → format_response
  └─ DATA_EXTRACTION → format_response                ← 시각화 기회 없음
```

따라서 "부서별 실적 뽑아줘"(EXTRACTION)처럼 차트에 적합한 결과가 나와도
시각화가 전혀 안 된다. 시각화 여부는 intent가 아니라 **결과 데이터의 특성**으로
판단해야 한다.

### 문제 2: 단건/소수 결과에 대한 시각화 부재

"총 여신 잔액", "강남지점 주소" 같은 행 1~2개 결과는
`min_rows_for_visualization=3` 게이트에 의해 시각화 자체가 스킵된다.
이런 결과는 카드 형태(info_card)로 표현하면 가독성이 크게 향상된다.


## 설계 원칙

1. **시각화는 intent와 독립** — SQL 결과가 있으면 항상 시각화 판단을 수행한다
2. **분석은 ANALYSIS일 때만** — LLM 기반 요약/인사이트 도출은 기존대로 DATA_ANALYSIS 전용
3. **기능별 노드 분리** — analyzer(분석), visualizer(시각화)를 독립 노드로 분리
4. **기존 서비스 함수 재사용** — `build_visualization`, `judge_visualization` 등은 변경 없이 재사용


## 변경 후 파이프라인 구조

```
execute_sql
  ├─ ERROR/CANCELLED → error_end
  ├─ DATA_ANALYSIS   → analyzer → visualizer → format_response → END
  └─ 그 외 (EXTRACTION 등) → visualizer → format_response → END
```

- `analyzer`: 분석만 (LLM 요약, 인사이트, 후속조치). state.analysis_result에 기록
- `visualizer`: 시각화만 (판단 + SVG 생성). state.visualization에 기록
- `format_response`: 기존과 동일. analysis_result, visualization 모두 참조하여 최종 응답 조립


## UI 출력 순서

### ANALYSIS (분석+시각화+테이블)
```
① 분석 내용 (핵심 요약, 인사이트, 후속 조치)
② 시각화 (차트/카드)
③ 테이블 (원본 데이터)
```

### EXTRACTION (시각화+테이블)
```
① 시각화 (차트/카드)
② 테이블 (원본 데이터)
```

### EXTRACTION (info_card일 때)
```
① 시각화 (info_card)
② 테이블 생략 — 행 1~2개에서 카드와 테이블이 동시 표시되면 동일 정보의 중복.
   info_card 판정 시 result_data 전송을 스킵하거나 프론트엔드에서 테이블을 숨긴다.
```

### EXTRACTION (테이블만, 시각화 none)
```
① 테이블
```

### 프론트엔드 DOM 순서 변경 필요

현행 `embedded.html`의 assistant 메시지 DOM 순서:
```
bot-bubble → result-data-slot(테이블) → process-summary-slot → viz-slot(시각화)
```

설계 의도("시각화 → 테이블")와 불일치하므로 DOM 순서를 변경:
```
bot-bubble → viz-slot(시각화) → result-data-slot(테이블) → process-summary-slot
```


## 변경 파일 상세

### A. 핵심 변경 — 노드 분리 + 파이프라인

| # | 파일 | 변경 |
|---|------|------|
| A1 | `src/models/enums.py` | `INFO_CARD = "info_card"` enum 추가 |
| A2 | `src/config.py` | `min_rows_for_visualization` 3→1 |
| A3 | `src/agents/nodes/present/analyzer.py` | 시각화 로직 제거, 노드 함수명 `analyzer_node`, 그래프 노드명 `analyzer` |
| A4 | `src/agents/nodes/present/visualizer.py` | **신규** — 시각화 전용 노드, `visualizer_node`, 그래프 노드명 `visualizer` |
| A5 | `src/agents/graph/pipeline.py` | 노드 등록 + `_route_after_execution` 라우팅 + 엣지 변경 |
| A6 | `src/services/data_analyzer.py` | `analyze_data` 함수에서 시각화 관련 파라미터/로직 분리. 내부 LLMNode 참조 3건 변경: `judge_visualization` 내 `LLMNode.ANALYZE_DATA_VIZ_JUDGMENT` → `VISUALIZER_JUDGMENT`, `_generate_svg_streaming` 내 `LLMNode.ANALYZE_DATA_SVG` → `VISUALIZER_SVG`, `analyze_data` 내 `LLMNode.ANALYZE_DATA` → `ANALYZER` |

### B. 프롬프트 파일 리네임 (resources/prompts/present/)

노드명 변경에 맞춰 `analyzer_viz_*` → `visualizer_*` 일괄 리네임.

| 현재 파일명 | 변경 후 |
|------------|---------|
| `analyzer_viz_judgment_system.txt` | `visualizer_judgment_system.txt` |
| `analyzer_viz_judgment_user.txt` | `visualizer_judgment_user.txt` |
| `analyzer_viz_svg_system_base.txt` | `visualizer_svg_system_base.txt` |
| `analyzer_viz_svg_user.txt` | `visualizer_svg_user.txt` |
| `analyzer_viz_svg_example_bar_chart.txt` | `visualizer_svg_example_bar_chart.txt` |
| `analyzer_viz_svg_example_line_chart.txt` | `visualizer_svg_example_line_chart.txt` |
| `analyzer_viz_svg_example_pie_chart.txt` | `visualizer_svg_example_pie_chart.txt` |
| `analyzer_viz_svg_example_donut_chart.txt` | `visualizer_svg_example_donut_chart.txt` |
| `analyzer_viz_svg_example_horizontal_bar.txt` | `visualizer_svg_example_horizontal_bar.txt` |
| `analyzer_viz_svg_example_flowchart.txt` | `visualizer_svg_example_flowchart.txt` |
| `analyzer_viz_svg_example_timeline.txt` | `visualizer_svg_example_timeline.txt` |
| `analyzer_viz_svg_example_mind_map.txt` | `visualizer_svg_example_mind_map.txt` |
| *(신규)* | `visualizer_svg_example_info_card.txt` |

백업 파일 삭제 대상:
- `analyzer_viz_svg_system.txt_org20260411`
- `analyzer_viz_judgment_system.txt_org20260411`

### C. 프롬프트 변수명 변경 (system_prompts.py)

| 현재 | 변경 후 |
|------|---------|
| `ANALYZER_VIZ_JUDGMENT_SYSTEM` | `VISUALIZER_JUDGMENT_SYSTEM` |
| `ANALYZER_VIZ_JUDGMENT_USER` | `VISUALIZER_JUDGMENT_USER` |
| `ANALYZER_VIZ_SVG_SYSTEM_BASE` | `VISUALIZER_SVG_SYSTEM_BASE` |
| `ANALYZER_VIZ_SVG_EXAMPLES` | `VISUALIZER_SVG_EXAMPLES` |
| `ANALYZER_VIZ_SVG_USER` | `VISUALIZER_SVG_USER` |

docstring 파일 매핑 주석도 동일하게 갱신.

### D. LLMNode enum 변경 (thinking_modes.py)

| 현재 | 변경 후 |
|------|---------|
| `ANALYZE_DATA = "analyze_data"` | `ANALYZER = "analyzer"` |
| `ANALYZE_DATA_VIZ_JUDGMENT = "analyze_data_viz_judgment"` | `VISUALIZER_JUDGMENT = "visualizer_judgment"` |
| `ANALYZE_DATA_SVG = "analyze_data_svg"` | `VISUALIZER_SVG = "visualizer_svg"` |

### E. 트래커/UI 라벨 문자열 변경

| # | 파일 | 변경 |
|---|------|------|
| E1 | `src/utils/tracker/callback_handler.py` | `"analyze_data"` → `"analyzer"` 키 변경, `"visualizer"` 추가 |
| E2 | `src/utils/tracker/visualizer.py` | phase 매핑(L63) + 표시명(L557) 변경/추가 |
| E3 | `src/services/insight_builder.py` | 조회과정 라벨(L337) 변경/추가 |
| E4 | `src/utils/llm/client.py` | 주석(L139) `analyze_data` → `analyzer/visualizer` 갱신 |

callback_handler.py 변경 상세:
```python
# 현재
"analyze_data": {"phase": "present", "label": "결과 분석·시각화", "thinking": "데이터 분석 중"}

# 변경 후
"analyzer":   {"phase": "present", "label": "결과 분석",  "thinking": "데이터 분석 중"}
"visualizer": {"phase": "present", "label": "시각화 생성", "thinking": "시각화 생성 중"}
```

### F. info_card 시각화 추가

| # | 파일 | 변경 |
|---|------|------|
| F1 | `visualizer_judgment_system.txt` | info_card 판단 규칙 K1~K3 추가, N1 수정, 유형명 목록에 추가, few-shot 예제 추가 |
| F2 | `visualizer_svg_system_base.txt` | SUPPORTED TYPES에 info_card 등록, STYLE DETAILS에 카드 스타일 추가 |
| F3 | `visualizer_svg_example_info_card.txt` | **신규** SVG 예제 3종 (단일 KPI, 2×2 그리드, 증감 비교) |
| F4 | `src/services/visualization/chart_generator.py` | info_card 템플릿 폴백 함수 추가 |

### G. State 변경

- `state.visualization`: 기존 analyzer가 write → visualizer가 write. 필드 자체 변경 불필요
- `state.analysis_result`: analyzer만 write. 변경 없음
- `turn_reset_updates()`: 이미 `visualization: VisualizationData()` 초기화. 변경 없음
- W/R 주석 갱신: `state.py` L729 약어 `ANL=analyze_data` → `ANLZ=analyzer, VIZ=visualizer`, L789~793 `W: ANL` → `W: ANLZ`(analysis_result), `W: VIZ`(visualization)

### H. 테스트 변경

| # | 파일 | 변경 |
|---|------|------|
| H1 | `tests/auto/unit/test_analyze_data.py` | 시각화 관련 테스트 분리/보정. `test_visualization_skipped_few_rows`는 visualizer 노드 기준으로 재작성 (min_rows=1이므로 "1행→스킵" 시나리오 폐기, "0행→스킵" 또는 "1행+K1→info_card" 시나리오로 교체) |
| H2 | `tests/test_cases/agentic_e2e_test_catalog.json` | `expected_path`에 `analyze_data` → `analyzer → visualizer` |
| H3 | `tests/auto/unit/test_pipeline_routing.py` | `_route_after_execution` 테스트 — `"analyze_data"` → `"analyzer"` + `"visualizer"` 라우팅 케이스 |
| H4 | `tests/auto/unit/test_callback_handler_llm_delta.py` | `"node": "analyze_data"` → `"analyzer"` 또는 `"visualizer"` |
| H5 | `tests/manual/e2e/test_full_pipeline_e2e.py` | `analyze_data` 노드 진입 확인 → `analyzer` + `visualizer` 경유 확인 |

### I. 프론트엔드 변경

| # | 파일 | 변경 |
|---|------|------|
| I1 | `static/embedded.html` | assistant 메시지 DOM 순서 변경: `viz-slot`을 `result-data-slot` 앞으로 이동 |
| I2 | `static/embedded.html` | info_card 테이블 중복 방지: `chart_type === "info_card"`일 때 `result-data-slot` 렌더링 스킵 |


## 노드 상세 설계

### analyzer_node (기존 analyze_data_node 수정)

```python
async def analyzer_node(state: PipelineState) -> dict:
    """데이터 분석 — LLM 기반 요약/인사이트 도출. DATA_ANALYSIS일 때만 진입."""
    # 1. analyze_data 서비스 호출 (시각화 파라미터 제거됨)
    #    → (AnalysisResult, streaming_delivered) 반환
    # 2. return {"analysis_result": analysis, "streaming_delivered": delivered}
```

제거 항목:
- ANALYZER_VIZ_* 프롬프트 import
- viz_* 파라미터 전달
- min_rows_for_visualization 참조
- visualization 반환

### visualizer_node (신규)

```python
async def visualizer_node(state: PipelineState) -> dict:
    """시각화 판단 + SVG 생성. intent 무관 항상 실행."""
    # 1. row_count < min_rows_for_visualization → 스킵
    # 2. build_visualization(state.sql_result, ...) 호출
    #    → judge_visualization → generate_svg_via_llm → 템플릿 폴백
    # 3. return {"visualization": viz}
    viz = await build_visualization(
        state.sql_result,
        viz_judgment_prompt=VISUALIZER_JUDGMENT_SYSTEM,
        viz_judgment_user=VISUALIZER_JUDGMENT_USER,
        viz_svg_base=VISUALIZER_SVG_SYSTEM_BASE,
        viz_svg_examples=VISUALIZER_SVG_EXAMPLES,
        viz_svg_user=VISUALIZER_SVG_USER,
        is_cancelled=_is_cancelled,
        streaming_enabled=state.streaming_enabled,
        turn_id=state.turn_id,
    )
```

프롬프트: VISUALIZER_JUDGMENT_*, VISUALIZER_SVG_* import

### data_analyzer.py analyze_data 함수 변경

시각화 관련 파라미터 제거:
- `viz_judgment_prompt`, `viz_judgment_user` 삭제
- `viz_svg_base`, `viz_svg_examples`, `viz_svg_user` 삭제
- `min_rows_for_viz` 삭제
- `build_visualization` 호출 삭제
- 반환 타입: `tuple[AnalysisResult, VisualizationData, bool]` → `tuple[AnalysisResult, bool]`

`build_visualization` 함수는 그대로 유지 — visualizer_node에서 직접 호출.

내부 LLMNode 참조 변경:
- `judge_visualization` 함수 내 `node_name=LLMNode.ANALYZE_DATA_VIZ_JUDGMENT` → `LLMNode.VISUALIZER_JUDGMENT`
- `_generate_svg_streaming` 함수 내 `node_name=LLMNode.ANALYZE_DATA_SVG` → `LLMNode.VISUALIZER_SVG`
- `analyze_data` 함수 내 `node_name=LLMNode.ANALYZE_DATA` → `LLMNode.ANALYZER`

### pipeline.py 변경

```python
# 노드 등록
workflow.add_node("analyzer", _cc(analyzer_node))
workflow.add_node("visualizer", _cc(visualizer_node))

# 라우팅
def _route_after_execution(state):
    if state.status in (QueryStatus.ERROR, QueryStatus.CANCELLED):
        return "error_end"
    if state.intent == IntentType.DATA_ANALYSIS:
        return "analyzer"
    return "visualizer"

# 엣지
workflow.add_conditional_edges("execute_sql", _route_after_execution, {
    "analyzer": "analyzer",
    "visualizer": "visualizer",
    "error_end": "error_end",
})
workflow.add_edge("analyzer", "visualizer")
workflow.add_edge("visualizer", "format_response")
workflow.add_edge("simple_responder", "format_response")
workflow.add_edge("format_response", END)
```


## info_card 판단 규칙

### 판단 구조 — STEP 명시

폐쇄망 소형 LLM(Solar Pro 2 70B, Qwen3.5 397B)이 우선순위를 정확히 준수하도록
STEP 기반 순차 판단 구조를 도입한다:

```
## 판단 순서 (반드시 이 순서대로 확인)

STEP 1: 정보 카드 판단 (K1~K3) — 하나라도 해당하면 즉시 info_card 반환. STEP 2로 넘어가지 않는다.
STEP 2: 시각화 불필요 판단 (N1~N8) — 하나라도 해당하면 none 반환.
STEP 3: 정량 차트 / 다이어그램 판단 (규칙 1~18) — 가장 먼저 매칭되는 유형 선택.
```

### K1~K3 규칙

```
## STEP 1: 판단 기준 — 정보 카드

K1. 행 1개 + 수치 컬럼 1개 이상 (단일 집계값, KPI, 총합, 다중 지표 등)
   → info_card

K2. 다음 조건을 모두 충족:
   - 행이 정확히 2개
   - 수치 컬럼이 1개 이상
   - 행 레이블(첫 번째 컬럼)이 시간 축이 아님
     (예: "이번달/전월", "목표/실적", "A지점/B지점" — 시계열이 아닌 비교/대조 구조)
   → info_card

K3. 행 1~2개 + 텍스트 컬럼만 (주소, 상태 등 단건 조회)
   → info_card
```

변경 사항:
- K1: 수치 컬럼 상한(4개)을 제거 → "1개 이상"으로 확장. 금융 KPI 6개(BIS/LCR/NIM/연체율/ROA/ROE)가 1행에 오는 케이스 커버
- K2: "비교/대조 구조" 조건을 체크리스트형으로 기계화 + "시간 축이 아님" 조건 추가. 시계열 2행(1월/2월)이 K2에 먹히는 충돌 방지
- K3: 변경 없음

### N1 수정

```
현재: N1. 행이 2개 미만 (단일 집계값, 합계 1행 등)
변경: N1. 행이 2개 이하이고 K1~K3에 해당하지 않는 경우
```

- "2개 미만"→"2개 이하"로 확장: 행 2개인데 K2 미매칭(시계열 2행 등)인 경우도 커버
- K1~K3이 STEP 1에서 먼저 평가되므로, 여기 도달한 1~2행은 info_card 부적합으로 판정된 케이스

### 기존 none 예제 17번 교체

현재 예제 17번이 K1과 직접 충돌함:
```
현재 예제 17: 컬럼=[총 고객수], 행(1): [(45,230)] → none
K1 적용 후: 행 1개 + 수치 1컬럼 → info_card
```

예제 17번을 K1→info_card 예제로 교체하고, none 예제는 K1~K3에 해당하지 않는 데이터로 변경:

```
--- 예제 (교체): 단일 집계값 → info_card ---

데이터:
컬럼: [총 고객수]
행(1): [(45230)]

판단 근거: STEP 1 → K1(행 1개, 수치 1컬럼) → info_card

출력:
CHART_TYPE: info_card
CHART_TITLE: 총 고객수
DATA: [{"총 고객수":45230}]
```

새 none 예제 (K1~K3 미매칭):
```
--- 예제: 행 1개 + 비구조화 텍스트 → none ---

데이터:
컬럼: [안내사항]
행(1): [("현재 시스템 점검 중입니다. 14시 이후 재접속 바랍니다.")]

판단 근거: 행 1개이지만 수치 없음, 자유 서술형 텍스트 → K1~K3 미해당 → STEP 2 N7 적용

출력:
CHART_TYPE: none
CHART_TITLE:
DATA:
```

### 추가 few-shot 예제

기본 3종 + 경계 명확화 2종 = 총 5종:

1. **K1 단일 집계값**: 총 고객수 1행 → info_card (위 교체 예제)
2. **K1 다중 지표**: BIS비율/LCR/NIM/연체율 4컬럼 1행 → info_card
3. **K2 비교/대조**: 이번달 신규계좌 320건 vs 전월 280건 → info_card
4. **K3 텍스트 단건**: 강남지점 주소 → info_card
5. **K2 반례 (시계열 2행)**: 1월/2월 매출 2행 → K2 미매칭(시간 축) → STEP 3 → line_chart

### viewBox 가이드

info_card는 기존 차트와 동일하게 `viewBox="0 0 800 500"` 을 사용하되,
콘텐츠를 캔버스 상단~중앙에 배치하고 하단 여백을 허용한다.
카드 유형별 viewBox 높이 차별화는 프롬프트 복잡도 대비 효용이 낮으므로
일단 통일하고, 추후 렌더링 결과를 보고 필요 시 조정한다.


## 데이터 흐름 검증

| 케이스 | intent | 경로 | 시각화 결과 | 테이블 표시 |
|--------|--------|------|-------------|-------------|
| "부서별 실적 분석해줘" | ANALYSIS | analyzer → visualizer | bar_chart | O |
| "부서별 실적 뽑아줘" | EXTRACTION | visualizer | bar_chart | O |
| "총 여신 잔액 알려줘" | EXTRACTION | visualizer → K1 | info_card | X (중복 방지) |
| "강남지점 주소" | EXTRACTION | visualizer → K3 | info_card | X (중복 방지) |
| "고객 명단 150건" | EXTRACTION | visualizer → N4/N6 | none | O |
| "서버 정상이야?" | EXTRACTION | visualizer → N2 | none | O |
| "월별 매출 추이 분석" | ANALYSIS | analyzer → visualizer | line_chart | O |
| "1월 vs 2월 매출" | EXTRACTION | visualizer → K2 미매칭(시간축) → line_chart | line_chart | O |
| "이번달 vs 전월 계좌 건수" | EXTRACTION | visualizer → K2 매칭 | info_card | X (중복 방지) |
| "BIS비율/LCR/NIM/연체율" | EXTRACTION | visualizer → K1(수치 4컬럼) | info_card | X (중복 방지) |


## 구현 순서

1. 프롬프트 파일 리네임 (B) — git mv
2. system_prompts.py 변수명 변경 (C)
3. thinking_modes.py LLMNode 변경 (D)
4. data_analyzer.py 시각화 분리 + 내부 LLMNode 참조 변경 (A6)
5. analyzer.py 수정 — 시각화 제거 (A3)
6. visualizer.py 신규 — 시각화 전용 노드 (A4)
7. pipeline.py 라우팅 변경 (A5)
8. 트래커/라벨 문자열 변경 (E1~E4)
9. state.py W/R 주석 갱신 (G)
10. info_card 프롬프트 + SVG 예제 추가 (F1~F3)
11. chart_generator.py 폴백 (F4)
12. 프론트엔드 DOM 순서 + info_card 테이블 중복 방지 (I1~I2)
13. 테스트 보정 (H1~H5)
