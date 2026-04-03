---
name: HITL 명확화 트리거 통합 아키텍처
description: LangGraph 파이프라인의 5개 명확화 트리거를 단일 clarification_handler 노드 + Strategy 패턴으로 통합하는 권고 아키텍처 (2026-03-30)
type: project
---

5개 명확화 트리거(T1 history_unsure, T2 intent_ambiguous, T3 schema_conflict, T4 db_source, T5 finalizer_conflict)를 단일 `clarification_handler` 노드로 통합. interrupt()는 단일 호출 고정(인덱스 규칙), Strategy 패턴으로 핸들러 분리, ClarificationRequest 스키마가 UI 렌더링 명세 포함.

**Why:** interrupt() 다중 조건부 호출은 인덱스 불일치 유발(공식 문서 명시). 분산된 응답 스키마로 프론트엔드 부담 과중. resume 복귀 지점 불명확.

**How to apply:** 새 명확화 케이스 추가 시 ClarificationHandler 구현 + HandlerRegistry 등록만으로 완료. return_to 필드로 복귀 노드 지정. interrupt() 전 사이드이펙트 절대 금지.

보고서 위치: `docs/research/20260330-hitl-clarification-unification.md`
