"""메인 파이프라인 ↔ 에이전틱 코어 통합 프로토타입.

기존 pipeline.py에 최소한의 변경을 가하여 에이전틱 코어를 조건부로 연결한다.
설정 플래그(agentic_core_enabled)로 기존 선형 파이프라인과 런타임 전환 가능.

이 파일은 실제 pipeline.py에 적용할 변경사항의 프로토타입이다.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph

# ── 기존 import 유지 ──
# from src.agents.state.state import PipelineState, QueryStatus, IntentType
# from src.agents.nodes.* import *
# from src.config import settings

# ── 에이전틱 코어 추가 import ──
# from prototype.agentic_core import (
#     build_agentic_core,
#     pipeline_to_agentic,
#     agentic_to_pipeline,
# )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 변경 1: 라우팅 함수 수정
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _next_after_intent_v2() -> str:
    """의도 분류 후 데이터 처리 경로를 결정한다.

    기존 _next_after_intent() 대체.
    normalization_enabled에 따라 정규화 경로 결정 후,
    agentic_core_enabled에 따라 에이전틱 코어 또는 기존 선형 경로로 분기.
    """
    # from src.config import settings
    settings_normalization_enabled = True  # placeholder
    settings_agentic_core_enabled = False  # placeholder

    if settings_normalization_enabled:
        return "normalize_query"
    # 정규화 비활성 시: 에이전틱 코어 또는 기존 경로
    if settings_agentic_core_enabled:
        return "agentic_entry"
    return "collect_context"


def _route_after_normalize_v2(state: Any) -> str:
    """정규화 후 라우팅 — 에이전틱 코어 분기점.

    기존에는 normalize_query → collect_context 직결.
    에이전틱 코어 활성화 시 normalize_query → agentic_entry로 변경.
    """
    settings_agentic_core_enabled = False  # placeholder

    if settings_agentic_core_enabled:
        return "agentic_entry"
    return "collect_context"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 변경 2: 에이전틱 코어 래퍼 노드
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def agentic_entry_node(state: Any) -> dict:
    """PipelineState → AgenticCoreState 변환 + 서브그래프 실행 + 역변환.

    메인 파이프라인의 단일 노드로 등록되며 내부에서 에이전틱 코어
    서브그래프를 실행한 뒤 결과를 PipelineState 형태로 반환한다.
    """
    from prototype.agentic_core import (
        build_agentic_core,
        pipeline_to_agentic,
        agentic_to_pipeline,
    )

    # Step 1: PipelineState → AgenticCoreState
    agentic_input = pipeline_to_agentic(state)

    # Step 2: 서브그래프 실행
    agentic_graph = build_agentic_core()
    compiled = agentic_graph.compile()
    agentic_result = await compiled.ainvoke(agentic_input.model_dump())

    # Step 3: AgenticCoreState → PipelineState
    from prototype.agentic_state import AgenticCoreState
    agentic_state = AgenticCoreState(**agentic_result)
    return agentic_to_pipeline(agentic_state)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 변경 3: build_pipeline 수정 — 조건부 에이전틱 코어 연결
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def build_pipeline_v2() -> StateGraph:
    """기존 build_pipeline()에 에이전틱 코어를 조건부로 추가한 버전.

    변경사항 요약:
      1. normalize_query → collect_context 직결 엣지 제거
      2. normalize_query → conditional_edges (agentic_entry / collect_context)
      3. agentic_entry 노드 등록
      4. agentic_entry → execute_sql 엣지 추가 (에이전틱 코어가 SQL 검증까지 완료)
    """
    # --- 프로토타입: 실제 코드 대신 의사코드로 표현 ---
    #
    # workflow = StateGraph(PipelineState)
    #
    # # 기존 노드 등록 (변경 없음)
    # workflow.add_node("preprocess", preprocess_node)
    # workflow.add_node("resolve_history", resolve_history_node)
    # workflow.add_node("classify_intent", classify_intent_node)
    # workflow.add_node("normalize_query", normalize_query_node)
    # workflow.add_node("clarify", clarify_node)
    # workflow.add_node("collect_context", collect_context_node)      # 선형 모드용
    # workflow.add_node("enrich_context", enrich_context_node)        # 선형 모드용
    # workflow.add_node("generate_sql", generate_sql_node)            # 선형 모드용
    # workflow.add_node("validate_sql", validate_sql_node)            # 선형 모드용
    # workflow.add_node("execute_sql", execute_sql_node)
    # workflow.add_node("analyze_data", analyze_data_node)
    # workflow.add_node("format_response", format_response_node)
    # workflow.add_node("error_end", _handle_error)
    #
    # # [신규] 에이전틱 코어 래퍼 노드
    # if settings.agentic_core_enabled:
    #     workflow.add_node("agentic_entry", agentic_entry_node)
    #
    # # 기존 엣지 (변경 없음)
    # workflow.set_entry_point("preprocess")
    # workflow.add_conditional_edges("preprocess", _route_after_preprocess, {...})
    # workflow.add_conditional_edges("resolve_history", _route_after_history_resolve, {...})
    # workflow.add_conditional_edges("classify_intent", _route_after_intent, {...})
    # workflow.add_edge("clarify", END)
    #
    # # [변경] normalize_query 이후 조건부 분기
    # # 기존: workflow.add_edge("normalize_query", "collect_context")
    # # 신규:
    # if settings.agentic_core_enabled:
    #     workflow.add_conditional_edges(
    #         "normalize_query",
    #         _route_after_normalize_v2,
    #         {
    #             "agentic_entry": "agentic_entry",
    #             "collect_context": "collect_context",  # fallback
    #         },
    #     )
    #     # 에이전틱 코어 → execute_sql (SQL 검증까지 완료됨)
    #     workflow.add_conditional_edges(
    #         "agentic_entry",
    #         _route_after_agentic,
    #         {
    #             "execute_sql": "execute_sql",
    #             "clarify": "clarify",
    #             "error_end": "error_end",
    #         },
    #     )
    # else:
    #     workflow.add_edge("normalize_query", "collect_context")
    #
    # # 기존 선형 경로 (agentic 비활성 시)
    # workflow.add_edge("collect_context", "enrich_context")
    # workflow.add_edge("enrich_context", "generate_sql")
    # workflow.add_edge("generate_sql", "validate_sql")
    # workflow.add_conditional_edges("validate_sql", _route_after_validation, {...})
    #
    # # 공통 후처리
    # workflow.add_conditional_edges("execute_sql", _route_after_execution, {...})
    # workflow.add_edge("analyze_data", "format_response")
    # workflow.add_edge("format_response", END)
    # workflow.add_edge("error_end", END)
    #
    # return workflow

    pass  # 프로토타입 — 위 의사코드 참조


def _route_after_agentic(state: Any) -> str:
    """에이전틱 코어 완료 후 라우팅.

    성공: validated_sql 존재 → execute_sql
    명확화: awaiting_clarification → clarify
    실패: error_message 존재 → error_end
    """
    if state.get("awaiting_clarification"):
        return "clarify"
    if state.get("error_message"):
        return "error_end"
    if state.get("validated_sql"):
        return "execute_sql"
    return "error_end"
