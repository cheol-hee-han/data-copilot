# WebSocket 응답 구조 개선 — 구조화 JSON 전환 설계

> **작성일**: 2026-04-07
> **구현 완료**: 2026-04-07 (Phase 1 + Phase 2)
> **관련 문서**: `20260407-formatter-rule-based-redesign.md`
> **상위 논의**: 답변 출력 구조 개선 — 논의 3 (WebSocket 스키마 구조화)
> **도출 방법**: UI 전문가 / 서버 전문가 에이전트 4라운드 교차 검토 + 사후 보정
> **설계 검토**: `docs/reviews/design/20260407-websocket-response-restructuring-review.md`

## 설계 검토 반영 사항

- **P1-1**: result_data 조립을 runner → **formatter로 이동** (코드값 변환 누락 방지, column_formats 중복 계산 해소)
- **P1-2**: 턴 metadata에 result_data/process_summary **포함** (세션 복원 시 lazy-load)
- **P2-2**: 테스트 수정 포함 (test_process_summary_builder, test_format_response)
- **P2-3**: REST API에 `include_insight` 파라미터 추가
- **P3-1**: PipelineResult docstring 보강 (result_data vs sql_result 구분)
- **P3-2**: `ui_result_max_rows` config 설정 분리
- **부수 수정**: `src/models/__init__.py`에서 삭제된 `format_trace_summary` re-export 제거

---

## 1. 문제 진단

### 1-1. 현재 구조

```
stream.chunk  →  마크다운 문자열 1개에 모든 정보가 합쳐져 전송
                 ┌─ 마크다운 테이블 (rule-based 포맷팅 완료)
                 ├─ 핵심 수치 요약 (1~2줄)
                 └─ <details>조회 과정 요약</details> (5단계 마크다운)

stream.end    →  insight JSON (구조화)
                 ┌─ query_interpretation, tables_used, validation_detail ...
                 └─ caveats (sql_generator INFER만 포함)

viz           →  시각화 JSON + table_data (시각화가 있을 때만)
```

### 1-2. 핵심 문제 4가지

| # | 문제 | 영향 |
|---|------|------|
| 1 | **조회 과정 요약 중복** | 마크다운 `<details>`와 insight JSON 양쪽에 유사 정보 존재 |
| 2 | **테이블 원본 데이터 미전송** | 프론트엔드가 정렬/필터/CSV복사 등 인터랙션 불가. viz.table_data는 시각화 있을 때만 전송 |
| 3 | **INFER 시그널 불완전** | insight.caveats는 `sql_generator` 노드 INFER만 수집. context_explorer, readiness_gate 등 다른 노드의 INFER와 pending_assumptions는 누락 |
| 4 | **마크다운 테이블이 실질적 스트리밍 없이 일괄 전송** | formatter가 rule-based 전환으로 LLM 미사용. `stream.chunk` 1회에 전체 마크다운 전송 (토큰 스트리밍 아님) |

### 1-3. 프론트엔드 현황

- `static/embedded.html` 단일 파일 (2,686줄, Vanilla JS, IIFE 모듈 패턴)
- marked.js(GFM)로 마크다운 렌더링, `_wrapRefBlocks`로 `<details>` → ref-block 스타일 후처리
- `renderInsight()`가 insight JSON을 별도 💡 접기 패널로 구조적 렌더링
- 마크다운 테이블은 `.table-wrap`으로 감싸져 `overflow-x/y: auto`, `max-height: 480px` 스크롤 적용됨
- TypeScript 없음 → 서버-클라이언트 스키마 보장 없음

---

## 2. 설계 방향: 최종 조립(Final Assembly) 패턴

### 2-1. 핵심 원칙

```
stream.chunk  →  텍스트 콘텐츠만 (핵심 수치 요약)
stream.end    →  구조화 데이터 (result_data, process_summary) + insight
프론트엔드    →  stream.end 수신 시 텍스트 + 구조화 컴포넌트를 최종 조립하여 표시
```

현재 formatter가 rule-based로 전환되어 LLM 토큰 스트리밍이 없으므로,
마크다운 테이블을 `stream.chunk`에 포함시킬 이유가 없다.
**테이블과 조회 과정 요약은 `stream.end`의 구조화 JSON으로 통합**한다.

### 2-2. 하이브리드 유지 이유

완전한 JSON 분리를 하지 않는 이유:
1. 폐쇄망 단일 HTML 환경에서 Vanilla JS로 구조화 렌더러를 전면 구현하면 복잡도 급증
2. `simple_responder` 경로(명확화 질문, 분석 설명 등)는 LLM 사용 → 마크다운 스트리밍 필요
3. marked.js 기반 마크다운 렌더링이 이미 잘 동작

### 2-3. 경로별 응답 구성

| 경로 | stream.chunk | stream.end 구조화 데이터 |
|------|-------------|------------------------|
| **데이터 추출** (formatter) | 핵심 수치 요약만 | result_data + process_summary |
| **데이터 분석** (formatter) | 분석 인사이트 텍스트 | result_data + process_summary |
| **명확화 질문** (simple_responder) | LLM 토큰 스트리밍 | — |
| **일반 응답** (simple_responder) | LLM 토큰 스트리밍 | — |

---

## 3. 최종 stream.end 메시지 스키마

```jsonc
{
  "type": "stream",
  "action": "end",

  // ── 기존 필드 (변경 없음) ──
  "status": "success" | "cancelled",
  "turn_id": "uuid" | null,
  "user_turn_id": "uuid" | null,
  "trace_files": [{"name": "...", "filename": "..."}],

  // ── insight (기존 유지, 변경 없음) ──
  "insight": {
    "is_success": true,
    "query_interpretation": { "original", "period", "target", "metric", "category" },
    "reasoning_trail": [{ "text", "tool", "warning" }],
    "tables_used": [{ "name", "alt_name", "desc", "reason", "columns" }],
    "tables_candidate": [{ "name", "alt_name", "desc", "reason" }],
    "tables_rejected": [{ "name", "alt_name", "desc", "reason" }],
    "validation_detail": [{ "label", "detail", "pass" }],
    "sql_summary": "string",
    "sql_code": "string",
    "references": [{ "source", "title", "detail" }],
    "confidence": "높음" | "보통" | "낮음",
    "caveats": ["string"],
    "total_elapsed": 3.2,
    "step_timings": [{ "label", "node", "elapsed" }],
    "result_stats": { "row_count", "column_count", "execution_time_ms" },
    "failure_narrative": "string",
    "dead_end_trail": [{ "failure_type", "reason", "lessons_learned" }]
  },

  // ★ 신규: SQL 결과 원본 데이터 (항상 전송, nullable)
  "result_data": {
    "columns": ["지점명", "고객수", "잔액"],
    "column_formats": {"고객수": "count", "잔액": "currency"},
    "rows": [{"지점명": "강남", "고객수": 1234, "잔액": 5000000000}],
    "total_count": 500,
    "displayed_count": 500
  } | null,

  // ★ 신규: 5단계 조회 과정 요약 (본문 영역에 렌더링)
  "process_summary": {
    "intent": {
      "label": "데이터 추출",
      "is_continuation": false
    },
    "interpretation": {
      "measures": ["신규 여신 건수"],
      "filters": ["영업점"],
      "period": "2024년 3월",
      "entities": ["예금"],
      "dimensions": ["지역별"]
    },
    "context": {
      "tables": [
        {"name": "TB_DEPOSIT_M", "label": "예금월말잔액", "status": "selected"}
      ],
      "use_case_count": 3,
      "manual_count": 1,
      "biz_terms": ["여신", "연체"]
    },
    "ai_decisions": {
      "inferences": [
        {"question": "기간 기준?", "value": "최근 1개월", "source_node": "context_interpreter"}
      ],
      "pending_assumptions": [
        "영업일 기준으로 산정했습니다"
      ],
      "notice": "다른 기준을 원하시면 말씀해 주세요"
    } | null,
    "validation": {
      "summary": "SQL 검증: 5개 항목 중 5개 통과",
      "row_count": 1523,
      "row_label": "1,523건 조회 완료"
    }
  } | null
}
```

### 3-1. process_summary를 최상위에 배치한 이유

process_summary는 **사용자가 봐야 할 정보**다 (AI가 어떻게 판단했는지, 다른 기준을 원하면 말씀해 달라는 안내 등).
insight(💡 패널)에 넣으면 대부분의 사용자가 클릭하지 않아 보지 못한다.

- **process_summary**: 본문 하단에 접기로 표시 → 사용자향 조회 과정 요약
- **insight**: 💡 버튼 클릭 시 표시 → 관리자/개발자향 기술 상세

### 3-2. result_data를 최상위에 배치한 이유

원본 테이블 데이터는 "추론 과정(insight)"과 성격이 다른 "결과 데이터"이므로 분리.

### 3-3. column_formats 포함 필수 근거

서버의 `detect_column_formats()`가 SQL 접미사 기반으로 이미 판별하는 `{"컬럼명": "currency"|"rate"|"count"|"text"}` 결과를 전달.
이것 없이 raw 숫자를 보내면 프론트엔드에서 "12345678"이 표시되어,
금융 도메인 요건(금액은 억/만원, 비율은 %, 건수는 ,건)을 충족하지 못함.

### 3-4. ai_decisions는 process_summary 안에만 존재

기존 문서에서 `insight.ai_decisions`(flat 배열)과 `process_summary.ai_decisions`를 이중으로 보내도록 설계했으나,
이는 아직 존재하지 않는 UI를 위한 투기적 설계였다. ai_decisions는 process_summary 안에만 유지한다.

---

## 4. 기타 메시지 변경

### 4-1. viz 메시지

```jsonc
// 과도기: table_data 유지 (시각화 내 "데이터 보기" 토글용)
{ "type": "viz", "title": "...", "code": "<svg>...", "chart_type": "bar",
  "table_data": {"columns": [...], "rows": [...]} }

// 안정화 후: table_data 제거 (stream.end.result_data로 통합)
{ "type": "viz", "title": "...", "code": "<svg>...", "chart_type": "bar" }
```

### 4-2. stream.chunk (마크다운)

```jsonc
// 변경 후: 마크다운 테이블과 <details> 모두 제거, 텍스트만 전송
{ "type": "stream", "action": "chunk", "text": "핵심 수치 요약 텍스트" }
```

마크다운 테이블과 `<details>조회 과정</details>`은 동시에 제거한다.
"전환기에 양쪽 다 보내는" 이중 전송 기간을 두지 않는다 (아래 5-4절 참조).

---

## 5. 프론트엔드 구현 상세

### 5-1. 최종 조립 렌더링 흐름

```
stream.start  → 답변 영역 생성, "답변 작성 중" 표시
stream.chunk  → 텍스트(요약) 마크다운 렌더링
stream.end    → 최종 조립:
                 ├─ 텍스트(요약) — 이미 렌더링됨
                 ├─ result_data → 구조화 테이블 렌더링 (텍스트 아래)
                 └─ process_summary → 조회 과정 접기 렌더링 (테이블 아래)
```

### 5-2. result_data 기반 테이블 (신규 함수)

```javascript
function renderResultTable(slot, resultData) {
  // column_formats 기반 셀 포맷팅
  // .table-wrap으로 감싸기 (기존 스크롤 CSS 재사용)
  // truncated 시 "전체 N건 중 500건만 표시" 안내
  // CSV 복사 버튼 부착
}
```

- `column_formats.currency` → format_currency() (한국어 단위: 조/억/만원)
- `column_formats.rate` → `value.toFixed(1) + '%'`
- `column_formats.count` → `value.toLocaleString() + '건'`
- 미판별 숫자 → 천 단위 구분자만

### 5-3. process_summary 렌더링 (본문 영역, 접기)

테이블 아래에 `<details>` 접기로 렌더링한다. 기존 `_wrapRefBlocks`의
ref-block 스타일을 재사용하여 일관된 UI를 유지한다.

```
<details class="ref-block" open>
  <summary class="ref-header">
    <span class="ref-tag">참고</span>
    <span class="ref-title">조회 과정 요약</span>
  </summary>
  <div class="ref-body">
    1. 질의 분류: ...
    2. 질의 해석: ...
    3. 활용 정보: ...
    4. AI 판단: ...     ← ai_decisions가 있을 때만 표시
    5. 검증 결과: ...
  </div>
</details>
```

### 5-4. 마크다운 테이블 전환 전략: 동시 전환 (이중 전송 없음)

**"전환기"를 두지 않는다.** 서버가 result_data를 보내기 시작하는 시점에
마크다운에서 테이블과 `<details>`를 동시에 제거한다.

이유:
- 현재 formatter는 rule-based(LLM 미사용)로 응답이 일괄 생성됨.
  `stream.chunk`와 `stream.end`가 거의 동시에 전송되므로 "스트리밍 중 테이블 표시" 시나리오 자체가 없음
- 양쪽 다 보내면 마크다운 테이블이 먼저 렌더링된 후 result_data 테이블로 교체해야 하여 깜빡임 발생
- 이중 렌더링 경로를 유지보수하는 비용이 영구적으로 발생

**과거 메시지 호환**: 이미 저장된 대화 이력에는 마크다운 테이블이 포함되어 있으므로,
프론트엔드의 기존 marked.js 렌더링과 `_wrapRefBlocks` 코드는 **삭제하지 않고 유지**한다.
새 메시지에서는 사용되지 않지만, 과거 메시지 렌더링 시 폴백으로 동작한다.

---

## 6. PipelineResult 모델 변경안

```python
class PipelineResult(BaseModel):
    """파이프라인 실행 결과."""

    # ── 기존 유지 ──
    response: str = ""
    trace_log: list[TraceEntry] = Field(default_factory=list)
    visualization: VisualizationData = Field(default_factory=VisualizationData)
    insight: dict[str, Any] = Field(default_factory=dict)
    sql_result: SQLResult = Field(default_factory=SQLResult)
    cancelled: bool = False
    awaiting_clarification: bool = False
    clarification_request: dict[str, Any] | None = None
    preprocessed_input: str = ""
    turn_id: str | None = None
    user_turn_id: str | None = None
    trace_files: list[dict[str, str]] = Field(default_factory=list)

    # ── 신규 ──
    result_data: dict[str, Any] | None = Field(
        default=None,
        description="SQL 결과 원본 (columns, rows, column_formats) — UI 테이블 렌더링용",
    )
    process_summary: dict[str, Any] | None = Field(
        default=None,
        description="5단계 조회 과정 요약 — 본문 하단 접기 렌더링용",
    )
```

---

## 7. 서버 구현 상세

### 7-1. result_data 조립 — runner.py `_build_result()`

```python
from src.services.response_formatter import detect_column_formats

def _build_result_data(
    result: dict[str, Any],
    sql_result: SQLResult,
) -> dict[str, Any] | None:
    """UI 전송용 구조화 데이터를 조립한다.

    PipelineState에 UI 전용 필드를 추가하지 않고,
    runner 계층에서 결과 조립 시 1회 생성한다.
    """
    if not sql_result or not sql_result.rows:
        return None

    reason = result.get("reason")
    validated_sql = (
        reason.validated_sql
        if reason and hasattr(reason, "validated_sql")
        else ""
    )
    column_formats = detect_column_formats(validated_sql) if validated_sql else {}

    MAX_ROWS = 500
    rows = sql_result.rows[:MAX_ROWS]

    return {
        "columns": sql_result.columns,
        "rows": rows,
        "column_formats": column_formats,
        "total_count": sql_result.row_count,
        "displayed_count": min(MAX_ROWS, sql_result.row_count),
    }
```

### 7-2. process_summary 구조화

기존 `process_summary_builder.py`를 str → dict 반환으로 리팩토링한다.
insight_builder가 아닌 **별도 모듈로 유지** (process_summary는 insight와 독립된 사용자향 데이터).

```python
# src/services/process_summary_builder.py
def build_process_summary(state: PipelineState) -> dict[str, Any] | None:
    """5단계 조회 과정 요약을 구조화 dict로 반환한다."""
    ...
```

### 7-3. formatter.py 변경

```python
async def format_response_node(state: PipelineState) -> dict:
    # 마크다운 테이블 생성 제거 — result_data로 대체
    # <details> 조회 과정 제거 — process_summary로 대체
    # 핵심 수치 요약 텍스트만 생성
    summary_line = build_summary_line(columns, rows, column_formats)

    # process_summary를 dict로 생성하여 State에 저장
    process_summary = build_process_summary(state)

    return {
        "formatted_response": summary_line,  # 텍스트만
        "process_summary": process_summary,  # 구조화 dict
        "status": QueryStatus.FORMATTED,
    }
```

### 7-4. main.py WebSocket 전송 변경

```python
end_msg = {
    "type": "stream",
    "action": "end",
    "status": "cancelled" if pipeline_result.cancelled else "success",
    "insight": pipeline_result.insight,
    "turn_id": pipeline_result.turn_id,
    "user_turn_id": pipeline_result.user_turn_id,
    "trace_files": pipeline_result.trace_files or [],
}
if pipeline_result.result_data:
    end_msg["result_data"] = pipeline_result.result_data
if pipeline_result.process_summary:
    end_msg["process_summary"] = pipeline_result.process_summary
```

### 7-5. REST API 대응

현재 REST `/api/query` 응답에는 `insight` 필드가 포함되지 않는다.
구조화 전환 시 REST 응답에도 `result_data`, `process_summary`, `insight`를 포함한다.

```python
result_body = {
    "session_id": session_id,
    "response": masked_response,         # 텍스트 요약 (마크다운)
    "result_data": pipeline_result.result_data,
    "process_summary": pipeline_result.process_summary,
    "insight": pipeline_result.insight,
}
```

REST 소비자(테스트 스크립트, 향후 연동 시스템)도 구조화 데이터에 접근 가능.

---

## 8. 실행 단계

기존 4-Phase 구조에서 동시 전환 방식으로 변경.
"이중 전송 전환기"를 두지 않으므로 Phase가 줄어든다.

### Phase 1: 서버 구조화 데이터 추가 + 마크다운 테이블/`<details>` 동시 제거

| 파일 | 변경 | 규모 |
|------|------|------|
| `src/agents/models/response.py` | `result_data`, `process_summary` 필드 추가 | +8줄 |
| `src/agents/graph/runner.py` | `_build_result_data()` + 호출 | +20줄 |
| `src/services/process_summary_builder.py` | str → dict 반환으로 리팩토링 | ±30줄 |
| `src/agents/nodes/present/formatter.py` | 마크다운 테이블 생성 제거, `<details>` 제거, 요약만 유지 | -15줄 |
| `src/main.py` | stream.end에 `result_data`, `process_summary` 포함 | +6줄 |
| `src/main.py` | REST `/api/query` 응답에 구조화 데이터 포함 | +5줄 |

### Phase 2: 프론트엔드 구조화 렌더링

| 파일 | 변경 | 규모 |
|------|------|------|
| `static/embedded.html` | `handleStream`에서 `result_data`, `process_summary` 수신/저장 | +5줄 |
| `static/embedded.html` | `renderResultTable()` 신규 (column_formats 기반 포맷팅, 스크롤, CSV 복사) | +100줄 JS, +20줄 CSS |
| `static/embedded.html` | `renderProcessSummary()` 신규 (ref-block 스타일 접기) | +60줄 JS |
| `static/embedded.html` | `render()` 분기: result_data 있으면 구조화 렌더링, 없으면 마크다운 폴백 | +5줄 |

- result_data/process_summary가 없는 과거 메시지는 기존 마크다운 렌더링으로 폴백
- `_wrapRefBlocks`, marked.js 테이블 렌더링 코드는 과거 호환을 위해 유지

### Phase 3: 레거시 정리 (Phase 2 안정화 후)

| 파일 | 변경 | 규모 |
|------|------|------|
| `src/main.py` | viz 메시지에서 `table_data` 제거 | -4줄 |
| `static/embedded.html` | `renderViz`에서 table_data 관련 코드 제거 | -25줄 |

---

## 9. 전체 변경량 요약

| 구분 | 서버 | 프론트엔드 | 비고 |
|------|:----:|:---------:|------|
| Phase 1 | +54줄 | — | 서버만 선행 배포 가능 (프론트 미수정 시 result_data 무시됨) |
| Phase 2 | — | +190줄 | 과거 메시지 폴백 보장 |
| Phase 3 | -4줄 | -25줄 | Phase 2 안정화 후 확정 |
| **합계** | **+50줄** | **+165줄** | — |

---

## 10. 합의 도출 과정 요약

| 라운드 | 주제 | 결론 |
|--------|------|------|
| **R1** | 개선 방향 | 양측 모두 하이브리드(마크다운 + JSON) 방식 제안 → 합의 |
| **R2** | 세부 스키마 교차 검토 | column_formats 필수 포함 합의, result_data 네이밍 합의, viz.table_data 과도기 유지 합의 |
| **R3** | process_summary 필요성 쟁점 | 서버측이 코드 분석으로 insight.caveats가 sql_generator INFER만 커버함을 증명 → process_summary 별도 생성 필요 확인 |
| **R4** | 최종 스키마 확정 | process_summary를 insight 내부에 구조화 dict로 배치 합의 |
| **사후 보정** | UX 관점 재검토 | 5가지 수정 반영 (아래) |

### 사후 보정 내역

| # | 항목 | 원래 결론 | 수정 후 | 이유 |
|---|------|----------|--------|------|
| 1 | process_summary 위치 | insight 내부 (💡 패널) | stream.end 최상위 → 본문 접기 | 사용자가 봐야 할 정보를 💡 패널에 넣으면 안 보임 |
| 2 | ai_decisions 이중 존재 | insight + process_summary 양쪽 | process_summary 안에만 | 투기적 설계 제거 |
| 3 | 마크다운↔result_data 전환기 | 이중 전송 기간 → 점진 전환 | 동시 전환 (이중 전송 없음) | 깜빡임 방지, 이중 경로 비용 제거 |
| 4 | Phase 3 "선택" | 마크다운 테이블 제거 선택 | Phase 1에 통합 (동시 제거) | 이중 렌더링 경로 영구화 방지 |
| 5 | REST API 대응 | 미언급 | Phase 1에 포함 | 외부 소비자 접근 보장 |
