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

from typing import Any, Literal

from pydantic import BaseModel, Field

# ── 공유 모델 re-export (기존 import 경로 호환) ──
from src.models.enums import (  # noqa: F401
    ConfidenceStatus,
    FailureType,
    FinalStatus,
    HypothesisStatus,
    IntentType,
    Phase,
    QueryStatus,
    StepStatus,
    TableSelectionStatus,
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
# 루프 제어 상수 (config.py 설정값 참조)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from src.config import settings as _settings

MAX_TOOL_CALLS = _settings.max_tool_calls
MAX_REPLANS = _settings.max_replans
MAX_GENERATES = _settings.max_generates
MAX_LOCAL_FIXES = _settings.max_local_fixes


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Reason 계층 서브타입
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class KnowledgeItem(BaseModel):
    """탐색 과정에서 축적되는 개별 지식 단위."""

    key: str
    value: str = ""
    confidence: float = 0.0
    status: ConfidenceStatus = ConfidenceStatus.UNRESOLVED
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
    based_on_use_case: str | None = None
    missing_terms: list[str] = Field(default_factory=list)
    priority: float = 0.5
    strategy: str = ""
    status: HypothesisStatus = HypothesisStatus.PENDING


class ExecutionStep(BaseModel):
    """explore 노드의 개별 실행 단위."""

    step: int
    tool: str
    input: str
    purpose: str
    expected_output: str = ""
    status: StepStatus = StepStatus.PENDING
    result_ref: str | None = None
    insight: str | None = None


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


class ColumnInfo(BaseModel):
    """컬럼 상세 정보."""

    name: str
    alt_name: str = ""
    description: str = ""
    col_type: str = ""
    is_pk: bool = False


class CandidateTable(BaseModel):
    """탐색 중 발견된 후보 테이블."""

    # ── 메타 원본 (ES에서 파싱) ──
    table_name: str
    alt_name: str = ""
    description: str = ""
    schema_name: str = ""
    db_source: str = ""
    columns: list[ColumnInfo] = Field(default_factory=list)
    join_keys: list[str] = Field(default_factory=list)

    # ── 관찰 사실 (rule-based, DB 쿼리) ──
    key_date_columns: list[KeyDateColumn] = Field(
        default_factory=list,
    )
    observed_date_columns: list[ObservedDateColumn] = Field(
        default_factory=list,
    )
    sample_rows: list[dict] = Field(default_factory=list)

    # ── LLM 추론 (출처 구분 필요) ──
    inferred_entity_scope: str = ""
    inferred_functional_usage: str = ""
    inferred_data_refresh_hint: str = ""
    inferred_key_date_column: str = ""
    inference_confidence: float = 0.0

    # ── 판정 결과 (batch_interpret 후) ──
    selection_status: TableSelectionStatus = TableSelectionStatus.PENDING
    selection_reason: str = ""

    @classmethod
    def from_meta(cls, meta: dict) -> CandidateTable | None:
        """MongoDB 메타 dict → CandidateTable 변환 팩토리.

        신규 필드(name, description, columns[].name)를 우선 참조하고
        하위 호환을 위해 table_name / table_description도 폴백 지원한다.
        테이블명이 없으면 None을 반환한다.
        """
        table_name = meta.get("name", "") or meta.get("table_name", "")
        if not table_name:
            return None

        raw_cols = meta.get("columns", [])
        col_infos: list[ColumnInfo] = []
        if isinstance(raw_cols, list):
            for c in raw_cols:
                if isinstance(c, dict) and c.get("name"):
                    col_infos.append(ColumnInfo(
                        name=c["name"],
                        alt_name=c.get("alt_name", ""),
                        description=c.get("description", ""),
                        col_type=c.get("type", ""),
                        is_pk=bool(c.get("is_pk")),
                    ))

        from src.connectors.manager import ConnectorManager

        return cls(
            table_name=table_name,
            alt_name=meta.get("alt_name", ""),
            description=(
                meta.get("description")
                or meta.get("table_description", "")
            ),
            schema_name=meta.get("schema_name", ""),
            db_source=ConnectorManager.parse_db_source(table_name),
            columns=col_infos,
        )

    @property
    def qualified_name(self) -> str:
        """스키마명.테이블명 형태를 반환한다. 스키마 없으면 테이블명만."""
        if self.schema_name:
            return f"{self.schema_name}.{self.table_name}"
        return self.table_name


class CodeMeta(BaseModel):
    """코드 컬럼의 코드값 매핑 정보.

    search_code_meta 결과를 컬럼 단위로 저장하여
    sql_generator, recovery_planner 등에서 풍부한 추론에 활용한다.
    """

    column_name: str          # "LOAN_STS_CD"
    column_desc: str = ""     # "대출상태코드"
    codes: dict[str, str] = Field(default_factory=dict)
    # codes 예시: {"01": "정상", "02": "연체", "03": "상환완료"}


class DeadEnd(BaseModel):
    """실패한 탐색 경로 기록."""

    hypothesis_id: str
    failure_type: FailureType = FailureType.NO_USE_CASE
    reason: str = ""
    lessons_learned: str = ""


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
    reason 계층 노드(planner → context_explorer → confidence_evaluator →
    sql_generator → sql_validator → recovery_planner → result_finalizer)가
    사용한다.

    W/R 표기: W=기록(Write), R=참조(Read) 하는 노드.
    약어: PLN=planner, EXP=context_explorer, EVL=confidence_evaluator,
          GEN=sql_generator, VAL=sql_validator, RCV=recovery_planner,
          FIN=result_finalizer
    """

    # ── 진행 상태 ──
    # W: PLN/EXP/EVL/GEN/VAL/RCV/FIN  R: pipeline 라우팅
    phase: Phase = Phase.PLANNING

    # ── 플래너 산출물 ──
    # W: PLN  R: GEN/VAL (SQL 생성 시 체크리스트)
    query_decomposition: dict = Field(default_factory=dict)
    # W: PLN/RCV  R: RCV (PENDING 가설 소비)
    hypotheses: list[Hypothesis] = Field(
        default_factory=list,
    )
    # W: PLN/RCV  R: RCV (FAILED 전환 시)
    current_hypothesis: Hypothesis | None = None
    # W: PLN/RCV  R: EXP (스텝 순차 실행)
    execution_plan: list[ExecutionStep] = Field(
        default_factory=list,
    )

    # ── 누적 지식 ──
    # W: PLN/EXP  R: EVL/GEN/VAL/RCV/FIN
    knowledge_items: list[KnowledgeItem] = Field(
        default_factory=list,
    )
    # W: PLN/EXP (search_use_cases 결과)  R: PLN/GEN/FIN
    explored_use_cases: list[dict] = Field(
        default_factory=list,
    )
    # W: PLN/EXP  R: GEN/VAL/RCV/FIN
    candidate_tables: list[CandidateTable] = Field(
        default_factory=list,
    )
    # W: PLN/EXP  R: PLN/EXP/RCV (중복 검색 방지)
    searched_queries: list[str] = Field(
        default_factory=list,
    )
    # W: EXP  R: RCV (도구 실행 결과 해석 누적)
    discovered_facts: list[str] = Field(
        default_factory=list,
    )
    # W: EXP (search_code_meta)  R: GEN/RCV (코드값 기반 SQL 추론)
    code_map: dict[str, CodeMeta] = Field(
        default_factory=dict,
    )
    # ── 실패 기록 ──
    # W: RCV  R: RCV/GEN/VAL/FIN (반복 방지, 실패 상세)
    dead_ends: list[DeadEnd] = Field(
        default_factory=list,
    )

    # ── SQL ──
    # W: GEN  R: VAL/FIN
    generated_sql: str | None = None
    # W: VAL  R: FIN/pipeline 라우팅
    validated_sql: str | None = None

    # ── SQL 검증 상세 (Layer2b PASS 시 체크 항목별 판정 사유) ──
    # W: VAL(PASS)  R: insight_builder
    # 구조: {"check_name": {"pass": bool, "detail": str}}
    validation_checks: dict[str, Any] = Field(
        default_factory=dict,
    )

    # ── 실패 맥락 (VAL/EVL → pipeline 라우팅/GEN/RCV) ──
    # W: VAL/EVL  R: pipeline 라우팅, GEN(fix 피드백), RCV(DeadEnd)
    failure_type: FailureType | None = None
    # W: VAL/EVL  R: GEN(fix 피드백), RCV(DeadEnd reason)
    failure_reason: str | None = None

    # ── 루프 제어 ──
    # W: EXP/RCV/GEN/VAL  R: EVL (종료 조건), pipeline 라우팅
    loop_guard: LoopGuard = Field(
        default_factory=LoopGuard,
    )

    # ── Fast-Path ──
    # W: PLN  R: pipeline 라우팅 (_route_after_planner)
    fast_path_triggered: bool = False

    # ── 최종 출력 ──
    # W: RCV/FIN  R: pipeline 라우팅
    final_status: FinalStatus = FinalStatus.PENDING
    # W: RCV/FIN  R: 외부 (응답 메시지)
    exploration_summary: str = ""

    # ── 헬퍼 메서드 ──

    def get_confirmed_knowledge(self) -> list[KnowledgeItem]:
        """CONFIRMED 상태인 지식 항목만 반환."""
        return [
            ki for ki in self.knowledge_items
            if ki.status == ConfidenceStatus.CONFIRMED
        ]

    def format_confirmed_text(self) -> str:
        """CONFIRMED 지식 항목을 프롬프트용 텍스트로 직렬화."""
        confirmed = self.get_confirmed_knowledge()
        if not confirmed:
            return "(확인된 항목 없음)"
        return "\n".join(
            f"- {ki.key}: {ki.value} ({ki.source})"
            for ki in confirmed
        )

    def format_dead_ends_text(self) -> str:
        """dead_ends를 프롬프트용 텍스트로 직렬화."""
        if not self.dead_ends:
            return "(없음)"
        lines: list[str] = []
        for de in self.dead_ends:
            line = f"- [{de.failure_type}] {de.reason}"
            if de.lessons_learned:
                line += f"\n  교훈: {de.lessons_learned}"
            lines.append(line)
        return "\n".join(lines)

    def get_unresolved_knowledge(self) -> list[KnowledgeItem]:
        """UNRESOLVED 상태인 지식 항목만 반환."""
        return [
            ki for ki in self.knowledge_items
            if ki.status == ConfidenceStatus.UNRESOLVED
        ]

    def get_pending_hypotheses(self) -> list[Hypothesis]:
        """PENDING 상태인 가설만 반환."""
        return [
            h for h in self.hypotheses
            if h.status == HypothesisStatus.PENDING
        ]


def should_terminate(reason: ReasoningState) -> bool:
    """루프 강제 종료 조건.

    5가지 조건 중 하나라도 충족하면 추론 루프를 종료한다.
    """
    g = reason.loop_guard
    pending = reason.get_pending_hypotheses()
    return (
        g.total_tool_calls >= MAX_TOOL_CALLS      # 도구 호출 총량 한도
        or g.replan_count >= MAX_REPLANS            # 재계획 횟수 한도
        or g.generate_attempts >= MAX_GENERATES     # SQL 생성 시도 한도
        or reason.final_status == FinalStatus.FAILURE  # 명시적 실패 선언
        or (                                        # 가설 소진: 시도할 경로 없음
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

    W/R 표기: W=기록, R=참조 하는 노드/계층.
    약어: PRE=preprocess, HIS=resolve_history, INT=classify_intent,
          NRM=normalize_query, CLR=clarify, EXE=execute_sql,
          ANL=analyze_data, FMT=format_response
    """

    # ── 공통 ──
    # W: runner (초기값)  R: PRE/HIS
    user_input: str = ""
    session_id: str = ""
    # W: runner (초기값)  R: HIS (대화 맥락)
    conversation_history: list[dict[str, str]] = Field(
        default_factory=list,
    )

    # ── Interpret 계층 ──
    # W: PRE  R: INT/NRM/CLR/reason 계층 전체
    preprocessed_input: str = ""
    # W: INT  R: pipeline 라우팅/CLR/NRM
    intent: IntentType = IntentType.UNKNOWN
    # W: INT  R: 로깅
    intent_confidence: float = 0.0
    # W: INT  R: 로깅
    query_category: str = ""
    # W: NRM  R: reason 계층 (planner 시드)
    normalized_query: Any = None
    # W: CLR/result_finalizer  R: pipeline 라우팅/runner
    clarification_question: str = ""
    # W: runner (재진입 시)  R: HIS
    clarification_response: str = ""
    # W: CLR/result_finalizer  R: pipeline 라우팅/runner
    awaiting_clarification: bool = False
    # W: CLR/result_finalizer  R: pipeline 라우팅 (max_turns 체크)
    clarification_turns: int = 0

    # ── Reason 계층 (에이전틱 추론) ──
    # W/R: reason 계층 전체 (상세는 ReasoningState 참조)
    reason: ReasoningState = Field(
        default_factory=ReasoningState,
    )

    # ── Present 계층 ──
    # W: result_finalizer  R: EXE/ANL/FMT
    context: ContextInfo = Field(
        default_factory=ContextInfo,
    )
    # W: EXE  R: ANL/FMT/runner
    sql_result: SQLResult = Field(
        default_factory=SQLResult,
    )
    # W: ANL  R: FMT/runner
    analysis_result: AnalysisResult = Field(
        default_factory=AnalysisResult,
    )
    # W: ANL  R: runner
    visualization: VisualizationData = Field(
        default_factory=VisualizationData,
    )
    # W: FMT/error_end  R: runner (최종 응답)
    formatted_response: str = ""

    # ── 상태 관리 ──
    # W: 각 노드 (에러 시)  R: pipeline 라우팅
    status: QueryStatus = QueryStatus.PENDING
    # W: 각 노드 (에러 시)  R: error_end/runner
    error_message: str = ""

    # ── 추론 추적 로그 ──
    # W: 각 노드 (add_trace)  R: FMT/runner
    trace_log: list[TraceEntry] = Field(
        default_factory=list,
    )
