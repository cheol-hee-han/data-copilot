"""해석 단계 노드 패키지.

작성자: 한철희 / 최종수정: 2026-04-20 16:27:03

자연어 질의 해석·명확화 단계 노드:
    - intent_classifier: 연속 여부 판정 + 의도 분류 (Outer Head)
    - continue_orchestrator: CONTINUE 4-way 라우팅 (redisplay/analyze/regenerate/refine)
    - query_normalizer: 자연어 → 8-Slot NormalizedQuery 정규화
    - clarification_handler: 통합 명확화 (2계층 판정 + interrupt)
"""
