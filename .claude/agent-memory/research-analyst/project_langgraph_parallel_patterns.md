---
name: LangGraph 병렬 Fan-out/Fan-in 패턴 확정
description: LangGraph 정적 fan-out, Send API, defer=True, asyncio.gather 비교 및 Data Copilot 적용 권고 (2026-04-04)
type: project
---

컨텍스트 수집 병렬화는 정적 fan-out (add_edge 3개) 권고. Send API는 복수 SQL 후보 생성에 적합.

**Why:** 정적 fan-out은 분기 수 고정 + RetryPolicy 브랜치별 적용 + 체크포인팅 보장. asyncio.gather는 LangSmith 추적 불가 + 브랜치별 재시도 불가로 기각.

**How to apply:**
- 병렬 쓰기 대상 상태 키는 반드시 `Annotated[list, operator.add]` 지정 — 미적용 시 INVALID_CONCURRENT_GRAPH_UPDATE 오류
- fan-in 명시 시 `add_edge(["a","b","c"], "sink")` 한 가지 문법만 사용 (개별 edge와 혼합 금지 — issue #3249 버그)
- 비균형 브랜치(깊이 다른 병렬 분기)는 서브그래프로 캡슐화 — 미처리 시 sink 노드 중복 실행 (issue #6320)
- Send 사용 supervisor 노드는 `defer=True` 필수 — 미적용 시 빠른 브랜치 완료 시 supervisor 조기 재실행
- 동시 실행 수 제한: `graph.invoke(state, config={"max_concurrency": N})`
- 보고서 위치: docs/research/20260404-langgraph-parallel-fanout-fanin.md

**Reducer 사용 범위 추가 확정 (2026-04-04):**
- Reducer는 `operator.add`(리스트 누적)와 `add_messages`(메시지 누적) 두 가지만 공식 예시에 존재
- 복잡한 중첩 객체에 커스텀 reducer 적용은 공식 미지원 + 버그 레포트 다수(이슈 #1546, #3587) — 안티패턴으로 간주
- 순서 보장·구조 제어 필요 시: 브랜치가 `{"source": "meta", "data": ...}` 형태 dict를 Annotated 리스트에 append하고, sink 노드 Python 코드에서 source별 필터링·구조화
- Reducer는 "병합 로직 그릇"이 아닌 "동시 쓰기 충돌 방지 장치"로 설계됨 — 병합 로직은 sink 노드에 위치
- 보고서 위치: docs/research/20260404-langgraph-reducer-vs-separate-fields.md
