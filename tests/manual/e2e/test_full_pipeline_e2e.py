"""전체 파이프라인 Real E2E 테스트 — 25건.

Docker 데이터소스(PostgreSQL, MongoDB, Qdrant) + LLM API를 사용하여
파이프라인의 모든 분기 경로를 검증하고, 트레이스를 자동 분석한다.

실행 조건:
  - Docker 컨테이너 실행 중
  - .env에 USE_DUMMY=false, LLM API 키 설정
  - pytest tests/manual/e2e/test_full_pipeline_e2e.py -m real_e2e -v

Rate Limit 대응:
  - Groq 무료 API 등 Rate Limit 발생 시 자동 대기 후 재시도
  - 테스트 간 간격 두기 (INTER_TEST_DELAY)
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.real_e2e

# ── 설정 ──────────────────────────────────────────────────────

TRACE_OUTPUT_DIR = Path("tests/reports/traces")
REPORT_OUTPUT = Path("tests/reports/full_e2e_report.txt")
INTER_TEST_DELAY = float(os.getenv("E2E_DELAY_SEC", "3"))
MAX_RATE_LIMIT_RETRIES = 3
RATE_LIMIT_WAIT_SEC = 30


# ── Rate Limit 재시도 래퍼 ────────────────────────────────────

async def _run_with_rate_limit_retry(
    query: str,
    conversation_history: list[dict[str, str]] | None = None,
) -> tuple[Any, Any]:
    """Rate Limit 발생 시 대기 후 재시도하여 파이프라인을 실행한다."""
    from src.agents.graph.runner import run_pipeline
    from src.utils.tracker import EvaluationTracker

    TRACE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for attempt in range(1, MAX_RATE_LIMIT_RETRIES + 1):
        tracker = EvaluationTracker(
            run_id=f"e2e_{int(time.time())}_{attempt}",
        )
        try:
            result = await run_pipeline(
                user_input=query,
                tracker=tracker,
                conversation_history=conversation_history,
            )
            return result, tracker
        except Exception as e:
            err_str = str(e).lower()
            is_rate_limit = any(
                kw in err_str
                for kw in ("rate_limit", "429", "too many", "quota")
            )
            if is_rate_limit and attempt < MAX_RATE_LIMIT_RETRIES:
                wait = RATE_LIMIT_WAIT_SEC * attempt
                print(
                    f"\n  [Rate Limit] {wait}초 대기 후 재시도 "
                    f"(attempt {attempt}/{MAX_RATE_LIMIT_RETRIES})"
                )
                await asyncio.sleep(wait)
                continue
            raise

    raise RuntimeError("Rate Limit 재시도 초과")


# ── 결과 수집 ─────────────────────────────────────────────────

RESULTS: list[dict[str, Any]] = []


def _record(
    test_id: str,
    category: str,
    query: str,
    result: Any,
    tracker: Any,
    checks: dict[str, bool],
    elapsed_ms: float,
):
    """테스트 결과를 수집한다."""
    trace = tracker.trace if tracker else None
    sql_rec = trace.sql if trace else None

    entry = {
        "test_id": test_id,
        "category": category,
        "query": query,
        "elapsed_ms": round(elapsed_ms, 1),
        "intent": (
            trace.final_intent if trace else ""
        ),
        "status": (
            trace.final_status if trace else ""
        ),
        "sql_generated": bool(
            sql_rec and sql_rec.generated_sql
        ),
        "sql_validated": bool(
            sql_rec and sql_rec.validated
        ),
        "sql_executed": bool(
            sql_rec and sql_rec.execution_success
        ),
        "row_count": (
            sql_rec.row_count if sql_rec else 0
        ),
        "llm_calls": (
            trace.total_llm_calls if trace else 0
        ),
        "checks": checks,
        "all_passed": all(checks.values()),
    }
    RESULTS.append(entry)


# ── 테스트 간 Rate Limit 방지 딜레이 ─────────────────────────

@pytest.fixture(autouse=True)
async def inter_test_delay():
    """각 테스트 사이에 딜레이를 둔다."""
    yield
    if INTER_TEST_DELAY > 0:
        await asyncio.sleep(INTER_TEST_DELAY)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 카테고리 A: 비데이터 질의
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestNonDataQueries:
    """비데이터 질의 — 의도 분류 후 clarify/종료 경로."""

    @pytest.mark.asyncio
    async def test_A01_casual_talk(self):
        """일상 대화 → CASUAL_TALK 분류."""
        t0 = time.perf_counter()
        result, tracker = await _run_with_rate_limit_retry(
            "안녕하세요",
        )
        elapsed = (time.perf_counter() - t0) * 1000

        trace = tracker.trace
        checks = {
            "intent_not_data": trace.final_intent not in (
                "data_extraction", "data_analysis",
            ),
            "no_sql_generated": not trace.sql.generated_sql,
            "has_response": bool(trace.final_response_summary),
        }
        _record("A-01", "non_data", "안녕하세요",
                result, tracker, checks, elapsed)

        assert checks["intent_not_data"], (
            f"일상 대화가 데이터 질의로 분류됨: {trace.final_intent}"
        )

    @pytest.mark.asyncio
    async def test_A02_meta_question(self):
        """메타 질문 → META_QUESTION 분류."""
        t0 = time.perf_counter()
        result, tracker = await _run_with_rate_limit_retry(
            "TB_LOAN_INFO 테이블에 어떤 컬럼이 있어?",
        )
        elapsed = (time.perf_counter() - t0) * 1000

        trace = tracker.trace
        checks = {
            "no_sql_generated": not trace.sql.generated_sql,
            "has_response": bool(trace.final_response_summary),
        }
        _record("A-02", "non_data", "메타 질문",
                result, tracker, checks, elapsed)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 카테고리 B: 단순 추출 (Happy Path)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestSimpleExtraction:
    """단순 데이터 추출 — SQL 생성/검증/실행 전체 경로."""

    @pytest.mark.asyncio
    async def test_B01_simple_count(self):
        """단일 테이블 COUNT 집계."""
        t0 = time.perf_counter()
        result, tracker = await _run_with_rate_limit_retry(
            "전체 고객 수 알려줘",
        )
        elapsed = (time.perf_counter() - t0) * 1000

        trace = tracker.trace
        sql = trace.sql.generated_sql.upper()
        checks = {
            "intent_extraction": trace.final_intent in (
                "data_extraction", "data_analysis",
            ),
            "sql_generated": bool(trace.sql.generated_sql),
            "has_count": "COUNT" in sql,
            "sql_validated": trace.sql.validated,
        }
        _record("B-01", "simple_extract", "전체 고객 수",
                result, tracker, checks, elapsed)

        assert checks["sql_generated"], "SQL 미생성"

    @pytest.mark.asyncio
    async def test_B02_date_filter(self):
        """날짜 조건이 포함된 집계."""
        t0 = time.perf_counter()
        result, tracker = await _run_with_rate_limit_retry(
            "이번 달 신규 여신 건수",
        )
        elapsed = (time.perf_counter() - t0) * 1000

        trace = tracker.trace
        sql = trace.sql.generated_sql.upper()
        checks = {
            "sql_generated": bool(trace.sql.generated_sql),
            "has_date_condition": any(
                kw in sql for kw in ("WHERE", "DATE", "YMD", "CURRENT")
            ),
            "has_count": "COUNT" in sql,
        }
        _record("B-02", "simple_extract", "이번 달 신규 여신",
                result, tracker, checks, elapsed)

    @pytest.mark.asyncio
    async def test_B03_group_by(self):
        """GROUP BY 집계."""
        t0 = time.perf_counter()
        result, tracker = await _run_with_rate_limit_retry(
            "고객별 대출 잔액 합계",
        )
        elapsed = (time.perf_counter() - t0) * 1000

        trace = tracker.trace
        sql = trace.sql.generated_sql.upper()
        checks = {
            "sql_generated": bool(trace.sql.generated_sql),
            "has_group_by": "GROUP BY" in sql,
            "has_sum": "SUM" in sql,
        }
        _record("B-03", "simple_extract", "고객별 잔액 합계",
                result, tracker, checks, elapsed)

    @pytest.mark.asyncio
    async def test_B04_join(self):
        """다중 테이블 JOIN."""
        t0 = time.perf_counter()
        result, tracker = await _run_with_rate_limit_retry(
            "지점별 수신 잔액 현황",
        )
        elapsed = (time.perf_counter() - t0) * 1000

        trace = tracker.trace
        sql = trace.sql.generated_sql.upper()
        checks = {
            "sql_generated": bool(trace.sql.generated_sql),
            "has_join": "JOIN" in sql,
            "has_group_by": "GROUP BY" in sql,
        }
        _record("B-04", "simple_extract", "지점별 수신 잔액",
                result, tracker, checks, elapsed)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 카테고리 D: 복잡 추론
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestComplexReasoning:
    """복잡한 추론이 필요한 질의."""

    @pytest.mark.asyncio
    async def test_D02_amount_unit(self):
        """금액 단위 변환 (1억 → 100000000)."""
        t0 = time.perf_counter()
        result, tracker = await _run_with_rate_limit_retry(
            "1억 이상 대출 보유 고객 수",
        )
        elapsed = (time.perf_counter() - t0) * 1000

        trace = tracker.trace
        sql = trace.sql.generated_sql
        checks = {
            "sql_generated": bool(sql),
            "correct_amount": "100000000" in sql if sql else False,
            "wrong_amount_absent": "10000000000" not in (sql or ""),
        }
        _record("D-02", "complex", "1억 이상 대출",
                result, tracker, checks, elapsed)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 카테고리 E: 에지 케이스 / 방어 경로
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestEdgeCases:
    """에지 케이스 및 보안/방어 경로."""

    @pytest.mark.asyncio
    async def test_E01_pii_masking(self):
        """PII 마스킹 — 전화번호 마스킹."""
        t0 = time.perf_counter()
        result, tracker = await _run_with_rate_limit_retry(
            "고객 이름과 전화번호 목록",
        )
        elapsed = (time.perf_counter() - t0) * 1000

        trace = tracker.trace
        sql = trace.sql.generated_sql.upper() if trace.sql.generated_sql else ""
        checks = {
            "sql_generated": bool(trace.sql.generated_sql),
            "has_masking": any(
                kw in sql for kw in ("LEFT(", "SUBSTR(", "****", "MASK")
            ) if sql else False,
        }
        _record("E-01", "edge", "PII 마스킹",
                result, tracker, checks, elapsed)

    @pytest.mark.asyncio
    async def test_E02_bulk_defense(self):
        """대용량 방어 — LIMIT 강제."""
        t0 = time.perf_counter()
        result, tracker = await _run_with_rate_limit_retry(
            "전체 거래 내역 다 뽑아줘",
        )
        elapsed = (time.perf_counter() - t0) * 1000

        trace = tracker.trace
        sql = trace.sql.generated_sql.upper() if trace.sql.generated_sql else ""
        checks = {
            "has_limit": (
                "LIMIT" in sql or "TOP" in sql
            ) if sql else False,
            "has_date_condition": any(
                kw in sql for kw in ("WHERE", "DATE", "YMD")
            ) if sql else False,
        }
        _record("E-02", "edge", "대용량 방어",
                result, tracker, checks, elapsed)

    @pytest.mark.asyncio
    async def test_E03_no_table_found(self):
        """존재하지 않는 도메인 — 실패 또는 재계획."""
        t0 = time.perf_counter()
        result, tracker = await _run_with_rate_limit_retry(
            "외환 파생상품 거래 현황",
        )
        elapsed = (time.perf_counter() - t0) * 1000

        trace = tracker.trace
        checks = {
            "graceful_handling": (
                trace.final_status in (
                    "failure", "error", "clarification",
                    "casual_response",
                )
                or not trace.sql.generated_sql
            ),
        }
        _record("E-03", "edge", "테이블 미발견",
                result, tracker, checks, elapsed)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 카테고리 F: 데이터 분석
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestDataAnalysis:
    """DATA_ANALYSIS 의도 — 분석 노드 진입."""

    @pytest.mark.asyncio
    async def test_F01_analysis_intent(self):
        """분석 질의 → analyze_data 노드 진입."""
        t0 = time.perf_counter()
        result, tracker = await _run_with_rate_limit_retry(
            "지점별 여신 잔액 비교 분석해줘",
        )
        elapsed = (time.perf_counter() - t0) * 1000

        trace = tracker.trace
        checks = {
            "intent_analysis": trace.final_intent == "data_analysis",
            "sql_generated": bool(trace.sql.generated_sql),
        }
        _record("F-01", "analysis", "지점별 분석",
                result, tracker, checks, elapsed)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 종합 보고서 + 자동 분석
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestFullReport:
    """종합 보고서 생성 및 트레이스 자동 분석."""

    def test_zz_generate_report(self):
        """테스트 결과 보고서 + trace_analyzer 분석."""
        if not RESULTS:
            print("\n[보고서] 수집된 결과 없음")
            return

        lines = [
            "",
            "=" * 76,
            "  Full Pipeline E2E 테스트 보고서",
            "=" * 76,
            "",
        ]

        # 카테고리별 결과
        categories: dict[str, list] = {}
        for r in RESULTS:
            categories.setdefault(r["category"], []).append(r)

        for cat, items in categories.items():
            lines.append(f"## {cat.upper()}")
            lines.append("-" * 60)
            for item in items:
                icon = "OK" if item["all_passed"] else "!!"
                lines.append(
                    f"  [{icon}] {item['test_id']}: "
                    f"{item['query'][:40]}"
                )
                lines.append(
                    f"    intent={item['intent']}, "
                    f"sql={'Y' if item['sql_generated'] else 'N'}, "
                    f"valid={'Y' if item['sql_validated'] else 'N'}, "
                    f"exec={'Y' if item['sql_executed'] else 'N'}, "
                    f"rows={item['row_count']}, "
                    f"llm={item['llm_calls']}calls, "
                    f"{item['elapsed_ms']}ms"
                )
                failed_checks = [
                    k for k, v in item["checks"].items() if not v
                ]
                if failed_checks:
                    lines.append(
                        f"    FAILED: {', '.join(failed_checks)}"
                    )
                lines.append("")

        # 요약
        total = len(RESULTS)
        passed = sum(1 for r in RESULTS if r["all_passed"])
        avg_ms = sum(r["elapsed_ms"] for r in RESULTS) / total
        total_llm = sum(r["llm_calls"] for r in RESULTS)

        lines.append("=" * 76)
        lines.append(
            f"  TOTAL: {total} | "
            f"PASS: {passed} ({passed/total:.0%}) | "
            f"FAIL: {total - passed}"
        )
        lines.append(f"  AVG LATENCY: {avg_ms:.0f}ms")
        lines.append(f"  TOTAL LLM CALLS: {total_llm}")
        lines.append("=" * 76)

        # trace_analyzer 자동 분석
        lines.append("")
        lines.append("## TRACE ANALYZER 자동 분석")
        lines.append("-" * 60)

        try:
            from src.utils.tracker import analyze_batch
            if TRACE_OUTPUT_DIR.exists():
                batch = analyze_batch(str(TRACE_OUTPUT_DIR))
                lines.append(batch.summary)
            else:
                lines.append("  (트레이스 디렉토리 없음)")
        except Exception as e:
            lines.append(f"  분석 오류: {e}")

        report_text = "\n".join(lines)

        # 콘솔 출력
        import sys
        try:
            print(report_text)
        except UnicodeEncodeError:
            safe = report_text.encode(
                sys.stdout.encoding or "utf-8",
                errors="replace",
            ).decode(sys.stdout.encoding or "utf-8")
            print(safe)

        # 파일 저장
        REPORT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        REPORT_OUTPUT.write_text(report_text, encoding="utf-8")
        print(f"\n보고서 저장: {REPORT_OUTPUT.absolute()}")
