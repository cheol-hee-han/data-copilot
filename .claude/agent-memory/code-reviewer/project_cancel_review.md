---
name: cancel_complexity_review
description: Cancel/abort 코드 리뷰 결과 - 12개 노드 14회 보일러플레이트 산재, with_cancel_check 래퍼 중앙화 제안
type: project
---

Cancel 기능 설계는 양호하나 구현에서 "난개발" 발생. 핵심 이슈: 12개 노드에 14회 cancel 체크 보일러플레이트 + 3가지 cancel 응답 패턴 비일관.

**Why:** 설계 문서(7곳)보다 구현(14곳)이 과도하게 방어적으로 확장됨. 응답 dict가 노드마다 다름.

**How to apply:** C-01 제안: `with_cancel_check` 데코레이터를 `pipeline.py`의 `add_node`에 적용하여 중앙화. mid-node 체크(sql_validator Layer2b, context_interpreter Level1 루프)만 예외로 노드 내부 유지. 기존 리뷰(20260406-pipeline-cancel-implementation-review-report.md)는 보안/동시성 초점이므로 중복 아님.
