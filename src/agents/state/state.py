"""LangGraph 파이프라인 공유 상태 모델 — 전 노드가 읽고 쓰는 중앙 상태 정의.

파이프라인의 모든 노드가 공유하는 PipelineState 를 정의하며,
입력·전처리·의도분류·정규화·컨텍스트·SQL 생성/검증·실행결과·분석·응답 등
파이프라인 전 단계의 데이터를 하나의 Pydantic 모델에 통합한다.
공유 데이터 모델(IntentType, QueryStatus, ContextInfo, SQLResult 등)은
src.models 패키지에 정의되어 있으며, 기존 import 경로 호환을 위해 re-export 한다.

핵심 클래스:
    - PipelineState: 파이프라인 전체 공유 상태 (Pydantic BaseModel)

re-export 대상:
    - IntentType, QueryStatus, VisualizationType (src.models.enums)
    - ColumnMeta, ContextInfo, TableMeta (src.models.context)
    - AnalysisResult, SQLResult (src.models.result)
    - TraceEntry, add_trace, format_trace_summary (src.models.trace)

멀티턴 명확화:
    - awaiting_clarification, clarification_response, clarification_turns 필드로
      최대 2회 왕복의 명확화 흐름을 관리한다.

SQL 재생성 루프:
    - sql_retry_count, validation_feedback 필드로 최대 2회 재생성을 제어한다.
"""

from __future__ import annotations

from typing import Any

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


class PipelineState(BaseModel):
    """LangGraph 파이프라인 전체 공유 상태.

    모든 노드가 이 상태를 읽고 쓰며 파이프라인을 진행한다.

    멀티턴 명확화 흐름:
        1. clarify_node 가 awaiting_clarification=True 로 설정하고 END
        2. 챗봇 레이어가 사용자 응답을 clarification_response 에 채워 재진입
        3. preprocess_node 가 clarification_response 를 감지, user_input 을
           "[원래 질의] + [명확화 응답]" 으로 합성한 뒤 awaiting_clarification=False 로 전환
        4. 이후 파이프라인이 정상 흐름으로 재실행됨

    SQL 재생성 루프:
        - sql_retry_count 가 SQL_MAX_RETRY(2) 미만이면
          validate_sql → generate_sql 루프
        - validation_feedback 에 검증 오류 내용을 담아
          generate_sql_node 에서 프롬프트 보강
    """

    # 입력
    user_input: str = ""
    session_id: str = ""
    conversation_history: list[dict[str, str]] = Field(default_factory=list)

    # 전처리 결과
    preprocessed_input: str = ""

    # 의도 분류
    intent: IntentType = IntentType.UNKNOWN
    intent_confidence: float = 0.0
    query_category: str = ""  # Intent Gate 카테고리 (DATA_QUERY, CASUAL_TALK, ...)

    # 질의 정규화 (8-Slot)
    normalized_query: Any = None  # NormalizedQuery 인스턴스 (순환 import 방지로 Any)

    # 명확화 (멀티턴)
    clarification_question: str = ""
    clarification_response: str = ""
    awaiting_clarification: bool = False
    clarification_turns: int = 0  # 명확화 왕복 횟수 (무한루프 방지: 최대 2회)

    # 컨텍스트
    context: ContextInfo = Field(default_factory=ContextInfo)

    # SQL 생성 및 검증
    generated_sql: str = ""
    validated_sql: str = ""
    sql_validation_errors: list[str] = Field(default_factory=list)
    sql_retry_count: int = 0  # SQL 재생성 시도 횟수 (최대 2회)
    validation_feedback: str = ""  # 검증 실패 내용을 재생성 프롬프트에 주입

    # 테이블 선택 검증
    table_selection_verdict: str = ""  # pass/warning/ambiguous
    table_selection_warnings: list[str] = Field(default_factory=list)

    # 실행 결과
    sql_result: SQLResult = Field(default_factory=SQLResult)

    # 분석 결과
    analysis_result: AnalysisResult = Field(default_factory=AnalysisResult)

    # 시각화 데이터
    visualization: VisualizationData = Field(default_factory=VisualizationData)

    # 최종 응답
    formatted_response: str = ""

    # 상태 관리
    status: QueryStatus = QueryStatus.PENDING
    error_message: str = ""

    # 추론 추적 로그
    trace_log: list[TraceEntry] = Field(default_factory=list)
