"""LangGraph 파이프라인 노드 모듈.

각 노드는 PipelineState를 입력받아 dict를 반환하는 얇은 함수로 구현된다.
비즈니스 로직은 services/ 계층에 위임하며, 노드는 다음 3가지만 수행한다:
  1. 상태에서 필요한 값을 꺼낸다
  2. 서비스를 호출한다 (프롬프트는 노드에서 주입)
  3. 결과를 상태에 넣는다

노드 목록:
  - preprocessor: 입력 정규화 + 인젝션 감지
  - intent_classifier: LLM 의도 분류
  - query_normalizer: 8-Slot 질의 정규화
  - clarifier: 명확화 질문 생성
  - context_collector: 다중 소스 컨텍스트 수집
  - context_enricher: 테이블 설명 LLM 보강
  - sql_generator: LLM SQL 생성
  - sql_validator: 보안·구문·PII 검증
  - sql_executor: DB 쿼리 실행
  - analyzer: LLM 데이터 분석 + 시각화
  - formatter: 보고서 포맷팅
"""
