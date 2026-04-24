"""continue_orchestrator 노드 단위 테스트 (4-way, Path F').

테스트 대상:
    - 4-way route(redisplay/analyze/regenerate/refine) 각각이 올바른 goto 노드를
      반환하는지
    - route별 handoff_note 필수 섹션 헤더 검증 (§5)
    - REGENERATE → REFINE 폴백 다운그레이드 (스냅샷 normalized_query 없음 시)
    - REGENERATE hydration — normalized_query 통째 복원
    - LLM JSON 파싱 실패 시 error_end fallback
    - turn_snapshots 비어있을 때 즉시 error_end fallback
    - _serialize_snapshots 직렬화 함수 독립 테스트
    - _parse_orchestrator_response 파싱 함수 독립 테스트 (3필드 OUTPUT)

테스트 대상 소스:
    src/agents/nodes/interpret/continue_orchestrator.py
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from langgraph.types import Command

from src.agents.models.normalization import NormalizedQuery
from src.agents.models.snapshot import TurnSnapshot
from src.agents.nodes.interpret.continue_orchestrator import (
    _parse_orchestrator_response,
    _serialize_snapshots,
    _validate_handoff_note_headers,
    continue_orchestrator_node,
)
from src.agents.state.state import (
    PipelineState,
    QueryStatus,
)
from src.models.enums import ContinueRoute, IntentType
from src.models.trace import TraceEntry

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 픽스처
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# route별 필수 헤더를 포함한 기본 handoff_note 샘플 (헤더 검증 통과용).
_DEFAULT_NOTE_BY_ROUTE: dict[ContinueRoute, str] = {
    ContinueRoute.REDISPLAY: (
        "### 시각화/포맷 지시\n표로 재출력하고 금액 단위는 억원으로 통일하세요."
    ),
    ContinueRoute.ANALYZE: (
        "### 분석 초점\n지점별 비중·집중도 관점으로 분석하세요."
    ),
    ContinueRoute.REGENERATE: (
        "### SQL 생성 지시\n분기 축을 지점별로 교체하세요."
    ),
    ContinueRoute.REFINE: (
        "### 연속 처리 의도\n서울 지역 조건을 추가하여 재조회하세요."
    ),
}


def _make_snapshot(
    seq: int = 10,
    with_normalized_query: bool = False,
) -> TurnSnapshot:
    """테스트용 최소 TurnSnapshot을 반환한다.

    Args:
        seq: user_message_seq.
        with_normalized_query: True 면 NormalizedQuery 기본 인스턴스를 부착
            (REGENERATE 경로 테스트용).
    """
    nq = NormalizedQuery(
        original_query="이전 턴 질의",
        rewritten_query="이전 턴 정규화",
    ) if with_normalized_query else None

    return TurnSnapshot(
        user_message_seq=seq,
        intent=IntentType.DATA_EXTRACTION,
        generated_sql="SELECT 1 FROM TB_TEST",
        sql_explanation="테스트용 SQL",
        result_data={
            "columns": ["COL1", "COL2"],
            "total_count": 5,
        },
        normalized_query=nq,
        target_db="INFO_DB",
    )


def _make_state(
    snapshots: list[Any] | None = None,
    trace_entries: int = 0,
    reference_turns: list[str] | None = None,
) -> PipelineState:
    """테스트용 PipelineState를 반환한다."""
    if snapshots is None:
        snapshots = [_make_snapshot(seq=10)]

    trace_log: list[TraceEntry] = []
    for _ in range(trace_entries):
        trace_log.append(
            TraceEntry(
                node="연속질의오케스트레이터",
                action="REDISPLAY → visualizer",
                detail="",
            )
        )

    return PipelineState(
        preprocessed_input="이전 결과 다시 보여줘",
        turn_snapshots=snapshots,
        trace_log=trace_log,
        status=QueryStatus.CONTINUE_ORCHESTRATION_PENDING,
        intent=IntentType.DATA_EXTRACTION,
        reference_turns=list(reference_turns) if reference_turns else [],
    )


def _make_llm_response(
    route: ContinueRoute,
    handoff_note: str | None = None,
    reasoning: str = "테스트 근거",
) -> dict:
    """LLM 파싱 결과 dict를 반환한다.

    handoff_note 가 None 이면 route별 기본 샘플(필수 헤더 포함)을 사용한다.
    """
    note = handoff_note if handoff_note is not None else _DEFAULT_NOTE_BY_ROUTE[route]
    return {
        "route": route,
        "handoff_note": note,
        "reasoning": reasoning,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. _serialize_snapshots 독립 테스트
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestSerializeSnapshots:
    """_serialize_snapshots 직렬화 함수 검증."""

    def test_empty_returns_placeholder(self) -> None:
        """빈 목록은 placeholder 문자열을 반환한다."""
        result = _serialize_snapshots([])
        assert "(이전 턴 스냅샷 없음)" in result

    def test_single_snapshot_contains_seq(self) -> None:
        """단일 스냅샷에 user_message_seq가 포함된다."""
        snap = _make_snapshot(seq=42)
        result = _serialize_snapshots([snap])
        assert "42" in result

    def test_multiple_snapshots_all_seqs(self) -> None:
        """복수 스냅샷에 모든 seq가 포함된다."""
        snaps = [_make_snapshot(seq=10), _make_snapshot(seq=20)]
        result = _serialize_snapshots(snaps)
        assert "10" in result
        assert "20" in result

    def test_no_rows_in_output(self) -> None:
        """rows 필드는 직렬화 결과에 포함되지 않는다."""
        snap = _make_snapshot(seq=5)
        result = _serialize_snapshots([snap])
        assert "rows" not in result

    def test_normalized_query_summary_included(self) -> None:
        """normalized_query 가 있으면 요약 라인이 포함된다 (REGENERATE 힌트용)."""
        snap = _make_snapshot(seq=7, with_normalized_query=True)
        result = _serialize_snapshots([snap])
        assert "normalized_query" in result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. _parse_orchestrator_response 독립 테스트 (3필드 OUTPUT)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestParseOrchestratorResponse:
    """_parse_orchestrator_response 파싱 함수 검증 (§3.2.3: 3필드)."""

    def test_valid_redisplay(self) -> None:
        """route=redisplay가 올바르게 파싱된다."""
        raw = """{
            "route": "redisplay",
            "handoff_note": "표로 재출력",
            "reasoning": "포맷 변환 요청"
        }"""
        result = _parse_orchestrator_response(raw)
        assert result["route"] is ContinueRoute.REDISPLAY
        assert result["handoff_note"] == "표로 재출력"
        assert result["reasoning"] == "포맷 변환 요청"

    def test_valid_refine(self) -> None:
        """route=refine이 올바르게 파싱된다."""
        raw = """{
            "route": "refine",
            "handoff_note": "서울 지역 조건 추가",
            "reasoning": "조건 추가 필요"
        }"""
        result = _parse_orchestrator_response(raw)
        assert result["route"] is ContinueRoute.REFINE
        assert "서울" in result["handoff_note"]

    def test_valid_analyze(self) -> None:
        """route=analyze가 올바르게 파싱된다."""
        raw = """{
            "route": "analyze",
            "handoff_note": "추세 분석",
            "reasoning": "분석 요청"
        }"""
        result = _parse_orchestrator_response(raw)
        assert result["route"] is ContinueRoute.ANALYZE

    def test_valid_regenerate(self) -> None:
        """route=regenerate가 올바르게 파싱된다 (4-way 신규)."""
        raw = """{
            "route": "regenerate",
            "handoff_note": "분기 축 교체",
            "reasoning": "표현 방식만 변경"
        }"""
        result = _parse_orchestrator_response(raw)
        assert result["route"] is ContinueRoute.REGENERATE

    def test_invalid_route_raises(self) -> None:
        """허용되지 않는 route는 ValueError를 발생시킨다."""
        raw = """{
            "route": "unknown_route",
            "handoff_note": "",
            "reasoning": ""
        }"""
        with pytest.raises(ValueError, match="허용되지 않는 route"):
            _parse_orchestrator_response(raw)

    def test_legacy_route_rejected(self) -> None:
        """이전 명칭(present/revise/fresh)은 더 이상 허용되지 않는다."""
        for legacy in ("present", "revise", "fresh"):
            raw = f'{{"route": "{legacy}", "handoff_note": "", "reasoning": ""}}'
            with pytest.raises(ValueError, match="허용되지 않는 route"):
                _parse_orchestrator_response(raw)

    def test_markdown_fence_stripped(self) -> None:
        """마크다운 코드 펜스가 있어도 파싱된다."""
        raw = """```json
{
    "route": "redisplay",
    "handoff_note": "재출력",
    "reasoning": "테스트"
}
```"""
        result = _parse_orchestrator_response(raw)
        assert result["route"] is ContinueRoute.REDISPLAY

    def test_invalid_json_raises(self) -> None:
        """JSON 파싱 불가 시 ValueError를 발생시킨다."""
        with pytest.raises(ValueError):
            _parse_orchestrator_response("완전히 잘못된 응답")

    def test_regex_fallback_partial_json(self) -> None:
        """부분 JSON(정규식 fallback)도 route 추출 가능하면 파싱된다."""
        raw = '일부 텍스트 "route": "refine", "handoff_note": "fallback 지시"'
        result = _parse_orchestrator_response(raw)
        assert result["route"] is ContinueRoute.REFINE


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. _validate_handoff_note_headers 독립 테스트 (§5)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestValidateHandoffNoteHeaders:
    """route별 필수 섹션 헤더 검증."""

    def test_redisplay_required_present(self) -> None:
        """REDISPLAY: '### 시각화/포맷 지시' 포함 시 위반 없음."""
        errors = _validate_handoff_note_headers(
            ContinueRoute.REDISPLAY,
            "### 시각화/포맷 지시\n표로 바꿔줘",
        )
        assert errors == []

    def test_redisplay_required_missing(self) -> None:
        """REDISPLAY: 필수 헤더 누락 시 위반 1건."""
        errors = _validate_handoff_note_headers(
            ContinueRoute.REDISPLAY, "표로 바꿔줘",
        )
        assert len(errors) == 1
        assert "시각화/포맷 지시" in errors[0]

    def test_regenerate_required_present(self) -> None:
        """REGENERATE: '### SQL 생성 지시' 포함 시 위반 없음 (정규화는 hydrate)."""
        errors = _validate_handoff_note_headers(
            ContinueRoute.REGENERATE,
            "### SQL 생성 지시\n분기 축 교체",
        )
        assert errors == []

    def test_regenerate_required_missing(self) -> None:
        """REGENERATE: 필수 헤더 누락 시 위반 1건."""
        errors = _validate_handoff_note_headers(
            ContinueRoute.REGENERATE, "분기 축 교체",
        )
        assert len(errors) == 1
        assert "SQL 생성 지시" in errors[0]

    def test_analyze_required_present(self) -> None:
        """ANALYZE: '### 분석 초점' 포함 시 위반 없음."""
        errors = _validate_handoff_note_headers(
            ContinueRoute.ANALYZE,
            "### 분석 초점\n비중 중심 분석",
        )
        assert errors == []


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. continue_orchestrator_node 통합 (LLM Mock)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.mark.asyncio
class TestContinueOrchestratorNode:
    """continue_orchestrator_node 4-way 라우팅 검증 (LLM Mock)."""

    async def _run(
        self,
        route: ContinueRoute,
        handoff_note: str | None = None,
        snapshots: list[Any] | None = None,
        reference_turns: list[str] | None = None,
    ) -> Command:
        """LLM을 Mock으로 대체하고 노드를 실행한다."""
        state = _make_state(snapshots=snapshots, reference_turns=reference_turns)
        parsed = _make_llm_response(route, handoff_note)

        with (
            patch(
                "src.agents.nodes.interpret.continue_orchestrator"
                ".llm_call_with_parse_retry",
                new_callable=AsyncMock,
                return_value=("raw_text", parsed),
            ),
            patch(
                "src.agents.nodes.interpret.continue_orchestrator"
                ".dispatch_tracking_event",
                new_callable=AsyncMock,
            ),
            # REDISPLAY/ANALYZE 경로는 rows 를 metadata JIT fetch 로 복원한다.
            # 단위 테스트는 라우팅·hydration 시맨틱만 검증하므로 빈 rows 성공으로 Mock.
            patch(
                "src.agents.nodes.interpret.continue_orchestrator"
                "._fetch_rows_from_metadata",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            return await continue_orchestrator_node(state)

    async def test_redisplay_goto_visualizer(self) -> None:
        """REDISPLAY는 visualizer로 라우팅된다 (SQL 재실행 없이 재렌더)."""
        cmd = await self._run(ContinueRoute.REDISPLAY)
        assert cmd.goto == "visualizer"
        assert cmd.update["route"] is ContinueRoute.REDISPLAY

    async def test_refine_goto_query_normalizer(self) -> None:
        """REFINE은 query_normalizer로 라우팅된다 (SQL 재생성)."""
        cmd = await self._run(ContinueRoute.REFINE)
        assert cmd.goto == "query_normalizer"
        assert cmd.update["route"] is ContinueRoute.REFINE

    async def test_analyze_goto_analyzer(self) -> None:
        """ANALYZE는 analyzer로 라우팅되고 intent가 DATA_ANALYSIS로 교체된다."""
        cmd = await self._run(ContinueRoute.ANALYZE)
        assert cmd.goto == "analyzer"
        assert cmd.update["route"] is ContinueRoute.ANALYZE
        assert cmd.update["intent"] == IntentType.DATA_ANALYSIS

    async def test_regenerate_goto_sql_generator(self) -> None:
        """REGENERATE는 sql_generator로 직행한다 (reasoning_preparer 스킵, 4-way 신규)."""
        snap = _make_snapshot(seq=10, with_normalized_query=True)
        cmd = await self._run(
            ContinueRoute.REGENERATE, snapshots=[snap],
        )
        assert cmd.goto == "sql_generator"
        assert cmd.update["route"] is ContinueRoute.REGENERATE

    async def test_regenerate_hydrates_normalized_query(self) -> None:
        """REGENERATE는 normalized_query 를 통째로 복원한다 (no slot patching)."""
        nq_marker = NormalizedQuery(
            original_query="마커용 원본",
            rewritten_query="마커용 재작성",
        )
        snap = TurnSnapshot(
            user_message_seq=15,
            intent=IntentType.DATA_EXTRACTION,
            generated_sql="SELECT 1",
            sql_explanation="",
            result_data={"columns": ["C1"], "total_count": 1},
            normalized_query=nq_marker,
            target_db="INFO_DB",
        )
        cmd = await self._run(
            ContinueRoute.REGENERATE, snapshots=[snap],
        )
        hydrated = cmd.update.get("normalized_query")
        assert hydrated is not None
        assert hydrated.rewritten_query == "마커용 재작성"

    async def test_regenerate_downgrades_to_refine_when_nq_missing(self) -> None:
        """스냅샷 normalized_query 가 없으면 REGENERATE → REFINE 폴백."""
        # normalized_query=None (기본값) 인 스냅샷
        snap = _make_snapshot(seq=10, with_normalized_query=False)
        # LLM은 REGENERATE 를 반환하지만, 오케스트레이터가 REFINE 으로 다운그레이드.
        # handoff_note 는 REGENERATE 헤더를 그대로 두고, 다운그레이드 후에는
        # 헤더 재검증을 수행하지 않는다(설계 §5).
        cmd = await self._run(
            ContinueRoute.REGENERATE, snapshots=[snap],
        )
        assert cmd.goto == "query_normalizer"
        assert cmd.update["route"] is ContinueRoute.REFINE

    async def test_handoff_note_propagated(self) -> None:
        """LLM이 생성한 handoff_note가 state.handoff_note에 기록된다."""
        note = "### 연속 처리 의도\nWHERE에 서울 추가"
        cmd = await self._run(ContinueRoute.REFINE, handoff_note=note)
        assert cmd.update.get("handoff_note") == note

    async def test_reference_turns_propagated(self) -> None:
        """intent_classifier 산출 reference_turns가 그대로 Command(update)에 포함된다."""
        cmd = await self._run(
            ContinueRoute.REDISPLAY, reference_turns=["T2", "T4"],
        )
        assert cmd.update.get("reference_turns") == ["T2", "T4"]

    async def test_redisplay_hydrates_sql_result(self) -> None:
        """REDISPLAY 경로는 대표 스냅샷 result_data를 sql_result로 hydrate한다."""
        cmd = await self._run(ContinueRoute.REDISPLAY)
        sql_result = cmd.update.get("sql_result")
        assert sql_result is not None
        assert sql_result.columns == ["COL1", "COL2"]

    async def test_refine_missing_required_header_goes_to_error_end(self) -> None:
        """REFINE에서 필수 헤더 '### 연속 처리 의도' 누락 시 error_end."""
        state = _make_state()
        parsed = {
            "route": ContinueRoute.REFINE,
            "handoff_note": "서울 조건 추가",  # 필수 헤더 없음
            "reasoning": "헤더 누락 테스트",
        }
        with (
            patch(
                "src.agents.nodes.interpret.continue_orchestrator"
                ".llm_call_with_parse_retry",
                new_callable=AsyncMock,
                return_value=("raw", parsed),
            ),
            patch(
                "src.agents.nodes.interpret.continue_orchestrator"
                ".dispatch_tracking_event",
                new_callable=AsyncMock,
            ),
        ):
            cmd = await continue_orchestrator_node(state)
        assert cmd.goto == "error_end"
        assert cmd.update["status"] == QueryStatus.ERROR

    async def test_regenerate_missing_header_goes_to_error_end(self) -> None:
        """REGENERATE 필수 헤더 누락 시 error_end."""
        snap = _make_snapshot(seq=10, with_normalized_query=True)
        state = _make_state(snapshots=[snap])
        parsed = {
            "route": ContinueRoute.REGENERATE,
            "handoff_note": "분기 축 교체",  # 필수 헤더 없음
            "reasoning": "헤더 누락 테스트",
        }
        with (
            patch(
                "src.agents.nodes.interpret.continue_orchestrator"
                ".llm_call_with_parse_retry",
                new_callable=AsyncMock,
                return_value=("raw", parsed),
            ),
            patch(
                "src.agents.nodes.interpret.continue_orchestrator"
                ".dispatch_tracking_event",
                new_callable=AsyncMock,
            ),
        ):
            cmd = await continue_orchestrator_node(state)
        assert cmd.goto == "error_end"

    async def test_empty_snapshots_goes_to_error_end(self) -> None:
        """turn_snapshots가 비어있으면 즉시 error_end로 종료한다."""
        state = _make_state(snapshots=[])
        cmd = await continue_orchestrator_node(state)
        assert cmd.goto == "error_end"
        assert cmd.update["status"] == QueryStatus.ERROR

    async def test_llm_parse_error_goes_to_error_end(self) -> None:
        """LLM 파싱 실패 시 error_end로 종료한다 (상류 회귀 없음)."""
        from src.utils.llm import ParseError

        state = _make_state()
        with (
            patch(
                "src.agents.nodes.interpret.continue_orchestrator"
                ".llm_call_with_parse_retry",
                new_callable=AsyncMock,
                side_effect=ParseError("파싱 실패"),
            ),
            patch(
                "src.agents.nodes.interpret.continue_orchestrator"
                ".dispatch_tracking_event",
                new_callable=AsyncMock,
            ),
        ):
            cmd = await continue_orchestrator_node(state)

        assert cmd.goto == "error_end"
        assert cmd.update["status"] == QueryStatus.ERROR
