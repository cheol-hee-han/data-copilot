# embedded.html Phase 2 구현 — 구조화 렌더링 + 누락 인터페이스 보완

> **작성일**: 2026-04-09
> **상위 설계**: `docs/todo/20260407-websocket-response-restructuring.md` (Phase 2)
> **설계 검토**: `docs/reviews/design/20260407-websocket-response-restructuring-review.md`
> **목적**: 서버가 이미 전송 중인 구조화 데이터를 프론트엔드에서 수신·렌더링하고, 누락된 서버 연동을 보완한다

---

## 1. 현황 진단

### 1-1. 서버 구현 완료 (Phase 1) — 리뷰 반영 확인

| 리뷰 항목 | 상태 | 반영 위치 |
|---|:---:|---|
| P1-1: result_data 조립을 formatter로 이동 | ✅ | `formatter.py:51-72` `_build_result_data()` |
| P1-2: 턴 metadata에 result_data/process_summary 포함 | ✅ | `runner.py:433-438` save_turn metadata |
| P2-1: column_formats 중복 계산 해소 | ✅ | P1-1과 동시 해소 |
| P2-2: 테스트 수정 | ✅ | `test_format_response.py`, `test_process_summary_builder.py` |
| P2-3: REST include_insight 파라미터 | ✅ | `main.py:87-89` QueryRequest |
| P3-1: PipelineResult docstring 보강 | ✅ | `response.py:67-75` |
| P3-2: ui_result_max_rows config 분리 | ✅ | `config.py:280` |

### 1-2. 프론트엔드 미구현 (Phase 2) — 본 문서 범위

| 항목 | 설계 문서 참조 | embedded.html 현재 |
|---|---|---|
| `stream.end`에서 result_data 수신·저장 | §5-1 | **미구현** — `handleStream(end)` 분기에서 `data.insight`만 처리 |
| `stream.end`에서 process_summary 수신·저장 | §5-1 | **미구현** |
| `stream.end`에서 turn_id/user_turn_id 수신·저장 | §3 스키마 | **미구현** |
| `stream.end`에서 trace_files 수신·저장 | §3 스키마 | **미구현** |
| `renderResultTable()` 신규 함수 | §5-2 | **미구현** — 함수 자체 없음 |
| `renderProcessSummary()` 신규 함수 | §5-3 | **미구현** — 함수 자체 없음 |
| `render()` 분기: result_data 유무에 따른 마크다운 폴백 | §5-4 | **미구현** |
| 서버 취소 API 호출 | `20260404-pipeline-cancel-design.md` | **미구현** — UI만 초기화, `POST /api/sessions/{sid}/cancel` 미호출 |
| download_ready의 turn_id 저장 | §3 스키마 | **미구현** — turn_id 무시 |

---

## 2. 설계 원칙

1. **기존 IIFE 모듈 패턴 유지** — 새 함수는 기존 모듈(RD, ED, CN 등) 내부에 추가
2. **Vanilla JS, 외부 의존 없음** — 폐쇄망 단일 HTML 환경 유지
3. **과거 메시지 마크다운 폴백 보장** — result_data 없는 메시지는 기존 mdRender 경로 유지
4. **설계 문서 명명 규칙 준수** — JS: camelCase, CSS: kebab-case (리뷰 P-명명 일관성 항목)

---

## 3. 변경 상세

### 3-1. MS (MessageStore) — 메시지 모델 확장

**위치**: `embedded.html:691-700` `MS.create()` 내부

**변경**: msg 객체에 5개 필드 추가

```javascript
var msg = {
  id: id, role: role, text: text || '', status: x.status || 'done',
  progress: x.progress || [], visualization: x.visualization || null,
  showThinking: x.showThinking || false, thinkingLabel: x.thinkingLabel || '',
  progressCollapsed: false, progressStartTime: null, progressElapsed: null,
  insight: x.insight || null, downloadReady: x.downloadReady || null,
  // ★ 신규 5개 필드
  resultData: x.resultData || null,           // stream.end result_data
  processSummary: x.processSummary || null,    // stream.end process_summary
  turnId: x.turnId || null,                   // stream.end turn_id
  userTurnId: x.userTurnId || null,           // stream.end user_turn_id
  traceFiles: x.traceFiles || null,           // stream.end trace_files
  timestamp: Date.now()
};
```

### 3-2. ED (EventDispatcher) — handleStream(end) 필드 수신

**위치**: `embedded.html:1265-1273` `handleStream`의 `action==='end'` 분기

**현재 코드**:
```javascript
if(data.action==='end'&&_cur){
  var m2=MS.get(_cur);
  if(m2){SE.finalize(_cur);
    if(m2.progress.length){m2.progressElapsed=(Date.now()-(m2.progressStartTime||Date.now()))/1000;m2.progressCollapsed=true;RD.render(m2);}
    if(data.insight){m2.insight=data.insight;RD.render(m2);}
    SB.saveCurrentMessage('assistant',m2.text);
  }
  _last=_cur;_cur=null;_cbt();IC2.setBusy(false);
}
```

**변경 후**:
```javascript
if(data.action==='end'&&_cur){
  var m2=MS.get(_cur);
  if(m2){SE.finalize(_cur);
    if(m2.progress.length){m2.progressElapsed=(Date.now()-(m2.progressStartTime||Date.now()))/1000;m2.progressCollapsed=true;}
    // ★ 구조화 데이터 수신
    if(data.insight)       m2.insight = data.insight;
    if(data.result_data)   m2.resultData = data.result_data;
    if(data.process_summary) m2.processSummary = data.process_summary;
    if(data.turn_id)       m2.turnId = data.turn_id;
    if(data.user_turn_id)  m2.userTurnId = data.user_turn_id;
    if(data.trace_files)   m2.traceFiles = data.trace_files;
    RD.render(m2);
    SB.saveCurrentMessage('assistant',m2.text);
  }
  _last=_cur;_cur=null;_cbt();IC2.setBusy(false);
}
```

**설계 근거**:
- `RD.render(m2)`를 1회로 통합 — 기존에 progress와 insight에서 각각 render 호출하던 것을 단일 호출로 변경. 모든 필드가 세팅된 후 한 번에 렌더링하여 깜빡임 방지.
- 서버 JSON 키는 snake_case(`result_data`), JS 변수는 camelCase(`resultData`) — 리뷰 명명 규칙 준수.

### 3-3. ED (EventDispatcher) — handleDownloadReady turn_id 저장

**위치**: `embedded.html:1305-1311` `handleDownloadReady`

**현재 코드**:
```javascript
msg.downloadReady = {
  session_id: data.session_id,
  row_count: data.row_count || 0,
  formats: data.formats || ['csv']
};
```

**변경 후**:
```javascript
msg.downloadReady = {
  session_id: data.session_id,
  row_count: data.row_count || 0,
  formats: data.formats || ['csv'],
  turn_id: data.turn_id || null    // ★ 다운로드 이력 기록용
};
```

### 3-4. RD (RenderDispatcher) — ensureDOM 슬롯 추가

**위치**: `embedded.html:739-750` `ensureDOM`의 assistant innerHTML

**현재 DOM 구조**:
```
.msg-content
  ├─ .thinking-slot
  ├─ .progress-block-slot
  ├─ .msg-bubble.bot-bubble       ← 텍스트 (마크다운)
  ├─ .viz-slot                    ← 시각화
  ├─ .download-slot               ← 다운로드 바
  ├─ .msg-actions                 ← 복사/재생성/💡 버튼
  ├─ .insight-slot                ← insight 패널
  └─ .ai-logo
```

**변경 후 DOM 구조**:
```
.msg-content
  ├─ .thinking-slot
  ├─ .progress-block-slot
  ├─ .msg-bubble.bot-bubble       ← 텍스트 (핵심 수치 요약)
  ├─ .result-table-slot           ← ★ 신규: 구조화 테이블
  ├─ .process-summary-slot        ← ★ 신규: 조회 과정 요약 접기
  ├─ .viz-slot                    ← 시각화
  ├─ .download-slot               ← 다운로드 바
  ├─ .trace-slot                  ← ★ 신규: 트레이스 다운로드
  ├─ .msg-actions                 ← 복사/재생성/💡 버튼
  ├─ .insight-slot                ← insight 패널
  └─ .ai-logo
```

**설계 근거**:
- `result-table-slot`은 `bot-bubble` 바로 아래 — 텍스트 요약 → 테이블 순서 (설계 문서 §5-1)
- `process-summary-slot`은 테이블 아래 — 테이블 → 조회 과정 순서 (설계 문서 §5-1)
- `trace-slot`은 download-slot 아래 — 다운로드와 트레이스를 묶어 표시
- `viz-slot`은 테이블 아래, 다운로드 위 — 기존 위치 유지

### 3-5. RD — render() 함수 확장

**위치**: `embedded.html:772-804` `render()` 함수

**기존 렌더링 흐름 끝부분** (800-803):
```javascript
renderTh(row,msg);
renderPB(row,msg);
if(msg.visualization) renderViz(row,msg.visualization);
if(msg.insight){...renderInsight(row,msg);}
if(msg.downloadReady) renderDownload(row,msg);
autoScroll();
```

**변경 후**:
```javascript
renderTh(row,msg);
renderPB(row,msg);
// ★ 구조화 렌더링 (result_data 기반)
if(msg.resultData) renderResultTable(row, msg);
if(msg.processSummary) renderProcessSummary(row, msg);
if(msg.visualization) renderViz(row, msg.visualization);
if(msg.downloadReady) renderDownload(row, msg);
if(msg.traceFiles && msg.traceFiles.length) renderTrace(row, msg);
if(msg.insight){var ib=row.querySelector('[data-act="insight"]');if(ib)ib.style.display='';renderInsight(row,msg);}
autoScroll();
```

**마크다운 폴백 분기** — render() 내 bot-bubble 렌더링 부분:

```javascript
if(msg.role==='assistant'){
  if(msg.status==='streaming'){
    bub.innerHTML = mdRender(msg.text) + '<span class="streaming-cursor"></span>';
  } else {
    // ★ result_data가 있으면 텍스트 요약만 렌더링 (마크다운 테이블은 result_data로 대체)
    // result_data가 없으면 기존 마크다운 전체 렌더링 (과거 메시지 폴백)
    bub.innerHTML = mdRender(msg.text);
    bub._rawText = msg.text;
    if(msg.text) attachCodeCopy(bub);
  }
}
```

**설계 근거**: 텍스트 렌더링 자체는 변경 불필요. 서버 Phase 1에서 formatter가 이미 마크다운 테이블/`<details>`를 제거하고 핵심 수치 요약만 `stream.chunk`로 전송하므로, `mdRender(msg.text)`는 자연스럽게 짧은 텍스트만 렌더링한다. 과거 메시지(마크다운 테이블 포함)는 `result_data`가 null이므로 기존 경로 유지.

### 3-6. RD — renderResultTable() 신규 함수

**위치**: RD IIFE 내부, `renderDownload` 함수 앞

**설계 문서 참조**: §5-2

```javascript
function renderResultTable(row, msg) {
  var slot = row.querySelector('.result-table-slot');
  if (!slot || !msg.resultData) return;
  if (slot.querySelector('.result-table-wrap')) return; // 중복 방지

  var rd = msg.resultData;
  var cols = rd.columns || [];
  var rows = rd.rows || [];
  var fmts = rd.column_formats || {};
  if (!cols.length || !rows.length) return;

  // 테이블 HTML 생성
  var h = '<div class="result-table-wrap">';

  // 건수 안내 (절삭 시)
  if (rd.total_count && rd.displayed_count && rd.total_count > rd.displayed_count) {
    h += '<div class="result-table-info">전체 '
      + _fmtCount(rd.total_count) + ' 중 '
      + _fmtCount(rd.displayed_count) + '만 표시합니다.</div>';
  }

  h += '<div class="result-table-scroll"><table class="result-table">';

  // thead
  h += '<thead><tr>';
  cols.forEach(function(c) {
    var align = _isNumericFormat(fmts[c]) ? ' class="num"' : '';
    h += '<th' + align + '>' + esc(c) + '</th>';
  });
  h += '</tr></thead>';

  // tbody
  h += '<tbody>';
  rows.forEach(function(r) {
    h += '<tr>';
    cols.forEach(function(c) {
      var val = r[c];
      var fmt = fmts[c];
      var align = _isNumericFormat(fmt) ? ' class="num"' : '';
      h += '<td' + align + '>' + _fmtCell(val, fmt) + '</td>';
    });
    h += '</tr>';
  });
  h += '</tbody></table></div>';

  // CSV 복사 버튼
  h += '<div class="result-table-actions">'
    + '<button class="result-table-csv">' + ICOPY + ' CSV 복사</button>'
    + '</div>';

  h += '</div>';
  slot.innerHTML = h;

  // CSV 복사 이벤트
  var csvBtn = slot.querySelector('.result-table-csv');
  if (csvBtn) {
    csvBtn.addEventListener('click', function() {
      var csv = _resultDataToCSV(cols, rows);
      navigator.clipboard.writeText(csv).then(function() {
        csvBtn.innerHTML = IC + ' 복사됨';
        setTimeout(function() { csvBtn.innerHTML = ICOPY + ' CSV 복사'; }, 2000);
      }).catch(function() { toast('복사 실패'); });
    });
  }
}
```

#### 3-6-1. 셀 포맷팅 헬퍼 함수

설계 문서 §5-2 + 리뷰 P3-3 반영. 서버 `detect_column_formats()`의 결과를 기반으로 포맷팅.

```javascript
function _isNumericFormat(fmt) {
  return fmt === 'currency' || fmt === 'rate' || fmt === 'count';
}

function _fmtCell(val, fmt) {
  if (val === null || val === undefined || val === '') return '<span class="null-val">-</span>';
  if (!fmt || fmt === 'text') return esc(String(val));
  var n = parseFloat(val);
  if (isNaN(n)) return esc(String(val));
  if (fmt === 'currency') return _fmtCurrency(n);
  if (fmt === 'rate')     return n.toFixed(1) + '%';
  if (fmt === 'count')    return n.toLocaleString('ko-KR') + '건';
  return esc(String(val));
}

function _fmtCurrency(n) {
  var abs = Math.abs(n);
  var sign = n < 0 ? '-' : '';
  if (abs >= 1e12)      return sign + (abs / 1e12).toFixed(1) + '조원';
  if (abs >= 1e8)       return sign + (abs / 1e8).toFixed(1) + '억원';
  if (abs >= 1e4)       return sign + (abs / 1e4).toFixed(0) + '만원';
  return sign + abs.toLocaleString('ko-KR') + '원';
}

function _fmtCount(n) {
  return parseInt(n, 10).toLocaleString('ko-KR') + '건';
}

function _resultDataToCSV(cols, rows) {
  var lines = [cols.map(function(c) { return '"' + c.replace(/"/g, '""') + '"'; }).join(',')];
  rows.forEach(function(r) {
    lines.push(cols.map(function(c) {
      var v = r[c];
      if (v === null || v === undefined) v = '';
      return '"' + String(v).replace(/"/g, '""') + '"';
    }).join(','));
  });
  return lines.join('\n');
}
```

### 3-7. RD — renderProcessSummary() 신규 함수

**위치**: RD IIFE 내부, `renderResultTable` 뒤

**설계 문서 참조**: §5-3 — 본문 하단 접기, ref-block 스타일 (현재 embedded.html에 ref-block CSS 없으므로 신규 추가)

```javascript
function renderProcessSummary(row, msg) {
  var slot = row.querySelector('.process-summary-slot');
  if (!slot || !msg.processSummary) return;
  if (slot.querySelector('.process-summary-block')) return; // 중복 방지

  var ps = msg.processSummary;
  var h = '<details class="process-summary-block">';
  h += '<summary class="process-summary-header">'
    + '<span class="process-summary-tag">참고</span>'
    + '<span class="process-summary-title">조회 과정 요약</span>'
    + '</summary>';
  h += '<div class="process-summary-body">';

  // 1. 질의 분류
  if (ps.intent) {
    h += '<div class="ps-step"><span class="ps-num">1</span>'
      + '<span class="ps-label">질의 분류</span>'
      + '<span class="ps-value">' + esc(ps.intent.label || '')
      + (ps.intent.is_continuation ? ' (이전 대화 연속)' : '') + '</span></div>';
  }

  // 2. 질의 해석
  if (ps.interpretation && Object.keys(ps.interpretation).length) {
    var interp = ps.interpretation;
    var parts = [];
    if (interp.measures)   parts.push('지표: ' + interp.measures.map(esc).join(', '));
    if (interp.period)     parts.push('기간: ' + esc(interp.period));
    if (interp.entities)   parts.push('대상: ' + interp.entities.map(esc).join(', '));
    if (interp.filters)    parts.push('조건: ' + interp.filters.map(esc).join(', '));
    if (interp.dimensions) parts.push('분류: ' + interp.dimensions.map(esc).join(', '));
    h += '<div class="ps-step"><span class="ps-num">2</span>'
      + '<span class="ps-label">질의 해석</span>'
      + '<span class="ps-value">' + parts.join(' · ') + '</span></div>';
  }

  // 3. 활용 정보
  if (ps.context && Object.keys(ps.context).length) {
    var ctx = ps.context;
    var ctxParts = [];
    if (ctx.tables && ctx.tables.length) {
      ctxParts.push('테이블 ' + ctx.tables.length + '개 ('
        + ctx.tables.map(function(t) { return esc(t.label || t.name); }).join(', ') + ')');
    }
    if (ctx.use_case_count)  ctxParts.push('유사 SQL ' + ctx.use_case_count + '건 참조');
    if (ctx.manual_count)    ctxParts.push('업무 매뉴얼 ' + ctx.manual_count + '건 참조');
    if (ctx.biz_terms && ctx.biz_terms.length) {
      ctxParts.push('용어: ' + ctx.biz_terms.map(esc).join(', '));
    }
    h += '<div class="ps-step"><span class="ps-num">3</span>'
      + '<span class="ps-label">활용 정보</span>'
      + '<span class="ps-value">' + ctxParts.join(' · ') + '</span></div>';
  }

  // 4. AI 판단 (있을 때만)
  if (ps.ai_decisions) {
    var ai = ps.ai_decisions;
    var aiParts = [];
    if (ai.inferences && ai.inferences.length) {
      ai.inferences.forEach(function(inf) {
        aiParts.push(esc(inf.question) + ' → ' + esc(inf.value));
      });
    }
    if (ai.pending_assumptions && ai.pending_assumptions.length) {
      ai.pending_assumptions.forEach(function(a) { aiParts.push(esc(a)); });
    }
    h += '<div class="ps-step ai-step"><span class="ps-num">4</span>'
      + '<span class="ps-label">AI 판단</span>'
      + '<span class="ps-value">' + aiParts.join('<br>') + '</span></div>';
    if (ai.notice) {
      h += '<div class="ps-notice">' + esc(ai.notice) + '</div>';
    }
  }

  // 5. 검증 결과
  if (ps.validation && Object.keys(ps.validation).length) {
    var val = ps.validation;
    var valText = val.summary || '';
    if (val.row_label) valText += (valText ? ' · ' : '') + val.row_label;
    h += '<div class="ps-step"><span class="ps-num">5</span>'
      + '<span class="ps-label">검증 결과</span>'
      + '<span class="ps-value">' + esc(valText) + '</span></div>';
  }

  h += '</div></details>';
  slot.innerHTML = h;
}
```

### 3-8. RD — renderTrace() 신규 함수

**위치**: RD IIFE 내부

**설계 문서 참조**: `20260407-trace-download-restore-fix.md`

```javascript
function renderTrace(row, msg) {
  var slot = row.querySelector('.trace-slot');
  if (!slot || !msg.traceFiles || !msg.traceFiles.length) return;
  if (slot.querySelector('.trace-bar')) return; // 중복 방지

  var h = '<div class="trace-bar">';
  msg.traceFiles.forEach(function(tf) {
    h += '<a class="trace-link" href="/api/traces/'
      + encodeURIComponent(tf.filename) + '" download>'
      + '📄 ' + esc(tf.name || tf.filename) + '</a>';
  });
  h += '</div>';
  slot.innerHTML = h;
}
```

### 3-9. CN (Connection) — sid 외부 노출 + 서버 취소 API 호출

**위치**: `embedded.html:1342-1363` CN 모듈

**변경 1**: `sid` 게터 추가

```javascript
// 기존: return {connect, send, reconnect};
// 변경:
return { connect: connect, send: send, reconnect: reconnect, getSid: function() { return sid; } };
```

**변경 2**: App.cancelStream에서 서버 취소 API 호출

**위치**: `embedded.html:1632-1638` `App.cancelStream()`

**현재 코드**:
```javascript
cancelStream: function() {
  SE.cancelAll();
  var id = ED.getCurrentId();
  if (id) {
    MS.update(id, {status:'done', showThinking:false, progressCollapsed:true});
    var m = MS.get(id);
    if (m) { m.progressElapsed = m.progressStartTime ? (Date.now()-m.progressStartTime)/1000 : null; RD.render(m); }
  }
  IC2.setBusy(false);
},
```

**변경 후**:
```javascript
cancelStream: function() {
  SE.cancelAll();
  var id = ED.getCurrentId();
  if (id) {
    MS.update(id, {status:'done', showThinking:false, progressCollapsed:true});
    var m = MS.get(id);
    if (m) { m.progressElapsed = m.progressStartTime ? (Date.now()-m.progressStartTime)/1000 : null; RD.render(m); }
  }
  IC2.setBusy(false);
  // ★ 서버 파이프라인 취소 요청
  var sid = CN.getSid();
  if (sid) {
    fetch('/api/sessions/' + encodeURIComponent(sid) + '/cancel', { method: 'POST' })
      .catch(function(err) { console.warn('[Cancel]', err); });
  }
},
```

**설계 근거**:
- fire-and-forget 패턴 — 취소 실패 시에도 UI는 이미 정상화됨
- 서버 엔드포인트: `POST /api/sessions/{session_id}/cancel` (`routers/sessions.py:188`)
- 취소 플래그 설정 후 다음 노드 진입 시 파이프라인 정상 종료(CANCELLED)

### 3-10. App.downloadCSV — turn_id 기반 다운로드 이력 기록

**위치**: `embedded.html:1692-1705` `App.downloadCSV()`

**변경 후**:
```javascript
downloadCSV: function(sessionId, turnId) {
  fetch('/api/download', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({session_id: sessionId, format: 'csv'})
  }).then(function(r) {
    if (!r.ok) throw new Error('다운로드 실패');
    return r.blob();
  }).then(function(blob) {
    var u = URL.createObjectURL(blob);
    var a = document.createElement('a'); a.href = u; a.download = 'data-copilot-export.csv'; a.click();
    URL.revokeObjectURL(u);
    toast('다운로드 완료');
    // ★ 다운로드 이력 기록
    if (turnId) {
      fetch('/api/turns/' + encodeURIComponent(turnId) + '/download', { method: 'PATCH' })
        .catch(function() {});
    }
  }).catch(function(err) { toast('다운로드 실패: ' + err.message); });
}
```

**renderDownload에서 호출부 변경**:
```javascript
// 기존: dbtn.addEventListener('click', function() { App.downloadCSV(dr.session_id); });
// 변경:
dbtn.addEventListener('click', function() { App.downloadCSV(dr.session_id, dr.turn_id); });
```

### 3-11. App.regen — turn_id 전달 (Phase 2 확장 스킵, 텍스트만 추가)

**현재 상태**: `regen`은 단순 텍스트 재전송. 서버가 아직 `action:"regen"` JSON 수신 프로토콜을 지원하지 않으므로 현행 유지.

**단, 하나만 개선**: 재생성 시 이전 assistant 메시지의 `turnId`를 참조할 수 있도록, 제거 대상 메시지의 turnId를 로깅.

> 서버측 `regen` 프로토콜이 구현되면 별도 설계에서 다룬다.

---

## 4. CSS 추가

**위치**: `<style>` 블록 끝부분 (Download Bar 섹션 뒤)

### 4-1. 구조화 테이블 CSS

```css
/* ══ Result Table (구조화 테이블) ══ */
.result-table-slot { margin-top: 8px; }
.result-table-wrap {
  border: 1px solid var(--border); border-radius: var(--r-sm);
  overflow: hidden; animation: fadeUp .3s var(--ease);
}
.result-table-info {
  padding: 6px 12px; font-size: 12px; color: var(--txt3);
  background: var(--bg2); border-bottom: 1px solid var(--border);
}
.result-table-scroll {
  overflow-x: auto; overflow-y: auto; max-height: 480px;
}
.result-table {
  width: 100%; border-collapse: collapse; font-size: 13px;
  font-variant-numeric: tabular-nums;
}
.result-table th {
  position: sticky; top: 0; background: var(--bg2); z-index: 1;
  font-weight: 600; color: var(--txt2); font-size: 12px;
  padding: 8px 12px; text-align: left; white-space: nowrap;
  border-bottom: 2px solid var(--border2);
}
.result-table td {
  padding: 7px 12px; border-bottom: 1px solid var(--border);
  white-space: nowrap;
}
.result-table th.num, .result-table td.num { text-align: right; }
.result-table tbody tr:hover { background: var(--bg2); }
.result-table .null-val { color: var(--txt3); font-style: italic; }
.result-table-actions {
  display: flex; gap: 8px; padding: 6px 12px;
  background: var(--bg2); border-top: 1px solid var(--border);
}
.result-table-csv {
  display: flex; align-items: center; gap: 4px;
  padding: 3px 10px; font-size: 11px; font-family: var(--sans);
  color: var(--txt2); background: none; border: 1px solid var(--border);
  border-radius: var(--r-xs); cursor: pointer; transition: all var(--t-fast);
}
.result-table-csv:hover { background: var(--bg3); color: var(--txt); }
```

### 4-2. 조회 과정 요약 CSS

```css
/* ══ Process Summary (조회 과정 접기) ══ */
.process-summary-slot { margin-top: 8px; }
.process-summary-block {
  border: 1px solid var(--border); border-radius: var(--r-sm);
  overflow: hidden; animation: fadeUp .3s var(--ease);
}
.process-summary-header {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 12px; cursor: pointer; font-size: 13px;
  background: var(--bg2); transition: background var(--t-fast);
}
.process-summary-header:hover { background: var(--bg3); }
.process-summary-tag {
  font-size: 10.5px; font-weight: 600; color: var(--txt-accent);
  background: var(--bg-accent-subtle); padding: 1px 6px;
  border-radius: var(--r-xs); letter-spacing: .02em;
}
.process-summary-title { font-weight: 500; color: var(--txt2); }
.process-summary-body { padding: 8px 12px 12px; }
.ps-step {
  display: flex; align-items: baseline; gap: 8px;
  padding: 4px 0; font-size: 12.5px; line-height: 1.6;
}
.ps-num {
  flex-shrink: 0; width: 18px; height: 18px;
  display: flex; align-items: center; justify-content: center;
  background: var(--bg3); color: var(--txt3); font-size: 11px;
  font-weight: 600; border-radius: 50%;
}
.ps-label { flex-shrink: 0; width: 64px; font-weight: 500; color: var(--txt3); }
.ps-value { color: var(--txt2); }
.ps-step.ai-step { color: var(--txt-accent); }
.ps-step.ai-step .ps-value { color: var(--txt-accent); }
.ps-notice {
  margin: 4px 0 0 26px; padding: 4px 8px;
  font-size: 12px; color: var(--txt-accent); font-style: italic;
  background: var(--bg-accent-subtle); border-radius: var(--r-xs);
}
```

### 4-3. 트레이스 다운로드 CSS

```css
/* ══ Trace Download ══ */
.trace-slot { margin-top: 4px; }
.trace-bar { display: flex; flex-wrap: wrap; gap: 6px; }
.trace-link {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 3px 10px; font-size: 11px; color: var(--txt2);
  background: var(--bg2); border: 1px solid var(--border);
  border-radius: var(--r-xs); text-decoration: none;
  transition: all var(--t-fast);
}
.trace-link:hover { background: var(--bg3); color: var(--txt); }
```

---

## 5. RD 모듈 export 확장

**위치**: `embedded.html:1160`

**현재**: `return {ensureDOM, render, showBanner, hideWel, showWel, clearChat};`

**변경 없음** — `renderResultTable`, `renderProcessSummary`, `renderTrace`는 `render()` 내부에서만 호출되므로 export 불필요.

---

## 6. 변경량 요약

| 구분 | 파일 | 변경 규모 |
|------|------|-----------|
| MS 모델 확장 | embedded.html | +5줄 (필드 추가) |
| ED handleStream(end) | embedded.html | ±15줄 (필드 수신 + render 통합) |
| ED handleDownloadReady | embedded.html | +1줄 (turn_id) |
| RD ensureDOM | embedded.html | +3줄 (슬롯 추가) |
| RD render() | embedded.html | +5줄 (분기 추가) |
| RD renderResultTable() | embedded.html | +80줄 (함수 + 헬퍼) |
| RD renderProcessSummary() | embedded.html | +65줄 (함수) |
| RD renderTrace() | embedded.html | +15줄 (함수) |
| CN getSid() | embedded.html | +1줄 |
| App.cancelStream | embedded.html | +5줄 (서버 cancel 호출) |
| App.downloadCSV | embedded.html | +5줄 (turn_id, download 기록) |
| CSS 추가 | embedded.html | +90줄 |
| **합계** | **embedded.html** | **약 +290줄** |

---

## 7. 구현 순서

| 단계 | 내용 | 의존 |
|------|------|------|
| 1 | CSS 추가 (§4) | 없음 |
| 2 | MS 모델 확장 (§3-1) | 없음 |
| 3 | RD ensureDOM 슬롯 추가 (§3-4) | 없음 |
| 4 | RD 헬퍼 함수 추가 (_fmtCell, _fmtCurrency 등) | 없음 |
| 5 | RD renderResultTable (§3-6) | 2, 3, 4 |
| 6 | RD renderProcessSummary (§3-7) | 2, 3 |
| 7 | RD renderTrace (§3-8) | 2, 3 |
| 8 | RD render() 확장 (§3-5) | 5, 6, 7 |
| 9 | ED handleStream(end) 수신 (§3-2) | 2 |
| 10 | ED handleDownloadReady turn_id (§3-3) | 없음 |
| 11 | CN getSid 노출 (§3-9) | 없음 |
| 12 | App.cancelStream 서버 연동 (§3-9) | 11 |
| 13 | App.downloadCSV turn_id (§3-10) | 10 |

---

## 8. 미포함 사항 (별도 설계 필요)

| 항목 | 사유 |
|---|---|
| **세션 복원 시 구조화 데이터 렌더링** | 현재 loadSession은 localStorage 기반 짧은 텍스트 복원만 수행. 서버 API(`GET /api/sessions/{id}`)에서 턴 metadata를 가져와 result_data/process_summary를 복원하려면 세션 관리 체계 자체 리팩토링 필요. 별도 설계 문서에서 다룸. |
| **App.regen 서버 프로토콜** | 서버가 `action:"regen"` + `original_turn_id` JSON 수신을 지원하지 않음. 서버측 구현 후 프론트 연동. |
| **Phase 3: viz table_data 제거** | 설계 문서 §4-1에 따라 Phase 2 안정화 후 진행. |

---

## 9. 테스트 체크리스트

### 기능 테스트

- [ ] 데이터 추출 질의 → stream.end에 result_data 수신 → 구조화 테이블 렌더링
- [ ] column_formats: currency(조/억/만원), rate(%), count(건) 포맷팅 정상
- [ ] NULL 셀 → `-` (이탤릭) 표시
- [ ] total_count > displayed_count → "전체 N건 중 M건만 표시" 안내
- [ ] CSV 복사 버튼 → 클립보드에 CSV 형식 복사
- [ ] process_summary → 접기 블록 5단계 렌더링
- [ ] ai_decisions 없는 경우 → 4단계 생략
- [ ] trace_files → 다운로드 링크 표시, 클릭 시 파일 다운로드
- [ ] 취소 버튼 → 서버 cancel API 호출 확인 (네트워크 탭)
- [ ] 다운로드 → PATCH /api/turns/{turnId}/download 호출 확인
- [ ] insight 💡 버튼 → 기존 insight 패널 정상 동작

### 폴백 테스트

- [ ] result_data 없는 메시지 (명확화 질문, 일반 응답) → 기존 마크다운 렌더링
- [ ] 과거 대화 이력 (localStorage) → 마크다운 테이블 포함 메시지 정상 렌더링
- [ ] 서버 cancel API 실패 → UI 정상화에 영향 없음

### 테마 테스트

- [ ] Light / Dim / Dark 3개 테마에서 구조화 테이블 가독성
- [ ] 모바일 뷰포트에서 테이블 가로 스크롤 정상
