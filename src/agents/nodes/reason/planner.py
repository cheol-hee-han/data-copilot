"""planner 노드 — 질의 분해 + 초기 가설 수립 + 실행계획 생성.

reason 계층의 첫 번째 노드. 최초 진입 전용이며 재계획은 recovery_planner가 담당한다.
8-Slot 정규화 결과(normalized_query)를 시드로 활용하여 query_decomposition을 구성하고,
UNRESOLVED 용어를 KnowledgeItem으로 목록화한 뒤 LLM으로 가설과 실행계획을 수립한다.

Fast-Path 판정:
    유사 SQL 이력에서 고유사도 매칭(≥ threshold)이 발견되면
    탐색 루프(context_explorer)를 건너뛰고 즉시 sql_generator로 진행한다.

핵심 함수:
    - planner_node: 메인 노드 함수
    - _build_decomposition_from_normalized: NormalizedQuery → query_decomposition 변환
    - _initialize_knowledge_items: 정규화 슬롯에서 UNRESOLVED 용어 추출
    - _build_initial_candidates: 유사 SQL에서 초기 후보 테이블 목록 구성
    - _should_fast_path: Fast-Path 진입 조건 판정
    - _build_fallback_plan: LLM 실패 시 rule-based 기본 실행계획 생성

위임 구조:
    - 프롬프트: system_prompts.py의 REASON_PLAN

v2.0 (2026-03-25): LLM 기반 가설 생성으로 전환 — 외부 프롬프트 사용.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from src.agents.models.normalization import NormalizedQuery

from src.agents.state.state import (
    PipelineState,
    CandidateTable,
    ConfidenceStatus,
    ExecutionStep,
    Hypothesis,
    HypothesisStatus,
    KnowledgeItem,
    Phase,
    StructuralHints,
)
from src.agents.nodes.reason.tools import (
    extract_hints_from_use_cases,
    search_table_meta,
    search_use_cases,
)
from src.agents.nodes.system_prompts import PLANNER_SYSTEM
from src.utils.llm import llm_call_with_parse_retry, ParseError
from src.utils.llm.response import extract_json
from src.utils.llm.prompt import render_prompt
from src.utils.logger import get_logger
from src.utils.tracker import record_prompt_variables
from src.utils.truncate import truncate_log

logger = get_logger(__name__)


async def planner_node(state: PipelineState) -> dict:
    """질의를 분해하고 초기 탐색 가설을 수립한다."""
    reason = state.reason.model_copy(deep=True)
    reason.phase = Phase.PLANNING

    nq = state.normalized_query
    decomposition = _build_decomposition_from_normalized(nq)
    reason.query_decomposition = decomposition

    knowledge_items = _initialize_knowledge_items(
        nq, decomposition, state.preprocessed_input,
    )
    reason.knowledge_items = knowledge_items

    # 초기 컨텍스트 1차 수집 (기존 서비스 재사용)
    initial_context = await _collect_initial_context(state.preprocessed_input, nq)
    searched_queries = list(reason.searched_queries)
    if state.preprocessed_input:
        searched_queries.append(state.preprocessed_input)
    reason.searched_queries = searched_queries  # C-17

    # 유사 SQL에서 구조적 힌트 추출 (sqlglot)
    structural_hints = extract_hints_from_use_cases(
        initial_context.get("use_cases", []),
    )
    reason.explored_use_cases = initial_context.get("use_cases", [])

    # 초기 후보 테이블
    candidate_tables = _build_initial_candidates(
        initial_context.get("table_metas", []), nq,
    )
    reason.candidate_tables = candidate_tables

    # Fast-Path 판정
    if _should_fast_path(knowledge_items, structural_hints, candidate_tables, nq):
        reason.fast_path_triggered = True
        reason.phase = Phase.GENERATING
        logger.info("planner: Fast-Path 트리거")
        return {"reason": reason}

    # 가설 수립 (LLM)
    hypotheses = await _generate_hypotheses(
        state.preprocessed_input, decomposition,
        initial_context, knowledge_items,
        structural_hints,
        searched_queries=searched_queries,
        candidate_tables=candidate_tables,
    )
    reason.hypotheses = hypotheses

    # 최우선 가설 선택 + 실행계획 생성 (C-07: 직접 mutation 방지)
    if hypotheses:
        top = hypotheses[0].model_copy()
        top.status = HypothesisStatus.ACTIVE
        hypotheses[0] = top
        reason.hypotheses = hypotheses
        reason.current_hypothesis = top
        # search_use_cases input: 원본 + 정규화 결합으로 벡터 검색 커버리지 극대화
        rewritten = (
            getattr(nq, "rewritten_query", "")
            if nq and hasattr(nq, "rewritten_query")
            else ""
        )
        original = state.preprocessed_input or ""
        if rewritten and rewritten != original:
            use_case_query = f"{original}\n{rewritten}"
        else:
            use_case_query = original
        reason.execution_plan = _build_execution_plan(
            top, knowledge_items, searched_queries,
            structural_hints, candidate_tables,
            original_query=use_case_query,
        )
    else:
        fallback = Hypothesis(
            hypothesis_id="H_FALLBACK",
            description="키워드 기반 직접 테이블 탐색",
            strategy="질의 키워드로 테이블 메타를 직접 검색",
            priority=0.1,
            status=HypothesisStatus.ACTIVE,
        )
        reason.hypotheses = [fallback]
        reason.current_hypothesis = fallback
        reason.execution_plan = _build_fallback_plan(
            state.preprocessed_input, decomposition,
        )

    reason.phase = Phase.EXPLORING

    # ── 추적: planner 결과 ──
    logger.info(
        "planner 완료",
        hypotheses=len(reason.hypotheses),
        top_hypothesis=(
            truncate_log(reason.current_hypothesis.description)
            if reason.current_hypothesis else "(없음)"
        ),
        execution_steps=len(reason.execution_plan),
        knowledge_items=len(reason.knowledge_items),
        candidate_tables=len(reason.candidate_tables),
        use_cases=len(reason.explored_use_cases),
    )

    return {"reason": reason}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 내부 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _build_decomposition_from_normalized(
    nq: NormalizedQuery | None,
) -> dict:
    """8-Slot NormalizedQuery에서 query_decomposition을 구성한다."""
    empty = {"measures": [], "filters": [], "group_by": [], "order_limit": []}
    if nq is None:
        return empty

    measures = [
        {"term": m.term, "agg_function": m.agg_function}
        for m in nq.measures
    ]

    filters = [
        {"term": f.target, "operator": f.filter_type, "value": f.values}
        for f in nq.filters
    ]

    group_by = [
        d.term for d in nq.dimensions if d.role == "GROUP"
    ]

    order_limit = [
        {"type": mod.type, "value": str(mod.limit or mod.by or "")}
        for mod in nq.modifiers
    ]

    required_concepts = [e.term for e in nq.entities]
    required_concepts.extend(m.get("term", "") for m in measures)

    return {
        "measures": measures,
        "filters": filters,
        "group_by": group_by,
        "order_limit": order_limit,
        "required_concepts": required_concepts,
    }


def _initialize_knowledge_items(
    nq: Any,
    decomposition: dict,
    original_query: str = "",
) -> list[KnowledgeItem]:
    """정규화 결과에서 UNRESOLVED 지식 항목을 초기화한다.

    output 모호 감지:
      measures가 비어있고, 질의가 추출형("뽑아", "추출", "명세", "현황", "목록")이면
      사용자가 어떤 컬럼/형태를 원하는지 불분명하므로
      output_scope CONFLICTED 항목을 등록하여 명확화 경로를 유도한다.
    """
    items: list[KnowledgeItem] = []

    for m in decomposition.get("measures", []):
        term = m.get("term", "")
        if term:
            items.append(KnowledgeItem(
                key=f"measure:{term}",
                status=ConfidenceStatus.UNRESOLVED,
            ))

    for f in decomposition.get("filters", []):
        term = f.get("term", "")
        value = f.get("value", "")
        if term and value:
            items.append(KnowledgeItem(
                key=f"filter:{term}={value}",
                status=ConfidenceStatus.UNRESOLVED,
            ))

    if nq and hasattr(nq, "ambiguities"):
        for amb in (nq.ambiguities or []):
            items.append(KnowledgeItem(
                key=f"ambiguity:{amb}",
                value=amb,
                status=ConfidenceStatus.CONFLICTED,
                confidence=0.0,
                is_critical=True,
                source="normalizer_ambiguity",
                evidence=[f"사용자 확인 필요: {amb}"],
            ))

    # output 모호 감지
    output_item = _detect_ambiguous_output(
        decomposition, original_query,
    )
    if output_item:
        items.append(output_item)

    return items


# ── output 모호 감지 ──────────────────────────────
#
# "고객 명세 추출해줘"처럼 output 범위가 불분명한 질의를 감지한다.
#
# 판단 기준 (OR 조건):
#   1) measures가 완전히 비어있고 추출 키워드가 있는 경우
#   2) measures가 있더라도 agg_function이 NONE/UNKNOWN이고
#      "명세", "현황", "정보" 같은 포괄적 키워드를 사용한 경우
#      → 정규화 모델이 "고객정보(NONE)"처럼 채웠을 수 있으나
#        실제로 어떤 컬럼을 SELECT할지는 여전히 불분명

# 포괄적 output 키워드 — 그 자체로는 구체적 컬럼을 특정할 수 없는 단어
# SSOT: 이 목록은 planner_system.txt 프롬프트에도 주입된다.
VAGUE_OUTPUT_KEYWORDS: list[str] = [
    "명세", "현황", "정보", "내역", "데이터",
    "목록", "리스트",
]

# 추출 동작 키워드
# SSOT: 이 목록은 planner_system.txt 프롬프트에도 주입된다.
EXTRACTION_KEYWORDS: list[str] = [
    "뽑아", "추출", "조회",
]


def _detect_ambiguous_output(
    decomposition: dict,
    original_query: str,
) -> KnowledgeItem | None:
    """output 범위가 모호한 질의를 감지한다.

    Case 1: measures 비어있음 + 추출/포괄 키워드
    Case 2: measures 있으나 전부 agg=NONE/UNKNOWN + 포괄 키워드
      → 정규화 모델이 "고객정보(NONE)"처럼 채웠지만
        실제 SELECT 컬럼은 여전히 불분명
    """
    if not original_query:
        return None

    has_vague = any(
        kw in original_query for kw in VAGUE_OUTPUT_KEYWORDS
    )
    has_extract = any(
        kw in original_query for kw in EXTRACTION_KEYWORDS
    )
    has_trigger = has_vague or has_extract

    if not has_trigger:
        return None

    measures = decomposition.get("measures", [])

    # Case 1: measures 완전히 비어있음
    if not measures:
        return _build_output_scope_item()

    # Case 2: measures 있지만 전부 NONE/UNKNOWN
    # → 정규화 모델이 무언가 채웠지만 구체적 집계가 아님
    concrete_aggs = {"SUM", "AVG", "COUNT", "COUNT_DISTINCT", "MAX", "MIN"}
    all_vague = all(
        m.get("agg_function", "").upper() not in concrete_aggs
        for m in measures
    )
    if all_vague and has_vague:
        return _build_output_scope_item()

    return None


def _build_output_scope_item() -> KnowledgeItem:
    """output_scope CONFLICTED KnowledgeItem을 생성한다."""
    return KnowledgeItem(
        key="output_scope",
        value="",
        status=ConfidenceStatus.CONFLICTED,
        confidence=0.0,
        source="planner",
        evidence=[
            "질의에 포괄적 output 키워드가 있으나 "
            "구체적인 측정값/컬럼 지정 없음",
        ],
        is_critical=True,
    )


def _extract_meta_search_query(nq: Any, fallback_query: str) -> str:
    """NormalizedQuery에서 meta_search 키워드를 추출한다.

    정규화 결과가 있으면 search_keywords.meta_search를 space join하여 반환.
    없으면 원본 질의를 그대로 반환 (폴백).
    """
    if nq is None:
        return fallback_query

    # NormalizedQuery가 dict인 경우
    if isinstance(nq, dict):
        sk = nq.get("search_keywords", {})
        meta_kws = sk.get("meta_search", [])
    else:
        # Pydantic 모델인 경우
        sk = getattr(nq, "search_keywords", None)
        meta_kws = (
            getattr(sk, "meta_search", [])
            if sk else []
        )

    if meta_kws:
        return " ".join(meta_kws)
    return fallback_query


async def _collect_initial_context(query: str, nq: Any) -> dict:
    """초기 컨텍스트 1차 수집 — MongoDB 단독.

    NormalizedQuery의 meta_search 키워드를 사용하여
    노이즈 없는 도메인 키워드로 테이블 메타를 검색한다.
    유사 SQL 검색(Qdrant)은 원본 질의를 사용한다.
    """
    use_cases: list[dict] = []
    table_metas: list[dict] = []

    if query:
        # Qdrant 벡터 검색(원본 자연어)과
        # MongoDB $text 검색(도메인 키워드)은 독립적이므로 병렬 실행
        meta_query = _extract_meta_search_query(nq, query)
        use_cases, table_metas = await asyncio.gather(
            search_use_cases(query),
            search_table_meta(meta_query),
        )

    return {
        "use_cases": use_cases,
        "table_metas": table_metas,
        "manuals": [],
    }


def _build_initial_candidates(
    table_metas: list[dict], nq: Any,
) -> list[CandidateTable]:
    """초기 후보 테이블을 구성한다 (MongoDB 메타 기준).

    신규 필드(name, description, columns[].name)를 우선 참조하고
    하위 호환을 위해 table_name / table_description / columns[].column_name도
    폴백으로 지원한다.
    """
    candidates: list[CandidateTable] = []
    for meta in table_metas:
        ct = CandidateTable.from_meta(meta)
        if ct is not None:
            candidates.append(ct)
    return candidates


def _should_fast_path(
    knowledge_items: list[KnowledgeItem],
    hints: StructuralHints,
    candidates: list[CandidateTable],
    nq: Any,
) -> bool:
    """Fast-Path 바이패스 조건 판정."""
    return (
        not hints.is_empty()
        and len(candidates) >= 1
        and all(ki.status != ConfidenceStatus.UNRESOLVED for ki in knowledge_items)
        and not (nq and hasattr(nq, "ambiguities") and nq.ambiguities)
    )




async def _generate_hypotheses(
    query: str,
    decomposition: dict,
    initial_context: dict,
    knowledge_items: list[KnowledgeItem],
    structural_hints: StructuralHints,
    *,
    searched_queries: list[str] | None = None,
    candidate_tables: list | None = None,
    conversation_history: str = "",
) -> list[Hypothesis]:
    """LLM을 사용하여 탐색 가설을 수립한다."""
    from src.config import settings

    # 초기 수집 결과 요약
    use_cases = initial_context.get("use_cases", [])
    table_metas = initial_context.get("table_metas", [])
    context_summary = ""
    if use_cases:
        context_summary += f"유사 활용사례 {len(use_cases)}건 발견\n"
        for uc in use_cases:
            desc = uc.get("description", "")
            score = uc.get("_score", 0)
            context_summary += f"  - (유사도 {score:.2f}) {desc}\n"
    # structural_hints 요약 (sqlglot이 활용사례 SQL에서 추출한 검증된 정보)
    if not structural_hints.is_empty():
        context_summary += structural_hints.to_prompt_text() + "\n"
    if table_metas:
        names = [
            m.get("table_name") or m.get("name", "")
            for m in table_metas
        ]
        context_summary += f"관련 테이블 {len(table_metas)}건: {', '.join(n for n in names if n)}\n"
    if not context_summary:
        context_summary = "(초기 수집 결과 없음 — Cold Start)"

    plan_vars = {
        "original_query": query,
        "conversation_history": conversation_history or "(없음)",
        "query_decomposition": json.dumps(decomposition, ensure_ascii=False),
        "initial_context_summary": context_summary,
        "searched_queries": ", ".join(searched_queries or []) or "(없음)",
        "sampled_tables": ", ".join(
            t.table_name for t in (candidate_tables or []) if t.sample_rows
        ) or "(없음)",
        "vague_output_keywords": ", ".join(
            f'"{kw}"' for kw in VAGUE_OUTPUT_KEYWORDS
        ),
        "extraction_keywords": ", ".join(
            f'"{kw}"' for kw in EXTRACTION_KEYWORDS
        ),
    }
    render_vars = {f"{{{k}}}": v for k, v in plan_vars.items()}
    prompt, variables = render_prompt(PLANNER_SYSTEM, render_vars)

    def _parse_fn(raw_text: str) -> list[Hypothesis]:
        parsed = _parse_plan_response(raw_text)
        if not parsed:
            raise ValueError("가설 파싱 결과 0건")
        return parsed

    try:
        _, parsed = await llm_call_with_parse_retry(
            system=prompt,
            messages=[
                {"role": "user", "content": query},
            ],
            parse_fn=_parse_fn,
            max_tokens=2048,
            timeout=settings.llm_long_timeout,
            node_name="planner",
        )
        await record_prompt_variables(variables)
        return parsed
    except (ParseError, Exception) as e:
        logger.warning("planner LLM 호출 실패, rule-based fallback", error=str(e))
        return _generate_hypotheses_fallback(initial_context)


def _parse_plan_response(raw: str) -> list[Hypothesis]:
    """LLM 응답 JSON에서 hypotheses를 파싱한다."""
    data = extract_json(raw)
    if not data:
        return []

    priority_map = {"high": 0.9, "medium": 0.5, "low": 0.1}
    hypotheses: list[Hypothesis] = []
    for h in data.get("hypotheses", []):
        hypotheses.append(Hypothesis(
            hypothesis_id=h.get("hypothesis_id", f"H{len(hypotheses)+1}"),
            description=h.get("description", ""),
            based_on_use_case=h.get("based_on_use_case"),
            missing_terms=h.get("missing_terms", []),
            strategy=h.get("strategy", ""),
            priority=priority_map.get(
                h.get("priority", "medium"), 0.5,
            ),
        ))

    return sorted(hypotheses, key=lambda h: h.priority, reverse=True)


def _generate_hypotheses_fallback(
    initial_context: dict,
) -> list[Hypothesis]:
    """LLM 호출 실패 시 rule-based fallback 가설."""
    hypotheses: list[Hypothesis] = []

    if initial_context.get("use_cases"):
        hypotheses.append(Hypothesis(
            hypothesis_id="H1",
            description="유사 SQL 활용사례 기반 접근",
            based_on_use_case="initial",
            strategy="유사 SQL의 테이블/조인 구조를 참고하여 SQL 생성",
            priority=0.9,
        ))

    hypotheses.append(Hypothesis(
        hypothesis_id="H2",
        description="테이블 메타 직접 탐색",
        strategy="질의 키워드로 테이블/컬럼 메타를 직접 검색",
        priority=0.5,
    ))

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
    structural_hints: StructuralHints,
    candidate_tables: list[CandidateTable],
    original_query: str = "",
) -> list[ExecutionStep]:
    """가설에 대한 실행계획을 생성한다.

    테이블 메타 조회 스텝은 structural_hints.source_tables(sqlglot 추출)과
    candidate_tables(MongoDB 검색)에서 검증된 테이블만 사용한다.
    search_use_cases의 input은 원본 질의를 사용한다 (벡터 검색 최적).
    """
    steps: list[ExecutionStep] = []
    step_num = 1

    if hypothesis.based_on_use_case:
        steps.append(ExecutionStep(
            step=step_num,
            tool="search_use_cases",
            input=original_query or hypothesis.description,
            purpose="유사 활용사례에서 테이블/조인 구조 참고",
            expected_output="유사 SQL + 테이블 정보",
        ))
        step_num += 1

    # 검증된 테이블로 메타 조회 스텝 생성
    verified_tables: list[str] = []
    for t in structural_hints.source_tables:
        if t not in verified_tables:
            verified_tables.append(t)
    for ct in candidate_tables:
        if ct.table_name not in verified_tables:
            verified_tables.append(ct.table_name)

    for table in verified_tables:
        if table not in searched_queries:
            steps.append(ExecutionStep(
                step=step_num,
                tool="search_table_meta",
                input=table,
                purpose=f"{table} 테이블의 컬럼 구조 확인",
                expected_output="컬럼 목록 + 설명",
            ))
            step_num += 1

    for ki in knowledge_items:
        if ki.status == ConfidenceStatus.UNRESOLVED and "filter:" in ki.key:
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


def _build_fallback_plan(query: str, decomposition: dict) -> list[ExecutionStep]:
    """Cold Start fallback 실행계획."""
    keywords = decomposition.get("required_concepts", [])
    if not keywords:
        keywords = query.split()

    return [
        ExecutionStep(
            step=1,
            tool="search_table_meta",
            input=" ".join(keywords),
            purpose="질의 키워드로 관련 테이블 직접 검색",
            expected_output="관련 테이블 목록",
        ),
    ]
