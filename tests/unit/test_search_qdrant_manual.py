"""Qdrant 업무 매뉴얼(biz_manual) 검색 품질 테스트.

=== 개념 설명 ===
Qdrant biz_manual 컬렉션(500+건)에서 사용자 질의에 대해
관련 업무 매뉴얼이 올바르게 검색되는지 품질을 검증한다.

검증 관점:
  1. 카테고리 적합성 — "여신" 질의 → 여신 카테고리 매뉴얼 반환
  2. 도메인 커버리지 — 주요 도메인(고객/여신/수신/카드/외환)별 검색 정확도
  3. 업무 규정 검색 — 금융 계수산출식·업무 규정이 필요한 질의에서 관련 매뉴얼 반환
  4. 검색 결과 구조 — payload 에 title, content, category 필드 존재

대상: QdrantConnector.search_manual() (실제 Qdrant 서비스)

=== 단독 실행 ===
    python -m pytest tests/unit/test_search_qdrant_manual.py -v -s

=== 테스트 데이터 예시 ===
    입력: "연체 판단 기준이 뭐야"
    기대: category="여신" 매뉴얼이 상위에 포함, content에 "연체" 관련 내용

=== 정상 결과 ===
    관련 카테고리 매뉴얼이 top-K 에 포함, payload 구조 정상
=== 오류 결과 ===
    무관한 카테고리만 반환, 빈 결과
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from tests.unit.conftest import get_test_logger, log_test_case

logger = get_test_logger("test_search_qdrant_manual")


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

async def _search_manual(query: str, top_k: int = 5) -> list[dict]:
    """QdrantConnector 를 통해 biz_manual 을 검색한다."""
    from src.connectors.impl.qdrant_connector import QdrantConnector

    conn = QdrantConnector(use_dummy=False)
    await conn.connect()
    try:
        return await conn.search_manual(query, top_k=top_k)
    finally:
        await conn.disconnect()


# ══════════════════════════════════════════════════════════════
# 골든셋 — 도메인별 질의 + 기대 카테고리
# ══════════════════════════════════════════════════════════════

MANUAL_GOLDEN_CASES = [
    {
        "query": "연체 판단 기준이 뭐야",
        "expected_category": "여신",
        "expected_keyword": "연체",
        "description": "여신 연체 기준",
    },
    {
        "query": "VIP 고객 선정 기준",
        "expected_category": "고객관리",
        "expected_keyword": "VIP",
        "description": "고객관리 VIP 기준",
    },
    {
        "query": "정기예금 중도해지 규정",
        "expected_category": "수신",
        "expected_keyword": "정기예금",
        "description": "수신 정기예금 규정",
    },
    {
        "query": "카드 포인트 적립 기준",
        "expected_category": "카드",
        "expected_keyword": "카드",
        "description": "카드 포인트 규정",
    },
    {
        "query": "해외송금 한도가 얼마야",
        "expected_category": "외환",
        "expected_keyword": "송금",
        "description": "외환 송금 한도",
    },
    {
        "query": "대출 금리 산출 방법",
        "expected_category": "여신",
        "expected_keyword": "금리",
        "description": "여신 금리 산출",
    },
    {
        "query": "고객등급 분류 기준",
        "expected_category": "고객관리",
        "expected_keyword": "등급",
        "description": "고객등급 분류",
    },
    {
        "query": "예금자보호 한도",
        "expected_category": "수신",
        "expected_keyword": "예금자보호",
        "description": "수신 예금자보호",
    },
]


# ══════════════════════════════════════════════════════════════
# 1. 카테고리 적합성
# ══════════════════════════════════════════════════════════════

@_SKIP
class TestManualCategoryRelevance:
    """검색 결과의 카테고리가 질의 도메인과 일치하는지."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "case", MANUAL_GOLDEN_CASES,
        ids=[c["description"] for c in MANUAL_GOLDEN_CASES],
    )
    async def test_expected_category_in_results(self, case: dict):
        """기대 카테고리가 상위 5건에 포함된다."""
        results = await _search_manual(case["query"], top_k=5)

        categories = [r.get("category", "") for r in results]
        found = case["expected_category"] in categories

        log_test_case(
            logger, f"test_category_{case['description']}",
            case["query"], case["expected_category"],
            categories, found,
        )
        assert found, (
            f"[{case['description']}] 카테고리 '{case['expected_category']}' 미반환.\n"
            f"  반환 카테고리: {categories}\n"
            f"  반환 제목: {[r.get('title', '') for r in results]}"
        )


# ══════════════════════════════════════════════════════════════
# 2. 키워드 포함 검증
# ══════════════════════════════════════════════════════════════

@_SKIP
class TestManualContentRelevance:
    """검색된 매뉴얼 본문에 질의 핵심 키워드가 포함되는지."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "case", MANUAL_GOLDEN_CASES,
        ids=[c["description"] for c in MANUAL_GOLDEN_CASES],
    )
    async def test_keyword_in_content(self, case: dict):
        """반환된 매뉴얼 중 하나 이상에 핵심 키워드가 포함된다."""
        results = await _search_manual(case["query"], top_k=5)

        kw = case["expected_keyword"]
        all_content = " ".join(
            f"{r.get('title', '')} {r.get('content', '')}" for r in results
        )
        found = kw in all_content

        log_test_case(
            logger, f"test_keyword_{case['description']}",
            case["query"], f"'{kw}' 포함",
            f"{'포함' if found else '미포함'} ({len(results)}건)", found,
        )
        assert found, (
            f"[{case['description']}] 키워드 '{kw}' 미포함.\n"
            f"  반환 제목: {[r.get('title', '') for r in results]}"
        )


# ══════════════════════════════════════════════════════════════
# 3. 결과 구조 검증
# ══════════════════════════════════════════════════════════════

@_SKIP
class TestManualPayloadStructure:
    """반환된 payload 의 필드 구조가 올바른지."""

    @pytest.mark.asyncio
    async def test_payload_has_required_fields(self):
        """payload 에 title, content, category 필드가 존재한다."""
        results = await _search_manual("대출 규정", top_k=3)

        assert len(results) > 0, "검색 결과 없음"
        for r in results:
            assert "title" in r, f"title 필드 누락: {r.keys()}"
            assert "content" in r, f"content 필드 누락: {r.keys()}"
            assert "category" in r, f"category 필드 누락: {r.keys()}"

        log_test_case(logger, "test_payload_fields", "대출 규정",
                      "title, content, category", f"{len(results)}건 모두 정상", True)

    @pytest.mark.asyncio
    async def test_content_not_empty(self):
        """반환된 content 가 비어있지 않다."""
        results = await _search_manual("고객 관리", top_k=3)

        for r in results:
            assert len(r.get("content", "")) > 0, f"빈 content: {r.get('title', '')}"

        log_test_case(logger, "test_content_not_empty", "고객 관리",
                      "content 비어있지 않음", f"{len(results)}건", True)

    @pytest.mark.asyncio
    async def test_result_count_within_top_k(self):
        """반환 건수가 top_k 이하이다."""
        results = await _search_manual("예금 상품", top_k=3)
        assert len(results) <= 3
        log_test_case(logger, "test_top_k_limit", "예금 상품 (top_k=3)",
                      "<= 3건", f"{len(results)}건", True)


# ══════════════════════════════════════════════════════════════
# 4. 도메인 커버리지 종합 점수
# ══════════════════════════════════════════════════════════════

@_SKIP
class TestManualQualityScorecard:
    """전체 골든셋에 대한 검색 품질 종합 점수."""

    @pytest.mark.asyncio
    async def test_category_hit_rate(self):
        """전체 골든셋 카테고리 적중률 >= 70%."""
        hits = 0
        total = len(MANUAL_GOLDEN_CASES)

        for case in MANUAL_GOLDEN_CASES:
            results = await _search_manual(case["query"], top_k=5)
            categories = [r.get("category", "") for r in results]
            if case["expected_category"] in categories:
                hits += 1

        rate = hits / total * 100
        log_test_case(
            logger, "test_category_hit_rate",
            f"{total}건 골든셋", ">= 70%",
            f"{rate:.0f}% ({hits}/{total})", rate >= 70,
        )

        print(f"\n  [biz_manual 카테고리 적중률] {rate:.1f}% ({hits}/{total})")
        assert rate >= 70, f"카테고리 적중률 {rate:.1f}% < 70%"
