"""sql_generator 노드 — 누적 지식 기반 SQL 생성.

CONFIRMED knowledge_items만 사용하여 SQL을 생성한다.
재진입 시 sql_fix_instruction을 프롬프트에 반드시 포함하고,
dead_ends를 참고하여 이전 실패 패턴을 반복하지 않는다.

기존 서비스 재사용:
  - sql_prompt_assembler.generate_sql() (프롬프트 조립 + LLM 호출)
  - SQL_GENERATION_RULES 시스템 프롬프트
"""

from __future__ import annotations

from typing import Any

from prototype.agentic_state import AgenticCoreState


async def sql_generator_node(state: AgenticCoreState) -> dict:
    """누적 지식을 컨텍스트로 SQL을 생성한다."""
    updates: dict[str, Any] = {"phase": "GENERATING"}

    # 루프 가드 업데이트
    loop_guard = state.loop_guard.model_copy()
    loop_guard.increment_generate()
    updates["loop_guard"] = loop_guard

    # ── 프롬프트 컨텍스트 조립 ──
    prompt_context = _build_generation_context(state)

    # ── LLM 호출 ──
    generated_sql = await _call_llm_generate(prompt_context)

    updates["generated_sql"] = generated_sql
    updates["sql_fix_instruction"] = None  # 다음 검증 결과 수신을 위해 초기화
    updates["sql_validation_result"] = None

    return updates


def _build_generation_context(state: AgenticCoreState) -> dict:
    """SQL 생성 프롬프트에 주입할 컨텍스트를 조립한다."""
    context: dict[str, Any] = {
        "query": state.original_query,
        "query_decomposition": state.query_decomposition,
    }

    # 1. CONFIRMED knowledge_items만 사용
    confirmed = state.get_confirmed_knowledge()
    context["confirmed_terms"] = [
        {"key": ki.key, "value": ki.value, "source": ki.source}
        for ki in confirmed
    ]

    # 2. 후보 테이블 + 조인 경로
    # knowledge_items에서 CONFIRMED/PROBABLE인 테이블만 사용
    confirmed_table_names = {
        ki.key.removeprefix("table:")
        for ki in state.knowledge_items
        if ki.key.startswith("table:") and ki.status in ("CONFIRMED", "PROBABLE")
    }
    context["tables"] = [
        {
            "name": ct.table_name,
            "role": ct.role,
            "columns": ct.relevant_columns,
            "join_keys": ct.join_keys,
        }
        for ct in state.candidate_tables
        if ct.table_name in confirmed_table_names
        or len(state.candidate_tables) == 1  # 단일 테이블이면 그대로 사용
    ]
    context["join_path"] = state.confirmed_join_path

    # 3. 구조적 힌트 (sqlglot 파싱 결과)
    if not state.structural_hints.is_empty():
        context["structural_hints"] = state.structural_hints.to_prompt_text()

    # 4. 활용사례 SQL (구조 참고용, 원문 대신 힌트 우선)
    if state.explored_use_cases:
        context["reference_sqls"] = [
            uc.get("sql", "")
            for uc in state.explored_use_cases[:3]
            if uc.get("sql")
        ]

    # 5. Dead-ends (반복 방지)
    if state.dead_ends:
        context["dead_ends"] = [
            f"[{de.failure_type}] {de.reason} (테이블: {', '.join(de.tried_tables)})"
            for de in state.dead_ends
        ]

    # 6. 재생성 시 fix_instruction
    if state.sql_fix_instruction:
        context["fix_instruction"] = state.sql_fix_instruction

    return context


async def _call_llm_generate(context: dict) -> str:
    """LLM을 호출하여 SQL을 생성한다.

    TODO: 실제 구현 시 sql_prompt_assembler.generate_sql() 호출.
    프롬프트에 다음을 반드시 포함:
      - confirmed_terms (CONFIRMED된 용어 매핑)
      - structural_hints (sqlglot 파싱 힌트)
      - dead_ends (실패한 접근 방식)
      - fix_instruction (재생성 사유)
      - 자기검증 체크리스트

    프로토타입에서는 placeholder SQL을 반환한다.
    """
    # from src.services.sql_prompt_assembler import generate_sql
    # sql = await generate_sql(
    #     user_query=context["query"],
    #     context_info=...,  # context를 ContextInfo로 변환
    #     system_prompt=SQL_GENERATION_RULES,
    #     additional_context=_format_agentic_context(context),
    # )
    return "SELECT 1 -- placeholder"
