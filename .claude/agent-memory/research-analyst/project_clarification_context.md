---
name: clarification_context_management
description: NL2SQL 명확화 컨텍스트 관리 리서치 - Approach B(Structured Context) 권고, 오류 전파 위험으로 Query Rewriting 기각, Hybrid 최종 설계 포함
type: project
---

Approach B (Structured Context Passing) 권고 확정 — 원본 질의 불변, 명확화 Q&A를 ClarificationEntry 리스트로 누적, SQL 생성 노드에 분리된 프롬프트 섹션으로 전달.

**Why:** CoE-SQL(NAACL 2024)에서 Query Rewriting 접근(ACT-SQL)이 오류 전파로 SParC -6.5% 열위 실증. Intent Scoping 논문(VLDB 2025)에서 paraphrase의 핵심 엔티티 소실 위험 경고. 금융 도메인 감사 요건상 원본 질의 보존 필수.

**How to apply:** AgentState에서 `original_query`는 immutable, `clarifications: list[ClarificationEntry]`로 누적. 다중 라운드(3회+) 또는 약한 모델에서만 선택적 Terminal Synthesis(AmbiSQL 패턴) 적용. 보고서 위치: `docs/research/20260330-clarification-context-management.md`
