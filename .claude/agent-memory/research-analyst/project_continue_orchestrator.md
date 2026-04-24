---
name: CONTINUE orchestrator routing research
description: LangGraph NL2SQL CONTINUE 오케스트레이터 노드 설계 리서치 (2026-04-16): 턴 선택+라우팅 분류체계+SQL수정vs재생성+LangGraph Command 패턴 확정
type: project
---

라우팅 분류: RERUN(SQL 재실행+시각화), MODIFY_SQL(WHERE/GROUP BY 수정), MODIFY_SQL_NEW_META(메타 재조회+SQL 수정), ANALYZE_ONLY(기존 결과로 분석), NEW_QUERY(처음부터) 5개 카테고리.

**Why:** 모든 CONTINUE 쿼리를 reasoning_preparer에서 시작하면 불필요한 메타 검색·SQL 재생성이 발생. CoE-SQL(NAACL 2024)은 편집 체인(14개 unit edit operations)으로 이전 SQL 수정이 처음부터 생성보다 우월함을 SOTA 달성으로 증명. SParC 4개 컨텍스트 관계 분류(Refinement 33.8%, Theme-Entity 48.4% 등)가 턴 타입 분류 근거.

**How to apply:** continue_orchestrator 노드를 interpret 페이즈 이후에 위치. LangGraph Command(update+goto) 패턴으로 state 사전 주입 + 라우팅 동시 수행. 턴 참조는 LLM이 turn_snapshots 전체 리스트를 보고 참조 턴 인덱스 결정(CoE-SQL greedy minimization과 동일 원리). SQL 수정 vs 재생성 기준: edition chain 길이 임계값(≤3 편집 = 수정, >3 = 재생성). 보고서 위치: 최종 응답으로만 전달.
