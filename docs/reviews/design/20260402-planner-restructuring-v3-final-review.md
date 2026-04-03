# Planner 구조 개선 v3 최종 설계 검토

- **검토일**: 2026-04-02
- **대상 문서**: `docs/strategy-proposals/planner-restructuring/01-strategy.md` (v3)
- **관련 소스**: `planner.py`, `knowledge_fetcher.py`, `knowledge_interpreter.py`, `pipeline.py`, `state.py`, `tools.py`

---

## 검토 결과 요약

| 등급 | 건수 |
|------|------|
| Critical | 2 |
| Warning | 3 |
| Info | 3 |

---

## Critical

### C1: 내장 후속 수집 결과가 knowledge_interpreter의 result_ref 경로에서 누락

`_fetch_use_case_related_metas()`는 candidate_tables/code_map에 **직접 반영**하지만, knowledge_interpreter는 `step.result_ref`에서 DONE 스텝의 결과를 수집하여 LLM 프롬프트(`{tool_results}`)에 주입한다. 내장 후속 수집으로 조회한 테이블 메타/코드 메타는 어떤 step의 result_ref에도 저장되지 않으므로, **interpreter의 `_serialize_tool_results()`에 포함되지 않는다**.

candidate_tables는 `{table_observations}`로 별도 직렬화되어 프롬프트에 들어가므로 테이블 메타는 간접 참조 가능하나, **코드 메타(code_map)는 interpreter 프롬프트에 주입되는 경로가 현재 없다**. interpreter가 코드값을 해석하지 못하면 UNRESOLVED가 유지되어 recovery 루프가 발생한다.

**대응 제안**: (A) `_fetch_use_case_related_metas()`의 결과를 search_use_cases step의 result_ref에 병합하거나, (B) interpreter 프롬프트에 `{code_map}` 변수를 신설하여 직접 주입.

### C2: loop_guard.total_tool_calls에 내장 후속 수집 호출이 미반영

문서 4.5절의 `_fetch_use_case_related_metas()`는 `search_table_meta`, `search_code_meta`를 asyncio.gather로 호출하지만, 이 호출 건수가 `total_tool_calls`에 가산되지 않는다. `_run_step()`은 항상 1을 반환하므로, 후속 수집 5~10건이 가드에 반영되지 않아 **MAX_TOOL_CALLS 초과 위험**이 있다.

**대응 제안**: `_fetch_use_case_related_metas()`가 실제 호출한 건수를 반환하고, `_run_step()` 반환값에 합산.

---

## Warning

### W1: code_columns는 dict[str, list[str]]이지만 문서 코드는 key만 순회

문서 4.5절 코드에서 `hints.code_columns`를 `cols_to_fetch`로 변환 시 key(컬럼명)만 추출한다. 이는 올바른 접근이나, `search_code_meta(col)`의 반환이 해당 컬럼의 코드 전체 목록인지 특정 값만인지 명시되어 있지 않다. **tools.py에서 `search_code_meta`는 column_name 단위로 전체 코드를 반환**하므로 동작은 정상이나, 문서에 이 매핑 관계를 명시하면 구현 시 혼란을 줄일 수 있다.

### W2: CandidateTable.from_meta()를 2회 호출하는 비효율

문서 4.5절 `new_tables` 생성부에서 `CandidateTable.from_meta(m)`를 리스트 컴프리헨션 내에서 2번 호출한다 (필터 조건 + 값 생성). 실제 구현 시 왈러스 연산자(`:=`) 또는 별도 변수로 1회 호출로 최적화해야 한다.

### W3: _route_after_reasoning_preparer에서 항상 knowledge_fetcher 반환 시 conditional_edges 불필요

문서 4.6절에서 `_route_after_reasoning_preparer`가 항상 `"knowledge_fetcher"`를 반환하면 `add_conditional_edges` 대신 `add_edge`로 단순화 가능. 실제 구현 시 불필요한 복잡도를 제거해야 한다.

---

## Info

### I1: 용어 일관성 확인 완료

문서 전체에서 "동적 스텝 확장" 표현은 제거되고 **"내장 후속 수집"**으로 통일되어 있다. 단, 5.2절에 "동적 스텝 확장에 필요한 정보"라는 표현이 1건 잔존한다 (line 573). "내장 후속 수집에 필요한 정보"로 수정 권장.

### I2: fast_path_triggered 필드의 잔존 영향

문서에서 `fast_path_triggered`를 유지하되 설정하지 않겠다고 명시했다. 현재 `pipeline.py`의 `_route_after_sql_validator()`(line 239)에서 `fast_path_triggered and ft is not None` 분기가 있으므로, reasoning_preparer에서 설정하지 않으면 이 분기는 **dead code**가 된다. 추후 별도 정리 필요.

### I3: 설계 논리의 정합성 평가

reasoning_preparer에서 LLM/도구 호출을 모두 제거하고 knowledge_fetcher에 집중하는 설계는 노드 역할 경계를 명확히 하며, 기존 코드의 `_run_step()` for 루프를 변경하지 않는 점에서 안전하다. Fast-Path 제거로 인한 latency 증가(~2초)는 planner LLM 제거(~2-4초)로 상쇄된다는 분석도 타당하다.

---

## 종합 의견

설계 자체의 방향성(결정론화, 노드 역할 분리, recovery 집중)은 타당하며 기존 코드와의 호환성도 잘 고려되어 있다. 다만 **C1(코드 메타의 interpreter 프롬프트 미도달)과 C2(loop_guard 미반영)**는 구현 전에 설계 문서에 반영하여 해소해야 한다. 특히 C1은 knowledge_interpreter의 해석 품질에 직접 영향하므로 설계 목표 G2(입력 품질 향상)를 달성하려면 필수적으로 보완해야 한다.
