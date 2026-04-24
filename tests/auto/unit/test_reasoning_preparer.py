"""reasoning_preparer 노드 내부 함수 단위 테스트.

테스트 대상 (LLM 없이 rule-based):
  - _build_decomposition_from_normalized: NormalizedQuery → query_decomposition 변환
  - _initialize_knowledge_items: UNRESOLVED KnowledgeItem 초기화
  - _detect_ambiguous_output: output 범위 모호 감지
  - _build_initial_hypothesis: rule-based 초기 가설 생성
  - _extract_meta_search_query: NormalizedQuery에서 meta 검색 키워드 추출
  - _build_execution_plan: 결정론적 실행계획 생성

실제 환경에서 실행 — Mock 없음.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import pytest

from src.agents.models.normalization import (
    DimensionSlot,
    EntitySlot,
    FilterSlot,
    MeasureSlot,
    ModifierSlot,
    NormalizedQuery,
    OutputHintSlot,
    SearchKeywords,
)
from src.agents.nodes.reason.reasoning_preparer import (
    _build_decomposition_from_normalized,
    _build_execution_plan,
    _build_initial_hypothesis,
    _detect_ambiguous_output,
    _extract_meta_search_query,
    _initialize_knowledge_items,
)
from src.agents.state.state import (
    ConfidenceStatus,
    HypothesisStatus,
    KnowledgeItem,
    StepStatus,
)
from tests.conftest import get_test_logger, log_test_case

logger = get_test_logger("test_reasoning_preparer")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 헬퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _nq(
    measures: list[dict] | None = None,
    filters: list[dict] | None = None,
    dimensions: list[dict] | None = None,
    entities: list[dict] | None = None,
    modifiers: list[dict] | None = None,
    output_hint: dict | None = None,
    meta_search: list[str] | None = None,
    rewritten_query: str = "",
) -> NormalizedQuery:
    """테스트용 NormalizedQuery 생성 헬퍼."""
    measure_slots = [
        MeasureSlot(term=m["term"], agg_function=m.get("agg_function", "COUNT"))
        for m in (measures or [])
    ]
    filter_slots = [
        FilterSlot(
            target=f["target"],
            filter_type=f.get("filter_type", "EQUALS"),
            values=f.get("values"),
        )
        for f in (filters or [])
    ]
    dim_slots = [
        DimensionSlot(term=d["term"], role=d.get("role", "GROUP"))
        for d in (dimensions or [])
    ]
    entity_slots = [
        EntitySlot(term=e["term"])
        for e in (entities or [])
    ]
    mod_slots = [
        ModifierSlot(
            type=mod["type"],
            limit=mod.get("limit"),
            by=mod.get("by"),
        )
        for mod in (modifiers or [])
    ]
    oh = OutputHintSlot(**(output_hint or {})) if output_hint else OutputHintSlot()
    sk = SearchKeywords(meta_search=meta_search or [])
    return NormalizedQuery(
        measures=measure_slots,
        filters=filter_slots,
        dimensions=dim_slots,
        entities=entity_slots,
        modifiers=mod_slots,
        output_hint=oh,
        search_keywords=sk,
        rewritten_query=rewritten_query,
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _build_decomposition_from_normalized
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestBuildDecompositionFromNormalized:
    """_build_decomposition_from_normalized 변환 테스트."""

    def test_none_returns_empty_structure(self):
        """NormalizedQuery가 None이면 빈 구조 반환."""
        result = _build_decomposition_from_normalized(None)
        passed = (
            result["measures"] == []
            and result["filters"] == []
            and result["group_by"] == []
            and result["order_limit"] == []
        )
        log_test_case(logger, "none_nq_empty", None, "empty", result, passed)
        assert passed

    def test_measures_extracted_correctly(self):
        """measures 슬롯이 term·agg_function 포함해서 추출된다."""
        nq = _nq(measures=[{"term": "여신잔액", "agg_function": "SUM"}])
        result = _build_decomposition_from_normalized(nq)
        passed = (
            len(result["measures"]) == 1
            and result["measures"][0]["term"] == "여신잔액"
            and result["measures"][0]["agg_function"] == "SUM"
        )
        log_test_case(logger, "measures_extracted", nq, "term=여신잔액,agg=SUM", result["measures"], passed)
        assert passed

    def test_filters_extracted_with_term_operator_value(self):
        """filters 슬롯이 term·operator·value를 포함해서 추출된다."""
        nq = _nq(filters=[{"target": "지점코드", "filter_type": "EQUALS", "values": ["001"]}])
        result = _build_decomposition_from_normalized(nq)
        passed = (
            len(result["filters"]) == 1
            and result["filters"][0]["term"] == "지점코드"
            and result["filters"][0]["operator"] == "EQUALS"
            and result["filters"][0]["value"] == ["001"]
        )
        log_test_case(logger, "filters_extracted", nq, "filter dict", result["filters"], passed)
        assert passed

    def test_group_by_only_includes_group_role(self):
        """GROUP 역할 dimension만 group_by에 포함된다."""
        nq = _nq(dimensions=[
            {"term": "지점", "role": "GROUP"},
            {"term": "고객등급", "role": "DISPLAY"},
        ])
        result = _build_decomposition_from_normalized(nq)
        passed = result["group_by"] == ["지점"]
        log_test_case(logger, "group_by_role_filter", nq, ["지점"], result["group_by"], passed)
        assert passed

    def test_order_limit_from_modifiers(self):
        """modifiers가 order_limit로 변환된다."""
        nq = _nq(modifiers=[{"type": "LIMIT", "limit": 100}])
        result = _build_decomposition_from_normalized(nq)
        passed = (
            len(result["order_limit"]) == 1
            and result["order_limit"][0]["type"] == "LIMIT"
            and result["order_limit"][0]["value"] == "100"
        )
        log_test_case(logger, "order_limit_modifiers", nq, "LIMIT:100", result["order_limit"], passed)
        assert passed

    def test_required_concepts_includes_entities_and_measures(self):
        """required_concepts에 엔티티와 측정값 모두 포함된다."""
        nq = _nq(
            entities=[{"term": "여신"}],
            measures=[{"term": "잔액", "agg_function": "SUM"}],
        )
        result = _build_decomposition_from_normalized(nq)
        rc = result.get("required_concepts", [])
        passed = "여신" in rc and "잔액" in rc
        log_test_case(logger, "required_concepts", nq, ["여신", "잔액"], rc, passed)
        assert passed

    def test_output_hint_extracted_when_present(self):
        """output_hint 슬롯이 올바르게 추출된다."""
        nq = _nq(output_hint={"format": "SUMMARY", "doc_type": "report", "expected_columns": ["잔액"]})
        result = _build_decomposition_from_normalized(nq)
        oh = result.get("output_hint", {})
        passed = oh.get("format") == "SUMMARY" and oh.get("doc_type") == "report"
        log_test_case(logger, "output_hint_extracted", nq, "SUMMARY", oh, passed)
        assert passed

    def test_empty_nq_returns_empty_measures_and_filters(self):
        """슬롯이 비어있는 NormalizedQuery → measures·filters 빈 리스트."""
        nq = NormalizedQuery()
        result = _build_decomposition_from_normalized(nq)
        passed = result["measures"] == [] and result["filters"] == []
        log_test_case(logger, "empty_nq_empty_slots", nq, "empty slots", result, passed)
        assert passed


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _initialize_knowledge_items
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestInitializeKnowledgeItems:
    """_initialize_knowledge_items 초기화 테스트."""

    def test_empty_decomposition_returns_no_items(self):
        """빈 decomposition + 트리거 키워드 없는 질의 → items 없음."""
        # "이번 달 건수 알려줘"는 추출/포괄 키워드 없으므로 output_scope 미생성
        result = _initialize_knowledge_items({}, "이번 달 건수 알려줘")
        passed = len(result) == 0
        log_test_case(logger, "empty_decomp_no_items", {}, 0, len(result), passed)
        assert passed

    def test_measures_generate_knowledge_items(self):
        """measures 용어마다 KnowledgeItem이 생성된다."""
        decomp = {"measures": [{"term": "여신잔액"}, {"term": "건수"}], "filters": []}
        result = _initialize_knowledge_items(decomp)
        keys = [ki.key for ki in result]
        passed = "measure:여신잔액" in keys and "measure:건수" in keys
        log_test_case(logger, "measures_to_ki", decomp, "measure keys", keys, passed)
        assert passed

    def test_all_initial_items_are_unresolved(self):
        """생성된 모든 KnowledgeItem은 UNRESOLVED 상태로 초기화된다."""
        decomp = {
            "measures": [{"term": "잔액"}],
            "filters": [{"term": "지점코드", "value": ["001"]}],
        }
        result = _initialize_knowledge_items(decomp)
        measure_items = [ki for ki in result if "measure:" in ki.key]
        passed = all(ki.status == ConfidenceStatus.UNRESOLVED for ki in measure_items)
        log_test_case(logger, "initial_unresolved", decomp, "all UNRESOLVED", result, passed)
        assert passed

    def test_filter_with_value_generates_knowledge_item(self):
        """filter 항목에 value가 있으면 KnowledgeItem 생성."""
        decomp = {
            "measures": [],
            "filters": [{"term": "상품코드", "value": ["A01"]}],
        }
        result = _initialize_knowledge_items(decomp)
        keys = [ki.key for ki in result]
        passed = any("filter:상품코드" in k for k in keys)
        log_test_case(logger, "filter_with_value", decomp, "filter key", keys, passed)
        assert passed

    def test_filter_without_value_skipped(self):
        """filter에 value가 없으면 KnowledgeItem 생성 안 함."""
        decomp = {
            "measures": [],
            "filters": [{"term": "날짜", "value": ""}],
        }
        result = _initialize_knowledge_items(decomp)
        filter_items = [ki for ki in result if "filter:" in ki.key]
        passed = len(filter_items) == 0
        log_test_case(logger, "filter_no_value_skipped", decomp, 0, len(filter_items), passed)
        assert passed

    def test_empty_measure_term_skipped(self):
        """term이 빈 문자열인 measure는 KnowledgeItem 생성 안 함."""
        decomp = {"measures": [{"term": ""}], "filters": []}
        result = _initialize_knowledge_items(decomp)
        measure_items = [ki for ki in result if "measure:" in ki.key]
        passed = len(measure_items) == 0
        log_test_case(logger, "empty_measure_term", decomp, 0, len(measure_items), passed)
        assert passed


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _detect_ambiguous_output
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestDetectAmbiguousOutput:
    """_detect_ambiguous_output output 모호 감지 테스트."""

    def test_empty_query_returns_none(self):
        """원본 질의 없으면 None."""
        result = _detect_ambiguous_output({}, "")
        passed = result is None
        log_test_case(logger, "empty_query_none", {}, None, result, passed)
        assert passed

    def test_no_trigger_keywords_returns_none(self):
        """추출/포괄 키워드 없으면 None."""
        result = _detect_ambiguous_output({}, "이번 달 여신 건수 알려줘")
        passed = result is None
        log_test_case(logger, "no_trigger_none", {}, None, result, passed)
        assert passed

    @pytest.mark.parametrize("query", [
        "고객 명세 뽑아줘",
        "여신 현황 추출해줘",
        "거래 내역 조회",
        "데이터 목록 조회",
    ])
    def test_extraction_keyword_with_no_measures_triggers(self, query):
        """추출/포괄 키워드 + measures 없음 → output_scope CONFLICTED 반환."""
        decomp = {"measures": []}
        result = _detect_ambiguous_output(decomp, query)
        passed = (
            result is not None
            and result.key == "output_scope"
            and result.status == ConfidenceStatus.CONFLICTED
        )
        log_test_case(logger, f"ambig_output_{query[:10]}", query, "output_scope", result, passed)
        assert passed, f"query '{query}' should trigger ambiguous output"

    def test_concrete_agg_measure_with_vague_keyword_does_not_trigger(self):
        """구체적 집계(SUM)가 있으면 포괄 키워드 있어도 None."""
        decomp = {"measures": [{"term": "잔액", "agg_function": "SUM"}]}
        result = _detect_ambiguous_output(decomp, "여신 잔액 현황 뽑아줘")
        passed = result is None
        log_test_case(logger, "concrete_agg_no_ambig", decomp, None, result, passed)
        assert passed

    def test_all_vague_agg_with_vague_keyword_triggers(self):
        """measures가 있지만 모두 NONE/UNKNOWN + 포괄 키워드 → 감지."""
        decomp = {
            "measures": [{"term": "고객정보", "agg_function": "NONE"}]
        }
        result = _detect_ambiguous_output(decomp, "고객 정보 현황 조회")
        passed = result is not None and result.key == "output_scope"
        log_test_case(logger, "vague_agg_ambig", decomp, "output_scope", result, passed)
        assert passed

    def test_output_scope_item_is_critical(self):
        """감지된 output_scope 항목은 is_critical=True."""
        decomp = {"measures": []}
        result = _detect_ambiguous_output(decomp, "거래 내역 뽑아줘")
        passed = result is not None and result.is_critical is True
        log_test_case(logger, "output_scope_critical", {}, "is_critical=True", result, passed)
        assert passed

    def test_mixed_vague_and_concrete_agg_does_not_trigger(self):
        """NONE과 SUM이 혼재 → SUM이 있으므로 not all_vague → None."""
        decomp = {
            "measures": [
                {"term": "건수", "agg_function": "COUNT"},
                {"term": "기타", "agg_function": "NONE"},
            ]
        }
        result = _detect_ambiguous_output(decomp, "여신 현황 조회")
        passed = result is None
        log_test_case(logger, "mixed_agg_no_ambig", decomp, None, result, passed)
        assert passed


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _build_initial_hypothesis
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestBuildInitialHypothesis:
    """_build_initial_hypothesis 초기 가설 생성 테스트."""

    def test_returns_hypothesis_with_active_status(self):
        """초기 가설 상태는 ACTIVE."""
        hyp = _build_initial_hypothesis()
        passed = hyp.status == HypothesisStatus.ACTIVE
        log_test_case(logger, "hyp_active", {}, "ACTIVE", hyp.status, passed)
        assert passed

    def test_hypothesis_id_is_h1(self):
        """초기 가설 ID는 H1."""
        hyp = _build_initial_hypothesis()
        passed = hyp.hypothesis_id == "H1"
        log_test_case(logger, "hyp_id_h1", {}, "H1", hyp.hypothesis_id, passed)
        assert passed

    def test_hypothesis_has_strategy(self):
        """초기 가설에 strategy가 설정되어 있다."""
        hyp = _build_initial_hypothesis()
        passed = bool(hyp.strategy)
        log_test_case(logger, "hyp_has_strategy", {}, "non-empty", hyp.strategy, passed)
        assert passed

    def test_hypothesis_has_description(self):
        """초기 가설에 description이 설정되어 있다."""
        hyp = _build_initial_hypothesis()
        passed = bool(hyp.description)
        log_test_case(logger, "hyp_has_description", {}, "non-empty", hyp.description, passed)
        assert passed


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _extract_meta_search_query
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestExtractMetaSearchQuery:
    """_extract_meta_search_query 키워드 추출 테스트."""

    def test_none_nq_returns_fallback(self):
        """NormalizedQuery가 None이면 fallback_query 반환."""
        result = _extract_meta_search_query(None, "여신 잔액")
        passed = result == "여신 잔액"
        log_test_case(logger, "none_nq_fallback", None, "여신 잔액", result, passed)
        assert passed

    def test_meta_search_keywords_joined_with_space(self):
        """meta_search 키워드를 공백으로 join해서 반환."""
        nq = _nq(meta_search=["여신", "잔액", "지점"])
        result = _extract_meta_search_query(nq, "원본질의")
        passed = result == "여신 잔액 지점"
        log_test_case(logger, "meta_search_join", nq, "여신 잔액 지점", result, passed)
        assert passed

    def test_empty_meta_search_returns_fallback(self):
        """meta_search가 비어있으면 fallback 반환."""
        nq = _nq(meta_search=[])
        result = _extract_meta_search_query(nq, "폴백질의")
        passed = result == "폴백질의"
        log_test_case(logger, "empty_meta_fallback", nq, "폴백질의", result, passed)
        assert passed

    def test_dict_nq_with_meta_search(self):
        """dict 형태 NQ에서도 meta_search를 추출한다."""
        nq_dict = {"search_keywords": {"meta_search": ["대출", "현황"]}}
        result = _extract_meta_search_query(nq_dict, "폴백")
        passed = result == "대출 현황"
        log_test_case(logger, "dict_nq_meta_search", nq_dict, "대출 현황", result, passed)
        assert passed

    def test_dict_nq_without_meta_search_returns_fallback(self):
        """dict 형태 NQ에 meta_search 없으면 fallback 반환."""
        nq_dict = {"search_keywords": {}}
        result = _extract_meta_search_query(nq_dict, "폴백")
        passed = result == "폴백"
        log_test_case(logger, "dict_nq_no_meta", nq_dict, "폴백", result, passed)
        assert passed


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _build_execution_plan
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestBuildExecutionPlan:
    """_build_execution_plan 실행계획 생성 테스트."""

    def _filter_ki(self, col: str = "지점코드") -> KnowledgeItem:
        return KnowledgeItem(
            key=f"filter:{col}=001",
            status=ConfidenceStatus.UNRESOLVED,
        )

    def test_original_query_adds_search_use_cases_step(self):
        """original_query가 있으면 search_use_cases 스텝이 첫 번째로 생성된다."""
        steps = _build_execution_plan([], set(), None, original_query="여신 잔액 조회")
        tools = [s.tool for s in steps]
        passed = steps[0].tool == "search_use_cases" if steps else False
        log_test_case(logger, "use_cases_first", {}, "search_use_cases[0]", tools, passed)
        assert passed

    def test_no_original_query_no_use_cases_step(self):
        """original_query가 없으면 search_use_cases 스텝 없음."""
        steps = _build_execution_plan([], set(), None, original_query="")
        tools = [s.tool for s in steps]
        passed = "search_use_cases" not in tools
        log_test_case(logger, "no_use_cases_no_query", {}, "no use_cases", tools, passed)
        assert passed

    def test_search_table_meta_step_added_for_nq(self):
        """NormalizedQuery의 meta_search 키워드로 search_table_meta 스텝이 추가된다."""
        nq = _nq(meta_search=["여신"])
        steps = _build_execution_plan([], set(), nq, original_query="여신 잔액")
        tools = [s.tool for s in steps]
        passed = "search_table_meta" in tools
        log_test_case(logger, "table_meta_step", nq, "search_table_meta", tools, passed)
        assert passed


    def test_filter_ki_generates_lookup_code_meta_step(self):
        """filter KnowledgeItem에 대한 lookup_code_meta 스텝은
        현재 context_retriever 내장 후속 수집으로 대체되어 실행계획에 포함되지 않는다."""
        items = [self._filter_ki("지점코드"), self._filter_ki("상품코드")]
        steps = _build_execution_plan(items, set(), None, original_query="")
        code_steps = [s for s in steps if s.tool == "lookup_code_meta"]
        passed = len(code_steps) == 0
        log_test_case(logger, "code_meta_steps", items, 0, len(code_steps), passed)
        assert passed

    def test_already_executed_table_meta_not_duplicated(self):
        """이미 실행된 search_table_meta 키가 executed_tool_keys에 있으면 스텝 건너뜀."""
        nq = _nq(meta_search=["여신"])
        meta_query = "여신"
        executed = {f"search_table_meta:{meta_query}, page=1"}
        steps = _build_execution_plan([], executed, nq, original_query="여신 잔액")
        tools = [s.tool for s in steps]
        passed = "search_table_meta" not in tools
        log_test_case(logger, "no_dup_table_meta", executed, "no search_table_meta", tools, passed)
        assert passed

    def test_all_steps_start_as_pending(self):
        """생성된 모든 스텝은 PENDING 상태로 초기화된다."""
        nq = _nq(meta_search=["여신"])
        items = [self._filter_ki()]
        steps = _build_execution_plan(items, set(), nq, original_query="여신 잔액")
        passed = all(s.status == StepStatus.PENDING for s in steps)
        log_test_case(logger, "steps_all_pending", {}, "all PENDING", steps, passed)
        assert passed

    def test_step_numbers_are_sequential(self):
        """스텝 번호가 1부터 순차적으로 증가한다."""
        nq = _nq(meta_search=["여신"])
        items = [self._filter_ki()]
        steps = _build_execution_plan(items, set(), nq, original_query="여신")
        numbers = [s.step for s in steps]
        expected = list(range(1, len(steps) + 1))
        passed = numbers == expected
        log_test_case(logger, "step_numbers_sequential", {}, expected, numbers, passed)
        assert passed

    def test_measure_ki_does_not_add_code_meta_step(self):
        """measure: KnowledgeItem은 lookup_code_meta 스텝을 생성하지 않는다."""
        items = [
            KnowledgeItem(key="measure:여신잔액", status=ConfidenceStatus.UNRESOLVED),
        ]
        steps = _build_execution_plan(items, set(), None, original_query="")
        code_steps = [s for s in steps if s.tool == "lookup_code_meta"]
        passed = len(code_steps) == 0
        log_test_case(logger, "measure_no_code_meta", items, 0, len(code_steps), passed)
        assert passed
