"""LangGraph 단일 파이프라인 정의.

3계층(interpret → reason → present) 전체 흐름을 단일 그래프로 정의한다.
노드 선언, 조건부 엣지 연결, 라우팅 함수를 한 곳에서 관리하여
파이프라인의 구조를 단일 진실 공급원으로 유지한다.

흐름:
  사용자 입력 → preprocess → resolve_history → classify_intent → [명확화 필요?]
    ├─ YES → clarify → (종료, 사용자 응답 대기)
    └─ NO (DATA) → normalize_query (8-Slot)
         → [reason 계층 추론 루프]
           planner → context_explorer → confidence_evaluator
           → sql_generator → sql_validator → recovery_planner
           → result_finalizer
         → execute_sql
         → [분석 필요?]
           ├─ YES → analyze_data → format_response
           └─ NO → format_response
         → 응답 반환

노드 명명 규칙:
    그래프 노드 이름 = 파일명 = 함수명(_node 접미사 제외).
    예: "context_explorer" → context_explorer.py → context_explorer_node()

핵심 함수:
    - build_pipeline: StateGraph를 구성하여 반환
    - create_app: build_pipeline + compile()
    - _route_after_*: 각 노드 뒤의 조건부 라우팅 함수
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph

from src.agents.models.user_messages import (
    ERR_GENERIC,
    ERR_SQL_RETRY_EXHAUSTED,
    REPHRASE_GUIDE,
)
from src.agents.state.state import (
    FailureType,
    IntentType,
    Phase,
    PipelineState,
    QueryStatus,
)

# ── Interpret 계층 노드 ──
from src.agents.nodes.interpret.preprocessor import preprocess_node
from src.agents.nodes.interpret.history_resolver import (
    resolve_history_node,
)
from src.agents.nodes.interpret.intent_classifier import (
    classify_intent_node,
)
from src.agents.nodes.interpret.query_normalizer import (
    normalize_query_node,
)
from src.agents.nodes.interpret.clarifier import clarify_node

# ── Reason 계층 노드 ──
from src.agents.nodes.reason.planner import planner_node
from src.agents.nodes.reason.context_explorer import (
    context_explorer_node,
)
from src.agents.nodes.reason.confidence_evaluator import (
    confidence_evaluator_node,
)
from src.agents.nodes.reason.sql_generator import (
    sql_generator_node,
)
from src.agents.nodes.reason.sql_validator import (
    sql_validator_node,
)
from src.agents.nodes.reason.recovery_planner import (
    recovery_planner_node,
)
from src.agents.nodes.reason.result_finalizer import (
    result_finalizer_node,
)

# ── Present 계층 노드 ──
from src.agents.nodes.present.sql_executor import execute_sql_node
from src.agents.nodes.present.analyzer import analyze_data_node
from src.agents.nodes.present.formatter import format_response_node

from src.agents.state.state import MAX_GENERATES
from src.services.confidence_scorer import evaluate_readiness
from src.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

# 설정 상수
SQL_MAX_RETRY = settings.sql_max_retry
CLARIFICATION_MAX_TURNS = settings.clarification_max_turns


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Interpret 계층 라우팅
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _route_after_preprocess(
    state: PipelineState,
) -> str:
    """전처리 후 라우팅."""
    if state.status == QueryStatus.ERROR:
        return "error_end"
    return "resolve_history"


def _route_after_history_resolve(
    state: PipelineState,
) -> str:
    """이력 해소 후 라우팅."""
    if state.status == QueryStatus.AWAITING_CLARIFICATION:
        return "clarify_end"
    return "classify_intent"


def _route_after_intent(
    state: PipelineState,
) -> str:
    """의도 분류 후 라우팅.

    비데이터 의도(명확화/일반질문/잡담/메타질문)는 clarify로 보내되,
    명확화 왕복이 상한에 도달하면 강제로 데이터 처리 경로로 진행한다.
    """
    if state.status == QueryStatus.ERROR:
        return "error_end"

    # 데이터 추출/분석이 아닌 의도는 모두 명확화 대상
    needs_clarification = state.intent in (
        IntentType.CLARIFICATION_NEEDED,
        IntentType.GENERAL_QUESTION,
        IntentType.CASUAL_TALK,
        IntentType.META_QUESTION,
    )

    if needs_clarification:
        # 명확화 왕복 상한 초과 시 현재 입력으로 강제 진행
        if state.clarification_turns >= CLARIFICATION_MAX_TURNS:
            return _next_after_intent()
        return "clarify"

    return _next_after_intent()


def _next_after_intent() -> str:
    """의도 분류 후 데이터 처리 경로."""
    if settings.normalization_enabled:
        return "normalize_query"
    return "planner"


def _route_after_normalize(
    state: PipelineState,
) -> str:
    """정규화 후 라우팅 — ambiguities가 있으면 즉시 명확화."""
    if state.intent == IntentType.CLARIFICATION_NEEDED:
        if state.clarification_turns < CLARIFICATION_MAX_TURNS:
            return "clarify"
    return "planner"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Reason 계층 라우팅
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _route_after_planner(
    state: PipelineState,
) -> str:
    """planner 후 Fast-Path 판정."""
    if state.reason.fast_path_triggered:
        return "sql_generator"
    return "context_explorer"


def _route_after_confidence_evaluator(
    state: PipelineState,
) -> str:
    """confidence_evaluator 후 다음 행동."""
    return evaluate_readiness(state.reason).value


def _route_after_sql_validator(
    state: PipelineState,
) -> str:
    """sql_validator 후 failure_type 기반 라우팅.

    6가지 분기:
      1. failure_type=None → result_finalizer (SQL 확정, 검증 통과)
      2. SQL_SYNTAX → sql_generator 재시도 (생성 횟수 미달 시)
      3. SQL_SEMANTIC_LOCAL → 로컬 수정 or 에스컬레이션
      4. SQL_STRUCTURAL / EMPTY_RESULT / DB_ERROR → recovery_planner
      5. fast-path 실패 → context_explorer (정상 탐색 전환)
      6. 기타 / 한계 초과 → result_finalizer (실패 처리)
    """
    ft = state.reason.failure_type

    # fast-path로 생성한 SQL이 실패하면 정상 탐색 루프로 전환
    # stale failure 컨텍스트 초기화는 context_explorer 진입부에서 수행
    if state.reason.fast_path_triggered and ft is not None:
        return "explore_after_fast_path"

    match ft:
        case None:
            return "conclude_success"

        case FailureType.SQL_SYNTAX:
            if state.reason.loop_guard.generate_attempts < MAX_GENERATES:
                return "fix_syntax"
            return "conclude_failure"

        case FailureType.SQL_SEMANTIC_LOCAL:
            lg = state.reason.loop_guard
            if lg.should_escalate_to_structural():
                return "replan"
            if lg.generate_attempts < MAX_GENERATES:
                return "fix_local"
            return "conclude_failure"

        case (
            FailureType.SQL_STRUCTURAL
            | FailureType.EMPTY_RESULT
            | FailureType.DB_ERROR
            | FailureType.NO_USE_CASE
            | FailureType.NO_TABLE
            | FailureType.TERM_UNRESOLVABLE
        ):
            return "replan"

        case _:
            return "conclude_failure"


def _route_after_recovery_planner(
    state: PipelineState,
) -> str:
    """recovery_planner 후 라우팅."""
    if state.reason.phase == Phase.DONE:
        return "conclude"
    return "explore"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Reason → Present 전환 라우팅
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _route_after_result_finalizer(
    state: PipelineState,
) -> str:
    """reason 계층 완료 후 라우팅.

    reason 계층에서 명확화가 필요한 경우(CONFLICTED, DB 분리 등)
    result_finalizer/sql_generator가 이미 clarification_question을
    생성했으므로 clarifier 노드를 거치지 않고 바로 END로 나간다.
    """
    if state.awaiting_clarification:
        return "clarify_end"
    if state.error_message:
        return "error_end"
    if state.reason.validated_sql:
        return "execute_sql"
    return "error_end"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Present 계층 라우팅
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _route_after_execution(
    state: PipelineState,
) -> str:
    """SQL 실행 후 라우팅."""
    if state.status == QueryStatus.ERROR:
        return "error_end"
    if state.intent == IntentType.DATA_ANALYSIS:
        return "analyze_data"
    return "format_response"


def _handle_error(state: PipelineState) -> dict:
    """에러 상태를 사용자 친화적 메시지로 변환."""
    error_msg = state.error_message or ERR_GENERIC

    if (
        state.reason.loop_guard.generate_attempts
        >= SQL_MAX_RETRY
    ):
        user_message = ERR_SQL_RETRY_EXHAUSTED
    else:
        user_message = (
            f"죄송합니다. {error_msg}\n{REPHRASE_GUIDE}"
        )

    return {
        "formatted_response": user_message,
        "status": QueryStatus.ERROR,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 파이프라인 빌더
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def build_pipeline() -> StateGraph:
    """3계층 단일 LangGraph 파이프라인을 구성한다.

    노드 함수를 직접 등록하며, 추적은 ``DataCopilotCallbackHandler`` 가
    ``config={"callbacks": [handler]}`` 로 주입되어 자동 처리한다.
    """
    workflow = StateGraph(PipelineState)

    # ── Interpret 계층 ──
    workflow.add_node("preprocess", preprocess_node)
    workflow.add_node("resolve_history", resolve_history_node)
    workflow.add_node("classify_intent", classify_intent_node)
    workflow.add_node("normalize_query", normalize_query_node)
    workflow.add_node("clarify", clarify_node)

    # ── Reason 계층 ──
    workflow.add_node("planner", planner_node)
    workflow.add_node("context_explorer", context_explorer_node)
    workflow.add_node("confidence_evaluator", confidence_evaluator_node)
    workflow.add_node("sql_generator", sql_generator_node)
    workflow.add_node("sql_validator", sql_validator_node)
    workflow.add_node("recovery_planner", recovery_planner_node)
    workflow.add_node("result_finalizer", result_finalizer_node)

    # ── Present 계층 ──
    workflow.add_node("execute_sql", execute_sql_node)
    workflow.add_node("analyze_data", analyze_data_node)
    workflow.add_node("format_response", format_response_node)
    workflow.add_node("error_end", _handle_error)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 엣지 연결
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    workflow.set_entry_point("preprocess")

    # ── Interpret 엣지 ──
    workflow.add_conditional_edges(
        "preprocess",
        _route_after_preprocess,
        {
            "resolve_history": "resolve_history",
            "error_end": "error_end",
        },
    )

    workflow.add_conditional_edges(
        "resolve_history",
        _route_after_history_resolve,
        {
            "classify_intent": "classify_intent",
            "clarify_end": END,
        },
    )

    workflow.add_conditional_edges(
        "classify_intent",
        _route_after_intent,
        {
            "clarify": "clarify",
            "normalize_query": "normalize_query",
            "planner": "planner",
            "error_end": "error_end",
        },
    )

    workflow.add_edge("clarify", END)
    workflow.add_conditional_edges(
        "normalize_query",
        _route_after_normalize,
        {
            "clarify": "clarify",
            "planner": "planner",
        },
    )

    # ── Reason 엣지 ──
    workflow.add_conditional_edges(
        "planner",
        _route_after_planner,
        {
            "sql_generator": "sql_generator",
            "context_explorer": "context_explorer",
        },
    )

    workflow.add_edge(
        "context_explorer", "confidence_evaluator",
    )

    workflow.add_conditional_edges(
        "confidence_evaluator",
        _route_after_confidence_evaluator,
        {
            "explore": "context_explorer",
            "generate_sql": "sql_generator",
            "replan": "recovery_planner",
            "conclude_failure": "result_finalizer",
            "ask_user": "result_finalizer",
        },
    )

    workflow.add_edge(
        "sql_generator", "sql_validator",
    )

    workflow.add_conditional_edges(
        "sql_validator",
        _route_after_sql_validator,
        {
            "conclude_success": "result_finalizer",
            "fix_syntax": "sql_generator",
            "fix_local": "sql_generator",
            "replan": "recovery_planner",
            "conclude_failure": "result_finalizer",
            "explore_after_fast_path": "context_explorer",
        },
    )

    workflow.add_conditional_edges(
        "recovery_planner",
        _route_after_recovery_planner,
        {
            "explore": "context_explorer",
            "conclude": "result_finalizer",
        },
    )

    # ── Reason → Present 전환 ──
    workflow.add_conditional_edges(
        "result_finalizer",
        _route_after_result_finalizer,
        {
            "execute_sql": "execute_sql",
            "clarify_end": END,
            "error_end": "error_end",
        },
    )

    # ── Present 엣지 ──
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


def create_app() -> Any:
    """컴파일된 LangGraph 앱을 생성한다."""
    workflow = build_pipeline()
    return workflow.compile()


# ── 싱글턴 캐시 ──

_compiled_app: Any = None


def get_compiled_app() -> Any:
    """컴파일된 LangGraph 앱 싱글턴을 반환한다.

    그래프 구조는 불변이므로 한 번만 빌드하고 재사용한다.
    요청별 추적은 ``config={"callbacks": [handler]}`` 로 주입한다.
    """
    global _compiled_app  # noqa: PLW0603
    if _compiled_app is None:
        _compiled_app = create_app()
        logger.info("LangGraph 파이프라인 컴파일 완료 (싱글턴)")
    return _compiled_app


def reset_compiled_app() -> None:
    """테스트 등에서 싱글턴을 초기화할 때 사용한다."""
    global _compiled_app  # noqa: PLW0603
    _compiled_app = None
