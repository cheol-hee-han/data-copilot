# Agentic Recovery Loop 재설계 — 상세 설계

- **작성일**: 2026-03-31
- **상위 문서**: `01-strategy.md`
- **구현 대상 Step**: Step 1 (기계적 분리) → Step 2 (리네이밍) → Step 3 (recovery_agent 구현) → Step 4 (정리)

---

## 목차

1. State 변경 사항
2. Step 1: knowledge_fetcher + knowledge_interpreter 분리
3. Step 2: readiness_gate 리네이밍
4. Step 3: recovery_agent 구현
5. Step 4: pipeline.py 라우팅 최종 설계
6. 프롬프트 템플릿
7. 에러 처리 및 Fallback
8. 테스트 전략

---

## 1. State 변경 사항

### 1.1 ReasoningState 필드 추가

```python
# src/agents/state/state.py — ReasoningState에 추가

class ReasoningState(BaseModel):
    # ── 기존 필드 전부 유지 ──
    ...

    # ── 신규 필드 ──
    exploration_phase: Literal["initial", "recovery"] = "initial"
    """현재 탐색 단계. readiness_gate의 라우팅 판단에 사용."""

    recovery_rounds: int = 0
    """recovery_agent 내부 ReAct 루프의 실행 라운드 수. trace/디버깅용."""
```

**변경하지 않는 필드**:
- `execution_plan`: Phase 1(knowledge_fetcher)에서만 사용. recovery_agent는 자체 ReAct 루프로 도구를 결정하므로 execution_plan을 갱신하지 않는다.
- `phase`: 기존 Phase enum을 그대로 사용. `REPLANNING`을 recovery_agent 진입 시의 Phase로 재사용.
- `loop_guard`: 기존 필드 그대로 사용. increment 위치만 변경.

### 1.2 신규 모델 (recovery_agent 전용)

```python
# src/agents/nodes/reason/recovery_agent.py 내부 정의
# 또는 src/agents/state/recovery_models.py로 분리 가능

from pydantic import BaseModel, Field
from typing import Literal


class ToolCall(BaseModel):
    """recovery_agent가 요청하는 단일 도구 호출."""
    tool: Literal[
        "search_table_meta",
        "search_code_meta",
        "search_manual",
        "search_glossary",
        "get_sample_rows",
        "get_date_distribution",
    ]
    kwargs: dict[str, str]
    purpose: str


class KnowledgeUpdate(BaseModel):
    """도구 결과 해석 후 knowledge_item 갱신 지시."""
    key: str
    new_status: Literal["PROBABLE", "CONFIRMED", "CONFLICTED"]
    evidence: str
    value: str | None = None


class TableUpdate(BaseModel):
    """도구 결과 해석 후 candidate_table 갱신 지시."""
    table_name: str
    action: Literal["SELECT", "REJECT", "ADD_JOIN_KEY", "SET_DATE_COLUMN"]
    reason: str
    detail: str | None = None  # join_key명, date_column명 등


class RecoveryDecision(BaseModel):
    """recovery_agent의 LLM 출력 스키마."""
    analysis: str
    lessons_learned: str = ""
    action: Literal["call_tools", "ready", "give_up"]
    tool_calls: list[ToolCall] = Field(default_factory=list, max_length=4)
    knowledge_updates: list[KnowledgeUpdate] = Field(default_factory=list)
    table_updates: list[TableUpdate] = Field(default_factory=list)
    target_knowledge_gap: str = ""
```

### 1.3 nodes/__init__.py 업데이트

```python
# src/agents/nodes/__init__.py — 최종 상태

# Interpret layer
from .interpret.history_resolver import resolve_history_node
from .interpret.intent_classifier import classify_intent_node
from .interpret.query_normalizer import normalize_query_node
from .interpret.clarification_handler import clarification_handler_node

# Reason layer
from .reason.planner import planner_node
from .reason.knowledge_fetcher import knowledge_fetcher_node      # 신규
from .reason.knowledge_interpreter import knowledge_interpreter_node  # 신규
from .reason.readiness_gate import readiness_gate_node          # 리네이밍
from .reason.recovery_agent import recovery_agent_node          # 신규
from .reason.sql_generator import sql_generator_node
from .reason.sql_validator import sql_validator_node
from .reason.result_finalizer import result_finalizer_node

# Present layer
from .present.sql_executor import execute_sql_node
from .present.analyzer import analyze_data_node
from .present.formatter import format_response_node
```

---

## 2. Step 1: knowledge_fetcher + knowledge_interpreter 분리

### 2.1 knowledge_fetcher_node 인터페이스

```python
# src/agents/nodes/reason/knowledge_fetcher.py

async def knowledge_fetcher_node(state: PipelineState) -> dict:
    """
    Phase 1 도구 실행 + 관찰 데이터 수집.

    context_explorer.py에서 Phase 1-2를 추출한 노드.
    LLM 호출 없음 — 순수 I/O 노드.

    수행 작업:
    1. execution_plan의 PENDING 스텝을 순차 실행
    2. candidate_tables에 대한 date distribution 수집
    3. 미샘플링 테이블에 대한 sample rows 수집

    입력 (from state.reason):
        - execution_plan: list[ExecutionStep]
        - candidate_tables: list[CandidateTable]
        - searched_queries: list[str]  (중복 방지)
        - loop_guard: LoopGuard

    출력 (state.reason 갱신):
        - execution_plan: 각 step의 status/result_ref/insight 갱신
        - candidate_tables: sample_rows, observed_date_columns 추가
        - searched_queries: 실행된 쿼리 추가
        - loop_guard.total_tool_calls: 실행된 도구 수만큼 증가
    """
    reason = state.reason

    # ── Phase 1: execution_plan 순차 실행 ──
    # 기존 context_explorer_node() lines 261-289의 인라인 루프를 함수로 추출
    await _execute_pending_steps(reason)

    # ── Phase 2: 관찰 데이터 수집 ──
    await _observe_all_date_distributions(reason.candidate_tables)
    await _sample_unsampled_tables(reason.candidate_tables)

    return {"reason": reason}
```

**이동 대상 함수** (context_explorer.py → knowledge_fetcher.py):

| 함수 | 원본 위치 | 변경 사항 |
|------|----------|----------|
| Phase 1 인라인 루프 | context_explorer_node() 내부 (lines 261-289) | 독립 함수 `_execute_pending_steps()`로 추출 |
| `_run_step()` | context_explorer.py | 시그니처 변경 없음 |
| `_should_skip_step()` | context_explorer.py | 시그니처 변경 없음 |
| `_observe_all_date_distributions()` | context_explorer.py | 시그니처 변경 없음 |
| `_sample_unsampled_tables()` | context_explorer.py | 시그니처 변경 없음 |

### 2.2 knowledge_interpreter_node 인터페이스

```python
# src/agents/nodes/reason/knowledge_interpreter.py

async def knowledge_interpreter_node(state: PipelineState) -> dict:
    """
    Phase 3-6 도구 결과 해석 + 상태 반영.

    context_explorer.py에서 Phase 3-6을 추출한 노드.
    LLM 1회 호출 (배치 해석).

    수행 작업:
    1. 도구 결과 + 관찰 데이터를 배치 LLM 해석
    2. 해석 결과를 knowledge_items, candidate_tables, code_map에 반영
    3. 부적합 테이블 필터링
    4. 신뢰도 승격

    입력 (from state.reason):
        - execution_plan: 실행 완료된 스텝들 (result_ref 채워짐)
        - candidate_tables: sample_rows, date 관찰 포함
        - knowledge_items, code_map, discovered_facts

    출력 (state.reason 갱신):
        - knowledge_items: 상태/증거 갱신
        - candidate_tables: selection_status, inferred_* 필드 갱신
        - code_map: 코드 메타 병합
        - discovered_facts: LLM 해석에서 추출된 사실 추가
    """
    reason = state.reason

    # ── Phase 3: 배치 LLM 해석 ──
    batch_insights = await _interpret_batch(reason)

    # ── Phase 4: 해석 결과 반영 ──
    _apply_batch_insights(reason, batch_insights)

    # ── Phase 5: 테이블 선택/거절 마킹 ──
    # 기존 context_explorer_node() lines 327-369의 인라인 코드를 함수로 추출
    _mark_table_selection(reason, batch_insights)

    # ── Phase 6: 중복 제거 + 신뢰도 승격 ──
    _dedup_knowledge_items(reason.knowledge_items)
    _promote_sampled_confidence(reason.candidate_tables, reason.knowledge_items)

    return {"reason": reason}
```

**이동 대상 함수** (context_explorer.py → knowledge_interpreter.py):

| 함수 | 원본 위치 | 변경 사항 |
|------|----------|----------|
| `_interpret_batch()` | context_explorer.py | 시그니처 변경 없음 |
| `_apply_batch_insights()` | context_explorer.py | 시그니처 변경 없음 |
| Phase 5 테이블 마킹 인라인 코드 | context_explorer_node() 내부 (lines 327-369) | 독립 함수 `_mark_table_selection()`으로 추출 |
| `_promote_sampled_confidence()`, `_dedup_knowledge_items()` | context_explorer.py (Phase 6) | 시그니처 변경 없음 |
| `_extract_time_slot()` | context_explorer.py | _interpret_batch 의존 → 함께 이동 |

### 2.3 공통 유틸리티

`knowledge_fetcher`와 `knowledge_interpreter`가 공유하는 헬퍼가 있다면 `nodes/reason/_exploration_utils.py`로 추출한다. 단, 현재 분석 결과 두 노드 간 직접 공유하는 함수는 없으므로 (Phase 1-2와 Phase 3-6이 깔끔하게 분리됨), 이 파일은 **필요 시에만** 생성한다.

---

## 3. Step 2: readiness_gate 리네이밍

```python
# src/agents/nodes/reason/readiness_gate.py

from src.services.confidence_scorer import evaluate_readiness, ReadinessVerdict

# 기존 confidence_evaluator_node과 동일한 로직
async def readiness_gate_node(state: PipelineState) -> dict:
    """
    규칙 기반 readiness 판정 → Phase 전이.

    confidence_evaluator.py에서 리네이밍.
    로직 변경 없음.

    추가된 라우팅 분기:
    - exploration_phase == "recovery" 이면 EXPLORE verdict도 recovery_agent로 라우팅
    """
    reason = state.reason
    score = calculate_readiness(reason)
    verdict = evaluate_readiness(reason)

    # Force-generate 로직 (기존과 동일)
    if (
        reason.loop_guard.replan_count >= 2
        and score >= THRESHOLD_FORCE_GENERATE
        and verdict in (ReadinessVerdict.REPLAN, ReadinessVerdict.TERMINATE)
    ):
        verdict = ReadinessVerdict.GENERATE

    # Phase 전이 (기존과 동일)
    reason.phase = VERDICT_TO_PHASE[verdict]

    # recovery 진입 시 exploration_phase 전환
    if verdict == ReadinessVerdict.REPLAN:
        reason.exploration_phase = "recovery"

    add_trace(state, "readiness_gate", f"score={score:.2f}, verdict={verdict.value}")
    return {"reason": reason}
```

---

## 4. Step 3: recovery_agent 구현

### 4.1 전체 구조

```python
# src/agents/nodes/reason/recovery_agent.py

"""
Agentic Recovery Loop — ReAct 패턴 기반 반응적 지식 탐색.

기존 recovery_planner + context_explorer의 recovery 경로를 통합.
내부 ReAct 루프에서 LLM이 도구를 선택하고, 결과를 해석하고, 다음 행동을 결정.

주요 함수:
    recovery_agent_node()          — 그래프 노드 엔트리포인트
    _handle_hypothesis_transition() — deterministic hypothesis 상태 전이
    _recovery_step()               — 단일 ReAct 스텝 (단위 테스트 가능)
    _execute_tools()               — 도구 실행 + LoopGuard 증분
    _apply_knowledge_updates()     — knowledge_items 갱신
    _apply_table_updates()         — candidate_tables 갱신
    _build_recovery_prompt()       — LLM 프롬프트 조립 (truncation 포함)
    _parse_recovery_response()     — JSON 파싱 + fallback
"""
```

### 4.2 recovery_agent_node 구현

```python
async def recovery_agent_node(state: PipelineState) -> dict:
    """
    Agentic Recovery Loop 오케스트레이터.

    흐름:
    1. [Deterministic] Hypothesis 상태 전이 (ACTIVE→FAILED, DeadEnd 기록)
    2. [ReAct Loop] LLM 호출 → 도구 실행 → 반복
    3. [Deterministic] Phase 전이 (GENERATING / DONE)

    진입 조건:
    - readiness_gate에서 REPLAN verdict
    - sql_validator에서 SEMANTIC/STRUCTURAL/EMPTY/DB_ERROR 실패

    종료 조건:
    - LLM이 action: "ready" 반환 → Phase.GENERATING
    - LLM이 action: "give_up" 반환 → Phase.DONE + FAILURE
    - LoopGuard 한계 도달 → Phase.DONE + FAILURE (또는 force-generate)
    """
    reason = state.reason

    # ── 1. Hypothesis 상태 전이 ──
    _handle_hypothesis_transition(reason)
    reason.loop_guard.increment_replan()

    # ── 2. ReAct 루프 ──
    tool_results: list[dict] = []
    last_decision: RecoveryDecision | None = None
    max_internal_rounds = 5  # 내부 루프 안전장치

    for round_num in range(max_internal_rounds):
        if should_terminate(reason):
            break

        decision = await _recovery_step(reason, tool_results)
        last_decision = decision
        reason.recovery_rounds += 1

        # lessons_learned 첨부
        if decision.lessons_learned and reason.dead_ends:
            reason.dead_ends[-1].lessons_learned = decision.lessons_learned

        # knowledge/table 갱신 적용
        _apply_knowledge_updates(reason, decision.knowledge_updates)
        _apply_table_updates(reason, decision.table_updates)

        if decision.action != "call_tools":
            break

        if not decision.tool_calls:
            break  # 도구 없이 call_tools → 방어

        # 도구 실행
        tool_results = await _execute_tools(decision.tool_calls, reason)

    # ── 3. Phase 전이 ──
    _finalize_recovery(reason, last_decision)

    add_trace(state, "recovery_agent",
              f"rounds={reason.recovery_rounds}, action={last_decision.action if last_decision else 'none'}")
    return {"reason": reason}
```

### 4.3 _handle_hypothesis_transition

```python
def _handle_hypothesis_transition(reason: ReasoningState) -> None:
    """
    Deterministic hypothesis 상태 전이.
    기존 recovery_planner.py lines 86-110의 로직을 재사용.

    1. current_hypothesis가 있으면 FAILED로 전이
    2. DeadEnd 기록 (failure_type, failure_reason 사용)
    3. 다음 PENDING hypothesis를 ACTIVE로 전환
    """
    if reason.current_hypothesis and reason.current_hypothesis.status == HypothesisStatus.ACTIVE:
        reason.current_hypothesis.status = HypothesisStatus.FAILED
        reason.dead_ends.append(DeadEnd(
            hypothesis_id=reason.current_hypothesis.hypothesis_id,
            failure_type=reason.failure_type or FailureType.TERM_UNRESOLVABLE,
            reason=reason.failure_reason or "readiness 미달 또는 SQL 검증 실패",
        ))

    # PENDING hypothesis 소비
    pending = [h for h in reason.hypotheses if h.status == HypothesisStatus.PENDING]
    if pending:
        pending.sort(key=lambda h: h.priority)
        next_hypo = pending[0]
        next_hypo.status = HypothesisStatus.ACTIVE
        reason.current_hypothesis = next_hypo
    # PENDING이 없으면 LLM이 새 hypothesis를 제안할 수 있음 (RecoveryDecision에서)

    # failure 컨텍스트 초기화 (다음 사이클용)
    reason.failure_type = None
    reason.failure_reason = None
```

### 4.4 _recovery_step

```python
async def _recovery_step(
    reason: ReasoningState,
    tool_results: list[dict],
) -> RecoveryDecision:
    """
    단일 ReAct 스텝. 독립적으로 단위 테스트 가능.

    Args:
        reason: 현재 reasoning 상태
        tool_results: 이전 라운드의 도구 실행 결과 (첫 호출 시 빈 리스트)

    Returns:
        RecoveryDecision: LLM의 분석 + 행동 결정
    """
    prompt = _build_recovery_prompt(reason, tool_results)

    response = await call_llm(
        system=RECOVERY_AGENT_SYSTEM_PROMPT,
        user=prompt,
        response_format={"type": "json_object"},
        # thinking_mode는 NODE_THINKING_MODES["recovery_agent"]에서 결정
    )

    return _parse_recovery_response(response)
```

### 4.5 _execute_tools

```python
async def _execute_tools(
    tool_calls: list[ToolCall],
    reason: ReasoningState,
) -> list[dict]:
    """
    도구 실행 + LoopGuard 증분.

    Args:
        tool_calls: LLM이 결정한 도구 호출 목록 (max 4)
        reason: LoopGuard 증분용

    Returns:
        각 도구의 실행 결과 리스트
    """
    results = []

    for tc in tool_calls:
        if should_terminate(reason):
            break

        try:
            # tools.py의 execute_tool 재사용
            result = await execute_tool(tc.tool, tc.kwargs)
            results.append({
                "tool": tc.tool,
                "purpose": tc.purpose,
                "status": "success",
                "result": result,
            })
        except Exception as e:
            results.append({
                "tool": tc.tool,
                "purpose": tc.purpose,
                "status": "error",
                "result": str(e),
            })

        reason.loop_guard.increment_tool_calls()

    return results
```

### 4.6 _apply_knowledge_updates

```python
def _apply_knowledge_updates(
    reason: ReasoningState,
    updates: list[KnowledgeUpdate],
) -> None:
    """
    LLM의 knowledge_updates를 state에 반영.

    매칭 전략: update.key와 knowledge_item.key의 정확 일치 또는 부분 일치.
    일치하는 항목이 없으면 새 KnowledgeItem 생성.
    """
    for update in updates:
        matched = _find_knowledge_item(reason.knowledge_items, update.key)

        if matched:
            # 상태 승격만 허용 (UNRESOLVED→PROBABLE→CONFIRMED)
            # 단, CONFLICTED는 어디서든 설정 가능
            target_status = ConfidenceStatus(update.new_status)
            if _is_valid_promotion(matched.status, target_status):
                matched.status = target_status
                matched.evidence.append(update.evidence)
                if update.value:
                    matched.value = update.value
        else:
            # 새 knowledge_item 생성
            reason.knowledge_items.append(KnowledgeItem(
                key=update.key,
                value=update.value or "",
                status=ConfidenceStatus(update.new_status),
                evidence=[update.evidence],
                source="recovery_agent",
            ))


def _find_knowledge_item(items: list[KnowledgeItem], key: str) -> KnowledgeItem | None:
    """key 매칭 — 정확 일치 우선, 부분 일치 fallback."""
    # 정확 일치
    for item in items:
        if item.key == key:
            return item
    # 부분 일치 (key가 item.key를 포함하거나 그 반대)
    for item in items:
        if key in item.key or item.key in key:
            return item
    return None


def _is_valid_promotion(current: ConfidenceStatus, target: ConfidenceStatus) -> bool:
    """상태 승격 유효성 검증. 역행 방지 (CONFIRMED → PROBABLE 불가)."""
    PROMOTION_ORDER = {
        ConfidenceStatus.UNRESOLVED: 0,
        ConfidenceStatus.PROBABLE: 1,
        ConfidenceStatus.CONFIRMED: 2,
        ConfidenceStatus.CONFLICTED: 3,  # CONFLICTED는 항상 설정 가능
    }
    if target == ConfidenceStatus.CONFLICTED:
        return True
    return PROMOTION_ORDER.get(target, 0) >= PROMOTION_ORDER.get(current, 0)
```

### 4.7 _apply_table_updates

```python
def _apply_table_updates(
    reason: ReasoningState,
    updates: list[TableUpdate],
) -> None:
    """
    LLM의 table_updates를 candidate_tables에 반영.
    """
    for update in updates:
        table = _find_candidate_table(reason.candidate_tables, update.table_name)
        if not table:
            continue

        if update.action == "SELECT":
            table.selection_status = TableSelectionStatus.SELECTED
            table.selection_reason = update.reason
        elif update.action == "REJECT":
            table.selection_status = TableSelectionStatus.REJECTED
            table.selection_reason = update.reason
        elif update.action == "ADD_JOIN_KEY" and update.detail:
            if update.detail not in table.join_keys:
                table.join_keys.append(update.detail)
        elif update.action == "SET_DATE_COLUMN" and update.detail:
            table.inferred_key_date_column = update.detail
```

### 4.8 _build_recovery_prompt (truncation 포함)

```python
def _build_recovery_prompt(
    reason: ReasoningState,
    tool_results: list[dict],
) -> str:
    """
    recovery_agent LLM 호출용 프롬프트 조립.

    기존 recovery_planner._build_replan_context() 기반.
    컨텍스트 윈도우 절약을 위한 truncation 적용.
    """
    sections = []

    # 1. 원본 질의
    sections.append(f"## 원본 질의\n{reason.query_decomposition.get('original_query', '')}")

    # 2. 현재 확인된 지식
    confirmed = [ki for ki in reason.knowledge_items if ki.status == ConfidenceStatus.CONFIRMED]
    probable = [ki for ki in reason.knowledge_items if ki.status == ConfidenceStatus.PROBABLE]
    if confirmed or probable:
        lines = []
        for ki in confirmed:
            lines.append(f"- [확정] {ki.key}: {ki.value} (근거: {ki.evidence[-1] if ki.evidence else 'N/A'})")
        for ki in probable:
            lines.append(f"- [추정] {ki.key}: {ki.value}")
        sections.append(f"## 확인된 지식\n" + "\n".join(lines))

    # 3. 미해결 항목 (탐색 대상)
    unresolved = [ki for ki in reason.knowledge_items if ki.status == ConfidenceStatus.UNRESOLVED]
    conflicted = [ki for ki in reason.knowledge_items if ki.status == ConfidenceStatus.CONFLICTED]
    if unresolved or conflicted:
        lines = []
        for ki in unresolved:
            lines.append(f"- [미해결] {ki.key}")
        for ki in conflicted:
            lines.append(f"- [상충] {ki.key}: {ki.value} (근거 상충: {', '.join(ki.evidence[-2:])})")
        sections.append(f"## 미해결 항목\n" + "\n".join(lines))

    # 4. 후보 테이블 (REJECTED 제외, 컬럼 20개 제한, sample_rows 3행 제한)
    active_tables = [t for t in reason.candidate_tables
                     if t.selection_status != TableSelectionStatus.REJECTED]
    if active_tables:
        lines = []
        for t in active_tables:
            cols = t.columns[:20]
            col_str = ", ".join(c.get("name", "") for c in cols)
            if len(t.columns) > 20:
                col_str += f" ... (+{len(t.columns) - 20}개)"
            sample_str = ""
            if t.sample_rows:
                sample_str = f"\n  샘플({len(t.sample_rows[:3])}행): {t.sample_rows[:3]}"
            lines.append(
                f"- [{t.selection_status.value}] {t.table_name}"
                f" ({t.description or 'N/A'})\n"
                f"  컬럼: {col_str}{sample_str}"
            )
        sections.append(f"## 후보 테이블\n" + "\n".join(lines))

    # 5. 실패 기록 (lessons_learned 100자 truncate)
    if reason.dead_ends:
        lines = []
        for de in reason.dead_ends:
            lessons = (de.lessons_learned or "")[:100]
            lines.append(f"- [{de.failure_type.value}] {de.reason[:80]} → 교훈: {lessons}")
        sections.append(f"## 실패 기록 (이 경로들은 피하세요)\n" + "\n".join(lines))

    # 6. 구조적 힌트 (use_case에서 추출)
    if reason.explored_use_cases:
        lines = []
        for uc in reason.explored_use_cases[:3]:
            lines.append(f"- 유사도 {uc.get('similarity', 0):.2f}: {uc.get('description', '')[:80]}")
        sections.append(f"## 참조 가능한 유사 SQL\n" + "\n".join(lines))

    # 7. 이전 도구 결과 (최근 라운드만)
    if tool_results:
        lines = []
        for tr in tool_results:
            result_str = str(tr["result"])[:500]  # 500자 truncate
            lines.append(f"- [{tr['tool']}] 목적: {tr['purpose']}\n  결과: {result_str}")
        sections.append(f"## 이전 도구 실행 결과\n" + "\n".join(lines))

    return "\n\n".join(sections)
```

### 4.9 _parse_recovery_response (fallback 포함)

```python
import json
import re

def _parse_recovery_response(response: str) -> RecoveryDecision:
    """
    LLM 응답을 RecoveryDecision으로 파싱.
    JSON 파싱 실패 시 regex fallback.
    """
    # 1차: 직접 JSON 파싱
    try:
        data = json.loads(response)
        return RecoveryDecision.model_validate(data)
    except (json.JSONDecodeError, ValueError):
        pass

    # 2차: JSON 블록 추출 (```json ... ``` 패턴)
    json_match = re.search(r"```json\s*\n?(.*?)\n?\s*```", response, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(1))
            return RecoveryDecision.model_validate(data)
        except (json.JSONDecodeError, ValueError):
            pass

    # 3차: Fallback — action만 추출
    action = "give_up"
    if re.search(r'"action"\s*:\s*"ready"', response):
        action = "ready"
    elif re.search(r'"action"\s*:\s*"call_tools"', response):
        action = "call_tools"

    return RecoveryDecision(
        analysis="LLM 응답 파싱 실패",
        action=action,
        target_knowledge_gap="parsing_failure",
    )
```

### 4.10 _finalize_recovery

```python
def _finalize_recovery(reason: ReasoningState, decision: RecoveryDecision | None) -> None:
    """recovery 종료 후 Phase 전이."""
    if decision is None or decision.action == "give_up" or should_terminate(reason):
        # 종료: force-generate 가능 여부는 readiness_gate가 판단
        # recovery_agent는 readiness_gate로 보내고, 거기서 force-generate 로직 적용
        reason.phase = Phase.VERIFYING  # readiness_gate로 재진입
    elif decision.action == "ready":
        reason.phase = Phase.GENERATING
```

---

## 5. Step 4: pipeline.py 라우팅 최종 설계

### 5.1 노드 등록

```python
def build_pipeline() -> StateGraph:
    workflow = StateGraph(PipelineState)

    # ── Interpret Layer ──
    workflow.add_node("resolve_history", resolve_history_node)
    workflow.add_node("classify_intent", classify_intent_node)
    workflow.add_node("normalize_query", normalize_query_node)
    workflow.add_node("clarification_handler", clarification_handler_node)

    # ── Reason Layer ──
    workflow.add_node("planner", planner_node)
    workflow.add_node("knowledge_fetcher", knowledge_fetcher_node)        # 신규
    workflow.add_node("knowledge_interpreter", knowledge_interpreter_node)  # 신규
    workflow.add_node("readiness_gate", readiness_gate_node)            # 리네이밍
    workflow.add_node("recovery_agent", recovery_agent_node)            # 신규
    workflow.add_node("sql_generator", sql_generator_node)
    workflow.add_node("sql_validator", sql_validator_node)
    workflow.add_node("result_finalizer", result_finalizer_node)

    # ── Present Layer ──
    workflow.add_node("execute_sql", execute_sql_node)
    workflow.add_node("analyze_data", analyze_data_node)
    workflow.add_node("format_response", format_response_node)
    workflow.add_node("error_end", _handle_error)  # W-10: 향후 nodes/present/error_handler.py로 이동

    ...
```

### 5.2 라우팅 함수

```python
# ── Reason Layer 라우팅 ──

def _route_after_planner(state: PipelineState) -> str:
    """planner 이후: fast-path 또는 Phase 1 진입."""
    if state.reason.fast_path_triggered:
        return "sql_generator"
    return "knowledge_fetcher"


def _route_after_knowledge_interpreter(state: PipelineState) -> str:
    """knowledge_interpreter는 항상 readiness_gate로."""
    return "readiness_gate"


def _route_after_readiness_gate(state: PipelineState) -> str:
    """readiness_gate: 5-way 라우팅."""
    reason = state.reason
    verdict = reason.phase  # readiness_gate_node에서 이미 VERDICT_TO_PHASE로 변환됨

    if verdict == Phase.GENERATING:
        return "sql_generator"

    if verdict == Phase.EXPLORING:
        # 초기 탐색 중 추가 탐색이 필요한 경우 (드문 케이스)
        if reason.exploration_phase == "initial":
            return "knowledge_fetcher"
        else:
            return "recovery_agent"

    if verdict == Phase.REPLANNING:
        return "recovery_agent"

    if verdict == Phase.VERIFYING:
        # ASK_USER → clarification_handler으로
        if any(s.decision == "ASK" for s in getattr(state, 'pending_signals', [])):
            return "clarification_handler"
        return "result_finalizer"

    # Phase.DONE → TERMINATE
    return "result_finalizer"


def _route_after_recovery_agent(state: PipelineState) -> str:
    """recovery_agent 이후: GENERATING 또는 readiness_gate 재진입."""
    reason = state.reason

    if reason.phase == Phase.GENERATING:
        return "sql_generator"

    if reason.phase == Phase.DONE:
        return "result_finalizer"

    # Phase.VERIFYING → readiness_gate 재진입 (force-generate 판정 등)
    return "readiness_gate"


def _route_after_sql_validator(state: PipelineState) -> str:
    """sql_validator 이후: 6-way 라우팅."""
    reason = state.reason

    # fast-path 실패 → Phase 1부터 정상 수행
    if reason.fast_path_triggered and not reason.validated_sql:
        reason.fast_path_triggered = False
        reason.exploration_phase = "initial"
        return "knowledge_fetcher"

    # 성공
    if reason.validated_sql:
        return "result_finalizer"

    # 구문 오류 → sql_generator 재시도
    if (reason.failure_type == FailureType.SQL_SYNTAX
            and reason.loop_guard.generate_attempts < MAX_GENERATES):
        return "sql_generator"

    # 로컬 의미 오류 → local fix 가능하면 sql_generator, 초과 시 recovery
    if reason.failure_type == FailureType.SQL_SEMANTIC_LOCAL:
        if reason.loop_guard.local_fix_count < MAX_LOCAL_FIXES:
            return "sql_generator"
        # local fix 초과 → recovery로 에스컬레이션
        return "recovery_agent"

    # 구조/빈결과/DB오류 → recovery
    if reason.failure_type in (
        FailureType.SQL_STRUCTURAL,
        FailureType.EMPTY_RESULT,
        FailureType.DB_ERROR,
    ):
        return "recovery_agent"

    # fallback
    return "result_finalizer"
```

### 5.3 엣지 등록

```python
    # ── Reason Layer 엣지 ──
    workflow.add_conditional_edges("planner", _route_after_planner,
        {"sql_generator": "sql_generator", "knowledge_fetcher": "knowledge_fetcher"})

    workflow.add_edge("knowledge_fetcher", "knowledge_interpreter")

    workflow.add_conditional_edges("knowledge_interpreter", _route_after_knowledge_interpreter,
        {"readiness_gate": "readiness_gate"})
    # 또는 단순 edge:
    # workflow.add_edge("knowledge_interpreter", "readiness_gate")

    workflow.add_conditional_edges("readiness_gate", _route_after_readiness_gate,
        {
            "sql_generator": "sql_generator",
            "knowledge_fetcher": "knowledge_fetcher",
            "recovery_agent": "recovery_agent",
            "clarification_handler": "clarification_handler",
            "result_finalizer": "result_finalizer",
        })

    workflow.add_conditional_edges("recovery_agent", _route_after_recovery_agent,
        {
            "sql_generator": "sql_generator",
            "result_finalizer": "result_finalizer",
            "readiness_gate": "readiness_gate",
        })

    workflow.add_edge("sql_generator", "sql_validator")

    workflow.add_conditional_edges("sql_validator", _route_after_sql_validator,
        {
            "result_finalizer": "result_finalizer",
            "sql_generator": "sql_generator",
            "recovery_agent": "recovery_agent",
            "knowledge_fetcher": "knowledge_fetcher",
        })
```

---

## 6. 프롬프트 템플릿

### 6.1 RECOVERY_AGENT_SYSTEM_PROMPT

```python
RECOVERY_AGENT_SYSTEM_PROMPT = """당신은 은행 데이터 분석을 위한 SQL 생성 에이전트의 recovery 모듈입니다.
이전 시도가 실패했거나 지식이 부족하여 SQL을 생성할 수 없었습니다.
현재 상태를 분석하고, 부족한 지식을 채우기 위해 도구를 사용하세요.

## 사용 가능한 도구

1. search_table_meta(query): 테이블/컬럼 메타데이터를 검색합니다.
   - kwargs: {"query": "검색할 테이블 또는 컬럼 관련 키워드"}
   - 예: {"query": "여신 실행"} → 여신 관련 테이블 메타 반환

2. search_code_meta(column_name): 코드 컬럼의 값 매핑을 조회합니다.
   - kwargs: {"column_name": "코드 컬럼명"}
   - 예: {"column_name": "loan_type_cd"} → {"01": "일반대출", "02": "주택담보대출", ...}

3. search_manual(query): 업무 매뉴얼에서 업무 규정, 계수 산출식을 검색합니다.
   - kwargs: {"query": "검색할 업무 키워드"}
   - 예: {"query": "연체율 산출"} → 연체율 계산 공식 및 관련 규정

4. search_glossary(term): 금융 용어사전에서 정의를 조회합니다.
   - kwargs: {"term": "금융 용어"}
   - 예: {"term": "BIS비율"} → 정의 및 관련 테이블/컬럼

5. get_sample_rows(table_name, schema_name?, db_source?, limit?): 테이블의 샘플 데이터를 조회합니다.
   - kwargs: {"table_name": "테이블명", "limit": "5"}
   - 예: {"table_name": "TB_LOAN_INFO", "limit": "5"} → 5행 샘플

6. get_date_distribution(table_name, date_column, schema_name?, db_source?): 날짜 컬럼의 범위를 조회합니다.
   - kwargs: {"table_name": "테이블명", "date_column": "날짜컬럼명"}
   - 예: {"table_name": "TB_LOAN_INFO", "date_column": "base_dt"} → {"min": "20200101", "max": "20260331"}

## 응답 규칙

1. 미해결 항목 중 가장 중요한 공백을 먼저 해소하세요.
2. 독립적인 공백은 한번에 여러 도구로 조회하세요 (최대 4개).
3. 실패 기록에 있는 경로를 반복하지 마세요.
4. 도구 결과가 있으면 그 결과를 기반으로 knowledge_updates와 table_updates를 제안하세요.
5. SQL 생성에 충분한 지식이 모이면 action: "ready"로 응답하세요.
6. 더 이상 시도할 수 있는 경로가 없으면 action: "give_up"으로 응답하세요.
7. 상충하는 정보(CONFLICTED)를 발견하면 knowledge_updates에서 해당 항목을 CONFLICTED로 표시하고 action: "ready"로 종료하세요. 사용자 확인이 필요합니다.

## 응답 JSON 스키마

```json
{
  "analysis": "현재 상황 분석 (어떤 지식이 부족한지, 왜 이전 시도가 실패했는지)",
  "lessons_learned": "이전 실패에서 배운 교훈 (없으면 빈 문자열)",
  "action": "call_tools | ready | give_up",
  "tool_calls": [
    {
      "tool": "도구명",
      "kwargs": {"key": "value"},
      "purpose": "이 도구를 호출하는 이유"
    }
  ],
  "knowledge_updates": [
    {
      "key": "knowledge_item의 key",
      "new_status": "PROBABLE | CONFIRMED | CONFLICTED",
      "evidence": "근거",
      "value": "값 (선택)"
    }
  ],
  "table_updates": [
    {
      "table_name": "테이블명",
      "action": "SELECT | REJECT | ADD_JOIN_KEY | SET_DATE_COLUMN",
      "reason": "사유",
      "detail": "join_key명 또는 date_column명 (선택)"
    }
  ],
  "target_knowledge_gap": "이번 라운드에서 해소하려는 주요 공백"
}
```
"""
```

### 6.2 NODE_THINKING_MODES 추가

```python
# src/agents/nodes/thinking_modes.py

NODE_THINKING_MODES: dict[str, str] = {
    ...  # 기존 항목 유지
    "knowledge_fetcher": "off",        # LLM 미사용
    "knowledge_interpreter": "auto",   # 배치 해석
    "readiness_gate": "off",         # 규칙 기반
    "recovery_agent": "auto",        # 폐쇄망 모델 thinking 지원 시 활용
}
```

---

## 7. 에러 처리 및 Fallback

### 7.1 recovery_agent 내부 에러 처리

| 시나리오 | 처리 |
|---------|------|
| LLM 호출 실패 (네트워크, 타임아웃) | `RecoveryDecision(action="give_up", analysis="LLM 호출 실패")` 반환 → readiness_gate에서 force-generate 판정 |
| LLM 응답 파싱 실패 | `_parse_recovery_response`의 regex fallback → action 추출 |
| 도구 실행 실패 | `_execute_tools`에서 개별 try/except → `status: "error"` 기록, 다음 도구 계속 실행 |
| should_terminate() 도달 | ReAct 루프 즉시 중단 → `_finalize_recovery`에서 readiness_gate로 위임 |
| max_internal_rounds (5) 도달 | 루프 중단 → 현재까지의 knowledge 유지, readiness_gate로 위임 |

### 7.2 execute_tool kwargs 매핑

현재 `tools.py`의 `execute_tool(tool_name, tool_input)` 시그니처가 `tool_input: str`(단일 문자열)을 받는 반면, RecoveryDecision의 `ToolCall.kwargs`는 `dict`이다.

**호환 방안**: recovery_agent에서 호출 시 kwargs를 tool_input 문자열로 변환하는 어댑터를 사용한다.

```python
async def _execute_single_tool(tc: ToolCall, reason: ReasoningState) -> Any:
    """ToolCall.kwargs를 execute_tool의 tool_input으로 변환."""
    tool_name = tc.tool

    if tool_name in ("search_table_meta", "search_use_cases", "search_manual", "search_glossary"):
        # 검색 도구: query 또는 term을 tool_input으로
        tool_input = tc.kwargs.get("query") or tc.kwargs.get("term", "")
    elif tool_name == "search_code_meta":
        tool_input = tc.kwargs.get("column_name", "")
    elif tool_name in ("get_sample_rows", "get_date_distribution"):
        # DB 관찰 도구: JSON 문자열로 전달
        tool_input = json.dumps(tc.kwargs, ensure_ascii=False)
    else:
        tool_input = json.dumps(tc.kwargs, ensure_ascii=False)

    return await execute_tool(tool_name, tool_input)
```

**향후 개선**: `execute_tool`의 시그니처를 `execute_tool(tool_name, **kwargs)` 형태로 변경하면 어댑터가 불필요해진다. 이는 별도 리팩터링으로 진행.

---

## 8. 테스트 전략

### 8.1 Step 1 검증 (동작 동일성)

```
기존 테스트 파일                        검증 내용
─────────────────────────────────────────────────────
test_agentic_core.py                  Phase 1 기본 흐름
test_agentic_e2e.py                   전체 파이프라인 e2e
test_agentic_flow_trace.py            trace_log에 새 노드명 반영 확인
```

**주의**: trace_log에 기록되는 노드명이 `context_explorer` → `knowledge_fetcher`/`knowledge_interpreter`로 변경되므로, trace를 assertion하는 테스트는 노드명 업데이트가 필요하다.

### 8.2 Step 3 단위 테스트

```python
# tests/auto/unit/test_recovery_agent.py

class TestHandleHypothesisTransition:
    """_handle_hypothesis_transition 단위 테스트."""

    def test_active_to_failed(self):
        """ACTIVE hypothesis가 FAILED로 전이되는지."""

    def test_dead_end_created(self):
        """DeadEnd가 failure_type/reason으로 생성되는지."""

    def test_pending_consumed_by_priority(self):
        """PENDING hypothesis가 priority 순으로 소비되는지."""

    def test_no_pending_leaves_current_none(self):
        """PENDING이 없으면 current_hypothesis가 None인지."""


class TestApplyKnowledgeUpdates:
    """_apply_knowledge_updates 단위 테스트."""

    def test_exact_key_match(self):
        """정확한 key 일치 시 갱신."""

    def test_partial_key_match(self):
        """부분 key 일치 시 갱신."""

    def test_new_item_created(self):
        """일치 항목 없으면 새 KnowledgeItem 생성."""

    def test_promotion_only(self):
        """상태 역행 방지 (CONFIRMED→PROBABLE 불가)."""

    def test_conflicted_always_allowed(self):
        """CONFLICTED는 어디서든 설정 가능."""


class TestRecoveryStep:
    """_recovery_step 단위 테스트 (LLM mock)."""

    async def test_call_tools_response(self):
        """call_tools action 시 tool_calls 파싱."""

    async def test_ready_response(self):
        """ready action 시 tool_calls 비어있음."""

    async def test_json_parse_failure_fallback(self):
        """JSON 파싱 실패 시 regex fallback."""


class TestRecoveryAgentNode:
    """recovery_agent_node 통합 테스트."""

    async def test_loop_terminates_on_ready(self):
        """LLM이 ready 반환 시 phase == GENERATING."""

    async def test_loop_terminates_on_give_up(self):
        """LLM이 give_up 반환 시 phase == VERIFYING (readiness_gate 위임)."""

    async def test_loop_guard_total_tool_calls(self):
        """MAX_TOOL_CALLS 도달 시 루프 중단."""

    async def test_max_internal_rounds(self):
        """5라운드 초과 시 루프 중단."""

    async def test_tool_execution_error_continues(self):
        """개별 도구 실패 시 다음 도구 계속 실행."""
```

### 8.3 Step 3 통합/e2e 테스트

recovery 경로를 타는 골든셋 질의 예시:

```python
# 1. 초기 탐색에서 테이블 미확정 → recovery 필요
"올해 1분기 지점별 여신 실행 금액 추이를 보여줘"
# 기대: planner → knowledge_fetcher → knowledge_interpreter → readiness_gate(NOT READY)
#       → recovery_agent (지점 테이블 확정) → readiness_gate(READY)
#       → sql_generator → sql_validator → ...

# 2. SQL 검증 실패 → recovery 진입
"작년 연체율이 가장 높은 상위 10개 지점"
# 기대: ... → sql_generator → sql_validator(SEMANTIC_LOCAL)
#       → sql_generator(fix) → sql_validator(STRUCTURAL) → recovery_agent
#       → readiness_gate → sql_generator → ...

# 3. recovery 실패 → force-generate
"특정 금융지표의 추이 분석" (메타 부실, 매뉴얼에도 없는 지표)
# 기대: ... → recovery_agent(2회 replan) → readiness_gate(force-generate)
#       → sql_generator → ...
```

---

## 부록: 파일 의존성 그래프 (변경 후)

```
nodes/reason/
├── planner.py              (변경 없음)
├── knowledge_fetcher.py      (신규 — context_explorer Phase 1-2)
├── knowledge_interpreter.py  (신규 — context_explorer Phase 3-6)
├── readiness_gate.py       (신규 — confidence_evaluator 리네이밍)
├── recovery_agent.py       (신규 — recovery_planner + recovery 경로 통합)
│   └── imports: tools.execute_tool, state.should_terminate, state.*, recovery_models.*
├── sql_generator.py        (변경 없음)
├── sql_validator.py        (변경 없음 — 라우팅만 pipeline.py에서 변경)
├── result_finalizer.py     (변경 없음)
└── tools.py                (변경 없음)

삭제 대상:
├── context_explorer.py     (Step 4에서 삭제)
├── recovery_planner.py     (Step 4에서 삭제)
└── confidence_evaluator.py (Step 4에서 삭제)

graph/
└── pipeline.py             (노드 등록 + 라우팅 함수 변경)

state/
└── state.py                (exploration_phase, recovery_rounds 필드 추가)
```
