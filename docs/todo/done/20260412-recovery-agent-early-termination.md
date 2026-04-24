# Recovery Agent 조기 종료 분석 및 수정 설계

- 작성일: 2026-04-12
- 상태: 사전 확인 완료 / Patch 1 옵션 A 확정 / 미적용
- 관련 파일:
  - [src/agents/nodes/reason/recovery_agent.py](../../src/agents/nodes/reason/recovery_agent.py)
  - [src/agents/state/state.py](../../src/agents/state/state.py)
  - [resources/prompts/reason/recovery_agent_system.txt](../../resources/prompts/reason/recovery_agent_system.txt)
- 추적 로그: `logs/traces/trace_reasoning_20260412_anonymous_session-1775801643744_79817aa057a1.md`

## 1. 증상

사용자 질의 "작년말대비 여신 2배 이상 증가한 고객 중 주담대 보유 고객 목록 알려줘"에 대해
시스템이 **도구 호출 2회, 재계획 1회** 만에 아래 메시지로 조기 종료했다.

> 죄송합니다. 총 2회 도구 호출, 1회 재계획 시도 실패 경로: TERM_UNRESOLVABLE

`MAX_TOOL_CALLS=40`, `MAX_REPLANS=10`이므로 한도 기반 종료는 아니다.

## 2. Trace 증거

Trace 파일: `logs/traces/trace_reasoning_20260412_anonymous_session-1775801643744_79817aa057a1.md`

- 총 80.9s, 5 LLM 호출, 42,340 토큰
- 파이프라인 경로: intent_classifier → normalize_query → preparer → retriever →
  interpreter → readiness_gate → REPLAN → recovery_agent → result_finalizer → error_end
- readiness score: 0.35
- 탐색 테이블 10개, SELECTED 3개: `TB_ADW_LNB301M`, `TB_ADW_LNB333M`, `TB_ADW_CSC101M`

### 핵심 — recovery_agent LLM 출력의 자가모순 (trace lines 263~272)

```json
{
  "action": "give_up",
  "new_hypothesis": { "...여신 이력 테이블 중심 재탐색..." },
  "new_plan": [
    {"tool": "get_date_distribution", "args": {"table": "TB_ADW_LNB341P"}},
    {"tool": "get_date_distribution", "args": {"table": "TB_ADW_CSC102H"}},
    {"tool": "search_use_cases", "args": {...}}
  ]
}
```

LLM은 **새 가설과 3스텝 실행 계획을 동시에 제시하면서도 action=give_up**을 냈다.
내부 사고 요약(lessons)에는 "여신 잔액 증감 분석을 위해서는 이력(H)/스냅샷(P) 테이블의
적재 주기와 기준일자 컬럼을 우선 확인해야 함"이 포함되어 있어, 모델은 다음 단계를
분명히 알고 있었다.

## 3. 근본 원인 4가지

### (A) 프롬프트 — `action=give_up`과 `new_hypothesis` 공존 허용

[resources/prompts/reason/recovery_agent_system.txt](../../resources/prompts/reason/recovery_agent_system.txt)
의 OUTPUT_CONTRACT가 "give_up이면 execution_plan=[]" 제약은 두었으나,
`new_hypothesis`가 존재할 때 give_up을 금지하는 하드 제약이 없다. Qwen3.5는 "give_up
=실패 인정"과 "new_hypothesis=재도전"을 병렬 생성 가능한 필드로 해석한다.

### (B) 코드 — should_terminate 체크가 new_hypothesis 반영 전에 실행

[src/agents/nodes/reason/recovery_agent.py:149-200](../../src/agents/nodes/reason/recovery_agent.py#L149-L200):

```python
plan_result, full_variables = await _build_recovery_plan(...)
if should_terminate(reason):                           # line ~159
    _finalize_give_up(reason)                          # ← 여기서 조기 종료
    ...
if plan_result is None or plan_result.action == "give_up":
    _finalize_give_up(reason)
reason.execution_plan = plan_result.execution_plan     # line ~200 (너무 늦음)
```

그리고 [src/agents/state/state.py:673-691](../../src/agents/state/state.py#L673-L691):

```python
def should_terminate(reason: ReasoningState) -> bool:
    ...
    return (
        g.total_tool_calls >= MAX_TOOL_CALLS
        or g.replan_count >= MAX_REPLANS
        or g.generate_attempts >= MAX_GENERATES
        or reason.final_status == FinalStatus.FAILURE
        or (len(pending) == 0 and reason.current_hypothesis is None)
    )
```

문제: LLM이 `new_hypothesis`를 제시해도 state에 반영되기 전 시점이라
`len(pending)==0 and current_hypothesis is None` 조건이 **true로 잘못 평가**되어
종료가 트리거된다. hypothesis exhaustion이 진짜 이유다.

### (C) 로그 — 종료 사유가 잘못 표시

현재 라벨: `routing_reason="루프 가드 한도 초과 → 종료"`

실제 트리거는 (B)의 hypothesis exhaustion. 루프 가드 한도(MAX_TOOL_CALLS/REPLANS)와
무관한 상황에서 이 라벨이 찍혀 디버깅을 오도한다.

### (D) _handle_hypothesis_transition이 pending 큐를 채우지 않음

hypothesis 전이 로직이 `new_hypothesis`를 `current_hypothesis`로만 승격시키고
pending 큐는 비워둔다. 이로 인해 (B)의 조건이 다시 참이 된다.

## 4. 수정 방향

### Patch 1 (프롬프트, A 대응) — 옵션 A 확정

#### 사전 확인 결과 (2026-04-12 실파일 기준)

`resources/prompts/reason/recovery_agent_system.txt`를 읽고 확정한 사실:

1. **`[HARD_CONSTRAINTS]` 블록은 존재하지 않는다.** 현재 파일은
   `[ROLE]` → `[RULES]` → `[TOOLS]` → `[HALLUCINATION_GUARD]` → `[EXAMPLES]` →
   `[OUTPUT_CONTRACT]` → `[CONTEXT]` → `[TASK]` 구성.
   따라서 "HARD_CONSTRAINTS에 규칙 추가" 전제 자체가 성립하지 않는다.
2. **`reasoning_summary` 필드는 이미 `[OUTPUT_CONTRACT]`에 존재**하며,
   예시 5건 전부 이 필드를 포함한다 (§5.2 적용 완료 상태).
3. **[RULES] give_up 원칙 블록**([recovery_agent_system.txt:47-53](../../resources/prompts/reason/recovery_agent_system.txt#L47-L53))에
   "give_up 시 execution_plan=[], new_hypothesis=null" 강제가 이미 포함되어 있다.
   단, **역방향 강제**("new_hypothesis 채워짐 → replan")는 없어 trace의 자가모순을 막지 못한다.
4. **Few-shot 학습 신호 비율 (replan : give_up) = 4 : 1**
   - EX1 (코드값 불명, readiness_gate), EX2 (포맷 불일치, sql_executor),
     EX3 (필터값 불명, readiness_gate), EX5 (산출식 불명, readiness_gate):
     `action=replan` + `new_hypothesis` 객체 + 1~3 스텝 `execution_plan` 매핑
   - EX4 (탐색 경로 소진): `action=give_up` + `new_hypothesis=null` + `execution_plan=[]` 매핑
   - **기준 "0~1건이면 예시 교체 필요" 미충족** → 예시 교체 불필요
5. **`analysis` 필드가 이미 존재**하여 "진입 경로 + 실패 원인" 서술을 담당한다.

#### 옵션 B 기각 사유 (§0 성능 우선 관점 재평가)

기존 문서는 §5.4 scaffolding(`surface_symptom` + `root_cause_diagnosis` 필드 분리)을
권장했으나, 사전 확인 결과 다음 이유로 기각한다.

- **§3.12 위반**: 옵션 B의 "root_cause_diagnosis가 채워져 있으면 action=replan" 규칙은
  코드로 정적 탐지 불가한 **의미 판단 기준**이므로 HARD_CONSTRAINTS가 아닌 [RULES]에
  있어야 한다. 그러나 옵션 B는 이를 HARD_CONSTRAINTS로 제안 → 블록 책임 분리 위반.
- **HARD_CONSTRAINTS 블록 부재**: 옵션 B는 "HARD 강제력"을 전제했으나 해당 블록 자체가
  없다. 신설 시 [RULES]의 give_up 원칙과 의미 판단이 중복 분산된다.
- **`analysis` 필드와 의미 중복**: `surface_symptom`은 기존 `analysis`의 "진입 경로 +
  실패 원인 분석" 서술과 내용이 거의 겹친다. 두 필드 공존 시 모델이 둘 중 어느 쪽에
  정보를 분산 배치해야 할지 혼동하여 `analysis` 품질이 떨어질 수 있다.
- **Few-shot 리라이트 비용/리스크**: 예시 5건 전부 2개 필드 추가 리라이트 필요(+80~120줄
  추정). §5.4 보완 규칙 "예시에서 변환 노출"을 5건 모두 주입해야 효과 발현. 리라이트
  품질 실패 시 기존 학습 신호(4:1)가 오히려 약화될 리스크.
- **근본 원인은 코드 계층**: trace의 자가모순은 Patch 2(`should_terminate`가
  `new_hypothesis` state 반영 전에 실행)가 **구조적 원인**이다. 이 수정이 들어가면 LLM이
  자가모순 출력을 내더라도 상위 루프에서 복구된다. 프롬프트 패치는 2차 방어로 충분하며,
  §5.4 같은 구조적 처방은 과잉(§0 "방어 과잉" 경계).

#### 옵션 A 최종안 — give_up 원칙 블록에 역방향 강제 2줄 추가

**위치**: [recovery_agent_system.txt:47-53](../../resources/prompts/reason/recovery_agent_system.txt#L47-L53)
`## give_up 원칙` 블록의 마지막 줄("give_up 시 execution_plan은 빈 배열([]), new_hypothesis는 null") **바로 아래**.

**추가 문구**:

```text
- new_hypothesis에 객체가 채워져 있으면 replan을 선택한다.
- execution_plan에 1개 이상의 스텝이 있으면 replan을 선택한다.
```

**이 문구가 LLM에 이해 쉬운 이유**:

- **필드명을 직접 언급** (`new_hypothesis`, `execution_plan`, `replan`):
  모델이 출력 JSON의 실제 필드와 1:1 매핑 가능. "신호", "후보" 같은 메타포를 제거하여
  §3.11 자기설명 용어 원칙 준수.
- **조건-결과 매핑 형식** (§3.5): "~이면 ~을 선택한다" 구조로 산문 없이 명료.
- **positive form 엄격 적용** (§3.7): "null이 아니면"·"빈 배열이 아니면" 같은 부정
  조건을 피하고, "객체가 채워져 있으면"·"1개 이상의 스텝이 있으면" 같은 긍정 조건으로
  서술. 부정 토큰("아니면")을 priming하지 않아 판단 분기가 더 안정적.
- **기존 give_up 원칙 블록 문체와 일치**: 기존 "give_up을 선택한다"의 동사 선언 스타일을
  그대로 계승해 "replan을 선택한다"로 맞춤. 블록 내 리듬이 깨지지 않음.
- **양방향 구속 성립**: 기존 규칙("give_up → []/null") + 신규 규칙("객체 채워짐/스텝 1개
  이상 → replan") 두 방향이 함께 있어 trace의 자가모순("give_up + new_hypothesis 객체 +
  3스텝 new_plan")은 어느 방향으로도 규칙 위반이 되어 모델이 선택 시점에 피할 확률이 상승.

**성능 저해 점검**:

- 복구 계획 적절성(primary KPI)에 미치는 영향: **없음**. 추가 2줄은 give_up 선택 조건에만
  개입하며, replan 시 생성되는 execution_plan·new_hypothesis 품질에는 직접 관여하지
  않는다. 오히려 자가모순 출력을 차단하여 Patch 2(코드 순서)와 함께 작동하면 복구 성공률이
  올라간다.
- 토큰/라우팅 부담: +2줄 (~40 토큰). MoE 라우팅 드롭 리스크 무시 가능.
- few-shot 신호와의 정합성: 기존 EX1·2·3·5 (replan + new_hypothesis 있음)와 EX4 (give_up +
  null) 모두 신규 규칙과 일치. 회귀 리스크 없음.

### Patch 2 (코드, B·D 대응) — recovery_agent.py 순서 재배치

- `_build_recovery_plan` 결과의 `new_hypothesis`를 **먼저 state에 반영**
  (`_handle_hypothesis_transition` 호출 + pending 큐 append)
- 그 후에 `should_terminate(reason)` 평가
- `plan_result.action == "replan"`이면 execution_plan도 즉시 주입

```python
plan_result, full_variables = await _build_recovery_plan(...)

if plan_result and plan_result.new_hypothesis:
    _apply_new_hypothesis(reason, plan_result.new_hypothesis)  # pending에 push

if plan_result and plan_result.action == "replan" and plan_result.execution_plan:
    reason.execution_plan = plan_result.execution_plan
    return _build_replan_result(...)

if should_terminate(reason):
    _finalize_give_up(reason, routing_reason=_diagnose_termination(reason))
    ...
```

### Patch 3 (로그, C 대응)

종료 사유를 계산하는 `_diagnose_termination(reason)` 도입:

```python
def _diagnose_termination(reason) -> str:
    g = reason.loop_guard
    if g.total_tool_calls >= MAX_TOOL_CALLS:
        return f"도구 호출 한도 초과({g.total_tool_calls}/{MAX_TOOL_CALLS})"
    if g.replan_count >= MAX_REPLANS:
        return f"재계획 한도 초과({g.replan_count}/{MAX_REPLANS})"
    if g.generate_attempts >= MAX_GENERATES:
        return f"SQL 생성 한도 초과({g.generate_attempts}/{MAX_GENERATES})"
    if reason.final_status == FinalStatus.FAILURE:
        return "최종 실패 상태"
    if len(reason.get_pending_hypotheses()) == 0 and reason.current_hypothesis is None:
        return "가설 소진"
    return "알 수 없는 종료"
```

### Patch 4 (state, D 대응) — Patch 2에 흡수됨

**원안**: `_handle_hypothesis_transition`에서 `new_hypothesis`를 `current_hypothesis`로
승격하되, 이전 `current_hypothesis`를 `dead_ends`로 옮기고 새 가설을 pending에 append.

**흡수 사유** (2026-04-12 재검토):

- `_handle_hypothesis_transition`은 recovery_agent 진입 시점
  ([recovery_agent.py:89](../../src/agents/nodes/reason/recovery_agent.py#L89))에
  호출되므로 이 시점에 LLM은 아직 호출되지 않았고 `new_hypothesis`가 존재하지 않는다.
  원안의 함수 수정 위치 자체가 호출 흐름과 불일치.
- 기존 `_handle_hypothesis_transition`은 이미 "current→FAILED→dead_ends, pending→current
  승격"을 수행한다. 원안이 요구하는 "이전 것을 dead_ends로"는 이미 구현된 상태.
- 원안의 본질적 목적("새 가설을 state에 반영해 should_terminate의 exhaustion
  false positive를 차단")은 Patch 2에서
  `reason.current_hypothesis = plan_result.new_hypothesis`를 `should_terminate` 평가
  이전으로 이동한 한 줄로 달성된다.
- 따라서 Patch 4는 Patch 2에 **흡수**되며 별도 작업 없음.

## 5. 검증 시나리오

- 동일 쿼리로 재실행 → recovery_agent가 replan을 수행하고 get_date_distribution 2회
  + search_use_cases 1회가 실제 실행되어야 함
- MAX_REPLANS 근처 종료 시 로그 라벨이 "재계획 한도 초과(10/10)"로 표기되어야 함
- give_up 출력 케이스(new_hypothesis=null)도 기존처럼 정상 종료되어야 함
