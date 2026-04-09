# FailureType 체계 재설계 Phase 1-2 구현 리뷰

> 리뷰 일자: 2026-04-05
> 대상: Phase 1-2 구현 완료분 (enums.py, state.py, readiness_gate.py, sql_generator.py, recovery_agent.py)
> 설계서: `docs/todo/20260405-failure-type-redesign.md`
> 리뷰어: Code Reviewer Agent

---

## 1. 설계 대비 구현 일치도

설계서의 Phase 1 (3-1, 3-2), Phase 2 (3-3, 3-4, 3-5) 구현이 설계 명세와 **정확히 일치**함을 확인하였다.
추가로 설계서에서 Phase 3으로 분류된 `pipeline.py`의 `_route_after_sql_generator` 분기도 이미 구현 완료 상태이다.

| 설계 항목 | 일치 여부 | 비고 |
|-----------|-----------|------|
| 3-1. enums.py NO_USE_CASE -> NO_KNOWLEDGE | 일치 | 주석도 갱신됨 |
| 3-1. GENERATION_FAILED 추가 | 일치 | |
| 3-2. DeadEnd 기본값 변경 | 일치 | |
| 3-2. Literal 확장 (sql_generator) | 일치 | |
| 3-3. readiness_gate SelectionStatus import | 일치 | |
| 3-3. _set_failure_context 전체 재작성 | 일치 | |
| 3-4. _parse_sql_response str->dict | 일치 | |
| 3-4. _call_llm_for_sql str->dict | 일치 | |
| 3-4. sql_generator_node success/fail 분기 | 일치 | |
| 3-5. _build_prompt 3분기 | 일치 | |
| 3-5. _handle_hypothesis_transition 방어 | 일치 | |
| 3-6. pipeline.py 라우팅 (Phase 3) | 이미 구현됨 | 설계서에서 Phase 3으로 분류했으나 선행 적용 |

---

## 2. 리뷰 결과

### [R-01] Warning -- pipeline.py의 sql_validator match-case에 GENERATION_FAILED 포함 (죽은 코드)

**파일:** `src/agents/graph/pipeline.py:239-247`

```python
case (
    FailureType.SQL_STRUCTURAL
    | FailureType.EMPTY_RESULT
    | FailureType.DB_ERROR
    | FailureType.NO_KNOWLEDGE
    | FailureType.GENERATION_FAILED  # <-- 도달 불가
    | FailureType.NO_TABLE
    | FailureType.TERM_UNRESOLVABLE
):
    state.reason.recovery_entry_source = (
        "sql_validator"
    )
    return "replan"
```

**문제:** `_route_after_sql_generator`에서 `GENERATION_FAILED`이면 `"replan"`을 반환하여 sql_validator를 우회한다(197행). 따라서 `_route_after_sql_validator`의 match-case에서 `GENERATION_FAILED`에 도달하는 경로가 존재하지 않는다.

또한, 만약 예기치 않은 경로로 이 분기에 도달하면 `recovery_entry_source = "sql_validator"`로 덮어써져 sql_generator에서 설정한 `"sql_generator"`가 소실되어 recovery_agent의 진입 경로 판별이 오작동한다.

**권장 조치:**
- `GENERATION_FAILED`를 이 match-case에서 제거한다.
- 또는 방어적으로 남기되, `recovery_entry_source` 덮어쓰기 전에 기존 값이 있으면 보존하는 가드를 추가한다.

---

### [R-02] Warning -- readiness_gate _set_failure_context에서 selected_count == 0과 ki_total == 0이 동시에 성립할 때의 우선순위

**파일:** `src/agents/nodes/reason/readiness_gate.py:173-178`

```python
if selected_count == 0:
    reason.failure_type = FailureType.NO_TABLE
elif ki_total == 0:
    reason.failure_type = FailureType.NO_KNOWLEDGE
else:
    reason.failure_type = FailureType.TERM_UNRESOLVABLE
```

**분석:** 테이블도 없고(selected_count == 0) 지식 항목도 없는(ki_total == 0) 상태에서는 `NO_TABLE`이 우선 선택된다. 이것이 의도된 동작인지 확인이 필요하다.

두 조건이 동시에 성립하는 경우 recovery_agent는 "테이블을 찾아라"는 방향으로 복구를 시도하게 되는데, 실제 근본 원인이 질의 정규화 실패(지식 항목 0건)라면 테이블 탐색을 시도해도 무의미할 수 있다.

**권장 조치:**
- 현재 우선순위가 의도된 것이라면 주석으로 근거를 명시한다.
- 필요 시 ki_total == 0을 최우선으로 변경하거나, 두 조건 동시 성립 시 별도 failure_reason을 조립하는 것을 검토한다.

---

### [R-03] Info -- _parse_sql_response에서 status가 "success"이나 sql이 빈 문자열일 때의 처리

**파일:** `src/agents/nodes/reason/sql_generator.py:419-449`

**분석:** `_parse_sql_response`가 반환하는 dict에서 `status == "success"`이면서 `sql == ""`인 경우가 가능하다.

```python
# 예: LLM이 {"status": "success", "sql": "", ...}를 반환
return {
    "status": status,  # "success"
    "sql": data.get("sql", "").strip(),  # ""
    ...
}
```

이 경우 `sql_generator_node`의 분기에서:

```python
if result["status"] == "success" and result["sql"]:  # False (빈 문자열)
```

빈 문자열은 falsy이므로 `else` 분기로 정상 처리된다. 로직상 안전하다.

다만, `_parse_sql_response` 내부에서 `status == "success"`이면서 `sql`이 비어있을 때 `status`를 `"fail"`로 교정하는 것이 더 명확할 수 있다 (파서 레벨에서 불일치를 조기 정규화).

**현재 상태:** 안전. 개선은 선택적.

---

### [R-04] Info -- 타입 안전성: llm_call_with_parse_retry와의 호환

**파일:** `src/agents/nodes/reason/sql_generator.py:452-465`

`llm_call_with_parse_retry` 시그니처:
```python
async def llm_call_with_parse_retry(
    ...,
    parse_fn: Callable[[str], T],
    ...
) -> tuple[str, T]:
```

`_parse_sql_response`가 `str -> dict`이므로 `T = dict`로 추론된다. `_call_llm_for_sql`의 반환 타입 `dict`와 일치한다. **타입 안전성 문제 없음.**

단, `dict`보다 `TypedDict`나 dataclass로 반환 타입을 명시하면 호출부에서 키 접근 시 정적 분석 이점을 얻을 수 있다. 이는 향후 개선 사항.

---

### [R-05] Info -- recovery_agent.py의 SelectionStatus 지역 import

**파일:** `src/agents/nodes/reason/recovery_agent.py:371, 483`

```python
# 371행 (_build_prompt 내)
from src.agents.state.state import SelectionStatus

# 483행 (_build_sample_summary 내)
from src.agents.state.state import SelectionStatus
```

상단 import(31-43행)에 `SelectionStatus`가 없고, 동일 모듈에서 2회 지역 import를 수행한다. Phase 1-2 변경 범위는 아니지만, 이 파일이 이번 변경의 대상이므로 함께 정리하는 것이 적절하다.

**권장 조치:** 상단 import 블록에 `SelectionStatus`를 추가하고, 371행과 483행의 지역 import를 제거한다.

---

### [R-06] Info -- docs/architecture/architecture.md에 NO_USE_CASE 잔존

**파일:** `docs/architecture/architecture.md:293, 1058`

```
293: | `SQL_STRUCTURAL`, `EMPTY_RESULT`, `DB_ERROR`, `NO_USE_CASE`, `NO_TABLE`, ...
1058: | FailureType | ... `NO_USE_CASE`, `NO_TABLE`, `TERM_UNRESOLVABLE` |
```

설계서 Phase 5에서 문서 갱신이 예정되어 있으나, 코드상 `NO_USE_CASE`는 이미 제거되어 문서와 코드 간 불일치가 존재한다. Phase 5 실행까지 이 상태가 유지되므로, 다른 개발자가 아키텍처 문서를 참조할 때 혼동이 발생할 수 있다.

**권장 조치:** Phase 5의 범위이므로 현 시점에서는 인지만 하되, Phase 5가 지연될 경우 우선 갱신을 검토한다.

---

### [R-07] Info -- _set_failure_context의 NO_KNOWLEDGE 분기에서 테이블 정보 미포함

**파일:** `src/agents/nodes/reason/readiness_gate.py:204-213`

```python
elif reason.failure_type == FailureType.NO_KNOWLEDGE:
    parts = [
        "질의 정규화(분해)에서 측정값/조건이 추출되지 않아 "
        "지식 항목이 생성되지 않았습니다.",
        "원본 질의를 다른 관점으로 재분해하거나, "
        "유사 SQL 이력을 참고하여 "
        "필요한 측정 항목을 파악해야 합니다.",
    ]
    parts.append(f"확신도 {score:.0%}로 생성 기준 미달")
```

NO_KNOWLEDGE 분기의 failure_reason에 `selected_count`, `explored_count`, `unresolved` 정보가 포함되지 않는다. R-02에서 언급한 대로 `selected_count == 0`이 우선 평가되므로 이 분기에 도달하려면 `selected_count > 0`이어야 하는데, 테이블은 있으나 지식 항목이 없다는 것은 recovery_agent에게 유용한 맥락이다.

**권장 조치:** NO_KNOWLEDGE 분기에도 `selected_count`/`explored_count` 정보를 포함하는 것을 검토한다.

---

### [R-08] Info -- sql_generator_node의 exception 핸들러에서 LLM 에러와 능동 거부가 동일 FailureType

**파일:** `src/agents/nodes/reason/sql_generator.py:288-296, 314-327`

설계서(3-4 변경점 C 하단 "중요 고려사항")에서 이미 인지하고 있는 사항이다. `TimeoutError`, `ConnectionError` 등 인프라 에러와 LLM의 정보 부족 판단이 모두 `GENERATION_FAILED`로 분류된다. failure_reason 텍스트로 구분 가능하나, recovery_agent가 인프라 에러에 대해 재탐색을 시도하는 것은 비효율적이다.

**현재 상태:** 설계서에서 인지. failure_reason 텍스트 기반 구분에 의존. 향후 `INFRA_ERROR` 등 별도 타입 분리를 검토할 수 있다.

---

## 3. 죽은 코드 점검

| 항목 | 결과 |
|------|------|
| `NO_USE_CASE` 참조 (src/ 내) | 없음 -- 완전 제거됨 |
| 이전 `_parse_sql_response(str -> str)` 호환 코드 | 없음 -- 깨끗하게 전환됨 |
| 이전 `_call_llm_for_sql(str -> str)` 관련 | 없음 |
| pipeline.py의 GENERATION_FAILED in sql_validator match | 죽은 코드 (R-01) |

---

## 4. 전체 일관성 점검

| 파일 | NO_USE_CASE 참조 | GENERATION_FAILED 대응 | 상태 |
|------|------------------|------------------------|------|
| `src/models/enums.py` | 제거됨 | 추가됨 | 완료 |
| `src/agents/state/state.py` | 제거됨 | Literal 포함 | 완료 |
| `src/agents/nodes/reason/readiness_gate.py` | 제거됨 | N/A | 완료 |
| `src/agents/nodes/reason/sql_generator.py` | N/A | 설정됨 | 완료 |
| `src/agents/nodes/reason/recovery_agent.py` | N/A | 분기 처리됨 | 완료 |
| `src/agents/graph/pipeline.py` | 제거됨 | 라우팅 추가됨 | 완료 (R-01 주의) |
| `src/agents/nodes/reason/sql_validator.py` | 없음 | N/A | Phase 4 대기 |
| `docs/architecture/architecture.md` | 잔존 | 미갱신 | Phase 5 대기 (R-06) |

---

## 5. 종합 평가

Phase 1-2 구현은 설계서와 정확히 일치하며, 핵심 변경(Enum 이름, 파싱 반환 타입, 노드 분기, 방어 로직)이 모두 올바르게 적용되었다. 타입 안전성, 로직 커버리지, 기존 유틸 재사용 측면에서 문제가 없다.

주요 조치 사항:
1. **R-01 (Warning):** pipeline.py sql_validator match-case에서 GENERATION_FAILED 제거 또는 entry_source 덮어쓰기 방어
2. **R-02 (Warning):** _set_failure_context의 우선순위 근거 주석 추가
3. **R-05 (Info):** recovery_agent.py SelectionStatus 지역 import를 상단으로 이동
4. **R-06 (Info):** Phase 5 실행 시 architecture.md의 NO_USE_CASE 갱신 확인

Critical 등급 이슈는 발견되지 않았다.
