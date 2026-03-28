"""LangGraph 파이프라인 통합 상태 모델.

3계층(interpret → reason → present) 파이프라인의 전체 상태를 정의한다.
reason 계층의 에이전틱 추론 상태는 ReasoningState로 중첩된다.

핵심 클래스:
    - PipelineState: 파이프라인 전체 공유 상태
    - ReasoningState: 에이전틱 추론 루프 내부 상태
    - KnowledgeItem, Hypothesis, ExecutionStep 등: 추론 서브타입
    - KeyDateColumn, ObservedDateColumn: CandidateTable 보조 모델

re-export 대상:
    - IntentType, QueryStatus, VisualizationType (src.models.enums)
    - ColumnMeta, ContextInfo, TableMeta (src.models.context)
    - AnalysisResult, SQLResult (src.models.result)
    - TraceEntry, add_trace, format_trace_summary (src.models.trace)
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

# ── 공유 모델 re-export (기존 import 경로 호환) ──
from src.models.enums import (  # noqa: F401
    IntentType,
    QueryStatus,
    VisualizationType,
)
from src.models.context import (  # noqa: F401
    ColumnMeta,
    ContextInfo,
    TableMeta,
)
from src.models.result import (  # noqa: F401
    AnalysisResult,
    SQLResult,
    VisualizationData,
)
from src.models.trace import (  # noqa: F401
    TraceEntry,
    add_trace,
    format_trace_summary,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 타입 별칭
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ConfidenceStatus = Literal[
    "UNRESOLVED",
    "CANDIDATE",
    "PROBABLE",
    "CONFIRMED",
    "CONFLICTED",
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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 루프 제어 상수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

MAX_TOOL_CALLS = 20
MAX_REPLANS = 3
MAX_GENERATES = 4
MAX_LOCAL_FIXES = 2


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Reason 계층 서브타입
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class KnowledgeItem(BaseModel):
    """탐색 과정에서 축적되는 개별 지식 단위."""

    key: str
    value: str = ""
    confidence: float = 0.0
    status: ConfidenceStatus = "UNRESOLVED"
    source: str = ""
    evidence: list[str] = Field(default_factory=list)
    is_critical: bool = True

    def promote(
        self, new_status: ConfidenceStatus, value: str,
        confidence: float, source: str, evidence: str,
    ) -> None:
        """지식 항목의 상태를 승격한다."""
        self.status = new_status
        self.value = value
        self.confidence = confidence
        self.source = source
        self.evidence.append(evidence)


class Hypothesis(BaseModel):
    """탐색 가설."""

    hypothesis_id: str
    description: str = ""
    based_on_use_case: Optional[str] = None
    missing_terms: list[str] = Field(default_factory=list)
    priority: float = 0.5
    strategy: str = ""
    status: Literal[
        "PENDING", "ACTIVE", "SUCCESS", "FAILED",
    ] = "PENDING"


class ExecutionStep(BaseModel):
    """explore 노드의 개별 실행 단위."""

    step: int
    tool: str
    input: str
    purpose: str
    expected_output: str = ""
    status: Literal[
        "PENDING", "DONE", "SKIPPED", "FAILED",
    ] = "PENDING"
    result_ref: Optional[str] = None
    insight: Optional[str] = None


class KeyDateColumn(BaseModel):
    """기준 날짜 컬럼 — rule-based로 식별된 날짜 기준 컬럼 정보."""

    column_name: str
    suffix: str = ""  # "YMD", "YM", "YY", "DT"
    source: Literal[
        "pk_rule", "alt_name_rule", "llm_fallback",
    ] = "pk_rule"


class ObservedDateColumn(BaseModel):
    """날짜 컬럼의 분포 관찰 결과 — rule-based로 검증 가능한 사실."""

    column_name: str
    date_range: str = ""
    date_pattern: str = ""


class CandidateTable(BaseModel):
    """탐색 중 발견된 후보 테이블."""

    table_name: str
    schema_name: str = ""
    db_source: str = ""
    role: str = ""
    relevant_columns: list[str] = Field(default_factory=list)
    column_alt_names: dict[str, str] = Field(default_factory=dict)
    join_keys: list[str] = Field(default_factory=list)
    missing_coverage: list[str] = Field(default_factory=list)

    # 기준 컬럼 (rule-based 식별)
    key_date_columns: list[KeyDateColumn] = Field(
        default_factory=list,
    )

    # 관찰 사실 (rule-based, 검증 가능)
    observed_date_columns: list[ObservedDateColumn] = Field(
        default_factory=list,
    )
    sample_rows: list[dict] = Field(default_factory=list)

    # LLM 추론 (출처 태그 부착하여 비교 프롬프트에도 전달)
    inferred_entity_scope: str = ""
    inferred_functional_usage: str = ""
    inferred_data_refresh_hint: str = ""
    inferred_key_date_column: str = ""
    inference_confidence: float = 0.0

    @property
    def qualified_name(self) -> str:
        """스키마명.테이블명 형태를 반환한다. 스키마 없으면 테이블명만."""
        if self.schema_name:
            return f"{self.schema_name}.{self.table_name}"
        return self.table_name


class DeadEnd(BaseModel):
    """실패한 탐색 경로 기록."""

    hypothesis_id: str
    reason: str
    tried_tables: list[str] = Field(default_factory=list)
    # 부적합 판정으로 제외된 테이블
    rejected_tables: list[str] = Field(default_factory=list)
    tried_terms: list[str] = Field(default_factory=list)
    failure_type: FailureType = "no_use_case"


class ColumnMapping(BaseModel):
    """질의 필요 정보 ↔ 테이블 컬럼 매핑."""

    need: str = ""
    table: str = ""
    column: str = ""
    confidence: str = "추정"


class TableResolution(BaseModel):
    """테이블 충족성 검증 결과."""

    can_resolve: bool = False
    column_mapping: list[ColumnMapping] = Field(
        default_factory=list,
    )
    missing_info: list[str] = Field(
        default_factory=list,
    )
    join_needed: bool = False
    join_path: str = ""
    main_table: str = ""
    reasoning: str = ""


class LoopGuard(BaseModel):
    """다층 루프 제어 카운터."""

    total_tool_calls: int = 0
    replan_count: int = 0
    generate_attempts: int = 0
    local_fix_count: int = 0

    def increment_tool_calls(self) -> None:
        self.total_tool_calls += 1

    def increment_replan(self) -> None:
        self.replan_count += 1

    def increment_generate(self) -> None:
        self.generate_attempts += 1

    def increment_local_fix(self) -> None:
        self.local_fix_count += 1

    def should_escalate_to_structural(self) -> bool:
        return self.local_fix_count >= MAX_LOCAL_FIXES


class SqlValidationResult(BaseModel):
    """3-레이어 SQL 검증 결과."""

    layer1_status: Literal["PASS", "FAIL", "SKIP"] = "SKIP"
    layer2_status: Literal["PASS", "FAIL", "SKIP"] = "SKIP"
    layer2_passed: list[str] = Field(default_factory=list)
    layer2_failed: list[str] = Field(default_factory=list)
    layer2_failure_type: Optional[
        Literal["semantic_local", "structural"]
    ] = None
    layer3_status: Literal["PASS", "FAIL", "SKIP"] = "SKIP"
    layer3_row_count: Optional[int] = None
    layer3_is_sane: Optional[bool] = None
    overall: ValidationOverall = "SUCCESS"


class StructuralHints(BaseModel):
    """유사 SQL에서 sqlglot으로 추출한 구조적 힌트.

    활용사례 SQL을 파싱하여 12가지 구조 정보를 저장한다.
    각 노드(planner, sql_generator, sql_validator)에서 필요한 것만 골라 사용한다.
    """

    # ── 기존 4가지 ──
    join_patterns: list[str] = Field(default_factory=list)
    code_columns: dict[str, list[str]] = Field(
        default_factory=dict,
    )
    agg_expressions: list[str] = Field(default_factory=list)
    date_filters: list[dict[str, str]] = Field(
        default_factory=list,
    )

    # ── 신규: 테이블 정보 ──
    source_tables: list[str] = Field(default_factory=list)

    # ── 신규: SELECT 출력 구조 ──
    select_columns: list[str] = Field(default_factory=list)
    group_by_columns: list[str] = Field(default_factory=list)
    order_by_columns: list[str] = Field(default_factory=list)
    limit_value: int | None = None
    has_distinct: bool = False
    has_subquery: bool = False
    has_having: bool = False

    def is_empty(self) -> bool:
        return not (
            self.join_patterns or self.code_columns
            or self.agg_expressions or self.date_filters
            or self.source_tables
        )

    def to_prompt_text(self) -> str:
        """LLM 프롬프트용 압축 텍스트 (sql_generator에서 사용)."""
        parts: list[str] = []
        if self.source_tables:
            parts.append(
                f"활용사례 테이블: "
                f"{', '.join(self.source_tables)}",
            )
        if self.join_patterns:
            parts.append(
                f"검증된 조인 패턴: "
                f"{'; '.join(self.join_patterns)}",
            )
        if self.code_columns:
            code_strs = [
                f"{col} IN "
                f"({', '.join(repr(v) for v in vals)})"
                for col, vals in self.code_columns.items()
            ]
            parts.append(
                f"과거 사용된 코드값: "
                f"{'; '.join(code_strs)}",
            )
        if self.agg_expressions:
            parts.append(
                f"유사 질의 집계 방식: "
                f"{', '.join(self.agg_expressions)}",
            )
        if self.select_columns:
            parts.append(
                f"유사 질의 출력 컬럼: "
                f"{', '.join(self.select_columns)}",
            )
        if self.group_by_columns:
            parts.append(
                f"유사 질의 GROUP BY: "
                f"{', '.join(self.group_by_columns)}",
            )
        if self.date_filters:
            date_strs = [
                f"{df.get('column', '?')} "
                f"({df.get('format', '?')} 형식)"
                for df in self.date_filters
            ]
            parts.append(
                f"날짜 조건: {', '.join(date_strs)}",
            )
        return "\n".join(parts)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ReasoningState — 에이전틱 추론 루프 상태
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class ReasoningState(BaseModel):
    """에이전틱 추론 루프의 내부 상태.

    PipelineState.reason 필드로 중첩되며,
    reason 계층 노드(planner, explorer, generator 등)가 사용한다.
    """

    # ── 진행 상태 ──
    phase: Phase = "PLANNING"

    # ── 플래너 산출물 ──
    query_decomposition: dict = Field(default_factory=dict)
    hypotheses: list[Hypothesis] = Field(
        default_factory=list,
    )
    current_hypothesis: Optional[Hypothesis] = None
    execution_plan: list[ExecutionStep] = Field(
        default_factory=list,
    )
    current_step_index: int = 0

    # ── 누적 지식 ──
    knowledge_items: list[KnowledgeItem] = Field(
        default_factory=list,
    )
    explored_use_cases: list[dict] = Field(
        default_factory=list,
    )
    candidate_tables: list[CandidateTable] = Field(
        default_factory=list,
    )
    confirmed_join_path: list[dict] = Field(
        default_factory=list,
    )
    table_resolution: Optional[TableResolution] = None
    searched_queries: list[str] = Field(
        default_factory=list,
    )
    sampled_tables: list[str] = Field(
        default_factory=list,
    )
    # 배치 해석에서 부적합 판정된 테이블명 목록
    rejected_tables: list[str] = Field(
        default_factory=list,
    )
    structural_hints: StructuralHints = Field(
        default_factory=StructuralHints,
    )

    # ── 실패 기록 ──
    dead_ends: list[DeadEnd] = Field(
        default_factory=list,
    )

    # ── SQL ──
    generated_sql: Optional[str] = None
    validated_sql: Optional[str] = None
    sql_fix_instruction: Optional[str] = None
    sql_validation_result: Optional[SqlValidationResult] = None

    # ── 루프 제어 ──
    loop_guard: LoopGuard = Field(
        default_factory=LoopGuard,
    )

    # ── Fast-Path ──
    fast_path_triggered: bool = False

    # ── 최종 출력 ──
    final_status: Literal[
        "success", "failure", "pending",
    ] = "pending"
    exploration_summary: str = ""

    # ── 외부 캐시 참조 ──
    cache_refs: dict[str, str] = Field(
        default_factory=dict,
    )

    # ── 헬퍼 메서드 ──
    def get_confirmed_knowledge(self) -> list[KnowledgeItem]:
        """CONFIRMED 상태인 지식 항목만 반환."""
        return [
            ki for ki in self.knowledge_items
            if ki.status == "CONFIRMED"
        ]

    def get_unresolved_knowledge(self) -> list[KnowledgeItem]:
        """UNRESOLVED 상태인 지식 항목만 반환."""
        return [
            ki for ki in self.knowledge_items
            if ki.status == "UNRESOLVED"
        ]

    def get_pending_hypotheses(self) -> list[Hypothesis]:
        """PENDING 상태인 가설만 반환."""
        return [
            h for h in self.hypotheses
            if h.status == "PENDING"
        ]


def should_terminate(reason: ReasoningState) -> bool:
    """루프 강제 종료 조건."""
    g = reason.loop_guard
    pending = reason.get_pending_hypotheses()
    return (
        g.total_tool_calls >= MAX_TOOL_CALLS
        or g.replan_count >= MAX_REPLANS
        or g.generate_attempts >= MAX_GENERATES
        or reason.final_status == "failure"
        or (
            len(pending) == 0
            and reason.current_hypothesis is None
        )
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PipelineState — 파이프라인 전체 통합 상태
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class PipelineState(BaseModel):
    """LangGraph 파이프라인 전체 통합 상태.

    3계층(interpret → reason → present)의 모든 데이터를 보유한다.
    reason 계층의 에이전틱 추론 상태는 reason 필드에 중첩된다.
    """

    # ── 공통 ──
    user_input: str = ""
    session_id: str = ""
    conversation_history: list[dict[str, str]] = Field(
        default_factory=list,
    )

    # ── Interpret 계층 ──
    preprocessed_input: str = ""
    intent: IntentType = IntentType.UNKNOWN
    intent_confidence: float = 0.0
    query_category: str = ""
    normalized_query: Any = None
    clarification_question: str = ""
    clarification_response: str = ""
    awaiting_clarification: bool = False
    clarification_turns: int = 0

    # ── Reason 계층 (에이전틱 추론) ──
    reason: ReasoningState = Field(
        default_factory=ReasoningState,
    )

    # ── Present 계층 ──
    context: ContextInfo = Field(
        default_factory=ContextInfo,
    )
    sql_result: SQLResult = Field(
        default_factory=SQLResult,
    )
    analysis_result: AnalysisResult = Field(
        default_factory=AnalysisResult,
    )
    visualization: VisualizationData = Field(
        default_factory=VisualizationData,
    )
    formatted_response: str = ""

    # ── 상태 관리 ──
    status: QueryStatus = QueryStatus.PENDING
    error_message: str = ""

    # ── 추론 추적 로그 ──
    trace_log: list[TraceEntry] = Field(
        default_factory=list,
    )
