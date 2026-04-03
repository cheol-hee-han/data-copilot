# 설계 검토 보고서: Agentic Recovery Loop 재설계

- **검토 대상**: `docs/strategy-proposals/agentic-recovery-redesign/01-strategy.md`, `02-detailed-design.md`
- **검토일**: 2026-04-01
- **검토 관점**: State 관리 연속성 → LLM 동작 신뢰성 → 라우팅 일관성 → 답변 정확도 → 폐쇄망 호환성
- **현재 코드 기준**: `main` 브랜치 (`6491f9b`)

---

## 총평

문제 진단(context_explorer 과부하, recovery_planner 실행 단절, 도구 일괄 전용)은 정확하고, 2-Phase Exploration 분리 전략은 합리적. 특히 Phase 1 기계적 분리(knowledge_fetcher + knowledge_interpreter)는 안전하고 가치 높은 변경. 그러나 recovery_agent 내부 ReAct 루프의 **상태 전이 규칙**, **외부 노드와의 라우팅 계약**, **폐쇄망 70B 모델 호환성**에서 구현 전 해소가 필요한 빈틈이 존재.

**심각도 분포**: P0 3건 / P1 4건 / P2 4건

---

## 잘 설계된 부분 (보존 권장)

### S-1. Phase 1 기계적 분리 (knowledge_fetcher + knowledge_interpreter)

현행 `context_explorer`의 Phase 1-2(순수 I/O)와 Phase 3-6(LLM 해석)은 책임 경계가 명확하여 기계적 추출만으로 동작 동일성이 보장됨. behavioral change 없이 1,177줄 단일 파일을 해소하는 가장 안전하고 가치 높은 변경.

### S-2. Hypothesis 상태 전이의 Python 코드 수행

검토 2에서 hypothesis 관리를 LLM이 아닌 deterministic Python 코드로 수행하기로 한 결정이 올바름. Solar Pro 2 70B에서 복잡한 상태 전이 JSON의 안정적 출력을 기대하기 어려움. 현행 `recovery_planner`와도 일관된 패턴.

### S-3. Structured Output 채택 (native tool-calling 기각)

폐쇄망 모델 호환성, `max_items: 4` 강제, 감사 추적 용이성, LLM 호출 횟수 절감 모두 native tool-calling 대비 우위. 모든 타겟 모델(Solar Pro 2, Qwen3.5, GPT OSS)에서 작동하는 최소 공통분모.

### S-4. CONFLICTED 처리를 recovery_agent 외부로 위임

recovery_agent 내부에서 LangGraph `interrupt()`를 호출하면 ReAct 루프의 상태 직렬화가 복잡해짐. CONFLICTED는 도구 호출로 해결되지 않는 성격의 문제이므로 readiness_gate → clarification_handler 경로로 위임하는 것이 적절.

### S-5. Truncation 전략의 티어별 설계

confirmed_knowledge 전량 포함, REJECTED 테이블 제외, tool_results 최근 1라운드만 유지하는 전략이 70B 모델의 8-16K 윈도우에 실용적. 예상 토큰 소모 2,000-4,000은 안전 마진 확보.

---

## [P0] 치명적 — 구현 전 반드시 해소

### P0-1. `_finalize_recovery` give_up 시 무한 루프 가능

**문제**: 01-strategy.md와 02-detailed-design.md에서 give_up 처리가 충돌함.

| 문서 | give_up 시 동작 | 위치 |
|------|----------------|------|
| 01-strategy.md | `Phase.DONE` + `FinalStatus.FAILURE` (즉시 종료) | 306-308행 |
| 02-detailed-design.md | `Phase.VERIFYING` (readiness_gate로 재진입) | 710행 |

02-detailed-design의 접근(readiness_gate 위임)이 force-generate SSOT 유지에는 유리하지만, **readiness_gate와 recovery_agent 사이에 무한 루프가 형성될 수 있음**.

**위험 시나리오**:

```
[1회차]
recovery_agent 진입 (replan_count: 0 → 1)
  → LLM이 어떤 도구도 유용하지 않다고 판단
  → action: "give_up" 반환
  → _finalize_recovery: Phase.VERIFYING 설정
  → readiness_gate로 라우팅

readiness_gate:
  readiness_score = 0.30 (< THRESHOLD_FORCE_GENERATE 0.40)
  replan_count = 1 (< MAX_REPLANS 3)
  → verdict: REPLAN
  → recovery_agent로 라우팅  ← 다시 돌아옴!

[2회차]
recovery_agent 진입 (replan_count: 1 → 2)
  → 상태가 변하지 않았으므로 다시 give_up
  → Phase.VERIFYING → readiness_gate
  → replan_count=2, score=0.30 → REPLAN → recovery_agent

[3회차]
recovery_agent 진입 (replan_count: 2 → 3)
  → 다시 give_up
  → readiness_gate: replan_count=3 ≥ MAX_REPLANS → TERMINATE
  → 비로소 종료
```

**결과**: 실질적 탐색 없이 LLM 3회 호출 + 불필요한 3회 루프 소모.

**해결안**: recovery_agent가 give_up을 반환한 사실을 state에 기록하여, readiness_gate에서 즉시 TERMINATE 또는 force-generate로 라우팅.

```python
# state.py — ReasoningState에 추가
recovery_gave_up: bool = False

# recovery_agent.py — _finalize_recovery에서
if decision.action == "give_up":
    reason.recovery_gave_up = True
    reason.phase = Phase.VERIFYING  # readiness_gate로 위임

# readiness_gate.py — 진입 시 체크
if reason.recovery_gave_up:
    reason.recovery_gave_up = False  # 리셋
    if score >= THRESHOLD_FORCE_GENERATE:
        verdict = ReadinessVerdict.GENERATE  # force-generate
    else:
        verdict = ReadinessVerdict.TERMINATE  # 즉시 종료
    # REPLAN으로 가지 않음 → 무한 루프 차단
```

---

### P0-2. `exploration_phase` 리셋 시점 미정의 — 멀티턴 라우팅 오류

**문제**: `exploration_phase: Literal["initial", "recovery"]` 필드가 `"recovery"`로 설정된 후 `"initial"`로 복귀하는 경로가 fast-path 실패 시(02-detailed-design.md 816행) 하나뿐임.

**위험 시나리오 (멀티턴)**:

```
── 1번째 질의: "올해 1분기 지점별 여신 실행 금액" ──

planner → knowledge_fetcher → knowledge_interpreter → readiness_gate
  → REPLAN verdict
  → exploration_phase = "recovery"  ← 여기서 설정됨
  → recovery_agent (ready) → sql_generator → sql_validator (PASS)
  → 성공 응답 반환

── 2번째 질의: "작년 수신잔액 추이" (같은 세션) ──

planner → knowledge_fetcher → knowledge_interpreter → readiness_gate
  → EXPLORE verdict (추가 탐색 필요)
  → exploration_phase == "recovery" (1번째 질의에서 잔류!)
  → recovery_agent로 잘못 라우팅!  ← 초기 탐색인데 recovery 경로 진입

기대 동작: knowledge_fetcher로 재진입하여 추가 도구 실행
실제 동작: recovery_agent의 ReAct 루프 진입 → 초기 컨텍스트 없이 탐색 시도
```

**해결안**: planner_node 진입 시 `exploration_phase`를 리셋.

```python
# planner.py — planner_node 시작부
async def planner_node(state: PipelineState) -> dict:
    reason = state.reason
    reason.exploration_phase = "initial"  # 매 질의 시작 시 리셋
    reason.recovery_rounds = 0           # recovery 카운터도 리셋
    ...
```

추가로 `readiness_gate`에서 `EXPLORE` verdict가 나왔을 때, `exploration_phase`가 `"recovery"`인 경우에도 초기 탐색 루프가 모두 완료된 상태인지 확인하는 가드를 추가.

---

### P0-3. execute_tool 어댑터와 현행 tool_input 형식 불일치

**문제**: 02-detailed-design.md 1006-1021행의 `_execute_single_tool` 어댑터에서 `get_sample_rows`, `get_date_distribution` 호출 시 kwargs를 `json.dumps`로 변환함. 그러나 현행 `tools.py`의 `_tool_get_sample_rows`, `_tool_get_date_distribution`은 **쉼표 구분 문자열**을 파싱함.

**현행 tools.py의 실제 파싱 패턴**:

```python
# tools.py — _tool_get_sample_rows (추정)
async def _tool_get_sample_rows(tool_input: str) -> list[dict]:
    # tool_input = "TB_LOAN_INFO" (테이블명만)
    # 또는 tool_input = "TB_LOAN_INFO,schema_name,db_source" (쉼표 구분)
    parts = tool_input.split(",")
    table_name = parts[0].strip()
    schema_name = parts[1].strip() if len(parts) > 1 else None
    ...
```

**어댑터가 전달하는 형식**:

```python
# _execute_single_tool에서
tool_input = json.dumps(tc.kwargs, ensure_ascii=False)
# → '{"table_name": "TB_LOAN_INFO", "limit": "5"}'
```

**결과**: `_tool_get_sample_rows`가 JSON 문자열 전체를 테이블명으로 인식 → DB 조회 실패 → recovery_agent가 모든 DB 관찰 도구를 사용할 수 없음.

**해결안**: kwargs를 현행 tool_input 형식으로 명시적 변환.

```python
async def _execute_single_tool(tc: ToolCall, reason: ReasoningState) -> Any:
    tool_name = tc.tool

    if tool_name in ("search_table_meta", "search_manual", "search_glossary"):
        tool_input = tc.kwargs.get("query") or tc.kwargs.get("term", "")
    elif tool_name == "search_code_meta":
        tool_input = tc.kwargs.get("column_name", "")
    elif tool_name == "get_sample_rows":
        # 현행 format: "table_name" 또는 "table_name,schema,db_source"
        parts = [tc.kwargs.get("table_name", "")]
        if tc.kwargs.get("schema_name"):
            parts.append(tc.kwargs["schema_name"])
        if tc.kwargs.get("db_source"):
            parts.append(tc.kwargs["db_source"])
        tool_input = ",".join(parts)
    elif tool_name == "get_date_distribution":
        # 현행 format: "table_name,date_column" 또는 +schema,db_source
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
        # fallback: 첫 번째 value 사용
        tool_input = next(iter(tc.kwargs.values()), "")

    return await execute_tool(tool_name, tool_input)
```

---

## [P1] 중요 — 구현 초기에 해소 권장

### P1-1. Phase 기반 라우팅에서 ReadinessVerdict 정보 소실

**문제**: 현행 `_route_after_confidence_evaluator`는 `ReadinessVerdict` enum을 직접 사용하여 라우팅함. 신규 설계에서는 `readiness_gate_node` 내부에서 `VERDICT_TO_PHASE`로 변환 후 `reason.phase`에 저장하고, 라우팅 함수에서 `reason.phase`를 읽음.

이 간접 참조에서 **ASK_USER verdict와 TERMINATE verdict가 동일한 `Phase.VERIFYING`으로 매핑**될 경우, 라우팅 함수가 이 둘을 구분할 수 없음.

**영향 예시**:

```
── ASK_USER verdict 시 ──
readiness_gate: verdict = ASK_USER → reason.phase = Phase.VERIFYING
_route_after_readiness_gate:
  verdict == Phase.VERIFYING
  → pending_signals에 ASK가 있는지 확인
  → "clarification_handler" 반환  ← 정상

── TERMINATE verdict 시 ──
readiness_gate: verdict = TERMINATE → reason.phase = Phase.DONE (?)
  (VERDICT_TO_PHASE 매핑이 TERMINATE → Phase.DONE이면 문제 없음)
  (하지만 TERMINATE → Phase.VERIFYING이면 아래 문제 발생)
_route_after_readiness_gate:
  verdict == Phase.VERIFYING
  → pending_signals에 ASK가 없음
  → "result_finalizer" 반환  ← 우연히 정상이지만 근거가 취약

── recovery_agent give_up 후 재진입 시 ──
readiness_gate: score 부족 → verdict = REPLAN → Phase.REPLANNING
  → recovery_agent로 라우팅  ← P0-1의 무한 루프 문제
```

**해결안**: ReadinessVerdict를 state에 직접 저장.

```python
# state.py
class ReasoningState(BaseModel):
    ...
    last_verdict: ReadinessVerdict | None = None

# readiness_gate.py
reason.last_verdict = verdict
reason.phase = VERDICT_TO_PHASE[verdict]

# _route_after_readiness_gate
def _route_after_readiness_gate(state: PipelineState) -> str:
    reason = state.reason
    verdict = reason.last_verdict  # Phase가 아닌 verdict 직접 참조

    if verdict == ReadinessVerdict.GENERATE:
        return "sql_generator"
    if verdict == ReadinessVerdict.EXPLORE:
        return "knowledge_fetcher" if reason.exploration_phase == "initial" else "recovery_agent"
    if verdict == ReadinessVerdict.REPLAN:
        return "recovery_agent"
    if verdict == ReadinessVerdict.ASK_USER:
        return "clarification_handler"
    return "result_finalizer"  # TERMINATE
```

### P1-2. knowledge_item 부분 일치 매칭의 복수 매칭 위험

**문제**: `_find_knowledge_item`(02-detailed-design.md 521-531행)의 부분 일치 fallback에서, 짧은 key가 복수 항목과 매칭될 수 있음.

**위험 예시 (금융 도메인)**:

```python
# 기존 knowledge_items
knowledge_items = [
    KnowledgeItem(key="여신실행일자", value="exec_dt", status=CONFIRMED),
    KnowledgeItem(key="기준일자", value="base_dt", status=CONFIRMED),
    KnowledgeItem(key="만기일자", value="mtr_dt", status=UNRESOLVED),
]

# LLM이 생성한 update
update = KnowledgeUpdate(
    key="일자",  # 70B 모델이 축약한 key
    new_status="CONFIRMED",
    evidence="TB_LOAN_INFO.loan_dt 확인",
    value="loan_dt",
)

# _find_knowledge_item 실행
for item in items:
    if "일자" in item.key or item.key in "일자":
        return item  # → "여신실행일자" 반환 (첫 번째 매칭!)

# 결과: "여신실행일자"의 value가 "loan_dt"로 오염됨
# 원래 의도: 새로운 "대출일자" 항목을 추가하려 했음
```

**해결안**: 복수 매칭 시 방어 로직 추가.

```python
def _find_knowledge_item(items: list[KnowledgeItem], key: str) -> KnowledgeItem | None:
    # 1. 정확 일치 (최우선)
    for item in items:
        if item.key == key:
            return item

    # 2. 부분 일치 — 복수 매칭 시 새 항목 생성으로 유도
    partial_matches = [
        item for item in items
        if key in item.key or item.key in key
    ]
    if len(partial_matches) == 1:
        return partial_matches[0]
    # 복수 매칭 또는 미매칭 → None 반환 → 새 항목 생성
    return None
```

### P1-3. `_is_valid_promotion`에서 CANDIDATE 상태 누락

**문제**: `PROMOTION_ORDER`(02-detailed-design.md 536-544행)에 `ConfidenceStatus.CANDIDATE`가 없음. 현행 코드베이스에는 CANDIDATE 상태가 존재(UNRESOLVED → CANDIDATE → PROBABLE → CONFIRMED).

```python
# 현행 설계의 PROMOTION_ORDER
PROMOTION_ORDER = {
    ConfidenceStatus.UNRESOLVED: 0,
    # CANDIDATE 누락! → .get(CANDIDATE, 0) = 0
    ConfidenceStatus.PROBABLE: 1,
    ConfidenceStatus.CONFIRMED: 2,
    ConfidenceStatus.CONFLICTED: 3,
}
```

**영향 예시**:

```python
# 기존 knowledge_item이 CANDIDATE 상태
item = KnowledgeItem(key="지점코드", status=ConfidenceStatus.CANDIDATE)

# LLM이 PROBABLE로 승격 요청
update = KnowledgeUpdate(key="지점코드", new_status="PROBABLE")

# _is_valid_promotion(CANDIDATE, PROBABLE) 실행
current_order = PROMOTION_ORDER.get(CANDIDATE, 0)  # → 0 (fallback)
target_order = PROMOTION_ORDER.get(PROBABLE, 0)     # → 1
return 1 >= 0  # True → 승격 허용

# 결과: 우연히 정상 동작하지만, 의도된 동작인지 불명확
# 역방향: CANDIDATE → UNRESOLVED는?
# PROMOTION_ORDER.get(UNRESOLVED, 0) = 0
# PROMOTION_ORDER.get(CANDIDATE, 0) = 0
# 0 >= 0 → True → 역행도 허용됨! ← 의도하지 않은 동작
```

**해결안**:

```python
PROMOTION_ORDER = {
    ConfidenceStatus.UNRESOLVED: 0,
    ConfidenceStatus.CANDIDATE: 1,   # 추가
    ConfidenceStatus.PROBABLE: 2,
    ConfidenceStatus.CONFIRMED: 3,
    ConfidenceStatus.CONFLICTED: 4,  # 항상 설정 가능
}
```

### P1-4. discovered_facts 갱신 경로 부재

**문제**: recovery_agent의 `_apply_knowledge_updates`는 `knowledge_items`만 갱신하고, `_apply_table_updates`는 `candidate_tables`만 갱신함. 현행 `knowledge_interpreter`의 `_apply_batch_insights`에서 수행하던 **`discovered_facts` 추가 경로가 recovery_agent에 없음**.

**영향**: `discovered_facts`는 sql_generator의 프롬프트에서 "발견된 사실" 섹션으로 제공됨. recovery_agent가 도구를 통해 새로운 사실을 발견해도 이 채널로 sql_generator에 전달되지 않음.

```
── 현행 흐름 ──
context_explorer Phase 3 (LLM 배치 해석):
  "TB_BRANCH_INFO는 일일 배치로 갱신되며, 폐쇄지점도 포함됨"
  → discovered_facts.append(위 내용)
  → sql_generator가 WHERE 조건에 "폐쇄여부 = 'N'" 추가

── 신규 recovery_agent 흐름 ──
recovery_agent LLM call:
  analysis: "TB_BRANCH_INFO 샘플을 확인한 결과 폐쇄지점도 포함됨"
  knowledge_updates: [{key: "지점테이블", new_status: "CONFIRMED", ...}]
  → discovered_facts에는 추가되지 않음!
  → sql_generator가 폐쇄지점 필터를 누락할 수 있음
```

**해결안**: recovery_agent의 ReAct 루프에서 `analysis`와 `lessons_learned`를 `discovered_facts`에 추가.

```python
# recovery_agent.py — ReAct 루프 내부
decision = await _recovery_step(reason, tool_results)

# analysis를 discovered_facts에 반영
if decision.analysis and decision.action in ("ready", "call_tools"):
    reason.discovered_facts.append(
        f"[recovery] {decision.analysis}"
    )
```

---

## [P2] 권장 — 품질/안정성 향상

### P2-1. 70B 모델에서의 ReAct 패턴 신뢰성 검증 부재

**문제**: recovery_agent의 핵심 가치는 "LLM이 도구 결과를 보고 다음 행동을 결정"하는 ReAct 패턴. Solar Pro 2 70B에서 이 패턴의 신뢰성에 대한 검증 계획이 부족함.

**구체적 우려**:

| 판단 | Claude Sonnet 4 | Solar Pro 2 70B | 위험도 |
|------|----------------|----------------|--------|
| `call_tools` → `ready` 전환 타이밍 | 정확 | 조기 ready 또는 무한 call_tools 가능 | 높음 |
| `analysis` 필드 품질 | 구체적 분석 | 모호하거나 반복적 분석 가능 | 중간 |
| `knowledge_updates` 정확성 | evidence 구체적 | evidence 누락 또는 환각 가능 | 높음 |
| `tool_calls.kwargs` 형식 | 스키마 준수 | key 변동 ("query" vs "keyword") | 높음 |

**해결안**: `degraded_mode` 옵션 추가.

```python
# config에서 모델별 설정
RECOVERY_MAX_ROUNDS = {
    "claude": 5,       # 충분한 ReAct 라운드 허용
    "solar_pro_2": 2,  # 제한적 ReAct (2라운드 후 강제 판정)
    "qwen3.5": 4,      # 중간 수준
}

# recovery_agent.py
max_internal_rounds = RECOVERY_MAX_ROUNDS.get(model_family, 3)

# 70B 모델 전용: 첫 라운드에서 ready가 아니면
# 2라운드 후 강제 ready 또는 기존 execution_plan 방식으로 fallback
if round_num >= max_internal_rounds - 1 and model_family == "solar_pro_2":
    decision.action = "ready"  # 현재까지 수집된 knowledge로 강제 진행
```

### P2-2. LLM 호출 효율 비교의 낙관적 기술

**문제**: 01-strategy.md 검토 1에서 "현행 recovery 경로 LLM 2회/cycle × 최대 3 cycle = 6회"라 기술. 실제로는 PENDING hypothesis가 남아있으면 recovery_planner에서 LLM 호출이 발생하지 않을 수 있음.

**현행 최선 경로 (PENDING hypothesis 존재 시)**:

```
recovery_planner: LLM 0회 (PENDING hypothesis 소비만)
context_explorer: LLM 1회 (배치 해석)
= cycle당 LLM 1회

최대 3 cycle = LLM 3회
```

**신규 recovery_agent (최선 경로)**:

```
LLM call 1: 상태 분석 + 도구 결정
LLM call 2: 결과 해석 + ready
= cycle당 LLM 2회 (최소)

1 cycle = LLM 2회
```

**비교 정리**:

| 시나리오 | 현행 LLM 호출 | 신규 LLM 호출 | 차이 |
|---------|-------------|-------------|------|
| PENDING 있음, 1 cycle 해소 | 1회 | 2회 | 신규 +1 |
| PENDING 없음, 1 cycle 해소 | 2회 | 2회 | 동등 |
| PENDING 없음, 2 cycle 필요 | 4회 | 3-4회 | 동등~개선 |
| 복합 공백 3개 해소 | 6회 (3 cycle) | 2-3회 (1 cycle) | **신규 우위** |

**결론**: 복합 공백 해소에서 신규 설계가 명확히 우위. 단일 공백에서는 약간 열위 또는 동등. 문서에 이 트레이드오프를 명시하는 것이 정확함.

### P2-3. 도구 실행 병렬화 P0 격상 권장

**문제**: `_execute_tools`(02-detailed-design.md 459-482행)에서 도구를 순차 실행. 검토 4에서 "독립적 공백은 한 라운드에 batch 처리"를 핵심 가치로 제시했지만, 순차 실행으로는 이 가치가 실현되지 않음.

```
── 순차 실행 (현재 설계) ──
search_table_meta("지점"): 200ms
search_code_meta("branch_type_cd"): 150ms
get_date_distribution("TB_LOAN_EXEC", "exec_dt"): 300ms
= 총 650ms

── 병렬 실행 (개선안) ──
asyncio.gather(
    search_table_meta("지점"),
    search_code_meta("branch_type_cd"),
    get_date_distribution("TB_LOAN_EXEC", "exec_dt"),
)
= 총 ~300ms (가장 느린 도구 기준)
```

**해결안**:

```python
async def _execute_tools(
    tool_calls: list[ToolCall],
    reason: ReasoningState,
) -> list[dict]:
    # 독립 도구들을 병렬 실행
    tasks = [_execute_single_tool(tc, reason) for tc in tool_calls]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    results = []
    for tc, raw in zip(tool_calls, raw_results):
        if isinstance(raw, Exception):
            results.append({"tool": tc.tool, "purpose": tc.purpose, "status": "error", "result": str(raw)})
        else:
            results.append({"tool": tc.tool, "purpose": tc.purpose, "status": "success", "result": raw})
        reason.loop_guard.increment_tool_calls()

    return results
```

현재 P2에 배치되어 있으나, recovery_agent의 효율성 제안과 직결되므로 **P1로 격상** 권장.

### P2-4. EXPLORE verdict에서 knowledge_fetcher 재진입의 실효성

**문제**: `_route_after_readiness_gate`(02-detailed-design.md 776-780행)에서 EXPLORE verdict + `exploration_phase == "initial"` 시 `knowledge_fetcher`로 재진입. 그러나 이전 실행에서 execution_plan의 모든 PENDING 스텝이 DONE으로 마킹되었다면, knowledge_fetcher는 **실행할 스텝이 없는 상태**.

```
── 시나리오 ──
knowledge_fetcher (1차): execution_plan의 3개 스텝 모두 실행 → 전부 DONE
knowledge_interpreter: 배치 해석 수행
readiness_gate: score 부족 → EXPLORE verdict

knowledge_fetcher (2차 재진입):
  _execute_pending_steps(reason)
  → PENDING 스텝 없음 → 아무것도 실행하지 않음
  _observe_all_date_distributions(candidate_tables)
  → 이미 관찰 완료 → 변동 없음
  → 결과: 상태 변화 없이 knowledge_interpreter로 전달

knowledge_interpreter (2차):
  → 이전과 동일한 입력 → 동일한 해석 결과
  → readiness_gate → 다시 EXPLORE → 무한 루프!
```

**해결안**: readiness_gate에서 EXPLORE verdict 시 실제로 수행할 추가 탐색이 있는지 확인하고, 없으면 GENERATE로 전환하거나 recovery_agent로 라우팅.

```python
# readiness_gate.py
if verdict == ReadinessVerdict.EXPLORE:
    pending_steps = [s for s in reason.execution_plan if s.status == StepStatus.PENDING]
    if reason.exploration_phase == "initial" and pending_steps:
        return  # knowledge_fetcher로 라우팅 (의미 있는 추가 탐색 가능)
    else:
        reason.exploration_phase = "recovery"
        verdict = ReadinessVerdict.REPLAN  # recovery_agent로 전환
```

---

## 라우팅 흐름 전체 요약 (수정 반영)

```
planner
  ├─ fast_path → sql_generator
  └─ else → knowledge_fetcher → knowledge_interpreter → readiness_gate
       ├─ GENERATE → sql_generator
       ├─ EXPLORE (PENDING 스텝 있음) → knowledge_fetcher (재진입)
       ├─ EXPLORE (PENDING 스텝 없음) → recovery_agent  [P2-4 수정]
       ├─ REPLAN → recovery_agent
       ├─ ASK_USER → clarification_handler
       └─ TERMINATE → result_finalizer

recovery_agent
  ├─ ready → sql_generator
  ├─ give_up + score ≥ threshold → sql_generator (force-generate) [P0-1 수정]
  ├─ give_up + score < threshold → result_finalizer (실패)      [P0-1 수정]
  └─ CONFLICTED 발견 → readiness_gate → ASK_USER → clarification_handler

sql_generator → sql_validator
  ├─ PASS → result_finalizer
  ├─ SYNTAX (retry 가능) → sql_generator
  ├─ SEMANTIC_LOCAL (fix 가능) → sql_generator
  ├─ SEMANTIC_LOCAL (fix 초과) → recovery_agent
  ├─ STRUCTURAL/EMPTY/DB_ERROR → recovery_agent
  └─ fast_path 실패 → knowledge_fetcher (exploration_phase=initial) [P0-2 수정]
```

---

## 구현 전 체크리스트 (우선순위 반영)

### P0 — 구현 전 설계 보완 필수

- [ ] P0-1: `recovery_gave_up` 플래그 추가, readiness_gate에서 무한 루프 차단 로직
- [ ] P0-2: planner_node에서 `exploration_phase = "initial"` 리셋 명시
- [ ] P0-3: `_execute_single_tool` 어댑터에서 쉼표 구분 형식 변환 구현

### P1 — 구현 초기 반영 권장

- [ ] P1-1: `last_verdict` 필드 추가, 라우팅에서 ReadinessVerdict 직접 참조
- [ ] P1-2: `_find_knowledge_item` 복수 매칭 시 None 반환 로직
- [ ] P1-3: `PROMOTION_ORDER`에 CANDIDATE 추가
- [ ] P1-4: recovery_agent의 `analysis`를 `discovered_facts`에 반영

### P2 — 품질 향상

- [ ] P2-1: 모델별 `max_internal_rounds` 설정 (degraded_mode)
- [ ] P2-2: LLM 호출 효율 트레이드오프 문서 보완
- [ ] P2-3: `_execute_tools` 병렬화 (`asyncio.gather`) — P1 격상 검토
- [ ] P2-4: EXPLORE verdict 재진입 시 실행 가능 스텝 존재 여부 검증
