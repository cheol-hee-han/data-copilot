"""planner 노드 — 질의 분해 + 초기 가설 수립 + 실행계획 생성.

최초 진입 전용 노드. 재계획은 recovery_planner 노드가 담당한다.
8-Slot 정규화 결과를 시드로 활용하여 query_decomposition을 구성하고,
UNRESOLVED 용어를 목록화한 뒤 가설과 실행계획을 수립한다.

Fast-Path 판정:
  유사 SQL 이력 고유사도 매칭 시 탐색 루프를 건너뛰고 즉시 SQL 생성.
"""

from __future__ import annotations

from typing import Any

from prototype.agentic_state import (
    AgenticCoreState,
    CandidateTable,
    ExecutionStep,
    Hypothesis,
    KnowledgeItem,
    StructuralHints,
)


async def planner_node(state: AgenticCoreState) -> dict:
    """질의를 분해하고 초기 탐색 가설을 수립한다."""
    updates: dict[str, Any] = {"phase": "PLANNING"}

    # ── Step 1: 정규화 결과에서 query_decomposition 시드 ──
    nq = state.normalized_query
    decomposition = _build_decomposition_from_normalized(nq)
    updates["query_decomposition"] = decomposition

    # ── Step 2: UNRESOLVED 용어 목록화 ──
    knowledge_items = _initialize_knowledge_items(nq, decomposition)
    updates["knowledge_items"] = knowledge_items

    # ── Step 3: 초기 컨텍스트 1차 수집 (기존 서비스 재사용) ──
    # 유사 SQL + 테이블 메타를 먼저 수집하여 가설 수립의 기반 마련
    initial_context = await _collect_initial_context(state.original_query, nq)

    # ── Step 4: 유사 SQL에서 구조적 힌트 추출 (sqlglot) ──
    structural_hints = _extract_hints_from_use_cases(
        initial_context.get("use_cases", [])
    )
    updates["structural_hints"] = structural_hints
    updates["explored_use_cases"] = initial_context.get("use_cases", [])

    # ── Step 5: 초기 후보 테이블 ──
    candidate_tables = _build_initial_candidates(
        initial_context.get("table_metas", []),
        nq,
    )
    updates["candidate_tables"] = candidate_tables

    # ── Step 6: Fast-Path 판정 ──
    if _should_fast_path(knowledge_items, structural_hints, candidate_tables, nq):
        updates["fast_path_triggered"] = True
        updates["phase"] = "GENERATING"
        return updates

    # ── Step 7: 가설 수립 (LLM) ──
    hypotheses = await _generate_hypotheses(
        state.original_query,
        decomposition,
        initial_context,
        knowledge_items,
    )
    updates["hypotheses"] = hypotheses

    # ── Step 8: 최우선 가설 선택 + 실행계획 생성 ──
    # C-07: 직접 mutation 대신 복사본으로 처리
    if hypotheses:
        top = hypotheses[0].model_copy()
        top.status = "ACTIVE"
        hypotheses[0] = top  # 복사본으로 교체
        updates["hypotheses"] = hypotheses
        updates["current_hypothesis"] = top
        updates["execution_plan"] = _build_execution_plan(
            top, knowledge_items, state.searched_queries,
        )
    else:
        # Cold Start fallback
        fallback = Hypothesis(
            hypothesis_id="H_FALLBACK",
            description="키워드 기반 직접 테이블 탐색",
            strategy="질의 키워드로 테이블 메타를 직접 검색",
            priority=0.1,
            status="ACTIVE",
        )
        updates["hypotheses"] = [fallback]
        updates["current_hypothesis"] = fallback
        updates["execution_plan"] = _build_fallback_plan(
            state.original_query, decomposition,
        )

    updates["current_step_index"] = 0
    updates["phase"] = "EXPLORING"
    return updates


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 내부 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _build_decomposition_from_normalized(nq: Any) -> dict:
    """8-Slot NormalizedQuery에서 query_decomposition을 구성한다."""
    if nq is None:
        return {"measures": [], "filters": [], "group_by": [], "order_limit": []}

    measures = []
    if hasattr(nq, "measures"):
        for m in nq.measures:
            measures.append({
                "term": getattr(m, "term", ""),
                "agg_function": getattr(m, "agg_function", ""),
            })

    filters = []
    if hasattr(nq, "filters"):
        for f in nq.filters:
            filters.append({
                "term": getattr(f, "column", ""),
                "operator": getattr(f, "operator", ""),
                "value": getattr(f, "value", ""),
            })

    group_by = []
    if hasattr(nq, "dimensions"):
        for d in nq.dimensions:
            if getattr(d, "role", "") == "GROUP":
                group_by.append(getattr(d, "term", ""))

    order_limit = []
    if hasattr(nq, "modifiers"):
        for mod in nq.modifiers:
            order_limit.append({
                "type": getattr(mod, "type", ""),
                "value": getattr(mod, "value", ""),
            })

    # required_concepts: 테이블 커버리지 계산에 사용
    required_concepts = []
    if hasattr(nq, "entities"):
        for e in nq.entities:
            required_concepts.append(getattr(e, "term", ""))
    required_concepts.extend(m.get("term", "") for m in measures)

    return {
        "measures": measures,
        "filters": filters,
        "group_by": group_by,
        "order_limit": order_limit,
        "required_concepts": required_concepts,
    }


def _initialize_knowledge_items(nq: Any, decomposition: dict) -> list[KnowledgeItem]:
    """정규화 결과에서 UNRESOLVED 지식 항목을 초기화한다."""
    items: list[KnowledgeItem] = []

    # 측정값 관련 용어
    for m in decomposition.get("measures", []):
        term = m.get("term", "")
        if term:
            items.append(KnowledgeItem(
                key=f"measure:{term}",
                status="UNRESOLVED",
            ))

    # 필터 조건의 코드값
    for f in decomposition.get("filters", []):
        term = f.get("term", "")
        value = f.get("value", "")
        if term and value:
            items.append(KnowledgeItem(
                key=f"filter:{term}={value}",
                status="UNRESOLVED",
            ))

    # 정규화 결과의 ambiguities
    if nq and hasattr(nq, "ambiguities"):
        for amb in (nq.ambiguities or []):
            items.append(KnowledgeItem(
                key=f"ambiguity:{amb}",
                status="UNRESOLVED",
            ))

    return items


async def _collect_initial_context(
    query: str, nq: Any,
) -> dict:
    """초기 컨텍스트 1차 수집 — 기존 서비스 재사용.

    TODO: 실제 구현 시 search_context_assembler.collect_context() 호출.
    프로토타입에서는 빈 결과를 반환한다.
    """
    # from src.services.search_context_assembler import collect_context
    # context = await collect_context(query, normalized_query=nq)
    return {
        "use_cases": [],        # Qdrant sql_history 검색 결과
        "table_metas": [],      # ES table_meta 검색 결과
        "report_sqls": [],      # ES report_sql 검색 결과
        "manuals": [],          # Qdrant biz_manual 검색 결과
    }


def _extract_hints_from_use_cases(
    use_cases: list[dict],
) -> StructuralHints:
    """유사 SQL에서 sqlglot 기반 구조적 힌트를 추출한다.

    TODO: 실제 구현 시 sql_hint_extractor.extract_structural_hints() 호출.
    """
    # from prototype.sql_hint_extractor import extract_structural_hints, merge_hints
    # hints_list = [
    #     extract_structural_hints(uc.get("sql", ""))
    #     for uc in use_cases if uc.get("sql")
    # ]
    # return merge_hints(hints_list)
    return StructuralHints()


def _build_initial_candidates(
    table_metas: list[dict], nq: Any,
) -> list[CandidateTable]:
    """초기 후보 테이블을 구성한다."""
    candidates: list[CandidateTable] = []
    for meta in table_metas:
        candidates.append(CandidateTable(
            table_name=meta.get("table_name", ""),
            role=meta.get("table_description", ""),
            relevant_columns=meta.get("columns", []),
        ))
    return candidates


def _should_fast_path(
    knowledge_items: list[KnowledgeItem],
    hints: StructuralHints,
    candidates: list[CandidateTable],
    nq: Any,
) -> bool:
    """Fast-Path 바이패스 조건 판정."""
    return (
        not hints.is_empty()                    # 구조적 힌트 확보
        and len(candidates) >= 1                # 테이블 후보 존재
        and all(                                # UNRESOLVED 없음
            ki.status != "UNRESOLVED"
            for ki in knowledge_items
        )
        and not (nq and hasattr(nq, "ambiguities") and nq.ambiguities)
    )


async def _generate_hypotheses(
    query: str,
    decomposition: dict,
    initial_context: dict,
    knowledge_items: list[KnowledgeItem],
) -> list[Hypothesis]:
    """LLM을 사용하여 탐색 가설을 수립한다.

    TODO: 실제 구현 시 LLM 호출.
    프로토타입에서는 기본 가설을 반환한다.
    """
    hypotheses: list[Hypothesis] = []

    # 가설 1: 유사 SQL 기반 접근
    if initial_context.get("use_cases"):
        hypotheses.append(Hypothesis(
            hypothesis_id="H1",
            description="유사 SQL 활용사례 기반 접근",
            based_on_use_case="initial",
            strategy="유사 SQL의 테이블/조인 구조를 참고하여 SQL 생성",
            priority=0.9,
        ))

    # 가설 2: 테이블 메타 직접 탐색
    hypotheses.append(Hypothesis(
        hypothesis_id="H2",
        description="테이블 메타 직접 탐색",
        strategy="질의 키워드로 테이블/컬럼 메타를 직접 검색",
        priority=0.5,
    ))

    # Cold Start fallback (반드시 포함)
    hypotheses.append(Hypothesis(
        hypothesis_id="H_FALLBACK",
        description="키워드 기반 직접 탐색",
        strategy="질의 키워드 조합으로 테이블을 직접 탐색",
        priority=0.1,
    ))

    return sorted(hypotheses, key=lambda h: h.priority, reverse=True)


def _build_execution_plan(
    hypothesis: Hypothesis,
    knowledge_items: list[KnowledgeItem],
    searched_queries: list[str],
) -> list[ExecutionStep]:
    """가설에 대한 실행계획을 생성한다.

    TODO: 실제 구현 시 LLM 기반 실행계획 생성.
    프로토타입에서는 기본 탐색 플로우를 반환한다.
    """
    steps: list[ExecutionStep] = []
    step_num = 1

    # Step 1: 활용사례 검색
    if hypothesis.based_on_use_case:
        steps.append(ExecutionStep(
            step=step_num,
            tool="search_use_cases",
            input=hypothesis.description,
            purpose="유사 활용사례에서 테이블/조인 구조 참고",
            expected_output="유사 SQL + 테이블 정보",
        ))
        step_num += 1

    # Step 2: 테이블 메타 검색
    for table in hypothesis.required_tables:
        steps.append(ExecutionStep(
            step=step_num,
            tool="search_table_meta",
            input=table,
            purpose=f"{table} 테이블의 컬럼 구조 확인",
            expected_output="컬럼 목록 + 설명",
        ))
        step_num += 1

    # Step 3: UNRESOLVED 용어 해소
    for ki in knowledge_items:
        if ki.status == "UNRESOLVED" and "filter:" in ki.key:
            col_name = ki.key.split(":")[1].split("=")[0]
            steps.append(ExecutionStep(
                step=step_num,
                tool="search_code_meta",
                input=col_name,
                purpose=f"{col_name}의 코드값 확인",
                expected_output="코드값 목록",
            ))
            step_num += 1

    return steps


def _build_fallback_plan(
    query: str, decomposition: dict,
) -> list[ExecutionStep]:
    """Cold Start fallback 실행계획."""
    keywords = decomposition.get("required_concepts", [])
    if not keywords:
        keywords = query.split()[:3]

    return [
        ExecutionStep(
            step=1,
            tool="search_table_meta",
            input=" ".join(keywords),
            purpose="질의 키워드로 관련 테이블 직접 검색",
            expected_output="관련 테이블 목록",
        ),
    ]
