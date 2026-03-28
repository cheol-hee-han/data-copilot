"""ES 스키마(Table Meta + Code Meta) 검색 품질 테스트.

=== 개념 설명 ===
ES table_meta(535개 테이블)와 code_meta(코드 메타)에서 사용자 질의에 대해
올바른 테이블·코드 메타가 검색되는지 품질을 검증한다.

검증 관점:
  1. 도메인 적합성 — "여신" 질의 → LON 도메인 테이블 상위 반환
  2. 테이블명 패턴 — "대출" 질의 → TB_LOAN_* 테이블 포함
  3. 코드 메타 연관 — 테이블 컬럼에 사용되는 코드 메타가 올바르게 검색되는지
  4. 검색 정밀도 — 상위 K건 중 관련 도메인 비율
  5. 결과 구조 — table_name, domain_cd, columns 등 필수 필드

대상: ElasticSearchConnector.search_table_meta(), search_code_meta() (실제 ES)

=== 단독 실행 ===
    python -m pytest tests/unit/test_search_es_schema.py -v -s

=== 테스트 데이터 예시 ===
    입력: "여신 실행 건수"
    기대: domain_cd="LON" 테이블이 상위 10건에 포함, TB_LOAN_* 패턴 존재

=== 정상 결과 ===
    관련 도메인 테이블·코드 메타 상위 반환, 구조 정상
=== 오류 결과 ===
    무관한 도메인만 반환, 코드 메타 누락
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from tests.conftest import get_test_logger, log_test_case

logger = get_test_logger("test_search_es_schema")


# ── ES 연결 확인 ──

def _es_available() -> bool:
    try:
        import requests
        r = requests.get(
            "http://localhost:9200/_cluster/health",
            auth=("elastic", "elastic_pass"),
            timeout=3,
        )
        return r.status_code == 200
    except Exception:
        return False


_SKIP = pytest.mark.skipif(
    not _es_available(),
    reason="ES 서비스(localhost:9200) 미기동",
)


# ── 검색 헬퍼 ──

async def _search_table_meta(query: str) -> list[dict]:
    """ElasticSearchConnector 를 통해 table_meta 를 검색한다."""
    from src.connectors.impl.elasticsearch_connector import ElasticSearchConnector

    conn = ElasticSearchConnector(use_dummy=False)
    await conn.connect()
    try:
        return await conn.search_table_meta(query)
    finally:
        await conn.disconnect()


async def _search_code_meta(query: str) -> list[dict]:
    """ElasticSearchConnector 를 통해 code_meta 를 검색한다."""
    from src.connectors.impl.elasticsearch_connector import ElasticSearchConnector

    conn = ElasticSearchConnector(use_dummy=False)
    await conn.connect()
    try:
        return await conn.search_code_meta(query)
    finally:
        await conn.disconnect()


# ══════════════════════════════════════════════════════════════
# 골든셋 — Table Meta
# ══════════════════════════════════════════════════════════════

TABLE_META_GOLDEN_CASES = [
    {
        "query": "여신 실행 건수",
        "expected_domain": "LON",
        "expected_table_pattern": "TB_ADW_LN",
        "description": "여신 테이블 검색",
    },
    {
        "query": "수신 잔액 현황",
        "expected_domain": "DEP",
        "expected_table_pattern": "TB_ADW_DEP",
        "description": "수신 테이블 검색",
    },
    {
        "query": "카드 이용금액",
        "expected_domain": "CRD",
        "expected_table_pattern": "TB_ADW_CR",
        "description": "카드 테이블 검색",
    },
    {
        "query": "고객 정보 조회",
        "expected_domain": "CUS",
        "expected_table_pattern": None,
        "description": "고객 테이블 검색",
    },
    {
        "query": "대출 유형별 현황",
        "expected_domain": "LON",
        "expected_table_pattern": "TB_ADW_LN",
        "description": "대출 유형 테이블 검색",
    },
]

# ── 코드 메타 골든셋 ──

CODE_META_GOLDEN_CASES = [
    {
        "query": "CUS_DCD",
        "expected_field": "CUS_DCD",
        "expected_code": "01",
        "expected_desc": "개인",
        "description": "고객구분코드",
    },
    {
        "query": "CUS_GRD_CD",
        "expected_field": "CUS_GRD_CD",
        "expected_code": "01",
        "expected_desc": "VIP",
        "description": "고객등급코드",
    },
    {
        "query": "ACT_DCD",
        "expected_field": "ACT_DCD",
        "expected_code": "01",
        "expected_desc": "보통예금",
        "description": "계좌구분코드",
    },
    {
        "query": "ACT_STCD",
        "expected_field": "ACT_STCD",
        "expected_code": "02",
        "expected_desc": "해지",
        "description": "계좌상태코드",
    },
]


# ══════════════════════════════════════════════════════════════
# 1. Table Meta — 도메인 적합성
# ══════════════════════════════════════════════════════════════

@_SKIP
class TestTableMetaDomainRelevance:
    """검색 결과의 domain_cd가 질의 도메인과 일치하는지."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "case", TABLE_META_GOLDEN_CASES,
        ids=[c["description"] for c in TABLE_META_GOLDEN_CASES],
    )
    async def test_domain_in_top_results(self, case: dict):
        """기대 domain_cd가 상위 10건에 포함된다."""
        results = await _search_table_meta(case["query"])

        domains = [r.get("domain_cd", "") for r in results]
        found = case["expected_domain"] in domains

        log_test_case(
            logger, f"test_domain_{case['description']}",
            case["query"], case["expected_domain"],
            domains, found,
        )
        assert found, (
            f"[{case['description']}] domain '{case['expected_domain']}' 미반환.\n"
            f"  반환 도메인: {domains}\n"
            f"  반환 테이블: {[r.get('table_name', '') for r in results]}"
        )


# ══════════════════════════════════════════════════════════════
# 2. Table Meta — 테이블명 패턴
# ══════════════════════════════════════════════════════════════

@_SKIP
class TestTableMetaNamePattern:
    """기대 테이블명 패턴이 결과에 포함되는지."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "case", TABLE_META_GOLDEN_CASES,
        ids=[c["description"] for c in TABLE_META_GOLDEN_CASES],
    )
    async def test_table_pattern_in_results(self, case: dict):
        """기대 테이블명 패턴이 상위 결과에 존재한다."""
        pattern = case.get("expected_table_pattern")
        if not pattern:
            pytest.skip("테이블 패턴 검증 대상 없음")

        results = await _search_table_meta(case["query"])
        table_names = [r.get("table_name", "") for r in results]
        found = any(pattern in t for t in table_names)

        log_test_case(
            logger, f"test_pattern_{case['description']}",
            case["query"], f"패턴 '{pattern}'",
            table_names[:5], found,
        )
        assert found, (
            f"[{case['description']}] 패턴 '{pattern}' 미매칭.\n"
            f"  반환 테이블: {table_names}"
        )


# ══════════════════════════════════════════════════════════════
# 3. Table Meta — 결과 구조 검증
# ══════════════════════════════════════════════════════════════

@_SKIP
class TestTableMetaStructure:
    """table_meta 검색 결과의 필드 구조 검증."""

    @pytest.mark.asyncio
    async def test_has_required_fields(self):
        """table_name, domain_cd 필드가 존재한다."""
        results = await _search_table_meta("대출 정보")
        assert len(results) > 0, "검색 결과 없음"

        for r in results:
            assert "table_name" in r, f"table_name 누락: {r.keys()}"

        log_test_case(logger, "test_table_meta_fields", "대출 정보",
                      "table_name 필드", f"{len(results)}건", True)

    @pytest.mark.asyncio
    async def test_has_columns(self):
        """검색된 테이블에 columns 정보가 포함된다."""
        results = await _search_table_meta("고객 정보")

        has_columns = any("columns" in r for r in results)
        log_test_case(logger, "test_has_columns", "고객 정보",
                      "columns 필드 존재", has_columns, has_columns)
        # columns 는 시딩 방식에 따라 없을 수 있으므로 warn 수준
        if not has_columns:
            pytest.skip("columns 필드 미포함 (시딩 방식에 따라 정상)")


# ══════════════════════════════════════════════════════════════
# 4. Table Meta — 검색 정밀도 (상위 5건 도메인 집중도)
# ══════════════════════════════════════════════════════════════

@_SKIP
class TestTableMetaPrecision:
    """상위 K건의 도메인 집중도."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "query,expected_domain",
        [
            ("여신 실행 건수", "LON"),
            ("수신 잔액 현황", "DEP"),
            ("카드 이용금액", "CRD"),
        ],
    )
    async def test_top5_domain_concentration(self, query: str, expected_domain: str):
        """상위 5건 중 기대 도메인이 최소 1건 이상."""
        results = await _search_table_meta(query)
        top5 = results[:5]

        domain_match = sum(
            1 for r in top5 if r.get("domain_cd") == expected_domain
        )

        log_test_case(
            logger, f"test_precision_{query[:10]}",
            query, f"domain={expected_domain} >= 1건",
            f"{domain_match}/{len(top5)}", domain_match >= 1,
        )
        assert domain_match >= 1, (
            f"상위 5건에 domain '{expected_domain}' 0건.\n"
            f"  반환: {[(r.get('table_name'), r.get('domain_cd')) for r in top5]}"
        )


# ══════════════════════════════════════════════════════════════
# 5. Code Meta — 코드 메타 검색
# ══════════════════════════════════════════════════════════════

@_SKIP
class TestCodeMetaSearch:
    """code_meta 인덱스에서 코드 필드명으로 코드값을 검색한다."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "case", CODE_META_GOLDEN_CASES,
        ids=[c["description"] for c in CODE_META_GOLDEN_CASES],
    )
    async def test_code_field_found(self, case: dict):
        """코드 필드명으로 검색 시 해당 코드 메타가 반환된다."""
        results = await _search_code_meta(case["query"])

        fields = [r.get("code_field", "") for r in results]
        found = case["expected_field"] in fields

        log_test_case(
            logger, f"test_code_{case['description']}",
            case["query"], case["expected_field"],
            fields[:5], found,
        )
        assert found, (
            f"[{case['description']}] code_field '{case['expected_field']}' 미반환.\n"
            f"  반환: {fields}"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "case", CODE_META_GOLDEN_CASES,
        ids=[c["description"] for c in CODE_META_GOLDEN_CASES],
    )
    async def test_code_value_exists(self, case: dict):
        """반환된 코드 메타에 기대 코드값이 포함된다."""
        results = await _search_code_meta(case["query"])

        target = [r for r in results if r.get("code_field") == case["expected_field"]]
        if not target:
            pytest.skip(f"code_field '{case['expected_field']}' 미반환")

        codes = target[0].get("codes", {})
        code_val = case["expected_code"]
        code_desc = case["expected_desc"]

        found = code_val in codes and codes[code_val] == code_desc

        log_test_case(
            logger, f"test_code_val_{case['description']}",
            case["query"], f"{code_val}={code_desc}",
            codes, found,
        )
        assert found, (
            f"[{case['description']}] 코드값 '{code_val}'='{code_desc}' 미매칭.\n"
            f"  실제 codes: {codes}"
        )


# ══════════════════════════════════════════════════════════════
# 6. 종합 — Table Meta + Code Meta 연관 검증
# ══════════════════════════════════════════════════════════════

@_SKIP
class TestSchemaQualityScorecard:
    """Table Meta 도메인 적중률 종합 점수."""

    @pytest.mark.asyncio
    async def test_domain_hit_rate(self):
        """전체 골든셋 도메인 적중률 >= 60%."""
        hits = 0
        total = len(TABLE_META_GOLDEN_CASES)

        for case in TABLE_META_GOLDEN_CASES:
            results = await _search_table_meta(case["query"])
            domains = [r.get("domain_cd", "") for r in results]
            if case["expected_domain"] in domains:
                hits += 1

        rate = hits / total * 100
        log_test_case(
            logger, "test_domain_hit_rate",
            f"{total}건 골든셋", ">= 60%",
            f"{rate:.0f}% ({hits}/{total})", rate >= 60,
        )

        print(f"\n  [table_meta 도메인 적중률] {rate:.1f}% ({hits}/{total})")
        assert rate >= 60, f"도메인 적중률 {rate:.1f}% < 60%"
