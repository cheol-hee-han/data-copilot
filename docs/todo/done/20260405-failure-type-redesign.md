# FailureType 체계 재설계 + SQL Generator fail 경로 구현

> 작성일: 2026-04-05
> 상태: 설계 확정, 구현 대기
> 선행 작업: SQL Generator 프롬프트 success/fail 출력 형식 적용 완료
> 리뷰: `docs/reviews/code/20260405-failure-type-redesign-review-report.md`

---

## 1. 목적

### 1-1. 문제

**A. SQL Generator가 실패를 표현할 수단이 없음**

현재 sql_generator는 항상 SQL 문자열을 반환하고(`sql_generator.py:291`), 정보 부족으로 SQL을 생성할 수 없는 경우에도 빈 문자열 또는 불완전한 SQL을 내보낸다. 빈 SQL은 sql_validator에서 `SQL_SYNTAX`로 처리되어(`sql_validator.py:59-63`) recovery 경로로 진입하지만, "정보 부족으로 생성 거부"라는 실패 맥락이 소실된다.

프롬프트 개선으로 SQL Generator가 `{"status": "fail", "reasons": [...]}` 형태로 능동적 거부를 표현할 수 있게 되었으나, 이를 수신하는 노드/파이프라인 코드가 아직 미대응이다.

**B. FailureType 이름 부정확**

`NO_USE_CASE`는 "활용사례 미발견"으로, knowledge_items == 0인 상태를 정확히 표현하지 못한다. 실제 의미는 "질의 정규화에서 측정값/조건이 추출되지 않아 지식 항목이 없는 상태"이므로 `NO_KNOWLEDGE`가 적합하다.

**C. failure_reason이 recovery에 유용한 정보를 담지 못함**

- `NO_TABLE`: "후보 테이블이 0개"만 전달. 탐색은 했으나 모두 REJECTED인지, 아예 탐색하지 못한 것인지 구분 불가.
- `NO_KNOWLEDGE` (현 NO_USE_CASE): "지식 항목 없음"만 전달. 근본 원인(질의 정규화 실패)과 해결 방향(재정규화/유사 SQL 참조) 미안내.
- `EMPTY_RESULT`: "조건이 너무 좁거나 테이블이 부적절합니다"만 전달. 실행된 SQL을 포함하지 않아 recovery_agent가 원인 진단 불가.

**D. readiness_gate의 NO_TABLE 판정 기준 오류**

`readiness_gate.py:154`에서 `len(reason.explored_tables)`로 판정하지만, 탐색된 테이블이 있더라도 모두 REJECTED이면 사용 가능한 테이블이 없는 것이므로 SELECTED 기준이 맞다.

**E. sql_validator 안전장치의 EMPTY_RESULT 오판**

`sql_validator.py:120-132`에서 Layer2b가 PASS(의미적으로 정당한 SQL)인데 Layer3가 0건이면 무조건 FAIL 처리한다. "2025년 미래 대출 건수"처럼 0건이 정상인 경우에도 실패로 분류되는 문제.

**F. sqlglot 파싱 실패의 과도한 FAIL 처리**

`sql_validator.py:224-229`에서 sqlglot이 지원하지 않는 방언(Sybase IQ 등)의 SQL을 파싱하지 못하면 `SQL_SYNTAX`로 즉시 실패 처리한다. 정상 SQL인데 sqlglot 미지원일 뿐인 경우에도 실패가 되는 문제.

### 1-2. 목표

1. SQL Generator의 능동적 거부(`status: "fail"`)를 파이프라인이 인식하여 recovery_agent로 라우팅
2. FailureType 이름/의미 정확도 개선 (NO_USE_CASE → NO_KNOWLEDGE, GENERATION_FAILED 신규)
3. 모든 FailureType의 failure_reason을 recovery_agent가 실질적으로 활용 가능한 수준으로 강화
4. readiness_gate NO_TABLE 판정을 SELECTED 기준으로 교정
5. sql_validator EMPTY_RESULT 안전장치와 sqlglot 파싱 실패 처리 개선

---

## 2. 변경 대상 파일 목록

| # | 파일 | 변경 내용 | Phase |
|---|---|---|---|
| 1 | `src/models/enums.py` | NO_USE_CASE→NO_KNOWLEDGE, +GENERATION_FAILED | 1 |
| 2 | `src/agents/state/state.py` | DeadEnd default, Literal 확장 | 1 |
| 3 | `src/agents/nodes/reason/readiness_gate.py` | import, selected_count, failure_reason 분기 | 2 |
| 4 | `src/agents/nodes/reason/sql_generator.py` | 파싱 dict, 노드 success/fail 분기 | 2 |
| 5 | `src/agents/graph/pipeline.py` | 라우팅 분기, 엣지 맵, match-case | 3 |
| 6 | `src/agents/nodes/reason/recovery_agent.py` | _build_prompt 3분기, hypothesis None 방어 | 2 |
| 7 | `src/agents/nodes/reason/sql_validator.py` | sqlglot PASS 위임, EMPTY_RESULT 안전장치/feedback | 4 |
| 8 | `resources/prompts/reason/recovery_agent_system.txt` | 진입 경로 설명 확장 | 5 |
| 9 | `docs/architecture/architecture.md` | FailureType 문서 갱신 | 5 |

---

## 3. 상세 구현

### Phase 1: Enum / State 타입 정의 (의존성 최상위)

> 모든 소비 코드가 이 정의에 의존하므로 반드시 먼저 변경한다.

#### 3-1. `src/models/enums.py` — FailureType Enum 변경

**파일 위치:** 87-98행

**현재 코드:**
```python
class FailureType(str, Enum):
    """SQL 생성/검증 실패 유형."""

    NO_USE_CASE = "NO_USE_CASE"              # 활용사례 미발견
    NO_TABLE = "NO_TABLE"                    # 후보 테이블 없음
    TERM_UNRESOLVABLE = "TERM_UNRESOLVABLE"  # 용어 매핑 불가
    SQL_SYNTAX = "SQL_SYNTAX"                # SQL 구문 오류
    SQL_SEMANTIC_LOCAL = "SQL_SEMANTIC_LOCAL"  # 의미 오류 (로컬 수정 가능)
    SQL_STRUCTURAL = "SQL_STRUCTURAL"        # 구조적 오류 (재계획 필요)
    EMPTY_RESULT = "EMPTY_RESULT"            # 빈 결과
    DB_ERROR = "DB_ERROR"                    # DB 실행 오류
```

**변경 후:**
```python
class FailureType(str, Enum):
    """SQL 생성/검증 실패 유형."""

    NO_KNOWLEDGE = "NO_KNOWLEDGE"            # 지식 항목 없음 (질의 정규화 실패)
    NO_TABLE = "NO_TABLE"                    # 사용 가능 테이블 없음
    TERM_UNRESOLVABLE = "TERM_UNRESOLVABLE"  # 용어 매핑 불가
    SQL_SYNTAX = "SQL_SYNTAX"                # SQL 구문 오류
    SQL_SEMANTIC_LOCAL = "SQL_SEMANTIC_LOCAL"  # 의미 오류 (로컬 수정 가능)
    SQL_STRUCTURAL = "SQL_STRUCTURAL"        # 구조적 오류 (재계획 필요)
    EMPTY_RESULT = "EMPTY_RESULT"            # 빈 결과
    DB_ERROR = "DB_ERROR"                    # DB 실행 오류
    GENERATION_FAILED = "GENERATION_FAILED"  # SQL Generator가 정보 부족으로 생성 거부
```

**변경 포인트:**
- `NO_USE_CASE` → `NO_KNOWLEDGE`: 이름과 값(`"NO_KNOWLEDGE"`) 모두 변경
- `GENERATION_FAILED` 신규 추가: SQL Generator LLM이 정보 부족을 판단하여 능동적으로 거부한 경우
- 주석도 의미에 맞게 갱신

---

#### 3-2. `src/agents/state/state.py` — DeadEnd 기본값 + Literal 확장

**변경점 A: DeadEnd.failure_type 기본값 (315행)**

현재:
```python
class DeadEnd(BaseModel):
    """실패한 탐색 경로 기록."""

    hypothesis_id: str
    failure_type: FailureType = FailureType.NO_USE_CASE
    reason: str = ""
    lessons_learned: str = ""
```

변경:
```python
class DeadEnd(BaseModel):
    """실패한 탐색 경로 기록."""

    hypothesis_id: str
    failure_type: FailureType = FailureType.NO_KNOWLEDGE
    reason: str = ""
    lessons_learned: str = ""
```

**변경점 B: recovery_entry_source Literal 확장 (535-537행)**

현재:
```python
    recovery_entry_source: Literal[
        "readiness_gate", "sql_validator", None,
    ] = None
```

변경:
```python
    recovery_entry_source: Literal[
        "readiness_gate", "sql_validator", "sql_generator", None,
    ] = None
```

**중요:** Pydantic v2에서 Literal 검증이 활성화되어 있으므로, sql_generator_node에서 `reason.recovery_entry_source = "sql_generator"`를 설정하기 전에 이 Literal 변경이 선행되어야 한다. 미변경 시 `ValidationError` 발생.

---

### Phase 2: 노드 구현 변경

#### 3-3. `src/agents/nodes/reason/readiness_gate.py` — 3가지 변경

**변경점 A: import 추가 (31-38행)**

현재:
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

변경:
```python
from src.agents.state.state import (
    ConfidenceStatus,
    FailureType,
    Phase,
    PipelineState,
    ReasoningState,
    SelectionStatus,
    StepStatus,
)
```

**변경점 B+C: `_set_failure_context` 전체 재작성 (146-190행)**

현재 코드 (146-190행):
```python
def _set_failure_context(
    reason: ReasoningState, score: float,
) -> None:
    """REPLAN 판정 시 failure_type과 failure_reason을 설정한다."""
    ki_total = len(reason.knowledge_items)
    ki_confirmed = len([
        i for i in reason.knowledge_items if i.confidence >= 0.8
    ])
    ct_count = len(reason.explored_tables)
    unresolved = [
        ki.key for ki in reason.knowledge_items
        if ki.status in (
            ConfidenceStatus.UNRESOLVED,
            ConfidenceStatus.CONFLICTED,
        )
    ]

    # failure_type 결정
    if ct_count == 0:
        reason.failure_type = FailureType.NO_TABLE
    elif ki_total == 0:
        reason.failure_type = FailureType.NO_USE_CASE
    else:
        reason.failure_type = FailureType.TERM_UNRESOLVABLE

    # failure_reason 조립
    parts = ["SQL 생성에 필요한 정보가 부족합니다."]
    if ki_confirmed == 0:
        parts.append(
            f"테이블·컬럼 매핑이 확인된 지식 항목이 "
            f"없습니다 (전체 {ki_total}건 중 확정 0건)"
        )
    else:
        parts.append(f"확정된 지식: {ki_confirmed}/{ki_total}건")
    if ct_count == 0:
        parts.append(
            "후보 테이블이 0개로, "
            "데이터 소스를 특정하지 못했습니다"
        )
    else:
        parts.append(f"후보 테이블: {ct_count}개")
    if unresolved:
        parts.append("미해소 용어: " + ", ".join(unresolved[:5]))
    parts.append(f"확신도 {score:.0%}로 생성 기준 미달")
    reason.failure_reason = "\n- ".join(parts)
```

변경 후 (전체 함수 교체):
```python
def _set_failure_context(
    reason: ReasoningState, score: float,
) -> None:
    """REPLAN 판정 시 failure_type과 failure_reason을 설정한다.

    failure_type별로 recovery_agent가 활용할 수 있는 구체적인
    failure_reason을 조립한다.
    """
    ki_total = len(reason.knowledge_items)
    ki_confirmed = len([
        i for i in reason.knowledge_items if i.confidence >= 0.8
    ])
    explored_count = len(reason.explored_tables)
    selected_count = len([
        t for t in reason.explored_tables
        if t.selection_status == SelectionStatus.SELECTED
    ])
    unresolved = [
        ki.key for ki in reason.knowledge_items
        if ki.status in (
            ConfidenceStatus.UNRESOLVED,
            ConfidenceStatus.CONFLICTED,
        )
    ]

    # ── failure_type 결정 (SELECTED 기준) ──
    if selected_count == 0:
        reason.failure_type = FailureType.NO_TABLE
    elif ki_total == 0:
        reason.failure_type = FailureType.NO_KNOWLEDGE
    else:
        reason.failure_type = FailureType.TERM_UNRESOLVABLE

    # ── failure_reason 조립 (타입별 분기) ──
    if reason.failure_type == FailureType.NO_TABLE:
        if explored_count == 0:
            table_msg = (
                "후보 테이블이 0개로, "
                "데이터 소스를 특정하지 못했습니다."
            )
        else:
            table_msg = (
                f"탐색된 테이블 {explored_count}개가 "
                f"모두 부적합(REJECTED) 판정되어 "
                "사용 가능한 테이블이 없습니다."
            )
        parts = [
            "SQL 생성에 필요한 테이블이 확보되지 않았습니다.",
            table_msg,
        ]
        if unresolved:
            parts.append(
                "미해소 용어: " + ", ".join(unresolved[:5]),
            )
        parts.append(f"확신도 {score:.0%}로 생성 기준 미달")
        reason.failure_reason = "\n- ".join(parts)

    elif reason.failure_type == FailureType.NO_KNOWLEDGE:
        parts = [
            "질의 정규화(분해)에서 측정값·조건이 추출되지 않아 "
            "지식 항목이 생성되지 않았습니다.",
            "원본 질의를 다른 관점으로 재분해하거나, "
            "유사 SQL 이력을 참고하여 "
            "필요한 측정 항목을 파악해야 합니다.",
        ]
        parts.append(f"확신도 {score:.0%}로 생성 기준 미달")
        reason.failure_reason = "\n- ".join(parts)

    else:  # TERM_UNRESOLVABLE
        parts = ["SQL 생성에 필요한 정보가 부족합니다."]
        if ki_confirmed == 0:
            parts.append(
                f"테이블·컬럼 매핑이 확인된 지식 항목이 "
                f"없습니다 (전체 {ki_total}건 중 확정 0건)",
            )
        else:
            parts.append(
                f"확정된 지식: {ki_confirmed}/{ki_total}건",
            )
        parts.append(
            f"후보 테이블: {selected_count}개 "
            f"(탐색 {explored_count}개)",
        )
        if unresolved:
            parts.append(
                "미해소 용어: " + ", ".join(unresolved[:5]),
            )
        parts.append(f"확신도 {score:.0%}로 생성 기준 미달")
        reason.failure_reason = "\n- ".join(parts)
```

**변경 근거:**
- `ct_count` → `selected_count`: 탐색했지만 모두 REJECTED이면 사용 가능 테이블 없음 (NO_TABLE 판정이 맞음)
- `NO_USE_CASE` → `NO_KNOWLEDGE`: Enum 이름 변경 반영
- failure_reason 타입별 분기: recovery_agent가 각 실패 유형에 맞는 복구 전략을 수립할 수 있도록 구체적 사유 제공
- NO_KNOWLEDGE의 경우: "질의 정규화 재시도" 또는 "유사 SQL 참조" 방향을 안내 (향후 normalize_query 도구 추가 대비)

---

#### 3-4. `src/agents/nodes/reason/sql_generator.py` — 파싱 + 노드 로직 변경

**변경점 A: `_parse_sql_response` 반환 타입 str → dict (397-412행)**

현재:
```python
def _parse_sql_response(raw: str) -> str:
    """LLM 응답에서 SQL을 추출한다.

    JSON 형식 → 마크다운 코드 블록 → raw 텍스트 순으로 시도.
    """
    data = extract_json(raw)
    if data:
        sql = data.get("sql", "")
        if sql:
            return sql

    cleaned = _clean_sql_response(raw)
    if cleaned:
        return cleaned

    raise ValueError("SQL을 추출할 수 없음: JSON 'sql' 키 없음, 코드 블록 없음")
```

변경:
```python
def _parse_sql_response(raw: str) -> dict:
    """LLM 응답에서 SQL 생성 결과를 추출한다.

    반환 형식: {"status", "sql", "reasons", "explanation"}
    JSON 형식 → 마크다운 코드 블록 fallback 순으로 시도.
    """
    data = extract_json(raw)
    if data and isinstance(data, dict):
        status = data.get("status", "success")
        if status not in ("success", "fail"):
            status = "fail"
        return {
            "status": status,
            "sql": data.get("sql", "").strip(),
            "reasons": data.get("reasons", []),
            "explanation": data.get("explanation", ""),
        }

    # JSON 추출 실패 시: 코드 블록에서 SQL 추출 (기존 호환)
    cleaned = _clean_sql_response(raw)
    if cleaned:
        return {
            "status": "success",
            "sql": cleaned,
            "reasons": [],
            "explanation": "",
        }

    raise ValueError(
        "SQL을 추출할 수 없음: JSON 파싱 실패, 코드 블록 없음",
    )
```

**주의사항:**
- `llm_call_with_parse_retry`의 제네릭 타입 `T`가 `str` → `dict`로 변경됨
- `llm_call_with_parse_retry` 시그니처: `parse_fn: Callable[[str], T]` → 반환 `tuple[str, T]`
- `_call_llm_for_sql`의 타입 힌트도 연쇄 변경 필요

**변경점 B: `_call_llm_for_sql` 반환 타입 str → dict (415-428행)**

현재:
```python
async def _call_llm_for_sql(
    prompt: str,
    query: str,
) -> str:
    """LLM을 호출하여 SQL을 생성한다."""
    _, sql = await llm_call_with_parse_retry(
        system=prompt,
        messages=[{"role": "user", "content": query}],
        parse_fn=_parse_sql_response,
        max_tokens=settings.llm_format_max_tokens,
        timeout=settings.llm_long_timeout,
        node_name="agentic_SQL생성",
    )
    return sql
```

변경:
```python
async def _call_llm_for_sql(
    prompt: str,
    query: str,
) -> dict:
    """LLM을 호출하여 SQL 생성 결과를 반환한다."""
    _, result = await llm_call_with_parse_retry(
        system=prompt,
        messages=[{"role": "user", "content": query}],
        parse_fn=_parse_sql_response,
        max_tokens=settings.llm_format_max_tokens,
        timeout=settings.llm_long_timeout,
        node_name="agentic_SQL생성",
    )
    return result
```

**변경점 C: `sql_generator_node` success/fail 분기 (283-309행)**

현재 (283-309행):
```python
    try:
        generated = await _call_llm_for_sql(
            prompt, state.preprocessed_input,
        )
    except Exception as e:
        logger.error("SQL 생성 LLM 호출 오류", error=str(e))
        generated = ""

    reason.generated_sql = generated
    reason.failure_type = None
    reason.failure_reason = None

    # ── 추적: 생성된 SQL + 치환 변수 ──
    attempt = reason.loop_guard.generate_attempts
    table_names = [ct.qualified_name for ct in reason.explored_tables]
    logger.info(
        "SQL 생성 완료",
        dialect=dialect,
        attempt=attempt,
        tables=table_names,
        sql=("\n" + format_sql(generated, dialect))
        if generated else "(빈 SQL)",
    )

    await record_prompt_variables(prompt_vars)

    return {"reason": reason}
```

변경:
```python
    try:
        result = await _call_llm_for_sql(
            prompt, state.preprocessed_input,
        )
    except Exception as e:
        logger.error("SQL 생성 LLM 호출 오류", error=str(e))
        result = {
            "status": "fail",
            "sql": "",
            "reasons": [f"LLM 호출 오류: {type(e).__name__}"],
            "explanation": "",
        }

    # ── success / fail 분기 ──
    attempt = reason.loop_guard.generate_attempts
    table_names = [
        ct.qualified_name for ct in reason.explored_tables
    ]

    if result["status"] == "success" and result["sql"]:
        reason.generated_sql = result["sql"]
        reason.failure_type = None
        reason.failure_reason = None
        logger.info(
            "SQL 생성 완료",
            dialect=dialect,
            attempt=attempt,
            tables=table_names,
            sql="\n" + format_sql(result["sql"], dialect),
        )
    else:
        reason.generated_sql = None
        reason.failure_type = FailureType.GENERATION_FAILED
        reason.failure_reason = "\n".join(
            result.get("reasons") or ["SQL 생성 실패 (사유 미제공)"],
        )
        reason.recovery_entry_source = "sql_generator"
        logger.warning(
            "SQL 생성 거부",
            dialect=dialect,
            attempt=attempt,
            reasons=result.get("reasons", []),
        )

    await record_prompt_variables(prompt_vars)

    return {"reason": reason}
```

**중요 고려사항:**
- `FailureType` import 확인: 현재 `sql_generator.py:31-46`에서 `FailureType`이 import되어 있는지 확인 필요. 없으면 추가:
  ```python
  from src.agents.state.state import FailureType
  ```
- `reason.recovery_entry_source = "sql_generator"`: Phase 1에서 Literal에 `"sql_generator"` 추가가 선행되어야 함
- LLM 호출 오류(네트워크 등)와 LLM 능동 거부(정보 부족)가 모두 `GENERATION_FAILED`로 분류됨. failure_reason에 `"LLM 호출 오류: TimeoutError"` vs `"코드값 불명: LN_DCD의 '기업대출' 코드값을 확인할 수 없음"` 등으로 구분되므로, recovery_agent LLM이 reason 텍스트로 구분 가능

---

#### 3-5. `src/agents/nodes/reason/recovery_agent.py` — 2가지 변경

**변경점 A: `_build_prompt` entry_desc 분기 추가 (318-332행)**

현재:
```python
    # 진입 경로 설명
    entry_src = reason.recovery_entry_source or "readiness_gate"
    if entry_src == "sql_validator":
        ft = entry_failure_type or "미제공"
        fr = entry_failure_reason or "미제공"
        entry_desc = (
            f"sql_validator에서 진입: SQL 검증 실패.\n"
            f"실패 유형: {ft}\n"
            f"실패 사유: {fr}"
        )
    else:
        entry_desc = (
            "readiness_gate에서 진입: "
            "초기 탐색이 불충분하여 추가 탐색이 필요합니다."
        )
```

변경:
```python
    # 진입 경로 설명
    entry_src = reason.recovery_entry_source or "readiness_gate"
    ft = entry_failure_type or "미제공"
    fr = entry_failure_reason or "미제공"

    if entry_src == "sql_validator":
        entry_desc = (
            f"sql_validator에서 진입: SQL 검증 실패.\n"
            f"실패 유형: {ft}\n"
            f"실패 사유: {fr}"
        )
    elif entry_src == "sql_generator":
        entry_desc = (
            f"sql_generator에서 진입: "
            f"SQL 생성이 정보 부족으로 거부되었습니다.\n"
            f"실패 유형: {ft}\n"
            f"거부 사유:\n{fr}"
        )
    else:  # readiness_gate
        entry_desc = (
            f"readiness_gate에서 진입: "
            f"초기 탐색이 불충분합니다.\n"
            f"실패 유형: {ft}\n"
            f"상세 사유:\n{fr}"
        )
```

**변경 근거:**
- `sql_generator` 경로 추가: GENERATION_FAILED의 reasons를 recovery LLM에 전달
- `readiness_gate` 경로도 failure_type/reason 전달로 변경: 기존에는 "초기 탐색이 불충분" 한 줄만 전달했는데, NO_TABLE/NO_KNOWLEDGE/TERM_UNRESOLVABLE의 구체적 사유가 소실되었음. Phase 2의 readiness_gate failure_reason 강화와 연계하여 recovery LLM이 정확한 상황 파악 가능

**변경점 B: `_handle_hypothesis_transition` — current_hypothesis None 방어 (144-173행)**

현재 (144-173행):
```python
def _handle_hypothesis_transition(
    reason: ReasoningState,
) -> None:
    """현재 가설 FAILED 전환 + PENDING 소비 + DeadEnd 기록."""
    if (
        reason.current_hypothesis
        and reason.current_hypothesis.status
        == HypothesisStatus.ACTIVE
    ):
        failed = reason.current_hypothesis.model_copy()
        failed.status = HypothesisStatus.FAILED
        for i, h in enumerate(reason.hypotheses):
            if h.hypothesis_id == failed.hypothesis_id:
                reason.hypotheses[i] = failed
                break

        reason.dead_ends.append(DeadEnd(
            hypothesis_id=failed.hypothesis_id,
            failure_type=(
                reason.failure_type
                or FailureType.TERM_UNRESOLVABLE
            ),
            reason=reason.failure_reason or "실패 사유 미제공",
        ))

    next_hyp = _consume_next_pending(reason.hypotheses)
    if next_hyp:
        reason.current_hypothesis = next_hyp
    else:
        reason.current_hypothesis = None
```

변경 (ACTIVE 가설이 없더라도 failure_type이 있으면 DeadEnd 기록):
```python
def _handle_hypothesis_transition(
    reason: ReasoningState,
) -> None:
    """현재 가설 FAILED 전환 + PENDING 소비 + DeadEnd 기록."""
    if (
        reason.current_hypothesis
        and reason.current_hypothesis.status
        == HypothesisStatus.ACTIVE
    ):
        failed = reason.current_hypothesis.model_copy()
        failed.status = HypothesisStatus.FAILED
        for i, h in enumerate(reason.hypotheses):
            if h.hypothesis_id == failed.hypothesis_id:
                reason.hypotheses[i] = failed
                break

        reason.dead_ends.append(DeadEnd(
            hypothesis_id=failed.hypothesis_id,
            failure_type=(
                reason.failure_type
                or FailureType.TERM_UNRESOLVABLE
            ),
            reason=reason.failure_reason or "실패 사유 미제공",
        ))
    elif reason.failure_type:
        # 가설 없이 진입한 경우에도 DeadEnd를 기록하여
        # recovery LLM이 이전 실패 패턴을 인지할 수 있도록 한다.
        reason.dead_ends.append(DeadEnd(
            hypothesis_id="no_hypothesis",
            failure_type=reason.failure_type,
            reason=reason.failure_reason or "가설 없이 실패",
        ))

    next_hyp = _consume_next_pending(reason.hypotheses)
    if next_hyp:
        reason.current_hypothesis = next_hyp
    else:
        reason.current_hypothesis = None
```

**변경 근거:**
- sql_generator에서 GENERATION_FAILED로 recovery_agent에 진입할 때, `_apply_force_generate` 경로 등에서 `current_hypothesis`가 None일 수 있음
- current_hypothesis가 None이면 DeadEnd가 기록되지 않아, recovery LLM이 동일 시도를 반복할 위험이 있음
- `elif reason.failure_type:` 조건으로 failure_type이 있을 때만 기록 (정상 진입 시 불필요한 DeadEnd 방지)

---

### Phase 3: 파이프라인 라우팅

#### 3-6. `src/agents/graph/pipeline.py` — 3가지 변경

**변경점 A: `_route_after_sql_generator` — GENERATION_FAILED 분기 추가 (187-193행)**

현재:
```python
def _route_after_sql_generator(
    state: PipelineState,
) -> str:
    """sql_generator 후 라우팅 — pending_signals(Cross-DB INFER) 우선."""
    if state.pending_signals:
        return "clarification_handler"
    return "sql_validator"
```

변경:
```python
def _route_after_sql_generator(
    state: PipelineState,
) -> str:
    """sql_generator 후 라우팅.

    3가지 분기:
      1. GENERATION_FAILED → recovery_agent (정보 보충 후 재시도)
      2. pending_signals → clarification_handler (Cross-DB INFER)
      3. 정상 → sql_validator (검증)
    """
    if state.reason.failure_type == FailureType.GENERATION_FAILED:
        return "replan"
    if state.pending_signals:
        return "clarification_handler"
    return "sql_validator"
```

**변경점 B: sql_generator 엣지 맵에 "replan" 추가 (458-465행)**

현재:
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

변경:
```python
    workflow.add_conditional_edges(
        "sql_generator",
        _route_after_sql_generator,
        {
            "sql_validator": "sql_validator",
            "clarification_handler": "clarification_handler",
            "replan": "recovery_agent",
        },
    )
```

**중요:** LangGraph는 엣지 맵에 없는 반환값을 받으면 런타임 에러를 발생시킨다. `"replan"` 키가 없으면 `_route_after_sql_generator`가 `"replan"`을 반환할 때 즉시 크래시.

**변경점 C: `_route_after_sql_validator` match-case — NO_USE_CASE → NO_KNOWLEDGE + GENERATION_FAILED 방어 (231-242행)**

현재:
```python
        case (
            FailureType.SQL_STRUCTURAL
            | FailureType.EMPTY_RESULT
            | FailureType.DB_ERROR
            | FailureType.NO_USE_CASE
            | FailureType.NO_TABLE
            | FailureType.TERM_UNRESOLVABLE
        ):
            state.reason.recovery_entry_source = (
                "sql_validator"
            )
            return "replan"
```

변경:
```python
        case (
            FailureType.SQL_STRUCTURAL
            | FailureType.EMPTY_RESULT
            | FailureType.DB_ERROR
            | FailureType.NO_KNOWLEDGE
            | FailureType.NO_TABLE
            | FailureType.TERM_UNRESOLVABLE
            | FailureType.GENERATION_FAILED
        ):
            state.reason.recovery_entry_source = (
                "sql_validator"
            )
            return "replan"
```

**비고:** GENERATION_FAILED는 정상 경로에서는 `_route_after_sql_generator`에서 처리되어 sql_validator에 도달하지 않는다. 여기에 포함하는 것은 방어적 코딩이며, 도달 시 `recovery_entry_source`가 `"sql_validator"`로 설정되는 것이 부정확하지만, 실제로는 도달하지 않으므로 무방하다.

**추가 고려:** 기존 `_route_after_sql_validator` 내부에서 `state.reason.recovery_entry_source = "sql_validator"`를 설정하는 패턴(224행, 239행)은 LangGraph의 "조건부 엣지는 순수 함수" 원칙에 어긋난다. 이번 변경에서는 기존 패턴을 유지하되, 향후 리팩터링 시 state mutation을 노드 내부로 이동시키는 것이 바람직하다.

---

### Phase 4: sql_validator 개선

#### 3-7. `src/agents/nodes/reason/sql_validator.py` — 3가지 변경

**변경점 A: Layer1 — sqlglot 파싱 실패 시 PASS 위임 (224-229행)**

현재:
```python
    # 2. dialect별 sqlglot 파싱
    ast = parse_sql_safe(sql, dialect=dialect)
    if ast is None:
        return {
            "status": "FAIL",
            "feedback": (f"SQL 파싱 실패 ({dialect} 문법 오류)"),
        }
```

변경:
```python
    # 2. dialect별 sqlglot 파싱
    ast = parse_sql_safe(sql, dialect=dialect)
    if ast is None:
        # sqlglot이 지원하지 않는 방언일 수 있음
        # 안전성 검증(DML/DDL/시스템카탈로그)은 위에서 통과했으므로
        # Layer3(DB 실행)에 위임하여 실제 유효성을 확인한다.
        logger.warning(
            "sqlglot 파싱 실패 — Layer3(DB 실행)으로 위임",
            dialect=dialect,
        )
        return {"status": "PASS"}
```

**스킵되는 검증 목록:**
1. Layer1 단계 3 (232-248행): 사용 테이블이 explored_tables에 존재하는지
2. Layer1 단계 4 (250-263행): 사용 컬럼이 explored_tables 컬럼 범위 안인지

**안전성 분석:**
- Layer1 단계 1(안전성 검증 — DML/DDL/시스템카탈로그)은 sqlglot 파싱 이전에 실행되므로 영향 없음
- Layer2a는 문자열 기반이므로 ast 없이도 정상 동작
- 테이블/컬럼 검증 스킵: DB가 읽기 전용이므로 보안 위험은 없으나, LLM이 hallucinate한 테이블명이 DB 실행까지 가는 것은 불필요한 자원 낭비. 다만 DB 실행 시 "테이블 없음" 에러로 `DB_ERROR`가 되어 recovery 경로로 라우팅되므로 최종 결과에는 문제 없음

**변경점 B: Layer3 — EMPTY_RESULT feedback 강화 (487-495행)**

현재:
```python
        if row_count == 0:
            return {
                "status": "FAIL",
                "failure_type": FailureType.EMPTY_RESULT,
                "row_count": 0,
                "feedback": (
                    "실행 결과 0건 — 조건이 너무 좁거나 " "테이블이 부적절합니다"
                ),
            }
```

변경:
```python
        if row_count == 0:
            return {
                "status": "FAIL",
                "failure_type": FailureType.EMPTY_RESULT,
                "row_count": 0,
                "feedback": (
                    "정상적으로 SQL을 생성하고 조회했으나 "
                    "데이터가 0건입니다.\n"
                    "조건이 과도하게 제한적이거나, "
                    "해당 기간에 데이터가 없을 수 있습니다.\n"
                    f"현재 SQL:\n{sql[:500]}"
                ),
            }
```

**변경 근거:**
- 기존 "테이블이 부적절합니다"는 부정확 (테이블은 적합하나 조건이 문제일 수 있음)
- SQL 전문을 포함하여 recovery_agent가 0건 원인을 진단할 수 있도록 함
- `sql[:500]`: DeadEnd.reason에 저장될 때 토큰 낭비를 방지하기 위해 truncate

**변경점 C: 안전장치 — Layer2b PASS + Layer3 EMPTY_RESULT 존중 (120-132행)**

현재:
```python
        # 안전장치: Layer2b가 PASS인데 Layer3가 FAIL이면
        # LLM이 DB 에러를 간과한 것이므로 Layer3 실패를 반영한다.
        if layer3_result["status"] == "FAIL":
            layer3_failure = layer3_result.get(
                "failure_type", FailureType.EMPTY_RESULT,
            )
            reason.failure_type = layer3_failure
            reason.failure_reason = layer3_result["feedback"]
            logger.warning(
                "SQL 검증 실패: Layer3(실행, Layer2b PASS이나 DB 에러)",
                failure_type=layer3_failure,
            )
            return {"reason": reason}
```

변경:
```python
        # 안전장치: Layer2b가 PASS인데 Layer3가 FAIL이면
        # DB_ERROR는 LLM이 간과한 것이므로 Layer3 실패를 반영한다.
        # 단, EMPTY_RESULT(0건)은 Layer2b가 의미적으로 PASS 판정한
        # 것이므로 정당한 0건일 수 있다 (예: 미래 날짜 조회).
        if layer3_result["status"] == "FAIL":
            layer3_failure = layer3_result.get(
                "failure_type", FailureType.EMPTY_RESULT,
            )
            if layer3_failure != FailureType.EMPTY_RESULT:
                reason.failure_type = layer3_failure
                reason.failure_reason = layer3_result["feedback"]
                logger.warning(
                    "SQL 검증 실패: Layer3"
                    "(실행, Layer2b PASS이나 DB 에러)",
                    failure_type=layer3_failure,
                )
                return {"reason": reason}
            # EMPTY_RESULT + Layer2b PASS → 정당한 0건으로 판정
            logger.info(
                "Layer3 0건이나 Layer2b PASS — "
                "정당한 0건으로 판정",
            )
```

**변경 근거:**
- Layer2b는 "SQL이 사용자 질의의 의도에 부합하는가"를 LLM이 판단하는 단계
- Layer2b PASS = "이 SQL은 질의 의도에 맞다"
- 이 상태에서 0건은 "조건에 맞는 데이터가 실제로 없다"일 수 있음 (예: "2025년 12월 이후 신규 대출" → 미래이므로 0건이 정상)
- DB_ERROR(테이블 없음, 문법 오류 등)는 Layer2b가 간과한 것이므로 여전히 FAIL 처리
- 참고: `sql_validator_system.txt` 규칙 7: "0건 자체를 실패로 판정하지 말고 의도 부합 여부로 판단하라"와 일관

---

### Phase 5: 프롬프트 / 문서

#### 3-8. `resources/prompts/reason/recovery_agent_system.txt` — 진입 경로 설명 확장

**현재 (8-11행):**
```
## 진입 경로
{entry_source_description}
  - readiness_gate: 초기 탐색이 불충분하여 추가 탐색이 필요합니다. 넓은 범위에서 공백을 채우세요.
  - sql_validator: SQL 검증이 실패했습니다. 실패 원인에 집중하세요.
```

**변경:**
```
## 진입 경로
{entry_source_description}
  - readiness_gate: 초기 탐색이 불충분하여 추가 탐색이 필요합니다. 넓은 범위에서 공백을 채우세요.
  - readiness_gate(NO_KNOWLEDGE): 질의 정규화에서 측정값·조건이 추출되지 않았습니다.
    유사 SQL을 참고하여 질의를 재해석하거나, 사용자에게 구체적인 항목을 확인하세요.
  - sql_validator: SQL 검증이 실패했습니다. 실패 원인에 집중하세요.
  - sql_generator: SQL 생성이 정보 부족으로 거부되었습니다. 거부 사유(reasons)를 확인하고 해당 정보를 채우는 탐색을 계획하세요.
```

**비고:** 이 설명은 `{entry_source_description}` 아래에 위치하여 LLM이 어떤 진입 경로인지 판단할 때 참조하는 가이드 역할을 한다. `{entry_source_description}` 자체에는 `_build_prompt`에서 동적으로 실패 유형과 사유가 치환된다.

---

#### 3-9. `docs/architecture/architecture.md` — FailureType 문서 갱신

`architecture.md` 내 `NO_USE_CASE` 참조 (293행, 1058행 등)를 `NO_KNOWLEDGE`로 변경하고, `GENERATION_FAILED` 설명을 추가한다.

---

## 4. FailureType 전체 현황 (변경 후)

| FailureType | 발생 노드 | 트리거 조건 | failure_reason 내용 | recovery 행동 |
|---|---|---|---|---|
| `NO_TABLE` | readiness_gate | SELECTED 테이블 0개 | "테이블 확보 안 됨" + 탐색/REJECTED 상세 | 테이블 재탐색 |
| `NO_KNOWLEDGE` | readiness_gate | knowledge_items == 0 | "질의 정규화 실패, 재분해 또는 유사 SQL 참조 필요" | 유사 SQL 검색 → 재정규화 (향후 normalize_query 도구) |
| `TERM_UNRESOLVABLE` | readiness_gate | 미해소 용어 존재 | "정보 부족" + 확정/미해소 건수 + 테이블 수 | 미해소 용어 대상 추가 탐색 |
| `SQL_SYNTAX` | sql_validator L1 | sqlglot 파싱 실패 (파싱 가능 방언만) / 안전성 위반 | sqlglot 에러 메시지 | sql_generator 재시도 (fix_syntax) |
| `SQL_SEMANTIC_LOCAL` | sql_validator L2b | LLM이 의미 오류 판정 + 로컬 수정 가능 | LLM의 check 항목별 판정 | sql_generator 재시도 (fix_local) |
| `SQL_STRUCTURAL` | sql_validator L2a/L2b | 구조적 결함 (GROUP BY 불일치 등) | 구조 검증 실패 상세 | recovery_agent (replan) |
| `EMPTY_RESULT` | sql_validator L3 | DB 실행 0건 (Layer2b PASS 시 제외) | "0건" + SQL 전문 (500자 truncate) | recovery_agent (조건/테이블 재검토) |
| `DB_ERROR` | sql_validator L3 | DB 실행 오류 | DB 에러 메시지 | recovery_agent (SQL 재구성) |
| `GENERATION_FAILED` | sql_generator | LLM이 status:"fail" 반환 / LLM 호출 오류 | LLM의 reasons 배열 join | recovery_agent (정보 보충) |

---

## 5. 파이프라인 라우팅 흐름 (변경 후)

```
sql_generator
  ├─ GENERATION_FAILED → "replan" → recovery_agent
  ├─ pending_signals   → "clarification_handler"
  └─ 정상(sql 있음)    → "sql_validator"

sql_validator
  ├─ None (전체 통과)  → "conclude_success" → result_finalizer
  ├─ SQL_SYNTAX        → "fix_syntax"       → sql_generator (재시도)
  ├─ SQL_SEMANTIC_LOCAL → "fix_local"        → sql_generator (재시도)
  │                    → "replan"           → recovery_agent (에스컬레이션)
  ├─ SQL_STRUCTURAL    ─┐
  │  EMPTY_RESULT      ─┤→ "replan"         → recovery_agent
  │  DB_ERROR          ─┤
  │  NO_KNOWLEDGE      ─┤
  │  NO_TABLE          ─┤
  │  TERM_UNRESOLVABLE ─┤
  │  GENERATION_FAILED ─┘  (방어, 정상 도달 안 함)
  └─ 기타              → "conclude_failure" → result_finalizer
```

---

## 6. recovery_agent 진입 경로별 프롬프트 치환 예시

### 6-1. readiness_gate (NO_TABLE) 진입

```
{entry_source_description} =
  readiness_gate에서 진입: 초기 탐색이 불충분합니다.
  실패 유형: NO_TABLE
  상세 사유:
  SQL 생성에 필요한 테이블이 확보되지 않았습니다.
  - 탐색된 테이블 3개가 모두 부적합(REJECTED) 판정되어 사용 가능한 테이블이 없습니다.
  - 확신도 15%로 생성 기준 미달
```

### 6-2. readiness_gate (NO_KNOWLEDGE) 진입

```
{entry_source_description} =
  readiness_gate에서 진입: 초기 탐색이 불충분합니다.
  실패 유형: NO_KNOWLEDGE
  상세 사유:
  질의 정규화(분해)에서 측정값·조건이 추출되지 않아 지식 항목이 생성되지 않았습니다.
  - 원본 질의를 다른 관점으로 재분해하거나, 유사 SQL 이력을 참고하여 필요한 측정 항목을 파악해야 합니다.
  - 확신도 0%로 생성 기준 미달
```

### 6-3. sql_generator (GENERATION_FAILED) 진입

```
{entry_source_description} =
  sql_generator에서 진입: SQL 생성이 정보 부족으로 거부되었습니다.
  실패 유형: GENERATION_FAILED
  거부 사유:
  코드값 불명: LN_DCD(대출구분코드)에서 '기업대출'에 해당하는 코드값을 확인할 수 없음
  테이블 컬럼 용도 불명: TB_ADW_LNB302M.OVD_AMT가 연체금액인지 연체잔액인지 구분 불가
```

### 6-4. sql_validator (EMPTY_RESULT) 진입

```
{entry_source_description} =
  sql_validator에서 진입: SQL 검증 실패.
  실패 유형: EMPTY_RESULT
  실패 사유: 정상적으로 SQL을 생성하고 조회했으나 데이터가 0건입니다.
  조건이 과도하게 제한적이거나, 해당 기간에 데이터가 없을 수 있습니다.
  현재 SQL:
  SELECT COUNT(*) FROM ADWOWN.TB_ADW_LNB301M WHERE LN_DT >= '20260301' AND LN_DCD = '01'
```

---

## 7. 구현 순서 및 체크리스트

**의존성 기반 실행 순서**: Enum → State → Nodes → Pipeline → Prompts → Docs

| 순서 | 파일 | 변경 | 비고 |
|---|---|---|---|
| 1 | `src/models/enums.py:87-98` | NO_USE_CASE→NO_KNOWLEDGE, +GENERATION_FAILED | 값 문자열도 변경 |
| 2 | `src/agents/state/state.py:315` | DeadEnd default → NO_KNOWLEDGE | |
| 3 | `src/agents/state/state.py:535-537` | Literal에 "sql_generator" 추가 | Pydantic 검증 선행 필수 |
| 4 | `src/agents/nodes/reason/readiness_gate.py:31-38` | SelectionStatus import 추가 | |
| 5 | `src/agents/nodes/reason/readiness_gate.py:146-190` | _set_failure_context 전체 교체 | selected_count + 타입별 reason |
| 6 | `src/agents/nodes/reason/sql_generator.py:397-412` | _parse_sql_response → dict 반환 | |
| 7 | `src/agents/nodes/reason/sql_generator.py:415-428` | _call_llm_for_sql → dict 반환 | |
| 8 | `src/agents/nodes/reason/sql_generator.py:283-309` | sql_generator_node success/fail 분기 | FailureType import 확인 |
| 9 | `src/agents/nodes/reason/recovery_agent.py:318-332` | _build_prompt 3분기 (validator/generator/gate) | |
| 10 | `src/agents/nodes/reason/recovery_agent.py:144-173` | _handle_hypothesis_transition None 방어 | |
| 11 | `src/agents/graph/pipeline.py:187-193` | _route_after_sql_generator GENERATION_FAILED 분기 | |
| 12 | `src/agents/graph/pipeline.py:458-465` | 엣지 맵에 "replan" 추가 | 누락 시 런타임 크래시 |
| 13 | `src/agents/graph/pipeline.py:231-242` | match-case NO_USE_CASE→NO_KNOWLEDGE + GENERATION_FAILED | |
| 14 | `src/agents/nodes/reason/sql_validator.py:224-229` | sqlglot 파싱 실패 → PASS | |
| 15 | `src/agents/nodes/reason/sql_validator.py:487-495` | EMPTY_RESULT feedback SQL 포함 | |
| 16 | `src/agents/nodes/reason/sql_validator.py:120-132` | EMPTY_RESULT 안전장치 Layer2b PASS 존중 | |
| 17 | `resources/prompts/reason/recovery_agent_system.txt:8-11` | 진입 경로 4개 | |
| 18 | `docs/architecture/architecture.md` | NO_USE_CASE → NO_KNOWLEDGE, +GENERATION_FAILED | |

---

## 8. 향후 확장 포인트

### 8-1. normalize_query 도구

NO_KNOWLEDGE 복구 시 recovery_agent가 `normalize_query` 도구를 execution_plan에 포함하여 질의 재정규화를 시도할 수 있다. 현재는 도구가 없으므로 유사 SQL 검색 → 수동 재해석 경로만 사용하지만, 도구 추가 시:

1. `recovery_agent_system.txt`의 도구 목록에 `normalize_query(query)` 추가
2. `tool_executor`에 normalizer LLM 호출 구현
3. 결과를 `decomposition` 필드에 반영하여 `_initialize_knowledge_items` 재실행

### 8-2. GENERATION_FAILED 세분화

현재 LLM 호출 오류(네트워크)와 LLM 능동 거부(정보 부족)가 동일 타입이다. failure_reason 텍스트로 구분 가능하지만, 필요 시:
- `GENERATION_FAILED` → LLM 능동 거부
- `LLM_ERROR` → 네트워크/타임아웃 등 인프라 오류 (재시도 적절)

### 8-3. pipeline.py state mutation 제거

현재 `_route_after_sql_validator`(224행, 239행)에서 `state.reason.recovery_entry_source`를 직접 설정하는 것은 LangGraph 순수 함수 원칙 위반이다. 향후 sql_validator_node 내부에서 failure_type에 따라 recovery_entry_source를 미리 설정하는 방식으로 리팩터링 가능.
