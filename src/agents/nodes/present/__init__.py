"""결과 표현 단계 노드 패키지.

작성자: 한철희 / 최종수정: 2026-04-20 16:27:03

사용자 응답 생성 단계 노드:
    - sql_executor: 검증된 SQL을 대상 DB에 실행하고 결과 적재
    - analyzer: LLM 데이터 분석 + 시각화 사양 생성
    - visualizer: 시각화 렌더링 사양 확정
    - formatter: 최종 보고서 포맷팅 (자연어 요약 + 테이블)
    - simple_responder: 비데이터 요청(인사·메타 질문) 직답
    - save_turn_snapshot: 턴 스냅샷 아카이빙 (CONTINUE 재활용용)
"""
