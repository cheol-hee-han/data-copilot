# Formatter 스마트 개선 방안

> **작성일**: 2026-04-07
> **대상**: `src/services/response_formatter.py`, `src/agents/nodes/present/formatter.py`, `resources/prompts/present/formatter_system.txt`
> **목적**: 결과 출력 포매터를 컨텍스트 인지형으로 고도화

---

## 현재 구조 분석

### 데이터 흐름

```
SQL Result → Analyzer (병렬) → 인사이트/시각화
           → Formatter (병렬) → 보고서 텍스트
```

### 현재 Formatter 입력

| 입력 | 설명 |
|------|------|
| `user_input` | 원본 사용자 질의 |
| `sql_result` | SQLResult(columns, rows, row_count, execution_time_ms) |
| `code_mappings` | SELECT 컬럼 기준 필터링된 코드 매핑 텍스트 |

### 핵심 문제: 컨텍스트 단절

Formatter는 **raw SQL 결과 + 코드매핑**만 받고, analyzer의 분석 결과(인사이트, 통계, 시각화 정보)를 전혀 모름. analyzer와 formatter가 **병렬 실행**되기 때문.

---

## 개선 방안

### A. 컨텍스트 주입 (analyzer → formatter 순차 전환)

analyzer 결과를 formatter에 전달하여 분석 맥락을 반영한 포매팅 수행.

```python
# formatter.py 변경안
def format_response_node(state: PipelineState) -> dict:
    analysis = state.get("analysis_result")  # analyzer 결과 활용
    format_response(
        user_input=state["user_input"],
        sql_result=state["sql_result"],
        code_mappings=code_mappings,
        analysis_context=analysis,         # NEW: 분석 컨텍스트 전달
        viz_type=state.get("viz_type"),     # NEW: 시각화 유형 전달
    )
```

**영향**: 파이프라인 그래프 구조 변경 필요 (병렬 → 순차)

---

### B. 의도 기반 적응형 포매팅

쿼리 의도(intent)에 따라 포매팅 전략을 달리함.

| 의도 유형 | 포매팅 전략 | 예시 |
|-----------|------------|------|
| **추이 분석** | 시계열 강조, 방향 지표, 변동폭 하이라이트 | "전월 대비 12.3% **증가** ↑" |
| **순위/비교** | 상위/하위 강조, 순위 번호, 차이값 표시 | "1위: 강남지점 (2위 대비 +23억)" |
| **구성비 분석** | 비율 합계 100% 검증, 주요 구성요소 강조 | "여신이 전체의 **62.3%**로 가장 큰 비중" |
| **단건 조회** | 표 대신 카드형 레이아웃, key-value 나열 | "고객명: 홍OO / 등급: VIP / 잔액: 5.2억원" |
| **대량 목록** | 표 위주, 요약 통계(합계/평균/최대/최소) 자동 부가 | "총 156건 / 합계: 1,234억원 / 평균: 7.9억원" |

**구현**: `formatter_system.txt`에 의도별 포매팅 가이드 + user prompt에 `query_intent` 필드 추가

```
[쿼리 의도]
{query_intent}  ← interpret 단계에서 추출한 intent_type

[분석 요약]
{analysis_summary}  ← analyzer 결과의 summary + insights
```

---

### C. 스마트 요약 생성

현재: "1~2줄 요약" 일률 적용
개선: 데이터 패턴에 따른 동적 요약 전략

```
## 요약 작성 전략 (formatter_system.txt 추가)

데이터 특성에 따라 요약 방식을 선택하세요:

1. **추이 데이터**: 방향(증가/감소/보합)과 변동 크기를 먼저 언급
   예: "최근 3개월간 신규 여신 실행액이 월평균 8.2% 증가하고 있습니다"

2. **비교 데이터**: 가장 높은/낮은 항목과 그 차이를 언급
   예: "강남지점이 1,234억원으로 가장 높고, 최하위 대비 3.2배 차이입니다"

3. **이상치 포함**: 이상치를 먼저 언급하고 원인 가능성 제시
   예: "3월 실적이 전월 대비 45% 급감했으며, 시즌 효과 가능성이 있습니다"

4. **단건 결과**: 핵심 수치를 자연어로 풀어서 설명
   예: "홍길동 고객은 VIP 등급으로, 총 예금 잔액은 5.2억원입니다"

5. **빈 결과**: 가능한 원인과 대안 질문 제안
   예: "조건에 맞는 데이터가 없습니다. 기간을 넓히거나 조건을 변경해보시겠어요?"
```

---

### D. 시각화-텍스트 연동

현재 차트와 텍스트가 독립적으로 생성되어 사용자가 연결점을 찾아야 함.

**개선**: formatter가 시각화 유형을 알면 텍스트에서 차트를 참조 가능

```
# 프롬프트에 추가
[시각화 정보]
차트 유형: {viz_type}  (예: bar_chart, line_chart, pie_chart)
차트 제목: {viz_title}

## 시각화 참조 규칙
- 시각화가 있으면 "위 차트에서 보시듯이..." 또는 "그래프를 보시면..." 형태로 연결
- 차트의 핵심 포인트를 텍스트에서 보충 설명
```

---

### E. 테이블 포매팅 고도화

현재: 모든 숫자에 천단위 콤마만 적용

| 개선 항목 | 현재 | 개선 후 |
|-----------|------|---------|
| **음수 표시** | -1234 | △1,234 또는 (1,234) |
| **변동 지표** | 12.3 | 12.3% ↑ |
| **상위/하위** | 동일 스타일 | **볼드** 처리 (마크다운) |
| **합계 행** | 없음 | 하단 합계/평균 자동 추가 |
| **열 단위** | 없음 | 헤더에 (억원), (%), (건) 단위 표기 |
| **날짜 컬럼** | 202403 | 2024년 3월 (자동 변환) |

**구현**: `rows_to_markdown_table()` 함수에 컬럼 타입 추론 로직 추가

```python
# response_formatter.py 개선안
def _infer_column_type(col_name: str, values: list) -> str:
    """컬럼명과 값으로 타입 추론: amount, ratio, count, date, code, text"""
    if any(kw in col_name for kw in ['금액', 'amt', '잔액', '실적']):
        return 'amount'
    if any(kw in col_name for kw in ['율', '비율', 'rate', 'ratio']):
        return 'ratio'
    # ... 패턴 기반 추론

def _format_cell(value, col_type: str) -> str:
    """타입별 셀 포매팅"""
    if col_type == 'amount':
        return format_korean_amount(value)  # 5.2억원
    if col_type == 'ratio':
        return f"{value:.1f}%"
    if col_type == 'date':
        return format_korean_date(value)  # 2024년 3월
```

---

### F. 도메인 특화 포매팅 (금융)

은행 업무 특성상 특수 포매팅이 필요한 영역:

| 도메인 | 특수 요구사항 |
|--------|-------------|
| **여신** | 연체율 빨간색 강조, 대출 등급별 구분, 만기 임박 알림 |
| **수신** | 예금 잔액 추이, 금리 비교 하이라이트 |
| **카드** | 사용 패턴 분석, 한도 대비 사용률 |
| **실적** | 목표 대비 달성률 (예: ██████░░ 75%), 순위 |

**구현**: interpret 단계의 `subject_area`를 formatter에 전달하여 도메인별 프롬프트 분기

---

### G. 결과 없음/소량 대응 강화

현재: LLM에 맡김 (일관성 없음)
개선: 명시적 분기 처리

```python
# formatter.py에 pre-check 추가
if sql_result.row_count == 0:
    return _format_empty_result(user_input, sql_info)
elif sql_result.row_count == 1:
    return _format_single_row(user_input, sql_result)  # 카드형 레이아웃
elif sql_result.row_count <= 5:
    return _format_small_table(user_input, sql_result)  # 표 + 상세 설명
else:
    return _format_with_llm(...)  # 기존 LLM 포매팅
```

---

## 구현 우선순위

| 우선순위 | 항목 | 난이도 | 효과 | 변경 범위 |
|----------|------|--------|------|-----------|
| 1 | **B. 의도 기반 포매팅** | 낮음 | 높음 | 프롬프트 수정 |
| 2 | **C. 스마트 요약** | 낮음 | 높음 | 프롬프트 수정 |
| 3 | **E. 테이블 고도화** | 중간 | 높음 | `response_formatter.py` |
| 4 | **A. 컨텍스트 주입** | 중간 | 높음 | 노드 순서 + 서비스 변경 |
| 5 | **G. 결과 없음/소량 대응** | 낮음 | 중간 | `formatter.py` 분기 |
| 6 | **D. 시각화-텍스트 연동** | 낮음 | 중간 | 프롬프트 수정 |
| 7 | **F. 도메인 특화** | 높음 | 높음 | 프롬프트 + 분기 로직 |

---

## 관련 파일

| 용도 | 파일 경로 |
|------|-----------|
| Formatter 프롬프트 | `resources/prompts/present/formatter_system.txt` |
| Formatter 템플릿 | `resources/prompts/present/formatter_user.txt` |
| Formatter 노드 | `src/agents/nodes/present/formatter.py` |
| Formatter 서비스 | `src/services/response_formatter.py` |
| Analyzer 노드 | `src/agents/nodes/present/analyzer.py` |
| Analyzer 서비스 | `src/services/data_analyzer.py` |
| 차트 판단 프롬프트 | `resources/prompts/present/analyzer_viz_judgment_system.txt` |
| SVG 생성 프롬프트 | `resources/prompts/present/analyzer_viz_svg_system.txt` |
| 응답 모델 | `src/agents/models/response.py` |
| 결과 모델 | `src/models/result.py` |
| WebSocket 핸들러 | `src/main.py` (_run_ws_pipeline) |
