"""LangGraph 단일 파이프라인 정의.

3계층(interpret → reason → present) 전체 흐름을 단일 그래프로 정의한다.

흐름:
  사용자 입력 → 전처리 → 이력 해소 → 의도 분류 → [명확화 필요?]
    ├─ YES → 명확화 질문 → (종료, 사용자 응답 대기)
    └─ NO (DATA) → 질의 정규화 (8-Slot)
         → [reason 계층 추론 루프]
           planner → explorer → evaluator
           → generator → validator → recovery
           → finalizer
         → SQL 실행
         → [분석 필요?]
           ├─ YES → 데이터 분석 → 포맷팅
           └─ NO → 포맷팅
         → 응답 반환
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
    IntentType,
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
from src.agents.nodes.reason.table_verifier import (
    table_verifier_node,
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

from src.services.confidence_scorer import evaluate_readiness
from src.utils.tracker import EvaluationTracker
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
    """의도 분류 후 라우팅."""
    if state.status == QueryStatus.ERROR:
        return "error_end"

    needs_clarification = state.intent in (
        IntentType.CLARIFICATION_NEEDED,
        IntentType.GENERAL_QUESTION,
        IntentType.CASUAL_TALK,
        IntentType.META_QUESTION,
    )

    if needs_clarification:
        if state.clarification_turns >= CLARIFICATION_MAX_TURNS:
            return _next_after_intent()
        return "clarify"

    return _next_after_intent()


def _next_after_intent() -> str:
    """의도 분류 후 데이터 처리 경로."""
    if settings.normalization_enabled:
        return "normalize_query"
    return "reason_plan"


def _route_after_normalize(
    state: PipelineState,
) -> str:
    """정규화 후 라우팅 — ambiguities가 있으면 즉시 명확화."""
    nq = state.normalized_query
    if nq and hasattr(nq, "ambiguities") and nq.ambiguities:
        if state.clarification_turns < CLARIFICATION_MAX_TURNS:
            state.intent = IntentType.CLARIFICATION_NEEDED
            return "clarify"
    return "reason_plan"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Reason 계층 라우팅
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _route_after_reason_plan(
    state: PipelineState,
) -> str:
    """planner 후 Fast-Path 판정."""
    if state.reason.fast_path_triggered:
        return "reason_generate_sql"
    return "reason_explore"


def _route_after_reason_evaluate(
    state: PipelineState,
) -> str:
    """confidence_evaluator 후 다음 행동."""
    return evaluate_readiness(state.reason).value


def _route_after_reason_validate_sql(
    state: PipelineState,
) -> str:
    """sql_validator 후 실패 유형별 라우팅."""
    result = state.reason.sql_validation_result
    if result is None:
        return "conclude_failure"

    if (
        state.reason.fast_path_triggered
        and result.overall != "SUCCESS"
    ):
        return "explore_after_fast_path"

    match result.overall:
        case "SUCCESS":
            return "conclude_success"

        case "FAIL_SYNTAX":
            if state.reason.loop_guard.generate_attempts < 4:
                return "fix_syntax"
            return "conclude_failure"

        case "FAIL_SEMANTIC_LOCAL":
            lg = state.reason.loop_guard
            if lg.should_escalate_to_structural():
                return "replan"
            if lg.generate_attempts < 4:
                return "fix_local"
            return "conclude_failure"

        case (
            "FAIL_STRUCTURAL"
            | "FAIL_EMPTY"
            | "FAIL_DB_ERROR"
        ):
            return "replan"

        case _:
            return "conclude_failure"


def _route_after_reason_recover(
    state: PipelineState,
) -> str:
    """recovery_planner 후 라우팅."""
    if state.reason.phase == "DONE":
        return "conclude"
    return "explore"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Reason → Present 전환 라우팅
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _route_after_reason_finalize(
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

def build_pipeline(
    tracker: EvaluationTracker | None = None,
) -> StateGraph:
    """3계층 단일 LangGraph 파이프라인을 구성한다."""
    workflow = StateGraph(PipelineState)

    def node(name: str, fn: Any) -> Any:
        return tracker.track(name)(fn) if tracker else fn

    if tracker:
        tracker.inject()

    # ── Interpret 계층 ──
    workflow.add_node(
        "preprocess",
        node("preprocess", preprocess_node),
    )
    workflow.add_node(
        "resolve_history",
        node("resolve_history", resolve_history_node),
    )
    workflow.add_node(
        "classify_intent",
        node("classify_intent", classify_intent_node),
    )
    workflow.add_node(
        "normalize_query",
        node("normalize_query", normalize_query_node),
    )
    workflow.add_node(
        "clarify",
        node("clarify", clarify_node),
    )

    # ── Reason 계층 ──
    workflow.add_node(
        "reason_plan",
        node("reason_plan", planner_node),
    )
    workflow.add_node(
        "reason_explore",
        node("reason_explore", context_explorer_node),
    )
    workflow.add_node(
        "reason_verify_tables",
        node("reason_verify_tables", table_verifier_node),
    )
    workflow.add_node(
        "reason_evaluate",
        node("reason_evaluate", confidence_evaluator_node),
    )
    workflow.add_node(
        "reason_generate_sql",
        node("reason_generate_sql", sql_generator_node),
    )
    workflow.add_node(
        "reason_validate_sql",
        node("reason_validate_sql", sql_validator_node),
    )
    workflow.add_node(
        "reason_recover",
        node("reason_recover", recovery_planner_node),
    )
    workflow.add_node(
        "reason_finalize",
        node("reason_finalize", result_finalizer_node),
    )

    # ── Present 계층 ──
    workflow.add_node(
        "execute_sql",
        node("execute_sql", execute_sql_node),
    )
    workflow.add_node(
        "analyze_data",
        node("analyze_data", analyze_data_node),
    )
    workflow.add_node(
        "format_response",
        node("format_response", format_response_node),
    )
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
            "reason_plan": "reason_plan",
            "error_end": "error_end",
        },
    )

    workflow.add_edge("clarify", END)
    workflow.add_conditional_edges(
        "normalize_query",
        _route_after_normalize,
        {
            "clarify": "clarify",
            "reason_plan": "reason_plan",
        },
    )

    # ── Reason 엣지 ──
    workflow.add_conditional_edges(
        "reason_plan",
        _route_after_reason_plan,
        {
            "reason_generate_sql": "reason_generate_sql",
            "reason_explore": "reason_explore",
        },
    )

    workflow.add_edge(
        "reason_explore", "reason_verify_tables",
    )
    workflow.add_edge(
        "reason_verify_tables", "reason_evaluate",
    )

    workflow.add_conditional_edges(
        "reason_evaluate",
        _route_after_reason_evaluate,
        {
            "explore": "reason_explore",
            "generate_sql": "reason_generate_sql",
            "replan": "reason_recover",
            "conclude_failure": "reason_finalize",
            "ask_user": "reason_finalize",
        },
    )

    workflow.add_edge(
        "reason_generate_sql", "reason_validate_sql",
    )

    workflow.add_conditional_edges(
        "reason_validate_sql",
        _route_after_reason_validate_sql,
        {
            "conclude_success": "reason_finalize",
            "fix_syntax": "reason_generate_sql",
            "fix_local": "reason_generate_sql",
            "replan": "reason_recover",
            "conclude_failure": "reason_finalize",
            "explore_after_fast_path": "reason_explore",
        },
    )

    workflow.add_conditional_edges(
        "reason_recover",
        _route_after_reason_recover,
        {
            "explore": "reason_explore",
            "conclude": "reason_finalize",
        },
    )

    # ── Reason → Present 전환 ──
    workflow.add_conditional_edges(
        "reason_finalize",
        _route_after_reason_finalize,
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


def create_app(
    tracker: EvaluationTracker | None = None,
):
    """컴파일된 LangGraph 앱을 생성한다."""
    workflow = build_pipeline(tracker=tracker)
    return workflow.compile()
