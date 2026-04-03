# E2E 테스트 케이스 설계 — 파이프라인 경로 커버리지

> **작성일**: 2026-03-26
> **목적**: 다양한 파이프라인 분기 경로를 커버하는 real e2e 테스트 케이스 설계
> **실행 조건**: Docker 컨테이너 + LLM API (`pytest -m real_e2e`)
> **추적**: 각 테스트에서 `EvaluationTracker` + `trace_analyzer` 적용

---

## 1. 파이프라인 분기 경로 맵

```
사용자 입력
  ↓
[preprocess] → ERROR → (종료)
  ↓
[resolve_history] → AWAITING_CLARIFICATION → [clarify] → (종료)
  ↓
[classify_intent]
  ├─ CASUAL_TALK/META_QUESTION → [clarify] → (종료)      ← 경로 A
  ├─ DATA_EXTRACTION → [normalize] → [reason 루프]        ← 경로 B
  └─ DATA_ANALYSIS → [normalize] → [reason 루프] → [analyze] ← 경로 C

[reason 루프]
  [planner]
    ├─ Fast-Path → [generate_sql]                         ← 경로 B-1
    └─ Normal → [explore]                                 ← 경로 B-2
  [explore] → [evaluate]
    ├─ GENERATE → [generate_sql]
    ├─ EXPLORE → [explore] (반복)
    ├─ REPLAN → [recovery] → [explore]                    ← 경로 B-3
    ├─ ASK_USER → [finalize] → [clarify]                  ← 경로 B-4
    └─ TERMINATE → [finalize] → (실패 종료)               ← 경로 B-5
  [generate_sql] → [validate_sql]
    ├─ SUCCESS → [finalize] → [execute_sql]               ← 경로 B-6
    ├─ FAIL_SYNTAX → [fix_syntax] → [generate_sql] (재시도) ← 경로 B-7
    ├─ FAIL_SEMANTIC_LOCAL → [fix_local] → [generate_sql]  ← 경로 B-8
    └─ FAIL_STRUCTURAL → [replan] → [explore]              ← 경로 B-9

[execute_sql]
  ├─ DATA_EXTRACTION → [format_response] → (종료)
  └─ DATA_ANALYSIS → [analyze_data] → [format_response]   ← 경로 C-1
```

---

## 2. 테스트 케이스 설계

### 카테고리 1: 비데이터 질의 (경로 A) — 3건

| ID | 질의 | 기대 경로 | 검증 포인트 |
|---|---|---|---|
| A-01 | "안녕하세요" | intent=CASUAL_TALK → clarify → END | 의도 분류가 CASUAL_TALK, 이유 있음 |
| A-02 | "TB_LOAN_INFO 테이블에 어떤 컬럼이 있어?" | intent=META_QUESTION → clarify → END | 메타 질문 분류, SQL 생성 안 함 |
| A-03 | "여신이 뭐야?" | intent=META_QUESTION → clarify → END | 금융 용어 질문 분류 |

---

### 카테고리 2: 단순 추출 (경로 B, Happy Path) — 6건

| ID | 질의 | 기대 경로 | 검증 포인트 |
|---|---|---|---|
| B-01 | "전체 고객 수 알려줘" | 단일 테이블 집계 → SELECT COUNT | 정규화(MEASURE=COUNT), 단일 테이블, SQL 검증 통과 |
| B-02 | "이번 달 신규 여신 건수" | 단일 테이블 + 날짜 조건 | TIME_RANGE 추출, 기준일자 WHERE 조건 생성 |
| B-03 | "고객별 대출 잔액 합계" | 단일 테이블 GROUP BY | DIMENSION=고객, MEASURE=SUM(잔액), GROUP BY 생성 |
| B-04 | "지점별 수신 잔액 현황" | 다중 테이블 JOIN | 2테이블 조인 경로 확인, JOIN 키 매칭 |
| B-05 | "연체 고객 목록" | 코드값 필터링 | 코드메타 검색, WHERE 코드값 조건 |
| B-06 | "지난달 거래 건수와 금액" | 복수 MEASURE + 날짜 | COUNT+SUM 동시 집계, 월 단위 시간 조건 |

---

### 카테고리 3: 유사 테이블 구분 (3측면 보강 검증) — 4건

| ID | 질의 | 기대 경로 | 검증 포인트 |
|---|---|---|---|
| C-01 | "이번 달 여신 잔액" | TB_LN_BAL_D(일별) 선택, TB_LN_BAL_M(월별) 거부 | 3측면 비교 트리거, 날짜 분포 관찰, rejected 테이블 제거 |
| C-02 | "작년 월별 수신 잔액 추이" | 월별 테이블 선택 | 시간 조건과 적재주기 매칭 |
| C-03 | "현재 연체 대출 건수" | TB_LOAN_INFO(현재 상태) 선택, TB_LOAN_OVERDUE_STAT(통계) 구분 | entity_scope로 구분 |
| C-04 | "지점별 월말 대출 잔액 통계" | 월말 집계 테이블 선택 | functional_usage 기반 판정 |

---

### 카테고리 4: 복잡 추론 (다중 조인, 계수산출식) — 4건

| ID | 질의 | 기대 경로 | 검증 포인트 |
|---|---|---|---|
| D-01 | "VIP 고객의 지점별 여신 잔액 현황" | 3테이블 조인 (고객+여신+지점) | 조인 경로 3-way, 코드값 필터(등급) |
| D-02 | "1억 이상 대출 보유 고객 수" | 금액 단위 변환 + 필터 | 금액 "1억"→100000000 변환 확인 |
| D-03 | "지점별 연체율" | 계수산출식 추론 | 용어사전/매뉴얼에서 산출식 확인, MEASURE=DERIVED |
| D-04 | "고객 등급별 평균 대출 금액과 건수" | GROUP BY + 복수 집계 | 등급 코드값 조회, AVG+COUNT 동시 |

---

### 카테고리 5: 에지 케이스 / 방어 경로 — 5건

| ID | 질의 | 기대 경로 | 검증 포인트 |
|---|---|---|---|
| E-01 | "고객 전화번호 목록" | PII 마스킹 | SQL에 LEFT()+**** 마스킹, 직접 노출 차단 |
| E-02 | "전체 거래 내역 다 뽑아줘" | 대용량 방어 | 날짜 조건 + LIMIT 강제 적용 확인 |
| E-03 | "외환 파생상품 거래 현황" | 테이블 없음 → 재계획/실패 | 후보 테이블 0건, REPLAN 또는 TERMINATE 경로 |
| E-04 | "5천만원 이상이면서 연체 중인 대출" | 복합 조건 + 금액 단위 | 금액 변환 + 코드값 + AND 조건 |
| E-05 | "어제 물어본 지점별 현황에서 서울 지점만" | 멀티턴 (이력 해석) | history_resolver가 CONTINUE 판정, 이전 컨텍스트 반영 |

---

### 카테고리 6: 데이터 분석 (경로 C) — 3건

| ID | 질의 | 기대 경로 | 검증 포인트 |
|---|---|---|---|
| F-01 | "지점별 여신 잔액 비교 분석해줘" | DATA_ANALYSIS → analyze_data | 의도=분석, SQL 실행 후 분석 노드 진입 |
| F-02 | "최근 3개월 수신 추이 분석" | 추이 분석 + 시각화 판정 | 시계열 데이터 분석, 차트 유형 판정 (LINE) |
| F-03 | "연체율 높은 지점 Top 5 분석" | 순위 분석 + ORDER BY + LIMIT | 분석 인사이트 도출 확인 |

---

## 3. 파이프라인 경로 커버리지 매트릭스

| 경로 | 커버하는 테스트 |
|------|---------------|
| A (비데이터) | A-01, A-02, A-03 |
| B-1 (Fast-Path) | B-01 (단순 질의) |
| B-2 (Normal Explore) | B-02~B-06, C-01~C-04, D-01~D-04 |
| B-3 (REPLAN) | E-03 |
| B-4 (ASK_USER) | — (CONFLICTED 항목 발생 시, 자연 발생 의존) |
| B-5 (TERMINATE) | E-03 (테이블 없음 시) |
| B-6 (SQL 성공) | B-01~B-06, C-01~C-04, D-01~D-04 |
| B-7 (구문 재시도) | — (LLM 의존, 자연 발생 추적) |
| B-8 (의미 재시도) | — (LLM 의존, 자연 발생 추적) |
| B-9 (구조적 실패→재계획) | E-03 |
| C-1 (분석) | F-01~F-03 |
| 멀티턴 | E-05 |
| PII 마스킹 | E-01 |
| 대용량 방어 | E-02 |
| 금액 단위 변환 | D-02, E-04 |
| 유사 테이블 비교 | C-01~C-04 |
| 스키마명 포함 SQL | (폐쇄망 환경에서 전체) |

**총 25건** — 기존 10건에서 +15건 추가

---

## 4. 테스트 구현 설계

### 4.1 공통 헬퍼: 전체 파이프라인 실행 + 트레이스 수집

```python
async def _run_full_pipeline(
    query: str,
    conversation_history: list[dict] | None = None,
) -> tuple[PipelineResult, TraceReport]:
    """전체 파이프라인을 실행하고 트레이스를 분석한다."""
    tracker = EvaluationTracker(run_id=f"test_{uuid4().hex[:8]}")
    result = await run_pipeline(
        user_input=query,
        tracker=tracker,
        conversation_history=conversation_history,
    )
    tracker.save()
    trace_report = analyze_trace(tracker.save_path)
    return result, trace_report
```

### 4.2 검증 패턴

각 테스트에서 검증하는 항목:

```python
# 1. 파이프라인 완료 여부
assert result.status != QueryStatus.ERROR or expected_error

# 2. 의도 분류 정확성 (tracker decisions에서 확인)
intent_decision = next(
    d for d in tracker.trace.decisions
    if d.decision_type == "intent_classification"
)
assert intent_decision.chosen == expected_intent

# 3. SQL 생성/검증/실행 (tracker sql에서 확인)
if expected_sql_success:
    assert tracker.trace.sql.validated
    assert tracker.trace.sql.execution_success
    assert tracker.trace.sql.row_count > 0

# 4. 자동 보완점 도출
for finding in trace_report.findings:
    if finding.severity == "CRITICAL":
        pytest.fail(f"CRITICAL 발견: {finding.message}")

# 5. 특수 검증 (금액 변환, PII 마스킹 등)
if expected_masking:
    sql = tracker.trace.sql.generated_sql
    assert "LEFT(" in sql or "SUBSTR(" in sql
```

### 4.3 보고서 통합

모든 테스트 완료 후 `trace_analyzer.analyze_batch()`로 종합 보고서 생성:

```python
class TestFullReport:
    def test_zz_batch_analysis(self):
        report = analyze_batch(settings.eval_tracker_output_dir)
        print(report.summary)
        # 성공률 70% 미만이면 경고
        if report.success_rate < 0.7:
            print(f"WARNING: 성공률 {report.success_rate:.0%}")
```

---

## 5. 구현 우선순위

| 순서 | 테스트 | 근거 |
|------|--------|------|
| 1 | B-01~B-04 | 핵심 Happy Path — 기본 SQL 생성/실행 검증 |
| 2 | C-01~C-02 | 3측면 보강 검증 — 이번 세션의 핵심 구현 |
| 3 | D-02, E-04 | 금액 단위 변환 — 이번 세션 프롬프트 보강 검증 |
| 4 | E-01, E-02 | 보안/방어 — 금융 도메인 필수 요건 |
| 5 | A-01~A-03 | 비데이터 경로 — 라우팅 정확성 |
| 6 | F-01~F-03 | 분석 경로 — Present 계층 검증 |
| 7 | D-01, D-03~D-04 | 복잡 추론 — 도전적 케이스 |
| 8 | E-03, E-05 | 에지/실패 경로 — 방어 로직 검증 |
