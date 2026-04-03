---
name: LangGraph Checkpointer 아키텍처 리서치
description: AsyncPostgresSaver 프로덕션 권고, interrupt() 패턴, 스트리밍 모드, FastAPI lifespan 통합, 폐쇄망 적합성 확인
type: project
---

AsyncPostgresSaver가 프로덕션 권고 (LangSmith 인프라 사용 체크포인터). psycopg3 autocommit=True + prepare_threshold=0 필수.

**Why:** Data Copilot은 FastAPI + WebSocket 멀티턴 챗봇으로 대화 영속성, Human-in-the-Loop(SQL 승인), 폐쇄망 배포 조건을 모두 충족해야 함.

**How to apply:**
- FastAPI lifespan에서 AsyncConnectionPool 1회 생성 → AsyncPostgresSaver 주입 → graph.compile(checkpointer=...) 싱글턴
- interrupt()는 try/except 금지, 순서 일관성 유지, 이전 부수효과 멱등 설계
- 토큰 스트리밍은 stream_mode="messages" (astream_events 아님), 진행률은 stream_mode="custom"
- State 필드: messages는 add_messages 리듀서, 나머지는 단순 교체. Pydantic 객체는 model_dump() 후 저장
- 보고서: docs/research/20260330-langgraph-checkpointer-architecture.md
