# 컨텍스트 조립 가이드

> 최종 수정: 2026-03-20 (검색 쿼리 전략 모듈 반영)

## 참조 소스 및 우선순위

1. **ES 메타 검색** → 관련 테이블 레이아웃, 컬럼 설명, 코드 메타
2. **ES 보고서 저장소** → 기존 보고서 SQL과 요건 정보
3. **과거 SQL 이력** → 유사 요청에 대한 기존 검증된 SQL (Qdrant sql_history 벡터 검색)
4. **업무 매뉴얼** → 업무 규정, 계수산출식, 프로세스 (Qdrant biz_manual 벡터 검색)
5. **코드 메타** → 코드값 매핑 (ES, 전체 로드)
6. **도메인 사전** → 금융 용어 매핑, 비즈니스 규칙 (`finance_terms.py` 150+개)

## 검색 쿼리 전략 (SearchQueryBuilder)

`preprocessed_input`을 4개 소스에 동일하게 전달하지 않고, **소스별 최적화된 쿼리를 생성**한다.

```text
src/services/search_query_builder.py → build_source_queries()

preprocessed_input
  ├─ Step 1: 도메인 용어 매칭 (150+개 금융 용어 사전)
  ├─ Step 2: 구조화 엔티티 추출 (테이블명, 컬럼명, 카테고리)
  ├─ Step 3: 불용어 제거 (조사·어미·요청동사 60+개)
  ├─ Step 4: 동의어 확장 ("여신"→"대출","론","대여금")
  ├─ Step 5: 유사 테이블 신호어 수집
  └─ Step 6: 소스별 쿼리 특화
       ├─ ES table_meta:  domain_cd 주입 + 테이블명 부스트 + 시간어 제거
       ├─ ES report_sql:  시간 표현 제거 + 카테고리 보강
       ├─ History DB:     핵심 키워드 + 동의어 확장 + 테이블명 (15개 제한)
       └─ Qdrant manual:  원본 유지 + 도메인 설명 보강 (벡터 의미 강화)
```

### 소스별 전략 상세

| 소스 | 검색 메커니즘 | 전략 쿼리 특화 | 이유 |
| ---- | ------------ | ------------- | ---- |
| ES table_meta | multi_match + nori | domain_cd 선두 주입, 시간어 제거 | keyword 타입 table_name은 부분검색 불가, domain_cd로 필터링 |
| ES report_sql | multi_match + nori | 시간 표현 제거, 카테고리 보강 | 보고서는 업무 목적으로 검색되므로 자연어 유지 |
| Qdrant sql_history | 벡터 유사도 (cosine) | 원본 자연어 그대로 | description 임베딩과 의미적 유사성 극대화 |
| Qdrant biz_manual | 벡터 유사도 (cosine) | 도메인 용어 설명 보강 | 업무 질의형 검색에서 도메인 보강이 효과적 |

### domain_cd 매핑

```python
# src/services/search_query_builder.py
_CATEGORY_TO_DOMAIN_CD = {
    "고객": ["CUS"], "여신": ["LON"], "수신": ["DEP"],
    "거래": ["TRX", "DEP"], "카드": ["CRD"], "외환": ["FEX"],
    "금융지표": ["MGT", "LON"], "조직": ["CUS"],
}
```

## 토큰 예산

| 구성 요소 | 토큰 예산 | 비고 |
| --------- | --------- | ---- |
| 시스템 프롬프트 | 1,000 | 고정 |
| 스키마 컨텍스트 | 2,000 | 관련 테이블만 동적 선택 |
| 도메인 규칙 | 1,000 | 해당 도메인만 |
| 퓨샷 예제 | 3,000 | 2~3개 |
| 사용자 질의 | ~200 | 가변 |
| **총 예산** | **~7,200** | |

## 트리밍 우선순위

예산 초과 시 제거 순서: 퓨샷 예제 → 도메인 규칙 → 스키마 컬럼 상세

## 컨텍스트 품질 진단

SQL 생성 실패 시 아래 항목 점검:

- 필요한 테이블이 컨텍스트에 포함되었는가?
- 비즈니스 규칙(marketing_agree 등)이 포함되었는가?
- 퓨샷 예제가 질의 유형과 유사한가?
- search_query_builder가 올바른 domain_cd를 주입했는가? (로그 확인: `검색 쿼리 전략 적용`)
- Qdrant 벡터 검색에서 관련 카테고리 매뉴얼이 반환되었는가?

## 검증 결과 (골든셋 90건 E2E, 2026-03-20)

| 소스 | 적합도 |
| ---- | ------ |
| ES table_meta | 98.9% (89/90) |
| Qdrant sql_history | 85.6% (77/90) |
| Qdrant biz_manual | 88.9% (80/90) |
| **종합** | **91.1% (246/270)** |
