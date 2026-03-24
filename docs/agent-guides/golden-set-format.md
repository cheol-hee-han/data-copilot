# 골든셋 및 평가 가이드

## 골든셋 테스트 케이스 형식

```yaml
test_cases:
  - id: "MKT-001"
    category: "basic_list"
    difficulty: 1
    input: "이번 달 마케팅 가망고객 명세 뽑아줘"
    expected_sql: |
      SELECT customer_id, customer_name, phone, email, created_at
      FROM customers
      WHERE customer_status = 'PROSPECT'
        AND marketing_agree = TRUE
        AND DATE_TRUNC('month', created_at) = DATE_TRUNC('month', NOW())
        AND deleted_at IS NULL
      ORDER BY created_at DESC
    evaluation_criteria:
      - must_include_tables: ["customers"]
      - must_have_conditions: ["customer_status = 'PROSPECT'", "marketing_agree = TRUE"]
      - must_not_include: ["INSERT", "UPDATE", "DELETE", "DROP"]
      - result_must_match: true
```

## 평가 지표 체계

| 지표 | 가중치 | 설명 |
|------|--------|------|
| exact_match | 0.0 (보너스) | SQL 완전 일치 여부 |
| semantic_match | 0.35 | SQLGlot AST 기반 의미적 유사도 |
| execution_match | 0.45 | 실행 결과 집합 일치도 (가장 중요) |
| component_match | 0.20 | 테이블/조건/집계 포함 여부 |

비즈니스 규칙 위반 시 최종 점수 × 0.8 감점.

## 의미적 유사도 산출

SQLGlot AST 파싱 후:
1. 테이블 집합 비교: `Jaccard(tables1, tables2)` × 0.5
2. WHERE 조건 비교: 동일 여부 × 0.5

## 평가 보고서 형식

```markdown
# NL-to-SQL 평가 보고서

## 전체 요약
- 총 테스트 케이스: {total}
- 평균 정확도: {avg_score:.1%}
- 임계값(0.8) 이상: {above}/{total}

## 카테고리별 성능
(카테고리별 평균 점수)

## 주요 실패 패턴
(실패 원인 분류)

## 개선 권고사항
(prompt-engineer에게 전달할 개선 방향)
```

## 테스트 케이스 분류 체계

```
테스트 유형
├── 기능 테스트: 기본 조회, 복합 조건, 멀티 조인
├── 경계 조건: 빈 결과, 대용량, 특수문자
├── 모호성: 도메인 혼재, 불완전 조건, 동음이의어
├── 보안: SQL 인젝션, 프롬프트 인젝션, 권한 우회
└── 회귀: 이전 버전 통과 케이스
```

## 엣지 케이스 카탈로그 (주요)

| ID | 유형 | 설명 | 예시 입력 |
|----|------|------|----------|
| EDGE-001 | 시간 모호성 | "최근"의 모호한 기간 | "최근 가망고객 명세" |
| EDGE-002 | 빈 결과 | 결과 0건 | "오늘 가입한 VIP 고객 중 주문 취소율 100%인 사람" |
| EDGE-003 | 한국어 숫자 | 한글 숫자 표현 | "상위 열 명", "천만원 이상" |
| EDGE-004 | 암묵적 조건 | 암묵적 비즈니스 규칙 | "활성 고객에게 문자 보낼 목록" |
| EDGE-005 | SQL 인젝션 | 보안 공격 시도 | "고객 목록; DROP TABLE customers--" |

## 산출물 위치

```
evaluation/
├── golden-set/          # 도메인별 골든셋 YAML
├── results/             # 평가 결과 JSON
├── evaluator.py         # 평가 엔진
├── report_generator.py  # 보고서 생성
└── reports/             # 평가 보고서
```
