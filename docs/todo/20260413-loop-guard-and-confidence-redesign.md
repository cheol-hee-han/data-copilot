# LoopGuard 카운터 재설계 + Validator 신뢰도 도입

> 작성일: 2026-04-13
> 상태: 이슈 1 구현 완료 / 이슈 2 설계 완료, 구현 대기

## 개요

에이전틱 추론 루프의 카운터 구조(`LoopGuard`)와 결과 신뢰도 산출 방식에 두 가지 설계 문제가 있다.

1. **LoopGuard 카운터가 가설 전환 시 리셋되지 않음** — `local_fix_count`가 전체 턴에 걸쳐 누적되어, 가설 B가 가설 A의 예산을 공유함
2. **신뢰도가 카운터 휴리스틱으로 산출됨** — `insight_builder._assess_confidence`가 replan/generate 횟수로 "높음/보통/낮음"을 하드코딩. LLM의 의미 검증 결과를 활용하지 않음

---

## 이슈 1: Validator confidence_score 도입 (구현 완료)

### 문제

`insight_builder._assess_confidence`가 `LoopGuard` 카운터(replan_count, generate_attempts)로 신뢰도를 산출:

```python
# AS-IS (삭제됨)
if replans == 0 and gen_attempts <= 1:
    return "높음"
if replans <= 1 and gen_attempts <= 2:
    return "보통"
return "낮음"
```

- replan 횟수와 결과 신뢰도 사이에 직접적 상관관계가 없음
- 한 번에 완벽한 SQL이 나와도 결과 데이터 자체가 불확실할 수 있음
- replan 2번 했어도 최종 결과가 정확할 수 있음
- validator(Layer2b)가 이미 9개 체크 + validation_summary를 출력하면서 의미 검증을 수행하고 있으나, 이 결과가 신뢰도에 반영되지 않음

### 설계

Layer2b LLM 응답에 `confidence_score`(0.0~1.0) 필드를 추가하여, validator가 검증 총평 작성 직후 신뢰도를 직접 산출하도록 변경.

```
[데이터 흐름]
Layer2b LLM 응답 {"confidence_score": 0.92, ...}
  → _validate_layer2b 반환 dict에 포함
  → validate_sql / _build_layer2b_failure에서 파싱
  → reason.confidence_score에 저장
  → insight_builder._assess_confidence에서 읽어 "높음 (0.92)" 형태로 변환
```

#### 프롬프트 산출 기준 (sql_validator_system.txt에 추가)

```
판단 기준:
- 사용자 질의 의도와 SQL 로직의 부합 정도
- checks 통과/실패 항목의 심각도와 맥락
- DB 실행 결과의 신뢰성
- fix_instruction으로 교정 가능한 수준인지 여부

verdict가 PASS이고 모든 체크가 통과하면 0.9 이상이다.
verdict가 FAIL이어도 교정 가능한 경미한 문제면 0.7 이상일 수 있다.
```

고정 감점표를 두지 않고 LLM이 총평 맥락에서 종합 판단하도록 설계. 실패를 반복하더라도 SQL이 더 정교해질 수 있으므로 기계적 감점은 부적절.

#### 출력 형식 변경

```json
{
  "validation_summary": "(검증 총평)",
  "confidence_score": 0.92,
  "fix_instruction": "..."
}
```

#### insight_builder 변환 로직

```python
# TO-BE
score = reason.confidence_score
if score >= 0.8: label = "높음"
elif score >= 0.5: label = "보통"
else: label = "낮음"
return f"{label} ({score:.2f})"  # "높음 (0.92)"
```

- score == 0.0 (Layer2b 미실행/비활성): "보통" 폴백

### 변경 파일

| 파일 | 변경 |
|---|---|
| `resources/prompts/reason/sql_validator_system.txt` | OUTPUT_CONTRACT에 confidence_score 스키마/산출기준 추가, 예시 6개 반영 |
| `src/agents/state/state.py` | `ReasoningState.confidence_score: float = 0.0` 필드 추가 |
| `src/agents/nodes/reason/sql_validator.py` | `_validate_layer2b` 반환 dict에 score 포함 + PASS/FAIL 양쪽에서 파싱 |
| `src/services/insight_builder.py` | `_assess_confidence` → confidence_score 기반으로 교체 |
| `tests/auto/unit/test_insight_builder.py` | 신뢰도 테스트 7개 → score 기반으로 재작성 |

### 리뷰 지적사항

#### [LOW] LLM 예외 시 confidence_score 폴백 (sql_validator.py:728-734)

`_validate_layer2b`의 except 블록 반환 dict에 `confidence_score` 키가 없어, `_build_layer2b_failure`에서 0.0 → insight_builder에서 "보통"으로 폴백된다. "LLM 실행 실패"인데 "보통"은 의미적으로 부정확.

실질 영향은 제한적: LLM 실패 시 structural → recovery → replan 경로를 타므로 이후 validator 재통과 시 score가 덮어써진다. 최종 실패 종료 시에만 "보통"이 노출되나, 그때는 실패 내러티브가 주 메시지.

후속 조치: except 블록에 `"confidence_score": 0.0` 명시 추가 + insight_builder에서 score == 0.0이면서 validation 실패인 경우를 구분하는 로직 검토.

#### [LOW] float() 변환 방어 (sql_validator.py:165, 390)

LLM이 `confidence_score`를 문자열(`"high"`)이나 범위 초과값(`1.5`)으로 출력할 경우 `float()` 변환에서 ValueError 발생 가능. 현재 상위의 `llm_call_with_parse_retry`가 JSON 파싱 실패를 재시도하므로 발생 확률은 낮으나, 방어 코드(`try/except` 또는 `min(max(...), 1.0)`) 추가 권장.

#### [INFO] 프롬프트 0.5 미만 부여 조건 미명시

산출 기준에 상한(0.9 이상)만 있고 하한(0.5 미만) 조건이 없어 LLM이 0.6~0.7에 몰릴 가능성. 예시 3(0.4)이 유일한 저점 참조. 실 운영 데이터 기반으로 분포를 확인 후 필요시 기준 보강.

### 후속 검토 사항

- **결과 신뢰도 vs SQL 신뢰도**: validator의 confidence_score는 "이 SQL이 얼마나 신뢰할 수 있는가"이다. "이 결과가 사용자 질문에 맞는 답인가"는 analyzer 영역이므로, 향후 analyzer에도 신뢰도를 도입하면 2레벨 신뢰도 체계를 구성할 수 있다.

---

## 이슈 2: local_fix_count 가설별 리셋 + MAX_GENERATES 비활성화 (구현 대기)

### 문제

`LoopGuard`의 `local_fix_count`가 전체 턴에 걸쳐 누적되어, 가설을 전환해도 이전 가설이 소비한 예산이 공유됨.

```
현재 동작:
  가설 A: local_fix 4회 → 한도 도달 → 가설 전환
  가설 B: local_fix 1회 (한도 5에 도달!) → 종료
  → 가설 B는 실질적으로 1회밖에 시도하지 못함
```

또한, `pipeline.py`의 SQL_SYNTAX/SQL_SEMANTIC_LOCAL 라우팅이 `generate_attempts < MAX_GENERATES` 조건을 사용하는데, `generate_attempts`는 전체 턴에 걸친 sql_generator 진입 횟수로 가설별 의미가 없다. `MAX_GENERATES = 0`으로 비활성화하고, 가설 내 반복 제어는 `local_fix_count`에 위임한다.

### 현재 카운터 분석

| 카운터 | 위치 | 세는 것 | 역할 |
|---|---|---|---|
| `total_tool_calls` | context_retriever | 외부 데이터소스 호출 총 횟수 | 글로벌 비용 상한 |
| `replan_count` | recovery_agent | 가설 전환(재계획) 횟수 | 글로벌 가설 상한 |
| `generate_attempts` | sql_generator | sql_generator 노드 진입 횟수 | **관찰 전용** (아래 참조) |
| `local_fix_count` | sql_validator | validator → generator 재시도 횟수 | **가설 내 반복 제한** |

### 핵심 결정: MAX_GENERATES = 0 으로 비활성화

`generate_attempts`와 `MAX_GENERATES`를 삭제하지 않고, **`MAX_GENERATES = 0`을 "무제한" 시맨틱으로 정의**하여 제어를 비활성화한다. 기존 조건에 `MAX_GENERATES > 0` 가드를 추가하면, 값이 0일 때 해당 조건이 건너뛰어져 사실상 `local_fix_count` 단독 제어가 된다.

**이 방식의 장점:**
- 기존 코드 구조(조건 분기, import, 상수)를 유지하므로 변경 범위 최소
- 향후 글로벌 생성 상한이 필요해지면 `max_generates`를 0이 아닌 값으로 되돌리기만 하면 됨
- `generate_attempts` 카운터, `increment_generate()`, runner.py/tracker의 소비 로직이 그대로 유지

**비활성화 이유:**
- `generate_attempts`는 글로벌 누적이므로 가설 B가 가설 A의 예산을 공유하는 문제 발생
- `local_fix_count`(가설별 리셋) + `replan_count`(글로벌) + `total_tool_calls`(글로벌)가 무한 루프를 이미 3중 방어
- 글로벌 생성 상한은 현재 불필요하나, 삭제보다 비활성화가 안전

### TO-BE 설계

#### 카운터 구조

| 카운터 | 범위 | 역할 | 변경 |
|---|---|---|---|
| `local_fix_count` | **가설별 리셋** | 가설 내 validator→generator 재시도 제한 | 가설 전환 시 0으로 리셋 |
| `replan_count` | 글로벌 | 가설 전환 횟수 제한 | 변경 없음 |
| `total_tool_calls` | 글로벌 | 외부 호출 비용 상한 | 변경 없음 |
| `generate_attempts` | 글로벌 | SQL 생성 총 횟수 추적 (관찰 전용) | `MAX_GENERATES = 0`으로 비활성화, 카운터 자체는 유지 |

#### 가설 전환 시 리셋

`recovery_agent._handle_hypothesis_transition()`에서 `local_fix_count = 0` 리셋 추가:

```python
def _handle_hypothesis_transition(reason: ReasoningState) -> None:
    # ... 기존 가설 FAILED 전환 + PENDING 소비 로직 ...

    # 새 가설에 대한 local_fix 예산 초기화
    loop_guard = reason.loop_guard.model_copy()
    loop_guard.local_fix_count = 0
    reason.loop_guard = loop_guard
```

#### config.py 변경

```python
# AS-IS
max_generates: int = 5

# TO-BE
max_generates: int = 0  # 0 = 무제한 (local_fix_count + replan_count로 제어)
```

#### 라우팅 변경 (pipeline.py)

```python
# AS-IS
case FailureType.SQL_SYNTAX:
    if state.reason.loop_guard.generate_attempts < MAX_GENERATES:
        return "fix_syntax"
    return "conclude_failure"

case FailureType.SQL_SEMANTIC_LOCAL:
    lg = state.reason.loop_guard
    if lg.should_escalate_to_structural():
        return "replan"
    if lg.generate_attempts < MAX_GENERATES:
        return "fix_local"
    return "conclude_failure"

# TO-BE
case FailureType.SQL_SYNTAX:
    lg = state.reason.loop_guard
    if lg.should_escalate_to_structural():
        return "replan"
    if MAX_GENERATES > 0 and lg.generate_attempts >= MAX_GENERATES:
        return "conclude_failure"
    return "fix_syntax"

case FailureType.SQL_SEMANTIC_LOCAL:
    lg = state.reason.loop_guard
    if lg.should_escalate_to_structural():
        return "replan"
    if MAX_GENERATES > 0 and lg.generate_attempts >= MAX_GENERATES:
        return "conclude_failure"
    return "fix_local"
```

- `MAX_GENERATES > 0` 가드 추가: 0이면 조건이 건너뛰어져 `local_fix_count` 단독 제어
- SQL_SYNTAX도 `should_escalate_to_structural()` 우선 체크 추가 (기존에는 없었음)
- `MAX_GENERATES > 0`일 때는 기존처럼 글로벌 상한 작동

#### should_terminate() 변경 (state.py)

```python
# AS-IS
if lg.generate_attempts >= MAX_GENERATES:
    return True, "SQL 생성 최대 횟수 도달"

# TO-BE
if MAX_GENERATES > 0 and lg.generate_attempts >= MAX_GENERATES:
    return True, "SQL 생성 최대 횟수 도달"
```

#### _infer_trace_routing 변경 (sql_validator.py)

```python
# AS-IS
if generate_attempts >= MAX_GENERATES:
    inferred_routing = "conclude_failure"

# TO-BE
if MAX_GENERATES > 0 and generate_attempts >= MAX_GENERATES:
    inferred_routing = "conclude_failure"
```

### 무한 루프 방어 검증

```
가설 A:
  generator → validator → local_fix +1 → generator → ... (최대 5회)
  → local_fix 한도 → replan (replan_count +1)

가설 B: (local_fix_count 리셋)
  generator → validator → local_fix +1 → ... (최대 5회)
  → local_fix 한도 → replan (replan_count +1)

... replan 10회 도달 → should_terminate() → 종료
```

최악의 경우: 가설 10개 x local_fix 5회 = generator 50회 진입. `replan_count`(10) 가드레일로 제어됨.

### 변경 파일 목록

#### 코드 (5개 파일)

| 파일 | 변경 | 상세 |
|---|---|---|
| `src/config.py` | 수정 | `max_generates` 기본값 `5` → `0` (0 = 무제한) |
| `src/agents/graph/pipeline.py` | 수정 | SQL_SYNTAX/SQL_SEMANTIC_LOCAL 라우팅에 `MAX_GENERATES > 0` 가드 추가 + SQL_SYNTAX에 `should_escalate_to_structural()` 우선 체크 추가 |
| `src/agents/state/state.py` | 수정 | `should_terminate()`에 `MAX_GENERATES > 0` 가드 추가 |
| `src/agents/nodes/reason/recovery_agent.py` | 수정 | `_handle_hypothesis_transition`에서 `local_fix_count = 0` 리셋 추가 |
| `src/agents/nodes/reason/sql_validator.py` | 수정 | `_infer_trace_routing`에 `MAX_GENERATES > 0` 가드 추가 |

#### 변경하지 않는 파일

| 파일 | 사유 |
|---|---|
| `src/agents/state/state.py` (LoopGuard) | `generate_attempts` 필드, `increment_generate()`, `MAX_GENERATES` 상수 유지 |
| `src/agents/nodes/reason/sql_generator.py` | `increment_generate()` 호출 유지 |
| `src/agents/graph/runner.py` | `retry_count: generate_attempts` 유지 (전체 생성 횟수 추적) |
| `src/agents/nodes/reason/result_finalizer.py` | 요약 출력의 `generate_attempts` 참조 유지 |
| `src/utils/tracker/*` | `retry_count` 소비 로직 유지 |

#### 테스트 (2개 파일)

| 파일 | 변경 |
|---|---|
| `tests/auto/unit/test_state_helpers.py` | `should_terminate`의 `generate_attempts` 조건 테스트 수정 |
| `tests/auto/unit/test_pipeline_routing.py` | 라우팅 조건 변경 반영 (SQL_SYNTAX/SQL_SEMANTIC_LOCAL 케이스) |

#### 문서 (2개 파일)

| 파일 | 변경 |
|---|---|
| `docs/architecture/architecture.md` | LoopGuard 카운터 역할 설명 업데이트 (generate_attempts: 관찰 전용 명시) |
| `docs/architecture/pipeline-architecture.md` | 종료 조건/라우팅 테이블에서 generate_attempts 조건 제거 |

### 리뷰 지적사항

#### [WARNING] SQL_SYNTAX → replan 시 순수 문법 오류 반복 비용

SQL_SYNTAX가 테이블/컬럼 문제가 아닌 순수 dialect 문법 오류인 경우, replan해도 동일 오류 반복 가능. `replan_count` 가드레일로 최종 방어되고, dead_ends에 SQL_SYNTAX가 누적되면 recovery LLM이 give_up할 가능성이 높아 실질적 위험은 낮으나, 불필요한 LLM 호출 비용 발생 가능.

#### [INFO] 무한 루프 방어 유효성 확인됨

SQL_SYNTAX 경로에서 `sql_validator.py:92`의 `increment_local_fix()`가 확실히 호출되므로 `local_fix_count` 단독으로 무한 루프 방어 가능. Layer2a/2b 실패 경로에서도 동일하게 동작 확인.

#### [INFO] local_fix_count 리셋 위치 적절성 확인됨

`_handle_hypothesis_transition`은 `recovery_agent_node`에서만 호출되며, 이것이 유일한 가설 전환 경로. LLM이 `new_hypothesis`를 생성하는 경로(recovery_agent.py:121-130)도 `_handle_hypothesis_transition` 이후에 실행되므로 리셋이 적용됨.

### 주의 사항

1. **`force_generate_after_replans`**: `generate_attempts`와 무관. `replan_count` 기반이라 변경 불필요
2. **`MAX_GENERATES = 0` 시맨틱**: 0은 "무제한"을 의미. 향후 글로벌 상한이 필요하면 양수로 되돌리기만 하면 됨. config.py 주석에 이 시맨틱을 명시할 것
