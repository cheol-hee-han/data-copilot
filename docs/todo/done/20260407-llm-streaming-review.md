# LLM 토큰 스트리밍 적용 검토

> **작성일**: 2026-04-07
> **최종 검토**: 2026-04-08 (UI~서버 전체 코드 리뷰 반영)
> **관련 문서**: `20260407-websocket-response-restructuring.md`, `20260407-formatter-rule-based-redesign.md`
> **목적**: UI로 나가는 모든 응답 경로에 LLM 토큰 스트리밍을 적용하는 방안 검토

---

## 1. 현황 진단

### 1-1. 현재 응답 전송 방식

모든 응답이 **완성 후 일괄 전송**된다. WebSocket 프로토콜은 `stream.start → chunk → end` 구조이지만,
chunk가 1회에 전체 텍스트를 보내므로 실질적 스트리밍이 아니다.

```python
# main.py L472-476 — 전체 응답을 chunk 1회로 전송
await _safe_send({"type": "stream", "action": "chunk", "text": masked_response})
```

### 1-2. 경로별 현황

| 경로 | 최종 응답 생성 | LLM 사용 | 토큰 스트리밍 | 사용자 체감 |
|------|--------------|:--------:|:----------:|-----------|
| **데이터 추출** | formatter (rule-based) | X | X | 파이프라인 대기 후 결과 한번에 표시 |
| **데이터 분석** | analyzer (LLM) → formatter | O (3회) | X | 분석 대기 후 결과 한번에 표시 |
| **비데이터** (인사/안내) | simple_responder (정형) | X | X | 즉시 표시 (지연 없음) |
| **명확화 질문** | clarification_handler | X | X | 즉시 표시 (지연 없음) |

### 1-3. LLM 호출 방식

모든 LLM 호출이 `stream=False`(기본값)로 완료 대기:

```python
# 현재: 응답 완성까지 블로킹
response = await client.messages.create(model=..., messages=..., system=...)
```

`AsyncAnthropic` 클라이언트는 이미 사용 중이므로, `stream=True` 전환은 클라이언트 수준에서 준비되어 있다.

### 1-4. 전체 LLM 호출 목록과 스트리밍 적합성

코드 전수 조사 결과, LLM 호출은 **직접 호출 3건 + `llm_call_with_parse_retry` 래퍼 10건 = 총 13건**이다.

| 호출 위치 | 출력 형식 | 파싱 | 스트리밍 적합 | 이유 |
|-----------|----------|:----:|:-----------:|------|
| **인사이트 코멘트** (신규) | 자유 텍스트 2~3문장 | X | **O** | 핵심 대상 |
| `data_analyzer.analyze_data()` | JSON | `parse_analysis_json` | **X** | JSON 완성 필수 |
| `data_analyzer.judge_visualization()` | 구조화 텍스트 | `parse_viz_judgment` | **X** | 파싱 필요 |
| `data_analyzer.generate_svg_via_llm()` | SVG 코드 | 정규식 추출 | **X** | 완성된 SVG 필요 |
| `intent_classifier` | JSON | retry+parse | **X** | JSON 파싱 |
| `query_normalizer._call_llm()` | JSON | retry+parse | **X** | JSON 파싱 |
| `sql_generator._call_llm_for_sql()` | JSON | retry+parse | **X** | JSON 파싱 |
| `sql_validator.validate_sql_layer2b()` | JSON | retry+parse | **X** | JSON 파싱 |
| `context_interpreter` (batch/step) | JSON | retry+parse | **X** | JSON 파싱 |
| `recovery_agent._plan_recovery()` | JSON | retry+parse | **X** | JSON 파싱 |
| `intent_classifier.rewrite_analysis_query()` | 자유 텍스트 | X | △ | 스트리밍 불필요 (내부 처리) |
| `seed_sql_history._infer_description()` | 자유 텍스트 | X | **X** | 배치 도구 (UI 무관) |

**결론**: `llm_call_with_parse_retry`를 경유하는 10건은 모두 JSON 파싱이 전제이므로
스트리밍 불가. **스트리밍 대상은 자유 텍스트 출력만** — 신규 인사이트 코멘트 1곳이 핵심이다.

---

## 2. 스트리밍이 필요한 경로 / 불필요한 경로

### 2-1. 스트리밍이 UX에 의미 있는 경로

| 경로 | 이유 | 우선순위 |
|------|------|:--------:|
| **데이터 추출 — 인사이트 코멘트** | 현재 `build_summary_line()`이 템플릿 1줄 생성. LLM이 데이터를 보고 의미 있는 코멘트를 생성하면 UX 향상 | 높음 |
| **데이터 분석 — 분석 보고서** | analyzer가 이미 LLM 호출(3회). 단, 출력이 JSON이라 직접 스트리밍 불가. 인사이트 코멘트 패턴으로 통합 | 높음 |

### 2-2. 스트리밍이 불필요한 경로

| 경로 | 이유 |
|------|------|
| **비데이터** (인사/안내) | 정형 응답이라 즉시 완성. 스트리밍할 것 없음 |
| **명확화 질문** | JSON 구조체 전송. 토큰 스트리밍 부적합 |
| **테이블 데이터** | 구조화 JSON(result_data). 스트리밍이 아닌 최종 조립 패턴 |
| **조회 과정 요약** | 구조화 JSON(process_summary). 동일 |

---

## 3. 응답 종료 방식의 변화: Rule-Based → LLM

### 3-1. 변경 본질

현재 데이터 추출 경로는 **파이프라인 전체가 rule-based로 종료**된다:

```
SQL 실행 → formatter(rule-based) → build_summary_line() → 완료
```

스트리밍 도입 후에는 **formatter 안에 LLM 호출이 추가**되어, 마지막 단계가 LLM으로 바뀐다:

```
SQL 실행 → formatter(rule-based 포맷팅) → LLM 인사이트 코멘트(스트리밍) → 완료
```

이것은 **formatter의 rule-based 전환 취지와 충돌하지 않는다.** 역할이 명확히 분리되기 때문이다:

### 3-2. 역할 분리: 무엇이 Rule-Based이고 무엇이 LLM인가

#### Rule-Based로 유지되는 것 (정확성·결정론적 영역)

| 항목 | 현재 담당 | 변경 | 이유 |
|------|----------|:----:|------|
| **result_data 구조화** | `_build_result_data()` | 유지 | 컬럼명, 행 데이터, 포맷 힌트 — 정확해야 함 |
| **컬럼 포맷 감지** | `detect_column_formats()` | 유지 | SQL alias 접미사 기반 결정론적 탐지 |
| **숫자 포맷팅** | `format_currency/rate/count()` | 유지 | "1억 5,000만원", "3.1%", "1,234건" — 정확한 수치 |
| **코드값 변환** | `apply_code_mappings()` | 유지 | "01"→"정상" 코드 룩업 — 결정론적 |
| **process_summary** | `build_process_summary()` | 유지 | 5단계 조회 과정 메타데이터 — 구조화 |

#### LLM으로 전환되는 것 (해석·자연어 영역)

| 항목 | 현재 | 변경 후 | 이유 |
|------|------|---------|------|
| **요약 텍스트** | `build_summary_line()` 템플릿 3종 | LLM 인사이트 코멘트 2~3문장 | 데이터의 의미 해석은 LLM이 적합 |

### 3-3. 현재 build_summary_line()의 한계

현재 rule-based 요약은 3가지 템플릿만 존재한다:

```python
# Case 1: 단일 행 + 메트릭
"{metric_col}은(는) {formatted}입니다."
# 예: "총자산은(는) 50억원입니다."

# Case 2: 다수 행 + 메트릭 + 라벨
"총 {row_count:,}건 조회되었으며, {label}이(가) {formatted}로 가장 큽니다."
# 예: "총 234건 조회되었으며, 강남지점이(가) 523억원으로 가장 큽니다."

# Case 3: 폴백
"총 {row_count:,}건이 조회되었습니다."
```

**한계점**:
- "가장 큰 값"만 언급하고 패턴·추세·이상치를 해석하지 않음
- 조은사(은/는, 이/가) 처리가 기계적 — 한글 문법 부자연스러움
- 다차원 데이터(기간별+지점별 등)의 교차 해석 불가

### 3-4. LLM 인사이트 코멘트가 제공하는 가치

LLM이 SQL 조회 결과를 보고 **데이터가 의미하는 바를 해석**한다:

```
# Rule-based (현재):
"총 12건 조회되었으며, 강남지점이(가) 523억원으로 가장 큽니다."

# LLM 인사이트 (변경 후):
"강남지점(523억)이 전체의 28%를 차지하며 가장 높고, 
 하위 3개 지점(마포·성동·관악)은 평균의 60% 수준으로 편차가 큽니다."
```

---

## 4. 응답 유형별 상세: 스트리밍 답변 vs result_data 답변

### 4-1. 두 응답의 본질적 차이

| 구분 | **result_data** (구조화 데이터) | **인사이트 코멘트** (스트리밍 텍스트) |
|------|-------------------------------|-------------------------------------|
| **목적** | 정확한 숫자·테이블 표시 | 데이터의 의미 해석 |
| **생성 주체** | rule-based 코드 | LLM |
| **전송 방식** | 일괄 전송 (JSON 1회) | 토큰 스트리밍 (다수 chunk) |
| **포맷팅** | `column_formats`로 결정론적 | LLM이 자유 서술 |
| **정확도 보장** | 코드 레벨 보장 (SQL 결과 그대로) | LLM 의존 (환각 가능성 있음) |
| **프론트엔드 렌더링** | HTML 테이블 (columns + rows) | 마크다운 텍스트 (streaming cursor) |
| **에러 시 폴백** | 없음 (실패 시 테이블 미표시) | `build_summary_line()` 템플릿으로 폴백 |

### 4-2. 사용자 화면에서의 배치

```
┌─────────────────────────────────────────────┐
│  [progress bar]  ← 기존 유지                  │
│                                             │
│  ┌─ result_data (테이블) ──────────────────┐  │
│  │  지점명    │  대출잔액   │  비율        │  │
│  │  강남지점  │  523억원    │  28.1%      │  │
│  │  서초지점  │  412억원    │  22.1%      │  │
│  │  ...      │  ...       │  ...        │  │
│  │            총 12건 중 12건 표시          │  │
│  └────────────────────────────────────────┘  │
│                                             │
│  강남지점(523억)이 전체의 28%를 차지하며 가장   │
│  높고, 하위 3개 지점은 평균의 60% 수준으로 편차  │
│  가 큽니다.█  ← LLM 스트리밍 (cursor 깜빡임)   │
│                                             │
│  ▸ 조회 과정 요약  ← stream.end 후 표시       │
└─────────────────────────────────────────────┘
```

### 4-3. 전송 타임라인

```
시간 →
├─ [progress]    interpret → reason → present (기존 유지)
├─ [result_data] 테이블 즉시 전송 ────── 사용자: 테이블 확인 중
├─ [stream.start] "답변 작성 중"
├─ [stream.chunk] "강남지점(523억)이"
├─ [stream.chunk] " 전체의 28%를 차지하며" ── 사용자: 텍스트 읽는 중
├─ [stream.chunk] " 가장 높고, ..."
├─ [stream.end]  process_summary + insight + turn_id
└─ [download_ready] CSV/JSON 다운로드 가능
```

**핵심**: 테이블이 먼저 나타나고, LLM 코멘트가 토큰 단위로 아래에 나타남.
사용자는 테이블을 확인하면서 코멘트가 생성되는 것을 지켜봄.

### 4-4. 데이터 분석 경로의 변화

현재 분석 경로의 3개 LLM 호출은 모두 **JSON/SVG 출력**이므로 직접 스트리밍이 불가능하다.
대신 다음과 같이 개선한다:

**현재 (순차 3회 → 일괄 전송)**:
```
Call #1 (분석 JSON) → Call #2 (시각화 판단) → Call #3 (SVG 생성) → 전체 일괄 전송
```

**개선안 (병렬 + 선행 전송 + 인사이트 스트리밍)**:
```
┌ Call #1 (분석 JSON)      ── 비스트리밍 유지
│                           
├ Call #2→#3 (시각화)       ── 병렬 실행, 완료 즉시 viz 전송
│
└ viz 전송 완료 후:
  result_data 전송 → 인사이트 코멘트 LLM 스트리밍 → stream.end
```

**효과**:
- Call #1과 Call #2→#3이 **병렬**로 실행되어 전체 지연 ~40% 감소
- 시각화가 먼저 화면에 표시되어 사용자 체감 대기 대폭 감소
- 분석 JSON 파싱 로직(`parse_analysis_json`)을 건드리지 않음
- 인사이트 코멘트가 분석 결과(`AnalysisResult.summary` + `insights`)를 참조하여 스트리밍 생성

---

## 5. 답변 퀄리티·정확도 검토

### 5-1. result_data: 정확도 영향 없음

result_data는 **SQL 실행 결과를 그대로** 구조화한 것이다:

```python
# formatter.py — SQL 결과를 코드 매핑 후 구조화
rows = apply_code_mappings(state.sql_result.rows, ...)  # "01"→"정상"
result_data = {
    "columns": state.sql_result.columns,
    "rows": rows[:max_rows],
    "column_formats": detect_column_formats(sql),  # 접미사 기반 결정론적 감지
}
```

- 숫자 포맷팅(`format_currency`, `format_rate`, `format_count`)은 프론트엔드에서 `column_formats` 힌트로 적용
- 코드값 변환은 MongoDB에서 가져온 코드 메타 딕셔너리로 결정론적 룩업
- **LLM 개입 없음 → 정확도 변화 없음**

### 5-2. 인사이트 코멘트: 환각(hallucination) 리스크 분석

LLM이 조회 결과를 보고 해석 코멘트를 생성하므로 환각 가능성이 있다.

#### 리스크 시나리오와 대응

| 시나리오 | 예시 | 발생 가능성 | 위험도 | 대응 |
|---------|------|:----------:|:-----:|------|
| **숫자 왜곡** | 실제 523억인데 "약 500억" | 중 | **높음** | 프롬프트에 "숫자를 반복하지 말고 의미를 해석하라" 지시 |
| **순위 오류** | 2위를 1위라고 서술 | 낮 | 높음 | 프롬프트에 상위 데이터 샘플 + 정렬 힌트 제공 |
| **없는 패턴 날조** | "전분기 대비 증가" (비교 데이터 없음) | 중 | **높음** | 프롬프트에 "제공된 데이터에 없는 사실을 추론하지 마라" 명시 |
| **과잉 해석** | 단순 목록인데 "추세가 보인다" | 중 | 낮 | 프롬프트에 데이터 성격(시계열/단면) 명시 |
| **전문 용어 오용** | 금융 지표 명칭 혼동 | 낮 | 중 | 프롬프트에 원본 질문과 컬럼명 포함 |

#### 핵심 방어 전략: "숫자는 rule-based, 해석은 LLM"

**인사이트 코멘트에서 LLM이 숫자를 직접 생성하지 않도록** 설계한다:

```
[프롬프트]
당신은 은행 데이터 분석가입니다.
아래 조회 결과의 핵심 의미를 2~3문장으로 해석하세요.

규칙:
- 구체적 숫자를 반복하지 마세요 (테이블에 이미 표시됩니다)
- 데이터에 없는 사실을 추론하지 마세요
- 비교/추세 언급은 데이터에 근거가 있을 때만 하세요
- 비전문가가 이해할 수 있는 표현을 사용하세요

[사용자 질문]: {original_query}
[조회 결과]: {row_count}건, 컬럼: {columns}
[상위 데이터 샘플]:
{top_rows_formatted}
```

**"숫자를 반복하지 마라"** 지시가 핵심이다. 사용자는 테이블에서 정확한 숫자를 확인하고,
인사이트 코멘트는 "그 숫자가 의미하는 바"만 전달한다. LLM이 숫자를 잘못 인용할
리스크 자체를 제거하는 것이다.

### 5-3. 폴백: LLM 실패 시 기존 품질 보장

LLM 인사이트 코멘트 생성이 실패(타임아웃, 에러, 빈 응답)하면:

```python
try:
    async for token in client.messages.stream_create(...):
        await dispatch_tracking_event("stream.token", {"text": token})
except Exception:
    # 폴백: 기존 rule-based 요약 사용
    fallback = build_summary_line(columns, rows, column_formats)
    await dispatch_tracking_event("stream.token", {"text": fallback})
```

- 사용자는 최소한 기존 수준의 요약을 받음
- result_data(테이블)는 LLM과 무관하게 이미 전송됨
- **LLM 실패가 데이터 전달을 차단하지 않음**

### 5-4. 폐쇄망 모델 품질 평가

| 모델 | 2~3문장 인사이트 품질 예상 | 근거 |
|------|-------------------------|------|
| **Claude** (온라인) | 우수 | 데이터 해석은 기본 역량. 프롬프트 준수도 높음 |
| **Solar Pro 2 70B** (현재 폐쇄망) | 양호 | 한국어 능력 우수. 2~3문장 수준은 난이도 낮음 |
| **Qwen3.5 397B** (예정) | 우수 | 파라미터 수 충분. 지시 따르기 능력 높음 |
| **GPT OSS 120B** (예정) | 양호~우수 | 모델 성능 미확인이나 충분한 파라미터 수 |

2~3문장 데이터 해석 코멘트는 **SQL 생성보다 난이도가 현저히 낮다**:
- 입력: 질문 + 조회 결과 샘플 (명확한 컨텍스트)
- 출력: 자연어 2~3문장 (자유 형식)
- 복잡한 추론 불필요, 패턴 인식 수준

### 5-5. 정확도 보장 체계 요약

```
┌─────────────────────────────────────────────────────┐
│                   사용자 화면                         │
├───────────────────┬─────────────────────────────────┤
│  result_data      │  인사이트 코멘트                   │
│  (테이블)          │  (스트리밍 텍스트)                  │
├───────────────────┼─────────────────────────────────┤
│  정확도: 코드 보장  │  정확도: LLM 의존                  │
│  숫자: SQL 결과 원본│  숫자: 직접 인용 금지 (프롬프트)     │
│  포맷: 결정론적     │  해석: 데이터 기반 제한              │
│  폴백: 없음 (필수)  │  폴백: build_summary_line()       │
│  생성: rule-based  │  생성: LLM 스트리밍                │
└───────────────────┴─────────────────────────────────┘
```

**설계 원칙**: 사용자가 **의사결정에 사용하는 정확한 데이터(숫자, 테이블)**는
rule-based로 보장하고, **데이터 이해를 돕는 해석**만 LLM에 위임한다.
LLM이 실패해도 테이블과 숫자는 항상 정확하게 전달된다.

---

## 6. 기술 구현 상세

### 6-1. 콜백 전달 메커니즘: `adispatch_custom_event` 패턴

#### 선택 근거

노드 내부에서 LLM 토큰을 WebSocket으로 전달하는 경로가 필요하다.
3가지 옵션을 검토한 결과 **기존 `adispatch_custom_event` 패턴의 확장**이 최적이다.

| 옵션 | 방식 | 판정 | 이유 |
|------|------|:----:|------|
| **A. adispatch_custom_event** | 기존 이벤트 디스패치 패턴 확장 | **채택** | 20+ 곳에서 검증된 패턴. 노드 시그니처 변경 없음 |
| B. config["configurable"] | 노드에서 ensure_config()로 콜백 접근 | 기각 | 새 패턴 도입. 체크포인터 직렬화 문제 |
| C. astream_events | ainvoke → astream_events 전환 | 기각 | runner.py 전면 변경. 이벤트 필터링 부담 |

#### 구현 흐름

```
formatter 노드
  └→ stream_create() — 토큰 생성
       └→ dispatch_tracking_event("stream.token", {"text": token})
            └→ adispatch_custom_event() — contextvars로 RunnableConfig 자동 획득
                 └→ callback_handler.on_custom_event()
                      └→ self._on_event({"type": "stream", "action": "chunk", "text": ...})
                           └→ _safe_send() → WebSocket
```

**기존 코드에서 이미 동일한 패턴으로 동작하는 예시**:

```python
# 현재 코드 (sql_generator.py) — 이벤트 디스패치
await dispatch_tracking_event(REASONING_STEP, {
    "node": "sql_generator", "phase": "reason", ...
})

# 추가할 코드 (formatter.py) — 토큰 디스패치 (동일 패턴)
await dispatch_tracking_event("stream.token", {
    "text": token
})
```

#### callback_handler 변경 (~10줄)

```python
# callback_handler.py — on_custom_event 확장
async def on_custom_event(self, name, data, *, run_id, **kwargs):
    domain = name.split(".")[0]
    match domain:
        case "decision": self._record_decision(node, data)
        case "context":  self._record_context_retrieval(node, data)
        case "llm":      ...
        case "stream":                                        # 신규
            if name == "stream.token" and self._on_event:     # 신규
                await self._on_event({                        # 신규
                    "type": "stream",                         # 신규
                    "action": "chunk",                        # 신규
                    "text": data.get("text", ""),             # 신규
                })                                            # 신규
```

#### 동시 전송 안전성

- `ainvoke` 유지 → 모든 콜백이 **단일 asyncio Task에서 순차 await**
- `on_chain_end`(progress)와 `on_custom_event`(stream.token)이 동시에 발생할 수 없음
- 방어적으로 `asyncio.Lock` 추가 권장 (비용 0, 향후 안전):

```python
# main.py
_ws_lock = asyncio.Lock()

async def _safe_send(msg):
    async with _ws_lock:
        if _ws_closed: return False
        try:
            await websocket.send_json(msg)
            return True
        except (WebSocketDisconnect, RuntimeError):
            _ws_closed = True
            return False
```

### 6-2. Provider별 스트리밍 API 설계

현재 `UnifiedLLMClient`는 `client.messages.create()` 단일 인터페이스로 Anthropic/OpenAI 호환을
추상화한다. 스트리밍도 동일한 원칙으로 **`client.messages.stream_create()`** 를 추가하여
프로바이더 무관 async generator를 제공한다.

**별도 메서드를 선택한 이유**:
- 기존 `create()`의 반환 타입(`LLMResponse`)과 스트리밍의 반환 타입(`AsyncGenerator[str]`)이 완전히 다름
- `create(stream=True)` 방식은 반환 타입이 Union이 되어 모든 호출처에서 분기 필요
- 기존 13개 호출처에 영향 제로

```python
# 통합 인터페이스 — 노드 코드에서 사용
client = get_llm_client()
async for token in client.messages.stream_create(
    model=settings.llm_model,
    max_tokens=300,
    system="...",
    messages=[{"role": "user", "content": "..."}],
):
    # token: str (text_delta 1개)
    await callback.on_stream_chunk(token)
```

#### Anthropic 분기 (`AnthropicMessages.stream_create`)

Anthropic SDK는 `messages.stream()` 컨텍스트 매니저 + `text_stream` async iterator를 제공한다.
`content_block_delta` 이벤트에서 `text_delta`만 추출하여 yield하므로 별도 필터링이 불필요하다.

```python
class AnthropicMessages:
    async def stream_create(
        self, *, model, max_tokens, system=None, messages,
        timeout=None, abort: asyncio.Event | None = None, **kwargs,
    ) -> AsyncGenerator[str, None]:
        call_kwargs = {"model": model, "max_tokens": max_tokens, "messages": messages, **kwargs}
        if system is not None:
            call_kwargs["system"] = system
        if timeout is not None:
            call_kwargs["timeout"] = timeout

        _start = _time.perf_counter()
        _first_token_time: float | None = None
        full_text_parts: list[str] = []

        async with self._client.messages.stream(**call_kwargs) as stream:
            async for text in stream.text_stream:
                if abort and abort.is_set():
                    break
                if _first_token_time is None:
                    _first_token_time = _time.perf_counter()
                full_text_parts.append(text)
                yield text

            # 스트리밍 완료 후 트래킹 1회 발행
            final_message = await stream.get_final_message()
            _elapsed = (_time.perf_counter() - _start) * 1000
            _ttft = ((_first_token_time - _start) * 1000) if _first_token_time else _elapsed
            _usage = getattr(final_message, "usage", None)
            await dispatch_tracking_event(LLM_CALL, {
                "node": get_current_node(),
                "prompt_summary": _build_prompt_summary(system, messages),
                "response_text": truncate_trace("".join(full_text_parts)),
                "model": model,
                "prompt_tokens": getattr(_usage, "input_tokens", 0),
                "response_tokens": getattr(_usage, "output_tokens", 0),
                "latency_ms": _elapsed,
                "ttft_ms": _ttft,
            })
```

**특이사항 없음**: Anthropic SDK가 `text_stream`으로 text_delta만 깔끔하게 제공하므로
thinking 블록, tool_use 블록 등은 자동으로 필터링된다.

#### OpenAI 호환 분기 (`OpenAICompatibleMessages.stream_create`) — Qwen3.5 대응

OpenAI 호환 API는 `stream=True`로 SSE 스트리밍을 활성화한다.
Qwen3.5에서는 `<think>` 태그 스트리밍 필터가 핵심이다.

```python
class OpenAICompatibleMessages:
    async def stream_create(
        self, *, model, max_tokens, system=None, messages,
        timeout=None, abort: asyncio.Event | None = None, **kwargs,
    ) -> AsyncGenerator[str, None]:
        from src.agents.nodes.thinking_modes import get_thinking_mode

        openai_messages = []
        if system:
            openai_messages.append({"role": "system", "content": system})
        openai_messages.extend(messages)

        call_kwargs = {"model": model, "max_tokens": max_tokens,
                       "messages": openai_messages, "stream": True}
        if timeout is not None:
            call_kwargs["timeout"] = timeout

        # ── Thinking 모드 제어 ──
        node_name = get_current_node()
        thinking_mode = get_thinking_mode(node_name)
        thinking_params = _resolve_thinking_params(model, thinking_mode)
        call_kwargs.update(thinking_params)

        _start = _time.perf_counter()
        _first_token_time: float | None = None
        full_text_parts: list[str] = []
        prompt_tokens = 0
        completion_tokens = 0

        response = await self._client.chat.completions.create(**call_kwargs)

        # ── Qwen <think> 태그 스트리밍 필터 ──
        is_qwen = "qwen" in model.lower()
        think_filter = _ThinkTagStreamFilter() if is_qwen else None

        async for chunk in response:
            if abort and abort.is_set():
                break

            choice = chunk.choices[0] if chunk.choices else None
            if not choice or not choice.delta or not choice.delta.content:
                continue

            raw_text = choice.delta.content

            if think_filter:
                filtered = think_filter.feed(raw_text)
                if filtered:
                    if _first_token_time is None:
                        _first_token_time = _time.perf_counter()
                    full_text_parts.append(filtered)
                    yield filtered
            else:
                if _first_token_time is None:
                    _first_token_time = _time.perf_counter()
                full_text_parts.append(raw_text)
                yield raw_text

            # 마지막 chunk에서 usage 수집
            if hasattr(chunk, "usage") and chunk.usage:
                prompt_tokens = getattr(chunk.usage, "prompt_tokens", 0)
                completion_tokens = getattr(chunk.usage, "completion_tokens", 0)

        # think_filter 잔여 버퍼 flush
        if think_filter:
            remaining = think_filter.flush()
            if remaining:
                full_text_parts.append(remaining)
                yield remaining

        _elapsed = (_time.perf_counter() - _start) * 1000
        _ttft = ((_first_token_time - _start) * 1000) if _first_token_time else _elapsed
        await dispatch_tracking_event(LLM_CALL, {
            "node": get_current_node(),
            "prompt_summary": _build_prompt_summary(system, messages),
            "response_text": truncate_trace("".join(full_text_parts)),
            "model": model,
            "prompt_tokens": prompt_tokens,
            "response_tokens": completion_tokens,
            "latency_ms": _elapsed,
            "ttft_ms": _ttft,
        })
```

**`abort` 파라미터**: v1에서는 미사용이지만 인터페이스를 확보해둔다.
향후 cancel 시 LLM 스트림을 즉시 중단하는 데 사용.

### 6-3. Qwen3.5 `<think>` 태그 스트리밍 필터 상세

#### 문제

Qwen3.5는 thinking 모드 활성화 시 응답 앞에 `<think>...</think>` 블록을 삽입한다.
비스트리밍에서는 `_strip_thinking_tags()` 정규식으로 한 번에 제거하지만,
스트리밍에서는 **태그가 chunk 경계에서 쪼개질 수 있다**:

```
delta[0]: "Let me "
delta[1]: "<thi"          ← 태그 시작이 불완전
delta[2]: "nk>\n분석해보면"
delta[3]: "...\n</think"  ← 닫기 태그가 불완전
delta[4]: ">\n실제 답변"
```

단순 `if "<think>" in chunk` 로는 분할된 태그를 감지할 수 없다.

#### 해결: 상태 머신 스트림 필터

```python
class _ThinkTagStreamFilter:
    """<think>...</think> 태그를 스트리밍 중 실시간 제거하는 상태 머신.

    상태:
        NORMAL   — <think> 외부. 텍스트를 그대로 yield.
                   단, 버퍼 끝에 '<'로 시작하는 부분 매치가 있으면 보류.
        THINKING — <think> 내부. 텍스트를 버리고 </think> 를 탐색.

    chunk 경계에서 태그가 쪼개지는 케이스를 처리하기 위해
    NORMAL 상태에서는 '<think>'의 부분 매치 가능성이 있는 끝부분을 버퍼에 보류하고,
    THINKING 상태에서는 '</think>'의 부분 매치를 추적한다.
    """

    _OPEN_TAG = "<think>"    # 7자
    _CLOSE_TAG = "</think>"  # 8자

    def __init__(self) -> None:
        self._inside_think = False
        self._buf = ""

    def feed(self, chunk: str) -> str:
        """chunk를 입력받아 필터링된 텍스트를 반환한다.

        Returns:
            필터링된 텍스트. <think> 내부 텍스트는 빈 문자열로 반환.
        """
        self._buf += chunk
        output_parts: list[str] = []

        while self._buf:
            if self._inside_think:
                # </think> 탐색
                close_idx = self._buf.find(self._CLOSE_TAG)
                if close_idx == -1:
                    # 아직 닫히지 않음 → 부분 매치 보류 (끝 7자)
                    # </think> 가 잘려서 도착할 수 있으므로
                    if len(self._buf) > len(self._CLOSE_TAG) - 1:
                        # 끝 7자만 보류, 나머지 버림
                        self._buf = self._buf[-(len(self._CLOSE_TAG) - 1):]
                    break
                # </think> 발견 → 태그 닫힘
                self._buf = self._buf[close_idx + len(self._CLOSE_TAG):]
                self._inside_think = False
            else:
                # <think> 탐색
                open_idx = self._buf.find(self._OPEN_TAG)
                if open_idx == -1:
                    # 부분 매치 가능성: 끝에 '<', '<t', '<th', ... 가 있을 수 있음
                    # 최대 len("<think>") - 1 = 6자를 보류
                    safe_end = len(self._buf) - (len(self._OPEN_TAG) - 1)
                    if safe_end > 0:
                        output_parts.append(self._buf[:safe_end])
                        self._buf = self._buf[safe_end:]
                    break
                # <think> 발견 → 앞부분은 출력, 태그 이후는 thinking 상태
                if open_idx > 0:
                    output_parts.append(self._buf[:open_idx])
                self._buf = self._buf[open_idx + len(self._OPEN_TAG):]
                self._inside_think = True

        return "".join(output_parts)

    def flush(self) -> str:
        """스트리밍 종료 시 버퍼에 남은 텍스트를 반환한다.

        NORMAL 상태에서 부분 매치 보류된 텍스트가 실제로는 태그가 아닌 경우
        (예: 응답이 '<th'로 끝나는 경우) 여기서 방출된다.
        THINKING 상태에서 남은 텍스트는 닫히지 않은 <think> 블록이므로 버린다.
        """
        if self._inside_think:
            self._buf = ""
            return ""
        remaining = self._buf
        self._buf = ""
        return remaining
```

#### 대안: thinking 비활성화

인사이트 코멘트(2~3문장)처럼 간단한 출력에는 thinking 자체가 불필요하다.
`_resolve_thinking_params()`에서 `enable_thinking: false`를 설정하면
`<think>` 블록이 생성되지 않아 필터가 동작하지 않아도 된다.

```python
# 현재 thinking_modes.py 에서 노드별 모드 지정 가능
# 스트리밍 인사이트/분석 노드는 thinking off 권장
THINKING_MODES = {
    "format_response": "off",   # 인사이트 코멘트 — thinking 불필요
    "analyze_data": "off",      # 분석 텍스트 — thinking 불필요 (데이터 해석)
    "generate_sql": "on",       # SQL 생성 — thinking 필요 (복잡 추론)
}
```

그럼에도 `_ThinkTagStreamFilter`는 구현해야 한다:

- 폐쇄망에서 thinking 모드 설정이 vLLM 버전에 따라 동작하지 않을 수 있음
- `enable_thinking: false`가 무시되는 모델/서빙 조합이 존재
- 안전망(방어적 코딩)으로 필터를 항상 적용

### 6-4. Anthropic vs Qwen 스트리밍 비교 요약

| 항목 | Anthropic (Claude) | OpenAI 호환 (Qwen3.5) |
| ------ | ------ | ------ |
| **프로토콜** | SSE (`messages.stream()`) | SSE (`completions.create(stream=True)`) |
| **텍스트 추출** | `stream.text_stream` (SDK가 text_delta만 필터) | `chunk.choices[0].delta.content` (raw delta) |
| **thinking 블록** | `thinking` content_block (SDK가 자동 분리) | `<think>` 인라인 태그 (직접 필터 필요) |
| **thinking 비활성화** | Extended thinking 미사용 시 자동 | `enable_thinking: false` 파라미터 |
| **태그 분할 위험** | 없음 (블록 단위 분리) | 있음 (chunk 경계에서 태그 쪼개짐) |
| **usage 수집** | `stream.get_final_message()` | 마지막 chunk의 `usage` 또는 별도 호출 |
| **트래킹** | 스트리밍 완료 후 final_message에서 | 스트리밍 완료 후 누적값에서 |
| **에러 핸들링** | `stream` 컨텍스트 매니저가 처리 | async for 루프 내 예외 캐치 |

### 6-5. 인사이트 코멘트 thinking 모드 가이드

| 용도 | 권장 thinking 모드 | 이유 |
|------|:---:|------|
| 인사이트 코멘트 (2~3문장) | **off** | 단순 데이터 해석. thinking 오버헤드만 증가 |
| 분석 보고서 (긴 텍스트) | **off** | 데이터 기반 서술. 복잡 추론 아님 |
| SQL 생성 | **on** | 테이블 선택·조인·조건 추론에 thinking 효과 큼 |
| 시각화 판단 | **off** | 규칙 기반 분류. JSON 출력 안정성 우선 |

### 6-6. chunk 버퍼링 전략

토큰을 1개씩 WebSocket으로 보내면 네트워크 오버헤드 + 프론트엔드 DOM 재작성이 과다하다.
**5토큰 단위 서버 버퍼링**을 적용한다.

```python
# formatter 노드 내부 — stream_create 호출 시 버퍼링
buffer: list[str] = []
async for token in client.messages.stream_create(...):
    buffer.append(token)
    if len(buffer) >= 5:
        await dispatch_tracking_event("stream.token", {"text": "".join(buffer)})
        buffer.clear()
# 잔여 버퍼 flush
if buffer:
    await dispatch_tracking_event("stream.token", {"text": "".join(buffer)})
```

**효과**:
- 2~3문장(50~80토큰) → 10~16회 WebSocket 전송
- 프론트엔드 `mdRender()` 호출도 10~16회 → 성능 문제 없음
- 프론트엔드 코드 변경 불필요 (기존 `appendChunk` + `RD.render` 그대로 사용)

### 6-7. 취소(cancel) 처리

**v1: 프론트엔드 무시 패턴 유지**

2~3문장 인사이트 코멘트는 1~3초면 완료된다. 사용자가 cancel을 누르면:
1. 프론트엔드: `_cancelled = true` → 이후 도착하는 chunk 무시
2. 서버: LLM 스트리밍 자연 종료 → `stream.end` 전송
3. 프론트엔드: `stream.end` 수신 → `_cancelled = false` 리셋

**v2 (향후)**: `stream_create(abort=asyncio.Event)` 활용

`abort` 파라미터는 v1에서 인터페이스만 확보하고 미사용.
향후 긴 텍스트 스트리밍이 추가되면 cancel 엔드포인트에서 `abort.set()`으로 스트림 즉시 중단.

---

## 7. 프론트엔드 변경 상세

### 7-1. 프론트엔드 현재 상태: 스트리밍 인프라 이미 존재

프론트엔드(`static/embedded.html`)에는 **실시간 스트리밍을 받을 준비가 이미 되어 있다**:

- `SE.appendChunk(id, ch)` — 텍스트 증분 append + 마크다운 렌더링
- `RD.render(msg)` — `status==='streaming'`일 때 cursor 애니메이션
- `autoScroll()` — 스트리밍 중 instant scroll, 완료 후 smooth scroll
- `_cancelled` 플래그 — cancel 후 chunk 무시

백엔드가 1회 chunk로 전체 텍스트를 보내고 있었을 뿐, 프론트엔드는 다수 chunk 수신에
이미 대응되어 있다. **스트리밍 자체를 위한 프론트엔드 변경은 최소한이다.**

### 7-2. 변경이 필요한 부분: result_data 선행 전송을 위한 DOM 구조

#### 문제

현재 `RD.render()`가 스트리밍 중 `bub.innerHTML = mdRender(msg.text) + cursor`로
**bubble 전체를 덮어쓴다.** result_data 테이블을 먼저 렌더링해도 다음 chunk에서 사라진다.

#### 해결: 테이블을 bubble 외부 슬롯에 배치

기존 DOM 구조:
```html
<div class="msg-content">
  <div class="thinking-slot"></div>
  <div class="progress-block-slot"></div>
  <div class="msg-bubble bot-bubble">    ← render()가 innerHTML 교체
    (텍스트 + 테이블 + 요약 전부 여기)
  </div>
</div>
```

변경 후 DOM 구조:
```html
<div class="msg-content">
  <div class="thinking-slot"></div>
  <div class="progress-block-slot"></div>
  <div class="result-data-slot"></div>    ← 신규: render()가 건드리지 않음
  <div class="msg-bubble bot-bubble">
    (스트리밍 텍스트만)
  </div>
  <div class="process-summary-slot"></div> ← 신규: render()가 건드리지 않음
</div>
```

**변경 이점**:
- `RD.render()`는 `msg-bubble`만 교체 → 테이블과 요약이 보존됨
- `renderResultTable()`의 삽입 대상만 `.result-data-slot`으로 변경
- `renderProcessSummary()`의 삽입 대상만 `.process-summary-slot`으로 변경
- 기존 중복 렌더링 가드(`if(slot.querySelector('.result-table-wrap')) return`)도 그대로 유효

### 7-3. 새 메시지 타입 핸들러: `result_data`

```javascript
// ED.handle() — switch에 추가
case 'result_data': return handleResultData(data);

// 새 함수
function handleResultData(data) {
    if (!_cur) return;
    var m = MS.get(_cur);
    if (!m) return;
    m.resultData = data.result_data;
    var row = chat().querySelector('[data-id="' + m.id + '"]');
    if (row) {
        var slot = row.querySelector('.result-data-slot');
        if (slot) RD.renderResultTable(slot, data.result_data);
    }
}
```

### 7-4. stream.end의 result_data 하위 호환

result_data를 `result_data` 메시지로 먼저 전송하되, **`stream.end`에도 동일하게 포함**한다.
`renderResultTable()`의 기존 중복 가드가 2번째 렌더링을 방지하므로 안전하다.

```python
# main.py — 전송 순서
await _safe_send({"type": "result_data", "result_data": pipeline_result.result_data})
await _safe_send({"type": "stream", "action": "start", "label": "답변 작성 중"})
# ... stream chunks ...
end_msg["result_data"] = pipeline_result.result_data  # 하위 호환용
await _safe_send(end_msg)
```

### 7-5. 프론트엔드 변경 요약

| 변경 | 위치 | 코드량 |
|------|------|:------:|
| `ensureDOM()`에 `.result-data-slot`, `.process-summary-slot` 추가 | embedded.html | ~5줄 |
| `renderResultTable()` 삽입 대상 변경 | embedded.html | ~3줄 |
| `renderProcessSummary()` 삽입 대상 변경 | embedded.html | ~3줄 |
| `handleResultData()` 함수 추가 | embedded.html | ~12줄 |
| `ED.handle()` switch에 `result_data` case 추가 | embedded.html | ~1줄 |

**변경하지 않는 것**:
- `SE.appendChunk()` — 그대로 사용
- `RD.render()` — 스트리밍 렌더링 로직 그대로
- `autoScroll()` — 그대로 사용
- `_cancelled` 플래그 — 그대로 사용
- CSS — 슬롯은 블록 요소로 자연스러운 흐름

---

## 8. 전송 순서 설계

### 8-1. 데이터 추출 경로 (최종안)

```
1. [progress]      phase: interpret → reason → present (기존 유지)
2. [result_data]   구조화 테이블 즉시 전송 (별도 메시지 타입)
3. [stream.start]  label: "답변 작성 중"
4. [stream.chunk]  LLM 인사이트 코멘트 토큰 스트리밍 (5토큰 단위 버퍼링)
5. [stream.chunk]  (반복)
6. [stream.end]    process_summary + insight + result_data(하위호환) + turn_id
7. [download_ready] CSV/JSON 다운로드 가능
```

### 8-2. 데이터 분석 경로 (최종안)

```
1. [progress]      phase: interpret → reason → present
2. [viz]           시각화 SVG 즉시 전송 (분석과 병렬 완료 시)
3. [result_data]   원본 데이터 테이블 전송
4. [stream.start]  label: "분석 결과 작성 중"
5. [stream.chunk]  LLM 인사이트 코멘트 토큰 스트리밍
6. [stream.chunk]  (반복)
7. [stream.end]    process_summary + insight + result_data(하위호환) + turn_id
8. [download_ready] CSV/JSON 다운로드 가능
```

### 8-3. 데이터 분석 — LLM 호출 병렬화

현재 3개 LLM 호출이 순차 실행되지만, Call #1(분석 JSON)과 Call #2→#3(시각화)은
입력이 동일(`sql_result`)하므로 병렬 실행 가능:

```python
# 현재 (순차)
analysis_result = await analyze_data(...)       # Call #1
viz_result = await build_visualization(...)     # Call #2→#3

# 개선 (병렬)
analysis_result, viz_result = await asyncio.gather(
    analyze_data(...),
    build_visualization(...),
)
```

**효과**: 분석(~3초) + 시각화(~4초) = 순차 7초 → 병렬 4초. 약 40% 지연 감소.
시각화가 먼저 완료되면 즉시 `viz` 메시지 전송 가능 → 사용자 체감 추가 개선.

---

## 9. 폐쇄망 환경 고려

### 9-1. 오픈소스 모델 스트리밍 호환성

| 모델 | 스트리밍 지원 | 비고 |
|------|:----------:|------|
| Solar Pro 2 70B (현재) | O | vLLM/TGI 서빙 시 SSE 스트리밍 지원 |
| Qwen3.5 397B (예정) | O | vLLM 호환 |
| GPT OSS 120B (예정) | O | 대부분의 서빙 프레임워크 지원 |

오픈소스 모델은 OpenAI 호환 API로 서빙되므로, `UnifiedLLMClient`에서
provider별 스트리밍 분기만 추가하면 된다.

### 9-2. 인사이트 코멘트 품질

2~3문장 데이터 해석 코멘트는 SQL 생성보다 난이도가 낮다:
- 입력: 질문 + 조회 결과 상위 N건
- 출력: 자연어 2~3문장
- 복잡한 추론 불필요, 패턴 인식 수준

Solar Pro 2 70B에서도 충분한 품질 기대 가능. 다만 프롬프트를 명확하고 구조화하여
모델 성능 차이에 대응해야 한다.

---

## 10. 리스크 및 트레이드오프

### 10-1. LLM 호출 추가에 따른 비용/지연

| 항목 | 현재 | 스트리밍 적용 후 |
|------|------|----------------|
| 데이터 추출 LLM 호출 | 0회 (formatter) | +1회 (인사이트 코멘트) |
| 데이터 분석 LLM 호출 | 3회 (analyzer) | 3회 + 1회 (인사이트 코멘트) |
| 추가 레이턴시 | — | 인사이트 코멘트 TTFB 0.5~1초 + 생성 1~3초 |
| 추가 토큰 비용 | — | ~200 토큰/요청 (코멘트 길이) |

**데이터 추출 경로에 LLM 1회가 추가**되지만, 테이블이 먼저 표시되므로
사용자 체감 대기 시간은 오히려 줄어든다 (테이블 확인하는 동안 코멘트 생성).

### 10-2. 스트리밍 실패 시 폴백

LLM 스트리밍이 실패(타임아웃, 네트워크 오류)하면:
- 인사이트 코멘트: 기존 `build_summary_line()` 템플릿으로 폴백
- result_data: LLM과 무관하게 이미 전송됨 → **데이터 전달은 항상 보장**

### 10-3. formatter의 rule-based 전환과의 관계

formatter에서 LLM을 제거한 이유는 "포맷팅에 LLM이 불필요"했기 때문이다.
인사이트 코멘트 생성은 포맷팅이 아닌 **데이터 해석**이므로 LLM이 적절하다.
역할이 다르므로 rule-based 전환의 취지와 충돌하지 않는다.

- **rule-based**: 숫자 포맷팅, 테이블 생성, 코드값 변환 → 정확성·결정론적
- **LLM**: "이 데이터가 의미하는 바" 해석 → 창의성·자연어

---

## 11. 구현 우선순위

| 순위 | 항목 | 효과 | 난이도 |
|:----:|------|------|:------:|
| 1 | `stream_create()` 메서드 추가 (client.py) | 모든 스트리밍의 기반 | 중 |
| 2 | 콜백 전달 (`callback_handler.on_custom_event` 확장) | 토큰 → WebSocket 경로 | 낮음 |
| 3 | result_data 선행 전송 + 프론트엔드 슬롯 분리 | 테이블 먼저 표시 | 낮음 |
| 4 | 데이터 추출 — 인사이트 코멘트 스트리밍 | 가장 빈번한 경로. UX 대폭 개선 | 중 |
| 5 | 데이터 분석 — LLM 병렬화 + viz 선행 전송 | 분석 경로 체감 지연 40% 감소 | 낮음 |
| 6 | 데이터 분석 — 인사이트 코멘트 스트리밍 | 데이터 추출과 동일 패턴 적용 | 낮음 |

---

## 12. 수정 대상 파일

| 파일 | 변경 내용 |
|------|-----------|
| `src/utils/llm/client.py` | `stream_create()` 메서드 추가 (Anthropic + OpenAI 호환 분기) |
| `src/utils/tracker/callback_handler.py` | `on_custom_event`에 `"stream.token"` 핸들러 추가 → `_on_event` 호출 |
| `src/agents/nodes/present/formatter.py` | 인사이트 코멘트 LLM 스트리밍 호출 추가 + 폴백 로직 |
| `src/agents/nodes/present/analyzer.py` | `asyncio.gather`로 분석+시각화 병렬화, viz 선행 전송 |
| `src/main.py` | result_data 선행 전송, `asyncio.Lock` 추가, 전송 순서 변경 |
| `static/embedded.html` | DOM 슬롯 분리, `handleResultData()` 추가 |
| `resources/prompts/present/insight_comment_system.txt` | 인사이트 코멘트 프롬프트 (신규) |
