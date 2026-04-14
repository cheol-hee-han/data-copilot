# 종료 통제 로직 단순화 리팩터링

> 작성일: 2026-04-10
> 상태: 설계 검토 중
> 선행 작업: 동일 실패 연속 반복 가드 버그 수정 완료 (recovery_agent.py:116-121, 155)
> 발단: GENERATION_FAILED 3회 연속 감지 후에도 48회까지 재시도한 무한 루프 버그

---

## 1. 배경

### 1-1. 긴급 수정 (완료)

동일 실패 유형이 `max_same_failure_repeats`(3)회 연속 반복되었음에도 루프가 종료되지 않는
버그를 2건 수정하였다.

**수정 A**: `recovery_agent.py:116-121`
- 동일 실패 연속 반복 가드에서 `_finalize_give_up()` 대신 직접 `Phase.DONE + FinalStatus.FAILURE` 설정
- 원인: `_finalize_give_up()`이 readiness score >= 0.55이면 `Phase.GENERATING`으로 번복하여
  "강제 종료"를 선언해놓고 실제로는 `sql_generator`로 재진입

**수정 B**: `recovery_agent.py:155`
- `should_terminate()` 조건에서 `plan_result` 유무 체크 제거
- 원인: LLM이 거의 항상 execution_plan을 생성하므로 `plan_result is None or not plan_result.execution_plan`
  조건이 사실상 항상 False → 가드 무력화

### 1-2. 구조적 문제

긴급 수정으로 무한 루프는 차단되었으나, 종료 통제 로직 자체가 **3개 파일, 5개 계층**에
분산되어 있어 유사 버그 재발 위험이 높다. 이 문서는 근본적인 단순화 방향을 정리한다.

---

## 2. 현황 분석

### 2-1. 종료 판단 계층 (현재 5개)

| # | 위치 | 메커니즘 | 종료 방식 |
|---|------|----------|-----------|
| A | `state.py:623` LoopGuard 4개 카운터 | `should_terminate()` | total_tool_calls>=40, replan>=10, generates>=5, local_fix>=5 |
| B | `recovery_agent.py:103-140` | `_count_consecutive_same_failure()` | 동일 실패 N회 연속 → 직접 DONE+FAILURE |
| C | `recovery_agent.py:155` | `should_terminate()` 재호출 | 한도 초과 → `_finalize_give_up()` |
| D | `recovery_agent.py:174` | LLM이 `give_up` 반환 | `_finalize_give_up()` |
| E | `confidence_scorer.py:88` + `readiness_gate.py:74` | `should_terminate()` → TERMINATE verdict | `_finalize_phase()` |

### 2-2. 종료 실행 함수 (현재 2가지)

| 함수 | 동작 | 호출 위치 |
|------|------|-----------|
| `_finalize_give_up()` | score >= 0.55 → **GENERATING** (재시도), 미만 → DONE+FAILURE | C, D 경로 |
| 직접 `Phase.DONE + FinalStatus.FAILURE` | 무조건 종료 | B 경로 (수정 후) |

### 2-3. force-generate 진입점 (현재 2개)

| 위치 | 조건 | `is_force_generated` 설정 |
|------|------|---------------------------|
| `readiness_gate.py:163-181` `_apply_force_generate()` | replan >= 3 AND score >= 0.55 AND explored_tables 존재 | **미설정** |
| `recovery_agent.py:957-971` `_finalize_give_up()` | score >= 0.55 | 설정 |

---

## 3. 검증된 문제점

아래 항목은 3개 분석 에이전트의 독립 조사 + 교차 검증을 거쳐 확인된 사항이다.

### 3-1. `_finalize_give_up()` 이중 역할 (확인됨)

- **문제**: 이름은 "give_up"이지만 score에 따라 `Phase.GENERATING`(재시도)을 설정할 수 있음
- **영향**: 호출자가 "이 경우엔 force-generate 불허"를 각자 판단해야 함 (B 경로의 긴급 수정이 그 예)
- **위치**: `recovery_agent.py:957-971`

### 3-2. `should_terminate()` 중복 호출 (확인됨)

- **호출 위치**: `confidence_scorer.py:88` (readiness_gate 경유), `recovery_agent.py:155`
- **의도**: recovery_agent에서는 LLM 호출 후 즉시 차단하는 early-exit 최적화
- **트레이드오프**: 제거하면 context_retriever 1회 추가 호출 발생, 유지하면 중복 판단점 존재
- **참고**: LLM 호출 전에 배치하면 "새 가설 생성 기회"를 잃음 (주석 lines 143-144)

### 3-3. `max_conflicted_bounces` 미사용 (확인됨)

- **정의**: `config.py:254` — `max_conflicted_bounces: int = 2`
- **검증**: 전체 코드베이스에서 config.py 외 참조 없음
- **영향**: CONFLICTED 항목의 왕복 반복을 제어할 가드가 없음

### 3-4. `generate_attempts` 카운터 명칭과 동작 불일치 (확인됨)

- **이름**: `generate_attempts` (시도 횟수)
- **실제**: `sql_generator.py:327`에서 성공 시에만 `increment_generate()` 호출
- **영향**: GENERATION_FAILED 시 카운터 미증가 → `should_terminate()`의 `generate_attempts >= 5` 조건 미작동
- **설계 의도**: 의도적으로 성공만 카운트 (실패는 `dead_ends`로 제어). 그러나 이름이 오해를 유발

### 3-5. force-generate 반복 시도 미제한 (확인됨)

- force-generate가 실패해도 score >= 0.55이면 다시 force-generate 시도 가능
- 연속 실패 가드(B)와 루프 가드(C)로 간접 차단되지만, force-generate 자체의 횟수 제한은 없음

### 3-6. force-generate 진입점 간 `is_force_generated` 설정 비대칭 (확인됨)

- `readiness_gate._apply_force_generate()`: 플래그 **미설정**
- `recovery_agent._finalize_give_up()`: 플래그 **설정**
- 동일한 "불완전한 상태에서 SQL 생성 시도"인데 추적 상태가 다름

### 검증에서 반박된 항목 (제외)

- ~~Phase.VERIFYING 체크가 dead code~~ → `state.reason.phase`(입력)과 `reason.phase`(수정본)을 구분하여 정상 동작
- ~~recovery_entry_source가 readiness_gate에서만 설정~~ → sql_generator, sql_validator에서도 설정 확인

---

## 4. 리팩터링 제안

### 4-1. A안: `_finalize_give_up()` 단일 책임 분리 (추천)

**변경 범위**: recovery_agent.py 내부만

```
현재:
  _finalize_give_up()  →  score에 따라 DONE 또는 GENERATING

변경 후:
  _finalize_give_up()           →  항상 DONE + FAILURE (이름 그대로 종료)
  _attempt_force_generate()     →  별도 함수, score 기반 force-generate 판단
```

적용 경로:

| 경로 | 현재 | 변경 후 |
|------|------|---------|
| B (동일 실패 연속) | 직접 DONE+FAILURE (긴급 수정) | `_finalize_give_up()` 호출 가능 (항상 종료) |
| C (루프 가드 한도) | `_finalize_give_up()` | `_attempt_force_generate()` |
| D (LLM give_up) | `_finalize_give_up()` | `_attempt_force_generate()` |

**장점**:
- B 경로에서 `_finalize_give_up()` 우회가 불필요해짐
- 각 경로의 의도가 함수명으로 명확히 표현됨
- 변경 범위가 recovery_agent.py 1개 파일

### 4-2. B안: force-generate 진입점 통합 (대규모)

readiness_gate로 force-generate 판단을 일원화:

```
recovery_agent → 항상 EXPLORING 또는 DONE만 반환
readiness_gate → force-generate 여부를 유일하게 판단, is_force_generated 설정 포함
```

**장점**: force-generate 조건이 한 곳에만 존재
**단점**: 그래프 엣지 변경 필요, recovery_agent에서 바로 종료하던 경로가 readiness_gate를 경유해야 함

### 4-3. 부수 개선 (A안과 독립 적용 가능)

| 항목 | 조치 | 우선순위 |
|------|------|----------|
| `max_conflicted_bounces` 미사용 | 구현 또는 config에서 제거 | 중 |
| `generate_attempts` 명칭 | `successful_generates`로 rename 또는 실패 시에도 증가 | 낮 (동작은 의도적) |
| `is_force_generated` 비대칭 | readiness_gate 경로에서도 플래그 설정 | 낮 |
| `should_terminate()` 중복 | recovery_agent 호출을 유지하되 주석에 "early-exit 최적화" 명시 | 낮 (현행 유지) |

---

## 5. 종료 통제 전체 흐름도 (현재)

```
                    ┌─────────────────────────────────┐
                    │        should_terminate()        │
                    │  (state.py:623)                  │
                    │  tool_calls>=40 OR replan>=10    │
                    │  OR generates>=5 OR FAILURE      │
                    │  OR 가설 소진                     │
                    └──────────┬──────────────────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
     confidence_scorer    recovery_agent    recovery_agent
      (readiness_gate)    line 155 (C)      line 103 (B)
      → TERMINATE         → _finalize_      → 직접 DONE
        verdict             give_up()         +FAILURE
              │                │
              ▼                ▼
         Phase.DONE      score >= 0.55?
                          ├─ Y → Phase.GENERATING (재시도)
                          └─ N → Phase.DONE (종료)
```

### 5-1. A안 적용 후 흐름도

```
                    ┌─────────────────────────────────┐
                    │        should_terminate()        │
                    │  (state.py:623)                  │
                    └──────────┬──────────────────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
     confidence_scorer    recovery_agent    recovery_agent
      (readiness_gate)    line C,D           line B
      → TERMINATE         → _attempt_       → _finalize_
        verdict             force_generate()   give_up()
              │                │                │
              ▼                ▼                ▼
         Phase.DONE      score >= 0.55?     항상 DONE
                          ├─ Y → GENERATING    +FAILURE
                          └─ N → _finalize_give_up()
                                    → 항상 DONE+FAILURE
```

---

## 6. 구현 체크리스트

- [ ] `_finalize_give_up()` → 항상 `Phase.DONE + FinalStatus.FAILURE` (score 분기 제거)
- [ ] `_attempt_force_generate()` 신설 (기존 score 분기 + `is_force_generated` 설정 이동)
- [ ] C 경로 (`should_terminate` 후): `_attempt_force_generate()` 호출
- [ ] D 경로 (LLM give_up): `_attempt_force_generate()` 호출
- [ ] B 경로 (동일 실패 연속): `_finalize_give_up()` 호출로 복원 (직접 DONE 설정 제거)
- [ ] `readiness_gate._apply_force_generate()` → `is_force_generated = True` 설정 추가
- [ ] `max_conflicted_bounces` 구현 여부 결정 (별도 이슈 가능)
- [ ] `generate_attempts` → `successful_generates` rename 검토 (별도 이슈 가능)
- [ ] 단위 테스트: 동일 실패 3회 → 종료 확인
- [ ] 단위 테스트: replan 10회 → 종료 확인 (force-generate 우회 불가)
- [ ] 단위 테스트: force-generate 실패 → 재시도 후 eventually 종료 확인

---

## 7. 참고: 종료 관련 설정값 일람

| 파라미터 | 값 | 위치 | 용도 | 실효성 |
|----------|-----|------|------|--------|
| `max_tool_calls` | 40 | config.py:247 | 도구 호출 총량 | 유효 |
| `max_replans` | 10 | config.py:248 | 재계획 최대 횟수 | 유효 |
| `max_generates` | 5 | config.py:249 | SQL 생성 성공 횟수 | 유효 (명칭 불일치) |
| `max_local_fixes` | 5 | config.py:250 | 로컬 문법 교정 | 유효 |
| `force_generate_after_replans` | 3 | config.py:251 | N회 replan 후 강제 생성 | 유효 |
| `max_same_failure_repeats` | 3 | config.py:255 | 동일 실패 연속 반복 | 유효 (수정 후) |
| `max_conflicted_bounces` | 2 | config.py:254 | CONFLICTED 왕복 | **미사용** |
