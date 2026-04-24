# Phase 3 — Continue `handoff_note` 하류 소비 설계 (통합 이관)

작성일: 2026-04-20
상태: **이관 완료 — [20260418-continue-orchestrator-4way-redesign.md §14](20260418-continue-orchestrator-4way-redesign.md) 로 통합**

---

## 통합 안내

본 문서의 모든 설계 내용(방문 매트릭스 · 소비자별 프롬프트 변경안 · SKIP 근거 · 구현 순서 · NEW 턴 회귀 분석 · 체크리스트) 은 Single Source of Truth 원칙에 따라 **parent 설계 문서([20260418-continue-orchestrator-4way-redesign.md](20260418-continue-orchestrator-4way-redesign.md)) 의 §14** 로 이관되었다.

구현 시 본 문서가 아닌 parent 문서를 참조할 것.

### 이관된 섹션 매핑

| 본 문서 구 번호 | parent 문서 신규 위치 |
| --- | --- |
| §0 Phase 3 정체성 | §14.1 |
| §1 6관점 원칙 | §14.1.1 |
| §2 방문 매트릭스 | §14.2 |
| §3.1 query_normalizer | §14.3.1 |
| §3.2 context_interpreter | §14.3.2 |
| §3.3 recovery_agent (`{handoff_note}`) | §14.3.3 |
| §3.4 visualizer ANALYZE 가드 | §14.3.4 |
| §3.5 REGENERATE non-local_fix 차단 | §14.3.5 (+ §4.4.7 확장판) |
| §3.6 `{previous_sql}` 주입 | §14.3.6 (+ §3.3 / §4.4.3 / §4.6 반영) |
| §4 SKIP 노드 | §14.4 |
| §5 구현 순서 | §14.5 |
| §6 공통 가드레일 | §14.6 |
| §7 NEW 턴 회귀 | §14.7 |
| §8 미결 질문 | §14.8 |
| §9 체크리스트 | §14.9 |

### 이관 이유

- 구현 시 Phase 1+2+3 을 한 문서에서 참조 가능해야 cross-reference 누락 방지 (§3.3 hydration 매트릭스, §4.4.3 `_build_hydration_updates`, §4.4.7 가드, §4.6 sql_generator 주석은 Phase 3 설계와 직접 연결).
- 본 문서를 별도 유지 시 "두 문서 중 어느 것이 최신인가" 판단 비용 발생 — SSoT 위반.

본 문서는 이력 보존용 스텁으로만 유지한다. 편집 금지.
