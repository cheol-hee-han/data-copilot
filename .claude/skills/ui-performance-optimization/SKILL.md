---
name: ui-performance-optimization
description: |
  UI 코드의 불필요한 리렌더링, 비효율적 데이터 처리, 번들 크기 이슈를 개선합니다.
  프론트엔드 성능 개선이 필요할 때 사용하세요.
user_invocable: true
---
# 역할

UI 성능 최적화 전문가. 불필요한 리렌더링과 비효율적 패턴을 식별하고
기존 동작을 유지하면서 개선한다.

# 사용법

```
/ui-performance-optimization                     # 프론트엔드 전체 검토 + 수정
/ui-performance-optimization src/components/     # 특정 디렉토리
/ui-performance-optimization --check-only        # 수정 없이 리스트업만
```

# 실행 절차

1. **리렌더링 원인 스캔**:

   a. 렌더 함수 내부에서 매번 새로 생성되는 객체/배열/함수가 있는가?
      - `const options = [...]`, `const handler = () => ...` 등이 컴포넌트 본문에 있는 경우
      - **수정**: useMemo/useCallback 적용 또는 컴포넌트 외부 상수로 이동
      - **판단**: deps가 없거나 변하지 않는 값만 참조하는 경우에만 적용

   b. 부모 상태 변경 시 불필요하게 리렌더링되는 자식 컴포넌트가 있는가?
      - 부모에서 자주 변하는 상태와 무관한 자식이 리렌더링되는 구조
      - **수정**: React.memo 래핑 또는 컴포넌트 분리 제안

2. **비효율적 데이터 처리 스캔**:

   a. 렌더링마다 동일 데이터를 filter/map/sort하는 경우
      - **수정**: useMemo로 캐싱 (deps에 원본 데이터 지정)

   b. 대용량 리스트(100건 이상)를 전체 렌더링하는 경우
      - **제안**: 가상화(react-window, react-virtuoso 등) 도입 제안
      - 직접 수정하지 않고 제안만 한다

3. **번들 크기 스캔**:

   a. 전체 라이브러리 import 후 일부만 사용하는 경우
      - 예: `import _ from 'lodash'` → `import { debounce } from 'lodash-es'`
      - **수정**: tree-shakeable import로 변경

   b. 초기 로드에 불필요한 대형 컴포넌트가 있는 경우
      - **제안**: `React.lazy()` + `Suspense` 도입 제안
      - 직접 수정하지 않고 제안만 한다

4. **안전성 검증**: 모든 수정 후 기존 동작이 바뀌지 않는지 확인한다.
   의심되면 수정하지 않고 제안으로 남긴다.

# 산출물 형식

```markdown
## 성능 최적화 결과

### 수정 항목

| file | line | 현재 패턴 | 개선 패턴 | 예상 효과 |
|------|------|-----------|-----------|-----------|
| ... | ... | inline object creation | useMemo | 불필요한 리렌더링 방지 |

### 제안 항목 (수동 적용 필요)

| file | line | 제안 | 이유 |
|------|------|------|------|
| ... | ... | lazy loading 도입 | 초기 번들 크기 절감 |
```

# 수행하지 않는 것

- 프로파일링 근거 없는 추측 최적화
- 기존 동작 변경 가능성이 있는 수정
- 과도한 메모이제이션 (deps가 자주 바뀌는 값이면 오히려 비효율)
- 컴포넌트 구조 변경 (→ `@ui-review` 에이전트)
