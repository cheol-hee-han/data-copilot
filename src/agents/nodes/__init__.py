"""LangGraph 파이프라인 노드 모듈.

작성자: 한철희 / 최종수정: 2026-04-20 16:27:03

각 노드는 PipelineState를 입력받아 dict를 반환하는 얇은 함수로 구현된다.
비즈니스 로직은 services/ 계층에 위임하며, 노드는 다음 3가지만 수행한다:
  1. 상태에서 필요한 값을 꺼낸다
  2. 서비스를 호출한다 (프롬프트는 노드에서 주입)
  3. 결과를 상태에 넣는다

Outer Head 노드:
  - intent_classifier: 연속 여부 판정 + 의도 분류 통합
  - query_normalizer: 8-Slot 질의 정규화

명확화 노드 (interpret/ 내):
  - clarification_handler: 통합 명확화 (2계층 판정 + interrupt)

Agentic Core 노드 (agentic/ 서브패키지):
  - reasoning_preparer: 결정론적 reasoning 초기화 + 실행계획 생성
  - context_retriever: 실행계획 도구 실행 + 관찰 데이터 수집
  - context_interpreter: 배치 LLM 해석 + 상태 반영
  - readiness_gate: 확신도 평가 (rule-based)
  - sql_generator: 누적 지식 기반 SQL 생성
  - sql_validator: 3-레이어 SQL 검증
  - recovery_agent: ReAct-style 반응적 복구 루프
  - result_finalizer: 최종 출력 구성

Outer Tail 노드:
  - sql_executor: DB 쿼리 실행
  - analyzer: LLM 데이터 분석 + 시각화
  - formatter: 보고서 포맷팅
"""
