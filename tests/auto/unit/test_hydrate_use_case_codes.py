"""use_case hydration 및 enrichment 코드 적재 단위 테스트.

테스트 대상:
    - _hydrate_use_cases_from_raw: _point_id 기반 UseCaseEntry 적재
    - _hydrate_enrichment: SELECTED use_case의 enrichment_codes → code_map 적재
    - _enrich_use_cases: use_case SQL에서 코드 컬럼 추출 및 매핑

버그 배경:
    Qdrant 반환 dict에 "id" 필드가 없고 "_point_id"만 존재하는데,
    hydration 함수들이 get("id")로 접근하여 항상 ""을 반환 → 전부 skip.
    fix: get("id") → get("_point_id")

실행:
    pytest tests/auto/unit/test_hydrate_use_case_codes.py -v
"""

from __future__ import annotations

import pytest

from src.agents.state.state import (
    CodeMeta,
    ExecutionStep,
    SelectionStatus,
    StepStatus,
    UseCaseEntry,
)
from src.agents.nodes.reason.context_interpreter import (
    _hydrate_use_cases_from_raw,
    _hydrate_enrichment,
    _collect_selected_use_case_ids,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 테스트 헬퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _qdrant_use_case(
    point_id: str | int,
    sql: str = "SELECT 1",
    description: str = "테스트 SQL",
    domain: str = "LN",
    enrichment_codes: dict | None = None,
    enrichment_tables: list | None = None,
) -> dict:
    """Qdrant 반환 형식의 use_case dict를 생성한다.

    실제 Qdrant connector는 payload에 _point_id, _score를 추가하며
    "id" 필드는 포함하지 않는다.
    """
    return {
        "sql": sql,
        "description": description,
        "domain": domain,
        "tables_used": [],
        "complexity": "simple",
        "_score": 0.85,
        "_point_id": str(point_id),
        "similarity": 0.85,
        "enrichment_codes": enrichment_codes or {},
        "enrichment_tables": enrichment_tables or [],
    }


def _step_with_use_cases(
    use_cases: list[dict],
    step_num: int = 1,
) -> ExecutionStep:
    """search_use_cases 완료 스텝을 생성한다."""
    return ExecutionStep(
        step=step_num,
        tool="search_use_cases",
        input="테스트 쿼리",
        purpose="유사 SQL 조회",
        status=StepStatus.DONE,
        raw_result={"use_cases": use_cases},
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _hydrate_use_cases_from_raw
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestHydrateUseCasesFromRaw:
    """_hydrate_use_cases_from_raw: _point_id 기반 UseCaseEntry 적재."""

    def test_point_id_기반_적재(self):
        """_point_id가 있는 use_case는 정상 적재된다."""
        raw = {"use_cases": [
            _qdrant_use_case(4998, description="여신 조회"),
            _qdrant_use_case(4050, description="담보대출 조회"),
        ]}
        explored: list[UseCaseEntry] = []
        existing_ids: set[str] = set()

        _hydrate_use_cases_from_raw(raw, explored, existing_ids)

        assert len(explored) == 2
        assert explored[0].id == "4998"
        assert explored[0].point_id == "4998"
        assert explored[0].description == "여신 조회"
        assert explored[1].id == "4050"

    def test_중복_point_id_방지(self):
        """동일 _point_id는 한 번만 적재된다."""
        raw = {"use_cases": [
            _qdrant_use_case(4998),
            _qdrant_use_case(4998),
        ]}
        explored: list[UseCaseEntry] = []
        existing_ids: set[str] = set()

        _hydrate_use_cases_from_raw(raw, explored, existing_ids)

        assert len(explored) == 1

    def test_이미_존재하는_id_skip(self):
        """existing_ids에 이미 있는 use_case는 skip."""
        raw = {"use_cases": [_qdrant_use_case(4998)]}
        explored: list[UseCaseEntry] = []
        existing_ids: set[str] = {"4998"}

        _hydrate_use_cases_from_raw(raw, explored, existing_ids)

        assert len(explored) == 0

    def test_point_id_없으면_skip(self):
        """_point_id가 없는 use_case는 skip."""
        raw = {"use_cases": [{"sql": "SELECT 1", "description": "no id"}]}
        explored: list[UseCaseEntry] = []
        existing_ids: set[str] = set()

        _hydrate_use_cases_from_raw(raw, explored, existing_ids)

        assert len(explored) == 0

    def test_score와_domain_정상_적재(self):
        """score, domain 등 메타 필드가 정상 적재된다."""
        uc = _qdrant_use_case(100, domain="CUS")
        uc["score"] = 0.92
        raw = {"use_cases": [uc]}
        explored: list[UseCaseEntry] = []

        _hydrate_use_cases_from_raw(raw, explored, set())

        assert explored[0].domain == "CUS"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _hydrate_enrichment
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestHydrateEnrichment:
    """_hydrate_enrichment: SELECTED use_case의 enrichment_codes → code_map."""

    def test_selected_use_case_코드_적재(self):
        """SELECTED use_case의 enrichment_codes가 code_map에 적재된다."""
        uc = _qdrant_use_case(
            4998,
            enrichment_codes={
                "LN_DCD": {
                    "column_name": "LN_DCD",
                    "column_desc": "여신구분코드",
                    "codes": {"01": "신용대출", "02": "담보대출"},
                },
            },
        )
        step = _step_with_use_cases([uc])
        code_map: dict[str, CodeMeta] = {}
        selected_uc_ids = {"4998"}

        _hydrate_enrichment(
            [step], [], selected_uc_ids, code_map,
        )

        assert "LN_DCD" in code_map
        assert code_map["LN_DCD"].column_desc == "여신구분코드"
        assert code_map["LN_DCD"].codes["02"] == "담보대출"

    def test_미선정_use_case_코드_skip(self):
        """SELECTED가 아닌 use_case의 enrichment_codes는 적재하지 않는다."""
        uc = _qdrant_use_case(
            9999,
            enrichment_codes={
                "LN_DCD": {
                    "column_name": "LN_DCD",
                    "column_desc": "여신구분코드",
                    "codes": {"01": "신용대출"},
                },
            },
        )
        step = _step_with_use_cases([uc])
        code_map: dict[str, CodeMeta] = {}
        selected_uc_ids = {"4998"}  # 9999는 미선정

        _hydrate_enrichment(
            [step], [], selected_uc_ids, code_map,
        )

        assert len(code_map) == 0

    def test_다수_use_case_선택적_적재(self):
        """여러 use_case 중 SELECTED만 코드가 적재된다."""
        uc_selected = _qdrant_use_case(
            4998,
            enrichment_codes={
                "LN_DCD": {
                    "column_name": "LN_DCD",
                    "column_desc": "여신구분코드",
                    "codes": {"01": "신용", "02": "담보"},
                },
            },
        )
        uc_not_selected = _qdrant_use_case(
            9999,
            enrichment_codes={
                "CUS_DCD": {
                    "column_name": "CUS_DCD",
                    "column_desc": "고객구분코드",
                    "codes": {"P": "개인", "C": "법인"},
                },
            },
        )
        step = _step_with_use_cases([uc_selected, uc_not_selected])
        code_map: dict[str, CodeMeta] = {}
        selected_uc_ids = {"4998"}

        _hydrate_enrichment(
            [step], [], selected_uc_ids, code_map,
        )

        assert "LN_DCD" in code_map
        assert "CUS_DCD" not in code_map

    def test_enrichment_없는_use_case(self):
        """enrichment_codes가 비어있는 SELECTED use_case — 에러 없이 통과."""
        uc = _qdrant_use_case(4998, enrichment_codes={})
        step = _step_with_use_cases([uc])
        code_map: dict[str, CodeMeta] = {}

        _hydrate_enrichment(
            [step], [], {"4998"}, code_map,
        )

        assert len(code_map) == 0

    def test_point_id_없는_use_case_skip(self):
        """_point_id가 없는 use_case는 skip된다."""
        uc = {
            "sql": "SELECT 1",
            "enrichment_codes": {
                "LN_DCD": {
                    "column_name": "LN_DCD",
                    "column_desc": "여신구분코드",
                    "codes": {"01": "신용"},
                },
            },
        }
        step = _step_with_use_cases([uc])
        code_map: dict[str, CodeMeta] = {}

        _hydrate_enrichment(
            [step], [], {"4998"}, code_map,
        )

        assert len(code_map) == 0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _collect_selected_use_case_ids
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestCollectSelectedUseCaseIds:
    """_collect_selected_use_case_ids: LLM 응답에서 SELECTED sql_id 수집."""

    def test_selected_ids_수집(self):
        """SELECTED 상태의 sql_id만 수집한다."""
        interpretations = [
            {
                "explored_use_cases": [
                    {"sql_id": "4998", "status": "SELECTED"},
                    {"sql_id": "4050", "status": "SELECTED"},
                    {"sql_id": "100", "status": "REFERENCE"},
                ],
            },
        ]
        result = _collect_selected_use_case_ids(interpretations)

        assert result == {"4998", "4050"}

    def test_빈_응답(self):
        """explored_use_cases가 비어있으면 빈 set 반환."""
        result = _collect_selected_use_case_ids([{}])
        assert result == set()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 통합: hydrate → judgment → enrichment 전체 흐름
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestEndToEndCodeHydration:
    """use_case 적재 → 판정 → enrichment 코드 적재 전체 흐름."""

    def test_전체_흐름_explored_codes_정상_적재(self):
        """Qdrant use_case → hydrate → selected → enrichment → code_map 적재."""
        # 1. Qdrant 형식 use_case (enrichment 포함)
        uc = _qdrant_use_case(
            4998,
            sql="SELECT * FROM TB WHERE LN_DCD = '02'",
            enrichment_codes={
                "LN_DCD": {
                    "column_name": "LN_DCD",
                    "column_desc": "여신구분코드",
                    "codes": {"01": "신용대출", "02": "담보대출", "03": "보증대출"},
                },
            },
        )
        step = _step_with_use_cases([uc])

        # 2. hydrate — use_case를 explored_use_cases에 적재
        explored_use_cases: list[UseCaseEntry] = []
        _hydrate_use_cases_from_raw(
            step.raw_result, explored_use_cases, set(),
        )
        assert len(explored_use_cases) == 1
        assert explored_use_cases[0].id == "4998"

        # 3. LLM 판정 결과에서 selected_uc_ids 수집
        interpretations = [
            {
                "explored_use_cases": [
                    {"sql_id": "4998", "status": "SELECTED", "reason": "관련 SQL"},
                ],
            },
        ]
        selected_uc_ids = _collect_selected_use_case_ids(interpretations)
        assert "4998" in selected_uc_ids

        # 4. enrichment 코드 적재
        code_map: dict[str, CodeMeta] = {}
        _hydrate_enrichment(
            [step], [], selected_uc_ids, code_map,
        )

        # 5. 검증 — explored_codes에 코드가 정상 적재됨
        assert "LN_DCD" in code_map
        assert code_map["LN_DCD"].codes == {
            "01": "신용대출",
            "02": "담보대출",
            "03": "보증대출",
        }
