# LangGraph Cancel/Abort 패턴 업데이트 리서치

- 작성일: 2026-04-06
- 작성자: Research Analyst Agent
- 분류: LangGraph / 취소 처리 / 버그 추적
- 선행 보고서: docs/research/20260404-langgraph-cancel-abort-pattern.md

---

## 1. 요약 (Executive Summary)

2026-04-04 리서치 이후 핵심 변화:

- **Issue #5682, #6726, #6950 전부 미해결** (2026-03-24 기준 모두 OPEN)
- #6726(ToolNode CancelledError)은 PR 3개(#6737, #6764, #6766)가 제출되었으나 아직 미병합
- #6950(AsyncPregelLoop cleanup)은 임시 핀 권고(1.0.8)만 존재, PR #7241 오픈 중
- #5682(서브그래프 미전파)는 PR #6775가 close된 채 이슈 REOPENED 상태
- LangGraph 1.0(2025-10) 공식 릴리즈에 cancel 전용 기능은 포함되지 않음
- LangGraph Platform 서버 changelog에서 `runs.cancel` gRPC 클라이언트 추가(v0.6.21, 2026-01-06)되었으나 self-hosted 비적용
- **공식 권장 패턴 변화 없음** — 앱 레벨 플래그(패턴 C)가 여전히 폐쇄망 유일 실용 대안

---

## 2. 조사 항목별 현황

### 2-1. LangGraph 공식 문서의 cancel/abort/stop 기능

공식 How-to 문서에 cancel 전용 가이드는 존재하지 않는다. LangChain 공식 문서에서 cancel과 관련된 콘텐츠는 다음 두 가지뿐이다:

1. **JS/TS 전용**: `langchain.js`의 [How to cancel execution](https://js.langchain.com/docs/how_to/cancel_execution/) — `AbortController` 기반, Python에 미적용
2. **interrupt 개념 문서**: 중단-재개(HITL) 패턴을 다루나 사용자 주도 cancel은 다루지 않음

Python LangGraph에 대한 "중단 버튼" 공식 가이드는 LangGraph 1.0 릴리즈(2025-10) 이후에도 추가되지 않았다.

### 2-2. astream() + break 패턴이 공식 권장되는가

**공식 권장은 아니나 구조적으로 가장 안전한 패턴이다.**

LangGraph 공식 문서는 `astream()` vs `ainvoke()` 취소 적합성에 대해 다음을 명시한다:

> "If you use stream or astream to run the graph, you will see an interrupt event when it was interrupted. However, if you use invoke or ainvoke, this information is not available as part of the response currently."

`astream()` 루프 내에서 `WebSocketDisconnect` 등 연결 해제 이벤트를 감지해 `break`하는 패턴은 커뮤니티에서 광범위하게 사용되나, 이것이 **서브그래프의 CancelledError 미전파 버그(#5682)를 해결하지는 않는다.** `astream()` 루프를 break해도 내부에서 `ainvoke()`로 호출된 서브그래프는 계속 실행될 수 있다.

### 2-3. interrupt()를 cancel 용도로 활용하는 공식 가이드

공식 문서에는 없다. `interrupt()`는 재개(resume)를 전제한 기능이며, "재개하지 않으면 사실상 cancel"이라는 것은 커뮤니티 관행이지 공식 가이드가 아니다.

다만 공식 HITL 문서에서는 interrupt() + thread 방기를 암묵적으로 허용하며, interrupted 상태 thread를 삭제하거나 새 thread를 시작하는 것이 프로덕션 패턴으로 언급된다.

**중요 제약 (변경 없음):** `interrupt()` 재개 시 해당 노드 전체를 처음부터 재실행한다. 노드 내 interrupt() 호출 이전 코드(DB 쓰기, 외부 API 호출 등)도 반복된다.

### 2-4. LangGraph Platform SDK runs.cancel() 현황

**플랫폼 서버 변경 이력 (Agent Server Changelog):**

| 버전 | 날짜 | 변경 내용 |
|------|------|----------|
| v0.2.73 | 2025-06-27 | 취소 중 deadlock 409 응답 처리 개선 |
| v0.6.21 | 2026-01-06 | Python gRPC 클라이언트에 Cancel 지원 추가 |

`runs.cancel()`의 동작 방식은 변경되지 않았다: "interrupt를 throw"하는 방식이며 실제 asyncio 태스크를 즉시 종료하지 않는다. SDK를 통한 cancel은 여전히 LangGraph Platform(호스팅) 환경 전용이며, **self-hosted 환경에서는 해당 API 엔드포인트가 없어 404를 반환한다** (LangChain Forum 보고, 2026년에도 동일 증상 유지).

### 2-5. 최근(2025~2026) 추가된 cancel 활용 가능한 신기능

LangGraph 1.0 GA(2025-10) 발표에서 강조된 4대 기능은 Durable Execution, Streaming, HITL, Background Jobs이며, cancel 전용 기능은 포함되지 않았다.

그 외 검토한 신기능:

| 기능 | cancel 활용 가능성 |
|------|------------------|
| `stream_version="v2"` (타입 안전 스트리밍) | 스트리밍 구조 개선이며 cancel에 직접적 영향 없음 |
| `checkpoint_during` 파라미터 | 노드 내부 중간 체크포인트 가능 — cancel 후 복구 개선 가능성 있으나 안정화 미완료 |
| Background Jobs (async run) | Platform 전용, self-hosted 미적용 |
| A2A 프로토콜 (v0.5.9+) | "interrupted" 상태를 A2A "input-required"로 매핑, 태스크 취소 지원 — Platform 전용 |

**`checkpoint_during` 파라미터**는 주목할 신기능이다. 노드 경계가 아닌 노드 내부에서도 중간 상태를 저장할 수 있어, cancel 시 상태 손실 범위를 줄일 수 있다. 그러나 2026-04 기준 PR/실험적 기능 수준이며 안정성 검증이 필요하다.

### 2-6. GitHub Issues #5682, #6726, #6950 해결 여부

| Issue | 최종 상태 (2026-03-24 기준) | 비고 |
|-------|---------------------------|------|
| #5682 | **OPEN (Reopened)** | PR #6775 close됨, FastAPI 재현 사례로 재오픈. 수정 미완료 |
| #6726 | **OPEN** | PR #6737, #6764, #6766 제출 중, 아직 미병합 |
| #6950 | **OPEN** | PR #6974, #7046(close), #7241(오픈). 임시 핀: 1.0.8 권장 |

세 이슈 모두 미해결이다. 특히 #6950은 프로덕션 LangSmith Cloud에서도 재현되는 intermittent 버그로, 임시 조치로 LangGraph 1.0.8 버전 핀이 제안된 상태다.

---

## 3. 이전 권고안 유효성 검토

2026-04-04 보고서의 권고 사항은 변경 없이 유효하다.

| 권고안 | 유효성 | 비고 |
|--------|--------|------|
| asyncio.Task.cancel() 직접 사용 기각 | 유효 | #5682 미해결 |
| SDK runs.cancel() 기각 (self-hosted) | 유효 | gRPC 클라이언트 추가되었으나 self-hosted 미적용 |
| interrupt() + thread 방기 | 유효 | 공식 HITL 메커니즘, 변경 없음 |
| 앱 레벨 Redis 취소 플래그 (패턴 C) | 유효 | 폐쇄망 유일 실용 대안 |
| astream() + for-loop break | 유효 | 단, 서브그래프 즉시 취소 보장 안 됨 |

---

## 4. Data Copilot 프로젝트 추가 주의사항

기존 권고 외 이번 조사에서 추가로 확인된 사항:

1. **LangGraph 버전 핀 권고**: Issue #6950이 intermittent하게 프로덕션 크래시를 유발하므로, 버전 업그레이드 시 1.0.8 이후 버전은 PR #7241 병합 여부를 확인 후 적용 권장
2. **서브그래프 존재 시 추가 주의**: Data Copilot이 내부적으로 서브그래프(ainvoke 방식)를 사용할 경우, 취소 신호 미전파로 서브그래프가 독립 실행될 수 있다. 서브그래프 내에서도 독립적인 앱 레벨 플래그 체크를 추가해야 한다.
3. **ToolNode 미사용 확인**: Data Copilot은 LangGraph 내장 ToolNode 대신 커스텀 노드 방식을 사용하므로 Issue #6726의 직접 영향은 없다 (project_langgraph_tool_node_patterns.md 참조). 다만 ToolNode를 사용하는 경우 PR 병합 전까지는 `CancelledError`를 명시적으로 except 처리가 필요하다.

---

## 5. 출처

### 공식 문서
- [LangGraph Interrupts 개념 문서](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [LangChain JS: How to cancel execution](https://js.langchain.com/docs/how_to/cancel_execution/)
- [Agent Server Changelog](https://docs.langchain.com/langsmith/agent-server-changelog)
- [LangGraph 1.0 General Availability Changelog](https://changelog.langchain.com/announcements/langgraph-1-0-is-now-generally-available)

### GitHub Issues / Discussions
- [Issue #5682: Can not stop sub graph when asyncio.CancelledError occurred](https://github.com/langchain-ai/langgraph/issues/5682) — OPEN (Reopened, 2026-03-24)
- [Issue #6726: handle_tool_errors=True does not catch asyncio.CancelledError](https://github.com/langchain-ai/langgraph/issues/6726) — OPEN, PR 3개 제출 중
- [Issue #6950: Random CancelledError in AsyncPregelLoop cleanup](https://github.com/langchain-ai/langgraph/issues/6950) — OPEN, 임시 핀 1.0.8 권장
- [Discussion #5356: How to abort a run using langgraph sdk](https://github.com/langchain-ai/langgraph/discussions/5356)

### 커뮤니티
- [LangChain Forum: Cancel graph run](https://forum.langchain.com/t/cancel-graph-run/2600)
- [LangChain Forum: Run cancellation 404 in production](https://forum.langchain.com/t/langgraph-run-cancellation-works-locally-but-returns-404-in-production/609)
- [Medium: LangGraph 1.0 released in October 2025](https://medium.com/@romerorico.hugo/langgraph-1-0-released-no-breaking-changes-all-the-hard-won-lessons-8939d500ca7c)
