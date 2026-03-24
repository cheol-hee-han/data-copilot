# 스키마 문서화 가이드

## 테이블 메타데이터 문서 형식

각 테이블에 대해 아래 YAML 형식으로 상세 문서를 작성합니다:

```yaml
table: customers
alias: ["고객", "customer", "회원"]
description: "서비스에 가입한 모든 고객 정보"
primary_key: customer_id
row_count_estimate: "약 50만 건"
update_frequency: "실시간"

columns:
  - name: customer_id
    type: UUID
    description: "고객 고유 식별자"
    nullable: false

  - name: customer_status
    type: VARCHAR(20)
    description: "고객 상태"
    alias: ["상태", "status"]
    allowed_values:
      - value: "ACTIVE"
        description: "활성 고객 (최근 12개월 구매 이력 있음)"
        korean: "활성"
      - value: "PROSPECT"
        description: "가망고객 (관심 표현, 미구매)"
        korean: "가망고객"

  - name: marketing_agree
    type: BOOLEAN
    description: "마케팅 수신 동의 여부"
    note: "마케팅 대상 쿼리 시 반드시 TRUE 조건 추가 필요"

common_filters:
  - "활성 고객만": "WHERE customer_status = 'ACTIVE'"
  - "마케팅 대상": "WHERE customer_status != 'WITHDRAWN' AND marketing_agree = TRUE"
  - "이번 달 신규": "WHERE DATE_TRUNC('month', created_at) = DATE_TRUNC('month', NOW())"
```

## 테이블 관계도 및 조인 패턴 형식

```yaml
relationships:
  - name: "고객-주문"
    tables: [customers, orders]
    join_type: ONE_TO_MANY
    join_condition: "customers.customer_id = orders.customer_id"
    use_case: "고객별 구매 이력 조회"

common_join_patterns:
  - name: "구매 고객 상세"
    sql: |
      FROM customers c
      JOIN orders o ON c.customer_id = o.customer_id
      JOIN order_items oi ON o.order_id = oi.order_id
      JOIN products p ON oi.product_id = p.product_id
    purpose: "어떤 고객이 어떤 상품을 얼마나 구매했는지"
```

## LLM용 스키마 요약본 형식

프롬프트 토큰 효율을 위한 압축 형태:
```
**customers** (고객): customer_id(PK), customer_name(이름), customer_status(상태: ACTIVE/PROSPECT/INACTIVE/WITHDRAWN), created_at(가입일), marketing_agree(마케팅동의)
⚠️ 마케팅 쿼리 시 marketing_agree=TRUE 조건 필수
```

## 복잡도 경고 형식

```yaml
warnings:
  - type: "ambiguous_column"
    description: "name 컬럼이 여러 테이블에 존재"
    guidance: "SQL 생성 시 반드시 테이블 별칭으로 구분"

  - type: "soft_delete"
    description: "deleted_at 컬럼으로 소프트 삭제 구현"
    guidance: "조회 시 WHERE deleted_at IS NULL 조건 필수"

  - type: "performance_sensitive"
    description: "대용량 테이블 (1억 건 이상)"
    guidance: "반드시 파티션 키 조건 포함, LIMIT 적용 권장"
```

## 산출물 위치

```
docs/schema/
├── tables/                  # 테이블별 상세 YAML
├── relationships.yaml       # 조인 관계
├── llm-schema-summary.md    # LLM 프롬프트용 압축 버전
└── complexity-warnings.yaml # 복잡도 경고
```
