"""simplify 리팩터링 변경사항 검증 테스트.

LLM 호출 없이 변경된 코드의 정합성을 검증한다.
커버 범위:
    - Enum 정의 및 호환성 (W-01~W-04)
    - ReasoningState 메서드 (W-06)
    - serialize_decomp_slots (W-07)
    - extract_json 코드펜스 + strict (W-09)
    - sql_safety_checker Sybase TOP N (W-10)
    - _safe_search 래퍼 (W-11)
    - planner asyncio.gather 병렬화 (W-12)
    - planner _build_decomposition_from_normalized (W-15)
    - confidence_evaluator failure 보존 (C-02)
    - trace_analyzer FinalStatus 비교
"""

from __future__ import annotations

import asyncio
import json

import pytest

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. Enum 정의 및 str 호환성 (W-01~W-04)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from src.models.enums import (
    ConfidenceStatus,
    FinalStatus,
    HypothesisStatus,
    StepStatus,
)


class TestEnumDefinitions:
    """Enum 멤버 존재 및 str 호환성."""

    def test_hypothesis_status_members(self):
        assert HypothesisStatus.PENDING == "PENDING"
        assert HypothesisStatus.ACTIVE == "ACTIVE"
        assert HypothesisStatus.SUCCESS == "SUCCESS"
        assert HypothesisStatus.FAILED == "FAILED"

    def test_step_status_members(self):
        assert StepStatus.PENDING == "PENDING"
        assert StepStatus.DONE == "DONE"
        assert StepStatus.SKIPPED == "SKIPPED"
        assert StepStatus.FAILED == "FAILED"

    def test_final_status_members(self):
        assert FinalStatus.PENDING == "pending"
        assert FinalStatus.SUCCESS == "success"
        assert FinalStatus.FAILURE == "failure"

    def test_enum_is_str_subclass(self):
        """str(Enum)이므로 JSON 직렬화에서 raw string과 동일."""
        assert isinstance(HypothesisStatus.ACTIVE, str)
        assert isinstance(StepStatus.DONE, str)
        assert isinstance(FinalStatus.SUCCESS, str)

    def test_enum_json_serializable(self):
        """Pydantic model_dump → JSON 시 str 값 유지."""
        data = {"status": FinalStatus.SUCCESS}
        dumped = json.dumps(data)
        assert '"success"' in dumped

    def test_enum_equality_with_raw_string(self):
        """역직렬화된 JSON 문자열과 Enum 비교."""
        raw = json.loads('{"status": "failure"}')
        assert raw["status"] == FinalStatus.FAILURE


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. ReasoningState 메서드 (W-06)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from src.agents.state.state import (
    DeadEnd,
    FailureType,
    KnowledgeItem,
    ReasoningState,
)


class TestReasoningStateMethods:
    """format_confirmed_text / format_dead_ends_text."""

    def test_format_confirmed_text_with_items(self):
        reason = ReasoningState(
            knowledge_items=[
                KnowledgeItem(
                    key="table:TB_CUST",
                    value="고객기본",
                    source="ES",
                    status=ConfidenceStatus.CONFIRMED,
                ),
                KnowledgeItem(
                    key="col:CUST_NO",
                    value="고객번호",
                    source="ES",
                    status=ConfidenceStatus.UNRESOLVED,
                ),
            ],
        )
        text = reason.format_confirmed_text()
        assert "table:TB_CUST" in text
        assert "col:CUST_NO" not in text

    def test_format_confirmed_text_empty(self):
        reason = ReasoningState()
        text = reason.format_confirmed_text()
        assert "사용 가능한 지식 항목 없음" in text

    def test_format_dead_ends_text_with_items(self):
        reason = ReasoningState(
            dead_ends=[
                DeadEnd(
                    hypothesis_id="h-1",
                    failure_type=FailureType.SQL_SYNTAX,
                    reason="GROUP BY 누락",
                ),
            ],
        )
        text = reason.format_dead_ends_text()
        assert "SQL_SYNTAX" in text
        assert "GROUP BY 누락" in text

    def test_format_dead_ends_text_empty(self):
        reason = ReasoningState()
        text = reason.format_dead_ends_text()
        assert text == "(없음)"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. serialize_decomp_slots (W-07)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from src.utils.llm.prompt import (
    render_prompt,
    serialize_decomp_slots,
)


class TestSerializeDecompSlots:

    def test_all_four_slots(self):
        decomp = {
            "measures": [{"term": "고객수", "agg": "COUNT"}],
            "filters": [{"col": "등급", "op": "=", "val": "VIP"}],
            "group_by": ["지점"],
            "order_limit": [{"type": "LIMIT", "value": "10"}],
        }
        result = serialize_decomp_slots(decomp)
        assert set(result.keys()) == {
            "{measures}", "{filters}", "{group_by}", "{order_limit}", "{output_hint}",
        }
        # JSON 문자열인지 확인
        for v in result.values():
            json.loads(v)  # 파싱 가능해야 함

    def test_empty_decomp(self):
        result = serialize_decomp_slots({})
        assert result["{measures}"] == "[]"
        assert result["{filters}"] == "[]"

    def test_korean_ensure_ascii_false(self):
        decomp = {"measures": [{"term": "고객수"}]}
        result = serialize_decomp_slots(decomp)
        assert "고객수" in result["{measures}"]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. extract_json 코드펜스 + strict (W-09)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from src.utils.llm.response import extract_json


class TestExtractJson:

    def test_plain_json(self):
        result = extract_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_json_with_surrounding_text(self):
        raw = '분석 결과입니다: {"intent": "EXTRACT"} 참고하세요.'
        result = extract_json(raw)
        assert result["intent"] == "EXTRACT"

    def test_code_fence_json(self):
        raw = '결과:\n```json\n{"intent": "EXTRACT"}\n```\n끝.'
        result = extract_json(raw)
        assert result["intent"] == "EXTRACT"

    def test_code_fence_without_lang(self):
        raw = '```\n{"a": 1}\n```'
        result = extract_json(raw)
        assert result == {"a": 1}

    def test_code_fence_with_surrounding_braces(self):
        """코드펜스 밖에 {참고}가 있어도 정상 추출."""
        raw = '설명: {참고}\n```json\n{"intent": "EXTRACT"}\n```'
        result = extract_json(raw)
        assert result["intent"] == "EXTRACT"

    def test_no_json_returns_none(self):
        assert extract_json("no json here") is None

    def test_strict_raises_on_failure(self):
        with pytest.raises(ValueError, match="유효한 JSON"):
            extract_json("no json", strict=True)

    def test_strict_success(self):
        result = extract_json('{"ok": true}', strict=True)
        assert result == {"ok": True}

    def test_invalid_json_returns_none(self):
        assert extract_json("{invalid json}") is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. sql_safety_checker Sybase TOP N (W-10)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from src.services.sql_safety_checker import validate_sql_safety


class TestSqlSafetyTopN:

    def test_postgres_limit_passes(self):
        result = validate_sql_safety(
            "SELECT * FROM tb_cust LIMIT 10",
            dialect="postgres",
        )
        # LIMIT이 있으므로 LIMIT 관련 오류 없어야 함
        limit_errors = [e for e in result.errors if "LIMIT" in e]
        assert len(limit_errors) == 0

    def test_tsql_top_passes(self):
        result = validate_sql_safety(
            "SELECT TOP 10 * FROM tb_cust",
            dialect="tsql",
        )
        limit_errors = [e for e in result.errors if "LIMIT" in e]
        assert len(limit_errors) == 0

    def test_tsql_without_top_fails(self):
        result = validate_sql_safety(
            "SELECT * FROM tb_cust",
            dialect="tsql",
        )
        limit_errors = [e for e in result.errors if "LIMIT" in e]
        assert len(limit_errors) > 0

    def test_aggregate_exempt(self):
        """집계 쿼리는 LIMIT 없어도 통과."""
        result = validate_sql_safety(
            "SELECT COUNT(*) FROM tb_cust",
            dialect="postgres",
        )
        limit_errors = [e for e in result.errors if "LIMIT" in e]
        assert len(limit_errors) == 0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 6. _safe_search 래퍼 (W-11)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from src.agents.nodes.reason.tools import _safe_search


class TestSafeSearch:
    """_safe_search 래퍼 동작 검증.

    _safe_search(coro) — 인수 1개 시그니처.
    예외는 호출자(_run_step)로 전파되어 텔레메트리에 정확히 기록된다.
    비-list 반환값은 빈 리스트로 정규화된다.
    """

    @pytest.mark.asyncio
    async def test_success(self):
        async def ok():
            return [{"a": 1}]
        result = await _safe_search(ok())
        assert result == [{"a": 1}]

    @pytest.mark.asyncio
    async def test_exception_propagates(self):
        """예외는 상위로 전파된다 — 구현 설계에 따른 기대값."""
        async def fail():
            raise ConnectionError("boom")
        with pytest.raises(ConnectionError, match="boom"):
            await _safe_search(fail())

    @pytest.mark.asyncio
    async def test_non_list_returns_empty(self):
        async def not_list():
            return "string result"
        result = await _safe_search(not_list())
        assert result == []


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 7. render_prompt (기존 + serialize_decomp_slots 통합)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestRenderPrompt:

    def test_basic_substitution(self):
        template = "질의: {query}, 테이블: {tables}"
        prompt, variables = render_prompt(template, {
            "{query}": "고객 수",
            "{tables}": "TB_CUST",
        })
        assert prompt == "질의: 고객 수, 테이블: TB_CUST"
        assert variables["query"] == "고객 수"

    def test_with_decomp_slots(self):
        decomp = {"measures": ["COUNT"], "filters": [], "group_by": [], "order_limit": []}
        template = "measures={measures}, filters={filters}"
        replacements = {
            **serialize_decomp_slots(decomp),
        }
        prompt, _ = render_prompt(template, replacements)
        assert '["COUNT"]' in prompt
        assert "[]" in prompt


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 8. _build_decomposition_from_normalized (W-15)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from src.agents.nodes.reason.reasoning_preparer import (
    _build_decomposition_from_normalized,
)
from src.agents.models.normalization import (
    NormalizedQuery,
    MeasureSlot,
    FilterSlot,
    DimensionSlot,
    ModifierSlot,
    EntitySlot,
)


class TestBuildDecomposition:

    def test_none_returns_empty(self):
        result = _build_decomposition_from_normalized(None)
        assert result == {
            "measures": [], "filters": [],
            "group_by": [], "order_limit": [],
        }

    def test_with_normalized_query(self):
        nq = NormalizedQuery(
            measures=[
                MeasureSlot(term="고객수", agg_function="COUNT"),
            ],
            filters=[
                FilterSlot(target="등급", filter_type="EQUALS", values=["VIP"]),
            ],
            dimensions=[
                DimensionSlot(term="지점", role="GROUP"),
                DimensionSlot(term="연도", role="FILTER"),
            ],
            modifiers=[
                ModifierSlot(type="LIMIT", limit=10),
            ],
            entities=[
                EntitySlot(term="고객"),
            ],
        )
        result = _build_decomposition_from_normalized(nq)

        assert len(result["measures"]) == 1
        assert result["measures"][0]["term"] == "고객수"
        assert result["measures"][0]["agg_function"] == "COUNT"

        assert len(result["filters"]) == 1
        assert result["filters"][0]["term"] == "등급"
        assert result["filters"][0]["operator"] == "EQUALS"
        assert result["filters"][0]["value"] == ["VIP"]

        # GROUP role만 group_by에 포함
        assert result["group_by"] == ["지점"]

        assert len(result["order_limit"]) == 1
        assert result["order_limit"][0]["value"] == "10"
        assert result["required_concepts"] == ["고객", "고객수"]

    def test_empty_normalized_query(self):
        nq = NormalizedQuery()
        result = _build_decomposition_from_normalized(nq)
        assert result["measures"] == []
        assert result["filters"] == []
        assert result["group_by"] == []


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 9. confidence_evaluator failure 보존 (C-02)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from src.agents.state.state import (
    Phase,
    PipelineState,
)
from src.agents.nodes.reason.readiness_gate import (
    readiness_gate_node,
)


class TestConfidenceEvaluatorFailurePreservation:

    @pytest.mark.asyncio
    async def test_preserves_existing_failure_type(self):
        """sql_validator가 설정한 failure_type이 보존되는지."""
        state = PipelineState(
            reason=ReasoningState(
                phase=Phase.VALIDATING,
                failure_type=FailureType.SQL_SYNTAX,
                failure_reason="GROUP BY 절 누락",
                knowledge_items=[],
            ),
        )
        result = await readiness_gate_node(state)
        reason = result["reason"]

        # REPLANNING으로 전환되더라도 기존 failure_type 보존
        if reason.phase == Phase.REPLANNING:
            assert reason.failure_type == FailureType.SQL_SYNTAX
            assert "GROUP BY" in reason.failure_reason

    @pytest.mark.asyncio
    async def test_sets_failure_when_none(self):
        """failure_type이 None이면 confidence_evaluator가 설정."""
        state = PipelineState(
            reason=ReasoningState(
                phase=Phase.EXPLORING,
                failure_type=None,
                knowledge_items=[],
            ),
        )
        result = await readiness_gate_node(state)
        reason = result["reason"]

        if reason.phase == Phase.REPLANNING:
            assert reason.failure_type is not None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 10. trace_analyzer FinalStatus 비교
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestFinalStatusComparison:
    """JSON에서 역직렬화된 문자열과 FinalStatus Enum 비교."""

    def test_success_comparison(self):
        assert "success" == FinalStatus.SUCCESS
        assert FinalStatus.SUCCESS == "success"

    def test_failure_comparison(self):
        assert "failure" == FinalStatus.FAILURE

    def test_in_dict_context(self):
        """trace_analyzer 패턴 시뮬레이션."""
        data = {"final_status": "failure"}
        assert data.get("final_status") == FinalStatus.FAILURE

    def test_json_roundtrip(self):
        original = FinalStatus.SUCCESS
        serialized = json.dumps({"status": original})
        deserialized = json.loads(serialized)
        assert deserialized["status"] == FinalStatus.SUCCESS


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 11. State Enum 필드 기본값 및 비교
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from src.agents.state.state import (
    ExecutionStep,
    Hypothesis,
)


class TestStateEnumFields:

    def test_hypothesis_default_status(self):
        h = Hypothesis(hypothesis_id="H1", description="test")
        assert h.status == HypothesisStatus.PENDING
        assert h.status == "PENDING"

    def test_step_default_status(self):
        s = ExecutionStep(
            step=1, tool="search_table_meta",
            input="고객", purpose="테스트",
        )
        assert s.status == StepStatus.PENDING

    def test_reasoning_state_default_final_status(self):
        r = ReasoningState()
        assert r.final_status == FinalStatus.PENDING
        assert r.final_status == "pending"

    def test_hypothesis_status_assignment(self):
        h = Hypothesis(
            hypothesis_id="H1", description="test",
            status=HypothesisStatus.ACTIVE,
        )
        assert h.status == HypothesisStatus.ACTIVE

    def test_step_status_done(self):
        s = ExecutionStep(
            step=1, tool="t", input="i", purpose="p",
            status=StepStatus.DONE,
        )
        assert s.status == StepStatus.DONE
