# Data Copilot — 점진적 탐색 기반 NL-to-SQL 에이전틱 루프 설계 전략

> 본 문서는 자연어 입력을 SQL로 변환하기 위한 반복 추론형 에이전틱 루프의 최종 설계 전략을 기술합니다.
> Claude Code를 통한 구현의 참고 문서로 작성되었습니다.

---

## 목차

1. 시스템 철학
2. 주요 설계 아이디어 (핵심 원칙)
3. 비판적 검토 및 개선 방향
4. State 설계
5. 그래프 스켈레톤
6. 노드별 상세 설계
7. Confidence Score 설계
8. 루프 제어 및 탈출 조건
9. 프롬프트 설계 지침
10. 구현 우선순위 및 체크리스트

- 부록 — 도구 우선순위 및 비용 기준
- 참고사항 1 — sqlglot 기반 SQL 파싱을 통한 LLM 비용 절감 전략

---

## 1. 시스템 철학

이 시스템의 본질은 하나입니다.

> **"모른다는 것을 명시적으로 관리하고, 아는 것이 늘어날수록 확신이 올라가는 구조"**

이를 구현한다는 것은:
- **불확실성을 상태로 표현**할 수 있어야 하고
- **확신이 쌓이는 과정을 측정**할 수 있어야 하고
- **언제 행동할지를 확신 수준으로 결정**할 수 있어야 합니다

사람이 데이터를 탐색할 때의 접근 방식을 그대로 에이전트에 이식합니다.

```
사람의 접근 방식:
  ① 비슷한 쿼리 본 적 있나? (기억 검색)
  ② 그 구조 빌려서 내 상황에 맞게 수정
  ③ 모르는 테이블이면 구조 확인
  ④ 실제 값 확인 (샘플)
  ⑤ SQL 조립 시도
  ⑥ 안 되면 접근 방식 자체를 바꿈
```

---

## 2. 주요 설계 아이디어 (핵심 원칙)

> ⚠️ 이 섹션의 내용은 구현 전반에 걸쳐 반드시 유지되어야 하는 설계 원칙입니다.

### 원칙 1 — 불확실성을 명시적 상태로 관리

불확실한 용어, 테이블, 조건은 모두 명시적인 상태값으로 관리합니다.
"아마 맞겠지"로 진행하는 것을 시스템이 허용하지 않아야 합니다.

```
UNRESOLVED  (0.0~0.3) → 아무것도 모름
CANDIDATE   (0.3~0.6) → 메타/사례에서 후보 발견, 미검증
PROBABLE    (0.6~0.8) → 논리적 추론으로 그럴 것 같음
CONFIRMED   (0.8~1.0) → 샘플 또는 실행으로 실제 확인
```

### 원칙 2 — 탐색은 내부 루프, 판단은 외부 노드

스텝 하나마다 LangGraph 노드 전이를 발생시키지 않습니다.
탐색(explore)은 노드 내부에서 루프로 처리하고,
"다음에 무엇을 할 것인가"라는 판단만 외부 노드(assess_confidence)로 분리합니다.

```python
# ❌ 잘못된 구조: 스텝마다 노드 전이
execute_step → update_knowledge → assess → execute_step → ...

# ✅ 올바른 구조: 탐색은 내부 루프
explore(내부에서 스텝 순차 실행) → assess_confidence → 다음 행동 결정
```

### 원칙 3 — State는 최소화, 요약만 보관

State에는 다음 판단에 필요한 최소한의 정보만 담습니다.
원본 데이터(샘플 결과 전체, 테이블 전체 스키마 등)는 외부 캐시에 보관하고
State에는 관찰 결과 요약문과 참조 키만 저장합니다.

```python
# ❌ State에 넣으면 안 됨
raw_sample_data: list[dict]     # 샘플 데이터 전체
full_table_schema: dict         # 테이블 전체 스키마

# ✅ State에는 요약만
sample_insights: list[str]      # "order_status에 CANCEL 존재 확인"
relevant_columns: list[str]     # 현재 질의에 필요한 컬럼만
```

### 원칙 4 — 프롬프트는 "무엇을 할지"가 아니라 "어떻게 생각할지"

프롬프트는 행동 지시가 아닌 사고방식을 지시합니다.
불확실한 것을 먼저 목록화하고, 자기검증 체크리스트를 포함시킵니다.

```
❌ "테이블 메타를 검색하고 SQL을 생성하세요"
✅ "무엇을 모르는지 먼저 목록화하고, 그것을 해소하기 위한 탐색 계획을 세우세요"
```

### 원칙 5 — 루프 탈출 조건을 다층으로 관리

단일 iteration 카운터가 아닌 의미 단위별 카운터를 별도로 관리합니다.

```python
total_tool_calls: int       # 도구 호출 총 횟수
replan_count: int           # 재계획 횟수
generate_attempts: int      # SQL 생성 시도 횟수
local_fix_count: int        # local fix 시도 횟수
```

### 원칙 6 — 실패를 유형별로 분류하여 처리

모든 실패를 동일하게 처리하면 local fix가 가능한 문제를 재탐색으로 보내거나,
구조적 실패를 local fix로 해결하려다 무한루프에 빠집니다.

```
syntax       → Layer1 FAIL  → SQL 즉시 재생성
semantic     → Layer2 FAIL local → SQL 재생성 (fix_instruction 포함)
structural   → Layer2 FAIL structural → 재계획 (replan)
empty_result → Layer3 결과 0건 → 조건/테이블 재검토
db_error     → Layer3 실행 오류 → 별도 처리
```

### 원칙 7 — plan과 replan은 반드시 별도 노드로 분리

최초 plan과 재계획 replan은 프롬프트와 컨텍스트가 완전히 다릅니다.
하나의 노드에서 if/else로 처리하면 LLM이 역할 혼선을 일으킵니다.

```
plan   → 최초 진입 전용: 질의 분해 + 전체 가설 수립
replan → 별도 노드: dead_ends 기반 가설 교체만 담당
```

---

## 3. 비판적 검토 및 개선 방향

초기 설계에서 식별된 문제점과 그 해결 방향입니다.

### 🔴 Critical — 즉시 수정 필요

#### 문제 1. plan/replan 노드 미분리
- **문제**: 최초 계획과 재계획이 같은 노드를 공유하면 LLM에 전달되는 컨텍스트와 지시가 혼재됨
- **해결**: `plan` (최초 진입)과 `replan` (재계획)을 완전히 별도 노드로 분리
- **추가**: replan 진입 시 dead_ends, explored_use_cases, confirmed_knowledge를 명시적으로 전달

#### 문제 2. validate_sql 실패 분류 불충분
- **문제**: Layer1 실패 처리 없음, Layer3만 실패 시 처리 없음, DB 오류 미처리
- **해결**: 실패 유형을 5단계로 세분화 (syntax / semantic_local / structural / empty_result / db_error)
- **추가**: `local_fix_count >= 2` 시 structural로 자동 격상

#### 문제 3. dataclass와 TypedDict 혼용
- **문제**: LangGraph State 직렬화/역직렬화 과정에서 dataclass 타입 정보 손실
- **해결**: 모든 서브 타입을 TypedDict 또는 Pydantic BaseModel로 통일

### 🟠 Warning — 설계 전 수정 필요

#### 문제 4. State 크기 미제어
- **문제**: 도구 결과 전체를 State에 보관하면 체크포인트 비용 폭증
- **해결**: 도구 결과는 외부 캐시(Redis 또는 메모리)에 보관, State에는 result_ref(키)만 저장

#### 문제 5. SQL 재생성 시 fix_instruction 누락
- **문제**: validate 실패 후 재생성 진입 시 "왜 재생성하는가"가 프롬프트에 전달되지 않아 동일 실수 반복
- **해결**: State에 `sql_fix_instruction: Optional[str]` 필드 추가, validate 노드가 실패 분석 문장을 생성하여 저장

#### 문제 6. next_action을 State에 저장하는 오염 문제
- **문제**: 이전 루프의 next_action이 State에 잔류하여 다음 루프에서 오작동 가능
- **해결**: next_action 필드 제거, 조건부 엣지 함수가 State를 직접 읽고 판단

### 🟡 Caution — 구현 중 수정 필요

#### 문제 7. update_knowledge의 LLM/Rule 경계 불명확
- **문제**: 어떤 경우에 LLM을 쓰고 Rule을 쓰는지 기준 없음
- **해결**: Rule-based 처리(용어 상태 갱신, 테이블 추가)와 LLM 처리(샘플 해석)를 명확히 분리

#### 문제 8. assess 노드의 동적 스텝 삽입 위험
- **문제**: assess에서 스텝을 동적 삽입 시 execution_plan이 실행 중 변형되고 무한루프 가능
- **해결**: 추가 탐색 필요 시 assess에서 직접 삽입하지 않고 replan으로 라우팅

#### 문제 9. Cold Start(활용사례 0건) 처리 경로 없음
- **문제**: 활용사례 검색 결과가 없으면 가설 수립 자체가 불가
- **해결**: fallback 가설 유형 추가 — 키워드 기반 직접 테이블 탐색 경로 별도 정의

### 🔵 v2 고려사항

#### 문제 10. 병렬 가설 탐색 미지원
- **현재**: 가설을 순서대로 하나씩 시도 → 레이턴시가 가설 수에 비례
- **개선 방향**: LangGraph Send API를 활용한 상위 N개 가설 병렬 탐색
- **초기 구현에서는 순차 처리로도 충분**

---

## 4. State 설계

### 4-1. 서브 타입 정의

```python
from typing import TypedDict, Literal, Optional

class KnowledgeItem(TypedDict):
    key: str                    # "취소 상태 표현"
    value: str                  # "order_status = 'CANCEL'"
    confidence: float           # 0.0 ~ 1.0
    status: Literal["UNRESOLVED", "CANDIDATE", "PROBABLE", "CONFIRMED", "CONFLICTED"]
    source: str                 # "코드메타" | "샘플데이터" | "활용사례" | "추론"
    evidence: list[str]         # ["SELECT DISTINCT 결과에서 CANCEL 확인"]

class Hypothesis(TypedDict):
    hypothesis_id: str          # "H1", "H2" ...
    description: str
    based_on_use_case: Optional[str]
    required_tables: list[str]
    missing_terms: list[str]
    priority: float             # 0.0 ~ 1.0
    strategy: str               # 접근 전략 한 줄 요약
    status: Literal["PENDING", "ACTIVE", "SUCCESS", "FAILED"]

class ExecutionStep(TypedDict):
    step: int
    tool: str                   # "search_use_cases" | "search_table_meta" | ...
    input: str                  # 검색어 또는 파라미터
    purpose: str                # 이 스텝이 필요한 이유
    expected_output: str        # 기대하는 결과
    status: Literal["PENDING", "DONE", "SKIPPED", "FAILED"]
    result_ref: Optional[str]   # 외부 캐시 키 (결과 원본은 캐시에 보관)
    insight: Optional[str]      # 결과로부터 추출한 핵심 관찰 요약

class CandidateTable(TypedDict):
    table_name: str
    role: str                   # "주문일자, 취소여부 보유"
    relevant_columns: list[str] # 현재 질의에 필요한 컬럼만
    join_keys: list[str]
    missing_coverage: list[str] # 이 테이블로 커버 못하는 요건
    confirmed: bool

class DeadEnd(TypedDict):
    hypothesis_id: str
    reason: str
    tried_tables: list[str]
    tried_terms: list[str]
    failure_type: Literal[
        "no_use_case",          # 활용사례 없음
        "no_table",             # 관련 테이블 없음
        "term_unresolvable",    # 용어 해소 불가
        "sql_syntax",           # SQL 문법 오류
        "sql_semantic_local",   # SQL 의미 오류 (수정 가능)
        "sql_structural",       # SQL 구조 오류 (테이블 재탐색 필요)
        "empty_result",         # 실행 결과 0건
        "db_error"              # DB 실행 오류
    ]

class LoopGuard(TypedDict):
    total_tool_calls: int       # 도구 호출 총 횟수 (MAX: 20)
    replan_count: int           # 재계획 횟수 (MAX: 3)
    generate_attempts: int      # SQL 생성 시도 횟수 (MAX: 4)
    local_fix_count: int        # local fix 시도 횟수 (MAX: 2, 초과 시 structural로 격상)

class SqlValidationResult(TypedDict):
    layer1_status: Literal["PASS", "FAIL", "SKIP"]
    layer2_status: Literal["PASS", "FAIL", "SKIP"]
    layer2_passed: list[str]    # 통과한 체크리스트 항목
    layer2_failed: list[str]    # 실패한 체크리스트 항목
    layer2_failure_type: Optional[Literal["semantic_local", "structural"]]
    layer3_status: Literal["PASS", "FAIL", "SKIP"]
    layer3_row_count: Optional[int]
    layer3_is_sane: Optional[bool]
    overall: Literal["SUCCESS", "FAIL_SYNTAX", "FAIL_SEMANTIC_LOCAL",
                     "FAIL_STRUCTURAL", "FAIL_EMPTY", "FAIL_DB_ERROR"]
```

### 4-2. 메인 State

```python
class AgentState(TypedDict):
    # ── 입력 ──────────────────────────────────────────
    original_query: str
    normalized_query: str
    intent: str

    # ── 현재 진행 상태 ─────────────────────────────────
    phase: Literal[
        "PLANNING", "EXPLORING", "VERIFYING",
        "GENERATING", "VALIDATING", "REPLANNING", "DONE"
    ]

    # ── 플래너 산출물 ──────────────────────────────────
    query_decomposition: dict               # measure / filters / group_by / order_limit
    hypotheses: list[Hypothesis]            # 전체 가설 큐 (우선순위 정렬)
    current_hypothesis: Optional[Hypothesis]
    execution_plan: list[ExecutionStep]     # 현재 가설의 스텝 목록
    current_step_index: int

    # ── 누적 지식 ─────────────────────────────────────
    knowledge_items: list[KnowledgeItem]    # 용어별 확신 수준
    explored_use_cases: list[dict]          # 탐색 완료된 사례 (중복 방지)
    candidate_tables: list[CandidateTable]
    confirmed_join_path: list[dict]         # 확인된 테이블 간 조인 경로
    searched_queries: list[str]             # 이미 검색한 쿼리 (중복 방지)
    sampled_tables: list[str]               # 이미 샘플 조회한 테이블 (중복 방지)

    # ── 실패 기록 ─────────────────────────────────────
    dead_ends: list[DeadEnd]

    # ── SQL ──────────────────────────────────────────
    generated_sql: Optional[str]
    sql_fix_instruction: Optional[str]      # 재생성 시 "무엇이 틀렸는가" 명시
    sql_validation_result: Optional[SqlValidationResult]

    # ── 루프 제어 ─────────────────────────────────────
    loop_guard: LoopGuard

    # ── 최종 출력 ─────────────────────────────────────
    final_answer: Optional[dict]
```

---

## 5. 그래프 스켈레톤

### 5-1. 전체 그래프 구조

```
[START]
   ↓
[plan]                    # 질의 분해 + 초기 가설 수립 + 실행계획 생성
   ↓
[explore]                 # 탐색 도구 호출 루프 (내부에서 스텝 순차 실행)
   ↓
[assess_confidence]       # 확신 수준 측정 → 다음 행동 결정 (rule-based, LLM 없음)
   ↓
┌─────────┬─────────────┬───────────┐
↓         ↓             ↓           ↓
[explore] [generate_sql] [replan]  [conclude]
(탐색 계속)(SQL 생성)    (재가설)   (종료)
              ↓
         [validate_sql]
              ↓
   ┌──────────┼──────────┬──────────┐
   ↓          ↓          ↓          ↓
[conclude] [generate]  [generate] [replan]
(성공)    (syntax fix) (local fix)(structural)
```

### 5-2. LangGraph 코드 스켈레톤

```python
from langgraph.graph import StateGraph, END

graph = StateGraph(AgentState)

# 노드 등록
graph.add_node("plan",              plan_node)
graph.add_node("explore",           explore_node)
graph.add_node("assess_confidence", assess_confidence_node)
graph.add_node("generate_sql",      generate_sql_node)
graph.add_node("validate_sql",      validate_sql_node)
graph.add_node("replan",            replan_node)
graph.add_node("conclude",          conclude_node)

# 엣지 연결
graph.set_entry_point("plan")
graph.add_edge("plan", "explore")
graph.add_edge("explore", "assess_confidence")

graph.add_conditional_edges(
    "assess_confidence",
    route_from_assess,
    {
        "explore":          "explore",
        "generate_sql":     "generate_sql",
        "replan":           "replan",
        "conclude_success": "conclude",
        "conclude_failure": "conclude",
    }
)

graph.add_edge("generate_sql", "validate_sql")

graph.add_conditional_edges(
    "validate_sql",
    route_from_validate,
    {
        "conclude_success":  "conclude",
        "fix_syntax":        "generate_sql",
        "fix_local":         "generate_sql",
        "replan":            "replan",
    }
)

graph.add_edge("replan", "explore")
graph.add_edge("conclude", END)
```

---

## 6. 노드별 상세 설계

### plan 노드

```
역할:
  최초 진입 전용 노드.
  질의 분해 + UNRESOLVED 용어 목록화 + 초기 가설 수립 + 실행계획 생성

입력 State:
  - normalized_query
  - intent

출력 State:
  - query_decomposition
  - knowledge_items (모두 UNRESOLVED 초기 상태)
  - hypotheses (우선순위 정렬된 큐)
  - current_hypothesis (priority 최상위)
  - execution_plan (current_hypothesis의 스텝들)
  - current_step_index = 0
  - phase = "PLANNING" → "EXPLORING"

LLM 호출: ✅ Heavy (플래너 system prompt 사용)

주의:
  - 활용사례 검색 없이 SQL 생성을 계획하면 안 됨
  - 모든 가설에 based_on_use_case 또는 fallback 전략 명시
  - Cold Start 대비 fallback 가설(키워드 기반 테이블 탐색) 반드시 포함
```

### explore 노드

```
역할:
  execution_plan의 스텝들을 내부 루프로 순차 실행.
  각 스텝 결과를 즉시 knowledge_items에 반영.
  조기 탈출 조건 충족 시 루프 종료.

입력 State:
  - execution_plan
  - current_step_index
  - knowledge_items
  - searched_queries (중복 방지용)
  - sampled_tables (중복 방지용)

출력 State:
  - execution_plan (각 step의 insight 업데이트)
  - knowledge_items (상태 갱신)
  - candidate_tables (추가/갱신)
  - confirmed_join_path (조인 경로 확인 시)
  - explored_use_cases (새 사례 추가)
  - searched_queries (새 검색어 추가)
  - sampled_tables (샘플 조회한 테이블 추가)
  - loop_guard.total_tool_calls (증가)
  - phase = "EXPLORING" | "VERIFYING"

LLM 호출: ✅ Light (샘플 데이터 관찰 시에만)
Rule-based: 메타 검색 결과 → knowledge_items 상태 갱신

도구 목록:
  search_use_cases(query)         → VectorDB 검색
  search_table_meta(table_name)   → 테이블/컬럼 메타
  search_code_meta(column_name)   → 코드값 목록
  search_glossary(term)           → 용어사전
  get_sample_data(table, columns) → 실제 DB 쿼리 (LIMIT 10)

중복 방지:
  - query가 searched_queries에 있으면 SKIPPED
  - table이 sampled_tables에 있으면 SKIPPED

조기 탈출 조건:
  - confidence_score >= THRESHOLD_GENERATE (0.75)
  - 모든 UNRESOLVED 용어가 CONFIRMED 도달 시
```

### assess_confidence 노드

```
역할:
  현재 누적 지식 상태를 보고 다음 행동을 결정.
  LLM 없이 rule-based로만 동작.

판단 로직:

  1. 강제 종료 조건 먼저 체크
     loop_guard 임계값 초과 → "conclude_failure"

  2. 탐색 스텝이 남아있는가?
     YES → "explore"

  3. confidence_score >= THRESHOLD_GENERATE(0.75) AND
     모든 critical_terms CONFIRMED?
     YES → "generate_sql"

  4. confidence_score < THRESHOLD_REPLAN(0.30) OR
     현재 가설이 FAILED?
     YES → dead_end 기록 → "replan"

  5. 스텝 소진 + confidence 부족
     → dead_end 기록 → "replan"

LLM 호출: ❌ 없음 (순수 rule-based)
```

### generate_sql 노드

```
역할:
  누적 지식 전체를 컨텍스트로 SQL 생성.
  재진입 시 sql_fix_instruction을 반드시 프롬프트에 포함.

입력 State:
  - query_decomposition
  - knowledge_items (CONFIRMED만 사용)
  - candidate_tables (조인 경로 포함)
  - explored_use_cases (참고 사례 SQL - 구조 템플릿으로만)
  - dead_ends (실패한 접근 방식 - 반복 방지)
  - sql_fix_instruction (재진입 시, None이면 최초 생성)

출력 State:
  - generated_sql
  - loop_guard.generate_attempts (증가)
  - phase = "GENERATING"

LLM 호출: ✅ Heavy
사전 조건: CONFIRMED되지 않은 knowledge_item이 없어야 함
```

### validate_sql 노드

```
역할:
  생성된 SQL을 3레이어로 검증.
  실패 시 유형을 분류하고 sql_fix_instruction 생성.

Layer 1 — Rule-based (문법/구조)
  - SQL 파싱 가능 여부
  - 사용된 테이블이 candidate_tables에 존재하는지
  - 사용된 컬럼이 해당 테이블의 relevant_columns에 있는지
  → 실패: overall = "FAIL_SYNTAX"

Layer 2 — LLM (의미 검증)
  query_decomposition의 체크리스트와 SQL 대조:
  - measure가 SQL에 반영됐는가?
  - 모든 filter 조건이 WHERE에 있는가?
  - group_by 기준이 GROUP BY에 있는가?
  - CONFIRMED 되지 않은 값을 사용하지 않았는가?
  → local 실패: overall = "FAIL_SEMANTIC_LOCAL" + sql_fix_instruction 생성
  → structural 실패: overall = "FAIL_STRUCTURAL"

Layer 3 — 실행 검증
  LIMIT 5로 실제 실행:
  - row_count = 0 → overall = "FAIL_EMPTY"
  - DB 오류 → overall = "FAIL_DB_ERROR"
  - 값이 비상식적 → overall = "FAIL_STRUCTURAL"
  - 정상 → overall = "SUCCESS"

출력 State:
  - sql_validation_result
  - sql_fix_instruction (실패 시 생성)
  - loop_guard.local_fix_count (local fix 시 증가)

라우팅 규칙:
  "SUCCESS"              → "conclude_success"
  "FAIL_SYNTAX"          → "fix_syntax" (generate_sql, local fix)
  "FAIL_SEMANTIC_LOCAL"  → "fix_local" (generate_sql, local fix)
    단, local_fix_count >= 2 이면 → "replan" (structural로 격상)
  "FAIL_STRUCTURAL"      → "replan"
  "FAIL_EMPTY"           → "replan"
  "FAIL_DB_ERROR"        → "replan"

LLM 호출: ✅ Medium (Layer 2)
```

### replan 노드

```
역할:
  dead_ends를 기반으로 다음 가설을 선택하고 새 실행계획 수립.
  이미 탐색한 것은 재탐색하지 않음.

입력 State:
  - hypotheses (FAILED 제외한 PENDING 목록)
  - dead_ends
  - knowledge_items (확인된 것은 재사용)
  - explored_use_cases
  - searched_queries
  - sampled_tables

출력 State:
  - current_hypothesis (다음 우선순위 가설)
  - execution_plan (새 가설의 스텝들, 이미 확인된 것 제외)
  - current_step_index = 0
  - loop_guard.replan_count (증가)
  - phase = "REPLANNING" → "EXPLORING"

LLM 호출: ✅ Medium (replan 전용 system prompt 사용)

가설 소진 시:
  hypotheses 큐가 비면 → state["phase"] = "DONE" + conclude_failure로 라우팅

Cold Start fallback:
  모든 use_case 기반 가설이 실패 시 → 키워드 기반 테이블 탐색 가설로 전환
```

### conclude 노드

```
역할:
  성공/실패 여부에 따라 최종 응답 구성.

성공 출력:
  {
    "status": "success",
    "sql": "...",
    "explanation": "이 SQL이 어떤 로직인지 자연어 설명",
    "used_tables": [...],
    "join_path": [...],
    "exploration_summary": "N번 시도, 어떤 사례 참고"
  }

실패 출력:
  {
    "status": "failure",
    "reason": "실패 요약",
    "dead_ends": [...],
    "missing_info": "어떤 정보가 있으면 해결 가능한지",
    "partial_sql": "부분적으로 가능한 SQL (있는 경우)"
  }

LLM 호출: ✅ Light
```

---

## 7. Confidence Score 설계

에이전트가 "SQL을 생성할 준비가 됐는가"를 판단하는 수치입니다.

```python
def calculate_readiness(state: AgentState) -> float:
    scores = []

    # 1. 용어 해소율 — 가중치 가장 높음
    items = state["knowledge_items"]
    if items:
        confirmed = [i for i in items if i["confidence"] >= 0.8]
        term_score = len(confirmed) / len(items)
    else:
        term_score = 0.0
    scores.append(("term_resolution", term_score, 0.4))

    # 2. 테이블 커버리지
    required = state["query_decomposition"].get("required_concepts", [])
    if required:
        covered = [r for r in required if is_covered_by_tables(r, state)]
        table_score = len(covered) / len(required)
    else:
        table_score = 0.0
    scores.append(("table_coverage", table_score, 0.3))

    # 3. 활용사례 유사도
    use_cases = state["explored_use_cases"]
    case_score = max((uc.get("similarity", 0) for uc in use_cases), default=0.0)
    scores.append(("use_case_coverage", case_score, 0.2))

    # 4. 조인 경로 확인 여부
    join_score = 1.0 if state["confirmed_join_path"] else 0.0
    scores.append(("join_path", join_score, 0.1))

    return sum(score * weight for _, score, weight in scores)

# 임계값
THRESHOLD_GENERATE = 0.75  # 이상이면 SQL 생성 시도
THRESHOLD_REPLAN   = 0.30  # 이하이면 가설 자체를 바꿔야 함
```

---

## 8. 루프 제어 및 탈출 조건

```python
MAX_TOOL_CALLS    = 20   # 도구 호출 총 횟수
MAX_REPLANS       = 3    # 재계획 횟수
MAX_GENERATES     = 4    # SQL 생성 시도 횟수
MAX_LOCAL_FIXES   = 2    # local fix 횟수 (초과 시 structural로 격상)

def should_terminate(state: AgentState) -> bool:
    """하나라도 해당되면 종료"""
    g = state["loop_guard"]
    return (
        g["total_tool_calls"] >= MAX_TOOL_CALLS   or
        g["replan_count"]     >= MAX_REPLANS      or
        g["generate_attempts"]>= MAX_GENERATES    or
        len([h for h in state["hypotheses"]
             if h["status"] == "PENDING"]) == 0    # 가설 소진
    )

def should_escalate_to_structural(state: AgentState) -> bool:
    """local fix가 반복되면 structural 실패로 격상"""
    return state["loop_guard"]["local_fix_count"] >= MAX_LOCAL_FIXES
```

---

## 9. 프롬프트 설계 지침

### plan 노드 프롬프트 핵심 지침

```
[역할]
당신은 데이터를 처음 보는 분석가입니다.
정답을 아는 척하지 말고, 탐색을 통해 점진적으로 확신을 쌓아가야 합니다.

[사고 순서]
1. 분석 요건을 measure / filters / group_by / order_limit 4가지로 분해하세요
2. 각 개념이 DB에서 어떻게 표현되는지 모르는 것을 UNRESOLVED로 목록화하세요
3. UNRESOLVED를 해소하기 위한 탐색 계획을 세우세요
4. 활용사례 검색 없이 SQL 생성을 계획하지 마세요

[탐색 우선순위 — 반드시 준수]
  활용사례 탐색 → 사례의 테이블 메타 탐색 → 불확실 용어 샘플 확인 → SQL 생성

[스텝 설계 원칙]
- 각 스텝은 "purpose(왜 필요한가)"를 반드시 명시
- searched_queries에 있는 검색어는 계획에 포함하지 마세요
- sampled_tables에 있는 테이블은 샘플 조회를 계획하지 마세요
- 스텝 수는 최소화하세요 (많을수록 좋은 것이 아닙니다)

[Cold Start 대비]
활용사례 검색 결과가 없을 경우를 대비한 fallback 가설을
반드시 가설 큐의 마지막에 포함하세요.
fallback 가설의 전략: "질의 키워드로 직접 테이블 메타 탐색"
```

### replan 노드 프롬프트 핵심 지침

```
[역할]
이전 접근 방식이 실패했습니다. 완전히 다른 각도로 접근하세요.

[반드시 참고할 것]
- 실패 기록: {dead_ends}
- 이미 탐색한 쿼리: {searched_queries}
- 이미 샘플 조회한 테이블: {sampled_tables}
- 현재 확인된 지식: {confirmed_knowledge_items}

[새 가설 수립 원칙]
- 실패한 테이블은 다시 사용하지 마세요
- 실패 이유가 "용어 해소 불가"라면 다른 키워드로 검색하세요
- 확인된 지식(CONFIRMED)은 재사용하고 탐색을 최소화하세요
- 가능성이 없다면 솔직하게 "해결 불가" 판단을 내리세요

[새 실행계획 원칙]
- 이미 확인된 knowledge_item은 다시 탐색하지 마세요
- 이미 본 활용사례는 다시 검색하지 마세요
```

### generate_sql 노드 프롬프트 핵심 지침

```
[역할]
아래 확인된 지식만 사용해서 SQL을 작성하세요.

[반드시 포함할 컨텍스트]
1. 참고 활용사례 SQL — 구조 템플릿으로만 활용, 그대로 복사하지 마세요
2. 확인된 용어 매핑 (CONFIRMED knowledge_items만)
3. 테이블 조인 경로
4. 실패한 접근 방식 (dead_ends — 반복 방지)
5. 질의 분해 체크리스트

[재생성 시 추가 지침]
이전 SQL의 문제점: {sql_fix_instruction}
위 문제를 반드시 수정하세요.

[SQL 생성 후 자기검증 — 출력 전 반드시 체크]
□ measure가 SQL에 반영됐는가?
□ 모든 filter 조건이 WHERE에 있는가?
□ group_by 기준이 GROUP BY에 있는가?
□ CONFIRMED되지 않은 값을 사용하지 않았는가?
□ dead_ends에 기록된 실패 패턴을 반복하지 않았는가?
```

### update_knowledge (explore 내부) 관찰 메모 기준

```
[관찰 메모 작성 기준]
결과를 그대로 저장하지 말고, 현재 질의와의 관련성을 판단해서
"무엇을 알게 됐는가"를 한 문장으로 기록하세요.

형식:
  발견:   "order_status 컬럼에 CANCEL 값 존재 확인"
  확인:   "grade_nm = 'VIP' 실존 확인, VVIP도 존재"
  부재:   "orders 테이블에 category 정보 없음 → 조인 필요"
  충돌:   "취소 데이터가 orders와 claims 두 곳에 존재 → CONFLICTED"

충돌 발견 시:
  status를 CONFLICTED로 표시하고
  어느 테이블을 써야 하는지 근거가 필요함을 명시하세요.
```

---

## 10. 구현 우선순위 및 체크리스트

### Phase 1 — 기반 구조 (필수)

```
□ AgentState TypedDict 정의 (모든 서브타입 포함)
□ 외부 캐시 레이어 구현 (result_ref 기반)
□ plan 노드 + 플래너 프롬프트
□ explore 노드 (내부 루프 + 도구 분기)
□ assess_confidence 노드 (rule-based)
□ confidence score 계산 함수
□ loop_guard 및 탈출 조건
□ LangGraph 그래프 조립 및 엣지 연결
```

### Phase 2 — 핵심 기능

```
□ generate_sql 노드 + 프롬프트
□ validate_sql 노드 (3레이어)
□ 실패 유형 분류 및 sql_fix_instruction 생성
□ replan 노드 + 프롬프트
□ conclude 노드 (성공/실패 응답 구성)
□ Cold Start fallback 가설 처리
```

### Phase 3 — 품질 개선

```
□ local_fix_count >= 2 시 structural 격상 로직
□ CONFLICTED 상태 처리 (활용사례 기반 해소)
□ 조기 탈출 최적화 (confidence 임계값 튜닝)
□ 탐색 결과 캐시 TTL 관리
□ 에이전트 실행 로그 및 디버깅 도구
```

### Phase 4 — v2 고도화

```
□ LangGraph Send API 기반 병렬 가설 탐색
□ confidence 임계값 도메인별 동적 조정
□ 탐색 패턴 학습 (자주 실패하는 경로 자동 회피)
```

---

## 부록 — 도구 우선순위 및 비용 기준

| 도구 | 용도 | 비용 | 사용 조건 |
|------|------|------|-----------|
| search_use_cases | 유사 활용사례 검색 | 낮음 | 항상 가장 먼저 |
| search_table_meta | 테이블/컬럼 메타 | 낮음 | 활용사례에서 테이블 발견 후 |
| search_code_meta | 코드값 목록 조회 | 낮음 | 코드성 컬럼 UNRESOLVED 시 |
| search_glossary | 용어사전 검색 | 낮음 | 업무 용어 UNRESOLVED 시 |
| get_sample_data | 샘플 데이터 조회 | 높음 | CANDIDATE 이상이고 값 확인 필요 시만 |
| generate_sql | SQL 생성 | 높음 | 모든 critical_terms CONFIRMED 후만 |
| validate_sql | SQL 검증 | 높음 | SQL 생성 직후 |

**비용이 높은 도구는 반드시 근거가 생긴 후에만 사용합니다.**

---

## 참고사항 1 — sqlglot 기반 SQL 파싱을 통한 LLM 비용 절감 전략

### 1-1. 배경 및 목적

Qdrant sql_history 벡터 검색으로 유사 SQL을 찾은 뒤, SQL 원문 전체를 LLM 프롬프트에 넣으면
**토큰 낭비 + 소형 모델의 분석 실패** 문제가 발생한다.

```
유사 SQL 10건 × 평균 30줄 = 300줄의 SQL 원문을 LLM이 매번 해석해야 함
→ 대형 모델: 가능하지만 토큰 비용 높음
→ 폐쇄망 소형 모델(7B~70B): 긴 SQL 10건 동시 분석은 신뢰 불가
```

sqlglot으로 유사 SQL에서 **구조적 힌트를 미리 파싱**하여, LLM에는 짧고 명확한 정보만 전달한다.

### 1-2. 파싱 시점: 저장 시가 아닌 조회 후 런타임

벡터 저장소에 파싱 결과를 사전 저장하지 않고, **유사 SQL 조회 후 런타임에 파싱**한다.

| 비교 | 저장 시 파싱 | 조회 후 파싱 (채택) |
| ------ | ------------- | ------------------- |
| 파싱 횟수 | 10,000건 × 1회 | 요청당 Top-10건 × 매번 |
| 런타임 비용 | 0 | 수 ms (무시 가능) |
| 저장 비용 | payload 증가 | 없음 |
| 파싱 로직 변경 시 | 전체 재시딩 필요 | 즉시 반영 |
| 메타 부패 리스크 | 있음 (스냅샷 고정) | 없음 (항상 최신 로직) |

### 1-3. 추출 대상 및 활용 방식

유사 SQL에서 sqlglot으로 추출하는 4가지 구조적 힌트:

#### (1) join_pattern — 조인 구조

```python
# 추출 결과 예시
["TB_LNB301M.CUST_NO = TB_COM001M.CUST_NO",
 "TB_LNB301M.BRANCH_CD = TB_BRC001M.BRANCH_CD"]
```

- **핵심 이유**: 정보계 DB에 FK가 없어 조인 키 추론이 agent 최대 난제
- **활용**: SQL 생성 프롬프트에 "검증된 조인 패턴"으로 제공
- **추출 API**: `parsed.find_all(sqlglot.exp.Join)` → ON 절 추출

#### (2) code_columns — 코드성 컬럼과 사용된 값

```python
# 추출 결과 예시
{"STATUS_CD": ["01", "02"], "PRODUCT_TYPE_CD": ["110", "120"]}
```

- **핵심 이유**: "정상 대출"이 `STATUS_CD = '01'`인지 `'1'`인지 메타만으로 확인 불가.
  과거 실제 동작한 코드값 조합은 매우 강력한 힌트
- **활용**: SQL 생성 프롬프트에 "과거 사용된 코드값" 참고로 제공
- **추출 API**: WHERE/HAVING 절에서 `col = 'literal'`, `col IN (...)` 패턴 파싱

#### (3) agg_expressions — 집계 패턴

```python
# 추출 결과 예시
["SUM(LOAN_AMT)", "COUNT(DISTINCT CUST_NO)"]
```

- **핵심 이유**: "대출잔액 합계"가 `SUM(LOAN_AMT)`인지 `SUM(LOAN_BAL)`인지 참고
- **활용**: SQL 생성 프롬프트에 "유사 질의의 집계 방식" 참고
- **추출 API**: `parsed.find_all(sqlglot.exp.AggFunc)`

#### (4) date_filter — 날짜 조건 패턴

```python
# 추출 결과 예시
{"column": "BASE_DT", "format": "YYYYMMDD"}
```

- **핵심 이유**: 테이블마다 날짜 컬럼명(`BASE_DT`, `STD_DT`, `TXN_DT`)과
  포맷(`YYYYMMDD`, `YYYY-MM-DD`, DATE 타입)이 다름. 틀리면 결과 0건
- **활용**: SQL 생성 프롬프트에 "해당 테이블의 날짜 사용법" 참고
- **추출 API**: WHERE 절에서 날짜 컬럼 + 리터럴 패턴 추출

### 1-4. LLM 프롬프트 투입 비교

```
❌ 파싱 없이 SQL 원문 전달 (토큰 多, 소형 모델 분석 실패 가능)
────────────────────────────
유사 SQL 1:
  SELECT a.BRANCH_CD, SUM(a.LOAN_AMT), COUNT(DISTINCT a.CUST_NO)
  FROM TB_LNB301M a
  JOIN TB_COM001M b ON a.CUST_NO = b.CUST_NO
  WHERE a.STATUS_CD IN ('01','02')
    AND a.BASE_DT BETWEEN '20240101' AND '20240331'
  GROUP BY a.BRANCH_CD
유사 SQL 2: ...
유사 SQL 3: ...
(이하 7건 더)

✅ 파싱 후 구조화 힌트 전달 (토큰 少, 소형 모델도 즉시 활용)
────────────────────────────
유사 질의에서 확인된 패턴:
  - 조인: TB_LNB301M.CUST_NO = TB_COM001M.CUST_NO
  - 코드값: STATUS_CD IN ('01','02')
  - 집계: SUM(LOAN_AMT), COUNT(DISTINCT CUST_NO)
  - 날짜: BASE_DT, YYYYMMDD 형식, BETWEEN 범위 조건
```

두 방식의 차이:

| 비교 항목 | SQL 원문 전체 | 구조화 힌트 |
| --- | --- | --- |
| 토큰 비용 | 높음 (10건 전문) | 낮음 (수 줄) |
| 대형 모델 (Claude) | 해석 가능 | 해석 가능 |
| 소형 모델 (7B~70B) | 놓칠 수 있음 | 확실히 전달됨 |
| 규칙 기반 활용 | 불가 (비정형) | 가능 (코드값 검증 등) |

### 1-5. sqlglot 파싱 정확도 분석

#### 전반적 파싱 정확도

DataHub 실증 데이터 기준, sqlglot 기반 SQL 파싱으로 **97~99% lineage 정확도**를 달성.
CTE, 서브쿼리, UNION ALL을 포함한 복합 쿼리 기준이다.

> 주의: CrackSQL(SIGMOD 2025) 벤치마크의 40~48% 오류율은 **방언 간 변환(transpilation)** 수치이며,
> **파싱(AST 생성) 자체**의 정확도와는 별개이다.

#### 추출 항목별 신뢰도

| 추출 대상 | 신뢰도 | 권장 API | 비고 |
| ----------- | -------- | ---------- | ------ |
| JOIN 조건 | **높음** | `find_all(exp.Join)` | ON 절 직접 추출 |
| WHERE 리터럴 | **높음** | `find_all(exp.Literal)` | 코드값 추출 |
| 집계함수 | **높음** | `find_all(exp.AggFunc)` | 집계 패턴 추출 |
| 테이블명 | **주의** | `traverse_scope()` 사용 필수 | `find_all(exp.Table)`은 CTE 오인 |

#### 알려진 함정 3가지

**함정 1: `find_all(exp.Table)`의 CTE 오인**

```python
# ❌ CTE 별칭 "x"를 실제 테이블로 잘못 반환
parse_one("WITH x AS (SELECT 1) SELECT * FROM x JOIN y").find_all(exp.Table)
# 결과: [x, y]

# ✅ traverse_scope 사용 — 실제 테이블만 반환
from sqlglot.optimizer.scope import traverse_scope
for scope in traverse_scope(ast):
    for alias, (node, source) in scope.selected_sources.items():
        if isinstance(source, exp.Table):
            print(source.name)  # y만 반환
```

#### 함정 2: 파싱 실패의 무음 처리

기본 `error_level=WARN`에서는 파싱 실패해도 예외 없이 불완전한 AST를 반환한다.
런타임 파싱에서는 try/except로 감싸고, 실패 시 빈 힌트로 폴백한다.
(힌트는 보조 정보이므로 실패해도 기존 동작에 영향 없음)

```python
try:
    ast = sqlglot.parse_one(sql, error_level=sqlglot.ErrorLevel.RAISE)
    hints = extract_structural_hints(ast)
except sqlglot.errors.ParseError:
    hints = empty_hints()  # 빈 힌트로 폴백
```

#### 함정 3: 미지원 구문의 Command 폴백

지원 불가 구문은 `exp.Command` 노드로 반환되며 `find_all(exp.Table)` 결과가 빈 리스트.
파싱 성공/실패 구분이 안 되므로 타입 체크가 필요하다.

```python
if isinstance(ast, sqlglot.exp.Command):
    hints = empty_hints()  # 미지원 구문, 힌트 추출 불가
```

### 1-6. 방언별 정확도 — PostgreSQL / Impala / Sybase IQ

#### PostgreSQL (`dialect="postgres"`)

**공식 지원**. 프로젝트 온라인 개발 환경의 기본 방언.

| 구문 | 지원 | 비고 |
|------|------|------|
| 표준 SELECT/JOIN/WHERE/CTE | 완전 지원 | |
| 윈도우함수 (OVER, PARTITION BY) | 완전 지원 | |
| LATERAL JOIN | 지원 | Issue #4133에서 수정 완료 |
| IS JSON (PG 17+) | 미지원 | Issue #3965, 대다수 환경 무해 |
| INTERVAL 일부 표현 | 부분 지원 | Issue #4490, 수정 진행 중 |

**결론**: 본 프로젝트의 SELECT 전용 환경에서 실질적 문제 없음.

#### Impala (`dialect="hive"` 매핑)

**공식 미지원**. Hive 방언으로 대체하여 사용한다.
(Apache Superset PR #34662에서 동일 전략으로 해결한 선례 있음)

| 구문 | dialect="hive" 처리 | 비고 |
| ------ | --------------------- | ------ |
| 표준 SELECT/JOIN/WHERE/CTE | **정상 파싱** | 95%+ 커버 |
| 윈도우함수 | **정상 파싱** | |
| DATE_ADD, DATEDIFF 등 | **정상 파싱** | Hive 방언에 명시적 override 존재 |
| NDV(), APPX_MEDIAN() | **파싱 성공** | `exp.Anonymous`로 처리 (이름 추출은 가능) |
| `[broadcast]` 대괄호 힌트 | **ParseError** | Impala 전용 힌트 구문 |
| `/* +broadcast */` 주석 힌트 | **미인식** | Hive가 Impala 힌트명 미지원 |
| COMPUTE STATS, INVALIDATE METADATA | 미지원 | DDL이므로 SELECT 파싱에 미출현, 무해 |

**결론**: SELECT 전용 쿼리에서 조인/WHERE/집계 추출은 **95% 이상 신뢰 가능**.
쿼리 힌트가 포함된 경우만 파싱 전 정규식으로 제거하면 해결된다.

```python
import re
# Impala 힌트 제거 전처리
cleaned = re.sub(r'/\*\s*\+.*?\*/', '', sql)         # /* +broadcast */ 제거
cleaned = re.sub(r'\[\s*(broadcast|shuffle)\s*\]', '', cleaned)  # [broadcast] 제거
ast = sqlglot.parse_one(cleaned, dialect="hive")
```

#### Sybase IQ (`dialect="tsql"` 매핑 + fallback)

**공식 미지원**. 메인테이너가 네이티브 지원을 명시적으로 거절 (Issues #3274, #7204, #4069 모두 Closed, Not Planned).
TSQL 방언이 가장 가까운 대체이다.

| 구문 | dialect=None (ANSI) | dialect="tsql" (채택) |
| ------ | -------------------- | ----------------------- |
| 표준 SELECT/JOIN/WHERE/CTE | 정상 파싱 | 정상 파싱 |
| DATEADD, DATEDIFF, DATEPART | `exp.Anonymous` | **지원** |
| SELECT TOP N | **ParseError** | **지원** |
| CONVERT(type, expr, style) 3인자 | 실패 | **지원** |
| KEY JOIN (Sybase IQ 전용) | 실패 | 실패 |
| `*=` (SQL-89 아우터 조인) | 실패 | 실패 |
| BIGDATETIME 타입 | 미인식 | 미인식 |

**결론**: `dialect="tsql"`로 **85~90% 커버 가능**. KEY JOIN, `*=` 조인 등
Sybase IQ 고유 구문은 정규식 전처리 또는 파싱 실패 시 빈 힌트 폴백으로 대응한다.

```python
DIALECT_MAP = {
    "postgresql": "postgres",   # 공식 지원
    "impala":     "hive",       # 근사 매핑 (95%+)
    "sybase_iq":  "tsql",       # 근사 매핑 (85~90%)
}
```

### 1-7. 방언 전환 시 권장 구현 패턴

```python
def parse_sql_safe(sql: str, dialect: str | None = None) -> sqlglot.Expression | None:
    """sqlglot 파싱 — 실패 시 None 반환.

    힌트 추출은 보조 정보이므로, 파싱 실패가 agent 전체 흐름을 차단하지 않는다.
    """
    try:
        cleaned = _preprocess_dialect_quirks(sql, dialect)
        ast = sqlglot.parse_one(cleaned, dialect=dialect, error_level=sqlglot.ErrorLevel.RAISE)
        if isinstance(ast, sqlglot.exp.Command):
            return None
        return ast
    except sqlglot.errors.ParseError:
        return None


def _preprocess_dialect_quirks(sql: str, dialect: str | None) -> str:
    """방언별 비표준 구문 전처리."""
    if dialect == "hive":  # Impala
        sql = re.sub(r'/\*\s*\+.*?\*/', '', sql)
        sql = re.sub(r'\[\s*(broadcast|shuffle|noshuffle)\s*\]', '', sql)
    return sql
```

### 1-8. 핵심 인사이트 요약

1. **파싱은 런타임에**: 벡터 저장소에 파싱 결과를 사전 저장하지 않는다.
   sqlglot 파싱 비용은 무시할 수준(수 ms)이며, 로직 변경이 즉시 반영되는 이점이 크다.

2. **힌트는 보조 정보**: 파싱 실패 시 빈 힌트로 폴백하면 기존 동작(SQL 원문 참조)과 동일.
   agent 전체 흐름을 차단하지 않는 안전한 구조.

3. **소형 모델 대응이 핵심 동기**: 대형 모델은 SQL 원문을 직접 읽을 수 있지만,
   폐쇄망 소형 모델(7B~70B)에는 구조화된 힌트가 정확도에 직접 영향.

4. **방언 커버리지**: PostgreSQL(완전) > Impala(95%+) > Sybase IQ(85~90%).
   모든 방언에서 파싱 실패 시 graceful fallback이 보장되므로 실용적으로 충분.

5. **현재 코드 개선점**: 기존 `_extract_tables()`의 `find_all(exp.Table)` 사용을
   `traverse_scope()`로 교체하여 CTE 오인 문제를 해소해야 한다.

---

*문서 버전: v1.0 | 작성 목적: Claude Code 구현 참고용*
