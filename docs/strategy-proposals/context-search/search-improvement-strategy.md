# 검색 쿼리 전략 개선 TODO

> 골든셋 90건 E2E 테스트 결과 기반 (2026-03-20)
> 현재: ES 98.9% / sql_history 85.6% / biz_manual 88.9% / 종합 91.1%

## 우선순위 1: Qdrant payload filter로 카테고리/도메인 사전 필터링

- **대상**: sql_history + biz_manual
- **기대 효과**: +15~20%p (실패 23건 중 18건 해결 가능)
- **난이도**: 중
- **내용**:
  - sql_history 검색 시 `query_strategy`가 추출한 `domain_cd`를 Qdrant `Filter(should=[...])` 조건으로 전달
  - biz_manual 검색 시 카테고리를 필터 조건으로 제한
  - "고객 수 알려줘"(CUS) 질의에 TRX 도메인 SQL이 올라오는 현상 차단
- **근거**: sql_history 실패 13건 중 12건이 `TB_TRX_HST` 거래이력 SQL이 범용 표현("건수","금액")과 높은 유사도를 보여 도메인 무관하게 상위 반환

## 우선순위 2: sql_history 시딩 시 tables_used 필드 추가

- **대상**: sql_history (seed_qdrant.py)
- **기대 효과**: 하이브리드 검색(벡터+키워드) 가능
- **난이도**: 중
- **내용**:
  - 시딩 시 SQL에서 테이블명을 추출하여 `tables_used` 필드에 저장
  - 검색 시 벡터 유사도 + `tables_used` MatchAny 필터 병행
  - `query_strategy`가 추출한 `extracted_tables`를 필터 조건으로 활용

## 우선순위 3: 도메인 사전 커버리지 확장

- **대상**: finance_terms.py
- **기대 효과**: ES domain_cd 주입 + Qdrant 카테고리 매칭 커버리지 확대
- **난이도**: 하
- **추가 필요 용어**:
  - 카드: "발급", "한도", "업그레이드", "연회비"
  - 마케팅: "교차판매", "리파이낸싱", "이탈", "온보딩", "잠재고객"
  - 연금: "퇴직연금", "연금보험", "연금저축"
  - 거래: "채널", "ATM", "모바일뱅킹", "인터넷뱅킹"
- **근거**: EX-MKT-008 "퇴직연금" 미등록으로 ES 유일한 실패 발생

## 우선순위 4: Qdrant Two-stage 검색 (필터 → 폴백)

- **대상**: biz_manual
- **기대 효과**: 모호한 질의에서도 정밀도 유지
- **난이도**: 중
- **내용**:
  - 1단계: 카테고리 필터 + 벡터 검색 → 결과 있으면 반환
  - 2단계: 1단계 결과 부족(< 3건 또는 score < 0.3) → 필터 없이 전체 벡터 검색
  - 도메인이 명확한 질의는 정밀하게, 모호한 질의는 넓게

## 우선순위 5: Qdrant 전략 컬렉션별 분기 (P2-3 dilute 대응)

- **대상**: search_query_builder.py `_build_qdrant_query()`
- **기대 효과**: sql_history top-1 score 개선 (0.96→0.71 하락 방지)
- **난이도**: 하
- **내용**:
  - biz_manual: 도메인 설명 보강 유지 (업무 질의에 효과적)
  - sql_history: 원본 자연어 그대로 사용 (description 임베딩과 의미적으로 가까움)
  - `SourceQuery`에 `qdrant_manual_query`와 `qdrant_sql_history_query` 분리
- **근거**: "여신 실행 건수" raw=0.963 → strategy=0.714 (description concat으로 벡터 dilute)

## 우선순위 6: lookup_terms() false-positive 방어 (P1-1)

- **대상**: finance_terms.py `lookup_terms()`
- **기대 효과**: "고정금리"→"고정"(자산건전성) 오매칭 방지
- **난이도**: 중
- **내용**:
  - 최장 일치 우선(longest match first) 로직 추가
  - 매칭 결과에 confidence score 부여, threshold 이하 제외
  - 사전 300개 초과 확장 전 또는 실서비스 전환 전 필수
- **근거**: "고정금리 대출" → ASSET_HLTH_CD='30' 오매칭으로 4개 소스 쿼리에 오류 전파

## 우선순위 7: ES 시간 표현 패턴 동적 생성 (P2-4)

- **대상**: search_query_builder.py `_build_es_report_query()`
- **기대 효과**: 도메인 사전 시간 카테고리와 자동 동기화
- **난이도**: 하
- **내용**:
  - 현재 하드코딩된 시간 정규식을 도메인 사전 `category=="시간"` 용어에서 동적 생성
  - 사전에 시간 표현 추가 시 자동 반영
