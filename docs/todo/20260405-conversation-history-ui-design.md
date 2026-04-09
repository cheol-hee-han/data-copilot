# 대화 이력 관리 — UI 구현 요구사항 및 상세 설계

- **작성일**: 2026-04-05
- **상태**: 설계 완료, 구현 대기
- **참조 백엔드 설계**: `docs/todo/20260405-postgres-conversation-history-design.md`
- **대상 파일**: `static/embedded.html` (vanilla JS SPA)
- **영향 범위**: SB(사이드바), ED(이벤트 디스패처), MS(메시지 스토어), CN(WebSocket), App 모듈

---

## 1. 현재 UI 구현 현황 분석

### 1.1 세션 관리 (SB 모듈)

| 항목 | 현재 구현 |
|------|----------|
| 저장소 | `localStorage` (`dc-sessions` 키) |
| 세션 ID | 클라이언트 생성 (`'s-' + Date.now()`) |
| 세션 제목 | 첫 메시지 앞 30자 자동 설정 (제목이 "새 대화"인 경우만) |
| 최대 보관 | 20개 (초과 시 가장 오래된 것 삭제) |
| 세션 데이터 | `{ id, title, msgs: [{role, text(200자), time}], ts }` |
| 삭제 | confirm 모달 후 localStorage에서 즉시 제거 |

### 1.2 메시지 관리 (MS 모듈)

| 항목 | 현재 구현 |
|------|----------|
| 저장소 | 인메모리 `Map` + `sessionStorage` (`dc-hist` 키) |
| 메시지 ID | `'msg-{timestamp}-{counter}'` (클라이언트 생성) |
| 메시지 구조 | `{ id, role, text, status, progress, visualization, insight, downloadReady, timestamp }` |
| 영속성 | 브라우저 탭 닫으면 `sessionStorage` 유실, `localStorage`에는 요약만 |

### 1.3 WebSocket (CN 모듈)

| 항목 | 현재 구현 |
|------|----------|
| WebSocket SID | `'session-' + Date.now()` (브라우저 세션마다 새로 생성) |
| URL 패턴 | `/ws/{sid}` |
| 재연결 | 최대 5회, 지수 백오프 (1s, 2s, 4s, 8s, 16s) |
| 메시지 프로토콜 | `stream`, `progress`, `viz`, `download_ready`, `status`, `error` |

### 1.4 메시지 액션 (현재 존재하는 것)

| 액션 | 구현 상태 |
|------|----------|
| 복사 (copy) | 구현됨 — `act-btn[data-act="copy"]` |
| 재생성 (regen) | 구현됨 — `act-btn[data-act="regen"]` |
| 분석 과정 (insight 💡) | 구현됨 — `act-btn[data-act="insight"]`, insight 패널 토글 |
| SVG/PNG 다운로드 | 구현됨 — viz-actions 영역 내 버튼 |
| CSV 다운로드 | 구현됨 — `POST /api/download` (download_ready 메시지 기반) |
| **좋아요/싫어요** | **미구현** |
| **다운로드 기록(서버)** | **미구현** (클라이언트 측 다운로드만) |

### 1.5 주요 GAP 요약

```
[현재]                              [TO-BE]
localStorage 기반 세션        →     REST API 기반 세션 (서버 영속)
클라이언트 세션 ID            →     서버 thread_id (= session_id)
요약 텍스트만 보관(200자)     →     전체 content 서버 저장 + 2-tier 로딩
좋아요/싫어요 없음            →     3-state 좋아요 (null/true/false)
다운로드 기록 없음(서버)      →     PATCH /api/turns/{id}/download
localStorage 즉시 삭제        →     soft delete (is_archived)
에러 턴 표시 없음             →     turn_type='error', status='failure' 표시
```

---

## 2. UI 구현 요구사항

### 2.1 인프라: API 통합 레이어

#### REQ-INFRA-01 — REST API 클라이언트 모듈 (필수)

**현재**: `downloadCSV()` 1곳에서만 `fetch` 직접 호출.
**변경**: 6개 이상 REST 엔드포인트를 호출해야 하므로 공통 API 모듈 필요.

```javascript
// 예시 구조 — embedded.html 내 API 모듈
const API = (function(){
  const BASE = '/api';

  async function request(method, path, body) {
    const opts = { method, headers: {'Content-Type':'application/json'} };
    if (body) opts.body = JSON.stringify(body);
    const res = await fetch(BASE + path, opts);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw { status: res.status, detail: err.detail || res.statusText };
    }
    return res.json();
  }

  return {
    // 세션
    getSessions:   (userId, limit, offset) => request('GET', `/sessions?user_id=${userId}&limit=${limit||20}&offset=${offset||0}`),
    getSession:    (sessionId)             => request('GET', `/sessions/${sessionId}`),
    deleteSession: (sessionId)             => request('DELETE', `/sessions/${sessionId}`),
    // 턴
    getTurnMeta:   (turnId)                => request('GET', `/turns/${turnId}/metadata`),
    toggleLike:    (turnId, isLiked)        => request('PATCH', `/turns/${turnId}/like`, { is_liked: isLiked }),
    markDownload:  (turnId)                => request('PATCH', `/turns/${turnId}/download`),
  };
})();
```

#### REQ-INFRA-02 — 메시지 ID 체계 전환 (필수)

**현재**: 클라이언트 전용 ID (`msg-{ts}-{n}`). 서버 `turn_id`와 무관.
**변경**: 서버 응답에 `turn_id`를 포함시키고, MS 모듈의 메시지에 `turnId` 필드를 추가하여 매핑.

```javascript
// MS.create() 확장
function create(role, text, opts) {
  var m = {
    id: 'msg-' + Date.now() + '-' + (++_n),
    turnId: opts && opts.turnId || null,   // ← 서버 turn_id
    // ... 기존 필드
  };
}
```

> **WebSocket 프로토콜 확장 필요**: `stream.end` 또는 `response` 메시지에 `turn_id` 필드를 포함해야 함.
> 백엔드에서 `save_turn()` 후 생성된 `turn_id`를 WebSocket 응답에 실어보내는 구조.

---

### 2.2 사이드바 — 세션 목록 (SB 모듈)

#### REQ-SB-01 — REST API 기반 세션 목록 조회 (필수)

**현재**: `SB.init()` → localStorage에서 `dc-sessions` 로드.
**변경**: `SB.init()` → `API.getSessions('anonymous')` 호출.

```javascript
async function init() {
  try {
    var resp = await API.getSessions('anonymous', 20, 0);
    _sessions = resp.sessions.map(function(s){
      return { id: s.session_id, title: s.title || '새 대화', ts: new Date(s.last_active).getTime() };
    });
    _total = resp.total_count;
  } catch(e) {
    // 폴백: localStorage 캐시 사용 (오프라인 대응)
    _sessions = JSON.parse(localStorage.getItem('dc-sessions') || '[]');
  }
  renderList();
}
```

**설계 결정**:
- 서버 응답을 localStorage에 캐시하여 오프라인/에러 시 폴백
- `total_count`로 추가 세션 존재 여부 표시 가능 (20개 초과 시)

#### REQ-SB-02 — 세션 ID 체계 서버 동기화 (필수)

**현재**: 클라이언트가 `'s-' + Date.now()`로 세션 ID 생성.
**변경**: WebSocket 연결 시 서버가 발급한 `session_id`(= thread_id)를 사용.

> WebSocket SID(`CN.sid`)와 백엔드의 `session_id`가 동일해야 함.
> 현재 `CN.sid = 'session-' + Date.now()`이 runner.py의 thread_id로 직접 사용되므로,
> SB 모듈의 세션 ID도 `CN.sid`를 기준으로 통일.

#### REQ-SB-03 — 세션 제목 서버 저장 (필수)

**현재**: `SB.updateTitle(text)` → 클라이언트에서 첫 메시지 앞 30자로 자동 설정.
**변경**: 서버의 `session_index.title`이 첫 질의 앞 50자로 자동 설정되므로, 세션 목록 조회 시 서버 제목 사용.
- 클라이언트 `updateTitle()`은 **즉시 UI 갱신용**(optimistic)으로 유지하되, 서버 응답이 오면 서버 제목으로 동기화.

#### REQ-SB-04 — 날짜 그룹 구분 (권고)

**현재**: 세션 목록이 시간순 flat list.
**변경 권고**: "오늘", "어제", "이번 주", "이전" 등 날짜 그룹 구분.

```
── 오늘 ──
  신규 고객 수 알려줘...
  대출 유형별 건수와 금액...
── 어제 ──
  연체율 추이 분석해줘...
── 이번 주 ──
  지점별 고객 수 상위 5개...
```

**근거**: 은행 직원은 "어제 뽑았던 데이터"처럼 시간 기준으로 대화를 기억함.

#### REQ-SB-05 — 세션 목록 무한 스크롤 / 더 보기 (권고)

**현재**: 최대 20개 표시, 초과분 유실.
**변경 권고**: `GET /api/sessions`의 `offset` 파라미터 활용, "더 보기" 버튼 또는 스크롤 시 추가 로드.
- 일반 사용자는 최근 20개로 충분하나, 장기 사용 시 과거 대화 탐색 필요.

---

### 2.3 대화 복원 — 2-tier 로딩

#### REQ-CR-01 — Tier 1: 전체 턴 즉시 로드 (필수)

**현재**: `SB.loadSession(id)` → localStorage의 `msgs` 배열(200자 요약)을 렌더링.
**변경**: `API.getSession(sessionId)` → 전체 content 포함 턴 목록 반환, MS에 로드.

```javascript
async function loadSession(id) {
  _active = id;
  MS.clear();
  RD.clearChat();

  try {
    var resp = await API.getSession(id);
    resp.turns.forEach(function(t) {
      MS.create(t.role, t.content, {
        turnId: t.turn_id,
        turnType: t.turn_type,
        status: t.status,
        isLiked: t.is_liked,
        isDownloaded: t.is_downloaded,
        hasMetadata: t.has_metadata,
        createdAt: t.created_at,
        restored: true  // 복원된 메시지 표시용 플래그
      });
    });
  } catch(e) {
    RD.showBanner('이전 대화를 불러올 수 없습니다.', 'error');
  }

  // WebSocket을 해당 session_id로 재연결
  CN.reconnectWith(id);
  renderList();
}
```

#### REQ-CR-02 — Tier 2: metadata 지연 로드 (필수)

**현재**: insight/visualization은 WebSocket 실시간 수신 시에만 존재.
**변경**: 복원된 assistant 턴에 `has_metadata: true`이면, 사용자가 해당 턴과 상호작용(insight 버튼 클릭, 차트 영역 진입 등) 시 Tier 2 로드.

```javascript
// insight 버튼 클릭 시
async function onInsightClick(msgId) {
  var msg = MS.get(msgId);
  if (!msg || !msg.turnId) return;

  // 이미 로드된 경우 토글만
  if (msg.insight) { toggleInsightPanel(msgId); return; }

  // Tier 2 로드
  try {
    var resp = await API.getTurnMeta(msg.turnId);
    MS.update(msgId, {
      insight: resp.metadata.insight,
      visualization: resp.metadata.visualization,
    });
    renderInsight(/* ... */);
    renderVisualization(/* ... */);
  } catch(e) {
    showToast('상세 정보를 불러올 수 없습니다.');
  }
}
```

**설계 결정**:
- Tier 2 로드 트리거: insight 💡 버튼 클릭, 차트 영역 viewport 진입
- 한번 로드된 metadata는 MS에 캐시 → 재클릭 시 재요청 안 함
- 로딩 중 skeleton/spinner 표시 (아래 REQ-CR-03)

#### REQ-CR-03 — metadata 로딩 인디케이터 (필수)

**현재**: 없음.
**변경**: Tier 2 로드 중 해당 영역에 로딩 표시.

```html
<!-- insight 로딩 -->
<div class="insight-loading">
  <span class="thinking-label">분석 과정 불러오는 중…</span>
</div>

<!-- 차트 로딩 -->
<div class="viz-loading">
  <div class="viz-body" style="min-height:120px;display:flex;align-items:center;justify-content:center">
    <span style="color:var(--txt3);font-size:13px">차트 불러오는 중…</span>
  </div>
</div>
```

#### REQ-CR-04 — 복원 메시지와 실시간 메시지 시각 구분 (권고)

**현재**: 모든 메시지가 동일한 스타일.
**변경 권고**: 복원된 과거 메시지에 타임스탬프 표시.

```html
<!-- 복원 메시지 하단 -->
<span class="msg-time">2026.04.05 14:30</span>
```

```css
.msg-time{font-size:11px;color:var(--txt3);margin-top:4px;display:block}
```

**근거**: 과거 대화 복원 시 "언제 한 대화인지" 맥락이 중요. 실시간 대화에는 불필요.

#### REQ-CR-05 — 복원 후 이어서 질문 (필수)

**현재**: 세션 로드 시 메시지 표시만 가능, WebSocket 연결은 새 SID.
**변경**: `CN.reconnectWith(sessionId)` — 기존 session_id로 WebSocket 재연결하여 체크포인터 상태를 이어받음.

```javascript
// CN 모듈 확장
function reconnectWith(sessionId) {
  if (ws) ws.close();
  sid = sessionId;  // 서버의 thread_id와 동일
  connect();
}
```

> 이렇게 하면 과거 세션의 interrupt(명확화) 상태도 복원 가능.

---

### 2.4 좋아요/싫어요 — 신규 기능

#### REQ-LIKE-01 — 좋아요/싫어요 UI 추가 (필수)

**현재**: msg-actions에 copy, regen, insight 버튼만 존재.
**변경**: assistant 턴의 msg-actions에 좋아요(👍)/싫어요(👎) 버튼 추가.

```javascript
// RD 모듈 — assistant 메시지 렌더링 시
+'<button class="act-btn like-btn" data-act="like" data-state="null" title="좋아요">'
+  ICON_THUMBUP
+'</button>'
+'<button class="act-btn dislike-btn" data-act="dislike" data-state="null" title="싫어요">'
+  ICON_THUMBDOWN
+'</button>'
```

**3-state 동작**:

| 현재 상태 | 👍 클릭 | 👎 클릭 |
|----------|---------|---------|
| null (미평가) | → true (좋아요) | → false (싫어요) |
| true (좋아요) | → null (취소) | → false (전환) |
| false (싫어요) | → true (전환) | → null (취소) |

```css
/* 좋아요/싫어요 활성 상태 */
.act-btn.liked{color:#22c55e}
.act-btn.disliked{color:#ef4444}
```

#### REQ-LIKE-02 — 좋아요 서버 동기화 (필수)

```javascript
async function onLikeClick(msgId, newState) {
  var msg = MS.get(msgId);
  if (!msg || !msg.turnId) return;

  // Optimistic UI 갱신
  var prevState = msg.isLiked;
  MS.update(msgId, { isLiked: newState });
  updateLikeButtons(msgId, newState);

  try {
    await API.toggleLike(msg.turnId, newState);
  } catch(e) {
    // 롤백
    MS.update(msgId, { isLiked: prevState });
    updateLikeButtons(msgId, prevState);
    showToast('평가를 저장할 수 없습니다.');
  }
}
```

#### REQ-LIKE-03 — 싫어요 시 피드백 입력 (권고)

**변경 권고**: 싫어요(👎) 클릭 시 선택적 피드백 입력 팝업.

```
┌──────────────────────────────────────┐
│  어떤 점이 아쉬우셨나요? (선택)       │
│                                      │
│  ○ 잘못된 데이터            │
│  ○ SQL이 의도와 다름                 │
│  ○ 응답이 불친절함                   │
│  ○ 기타: [____________]             │
│                                      │
│              [건너뛰기] [제출]        │
└──────────────────────────────────────┘
```

**근거**: 좋아요/싫어요만으로는 품질 개선 정보가 부족. 피드백은 프롬프트 개선의 핵심 데이터.
**구현 시점**: 프롬프트 개선 프로세스 구축 후. 현재는 좋아요/싫어요만으로도 충분.

---

### 2.5 다운로드 기록

#### REQ-DL-01 — 다운로드 시 서버 기록 (필수)

**현재**: `App.downloadCSV(sessionId)` → 서버에서 Blob 다운로드만.
**변경**: 다운로드 완료 후 `API.markDownload(turnId)` 호출.

```javascript
async downloadCSV(sessionId) {
  // 기존 다운로드 로직 유지
  const blob = await fetch('/api/download', { ... }).then(r => r.blob());
  // ... 브라우저 다운로드 트리거

  // 서버 기록 (fire-and-forget, 실패해도 다운로드에 영향 없음)
  var msg = findDownloadMessage(sessionId);
  if (msg && msg.turnId) {
    API.markDownload(msg.turnId).catch(function(){});
    MS.update(msg.id, { isDownloaded: true });
  }
}
```

#### REQ-DL-02 — 다운로드 완료 상태 표시 (권고)

**현재**: 다운로드 바에 매번 동일하게 "다운로드" 버튼 표시.
**변경 권고**: 복원된 턴에서 `is_downloaded: true`이면 "다운로드됨 ✓" 상태 표시.

```html
<!-- is_downloaded: true인 경우 -->
<div class="download-bar downloaded">
  <span>📥 이미 다운로드됨</span>
  <button>다시 다운로드</button>
</div>
```

---

### 2.6 세션 삭제 — soft delete 전환

#### REQ-DEL-01 — REST API 기반 세션 삭제 (필수)

**현재**: `SB.deleteSession(id)` → localStorage에서 즉시 제거.
**변경**: `API.deleteSession(id)` 호출 → 서버 soft delete (is_archived = true).

```javascript
async function deleteSession(id) {
  try {
    await API.deleteSession(id);
    _sessions = _sessions.filter(function(s){ return s.id !== id; });
    renderList();
    if (_active === id) onNewChat();
  } catch(e) {
    showToast('대화를 삭제할 수 없습니다.');
  }
}
```

#### REQ-DEL-02 — Undo 토스트 (권고)

**현재**: confirm 모달 후 즉시 삭제.
**변경 권고**: 삭제 시 "대화가 삭제되었습니다. [실행 취소]" 토스트 5초 표시.
- soft delete이므로 실행 취소 가능 (unarchive API 호출).

```javascript
function deleteWithUndo(id) {
  var session = _find(id);
  _sessions = _sessions.filter(function(s){ return s.id !== id; });
  renderList();

  showToastWithUndo('대화가 삭제되었습니다.', function undo() {
    _sessions.push(session);
    renderList();
    // 서버 unarchive는 별도 엔드포인트 필요 (현재 미정의)
  }, function commit() {
    API.deleteSession(id).catch(function(){});
  });
}
```

**근거**: 실수 삭제 방지. soft delete 특성상 undo 구현이 자연스러움.
**주의**: 현재 REST API에 unarchive 엔드포인트가 없으므로, 필요 시 백엔드 설계에 추가 요청.

#### REQ-DEL-03 — 삭제 확인 문구 개선 (권고)

**현재**: "'{title}'을(를) 삭제하시겠습니까?"
**변경 권고**: "대화 내용은 시스템에 보관되며, 목록에서만 제거됩니다." 안내 추가.
- 금융 감사 요건상 turn_texts는 삭제되지 않음을 사용자에게 투명하게 안내.

---

### 2.7 에러/누락 표시

#### REQ-ERR-01 — 에러 턴 시각적 구분 (필수)

**현재**: 에러 시 `status-banner.error`로 상단 배너만 표시.
**변경**: 복원된 에러 턴(`status='failure'`)에 시각적 구분 스타일 적용.

```css
.msg-bubble.error-bubble{
  background:var(--bg-accent-subtle);
  border-left:3px solid #ef4444;
  padding:12px 16px;
  border-radius:var(--r-sm);
  color:var(--txt2);
  font-size:13px;
}
```

#### REQ-ERR-02 — 이력 누락 턴 표시 (필수)

설계문서 §8.4의 연속성 검증 로직 구현:

```javascript
function validateAndEnrich(turns) {
  var enriched = [];
  for (var i = 0; i < turns.length; i++) {
    enriched.push(turns[i]);
    if (turns[i].role === 'user') {
      var next = turns[i + 1];
      if (!next || next.role !== 'assistant') {
        enriched.push({
          role: 'system',
          content: '응답이 기록되지 않았습니다.',
          turnType: 'gap'
        });
      }
    }
  }
  return enriched;
}
```

#### REQ-ERR-03 — 세션 로드 실패 처리 (필수)

| 시나리오 | HTTP 상태 | UI 처리 |
|----------|----------|---------|
| 세션 존재, 턴 있음 | 200 | 정상 렌더링 |
| 세션 존재, 턴 없음 | 200 (turns: []) | "이전 대화를 불러올 수 없습니다. 새 대화를 시작해주세요" 안내 |
| 세션 미존재 | 404 | 목록에서 제거 + "대화를 찾을 수 없습니다" 토스트 |
| 서버 오류 | 500 | "일시적으로 불러올 수 없습니다. 잠시 후 다시 시도해주세요" 배너 + 재시도 버튼 |
| 네트워크 단절 | fetch 실패 | localStorage 캐시 폴백 시도 |

#### REQ-ERR-04 — timeout/cancelled 턴 표시 (필수)

```javascript
// 복원 시 status별 처리
if (turn.status === 'timeout') {
  // "처리 시간이 초과되었습니다" 표시
} else if (turn.status === 'cancelled') {
  // "요청이 취소되었습니다" 표시
}
```

#### REQ-ERR-05 — 명확화 턴 복원 (필수)

`turn_type='clarification'`인 턴 복원 시, 명확화 선택지 UI를 재현해야 함.
- metadata에 `clarification: { question, options }` 저장되어 있음.
- 복원 시에는 **이미 선택된 상태**로 렌더링 (다음 user 턴의 content가 선택값).

```javascript
// 명확화 턴 복원 렌더링
if (turn.turnType === 'clarification' && turn.role === 'assistant') {
  // 선택지 버튼을 disabled 상태로 렌더링
  // 사용자가 선택한 항목에 '선택됨' 표시
}
```

---

### 2.8 기타 UX 개선 (권고)

#### REQ-UX-01 — 세션 목록 검색 (권고)

사이드바 상단에 검색 입력 필드 추가. 클라이언트 측 제목 필터링.

```html
<input class="sb-search" placeholder="대화 검색…" />
```

**근거**: 세션이 수십 개 쌓이면 원하는 대화를 찾기 어려움. 특히 은행 직원이 "어제 뽑았던 그 데이터" 같은 맥락에서 유용.

#### REQ-UX-02 — 세션 제목 수동 편집 (권고)

**현재**: 자동 생성만. 수정 불가.
**변경 권고**: 사이드바에서 세션 제목 더블클릭 → 인라인 편집.

> 백엔드 PATCH 엔드포인트 필요 (현재 미정의). 세션 목록은 `session_index.title` UPDATE로 구현 가능.

#### REQ-UX-03 — 세션 목록 로딩 스켈레톤 (권고)

API 응답 대기 중 사이드바에 스켈레톤 UI 표시.

```html
<div class="chat-item skeleton">
  <div style="width:70%;height:12px;background:var(--bg3);border-radius:4px"></div>
</div>
```

#### REQ-UX-04 — 재시도 버튼 (권고)

에러 턴(status='failure') 복원 시, "다시 시도" 버튼 표시.
- 클릭 시 해당 user 턴의 질문을 재전송.

#### REQ-UX-05 — 키보드 단축키 (권고)

| 단축키 | 동작 |
|--------|------|
| `Ctrl+Shift+N` | 새 대화 |
| `Ctrl+Shift+↑/↓` | 세션 전환 |
| `Ctrl+Shift+Backspace` | 현재 세션 삭제 |

#### REQ-UX-06 — 오프라인 대응 (권고)

WebSocket 끊김 + REST API 실패 시:
- 입력 영역에 "오프라인 상태입니다" 안내
- 세션 목록은 localStorage 캐시로 표시
- 메시지 전송 시 큐에 보관, 연결 복구 후 자동 재전송

---

## 3. 상세 구현 설계

### 3.1 모듈별 변경 명세

#### 3.1.1 API 모듈 (신규)

| 함수 | HTTP | 경로 | 비고 |
|------|------|------|------|
| `getSessions(userId, limit, offset)` | GET | `/api/sessions` | 세션 목록 |
| `getSession(sessionId)` | GET | `/api/sessions/{id}` | Tier 1 턴 목록 |
| `deleteSession(sessionId)` | DELETE | `/api/sessions/{id}` | soft delete |
| `getTurnMeta(turnId)` | GET | `/api/turns/{id}/metadata` | Tier 2 metadata |
| `toggleLike(turnId, isLiked)` | PATCH | `/api/turns/{id}/like` | 좋아요 토글 |
| `markDownload(turnId)` | PATCH | `/api/turns/{id}/download` | 다운로드 기록 |

에러 처리 공통:
```javascript
// 401 → 인증 만료 안내 (SSO 연동 후)
// 404 → 리소스 미존재 처리 (세션 목록에서 제거 등)
// 500 → 재시도 안내
// fetch 실패 → 오프라인 폴백
```

#### 3.1.2 SB 모듈 변경

| 함수 | 변경 유형 | 내용 |
|------|----------|------|
| `init()` | **수정** | localStorage → `API.getSessions()`, 폴백으로 localStorage 캐시 |
| `newSession()` | **수정** | 세션 생성은 기존대로 클라이언트에서 수행, 첫 메시지 전송 시 서버에 자동 등록 (runner.py의 upsert_session_index) |
| `loadSession(id)` | **수정** | localStorage msgs → `API.getSession(id)` + MS 로드 + CN 재연결 |
| `deleteSession(id)` | **수정** | localStorage 삭제 → `API.deleteSession(id)` |
| `_save()` | **수정** | 서버 동기화 + localStorage 캐시 갱신 |
| `renderList()` | **수정** | 날짜 그룹 구분 추가 (REQ-SB-04) |

#### 3.1.3 MS 모듈 변경

| 함수 | 변경 유형 | 내용 |
|------|----------|------|
| `create()` | **수정** | `turnId`, `isLiked`, `isDownloaded`, `hasMetadata`, `createdAt`, `restored` 필드 추가 |
| `update()` | 유지 | 기존 동작 그대로 (필드 병합) |

#### 3.1.4 ED 모듈 변경

| 함수 | 변경 유형 | 내용 |
|------|----------|------|
| `handleStream()` | **수정** | `stream.end`에 `turn_id`가 포함되면 `MS.update(id, {turnId: data.turn_id})` |
| `handleLegacy()` | **수정** | `response`에 `turn_id` 포함 시 동일 처리 |

#### 3.1.5 CN 모듈 변경

| 함수 | 변경 유형 | 내용 |
|------|----------|------|
| `reconnectWith(sessionId)` | **신규** | 특정 session_id로 WebSocket 재연결 |
| `connect()` | 유지 | 기존 동작 (URL: `/ws/{sid}`) |

#### 3.1.6 RD 모듈 변경 (렌더링)

| 변경 | 내용 |
|------|------|
| assistant 메시지 렌더링 | 좋아요/싫어요 버튼 추가 (msg-actions 영역) |
| 에러 턴 렌더링 | `.error-bubble` 스타일 적용 |
| 누락 턴 렌더링 | system 메시지로 "응답이 기록되지 않았습니다" 표시 |
| 명확화 턴 렌더링 | 선택지 disabled 상태로 복원 |
| 타임스탬프 | 복원 메시지에 `.msg-time` 표시 |
| metadata 로딩 | insight/viz 영역 로딩 인디케이터 |

### 3.2 WebSocket 프로토콜 확장

#### 3.2.1 서버 → 클라이언트 메시지 변경

```javascript
// stream.end에 turn_id 추가
{
  type: 'stream',
  action: 'end',
  turn_id: 'uuid-5678',          // ← 신규: 서버 turn_id
  user_turn_id: 'uuid-1234',     // ← 신규: user 턴 turn_id (좋아요 등에 필요)
  insight: { ... }
}

// download_ready에 turn_id 추가
{
  type: 'download_ready',
  session_id: 'sess-a1b2',
  turn_id: 'uuid-5678',          // ← 신규
  row_count: 100,
  formats: ['csv']
}
```

#### 3.2.2 클라이언트 → 서버 메시지 (변경 없음)

기존 plain text 전송 유지. session_id는 WebSocket URL 경로로 전달.

### 3.3 데이터 흐름 (TO-BE)

```
[새 대화 시작]
  User clicks "새 대화"
    → SB.newSession() — 클라이언트 세션 생성
    → CN.reconnect() — 새 WebSocket SID 발급
    → 첫 메시지 전송 시 서버가 session_index에 upsert

[메시지 전송]
  User types → App.sendMessage()
    → MS.create('user', text) — 로컬 렌더링 (optimistic)
    → CN.send(text) — WebSocket 전송
    → 서버: runner.py → save_turn(user) → save_turn(assistant)
    → 서버: WebSocket → stream.end { turn_id, user_turn_id }
    → ED.handleStream() → MS.update(id, { turnId })
    → SB.saveCurrentMessage() — localStorage 캐시 갱신

[과거 세션 로드]
  User clicks session in sidebar
    → SB.loadSession(id)
    → API.getSession(id) — Tier 1 로드
    → turns.forEach → MS.create(role, content, {turnId, restored: true})
    → CN.reconnectWith(id) — 해당 세션으로 WebSocket 재연결
    → 사용자 상호작용 시 → API.getTurnMeta(turnId) — Tier 2 로드

[좋아요 클릭]
  User clicks 👍
    → MS.update(msgId, {isLiked: true}) — optimistic 갱신
    → updateLikeButtons(msgId, true)
    → API.toggleLike(turnId, true) — 서버 동기화
    → 실패 시 롤백

[세션 삭제]
  User clicks 🗑️ → confirm 모달
    → API.deleteSession(id) — 서버 soft delete
    → SB 목록에서 제거
    → 활성 세션이면 새 대화로 전환
```

### 3.4 CSS 추가 사항

```css
/* ══ 좋아요/싫어요 ══ */
.act-btn.liked{color:#22c55e}
.act-btn.disliked{color:#ef4444}
.act-btn.like-btn:hover{color:#22c55e}
.act-btn.dislike-btn:hover{color:#ef4444}

/* ══ 에러 턴 ══ */
.msg-bubble.error-bubble{
  background:var(--bg-accent-subtle);border-left:3px solid #ef4444;
  padding:12px 16px;border-radius:var(--r-sm);color:var(--txt2);font-size:13px}

/* ══ 누락 턴 ══ */
.msg-bubble.gap-bubble{
  background:var(--bg2);border:1px dashed var(--border2);
  padding:10px 14px;border-radius:var(--r-sm);color:var(--txt3);font-size:12.5px;
  text-align:center;font-style:italic}

/* ══ 타임스탬프 ══ */
.msg-time{font-size:11px;color:var(--txt3);margin-top:4px;display:block}

/* ══ 사이드바 날짜 그룹 ══ */
.sb-date-group{padding:8px 13px 3px;font-size:10.5px;font-weight:600;
  color:var(--txt3);letter-spacing:.04em}

/* ══ 사이드바 검색 ══ */
.sb-search{width:calc(100% - 18px);margin:4px 9px;padding:6px 10px;
  background:var(--bg);border:1px solid var(--border);border-radius:var(--r-sm);
  font-size:12.5px;color:var(--txt);outline:none}
.sb-search:focus{border-color:var(--bg-accent)}

/* ══ 스켈레톤 로딩 ══ */
.chat-item.skeleton{pointer-events:none}
.skeleton-bar{height:12px;background:var(--bg3);border-radius:4px;
  animation:shimmer 1.5s infinite}
@keyframes shimmer{0%{opacity:.4}50%{opacity:.8}100%{opacity:.4}}

/* ══ 다운로드 완료 상태 ══ */
.download-bar.downloaded{opacity:.7}
.download-bar.downloaded span{color:var(--txt3)}
```

### 3.5 구현 순서

```
Phase 1 — 인프라 + 핵심 기능 (MVP)
  ├── 1-1. API 모듈 추가 (REQ-INFRA-01)
  ├── 1-2. MS 모듈 turnId 필드 확장 (REQ-INFRA-02)
  ├── 1-3. SB.init() → REST API 전환 (REQ-SB-01)
  ├── 1-4. SB.loadSession() → REST API + Tier 1 (REQ-CR-01)
  ├── 1-5. CN.reconnectWith() 추가 (REQ-CR-05)
  ├── 1-6. 좋아요/싫어요 UI + 서버 동기화 (REQ-LIKE-01, 02)
  ├── 1-7. 다운로드 서버 기록 (REQ-DL-01)
  ├── 1-8. 세션 삭제 REST API 전환 (REQ-DEL-01)
  ├── 1-9. 에러/누락/timeout 턴 표시 (REQ-ERR-01~04)
  └── 1-10. 명확화 턴 복원 (REQ-ERR-05)

Phase 2 — UX 향상
  ├── 2-1. Tier 2 metadata 지연 로드 (REQ-CR-02, 03)
  ├── 2-2. 복원 메시지 타임스탬프 (REQ-CR-04)
  ├── 2-3. 사이드바 날짜 그룹 (REQ-SB-04)
  ├── 2-4. 다운로드 완료 상태 표시 (REQ-DL-02)
  ├── 2-5. Undo 삭제 토스트 (REQ-DEL-02)
  └── 2-6. 삭제 확인 문구 개선 (REQ-DEL-03)

Phase 3 — 파워 유저 기능
  ├── 3-1. 세션 목록 검색 (REQ-UX-01)
  ├── 3-2. 세션 제목 수동 편집 (REQ-UX-02)
  ├── 3-3. 스켈레톤 로딩 (REQ-UX-03)
  ├── 3-4. 에러 턴 재시도 버튼 (REQ-UX-04)
  ├── 3-5. 무한 스크롤/더 보기 (REQ-SB-05)
  ├── 3-6. 싫어요 피드백 입력 (REQ-LIKE-03)
  ├── 3-7. 키보드 단축키 (REQ-UX-05)
  └── 3-8. 오프라인 대응 (REQ-UX-06)
```

---

## 4. 비판적 검토

### 4.1 통합 시 이슈

#### ISSUE-01 — localStorage 캐시와 서버 데이터 불일치 (심각도: 낮음)

**문제**: REQ-SB-01에서 서버 응답을 localStorage에 캐시하되,
다른 기기에서 세션을 삭제/수정하면 캐시가 stale해짐.

**판단**: 현재 SSO 미구현으로 단일 기기 사용이 전제. 실질적 문제 낮음.

**권고**: 향후 SSO 도입 시 세션 목록에 `ETag`/`Last-Modified` 헤더를 추가하여 캐시 검증.

#### ISSUE-02 — Tier 2 로딩 시 SVG 보안 (심각도: 중간)

**문제**: `metadata.visualization.svg_code`를 innerHTML로 렌더링하면,
악의적 SVG에 `<script>` 또는 `onload` 이벤트가 포함될 수 있음.

**권고**:
- SVG 삽입 전 `<script>`, `on*` 이벤트 속성 제거 (sanitize).
- 현재도 서버에서 생성한 SVG만 저장하므로 위험은 낮으나, 방어적 코딩 필요.

#### ~~기각: 과거 세션 "읽기 모드 vs 대화 모드" 구분~~

초기 검토에서 과거 세션 로드 시 WebSocket 재연결이 interrupt 상태와 충돌할 수 있다고 판단했으나, 재검토 결과 **과설계**로 결론:

- 과거 세션을 **보는 것**(turn_texts SELECT)은 체크포인터와 무관. 체크포인터가 로드되는 건 `ainvoke()` 호출 시점, 즉 사용자가 **실제로 질문을 전송할 때**뿐.
- interrupt 세션 복원 시 사용자는 명확화 질문을 보면서 입력하므로 맥락이 충분.
- 대처: 복원 세션의 마지막 턴이 `turn_type='clarification'`이면 해당 UI를 활성 상태로 렌더링. 별도 모드 구분 불필요.

#### ~~기각: turn_id 미수신 시 좋아요 비활성화~~

초기 검토에서 optimistic 렌더링과 turn_id 수신 사이 타이밍 충돌을 우려했으나, 재검토 결과 **비이슈**:

- 좋아요 버튼이 표시되는 시점 = `stream.end` 수신 후 = turn_id가 이미 도착한 시점.
- 스트리밍 중에는 msg-actions가 표시되지 않으므로 클릭 자체가 불가.
- 복원 메시지는 `GET /api/sessions/{id}` 응답에 turn_id가 포함되어 처음부터 사용 가능.

### 4.2 유지보수성

#### MAINT-01 — embedded.html 단일 파일 비대화

**현재**: 1,863줄 단일 HTML에 CSS + JS 전부 포함.
이번 변경으로 API 모듈, 좋아요/싫어요 로직, 2-tier 로딩, 에러 처리 등이 추가되면
**2,500줄 이상**으로 증가할 것으로 예상.

**권고**:
- 당장 파일 분리를 강제하지는 않되, 모듈 경계를 IIFE로 명확히 유지.
- API 모듈은 별도 `<script>` 블록 또는 `static/api.js`로 분리 가능 (폐쇄망에서도 문제 없음).

#### MAINT-02 — 상태 동기화 복잡도

localStorage 캐시 + 인메모리 MS + 서버 DB 3중 상태 관리는 버그 가능성이 높음.

**권고**:
- **서버가 진실의 원천**: 세션 목록은 항상 서버에서 조회, localStorage는 오프라인 폴백 전용.
- **MS는 현재 세션 전용**: 세션 전환 시 MS 완전 초기화 후 서버 데이터로 재구성.
- 복잡한 캐시 무효화 로직 도입하지 않음.

### 4.3 UI/UX 측면

#### UX-01 — 좋아요 피드백이 사용자에게 보이지 않음

**문제**: 좋아요/싫어요는 운영팀 분석용이지만, 사용자 입장에서는 "내 피드백이 반영되는가?" 의문.

**권고**:
- 좋아요 클릭 시 짧은 토스트: "피드백이 기록되었습니다. 서비스 개선에 활용됩니다."
- 과도한 피드백 요청은 자제 (매 턴마다 팝업 금지).

#### UX-02 — 대용량 SVG 차트 모바일 대응

**문제**: Tier 2로 로드된 SVG 차트가 모바일에서 과도한 렌더링 비용 발생 가능.

**권고**: 모바일(`@media max-width:640px`)에서는 차트 축소 렌더링 또는 "차트 보기" 버튼으로 지연 표시.

#### UX-03 — 세션 목록 빈 상태 (Empty State)

**현재**: 세션이 없으면 `chatList`가 빈 div.
**권고**: "아직 대화가 없습니다. 새 대화를 시작해보세요!" 안내 + 새 대화 버튼.

#### UX-04 — 좋아요/싫어요 위치와 가시성

**문제**: 현재 msg-actions는 hover 시에만 표시(`opacity:0` → `opacity:1`).
좋아요/싫어요는 "의도적 행위"이므로 hover 시에만 보이면 발견이 어려울 수 있음.

**권고**:
- 좋아요/싫어요 버튼은 assistant 메시지 하단에 항상 표시 (약한 색상).
- 복사/재생성은 hover 시에만 표시 유지.
- 또는 최소한 `is_liked === null`(미평가) 상태에서만 항상 보이게 하고,
  평가 완료 후에는 hover 시에만 표시.

### 4.4 백엔드 설계와의 GAP

#### GAP-01 — unarchive(삭제 취소) 엔드포인트 부재

REQ-DEL-02(Undo 토스트)를 구현하려면 `PATCH /api/sessions/{id}/unarchive` 또는
`PATCH /api/sessions/{id}` (body: `{is_archived: false}`)가 필요하나, 현재 REST API 설계에 없음.

**권고**: 백엔드 설계에 세션 복원 엔드포인트 추가를 요청하거나, Undo를 Phase 3으로 미룸.

#### GAP-02 — 세션 제목 수정 엔드포인트 부재

REQ-UX-02(제목 편집)를 구현하려면 `PATCH /api/sessions/{id}` (body: `{title: "..."}`)가 필요.

**권고**: 백엔드 설계에 세션 제목 수정 엔드포인트 추가 검토.

#### ~~기각: WebSocket turn_id 전달 명세 누락~~

초기 검토에서 백엔드 설계에 WebSocket으로 turn_id를 전달하는 명세가 없다고 지적했으나,
재검토 결과 **별도 설계 보완이 필요한 수준의 GAP이 아님**:

- REST API(§8)에 turn_id 기반 엔드포인트가 명확히 정의되어 있음.
- `stream.end`에 turn_id를 포함시키는 건 구현 시 자연스럽게 처리되는 세부사항.
- 본 문서 §3.2에 WebSocket 프로토콜 확장 명세로 기술되어 있으므로 충분.

#### ~~기각: 세션 interrupt 상태 조회 API 부재~~

"읽기/대화 모드 구분" 이슈에 종속된 항목이었으며, 해당 이슈가 과설계로 기각됨에 따라 불필요.
또한 interrupt 상태 확인은 `aget_state()` 호출(= 체크포인터 로드)을 수반하여,
turn_texts 경량 조회로 과거 대화를 복원한다는 설계 취지에 모순.

### 4.5 요약 — 우선 조치 사항

| 순위 | 항목 | 구분 |
|------|------|------|
| 1 | ISSUE-02: SVG sanitize 방어 코딩 | UI 구현 |
| 2 | UX-04: 좋아요 버튼 가시성 정책 결정 | UI 설계 결정 |
| 3 | MAINT-02: 서버=진실의 원천 원칙 준수 | 구현 원칙 |
| 4 | GAP-01: unarchive 엔드포인트 | Phase 3으로 미룸 가능 |
| 5 | GAP-02: 제목 수정 엔드포인트 | Phase 3으로 미룸 가능 |
