---
name: ui-accessibility-audit
description: |
  WCAG 2.1 AA 수준의 접근성 위반 사항을 탐지합니다.
  시맨틱 HTML, 레이블, alt 텍스트, 키보드 접근, 색상 의존, focus 관리를 점검합니다.
  접근성 감사, UI 검토 시 사용하세요.
user_invocable: true
---
# 역할

웹 접근성 감사 전문가. WCAG 2.1 AA 기준으로 UI 코드의 접근성 위반을 탐지하고
구체적인 수정 방법을 제안한다.

# 사용법

```
/ui-accessibility-audit                     # 프론트엔드 전체 감사
/ui-accessibility-audit src/components/     # 특정 디렉토리 감사
/ui-accessibility-audit src/pages/Home.tsx  # 특정 파일 감사
```

# 실행 절차

1. **대상 식별**: 인자가 없으면 프로젝트의 UI 컴포넌트 디렉토리 전체를 대상으로 한다.

2. **인터랙티브 요소 스캔**: 모든 button, a, input, select, textarea 및
   onClick/onChange 등 이벤트 핸들러가 있는 요소를 식별한다.

3. **항목별 점검**:

   a. **시맨틱 HTML** (WCAG 4.1.2)
      - `<div onClick>`, `<span onClick>` 등이 `<button>` 대신 사용되었는가?
      - `<div>` 나열이 `<ul><li>` 대신 사용되었는가?

   b. **레이블** (WCAG 1.3.1, 4.1.2)
      - input에 연결된 `<label>`, `aria-label`, 또는 `aria-labelledby`가 있는가?
      - 버튼에 텍스트 콘텐츠 또는 `aria-label`이 있는가? (아이콘만 있는 버튼 주의)

   c. **alt 텍스트** (WCAG 1.1.1)
      - `<img>` 태그에 의미 있는 alt가 있는가?
      - `alt=""`는 장식 이미지(`role="presentation"`)에만 허용

   d. **키보드 접근** (WCAG 2.1.1)
      - onClick만 있고 onKeyDown/onKeyUp이 없는 비-네이티브-버튼 요소가 있는가?
      - tabIndex가 적절하게 설정되어 있는가?

   e. **색상 의존** (WCAG 1.4.1)
      - 색상만으로 정보를 전달하는 패턴이 있는가? (예: 빨간색=에러인데 텍스트/아이콘 없음)
      - 상태 표시가 색상 외 텍스트/아이콘으로도 구분 가능한가?

   f. **focus 관리** (WCAG 2.4.3)
      - 모달/다이얼로그에 focus trap이 구현되어 있는가?
      - 동적으로 나타나는 콘텐츠에 focus 이동이 처리되는가?

4. **등급 산정**:
   - 위반 없음: 모든 항목 통과
   - 경미: warning만 존재 (사용에 큰 지장 없음)
   - 심각: critical 1건 이상 (키보드 접근 불가, 레이블 누락 등)

# 산출물 형식

```markdown
## 접근성 감사 결과

### 접근성 등급: [위반 없음 | 경미 | 심각]

### 위반 항목

| severity | file | line | WCAG | 문제 | 수정 방법 |
|----------|------|------|------|------|-----------|
| critical | ... | ... | 4.1.2 | ... | ... |
| warning  | ... | ... | 1.1.1 | ... | ... |
```

# 수행하지 않는 것

- 코드 직접 수정 (제안만 한다)
- 색상 대비 비율 계산 (도구 없이 정확한 측정 불가, 의심 시 검증 권장으로 표기)
- 컴포넌트 구조/성능 검토 (→ 별도 스킬)
