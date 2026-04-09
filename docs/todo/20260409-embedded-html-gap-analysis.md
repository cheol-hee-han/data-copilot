# embedded.html Gap 분석 및 상세 구현 설계

> **작성일**: 2026-04-09
> **분석 대상**: `static/embedded.html` (현재 약 2,700줄)
> **참조 문서**:
> - `20260406-ui-ux-improvement-plan.md` (사용자 요구 17건 + UX 에이전트 발견사항)
> - `20260407-llm-streaming-review.md` (LLM 토큰 스트리밍 검토)
> - `20260407-output-structure-gap-analysis.md` (정보 흐름 단절 분석)
> - `20260407-present-streaming-proposal.md` (하이브리드 스트리밍 설계)
> - `20260407-websocket-response-restructuring.md` (WebSocket 구조화 전환)
> **전제**: 서버 코드는 최신 상태 (result_data, process_summary, trace_files 등 이미 구현). UI만 맞추면 됨.

---

## 목차

1. [사용자 원본 요구사항 대비 Gap 매트릭스](#1-사용자-원본-요구사항-대비-gap-매트릭스)
2. [서버 인터페이스 현황 요약](#2-서버-인터페이스-현황-요약)
3. [embedded.html 현재 구조 요약](#3-embeddedhtml-현재-구조-요약)
4. [항목별 상세 Gap 분석 및 구현 설계](#4-항목별-상세-gap-분석-및-구현-설계)
5. [설계문서 추가 요구사항 Gap](#5-설계문서-추가-요구사항-gap)
6. [구현 우선순위 및 일정](#6-구현-우선순위-및-일정)

---

## 1. 사용자 원본 요구사항 대비 Gap 매트릭스

### 1-1. 원본 요구 15건 + 추가 2건

| # | 요구사항 | 현재 상태 | Gap 수준 |
|---|---------|----------|---------|
| **1** | 액션 아이콘 한 줄 통합 | `msg-actions` + `msg-like-actions` 2줄 분리 (L882, L887) | **미구현** |
| **2** | CSV 다운로드 배너 삭제 | `.download-bar` + `renderDownload()` 존재 (L453-461, L1374-1386) | **미구현** |
| **3** | Trace/Report 파일 다운로드 버튼 | 버튼 없음. 서버는 `trace_files` 이미 전송 (main.py L502) | **미구현** |
| **4** | 명확화 질문 UI (선택/입력) | `_renderClarificationRestored`만 존재 (L1436). 실시간 UI 없음 | **미구현** |
| **5** | 데이터 추출 결과 순서 변경 | 서버가 result_data/process_summary를 분리 전송 → UI에서 조립해야 함 | **미구현** |
| **6** | "조회 기준 안내" 접기 가능 | 해당 UI 요소 자체가 없음 | **미구현** |
| **7** | 회색 [참고] 블럭 (default=open) | `ref-block`, `_wrapRefBlocks` 코드 없음 | **미구현** |
| **8** | 대화이력 복원 시 데이터 전달 확인 | insight: lazy-load 정상, progress: 복원 안됨 (정상 — 실시간 전용) | **완료** (설계 의도) |
| **9a** | "질문 해석" 데이터 전달 | `renderInsight()` L1252-1259에서 query_interpretation 렌더링 구현됨 | **완료** |
| **9b** | SQL 보기 하이라이팅 | `language-sql` 클래스 존재, `hljs.highlightElement()` **미호출** (L1305-1306) | **부분 구현** |
| **10** | 설정 모달 확장 | 테마 + 글꼴 크기 2개만 (L665-694) | **부분 구현** |
| **11** | 재생성 기능 동작 | `App.regen()` L2155-2167 구현됨. plain text 재전송 방식 | **완료** (기본 동작) |
| **12** | 마크다운 셀 높이 축소 | `line-height:1.78`, `padding:10px 12px` (L197, L216) — 축소 전 | **미구현** |
| **13** | 사이드바 최신순 정렬 | 클라이언트 정렬 로직 없음 (L1780-1785) | **미구현** |
| **14** | 스트리밍 출력 확인 | text/markdown/svg 모두 처리 로직 존재 | **완료** |
| **15** | 마크다운 표 스크롤 | `.table-wrap` 존재하나 `overflow-x:auto`, `max-height` 미적용 (L221) | **부분 구현** |
| **16** | Progress step fade-out + "더 보기" | `.phase-steps-inner` CSS 존재, `phase-expand-btn` JS 없음 | **미구현** |
| **17** | /reset→/new, /history 제거 | `/reset`, `/history` 그대로 (L1695-1697, L1745-1755) | **미구현** |

### 1-2. 요약 통계

| 상태 | 건수 | 항목 |
|------|:----:|------|
| **완료** | 4 | #8, #9a, #11, #14 |
| **부분 구현** | 3 | #9b, #10, #15 |
| **미구현** | 10 | #1, #2, #3, #4, #5, #6, #7, #12, #13, #16, #17 |

---

## 2. 서버 인터페이스 현황 요약

서버는 최신 상태이며, UI가 수신해야 할 데이터는 모두 전송 중.

### 2-1. stream.end 메시지 (main.py L492-509)

```jsonc
{
  "type": "stream",
  "action": "end",
  "status": "success" | "cancelled",
  "insight": { /* query_interpretation, tables_used, validation_detail, sql_code, ... */ },
  "turn_id": "uuid",
  "user_turn_id": "uuid",
  "trace_files": [{"name": "표시명", "filename": "파일명.md"}, ...],

  // ★ 서버에서 이미 전송 중이나 UI가 무시하고 있는 필드
  "result_data": {                    // 조건부 (SQL 결과 있을 때)
    "columns": ["지점명", "고객수"],
    "rows": [{"지점명": "강남", "고객수": 1234}],
    "column_formats": {"고객수": "count"},
    "total_count": 500,
    "displayed_count": 500
  },
  "process_summary": {                // 조건부 (추출/분석 경로)
    "intent": {"label": "데이터 추출", "is_continuation": false},
    "interpretation": {"measures": [...], "filters": [...], "period": "...", ...},
    "context": {"tables": [...], "use_case_count": 3, "manual_count": 1, "biz_terms": [...]},
    "ai_decisions": {"inferences": [...], "pending_assumptions": [...], "notice": "..."} | null,
    "validation": {"summary": "...", "row_count": 1523, "row_label": "1,523건 조회 완료"}
  }
}
```

### 2-2. 명확화 처리 (main.py L450, runner.py L299-355)

명확화 시 서버는 `stream.end`의 `status`가 `"awaiting_clarification"`이 되고,
`clarification_request` 필드가 포함된다:

```jsonc
{
  "type": "stream", "action": "end",
  "status": "awaiting_clarification",
  "clarification_request": {
    "question": "어떤 테이블을 사용할까요?",
    "question_type": "single_select" | "free_text" | "confirm",
    "options": ["Option A", "Option B"],
    "ambiguity_type": "TABLE",
    "source_node": "context_retriever"
  }
}
```

### 2-3. 기타 메시지

| 메시지 | 형식 | 비고 |
|--------|------|------|
| `viz` | `{type, title, code, chart_type}` | 시각화 SVG |
| `download_ready` | `{type, session_id, row_count, formats, turn_id}` | CSV 준비 |
| `progress` | `{type, action, phase, phaseLabel, label, thinkingLabel}` | 진행 표시 |

### 2-4. REST API

| 메서드 | 경로 | 비고 |
|--------|------|------|
| GET | `/api/traces/{filename}` | **이미 구현됨** — Trace 파일 다운로드 |
| POST | `/api/download` | CSV/JSON 다운로드 |
| GET | `/api/turns/{turn_id}/metadata` | insight lazy-load |
| 기타 | sessions CRUD, like, cancel 등 | 기존 동작 |
| ❌ | `/api/models` | **미구현** — 모델 선택 UI는 서버 API 선행 필요 |

### 2-5. WebSocket 수신 방식

- **서버 → UI**: JSON (`JSON.parse(e.data)` L1667)
- **UI → 서버**: **plain text** (`ws.send(t)` L1671) — JSON 전환 필요

서버(`main.py` L579-581)는 이미 JSON 파싱을 시도하고 실패 시 plain text로 fallback하는 코드가 있음.

---

## 3. embedded.html 현재 구조 요약

### 3-1. 파일 구성

| 영역 | 라인 범위 | 규모 |
|------|----------|------|
| CSS | 1~574 | 574줄 |
| HTML | 575~713 | 138줄 |
| JS | 714~2412 | 1,698줄 |
| **합계** | | **~2,700줄** |

### 3-2. JS 모듈 구조

| 모듈 | 라인 | 역할 |
|------|------|------|
| TM | 731-753 | 테마 관리 |
| API | 758-781 | REST 클라이언트 |
| MS | 786-831 | 메시지 스토어 (Map 기반) |
| RD | 836-1454 | 렌더러 (ensureDOM, render, renderPB, renderViz, renderInsight, renderDownload) |
| SE | 1459-1483 | 스트리밍 엔진 (appendChunk, finalize) |
| ED | 1488-1634 | 이벤트 디스패처 (handle, handleStream, handleProgress, handleViz 등) |
| IC2 | 1639-1655 | 입력 컨트롤러 |
| CN | 1660-1689 | WebSocket 연결 |
| CM | 1694-1770 | 슬래시 명령어 |
| SB | 1775-2032 | 사이드바 |
| App | 2130-2233 | 공개 API |
| Init | 2238-2410 | 초기화 |

### 3-3. assistant 메시지 DOM 슬롯 구조 (ensureDOM L878-894)

```
message-row.assistant
  └─ msg-content
      ├─ thinking-slot           ← renderTh()
      ├─ progress-block-slot     ← renderPB()
      ├─ bot-bubble (msg-bubble) ← render() 본문 (마크다운)
      ├─ viz-slot                ← renderViz()
      ├─ download-slot           ← renderDownload()  ★ 삭제 대상
      ├─ msg-actions             ← 복사/재생성/인사이트
      ├─ msg-like-actions        ← 좋아요/싫어요      ★ msg-actions에 통합
      ├─ msg-time
      ├─ insight-slot            ← renderInsight()
      └─ ai-logo

※ 현재 없는 슬롯: result-data-slot, process-summary-slot
```

### 3-4. render() 후처리 파이프라인 (L1043-1081)

```
ensureDOM(msg) → 본문 마크다운 렌더 → attachCodeCopy()
→ renderTh() → renderPB() → renderViz() → renderInsight() → renderDownload()
```

### 3-5. stream.end 핸들링 (ED.handleStream L1570-1589)

현재 처리하는 필드:
- ✅ `data.insight` → `m2.insight`
- ✅ `data.turn_id` → `MS.update(turnId, hasMetadata)`
- ✅ `data.user_turn_id` → 마지막 user 메시지에 연결
- ❌ `data.result_data` — **무시됨**
- ❌ `data.process_summary` — **무시됨**
- ❌ `data.trace_files` — **무시됨** (서버는 전송 중)
- ❌ `data.clarification_request` — **무시됨**

---

## 4. 항목별 상세 Gap 분석 및 구현 설계

### 4-1. 액션 아이콘 한 줄 통합 (#1)

**현재 코드**:
```
L882: '<div class="msg-actions">'        → 복사, 재생성, 인사이트
L887: '<div class="msg-like-actions">'   → 좋아요, 싫어요
L512: .msg-like-actions{display:flex;gap:2px;margin-top:4px}
```

**구현 설계**:

1. **ensureDOM() L882-890**: `msg-like-actions` div 제거, 좋아요/싫어요 버튼을 `msg-actions` 안으로 이동
2. **CSS L512-516**: `.msg-like-actions` 스타일 삭제
3. **이벤트 L896**: `msg-like-actions .act-btn` → `msg-actions .act-btn`으로 셀렉터 통합

```html
<!-- 변경 후: 단일 div -->
<div class="msg-actions">
  [복사] [재생성] [인사이트] [Trace 다운로드] [좋아요] [싫어요]
</div>
```

**변경 규모**: CSS -5줄, HTML 생성 -3줄, 이벤트 ±5줄

---

### 4-2. CSV 다운로드 배너 삭제 (#2)

**현재 코드**:
```
L453-461:  CSS .download-bar 스타일
L538-539:  CSS .download-bar.downloaded 스타일
L881:      ensureDOM에서 download-slot div 생성
L1079:     render()에서 renderDownload() 호출
L1374-1386: renderDownload() 함수
L1621-1628: ED.handleDownloadReady()
L2227:     App.downloadCSV에서 download-bar 참조
```

**구현 설계**:

1. `renderDownload()` 함수 → **배너 생성 코드 제거**
2. CSS `.download-bar` 관련 스타일 → **삭제**
3. `render()`의 `renderDownload()` 호출 → **삭제**
4. `ED.handleDownloadReady()` → **유지하되**, 배너 대신 `msg-actions`의 다운로드 아이콘 활성화로 변경
5. `App.downloadCSV` → `msg.downloadReady` 데이터 기반으로 동작 유지

**변경 규모**: CSS -12줄, JS -20줄

---

### 4-3. Trace/Report 파일 다운로드 버튼 (#3)

**현재 코드**: 해당 버튼 없음
**서버**: `stream.end`에 `trace_files: [{name, filename}]` 이미 전송 (main.py L502)
**API**: `GET /api/traces/{filename}` 이미 구현

**구현 설계**:

1. **ensureDOM()**: `msg-actions` 안에 Trace 다운로드 버튼 추가 (기본 `display:none`)

```html
<button class="act-btn" data-act="download-trace" title="분석 리포트 다운로드" style="display:none">
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    stroke-width="2" stroke-linecap="round">
    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
    <polyline points="7 10 12 15 17 10"/>
    <line x1="12" y1="15" x2="12" y2="3"/>
  </svg>
</button>
```

2. **ED.handleStream() end 블록**: `data.trace_files` 캡처

```javascript
if (data.trace_files && data.trace_files.length) {
  MS.update(m2.id, {traceFiles: data.trace_files});
  var trBtn = row.querySelector('[data-act="download-trace"]');
  if (trBtn) trBtn.style.display = '';
}
```

3. **이벤트 핸들러**: 클릭 시 드롭다운으로 파일 목록 표시 → 클릭하면 `window.open('/api/traces/' + filename)`

4. **MS 모델**: `traceFiles` 필드 추가

**변경 규모**: CSS +10줄, JS +40줄

---

### 4-4. 명확화 질문 UI (#4)

**현재 코드**: `_renderClarificationRestored()` L1436만 존재 (복원 전용)
**서버**: `stream.end`에 `status: "awaiting_clarification"` + `clarification_request` 전송

**구현 설계**:

1. **ED.handleStream() end 블록에 분기 추가** (L1570 부근):

```javascript
if (data.status === 'awaiting_clarification' && data.clarification_request) {
  _renderClarification(row, data.clarification_request);
  IC2.setBusy(false);
  return;  // 일반 end 처리 건너뜀
}
```

2. **`_renderClarification(row, req)` 신규 함수**:

```javascript
function _renderClarification(row, req) {
  var bub = row.querySelector('.bot-bubble');
  var card = document.createElement('div');
  card.className = 'clarification-card';

  var h = '<div class="clarification-question">' + esc(req.question) + '</div>';

  if (req.question_type === 'single_select' && req.options) {
    req.options.forEach(function(opt, i) {
      h += '<label class="clarification-option">'
        + '<input type="radio" name="clar" value="' + esc(opt) + '">'
        + '<span>' + esc(opt) + '</span></label>';
    });
    h += '<label class="clarification-option">'
      + '<input type="radio" name="clar" value="__custom__">'
      + '<span>기타 (직접 입력)</span></label>';
    h += '<input type="text" class="clarification-custom" placeholder="직접 입력해주세요…" style="display:none">';
  } else if (req.question_type === 'confirm') {
    h += '<div class="clarification-confirm">'
      + '<button class="clarification-btn" data-value="yes">예</button>'
      + '<button class="clarification-btn" data-value="no">아니오</button></div>';
  } else {
    h += '<input type="text" class="clarification-custom" placeholder="답변을 입력해주세요…">';
  }

  h += '<button class="clarification-submit">제출</button>';
  card.innerHTML = h;
  bub.after(card);

  // 이벤트 바인딩
  card.querySelector('.clarification-submit').addEventListener('click', function() {
    var val;
    if (req.question_type === 'single_select') {
      var checked = card.querySelector('input[name="clar"]:checked');
      if (!checked) return;
      val = checked.value === '__custom__'
        ? card.querySelector('.clarification-custom').value
        : checked.value;
    } else if (req.question_type === 'confirm') {
      // confirm은 버튼 클릭으로 처리
    } else {
      val = card.querySelector('.clarification-custom').value;
    }
    if (val) {
      card.remove();
      CN.send(val);
    }
  });
}
```

3. **CSS `.clarification-card`**: `feedback-popup` 유사 디자인

```css
.clarification-card {
  background: var(--bg2); border: 1px solid var(--border);
  border-radius: var(--r-md); padding: 16px; margin-top: 8px;
}
.clarification-question {
  font-size: 14px; font-weight: 600; color: var(--txt); margin-bottom: 12px;
}
.clarification-option {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 12px; border-radius: var(--r-sm); cursor: pointer;
  transition: background var(--t-fast);
}
.clarification-option:hover { background: var(--bg3); }
.clarification-custom {
  width: 100%; padding: 8px 12px; margin-top: 8px;
  border: 1px solid var(--border); border-radius: var(--r-sm);
  font-size: 13px; background: var(--bg-input); color: var(--txt);
}
.clarification-submit {
  margin-top: 12px; padding: 6px 20px;
  background: var(--bg-accent); color: var(--txt-inv);
  border: none; border-radius: var(--r-sm); font-size: 13px; cursor: pointer;
}
.clarification-submit:hover { background: var(--bg-accent-hover); }
```

**변경 규모**: CSS +30줄, JS +70줄

---

### 4-5. 결과 순서 + 구조화 렌더링 (#5, #6, #7)

**핵심 변경**: 서버의 `result_data` + `process_summary`를 UI에서 수신하여 구조화 렌더링

#### (a) ensureDOM() 슬롯 추가

`bot-bubble` 이후에 신규 슬롯 2개 삽입:

```
├─ bot-bubble              ← 텍스트 요약 (마크다운 스트리밍)
├─ result-data-slot        ← ★ 신규: 구조화 테이블
├─ process-summary-slot    ← ★ 신규: 조회 과정 요약 (ref-block)
├─ viz-slot                ← 시각화
```

#### (b) stream.end에서 데이터 캡처 (ED.handleStream L1570)

```javascript
// stream.end 블록에 추가
if (data.result_data) {
  MS.update(m2.id, {resultData: data.result_data});
}
if (data.process_summary) {
  MS.update(m2.id, {processSummary: data.process_summary});
}
RD.render(m2);  // 기존 render 호출이 신규 슬롯도 렌더링
```

#### (c) renderResultTable() 신규 함수

```javascript
function renderResultTable(row, msg) {
  if (!msg.resultData) return;
  var slot = row.querySelector('.result-data-slot');
  if (!slot || slot.children.length) return;  // 이미 렌더링됨

  var rd = msg.resultData;
  var h = '<div class="table-wrap"><table><thead><tr>';
  rd.columns.forEach(function(col) {
    h += '<th>' + esc(col) + '</th>';
  });
  h += '</tr></thead><tbody>';

  rd.rows.forEach(function(r) {
    h += '<tr>';
    rd.columns.forEach(function(col) {
      var val = r[col];
      var fmt = (rd.column_formats || {})[col];
      h += '<td>' + _formatCell(val, fmt) + '</td>';
    });
    h += '</tr>';
  });
  h += '</tbody></table>';

  // 절삭 안내
  if (rd.total_count > rd.displayed_count) {
    h += '<div class="table-truncated">전체 '
      + rd.total_count.toLocaleString() + '건 중 '
      + rd.displayed_count.toLocaleString() + '건만 표시</div>';
  }
  h += '</div>';

  slot.innerHTML = h;
  attachCodeCopy(slot);  // CSV 복사 버튼 부착
}

function _formatCell(val, fmt) {
  if (val == null || val === '') return '';
  if (typeof val !== 'number') return esc(String(val));
  switch (fmt) {
    case 'currency': return _formatCurrency(val);
    case 'rate':     return val.toFixed(1) + '%';
    case 'count':    return val.toLocaleString() + '건';
    default:         return val.toLocaleString();
  }
}

function _formatCurrency(val) {
  var abs = Math.abs(val);
  var sign = val < 0 ? '-' : '';
  if (abs >= 1e12) return sign + (abs / 1e12).toFixed(1) + '조원';
  if (abs >= 1e8)  return sign + (abs / 1e8).toFixed(0) + '억원';
  if (abs >= 1e4)  return sign + (abs / 1e4).toFixed(0) + '만원';
  return sign + abs.toLocaleString() + '원';
}
```

#### (d) renderProcessSummary() 신규 함수

```javascript
function renderProcessSummary(row, msg) {
  if (!msg.processSummary) return;
  var slot = row.querySelector('.process-summary-slot');
  if (!slot || slot.children.length) return;

  var ps = msg.processSummary;
  var h = '<details class="ref-block" open>'
    + '<summary class="ref-header">'
    + '<span class="ref-tag">참고</span>'
    + '<span class="ref-title">조회 과정 요약</span>'
    + '<span class="ref-chevron">▾</span></summary>'
    + '<div class="ref-body">';

  // 1. 질의 분류
  if (ps.intent) {
    h += '<div class="ps-step"><strong>1. 질의 분류:</strong> '
      + esc(ps.intent.label) + '</div>';
  }

  // 2. 질의 해석
  if (ps.interpretation) {
    var ip = ps.interpretation;
    var parts = [];
    if (ip.period) parts.push('기간=' + esc(ip.period));
    if (ip.entities && ip.entities.length) parts.push('대상=' + ip.entities.map(esc).join(', '));
    if (ip.measures && ip.measures.length) parts.push('지표=' + ip.measures.map(esc).join(', '));
    if (ip.filters && ip.filters.length) parts.push('조건=' + ip.filters.map(esc).join(', '));
    if (ip.dimensions && ip.dimensions.length) parts.push('기준=' + ip.dimensions.map(esc).join(', '));
    h += '<div class="ps-step"><strong>2. 질의 해석:</strong> ' + parts.join(' | ') + '</div>';
  }

  // 3. 활용 정보
  if (ps.context) {
    var cx = ps.context;
    var items = [];
    if (cx.tables && cx.tables.length) {
      items.push('테이블 ' + cx.tables.length + '개 (' + cx.tables.map(function(t){ return esc(t.label || t.name); }).join(', ') + ')');
    }
    if (cx.use_case_count) items.push('유사 SQL ' + cx.use_case_count + '건 참조');
    if (cx.manual_count) items.push('업무 매뉴얼 ' + cx.manual_count + '건 참조');
    if (cx.biz_terms && cx.biz_terms.length) items.push('용어: ' + cx.biz_terms.map(esc).join(', '));
    h += '<div class="ps-step"><strong>3. 활용 정보:</strong> ' + items.join(' · ') + '</div>';
  }

  // 4. AI 판단 (있을 때만)
  if (ps.ai_decisions) {
    var ad = ps.ai_decisions;
    h += '<div class="ps-step"><strong>4. AI 판단:</strong>';
    if (ad.inferences && ad.inferences.length) {
      ad.inferences.forEach(function(inf) {
        h += '<div class="ps-inference">· ' + esc(inf.question) + ' → ' + esc(inf.value) + '</div>';
      });
    }
    if (ad.pending_assumptions && ad.pending_assumptions.length) {
      ad.pending_assumptions.forEach(function(a) {
        h += '<div class="ps-assumption">· ' + esc(a) + '</div>';
      });
    }
    if (ad.notice) h += '<div class="ps-notice">' + esc(ad.notice) + '</div>';
    h += '</div>';
  }

  // 5. 검증 결과
  if (ps.validation) {
    h += '<div class="ps-step"><strong>' + (ps.ai_decisions ? '5' : '4')
      + '. 검증 결과:</strong> ' + esc(ps.validation.row_label || ps.validation.summary) + '</div>';
  }

  h += '</div></details>';
  slot.innerHTML = h;
}
```

#### (e) ref-block CSS

```css
.ref-block{background:var(--bg2);border:1px solid var(--border);
  border-radius:var(--r-sm);margin:12px 0;overflow:hidden}
.ref-header{display:flex;align-items:center;gap:8px;
  padding:10px 14px;cursor:pointer;user-select:none;list-style:none}
.ref-header::-webkit-details-marker{display:none}
.ref-tag{font-size:11px;font-weight:600;color:var(--txt3);
  background:var(--bg3);padding:2px 8px;border-radius:var(--r-xs)}
.ref-title{font-size:13px;font-weight:600;color:var(--txt2);flex:1}
.ref-chevron{transition:transform var(--t)}
.ref-block:not([open]) .ref-chevron{transform:rotate(-90deg)}
.ref-body{padding:0 14px 12px;font-size:13.5px;line-height:1.65}
.ps-step{margin-bottom:6px}
.ps-inference,.ps-assumption{margin-left:16px;font-size:12.5px;color:var(--txt2)}
.ps-notice{margin-top:6px;font-size:12.5px;color:var(--txt-accent);font-style:italic}
.table-truncated{text-align:center;padding:8px;font-size:12px;color:var(--txt3);
  border-top:1px solid var(--border)}
```

#### (f) render() 파이프라인 확장 (L1077 부근)

```javascript
// 기존: renderViz → renderInsight → renderDownload
// 변경: renderResultTable → renderProcessSummary → renderViz → renderInsight
renderResultTable(row, msg);          // ★ 신규
renderProcessSummary(row, msg);       // ★ 신규
renderViz(row, msg.visualization);    // 기존
renderInsight(row, msg);              // 기존
// renderDownload 삭제 (#2)
```

**변경 규모**: CSS +25줄, JS +150줄

---

### 4-6. "조회 기준 안내" 마크다운 후처리 ref-block (#6, #7)

서버 응답의 마크다운에 "조회 기준 안내" 섹션이 포함된 경우, 후처리로 ref-block 스타일을 적용.

**`_wrapRefBlocks(bub)` 함수**:

```javascript
function _wrapRefBlocks(bub) {
  // 마크다운 안의 <details><summary>조회 기준 안내</summary> 등을 ref-block으로 변환
  bub.querySelectorAll('details').forEach(function(det) {
    var sum = det.querySelector('summary');
    if (!sum) return;
    var txt = sum.textContent.trim();
    if (txt.indexOf('조회 기준') >= 0 || txt.indexOf('조회 과정') >= 0) {
      det.className = 'ref-block';
      det.setAttribute('open', '');
      sum.className = 'ref-header';
      sum.innerHTML = '<span class="ref-tag">참고</span>'
        + '<span class="ref-title">' + esc(txt) + '</span>'
        + '<span class="ref-chevron">▾</span>';
      var body = det.querySelector(':scope > :not(summary)');
      if (body) body.className = 'ref-body';
    }
  });
}
```

**호출 위치**: `render()` L1052 `attachCodeCopy(bub)` 직후

```javascript
attachCodeCopy(bub);
_wrapRefBlocks(bub);  // ★ 추가
```

**변경 규모**: JS +20줄

---

### 4-7. SQL 보기 하이라이팅 (#9b)

**현재**: L1305-1306에서 `<code class="language-sql">` 렌더링하나 `hljs.highlightElement()` 미호출

**구현 설계**: `renderInsight()` L1372 `slot.innerHTML = h;` 직후에 추가

```javascript
slot.innerHTML = h;
// SQL 하이라이팅 적용
slot.querySelectorAll('pre code.language-sql').forEach(function(block) {
  if (typeof hljs !== 'undefined') hljs.highlightElement(block);
});
```

**변경 규모**: JS +4줄

---

### 4-8. 설정 모달 확장 (#10)

현재: 테마 + 글꼴 크기 2개 (L665-694)

**추가 항목 (서버 변경 없이 가능한 것만)**:

| 카테고리 | 항목 | 컨트롤 | 저장소 |
|----------|------|--------|--------|
| 표시 | 대화 폭 (600/700/900px) | 3단 세그먼트 | localStorage |
| 표시 | 코드 글꼴 크기 (12/13/14px) | 3단 세그먼트 | localStorage |
| 표시 | 줄 간격 (1.5/1.65/1.8) | 3단 세그먼트 | localStorage |
| 동작 | 자동 스크롤 on/off | 토글 | localStorage |
| 데이터 | CSV 인코딩 (UTF-8/EUC-KR) | 2단 세그먼트 | localStorage |
| 분석 | SQL 표시 여부 (표시/접기/숨김) | 3단 세그먼트 | localStorage |

**변경 규모**: CSS +20줄, JS +60줄, HTML +40줄

---

### 4-9. 마크다운 셀 높이 축소 (#12)

**변경**:

```css
/* 변경 전 */
.bot-bubble{line-height:1.78;font-size:14.5px}
.bot-bubble p{margin-bottom:12px}
.bot-bubble th,.bot-bubble td{padding:10px 12px}
.bot-bubble li{margin-bottom:4px}

/* 변경 후 */
.bot-bubble{line-height:1.6;font-size:14px}
.bot-bubble p{margin-bottom:8px}
.bot-bubble th,.bot-bubble td{padding:7px 10px}
.bot-bubble li{margin-bottom:2px}
```

**변경 규모**: CSS ±6줄

---

### 4-10. 사이드바 최신순 정렬 (#13)

**현재**: L1780-1785에서 서버 응답 순서 그대로 사용

**구현**: `SB.init()` 세션 로드 직후 정렬 추가

```javascript
// L1783 이후
_sessions.sort(function(a, b) { return b.ts - a.ts; });
```

**변경 규모**: JS +1줄

---

### 4-11. 마크다운 표 스크롤 (#15)

**현재**: `.table-wrap`에 `position:relative`만 (L221)

**구현**:

```css
.table-wrap{
  position:relative;
  overflow-x:auto;
  overflow-y:auto;
  max-height:480px;
  background:var(--bg2);
  border:1px solid var(--border);
  border-radius:var(--r-md);
}
.table-wrap table{margin:0}
```

**변경 규모**: CSS +6줄

---

### 4-12. Progress step fade-out + "더 보기" (#16)

**현재**: `.phase-steps-inner` CSS 존재 (L278), max-height/fade 효과 미적용, 버튼 없음

**구현 설계**:

1. **CSS 변경**:

```css
/* phase-steps: 외부 — 펼침/접힘만 */
.progress-phase.active .phase-steps,
.progress-phase.expanded .phase-steps{max-height:none;opacity:1}

/* phase-steps-inner: 내부 — 실제 높이 제한 + fade */
.phase-steps-inner{
  /* 기존 flex/padding/border-left 유지 */
  position:relative;
  max-height:120px;overflow:hidden;
  transition:max-height var(--t-slow) var(--ease);
}
.phase-steps-inner:not(.expanded)::after{
  content:'';position:absolute;bottom:0;left:0;right:0;height:40px;
  background:linear-gradient(transparent,var(--bg));
  pointer-events:none;
}
.phase-steps-inner.expanded{max-height:none}
.phase-steps-inner.expanded::after{display:none}

/* 더 보기 / 접기 버튼 */
.phase-expand-btn{
  display:inline-flex;align-items:center;gap:4px;
  font-size:11px;color:var(--txt3);cursor:pointer;
  padding:4px 0 0 28px;background:none;border:none;
}
.phase-expand-btn:hover{color:var(--txt2)}
```

2. **JS: _renderPhaseHTML() 수정** (L1119 부근):

```javascript
// 스텝 4개 이상일 때 "더 보기" 버튼 추가
if (g.steps.length > 4) {
  var isExpanded = (msg.phaseExpanded || {})[g.phase];
  var innerCls = 'phase-steps-inner' + (isExpanded ? ' expanded' : '');
  // inner div에 cls 적용
  h += '<button class="phase-expand-btn" data-phase="' + g.phase + '">'
    + (isExpanded ? '접기' : '더 보기 (' + g.steps.length + '단계)') + '</button>';
}
```

3. **JS: 이벤트 위임** (progress-block-slot에서 클릭 감지):

```javascript
slot.addEventListener('click', function(e) {
  var btn = e.target.closest('.phase-expand-btn');
  if (!btn) return;
  var phase = btn.dataset.phase;
  msg.phaseExpanded = msg.phaseExpanded || {};
  msg.phaseExpanded[phase] = !msg.phaseExpanded[phase];
  RD.render(msg);
});
```

**변경 규모**: CSS +20줄, JS +25줄

---

### 4-13. /reset→/new, /history 제거 (#17)

**현재 코드** (CM 모듈 L1694-1770):
```javascript
var CMDS=[
  {name:'/reset',desc:'대화 초기화 — ...',icon:'↺'},
  {name:'/history',desc:'대화 기록 — ...',icon:'⏱'}
];
// L1745-1755: _execReset(), _execHistory()
```

**구현 설계**:

```javascript
// CMDS 변경
var CMDS=[
  {name:'/new',desc:'새 대화 — 새로운 대화를 시작합니다',icon:'+'}
];

// tryExec 변경
function tryExec(text){
  var cmd=text.trim().toLowerCase();
  if(cmd==='/new'){_execNew();return true;}
  return false;
}

// _execNew 신규
function _execNew(){
  SB.onNewChat();  // 새 대화 열기 (기존 SB 메서드)
}

// _execReset, _execHistory, _renderHL 삭제
```

**변경 규모**: JS ±20줄

---

### 4-14. WebSocket JSON 프로토콜 전환 (선행 작업)

**현재**: `CN.send(t)` → `ws.send(t)` (plain text, L1671)
**서버**: 이미 JSON 파싱 지원 (L579-581)

**구현**:

```javascript
// CN.send 변경
function send(t, opts) {
  if (ws && ws.readyState === 1) {
    var payload = {text: t};
    if (opts) Object.assign(payload, opts);
    ws.send(JSON.stringify(payload));
    return true;
  }
  return false;
}
```

**영향**: `App.sendMessage()`, `App.regen()`, CM 슬래시 명령어의 `CN.send()` 호출부 4개 → 인자 형태 변경 없이 자동 적용

**변경 규모**: JS +5줄

---

## 5. 설계문서 추가 요구사항 Gap

5개 설계문서에서 도출되었으나 사용자 원본 요구 15+2건에 포함되지 않은 추가 항목:

| # | 항목 | 출처 문서 | 현재 | 우선순위 |
|---|------|---------|------|---------|
| A | "스크롤 맨아래로" 버튼 | 20260406 #1.20 | 미구현 | Day 1 |
| B | `scroll-behavior:smooth` 제거 | 20260406 #1.21 | CSS에 적용 중 | Day 1 |
| C | result_data `type:"result_data"` 선행 전송 핸들러 | 20260407-present-streaming | 미구현 | Day 3+ (서버 스트리밍 구현 후) |
| D | LLM 인사이트 코멘트 스트리밍 | 20260407-llm-streaming | 미구현 | Day 3+ (서버 구현 후) |
| E | 모델 선택 UI (MM 모듈) | 20260406 #1.18 | 미구현 | 서버 `/api/models` 선행 필요 |
| F | LLM CoT 표시 | 20260406 #1.19 | 미구현 | 서버 thinking 추출 선행 필요 |

**참고**: C, D, E, F는 서버 인프라 변경이 선행되어야 하므로 이번 UI 구현 범위에서 제외.
A, B는 사용자 요구와 직접 관련되므로 포함.

### 5-A. "스크롤 맨아래로" 버튼

```html
<button id="scrollBottomBtn" class="scroll-bottom-btn" style="display:none">
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
    stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
    <polyline points="6 9 12 15 18 9"/>
  </svg>
</button>
```

```css
.scroll-bottom-btn{
  position:sticky;bottom:16px;left:50%;transform:translateX(-50%);
  width:36px;height:36px;border-radius:50%;
  background:var(--bg);border:1px solid var(--border);box-shadow:var(--sh-sm);
  display:none;align-items:center;justify-content:center;
  cursor:pointer;z-index:10;
  transition:opacity var(--t),background var(--t);color:var(--txt2);
}
.scroll-bottom-btn:hover{background:var(--bg2);border-color:var(--border2)}
```

```javascript
// scroll 리스너 확장
chatWrapEl.addEventListener('scroll', function() {
  var gap = chatWrapEl.scrollHeight - chatWrapEl.scrollTop - chatWrapEl.clientHeight;
  var btn = document.getElementById('scrollBottomBtn');
  if (gap > 100) { btn.style.display = 'flex'; }
  else { btn.style.display = 'none'; }
}, {passive: true});
```

### 5-B. scroll-behavior 제거

```css
/* .chat-wrap에서 scroll-behavior:smooth 제거 */
/* 프로그래밍 방식 스크롤에서만 behavior:'smooth' 사용 */
```

---

## 6. 구현 우선순위 및 일정

### Phase 1: Quick Wins (서버 변경 없음, UI만)

| # | 항목 | 예상 규모 | 난이도 |
|---|------|----------|--------|
| 4-1 | 액션 아이콘 한 줄 통합 | -8줄 | 낮 |
| 4-2 | CSV 다운로드 배너 삭제 | -32줄 | 낮 |
| 4-9 | 마크다운 셀 높이 축소 | ±6줄 CSS | 낮 |
| 4-11 | 마크다운 표 스크롤 완성 | +6줄 CSS | 낮 |
| 4-10 | 사이드바 최신순 정렬 | +1줄 JS | 낮 |
| 4-7 | SQL 하이라이팅 | +4줄 JS | 낮 |
| 4-13 | /reset→/new, /history 제거 | ±20줄 JS | 낮 |
| 5-A | "스크롤 맨아래로" 버튼 | +35줄 | 낮 |
| 5-B | scroll-behavior 제거 | CSS 1줄 | 낮 |

### Phase 2: 핵심 구조 전환 (서버 데이터 수신 + 구조화 렌더링)

| # | 항목 | 예상 규모 | 난이도 |
|---|------|----------|--------|
| 4-14 | WebSocket JSON 프로토콜 전환 | +5줄 | 낮 |
| 4-5 | result_data + process_summary 렌더링 | +175줄 (JS 150 + CSS 25) | 중 |
| 4-6 | ref-block 마크다운 후처리 | +20줄 JS | 낮 |
| 4-12 | Progress fade-out + "더 보기" | +45줄 (JS 25 + CSS 20) | 중 |

### Phase 3: 대화형 기능

| # | 항목 | 예상 규모 | 난이도 |
|---|------|----------|--------|
| 4-3 | Trace 파일 다운로드 버튼 | +50줄 (JS 40 + CSS 10) | 중 |
| 4-4 | 명확화 질문 UI | +100줄 (JS 70 + CSS 30) | 중 |
| 4-8 | 설정 모달 확장 | +120줄 (JS 60 + CSS 20 + HTML 40) | 중 |

### 전체 변경량 추정

| Phase | 추가 | 삭제 | 순변화 |
|-------|:----:|:----:|:------:|
| Phase 1 | +46줄 | -40줄 | +6줄 |
| Phase 2 | +245줄 | -15줄 | +230줄 |
| Phase 3 | +270줄 | -0줄 | +270줄 |
| **합계** | **+561줄** | **-55줄** | **+506줄** |

최종 파일 크기: ~2,700 + 506 = **~3,200줄** (단일 파일 유지 가능, CSS 분리 검토 시점에 근접)

---

## 부록: 서버-UI 인터페이스 체크리스트

서버가 이미 보내고 있으나 UI가 아직 처리하지 않는 필드:

| 메시지 | 필드 | 서버 위치 | UI 처리 |
|--------|------|----------|---------|
| stream.end | `result_data` | main.py L504-505 | ❌ 무시 → Phase 2에서 처리 |
| stream.end | `process_summary` | main.py L506-509 | ❌ 무시 → Phase 2에서 처리 |
| stream.end | `trace_files` | main.py L502 | ❌ 무시 → Phase 3에서 처리 |
| stream.end | `clarification_request` | runner.py 명확화 시 | ❌ 무시 → Phase 3에서 처리 |
| stream.end | `status:"awaiting_clarification"` | main.py L496 | ❌ 미분기 → Phase 3에서 처리 |
