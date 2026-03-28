# 에이전틱 루프 통합 설계안 — Hybrid Pipeline with Agentic Core

> **상태**: Working Draft v1.1 (비판적 검토 반영)
> **목적**: 현재 13-노드 선형 파이프라인의 장점을 유지하면서, 점진적 탐색 기반 에이전틱 루프의 핵심 가치를 융합하는 최적 설계안
> **작성일**: 2026-03-24
> **검토**: review-and-improvements.md 참조 (30건 리뷰 반영)

---

## 1. 현재 아키텍처 vs 에이전틱 루프 — GAP 분석

### 1-1. 현재 아키텍처의 강점 (보존 대상)

| 영역 | 강점 | 보존 근거 |
|------|------|-----------|
| 전처리 파이프라인 | preprocess → history_resolve → intent_classify → normalize_query 4단계가 안정적으로 검증됨 | 보안/PII/세션 관리가 이미 완성 |
| 8-Slot 정규화 | 2-Phase LLM + 12개 교차검증 규칙 (R1~R12) | SQL 생성 정확도의 핵심 기반 |
| 6-Source 병렬 수집 | ES 테이블메타/보고서SQL/코드메타 + PostgreSQL 이력 + Qdrant 매뉴얼/SQL이력 | asyncio.gather 기반 성능 최적화 완성 |
| 테이블 3-View 보강 | Entity/Functional/Lifecycle 관점 LLM 보강 | 불완전 메타 대응의 핵심 |
| 유사 테이블 해소 | similar_table_resolver의 PASS/WARNING/AMBIGUOUS 판정 | 금융 정보계 특화 로직 |
| 커넥터 아키텍처 | BaseConnector → Search/Database 분리, Dummy 모드 | 폐쇄망 전환 대비 완성 |
| 후처리 파이프라인 | execute_sql → analyze_data → format_response | 분석/시각화/포맷팅 안정 |
| 보안 레이어 | 13개 SQL 인젝션 + 13개 프롬프트 인젝션 패턴 | 금융 필수 보안 요건 |

### 1-2. 현재 아키텍처의 한계 (개선 대상)

| 한계 | 영향 | 에이전틱 루프 해결 방식 |
|------|------|------------------------|
| **단발성 컨텍스트 수집** | 한 번의 수집으로 부족한 정보가 있어도 보완 불가 | 탐색 루프로 점진적 지식 축적 |
| **불확실성 비관리** | "아마 맞겠지"로 SQL 생성 진행, 용어 확신도 추적 없음 | KnowledgeItem의 confidence 상태 관리 |
| **SQL 재시도의 맹목성** | validate → generate 루프에서 "왜 틀렸는지" 구조적 분류 없음 | 5단계 실패 유형 분류 + fix_instruction |
| **가설 전환 불가** | 테이블/접근방식이 틀렸을 때 처음부터 다시 시도할 메커니즘 없음 | Hypothesis 큐 + recovery_planner 노드 |
| **Dead-End 미기록** | 실패한 시도가 기록되지 않아 같은 실수 반복 가능 | dead_ends 기반 학습된 회피 |
| **활용사례 구조 미파싱** | SQL 이력을 원문 그대로 LLM에 전달 → 토큰 낭비 | sqlglot 구조적 힌트 추출 |

### 1-3. 에이전틱 루프의 리스크 (완화 필요)

| 리스크 | 설명 | 완화 전략 |
|--------|------|-----------|
| **레이턴시 증가** | 탐색 루프가 여러 번 순환하면 응답 지연 | Fast-Path 바이패스 (고확신 시 탐색 스킵) |
| **LLM 비용 증가** | planner/confidence_evaluator/recovery_planner 각 노드의 LLM 호출 | confidence_evaluator는 rule-based, planner는 조건부 |
| **상태 복잡도** | AgentState가 PipelineState보다 훨씬 복잡 | 에이전틱 상태를 서브모델로 격리 |
| **기존 코드 파괴** | 전면 교체 시 검증된 로직 유실 | 서브그래프 패턴으로 기존 노드 재사용 |

---

## 2. 통합 전략 — "Hybrid Pipeline with Agentic Core"

### 2-1. 핵심 아이디어

```
현재 파이프라인의 "안정된 외곽"을 유지하면서,
중간부(컨텍스트 수집 → SQL 생성 → 검증)를 에이전틱 서브그래프로 교체한다.
```

```
[현재 유지]                    [에이전틱 코어로 교체]              [현재 유지]
┌─────────────────┐     ┌──────────────────────────┐     ┌─────────────────┐
│ preprocess       │     │ ┌─ planner ───────────────┐│     │ execute_sql      │
│ resolve_history  │     │ │  context_explorer       ││     │ analyze_data     │
│ classify_intent  │ ──→ │ │  confidence_evaluator   ││ ──→ │ format_response  │
│ normalize_query  │     │ │  sql_generator          ││     │                  │
│                  │     │ │  sql_validator          ││     │                  │
│                  │     │ │  recovery_planner       ││     │                  │
│                  │     │ └─ result_finalizer ──────┘│     │                  │
└─────────────────┘     └──────────────────────────┘     └─────────────────┘
   Outer Head                Agentic Core                   Outer Tail
  (그대로 유지)           (서브그래프)                    (그대로 유지)
```

### 2-2. 설계 원칙

1. **서브그래프 격리**: 에이전틱 코어는 LangGraph 서브그래프로 구현. 메인 파이프라인과 상태 경계를 명확히 분리
2. **기존 서비스 재사용**: collect_context, enrich_context, sql_prompt_assembler, sql_safety_checker 등 기존 서비스는 에이전틱 노드 내부에서 "도구"로 호출
3. **Fast-Path 바이패스**: 유사 SQL 이력이 높은 유사도로 매칭되면 탐색 루프를 건너뛰고 즉시 SQL 생성. **Fast-Path 검증 실패 시 정상 탐색 루프로 복귀하는 복구 경로 포함** (리뷰 C-24)
4. **점진적 전환**: 설정 플래그로 기존 선형 파이프라인 ↔ 에이전틱 코어를 런타임 전환 가능
5. **소형 모델 Fallback**: 모든 LLM Heavy 노드(planner, recovery_planner, sql_validator-Layer2)에 rule-based fallback 경로 내장. `settings.model_capability: Literal["large", "small"]`로 분기 (리뷰 C-15, C-16, C-22)
6. **의존성 주입**: 외부 서비스(LLM, DB, ES, Qdrant)를 `ExplorationTools` Protocol로 추상화하여 테스트 가능성 확보 (리뷰 C-20)
7. **Immutable 상태 패턴**: 모든 상태 변경은 노드 반환 dict를 통해서만 수행. Pydantic 모델 직접 mutation 금지 (리뷰 C-07)

### 2-3. 선형 파이프라인과의 전환 매트릭스

```python
# config.py에 추가
agentic_core_enabled: bool = False  # True: 에이전틱 코어, False: 기존 선형

# pipeline.py에서 조건부 라우팅
def _next_after_normalize(state) -> str:
    if settings.agentic_core_enabled:
        return "agentic_core"      # 서브그래프 진입
    return "collect_context"        # 기존 선형 경로
```

---

## 3. 상태 설계 — AgenticCoreState

### 3-1. 설계 철학

PipelineState를 확장하지 않고, 에이전틱 코어 전용 상태를 별도 모델로 정의한다.
메인 파이프라인과의 경계에서 입력/출력을 명시적으로 매핑한다.

```
PipelineState (메인)                  AgenticCoreState (서브그래프)
┌───────────────────────┐            ┌──────────────────────────┐
│ preprocessed_input     │──(입력)──→│ original_query            │
│ normalized_query       │──(입력)──→│ normalized_query          │
│ intent                 │──(입력)──→│ intent                    │
│ conversation_history   │──(입력)──→│ conversation_history      │  ← C-02 반영
│ context                │           │ knowledge_items           │
│                        │           │ hypotheses                │
│                        │           │ candidate_tables          │
│                        │           │ ...                       │
│ generated_sql          │←──(출력)──│ generated_sql             │
│ validated_sql          │←──(출력)──│ validated_sql             │
│ context.table_metas    │←──(출력)──│ → TableMeta(+columns) 변환│  ← C-03 반영
│ trace_log              │←──(출력)──│ trace_entries             │  ← C-01 반영
│ awaiting_clarification │←──(출력)──│ needs_user_input          │
│ clarification_question │←──(출력)──│ user_question             │
│ error_message          │←──(출력)──│ exploration_summary       │
└───────────────────────┘            └──────────────────────────┘
```

### 3-2. 서브타입 정의

```python
from typing import TypedDict, Literal, Optional
from pydantic import BaseModel, Field


class KnowledgeItem(BaseModel):
    """탐색 과정에서 축적되는 개별 지식 단위."""
    key: str                    # "취소 상태 표현", "지점코드 컬럼"
    value: str                  # "order_status = 'CANCEL'"
    confidence: float = 0.0     # 0.0 ~ 1.0
    status: Literal[
        "UNRESOLVED",   # 아무것도 모름
        "CANDIDATE",    # 메타/사례에서 후보 발견, 미검증
        "PROBABLE",     # 논리적 추론으로 그럴 것 같음
        "CONFIRMED",    # 샘플 또는 실행으로 실제 확인
        "CONFLICTED",   # 여러 소스에서 충돌하는 정보
    ] = "UNRESOLVED"
    source: str = ""            # "코드메타" | "샘플데이터" | "활용사례" | "추론"
    evidence: list[str] = Field(default_factory=list)
    is_critical: bool = True    # SQL 생성에 필수인가? (C-26 반영)


class Hypothesis(BaseModel):
    """탐색 가설 — 어떤 접근 방식으로 SQL을 만들 것인가."""
    hypothesis_id: str          # "H1", "H2"
    description: str
    based_on_use_case: Optional[str] = None
    required_tables: list[str] = Field(default_factory=list)
    missing_terms: list[str] = Field(default_factory=list)
    priority: float = 0.5
    strategy: str = ""          # 접근 전략 한 줄 요약
    status: Literal["PENDING", "ACTIVE", "SUCCESS", "FAILED"] = "PENDING"


class ExecutionStep(BaseModel):
    """context_explorer 노드의 실행 단위."""
    step: int
    tool: str                   # "search_use_cases" | "search_table_meta" | ...
    input: str                  # 검색어 또는 파라미터
    purpose: str                # 이 스텝이 필요한 이유
    expected_output: str = ""
    status: Literal["PENDING", "DONE", "SKIPPED", "FAILED"] = "PENDING"
    result_ref: Optional[str] = None   # 외부 캐시 키
    insight: Optional[str] = None      # 결과로부터 추출한 핵심 관찰


class CandidateTable(BaseModel):
    """탐색 중 발견된 후보 테이블 — 구조 데이터 운반용.

    테이블 적합성 판단은 knowledge_items에서 수행 (C-21 반영).
    key="table:{table_name}", status=CONFIRMED 여부로 판단.
    이 모델은 SQL 생성에 필요한 구조 정보만 보관.
    """
    table_name: str
    role: str = ""              # "주문일자, 취소여부 보유"
    relevant_columns: list[str] = Field(default_factory=list)
    join_keys: list[str] = Field(default_factory=list)
    missing_coverage: list[str] = Field(default_factory=list)


class DeadEnd(BaseModel):
    """실패한 탐색 경로 기록."""
    hypothesis_id: str
    reason: str
    tried_tables: list[str] = Field(default_factory=list)
    tried_terms: list[str] = Field(default_factory=list)
    failure_type: Literal[
        "no_use_case",
        "no_table",
        "term_unresolvable",
        "sql_syntax",
        "sql_semantic_local",
        "sql_structural",
        "empty_result",
        "db_error",
    ] = "no_use_case"


class LoopGuard(BaseModel):
    """루프 제어 카운터."""
    total_tool_calls: int = 0       # MAX: 20
    replan_count: int = 0           # MAX: 3
    generate_attempts: int = 0      # MAX: 4
    local_fix_count: int = 0        # MAX: 2 (초과 시 structural 격상)


class SqlValidationResult(BaseModel):
    """3-레이어 SQL 검증 결과."""
    layer1_status: Literal["PASS", "FAIL", "SKIP"] = "SKIP"
    layer2_status: Literal["PASS", "FAIL", "SKIP"] = "SKIP"
    layer2_passed: list[str] = Field(default_factory=list)
    layer2_failed: list[str] = Field(default_factory=list)
    layer2_failure_type: Optional[Literal["semantic_local", "structural"]] = None
    layer3_status: Literal["PASS", "FAIL", "SKIP"] = "SKIP"
    layer3_row_count: Optional[int] = None
    layer3_is_sane: Optional[bool] = None
    overall: Literal[
        "SUCCESS", "FAIL_SYNTAX", "FAIL_SEMANTIC_LOCAL",
        "FAIL_STRUCTURAL", "FAIL_EMPTY", "FAIL_DB_ERROR",
    ] = "SUCCESS"


class StructuralHints(BaseModel):
    """sqlglot으로 유사 SQL에서 추출한 구조적 힌트."""
    join_patterns: list[str] = Field(default_factory=list)
    code_columns: dict[str, list[str]] = Field(default_factory=dict)
    agg_expressions: list[str] = Field(default_factory=list)
    date_filters: list[dict[str, str]] = Field(default_factory=list)
```

### 3-3. 메인 AgenticCoreState

```python
class AgenticCoreState(BaseModel):
    """에이전틱 코어 서브그래프의 전체 상태."""

    # ── 입력 (메인 파이프라인에서 주입) ─────────────
    original_query: str = ""
    normalized_query: Any = None    # NormalizedQuery
    intent: str = ""

    # ── 현재 진행 상태 ──────────────────────────────
    phase: Literal[
        "PLANNING", "EXPLORING", "VERIFYING",
        "GENERATING", "VALIDATING", "REPLANNING", "DONE",
    ] = "PLANNING"

    # ── 플래너 산출물 ───────────────────────────────
    query_decomposition: dict = Field(default_factory=dict)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    current_hypothesis: Optional[Hypothesis] = None
    execution_plan: list[ExecutionStep] = Field(default_factory=list)
    current_step_index: int = 0

    # ── 누적 지식 ───────────────────────────────────
    knowledge_items: list[KnowledgeItem] = Field(default_factory=list)
    explored_use_cases: list[dict] = Field(default_factory=list)
    candidate_tables: list[CandidateTable] = Field(default_factory=list)
    confirmed_join_path: list[dict] = Field(default_factory=list)
    searched_queries: list[str] = Field(default_factory=list)
    sampled_tables: list[str] = Field(default_factory=list)
    structural_hints: StructuralHints = Field(default_factory=StructuralHints)

    # ── 실패 기록 ───────────────────────────────────
    dead_ends: list[DeadEnd] = Field(default_factory=list)

    # ── SQL ─────────────────────────────────────────
    generated_sql: Optional[str] = None
    validated_sql: Optional[str] = None
    sql_fix_instruction: Optional[str] = None
    sql_validation_result: Optional[SqlValidationResult] = None

    # ── 루프 제어 ───────────────────────────────────
    loop_guard: LoopGuard = Field(default_factory=LoopGuard)

    # ── Fast-Path ───────────────────────────────────
    fast_path_triggered: bool = False
    fast_path_sql: Optional[str] = None

    # ── 최종 출력 ───────────────────────────────────
    final_status: Literal["success", "failure", "pending"] = "pending"
    exploration_summary: str = ""

    # ── 외부 캐시 ───────────────────────────────────
    cache_refs: dict[str, str] = Field(default_factory=dict)  # key → cache_key 매핑
```

---

## 4. 그래프 설계 — 서브그래프 구조

### 4-1. 메인 파이프라인 통합 구조

```
[START]
   ↓
[preprocess]                          ← 기존 유지
   ↓
[resolve_history]                     ← 기존 유지
   ↓
[classify_intent]                     ← 기존 유지
   ├→ CASUAL/META → [clarify] → END  ← 기존 유지
   └→ DATA →
[normalize_query]                     ← 기존 유지
   ↓
┌─── agentic_core_enabled? ──────────────────────────────────────────────┐
│ YES: [agentic_core] (서브그래프)                                        │
│      ├── planner → context_explorer → confidence_evaluator              │
│      │   → sql_generator → sql_validator → result_finalizer            │
│      └── recovery_planner 루프                                          │
│ NO:  [collect_context] → [enrich_context] → [generate_sql]             │
│      → [validate_sql] (기존 선형)                                       │
└────────────────────────────────────────────────────────────────────────┘
   ↓
[execute_sql]                         ← 기존 유지
   ↓
[analyze_data] (if DATA_ANALYSIS)     ← 기존 유지
   ↓
[format_response]                     ← 기존 유지
   ↓
[END]
```

### 4-2. 에이전틱 코어 내부 그래프

```
[agentic_entry]          ← PipelineState → AgenticCoreState 변환
     ↓
[planner]                ← 질의 분해 + 초기 가설 수립 + Fast-Path 판정
     ↓
 fast_path? ──YES──→ [sql_generator] → [sql_validator]
     │NO                                      ↓
     ↓                          ┌─────────────┼────────────┬────────────────┐
[context_explorer]              ↓             ↓            ↓                ↓
     ↓                   [result_finalizer] [sql_generator] [sql_generator] [context_explorer]
[confidence_evaluator]   (성공)            (syntax fix)   (local fix)     (Fast-Path 복구 C-24)
     ↓
┌──────────────────┬──────────────────┬────────────────────┬──────────────────┐
↓                  ↓                  ↓                    ↓                  ↓
[context_explorer] [sql_generator]    [recovery_planner]   [result_finalizer] [ask_user]
(탐색계속)          (SQL 생성)         (재가설)              (성공/실패)        (사용자확인)
                        ↓                  ↓                                  ※ C-09
                   [sql_validator]   가설소진? ──YES──→ [result_finalizer(failure)]
                        ↓                │NO
             ┌──────────┼──────┐         ↓
             ↓          ↓      ↓    [context_explorer]
     [result_finalizer] [sql_generator] [recovery_planner]
     (성공)            (fix)           (structural)

     ↓
[agentic_exit]           ← AgenticCoreState → PipelineState 역변환
```

### 4-3. Fast-Path 바이패스 조건

planner 노드에서 다음 조건을 모두 만족하면 탐색 루프를 건너뛴다:

```python
def should_fast_path(state: AgenticCoreState) -> bool:
    """탐색 없이 즉시 SQL 생성이 가능한 경우를 판단한다."""
    return (
        # 1. 유사 SQL 이력에서 높은 유사도(>0.85) 매칭이 있고
        state.structural_hints.join_patterns  # 조인 패턴 확보
        and len(state.candidate_tables) >= 1  # 테이블 후보 존재
        # 2. UNRESOLVED 용어가 0개이고
        and all(
            ki.status != "UNRESOLVED"
            for ki in state.knowledge_items
        )
        # 3. 정규화 결과에 ambiguities가 없는 경우
        and not (state.normalized_query and state.normalized_query.ambiguities)
    )
```

---

## 5. 노드별 상세 설계

### 5-1. planner (질의 분해 + 가설 수립)

```
역할:
  질의 분해 + UNRESOLVED 용어 목록화 + 초기 가설 수립 + 실행계획 생성
  Fast-Path 판정 (유사 SQL 고유사도 매칭 시 탐색 스킵)

입력:
  - original_query, normalized_query, intent, conversation_history

출력:
  - query_decomposition (measure/filters/group_by/order_limit)
  - knowledge_items (모두 UNRESOLVED 초기 상태, is_critical 설정)
  - hypotheses (우선순위 큐)
  - current_hypothesis, execution_plan
  - fast_path_triggered (True면 context_explorer 스킵)
  - searched_queries (초기 수집에 사용된 쿼리 포함 — C-17 반영)

기존 서비스 재사용:
  - search_context_assembler.collect_context() → 초기 컨텍스트 1차 수집
  - search_query_builder → 검색 쿼리 최적화
  - search_query_embedder → 벡터 임베딩

LLM 호출: ✅ Heavy (플래너 프롬프트)
  ※ 소형 모델 fallback (C-15 반영):
    - settings.model_capability == "small" 시 2-Phase 분리:
      Phase A: 질의 분해 (간단 프롬프트)
      Phase B: rule-based 가설 템플릿 매칭 (LLM 미사용)

특이사항:
  - 8-Slot 정규화 결과를 query_decomposition의 시드로 활용
  - NormalizedQuery.entities → CandidateTable 초기 후보
  - NormalizedQuery.search_keywords → 초기 searched_queries
  - Cold Start fallback 가설 반드시 포함
```

### 5-2. context_explorer (탐색 루프)

```
역할:
  execution_plan의 스텝들을 내부 루프로 순차 실행.
  각 스텝 결과를 knowledge_items에 즉시 반영.

도구 목록 (기존 서비스 재사용):
  search_use_cases(query)
    → QdrantConnector.search("sql_history", ...)
    → sqlglot 구조적 힌트 추출 (StructuralHints)
  search_table_meta(table_name)
    → ElasticSearchConnector.search("table_meta", ...)
    → table_meta_enricher (3-View 보강)
  search_code_meta(column_name)
    → ElasticSearchConnector.search("code_meta", ...)
  search_report_sql(query)
    → ElasticSearchConnector.search("report_sql", ...)
  search_manual(query)
    → QdrantConnector.search("biz_manual", ...)
  search_glossary(term)                           ← C-06 반영
    → 금융 용어사전 검색 (finance_terms.py)
  get_sample_data(table, columns, limit=10)
    → InfoDBConnector.execute_query(f"SELECT {cols} FROM {table} LIMIT 10")

중복 방지:
  - searched_queries 체크
  - sampled_tables 체크

조기 탈출:
  - confidence_score >= 0.75
  - 모든 UNRESOLVED 용어가 CONFIRMED 달성
```

### 5-3. confidence_evaluator (확신도 평가)

```
역할:
  현재 누적 지식 상태를 보고 다음 행동을 결정.
  LLM 없이 rule-based로만 동작.

Confidence Score 계산 (3차원, 옵션 C — C-21 반영):
  term_resolution (50%) — knowledge_items의 CONFIRMED 비율 (용어+테이블 통합)
  use_case_match  (30%) — 유사 활용사례 유사도
  join_path       (20%) — 조인 경로 확인 여부 (knowledge_items의 table: 키 기준)

라우팅:
  loop_guard 초과 → "conclude_failure"
  탐색 스텝 남음 → "context_explorer"
  score >= 0.75 AND 모든 critical CONFIRMED → "sql_generator"
  score < 0.30 OR 현재 가설 FAILED → "recovery_planner"
  스텝 소진 + 확신 부족 → "recovery_planner"
  CONFLICTED 항목 존재 + 해소 불가 → "ask_user"
```

### 5-4. sql_generator (SQL 생성)

```
역할:
  누적 지식 전체를 컨텍스트로 SQL 생성.

기존 서비스 재사용:
  - sql_prompt_assembler.generate_sql() — 프롬프트 조립 + LLM 호출
  - SQL_GENERATION_RULES 시스템 프롬프트

추가 컨텍스트 (에이전틱 코어에서 추가):
  - structural_hints (sqlglot 파싱 결과)
  - confirmed knowledge_items (용어 매핑)
  - dead_ends (반복 방지)
  - sql_fix_instruction (재생성 시)

LLM 호출: ✅ Heavy
```

### 5-5. sql_validator (3-레이어 검증)

```
역할:
  생성된 SQL을 3레이어로 검증.

Layer 1 — Rule-based (기존 sql_safety_checker 재사용)
  + sqlglot 파싱 가능 여부 추가
  + 사용된 테이블이 candidate_tables에 존재하는지
  + 사용된 컬럼이 relevant_columns에 있는지

Layer 2a — 구조적 sanity check (sqlglot, rule-based) (C-22 반영)
  명백한 구조적 누락만 체크 (의미적 매칭은 하지 않음 — 과잉 필터링 방지):
  - group_by 있는데 SQL에 GROUP BY 절 자체가 없음
  - agg_function 있는데 SQL에 집계함수가 하나도 없음
  - SQL에 사용된 테이블이 candidate_tables에 없음
  - SQL에 사용된 컬럼이 해당 테이블에 존재하지 않음
  ※ "고객 수"↔COUNT(cust_no) 등 용어-컬럼 매핑은 하지 않음
  ※ 소형 모델 환경에서는 Layer 2a만 실행 (Layer 2b 스킵)

Layer 2b — LLM 의미 검증
  "이 SQL이 사용자 질의의 의도를 반영하는가?" 판단:
  - CONFIRMED되지 않은 값 사용 여부
  - dead_ends 패턴 반복 여부
  - 자기검증 체크리스트 대조 (C-05 반영)

Layer 3 — 실행 검증 (신규)
  LIMIT 5로 실제 실행:
  - 0건 → FAIL_EMPTY
  - DB 오류 → FAIL_DB_ERROR
  - 값 비상식적 → FAIL_STRUCTURAL
  - 정상 → SUCCESS

실패 유형 분류:
  FAIL_SYNTAX → sql_generator (syntax fix)
  FAIL_SEMANTIC_LOCAL → sql_generator (local fix, max 2회)
  FAIL_STRUCTURAL → recovery_planner
  FAIL_EMPTY → recovery_planner
  FAIL_DB_ERROR → recovery_planner
  local_fix_count >= 2 → structural로 자동 격상
```

### 5-6. recovery_planner (실패 복구 + 재계획)

```
역할:
  dead_ends 기반 다음 가설 선택 + 새 실행계획 수립.

기존 지식 재사용:
  - CONFIRMED knowledge_items는 재탐색하지 않음
  - explored_use_cases 중복 방지
  - searched_queries 중복 방지

LLM 호출: ✅ Medium (replan 전용 프롬프트)
```

### 5-7. result_finalizer (최종 출력)

```
역할:
  성공/실패 여부에 따라 최종 출력 구성.
  AgenticCoreState → PipelineState 역변환 수행.

성공 시:
  - validated_sql → PipelineState.validated_sql
  - candidate_tables → ContextInfo.table_metas
  - exploration_summary → trace_log에 추가

실패 시:
  - error_message 생성
  - partial_sql (있으면) 제공
  - missing_info 안내
```

### 5-8. ask_user 노드

```
역할:
  CONFLICTED 상태의 knowledge_item이 있고 자동 해소 불가능할 때,
  사용자에게 선택지를 제시하는 명확화 질문 생성.

기존 서비스 재사용:
  - clarifier.py 로직 재사용

출력:
  - clarification_question → PipelineState로 전달
  - awaiting_clarification = True
```

### 5-9. 체크포인트 기반 재진입 설계 (C-25)

에이전틱 코어가 사용자 명확화(ask_user)를 요청하고 종료한 뒤,
사용자 응답이 오면 **이전 탐색 상태에서 재개**해야 한다.

#### 핵심 개념: session_id vs turn_id

```
session_id:
  사용자 대화 세션 단위.
  conversation_history의 저장 키. 세션 전체 대화가 누적된다.
  /reset 시 새 session_id 발급.

turn_id:
  질의 해결 단위. 하나의 질의 + 그에 대한 명확화 왕복을 포함.
  에이전틱 코어 체크포인트의 키로 사용.
  resolve_history가 NEW/SKIP → 새 turn_id, CONTINUE → 기존 turn_id 유지.
```

#### 생명주기 비교

```
conversation_history (Redis, session_id 키):
  세션 내 모든 메시지 누적 — "아까 그거"를 해석하려면 전체가 필요
  /reset 또는 세션 만료 시 삭제

checkpointer (Redis, turn_id 키):
  에이전틱 코어의 AgenticCoreState 스냅샷 — 질의 1건의 탐색 상태
  명확화 응답 시 같은 turn_id로 재개
  새 질의 시 새 turn_id → 이전 체크포인트 참조 안 함
  TTL로 자동 만료 (예: 1시간)
```

#### Redis 키 구조

```
Redis:
  session:{session_id}                → conversation_history (기존)
  checkpoint:{turn_id}                → AgenticCoreState 스냅샷 (신규)
  checkpoint:{turn_id}:metadata       → 체크포인트 메타 (신규)
```

#### LangGraph checkpointer 연동

```python
from langgraph.checkpoint.redis import RedisSaver

checkpointer = RedisSaver(redis_url="redis://localhost:6379", ttl=3600)
app = graph.compile(checkpointer=checkpointer)

# 실행 시 turn_id를 LangGraph의 thread_id 파라미터로 전달
config = {"configurable": {"thread_id": turn_id}}
result = await app.ainvoke(state, config)

# 명확화 응답 후 같은 turn_id로 재진입 → 이전 상태에서 재개
result2 = await app.ainvoke(new_input, config)
```

#### 재진입 흐름

```
질의: "지점별 신규 고객 수"  (turn_id = "turn_1")
  → 에이전틱 코어 실행 → CONFLICTED → ask_user → END
  → checkpointer에 저장 (turn_id = "turn_1")

사용자 응답: "'01'이 정상이에요"
  → resolve_history: CONTINUE → turn_id = "turn_1" 유지
  → 에이전틱 코어 재진입 (turn_id = "turn_1" → 체크포인트 복원)
  → CONFLICTED 해소 → SQL 생성 계속

새 질의: "연체율 추이 알려줘"
  → resolve_history: NEW → turn_id = "turn_2" 발급
  → 에이전틱 코어 새로 시작 (체크포인트 없음)
```

---

## 6. sqlglot 구조적 힌트 추출 모듈

### 6-1. 설계

context_explorer에서 유사 SQL을 검색한 후, 원문을 LLM에 직접 전달하는 대신
sqlglot으로 4가지 구조적 힌트를 추출하여 압축된 형태로 제공한다.

```python
# 추출 대상 4가지
# 1. join_patterns   — 검증된 조인 경로 (정보계 FK 없음 대응)
# 2. code_columns    — 과거 사용된 코드값 (코드성 컬럼 값 확인)
# 3. agg_expressions — 집계 패턴 (SUM/COUNT/AVG 대상 컬럼)
# 4. date_filters    — 날짜 조건 패턴 (컬럼명 + 포맷)
```

### 6-2. 방언 매핑

```python
DIALECT_MAP = {
    "postgresql": "postgres",   # 공식 지원 — 파싱 정확도 99%
    "impala":     "hive",       # 근사 매핑 — 95%+ (힌트 제거 전처리)
    "sybase_iq":  "tsql",       # 근사 매핑 — 85~90% (KEY JOIN 폴백)
}
```

---

## 7. 기존 서비스 재사용 매핑

| 에이전틱 코어 도구 | 기존 서비스 | 재사용 방식 |
|---------------------|-------------|-------------|
| search_use_cases | QdrantConnector + search_query_embedder | 직접 호출 |
| search_table_meta | ElasticSearchConnector + search_query_builder | 직접 호출 |
| search_code_meta | ElasticSearchConnector (code_meta 인덱스) | 직접 호출 |
| search_report_sql | ElasticSearchConnector (report_sql 인덱스) | 직접 호출 |
| search_manual | QdrantConnector (biz_manual 컬렉션) | 직접 호출 |
| get_sample_data | InfoDBConnector.execute_query | LIMIT 10 래핑 |
| enrich_table | table_meta_enricher | 직접 호출 |
| generate_sql | sql_prompt_assembler | 컨텍스트 확장 후 호출 |
| validate_safety | sql_safety_checker | Layer 1에서 호출 |
| resolve_tables | similar_table_resolver | Layer 1에서 호출 |
| search_glossary | MongoConnector.search_glossary (glossary 컬렉션) | 직접 호출 (C-06) |
| parse_sql_hints | sqlglot (신규) | 힌트 추출 전용 |

---

## 8. 디렉토리 구조

```
src/
├── agents/
│   ├── graph/
│   │   ├── pipeline.py              # 메인 파이프라인 (조건부 에이전틱 코어 라우팅 추가)
│   │   ├── agentic_core.py          # [신규] 에이전틱 코어 서브그래프 정의
│   │   ├── runner.py
│   │   └── instrumented_pipeline.py
│   ├── state/
│   │   ├── state.py                 # PipelineState (기존)
│   │   └── agentic_state.py         # [신규] AgenticCoreState + 서브타입
│   ├── models/
│   │   ├── normalization.py
│   │   ├── response.py
│   │   └── user_messages.py
│   └── nodes/
│       ├── preprocessor.py          # 기존 유지
│       ├── history_resolver.py      # 기존 유지
│       ├── intent_classifier.py     # 기존 유지
│       ├── query_normalizer.py      # 기존 유지
│       ├── context_collector.py     # 기존 유지 (선형 모드용)
│       ├── context_enricher.py      # 기존 유지 (선형 모드용)
│       ├── sql_generator.py         # 기존 유지 (선형 모드용)
│       ├── sql_validator.py         # 기존 유지 (선형 모드용)
│       ├── sql_executor.py          # 기존 유지
│       ├── analyzer.py              # 기존 유지
│       ├── clarifier.py             # 기존 유지
│       ├── formatter.py             # 기존 유지
│       ├── agentic/                 # [신규] 에이전틱 코어 노드
│       │   ├── __init__.py
│       │   ├── planner.py              # planner 노드
│       │   ├── context_explorer.py    # context_explorer 노드 (내부 루프)
│       │   ├── confidence_evaluator.py # confidence_evaluator 노드
│       │   ├── sql_generator.py       # sql_generator 노드 (에이전틱 버전)
│       │   ├── sql_validator.py       # sql_validator 3-레이어
│       │   ├── recovery_planner.py    # recovery_planner 노드
│       │   ├── result_finalizer.py    # result_finalizer 노드
│       │   └── tools.py             # 탐색 도구 래퍼 (기존 서비스 호출)
│       └── prompts/
│           ├── system_prompts.py    # 기존 유지
│           └── agentic_prompts.py   # [신규] planner/recovery_planner/sql_generator 프롬프트
├── services/
│   ├── sql_hint_extractor.py        # [신규] sqlglot 구조적 힌트 추출
│   ├── confidence_scorer.py         # [신규] 확신도 계산
│   ├── exploration_cache.py         # [신규] 외부 캐시 레이어
│   └── ... (기존 서비스 모두 유지)
```

---

## 9. 구현 우선순위

### Phase 1 — 기반 구조 (1주)
- [ ] AgenticCoreState + 서브타입 정의 (agentic_state.py)
- [ ] ExplorationTools Protocol 정의 (의존성 주입 — C-20)
- [ ] exploration_cache.py (CacheStore Protocol + InMemoryCacheStore — C-04)
- [ ] confidence_scorer.py
- [ ] sql_hint_extractor.py (sqlglot 기반)
- [ ] agentic_core.py 그래프 스켈레톤
- [ ] **경계면 변환 단위 테스트** (pipeline_to_agentic, agentic_to_pipeline — C-01~03)

### Phase 2 — 핵심 노드 (1주)
- [ ] planner 노드 + 플래너 프롬프트 (소형 모델 2-Phase fallback 포함 — C-15)
- [ ] context_explorer 노드 + tools.py (기존 서비스 래퍼, search_glossary 포함 — C-06)
- [ ] confidence_evaluator 노드 (rule-based)
- [ ] generate 노드 (에이전틱 버전, 자기검증 체크리스트 — C-05)
- [ ] validate 노드 (Layer 2a rule-based + Layer 2b LLM — C-22)

### Phase 3 — 루프 완성 (1주)
- [ ] replanner 노드 (rule-based fallback, replan→conclude 조건부 엣지 — C-09, C-16)
- [ ] result_finalizer 노드 (상태 역변환, trace_entries 포함 — C-01)
- [ ] Fast-Path 바이패스 + 실패 복구 경로 (validate→explore — C-24)
- [ ] pipeline.py 조건부 라우팅 연결
- [ ] ask_user (checkpointer 기반 중간 상태 저장/재개 — C-25)
- [ ] agentic_entry_node 예외 처리 (C-14)

### Phase 4 — 검증 및 튜닝
- [ ] 골든셋 테스트 (기존 선형 vs 에이전틱 A/B)
- [ ] confidence 임계값 튜닝
- [ ] 레이턴시/비용 벤치마크
- [ ] 에러 핸들링 엣지케이스
- [ ] 소형 모델(7B~70B) 호환성 테스트

---

## 10. 리스크 및 완화 전략

| 리스크 | 영향 | 완화 |
|--------|------|------|
| 에이전틱 코어 레이턴시 | 단순 질의도 탐색 루프 진입 | Fast-Path로 고유사도 매칭 시 즉시 생성 + 실패 시 context_explorer 복귀 (C-24) |
| planner LLM 비용 | 매 요청마다 Heavy LLM 호출 | 정규화 결과 재활용으로 planner 프롬프트 경량화 |
| 소형 모델 planner 품질 | 폐쇄망에서 가설 수립 품질 저하 | 2-Phase 분리 + rule-based 가설 템플릿 fallback (C-15) |
| 소형 모델 sql_validator 품질 | Layer 2 LLM 검증의 false positive/negative | Layer 2a(sqlglot rule-based) + Layer 2b(LLM) 이원화 (C-22) |
| State 체크포인트 비용 | 탐색 결과를 State에 넣으면 폭증 | CacheStore Protocol + result_ref 패턴 (C-04) |
| 기존 테스트 회귀 | 에이전틱 코어 도입 시 기존 테스트 깨짐 | 설정 플래그로 선형/에이전틱 전환, 기존 테스트는 선형 모드로 유지 |
| 경계면 상태 유실 | 서브그래프 진입/탈출 시 필드 누락 | 경계면 단위 테스트 Phase 1 필수 (C-01~03) |
| 사용자 명확화 후 재진입 | 에이전틱 코어 중간 상태 유실 | LangGraph checkpointer 기반 상태 저장/복원 (C-25) |

---

## 부록 A — 폐쇄망 대형 모델 배포 시 전략 변경 사항

> CLAUDE.md에 "GPT-3.5 Turbo급 7B~70B"로 명시되어 있으나,
> 실제 폐쇄망 배포 모델이 **Qwen3.5 397B 또는 GPT OSS 120B+ 급 대형 모델**일
> 가능성이 높아진 상황에 대한 보완 참고자료.

### A-1. 모델 스펙 비교

| 구분 | 기존 가정 (7B~70B) | 실제 후보 (120B~397B) |
|------|---------------------|------------------------|
| 추론 능력 | GPT-3.5 Turbo급 | Claude Sonnet / GPT-4o급 |
| JSON 구조 출력 | 불안정, few-shot 필수 | 안정적, zero-shot 가능 |
| 복합 추론 | 단일 호출로 가설+계획 어려움 | 단일 호출로 충분 |
| 프롬프트 길이 | 4K~8K 제한 | 128K+ 가능 |
| 메타인지 | 취약 (무엇을 모르는지 판단 어려움) | 가능 (planner 핵심 능력) |

### A-2. 전략 변경 매트릭스

| 설계 항목 | 소형 모델 (7B~70B) 전략 | 대형 모델 (120B+) 전략 | 변경 영향 |
|-----------|------------------------|----------------------|-----------|
| **planner** (C-15) | 2-Phase 분리 + rule-based 가설 템플릿 | 단일 LLM 호출로 분해+가설+계획 한 번에 | 프롬프트 단순화, 코드 경량화 |
| **recovery_planner** (C-16) | 가설 큐 소비만, LLM 미사용 | LLM으로 교훈 도출 + 새 가설 수립 | 더 높은 품질의 재계획 |
| **sql_validator Layer 2** (C-22) | Layer 2a(rule-based)만, Layer 2b 스킵 | Layer 2a + Layer 2b(LLM) 모두 활성화 | 의미 검증 정확도 향상 |
| **프롬프트 복잡도** | few-shot 필수, JSON 단순화, priority→string | zero-shot 가능, 중첩 JSON 허용, priority→float | 프롬프트 자유도 증가 |
| **explore_observe** | rule-based 결과 해석 우선 | LLM 결과 해석 적극 활용 (is_critical 판단 포함) | knowledge 품질 향상 |
| **self_check** (generate_sql) | 제거 (소형 모델 무시 가능성) | 활성화 가능 (대형 모델은 자기검증 수행 가능) | SQL 품질 향상 |

### A-3. 설정 기반 분기 (변경 없이 대응)

현재 설계의 fallback 경로는 **설정 플래그로 제어**되므로, 대형 모델 배포 시 코드 변경 없이 설정만 조정하면 됩니다:

```python
# .env 또는 config.py — 대형 모델 배포 시
plan_use_llm: bool = True                    # planner에서 LLM 사용 (True 유지)
validate_layer2b_enabled: bool = True        # Layer 2b LLM 의미 검증 활성화
replan_use_llm: bool = True                  # recovery_planner에서 LLM 사용

# 소형 모델이었다면:
# plan_use_llm: bool = True                  # LLM 사용하되 프롬프트 경량화
# validate_layer2b_enabled: bool = False     # Layer 2a만
# replan_use_llm: bool = False               # 가설 큐 소비만
```

### A-4. 대형 모델에서 추가로 활용 가능한 패턴

대형 모델이 확정되면 소형 모델에서는 불가능했던 다음 패턴을 적극 활용할 수 있습니다:

**1. planner의 Single-Shot 복합 추론**

소형 모델에서는 "분해 → 가설 → 계획"을 단계별로 나눠야 했지만, 대형 모델은 한 번의 호출로 전체를 수행할 수 있습니다. plan_system.txt 프롬프트를 그대로 사용하면 됩니다.

**2. SQL 원문 직접 분석**

소형 모델 대응으로 sqlglot 구조적 힌트를 추출하는 설계를 했지만, 대형 모델은 유사 SQL 원문을 직접 읽고 패턴을 파악할 수 있습니다. 다만 **토큰 비용 관점에서 구조적 힌트가 여전히 효율적**이므로, 힌트 추출은 유지하되 원문도 참고 자료로 함께 제공하는 하이브리드 전략이 가능합니다:

```
유사 SQL 3건 원문 + 구조적 힌트 요약
→ 대형 모델: 둘 다 활용하여 더 정확한 SQL 생성
→ 소형 모델: 힌트만 사용 (원문은 토큰 초과 위험)
```

**3. recovery_planner의 심층 교훈 도출**

대형 모델은 dead_ends + discovered_facts를 종합하여 "왜 실패했는지, 다음에 뭘 다르게 해야 하는지"를 질적으로 분석할 수 있습니다. `_format_replan_prompt()`가 제공하는 구조화된 컨텍스트의 효과가 대형 모델에서 극대화됩니다.

### A-5. 권고: fallback 경로는 유지

대형 모델이 확정되더라도 **rule-based fallback 경로는 제거하지 않는 것을 권장**합니다:

1. **모델 교체 가능성**: 라이선스/비용/GPU 변경으로 소형 모델로 전환될 수 있음
2. **양자화 영향**: 397B를 INT4 양자화하면 추론 품질이 떨어질 수 있음
3. **장애 대응**: LLM 서버 장애 시 rule-based만으로 부분 동작 가능
4. **비용 최적화**: 단순 질의는 rule-based로 처리하여 LLM 호출 절감

fallback 코드가 있다고 해서 런타임 비용이 발생하지 않으므로(설정으로 분기), 보험으로 유지하는 것이 합리적입니다.
