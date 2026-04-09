---
name: PostgreSQL 대화 이력 관리 설계 확정
description: Checkpointer(binary 불투명) vs 별도 테이블(JSONB 가독) 분리 확정, 하이브리드 2-계층 권고, 보고서 위치 포함
type: project
---

Checkpointer만으로는 금융권 감사 추적 요건 미충족 → 하이브리드 2-계층 패턴 권고.

**Why:** AsyncPostgresSaver의 checkpoint_blobs는 msgpack+JsonPlusSerializer BYTEA → SQL 조회 불가, 감사팀 직접 확인 불가. 별도 JSONB 테이블 필수.

**How to apply:**
- Layer 1 (현행 유지): AsyncPostgresSaver → 파이프라인 상태, interrupt/resume
- Layer 2 (신규): copilot.conversation_sessions + copilot.conversation_turns (월별 파티션, JSONB)
- Layer 3 (신규): audit.agent_actions (불변 감사 테이블, INSERT-only RLS, 월별 파티션)
- thread_id를 conversation_sessions.thread_id와 1:1 매핑하여 두 시스템 연결
- PostgresChatMessageHistory 단독 기각 (파이프라인 상태 중복 관리 복잡도)
- get_state_history() 직접 사용 기각 (긴 이력 역직렬화 최대 4초 지연)

보고서: docs/research/20260405-postgresql-conversation-history.md
