---
name: ui-dead-code-cleanup
description: |
  UI 코드에서 사용되지 않는 import, 변수, 함수, 주석 처리된 코드를 제거합니다.
  프론트엔드 코드 정리, 리팩토링 후 사용하세요.
user_invocable: true
---
# 역할

UI 불필요 코드 제거 전문가. 동작에 기여하지 않는 코드를 식별하고 안전하게 제거한다.

# 사용법

```
/ui-dead-code-cleanup                     # 프론트엔드 전체 정리
/ui-dead-code-cleanup src/components/     # 특정 디렉토리 정리
/ui-dead-code-cleanup src/pages/Home.tsx  # 특정 파일 정리
/ui-dead-code-cleanup --check-only        # 수정 없이 리스트업만
```

# 실행 절차

1. **미사용 import 스캔**:
   - 모든 import문을 스캔하고, 파일 내에서 실제 참조되는지 확인한다.
   - 참조되지 않는 import는 제거한다.
   - 타입 전용 import(`import type`)도 동일하게 검사한다.

2. **미사용 선언 스캔**:
   - 선언되었지만 사용되지 않는 변수, 함수, 타입, 인터페이스를 식별한다.
   - export되지 않고 파일 내에서 참조되지 않으면 제거한다.
   - export된 항목은 프로젝트 전체에서 import되는지 Grep으로 확인 후 제거한다.

3. **주석 처리된 코드 블록**:
   - 3줄 이상의 주석 처리된 코드 블록을 식별한다.
   - git history에서 복원 가능하므로 제거한다.
   - 설명 주석(코드가 아닌 자연어 설명)은 유지한다.

4. **도달 불가 코드**:
   - early return 이후의 코드를 식별하고 제거한다.
   - 항상 false인 조건문 내부 코드를 식별하고 제거한다.

5. **`--check-only` 모드**: 수정하지 않고 제거 후보 목록만 출력한다.

# 산출물 형식

```markdown
## Dead Code Cleanup 결과

### 제거 항목: N건

| 유형 | file | line | 항목명 | 비고 |
|------|------|------|--------|------|
| unused import | ... | ... | React | |
| unused variable | ... | ... | tempData | |
| commented code | ... | 42-58 | (코드 블록) | git 복원 가능 |
```

# 수행하지 않는 것

- 기능 변경/추가
- 사용 중인 코드의 리팩토링
- 테스트 파일의 정리 (테스트는 명시적이어야 하므로 별도 판단 필요)
