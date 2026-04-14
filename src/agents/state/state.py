"""LangGraph 파이프라인 통합 상태 모델.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

3계층(interpret → reason → present) 파이프라인의 전체 상태를 정의한다.
reason 계층의 에이전틱 추론 상태는 ReasoningState로 중첩된다.

핵심 클래스:
    - PipelineState: 파이프라인 전체 공유 상태
    - ReasoningState: 에이전틱 추론 루프 내부 상태
    - KnowledgeItem, Hypothesis, ExecutionStep 등: 추론 서브타입
    - KeyDateColumn, ObservedDateColumn: TableMeta 보조 모델

re-export 대상:
    - IntentType, QueryStatus, VisualizationType (src.models.enums)
    - AnalysisResult, SQLResult (src.models.result)
    - TraceEntry, add_trace (src.models.trace)
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# ── 공유 모델 re-export (기존 import 경로 호환) ──
# PEP 484 explicit re-export 패턴(`X as X`): mypy strict가 아닌 경우에도 의도를 명시.
from src.models.enums import (
    ConfidenceStatus as ConfidenceStatus,
    FailureType as FailureType,
    FinalStatus as FinalStatus,
    HypothesisStatus as HypothesisStatus,
    IntentType as IntentType,
    Phase as Phase,
    QueryStatus as QueryStatus,
    SelectionStatus as SelectionStatus,
    StepStatus as StepStatus,
    TargetDbStatus as TargetDbStatus,
    VisualizationType as VisualizationType,
)
from src.models.result import (
    AnalysisResult as AnalysisResult,
    SQLResult as SQLResult,
    VisualizationData as VisualizationData,
)
from src.models.trace import (
    TraceEntry as TraceEntry,
    add_trace as add_trace,
)
from src.agents.models.clarification import AmbiguitySignal
from src.agents.models.normalization import NormalizedQuery


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
    # reasoning_preparer에서 "K1", "K2" 등으로 채번
    knowledge_id: str = ""

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
    readiness_score: float | None = None
    readiness_verdict: str = ""


class ExecutionStep(BaseModel):
    """explore 노드의 개별 실행 단위."""

    step: int
    tool: str
    input: str
    purpose: str
    status: StepStatus = StepStatus.PENDING
    insight: str | None = None
    raw_result: dict[str, Any] | list | None = None
    # depends_on: int | None = None  # (TODO) 선행 스텝 번호 (None이면 독립 실행)
    # 소속 가설 (저니 뷰 도구호출↔가설 연결용)
    hypothesis_id: str = ""


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
    recent_values: list[str] = Field(default_factory=list)


class ColumnInfo(BaseModel):
    """컬럼 상세 정보.

    메타 원본(name~is_pk)은 MongoDB 테이블 메타에서 파싱되고,
    DB 관찰 결과(total_rows~discovered_values)는 context_retriever에서
    get_column_profile / get_column_values 실행 시 채워진다.
    """

    # ── 메타 원본 ──
    name: str
    alt_name: str = ""
    description: str = ""
    col_type: str = ""
    is_pk: bool = False

    # ── DB 관찰 결과 (context_retriever에서 채움) ──
    # W: RET (get_column_profile)  R: INT/GEN/VAL/RCV
    total_rows: int | None = None
    non_null_count: int | None = None
    null_count: int | None = None
    null_rate: float | None = None
    distinct_count: int | None = None
    min_val: str | None = None
    max_val: str | None = None
    # W: RET (get_column_values)  R: INT/GEN/RCV
    # LIKE 검색으로 확인한 실제 DB 고유값 목록 (중복 제거된 합집합)
    discovered_values: list[str] | None = None


class BizManualEntry(BaseModel):
    """업무 매뉴얼 검색 결과.

    Qdrant biz_manual 컬렉션에서 검색된 개별 매뉴얼 항목.
    context_interpreter에서 질의 관련성을 판정한다.
    """

    biz_manual_id: str = ""          # "bm_001" 등 (fetcher에서 채번)
    content: str = ""
    score: float = 0.0
    source: str = ""             # 검색 쿼리
    point_id: str = ""               # Qdrant point id (페이징 시 exclude 대상)
    source_step: int = 0             # 발견 스텝 번호 (tool_execution_history 크로스 레퍼런스용)
    # 소속 가설 (저니 뷰 연결용)
    hypothesis_id: str = ""
    selection_status: SelectionStatus = SelectionStatus.PENDING
    selection_reason: str = ""


class BizTermEntry(BaseModel):
    """비즈니스 용어 사전 검색 결과.

    MongoDB biz_term 컬렉션에서 검색된 개별 용어 항목.
    context_interpreter에서 질의 관련성을 판정한다.
    """

    biz_term_id: str = ""        # "bt_001" 등 (fetcher에서 채번)
    term: str = ""
    definition: str = ""
    synonyms: list[str] = Field(default_factory=list)
    related_tables: list[str] = Field(default_factory=list)
    source: str = ""             # 검색 쿼리
    source_step: int = 0         # 발견 스텝 번호 (tool_execution_history 크로스 레퍼런스용)
    # 소속 가설 (저니 뷰 연결용)
    hypothesis_id: str = ""
    selection_status: SelectionStatus = SelectionStatus.PENDING
    selection_reason: str = ""


class UseCaseEntry(BaseModel):
    """탐색 중 발견된 유사 SQL 활용사례.

    Qdrant sql_history 검색 결과를 구조화한다.
    context_interpreter에서 질의 관련성을 판정(relevant)하고,
    enrichment 결과(enrichment_tables, enrichment_codes)를 첨부한다.
    """

    id: str = ""
    description: str = ""
    sql: str = ""
    domain: str = ""
    score: float = 0.0

    # ── Qdrant point id (페이징 시 exclude 대상) ──
    point_id: str = ""
    # ── 발견 스텝 번호 (tool_execution_history 크로스 레퍼런스용) ──
    source_step: int = 0
    # 소속 가설 (저니 뷰 연결용)
    hypothesis_id: str = ""

    # ── LLM 판정 (context_interpreter) ──
    relevant: bool = False
    eval_reason: str = ""

    # ── enrichment (context_retriever) ──
    enrichment_tables: list[dict] = Field(default_factory=list)
    enrichment_codes: dict[str, dict] = Field(default_factory=dict)

    model_config = {"extra": "allow"}


class TableMeta(BaseModel):
    """탐색 중 발견된 테이블 엔트리."""

    # ── 메타 원본 (MongoDB에서 파싱) ──
    table_name: str
    alt_name: str = ""
    description: str = ""
    schema_name: str = ""
    # 시스템 코드 (ADW/BDP/CRP 등 target_db_schema_map 키와 일치).
    # ConnectorManager.parse_db_source 가 테이블명 접두사에서 태깅한다.
    db_source: str = ""
    subject_area: str = ""
    source_step: int = 0         # 발견 스텝 번호 (tool_execution_history 크로스 레퍼런스용)
    # 소속 가설 (저니 뷰 연결용)
    hypothesis_id: str = ""
    columns: list[ColumnInfo] = Field(default_factory=list)

    # ── 관찰 사실 (rule-based, DB 쿼리) ──
    key_date_columns: list[KeyDateColumn] = Field(
        default_factory=list,
    )
    observed_date_columns: list[ObservedDateColumn] = Field(
        default_factory=list,
    )
    sample_rows: list[dict] | None = None

    # ── LLM 추론 ──
    inference_confidence: float = 0.0

    # ── 판정 결과 (batch_interpret 후) ──
    selection_status: SelectionStatus = SelectionStatus.PENDING
    selection_reason: str = ""

    @classmethod
    def from_meta(cls, meta: dict) -> TableMeta | None:
        """MongoDB 메타 dict → TableMeta 변환 팩토리.

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
            subject_area=meta.get("subject_area", ""),
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

    lookup_code_meta 결과를 컬럼 단위로 저장하여
    sql_generator, recovery_agent 등에서 풍부한 추론에 활용한다.
    """

    column_name: str          # "LOAN_STS_CD"
    column_desc: str = ""     # "대출상태코드"
    codes: dict[str, str] = Field(default_factory=dict)
    # codes 예시: {"01": "정상", "02": "연체", "03": "상환완료"}


class DeadEnd(BaseModel):
    """실패한 탐색 경로 기록."""

    hypothesis_id: str
    failure_type: FailureType = FailureType.NO_KNOWLEDGE
    reason: str = ""
    lessons_learned: str = ""


class TargetDbDecision(BaseModel):
    """target_db 결정 결과 (단일 진실원).

    readiness_gate가 GENERATING 전이 시 target_db_resolver를 호출하여
    이 객체를 채운다. sql_generator/sql_executor/format_sql 등 모든
    하위 노드는 reason.target_db만 읽고, 결정 근거가 필요하면
    reason.target_db_decision을 참조한다.

    분류:
        - FORCED: settings.target_db_code(시스템코드, 예: ADW/BDP/CRP)로 강제 지정
        - SINGLE: SELECTED 테이블이 단일 시스템 소속
        - AMBIGUOUS: 복수 시스템 혼재 → 사용자 명확화 요청 (자동 선정하지 않음)
        - NO_SELECTION: SELECTED 테이블 없음 → 호출부에서 fail 처리

    decision_rationale은 사용자에게 노출되어 "왜 이 DB를 선택했는지"를
    설명한다 (insight_builder/process_summary_builder가 소비).
    """

    status: TargetDbStatus
    target: str = ""
    chosen_tables: list[str] = Field(default_factory=list)
    dropped_tables: list[tuple[str, str]] = Field(default_factory=list)
    decision_rationale: str = ""


class LoopGuard(BaseModel):
    """다층 루프 제어 카운터.

    에이전틱 추론 루프가 무한 반복에 빠지지 않도록 4가지 차원의 카운터를 관리한다.
    각 카운터가 config.py에 정의된 상한(MAX_TOOL_CALLS 등)에 도달하면
    should_terminate()가 True를 반환하여 루프를 강제 종료시킨다.
    """

    total_tool_calls: int = 0
    replan_count: int = 0
    generate_attempts: int = 0
    local_fix_count: int = 0

    def increment_tool_calls(self) -> None:
        """도구 호출 횟수를 1 증가시킨다."""
        self.total_tool_calls += 1

    def increment_replan(self) -> None:
        """재계획 횟수를 1 증가시킨다."""
        self.replan_count += 1

    def increment_generate(self) -> None:
        """SQL 생성 시도 횟수를 1 증가시킨다."""
        self.generate_attempts += 1

    def increment_local_fix(self) -> None:
        """로컬 수정 횟수를 1 증가시킨다."""
        self.local_fix_count += 1

    def should_escalate_to_structural(self) -> bool:
        """로컬 수정 한도 초과 시 구조적 재계획으로 전환해야 하는지 판정한다."""
        return self.local_fix_count >= MAX_LOCAL_FIXES


class StructuralHints(BaseModel):
    """유사 SQL에서 sqlglot으로 추출한 구조적 힌트.

    활용사례 SQL을 파싱하여 12가지 구조 정보를 저장한다.
    각 노드(reasoning_preparer, sql_generator, sql_validator)에서 필요한 것만 골라 사용한다.
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
        """구조적 힌트가 하나도 추출되지 않았는지 판정한다."""
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
    reason 계층 노드(reasoning_preparer → context_retriever → context_interpreter →
    readiness_gate → sql_generator → sql_validator → recovery_agent →
    result_finalizer)가 사용한다.

    W/R 표기: W=기록(Write), R=참조(Read) 하는 노드.
    약어: PRP=reasoning_preparer, RET=context_retriever, INT=context_interpreter,
          RDG=readiness_gate, GEN=sql_generator, VAL=sql_validator,
          RCV=recovery_agent, FIN=result_finalizer
    """

    # ── 진행 상태 ──
    # W: PRP/FET/INT/RDG/GEN/VAL/RCV/FIN  R: pipeline 라우팅
    phase: Phase = Phase.PLANNING

    # ── 플래너 산출물 ──
    # W: PRP  R: GEN/VAL (SQL 생성 시 체크리스트)
    query_decomposition: dict = Field(default_factory=dict)
    # W: PRP/RCV  R: RCV (PENDING 가설 소비)
    hypotheses: list[Hypothesis] = Field(
        default_factory=list,
    )
    # W: PRP/RCV  R: RCV (FAILED 전환 시)
    current_hypothesis: Hypothesis | None = None
    # W: PRP/RCV  R: FET (스텝 순차 실행)
    execution_plan: list[ExecutionStep] = Field(
        default_factory=list,
    )

    # ── 누적 지식 ──
    # W: PRP/INT/RCV  R: RDG/GEN/VAL/FIN
    knowledge_items: list[KnowledgeItem] = Field(
        default_factory=list,
    )
    # W: PRP/INT (search_use_cases 결과)  R: PRP/GEN/FIN
    explored_use_cases: list[UseCaseEntry] = Field(
        default_factory=list,
    )
    # W: PRP/INT  R: GEN/VAL/RCV/FIN
    explored_tables: list[TableMeta] = Field(
        default_factory=list,
    )
    # W: INT (search_manual 결과 hydrate)  R: GEN/RCV (업무 규정·계수산출식 참조)
    explored_biz_manuals: list[BizManualEntry] = Field(
        default_factory=list,
    )
    # W: INT (search_biz_terms 결과 hydrate)  R: GEN/RCV (용어 해소·매핑)
    explored_biz_terms: list[BizTermEntry] = Field(
        default_factory=list,
    )
    # W: INT (lookup_code_meta 결과 hydrate)  R: GEN/RCV (코드값 기반 SQL 추론)
    explored_codes: dict[str, CodeMeta] = Field(
        default_factory=dict,
    )
    # W: FET  R: PRP/FET (도구 실행 중복 방지, "tool:input" 형식)
    executed_tool_keys: set[str] = Field(
        default_factory=set,
    )
    # W: FET  R: RCV (도구 실행 결과 해석 누적)
    discovered_facts: list[str] = Field(
        default_factory=list,
    )

    # ── 실패 기록 ──
    # W: RCV  R: RCV/GEN/VAL/FIN (반복 방지, 실패 상세)
    dead_ends: list[DeadEnd] = Field(
        default_factory=list,
    )

    # ── 타깃 DB 라우팅 (단일 진실원) ──
    # W: readiness_gate(GENERATING 전이 시 target_db_resolver 호출)
    # R: sql_generator/sql_executor/format_sql 등 모든 DB 접근 지점
    # 빈 문자열이면 아직 결정되지 않음(EXPLORING 단계).
    target_db: str = ""
    # W: readiness_gate(target_db_resolver 결과 기록)
    # R: insight_builder/process_summary_builder (사용자에게 결정 근거 노출)
    target_db_decision: TargetDbDecision | None = None

    # ── SQL ──
    # W: GEN  R: VAL/FIN
    generated_sql: str | None = None
    # W: VAL  R: FIN/pipeline 라우팅
    validated_sql: str | None = None
    # W: GEN  R: FIN/message_store (DB 별도 컬럼 저장)
    # SQL generator가 LLM 응답에서 추출한 SQL 1줄 요약 설명
    sql_explanation: str = ""

    # ── SQL 생성 가정 (재시도 시 덮어쓰기, 최종 성공 시 resolved_signals로 전환) ──
    # W: GEN  R: FIN
    pending_assumptions: list[str] = Field(
        default_factory=list,
    )

    # ── SQL 검증 상세 (Layer2b PASS 시 체크 항목별 판정 사유) ──
    # W: VAL(PASS)  R: insight_builder
    # 구조: {"check_name": {"pass": bool, "detail": str}}
    validation_checks: dict[str, Any] = Field(
        default_factory=dict,
    )

    # ── SQL 검증 총평 (Layer2b PASS/FAIL 시 LLM 종합 판단) ──
    # W: VAL  R: formatter (조회 과정 요약), insight_builder
    validation_summary: str = ""
    # ── SQL 검증 신뢰도 (Layer2b LLM이 검증 총평 기반으로 산출) ──
    # W: VAL(PASS)  R: insight_builder
    confidence_score: float = 0.0

    # ── 실패 맥락 (VAL/RDG → pipeline 라우팅/GEN/RCV) ──
    # W: VAL/RDG  R: pipeline 라우팅, GEN(fix 피드백), RCV(DeadEnd)
    failure_type: FailureType | None = None
    # W: VAL/RDG  R: GEN(fix 피드백), RCV(DeadEnd reason)
    failure_reason: str | None = None
    # W: VAL(local_fix)  R: GEN(재시도 시 이전 시도 전체 표시)
    # generator↔validator 루프에서 이전 fix 시도를 누적 기록
    fix_history: list[str] = Field(default_factory=list)

    # ── 루프 제어 ──
    # W: FET/INT/RCV/GEN/VAL  R: RDG (종료 조건), pipeline 라우팅
    loop_guard: LoopGuard = Field(
        default_factory=LoopGuard,
    )

    # ── Recovery 제어 ──
    # W: PRP (초기화), recovery_agent (갱신)  R: readiness_gate, pipeline 라우팅
    exploration_phase: Literal["initial", "recovery"] = "initial"
    recovery_rounds: int = 0
    recovery_entry_source: Literal[
        "readiness_gate", "sql_validator", "sql_generator", None,
    ] = None
    is_force_generated: bool = False

    # ── 최종 출력 ──
    # W: RCV/FIN  R: pipeline 라우팅
    final_status: FinalStatus = FinalStatus.PENDING
    # W: RCV/FIN  R: 외부 (응답 메시지)
    exploration_summary: str = ""

    # ── 헬퍼 메서드 ──

    def get_confirmed_knowledge(self) -> list[KnowledgeItem]:
        """SQL 생성에 사용 가능한 지식 항목을 반환한다.

        CONFIRMED(도구 증거 확정) + PROBABLE(약한 증거 또는 관행적 추론)을
        모두 포함한다. readiness_gate, recovery_agent와 동일한 기준.
        """
        return [
            ki for ki in self.knowledge_items
            if ki.status in (
                ConfidenceStatus.CONFIRMED, ConfidenceStatus.PROBABLE,
            )
        ]

    def format_confirmed_text(self) -> str:
        """사용 가능한 지식 항목(CONFIRMED/PROBABLE)을 프롬프트용으로 직렬화.

        각 항목 끝에 "— 확정" 또는 "— 추정" 태그를 붙여 근거 강도를 표시한다.
        """
        items = self.get_confirmed_knowledge()
        if not items:
            return "(사용 가능한 지식 항목 없음)"
        return "\n".join(
            f"- {ki.key}: {ki.value} ({ki.source}) — "
            f"{'확정' if ki.status == ConfidenceStatus.CONFIRMED else '추정'}"
            for ki in items
        )

    def format_dead_ends_text(self) -> str:
        """dead_ends를 프롬프트용 텍스트로 직렬화."""
        if not self.dead_ends:
            return "(없음)"
        lines: list[str] = []
        for de in self.dead_ends:
            # Python 3.12에서 str Enum f-string이 "FailureType.X"를 출력하므로
            # 명시적으로 .value를 사용한다. 대괄호는 프롬프트의 [ROLE]/[TASK] 등
            # 섹션 헤더와 혼동될 수 있어 백틱으로 감싼다.
            line = f"- `{de.failure_type.value}` {de.reason}"
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
    """추론 루프의 강제 종료 조건을 판정한다.

    도구 호출 총량, 재계획 횟수, SQL 생성 시도, 명시적 실패, 가설 소진 등
    5가지 조건 중 하나라도 충족하면 True를 반환하여 루프를 종료시킨다.
    pipeline.py의 조건부 엣지에서 호출되어 라우팅을 결정한다.
    """
    g = reason.loop_guard
    pending = reason.get_pending_hypotheses()
    return (
        g.total_tool_calls >= MAX_TOOL_CALLS      # 도구 호출 총량 한도
        or g.replan_count >= MAX_REPLANS            # 재계획 횟수 한도
        or (MAX_GENERATES > 0 and g.generate_attempts >= MAX_GENERATES)  # SQL 생성 시도 한도 (0=무제한)
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
    약어: PRE=preprocess, CTX=intent_classifier,
          NRM=normalize_query, CLR=clarify, EXE=execute_sql,
          ANL=analyze_data, FMT=format_response
    """

    # ── 공통 ──
    # W: runner (초기값)  R: HIS
    user_input: str = ""
    session_id: str = ""
    # W: runner (초기값, immutable)  R: 감사 추적, 복귀 노드 프롬프트
    original_query: str = ""
    # W: runner (초기값)  R: HIS (대화 맥락)
    conversation_history: list[dict[str, str]] = Field(
        default_factory=list,
    )

    # ── 턴 격리 ──
    # W: runner (매 턴 uuid4 생성)  R: clarification_context, pipeline 라우팅
    turn_id: str = ""

    # ── Interpret 계층 ──
    # W: runner (sanitize 후)  R: CTX/NRM/reason 계층 전체
    preprocessed_input: str = ""
    # W: intent_classifier (DATA_ANALYSIS 시 rewriter 입력 보관,
    #    CONTINUE 시 맥락 해소 후 질의)
    # R: analyzer (시각화/분석 지시 참조)
    analysis_query: str = ""
    # W: CTX  R: pipeline 라우팅/NRM
    intent: IntentType = IntentType.UNKNOWN
    # W: CTX  R: 로깅
    intent_confidence: float = 0.0
    # W: CTX  R: 로깅
    query_category: str = ""
    # W: CTX  R: 하류 노드 (CONTINUE 시 맥락 힌트)
    is_continuation: bool = False
    # W: CTX  R: 하류 노드 (CONTINUE 시 대화 맥락 반영 질문 해석)
    continue_context: str = ""
    # W: NRM  R: reason 계층 (reasoning_preparer 시드)
    normalized_query: NormalizedQuery | None = None

    # ── Unified Clarification (2계층 판정) ──
    # 현재 턴의 미처리 시그널 — 노드가 반환, clarification_handler가 소비 후 [] 로 비움
    # 일반 필드 (reducer 없음, 덮어쓰기)
    pending_signals: list[AmbiguitySignal] = Field(
        default_factory=list,
    )
    # 처리 완료된 시그널 누적 — ASK(answer 채워짐) + INFER 모두 append
    # 호출 측 누적 패턴: [*state.resolved_signals, new]. turn_reset이 턴 경계에서 []로 초기화.
    resolved_signals: list[AmbiguitySignal] = Field(default_factory=list)

    # ── Reason 계층 (에이전틱 추론) ──
    # W/R: reason 계층 전체 (상세는 ReasoningState 참조)
    reason: ReasoningState = Field(
        default_factory=ReasoningState,
    )

    # ── Present 계층 ──
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
    # W: FMT  R: runner (stream.end 전송, 턴 metadata 저장)
    result_data: dict[str, Any] | None = None
    # W: FMT  R: runner (stream.end 전송, 턴 metadata 저장)
    process_summary: dict[str, Any] | None = None

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

    # ── 턴 경계 리셋 헬퍼 ──

    @classmethod
    def turn_reset_updates(cls) -> dict[str, Any]:
        """턴 경계에서 이전 턴 산출물을 초기화하기 위한 updates dict.

        LangGraph checkpointer가 같은 thread_id에서 상태를 유지하므로,
        새 턴 시작 시 턴 스코프 필드를 명시적으로 초기값으로 덮어써야
        이전 턴 데이터 누출을 막을 수 있다. 이 메서드는 그 단일 진실
        공급원이다. 본 리스트에 없는 필드가 새로 추가되면 리셋 여부를
        반드시 판단해야 한다.

        포함 — 턴 스코프 19개 필드 (resolved_signals 포함).

        제외 — 세션 지속 6개 필드
        (session_id/conversation_history/user_input/original_query/
        preprocessed_input/turn_id)는 runner의 initial_state가 담당하며,
        여기서 건드리지 않는다.
        """
        return {
            # interpret 계층 산출물
            "analysis_query": "",
            "intent": IntentType.UNKNOWN,
            "intent_confidence": 0.0,
            "query_category": "",
            "is_continuation": False,
            "continue_context": "",
            "normalized_query": None,
            "pending_signals": [],
            "resolved_signals": [],
            # reason 계층 통째 교체 (내부 서브필드 모두 기본값)
            "reason": ReasoningState(),
            # present 계층 산출물
            "sql_result": SQLResult(),
            "analysis_result": AnalysisResult(),
            "visualization": VisualizationData(),
            "formatted_response": "",
            "result_data": None,
            "process_summary": None,
            # 상태/로그
            "status": QueryStatus.PENDING,
            "error_message": "",
            "trace_log": [],
        }
