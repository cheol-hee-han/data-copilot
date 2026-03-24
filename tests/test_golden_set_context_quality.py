"""골든셋 90건 기반 컨텍스트 탐색 품질 E2E 테스트.

test_queries.json의 실제 사용자 질의 90건에 대해:
  1. search_query_builder 로 소스별 쿼리 생성
  2. ES table_meta 실제 검색 → expected_tables 커버 여부
  3. Qdrant sql_history 벡터 검색 → 관련 SQL 반환 여부
  4. Qdrant biz_manual 벡터 검색 → 관련 카테고리 반환 여부
  5. 종합 스코어카드 (도메인별, 난이도별, 소스별)

실행: pytest tests/test_golden_set_context_quality.py -v -s
"""

from __future__ import annotations

import json
import os
import warnings
from pathlib import Path

import pytest
import requests

warnings.filterwarnings("ignore", category=UserWarning)

from src.services.search_query_builder import build_source_queries

# ──────────────────────────────────────────────────────────────
# 인프라 셋업
# ──────────────────────────────────────────────────────────────

ES_URL = "http://localhost:9200"
ES_AUTH = ("elastic", "elastic_pass")
QDRANT_URL = "http://localhost:6333"
EMBEDDING_MODEL = (
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
)

_embedder = None
_qdrant = None


def _get_embedder():
    global _embedder
    if _embedder is None:
        from fastembed import TextEmbedding
        _embedder = TextEmbedding(model_name=EMBEDDING_MODEL)
    return _embedder


def _get_qdrant():
    global _qdrant
    if _qdrant is None:
        from qdrant_client import QdrantClient
        _qdrant = QdrantClient(
            url=QDRANT_URL, timeout=10,
            check_compatibility=False,
        )
    return _qdrant


def _services_available() -> bool:
    try:
        r1 = requests.get(
            f"{ES_URL}/_cluster/health",
            auth=ES_AUTH, timeout=3,
        )
        r2 = requests.get(f"{QDRANT_URL}/healthz", timeout=3)
        return r1.status_code == 200 and r2.status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _services_available(),
    reason="Docker 서비스(ES/Qdrant) 미기동",
)

# ──────────────────────────────────────────────────────────────
# 골든셋 로드
# ──────────────────────────────────────────────────────────────

_GOLDEN_PATH = Path(__file__).resolve().parent.parent / (
    "evaluation/golden_set/test_queries.json"
)


def _load_golden_set() -> list[dict]:
    """골든셋 90건을 로드하여 flat list로 반환."""
    with open(_GOLDEN_PATH, encoding="utf-8") as f:
        data = json.load(f)
    cases = []
    for section in ["extraction", "analysis", "visualization"]:
        for item in data.get(section, []):
            cases.append(item)
    return cases


ALL_CASES = _load_golden_set()

# 도메인 코드 매핑 (골든셋 domain → ES domain_cd)
_DOMAIN_TO_ES = {
    "CUS": ["CUS"],
    "DEP": ["DEP"],
    "LON": ["LON"],
    "CRD": ["CRD"],
    "TRX": ["TRX"],
    "MKT": ["CUS", "DEP", "LON", "CRD", "TRX"],  # 마케팅은 복합
}

# 도메인 → Qdrant biz_manual 카테고리 매핑
_DOMAIN_TO_MANUAL_CAT = {
    "CUS": ["고객관리", "공통"],
    "DEP": ["수신", "공통"],
    "LON": ["여신", "리스크/준법감시"],
    "CRD": ["카드"],
    "TRX": ["거래/결제", "전자금융"],
    "MKT": [
        "고객관리", "수신", "여신", "카드",
        "마케팅", "거래/결제",
    ],
}


# ──────────────────────────────────────────────────────────────
# 검색 헬퍼
# ──────────────────────────────────────────────────────────────

def _es_search_tables(query: str, size: int = 10) -> list[dict]:
    """ES table_meta 검색 → [{table_name, domain_cd, score}]."""
    r = requests.post(
        f"{ES_URL}/table_meta/_search",
        auth=ES_AUTH,
        json={
            "size": size,
            "query": {
                "multi_match": {"query": query, "fields": ["*"]},
            },
            "_source": ["table_name", "domain_cd"],
        },
        timeout=10,
    )
    return [
        {
            "table_name": h["_source"]["table_name"],
            "domain_cd": h["_source"].get("domain_cd", ""),
            "score": h["_score"],
        }
        for h in r.json().get("hits", {}).get("hits", [])
    ]


def _es_search_reports(query: str, size: int = 5) -> list[dict]:
    """ES report_sql 검색."""
    r = requests.post(
        f"{ES_URL}/report_sql/_search",
        auth=ES_AUTH,
        json={
            "size": size,
            "query": {
                "multi_match": {"query": query, "fields": ["*"]},
            },
            "_source": [
                "report_nm", "report_desc",
                "domain_cd", "tables_used",
            ],
        },
        timeout=10,
    )
    return [
        {
            "report_nm": h["_source"].get("report_nm", ""),
            "domain_cd": h["_source"].get("domain_cd", ""),
            "tables_used": h["_source"].get("tables_used", []),
            "score": h["_score"],
        }
        for h in r.json().get("hits", {}).get("hits", [])
    ]


def _qdrant_vector_search(
    collection: str, query: str, limit: int = 5,
) -> list[dict]:
    """Qdrant 벡터 검색."""
    vec = list(_get_embedder().embed([query]))[0].tolist()
    results = _get_qdrant().query_points(
        collection_name=collection,
        query=vec,
        limit=limit,
    )
    return [
        {"payload": r.payload, "score": r.score}
        for r in results.points
    ]


# ──────────────────────────────────────────────────────────────
# 평가 로직
# ──────────────────────────────────────────────────────────────

def _eval_es_table_coverage(
    case: dict, es_results: list[dict],
) -> dict:
    """ES 검색 결과가 expected_tables를 얼마나 커버하는지 평가.

    Returns:
        {hit: bool, matched: [...], missed: [...], detail: str}
    """
    expected = set(case.get("expected_tables", []))
    found_tables = {r["table_name"] for r in es_results}
    found_domains = {r["domain_cd"] for r in es_results}

    # 테이블 정확 매칭
    exact_match = expected & found_tables

    # 테이블명 접두사 매칭 (TB_LOAN_INFO → TB_LOAN 패턴)
    prefix_match = set()
    for exp in expected:
        prefix = exp.rsplit("_", 1)[0] if "_" in exp else exp
        for found in found_tables:
            if found.startswith(prefix):
                prefix_match.add(exp)
                break

    # 도메인 매칭 (같은 도메인의 테이블이라도 관련성 있음)
    domain = case.get("domain", "")
    es_domains = _DOMAIN_TO_ES.get(domain, [])
    domain_hit = any(d in found_domains for d in es_domains)

    matched = exact_match | prefix_match
    missed = expected - matched
    hit = len(matched) > 0 or domain_hit

    return {
        "hit": hit,
        "exact": list(exact_match),
        "prefix": list(prefix_match - exact_match),
        "missed": list(missed),
        "domain_hit": domain_hit,
        "found_count": len(es_results),
    }


def _eval_sql_history(
    case: dict, results: list[dict],
) -> dict:
    """sql_history 벡터 검색 결과가 관련 SQL을 반환하는지 평가."""
    expected = case.get("expected_tables", [])
    if not results:
        return {"hit": False, "detail": "결과 없음"}

    # 반환된 SQL에서 expected_tables의 테이블명이 포함되는지
    all_sql = " ".join(
        r["payload"].get("sql", "").upper() for r in results[:3]
    )
    all_desc = " ".join(
        r["payload"].get("description", "") for r in results[:3]
    )

    table_hits = []
    for t in expected:
        # TB_CUST_INFO → CUST 패턴으로 검색
        core = t.replace("TB_", "").split("_")[0]
        if core.upper() in all_sql:
            table_hits.append(t)

    top_score = results[0]["score"] if results else 0

    return {
        "hit": len(table_hits) > 0,
        "table_hits": table_hits,
        "top_score": top_score,
        "top_desc": results[0]["payload"].get(
            "description", "",
        )[:50] if results else "",
    }


def _eval_biz_manual(
    case: dict, results: list[dict],
) -> dict:
    """biz_manual 벡터 검색 결과의 카테고리 적합성 평가."""
    domain = case.get("domain", "")
    expected_cats = _DOMAIN_TO_MANUAL_CAT.get(domain, [])

    if not results:
        return {"hit": False, "detail": "결과 없음"}

    found_cats = [
        r["payload"].get("category", "") for r in results[:3]
    ]
    cat_hit = any(c in expected_cats for c in found_cats)
    top_score = results[0]["score"] if results else 0

    return {
        "hit": cat_hit,
        "found_cats": found_cats,
        "expected_cats": expected_cats,
        "top_score": top_score,
        "top_title": results[0]["payload"].get(
            "title", "",
        ) if results else "",
    }


# ──────────────────────────────────────────────────────────────
# 1. ES 테이블 탐색 품질 (90건 전체)
# ──────────────────────────────────────────────────────────────

class TestGoldenSetESTableCoverage:
    """골든셋 90건의 expected_tables가 ES 검색으로 커버되는지."""

    @pytest.mark.parametrize(
        "case", ALL_CASES,
        ids=[c["id"] for c in ALL_CASES],
    )
    def test_es_finds_relevant_tables(self, case: dict):
        """전략 쿼리로 ES 검색 시 기대 도메인의 테이블이 반환된다."""
        sq = build_source_queries(case["user_input"])
        results = _es_search_tables(sq.es_table_query)
        ev = _eval_es_table_coverage(case, results)

        assert ev["hit"], (
            f"[{case['id']}] ES 검색 실패.\n"
            f"  질의: {case['user_input']}\n"
            f"  기대: {case['expected_tables']}\n"
            f"  전략쿼리: {sq.es_table_query[:80]}\n"
            f"  반환: {[r['table_name'] for r in results[:5]]}\n"
            f"  missed: {ev['missed']}"
        )


# ──────────────────────────────────────────────────────────────
# 2. Qdrant SQL 이력 검색 품질 (90건 전체)
# ──────────────────────────────────────────────────────────────

class TestGoldenSetSqlHistoryRelevance:
    """골든셋 질의에 대해 sql_history에서 관련 SQL이 반환되는지."""

    @pytest.mark.parametrize(
        "case", ALL_CASES,
        ids=[c["id"] for c in ALL_CASES],
    )
    def test_sql_history_returns_relevant(self, case: dict):
        """벡터 검색으로 관련 테이블을 참조하는 SQL이 top-3에 포함."""
        # sql_history는 원본 자연어가 벡터 검색에 더 적합
        results = _qdrant_vector_search(
            "sql_history", case["user_input"], limit=5,
        )
        ev = _eval_sql_history(case, results)

        assert ev["hit"], (
            f"[{case['id']}] sql_history 관련 SQL 미반환.\n"
            f"  질의: {case['user_input']}\n"
            f"  기대 테이블: {case['expected_tables']}\n"
            f"  top1: [{ev['top_score']:.3f}] {ev['top_desc']}"
        )


# ──────────────────────────────────────────────────────────────
# 3. Qdrant biz_manual 검색 품질
# ──────────────────────────────────────────────────────────────

class TestGoldenSetBizManualRelevance:
    """골든셋 질의에 대해 biz_manual에서 관련 카테고리가 반환되는지."""

    @pytest.mark.parametrize(
        "case", ALL_CASES,
        ids=[c["id"] for c in ALL_CASES],
    )
    def test_manual_returns_relevant_category(self, case: dict):
        """벡터 검색 top-3의 카테고리가 질의 도메인과 매칭."""
        sq = build_source_queries(case["user_input"])
        results = _qdrant_vector_search(
            "biz_manual", sq.qdrant_query, limit=5,
        )
        ev = _eval_biz_manual(case, results)

        assert ev["hit"], (
            f"[{case['id']}] biz_manual 카테고리 불일치.\n"
            f"  질의: {case['user_input']} (domain={case['domain']})\n"
            f"  기대 카테고리: {ev['expected_cats']}\n"
            f"  반환 카테고리: {ev['found_cats']}\n"
            f"  top1: [{ev['top_score']:.3f}] {ev['top_title']}"
        )


# ──────────────────────────────────────────────────────────────
# 4. 종합 스코어카드
# ──────────────────────────────────────────────────────────────

class TestGoldenSetScorecard:
    """90건 전체에 대한 종합 품질 점수를 소스별·도메인별·난이도별로 산출."""

    def test_comprehensive_scorecard(self):
        """전체 골든셋 컨텍스트 탐색 품질 리포트."""
        # 집계 구조
        scores = {
            "es_table": {"hit": 0, "total": 0},
            "sql_history": {"hit": 0, "total": 0},
            "biz_manual": {"hit": 0, "total": 0},
        }
        by_domain: dict[str, dict] = {}
        by_difficulty: dict[str, dict] = {}
        failures: list[str] = []

        for case in ALL_CASES:
            domain = case.get("domain", "?")
            diff = case.get("difficulty", "?")
            cid = case["id"]

            # 초기화
            for group in [by_domain, by_difficulty]:
                key = domain if group is by_domain else diff
                if key not in group:
                    group[key] = {
                        "es": 0, "sql": 0, "manual": 0, "total": 0,
                    }

            d_key = domain
            diff_key = diff
            by_domain[d_key]["total"] += 1
            by_difficulty[diff_key]["total"] += 1

            sq = build_source_queries(case["user_input"])

            # ES
            es_results = _es_search_tables(sq.es_table_query)
            es_ev = _eval_es_table_coverage(case, es_results)
            scores["es_table"]["total"] += 1
            if es_ev["hit"]:
                scores["es_table"]["hit"] += 1
                by_domain[d_key]["es"] += 1
                by_difficulty[diff_key]["es"] += 1
            else:
                failures.append(
                    f"  ES  {cid}: {case['user_input'][:30]}... "
                    f"missed={es_ev['missed']}"
                )

            # SQL History
            sql_results = _qdrant_vector_search(
                "sql_history", case["user_input"], limit=5,
            )
            sql_ev = _eval_sql_history(case, sql_results)
            scores["sql_history"]["total"] += 1
            if sql_ev["hit"]:
                scores["sql_history"]["hit"] += 1
                by_domain[d_key]["sql"] += 1
                by_difficulty[diff_key]["sql"] += 1

            # biz_manual
            manual_results = _qdrant_vector_search(
                "biz_manual", sq.qdrant_query, limit=5,
            )
            manual_ev = _eval_biz_manual(case, manual_results)
            scores["biz_manual"]["total"] += 1
            if manual_ev["hit"]:
                scores["biz_manual"]["hit"] += 1
                by_domain[d_key]["manual"] += 1
                by_difficulty[diff_key]["manual"] += 1

        # ── 리포트 출력 ──
        def pct(h: int, t: int) -> str:
            return f"{h/t*100:.1f}%" if t else "N/A"

        print(f"\n{'='*72}")
        print(f"  골든셋 90건 컨텍스트 탐색 품질 종합 리포트")
        print(f"{'='*72}")

        print(f"\n  [소스별 적합도]")
        for src, s in scores.items():
            print(
                f"    {src:15s}: "
                f"{pct(s['hit'], s['total']):>6s} "
                f"({s['hit']}/{s['total']})"
            )

        overall_hit = sum(s["hit"] for s in scores.values())
        overall_total = sum(s["total"] for s in scores.values())
        print(
            f"    {'종합':15s}: "
            f"{pct(overall_hit, overall_total):>6s} "
            f"({overall_hit}/{overall_total})"
        )

        print(f"\n  [도메인별 적합도]")
        print(f"    {'도메인':8s} {'ES':>8s} {'SQL이력':>8s} {'매뉴얼':>8s} {'건수':>5s}")
        for dom in sorted(by_domain.keys()):
            d = by_domain[dom]
            t = d["total"]
            print(
                f"    {dom:8s} "
                f"{pct(d['es'], t):>8s} "
                f"{pct(d['sql'], t):>8s} "
                f"{pct(d['manual'], t):>8s} "
                f"{t:>5d}"
            )

        print(f"\n  [난이도별 적합도]")
        print(f"    {'난이도':8s} {'ES':>8s} {'SQL이력':>8s} {'매뉴얼':>8s} {'건수':>5s}")
        for diff in ["easy", "medium", "hard"]:
            if diff in by_difficulty:
                d = by_difficulty[diff]
                t = d["total"]
                print(
                    f"    {diff:8s} "
                    f"{pct(d['es'], t):>8s} "
                    f"{pct(d['sql'], t):>8s} "
                    f"{pct(d['manual'], t):>8s} "
                    f"{t:>5d}"
                )

        if failures:
            print(f"\n  [ES 실패 케이스 (상위 10건)]")
            for f in failures[:10]:
                print(f)

        print(f"\n{'='*72}\n")

        # 최소 기준: ES 테이블 70% 이상
        es_pct = scores["es_table"]["hit"] / scores["es_table"]["total"] * 100
        assert es_pct >= 70.0, (
            f"ES 테이블 적합도 {es_pct:.1f}% < 70%"
        )
