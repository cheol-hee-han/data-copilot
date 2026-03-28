"""에이전틱 코어 서브그래프 상태 모델.

점진적 탐색 기반 NL-to-SQL 에이전틱 루프의 상태를 정의한다.
메인 PipelineState와 격리되며, 서브그래프 진입/탈출 시 명시적 변환을 수행한다.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 서브타입 정의
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ConfidenceStatus = Literal[
    "UNRESOLVED",   # 0.0~0.3 — 아무것도 모름
    "CANDIDATE",    # 0.3~0.6 — 후보 발견, 미검증
    "PROBABLE",     # 0.6~0.8 — 논리적 추론으로 가능성 높음
    "CONFIRMED",    # 0.8~1.0 — 샘플 또는 실행으로 확인
    "CONFLICTED",   # 여러 소스에서 충돌
]

FailureType = Literal[
    "no_use_case",
    "no_table",
    "term_unresolvable",
    "sql_syntax",
    "sql_semantic_local",
    "sql_structural",
    "empty_result",
    "db_error",
]

ValidationOverall = Literal[
    "SUCCESS",
    "FAIL_SYNTAX",
    "FAIL_SEMANTIC_LOCAL",
    "FAIL_STRUCTURAL",
    "FAIL_EMPTY",
    "FAIL_DB_ERROR",
]

Phase = Literal[
    "PLANNING",
    "EXPLORING",
    "VERIFYING",
    "GENERATING",
    "VALIDATING",
    "REPLANNING",
    "DONE",
]


class KnowledgeItem(BaseModel):
    """탐색 과정에서 축적되는 개별 지식 단위.

    각 지식 항목은 확신 수준(confidence)과 상태(status)를 가지며,
    탐색이 진행될수록 UNRESOLVED → CANDIDATE → PROBABLE → CONFIRMED로 승격된다.
    """

    key: str                                # "취소 상태 표현", "지점코드 컬럼"
    value: str = ""                         # "order_status = 'CANCEL'"
    confidence: float = 0.0                 # 0.0 ~ 1.0
    status: ConfidenceStatus = "UNRESOLVED"
    source: str = ""                        # "코드메타" | "샘플데이터" | "활용사례"
    evidence: list[str] = Field(default_factory=list)

    def promote(self, new_status: ConfidenceStatus, value: str,
                confidence: float, source: str, evidence: str) -> None:
        """지식 항목의 상태를 승격한다."""
        self.status = new_status
        self.value = value
        self.confidence = confidence
        self.source = source
        self.evidence.append(evidence)


class Hypothesis(BaseModel):
    """탐색 가설 — 어떤 접근 방식으로 SQL을 생성할 것인가."""

    hypothesis_id: str                      # "H1", "H2"
    description: str
    based_on_use_case: Optional[str] = None
    required_tables: list[str] = Field(default_factory=list)
    missing_terms: list[str] = Field(default_factory=list)
    priority: float = 0.5                   # 0.0 ~ 1.0
    strategy: str = ""                      # 접근 전략 한 줄 요약
    status: Literal["PENDING", "ACTIVE", "SUCCESS", "FAILED"] = "PENDING"


class ExecutionStep(BaseModel):
    """explore 노드의 개별 실행 단위."""

    step: int
    tool: str                               # "search_use_cases" | "search_table_meta" | ...
    input: str                              # 검색어 또는 파라미터
    purpose: str                            # 이 스텝이 필요한 이유
    expected_output: str = ""
    status: Literal["PENDING", "DONE", "SKIPPED", "FAILED"] = "PENDING"
    result_ref: Optional[str] = None        # 외부 캐시 키
    insight: Optional[str] = None           # 결과로부터 추출한 핵심 관찰


class CandidateTable(BaseModel):
    """탐색 중 발견된 후보 테이블 — 구조 데이터 운반용.

    테이블의 적합성 판단은 knowledge_items에서 수행한다.
    (key="table:{table_name}", status=CONFIRMED 여부로 판단)
    이 모델은 SQL 생성에 필요한 구조 정보(컬럼, 조인키)만 보관한다.
    """

    table_name: str
    role: str = ""                          # "주문일자, 취소여부 보유"
    relevant_columns: list[str] = Field(default_factory=list)
    join_keys: list[str] = Field(default_factory=list)
    missing_coverage: list[str] = Field(default_factory=list)


class DeadEnd(BaseModel):
    """실패한 탐색 경로 기록 — 같은 실패를 반복하지 않기 위한 학습."""

    hypothesis_id: str
    reason: str
    tried_tables: list[str] = Field(default_factory=list)
    tried_terms: list[str] = Field(default_factory=list)
    failure_type: FailureType = "no_use_case"


class LoopGuard(BaseModel):
    """다층 루프 제어 카운터.

    단일 iteration 카운터가 아닌 의미 단위별 카운터로 세밀한 종료 제어.
    """

    total_tool_calls: int = 0               # MAX: 20
    replan_count: int = 0                   # MAX: 3
    generate_attempts: int = 0              # MAX: 4
    local_fix_count: int = 0                # MAX: 2 (초과 시 structural 격상)

    def increment_tool_calls(self) -> None:
        self.total_tool_calls += 1

    def increment_replan(self) -> None:
        self.replan_count += 1

    def increment_generate(self) -> None:
        self.generate_attempts += 1

    def increment_local_fix(self) -> None:
        self.local_fix_count += 1

    def should_escalate_to_structural(self) -> bool:
        """local fix 반복 시 structural로 격상."""
        return self.local_fix_count >= MAX_LOCAL_FIXES


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
    overall: ValidationOverall = "SUCCESS"


class StructuralHints(BaseModel):
    """sqlglot으로 유사 SQL에서 추출한 구조적 힌트.

    LLM에 SQL 원문 대신 파싱된 구조 정보를 제공하여
    토큰 비용을 절감하고 소형 모델 대응력을 높인다.
    """

    join_patterns: list[str] = Field(default_factory=list)
    code_columns: dict[str, list[str]] = Field(default_factory=dict)
    agg_expressions: list[str] = Field(default_factory=list)
    date_filters: list[dict[str, str]] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.join_patterns or self.code_columns
                    or self.agg_expressions or self.date_filters)

    def to_prompt_text(self) -> str:
        """LLM 프롬프트용 압축 텍스트 생성."""
        parts: list[str] = []
        if self.join_patterns:
            parts.append(f"검증된 조인 패턴: {'; '.join(self.join_patterns)}")
        if self.code_columns:
            code_strs = [
                f"{col} IN ({', '.join(repr(v) for v in vals)})"
                for col, vals in self.code_columns.items()
            ]
            parts.append(f"과거 사용된 코드값: {'; '.join(code_strs)}")
        if self.agg_expressions:
            parts.append(f"유사 질의 집계 방식: {', '.join(self.agg_expressions)}")
        if self.date_filters:
            date_strs = [
                f"{df.get('column', '?')} ({df.get('format', '?')} 형식)"
                for df in self.date_filters
            ]
            parts.append(f"날짜 조건: {', '.join(date_strs)}")
        return "\n".join(parts)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 루프 제어 상수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

MAX_TOOL_CALLS = 20
MAX_REPLANS = 3
MAX_GENERATES = 4
MAX_LOCAL_FIXES = 2


def should_terminate(state: AgenticCoreState) -> bool:
    """루프 강제 종료 조건 — 하나라도 해당되면 종료."""
    g = state.loop_guard
    pending_hypotheses = [
        h for h in state.hypotheses if h.status == "PENDING"
    ]
    return (
        g.total_tool_calls >= MAX_TOOL_CALLS
        or g.replan_count >= MAX_REPLANS
        or g.generate_attempts >= MAX_GENERATES
        or (len(pending_hypotheses) == 0 and state.current_hypothesis is None)
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 메인 State
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class AgenticCoreState(BaseModel):
    """에이전틱 코어 서브그래프의 전체 상태.

    메인 PipelineState와 격리되며, 서브그래프 진입 시 입력 필드가 주입되고
    서브그래프 탈출 시 출력 필드가 PipelineState로 역변환된다.
    """

    # ── 입력 (메인 파이프라인에서 주입) ─────────────
    original_query: str = ""
    normalized_query: Any = None        # NormalizedQuery 인스턴스
    intent: str = ""
    conversation_history: list[dict[str, str]] = Field(
        default_factory=list,
    )  # 멀티턴 대화 이력 (C-02)

    # ── 현재 진행 상태 ──────────────────────────────
    phase: Phase = "PLANNING"

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

    # ── 최종 출력 ───────────────────────────────────
    final_status: Literal["success", "failure", "pending"] = "pending"
    exploration_summary: str = ""

    # ── 외부 캐시 참조 ──────────────────────────────
    cache_refs: dict[str, str] = Field(default_factory=dict)

    # ── 사용자 명확화 ──────────────────────────────
    needs_user_input: bool = False
    user_question: str = ""

    # ── 추론 추적 로그 (C-01) ────────────────────
    trace_entries: list[dict[str, str]] = Field(
        default_factory=list,
    )  # 각 노드에서 add_trace()로 기록, 탈출 시 PipelineState.trace_log로 변환

    def get_confirmed_knowledge(self) -> list[KnowledgeItem]:
        """CONFIRMED 상태인 지식 항목만 반환."""
        return [ki for ki in self.knowledge_items if ki.status == "CONFIRMED"]

    def get_unresolved_knowledge(self) -> list[KnowledgeItem]:
        """UNRESOLVED 상태인 지식 항목만 반환."""
        return [ki for ki in self.knowledge_items if ki.status == "UNRESOLVED"]

    def get_pending_hypotheses(self) -> list[Hypothesis]:
        """PENDING 상태인 가설만 반환."""
        return [h for h in self.hypotheses if h.status == "PENDING"]

    def record_dead_end(self, hypothesis: Hypothesis,
                        reason: str, failure_type: FailureType) -> None:
        """현재 가설을 dead-end로 기록."""
        hypothesis.status = "FAILED"
        self.dead_ends.append(DeadEnd(
            hypothesis_id=hypothesis.hypothesis_id,
            reason=reason,
            tried_tables=hypothesis.required_tables,
            tried_terms=hypothesis.missing_terms,
            failure_type=failure_type,
        ))
