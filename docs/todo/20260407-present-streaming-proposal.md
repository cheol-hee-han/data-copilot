# Present 구간 LLM 스트리밍 설계 제안서

> **작성일**: 2026-04-07
> **관련 문서**: `20260407-llm-streaming-review.md`, `20260407-websocket-response-restructuring.md`
> **목적**: Claude.ai 스트리밍 아키텍처를 벤치마크하여 Data Copilot present 구간의 최적 스트리밍 방안 도출

---

## 1. Claude.ai 스트리밍 아키텍처 벤치마크

### 1-1. 프로토콜: SSE (Server-Sent Events)

Anthropic API는 **HTTP/1.1 기반 SSE**를 사용한다 (WebSocket 아님).

```
POST /v1/messages  +  "stream": true
Content-Type: text/event-stream
```

- 데이터 블록은 `\r\n\r\n`으로 구분
- 각 이벤트: `event: <이름>\ndata: <JSON>` 형식
- `ping` 이벤트로 명시적 연결 유지(keepalive)

OpenAI 대비 더 작고 빈번한 delta를 전송하며, content block 단위로 구조화된 이벤트 시퀀스를 갖는다.

### 1-2. 이벤트 시퀀스 (완전 명세)

```
message_start                    ← 메시지 메타 (model, usage)
  ├─ content_block_start [0]     ← 블록 타입 선언 (text | tool_use | thinking)
  │    ├─ content_block_delta    ← 토큰 스트리밍 (text_delta | input_json_delta | thinking_delta)
  │    ├─ content_block_delta    ← (반복)
  │    ├─ ping                   ← keepalive (언제든 삽입)
  │    └─ content_block_stop     ← 블록 완료
  ├─ content_block_start [1]     ← 복수 블록 가능 (예: text → tool_use)
  │    ├─ content_block_delta
  │    └─ content_block_stop
  ├─ message_delta               ← stop_reason, 최종 usage
  └─ message_stop                ← 메시지 완료
```

### 1-3. Content Block Delta 타입

| delta 타입 | content_block 타입 | 용도 |
|---|---|---|
| `text_delta` | `text` | 일반 텍스트 응답 (마크다운 포함) |
| `input_json_delta` | `tool_use` | 도구 호출 파라미터 (partial JSON) |
| `thinking_delta` | `thinking` | Extended thinking 추론 과정 |
| `signature_delta` | `thinking` | 블록 무결성 검증 서명 (1회) |

**핵심 관찰**: Claude API는 마크다운, 코드, SVG 등을 모두 `text_delta`로 전송한다.
별도의 "테이블 블록"이나 "SVG 블록" 타입은 없다. 콘텐츠 구분은 **클라이언트 파서의 책임**이다.

### 1-4. Claude.ai의 마크다운 테이블 처리

Claude.ai는 마크다운 테이블을 `text_delta` 스트림으로 전송한다.

**렌더링 전략 (관찰 기반)**:
- 테이블 행이 도착할 때마다 점진적 렌더링 시도
- 단, `|` 파이프 문자 기반 파싱이 불완전할 때 깜빡임/깨짐 발생 (claude-code#14763, big-agi#963)
- 최종적으로 `message_stop` 이후 전체 마크다운을 한 번 더 파싱하여 확정 렌더링

**실질적 UX**: 테이블이 한 행씩 나타나는 것처럼 보이지만,
파싱 불안정으로 인해 "깜빡 → 재렌더링"이 발생하는 경우가 있다.
**완벽한 점진적 테이블 렌더링은 아직 해결된 문제가 아니다.**

### 1-5. Claude.ai의 SVG/시각화 처리 — Artifacts 패턴

Claude.ai는 SVG를 **Artifacts**라는 별도 메커니즘으로 처리한다.

```xml
<!-- text_delta 스트림 안에 XML 태그로 인라인 인코딩 -->
<antArtifact
  identifier="chart-1"
  type="image/svg+xml"
  title="월별 매출 추이">
  <svg viewBox="0 0 600 400">...</svg>
</antArtifact>
```

**렌더링 아키텍처**:
```
text_delta 스트림 → 클라이언트 XML 파서: <antArtifact> 태그 감지
    → 태그 완성 시점까지 버퍼링 (SVG는 불완전한 상태로 렌더링 불가)
    → window.postMessage()
    → Sandboxed iframe (claudeusercontent.com, 별도 도메인)
    → 즉시 렌더링
```

**핵심**: SVG는 구조적 특성상 **완성 후 일괄 표시**된다.
스트리밍 중에는 "차트를 생성하고 있어요..." 같은 placeholder만 보인다.

### 1-6. 지원 Artifact MIME 타입

| MIME type | 용도 |
|---|---|
| `image/svg+xml` | SVG 시각화 |
| `application/vnd.ant.react` | React 컴포넌트 (인터랙티브) |
| `text/html` | HTML 페이지 |
| `application/vnd.ant.mermaid` | Mermaid 다이어그램 |
| `application/vnd.ant.code` | 코드 스니펫 |
| `text/markdown` | 마크다운 문서 |

---

## 2. Claude.ai vs Data Copilot: 근본적 차이

Claude.ai 방식을 **그대로 적용할 수 없는** 구조적 차이가 있다.

| 관점 | Claude.ai | Data Copilot |
|------|-----------|-------------|
| **테이블 데이터 소스** | LLM이 마크다운 테이블을 생성 | SQL 실행 결과가 구조화 JSON으로 존재 |
| **테이블 정확성** | LLM 생성 → 환각 가능 | DB 결과 → 정확 (rule-based 포맷팅) |
| **시각화 소스** | LLM이 SVG/React 코드 생성 | LLM이 SVG 생성 (동일) |
| **스트리밍 대상** | 전체 응답 (텍스트+테이블+코드 혼합) | 텍스트 코멘트만 (테이블은 구조화 전송) |
| **프로토콜** | SSE (HTTP 스트리밍) | WebSocket (양방향, 이미 구축) |

### 핵심 판단

Data Copilot에서 마크다운 테이블을 LLM 스트리밍으로 보내는 것은 **역행**이다.
- SQL 결과는 이미 정확한 구조화 데이터로 존재
- LLM이 마크다운 테이블을 생성하면 숫자 환각, 포맷팅 불일치 위험
- `20260407-websocket-response-restructuring.md`에서 이미 `result_data` 구조화 전송으로 설계 확정

**→ Claude.ai에서 벤치마크할 것은 "테이블 스트리밍 방식"이 아니라,
"텍스트 코멘트 스트리밍 + 구조화 데이터 즉시 렌더링의 조합 패턴"이다.**

---

## 3. 제안: 하이브리드 스트리밍 아키텍처

### 3-1. 설계 원칙

Claude.ai의 Artifacts 패턴에서 착안:

```
텍스트 (자연어 해석/인사이트)  →  토큰 스트리밍 (text_delta 방식)
구조화 데이터 (테이블, 시각화)  →  완성 후 즉시 전송 (Artifact 방식)
```

이를 Data Copilot의 WebSocket 프로토콜로 매핑:

```
[progress]      →  파이프라인 진행 상태 (기존 유지)
[result_data]   →  SQL 결과 테이블 즉시 전송 (Artifact 패턴)
[viz]           →  SVG 시각화 즉시 전송 (Artifact 패턴)
[stream.chunk]  →  LLM 텍스트 토큰 스트리밍 (text_delta 패턴)
[stream.end]    →  메타데이터 확정 (message_stop 패턴)
```

### 3-2. 경로별 이벤트 시퀀스

#### 데이터 추출 경로

```
①  [progress]      phase: interpret → reason → present (기존)
②  [result_data]   구조화 테이블 즉시 전송
                    {"type": "result_data",
                     "columns": [...], "rows": [...],
                     "column_formats": {...},
                     "total_count": N, "displayed_count": M}
③  [stream.start]  {"type": "stream", "action": "start",
                     "label": "데이터를 해석하고 있어요"}
④  [stream.chunk]  LLM 인사이트 토큰 (여러 회)
                    {"type": "stream", "action": "chunk", "text": "강남지점이"}
⑤  [stream.chunk]  {"type": "stream", "action": "chunk", "text": " 523억원으로"}
    ...             (토큰 계속)
⑥  [stream.end]    {"type": "stream", "action": "end",
                     "status": "success",
                     "process_summary": {...},
                     "insight": {...},
                     "turn_id": "..."}
```

**프론트엔드 렌더링 순서**:
1. progress 바 표시
2. **② 수신 즉시**: 테이블 렌더링 (사용자가 데이터 확인 시작)
3. **③ 수신**: 테이블 아래에 커서 깜빡임 표시
4. **④⑤ 수신**: 한 글자씩 인사이트 코멘트 나타남
5. **⑥ 수신**: 코멘트 확정 + 조회 과정 요약(접기) 표시

**체감 UX**: 테이블이 먼저 보이고, 그 아래에서 AI가 해석을 "타이핑"하는 느낌.
Claude.ai에서 텍스트가 흘러나오는 것과 동일한 체감.

#### 데이터 분석 경로

```
①  [progress]      phase: interpret → reason → present
②  [viz]           SVG 시각화 즉시 전송 (기존 프로토콜)
③  [result_data]   원본 데이터 테이블 전송
④  [stream.start]  {"label": "분석 결과를 작성하고 있어요"}
⑤  [stream.chunk]  LLM 분석 보고서 토큰 (여러 회)
    ...
⑥  [stream.end]    process_summary + insight
```

**차이점**: 분석 경로는 시각화가 먼저 표시되고, 테이블과 분석 텍스트가 뒤따른다.

### 3-3. result_data 전송 시점 — stream.start 이전

Claude.ai의 Artifacts는 텍스트 스트림 **중간에** 삽입되지만,
Data Copilot에서는 result_data가 LLM과 무관하게 **SQL 실행 직후 확정**되어 있다.

따라서 LLM 스트리밍 시작(stream.start) **이전에** 별도 메시지로 전송하는 것이 최적이다:
- 테이블이 가장 빨리 표시됨 (LLM 응답 대기 없이)
- 사용자가 테이블을 보는 동안 LLM이 인사이트를 생성
- 프론트엔드 구현이 단순 (별도 메시지 핸들러 1개)

이는 `20260407-websocket-response-restructuring.md` 방법 1과 일치한다.

---

## 4. 백엔드 구현 설계

### 4-1. LLM 스트리밍 호출 — client.py 확장

```python
# src/utils/llm/client.py

async def stream_create(
    self,
    *,
    model: str,
    max_tokens: int,
    system: str,
    messages: list[dict],
) -> AsyncGenerator[str, None]:
    """LLM 토큰 스트리밍. text_delta만 yield한다."""
    if self._provider == "anthropic":
        async with self._client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
        ) as stream:
            async for text in stream.text_stream:
                yield text
    else:
        # OpenAI 호환 (vLLM, Groq 등)
        response = await self._client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "system", "content": system}] + messages,
            stream=True,
        )
        async for chunk in response:
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content
```

**폐쇄망 호환**: OpenAI 호환 API(vLLM/TGI)도 동일한 SSE 스트리밍을 지원하므로,
Solar Pro 2 / Qwen3.5에서도 동일 코드로 동작한다.

### 4-2. 콜백 기반 스트리밍 중계 — callback_handler.py 확장

기존 `callback_handler.py`에 이미 progress 이벤트 콜백 패턴이 있으므로,
LLM 스트리밍 이벤트를 동일 패턴으로 확장한다.

```python
# src/utils/tracker/callback_handler.py

class PipelineCallbackHandler:
    """파이프라인 이벤트 → WebSocket 중계."""

    async def on_stream_start(self, label: str) -> None:
        """LLM 텍스트 스트리밍 시작."""
        await self._emit({
            "type": "stream", "action": "start", "label": label,
        })

    async def on_stream_chunk(self, text: str) -> None:
        """LLM 토큰 1개 수신."""
        await self._emit({
            "type": "stream", "action": "chunk", "text": text,
        })

    async def on_stream_end(self, **metadata) -> None:
        """LLM 스트리밍 완료 + 메타데이터 전송."""
        await self._emit({
            "type": "stream", "action": "end", **metadata,
        })

    async def on_result_data(self, data: dict) -> None:
        """구조화 테이블 데이터 즉시 전송."""
        await self._emit({"type": "result_data", **data})
```

### 4-3. formatter 노드 — 인사이트 코멘트 스트리밍

```python
# src/agents/nodes/present/formatter.py

async def format_response_node(state: PipelineState) -> dict:
    callback = state.get("callback_handler")
    sql_result = state["sql_result"]

    # ── 1. 구조화 테이블 즉시 전송 (스트리밍 이전) ──
    result_data = build_result_data(state)
    if result_data and callback:
        await callback.on_result_data(result_data)

    # ── 2. LLM 인사이트 코멘트 스트리밍 ──
    if callback:
        await callback.on_stream_start("데이터를 해석하고 있어요")

    full_text = []
    async for token in llm_client.stream_create(
        model=settings.llm_model,
        max_tokens=300,
        system=INSIGHT_COMMENT_SYSTEM,
        messages=[{
            "role": "user",
            "content": INSIGHT_COMMENT_USER.format(
                query=state["preprocessed_input"],
                row_count=sql_result.row_count,
                columns=sql_result.columns,
                top_rows=sql_result.rows[:10],
            ),
        }],
    ):
        full_text.append(token)
        if callback:
            await callback.on_stream_chunk(token)

    insight_comment = "".join(full_text)

    # ── 3. 폴백: LLM 실패 시 rule-based 요약 ──
    if not insight_comment.strip():
        insight_comment = build_summary_line(
            sql_result.columns, sql_result.rows, column_formats,
        )

    # ── 4. process_summary 구조화 ──
    process_summary = build_process_summary(state)

    return {
        "formatted_response": insight_comment,
        "result_data": result_data,
        "process_summary": process_summary,
        "status": QueryStatus.FORMATTED,
    }
```

### 4-4. analyzer 노드 — 분석 보고서 스트리밍

```python
# src/services/data_analyzer.py — analyze_data() 내부

async def _generate_analysis_text(
    self, user_input: str, query_result: str, callback=None,
) -> str:
    """분석 텍스트를 LLM 스트리밍으로 생성한다."""
    if callback:
        await callback.on_stream_start("분석 결과를 작성하고 있어요")

    full_text = []
    async for token in self._llm.stream_create(
        model=settings.llm_model,
        max_tokens=2000,
        system=ANALYZER_SYSTEM,
        messages=[{
            "role": "user",
            "content": ANALYZER_USER.format(user_input, query_result),
        }],
    ):
        full_text.append(token)
        if callback:
            await callback.on_stream_chunk(token)

    return "".join(full_text)
```

**주의**: analyzer의 3회 LLM 호출 중 **분석 텍스트 생성만** 스트리밍한다.
시각화 판단(JSON)과 SVG 생성(코드)은 스트리밍 부적합 → 기존 `stream=False` 유지.

**실행 순서 최적화**:
```
1. 시각화 판단 (stream=False, JSON)  → viz 즉시 전송
2. SVG 생성 (stream=False, 코드)     → viz 즉시 전송
3. 분석 텍스트 (stream=True)          → 토큰 스트리밍
```

시각화를 먼저 보여주고, 분석 텍스트가 뒤따르면 체감 대기 시간이 최소화된다.

### 4-5. LangGraph 이벤트 vs 콜백: 최종 선택

| 항목 | 콜백 기반 (권장) | LangGraph astream_events |
|------|:---:|:---:|
| 기존 패턴 호환 | O (callback_handler 확장) | X (astream → astream_events 전환) |
| 노드 내부 제어 | O (전송 시점 정밀 제어) | △ (이벤트 필터링 필요) |
| result_data 선행 전송 | O (콜백에서 직접 호출) | X (LangGraph 이벤트에 해당 없음) |
| 폐쇄망 모델 호환 | O (provider별 분기 가능) | O (LangGraph 추상화) |
| 구현 복잡도 | 낮음 | 중간 |

**결론: 콜백 기반**. result_data 선행 전송, 시각화→텍스트 순서 제어 등
present 구간 특유의 "구조화 데이터 + 텍스트 스트리밍 혼합" 패턴에는
노드 내부에서 전송 시점을 직접 제어할 수 있는 콜백이 적합하다.

---

## 5. 프론트엔드 구현 설계

### 5-1. Claude.ai에서 배울 점: 스트리밍 마크다운 렌더링

Claude.ai의 text_delta 렌더링에서 확인된 베스트 프랙티스:

**금지 패턴**:
```javascript
// 매 토큰마다 전체 innerHTML 재파싱 — O(n²) 비용
element.innerHTML = marked.parse(accumulatedText); // NEVER
```

**권장 패턴 A: streaming-markdown 라이브러리**
```javascript
import { StreamingMarkdownParser } from 'streaming-markdown';

const parser = StreamingMarkdownParser.create(container);
ws.onmessage = (msg) => {
    if (msg.action === 'chunk') {
        parser.write(msg.text);  // append-only DOM 조작
    }
};
```

**권장 패턴 B: append 기반 (인사이트 코멘트처럼 짧은 텍스트에 적합)**
```javascript
// Data Copilot의 인사이트 코멘트는 2~3문장 (마크다운 복잡 구조 없음)
// → 단순 append로 충분
const commentEl = document.createElement('span');
commentEl.className = 'insight-comment streaming';
container.appendChild(commentEl);

ws.onmessage = (msg) => {
    if (msg.action === 'chunk') {
        commentEl.append(document.createTextNode(msg.text));  // O(1)
    } else if (msg.action === 'end') {
        // 스트리밍 완료 후 1회 마크다운 파싱 (볼드, 숫자 포맷 등)
        commentEl.innerHTML = marked.parse(commentEl.textContent);
        commentEl.classList.remove('streaming');
    }
};
```

**패턴 B 권장 이유**: Data Copilot의 인사이트 코멘트는 2~3문장 plain text에 가까우므로,
스트리밍 중에는 `append()`로 빠르게 표시하고, 완료 후 1회 마크다운 파싱이면 충분하다.
streaming-markdown 같은 외부 의존성을 추가할 필요가 없다.

### 5-2. 테이블 렌더링: Claude.ai와 다른 접근

Claude.ai는 마크다운 테이블을 text_delta로 스트리밍하면서 점진적 렌더링을 시도하지만,
파싱 불안정으로 깜빡임이 발생한다 (claude-code#14763).

**Data Copilot은 이 문제를 근본적으로 우회**한다:
- 테이블은 `result_data` (구조화 JSON)로 **완성된 상태로** 즉시 전송
- 프론트엔드에서 React/JS로 직접 렌더링 (마크다운 파싱 불필요)
- 정렬, 필터, CSV 복사 등 인터랙션 가능

이는 Claude.ai의 Artifacts 패턴과 동일한 철학이다:
**구조화 데이터는 스트리밍하지 않고, 완성 후 전용 렌더러로 표시한다.**

### 5-3. SVG 시각화 렌더링: Claude.ai Artifacts 패턴 적용

현재 Data Copilot의 `viz` 메시지는 이미 Artifacts 패턴과 유사하다:

```
Claude.ai:  <antArtifact type="image/svg+xml"> → 버퍼링 → iframe 렌더링
Data Copilot: {"type": "viz", "code": "<svg>..."} → 즉시 렌더링
```

**개선 가능 포인트**:

| 항목 | 현재 | Claude.ai 참조 개선안 |
|------|------|---------------------|
| 보안 | 메인 DOM에 직접 삽입 | iframe sandbox로 격리 (XSS 방어) |
| 에러 격리 | SVG 파싱 에러 시 전체 UI 영향 | iframe 내부에서만 실패 |
| 인터랙티브 | 정적 SVG | D3.js 번들 포함 iframe으로 확장 가능 |

**단, 폐쇄망 단일 HTML 환경에서 iframe sandbox는 구현 복잡도가 높다.**
우선순위가 낮으므로 Phase 2 이후 검토로 남긴다.

### 5-4. 스트리밍 UX 상세: 커서 애니메이션

Claude.ai의 "타이핑 중" UX를 구현한다:

```css
/* 스트리밍 중 커서 깜빡임 */
.insight-comment.streaming::after {
    content: '▋';
    animation: blink 0.7s infinite;
}

@keyframes blink {
    0%, 100% { opacity: 1; }
    50% { opacity: 0; }
}
```

```javascript
// stream.end 수신 시 커서 제거
commentEl.classList.remove('streaming');
```

### 5-5. 메시지 핸들러 변경 요약

```javascript
// static/embedded.html — handleMessage 확장

function handleMessage(msg) {
    switch (msg.type) {
        case 'result_data':
            // 신규: 구조화 테이블 즉시 렌더링
            renderResultTable(currentSlot, msg);
            break;

        case 'stream':
            if (msg.action === 'start') {
                // 텍스트 스트리밍 영역 생성 + 커서
                initStreamingArea(currentSlot, msg.label);
            } else if (msg.action === 'chunk') {
                // 토큰 append (marked 미사용)
                appendStreamToken(currentSlot, msg.text);
            } else if (msg.action === 'end') {
                // 스트리밍 완료:
                // 1. 커서 제거
                // 2. 최종 마크다운 파싱 (1회)
                // 3. process_summary 접기 렌더링
                // 4. insight 패널 활성화
                finalizeStream(currentSlot, msg);
            }
            break;

        case 'viz':
            // 기존 시각화 렌더링 (변경 없음)
            renderViz(currentSlot, msg);
            break;
    }
}
```

---

## 6. 분석 보고서 스트리밍 — 마크다운 복잡 구조 처리

데이터 분석 경로의 분석 보고서는 인사이트 코멘트(2~3문장)보다 길고,
마크다운 구조(헤딩, 볼드, 리스트)를 포함할 수 있다.

### 6-1. 두 가지 선택지

**방안 A: 완료 후 1회 파싱 (인사이트와 동일)**

```javascript
// 스트리밍 중: plain text append
appendStreamToken(slot, token);

// stream.end: 전체 마크다운 파싱
commentEl.innerHTML = marked.parse(commentEl.textContent);
```

- 장점: 구현 단순, 깜빡임 없음
- 단점: 스트리밍 중 마크다운 서식이 보이지 않음 (raw `**`, `#` 등 노출)
- 분석 텍스트가 길면(500+ 토큰) raw 마크다운 노출 시간이 체감됨

**방안 B: 디바운스 렌더링 (streaming-markdown 대안)**

```javascript
let debounceTimer = null;
let accumulated = '';

function appendStreamToken(slot, token) {
    accumulated += token;
    // DOM에는 plain text로 즉시 append
    slot.commentEl.append(document.createTextNode(token));

    // 200ms 디바운스: 토큰 유입 멈추면 마크다운 재파싱
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
        slot.commentEl.innerHTML = marked.parse(accumulated);
    }, 200);
}
```

- 장점: 토큰 간 간격(보통 30~100ms)에서는 plain text, 잠시 멈추면 서식 적용
- 단점: 재파싱 시 미세한 깜빡임 가능 (200ms 간격이라 체감 약함)
- 외부 라이브러리 불필요

**권장: 방안 A (데이터 추출) + 방안 B (데이터 분석)**

- 인사이트 코멘트(2~3문장): 방안 A로 충분 → plain text append + 완료 후 1회 파싱
- 분석 보고서(500+ 토큰): 방안 B로 중간 렌더링 제공 → 디바운스 마크다운 파싱

---

## 7. 폐쇄망 환경 고려

### 7-1. 오픈소스 모델 스트리밍 호환

| 모델 | 서빙 프레임워크 | 스트리밍 | API 호환 |
|------|---------------|:------:|---------|
| Solar Pro 2 70B | vLLM / TGI | O (SSE) | OpenAI 호환 |
| Qwen3.5 397B | vLLM | O (SSE) | OpenAI 호환 |
| GPT OSS 120B | vLLM | O (SSE) | OpenAI 호환 |

`stream_create()`의 OpenAI 호환 분기가 모든 모델을 커버한다.

### 7-2. 인사이트 코멘트 품질 — 모델별 프롬프트 전략

인사이트 코멘트(2~3문장 데이터 해석)는 SQL 생성보다 난이도가 낮다.
그러나 모델별 출력 스타일 차이에 대응이 필요하다:

```
[시스템 프롬프트 — 모든 모델 공통]
당신은 은행 데이터 분석가입니다.
SQL 조회 결과의 핵심 인사이트를 2~3문장으로 설명하세요.

규칙:
- 숫자를 나열하지 말고, 데이터가 의미하는 바를 해석
- 비교("전월 대비", "가장 높은") 관점으로 서술
- 금액은 억원/만원 단위, 비율은 %로 표기
- 마크다운 서식 사용 금지 (plain text만)

[사용자 프롬프트]
질문: {original_query}
조회 결과: {row_count}건
컬럼: {columns}
상위 데이터:
{top_rows_formatted}
```

**"마크다운 서식 사용 금지"** 지시로 방안 A(plain text append)의 raw 마크다운 노출 문제를 원천 차단.
인사이트 코멘트에서 마크다운이 필요할 만큼 복잡한 구조는 없다.

### 7-3. Qwen thinking 모드 대응

Qwen3.5의 `<think>` 태그는 현재 `client.py`에서 이미 제거하고 있다.
스트리밍에서도 동일하게 `<think>...</think>` 구간을 필터링해야 한다:

```python
# stream_create() 내부 — OpenAI 호환 분기
async for chunk in response:
    delta = chunk.choices[0].delta
    if delta.content:
        text = strip_thinking_tags(delta.content)
        if text:
            yield text
```

---

## 8. 구현 우선순위 (최종)

| 순위 | 항목 | 효과 | 난이도 | 의존성 |
|:---:|------|------|:-----:|-------|
| **1** | `result_data` 선행 전송 + 프론트 핸들러 | 테이블이 먼저 표시되어 체감 속도 개선 | 낮 | websocket-restructuring Phase 1 |
| **2** | 데이터 분석 — 분석 보고서 `stream=True` 전환 | 기존 LLM 호출에 스트리밍만 추가. 가장 빠르게 적용 가능 | 낮 | #1 |
| **3** | 데이터 추출 — 인사이트 코멘트 LLM 추가 + 스트리밍 | 가장 빈번한 경로. UX 대폭 개선. 단 LLM 호출 1회 추가 | 중 | #1 |
| **4** | 프론트 스트리밍 UX (커서, 디바운스 렌더링) | 체감 품질 향상 | 낮 | #2 또는 #3 |
| **5** | SVG iframe sandbox (선택) | 보안 강화 | 중 | 없음 |

### 수정 대상 파일

| 파일 | 변경 내용 |
|------|-----------|
| `src/utils/llm/client.py` | `stream_create()` 메서드 추가 |
| `src/utils/tracker/callback_handler.py` | `on_stream_start/chunk/end`, `on_result_data` 추가 |
| `src/agents/nodes/present/formatter.py` | result_data 선행 전송 + 인사이트 코멘트 LLM 스트리밍 |
| `src/services/data_analyzer.py` | 분석 텍스트 생성을 stream_create로 전환 |
| `src/main.py` | result_data 메시지 핸들러, stream.end 메타데이터 확장 |
| `static/embedded.html` | result_data 핸들러, 스트리밍 텍스트 append, 커서 UX |
| `resources/prompts/present/insight_comment_*.txt` | 인사이트 코멘트 프롬프트 (신규) |

---

## 9. Claude.ai 벤치마크 요약

| 관점 | Claude.ai 방식 | Data Copilot 적용 |
|------|---------------|------------------|
| **텍스트 스트리밍** | SSE `text_delta` → 토큰 단위 전송 | WebSocket `stream.chunk` → 동일 패턴 |
| **마크다운 테이블** | text_delta로 스트리밍 (파싱 불안정) | **적용하지 않음** — result_data로 완성 후 전송 (정확성 보장) |
| **SVG 시각화** | Artifacts: text_delta 안 XML 태그 → 완성 후 iframe 렌더링 | viz 메시지: 완성 후 즉시 렌더링 (동일 철학) |
| **구조화 데이터** | Artifacts (React/HTML/SVG) → sandboxed iframe | result_data (JSON) → JS 렌더러 |
| **커서 UX** | ▋ 깜빡임 → 완료 시 제거 | 동일 구현 |
| **DOM 업데이트** | append 기반 (streaming-markdown) | append 기반 (외부 의존성 없이 구현) |
| **보안** | Artifacts → claudeusercontent.com iframe | viz → 현재 메인 DOM (향후 iframe 검토) |

**핵심 교훈**: Claude.ai에서 마크다운 테이블 스트리밍이 완벽하지 않다는 점이
Data Copilot의 "구조화 JSON 즉시 전송" 결정을 더욱 뒷받침한다.
LLM 스트리밍은 **자연어 텍스트에만** 적용하고,
구조화 데이터는 **완성 후 전용 렌더러로** 표시하는 것이 최적이다.
