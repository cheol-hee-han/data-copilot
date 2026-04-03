---
name: Turn Isolation Feature
description: turn_id 기반 턴 격리 기능의 테스트 커버리지 및 설계 결정 요약
type: project
---

turn_id 필드가 resolved_signals의 이전 턴 오염을 방지한다.

**Why:** 멀티턴 대화에서 이전 대화 턴의 resolved_signals가 현재 턴의 컨텍스트에 섞이면
라우팅 오류, 잘못된 SQL 프롬프트, 무한루프 방어 우회 등의 버그가 발생한다.

**How to apply:** turn_id를 사용하는 모든 함수 수정 시 아래 테스트가 회귀 방어막이 된다.

## 테스트 위치
`tests/auto/unit/test_turn_isolation.py` — 19개 테스트, LLM 미사용

## 커버리지 구간
| 클래스 | 대상 함수/구간 | 케이스 수 |
|---|---|---|
| TestBuildAutoResolvedNotice | build_auto_resolved_notice | 3 |
| TestBuildClarificationContext | build_clarification_context | 3 |
| TestRouteAfterClarify | _route_after_clarify | 4 |
| TestAskCountSessionWide | context_classifier ask_count (회귀) | 2 |
| TestEmptyTurnIdDefense | 빈 turn_id 방어 | 2 |
| TestClarificationHandlerInjectsTurnId | clarification_handler_node | 3 |
| TestQueryNormalizerSignalTurnId | query_normalizer T3 블록 | 2 |

## 핵심 설계 결정

- ask_count(무한루프 방어)는 의도적으로 세션 전체 카운트 — turn_id 필터 없음
- clarification_handler_node는 pending_signals에 turn_id를 in-place 주입
  (AmbiguitySignal에 frozen=True 추가 시 이 로직이 깨짐 — 주의)
- build_auto_resolved_notice / build_clarification_context는 turn_id가 빈 문자열이면 조기 반환
- _route_after_clarify는 resolved_signals의 마지막 현재 턴 시그널로 라우팅
