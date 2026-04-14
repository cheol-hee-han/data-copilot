"""골든셋 배치 평가 실행기.

골든셋의 각 테스트 케이스를 파이프라인으로 실행하고,
DataCopilotCallbackHandler로 추론 과정을 자동 기록하며,
기존 evaluator로 정확도를 평가한 뒤 종합 보고서를 생성한다.

사용법:
    # 전체 골든셋 평가
    python -m evaluation.run_evaluation

    # 특정 골든셋 파일
    python -m evaluation.run_evaluation golden_set/golden_queries.json

    # 특정 케이스만 (ID 지정)
    python -m evaluation.run_evaluation --ids GS001 GS005 GS010

    # 카테고리 필터
    python -m evaluation.run_evaluation --category 고객

결과는 logs/traces/{batch_id}/ 에 저장된다:
    summary.json    - 종합 보고서
    failures.json   - 실패 케이스 상세
    traces/         - 개별 트레이스 파일
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from devtools.evaluation.evaluator import (
    evaluate_single,
    generate_report,
    load_golden_set,
    print_report,
)
from src.connectors.manager import get_connector_manager
from src.agents.graph.runner import run_pipeline
from src.utils.logger import get_logger, setup_logging

logger = get_logger(__name__)


async def evaluate_single_case(
    golden: dict,
) -> dict:
    """단일 골든셋 케이스를 평가한다.

    Returns:
        평가 결과 딕셔너리
    """
    case_id = golden["id"]
    user_input = golden["user_input"]

    logger.info("평가 시작", case_id=case_id, user_input=user_input[:50])

    try:
        # 파이프라인 실행 (내부에서 DataCopilotCallbackHandler 자동 생성)
        pipeline_result = await run_pipeline(
            user_input=user_input,
            session_id=case_id,
        )

        # PipelineResult.insight에서 intent/sql 추출
        insight = pipeline_result.insight or {}
        interp = insight.get("query_interpretation", {})
        actual_intent = interp.get("intent", "unknown")
        generated_sql = insight.get("sql_code", "")

        # evaluator로 정확도 평가
        eval_result = evaluate_single(
            golden=golden,
            actual_intent=actual_intent,
            generated_sql=generated_sql,
        )

        status_str = "PASS" if eval_result.passed else "FAIL"
        logger.info(
            f"평가 완료 [{status_str}]",
            case_id=case_id,
            passed=eval_result.passed,
            errors=eval_result.errors,
        )

        return {
            "case_id": case_id,
            "passed": eval_result.passed,
            "eval_result": eval_result,
        }

    except Exception as e:
        logger.error("평가 중 오류", case_id=case_id, error=str(e))

        return {
            "case_id": case_id,
            "passed": False,
            "error": str(e),
        }


async def run_batch_evaluation(
    golden_set: list[dict],
    batch_id: str = "",
) -> dict:
    """골든셋 전체를 배치 평가한다."""
    # 커넥터 초기화
    manager = get_connector_manager()
    await manager.connect_all()

    print(f"\n{'=' * 60}")
    print(f"골든셋 배치 평가 시작: {len(golden_set)}건")
    print(f"{'=' * 60}\n")

    results = []
    eval_results = []

    for i, golden in enumerate(golden_set, 1):
        print(f"[{i}/{len(golden_set)}] {golden['id']}: "
              f"{golden['user_input'][:50]}...")

        case_result = await evaluate_single_case(golden=golden)
        results.append(case_result)

        if "eval_result" in case_result:
            eval_results.append(case_result["eval_result"])

        status = "PASS" if case_result["passed"] else "FAIL"
        print(f"  → [{status}]")
        if not case_result["passed"]:
            eval_res = case_result.get("eval_result")
            if eval_res and hasattr(eval_res, "errors"):
                for err in eval_res.errors:
                    print(f"    {err}")

    # evaluator 보고서 출력
    if eval_results:
        report = generate_report(eval_results)
        print_report(report)

    # 요약 통계
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    summary = {
        "batch_id": batch_id,
        "summary": {
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "pass_rate": round(passed / total * 100, 1) if total else 0,
        },
    }
    _print_eval_summary(summary)

    return summary


def _print_eval_summary(summary: dict) -> None:
    """평가 요약 보고서를 콘솔에 출력한다."""
    print(f"\n{'=' * 60}")
    print("골든셋 평가 요약")
    print(f"{'=' * 60}")

    s = summary.get("summary", {})
    print(f"총 테스트: {s.get('total', 0)}건")
    print(f"통과: {s.get('passed', 0)}건 "
          f"({s.get('pass_rate', 0)}%)")
    print(f"실패: {s.get('failed', 0)}건")
    print(f"{'=' * 60}\n")


def main() -> None:
    """CLI 엔트리포인트."""
    parser = argparse.ArgumentParser(
        description="골든셋 배치 평가 실행",
    )
    parser.add_argument(
        "golden_path",
        nargs="?",
        default=None,
        help="골든셋 파일 경로 (기본: golden_queries.json)",
    )
    parser.add_argument(
        "--ids",
        nargs="+",
        default=None,
        help="평가할 케이스 ID 목록",
    )
    parser.add_argument(
        "--category",
        default=None,
        help="평가할 카테고리 필터",
    )
    parser.add_argument(
        "--batch-id",
        default="",
        help="배치 ID (기본: 타임스탬프)",
    )

    args = parser.parse_args()

    setup_logging()

    # 골든셋 로드
    golden_set = load_golden_set(args.golden_path)
    print(f"골든셋 {len(golden_set)}건 로드 완료")

    # 필터링
    if args.ids:
        golden_set = [
            g for g in golden_set if g["id"] in args.ids
        ]
        print(f"ID 필터 적용: {len(golden_set)}건")

    if args.category:
        golden_set = [
            g for g in golden_set
            if g.get("category") == args.category
        ]
        print(f"카테고리 필터 적용: {len(golden_set)}건")

    if not golden_set:
        print("평가할 케이스가 없습니다.")
        sys.exit(1)

    # 배치 평가 실행
    asyncio.run(
        run_batch_evaluation(
            golden_set=golden_set,
            batch_id=args.batch_id,
        )
    )


if __name__ == "__main__":
    main()
