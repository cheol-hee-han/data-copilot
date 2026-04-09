# UI/UX 설계 품질 검토 보고서: 프론트엔드 변경 사항

- **검토일**: 2026-04-05
- **검토 대상**: static/embedded.html (1,863줄, 순수 JS 단일 파일)
- **검토 맥락**: PostgreSQL 기반 대화 이력 관리 시스템 도입에 따른 프론트엔드 변경 검토
- **참조 설계서**: docs/todo/20260405-postgres-conversation-history-design.md

---

## 1. 컴포넌트 구조 검토 (Component Structure Review)

### 1.1 현재 모듈 구조 평가

| 모듈 | 줄 수(추정) | 책임 | 평가 |
|------|------------|------|------|
| TM (Theme Manager) | ~23줄 | 테마 전환 (light/dim/dark) | pass |
| MS (Message Store) | ~30줄 | 메시지 CRUD, sessionStorage 영속화 | needs-improvement |
| RD (Renderer) | ~240줄 | Markdown 렌더링, 진행률, 시각화, 인사이트, 다운로드 | needs-improvement |
| SE (Stream Engine) | ~25줄 | 텍스트 스트리밍 애니메이션 | pass |
| ED (Event Dispatcher) | ~120줄 | WebSocket 이벤트 핸들링, 상태 전이 | needs-improvement |
| IC2 (Input Controller) | ~17줄 | 입력 상태 제어 (busy/idle) | pass |
| CN (Connection) | ~22줄 | WebSocket 연결 관리 | needs-improvement |
| CM (Command Manager) | ~80줄 | / 명령어 자동완성 | pass |
| SB (Sidebar Manager) | ~70줄 | 세션 목록 관리 (localStorage) | needs-improvement |
| Utils | ~100줄 | sanitize, esc, toast, confirm 등 | pass |
| App (Public API) | ~100줄 | 외부 이벤트 핸들러 | needs-improvement |

### 1.2 구조적 이슈

(1) MS 모듈 (line 689-719) [warning/structure]: 메시지 구조에 turn_id가 없어 서버 응답과 매핑 불가. -> MS.create()에 turnId 필드 추가 필요.

(2) RD 모듈 (line 724-1161) [warning/structure]: ~240줄로 5개 이상의 렌더링 관심사를 처리. -> IIFE 내부에서 논리적 그룹으로 주석 분리.

(3) SB 모듈 (line 1449-1518) [critical/structure]: localStorage 동기 접근. 서버 API 전환 시 전면 재작성 필요. -> _cache 패턴으로 비동기 전환을 SB 내부에 격리.

(4) CN 모듈 (line 1342-1363) [warning/structure]: session_id를 클라이언트에서 생성. -> 서버 할당 또는 2단계 패턴.

### 1.3 재사용 후보

(5) 액션 버튼 바인딩 (line 752-763) [suggestion/structure]: ensureDOM에 inline 작성. -> createActionBar() 헬퍼 추출.

---

## 2. 접근성 감사 (Accessibility Audit)

### 접근성 등급: 경미 (Minor)

(6) msg-actions (line 236, 740-748) [warning/accessibility]: hover에만 반응, 키보드 focus 시 보이지 않음. WCAG 2.1 AA 2.1.1. -> :focus-within 규칙 추가.

(7) 설정/확인 모달 (line 600-643) [suggestion/accessibility]: aria-modal, role="dialog" 누락. WCAG 2.1 AA 4.1.2. -> 속성 추가.

(8) 모달 focus trap (line 367-370) [suggestion/accessibility]: 설정/확인 모달에 focus trap 미구현. WCAG 2.1 AA 2.1.2. -> trapFocus() 유틸 구현.

(9) 좋아요/싫어요 (신규) [warning/accessibility]: 3-state 토글 스크린리더 전달 필요. -> aria-pressed 동적 업데이트.

---

## 3. 시각적 일관성 검사 (Visual Consistency Check)

### 일관성 등급: 높음 (High)

CSS 변수 기반 디자인 토큰이 잘 구성되어 있다(line 23-59). 3개 테마 일관.

(10) 상태 색상 하드코딩 (line 122, 419-434) [suggestion/consistency]: #22c55e, #f59e0b, #991b1b가 CSS 변수가 아닌 하드코딩. -> --clr-success, --clr-warning, --clr-danger 시맨틱 변수 도입.

---

## 4~6. 변경 항목별 UX 검토 및 추가 고려사항

> 상세 내용은 section-8-7 제안 파일에 통합.

주요 발견:
- SB 비동기 전환 시 스켈레톤 로더 + 캐시 패턴 필요 (critical)
- 2-tier 로딩의 IntersectionObserver 기반 지연 로드 + placeholder UX 필요 (warning)
- 좋아요/싫어요 3-state optimistic update + rollback 패턴 필요 (warning)
- turn_id를 stream:start WebSocket 이벤트로 전달하는 백엔드 연동 필요 (critical)
- localStorage 마이그레이션 1회성 플로우 필요 (critical)
- WebSocket 재연결 시 기존 session_id 유지 (warning)
- 오프라인 대응 retry queue (suggestion)
- 사용자명 하드코딩(line 547) 해소 (suggestion)
- 과거 대화 이어서 질문 UX + 30일 TTL 읽기 전용 (suggestion)

---

## 이슈 요약

| 등급 | 개수 | 주요 내용 |
|------|------|----------|
| Critical | 3 | SB 모듈 비동기 전환, turn_id 수신 구조, localStorage 마이그레이션 |
| Warning | 8 | RD 모듈 분리, CN session_id, 좋아요 UX, Tier 2 placeholder, 로딩 상태, 접근성 |
| Suggestion | 8 | 색상 변수화, 오프라인 대응, 사용자 인증, 과거 대화 이어가기, 다운로드 기록, 턴 연속성, 액션바 추출, focus trap |
