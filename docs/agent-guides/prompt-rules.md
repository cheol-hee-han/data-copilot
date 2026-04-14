# 에이전트 모듈 프롬프트 규칙

`.claude/skills/prompt-engineer/SKILL.md` 의 공통 규칙에 **추가로** 적용되는 에이전트
모듈 전용 규칙이다. 계획 수립형(`recovery_agent`, `sql_generator`)과 분석 판정형
(`context_interpreter`, `query_normalizer_phase1/phase2`, `sql_validator`, `analyzer`)
프롬프트를 작성·수정할 때 본 문서와 SKILL.md 를 함께 참고한다.

- 공통 구조/태그/금지 패턴/JSON 방어: SKILL.md
- 본 지침의 근거·기각된 대안·레퍼런스: `docs/todo/qwen35-prompting-skill-strategy.md`

---

## 1. 모듈 유형별 골격 순서

SKILL.md §4.3 의 유형별 권장 블록 순서를 기본으로 한다. 에이전트 모듈에서는 아래 2유형이
주로 사용된다.

### 1.1 계획 수립형 (recovery_agent, sql_generator 등)

```
[ROLE]
[MISSION]              ← sql_generator 등 복잡 생성형 선택
[HARD_CONSTRAINTS]     ← 위반 시 즉시 fail
[HALLUCINATION_GUARD]
[RULES]                ← 판단 기준, 재계획 전략
[TOOLS]                ← 사용 가능한 도구 명세 (recovery_agent)
[EXAMPLES]
[OUTPUT_CONTRACT]
[CONTEXT]              ← 가변 데이터. [TASK] 직전 배치
[TASK]                 ← reasoning_summary 재명시, 마지막
```

### 1.2 분석 판정형 (context_interpreter, sql_validator 등)

```
[ROLE]
[RULES]                ← 판정 기준, 교차 참조 지침, 도구 타입별 분기
[HALLUCINATION_GUARD]
[EXAMPLES]
[OUTPUT_CONTRACT]
[CONTEXT]              ← context_interpreter 는 §4 내부 배치 규칙 적용
[TASK]                 ← 마지막, 핵심 규칙 재강조
```

**공통 원칙**:

- `[TASK]`는 **반드시 마지막**. 중간 배치 시 모델이 예시·규칙을 참고하지 않고 조기 응답한다.
- 가변 `[CONTEXT]`는 **`[TASK]` 바로 직전**. recency bias 활용.
- thinking ON 노드(`sql_generator`, `recovery_agent`, `context_interpreter`, `analyzer`)는 `reasoning_summary` 필드 필수 + `[TASK]` 말미 재명시.

---

## 2. `[ROLE]` 작성 3요소

역할 선언은 반드시 3가지를 포함한다.

- **이 모듈의 정체**: "너는 X 모듈이다"
- **이 모듈이 하는 것**: 핵심 동작 1문장
- **이 모듈이 하지 않는 것**: 혼동 방지를 위한 명시적 제외

```
[ROLE]
너는 SQL 생성 에이전트의 recovery 모듈이다.
현재 상태를 분석하고 탐색 계획(execution_plan)을 수립한다.
도구를 직접 실행하지 않는다. 계획 수립만 담당한다.
```

**"하지 않는 것"을 명시하지 않으면 모델이 역할 경계를 침범한다.** 예: recovery가 직접
SQL을 작성하거나, `context_interpreter`가 재계획을 수립하는 등.

---

## 3. `[CONTEXT]` 변수 주입 규칙 (에이전트 전용 보강)

SKILL.md §10 의 공통 규칙에 더해 에이전트 모듈은 아래를 적용한다.

### 3.1 플레이스홀더 순서

모델이 추론할 때 참고하는 순서와 동일하게 배치한다.

```
원 질의 → 확인된 것 → 미확인된 것 → 도구 결과 → 이력 → 요약
```

### 3.2 분석 판정형 전용 — 도구 실행 결과 형식 명시

`{tool_results}` 같은 반복 블록 플레이스홀더는 **형식 명세**를 바로 아래에 붙인다.

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

### 3.3 선택지 설명 분리

변수가 여러 경우의 수일 때, 선택지 설명을 플레이스홀더와 같은 블록에 두지 않는다.
`[RULES]`에 별도 `## 진입 경로 해석` 같은 섹션으로 기술한다. (SKILL.md §10.4 참고)

---

## 4. `context_interpreter` long context 내부 배치

가장 긴 컨텍스트를 받는 노드. lost-in-the-middle 방어를 위해 `[CONTEXT]` **내부** 순서를
다음과 같이 강제한다.

```
[CONTEXT]
## 원 질의        ← 가장 짧고 중요
## 미해결 항목     ← 짧고 판단 분기의 기준
## 도구 실행 결과  ← 가장 길고 중간 위치 불가피, 관련도 상위 N개로 트리밍
## 이전 지식 상태  ← 갱신 대상
```

- 가장 중요한 규칙은 `[RULES]` 최상단 + `[TASK]` 말미 양쪽에 배치
- 중간의 도구 실행 결과는 `context_retriever` 노드가 상위 N개로 트리밍한 뒤 전달

---

## 5. `[RULES]` 판단 기준 기술 규칙

### 5.1 교차 참조 지시 기술

여러 스텝 결과를 연결해서 해석해야 할 때 명시적으로 기술한다.

```
- 활용사례(search_use_cases)에서 확인된 조인 구조를 테이블 메타 해석에 반영한다
- 활용사례 SQL에서 사용된 테이블이 메타에서도 확인되면 confidence를 한 단계 높인다
```

교차 참조를 명시하지 않으면 모델이 스텝 간 연결을 놓치고 낮은 confidence를 유지하는
경향이 생긴다.

### 5.2 도구 타입별 출력 분기 (분석 판정형 전용)

도구 타입에 따라 출력해야 할 필드가 달라질 때 명시한다.

```
탐색 도구 (search_*, lookup_*):
- insight, knowledge_updates, explored_* 배열 모두 출력

관찰 도구 (get_*, read_*):
- insight, knowledge_updates만 출력
- explored_* 배열 출력하지 않음
```

이 분기를 명시하지 않으면 모델이 관찰 도구 스텝에서도 판정 배열을 출력하거나 누락하는
오류가 발생한다.

### 5.3 판정 기준 엔티티 유형별 분리 (분석 판정형 전용)

SELECTED/REJECTED 판정이 필요한 엔티티 유형별로 **독립적으로** 기술한다. 여러 엔티티의
판정 기준을 하나의 섹션에 섞으면 모델이 어느 기준을 어느 엔티티에 적용해야 할지 혼동한다.

```
### 용어사전 판정 기준
- SELECTED: SQL 변환(집계 방식, 필터 조건, 산출식)에 구체적 힌트를 제공하는 경우
- REJECTED: 일반적 업무 설명에 그쳐 SQL 변환에 영향을 주지 않는 경우
- reason 기재: "어떤 SQL 변환 힌트를 제공하는지" 또는 "SQL 변환과 무관한 이유"

### 테이블 메타 판정 기준
- SELECTED: 질의의 measure/dimension이 해당 테이블의 컬럼과 직접 매칭되는 경우
- REJECTED: 주제영역이 일치하지만 컬럼이 매칭되지 않는 경우
- reason 기재: "어떤 컬럼이 어떤 measure/dimension에 매핑되는지"

### 활용사례 판정 기준
- SELECTED: 집계 방식, JOIN 구조, WHERE 패턴 중 하나 이상이 재사용 가능한 경우
- REJECTED: 주제는 유사하나 구조가 완전히 다른 경우
- reason 기재: "재사용 가능한 구조 요소"
```

---

## 6. `[TOOLS]` 도구 명세 규칙 (계획 수립형 전용)

### 6.1 그룹핑 원칙

도구를 기능 성격으로 그룹화한다. **번호 목록을 쓰지 않는다** (번호는 우선순위 순서로
오해를 유발).

```
지정 조회 도구 (이름/키를 알고 있을 때):
- lookup_table_meta(table_name): 영문 테이블명으로 단일 테이블 메타 조회 (page 미지원)
- lookup_code_meta(column_name): 코드 컬럼명으로 코드값 매핑 조회 (page 지원)

탐색 도구 (키워드/의미 기반 탐색):
- search_table_meta(query, page=N): 업무 키워드로 테이블/컬럼 메타 검색
- search_use_cases(query, page=N): 과거 유사 SQL 벡터 검색
```

각 그룹 아래에 "언제 이 그룹을 쓰는가"를 한 줄로 명시한다.

### 6.2 도구 기술 형식

```
도구명(파라미터): 한 줄 설명 (page 지원 여부)
```

### 6.3 도구 input 형식 규칙

```
단일 파라미터: "TB_ADW_LNB301M"
복수 파라미터: "테이블명,컬럼명,키워드" (쉼표 구분 명시)
page 포함: "검색어, page=N"
```

### 6.4 페이징 규칙

- `page` 지원/미지원 도구를 명확히 구분
- `page=3` 이상은 효율이 낮으므로 키워드 변경을 권장한다는 원칙 명시
- 동일 키워드로 이미 조회한 페이지를 반복 조회하지 않는다

---

## 7. 모듈 간 데이터 전달 정합성

에이전트 루프에서 한 모듈의 `[OUTPUT_CONTRACT]` 형식이 다음 모듈 플레이스홀더와
**정확히 동일**해야 한다.

```
이 모듈 [OUTPUT_CONTRACT]:
  "dead_ends": [{"type": "TERM_UNRESOLVABLE", "lesson": "..."}]

다음 모듈 플레이스홀더 형식 명시:
  {dead_ends_summary} 는 아래 형식으로 제공된다:
  - [TYPE] 설명
    교훈: ...
```

형식 불일치 시 다음 모듈이 데이터를 잘못 파싱한다. 프롬프트 수정 시 **업스트림/다운스트림
양쪽을 동시에 점검**한다.

현재 파이프라인 데이터 흐름:

```
query_normalizer → context_interpreter → sql_generator → sql_validator → analyzer
                     ↓                      ↓
                  recovery_agent ←─────────┘
```

프롬프트 수정 시 영향 범위 점검 체크리스트:

- 이 모듈 `[OUTPUT_CONTRACT]` 변경 → 다음 모듈 `[CONTEXT]` 플레이스홀더 형식 갱신
- 이 모듈 `[CONTEXT]` 필드명 변경 → 이전 모듈 `[OUTPUT_CONTRACT]` 필드명 갱신
- `reasoning_summary` 추가/제거 → 해당 노드 코드의 응답 파서 + 교차 검증 로직 동시 수정

---

## 8. thinking ON 노드 전용 체크리스트

`sql_generator`, `recovery_agent`, `context_interpreter`, `analyzer` 프롬프트를 작성·수정할
때 아래를 모두 만족하는지 확인한다.

- [ ] `[OUTPUT_CONTRACT]`에 `reasoning_summary: string` 필드 선언
- [ ] `reasoning_summary` 작성 지침 명시 ("어떤 테이블/컬럼/도구 결과를 썼는지 + 어떤 판단")
- [ ] `[TASK]` 말미에 "최종 출력은 reasoning_summary 포함 JSON 객체 하나" 재명시
- [ ] `[HALLUCINATION_GUARD]` 블록 존재, 위반/대응 쌍 제시
- [ ] `[CONTEXT]`가 `[TASK]` 바로 직전에 배치
- [ ] 프롬프트 본문에 thinking/샘플링 관련 지시 없음 (인프라 계층)
- [ ] 노드 코드가 `mode="on"`으로 `_resolve_thinking_params` 호출
- [ ] 노드 코드에 `reasoning_summary` 수신 + 교차 검증 로직 구현

---

## 9. Few-shot 설계 (에이전트 전용 보강)

SKILL.md §8 의 공통 규칙에 더해, 에이전트 모듈 예시에는 아래 품질 기준을 적용한다.

### 9.1 계획 수립형 예시 JSON 품질

- `analysis`: 왜 실패했는지 원인을 **구체적으로** 기술. 추상적 서술 금지. 예: "코드값 부족" → "LN_STCD 코드값 매핑 없음 — WHERE 조건에 대출상태 필터 필요"
- `lessons_learned`: 이 케이스에서만 배울 수 있는 교훈. 일반론 금지
- `execution_plan`: 실제 실행 가능한 도구 조합만 포함
- `depends_on`: 선행 스텝 결과가 필요한 경우에만 스텝 번호 지정, 독립 실행이면 null

### 9.2 분석 판정형 예시 JSON 품질

- `insight`: 관찰 내용을 한 문장으로 요약, 판단 포함
- `knowledge_updates`: key는 `unresolved_items`의 key를 그대로 사용
- `confidence`: 조건-결과 매핑 기준에 맞는 값
- `reason`: 판정 사유를 구체적으로 (SELECTED/REJECTED 근거 명시)
- 관찰 도구 스텝 예시에는 `explored_*` 배열을 포함하지 않는다

### 9.3 커버리지 매트릭스

SKILL.md §8.2 의 방법론에 따라, 새 예시 추가·교체 전 반드시 2차원 매트릭스를 먼저 그린다.
에이전트 모듈별 권장 축은 SKILL.md §8.2 의 "노드별 필수 커버리지 축" 표 참고.

---

## 10. 참조

- 공통 프롬프트 규칙: `.claude/skills/prompt-engineer/SKILL.md`
- 전략 문서 (근거·기각된 대안·레퍼런스): `docs/todo/qwen35-prompting-skill-strategy.md`
- 파이프라인 아키텍처: `docs/architecture/pipeline-architecture.md`
- 금융 도메인 규칙: `.claude/rules/financial-domain.md`
