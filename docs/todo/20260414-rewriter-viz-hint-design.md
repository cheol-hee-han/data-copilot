# Rewriter 기반 viz_hint 설계

작성자: 한철희 / 작성일: 2026-04-14
관련 문서: [20260414-visualization-coverage-expansion.md](20260414-visualization-coverage-expansion.md) §11-1 결정

## 1. 배경

현재 파이프라인은 `intent_classifier_query_rewriter`가 사용자 질의에서 시각화 지시어를 제거하여 `preprocessed_input`을 생성하고, 이를 SQL Generator가 사용한다. 결과적으로 SQL Generator는 사용자가 어떤 차트를 원했는지, 그에 따라 어떤 SQL 구조가 필요한지 알 수 없다.

**문제 사례**

| 사용자 원문 | preprocessed_input (SQL Gen 입력) | 결과 |
|---|---|---|
| "이번 달 손익 구성 워터폴로 보여줘" | "이번 달 손익 구성" | `유형` 컬럼 없어 waterfall 렌더링 실패 |
| "고객 등급별 예금 비중을 파이차트로" | "고객 등급별 예금 비중" | TOP N + '기타' 집계 없어 비중 왜곡 |
| "지점별 여신-수신 산점도로" | "지점별 여신-수신" | 측정값 1개만 나와 scatter 불가 |
| "요일×시간대 거래량 히트맵으로" | "요일별 시간대별 거래량" | 3컬럼 pivot 구조 확보 안 됨 |

## 2. 설계 원칙

### 2.1 역할 분리

| 결정 | 담당 | 근거 |
|---|---|---|
| **SQL 구조 요건** | rewriter (사전) | 사용자가 명시한 차트 → 결정론적 도출 |
| **최종 차트 선택** | judge_visualization (사후) | 실제 데이터 형태(행수·컬럼·분포) 반영 |

rewriter는 **"사용자가 뭘 원했나"** 를 캡처하고, judge는 **"실제로 뭘 그리는 게 적절한가"** 를 결정한다. rewriter가 데이터를 보지 않고 최종 차트를 결정하면 3행짜리 결과에 pie를 강제하는 오류가 발생한다.

### 2.2 rewritten_query 의미 보존

`rewritten_query`(→ `preprocessed_input`)는 **자연스러운 사용자 질의 형태**로 유지한다. SQL pseudocode ("항목별 시작/증가/감소/최종 유형으로 구분")를 본문에 삽입하지 않는다. 구조 힌트는 별도 필드 `sql_structure_hint`로 분리한다.

### 2.3 3단계 신호 강도별 동작

| 단계 | 신호 | rewriter 동작 |
|---|---|---|
| A. 명시적 차트명 ("파이차트로", "워터폴로") | 강 | viz 필드 채움 (`user_requested_chart` + `sql_structure_hint`) |
| B. 의미적 암시만 ("손익 구성", "비중") | 중 | viz 필드 비움, rewritten_query에 의미 보존만 |
| C. 일반 시각화 ("시각화해서", "차트로") | 약 | viz 필드 비움, 지시어만 제거 |

**핵심 원칙**: rewriter는 사용자가 명시한 만큼만 구조를 캡처한다. 추측하지 않는다.

## 3. 현재 아키텍처 관례 (조사 결과)

### 3.1 명명 규칙
- `PipelineState` 필드: snake_case (`preprocessed_input`, `analysis_query`, `normalized_query`)
- 서브 모델: PascalCase + Slot 접미사 (`OutputHintSlot`, `IntentSlot`) — NormalizedQuery 내부 구조에 한함
- 최상위 구조 모델: `NormalizedQuery`, `StructuralHints` (Slot 미사용)
- Enum 우선: `IntentType`, `VisualizationType`, `QueryStatus`

### 3.2 state 위치
[src/agents/state/state.py:748-765](src/agents/state/state.py#L748-L765) Interpret 계층 섹션은 flat 필드 나열:
```python
preprocessed_input: str = ""
analysis_query: str = ""
intent: IntentType | None = None
continue_context: str = ""
normalized_query: NormalizedQuery | None = None
```
`NormalizedQuery` 외 모든 Interpret 필드는 **flat** (str/Enum).

### 3.3 DB 저장 (checkpointer)
- [checkpointer.py:116-138](src/agents/graph/checkpointer.py#L116-L138) `ALLOWLIST_MODULES`에 `src.agents.state.state`, `src.agents.models.*` 이미 등록
- flat 필드(str, Enum)는 msgpack 기본 지원
- `checkpoint_blobs` 테이블은 채널(필드명)별 바이너리 저장

### 3.4 트레이싱
- [intent_classifier.py:77-102](src/agents/nodes/interpret/intent_classifier.py#L77-L102) `_build_trace()` — `add_trace(state, 노드명, 메시지)` 호출
- [intent_classifier.py:317-350](src/agents/nodes/interpret/intent_classifier.py#L317-L350) `dispatch_tracking_event(REASONING_STEP, {...})` — `inputs`/`output`/`routing` 구조
- `record_prompt_variables` — LLM 출력 파싱 직후 프롬프트 변수 기록 (선택)

### 3.5 AI 추론 사용자 안내 (resolved_signals)
[intent_classifier.py:165-179](src/agents/nodes/interpret/intent_classifier.py#L165-L179), [sql_validator.py:696-713](src/agents/nodes/reason/sql_validator.py#L696-L713) 참조:
- `AmbiguitySignal(decision="INFER", ambiguity_type, reasoning, inferred_value)` 생성
- `state.resolved_signals`에 축적 → 최종 응답에 `[AI 추론]` 섹션으로 노출

### 3.6 턴 리셋 / multi-turn / recovery
- [state.py:816-857](src/agents/state/state.py#L816-L857) `turn_reset_updates()`에 턴 경계 리셋 대상 명시
- recovery_agent는 reason 계층만 리셋 ([recovery_agent.py:75-104](src/agents/nodes/reason/recovery_agent.py#L75-L104))
- multi-turn continue: intent_classifier가 재실행되며 Interpret 계층 필드 자연 갱신

## 4. 설계

### 4.1 필드 정의 — flat 2개

2개 필드만 있으므로 BaseModel 서브 모델을 별도로 만들지 않고, PipelineState에 flat으로 추가한다 (§3.2 기존 관례 준수).

```python
# state.py:748-765 Interpret 계층 섹션에 추가
# W: intent_classifier (rewriter가 시각화 지시 캡처)
# R: sql_generator, judge_visualization
user_requested_chart: VisualizationType | None = None
sql_structure_hint: str = ""
```

`normalized_query` 바로 앞에 배치한다 (rewriter 출력 필드 그룹).

**근거**: `NormalizedQuery`만 8-slot 복합 구조라 BaseModel로 묶여있고, 나머지 Interpret 필드는 전부 flat. 2개 필드를 BaseModel로 감쌀 오버헤드(신규 파일, msgpack allowlist, 접근 경로 추가)가 실익을 넘음. 추후 필드가 3~4개로 늘면 BaseModel로 리팩토링.

### 4.2 턴 리셋 등록

[state.py:816-857](src/agents/state/state.py#L816-L857) `turn_reset_updates()` 메서드에 추가:

```python
"user_requested_chart": None,
"sql_structure_hint": "",
```

기존 `normalized_query`, `analysis_query` 인접 위치에 삽입.

### 4.3 rewriter 프롬프트 출력 스키마 확장

`resources/prompts/interpret/intent_classifier_query_rewriter.txt`

**현재 출력**: `rewritten_query` (문자열)

**변경 후 출력**: JSON 객체
```json
{
  "rewritten_query": "고객 등급별 예금 비중",
  "user_requested_chart": "pie_chart",
  "sql_structure_hint": "카테고리가 많을 수 있으므로 상위 9개와 나머지를 '기타'로 묶어 집계"
}
```

**동작 규칙** (프롬프트에 명시):

1. **명시적 차트명 감지**: "파이차트", "도넛차트", "워터폴", "산점도", "히트맵", "막대차트", "꺾은선(라인)" 등을 감지하여 `user_requested_chart`에 `VisualizationType` **값 문자열**로 기록 (예: `"pie_chart"`, `"waterfall_chart"`)
2. **구조 힌트 자연어**: 특수 SQL 구조를 요구하는 차트(파이/도넛/워터폴/산점도/히트맵)에 한해 `sql_structure_hint`를 자연어 문장으로 기록. 기타 차트(bar/line/horizontal_bar/stacked_bar/grouped_bar)는 빈 문자열
3. **차트 미명시**: `user_requested_chart: null`, `sql_structure_hint: ""`
4. **의미적 암시만 있는 경우(B)**: `rewritten_query`에 의미 단어 보존 (기존 정책 유지), viz 필드는 비움
5. **복수 차트 요청**: "파이차트랑 라인차트 둘 다" 같은 복수 지정은 `user_requested_chart: null` (단일 필드라 표현 불가 → judge가 데이터 보고 자유 판단)
6. **"표로 보여줘" 요청**: `user_requested_chart: "table_only"` (VisualizationType에 값 존재), `sql_structure_hint: ""`
7. **"시각화 안 함" / "차트 말고"**: `user_requested_chart: "none"`, `sql_structure_hint: ""`

**유효한 `user_requested_chart` 값**: `VisualizationType` enum 전체 (bar_chart, line_chart, pie_chart, donut_chart, horizontal_bar, stacked_bar, grouped_bar, scatter_plot, waterfall_chart, heatmap, table_only, none). 그 외 문자열은 intent_classifier 파싱 단계에서 `null`로 폴백.

**새 예제 추가** (`intent_classifier_query_rewriter.txt` EXAMPLES 섹션에):

```
--- 예시 N: 파이차트 (구조 힌트 포함) ---
입력: 고객 등급별 예금 비중을 파이차트로 보여줘
출력:
{
  "rewritten_query": "고객 등급별 예금 비중",
  "user_requested_chart": "pie_chart",
  "sql_structure_hint": "카테고리가 많으면 상위 9개와 나머지를 '기타'로 묶어 집계"
}

--- 예시 N+1: 워터폴 ---
입력: 이번 달 손익 구성 워터폴로 보여줘
출력:
{
  "rewritten_query": "이번 달 항목별 손익",
  "user_requested_chart": "waterfall_chart",
  "sql_structure_hint": "각 항목을 시작/증가/감소/최종 유형으로 구분하는 유형 컬럼을 CASE로 생성"
}

--- 예시 N+2: 산점도 ---
입력: 지점별 여신-수신 산점도로 보여줘
출력:
{
  "rewritten_query": "지점별 여신잔액과 수신잔액",
  "user_requested_chart": "scatter_plot",
  "sql_structure_hint": "X축과 Y축에 해당하는 두 개의 수치 측정값을 함께 조회"
}

--- 예시 N+3: 히트맵 ---
입력: 요일×시간대 거래량 히트맵으로 분석해줘
출력:
{
  "rewritten_query": "요일별 시간대별 거래량",
  "user_requested_chart": "heatmap",
  "sql_structure_hint": "행 차원(요일), 열 차원(시간대), 값(거래량)의 세 컬럼으로 집계"
}

--- 예시 N+4: 표 요청 ---
입력: 이번 달 신규고객 명세 표로 보여줘
출력:
{
  "rewritten_query": "이번 달 신규고객 명세",
  "user_requested_chart": "table_only",
  "sql_structure_hint": ""
}

--- 예시 N+5: 일반 시각화 (힌트 없음) ---
입력: 이번 달 지점별 실적 시각화해서 보여줘
출력:
{
  "rewritten_query": "이번 달 지점별 실적",
  "user_requested_chart": null,
  "sql_structure_hint": ""
}

--- 예시 N+6: 의미적 암시만 (힌트 없음) ---
입력: 이번 달 손익 구성 보여줘
출력:
{
  "rewritten_query": "이번 달 손익 구성",
  "user_requested_chart": null,
  "sql_structure_hint": ""
}

--- 예시 N+7: 복수 차트 요청 (힌트 없음) ---
입력: 지점별 실적을 파이차트랑 막대차트 둘 다 보여줘
출력:
{
  "rewritten_query": "지점별 실적",
  "user_requested_chart": null,
  "sql_structure_hint": ""
}
```

### 4.4 intent_classifier 노드 수정

[src/agents/nodes/interpret/intent_classifier.py](src/agents/nodes/interpret/intent_classifier.py) rewriter 응답 파싱 로직 확장:

```python
def _parse_rewriter_response(
    raw: str,
) -> tuple[str, VisualizationType | None, str]:
    """rewriter JSON 응답에서 rewritten_query, chart, structure_hint 추출.

    JSON 파싱 실패 시 raw 전체를 rewritten_query로 fallback.
    enum 변환 실패 시 chart=None 폴백.
    """
    try:
        data = json.loads(raw.strip())
    except json.JSONDecodeError:
        logger.warning("rewriter JSON 파싱 실패, plain text 폴백")
        return raw.strip(), None, ""

    if not isinstance(data, dict):
        logger.warning("rewriter 응답 dict 아님, plain text 폴백")
        return raw.strip(), None, ""

    rewritten = data.get("rewritten_query", "").strip()
    if not rewritten:
        rewritten = raw.strip()  # 필드 누락 시 원본 사용

    chart: VisualizationType | None = None
    chart_str = data.get("user_requested_chart")
    if isinstance(chart_str, str) and chart_str:
        try:
            chart = VisualizationType(chart_str)
        except ValueError:
            logger.warning(
                "user_requested_chart 값 무효, null 처리",
                value=chart_str,
            )

    structure_hint = data.get("sql_structure_hint") or ""
    if not isinstance(structure_hint, str):
        structure_hint = ""

    return rewritten, chart, structure_hint
```

**폴백 계층**:
1. JSON 전체 깨짐 → raw 텍스트를 rewritten_query로, viz 필드 비움
2. JSON OK, `rewritten_query` 누락 → raw 텍스트로 대체
3. JSON OK, `user_requested_chart` 무효 값 → `null`
4. JSON OK, `sql_structure_hint` 타입 불일치 → `""`

rewriter LLM의 어떤 포맷 오류든 전체 파이프라인을 깨뜨리지 않는다.

### 4.5 SQL Generator 프롬프트 해석 규칙

`resources/prompts/reason/sql_generator_system_*.txt` (dialect별 4개 파일)

상단 RULES 섹션에 추가:

```
[VIZ STRUCTURE HINT]

사용자 원 질의에 시각화 차트 유형이 명시된 경우, 아래 힌트에 따라 SQL 구조를 조정한다.

sql_structure_hint: {sql_structure_hint}

힌트가 빈 문자열이면 표준 구조로 생성한다. 힌트가 있으면 표준 구조에 해당 지시를 추가 반영하되,
- 추출 대상 데이터가 힌트 구조에 맞지 않으면 힌트를 무시하고 표준 구조로 생성 (fail-safe)
- 힌트가 재무/금융 도메인 관례와 충돌하면 도메인 관례를 우선

## 대표 구조 스니펫 (힌트 해석 참고용)

### 파이/도넛: 카테고리 상위 N + '기타' 집계
-- postgres 예시
WITH ranked AS (
  SELECT category, value,
         ROW_NUMBER() OVER (ORDER BY value DESC) AS rn
  FROM (SELECT category, SUM(metric) AS value FROM src GROUP BY category) s
)
SELECT CASE WHEN rn <= 9 THEN category ELSE '기타' END AS category,
       SUM(value) AS value
FROM ranked
GROUP BY 1
ORDER BY CASE WHEN category = '기타' THEN 2 ELSE 1 END, value DESC;

### 워터폴: 유형 컬럼 CASE
SELECT 항목,
  CASE
    WHEN 항목 LIKE '기초%' OR 항목 LIKE '전월말%' THEN 'start'
    WHEN 항목 LIKE '기말%' OR 항목 LIKE '당월말%' THEN 'end'
    WHEN 금액 >= 0 THEN 'increase'
    ELSE 'decrease'
  END AS 유형,
  금액
FROM src;

### 산점도: 두 측정값 컬럼 확보
SELECT 기준축, SUM(측정값1) AS x_value, SUM(측정값2) AS y_value
FROM src
GROUP BY 기준축;

### 히트맵: 행·열·값 3컬럼 long format
SELECT 행차원, 열차원, SUM(값) AS 값
FROM src
GROUP BY 행차원, 열차원;
```

**소형 모델 대응**: 자연어 힌트 + 구체 템플릿 스니펫을 함께 제공하여 SQL Generator가 구조를 안정적으로 생성하도록 보조.

### 4.6 SQL 구조 적합성 검증 위치

힌트를 반영한 SQL이 실제로 요구 컬럼을 포함했는지 확인하는 로직이 필요하다 (§11의 fail-safe와 judge 폴백의 근거).

**결정**: 별도 validator 노드 수정 대신 **judge_visualization 진입 시점에 SQL 결과의 컬럼 구성을 검사**한다.

판단 근거:
- 파이의 '기타' 행은 데이터 내용 검사 필요 (컬럼명만으론 불충분) → judge 시점이 자연
- 워터폴의 `유형` 컬럼은 컬럼명 존재 여부로 판정 가능 → judge 시점에 처리 용이
- sql_validator에 추가하면 차트 유형별 검사 로직이 reason 계층으로 누출 (역할 경계 위배)

구현:
```python
def _supports_chart(
    requested: VisualizationType,
    result: SQLResult,
) -> bool:
    """SQL 결과가 요청 차트를 지원하는지 확인."""
    cols = {c.lower() for c in result.columns}
    n = result.row_count
    if requested == VisualizationType.PIE_CHART:
        return n >= 4  # 3행 이하면 의미 없음
    if requested == VisualizationType.DONUT_CHART:
        return n >= 4
    if requested == VisualizationType.WATERFALL_CHART:
        return "유형" in cols or "type" in cols
    if requested == VisualizationType.SCATTER_PLOT:
        numeric_cols = _count_numeric_columns(result)
        return numeric_cols >= 2
    if requested == VisualizationType.HEATMAP:
        return len(result.columns) >= 3
    if requested == VisualizationType.LINE_CHART:
        return n >= 3
    return True  # bar 계열은 대부분 지원
```

### 4.7 judge_visualization 참조 로직

[src/services/data_analyzer.py](src/services/data_analyzer.py) `judge_visualization` 함수 수정:

```
user_requested_chart가 주어졌는가?
├─ YES → _supports_chart()로 데이터 지원 여부 확인
│       ├─ YES → user_requested_chart 그대로 사용
│       └─ NO  → 안전한 폴백 차트 선택 + AmbiguitySignal 생성 (§6.4)
└─ NO  → 기존 로직: 데이터 형태 기반 자유 판단
```

**폴백 매핑**:
| 요청 차트 | 부적합 시 폴백 |
|---|---|
| pie_chart / donut_chart | table_only (행수 부족) 또는 horizontal_bar (행수 과다) |
| waterfall_chart | stacked_bar |
| scatter_plot | bar_chart |
| heatmap | grouped_bar |
| line_chart (2행) | bar_chart |

프롬프트 주입:
```
[USER REQUESTED CHART]
사용자가 명시적으로 요청한 차트: {user_requested_chart or "없음"}

사용자 요청이 있으면 우선 고려하되, 실제 데이터가 해당 차트에 부적합하면
(행수 너무 적음, 컬럼 구성 불일치 등) 안전한 차트로 대체하라.
```

## 5. 케이스별 동작 표

| 단계 | 사용자 원문 | rewritten_query | user_requested_chart | sql_structure_hint | judge 결과 |
|---|---|---|---|---|---|
| A | "파이차트로 비중" | "등급별 예금 비중" | pie_chart | "상위 N개+기타 집계" | 데이터 OK → pie |
| A | "워터폴로 손익" | "항목별 손익" | waterfall_chart | "유형 컬럼 CASE 생성" | 유형 OK → waterfall |
| A | "산점도로 여수신" | "지점별 여신-수신" | scatter_plot | "측정값 2개 확보" | 2개 OK → scatter |
| A | "히트맵으로 요일×시간대" | "요일 시간대 거래량" | heatmap | "3컬럼 pivot" | OK → heatmap |
| A | "표로 명세 보여줘" | "신규고객 명세" | table_only | "" | table_only |
| A + 실패 | "파이차트로" + 결과 3행 | "..." | pie_chart | "..." | **table_only 폴백** + 안내 |
| A + 실패 | "워터폴로" + 유형 컬럼 누락 | "..." | waterfall_chart | "..." | **stacked_bar 폴백** + 안내 |
| B | "손익 구성" | "손익 구성" | null | "" | judge 자유 판단 |
| C | "시각화해서" | "..." | null | "" | judge 자유 판단 |
| 복수 | "파이+라인 둘 다" | "..." | null | "" | judge 자유 판단 |

## 6. 트레이싱 · 로깅 설계

### 6.1 add_trace
intent_classifier 노드 완료 시 `_build_trace()` 메시지에 viz 요약 추가:

```python
trace_msg = f"의도:{intent.value}"
if user_requested_chart:
    trace_msg += f", 시각화요청:{user_requested_chart.value}"
elif sql_structure_hint:
    trace_msg += ", 시각화힌트:구조만"
```

### 6.2 REASONING_STEP 이벤트
intent_classifier 노드의 `dispatch_tracking_event(REASONING_STEP, {...})` 호출에서 `output` 섹션에 포함:

```python
"output": {
    "intent": intent.value,
    "preprocessed_input": preprocessed[:200],
    "user_requested_chart": (
        user_requested_chart.value if user_requested_chart else None
    ),
    "sql_structure_hint": sql_structure_hint,  # 짧으므로 트러ncation 없음
    ...
}
```

프론트엔드 trace 패널에서 viz 필드가 있을 때만 노출.

### 6.3 record_prompt_variables
rewriter LLM 호출 후 기존 record_prompt_variables 호출에 viz 관련 변수 추가 (선택 — 프롬프트 디버깅용).

### 6.4 AI 추론 안내 surfacing (resolved_signals)

사용자 명시 요청이 judge에서 **다른 차트로 변경**될 때 반드시 안내한다. §3.5 `resolved_signals` 패턴 사용.

**judge_visualization에서 폴백 발생 시**:

```python
from src.agents.models.ambiguity import AmbiguitySignal

fallback_chart = _select_fallback(requested, result)
reason_msg = _build_fallback_reason(requested, result)  # 사용자 친화 문장

signal = AmbiguitySignal(
    source_node="judge_visualization",
    decision="INFER",
    ambiguity_type="CHART_SELECTION",
    confidence="MEDIUM",
    question="",
    question_type="confirm",
    options=[],
    inferred_value=fallback_chart.value,
    reasoning=reason_msg,
    turn_id=state.turn_id,
)
state.resolved_signals.append(signal)
```

**안내 메시지 예시**:
| 요청 → 폴백 | reasoning 메시지 |
|---|---|
| pie → table_only | "요청하신 파이차트는 데이터가 {n}건뿐이라 표로 제공합니다" |
| pie → horizontal_bar | "파이차트로 표현하기에 카테고리가 많아 가로 막대로 대체합니다" |
| waterfall → stacked_bar | "워터폴에 필요한 유형 구분이 없어 누적 막대로 대체합니다" |
| scatter → bar | "산점도에 필요한 두 측정값이 없어 막대그래프로 대체합니다" |
| heatmap → grouped_bar | "히트맵에 필요한 3차원 구성이 없어 그룹 막대로 대체합니다" |

응답 포매팅 단에서 `[AI 추론]` 섹션으로 사용자에게 노출된다.

**안내하지 않는 케이스** (투명 동작):
- user_requested_chart와 judge 결정이 일치 (요청 그대로 사용)
- user_requested_chart가 `null`이었고 judge가 자유 판단
- sql_structure_hint가 SQL Generator에서 정상 반영

## 7. recovery · multi-turn 동작

### 7.1 turn_reset_updates
턴 경계에서 `user_requested_chart: None`, `sql_structure_hint: ""`로 리셋. 새 턴의 intent_classifier가 재생성.

### 7.2 recovery_agent
recovery_agent는 reason 계층만 리셋하므로 viz 필드는 **유지**. SQL 재생성 시 기존 힌트를 동일하게 참조.

### 7.3 multi-turn continue
`continuity=CONTINUE` 케이스에서 intent_classifier가 재실행되므로 viz 필드는 **매 턴 갱신**. 이전 턴에서 "파이차트로" 요청했더라도 이번 턴에 "그냥 표로"로 바꾸면 `user_requested_chart=table_only`로 갱신됨.

### 7.4 이전 턴 viz 재렌더링 시나리오
"아까 그거 차트로 다시 보여줘" 같은 연속 요청 시 intent_classifier가 이전 context를 참조하여 viz 필드 재판정. 별도 특수 처리 불필요.

## 8. 기존 coverage-expansion 문서와의 관계

[20260414-visualization-coverage-expansion.md](20260414-visualization-coverage-expansion.md)

- §5 상세 설계: 본 문서의 viz_hint 플로우를 참조로 링크. `judge_visualization` 수정(§5.7) 설명에 user_requested_chart 참조 + §6.4 surfacing 추가
- §11-1 결정: "본건은 별도 설계 문서 참조"로 축약 후 본 문서 링크
- §7 테스트 질의: A 케이스(명시 차트) 테스트가 viz_hint 경로를 검증

## 9. 테스트 계획

### 9.1 rewriter 출력 단위 테스트
- A 케이스 10개 (차트명 명시) → viz 필드 정확히 생성되는지
- B 케이스 5개 (의미적 암시) → viz 필드 비어있는지
- C 케이스 5개 (일반 시각화) → viz 필드 비어있는지
- 복수 차트 요청 3개 → `user_requested_chart=null`
- 표/none 요청 3개 → `table_only`/`none` 매핑
- 파싱 실패 케이스 (malformed JSON) → plain text 폴백

### 9.2 SQL Generator 통합 테스트
- `sql_structure_hint` 주어졌을 때 실제 SQL에 해당 구조 포함 여부 검증
- 힌트가 빈 문자열일 때 표준 구조 생성 확인
- 힌트와 데이터 가용성 충돌 시 폴백 동작

### 9.3 judge_visualization 폴백 + surfacing 테스트
- user_requested_chart=pie + 3행 결과 → table_only 폴백 + resolved_signals 생성 확인
- user_requested_chart=waterfall + 유형 컬럼 없음 → stacked_bar 폴백 + AmbiguitySignal reasoning 정확성
- user_requested_chart=None → 기존 자유 판단, resolved_signals 추가 없음
- 폴백 메시지가 사용자 친화적 문장인지 (기술 용어 미포함)

### 9.4 E2E 골든셋
coverage-expansion §7의 테스트 질의 카탈로그 재사용. A 케이스 모두 viz_hint 경로 통과. 폴백 케이스도 최소 3개 포함.

## 10. 구현 순서

### Phase 1 — state 필드 정의 (0.1 day)
1. PipelineState에 `user_requested_chart`, `sql_structure_hint` 2개 flat 필드 추가
2. `turn_reset_updates()` 갱신
3. mypy --strict 통과 확인

### Phase 2 — rewriter 프롬프트 확장 (0.5 day)
1. `intent_classifier_query_rewriter.txt` 출력 스키마 변경 (plain → JSON)
2. 예제 8개 추가 (A/B/C + 복수/표/none 케이스)
3. intent_classifier.py `_parse_rewriter_response` 함수 신설 + 호출부 수정
4. 파싱 실패 fallback 단위 테스트

### Phase 3 — SQL Generator 통합 (0.5 day)
1. dialect별 4개 프롬프트에 `[VIZ STRUCTURE HINT]` 섹션 + 템플릿 스니펫 추가
2. sql_generator.py에서 `sql_structure_hint` 프롬프트 변수 주입
3. 프롬프트 변수 치환 확인

### Phase 4 — judge_visualization 폴백 + surfacing (0.5 day)
1. `_supports_chart()` 판정 헬퍼 추가
2. data_analyzer.py `judge_visualization`에 `user_requested_chart` 참조 로직
3. `_build_fallback_reason()` 사용자 친화 메시지 생성
4. `AmbiguitySignal` 생성 및 `resolved_signals` 추가 로직
5. viz_judgment 프롬프트에 `[USER REQUESTED CHART]` 섹션 추가
6. 폴백 단위 테스트

### Phase 5 — 트레이싱 · E2E 검증 (0.25 day)
1. `add_trace` / `REASONING_STEP` 이벤트 업데이트
2. 프론트엔드 trace 패널에서 viz 필드 노출 확인 (프론트엔드 미존재 시 스킵)
3. 골든셋 E2E 실행, 회귀 확인
4. 응답에 `[AI 추론]` 섹션으로 폴백 사유 노출 확인

**총 1.85 day** (기존 1.5 day → surfacing + 스니펫 작업 추가)

## 11. 리스크 · 제약

### 11.1 rewriter JSON 포맷 안정성
- 기존 rewriter는 plain string 출력이라 JSON 전환 시 소형 모델 포맷 오류 가능
- 완화: §4.4 파싱 4단계 폴백으로 어떤 오류도 파이프라인 파괴 방지

### 11.2 user_requested_chart enum 미스매치
- rewriter가 `"파이차트"`, `"pie"`, `"Pie"` 등 비표준 값 생성 가능
- 완화: `VisualizationType()` 변환 실패 시 `null` 폴백 + 프롬프트에 유효 값 enum 전체 명시

### 11.3 SQL Generator 힌트 무시 경향
- 소형 모델이 `sql_structure_hint` 자연어를 무시할 가능성
- 완화: §4.5에 대표 4종 SQL 템플릿 스니펫을 프롬프트에 포함, judge가 `_supports_chart()`로 최종 방어선

### 11.4 multi-turn 힌트 오염
- 이전 턴 "파이차트로" → 다음 턴 "추이 보여줘"인데 viz 필드 남아있음
- 완화: `turn_reset_updates`로 매 턴 리셋 + intent_classifier 재실행이 자연 갱신

### 11.5 폴백 빈발 시 사용자 불만족
- user_requested_chart가 자주 덮어씌워지면 "명시한 차트를 무시한다"는 느낌
- 완화:
  - reasoning 메시지 품질 중요 — 항상 "왜 대체했는지" 구체적 이유 제시
  - 골든셋 모니터링으로 폴백률이 특정 임계(예: 30%) 초과 시 rewriter 프롬프트·폴백 규칙 재조정

## 12. 참고

- 관련 파일
  - [src/agents/state/state.py](src/agents/state/state.py)
  - [src/agents/models/normalization.py](src/agents/models/normalization.py)
  - [src/agents/nodes/interpret/intent_classifier.py](src/agents/nodes/interpret/intent_classifier.py)
  - [src/services/data_analyzer.py](src/services/data_analyzer.py)
  - [resources/prompts/interpret/intent_classifier_query_rewriter.txt](resources/prompts/interpret/intent_classifier_query_rewriter.txt)
- 연계 문서
  - [20260414-visualization-coverage-expansion.md](20260414-visualization-coverage-expansion.md) §11-1
