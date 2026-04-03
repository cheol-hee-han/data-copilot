# FM-1 code_map 동적 주입 코드 리뷰

- 리뷰 일자: 2026-04-03
- 대상: `sqlglot_analyzer.extract_select_alias_map`, `formatter.py`, `response_formatter.py`, `formatter_system.txt`
- 중점: 타입 정합성, import 유효성, 로직 오류, 프롬프트 placeholder 안전성, 엣지 케이스

---

## 1. 발견 사항 요약

| # | 심각도 | 파일 | 내용 |
|---|--------|------|------|
| 1 | :red_circle: Critical | `response_formatter.py` | `format_result_for_prompt` 이중 호출 (성능 낭비 + 불일치 위험) |
| 2 | :yellow_circle: Warning | `formatter.py` | `_build_code_mappings`에서 `SELECT *` 엣지 케이스 미처리 |
| 3 | :yellow_circle: Warning | `formatter_system.txt` | `.format()` / `.replace()` 혼용 전략의 취약점 |
| 4 | :yellow_circle: Warning | `formatter.py` | `code_mappings` 직렬화 시 코드값에 특수문자 포함 가능성 |
| 5 | :green_circle: Info | `sqlglot_analyzer.py` | `extract_select_alias_map` 서브쿼리/CTE SELECT 오인 가능성 |
| 6 | :green_circle: Info | `formatter.py` | `_build_code_mappings` 반환 타입 문서화 부족 |

---

## 2. 상세 분석

### #1 :red_circle: Critical -- `format_result_for_prompt` 이중 호출

**파일**: `src/services/response_formatter.py` (L86, L133)

```python
# L115-118: user_message 생성 시 1회 호출
user_message = user_template.format(
    user_input=user_input,
    query_result=format_result_for_prompt(sql_result),  # ← 1회
)

# L133: 로깅용으로 동일 함수 재호출
result_text = format_result_for_prompt(sql_result)  # ← 2회
await record_prompt_variables({
    "user_input": user_input,
    "query_result": truncate_log(result_text),
})
```

**문제**: `format_result_for_prompt`가 동일한 `sql_result`에 대해 두 번 호출된다. 현재 구현은 순수 함수이므로 결과가 다르진 않지만, (1) 불필요한 연산 중복이며 (2) 향후 max_rows 기본값이 변경되거나 사이드이펙트가 추가되면 불일치가 발생할 수 있다.

**수정안**:
```python
result_text = format_result_for_prompt(sql_result)
user_message = user_template.format(
    user_input=user_input,
    query_result=result_text,
)
# ... LLM 호출 후 ...
await record_prompt_variables({
    "user_input": user_input,
    "query_result": truncate_log(result_text),
})
```

---

### #2 :yellow_circle: Warning -- `SELECT *` 엣지 케이스 미처리

**파일**: `src/agents/nodes/present/formatter.py` (`_build_code_mappings`, L152-185)

`extract_select_alias_map`은 `SELECT *` 구문에서 빈 dict를 반환한다 (L186-197의 반복문에서 `exp.Alias`도 `exp.Column`도 아닌 `exp.Star`는 처리되지 않음). 이 경우 `_build_code_mappings`는 즉시 `"해당 없음"`을 반환하여, code_map에 유효한 코드값이 있어도 프롬프트에 주입되지 않는다.

**발생 시나리오**: SQL이 `SELECT * FROM LOAN_TBL WHERE LOAN_STS_CD = '01'` 형태일 때.

**영향**: 코드값이 결과에 코드 형태(`01`, `02`)로 표시되어 사용자가 의미를 알 수 없게 된다.

**수정안**: `alias_map`이 비어있을 때 code_map 전체를 폴백으로 직렬화하는 분기 추가.

```python
def _build_code_mappings(
    code_map: dict[str, CodeMeta],
    sql: str | None,
) -> str:
    if not code_map or not sql:
        return _NO_CODE_MAPPINGS

    alias_map = extract_select_alias_map(sql)

    # SELECT * 등으로 alias 추출 불가 시 code_map 전체를 폴백 직렬화
    if not alias_map:
        return _serialize_all_code_map(code_map)

    # ... 기존 필터링 로직 ...
```

---

### #3 :yellow_circle: Warning -- `.format()` / `.replace()` 혼용 전략

**파일**: `resources/prompts/present/formatter_system.txt`, `src/services/response_formatter.py`

현재 설계:
- `formatter_system.txt`의 `{code_mappings}` → `.replace()` 로 치환 (L112-113)
- `formatter_user.txt`의 `{user_input}`, `{query_result}` → `.format()` 로 치환 (L115-118)

**현재 상태는 안전하다**: `formatter_system.txt`에는 `{code_mappings}` 외에 다른 `{}`가 없고, `formatter_user.txt`에도 정확히 `{user_input}`과 `{query_result}`만 있으므로 `.format()` 충돌은 발생하지 않는다.

**잠재적 위험**: 프롬프트 작성자가 `formatter_user.txt`에 중괄호를 포함하는 예시(예: JSON 형태 `{"key": "value"}`)를 추가하면 `.format()`이 `KeyError`를 발생시킨다. 또한 `formatter_system.txt`에 `{다른변수}` 형태를 추가해도 `.replace()`가 이를 무시하여 LLM에 미치환 placeholder가 노출된다.

**권장 사항**: 두 프롬프트 파일 모두 `.replace()` 방식으로 통일하거나, `string.Template`의 `$variable` 패턴을 사용하면 중괄호 충돌 위험을 근본적으로 제거할 수 있다. 당장의 수정보다는 프롬프트 치환 방식을 프로젝트 전체에서 표준화하는 것이 장기적으로 유리하다.

---

### #4 :yellow_circle: Warning -- 코드값 특수문자 이스케이핑 미처리

**파일**: `src/agents/nodes/present/formatter.py` (L179-183)

```python
pairs = ", ".join(
    f"{k}={v}"
    for k, v in list(meta.codes.items())[:20]
)
lines.append(f"- {display}({col_name}): {pairs}")
```

코드값(key)이나 코드명(value)에 쉼표(`,`), 등호(`=`), 개행 등 구분자와 동일한 문자가 포함되면 LLM이 매핑을 잘못 파싱할 수 있다.

**현실적 위험도**: 금융 코드값은 일반적으로 숫자/영문 코드이므로 낮지만, 코드명(value)에 "수수료(일반, 특별)" 같은 쉼표 포함 문자열이 있을 수 있다.

**수정안**: 구분자를 `" | "`로 변경하고, 값에 `"`를 감싸는 것이 안전하다.

```python
pairs = " | ".join(
    f'{k}="{v}"'
    for k, v in list(meta.codes.items())[:20]
)
```

---

### #5 :green_circle: Info -- `extract_select_alias_map` 서브쿼리 SELECT 오인

**파일**: `src/utils/sqlglot_analyzer.py` (L176-197)

```python
select = ast.find(exp.Select)
```

`ast.find(exp.Select)`는 AST를 깊이 우선 탐색하여 첫 번째 SELECT를 반환한다. 서브쿼리가 있는 SQL(예: `SELECT * FROM (SELECT A.COL1 AS X FROM T) sub`)에서는 내부 SELECT가 먼저 발견될 수 있다.

**현실적 영향**: present 계층에서 사용되는 validated_sql은 대부분 최외곽 SELECT가 최종 결과인 단순 구조이므로 실질적 위험은 낮다. 그러나 `WITH CTE AS (SELECT ...) SELECT ...` 형태에서 CTE의 SELECT가 먼저 매칭될 가능성이 있다.

**개선안** (우선순위 낮음): `ast.find(exp.Select)` 대신 AST 루트가 Select인지 확인하거나, traverse_scope를 사용하여 최외곽 scope의 SELECT만 추출.

---

### #6 :green_circle: Info -- `_build_code_mappings` docstring 보완 필요

**파일**: `src/agents/nodes/present/formatter.py` (L152-156)

docstring이 "필터링하여 프롬프트 텍스트로 직렬화한다"로 간결하지만, 반환값 형태(예: `"- 대출구분(LOAN_DCD): 01=정상, 02=연체"` 형태의 줄 구분 문자열, 또는 `"해당 없음"`)에 대한 설명이 없다. 호출측(`format_response`)에서 이 값이 프롬프트에 어떻게 삽입되는지 이해하기 위해 반환값 예시를 docstring에 추가하면 좋다.

---

## 3. 타입 정합성 검증 결과

| 항목 | 결과 | 비고 |
|------|------|------|
| `state.reason.code_map` 접근 | OK | `ReasoningState.code_map: dict[str, CodeMeta]` (L421) |
| `state.reason.validated_sql` 접근 | OK | `ReasoningState.validated_sql: str \| None` (L434) |
| `_build_code_mappings(code_map, sql)` 시그니처 | OK | `dict[str, CodeMeta]`, `str \| None` 정합 |
| `extract_select_alias_map` 반환 타입 | OK | `dict[str, str \| None]` → `alias_map.items()`에서 `orig_col`이 `None`일 수 있으나 L168에서 `if orig_col:` 체크됨 |
| `format_response` kwargs | OK | `code_mappings: str = "해당 없음"` 기본값 존재 |
| `CodeMeta.codes` 타입 | OK | `dict[str, str]` → `meta.codes.items()`의 k, v 모두 str |

---

## 4. import 검증 결과

| 파일 | 추가된 import | 유효성 |
|------|--------------|--------|
| `formatter.py` L35 | `CodeMeta` from `state.py` | OK (L231에 정의) |
| `formatter.py` L47 | `extract_select_alias_map` from `sqlglot_analyzer.py` | OK (L166에 정의) |

---

## 5. 프롬프트 placeholder 안전성

| 프롬프트 파일 | placeholder | 치환 방식 | 다른 `{}` 존재 | 충돌 위험 |
|--------------|-------------|-----------|---------------|-----------|
| `formatter_system.txt` | `{code_mappings}` | `.replace()` | 없음 | 없음 (현재 안전) |
| `formatter_user.txt` | `{user_input}`, `{query_result}` | `.format()` | 없음 | 없음 (현재 안전) |

**결론**: 현재 두 프롬프트 파일 모두 충돌 없음. 단, 프롬프트 편집 시 `formatter_user.txt`에 raw 중괄호를 추가하면 깨질 수 있으므로 #3의 장기적 표준화를 권장.

---

## 6. 엣지 케이스 점검

| 시나리오 | 동작 | 판정 |
|----------|------|------|
| `validated_sql`이 `None` | `_build_code_mappings`에서 L157의 `not sql` 체크 → `"해당 없음"` 반환 | OK |
| `code_map`이 빈 dict `{}` | L157의 `not code_map` 체크 → `"해당 없음"` 반환 | OK |
| SQL 파싱 실패 (비표준 방언) | `parse_sql_safe` → `None` → `extract_select_alias_map` → `{}` → `"해당 없음"` | OK |
| alias 없는 `SELECT COL1, COL2` | `exp.Column` 분기 타서 `{"COL1": "COL1", "COL2": "COL2"}` → 정상 매칭 | OK |
| `SELECT *` | alias_map `{}` → `"해당 없음"` (코드값 손실) | #2 참조 |
| `code_map`에 매칭되는 컬럼 없음 | L177 조건 불충족 → `lines` 빈 리스트 → `"해당 없음"` | OK |
| 코드값 20개 초과 | L181 `[:20]` 슬라이스 → 상위 20개만 포함 | OK (의도된 제한) |

---

## 7. 조치 우선순위

1. **즉시 수정**: #1 `format_result_for_prompt` 이중 호출 제거 (단순 리팩토링, 위험 낮음)
2. **이번 스프린트**: #2 `SELECT *` 폴백 처리 추가
3. **백로그**: #3 프롬프트 치환 방식 프로젝트 표준화, #4 구분자 개선
4. **참고**: #5, #6은 현재 동작에 영향 없으므로 시간 여유 시 개선
