"""파이프라인 트레이스 데이터 모델 — Pydantic 기반 직렬화 스키마.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

단일 파이프라인 실행의 전체 텔레메트리를 구조화된 Pydantic 모델로
정의한다. ``DataCopilotCallbackHandler``가 런타임에 이 모델들을
채워 넣고, JSON 직렬화하여 트레이스 파일로 저장한다.

Pydantic을 사용하는 이유: 트레이스 JSON의 스키마를 명시적으로 정의하여
직렬화/역직렬화 안정성을 보장하고, visualizer·trace_analyzer 등
소비자가 일관된 구조에 의존할 수 있도록 한다.

모델 구조:
    - EvaluationTrace: 최상위 컨테이너 (run_id, 요약 통계, 하위 기록 리스트)
    - TimelineEntry: 통합 타임라인 — 모든 이벤트를 순번(seq)으로 정렬
    - NodeRecord: 노드 시작/종료 + 입출력 요약
    - LLMCallRecord: LLM 호출별 프롬프트/응답/토큰/지연
    - DecisionRecord: 에이전트 의사결정과 근거
    - ContextRetrievalRecord: 컨텍스트 소스별 검색 결과
    - SQLRecord: SQL 생성/검증/실행 라이프사이클
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from src.utils.timezone import now_stamp


class TimelineEntry(BaseModel):
    """통합 타임라인 엔트리 — 모든 이벤트를 실행 순서대로 기록."""

    seq: int                          # 글로벌 순번 (1부터)
    event_type: str                    # node_start | node_end
                                       # llm_call | tool_call
                                       # decision
    node: str                          # 소속 노드 이름
    parent_seq: int | None = None      # 부모 node_start seq
    summary: str = ""                  # 한 줄 요약
    detail: dict[str, Any] = Field(
        default_factory=dict,
    )
    duration_ms: float = 0.0
    status: str = ""                   # success | error | skipped
    timestamp: str = Field(
        default_factory=now_stamp,
    )


class LLMCallRecord(BaseModel):
    """LLM 호출 기록."""

    node: str
    prompt_summary: str = ""  # 프롬프트 요약 (전체 저장은 토큰 낭비)
    prompt_variables: dict[str, str] = Field(default_factory=dict)
    prompt_tokens: int = 0
    response_text: str = ""
    response_tokens: int = 0
    model: str = ""
    latency_ms: float = 0.0
    timestamp: str = Field(
        default_factory=now_stamp,
    )


class NodeRecord(BaseModel):
    """노드 실행 기록."""

    node: str
    input_summary: dict[str, Any] = Field(default_factory=dict)
    output_summary: dict[str, Any] = Field(default_factory=dict)
    duration_ms: float = 0.0
    status: str = "success"  # success, error, skipped
    error_message: str = ""
    timestamp: str = Field(
        default_factory=now_stamp,
    )


class DecisionRecord(BaseModel):
    """의사결정 기록 — 에이전트가 선택한 판단과 그 근거."""

    node: str
    decision_type: str  # intent_classification, table_selection, routing, ...
    chosen: str  # 선택된 값
    alternatives: list[str] = Field(default_factory=list)  # 후보군
    confidence: float = 0.0
    reason: str = ""  # 선택 근거
    detail: dict[str, Any] = Field(
        default_factory=dict,
    )  # 판단 재료 (확정 지식, 미확정 지식, 후보 테이블 등)
    timestamp: str = Field(
        default_factory=now_stamp,
    )


class ContextRetrievalRecord(BaseModel):
    """컨텍스트 수집 기록."""

    source: str  # es_meta, es_report, history_sql, qdrant_manual, domain_dict
    query: str = ""
    results_count: int = 0
    results_summary: list[str] = Field(default_factory=list)
    latency_ms: float = 0.0
    timestamp: str = Field(
        default_factory=now_stamp,
    )


class SQLRecord(BaseModel):
    """SQL 생성/검증/실행 기록."""

    generated_sql: str = ""
    validated: bool = False
    validation_errors: list[str] = Field(default_factory=list)
    retry_count: int = 0
    validation_feedback: str = ""
    execution_success: bool = False
    row_count: int = 0
    execution_time_ms: float = 0.0


class RoutingDecision(BaseModel):
    """LLM/Rule 판단 후 엣지 결정."""

    next_node: str = ""                    # 다음 노드 이름
    reason: str = ""                       # "NEW+DATA_EXTRACTION → 정규화 진행"
    is_retry: bool = False                 # 재시도 여부
    retry_count: int = 0                   # 몇 번째 재시도


class ReasoningStep(BaseModel):
    """에이전트 사고 흐름의 단일 단계."""

    seq: int                               # 글로벌 순번 (1부터)
    node: str                              # 노드 이름
    phase: str                             # interpret | reason | present
    round: int = 0                         # 가설 라운드 (0=초기, 1+=복구)
    hypothesis_id: str = ""                # H1, H2, ...
    step_type: str = ""                    # llm_decision | rule_decision
                                           # | tool_execution | validation
                                           # | recovery | analysis

    # 입력 요약 (사람이 읽을 수 있는 수준으로 압축)
    inputs: dict[str, Any] = Field(default_factory=dict)

    # LLM/Rule 판단 결과
    output: dict[str, Any] = Field(default_factory=dict)

    # 라우팅
    routing: RoutingDecision = Field(
        default_factory=RoutingDecision,
    )

    # 메타
    duration_ms: float = 0.0
    model: str = ""                        # LLM 모델 (rule-based면 빈 문자열)
    tokens: int = 0                        # 총 토큰
    timestamp: str = Field(
        default_factory=now_stamp,
    )


class EvaluationTrace(BaseModel):
    """단일 파이프라인 실행의 전체 트레이스."""

    run_id: str
    user_input: str = ""
    session_id: str = ""
    start_time: str = ""
    end_time: str = ""
    total_duration_ms: float = 0.0

    # 최종 결과
    final_intent: str = ""
    final_status: str = ""
    final_response_summary: str = ""
    error_message: str = ""

    # 상세 기록
    nodes: list[NodeRecord] = Field(default_factory=list)
    llm_calls: list[LLMCallRecord] = Field(
        default_factory=list,
    )
    decisions: list[DecisionRecord] = Field(
        default_factory=list,
    )
    context_retrievals: list[ContextRetrievalRecord] = Field(
        default_factory=list,
    )
    sql: SQLRecord = Field(default_factory=SQLRecord)

    # 통합 타임라인 (실행 순서 재현용)
    timeline: list[TimelineEntry] = Field(
        default_factory=list,
    )

    # 추론 흐름 (사람이 읽을 수 있는 사고 과정 추적)
    reasoning_flow: list[ReasoningStep] = Field(
        default_factory=list,
    )

    # 요약 통계
    total_llm_calls: int = 0
    total_llm_latency_ms: float = 0.0
    total_llm_tokens: int = 0
    node_path: list[str] = Field(
        default_factory=list,
    )


