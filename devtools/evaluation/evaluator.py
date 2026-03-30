"""골든셋 기반 SQL 정확도 평가 스크립트.

생성된 SQL을 골든셋의 정답과 비교하여 정확도를 측정한다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import sqlglot



@dataclass
class EvalResult:
    """개별 평가 결과."""

    query_id: str
    user_input: str
    passed: bool
    intent_match: bool = False
    table_match: bool = False
    pattern_match: bool = False
    semantic_match: bool = False
    errors: list[str] = field(default_factory=list)


@dataclass
class EvalReport:
    """전체 평가 보고서."""

    total: int = 0
    passed: int = 0
    failed: int = 0
    intent_accuracy: float = 0.0
    table_accuracy: float = 0.0
    pattern_accuracy: float = 0.0
    results: list[EvalResult] = field(default_factory=list)


def load_golden_set(path: str | None = None) -> list[dict]:
    """골든셋을 로드한다.

    Args:
        path: 골든셋 파일 경로. None이면 resources/evaluation/golden_queries.json 사용.
    """
    if path is None:
        from src.utils.resource_loader import RESOURCES_DIR

        path = str(
            RESOURCES_DIR / "evaluation" / "golden_queries.json"
        )
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_test_queries(path: str | None = None) -> list[dict]:
    """테스트 쿼리셋을 로드한다.

    Args:
        path: 테스트셋 파일 경로. None이면
              resources/evaluation/test_queries.json 사용.
    """
    if path is None:
        from src.utils.resource_loader import RESOURCES_DIR

        path = str(
            RESOURCES_DIR / "evaluation" / "test_queries.json"
        )
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def check_intent_match(expected: str, actual: str) -> bool:
    """의도 분류가 일치하는지 확인한다."""
    return expected.lower() == actual.lower()


def check_table_match(expected_tables: list[str], generated_sql: str) -> bool:
    """SQL에 필요한 테이블이 모두 포함되어 있는지 확인한다."""
    sql_upper = generated_sql.upper()
    return all(table.upper() in sql_upper for table in expected_tables)


def check_pattern_match(pattern: str, generated_sql: str) -> bool:
    """SQL이 예상 패턴과 일치하는지 확인한다."""
    if not pattern:
        return True
    return bool(re.search(pattern, generated_sql, re.IGNORECASE | re.DOTALL))


def check_sql_parseable(sql: str) -> tuple[bool, str]:
    """SQL이 유효한 구문인지 확인한다."""
    try:
        parsed = sqlglot.parse(sql, dialect="postgres")
        if parsed:
            return True, ""
        return False, "파싱 결과가 비어있음"
    except Exception as e:
        return False, str(e)


def _check_rejected(
    golden: dict,
    generated_sql: str,
    errors: list[str],
) -> bool:
    """부적합 유사 테이블이 사용되지 않았는지 검증한다."""
    rejected_tables = golden.get("rejected_tables", [])
    if not rejected_tables:
        return True

    # SQL에서 FROM/JOIN 절의 테이블명 추출
    matches = re.findall(
        r"(?:FROM|JOIN)\s+(\w+)", generated_sql, re.IGNORECASE,
    )
    used = {m.upper() for m in matches if m.upper().startswith("TB_")}

    rej_errors = [
        f"부적합한 유사 테이블 '{t}' 사용됨"
        for t in rejected_tables
        if t.upper() in used
    ]
    errors.extend(rej_errors)
    return len(rej_errors) == 0


def evaluate_single(
    golden: dict,
    actual_intent: str,
    generated_sql: str,
) -> EvalResult:
    """단일 골든셋 항목을 평가한다."""
    result = EvalResult(
        query_id=golden["id"],
        user_input=golden["user_input"],
        passed=False,
    )

    # 1. 의도 분류 매칭
    result.intent_match = check_intent_match(
        golden["expected_intent"], actual_intent
    )
    if not result.intent_match:
        result.errors.append(
            f"의도 불일치: expected={golden['expected_intent']}, actual={actual_intent}"
        )

    # 명확화 요청은 SQL이 필요 없음
    if golden["expected_intent"] == "clarification_needed":
        result.passed = result.intent_match
        return result

    # 2. 테이블 매칭
    if golden["expected_tables"]:
        result.table_match = check_table_match(
            golden["expected_tables"], generated_sql
        )
        if not result.table_match:
            result.errors.append(
                f"테이블 불일치: expected={golden['expected_tables']}"
            )

    # 3. 패턴 매칭
    if golden.get("expected_sql_pattern"):
        result.pattern_match = check_pattern_match(
            golden["expected_sql_pattern"], generated_sql
        )
        if not result.pattern_match:
            result.errors.append("SQL 패턴 불일치")

    # 4. SQL 구문 검증
    parseable, parse_error = check_sql_parseable(generated_sql)
    if not parseable:
        result.errors.append(f"SQL 구문 오류: {parse_error}")

    # 5. 부적합 유사 테이블 검증 (rejected_tables)
    no_rejected_used = _check_rejected(
        golden, generated_sql, result.errors,
    )

    # 종합 판정
    result.passed = (
        result.intent_match
        and result.table_match
        and (result.pattern_match or parseable)
        and no_rejected_used
    )

    return result


def generate_report(results: list[EvalResult]) -> EvalReport:
    """평가 결과 보고서를 생성한다."""
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    intent_correct = sum(1 for r in results if r.intent_match)
    table_correct = sum(1 for r in results if r.table_match)
    pattern_correct = sum(1 for r in results if r.pattern_match)

    return EvalReport(
        total=total,
        passed=passed,
        failed=total - passed,
        intent_accuracy=intent_correct / total if total > 0 else 0.0,
        table_accuracy=table_correct / total if total > 0 else 0.0,
        pattern_accuracy=pattern_correct / total if total > 0 else 0.0,
        results=results,
    )


def print_report(report: EvalReport) -> None:
    """평가 보고서를 출력한다."""
    print("\n" + "=" * 60)
    print("골든셋 평가 결과 보고서")
    print("=" * 60)
    print(f"총 테스트: {report.total}건")
    print(f"통과: {report.passed}건 ({report.passed / report.total * 100:.1f}%)")
    print(f"실패: {report.failed}건")
    print(f"의도 분류 정확도: {report.intent_accuracy * 100:.1f}%")
    print(f"테이블 선택 정확도: {report.table_accuracy * 100:.1f}%")
    print(f"SQL 패턴 정확도: {report.pattern_accuracy * 100:.1f}%")
    print("-" * 60)

    for r in report.results:
        status = "PASS" if r.passed else "FAIL"
        print(f"[{status}] {r.query_id}: {r.user_input}")
        if r.errors:
            for err in r.errors:
                print(f"       {err}")

    print("=" * 60)


if __name__ == "__main__":
    import sys

    golden_path = sys.argv[1] if len(sys.argv) > 1 else None
    golden_set = load_golden_set(golden_path)
    print(f"골든셋 {len(golden_set)}건 로드 완료")
    print(
        "평가를 실행하려면 파이프라인을 통해 "
        "actual_intent와 generated_sql을 생성하세요.",
    )
    print(
        "사용법: python -m evaluation.evaluator "
        "[golden_set_path]",
    )
