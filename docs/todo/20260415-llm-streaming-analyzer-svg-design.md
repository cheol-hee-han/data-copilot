# LLM 스트리밍 설계 — Analyzer 내러티브 + Visualization SVG

작성: 2026-04-15
개정:

- v2 (2026-04-15): pipeline-designer / research-analyst / code-reviewer 3중 리뷰 반영
- v3 (2026-04-15): prompt-engineer 리뷰, Claude 방식 채택(sentinel 폐기), 사용자 UI 토글
- v4 (2026-04-15): IBK 범위 제외, 기존 구현 일관성 반영
- v5 (2026-04-15): 덧붙이기 제거 전면 재정리 — 책임 소속 정리, 근본 수정, 직교 축 분리, PR 분할, API 대칭성 회복
- v6 (2026-04-15): **프론트 협의 결과 반영** — 프로토콜 네이밍을 Anthropic/Vercel AI SDK 표준(`llm_delta`, `event`, `part_id`/`part_type`) 에 정합, 설정을 user_message 봉투로 이관, 취소 프로토콜 명시, UI 토글을 턴 단위 헤더 버튼으로 확정, Incremark + requestAnimationFrame 채택

범위: analyzer 내러티브 + SVG 두 경로에 한정한 실제 LLM 토큰 스트리밍. IBK 커스텀 게이트웨이는 본 범위 제외.

---

## 0. 설계 원칙 (v5 에서 명시화)

v4 까지 누적된 덧붙이기를 제거하기 위해 다음 원칙을 선언한다. 이후 모든 의사결정은 이 원칙에 정합해야 한다.

1. **책임 소속 정합성**: 필드/분기는 "도메인 모델 vs 파이프라인 상태기계 vs UI 전달 메타" 중 의미가 맞는 곳에 둔다.
2. **근본 수정 우선**: 잠재 버그를 우회하는 조건부 분기 대신 버그를 직접 수정한다.
3. **직교 축 분리**: 독립적인 두 관심사를 한 플래그에 묶지 않는다 (본 설계에서 특히 *출력 포맷* ⟂ *전송 방식*).
4. **API 대칭성**: 동일 객체의 쌍(`create` ⟂ `stream`)은 같은 내부 primitive 로 구현한다.
5. **PR 단위 리뷰 가능성**: cross-cutting 변경은 분할한다.

---

## 1. 배경

현재 WebSocket `type:"stream"` 은 formatter 완성 텍스트를 단일 청크로 전송하는 **의사 스트리밍**. LLM 은 토큰을 SSE 로 흘려보낼 수 있으나 다음 두 지점에서 버퍼링된다:

1. `llm_call_with_parse_retry()` 가 분석 응답 전체 수신 후 파싱.
2. `generate_svg_via_llm()` 이 `client.messages.create()` 동기 호출로 XML 전체 반환.

본 설계는 이 두 지점을 토큰 단위로 사용자에게 흘려보내도록 재구성한다.

---

## 2. 범위

### 구현 대상

| # | 경로 | 소스 | 산출물 | 비고 |
|---|---|---|---|---|
| A | `data_analyzer.analyze_data()` | LLM | summary / initial_reading / insights / action_items | DATA_ANALYSIS 인텐트 |
| B | `data_analyzer.generate_svg_via_llm()` | LLM | SVG XML | analyzer 내부 조건부 호출 |

### 제외 (본 범위 외)

- IBKCustomMessages 스트리밍 — 게이트웨이 SSE 스펙 확정 후 별도 PR
- DATA_EXTRACTION 요약 LLM 화 — 별도 문서
- formatter 룰 조립, `judge_visualization`, recovery/intent/context 노드

### 프로바이더 지원

| 프로바이더 | stream() 구현 | 비고 |
|---|---|---|
| Anthropic | ✅ | `messages.stream()` context manager + event loop |
| OpenAI-compatible | ✅ | `chat.completions.create(stream=True, stream_options={"include_usage": True})` |
| IBK Custom | ❌ | 본 범위 제외. `_should_stream()` provider 가드로 차단 → 기존 `create()` 경로만 동작 |

---

## 3. 핵심 결정 (§0 원칙에 정합)

### 결정 #1. 출력 포맷과 전송 방식을 직교 축으로 분리 (원칙 #3)

v4 의 "streaming ↔ markdown / non-streaming ↔ JSON" 결합을 해제한다.

- **출력 포맷 축**: JSON → Markdown 전환 (4 섹션 `## 핵심 요약 / ## 데이터 현황 / ## 분석 인사이트 / ## 후속 조치`).
- **전송 방식 축**: `_should_stream()` 이 **chunk 방출 여부만** 결정.

두 축은 서로 독립. 조합은 네 가지 중 본 설계가 채택하는 것:

| 포맷 | 전송 | 상태 |
|---|---|---|
| JSON | 수집 | 기존 (PR3~PR7 동안 회귀 안전망) |
| Markdown | 수집 | **신규. streaming OFF 시 기본 경로** |
| Markdown | 스트림 | **신규. streaming ON 시 경로** |
| JSON | 스트림 | 불사용 (가치 없음) |

즉 PR3 부터는 **Markdown 이 프라이머리 포맷**이고, 전송 방식은 별도 스위치. JSON 은 PR8 에서 A/B 통과 후 삭제.

### 결정 #2. analyzer thinking 모드: 무조건 `high` (원칙 #2)

- 기존 `AnthropicMessages.create()` 가 `response.content[0].text` 를 가정하는 **잠재 버그** 를 근본 수정: text 타입 블록만 연결(`"".join(b.text for b in content if b.type=="text")`).
- 수정 후 `NODE_THINKING_MODES["analyzer"]="high"` 를 **전송 방식과 무관하게** 적용. 분석 품질은 delivery 와 독립.
- streaming 경로/non-streaming 경로 모두 동일한 thinking 설정 → 조건부 분기 제거.

### 결정 #3. SVG: 전송은 스트리밍, 렌더는 원자적

백엔드는 토큰 스트리밍, 프론트는 `event:"end"` 수신 시점에 일괄 렌더.

### 결정 #4. WebSocket 프로토콜: `type:"llm_delta"` 분리 (Anthropic/Vercel AI SDK 표준 정합)

- 네이밍: `action` → `event`, `section` → `part_id`+`part_type`, `turn_id` 추가 (취소 연결). Anthropic SSE (`content_block_delta` + `index`) 및 Vercel AI SDK v5 (`start`/`delta`/`end` 라이프사이클) 표준에 정합.
- `part_id` 로 병렬 블록 확장성 확보 (향후 4 섹션 독립 스트리밍, 복수 SVG 등).
- 기존 `type:"stream"` 은 formatter 최종 응답용으로 유지. 구프론트 보호를 위해 PR3 에서 "알 수 없는 type 무시" 방어코드 선배포.

### 결정 #5. UI 전달 메타는 PipelineState 로 (원칙 #1)

`streaming_delivered: bool` 을 **`PipelineState`** 에 둔다 (v4 의 `AnalysisResult.body_streamed` 철회).
- 이유: "내러티브를 이미 stream 으로 보냈는가" 는 분석 결과의 속성이 아니라 파이프라인 상태기계의 속성.
- formatter 분기 조건이 `state.streaming_delivered` 로 의미가 명료해짐.

### 결정 #6. 활성화 제어: 서버 Flag + UI 토글(턴 단위) + 프로바이더 판정

최종 조건: `server_flag AND user_turn_toggle AND provider in {anthropic, openai_compatible}`.

- 서버 flag `LLM_STREAMING_ENABLED` (PR 초기 false, PR8 true 전환).
- **UI 토글**: 헤더 상단의 다크/라이트 모드 토글 **옆에 배치**. 매 턴마다 사용자가 즉시 ON/OFF 가능.
- **전달 방식**: 별도 control 메시지 폐기. 사용자 메시지 봉투에 `streaming_enabled` 동봉 (경쟁조건 제거).
- 턴 중간 토글 변경: 이미 시작된 턴은 완료까지 유지, 다음 턴부터 반영. UI 에 "현재 응답 완료 후 적용됩니다" 토스트.
- 프로바이더 판정으로 IBK 차단.

### 결정 #7. CircuitBreaker API 대칭성 (원칙 #4)

- `CircuitBreaker` 에 `@asynccontextmanager guard()` 를 일반화 primitive 로 추가.
- 기존 `call(coro_factory)` 는 내부적으로 `guard()` 를 호출하도록 재구현 (외부 API 보존).
- 신규 `stream_guard()` 또한 `guard()` 재사용. "성공 판정 = 정상 종료" 단일 정책.

---

## 4. 컴포넌트별 변경

### 4.1 `src/utils/llm/client.py` — 어댑터 + 근본 수정

#### 근본 수정 (결정 #2)

`AnthropicMessages.create()` 가 content 리스트에서 **text 타입 블록만 안전 추출**하도록 수정:

```python
def _extract_text(content_blocks: list[Any]) -> str:
    return "".join(
        b.text for b in content_blocks
        if getattr(b, "type", None) == "text"
    )
```

- 기존 `response.content[0].text` 접근은 extended thinking 활성 시 ThinkingBlock 을 만나 AttributeError 유발 (잠재 버그). 본 수정으로 thinking block 이 있든 없든 안전.
- 반환하는 `LLMResponse.content` 는 기존 호출부 호환을 위해 `[TextBlock(text=_extract_text(...))]` 형태 유지.

#### 스트리밍 인터페이스

```python
@dataclass
class StreamEvent:
    kind: Literal["text", "thinking"]
    text: str

async def stream(self, *, model, max_tokens, system=None, messages,
                 timeout=None, temperature=None, thinking=None,
                 ) -> AsyncIterator[StreamEvent]: ...
```

- **AnthropicMessages.stream**: `async with self._client.messages.stream(**kwargs) as s:` 이벤트 루프. `content_block_delta` 의 `text_delta` → `kind="text"`, `thinking_delta` → `kind="thinking"`. `message_delta.usage.output_tokens` 누적.
- **OpenAICompatibleMessages.stream**: `create(stream=True, stream_options={"include_usage": True})`. `chunk.choices[0].delta.content` → text. Qwen `<think>...</think>` 는 **경계 상태머신 필터** 로 text/thinking 분리 (토큰 경계에 태그가 쪼개질 수 있음). reasoning 모델(DeepSeek-R1 등)의 `delta.reasoning_content` → thinking (선택적).
- **IBKCustomMessages.stream**: 미구현 — `_should_stream()` 에서 차단.

#### Qwen `<think>` 경계 상태머신 (의사코드)

토큰이 `<think>` / `</think>` 태그 경계를 가로질러 쪼개질 수 있으므로, 단순 `str.replace` 는 불가. 아래 상태머신으로 안전 분리:

```python
_OPEN = "<think>"
_CLOSE = "</think>"

class QwenThinkFilter:
    def __init__(self) -> None:
        self._buf = ""           # 부분 태그 hold 버퍼
        self._in_think = False   # 현재 thinking 영역인가

    def feed(self, chunk: str) -> list[StreamEvent]:
        """chunk 1개를 StreamEvent 리스트로 변환 (0개 이상)."""
        self._buf += chunk
        out: list[StreamEvent] = []
        while self._buf:
            tag = _CLOSE if self._in_think else _OPEN
            idx = self._buf.find(tag)
            if idx >= 0:
                head = self._buf[:idx]
                if head:
                    kind = "thinking" if self._in_think else "text"
                    out.append(StreamEvent(kind, head))
                self._buf = self._buf[idx + len(tag):]
                self._in_think = not self._in_think
                continue
            # 태그 미발견: 부분 일치 접미사가 있으면 hold, 아니면 flush
            hold = _tag_prefix_len(self._buf, tag)
            if hold < len(self._buf):
                kind = "thinking" if self._in_think else "text"
                out.append(StreamEvent(kind, self._buf[:-hold] if hold else self._buf))
                self._buf = self._buf[-hold:] if hold else ""
            break
        return out

    def flush(self) -> list[StreamEvent]:
        """스트림 종료 시 잔여 버퍼 방출."""
        if not self._buf:
            return []
        kind = "thinking" if self._in_think else "text"
        ev = [StreamEvent(kind, self._buf)]
        self._buf = ""
        return ev


def _tag_prefix_len(buf: str, tag: str) -> int:
    """buf 의 접미사 중 tag 의 접두사와 일치하는 최대 길이."""
    for n in range(min(len(buf), len(tag) - 1), 0, -1):
        if tag.startswith(buf[-n:]):
            return n
    return 0
```

- "부분 일치 hold": 버퍼 끝이 `<`, `<t`, `<th`, `<thi`, `<thin`, `<think` 중 하나면 다음 청크까지 hold.
- 태그 완전 매치 시 모드 전환 + 태그 자체는 방출 금지.
- Qwen 모델이 아닌 경우 필터 미적용 (`model.startswith("qwen")` 가드).

종료 시 누적 텍스트·토큰·레이턴시로 `LLM_CALL` 이벤트 **1회** 방출 (기존 `create()` 경로와 동일 추적).

### 4.2 `src/utils/llm/circuit_breaker.py` — guard primitive 일반화 (결정 #7)

```python
@asynccontextmanager
async def guard(self) -> AsyncIterator[None]:
    """OPEN 이면 CircuitOpenError. 블록 정상 종료 시 _on_success, 예외 시 _on_failure."""
    async with self._lock:
        self._maybe_transition_to_half_open()
        if self._state is CircuitState.OPEN:
            raise CircuitOpenError(...)
    try:
        yield
    except self.counted_excs as exc:
        await self._on_failure(exc); raise
    except Exception as exc:
        if _is_server_side_status_error(exc):
            await self._on_failure(exc)
        raise
    else:
        await self._on_success()
```

`call(coro_factory)` 는 `async with self.guard(): return await coro_factory()` 로 재구현.

`_CircuitBreakerMessages`:

```python
async def create(self, **kw):
    async with self._breaker.guard():
        return await self._inner.create(**kw)

async def stream(self, **kw):
    async with self._breaker.guard():
        async for ev in self._inner.stream(**kw):
            yield ev
```

예외로 guard 를 빠져나가면 자동 실패 처리. 정상 종료면 성공. 스트림의 경우 이터레이션 완료가 정상 종료.

### 4.3 `src/agents/state/state.py` — PipelineState 확장 (결정 #5)

```python
class ClientSettings(BaseModel):
    streaming_enabled: bool = True

class PipelineState(BaseModel):
    ...
    client_settings: ClientSettings = Field(default_factory=ClientSettings)
    streaming_delivered: bool = False  # 내러티브를 stream 채널로 이미 송출했는가
```

기존 checkpoint 는 두 필드 모두 기본값으로 자동 채워짐 (Pydantic).

### 4.4 `src/models/result.py` — 정리

- `AnalysisResult.statistics` **제거** (dead field; prod consumer 0개, test 2건만 수정).
- `AnalysisResult.body_streamed` **도입하지 않음** (v4 철회, state 로 이관).
- `AnalysisResult.reasoning_summary` **유지** (JSON 경로 호환; Markdown 경로에서는 빈 문자열).

### 4.5 `src/utils/tracker/dispatch.py` — 이벤트 상수

```python
LLM_DELTA_START = "llm.delta.start"   # {part_id, part_type, turn_id, node}
LLM_DELTA_CHUNK = "llm.delta.chunk"   # {part_id, text, turn_id, node}
LLM_DELTA_END   = "llm.delta.end"     # {part_id, turn_id, node, cancelled?, error?, error_code?}
LLM_DELTA_RESET = "llm.delta.reset"   # {part_id, turn_id, node, reason}
```

- `part_id` 예: `"analysis_0"`, `"svg_0"` (턴 내 유일).
- `part_type ∈ {"analysis", "svg"}` — 렌더러 선택에 사용 (Markdown vs SVG 원자).
- `kind="thinking"` 청크는 사용자 채널 비방출, 내부 트레이스만.

### 4.6 `src/utils/tracker/callback_handler.py` — WS 라우팅

`on_custom_event::case "llm"` 분기에 위 4 이벤트를 다음 형태로 `_on_event` 방출:

```jsonc
{"type":"llm_delta","turn_id":"t_001","part_id":"analysis_0","part_type":"analysis","event":"start"}
{"type":"llm_delta","turn_id":"t_001","part_id":"analysis_0","part_type":"analysis","event":"delta","text":"..."}
{"type":"llm_delta","turn_id":"t_001","part_id":"analysis_0","event":"reset","reason":"llm_retry"}
{"type":"llm_delta","turn_id":"t_001","part_id":"analysis_0","event":"end","cancelled"?:bool,"error"?:bool,"error_code"?:"..."}
```

### 4.7 `src/utils/llm/retry.py` — 파라미터 확장

```python
async def llm_call_with_parse_retry(
    *, system, messages, parse_fn, max_tokens=None, timeout=None,
    max_retries=None, node_name="", temperature=None, thinking=None,
    stream_callback: Callable[[str], Awaitable[None]] | None = None,
    stream_section: str | None = None,           # "analysis" | "svg"
    is_cancelled: Callable[[], Awaitable[bool]] | None = None,
) -> tuple[str, T]:
```

- `stream_callback=None` → 기존 `create()` 경로 (무변경).
- 지정 시 → `client.messages.stream()` 사용. `kind="text"` 만 콜백 호출 + `LLM_STREAM_CHUNK` 방출. `kind="thinking"` 은 내부 버퍼·트레이스.
- `is_cancelled` 32 토큰 / 100ms 주기 체크 → True 시 break + `LLM_STREAM_END(cancelled=True)`.
- 파싱 실패 시 `LLM_STREAM_RESET(reason="parse_error")` 방출 후 교정 메시지로 재시도.
- 최종 실패 → `ParseError` raise → 호출자가 기존 텍스트 폴백.

### 4.8 `src/services/data_analyzer.py` — Markdown 일원화 (결정 #1)

#### 프롬프트

- `resources/prompts/present/analyzer_system.txt` 를 **Markdown 4 섹션** 출력으로 전환 (JSON 버전은 `analyzer_system_legacy_json.txt` 로 rename, 회귀 안전망).
- `ANALYZER_SYSTEM_MARKDOWN` / `ANALYZER_SYSTEM_LEGACY_JSON` 두 상수.
- 플래그: `settings.analyzer_output_format: Literal["markdown","json"] = "markdown"` (기본 markdown; PR3 배포 시 false=json 으로 1회 회귀 테스트 가능, A/B 통과 후 flag 제거).

#### 파서 세부 스펙

`parse_analysis_markdown(text: str) -> AnalysisResult` 상세:

**헤더 정규식** (엄격):

```python
_SECTION_RE = re.compile(
    r"^##[ \t]+(핵심 요약|데이터 현황|분석 인사이트|후속 조치)[ \t]*$",
    re.MULTILINE,
)
```

- `^##` 뒤에 반드시 공백 1개 이상. `###` 등 다른 레벨은 섹션 헤더로 간주하지 않음.
- ZWSP(`\u200b`) 가 섞여 있으면 비매칭 → 인젝션 방어.
- 섹션 제목은 정확히 4개 중 하나. 오탈자 불허.

**파싱 알고리즘**:

1. `_SECTION_RE.finditer(text)` 로 헤더 위치 수집.
2. 헤더 사이 본문을 섹션별 dict 에 저장. 4개 섹션 모두 초기값 `""`.
3. **`핵심 요약` 섹션 누락 시 `ValueError("필수 섹션 '핵심 요약' 누락")`** → retry 루프가 교정 재시도.
4. 나머지 3개 섹션 누락은 빈 리스트 허용 (LLM 이 해당 영역이 비어있다고 판단한 경우).

**불릿 파싱** (`데이터 현황`, `분석 인사이트`, `후속 조치` 대상):

```python
_BULLET_RE = re.compile(r"^[ \t]*(?:[-*]|\d+\.)[ \t]+(.+?)[ \t]*$", re.MULTILINE)
```

- `- foo` / `* foo` / `1. foo` / `  - foo` (들여쓰기) 모두 허용.
- 연속된 빈 줄은 무시. 불릿이 없으면 본문 전체를 단일 문자열로 `summary` 류 필드에 저장하지 않고 리스트는 빈 리스트.

**AnalysisResult 매핑**:

| 섹션 | 필드 | 타입 |
|---|---|---|
| 핵심 요약 | `summary` | `str` (여러 줄 trim + `\n\n` 정규화) |
| 데이터 현황 | `initial_reading` | `list[str]` (불릿 목록) |
| 분석 인사이트 | `insights` | `list[str]` (불릿 목록) |
| 후속 조치 | `action_items` | `list[str]` (불릿 목록) |
| — | `reasoning_summary` | `""` (Markdown 경로에서는 공란) |

**에지 케이스**:

- 헤더 중복 등장 시: 마지막 등장 본문 채택, WARN 로그.
- 본문 시작 전 preamble 텍스트(첫 헤더 이전): 무시.
- 코드펜스(` ``` `) 내 `##` 는 헤더 취급 금지 → 본문 파싱 전 코드펜스 구간을 마스킹한 뒤 헤더 위치 탐색.

`parse_analysis_json` 은 legacy 로 유지, `analyzer_output_format=="json"` 일 때만 사용.

#### 프롬프트 인젝션 방어 — `^##` 이스케이프

`format_report_table()` 출력의 셀 중 `"## "` / `"###"` 로 시작하는 값 앞에 `\u200b` (ZWSP) 삽입. LLM 은 ZWSP 를 무시하므로 JSON 경로에도 무해, Markdown 파서는 엄격 매칭으로 ZWSP 포함 `##` 를 헤더로 인정하지 않음.

#### analyze_data() 스트리밍 분기

```python
stream_cb = None
if _should_stream(state):
    async def stream_cb(chunk: str):
        await dispatch_tracking_event(LLM_DELTA_CHUNK,
            {"part_id":"analysis_0", "text":chunk,
             "turn_id":state.turn_id, "node":"analyze_data"})

parse_fn = (parse_analysis_markdown
            if settings.analyzer_output_format == "markdown"
            else parse_analysis_json)
system_prompt = (ANALYZER_SYSTEM_MARKDOWN
                 if settings.analyzer_output_format == "markdown"
                 else ANALYZER_SYSTEM_LEGACY_JSON)

_, analysis = await llm_call_with_parse_retry(
    system=system_prompt, messages=..., parse_fn=parse_fn,
    stream_callback=stream_cb, stream_section="analysis",
    is_cancelled=_is_cancelled,
    thinking="high",   # 결정 #2: 전송 방식 무관 항상 high
    node_name="데이터분석",
)
```

analyzer_node 반환에 `streaming_delivered = stream_cb is not None and analysis is not None` 포함 → state 업데이트.

#### analyzer_node 진입 시 state 초기화 (체크포인트 엣지 케이스 방어)

LangGraph CheckPointer 가 이전 턴 state 를 복원하는 경우 `streaming_delivered=True` 가 잔존할 위험. analyzer_node 진입부에서 명시 초기화:

```python
async def analyze_data_node(state: PipelineState) -> dict:
    # 새 턴 시작: 스트리밍 전달 플래그는 이 턴 범위. 체크포인트 잔존값 제거.
    updates: dict[str, Any] = {"streaming_delivered": False}
    ...
    # 이후 스트리밍 실제 수행 시 updates["streaming_delivered"] = True 덮어씀
    return updates
```

- visualize 경로에도 동일 원칙 적용 (SVG 스트리밍이 첫 part 인 케이스 대비).
- `client_settings` 는 턴 시작 시 main.py 가 `run_pipeline(client_settings=...)` 으로 주입하므로 노드에서 재초기화 불필요.

#### generate_svg_via_llm 스트리밍

`parse_svg` 정규식 추출 재사용. 스트리밍 시 chunk 방출 + `LLM_STREAM_END(section="svg")` → `Visualization.svg_code` 기록.

### 4.9 `src/agents/nodes/present/formatter.py` — streaming_delivered 분기 (결정 #5)

```python
if state.streaming_delivered:
    formatted = ""
elif has_analysis:
    formatted = build_analysis_report(analysis)
elif analysis and analysis.summary:
    formatted = analysis.summary
else:
    formatted = build_summary_line(...)
```

`result_data`, `process_summary`, `visualization` 는 기존과 동일하게 채움. 빈 `formatted_response` 는 main.py stream.start/chunk/end 경로에서 빈 chunk 로 안전 처리 (UI `setBusy(false)` 는 stream.end 가 보장).

### 4.10 `src/agents/nodes/present/analyzer.py` — reasoning_summary 경고 정리

기존 line 102-105 누락 경고는 **Markdown 포맷에서는 항상 빈 값**. 경고 조건을 `analyzer_output_format == "json"` 일 때로 한정.

### 4.11 `src/main.py` — 프로토콜 + 세션 설정 영속화

#### 프로토콜

```jsonc
// C→S: 사용자 메시지 (봉투에 설정 포함)
{"type":"user_message","text":"...","streaming_enabled":true,"turn_id":"t_001"}

// C→S: 취소 요청
{"type":"cancel","turn_id":"t_001"}

// S→C: LLM 토큰 스트림 (신규)
{"type":"llm_delta","turn_id":"t_001","part_id":"analysis_0","part_type":"analysis","event":"start"}
{"type":"llm_delta","turn_id":"t_001","part_id":"analysis_0","part_type":"analysis","event":"delta","text":"..."}
{"type":"llm_delta","turn_id":"t_001","part_id":"analysis_0","event":"reset","reason":"llm_retry"}
{"type":"llm_delta","turn_id":"t_001","part_id":"analysis_0","event":"end","cancelled"?:bool,"error"?:bool,"error_code"?:"..."}

// S→C: 기존 formatter 의사 스트리밍 (비-DATA_ANALYSIS / 폴백, 유지)
{"type":"stream","action":"start"|"chunk"|"end", ...}
```

#### 턴 단위 설정 주입 (세션 영속화 제거)

별도 `client_settings` control 메시지 경로는 경쟁조건 우려로 폐기. 사용자 메시지 봉투에서 직접 추출:

```python
parsed = json.loads(raw)
if parsed.get("type") == "cancel":
    await cancel_turn(session_id, parsed["turn_id"])
    continue

if parsed.get("type") == "user_message":
    user_text = parsed["text"]
    turn_settings = ClientSettings(
        streaming_enabled=parsed.get("streaming_enabled", True),
    )
    await _run_ws_pipeline(
        user_text, session_id, websocket,
        client_settings=turn_settings,
    )
```

하위 호환: 기존 plain text / `{"text": "..."}` 포맷도 계속 지원 (fallback 시 `streaming_enabled=True`).

#### turn_id 생성 위치 및 cancel 매칭 규약

**생성 위치** — 클라이언트 주도:

- 클라이언트가 `crypto.randomUUID()` 로 생성 후 `user_message.turn_id` 에 동봉.
- 서버는 수신한 `turn_id` 를 **그대로** 사용 (재발급 금지). 동일 `turn_id` 를 `llm_delta` / `stream` 응답에 echo.
- 클라이언트가 `turn_id` 를 보내지 않은 하위 호환 입력: 서버가 `uuid4()` 로 대체 생성하되, 이 턴은 cancel 불가 (토스트: "이 메시지는 중단 기능이 지원되지 않습니다").

**서버 측 ActiveRunStore 연동**:

```python
# 턴 시작 시 등록
await active_run_store.register(session_id, turn_id, task=asyncio.current_task())
try:
    await run_pipeline(...)
finally:
    await active_run_store.unregister(session_id, turn_id)

# cancel 수신 시 검증 + 태스크 취소
async def handle_cancel(session_id: str, turn_id: str) -> None:
    task = await active_run_store.get(session_id, turn_id)
    if task is None:
        logger.info("cancel: 해당 turn 미존재 (이미 완료/다른 턴)",
                    session_id=session_id, turn_id=turn_id)
        return
    await cancel_store.set(session_id, turn_id)  # is_cancelled() polling 용
    # 강제 태스크 취소는 하지 않음 — 32토큰/100ms 폴링으로 협조적 종료 유도
```

- cancel 의 `turn_id` 가 현재 진행 중인 턴과 **불일치**하면 무시 (레이스: 사용자가 이전 턴 cancel 버튼을 새 턴 시작 후 누름).
- `is_cancelled()` 구현: `cancel_store.has(session_id, current_turn_id)`. 32 토큰 / 100ms 주기 체크.
- 턴 종료 시 `cancel_store` 에서 해당 `turn_id` entry 도 함께 삭제 (메모리 누수 방지).

**단일 세션 동시 턴 정책**: 서버는 세션당 1개 턴만 진행. 새 `user_message` 수신 시 직전 턴이 진행 중이면:

- 옵션 A (채택): 직전 턴을 자동 cancel → ActiveRunStore 에서 발견 시 `cancel_store.set()` → 자연 종료 대기 후 새 턴 시작.
- 옵션 B (기각): 신규 턴 거부 → UX 저하.

#### `_should_stream()` — 단일 판정점

```python
_STREAMING_PROVIDERS = {"anthropic", "openai_compatible"}

def _should_stream(state: PipelineState) -> bool:
    return (
        settings.llm_streaming_enabled
        and state.client_settings.streaming_enabled
        and settings.llm_provider.lower() in _STREAMING_PROVIDERS
    )
```

#### stream.start 라벨 조건부

`state.streaming_delivered=True` 시 main.py 가 `stream.start` 의 `label` 을 `"답변 정리 중"` 으로 변경하거나 생략.

### 4.12 프론트엔드 — 헤더 토글 + Incremark 렌더

#### UI 토글 배치 (턴 단위)
- 헤더 상단 다크/라이트 모드 토글 **옆에 "⚡ 실시간" 토글 버튼**.
- `localStorage["streaming_enabled"]` 저장(새 세션 기본값 복원).
- 매 사용자 메시지 전송 시 현재 토글 상태를 `user_message` 봉투의 `streaming_enabled` 필드로 동봉.
- 턴 진행 중 변경 시: 로컬 상태는 즉시 변경, UI에 "다음 응답부터 적용" 토스트. 진행 중인 턴은 영향 없음.

#### 취소 버튼
- "⏹ 중단" 버튼 → WS 로 `{"type":"cancel","turn_id":currentTurnId}` 전송.
- 서버가 `llm_delta.end{cancelled:true}` 응답 또는 `stream.end{status:"cancelled"}` 까지 대기.

#### 렌더 전략 (Incremark + requestAnimationFrame)
- react-markdown 은 O(n²) → **Incremark** 채택 (벤치마크 65배 빠름). `streamdown` 은 Incremark 대비 6배 느려 기각.
- `part_type:"analysis"`: rAF 배치로 증분 re-render. 100ms throttle 불필요 (브라우저 프레임 동기화).
- `part_type:"svg"`: 버퍼 누적, `event:"end"` 시점에 일괄 `innerHTML` (부분 SVG 파싱 오류 방지).
- `event:"reset"` → 해당 `part_id` 버퍼 초기화.
- `event:"end"{error:true}` → 에러 UI 표시.
- 알 수 없는 `type` 무시 (PR3 선배포, 롤아웃 안전).

#### 턴 ID 관리
- 클라이언트가 `turn_id = crypto.randomUUID()` 생성, user_message 전송 시 동봉.
- 서버는 동일 `turn_id` 를 llm_delta / stream 메시지에 그대로 echo (취소·상관관계 추적).

---

## 5. 에러/취소 처리

- **취소**: 32 토큰 / 100ms 주기 `is_cancelled()` → break → `LLM_STREAM_END(cancelled=True)`.
- **첫 토큰 전 실패**: 기존 analyzer 폴백 경로 (stream_guard 가 예외를 CB 실패로 카운트).
- **중간 실패**: `LLM_STREAM_END(error=True)` + 예외 전파 + CB 실패.
- **파싱 실패**: retry 루프 교정 재시도 + `LLM_STREAM_RESET`. 최종 실패 시 `ParseError` → `e.last_response` 를 `summary` 로 폴백.
- **서킷 OPEN**: `guard()` 가 `CircuitOpenError` → analyzer 폴백.
- **에러 summary 경로**: `format_error(ERR_DATA_ANALYSIS)` → `AnalysisResult.summary` 직접 대입 유지.

---

## 6. 테스트 계획

### 단위

- `test_anthropic_create_extracts_text_only.py` — 근본 수정 검증 (thinking block 존재/부재 모두 텍스트만 추출).
- `test_circuit_breaker_guard.py` — `guard()` 성공/실패/OPEN/HALF_OPEN 전이 + `call`/`stream` 모두 guard 위에서 동작.
- `test_client_stream_anthropic.py` — StreamEvent kind 분리 + usage 누적.
- `test_client_stream_openai.py` — `include_usage` 동작, Qwen `<think>` 경계 상태머신.
- `test_parse_analysis_markdown.py` — 정상/빈 섹션/헤더 누락/불릿 변형.
- `test_markdown_escape.py` — `## ` / `###` ZWSP 이스케이프.

### 통합

- `test_analyze_data_streaming.py` — 목 스트림, dispatch 시퀀스, `state.streaming_delivered=True`.
- `test_svg_streaming.py` — SVG 토큰 스트림.
- `test_formatter_streaming_delivered_skip.py` — 빈 `formatted_response` + result_data 정상.
- `test_streaming_toggle.py` — 서버/사용자/provider 3 gate 조합.
- `test_analyzer_format_switch.py` — `analyzer_output_format` flag 로 legacy json 회귀 경로 동작.

### 수정

- `test_edge_cases.py`, `test_analyze_data.py` — `statistics` assertion 제거.

### E2E

- DATA_ANALYSIS 1건 WS 메시지 순서 검증.
- 취소 초기/중간/종료 직후.
- 골든셋 A/B — Markdown vs JSON 파싱 성공률·품질.

---

## 7. 마이그레이션 순서 (원칙 #5: PR 단위 리뷰 가능성)

1. **PR1 (근본 수정 + CB primitive)**: `AnthropicMessages.create()` text 블록 필터, `CircuitBreaker.guard()` asynccontextmanager, `call()` 재구현 (외부 API 불변), 단위 테스트.
2. **PR2 (스트리밍 어댑터)**: `StreamEvent`, `AnthropicMessages.stream`, `OpenAICompatibleMessages.stream`, `_CircuitBreakerMessages.stream`, Qwen 경계 상태머신, 단위 테스트.
3. **PR3 (트랜스포트/프로토콜)**: dispatch 4 이벤트(`llm.delta.start/chunk/end/reset`) + callback_handler 라우팅 + WS 프로토콜(`llm_delta`, `user_message` 봉투, `cancel`) + 프론트 방어코드(알 수 없는 type 무시) 선배포.
4. **PR4 (출력 포맷 전환)**: `ANALYZER_SYSTEM_MARKDOWN` 작성 + `parse_analysis_markdown` + `^##` 이스케이프 + `analyzer_output_format` flag (기본 markdown) + reasoning_summary 경고 조건화 + `NODE_THINKING_MODES["analyzer"]="high"` + `statistics` 필드 제거 + 테스트 2건 수정.
5. **PR5a (상태기계 확장)**: `PipelineState.client_settings`, `PipelineState.streaming_delivered`, `ClientSettings` 모델, formatter 분기.
6. **PR5b (retry 확장)**: `llm_call_with_parse_retry` 의 `stream_callback`/`stream_section`/`is_cancelled` + `analyze_data()` 스트리밍 분기 + analyzer_node 의 `streaming_delivered` 업데이트.
7. **PR5c (main 배선)**: main.py `client_settings` 수신/영속화 + `_should_stream()` + `stream.start` 라벨 조건부 + `run_pipeline(client_settings=...)` 전달.
8. **PR6 (SVG)**: `generate_svg_via_llm()` 스트리밍 경로.
9. **PR7 (프론트)**: 설정 토글 UI + `llm_stream` 렌더.
10. **PR8 (기본값 전환)**: 골든셋 회귀 통과 후 `LLM_STREAMING_ENABLED=true`.
11. **PR9 (legacy 제거)**: A/B 통과 후 `analyzer_system_legacy_json.txt` + `parse_analysis_json` + `analyzer_output_format` flag 삭제.

---

## 8. 리스크 및 완화

| 리스크 | 영향 | 완화 |
|---|---|---|
| Solar thinking 미지원 → 인사이트 얕아짐 | 품질 저하 | Few-shot 3건 강화 + ANTI_PATTERN 예시. Qwen 전환 시 해소 |
| Markdown 헤더가 데이터에 혼입 | 섹션 오인 | `^##` ZWSP 이스케이프 + 엄격 헤더 매칭 + 첫 섹션 강제 |
| 부분 SVG 렌더 오류 | 시각화 깨짐 | `event:"end"` 일괄 렌더 |
| 재시도 화면 깜빡임 | UX 저하 | `event:"reset"` 버퍼 초기화 |
| 취소 레이턴시 | Redis I/O | 32 토큰 / 100ms 쓰로틀 |
| Qwen `<think>` 토큰 경계 누설 | 화면 오염 | OpenAI 어댑터 경계 상태머신 |
| 긴 응답 Markdown 렌더 지연 | 체감 품질 저하 | Incremark O(n) 채택 + rAF 프레임 배치 (throttle 불필요) |
| Markdown 파서가 JSON 대비 취약 | 품질 회귀 | `analyzer_output_format` flag 로 legacy 회귀 즉시 가능 + 골든셋 A/B |
| Anthropic thinking block 첫 순서 `.content[0]` AttributeError | 기존 non-streaming 경로 크래시 | PR1 에서 `_extract_text` 근본 수정 (thinking 활성 여부 무관 안전) |
| OpenAI usage 누락 | 토큰 집계 실패 | `stream_options={"include_usage": True}` 필수 |
| CheckPointer 호환 | 세션 재개 실패 | 신규 필드 모두 `default` 또는 `default_factory` |
| IBK 환경 회귀 | 폐쇄망 장애 | `_should_stream()` provider 가드 |
| 이중 전송 | 중복 내러티브 | `state.streaming_delivered` + formatter 빈 본문 |
| 구프론트 배포 중 오염 | 메시지 깨짐 | PR3 방어코드 선배포 |

---

## 9. 미결 사항

### 해소됨 (v5/v6 반영)

- ~~UI 토글 위치~~ → 헤더 다크/라이트 옆, 턴 단위 `user_message` 봉투 (§4.12).
- ~~프론트 프로토콜~~ → Anthropic SSE + Vercel AI SDK v5 naming 채택 (§3 결정 #4).
- ~~Markdown 렌더러~~ → Incremark + rAF 확정 (§4.12).
- ~~thinking mode 분기~~ → 루트 수정 + 무조건 high (§3 결정 #1).

### 승인 필요 (내부 결정이 아닌 운영/범위 판단)

1. `LLM_STREAMING_ENABLED` 기본값 전환 타이밍 (PR8 에서 true 로 전환할지, 골든셋 A/B 통과 기준).
2. `analyzer_output_format` legacy JSON 경로 제거 시점 (PR9, A/B 품질 검증 후).
3. IBK 스트리밍은 본 범위 외 — 게이트웨이 SSE 스펙 확정 후 별도 설계.
4. DATA_EXTRACTION 요약 LLM 화는 별도 문서.
