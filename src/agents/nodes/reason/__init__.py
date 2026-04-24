"""Reasoning 단계 노드 패키지.

작성자: 한철희 / 최종수정: 2026-04-20 16:27:03

Agentic Core 구성 노드:
    - reasoning_preparer: reasoning 초기화 + 실행계획 생성
    - context_retriever: 실행계획 도구 실행 + 관찰 데이터 수집
    - context_interpreter: 배치 LLM 해석 + 상태 반영
    - readiness_gate: 확신도 평가 (rule-based)
    - sql_generator: 누적 지식 기반 SQL 생성
    - sql_validator: 3-레이어 SQL 검증
    - recovery_agent: ReAct-style 반응적 복구 루프
    - result_finalizer: 최종 출력 구성
"""
