---
name: LangGraph 실행 중단(Cancel/Abort) 패턴 조사 결과
description: asyncio.Task.cancel() + LangGraph 조합의 한계, 체크포인터 상태 보존 조건, 미해결 버그 목록, 권고 패턴
type: project
---

LangGraph 사용자 주도 중단은 공식 완전 해결 패턴이 없으며, 다수 버그가 미해결 상태 (2026-04-06 재확인, LangGraph 1.0 포함).

**체크포인터 상태 보존 조건:**
- superstep 완료 시점에만 checkpoint_writes 확정
- 실행 중 노드 출력은 cancel 시 손실
- AsyncPregelLoop.__aexit__ 중 CancelledError 재발생 버그 (Issue #6950) → 클린업 실패 위험

**미해결 버그:**
- Issue #5682: asyncio.Task.cancel() 시 서브그래프(ainvoke 호출)에 CancelledError 미전파 — LangGraph 0.3.5+
- Issue #6726: ToolNode handle_tool_errors=True가 CancelledError(BaseException) 미포착 → INVALID_CHAT_HISTORY
- Issue #6950: AsyncPregelLoop 클린업 중 CancelledError 재발생

**기각된 접근:**
- asyncio.Task.cancel() 직접: 서브그래프 미전파(#5682) + ToolNode 파손(#6726)
- SDK runs.cancel(): interrupt throw만 하며 실제 취소 아님, 프로덕션 404 오류 보고

**권고 패턴 (Data Copilot):**
- interrupt() 대기 중 사용자 취소 → thread 방기(abandon) — LangGraph 공식 메커니즘
- 노드 시작부 Redis 취소 플래그 체크 (패턴 C) — 폐쇄망에서 유일한 실용 대안
- astream() 사용 시 WebSocketDisconnect에서 for-loop break으로 자연스러운 취소

**Why:** 사용자 중단 버튼 구현 요건에서 발생. asyncio.Task.cancel()의 버그들로 직접 사용 위험.
**How to apply:** cancel 기능 구현 시 interrupt()+thread 방기 우선, 즉시 취소 필요 시 앱 레벨 플래그 병행.

보고서: docs/research/20260404-langgraph-cancel-abort-pattern.md
