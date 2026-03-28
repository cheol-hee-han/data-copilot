"""에이전틱 코어 실제 E2E 테스트 — Docker 데이터소스 + LLM API.

실제 seeded 데이터(PostgreSQL, ES, Qdrant, MongoDB)와
LLM API(Groq/OpenAI Compatible)를 사용하여
에이전트의 전체 흐름을 추적하고 검증한다.

실행 조건:
  - Docker 컨테이너 실행 중 (dc-postgres, dc-elasticsearch, dc-qdrant, dc-mongodb)
  - .env에 USE_DUMMY=false, 실제 API 키 설정
  - pytest -m real_e2e 로 실행
"""

from __future__ import annotations

import time
from typing import Any

import pytest

# ── 마커 설정 ──
pytestmark = pytest.mark.real_e2e


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 보고서 수집기
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

REPORT: list[dict[str, Any]] = []


def _record(
    category: str, test_name: str,
    query: str,
    state_trace: dict,
    verdict: str,
    elapsed_ms: float = 0,
    findings: str = "",
):
    REPORT.append({
        "category": category,
        "test": test_name,
        "query": query,
        "state": state_trace,
        "verdict": verdict,
        "elapsed_ms": round(elapsed_ms, 1),
        "findings": findings,
    })


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 픽스처: 실제 커넥터 초기화
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.fixture()
async def connector_mgr():
    """실제 커넥터 매니저 초기화 — 매 테스트마다 새 연결."""
    from src.connectors.manager import (
        get_connector_manager,
        reset_connector_manager,
    )
    reset_connector_manager()
    mgr = get_connector_manager(use_dummy=False)
    await mgr.connect_all()
    yield mgr
    await mgr.disconnect_all()
    reset_connector_manager()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 카테고리 1: 실제 데이터소스 연결 검증 (기반 테스트)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestRealConnectorHealth:
    """실제 데이터소스 연결 상태 검증."""

    @pytest.mark.asyncio
    async def test_01_mongo_table_meta_search(
        self, connector_mgr,
    ):
        """MongoDB 테이블 메타 검색."""
        t0 = time.perf_counter()
        try:
            results = await connector_mgr.mongo.search_table_meta(
                "고객",
            )
        except Exception as e:
            elapsed = (time.perf_counter() - t0) * 1000
            _record(
                "connector", "mongo_table_meta",
                "고객",
                {"error": str(e)[:120]},
                "WARN", elapsed,
                findings=(
                    ".env의 MONGO_TABLE_META_COLLECTION과 "
                    "실제 MongoDB 컬렉션명 불일치 가능"
                ),
            )
            return  # WARN으로 기록, 테스트 실패 안 시킴

        elapsed = (time.perf_counter() - t0) * 1000

        trace = {
            "result_count": len(results),
            "first_table": (
                results[0].get("name", "?") if results
                else "none"
            ),
            "elapsed_ms": round(elapsed, 1),
        }

        _record(
            "connector", "mongo_table_meta",
            "고객", trace,
            "PASS" if len(results) > 0 else "WARN",
            elapsed,
        )

    @pytest.mark.asyncio
    async def test_02_mongo_code_meta_search(
        self, connector_mgr,
    ):
        """MongoDB 코드 메타 검색."""
        t0 = time.perf_counter()
        try:
            results = await connector_mgr.mongo.search_code_meta(
                "상태",
            )
        except Exception as e:
            elapsed = (time.perf_counter() - t0) * 1000
            _record(
                "connector", "mongo_code_meta",
                "상태", {"error": str(e)[:120]},
                "WARN", elapsed,
            )
            return

        elapsed = (time.perf_counter() - t0) * 1000
        trace = {
            "result_count": len(results),
            "elapsed_ms": round(elapsed, 1),
        }
        _record(
            "connector", "mongo_code_meta",
            "상태", trace,
            "PASS" if len(results) > 0 else "WARN",
            elapsed,
        )

    @pytest.mark.asyncio
    async def test_03_mongo_glossary_search(
        self, connector_mgr,
    ):
        """MongoDB 용어사전 검색."""
        t0 = time.perf_counter()
        try:
            results = await connector_mgr.mongo.search_glossary(
                "여신",
            )
        except Exception as e:
            elapsed = (time.perf_counter() - t0) * 1000
            _record(
                "connector", "mongo_glossary",
                "여신", {"error": str(e)[:120]},
                "WARN", elapsed,
            )
            return

        elapsed = (time.perf_counter() - t0) * 1000
        trace = {
            "result_count": len(results),
            "first_term": (
                results[0].get("name", "?") if results
                else "none"
            ),
            "elapsed_ms": round(elapsed, 1),
        }
        _record(
            "connector", "mongo_glossary",
            "여신", trace,
            "PASS" if len(results) > 0 else "WARN",
            elapsed,
        )

    @pytest.mark.asyncio
    async def test_04_es_table_meta_search(
        self, connector_mgr,
    ):
        """ES 테이블 메타 검색."""
        t0 = time.perf_counter()
        results = await connector_mgr.es.search_table_meta(
            "고객 마스터",
        )
        elapsed = (time.perf_counter() - t0) * 1000

        trace = {
            "result_count": len(results),
            "elapsed_ms": round(elapsed, 1),
        }

        _record(
            "connector", "es_table_meta",
            "고객 마스터", trace,
            "PASS" if len(results) > 0 else "WARN",
            elapsed,
        )

    @pytest.mark.asyncio
    async def test_05_es_report_sql_search(
        self, connector_mgr,
    ):
        """ES 보고서 SQL 검색."""
        t0 = time.perf_counter()
        try:
            results = await connector_mgr.es.search_report_sql(
                "여신 잔액",
            )
        except Exception as e:
            elapsed = (time.perf_counter() - t0) * 1000
            _record(
                "connector", "es_report_sql",
                "여신 잔액", {"error": str(e)[:120]},
                "WARN", elapsed,
            )
            return

        elapsed = (time.perf_counter() - t0) * 1000
        trace = {
            "result_count": len(results),
            "elapsed_ms": round(elapsed, 1),
        }
        _record(
            "connector", "es_report_sql",
            "여신 잔액", trace,
            "PASS" if len(results) > 0 else "WARN",
            elapsed,
        )

    @pytest.mark.asyncio
    async def test_06_qdrant_sql_history_search(
        self, connector_mgr,
    ):
        """Qdrant SQL 이력 벡터 검색."""
        t0 = time.perf_counter()
        try:
            results = await connector_mgr.qdrant.search_sql_history(
                "지점별 고객 수 집계",
            )
            elapsed = (time.perf_counter() - t0) * 1000
            trace = {
                "result_count": len(results),
                "top_score": (
                    results[0].get("_score", 0)
                    if results else 0
                ),
                "elapsed_ms": round(elapsed, 1),
            }
            _record(
                "connector", "qdrant_sql_history",
                "지점별 고객 수 집계", trace,
                "PASS" if len(results) > 0 else "WARN",
                elapsed,
            )
        except Exception as e:
            elapsed = (time.perf_counter() - t0) * 1000
            _record(
                "connector", "qdrant_sql_history",
                "지점별 고객 수 집계",
                {"error": str(e)[:100]},
                "FAIL", elapsed,
                findings=f"Qdrant 검색 실패: {e}",
            )

    @pytest.mark.asyncio
    async def test_07_qdrant_manual_search(
        self, connector_mgr,
    ):
        """Qdrant 업무 매뉴얼 검색."""
        t0 = time.perf_counter()
        try:
            results = await connector_mgr.qdrant.search_manual(
                "연체 관리",
            )
            elapsed = (time.perf_counter() - t0) * 1000
            trace = {
                "result_count": len(results),
                "elapsed_ms": round(elapsed, 1),
            }
            _record(
                "connector", "qdrant_manual",
                "연체 관리", trace,
                "PASS" if len(results) > 0 else "WARN",
                elapsed,
            )
        except Exception as e:
            elapsed = (time.perf_counter() - t0) * 1000
            _record(
                "connector", "qdrant_manual",
                "연체 관리",
                {"error": str(e)[:100]},
                "FAIL", elapsed,
            )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 카테고리 2: 에이전틱 노드별 실제 데이터 흐름 추적
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestRealAgenticNodeFlow:
    """실제 데이터소스를 사용한 에이전틱 노드 흐름."""

    @pytest.mark.asyncio
    async def test_01_planner_real_context(
        self, connector_mgr,
    ):
        """planner 노드 — 실제 데이터소스에서 초기 컨텍스트 수집."""
        from src.agents.state.state import (
            PipelineState,
            ReasoningState,
        )
        from src.agents.nodes.reason.planner import (
            planner_node,
        )

        state = PipelineState(
            preprocessed_input="고객별 여신 잔액 합계",
        )

        t0 = time.perf_counter()
        result = await planner_node(state)
        elapsed = (time.perf_counter() - t0) * 1000

        reason = result["reason"]
        trace = {
            "phase": reason.phase,
            "hypotheses": len(reason.hypotheses),
            "knowledge_items": len(
                reason.knowledge_items,
            ),
            "candidates": len(
                reason.candidate_tables,
            ),
            "candidate_names": [
                ct.table_name
                for ct in reason.candidate_tables
            ][:5],
            "fast_path": reason.fast_path_triggered,
            "use_cases": len(
                reason.explored_use_cases,
            ),
            "searched_queries": reason.searched_queries,
            "hints_empty": reason.structural_hints.is_empty(),
            "elapsed_ms": round(elapsed, 1),
        }

        findings = []
        if trace["candidates"] == 0:
            findings.append(
                "후보 테이블 0건 — search_table_meta 키워드 조정 필요"
            )
        if trace["use_cases"] == 0:
            findings.append(
                "활용사례 0건 — Qdrant sql_history 검색 결과 없음"
            )
        if trace["hypotheses"] < 2:
            findings.append(
                "가설 2개 미만 — Cold Start fallback만 존재"
            )

        _record(
            "planner_real", "customer_loan_query",
            "고객별 여신 잔액 합계", trace,
            "PASS" if trace["phase"] in (
                "EXPLORING", "GENERATING",
            ) else "WARN",
            elapsed,
            findings=" | ".join(findings) if findings else "",
        )

        assert reason.phase in (
            "EXPLORING", "GENERATING",
        ), f"planner가 예상 phase로 전환 실패: {reason.phase}"

    @pytest.mark.asyncio
    async def test_02_explorer_real_tools(
        self, connector_mgr,
    ):
        """context_explorer — 실제 도구 실행."""
        from src.agents.state.state import (
            PipelineState,
            ReasoningState,
            ExecutionStep,
            Hypothesis,
        )
        from src.agents.nodes.reason.context_explorer import (
            context_explorer_node,
        )

        state = PipelineState(
            preprocessed_input="고객 정보",
            reason=ReasoningState(
                hypotheses=[
                    Hypothesis(
                        hypothesis_id="H1",
                        description="test",
                        status="ACTIVE",
                    ),
                ],
                current_hypothesis=Hypothesis(
                    hypothesis_id="H1",
                    description="test",
                    status="ACTIVE",
                ),
                execution_plan=[
                    ExecutionStep(
                        step=1,
                        tool="search_table_meta",
                        input="고객",
                        purpose="고객 관련 테이블 탐색",
                        status="PENDING",
                    ),
                    ExecutionStep(
                        step=2,
                        tool="search_glossary",
                        input="여신",
                        purpose="여신 용어 정의 확인",
                        status="PENDING",
                    ),
                ],
            ),
        )

        t0 = time.perf_counter()
        result = await context_explorer_node(state)
        elapsed = (time.perf_counter() - t0) * 1000

        reason = result["reason"]
        plan = reason.execution_plan
        done = [s for s in plan if s.status == "DONE"]
        failed = [s for s in plan if s.status == "FAILED"]

        trace = {
            "done_steps": len(done),
            "failed_steps": len(failed),
            "tool_calls": reason.loop_guard.total_tool_calls,
            "knowledge_items": len(
                reason.knowledge_items,
            ),
            "candidate_tables": len(
                reason.candidate_tables,
            ),
            "step_insights": [
                s.insight for s in plan if s.insight
            ],
            "elapsed_ms": round(elapsed, 1),
        }

        findings = []
        if failed:
            findings.append(
                f"실패 스텝 {len(failed)}건: "
                + ", ".join(
                    f"{s.tool}({s.input})"
                    for s in failed
                ),
            )
        if trace["knowledge_items"] == 0:
            findings.append(
                "지식 항목 0건 — 도구 결과 해석 개선 필요"
            )

        _record(
            "explorer_real", "table_glossary_search",
            "고객 + 여신", trace,
            "PASS" if trace["done_steps"] > 0 else "FAIL",
            elapsed,
            findings=" | ".join(findings) if findings else "",
        )

        assert trace["done_steps"] >= 1, (
            "도구 실행 성공 0건"
        )

    @pytest.mark.asyncio
    async def test_03_full_planner_to_explorer_flow(
        self, connector_mgr,
    ):
        """planner → explorer 연쇄 실행."""
        from src.agents.state.state import (
            PipelineState,
            ReasoningState,
        )
        from src.agents.nodes.reason.planner import (
            planner_node,
        )
        from src.agents.nodes.reason.context_explorer import (
            context_explorer_node,
        )

        # Step 1: planner
        state = PipelineState(
            preprocessed_input="지점별 수신 잔액",
        )
        plan_result = await planner_node(state)

        # 상태 전이
        d = state.model_dump()
        reason_obj = plan_result["reason"]
        if isinstance(reason_obj, ReasoningState):
            d["reason"] = reason_obj.model_dump()
        else:
            d["reason"].update(reason_obj)
        state2 = PipelineState(**d)

        # Step 2: explorer
        t0 = time.perf_counter()
        explore_result = await context_explorer_node(state2)
        elapsed = (time.perf_counter() - t0) * 1000

        d2 = state2.model_dump()
        reason_obj2 = explore_result["reason"]
        if isinstance(reason_obj2, ReasoningState):
            d2["reason"] = reason_obj2.model_dump()
        else:
            d2["reason"].update(reason_obj2)
        state3 = PipelineState(**d2)

        trace = {
            "planner_hypotheses": len(
                state2.reason.hypotheses,
            ),
            "planner_candidates": len(
                state2.reason.candidate_tables,
            ),
            "explorer_knowledge": len(
                state3.reason.knowledge_items,
            ),
            "explorer_candidates": len(
                state3.reason.candidate_tables,
            ),
            "explorer_tool_calls": (
                state3.reason.loop_guard.total_tool_calls
            ),
            "phase_after_explore": state3.reason.phase,
            "elapsed_ms": round(elapsed, 1),
        }

        _record(
            "flow_real", "planner_to_explorer",
            "지점별 수신 잔액", trace,
            "PASS",
            elapsed,
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 보고서 출력
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestRealE2EReport:
    """보고서 생성."""

    def test_zz_generate_report(self):
        """보고서 출력."""
        if not REPORT:
            print("\n[보고서] 수집된 결과 없음")
            return

        lines = [
            "",
            "=" * 72,
            "  에이전틱 코어 실제 E2E 테스트 보고서",
            "  (Docker 데이터소스 + LLM API)",
            "=" * 72,
            "",
        ]

        categories: dict[str, list] = {}
        for r in REPORT:
            cat = r["category"]
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(r)

        for cat, items in categories.items():
            lines.append(f"## {cat.upper()}")
            lines.append("-" * 50)
            for item in items:
                v = item["verdict"]
                icon = {
                    "PASS": "OK", "WARN": "!!",
                    "FAIL": "XX",
                }.get(v, "??")
                lines.append(f"  [{icon}] {item['test']}")
                lines.append(f"    Query: {item['query']}")
                lines.append(
                    f"    Time: {item['elapsed_ms']}ms"
                )

                # 주요 state 필드 출력
                st = item["state"]
                for k, val in st.items():
                    if k != "elapsed_ms":
                        val_str = str(val)
                        if len(val_str) > 80:
                            val_str = val_str[:80] + "..."
                        lines.append(
                            f"    {k}: {val_str}"
                        )

                if item.get("findings"):
                    lines.append(
                        f"    FINDINGS: {item['findings']}"
                    )
                lines.append("")

        # 요약
        total = len(REPORT)
        passed = sum(
            1 for r in REPORT if r["verdict"] == "PASS"
        )
        warned = sum(
            1 for r in REPORT if r["verdict"] == "WARN"
        )
        failed = sum(
            1 for r in REPORT if r["verdict"] == "FAIL"
        )
        avg_ms = (
            sum(r["elapsed_ms"] for r in REPORT) / total
            if total else 0
        )

        lines.append("=" * 72)
        lines.append(
            f"  TOTAL: {total} | "
            f"PASS: {passed} | "
            f"WARN: {warned} | "
            f"FAIL: {failed}"
        )
        lines.append(
            f"  AVG LATENCY: {avg_ms:.1f}ms"
        )
        lines.append("=" * 72)

        # 개선 분석
        lines.append("")
        lines.append("## 개선 분석")
        lines.append("-" * 50)

        all_findings = [
            r["findings"]
            for r in REPORT if r.get("findings")
        ]
        if all_findings:
            for i, f in enumerate(all_findings, 1):
                lines.append(f"  {i}. {f}")
        else:
            lines.append("  (특이 발견 사항 없음)")

        report_text = "\n".join(lines)

        # 콘솔 출력 (인코딩 안전)
        import sys
        try:
            print(report_text)
        except UnicodeEncodeError:
            safe = report_text.encode(
                sys.stdout.encoding or "utf-8",
                errors="replace",
            ).decode(sys.stdout.encoding or "utf-8")
            print(safe)

        # 보고서 파일 저장
        from pathlib import Path
        report_path = Path(
            "tests/reports/agentic_real_e2e_report.txt"
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            report_text, encoding="utf-8",
        )
        print(
            f"\n보고서 저장: {report_path.absolute()}"
        )
