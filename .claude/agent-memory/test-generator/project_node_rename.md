---
name: Pipeline Node Rename History
description: 파이프라인 노드명 변경 이력 — 테스트 작성 시 구 이름 사용 금지
type: project
---

파이프라인 노드명이 아래와 같이 변경되었다. 테스트 작성 시 반드시 현재 이름을 사용해야 한다.

| 구 이름 | 현재 이름 | 위치 |
|---|---|---|
| `execute_sql` | `sql_executor` | Present 계층 (present/sql_executor.py) |
| `normalize_query` | `query_normalizer` | Interpret 계층 (interpret/query_normalizer.py) |

**Why:** v6~v7 리팩터링 과정에서 노드 파일명/함수명 일관성 규칙(노드 이름 = 파일명 = 함수명 접미사 제외)에 맞춰 일괄 개명.

**How to apply:**
- 라우팅 테스트에서 노드명 기대값 작성 시 위 현재 이름 사용
- `_route_after_clarify`의 `_VALID_RETURN_TARGETS`는 `{"intent_classifier", "query_normalizer", "recovery_agent"}`임. 이 목록 외 노드는 `source_node`로 사용하면 `intent_classifier`로 폴백됨.
- 레거시 호환 매핑(`_LEGACY_TARGET_MAP`)은 구 intent_classifier 이름들만 포함하므로, `normalize_query` 같은 구 쿼리노마라이저 이름은 매핑되지 않고 invalid 처리됨.
