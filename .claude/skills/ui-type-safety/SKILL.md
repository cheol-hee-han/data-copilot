---
name: ui-type-safety
description: |
  TypeScript UI 코드의 any 타입 제거, 누락된 타입 정의 추가, API 응답 타입 강화를 수행합니다.
  타입 안전성 강화, TypeScript strict 모드 전환 시 사용하세요.
user_invocable: true
---
# 역할

TypeScript 타입 안전성 전문가. any 타입을 제거하고, 누락된 타입을 추가하며,
외부 데이터의 런타임 검증을 강화한다.

# 사용법

```
/ui-type-safety                     # 프론트엔드 전체 검토 + 수정
/ui-type-safety src/components/     # 특정 디렉토리
/ui-type-safety --check-only        # 수정 없이 리스트업만
```

# 실행 절차

1. **any 타입 스캔 및 교체**:
   - 프로젝트 내 모든 명시적 `any` 사용을 찾는다.
   - 각 any에 대해:
     a. 실제 타입을 추론할 수 있으면 → 구체 타입으로 교체
     b. 추론 불가능하면 → `unknown`으로 교체 + 필요한 타입 가드 추가
     c. 외부 라이브러리 타입 한계로 불가피한 경우 → `// eslint-disable-next-line @typescript-eslint/no-explicit-any` + 사유 주석

2. **API 응답 타입 확인**:
   - fetch/axios/기타 HTTP 클라이언트 호출을 찾는다.
   - 응답이 타입 지정 없이 사용되는 경우:
     a. 응답 구조에 맞는 interface를 정의한다
     b. 제네릭 파라미터 또는 타입 단언으로 적용한다
   - 외부 데이터를 런타임 검증 없이 신뢰하는 경우:
     a. zod/valibot 스키마 또는 타입 가드 함수 추가를 제안한다

3. **컴포넌트 Props 타입 확인**:
   - Props 타입이 정의되지 않은 컴포넌트 → interface 정의
   - optional(?) prop에 기본값 없이 사용 → 기본값 추가 또는 undefined 처리 코드 추가
   - children prop의 타입이 `any`인 경우 → `React.ReactNode`로 교체

4. **검증**: 수정 후 `tsc --noEmit`을 실행하여 타입 에러가 없는지 확인한다.

# 산출물 형식

```markdown
## 타입 안전성 결과

### 수정 항목

| file | line | before | after | 비고 |
|------|------|--------|-------|------|
| ... | ... | `any` | `UserResponse` | API 응답 타입 |
| ... | ... | `props: any` | `props: CardProps` | Props 인터페이스 신규 정의 |

### 새로 정의한 타입
- `UserResponse` (src/types/api.ts:15)
- `CardProps` (src/components/Card.tsx:3)

### tsc 검증: PASS / FAIL (실패 시 상세 내역)
```

# 수행하지 않는 것

- JavaScript 프로젝트에서 TypeScript 전환 (별도 마이그레이션 작업)
- 과도한 제네릭 추상화 (실제 필요한 수준으로만)
- 타입만을 위한 래퍼 함수/클래스 생성
- 테스트 파일의 타입 강화 (테스트에서는 유연성 우선)
