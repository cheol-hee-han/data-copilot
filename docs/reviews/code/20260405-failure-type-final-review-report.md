# FailureType 체계 재설계 — 전체 구현 최종 리뷰

> 리뷰 대상: FailureType 재설계 9개 파일 전체 변경 (Phase 1~5)
> 리뷰어: code-reviewer agent
> 리뷰일: 2026-04-05
> 설계서: `docs/todo/20260405-failure-type-redesign.md`

---

## 리뷰 요약

| 등급 | 건수 |
|------|------|
| Critical | 2 |
| Warning | 4 |
| Info | 3 |

전체적으로 설계서에 명시된 9개 변경 포인트가 정확하게 구현되었으며, NO_USE_CASE는 src/, resources/ 전체에서 완전히 제거되었다. GENERATION_FAILED 경로(sql_generator -> pipeline replan -> recovery_agent)의 전체 흐름도 논리적으로 정합하다. 아래에 식별된 개선점을 등급별로 정리한다.

---

## Critical (2건)

### [C-01] pipeline.py `_route_after_sql_validator`에서 state mutation — LangGraph 순수 함수 원칙 위반

**파일:** `src/agents/graph/pipeline.py:247-249`, `231-232`

```python
# 247-249행
state.reason.recovery_entry_source = (
    "sql_validator"
)
# 231-232행 (SQL_SEMANTIC_LOCAL 경로)
state.reason.recovery_entry_source = (
    "sql_validator"
)
```

**문제:** LangGraph의 조건부 엣지 함수는 순수 함수(state를 읽기만 하고 변경하지 않음)여야 한다. `_route_after_sql_validator`에서 `state.reason.recovery_entry_source`를 직접 변경하고 있다. 이는 다음 위험을 수반한다:
- LangGraph 체크포인트 복원 시 side-effect가 재현되지 않을 수 있음
- 라우팅 함수의 멱등성(idempotency) 보장 불가

**참고:** 설계서(782-784행)에서도 이 문제를 인지하고 있으며 "기존 패턴을 유지하되 향후 리팩터링" 방침이다. 그러나 sql_generator_node에서는 `reason.recovery_entry_source = "sql_generator"`를 노드 내부에서 올바르게 설정하고 있어(sql_generator.py:322), **같은 변경 내에서 두 가지 패턴이 혼재**하는 상황이다.

**제안:** sql_validator_node에서 `recovery_entry_source = "sql_validator"`를 설정하고, 라우팅 함수에서의 mutation을 제거한다. 이번 변경에서 sql_generator 경로가 올바른 패턴을 사용했으므로, sql_validator 경로도 동일하게 정리하면 일관성이 확보된다.

---

### [C-02] `_route_after_sql_validator`의 `case _` fallback이 GENERATION_FAILED를 `conclude_failure`로 처리

**파일:** `src/agents/graph/pipeline.py:239-253`

```python
case (
    FailureType.SQL_STRUCTURAL
    | FailureType.EMPTY_RESULT
    | FailureType.DB_ERROR
    | FailureType.NO_KNOWLEDGE
    | FailureType.NO_TABLE
    | FailureType.TERM_UNRESOLVABLE
):
    ...
    return "replan"

case _:
    return "conclude_failure"
```

**문제:** 설계서(747-782행)에서는 방어적 코딩으로 GENERATION_FAILED를 match-case에 포함시키도록 명시했으나, 실제 구현에서는 "도달 불가" 판단으로 제외하였다. 현재 정상 경로에서는 도달하지 않지만, 다음 시나리오에서 예기치 않게 도달할 수 있다:

1. sql_generator가 GENERATION_FAILED를 설정한 후 pending_signals도 동시에 설정하는 코드가 추가되는 경우
2. clarification_handler에서 복귀 후 sql_validator로 라우팅되는 경우 (현재 _VALID_RETURN_TARGETS에는 없지만 향후 추가 시)

이 경우 `case _`에 매칭되어 `conclude_failure`로 즉시 종료되며, recovery 기회가 없어진다.

**제안:** 설계서 대로 GENERATION_FAILED를 replan case에 추가한다. 비용이 없는 방어적 코딩이며, 향후 변경에 대한 안전장치가 된다.

---

## Warning (4건)

### [W-01] readiness_gate `_set_failure_context`의 NO_KNOWLEDGE 경우에 `selected_count`/`explored_count` 정보 포함 — 설계서 대비 추가 구현

**파일:** `src/agents/nodes/reason/readiness_gate.py:204-215`

```python
elif reason.failure_type == FailureType.NO_KNOWLEDGE:
    parts = [
        "질의 정규화(분해)에서 측정값·조건이 추출되지 않아 "
        "지식 항목이 생성되지 않았습니다.",
        ...
        f"후보 테이블: {selected_count}개 "
        f"(탐색 {explored_count}개)",
    ]
```

**분석:** 설계서의 NO_KNOWLEDGE failure_reason에는 후보 테이블 정보가 없었으나, 실제 구현에서는 추가되었다. 이는 recovery_agent에 더 풍부한 컨텍스트를 제공하므로 **긍정적 개선**이다. 다만 설계서와의 차이를 문서화할 필요가 있다.

**등급 사유:** 기능적 문제가 아니라 설계서-구현 정합성 관리 차원.

---

### [W-02] sql_generator_node의 `_parse_sql_response`에서 `status`가 "success"/"fail" 외의 값일 때 무조건 "fail" 처리

**파일:** `src/agents/nodes/reason/sql_generator.py:427-429`

```python
status = data.get("status", "success")
if status not in ("success", "fail"):
    status = "fail"
```

**문제:** LLM이 `"status": "partial"`, `"status": "SUCCESS"` 등 대소문자 변형이나 유사값을 반환할 수 있다. 현재 로직은 대소문자를 구분하며(`"SUCCESS"` != `"success"`), 이 경우 SQL이 정상 포함되어 있어도 fail로 처리된다.

**제안:**
```python
status = data.get("status", "success").lower()
if status not in ("success", "fail"):
    status = "fail" if not data.get("sql", "").strip() else "success"
```

---

### [W-03] recovery_agent `_build_prompt`에서 `SelectionStatus` 중복 import

**파일:** `src/agents/nodes/reason/recovery_agent.py:371`, `485`

```python
# 371행
from src.agents.state.state import SelectionStatus
...
# 485행
from src.agents.state.state import SelectionStatus
```

**문제:** 동일 모듈에서 `SelectionStatus`를 함수 내부에서 2회 지연 import하고 있다. 이 클래스는 이미 파일 상단(31-43행)의 import 블록에서 import할 수 있는 위치이며, 실제로 `FailureType`, `DeadEnd`, `HypothesisStatus` 등은 파일 상단에서 import되어 있다.

**제안:** 파일 상단 import 블록에 `SelectionStatus`를 추가하고, 371행/485행의 지연 import를 제거한다.

```python
from src.agents.state.state import (
    ConfidenceStatus,
    DeadEnd,
    ExecutionStep,
    FailureType,
    FinalStatus,
    Hypothesis,
    HypothesisStatus,
    Phase,
    PipelineState,
    ReasoningState,
    SelectionStatus,  # 추가
    should_terminate,
)
```

---

### [W-04] sql_validator Layer3 `_validate_layer3`의 limited_sql에 f-string SQL 조립 사용

**파일:** `src/agents/nodes/reason/sql_validator.py:489-492`

```python
if db.dialect == "tsql":
    limited_sql = f"SELECT TOP 5 * FROM ({sql}) _t"
else:
    limited_sql = f"SELECT * FROM ({sql}) _t LIMIT 5"
```

**문제:** `sql` 변수는 LLM이 생성한 SQL이므로 외부 입력에 해당한다. 이 값을 f-string으로 직접 조합하고 있다. `validate_sql_safety`를 Layer1에서 이미 통과했으므로 DML/DDL은 차단된 상태이지만, data-security 규칙("SQL은 반드시 파라미터 바인딩 사용, f-string 금지")에는 위배된다.

**완화 요소:** 이 패턴은 이번 변경에서 새로 도입된 것이 아닌 기존 코드이며, 서브쿼리 래핑이라 파라미터 바인딩으로 대체하기 어렵다. Layer1 안전성 검증이 선행되므로 실질 위험은 낮다.

**제안:** 이 패턴에 대해 명시적 주석으로 안전성 근거를 기록한다:
```python
# 안전성: validate_sql_safety(Layer1)에서 DML/DDL/시스템카탈로그 차단 후 도달
limited_sql = f"SELECT TOP 5 * FROM ({sql}) _t"
```

---

## Info (3건)

### [I-01] recovery_agent_system.txt 프롬프트의 진입 경로 설명이 4개이나 실제 코드 경로는 3개

**파일:** `resources/prompts/reason/recovery_agent_system.txt:11-14`

```
  - readiness_gate: 초기 탐색이 불충분하여...
  - readiness_gate(NO_KNOWLEDGE): 질의 정규화에서...
  - sql_validator: SQL 검증이 실패했습니다...
  - sql_generator: SQL 생성이 정보 부족으로...
```

**분석:** 프롬프트에서 "readiness_gate"와 "readiness_gate(NO_KNOWLEDGE)"를 별도 경로로 설명하고 있으나, `_build_prompt`의 `entry_src` 분기는 `"readiness_gate"` / `"sql_validator"` / `"sql_generator"` 3가지이다. readiness_gate(NO_KNOWLEDGE)는 `entry_desc` 텍스트 내 `failure_type: NO_KNOWLEDGE` 문자열로 구분되므로 LLM이 판별 가능하다. 프롬프트의 4개 설명은 LLM 이해도를 높이기 위한 의도적 선택으로 판단된다.

---

### [I-02] sql_validator Layer2b PASS + Layer3 EMPTY_RESULT 시 `validated_sql` 설정 경로 확인

**파일:** `src/agents/nodes/reason/sql_validator.py:124-142`

```python
if layer3_failure != FailureType.EMPTY_RESULT:
    ...
    return {"reason": reason}
# EMPTY_RESULT + Layer2b PASS → 정당한 0건으로 판정
logger.info("Layer3 0건이나 Layer2b PASS — 정당한 0건으로 판정")
# (여기서 return하지 않고 158행의 "전체 통과" 블록으로 fall-through)
```

158-166행:
```python
# 전체 통과
reason.validated_sql = sql
reason.failure_type = None
reason.failure_reason = None
```

**확인 결과:** Layer2b PASS + EMPTY_RESULT 시 return하지 않고 fall-through하여 158행에서 `validated_sql = sql`, `failure_type = None`이 설정된다. 이후 `_route_after_sql_validator`에서 `ft = None` → `"conclude_success"` → `result_finalizer` → `execute_sql` 경로를 탄다.

실행 시 0건이 반환되지만, 이는 의미적으로 "정당한 0건"이므로 사용자에게 "조건에 해당하는 데이터가 없습니다"로 안내하는 것이 적절하다. **현재 흐름에서 execute_sql 실행 시 0건 결과를 사용자에게 어떻게 전달하는지는 present 계층의 영역**이므로 이번 리뷰 범위 밖이나, 해당 경로의 사용자 경험을 확인할 것을 권장한다.

---

### [I-03] `_build_exploration_history`에서 `_search_query` 어트리뷰트 접근

**파일:** `src/agents/nodes/reason/recovery_agent.py:443`

```python
sq = getattr(uc, "_search_query", "(알 수 없음)")
```

**분석:** `UseCaseEntry` 모델에 `_search_query` 필드가 정의되어 있지 않고 `model_config = {"extra": "allow"}`로 설정되어 있으므로 동적 어트리뷰트로 접근한다. 이는 이번 변경과 무관한 기존 코드이지만, 타입 안전성 관점에서 `UseCaseEntry`에 `search_query: str = ""` 필드를 명시적으로 추가하는 것이 바람직하다. 언더스코어 접두사(`_search_query`)는 Pydantic에서 private 필드로 취급될 수 있어 호환성 문제가 발생할 수 있다.

---

## 설계서-구현 정합성 매트릭스

| 설계서 항목 | 구현 상태 | 비고 |
|---|---|---|
| 3-1. enums.py NO_USE_CASE -> NO_KNOWLEDGE | 일치 | 주석 갱신 포함 |
| 3-1. enums.py +GENERATION_FAILED | 일치 | |
| 3-2. DeadEnd default NO_KNOWLEDGE | 일치 | |
| 3-2. Literal "sql_generator" 추가 | 일치 | |
| 3-3. readiness_gate SelectionStatus import | 일치 | |
| 3-3. readiness_gate selected_count 기준 | 일치 | |
| 3-3. readiness_gate failure_reason 타입별 분기 | 일치 (개선 포함) | NO_KNOWLEDGE에 후보 테이블 정보 추가 |
| 3-4. _parse_sql_response str -> dict | 일치 | |
| 3-4. _call_llm_for_sql str -> dict | 일치 | |
| 3-4. sql_generator_node success/fail 분기 | 일치 | |
| 3-5. _build_prompt 3분기 | 일치 | |
| 3-5. _handle_hypothesis_transition None 방어 | 일치 | |
| 3-6A. _route_after_sql_generator GENERATION_FAILED | 일치 | |
| 3-6B. 엣지 맵 "replan" 추가 | 일치 | |
| 3-6C. match-case NO_KNOWLEDGE (GENERATION_FAILED 제거) | **차이** | 설계서는 포함, 구현은 제외 (도달 불가 판단). C-02 참조 |
| 3-7A. sqlglot 파싱 실패 PASS 위임 | 일치 | 안전성 검증 선행 확인 |
| 3-7B. EMPTY_RESULT feedback SQL 포함 | 일치 | `sql[:500]` 포함 |
| 3-7C. Layer2b PASS + EMPTY_RESULT 0건 허용 | 일치 | |
| recovery_agent_system.txt 진입 경로 확장 | 일치 | 4경로 기술 |
| architecture.md FailureType 갱신 | 일치 | NO_USE_CASE 완전 제거, GENERATION_FAILED 추가 |

---

## NO_USE_CASE 잔존 검사

| 검색 범위 | 잔존 여부 |
|---|---|
| `src/` 전체 | 없음 (완전 제거) |
| `resources/` 전체 | 없음 (완전 제거) |
| `docs/architecture/architecture.md` | 없음 (NO_KNOWLEDGE로 갱신) |
| `docs/todo/` (설계서) | 잔존 (변경 이전 코드 인용으로 의도적) |
| `docs/reviews/` (기존 리뷰) | 잔존 (히스토리 참조로 의도적) |

---

## GENERATION_FAILED 전체 경로 추적

```
sql_generator_node (sql_generator.py:314-322)
  result["status"] == "fail" or not result["sql"]
    → reason.failure_type = GENERATION_FAILED
    → reason.recovery_entry_source = "sql_generator"
    → return {"reason": reason}

_route_after_sql_generator (pipeline.py:197-198)
  state.reason.failure_type == GENERATION_FAILED
    → return "replan"

엣지 맵 (pipeline.py:472)
  "replan": "recovery_agent"

recovery_agent_node (recovery_agent.py:62-137)
  _handle_hypothesis_transition (recovery_agent.py:144-182)
    → elif reason.failure_type (GENERATION_FAILED):  # 가설 없이 진입 방어
      → DeadEnd(hypothesis_id="no_hypothesis", failure_type=GENERATION_FAILED, ...)
  entry_failure_type = GENERATION_FAILED (recovery_agent.py:84)
  _build_recovery_plan → _build_prompt (recovery_agent.py:319-429)
    → entry_src = "sql_generator"
    → entry_desc에 "sql_generator에서 진입: SQL 생성이 정보 부족으로 거부" 포함
```

**판정:** 전체 경로가 논리적으로 정합하며, failure 맥락(failure_type, failure_reason, recovery_entry_source)이 소실 없이 recovery_agent까지 전달된다.

---

## sqlglot 파싱 실패 PASS 위임 시 안전성 검증

**파일:** `src/agents/nodes/reason/sql_validator.py:224-242`

```python
# 1. 공통 안전성 검증 (DML/DDL/시스템카탈로그)
safety = validate_sql_safety(sql, dialect)
if not safety.is_safe:
    return {"status": "FAIL", ...}

# 2. dialect별 sqlglot 파싱
ast = parse_sql_safe(sql, dialect=dialect)
if ast is None:
    # 안전성 검증은 위에서 통과했으므로 Layer3에 위임
    return {"status": "PASS"}
```

**판정:** `validate_sql_safety`가 sqlglot 파싱 이전에 실행되므로, DML/DDL/시스템카탈로그/다중쿼리/PII 노출이 모두 차단된 상태에서만 PASS 위임이 발생한다. `sql_safety_checker.py`의 5단계 검증(유니코드 정규화, 금지 패턴, sqlglot 파싱, PII, LIMIT)이 선행되므로 보안 위험은 없다.

---

## 결론 및 후속 조치

구현 품질은 전반적으로 높으며, 설계서의 핵심 목표 5가지가 모두 달성되었다.

**즉시 조치 권장 (Critical):**
1. C-01: `_route_after_sql_validator`의 state mutation을 sql_validator_node 내부로 이동
2. C-02: `_route_after_sql_validator` match-case에 `FailureType.GENERATION_FAILED` 방어적 추가

**차기 작업 시 포함 권장 (Warning):**
3. W-02: `_parse_sql_response`의 status 대소문자 정규화
4. W-03: recovery_agent.py의 SelectionStatus 중복 지연 import 정리

**참고 사항 (Info):**
5. I-02: Layer2b PASS + EMPTY_RESULT 0건 경로의 사용자 경험(present 계층) 확인 필요
