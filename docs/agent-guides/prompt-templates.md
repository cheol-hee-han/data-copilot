# 프롬프트 설계 가이드

## 시스템 프롬프트 구조

```
# src/agents/nodes/prompts/system-prompt-v1.md

## 역할
당신은 한국어 자연어 질의를 정확한 SQL로 변환하는 전문 AI입니다.
아래의 데이터베이스 스키마와 비즈니스 규칙을 반드시 준수하세요.

## 데이터베이스 스키마
{schema_context}  ← schema-architect의 llm-schema-summary.md

## 비즈니스 도메인 규칙
{domain_context}  ← domain-researcher의 business-rules.yaml

## SQL 생성 규칙
1. 반드시 SELECT 문만 생성하세요. INSERT/UPDATE/DELETE/DROP 등은 절대 금지.
2. 개인정보 컬럼은 마스킹: LEFT(col, 3) || '****'
3. 마케팅 관련 쿼리는 marketing_agree = TRUE 조건 필수.
4. 소프트 삭제 테이블은 WHERE deleted_at IS NULL 조건 포함.
5. 대용량 테이블(event_logs, clickstream)은 LIMIT 1000 기본 적용.
6. 인덱스가 있는 컬럼을 WHERE 조건 앞에 배치.

## 응답 형식
반드시 아래 JSON 형식으로만 응답하세요:
{
  "thinking": "사용자 질의 해석 과정 (한국어)",
  "sql": "생성된 SQL 쿼리",
  "explanation": "SQL이 하는 일 설명 (비기술적 언어)",
  "confidence": 0.0~1.0,
  "ambiguities": ["불명확한 부분이 있다면 목록으로"]
}
```

## 퓨샷(Few-Shot) 예제 라이브러리

### 기본 조회 예제
```yaml
- category: "가망고객 조회"
  difficulty: "basic"
  input: "이번 달 마케팅 가망고객 명세 뽑아줘"
  thinking: |
    - '가망고객' = customer_status = 'PROSPECT'
    - '이번 달' = DATE_TRUNC('month', NOW())
    - '명세' = 상세 정보 포함
    - 마케팅 쿼리이므로 marketing_agree = TRUE 필수
  sql: |
    SELECT customer_id, customer_name, phone, email, created_at AS 가입일
    FROM customers
    WHERE customer_status = 'PROSPECT'
      AND marketing_agree = TRUE
      AND DATE_TRUNC('month', created_at) = DATE_TRUNC('month', NOW())
      AND deleted_at IS NULL
    ORDER BY created_at DESC
  confidence: 0.95
```

### 집계 분석 예제
```yaml
- category: "매출 집계"
  difficulty: "intermediate"
  input: "지난 분기 상품 카테고리별 매출 순위"
  thinking: |
    - '지난 분기' = 이전 분기 전체 기간
    - '카테고리별' = products.category로 GROUP BY
    - '매출' = order_items.subtotal 합계
    - 취소된 주문 제외 필요
  sql: |
    SELECT p.category AS 카테고리,
           SUM(oi.subtotal) AS 총매출,
           COUNT(DISTINCT o.order_id) AS 주문건수,
           RANK() OVER (ORDER BY SUM(oi.subtotal) DESC) AS 순위
    FROM orders o
    JOIN order_items oi ON o.order_id = oi.order_id
    JOIN products p ON oi.product_id = p.product_id
    WHERE o.order_status != 'CANCELLED'
      AND o.created_at >= DATE_TRUNC('quarter', NOW() - INTERVAL '3 months')
      AND o.created_at < DATE_TRUNC('quarter', NOW())
    GROUP BY p.category
    ORDER BY 총매출 DESC
  confidence: 0.92
```

### 교차 분석 예제
```yaml
- category: "교차 분석"
  difficulty: "advanced"
  input: "구매 이력이 없는 가망고객 중 마케팅 동의한 사람"
  thinking: |
    - '구매 이력 없는' = NOT EXISTS 사용 (LEFT JOIN보다 성능 우수)
    - '가망고객' = customer_status = 'PROSPECT'
    - '마케팅 동의' = marketing_agree = TRUE
  sql: |
    SELECT c.customer_id, c.customer_name, c.email, c.phone, c.created_at AS 가입일
    FROM customers c
    WHERE c.customer_status = 'PROSPECT'
      AND c.marketing_agree = TRUE
      AND c.deleted_at IS NULL
      AND NOT EXISTS (
        SELECT 1 FROM orders o
        WHERE o.customer_id = c.customer_id AND o.order_status != 'CANCELLED'
      )
    ORDER BY c.created_at DESC
  confidence: 0.90
```

## 컨텍스트 주입 전략

### 동적 컨텍스트 선택
- **스키마 선택**: 질의 키워드로 관련 테이블 예측, 전체 스키마 대신 관련 테이블만 주입
- **도메인 규칙 선택**: 질의 도메인 분류 후 해당 도메인 규칙만 주입
- **퓨샷 예제 선택**: 질의 유형 분류 + 의미적 유사도 기반 2~3개 선택

### 토큰 예산 관리
| 구성 요소 | 토큰 예산 |
|----------|----------|
| 시스템 프롬프트 | ~1,000 |
| 스키마 컨텍스트 | ~2,000 (동적 선택) |
| 도메인 규칙 | ~1,000 (동적 선택) |
| 퓨샷 예제 | ~3,000 (2~3개) |
| 사용자 질의 | ~100 |

## 프롬프트 버전 관리

```
src/agents/nodes/prompts/
├── versions/
│   ├── v1.0.0/  ← 최초 버전
│   ├── v1.1.0/  ← 퓨샷 예제 추가
│   └── v1.2.0/  ← 마케팅 규칙 강화
├── current → v1.2.0
├── few-shot-examples.yaml
├── system-prompt.md
└── CHANGELOG.md
```
