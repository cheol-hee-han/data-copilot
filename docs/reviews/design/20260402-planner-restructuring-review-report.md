# 설계 검토 보고서: Planner 구조 개선

- **검토일**: 2026-04-02
- **검토 대상**: `docs/strategy-proposals/planner-restructuring/01-strategy.md`
- **검토 범위**: 4개 프레임 (가정 검증 → 실패 시나리오 → 대안 비교 → 시간축 리스크)

---

## 총평

설계 방향은 **타당하다**. 현행 planner의 LLM 가설 생성이 실질적으로
deterministic한 로직 위에 얹혀 있다는 분석(P1~P4)은 코드 수준에서 검증된다.
초기 탐색 결정론화 + LLM 계획을 recovery에 집중하는 구조는
latency 절감, 코드 단순화, knowledge_interpreter 입력 품질 향상을 동시에 달성한다.

다만 아래 **5건의 비판 사항**이 구현 전 해소되어야 한다.

---

## 가정 검증 결과

### A1: "knowledge_interpreter는 코드 변경 없이 동작한다"

**검증 결과: 조건부 유효.**

설계 문서의 4.5절에서 planner가 가상 `ExecutionStep(status=DONE, result_ref=...)`을
생성하면 knowledge_interpreter가 변경 없이 동작한다고 주장한다.

코드 확인 (`knowledge_interpreter.py:96-105`):
```python
execution_plan = list(reason.execution_plan)
for step in execution_plan:
    if step.status == StepStatus.DONE and step.result_ref is not None:
        collected_results.append((step, step.result_ref))
```

DONE + result_ref 조건만 확인하므로 **가상 스텝도 정상 처리된다** — 유효.

단, knowledge_interpreter는 `_serialize_tool_results()`에서 step.tool과 step.input을
프롬프트에 포함하므로, planner가 생성하는 가상 스텝의 tool/input 값이
정확해야 LLM 해석 품질이 보장된다.

### A2: "readiness_gate가 초기 탐색 후 자동으로 recovery_agent로 라우팅된다"

**검증 결과: 유효하나 전제 조건 필요.**

`pipeline.py:190-203`의 `_route_after_readiness_gate()`:
```python
if verdict == "explore":
    if reason.exploration_phase == "initial":
        if pending_steps:
            return "explore"
    reason.exploration_phase = "recovery"
    return "recovery"
```

planner에서 모든 스텝을 DONE으로 채우면 `pending_steps`가 비어있고,
`exploration_phase == "initial"`이므로 바로 recovery로 전환 — **정상 동작.**

**전제 조건:** planner가 `exploration_phase = "initial"`을 설정해야 한다.
현재 코드(`planner.py:67`)에서 이미 설정하므로 문제없다.

### A3: "recovery_agent는 변경 없이 동작한다"

**검증 결과: 유효.**

recovery_agent의 `_handle_hypothesis_transition()`은 `current_hypothesis`가
ACTIVE 상태인지만 확인하고 FAILED 전이 + DeadEnd 기록을 수행한다.
H_INIT가 ACTIVE 상태로 설정되므로 정상 동작한다.

DeadEnd에 기록되는 `failure_type`은 `reason.failure_type`에서 가져오는데,
초기 탐색 후 readiness_gate → recovery 경로에서는 `failure_type`이 None이다.
이 경우 `FailureType.TERM_UNRESOLVABLE`로 폴백된다 (`recovery_agent.py:160-161`).

**이것이 정확한 실패 유형인가?** → 비판 사항 F2 참조.

### A4: "Fast-Path 트리거 빈도가 높아진다"

**검증 결과: 부분 유효.**

`_should_fast_path()` 조건 중 ③ "모든 knowledge_items가 UNRESOLVED가 아닐 것"이
가장 제약적이다. planner에서 `_initialize_knowledge_items()`가 생성하는 항목은
초기에 모두 UNRESOLVED이다 (`planner.py:239-252`).

유사SQL 메타를 추가 조회하더라도, **knowledge_items의 상태를 승격하는 것은
knowledge_interpreter**이므로 planner 단계에서는 여전히 UNRESOLVED이다.

따라서 Fast-Path 트리거 빈도는 **현재와 거의 동일**하다.
설계 문서 6.2절의 "빈도가 높아질 수 있다"는 부정확하다.

---

## 주요 비판 사항

### [P1] F1: knowledge_fetcher의 Phase 2(관찰 데이터 수집)가 초기 경로에서 누락

**근거:**

현재 knowledge_fetcher는 도구 실행(Phase 1) 후에 **관찰 데이터 수집(Phase 2)**을
수행한다 (`knowledge_fetcher.py:233-235`):

```python
# Phase 2: 관찰 데이터 수집 (DB 쿼리, 전체 대상)
await _observe_all_date_distributions(candidate_tables)
await _sample_unsampled_tables(candidate_tables)
```

이 Phase 2는 candidate_tables의 **날짜 분포 조회**와 **샘플 데이터 조회**를 수행하며,
knowledge_interpreter의 프롬프트에서 `{table_observations}`로 주입된다.

제안 구조에서 planner → knowledge_interpreter 직행 시,
**날짜 분포와 샘플 데이터가 없는 상태에서 해석**하게 된다.

knowledge_interpreter의 테이블 판정 기준 (프롬프트):
> "질의의 시간 조건과 테이블의 날짜 분포/패턴이 부합하는지 판단하세요."

날짜 분포가 없으면 이 판정이 불가능하다.

**대안:** planner에서 candidate_tables 구성 후 Phase 2를 직접 수행한다.

```python
# planner_node 내부 — candidate_tables 구성 직후
await _observe_all_date_distributions(candidate_tables)
await _sample_unsampled_tables(candidate_tables)
```

`_observe_all_date_distributions()`과 `_sample_unsampled_tables()`는
knowledge_fetcher의 모듈 내부 함수이므로, planner에서 import하거나
공통 유틸리티로 분리해야 한다.

---

### [P1] F2: 초기 탐색 실패 시 DeadEnd의 failure_type이 부정확

**근거:**

recovery_agent 진입 시 `_handle_hypothesis_transition()`이 DeadEnd를 기록한다:

```python
reason.dead_ends.append(DeadEnd(
    hypothesis_id=failed.hypothesis_id,
    failure_type=reason.failure_type or FailureType.TERM_UNRESOLVABLE,
    reason=reason.failure_reason or "실패 사유 미제공",
))
```

초기 탐색 후 readiness_gate → recovery 경로에서는:
- `failure_type = None` → `TERM_UNRESOLVABLE`로 폴백
- `failure_reason = None` → `"실패 사유 미제공"`

recovery LLM이 보게 되는 dead_end:
```
- [TERM_UNRESOLVABLE] 실패 사유 미제공
```

이는 실제 상황("초기 탐색은 수행했으나 지식이 부족하여 추가 탐색 필요")을
정확히 반영하지 못하며, recovery LLM의 재계획 품질을 저하시킨다.

**대안:** readiness_gate에서 recovery로 전환할 때 failure context를 설정한다.

```python
# pipeline.py: _route_after_readiness_gate()
reason.failure_type = FailureType.TERM_UNRESOLVABLE  # 또는 신규 타입 추가
reason.failure_reason = _build_readiness_gap_reason(reason)
```

`_build_readiness_gap_reason()`은 현재 상태에서 부족한 항목을 요약한다:
```
"UNRESOLVED 항목 3건 (measure:건수, filter:신규, dimension:지점), 
 조인 경로 미확인 (TB_LOAN_MASTER, TB_LOAN_EXEC)"
```

---

### [P2] F3: planner의 초기 조회가 병렬화 설계에 순서 의존성 존재

**근거:**

설계 문서 3.1절의 플로우:
```
├─ search_use_cases(원본 질의)                      ┐
├─ search_table_meta(8-slot meta_search 키워드)      ├─ 병렬
├─ extract_hints_from_use_cases() (sqlglot)          │
└─ search_table_meta(유사SQL 추출 테이블)            ┘ ← 신규
```

이 4개를 "병렬"로 표기했으나, 실제로는:
1. `search_use_cases()` 완료 →
2. `extract_hints_from_use_cases()` 실행 →
3. `source_tables` 추출 →
4. `search_table_meta(source_tables)` 실행

**2단계 순서 의존**이 있다. 1+2가 완료되어야 4를 실행할 수 있다.

**대안:** 설계 문서의 플로우를 정확한 2-Phase 병렬로 수정한다.

```
Phase A (병렬):
  ├─ search_use_cases(원본 질의)
  └─ search_table_meta(8-slot 키워드)

Phase B (Phase A 완료 후, 병렬):
  ├─ extract_hints + search_table_meta(유사SQL 테이블) ← 순차
  └─ (8-slot 메타는 이미 완료)
```

코드 수준:
```python
# Phase A: 병렬
use_cases, keyword_metas = await asyncio.gather(
    search_use_cases(query),
    search_table_meta(meta_query),
)
# Phase B: 유사SQL 테이블 메타 추가 조회
hints = extract_hints_from_use_cases(use_cases)
additional_metas = await _collect_use_case_table_metas(
    hints.source_tables, keyword_metas,
)
```

---

### [P2] F4: `search_table_meta(테이블명)`의 동작이 키워드 검색과 다를 수 있음

**근거:**

`_collect_use_case_table_metas()`에서 `search_table_meta(table)` 호출 시,
`table`은 "TB_LOAN_MASTER" 같은 **정확한 테이블명**이다.

그러나 `search_table_meta()`는 MongoDB의 `$text` 검색을 사용하므로,
정확한 테이블명이 텍스트 인덱스에서 매칭되지 않을 수 있다.
(테이블명에 포함된 언더스코어, 약어 등이 토큰화에서 분리될 수 있음)

**대안:** `search_table_meta()` 외에 테이블명 정확 매칭 함수(`get_table_by_name()`)가
존재하는지 확인하고, 있으면 그것을 사용한다. 없으면 신규 구현이 필요하다.
구현 전에 tools.py의 `search_table_meta()` 내부 로직을 확인하여
정확 매칭 vs 텍스트 검색의 차이를 파악해야 한다.

---

### [P3] F5: 가상 ExecutionStep의 loop_guard 카운팅 불일치

**근거:**

현재 knowledge_fetcher는 도구 실행 시 `total_tool_calls`를 증가시킨다:
```python
total_tool_calls += calls  # knowledge_fetcher.py:215
```

planner에서 가상 DONE 스텝을 생성하면, 이 스텝들은 **실제로 도구를 호출했지만
loop_guard에 카운팅되지 않는다**.

이후 recovery_agent의 `should_terminate()` 판정에서
실제 도구 호출 횟수보다 낮은 값으로 판단하게 된다.

최악의 경우 루프 가드 한도(20회)를 초과하여 도구를 호출할 수 있다.

**대안:** planner에서 초기 조회 횟수를 loop_guard에 반영한다.

```python
reason.loop_guard = reason.loop_guard.model_copy()
reason.loop_guard.total_tool_calls = initial_tool_call_count
```

---

## 실패 시나리오

### S1: 유사 SQL에서 추출한 테이블이 5개 이상인 경우

유사 SQL이 3건이고 각각 2-3개 테이블을 참조하면,
`source_tables`가 5-8개가 될 수 있다.
이 경우 `_collect_use_case_table_metas()`가 5-8건의 병렬 MongoDB 조회를 발생시킨다.

**영향:** planner latency 증가 (현재 대비 +0.5~1.5초 예상).
**대응:** `source_tables`에 상한(예: 5개)을 설정하고, 유사도 높은 SQL의 테이블을 우선한다.

### S2: knowledge_interpreter가 빈 collected_results를 받는 경우

planner에서 유사 SQL 0건 + 8-slot 메타 0건이면,
execution_plan이 비어있고 collected_results도 빈다.

`_interpret_batch()`는 `if not collected_results: return BatchInterpretResult()` →
knowledge_items 승격 없음 → readiness_gate에서 REPLAN → recovery_agent.

**영향:** Cold Start 시 knowledge_interpreter LLM 호출이 무의미하게 소비된다.
**대응:** planner에서 collected_results가 비어있으면 knowledge_interpreter를 건너뛰고
직접 recovery_agent로 라우팅하는 Fast-Fail 경로를 추가한다.

---

## 아키텍처 대안 비교

| 관점 | 현행 유지 | 제안 (본 설계) | 대안: planner LLM에 상세 입력 |
|------|----------|---------------|------------------------------|
| LLM 호출 수 | planner 1 + interpreter 1 = 2 | interpreter 1 = 1 | planner 1 + interpreter 1 = 2 |
| 초기 latency | 2-4초 (LLM) + 조회 | 조회만 (~1초) | 2-4초 (LLM) + 추가 조회 |
| 가설 품질 | 낮음 (요약만 입력) | N/A (rule-based) | 높음 (상세 입력) |
| 코드 복잡도 | 높음 | 낮음 | 높음 (프롬프트 확장) |
| 폐쇄망 호환 | LLM 2회 | LLM 1회 (개선) | LLM 2회 |

**"대안: planner LLM에 상세 입력"**은 P2(요약만 전달)와 P1(유사SQL 메타 미조회)만 해결하고,
P3(deterministic 로직)과 비용 문제는 해결하지 못한다. 본 설계가 우위.

---

## 수용 불가 항목 (재설계 권고)

- **F1 (P1)**: Phase 2 관찰 데이터 누락 — knowledge_interpreter 해석 품질에 직접 영향.
  planner에서 Phase 2를 수행하거나, planner → knowledge_fetcher(Phase 2만) → knowledge_interpreter 경로를 추가해야 한다.

## 수용 가능 항목 (개선 권고)

- **F2 (P1)**: DeadEnd failure context — 구현 시 함께 해소 가능.
- **F3 (P2)**: 병렬화 순서 의존 — 설계 문서 표기 수정 + 코드에서 2-Phase 병렬 구현.
- **F4 (P2)**: 테이블명 정확 매칭 — tools.py 확인 후 판단.
- **F5 (P3)**: loop_guard 카운팅 — 구현 시 반영.

---

## 합의 추천 설계 방향

본 설계의 핵심 방향(초기 탐색 결정론화 + recovery 집중)은 **수용**.
F1(관찰 데이터 수집)을 반영하여 planner의 흐름을 보완한 후 구현 진행을 권고한다.
