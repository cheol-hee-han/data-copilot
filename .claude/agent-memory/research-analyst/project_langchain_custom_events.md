---
name: LangChain Custom Events API 확정
description: adispatch_custom_event / on_custom_event 정확한 시그니처, 버전, LangGraph 노드 config 전파 패턴 확정
type: project
---

langchain-core 0.2.15부터 adispatch_custom_event(name, data, *, config) / on_custom_event 도입.
LangGraph 노드에서 config: RunnableConfig 두 번째 인수로 선언하면 callbacks 자동 주입.
Python 3.10 이하에서만 config 명시 필수 (3.11+는 contextvars 자동 추출, 명시 권장).
astream_events version="v2" 필수 — v1에서는 custom event 미노출.
parent_run_id 없는 컨텍스트(직접 함수 호출)에서 RuntimeError 발생 — 단위 테스트 주의.
AsyncCallbackHandler.on_custom_event(self, name, data, *, run_id, tags, metadata, **kwargs) -> None 오버라이드.
폐쇄망 텔레메트리 권장 패턴: adispatch_custom_event + AsyncCallbackHandler (LangSmith 대체).

**Why:** 폐쇄망 배포 환경에서 LangSmith 불가. BaseCallbackHandler 기반 커스텀 핸들러가 유일한 구조적 텔레메트리 수단.
**How to apply:** 노드 함수 시그니처에 config: RunnableConfig 명시, adispatch_custom_event에 config 전달, AsyncCallbackHandler 상속하여 on_custom_event 오버라이드.

보고서 위치: docs/research/20260330-langchain-custom-events.md
