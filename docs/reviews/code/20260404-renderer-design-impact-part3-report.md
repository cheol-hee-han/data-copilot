# 영향도 분석 Part 3: 하류 소비자 + 프롬프트

**날짜**: 2026-04-04
**대상**: `docs/working/tool-result-renderer-design.md` SS10 수정 대상 파일 중 하류 파일 + 프롬프트
**설계 핵심 변경**: `CandidateTable` -> `TableEntry`, `candidate_tables` -> `explored_tables`, dead field(`last_verdict`, `conflicted_bounce_count`) 제거, `is_inferred` 검토

---

## 1. src/agents/nodes/reason/sql_generator.py

```
참조 위치:
  L3   (docstring) candidate_tables 언급
  L8   (docstring) candidate_tables 언급
  L24  (docstring) CandidateTable 언급
  L39  (import) CandidateTable from src.agents.state.state
  L69  (함수 시그니처) _format_table_for_sql_prompt(ct: CandidateTable)
  L70  (docstring) CandidateTable 언급
  L81  (함수 시그니처) _format_table_header(ct: CandidateTable)
  L107 (함수 시그니처) _format_columns(ct: CandidateTable)
  L114 (함수 시그니처) _format_table_details(ct: CandidateTable)
  L153 (사용) reason.candidate_tables
  L165 (사용) reason.candidate_tables
  L176 (사용) reason.candidate_tables
  L228 (사용) reason.candidate_tables
  L261 (사용) reason.candidate_tables

변경 방법:
  - L39: import CandidateTable -> import TableEntry
  - L69, L81, L107, L114: 타입 힌트 CandidateTable -> TableEntry
  - L153, L165, L176, L228, L261: reason.candidate_tables -> reason.explored_tables
  - L3, L8, L24, L70: docstring 내 CandidateTable/candidate_tables 문자열 갱신
  - 함수명 _format_table_for_sql_prompt는 CandidateTable을 포함하지 않으므로 유지 가능

SS10 기재 여부: 있음
```

---

## 2. src/agents/nodes/reason/sql_validator.py

```
참조 위치:
  L231 (주석) candidate_tables 언급
  L235 (사용) reason.candidate_tables (Layer1 - 테이블 존재 검증)
  L250 (주석) candidate_tables 언급
  L336 (사용) reason.candidate_tables (Layer2b - _format_table_schema)

  주의: CandidateTable을 직접 import하지 않음.
        sql_generator._format_table_for_sql_prompt를 호출하여 간접 사용.

변경 방법:
  - L235, L336: reason.candidate_tables -> reason.explored_tables
  - L231, L250: 주석 내 candidate_tables 문자열 갱신

SS10 기재 여부: 있음
```

---

## 3. src/agents/nodes/reason/recovery_agent.py

```
참조 위치:
  L356 (import 필요 없음) SelectionStatus를 L356에서 로컬 import
  L358 (사용) reason.candidate_tables (후보 테이블 요약 순회)
  L392 (사용) "{candidate_tables_summary}" 문자열 키 (프롬프트 치환)
  L479 (사용) reason.candidate_tables (_build_sample_summary 순회)

변경 방법:
  - L358, L479: reason.candidate_tables -> reason.explored_tables
  - L392: 치환 키 "{candidate_tables_summary}" 는 프롬프트 파일의 placeholder와 1:1 대응.
    프롬프트 파일도 함께 변경하거나, 치환 키만 변경 시 양쪽 동기화 필수.
    권장: 프롬프트 파일 placeholder를 "{explored_tables_summary}"로 변경하고 코드도 동기화.

SS10 기재 여부: 있음
```

---

## 4. src/agents/nodes/reason/readiness_gate.py

```
참조 위치:
  L72  (사용) reason.last_verdict = verdict.value  [DEAD FIELD 쓰기]
  L91  (사용) stats['candidate_tables'] (추적 이벤트 문자열)
  L154 (사용) len(reason.candidate_tables) (_set_failure_context)
  L201 (사용) len(reason.candidate_tables) (_collect_stats)

변경 방법:
  - L72: reason.last_verdict = verdict.value 라인 삭제 (dead field 제거)
  - L91: 문자열 "candidate_tables" -> "explored_tables" (추적 로그 키)
  - L154, L201: reason.candidate_tables -> reason.explored_tables
  - _collect_stats 반환 딕셔너리의 키 "candidate_tables" -> "explored_tables"

SS10 기재 여부: 있음
```

---

## 5. src/agents/nodes/reason/result_finalizer.py

```
참조 위치:
  L9   (docstring) candidate_tables 언급
  L120 (사용) reason.candidate_tables (ContextInfo 구성 - CONFIRMED 테이블 추출)

변경 방법:
  - L120: reason.candidate_tables -> reason.explored_tables
  - L9: docstring 갱신

SS10 기재 여부: 있음
```

---

## 6. src/agents/nodes/reason/reasoning_preparer.py

```
참조 위치:
  L53  (사용) reason.last_verdict = None           [DEAD FIELD 초기화]
  L56  (사용) reason.conflicted_bounce_count = 0   [DEAD FIELD 초기화]

  참고: candidate_tables/CandidateTable 직접 참조 없음.

변경 방법:
  - L53: reason.last_verdict = None 라인 삭제
  - L56: reason.conflicted_bounce_count = 0 라인 삭제

SS10 기재 여부: 있음
```

---

## 7. src/services/insight_builder.py

```
참조 위치:
  L115 (사용) _get_attr_or_key(reason, "candidate_tables", [])  (_build_tables_used)
  L142 (사용) _get_attr_or_key(reason, "candidate_tables", [])  (_build_tables_candidate)
  L167 (사용) _get_attr_or_key(reason, "candidate_tables", [])  (_build_tables_rejected)
  L190 (사용) _get_attr_or_key(reason, "candidate_tables", [])  (_build_sql_summary)
  L218 (docstring) CandidateTable 언급 (_table_name)
  L264-265 (주석+사용) candidate_tables 언급 (_build_references)
  L403 (사용) _get_attr_or_key(reason, "candidate_tables", [])  (_build_caveats)

변경 방법:
  - L115, L142, L167, L190, L265, L403: 문자열 "candidate_tables" -> "explored_tables"
  - L218, L264: docstring/주석 갱신
  - SelectionStatus import (L15)는 src.models.enums에서 가져오므로 변경 불필요

  주의: insight_builder는 reason을 dict 또는 Pydantic 모델 양쪽으로 접근하므로
  _get_attr_or_key의 문자열 키가 state.py 필드명과 정확히 일치해야 한다.

SS10 기재 여부: 있음
```

---

## 8. src/services/confidence_scorer.py

```
참조 위치:
  직접 참조 없음.
  candidate_tables, CandidateTable, last_verdict, conflicted_bounce_count, is_inferred
  어느 것도 직접 접근하지 않는다.

  reason.knowledge_items, reason.explored_use_cases, reason.execution_plan만 접근.
  ReasoningState를 TYPE_CHECKING으로 import하므로 state.py 필드명 변경 시
  자동으로 반영된다.

변경 방법:
  - 직접 수정 불필요 (SS10 기재와 일치)

SS10 기재 여부: 있음 (직접 수정 불필요할 수 있음이라고 명시)
```

---

## 9. src/connectors/manager.py

```
참조 위치:
  L171 (docstring) candidate_tables 언급
  L188 (사용) reason.candidate_tables (DB 소스 라우팅)

변경 방법:
  - L188: reason.candidate_tables -> reason.explored_tables
  - L171: docstring 갱신

SS10 기재 여부: 있음
```

---

## 10. src/utils/tracker/callback_handler.py

```
참조 위치:
  L839-840 (사용) "candidate_tables_count" 문자열 키 + getattr(val, "candidate_tables", [])

변경 방법:
  - L839: 문자열 키 "candidate_tables_count" -> "explored_tables_count"
  - L840: getattr 키 "candidate_tables" -> "explored_tables"

SS10 기재 여부: 있음
```

---

## 11. src/utils/tracker/visualizer.py

```
참조 위치:
  L235 (사용) detail.get("candidate_tables", [])  (요약 렌더링)
  L352 (사용) detail.get("candidate_tables", [])  (판단 재료 상세)

변경 방법:
  - L235, L352: 문자열 "candidate_tables" -> "explored_tables"

  주의: 이 키는 tracking event의 detail 딕셔너리에서 읽으므로,
  이벤트 발행 측(readiness_gate의 dispatch_tracking_event)에서
  detail 딕셔너리의 키도 함께 변경해야 한다.
  현재 readiness_gate._collect_stats에서 "candidate_tables" 키를 사용하므로
  readiness_gate 변경과 동기화 필수.

SS10 기재 여부: 있음
```

---

## 12. src/agents/state/state.py

```
참조 위치:
  L85  (사용) is_inferred: bool = False  (KnowledgeItem 필드)
  L202 (정의) class CandidateTable(BaseModel)
  L235-236 (사용) CandidateTable 팩토리 메서드
  L460 (정의) candidate_tables: list[CandidateTable]
  L518 (정의) last_verdict: str | None = None     [DEAD FIELD]
  L523 (정의) conflicted_bounce_count: int = 0    [DEAD FIELD]

변경 방법:
  Phase 1 (네이밍):
    - L202: class CandidateTable -> class TableEntry
    - L235-236: 팩토리 메서드 반환 타입 갱신
    - L460: candidate_tables: list[CandidateTable] -> explored_tables: list[TableEntry]
  Phase 1 (dead field 제거):
    - L518: last_verdict 필드 삭제
    - L523: conflicted_bounce_count 필드 삭제
  is_inferred (L85):
    - KnowledgeItem.is_inferred 필드. 설계문서 SS10에서 명시적으로 언급되지 않음.
    - 현재 state.py에서만 정의되어 있고 다른 파일에서 읽는 곳이 grep 결과에 없음.
    - knowledge_interpreter에서 is_inferred=True로 설정하는 곳이 있을 수 있으므로
      구현 시 실제 사용 여부를 재확인 필요.

SS10 기재 여부: 있음
```

---

## 13. src/agents/state/__init__.py

```
참조 위치:
  L9  (re-export) CandidateTable
  L16 (re-export) CandidateTable

변경 방법:
  - L9, L16: CandidateTable -> TableEntry

SS10 기재 여부: 있음
```

---

## 14. resources/prompts/reason/knowledge_interpreter_system.txt

```
참조 위치:
  L1-3   (시스템 역할) "후보 테이블의 적합성을 판정" -- 직접 변경 불필요하나 용어 통일 시 갱신
  L35-39 (섹션) "## 후보 테이블 관찰 데이터" + {table_observations} placeholder
  L42-48 (분석 지침) 도구 결과별 해석 지시
  L84-91 (테이블 판정 기준) "(메타 원본) > (관찰) > (LLM 추론)" 출처 우선순위
  L93-98 (주의사항) entity_scope/functional_usage/data_refresh_hint 관찰 제한
  L110-149 (출력 형식) JSON 스키마 정의
  L154-269 (예시 1) 교차 참조 예시
  L273-343 (예시 2) Cold Start 예시

변경이 필요한 구체적 위치:

  (A) SS10 설계 변경에 의한 변경 (Phase 3):
    - 전면 갱신 대상. 현행 "## 도구 실행 결과" 섹션(L24-33)과
      "## 후보 테이블 관찰 데이터" 섹션(L35-39)이 분리되어 있는 구조가
      설계의 "스텝 단위 블록 조립" (purpose + result 한 블록)으로 통합됨.
    - {tool_results}, {table_observations} placeholder가 새 렌더러의 단일 블록 출력으로 대체.
    - "## 분석 지침" (L42-66)에서 도구별 판단 가이드가 렌더러 블록 내
      "->" 지시문으로 이동하므로 프롬프트 지침 단순화.
    - "## 출력 형식" (L110-149): new_tables의 entity_scope/functional_usage/
      data_refresh_hint 필드가 설계에서 유지되는지 확인 필요.
    - 예시 전체 (L154-343): 새 블록 형식에 맞게 전면 재작성.

  (B) 네이밍 변경 (Phase 1):
    - "후보 테이블" 용어를 "탐색 테이블"로 변경할지는 프롬프트 스타일에 따라 결정.
    - 프롬프트 내부 용어는 사용자 친화적이므로 "후보 테이블" 유지도 합리적.
    - 단, 출력 JSON 필드명에 candidate_tables가 없으므로 JSON 스키마 변경은 불필요.

SS10 기재 여부: 있음
```

---

## 15. resources/prompts/reason/recovery_agent_system.txt

```
참조 위치:
  L22  (placeholder) {candidate_tables_summary}
  L46  (도구 목록) get_date_distribution 설명
  L60  (우선순위) get_date_distribution 순위 6위
  L69  (input 형식) get_date_distribution input 예시
  L130 (예시 2) get_date_distribution 사용 예시

변경이 필요한 구체적 위치:

  (A) 네이밍 변경 (Phase 1):
    - L22: {candidate_tables_summary} -> {explored_tables_summary}
      코드(recovery_agent.py L392)와 동기화 필수.

  (B) get_date_distribution 관련 (Phase 2/3):
    - L46: 현행 설명은 "날짜 컬럼 분포 조회"로 충분.
      설계에서 ObservedDateColumn.recent_values 추가 시,
      "날짜 컬럼 분포 + 최근 실제 값 목록 조회"로 갱신 권장.
    - L60: 우선순위 6위는 현행 유지 가능.
      설계의 "모든 CandidateTable에 자동 날짜 분포 조회" 정책이
      fetcher에서 자동 수행되면, recovery에서 명시적 계획에 포함할 필요성 감소.
      가이드에 "날짜 분포는 초기 탐색에서 자동 조회됨. 추가 날짜 컬럼 확인이
      필요한 경우에만 계획에 포함" 문구 추가 권장.
    - L69: input 형식 변경 없음.
    - L130: 예시에서 get_date_distribution 사용은 "SQL 0건 후 날짜 포맷 확인" 시나리오로
      여전히 유효. 변경 불필요.

SS10 기재 여부: 있음
```

---

## 16. resources/prompts/reason/reasoning_preparer_system.txt

```
파일 존재 여부: 존재하지 않음.

reasoning_preparer_node는 LLM 호출 없이 rule-based로 동작하므로
system prompt 파일이 없다. SS10에 기재되어 있지만 실제 파일이 부재.

변경 방법:
  - 해당 없음. SS10에서 "get_date_distribution 계획 포함 지시 추가"라고 기재되어 있으나,
    reasoning_preparer는 프롬프트가 아닌 Python 코드(_build_execution_plan)에서
    결정론적으로 실행계획을 수립한다.
  - 설계 의도가 "초기 실행계획에 get_date_distribution을 포함"이라면,
    실제 변경 대상은 reasoning_preparer.py의 _build_execution_plan() 함수이다.
    다만 설계의 fetcher 리팩터링에서 "모든 테이블에 자동 날짜 분포 조회"가
    포함되므로 초기 계획에 명시적 스텝을 추가할 필요는 없을 수 있다.

SS10 기재 여부: 있음 (단, 파일이 존재하지 않으므로 SS10 오류)
```

---

## 추가 탐색: SS10에 없지만 변경이 필요한 파일

### Python 파일

`candidate_tables` 또는 `CandidateTable`을 참조하는 **모든** Python 파일 (13개) 중
SS10에 기재된 파일 외 추가 확인:

| 파일 | SS10 기재 | 비고 |
|------|----------|------|
| `src/agents/nodes/reason/knowledge_fetcher.py` | 있음 | 핵심 변경 대상 |
| `src/agents/nodes/reason/knowledge_interpreter.py` | 있음 | 핵심 변경 대상 |

--> **모든 Python 참조 파일이 SS10에 기재되어 있음. 누락 없음.**

### 프롬프트 파일

`candidate_tables`를 참조하는 txt 파일은 `recovery_agent_system.txt` 1개뿐.
`knowledge_interpreter_system.txt`에는 candidate_tables 문자열 자체는 없고
`{table_observations}` placeholder를 사용.

### 테스트 파일 (SS10에 "6개+"로 기재)

```
참조 파일 (6개):
  - tests/manual/e2e/test_agentic_real_e2e.py
  - tests/auto/e2e/test_agentic_e2e.py
  - tests/auto/unit/test_recovery_agent.py
  - tests/auto/e2e/test_agentic_core.py
  - tests/auto/e2e/test_agentic_flow_trace.py
  - tests/auto/unit/test_three_aspect_enrichment.py

변경 방법:
  - CandidateTable -> TableEntry (import + 인스턴스 생성)
  - candidate_tables -> explored_tables (fixture/mock 데이터)
  - last_verdict, conflicted_bounce_count 참조 있으면 삭제

SS10 기재 여부: 있음 (테스트 파일 6개+)
```

---

## Dead Field 영향도 요약

### last_verdict

| 파일 | 라인 | 동작 | 변경 |
|------|------|------|------|
| `state.py` | L518 | 필드 정의 | 삭제 |
| `readiness_gate.py` | L72 | 쓰기 | 라인 삭제 |
| `reasoning_preparer.py` | L53 | 초기화 | 라인 삭제 |

읽는 곳이 없음 -- 완전한 dead field 확인.

### conflicted_bounce_count

| 파일 | 라인 | 동작 | 변경 |
|------|------|------|------|
| `state.py` | L523 | 필드 정의 | 삭제 |
| `reasoning_preparer.py` | L56 | 초기화 | 라인 삭제 |

쓰기 외 사용처 없음 -- 완전한 dead field 확인.

### is_inferred

| 파일 | 라인 | 동작 | 변경 |
|------|------|------|------|
| `state.py` | L85 | KnowledgeItem 필드 정의 | 구현 시 사용 여부 재확인 |

grep 결과상 읽기/쓰기가 state.py 정의 외에 없음. 잠재적 dead field이나,
knowledge_interpreter에서 동적으로 설정될 가능성이 있으므로 Phase 1에서 삭제하지 않고
Phase 2/3에서 interpreter 리팩터링 시 확인 후 결정 권장.

---

## SS10 정합성 검증 결과

| 항목 | 상태 |
|------|------|
| Python 파일 누락 | 없음 -- 13개 전체 커버 |
| 프롬프트 파일 누락 | 없음 |
| 테스트 파일 누락 | 없음 (6개 식별, SS10에 "6개+"로 기재) |
| SS10 오류 | **1건** -- `reasoning_preparer_system.txt` 파일 부재. 실제 변경 대상은 `.py` 코드 |
| placeholder 동기화 필요 | **1건** -- `{candidate_tables_summary}` (recovery_agent.py <-> recovery_agent_system.txt) |
| tracking event 키 동기화 | **1건** -- readiness_gate._collect_stats -> visualizer.py 키 일치 필요 |

---

## Phase 1 (네이밍 변경) 영향 범위 체크리스트

Phase 1에서 변경이 필요한 파일 총 수:

| 카테고리 | 파일 수 | 파일 목록 |
|----------|---------|----------|
| 상태 정의 | 2 | state.py, __init__.py |
| 하류 노드 | 6 | sql_generator, sql_validator, recovery_agent, readiness_gate, result_finalizer, reasoning_preparer |
| 서비스 | 1 | insight_builder.py |
| 커넥터 | 1 | manager.py |
| 추적 | 2 | callback_handler.py, visualizer.py |
| 프롬프트 | 1 | recovery_agent_system.txt |
| 테스트 | 6 | (위 목록 참조) |
| **합계** | **19** | |

confidence_scorer.py는 직접 수정 불필요 (TYPE_CHECKING import만 사용).
