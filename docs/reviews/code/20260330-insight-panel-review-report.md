# Insight Panel 개선 코드 리뷰 리포트

**일시**: 2026-03-30
**대상 파일**:
- `src/agents/state/state.py` (ReasoningState.validation_checks 필드 추가)
- `src/agents/nodes/reason/sql_validator.py` (Layer2b PASS 시 checks 저장)
- `src/agents/nodes/reason/result_finalizer.py` (exploration_summary 보존 로직)
- `src/services/insight_builder.py` (4개 신규 함수 + is_success 플래그)
- `static/embedded.html` (renderInsight 성공/실패 분기, CSS)

---

## 1. Data Flow Correctness (데이터 흐름 정확성)

### 1-1. validation_checks 흐름: PASS

sql_validator_node (line 100) -> reason.validation_checks에 저장 ->
result_finalizer에서 reason이 그대로 전달 ->
runner._build_safe_insight(result) -> build_insight(state) ->
_build_validation_detail(reason) -> UI ins.validation_detail

데이터 흐름이 끊기지 않고 정상적으로 연결됨.

### 1-2. is_success 흐름: PASS

`build_insight` (line 34-36)에서 `reason.validated_sql` 존재 여부로 판단하고,
UI에서 `ins.is_success`로 분기. 논리적으로 정확함.

### 1-3. failure_narrative 흐름: PASS

`_build_failure_narrative`에서 `exploration_summary` -> `error_message` 우선순위로
내러티브를 구성. `result_finalizer`가 실패 시 `error_message`에도 동일 값을 설정하므로
경로가 정상적으로 연결됨.

---

## 2. Critical Issues (즉시 수정 필요)

### 2-1. [CRITICAL] validation_checks 타입 힌트와 실제 데이터 불일치

**파일**: `src/agents/state/state.py` line 424
**현상**: 타입 힌트가 `dict[str, dict[str, str]]`이지만, LLM이 반환하는 실제 구조는 `{"pass": bool, "detail": str}` 형태.

```python
# state.py line 424
validation_checks: dict[str, dict[str, str]] = Field(default_factory=dict)
```

```json
// 실제 LLM 응답 (sql_validator_system.txt line 48-54)
"measure_reflected": {"pass": true, "detail": "..."}
```

`pass` 필드가 `bool`인데 타입 힌트는 `dict[str, str]`이다. Pydantic v2의 strict 모드에서는 `True`가 `"True"`로 자동 변환되지 않으므로, 만약 Pydantic validation이 활성화되면 런타임 에러가 발생할 수 있다.

또한 `_build_validation_detail` (insight_builder.py line 469)에서 `value.get("pass", True)`로 접근하는데, 타입 힌트와 불일치하므로 mypy --strict에서 오류가 발생한다.

**수정 제안**:
```python
# state.py
validation_checks: dict[str, dict[str, Any]] = Field(default_factory=dict)
```
또는 전용 모델을 정의:
```python
class ValidationCheck(BaseModel):
    passed: bool = True  # "pass"는 Python 예약어이므로 rename
    detail: str = ""

validation_checks: dict[str, ValidationCheck] = Field(default_factory=dict)
```

**등급**: CRITICAL (타입 안전성 위반 + mypy 빌드 실패)

---

### 2-2. [CRITICAL] _build_tables_rejected가 list[str]을 dict로 취급

**파일**: `src/services/insight_builder.py` line 106-125
**현상**: `rejected_tables`는 `ReasoningState`에서 `list[str]`로 정의되어 있다 (state.py line 402). 그런데 `_build_tables_rejected`는 각 항목에 대해 `model_dump()`이나 `dict` 여부를 검사한 후 `td.get("table_name", ...)` 등을 호출한다.

```python
# state.py line 402
rejected_tables: list[str] = Field(default_factory=list)

# insight_builder.py line 113-119 — 문자열에 대해 hasattr(t, "model_dump") 체크
for t in rejected:
    if hasattr(t, "model_dump"):   # str에 model_dump 없음 -> 건너뜀
        td = t.model_dump()
    elif isinstance(t, dict):       # str은 dict가 아님 -> 건너뜀
        td = t
    else:
        continue                    # 항상 여기로 빠짐 -> 빈 리스트 반환
```

**결과**: `_build_tables_rejected`는 항상 빈 리스트를 반환하므로, UI의 "제외된 테이블" 섹션이 절대 표시되지 않는다. 기능적으로 Dead Code와 동일하다.

**수정 제안**:
```python
def _build_tables_rejected(reason: Any) -> list[dict[str, Any]]:
    if not reason:
        return []
    rejected = _get_attr_or_key(reason, "rejected_tables", [])
    # rejected_tables는 list[str] (테이블명만 저장)
    return [{"name": name, "desc": "", "reason": ""} for name in rejected if name]
```

**등급**: CRITICAL (기능 미동작 — silent failure)

---

### 2-3. [CRITICAL] _build_tables_used의 columns 필드가 항상 빈 리스트

**파일**: `src/services/insight_builder.py` line 101
**현상**: CandidateTable 모델의 컬럼 필드는 `columns: list[ColumnInfo]`이고, `model_dump()`하면 `columns`가 ColumnInfo dict 리스트가 된다. 그런데 코드는 `columns_used`나 `key_columns`를 먼저 찾는다.

```python
# insight_builder.py line 101
"columns": td.get("columns_used", td.get("key_columns", [])),
```

CandidateTable에 `columns_used`도 `key_columns`도 없다. `columns`는 존재하지만 이 키로 조회하지 않는다. 따라서 항상 `[]`가 반환된다.

UI (line 961)에서 `t.columns.map(esc).join(', ')`로 표시하려 하지만 빈 배열이므로 아무것도 표시되지 않는다.

**수정 제안**:
```python
# CandidateTable.columns는 list[ColumnInfo] -> list[dict]로 변환됨
raw_cols = td.get("columns", [])
"columns": [c.get("name", "") for c in raw_cols if isinstance(c, dict) and c.get("name")],
```

**등급**: CRITICAL (기능 미동작 — 사용 컬럼 정보가 항상 누락)

---

## 3. Warning Issues (주의 필요)

### 3-1. [WARNING] reasoning_trail 한국어 키워드 감지의 한계

**파일**: `src/services/insight_builder.py` line 422-425

```python
is_warning = any(
    kw in insight_text
    for kw in ("부재", "부족", "불가", "없어", "없음", "실패", "제한")
)
```

**문제점**:
1. 부분 문자열 매칭으로 인한 false positive 가능: "제한적으로 확인됨"은 warning이 아닌 정상 결과일 수 있지만 "제한"이 포함되어 warning 표시됨
2. LLM이 생성하는 insight 텍스트는 모델에 따라 표현이 달라질 수 있음 (특히 폐쇄망 Solar Pro 2 / Qwen3.5 환경)
3. "데이터 없음" vs "문제 없음"에서 "없음" 매칭으로 오판

**수정 제안**: insight 생성 시점(context_explorer)에서 warning 여부를 함께 기록하는 것이 더 안정적. ExecutionStep에 `is_warning: bool = False` 필드를 추가하고 insight 작성 시 LLM/rule이 직접 판정하는 방식을 권장.

**등급**: WARNING (false positive/negative 가능, 모델 의존적)

---

### 3-2. [WARNING] total_elapsed 숫자를 esc() 없이 직접 삽입

**파일**: `static/embedded.html` line 1008

```javascript
h+='... 처리 시간: '+ins.total_elapsed.toFixed(1)+'초';
```

`total_elapsed`가 숫자이므로 XSS 위험은 없다. 그러나 서버가 문자열이나 null을 보내면 `.toFixed(1)`에서 TypeError가 발생한다. line 1007의 `ins.total_elapsed!=null` 체크가 있지만, 문자열 "10.5"가 올 경우 `!=null`을 통과하고 `.toFixed(1)`에서 에러가 발생한다.

마찬가지로 line 1014:
```javascript
h+='...'+s.elapsed.toFixed(1)+'초</span></div>';
```

**수정 제안**:
```javascript
var elapsed = parseFloat(ins.total_elapsed) || 0;
h+='... 처리 시간: '+elapsed.toFixed(1)+'초';
```

**등급**: WARNING (방어적 타입 변환 누락)

---

### 3-3. [WARNING] _build_tables_used에서 selection_reason 필드 부재

**파일**: `src/services/insight_builder.py` line 100

```python
"reason": td.get("selection_reason", td.get("reason", "")),
```

CandidateTable에 `selection_reason`이나 `reason` 필드가 없다. 항상 빈 문자열이 반환되므로 UI에서 테이블 선택 사유가 표시되지 않는다. 이는 기능적으로 불완전하지만, CRITICAL 대비 사용자 경험에 미치는 영향이 제한적이므로 WARNING으로 분류.

**수정 제안**: `inferred_functional_usage` 필드를 활용하거나, knowledge_items에서 해당 테이블의 evidence를 추출하여 사유로 사용.

**등급**: WARNING (불완전한 정보 표시)

---

### 3-4. [WARNING] result_finalizer의 exploration_summary 보존 로직

**파일**: `src/agents/nodes/reason/result_finalizer.py` line 76

```python
if not reason.exploration_summary:
    reason.exploration_summary = _build_failure_output(reason)
```

빈 문자열 `""`도 falsy이므로 `not ""`은 `True`가 된다. 이는 의도한 동작이다 (빈 문자열은 "미작성"으로 간주). 그러나 공백만 있는 문자열 `"  "`은 truthy이므로, recovery_planner가 공백 문자열을 기록한 경우 의미 없는 공백이 보존될 수 있다.

**수정 제안**:
```python
if not reason.exploration_summary.strip():
```

**등급**: WARNING (엣지 케이스)

---

### 3-5. [WARNING] _build_failure_narrative에 부분 SQL 노출 위험

**파일**: `src/services/insight_builder.py` line 474-492 -> `src/agents/nodes/reason/result_finalizer.py` line 174-178

`_build_failure_output`에서 `reason.generated_sql[:100]`을 포함한 텍스트가 `exploration_summary`에 기록되고, `_build_failure_narrative`에서 이를 그대로 반환한다. 사용자 상호작용 규칙에 따르면 SQL은 사용자에게 직접 보여주지 않거나 접기 처리해야 한다.

실패 시 부분 SQL이 failure_narrative로 표시되면 IT 지식이 없는 사용자에게 혼란을 줄 수 있다.

**수정 제안**: `_build_failure_output`에서 부분 SQL 노출 부분을 제거하거나, insight_builder에서 failure_narrative 작성 시 SQL 패턴을 필터링.

**등급**: WARNING (사용자 경험 규칙 위반)

---

## 4. Info Issues (개선 권장)

### 4-1. [INFO] XSS 방어: PASS

`embedded.html`의 `esc()` 함수 (line 1463)는 `textContent` -> `innerHTML` 패턴을 사용하여 HTML 이스케이프를 수행한다. `renderInsight`에서 모든 사용자 데이터(`qi.period`, `t.name`, `v.label`, `de.reason` 등)가 `esc()`를 거쳐 삽입되고 있다.

한 가지 예외: line 1033에서 `(i+1)+'차 시도'`는 정수 + 한국어 리터럴이므로 XSS 위험 없음.

전반적으로 XSS 방어가 일관되게 적용됨.

---

### 4-2. [INFO] is_success=undefined일 때 UI 동작: PASS

UI에서 `ins.is_success`가 `undefined`이면 falsy이므로 else 분기(실패)로 진입한다. 이는 "알 수 없으면 실패로 간주"하는 안전한 방향의 fallback이다.

---

### 4-3. [INFO] _build_safe_insight의 예외 처리

**파일**: `src/agents/graph/runner.py` line 153-161

`build_insight` 전체를 try/except로 감싸고 실패 시 빈 dict를 반환하는 구조는 적절하다. 다만 `logger.debug`로 기록하므로 프로덕션에서 문제 추적이 어려울 수 있다.

**수정 제안**: `logger.warning`으로 변경하고 `exc_info=True` 추가를 권장.

**등급**: INFO

---

### 4-4. [INFO] build_insight 함수의 타입 힌트

**파일**: `src/services/insight_builder.py` line 20

```python
def build_insight(state: dict[str, Any]) -> dict[str, Any]:
```

`state` 인자가 `dict[str, Any]`로 되어 있는데, 실제로는 LangGraph가 반환하는 state dict이다. `PipelineState` 또는 `dict`가 올 수 있으므로 현재의 `dict[str, Any]`는 적절하다. 다만 내부 헬퍼 함수들의 `reason` 파라미터가 모두 `Any`인 것은 타입 안전성을 약화시킨다.

`reason: ReasoningState | dict[str, Any] | None` 같은 Union 타입을 명시하면 IDE 지원과 mypy 검증이 향상된다.

**등급**: INFO (타입 힌트 정밀도 개선)

---

### 4-5. [INFO] `except (ParseError, Exception)` 중복 처리

**파일**: `src/agents/nodes/reason/sql_validator.py` line 360

```python
except (ParseError, Exception) as e:
```

`Exception`이 이미 `ParseError`의 상위 클래스이므로 `ParseError`를 별도로 나열하는 것은 무의미하다. 가독성을 위해 간소화를 권장.

```python
except Exception as e:
```

**등급**: INFO (코드 가독성)

---

## 5. Breaking Changes (기존 호출자 영향)

### 5-1. validation_checks 필드 추가 (state.py)

Pydantic BaseModel에 `Field(default_factory=dict)` 기본값이 있으므로, 기존 ReasoningState 인스턴스나 직렬화/역직렬화에 영향 없음. **비파괴적 변경**.

### 5-2. result_finalizer의 exploration_summary 보존 로직

기존에는 실패 시 항상 `_build_failure_output`으로 덮어썼다. 변경 후에는 recovery_planner가 `give_up_reason`을 기록한 경우 해당 값이 보존된다. 이는 의도된 동작이며, 기존에 실패 메시지 내용이 바뀌는 것이 유일한 행동 변화이다. UI 소비자 관점에서는 개선된 메시지가 표시되므로 **긍정적 변경**.

### 5-3. build_insight 반환값에 새 키 추가

기존 키(`query_interpretation`, `tables_used` 등)는 그대로이고, `is_success`, `reasoning_trail`, `validation_detail`, `failure_narrative`, `dead_end_trail`이 추가됨. UI에서 새 키가 없으면 해당 섹션을 표시하지 않으므로 **하위 호환**.

---

## 6. 요약

| 등급 | 건수 | 핵심 |
|------|------|------|
| CRITICAL | 3 | 타입 불일치, rejected_tables dead code, columns 필드 미매핑 |
| WARNING | 5 | 한국어 키워드 감지 한계, 숫자 방어 누락, 빈 selection_reason, 공백 엣지케이스, 부분 SQL 노출 |
| INFO | 5 | XSS PASS, undefined fallback PASS, 로그 레벨, 타입 힌트 정밀도, except 중복 |

**우선 조치**: CRITICAL 3건 수정 후 WARNING 순서대로 처리를 권장합니다.
