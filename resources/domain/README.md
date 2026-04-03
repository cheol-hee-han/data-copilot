# resources/domain/

금융 도메인 지식. LLM 프롬프트 보강, 검색 쿼리 확장, 유사 테이블 구분에 사용된다.

## 파일 목록

| 파일 | 용도 | 사용처 |
|------|------|--------|
| `business_dictionary.yaml` | 금융 용어 → DB 표현 매핑 | `domain_dictionary.py` → 검색 쿼리 확장 + SQL 생성 프롬프트 |
| `business_synonyms.yaml` | 동의어·약어 사전 (여신↔대출 등) | `query_normalizer.py` → 프롬프트 주입·약어 확장 |
| `business_categories.yaml` | 업무 카테고리 분류 | `search_query_builder.py` → 검색 범위 결정 |
| `output_templates.yaml` | 출력 템플릿 정의 | 결과 포맷팅 |
| `stopwords.yaml` | 검색 불용어 | `search_query_builder.py` → 검색 쿼리 정제 |
| `pii_columns.yaml` | PII 컬럼 정의 (금지/마스킹) | `sql_safety_checker.py` → SQL 내 PII 노출 차단 |
| `chart_config.yaml` | 차트 색상/폰트 설정 | `chart_generator.py` → 템플릿 SVG 스타일 |

## 강화 방법

### business_dictionary.yaml
가장 중요한 파일. 용어 매핑이 정확할수록 SQL 생성 정확도가 올라간다.

```yaml
# 현재 예시
신규 고객: "REG_DT가 해당 기간 내인 고객"
연체: "OVERDUE_YN = 'Y'"

# 강화: 실제 테이블/컬럼을 구체적으로 매핑
신규 고객: "TB_CUSTOMER.CUST_REG_DT >= 기준일"
연체: "TB_OVERDUE.OVDU_YN = 'Y' (TB_OVERDUE 테이블)"
BIS비율: "자기자본 / 위험가중자산 × 100 (TB_CAPITAL_ADEQUACY)"
```

### business_synonyms.yaml
사용자가 쓰는 표현과 DB 컬럼명 사이의 갭을 메운다.

```yaml
# 사용자 표현 → 검색 키워드
대출: [여신, 론, LOAN, 대부]
잔액: [잔고, 밸런스, BAL, 나머지]
```

## 데이터 수집 팁

- IT 메타 시스템에서 테이블/컬럼 목록을 추출하여 dictionary 자동 생성
- 업무 매뉴얼에서 금융 용어-산출식 매핑을 추출
- 기존 보고서 SQL에서 사용된 테이블/조건을 역분석하여 synonyms 보강
