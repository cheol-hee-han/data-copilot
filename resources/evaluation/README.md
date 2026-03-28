# resources/evaluation/

정확도 평가용 골든셋. `evaluation/` 모듈과 `tests/`에서 사용된다.

## 파일 목록

| 파일 | 건수 | 용도 |
|------|------|------|
| `golden_queries.json` | — | 골든셋 — 기대 SQL이 포함된 정답 데이터 |
| `test_queries.json` | 90건 | 테스트 질의 — 다양한 패턴의 사용자 질의 |

## 사용처

| 사용처 | 파일 | 용도 |
|--------|------|------|
| `evaluation/evaluator.py` | 양쪽 모두 | 정확도 측정 (의미 동치, 실행 결과 일치) |
| `evaluation/run_evaluation.py` | `golden_queries.json` | CLI 평가 실행 |
| `tests/test_golden_set_context_quality.py` | `test_queries.json` | 컨텍스트 품질 자동 테스트 |

## 골든셋 강화 방법

### 케이스 추가 기준

1. **도메인 커버리지**: 여신/수신/외환/카드 등 업무 영역별 최소 5건
2. **복잡도 스펙트럼**: 단순 집계 → 다중 조인 → 서브쿼리 → 계수산출식
3. **엣지 케이스**: 모호한 질의, 코드값 필터, PII 포함, 대용량 테이블
4. **불완전 메타 시나리오**: 테이블 설명 없음, 코드값 정의 누락

### golden_queries.json 형식

```json
[
  {
    "query": "이번 달 지점별 신규 대출 건수",
    "expected_sql": "SELECT branch_nm, COUNT(*) ...",
    "expected_tables": ["TB_LOAN_EXEC", "TB_BRANCH"],
    "difficulty": "medium",
    "category": "여신",
    "tags": ["집계", "조인", "시계열"]
  }
]
```

### 평가 실행

```bash
# 전체 골든셋 평가
python -m evaluation.run_evaluation

# 특정 카테고리만
python -m evaluation.run_evaluation --category 여신
```

## 주의사항

- 골든셋의 `expected_sql`은 **PostgreSQL 문법** 기준 (개발 환경)
- 폐쇄망(Sybase IQ/Impala) 배포 시 dialect에 맞게 expected_sql 변환 필요
- 실제 DB 스키마 변경 시 골든셋도 함께 업데이트해야 평가가 유효
