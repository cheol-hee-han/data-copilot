# 시각화 커버리지 확장 설계

작성자: 한철희 / 작성일: 2026-04-14 / 개정: 2026-04-14 (리뷰 반영)
상태: 설계 (구현 전 최종 검토)
관련 문서: `docs/todo/20260410-qwen35-prompting-skill-strategy.md`

## 1. 배경

현재 시각화 파이프라인은 3단계로 동작한다.

1. **판단(judgment)** — `analyzer_viz_judgment_system.txt` 이 SQL 결과를 보고 `CHART_TYPE` / `CHART_TITLE` / `DATA` 를 반환 (19 + 1 유형)
2. **LLM SVG 생성** — `analyzer_viz_svg_system_base.txt` + 차트별 예제로 순수 SVG 생성
3. **템플릿 폴백** — LLM 실패 시 `chart_generator` 파이썬 코드로 제한된 유형만 생성

## 2. 현재 문제 — 3계층 불일치

| 계층 | 위치 | 유형 수 | 유형 목록 |
|------|------|---------|-----------|
| A. enum | `src/models/enums.py:57-65` | 6 | `none, bar_chart, line_chart, pie_chart, stacked_bar, table_only` |
| B. 판단 프롬프트 | `analyzer_viz_judgment_system.txt:106-111` | 19 | 정량10 + 다이어그램8 + none |
| C. SVG 예제 dict | `system_prompts.py:157-170` | 8 | `bar, line, pie, horizontal_bar, flowchart, timeline, donut, mind_map` |
| D. SVG base 지원 선언 | `analyzer_viz_svg_system_base.txt:57-87` | 20+ | 정량7 + 비율3 + 비교/순위4 + 다이어그램7 |
| E. 템플릿 폴백 | `chart_generator.py:398-401` | 4 | `bar, stacked_bar, line, pie` |

### 실동작 교집합

- 판단이 enum 밖 값을 반환 → `VisualizationType(raw)` ValueError → `parse_viz_judgment` 재raise → `llm_call_with_parse_retry` 재시도 3회 **낭비** → 최종 `VisualizationType.NONE` 폴백.
- 결과적으로 **실제 LLM SVG 도달 유형은 `bar_chart`, `line_chart`, `pie_chart` 3종뿐**.
- dict 의 5개 예제 (`horizontal_bar`, `flowchart`, `timeline`, `donut_chart`, `mind_map`) 는 도달 불가 dead code.
- `stacked_bar` 는 enum 엔 있으나 예제 없음 → LLM SVG 건너뛰고 템플릿 폴백(유일하게 동작).

## 3. 은행 업무 시각화 수요 매트릭스

Data Copilot 이 받는 질문을 업무 영역별로 분류하고 각 경우에 적합한 시각화를 정리한다.

### 3.1 여신 (Loan)

| 질문 예 | 데이터 형상 | 적합 시각화 |
|--------|-----------|-----------|
| 월별 대출잔액 추이 보여줘 | 시계열 1계열 | line_chart |
| 상품별 대출 실행액 비교 | 카테고리 × 수치 | bar_chart |
| 지점별 연체율 순위 | 많은 카테고리 순위 | horizontal_bar |
| 월별 상품군 실행액 변화 | 시계열 × 다계열 누적 | stacked_bar |
| 분기별 채널(온·오프)별 실행 | 시계열 × 다계열 비교 | grouped_bar |
| 등급별 대출 잔액 구성 | 구성비 (≤6개) | pie_chart |
| 고객 소득 × 연체 여부 분포 | 연속형×연속형 | scatter_plot |
| 요일·시간대 승인 건수 | 카테고리²+수치 | heatmap |

### 3.2 수신 (Deposit)

| 질문 예 | 적합 시각화 |
|--------|-----------|
| 예금 잔액 추이 | line_chart |
| 상품별 예금 비중 | pie_chart / donut_chart |
| 만기 구간별 잔액 | horizontal_bar |
| 지점별 수신 실적 순위 | horizontal_bar |
| 연령대 × 상품유형 가입 분포 | heatmap |

### 3.3 카드

| 질문 예 | 적합 시각화 |
|--------|-----------|
| 업종별 결제금액 비중 | pie_chart |
| 시간대별 승인 건수 | bar_chart / line_chart |
| 요일·시간대 거래 밀도 | heatmap |
| 카드 등급별 연체율 | bar_chart |
| 기간별 신규 발급·해지 | grouped_bar |

### 3.4 지점·채널

| 질문 예 | 적합 시각화 |
|--------|-----------|
| 지점별 종합 실적 순위 (20개) | horizontal_bar |
| 채널별 거래건수 추이 | stacked_bar / grouped_bar |
| 지역별 거래 집중도 | heatmap |

### 3.5 고객

| 질문 예 | 적합 시각화 |
|--------|-----------|
| 연령대별 고객 분포 | bar_chart |
| 등급별 비중 | pie_chart / donut_chart |
| 소득·자산 분포 산점 | scatter_plot |
| 고객 세그먼트 × 상품보유율 | heatmap |
| 신규 가입 월별 추이 | line_chart |

### 3.6 재무·리스크

| 질문 예 | 적합 시각화 |
|--------|-----------|
| 손익 구조 분해 | waterfall_chart |
| BIS / LCR / NSFR 추이 | line_chart |
| 월별 수익원 구성 변화 | stacked_bar |
| 리스크 익스포저 × 신용등급 | heatmap |

### 3.7 개별 레코드 조회 (차트 불필요)

| 질문 예 | 처리 |
|--------|------|
| 특정 고객 최근 거래내역 20건 | table_only |
| 대출 계약 목록 | table_only |

## 4. 목표 범위

### 4.1 설계 원칙

1. **Data Copilot 은 데이터 분석 어시스턴트** — 업무매뉴얼 도식(flowchart/timeline/mind_map/org_chart/process_diagram/venn/matrix/value_chain)은 범위 외. 프롬프트와 예제 둘 다에서 제거.
2. **enum = 판단 프롬프트 = 예제 dict** 3계층을 단일 source of truth 로 정렬.
3. **템플릿 폴백은 최소 3종만 지원**(`bar_chart`, `line_chart`, `pie_chart`) — 나머지는 LLM SVG 실패 시 `VisualizationData()` 빈값 반환.
4. **예제는 은행 도메인 데이터로 작성** — 현재 "프로그래밍 언어 사용률" 같은 IT 예제는 금융 데이터로 대체.
5. **`build_visualization` 은 `SQLResult` 전용** — Qdrant 매뉴얼/상품설명서 응답(meta_question, general_question intent)은 이 경로를 타지 않음 (grep 교차확인: `VisualizationType` 은 analyzer 계열 6파일에만 존재). 다이어그램 제거가 매뉴얼 응답 품질에 영향 없음.

### 4.2 최종 VisualizationType (12종)

| Enum 값 | 한글 | 판단 규칙 | 예제 필요 | 템플릿 폴백 |
|--------|------|-----------|---------|---|
| `NONE` | 시각화 없음 | N1~N3, N6~N8 | — | — |
| `TABLE_ONLY` | 표 표시 | N4 명세성 데이터 | — | (UI 테이블) |
| `BAR_CHART` | 세로 막대 | 규칙4 | ○ | ○ |
| `LINE_CHART` | 꺾은선 | 규칙1 | ○ | ○ |
| `HORIZONTAL_BAR` | 가로 막대 | 규칙5 | ○ | — |
| `STACKED_BAR` | 누적 막대 | 규칙2 | ○ | — (제거) |
| `GROUPED_BAR` | 그룹 막대 | 규칙3 | ○ | — |
| `PIE_CHART` | 원형 | 규칙6 | ○ | ○ |
| `DONUT_CHART` | 도넛 | 규칙7 | ○ | — |
| `SCATTER_PLOT` | 산점도 | 규칙8 | ○ | — |
| `WATERFALL_CHART` | 폭포 | 규칙9 | ○ | — |
| `HEATMAP` | 히트맵 | 규칙10 | ○ | — |

총 **10개 차트 + table_only + none**. 제거: `flowchart`, `timeline`, `mind_map`, `org_chart`, `process_diagram`, `venn_diagram`, `matrix_chart`, `value_chain` (8종).

### 4.3 향후 확장 후보 (이번 범위 외)

폐쇄망 도입 후 품질 안정화되면 검토: `AREA_CHART`, `RADAR_CHART`, `GAUGE`, `TREEMAP`, `BULLET_CHART`.

## 5. 상세 설계

### 5.1 enum 변경 ([src/models/enums.py:57-65](src/models/enums.py#L57-L65))

```python
class VisualizationType(str, Enum):
    """시각화 유형."""

    NONE = "none"
    TABLE_ONLY = "table_only"
    # 정량 차트
    BAR_CHART = "bar_chart"
    LINE_CHART = "line_chart"
    HORIZONTAL_BAR = "horizontal_bar"
    STACKED_BAR = "stacked_bar"
    GROUPED_BAR = "grouped_bar"
    PIE_CHART = "pie_chart"
    DONUT_CHART = "donut_chart"
    SCATTER_PLOT = "scatter_plot"
    WATERFALL_CHART = "waterfall_chart"
    HEATMAP = "heatmap"
```

### 5.2 판단 프롬프트 변경 (`analyzer_viz_judgment_system.txt`)

- **다이어그램 섹션(규칙 11~18) 전체 삭제**. Few-shot 예제 11~16(flowchart/timeline/mind_map/org_chart/matrix_chart/value_chain) 삭제.
- **N4 통과 시 반환값** 을 `CHART_TYPE: none` 에서 `CHART_TYPE: table_only` 로 변경. 단, `예제 17~19`(집계 1행, 이진 결과, KPI 2건)는 `CHART_TYPE: none` 유지. `예제 20`(행 150개)은 `table_only` 로 재작성.
- **유형명 목록**을 12개로 축소:
  ```
  정량 차트: line_chart, bar_chart, horizontal_bar, grouped_bar,
            stacked_bar, pie_chart, donut_chart, scatter_plot,
            waterfall_chart, heatmap
  비차트: table_only
  없음: none
  ```
- **예제 10 (heatmap) 재작성** — 현재 3×4=12셀 → **7×4=28셀**(월~일 × 오전/오후/저녁/심야)로 확장. SVG 예제와 전형 일치시켜 판단/생성 간 셀 규모 편차 제거.
- **stacked_bar vs grouped_bar 우선순위 명시** — "위에서부터 매칭" 규칙상 규칙2(stacked) 가 우선. 판단 근거 설명에 "3계열 이상 이고 **구성비 변화 해석 목적** → stacked, **절대값 비교 목적** → grouped" 를 한 줄 명시.

### 5.3 SVG base 프롬프트 변경 (`analyzer_viz_svg_system_base.txt`)

#### 5.3.1 SUPPORTED TYPES 축소

- `## SUPPORTED TYPES` 섹션을 10종으로 축소. 다이어그램/비교순위(radar/gauge/treemap) 제거.
- `## STYLE DETAILS` 의 "다이어그램" 하위 규칙 제거.

#### 5.3.2 SCALING RULES 신규 5종 추가

**stacked_bar**
- 각 x 위치에서 계열 누적: `y_cum[i] = Σ_{j<=i} value[j]`
- Y축 max = `max(Σ 전체계열) × 1.15`
- 각 계열 사각형: `y = plot_bottom - y_cum_after × scale`, `height = value × scale`

**grouped_bar**
- 그룹 내 막대 수 `n`, 그룹 너비 `gw = plot_width / 그룹수 × 0.7`
- 각 막대 너비 `bw = gw / n`, 간격 `gap = bw × 0.15`
- 그룹 x 중심에서 좌측부터 `n` 개 배치

**scatter_plot**
- X/Y 독립 스케일: `x_min = min(X) × 0.9 (양수) / × 1.1 (음수)`, `x_max = max(X) × 1.1`, Y 동일
- 원(circle): `r=5`, `fill=계열색`, `opacity=0.7`
- 추세선 선택적 — 규칙으로 강제하지 않음

**waterfall_chart**
- **입력 DATA 는 반드시 `유형` 필드 포함** (`시작/감소/증가/소계/최종` 중 하나). SQL 생성 단계에서 이 필드를 산출해야 하므로 `sql_generator` 프롬프트에 waterfall 용도 쿼리 패턴 추가 필요(§11 결정사항).
- 색: 시작/소계/최종 = `#1a56db`, 감소 = `#ef4444`, 증가 = `#10b981`
- 누적 Y 좌표: 각 막대의 top = 이전 누적값, bottom = 새 누적값. 소계/최종은 0 기준 전체 막대.

**heatmap**
- 셀 강도 `t = value / max_value` (0~1 정규화)
- **단일색 opacity 스케일 채택**(3색 보간 대신): `fill=#1a56db`, `fill-opacity = 0.15 + 0.85 × t`
- 사유: LLM 이 RGB 보간 수식 실수할 여지 제거, 시각적 일관성 확보
- 셀 크기: 균등 분할, 셀 간격 2px, rx=2

### 5.4 예제 dict 변경 ([system_prompts.py:157-170](src/agents/nodes/system_prompts.py#L157-L170))

```python
ANALYZER_VIZ_SVG_EXAMPLES: dict[str, str] = {
    "bar_chart":        _present("analyzer_viz_svg_example_bar_chart.txt"),
    "line_chart":       _present("analyzer_viz_svg_example_line_chart.txt"),
    "horizontal_bar":   _present("analyzer_viz_svg_example_horizontal_bar.txt"),
    "stacked_bar":      _present("analyzer_viz_svg_example_stacked_bar.txt"),
    "grouped_bar":      _present("analyzer_viz_svg_example_grouped_bar.txt"),
    "pie_chart":        _present("analyzer_viz_svg_example_pie_chart.txt"),
    "donut_chart":      _present("analyzer_viz_svg_example_donut_chart.txt"),
    "scatter_plot":     _present("analyzer_viz_svg_example_scatter_plot.txt"),
    "waterfall_chart":  _present("analyzer_viz_svg_example_waterfall_chart.txt"),
    "heatmap":          _present("analyzer_viz_svg_example_heatmap.txt"),
}
```

### 5.5 예제 파일 작업

**삭제 (3개)**: `analyzer_viz_svg_example_flowchart.txt`, `analyzer_viz_svg_example_timeline.txt`, `analyzer_viz_svg_example_mind_map.txt`

**정비 (5개)** — 예제 번호 제거 + 은행 도메인 데이터로 재작성:
- `bar_chart.txt` — 지점별 여신 실행액 TOP5
- `line_chart.txt` — 최근 12개월 연체율 추이
- `pie_chart.txt` — 상품유형별 대출 비중
- `donut_chart.txt` — 고객 세그먼트별 비중 (중앙 총 고객수)
- `horizontal_bar.txt` — 전체 지점(20개) 수신 실적 순위

**신규 작성 (5개)**:
1. `stacked_bar.txt` — 분기별 수익원 구성(이자수익/수수료수익/유가증권이익), 5분기×3계열
2. `grouped_bar.txt` — 분기별 지점그룹(수도권/광역시/지방)별 실적, 4분기×3그룹
3. `scatter_plot.txt` — 고객 소득 × 여신잔액 분포, 12~15개 포인트
4. `waterfall_chart.txt` — 2024년 손익 분해(매출액+ → 이자비용− → 대손비용− → 영업이익= → 영업외± → 당기순이익=)
5. `heatmap.txt` — 요일(월~일) × 시간대(오전/오후/저녁/심야) 카드 승인건수, 7×4=28셀

### 5.6 `chart_generator` 템플릿 폴백 변경

- **현재 지원**(`src/services/visualization/chart_generator.py:398-401`): `BAR_CHART, STACKED_BAR, LINE_CHART, PIE_CHART` **4종**. `TABLE_ONLY` 는 `None` 반환(미지원).
- **목표**: `BAR_CHART, LINE_CHART, PIE_CHART` **3종** 으로 축소. `STACKED_BAR` 1건 제거.
- 나머지 유형은 LLM SVG 실패 시 `VisualizationData()` 빈값 반환.
- 사유: 템플릿 구현 복잡도 대비 실용성 낮음. LLM 이 실패할 정도의 데이터면 템플릿도 품질 확보 어려움.

### 5.7 parse_viz_judgment 개선 ([src/services/data_analyzer.py:51-73](src/services/data_analyzer.py#L51-L73))

현재는 enum 변환 실패 시 `ValueError` 재raise → `llm_call_with_parse_retry` 가 3회 재시도 **낭비**. Enum 정합 후에는 미지원 값이 들어오는 것이 **판단 프롬프트 버그** 이므로 재시도 의미 없음.

```python
try:
    chart_type = VisualizationType(raw)
except ValueError:
    logger.warning(
        "판단 프롬프트가 미지원 유형 반환, NONE 폴백",
        raw_type=raw,
    )
    chart_type = VisualizationType.NONE
```

바깥의 `if chart_type is None:` 조건 블록 제거. 미지원 유형 → 즉시 `NONE` 폴백 정책 명문화. 재시도 0회로 레이턴시 개선.

## 6. 예제 품질 가이드

### 6.1 은행 도메인 데이터 원칙

- **금액 단위**: 억원(기본), 만원(고객 단위). "12,500" 형식으로 천 단위 구분
- **비율**: 소수점 1자리(12.3%), BIS/연체율은 소수 2자리(2.34%)
- **시점**: "2024년 3월", "Q1 2024", "2024-03"
- **카테고리**: 실제 은행 용어 — "주택담보대출", "개인신용대출", "기업여신", "요구불예금", "정기예금", "VIP/우수/일반/잠재/관리"

### 6.2 예제 SVG 공통 구조

1. 제목(y=35) / 2. 축·그리드(정량만) / 3. 데이터 요소 / 4. 값 레이블 / 5. 범례(다계열 또는 비율)

### 6.3 색상 검증

규칙 6색을 순서대로 사용: `#1a56db → #f59e0b → #10b981 → #ef4444 → #8b5cf6 → #ec4899`

예외:
- waterfall: 증가=#10b981, 감소=#ef4444, 시작/소계/최종=#1a56db
- heatmap: 단일색 `#1a56db` + opacity 스케일(§5.3.2)

**템플릿 폴백** (`chart_generator.py:37-40`) 은 별도 7색 팔레트 사용 — 의도된 차이. 코드 주석에 명시.

## 7. 테스트 질의 설계 (실제 seed 테이블 기반)

### 7.1 테스트 환경 전제 — DB 직접 조회로 검증

**전체 572개 테이블 모두 시딩 완료** (총 약 139K 행). DB 직접 조회로 확인된 핵심 제약:

#### 7.1.1 마스터 vs 이력 구분 (매우 중요)

- **마스터 테이블(`M` 접미사)은 단일 시점(2026-03-21) 스냅샷만** 저장.
  - `tb_adw_lnb301m` (대출 800건), `tb_adw_dep201p` (예금 600건), `tb_adw_crd401m` (카드 300건), `tb_adw_csc101m` (고객 500명) 모두 `std_dt = 2026-03-21` 단일값.
  - **→ 이 테이블로는 "월별 추이" 같은 시계열 차트 생성 불가.** 단일 시점 분포/순위/구성비만 가능.
- **시계열은 이력 테이블(`L` 접미사) / 전용 테이블 사용**:

| 테이블 | 시계열 컬럼 | 범위 | 행수 | 용도 |
|--------|-----------|------|------|------|
| `tb_adw_lnb311l` | `ovdu_start_dt` | 2025-03-21 ~ 2026-03-21 | 940 | 연체 발생 이력 |
| `tb_adw_lnb330l` | `recovery_dt` | 2025-03-21 ~ 2026-03-21 | 993 | 대출 회수 이력 |
| `tb_adw_crd406l` | `issue_dt` | 2025-03-21 ~ 2026-03-21 | 930 | 카드 발급 이력 |
| `tb_adw_dep234l` | `wdrw_dt` | 2025-03-21 ~ 2026-03-21 | 865 | 예금 출금 이력 |
| `tb_adw_trx701l` | `tr_dt` | 2025-04-01 ~ 2026-03-21 | 3,000 | 거래 이력 (일+시간) |
| `tb_adw_fin1306s` | `base_ym` | 202506 ~ 202603 (10개월) | 2,000 | 손익항목별 월집계 |
| `tb_adw_rsk1101m` | `std_dt` | 2025-06-24 ~ 2026-03-21 (10일치) | 100 | 리스크 지표 일별 |

#### 7.1.2 실제 코드값·카테고리 분포 (DB 조회 결과)

| 구분 | 테이블.컬럼 | 값 분포 |
|------|-----------|--------|
| 고객등급 | `csc101m.cus_grd_cd` | 01(18) / 02(87) / 03(169) / 04(122) / 05(82) / 99(14) / NULL(8) |
| 대출구분 | `lnb301m.ln_dcd` | 01(316) / 02(357) / 03(127) — 3종 |
| 리스크 지표 | `rsk1101m.ind_cd` | BIS / CVA / DSR / LCR / LTV / NIM / NPL / NSFR / ROA / ROE — 10종 × 10일 |
| 손익항목 | `fin1306s.pl_item_cd` | INT_INC / FEE_INC / FX_INC / FUND_INC / NII / NFI / OPEX / PROV / PRETAX / NET — 10종 × 200건 |
| 지점 | `com001m` | 20개(12개 지역: 서울8/경기2/기타10) |

#### 7.1.3 테스트 기준일 및 단위

- **기준일(STD_DT)**: 2026-03-21
- **시계열 질의**: "최근 N개월" 형식 (fin1306s: 최대 10개월, 이력: 최대 12개월)
- **금액 단위**: numeric(18,2) 원화 기준 — 시각화 시 "만원/억원" 변환

### 7.2 차트 유형별 테스트 질의 카탈로그

각 유형별 3개 이상 질의. 기대 CHART_TYPE + SQL 형상 + 사용 테이블 + 실현가능성 명시.

#### bar_chart (마스터 스냅샷 기반)

| # | 질의 | 기대 결과 형상 | 테이블 |
|---|------|--------------|--------|
| B1 | "상품유형별 대출잔액 비교해줘" | (ln_dcd, SUM(ln_bal_amt)) 3행 | lnb301m |
| B2 | "고객등급별 평균 예금잔액" | (cus_grd_cd, AVG(bal_amt)) 5~7행 | dep201p + csc101m |
| B3 | "연령대별 고객 수" | (age_grp_cd, COUNT) 5~7행 | csc101m |
| B4 | "대출구분별 평균 금리" | (ln_dcd, AVG(aply_rt)) 3행 | lnb301m |

#### line_chart (시계열 전용 테이블 필수)

| # | 질의 | 기대 결과 형상 | 테이블 |
|---|------|--------------|--------|
| L1 | "최근 10일 BIS 비율 추이" | (std_dt, ind_val) 10행 | rsk1101m WHERE ind_cd='BIS_RATIO' |
| L2 | "2025년 4월부터 월별 거래건수 추이" | (date_trunc('month',tr_dt), COUNT) 12행 | trx701l |
| L3 | "최근 10개월 순이자이익(NII) 월별 추이" | (base_ym, SUM(amt)) 10행 | fin1306s WHERE pl_item_cd='NII' |
| L4 | "최근 12개월 월별 연체 발생 건수" | (date_trunc('month',ovdu_start_dt), COUNT) 12행 | lnb311l |

#### horizontal_bar

| # | 질의 | 기대 결과 형상 | 테이블 |
|---|------|--------------|--------|
| H1 | "전체 지점 여신잔액 순위" | (br_nm, SUM(ln_bal_amt)) 20행 | lnb301m + com001m |
| H2 | "지점별 예금 잔액 많은 순" | (br_nm, SUM(bal_amt)) 20행 | dep201p + com001m |
| H3 | "리스크 지표별 현재값 비교" | (ind_nm, ind_val) 10행 | rsk1101m (최신일자) |
| H4 | "손익항목별 누적 금액 순위" | (pl_item_nm, SUM(amt)) 10행 | fin1306s |

#### stacked_bar (시계열 + 다계열)

| # | 질의 | 기대 결과 형상 | 테이블 |
|---|------|--------------|--------|
| S1 | "월별 손익 구성 10개월 (이자·수수료·외환·펀드 등)" | (base_ym × pl_item_cd pivot) 10행 × 5계열 | fin1306s |
| S2 | "월별 지역별 거래 건수 구성" | (연월 × 지역 pivot) 12행 × 3~5지역 | trx701l + com001m |
| S3 | "분기별 카드구분별 발급 건수 구성" | (분기 × crd_dcd pivot) 4분기 × 3~4유형 | crd406l |

#### grouped_bar (시계열 + 절대값 비교)

| # | 질의 | 기대 결과 형상 | 테이블 |
|---|------|--------------|--------|
| G1 | "분기별 지역권별(수도권/광역시/기타) 연체 발생건수 비교" | (분기 × 지역권 pivot) 4×3 | lnb311l + com001m |
| G2 | "분기별 대출구분별 회수 건수 비교" | (분기 × ln_dcd pivot) 4×3 | lnb330l |
| G3 | "최근 10개월 NII·NFI·OPEX 비교" | (base_ym × 3계열) 10×3 | fin1306s |

#### pie_chart

| # | 질의 | 기대 결과 형상 | 테이블 |
|---|------|--------------|--------|
| P1 | "고객등급별 분포 비중" | (cus_grd_cd, COUNT) 5행 (NULL/99 제외) | csc101m |
| P2 | "대출구분별 잔액 비중" | (ln_dcd, SUM) 3행 | lnb301m |
| P3 | "지역권별 고객 비중 (서울/경기/기타)" | (region_group, COUNT) 3행 | csc101m + com001m |

#### donut_chart (중앙 KPI 표시)

| # | 질의 | 기대 결과 형상 | 테이블 |
|---|------|--------------|--------|
| D1 | "고객등급별 대출잔액 구성과 총 잔액" | (cus_grd_cd, SUM) + 총합 | lnb301m + csc101m |
| D2 | "지역별 예금잔액 비중 (총액 중앙 표시)" | (rgn_nm, SUM) + 총합 | dep201p + com001m |

#### scatter_plot

| # | 질의 | 기대 결과 형상 | 테이블 |
|---|------|--------------|--------|
| SC1 | "고객별 예금잔액과 대출잔액 관계" | (dep_bal, ln_bal) 100~300 포인트 | dep201p + lnb301m (동일 edps_csn JOIN) |
| SC2 | "고객별 가입기간과 거래건수 분포" | (가입 경과일, COUNT(tr_id)) 500 포인트 | csc101m + trx701l |
| SC3 | "지점별 대출잔액과 예금잔액 관계" | (ln_total, dep_total) 20 포인트 | lnb301m + dep201p + com001m |

#### waterfall_chart

| # | 질의 | 기대 결과 형상 | 테이블 |
|---|------|--------------|--------|
| W1 | "2026년 3월 손익 구조 분해" | (pl_item_nm, amt, type) 5~7행 | fin1306s WHERE base_ym='202603' |
| W2 | "최근달 영업이익 구성 분해" | (NII+, NFI+, FX_INC+, FUND_INC+, OPEX−, PROV−, PRETAX=) | fin1306s pivot |

> **구현 주의**: `pl_item_cd` 는 이미 `NII/NFI/PRETAX/NET` 같은 소계 항목을 포함함 → `sql_generator` 가 이 코드에서 `CASE WHEN pl_item_cd IN ('NII','NFI','PRETAX','NET') THEN '소계' WHEN pl_item_cd IN ('OPEX','PROV') THEN '감소' ELSE '증가' END AS 유형` 을 자동으로 덧붙이도록 프롬프트 가이드 추가 (§11 결정사항).

#### heatmap

| # | 질의 | 기대 결과 형상 | 테이블 |
|---|------|--------------|--------|
| HM1 | "요일·시간대별 거래 건수 분포" | (EXTRACT(dow), 시간대4구간, COUNT) 7×4=28 | trx701l |
| HM2 | "지역별 × 대출구분별 잔액 집중도" | (rgn_nm × ln_dcd) 12×3=36 | lnb301m + com001m |
| HM3 | "월별 × 리스크지표별 값 변동" | (월 × 10지표) | rsk1101m (월평균) |

#### none / table_only

| # | 질의 | 기대 결과 | 판정 |
|---|------|---------|------|
| N1 | "전체 고객 수 알려줘" | COUNT 1행 | none |
| N2 | "현재 BIS 비율 얼마야" | 단일값 1행 | none |
| N3 | "최근 거래 20건 보여줘" | (tr_dt, acn, tr_amt, blng_brcd) 20행 | table_only |
| N4 | "VIP 고객 목록" | (edps_csn, csm, cus_grd_cd, join_dt) | table_only |
| N5 | "모든 지점 현황" | (blng_brcd, br_nm, rgn_nm, br_dcd) 20행 | table_only |

### 7.3 경계 케이스 (모델 편차 탐지용)

| # | 질의 | 모호 지점 | 기대 판정 |
|---|------|---------|--------|
| E1 | "지점별 실적과 지점정보 (지점명, 지역, 지점장, 실적)" | N4 vs 규칙5 | horizontal_bar (수치 비교 의도) |
| E2 | "고객 TOP10 잔액" | 규칙4 vs N4 | bar_chart (TOP N 수치 비교) |
| E3 | "분기별 손익 3계열 추이" | stacked vs grouped | "추이/변화" → stacked, "비교" → grouped |
| E4 | "손익항목 10종 비중" (>6개) | pie 초과 → horizontal_bar | horizontal_bar |
| E5 | "매우 긴 상품명들의 잔액" | bar vs horizontal_bar | horizontal_bar (레이블 길이) |
| E6 | "500명 전체 고객 소득·잔액 산점" | scatter vs N6 | scatter (포인트 많아도 유효) |
| E7 | "요일×시간대×지역 3차원 거래" | heatmap vs N6 | none + 차원 축소 권고 |
| E8 | "고객등급 NULL 포함 분포" | pie vs none (NULL 처리) | pie_chart (NULL을 "미분류"로) |

### 7.4 테스트 데이터로 실현 불가능한 질의 (제외)

의도적으로 배제 — 설계 단계에서 **불가능** 이 확인된 질의들:

- ❌ "월별 대출잔액 추이" — lnb301m 은 단일 시점, 월별 잔액 스냅샷 이력 테이블 없음
- ❌ "연도별 신규가입 추이" — csc101m.join_dt 가 있으나 std_dt 가 단일값이라 연도별 집계는 가능하나 **5년 분포가 확인된 바 없음** → 실데이터 조회 후 확정
- ❌ "최근 5년 BIS 추이" — rsk1101m 은 10일치만
- ❌ "일별 거래 건수 2년" — trx701l 은 1년치만

골든셋 구축 시 **실 SQL 실행 + 결과 형상 확인** 을 거쳐 위 목록에 추가/삭제.

### 7.5 테스트 자동화 구조

```
tests/auto/golden/visualization/
  ├── test_viz_judgment.py       # §7.2 35개 + §7.3 8개 = 43 케이스
  ├── test_viz_svg_validity.py   # LLM SVG 구조 검증
  ├── test_viz_sql_feasibility.py # SQL 실행 가능성 사전 검증
  └── fixtures/
      └── viz_test_cases.yaml    # 질의·기대 chart_type·기대 DATA 형상·사용 테이블
```

**YAML 스키마**:

```yaml
- id: B1
  query: "상품유형별 대출잔액 비교해줘"
  expected_chart_type: bar_chart
  tables: [tb_adw_lnb301m]
  expected_row_count_min: 3
  expected_row_count_max: 3
  expected_columns: [ln_dcd, loan_balance]
```

**검증 항목**
- **실행 가능성**: `test_viz_sql_feasibility.py` 가 fixtures 의 expected SQL 패턴을 실행하여 0건이 아닌지 사전 확인
- **판단 정확도**: 기대 CHART_TYPE 일치율 ≥ 90%
- **SVG 유효성**: 시작/종료 태그, viewBox, 차트별 최소 요소 수
- **색상 준수**: 지정 6색(waterfall/heatmap 예외)
- **경계 케이스 회귀**: E1~E8 판정 변동 탐지

## 8. 구현 로드맵

### Phase 1 — 정합 맞추기 (0.5 day)

1. `VisualizationType` enum 12종 확장
2. 판단 프롬프트 다이어그램 섹션/예제 삭제, 유형 12종으로 축소
3. SVG base `SUPPORTED TYPES` 10종 정비, 다이어그램 STYLE 제거
4. 예제 파일 **3개 삭제** (flowchart/timeline/mind_map), dict 키 제거
5. `parse_viz_judgment` 즉시 NONE 폴백 + 경고 로그
6. `chart_generator` 에서 `STACKED_BAR` 폴백 제거

### Phase 2 — 기존 예제 정비 (0.5 day)

7. 예제 **5개** 재작성: bar, line, pie, donut, horizontal_bar — 예제 번호 제거 + 은행 도메인 데이터
8. SVG 좌표·색상·레이블 재계산

### Phase 3 — 신규 예제 작성 (1.5~2 day)

9. `stacked_bar`, `grouped_bar`, `scatter_plot`, `waterfall_chart`, `heatmap` 5개 SVG 수작업 설계
10. 각 SVG 브라우저 렌더 검증 (좌표/색상/가독성)
11. SVG base `SCALING RULES` 에 5종 규칙 반영 (§5.3.2)
12. 판단 프롬프트 예제 10(heatmap) 28셀로 재작성

### Phase 4 — 테스트 질의 구현 (0.5~1 day)

13. `tests/auto/golden/visualization/fixtures/viz_test_cases.yaml` 작성 — §7.2 32개 + §7.3 7개
14. `test_viz_judgment.py` 구현 — LLM 호출로 CHART_TYPE 일치 검증
15. `test_viz_svg_validity.py` 구현 — SVG 구조 검증

### Phase 5 — 검증·통합 (0.5 day)

16. 회귀: 기존 테스트 스위트 전체 실행
17. 골든셋 정확도 측정 및 프롬프트 튜닝
18. 판단 프롬프트 토큰 실측 (§9.4 현재 추정치 검증)

**총 소요**: 약 **3.5~4.5 일** (Phase 3·4 가 메인).

## 9. 위험 / 제약

### 9.1 소형 모델 판단 정확도

폐쇄망 Qwen3.5 397B / Solar Pro 2 70B 에서 10종 선택은 감당 가능 범위. 19종 → 10종 축소는 오히려 정확도 상승 기대.

### 9.2 SVG 생성 실패율

신규 5종은 bar/line 대비 복잡. 모델별 실패율 측정 후 필요 시 해당 유형만 few-shot 추가.

### 9.3 UI 렌더링 호환성

waterfall 소계(=) 바, heatmap opacity 스케일은 일반 SVG 렌더러에서 정상 동작 검증 필요.

### 9.4 판단 프롬프트 토큰

현재 약 2.3K 추정 → 다이어그램 섹션 8규칙·예제 6개 제거 시 약 1.3~1.5K 예상. **구현 후 실측 필요**(근거 없는 추정).

### 9.5 체크포인터 하위호환성 — **불필요**

`VisualizationType` 은 `VisualizationData` Pydantic 필드로만 존재(`src/models/result.py:71`), PostgreSQL 체크포인터에 pickle 로 직렬화됨. 이번 변경은 **enum 확장만 있고 제거되는 값이 없으므로** 기존 체크포인트 레코드 호환성 파괴 없음. 기존 문자열 값(`bar_chart`, `line_chart`, `pie_chart`, `stacked_bar`, `none`, `table_only`) 모두 신규 enum 에서 유효.

### 9.6 waterfall SQL 생성 의존성

판단 프롬프트는 `유형` 필드를 DATA 에 넣도록 지시하지만, 이는 **SQL 결과에 `유형` 컬럼이 있어야** 가능. `sql_generator` 프롬프트에 waterfall 패턴 가이드 추가 필요. 없으면 LLM 이 항목명·금액 부호로 추론하나 모델 편차 큼.

## 10. 테스트 전략

### 10.1 유닛

- `parse_viz_judgment` — enum 밖 값 → 로그 + NONE 처리 (재시도 0회 검증)
- `judge_visualization` — 각 chart_type 반환 케이스

### 10.2 골든셋

§7.2 (기본 32 케이스) + §7.3 (경계 7 케이스) = **39 케이스**. fixtures YAML 로 관리, CI 에서 주기 실행.

### 10.3 SVG 유효성 자동 검증

- 시작/종료 태그
- `viewBox="0 0 800 500"` 정확 일치
- 차트별 최소 요소 수:
  - line: polyline 1+, circle 3+
  - bar: rect 3+
  - pie: path 2+
  - heatmap: rect 12+
  - scatter: circle 5+

### 10.4 수동 검증

Phase 3 완료 시 10개 예제 파일 SVG 를 브라우저로 직접 렌더하여 시각 품질 확인 (체크리스트: 잘림·겹침·색상 대비·레이블 가독성).

## 11. 결정 필요 사항

1. **waterfall SQL 생성 가이드** — `sql_generator` 프롬프트에 추가할지, 아니면 판단 프롬프트에 "유형 필드 없으면 부호·이름으로 추론" 휴리스틱 명시할지. 추천: **SQL 생성 가이드 추가** (더 안정적).
2. **heatmap 3차원 데이터 정책** (E7) — 요일×시간대×지역 같은 3차원 요청은 자동 차원 축소할지, `none + 권고 메시지` 할지. 추천: **none + 권고** (자동 축소는 의도 왜곡 위험).
3. **예제 해상도** — `viewBox="0 0 800 500"` 고정. 4K 환경에서 확대 시 선명도는 SVG 특성상 무관.
4. **신규 5종 SVG 제작 주체** — 본인 작성 vs 서브에이전트 위임. SVG 좌표 정확성이 핵심 → **본인 수작업 권장**.

**Closed**:
- ~~`TABLE_ONLY` enum 유지 여부~~ → **유지**. N4 명세성 데이터 라우팅 명시화를 위해 필요.
- ~~`chart_generator` 템플릿 폴백 범위~~ → **3종**(`bar_chart`, `line_chart`, `pie_chart`).

## 12. 참고

- 판단 프롬프트: `resources/prompts/present/analyzer_viz_judgment_system.txt`
- SVG base 프롬프트: `resources/prompts/present/analyzer_viz_svg_system_base.txt`
- 서비스 진입점: `src/services/data_analyzer.py:223` `build_visualization`
- 노드: `src/agents/nodes/present/analyzer.py:57` `analyze_data_node`
- 템플릿 폴백: `src/services/visualization/chart_generator.py:398-401`
- 테스트 기준일: **2026-03-21** (seed_postgres 기준)
- 실데이터 테이블 목록: `devtools/scripts/seed_postgres.py`
