# Formatter Rule-Based 전환 + 조회 과정 요약 구조화 — 통합 설계

> **작성일**: 2026-04-07
> **관련 문서**: `20260407-output-structure-gap-analysis.md`, `20260407-formatter-smart-improvement.md`
> **상위 논의**: 답변 출력 구조 개선 — 논의 2 (조회 과정 요약 개선)
> **목적**: formatter LLM 호출 완전 제거, rule-based 포맷팅 전환, 5단계 조회 과정 요약 구현

---

## 1. 배경 및 동기

### 1-1. 현재 구조의 문제

formatter 노드(`src/agents/nodes/present/formatter.py`)가 LLM을 호출하여 수행하는 작업:

| 작업 | LLM 필요 여부 | 근거 |
|------|:---:|------|
| 마크다운 테이블 재포맷 | X | 문자열 조립으로 충분 |
| 금액 단위 변환 (1조/1억/1만) | X | 임계값 분기 함수로 충분 (프롬프트에 이미 규칙이 명시적) |
| 비율/건수 포맷 | X | `f"{value:.1f}%"`, `f"{value:,}건"` |
| 날짜 변환 ("202403"→"2024년 3월") | X | `datetime.strptime` + format |
| 코드값→한글 변환 | X | dict lookup |
| 핵심 수치 1~2줄 요약 | X | 템플릿 기반으로 대체 가능 |
| "조회 기준 안내" | X | `build_auto_resolved_notice` + 5단계 요약으로 대체 |

**결론**: formatter LLM 호출은 완전히 제거 가능하다.

### 1-2. 전환의 이점

1. **LLM 호출 1회 절감** — 레이턴시 감소, 비용 절감
2. **결정론적 출력** — 동일 입력이면 동일 출력, 디버깅 용이
3. **폐쇄망 모델 의존도 감소** — 소형 모델에서 포맷팅 품질 걱정 불필요
4. **`format_trace_summary`(TraceEntry 나열) 대체** — 실질적 정보가 담긴 5단계 구조화 요약으로 전환

### 1-3. 설계 원칙

- SQL Generator가 이미 한글 alias를 부여 (프롬프트 규칙 6번) → 테이블 컬럼명 별도 변환 불필요
- 컬럼 타입 판별은 SQL 원본 컬럼 접미사 기반 (정보계 DB 네이밍 규칙 활용)
- 5단계 요약은 State 필드에서 직접 조합 (EvaluationTrace 접근 불필요)
- `build_auto_resolved_notice`는 제거하고 5단계 요약의 "AI판단" 섹션으로 통합

---

## 2. 현재 상태 (AS-IS)

### 2-1. formatter.py 흐름

```
format_response_node(state):
  1. _build_code_mappings()       ← explored_codes에서 SELECT 컬럼 관련 코드만 필터
  2. format_response()            ← LLM 호출 (system: formatter_system.txt, user: formatter_user.txt)
  3. build_auto_resolved_notice() ← rule-based (INFER 시그널 → "조회 기준 안내:")
  4. is_force_generated 경고      ← 고정 문자열
  5. format_trace_summary()       ← TraceEntry 나열 → <details> 접기
  → formatted_response 에 결합하여 반환
```

### 2-2. 출력 구조

```
[force_generated 경고]                    ← 선택적 (고정 문자열)
[INFER 안내 — "조회 기준 안내:"]           ← 선택적 (rule-based)
[LLM 보고서]
  | 마크다운 테이블 (숫자 단위변환 포함) |
  핵심 수치 1~2줄 요약
  ### 조회 기준 안내                      ← LLM이 생성하는 "어떤 조건으로 조회했는지" 설명
<details>
  <summary>조회 과정 요약</summary>
  1. 의도분류: DATA_EXTRACTION
  2. 정규화: 8-slot 변환 완료
  ...
</details>
```

### 2-3. 관련 파일

| 파일 | 역할 |
|------|------|
| `src/agents/nodes/present/formatter.py` | 노드 — LLM 호출 오케스트레이션 |
| `src/services/response_formatter.py` | 서비스 — LLM 호출, `rows_to_markdown_table` |
| `resources/prompts/present/formatter_system.txt` | 시스템 프롬프트 (포맷팅 규칙) |
| `resources/prompts/present/formatter_user.txt` | 유저 프롬프트 템플릿 |
| `src/agents/nodes/system_prompts.py` | `FORMATTER_SYSTEM`, `FORMATTER_USER` 로드 |
| `src/models/trace.py` | `TraceEntry`, `add_trace`, `format_trace_summary` |
| `src/agents/utils/clarification_context.py` | `build_auto_resolved_notice` |
| `src/services/insight_builder.py` | insight 패널 데이터 빌더 (trace_log 타이밍 의존) |
| `src/agents/models/response.py` | `PipelineResult` (trace_log 포함) |
| `src/agents/graph/pipeline.py:559` | `simple_responder → format_response` 엣지 |
| `src/main.py:698-707` | API 응답에 trace_log 포함 |

---

## 3. 목표 상태 (TO-BE)

### 3-1. formatter.py 흐름

```
format_response_node(state):
  [가드] simple_responder가 이미 응답 완성한 경우 → 스킵
  1. detect_column_formats()       ← SQL 접미사 기반 컬럼 타입 판별
  2. apply_code_mappings()         ← 코드값 → 한글 dict lookup
  3. format_report_table()         ← 마크다운 테이블 + 셀 단위 포맷팅
  4. build_summary_line()          ← 핵심 수치 템플릿 요약
  5. is_force_generated 경고       ← 기존 유지 (고정 문자열)
  6. build_process_summary()       ← 5단계 조회 과정 요약 (INFER 안내 통합)
  → formatted_response 에 결합하여 반환
```

### 3-2. 출력 구조

```
[force_generated 경고]                    ← 선택적 (기존 유지)
| 마크다운 테이블 (rule-based 단위변환) |
핵심 수치 요약 (템플릿)
<details>
  <summary>조회 과정 요약</summary>
  **1. 질의 분류**
  데이터 추출 요청 (신규 질의)

  **2. 질의 해석**
  - 측정값: 신규 여신 건수, 금액 합계
  - 조건: 이번 달 (2026년 4월)
  - 그룹: 지점별

  **3. 활용 정보**
  - 여신기본마스터(TB_ADW_LNB301M), 부점정보기본(TB_ADW_COM001M) 2개 테이블 사용
  - 유사 SQL 2건 참조, 업무 매뉴얼 1건 확인

  **4. AI 판단**
  - '이번 달 신규'의 해석 → 대출실행일자(LN_DT) 기준 당월 실행 건
  - 이전 대화의 연속으로 판단 → 동일 기간·대상 기준 유지
  (다른 기준을 원하시면 말씀해 주세요)

  **5. 검증 결과**
  SQL 검증 통과. 8개 항목 모두 정상. 342건 조회.
  사용자 질의 의도와 SQL 구조가 정확히 부합합니다.
</details>
```

---

## 4. 상세 구현

### 4-1. 컬럼 타입 판별 — `detect_column_formats()`

**위치**: `src/services/response_formatter.py`

SQL의 `extract_select_alias_map()`을 활용하여 {한글alias: 원본컬럼명} 매핑을 추출하고,
원본 컬럼명의 접미사로 포맷 타입을 추론한다.

```python
_CURRENCY_SUFFIXES = ("_AMT", "_BAL", "_PRIN", "_INT_AMT", "_TAMT", "_QTY_AMT")
_RATE_SUFFIXES = ("_RT", "_RATE", "_RTO", "_RATIO")
_COUNT_SUFFIXES = ("_CNT", "_NUM", "_QTY")
_COUNT_FUNCTIONS = ("COUNT(",)

def detect_column_formats(
    sql: str,
    dialect: str | None = None,
) -> dict[str, str]:
    """SQL의 SELECT alias → 원본컬럼명 매핑에서 포맷 타입을 추론한다.

    Returns:
        {한글alias: "currency"|"rate"|"count"|"text"}
    """
    from src.utils.sqlglot_analyzer import extract_select_alias_map
    alias_map = extract_select_alias_map(sql, dialect)

    formats: dict[str, str] = {}
    for alias, orig_col in alias_map.items():
        if orig_col is None:
            # 집계 함수 (COUNT(*) AS 건수 등) — 한글 alias에서 추론
            formats[alias] = _infer_from_alias(alias)
        else:
            formats[alias] = _infer_from_column(orig_col)
    return formats
```

**판별 우선순위**: 원본 컬럼 접미사 > 한글 alias 키워드 > "text" 폴백

**접미사 판별 — `_infer_from_column()`**:

```python
def _infer_from_column(col: str) -> str:
    upper = col.upper()
    if any(upper.endswith(s) for s in _CURRENCY_SUFFIXES):
        return "currency"
    if any(upper.endswith(s) for s in _RATE_SUFFIXES):
        return "rate"
    if any(upper.endswith(s) for s in _COUNT_SUFFIXES):
        return "count"
    return "text"
```

**한글 alias 폴백 — `_infer_from_alias()`**:

```python
def _infer_from_alias(alias: str) -> str:
    if any(k in alias for k in ("건수", "수량", "횟수", "인원")):
        return "count"
    if any(k in alias for k in ("금액", "잔액", "합계", "원금", "이자", "실적")):
        return "currency"
    if any(k in alias for k in ("율", "비율", "비중")):
        return "rate"
    return "text"
```

**엣지 케이스 처리**:

| 케이스 | 판별 결과 | 안전성 |
|--------|-----------|--------|
| `SUM(A.LN_BAL_AMT) AS 잔액합계` | orig_col=None → alias "잔액합계" → currency | O |
| `A.COL1 - A.COL2 AS 차액` | orig_col=None → alias "차액" → text(폴백) | 안전 (천 단위 구분자만) |
| `CASE WHEN ... END AS 구분` | orig_col=None → alias "구분" → text | O |
| `A.OVDU_RTO AS 연체비율` | orig_col="OVDU_RTO" → `_RTO` → rate | O |
| 접미사 미매칭 숫자 컬럼 | text → 천 단위 구분자만 적용 | 안전 (잘못된 변환 없음) |

**설계 결정**: 판별 불가 시 **원본 숫자를 천 단위 구분자만 적용**하여 안전하게 표시.
잘못된 단위 변환(건수를 금액으로 변환 등)보다 나은 선택.

---

### 4-2. 숫자 포맷팅 함수

**위치**: `src/services/response_formatter.py`

```python
def format_currency(value: int | float) -> str:
    """금액을 한국어 단위로 변환한다."""
    abs_val = abs(value)
    sign = "-" if value < 0 else ""
    if abs_val >= 1_000_000_000_000:  # 1조
        jo = abs_val // 1_000_000_000_000
        eok = (abs_val % 1_000_000_000_000) // 100_000_000
        return f"{sign}{jo}조 {eok:,}억원" if eok else f"{sign}{jo}조원"
    if abs_val >= 100_000_000:  # 1억
        eok = abs_val // 100_000_000
        man = (abs_val % 100_000_000) // 10_000
        return f"{sign}{eok:,}억 {man:,}만원" if man else f"{sign}{eok:,}억원"
    if abs_val >= 10_000:  # 1만
        man = abs_val // 10_000
        return f"{sign}{man:,}만원"
    return f"{sign}{abs_val:,}원"


def format_rate(value: float) -> str:
    """비율을 퍼센트로 포맷팅한다."""
    return f"{value:.1f}%"


def format_count(value: int | float) -> str:
    """건수를 천 단위 구분자 + '건'으로 포맷팅한다."""
    return f"{int(value):,}건"
```

**기존 `rows_to_markdown_table`과의 관계**:
- 기존 함수는 유지 (테스트/다른 경로에서 참조 가능)
- 신규 `format_report_table`은 기존 함수를 **확장**하여 셀 단위 포맷팅 적용

---

### 4-3. 테이블 포맷팅 — `format_report_table()`

**위치**: `src/services/response_formatter.py`

```python
def format_report_table(
    columns: list[str],
    rows: list[dict[str, Any]],
    column_formats: dict[str, str],
    max_rows: int = 100,
) -> str:
    """dict 행 목록을 포맷팅된 마크다운 테이블로 변환한다.

    column_formats의 타입에 따라 셀 값을 한국어 단위로 변환한다.
    판별 불가 숫자는 천 단위 구분자만 적용한다.
    """
    if not columns or not rows:
        return "(조회 결과 없음)"

    def _fmt_cell(col: str, value: Any) -> str:
        if value is None:
            return ""
        fmt = column_formats.get(col, "text")
        if fmt == "currency" and isinstance(value, (int, float)):
            return format_currency(value)
        if fmt == "rate" and isinstance(value, (int, float)):
            return format_rate(value)
        if fmt == "count" and isinstance(value, (int, float)):
            return format_count(value)
        # text 또는 미판별 숫자
        if isinstance(value, float):
            return f"{int(value):,}" if value == int(value) else f"{value:,.2f}"
        if isinstance(value, int):
            return f"{value:,}"
        return str(value)

    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join("---" for _ in columns) + " |"
    body_lines: list[str] = []
    for row in rows[:max_rows]:
        cells = [_fmt_cell(col, row.get(col, "")) for col in columns]
        body_lines.append("| " + " | ".join(cells) + " |")

    table = "\n".join([header, separator, *body_lines])
    total = len(rows)
    if total > max_rows:
        table += f"\n\n(총 {total:,}건 중 상위 {max_rows}건 표시)"
    else:
        table += f"\n\n(총 {total:,}건)"
    return table
```

---

### 4-4. 핵심 수치 요약 — `build_summary_line()`

**위치**: `src/services/response_formatter.py`

```python
def build_summary_line(
    columns: list[str],
    rows: list[dict[str, Any]],
    column_formats: dict[str, str],
) -> str:
    """핵심 수치를 1~2줄로 요약한다."""
    row_count = len(rows)
    if row_count == 0:
        return ""

    # 금액/건수 컬럼 중 첫 번째를 핵심 지표로 선택
    metric_col = next(
        (c for c in columns if column_formats.get(c) in ("currency", "count")),
        None,
    )
    # 첫 번째 text 컬럼을 라벨로 사용
    label_col = next(
        (c for c in columns if column_formats.get(c) == "text"),
        None,
    )

    if row_count == 1 and metric_col:
        val = rows[0].get(metric_col)
        formatted = _format_value(val, column_formats.get(metric_col, "text"))
        return f"{metric_col}은(는) {formatted}입니다."

    if row_count > 1 and metric_col:
        values = [
            (r, r.get(metric_col, 0))
            for r in rows
            if r.get(metric_col) is not None
        ]
        if values:
            top_row, top_val = max(values, key=lambda x: x[1])
            formatted = _format_value(top_val, column_formats.get(metric_col, "text"))
            label = top_row.get(label_col, "") if label_col else ""
            if label:
                return (
                    f"총 {row_count:,}건 조회되었으며, "
                    f"{label}이(가) {formatted}로 가장 높습니다."
                )

    return f"총 {row_count:,}건이 조회되었습니다."
```

**analysis_result 분기**: `state.analysis_result.summary`가 존재하면 `build_summary_line` 대신 해당 요약을 사용한다. DATA_ANALYSIS 경로에서 analyzer가 이미 인사이트를 생성했으므로 rule-based 요약보다 우선.

---

### 4-5. 코드값 변환 — `apply_code_mappings()`

**위치**: `src/services/response_formatter.py`

SQL Generator 프롬프트 규칙 10번이 "코드값 컬럼은 반드시 대응하는 명칭 컬럼을 함께 포함"하도록 지시하므로, 대부분의 경우 결과에 이미 한글 명칭이 포함된다. 이 함수는 **명칭 컬럼 없이 코드값만 있는 경우**의 fallback이다.

```python
def apply_code_mappings(
    rows: list[dict[str, Any]],
    code_map: dict[str, CodeMeta],
    sql: str,
    dialect: str | None = None,
) -> list[dict[str, Any]]:
    """결과 행의 코드값을 한글 명칭으로 변환한다 (fallback).

    SQL Generator가 명칭 컬럼을 함께 포함하도록 지시하고 있으므로
    이 함수는 명칭 컬럼 누락 시의 안전장치이다.
    """
    from src.utils.sqlglot_analyzer import extract_select_alias_map
    alias_map = extract_select_alias_map(sql, dialect)

    # alias → 원본컬럼, 원본컬럼이 code_map에 있으면 변환 대상
    col_to_codes: dict[str, dict[str, str]] = {}
    for alias, orig in alias_map.items():
        if orig and orig in code_map and code_map[orig].codes:
            col_to_codes[alias] = code_map[orig].codes

    if not col_to_codes:
        return rows

    converted = []
    for row in rows:
        new_row = dict(row)
        for col, codes in col_to_codes.items():
            if col in new_row and str(new_row[col]) in codes:
                new_row[col] = codes[str(new_row[col])]
        converted.append(new_row)
    return converted
```

---

### 4-6. 5단계 조회 과정 요약 — `build_process_summary()`

**위치**: `src/services/process_summary_builder.py` (신규)

`format_trace_summary`(TraceEntry 단순 나열)를 대체한다. State 필드에서 직접 조합하여
사용자가 "AI가 어떤 과정을 거쳐 이 결과를 만들었는지" 이해할 수 있는 구조화된 텍스트를 생성한다.

기존 `build_auto_resolved_notice`의 INFER 안내를 4단계(AI판단)에 통합하여
정보 중복을 제거한다.

```python
"""5단계 조회 과정 요약 빌더.

State 필드에서 조회 과정 정보를 추출하여 구조화된 마크다운 텍스트를 생성한다.
formatter 노드에서 <details> 접기 태그 내부에 삽입된다.

5단계 구조:
    1. 질의 분류 — intent, is_continuation
    2. 질의 해석 — normalized_query (measures, filters, time, entities 등)
    3. 활용 정보 — explored_tables, explored_use_cases, explored_biz_manuals, explored_biz_terms
    4. AI 판단  — resolved_signals(INFER), pending_assumptions
    5. 검증 결과 — validation_summary, sql_result.row_count
"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.agents.state.state import PipelineState


def build_process_summary(state: PipelineState) -> str:
    """5단계 조회 과정 요약을 State 필드에서 구성한다."""
    sections: list[str] = []

    sections.append(_build_intent_section(state))
    sections.append(_build_interpretation_section(state))
    sections.append(_build_context_section(state))

    ai_section = _build_ai_decision_section(state)
    if ai_section:
        sections.append(ai_section)

    sections.append(_build_validation_section(state))

    return "\n\n".join(s for s in sections if s)
```

#### 각 섹션 데이터 소스 및 출력 형식

**1단계: 질의 분류**

| State 필드 | 용도 |
|-----------|------|
| `intent` | 질의 유형 (DATA_EXTRACTION / DATA_ANALYSIS) |
| `is_continuation` | 신규 질의 / 이전 대화 연속 |
| `continue_context` | 연속 시 맥락 힌트 |

```python
def _build_intent_section(state: PipelineState) -> str:
    _INTENT_LABELS = {
        "data_extraction": "데이터 추출",
        "data_analysis": "데이터 분석",
    }
    intent_label = _INTENT_LABELS.get(state.intent.value, state.intent.value)
    cont = " (이전 대화 연속)" if state.is_continuation else " (신규 질의)"
    return f"**1. 질의 분류**\n{intent_label} 요청{cont}"
```

**2단계: 질의 해석**

| State 필드 | 용도 |
|-----------|------|
| `normalized_query.measures` | 측정값 (건수, 금액 등) |
| `normalized_query.filters` | 필터 조건 |
| `normalized_query.time` | 기간 |
| `normalized_query.entities` | 대상 엔티티 |
| `normalized_query.dimensions` | 분석 축 |
| `normalized_query.output_hint` | 출력 형식 힌트 |

```python
def _build_interpretation_section(state: PipelineState) -> str:
    nq = state.normalized_query
    if not nq:
        return "**2. 질의 해석**\n(정규화 결과 없음)"

    lines = ["**2. 질의 해석**"]
    if nq.measures:
        terms = [m.term for m in nq.measures if hasattr(m, "term")]
        if terms:
            lines.append(f"- 측정값: {', '.join(terms)}")
    if nq.filters:
        items = [f.target for f in nq.filters if f.target]
        if items:
            lines.append(f"- 조건: {', '.join(items)}")
    if nq.time and nq.time.base_period:
        time_label = nq.time.base_period.label or nq.time.base_period.resolve or str(nq.time.type)
        lines.append(f"- 기간: {time_label}")
    if nq.entities:
        lines.append(f"- 대상: {', '.join(e.term for e in nq.entities)}")
    if nq.dimensions:
        lines.append(f"- 그룹: {', '.join(d.term for d in nq.dimensions)}")
    return "\n".join(lines)
```

**3단계: 활용 정보**

| State 필드 | 용도 |
|-----------|------|
| `reason.explored_tables` (SELECTED) | 사용한 테이블 + 한글명 |
| `reason.explored_use_cases` (relevant) | 참조한 유사 SQL 건수 |
| `reason.explored_biz_manuals` (SELECTED) | 참조한 업무 매뉴얼 건수 |
| `reason.explored_biz_terms` (SELECTED) | 참조한 비즈 용어 건수 |
| `reason.knowledge_items` (CONFIRMED) | 확인된 지식 건수 |

```python
def _build_context_section(state: PipelineState) -> str:
    reason = state.reason
    lines = ["**3. 활용 정보**"]

    # 사용 테이블
    selected = [
        t for t in reason.explored_tables
        if t.selection_status != SelectionStatus.REJECTED
    ]
    if selected:
        table_descs = []
        for t in selected:
            label = t.alt_name or t.description or t.table_name
            table_descs.append(f"{label}({t.table_name})")
        lines.append(f"- 테이블: {', '.join(table_descs)}")

    # 유사 SQL
    relevant_ucs = [uc for uc in reason.explored_use_cases if uc.relevant]
    if relevant_ucs:
        lines.append(f"- 유사 SQL {len(relevant_ucs)}건 참조")

    # 업무 매뉴얼
    selected_manuals = [
        m for m in reason.explored_biz_manuals
        if m.selection_status == SelectionStatus.SELECTED
    ]
    if selected_manuals:
        lines.append(f"- 업무 매뉴얼 {len(selected_manuals)}건 확인")

    # 비즈 용어
    selected_terms = [
        bt for bt in reason.explored_biz_terms
        if bt.selection_status == SelectionStatus.SELECTED
    ]
    if selected_terms:
        term_names = [bt.term for bt in selected_terms]
        lines.append(f"- 용어 확인: {', '.join(term_names)}")

    return "\n".join(lines)
```

**4단계: AI 판단**

`build_auto_resolved_notice`를 대체하여 INFER 시그널과 assumptions를 통합 표시한다.
내용이 없으면 섹션 자체를 생략한다.

| State 필드 | 용도 |
|-----------|------|
| `resolved_signals` (decision="INFER", 현재 턴) | 자동 추론 결과 |
| `reason.pending_assumptions` | SQL 생성 시 해석적 선택 |

```python
def _build_ai_decision_section(state: PipelineState) -> str:
    lines: list[str] = []

    # INFER 시그널 (현재 턴)
    tid = state.turn_id
    infers = [
        s for s in state.resolved_signals
        if s.decision == "INFER"
        and s.turn_id is not None
        and s.turn_id == tid
    ]
    for s in infers:
        lines.append(f"- {s.question} → {s.inferred_value}")

    # assumptions
    for a in state.reason.pending_assumptions:
        lines.append(f"- {a}")

    if not lines:
        return ""  # 섹션 생략

    lines.append("(다른 기준을 원하시면 말씀해 주세요)")
    return "**4. AI 판단**\n" + "\n".join(lines)
```

**5단계: 검증 결과**

| State 필드 | 용도 |
|-----------|------|
| `reason.validation_summary` | LLM 검증 총평 |
| `sql_result.row_count` | 조회 건수 |
| `reason.validation_checks` | 개별 체크 pass/fail 수 |

```python
def _build_validation_section(state: PipelineState) -> str:
    reason = state.reason
    lines = ["**5. 검증 결과**"]

    # 검증 총평
    if reason.validation_summary:
        lines.append(reason.validation_summary)
    else:
        # validation_checks에서 pass/fail 집계
        checks = reason.validation_checks
        if checks:
            passed = sum(1 for v in checks.values() if isinstance(v, dict) and v.get("pass"))
            total = sum(1 for v in checks.values() if isinstance(v, dict))
            lines.append(f"SQL 검증: {total}개 항목 중 {passed}개 통과")

    # 조회 건수
    if state.sql_result and state.sql_result.row_count > 0:
        lines.append(f"{state.sql_result.row_count:,}건 조회 완료.")
    elif state.sql_result:
        lines.append("조회 결과 0건.")

    return "\n".join(lines)
```

---

### 4-7. formatter.py 노드 전환

**위치**: `src/agents/nodes/present/formatter.py`

```python
async def format_response_node(state: PipelineState) -> dict:
    """결과를 사용자 친화적 형태로 포맷팅한다."""
    logger.info("결과 포맷팅 시작")

    # ── 가드: simple_responder가 이미 응답 완성한 경우 ──
    if state.formatted_response and not state.sql_result.rows:
        logger.info("경량 응답 통과 — 포맷팅 스킵")
        # 5단계 요약만 추가 (비데이터 의도에는 불필요하므로 생략)
        return {
            "trace_log": add_trace(state, "포맷팅", "경량 응답 통과"),
        }

    # ── 1. 컬럼 타입 판별 ──
    column_formats = detect_column_formats(
        state.reason.validated_sql or "",
    )

    # ── 2. 코드값 변환 (fallback) ──
    rows = apply_code_mappings(
        state.sql_result.rows,
        state.reason.explored_codes,
        state.reason.validated_sql or "",
    )

    # ── 3. 마크다운 테이블 ──
    table_text = format_report_table(
        state.sql_result.columns,
        rows,
        column_formats,
    )

    # ── 4. 핵심 수치 요약 ──
    if state.analysis_result and state.analysis_result.summary:
        summary_line = state.analysis_result.summary
    else:
        summary_line = build_summary_line(
            state.sql_result.columns,
            rows,
            column_formats,
        )

    formatted = f"{table_text}\n\n{summary_line}" if summary_line else table_text

    # ── 5. force-generate 경고 (기존 유지) ──
    if state.reason.is_force_generated:
        formatted = (
            "**참고**: 확인된 정보가 충분하지 않아 "
            "일부 추론을 포함하여 조회하였습니다. "
            "결과가 예상과 다를 경우 구체적으로 요청해 주세요."
            f"\n\n{formatted}"
        )

    # ── 6. 조회 과정 요약 (5단계) ──
    process_summary = build_process_summary(state)
    if process_summary:
        formatted += (
            "\n\n<details>\n"
            "<summary>조회 과정 요약</summary>\n\n"
            f"{process_summary}\n"
            "</details>"
        )

    logger.info("결과 포맷팅 완료", response_length=len(formatted))

    # ── 트래킹 ──
    try:
        await dispatch_tracking_event(REASONING_STEP, {
            "node": "format_response",
            "phase": "present",
            "step_type": "rule_based",  # 변경: llm_decision → rule_based
            "round": 0,
            "hypothesis_id": "",
            "inputs": {
                "user_input": state.preprocessed_input,
                "sql_result": f"{state.sql_result.row_count if state.sql_result else 0}건",
            },
            "output": {
                "format": "rule-based 마크다운 보고서",
                "is_force_generated": state.reason.is_force_generated,
                "process_summary_appended": bool(process_summary),
            },
            "routing": {
                "next_node": "(완료)",
                "reason": "최종 응답 생성 완료",
            },
        })
    except Exception as e:
        logger.warning("포맷팅 트래킹 이벤트 전송 실패", error=str(e))

    return {
        "formatted_response": formatted,
        "status": QueryStatus.FORMATTED,
        "trace_log": add_trace(
            state, "포맷팅",
            "보고서 형태로 결과 정리 완료",
        ),
    }
```

**제거되는 import/호출**:
- `FORMATTER_SYSTEM`, `FORMATTER_USER` (system_prompts.py)
- `format_response` (response_formatter.py의 LLM 호출 함수)
- `build_auto_resolved_notice` (clarification_context.py — 5단계 AI판단으로 통합)
- `format_trace_summary` (trace.py — 5단계 요약으로 대체)
- `_build_code_mappings`, `_serialize_code_map` (formatter.py 내부 함수)
- `extract_select_alias_map` import (formatter.py에서 제거, response_formatter.py로 이동)

**추가되는 import**:
- `detect_column_formats`, `format_report_table`, `build_summary_line`, `apply_code_mappings` (response_formatter.py)
- `build_process_summary` (process_summary_builder.py)

---

## 5. 삭제/정리 대상

### 5-1. 삭제

| 대상 | 이유 |
|------|------|
| `resources/prompts/present/formatter_system.txt` | LLM 호출 제거 |
| `resources/prompts/present/formatter_user.txt` | LLM 호출 제거 |
| `src/agents/nodes/system_prompts.py`의 `FORMATTER_SYSTEM`, `FORMATTER_USER` 변수 | 프롬프트 파일 삭제에 따른 정리 |
| `src/services/response_formatter.py`의 `format_response()` 함수 | LLM 호출 함수 |
| `src/agents/nodes/present/formatter.py`의 `_build_code_mappings()`, `_serialize_code_map()` | `apply_code_mappings`로 대체 |

### 5-2. 유지 (당분간)

| 대상 | 유지 이유 |
|------|-----------|
| `trace_log` 필드 (PipelineState) | insight_builder가 timestamp로 `total_elapsed`, `step_timings` 계산에 사용 |
| `add_trace()` (trace.py) | trace_log 기록용 — 각 노드에서 호출 (6곳) |
| `TraceEntry` 모델 | trace_log, PipelineResult, API 반환 |
| `format_trace_summary()` (trace.py) | 함수 자체 유지, 호출부만 제거 (외부 참조 방어) |
| `PipelineResult.trace_log` | main.py API `include_trace` 옵션, runner.py 메타데이터 |
| `build_auto_resolved_notice()` | 함수 자체 유지, formatter 호출만 제거. 다른 곳에서 사용 가능 |
| `rows_to_markdown_table()` | 다른 서비스에서 참조 가능 |
| `format_result_for_prompt()` | 다른 프롬프트 주입에 사용 가능 |

### 5-3. 향후 제거 계획

trace_log 완전 제거는 별도 작업으로 진행:
1. insight_builder의 `_calc_total_elapsed`, `_build_step_timings`를 EvaluationTrace 기반으로 전환
2. PipelineResult.trace_log → 제거 또는 5단계 요약 텍스트로 대체
3. 각 노드의 `add_trace` 호출 제거
4. TraceEntry, trace_log 필드 제거

---

## 6. 영향 분석

### 6-1. simple_responder → format_response 경로

`pipeline.py:559`에 `simple_responder → format_response` 엣지가 존재한다.
simple_responder는 이미 `formatted_response`를 완성하여 반환하므로,
format_response_node의 가드 절에서 `state.formatted_response`가 존재하고
`state.sql_result.rows`가 비어있으면 포맷팅을 스킵한다.

기존 LLM 호출에서도 simple_responder가 설정한 `formatted_response`를 LLM이 덮어쓰고 있었으므로,
이 가드는 기존 동작을 개선하는 것이다.

### 6-2. DATA_ANALYSIS 경로

`pipeline.py:558`에 `analyze_data → format_response` 엣지가 존재한다.
analyzer는 `analysis_result` (summary, insights, statistics)를 생성하고 format_response로 넘긴다.

현재 formatter LLM은 analysis_result를 무시하고 sql_result만 포맷팅하는 문제가 있었다.
rule-based 전환에서 analysis_result.summary가 있으면 이를 요약으로 사용하는 분기를 추가하여
오히려 기존보다 개선된다.

### 6-3. WebSocket 스트리밍

현재 formatter의 LLM 응답이 WebSocket stream으로 전송된다.
rule-based 전환 후에는 완성된 문자열이 한 번에 반환되므로,
WebSocket 전송 방식 변경이 필요할 수 있다 (stream → 일괄 전송).

단, 이는 **논의 3 (WebSocket 스키마 정의)**에서 다루는 것이 적절하다.
당장은 기존 `formatted_response` 문자열을 그대로 전송하면 동작한다.

### 6-4. 테스트 영향

- `tests/` 내 formatter 관련 테스트가 있다면 LLM mock 제거 필요
- rule-based 함수들은 단위 테스트 작성이 쉬움 (입출력 결정론적)

---

## 7. 변경 파일 요약

| # | 파일 | 변경 유형 | 내용 |
|---|------|-----------|------|
| 1 | `src/services/response_formatter.py` | 수정 | `format_response` 제거, `detect_column_formats`/`format_report_table`/`build_summary_line`/`apply_code_mappings`/`format_currency`/`format_rate`/`format_count` 추가 |
| 2 | `src/agents/nodes/present/formatter.py` | 수정 | LLM → rule-based 전환, simple_responder 가드, import 정리 |
| 3 | `src/services/process_summary_builder.py` | **신규** | 5단계 조회 과정 요약 빌더 |
| 4 | `src/agents/nodes/system_prompts.py` | 수정 | `FORMATTER_SYSTEM`, `FORMATTER_USER` 제거 |
| 5 | `resources/prompts/present/formatter_system.txt` | **삭제** | - |
| 6 | `resources/prompts/present/formatter_user.txt` | **삭제** | - |

---

## 8. 구현 순서

1. `response_formatter.py`에 rule-based 포맷팅 함수들 추가 (기존 코드와 병존)
2. `process_summary_builder.py` 신규 작성
3. `formatter.py` 전환 (LLM → rule-based), import 정리
4. `system_prompts.py`에서 FORMATTER 변수 제거
5. 프롬프트 파일 삭제
6. 수동 검증 (파이프라인 실행 → 출력 확인)
7. `format_response()` LLM 함수 제거 (다른 참조 없음 확인 후)

---

## 9. 검증 방법

1. **단위 테스트**: `format_currency`, `format_rate`, `format_count` — 경계값, 음수, 0
2. **단위 테스트**: `detect_column_formats` — 접미사 매칭, 한글 폴백, 미판별 폴백
3. **단위 테스트**: `build_process_summary` — 각 섹션 생성, 빈 State, 부분 State
4. **통합 검증**: 파이프라인 실행 → formatted_response 출력 확인
5. **경로별 검증**:
   - DATA_EXTRACTION: 일반 조회 → 테이블 + 요약 + 5단계 확인
   - DATA_ANALYSIS: 분석 요청 → analysis_result.summary 반영 확인
   - CASUAL_TALK: simple_responder → formatter 스킵 확인
   - 실패 케이스: sql_result 없음 → 에러 메시지 확인

---

## 10. 관련 문서

| 문서 | 내용 |
|------|------|
| `20260407-output-structure-gap-analysis.md` | 정보 흐름 단절 전체 분석 (상위 문서) |
| `20260407-formatter-smart-improvement.md` | formatter 고도화 방안 (이 문서가 대체) |
| `20260407-sql-assumptions-design.md` | sql_generator assumptions 설계 (논의 1, 별도 구현) |
| `project_output_structure_discussion.md` | 메모리: 3건 논의 진행 현황 |

---

## 11. 재검토 결과 (2차 비판적 검토)

> **검토일**: 2026-04-07
> **검토 관점**: 과도한 비판 여부, 아키텍처 일관성, 불필요한 필드 증식, NL-to-SQL 정확도 영향

### 11-1. 코드 레벨 오류 수정 — 3건 (본문 반영 완료)

| 위치 | 수정 전 | 수정 후 | 근거 |
|------|---------|---------|------|
| 4-6절 `_build_interpretation_section` filters | `f.term` | `f.target` | `FilterSlot`에 `term` 필드 없음, `target: str` 사용 |
| 4-6절 `_build_interpretation_section` entities | `', '.join(nq.entities)` | `', '.join(e.term for e in nq.entities)` | `list[EntitySlot]`이므로 `.term` 접근 필요 |
| 4-6절 `_build_interpretation_section` dimensions | `', '.join(nq.dimensions)` | `', '.join(d.term for d in nq.dimensions)` | `list[DimensionSlot]`이므로 `.term` 접근 필요 |

추가: `nq.time`은 `TimeSlot` 객체이므로 `nq.time.base_period.label` 등으로 접근하도록 수정.

### 11-2. 과도한 비판 포함 여부 — 없음

설계 전반이 실용적이고 절제되어 있다. 구체적으로:
- LLM 호출 제거 판단은 1-1절 분석표에 의해 정확히 근거됨
- 신규 파일 1개(`process_summary_builder.py`)만 추가, 나머지는 기존 파일 수정
- **새로운 State 필드를 하나도 추가하지 않음** — 기존 필드에서만 읽기
- `build_auto_resolved_notice` 통합 판단도 정보 중복 제거 관점에서 합리적

### 11-3. 아키텍처 일관성 — 유지됨

| 관점 | 평가 |
|------|------|
| 노드 패턴 | formatter 노드가 서비스 함수를 호출하는 기존 패턴 유지 (노드=오케스트레이션, 서비스=로직) |
| State 접근 패턴 | 읽기 전용 — formatter가 State를 변경하지 않는 기존 원칙 유지 |
| 반환 패턴 | `{"formatted_response", "status", "trace_log"}` dict 반환 — 기존과 동일 |
| 트래킹 패턴 | `dispatch_tracking_event` 호출 유지 (`step_type`만 `rule_based`로 변경) |

**주의점**: 4-7절 가드 조건 `state.formatted_response and not state.sql_result.rows`는 현재 구현에 없는 분기.
에러 복구 경로에서 `formatted_response`가 세팅되었지만 `sql_result.rows`가 빈 경우의 edge case를 테스트에 포함할 것.

### 11-4. 불필요한 필드 증식 — 없음

| 구분 | 제거 | 추가 |
|------|------|------|
| 프롬프트 파일 | 2개 삭제 | 0 |
| LLM 호출 함수 | `format_response()` 제거 | 0 |
| 내부 헬퍼 | `_build_code_mappings`, `_serialize_code_map` 제거 | rule-based 함수 추가 (동일 서비스 내) |
| State 필드 | 0 | 0 (신규 없음) |

**Dead code 정리 결정**: 5-2절 "유지 (당분간)" 중 `format_trace_summary()`와 `build_auto_resolved_notice()`는
호출부가 없는 dead code가 되므로 개발 지침("죽은 코드 즉시 제거")에 따라 **함께 제거**한다.
단, `format_trace_summary`는 외부 참조가 없음을 확인 후 제거. `build_auto_resolved_notice`도 동일.

### 11-5. NL-to-SQL 정확도 영향 — 없음

formatter는 파이프라인 최종 단계(present phase)에서 실행되며, SQL 생성(generate)과 검증(validate) 이후에 동작한다.
- formatter의 출력(`formatted_response`)은 SQL 생성 프로세스에 피드백되지 않음
- 5단계 요약의 "AI 판단" 섹션 안내("다른 기준을 원하시면 말씀해 주세요")는 기존 `build_auto_resolved_notice`와 동일한 패턴

### 11-6. 추가 개선 사항 (구현에 포함)

**1. `build_summary_line`의 "가장 높습니다" 표현 개선**

비용/부채 컬럼에서 "가장 높습니다"가 긍정적 뉘앙스로 오해될 수 있다.
→ "가장 큽니다"로 중립적 표현 사용:

```python
# 변경 전
return f"{label}이(가) {formatted}로 가장 높습니다."
# 변경 후
return f"{label}이(가) {formatted}로 가장 큽니다."
```

**2. `format_currency`의 소수점 금액 방어**

정보계 DB 금액 컬럼은 대부분 정수 원 단위이지만, float 입력 시 안전하게 반올림:

```python
def format_currency(value: int | float) -> str:
    """금액을 한국어 단위로 변환한다."""
    abs_val = abs(value)
    sign = "-" if value < 0 else ""
    # 소수점 방어: 원 단위 반올림
    abs_val = round(abs_val)
    ...
```

**3. dead code 즉시 제거**

5-2절의 "유지 (당분간)" 정책을 변경:
- `format_trace_summary()` — 외부 참조 없으면 제거
- `build_auto_resolved_notice()` — formatter 호출 제거와 함께 제거
- `_build_code_mappings()`, `_serialize_code_map()` — 즉시 제거 (5-1절에 이미 포함)
