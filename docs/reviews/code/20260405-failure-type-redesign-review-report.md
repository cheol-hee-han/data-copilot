# FailureType 재설계 상세 코드 리뷰

- 일시: 2026-04-05
- 대상: FailureType Enum 변경, sql_generator fail 경로, sqlglot PASS 위임, EMPTY_RESULT 안전장치
- 관점: 논리적 일관성, 기존 코드 정합성, 누락 변경점, 명명 규칙, 보안, 타입 안전성, 에러 처리

---

## 1. NO_USE_CASE -> NO_KNOWLEDGE 전파 누락 분석

### 1-1. (Critical) src/agents/state/state.py:315 -- DeadEnd 기본값 미변경

**현재 코드:**
```python
class DeadEnd(BaseModel):
    failure_type: FailureType = FailureType.NO_USE_CASE
```

**문제:** 상세설계에서 DeadEnd default를 NO_KNOWLEDGE로 변경한다고 명시했으나, 현재 코드는 NO_USE_CASE 그대로다. NO_USE_CASE Enum 멤버를 삭제하면 이 라인에서 즉시 ImportError/AttributeError가 발생한다.

**영향 범위:** `_handle_hypothesis_transition()` (recovery_agent.py:160)에서 `reason.failure_type`이 None일 때 DeadEnd가 NO_USE_CASE 기본값으로 생성되므로, Enum 이름 변경 시 런타임 오류로 직결된다.

**조치:** Enum 변경과 동시에 DeadEnd.failure_type 기본값을 `FailureType.NO_KNOWLEDGE`로 변경 필수.

### 1-2. (Critical) src/agents/nodes/reason/readiness_gate.py:167 -- NO_USE_CASE 직접 참조

**현재 코드:**
```python
elif ki_total == 0:
    reason.failure_type = FailureType.NO_USE_CASE
```

**문제:** Enum 멤버 이름이 NO_KNOWLEDGE로 바뀌면 이 라인이 AttributeError를 발생시킨다.

**조치:** `FailureType.NO_KNOWLEDGE`로 변경 필수.

### 1-3. (Critical) src/agents/graph/pipeline.py:235 -- 라우팅 match-case 누락

**현재 코드:**
```python
case (
    FailureType.SQL_STRUCTURAL
    | FailureType.EMPTY_RESULT
    | FailureType.DB_ERROR
    | FailureType.NO_USE_CASE     # <-- 변경 필요
    | FailureType.NO_TABLE
    | FailureType.TERM_UNRESOLVABLE
):
```

**문제:** NO_USE_CASE -> NO_KNOWLEDGE 변경 시 이 패턴 매칭이 깨진다. 또한 GENERATION_FAILED가 이 match-case 어디에도 포함되지 않으므로, `_route_after_sql_validator`의 `case _: return "conclude_failure"`로 빠진다. 그런데 상세설계의 의도는 GENERATION_FAILED가 `_route_after_sql_generator`에서 처리되므로 sql_validator까지 도달하지 않는 것이지만, 방어적으로 이 match-case에도 GENERATION_FAILED를 포함하는 것이 안전하다.

**조치:**
1. `FailureType.NO_USE_CASE` -> `FailureType.NO_KNOWLEDGE`
2. GENERATION_FAILED를 replan 분기에 추가하거나, 최소한 주석으로 "sql_generator에서 이미 처리됨"을 명시

### 1-4. (Warning) docs/architecture/architecture.md -- 문서 내 NO_USE_CASE 참조

architecture.md의 라인 293, 1058에 NO_USE_CASE가 명시되어 있다. Enum 변경 시 문서도 갱신이 필요하다.

---

## 2. GENERATION_FAILED 신규 추가 -- 논리적 일관성

### 2-1. (Critical) 명명 규칙 불일관 -- GENERATION_FAILED vs 기존 패턴

기존 FailureType 네이밍 패턴:
- `NO_TABLE`, `NO_USE_CASE` -- "부재" 유형: `NO_` 접두사
- `SQL_SYNTAX`, `SQL_SEMANTIC_LOCAL`, `SQL_STRUCTURAL` -- "SQL 결함" 유형: `SQL_` 접두사
- `TERM_UNRESOLVABLE` -- "해소 불가" 유형
- `EMPTY_RESULT`, `DB_ERROR` -- "실행 결과" 유형

`GENERATION_FAILED`는 "SQL Generator가 정보 부족으로 생성을 거부"한 것이므로 본질적으로 "정보 부재" 범주에 속한다. 기존 패턴과의 일관성 측면에서 다음 대안을 검토할 필요가 있다:

| 후보 | 근거 |
|---|---|
| `GENERATION_REFUSED` | LLM의 능동적 거부를 표현 (FAILED는 수동적) |
| `INFO_INSUFFICIENT` | 원인(정보 부족)에 초점, NO_ 계열과 의미 유사 |
| `GENERATION_FAILED` (현안) | 발생 위치(Generator)에 초점 |

**권장:** GENERATION_FAILED 자체는 수용 가능하나, 주석에 "LLM이 정보 부족을 판단하여 능동적으로 거부"임을 명확히 해야 한다. 현재 주석 "SQL Generator가 정보 부족으로 생성 거부"는 적절하다.

### 2-2. (Critical) sql_generator -> recovery -> sql_generator 재진입 시 reasons 전달 경로 검증

상세설계의 sql_generator 변경안:
```python
reason.failure_type = FailureType.GENERATION_FAILED
reason.failure_reason = "\n".join(result["reasons"])
reason.recovery_entry_source = "sql_generator"
```

recovery_agent.py의 `_handle_hypothesis_transition` (라인 144-167):
```python
reason.dead_ends.append(DeadEnd(
    hypothesis_id=failed.hypothesis_id,
    failure_type=(
        reason.failure_type
        or FailureType.TERM_UNRESOLVABLE
    ),
    reason=reason.failure_reason or "실패 사유 미제공",
))
```

여기서 `reason.failure_type`은 GENERATION_FAILED, `reason.failure_reason`은 LLM의 reasons가 된다. DeadEnd에 정상 기록된다.

그러나 recovery_agent_node (라인 84-87)에서:
```python
entry_failure_type = reason.failure_type
entry_failure_reason = reason.failure_reason
reason.failure_type = None
reason.failure_reason = None
```

이후 `_build_prompt`에서 entry_failure_type/reason을 사용하는데, 현재 코드 (라인 319-332):
```python
entry_src = reason.recovery_entry_source or "readiness_gate"
if entry_src == "sql_validator":
    ...
else:
    entry_desc = "readiness_gate에서 진입: ..."
```

**문제:** `entry_src == "sql_generator"` 분기가 없다. "readiness_gate"도 아니고 "sql_validator"도 아닌 "sql_generator"이므로 `else` 절에 빠져서 "readiness_gate에서 진입: 초기 탐색이 불충분하여 추가 탐색이 필요합니다"라는 부정확한 설명이 LLM에 전달된다.

상세설계에서 이 분기 추가를 명시했으나, 현재 코드에는 반영되지 않았고, elif 체인으로 추가해야 한다. 이것이 누락되면 recovery_agent의 LLM이 잘못된 맥락으로 재계획을 수립하여 복구 품질이 저하된다.

**조치:** `_build_prompt`에서 `elif entry_src == "sql_generator":` 분기 추가 필수.

### 2-3. (Warning) recovery_agent_node에서 current_hypothesis가 None인 경우

sql_generator에서 GENERATION_FAILED로 recovery_agent에 진입할 때, `current_hypothesis`가 ACTIVE 상태인지 확인이 필요하다. sql_generator 재진입 경로에서는 readiness_gate가 GENERATE를 판정한 후이므로 current_hypothesis가 ACTIVE일 수 있지만, 강제 생성(`_apply_force_generate`)의 경우 가설 상태가 불확실할 수 있다.

`_handle_hypothesis_transition` (라인 148-153)에서 `current_hypothesis`가 None이면 DeadEnd가 기록되지 않는다. GENERATION_FAILED의 failure_reason이 DeadEnd에 기록되지 않으면 recovery_agent LLM이 이전 실패 패턴을 인지하지 못하고 동일 시도를 반복할 위험이 있다.

**조치:** `_handle_hypothesis_transition`에서 current_hypothesis가 None일 때도 DeadEnd를 기록하는 방어 로직 추가를 검토해야 한다. 예를 들어:
```python
if reason.failure_type and not reason.current_hypothesis:
    reason.dead_ends.append(DeadEnd(
        hypothesis_id="no_hypothesis",
        failure_type=reason.failure_type,
        reason=reason.failure_reason or "가설 없이 실패",
    ))
```

---

## 3. readiness_gate -- SELECTED 기준 변경

### 3-1. (Warning) ct_count vs selected_count -- 의미 변경의 암묵적 영향

**현재 코드 (readiness_gate.py:154, 164):**
```python
ct_count = len(reason.explored_tables)
if ct_count == 0:
    reason.failure_type = FailureType.NO_TABLE
```

**상세설계:**
```python
selected_count = len([
    t for t in reason.explored_tables
    if t.selection_status == SelectionStatus.SELECTED
])
if selected_count == 0:
    reason.failure_type = FailureType.NO_TABLE
```

이 변경은 의미적으로 정확하다. 탐색은 했지만 모두 REJECTED된 경우도 NO_TABLE로 판정하는 것이 맞다. 다만 failure_reason 조립부(라인 172-190)에서 `ct_count`를 여전히 사용하는 코드가 있다:

```python
if ct_count == 0:
    parts.append("후보 테이블이 0개로, ...")
else:
    parts.append(f"후보 테이블: {ct_count}개")
```

상세설계에서는 `explored_count`와 `selected_count`를 구분하여 "탐색된 테이블 N개가 모두 부적합 판정"이라는 더 정확한 메시지를 제안했다. 현재 코드의 failure_reason 조립부도 동시에 변경해야 일관성이 유지된다.

### 3-2. (Info) SelectionStatus import 누락 가능성

현재 readiness_gate.py의 import (라인 31-38)에 `SelectionStatus`가 없다:
```python
from src.agents.state.state import (
    ConfidenceStatus,
    FailureType,
    Phase,
    PipelineState,
    ReasoningState,
    StepStatus,
)
```

상세설계에서 `t.selection_status == SelectionStatus.SELECTED`를 사용하므로 `SelectionStatus` import 추가가 필요하다.

---

## 4. sql_generator 파싱 변경 -- 타입 안전성

### 4-1. (Critical) _parse_sql_response 반환 타입 변경: str -> dict -- 호환성 파괴

**현재 코드 (sql_generator.py:397-412):**
```python
def _parse_sql_response(raw: str) -> str:
    """LLM 응답에서 SQL을 추출한다."""
    ...
    return sql  # str 반환
```

**현재 _call_llm_for_sql (라인 415-428):**
```python
async def _call_llm_for_sql(prompt: str, query: str) -> str:
    _, sql = await llm_call_with_parse_retry(
        ...
        parse_fn=_parse_sql_response,  # T = str
    )
    return sql  # str
```

**llm_call_with_parse_retry 시그니처 (retry.py:47-56):**
```python
async def llm_call_with_parse_retry(
    ...
    parse_fn: Callable[[str], T],
) -> tuple[str, T]:
```

`parse_fn`의 반환 타입 T가 `_parse_sql_response`의 반환 타입에 의해 결정된다. 현재는 `T = str`이므로 `_call_llm_for_sql`의 반환값도 `str`이다.

**상세설계에서 `_parse_sql_response`를 `dict`로 변경하면:**
- `_call_llm_for_sql`의 `_, sql`에서 `sql`이 `dict`가 된다
- `_call_llm_for_sql`의 반환 타입 힌트 `-> str`과 실제 반환 `dict`가 불일치
- `sql_generator_node`에서 `generated = await _call_llm_for_sql(...)`의 타입이 `dict`가 되므로 `reason.generated_sql = generated`에서 `str | None` 필드에 `dict`가 할당된다

**조치:** _call_llm_for_sql의 반환 타입과 내부 로직을 동시에 변경해야 한다:

```python
async def _call_llm_for_sql(prompt: str, query: str) -> dict:
    _, result = await llm_call_with_parse_retry(
        ...
        parse_fn=_parse_sql_response,
    )
    return result  # dict

# sql_generator_node에서:
result = await _call_llm_for_sql(prompt, state.preprocessed_input)
if result["status"] == "success":
    reason.generated_sql = result["sql"]
else:
    reason.generated_sql = None
    reason.failure_type = FailureType.GENERATION_FAILED
    ...
```

### 4-2. (Warning) LLM 호출 오류 시 fallback 처리

**상세설계:**
```python
except Exception as e:
    result = {"status": "fail", "sql": "", "reasons": [f"LLM 호출 오류: {e}"], "explanation": ""}
```

**현재 코드 (라인 287-289):**
```python
except Exception as e:
    logger.error("SQL 생성 LLM 호출 오류", error=str(e))
    generated = ""
```

현재는 빈 SQL을 그대로 진행시키고, sql_validator에서 빈 SQL을 SQL_SYNTAX로 잡아낸다 (sql_validator.py:59-63). 상세설계는 이를 sql_generator 단계에서 GENERATION_FAILED로 즉시 처리하는 방식으로 변경한다.

이 변경 자체는 합리적이나, LLM 호출 오류(네트워크 타임아웃 등)와 LLM의 능동적 거부(정보 부족 판단)를 동일한 GENERATION_FAILED로 분류하면 recovery_agent가 구별하지 못한다. 네트워크 오류는 재시도가 적절하고, 정보 부족은 추가 탐색이 적절하다.

**권장:** Exception catch를 별도 failure_type으로 분류하거나, failure_reason에 "LLM 호출 오류"를 명시하여 recovery_agent가 구분할 수 있도록 하는 것이 바람직하다.

### 4-3. (Warning) _parse_sql_response에서 status 필드 검증 부재

```python
"status": data.get("status", "success"),
```

LLM이 `"status": "partial"` 같은 예상 외 값을 반환하면 success도 fail도 아닌 상태가 된다. Literal["success", "fail"] 검증이 필요하다:

```python
status = data.get("status", "success")
if status not in ("success", "fail"):
    status = "fail"  # 안전 기본값
```

---

## 5. pipeline.py -- sql_generator 후 라우팅

### 5-1. (Critical) 엣지 맵에 "replan" 누락

**현재 엣지 맵 (pipeline.py:458-465):**
```python
workflow.add_conditional_edges(
    "sql_generator",
    _route_after_sql_generator,
    {
        "sql_validator": "sql_validator",
        "clarification_handler": "clarification_handler",
    },
)
```

상세설계의 `_route_after_sql_generator`에 `return "replan"` 분기가 추가되지만, 엣지 맵에 `"replan": "recovery_agent"`가 없다. LangGraph는 엣지 맵에 없는 반환값을 받으면 런타임 에러를 발생시킨다.

**조치:** 엣지 맵에 `"replan": "recovery_agent"` 추가 필수:
```python
{
    "sql_validator": "sql_validator",
    "clarification_handler": "clarification_handler",
    "replan": "recovery_agent",
}
```

### 5-2. (Warning) _route_after_sql_generator에서 state mutation

상세설계에서 sql_generator_node 내부에서 `reason.recovery_entry_source = "sql_generator"`를 설정한다. 그런데 현재 pipeline.py의 `_route_after_sql_validator`(라인 223, 239)에서는 라우팅 함수 내에서 `state.reason.recovery_entry_source = "sql_validator"`를 직접 설정하고 있다.

라우팅 함수 내 state mutation은 LangGraph의 원칙에 어긋난다 (조건부 엣지는 순수 함수여야 함). 기존 코드의 `_route_after_sql_validator`에서 이미 위반하고 있으므로 당장의 일관성은 유지되지만, sql_generator_node에서 recovery_entry_source를 설정하는 방식이 더 올바르다. 향후 기존 pipeline.py의 state mutation도 노드 내부로 이동시키는 것이 바람직하다.

---

## 6. sql_validator -- sqlglot 파싱 실패 PASS 위임

### 6-1. (Critical) Layer1 PASS 시 Layer2a 구조 검증 스킵 문제

**현재 코드 (sql_validator.py:209-270):**
```python
def _validate_layer1(sql, reason, dialect):
    # 1. 안전성 검증
    # 2. sqlglot 파싱
    ast = parse_sql_safe(sql, dialect=dialect)
    if ast is None:
        return {"status": "FAIL", "feedback": ...}
    # 3. 테이블 확인 (ast 사용)
    # 4. 컬럼 확인 (ast 사용)
```

**상세설계: sqlglot 파싱 실패 시 PASS로 통과**

이 변경의 핵심 문제: sqlglot 파싱이 실패하면 `ast`가 None이므로, Layer1의 테이블/컬럼 확인 (단계 3, 4)도 스킵된다. 또한 Layer2a의 `_validate_layer2a`는 문자열 기반이므로 ast 없이도 동작하지만, Layer1의 보안 검증(DML/DDL/시스템 카탈로그)은 sqlglot 파싱 이전에 수행되므로 안전하다.

**스킵되는 검증 목록:**
1. Layer1 - 사용 테이블이 explored_tables에 존재하는지 (라인 231-248)
2. Layer1 - 사용 컬럼이 explored_tables 컬럼 범위 안인지 (라인 250-263)
3. Layer2a - GROUP BY/집계 함수 구조 체크 (문자열 기반, ast 불필요 -- 정상 동작)

**결론:** 테이블/컬럼 검증이 스킵되는 것은 심각한 보안 우회가 아니다(DB가 읽기 전용이므로). 그러나 LLM이 hallucinate한 테이블명이 DB 실행까지 가는 것은 불필요한 자원 낭비다.

**권장:** Layer1을 두 단계로 분리하는 것을 검토:
```
Layer1a: 안전성 검증 (항상 실행)
Layer1b: 구조 검증 (sqlglot 파싱 가능 시)
-> 파싱 실패 시: Layer1b 스킵, Layer2a/3/2b로 진행
```

### 6-2. (Warning) Layer1에서 PASS 반환 시 후속 Layer에 ast 전달 부재

현재 Layer1은 PASS/FAIL만 반환하고 ast를 전달하지 않는다. sqlglot 파싱이 성공해도 ast를 Layer2a에서 재사용하지 않는다(Layer2a는 문자열 기반). 따라서 이 구조는 현재 문제가 없으나, 향후 ast 기반 검증을 Layer2a에 추가할 때 재파싱이 필요하다.

---

## 7. sql_validator -- EMPTY_RESULT 안전장치

### 7-1. (Warning) Layer2b PASS + Layer3 EMPTY_RESULT 판정 로직

**현재 코드 (sql_validator.py:120-132):**
```python
# 안전장치: Layer2b가 PASS인데 Layer3가 FAIL이면
if layer3_result["status"] == "FAIL":
    layer3_failure = layer3_result.get("failure_type", FailureType.EMPTY_RESULT)
    reason.failure_type = layer3_failure
    reason.failure_reason = layer3_result["feedback"]
    return {"reason": reason}
```

**상세설계:**
```python
if layer3_failure != FailureType.EMPTY_RESULT:
    reason.failure_type = layer3_failure
    reason.failure_reason = layer3_result["feedback"]
    return {"reason": reason}
logger.info("Layer3 0건이나 Layer2b PASS -- 정당한 0건으로 판정")
```

이 변경은 "Layer2b가 의미적으로 PASS를 판정했다면, 0건 결과도 정당할 수 있다"는 논리다. 예: "2025년 미래 대출 건수"를 조회하면 0건이 정상이다.

**주의점:**
1. Layer2b가 PASS인데 Layer3이 DB_ERROR인 경우: `layer3_failure != FailureType.EMPTY_RESULT`이 True이므로 DB_ERROR로 정상 처리된다. 이것은 올바르다.
2. Layer2b 비활성(`settings.validate_layer2b_enabled = False`) 시: 상세설계의 변경은 라인 120-132 범위(Layer2b 활성 블록)에만 적용되므로, Layer2b 비활성 블록(라인 134-147)에는 영향이 없다. Layer2b 비활성 시 0건은 항상 EMPTY_RESULT로 FAIL 처리된다. 이것은 안전한 기본값이다.

### 7-2. (Info) EMPTY_RESULT feedback에 SQL 전문 포함 -- 보안 검토

**상세설계:**
```python
"feedback": f"정상적으로 SQL을 생성하고 조회했으나 데이터가 0건입니다.\n현재 SQL:\n{sql}",
```

이 feedback은 recovery_agent의 LLM 프롬프트에 전달된다. SQL 전문이 프롬프트에 포함되는 것 자체는 보안 문제가 아니다(LLM 내부 처리이므로). 다만 recovery_agent가 DeadEnd.reason에 이 전체 문자열을 저장하면, 이후 sql_generator 프롬프트의 `{dead_ends}` 슬롯에 SQL 전문이 반복 노출되어 토큰 낭비가 발생할 수 있다.

**권장:** feedback에 SQL 전문을 포함하되, DeadEnd.reason에는 truncate 처리하거나 SQL 전문을 별도 필드에 저장하는 것을 검토.

---

## 8. recovery_entry_source에 "sql_generator" 추가

### 8-1. (Critical) Literal 타입 변경의 전파 확인

**현재 코드 (state.py:535-537):**
```python
recovery_entry_source: Literal[
    "readiness_gate", "sql_validator", None,
] = None
```

**상세설계:** `"sql_generator"` 추가.

이 Literal 타입을 참조하는 코드:
- readiness_gate.py:141 -- `"readiness_gate"` 설정 (영향 없음)
- pipeline.py:223, 239 -- `"sql_validator"` 설정 (영향 없음)
- reasoning_preparer.py:53 -- `None` 초기화 (영향 없음)
- recovery_agent.py:319 -- `or "readiness_gate"` 폴백 (영향 있음: "sql_generator" 분기 추가 필요)

Pydantic v2에서 Literal 검증이 활성화되어 있으므로, sql_generator_node에서 `reason.recovery_entry_source = "sql_generator"`를 설정하면 Literal에 "sql_generator"가 없는 경우 ValidationError가 발생한다. **반드시 state.py의 Literal 변경이 선행되어야 한다.**

---

## 9. 추가 발견 사항

### 9-1. (Warning) sql_generator_node에서 failure_type/reason 초기화 타이밍

**현재 코드 (sql_generator.py:292-293):**
```python
reason.generated_sql = generated
reason.failure_type = None
reason.failure_reason = None
```

현재는 성공/실패 구분 없이 항상 None으로 초기화한다. 상세설계에서 fail 시 GENERATION_FAILED를 설정하지만, 이 초기화가 먼저 실행되고 이후에 조건 분기로 덮어쓰는 순서를 명확히 해야 한다. 상세설계 코드에서는 success/fail 분기 안에서 각각 설정하므로 순서 문제는 없다.

### 9-2. (Warning) sql_generator 프롬프트에 fail 출력 포맷 지시 필요

상세설계에서 sql_generator가 `{"status": "fail", "reasons": [...]}` 포맷으로 응답하도록 기대하지만, 현재 `SQL_GENERATOR_SYSTEM` 프롬프트에 이 출력 포맷이 명시되어 있는지 확인이 필요하다. LLM이 fail 응답 포맷을 알지 못하면 항상 SQL을 강제 생성하려 하거나, 자유 형식 텍스트로 거부 의사를 표현할 수 있다.

**조치:** `SQL_GENERATOR_SYSTEM` 프롬프트에 fail 출력 포맷 지시를 추가해야 한다.

### 9-3. (Info) recovery_agent 프롬프트의 진입 경로 설명에 sql_generator 추가

recovery_agent_system.txt (라인 10-11):
```
  - readiness_gate: 초기 탐색이 불충분하여 ...
  - sql_validator: SQL 검증이 실패했습니다. ...
```

여기에 `- sql_generator: SQL 생성이 거부되었습니다. 부족한 정보를 확인하세요.` 항목을 추가해야 LLM이 sql_generator 진입 경로를 이해할 수 있다.

---

## 변경 실행 체크리스트

| 순서 | 파일 | 변경 | 등급 |
|---|---|---|---|
| 1 | src/models/enums.py | NO_USE_CASE -> NO_KNOWLEDGE, GENERATION_FAILED 추가 | Critical |
| 2 | src/agents/state/state.py:315 | DeadEnd default NO_KNOWLEDGE | Critical |
| 3 | src/agents/state/state.py:535 | Literal에 "sql_generator" 추가 | Critical |
| 4 | src/agents/nodes/reason/readiness_gate.py:167 | NO_USE_CASE -> NO_KNOWLEDGE | Critical |
| 5 | src/agents/nodes/reason/readiness_gate.py:31 | SelectionStatus import 추가 | Info |
| 6 | src/agents/nodes/reason/readiness_gate.py:154 | ct_count -> selected_count 변경 + failure_reason 조립 동기화 | Warning |
| 7 | src/agents/nodes/reason/sql_generator.py:397 | _parse_sql_response 반환 타입 str -> dict | Critical |
| 8 | src/agents/nodes/reason/sql_generator.py:415 | _call_llm_for_sql 반환 타입 str -> dict | Critical |
| 9 | src/agents/nodes/reason/sql_generator.py:207 | sql_generator_node에 success/fail 분기 | Critical |
| 10 | src/agents/graph/pipeline.py:187 | _route_after_sql_generator에 GENERATION_FAILED 분기 | Critical |
| 11 | src/agents/graph/pipeline.py:458 | 엣지 맵에 "replan" 추가 | Critical |
| 12 | src/agents/graph/pipeline.py:235 | NO_USE_CASE -> NO_KNOWLEDGE | Critical |
| 13 | src/agents/nodes/reason/recovery_agent.py:319 | entry_src == "sql_generator" 분기 추가 | Critical |
| 14 | resources/prompts/.../recovery_agent_system.txt | sql_generator 진입 경로 설명 추가 | Info |
| 15 | resources/prompts/.../sql_generator_system.txt | fail 출력 포맷 지시 추가 | Warning |
| 16 | src/agents/nodes/reason/sql_validator.py:225 | sqlglot 파싱 실패 PASS 처리 (Layer1 분리 검토) | Warning |
| 17 | src/agents/nodes/reason/sql_validator.py:493 | EMPTY_RESULT feedback SQL 전문 포함 | Info |
| 18 | src/agents/nodes/reason/sql_validator.py:120 | EMPTY_RESULT 안전장치 변경 | Warning |
| 19 | docs/architecture/architecture.md | NO_USE_CASE -> NO_KNOWLEDGE, GENERATION_FAILED 추가 | Info |

---

## 총평

상세설계의 의도와 방향성은 적절하다. sql_generator의 능동적 거부 경로 추가, SELECTED 기준 NO_TABLE 판정, EMPTY_RESULT 0건 존중 논리 모두 파이프라인 완성도를 높이는 변경이다.

다만 **11개의 Critical 항목**이 식별되었다. 특히 (1) NO_USE_CASE -> NO_KNOWLEDGE 전파 누락 3건, (2) _parse_sql_response 반환 타입 str -> dict 호환성 파괴, (3) pipeline.py 엣지 맵에 "replan" 누락, (4) recovery_agent _build_prompt에 sql_generator 분기 부재는 구현 시 런타임 오류를 직접 유발하므로 반드시 선행 해결해야 한다.

변경 순서: Enum 정의(1) -> State 타입(2,3) -> 소비 코드(4,6,7,8,9,10,11,12,13) -> 프롬프트(14,15) -> 검증/문서(16-19) 순서로 진행하는 것이 의존성 충돌을 최소화한다.
