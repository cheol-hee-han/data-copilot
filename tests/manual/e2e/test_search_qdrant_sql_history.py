"""Qdrant SQL History(sql_history) 검색 품질 테스트.

=== 개념 설명 ===
Qdrant sql_history 컬렉션(10,000+건)에서 사용자 질의에 대해
의미적으로 유사한 과거 SQL이 올바르게 검색되는지 품질을 검증한다.

검증 관점:
  1. 도메인 적합성 — "대출 잔액" 질의 → 대출 관련 SQL 반환
  2. 하이브리드 검색 품질 — Dense+Sparse RRF 결과의 관련성
  3. 결과 구조 — payload에 sql, description 필드 존재
  4. Reranker 통합 — search_context_assembler 경유 시 재순위 결과 품질

대상: QdrantConnector.search_sql_history() (실제 Qdrant 서비스)

=== 단독 실행 ===
    python -m pytest tests/unit/test_search_qdrant_sql_history.py -v -s

=== 테스트 데이터 예시 ===
    입력: "부서별 대출건수 집계"
    기대: description에 "대출" 또는 "여신" 포함, sql에 SELECT 포함

=== 정상 결과 ===
    관련 SQL이 top-K 에 포함, payload 구조 정상
=== 오류 결과 ===
    무관한 SQL만 반환, 빈 결과, sql 필드 누락
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from tests.conftest import get_test_logger, log_test_case

logger = get_test_logger("test_search_qdrant_sql_history")


# ── Qdrant 연결 확인 ──

def _qdrant_available() -> bool:
    try:
        import requests
        r = requests.get("http://localhost:6333/healthz", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


_SKIP = pytest.mark.skipif(
    not _qdrant_available(),
    reason="Qdrant 서비스(localhost:6333) 미기동",
)


# ── 검색 헬퍼 ──

async def _search_sql_history(query: str, prefetch: int = 50) -> list[dict]:
    """QdrantConnector 를 통해 sql_history 를 하이브리드 검색한다."""
    from src.connectors.impl.qdrant_connector import QdrantConnector

    conn = QdrantConnector(use_dummy=False)
    await conn.connect()
    try:
        return await conn.search_sql_history(query, prefetch_limit=prefetch)
    finally:
        await conn.disconnect()


# ══════════════════════════════════════════════════════════════
# 골든셋 — 질의 + 기대 키워드
# ══════════════════════════════════════════════════════════════

SQL_HISTORY_GOLDEN_CASES = [
    {
        "query": "부서별 대출건수 및 총잔액 집계",
        "expected_keyword_in_desc": "대출",
        "expected_keyword_in_sql": "TB_LOAN",
        "description": "대출 건수 집계",
    },
    {
        "query": "고객 등급별 예금 평균 잔액",
        "expected_keyword_in_desc": "등급",
        "expected_keyword_in_sql": "AVG",
        "description": "등급별 예금 평균",
    },
    {
        "query": "월별 신규 고객 수 추이",
        "expected_keyword_in_desc": "고객",
        "expected_keyword_in_sql": "COUNT",
        "description": "신규 고객 추이",
    },
    {
        "query": "연체 대출 현황",
        "expected_keyword_in_desc": "연체",
        "expected_keyword_in_sql": "OVERDUE",
        "description": "연체 현황",
    },
    {
        "query": "지점별 수신 잔액 합계",
        "expected_keyword_in_desc": "잔액",
        "expected_keyword_in_sql": "SUM",
        "description": "지점별 수신 잔액",
    },
    {
        "query": "카드 이용금액 상위 고객",
        "expected_keyword_in_desc": "카드",
        "expected_keyword_in_sql": "TB_CARD",
        "description": "카드 이용 상위",
    },
]


# ══════════════════════════════════════════════════════════════
# 1. 도메인 적합성 — description 검증
# ══════════════════════════════════════════════════════════════

@_SKIP
class TestSqlHistoryDescriptionRelevance:
    """검색된 SQL의 description이 질의 도메인과 관련 있는지."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "case", SQL_HISTORY_GOLDEN_CASES,
        ids=[c["description"] for c in SQL_HISTORY_GOLDEN_CASES],
    )
    async def test_keyword_in_description(self, case: dict):
        """상위 결과의 description에 기대 키워드가 포함된다."""
        results = await _search_sql_history(case["query"], prefetch=20)

        kw = case["expected_keyword_in_desc"]
        all_desc = " ".join(
            r.get("description", "") for r in results[:10]
        ).lower()
        found = kw.lower() in all_desc

        log_test_case(
            logger, f"test_desc_{case['description']}",
            case["query"], f"description에 '{kw}' 포함",
            f"{'포함' if found else '미포함'} ({len(results)}건)", found,
        )
        assert found, (
            f"[{case['description']}] description에 '{kw}' 미포함.\n"
            f"  상위 descriptions: {[r.get('description', '')[:50] for r in results[:5]]}"
        )


# ══════════════════════════════════════════════════════════════
# 2. SQL 내용 적합성
# ══════════════════════════════════════════════════════════════

@_SKIP
class TestSqlHistorySqlRelevance:
    """검색된 SQL 본문이 질의 의도에 맞는지."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "case", SQL_HISTORY_GOLDEN_CASES,
        ids=[c["description"] for c in SQL_HISTORY_GOLDEN_CASES],
    )
    async def test_keyword_in_sql(self, case: dict):
        """상위 결과의 SQL에 기대 키워드(테이블명/함수)가 포함된다."""
        results = await _search_sql_history(case["query"], prefetch=20)

        kw = case["expected_keyword_in_sql"]
        all_sql = " ".join(
            r.get("sql", "") for r in results[:10]
        ).upper()
        found = kw.upper() in all_sql

        log_test_case(
            logger, f"test_sql_{case['description']}",
            case["query"], f"SQL에 '{kw}' 포함",
            f"{'포함' if found else '미포함'} ({len(results)}건)", found,
        )
        assert found, (
            f"[{case['description']}] SQL에 '{kw}' 미포함.\n"
            f"  상위 SQL: {[r.get('sql', '')[:60] for r in results[:3]]}"
        )


# ══════════════════════════════════════════════════════════════
# 3. 결과 구조 검증
# ══════════════════════════════════════════════════════════════

@_SKIP
class TestSqlHistoryPayloadStructure:
    """반환된 payload 의 필드 구조가 올바른지."""

    @pytest.mark.asyncio
    async def test_payload_has_sql_and_description(self):
        """payload에 sql, description 필드가 존재한다."""
        results = await _search_sql_history("대출 잔액 조회", prefetch=10)

        assert len(results) > 0, "검색 결과 없음"
        for r in results[:5]:
            assert "sql" in r, f"sql 필드 누락: {r.keys()}"
            assert "description" in r, f"description 필드 누락: {r.keys()}"

        log_test_case(logger, "test_payload_fields", "대출 잔액 조회",
                      "sql, description", f"{len(results)}건 모두 정상", True)

    @pytest.mark.asyncio
    async def test_sql_is_select(self):
        """반환된 SQL이 SELECT 문이다."""
        results = await _search_sql_history("고객 수 조회", prefetch=10)

        for r in results[:5]:
            sql = r.get("sql", "").strip().upper()
            assert sql.startswith("SELECT") or sql.startswith("WITH"), (
                f"SELECT가 아닌 SQL: {sql[:60]}"
            )

        log_test_case(logger, "test_sql_is_select", "고객 수 조회",
                      "SELECT로 시작", f"{len(results)}건", True)

    @pytest.mark.asyncio
    async def test_has_score_field(self):
        """하이브리드 검색 결과에 _score 필드가 존재한다."""
        results = await _search_sql_history("잔액 현황", prefetch=10)

        scored = [r for r in results if "_score" in r]
        log_test_case(logger, "test_score_field", "잔액 현황",
                      "_score 존재", f"{len(scored)}/{len(results)}", len(scored) > 0)
        assert len(scored) > 0, "_score 필드가 있는 결과 없음"


# ══════════════════════════════════════════════════════════════
# 4. 검색 품질 종합 점수
# ══════════════════════════════════════════════════════════════

@_SKIP
class TestSqlHistoryQualityScorecard:
    """전체 골든셋에 대한 검색 품질 종합 점수."""

    @pytest.mark.asyncio
    async def test_description_hit_rate(self):
        """description 키워드 적중률 >= 70%."""
        hits = 0
        total = len(SQL_HISTORY_GOLDEN_CASES)

        for case in SQL_HISTORY_GOLDEN_CASES:
            results = await _search_sql_history(case["query"], prefetch=20)
            kw = case["expected_keyword_in_desc"].lower()
            all_desc = " ".join(
                r.get("description", "") for r in results[:10]
            ).lower()
            if kw in all_desc:
                hits += 1

        rate = hits / total * 100
        log_test_case(
            logger, "test_desc_hit_rate",
            f"{total}건 골든셋", ">= 70%",
            f"{rate:.0f}% ({hits}/{total})", rate >= 70,
        )

        print(f"\n  [sql_history description 적중률] {rate:.1f}% ({hits}/{total})")
        assert rate >= 70, f"description 적중률 {rate:.1f}% < 70%"

    @pytest.mark.asyncio
    async def test_sql_hit_rate(self):
        """SQL 키워드 적중률 >= 60%."""
        hits = 0
        total = len(SQL_HISTORY_GOLDEN_CASES)

        for case in SQL_HISTORY_GOLDEN_CASES:
            results = await _search_sql_history(case["query"], prefetch=20)
            kw = case["expected_keyword_in_sql"].upper()
            all_sql = " ".join(
                r.get("sql", "") for r in results[:10]
            ).upper()
            if kw in all_sql:
                hits += 1

        rate = hits / total * 100
        log_test_case(
            logger, "test_sql_hit_rate",
            f"{total}건 골든셋", ">= 60%",
            f"{rate:.0f}% ({hits}/{total})", rate >= 60,
        )

        print(f"\n  [sql_history SQL 적중률] {rate:.1f}% ({hits}/{total})")
        assert rate >= 60, f"SQL 적중률 {rate:.1f}% < 60%"
