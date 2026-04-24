"""reasoning_preparer 노드 — 결정론적 reasoning 초기화 + 실행계획 생성.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

reason 계층의 첫 번째 노드. 최초 진입 전용이며 재계획은 recovery_agent가 담당한다.
8-Slot 정규화 결과(normalized_query)를 시드로 활용하여 query_decomposition을 구성하고,
UNRESOLVED 용어를 KnowledgeItem으로 목록화한 뒤 결정론적 실행계획을 수립한다.

LLM 호출 없음 — 모든 로직이 rule-based이다.
도구 호출 없음 — 실행은 context_retriever에 위임한다.

핵심 함수:
    - reasoning_preparer_node: 메인 노드 함수
    - _build_decomposition_from_normalized: NormalizedQuery → query_decomposition 변환
    - _initialize_knowledge_items: 정규화 슬롯에서 UNRESOLVED 용어 추출
    - _build_initial_hypothesis: rule-based 초기 가설 1개 생성
    - _build_execution_plan: 결정론적 실행계획 생성 (도구 의존 없음)

v3.0 (2026-04-02): planner에서 리네이밍 + LLM/도구/Fast-Path 제거.
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from src.agents.models.normalization import NormalizedQuery

from src.agents.state.state import (
    PipelineState,
    ConfidenceStatus,
    ExecutionStep,
    Hypothesis,
    HypothesisStatus,
    KnowledgeItem,
    Phase,
)
from src.utils.logger import get_logger
from src.utils.tracker.dispatch import (
    dispatch_tracking_event,
    REASONING_STEP,
)
from src.utils.truncate import truncate_log

logger = get_logger(__name__)


async def reasoning_preparer_node(state: PipelineState) -> dict:
    """reasoning 상태를 초기화하고 결정론적 탐색 계획을 수립한다.

    외부 도구 호출 없음. 8-slot 정규화 결과를 기반으로
    knowledge_items, 초기 가설, execution_plan을 생성한다.
    """
    reason = state.reason.model_copy(deep=True)

    # recovery 상태 초기화 (최초 진입 시 클린 상태 보장)
    reason.exploration_phase = "initial"
    reason.recovery_rounds = 0
    reason.recovery_entry_source = None
    reason.is_force_generated = False

    nq = state.normalized_query
    query = state.preprocessed_input or ""

    # 1. 8-slot에서 decomposition + knowledge_items 초기화
    decomposition = _build_decomposition_from_normalized(nq)
    reason.query_decomposition = decomposition

    knowledge_items = _initialize_knowledge_items(
        decomposition, query,
    )
    for i, ki in enumerate(knowledge_items):
        ki.id = f"K{i + 1}"
    reason.knowledge_items = knowledge_items

    # 2. rule-based 초기 가설 생성
    hypothesis = _build_initial_hypothesis()
    reason.hypotheses = [hypothesis]
    reason.current_hypothesis = hypothesis

    # 4. deterministic execution_plan 생성 (도구 호출 없음)
    # search_use_cases input: 원본 + 정규화 결합으로 벡터 검색 커버리지 극대화
    rewritten = (
        getattr(nq, "rewritten_query", "")
        if nq and hasattr(nq, "rewritten_query")
        else ""
    )
    if rewritten and rewritten != query:
        use_case_query = f"{query}\n{rewritten}"
    else:
        use_case_query = query

    reason.execution_plan = _build_execution_plan(
        knowledge_items, reason.executed_tool_keys, nq,
        original_query=use_case_query,
    )
    # 저니 뷰: 스텝에 소속 가설 태깅
    for step in reason.execution_plan:
        step.hypothesis_id = hypothesis.hypothesis_id

    reason.phase = Phase.EXPLORING

    # ── 추적: reasoning_preparer 결과 ──
    logger.info(
        "reasoning_preparer 완료",
        execution_steps=len(reason.execution_plan),
        knowledge_items=len(reason.knowledge_items),
    )

    # ── Reasoning Flow 트레이스 ──
    await dispatch_tracking_event(REASONING_STEP, {
        "node": "reasoning_preparer",
        "phase": "reason",
        "step_type": "rule_decision",
        "round": 0,
        "hypothesis_id": hypothesis.hypothesis_id,
        "inputs": {
            "normalized_query": "(8-Slot 참조)",
        },
        "output": {
            "query_decomposition": {
                "measures": [
                    m.get("term", "") for m in decomposition.get("measures", [])
                ],
                "filters": [
                    f.get("term", "") for f in decomposition.get("filters", [])
                ],
                "group_by": decomposition.get("group_by", []),
                "order_limit": decomposition.get("order_limit", []),
            },
            "knowledge_items": [
                f"{ki.id}: {ki.key} ({ki.status.value})"
                for ki in knowledge_items
            ],
            "hypothesis": f"{hypothesis.hypothesis_id}: {hypothesis.description}",
            "execution_plan": [
                f"Step {s.step}: {s.tool}(\"{s.input}\")"
                for s in reason.execution_plan
            ],
        },
        "routing": {
            "next_node": "context_retriever",
            "reason": "초기 탐색 계획 수립 완료 → 도구 실행",
        },
    })

    return {"reason": reason}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 내부 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _build_decomposition_from_normalized(
    nq: NormalizedQuery | None,
) -> dict:
    """8-Slot NormalizedQuery에서 query_decomposition을 구성한다."""
    empty: dict[str, list[Any]] = {"measures": [], "filters": [], "group_by": [], "order_limit": []}
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

    output_hint = {}
    if nq.output_hint:
        oh = nq.output_hint
        output_hint = {
            "format": oh.format,
            "doc_type": oh.doc_type,
            "expected_columns": oh.expected_columns or [],
        }

    return {
        "measures": measures,
        "filters": filters,
        "group_by": group_by,
        "order_limit": order_limit,
        "required_concepts": required_concepts,
        "output_hint": output_hint,
    }


def _initialize_knowledge_items(
    decomposition: dict,
    original_query: str = "",
) -> list[KnowledgeItem]:
    """정규화 결과에서 UNRESOLVED 지식 항목을 초기화한다.

    INFER 모호성은 resolved_signals로 관리되므로 여기서 등록하지 않는다.

    output 모호 감지:
      measures가 비어있고, 질의가 추출형("뽑아", "추출", "명세", "현황", "목록")이면
      사용자가 어떤 컬럼/형태를 원하는지 불분명하므로
      output_scope CONFLICTED 항목을 등록하여 명확화 경로를 유도한다.
    """
    items: list[KnowledgeItem] = []

    for m in (decomposition or {}).get("measures", []):
        term = m.get("term", "")
        if term:
            items.append(KnowledgeItem(
                key=f"measure:{term}",
                status=ConfidenceStatus.UNRESOLVED,
            ))

    for f in (decomposition or {}).get("filters", []):
        term = f.get("term", "")
        value = f.get("value", "")
        if term and value:
            items.append(KnowledgeItem(
                key=f"filter:{term}={value}",
                status=ConfidenceStatus.UNRESOLVED,
            ))

    # INFER 모호성은 knowledge_items에 등록하지 않는다.
    # normalizer가 INFER로 결정한 모호성은 resolved_signals에 이미 기록되어 있고,
    # sql_generator 등이 build_clarification_context()를 통해 참조한다.
    # measure:UNRESOLVED 항목이 탐색 루프에서 증거 기반으로 확인되는 것이
    # 모호성 해소의 정상 경로이다.

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
VAGUE_OUTPUT_KEYWORDS: list[str] = [
    "명세", "현황", "정보", "내역", "데이터",
    "목록", "리스트",
]

# 추출 동작 키워드
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

    measures = (decomposition or {}).get("measures", [])

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
        source="reasoning_preparer",
        evidence=[
            "질의에 포괄적 output 키워드가 있으나 "
            "구체적인 측정값/컬럼 지정 없음",
        ],
        is_critical=True,
    )


def _build_initial_hypothesis() -> Hypothesis:
    """rule-based 초기 가설 — recovery_agent의 dead_end 참조용."""
    return Hypothesis(
        hypothesis_id="H1",
        description="유사 SQL + 테이블 메타 기반 초기 탐색",
        strategy=(
            "사용자 질의를 키워드로 하여 조회한 유사SQL과, "
            "유사 SQL에서 추출한 테이블 및 8-slot 키워드로 조회한 "
            "테이블의 메타를 수집하여 SQL 생성 가능성 판단"
        ),
        status=HypothesisStatus.ACTIVE,
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


def _build_execution_plan(
    knowledge_items: list[KnowledgeItem],
    executed_tool_keys: set[str],
    nq: Any,
    original_query: str = "",
) -> list[ExecutionStep]:
    """결정론적 실행계획 생성 — 도구 의존 없음.

    1. search_use_cases(원본 질의) — 유사 SQL 조회
       → context_retriever에서 실행 시 내장 후속 수집 (테이블 메타 + 코드 메타)
    2. search_table_meta(8-slot 키워드) — 키워드 기반 테이블 검색
    3. search_code_meta(filter 컬럼) — UNRESOLVED 필터의 코드값 확인
    """
    steps: list[ExecutionStep] = []
    step_num = 1

    # (1) 유사 SQL 조회 — context_retriever에서 실행 시 내장 후속 수집 자동 수행
    if original_query:
        steps.append(ExecutionStep(
            step=step_num,
            tool="search_use_cases",
            input=f"{original_query}, page=1",
            purpose="유사 SQL 조회 → 관련 테이블 메타 + 코드 자동 수집",
        ))
        step_num += 1

    # (2) 8-slot 키워드로 테이블 메타 검색
    meta_query = _extract_meta_search_query(nq, original_query)
    if meta_query and f"search_table_meta:{meta_query}, page=1" not in executed_tool_keys:
        steps.append(ExecutionStep(
            step=step_num,
            tool="search_table_meta",
            input=f"{meta_query}, page=1",
            purpose="8-slot 키워드 기반 테이블 메타 검색",
        ))
        step_num += 1

    # (3) UNRESOLVED filter 컬럼의 코드 메타 조회 (2026-04-07: 한글명으로 코드조회 불가 -> retriever의 내장 후속 수집으로 대체)
    # for ki in knowledge_items:
    #     if (
    #         ki.status == ConfidenceStatus.UNRESOLVED
    #         and "filter:" in ki.key
    #     ):
    #         col_name = ki.key.split(":")[1].split("=")[0]
    #         steps.append(ExecutionStep(
    #             step=step_num,
    #             tool="lookup_code_meta",
    #             input=f"{col_name}, page=1",
    #             purpose=f"{col_name}의 코드값 확인",
    #         ))
    #         step_num += 1

    return steps
