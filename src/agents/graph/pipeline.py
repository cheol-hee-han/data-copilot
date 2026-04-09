"""LangGraph 단일 파이프라인 정의.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

3계층(interpret → reason → present) 전체 흐름을 단일 그래프로 정의한다.
노드 선언, 조건부 엣지 연결, 라우팅 함수를 한 곳에서 관리하여
파이프라인의 구조를 단일 진실 공급원으로 유지한다.

흐름:
  사용자 입력 → intent_classifier
    → [pending_signals?] → clarification_handler (interrupt/resume)
    → [비데이터?] → simple_responder → format_response → END
    → normalize_query (8-Slot)
    → [reason 계층 추론 루프]
        reasoning_preparer → context_retriever → context_interpreter
        → readiness_gate → recovery_agent (재계획)
            → context_retriever (기존 파이프라인 재진입)
        → sql_generator → sql_validator
        → result_finalizer
    → execute_sql
    → [분석 필요?]
        ├─ YES → analyze_data → format_response
        └─ NO → format_response
    → 응답 반환

  sanitize는 runner.py에서 1회 실행 (preprocess 노드 제거).
  5개 트리거(T1~T5)가 AmbiguitySignal → pending_signals를 생성하면
  clarification_handler로 라우팅되어 통합 명확화 흐름을 수행한다.

노드 명명 규칙:
    그래프 노드 이름 = 파일명 = 함수명(_node 접미사 제외).
    예: "context_retriever" → context_retriever.py → context_retriever_node()

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
from src.agents.nodes.interpret.intent_classifier import (
    intent_classifier_node,
)
from src.agents.nodes.interpret.query_normalizer import (
    normalize_query_node,
)

# ── 통합 명확화 노드 ──
from src.agents.nodes.interpret.clarification_handler import (
    clarification_handler_node,
)

# ── Reason 계층 노드 ──
from src.agents.nodes.reason.reasoning_preparer import (
    reasoning_preparer_node,
)
from src.agents.nodes.reason.context_retriever import (
    context_retriever_node,
)
from src.agents.nodes.reason.context_interpreter import (
    context_interpreter_node,
)
from src.agents.nodes.reason.readiness_gate import (
    readiness_gate_node,
)
from src.agents.nodes.reason.sql_generator import (
    sql_generator_node,
)
from src.agents.nodes.reason.sql_validator import (
    sql_validator_node,
)
from src.agents.nodes.reason.recovery_agent import (
    recovery_agent_node,
)
from src.agents.nodes.reason.result_finalizer import (
    result_finalizer_node,
)

# ── Present 계층 노드 ──
from src.agents.nodes.present.sql_executor import execute_sql_node
from src.agents.nodes.present.analyzer import analyze_data_node
from src.agents.nodes.present.formatter import format_response_node
from src.agents.nodes.present.simple_responder import (
    simple_responder_node,
)

from src.agents.graph.cancel import with_cancel_check
from src.agents.state.state import MAX_GENERATES
from src.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

# 설정 상수
CLARIFICATION_MAX_TURNS = settings.clarification_max_turns


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Interpret 계층 라우팅
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _route_after_intent_classifier(
    state: PipelineState,
) -> str:
    """통합 노드 후 라우팅.

    1. pending_signals → clarification_handler (UNSURE / AMBIGUOUS)
    2. 에러 → error_end
    3. 비데이터 의도 (CASUAL_TALK / META_QUESTION) → simple_responder
    4. 데이터 의도 → normalize_query 또는 reasoning_preparer
    """
    if state.pending_signals:
        return "clarification_handler"
    if state.status == QueryStatus.CANCELLED:
        return "error_end"
    if state.status == QueryStatus.ERROR:
        return "error_end"

    # 비데이터 의도 → 경량 응답 노드에서 직접 처리
    if state.intent in (
        IntentType.CASUAL_TALK,
        IntentType.META_QUESTION,
    ):
        return "simple_responder"

    return _next_after_intent()


def _next_after_intent() -> str:
    """의도 분류 후 데이터 처리 경로."""
    if settings.normalization_enabled:
        return "normalize_query"
    return "reasoning_preparer"


def _route_after_normalize(
    state: PipelineState,
) -> str:
    """정규화 후 라우팅 — pending_signals가 있으면 clarification_handler."""
    if state.status == QueryStatus.CANCELLED:
        return "error_end"
    if state.pending_signals:
        return "clarification_handler"
    return "reasoning_preparer"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Reason 계층 라우팅
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_PHASE_TO_ROUTE: dict[Phase, str] = {
    Phase.EXPLORING: "explore",
    Phase.GENERATING: "generate_sql",
    Phase.REPLANNING: "recovery",
    Phase.VERIFYING: "ask_user",
    Phase.DONE: "conclude_failure",
}


def _route_after_readiness_gate(
    state: PipelineState,
) -> str:
    """readiness_gate 후 다음 행동 — reason.phase 기반 순수 라우팅.

    모든 판정·state mutation은 readiness_gate_node에서 완결되며,
    이 함수는 phase를 라우팅 키로 변환하기만 한다.
    """
    if state.status == QueryStatus.CANCELLED:
        return "conclude_failure"
    if state.pending_signals:
        return "clarification_handler"
    return _PHASE_TO_ROUTE.get(state.reason.phase, "conclude_failure")


def _route_after_sql_generator(
    state: PipelineState,
) -> str:
    """sql_generator 후 라우팅.

    3가지 분기:
      1. GENERATION_FAILED → recovery_agent (정보 보충 후 재시도)
      2. pending_signals → clarification_handler (Cross-DB INFER)
      3. 정상 → sql_validator (검증)
    """
    if state.reason.failure_type == FailureType.GENERATION_FAILED:
        return "replan"
    if state.pending_signals:
        return "clarification_handler"
    return "sql_validator"


def _route_after_sql_validator(
    state: PipelineState,
) -> str:
    """sql_validator 후 failure_type 기반 라우팅.

    6가지 분기:
      1. failure_type=None → result_finalizer (SQL 확정, 검증 통과)
      2. SQL_SYNTAX → sql_generator 재시도 (생성 횟수 미달 시)
      3. SQL_SEMANTIC_LOCAL → 로컬 수정 or 에스컬레이션
      4. SQL_STRUCTURAL / EMPTY_RESULT / DB_ERROR → recovery_agent
      5. fast-path 실패 → context_retriever (정상 탐색 전환)
      6. 기타 / 한계 초과 → result_finalizer (실패 처리)
    """
    ft = state.reason.failure_type

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
            | FailureType.NO_KNOWLEDGE
            | FailureType.NO_TABLE
            | FailureType.TERM_UNRESOLVABLE
            | FailureType.GENERATION_FAILED
        ):
            # GENERATION_FAILED는 정상 경로에서 도달하지 않으나
            # 방어적으로 포함.
            return "replan"

        case _:
            return "conclude_failure"


def _route_after_recovery_agent(
    state: PipelineState,
) -> str:
    """recovery_agent 후 라우팅.

    recovery_agent는 재계획만 수행하므로:
    - CANCELLED → result_finalizer (즉시 종료)
    - pending_signals → clarification_handler (통합 명확화)
    - EXPLORING → context_retriever (새 execution_plan 실행)
    - GENERATING → sql_generator (force-generate)
    - DONE → result_finalizer (give_up + 점수 미달)
    """
    if state.status == QueryStatus.CANCELLED:
        return "result_finalizer"
    if state.pending_signals:
        return "clarification_handler"

    reason = state.reason
    if reason.phase == Phase.EXPLORING:
        return "context_retriever"
    if reason.phase == Phase.GENERATING:
        return "sql_generator"
    if reason.phase == Phase.DONE:
        return "result_finalizer"

    # 예상치 못한 phase — 안전하게 result_finalizer로
    logger.warning(
        "recovery_agent 후 예상치 못한 phase",
        phase=reason.phase.value,
    )
    return "result_finalizer"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Reason → Present 전환 라우팅
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _route_after_result_finalizer(
    state: PipelineState,
) -> str:
    """reason 계층 완료 후 라우팅.

    CANCELLED를 최우선 체크하여 validated_sql이 남아있어도
    execute_sql로 빠지지 않도록 한다 (F2 해소).
    """
    if state.status == QueryStatus.CANCELLED:
        return "error_end"
    if state.pending_signals:
        return "clarification_handler"
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
    if state.status in (QueryStatus.ERROR, QueryStatus.CANCELLED):
        return "error_end"
    if state.intent == IntentType.DATA_ANALYSIS:
        return "analyze_data"
    return "format_response"


# ── clarification_handler 후속 라우팅 ──

_VALID_RETURN_TARGETS = frozenset({
    "intent_classifier",
    "normalize_query", "sql_generator",
    "readiness_gate", "result_finalizer",
})

# 배포 과도기 호환 — 기존 세션의 source_node가 구 이름일 수 있음
_LEGACY_TARGET_MAP: dict[str, str] = {
    "resolve_history": "intent_classifier",
    "classify_intent": "intent_classifier",
    "resolve_and_classify": "intent_classifier",
}


def _route_after_clarify(
    state: PipelineState,
) -> str:
    """clarification_handler 후 라우팅 — source_node로 복귀.

    현재 턴(turn_id)의 시그널만 필터링하여 라우팅 대상을 결정한다.
    이전 턴 시그널에 의한 오라우팅을 방지한다.
    """
    current_signals = [
        s for s in state.resolved_signals
        if s.turn_id is not None and s.turn_id == state.turn_id
    ]
    if current_signals:
        target = current_signals[-1].source_node
        target = _LEGACY_TARGET_MAP.get(target, target)
        if target in _VALID_RETURN_TARGETS:
            return target
        logger.error(
            "Invalid return target",
            target=target,
        )
    return "intent_classifier"


def _handle_error(state: PipelineState) -> dict:
    """에러 상태를 사용자 친화적 메시지로 변환한다.

    LangGraph 노드로 등록되어 error_end 경로에서 호출된다.
    CANCELLED 상태는 기존 cancel 메시지를 보존한다.
    SQL 재시도 소진 여부에 따라 다른 안내 메시지를 반환한다.
    """
    # CANCELLED: 이미 설정된 cancel 메시지를 보존
    if state.status == QueryStatus.CANCELLED:
        return {
            "formatted_response": (
                state.formatted_response
                or "요청이 중단되었습니다."
            ),
            "status": QueryStatus.CANCELLED,
        }

    error_msg = state.error_message or ERR_GENERIC

    if (
        state.reason.loop_guard.generate_attempts
        >= MAX_GENERATES
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
    _cc = with_cancel_check
    workflow.add_node("intent_classifier", _cc(intent_classifier_node))
    workflow.add_node("normalize_query", _cc(normalize_query_node))

    # ── 통합 명확화 노드 ──
    workflow.add_node("clarification_handler", clarification_handler_node)

    # ── Reason 계층 ──
    workflow.add_node("reasoning_preparer", reasoning_preparer_node)
    workflow.add_node("context_retriever", _cc(context_retriever_node))
    workflow.add_node("context_interpreter", _cc(context_interpreter_node))
    workflow.add_node("readiness_gate", _cc(readiness_gate_node))
    workflow.add_node("sql_generator", _cc(sql_generator_node))
    workflow.add_node("sql_validator", _cc(sql_validator_node))
    workflow.add_node("recovery_agent", _cc(recovery_agent_node))
    workflow.add_node("result_finalizer", result_finalizer_node)

    # ── Present 계층 ──
    workflow.add_node("execute_sql", _cc(execute_sql_node))
    workflow.add_node("analyze_data", _cc(analyze_data_node))
    workflow.add_node("format_response", _cc(format_response_node))
    workflow.add_node("simple_responder", simple_responder_node)
    workflow.add_node("error_end", _handle_error)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 엣지 연결
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    # preprocess 제거 → intent_classifier가 시작 노드
    workflow.set_entry_point("intent_classifier")

    # ── Interpret 엣지 ──
    workflow.add_conditional_edges(
        "intent_classifier",
        _route_after_intent_classifier,
        {
            "clarification_handler": "clarification_handler",
            "simple_responder": "simple_responder",
            "normalize_query": "normalize_query",
            "reasoning_preparer": "reasoning_preparer",
            "error_end": "error_end",
        },
    )

    workflow.add_conditional_edges(
        "normalize_query",
        _route_after_normalize,
        {
            "error_end": "error_end",
            "clarification_handler": "clarification_handler",
            "reasoning_preparer": "reasoning_preparer",
        },
    )

    # ── Reason 엣지 ──
    workflow.add_edge("reasoning_preparer", "context_retriever")

    workflow.add_edge("context_retriever", "context_interpreter")
    workflow.add_edge("context_interpreter", "readiness_gate")

    workflow.add_conditional_edges(
        "readiness_gate",
        _route_after_readiness_gate,
        {
            "explore": "context_retriever",
            "generate_sql": "sql_generator",
            "recovery": "recovery_agent",
            "conclude_failure": "result_finalizer",
            "ask_user": "result_finalizer",
            "clarification_handler": "clarification_handler",
        },
    )

    workflow.add_conditional_edges(
        "sql_generator",
        _route_after_sql_generator,
        {
            "sql_validator": "sql_validator",
            "clarification_handler": "clarification_handler",
            "replan": "recovery_agent",
        },
    )

    workflow.add_conditional_edges(
        "sql_validator",
        _route_after_sql_validator,
        {
            "conclude_success": "result_finalizer",
            "fix_syntax": "sql_generator",
            "fix_local": "sql_generator",
            "replan": "recovery_agent",
            "conclude_failure": "result_finalizer",
        },
    )

    workflow.add_conditional_edges(
        "recovery_agent",
        _route_after_recovery_agent,
        {
            "context_retriever": "context_retriever",
            "sql_generator": "sql_generator",
            "result_finalizer": "result_finalizer",
            "clarification_handler": "clarification_handler",
        },
    )

    # ── Reason → Present 전환 ──
    workflow.add_conditional_edges(
        "result_finalizer",
        _route_after_result_finalizer,
        {
            "execute_sql": "execute_sql",
            "clarification_handler": "clarification_handler",
            "error_end": "error_end",
        },
    )

    # ── clarification_handler 후속: source_node로 복귀 ──
    workflow.add_conditional_edges(
        "clarification_handler",
        _route_after_clarify,
        {
            target: target
            for target in _VALID_RETURN_TARGETS
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
    workflow.add_edge("simple_responder", "format_response")
    workflow.add_edge("format_response", END)
    workflow.add_edge("error_end", END)

    return workflow


def create_app(checkpointer: Any = None) -> Any:
    """컴파일된 LangGraph 앱을 생성한다.

    Args:
        checkpointer: LangGraph checkpointer 인스턴스.
            None이면 체크포인트 없이 컴파일 (하위 호환).
    """
    workflow = build_pipeline()
    return workflow.compile(checkpointer=checkpointer)


# ── 싱글턴 캐시 ──

_compiled_app: Any = None


def get_compiled_app(checkpointer: Any = None) -> Any:
    """컴파일된 LangGraph 앱 싱글턴을 반환한다.

    최초 호출 시 checkpointer를 주입하여 컴파일한다.
    이후 호출에서는 checkpointer 인자를 무시하고 캐시된 앱을 반환한다.
    요청별 추적은 ``config={"callbacks": [handler]}`` 로 주입한다.
    """
    global _compiled_app  # noqa: PLW0603
    if _compiled_app is None:
        _compiled_app = create_app(checkpointer=checkpointer)
        logger.info("LangGraph 파이프라인 컴파일 완료 (싱글턴)")
    return _compiled_app


def reset_compiled_app() -> None:
    """테스트 등에서 싱글턴을 초기화할 때 사용한다."""
    global _compiled_app  # noqa: PLW0603
    _compiled_app = None
