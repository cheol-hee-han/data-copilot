# SQL Assumptions Feature - Code Review Report

- 일자: 2026-04-07
- 대상: SQL 생성 시 해석적 선택(assumptions) 서피싱 기능
- 범위: state.py, sql_generator_system.txt, sql_generator.py, result_finalizer.py, insight_builder.py

---

## 1. Critical (RED)

### 1.1 `state.get("turn_id")` - PipelineState는 Pydantic 모델이므로 `.get()` 불가

- 파일: `src/agents/nodes/reason/result_finalizer.py` (line 87)
- 코드: `state.get("turn_id")`
- 문제: `PipelineState`는 Pydantic `BaseModel`이며 `dict`가 아니다. `.get()` 메서드가 없으므로 `AttributeError`가 런타임에 발생한다. 다른 모든 노드에서는 `state.turn_id`로 접근하고 있다 (clarification_handler.py:137, intent_classifier.py:176, sql_validator.py:127 등).
- 영향: assumptions가 있는 SQL 생성 성공 시 result_finalizer에서 **런타임 에러** 발생. 전체 파이프라인이 실패한다.
- 수정:

```python
# before
state.get("turn_id"),
# after
state.turn_id,
```

### 1.2 `_build_assumption_signals`의 `_` prefix - 내부 함수를 외부 모듈에서 import

- 파일: `src/agents/nodes/reason/result_finalizer.py` (line 26)
- 코드: `from src.agents.nodes.reason.sql_generator import _build_assumption_signals`
- 문제: Python convention에서 `_` prefix는 모듈 내부 전용 함수를 의미한다. 이 함수를 외부 모듈(result_finalizer)에서 import하는 것은 캡슐화 위반이며, IDE/linter가 경고를 발생시킬 수 있고, 리팩토링 시 깨지기 쉽다.
- 수정 방안:
  - (A) 함수를 `build_assumption_signals`로 rename하여 공개 API화
  - (B) 공용 유틸리티 모듈(`src/agents/utils/signal_factory.py` 등)로 이동
  - (B)를 권장 -- result_finalizer의 `_build_conflicted_signals`도 같은 패턴이므로 signal 생성 유틸리티로 통합 가능

---

## 2. Warning (YELLOW)

### 2.1 `_build_assumption_signals`의 `->` 파싱이 방어적이지 않음

- 파일: `src/agents/nodes/reason/sql_generator.py` (lines 555-560)
- 코드:

```python
if "\u2192" in text:
    q, v = text.split("\u2192", 1)
    question = q.strip()
    inferred = v.strip()
else:
    question = text
    inferred = text
```

- 문제:
  1. 유니코드 화살표 `\u2192` ("->")만 처리한다. LLM이 ASCII `->`, `-->`, `=>`, 또는 전각 `\uff0d\uff1e` 등을 출력할 수 있다. 특히 폐쇄망 오픈소스 모델(Solar Pro 2, Qwen3.5)에서 유니코드 일관성이 보장되지 않는다.
  2. fallback(else 분기)에서 `question`과 `inferred`가 동일한 값이 된다. 이는 caveat에서 question만 표시할 때 원본 텍스트가 그대로 노출되어 의미가 불분명해진다.
- 수정:

```python
import re

_ARROW_RE = re.compile(r"\s*(?:\u2192|->|=>|-->)\s*")

def _split_assumption(text: str) -> tuple[str, str]:
    parts = _ARROW_RE.split(text, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return text.strip(), text.strip()
```

### 2.2 fail 분기에서 `pending_assumptions`가 초기화되지 않음

- 파일: `src/agents/nodes/reason/sql_generator.py` (lines 327-340)
- 코드: fail 분기에는 `reason.pending_assumptions` 설정이 없다.
- 문제: 첫 시도에서 success + assumptions 후 validator에서 fail되어 sql_generator가 재진입하면, 이전 pending_assumptions가 그대로 남는다. 두 번째 시도가 fail이면 이전의 assumptions가 상태에 잔류한다. `reason = state.reason.model_copy(deep=True)`로 복사하므로 이전 값이 복사된다.
- 영향: 재시도 후 실패해도 이전 성공의 assumptions가 남아있을 수 있다. 단, 최종적으로 fail이면 result_finalizer에서 `validated_sql` 체크로 assumptions가 사용되지 않으므로 실제 영향은 제한적이다. 그러나 디버깅 시 혼란을 줄 수 있다.
- 수정:

```python
# fail 분기 시작부에 추가
reason.pending_assumptions = []
```

### 2.3 insight_builder에서 assumptions caveat 표시가 question만 사용

- 파일: `src/services/insight_builder.py` (lines 419-428)
- 코드:

```python
sql_assumptions = [
    s for s in resolved
    if (getattr(s, "source_node", "") == "sql_generator"
        and getattr(s, "decision", "") == "INFER")
]
for s in sql_assumptions:
    q = getattr(s, "question", "")
    if q:
        caveats.append(q)
```

- 문제: `question`만 caveat에 표시하고 `inferred_value`는 표시하지 않는다. 예를 들어 `"'예금신규 금액'의 해석"`만 표시되고 `"요청 기간 내 신규된 예금의 전체 잔액"`은 보이지 않는다. 사용자가 "어떻게 해석했는지"를 알 수 없다.
- 수정:

```python
for s in sql_assumptions:
    q = getattr(s, "question", "")
    v = getattr(s, "inferred_value", "")
    if q and v and q != v:
        caveats.append(f"{q} -> {v}")
    elif q:
        caveats.append(q)
```

### 2.4 `resolved_signals` reducer 동작과 반환 형태 확인

- 파일: `src/agents/nodes/reason/result_finalizer.py` (line 90)
- 코드: `updates["resolved_signals"] = assumption_signals`
- 분석: `PipelineState.resolved_signals`는 `Annotated[list[AmbiguitySignal], operator.add]`로 정의되어 있다. LangGraph의 `operator.add` reducer는 기존 리스트에 새 리스트를 concat한다. 따라서 반환값이 `list`이면 정상 동작한다 -- 이 부분은 **정상**이다.
- 참고: 만약 `operator.add` 대신 일반 필드였다면 덮어쓰기가 되어 기존 signals가 유실되므로, 현재 설계가 올바르다.

---

## 3. Info (GREEN)

### 3.1 backward compatibility의 `data.get("reasons")` fallback 적절

- 파일: `src/agents/nodes/reason/sql_generator.py` (line 492)
- 코드: `data.get("failure_reasons", data.get("reasons", []))`
- 평가: 프롬프트가 `failure_reasons`로 변경되었으나 LLM이 이전 키를 출력할 가능성에 대한 fallback이 포함되어 있다. 특히 폐쇄망 오픈소스 모델에서는 few-shot example을 무시하고 이전 패턴을 출력할 수 있으므로 이 fallback은 적절하다.

### 3.2 프롬프트 내 assumptions 가이드라인 충실

- 파일: `resources/prompts/reason/sql_generator_system.txt` (lines 176-209)
- 평가: 기재/비기재 체크리스트, 출력 형식 규약(`->` 구분자), IT 용어 한글 병기 규칙까지 상세하게 기술되어 있다. Few-shot 예시도 6개 케이스 전부에 assumptions 필드가 포함되어 있어 LLM의 형식 준수율이 높을 것으로 기대된다.

### 3.3 pending_assumptions 덮어쓰기 방식 적절

- 파일: `src/agents/nodes/reason/sql_generator.py` (line 319)
- 코드: `reason.pending_assumptions = result.get("assumptions", [])`
- 평가: 재시도 시 이전 assumptions를 덮어쓰는 것은 올바르다. 각 시도의 assumptions는 해당 SQL에 종속되므로, append가 아닌 replace가 맞다.

### 3.4 state.py 필드 주석 충실

- 파일: `src/agents/state/state.py` (lines 529-533)
- 코드:

```python
# -- SQL 생성 가정 (재시도 시 덮어쓰기, 최종 성공 시 resolved_signals로 전환) --
# W: GEN  R: FIN
pending_assumptions: list[str] = Field(default_factory=list)
```

- 평가: W/R 표기, 동작 방식 요약이 기존 패턴과 일관되게 작성되어 있다.

### 3.5 unused import 없음

- 확인 결과: 변경된 5개 파일 모두에서 미사용 import는 발견되지 않았다.

---

## 4. 요약

| 등급 | 건수 | 핵심 |
|------|------|------|
| Critical | 2 | `state.get("turn_id")` 런타임 에러, `_` prefix 함수 외부 import |
| Warning | 3 | 화살표 파싱 방어 부족, fail시 assumptions 미초기화, caveat에 inferred_value 누락 |
| Info | 5 | backward compat fallback 적절, 프롬프트 가이드라인 충실, 덮어쓰기 방식 정상 |

### 즉시 수정 필요 (Critical)

1. `result_finalizer.py:87` -- `state.get("turn_id")` -> `state.turn_id`
2. `_build_assumption_signals` -- 공개 API로 rename 또는 공용 모듈로 이동

### 권장 수정 (Warning)

3. 화살표 구분자 파싱에 ASCII `->` 등 변형 지원 추가
4. fail 분기에서 `reason.pending_assumptions = []` 초기화
5. insight_builder caveat에 `inferred_value` 포함
