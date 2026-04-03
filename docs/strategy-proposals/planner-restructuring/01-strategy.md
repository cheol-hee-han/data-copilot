# Planner 구조 개선 — 초기 탐색 결정론화 + LLM 계획을 Recovery에 집중

- **작성일**: 2026-04-02
- **최종 수정**: 2026-04-02 (v3 — reasoning_preparer 리네이밍, 사전 조회/Fast-Path 제거, 내장 후속 수집)
- **상태**: 설계 확정 (구현 대기)
- **영향 범위**: `planner.py` → `reasoning_preparer.py`, `knowledge_fetcher.py`, `pipeline.py`, `system_prompts.py`
- **참조 문서**:
  - `docs/architecture/pipeline-architecture.md` (현행 파이프라인)
  - `docs/strategy-proposals/agentic-recovery-redesign/` (recovery_agent 설계)
  - `docs/reviews/design/20260402-planner-restructuring-review-report.md` (v1 리뷰)

---

## 목차

1. 현황 분석 및 문제 정의
2. 설계 목표
3. 제안 구조
4. 노드별 상세 변경
5. 데이터 모델 변경
6. Fast-Path 처리
7. 마이그레이션 전략
8. 리스크 및 대응

---

## 1. 현황 분석 및 문제 정의

### 1.1 현재 플로우

```
planner (LLM 1회)
  ├─ search_use_cases(원본 질의)            ← Qdrant 벡터 검색
  ├─ search_table_meta(8-slot 키워드)       ← MongoDB 텍스트 검색
  ├─ extract_hints_from_use_cases()         ← sqlglot 구조 추출
  ├─ LLM: 가설 수립 (PLANNER_SYSTEM)       ← 요약 텍스트만 보고 추측
  └─ _build_execution_plan()                ← 사실상 deterministic
→ knowledge_fetcher (plan 스텝 실행 + Phase 2 관찰 데이터 수집)
→ knowledge_interpreter (LLM: 결과 해석)
→ readiness_gate (준비도 판정)
→ 부족 시 recovery_agent (LLM: 재계획) → 루프
```

### 1.2 문제점 4가지

#### P1: 유사 SQL 테이블 메타 미조회

planner가 유사 SQL에서 `structural_hints.source_tables`를 추출하지만,
해당 테이블의 메타(컬럼 구조, 설명, PK)를 **가설 수립 전에 조회하지 않는다**.

```
현재:  유사SQL 조회 → (테이블 메타 없이) 가설 수립 → knowledge_fetcher에서 메타 조회
```

결과: LLM이 테이블명만 보고 가설을 세우므로 가설 품질이 낮다.

#### P2: 조회 결과를 LLM에 요약만 전달

`_generate_hypotheses()`에서 `{initial_context_summary}`로 주입되는 내용:

```
"유사 활용사례 3건 발견"
"  - (유사도 0.82) 월간 신규 대출 실행 건수 조회"
"관련 테이블 3건: TB_LOAN_MASTER, TB_LOAN_EXEC, TB_LOAN_DETAIL"
```

SQL 본문, 테이블 컬럼 구조, 코드값 등 **상세 정보가 없다**.

#### P3: 초기 스텝이 항상 동일한데 LLM이 결정

`_build_execution_plan()`의 실제 로직:

1. `hypothesis.based_on_use_case` → `search_use_cases` 스텝 추가
2. `structural_hints.source_tables` + `candidate_tables` → `search_table_meta` 스텝 추가
3. UNRESOLVED filter → `search_code_meta` 스텝 추가

**모든 분기가 rule-based**이며 LLM 가설 내용에 의존하지 않는다.

#### P4: 프롬프트에 조회 결과 활용 예시 부재

`planner_system.txt`의 few-shot 예시 4개 모두 `{initial_context_summary}`를
참고하는 패턴이 **없다**. LLM이 이 변수를 활용할 유인이 없다.

### 1.3 낭비되는 리소스

| 항목 | 비용 |
|------|------|
| planner LLM 호출 1회 | ~2048 토큰 출력, latency 2-4초 |
| 가설 파싱/정렬 로직 | 코드 복잡도 |
| `Hypothesis.priority` 관리 | 초기 가설 1개면 의미 없음 |

---

## 2. 설계 목표

| 목표 | 설명 |
|------|------|
| G1 | 초기 탐색을 결정론적(LLM 없음)으로 전환하여 latency와 비용 절감 |
| G2 | 유사 SQL 조회 후 관련 테이블 메타 + 코드 메타를 자동 수집하여 knowledge_interpreter 입력 품질 향상 |
| G3 | LLM 계획 수립을 recovery_agent에 집중 — 실패 컨텍스트 기반의 의미 있는 계획 |
| G4 | Hypothesis 모델은 유지 — recovery_agent의 dead_end 참조 기능 보존 |
| G5 | knowledge_fetcher의 기존 기능(dedup, Phase 2, loop_guard, 추적) 완전 보존 |
| G6 | 노드 역할 경계 명확화 — preparer는 준비만, fetcher는 실행만 |

---

## 3. 제안 구조

### 3.1 전체 플로우

```
reasoning_preparer (LLM 없음, deterministic) ← 기존 planner 리네이밍
  ├─ 8-slot에서 knowledge_items 초기화 (UNRESOLVED)
  ├─ rule-based 초기 가설 1개 생성 (H_INIT)
  └─ deterministic execution_plan 생성 (PENDING):
      - search_use_cases(원본 질의)
      - search_table_meta(8-slot meta_search 키워드)
      - search_code_meta(UNRESOLVED filter 컬럼)
→ knowledge_fetcher
    Phase 1: PENDING 스텝 순차 실행
      └─ search_use_cases 실행 시 내장 후속 수집:          ← 신규
         ├─ extract_hints → source_tables, code_columns 추출
         ├─ search_table_meta(유사SQL 추출 테이블들)       ← 자동 수집
         └─ search_code_meta(유사SQL 코드 컬럼들)          ← 자동 수집
    Phase 2: candidate_tables 대상 날짜 분포 + 샘플 조회
→ knowledge_interpreter (LLM: 유사SQL + 전체 메타 + 코드 통합 해석)
→ readiness_gate (SQL 작성 가능한지 판정)
  ├─ GENERATE → sql_generator
  ├─ EXPLORE (PENDING 소진) → recovery_agent
  ├─ REPLAN → recovery_agent
  └─ ASK_USER / TERMINATE → result_finalizer
→ recovery_agent (LLM: 부족한 지식을 채울 추가 탐색 계획)
→ knowledge_fetcher → knowledge_interpreter → readiness_gate → 루프
```

### 3.2 핵심 설계 결정

#### D1: reasoning_preparer는 초기화만 — 도구 실행 없음

v1 설계에서는 planner가 search_use_cases를 사전 조회했으나,
이는 "planner가 plan만 만든다"는 원칙에 어긋난다.

reasoning_preparer는 **어떠한 외부 도구도 호출하지 않는다**.
use_cases 조회를 포함한 모든 실행은 knowledge_fetcher에 위임한다.

#### D2: search_use_cases의 내장 후속 수집

유사SQL 조회 결과에서 추출한 테이블 메타와 코드 메타를
**search_use_cases 스텝 실행의 내장 후속 처리**로 자동 수집한다.
별도 ExecutionStep을 동적으로 추가하는 것이 아니라,
search_use_cases 실행 후 당연히 수행되어야 하는 후속 수집으로 취급한다.

```
search_use_cases 스텝 실행
  → 결과에서 extract_hints_from_use_cases() (sqlglot)
  → _fetch_use_case_related_metas() 내부에서:
    ├─ search_table_meta(TB_LOAN_MASTER)  → candidate_tables에 추가
    ├─ search_table_meta(TB_LOAN_EXEC)    → candidate_tables에 추가
    ├─ search_code_meta(LOAN_TYPE_CD)     → code_map에 추가
    └─ search_code_meta(STATUS_CD)        → code_map에 추가
```

execution_plan은 정적으로 유지되며, for 루프도 변경 없다.
후속 수집 결과는 candidate_tables와 code_map에 직접 반영된다.

이 접근의 장점:
- execution_plan 변경 없음 (정적 유지)
- knowledge_fetcher의 기존 for 루프 변경 없음
- 1회의 knowledge_fetcher 실행으로 use_cases + 관련 메타 + 코드를 모두 수집
- knowledge_interpreter가 첫 해석 시 **완전한 컨텍스트**를 받음

#### D3: Fast-Path 판정을 reasoning_preparer에서 제거

reasoning_preparer 시점에서는 Fast-Path 조건을 판정할 수 없다:

| 조건 | 필요 데이터 | reasoning_preparer 시점 |
|------|-----------|----------------------|
| structural_hints 비어있지 않음 | use_cases | **없음** — fetcher가 조회 |
| candidate_tables >= 1 | table_meta | **없음** — fetcher가 조회 |
| 모든 knowledge_items RESOLVED | interpreter 해석 | **불가** — 모두 UNRESOLVED |

readiness_gate의 GENERATE verdict가 동일한 역할을 수행하므로,
reasoning_preparer의 Fast-Path는 **제거**한다.

기존 `fast_path_triggered` 필드와 `_route_after_planner`의 Fast-Path 분기:
- `fast_path_triggered` 필드는 유지하되 reasoning_preparer에서 설정하지 않음
- readiness_gate에서 첫 GENERATE verdict 시 기존과 동일하게 sql_generator로 라우팅

### 3.3 현재 vs 제안 비교

| 관점 | 현재 | 제안 |
|------|------|------|
| 노드명 | planner | reasoning_preparer |
| LLM 호출 | 1회 (가설 생성) | 0회 |
| 도구 실행 | use_cases + table_meta (병렬) | **없음** — 모두 fetcher에 위임 |
| execution_plan | LLM 가설 의존 (실제론 rule-based) | 명시적 rule-based |
| 유사SQL 관련 메타 수집 | 없음 (use_case 테이블 메타 미조회) | **fetcher가 search_use_cases 내장 후속 처리로 자동 수집** |
| 유사SQL 관련 코드 수집 | 없음 | **fetcher가 search_use_cases 내장 후속 처리로 자동 수집** |
| Fast-Path | planner에서 판정 | **제거** — readiness_gate GENERATE로 대체 |
| Phase 2 관찰 데이터 | fetcher에서 수행 | **동일** |

### 3.4 노드 간 데이터 흐름

```
reasoning_preparer
  output: {
    reason: {
      knowledge_items: [...UNRESOLVED...],
      candidate_tables: [],               ← fetcher가 채움
      explored_use_cases: [],             ← fetcher가 채움
      hypotheses: [H_INIT],
      current_hypothesis: H_INIT,
      phase: EXPLORING,
      execution_plan: [                   ← PENDING 스텝들 (정적)
        {tool: search_use_cases, input: "원본 질의", status: PENDING},
        {tool: search_table_meta, input: "8-slot 키워드", status: PENDING},
        {tool: search_code_meta, input: "신규여부", status: PENDING},
      ],
    }
  }

→ knowledge_fetcher
  Phase 1: PENDING 스텝 순차 실행 (기존 for 루프 유지)
    search_use_cases 실행 → DONE
      └─ 내장 후속 수집: source_tables 메타 + code_columns 코드
         결과를 candidate_tables, code_map에 직접 반영
    search_table_meta 실행 → DONE
    search_code_meta 실행 → DONE
  Phase 2: candidate_tables 대상 날짜 분포 + 샘플 조회
  output: {
    execution_plan: [...DONE...],
    candidate_tables: [...메타 + 관찰 데이터 포함...],
    explored_use_cases: [...유사SQL...],
    code_map: {...코드값 포함...},
  }

→ knowledge_interpreter
  input: DONE 스텝의 result_ref + candidate_tables(관찰 데이터 포함)
  output: knowledge_items 승격, 테이블 판정(selected/rejected)

→ readiness_gate
  input: knowledge_items, candidate_tables
  output: ReadinessVerdict
    GENERATE → sql_generator (기존 Fast-Path 역할 흡수)
    EXPLORE/REPLAN → recovery_agent

→ recovery_agent
  input: confirmed/unresolved items, candidate_tables, dead_ends
  output: 새 execution_plan + 선택적 new_hypothesis

→ knowledge_fetcher → knowledge_interpreter → readiness_gate → 루프
```

---

## 4. 노드별 상세 변경

### 4.1 reasoning_preparer (기존 planner — 대폭 변경 + 리네이밍)

**파일명 변경:** `planner.py` → `reasoning_preparer.py`
**함수명 변경:** `planner_node()` → `reasoning_preparer_node()`

**제거:**

- `_generate_hypotheses()` — LLM 가설 생성
- `_parse_plan_response()` — LLM 응답 파싱
- `_generate_hypotheses_fallback()` — fallback 가설 생성
- `_collect_initial_context()` — 도구 직접 호출 (use_cases, table_meta)
- `_build_initial_candidates()` — candidate_tables 구성 (fetcher가 담당)
- `_should_fast_path()` — Fast-Path 판정
- `PLANNER_SYSTEM` 프롬프트 임포트 및 사용
- `llm_call_with_parse_retry`, `extract_json`, `render_prompt` 임포트
- `search_use_cases`, `search_table_meta` 도구 임포트

**유지:**

- `_build_decomposition_from_normalized()` — 8-slot → decomposition
- `_initialize_knowledge_items()` — UNRESOLVED 항목 초기화
- `_detect_ambiguous_output()` — output 모호 감지
- `_extract_meta_search_query()` — 8-slot에서 meta_search 키워드 추출
- `_build_execution_plan()` — **대폭 수정: 순수 rule-based, 도구 의존 없음**

**신규:**

- `_build_initial_hypothesis()` — rule-based 초기 가설 1개 생성

**변경된 reasoning_preparer_node 흐름:**

```python
async def reasoning_preparer_node(state: PipelineState) -> dict:
    """reasoning 상태를 초기화하고 결정론적 탐색 계획을 수립한다.

    외부 도구 호출 없음. 8-slot 정규화 결과를 기반으로
    knowledge_items, 초기 가설, execution_plan을 생성한다.
    """
    reason = state.reason.model_copy(deep=True)
    reason.phase = Phase.EXPLORING

    nq = state.normalized_query
    query = state.preprocessed_input or ""

    # 1. 8-slot에서 decomposition + knowledge_items 초기화
    decomposition = _build_decomposition_from_normalized(nq)
    reason.query_decomposition = decomposition

    knowledge_items = _initialize_knowledge_items(nq, decomposition, query)
    for i, ki in enumerate(knowledge_items):
        ki.knowledge_id = f"K{i + 1}"
    reason.knowledge_items = knowledge_items

    # 2. rule-based 초기 가설 생성
    hypothesis = _build_initial_hypothesis()
    reason.hypotheses = [hypothesis]
    reason.current_hypothesis = hypothesis

    # 3. deterministic execution_plan 생성 (도구 호출 없음)
    reason.execution_plan = _build_execution_plan(
        knowledge_items, list(reason.searched_queries), nq,
        original_query=query,
    )

    # 4. 초기화 상태 설정
    reason.exploration_phase = "initial"
    reason.recovery_rounds = 0

    return {"reason": reason}
```

### 4.2 `_build_initial_hypothesis()` (신규)

```python
def _build_initial_hypothesis() -> Hypothesis:
    """rule-based 초기 가설 — recovery_agent의 dead_end 참조용."""
    return Hypothesis(
        hypothesis_id="H_INIT",
        description="유사 SQL + 테이블 메타 기반 초기 탐색",
        strategy=(
            "사용자 질의를 키워드로 하여 조회한 유사SQL과, "
            "유사 SQL에서 추출한 테이블 및 8-slot 키워드로 조회한 "
            "테이블의 메타를 수집하여 SQL 생성 가능성 판단"
        ),
        status=HypothesisStatus.ACTIVE,
    )
```

### 4.3 `_build_execution_plan()` 변경

```python
def _build_execution_plan(
    knowledge_items: list[KnowledgeItem],
    searched_queries: list[str],
    nq: Any,
    original_query: str = "",
) -> list[ExecutionStep]:
    """결정론적 실행계획 생성 — 도구 의존 없음.

    1. search_use_cases(원본 질의) — 유사 SQL 조회
       → knowledge_fetcher에서 실행 시 내장 후속 수집 (테이블 메타 + 코드 메타)
    2. search_table_meta(8-slot 키워드) — 키워드 기반 테이블 검색
    3. search_code_meta(filter 컬럼) — UNRESOLVED 필터의 코드값 확인
    """
    steps: list[ExecutionStep] = []
    step_num = 1

    # (1) 유사 SQL 조회 — fetcher에서 실행 시 내장 후속 수집 자동 수행
    if original_query:
        steps.append(ExecutionStep(
            step=step_num,
            tool="search_use_cases",
            input=original_query,
            purpose="유사 SQL 조회 → 관련 테이블 메타 + 코드 자동 수집",
            expected_output="유사 SQL + 관련 테이블 메타/코드 (내장 후속 수집)",
        ))
        step_num += 1

    # (2) 8-slot 키워드로 테이블 메타 검색
    meta_query = _extract_meta_search_query(nq, original_query)
    if meta_query and meta_query not in searched_queries:
        steps.append(ExecutionStep(
            step=step_num,
            tool="search_table_meta",
            input=meta_query,
            purpose="8-slot 키워드 기반 테이블 메타 검색",
            expected_output="관련 테이블 목록 + 컬럼 구조",
        ))
        step_num += 1

    # (3) UNRESOLVED filter 컬럼의 코드 메타 조회
    for ki in knowledge_items:
        if ki.status == ConfidenceStatus.UNRESOLVED and "filter:" in ki.key:
            col_name = ki.key.split(":")[1].split("=")[0]
            steps.append(ExecutionStep(
                step=step_num,
                tool="search_code_meta",
                input=col_name,
                purpose=f"{col_name}의 코드값 확인",
                expected_output="코드값 목록",
            ))
            step_num += 1

    return steps
```

### 4.4 knowledge_fetcher 변경 — search_use_cases 내장 후속 수집

기존 `_run_step()` 내부에서 `search_use_cases` 결과가 있으면
**관련 테이블 메타 + 코드 메타를 내장 후속 처리로 자동 수집**한다.
별도 ExecutionStep을 추가하지 않으며, 기존 for 루프도 변경하지 않는다.

`_run_step()` 내부의 기존 `search_use_cases` 처리 블록 확장:

```python
# _run_step() 내부 — 기존 코드
if step.tool == "search_use_cases" and result:
    explored_use_cases.extend(result)

    # ── 내장 후속 수집 (신규) ──
    await _fetch_use_case_related_metas(
        result, searched_queries, candidate_tables, code_map,
    )
```

### 4.5 `_fetch_use_case_related_metas()` (knowledge_fetcher 신규 함수)

```python
async def _fetch_use_case_related_metas(
    use_cases: list[dict],
    searched_queries: list[str],
    candidate_tables: list[CandidateTable],
    code_map: dict[str, CodeMeta],
) -> None:
    """유사SQL에서 추출한 테이블 메타 + 코드 메타를 자동 수집한다.

    search_use_cases 실행의 내장 후속 처리.
    execution_plan에 스텝을 추가하지 않고,
    결과를 candidate_tables와 code_map에 직접 반영한다.
    """
    hints = extract_hints_from_use_cases(use_cases)
    if hints.is_empty():
        return

    already_queried = set(searched_queries)

    # (1) source_tables → 테이블 메타 수집
    tables_to_fetch = [
        t for t in hints.source_tables if t not in already_queried
    ]
    if tables_to_fetch:
        meta_results = await asyncio.gather(
            *(search_table_meta(t) for t in tables_to_fetch),
            return_exceptions=True,
        )
        for table_name, result in zip(tables_to_fetch, meta_results):
            searched_queries.append(table_name)
            if isinstance(result, list):
                new_tables = [
                    CandidateTable.from_meta(m) for m in result
                    if CandidateTable.from_meta(m) is not None
                ]
                candidate_tables.extend(new_tables)

    # (2) code_columns → 코드 메타 수집
    cols_to_fetch = [
        col for col in hints.code_columns if col not in already_queried
    ]
    if cols_to_fetch:
        code_results = await asyncio.gather(
            *(search_code_meta(col) for col in cols_to_fetch),
            return_exceptions=True,
        )
        for col_name, result in zip(cols_to_fetch, code_results):
            searched_queries.append(col_name)
            if isinstance(result, list):
                for item in result:
                    col = item.get("code_field", "")
                    if col and col not in code_map:
                        code_map[col] = CodeMeta(
                            column_name=col,
                            column_desc=item.get("code_field_desc", ""),
                            codes=item.get("codes", {}),
                        )
```

**설계 포인트:**

- 테이블 메타와 코드 메타를 각각 **병렬(asyncio.gather)** 로 수집하여 latency 최소화
- `searched_queries`에 추가하여 이후 스텝에서 **dedup 자동 적용**
- `candidate_tables`, `code_map`에 직접 반영 → knowledge_interpreter가 참조
- execution_plan, for 루프 변경 없음
- `StructuralHints`의 기존 필드(`source_tables`, `code_columns`)를 그대로 활용

### 4.6 pipeline.py 라우팅 변경

**노드 등록 변경:**

```python
# 기존
graph.add_node("planner", planner_node)
# 변경
graph.add_node("reasoning_preparer", reasoning_preparer_node)
```

**라우팅 변경:**

```python
# 기존
def _route_after_planner(state: PipelineState) -> str:
    if state.reason.fast_path_triggered:
        return "sql_generator"
    return "knowledge_fetcher"

# 변경
def _route_after_reasoning_preparer(state: PipelineState) -> str:
    return "knowledge_fetcher"  # 항상 fetcher로 — Fast-Path 제거
```

**F2 대응 — readiness_gate → recovery 진입 시 failure context:**

```python
def _route_after_readiness_gate(state: PipelineState) -> str:
    # ... 기존 로직 ...
    if verdict in ("explore", "replan"):
        if not reason.failure_type:
            reason.failure_type = FailureType.TERM_UNRESOLVABLE
            reason.failure_reason = _build_readiness_gap_reason(reason)
        # ...
```

### 4.7 knowledge_interpreter 변경

**변경 없음.** DONE 스텝의 result_ref를 참조하는 기존 로직이 그대로 동작한다.
knowledge_fetcher가 Phase 2를 수행하므로 `{table_observations}` 입력도 정상 공급된다.

### 4.8 recovery_agent 변경

**변경 없음.** H_INIT → FAILED 전이 + DeadEnd 기록 로직이 그대로 동작한다.

### 4.9 system_prompts.py 변경

```python
# 제거
PLANNER_SYSTEM = _reason("planner_system.txt")
```

### 4.10 thinking_modes.py 변경

```python
# 기존
"planner": "off",
# 변경
"reasoning_preparer": "off",
```

---

## 5. 데이터 모델 변경

### 5.1 Hypothesis 모델

**제거 대상 필드:**

| 필드 | 현재 용도 | 제거 근거 |
|------|----------|----------|
| `priority` | 복수 가설 정렬 | 초기 가설 1개 고정, recovery도 즉시 ACTIVE로 설정 |
| `based_on_use_case` | search_use_cases 스텝 추가 여부 | execution_plan이 Hypothesis 비의존 |
| `missing_terms` | LLM 가설의 부족 용어 목록 | LLM 가설 생성 제거됨 |

**안전한 접근:** 이번 리팩토링에서는 필드를 유지하되 reasoning_preparer에서 사용하지 않는다.
recovery_agent의 `_consume_next_pending()`에서 priority 기반 정렬 확인 후 별도 정리.

### 5.2 StructuralHints 모델

**변경 없음.** 기존 필드가 내장 후속 수집에 필요한 정보를 이미 포함한다:

- `source_tables: list[str]` — FROM/JOIN 테이블명 → 내장 후속 수집에서 `search_table_meta` 호출에 사용
- `code_columns: dict[str, list[str]]` — WHERE 절 코드 컬럼 + 값 → 내장 후속 수집에서 `search_code_meta` 호출에 사용

### 5.3 ReasoningState 영향

변경 없음. 기존 필드가 모두 호환된다.

### 5.4 fast_path_triggered 필드

reasoning_preparer에서 설정하지 않으므로 항상 False.
readiness_gate의 GENERATE verdict가 동일 역할을 수행한다.
필드 자체는 유지 (다른 곳에서 참조할 수 있으므로 별도 정리).

---

## 6. Fast-Path 처리

### 6.1 기존 Fast-Path

```
planner → (fast_path_triggered) → sql_generator (fetcher/interpreter 건너뜀)
```

### 6.2 제안 구조에서의 Fast-Path

reasoning_preparer 시점에서 Fast-Path 조건 판정이 불가하므로 **제거**.

```
reasoning_preparer → knowledge_fetcher → knowledge_interpreter
→ readiness_gate (GENERATE) → sql_generator
```

readiness_gate의 첫 GENERATE verdict가 기존 Fast-Path를 **자연 대체**한다.

**차이점:** 기존 Fast-Path는 knowledge_interpreter LLM 호출 1회를 절약했으나,
제안 구조에서는 항상 1회 실행된다. 이 비용(~2초)은 planner LLM 제거(~2-4초)로 상쇄된다.

---

## 7. 마이그레이션 전략

### 7.1 변경 파일 목록

| 파일 | 변경 유형 | 설명 |
|------|----------|------|
| `src/agents/nodes/reason/planner.py` → `reasoning_preparer.py` | **리네이밍 + 대폭 수정** | LLM 제거, 도구 호출 제거, rule-based plan |
| `src/agents/nodes/reason/knowledge_fetcher.py` | **수정** | 내장 후속 수집 (`_fetch_use_case_related_metas`) |
| `src/agents/graph/pipeline.py` | **수정** | 노드명 변경, Fast-Path 분기 제거, F2 대응 |
| `src/agents/nodes/__init__.py` | **수정** | export 변경 |
| `src/agents/nodes/system_prompts.py` | **수정** | `PLANNER_SYSTEM` 제거 |
| `src/agents/nodes/thinking_modes.py` | **수정** | 노드명 변경 |
| `resources/prompts/reason/planner_system.txt` | **제거** | 더 이상 사용하지 않음 |
| `src/agents/state/state.py` | 변경 없음 | `StructuralHints` 기존 필드로 충분 |
| `tests/auto/e2e/test_agentic_*.py` | **수정** | 노드명 변경 + 테스트 업데이트 |
| `docs/` 다수 | **수정** | planner → reasoning_preparer 용어 변경 |

### 7.2 변경하지 않는 파일

| 파일 | 이유 |
|------|------|
| `knowledge_interpreter.py` | DONE 스텝의 result_ref 참조 — 변경 불필요 |
| `recovery_agent.py` | 기존 로직 그대로 동작 |
| `readiness_gate.py` | 기존 로직 그대로 동작 |
| `confidence_scorer.py` | 기존 로직 그대로 동작 |

### 7.3 구현 순서

1. `planner.py` → `reasoning_preparer.py` 리네이밍 + 코드 리팩토링
2. `knowledge_fetcher.py`에 `_fetch_use_case_related_metas()` + 내장 후속 수집 로직 추가
3. `pipeline.py` 노드명/라우팅 변경 + F2 대응
4. `system_prompts.py`, `thinking_modes.py`, `__init__.py` 업데이트
5. 테스트 업데이트
6. 문서 업데이트 (planner → reasoning_preparer)

---

## 8. 리스크 및 대응

### R1: 내장 후속 수집으로 인한 tool_calls 증가

**상황:** 유사 SQL 3건에서 각 2-3개 테이블 + 코드 컬럼 추출 시,
후속 수집이 5-10건 추가될 수 있다.

**대응:**

- `_fetch_use_case_related_metas()`에서 source_tables 상한(예: 5개)을 설정
- code_columns도 상한(예: 5개)을 설정, 유사도 높은 SQL의 컬럼 우선
- `searched_queries`에 추가하여 이후 execution_plan 스텝에서 dedup 자동 적용
- `MAX_TOOL_CALLS` 가드가 전체 호출 횟수 제한

### R2: Cold Start (유사 SQL 0건)

**상황:** search_use_cases 결과가 0건이면 후속 수집이 발생하지 않는다.

**대응:** 8-slot 키워드 검색 + code_meta 검색만으로 진행.
knowledge_interpreter가 메타만으로 해석하고, 부족하면 recovery_agent가 추가 탐색.
현재 동작과 동일.

### R3: search_table_meta의 테이블명 정확 매칭 (F4)

**상황:** `search_table_meta()`는 MongoDB `$text` 검색을 사용하므로,
"TB_LOAN_MASTER" 같은 정확한 테이블명이 토큰화에서 분리될 수 있다.

**대응:** 구현 시 `tools.py`의 `search_table_meta()` 내부 로직을 확인.
정확 매칭이 필요하면 `get_table_by_name()` 등 별도 함수를 검토.
knowledge_fetcher의 `_extract_tables()`가 도구 결과에서 테이블을 추출하여
candidate_tables에 추가하므로, 검색 결과가 다소 부정확해도 보완된다.

### R4: 폐쇄망 모델 호환성

**상황:** LLM 호출 1회(planner) 감소 + knowledge_interpreter 첫 호출의 입력 품질 향상.

**대응:** 폐쇄망 환경에서 **latency 감소 + 해석 정확도 향상** 동시 달성.
특히 중형 모델(Solar Pro 2 70B)에서 풍부한 컨텍스트가 해석 품질에 큰 차이를 만든다.

---

## 부록: v1 → v3 설계 리뷰 반영 이력

| 리뷰 ID | 심각도 | 제목 | v3 반영 |
|---------|--------|------|---------|
| F1 | P1 | Phase 2 관찰 데이터 누락 | **해소** — knowledge_fetcher 위임 구조 |
| F2 | P1 | DeadEnd failure_type 부정확 | **반영** — 4.6절 failure context 설정 |
| F3 | P2 | 병렬화 순서 의존성 | **해소** — reasoning_preparer 도구 호출 없음, fetcher 내 내장 후속 수집 |
| F4 | P2 | 테이블명 정확 매칭 | **반영** — R3에서 대응 전략 기술 |
| F5 | P3 | loop_guard 카운팅 불일치 | **해소** — knowledge_fetcher가 카운팅 |
| A4 | - | Fast-Path 트리거 빈도 | **반영** — Fast-Path 자체를 제거, readiness_gate GENERATE로 대체 |

| 변경 | v1 | v2 | v3 (현재) |
|------|------|------|------|
| planner 도구 호출 | 직접 수행 | use_cases만 사전 조회 | **없음** |
| knowledge_fetcher 경유 | 건너뜀 | 경유 | **경유 + 내장 후속 수집** |
| Fast-Path | planner에서 판정 | planner에서 판정 | **제거** |
| 노드명 | planner | planner | **reasoning_preparer** |
| 유사SQL 코드 메타 수집 | 없음 | 없음 | **내장 후속 수집으로 자동 수집** |
