"""골든셋 배치 평가 실행기.

골든셋의 각 테스트 케이스를 파이프라인으로 실행하고,
EvaluationTracker로 추론 과정을 기록하며,
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

결과는 evaluation/traces/{batch_id}/ 에 저장된다:
    summary.json    - 종합 보고서
    failures.json   - 실패 케이스 상세
    traces/         - 개별 트레이스 파일
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from evaluation.evaluator import (
    evaluate_single,
    generate_report,
    load_golden_set,
    print_report,
)
from src.connectors.manager import get_connector_manager
from src.agents.graph.runner import run_pipeline
from src.utils.tracker import (
    BatchEvaluationTracker,
    EvaluationTracker,
)
from src.tools.langsmith import setup_langsmith
from src.utils.logger import get_logger, setup_logging

logger = get_logger(__name__)


async def evaluate_single_case(
    golden: dict,
    batch_tracker: BatchEvaluationTracker,
) -> dict:
    """단일 골든셋 케이스를 평가한다.

    Returns:
        평가 결과 딕셔너리
    """
    case_id = golden["id"]
    user_input = golden["user_input"]

    logger.info("평가 시작", case_id=case_id, user_input=user_input[:50])

    # 개별 트래커 생성
    tracker = EvaluationTracker(run_id=case_id)
    tracker.start_run(
        user_input=user_input,
        golden_id=case_id,
    )

    try:
        # 파이프라인 실행
        result = await run_pipeline(
            user_input=user_input,
            tracker=tracker,
        )

        # 파이프라인 결과에서 intent/sql 추출 (트래커에서)
        trace = tracker.trace
        actual_intent = trace.final_intent or "unknown"
        generated_sql = trace.sql.generated_sql or ""

        # evaluator로 정확도 평가
        eval_result = evaluate_single(
            golden=golden,
            actual_intent=actual_intent,
            generated_sql=generated_sql,
        )

        # 평가 결과를 트래커에 기록
        tracker.track_eval_result(
            passed=eval_result.passed,
            errors=eval_result.errors,
        )

        status_str = "PASS" if eval_result.passed else "FAIL"
        logger.info(
            f"평가 완료 [{status_str}]",
            case_id=case_id,
            passed=eval_result.passed,
            errors=eval_result.errors,
        )

        # 배치 트래커에 추가
        batch_tracker.add_trace(tracker)

        return {
            "case_id": case_id,
            "passed": eval_result.passed,
            "eval_result": eval_result,
            "trace": tracker.trace,
        }

    except Exception as e:
        logger.error("평가 중 오류", case_id=case_id, error=str(e))
        tracker.end_run(
            final_status="error",
            error_message=str(e),
        )
        tracker.track_eval_result(
            passed=False,
            errors=[f"실행 오류: {e!s}"],
        )
        batch_tracker.add_trace(tracker)

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
    batch_tracker = BatchEvaluationTracker(batch_id=batch_id)
    batch_tracker.start_batch()

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

        case_result = await evaluate_single_case(
            golden=golden,
            batch_tracker=batch_tracker,
        )
        results.append(case_result)

        if "eval_result" in case_result:
            eval_results.append(case_result["eval_result"])

        status = "PASS" if case_result["passed"] else "FAIL"
        print(f"  → [{status}]")
        if not case_result["passed"]:
            errors = (
                case_result.get("eval_result", {})
                if hasattr(case_result.get("eval_result", {}), "errors")
                else {}
            )
            if hasattr(errors, "errors"):
                for err in errors.errors:
                    print(f"    {err}")

    # 기존 evaluator 보고서 출력
    if eval_results:
        report = generate_report(eval_results)
        print_report(report)

    # 배치 트래커 보고서 저장
    save_path = batch_tracker.save()

    # 요약 통계 출력
    summary = batch_tracker.generate_summary()
    _print_tracker_summary(summary, save_path)

    return summary


def _print_tracker_summary(
    summary: dict, save_path: Path | None,
) -> None:
    """트래커 요약 보고서를 콘솔에 출력한다."""
    print(f"\n{'=' * 60}")
    print("추론 과정 분석 보고서")
    print(f"{'=' * 60}")

    s = summary.get("summary", {})
    print(f"총 테스트: {s.get('total', 0)}건")
    print(f"통과: {s.get('passed', 0)}건 "
          f"({s.get('pass_rate', 0)}%)")
    print(f"실패: {s.get('failed', 0)}건")
    print(f"오류: {s.get('errors', 0)}건")

    llm = summary.get("llm_stats", {})
    print(f"\nLLM 호출 통계:")
    print(f"  총 호출: {llm.get('total_calls', 0)}회")
    print(f"  총 토큰: {llm.get('total_tokens', 0):,}")
    print(f"  평균 지연: {llm.get('avg_latency_per_call_ms', 0):.1f}ms")

    sql = summary.get("sql_stats", {})
    print(f"\nSQL 통계:")
    print(f"  평균 재시도: {sql.get('avg_retry_count', 0):.2f}회")
    print(f"  최대 재시도: {sql.get('max_retry_count', 0)}회")

    node_durations = summary.get("avg_node_durations_ms", {})
    if node_durations:
        print(f"\n노드별 평균 소요시간:")
        for node, ms in sorted(
            node_durations.items(),
            key=lambda x: x[1],
            reverse=True,
        ):
            print(f"  {node}: {ms:.1f}ms")

    failures = summary.get("failure_reasons", {})
    if failures:
        print(f"\n실패 원인 분류:")
        for reason, count in sorted(
            failures.items(),
            key=lambda x: x[1],
            reverse=True,
        ):
            print(f"  {reason}: {count}건")

    paths = summary.get("execution_path_distribution", {})
    if paths:
        print(f"\n실행 경로 분포:")
        for path, count in sorted(
            paths.items(),
            key=lambda x: x[1],
            reverse=True,
        )[:5]:  # 상위 5개만
            print(f"  [{count}건] {path}")

    if save_path:
        print(f"\n상세 결과 저장 위치: {save_path}")
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
    setup_langsmith()

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
