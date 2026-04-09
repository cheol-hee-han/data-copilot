---
name: LangGraph Tool-as-Node 패턴 분석
description: LangGraph에서 tool을 노드로 정의하는 공식 패턴 확정 (2026-04-04): 단일 ToolNode가 공식 권장, Data Copilot은 커스텀 노드 방식이 적합
type: project
---

단일 ToolNode가 공식 권장 패턴. 각 tool이 별도 그래프 노드가 되는 방식은 비권장.

**Why:** ToolNode는 LLM의 동적 tool 선택(tool_calls)을 내부 디스패치로 처리. 개별 tool 노드는 tool 수가 고정적이고 LLM 라우팅 없이 결정론적으로 호출될 때만 유효. Data Copilot처럼 순차 파이프라인 + State 직접 갱신이 필요한 경우는 커스텀 노드가 적합.

**핵심 결정사항:**
- ToolNode v1: 하나의 노드 안에서 모든 tool call을 asyncio.gather()로 병렬 실행
- ToolNode v2: Send API로 각 tool call을 별도 ToolNode 인스턴스에 분산 (tool_execution_type="v2")
- Data Copilot 권고: 순차 파이프라인의 각 단계를 독립 커스텀 노드로 정의 (ToolNode 아님)
  - 이유: State field 직접 갱신 필요, 순서 의존성, tool 수 고정, LLM 동적 선택 불필요
- LLM이 동적으로 tool을 선택하는 서브에이전트(SQL 디버깅 등)에는 ToolNode 적합

**How to apply:** 새 파이프라인 노드 설계 시 - LLM이 tool을 선택하는 구조인지 판단. 아니라면 커스텀 노드로. 맞다면 ToolNode 사용.

보고서: `docs/research/20260404-langgraph-tool-as-node-patterns.md`
