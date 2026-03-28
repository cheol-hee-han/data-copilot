"""LangGraph 파이프라인 노드 모듈.

각 노드는 PipelineState를 입력받아 dict를 반환하는 얇은 함수로 구현된다.
비즈니스 로직은 services/ 계층에 위임하며, 노드는 다음 3가지만 수행한다:
  1. 상태에서 필요한 값을 꺼낸다
  2. 서비스를 호출한다 (프롬프트는 노드에서 주입)
  3. 결과를 상태에 넣는다

Outer Head 노드:
  - preprocessor: 입력 정규화 + 인젝션 감지
  - history_resolver: 대화 이력 판정
  - intent_classifier: LLM 의도 분류
  - query_normalizer: 8-Slot 질의 정규화
  - clarifier: 명확화 질문 생성

Agentic Core 노드 (agentic/ 서브패키지):
  - planner: 질의 분해 + 가설 수립 + 실행계획
  - context_explorer: 점진적 탐색 루프
  - confidence_evaluator: 확신도 평가 (rule-based)
  - sql_generator: 누적 지식 기반 SQL 생성
  - sql_validator: 3-레이어 SQL 검증
  - recovery_planner: 실패 분석 + 재계획
  - result_finalizer: 최종 출력 구성

Outer Tail 노드:
  - sql_executor: DB 쿼리 실행
  - analyzer: LLM 데이터 분석 + 시각화
  - formatter: 보고서 포맷팅
"""
