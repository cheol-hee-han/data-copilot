# 에이전트 모듈 프롬프트 규칙

SKILL.md의 기본 규칙에 추가로 적용되는 에이전트 전용 규칙이다.

---

## 모듈 유형별 골격 순서

### 계획 수립형 (recovery, strategy planner, routing 등)

```
[ROLE]
[CONTEXT]
[RULES]      ← 진입 경로 해석, 판단 기준
[TOOLS]      ← 사용 가능한 도구 명세
[EXAMPLES]
[OUTPUT_CONTRACT]
[TASK]       ← 반드시 마지막
```

### 분석 판정형 (context interpreter, result analyzer 등)

```
[ROLE]
[CONTEXT]
[RULES]      ← 판정 기준, 교차 참조 지침, 도구 타입별 출력 분기
[EXAMPLES]
[OUTPUT_CONTRACT]
[TASK]       ← 반드시 마지막
```

[TASK]가 중간에 오면 모델이 예시와 규칙을 참고하지 않고
조기에 응답을 생성하려는 경향이 생긴다.

---

## [ROLE] 작성 규칙

역할 선언은 3가지를 반드시 포함한다.

- 이 모듈의 정체: "너는 X 모듈이다"
- 이 모듈이 하는 것: 핵심 동작 1문장
- 이 모듈이 하지 않는 것: 혼동 방지를 위한 명시적 제외

```
[ROLE]
너는 SQL 생성 에이전트의 recovery 모듈이다.
현재 상태를 분석하고 탐색 계획(execution_plan)을 수립한다.
도구를 직접 실행하지 않는다. 계획 수립만 담당한다.
```

하지 않는 것을 명시하지 않으면 모델이 역할 경계를 침범한다.

---

## [CONTEXT] 변수 주입 규칙

### 선택지 설명 분리

변수 값이 여러 경우의 수를 가질 때
선택지 설명을 플레이스홀더와 같은 블록에 두지 않는다.
선택지 설명은 [RULES] 섹션 안에 별도로 기술한다.

잘못된 방식:
```
[CONTEXT]
진입 경로: {entry_source}
  - readiness_gate: 초기 탐색이 불충분합니다
  - sql_validator: SQL 검증이 실패했습니다
```

올바른 방식:
```
[CONTEXT]
진입 경로: {entry_source}
확인된 지식: {confirmed_knowledge}
미확인 항목: {unresolved_items}

## 진입 경로 해석 규칙  ← [RULES] 섹션 안에 기술
### readiness_gate
초기 탐색이 불충분하여 추가 탐색이 필요하다.
```

### 플레이스홀더 순서 원칙

모델이 추론할 때 참고하는 순서와 동일하게 배치한다.

```
진입 경로 → 확인된 것 → 미확인된 것 → 이력 → 요약
```

### 분석 판정형 전용

도구 실행 결과 블록의 형식을 [CONTEXT] 바로 아래에 명시한다.

```
[CONTEXT]
현재 질의: {original_query}
미해소 지식 항목: {unresolved_items}
도구 실행 결과: {tool_results}

도구 실행 결과는 아래 형식으로 제공된다:
### [Step N] tool_name(input)
목적: ...
결과: ...
---
```

---

## [RULES] 판단 기준 기술 규칙

### 교차 참조 지시 기술 방식

여러 스텝 결과를 연결해서 해석해야 할 때 명시적으로 기술한다.

```
- 활용사례(search_use_cases)에서 확인된 조인 구조를 테이블 메타 해석에 반영한다
- 활용사례 SQL에서 사용된 테이블이 메타에서도 확인되면 confidence를 한 단계 높인다
```

교차 참조를 명시하지 않으면 모델이 스텝 간 연결을 놓치고
낮은 confidence를 유지하는 경향이 생긴다.

### 도구 타입별 출력 분기 규칙 (분석 판정형 전용)

도구 타입에 따라 출력해야 할 필드가 달라질 때 명시한다.

```
탐색 도구 (search_*, lookup_*):
- insight, knowledge_updates, explored_* 배열 모두 출력

관찰 도구 (get_*):
- insight, knowledge_updates만 출력
- explored_* 배열 출력하지 않음
```

이 분기를 명시하지 않으면 모델이 관찰 도구 스텝에서도
판정 배열을 출력하거나 누락하는 오류가 발생한다.

### 판정 기준 기술 방식 (분석 판정형 전용)

SELECTED/REJECTED 판정이 필요한 엔티티 유형별로 독립적으로 기술한다.

```
### 용어사전 판정 기준
- SELECTED: SQL 변환(집계 방식, 필터 조건, 산출식)에 구체적인 힌트를 제공하는 경우
- REJECTED: 일반적 업무 설명에 그쳐 SQL 변환에 영향을 주지 않는 경우
- reason 기재: "어떤 SQL 변환 힌트를 제공하는지" 또는 "SQL 변환과 무관한 이유"
```

판정 기준을 하나의 섹션에 섞으면 모델이 어느 기준을 어느 엔티티에
적용해야 할지 혼동한다.

---

## [TOOLS] 도구 명세 규칙 (계획 수립형 전용)

### 그룹핑 원칙

도구를 기능 성격으로 그룹화한다.
번호 목록을 쓰지 않는다. 번호는 우선순위 순서로 오해를 유발한다.

```
지정 조회 도구 (이름/키를 알고 있을 때):
- lookup_table_meta(table_name): 영문 테이블명으로 단일 테이블 메타 조회 (page 미지원)
- lookup_code_meta(column_name): 코드 컬럼명으로 코드값 매핑 조회 (page 지원)

탐색 도구 (키워드/의미 기반 탐색):
- search_table_meta(query, page=N): 업무 키워드로 테이블/컬럼 메타 검색
- search_use_cases(query, page=N): 과거 유사 SQL 벡터 검색
```

### 도구 기술 형식

```
도구명(파라미터): 한 줄 설명 (page 지원 여부)
```

각 그룹 아래에 "언제 이 그룹을 쓰는가"를 한 줄로 명시한다.

### 도구 input 형식 규칙

```
단일 파라미터: "TB_ADW_LNB301M"
복수 파라미터: "테이블명,컬럼명,키워드" (쉼표 구분 명시)
page 포함: "검색어, page=N"
```

### 페이징 규칙

page 지원/미지원 도구를 명확히 구분한다.
page=3 이상은 효율이 낮으므로 키워드 변경을 권장한다는 원칙을 명시한다.

---

## [EXAMPLES] few-shot 설계 규칙

### 예시 수

3개를 기본으로 한다. 5개를 초과하지 않는다.

### 예시 선택 기준

- 가장 빈번한 케이스 1개
- 가장 헷갈리기 쉬운 케이스 1개
- 경계 케이스 (give_up, cold start, 조건부 출력 등) 1개

중복 패턴의 예시는 제거한다.

### 예시 내부 구조

```
### 예시 N: 상황 제목 (진입 경로 또는 케이스 유형)
상황: 한 줄 요약
- 핵심 상태 정보만 나열

{JSON 출력 예시}
```

### 예시 JSON 품질 기준 — 계획 수립형

- analysis: 왜 실패했는지 원인을 구체적으로 기술 (추상적 서술 금지)
- lessons_learned: 이 케이스에서만 배울 수 있는 교훈 (일반론 금지)
- execution_plan: 실제로 실행 가능한 도구 조합만 포함
- depends_on: 선행 스텝 결과가 필요한 경우에만 스텝 번호 지정, 독립 실행이면 null

### 예시 JSON 품질 기준 — 분석 판정형

- insight: 관찰 내용을 한 문장으로 요약 (판단 포함)
- knowledge_updates: key는 unresolved_items의 key를 그대로 사용
- confidence: 조건-결과 매핑 기준에 맞는 값 사용
- reason: 판정 사유를 구체적으로 기술 (SELECTED/REJECTED 근거 명시)
- 관찰 도구 스텝 예시에는 explored_* 배열을 포함하지 않는다
