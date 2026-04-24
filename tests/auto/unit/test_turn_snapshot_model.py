"""TurnSnapshot 모델 단위 테스트.

테스트 대상:
    - TurnSnapshot 9개 필드 생성 (정상/최소 케이스)
    - JSON 직렬화 round-trip (model_dump_json → model_validate_json)
    - frozen 동작 (immutability)
    - extra="forbid" 동작
    - LangGraph Command(update) 주입 검증 (W4: 가장 중요)
    - PipelineState.turn_reset_updates()가 4개 필드를 초기화하고
      turn_snapshots는 보존하는지

테스트 대상 소스:
    src/agents/models/snapshot.py
    src/agents/state/state.py
    src/models/enums.py
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.agents.models.normalization import NormalizedQuery
from src.agents.models.snapshot import TurnSnapshot
from src.agents.state.state import (
    CodeMeta,
    PipelineState,
    SelectionStatus,
    TableMeta,
)
from src.models.enums import ContinueRoute, IntentType


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 픽스처
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _make_table_meta(table_name: str = "TB_ADW_CUST001M") -> TableMeta:
    """테스트용 최소 TableMeta를 반환한다."""
    return TableMeta(
        table_name=table_name,
        alt_name="고객기본",
        description="고객 기본 정보",
        selection_status=SelectionStatus.SELECTED,
    )


def _make_code_meta(column_name: str = "LOAN_STS_CD") -> CodeMeta:
    """테스트용 최소 CodeMeta를 반환한다."""
    return CodeMeta(
        column_name=column_name,
        column_desc="대출상태코드",
        codes={"01": "정상", "02": "연체"},
    )


def _make_full_snapshot() -> TurnSnapshot:
    """10개 필드 전부 채운 TurnSnapshot을 반환한다 (Path F' normalized_query 포함)."""
    return TurnSnapshot(
        user_message_seq=42,
        intent=IntentType.DATA_EXTRACTION,
        generated_sql="SELECT * FROM TB_ADW_CUST001M WHERE STDR_YMD = '20240101'",
        sql_explanation="고객기본 테이블에서 2024-01-01 기준 전체 조회",
        result_data={
            "columns": ["CUST_NO", "CUST_NM"],
            "column_formats": {},
            "total_count": 100,
            "displayed_count": 100,
        },
        visualization={
            "chart_type": "bar_chart",
            "config": {"title": "고객 현황"},
            "series": [],
        },
        selected_tables=[_make_table_meta()],
        explored_codes={"LOAN_STS_CD": _make_code_meta()},
        inferred_signals=[
            {
                "question": "기준일자",
                "value": "2024-01-01",
                "reasoning": "질의에 '이번 달 1일'로 명시",
                "source_node": "query_normalizer",
            }
        ],
        normalized_query=NormalizedQuery(
            original_query="이번 달 1일 고객 현황 보여줘",
            rewritten_query="2024-01-01 기준 고객 기본 현황 조회",
        ),
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. 필드 생성 — 정상 케이스
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestTurnSnapshotCreation:
    """TurnSnapshot 9개 필드 생성 검증."""

    def test_full_snapshot_all_fields(self) -> None:
        """9개 필드 전부 채워진 스냅샷이 정상 생성된다."""
        snap = _make_full_snapshot()

        assert snap.user_message_seq == 42
        assert snap.intent == IntentType.DATA_EXTRACTION
        assert snap.generated_sql is not None
        assert "TB_ADW_CUST001M" in snap.generated_sql
        assert snap.sql_explanation == "고객기본 테이블에서 2024-01-01 기준 전체 조회"
        assert snap.result_data is not None
        assert snap.result_data["total_count"] == 100
        assert snap.visualization is not None
        assert snap.visualization["chart_type"] == "bar_chart"
        assert len(snap.selected_tables) == 1
        assert snap.selected_tables[0].table_name == "TB_ADW_CUST001M"
        assert "LOAN_STS_CD" in snap.explored_codes
        assert snap.explored_codes["LOAN_STS_CD"].codes["01"] == "정상"
        assert len(snap.inferred_signals) == 1
        assert snap.inferred_signals[0]["source_node"] == "query_normalizer"

    def test_minimal_snapshot_required_fields_only(self) -> None:
        """필수 필드(user_message_seq, intent)만으로 생성되고 나머지는 기본값."""
        snap = TurnSnapshot(
            user_message_seq=1,
            intent=IntentType.DATA_ANALYSIS,
        )

        assert snap.user_message_seq == 1
        assert snap.intent == IntentType.DATA_ANALYSIS
        assert snap.generated_sql is None
        assert snap.sql_explanation == ""
        assert snap.result_data is None
        assert snap.visualization is None
        assert snap.selected_tables == []
        assert snap.explored_codes == {}
        assert snap.inferred_signals == []
        assert snap.normalized_query is None

    def test_normalized_query_field_preserved(self) -> None:
        """normalized_query 가 NormalizedQuery 타입으로 보존된다 (Path F' REGENERATE 재사용용)."""
        nq = NormalizedQuery(
            original_query="원본",
            rewritten_query="재작성",
        )
        snap = TurnSnapshot(
            user_message_seq=11,
            intent=IntentType.DATA_EXTRACTION,
            normalized_query=nq,
        )
        assert snap.normalized_query is not None
        assert snap.normalized_query.rewritten_query == "재작성"

    def test_null_optional_fields_accepted(self) -> None:
        """선택 필드에 명시적 None이 허용된다."""
        snap = TurnSnapshot(
            user_message_seq=5,
            intent=IntentType.GENERAL_QUESTION,
            generated_sql=None,
            result_data=None,
            visualization=None,
        )
        assert snap.generated_sql is None
        assert snap.result_data is None
        assert snap.visualization is None

    def test_explored_codes_dict_semantics(self) -> None:
        """explored_codes는 dict[str, CodeMeta] 시맨틱을 유지한다.

        하류 노드(sql_generator 등)가 reason.explored_codes와 동일한
        dict 접근 방식으로 소비할 수 있어야 한다.
        """
        snap = TurnSnapshot(
            user_message_seq=3,
            intent=IntentType.DATA_EXTRACTION,
            explored_codes={
                "LOAN_STS_CD": _make_code_meta("LOAN_STS_CD"),
                "PROD_CD": _make_code_meta("PROD_CD"),
            },
        )
        assert len(snap.explored_codes) == 2
        assert isinstance(snap.explored_codes["LOAN_STS_CD"], CodeMeta)
        assert snap.explored_codes["LOAN_STS_CD"].column_name == "LOAN_STS_CD"

    def test_selected_tables_full_meta_objects(self) -> None:
        """selected_tables는 이름 문자열이 아닌 TableMeta 풀 객체 리스트다."""
        tables = [
            _make_table_meta("TB_ADW_CUST001M"),
            _make_table_meta("TB_ADW_LOAN001M"),
        ]
        snap = TurnSnapshot(
            user_message_seq=7,
            intent=IntentType.DATA_EXTRACTION,
            selected_tables=tables,
        )
        assert len(snap.selected_tables) == 2
        assert isinstance(snap.selected_tables[0], TableMeta)
        assert snap.selected_tables[1].table_name == "TB_ADW_LOAN001M"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. JSON 직렬화 round-trip
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestTurnSnapshotJsonRoundTrip:
    """model_dump_json() → model_validate_json() round-trip 검증."""

    def test_full_snapshot_round_trip(self) -> None:
        """9개 필드 전부 채운 스냅샷이 JSON 직렬화 후 동일하게 복원된다."""
        original = _make_full_snapshot()
        json_str = original.model_dump_json()
        restored = TurnSnapshot.model_validate_json(json_str)

        assert restored.user_message_seq == original.user_message_seq
        assert restored.intent == original.intent
        assert restored.generated_sql == original.generated_sql
        assert restored.sql_explanation == original.sql_explanation
        assert restored.result_data == original.result_data
        assert restored.visualization == original.visualization
        assert len(restored.selected_tables) == len(original.selected_tables)
        assert restored.selected_tables[0].table_name == "TB_ADW_CUST001M"
        assert restored.explored_codes["LOAN_STS_CD"].codes == {"01": "정상", "02": "연체"}
        assert restored.inferred_signals == original.inferred_signals

    def test_minimal_snapshot_round_trip(self) -> None:
        """최소 필드 스냅샷도 round-trip이 정상 동작한다."""
        original = TurnSnapshot(
            user_message_seq=1,
            intent=IntentType.CASUAL_TALK,
        )
        json_str = original.model_dump_json()
        restored = TurnSnapshot.model_validate_json(json_str)

        assert restored.user_message_seq == 1
        assert restored.intent == IntentType.CASUAL_TALK
        assert restored.generated_sql is None
        assert restored.selected_tables == []

    def test_intent_enum_serialized_as_string(self) -> None:
        """IntentType이 JSON에서 문자열 값으로 직렬화된다."""
        snap = TurnSnapshot(
            user_message_seq=2,
            intent=IntentType.DATA_EXTRACTION,
        )
        json_str = snap.model_dump_json()
        assert '"data_extraction"' in json_str

    def test_model_dump_excludes_rows(self) -> None:
        """result_data에 rows 키가 없어도 직렬화/복원이 정상이다.

        rows는 checkpoint_dc_messages 단일 원천. 스냅샷에 포함하지 않는다.
        """
        snap = TurnSnapshot(
            user_message_seq=9,
            intent=IntentType.DATA_EXTRACTION,
            result_data={
                "columns": ["A", "B"],
                "column_formats": {},
                "total_count": 50,
                "displayed_count": 50,
                # rows 키 없음 — 의도적 제외
            },
        )
        json_str = snap.model_dump_json()
        restored = TurnSnapshot.model_validate_json(json_str)
        assert restored.result_data is not None
        assert "rows" not in restored.result_data
        assert restored.result_data["total_count"] == 50


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. frozen 동작 (immutability)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestTurnSnapshotFrozen:
    """frozen=True — 생성 후 필드 변경 불가 검증."""

    def test_cannot_assign_field_after_creation(self) -> None:
        """생성 후 필드에 값을 할당하면 ValidationError가 발생한다."""
        snap = TurnSnapshot(
            user_message_seq=1,
            intent=IntentType.DATA_EXTRACTION,
        )
        with pytest.raises((ValidationError, TypeError)):
            snap.user_message_seq = 999  # type: ignore[misc]

    def test_cannot_assign_optional_field(self) -> None:
        """선택 필드도 할당 불가다."""
        snap = TurnSnapshot(
            user_message_seq=2,
            intent=IntentType.DATA_ANALYSIS,
        )
        with pytest.raises((ValidationError, TypeError)):
            snap.generated_sql = "SELECT 1"  # type: ignore[misc]

    def test_cannot_mutate_intent(self) -> None:
        """intent 필드도 변경 불가다."""
        snap = TurnSnapshot(
            user_message_seq=3,
            intent=IntentType.GENERAL_QUESTION,
        )
        with pytest.raises((ValidationError, TypeError)):
            snap.intent = IntentType.DATA_EXTRACTION  # type: ignore[misc]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. extra="forbid" 동작
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestTurnSnapshotExtraForbid:
    """extra="forbid" — 정의되지 않은 필드 주입 즉시 오류."""

    def test_unknown_field_raises_validation_error(self) -> None:
        """정의되지 않은 필드를 넘기면 ValidationError가 발생한다."""
        with pytest.raises(ValidationError) as exc_info:
            TurnSnapshot(  # type: ignore[call-arg]
                user_message_seq=1,
                intent=IntentType.DATA_EXTRACTION,
                unknown_field="should_fail",
            )
        assert "unknown_field" in str(exc_info.value)

    def test_extra_nested_field_raises(self) -> None:
        """복수의 알 수 없는 필드도 거부된다."""
        with pytest.raises(ValidationError):
            TurnSnapshot(  # type: ignore[call-arg]
                user_message_seq=1,
                intent=IntentType.DATA_EXTRACTION,
                rows=[{"A": 1}],
                summary="이전 분석 요약",
            )

    def test_known_fields_do_not_raise(self) -> None:
        """정의된 필드만 사용하면 오류 없이 생성된다."""
        snap = TurnSnapshot(
            user_message_seq=1,
            intent=IntentType.DATA_EXTRACTION,
            sql_explanation="정상 필드",
        )
        assert snap.sql_explanation == "정상 필드"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. LangGraph Command(update) 주입 검증 (W4)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestTurnSnapshotLangGraphCommandInjection:
    """LangGraph Command(update) 로 PipelineState에 TurnSnapshot 주입 검증.

    PipelineState가 Pydantic BaseModel이기 때문에 LangGraph가 dict 머지 시
    BaseModel 필드 검증이 정상 통과해야 한다 (W4 설계 검토 항목).
    """

    def test_turn_snapshots_and_route_set_via_dict_update(self) -> None:
        """dict 직접 업데이트로 turn_snapshots에 append하고 route 필드가 주입된다."""
        snap = _make_full_snapshot()
        state = PipelineState()

        # LangGraph Command(update)가 내부적으로 수행하는 dict 머지를 직접 시뮬레이션
        updated = state.model_copy(
            update={
                "turn_snapshots": [snap],
                "route": ContinueRoute.REDISPLAY,
            }
        )

        assert len(updated.turn_snapshots) == 1
        assert updated.turn_snapshots[0] is snap
        assert updated.turn_snapshots[0].user_message_seq == 42
        assert updated.route == ContinueRoute.REDISPLAY

    def test_turn_snapshot_fields_readable_after_injection(self) -> None:
        """turn_snapshots에 주입된 스냅샷의 모든 9개 필드를 정상 읽을 수 있다."""
        snap = _make_full_snapshot()
        state = PipelineState()
        updated = state.model_copy(update={"turn_snapshots": [snap]})

        rs = updated.turn_snapshots[-1]
        assert rs.intent == IntentType.DATA_EXTRACTION
        assert rs.generated_sql is not None
        assert rs.result_data is not None
        assert rs.visualization is not None
        assert len(rs.selected_tables) == 1
        assert "LOAN_STS_CD" in rs.explored_codes
        assert len(rs.inferred_signals) == 1

    def test_command_update_with_all_continue_fields(self) -> None:
        """continue_orchestrator가 반환하는 Command(update) 전체 필드 패턴 검증.

        §3.5 설계 Command(update) 구조:
            route, handoff_note, reference_turns, intent
        """
        snap = _make_full_snapshot()
        state = PipelineState(turn_snapshots=[snap])

        updated = state.model_copy(
            update={
                "route": ContinueRoute.REFINE,
                "handoff_note": "WHERE에 서울 지역 조건 추가",
                "reference_turns": ["T1"],
                "intent": snap.intent,  # C2: ANALYZE 오라우팅 방지
            }
        )

        assert len(updated.turn_snapshots) == 1
        assert updated.route == ContinueRoute.REFINE
        assert updated.handoff_note == "WHERE에 서울 지역 조건 추가"
        assert updated.reference_turns == ["T1"]
        assert updated.intent == IntentType.DATA_EXTRACTION

    def test_turn_snapshots_list_appended(self) -> None:
        """turn_snapshots 리스트에 스냅샷을 추가할 수 있다."""
        snap1 = TurnSnapshot(user_message_seq=1, intent=IntentType.DATA_EXTRACTION)
        snap2 = TurnSnapshot(user_message_seq=2, intent=IntentType.DATA_ANALYSIS)
        state = PipelineState()

        updated = state.model_copy(
            update={"turn_snapshots": [snap1, snap2]}
        )
        assert len(updated.turn_snapshots) == 2
        assert updated.turn_snapshots[0].user_message_seq == 1
        assert updated.turn_snapshots[1].user_message_seq == 2


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 6. turn_reset_updates() — 4개 필드 초기화 + turn_snapshots 보존
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestTurnResetUpdates:
    """turn_reset_updates()가 CONTINUE 관련 필드를 올바르게 처리하는지 검증."""

    def test_continue_fields_initialized(self) -> None:
        """턴 스코프 CONTINUE 필드가 turn_reset_updates() 반환값에 포함된다."""
        updates = PipelineState.turn_reset_updates()

        assert "route" in updates
        assert "handoff_note" in updates
        assert "reference_turns" in updates
        assert "current_user_message_seq" in updates

    def test_route_reset_to_none(self) -> None:
        """route는 None으로 초기화된다."""
        updates = PipelineState.turn_reset_updates()
        assert updates["route"] is None

    def test_handoff_note_reset_to_empty_string(self) -> None:
        """handoff_note는 빈 문자열로 초기화된다."""
        updates = PipelineState.turn_reset_updates()
        assert updates["handoff_note"] == ""

    def test_reference_turns_reset_to_empty_list(self) -> None:
        """reference_turns는 빈 리스트로 초기화된다."""
        updates = PipelineState.turn_reset_updates()
        assert updates["reference_turns"] == []

    def test_current_user_message_seq_reset_to_none(self) -> None:
        """current_user_message_seq는 None으로 초기화된다."""
        updates = PipelineState.turn_reset_updates()
        assert updates["current_user_message_seq"] is None

    def test_turn_snapshots_not_in_reset_updates(self) -> None:
        """turn_snapshots는 세션 지속 필드이므로 turn_reset_updates에 없다."""
        updates = PipelineState.turn_reset_updates()
        assert "turn_snapshots" not in updates

    def test_reset_applied_to_state_clears_continue_fields(self) -> None:
        """turn_reset_updates()를 적용하면 이전 턴 CONTINUE 필드가 초기화된다."""
        snap = _make_full_snapshot()
        state = PipelineState()
        # 이전 턴 CONTINUE 상태 주입
        populated = state.model_copy(
            update={
                "turn_snapshots": [snap],
                "route": ContinueRoute.REDISPLAY,
                "handoff_note": "이전 지시",
                "reference_turns": ["T1"],
                "current_user_message_seq": 99,
            }
        )
        assert len(populated.turn_snapshots) == 1
        assert populated.route == ContinueRoute.REDISPLAY

        # 새 턴 시작 시 turn_reset_updates 적용 — turn_snapshots 는 세션 지속
        reset = populated.model_copy(update=PipelineState.turn_reset_updates())
        assert reset.route is None
        assert reset.handoff_note == ""
        assert reset.reference_turns == []
        assert len(reset.turn_snapshots) == 1  # 세션 지속
        assert reset.current_user_message_seq is None

    def test_reset_preserves_turn_snapshots(self) -> None:
        """turn_reset_updates() 적용 후 turn_snapshots는 보존된다."""
        snap = _make_full_snapshot()
        state = PipelineState()
        with_snaps = state.model_copy(update={"turn_snapshots": [snap]})
        assert len(with_snaps.turn_snapshots) == 1

        # turn_reset을 적용해도 turn_snapshots는 변하지 않아야 함
        reset = with_snaps.model_copy(update=PipelineState.turn_reset_updates())
        assert len(reset.turn_snapshots) == 1
        assert reset.turn_snapshots[0].user_message_seq == 42


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 7. ContinueRoute / QueryStatus Enum 검증
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestNewEnumValues:
    """enums.py 신규 추가 값 검증."""

    def test_continue_route_values(self) -> None:
        """ContinueRoute 4개 멤버가 소문자 문자열 값을 가진다 (Path F' §3.2.3)."""
        assert ContinueRoute.REDISPLAY == "redisplay"
        assert ContinueRoute.ANALYZE == "analyze"
        assert ContinueRoute.REGENERATE == "regenerate"
        assert ContinueRoute.REFINE == "refine"

    def test_continue_route_is_str_subclass(self) -> None:
        """ContinueRoute이 str 상속으로 JSON 직렬화 호환된다."""
        assert isinstance(ContinueRoute.REDISPLAY, str)
        assert isinstance(ContinueRoute.REGENERATE, str)
        assert isinstance(ContinueRoute.REFINE, str)

    def test_query_status_continue_orchestration_pending(self) -> None:
        """QueryStatus.CONTINUE_ORCHESTRATION_PENDING가 정상 접근 가능하다."""
        from src.models.enums import QueryStatus
        assert (
            QueryStatus.CONTINUE_ORCHESTRATION_PENDING
            == "continue_orchestration_pending"
        )

    def test_query_status_existing_values_unchanged(self) -> None:
        """기존 QueryStatus 멤버가 변경되지 않았다."""
        from src.models.enums import QueryStatus
        assert QueryStatus.PENDING == "pending"
        assert QueryStatus.COMPLETED == "completed"
        assert QueryStatus.ERROR == "error"
        assert QueryStatus.SQL_GENERATED == "sql_generated"
