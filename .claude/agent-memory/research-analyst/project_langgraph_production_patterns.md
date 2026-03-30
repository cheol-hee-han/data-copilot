---
name: LangGraph Production Patterns
description: LangGraph graph.compile() 싱글턴 패턴 확정 (스레드 세이프), 폐쇄망 트레이싱은 BaseCallbackHandler + config 주입, 보고서 위치 포함
type: project
---

컴파일된 그래프는 스레드 세이프한 불변 객체 — 모듈/lifespan에서 1회 컴파일 후 전체 앱 공유가 공식 권장 (GitHub Discussion #1211, #1454).

**Why:** `graph.compile()`은 노드 연결 검증·사이클 탐지·경로 최적화를 수행하므로 요청별 재컴파일은 불필요한 오버헤드이며, 그래프 인스턴스에 상태가 저장되지 않아 공유해도 안전.

**How to apply:**
- `pipeline.py` 모듈 수준에서 `graph = builder.compile()` 후 FastAPI lifespan에서 `graph.checkpointer = saver` 사후 주입
- 트레이싱은 `graph.ainvoke(state, config={"callbacks": [handler], "run_id": ..., "tags": [...]})` 패턴
- 폐쇄망에서는 LangSmith 환경변수 방식 불가 → `BaseCallbackHandler` 커스텀 구현 + 내부 추적 시스템 연동
- `metadata["langgraph_node"]`로 콜백 핸들러 내부에서 현재 노드명 접근 가능

보고서 위치: `docs/research/20260330-langgraph-production-patterns.md`
