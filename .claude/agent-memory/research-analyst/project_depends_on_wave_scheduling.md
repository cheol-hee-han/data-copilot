---
name: depends_on + wave scheduling 패턴 확정
description: LLMCompiler(ICML 2024)가 depends_on DAG 패턴의 정식 선례. LangGraph 공식은 flat List[str]만 제공, wave grouping은 LLMCompiler 변형. Data Copilot 권고 패턴 및 보고서 위치 포함.
type: project
---

depends_on + wave topological sort는 LLMCompiler(Kim et al., ICML 2024, arXiv:2312.04511)에서 확립된 패턴이다.

**Why:** 사용자가 실행 플랜의 순차 의존성 처리 패턴을 선택해야 하는 설계 시점에 있음.

**핵심 사실:**
- LangGraph 공식 plan-and-execute: `List[str]` flat 순차, depends_on 없음
- LLMCompiler: `DEPENDS_ON: [id, ...]` 명시 + streaming eager dispatch (wave 아님)
- CrewAI: `context=[task_obj]` (depends_on의 다른 이름)
- Wave grouping은 LLMCompiler의 변형 — 원본은 의존성 충족 즉시 dispatch

**Data Copilot 권고:** depends_on + wave topological sort (패턴 A)
- 태스크 수 ≤10에서 streaming vs wave 성능 차이 무의미
- LLM 생성 용이한 flat JSON 구조
- LangGraph Send() + wave 분리 노드로 네이티브 구현 가능

**기각:** LLMCompiler streaming (구현 복잡), multi-round replanning (LLM 호출 증가), 정적 그래프 (동적 플랜 불가)

**보고서:** `docs/research/20260404-depends-on-wave-scheduling-pattern.md`

**How to apply:** 실행 플랜 설계 시 이 패턴을 기본으로 사용. wave 계산은 BFS 레벨 분리(보고서 내 구현 참조).
