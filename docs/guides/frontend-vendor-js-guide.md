# 프론트엔드 vendor JS 유지보수 가이드

> **대상**: `static/embedded.html` 및 `static/vendor/` 에 수동 반입된 서드파티 JS/CSS
> **작성일**: 2026-04-15
> **용도**: 폐쇄망에서 CDN 불가 → 로컬 파일 반입으로 운영하는 단일 HTML 프론트엔드의 버전 관리

---

## 1. 현황 — 왜 vendor 로컬 반입 방식인가

이 프로젝트의 프론트엔드는 **빌드 없는 단일 HTML 파일**(`static/embedded.html`, ~3,000줄) 입니다.

- React/Vite/npm 체인 **없음** — 빌드 아티팩트·node_modules·package.json 모두 없음
- 모든 DOM·이벤트·렌더링 로직이 `<script>` 인라인으로 포함됨
- FastAPI 가 `static/` 디렉토리를 `/vendor/...` 경로로 서빙 → CDN 의존 없이 폐쇄망에서도 동일 동작

**제약**: 브라우저가 로드하는 모든 JS 는 이 저장소에 **직접 커밋된 파일**이어야 합니다.
CDN (`cdnjs.cloudflare.com`, `unpkg.com` 등) 은 폐쇄망에서 접근 불가이므로 `<script src="https://…">` 형태는 금지.

---

## 2. 현재 반입된 vendor 자산

| 경로 | 파일 | 버전 | 라이선스 | 용도 | embedded.html 참조 |
|------|------|------|----------|------|:--:|
| `vendor/marked.min.js` | marked | **v4.3.0** | MIT | Markdown → HTML 변환 (LLM 응답 렌더링) | ✅ L8 |
| `vendor/hljs/highlight.min.js` | highlight.js | **v11.9.0** | BSD-3 | 코드 블록 구문 강조 | ✅ L9 |
| `vendor/hljs/{python,sql,javascript,json,bash}.min.js` | hljs 언어 모듈 | v11.9.0 | BSD-3 | 언어별 문법 (core 에서 분리됨) | 동적 로드 가능 |
| `vendor/hljs/github{,-dark}.min.css` | hljs 테마 CSS | v11.9.0 | BSD-3 | 라이트/다크 테마 스타일 | ✅ L11-12 |
| `vendor/sql-formatter.min.js` | sql-formatter | (버전 헤더 없음) | MIT | SQL 자동 정렬 | ✅ L10 |
| `vendor/html2canvas.min.js` | html2canvas | **1.4.1** | MIT | DOM → PNG 캡처 (차트 PNG 다운로드) | ❌ **미참조** (§4-3 참조) |
| `vendor/fonts/pretendard.css` + woff2 | Pretendard | 로컬 사본 | Open Font License 1.1 | 한글 폰트 | ✅ L13 |

**마지막 확인일**: 2026-04-15 — `head -c 200 static/vendor/*.js` 로 헤더에 명시된 버전을 기준.

---

## 3. 업데이트가 필요한 경우

### 3-1. 보안 취약점 발견 (CVE)

Dependabot 을 쓸 수 없으므로 **분기 1회** 수동 점검 권장:

```bash
# 각 라이브러리의 GitHub releases 페이지에서 현재 버전 이후 Security Advisory 확인
# - marked:           https://github.com/markedjs/marked/security/advisories
# - highlight.js:     https://github.com/highlightjs/highlight.js/security/advisories
# - sql-formatter:    https://github.com/sql-formatter-org/sql-formatter/security/advisories
# - html2canvas:      https://github.com/niklasvh/html2canvas/security/advisories
```

### 3-2. 기능 개선 필요

- Markdown 확장 구문 (mermaid, math) 요구 시 → marked v5+ (plugin API 변경, breakings 주의)
- 새 프로그래밍 언어 하이라이팅 추가 → hljs 공식 배포에서 해당 `<lang>.min.js` 내려받아 `static/vendor/hljs/` 에 배치

---

## 4. 업데이트 절차 (온라인 → 폐쇄망)

### 4-1. 온라인 환경에서 파일 수집

```bash
# 예: marked v4.3.0 → v4.3.1 업그레이드
curl -fLO https://cdn.jsdelivr.net/npm/marked@4.3.1/marked.min.js
# 해시 검증 — npm 공식 해시 또는 GitHub release 첨부 해시 대조
sha256sum marked.min.js
# 기대값을 GitHub release notes 의 sha256 와 비교 (예: "Asset checksums" 섹션)
```

### 4-2. 저장소에 커밋

```bash
cp marked.min.js static/vendor/marked.min.js
# 헤더 주석에 버전이 명시되는지 확인 (없으면 수동 주석 추가)
head -c 300 static/vendor/marked.min.js
git add static/vendor/marked.min.js
git commit -m "chore: marked v4.3.0 → v4.3.1"
# 본 가이드(§2 표)의 버전·마지막 확인일도 함께 갱신
```

### 4-3. 스모크 테스트

`static/embedded.html` 에서 업데이트 대상이 쓰이는 흐름을 눈으로 확인:

| 라이브러리 | 확인 경로 |
|---|---|
| marked | 챗 응답 영역에 markdown 렌더 (볼드·리스트·인라인 코드) |
| highlight.js | SQL/Python 코드 블록에 구문 강조가 적용되는지 |
| sql-formatter | "SQL 정렬" 버튼 클릭 시 들여쓰기 결과 |
| html2canvas | (현재 미참조) — 참조 시점에 차트 PNG 다운로드 동작 확인 |
| Pretendard | 한글 폰트가 웹 기본 돋움/굴림으로 깨지지 않는지 |

### 4-4. 폐쇄망 반영

저장소 커밋 → `deploy/offline-bundle/build.sh` 로 번들 재생성 → 매체 반입 → `install.sh` 로
`/opt/bdp/data-copilot/static/vendor/` 덮어쓰기. FastAPI 가 파일 변경을 바로 반영하므로 재기동 불필요.

(static 만 변경된 경우 전체 번들 대신 `static/vendor/` 만 별도 tar 로 반입해도 무방.)

---

## 5. 주의 사항

### 5-1. hljs 언어 모듈은 `highlight.min.js` 와 버전 일치 필수

`core.min.js` + `python.min.js` 조합처럼 모듈을 분리해 쓸 경우 **모든 파일을 같은 버전으로** 유지하세요.
버전 불일치 시 로딩은 되지만 특정 언어에서 정규식 엔진 호환성 에러가 발생할 수 있습니다.

### 5-2. marked v5+ 는 breaking

현재 v4 API (`marked.parse()`, `marked.setOptions()`) 에 맞춰진 코드가 `embedded.html` L3097-3108 에 있습니다.
v5 로 넘기려면 `marked.use()` + 새 `renderer` API 로 재작성 필요. v4 보안 패치가 유지되는 동안은 v4 고정 권장.

### 5-3. html2canvas 미참조 자산 정리

현재 `embedded.html` 에서 `html2canvas` 스크립트 태그 참조가 없습니다 (PNG 다운로드 코드 L3038 는
`html2canvas` 전역 객체를 가정하지만 로드되지 않음). 다음 중 한 방향으로 정리 필요:

- **기능 복구**: `embedded.html` 헤더에 `<script src="/vendor/html2canvas.min.js"></script>` 추가
- **자산 제거**: 사용하지 않는다면 `static/vendor/html2canvas.min.js` (~200KB) 삭제하여 번들 축소

결정되기 전까지는 현 상태 유지 (라이브러리는 반입하되 참조 안 됨).

### 5-4. Pretendard 웹폰트 woff2 파일

`static/vendor/fonts/woff2/` 아래 개별 weight 파일들을 폐쇄망 반입 시 누락하지 않도록 주의.
`static/vendor/fonts/pretendard.css` 가 상대경로로 참조하므로 디렉토리 구조 그대로 유지해야 합니다.

---

## 6. 참고 문서

- [closed-network-runbook.md](closed-network-runbook.md) §6 설치, §12-4 프론트엔드 스모크
- [../../static/embedded.html](../../static/embedded.html) — 단일 파일 프론트엔드 실체
- [../../deploy/offline-bundle/build.sh](../../deploy/offline-bundle/build.sh) — 오프라인 번들 빌드 (static 포함)
