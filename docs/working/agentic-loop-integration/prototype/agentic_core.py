"""에이전틱 코어 서브그래프 — LangGraph 기반 점진적 탐색 루프.

메인 파이프라인의 중간부(컨텍스트 수집 → SQL 생성 → 검증)를 대체하는
에이전틱 서브그래프를 정의한다.

노드 이름 (의인화):
  planner              — 질의 분해 + 가설 수립 + 실행계획 생성
  context_explorer     — 실행계획 스텝을 내부 루프로 실행
  confidence_evaluator — 현재 지식 상태 평가 (rule-based)
  sql_generator        — 누적 지식 기반 SQL 생성
  sql_validator        — 3-레이어 SQL 검증
  recovery_planner     — 실패 분석 + 교훈 도출 + 새 가설 수립
  result_finalizer     — 성공/실패 최종 출력 + 상태 역변환

그래프 구조:
  planner → context_explorer → confidence_evaluator
    ├→ context_explorer (탐색 계속)
    ├→ sql_generator → sql_validator
    │     ├→ result_finalizer (성공)
    │     ├→ sql_generator (syntax/local fix)
    │     └→ recovery_planner (structural)
    ├→ recovery_planner → context_explorer
    ├→ result_finalizer (성공/실패)
    └→ result_finalizer (CONFLICTED → ask_user)
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph

from prototype.agentic_state import AgenticCoreState
from prototype.confidence_scorer import evaluate_readiness

# ── 개별 노드 import (각 노드는 별도 모듈) ──
from prototype.nodes.planner import planner_node
from prototype.nodes.context_explorer import context_explorer_node
from prototype.nodes.confidence_evaluator import confidence_evaluator_node
from prototype.nodes.sql_generator import sql_generator_node
from prototype.nodes.sql_validator import sql_validator_node
from prototype.nodes.recovery_planner import recovery_planner_node
from prototype.nodes.result_finalizer import result_finalizer_node


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 라우팅 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def route_after_planner(state: AgenticCoreState) -> str:
    """planner 노드 후 라우팅 — Fast-Path 판정."""
    if state.fast_path_triggered:
        return "sql_generator"
    return "context_explorer"


def route_from_evaluator(state: AgenticCoreState) -> str:
    """confidence_evaluator 후 다음 행동을 결정한다.

    evaluate_readiness() 단일 판정 함수에 위임하여
    context_explorer 조기 탈출과 동일한 판단 로직을 보장한다.
    """
    return evaluate_readiness(state).value


def route_from_validator(state: AgenticCoreState) -> str:
    """sql_validator 후 실패 유형별 라우팅.

    실패 유형 분류:
      SUCCESS           → result_finalizer (성공)
      FAIL_SYNTAX       → sql_generator (syntax fix)
      FAIL_SEMANTIC_LOCAL → sql_generator (local fix, max 2)
      FAIL_STRUCTURAL   → recovery_planner
      FAIL_EMPTY        → recovery_planner
      FAIL_DB_ERROR     → recovery_planner

    C-24: Fast-Path 실패 시 context_explorer로 복귀하여 정상 탐색 루프 시작.
    """
    result = state.sql_validation_result
    if result is None:
        return "conclude_failure"

    # C-24: Fast-Path에서 검증 실패 시 탐색 루프로 복귀
    if state.fast_path_triggered and result.overall != "SUCCESS":
        return "explore_after_fast_path"

    match result.overall:
        case "SUCCESS":
            return "conclude_success"

        case "FAIL_SYNTAX":
            if state.loop_guard.generate_attempts < 4:
                return "fix_syntax"
            return "conclude_failure"

        case "FAIL_SEMANTIC_LOCAL":
            if state.loop_guard.should_escalate_to_structural():
                return "replan"
            if state.loop_guard.generate_attempts < 4:
                return "fix_local"
            return "conclude_failure"

        case "FAIL_STRUCTURAL" | "FAIL_EMPTY" | "FAIL_DB_ERROR":
            return "replan"

        case _:
            return "conclude_failure"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 서브그래프 빌더
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def build_agentic_core() -> StateGraph:
    """에이전틱 코어 서브그래프를 구성한다."""
    graph = StateGraph(AgenticCoreState)

    # 노드 등록 (의인화 이름)
    graph.add_node("planner", planner_node)
    graph.add_node("context_explorer", context_explorer_node)
    graph.add_node("confidence_evaluator", confidence_evaluator_node)
    graph.add_node("sql_generator", sql_generator_node)
    graph.add_node("sql_validator", sql_validator_node)
    graph.add_node("recovery_planner", recovery_planner_node)
    graph.add_node("result_finalizer", result_finalizer_node)

    # 진입점
    graph.set_entry_point("planner")

    # planner → Fast-Path 판정
    graph.add_conditional_edges(
        "planner",
        route_after_planner,
        {
            "sql_generator": "sql_generator",
            "context_explorer": "context_explorer",
        },
    )

    # context_explorer → confidence_evaluator
    graph.add_edge("context_explorer", "confidence_evaluator")

    # confidence_evaluator → 분기
    graph.add_conditional_edges(
        "confidence_evaluator",
        route_from_evaluator,
        {
            "explore": "context_explorer",
            "generate_sql": "sql_generator",
            "replan": "recovery_planner",
            "conclude_failure": "result_finalizer",
            "ask_user": "result_finalizer",
        },
    )

    # sql_generator → sql_validator
    graph.add_edge("sql_generator", "sql_validator")

    # sql_validator → 실패 유형별 분기
    graph.add_conditional_edges(
        "sql_validator",
        route_from_validator,
        {
            "conclude_success": "result_finalizer",
            "fix_syntax": "sql_generator",
            "fix_local": "sql_generator",
            "replan": "recovery_planner",
            "conclude_failure": "result_finalizer",
            "explore_after_fast_path": "context_explorer",  # C-24
        },
    )

    # recovery_planner → context_explorer (새 가설 탐색)
    graph.add_edge("recovery_planner", "context_explorer")

    # result_finalizer → END
    graph.add_edge("result_finalizer", END)

    return graph


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 메인 파이프라인 ↔ 에이전틱 코어 변환 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def pipeline_to_agentic(pipeline_state: dict) -> AgenticCoreState:
    """PipelineState → AgenticCoreState 입력 변환.

    메인 파이프라인에서 에이전틱 코어 서브그래프로 진입할 때 호출한다.
    """
    return AgenticCoreState(
        original_query=pipeline_state.get("preprocessed_input", ""),
        normalized_query=pipeline_state.get("normalized_query"),
        intent=pipeline_state.get("intent", ""),
        conversation_history=pipeline_state.get(
            "conversation_history", [],
        ),  # C-02: 멀티턴 대화 맥락 전달
    )


def agentic_to_pipeline(agentic_state: AgenticCoreState) -> dict:
    """AgenticCoreState → PipelineState 출력 변환.

    에이전틱 코어 서브그래프에서 메인 파이프라인으로 복귀할 때 호출한다.
    """
    from src.models.context import ColumnMeta, ContextInfo, TableMeta

    # knowledge_items에서 CONFIRMED 테이블 추출
    # → candidate_tables에서 구조 정보 조회
    confirmed_table_names = {
        ki.key.removeprefix("table:")
        for ki in agentic_state.knowledge_items
        if ki.key.startswith("table:") and ki.status == "CONFIRMED"
    }
    # C-03: relevant_columns → ColumnMeta 리스트로 변환
    table_metas = [
        TableMeta(
            table_name=ct.table_name,
            table_description=ct.role,
            columns=[
                ColumnMeta(
                    column_name=col,
                    column_description="",
                    data_type="",
                    is_pii=False,
                )
                for col in ct.relevant_columns
            ],
        )
        for ct in agentic_state.candidate_tables
        if ct.table_name in confirmed_table_names
    ]

    result: dict[str, Any] = {
        "generated_sql": agentic_state.generated_sql or "",
        "validated_sql": agentic_state.validated_sql or "",
        "context": ContextInfo(table_metas=table_metas),
    }

    # C-01: trace_entries → trace_log 변환
    if agentic_state.trace_entries:
        result["trace_log"] = agentic_state.trace_entries

    # 실패 시 에러 정보 전달
    if agentic_state.final_status == "failure":
        result["error_message"] = agentic_state.exploration_summary
        result["status"] = "ERROR"

    # 사용자 확인 필요 시 명확화 플래그
    if agentic_state.needs_user_input:
        result["clarification_question"] = agentic_state.user_question
        result["awaiting_clarification"] = True

    return result
