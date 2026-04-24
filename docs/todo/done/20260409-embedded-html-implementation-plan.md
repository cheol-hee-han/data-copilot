# embedded.html 상세 구현 계획

> **작성일**: 2026-04-09
> **기반 문서**: `20260409-embedded-html-gap-analysis.md` (Gap 분석)
> **UI 전문 에이전트 검토 반영**: UX 매끄러움, 디자인 일관성, 접근성, 서버 호환성, 죽은 코드, 재사용성, 태스크 분리
> **전제**: 서버 코드 수정 없음. UI (`static/embedded.html`) 만 변경.

---

## 검토 결과 요약 및 설계 보정

### 중대 발견 및 조치

| # | 발견 | 심각도 | 조치 |
|---|------|:------:|------|
| 1 | **명확화 UI(#4): 서버가 `stream.end`에 `clarification_request`를 포함하지 않음** — main.py L492-509에서 `end_msg` 구성 시 `clarification_request` 필드를 추가하는 코드 없음. UI만으로는 명확화를 감지할 수 없음 | 🔴 | **Phase 3 → "서버 수정 선행 필요"로 재분류.** 이번 구현 범위에서 제외 |
| 2 | 4-4 confirm 타입 "예/아니오" 클릭 이벤트 미구현 + 불필요한 "제출" 버튼 | 🔴 | 명확화 전체가 이번 범위 제외로 해소 |
| 3 | 4-4 "기타" 라디오 change 이벤트 누락 | 🔴 | 동일 |
| 4 | clarification-card 키보드/스크린리더 접근성 미설계 | 🔴 | 동일 |
| 5 | 4-5 stream.end 테이블 삽입 시 스크롤 점프 | 🟡 | 설계에 스크롤 위치 보존 로직 추가 |
| 6 | 4-12 active phase에서 최신 step이 fade-out으로 가려짐 | 🟡 | active phase는 제한 해제, done phase만 접기 적용 |
| 7 | 4-3 Trace 드롭다운 열기/닫기 상호작용 미정의 | 🟡 | 파일 1개면 즉시 다운로드, 2개 이상이면 드롭다운 |
| 8 | 4-2 `App.downloadCSV()`의 `.download-bar` DOM 참조 + `download-slot` 삭제 미기술 | 🟡 | dead code 목록에 추가 |
| 9 | 4-1 이벤트 핸들러 병합 전략 미기술 | 🟡 | 이벤트 위임으로 통합 설계 추가 |
| 10 | Phase 간 의존 관계 미명시 | 🟡 | Phase 의존 관계도 추가 |
| 11 | 4-5 MS 모델 필드 추가 미기술 | 🟡 | MS.create 초기화 명세 추가 |
| 12 | 4-8 CSV 인코딩 설정은 서버 API 변경 필요 | 🟡 | 설정 목록에서 제외 |
| 13 | 5-A 스크롤 버튼 위치 조정 | 🟡 | `bottom:24px` + 모바일 축소 |

### 최종 범위 결정

| 항목 | 원래 Phase | 최종 결정 | 사유 |
|------|-----------|----------|------|
| 4-4 명확화 질문 UI | Phase 3 | **제외** (서버 수정 선행 필요) | `stream.end`에 `clarification_request` 미포함 |
| 4-8 CSV 인코딩 설정 | Phase 3 (설정 확장) | **제외** | `/api/download` 서버 확장 필요 |
| 나머지 전체 | 그대로 | **포함** | UI만으로 구현 가능 확인 |

---

## Phase 1: Quick Wins (서버 변경 0, 독립 항목)

### 의존 관계

```
4-1 (아이콘 통합) ──→ 4-2 (배너 삭제)  [둘 다 ensureDOM 수정]
나머지 (4-9, 4-10, 4-11, 4-7, 4-13, 5-A, 5-B) → 독립
```

### Task 1-1: 액션 아이콘 한 줄 통합

**수정 파일**: `static/embedded.html`

**(a) ensureDOM() — L882-890**

변경 전:
```javascript
+'<div class="msg-actions">'
+IC+'...'  // 복사, 재생성, 인사이트
+'</div>'
+'<div class="msg-like-actions">'
+'...'     // 좋아요, 싫어요
+'</div>'
```

변경 후:
```javascript
+'<div class="msg-actions">'
+IC+'...'  // 복사, 재생성, 인사이트, 좋아요, 싫어요
+'</div>'
// msg-like-actions div 삭제
```

**(b) CSS 삭제 — L512-516**:
```css
/* 삭제: .msg-like-actions 관련 스타일 전체 */
```

**(c) 이벤트 핸들러 통합 — L896-912**:

변경 전: 두 개의 `querySelectorAll` 루프
```javascript
row.querySelectorAll('.msg-like-actions .act-btn').forEach(...)  // L896
row.querySelectorAll('.msg-actions .act-btn').forEach(...)        // L902
```

변경 후: 단일 이벤트 위임
```javascript
var actions = row.querySelector('.msg-actions');
if (actions) {
  actions.addEventListener('click', function(e) {
    var btn = e.target.closest('.act-btn');
    if (!btn) return;
    var act = btn.dataset.act;
    if (act === 'copy') { /* 복사 로직 */ }
    else if (act === 'regen') { /* 재생성 로직 */ }
    else if (act === 'insight') { /* 인사이트 로직 */ }
    else if (act === 'like' || act === 'dislike') { /* 좋아요/싫어요 로직 */ }
    else if (act === 'download-trace') { /* Phase 3에서 구현 */ }
  });
}
```

**dead code 정리**: `msg-like-actions` 관련 CSS (L512-516), 개별 이벤트 바인딩 2개 (L896-912)

---

### Task 1-2: CSV 다운로드 배너 삭제

**수정 파일**: `static/embedded.html`

**(a) CSS 삭제**:
- L453-461: `.download-bar` 스타일
- L537-539: `.download-bar.downloaded` 스타일

**(b) ensureDOM() — L881**:
```javascript
// 삭제: +'<div class="download-slot"></div>'
```

**(c) render() — L1079**:
```javascript
// 삭제: if(msg.downloadReady)renderDownload(row,msg);
```

**(d) renderDownload() — L1374-1386**: 함수 전체 삭제

**(e) ED.handleDownloadReady() — L1621-1628**: 유지하되, 배너 대신 `msg.downloadReady` 플래그만 설정 (향후 액션 아이콘에서 CSV 다운로드 활용)

```javascript
function handleDownloadReady(data) {
  if (!_cur) return;
  var m = MS.get(_cur);
  if (m) {
    m.downloadReady = data;
    // 배너 렌더링 삭제 — msg-actions의 다운로드 기능에서 활용
  }
}
```

**(f) App.downloadCSV() — L2226-2229**: `.download-bar` DOM 참조 삭제
```javascript
// 삭제: var bar=row.querySelector('.download-bar');
// 삭제: bar 관련 CSS 클래스 조작 코드
```

**dead code 정리**: CSS 12줄, `renderDownload()` 함수 13줄, `download-slot` div, `download-bar` DOM 참조

---

### Task 1-3: 마크다운 셀 높이 축소

**CSS 변경**:
```css
/* 변경 전 → 변경 후 */
.bot-bubble { line-height:1.78 → 1.6; font-size:14.5px → 14px }
.bot-bubble p { margin-bottom:12px → 8px }
.bot-bubble ul,.bot-bubble ol { margin-bottom:12px → 8px }
.bot-bubble li { margin-bottom:4px → 2px }
.bot-bubble th,.bot-bubble td { padding:10px 12px → 7px 10px }
```

---

### Task 1-4: 마크다운 표 스크롤 완성

**CSS 변경 — L221**:
```css
/* 변경 전 */
.code-wrap,.table-wrap{position:relative}

/* 변경 후 */
.code-wrap{position:relative}
.table-wrap{
  position:relative;
  overflow-x:auto;overflow-y:auto;
  max-height:480px;
  background:var(--bg2);border:1px solid var(--border);
  border-radius:var(--r-md)
}
.table-wrap table{margin:0}
```

디자인 일관성: `.viz-body`(L303)와 동일한 `background`, `border`, `border-radius` 적용.

---

### Task 1-5: 사이드바 최신순 정렬

**JS 변경 — SB.init() L1783 이후**:
```javascript
_sessions.sort(function(a, b) { return b.ts - a.ts; });
```

---

### Task 1-6: SQL 하이라이팅

**JS 변경 — renderInsight() L1372 직후**:
```javascript
slot.innerHTML = h;
// ★ 추가: SQL 하이라이팅 적용
slot.querySelectorAll('pre code.language-sql').forEach(function(block) {
  if (typeof hljs !== 'undefined') hljs.highlightElement(block);
});
```

---

### Task 1-7: /reset→/new, /history 제거

**JS 변경 — CM 모듈 (L1694-1770)**:

```javascript
// CMDS 변경
var CMDS = [
  {name:'/new', desc:'새 대화 — 새로운 대화를 시작합니다', icon:'+'}
];

// tryExec 변경
function tryExec(text) {
  var cmd = text.trim().toLowerCase();
  if (cmd === '/new') { _execNew(); return true; }
  return false;
}

// 신규
function _execNew() {
  SB.onNewChat();
}
```

**dead code 삭제**:
- `_execReset()` 함수 (L1749-1753)
- `_execHistory()` 함수 (L1755-1770)
- `_renderHL()` 함수 (history 렌더링)
- `MS.getSummary()` 메서드 (history 전용)
- `MS.loadPersisted()` 메서드 (history 전용, `_execHistory`에서만 호출)
- `SB.onReset()` 메서드 참조 확인 후 dead이면 삭제

---

### Task 1-8: "스크롤 맨아래로" 버튼

**(a) HTML — `#chatWrap` 내부, `chat-inner` 다음**:
```html
<button id="scrollBottomBtn" class="scroll-bottom-btn" style="display:none" aria-label="맨 아래로 스크롤">
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
    stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
    <polyline points="6 9 12 15 18 9"/>
  </svg>
</button>
```

**(b) CSS**:
```css
.scroll-bottom-btn{
  position:absolute;bottom:24px;left:50%;transform:translateX(-50%);
  width:36px;height:36px;border-radius:50%;
  background:var(--bg);border:1px solid var(--border);box-shadow:var(--sh-sm);
  display:none;align-items:center;justify-content:center;
  cursor:pointer;z-index:10;color:var(--txt2);
  transition:opacity var(--t),background var(--t)
}
.scroll-bottom-btn:hover{background:var(--bg2);border-color:var(--border2)}
@media(max-width:640px){
  .scroll-bottom-btn{width:32px;height:32px;bottom:12px}
}
```

> `.chat-wrap`에 `position:relative` 추가 (absolute 기준점)

**(c) JS — 기존 scroll 리스너 확장 (L2296 부근)**:
```javascript
chatWrapEl.addEventListener('scroll', function() {
  var gap = chatWrapEl.scrollHeight - chatWrapEl.scrollTop - chatWrapEl.clientHeight;
  var btn = document.getElementById('scrollBottomBtn');
  if (gap > 100) {
    btn.style.display = 'flex';
    _userScrolledUp = true;
  } else {
    btn.style.display = 'none';
    _userScrolledUp = false;
  }
}, {passive: true});

document.getElementById('scrollBottomBtn').addEventListener('click', function() {
  _userScrolledUp = false;
  chatWrapEl.scrollTo({top: chatWrapEl.scrollHeight, behavior: 'smooth'});
  this.style.display = 'none';
});
```

---

### Task 1-9: scroll-behavior 제거

**CSS 변경 — L134**:
```css
/* 삭제: scroll-behavior:smooth */
/* 프로그래밍 스크롤에서만 behavior:'smooth' 옵션 사용 */
```

---

## Phase 2: 핵심 구조 전환

### 의존 관계

```
Phase 1 (4-1, 4-2) 완료 필수 → ensureDOM 슬롯 순서가 확정되어야 함

Phase 2 내부:
  2-1 (인프라) ──→ 2-2 (renderResultTable)
                ──→ 2-3 (renderProcessSummary + ref-block)
                ──→ 2-4 (Progress 더보기)
  2-0 (JSON 전환) → 독립
```

### Task 2-0: WebSocket JSON 프로토콜 전환

**JS 변경 — CN 모듈 L1671**:
```javascript
// 변경 전
function send(t) {
  if (ws && ws.readyState === 1) { ws.send(t); return true; }
  return false;
}

// 변경 후
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

**호출부 확인** (모두 자동 호환):
- `App.sendMessage()` — `CN.send(t)` → `CN.send(t)` (opts 없으면 `{text:t}` 전송)
- `App.regen()` — `CN.send(lastUser.text)` → 동일
- `CM._execNew()` — `SB.onNewChat()` (CN.send 미호출)
- 명확화 응답 — 향후 구현 시 `CN.send(val)` 형태

**서버 호환**: main.py L579-581에서 이미 JSON 파싱 + plain text fallback 구현됨. **서버 수정 불필요.**

---

### Task 2-1: 인프라 (슬롯 + 데이터 캡처 + 렌더 파이프라인)

**(a) ensureDOM() — assistant innerHTML에 슬롯 추가**:

```
(변경 전)                          (변경 후)
├─ bot-bubble                      ├─ bot-bubble
├─ viz-slot                        ├─ result-data-slot     ★ 신규
├─ download-slot  ← Phase 1에서 삭제  ├─ process-summary-slot ★ 신규
├─ msg-actions                     ├─ viz-slot
                                   ├─ msg-actions
```

순서: 텍스트(bot-bubble) → 테이블(result-data) → 조회과정(process-summary) → 시각화(viz) → 액션버튼

**(b) MS 모델 필드 추가**:

`MS.create()` 시 초기화에 추가:
```javascript
resultData: null,        // stream.end의 result_data
processSummary: null,    // stream.end의 process_summary
traceFiles: [],          // stream.end의 trace_files
```

**(c) ED.handleStream() end 블록 — L1570 부근**:

기존 코드 이후에 추가:
```javascript
// ★ 신규 필드 캡처
if (data.result_data) {
  m2.resultData = data.result_data;
}
if (data.process_summary) {
  m2.processSummary = data.process_summary;
}
if (data.trace_files && data.trace_files.length) {
  m2.traceFiles = data.trace_files;
}
RD.render(m2);  // 기존 render 호출이 신규 슬롯도 렌더링
```

**(d) render() 후처리 파이프라인 확장 — L1077 부근**:

```javascript
// 변경 전: renderViz → renderInsight → renderDownload
// 변경 후:
renderResultTable(row, msg);          // ★ 신규
renderProcessSummary(row, msg);       // ★ 신규
renderViz(row, msg.visualization);    // 기존
renderInsight(row, msg);              // 기존
// renderDownload 삭제 (Phase 1)
```

**(e) 스크롤 점프 방지 (검토 반영)**:

render() 내에서 result_data 슬롯 삽입 전후에 스크롤 위치 보존:
```javascript
function renderResultTable(row, msg) {
  if (!msg.resultData) return;
  var slot = row.querySelector('.result-data-slot');
  if (!slot || slot.children.length) return;

  // ★ 스크롤 위치 보존
  var cw = document.querySelector('.chat-wrap');
  var wasAtBottom = (cw.scrollHeight - cw.scrollTop - cw.clientHeight) < 50;

  // ... 테이블 HTML 생성 + slot.innerHTML = h ...

  // ★ 스크롤 복원
  if (wasAtBottom) {
    cw.scrollTop = cw.scrollHeight;
  }
}
```

---

### Task 2-2: renderResultTable()

**JS 신규 함수 — RD 모듈 내부**:

```javascript
function renderResultTable(row, msg) {
  if (!msg.resultData) return;
  var slot = row.querySelector('.result-data-slot');
  if (!slot || slot.children.length) return;

  var cw = document.querySelector('.chat-wrap');
  var wasAtBottom = (cw.scrollHeight - cw.scrollTop - cw.clientHeight) < 50;

  var rd = msg.resultData;
  var h = '<div class="table-wrap"><table><thead><tr>';
  rd.columns.forEach(function(col) {
    h += '<th>' + esc(col) + '</th>';
  });
  h += '</tr></thead><tbody>';

  var maxRender = Math.min(rd.rows.length, 200);  // DOM 성능: 최대 200행 렌더링
  for (var i = 0; i < maxRender; i++) {
    var r = rd.rows[i];
    h += '<tr>';
    rd.columns.forEach(function(col) {
      h += '<td>' + _formatCell(r[col], (rd.column_formats || {})[col]) + '</td>';
    });
    h += '</tr>';
  }
  h += '</tbody></table>';

  // 절삭 안내
  if (rd.total_count > rd.displayed_count) {
    h += '<div class="table-truncated">전체 '
      + rd.total_count.toLocaleString() + '건 중 '
      + rd.displayed_count.toLocaleString() + '건 표시</div>';
  } else if (rd.rows.length > maxRender) {
    h += '<div class="table-truncated">표시 제한: '
      + rd.rows.length.toLocaleString() + '건 중 '
      + maxRender + '건 표시 (전체 데이터는 CSV로 다운로드)</div>';
  }
  h += '</div>';

  slot.innerHTML = h;
  attachCodeCopy(slot);

  if (wasAtBottom) cw.scrollTop = cw.scrollHeight;
}

// ── 포맷팅 유틸 (RD 내부, renderViz table_data에서도 재사용 가능) ──

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
  var abs = Math.abs(val), sign = val < 0 ? '-' : '';
  if (abs >= 1e12) return sign + (abs / 1e12).toFixed(1) + '조원';
  if (abs >= 1e8)  return sign + (abs / 1e8).toFixed(0) + '억원';
  if (abs >= 1e4)  return sign + (abs / 1e4).toFixed(0) + '만원';
  return sign + abs.toLocaleString() + '원';
}
```

**CSS 추가**:
```css
.table-truncated{text-align:center;padding:8px;font-size:12px;color:var(--txt3);
  border-top:1px solid var(--border)}
```

**성능 고려** (검토 반영): DOM 렌더링 최대 200행 제한. `displayed_count`(서버 기본 500)보다 작은 경우 안내 표시.

---

### Task 2-3: renderProcessSummary() + ref-block + _wrapRefBlocks

**(a) ref-block CSS**:
```css
.ref-block{background:var(--bg2);border:1px solid var(--border);
  border-radius:var(--r-sm);margin:12px 0;overflow:hidden}
.ref-header{display:flex;align-items:center;gap:8px;
  padding:10px 14px;cursor:pointer;user-select:none;list-style:none}
.ref-header::-webkit-details-marker{display:none}
.ref-header summary{list-style:none}
.ref-tag{font-size:11px;font-weight:600;color:var(--txt3);
  background:var(--bg3);padding:2px 8px;border-radius:var(--r-xs)}
.ref-title{font-size:13px;font-weight:600;color:var(--txt2);flex:1}
.ref-chevron{transition:transform var(--t)}
.ref-block:not([open]) .ref-chevron{transform:rotate(-90deg)}
.ref-body{padding:0 14px 12px;font-size:13.5px;line-height:1.65}
.ps-step{margin-bottom:6px}
.ps-inference,.ps-assumption{margin-left:16px;font-size:12.5px;color:var(--txt2)}
.ps-notice{margin-top:6px;font-size:12.5px;color:var(--txt-accent);font-style:italic}
```

> `border-radius:var(--r-sm)` — `insight-panel`(L410)과 일관. `viz-body`의 `--r-md`와 다르지만, "접기/펼치기 보조 정보"는 `--r-sm`, "콘텐츠 표시"는 `--r-md` 규칙.

**(b) renderProcessSummary() 함수**:

Gap 분석 문서의 4-5(d) 설계를 그대로 사용. 변경점 없음.

**(c) _wrapRefBlocks() — 마크다운 후처리**:

```javascript
function _wrapRefBlocks(bub) {
  bub.querySelectorAll('details').forEach(function(det) {
    var sum = det.querySelector('summary');
    if (!sum) return;
    var txt = sum.textContent.trim();
    if (txt.indexOf('조회 기준') >= 0 || txt.indexOf('조회 과정') >= 0
        || txt.indexOf('참고') >= 0) {
      det.className = 'ref-block';
      if (!det.hasAttribute('open')) det.setAttribute('open', '');
      var body = document.createElement('div');
      body.className = 'ref-body';
      while (det.children.length > 1) {
        body.appendChild(det.children[1]);
      }
      det.appendChild(body);
      sum.className = 'ref-header';
      sum.innerHTML = '<span class="ref-tag">참고</span>'
        + '<span class="ref-title">' + esc(txt) + '</span>'
        + '<span class="ref-chevron">▾</span>';
    }
  });
}
```

**호출 위치**: `render()` L1052 `attachCodeCopy(bub)` 직후
```javascript
attachCodeCopy(bub);
_wrapRefBlocks(bub);  // ★ 추가
```

---

### Task 2-4: Progress step fade-out + "더 보기"

**핵심 보정** (검토 반영): **active phase는 전체 표시, done phase만 접기**

**(a) CSS**:
```css
/* phase-steps-inner: 기본 — 완료 phase만 높이 제한 */
.phase-steps-inner{
  position:relative;max-height:120px;overflow:hidden;
  transition:max-height var(--t-slow) var(--ease)
}
/* ★ active phase: 높이 제한 해제 (최신 step 항상 보임) */
.progress-phase.active .phase-steps-inner{
  max-height:none
}
/* done phase: fade-out gradient */
.phase-steps-inner:not(.expanded)::after{
  content:'';position:absolute;bottom:0;left:0;right:0;height:40px;
  background:linear-gradient(transparent,var(--bg));pointer-events:none
}
/* active phase에서는 gradient 미적용 */
.progress-phase.active .phase-steps-inner::after{display:none}
/* 펼친 상태 */
.phase-steps-inner.expanded{max-height:none}
.phase-steps-inner.expanded::after{display:none}
/* 더 보기 / 접기 버튼 */
.phase-expand-btn{
  display:inline-flex;align-items:center;gap:4px;
  font-size:11px;color:var(--txt3);cursor:pointer;
  padding:4px 0 0 28px;background:none;border:none
}
.phase-expand-btn:hover{color:var(--txt2)}
```

**(b) JS — _renderPhaseHTML() 수정** (L1119 부근):

```javascript
// 스텝 4개 초과 + done 상태일 때만 "더 보기" 버튼 추가
if (g.steps.length > 4 && !g.active) {
  var isExpanded = (msg.phaseExpanded || {})[g.phase];
  var innerCls = 'phase-steps-inner' + (isExpanded ? ' expanded' : '');
  // inner div에 cls 적용
  h += '<button class="phase-expand-btn" data-phase="' + g.phase + '">'
    + (isExpanded ? '접기' : '더 보기 (' + g.steps.length + '단계)') + '</button>';
}
```

**(c) JS — 이벤트 위임**:

```javascript
// progress-block-slot에서 클릭 감지 (ensureDOM 이후 1회 바인딩)
slot.addEventListener('click', function(e) {
  var btn = e.target.closest('.phase-expand-btn');
  if (!btn) return;
  var phase = btn.dataset.phase;
  msg.phaseExpanded = msg.phaseExpanded || {};
  msg.phaseExpanded[phase] = !msg.phaseExpanded[phase];
  RD.render(msg);
});
```

---

## Phase 3: 기능 확장

### 의존 관계

```
Phase 1 (4-1 아이콘 통합) 완료 필수 → msg-actions 구조 확정
Phase 2 (2-0 JSON 전환) 완료 필수 → CN.send 형식 확정

3-1 (Trace 다운로드) → Phase 1 의존
3-2 (설정 모달) → 독립
```

### Task 3-1: Trace 파일 다운로드 버튼

**서버 확인**: `GET /api/traces/{filename}` (sessions.py L147-182) 이미 구현됨.
**데이터 형식**: `trace_files: [{name: "표시명", filename: "파일명.md"}, ...]` (callback_handler.py L808, L825, L850)

**(a) ensureDOM() — msg-actions 내에 버튼 추가**:

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

위치: `msg-actions` 내에서 insight 버튼 뒤, 좋아요 버튼 앞

**(b) 버튼 활성화 — ED.handleStream end 블록** (Task 2-1에서 traceFiles 캡처 완료):

render() 내에서:
```javascript
// traceFiles가 있으면 다운로드 버튼 표시
var trBtn = row.querySelector('[data-act="download-trace"]');
if (trBtn) trBtn.style.display = msg.traceFiles && msg.traceFiles.length ? '' : 'none';
```

**(c) 클릭 핸들러** (Task 1-1의 이벤트 위임에 분기 추가):

```javascript
else if (act === 'download-trace') {
  var files = msg.traceFiles || [];
  if (files.length === 0) return;
  if (files.length === 1) {
    // 파일 1개: 즉시 다운로드
    window.open('/api/traces/' + encodeURIComponent(files[0].filename));
  } else {
    // 파일 2개 이상: 드롭다운 표시
    _showTraceDropdown(btn, files);
  }
}
```

**(d) 드롭다운 함수**:

```javascript
function _showTraceDropdown(anchor, files) {
  // 기존 드롭다운 닫기
  var old = document.querySelector('.trace-dropdown');
  if (old) { old.remove(); return; }

  var dd = document.createElement('div');
  dd.className = 'trace-dropdown';
  files.forEach(function(f) {
    var item = document.createElement('a');
    item.className = 'trace-item';
    item.href = '/api/traces/' + encodeURIComponent(f.filename);
    item.target = '_blank';
    item.textContent = f.name;
    dd.appendChild(item);
  });
  anchor.parentElement.style.position = 'relative';
  anchor.parentElement.appendChild(dd);

  // 외부 클릭 닫기
  setTimeout(function() {
    document.addEventListener('click', function close(e) {
      if (!dd.contains(e.target)) {
        dd.remove();
        document.removeEventListener('click', close);
      }
    });
  }, 0);

  // Esc 닫기
  document.addEventListener('keydown', function escClose(e) {
    if (e.key === 'Escape') {
      dd.remove();
      document.removeEventListener('keydown', escClose);
    }
  });
}
```

**(e) CSS**:
```css
.trace-dropdown{
  position:absolute;bottom:100%;right:0;margin-bottom:4px;
  background:var(--bg);border:1px solid var(--border);
  border-radius:var(--r-sm);box-shadow:var(--sh-md);
  min-width:200px;z-index:20;overflow:hidden
}
.trace-item{
  display:block;padding:8px 14px;font-size:12.5px;
  color:var(--txt);text-decoration:none;
  transition:background var(--t-fast)
}
.trace-item:hover{background:var(--bg3)}
```

---

### Task 3-2: 설정 모달 확장

**서버 변경 불필요 항목만** (CSV 인코딩 제외):

| 카테고리 | 항목 | 컨트롤 | localStorage 키 | CSS 적용 대상 |
|----------|------|--------|-----------------|--------------|
| 표시 | 대화 폭 | 3단 (좁게600/보통700/넓게900) | `pref-chat-width` | `--mx` 변수 |
| 표시 | 코드 글꼴 | 3단 (12/13/14px) | `pref-code-font` | `.code-wrap` font-size |
| 표시 | 줄 간격 | 3단 (1.5/1.65/1.8) | `pref-line-height` | `.bot-bubble` line-height |
| 동작 | 자동 스크롤 | 토글 | `pref-auto-scroll` | `autoScroll()` 분기 |
| 분석 | SQL 표시 | 3단 (표시/접기/숨김) | `pref-sql-display` | `renderInsight()` SQL 섹션 |

구현 상세는 `20260406-ui-ux-improvement-plan.md` 1.10절 참조. 기존 설정 모달 HTML(L665-694)을 확장.

---

## Phase 의존 관계 요약

```
Phase 1
  ├─ Task 1-1 (아이콘 통합) ─→ Task 1-2 (배너 삭제) [ensureDOM 충돌 방지]
  └─ Task 1-3~1-9: 독립 (병렬 가능)

Phase 2 (Phase 1 완료 후)
  ├─ Task 2-0 (JSON 전환): 독립
  ├─ Task 2-1 (인프라) ─→ Task 2-2 (테이블) [슬롯 필요]
  │                    ─→ Task 2-3 (과정 요약) [슬롯 필요]
  └─ Task 2-4 (Progress): 독립

Phase 3 (Phase 2 완료 후)
  ├─ Task 3-1 (Trace 다운로드): Phase 1(4-1) + Phase 2(2-1) 의존
  └─ Task 3-2 (설정 모달): 독립

서버 수정 필요 (이번 범위 제외)
  ├─ 명확화 질문 UI (#4): stream.end에 clarification_request 포함 필요
  ├─ CSV 인코딩 선택: /api/download 확장 필요
  └─ 모델 선택 UI (#1.18): /api/models 필요
```

---

## 전체 변경량 추정

| Phase | Task | 추가 | 삭제 | 비고 |
|-------|------|:----:|:----:|------|
| 1 | 1-1 아이콘 통합 | +15 | -15 | 이벤트 위임 전환 |
| 1 | 1-2 배너 삭제 | +3 | -35 | dead code 대량 삭제 |
| 1 | 1-3 셀 축소 | +0 | +0 | CSS 값 변경만 |
| 1 | 1-4 표 스크롤 | +8 | -1 | CSS 추가 |
| 1 | 1-5 정렬 | +1 | +0 | 1줄 추가 |
| 1 | 1-6 SQL 하이라이팅 | +4 | +0 | |
| 1 | 1-7 /new | +5 | -30 | dead code 삭제 |
| 1 | 1-8 스크롤 버튼 | +35 | +0 | |
| 1 | 1-9 scroll-behavior | +0 | -1 | |
| **Phase 1 합계** | | **+71** | **-82** | **순 -11줄** |
| 2 | 2-0 JSON 전환 | +5 | -1 | |
| 2 | 2-1 인프라 | +25 | +0 | 슬롯+캡처+파이프라인 |
| 2 | 2-2 테이블 | +70 | +0 | JS 55 + CSS 15 |
| 2 | 2-3 과정 요약 + ref-block | +110 | +0 | JS 70 + CSS 40 |
| 2 | 2-4 Progress | +45 | +0 | JS 25 + CSS 20 |
| **Phase 2 합계** | | **+255** | **-1** | **순 +254줄** |
| 3 | 3-1 Trace 다운로드 | +65 | +0 | JS 50 + CSS 15 |
| 3 | 3-2 설정 모달 | +100 | +0 | JS 50 + CSS 20 + HTML 30 |
| **Phase 3 합계** | | **+165** | **+0** | **순 +165줄** |
| **전체** | | **+491** | **-83** | **순 +408줄** (최종 ~3,100줄) |
