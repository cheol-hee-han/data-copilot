# UI/UX 종합 개선 계획

> **작성일**: 2026-04-06  
> **대상 파일**: `static/embedded.html` (2,415 lines, CSS+JS 단일 파일)  
> **검토 관점**: UX/UI 전문가 에이전트 리뷰 + 서버 코드 분석  

---

## 목차

1. [사용자 요청 사항 (17건)](#1-사용자-요청-사항)
2. [UX/UI 에이전트 추가 발견 사항](#2-uxui-에이전트-추가-발견-사항)
3. [신규 기능 제안 — 사용자 편의기능](#3-신규-기능-제안--사용자-편의기능)
4. [신규 기능 제안 — 관리자 기능](#4-신규-기능-제안--관리자-기능)
5. [구조적 개선 (코드 품질)](#5-구조적-개선-코드-품질)
6. [구현 우선순위 및 일정 제안](#6-구현-우선순위-및-일정-제안)

---

## 1. 사용자 요청 사항

### 1.1 액션 아이콘 한 줄 정렬

| 항목 | 내용 |
|------|------|
| **현상** | 복사, 재생성, 인사이트 버튼이 `.msg-actions`에, 좋아요/싫어요가 `.msg-like-actions`에 분리되어 **2줄로 렌더링** |
| **원인** | `ensureDOM()` (line 882-890)에서 두 개의 `<div>`로 분리 생성 |
| **해결** | 하나의 `<div class="msg-actions">`로 통합. 5개 버튼 모두 `display:flex; gap:1px`로 한 줄 배치 |
| **난이도** | 낮음 |

**변경 대상**:
- HTML 생성부 (line 882-890): `.msg-like-actions` 제거, 좋아요/싫어요 버튼을 `.msg-actions`에 통합
- CSS (line 511-516): `.msg-like-actions` 스타일 제거 또는 `.msg-actions`에 병합
- 이벤트 바인딩 (line 896-912): 통합된 컨테이너에서 이벤트 위임으로 변경

```
변경 전:
[복사] [재생성] [인사이트]
[좋아요] [싫어요]

변경 후:
[복사] [재생성] [인사이트] [좋아요] [싫어요] [다운로드]
```

---

### 1.2 CSV 다운로드 배너 삭제

| 항목 | 내용 |
|------|------|
| **현상** | 데이터 추출 답변 하단에 주황색 `download-bar` 배너 표시 |
| **원인** | `renderDownload()` (line 1374-1386)에서 `download_ready` 이벤트 수신 시 생성 |
| **해결** | 마크다운 표의 CSV 복사 기능이 이미 존재하므로 다운로드 배너 제거 |
| **난이도** | 낮음 |

**`download_ready` 이벤트 역할**: 쿼리 결과가 있을 때(`row_count > 0`) 서버가 결과를 메모리에 캐시한 뒤 UI에 보내는 "다운로드 준비 완료" 신호. UI에서 배너를 표시하고, 클릭 시 `POST /api/download` → 서버가 캐시에서 CSV 생성 후 반환하는 흐름.

**변경 대상**:

- `renderDownload()` 함수 및 `.download-bar` 배너 렌더링 제거
- CSS `.download-bar` 관련 스타일 (line 453-461, 537-539) 제거
- `render()` 내 `downloadReady` 호출부 (line 1079) 제거
- **`ED.handleDownloadReady()` (line 1621-1628)은 유지하되, 배너 대신 `.msg-actions`의 다운로드 아이콘 활성화 트리거로 변경**
- `download_ready` 이벤트 자체는 서버에서 계속 전송 — 1.1의 다운로드 아이콘 버튼 활성화에 활용

> **참고**: `download_ready`는 사전 생성 파일이 아닌 준비 신호. 실제 CSV는 클릭 시 `POST /api/download`로 온디맨드 생성.

---

### 1.3 Trace/Report 파일 다운로드 버튼

| 항목 | 내용 |
|------|------|
| **현상** | 서버에서 생성하는 trace/reasoning/report 파일을 UI에서 다운로드할 수 없음 |
| **요구** | 싫어요 버튼 옆에 다운로드 버튼 추가 |
| **난이도** | 중간 |

**구현 설계**:

1. **서버 변경**: `stream.end` 응답에 trace 파일 경로 포함
   ```json
   {
     "type": "stream", "action": "end",
     "trace_files": [
       {"name": "trace_report_20260406_..._turn123.md", "path": "/logs/traces/..."},
       {"name": "trace_reasoning_20260406_..._turn123.md", "path": "/logs/traces/..."}
     ]
   }
   ```

2. **서버 API 추가**: `GET /api/traces/{filename}` — 파일 다운로드 엔드포인트
   - 경로 순회(path traversal) 방어 필수
   - `settings.eval_tracker_output_dir` 내 파일만 허용

3. **UI 변경**:
   - `.msg-actions`에 다운로드 아이콘 버튼 추가 (기본 `display:none`)
   - `stream.end` 수신 시 `trace_files` 존재하면 버튼 활성화
   - 클릭 시 드롭다운으로 파일 목록 표시 → 클릭하면 다운로드
   - `MS` 모델에 `traceFiles` 필드 추가

4. **아이콘**: 다운로드 아이콘 SVG (기존 프로젝트 스타일 일관성)
   ```html
   <button class="act-btn" data-act="download-trace" title="분석 리포트 다운로드" style="display:none">
     <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
       <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
       <polyline points="7 10 12 15 17 10"/>
       <line x1="12" y1="15" x2="12" y2="3"/>
     </svg>
   </button>
   ```

---

### 1.4 명확화 질문 UI 재설계

| 항목 | 내용 |
|------|------|
| **현상** | 명확화 질문이 서버에서 `interrupt()`로 전송되지만 UI에서 처리 로직 부재 |
| **원인** | `ED.handle()` (line 1501-1514)에 `clarification` 타입 핸들러 없음. 복원 시만 `_renderClarificationRestored()` 존재 |
| **요구** | 좋아요 피드백 모달과 유사한 디자인으로 대화창 내 인라인 구현 |
| **난이도** | 높음 (서버-UI 연동 + 새 컴포넌트) |

**구현 설계**:

1. **서버 메시지 형식** (이미 구현됨 — `runner.py`에서 `stream.end`에 포함):
   ```json
   {
     "type": "stream", "action": "end",
     "status": "awaiting_clarification",
     "clarification_request": {
       "question": "어떤 테이블을 사용할까요?",
       "question_type": "single_select",
       "options": ["Option A", "Option B"],
       "ambiguity_type": "TABLE",
       "source_node": "context_retriever"
     }
   }
   ```

2. **UI 컴포넌트 설계**:
   ```
   ┌─────────────────────────────────────────┐
   │ 🤔 어떤 테이블을 사용할까요?             │
   │                                         │
   │  ○ Option A                             │
   │  ○ Option B                             │
   │  ○ 기타 (직접 입력)                      │
   │  ┌──────────────────────────────┐       │
   │  │ 직접 입력해주세요…            │       │
   │  └──────────────────────────────┘       │
   │                                         │
   │                          [제출]          │
   └─────────────────────────────────────────┘
   ```

3. **동작 규칙**:
   - `question_type === "single_select"`: 라디오 선택 + "기타" 옵션(텍스트 입력)
   - `question_type === "free_text"`: 텍스트 입력만
   - `question_type === "confirm"`: 예/아니오 버튼
   - "건너뛰기" 버튼 불필요 — 사용자가 답변을 원하지 않으면 무시하거나, 아래 대화 입력창에서 직접 다른 질문 입력 가능
   - 제출 시 WebSocket으로 선택값 전송 → `Command(resume=)` 처리 (일반 메시지와 동일 경로)

4. **CSS 클래스**: `.clarification-card` — 피드백 팝업(`.feedback-popup`)과 유사 디자인
   - 배경: `var(--bg2)`, 테두리: `var(--border)`, 라운드: `var(--r-md)`
   - 옵션 버튼: `.feedback-option`과 동일 스타일 재사용
   - 인라인(대화 버블 내) 배치 — 모달이 아님

5. **`ED.handleStream()`의 `end` 분기에 추가**:
   ```javascript
   if (data.status === 'awaiting_clarification' && data.clarification_request) {
     _renderClarification(msg, data.clarification_request);
     return;
   }
   ```

---

### 1.5 데이터 추출 결과 순서 변경

| 항목 | 내용 |
|------|------|
| **현상** | "조회 기준 안내" → 데이터 표 → "조회 과정 요약" 순으로 표시 |
| **요구** | (1) 추출 결과(표) → (2) 조회 기준 안내 → (3) 조회 과정 요약 |
| **난이도** | 낮음 (서버 프롬프트 변경) |

> **분석 결과**: 서버 응답은 `formatted_response` 단일 마크다운 문자열로 전달됨 (JSON key/value 분리 아님). 따라서 UI 후처리로 섹션 재배치하려면 마크다운 헤딩 파싱이 필요하나, 헤딩 구조가 일관되지 않을 수 있어 신뢰성이 낮음.

**해결 방안**: **방안 A (서버 프롬프트 변경)** 단독 적용

- `resources/prompts/present/formatter_system.txt`에서 출력 순서를 명시적으로 지시:
  1. 추출 결과(표) 먼저
  2. 조회 기준 안내
  3. 조회 과정 요약
- 프롬프트에서 순서를 구체적으로 지시하면 대부분의 LLM이 준수함
- 방안 B(UI 마크다운 파싱 후처리)는 스트리밍 중 순서 불일치, 헤딩 비일관성 문제로 **보류**

---

### 1.6 & 1.7 접기 가능한 회색 [참고] 블럭

| 항목 | 내용 |
|------|------|
| **요구** | "조회 기준 안내"와 "조회 과정 요약"을 접기/열기 가능한 회색 참고 블럭으로 구현 (default=open) |
| **난이도** | 중간 |

**구현 설계**:

1. **마크다운 후처리**: `mdRender()` 결과에서 해당 섹션을 네이티브 `<details>` 태그 + 커스텀 스타일로 래핑
   - 네이티브 `<details><summary>` 사용 → 접근성(키보드, 스크린리더) 자동 지원
   - 커스텀 CSS로 디자인 통일
   ```html
   <details class="ref-block" open>
     <summary class="ref-header">
       <span class="ref-tag">참고</span>
       <span class="ref-title">조회 기준 안내</span>
       <span class="ref-chevron">▾</span>
     </summary>
     <div class="ref-body">
       <!-- 원래 내용 -->
     </div>
   </details>
   ```

2. **CSS 추가**:
   ```css
   .ref-block { background: var(--bg2); border: 1px solid var(--border);
     border-radius: var(--r-sm); margin: 12px 0; overflow: hidden; }
   .ref-header { display: flex; align-items: center; gap: 8px;
     padding: 10px 14px; cursor: pointer; user-select: none; list-style: none; }
   .ref-header::-webkit-details-marker { display: none; }
   .ref-tag { font-size: 11px; font-weight: 600; color: var(--txt3);
     background: var(--bg3); padding: 2px 8px; border-radius: var(--r-xs); }
   .ref-title { font-size: 13px; font-weight: 600; color: var(--txt2); flex: 1; }
   .ref-chevron { transition: transform var(--t); }
   .ref-block:not([open]) .ref-chevron { transform: rotate(-90deg); }
   .ref-body { padding: 0 14px 12px; font-size: 13.5px; line-height: 1.65; }
   ```

3. **적용 위치**: `render()` → `SE.finalize()` 후 `attachCodeCopy()` 호출 전에 참고 블럭 변환

---

### 1.8 대화이력 복원 시 데이터 전달 확인

| 항목 | 분석 결과 |
|------|----------|
| **분석 과정 조회 (insight 버튼)** | **조건부 전달됨**. `loadSession()` (line 1823)에서 `GET /api/sessions/{id}`로 turn 목록 조회 → `has_metadata: true`인 턴에 한해 insight 버튼 표시 (`data-act="insight"`, line 885). 실제 insight 데이터는 **lazy load** — 버튼 클릭 시 `GET /api/turns/{turn_id}/metadata` 호출 (line 951) |
| **진행상황 [interpret]>[reason]>[present]** | **전달되지 않음**. 대화이력 조회 시 `restored: true`로 생성되며, progress 데이터는 실시간 WebSocket에서만 전달. 복원 시 progress 배열은 빈 상태 |

**개선 방안**:
- progress 데이터는 실시간 전용이므로 복원 불필요 (정상 동작)
- insight 버튼은 `has_metadata: true`일 때 자동 표시됨 — 현재 정상
- 단, insight 버튼이 `style="display:none"` (line 885)으로 기본 숨김 → `has_metadata` 체크 시 표시 로직 확인 필요

**확인 필요**: `render()` 함수 line 1078에서 `msg.insight`가 있을 때만 insight 버튼을 보이게 하는데, restored 메시지는 lazy-load 전이므로 `msg.hasMetadata`를 기준으로 버튼을 먼저 보여야 함.

---

### 1.9 질문 해석(query_interpretation) 데이터 전달 확인 + SQL 보기 하이라이팅

| 항목 | 분석 결과 |
|------|----------|
| **질문 해석 데이터** | 서버의 `callback_handler.py`에서 `insight.query_interpretation`에 `period`, `target`, `metric` 필드를 포함하여 전달. 단, interpret 노드에서 해당 데이터를 추출하여 insight에 넣는 로직이 실제로 구현되어 있는지 확인 필요 |
| **SQL 보기 하이라이팅** | **미적용**. line 1305-1306에서 `<code class="language-sql">`로 마크업하지만 `hljs.highlightElement()` 미호출 |

**SQL 하이라이팅 해결**:
- `renderInsight()` (line 1372) `slot.innerHTML = h;` 직후에 하이라이팅 적용:
  ```javascript
  slot.querySelectorAll('pre code.language-sql').forEach(function(block) {
    if (typeof hljs !== 'undefined') hljs.highlightElement(block);
  });
  ```
- SQL 포매팅(prettier): SQLGlot은 서버 측 Python 라이브러리이므로 UI에서는 hljs 하이라이팅만 적용. 
  서버에서 `insight.sql_code`를 저장할 때 SQLGlot으로 포매팅하여 저장하는 것이 바람직.

---

### 1.10 설정 모달 기능 확장

현재 설정 (line 666-694):
- 테마 (라이트/딤/다크)
- 글꼴 크기 (13/14/15/16px)

**추가 설정 옵션** (확정):

| 카테고리 | 설정 항목 | 설명 | 컨트롤 유형 | 저장소 |
|----------|----------|------|------------|--------|
| **표시** | 대화 폭 | 좁게(600px) / 보통(700px) / 넓게(900px) | 3단 세그먼트 | localStorage |
| **표시** | 코드 글꼴 크기 | 코드 블록 전용 폰트 크기 (12/13/14px) | 3단 세그먼트 | localStorage |
| **표시** | 마크다운 줄 간격 | 촘촘(1.5) / 보통(1.65) / 넉넉(1.8) | 3단 세그먼트 | localStorage |
| **동작** | 자동 스크롤 | 스트리밍 중 자동 스크롤 on/off | 토글 스위치 | localStorage |
| **동작** | 사운드 알림 | 응답 완료 시 알림음 on/off | 토글 스위치 | localStorage |
| **데이터** | CSV 인코딩 | UTF-8 / EUC-KR (은행 환경) | 2단 세그먼트 | localStorage |
| **분석** | 분석 과정 자동 펼침 | insight 패널 자동 열기 on/off | 토글 스위치 | localStorage |
| **분석** | SQL 표시 여부 | 항상 표시 / 접기 / 숨김 | 3단 세그먼트 | localStorage |

> **제외된 항목**: 기본 조회 건수(서버 설정 영역), Enter 키 동작, 숫자 포맷(프롬프트 연동 필요), 고대비 모드

**설정 모달 UI 레이아웃**:

```text
┌────────────────────────────────────────────┐
│  ⚙ 설정                              ✕    │
│────────────────────────────────────────────│
│  표시                                      │
│  ┌──────────────────────────────────────┐  │
│  │ 테마         [라이트|딤|다크]         │  │
│  │ 글꼴 크기    [13|14|15|16]px         │  │
│  │ 대화 폭      [좁게|보통|넓게]         │  │
│  │ 코드 글꼴    [12|13|14]px            │  │
│  │ 줄 간격      [촘촘|보통|넉넉]         │  │
│  └──────────────────────────────────────┘  │
│                                            │
│  동작                                      │
│  ┌──────────────────────────────────────┐  │
│  │ 자동 스크롤               [●━━━]     │  │
│  │ 사운드 알림               [━━━○]     │  │
│  └──────────────────────────────────────┘  │
│                                            │
│  데이터                                    │
│  ┌──────────────────────────────────────┐  │
│  │ CSV 인코딩   [UTF-8|EUC-KR]         │  │
│  └──────────────────────────────────────┘  │
│                                            │
│  분석                                      │
│  ┌──────────────────────────────────────┐  │
│  │ 분석 과정 자동 펼침       [●━━━]     │  │
│  │ SQL 표시     [표시|접기|숨김]         │  │
│  └──────────────────────────────────────┘  │
│                                            │
│  [키보드 단축키 안내]    [설정 초기화]      │
└────────────────────────────────────────────┘
```

- 카테고리별 섹션 구분 (표시 / 동작 / 데이터 / 분석)
- 세그먼트 컨트롤: 선택지가 2~4개인 항목 (토글보다 직관적)
- 토글 스위치: on/off 항목
- 설정 초기화 버튼 + 키보드 단축키 안내 섹션

---

### 1.11 새로고침(재생성) 기능 검증

| 항목 | 분석 결과 |
|------|----------|
| **현재 구현** | `App.regen()` (line 2155-2168): 마지막 assistant 메시지 삭제 → 마지막 user 메시지 텍스트를 `CN.send()`로 재전송 |
| **문제점** | `CN.send(lastUser.text)` — 원본 텍스트만 전송하고 `turn_id`/`turn_seq` 정보 없음. 서버는 **새 턴으로 생성** (runner.py line 288에서 새 UUID 할당) |
| **영향** | 동일 질문에 대한 재시도가 별개 턴으로 기록되어 이력 중복 |

**개선 방안**: 방안 A (메시지 프로토콜 확장) — **Phase 1.5의 JSON 전환을 활용**

- UI: `CN.send(lastUser.text, {action: "regen", original_turn_id: turnId})`
  ```json
  {"text": "원본 질문", "action": "regen", "original_turn_id": "uuid"}
  ```
- 서버: `is_regen=True`이면 기존 턴을 `status=regenerated`로 마킹하고 새 턴 생성
- Phase 1.5에서 `CN.send(t, opts)` 형태로 전환되므로, 이 단계에서는 호출부에 `opts` 추가만 필요

> **참고**: 방안 B(기존 턴 덮어쓰기)는 체크포인트 관리 복잡도가 높아 채택하지 않음

---

### 1.12 마크다운 셀 높이 축소

| 항목 | 내용 |
|------|------|
| **현재** | `.bot-bubble` line-height: 1.78, font-size: 14.5px (line 196) |
| **요구** | 데이터가 더 촘촘하게 보이도록 축소 |
| **난이도** | 낮음 |

**변경 제안**:
```css
/* 변경 전 */
.bot-bubble { line-height: 1.78; font-size: 14.5px; }
.bot-bubble p { margin-bottom: 12px; }
.bot-bubble ul, .bot-bubble ol { margin-bottom: 12px; }
.bot-bubble li { margin-bottom: 4px; }
.bot-bubble th, .bot-bubble td { padding: 10px 12px; }

/* 변경 후 */
.bot-bubble { line-height: 1.6; font-size: 14px; }
.bot-bubble p { margin-bottom: 8px; }
.bot-bubble ul, .bot-bubble ol { margin-bottom: 8px; }
.bot-bubble li { margin-bottom: 2px; }
.bot-bubble th, .bot-bubble td { padding: 7px 10px; }
```

---

### 1.13 사이드바 대화 이력 최신순 정렬

| 항목 | 분석 결과 |
|------|----------|
| **현재** | `SB.init()` (line 1780-1783): 서버에서 받은 순서 그대로 사용. 서버 API `GET /api/sessions`는 `last_active` 기준 정렬을 보장하지 않을 수 있음 |
| **문제** | 날짜 그룹("오늘", "어제") 내에서 시간순 정렬이 보장되지 않음 |
| **난이도** | 낮음 |

**해결**:
- `SB.init()` line 1783 이후에 정렬 추가:
  ```javascript
  _sessions.sort(function(a, b) { return b.ts - a.ts; });
  ```
- `renderList()` 내 `filtered` 배열도 동일 정렬 적용

---

### 1.14 스트리밍 출력 검증

| 항목 | 분석 결과 |
|------|----------|
| **텍스트 스트리밍** | **정상**. `ED.handleStream()` → `SE.appendChunk()` → `RD.render()` 순으로 청크 단위 렌더링 |
| **마크다운 렌더링** | **부분 이슈**. 스트리밍 중 `mdRender(msg.text)` 호출 시 불완전한 마크다운(미닫힌 태그)으로 일시적 깨짐 가능 |
| **SVG** | `viz` 이벤트로 별도 전달 — 스트리밍 아닌 일괄 렌더링 (정상) |
| **코드 블럭** | 스트리밍 중 ` ``` ` 가 미닫혀서 일시적으로 인라인 코드로 렌더링될 수 있음 |

**개선 방안**:
- 스트리밍 중에는 마크다운 파싱 대신 `textContent`로 출력하고, 완료 시 마크다운 렌더링
- 또는 `marked.parse()`에 incomplete markdown 방어 로직 추가 (현재는 방어 없음)
- **권장**: 스트리밍 중 마크다운 사전 렌더링 유지하되, `finalize()` 시 최종 re-render 수행 (현재 구현 방식 유지 — 큰 문제 아님)

---

### 1.15 마크다운 표 스크롤 적용

| 항목 | 내용 |
|------|------|
| **현상** | 가로/세로로 긴 표가 레이아웃을 벗어남 |
| **요구** | 스크롤 가능한 컨테이너에 넣고, SVG/HTML 시각화 박스와 디자인 일관성 유지 |
| **난이도** | 낮음 |

**구현**:

1. `attachCodeCopy()` (line 1409-1422)에서 이미 `.table-wrap`으로 감싸고 있으므로, CSS 추가:
   ```css
   .table-wrap {
     position: relative;
     overflow-x: auto;
     overflow-y: auto;
     max-height: 480px;
     background: var(--bg2);
     border: 1px solid var(--border);
     border-radius: var(--r-md);
     /* viz-body 와 디자인 일관성 */
   }
   .table-wrap table {
     margin: 0;  /* 기존 .bot-bubble table margin 리셋 */
   }
   ```

2. **디자인 일관성**: `.viz-body`와 동일한 `background`, `border`, `border-radius` 적용
   - `.code-wrap` (코드 블럭)도 동일 패턴 적용하여 3종 통일:
     - `.code-wrap` — 코드 블럭
     - `.table-wrap` — 마크다운 표
     - `.viz-body` — SVG/HTML 시각화

---

### 1.16 Progress Step Fade-out + "더 보기" (추가 요청)

| 항목 | 내용 |
|------|------|
| **현상** | `reason` 페이즈의 에이전틱 루프가 길어지면 하위 스텝 트리가 많아져 표시 영역을 초과하여 잘림 |
| **원인** | `.phase-steps`의 `max-height: 300px` (line 276) 설정이 있으나, 넘어가면 잘림 (overflow: hidden) |
| **요구** | 스크롤 대신 하단 fade-out 그라데이션 + [더 보기] 버튼으로 구현 (Claude.ai 참고) |
| **난이도** | 중간 |

**구현 설계**:

1. **CSS 변경** — 외부 `.phase-steps`는 높이 제한 해제, 내부 `.phase-steps-inner`에서 fade 처리:

```css
/* .phase-steps: 외부 컨테이너 — 펼침/접힘 트랜지션만 담당 */
.phase-steps {
  overflow: hidden; max-height: 0; opacity: 0;
  transition: max-height var(--t-slow) var(--ease), opacity var(--t) var(--ease);
}
.progress-phase.active .phase-steps,
.progress-phase.expanded .phase-steps {
  max-height: none;   /* 300px 제거 → 내부에 위임 */
  opacity: 1;
}

/* .phase-steps-inner: 실제 높이 제한 + fade-out */
.phase-steps-inner {
  display: flex; flex-direction: column; gap: 1px;
  padding: 2px 0 4px 22px;
  border-left: 2px solid var(--border); margin-left: 6px;
  background: var(--bg);     /* fade 그라데이션 기준색 명시 */
  position: relative;
  max-height: 120px; overflow: hidden;
  transition: max-height var(--t-slow) var(--ease);
}
/* 접힌 상태: 하단 fade-out 그라데이션 */
.phase-steps-inner:not(.expanded)::after {
  content: ''; position: absolute; bottom: 0; left: 0; right: 0; height: 40px;
  background: linear-gradient(transparent, var(--bg));
  pointer-events: none;
}
/* 펼친 상태 */
.phase-steps-inner.expanded { max-height: none; }
.phase-steps-inner.expanded::after { display: none; }

/* 더 보기 / 접기 버튼 */
.phase-expand-btn {
  display: inline-flex; align-items: center; gap: 4px;
  font-size: 11px; color: var(--txt3); cursor: pointer;
  padding: 4px 0 0 28px; background: none; border: none;
}
.phase-expand-btn:hover { color: var(--txt2); }
```

2. **JS 변경** — 펼침 상태를 `msg` 데이터에 저장하여 re-render 시 복원:

```javascript
// MS (Message Store)에 phaseExpanded 필드 추가
// msg.phaseExpanded = { "reason": true, "interpret": false, ... }

// _renderPhaseHTML에서 상태 복원
function _renderPhaseHTML(groups, collapsed, phaseExpanded) {
  return groups.map(function(g) {
    var isExpanded = (phaseExpanded || {})[g.phase];
    var innerCls = 'phase-steps-inner' + (isExpanded ? ' expanded' : '');
    // ... 기존 렌더링 ...
    // 스텝 4개 이상일 때만 "더 보기" 버튼 표시
    if (g.steps.length > 4) {
      h += '<button class="phase-expand-btn" data-phase="' + g.phase + '">'
        + (isExpanded ? '접기' : '더 보기') + '</button>';
    }
    // ...
  }).join('');
}

// 이벤트 위임으로 클릭 처리
slot.addEventListener('click', function(e) {
  var btn = e.target.closest('.phase-expand-btn');
  if (!btn) return;
  var phase = btn.dataset.phase;
  var msg = MS.get(msgId);
  msg.phaseExpanded = msg.phaseExpanded || {};
  msg.phaseExpanded[phase] = !msg.phaseExpanded[phase];
  RD.render(msg);  // re-render 시 phaseExpanded 참조하여 복원
});
```

3. **동작 흐름**:
   - 스텝 ≤ 4개: fade-out 없이 전체 표시
   - 스텝 > 4개 (접힌 상태): `max-height: 120px` + fade-out + [더 보기]
   - [더 보기] 클릭 → `msg.phaseExpanded[phase] = true` → re-render → 전체 표시 + [접기]
   - [접기] 클릭 → `msg.phaseExpanded[phase] = false` → re-render → 다시 120px + fade

> **핵심**: 펼침 상태를 DOM이 아닌 `msg` 데이터에 저장하여, progress 업데이트로 innerHTML이 재구축되어도 상태가 보존됨

---

### 1.17 /reset, /history → /new 명령어 변경 (추가 요청)

| 항목 | 내용 |
|------|------|
| **현상** | 체크포인트 DB 환경에서 `/reset`(대화 초기화), `/history`(대화 기록) 명령어의 의미가 퇴색 |
| **요구** | `/reset`, `/history` 제거 → `/new` (새 대화 열기) 로 통합 |
| **난이도** | 낮음 |

**변경 대상**:

1. **CM (Command Manager)** (line 1694-1770):
   - `CMDS` 배열에서 `/reset`, `/history` 모두 제거
   - `/new` 명령어 추가: `{name:'/new', desc:'새 대화 — 새로운 대화를 시작합니다', icon:'+'}`
   - 새 실행 함수 추가:
   ```javascript
   // 변경 전
   var CMDS = [
     {name:'/reset', desc:'대화 초기화 — 현재 대화를 모두 지웁니다', icon:'↺'},
     {name:'/history', desc:'대화 기록 — 이번 세션의 대화 요약을 표시합니다', icon:'⏱'}
   ];
   function _execReset() {
     SE.cancelAll(); MS.clear(); RD.clearChat(); IC2.setBusy(false);
     CN.send('/reset');
     RD.showBanner('status','대화가 초기화되었습니다.');
     SB.onReset();
   }
   
   // 변경 후
   var CMDS = [
     {name:'/new', desc:'새 대화 — 새로운 대화를 시작합니다', icon:'+'}
   ];
   function _execNew() {
     SB.onNewChat();  // 새 대화 열기 (CN.reconnect() 포함)
   }
   ```

2. **`_execHistory()`, `_renderHL()`, `_execReset()` 함수 제거**
3. **`tryExec()` 변경**: `/reset`, `/history` 분기 제거 → `/new` 분기 추가
   ```javascript
   function tryExec(text) {
     var cmd = text.trim().toLowerCase();
     if (cmd === '/new') { _execNew(); return true; }
     return false;
   }
   ```

---

### 1.18 LLM 모델 선택 및 Thinking 모드 UI (추가 요청)

| 항목 | 내용 |
|------|------|
| **참고** | Claude.ai 의 모델 선택 UI 참조 (입력창 우측 하단 드롭다운) |
| **현상** | 현재 서버는 단일 모델(`settings.llm_model`)로 고정 운영. 모델 Enum/목록 API 없음 |
| **요구** | 서버에서 사용 가능한 모델 목록 + Thinking 모드 옵션을 UI에서 선택 가능하도록 구현 |
| **난이도** | 높음 (서버 + UI 전체 설계) |

#### 현재 서버 상태 분석

| 영역 | 현재 구현 |
|------|----------|
| **모델 설정** | `config.py` line 45: `llm_model: str = "claude-sonnet-4-20250514"` — 단일 문자열 |
| **프로바이더** | `llm_provider: str` — `"anthropic"` 또는 `"openai_compatible"` |
| **Thinking 모드** | `thinking_modes.py` — 노드별 `off/auto/low/high` 설정. Gemini: `reasoning_effort`, Qwen: `enable_thinking` |
| **모델 목록 API** | 없음 |

#### 구현 설계

**1) 서버: 모델 Enum 및 설정 모델**

```python
# src/models/api/model_config.py (신규)
from enum import Enum
from pydantic import BaseModel

class ThinkingMode(str, Enum):
    OFF = "off"
    AUTO = "auto"      # 노드별 기본값 사용
    LOW = "low"
    HIGH = "high"

class AvailableModel(BaseModel):
    id: str                          # "claude-sonnet-4-20250514"
    display_name: str                # "Claude Sonnet 4.6"
    provider: str                    # "anthropic"
    supports_thinking: bool          # True
    thinking_modes: list[str]        # ["off", "auto", "low", "high"]
    is_default: bool                 # True
    description: str | None = None   # "Most efficient for everyday tasks"
```

**2) 서버: config.py 확장**

```python
# config.py 추가 필드
available_models: list[dict] = [
    {
        "id": "claude-sonnet-4-20250514",
        "display_name": "Sonnet 4.6",
        "provider": "anthropic",
        "supports_thinking": False,
        "thinking_modes": [],
        "is_default": True,
        "description": "빠르고 효율적인 범용 모델"
    },
    {
        "id": "claude-opus-4-20250514",
        "display_name": "Opus 4.5",
        "provider": "anthropic",
        "supports_thinking": False,
        "thinking_modes": [],
        "is_default": False,
        "description": "최고 성능의 복잡한 분석용 모델"
    },
    {
        "id": "gemini-3.1-flash-lite-preview",
        "display_name": "Gemini Flash Lite",
        "provider": "openai_compatible",
        "supports_thinking": True,
        "thinking_modes": ["off", "low", "high"],
        "is_default": False,
        "description": "빠른 응답의 경량 모델"
    }
]
default_thinking_mode: str = "auto"   # 사용자 기본 Thinking 모드
```

> **폐쇄망 배포 시**: `available_models`에 Solar Pro 2 70B, Qwen3.5 397B 등 폐쇄망 모델만 등록

**3) 서버: API 엔드포인트 추가**

```python
# src/routers/sessions.py 또는 src/main.py

@app.get("/api/models")
async def get_available_models():
    """사용 가능한 LLM 모델 목록 반환"""
    return {
        "models": settings.available_models,
        "current_model": settings.llm_model,
        "default_thinking_mode": settings.default_thinking_mode
    }
```

**4) 서버: WebSocket 메시지에 모델/Thinking 옵션 수신**

```python
# WebSocket 메시지 프로토콜 확장
# 현재: 단순 텍스트 전송 (ws.send(text))
# 변경: JSON 메시지 전송

# 클라이언트 → 서버
{
    "text": "이번 달 신규 고객 수 알려줘",
    "model": "claude-sonnet-4-20250514",    # 선택 모델 (없으면 기본값)
    "thinking_mode": "auto"                  # Thinking 모드 (없으면 기본값)
}
```

> **주의**: 기존 plain text 전송과의 하위호환 필요 — 서버에서 JSON 파싱 실패 시 plain text로 fallback

**5) 서버: runner.py 모델 오버라이드**

```python
# runner.py — 파이프라인 실행 시 사용자 선택 모델 적용
# config 오버라이드 또는 state에 model/thinking_mode 전달
state["user_model_override"] = selected_model
state["user_thinking_mode"] = thinking_mode
```

**6) UI: 모델 선택 컴포넌트**

```
입력창 레이아웃:
┌──────────────────────────────────────────────────────┐
│ 데이터 요청을 입력하세요…                               │
│                                                      │
│ ┌─────────────────────┐                    ┌──┐     │
│ │                     │  [Sonnet 4.6  ▾]   │↑ │     │
│ └─────────────────────┘                    └──┘     │
└──────────────────────────────────────────────────────┘

드롭다운 (위로 펼침):
┌──────────────────────────────┐
│ Claude Sonnet 4.6        ✓  │
│  빠르고 효율적인 범용 모델     │
│─────────────────────────────│
│ Claude Opus 4.5             │
│  최고 성능의 복잡한 분석용     │
│─────────────────────────────│
│ Gemini Flash Lite           │
│  빠른 응답의 경량 모델        │
│═════════════════════════════│
│ ☐ 확장 사고 (Thinking)       │
│   복잡한 작업을 위해 더 오래   │
│   사고합니다                  │
│─────────────────────────────│
│ Thinking 수준: [auto ▾]     │
│   off / auto / low / high   │
└──────────────────────────────┘
```

**7) UI: CSS 추가**

```css
/* 모델 선택 버튼 */
.model-select {
  display: flex; align-items: center; gap: 4px;
  padding: 3px 10px; border: 1px solid var(--border);
  border-radius: var(--r-full); font-size: 12px;
  color: var(--txt2); background: none; cursor: pointer;
  transition: all var(--t);
}
.model-select:hover { border-color: var(--border2); color: var(--txt); }
.model-select .model-dot {
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--bg-accent);
}

/* 모델 드롭다운 (위로 펼침) */
.model-dropdown {
  position: absolute; bottom: 100%; right: 0; margin-bottom: 6px;
  background: var(--bg); border: 1px solid var(--border);
  border-radius: var(--r-sm); box-shadow: var(--sh-md);
  min-width: 260px; z-index: 20; overflow: hidden;
}
.model-option {
  padding: 10px 14px; cursor: pointer; transition: background var(--t-fast);
}
.model-option:hover, .model-option.active { background: var(--bg3); }
.model-option-name { font-size: 13px; font-weight: 500; color: var(--txt); }
.model-option-desc { font-size: 11.5px; color: var(--txt3); margin-top: 2px; }
.model-option .check-mark { color: var(--bg-accent); float: right; }

/* Thinking 토글 섹션 */
.thinking-section {
  padding: 10px 14px; border-top: 1px solid var(--border);
}
.thinking-toggle {
  display: flex; align-items: center; justify-content: space-between;
}
.thinking-toggle-label { font-size: 13px; color: var(--txt); }
.thinking-toggle-desc { font-size: 11px; color: var(--txt3); margin-top: 2px; }
/* iOS 스타일 토글 스위치 */
.toggle-switch {
  width: 40px; height: 22px; border-radius: 11px;
  background: var(--border2); position: relative; cursor: pointer;
  transition: background var(--t);
}
.toggle-switch.on { background: var(--bg-accent); }
.toggle-switch::after {
  content: ''; position: absolute; top: 2px; left: 2px;
  width: 18px; height: 18px; border-radius: 50%;
  background: white; transition: transform var(--t);
}
.toggle-switch.on::after { transform: translateX(18px); }
```

**8) UI: JS 모듈 추가 — MM (Model Manager)**

```javascript
var MM = (function() {
  var _models = [], _current = null, _thinking = true, _thinkingLevel = 'auto';
  var _popup = null;

  async function init() {
    try {
      var resp = await API.getModels();  // GET /api/models
      _models = resp.models || [];
      _current = resp.current_model;
      _thinking = resp.default_thinking_mode !== 'off';
      _thinkingLevel = resp.default_thinking_mode || 'auto';
    } catch(e) {
      // fallback: 서버 모델 조회 실패 시 기본값 사용
      _models = [{ id: 'default', display_name: '기본 모델', is_default: true }];
    }
    _renderButton();
  }

  function _renderButton() { /* 입력창 하단에 모델 선택 버튼 렌더링 */ }
  function _showDropdown() { /* 드롭다운 표시 */ }
  function _hideDropdown() { /* 드롭다운 숨김 */ }
  function selectModel(modelId) { _current = modelId; _renderButton(); }
  function setThinking(on) { _thinking = on; }
  function getSelection() {
    return { model: _current, thinking_mode: _thinking ? _thinkingLevel : 'off' };
  }

  return { init: init, getSelection: getSelection };
})();
```

**9) UI: CN.send() 변경 — JSON 프로토콜**

> **Phase 1.5에서 이미 JSON 전환 완료**. 이 단계에서는 `MM.getSelection()`으로 모델/thinking 정보를 추가 전달만 하면 됨.

```javascript
// Phase 1.5에서 CN.send(t, opts) 형태로 전환 완료
// 이 단계에서는 호출부에서 opts 추가:
CN.send(t, MM.getSelection());
// → { text: t, model: "...", thinking_mode: "..." }
```

**10) API 모듈 확장**

```javascript
// API 객체에 추가
getModels: function() { return request('GET', '/models'); }
```

#### 고려사항

| 항목 | 설명 |
|------|------|
| **폐쇄망 전환** | `available_models` 설정만 변경하면 UI 자동 반영 |
| **모델별 비용** | 향후 토큰 비용 표시 기능과 연계 가능 |
| **권한 제어** | 관리자만 고성능 모델(Opus) 사용 가능하도록 권한 분리 검토 |
| **세션 단위 vs 턴 단위** | 턴 단위 모델 변경 허용 (같은 대화에서 모델 전환 가능) |
| **Thinking 모드 노출 조건** | `supports_thinking: true`인 모델만 Thinking 섹션 표시. 모델 전환 시 동적으로 표시/숨김 처리. `_showDropdown()` 내에서 `model.supports_thinking` 체크하여 `.thinking-section` display 제어 |
| **모델 상태 표시** | 현재 topbar의 `model-pill`에 선택된 모델명 동적 표시 |

---

### 1.19 LLM Chain of Thought 표시 (추가 요청)

| 항목 | 내용 |
|------|------|
| **참고** | Claude.ai 캡처에서 progress 영역에 `1. 2. ...` 형태로 LLM 사고 과정이 스트리밍됨 |
| **현상** | 현재 progress 이벤트는 노드 진입/완료 라벨만 전송. LLM reasoning 내용은 전달되지 않음 |
| **원인** | `callback_handler.py`의 `_emit_progress()`가 `thinkingLabel`(정적 텍스트)만 전송. `on_llm_new_token` 미구현. `llm_client.py`가 Qwen thinking 태그를 `_strip_thinking_tags()`로 제거 |
| **난이도** | 높음 (서버 LLM 클라이언트 + 콜백 + UI 전체 변경) |

**2단계 구현 계획**:

**Phase 2 (즉시 가능) — 상세 라벨 확장**:

현재 `NODE_PROGRESS_MAP`의 `label`을 더 상세하게 확장. 서버 변경 최소:

```python
# callback_handler.py — on_custom_event에서 상세 정보 추가 전송
# 예: context_retriever가 찾은 테이블 수, sql_generator가 생성한 SQL 요약 등
await self._on_event({
    "type": "progress",
    "action": "update",        # 기존 스텝 라벨 업데이트
    "phase": phase,
    "label": "3개 테이블에서 관련 컬럼 12개 확인",  # 동적 상세 라벨
})
```

UI에서 `action === 'update'`를 처리하여 마지막 active 스텝의 라벨을 업데이트.

**Phase 3 (서버 인프라 변경) — 실제 CoT 스트리밍**:

1. **서버: LLM thinking 추출 인프라**:
   - `llm_client.py`: `_strip_thinking_tags()` → `_extract_thinking_tags()`로 변경, thinking 내용을 별도 반환
   - Anthropic API: `thinking` 파라미터 활성화, `response.content`에서 `type="thinking"` 블록 추출
   - `on_llm_new_token` 콜백 구현 (thinking 토큰과 응답 토큰 구분)

2. **서버: progress 이벤트 확장**:
   ```python
   await self._on_event({
       "type": "progress",
       "action": "thinking",          # 신규 action
       "phase": phase,
       "node": current_node,          # 노드별 구분
       "content": thinking_chunk,     # 실제 사고 내용 (청크)
   })
   ```

3. **UI: thinking 콘텐츠 렌더링**:
   - 각 노드별 thinking을 `msg.nodeThinking[node]`에 저장 (노드별 분리, 누적 아님)
   - 현재 active 페이즈의 active 노드 thinking만 표시
   - 1.16의 fade-out + "더 보기" 패턴 동일 적용

   ```javascript
   // handleProgress에 thinking action 추가
   if (data.action === 'thinking') {
     msg.nodeThinking = msg.nodeThinking || {};
     msg.nodeThinking[data.node] = (msg.nodeThinking[data.node] || '') + data.content;
     RD.render(msg);
   }
   ```

4. **CSS: thinking 콘텐츠 영역**:
   ```css
   .thinking-content {
     margin: 4px 0 4px 28px; padding: 8px 12px;
     background: var(--bg2); border-radius: var(--r-sm);
     font-size: 12.5px; line-height: 1.55; color: var(--txt3);
     max-height: 120px; overflow: hidden; position: relative;
   }
   .thinking-content:not(.expanded)::after {
     content: ''; position: absolute; bottom: 0; left: 0; right: 0; height: 40px;
     background: linear-gradient(transparent, var(--bg2));
     pointer-events: none;
   }
   .thinking-content.expanded { max-height: none; }
   ```

#### 고려사항

| 항목 | 설명 |
|------|------|
| **Anthropic** | `thinking` 파라미터 + `budget_tokens` 설정 필요. thinking 블록은 `content` 배열에서 `type="thinking"`으로 구분 |
| **Qwen (폐쇄망)** | `<think>...</think>` 태그 내부 추출. `enable_thinking` 파라미터 활성화 |
| **모델별 분기** | `thinking_modes.py`의 노드별 설정에 따라 thinking이 off인 노드는 표시 안 함 |
| **Phase 2 vs 3** | Phase 2는 상세 라벨만으로도 사용자 경험 크게 개선. Phase 3는 서버 인프라 변경이 선행되어야 함 |

---

### 1.20 "스크롤 맨아래로" 버튼 (추가 요청)

| 항목 | 내용 |
|------|------|
| **현상** | 스크롤을 올려 답변이 가려지면 맨 아래로 돌아갈 수 있는 수단이 없음. `_userScrolledUp` 플래그로 자동 스크롤만 제어 |
| **요구** | 입력창 바로 위 가운데에 하향 화살표 버튼 표시 → 클릭 시 맨 아래로 이동 |
| **난이도** | 낮음 |

**구현 설계**:

1. **HTML** — `#chatWrap` 내부에 배치 (absolute 포지셔닝 기준):
   ```html
   <!-- chatWrap 내부, chat-inner 앞 -->
   <button id="scrollBottomBtn" class="scroll-bottom-btn" style="display:none">
     <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
       stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
       <polyline points="6 9 12 15 18 9"/>
     </svg>
   </button>
   ```

2. **CSS**:
   ```css
   /* .chat-wrap에 position: relative 추가 (기존에 없음) */
   .chat-wrap { position: relative; /* 기존 스타일 유지 */ }

   .scroll-bottom-btn {
     position: sticky; bottom: 16px;
     left: 50%; transform: translateX(-50%);
     width: 36px; height: 36px; border-radius: 50%;
     background: var(--bg); border: 1px solid var(--border);
     box-shadow: var(--sh-sm);
     display: none; align-items: center; justify-content: center;
     cursor: pointer; z-index: 10;
     transition: opacity var(--t), background var(--t);
     color: var(--txt2);
   }
   .scroll-bottom-btn:hover {
     background: var(--bg2); border-color: var(--border2);
   }
   ```

3. **JS** — 기존 scroll 이벤트 리스너 확장 (새 리스너 추가하지 않음):
   ```javascript
   // 기존 scroll 리스너 (line 2296-2303) 확장
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

   // App에 추가
   scrollToBottom: function() {
     _userScrolledUp = false;
     chatWrapEl.scrollTo({ top: chatWrapEl.scrollHeight, behavior: 'smooth' });
     document.getElementById('scrollBottomBtn').style.display = 'none';
   }
   ```

4. **동작 흐름**:
   - 100px 이상 스크롤 올리면 → 버튼 fade-in (입력창 바로 위 가운데)
   - 클릭 → smooth scroll to bottom + 버튼 숨김 + 자동 스크롤 재활성화
   - 수동으로 맨 아래로 스크롤해도 → 버튼 자동 숨김

---

### 1.21 스크롤 부드러움 개선 (추가 발견)

| 항목 | 내용 |
|------|------|
| **현상** | 대화창 상하 스크롤이 뻑뻑하게 느껴짐 |
| **원인** | `.chat-wrap`에 `scroll-behavior: smooth` (line 134) 적용됨. 이 속성은 프로그래밍 방식 스크롤뿐 아니라 **사용자 수동 휠/터치 스크롤에도 easing을 적용**하여 입력 지연(lag) 느낌 발생 |
| **난이도** | 낮음 |

**변경**:
```css
/* 변경 전 */
.chat-wrap { scroll-behavior: smooth; /* ... */ }

/* 변경 후: scroll-behavior 제거 */
.chat-wrap { /* scroll-behavior 제거 — 수동 스크롤은 네이티브 속도 유지 */ }
```

프로그래밍 방식 스크롤(자동 스크롤, 맨아래로 이동 등)에서만 `behavior: 'smooth'` 옵션 사용:
```javascript
// autoScroll() 등에서 명시적으로 smooth 지정
chatWrapEl.scrollTo({ top: chatWrapEl.scrollHeight, behavior: 'smooth' });
```

> **원리**: CSS `scroll-behavior: smooth`는 컨테이너의 **모든** 스크롤에 적용되어 수동 휠 스크롤까지 easing 처리됨. JS `scrollTo({behavior: 'smooth'})`는 해당 호출에만 적용되므로 수동 스크롤은 네이티브 속도 유지.

---

## 2. UX/UI 에이전트 추가 발견 사항

### 2.1 접근성 (Accessibility)

| 이슈 | 위치 | 심각도 | 설명 |
|------|------|--------|------|
| 키보드 미지원 | `.chat-item`, `.phase-header` | 높음 | `tabindex="0"` + `role="button"` 있으나 Enter/Space keydown 핸들러 없음 |
| ARIA 부재 | 채팅 컨테이너 | 중간 | `role="log"` 또는 `aria-live="polite"` 미적용 → 스크린리더가 새 메시지 인지 불가 |
| 포커스 관리 | 모달 | 중간 | 모달 열릴 때 포커스 트랩 없음 (Tab으로 모달 외부 이동 가능) |
| 색상 의존 | 상태 표시 | 낮음 | `.status-dot`가 색상만으로 상태 구분 (색각 이상자 대응 필요) |

### 2.2 모바일 대응

| 이슈 | 설명 |
|------|------|
| 터치 피드백 | 버튼 hover 효과만 있고 active/touch 피드백 없음 |
| 뷰포트 높이 | 모바일 키보드 올라올 때 입력 영역이 가려질 수 있음 (`vh` → `dvh` 또는 JS 보정) |
| 사이드바 스와이프 | 모바일에서 스와이프로 사이드바 열기/닫기 미지원 |

### 2.3 에러 핸들링 UX

| 이슈 | 설명 |
|------|------|
| 네트워크 에러 | 오프라인 배너만 표시, 재연결 시도 횟수/상태 미표시 |
| 타임아웃 | 30초 타임아웃 (line 1491) 후 단순 배너만 표시, 사용자 액션 유도 없음 |
| WebSocket 재연결 | 5회 실패 시 "새로고침해주세요" — 자동 재연결 버튼 없음 |

### 2.4 시각적 일관성

| 이슈 | 설명 |
|------|------|
| 이모지 아이콘 혼용 | progress step에서 `🔍`, `🧠`, `📊` 이모지 사용 vs insight에서 `📋`, `🔍`, `🗂️` 사용. SVG 아이콘과 이모지 혼용 |
| 간격 불일치 | `.msg-actions` gap: 1px vs `.viz-actions` gap: 8px — 버튼 간격 불일치 |

---

## 3. 신규 기능 제안 — 사용자 편의기능

### 3.1 우선순위 높음

| 기능 | 설명 | 난이도 |
|------|------|--------|
| **메시지 검색** | 대화 내 텍스트 검색 (Ctrl+F 연동 또는 별도 UI) | 중간 |
| **답변 북마크** | 중요한 답변에 별표 → 빠르게 재참조 | 중간 |
| **표 엑셀 다운로드** | CSV 외에 `.xlsx` 포맷 다운로드 (서식 유지). 서버에서 Python `openpyxl`로 생성 → `pyproject.toml` 추가, `uv`로 설치 | 중간 |
| **입력 히스토리** | ↑↓ 키로 이전 입력 불러오기 | 낮음 |
| **응답 완료 알림** | 브라우저 알림 (긴 분석 대기 시) | 낮음 |

### 3.2 우선순위 보통

| 기능 | 설명 | 난이도 |
|------|------|--------|
| **대화 공유/내보내기** | 대화를 PDF/HTML로 내보내기 (보고용) | 중간 |
| **빠른 질문 템플릿** | 자주 쓰는 질문을 저장/재사용 | 중간 |
| **멀티턴 요약** | 긴 대화 후 "지금까지 내용 요약해줘" 기능 | 높음 |
| **드래그앤드롭 파일 업로드** | 엑셀 파일 업로드 → 데이터 참조 분석 | 높음 |
| **차트 커스터마이징** | 차트 색상, 제목, 범례 수정 UI | 높음 |

### 3.3 우선순위 낮음

| 기능 | 설명 | 난이도 |
|------|------|--------|
| **음성 입력** | Web Speech API 기반 음성→텍스트 | 중간 |
| **다국어 지원** | 영어/한국어 전환 | 높음 |
| **대화 태그/분류** | 대화에 태그 부여하여 분류 | 중간 |

---

## 4. 신규 기능 제안 — 관리자 기능

| 기능 | 설명 | 난이도 |
|------|------|--------|
| **사용량 대시보드** | 일별 질의 건수, 평균 응답 시간, 성공률, 많이 사용한 테이블 | 높음 |
| **피드백 리포트** | 좋아요/싫어요 통계, 싫어요 사유 분류, 개선 필요 질문 목록 | 중간 |
| **SQL 골든셋 관리** | 검증된 SQL-질문 쌍 등록/수정/삭제 UI | 높음 |
| **테이블 메타 관리** | MongoDB 메타데이터 조회/수정 UI (설명, 용도, 갱신주기) | 높음 |
| **사용자 권한 관리** | 사용자별 접근 가능 테이블/스키마 설정 | 높음 |
| **프롬프트 버전 관리** | 프롬프트 A/B 테스트, 버전별 성능 비교 | 높음 |
| **실시간 모니터링** | 현재 활성 세션, 진행 중 파이프라인, 에러 알림 | 높음 |
| **LLM 비용 추적** | 모델별 토큰 사용량, 비용 추이, 예산 알림 | 중간 |
| **공지사항 관리** | 시스템 점검, 기능 업데이트 알림 배너 | 낮음 |
| **금칙어/블랙리스트** | SQL 인젝션 패턴, 금칙어 관리 UI | 중간 |

---

## 5. 구조적 개선 (코드 품질)

### 5.1 현재 구조 평가

단일 파일 2,415줄 구성:
- CSS (574줄): 체계적인 CSS 변수 시스템, 3 테마 지원 — 양호
- HTML (138줄): 간결한 구조 — 양호
- JS (1,703줄): 10개 모듈 (TM, API, MS, RD, SE, ED, IC2, CN, CM, SB) — IIFE 패턴으로 적절히 분리

### 5.2 주요 개선 포인트

| 영역 | 이슈 | 제안 |
|------|------|------|
| **DOM 생성** | `ensureDOM()`에서 innerHTML 문자열 연결 (line 878-894) | 템플릿 리터럴 또는 DOM fragment 패턴으로 리팩토링 |
| **이벤트 위임** | 개별 요소에 이벤트 바인딩 (line 896-912) | `.msg-actions`에 이벤트 위임(delegation) 적용 |
| **메모리 관리** | `renderList()`에서 매번 `el.innerHTML` 교체 → 기존 이벤트 리스너 정리 안 됨 | 이벤트 위임으로 전환 |
| **상태 동기화** | MS(Message Store)와 DOM 상태 분리됨 | 현재 수준에서는 충분, React 전환 시 개선 |
| **CSS 중복** | `.viz-body`, `.table-wrap`, `.code-wrap` 유사 스타일 중복 | 공통 `.content-block` 클래스 추출 |

### 5.3 파일 분리 여부

현재 단일 파일 유지 **권장**:
- 폐쇄망 배포 환경에서 단일 파일이 유리 (번들링 도구 불필요)
- 2,415줄은 관리 가능한 수준
- 모듈 패턴(IIFE)으로 적절히 분리되어 있음
- 단, **3,000줄 초과 시** CSS를 별도 파일로 분리 검토

---

## 6. 구현 우선순위 및 일정 제안

### Phase 1: Quick Wins (즉시 적용 가능)

| # | 항목 | 난이도 | 영향도 |
|---|------|--------|--------|
| 1.1 | 액션 아이콘 한 줄 통합 | 낮음 | 높음 |
| 1.2 | CSV 다운로드 배너 삭제 | 낮음 | 중간 |
| 1.12 | 마크다운 셀 높이 축소 | 낮음 | 중간 |
| 1.13 | 사이드바 최신순 정렬 | 낮음 | 중간 |
| 1.15 | 마크다운 표 스크롤 | 낮음 | 높음 |
| 1.17 | /reset, /history → /new 변경 | 낮음 | 낮음 |
| 1.9b | SQL 하이라이팅 | 낮음 | 중간 |
| 1.20 | "스크롤 맨아래로" 버튼 | 낮음 | 중간 |
| 1.21 | 스크롤 부드러움 개선 (`scroll-behavior` 제거) | 낮음 | 높음 |

### Phase 1.5: WebSocket JSON 프로토콜 전환 (Phase 2/3의 선행 작업)

> **배경**: 현재 `CN.send()`는 plain text로 전송하고 서버는 `receive_text()`로 수신.
> Phase 2의 1.11(재생성)과 Phase 3의 1.18(모델 선택)이 모두 JSON payload를 필요로 하므로,
> 프로토콜 전환을 독립 작업으로 먼저 수행.

| # | 항목 | 난이도 | 영향도 |
|---|------|--------|--------|
| P1.5-1 | 서버: `receive_text()` 후 JSON 파싱 시도 → 실패 시 plain text fallback | 낮음 | 높음 |
| P1.5-2 | UI: `CN.send(t)` → `CN.send(JSON.stringify({text: t}))` 전환 | 낮음 | 높음 |
| P1.5-3 | 슬래시 명령어(`/new` 등) JSON 래핑 호환 확인 | 낮음 | 중간 |

**서버 변경** (`src/main.py`):

```python
# 변경 전
data = await websocket.receive_text()

# 변경 후 (backward compatible)
raw = await websocket.receive_text()
try:
    parsed = json.loads(raw)
    user_text = parsed.get("text", raw)
    user_model = parsed.get("model")           # Phase 3에서 활용
    user_thinking = parsed.get("thinking_mode") # Phase 3에서 활용
    is_regen = parsed.get("action") == "regen"  # Phase 2에서 활용
    original_turn_id = parsed.get("original_turn_id")
except (json.JSONDecodeError, AttributeError):
    user_text = raw  # plain text fallback
    user_model = None
    user_thinking = None
    is_regen = False
    original_turn_id = None
```

**UI 변경** (`static/embedded.html` — CN 모듈):

```javascript
// 4개 CN.send() 호출 지점 모두 자동 적용
function send(t, opts) {
  if (ws && ws.readyState === 1) {
    var payload = { text: t };
    if (opts) Object.assign(payload, opts);
    ws.send(JSON.stringify(payload));
    return true;
  }
  return false;
}
```

### Phase 2: 중요 기능 구현

| # | 항목 | 난이도 | 영향도 |
|---|------|--------|--------|
| 1.3 | Trace 파일 다운로드 버튼 | 중간 | 중간 |
| 1.5 | 결과 순서 변경 (프롬프트 변경) | 낮음 | 높음 |
| 1.6/1.7 | 접기 가능한 참고 블럭 | 중간 | 높음 |
| 1.8 | 대화이력 insight 버튼 표시 | 낮음 | 중간 |
| 1.10 | 설정 모달 확장 (8개 항목) | 중간 | 중간 |
| 1.11 | 재생성 기능 개선 (JSON `action:"regen"` 활용) | 중간 | 중간 |
| 1.16 | Progress step fade-out + "더 보기" | 중간 | 중간 |
| 1.19a | LLM CoT — 상세 라벨 확장 (서버 최소 변경) | 중간 | 중간 |

### Phase 3: 핵심 기능 구현

| # | 항목 | 난이도 | 영향도 |
|---|------|--------|--------|
| 1.4 | 명확화 질문 UI | 높음 | 높음 |
| 1.18 | LLM 모델 선택 + Thinking 모드 UI (JSON `model`/`thinking_mode` 활용) | 높음 | 높음 |
| 1.19b | LLM CoT — 실제 thinking 추출 + 스트리밍 (서버 인프라) | 높음 | 높음 |
| 2.1 | 접근성 개선 | 중간 | 중간 |
| 3.1 | 사용자 편의 기능 | 다양 | 높음 |

### Phase 4: 관리자 기능 (별도 HTML 페이지)

> **기술 결정**: 별도 `admin.html` 파일로 구현 (vanilla JS 단일 파일 방식 유지).
> 폐쇄망 배포 환경에서 번들링 도구 불필요, 현재 프로젝트 컨벤션과 일관성 유지.
> 차트/테이블 위주이므로 Chart.js 정도면 충분.

| # | 항목 | 난이도 | 영향도 |
|---|------|--------|--------|
| 4.x | 관리자 대시보드 | 높음 | 높음 |
| 4.x | 피드백/사용량 리포트 | 중간 | 높음 |

---

## 부록: 서버-UI 인터페이스 요약

### WebSocket 메시지 타입 (서버→UI)

| 타입 | action | 설명 |
|------|--------|------|
| `progress` | add/done/set | 진행 단계 업데이트 |
| `stream` | start/chunk/end | 응답 스트리밍 |
| `viz` | — | 시각화 데이터 (SVG/HTML) |
| `download_ready` | — | CSV 다운로드 준비 |
| `error` | — | 에러 메시지 |
| `status` | — | 상태 메시지 |

### REST API 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/sessions` | 세션 목록 |
| GET | `/api/sessions/{id}` | 세션 상세 (턴 목록) |
| DELETE | `/api/sessions/{id}` | 세션 아카이브 |
| PATCH | `/api/sessions/{id}/title` | 제목 수정 |
| GET | `/api/turns/{id}/metadata` | 턴 메타데이터 (lazy load) |
| PATCH | `/api/turns/{id}/like` | 피드백 기록 |
| PATCH | `/api/turns/{id}/download` | 다운로드 이력 기록 |
| POST | `/api/sessions/{id}/cancel` | 파이프라인 취소 |
| PATCH | `/api/sessions/{id}/unarchive` | 세션 아카이브 복원 |
| POST | `/api/download` | CSV/JSON 다운로드 |
| GET | `/api/models` | 사용 가능한 LLM 모델 목록 **(1.18 신규)** |
| GET | `/api/traces/{filename}` | Trace 파일 다운로드 **(1.3 신규)** |

### Trace 파일 (서버 생성)

| 파일 | 경로 패턴 | 설명 |
|------|----------|------|
| JSON 텔레메트리 | `logs/traces/trace_telemetry_{date}_{user}_{session}_{turn}.json` | 전체 추적 데이터 |
| MD 리포트 | `logs/traces/trace_report_{date}_{user}_{session}_{turn}.md` | 실행 요약 보고서 |
| MD 추론 플로우 | `logs/traces/trace_reasoning_{date}_{user}_{session}_{turn}.md` | LLM 의사결정 흐름 |
