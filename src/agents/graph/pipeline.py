"""LangGraph 파이프라인 정의.

전체 데이터 추출/분석 파이프라인을 그래프로 정의한다.

흐름:
  사용자 입력 → 전처리 → 이력 해소 → 의도 분류 → [명확화 필요?]
    ├─ YES → 명확화 질문 → (종료, 사용자 응답 대기)
    │         └─ (재진입) → 전처리 → 의도 분류 → ...
    └─ NO (DATA) → 질의 정규화 (8-Slot) → 컨텍스트 수집 → SQL 생성
              → SQL 검증
              ├─ 검증 실패 & retry 남음 → SQL 생성 (피드백 포함 재시도)
              └─ 검증 통과 → SQL 실행
                 → [분석 필요?]
                   ├─ YES → 데이터 분석 → 포맷팅
                   └─ NO → 포맷팅
                 → 응답 반환

질의 정규화 (8-Slot):
  DATA_QUERY 카테고리로 분류된 질의만 정규화 노드를 통과한다.
  LLM 2-Phase 파이프라인으로 자연어를 구조화된 슬롯으로 분해하여
  컨텍스트 수집과 SQL 생성의 정확도를 높인다.
  normalization_enabled=False 시 스킵한다.

SQL 재생성 루프:
  validate_sql 실패 시 sql_retry_count < SQL_MAX_RETRY 이면
  generate_sql 로 되돌아간다.
  최대 2회 재시도 후에도 실패하면 error_end 로 종료한다.

멀티턴 명확화:
  clarify_node 가 awaiting_clarification=True 로 설정하고 END 한다.
  챗봇 레이어가 사용자 응답을 clarification_response 에 채워
  파이프라인을 재실행한다.
  preprocess_node 가 clarification_response 를 감지하여
  user_input 을 합성하고 awaiting_clarification=False 로 전환한 뒤
  정상 흐름을 재개한다.
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from src.agents.models.user_messages import (
    ERR_GENERIC,
    ERR_SQL_RETRY_EXHAUSTED,
    REPHRASE_GUIDE,
)
from src.agents.state.state import IntentType, PipelineState, QueryStatus
from src.agents.nodes.analyzer import analyze_data_node
from src.agents.nodes.clarifier import clarify_node
from src.agents.nodes.context_collector import collect_context_node
from src.agents.nodes.context_enricher import enrich_context_node
from src.agents.nodes.formatter import format_response_node
from src.agents.nodes.history_resolver import resolve_history_node
from src.agents.nodes.intent_classifier import classify_intent_node
from src.agents.nodes.preprocessor import preprocess_node
from src.agents.nodes.query_normalizer import normalize_query_node
from src.agents.nodes.sql_executor import execute_sql_node
from src.agents.nodes.sql_generator import generate_sql_node
from src.agents.nodes.sql_validator import validate_sql_node
from src.utils.tracker import EvaluationTracker
from src.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

# SQL 재생성 최대 재시도 횟수
SQL_MAX_RETRY = settings.sql_max_retry

# 명확화 최대 왕복 횟수 (무한 명확화 루프 방지)
CLARIFICATION_MAX_TURNS = settings.clarification_max_turns


def _route_after_preprocess(state: PipelineState) -> str:
    """전처리 후 라우팅.

    에러 발생 시 종료. 정상이면 이력 해소로 진행.
    """
    if state.status == QueryStatus.ERROR:
        logger.info("라우팅: 전처리 → 에러 종료", reason=state.error_message)
        return "error_end"
    return "resolve_history"


def _route_after_history_resolve(state: PipelineState) -> str:
    """이력 해소 후 라우팅.

    UNSURE(awaiting_clarification=True) → clarify → END
    그 외(CONTINUE/NEW/SKIP) → classify_intent
    """
    if state.status == QueryStatus.AWAITING_CLARIFICATION:
        logger.info("라우팅: 이력해소 → 명확화 (UNSURE)")
        return "clarify_end"
    return "classify_intent"


def _route_after_intent(state: PipelineState) -> str:
    """의도 분류 후 라우팅.

    DATA 계열 의도:
      - normalization_enabled=True → 질의 정규화로 진행
      - normalization_enabled=False → 컨텍스트 수집으로 직행

    명확화 필요 / 일반대화 / 메타질의:
      - 명확화 횟수 초과 시 정규화 또는 컨텍스트 수집으로 진행
    """
    if state.status == QueryStatus.ERROR:
        logger.info("라우팅: 의도분류 → 에러 종료")
        return "error_end"

    needs_clarification = state.intent in (
        IntentType.CLARIFICATION_NEEDED,
        IntentType.GENERAL_QUESTION,
        IntentType.CASUAL_TALK,
        IntentType.META_QUESTION,
    )

    if needs_clarification:
        # 일반대화/메타질의도 명확화 경로로 라우팅
        if state.clarification_turns >= CLARIFICATION_MAX_TURNS:
            logger.warning(
                "라우팅: 의도분류 → 다음 단계 "
                "(명확화 횟수 소진)",
                turns=state.clarification_turns,
            )
            return _next_after_intent()
        logger.info(
            "라우팅: 의도분류 → 명확화",
            intent=state.intent.value,
        )
        return "clarify"

    logger.info(
        "라우팅: 의도분류 → 다음 단계",
        intent=state.intent.value,
    )
    return _next_after_intent()


def _next_after_intent() -> str:
    """의도 분류 후 데이터 처리 경로를 결정한다."""
    if settings.normalization_enabled:
        return "normalize_query"
    return "collect_context"


def _route_after_validation(state: PipelineState) -> str:
    """SQL 검증 후 라우팅.

    검증 실패 시:
      - sql_retry_count < SQL_MAX_RETRY → generate_sql 로 되돌아가 재생성
      - sql_retry_count >= SQL_MAX_RETRY → error_end 종료
    테이블 선택 모호 시:
      - clarify 로 분기하여 사용자에게 테이블 용도 확인
    검증 성공 시 execute_sql 진행.
    """
    # 테이블 선택 모호 → 명확화 질문
    if state.table_selection_verdict == "ambiguous":
        if state.clarification_turns < CLARIFICATION_MAX_TURNS:
            logger.info("라우팅: SQL검증 → 명확화 (테이블 모호)")
            return "clarify"
        logger.warning(
            "라우팅: SQL검증 → 실행 진행 (테이블 모호하나 명확화 횟수 초과)",
        )

    if not state.sql_validation_errors:
        logger.info("라우팅: SQL검증 → SQL 실행")
        return "execute_sql"

    if state.sql_retry_count < SQL_MAX_RETRY:
        logger.info(
            "라우팅: SQL검증 → SQL 재생성",
            retry_count=state.sql_retry_count,
            errors=state.sql_validation_errors,
        )
        return "generate_sql"

    logger.warning(
        "라우팅: SQL검증 → 에러 종료 (재시도 횟수 초과)",
        retry_count=state.sql_retry_count,
        errors=state.sql_validation_errors,
    )
    return "error_end"


def _route_after_execution(state: PipelineState) -> str:
    """SQL 실행 후 라우팅: 분석 필요 여부."""
    if state.status == QueryStatus.ERROR:
        logger.info("라우팅: SQL실행 → 에러 종료")
        return "error_end"
    if state.intent == IntentType.DATA_ANALYSIS:
        logger.info("라우팅: SQL실행 → 데이터 분석")
        return "analyze_data"
    logger.info("라우팅: SQL실행 → 결과 포맷팅")
    return "format_response"


def _handle_error(state: PipelineState) -> dict:
    """에러 상태를 사용자 친화적 메시지로 변환한다."""
    error_msg = state.error_message or ERR_GENERIC

    # 재시도 횟수 소진 메시지는 별도 안내
    if state.sql_retry_count >= SQL_MAX_RETRY and state.sql_validation_errors:
        user_message = ERR_SQL_RETRY_EXHAUSTED
    else:
        user_message = (
            f"죄송합니다. {error_msg}\n"
            f"{REPHRASE_GUIDE}"
        )

    return {
        "formatted_response": user_message,
        "status": QueryStatus.ERROR,
    }


def build_pipeline(
    tracker: EvaluationTracker | None = None,
) -> StateGraph:
    """LangGraph 파이프라인을 구성하고 반환한다.

    Args:
        tracker: 평가 트래커. 제공 시 각 노드에 자동 계측을 적용한다.
    """
    workflow = StateGraph(PipelineState)

    # 트래커 유무와 관계없이 동일한 등록 코드
    def node(name: str, fn):  # type: ignore[no-untyped-def]
        return tracker.track(name)(fn) if tracker else fn

    if tracker:
        tracker.inject()

    # 노드 등록
    workflow.add_node("preprocess",       node("preprocess",       preprocess_node))
    workflow.add_node("resolve_history",  node("resolve_history",  resolve_history_node))
    workflow.add_node("classify_intent",  node("classify_intent",  classify_intent_node))
    workflow.add_node("normalize_query", node("normalize_query", normalize_query_node))
    workflow.add_node("clarify",         node("clarify",         clarify_node))
    workflow.add_node("collect_context", node("collect_context", collect_context_node))
    workflow.add_node("enrich_context",  node("enrich_context",  enrich_context_node))
    workflow.add_node("generate_sql",    node("generate_sql",    generate_sql_node))
    workflow.add_node("validate_sql",    node("validate_sql",    validate_sql_node))
    workflow.add_node("execute_sql",     node("execute_sql",     execute_sql_node))
    workflow.add_node("analyze_data",    node("analyze_data",    analyze_data_node))
    workflow.add_node("format_response", node("format_response", format_response_node))
    workflow.add_node("error_end", _handle_error)

    # 진입점
    workflow.set_entry_point("preprocess")

    # 전처리 → 이력 해소 or 에러
    workflow.add_conditional_edges(
        "preprocess",
        _route_after_preprocess,
        {
            "resolve_history": "resolve_history",
            "error_end": "error_end",
        },
    )

    # 이력 해소 → 의도 분류 or 명확화(UNSURE)
    workflow.add_conditional_edges(
        "resolve_history",
        _route_after_history_resolve,
        {
            "classify_intent": "classify_intent",
            "clarify_end": END,  # UNSURE → formatted_response에 질문이 담겨 END
        },
    )

    # 의도 분류 → 명확화 or 정규화 or 컨텍스트 수집 or 에러
    workflow.add_conditional_edges(
        "classify_intent",
        _route_after_intent,
        {
            "clarify": "clarify",
            "normalize_query": "normalize_query",
            "collect_context": "collect_context",
            "error_end": "error_end",
        },
    )

    # 명확화 → END
    workflow.add_edge("clarify", END)

    # 질의 정규화 → 컨텍스트 수집
    workflow.add_edge("normalize_query", "collect_context")

    # 컨텍스트 수집 → 컨텍스트 보강 → SQL 생성
    workflow.add_edge("collect_context", "enrich_context")
    workflow.add_edge("enrich_context", "generate_sql")

    # SQL 생성 → SQL 검증 (재시도 시에도 동일 경로)
    workflow.add_edge("generate_sql", "validate_sql")

    # SQL 검증 → 재생성 루프 or 실행 or 테이블 명확화 or 에러
    workflow.add_conditional_edges(
        "validate_sql",
        _route_after_validation,
        {
            "generate_sql": "generate_sql",  # 재생성 루프 엣지
            "execute_sql": "execute_sql",
            "clarify": "clarify",  # 테이블 모호성 → 명확화
            "error_end": "error_end",
        },
    )

    # SQL 실행 → 분석 or 포맷팅 or 에러
    workflow.add_conditional_edges(
        "execute_sql",
        _route_after_execution,
        {
            "analyze_data": "analyze_data",
            "format_response": "format_response",
            "error_end": "error_end",
        },
    )

    workflow.add_edge("analyze_data", "format_response")
    workflow.add_edge("format_response", END)
    workflow.add_edge("error_end", END)

    return workflow


def create_app(tracker: EvaluationTracker | None = None):
    """컴파일된 LangGraph 앱을 생성한다.

    Args:
        tracker: 평가 트래커. 제공 시 노드 계측이 적용된다.
    """
    workflow = build_pipeline(tracker=tracker)
    return workflow.compile()
