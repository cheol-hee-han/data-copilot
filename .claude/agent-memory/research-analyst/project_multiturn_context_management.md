---
name: multi-turn context management research
description: LangGraph+NL2SQL 멀티턴 컨텍스트 관리 패턴 리서치 (2026-04-16): 3계층 분리 구조 권고, TurnSnapshot 패턴, Dual-Channel State, CoE-SQL/Track-SQL 연구 근거
type: project
---

권고 아키텍처: Persistent Channel(conversation_history add_messages) + Ephemeral Channel(overwrite reducer) + TurnSnapshot(이전 턴 구조화 요약) 3계층 분리.

**Why:** turn_reset이 이전 턴 SQL/결과/테이블 선택 등 핵심 컨텍스트를 전부 소멸시키는 문제. full conversation_history 전달은 토큰 낭비. CoE-SQL(NAACL 2024)은 previous SQL+편집 체인만 전달로 sota 달성. Track-SQL(NAACL 2025)은 schema probability 누적+base SQL 선택적 참조로 +7.1%/+9.55% 개선. JetBrains 연구: observation masking이 LLM summarization보다 비용 52% 절감하면서 성능 동등/우월.

**How to apply:** TurnSnapshot = {sql, tables, columns, result_summary, clarifications}를 State에 Annotated[list, append] reducer로 누적. 노드 입력 시 latest snapshot만 주입(full history 아님). ephemeral 필드(reasoning_scratchpad 등)는 overwrite_reducer로 턴 간 소멸. LangGraph Store는 cross-thread(세션 간) 사용자 선호 저장 용도로 예약.

보고서 위치: 없음 (본 리서치는 최종 응답으로만 전달됨)
