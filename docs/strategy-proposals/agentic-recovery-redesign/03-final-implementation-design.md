# Agentic Recovery Loop 재설계 — 최종 구현 설계서

- **작성일**: 2026-04-01
- **상태**: 최종 확정, 구현 대기
- **기반 문서**:
  - 원안: `01-strategy.md`, `02-detailed-design.md` (2026-03-31)
  - 1차 리뷰 → 크로스리뷰 → 최종 통합 리뷰 (2026-04-01)
- **선행 조건**: C-01(validate_sql_safety 중복), C-02(ConfidenceLevel 중복) 해소 권장
- **영향 범위**: `context_explorer.py`, `recovery_planner.py`, `confidence_evaluator.py`, `pipeline.py`, `state.py`
- **코드 검증 기준**: `main` 브랜치 (`6491f9b`) — 코드베이스 직접 대조 완료

---

## 목차

1. [개요 및 문제 정의](#1-개요-및-문제-정의)
2. [설계 원칙 및 보존 대상](#2-설계-원칙-및-보존-대상)
3. [아키텍처: 2-Phase Exploration](#3-아키텍처-2-phase-exploration)
4. [State 변경 사항](#4-state-변경-사항)
5. [스키마 정의](#5-스키마-정의)
6. [노드별 상세 구현 명세](#6-노드별-상세-구현-명세)
7. [라우팅 테이블 및 전체 흐름](#7-라우팅-테이블-및-전체-흐름)
8. [LoopGuard 및 종료 조건](#8-loopguard-및-종료-조건)
9. [프로덕션 환경 대응](#9-프로덕션-환경-대응)
10. [프롬프트 설계](#10-프롬프트-설계)
11. [마이그레이션 전략](#11-마이그레이션-전략)
12. [구현 우선순위 및 체크리스트](#12-구현-우선순위-및-체크리스트)
13. [부록](#13-부록)

---

## 1. 개요 및 문제 정의

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

`context_explorer.py`는 1,177줄에 8개 이상의 책임이 혼재한다.

| Phase | 함수 | 책임 |
|-------|------|------|
| Phase 1 | `context_explorer_node()` 내부 인라인 루프 + `_run_step()`, `_should_skip_step()` | 도구 순차 실행 |
| Phase 2 | `_observe_all_date_distributions()`, `_sample_unsampled_tables()` | 관찰 데이터 수집 |
| Phase 3 | `_interpret_batch()` | 배치 LLM 해석 (1회) |
| Phase 4 | `_apply_batch_insights()` | 해석 결과 → 상태 반영 |
| Phase 5 | `_remove_unsuitable_tables()` | 부적합 테이블 필터링 |
| Phase 6 | 신뢰도 승격 + early exit 판정 | 루프 제어 |

**문제**: 초기 탐색과 recovery 탐색이 동일한 6-Phase 파이프라인을 공유한다. recovery에서 필요한 것은 "특정 지식 공백 1~2개를 정밀하게 채우는 것"인데, 매번 전체 6-Phase를 재실행하므로 불필요한 LLM 호출과 도구 실행이 발생한다.

**코드 검증**: 도구 실행이 290행에서 완료되고 LLM 해석이 297행에서 시작하는 깔끔한 분리점이 이미 존재하여, 기계적 분리가 안전하다.

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

---

## 2. 설계 원칙 및 보존 대상

### 2.1 설계 목표

| 목표 | 측정 기준 | 우선순위 |
|------|----------|---------|
| **recovery의 반응적 도구 사용** | recovery_agent가 도구 결과를 보고 다음 도구를 즉시 결정할 수 있음 | P0 |
| **context_explorer 분해** | 단일 파일 1,177줄 → 3개 파일, 각 300줄 이하 | P0 |
| **LLM 호출 효율** | recovery loop 1회당 LLM 호출 1회 (현행과 동일 또는 개선) | P0 |
| **폐쇄망 모델 호환** | Structured Output 기반, native tool-calling 미사용 | P1 |
| **기존 테스트 호환** | Phase 1 분리는 behavioral change 없음 | P1 |
| **Hypothesis 생명주기 보존** | PENDING→ACTIVE→FAILED, DeadEnd 기록 유지 | P1 |
| **LoopGuard 완전 보존** | 5종 종료 조건 동일하게 작동 | P1 |

### 2.2 보존 대상 (현행 설계의 강점)

다음 요소는 현행 설계에서 잘 작동하고 있으므로 재설계 시 반드시 보존한다.

| # | 요소 | 위치 | 보존 이유 |
|---|------|------|----------|
| S-1 | **Phase 1 기계적 분리** | context_explorer.py | 도구 실행(Phase 1-2)과 LLM 해석(Phase 3-6) 경계가 코드에서도 명확 (290행/297행). behavioral change 없이 1,177줄 해소 가능. CHESS(2024), DIN-SQL(NeurIPS 2023) 등 NL-to-SQL 연구와 부합. |
| S-2 | **Hypothesis 상태 전이의 Python 코드 수행** | recovery_planner.py | 상태 전이를 deterministic Python 코드로 수행. Solar Pro 2 70B에서 복잡한 상태 전이 JSON 안정적 출력 어려움. AgentBench(ICLR 2024)에서 70B 이하 모델의 복잡 상태 전이 실패율 높음 확인. ReAct 원논문: state tracking은 환경(코드)이 수행, LLM은 thought+action만 생성. |
| S-3 | **Structured Output 채택** | (신규 결정) | 폐쇄망 모델 호환 최소 공통분모. `max_items: 4` 강제, 감사 추적 용이, LLM 호출 횟수 절감. JSONSchemaBench(2025)에서 제약 디코딩이 생성 속도 50% 향상, 정확도 최대 4% 향상. |
| S-4 | **CONFLICTED 처리의 외부 위임** | confidence_evaluator → clarification_handler | recovery_agent 내부 `interrupt()`는 LangGraph 상태 직렬화 복잡. 단, ASK_USER 발동 기준은 "추론 불가 충돌"로 제한 (§9.1 참조). |
| S-5 | **Truncation 전략의 티어별 설계** | (신규 결정) | confirmed_knowledge 전량 포함, REJECTED 테이블 제외, tool_results 최근 1라운드. 예상 소모 2,000-4,000 토큰. Complexity Trap(2025): 단순 masking이 LLM 요약과 동등한 solve rate. |
| S-6 | **Fast-path 보존 + 실패 시 knowledge_fetcher** | planner.py | fast-path 실패 시 초기 컨텍스트가 없으므로 Phase 1부터 시작이 논리적으로 정확. |
| S-7 | **테스트 전략: 단일 스텝 독립 함수 추출** | (신규 결정) | `_recovery_step()`, `_handle_hypothesis_transition()`, `_apply_knowledge_updates()`를 독립 함수로 추출하여 단위 테스트 가능. MAC-SQL(COLING 2025)에서도 동일 패턴. |
| S-8 | **마이그레이션 Step 1→2→3→4 단계 실행** | (전략) | behavioral change 없는 Step 1-2를 먼저, 핵심 변경인 Step 3을 이후 수행하여 리스크 격리. |
| S-9 | 3-Layer 분리 (Interpret/Reason/Present) | `pipeline.py` | 관심사 분리가 명확 |
| S-10 | ReasoningState 격리 | `state.py` | 에이전틱 루프를 독립적으로 제어 |
| S-11 | 통합 실패 컨텍스트 (failure_type/failure_reason) | `state.py` | 결과 객체 없이 상태 기반 라우팅 |
| S-12 | Readiness SSOT | `confidence_scorer.py` | 단일 진실 공급원으로 판정 일관성 |
| S-13 | TOOL_MAP 디스패처 | `tools.py` | 도구 추가/제거가 선언적 |
| S-14 | Hypothesis + DeadEnd 생명주기 | `recovery_planner.py` | 실패 학습이 구조화됨 |
| S-15 | LoopGuard 5종 종료 조건 | `state.py` | 무한 루프 방지가 체계적 |
| S-16 | Fast-path 바이패스 | `planner.py` | 단순 질의 최적화 |

---

## 3. 아키텍처: 2-Phase Exploration

### 3.1 핵심 아이디어

**초기 탐색(Phase 1)은 deterministic 일괄 처리로, recovery(Phase 2)는 ReAct-style 반응적 루프로 분리한다.**

- **Phase 1 (Structured Exploration)**: 예측 가능한 기본 컨텍스트 수집. planner가 생성한 execution_plan을 그대로 실행하고 결과를 일괄 해석한다. 현행 context_explorer의 동작을 보존하되 파일을 분리한다.
- **Phase 2 (Agentic Recovery)**: 지식 공백을 반응적으로 채우는 루프. LLM이 현재 상태를 보고 도구를 선택하고, 결과를 해석하고, 다음 행동을 결정한다. 하나의 노드 내부에서 ReAct 루프가 동작한다.

**근거**:
- **CHESS** (Talaei et al., Stanford, 2024, arXiv:2405.16755): 4단계 분리 파이프라인에서 스키마 프루닝만으로 LLM 토큰 5배 감소 + 정확도 2% 향상. "컨텍스트 수집과 SQL 생성의 완전 분리가 핵심".
- **DIN-SQL** (Pourreza & Rafiei, NeurIPS 2023, arXiv:2304.11015): 태스크 분해 → 서브태스크 few-shot → 자기교정 패턴으로 단순 few-shot 대비 ~10% 향상.

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
| **추출 대상** | `_interpret_batch()`, `_apply_batch_insights()`, `_remove_unsuitable_tables()`, `_promote_sampled_confidence()`, `_dedup_knowledge_items()` |

#### readiness_gate (기존 confidence_evaluator 리네이밍)

| 항목 | 내용 |
|------|------|
| **책임** | `evaluate_readiness()` 호출 → Phase 전이 결정 |
| **LLM 호출** | 없음 (규칙 기반) |
| **변경 사항** | 로직 변경 없음. 라우팅 분기에 `recovery_agent` 추가. ASK_USER 발동 기준을 "추론 불가 충돌"로 제한 (§9.1). |
| **기존 함수 재사용** | `confidence_scorer.evaluate_readiness()` — SSOT 유지 |
| **추가 로직** | `last_verdict` 저장 (§4 참조), EXPLORE verdict PENDING 스텝 가드 (§7 참조) |

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
  │ 3. [Python] 도구 병렬 실행 (asyncio.gather)   │
  │                                              │
  │ 4. [LLM과 동일 turn] 결과 해석 + 다음 결정    │
  │    → "call_tools" → step 3 반복              │
  │    → "ready" → 루프 종료                     │
  │    → "give_up" → 루프 종료                   │
  │                                              │
  │ 5. [Python] LoopGuard 체크 (매 tool 실행 후) │
  │ 6. [Python] 진전 감지 (2회 연속 무변화 시 종료)│
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

**근거**:
- **JSONSchemaBench** (Guidance-AI/Microsoft Research, 2025, arXiv:2501.10868): 10K 실세계 JSON 스키마 벤치마크에서 제약 디코딩이 비제약 대비 생성 속도 50% 향상, 다운스트림 정확도 최대 4% 향상.
- **BFCL** (UC Berkeley): Qwen 3 14B가 F1 0.971로 GPT-4 수준이나, "memory, dynamic decision-making, long-horizon reasoning은 미해결".
- **StructEval** (Tiger AI Lab, 2025): OSS 모델의 구조화 출력 준수율이 상용 모델 대비 ~10점 낮음.

#### knowledge_integrator 분리 기각 사유

**초기안**: recovery_agent(도구 결정) + tool_executor(실행) + knowledge_integrator(해석)를 별도 노드로 분리.

**문제**: recovery loop 1회당 LLM 2회 호출 (recovery_agent + knowledge_integrator). Solar Pro 2 70B 기준 call당 3-15초이므로, recovery 3회 시 18-90초 추가 지연.

**최종 채택 패턴**: knowledge_integrator를 제거하고, recovery_agent 내부 ReAct 루프에서 도구 결과를 동일 LLM turn에 포함하여 해석과 다음 행동 결정을 1회 호출로 처리.

```
recovery_agent 내부:
  LLM call 1: "상태 분석 + 도구 A,B 호출 결정"
  → 도구 A,B 병렬 실행 (no LLM)
  LLM call 2: "도구 A,B 결과 해석 + 다음 도구 C 호출 또는 ready 선언"
  → 도구 C 실행 (no LLM)
  LLM call 3: "도구 C 결과 해석 + ready 선언"

= 내부 루프 3회 = LLM 3회 (각 라운드에서 해석+결정 동시 수행)
```

현행 recovery 경로 (recovery_planner + context_explorer = LLM 2회/cycle × 최대 3 cycle = 6회)와 비교하면, 신규 설계는 동일하거나 더 적은 LLM 호출로 더 정밀한 탐색이 가능하다. 단일 공백 시에는 동등~약간 열위일 수 있다.

#### recovery_agent 내부의 Hypothesis 관리

Hypothesis 상태 전이는 **LLM이 아닌 Python 코드**가 수행한다.

| 동작 | 수행 주체 | 근거 |
|------|----------|------|
| ACTIVE → FAILED 전이 | Python 코드 | 상태 전이는 결정론적이어야 함 |
| DeadEnd 생성/기록 | Python 코드 | failure_type/reason은 이전 노드에서 설정됨 |
| lessons_learned 첨부 | LLM → Python 코드가 DeadEnd에 저장 | 교훈 추출은 LLM이 적합 |
| PENDING hypothesis 소비 | Python 코드 (우선순위순) | 순서 보장 필요 |
| 새 hypothesis 제안 | LLM (PENDING 소진 시) | 창의적 대안은 LLM이 적합 |
| knowledge_items 갱신 | LLM 제안 → Python 코드가 적용 | `KnowledgeUpdate` 스키마로 구조화 |

#### 복수 지식 공백의 Batch 처리

`tool_calls`를 최대 4개로 허용하여 **독립적 공백은 한 라운드에 batch 처리**.

```
예시: "지점 테이블 미확정 + 코드값 미확인 + 날짜 포맷 불명"

LLM call 1 (분석 + 도구 결정):
  tool_calls: [
    {tool: "get_sample_rows", kwargs: {table: "TB_BRANCH_INFO"}, purpose: "지점 테이블 구조 확인"},
    {tool: "search_code_meta", kwargs: {column_name: "branch_type_cd"}, purpose: "지점 유형 코드값 확인"},
    {tool: "get_date_distribution", kwargs: {table: "TB_LOAN_EXEC", column: "exec_dt"}, purpose: "날짜 범위 확인"}
  ]

→ 3개 도구 동시 실행 (asyncio.gather)

LLM call 2 (결과 해석 + ready 판정):
  knowledge_updates: [
    {item_id: "K3", new_status: "CONFIRMED", evidence: "TB_BRANCH_INFO에 branch_cd, branch_nm 존재"},
    {item_id: "K5", new_status: "CONFIRMED", evidence: "01=영업점, 02=출장소"},
    {item_id: "K7", new_status: "CONFIRMED", evidence: "exec_dt YYYYMMDD, 범위 20240101-20260331"}
  ]
  action: "ready"
```

= **LLM 2회로 3개 공백 해소** (iteration당 1개 처리 시 6회 필요했을 것)

### 3.4 Phase 3: SQL Generation (변경 최소화)

```
sql_generator → sql_validator
    ├── PASS → result_finalizer
    ├── SQL_SYNTAX (retry 가능) → sql_generator
    ├── SEMANTIC_LOCAL (fix 가능) → sql_generator
    ├── SEMANTIC_LOCAL (fix 초과) → recovery_agent (entry_source=sql_validator)
    ├── STRUCTURAL/EMPTY/DB_ERROR → recovery_agent (entry_source=sql_validator)
    └── fast_path 실패 → knowledge_fetcher (exploration_phase=initial)
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
| `confidence_evaluator` | `readiness_gate` | 리네이밍 (로직 동일 + 소폭 보강) |
| `recovery_planner` + `context_explorer` (recovery 경로) | `recovery_agent` | **통합 재구현** |
| `planner` | `planner` | 변경 없음 (리셋 로직 추가만) |
| `sql_generator` | `sql_generator` | 변경 없음 |
| `sql_validator` | `sql_validator` | 라우팅 변경만 |
| `result_finalizer` | `result_finalizer` | 변경 없음 |

---

## 4. State 변경 사항

### 4.1 ReasoningState 필드 추가

```python
class ReasoningState(BaseModel):
    # ── 기존 필드 (변경 없음) ──
    phase: Phase
    hypotheses: list[Hypothesis]
    current_hypothesis: Hypothesis | None
    dead_ends: list[DeadEnd]
    knowledge_items: list[KnowledgeItem]
    candidate_tables: list[CandidateTable]
    execution_plan: list[ExecutionStep]
    discovered_facts: list[str]
    loop_guard: LoopGuard
    failure_type: str | None
    failure_reason: str | None
    fast_path_triggered: bool
    # ...

    # ── 신규 필드 ──

    exploration_phase: Literal["initial", "recovery"] = "initial"
    """현재 탐색 단계. planner_node 진입 시 "initial"로 리셋."""

    recovery_rounds: int = 0
    """recovery_agent 내부 ReAct 루프의 현재 진입 내 실행 라운드 수.
    recovery_agent_node 진입 시 0으로 리셋."""

    last_verdict: ReadinessVerdict | None = None
    """readiness_gate가 마지막으로 내린 verdict.
    Phase 변환 후에도 원본 verdict를 보존하여 라우팅에서 직접 참조."""

    recovery_entry_source: Literal["readiness_gate", "sql_validator"] | None = None
    """recovery_agent의 진입 경로. 프롬프트에서 활용하여 넓은 탐색 vs 특정 문제 해결을 구분."""

    inference_notes: list[str] = Field(default_factory=list)
    """추론으로 결정한 사항과 그 근거. Present Layer에서 사용자에게 표시."""

    conflicted_bounce_count: int = 0
    """recovery_agent → readiness_gate → recovery_agent CONFLICTED 왕복 횟수.
    recovery_agent가 CONFLICTED로 readiness_gate에 되돌릴 때 increment.
    planner_node 진입 시 0으로 리셋. MAX_CONFLICTED_BOUNCES(2) 초과 시 give_up."""

    is_force_generated: bool = False
    """True이면 force-generate 경로로 SQL이 생성됨.
    Present Layer에서 면책 고지 표시에 사용. planner_node 진입 시 False로 리셋."""
```

### 4.2 KnowledgeItem 필드 추가

```python
class KnowledgeItem(BaseModel):
    knowledge_id: str
    """자동 채번된 ID. "K1", "K2" 등. 코드에서 생성, LLM은 참조만."""

    key: str
    status: ConfidenceStatus
    value: str | None = None
    evidence: list[str] = Field(default_factory=list)
    is_inferred: bool = False
    """True이면 도구 증거 없는 관행적 추론으로 설정됨."""
```

> **설계 결정: ID 기반 참조로 전환**
>
> 기존의 `_find_knowledge_item`은 key 문자열 기반 부분 일치 fallback을 사용했으나, 한국어 금융 용어는 접미어 공유가 매우 빈번(`~일자`, `~코드`, `~금액`, `~건수`, `~비율`)하여 복수 매칭 위험이 있다.
>
> ```python
> # 위험 예시
> knowledge_items = [
>     KnowledgeItem(key="여신실행일자", value="exec_dt", status=CONFIRMED),
>     KnowledgeItem(key="만기일자", value="mtr_dt", status=UNRESOLVED),
> ]
> # LLM이 key="일자"로 update → "여신실행일자"와 첫 번째 매칭 → value 오염
> ```
>
> ID 기반 참조: "채번은 코드, 참조는 LLM" 원칙. `candidate_tables`(table_name 유니크)와 `execution_plan`(step 번호)은 기존 키가 충분하므로 별도 ID 추가 불필요. `knowledge_items`만 ID를 추가한다.

### 4.3 PROMOTION_ORDER 수정

```python
PROMOTION_ORDER = {
    ConfidenceStatus.UNRESOLVED: 0,
    ConfidenceStatus.CANDIDATE: 1,   # 추가 필수 — 현행 누락
    ConfidenceStatus.PROBABLE: 2,
    ConfidenceStatus.CONFIRMED: 3,
    ConfidenceStatus.CONFLICTED: 4,
}
```

**근거**: `ConfidenceStatus.CANDIDATE`는 `src/models/enums.py:72`에 실제 존재하며, `context_explorer.py:562`에서 CANDIDATE로 knowledge_item을 생성하고, `confidence_scorer.py:163`에서 점수 계산에 사용됨. PROMOTION_ORDER 누락 시 CANDIDATE→UNRESOLVED 역행이 허용됨.

### 4.4 Phase 전이 매핑

| 현행 Phase | 전이 시점 | 신규 설계에서의 변경 |
|-----------|----------|-------------------|
| `PLANNING → EXPLORING` | planner 완료 | 변경 없음 |
| `EXPLORING → GENERATING` | readiness_gate: GENERATE | 변경 없음 |
| `EXPLORING → REPLANNING` | readiness_gate: REPLAN | → recovery_agent 진입 시 `REPLANNING` 재사용 |
| `REPLANNING → EXPLORING` | recovery_planner 완료 | → recovery_agent 내부에서 처리 (외부 Phase 전이 불필요) |
| `GENERATING → VALIDATING` | sql_generator 완료 | 변경 없음 |
| `VALIDATING → GENERATING` | sql_validator: 구문 재시도 | 변경 없음 |
| `VALIDATING → REPLANNING` | sql_validator: 구조/의미 실패 | → `VALIDATING → recovery_agent` (REPLANNING Phase 사용) |

**Phase enum 결정**: 현행 `REPLANNING`을 재사용하여 State 변경을 최소화한다.

### 4.5 planner_node에서의 필드 리셋

**멀티턴 라우팅 오류 방지를 위해 planner_node 진입 시 ephemeral state를 리셋한다.**

```python
async def planner_node(state: PipelineState) -> dict:
    reason = state.reason
    reason.exploration_phase = "initial"    # 매 질의 시작 시 리셋
    reason.recovery_rounds = 0             # recovery 카운터 리셋
    reason.last_verdict = None             # verdict 리셋
    reason.recovery_entry_source = None    # 진입 경로 리셋
    reason.inference_notes = []            # 추론 메모 리셋
    reason.conflicted_bounce_count = 0    # CONFLICTED 왕복 카운터 리셋
    reason.is_force_generated = False      # force-generate 플래그 리셋
    ...
```

**위험 시나리오 (리셋 없을 때)**:

```
── 1번째 질의 ──
planner → ... → recovery_agent → exploration_phase = "recovery" → 성공

── 2번째 질의 (같은 세션) ──
planner → ... → readiness_gate → EXPLORE verdict
→ exploration_phase == "recovery" (1번째 질의에서 잔류!)
→ recovery_agent로 잘못 라우팅!
```

**근거**: LangGraph Checkpointer 패턴에서 "각 conversation turn의 시작에 ephemeral state를 리셋"하는 것이 기본 원칙.

---

## 5. 스키마 정의

### 5.1 RecoveryDecision (recovery_agent LLM 출력)

```python
class ToolCall(BaseModel):
    """recovery_agent가 요청하는 단일 도구 호출"""
    tool: Literal[
        "search_table_meta", "search_code_meta", "search_manual",
        "search_glossary", "get_sample_rows", "get_date_distribution",
        "search_use_cases",  # P2-2 조건부 복원
    ]
    kwargs: dict[str, str]
    purpose: str  # 이 호출로 채우려는 지식 공백

class KnowledgeUpdate(BaseModel):
    """도구 결과 해석 후 지식 갱신 지시"""
    item_id: str | None = None   # 기존 항목 갱신 시 "K1" 등, 신규 시 null
    key: str                      # 신규 생성 시에만 사용
    new_status: Literal["CANDIDATE", "PROBABLE", "CONFIRMED", "CONFLICTED"]
    evidence: str                 # 도구 증거 또는 "관행적 해석: ..."
    value: str | None = None
    is_inferred: bool = False     # True이면 도구 증거 없는 추론

class TableUpdate(BaseModel):
    """candidate_table의 SELECT/REJECT/JOIN_KEY/DATE_COLUMN 갱신"""
    table_name: str
    action: Literal["SELECT", "REJECT", "UPDATE_JOIN_KEY", "UPDATE_DATE_COLUMN"]
    value: str | None = None      # JOIN_KEY, DATE_COLUMN일 때 컬럼명
    reason: str

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

> **`search_use_cases` 관련 설계 결정**: 원안에서는 recovery_agent 도구 목록에서 제외했으나 (planner에서 이미 수행), 리뷰 결과 Qdrant SQL 이력이 사실상 유일한 참조 SQL 소스이므로 조건부 복원한다. 이전 검색 이력을 프롬프트에 포함하여 동일 검색어 중복을 방지한다 (§10 참조).

### 5.2 knowledge_items 적용 로직

> **구현 주의: 순차 적용 필수**
>
> 하나의 `RecoveryDecision`에 복수의 `knowledge_updates`가 포함될 수 있으며, 그 중 여러 개가 신규 항목(`item_id=None`)일 수 있다. ID 채번이 `len(reason.knowledge_items) + 1` 기반이므로, **반드시 하나씩 순차적으로 적용**해야 ID 충돌을 방지할 수 있다. bulk 처리(사전에 map을 만들고 일괄 적용)는 동일 ID 채번 위험이 있으므로 금지한다.
>
> ```python
> # 올바른 패턴: 순차 적용
> for update in decision.knowledge_updates:
>     _apply_knowledge_update(reason, update)  # 개별 호출, append 후 다음 처리
>
> # 금지 패턴: bulk map 선구축
> knowledge_map = {ki.knowledge_id: ki for ki in reason.knowledge_items}
> for update in updates:  # 신규 항목이 map에 반영되지 않아 ID 충돌
>     ...
> ```

```python
def _apply_knowledge_update(reason: ReasoningState, update: KnowledgeUpdate):
    """KnowledgeUpdate를 state에 적용. ID 기반 O(1) lookup.
    반드시 건별로 호출하여 신규 항목의 ID 채번이 순차적으로 이루어지도록 한다."""
    knowledge_map = {ki.knowledge_id: ki for ki in reason.knowledge_items}

    if update.item_id and update.item_id in knowledge_map:
        item = knowledge_map[update.item_id]
        # 승격만 허용 (PROMOTION_ORDER 기반)
        if PROMOTION_ORDER.get(update.new_status, 0) >= PROMOTION_ORDER.get(item.status, 0):
            item.status = update.new_status
        item.evidence.append(update.evidence)
        if update.value is not None:
            item.value = update.value
        if update.is_inferred:
            item.is_inferred = True
    else:
        # 신규 항목 생성
        new_id = f"K{len(reason.knowledge_items) + 1}"
        reason.knowledge_items.append(
            KnowledgeItem(
                knowledge_id=new_id,
                key=update.key,
                status=update.new_status,
                value=update.value,
                evidence=[update.evidence],
                is_inferred=update.is_inferred,
            )
        )
```

### 5.3 table_updates 적용 로직

```python
def _apply_table_updates(reason: ReasoningState, updates: list[TableUpdate]) -> None:
    """TableUpdate를 candidate_tables에 적용."""
    table_map = {ct.table_name: ct for ct in reason.candidate_tables}

    for update in updates:
        ct = table_map.get(update.table_name)
        if not ct:
            continue

        if update.action == "SELECT":
            ct.selection_status = "SELECTED"
            ct.selection_reason = update.reason
        elif update.action == "REJECT":
            ct.selection_status = "REJECTED"
            ct.selection_reason = update.reason
        elif update.action == "UPDATE_JOIN_KEY" and update.value:
            ct.join_key = update.value
        elif update.action == "UPDATE_DATE_COLUMN" and update.value:
            ct.date_column = update.value
```

---

## 6. 노드별 상세 구현 명세

### 6.1 knowledge_fetcher (~250줄)

| 항목 | 내용 |
|------|------|
| **파일** | `nodes/reason/knowledge_fetcher.py` |
| **핵심 함수** | `knowledge_fetcher_node(state) → dict` |
| **추출 함수** | `_execute_steps()` (80줄), `_run_step()` (50줄), `_should_skip_step()` (30줄), `_observe_all_date_distributions()` (45줄), `_sample_unsampled_tables()` (45줄) |
| **LLM 호출** | 없음 |
| **behavioral change** | 없음 — 순수 함수 추출 |

### 6.2 knowledge_interpreter (~350줄)

| 항목 | 내용 |
|------|------|
| **파일** | `nodes/reason/knowledge_interpreter.py` |
| **핵심 함수** | `knowledge_interpreter_node(state) → dict` |
| **추출 함수** | `_interpret_batch()` (120줄), `_apply_batch_insights()` (80줄), `_remove_unsuitable_tables()` (60줄), `_promote_sampled_confidence()` (40줄), `_dedup_knowledge_items()` (프롬프트/파싱 50줄) |
| **LLM 호출** | 1회 (배치 해석) |
| **behavioral change** | 없음 — 순수 함수 추출 |

### 6.3 readiness_gate (~180줄)

| 항목 | 내용 |
|------|------|
| **파일** | `nodes/reason/readiness_gate.py` |
| **핵심 함수** | `readiness_gate_node(state) → dict` |
| **기존 재사용** | `confidence_scorer.evaluate_readiness()` — SSOT 유지 |
| **LLM 호출** | 없음 (규칙 기반) |

**주요 변경 로직**:

```python
async def readiness_gate_node(state: PipelineState) -> dict:
    """readiness 판정만 수행. verdict 오버라이드는 라우팅 함수에 위임."""
    reason = state.reason
    verdict = evaluate_readiness(reason)

    # last_verdict 저장 (라우팅에서 직접 참조)
    reason.last_verdict = verdict
    reason.phase = VERDICT_TO_PHASE[verdict]

    # 주의: EXPLORE + PENDING 스텝 없음 → recovery 전환 판단은
    # _route_after_readiness_gate()에서 수행한다.
    # readiness_gate는 순수한 "판정" 책임만 유지하고,
    # verdict 변경은 라우팅 함수에서 처리하여 노드 책임 분리를 보존한다.

    return {"reason": reason}
```

> **설계 결정: readiness_gate에서 verdict 오버라이드 제거**
>
> 하네스 디자인에서 각 노드는 자신의 책임만 수행해야 한다. readiness_gate의 책임은 "판정(verdict)"이지 "판정 결과 변경"이 아니다. EXPLORE verdict에서 PENDING 스텝이 없을 때 recovery_agent로 전환하는 판단은 **라우팅 함수**(`_route_after_readiness_gate`)에서 수행한다. 이를 통해 readiness_gate는 순수한 판정 노드로 유지되고, 라우팅 로직의 변경이 readiness_gate 내부에 영향을 주지 않는다.

**ASK_USER 발동 기준 변경** (§9.1에서 상세 기술):

```python
class KnowledgeItem(BaseModel):
    # ... 기존 필드 ...
    is_critical: bool = False
    """True이면 SQL 생성에 필수적인 항목 (테이블 선택, 조인 키 등).
    planner에서 hypothesis 생성 시 함께 설정."""


def _is_unresolvable_conflict(ki: KnowledgeItem) -> bool:
    """추론으로 해결 불가능한 충돌인지 판별.

    True인 경우 (ASK_USER 필요):
    - 서로 다른 테이블을 사용해야 하는 완전히 다른 의미가 2개 이상 존재
    - 금융 지표 산출식이 충돌 (연체율 계산 방식 등)

    False인 경우 (추론으로 진행):
    - 단순 용어 모호성 (예금신규액 vs 건수) → 관행적 해석으로 추론
    - 날짜 범위 불확실 → 최근 기간으로 추론
    """
    if not ki.evidence or len(ki.evidence) < 2:
        return False
    # 충돌하는 evidence가 서로 다른 테이블을 가리키면 unresolvable
    table_refs = set()
    for ev in ki.evidence:
        # evidence에서 테이블명 패턴 추출 (구현 시 정교화 필요)
        if "TB_" in ev:
            table_refs.update(
                word for word in ev.split() if word.startswith("TB_")
            )
    return len(table_refs) >= 2


def _should_ask_user(reason: ReasoningState) -> bool:
    """ASK_USER는 '추론으로도 해결 불가능한' 경우에만 발동."""
    critical_conflicts = [
        ki for ki in reason.knowledge_items
        if ki.status == ConfidenceStatus.CONFLICTED and ki.is_critical
    ]
    unresolvable = [
        ki for ki in critical_conflicts
        if _is_unresolvable_conflict(ki)
    ]
    return len(unresolvable) > 0
```

### 6.4 recovery_agent (~400줄) — 핵심 신규

| 항목 | 내용 |
|------|------|
| **파일** | `nodes/reason/recovery_agent.py` |
| **핵심 함수** | `recovery_agent_node(state) → dict` |
| **헬퍼 함수** | `_handle_hypothesis_transition()`, `_recovery_step()`, `_execute_tools()`, `_apply_knowledge_updates()`, `_build_recovery_prompt()`, `_parse_recovery_response()`, `_finalize_recovery()`, `_snapshot_knowledge_state()` |
| **LLM 호출** | 루프당 1회 (내부 최대 `max_internal_rounds`회) |

#### 전체 구현 구조

```python
async def recovery_agent_node(state: PipelineState) -> dict:
    reason = state.reason

    # ── Step 1: Deterministic hypothesis 관리 ──
    _handle_hypothesis_transition(reason)
    reason.loop_guard.increment_replan()
    reason.recovery_rounds = 0  # 현재 진입 내 라운드 카운터 리셋

    # ── Step 2-4: ReAct 루프 (진전 감지 포함) ──
    tool_results: list[dict] = []
    decision: RecoveryDecision | None = None
    prev_knowledge_snapshot = _snapshot_knowledge_state(reason.knowledge_items)
    no_progress_count = 0

    for round_num in range(RECOVERY_MAX_INTERNAL_ROUNDS):
        if should_terminate(reason):
            break

        decision = await _recovery_step(reason, tool_results)
        # LLM 호출 실패 시 decision=None → 즉시 종료 (§6.4.1 참조)
        if decision is None:
            break
        reason.recovery_rounds = round_num + 1

        # lessons_learned를 최신 DeadEnd에 첨부
        if decision.lessons_learned and reason.dead_ends:
            reason.dead_ends[-1].lessons_learned = decision.lessons_learned

        # knowledge 갱신 적용 (deterministic)
        _apply_knowledge_updates(reason, decision.knowledge_updates)

        # table 갱신 적용
        _apply_table_updates(reason, decision.table_updates)

        # discovered_facts 갱신 (knowledge_updates가 있는 경우에만)
        if decision.analysis and decision.knowledge_updates:
            reason.discovered_facts.append(f"[recovery] {decision.analysis}")

        # inference_notes 기록 (ready 시)
        if decision.action == "ready":
            _record_inference_notes(reason)

        if decision.action in ("ready", "give_up"):
            break

        # ── 진전 감지 ──
        curr_snapshot = _snapshot_knowledge_state(reason.knowledge_items)
        if curr_snapshot == prev_knowledge_snapshot:
            no_progress_count += 1
            if no_progress_count >= 2:
                decision = RecoveryDecision(
                    analysis="2회 연속 진전 없음, 탐색 중단",
                    action="give_up",
                    lessons_learned="추가 도구 호출로도 지식 상태 변화 없음",
                    target_knowledge_gap="no_progress",
                )
                break
        else:
            no_progress_count = 0
        prev_knowledge_snapshot = curr_snapshot

        # ── 도구 병렬 실행 ──
        tool_results = await _execute_tools(decision.tool_calls, reason)

    # ── Phase 전이 ──
    _finalize_recovery(reason, decision)

    return {"reason": reason}
```

#### _handle_hypothesis_transition (기존 recovery_planner 코드 재사용)

```python
def _handle_hypothesis_transition(reason: ReasoningState) -> None:
    """Deterministic hypothesis 상태 전이."""
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
```

#### _execute_tools (병렬 실행)

```python
# config.py
TOOL_EXECUTION_TIMEOUT: int = 15  # 개별 도구 타임아웃 (초), 폐쇄망 환경에서 조정 가능
TOOL_BATCH_TIMEOUT: int = 30      # 병렬 실행 전체 타임아웃 (초)


async def _execute_tools(
    tool_calls: list[ToolCall],
    reason: ReasoningState,
) -> list[dict]:
    """독립 도구 병렬 실행. 개별 타임아웃 + 전체 타임아웃 적용."""
    tasks = [
        asyncio.wait_for(
            _execute_single_tool(tc, reason),
            timeout=TOOL_EXECUTION_TIMEOUT,
        )
        for tc in tool_calls
    ]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    results = []
    for tc, raw in zip(tool_calls, raw_results):
        if isinstance(raw, asyncio.TimeoutError):
            results.append({
                "tool": tc.tool, "purpose": tc.purpose,
                "status": "timeout",
                "result": f"도구 실행 {TOOL_EXECUTION_TIMEOUT}초 초과 — 다른 도구로 전환하세요",
            })
        elif isinstance(raw, Exception):
            results.append({
                "tool": tc.tool, "purpose": tc.purpose,
                "status": "error", "result": str(raw),
            })
        else:
            results.append({
                "tool": tc.tool, "purpose": tc.purpose,
                "status": "success", "result": raw,
            })
        reason.loop_guard.increment_tool_calls()

    return results
```

> **설계 결정: 개별 도구 타임아웃 + status="timeout" 분리**
>
> `asyncio.gather`는 가장 느린 태스크를 기다리므로, 4개 도구 중 1개가 지연되면 나머지 3개의 결과도 대기 상태가 된다. `asyncio.wait_for`로 개별 도구에 타임아웃을 적용하면, 타임아웃된 도구만 `{"status": "timeout"}`으로 처리되고 나머지 결과는 즉시 사용 가능하다. LLM은 다음 라운드에서 타임아웃된 도구를 회피하거나 대체 전략을 세울 수 있다. PostgreSQL `get_sample_rows`가 대용량 테이블에서 지연되는 경우가 주요 타겟이다.

**근거**: Latency-Aware Orchestration (2025, arXiv:2601.10560) — "parallel scaling은 sequential 대비 1.6x 빠름". 프롬프트 수준에서 도구 간 독립성 규칙을 명시하여 의존성 충돌을 방지한다.

#### _execute_single_tool (kwargs → 현행 tool_input 변환 어댑터)

```python
async def _execute_single_tool(tc: ToolCall, reason: ReasoningState) -> Any:
    """ToolCall의 kwargs를 현행 tools.py의 tool_input 형식으로 변환."""
    tool_name = tc.tool

    if tool_name in ("search_table_meta", "search_manual", "search_glossary"):
        tool_input = tc.kwargs.get("query") or tc.kwargs.get("term", "")
    elif tool_name == "search_code_meta":
        tool_input = tc.kwargs.get("column_name", "")
    elif tool_name == "search_use_cases":
        tool_input = tc.kwargs.get("query", "")
    elif tool_name == "get_sample_rows":
        parts = [tc.kwargs.get("table_name", "")]
        if tc.kwargs.get("schema_name"):
            parts.append(tc.kwargs["schema_name"])
        if tc.kwargs.get("db_source"):
            parts.append(tc.kwargs["db_source"])
        tool_input = ",".join(parts)
    elif tool_name == "get_date_distribution":
        parts = [
            tc.kwargs.get("table_name", ""),
            tc.kwargs.get("date_column", ""),
        ]
        if tc.kwargs.get("schema_name"):
            parts.append(tc.kwargs["schema_name"])
        if tc.kwargs.get("db_source"):
            parts.append(tc.kwargs["db_source"])
        tool_input = ",".join(parts)
    else:
        tool_input = next(iter(tc.kwargs.values()), "")

    return await execute_tool(tool_name, tool_input)
```

> **중요**: 현행 `tools.py`의 도구들은 쉼표 구분 문자열을 파싱한다 (예: `_tool_get_sample_rows`에서 `tool_input.split(",")`). `json.dumps(tc.kwargs)`로 변환하면 파싱 실패한다. 이 어댑터는 Step 3 구현 시 필수이며, 추후 `execute_tool` 시그니처를 kwargs 기반으로 변경하면 어댑터 자체가 불필요해진다.

#### _finalize_recovery (give_up 시 즉시 종료 + force-generate 내부 판정)

```python
def _finalize_recovery(reason: ReasoningState, decision: RecoveryDecision | None) -> None:
    """recovery_agent 종료 시 Phase 전이. readiness_gate 재진입 없이 즉시 결정."""

    # decision이 None인 경우 (should_terminate로 루프 미실행, 또는 파싱 완전 실패)
    if decision is None:
        reason.phase = Phase.DONE
        reason.final_status = FinalStatus.FAILURE
        return

    if decision.action == "ready":
        reason.phase = Phase.GENERATING
        return

    # give_up 또는 should_terminate
    if decision.action == "give_up" or should_terminate(reason):
        score = calculate_readiness(reason)
        if score >= THRESHOLD_FORCE_GENERATE:
            reason.phase = Phase.GENERATING  # force-generate 직접 판정
            _attach_force_generate_disclaimer(reason)
        else:
            reason.phase = Phase.DONE
            reason.final_status = FinalStatus.FAILURE
        return

    # call_tools로 끝난 경우 (max_rounds 도달)
    score = calculate_readiness(reason)
    if score >= THRESHOLD_FORCE_GENERATE:
        reason.phase = Phase.GENERATING
        _attach_force_generate_disclaimer(reason)
    else:
        reason.phase = Phase.DONE
        reason.final_status = FinalStatus.FAILURE
```

> **설계 결정: give_up 시 readiness_gate 재진입 차단**
>
> 리뷰 결과, give_up 후 readiness_gate로 돌아가면 REPLAN → recovery_agent → give_up → 3회 반복의 무한 루프가 형성될 수 있다. recovery_agent 내부에서 force-generate 판정을 직접 수행하여, 추가 state 필드 없이 깔끔하게 처리한다.
>
> **프로덕션 중요도**: "선 추론 후 표시" 정책에 따라 give_up이어도 score가 일정 수준이면 추론 기반으로 SQL을 생성하고 결과에 주의사항을 표시해야 하며, 70B 모델에서 give_up 빈도가 더 높으므로 이 경로의 안정성이 정확도에 직접적 영향.
>
> **근거**:
> - MAST (arXiv:2503.13657): "lack of termination criteria"가 System Design Issues의 직접 원인.
> - LLM Repetition Problem (arXiv:2512.04419): "once the model enters a repetitive state, the expected escape time is infinite under greedy decoding". 외부 종료 메커니즘 필수.

#### _attach_force_generate_disclaimer (force-generate 면책 고지)

```python
def _attach_force_generate_disclaimer(reason: ReasoningState) -> None:
    """force-generate 경로 진입 시 면책 고지를 inference_notes에 추가.
    Present Layer의 format_response에서 사용자에게 표시된다 (§9.1.1 참조)."""
    reason.is_force_generated = True  # format_response에서 참조
    reason.inference_notes.insert(0,
        "확인된 정보가 충분하지 않아 일부 추론을 포함하여 조회하였습니다. "
        "결과가 예상과 다를 경우 구체적으로 요청해 주세요."
    )
```

> **설계 결정: force-generate 시 면책 고지 필수**
>
> give_up + force-generate 경로에서 생성된 SQL은 불확실한 추론 기반 결과이지만, 사용자는 정상 응답으로 인식한다. 금융 도메인에서 추론 기반 결과를 투명하게 전달하지 않으면 사용자 신뢰에 직접적 영향을 미친다. `is_force_generated` 플래그와 `inference_notes` 선두 삽입을 통해 Present Layer에서 결과 상단에 면책 고지를 표시한다 (§9.1.1의 렌더링 규칙 참조).

#### _parse_recovery_response (fallback 안전성)

```python
def _parse_recovery_response(response: str) -> RecoveryDecision:
    """LLM 응답을 RecoveryDecision으로 파싱. 3단계 fallback."""

    # 1차: 표준 JSON 파싱
    try:
        return RecoveryDecision.model_validate_json(response)
    except ValidationError:
        pass

    # 2차: JSON 블록 추출 후 파싱
    json_match = re.search(r'\{[\s\S]*\}', response)
    if json_match:
        try:
            return RecoveryDecision.model_validate_json(json_match.group())
        except ValidationError:
            pass

    # 3차: Fallback — action만 추출, call_tools는 give_up으로 안전 전환
    action = "give_up"  # 기본값은 안전한 give_up
    if re.search(r'"action"\s*:\s*"ready"', response):
        action = "ready"
    # call_tools는 tool_calls 없이 의미 없으므로 give_up 유지

    logger.warning(
        "recovery LLM 응답 JSON 파싱 실패, fallback 적용",
        action=action,
        response_preview=response[:200],
    )

    return RecoveryDecision(
        analysis=f"LLM 응답 파싱 실패 — fallback action: {action}",
        action=action,
        lessons_learned="",
        target_knowledge_gap="parsing_failure",
    )
```

> **설계 결정: fallback에서 call_tools 차단**
>
> 파인튜닝 없는 70B 모델에서 JSON 파싱 실패는 예외가 아니라 **정상 운영 시나리오**. StructEval(2025) 기준 OSS 모델의 구조화 출력 준수율이 상용 대비 ~10점 낮음. action이 `call_tools`인데 tool_calls가 비어있으면 silent failure이므로, fallback에서는 `give_up`으로 안전 전환한다.

#### 6.4.1 _recovery_step의 LLM 호출 실패 처리

LLM 호출은 타임아웃, 네트워크 오류, rate limit 등으로 언제든 실패할 수 있다. `_recovery_step()`에서 LLM 호출 자체의 예외를 catch하여 안전하게 처리한다.

```python
async def _recovery_step(
    reason: ReasoningState,
    tool_results: list[dict],
) -> RecoveryDecision | None:
    """LLM 호출 + RecoveryDecision 파싱. 실패 시 None 반환."""
    prompt = _build_recovery_prompt(reason, tool_results)

    try:
        response = await llm_client.generate(
            prompt=prompt,
            response_format={"type": "json_object"},
            timeout=LLM_CALL_TIMEOUT,  # config 기반 (기본 30초)
        )
    except Exception as e:
        logger.warning(
            "recovery LLM 호출 실패",
            error=str(e),
            round=reason.recovery_rounds,
        )
        return None  # caller에서 _finalize_recovery(decision=None) 경로로 처리

    return _parse_recovery_response(response)
```

> **설계 결정: LLM 호출 실패 시 재시도하지 않고 즉시 None 반환**
>
> 하네스 엔지니어링의 기본 가정: "LLM 호출은 언제든 실패할 수 있다". `_recovery_step()`이 `None`을 반환하면 ReAct 루프가 즉시 종료되고, `_finalize_recovery(decision=None)`에서 `Phase.DONE + FAILURE`로 안전 종료된다. recovery_agent 내부에서 재시도하면 루프 내 에러 누적 위험이 있으므로, 재시도는 상위 레이어(pipeline runner)의 exponential backoff에 위임한다. 단, 이 경로는 사용자에게 "일시적 오류, 다시 시도해 주세요" 메시지로 전달되어야 한다.

#### _snapshot_knowledge_state (진전 감지)

```python
def _snapshot_knowledge_state(items: list[KnowledgeItem]) -> tuple:
    """knowledge_items의 상태 스냅샷 — 변화 감지용."""
    return tuple(
        (ki.knowledge_id, ki.status, ki.value)
        for ki in sorted(items, key=lambda x: x.knowledge_id)
    )
```

> **설계 결정: 2회 연속 무변화 시 조기 종료**
>
> 70B 모델 + 부실 메타데이터 조합에서 "도구를 호출했지만 유용한 정보를 못 찾는" 케이스가 빈번. 70B 모델 기준 라운드당 3-15초 → 진전 없는 5라운드 = 최대 75초 낭비.
>
> **근거**:
> - Pre-Act (arXiv:2505.09970): 불필요 반복 방지가 Action Recall +70% 달성의 핵심.
> - AgentBench (ICLR 2024): "Task Limit Exceeded" 실패 유형은 progress detection 없이 max_rounds만 의존할 때 전형적.

#### _record_inference_notes (추론 근거 기록)

```python
def _record_inference_notes(reason: ReasoningState) -> None:
    """ready 선언 시 추론 기반 PROBABLE 항목에 대해 inference_notes 기록."""
    for ki in reason.knowledge_items:
        if ki.status in (ConfidenceStatus.PROBABLE, ConfidenceStatus.CANDIDATE) and ki.is_inferred:
            reason.inference_notes.append(
                f"'{ki.key}'를 '{ki.value}' 기준으로 해석하였습니다 "
                f"({ki.evidence[-1] if ki.evidence else '추론'})"
            )
```

### 6.5 파일별 예상 규모

| 파일 | 예상 라인 수 | 산출 근거 |
|------|-------------|----------|
| `knowledge_fetcher.py` | ~250 | `_execute_steps`(80) + `_run_step`(50) + `_should_skip_step`(30) + observation 함수들(90) |
| `knowledge_interpreter.py` | ~350 | `_interpret_batch`(120) + `_apply_batch_insights`(80) + table selection(60) + confidence promotion(40) + 프롬프트/파싱(50) |
| `readiness_gate.py` | ~180 | confidence_evaluator.py 기반(153줄) + last_verdict/PENDING 가드 추가 |
| `recovery_agent.py` | ~400 | hypothesis 관리(80) + ReAct 루프(80) + 프롬프트 빌더(100) + 응답 파싱/적용(60) + 스키마(40) + 헬퍼(40) |

**총합: ~1,180줄** (현행 context_explorer 1,177줄 + recovery_planner 481줄 = 1,658줄에서 약 29% 감소)

---

## 7. 라우팅 테이블 및 전체 흐름

### 7.1 라우팅 테이블

| 출발 노드 | 조건 | 도착 노드 |
|-----------|------|----------|
| `planner` | `fast_path_triggered` | `sql_generator` |
| `planner` | else | `knowledge_fetcher` |
| `knowledge_fetcher` | 항상 | `knowledge_interpreter` |
| `knowledge_interpreter` | 항상 | `readiness_gate` |
| `readiness_gate` | GENERATE | `sql_generator` |
| `readiness_gate` | EXPLORE (PENDING 스텝 있음, initial phase) | `knowledge_fetcher` (재진입) |
| `readiness_gate` | EXPLORE (PENDING 스텝 없음) | `recovery_agent` [P2-4 가드] |
| `readiness_gate` | REPLAN | `recovery_agent` |
| `readiness_gate` | ASK_USER (추론 불가 충돌만) | `clarification_handler` [P1-1 기준 변경] |
| `readiness_gate` | TERMINATE | `result_finalizer` |
| `recovery_agent` | `phase == GENERATING` (ready 또는 force-generate) | `sql_generator` |
| `recovery_agent` | `phase == DONE` (give_up + score 미달, 또는 decision=None) | `result_finalizer` |
| `recovery_agent` | CONFLICTED 발견 | `readiness_gate` (→ 추론 or ASK_USER) |
| `sql_generator` | 항상 | `sql_validator` |
| `sql_validator` | PASS | `result_finalizer` |
| `sql_validator` | SYNTAX (retry 가능) | `sql_generator` |
| `sql_validator` | SEMANTIC_LOCAL (fix 가능) | `sql_generator` |
| `sql_validator` | SEMANTIC_LOCAL (fix 초과) | `recovery_agent` (entry_source=sql_validator) |
| `sql_validator` | STRUCTURAL/EMPTY/DB_ERROR | `recovery_agent` (entry_source=sql_validator) |
| `sql_validator` | fast_path 실패 | `knowledge_fetcher` (exploration_phase=initial) |
| `result_finalizer` | `validated_sql` 존재 | `execute_sql` |
| `result_finalizer` | else | `error_end` |

### 7.2 라우팅 함수 구현

```python
def _route_after_readiness_gate(state: PipelineState) -> str:
    reason = state.reason
    verdict = reason.last_verdict  # Phase가 아닌 verdict 직접 참조

    if verdict == ReadinessVerdict.GENERATE:
        return "sql_generator"
    if verdict == ReadinessVerdict.EXPLORE:
        # EXPLORE + PENDING 스텝 가드: readiness_gate가 아닌 라우팅에서 전환 판단
        if reason.exploration_phase == "initial":
            pending_steps = [
                s for s in reason.execution_plan
                if s.status == StepStatus.PENDING
            ]
            if pending_steps:
                return "knowledge_fetcher"
            # PENDING 스텝이 없으면 recovery로 전환
            reason.exploration_phase = "recovery"
            reason.recovery_entry_source = "readiness_gate"
            return "recovery_agent"
        return "recovery_agent"
    if verdict == ReadinessVerdict.REPLAN:
        reason.recovery_entry_source = "readiness_gate"
        reason.exploration_phase = "recovery"
        return "recovery_agent"
    if verdict == ReadinessVerdict.ASK_USER:
        return "clarification_handler"
    return "result_finalizer"  # TERMINATE


def _route_after_planner(state: PipelineState) -> str:
    if state.reason.fast_path_triggered:
        return "sql_generator"
    return "knowledge_fetcher"


def _route_after_sql_validator(state: PipelineState) -> str:
    reason = state.reason

    # fast-path 실패 → 정상 탐색 전환
    if reason.fast_path_triggered and not reason.validated_sql:
        reason.fast_path_triggered = False
        reason.exploration_phase = "initial"
        return "knowledge_fetcher"

    if reason.validated_sql:
        return "result_finalizer"

    # SQL 구문 에러 + retry 가능
    if reason.failure_type == "SQL_SYNTAX" and reason.loop_guard.local_fix_count < MAX_LOCAL_FIX:
        return "sql_generator"

    # SEMANTIC_LOCAL + fix 가능
    if reason.failure_type == "SEMANTIC_LOCAL" and reason.loop_guard.local_fix_count < MAX_LOCAL_FIX:
        return "sql_generator"

    # 그 외 모든 실패 → recovery_agent
    reason.recovery_entry_source = "sql_validator"
    reason.exploration_phase = "recovery"
    return "recovery_agent"


def _route_after_recovery_agent(state: PipelineState) -> str:
    reason = state.reason
    if reason.phase == Phase.GENERATING:
        return "sql_generator"
    if reason.phase == Phase.DONE:
        return "result_finalizer"
    # CONFLICTED 발견 시 — 왕복 가드 적용
    reason.conflicted_bounce_count += 1
    if reason.conflicted_bounce_count > MAX_CONFLICTED_BOUNCES:
        # 왕복 한도 초과 → force-generate 또는 실패 처리
        score = calculate_readiness(reason)
        if score >= THRESHOLD_FORCE_GENERATE:
            reason.phase = Phase.GENERATING
            return "sql_generator"
        reason.phase = Phase.DONE
        reason.final_status = FinalStatus.FAILURE
        return "result_finalizer"
    return "readiness_gate"
```

### 7.3 전체 라우팅 흐름 요약

```
planner (exploration_phase/recovery_rounds 리셋)
  ├─ fast_path → sql_generator
  └─ else → knowledge_fetcher → knowledge_interpreter → readiness_gate
       ├─ GENERATE → sql_generator
       ├─ EXPLORE (PENDING 스텝 있음) → knowledge_fetcher (재진입)
       ├─ EXPLORE (PENDING 스텝 없음) → recovery_agent  [P2-4 가드]
       ├─ REPLAN → recovery_agent (entry_source=readiness_gate)
       ├─ ASK_USER (추론 불가 충돌만) → clarification_handler
       └─ TERMINATE → result_finalizer

recovery_agent (병렬 도구 실행, 진전 감지)
  ├─ ready → sql_generator (inference_notes 기록)
  ├─ give_up + score ≥ threshold → sql_generator (force-generate)
  ├─ give_up + score < threshold → result_finalizer (실패)
  ├─ decision=None → result_finalizer (실패)
  └─ CONFLICTED 발견 → readiness_gate → 추론 or ASK_USER

sql_generator → sql_validator
  ├─ PASS → result_finalizer
  ├─ SYNTAX (retry 가능) → sql_generator
  ├─ SEMANTIC_LOCAL (fix 가능) → sql_generator
  ├─ SEMANTIC_LOCAL (fix 초과) → recovery_agent (entry_source=sql_validator)
  ├─ STRUCTURAL/EMPTY/DB_ERROR → recovery_agent (entry_source=sql_validator)
  └─ fast_path 실패 → knowledge_fetcher (exploration_phase=initial)
```

---

## 8. LoopGuard 및 종료 조건

### 8.1 6종 종료 조건의 신규 노드 배치

| 종료 조건 | 현행 체크 위치 | 신규 체크 위치 | 비고 |
|-----------|-------------|-------------|------|
| `total_tool_calls ≥ MAX_TOOL_CALLS` (20) | `context_explorer` 내부 | `recovery_agent` 내부 (매 tool 실행 후) + `knowledge_fetcher` 내부 | ReAct 루프 내부에서 매번 확인 |
| `replan_count ≥ MAX_REPLANS` (3) | `confidence_evaluator` | `readiness_gate` + `recovery_agent` 진입 시 | recovery_agent 진입 전에 사전 차단 |
| `generate_attempts ≥ MAX_GENERATES` (4) | `sql_generator` | `sql_generator` | 변경 없음 |
| `final_status == FAILURE` | `should_terminate()` | `readiness_gate` | 변경 없음 |
| hypotheses 소진 | `should_terminate()` | `recovery_agent` 내부 | give_up 판정으로 전환 |
| `conflicted_bounce_count ≥ MAX_CONFLICTED_BOUNCES` (2) | (신규) | `_route_after_recovery_agent` | CONFLICTED 왕복 무한 루프 차단 |

> **설계 결정: CONFLICTED 왕복 가드 (6번째 종료 조건)**
>
> recovery_agent가 CONFLICTED 상태의 knowledge_item을 발견하여 readiness_gate로 돌려보내면, readiness_gate에서 REPLAN → recovery_agent로 재진입할 수 있다. 이때 recovery_agent 진입 시 `increment_replan()`이 호출되므로, 실제 1회의 recovery 시도에 대해 replan이 2회 카운트되는 문제가 있다.
>
> `conflicted_bounce_count`는 이 특정 순환 경로만을 추적한다. recovery_agent가 CONFLICTED로 readiness_gate에 돌려보낼 때 increment하며, `MAX_CONFLICTED_BOUNCES`(2) 초과 시 readiness_gate로 보내는 대신 give_up으로 전환한다. 이 카운터는 `increment_replan()`과 독립적으로 동작하므로, 정상 recovery 경로의 replan 카운트에 영향을 주지 않는다.

### 8.2 increment 위치

| 카운터 | 현행 increment 위치 | 신규 increment 위치 |
|--------|-------------------|-------------------|
| `total_tool_calls` | `context_explorer._run_step()` | `knowledge_fetcher._run_step()` + `recovery_agent._execute_tools()` |
| `replan_count` | `recovery_planner_node()` 진입 시 | `recovery_agent_node()` 진입 시 |
| `generate_attempts` | `sql_generator_node()` 진입 시 | 변경 없음 |
| `local_fix_count` | `sql_validator` 경유 시 | 변경 없음 |

### 8.3 Force-Generate 로직

**readiness_gate**: 현행 `confidence_evaluator`의 force-generate 로직을 그대로 유지.

```
if replan_count ≥ 2 AND readiness_score ≥ THRESHOLD_FORCE_GENERATE:
    verdict를 GENERATE로 override
```

**recovery_agent**: give_up 시 `_finalize_recovery`에서 동일 로직으로 force-generate 판정.

> **THRESHOLD_FORCE_GENERATE 통일**: 원안(0.55)과 리뷰 시나리오(0.40)에서 상수 값 차이가 있었음. 실제 `confidence_scorer.py`의 현행 값을 확인하고, config에서 관리하여 문서 불일치 위험을 근본적으로 제거한다. 임계값의 최적 값은 골든셋 실험으로 검증한다.
>
> ```python
> # config.py
> THRESHOLD_FORCE_GENERATE: float = 0.55  # 실제 값은 confidence_scorer.py 현행 확인 후 통일
> ```

---

## 9. 프로덕션 환경 대응

프로덕션 환경의 세 가지 핵심 제약이 설계 전반의 판단 기준이 된다:

1. **참조 저장소의 실질적 한계** — Qdrant의 상품설명서/업무매뉴얼은 SQL 추론에 직접적 힌트가 아님, MongoDB 비즈용어사전은 200개 미만으로 부실, 보고서 SQL/골든셋은 아직 없음
2. **70B~397B LLM의 ReAct 능력 한계** — 파인튜닝 없는 상태에서 복잡한 구조화 출력의 안정성이 보장되지 않음
3. **함축적 사용자 질의 + "선 추론 후 표시" 정책** — 대부분 명확화 질문 없이 추론으로 진행해야 함

### 9.1 "선 추론 후 표시" 정책

**원칙**: 대부분의 모호한 질의에서 **추론으로 진행하고 결과에 추론 근거를 표시**한다. ASK_USER는 "추론으로도 해결 불가능한" 경우에만 발동한다.

```
── 현재 설계의 가정 (변경 전) ──
"예금신규 top 3" → 모호함 → ASK_USER → 사용자에게 "예금신규액? 예금신규건수?" 확인

── 프로덕션 정책 (변경 후) ──
"예금신규 top 3" → 모호함 → "예금신규액"으로 추론 → SQL 생성
                         → 결과에 "예금신규액 기준으로 조회하였습니다" 표시
```

**영향 범위**:

1. **readiness_gate**: ASK_USER 발동 기준을 "추론 불가 충돌"로 제한
2. **recovery_agent**: give_up 기준 완화 — 정확한 메타를 찾지 못해도 "합리적 추론"으로 진행
3. **sql_generator**: 추론 근거(inference_notes)를 Present Layer까지 전달
4. **추론 비중 안내**: readiness_gate에서 추론 기반 PROBABLE이 많으면 결과에 추론 사항 안내 강화

```python
# readiness_gate에서 추론 비중 체크
inferred_count = sum(
    1 for ki in reason.knowledge_items
    if ki.status == ConfidenceStatus.PROBABLE and ki.is_inferred
)
if inferred_count > 0:
    reason.inference_notes.append(
        f"총 {inferred_count}건의 용어를 관행적 해석으로 추론하였습니다. "
        "결과가 예상과 다를 경우 구체적으로 요청해 주세요."
    )
```

### 9.1.1 inference_notes의 Present Layer 렌더링 가이드라인

`inference_notes`는 Reason Layer에서 수집되어 Present Layer의 `format_response`까지 전달된다. 사용자에게 시스템의 불확실성을 투명하게 전달하는 것은 금융 도메인에서 신뢰 확보의 핵심이다.

**렌더링 규칙**:

| 조건 | 렌더링 방식 |
|------|-----------|
| `inference_notes`가 비어있음 | 추론 관련 표시 없음 (정상 응답) |
| `inference_notes`가 1-2건 | 결과 응답 **상단**에 요약 섹션으로 표시 |
| `inference_notes`가 3건 이상 | 결과 응답 **상단**에 경고 수준 요약 + 개별 항목 목록 표시 |
| force-generate 경로 | 면책 고지 포함 (§9.1.2 참조) |

**format_response에서의 구현 가이드**:

```python
def _build_inference_disclaimer(inference_notes: list[str], is_force_generated: bool) -> str:
    """추론 사항 안내 메시지 생성. Present Layer의 format_response에서 호출."""
    if not inference_notes and not is_force_generated:
        return ""

    parts = []

    if is_force_generated:
        parts.append(
            "⚠️ 확인된 정보가 충분하지 않아 일부 추론을 포함하여 조회하였습니다. "
            "결과가 예상과 다를 경우 구체적으로 요청해 주세요."
        )

    if inference_notes:
        parts.append("ℹ️ 다음 항목은 관행적 해석을 적용하였습니다:")
        for note in inference_notes:
            parts.append(f"  • {note}")

    if len(inference_notes) >= 3:
        parts.append(
            "추론 항목이 다수 포함되어 있습니다. "
            "보다 정확한 결과를 원하시면 구체적인 조건을 말씀해 주세요."
        )

    return "\n".join(parts)
```

**액션 가이드 포함 원칙**: 각 추론 항목은 사용자가 재질의할 수 있는 힌트를 포함한다.

```
# 좋은 예시
"'여신'을 '여신잔액' 기준으로 해석하였습니다 (관행적 해석). 
 다른 기준(여신건수, 여신실행액 등)을 원하시면 말씀해 주세요."

# 나쁜 예시 (액션 가이드 없음)
"'여신'을 '여신잔액'으로 해석하였습니다."
```

### 9.2 함축적 금융 용어의 "합리적 추론" 경로

현재 knowledge_items의 상태 전이 모델에는 **"증거 없이 관행적 추론으로 결정"하는 경로**가 없다. 도구 결과(evidence)가 없으면 UNRESOLVED에서 벗어날 수 없고, "여신 = 여신잔액"이라는 관행적 매핑은 메타 저장소에 없으므로 도구를 아무리 호출해도 발견할 수 없다.

**해결**: PROBABLE의 의미를 확장하여 추론 기반 설정을 허용. `KnowledgeUpdate.is_inferred` 플래그로 구분.

```python
class KnowledgeUpdate(BaseModel):
    # ...
    is_inferred: bool = False  # True이면 도구 증거 없는 추론
```

**recovery_agent 프롬프트에 추론 지침 추가** (§10 참조):

```
## 추론 지침

도구 검색으로 정확한 답을 찾지 못했지만, 금융 도메인 관행상 합리적 추론이 가능한 경우:
- new_status를 "PROBABLE"로 설정하고 is_inferred=true로 표시
- evidence에 추론 근거를 명시 (예: "관행적 해석: '여신'은 통상 '여신잔액'을 의미")
- 이 추론은 결과 응답에서 사용자에게 표시됩니다

추론이 합리적인 경우:
- 금융 용어의 일반적 해석 (예: "여신" → 여신잔액, "수신" → 수신잔액)
- 기간 미지정 시 최근 기간 (예: "실적" → 당월 실적)
- 집계 기준 미지정 시 금액 기준 (예: "top 3" → 금액 기준 상위 3)

추론이 부적절한 경우 (ASK_USER 필요):
- 서로 다른 테이블을 사용해야 하는 완전히 다른 의미가 존재하는 경우
- 금융 지표 산출식이 불확실한 경우 (연체율, BIS비율 등)
```

**근거**: financial-domain.md에서 "금융 계수산출식은 정확한 산출식이 필수"라고 명시되어 있으나, 이는 **산출식**에 한정된 규칙. 일반적인 용어 해석은 "선 추론 후 표시" 정책이 적용됨.

### 9.3 참조 저장소 한계를 반영한 도구 전략

recovery_agent의 6+1개 도구를 동등하게 취급하지 않는다. 프로덕션 저장소별 SQL 추론 기여도에 큰 차이가 있다.

| 도구 | 참조 저장소 | 프로덕션 기대 효과 |
|------|-----------|------------------|
| `search_table_meta` | MongoDB (테이블/컬럼) | **높음** |
| `search_code_meta` | MongoDB (코드 메타) | **높음** |
| `get_sample_rows` | PostgreSQL (직접 조회) | **높음** |
| `get_date_distribution` | PostgreSQL (직접 조회) | **중간** |
| `search_use_cases` | Qdrant (과거 SQL) | **중간** — 유일한 참조 SQL 소스 |
| `search_manual` | Qdrant (업무매뉴얼) | **낮음** — SQL 추론에 직접적 힌트 아님 |
| `search_glossary` | MongoDB (200개 미만) | **낮음** — 부실, 결과 없을 확률 높음 |

**프롬프트에 도구 우선순위 가이드 포함** + **빈 결과 도구 피드백** (§10 참조).

### 9.4 정확도 관점 종합 평가

```
사용자 질의 → [함축적 용어 해석] → [메타 검색] → [SQL 생성] → [검증] → 결과

      ↑ 병목 1              ↑ 병목 2         ↑ 병목 3
  관행적 추론 경로 없음   부실한 메타로      70B의 ReAct
  → ASK_USER 과다        도구 결과 빈약    라운드 증가
                        → recovery 루프 낭비  → 판단 오류 증가
```

| 병목 | 관련 설계 결정 | 기대 효과 |
|------|-------------|----------|
| 병목 1: 함축적 용어 | "선 추론 후 표시" 정책 (§9.1), 합리적 추론 경로 (§9.2) | ASK_USER 빈도 50% 이상 감소, 대화 턴 절약 |
| 병목 2: 부실 메타 | 도구 우선순위 (§9.3), search_use_cases 복원 | 유효하지 않은 도구 호출 감소, 참조 SQL 활용 |
| 병목 3: 70B ReAct | 병렬 실행 (§6.4), 진전 감지 (§6.4), 파싱 안전 (§6.4) | ReAct 라운드 감소, 비정상 종료 방지 |

### 9.5 Recovery 과정의 사용자 중간 피드백

recovery_agent의 내부 ReAct 루프(최대 `RECOVERY_MAX_INTERNAL_ROUNDS`회)가 실행되는 동안 70B 모델 기준 라운드당 3-15초이므로, 5라운드 시 최대 75초간 무응답 상태가 발생한다. 금융 도메인의 일반 직원 사용자에게 이 시간은 "시스템 오류"로 인식될 수 있다.

**구현 방식**: WebSocket 기반 UI에서 recovery_agent의 각 라운드 시작/종료 시 중간 상태 메시지를 전송한다. 이 메커니즘은 **pipeline runner 레벨**에서 state 변화를 구독하여 UI에 전달하며, recovery_agent 노드 자체는 변경하지 않는다.

```python
# pipeline runner에서 recovery 진행 상태 콜백 (WebSocket 전송)
RECOVERY_PROGRESS_MESSAGES = {
    0: "추가 정보를 확인하고 있습니다...",
    1: "관련 테이블 데이터를 분석하고 있습니다...",
    2: "수집된 정보를 종합하고 있습니다...",
}

async def _on_recovery_round(state: PipelineState, ws: WebSocket) -> None:
    """recovery_agent의 recovery_rounds 변경을 감지하여 중간 피드백 전송."""
    round_num = state.reason.recovery_rounds
    max_rounds = RECOVERY_MAX_INTERNAL_ROUNDS
    message = RECOVERY_PROGRESS_MESSAGES.get(round_num, "추가 확인 중입니다...")

    await ws.send_json({
        "type": "progress",
        "message": message,
        "progress": f"{round_num + 1}/{max_rounds}",
    })
```

**구현 위치**: `runner.py`의 LangGraph `astream_events` 또는 state callback 활용. recovery_agent 노드 내부에서 WebSocket 의존성을 갖지 않도록 한다.

**최소 구현 (Step 3 이후 추가)**:

- recovery_agent 진입 시: `{"type": "progress", "message": "추가 정보를 확인하고 있습니다..."}`
- recovery_agent 종료 시: `{"type": "progress", "message": "확인 완료"}`
- 라운드별 세분화는 UX 테스트 후 점진적으로 추가

---

## 10. 프롬프트 설계

### 10.1 recovery_agent 시스템 프롬프트

```
당신은 데이터 분석을 위한 SQL 생성 에이전트의 recovery 모듈입니다.
이전 시도가 실패했거나 지식이 부족하여 SQL을 생성할 수 없었습니다.
현재 상태를 분석하고, 부족한 지식을 채우기 위해 도구를 사용하세요.

## 진입 경로
{entry_source_description}
  - readiness_gate: 초기 탐색이 불충분하여 추가 탐색이 필요합니다. 넓은 범위에서 공백을 채우세요.
  - sql_validator: SQL 검증이 실패했습니다. 실패 원인({failure_type}: {failure_reason})에 집중하세요.

## 현재 확인된 지식
{confirmed_knowledge}
  예) [K1] 여신실행일자 — CONFIRMED (exec_dt, TB_LOAN_EXEC)
      [K2] 기준일자 — CONFIRMED (base_dt)

## 후보 확인 (단일 출처)
{candidate_knowledge}
  예) [K3] 만기일자 — CANDIDATE (mtr_dt, 추가 확인 권장)

## 아직 확인되지 않은 항목
{unresolved_items}
  예) [K5] 지점유형코드 — UNRESOLVED

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
- search_use_cases(query): 과거 유사 SQL 이력 검색

## 도구 우선순위 가이드
1. search_table_meta: 테이블/컬럼 구조 확인 (SQL 생성에 직접적 힌트)
2. get_sample_rows: 실제 데이터 패턴 확인
3. search_code_meta: 코드 컬럼의 값-설명 매핑 확인
4. get_date_distribution: 날짜 컬럼의 데이터 범위 확인
5. search_use_cases: 유사한 과거 SQL 검색 (이전에 검색한 키워드: {searched_use_case_queries})
6. search_glossary: 금융 용어 정의 확인 (용어사전이 부실하여 결과가 없을 수 있음)
7. search_manual: 업무 프로세스 확인 (SQL 추론에 간접적 참고만 됨)

주의: search_glossary와 search_manual의 결과가 비어있는 것은 정상입니다.
결과가 없다면 다른 도구로 전환하세요. 동일 도구를 다른 검색어로 재시도하지 마세요.

{empty_tools_warning}
  예) "이전 라운드에서 결과가 없었던 도구: search_glossary, search_manual"

## 이전 도구 실행 결과
{previous_tool_results}  ← 첫 turn에서는 비어있음

## 추론 지침

도구 검색으로 정확한 답을 찾지 못했지만, 금융 도메인 관행상 합리적 추론이 가능한 경우:
- new_status를 "PROBABLE"로 설정하고 is_inferred=true로 표시
- evidence에 추론 근거를 명시 (예: "관행적 해석: '여신'은 통상 '여신잔액'을 의미")
- 이 추론은 결과 응답에서 사용자에게 표시됩니다

추론이 합리적인 경우:
- 금융 용어의 일반적 해석 (예: "여신" → 여신잔액, "수신" → 수신잔액)
- 기간 미지정 시 최근 기간 (예: "실적" → 당월 실적)
- 집계 기준 미지정 시 금액 기준 (예: "top 3" → 금액 기준 상위 3)

추론이 부적절한 경우 (action: "give_up" 또는 CONFLICTED 표시):
- 서로 다른 테이블을 사용해야 하는 완전히 다른 의미가 존재하는 경우
- 금융 지표 산출식이 불확실한 경우 (연체율, BIS비율 등)

## 지시
1. unresolved_items 중 가장 중요한 공백을 식별하세요.
2. 독립적인 공백은 한번에 여러 도구로 조회하세요 (최대 4개).
3. dead_ends에 기록된 실패 패턴을 반복하지 마세요.
4. 도구 결과를 기반으로 knowledge_updates를 제안하세요.
5. 기존 항목 갱신 시 반드시 item_id (예: "K1")를 사용하세요. 신규 항목은 item_id를 null로.
6. 충분한 지식이 모이면 action: "ready"로 응답하세요.
7. 더 이상 시도할 수 있는 경로가 없으면 action: "give_up"으로 응답하세요.
7-1. 한 라운드의 tool_calls는 서로 독립적이어야 합니다.
     다른 도구의 결과가 필요한 호출은 다음 라운드에 요청하세요.

아래 JSON 스키마에 맞춰 응답하세요:
{recovery_decision_schema}
```

### 10.2 프롬프트 설계 원칙

1. **dead_ends를 항상 포함**: 동일 경로 재시도를 방지하는 핵심 컨텍스트
2. **unresolved_items를 명시적으로 열거**: LLM이 "무엇이 부족한지"를 정확히 인식
3. **knowledge_items에 ID와 상태를 모두 표시**: `[K1] 여신실행일자 — CONFIRMED` 형태
4. **CANDIDATE 상태 포함**: 단일 출처에서 확인된 항목도 프롬프트에 표시하여 중복 탐색 방지
5. **도구 설명에 금융 도메인 예시 포함**: "search_code_meta(column_name='loan_type_cd') → 여신 유형 코드값 조회" 형태
6. **이전 tool_results는 최근 1라운드만**: 컨텍스트 윈도우 절약
7. **스키마를 매번 명시**: 오픈소스 모델의 JSON 출력 안정성을 위해
8. **진입 경로를 명시**: readiness_gate vs sql_validator에서 진입했는지에 따라 분석 방향 유도

### 10.3 Truncation 전략

| 정보 | 기본 한도 | truncation 전략 |
|------|----------|----------------|
| confirmed_knowledge | 전체 포함 | truncation 없음 (핵심 정보) |
| candidate_knowledge | 전체 포함 | truncation 없음 (중복 탐색 방지) |
| unresolved_items | 전체 포함 | truncation 없음 (탐색 대상) |
| dead_ends | 전체 포함, lessons_learned 100자 | 실패 패턴 학습에 필수 |
| candidate_tables | SELECTED/PENDING만 | REJECTED 제외 |
| candidate_tables.columns | 주요 컬럼 20개 | 전체 컬럼 목록은 생략 |
| candidate_tables.sample_rows | 3행/테이블 | 관찰 목적에 충분 |
| discovered_facts | hypothesis당 최근 5개 | 오래된 사실은 knowledge_items에 반영됨 |
| tool_results (이전 라운드) | 최근 1라운드만 | 이전 결과는 knowledge_updates로 반영됨 |
| structural_hints | 전체 포함 | use_case에서 추출된 구조 힌트는 압축적 |

**예상 토큰 소모**: truncation 적용 시 recovery LLM call당 약 2,000-4,000 토큰 (입력). 70B 모델의 8K 윈도우 내에서 안전.

**근거**:
- **Complexity Trap** (2025, arXiv:2508.21433): "simple observation masking이 LLM 요약과 동등한 solve rate를 달성하면서 비용은 절반".
- **IBM Context Window Overflow** (Labate et al., 2025, arXiv:2511.22729): 메모리 포인터 방식으로 토큰 사용량 7배 감소.
- **Lost in the Middle** (Liu et al., 2023, Stanford): LLM은 컨텍스트의 시작과 끝에 집중하며 중간 정보 활용도가 낮음.

#### 10.3.1 적응적 Truncation 가드

위 truncation 전략을 적용해도 candidate_tables가 다수(예: 20개)이고 각각 sample_rows 3행 + columns 20개를 포함하면, 예상 범위(2,000-4,000 토큰)를 초과할 수 있다. Solar Pro 2의 8K 윈도우에서 이 초과는 프롬프트 잘림 → LLM 판단 오류로 직결되므로, `_build_recovery_prompt()`에서 **적응적 truncation**을 수행한다.

```python
# config.py
RECOVERY_PROMPT_MAX_CHARS: int = 12000  # ~4,000 토큰 (1토큰≈3문자 근사)
RECOVERY_PROMPT_HARD_LIMIT: int = 20000  # ~6,700 토큰 (8K 윈도우에서 응답 여유 확보)

def _build_recovery_prompt(reason: ReasoningState, tool_results: list[dict]) -> str:
    """recovery_agent 프롬프트 조립. 적응적 truncation 적용."""

    # 1단계: 기본 truncation으로 프롬프트 조립
    prompt = _assemble_prompt(reason, tool_results, truncation_level="normal")

    # 2단계: 소프트 한도 초과 시 축소
    if len(prompt) > RECOVERY_PROMPT_MAX_CHARS:
        prompt = _assemble_prompt(reason, tool_results, truncation_level="aggressive")
        # aggressive: columns 10개, sample_rows 1행, discovered_facts 3개

    # 3단계: 하드 한도 초과 시 최소 축소
    if len(prompt) > RECOVERY_PROMPT_HARD_LIMIT:
        prompt = _assemble_prompt(reason, tool_results, truncation_level="minimal")
        # minimal: columns 5개, sample_rows 제거, discovered_facts 1개, dead_ends 최근 2개만

    return prompt
```

| truncation_level | columns / 테이블 | sample_rows / 테이블 | discovered_facts | dead_ends |
| --- | --- | --- | --- | --- |
| `normal` | 20개 | 3행 | 5개 / hypothesis | 전체 |
| `aggressive` | 10개 | 1행 | 3개 | 전체 |
| `minimal` | 5개 | 제거 | 1개 | 최근 2개 |

> **설계 결정: 문자 수 기반 근사**
>
> 정확한 토큰 수 계산(tiktoken 등)은 모델별 토크나이저 의존성을 도입한다. 폐쇄망 환경에서 Solar Pro 2 / Qwen3.5 각각의 토크나이저를 관리하는 것은 복잡도 대비 이점이 적다. 1토큰≈3문자 근사는 한국어+영어 혼합 텍스트에서 ±20% 오차 범위이며, 하드 한도에 여유 마진을 두어 오차를 흡수한다.

---

## 11. 마이그레이션 전략

### 11.1 단계별 실행 계획

#### Step 1: context_explorer 기계적 분리 (behavioral change 없음)

**목표**: `context_explorer.py` 1,177줄 → `knowledge_fetcher.py` + `knowledge_interpreter.py` + 공통 유틸리티

| 작업 | 내용 | 리스크 |
|------|------|--------|
| 1-1 | `knowledge_fetcher.py` 생성: `_execute_steps()`, `_run_step()`, `_should_skip_step()`, `_observe_all_date_distributions()`, `_sample_unsampled_tables()` 이동 | 낮음 — 함수 경계가 명확 |
| 1-2 | `knowledge_interpreter.py` 생성: `_interpret_batch()`, `_apply_batch_insights()`, `_remove_unsuitable_tables()`, 신뢰도 승격 로직 이동 | 낮음 — 함수 경계가 명확 |
| 1-3 | `pipeline.py` 엣지 수정: `context_explorer` → `knowledge_fetcher → knowledge_interpreter` | 낮음 — 노드명 변경만 |
| 1-4 | 기존 테스트 실행으로 동작 동일성 검증 | — |

**완료 기준**: 기존 e2e 테스트(`test_agentic_core.py`, `test_agentic_e2e.py`, `test_agentic_flow_trace.py`)가 수정 없이 통과.

#### Step 2: readiness_gate 리네이밍 + State 필드 추가 (behavioral change 최소)

| 작업 | 내용 |
|------|------|
| 2-1 | `confidence_evaluator.py` → `readiness_gate.py` 복사/리네이밍 |
| 2-2 | `confidence_evaluator_node()` → `readiness_gate_node()` 리네이밍 |
| 2-3 | `pipeline.py` 노드 등록명 변경 |
| 2-4 | `nodes/__init__.py` export 업데이트 |
| 2-5 | `state.py`에 신규 필드 추가: `exploration_phase`, `recovery_rounds`, `last_verdict`, `recovery_entry_source`, `inference_notes`, `conflicted_bounce_count`, `is_force_generated` |
| 2-6 | `KnowledgeItem`에 `knowledge_id`, `is_inferred` 필드 추가 |
| 2-7 | `PROMOTION_ORDER`에 `CANDIDATE` 추가 |
| 2-8 | `last_verdict` 저장 로직 추가 (readiness_gate는 판정만 수행, EXPLORE+PENDING 가드는 라우팅 함수에서 처리) |
| 2-9 | `planner_node`에 리셋 로직 추가 |

#### Step 3: recovery_agent 구현 (핵심 behavioral change)

| 작업 | 내용 | 리스크 |
|------|------|--------|
| 3-1 | `RecoveryDecision`, `ToolCall`, `KnowledgeUpdate`, `TableUpdate` 스키마 정의 | 낮음 |
| 3-2 | `recovery_planner.py`에서 hypothesis 관리 코드 추출 → `_handle_hypothesis_transition()` | 중간 — 기존 로직 보존 검증 필요 |
| 3-3 | `_build_recovery_prompt()` 구현 (기존 `_build_replan_context()` 기반 + truncation + 진입 경로 + 도구 우선순위 + 추론 지침) | 중간 — 프롬프트 품질 검증 필요 |
| 3-4 | `_recovery_step()` 구현 (LLM 호출 + RecoveryDecision 파싱) | 중간 |
| 3-5 | `_parse_recovery_response()` 구현 (3단계 fallback) | 중간 |
| 3-6 | `_execute_single_tool()` 어댑터 구현 (kwargs → 현행 tool_input 변환) | 중간 — 도구별 형식 검증 필수 |
| 3-7 | `_execute_tools()` 병렬 실행 구현 (asyncio.gather + 개별 타임아웃 + status="timeout" 분리) | 중간 |
| 3-8 | `_apply_knowledge_updates()`, `_apply_table_updates()` 구현 (ID 기반 참조) | 낮음 |
| 3-9 | `_snapshot_knowledge_state()` + 진전 감지 로직 구현 | 낮음 |
| 3-10 | `_finalize_recovery()` 구현 (give_up 즉시 종료 + force-generate 내부 판정) | 중간 — P0-1 핵심 |
| 3-11 | `_record_inference_notes()` 구현 | 낮음 |
| 3-12 | `recovery_agent_node()` ReAct 루프 오케스트레이터 구현 | 중간 |
| 3-13 | `pipeline.py` 라우팅 수정: `recovery_planner` → `recovery_agent`, sql_validator 분기 변경 | 중간 |
| 3-14 | ASK_USER 발동 기준 변경 (`_should_ask_user` 구현) | 중간 |
| 3-15 | `_recovery_step()`에서 LLM 호출 실패(타임아웃/네트워크) → `None` 반환 처리 | 중간 — §6.4.1 |
| 3-16 | `_attach_force_generate_disclaimer()` 구현 + `is_force_generated` 플래그 | 낮음 |
| 3-17 | `_route_after_recovery_agent()`에서 CONFLICTED 왕복 가드 (`conflicted_bounce_count`) | 중간 |
| 3-18 | `_build_recovery_prompt()`에서 적응적 truncation (normal/aggressive/minimal) | 중간 — §10.3.1 |

**완료 기준**:
- `_handle_hypothesis_transition()` 단위 테스트: ACTIVE→FAILED, DeadEnd 생성, PENDING 소비
- `_recovery_step()` 단위 테스트: mock LLM으로 RecoveryDecision 파싱 검증
- `_apply_knowledge_updates()` 단위 테스트: ID 기반 매칭, 승격만 허용 검증
- `_execute_single_tool()` 단위 테스트: 각 도구별 kwargs→tool_input 변환 검증
- `_parse_recovery_response()` 단위 테스트: 정상 JSON, 부분 JSON, fallback 검증
- `_finalize_recovery()` 단위 테스트: give_up+force-generate, give_up+failure, decision=None 검증
- `recovery_agent_node()` 통합 테스트: LoopGuard 종료 조건 + 진전 감지 검증
- e2e 테스트: recovery 경로를 타는 골든셋 질의로 전체 흐름 검증

#### Step 4: 정리 및 검증

| 작업 | 내용 |
|------|------|
| 4-1 | 기존 `context_explorer.py` 제거 (Step 1 검증 후) |
| 4-2 | 기존 `recovery_planner.py` 제거 (Step 3 검증 후) |
| 4-3 | `nodes/__init__.py` 최종 정리 |
| 4-4 | dead prompt 정리 (W-04: CLARIFIER_SYSTEM 등) |
| 4-5 | dead code 제거 (W-04 관련 미사용 프롬프트 상수 등) |
| 4-6 | 전체 테스트 스위트 실행 |

### 11.2 롤백 전략

Step 1-2는 behavioral change가 없으므로 롤백 필요성이 낮다. Step 3에서 문제 발생 시:

- `recovery_planner.py` + `context_explorer.py`를 복원하고 `pipeline.py` 라우팅을 원복
- `recovery_agent.py`는 별도 파일이므로 삭제만 하면 됨
- State 변경이 필드 추가만이므로 (제거 없음) backward compatible

---

## 12. 구현 우선순위 및 체크리스트

### P0 — 구현 전 반드시 해소 (8건)

| # | 항목 | 구현 위치 | 핵심 근거 |
|---|------|----------|----------|
| 1 | give_up 시 즉시 종료 + force-generate 내부 판정 | `recovery_agent._finalize_recovery()` | 무한 루프 차단, "선 추론" 정책 연계 |
| 2 | planner_node에서 ephemeral state 전체 리셋 (exploration_phase, recovery_rounds, last_verdict, recovery_entry_source, inference_notes, conflicted_bounce_count, is_force_generated) | `planner.planner_node()` | 멀티턴 라우팅 오류 방지 |
| 3 | _execute_single_tool 어댑터 kwargs→쉼표 구분 문자열 변환 | `recovery_agent._execute_single_tool()` | 현행 tools.py와 호환 필수 |
| 4 | decision=None 시 즉시 FAILURE 종료 (LLM 호출 실패 포함) | `recovery_agent._finalize_recovery()`, `_recovery_step()` | 70B 파싱 실패율 높음 + 네트워크 오류 대응 |
| 5 | 도구 실행 병렬화 (asyncio.gather) + 개별 타임아웃 + 프롬프트 독립성 규칙 | `recovery_agent._execute_tools()` + 프롬프트 7-1항 | ReAct 라운드 감소, 도구 지연 차단 |
| 6 | _recovery_step() LLM 호출 예외 처리 → None 반환 | `recovery_agent._recovery_step()` | LLM은 언제든 실패할 수 있다 (§6.4.1) |
| 7 | CONFLICTED 왕복 가드 (conflicted_bounce_count) | `state.py`, `_route_after_recovery_agent()` | recovery↔readiness_gate 무한 순환 차단 (§8.1) |
| 8 | force-generate 면책 고지 + is_force_generated 플래그 | `recovery_agent._attach_force_generate_disclaimer()` | 금융 도메인 사용자 신뢰 확보 (§9.1.1) |

### P1 — 구현 초기 반영 권장 (14건)

| # | 항목 | 구현 위치 | 핵심 근거 |
|---|------|----------|----------|
| 9 | "선 추론 후 표시" 정책 — ASK_USER 기준 변경 + inference_notes 채널 | `readiness_gate._should_ask_user()`, `state.py` | 정확도 + UX 핵심 |
| 10 | 도구 우선순위 가이드 + 빈 결과 도구 피드백 | recovery_agent 프롬프트 | 참조 저장소 한계 대응 |
| 11 | 함축적 용어의 관행적 추론 경로 (is_inferred 플래그) | `KnowledgeUpdate`, recovery_agent 프롬프트 추론 지침 | 대부분의 프로덕션 질의가 함축적 |
| 12 | last_verdict 필드 추가, 라우팅에서 직접 참조 | `state.py`, `readiness_gate`, 라우팅 함수 | 정보 소실 방지 |
| 13 | knowledge_item ID 기반 참조 전환 + 순차 적용 보장 | `KnowledgeItem`, `KnowledgeUpdate`, 프롬프트 | 금융 도메인 접미어 공유 빈번, ID 채번 안전성 (§5.2) |
| 14 | PROMOTION_ORDER에 CANDIDATE 추가 | `recovery_agent._apply_knowledge_updates()` | 현행 코드에서 CANDIDATE 실사용 중 |
| 15 | discovered_facts 갱신 경로 + CANDIDATE 프롬프트 포함 | `recovery_agent_node()`, `_build_recovery_prompt()` | sql_generator 연계 |
| 16 | KnowledgeUpdate.new_status에 CANDIDATE 포함 | 스키마 정의 | 시스템 일관성 |
| 17 | 진전 감지 (2회 연속 무변화 → 조기 종료) | `recovery_agent_node()` | 부실 메타 환경 비용 효율 |
| 18 | fallback 파싱 안전성 강화 (call_tools → give_up 전환) | `recovery_agent._parse_recovery_response()` | 70B JSON 불안정 |
| 19 | recovery_entry_source 필드 + 프롬프트 진입 경로 표시 | `state.py`, 라우팅 함수, 프롬프트 | 70B에 맥락 제공 |
| 20 | recovery_rounds 의미 명확화 (진입 시 리셋) | `recovery_agent_node()` | 가독성 |
| 21 | readiness_gate 노드 책임 분리 — verdict 오버라이드 제거, 라우팅 함수에 위임 | `readiness_gate_node()`, `_route_after_readiness_gate()` | 하네스 디자인 원칙: 노드는 판정만, 전환은 라우팅이 (§6.3) |
| 22 | inference_notes Present Layer 렌더링 구현 (§9.1.1 가이드라인 기반) | `format_response`, `state.py` | 추론 기반 응답의 UX 투명성 |

### P2 — 품질/안정성 향상 (8건)

| # | 항목 | 구현 위치 | 핵심 근거 |
|---|------|----------|----------|
| 23 | readiness_gate 추론 비중 체크 + 안내 강화 | `readiness_gate_node()` | 추론 기반 응답의 투명성 |
| 24 | search_use_cases 조건부 복원 (recovery 도구 목록) | `ToolCall.tool` Literal, 프롬프트 | 유일한 참조 SQL 소스 |
| 25 | THRESHOLD_FORCE_GENERATE 통일 + config 관리 | `config.py` | 문서/코드 일관성 |
| 26 | EXPLORE verdict PENDING 스텝 가드 (라우팅 함수에서 처리) | `_route_after_readiness_gate()` | 방어적 프로그래밍 |
| 27 | max_internal_rounds config 기반 관리 | `config.py` | 폐쇄망 배포 원칙 부합 (설정 변경만으로 전환) |
| 28 | 적응적 truncation 가드 (normal/aggressive/minimal 3단계) | `recovery_agent._build_recovery_prompt()` | 8K 윈도우 초과 방지, 프롬프트 잘림 → 판단 오류 차단 (§10.3.1) |
| 29 | 도구 실행 타임아웃 config 기반 관리 (TOOL_EXECUTION_TIMEOUT, TOOL_BATCH_TIMEOUT) | `config.py` | 폐쇄망 DB 응답 시간 차이 대응 |
| 30 | recovery 중간 피드백 WebSocket 전송 | `runner.py` (state callback) | 사용자 UX — 75초 무응답 방지 (§9.5) |

---

## 13. 부록

### 부록 A — 폐쇄망 모델 호환성 고려사항

#### Solar Pro 2 70B

| 항목 | 대응 |
|------|------|
| 컨텍스트 윈도우 (8-16K) | truncation 전략 필수, recovery 프롬프트 4K 이내 유지 |
| JSON 출력 안정성 | `response_format: {"type": "json_object"}` 사용, 실패 시 regex fallback 파싱 |
| tool-calling 미지원 | Structured Output 방식 채택 (본 설계의 기본 전제) |
| thinking 모드 없음 | `NODE_THINKING_MODES` 에서 recovery_agent = "off" |

#### Qwen3.5 397B (예정)

| 항목 | 대응 |
|------|------|
| 컨텍스트 윈도우 (32K+) | truncation을 완화 가능, 그러나 기본 전략은 유지 |
| JSON 출력 안정성 | 양호, 그러나 thinking 모드에서 JSON 깨짐 가능 |
| thinking 모드 | `NODE_THINKING_MODES`에서 recovery_agent = "auto" 설정 가능 |
| tool-calling | 지원하지만 안정성 검증 후 전환 결정 |

#### 모델 무관 설계 원칙

1. **Structured Output을 기본으로**: 모든 폐쇄망 모델에서 작동하는 최소 공통분모
2. **Fallback 파싱**: JSON 파싱 실패 시 regex로 `action` 추출, `call_tools`는 `give_up`으로 전환
3. **프롬프트 명확성**: 암묵적 추론 최소화, 모든 컨텍스트를 명시적으로 제공
4. **스키마 반복 제시**: 매 turn마다 `RecoveryDecision` 스키마를 포함 (모델의 JSON 충실도 향상)
5. **max_internal_rounds config 기반**: 폐쇄망 배포 시 config 변경만으로 조절 (코드 분기 없음)

```python
# config.py
RECOVERY_MAX_INTERNAL_ROUNDS: int = 5  # 환경별 설정 — 폐쇄망에서는 2-3으로 조절
```

### 부록 B — 참고 문헌

| ID | 제목 | 저자 | 연도 | 출처 |
|----|------|------|------|------|
| T1 | Pre-Act: Multi-Step Planning and Reasoning Improves Acting in LLM Agents | Rawat et al. (Uniphore) | 2025 | arXiv:2505.09970 |
| T2 | AgentBench: Evaluating LLMs as Agents | Xiao Liu et al. (Tsinghua) | 2024 | ICLR 2024, arXiv:2308.03688 |
| T3 | Why Do Multi-Agent LLM Systems Fail? (MAST) | Cemri, Pan, Yang et al. (UC Berkeley) | 2025 | NeurIPS 2025, arXiv:2503.13657 |
| T4 | JSONSchemaBench: A Rigorous Benchmark of Structured Outputs | Guidance-AI (Microsoft Research) | 2025 | arXiv:2501.10868 |
| T5 | StructEval: Benchmarking LLMs' Structured Output Capabilities | Tiger AI Lab | 2025 | arXiv:2505.20139 |
| T6 | Solving LLM Repetition Problem in Production | — | 2024 | arXiv:2512.04419 |
| T7 | Solving Context Window Overflow in AI Agents | Labate et al. (IBM Research Brazil) | 2025 | arXiv:2511.22729 |
| T8 | The Complexity Trap: Simple Observation Masking | — | 2025 | arXiv:2508.21433 |
| T9 | Learning Latency-Aware Orchestration for Parallel Multi-Agent Systems | — | 2025 | arXiv:2601.10560 |
| T10 | CHESS: Contextual Harnessing for Efficient SQL Synthesis | Talaei, Pourreza et al. (Stanford) | 2024 | arXiv:2405.16755 |
| T11 | MAC-SQL: Multi-Agent Collaborative Framework for Text-to-SQL | Wang et al. | 2025 | COLING 2025, arXiv:2312.11242 |
| T12 | DIN-SQL: Decomposed In-Context Learning of Text-to-SQL | Pourreza & Rafiei | 2023 | NeurIPS 2023, arXiv:2304.11015 |
| T13 | Lost in the Middle: How Language Models Use Long Contexts | Liu et al. (Stanford) | 2023 | — |
| T14 | Reflexion: Language Agents with Verbal Reinforcement Learning | Shinn et al. | 2023 | arXiv:2303.11366 |
| T15 | Chain-of-Table: Evolving Tables in the Reasoning Chain | Wang et al. | 2024 | arXiv:2401.04398 |
| B1 | Berkeley Function Calling Leaderboard (BFCL) | Patil, Mao et al. (UC Berkeley) | 지속 | gorilla.cs.berkeley.edu |
| R1 | LangGraph 공식 문서 | LangChain | 2024 | — |

### 부록 C — 설계 검토 이력 요약

본 문서는 다음 검토 과정을 거쳐 확정되었다:

| 단계 | 일자 | 내용 |
|------|------|------|
| 원안 작성 | 2026-03-31 | 01-strategy.md, 02-detailed-design.md |
| 1차 리뷰 | 2026-04-01 | State 관리 연속성 → LLM 동작 신뢰성 → 라우팅 일관성 → 답변 정확도 → 폐쇄망 호환성 |
| 크로스리뷰 | 2026-04-01 | 원안과 1차 리뷰를 비판적 재검토, 연구 근거 기반 교차 비교, 프로덕션 환경 관점 재검토 |
| 최종 통합 | 2026-04-01 | 1차 리뷰와 크로스리뷰 전체 내용 통합, 상충 해소, 누락 보완 (P0 5건, P1 12건, P2 5건) |

**주요 설계 변경 (리뷰 결과 원안에서 변경된 사항)**:

1. **give_up 처리**: 원안의 `Phase.DONE + FAILURE` 즉시 종료 방식을 채택하되, force-generate 판정을 recovery_agent 내부에서 수행하도록 보강. (readiness_gate 재진입 차단)
2. **도구 실행 병렬화**: 원안에서는 순차 실행만 명시했으나, P0으로 격상하여 asyncio.gather 기반 병렬 실행 채택. 프롬프트 수준 독립성 규칙 추가.
3. **"선 추론 후 표시" 정책**: 원안과 1차 리뷰가 전제한 "모호하면 ASK_USER" 패턴을 프로덕션 정책에 맞게 변경. 합리적 추론 경로 신설.
4. **knowledge_item ID 기반 참조**: 원안의 문자열 key 매칭을 ID 기반으로 전환.
5. **진전 감지 메커니즘**: 원안에 없던 2회 연속 무변화 조기 종료 추가.
6. **fallback 파싱 강화**: call_tools를 give_up으로 안전 전환하는 방어 로직 추가.
7. **max_internal_rounds config 기반**: 1차 리뷰의 모델별 코드 분기(degraded_mode) 대신 원안의 단일 config 방식 채택.
8. **search_use_cases 조건부 복원**: 원안에서 제외했던 도구를 recovery 경로에서 조건부 복원.
9. **PROMOTION_ORDER에 CANDIDATE 추가**: 코드 직접 검증으로 누락 확인.
10. **discovered_facts 갱신 경로 추가**: recovery_agent의 발견이 sql_generator까지 전달되도록 보강.
