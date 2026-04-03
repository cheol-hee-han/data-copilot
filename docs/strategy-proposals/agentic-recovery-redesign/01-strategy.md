# Agentic Recovery Loop 재설계 전략

- **작성일**: 2026-03-31
- **상태**: 설계 완료, 구현 대기
- **선행 조건**: C-01(validate_sql_safety 중복), C-02(ConfidenceLevel 중복) 해소 권장
- **영향 범위**: `context_explorer.py`, `recovery_planner.py`, `confidence_evaluator.py`, `pipeline.py`, `state.py`
- **참조 문서**:
  - `docs/strategy-proposals/nl2sql-agentic-loop-design-strategy/` (현행 에이전틱 루프 원본 설계)
  - `docs/reviews/code/20260331-agents-code-quality-report.md` (C-03 이슈)

---

## 목차

1. 현황 분석 및 문제 정의
2. 설계 목표
3. 아키텍처 결정: 2-Phase Exploration
4. 비판적 검토 및 보완
5. 최종 설계안
6. LoopGuard 및 종료 조건 재배치
7. 마이그레이션 전략
8. 구현 우선순위 및 체크리스트

- 부록 A — recovery_agent 프롬프트 설계 가이드
- 부록 B — 폐쇄망 모델 호환성 고려사항

---

## 1. 현황 분석 및 문제 정의

### 1.1 현재 Reason Layer 구조

```
planner → context_explorer → confidence_evaluator
               ↑                     │
               │              ┌──────┴──────┐
               │           EXPLORE      REPLAN
               │              │            │
               └──────────────┘    recovery_planner
                                       │
                                       └──→ context_explorer (재진입)
```

| 노드 | 파일 | 라인 수 | 책임 |
|------|------|---------|------|
| `planner` | `nodes/reason/planner.py` | 644 | 쿼리 분해, hypothesis 생성, execution_plan, fast-path 판정 |
| `context_explorer` | `nodes/reason/context_explorer.py` | 1177 | **6-Phase 일괄 처리** (도구 실행 + 관찰 수집 + LLM 해석 + 지식 반영 + 테이블 선택 + 신뢰도 승격) |
| `confidence_evaluator` | `nodes/reason/confidence_evaluator.py` | 153 | 규칙 기반 readiness 판정 → Phase 전이 |
| `recovery_planner` | `nodes/reason/recovery_planner.py` | 481 | 실패 가설 관리, DeadEnd 기록, 새 hypothesis/execution_plan 생성 |
| `sql_generator` | `nodes/reason/sql_generator.py` | 333 | CONFIRMED 지식 + 후보 테이블로 SQL 생성 |
| `sql_validator` | `nodes/reason/sql_validator.py` | 420 | 3-Layer 검증 (안전성→구조→의미→실행) |

### 1.2 핵심 문제 3가지

#### 문제 1: context_explorer 과부하 (C-03)

`context_explorer.py`는 1177줄에 8개 이상의 책임이 혼재한다.

| Phase | 함수 | 책임 |
|-------|------|------|
| Phase 1 | `context_explorer_node()` 내부 인라인 루프 + `_run_step()`, `_should_skip_step()` | 도구 순차 실행 |
| Phase 2 | `_observe_all_date_distributions()`, `_sample_unsampled_tables()` | 관찰 데이터 수집 |
| Phase 3 | `_interpret_batch()` | 배치 LLM 해석 (1회) |
| Phase 4 | `_apply_batch_insights()` | 해석 결과 → 상태 반영 |
| Phase 5 | `_remove_unsuitable_tables()` | 부적합 테이블 필터링 |
| Phase 6 | 신뢰도 승격 + early exit 판정 | 루프 제어 |

**문제**: 초기 탐색과 recovery 탐색이 동일한 6-Phase 파이프라인을 공유한다. recovery에서 필요한 것은 "특정 지식 공백 1~2개를 정밀하게 채우는 것"인데, 매번 전체 6-Phase를 재실행하므로 불필요한 LLM 호출과 도구 실행이 발생한다.

#### 문제 2: recovery_planner와 실행의 단절

현재 recovery 흐름:

```
sql_validator FAIL → recovery_planner → context_explorer → confidence_evaluator
```

`recovery_planner`는 새 `execution_plan`을 생성하지만, 그 실행 결과를 직접 확인하지 못한다. execution_plan이 `context_explorer`에서 실행된 뒤 `confidence_evaluator`를 거쳐야 다시 `recovery_planner`에 도달하므로, **계획→실행→피드백** 루프가 3개 노드에 걸쳐 분산되어 있다.

```python
# recovery_planner.py — 계획만 생성하고 결과를 모름
async def recovery_planner_node(state):
    ...
    new_plan = _build_replan_execution(hypothesis)  # execution_plan 생성
    return {"reason": {"execution_plan": new_plan}}  # context_explorer에 위임
```

**문제**: recovery_planner가 도구 결과를 기반으로 **반응적(reactive)** 판단을 할 수 없다. "이 테이블 샘플을 봤더니 다른 테이블이 더 적합하다"는 판단이 즉시 다음 도구 호출로 이어지지 않는다.

#### 문제 3: 도구 실행이 일괄(batch) 전용

`context_explorer`의 도구 실행은 "execution_plan의 모든 PENDING 스텝을 순차 실행 → 전부 완료 후 LLM에 일괄 전달"하는 패턴이다.

```python
# context_explorer.py — Phase 1: 일괄 실행
for step in execution_plan:
    if step.status == StepStatus.PENDING:
        result = await execute_tool(step.tool, step.input)
        step.result_ref = result
# Phase 3: 전체 결과를 한번에 LLM에 전달
insights = await _interpret_batch(all_results)
```

**문제**: 초기 탐색에서는 이 패턴이 효율적이다 (search_use_cases + search_table_meta를 한번에 해석). 그러나 recovery에서는 "도구 A 결과를 보고 도구 B를 결정"하는 반응적 전략이 불가능하다. 예를 들어 `search_table_meta` 결과에서 코드 컬럼을 발견했을 때 즉시 `search_code_meta`를 호출해야 하지만, 현재 구조에서는 다음 replan 사이클까지 기다려야 한다.

### 1.3 현행 구조의 강점 (보존 대상)

다음 요소는 현행 설계에서 잘 작동하고 있으므로 재설계 시 반드시 보존한다.

| 요소 | 위치 | 보존 이유 |
|------|------|----------|
| 3-Layer 분리 (Interpret/Reason/Present) | `pipeline.py` | 관심사 분리가 명확 |
| ReasoningState 격리 | `state.py` | 에이전틱 루프를 독립적으로 제어 |
| 통합 실패 컨텍스트 (failure_type/failure_reason) | `state.py` | 결과 객체 없이 상태 기반 라우팅 |
| Readiness SSOT | `confidence_scorer.py` | 단일 진실 공급원으로 판정 일관성 |
| TOOL_MAP 디스패처 | `tools.py` | 도구 추가/제거가 선언적 |
| Hypothesis + DeadEnd 생명주기 | `recovery_planner.py` | 실패 학습이 구조화됨 |
| LoopGuard 5종 종료 조건 | `state.py` | 무한 루프 방지가 체계적 |
| Fast-path 바이패스 | `planner.py` | 단순 질의 최적화 |

---

## 2. 설계 목표

| 목표 | 측정 기준 | 우선순위 |
|------|----------|---------|
| **recovery의 반응적 도구 사용** | recovery_agent가 도구 결과를 보고 다음 도구를 즉시 결정할 수 있음 | P0 |
| **context_explorer 분해** | 단일 파일 1177줄 → 3개 파일, 각 300줄 이하 | P0 |
| **LLM 호출 효율** | recovery loop 1회당 LLM 호출 1회 (현행과 동일 또는 개선) | P0 |
| **폐쇄망 모델 호환** | Structured Output 기반, native tool-calling 미사용 | P1 |
| **기존 테스트 호환** | Phase 1 분리는 behavioral change 없음 | P1 |
| **Hypothesis 생명주기 보존** | PENDING→ACTIVE→FAILED, DeadEnd 기록 유지 | P1 |
| **LoopGuard 완전 보존** | 5종 종료 조건 동일하게 작동 | P1 |

---

## 3. 아키텍처 결정: 2-Phase Exploration

### 3.1 핵심 아이디어

**초기 탐색(Phase 1)은 deterministic 일괄 처리로, recovery(Phase 2)는 ReAct-style 반응적 루프로 분리한다.**

- **Phase 1 (Structured Exploration)**: 예측 가능한 기본 컨텍스트 수집. planner가 생성한 execution_plan을 그대로 실행하고 결과를 일괄 해석한다. 현행 context_explorer의 동작을 보존하되 파일을 분리한다.
- **Phase 2 (Agentic Recovery)**: 지식 공백을 반응적으로 채우는 루프. LLM이 현재 상태를 보고 도구를 선택하고, 결과를 해석하고, 다음 행동을 결정한다. 하나의 노드 내부에서 ReAct 루프가 동작한다.

### 3.2 Phase 1: Structured Exploration

기존 `context_explorer`의 6-Phase를 2개 노드로 **기계적 분리**한다.

```
planner → knowledge_fetcher → knowledge_interpreter → readiness_gate
```

#### knowledge_fetcher (기존 Phase 1-2 추출)

| 항목 | 내용 |
|------|------|
| **책임** | execution_plan의 도구를 순차 실행 + date distribution/sample rows 수집 |
| **LLM 호출** | 없음 (순수 I/O) |
| **입력** | `execution_plan: list[ExecutionStep]`, `candidate_tables: list[CandidateTable]` |
| **출력** | 각 step의 `result_ref`/`insight` 갱신, `candidate_tables`에 sample_rows/date 관찰 추가 |
| **추출 대상** | `context_explorer_node()` Phase 1 인라인 루프 → 함수 추출, `_run_step()`, `_should_skip_step()`, `_observe_all_date_distributions(candidate_tables)`, `_sample_unsampled_tables(candidate_tables)` |

#### knowledge_interpreter (기존 Phase 3-6 추출)

| 항목 | 내용 |
|------|------|
| **책임** | 도구 결과 일괄 LLM 해석 → knowledge_items/candidate_tables/code_map 갱신 → 테이블 선택 마킹 → 신뢰도 승격 |
| **LLM 호출** | 1회 (배치 해석) |
| **입력** | 도구 결과가 채워진 `execution_plan`, `candidate_tables`, `knowledge_items` |
| **출력** | 갱신된 `knowledge_items`, `candidate_tables`, `code_map`, `discovered_facts` |
| **추출 대상** | `_interpret_batch()`, `_apply_batch_insights()`, Phase 5 테이블 마킹 인라인 코드 (함수 추출 필요), `_promote_sampled_confidence()`, `_dedup_knowledge_items()` |

#### readiness_gate (기존 confidence_evaluator 리네이밍)

| 항목 | 내용 |
|------|------|
| **책임** | `evaluate_readiness()` 호출 → Phase 전이 결정 |
| **LLM 호출** | 없음 (규칙 기반) |
| **변경 사항** | 로직 변경 없음. 라우팅 분기에 `recovery_agent` 추가 |
| **기존 함수 재사용** | `confidence_scorer.evaluate_readiness()` — SSOT 유지 |

**Phase 1은 현행 context_explorer와 동일한 동작을 보장한다.** 코드 분리만 수행하고 behavioral change는 없다.

### 3.3 Phase 2: Agentic Recovery (ReAct Loop)

기존 `recovery_planner` + `context_explorer`의 recovery 경로를 **하나의 노드 내부 ReAct 루프**로 통합한다.

```
recovery_agent (내부 루프)
  ┌────────────────────────────────────────────┐
  │ 1. [Python] Hypothesis 상태 전이            │
  │    (ACTIVE→FAILED, DeadEnd 기록, PENDING 소비)│
  │                                              │
  │ 2. [LLM] 현재 상태 분석 + 도구 호출 결정      │
  │    → RecoveryDecision (max 4 tools)          │
  │                                              │
  │ 3. [Python] 도구 실행 (execute_tool 재사용)   │
  │                                              │
  │ 4. [LLM과 동일 turn] 결과 해석 + 다음 결정    │
  │    → "call_tools" → step 3 반복              │
  │    → "ready" → 루프 종료                     │
  │    → "give_up" → 루프 종료                   │
  │                                              │
  │ 5. [Python] LoopGuard 체크 (매 tool 실행 후) │
  └────────────────────────────────────────────┘
```

#### 왜 네이티브 tool-calling 대신 Structured Output인가

| 기준 | Native Tool-Calling | Structured Output (채택) |
|------|---------------------|------------------------|
| 도구 호출 수 제한 | 모델 재량 (제어 어려움) | 스키마에서 `max_items: 4` 강제 |
| 감사 추적 | tool_calls 메시지 파싱 필요 | `reasoning`, `target_knowledge_gap` 명시 |
| 폐쇄망 모델 호환 | Solar Pro 2의 tool-calling 안정성 미검증 | JSON 구조화 출력은 대부분 안정 |
| knowledge 갱신 | 별도 노드 필요 | 동일 응답에서 `knowledge_updates` 포함 |
| LLM 호출 횟수 | 1회/도구 (LangGraph ToolNode 패턴) | 1회/라운드 (라운드당 1-4개 도구) |

#### RecoveryDecision 스키마

```python
class ToolCall(BaseModel):
    """recovery_agent가 요청하는 단일 도구 호출"""
    tool: Literal[
        "search_table_meta", "search_code_meta", "search_manual",
        "search_glossary", "get_sample_rows", "get_date_distribution"
    ]
    kwargs: dict[str, str]
    purpose: str  # 이 호출로 채우려는 지식 공백

class KnowledgeUpdate(BaseModel):
    """도구 결과 해석 후 지식 갱신 지시"""
    key: str                                      # knowledge_item의 key
    new_status: Literal["PROBABLE", "CONFIRMED", "CONFLICTED"]
    evidence: str                                  # 근거
    value: str | None = None                       # 값 갱신 (테이블명, 컬럼명 등)

class RecoveryDecision(BaseModel):
    """recovery_agent의 LLM 출력 스키마"""
    analysis: str                                  # 현재 상황 분석
    lessons_learned: str                           # 이전 실패에서 배운 교훈 (DeadEnd에 첨부)
    action: Literal["call_tools", "ready", "give_up"]
    tool_calls: list[ToolCall] = Field(default_factory=list, max_length=4)
    knowledge_updates: list[KnowledgeUpdate] = Field(default_factory=list)
    table_updates: list[TableUpdate] = Field(default_factory=list)
    target_knowledge_gap: str                      # 이번 라운드에서 해소하려는 주요 공백
```

> **참고**: `TableUpdate` 스키마는 02-detailed-design.md 1.2절에 정의. candidate_table의 SELECT/REJECT/JOIN_KEY/DATE_COLUMN 갱신을 LLM이 구조화하여 제안.

**`search_use_cases`가 도구 목록에서 제외된 이유**: use_case 검색은 Phase 1(planner)에서 이미 수행되며, recovery에서 재검색해도 동일한 결과가 나올 확률이 높다. recovery_agent는 이미 `explored_use_cases`와 `structural_hints`를 참조할 수 있다.

#### recovery_agent 내부의 Hypothesis 관리

Hypothesis 상태 전이는 **LLM이 아닌 Python 코드**가 수행한다. 오픈소스 70B 모델이 복잡한 상태 전이를 JSON으로 정확히 출력하는 것은 불안정하기 때문이다.

```python
async def recovery_agent_node(state: PipelineState) -> dict:
    reason = state.reason

    # ── Step 1: Deterministic hypothesis 관리 (기존 recovery_planner 코드 재사용) ──
    if reason.current_hypothesis:
        reason.current_hypothesis.status = HypothesisStatus.FAILED
        reason.dead_ends.append(DeadEnd(
            hypothesis_id=reason.current_hypothesis.hypothesis_id,
            failure_type=reason.failure_type,
            reason=reason.failure_reason or "",
        ))

    next_hypo = _consume_next_pending(reason.hypotheses)
    if next_hypo:
        reason.current_hypothesis = next_hypo
    # next_hypo가 없으면 LLM이 새 hypothesis를 제안할 수 있음

    reason.loop_guard.increment_replan()

    # ── Step 2-4: ReAct 루프 ──
    tool_results: list[dict] = []

    while not should_terminate(reason):
        decision = await _call_recovery_llm(reason, tool_results)

        # lessons_learned를 최신 DeadEnd에 첨부
        if decision.lessons_learned and reason.dead_ends:
            reason.dead_ends[-1].lessons_learned = decision.lessons_learned

        # knowledge 갱신 적용 (deterministic)
        _apply_knowledge_updates(reason, decision.knowledge_updates)

        if decision.action in ("ready", "give_up"):
            break

        # 도구 실행
        tool_results = []
        for tc in decision.tool_calls:
            result = await execute_tool(tc.tool, tc.kwargs)
            tool_results.append({"tool": tc.tool, "purpose": tc.purpose, "result": result})
            reason.loop_guard.increment_tool_calls()

            if should_terminate(reason):
                break

    # ── Phase 전이 ──
    if decision.action == "ready":
        reason.phase = Phase.GENERATING
    elif decision.action == "give_up":
        reason.phase = Phase.DONE
        reason.final_status = FinalStatus.FAILURE

    return {"reason": reason}
```

### 3.4 Phase 3: SQL Generation (변경 최소화)

```
sql_generator → sql_validator
    ├── PASS → result_finalizer
    ├── SQL_SYNTAX (retry 가능) → sql_generator
    └── SEMANTIC/STRUCTURAL/EMPTY/DB_ERROR → recovery_agent
```

**변경점**: sql_validator 실패 시 `recovery_planner`를 거치지 않고 **직접 `recovery_agent`로 진입**한다. recovery_agent가 failure_type/failure_reason을 읽고 hypothesis 실패 처리 + 새 탐색을 자체적으로 수행한다.

**기존 recovery_planner의 `_build_replan_context()` 로직**은 recovery_agent의 프롬프트 빌더로 이관된다.

### 3.5 전체 그래프 흐름

```
┌─ INTERPRET LAYER ─────────────────────────────────────────────────────────┐
│ resolve_history → classify_intent → normalize_query → [clarification_handler]  │
└───────────────────────────────────────────────────────────────────────────┘
                              │
┌─ REASON LAYER ────────────────────────────────────────────────────────────┐
│                                                                           │
│  planner ──[fast_path]──────────────────────────→ sql_generator           │
│    │                                                    │                 │
│    └──→ knowledge_fetcher → knowledge_interpreter           │                 │
│                               │                         │                 │
│                         readiness_gate                   │                 │
│                          │    │    │                     │                 │
│                     READY │  NOT   │ ASK_USER            │                 │
│                          │ READY  │    │                 │                 │
│                          │    │   │ clarification_handler      │                 │
│                          │    │   │                      │                 │
│                          │    ▼   │                      │                 │
│                          │ recovery_agent ←──────────────┤ (validation    │
│                          │    │                          │  failure)       │
│                          │    │ READY                    │                 │
│                          │    ▼                          │                 │
│                          └──→ sql_generator ─→ sql_validator              │
│                                                    │                      │
│                                              PASS  │                      │
│                                                    ▼                      │
│                                            result_finalizer               │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
                              │
┌─ PRESENT LAYER ───────────────────────────────────────────────────────────┐
│ execute_sql → analyze_data → format_response                              │
└───────────────────────────────────────────────────────────────────────────┘
```

### 3.6 노드 매핑 (현행 → 신규)

| 현행 노드 | 신규 노드 | 관계 |
|-----------|----------|------|
| `context_explorer` (Phase 1-2) | `knowledge_fetcher` | 함수 추출 (기계적) |
| `context_explorer` (Phase 3-6) | `knowledge_interpreter` | 함수 추출 (기계적) |
| `confidence_evaluator` | `readiness_gate` | 리네이밍 (로직 동일) |
| `recovery_planner` + `context_explorer` (recovery 경로) | `recovery_agent` | **통합 재구현** |
| `planner` | `planner` | 변경 없음 |
| `sql_generator` | `sql_generator` | 변경 없음 |
| `sql_validator` | `sql_validator` | 라우팅 변경만 |
| `result_finalizer` | `result_finalizer` | 변경 없음 |

---

## 4. 비판적 검토 및 보완

### 검토 1: LLM 호출 효율 — recovery_agent + knowledge_integrator 분리 기각

**초기안**: recovery_agent(도구 결정) + tool_executor(실행) + knowledge_integrator(해석)를 별도 노드로 분리.

**문제**: recovery loop 1회당 LLM 2회 호출 (recovery_agent + knowledge_integrator). Solar Pro 2 70B 기준 call당 3-15초이므로, recovery 3회 시 **18-90초 추가 지연**. 현행 recovery 경로의 LLM 호출(recovery_planner 1회 + context_explorer 배치 해석 1회 = 2회/cycle)과 비교하면 열위.

**보완**: knowledge_integrator를 제거하고, recovery_agent 내부의 ReAct 루프에서 **도구 결과를 동일 LLM turn에 포함**하여 해석과 다음 행동 결정을 1회 호출로 처리.

```
[최종 채택 패턴]
recovery_agent 내부:
  LLM call 1: "상태 분석 + 도구 A,B 호출 결정"
  → 도구 A,B 실행 (no LLM)
  LLM call 2: "도구 A,B 결과 해석 + 다음 도구 C 호출 또는 ready 선언"
  → 도구 C 실행 (no LLM)
  LLM call 3: "도구 C 결과 해석 + ready 선언"

= 내부 루프 3회 = LLM 3회 (각 라운드에서 해석+결정 동시 수행)
```

현행 recovery 경로 (recovery_planner + context_explorer = LLM 2회/cycle × 최대 3 cycle = 6회)와 비교하면, 신규 설계는 동일하거나 더 적은 LLM 호출로 더 정밀한 탐색이 가능하다.

### 검토 2: Hypothesis 생명주기 유실 위험

**문제**: recovery_agent가 hypothesis 관리까지 LLM에 위임하면, 오픈소스 70B 모델이 `ACTIVE→FAILED` 전이나 `DeadEnd` 기록을 누락할 수 있다.

**보완**: 3.3절에서 확정한 대로, hypothesis 상태 전이는 **Python 코드(deterministic)**가 수행한다. LLM은 `analysis`, `lessons_learned`, `tool_calls`, `knowledge_updates`만 출력한다. 구체적으로:

| 동작 | 수행 주체 | 근거 |
|------|----------|------|
| ACTIVE → FAILED 전이 | Python 코드 | 상태 전이는 결정론적이어야 함 |
| DeadEnd 생성/기록 | Python 코드 | failure_type/reason은 이전 노드에서 설정됨 |
| lessons_learned 첨부 | LLM → Python 코드가 DeadEnd에 저장 | 교훈 추출은 LLM이 적합 |
| PENDING hypothesis 소비 | Python 코드 (우선순위순) | 순서 보장 필요 |
| 새 hypothesis 제안 | LLM (PENDING 소진 시) | 창의적 대안은 LLM이 적합 |
| knowledge_items 갱신 | LLM 제안 → Python 코드가 적용 | `KnowledgeUpdate` 스키마로 구조화 |

### 검토 3: 컨텍스트 윈도우 압박

**문제**: recovery loop 반복 시 누적 컨텍스트(dead_ends + discovered_facts + candidate_tables + tool_results)가 증가. Solar Pro 2 70B의 유효 컨텍스트 8-16K에서 2-3회 반복 후 한계에 도달할 수 있다.

**보완**: `_build_recovery_prompt()` 내부에 티어별 truncation 적용.

| 정보 | 기본 한도 | truncation 전략 |
|------|----------|----------------|
| confirmed_knowledge | 전체 포함 | truncation 없음 (핵심 정보) |
| unresolved_items | 전체 포함 | truncation 없음 (탐색 대상) |
| dead_ends | 전체 포함, lessons_learned 100자 | 실패 패턴 학습에 필수 |
| candidate_tables | SELECTED/PENDING만 | REJECTED 제외 |
| candidate_tables.columns | 주요 컬럼 20개 | 전체 컬럼 목록은 생략 |
| candidate_tables.sample_rows | 3행/테이블 | 관찰 목적에 충분 |
| discovered_facts | hypothesis당 최근 5개 | 오래된 사실은 knowledge_items에 반영됨 |
| tool_results (이전 라운드) | 최근 1라운드만 | 이전 결과는 knowledge_updates로 반영됨 |
| structural_hints | 전체 포함 | use_case에서 추출된 구조 힌트는 압축적 |

**예상 토큰 소모**: truncation 적용 시 recovery LLM call당 약 2,000-4,000 토큰 (입력). 70B 모델의 8K 윈도우 내에서 안전.

### 검토 4: 단일 vs 복수 지식 공백 처리

**문제**: recovery_agent가 iteration당 1개 공백만 처리하면, 독립적 공백 3개에 대해 3회 반복(3회 LLM 호출)이 필요.

**보완**: `tool_calls`를 최대 4개로 허용하여 **독립적 공백은 한 라운드에 batch 처리**. LLM이 동일 응답에서 여러 도구를 요청하고, 모든 결과를 다음 turn에서 한번에 해석한다.

```
예시: "지점 테이블 미확정 + 코드값 미확인 + 날짜 포맷 불명"

LLM call 1 (분석 + 도구 결정):
  tool_calls: [
    {tool: "get_sample_rows", kwargs: {table: "TB_BRANCH_INFO"}, purpose: "지점 테이블 구조 확인"},
    {tool: "search_code_meta", kwargs: {column_name: "branch_type_cd"}, purpose: "지점 유형 코드값 확인"},
    {tool: "get_date_distribution", kwargs: {table: "TB_LOAN_EXEC", column: "exec_dt"}, purpose: "날짜 범위 확인"}
  ]

→ 3개 도구 동시 실행

LLM call 2 (결과 해석 + ready 판정):
  knowledge_updates: [
    {key: "지점", new_status: "CONFIRMED", evidence: "TB_BRANCH_INFO에 branch_cd, branch_nm 존재"},
    {key: "지점유형코드", new_status: "CONFIRMED", evidence: "01=영업점, 02=출장소"},
    {key: "실행일자", new_status: "CONFIRMED", evidence: "exec_dt YYYYMMDD, 범위 20240101-20260331"}
  ]
  action: "ready"
```

= **LLM 2회로 3개 공백 해소** (iteration당 1개 처리 시 6회 필요했을 것)

### 검토 5: Fast-Path 보존

**문제**: 현행 planner의 fast_path는 context_explorer를 건너뛰고 sql_generator로 직행한다. 노드 이름 변경 시 라우팅 연결이 끊어질 수 있다.

**보완**: Fast-path 라우팅은 **타겟 노드명만 변경**하면 된다. 로직 변경 없음.

```python
# pipeline.py — 변경 전
def _route_after_planner(state):
    if state.reason.fast_path_triggered:
        return "sql_generator"
    return "context_explorer"

# pipeline.py — 변경 후
def _route_after_planner(state):
    if state.reason.fast_path_triggered:
        return "sql_generator"
    return "knowledge_fetcher"  # 이름만 변경
```

**추가 주의점**: sql_validator 실패로 fast-path SQL이 실패했을 때(`explore_after_fast_path` 경로), `knowledge_fetcher`로 보내야 한다 (recovery_agent가 아님). 초기 컨텍스트 자체가 없는 상태이므로 Phase 1부터 시작해야 한다.

```python
# pipeline.py — fast-path 실패 후 정상 탐색 전환
def _route_after_sql_validator(state):
    reason = state.reason
    if reason.fast_path_triggered and not reason.validated_sql:
        reason.fast_path_triggered = False  # fast-path 해제
        return "knowledge_fetcher"  # Phase 1부터 정상 수행
    ...
```

### 검토 6: 명확화(Clarification) 연동

**문제**: recovery_agent 내부 ReAct 루프 중 CONFLICTED knowledge_item을 발견하면 어떻게 처리하는가?

**보완**: recovery_agent는 **CONFLICTED 처리를 하지 않는다**. CONFLICTED 항목이 있으면 `action: "ready"`로 루프를 종료하고, `readiness_gate`에서 `ASK_USER` verdict로 `clarification_handler`에 위임한다.

이유:
- recovery_agent 내부에서 interrupt()를 호출하면 LangGraph의 상태 관리가 복잡해진다
- 현행 시스템도 `confidence_evaluator` → `clarification_handler` 경로를 사용하며, recovery_agent 내부에서 처리하지 않는다
- CONFLICTED는 "정보가 부족한 것"이 아니라 "상충하는 정보가 있는 것"이므로, 추가 도구 호출로 해결되지 않고 사용자 확인이 필요하다

### 검토 7: 테스트 가능성

**문제**: recovery_agent 내부의 ReAct 루프가 불투명하면 개별 iteration을 단위 테스트하기 어렵다.

**보완**: ReAct 루프의 단일 스텝을 독립 함수로 추출한다.

```python
async def _recovery_step(
    reason: ReasoningState,
    tool_results: list[dict],
) -> RecoveryDecision:
    """단일 recovery 스텝. 독립적으로 테스트 가능."""
    prompt = _build_recovery_prompt(reason, tool_results)
    response = await call_llm(prompt)
    return RecoveryDecision.model_validate_json(response)

async def recovery_agent_node(state: PipelineState) -> dict:
    """ReAct 루프 오케스트레이터."""
    reason = state.reason
    _handle_hypothesis_transition(reason)  # deterministic

    tool_results = []
    while not should_terminate(reason):
        decision = await _recovery_step(reason, tool_results)
        _apply_knowledge_updates(reason, decision.knowledge_updates)

        if decision.action != "call_tools":
            break

        tool_results = await _execute_tools(decision.tool_calls, reason)

    _finalize_phase(reason, decision)
    return {"reason": reason}
```

테스트 전략:
- `_recovery_step()`: LLM mock으로 RecoveryDecision 반환 → 프롬프트 빌딩 검증
- `_handle_hypothesis_transition()`: 순수 Python 함수 → hypothesis 상태 전이 검증
- `_apply_knowledge_updates()`: 순수 Python 함수 → knowledge_items 갱신 검증
- `recovery_agent_node()`: 통합 테스트 → 루프 종료 조건 + LoopGuard 검증

---

## 5. 최종 설계안

### 5.1 노드 최종 구성

| 노드 | 파일 (신규) | LLM | 변경 유형 |
|------|------------|-----|----------|
| `planner` | `nodes/reason/planner.py` | Yes | **변경 없음** |
| `knowledge_fetcher` | `nodes/reason/knowledge_fetcher.py` | No | context_explorer Phase 1-2 추출 |
| `knowledge_interpreter` | `nodes/reason/knowledge_interpreter.py` | Yes (1회) | context_explorer Phase 3-6 추출 |
| `readiness_gate` | `nodes/reason/readiness_gate.py` | No | confidence_evaluator 리네이밍 |
| `recovery_agent` | `nodes/reason/recovery_agent.py` | Yes (루프당 1회) | **핵심 신규** |
| `sql_generator` | `nodes/reason/sql_generator.py` | Yes | **변경 없음** |
| `sql_validator` | `nodes/reason/sql_validator.py` | Yes (L2b) | 라우팅 변경만 |
| `result_finalizer` | `nodes/reason/result_finalizer.py` | No | **변경 없음** |

### 5.2 파일별 예상 규모

| 파일 | 예상 라인 수 | 산출 근거 |
|------|-------------|----------|
| `knowledge_fetcher.py` | ~250 | `_execute_steps`(80) + `_run_step`(50) + `_should_skip_step`(30) + observation 함수들(90) |
| `knowledge_interpreter.py` | ~350 | `_interpret_batch`(120) + `_apply_batch_insights`(80) + table selection(60) + confidence promotion(40) + 프롬프트/파싱(50) |
| `readiness_gate.py` | ~160 | confidence_evaluator.py 거의 그대로 (153줄) |
| `recovery_agent.py` | ~400 | hypothesis 관리(80) + ReAct 루프(60) + 프롬프트 빌더(100) + 응답 파싱/적용(60) + RecoveryDecision 스키마(40) + 헬퍼(60) |

**총합: ~1,160줄** (현행 context_explorer 1,177줄 + recovery_planner 481줄 = 1,658줄에서 약 30% 감소)

### 5.3 라우팅 테이블

| 출발 노드 | 조건 | 도착 노드 |
|-----------|------|----------|
| `planner` | `fast_path_triggered` | `sql_generator` |
| `planner` | else | `knowledge_fetcher` |
| `knowledge_fetcher` | 항상 | `knowledge_interpreter` |
| `knowledge_interpreter` | 항상 | `readiness_gate` |
| `readiness_gate` | GENERATE | `sql_generator` |
| `readiness_gate` | EXPLORE (initial phase) | `knowledge_fetcher` |
| `readiness_gate` | REPLAN / EXPLORE (recovery) | `recovery_agent` |
| `readiness_gate` | ASK_USER | `clarification_handler` |
| `readiness_gate` | TERMINATE | `result_finalizer` |
| `recovery_agent` | `phase == GENERATING` | `sql_generator` |
| `recovery_agent` | `phase == DONE` (give_up) | `result_finalizer` |
| `recovery_agent` | CONFLICTED 발견 | `readiness_gate` (→ ASK_USER) |
| `sql_generator` | 항상 | `sql_validator` |
| `sql_validator` | PASS | `result_finalizer` |
| `sql_validator` | SYNTAX (retry 가능) | `sql_generator` |
| `sql_validator` | SEMANTIC/STRUCTURAL/EMPTY/DB_ERROR | `recovery_agent` |
| `sql_validator` | fast_path 실패 | `knowledge_fetcher` |
| `result_finalizer` | `validated_sql` 존재 | `execute_sql` |
| `result_finalizer` | else | `error_end` |

### 5.4 Phase 전이 매핑

| 현행 Phase | 전이 시점 | 신규 설계에서의 변경 |
|-----------|----------|-------------------|
| `PLANNING → EXPLORING` | planner 완료 | 변경 없음 |
| `EXPLORING → GENERATING` | readiness_gate: GENERATE | 변경 없음 |
| `EXPLORING → REPLANNING` | readiness_gate: REPLAN | → recovery_agent 진입 시 `RECOVERING` (신규 Phase 추가 검토) |
| `REPLANNING → EXPLORING` | recovery_planner 완료 | → recovery_agent 내부에서 처리 (외부 Phase 전이 불필요) |
| `GENERATING → VALIDATING` | sql_generator 완료 | 변경 없음 |
| `VALIDATING → GENERATING` | sql_validator: 구문 재시도 | 변경 없음 |
| `VALIDATING → REPLANNING` | sql_validator: 구조/의미 실패 | → `VALIDATING → recovery_agent` (REPLANNING Phase 사용) |

**Phase enum 변경 여부**: `RECOVERING`을 추가할지 `REPLANNING`을 재사용할지는 구현 시 결정. 현행 `REPLANNING`을 재사용하는 것이 State 변경을 최소화한다.

---

## 6. LoopGuard 및 종료 조건 재배치

### 6.1 5종 종료 조건의 신규 노드 배치

| 종료 조건 | 현행 체크 위치 | 신규 체크 위치 | 비고 |
|-----------|-------------|-------------|------|
| `total_tool_calls ≥ MAX_TOOL_CALLS` (20) | `context_explorer` 내부 | `recovery_agent` 내부 (매 tool 실행 후) | ReAct 루프 내부에서 매번 확인 |
| `replan_count ≥ MAX_REPLANS` (3) | `confidence_evaluator` | `readiness_gate` + `recovery_agent` 진입 시 | recovery_agent 진입 전에 사전 차단 |
| `generate_attempts ≥ MAX_GENERATES` (4) | `sql_generator` | `sql_generator` | 변경 없음 |
| `final_status == FAILURE` | `should_terminate()` | `readiness_gate` | 변경 없음 |
| hypotheses 소진 | `should_terminate()` | `recovery_agent` 내부 | give_up 판정으로 전환 |

### 6.2 increment 위치

| 카운터 | 현행 increment 위치 | 신규 increment 위치 |
|--------|-------------------|-------------------|
| `total_tool_calls` | `context_explorer._run_step()` | `knowledge_fetcher._run_step()` + `recovery_agent._execute_tools()` |
| `replan_count` | `recovery_planner_node()` 진입 시 | `recovery_agent_node()` 진입 시 |
| `generate_attempts` | `sql_generator_node()` 진입 시 | 변경 없음 |
| `local_fix_count` | `sql_validator` 경유 시 | 변경 없음 |

### 6.3 Force-Generate 로직 보존

현행 `confidence_evaluator`의 force-generate 로직:

```
if replan_count ≥ 2 AND readiness_score ≥ THRESHOLD_FORCE_GENERATE (0.55):
    verdict를 GENERATE로 override
```

이 로직은 `readiness_gate`에 그대로 유지된다. recovery_agent에서 돌아온 후 readiness_gate가 재평가할 때 동일하게 작동한다.

---

## 7. 마이그레이션 전략

### 7.1 단계별 실행 계획

#### Step 1: context_explorer 기계적 분리 (behavioral change 없음)

**목표**: `context_explorer.py` 1,177줄 → `knowledge_fetcher.py` + `knowledge_interpreter.py` + 공통 유틸리티

| 작업 | 내용 | 리스크 |
|------|------|--------|
| 1-1 | `knowledge_fetcher.py` 생성: `_execute_steps()`, `_run_step()`, `_should_skip_step()`, `_observe_all_date_distributions()`, `_sample_unsampled_tables()` 이동 | 낮음 — 함수 경계가 명확 |
| 1-2 | `knowledge_interpreter.py` 생성: `_interpret_batch()`, `_apply_batch_insights()`, `_remove_unsuitable_tables()`, 신뢰도 승격 로직 이동 | 낮음 — 함수 경계가 명확 |
| 1-3 | `pipeline.py` 엣지 수정: `context_explorer` → `knowledge_fetcher → knowledge_interpreter` | 낮음 — 노드명 변경만 |
| 1-4 | 기존 테스트 실행으로 동작 동일성 검증 | — |

**완료 기준**: 기존 e2e 테스트(`test_agentic_core.py`, `test_agentic_e2e.py`, `test_agentic_flow_trace.py`)가 수정 없이 통과.

#### Step 2: readiness_gate 리네이밍 (behavioral change 없음)

| 작업 | 내용 |
|------|------|
| 2-1 | `confidence_evaluator.py` → `readiness_gate.py` 복사/리네이밍 |
| 2-2 | `confidence_evaluator_node()` → `readiness_gate_node()` 리네이밍 |
| 2-3 | `pipeline.py` 노드 등록명 변경 |
| 2-4 | `nodes/__init__.py` export 업데이트 |

#### Step 3: recovery_agent 구현 (핵심 behavioral change)

| 작업 | 내용 | 리스크 |
|------|------|--------|
| 3-1 | `RecoveryDecision`, `ToolCall`, `KnowledgeUpdate` 스키마 정의 | 낮음 |
| 3-2 | `recovery_planner.py`에서 hypothesis 관리 코드 추출 → `_handle_hypothesis_transition()` | 중간 — 기존 로직 보존 검증 필요 |
| 3-3 | `_build_recovery_prompt()` 구현 (기존 `_build_replan_context()` 기반 + truncation) | 중간 — 프롬프트 품질 검증 필요 |
| 3-4 | `_recovery_step()` 구현 (LLM 호출 + RecoveryDecision 파싱) | 중간 |
| 3-5 | `_apply_knowledge_updates()` 구현 | 낮음 |
| 3-6 | `recovery_agent_node()` ReAct 루프 오케스트레이터 구현 | 중간 |
| 3-7 | `pipeline.py` 라우팅 수정: `recovery_planner` → `recovery_agent`, sql_validator 분기 변경 | 중간 |
| 3-8 | 기존 `recovery_planner.py` 제거 | — |

**완료 기준**:
- `_handle_hypothesis_transition()` 단위 테스트: ACTIVE→FAILED, DeadEnd 생성, PENDING 소비
- `_recovery_step()` 단위 테스트: mock LLM으로 RecoveryDecision 파싱 검증
- `recovery_agent_node()` 통합 테스트: LoopGuard 종료 조건 검증
- e2e 테스트: recovery 경로를 타는 골든셋 질의로 전체 흐름 검증

#### Step 4: 정리 및 검증

| 작업 | 내용 |
|------|------|
| 4-1 | 기존 `context_explorer.py` 제거 (Step 1 검증 후) |
| 4-2 | 기존 `recovery_planner.py` 제거 (Step 3 검증 후) |
| 4-3 | `nodes/__init__.py` 최종 정리 |
| 4-4 | dead prompt 정리 (W-04: CLARIFIER_SYSTEM 등) |
| 4-5 | 전체 테스트 스위트 실행 |

### 7.2 롤백 전략

Step 1-2는 behavioral change가 없으므로 롤백 필요성이 낮다. Step 3에서 문제 발생 시:

- `recovery_planner.py` + `context_explorer.py`를 복원하고 `pipeline.py` 라우팅을 원복
- `recovery_agent.py`는 별도 파일이므로 삭제만 하면 됨
- State 변경이 필드 추가만이므로 (제거 없음) backward compatible

---

## 8. 구현 우선순위 및 체크리스트

### P0 (필수 — 설계 목표 달성에 직접 기여)

- [ ] `knowledge_fetcher.py` 생성 및 함수 이동
- [ ] `knowledge_interpreter.py` 생성 및 함수 이동
- [ ] `pipeline.py` Phase 1 엣지 리와이어링
- [ ] Step 1 동작 동일성 검증 (기존 테스트 통과)
- [ ] `RecoveryDecision` / `ToolCall` / `KnowledgeUpdate` 스키마 정의
- [ ] `_handle_hypothesis_transition()` 구현 + 단위 테스트
- [ ] `_build_recovery_prompt()` 구현 (truncation 포함)
- [ ] `_recovery_step()` 구현 + 단위 테스트
- [ ] `_apply_knowledge_updates()` 구현 + 단위 테스트
- [ ] `recovery_agent_node()` ReAct 루프 구현
- [ ] `pipeline.py` recovery 엣지 리와이어링
- [ ] LoopGuard increment 위치 재배치 검증
- [ ] fast-path 실패 → `knowledge_fetcher` 라우팅 확인

### P1 (권장 — 품질/안정성)

- [ ] `readiness_gate.py` 리네이밍
- [ ] recovery_agent 프롬프트 A/B 테스트 (Claude vs Solar Pro 2)
- [ ] 컨텍스트 truncation 효과 검증 (토큰 수 측정)
- [ ] recovery 경로 e2e 골든셋 테스트 추가
- [ ] 기존 `context_explorer.py`, `recovery_planner.py` 제거

### P2 (향후 — 최적화)

- [ ] recovery_agent 내부 도구 실행 병렬화 (독립 도구 간 `asyncio.gather`)
- [ ] recovery 루프 trace/telemetry 연동 (dispatch_tracking_event)
- [ ] `RECOVERING` Phase 추가 여부 최종 결정

---

## 부록 A — recovery_agent 프롬프트 설계 가이드

### 시스템 프롬프트 구조

```
당신은 데이터 분석을 위한 SQL 생성 에이전트의 recovery 모듈입니다.
이전 시도가 실패했거나 지식이 부족하여 SQL을 생성할 수 없었습니다.
현재 상태를 분석하고, 부족한 지식을 채우기 위해 도구를 사용하세요.

## 현재 확인된 지식
{confirmed_knowledge}

## 아직 확인되지 않은 항목
{unresolved_items}

## 후보 테이블
{candidate_tables_summary}

## 이전 실패 기록 (이 경로들은 피하세요)
{dead_ends_summary}

## 사용 가능한 도구
- search_table_meta(query): 테이블/컬럼 메타데이터 검색
- search_code_meta(column_name): 코드값 매핑 조회
- search_manual(query): 업무 매뉴얼 검색
- search_glossary(term): 금융 용어사전 조회
- get_sample_rows(table_name, schema_name?, db_source?, limit?): 샘플 데이터 조회
- get_date_distribution(table_name, date_column, schema_name?, db_source?): 날짜 컬럼 분포 조회

## 이전 도구 실행 결과
{previous_tool_results}  ← 첫 turn에서는 비어있음

## 지시
1. unresolved_items 중 가장 중요한 공백을 식별하세요.
2. 독립적인 공백은 한번에 여러 도구로 조회하세요 (최대 4개).
3. dead_ends에 기록된 실패 패턴을 반복하지 마세요.
4. 도구 결과를 기반으로 knowledge_updates를 제안하세요.
5. 충분한 지식이 모이면 action: "ready"로 응답하세요.
6. 더 이상 시도할 수 있는 경로가 없으면 action: "give_up"으로 응답하세요.

아래 JSON 스키마에 맞춰 응답하세요:
{recovery_decision_schema}
```

### 프롬프트 설계 원칙

1. **dead_ends를 항상 포함**: 동일 경로 재시도를 방지하는 핵심 컨텍스트
2. **unresolved_items를 명시적으로 열거**: LLM이 "무엇이 부족한지"를 정확히 인식
3. **도구 설명에 금융 도메인 예시 포함**: "search_code_meta(column_name='loan_type_cd') → 여신 유형 코드값 조회" 형태
4. **이전 tool_results는 최근 1라운드만**: 컨텍스트 윈도우 절약
5. **스키마를 매번 명시**: 오픈소스 모델의 JSON 출력 안정성을 위해

---

## 부록 B — 폐쇄망 모델 호환성 고려사항

### Solar Pro 2 70B

| 항목 | 대응 |
|------|------|
| 컨텍스트 윈도우 (8-16K) | truncation 전략 필수, recovery 프롬프트 4K 이내 유지 |
| JSON 출력 안정성 | `response_format: {"type": "json_object"}` 사용, 실패 시 regex fallback 파싱 |
| tool-calling 미지원 | Structured Output 방식 채택 (본 설계의 기본 전제) |
| thinking 모드 없음 | `NODE_THINKING_MODES` 에서 recovery_agent = "off" |

### Qwen3.5 397B (예정)

| 항목 | 대응 |
|------|------|
| 컨텍스트 윈도우 (32K+) | truncation을 완화 가능, 그러나 기본 전략은 유지 |
| JSON 출력 안정성 | 양호, 그러나 thinking 모드에서 JSON 깨짐 가능 |
| thinking 모드 | `NODE_THINKING_MODES`에서 recovery_agent = "auto" 설정 가능 |
| tool-calling | 지원하지만 안정성 검증 후 전환 결정 |

### 모델 무관 설계 원칙

1. **Structured Output을 기본으로**: 모든 폐쇄망 모델에서 작동하는 최소 공통분모
2. **Fallback 파싱**: JSON 파싱 실패 시 regex로 `action`, `tool_calls` 추출
3. **프롬프트 명확성**: 암묵적 추론 최소화, 모든 컨텍스트를 명시적으로 제공
4. **스키마 반복 제시**: 매 turn마다 `RecoveryDecision` 스키마를 포함 (모델의 JSON 충실도 향상)
