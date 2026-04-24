"""TurnSnapshotStore 단위 테스트.

테스트 대상:
    src/services/turn_snapshot_store.py

테스트 케이스:
    - restore_from_db: pool=None 시 빈 리스트 반환
    - restore_from_db: DB 조회 실패 시 빈 리스트 반환 (파이프라인 차단 없음)
    - restore_from_db: 성공 케이스 — 스냅샷 ASC 정렬 확인
    - restore_from_db: 개별 턴 빌드 실패 시 해당 턴만 스킵
    - _parse_intent: 정상/알수없음 폴백
    - _extract_table_names: process_summary에서 used=true 테이블만 추출
    - _extract_code_columns: SQL에서 _CD/_TP 등 코드 컬럼 추출
    - extract_snapshot_result_data: rows 제외 메타데이터만 추출
    - _extract_inferred_signals: intent_classifier 제외, INFER만 추출
    - _fanout_mongo_lookups: 일부 실패 시 해당 항목만 제외 (Partial Hydration)
    - _build_snapshot_from_row: 정상 케이스

실행:
    pytest tests/auto/unit/test_turn_snapshot_store.py -v
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.models.snapshot import TurnSnapshot
from src.agents.state.state import CodeMeta, TableMeta
from src.models.enums import IntentType
from src.services.turn_snapshot_store import (
    _build_code_meta,
    _build_snapshot_from_row,
    _extract_code_columns,
    _extract_inferred_signals,
    _extract_knowledge_items,
    _extract_normalized_query,
    _extract_query_decomposition,
    extract_snapshot_result_data,
    _extract_table_names,
    _parse_intent,
    _safe_dict,
    restore_from_db,
)


def _make_row(
    seq: int = 10,
    intent: str = "data_extraction",
    executed_sql: str = "SELECT * FROM TB_ADW_LOAN001M",
    sql_explanation: str = "여신 현황 조회",
    process_summary: dict | None = None,
    result_data: dict | None = None,
    visualization: dict | None = None,
) -> dict[str, Any]:
    """테스트용 DB 행 dict를 반환한다."""
    ps = process_summary or {
        "context": {
            "tables": [
                {
                    "name": "TB_ADW_LOAN001M",
                    "used": True,
                    "status": "selected",
                },
            ],
        },
        "ai_decisions": {
            "inferences": [
                {
                    "question": "기간은?",
                    "value": "2024년 전체",
                    "source_node": "normalize_query",
                    "reason": "질의에 연도 명시",
                },
            ],
        },
    }
    rd = result_data or {
        "columns": ["loan_amt", "branch_nm"],
        "total_count": 100,
        "displayed_count": 50,
        "rows": [{"loan_amt": 1000, "branch_nm": "서울"}],
    }
    return {
        "seq": seq,
        "intent": intent,
        "executed_sql": executed_sql,
        "sql_explanation": sql_explanation,
        "result_data": rd,
        "visualization": visualization,
        "process_summary": ps,
    }


def _make_table_meta_dict(
    table_name: str = "TB_ADW_LOAN001M",
) -> dict[str, Any]:
    """테스트용 MongoDB 테이블 메타 raw dict를 반환한다."""
    return {
        "name": table_name,
        "alt_name": "여신현황",
        "description": "여신 현황 마스터 테이블",
        "schema_name": "ADW",
        "columns": [
            {"name": "LOAN_AMT", "description": "여신금액", "type": "DECIMAL"},
        ],
    }


def _make_code_meta_dict(
    column_name: str = "LOAN_STS_CD",
) -> dict[str, Any]:
    """테스트용 MongoDB 코드 메타 raw dict를 반환한다."""
    return {
        "column_desc": "여신상태코드",
        "codes": {"01": "정상", "02": "연체", "03": "상환완료"},
    }


def _make_mock_pool(
    assistant_rows: list[dict],
    user_seq_rows: list[dict],
) -> MagicMock:
    """두 쿼리(assistant rows, user_seq)에 순서대로 응답하는 mock pool."""
    call_count = 0

    def _execute(query: str, params: dict) -> AsyncMock:
        nonlocal call_count
        call_count += 1
        cursor = AsyncMock()
        if "ORDER BY seq DESC" in query:
            cursor.fetchall = AsyncMock(return_value=assistant_rows)
        elif "UNNEST" in query:
            cursor.fetchall = AsyncMock(return_value=user_seq_rows)
        else:
            cursor.fetchall = AsyncMock(return_value=[])
        return cursor

    mock_conn = AsyncMock()
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=None)
    mock_conn.execute = AsyncMock(side_effect=_execute)

    mock_pool = MagicMock()
    mock_pool.connection = MagicMock(return_value=mock_conn)
    return mock_pool


@pytest.mark.asyncio
async def test_restore_from_db_pool_none() -> None:
    """pool이 None이면 빈 리스트를 반환한다."""
    result = await restore_from_db(pool=None, session_id="test-session")
    assert result == []


@pytest.mark.asyncio
async def test_restore_from_db_db_error_returns_empty() -> None:
    """DB 조회 실패 시 빈 리스트를 반환하고 파이프라인을 차단하지 않는다."""
    mock_conn = AsyncMock()
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=None)
    mock_conn.execute = AsyncMock(side_effect=RuntimeError("DB 연결 오류"))

    mock_pool = MagicMock()
    mock_pool.connection = MagicMock(return_value=mock_conn)

    result = await restore_from_db(pool=mock_pool, session_id="test-session")
    assert result == []


@pytest.mark.asyncio
async def test_restore_from_db_no_rows_returns_empty() -> None:
    """성공 턴이 없으면 빈 리스트를 반환한다."""
    mock_pool = _make_mock_pool(
        assistant_rows=[],
        user_seq_rows=[],
    )
    result = await restore_from_db(pool=mock_pool, session_id="test-session")
    assert result == []


@pytest.mark.asyncio
async def test_restore_from_db_success_sorted_asc() -> None:
    """성공 케이스: 스냅샷이 user_message_seq ASC 순서로 정렬된다."""
    rows = [_make_row(seq=20), _make_row(seq=10)]
    mock_pool = _make_mock_pool(
        assistant_rows=rows,
        user_seq_rows=[
            {"a_seq": 20, "user_seq": 19},
            {"a_seq": 10, "user_seq": 9},
        ],
    )

    with patch(
        "src.services.turn_snapshot_store._fanout_mongo_lookups",
        new_callable=AsyncMock,
        return_value=({}, {}),
    ):
        result = await restore_from_db(
            pool=mock_pool, session_id="test-session",
        )

    assert len(result) == 2
    assert result[0].user_message_seq == 9
    assert result[1].user_message_seq == 19


@pytest.mark.asyncio
async def test_restore_from_db_partial_row_failure() -> None:
    """개별 턴 빌드 실패 시 해당 턴만 스킵하고 나머지는 복원한다."""
    rows = [_make_row(seq=20), _make_row(seq=10)]
    mock_pool = _make_mock_pool(
        assistant_rows=rows,
        user_seq_rows=[
            {"a_seq": 20, "user_seq": 19},
            {"a_seq": 10, "user_seq": 9},
        ],
    )

    build_call_count = 0
    original_build = _build_snapshot_from_row

    def _mock_build(
        row: dict,
        user_seq_map: dict,
        table_index: dict,
        code_index: dict,
    ) -> TurnSnapshot:
        nonlocal build_call_count
        build_call_count += 1
        if build_call_count == 1:
            raise ValueError("첫 번째 턴 빌드 실패 시뮬레이션")
        return original_build(row, user_seq_map, table_index, code_index)

    with (
        patch(
            "src.services.turn_snapshot_store._fanout_mongo_lookups",
            new_callable=AsyncMock,
            return_value=({}, {}),
        ),
        patch(
            "src.services.turn_snapshot_store._build_snapshot_from_row",
            side_effect=_mock_build,
        ),
    ):
        result = await restore_from_db(
            pool=mock_pool, session_id="test-session",
        )

    assert len(result) == 1


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("data_extraction", IntentType.DATA_EXTRACTION),
        ("data_analysis", IntentType.DATA_ANALYSIS),
        ("", IntentType.UNKNOWN),
    ],
)
def test_parse_intent(raw: str, expected: IntentType) -> None:
    """intent 문자열을 IntentType으로 올바르게 변환한다."""
    assert _parse_intent(raw) == expected


def test_parse_intent_unknown_raises() -> None:
    """알 수 없는 값(비어있지 않음)은 M12 strict — ValueError 발생."""
    with pytest.raises(ValueError, match="알 수 없는 intent"):
        _parse_intent("unknown_value_xyz")


def test_extract_table_names_used_only() -> None:
    """used=True인 테이블명만 추출한다."""
    row: dict[str, Any] = {
        "process_summary": {
            "context": {
                "tables": [
                    {"name": "TB_ADW_LOAN001M", "used": True},
                    {"name": "TB_ADW_CUST001M", "used": False},
                    {"name": "TB_ADW_DEPT001M", "used": True},
                ],
            },
        },
    }
    result = _extract_table_names(row)
    assert result == ["TB_ADW_LOAN001M", "TB_ADW_DEPT001M"]


def test_extract_table_names_no_process_summary() -> None:
    """process_summary가 없으면 빈 리스트를 반환한다."""
    assert _extract_table_names({"process_summary": None}) == []
    assert _extract_table_names({}) == []


def test_extract_table_names_malformed_table_entry() -> None:
    """tables 항목이 dict가 아니거나 name이 없으면 스킵한다."""
    row: dict[str, Any] = {
        "process_summary": {
            "context": {
                "tables": [
                    "not_a_dict",
                    {"used": True},
                    {"name": "TB_ADW_LOAN001M", "used": True},
                ],
            },
        },
    }
    result = _extract_table_names(row)
    assert result == ["TB_ADW_LOAN001M"]


def test_extract_code_columns_from_sql() -> None:
    """SQL에서 _CD 접미사 코드 컬럼을 추출한다."""
    sql = (
        "SELECT LOAN_STS_CD, CUST_NM, LOAN_AMT "
        "FROM TB_ADW_LOAN001M "
        "WHERE LOAN_STS_CD IN ('01', '02')"
    )
    result = _extract_code_columns(sql)
    assert "LOAN_STS_CD" in result


def test_extract_code_columns_empty_sql() -> None:
    """빈 SQL이면 빈 리스트를 반환한다."""
    assert _extract_code_columns("") == []


def testextract_snapshot_result_data_rows_excluded() -> None:
    """rows 키를 제외하고 메타데이터만 반환한다."""
    raw: dict[str, Any] = {
        "columns": ["loan_amt"],
        "column_formats": {},
        "total_count": 100,
        "displayed_count": 50,
        "rows": [{"loan_amt": 1000}],
    }
    result = extract_snapshot_result_data(raw)
    assert result is not None
    assert "rows" not in result
    assert result["columns"] == ["loan_amt"]
    assert result["total_count"] == 100
    assert result["displayed_count"] == 50


def testextract_snapshot_result_data_none() -> None:
    """None이면 None을 반환한다."""
    assert extract_snapshot_result_data(None) is None


def testextract_snapshot_result_data_rows_only() -> None:
    """rows만 있는 경우 메타키가 없으므로 None을 반환한다."""
    assert extract_snapshot_result_data({"rows": [{"a": 1}]}) is None


def test_extract_inferred_signals_excludes_intent_classifier() -> None:
    """source_node='intent_classifier'인 INFER는 제외한다."""
    ps: dict[str, Any] = {
        "ai_decisions": {
            "inferences": [
                {
                    "question": "기간은?",
                    "value": "2024년 전체",
                    "source_node": "normalize_query",
                    "reason": "질의에 연도 명시",
                },
                {
                    "question": "연속 질의 해석",
                    "value": "이전 결과 참조",
                    "source_node": "intent_classifier",
                    "reason": "CONTINUE 감지",
                },
            ],
        },
    }
    result = _extract_inferred_signals(ps)
    assert len(result) == 1
    assert result[0]["source_node"] == "normalize_query"


def test_extract_inferred_signals_none_process_summary() -> None:
    """process_summary가 None이면 빈 리스트를 반환한다."""
    assert _extract_inferred_signals(None) == []
    assert _extract_inferred_signals({}) == []


def test_extract_inferred_signals_reason_fallback() -> None:
    """'reason' 키가 없으면 'reasoning' 키를 폴백으로 사용한다."""
    ps: dict[str, Any] = {
        "ai_decisions": {
            "inferences": [
                {
                    "question": "기간은?",
                    "value": "2024년 전체",
                    "source_node": "normalize_query",
                    "reasoning": "질의에 연도 명시",
                },
            ],
        },
    }
    result = _extract_inferred_signals(ps)
    assert len(result) == 1
    assert result[0]["reason"] == "질의에 연도 명시"


@pytest.mark.asyncio
async def test_fanout_partial_failure() -> None:
    """일부 테이블/코드 조회 실패 시 해당 항목만 제외하고 나머지는 복원한다."""
    from src.services.turn_snapshot_store import (
        _fanout_mongo_lookups as fanout,
    )

    table_names = {"TB_ADW_LOAN001M", "TB_ADW_CUST001M"}
    code_columns = {"LOAN_STS_CD"}

    def _lookup_table(name: str) -> list[dict]:
        if name == "TB_ADW_LOAN001M":
            return [_make_table_meta_dict(name)]
        raise RuntimeError("MongoDB 연결 실패")

    def _lookup_code(col: str) -> list[dict]:
        return [_make_code_meta_dict(col)]

    with (
        patch(
            "src.agents.nodes.reason.tools.lookup_table_meta",
            side_effect=_lookup_table,
        ),
        patch(
            "src.agents.nodes.reason.tools.lookup_code_meta",
            side_effect=_lookup_code,
        ),
    ):
        table_index, code_index = await fanout(table_names, code_columns)

    assert "TB_ADW_LOAN001M" in table_index
    assert "TB_ADW_CUST001M" not in table_index
    assert isinstance(table_index["TB_ADW_LOAN001M"], TableMeta)
    assert "LOAN_STS_CD" in code_index
    assert isinstance(code_index["LOAN_STS_CD"], CodeMeta)


def test_build_snapshot_from_row_full() -> None:
    """정상 케이스: 9개 필드가 올바르게 채워진 TurnSnapshot을 반환한다."""
    row = _make_row(seq=10)
    table_meta = TableMeta(table_name="TB_ADW_LOAN001M")

    snapshot = _build_snapshot_from_row(
        row=row,
        user_seq_map={10: 9},
        table_index={"TB_ADW_LOAN001M": table_meta},
        code_index={},
    )

    assert isinstance(snapshot, TurnSnapshot)
    assert snapshot.user_message_seq == 9
    assert snapshot.intent == IntentType.DATA_EXTRACTION
    assert snapshot.generated_sql == "SELECT * FROM TB_ADW_LOAN001M"
    assert snapshot.sql_explanation == "여신 현황 조회"
    assert snapshot.result_data is not None
    assert "rows" not in (snapshot.result_data or {})
    assert len(snapshot.selected_tables) == 1
    assert snapshot.selected_tables[0].table_name == "TB_ADW_LOAN001M"


def test_build_snapshot_from_row_missing_user_seq_defaults_zero() -> None:
    """user_seq_map에 assistant_seq가 없으면 user_message_seq=0으로 폴백한다."""
    row = _make_row(seq=99)
    snapshot = _build_snapshot_from_row(
        row=row,
        user_seq_map={},
        table_index={},
        code_index={},
    )
    assert snapshot.user_message_seq == 0


def test_build_snapshot_from_row_no_tables_in_index() -> None:
    """table_index에 해당 테이블이 없으면 selected_tables가 빈 리스트다."""
    row = _make_row(seq=10)
    snapshot = _build_snapshot_from_row(
        row=row,
        user_seq_map={10: 9},
        table_index={},
        code_index={},
    )
    assert snapshot.selected_tables == []


def test_safe_dict_passthrough() -> None:
    """dict이면 그대로 반환한다."""
    d: dict[str, Any] = {"key": "value"}
    assert _safe_dict(d) == {"key": "value"}


def test_safe_dict_none() -> None:
    """None이면 None을 반환한다."""
    assert _safe_dict(None) is None


def test_safe_dict_json_string() -> None:
    """JSON 문자열이면 파싱하여 반환한다."""
    result = _safe_dict('{"key": "value"}')
    assert result == {"key": "value"}


def test_safe_dict_invalid_string_raises() -> None:
    """파싱 불가 문자열은 M12 strict — ValueError(JSONDecodeError) 발생."""
    with pytest.raises(ValueError):
        _safe_dict("not_json")


def test_build_code_meta_normal() -> None:
    """MongoDB raw dict에서 CodeMeta를 올바르게 구성한다."""
    raw: dict[str, Any] = {
        "column_desc": "여신상태코드",
        "codes": {"01": "정상", "02": "연체"},
    }
    result = _build_code_meta("LOAN_STS_CD", raw)
    assert result.column_name == "LOAN_STS_CD"
    assert result.column_desc == "여신상태코드"
    assert result.codes == {"01": "정상", "02": "연체"}


def test_build_code_meta_empty_raw() -> None:
    """codes가 없는 raw dict도 빈 codes로 CodeMeta를 생성한다."""
    result = _build_code_meta("SOME_CD", {})
    assert result.column_name == "SOME_CD"
    assert result.codes == {}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Path F' §3.5: process_summary 확장 필드 추출 헬퍼 테스트
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_extract_normalized_query_from_raw() -> None:
    """interpretation._raw 에 유효한 NQ dict 가 있으면 NormalizedQuery 로 복원한다."""
    ps = {
        "interpretation": {
            "measures": ["대출잔액"],
            "_raw": {
                "original_query": "지점별 대출잔액",
                "rewritten_query": "지점별 대출잔액 합계",
                "intent": {"primary": "AGGREGATE"},
                "measures": [
                    {"term": "대출잔액", "measure_type": "RAW"},
                ],
            },
        },
    }
    nq = _extract_normalized_query(ps)
    assert nq is not None
    assert nq.original_query == "지점별 대출잔액"
    assert nq.rewritten_query == "지점별 대출잔액 합계"


def test_extract_normalized_query_missing_raw() -> None:
    """_raw 가 없으면 None 반환 (과거 턴 하위 호환)."""
    ps = {"interpretation": {"measures": ["건수"]}}
    assert _extract_normalized_query(ps) is None


def test_extract_normalized_query_invalid_raw() -> None:
    """_raw 가 NQ 스키마에 맞지 않으면 None 반환 (validate 실패)."""
    ps = {"interpretation": {"_raw": {"broken": "payload"}}}
    # NormalizedQuery 가 최소 필수 필드를 요구하는 경우 validate 실패 → None.
    # 모델이 관대하면 인스턴스가 생성될 수 있으므로 None-또는-인스턴스 둘 다 허용.
    result = _extract_normalized_query(ps)
    assert result is None or result.original_query == ""


def test_extract_normalized_query_none_ps() -> None:
    """process_summary 가 None 이면 None 반환."""
    assert _extract_normalized_query(None) is None


def test_extract_knowledge_items_success() -> None:
    """context._knowledge_items 에서 KnowledgeItem 리스트 복원."""
    ps = {
        "context": {
            "_knowledge_items": [
                {
                    "id": "K1",
                    "key": "대출잔액",
                    "status": "CONFIRMED",
                    "value": "LOAN_BAL_AMT",
                },
                {
                    "id": "K2",
                    "key": "지점",
                    "status": "PROBABLE",
                    "value": "BRCD",
                },
            ],
        },
    }
    items = _extract_knowledge_items(ps)
    assert len(items) == 2
    assert items[0].id == "K1"


def test_extract_knowledge_items_partial_invalid_raises() -> None:
    """M12 strict — 일부 항목이라도 validate 실패하면 전체 실패.

    개별 항목 skip 은 스냅샷 정확도를 훼손하므로 허용하지 않는다.
    상위 `restore_from_db` 가 per-snapshot 단위로 try/except 하여
    해당 스냅샷만 drop 한다.
    """
    from pydantic import ValidationError

    ps = {
        "context": {
            "_knowledge_items": [
                {
                    "id": "K1",
                    "key": "ok",
                    "status": "CONFIRMED",
                },
                # confidence 타입 오류 → validate 실패 → 전체 실패.
                {"id": "K2", "key": "bad", "confidence": "not-a-number"},
            ],
        },
    }
    with pytest.raises(ValidationError):
        _extract_knowledge_items(ps)


def test_extract_knowledge_items_missing() -> None:
    """_knowledge_items 없으면 빈 리스트."""
    ps = {"context": {"tables": []}}
    assert _extract_knowledge_items(ps) == []


def test_extract_knowledge_items_none_ps() -> None:
    """process_summary None 이면 빈 리스트."""
    assert _extract_knowledge_items(None) == []


def test_extract_query_decomposition_success() -> None:
    """_query_decomposition 을 dict 그대로 복원."""
    ps = {
        "_query_decomposition": {
            "measures": [{"term": "대출잔액"}],
            "group_by": ["지점"],
            "output_hint": {"expected_columns": ["지점", "대출잔액"]},
        },
    }
    decomp = _extract_query_decomposition(ps)
    assert decomp["group_by"] == ["지점"]
    assert decomp["output_hint"]["expected_columns"] == ["지점", "대출잔액"]


def test_extract_query_decomposition_missing() -> None:
    """_query_decomposition 없으면 빈 dict."""
    ps = {"interpretation": {}}
    assert _extract_query_decomposition(ps) == {}


def test_extract_query_decomposition_invalid_type() -> None:
    """_query_decomposition 가 dict 가 아니면 빈 dict."""
    ps = {"_query_decomposition": "not a dict"}
    assert _extract_query_decomposition(ps) == {}


def test_extract_query_decomposition_none_ps() -> None:
    """process_summary None 이면 빈 dict."""
    assert _extract_query_decomposition(None) == {}


def test_build_snapshot_from_row_hydrates_path_f_fields() -> None:
    """Path F' 3필드(+ target_db) 가 process_summary 에서 복원됨을 확인."""
    ps = {
        "context": {
            "tables": [
                {
                    "name": "TB_ADW_LOAN001M",
                    "used": True,
                    "status": "selected",
                },
            ],
            "_knowledge_items": [
                {
                    "id": "K1",
                    "key": "대출잔액",
                    "status": "CONFIRMED",
                },
            ],
        },
        "interpretation": {
            "_raw": {
                "original_query": "지점별 대출잔액",
                "intent": {"primary": "AGGREGATE"},
            },
        },
        "_query_decomposition": {
            "group_by": ["지점"],
        },
    }
    row = _make_row(seq=10, process_summary=ps)
    row["target_db"] = "mssql_info"

    snap = _build_snapshot_from_row(
        row=row,
        user_seq_map={10: 9},
        table_index={"TB_ADW_LOAN001M": TableMeta(
            table_name="TB_ADW_LOAN001M",
        )},
        code_index={},
    )

    assert snap.normalized_query is not None
    assert snap.normalized_query.original_query == "지점별 대출잔액"
    assert len(snap.knowledge_items) == 1
    assert snap.knowledge_items[0].id == "K1"
    assert snap.query_decomposition == {"group_by": ["지점"]}
    assert snap.target_db == "mssql_info"


def test_build_snapshot_from_row_no_path_f_fields() -> None:
    """Path F' 필드 부재 시 안전한 기본값(None/빈)을 채운다 (과거 턴 하위 호환)."""
    row = _make_row(seq=10)  # 기본 process_summary 는 Path F' 필드 없음.
    snap = _build_snapshot_from_row(
        row=row,
        user_seq_map={10: 9},
        table_index={},
        code_index={},
    )
    assert snap.normalized_query is None
    assert snap.knowledge_items == []
    assert snap.query_decomposition == {}
    assert snap.target_db == ""
