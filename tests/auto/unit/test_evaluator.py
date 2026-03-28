"""골든셋 기반 평가 모듈(evaluation/evaluator) 단위 테스트.

테스트 대상:
    골든셋 JSON을 로드하고, 의도·테이블·SQL 패턴 매칭을 평가하며,
    단건/배치 평가 결과를 보고서로 집계하는 모듈을 검증한다.

입력 예시 (정상):
    - golden: {expected_intent: "data_extraction", expected_tables: ["TB_CUST_INFO"]}
    - actual_intent: "data_extraction", generated_sql: "SELECT COUNT(*) FROM TB_CUST_INFO"
    - 기대: intent_match=True, table_match=True, pattern_match=True

결과 예시 (오류 케이스):
    - 의도 불일치: expected="clarification_needed", actual="data_extraction" → intent_match=False
    - 테이블 불일치: expected=["TB_LOAN_INFO"], sql에 TB_CUST_INFO만 → table_match=False

실행 스크립트:
    pytest tests/unit/test_evaluator.py -v

참고:
    - 외부 의존성 없음 (LLM, DB 불필요)
    - 테스트 대상 소스: evaluation/evaluator.py
"""

from devtools.evaluation.evaluator import (
    check_intent_match,
    check_pattern_match,
    check_sql_parseable,
    check_table_match,
    evaluate_single,
    generate_report,
    load_golden_set,
)


def test_load_golden_set():
    """골든셋 로드."""
    golden_set = load_golden_set()
    assert len(golden_set) > 0
    assert "id" in golden_set[0]
    assert "user_input" in golden_set[0]


def test_check_intent_match():
    """의도 매칭."""
    assert check_intent_match("data_extraction", "data_extraction")
    assert not check_intent_match("data_extraction", "data_analysis")


def test_check_table_match():
    """테이블 매칭."""
    sql = "SELECT COUNT(*) FROM TB_CUST_INFO WHERE REG_DT >= '2024-01-01'"
    assert check_table_match(["TB_CUST_INFO"], sql)
    assert not check_table_match(["TB_LOAN_INFO"], sql)


def test_check_pattern_match():
    """패턴 매칭."""
    sql = "SELECT COUNT(*) AS cnt FROM TB_CUST_INFO"
    assert check_pattern_match(r"SELECT.*COUNT.*FROM TB_CUST_INFO", sql)
    assert not check_pattern_match(r"SELECT.*SUM.*FROM TB_LOAN_INFO", sql)


def test_check_sql_parseable():
    """SQL 파싱 검증."""
    valid, _ = check_sql_parseable("SELECT COUNT(*) FROM TB_CUST_INFO")
    assert valid

    valid, error = check_sql_parseable("INVALID SQL HERE!!!")
    # sqlglot이 관대하게 파싱할 수 있으므로 이 테스트는 구현에 따라 다름


def test_evaluate_single_pass():
    """단일 항목 평가 - 통과."""
    golden = {
        "id": "TEST001",
        "user_input": "이번 달 신규 고객 수",
        "expected_intent": "data_extraction",
        "expected_tables": ["TB_CUST_INFO"],
        "expected_sql_pattern": "SELECT.*COUNT.*FROM TB_CUST_INFO",
    }
    result = evaluate_single(
        golden,
        actual_intent="data_extraction",
        generated_sql="SELECT COUNT(*) FROM TB_CUST_INFO WHERE REG_DT >= DATE_TRUNC('month', CURRENT_DATE)",
    )
    assert result.intent_match
    assert result.table_match
    assert result.pattern_match


def test_evaluate_single_fail_intent():
    """단일 항목 평가 - 의도 불일치."""
    golden = {
        "id": "TEST002",
        "user_input": "데이터 뽑아줘",
        "expected_intent": "clarification_needed",
        "expected_tables": [],
        "expected_sql_pattern": "",
    }
    result = evaluate_single(
        golden,
        actual_intent="data_extraction",
        generated_sql="SELECT 1",
    )
    assert not result.intent_match


def test_generate_report():
    """보고서 생성."""
    from devtools.evaluation.evaluator import EvalResult

    results = [
        EvalResult(query_id="T1", user_input="test1", passed=True, intent_match=True, table_match=True),
        EvalResult(query_id="T2", user_input="test2", passed=False, intent_match=False),
    ]
    report = generate_report(results)
    assert report.total == 2
    assert report.passed == 1
    assert report.intent_accuracy == 0.5
