"""context_explorer 노드 — 실행계획 스텝을 내부 루프로 순차 실행.

탐색은 노드 내부에서 루프로 처리하고, 판단만 외부 노드(confidence_evaluator)로 분리한다.
각 스텝 결과를 즉시 knowledge_items에 반영하며, 조기 탈출 조건 충족 시 루프를 종료한다.

기존 서비스 재사용:
  - search_context_assembler 의 개별 검색 함수
  - search_query_embedder (벡터 임베딩)
  - table_meta_enricher (3-View 보강)
  - sql_hint_extractor (sqlglot 구조적 힌트)
"""

from __future__ import annotations

from typing import Any

from prototype.agentic_state import (
    AgenticCoreState,
    CandidateTable,
    ExecutionStep,
    KnowledgeItem,
    MAX_TOOL_CALLS,
)
from prototype.confidence_scorer import ReadinessVerdict, evaluate_readiness


async def context_explorer_node(state: AgenticCoreState) -> dict:
    """실행계획의 스텝들을 내부 루프로 실행한다."""
    updates: dict[str, Any] = {"phase": "EXPLORING"}

    execution_plan = list(state.execution_plan)
    knowledge_items = list(state.knowledge_items)
    candidate_tables = list(state.candidate_tables)
    searched_queries = list(state.searched_queries)
    sampled_tables = list(state.sampled_tables)
    explored_use_cases = list(state.explored_use_cases)
    total_tool_calls = state.loop_guard.total_tool_calls

    for i, step in enumerate(execution_plan):
        if step.status != "PENDING":
            continue

        # 루프 가드: 총 도구 호출 횟수 초과 시 탈출
        if total_tool_calls >= MAX_TOOL_CALLS:
            break

        # 중복 방지
        if step.tool in ("search_use_cases", "search_table_meta",
                         "search_code_meta", "search_report_sql",
                         "search_manual", "search_biz_terms"):
            if step.input in searched_queries:
                step.status = "SKIPPED"
                step.insight = "이미 검색한 쿼리 — 스킵"
                continue

        if step.tool == "get_sample_data":
            table_name = step.input.split(",")[0].strip()
            if table_name in sampled_tables:
                step.status = "SKIPPED"
                step.insight = "이미 샘플 조회한 테이블 — 스킵"
                continue

        # ── 도구 실행 ──
        try:
            result = await _execute_tool(step)
            step.status = "DONE"
            total_tool_calls += 1

            # 검색 쿼리 기록
            if step.tool != "get_sample_data":
                searched_queries.append(step.input)
            else:
                table_name = step.input.split(",")[0].strip()
                sampled_tables.append(table_name)

            # ── 결과 해석 + knowledge 갱신 ──
            insight, new_knowledge, new_tables = _interpret_result(
                step, result, knowledge_items,
            )
            step.insight = insight

            knowledge_items.extend(new_knowledge)
            candidate_tables.extend(new_tables)

            if step.tool == "search_use_cases" and result:
                explored_use_cases.extend(result)

        except Exception as e:
            step.status = "FAILED"
            step.insight = f"도구 실행 실패: {e}"
            total_tool_calls += 1

        # ── 조기 탈출 조건 (evaluate_readiness 단일 판정 함수 사용) ──
        temp_state = AgenticCoreState(
            knowledge_items=knowledge_items,
            candidate_tables=candidate_tables,
            explored_use_cases=explored_use_cases,
            confirmed_join_path=state.confirmed_join_path,
            query_decomposition=state.query_decomposition,
            execution_plan=execution_plan,
            loop_guard=state.loop_guard,
            hypotheses=state.hypotheses,
            current_hypothesis=state.current_hypothesis,
        )
        verdict = evaluate_readiness(temp_state)
        if verdict == ReadinessVerdict.GENERATE:
            break

    # 루프 가드 업데이트
    loop_guard = state.loop_guard.model_copy()
    loop_guard.total_tool_calls = total_tool_calls

    updates.update({
        "execution_plan": execution_plan,
        "knowledge_items": knowledge_items,
        "candidate_tables": candidate_tables,
        "searched_queries": searched_queries,
        "sampled_tables": sampled_tables,
        "explored_use_cases": explored_use_cases,
        "loop_guard": loop_guard,
    })

    return updates


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 도구 실행
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _execute_tool(step: ExecutionStep) -> Any:
    """실행계획 스텝의 도구를 호출한다.

    TODO: 실제 구현 시 기존 커넥터/서비스를 호출.
    프로토타입에서는 빈 결과를 반환한다.
    """
    tool_map = {
        "search_use_cases": _tool_search_use_cases,
        "search_table_meta": _tool_search_table_meta,
        "search_code_meta": _tool_search_code_meta,
        "search_report_sql": _tool_search_report_sql,
        "search_manual": _tool_search_manual,
        "search_biz_terms": _tool_search_biz_terms,
        "get_sample_data": _tool_get_sample_data,
    }
    tool_fn = tool_map.get(step.tool)
    if tool_fn:
        return await tool_fn(step.input)
    return None


async def _tool_search_use_cases(query: str) -> list[dict]:
    """유사 활용사례 검색.

    TODO: QdrantConnector.search("sql_history", query)
    """
    # from src.connectors.manager import get_connector_manager
    # mgr = get_connector_manager()
    # results = await mgr.qdrant.search("sql_history", query, limit=10)
    return []


async def _tool_search_table_meta(query: str) -> list[dict]:
    """테이블/컬럼 메타 검색.

    TODO: ElasticSearchConnector.search("table_meta", query)
    """
    return []


async def _tool_search_code_meta(column_name: str) -> list[dict]:
    """코드값 목록 검색.

    TODO: ElasticSearchConnector.search("code_meta", column_name)
    """
    return []


async def _tool_search_report_sql(query: str) -> list[dict]:
    """보고서 SQL 검색.

    TODO: ElasticSearchConnector.search("report_sql", query)
    """
    return []


async def _tool_search_manual(query: str) -> list[dict]:
    """업무 매뉴얼 검색.

    TODO: QdrantConnector.search("biz_manual", query)
    """
    return []


async def _tool_search_biz_terms(term: str) -> list[dict]:
    """금융 용어사전 검색.

    TODO: MongoConnector.search_biz_terms(term)
    """
    # from src.connectors.manager import get_connector_manager
    # mgr = get_connector_manager()
    # results = await mgr.mongo.search_biz_terms(term)
    return []


async def _tool_get_sample_data(input_str: str) -> list[dict]:
    """샘플 데이터 조회 (LIMIT 10).

    TODO: InfoDBConnector.execute_query(
        f"SELECT {columns} FROM {table} LIMIT 10"
    )
    """
    return []


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 결과 해석
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _interpret_result(
    step: ExecutionStep,
    result: Any,
    existing_knowledge: list[KnowledgeItem],
) -> tuple[str, list[KnowledgeItem], list[CandidateTable]]:
    """도구 결과를 해석하여 insight, new_knowledge, new_tables를 반환.

    [관찰 메모 작성 기준]
    결과를 그대로 저장하지 말고, 현재 질의와의 관련성을 판단해서
    "무엇을 알게 됐는가"를 한 문장으로 기록한다.
    """
    new_knowledge: list[KnowledgeItem] = []
    new_tables: list[CandidateTable] = []

    if not result:
        return f"{step.tool} 결과 없음", new_knowledge, new_tables

    if step.tool == "search_table_meta" and isinstance(result, list):
        for meta in result:
            table_name = meta.get("table_name", "")
            columns = [
                c.get("column_name", "")
                for c in meta.get("columns", [])
            ]
            # 구조 데이터 운반용 CandidateTable
            new_tables.append(CandidateTable(
                table_name=table_name,
                role=meta.get("table_description", ""),
                relevant_columns=columns,
            ))
            # 테이블 적합성 판단용 KnowledgeItem
            # 메타에서 존재 확인 → CANDIDATE (샘플 확인 후 CONFIRMED로 승격)
            # is_critical은 rule-based 기본값 True로 설정.
            # 실제 구현에서는 explore_observe LLM이 도구 결과 해석 시
            # "이 테이블이 SQL에 필수인가?"를 판단하여 is_critical을 결정한다.
            new_knowledge.append(KnowledgeItem(
                key=f"table:{table_name}",
                value=f"{meta.get('table_description', '')} "
                      f"(컬럼: {', '.join(columns[:5])})",
                confidence=0.4,
                status="CANDIDATE",
                source="테이블메타",
                evidence=[f"ES table_meta에서 {table_name} 발견"],
                is_critical=True,  # 기본 보수적. LLM 해석 후 False로 변경 가능
            ))
        return (
            f"테이블 {len(result)}건 발견: "
            f"{', '.join(m.get('table_name', '') for m in result)}",
            new_knowledge,
            new_tables,
        )

    if step.tool == "search_code_meta" and isinstance(result, list):
        for code_entry in result:
            col = code_entry.get("column_name", "")
            values = code_entry.get("values", [])
            if col and values:
                new_knowledge.append(KnowledgeItem(
                    key=f"code:{col}",
                    value=f"{col} IN ({', '.join(repr(v) for v in values[:5])})",
                    confidence=0.7,
                    status="PROBABLE",
                    source="코드메타",
                    evidence=[f"코드메타에서 {len(values)}개 값 확인"],
                ))
        return (
            f"코드값 {len(result)}건 확인",
            new_knowledge,
            new_tables,
        )

    if step.tool == "get_sample_data" and isinstance(result, list):
        # 샘플 데이터 관찰 → LLM 해석 필요 (프로토타입에서는 기본 처리)
        return (
            f"샘플 데이터 {len(result)}건 조회 완료",
            new_knowledge,
            new_tables,
        )

    return f"{step.tool} 처리 완료", new_knowledge, new_tables
