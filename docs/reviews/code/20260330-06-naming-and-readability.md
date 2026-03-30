# 네이밍 / 가독성 상세 리포트

- **검토 일시**: 2026-03-30
- **검토 관점**: 이름이 역할을 설명하지 못하는 코드, 저수준 구현이 비즈니스 로직에 혼재하여 가독성이 떨어지는 코드

---

## 요약

| ID | 카테고리 | 위치 | 한줄 요약 |
|----|---------|------|----------|
| N-01 | 실수방지 | nodes/__init__.py | docstring 디렉토리명이 이전 버전(agentic/ → reason/) |
| N-02 | 실수방지 | thinking_modes.py | query_normalizer가 reason 계층으로 잘못 분류 |
| N-03 | 실수방지 | system_prompts.py | docstring 파일 매핑과 실제 로드 파일 불일치 |
| N-04 | 실수방지 | confidence_scorer.py | docstring 가중치(50/30/20)와 코드 가중치(55/25/20) 불일치 |
| L-01 | 가독성 | data_analyzer.py | parse_analysis_json에 문자열 처리 30줄 인라인 |
| L-02 | 가독성 | chart_generator.py | 3개 차트 함수에서 레이아웃 상수 인라인 반복 |
| L-03 | 가독성 | response_formatter.py | format_result_for_prompt 동일 데이터 2회 호출 |
| L-04 | 가독성 | prompt.py | f-string 이중 중괄호 이스케이프로 가독성 저하 |
| L-05 | 가독성 | evaluation.py | UI 표시 문자열이 추적 로직에 하드코딩 |
| L-06 | 가독성 | seed_sql_history.py | __import__("sqlalchemy") 사용 |

---

## N-01. (실수방지) nodes/__init__.py docstring 디렉토리명 불일치

### 위치
- `src/agents/nodes/__init__.py:16`

### 문제 상세

```python
# 현재 docstring
"Agentic Core 노드 (agentic/ 서브패키지)"
```

리팩토링 과정에서 `agentic/` → `reason/`으로 디렉토리가 변경되었으나 docstring이 업데이트되지 않았다. 새 개발자가 `agentic/` 디렉토리를 찾으려 할 수 있다.

### 해결 방안

```diff
- Agentic Core 노드 (agentic/ 서브패키지)
+ Agentic Core 노드 (reason/ 서브패키지)
```

---

## N-02. (실수방지) thinking_modes.py에서 query_normalizer가 reason 계층으로 분류

### 위치
- `src/agents/nodes/thinking_modes.py:24`

### 문제 상세

`NODE_THINKING_MODES` dict의 주석에서 `query_normalizer`가 **Reason 계층** 아래에 분류되어 있다:

```python
# ── Reason ──
"planner": "auto",
"context_explorer": "auto",
"query_normalizer": "auto",     # ← 실제로는 interpret/ 디렉토리
"sql_generator": "auto",
```

`query_normalizer`는 실제로 `src/agents/nodes/interpret/query_normalizer.py`에 위치하는 **Interpret 계층 노드**이다.

### 해결 방안

주석을 실제 디렉토리 구조와 일치시킨다:

```python
NODE_THINKING_MODES = {
    # ── Interpret ──
    "intent_classifier": "auto",
    "query_normalizer": "auto",   # interpret/ 계층
    "clarifier": "auto",

    # ── Reason ──
    "planner": "auto",
    "context_explorer": "auto",
    "sql_generator": "auto",
    # ...
}
```

---

## N-03. (실수방지) system_prompts.py docstring 파일 매핑 불일치

### 위치
- `src/agents/nodes/system_prompts.py:48-54`

### 문제 상세

docstring 상단의 "파일 매핑 테이블"과 실제 코드에서 로드하는 파일명이 불일치하는 항목이 있다:

| docstring 기술 | 실제 로드 파일 | 상태 |
|---------------|-------------|------|
| `BATCH_INTERPRET_SYSTEM ← batch_interpret_system.txt` | `context_explorer_batch_interpret.txt` | **불일치** |
| `CONTEXT_EXPLORER_SYSTEM ← context_explorer_system.txt` | (변수 자체가 선언되지 않음) | **불일치** |
| `TABLE_COMPARISON_SYSTEM ← table_comparison_system.txt` | (실제 파일명 확인 필요) | **요확인** |

**위험**: 개발자가 docstring을 보고 파일명을 추측하여 수정하면 실제 로드 경로와 달라 **런타임에 프롬프트 로드 실패**가 발생할 수 있다.

### 해결 방안

docstring의 파일 매핑 테이블을 실제 코드와 동기화한다. 매핑 테이블 자체를 **코드에서 자동 생성**하는 것이 더 안전하다:

```python
# 매핑 테이블을 코드로 관리
_PROMPT_REGISTRY = {
    "BATCH_INTERPRET_SYSTEM": _reason("context_explorer_batch_interpret.txt"),
    "TABLE_COMPARISON_SYSTEM": _reason("table_comparison_system.txt"),
    # ...
}

# 모듈 레벨 상수로 노출
BATCH_INTERPRET_SYSTEM = _PROMPT_REGISTRY["BATCH_INTERPRET_SYSTEM"]
TABLE_COMPARISON_SYSTEM = _PROMPT_REGISTRY["TABLE_COMPARISON_SYSTEM"]
```

또는 최소한 docstring을 실제와 일치시킨다:

```python
"""
BATCH_INTERPRET_SYSTEM ← reason/context_explorer_batch_interpret.txt
"""
```

---

## N-04. (실수방지) confidence_scorer.py docstring 가중치와 코드 불일치

### 위치
- `src/services/confidence_scorer.py` — docstring vs 코드

### 문제 상세

모듈 docstring 또는 함수 docstring에 확신도 가중치가 **50%/30%/20%** 로 기술되어 있으나, 실제 코드의 가중치는 **55%/25%/20%** 이다:

```python
# docstring
"가중 평균: 용어 해소 50%, 유스케이스 매치 30%, 조인 경로 20%"

# 실제 코드
WEIGHT_TERM = 0.55
WEIGHT_USE_CASE = 0.25
WEIGHT_JOIN = 0.20
```

**위험**: 확신도 점수의 의미를 분석할 때 docstring을 기준으로 계산하면 실제와 5% 차이가 나서 디버깅에 혼란을 준다.

### 해결 방안

docstring을 코드 값과 일치시킨다:

```python
"""가중 평균: 용어 해소 55%, 유스케이스 매치 25%, 조인 경로 20%"""
```

---

## L-01. (가독성) parse_analysis_json에 문자열 처리 30줄 인라인

### 위치
- `src/services/data_analyzer.py:72-98`

### 문제 상세

`parse_analysis_json` 함수는 비즈니스 로직(분석 결과 구조화)과 저수준 구현(코드펜스 제거, JSON 파싱, 에러 핸들링)이 한 함수에 섞여 있다:

```python
def parse_analysis_json(text: str) -> AnalysisResult:
    # ── 저수준: 코드펜스 제거 (이미 extract_json에 존재) ──
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3]

    # ── 저수준: JSON 파싱 + 에러 핸들링 ──
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # fallback 시도...
        ...

    # ── 비즈니스: AnalysisResult 구성 ──
    return AnalysisResult(
        summary=data.get("summary", ""),
        ...
    )
```

함수를 읽는 사람이 "이 함수는 뭘 하는가?"에 답하려면 코드펜스 파싱 디테일을 먼저 건너뛰어야 한다.

### 해결 방안

저수준 파싱을 `extract_json` 유틸에 위임하고, 이 함수는 비즈니스 변환만 담당한다:

```python
from src.utils.llm.response import extract_json

def parse_analysis_json(text: str) -> AnalysisResult:
    """LLM 분석 응답을 AnalysisResult로 변환한다."""
    data = extract_json(text)
    return AnalysisResult(
        summary=data.get("summary", ""),
        key_findings=data.get("key_findings", []),
        statistics=data.get("statistics", {}),
    )
```

함수가 **5줄**로 줄어들고, 역할이 명확해진다.

---

## L-02. (가독성) 3개 차트 함수에서 레이아웃 상수 인라인 반복

> D-06과 동일 이슈. `20260330-02-duplicate-implementations.md#D-06` 참조.

---

## L-03. (가독성) format_result_for_prompt 동일 데이터로 2회 호출

> D-07과 동일 이슈. `20260330-02-duplicate-implementations.md#D-07` 참조.

---

## L-04. (가독성) prompt.py의 f-string 이중 중괄호 이스케이프

### 위치
- `src/utils/llm/prompt.py:32-38`

### 문제 상세

```python
return {
    f"{{{slot}}}": json.dumps(value, ensure_ascii=False)
    for slot, value in pairs
}
```

`f"{{{slot}}}"` 패턴은 Python의 f-string에서 리터럴 중괄호를 출력하기 위해 `{{`와 `}}`를 사용하는 것이지만, 한눈에 파악하기 어렵다. "slot이 `ENTITY`이면 `{ENTITY}`를 키로 쓴다"는 의도가 숨겨져 있다.

### 해결 방안

명시적 문자열 연결을 사용한다:

```python
return {
    "{" + slot + "}": json.dumps(value, ensure_ascii=False)
    for slot, value in pairs
}
```

또는 의도를 주석으로 보충한다:

```python
# 프롬프트 템플릿의 {SLOT_NAME} 플레이스홀더를 JSON 값으로 치환
template_vars = {}
for slot, value in pairs:
    placeholder = "{" + slot + "}"  # ex: "{ENTITY}"
    template_vars[placeholder] = json.dumps(value, ensure_ascii=False)
return template_vars
```

---

## L-05. (가독성) evaluation.py에 UI 표시 문자열 하드코딩

### 위치
- `src/utils/tracker/evaluation.py:255-258`

### 문제 상세

```python
label = (
    "📂 추가 데이터를 탐색하고 있습니다"
    f" ({self._explore_count}차)"
)
```

UI 표시용 한국어 문자열과 이모지가 **추적 로직(evaluation tracker)** 내부에 하드코딩되어 있다. `runner.py`에는 이미 `NODE_PROGRESS_MAP`이라는 노드별 진행 메시지 매핑이 정의되어 있으므로, 이 특수 케이스도 동일한 곳에서 관리해야 한다.

**위험**:
- UI 문구를 변경하려면 tracker 파일까지 수정해야 함 (관심사 분리 위반)
- 다국어 지원 시 tracker 내부의 문자열도 변환 대상이 됨

### 해결 방안

`NODE_PROGRESS_MAP`에 탐색 노드의 메시지 템플릿을 추가하고, tracker는 이를 참조한다:

```python
# runner.py
NODE_PROGRESS_MAP = {
    "context_explorer": "추가 데이터를 탐색하고 있습니다 ({count}차)",
    "sql_generator": "SQL을 생성하고 있습니다",
    # ...
}
```

```python
# evaluation.py — runner에서 정의된 메시지 사용
label = NODE_PROGRESS_MAP["context_explorer"].format(count=self._explore_count)
```

---

## L-06. (가독성) seed_sql_history.py에서 __import__ 사용

### 위치
- `src/tools/seed_sql_history.py:210`

### 문제 상세

```python
result = await session.execute(__import__("sqlalchemy").text(query))
```

`__import__("sqlalchemy")` 는 지연 import를 의도한 것이지만:
1. **가독성이 극히 떨어짐**: 일반적인 Python 코드에서 `__import__`를 직접 사용하는 것은 매우 드문 패턴
2. **IDE 지원 부재**: 자동완성, 타입 추론, 참조 검색이 작동하지 않음
3. **매 호출마다 import 해소**: 캐시되긴 하지만 의도가 불분명

### 해결 방안

메서드 상단에서 일반 import를 사용한다:

```python
async def _extract_sql_history(self):
    from sqlalchemy import text  # 지연 import (필요 시에만 로드)

    # ...
    result = await session.execute(text(query))
```

함수 레벨 지연 import는 Python에서 널리 사용되는 관용구이며, 가독성과 IDE 지원이 모두 유지된다.
