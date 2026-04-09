# 구현 준비 점검 -- 영향도 분석 Part 4: 테스트 + 죽은 코드 + 프로세스 충실도

**일자**: 2026-04-04  
**대상 설계문서**: `docs/working/tool-result-renderer-design.md`  
**분석 범위**: 테스트 영향도, 죽은 코드 예측, LangGraph 프로세스 충실도

---

## Part A: 테스트 파일 영향도

### A.1. 심볼별 테스트 참조 현황

#### `CandidateTable` (6개 테스트 파일)

| 파일 | 라인 | 용도 |
|------|------|------|
| `tests/auto/e2e/test_agentic_core.py` | 31, 416, 428 | import + CandidateTable 인스턴스 생성 |
| `tests/auto/e2e/test_agentic_e2e.py` | 27, 141-142, 353-354, 755-756 | import + 헬퍼 함수 반환 타입 + 인스턴스 생성 (7개소) |
| `tests/auto/e2e/test_agentic_flow_trace.py` | 23, 460, 501, 654 | import + 인스턴스 생성 (3개소) |
| `tests/auto/unit/test_recovery_agent.py` | 256, 265 | 로컬 import + 인스턴스 생성 |
| `tests/auto/unit/test_three_aspect_enrichment.py` | 19, 186-480 (30+개소) | import + 광범위한 CandidateTable 테스트 (qualified_name, from_meta 등) |
| `tests/manual/e2e/test_agentic_real_e2e.py` | (candidate_tables 참조) | 실행 결과 검증 |

#### `candidate_tables` (7개 테스트 파일)

| 파일 | 라인 | 용도 |
|------|------|------|
| `tests/auto/e2e/test_agentic_core.py` | 427, 447, 449 | ReasoningState 생성 + 결과 assertion |
| `tests/auto/e2e/test_agentic_e2e.py` | 177, 352, 385, 391, 757-758 | ReasoningState 생성 + assertion |
| `tests/auto/e2e/test_agentic_flow_trace.py` | 114-115, 127, 160, 242, 459, 500, 653, 674 | 추적 검증 + ReasoningState 생성 |
| `tests/auto/unit/test_recovery_agent.py` | 264 | ReasoningState 생성 |
| `tests/manual/e2e/test_agentic_real_e2e.py` | 337, 341, 438-439, 522, 528 | 결과 검증 |
| `tests/test_cases/agentic_e2e_test_catalog.json` | 163 | 기대값 문자열 내 참조 |
| `tests/reports/agentic_real_e2e_report.txt` | 72 | 리포트 출력 (자동 생성) |

#### `is_inferred`, `conflicted_bounce_count`, `last_verdict`

**테스트에서 참조 없음** -- 3개 dead field 모두 테스트 코드에서 사용되지 않는다.

#### `_serialize_tool_results`, `_apply_tool_result`, `BatchInterpretResult`

**테스트에서 참조 없음** -- private 함수와 내부 클래스 모두 단위 테스트가 존재하지 않는다.

### A.2. 파일별 변경 범위 요약

| 테스트 파일 | 변경 범위 | 난이도 |
|------------|----------|--------|
| **test_three_aspect_enrichment.py** | **가장 큰 영향**. CandidateTable 인스턴스 30+개소 -> TableEntry 치환. `candidate_tables` -> `explored_tables` 필드명 치환. from_meta, qualified_name 등 메서드 테스트도 새 클래스명 반영 필요. | 높음 |
| **test_agentic_e2e.py** | CandidateTable import/사용 7개소, candidate_tables 필드 5개소 치환. `_ct` 헬퍼 함수의 반환 타입 변경. | 중간 |
| **test_agentic_flow_trace.py** | CandidateTable 4개소, candidate_tables 8개소 치환. 추적 로깅 문자열 "candidate_tables_count" 키도 변경 여부 확인 필요. | 중간 |
| **test_agentic_core.py** | CandidateTable 3개소, candidate_tables 3개소 치환. | 낮음 |
| **test_recovery_agent.py** | CandidateTable 2개소, candidate_tables 1개소 치환. | 낮음 |
| **test_agentic_real_e2e.py** | candidate_tables 문자열 참조 5개소. 리포트 출력 포맷도 변경. | 낮음 |
| **agentic_e2e_test_catalog.json** | "candidate_tables" 문자열 1개소. | 최소 |

### A.3. 테스트 부재 위험

| 항목 | 위험도 | 설명 |
|------|--------|------|
| `_serialize_tool_results` -> 렌더러 전환 | 🔴 Critical | 현재 단위 테스트 0건. 렌더러 9개 함수 각각에 대한 테스트가 필수. |
| `_apply_tool_result` -> step.raw_result 전환 | 🔴 Critical | 현재 단위 테스트 0건. fetcher의 state 쓰기 범위 변경 검증 필요. |
| `BatchInterpretResult` 출력 형식 변경 | 🟡 Warning | 파싱 로직(`_parse_batch_result`)의 selected/rejected -> interpretations 하위 통합 테스트 필요. |
| Level 0/1 분기 로직 | 🟡 Warning | 토큰 추정 + 분기 경로 테스트 신규 작성 필요. |

---

## Part B: 죽은 코드 예측

### B.1. fetcher에서 삭제/변경되는 함수

#### `_apply_tool_result` (삭제 대상)

현재 호출 위치:
- `src/agents/nodes/reason/knowledge_fetcher.py:122` -- `_run_step` 내부에서 호출

설계 변경 후: fetcher는 도구 실행 결과를 `step.raw_result`에 저장하고, state 필드에 직접 적재하지 않는다. 따라서 `_apply_tool_result`와 그 하위 디스패치 대상 전체가 죽은 코드가 된다.

**삭제 대상 함수 목록**:

| 함수 | 파일:라인 | 사유 |
|------|----------|------|
| `_apply_tool_result` | `knowledge_fetcher.py:175-209` | state 직접 적재 디스패처 -- interpreter로 이관 |
| `_store_use_cases` | `knowledge_fetcher.py:215-225` | explored_use_cases 직접 적재 -- interpreter가 판정 후 적재 |
| `_store_biz_manuals` | `knowledge_fetcher.py:243-256` | explored_biz_manuals 직접 적재 -- interpreter가 판정 후 적재 |
| `_store_biz_terms` | `knowledge_fetcher.py:259-275` | explored_biz_terms 직접 적재 -- interpreter가 판정 후 적재 |
| `_store_code_meta` | `knowledge_fetcher.py:228-240` | code_map 직접 적재 -- interpreter가 적재 |
| `_store_sample_rows` | `knowledge_fetcher.py:305-317` | CandidateTable.sample_rows 직접 적재 |
| `_store_date_distribution` | `knowledge_fetcher.py:320-329+` | observed_date_columns 직접 적재 |
| `_store_column_values` | `knowledge_fetcher.py` (350 부근) | ColumnInfo.discovered_values 직접 적재 |
| `_store_column_profile` | `knowledge_fetcher.py` (370 부근) | ColumnInfo 통계 직접 적재 |

#### `_fetch_use_case_related_metas` (삭제/변경 대상)

현재 호출 위치:
- `knowledge_fetcher.py:193` -- `_apply_tool_result` 내부에서 `search_use_cases` 결과 후 호출

설계 변경 후: search_use_cases의 enrichment로 통합된다. fetcher 내부에서 수행하되 결과를 `step.raw_result`에 포함시키는 형태로 변경. 함수 자체는 리팩터링되어 존속할 수 있으나, 현재의 state 직접 적재 로직(`candidate_tables.append`, `code_map[col] = ...`)은 제거된다.

영향 범위:
- `knowledge_fetcher.py:396-476` -- 함수 본체
- `knowledge_fetcher.py:409` -- `extract_hints_from_use_cases` 호출 (이 함수 자체는 존속)
- `knowledge_fetcher.py:420-436` -- `search_table_meta` 호출 + `CandidateTable.from_meta` + `candidate_tables.append` (삭제)
- `knowledge_fetcher.py:445-467` -- `search_code_meta` 호출 + `code_map` 적재 (삭제)

#### Phase 2 함수 (완전 삭제)

| 함수 | 파일:라인 | 사유 |
|------|----------|------|
| `_sample_unsampled_tables` | `knowledge_fetcher.py:598+` | 암묵적 자동 수집 -- 설계서 S3.3에서 삭제 명시 |
| `_observe_all_date_distributions` | `knowledge_fetcher.py:549+` | 플래너가 `get_date_distribution`을 명시적 스텝으로 계획 |

호출 위치:
- `knowledge_fetcher.py:527-528` -- `knowledge_fetcher_node` 내부 Phase 2 블록

### B.2. interpreter에서 삭제/변경되는 함수

#### `_serialize_tool_results` (렌더러로 대체)

| 위치 | 내용 |
|------|------|
| `knowledge_interpreter.py:242-287` | 함수 본체 (45줄) -- 완전 삭제 |
| `knowledge_interpreter.py:324` | 호출부 -- `serialize_tool_results_by_step(execution_plan)` 으로 대체 |

#### `_parse_batch_result`의 selected/rejected 파싱 로직

| 위치 | 내용 |
|------|------|
| `knowledge_interpreter.py:396-397` | `selected=data.get("selected", [])`, `rejected=data.get("rejected", [])` -- 제거 |
| `knowledge_interpreter.py:137-178` | Phase 5 전체 -- `selected_map`/`rejected_map` 구축 + 마킹 로직을 interpretations 하위의 `explored_tables` 배열 기반으로 전면 재작성 |

#### `BatchInterpretResult`의 selected/rejected 필드

| 위치 | 내용 |
|------|------|
| `knowledge_interpreter.py:68-69` | `selected: list[dict]`, `rejected: list[dict]` 필드 선언 -- 제거 |
| `knowledge_interpreter.py:75-76` | 초기화 -- 제거 |

대체 구조: interpretations 하위의 `explored_tables`, `explored_use_cases`, `explored_biz_terms`, `explored_biz_manuals` 배열에서 `status` 필드로 SELECTED/REJECTED 판정.

### B.3. state에서 삭제되는 필드

#### `KnowledgeItem.is_inferred`

| 위치 | 내용 |
|------|------|
| `src/agents/state/state.py:85` | 필드 선언: `is_inferred: bool = False` |
| 테스트 참조 | **없음** |
| 소스 참조 | state.py:85만 존재 -- 다른 코드에서 읽기/쓰기 없음 |
| 판정 | **안전 삭제 가능** -- 완전한 dead field |

#### `ReasoningState.conflicted_bounce_count`

| 위치 | 내용 |
|------|------|
| `src/agents/state/state.py:523` | 필드 선언: `conflicted_bounce_count: int = 0` |
| `src/agents/nodes/reason/reasoning_preparer.py:56` | 초기화: `reason.conflicted_bounce_count = 0` |
| 테스트 참조 | **없음** |
| 판정 | **안전 삭제 가능** -- 초기화만 하고 읽는 곳 없음 |

#### `ReasoningState.last_verdict`

| 위치 | 내용 |
|------|------|
| `src/agents/state/state.py:518` | 필드 선언: `last_verdict: str \| None = None` |
| `src/agents/nodes/reason/reasoning_preparer.py:53` | 초기화: `reason.last_verdict = None` |
| `src/agents/nodes/reason/readiness_gate.py:72` | 갱신: `reason.last_verdict = verdict.value` |
| 테스트 참조 | **없음** |
| 소스 읽기 참조 | **없음** -- 쓰기만 있고 읽기 없음 |
| 판정 | **안전 삭제 가능** -- 쓰기만 존재하는 dead field |

### B.4. Enum 잔재

#### `TableSelectionStatus`

**src/ 에서 참조 없음**. 이미 `SelectionStatus`로 통합 완료된 상태. 코드에서 완전히 제거됨.

#### `RelevanceStatus`

**src/ 에서 참조 없음**. 테스트에서도 참조 없음. 설계문서 및 리뷰 문서에서만 언급됨. 
단, `docs/reviews/code/20260404-rename-integrity-verification-report.md`에서 `BizManualEntry`와 `BizTermEntry`가 `relevance_status(RelevanceStatus)` 필드를 가진다고 기술되어 있으므로, state.py 원본을 재확인했지만 현재 src/에서 `RelevanceStatus` import/사용이 없다.

**주의**: 이 enum이 실제로 `BizManualEntry`/`BizTermEntry` 에서 사용 중인지 한 번 더 확인 필요. 리뷰 문서가 구 버전 기준일 수 있음.

---

## Part C: 프로세스 충실도 점검

### C.1. fetcher -> step.raw_result 저장 -> interpreter 읽기 흐름

#### 그래프 노드 연결 확인

`src/agents/graph/pipeline.py:440-443`:
```python
workflow.add_edge("reasoning_preparer", "knowledge_fetcher")
workflow.add_edge("knowledge_fetcher", "knowledge_interpreter")
workflow.add_edge("knowledge_interpreter", "readiness_gate")
```

**결론**: fetcher -> interpreter는 직접 연결된 순차 엣지이다. 조건부 분기 없이 반드시 fetcher 다음에 interpreter가 실행된다. 설계문서의 흐름과 **완전 호환**.

#### LangGraph에서 nested Pydantic 필드 갱신

현재 패턴 (`knowledge_fetcher_node`, L490-532):
```python
reason = state.reason.model_copy(deep=True)
# ... reason의 하위 필드 수정 ...
return {"reason": reason}
```

`ExecutionStep.raw_result` 필드를 추가하면:
- fetcher가 `reason.execution_plan[i].raw_result = result`로 설정
- `return {"reason": reason}`으로 전체 ReasoningState를 반환
- LangGraph가 state.reason을 통째로 교체

**결론**: 현재 패턴이 deep copy + 전체 반환이므로, ExecutionStep 내부의 `raw_result` 필드 갱신은 **문제 없이 동작**한다. LangGraph는 반환된 dict의 key에 대해 state를 교체하므로, nested 필드 갱신이 자연스럽게 전파된다.

**주의점**: `raw_result`가 대형 dict (예: 100+ 컬럼 테이블 메타)를 포함할 경우, `model_copy(deep=True)`의 비용이 증가한다. interpreter에서 `step.raw_result = None` 설정으로 해제하는 설계는 메모리 관리 측면에서 적절하다.

### C.2. interpreter가 state에 적재하는 반환 구조

현재 interpreter 노드 반환값 (`knowledge_interpreter.py:184-189`):
```python
reason.knowledge_items = knowledge_items
reason.candidate_tables = candidate_tables
reason.execution_plan = execution_plan
reason.discovered_facts = discovered_facts
return {"reason": reason}
```

설계 변경 후 추가 적재 대상:
- `reason.explored_use_cases` -- 현재 interpreter에서 **쓰지 않음** (fetcher가 직접 적재)
- `reason.explored_biz_terms` -- 현재 interpreter에서 **쓰지 않음** (fetcher가 직접 적재)
- `reason.explored_biz_manuals` -- 현재 interpreter에서 **쓰지 않음** (fetcher가 직접 적재)
- `reason.code_map` -- 현재 interpreter에서 **읽기만** (fetcher가 직접 적재)

**결론**: 한 번의 `return {"reason": reason}`으로 모든 필드를 한꺼번에 반환 가능하다. 추가 구현이 필요한 부분:

1. interpreter 시작 시 reason에서 `explored_use_cases`, `explored_biz_terms`, `explored_biz_manuals`, `code_map`을 깊은 복사로 가져오기
2. LLM 판정 결과를 기반으로 이 4개 필드에 적재
3. `reason.explored_use_cases = ...` 등으로 할당 후 반환

현재 fetcher가 이 4개 필드를 적재하고 반환하므로, fetcher의 반환에서 이 필드들을 **제거**해야 한다. 구체적으로:

`knowledge_fetcher_node` (L519-523)에서 다음 5줄이 제거/변경 대상:
```python
reason.explored_use_cases = explored_use_cases      # 제거
reason.code_map = code_map                           # 제거
reason.explored_biz_manuals = explored_biz_manuals   # 제거
reason.explored_biz_terms = explored_biz_terms       # 제거
```

단, **설계서 S3.4에서 `code_map`은 "판정 없이 적재"로 규정**되어 있다. interpreter가 code_map을 판정 없이 그대로 적재하므로, fetcher에서 `step.raw_result` 내에 code_map 데이터를 포함시키고 interpreter가 추출하여 state에 적재하는 구조가 된다.

### C.3. Level 0/1 분기 위치

설계서 S6에 따르면:
1. 렌더링 완료 후 전체 텍스트의 토큰 수를 추정
2. 예산 초과 시 Level 1 (스텝별 개별 호출)로 전환

**자연스러운 위치**: `_interpret_batch` 함수 내부, 렌더링 직후/LLM 호출 직전.

현재 `_interpret_batch` 구조 (`knowledge_interpreter.py:307-371`):
```python
async def _interpret_batch(...) -> BatchInterpretResult:
    done_steps = [s for s in execution_plan if s.status == StepStatus.DONE]
    if not done_steps:
        return BatchInterpretResult()

    tool_results_str = _serialize_tool_results(...)  # <-- 여기서 렌더링
    # ... 프롬프트 구성 ...
    # ... LLM 호출 ...
```

**제안되는 변경 흐름**:
```python
async def _interpret_batch(...) -> BatchInterpretResult:
    done_steps = [...]
    rendered_text = serialize_tool_results_by_step(execution_plan)  # 렌더링
    estimated_tokens = len(rendered_text) // 3  # 간이 추정

    if estimated_tokens <= TOKEN_BUDGET:
        return await _interpret_batch_level0(rendered_text, ...)  # 전체 배치
    else:
        return await _interpret_batch_level1(execution_plan, ...)  # 스텝별
```

**결론**: 분기 위치가 자연스럽다. `_interpret_batch`가 이미 단일 진입점 역할을 하므로, 내부에서 Level 분기만 추가하면 된다. 별도 노드 분리나 그래프 구조 변경은 불필요하다.

**Level 1의 기술적 고려사항**:
- 스텝별 개별 LLM 호출이므로 `await` N+1회 (N 스텝 + 1 종합 판정)
- 각 호출의 프롬프트가 독립적이므로 `asyncio.gather`로 병렬화 가능한지 검토 필요
  - 설계서에서 "이전 스텝에서 이번 라운드에 도출한 insight (누적)"을 공통 컨텍스트로 포함하므로, **순차 실행이 원칙** (이전 스텝 insight가 다음 스텝에 필요)
  - 단, 성능을 위해 "이전 라운드 insight만 공통 컨텍스트로 제공하고 현 라운드 스텝은 병렬" 변형도 가능 (설계 결정 필요)

---

## 종합 요약

### 변경 규모 매트릭스

| 영역 | 파일 수 | 주요 변경 |
|------|---------|----------|
| 테스트 (CandidateTable/candidate_tables 치환) | 6 + 1 JSON | 기계적 rename, test_three_aspect_enrichment.py가 최대 |
| fetcher 삭제 함수 | 1 파일, 10개 함수 | `_apply_tool_result` + 9개 `_store_*` + Phase 2 함수 2개 |
| interpreter 변경 함수 | 1 파일, 3개 함수 | `_serialize_tool_results` 삭제, `_parse_batch_result`/Phase 5 재작성 |
| state dead field | 3개 필드 | `is_inferred`, `conflicted_bounce_count`, `last_verdict` |
| state dead field 초기화 코드 | 2 파일 | `reasoning_preparer.py`, `readiness_gate.py` |
| LangGraph 프로세스 | 0 파일 | 그래프 구조 변경 불필요 |

### 위험도별 분류

| 등급 | 항목 |
|------|------|
| 🔴 Critical | 렌더러 9개 함수에 대한 신규 단위 테스트 필수 (현재 0건) |
| 🔴 Critical | `_apply_tool_result` 삭제 시 fetcher -> interpreter 데이터 전달 경로 검증 테스트 필수 |
| 🟡 Warning | `BatchInterpretResult` 출력 형식 변경에 따른 `_parse_batch_result` 재작성 -- 파싱 실패 시 rule-based fallback이 있으나, fallback 품질 검증 필요 |
| 🟡 Warning | Level 1 스텝별 호출의 순차/병렬 실행 결정 미확정 |
| 🟡 Warning | fetcher 반환에서 explored_use_cases/code_map/explored_biz_manuals/explored_biz_terms 제거 시, **interpreter가 이전 라운드 데이터를 읽어야 하는 경우** 주의 -- state에서 직접 읽으므로 문제 없으나 구현 시 확인 |
| 🟢 Info | dead field 3개는 테스트/소스 참조 없으므로 안전 삭제 |
| 🟢 Info | `TableSelectionStatus`, `RelevanceStatus` enum은 src/에서 이미 제거 완료 |
| 🟢 Info | 그래프 구조(pipeline.py) 변경 불필요 -- fetcher->interpreter 직접 엣지 유지 |
