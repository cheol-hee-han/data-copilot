"""LangGraph Studio 전용 엔트리포인트 — 로컬 시각적 디버깅 지원.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

langgraph dev 명령으로 LangGraph Studio를 실행할 때 이 모듈의 graph 변수를 참조한다.
Studio에서는 그래프 구조 시각화, 노드별 입출력 확인, 스텝 단위 실행 등이 가능하며,
개발 중 파이프라인 흐름을 시각적으로 디버깅하는 데 활용한다.
tracker=None으로 파이프라인을 컴파일하여 추적 오버헤드 없이 순수 그래프만 노출한다.

핵심 함수/클래스:
    - graph: create_app(tracker=None)으로 생성된 컴파일된 LangGraph 파이프라인 객체

설정 커스터마이징: langgraph.json에서 이 모듈 경로를 지정하여 Studio가 참조하도록 한다.
"""

from src.agents.graph.pipeline import create_app

graph = create_app()
