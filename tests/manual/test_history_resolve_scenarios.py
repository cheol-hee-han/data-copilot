"""대화 이력 해소(resolve_history) 시나리오별 건별 평가 스크립트.

다양한 명확화/후속 질의 시나리오에 대해 LLM이 DECISION(CONTINUE/NEW/UNSURE)을
올바르게 판정하는지 건별로 평가하고 결과표를 출력한다.

사용법:
    uv run python tests/manual/test_history_resolve_scenarios.py
    uv run python tests/manual/test_history_resolve_scenarios.py -v
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@dataclass
class TestCase:
    """테스트 케이스 정의."""

    id: str
    category: str
    description: str
    history: list[dict[str, str]]
    current_input: str
    expected_decision: str  # CONTINUE | NEW | UNSURE
    awaiting_clarification: bool = False


# ──────────────────────────────────────────────────────────────
# 테스트 케이스 정의
# ──────────────────────────────────────────────────────────────

CASES: list[TestCase] = [
    # ── A. 후속 질의 (CONTINUE) ──
    TestCase(
        id="A1",
        category="후속 질의",
        description="지시대명사로 이전 질의 참조",
        history=[
            {"role": "user", "content": "이번 달 신규 고객 수 알려줘"},
            {"role": "assistant", "content": "이번 달 신규 고객은 총 1,234명입니다."},
        ],
        current_input="그 중에서 VIP 등급은 몇 명이야?",
        expected_decision="CONTINUE",
    ),
    TestCase(
        id="A2",
        category="후속 질의",
        description="짧은 조건 추가",
        history=[
            {"role": "user", "content": "지점별 여신잔액 현황 보여줘"},
            {"role": "assistant", "content": "(지점별 여신잔액 표)"},
        ],
        current_input="서울지점만",
        expected_decision="CONTINUE",
    ),
    TestCase(
        id="A3",
        category="후속 질의",
        description="추가/수정 표현",
        history=[
            {"role": "user", "content": "이번 달 신규 고객 중 VIP 고객 수 알려줘"},
            {"role": "assistant", "content": "VIP 등급 고객은 89명입니다."},
        ],
        current_input="지점별로 나눠서 보여줘",
        expected_decision="CONTINUE",
    ),
    TestCase(
        id="A4",
        category="후속 질의",
        description="제외 조건 추가",
        history=[
            {"role": "user", "content": "전체 대출 목록 보여줘"},
            {"role": "assistant", "content": "(대출 목록)"},
        ],
        current_input="연체 건은 빼줘",
        expected_decision="CONTINUE",
    ),

    # ── B. 명확화 답변 (CONTINUE) ──
    TestCase(
        id="B1",
        category="명확화 답변",
        description="번호 선택 응답",
        history=[
            {"role": "user", "content": "고객 관련해서"},
            {"role": "assistant", "content": "어떤 데이터가 필요하신가요?\n1) 고객 수\n2) 대출 잔액\n3) 직접 입력"},
        ],
        current_input="2번",
        expected_decision="CONTINUE",
        awaiting_clarification=True,
    ),
    TestCase(
        id="B2",
        category="명확화 답변",
        description="구체화 응답 (직접 입력)",
        history=[
            {"role": "user", "content": "데이터 좀 뽑아줘"},
            {"role": "assistant", "content": "어떤 데이터가 필요하신가요?\n1) 고객 정보\n2) 여신 정보\n3) 직접 입력"},
        ],
        current_input="이번달 신규 여신 건수",
        expected_decision="CONTINUE",
        awaiting_clarification=True,
    ),
    TestCase(
        id="B3",
        category="명확화 답변",
        description="테이블 명확화 응답",
        history=[
            {"role": "user", "content": "연체 현황 보여줘"},
            {"role": "assistant", "content": "건별 현황(TB_LOAN_INFO)과 월별 통계(TB_LOAN_OVERDUE_STAT) 중 어떤 데이터인가요?\n1) 건별 현황\n2) 월별 통계"},
        ],
        current_input="월별 통계",
        expected_decision="CONTINUE",
        awaiting_clarification=True,
    ),
    TestCase(
        id="B4",
        category="명확화 답변",
        description="UNSURE 명확화 '네' 응답",
        history=[
            {"role": "user", "content": "이번 달 신규 고객 수 알려줘"},
            {"role": "assistant", "content": "1,234명입니다."},
            {"role": "user", "content": "안녕"},
            {"role": "assistant", "content": "안녕하세요."},
            {"role": "assistant", "content": "혹시 이전에 대화했던 '이번 달 신규 고객 수 알려줘'에 이어서 질문하신 건가요?\n1) 네\n2) 아니요"},
        ],
        current_input="1번",
        expected_decision="CONTINUE",
        awaiting_clarification=True,
    ),

    # ── C. 새 독립 질의 (NEW) ──
    TestCase(
        id="C1",
        category="새 질의",
        description="완전히 다른 주제",
        history=[
            {"role": "user", "content": "이번 달 연체 현황 알려줘"},
            {"role": "assistant", "content": "(연체 현황 보고서)"},
        ],
        current_input="올해 수신 실적 추이 분석해줘",
        expected_decision="NEW",
    ),
    TestCase(
        id="C2",
        category="새 질의",
        description="명확화 중 새 질의",
        history=[
            {"role": "user", "content": "고객 관련해서"},
            {"role": "assistant", "content": "어떤 데이터가 필요하신가요?\n1) 고객 수\n2) 대출 잔액"},
        ],
        current_input="올해 연체율 추이 분석해줘",
        expected_decision="NEW",
        awaiting_clarification=True,
    ),
    TestCase(
        id="C3",
        category="새 질의",
        description="명확화 거부/취소",
        history=[
            {"role": "user", "content": "데이터 좀"},
            {"role": "assistant", "content": "어떤 데이터가 필요하신가요?\n1) 고객 정보\n2) 여신 정보"},
        ],
        current_input="됐어",
        expected_decision="NEW",
        awaiting_clarification=True,
    ),
    TestCase(
        id="C4",
        category="새 질의",
        description="인사 (이전 데이터 질의 후)",
        history=[
            {"role": "user", "content": "이번 달 신규 고객 수 알려줘"},
            {"role": "assistant", "content": "1,234명입니다."},
        ],
        current_input="감사합니다",
        expected_decision="NEW",
    ),

    # ── D. 모호한 맥락 (UNSURE) ──
    TestCase(
        id="D1",
        category="모호한 맥락",
        description="중간에 인사 턴 후 짧은 질의",
        history=[
            {"role": "user", "content": "이번 달 신규 고객 수 알려줘"},
            {"role": "assistant", "content": "1,234명입니다."},
            {"role": "user", "content": "안녕"},
            {"role": "assistant", "content": "안녕하세요. 무엇을 도와드릴까요?"},
        ],
        current_input="지점별은?",
        expected_decision="UNSURE",
    ),
    TestCase(
        id="D2",
        category="모호한 맥락",
        description="여러 이전 질의 중 어느 것인지 불분명",
        history=[
            {"role": "user", "content": "이번 달 신규 고객 수 알려줘"},
            {"role": "assistant", "content": "1,234명입니다."},
            {"role": "user", "content": "이번 달 대출 잔액도 알려줘"},
            {"role": "assistant", "content": "총 5조 2천억원입니다."},
        ],
        current_input="지점별로",
        expected_decision="UNSURE",
    ),
]


# ──────────────────────────────────────────────────────────────
# 실행 및 평가
# ──────────────────────────────────────────────────────────────

async def run_all_cases(verbose: bool = False) -> None:
    """모든 테스트 케이스를 실행하고 결과표를 출력한다."""
    from src.agents.nodes.prompts.system_prompts import (
        HISTORY_RESOLVE,
        HISTORY_RESOLVE_USER,
    )
    from src.services.history_resolver import resolve_history

    results: list[dict] = []

    for tc in CASES:
        try:
            r = await resolve_history(
                tc.current_input,
                tc.history,
                system_prompt=HISTORY_RESOLVE,
                user_template=HISTORY_RESOLVE_USER,
                awaiting_clarification=tc.awaiting_clarification,
            )
            actual = r.decision.value
            passed = actual == tc.expected_decision
            query = r.resolved_query
        except Exception as e:
            actual = f"ERROR: {e}"
            passed = False
            query = ""

        results.append({
            "id": tc.id,
            "category": tc.category,
            "description": tc.description,
            "input": tc.current_input,
            "expected": tc.expected_decision,
            "actual": actual,
            "passed": passed,
            "query": query,
        })

        if verbose:
            mark = "PASS" if passed else "FAIL"
            print(f"[{mark}] {tc.id}: {tc.description}")
            print(f"  입력: {tc.current_input}")
            print(f"  기대: {tc.expected_decision}, 실제: {actual}")
            if query and query != tc.current_input:
                print(f"  재작성: {query}")
            print()

    # ── 결과표 출력 ──
    _print_result_table(results)


def _print_result_table(results: list[dict]) -> None:
    """건별 평가 결과를 표 형태로 출력한다."""
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed = total - passed

    print(f"\n{'='*80}")
    print(f"대화 이력 해소 시나리오 평가 결과: {passed}/{total} 통과, {failed} 실패")
    print(f"{'='*80}")
    print(
        f"{'ID':<4} {'카테고리':<10} {'설명':<25} "
        f"{'입력':<15} {'기대':<10} {'실제':<10} {'결과':<5}"
    )
    print(f"{'-'*80}")

    for r in results:
        mark = "PASS" if r["passed"] else "FAIL"
        inp = r["input"][:12] + ".." if len(r["input"]) > 14 else r["input"]
        desc = r["description"][:23] + ".." if len(r["description"]) > 25 else r["description"]
        print(
            f"{r['id']:<4} {r['category']:<10} {desc:<25} "
            f"{inp:<15} {r['expected']:<10} {r['actual']:<10} {mark:<5}"
        )

    if failed > 0:
        print(f"\n{'─'*80}")
        print("실패 케이스 상세:")
        for r in results:
            if not r["passed"]:
                print(f"\n  [{r['id']}] {r['description']}")
                print(f"  입력: {r['input']}")
                print(f"  기대: {r['expected']}, 실제: {r['actual']}")
                if r["query"]:
                    print(f"  QUERY: {r['query']}")

    print(f"\n{'='*80}\n")


def main() -> None:
    """메인 엔트리포인트."""
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    asyncio.run(run_all_cases(verbose=verbose))


if __name__ == "__main__":
    main()
